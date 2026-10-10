#!/usr/bin/env bash
# heal-omniroute-admission.sh — detect and clear a stuck OmniRoute
# chat-admission lease (upstream diegosouzapw/OmniRoute#14456: a client that
# disconnects while a heavy request's handler is still pending never
# releases its admission slot, so the effective concurrency cap silently
# ratchets down by one per leak until every subsequent heavy request 503s
# with "Chat admission capacity is temporarily unavailable"). Not fixed
# upstream as of OmniRoute 3.8.50/51 -- only a process restart clears a
# genuinely stuck lease (this resets the in-memory counter; it does not
# need a full container recreation since no env/config is changing).
#
# Deliberately conservative about restarting: `podman restart omniroute`
# drops every in-flight SSE stream, including ones that are working fine.
# This only fires when GET /api/monitoring/health's chatAdmission block
# shows activeHeavy pinned at capacity AND shedTotal still climbing across
# several consecutive samples with zero drops back to a lower value in
# between -- a real, still-in-progress heavy request completes in tens of
# seconds to a few minutes and activeHeavy drops when it does, so several
# samples in a row with no drop is the signature of a leak, not load.
set -euo pipefail
# shellcheck disable=SC1091 # Resolved from this script at runtime.
# shellcheck source=../tools/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/../tools/lib.sh"

require_cmd curl jq yq podman

# How many consecutive stuck samples (this script's own invocations, spaced
# by the systemd timer below) before healing. At the default 5-minute timer
# interval, 3 strikes is >=10 minutes of continuous, non-recovering
# saturation -- comfortably past any legitimate single heavy request's
# lifetime, short enough that a real leak isn't left biting for hours.
STRIKE_THRESHOLD="${OMNIROUTE_HEAL_STRIKE_THRESHOLD:-3}"
STATE_FILE="${LLM_ENV_HEAL_STATE_FILE:-${HOME}/.cache/llm-env/omniroute-admission-heal-state.json}"
mkdir -p "$(dirname "$STATE_FILE")"

[ -f "$CONFIG_PATH" ] || die "no config at ${CONFIG_PATH}; run 'make setup' first"

omniroute_port="$(yq -r '.omniroute.port' "$CONFIG_PATH")"
omniroute_password="$(yq -r '.omniroute.initial_password' "$CONFIG_PATH")"
omniroute_base="http://127.0.0.1:${omniroute_port}"

cookie_jar="$(mktemp)"
trap 'rm -f "$cookie_jar"' EXIT

login_body="$(jq -n --arg p "$omniroute_password" '{password: $p}')"
login_status="$(curl -sS -o /dev/null -w '%{http_code}' -c "$cookie_jar" \
    --max-time 10 -H 'Content-Type: application/json' --data-raw "$login_body" \
    "${omniroute_base}/api/auth/login")" \
    || die "could not reach OmniRoute at ${omniroute_base}; is 'make start' running?"
[ "$login_status" = "200" ] || die "OmniRoute dashboard login failed (HTTP ${login_status})"

health_json="$(curl -sS --max-time 10 -b "$cookie_jar" "${omniroute_base}/api/monitoring/health")" \
    || die "could not read OmniRoute health"

active_heavy="$(printf '%s' "$health_json" | jq -r '.chatAdmission.activeHeavy // 0')"
shed_total="$(printf '%s' "$health_json" | jq -r '.chatAdmission.shedTotal // 0')"

prev_shed_total=0
prev_strikes=0
if [ -f "$STATE_FILE" ]; then
    prev_shed_total="$(jq -r '.shedTotal // 0' "$STATE_FILE" 2>/dev/null || echo 0)"
    prev_strikes="$(jq -r '.strikes // 0' "$STATE_FILE" 2>/dev/null || echo 0)"
fi

if [ "$active_heavy" -gt 0 ] && [ "$shed_total" -gt "$prev_shed_total" ]; then
    strikes=$((prev_strikes + 1))
else
    strikes=0
fi

jq -n --argjson shedTotal "$shed_total" --argjson strikes "$strikes" \
    '{shedTotal: $shedTotal, strikes: $strikes}' > "$STATE_FILE"

if [ "$strikes" -lt "$STRIKE_THRESHOLD" ]; then
    log_info "chatAdmission: activeHeavy=${active_heavy} shedTotal=${shed_total} strikes=${strikes}/${STRIKE_THRESHOLD} -- not restarting"
    exit 0
fi

log_warn "chatAdmission stuck for ${strikes} consecutive checks (activeHeavy=${active_heavy}, shedTotal=${shed_total} still climbing) -- restarting omniroute to clear the leaked lease"
podman restart omniroute
rm -f "$STATE_FILE"

if command -v notify-send >/dev/null 2>&1; then
    notify-send --urgency=normal \
        "OmniRoute: restarted to clear a stuck admission lease" \
        "activeHeavy was pinned at ${active_heavy} across ${strikes} checks (upstream #14456). In-flight requests were dropped." \
        || log_warn "could not send a desktop notification"
fi
