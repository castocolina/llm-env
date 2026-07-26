#!/usr/bin/env bash
# enable-boot.sh — start the server automatically at boot.
set -euo pipefail
# shellcheck source=lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

require_cmd yq loginctl systemctl

[ -f "$CONFIG_PATH" ] || die "no config at ${CONFIG_PATH}; run 'make setup' first"

yq -i '.server.start_at_boot = true' "$CONFIG_PATH"
loginctl enable-linger "$USER"
log_info "lingering enabled for ${USER}"

if [ -f "${QUADLET_DIR}/${UNIT_NAME}.container" ]; then
    bash "${REPO_DIR}/start.sh"
    log_info "unit re-rendered with [Install]"
fi
systemctl --user daemon-reload
log_info "the server will now start at boot. Verify with: make status"
