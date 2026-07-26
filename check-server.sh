#!/usr/bin/env bash
# check-server.sh — assert the running server honours its API contract.
set -uo pipefail
# shellcheck source=lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
set +e

require_cmd curl jq yq

port="$(yq -r '.server.port' "$CONFIG_PATH")"
api_key="$(yq -r '.server.api_key' "$CONFIG_PATH")"

auth_conf="$(mktemp)"
chmod 600 "$auth_conf"
printf 'header = "Authorization: Bearer %s"\n' "$api_key" > "$auth_conf"
bad_conf="$(mktemp)"
chmod 600 "$bad_conf"
printf 'header = "Authorization: Bearer definitely-not-the-key"\n' > "$bad_conf"
trap 'rm -f "$auth_conf" "$bad_conf"' EXIT

# 127.0.0.1 rather than localhost: localhost resolves to ::1 first on this system
# while podman publishes the port on 0.0.0.0 (IPv4), so localhost never connects.
base="http://127.0.0.1:${port}"

PASS=0; FAIL=0
ok()   { log_info "$1"; PASS=$((PASS + 1)); }
bad()  { log_error "$1"; FAIL=$((FAIL + 1)); }

log_step "Health"
if curl -fsS --max-time 10 -o /dev/null "${base}/health"; then
    ok "/health responds"
else
    bad "/health did not respond; is the server running?"
    log_step "Results: ${PASS} passed, ${FAIL} failed"
    exit 1
fi

log_step "Authentication"
code="$(curl -s --max-time 10 -o /dev/null -w '%{http_code}' \
        -K "$bad_conf" "${base}/v1/models")"
if [ "$code" = "401" ]; then
    ok "an invalid API key is rejected (401)"
else
    bad "an invalid API key returned HTTP ${code}, expected 401"
fi

log_step "Model listing"
listed="$(curl -fsS --max-time 10 -K "$auth_conf" "${base}/v1/models" \
          | jq -r '[.data[].id] | sort | join(",")')"
expected="$(yq -r '[.models[] | select(.enabled) | .alias] | sort | join(",")' "$CONFIG_PATH")"
if [ "$listed" = "$expected" ]; then
    ok "/v1/models lists exactly: ${expected}"
else
    bad "/v1/models listed '${listed}', expected '${expected}'"
fi

log_step "Completions"
while read -r alias; do
    [ -n "$alias" ] || continue
    body="$(jq -n --arg m "$alias" \
        '{model: $m,
          messages: [{role: "user", content: "Reply with the single word: ready"}],
          max_tokens: 16, stream: false}')"

    response="$(curl -fsS --max-time 120 \
        -K "$auth_conf" \
        -H "Content-Type: application/json" \
        -d "$body" "${base}/v1/chat/completions")"

    if [ -z "$response" ]; then
        bad "${alias}: request failed"
        continue
    fi

    content="$(jq -r '.choices[0].message.content // empty' <<<"$response")"
    if [ -n "$content" ]; then
        ok "${alias}: returned $(printf '%q' "$(head -c 40 <<<"$content")")"
    else
        bad "${alias}: empty content — $(jq -c '.error // .' <<<"$response" | head -c 120)"
    fi
done < <(yq -r '.models[] | select(.enabled) | .alias' "$CONFIG_PATH")

echo
log_step "Results: ${PASS} passed, ${FAIL} failed"
[ "$FAIL" -eq 0 ]
