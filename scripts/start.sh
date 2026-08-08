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
    echo "$budget" | jq -r '"  short by \(.shortfall_mib) MiB"'
    echo "$budget" | jq -r '.remedies[] | "    - \(.)"'
    die "VRAM budget exceeded for models_max=${models_max}; adjust the config and retry"
fi
echo "$budget" | jq -r '"  available \(.available_mib) MiB, required \(.required_mib) MiB"'

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
