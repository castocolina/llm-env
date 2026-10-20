#!/usr/bin/env bash
# logs.sh — follow main-stack and separate Phoenix project logs.
set -euo pipefail
# shellcheck disable=SC1091 # Resolved from this script at runtime.
# shellcheck source=../tools/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/../tools/lib.sh"
require_cmd setsid podman

main_pid=""
phoenix_pid=""
cleanup() {
    local status=$?
    trap - EXIT INT TERM
    for pid in "$main_pid" "$phoenix_pid"; do
        [[ -n "$pid" ]] || continue
        kill -- "-$pid" 2>/dev/null || true
    done
    for pid in "$main_pid" "$phoenix_pid"; do
        [[ -n "$pid" ]] || continue
        wait "$pid" 2>/dev/null || true
    done
    exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# Separate sessions let us terminate/reap exactly these two follow commands
# and their descendants on exit or interrupt, without broad pkill matching.
setsid podman compose -f "$COMPOSE_FILE" logs -f &
main_pid=$!
phoenix_id="$(podman ps -q --filter 'label=llm-env.project=llm-env-phoenix' \
    --filter 'label=com.docker.compose.service=phoenix' | head -n 1)"
if [[ -n "$phoenix_id" ]]; then
    setsid podman logs --follow "$phoenix_id" &
    phoenix_pid=$!
else
    log_warn "Phoenix container is not running; following main-stack logs only"
fi

pids=("$main_pid")
[[ -n "$phoenix_pid" ]] && pids+=("$phoenix_pid")
wait -n "${pids[@]}"
