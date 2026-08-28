#!/usr/bin/env bash
# disable-token-check.sh — stop and remove the daily provider-token-check timer.
set -euo pipefail
# shellcheck disable=SC1091 # Resolved from this script at runtime.
# shellcheck source=../tools/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/../tools/lib.sh"

require_cmd systemctl

service_unit="${HOME}/.config/systemd/user/${TOKEN_CHECK_UNIT_NAME}.service"
timer_unit="${HOME}/.config/systemd/user/${TOKEN_CHECK_UNIT_NAME}.timer"

if [ ! -f "$timer_unit" ] && [ ! -f "$service_unit" ]; then
    log_warn "${TOKEN_CHECK_UNIT_NAME}.timer is not installed; nothing to do"
    exit 0
fi

systemctl --user disable --now "${TOKEN_CHECK_UNIT_NAME}.timer" 2>/dev/null || true
rm -f "$service_unit" "$timer_unit"
systemctl --user daemon-reload
log_info "removed ${TOKEN_CHECK_UNIT_NAME}.timer and ${TOKEN_CHECK_UNIT_NAME}.service"
