#!/usr/bin/env bash
# start.sh — generate the runtime key, render, and start the server.
set -euo pipefail
# shellcheck disable=SC1091 # Resolved from this script at runtime.
# shellcheck source=../tools/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/../tools/lib.sh"

require_cmd uv jq yq systemctl curl podman

[ -f "$CONFIG_PATH" ] || die "no config at ${CONFIG_PATH}; run 'make setup' first"

migrate_config_file || die "configuration migration failed"
ensure_api_key
ensure_omniroute_secrets

# migrate_config_file (above) always normalizes llm_server.enabled to a
# concrete boolean, so this reads it directly -- `// true` would be wrong
# here: jq/yq's `//` treats `false` itself as falsy and would silently
# coerce a disabled config back to enabled.
llm_server_enabled="$(yq -r '.llm_server.enabled' "$CONFIG_PATH")"

system_one_enabled="$(yq -r '.system_one.enabled' "$CONFIG_PATH")"
if [ "$system_one_enabled" = "true" ]; then
    system_one_image="$(yq -r '.system_one.image' "$CONFIG_PATH")"
    system_one_models="$(yq -r '.system_one.models' "$CONFIG_PATH")"
    podman image exists "$system_one_image" || die "system-one image missing; run 'make setup'"
    podman volume exists llm-env-system-one-cache \
        || die "system-one weights cache missing; run 'make setup'"
    if ! podman run --rm \
            -e LAYA_REVISION=reviewed -e "LAYA_MODELS=${system_one_models}" \
            -v llm-env-system-one-cache:/home/laya/.cache/huggingface \
            -v "${REPO_DIR}/scripts/system-one-cache-check.py:/opt/system-one-cache-check.py:ro,z" \
            --entrypoint python3 "$system_one_image" /opt/system-one-cache-check.py verify; then
        die "system-one weights incomplete for configured models; run 'make setup'"
    fi
fi

if [ "$llm_server_enabled" = "true" ]; then
    models_max="$(yq -r '.runtime.models_max' "$CONFIG_PATH")"
    [ "$models_max" -gt 0 ] || die "no models enabled; run 'make setup'"
fi

if systemctl --user is-active --quiet "${UNIT_NAME}.service"; then
    log_step "Stopping the active router before measuring VRAM"
    systemctl --user stop "${UNIT_NAME}.service"
fi

if [ "$llm_server_enabled" = "true" ]; then
    log_step "Checking the VRAM budget"
    if ! budget="$(llmenv --config "$CONFIG_PATH" budget --models-dir "$MODELS_DIR")"; then
        # See setup/setup.sh's identical Step 7 gate for why `.error` is
        # checked first: an OPERATIONAL failure (`{"error": "..."}`, no
        # `.remedies`/`.vram_feasible`) piped into `jq -r '.remedies[] | ...'`
        # would crash this script under `set -euo pipefail` instead of
        # surfacing the real, more actionable message.
        if echo "$budget" | jq -e '.error' > /dev/null 2>&1; then
            die "$(echo "$budget" | jq -r '.error')"
        fi
        if [ "$(echo "$budget" | jq -r 'if has("vram_feasible") then .vram_feasible else true end')" = "false" ]; then
            echo "$budget" | jq -r '"  VRAM short by \(.shortfall_mib) MiB (available \(.available_mib) MiB, required \(.required_mib) MiB)"'
        fi
        if [ "$(echo "$budget" | jq -r 'if has("ram_feasible") then .ram_feasible else true end')" = "false" ]; then
            echo "$budget" | jq -r '"  RAM short by \(.ram_shortfall_mib) MiB (available \(.ram_available_mib) MiB, required \(.ram_required_mib) MiB)"'
        fi
        echo "$budget" | jq -r '.remedies // [] | .[] | "    - \(.)"'
        die "budget exceeded for models_max=${models_max}; adjust the config and retry"
    fi
    echo "$budget" | jq -r '"  available \(.available_mib) MiB, required \(.required_mib) MiB"'
fi

