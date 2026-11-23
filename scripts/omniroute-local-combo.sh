#!/usr/bin/env bash
# omniroute-local-combo.sh — create or update the local-only OmniRoute combo
# my-local-worker from the capacity card, and list every enabled model with a
# card on Dashboard > Providers > llama.cpp. Skips (exit 0) unless llm-server is
# enabled and running; llmenv skips again when the card or interference gate is
# not current. Never stops, restarts or recreates a container.
#
# Usage: scripts/omniroute-local-combo.sh
set -euo pipefail
# shellcheck disable=SC1091 # Resolved from this script at runtime.
# shellcheck source=../tools/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/../tools/lib.sh"

require_cmd yq jq uv podman

[ -f "$CONFIG_PATH" ] || die "no config at ${CONFIG_PATH}; run 'make setup' first"
migrate_config_file || die "configuration migration failed"

if [ "$(yq -r '.llm_server.enabled' "$CONFIG_PATH")" != "true" ]; then
    log_info "llm-server disabled -- skipping my-local-worker"
    exit 0
fi
if ! container_running llm-server; then
    log_info "llm-server is not running -- skipping my-local-worker"
    exit 0
fi

omniroute_port="$(yq -r '.omniroute.port' "$CONFIG_PATH")"
wait_for_tcp_port "$omniroute_port" \
    || die "OmniRoute is not reachable on 127.0.0.1:${omniroute_port} within ${LLM_ENV_HEALTH_TIMEOUT_SECONDS}s; run 'make start' first"

result="$(llmenv --config "$CONFIG_PATH" omniroute ensure-local-combo)" \
    || die "$(jq -r '.error // "ensure-local-combo failed"' <<<"$result")"

jq -r '"my-local-worker: \(.action)" + (if .reason then " (\(.reason))" else "" end)
    + (if .output_action then "; output limit \(.max_output_tokens): \(.output_action)" else "" end)
    + (if (.provider_models // {}) != {} then "; llama-cpp provider models: "
        + (.provider_models | to_entries | map("\(.key) \(.value)") | join(", ")) else "" end)' <<<"$result"
