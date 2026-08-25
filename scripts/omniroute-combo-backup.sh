#!/usr/bin/env bash
# omniroute-combo-backup.sh — write a lightweight, combo-scoped backup.
#
# The OmniRoute dashboard's "backup" button (Settings -> System/Storage)
# snapshots the *entire* SQLite database -- every connection, every API
# key, everything -- via GET /api/db-backups/export. This script instead
# uses the purpose-built GET/POST/PUT /api/combos endpoints to back up
# only combo definitions, so a routine "save my combo setup before I
# experiment" doesn't require handling a full-DB secrets dump.
#
# Usage: scripts/omniroute-combo-backup.sh [output-path]
# Defaults to ~/.config/llm-env/combo-backups/combos-<timestamp>.json
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

backup_dir="${HOME}/.config/llm-env/combo-backups"
output_path="${1:-${backup_dir}/combos-$(date +%Y%m%dT%H%M%S).json}"
mkdir -p "$(dirname "$output_path")"

log_step "Backing up combos"
result="$(llmenv --config "$CONFIG_PATH" omniroute backup-combos --output "$output_path")" \
    || die "$(echo "$result" | jq -r '.error // "backup-combos failed"')"

count="$(echo "$result" | jq -r '.count')"
log_info "backed up ${count} combo(s) to ${output_path}"
