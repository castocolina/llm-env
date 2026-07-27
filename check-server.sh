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

# The router enforces the API key on inference endpoints only; /health and /v1/models
# answer unauthenticated, so probing those would never detect a missing key.
log_step "Authentication"
auth_probe_body="$(jq -n '{model: "x", messages: [{role: "user", content: "x"}], max_tokens: 1}')"
code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 \
    -K "$bad_conf" -H "Content-Type: application/json" \
    -d "$auth_probe_body" "${base}/v1/chat/completions")"
if [ "$code" = "401" ]; then
    ok "an invalid API key is rejected on inference (401)"
else
    bad "an invalid API key returned HTTP ${code} on inference, expected 401"
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
          messages: [{role: "user", content: "Reply with exactly: ready"}],
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
    normalized="$(printf '%s' "$content" | tr '[:upper:]' '[:lower:]' | \
        sed -E 's/^[[:space:][:punct:]]+//; s/[[:space:][:punct:]]+$//')"
    if [ "$normalized" = ready ]; then
        ok "${alias}: returned ready"
    elif [ -z "$content" ]; then
        bad "${alias}: empty assistant content"
    else
        bad "${alias}: expected ready, got $(printf '%.80s' "$content")"
    fi
done < <(yq -r '.models[] | select(.enabled) | .alias' "$CONFIG_PATH")

echo
log_step "Results: ${PASS} passed, ${FAIL} failed"
[ "$FAIL" -eq 0 ]
