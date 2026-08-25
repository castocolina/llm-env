#!/usr/bin/env bash
# omniroute-connections-backup.sh — write provider-connection metadata
# (provider, name, id -- never secrets) to a JSON file.
#
# GET /api/providers never returns raw credentials (OAuth connections carry
# no token field at all; API-key connections have their key masked), so
# this is NOT a secret-preserving backup -- there is no way to recover a
# connection's actual auth from it. What it DOES enable: passing this file
# is unnecessary for restore-combos itself (it always queries the live
# connections directly), but this file is useful as a reference of what
# connections existed and by what name/provider, e.g. after a `make clean`
# to know what to re-add via the OmniRoute dashboard.
#
# Usage: scripts/omniroute-connections-backup.sh [output-path]
# Defaults to ~/.config/llm-env/combo-backups/connections-<timestamp>.json
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
output_path="${1:-${backup_dir}/connections-$(date +%Y%m%dT%H%M%S).json}"
mkdir -p "$(dirname "$output_path")"

log_step "Backing up connection metadata"
result="$(llmenv --config "$CONFIG_PATH" omniroute backup-connections --output "$output_path")" \
    || die "$(echo "$result" | jq -r '.error // "backup-connections failed"')"

count="$(echo "$result" | jq -r '.count')"
log_info "backed up ${count} connection(s) (metadata only, no secrets) to ${output_path}"
