#!/usr/bin/env bash
# check-server.sh — assert the running server honours its API contract.
set -uo pipefail
# shellcheck disable=SC1091 # Resolved from this script at runtime.
# shellcheck source=../tools/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/../tools/lib.sh"
set +e

require_cmd base64 curl jq yq

load_server_config
# shellcheck disable=SC2153 # PORT/API_KEY are set by load_server_config() in ../tools/lib.sh.
port="$PORT"
# shellcheck disable=SC2153
api_key="$API_KEY"

auth_conf="$(mktemp)"
chmod 600 "$auth_conf"
printf 'header = "Authorization: Bearer %s"\n' "$api_key" > "$auth_conf"
bad_conf="$(mktemp)"
chmod 600 "$bad_conf"
printf 'header = "Authorization: Bearer definitely-not-the-key"\n' > "$bad_conf"
omniroute_cookie_jar="$(mktemp)"
chmod 600 "$omniroute_cookie_jar"
omniroute_port="$(yq -r '.omniroute.port' "$CONFIG_PATH")"
omniroute_password="$(yq -r '.omniroute.initial_password' "$CONFIG_PATH")"
llm_server_enabled="$(yq -r '.llm_server.enabled' "$CONFIG_PATH" 2>/dev/null)"
omniroute_base="http://127.0.0.1:${omniroute_port}"
diagnostic_dir="$(prepare_diagnostic_dir server)"

cleanup() {
    local status=$?
    finish_diagnostic_dir "$diagnostic_dir"
    rm -f "$auth_conf" "$bad_conf" "$omniroute_cookie_jar" \
        "${omniroute_auth_conf:-}" "${enabled_models_file:-}"
    exit "$status"
}
trap cleanup EXIT

# 127.0.0.1 rather than localhost: localhost resolves to ::1 first on this system
# while podman publishes the port on 0.0.0.0 (IPv4), so localhost never connects.
base="http://127.0.0.1:${port}"

PASS=0; FAIL=0
ok()   { log_info "$1"; PASS=$((PASS + 1)); }
bad()  { log_error "$1"; FAIL=$((FAIL + 1)); }

# Tracks each alias's direct (non-OmniRoute) completion result so the
# OmniRoute completions loop below can skip instead of re-testing a model
# that's already known to be broken -- otherwise a single broken model
# produces two FAIL rows (direct and via OmniRoute) that look like two
# separate problems, when routing was never the issue.
declare -A LLM_SERVER_COMPLETION_OK

enabled_models_file="$(mktemp "${diagnostic_dir}/enabled-models.XXXXXX")" \
    || die "could not create enabled-model diagnostic"
model_stream_status=0
(
    yq -o=json -I=0 '[.models[] | select(.enabled)]' "$CONFIG_PATH" |
        jq -r '.[] | @base64'
) >"$enabled_models_file" || model_stream_status=$?

models_ready=1
if [ "$model_stream_status" -ne 0 ]; then
    bad "Verdict: FAIL stage=model enumeration reason=could not enumerate enabled models (exit ${model_stream_status})"
    models_ready=0
fi
checked_models=0

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

    open_diagnostic_capture "$diagnostic_dir"
    log_block "Identity" "$identity"
    log_command "$display_command"
    if [[ "$display_command" != *"--data-raw"* ]]; then
        log_block "Request payload" "$payload"
    fi
    log_block "HTTP response" "$(<"$body_file")"
    log_nonempty_block "HTTP stderr" "$(<"$stderr_file")"
    log_block "HTTP status" "$http_status"

    REQUEST_CURL_STATUS="$curl_status"
    REQUEST_HTTP_STATUS="$http_status"
    REQUEST_BODY_FILE="$body_file"
    REQUEST_EXPECTATION="$expectation"
}

request_failed() {
    local expected_status="$1" identity="$2"
    if [ "$REQUEST_CURL_STATUS" -ne 0 ]; then
        close_diagnostic_capture 1
        bad "Verdict: FAIL stage=curl failure identity=${identity} exit=${REQUEST_CURL_STATUS}"
        return 0
    fi
    if [ "$REQUEST_HTTP_STATUS" != "$expected_status" ]; then
        close_diagnostic_capture 1
        bad "Verdict: FAIL stage=HTTP response identity=${identity} status=${REQUEST_HTTP_STATUS} expected=${expected_status}"
        return 0
    fi
    return 1
}

