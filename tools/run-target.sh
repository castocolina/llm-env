#!/usr/bin/env bash
# run-target.sh — wrap a make target's command with a colored start/end banner.
set -euo pipefail
# shellcheck disable=SC1091 # Resolved from this script at runtime.
# shellcheck source=./lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

[ "$#" -ge 2 ] || { echo "usage: run-target.sh <name> -- <command...>" >&2; exit 64; }
name="$1"
shift
[ "$1" = -- ] || { echo "usage: run-target.sh <name> -- <command...>" >&2; exit 64; }
shift

printf '\n%s%s▶ %s%s\n' "$BOLD" "$BLUE" "$name" "$NC"
started_at=$(date +%s)
status=0
"$@" || status=$?
elapsed=$(( $(date +%s) - started_at ))

if [ "$status" -eq 0 ]; then
    printf '%s%s■ %s end — ok (%ss)%s\n' "$BOLD" "$GREEN" "$name" "$elapsed" "$NC"
else
    printf '%s%s■ %s end — failed, exit %s (%ss)%s\n' "$BOLD" "$RED" "$name" "$status" "$elapsed" "$NC"
fi
exit "$status"
