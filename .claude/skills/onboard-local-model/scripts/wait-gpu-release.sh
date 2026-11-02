#!/usr/bin/env bash
# wait-gpu-release.sh — poll preflight.sh until no GPU holder is a llama-server process, e.g. after `make stop-llama`.
# usage: wait-gpu-release.sh [--timeout SECONDS (default 60)] [--interval SECONDS (default 5)]
# Prints {"released": true, "used_mib": X, "waited_s": T} and exits 0 once .gpu.holders lists no `llama-server` comm. Used VRAM
# is reported, never compared: System One (a python3 holder) may reload about 3.7 GiB during the wait. After --timeout seconds
# (the GPU is always read at least once) prints {"released": false, "used_mib": X, "holders": [...]} and exits 1. A null or
# missing holders list is unreadable: it never releases.
# ONBOARD_PREFLIGHT overrides the preflight command (test seam). Read-only: it never stops or starts anything.
set -euo pipefail

bound=60; interval=5
while [ $# -gt 0 ]; do
    case "$1" in
    --timeout|--interval)
        [ $# -ge 2 ] || { echo "$1 needs a value" >&2; exit 2; }
        case "$1" in --timeout) bound="$2" ;; *) interval="$2" ;; esac
        [[ "$2" =~ ^[0-9]+$ ]] || { echo "$1 must be a non-negative integer, got '$2'" >&2; exit 2; }
        shift 2 ;;
    -h|--help) sed -n '2,/^set -/{/^set -/!p}' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown flag: $1" >&2; exit 2 ;;
    esac
done

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
preflight="${ONBOARD_PREFLIGHT:-$here/preflight.sh}"
SECONDS=0
while :; do
    state="$(bash "$preflight" | jq -c '.gpu | {used_mib, holders}')"
    # an unreadable holder list (null or missing) never counts as released; it ends at the timeout check
    if jq -e '(.holders | type) == "array" and all(.holders[]; .comm != "llama-server")' <<<"$state" >/dev/null; then
        jq -c --argjson waited "$SECONDS" '{released: true, used_mib, waited_s: $waited}' <<<"$state"
        exit 0
    fi
    if [ "$SECONDS" -ge "$bound" ]; then
        jq -c '{released: false, used_mib, holders: (.holders // [])}' <<<"$state"
        exit 1
    fi
    sleep "$interval"
done
