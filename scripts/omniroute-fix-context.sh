#!/usr/bin/env bash
# omniroute-fix-context.sh — correct OmniRoute's understanding of the codex
# GPT-5.6 family's (sol/terra/luna) real context window.
#
# OmniRoute's synced catalog caps every effort-suffixed GPT-5.6 variant
# (e.g. "gpt-5.6-sol-high") at Codex CLI's conservative 272K product-layer
# default, even though the real API supports up to 1,050,000 tokens and
# OmniRoute's own bundled model spec already knows this for the bare id.
# See pylib/omniroute.py::fix_codex_context_overrides for the full writeup,
# including live verification against a real OmniRoute instance.
#
# Safe to run standalone and repeatedly (make fix-codex-context) -- each
# run just re-affirms the same override values.
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

log_step "Correcting codex GPT-5.6 context-window overrides"
result="$(llmenv --config "$CONFIG_PATH" omniroute fix-codex-context)" \
    || die "$(echo "$result" | jq -r '.error // "fix-codex-context failed"')"

count="$(echo "$result" | jq '.fixed | length')"
if [ "$count" -eq 0 ]; then
    log_info "no codex GPT-5.6 models found in the catalog; nothing to fix"
else
    echo "$result" | jq -r '.fixed[] | "  \(.model) -> \(.context_window) tokens"'
    log_info "corrected ${count} codex GPT-5.6 model(s)"
fi
