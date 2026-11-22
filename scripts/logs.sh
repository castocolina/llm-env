#!/usr/bin/env bash
# logs.sh — follow the compose stack logs.
set -euo pipefail
# shellcheck disable=SC1091 # Resolved from this script at runtime.
# shellcheck source=../tools/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/../tools/lib.sh"
require_cmd setsid podman

main_pid=""
cleanup() {
    local status=$?
    trap - EXIT INT TERM
    [[ -z "$main_pid" ]] || kill -- "-$main_pid" 2>/dev/null || true
    [[ -z "$main_pid" ]] || wait "$main_pid" 2>/dev/null || true
    exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

setsid podman compose -f "$COMPOSE_FILE" logs -f &
main_pid=$!
wait "$main_pid"
