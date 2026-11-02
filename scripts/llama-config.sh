#!/usr/bin/env bash
# llama-config.sh — enable or disable local llama inference in models.yml:
# timestamped backup, edit on a private copy, atomic rename. Config only.
set -euo pipefail
# shellcheck disable=SC1091 # Resolved from this script at runtime.
# shellcheck source=../tools/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/../tools/lib.sh"

# Dies with llmenv's JSON .error, or $2 when its output ($1) is not JSON or has no error.
die_with_error() { local msg; msg="$(jq -r '.error // empty' <<<"$1" 2>/dev/null || true)"; die "${msg:-$2}"; }

usage() { printf '%s\n' "$1" >&2; exit 2; }

action="${1:-}"
alias_name="${2:-}"
case "$action" in
    enable) [[ -n "$alias_name" ]] || usage "usage: make llama-enable MODEL=<alias>" ;;
    disable) ;;
    *) usage "usage: llama-config.sh enable <alias> | disable" ;;
esac

require_cmd uv jq yq
[[ -f "$CONFIG_PATH" ]] || die "configuration not found: ${CONFIG_PATH}; run 'make setup' first"

# apply_llama WORK: the edit, on the private copy WORK. llmenv prints JSON on both outcomes; keep it off stdout and parse it only on failure.
apply_llama() {
    local work="$1" output
    if [[ "$action" == enable ]]; then
        if ! output="$(llmenv --config "$work" models select "$alias_name")"; then
            die_with_error "$output" "models select failed"
        fi
        yq -i '.llm_server.enabled = true' "$work"
    else
        yq -i '.llm_server.enabled = false' "$work"
    fi
}

edit_config_atomically "" apply_llama

if [[ "$action" == enable ]]; then
    log_info "llama enabled with model ${alias_name}; run 'make start-llama' (or 'make start' when the stack is down)"
else
    log_info "llama disabled; llm-server keeps running until 'make stop-llama' (frees the GPU now) or 'make restart' (applies gateway-only mode to the whole stack)"
fi
