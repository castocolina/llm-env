#!/usr/bin/env bash
# provider-provision.sh — import local CLI-tool auth sessions into OmniRoute
# as named provider connections, bypassing each tool's normal OAuth flow
# (which requires a loopback callback the OmniRoute container can't receive
# without SSH port-forwarding). Currently: Codex CLI (~/.codex/auth.json ->
# the "cco-cl" connection).
#
# Safe to run standalone against an already-running OmniRoute
# (`make provider-provision`, no server restart needed), or chained after
# `make start` once OmniRoute's own health check has passed -- creates the
# connection on first run, updates its tokens on every later run.
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

codex_auth_path="${HOME}/.codex/auth.json"
if [ -f "$codex_auth_path" ]; then
    log_step "Importing Codex session (cco-cl)"
    if result="$(llmenv --config "$CONFIG_PATH" omniroute import-codex --auth-path "$codex_auth_path" --name cco-cl)"; then
        action="$(echo "$result" | jq -r '.action')"
        log_info "Codex connection cco-cl ${action}"
    else
        echo "$result" | jq -r '.error // "import failed"' >&2
        log_warn "Codex import failed; connect it manually via the OmniRoute dashboard"
    fi
else
    log_info "no local Codex session at ${codex_auth_path}; skipping"
fi
