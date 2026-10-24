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
#
# Memory recycle: OmniRoute's resident memory creeps up over days, and its
# own pressure guard starts shedding requests with 503 well before the cgroup
# cap. When the lease check does not restart, the container is restarted
# only if ALL hold for OMNIROUTE_HEAL_MEMORY_SAMPLES consecutive runs: it is
# idle (no admitted, queued or in-flight request and no call log newer than
# 120 s; undeterminable activity counts as busy), and its cgroup memory read
# from the host (memory.current minus reclaimable file cache, no exec) is
# above the threshold. Memory restarts are also spaced by
# OMNIROUTE_HEAL_MEMORY_COOLDOWN_HOURS and capped at 2 per rolling 24 h; both
# are kept in the state file.
# Threshold: 60 % of the running container's own cgroup memory.max (1228 MiB
# for the 2048 MiB default), the same cap the guard divides by, so the order
#   threshold (0.60) < guard high (0.85) < guard critical (0.92) < cap
# holds for any cap: an idle, bloated process is recycled before a normal burst
# (~750 MiB observed) can push it into the guard's 503 shedding.
# OMNIROUTE_HEAL_MEMORY_MIB sets an absolute threshold instead (a warning is
# logged if it is at or above the guard's high ratio); 0 disables the recycle.
# "Idle" is the updater's own API-only rule (`omniroute-update activity`: the
# reviewed activity profile of the running image; internal provider-test probe
# rows are not activity; an unprofiled image or row route is unknown, so no
# recycle). A memory recycle is skipped while an OmniRoute update holds the
# update lock, and holds that lock itself through the restart. The state file
# is rewritten atomically; a missing, truncated or garbage one counts as zeros.
set -euo pipefail
# shellcheck disable=SC1091 # Resolved from this script at runtime.
# shellcheck source=../tools/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/../tools/lib.sh"

require_cmd curl jq yq podman uv flock

# How many consecutive stuck samples (this script's own invocations, spaced
# by the systemd timer below) before healing. At the default 5-minute timer
# interval, 3 strikes is >=10 minutes of continuous, non-recovering
# saturation -- comfortably past any legitimate single heavy request's
# lifetime, short enough that a real leak isn't left biting for hours.
STRIKE_THRESHOLD="${OMNIROUTE_HEAL_STRIKE_THRESHOLD:-3}"
STATE_FILE="${LLM_ENV_HEAL_STATE_FILE:-${HOME}/.cache/llm-env/omniroute-admission-heal-state.json}"
MEMORY_MIB="${OMNIROUTE_HEAL_MEMORY_MIB:-}"   # empty: MEMORY_CAP_PCT of the container's memory.max
MEMORY_CAP_PCT=60
GUARD_HIGH_PCT=85   # OmniRoute's own pressure guard sheds load at 0.85 of memory.max
MEMORY_SAMPLES="${OMNIROUTE_HEAL_MEMORY_SAMPLES:-3}"
MEMORY_COOLDOWN_HOURS="${OMNIROUTE_HEAL_MEMORY_COOLDOWN_HOURS:-6}"
MEMORY_RESTARTS_PER_DAY=2
IDLE_SECONDS=120
CGROUP_ROOT="${LLM_ENV_CGROUP_ROOT:-/sys/fs/cgroup}"
[[ "$MEMORY_MIB" =~ ^(|0|[1-9][0-9]{0,6})$ ]] \
    || die "OMNIROUTE_HEAL_MEMORY_MIB must be 0 (disabled) or a positive integer, got '${MEMORY_MIB}'"
[[ "$MEMORY_SAMPLES" =~ ^[1-9][0-9]{0,3}$ ]] \
    || die "OMNIROUTE_HEAL_MEMORY_SAMPLES must be a positive integer, got '${MEMORY_SAMPLES}'"