log_step "Computing resource limits"
resources_json="$(mktemp)"
trap 'rm -f "$resources_json"' EXIT
if llmenv --config "$CONFIG_PATH" resources > "$resources_json"; then
    omniroute_cpus_min="$(jq -r '.omniroute.cpus_min' "$resources_json")"
    omniroute_cpus_max="$(jq -r '.omniroute.cpus_max' "$resources_json")"
    omniroute_memory_min_mib="$(jq -r '.omniroute.memory_min_mib' "$resources_json")"
    omniroute_memory_max_mib="$(jq -r '.omniroute.memory_max_mib' "$resources_json")"
    remote_setup_cpus_min="$(jq -r '.remote_setup.cpus_min' "$resources_json")"
    remote_setup_cpus_max="$(jq -r '.remote_setup.cpus_max' "$resources_json")"
    remote_setup_memory_min_mib="$(jq -r '.remote_setup.memory_min_mib' "$resources_json")"
    remote_setup_memory_max_mib="$(jq -r '.remote_setup.memory_max_mib' "$resources_json")"
    if [ "$llm_server_enabled" = "true" ]; then
        cpus="$(jq -r '.llm_server.cpus' "$resources_json")"
        memory_mib="$(jq -r '.llm_server.memory_mib' "$resources_json")"
        CPUS="$cpus" MEMORY_MIB="$memory_mib" \
          OMNIROUTE_CPUS_MIN="$omniroute_cpus_min" OMNIROUTE_CPUS_MAX="$omniroute_cpus_max" \
          OMNIROUTE_MEMORY_MIN_MIB="$omniroute_memory_min_mib" OMNIROUTE_MEMORY_MAX_MIB="$omniroute_memory_max_mib" \
          REMOTE_SETUP_CPUS_MIN="$remote_setup_cpus_min" REMOTE_SETUP_CPUS_MAX="$remote_setup_cpus_max" \
          REMOTE_SETUP_MEMORY_MIN_MIB="$remote_setup_memory_min_mib" REMOTE_SETUP_MEMORY_MAX_MIB="$remote_setup_memory_max_mib" \
          yq -i '
            .resources.llm_server.cpus = (strenv(CPUS) | tonumber) |
            .resources.llm_server.memory_mib = (strenv(MEMORY_MIB) | tonumber) |
            .resources.omniroute.cpus_min = (strenv(OMNIROUTE_CPUS_MIN) | tonumber) |
            .resources.omniroute.cpus_max = (strenv(OMNIROUTE_CPUS_MAX) | tonumber) |
            .resources.omniroute.memory_min_mib = (strenv(OMNIROUTE_MEMORY_MIN_MIB) | tonumber) |
            .resources.omniroute.memory_max_mib = (strenv(OMNIROUTE_MEMORY_MAX_MIB) | tonumber) |
            .resources.remote_setup.cpus_min = (strenv(REMOTE_SETUP_CPUS_MIN) | tonumber) |
            .resources.remote_setup.cpus_max = (strenv(REMOTE_SETUP_CPUS_MAX) | tonumber) |
            .resources.remote_setup.memory_min_mib = (strenv(REMOTE_SETUP_MEMORY_MIN_MIB) | tonumber) |
            .resources.remote_setup.memory_max_mib = (strenv(REMOTE_SETUP_MEMORY_MAX_MIB) | tonumber)
          ' "$CONFIG_PATH"
        log_info "reserved ${cpus} CPUs, ${memory_mib} MiB RAM for llm-server"
    else
        OMNIROUTE_CPUS_MIN="$omniroute_cpus_min" OMNIROUTE_CPUS_MAX="$omniroute_cpus_max" \
          OMNIROUTE_MEMORY_MIN_MIB="$omniroute_memory_min_mib" OMNIROUTE_MEMORY_MAX_MIB="$omniroute_memory_max_mib" \
          REMOTE_SETUP_CPUS_MIN="$remote_setup_cpus_min" REMOTE_SETUP_CPUS_MAX="$remote_setup_cpus_max" \
          REMOTE_SETUP_MEMORY_MIN_MIB="$remote_setup_memory_min_mib" REMOTE_SETUP_MEMORY_MAX_MIB="$remote_setup_memory_max_mib" \
          yq -i '
            .resources.omniroute.cpus_min = (strenv(OMNIROUTE_CPUS_MIN) | tonumber) |
            .resources.omniroute.cpus_max = (strenv(OMNIROUTE_CPUS_MAX) | tonumber) |
            .resources.omniroute.memory_min_mib = (strenv(OMNIROUTE_MEMORY_MIN_MIB) | tonumber) |
            .resources.omniroute.memory_max_mib = (strenv(OMNIROUTE_MEMORY_MAX_MIB) | tonumber) |
            .resources.remote_setup.cpus_min = (strenv(REMOTE_SETUP_CPUS_MIN) | tonumber) |
            .resources.remote_setup.cpus_max = (strenv(REMOTE_SETUP_CPUS_MAX) | tonumber) |
            .resources.remote_setup.memory_min_mib = (strenv(REMOTE_SETUP_MEMORY_MIN_MIB) | tonumber) |
            .resources.remote_setup.memory_max_mib = (strenv(REMOTE_SETUP_MEMORY_MAX_MIB) | tonumber)
          ' "$CONFIG_PATH"
    fi
