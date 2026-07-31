#!/usr/bin/env bash
# key-reset.sh — rotate the server API key without changing service state.
set -euo pipefail
# shellcheck disable=SC1091 # Resolved from this script at runtime.
# shellcheck source=../tools/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/../tools/lib.sh"

require_cmd uv jq yq systemctl

[ -f "$CONFIG_PATH" ] || die "no config at ${CONFIG_PATH}; run 'make setup' first"
migrate_config_file || die "configuration migration failed"

reset_api_key
log_info "API key reset"

if systemctl --user is-active --quiet "${UNIT_NAME}.service"; then
    bash "${REPO_DIR}/scripts/stop.sh"
    bash "${REPO_DIR}/scripts/start.sh"
else
    log_info "the new API key will apply on the next start"
fi