if [ "$llm_server_enabled" != "false" ]; then

log_step "Health"
request_record "server health" \
    "curl --silent --show-error --max-time 10 ${base}/health" \
    "" "HTTP status: 200" -- \
    curl --silent --show-error --max-time 10 "${base}/health"
log_block "Expectation" "$REQUEST_EXPECTATION"
if request_failed 200 "server health"; then
    :
else
    close_diagnostic_capture 0
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
    "curl --silent --show-error --max-time 10 -H 'Authorization: Bearer definitely-not-the-key' -H 'Content-Type: application/json' --data-raw '${auth_probe_body}' ${base}/v1/chat/completions" \
    "$auth_probe_body" "HTTP status: 401" -- \
    curl --silent --show-error --max-time 10 \
    -K "$bad_conf" -H "Content-Type: application/json" \
    --data-raw "$auth_probe_body" "${base}/v1/chat/completions"
log_block "Expectation" "$REQUEST_EXPECTATION"
if request_failed 401 "server invalid-key probe"; then
    :
else
    close_diagnostic_capture 0
    ok "Verdict: PASS identity=server invalid-key probe rejects invalid API key (401)"
fi

log_step "Model listing"
expected="$(yq -r '[.models[] | select(.enabled) | .alias] | sort | join(",")' "$CONFIG_PATH")"
request_record "server model listing" \
    "curl --silent --show-error --max-time 10 -H 'Authorization: Bearer ${api_key}' ${base}/v1/models" \
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
    log_nonempty_block "Response parsing stderr" "$(<"$model_parse_stderr")"
    if [ "$model_parse_status" -ne 0 ]; then
        close_diagnostic_capture 1
        bad "Verdict: FAIL stage=response parsing identity=server model listing"
    elif [ "$listed" = "$expected" ]; then
        close_diagnostic_capture 0
        ok "Verdict: PASS identity=server model listing /v1/models lists exactly: ${expected}"
    else
        close_diagnostic_capture 1
        bad "Verdict: FAIL stage=model-list mismatch listed='${listed}' expected='${expected}'"
    fi
fi

log_step "Completions"
if [ "$models_ready" -eq 1 ]; then
while IFS= read -r model_b64; do
    [ -n "$model_b64" ] || continue
    model_json="$(printf '%s' "$model_b64" | base64 --decode)"
    alias="$(jq -r '.alias' <<<"$model_json")"
    max_tokens="$(jq -r '.client_max_output_tokens' <<<"$model_json")"
    timeout_seconds="$(jq -r '.check_timeout_seconds // 131' <<<"$model_json")"
    checked_models=$((checked_models + 1))
    body="$(jq -n --arg m "$alias" --argjson mt "$max_tokens" \
        '{model: $m, messages: [{role: "user", content: "Reply with exactly: ready"}], max_tokens: $mt, stream: false}')"

    identity="server completion model=${alias}"
    expectation="normalized assistant content: ready"
    request_record "$identity" \
        "curl --silent --show-error --max-time ${timeout_seconds} -H 'Authorization: Bearer ${api_key}' -H 'Content-Type: application/json' --data-raw '${body}' ${base}/v1/chat/completions" \
        "$body" "$expectation" -- \
        curl --silent --show-error --max-time "$timeout_seconds" \
        -K "$auth_conf" \
        -H "Content-Type: application/json" \
        --data-raw "$body" "${base}/v1/chat/completions"

    content=""
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
    log_block "Normalized content" "$normalized"
    log_nonempty_block "Response parsing stderr" "$(<"$completion_parse_stderr")"
    log_block "Expectation" "$REQUEST_EXPECTATION"
    if [ -n "$failure_stage" ]; then
        close_diagnostic_capture 1
        bad "Verdict: FAIL stage=${failure_stage} identity=${identity} ${failure_detail}"
        LLM_SERVER_COMPLETION_OK["$alias"]=0
        continue
    fi

    close_diagnostic_capture 0
    ok "Verdict: PASS identity=${identity} ${alias}: returned ready"
    LLM_SERVER_COMPLETION_OK["$alias"]=1