else
    # A host too small to reserve the fixed floors is exactly the host
    # where an uncapped container is most dangerous -- render_compose()
    # treats cpus/memory_mib == 0 as "no explicit limit", so proceeding to
    # render with a stale or absent persisted value here would silently
    # disable the safety mechanism precisely when it matters most. Fail
    # loudly instead, exactly like setup/setup.sh's Step 8 already does.
    die "$(jq -r '.error' "$resources_json")"
fi

bash "${REPO_DIR}/setup/render-unit.sh"

port="$(yq -r '.server.port' "$CONFIG_PATH")"
log_step "Starting the service"
systemctl --user start "${UNIT_NAME}.service"

if [ "$llm_server_enabled" = "true" ]; then
    log_step "Waiting for health"
    # Probe 127.0.0.1 explicitly: "localhost" resolves to ::1 first on this system while
    # podman publishes the port on 0.0.0.0 (IPv4), so a localhost probe would never connect.
    if ! wait_for_health "$port"; then
        log_error "server did not become healthy within ${LLM_ENV_HEALTH_TIMEOUT_SECONDS}s"
        echo "  Logs: podman compose -f ${COMPOSE_FILE} logs"
        exit 1
    fi
    log_info "server is ready"
fi

if [ "$system_one_enabled" = "true" ]; then
    log_step "Waiting for System One"
    system_one_port="$(yq -r '.system_one.port' "$CONFIG_PATH")"
    if ! LLM_ENV_HEALTH_TIMEOUT_SECONDS="$LLM_ENV_SYSTEM_ONE_HEALTH_TIMEOUT_SECONDS" \
            wait_for_health "$system_one_port"; then
        log_error "System One did not become healthy within ${LLM_ENV_SYSTEM_ONE_HEALTH_TIMEOUT_SECONDS}s"
        echo "  Logs: podman compose -f ${COMPOSE_FILE} logs system-one"
        exit 1
    fi
    log_info "System One is ready"
    # First decision after start pays GPU warm-up (~1.6s); a failure must not fail start.
    warmup_body='{"model":"jev-1","state":{"ticket":"I was charged twice this month, please refund me"},"questions":{"team":{"type":"choice","instructions":"Which team should handle this ticket?","criteria":{"billing":"charges, invoices, refunds","technical":"bugs, crashes, outages","other":"everything else"}}}}'
    if ! curl -fsS --max-time 10 -o /dev/null \
            -H "Content-Type: application/json" \
            --data-raw "$warmup_body" \
            "http://127.0.0.1:${system_one_port}/v1/systemone" 2>/dev/null; then
        log_warn "System One warm-up request failed"
    fi
fi

bash "${REPO_DIR}/setup/network.sh"

log_step "Ensuring the OmniRoute admission-lease healer is installed"
bash "${REPO_DIR}/setup/enable-omniroute-heal.sh" \
    || log_warn "could not enable the OmniRoute healer timer; run 'bash setup/enable-omniroute-heal.sh' manually"

log_step "Waiting for OmniRoute"
omniroute_port="$(yq -r '.omniroute.port' "$CONFIG_PATH")"
if wait_for_tcp_port "$omniroute_port"; then
    log_info "OmniRoute is ready"
    if [ "$llm_server_enabled" = "true" ]; then
        provisioned=0
        for attempt in 1 2 3; do
            if response="$(llmenv --config "$CONFIG_PATH" omniroute provision)"; then
                provisioned=1
                break
            fi
            [ "$attempt" -eq 3 ] || sleep 2
        done
        if [ "$provisioned" -eq 1 ]; then
            log_info "OmniRoute connection configured"
        else
            jq -r '.error // "provisioning failed"' <<<"$response" >&2
            log_warn "OmniRoute provisioning failed; configure it manually via the dashboard"
        fi
    else
        log_info "llm-server disabled -- skipping local OmniRoute provider provisioning; configure providers via the OmniRoute dashboard."
    fi
    bash "${REPO_DIR}/scripts/provider-provision.sh" \
        || log_warn "codex-provision failed; run 'make codex-provision' manually"
else
    log_warn "OmniRoute did not become reachable within ${LLM_ENV_HEALTH_TIMEOUT_SECONDS}s; configure it manually"
fi
