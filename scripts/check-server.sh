#!/usr/bin/env bash
# check-server.sh — assert the running server honours its API contract.
set -uo pipefail
# shellcheck disable=SC1091 # Resolved from this script at runtime.
# shellcheck source=../tools/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/../tools/lib.sh"
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
diagnostic_dir="$(prepare_diagnostic_dir server)"

cleanup() {
    local status=$?
    finish_diagnostic_dir "$diagnostic_dir"
    rm -f "$auth_conf" "$bad_conf"
    exit "$status"
}
trap cleanup EXIT

# 127.0.0.1 rather than localhost: localhost resolves to ::1 first on this system
# while podman publishes the port on 0.0.0.0 (IPv4), so localhost never connects.
base="http://127.0.0.1:${port}"

PASS=0; FAIL=0
ok()   { log_info "$1"; PASS=$((PASS + 1)); }
bad()  { log_error "$1"; FAIL=$((FAIL + 1)); }

REQUEST_CURL_STATUS=""
REQUEST_HTTP_STATUS=""
REQUEST_BODY_FILE=""
REQUEST_EXPECTATION=""

request_record() {
    local identity="$1" display_command="$2" payload="$3" expectation="$4"
    local body_file stderr_file http_status curl_status
    shift 4
    [ "$1" = -- ] || return 2
    shift

    body_file="$(mktemp "${diagnostic_dir}/body.XXXXXX")"
    stderr_file="$(mktemp "${diagnostic_dir}/stderr.XXXXXX")"
    http_status="$("$@" -o "$body_file" -w '%{http_code}' 2>"$stderr_file")"
    curl_status=$?

    log_block "Identity" "$identity"
    log_command "$display_command"
    if [[ "$display_command" != *"--data-raw"* ]]; then
        log_block "Request payload" "$payload"
    fi
    log_block "HTTP response" "$(<"$body_file")"
    log_block "HTTP stderr" "$(<"$stderr_file")"
    log_block "HTTP status" "$http_status"

    REQUEST_CURL_STATUS="$curl_status"
    REQUEST_HTTP_STATUS="$http_status"
    REQUEST_BODY_FILE="$body_file"
    REQUEST_EXPECTATION="$expectation"
}

request_failed() {
    local expected_status="$1" identity="$2"
    if [ "$REQUEST_CURL_STATUS" -ne 0 ]; then
        bad "Verdict: FAIL stage=curl failure identity=${identity} exit=${REQUEST_CURL_STATUS}"
        return 0
    fi
    if [ "$REQUEST_HTTP_STATUS" != "$expected_status" ]; then
        bad "Verdict: FAIL stage=HTTP response identity=${identity} status=${REQUEST_HTTP_STATUS} expected=${expected_status}"
        return 0
    fi
    return 1
}

log_step "Health"
request_record "server health" \
    "curl --silent --show-error --max-time 10 ${base}/health" \
    "" "HTTP status: 200" -- \
    curl --silent --show-error --max-time 10 "${base}/health"
log_block "Expectation" "$REQUEST_EXPECTATION"
if request_failed 200 "server health"; then
    :
else
    ok "Verdict: PASS identity=server health /health responds"
fi
if [ "$FAIL" -ne 0 ]; then
    log_step "Results: ${PASS} passed, ${FAIL} failed"
    exit 1
fi

# The router enforces the API key on inference endpoints only; /health and /v1/models
# answer unauthenticated, so probing those would never detect a missing key.
log_step "Authentication"
auth_probe_body="$(jq -n '{model: "x", messages: [{role: "user", content: "x"}], max_tokens: 1}')"
request_record "server invalid-key probe" \
    "curl --silent --show-error --max-time 10 -H 'Authorization: Bearer <redacted>' -H 'Content-Type: application/json' --data-raw '${auth_probe_body}' ${base}/v1/chat/completions" \
    "$auth_probe_body" "HTTP status: 401" -- \
    curl --silent --show-error --max-time 10 \
    -K "$bad_conf" -H "Content-Type: application/json" \
    --data-raw "$auth_probe_body" "${base}/v1/chat/completions"
log_block "Expectation" "$REQUEST_EXPECTATION"
if request_failed 401 "server invalid-key probe"; then
    :
else
    ok "Verdict: PASS identity=server invalid-key probe rejects invalid API key (401)"
fi

