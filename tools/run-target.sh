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

cols="$(tput cols 2>/dev/null || echo 80)"
# Plain ASCII, not a box-drawing character: tr's SET2 is byte-wise, so a
# multi-byte UTF-8 replacement char corrupts under a non-UTF-8 locale.
printf -v separator '%*s' "$cols" ''
separator="${separator// /-}"

printf '\n%s%s%s%s\n' "$BOLD" "$BLUE" "$separator" "$NC"
printf '%s%s▶ %s%s\n' "$BOLD" "$BLUE" "$name" "$NC"
started_at=$(date +%s)
status=0
"$@" || status=$?
elapsed=$(( $(date +%s) - started_at ))

if [ "$status" -eq 0 ]; then
    printf '%s%s■ %s end — ok (%ss)%s\n' "$BOLD" "$GREEN" "$name" "$elapsed" "$NC"
    printf '%s%s%s%s\n' "$BOLD" "$GREEN" "$separator" "$NC"
else
    printf '%s%s■ %s end — failed, exit %s (%ss)%s\n' "$BOLD" "$RED" "$name" "$status" "$elapsed" "$NC"
    printf '%s%s%s%s\n' "$BOLD" "$RED" "$separator" "$NC"
fi
exit "$status"
