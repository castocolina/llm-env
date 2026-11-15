#!/usr/bin/env bash
# enable-omniroute-zai-reset.sh — install and enable a systemd --user timer that runs
# scripts/zai-reset-weekly.sh every 5 minutes: it redeems a z.ai weekly-quota reset card once the
# weekly quota reaches 99 %, at most once per weekly window (see pylib/zai_reset.py).
set -euo pipefail
# shellcheck disable=SC1091 # Resolved from this script at runtime.
# shellcheck source=../tools/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/../tools/lib.sh"

require_cmd systemctl

[ -f "$CONFIG_PATH" ] || die "no config at ${CONFIG_PATH}; run 'make setup' first"

install_user_timer "${ZAI_RESET_UNIT_NAME}" "llm-env z.ai weekly quota reset (redeems a reset card at 99 % weekly use)" \
    "Check every 5 minutes whether the z.ai weekly quota needs its reset card" \
    scripts/zai-reset-weekly.sh OnBootSec=5min OnUnitActiveSec=5min
log_info "enabled ${ZAI_RESET_UNIT_NAME}.timer (every 5min; redeems a WEEK reset card at 99 % weekly use, once per window)"
log_info "run it now without redeeming: bash ${REPO_DIR}/scripts/zai-reset-weekly.sh --dry-run"
