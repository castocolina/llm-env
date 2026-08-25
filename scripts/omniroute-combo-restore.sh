#!/usr/bin/env bash
# omniroute-combo-restore.sh — restore combos from an
# omniroute-combo-backup.sh JSON file.
#
# A combo whose name already exists is left untouched unless --overwrite is
# passed, in which case it is updated in place; new combos are always
# created. Safe to re-run: without --overwrite it only ever fills in what's
# missing.
#
# Usage: scripts/omniroute-combo-restore.sh <backup-path> [--overwrite]
set -euo pipefail
# shellcheck disable=SC1091 # Resolved from this script at runtime.
# shellcheck source=../tools/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/../tools/lib.sh"

require_cmd yq jq uv

input_path="${1-}"
[ -n "$input_path" ] || die "usage: scripts/omniroute-combo-restore.sh <backup-path> [--overwrite]"
[ -f "$input_path" ] || die "no backup file at ${input_path}"

overwrite_args=()
if [ "${2-}" = "--overwrite" ]; then
    overwrite_args=(--overwrite)
fi

[ -f "$CONFIG_PATH" ] || die "no config at ${CONFIG_PATH}; run 'make setup' first"
migrate_config_file || die "configuration migration failed"

omniroute_port="$(yq -r '.omniroute.port' "$CONFIG_PATH")"

log_step "Checking OmniRoute is reachable"
if ! wait_for_tcp_port "$omniroute_port"; then
    die "OmniRoute is not reachable on 127.0.0.1:${omniroute_port} within ${LLM_ENV_HEALTH_TIMEOUT_SECONDS}s; run 'make start' first"
fi
log_info "OmniRoute is ready"

log_step "Restoring combos from ${input_path}"
result="$(llmenv --config "$CONFIG_PATH" omniroute restore-combos --input "$input_path" "${overwrite_args[@]}")" \
    || die "$(echo "$result" | jq -r '.error // "restore-combos failed"')"

echo "$result" | jq -r '.restored[] | "  \(.combo): \(.action)"'
created="$(echo "$result" | jq '[.restored[] | select(.action == "created")] | length')"
updated="$(echo "$result" | jq '[.restored[] | select(.action == "updated")] | length')"
skipped="$(echo "$result" | jq '[.restored[] | select(.action == "skipped")] | length')"
log_info "created ${created}, updated ${updated}, skipped ${skipped}"
