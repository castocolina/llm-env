#!/usr/bin/env bash
# disable-boot.sh — do not start the server at boot.
set -euo pipefail
# shellcheck source=lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

require_cmd yq systemctl

[ -f "$CONFIG_PATH" ] || die "no config at ${CONFIG_PATH}; run 'make setup' first"

yq -i '.server.start_at_boot = false' "$CONFIG_PATH"

unit="${QUADLET_DIR}/${UNIT_NAME}.container"
if [ -f "$unit" ]; then
    # Drop the [Install] section so the generator stops wanting it at boot.
    sed -i '/^\[Install\]$/,$d' "$unit"
    log_info "removed [Install] from the unit"
fi
systemctl --user daemon-reload
log_info "the server will no longer start at boot. Lingering is unchanged; run"
log_info "  loginctl disable-linger ${USER}   to also stop user services at logout."
