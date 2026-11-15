#!/usr/bin/env bash
# enable-omniroute-heal.sh — install and enable a systemd --user timer that
# runs scripts/heal-omniroute-admission.sh every 5 minutes, restarting the
# omniroute container ONLY when it detects a stuck chat-admission lease
# (upstream diegosouzapw/OmniRoute#14456) or, while idle, sustained high
# memory (rate-limited), not on every busy-but-healthy check. See heal-omniroute-admission.sh's own header for the detection
# logic and why a blind periodic restart would be too disruptive.
set -euo pipefail
# shellcheck disable=SC1091 # Resolved from this script at runtime.
# shellcheck source=../tools/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/../tools/lib.sh"

require_cmd systemctl

[ -f "$CONFIG_PATH" ] || die "no config at ${CONFIG_PATH}; run 'make setup' first"

install_user_timer "${OMNIROUTE_HEAL_UNIT_NAME}" "llm-env OmniRoute healer (stuck admission lease, idle memory recycle)" \
    "Check every 5 minutes for a stuck OmniRoute admission lease" \
    scripts/heal-omniroute-admission.sh OnBootSec=5min OnUnitActiveSec=5min
log_info "enabled ${OMNIROUTE_HEAL_UNIT_NAME}.timer (checks every 5min; only restarts omniroute after 3 consecutive stuck checks, ~10min+ of confirmed saturation)"
log_info "check its schedule: systemctl --user list-timers ${OMNIROUTE_HEAL_UNIT_NAME}.timer"
log_info "run it right now:   systemctl --user start ${OMNIROUTE_HEAL_UNIT_NAME}.service"
log_info "tune the strike threshold: OMNIROUTE_HEAL_STRIKE_THRESHOLD=<n> via 'systemctl --user edit ${OMNIROUTE_HEAL_UNIT_NAME}.service' (a drop-in survives this rewrite)"
log_info "memory recycle: OMNIROUTE_HEAL_MEMORY_MIB (default 60% of omniroute memory.max, 0 disables), OMNIROUTE_HEAL_MEMORY_SAMPLES (3), OMNIROUTE_HEAL_MEMORY_COOLDOWN_HOURS (6); max 2 per 24h"
