#!/usr/bin/env bash
# profile.sh — make profile-apply / profile-check: merge the tracked profile onto models.yml, or report drift.
# apply backs models.yml up first and writes only when something changes; check is read-only and exits 1 on drift.
set -euo pipefail
# shellcheck disable=SC1091 # Resolved from this script at runtime.
# shellcheck source=../tools/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/../tools/lib.sh"

action="${1:-}"
case "$action" in
    apply | check) ;;
    *) die "usage: profile.sh apply|check" ;;
esac
require_cmd uv jq
[ -f "$PROFILE_PATH" ] || die "no profile at ${PROFILE_PATH}; create one with 'uv run llmenv.py profile export'"
[ -f "$CONFIG_PATH" ] || die "no config at ${CONFIG_PATH}; run 'make setup' first"

status=0
result="$(llmenv --config "$CONFIG_PATH" profile "$action" --profile "$PROFILE_PATH")" || status=$?
jq -r '
  if .error then "error: \(.error)"
  else
    ((.differences // [])[] | "  \(.path)" + (if has("live") then ": \(.live | tojson) -> \(.profile | tojson)" else "" end)),
    (if .backup then "backup: \(.backup)" else empty end),
    (if has("drift") then (if .drift then "drift: models.yml differs from the profile (make profile-apply)" else "no drift" end)
     elif .changed then "applied the profile" else "no change: models.yml already matches the profile" end)
  end' <<<"$result"
exit "$status"
