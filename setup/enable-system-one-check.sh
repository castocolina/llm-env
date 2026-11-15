#!/usr/bin/env bash
# enable-system-one-check.sh — install and enable a weekly systemd --user timer that runs
# scripts/system-one-check.sh --save: a read-only report of the Laya pin against upstream, saved to
# ~/.local/state/llm-env/system-one-check.json for `make status`. It never builds or swaps anything;
# `make update-system-one` does that.
set -euo pipefail
# shellcheck disable=SC1091 # Resolved from this script at runtime.
# shellcheck source=../tools/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/../tools/lib.sh"

require_cmd systemctl
install_user_timer "$SYSTEM_ONE_CHECK_UNIT_NAME" "llm-env System One (Laya) upstream check (read-only report)" \
    "Weekly check of the Laya pin against upstream" \
    "scripts/system-one-check.sh --save" OnCalendar=weekly RandomizedDelaySec=1h
log_info "enabled ${SYSTEM_ONE_CHECK_UNIT_NAME}.timer (weekly; report only, apply with make update-system-one)"