log_step "Model listing"
expected="$(yq -r '[.models[] | select(.enabled) | .alias] | sort | join(",")' "$CONFIG_PATH")"
request_record "server model listing" \
    "curl --silent --show-error --max-time 10 -H 'Authorization: Bearer <redacted>' ${base}/v1/models" \
    "" "model IDs: ${expected}" -- \
    curl --silent --show-error --max-time 10 -K "$auth_conf" "${base}/v1/models"
log_block "Expectation" "$REQUEST_EXPECTATION"
if request_failed 200 "server model listing"; then
    :
else
    model_parse_stderr="$(mktemp "${diagnostic_dir}/parse.XXXXXX")"
    listed="$(jq -r '[.data[].id] | sort | join(",")' \
        < "$REQUEST_BODY_FILE" 2>"$model_parse_stderr")"
    model_parse_status=$?
    log_block "Response parsing stderr" "$(<"$model_parse_stderr")"
    if [ "$model_parse_status" -ne 0 ]; then
        bad "Verdict: FAIL stage=response parsing identity=server model listing"
    elif [ "$listed" = "$expected" ]; then
        ok "Verdict: PASS identity=server model listing /v1/models lists exactly: ${expected}"
    else
        bad "Verdict: FAIL stage=model-list mismatch listed='${listed}' expected='${expected}'"
    fi
fi

log_step "Completions"
while read -r alias; do
    [ -n "$alias" ] || continue
    body="$(jq -n --arg m "$alias" \
        '{model: $m,
          messages: [{role: "user", content: "Reply with exactly: ready"}],
          max_tokens: 256, stream: false}')"

    identity="server completion model=${alias}"
    expectation="normalized assistant content: ready"
    request_record "$identity" \
        "curl --silent --show-error --max-time 120 -H 'Authorization: Bearer <redacted>' -H 'Content-Type: application/json' --data-raw '${body}' ${base}/v1/chat/completions" \
        "$body" "$expectation" -- \
        curl --silent --show-error --max-time 120 \
        -K "$auth_conf" \
        -H "Content-Type: application/json" \
        --data-raw "$body" "${base}/v1/chat/completions"

    content=""
    reasoning=""
    normalized=""
    failure_stage=""
    failure_detail=""
    completion_parse_stderr="$(mktemp "${diagnostic_dir}/parse.XXXXXX")"
    if [ "$REQUEST_CURL_STATUS" -ne 0 ]; then
        failure_stage="curl failure"
        failure_detail="exit=${REQUEST_CURL_STATUS}"
    elif [[ ! "$REQUEST_HTTP_STATUS" =~ ^2[0-9][0-9]$ ]]; then
        failure_stage="HTTP response"
        failure_detail="status=${REQUEST_HTTP_STATUS}"
    elif ! jq . "$REQUEST_BODY_FILE" >/dev/null 2>"$completion_parse_stderr"; then
        failure_stage="invalid JSON"
    else
        content="$(jq -r '.choices?[0]?.message?.content? // empty' \
            < "$REQUEST_BODY_FILE" 2>>"$completion_parse_stderr")"
        reasoning="$(jq -r '.choices?[0]?.message?.reasoning_content? // empty' \
            < "$REQUEST_BODY_FILE" 2>>"$completion_parse_stderr")"
        normalized="$(printf '%s' "$content" | tr '[:upper:]' '[:lower:]' | \
            sed -E 's/^[[:space:][:punct:]]+//; s/[[:space:][:punct:]]+$//')"
        if [ -z "$content" ]; then
            failure_stage="missing assistant content"
        elif [ "$normalized" != ready ]; then
            failure_stage="normalized-value mismatch"
            failure_detail="${alias}: expected ready, got $(printf '%.80s' "$content")"
        fi
    fi

    log_block "Assistant content" "$content"
    log_block "Reasoning content" "$reasoning"
    log_block "Normalized content" "$normalized"
    log_block "Response parsing stderr" "$(<"$completion_parse_stderr")"
    log_block "Expectation" "$REQUEST_EXPECTATION"
    if [ -n "$failure_stage" ]; then
        bad "Verdict: FAIL stage=${failure_stage} identity=${identity} ${failure_detail}"
        continue
    fi

    ok "Verdict: PASS identity=${identity} ${alias}: returned ready"
done < <(yq -r '.models[] | select(.enabled) | .alias' "$CONFIG_PATH")

echo
log_step "Results: ${PASS} passed, ${FAIL} failed"
[ "$FAIL" -eq 0 ]