[[ "$MEMORY_COOLDOWN_HOURS" =~ ^[1-9][0-9]{0,3}$ ]] \
    || die "OMNIROUTE_HEAL_MEMORY_COOLDOWN_HOURS must be a positive integer, got '${MEMORY_COOLDOWN_HOURS}'"
mkdir -p "$(dirname "$STATE_FILE")"
unset COMPOSE_FILE COMPOSE_INSPECT_DIR   # the llmenv CLI rejects every COMPOSE_* variable
llm=(uv run --project "$REPO_DIR" "$REPO_DIR/llmenv.py" omniroute-update)

[ -f "$CONFIG_PATH" ] || die "no config at ${CONFIG_PATH}; run 'make setup' first"

omniroute_port="$(yq -r '.omniroute.port' "$CONFIG_PATH")"
omniroute_password="$(yq -r '.omniroute.initial_password' "$CONFIG_PATH")"
omniroute_base="http://127.0.0.1:${omniroute_port}"

tmpd="$(private_work_dir)"               # 0700: cookie jar, password file, inspect document
trap 'rm -rf "$tmpd"' EXIT
trap_signals_to_exit
cookie_jar="$tmpd/cookies"
( umask 077; printf '%s' "$omniroute_password" >"$tmpd/pw" )

login_body="$(jq -n --arg p "$omniroute_password" '{password: $p}')"
login_status="$(curl -sS -o /dev/null -w '%{http_code}' -c "$cookie_jar" \
    --max-time 10 -H 'Content-Type: application/json' --data-raw "$login_body" \
    "${omniroute_base}/api/auth/login")" \
    || die "could not reach OmniRoute at ${omniroute_base}; is 'make start' running?"
[ "$login_status" = "200" ] || die "OmniRoute dashboard login failed (HTTP ${login_status})"

# The health endpoint is stale-while-revalidate: a lone read answers the snapshot the PREVIOUS request computed (up
# to this timer's whole interval old at idle). Prime it, then read the snapshot the prime computed (PRIME_GAP_S).
curl -sS --max-time 10 -b "$cookie_jar" -o /dev/null "${omniroute_base}/api/monitoring/health" \
    || die "could not read OmniRoute health"
sleep 0.2
health_json="$(curl -sS --max-time 10 -b "$cookie_jar" "${omniroute_base}/api/monitoring/health")" \
    || die "could not read OmniRoute health"

# A non-negative integer, or 0 for anything else (missing field, garbage, several JSON values, empty input).
int_or_zero() { if [[ "$1" =~ ^[0-9]{1,15}$ ]]; then echo "$1"; else echo 0; fi; }
active_heavy="$(int_or_zero "$(jq -r '.chatAdmission.activeHeavy // 0' <<<"$health_json" 2>/dev/null || true)")"
shed_total="$(int_or_zero "$(jq -r '.chatAdmission.shedTotal // 0' <<<"$health_json" 2>/dev/null || true)")"

# Every value read back from the state file is validated: a truncated or hand-edited file must never stop the
# stuck-lease check (it once made write_state fail on every run).
state_field() { int_or_zero "$(jq -r --arg k "$1" '.[$k] // 0' "$STATE_FILE" 2>/dev/null || true)"; }
prev_shed_total=0
prev_strikes=0
prev_mem_strikes=0
mem_restarts='[]'
if [ -f "$STATE_FILE" ]; then
    prev_shed_total="$(state_field shedTotal)"
    prev_strikes="$(state_field strikes)"
    prev_mem_strikes="$(state_field memStrikes)"
    mem_restarts="$(jq -c '[.memRestarts // [] | .[] | numbers | select(. >= 0 and . < 1e15) | floor]' "$STATE_FILE" 2>/dev/null || true)"
    [[ "$mem_restarts" =~ ^\[([0-9]{1,15}(,[0-9]{1,15})*)?\]$ ]] || mem_restarts='[]'
