#!/usr/bin/env bash
# start.sh — generate the runtime key, render, and start the server.
set -euo pipefail
# shellcheck source=lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

require_cmd uv jq yq systemctl curl

[ -f "$CONFIG_PATH" ] || die "no config at ${CONFIG_PATH}; run 'make setup' first"

ensure_api_key

models_max="$(yq -r '.runtime.models_max' "$CONFIG_PATH")"
[ "$models_max" -gt 0 ] || die "no models enabled; run 'make setup'"

log_step "Checking the VRAM budget"
if ! budget="$(llmenv --config "$CONFIG_PATH" budget --models-dir "$MODELS_DIR")"; then
    echo "$budget" | jq -r '"  short by \(.shortfall_mib) MiB"'
    echo "$budget" | jq -r '.remedies[] | "    - \(.)"'
    die "VRAM budget exceeded for models_max=${models_max}; adjust the config and retry"
fi
echo "$budget" | jq -r '"  available \(.available_mib) MiB, required \(.required_mib) MiB"'

bash "${REPO_DIR}/render-unit.sh"

port="$(yq -r '.server.port' "$CONFIG_PATH")"
log_step "Starting the service"
systemctl --user start "${UNIT_NAME}.service"

log_step "Waiting for health"
# Probe 127.0.0.1 explicitly: "localhost" resolves to ::1 first on this system while
# podman publishes the port on 0.0.0.0 (IPv4), so a localhost probe would never connect.
for _ in $(seq 1 60); do
    if curl -fsS -o /dev/null "http://127.0.0.1:${port}/health" 2>/dev/null; then
        log_info "server is ready"
        bash "${REPO_DIR}/network.sh"
        exit 0
    fi
    sleep 1
done

log_error "server did not become healthy within 60s"
echo "  Logs: journalctl --user -u ${UNIT_NAME}.service -n 50"
exit 1
