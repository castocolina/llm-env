#!/usr/bin/env bash
# omniroute-combo-context.sh — report each combo's real minimum context
# window, correcting for OmniRoute's own /api/combos display bug.
#
# OmniRoute's "computed_context_length" field on /api/combos is not
# trustworthy: it resolves the same stale "synced -> registry -> spec"
# chain as /v1/models and never consults the manual context-window
# override table, so it stays wrong even after `make fix-codex-context`
# corrects the underlying override. This script walks the live catalog and
# combo membership directly and applies the known corrections itself, so
# the printed minimum is the actual number an orchestrator can use to
# decide when to split a request -- before the provider ever rejects it.
#
# Usage: scripts/omniroute-combo-context.sh [combo-name]
set -euo pipefail
# shellcheck disable=SC1091 # Resolved from this script at runtime.
# shellcheck source=../tools/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/../tools/lib.sh"

require_cmd yq jq uv

[ -f "$CONFIG_PATH" ] || die "no config at ${CONFIG_PATH}; run 'make setup' first"
migrate_config_file || die "configuration migration failed"

omniroute_port="$(yq -r '.omniroute.port' "$CONFIG_PATH")"

log_step "Checking OmniRoute is reachable"
if ! wait_for_tcp_port "$omniroute_port"; then
    die "OmniRoute is not reachable on 127.0.0.1:${omniroute_port} within ${LLM_ENV_HEALTH_TIMEOUT_SECONDS}s; run 'make start' first"
fi
log_info "OmniRoute is ready"

log_step "Computing real combo context windows"
combo_args=()
if [ "${1-}" != "" ]; then
    combo_args=(--combo "$1")
fi
result="$(llmenv --config "$CONFIG_PATH" omniroute combo-context "${combo_args[@]}")" \
    || die "$(echo "$result" | jq -r '.error // "combo-context failed"')"

count="$(echo "$result" | jq '.combos | length')"
if [ "$count" -eq 0 ]; then
    log_info "no matching combo found"
else
    echo "$result" | jq -r '.combos[] |
        "\(.combo): min_context_window=\(.min_context_window // "unknown")",
        (.members[] | "    \(.provider)/\(.model) -> \(.context_window // "unknown")")'
fi