done < "$enabled_models_file"
fi

if [ "$models_ready" -eq 1 ] && [ "$checked_models" -eq 0 ]; then
    bad "Verdict: FAIL stage=model checks reason=no enabled models were checked"
fi

else
    log_step "Health"
    log_warn "Verdict: SKIP identity=server health reason=llm-server is disabled"
    log_step "Authentication"
    log_warn "Verdict: SKIP identity=server invalid-key probe reason=llm-server is disabled"
    log_step "Model listing"
    log_warn "Verdict: SKIP identity=server model listing reason=llm-server is disabled"
    log_step "Completions"
    log_warn "Verdict: SKIP identity=server completions reason=llm-server is disabled"
    models_ready=0
fi

log_step "OmniRoute login"
omniroute_login_body="$(jq -n --arg p "$omniroute_password" '{password: $p}')"
request_record "omniroute dashboard login" \
    "curl --silent --show-error --max-time 10 -H 'Content-Type: application/json' --data-raw '${omniroute_login_body}' ${omniroute_base}/api/auth/login" \
    "" "HTTP status: 200" -- \
    curl --silent --show-error --max-time 10 -c "$omniroute_cookie_jar" \
    -H "Content-Type: application/json" \
    --data-raw "$omniroute_login_body" "${omniroute_base}/api/auth/login"
log_block "Expectation" "$REQUEST_EXPECTATION"
if request_failed 200 "omniroute dashboard login"; then
    log_step "OmniRoute providers"
    bad "Verdict: FAIL stage=skipped reason=omniroute dashboard login failed"