fi
now="$(date +%s)"
# Only restarts inside the rolling 24 h window matter for the cooldown and the cap.
mem_restarts="$(jq -c --argjson now "$now" '[.[] | select(. > $now - 86400)]' <<<"$mem_restarts")"

write_state() {  # shedTotal strikes memStrikes
    jq -n --argjson shedTotal "$1" --argjson strikes "$2" --argjson memStrikes "$3" \
        --argjson memRestarts "$mem_restarts" \
        '{shedTotal: $shedTotal, strikes: $strikes, memStrikes: $memStrikes, memRestarts: $memRestarts}' \
        >"$STATE_FILE.tmp" && mv -f "$STATE_FILE.tmp" "$STATE_FILE"   # atomic: a crash never leaves a partial file
}

if [ "$active_heavy" -gt 0 ] && [ "$shed_total" -gt "$prev_shed_total" ]; then
    strikes=$((prev_strikes + 1))
else
    strikes=0
fi

write_state "$shed_total" "$strikes" "$prev_mem_strikes"

if [ "$strikes" -ge "$STRIKE_THRESHOLD" ]; then
    log_warn "chatAdmission stuck for ${strikes} consecutive checks (activeHeavy=${active_heavy}, shedTotal=${shed_total} still climbing) -- restarting omniroute to clear the leaked lease"
    podman restart omniroute
    # A fresh process starts at shedTotal 0; the memory-restart history survives.
    write_state 0 0 0
    if command -v notify-send >/dev/null 2>&1; then
        notify-send --urgency=normal \
            "OmniRoute: restarted to clear a stuck admission lease" \
            "activeHeavy was pinned at ${active_heavy} across ${strikes} checks (upstream #14456). In-flight requests were dropped." \
            || log_warn "could not send a desktop notification"
    fi
    exit 0
fi
log_info "chatAdmission: activeHeavy=${active_heavy} shedTotal=${shed_total} strikes=${strikes}/${STRIKE_THRESHOLD} -- not restarting"

if [ "$MEMORY_MIB" = 0 ]; then
    write_state "$shed_total" "$strikes" 0
    exit 0
fi

# Prints "busy", "idle" or "unknown(<reason>)": the updater's API-only activity sample over the running image's
# reviewed profile (probe rows ignored, no client trace younger than IDLE_SECONDS), plus this healer's own
# chatAdmission.activeHeavy check. Any failed read is unknown.
activity() {
    local out rc=0
    jq -e '(.chatAdmission.activeHeavy | type) == "number"' <<<"$health_json" >/dev/null 2>&1 || { echo unknown; return; }
    [ "$active_heavy" = 0 ] || { echo busy; return; }
    podman inspect omniroute >"$tmpd/inspect.json" 2>/dev/null || { echo unknown; return; }
    out="$("${llm[@]}" activity --inspect "$tmpd/inspect.json" --port "$omniroute_port" \
        --password-file "$tmpd/pw" --min-wait "$IDLE_SECONDS" 2>/dev/null)" || rc=$?
    case "$rc $out" in
        "0 idle") echo idle ;;
        "75 deferred: busy") echo busy ;;
        *) echo "unknown(${out#deferred: })" ;;
    esac
}

# "<anon MiB> <cap MiB|max>" from the host cgroup: anonymous plus kernel memory
# (memory.current minus reclaimable file cache, as OmniRoute's own guard
# measures it) and the container's memory.max.
memory_read() {
    local path dir current file cap
    path="$(podman inspect --format '{{.State.CgroupPath}}' omniroute 2>/dev/null)" || return 1
    [[ -n "$path" && "$path" != / ]] || return 1
    dir="${CGROUP_ROOT}${path}"
    current="$(cat "$dir/memory.current" 2>/dev/null)" || return 1
    file="$(awk '$1 == "file" {print $2}' "$dir/memory.stat" 2>/dev/null)" || return 1
    cap="$(cat "$dir/memory.max" 2>/dev/null)" || return 1
    [[ "$current" =~ ^[0-9]+$ && "$file" =~ ^[0-9]+$ && "$cap" =~ ^([0-9]+|max)$ ]] || return 1
    [ "$cap" = max ] || cap=$((cap / 1048576))
    echo "$(((current - file) / 1048576)) ${cap}"
}

