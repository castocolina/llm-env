#!/usr/bin/env bash
# enable-boot.sh — start the server automatically at boot.
set -euo pipefail
# shellcheck source=lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

require_cmd yq loginctl systemctl

[ -f "$CONFIG_PATH" ] || die "no config at ${CONFIG_PATH}; run 'make setup' first"

yq -i '.server.start_at_boot = true' "$CONFIG_PATH"
ensure_api_key
bash "${REPO_DIR}/render-unit.sh"
log_info "unit rendered with [Install]"

loginctl enable-linger "$USER"
log_info "lingering enabled for ${USER}"
log_info "the server will now start at boot. Verify with: make status"