else
    close_diagnostic_capture 0
    ok "Verdict: PASS identity=omniroute dashboard login session established"

    log_step "OmniRoute providers"
    request_record "omniroute provider listing" \
        "curl --silent --show-error --max-time 10 -b <omniroute session cookie> ${omniroute_base}/api/providers" \
        "" "HTTP status: 200" -- \
        curl --silent --show-error --max-time 10 -b "$omniroute_cookie_jar" "${omniroute_base}/api/providers"
    log_block "Expectation" "$REQUEST_EXPECTATION"
    if request_failed 200 "omniroute provider listing"; then
        :
    else
        connection_parse_stderr="$(mktemp "${diagnostic_dir}/parse.XXXXXX")"
        is_active="$(jq -r '
            (if type == "array" then . else (.connections // .providers // .data // []) end)
            | map(select(.name == "llm-env-local"))[0].isActive // false
          ' < "$REQUEST_BODY_FILE" 2>"$connection_parse_stderr")"
        log_nonempty_block "Response parsing stderr" "$(<"$connection_parse_stderr")"
        if [ "$is_active" = "true" ]; then
            close_diagnostic_capture 0
            ok "Verdict: PASS identity=omniroute provider listing llm-env-local connection is present and active"
        else
            close_diagnostic_capture 1
            bad "Verdict: FAIL stage=connection lookup reason=llm-env-local connection missing or inactive"
        fi
    fi
fi

log_step "OmniRoute API key"
# The compose file sets REQUIRE_API_KEY=true on the omniroute service (it is
# published on 0.0.0.0 for LAN clients), so /v1/* now validates the bearer
# against OmniRoute's issued-key table. The dashboard password is NOT such a
# key: it only ever "worked" because REQUIRE_API_KEY=false made OmniRoute
# treat any unrecognized bearer as anonymous. Mint/reuse the cached scoped key
# through `llmenv omniroute issue-key` -- the same helper the local agent setup
# uses, so repeated runs reuse one key instead of littering the dashboard.
omniroute_api_key=""
omniroute_key_stderr="$(mktemp "${diagnostic_dir}/omniroute-key.XXXXXX")"
if omniroute_key_json="$(llmenv --config "$CONFIG_PATH" omniroute issue-key \
        2>"$omniroute_key_stderr")"; then
    omniroute_api_key="$(printf '%s' "$omniroute_key_json" | jq -r '.api_key // empty' 2>/dev/null)"
fi
log_nonempty_block "Key issuance stderr" "$(<"$omniroute_key_stderr")"
if [ -z "$omniroute_api_key" ]; then
    bad "Verdict: FAIL stage=api key issuance reason=could not mint an OmniRoute API key; /v1/* requires one"
else
    ok "Verdict: PASS identity=omniroute api key scoped client key issued"
    # Passed via a mode-0600 curl config file, never -H on the command line:
    # a bearer token in argv is readable by every local user through
    # /proc/<pid>/cmdline for as long as curl runs.
    omniroute_auth_conf="$(mktemp)"
    chmod 600 "$omniroute_auth_conf"
    printf 'header = "Authorization: Bearer %s"\n' "$omniroute_api_key" >"$omniroute_auth_conf"
fi

log_step "OmniRoute completions"
if [ -z "$omniroute_api_key" ]; then
    log_warn "Verdict: SKIP identity=omniroute completions reason=no OmniRoute API key available"
elif [ "$models_ready" -eq 1 ] && [ "$checked_models" -gt 0 ]; then
while IFS= read -r model_b64; do
    [ -n "$model_b64" ] || continue
    model_json="$(printf '%s' "$model_b64" | base64 --decode)"
    alias="$(jq -r '.alias' <<<"$model_json")"
    max_tokens="$(jq -r '.client_max_output_tokens' <<<"$model_json")"
    timeout_seconds="$(jq -r '.check_timeout_seconds // 131' <<<"$model_json")"
    if [ "${LLM_SERVER_COMPLETION_OK[$alias]:-1}" -eq 0 ]; then
        log_warn "Verdict: SKIP identity=omniroute completion model=${alias} reason=server completion model=${alias} already failed; fix that first, OmniRoute proxies to the same model"
        continue
    fi
    # Routing keys on the provider slug ("llama-cpp"), not the connection's
    # own name -- confirmed live via GET /v1/models, which lists synced
    # models as "llama-cpp/<alias>" regardless of the connection's name.
    body="$(jq -n --arg m "llama-cpp/${alias}" --argjson mt "$max_tokens" \
        '{model: $m, messages: [{role: "user", content: "Reply with exactly: ready"}], max_tokens: $mt, stream: false}')"

    identity="omniroute completion model=${alias}"
    expectation="normalized assistant content: ready"
    request_record "$identity" \
        "curl --silent --show-error --max-time ${timeout_seconds} -K <omniroute api key config> -H 'Content-Type: application/json' --data-raw '${body}' ${omniroute_base}/v1/chat/completions" \
        "$body" "$expectation" -- \
        curl --silent --show-error --max-time "$timeout_seconds" \
        -K "$omniroute_auth_conf" \
        -H "Content-Type: application/json" \
        --data-raw "$body" "${omniroute_base}/v1/chat/completions"

    content=""
    normalized=""
    failure_stage=""
    failure_detail=""
    omniroute_parse_stderr="$(mktemp "${diagnostic_dir}/parse.XXXXXX")"
    if [ "$REQUEST_CURL_STATUS" -ne 0 ]; then
        failure_stage="curl failure"
        failure_detail="exit=${REQUEST_CURL_STATUS}"
    elif [[ ! "$REQUEST_HTTP_STATUS" =~ ^2[0-9][0-9]$ ]]; then
        failure_stage="HTTP response"
        failure_detail="status=${REQUEST_HTTP_STATUS}"
    elif ! jq . "$REQUEST_BODY_FILE" >/dev/null 2>"$omniroute_parse_stderr"; then
        failure_stage="invalid JSON"
    else
        content="$(jq -r '.choices?[0]?.message?.content? // empty' \
            < "$REQUEST_BODY_FILE" 2>>"$omniroute_parse_stderr")"
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
    log_block "Normalized content" "$normalized"
    log_nonempty_block "Response parsing stderr" "$(<"$omniroute_parse_stderr")"
    log_block "Expectation" "$REQUEST_EXPECTATION"
    if [ -n "$failure_stage" ]; then
        close_diagnostic_capture 1
        bad "Verdict: FAIL stage=${failure_stage} identity=${identity} ${failure_detail}"
        continue
    fi

    close_diagnostic_capture 0
    ok "Verdict: PASS identity=${identity} ${alias}: returned ready via OmniRoute"
done < "$enabled_models_file"
fi

echo
log_step "Results: ${PASS} passed, ${FAIL} failed"
[ "$FAIL" -eq 0 ]
