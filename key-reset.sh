#!/usr/bin/env bash
# key-reset.sh — rotate the server API key without changing service state.
set -euo pipefail
# shellcheck source=lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

require_cmd yq systemctl

[ -f "$CONFIG_PATH" ] || die "no config at ${CONFIG_PATH}; run 'make setup' first"

api_key="$(head -c 32 /dev/urandom | base64 | tr -d '/+=\n')"
API_KEY="$api_key" yq -i '.server.api_key = strenv(API_KEY)' "$CONFIG_PATH"
chmod 600 "$CONFIG_PATH"
log_info "API key reset"

if systemctl --user is-active --quiet "${UNIT_NAME}.service"; then
    bash "${REPO_DIR}/stop.sh"
    bash "${REPO_DIR}/start.sh"
else
    log_info "the new API key will apply on the next start"
fi
