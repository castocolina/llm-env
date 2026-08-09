#!/usr/bin/env bash
# disable-boot.sh — do not start the server at boot.
set -euo pipefail
# shellcheck disable=SC1091 # Resolved from this script at runtime.
# shellcheck source=../tools/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/../tools/lib.sh"

require_cmd yq systemctl

[ -f "$CONFIG_PATH" ] || die "no config at ${CONFIG_PATH}; run 'make setup' first"

yq -i '.server.start_at_boot = false' "$CONFIG_PATH"

unit="$WRAPPER_UNIT_PATH"
if [ -f "$unit" ]; then
    # Disable while [Install] is still present so systemd can find and
    # remove the enablement symlink it created; only then drop the section
    # so the generator stops rendering it as wanted at boot.
    systemctl --user disable "${UNIT_NAME}.service" 2>/dev/null || true
    sed -i '/^\[Install\]$/,$d' "$unit"
    log_info "removed [Install] from the unit"
fi
systemctl --user daemon-reload
log_info "the server will no longer start at boot. Lingering is unchanged; run"
log_info "  loginctl disable-linger ${USER}   to also stop user services at logout."
