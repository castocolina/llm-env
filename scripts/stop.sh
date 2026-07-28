#!/usr/bin/env bash
# stop.sh — stop the server.
set -euo pipefail
# shellcheck disable=SC1091 # Resolved from this script at runtime.
# shellcheck source=../tools/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/../tools/lib.sh"

require_cmd systemctl

if ! systemctl --user list-unit-files "${UNIT_NAME}.service" >/dev/null 2>&1; then
    log_warn "unit ${UNIT_NAME}.service is not installed; nothing to stop"
    exit 0
fi

if systemctl --user is-active --quiet "${UNIT_NAME}.service"; then
    systemctl --user stop "${UNIT_NAME}.service"
    log_info "server stopped"
else
    log_warn "server is not running"
fi
