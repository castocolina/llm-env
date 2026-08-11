#!/usr/bin/env bash
# start.sh — generate the runtime key, render, and start the server.
set -euo pipefail
# shellcheck disable=SC1091 # Resolved from this script at runtime.
# shellcheck source=../tools/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/../tools/lib.sh"

require_cmd uv jq yq systemctl curl

[ -f "$CONFIG_PATH" ] || die "no config at ${CONFIG_PATH}; run 'make setup' first"

migrate_config_file || die "configuration migration failed"
ensure_api_key
ensure_omniroute_secrets

models_max="$(yq -r '.runtime.models_max' "$CONFIG_PATH")"
[ "$models_max" -gt 0 ] || die "no models enabled; run 'make setup'"

if systemctl --user is-active --quiet "${UNIT_NAME}.service"; then
    log_step "Stopping the active router before measuring VRAM"
    systemctl --user stop "${UNIT_NAME}.service"
fi

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

log_step "Computing resource limits"
resources_json="$(mktemp)"
trap 'rm -f "$resources_json"' EXIT
if llmenv --config "$CONFIG_PATH" resources > "$resources_json"; then
    cpus="$(jq -r '.llm_server.cpus' "$resources_json")"
    memory_mib="$(jq -r '.llm_server.memory_mib' "$resources_json")"
    omniroute_cpus="$(jq -r '.omniroute.cpus' "$resources_json")"
    omniroute_memory_mib="$(jq -r '.omniroute.memory_mib' "$resources_json")"
    CPUS="$cpus" MEMORY_MIB="$memory_mib" \
      OMNIROUTE_CPUS="$omniroute_cpus" OMNIROUTE_MEMORY_MIB="$omniroute_memory_mib" \
      yq -i '
        .resources.llm_server.cpus = (strenv(CPUS) | tonumber) |
        .resources.llm_server.memory_mib = (strenv(MEMORY_MIB) | tonumber) |
        .resources.omniroute.cpus = (strenv(OMNIROUTE_CPUS) | tonumber) |
        .resources.omniroute.memory_mib = (strenv(OMNIROUTE_MEMORY_MIB) | tonumber)
      ' "$CONFIG_PATH"
    log_info "reserved ${cpus} CPUs, ${memory_mib} MiB RAM for llm-server"
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

log_step "Waiting for health"
# Probe 127.0.0.1 explicitly: "localhost" resolves to ::1 first on this system while
# podman publishes the port on 0.0.0.0 (IPv4), so a localhost probe would never connect.
if wait_for_health "$port"; then
    log_info "server is ready"
    bash "${REPO_DIR}/setup/network.sh"

    log_step "Waiting for OmniRoute"
    omniroute_port="$(yq -r '.omniroute.port' "$CONFIG_PATH")"
    if wait_for_tcp_port "$omniroute_port"; then
        log_info "OmniRoute is ready"
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
        log_warn "OmniRoute did not become reachable within ${LLM_ENV_HEALTH_TIMEOUT_SECONDS}s; configure it manually"
    fi
    exit 0
fi

log_error "server did not become healthy within ${LLM_ENV_HEALTH_TIMEOUT_SECONDS}s"
echo "  Logs: podman compose -f ${COMPOSE_FILE} logs"
exit 1
