#!/usr/bin/env bash
# omniroute-backup-combos.sh — snapshot every OmniRoute Combo (name + model
# list + strategy) to a timestamped JSON file, so a risky combo edit made
# through the dashboard can be undone without falling back to the UI's own
# full-database backup button (which snapshots everything -- connections,
# API keys, all of it -- not just combos).
#
# Safe to run standalone and repeatedly (make backup-combos); each run
# writes a new timestamped file, never overwriting a previous backup.
#
# Usage: scripts/omniroute-backup-combos.sh [output-path]
#   output-path defaults to
#   $(dirname "$CONFIG_PATH")/combo-backups/<UTC-timestamp>.json
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

output_path="${1:-}"
if [ -z "$output_path" ]; then
    backup_dir="$(dirname "$CONFIG_PATH")/combo-backups"
    mkdir -p "$backup_dir"
    output_path="${backup_dir}/$(date -u +%Y%m%dT%H%M%SZ).json"
fi

log_step "Backing up OmniRoute combos"
result="$(llmenv --config "$CONFIG_PATH" omniroute backup-combos --output "$output_path")" \
    || die "$(echo "$result" | jq -r '.error // "backup-combos failed"')"

count="$(echo "$result" | jq -r '.combos')"
log_info "backed up ${count} combo(s) to ${output_path}"
