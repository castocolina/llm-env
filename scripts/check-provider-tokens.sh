#!/usr/bin/env bash
# check-provider-tokens.sh — flag any OmniRoute provider connection that
# needs re-authentication (testStatus != "active"), so a broken OAuth
# session is caught ahead of time instead of failing a long-running job.
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
    notify_body="$(printf '%s' "$unhealthy" | jq -r '.[] | "\(.provider)/\(.name): \(.testStatus)"')"
    notify-send --urgency=critical \
        "OmniRoute: ${count} provider connection(s) need reauthentication" \
        "$notify_body" \
        || log_warn "could not send a desktop notification"
fi

exit 1
