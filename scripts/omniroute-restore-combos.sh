#!/usr/bin/env bash
# omniroute-restore-combos.sh — restore a snapshot written by
# omniroute-backup-combos.sh: for each backed-up combo, find it by name
# (recreating it if it's gone) and put its saved model list back.
#
# Only undoes combo edits made on the SAME OmniRoute instance the backup was
# taken from -- each combo model entry pins a connectionId, which is not
# portable across instances. Idempotent: safe to re-run.
#
# Usage: scripts/omniroute-restore-combos.sh [input-path]
#   input-path defaults to the most recently written file under
#   $(dirname "$CONFIG_PATH")/combo-backups/
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

input_path="${1:-}"
if [ -z "$input_path" ]; then
    backup_dir="$(dirname "$CONFIG_PATH")/combo-backups"
    input_path="$(find "$backup_dir" -maxdepth 1 -name '*.json' -printf '%T@ %p\n' 2>/dev/null \
        | sort -rn | head -n1 | cut -d' ' -f2- || true)"
    [ -n "$input_path" ] || die "no backup path given and none found under ${backup_dir}; run 'make backup-combos' first"
fi
[ -f "$input_path" ] || die "no combo backup at ${input_path}"

log_step "Restoring OmniRoute combos from ${input_path}"
result="$(llmenv --config "$CONFIG_PATH" omniroute restore-combos --input "$input_path")" \
    || die "$(echo "$result" | jq -r '.error // "restore-combos failed"')"

echo "$result" | jq -r '.restored[] | "  \(.combo): \(.action) (\(.models) model(s))"'
count="$(echo "$result" | jq '.restored | length')"
log_info "restored ${count} combo(s) from ${input_path}"
