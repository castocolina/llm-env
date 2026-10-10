#!/usr/bin/env bash
# check-provider-tokens.sh — flag any OmniRoute provider connection that
# needs attention (testStatus != "active": expired auth, exhausted quota,
# billing, or a policy block), so it's caught ahead of time instead of
# failing a long-running job.
#
# Known blind spot: testStatus reflects OmniRoute's own connection-level
# health check, which only validates the credential (see
# `POST /api/providers/<id>/test`, "source":"local") -- it does not send a
# real completion, so it cannot see a provider that returns HTTP 200 with an
# error buried in the body (e.g. "Insufficient balance", "resource
# pressure", "recharge" -- the same soft-fail patterns some combos guard
# against via their own `responseValidation.forbiddenSubstrings`, see the
# "my-plan-review" combo in the OmniRoute dashboard). Catching that class of
# failure here would require sending a real, token-costing completion per
# connection on every run; deliberately not done -- if a model looks fine
# here but a combo run still fails on one of those substrings, that combo's
# own forbiddenSubstrings/fallback handling is the source of truth, not
# this script.
set -euo pipefail
# shellcheck disable=SC1091 # Resolved from this script at runtime.
# shellcheck source=../tools/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/../tools/lib.sh"

require_cmd curl jq yq

[ -f "$CONFIG_PATH" ] || die "no config at ${CONFIG_PATH}; run 'make setup' first"
migrate_config_file || die "configuration migration failed"

omniroute_port="$(yq -r '.omniroute.port' "$CONFIG_PATH")"
omniroute_password="$(yq -r '.omniroute.initial_password' "$CONFIG_PATH")"
omniroute_base="http://127.0.0.1:${omniroute_port}"

cookie_jar="$(mktemp)"
trap 'rm -f "$cookie_jar"' EXIT

log_step "Logging in to the OmniRoute dashboard"
login_body="$(jq -n --arg p "$omniroute_password" '{password: $p}')"
login_status="$(curl -sS -o /dev/null -w '%{http_code}' -c "$cookie_jar" \
    --max-time 10 -H 'Content-Type: application/json' --data-raw "$login_body" \
    "${omniroute_base}/api/auth/login")" \
    || die "could not reach OmniRoute at ${omniroute_base}; is 'make start' running?"
[ "$login_status" = "200" ] || die "OmniRoute dashboard login failed (HTTP ${login_status})"

log_step "Checking provider connection health"
connections_json="$(curl -sS --max-time 10 -b "$cookie_jar" "${omniroute_base}/api/providers")" \
    || die "could not list OmniRoute provider connections"

unhealthy="$(printf '%s' "$connections_json" | jq -c '
    [.connections[]? | select(.testStatus != "active") |
        {provider, name, testStatus, lastError}]
')" || die "could not parse the OmniRoute provider listing"

count="$(printf '%s' "$unhealthy" | jq 'length')"
if [ "$count" -eq 0 ]; then
    total="$(printf '%s' "$connections_json" | jq '.connections | length')"
    log_info "all ${total} provider connection(s) are active"
    exit 0
fi

log_warn "${count} provider connection(s) need attention:"
summary_lines="$(printf '%s' "$unhealthy" | jq -r '
    .[] | "  - \(.provider)/\(.name): \(.testStatus)" +
        (if .lastError then " (\(.lastError))" else "" end)
')"
printf '%s\n' "$summary_lines"

if command -v notify-send >/dev/null 2>&1; then
    # testStatus covers more than expired auth (e.g. "unavailable" for a
    # quota/billing block, "banned" for a policy block) -- don't claim
    # "reauthentication" for those, just surface the real status/reason.
    notify_body="$(printf '%s' "$unhealthy" | jq -r '
        .[] | "\(.provider)/\(.name): \(.testStatus)" +
            (if .lastError then " (\(.lastError))" else "" end)
    ')"
    notify-send --urgency=critical \
        "OmniRoute: ${count} provider connection(s) need attention" \
        "$notify_body" \
        || log_warn "could not send a desktop notification"
fi

exit 1