state_now="$(activity)"
if ! reading="$(memory_read)"; then
    write_state "$shed_total" "$strikes" 0
    log_warn "memory recycle: cannot read omniroute cgroup memory -- not restarting"
    exit 0
fi
read -r mem cap <<<"$reading"
threshold="$MEMORY_MIB"
if [ -z "$threshold" ]; then
    if [ "$cap" = max ]; then
        write_state "$shed_total" "$strikes" 0
        log_warn "memory recycle: omniroute has no memory cap to derive a threshold from; set OMNIROUTE_HEAL_MEMORY_MIB -- not restarting"
        exit 0
    fi
    threshold=$((cap * MEMORY_CAP_PCT / 100))
elif [ "$cap" != max ] && [ "$threshold" -ge $((cap * GUARD_HIGH_PCT / 100)) ]; then
    log_warn "memory recycle: OMNIROUTE_HEAL_MEMORY_MIB=${threshold} is at or above the guard's high mark ($((cap * GUARD_HIGH_PCT / 100))MiB of a ${cap}MiB cap); the guard sheds before a recycle"
fi
if [ "$state_now" != idle ] || [ "$mem" -le "$threshold" ]; then
    write_state "$shed_total" "$strikes" 0
    log_info "memory recycle: activity=${state_now} memory=${mem}MiB threshold=${threshold}MiB cap=${cap}MiB -- not restarting"
    exit 0
fi
mem_strikes=$((prev_mem_strikes + 1))
if [ "$mem_strikes" -lt "$MEMORY_SAMPLES" ]; then
    write_state "$shed_total" "$strikes" "$mem_strikes"
    log_info "memory recycle: idle at ${mem}MiB > ${threshold}MiB, strikes=${mem_strikes}/${MEMORY_SAMPLES} -- not restarting yet"
    exit 0
fi
# Keep the strikes at the threshold so the restart happens on the first run after a skip ends.
write_state "$shed_total" "$strikes" "$MEMORY_SAMPLES"
last_restart="$(jq -r 'max // 0' <<<"$mem_restarts")"
if [ "$(jq length <<<"$mem_restarts")" -ge "$MEMORY_RESTARTS_PER_DAY" ]; then
    log_warn "memory recycle: idle at ${mem}MiB for ${mem_strikes} checks, skipped -- cap of ${MEMORY_RESTARTS_PER_DAY} memory restarts per 24h reached"
    exit 0
fi
if [ $((now - last_restart)) -lt $((MEMORY_COOLDOWN_HOURS * 3600)) ]; then
    log_warn "memory recycle: idle at ${mem}MiB for ${mem_strikes} checks, skipped -- cooldown of ${MEMORY_COOLDOWN_HOURS}h since the last memory restart"
    exit 0
fi
# The update lock (shared with make update-omniroute) is held through the restart: never recycle under an update.
lock_file="$("${llm[@]}" lock-path 2>/dev/null)" || lock_file=""
if [[ -z "$lock_file" ]] || ! exec 8>>"$lock_file" || ! flock -n 8; then
    log_warn "memory recycle: idle at ${mem}MiB for ${mem_strikes} checks, skipped -- an OmniRoute update holds the update lock (or it cannot be taken)"
    exit 0
fi
mem_restarts="$(jq -c --argjson now "$now" '. + [$now]' <<<"$mem_restarts")"
# Record the attempt before restarting so a failing restart still counts toward the cap.
write_state 0 0 0
log_warn "memory recycle: idle at ${mem}MiB > ${threshold}MiB for ${mem_strikes} consecutive checks -- restarting omniroute"
podman restart omniroute 8>&-   # the restarted container (conmon) must not inherit the lock
