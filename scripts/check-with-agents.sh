#!/usr/bin/env bash
# check-with-agents.sh — ask installed coding agents to independently verify public data.
set +x
set -uo pipefail
# shellcheck disable=SC1091 # Resolved from this script at runtime.
# shellcheck source=../tools/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/../tools/lib.sh"
set +e

if [ "$#" -ne 0 ]; then
    printf '%s\n' 'fail check-with-agents accepts no arguments' >&2
    exit 1
fi

require_cmd curl jq yq date uv systemd-run systemctl wc

workspace="$(mktemp -d)" || die "could not create private workspace"
chmod 700 "$workspace" || die "could not secure private workspace"
diagnostic_dir="$(prepare_diagnostic_dir agents)"

cleanup() {
    local status=$?
    finish_diagnostic_dir "$diagnostic_dir"
    rm -rf "$workspace"
    exit "$status"
}
trap cleanup EXIT

auth_conf="$(mktemp "$workspace/auth.XXXXXX")" || die "could not create curl authentication config"
chmod 600 "$auth_conf" || die "could not secure curl authentication config"

port="$(yq -r '.server.port' "$CONFIG_PATH")"
api_key="$(yq -r '.server.api_key' "$CONFIG_PATH")"
[ -n "$port" ] && [ "$port" != null ] || die "server port is not configured"
[ -n "$api_key" ] && [ "$api_key" != null ] || die "server API key is not configured"
printf 'header = "Authorization: Bearer %s"\n' "$api_key" > "$auth_conf"

# 127.0.0.1 rather than localhost: localhost resolves to ::1 first on this system
# while podman publishes the port on 0.0.0.0 (IPv4), so localhost never connects.
base="http://127.0.0.1:${port}"
weather_url='https://api.open-meteo.com/v1/forecast?latitude=-33.4489&longitude=-70.6693&current=temperature_2m,weather_code&timezone=America%2FSantiago'
fx_url='https://open.er-api.com/v6/latest/USD'
agent_diagnostic_excerpt_bytes=262144
source_response_limit_bytes=1048576
final_response_limit_bytes=1048576

fetch_models() {
    local config_file="$1"
    local server_base="$2"

    curl -fsS --max-time 20 --max-filesize "$source_response_limit_bytes" \
        -K "$config_file" "${server_base}/v1/models" |
        jq -er '.data[]?.id'
}

snapshot_for() {
    local check_name="$1" stdout_file="$2" stderr_file="$3" parser_stderr_file="$4"
    local url filter snapshot status source_date

    case "$check_name" in
        weather)
            url="$weather_url"
            # shellcheck disable=SC2016 # jq variables must remain literal until jq evaluates them.
            filter='. as $source
                | select(($source.current.time | strings | test("^[0-9]{4}-[0-9]{2}-[0-9]{2}T")))
                | ($source.current.time[0:10]) as $source_date
                | select(($source_date | strptime("%Y-%m-%d") | strftime("%Y-%m-%d")) == $source_date)
                | select($source.current.temperature_2m | numbers)
                | select($source.current.weather_code | numbers)
                | {source_url: $url,
                   source_timestamp: $source.current.time,
                   source_date: ($source.current.time | .[0:10]),
                   temperature_2m: $source.current.temperature_2m,
                   weather_code: $source.current.weather_code}'
            ;;
        fx)
            url="$fx_url"
            # shellcheck disable=SC2016 # jq variables must remain literal until jq evaluates them.
            filter='. as $source | select(($source.time_last_update_utc | strings | length) > 0) | select($source.rates.CLP | numbers) | {source_url: $url, source_timestamp: $source.time_last_update_utc, usd_to_clp: $source.rates.CLP}'
            ;;
        *) return 64 ;;
    esac

    log_block "Source" "fetch check=${check_name}"
    log_command "curl --fail --silent --show-error --max-time 20 --max-filesize ${source_response_limit_bytes} ${url}"
    log_block "Input" "(none)"
    if curl -fsS --max-time 20 --max-filesize "$source_response_limit_bytes" \
        "$url" >"$stdout_file" 2>"$stderr_file"; then
        status=0
    else
        status=$?
    fi
    if [ "$status" -ne 0 ]; then
        log_file_excerpt "Source stdout" "$stdout_file" "$agent_diagnostic_excerpt_bytes"
        if [ -s "$stderr_file" ]; then
            log_file_excerpt "Source stderr" "$stderr_file" "$agent_diagnostic_excerpt_bytes"
        fi
        log_block "Exit status" "$status"
        log_error "Verdict: FAIL stage=source fetch reason=${check_name} source exited ${status}"
        return 1
    fi
    if ! snapshot="$(jq -ce --arg url "$url" "$filter" "$stdout_file" 2>"$parser_stderr_file")"; then
        log_file_excerpt "Source stdout" "$stdout_file" "$agent_diagnostic_excerpt_bytes"
        if [ -s "$stderr_file" ]; then
            log_file_excerpt "Source stderr" "$stderr_file" "$agent_diagnostic_excerpt_bytes"
        fi
        log_block "Exit status" "$status"
        if [ -s "$parser_stderr_file" ]; then
            log_file_excerpt "Source parser stderr" "$parser_stderr_file" "$agent_diagnostic_excerpt_bytes"
        fi
        log_error "Verdict: FAIL stage=source fetch reason=${check_name} source body is invalid"
        return 1
    fi
    if [ "$check_name" = fx ]; then
        if ! source_date="$(date -u --date "$(jq -r '.source_timestamp' <<<"$snapshot")" +%F \
            2>>"$parser_stderr_file")" \
            || ! snapshot="$(jq -ce --arg source_date "$source_date" '. + {source_date: $source_date}' \
                <<<"$snapshot" 2>>"$parser_stderr_file")"; then
            log_file_excerpt "Source stdout" "$stdout_file" "$agent_diagnostic_excerpt_bytes"
            if [ -s "$stderr_file" ]; then
                log_file_excerpt "Source stderr" "$stderr_file" "$agent_diagnostic_excerpt_bytes"
            fi
            log_block "Exit status" "$status"
            if [ -s "$parser_stderr_file" ]; then
                log_file_excerpt "Source parser stderr" "$parser_stderr_file" "$agent_diagnostic_excerpt_bytes"
            fi
            log_error "Verdict: FAIL stage=source fetch reason=${check_name} source body is invalid"
            return 1
        fi
    fi
    if [ -s "$stderr_file" ]; then
        log_file_excerpt "Source stderr" "$stderr_file" "$agent_diagnostic_excerpt_bytes"
    fi
    log_block "Exit status" "$status"
    if [ -s "$parser_stderr_file" ]; then
        log_file_excerpt "Source parser stderr" "$parser_stderr_file" "$agent_diagnostic_excerpt_bytes"
    fi
    log_block "Parsed result" "$snapshot"
    log_block "Expectation" "a current typed ${check_name} source object"
    log_info "Verdict: PASS"
    SNAPSHOT_RESULT="$snapshot"
}

log_agent_configuration() {
    local client="$1" alias="$2" client_base="$3" credential

    case "$client" in
        pi) credential='Credential: <private>/models.json configures the Pi apiKey' ;;
        opencode) credential='Credential: OPENCODE_API_KEY=<redacted> from the environment' ;;
    esac
    log_block "Configuration" "Provider: llm-env
Base URL: ${client_base}
Model: ${alias}
Tools: bash
${credential}"
}

log_validation_facts() {
    local check_name="$1" snapshot="$2"

    case "$check_name" in
        weather) jq -r '"source_url=\(.source_url)\nsource_date=\(.source_date)\ntemperature_2m=\(.temperature_2m)\nweather_code=\(.weather_code)"' <<<"$snapshot" ;;
        fx) jq -r '"source_url=\(.source_url)\nsource_date=\(.source_date)\nusd_to_clp=\(.usd_to_clp)"' <<<"$snapshot" ;;
    esac
}

parse_bounded_result() {
    local result_file="$1"

    # Validate emitted integer lexemes before jq converts them to binary floats.
    if ! jq -Rse '
        def exactly_one($pattern):
            [scan($pattern)] | length == 1;

        (contains("\u0000") | not)
        and exactly_one("\"schema\"[[:space:]]*:[[:space:]]*1[[:space:]]*[,}]")
        and exactly_one("\"exit_status\"[[:space:]]*:[[:space:]]*(?:null|0|-?[1-9][0-9]*)[[:space:]]*[,}]")
        and exactly_one("\"transcript_bytes\"[[:space:]]*:[[:space:]]*(?:0|[1-9][0-9]*)[[:space:]]*[,}]")
        and exactly_one("\"stderr_bytes\"[[:space:]]*:[[:space:]]*(?:0|[1-9][0-9]*)[[:space:]]*[,}]")
    ' "$result_file" >/dev/null; then
        return 1
    fi
    if ! jq --stream -se '
        [.[] | select(length == 2)]
        | length == 6 and all(.[]; (.[0] | length) == 1)
    ' "$result_file" >/dev/null; then
        return 1
    fi
    if ! jq -se '
        def integral_number:
            type == "number" and isfinite and (. == floor);

        select(length == 1)
        | .[0]
        | select(type == "object")
        | select(keys == [
            "cleanup_proved",
            "exit_status",
            "outcome",
            "schema",
            "stderr_bytes",
            "transcript_bytes"
        ])
        | select(.schema == 1)
        | select(
            .outcome == "completed"
            or .outcome == "timeout"
            or .outcome == "transcript-limit"
            or .outcome == "stderr-limit"
            or .outcome == "boundary-failure"
        )
        | select(.exit_status == null or (.exit_status | integral_number))
        | select((.transcript_bytes | integral_number) and .transcript_bytes >= 0)
        | select((.stderr_bytes | integral_number) and .stderr_bytes >= 0)
        | select(.cleanup_proved | type == "boolean")
    ' "$result_file" >/dev/null; then
        return 1
    fi
    jq -Rrse '
        def exactly_one_value($pattern):
            [scan($pattern)]
            | select(length == 1 and (.[0] | length) == 1)
            | .[0][0];

        exactly_one_value("\"outcome\"[[:space:]]*:[[:space:]]*\"(completed|timeout|transcript-limit|stderr-limit|boundary-failure)\"[[:space:]]*[,}]"),
        exactly_one_value("\"exit_status\"[[:space:]]*:[[:space:]]*(null|0|-?[1-9][0-9]*)[[:space:]]*[,}]"),
        exactly_one_value("\"cleanup_proved\"[[:space:]]*:[[:space:]]*(true|false)[[:space:]]*[,}]")
    ' "$result_file"
}

mark_agent_boundary_failure() {
    AGENT_FAILURE_STAGE="resource boundary"
    AGENT_FAILURE_REASON="scope setup or cleanup could not be proved"
    AGENT_RESULT_REASON="boundary-failure"
    AGENT_ABORT_MATRIX=1
}

classify_bounded_result() {
    local runner_status="$1" result_file="$2" parser_error_file="$3"
    local parsed_result outcome exit_status cleanup_proved
    local -a result_fields

    if [ "$runner_status" -eq 0 ] \
        && parsed_result="$(parse_bounded_result "$result_file" 2>>"$parser_error_file")"; then
        mapfile -t result_fields <<< "$parsed_result"
        outcome="${result_fields[0]}"
        exit_status="${result_fields[1]}"
        cleanup_proved="${result_fields[2]}"

        if [ "$outcome" != boundary-failure ] && [ "$cleanup_proved" = true ]; then
            if [ "$exit_status" = null ]; then
                AGENT_EXIT_STATUS="NOT REPORTED"
            else
                AGENT_EXIT_STATUS="$exit_status"
            fi
            case "$outcome" in
                completed)
                    if [ "$exit_status" = null ]; then
                        :
                    elif [ "$exit_status" = 0 ]; then
                        return 0
                    else
                        return 1
                    fi
                    ;;
                timeout)
                    AGENT_FAILURE_STAGE="agent timeout"
                    AGENT_FAILURE_REASON="bounded client runtime expired"
                    AGENT_RESULT_REASON="timeout"
                    return 1
                    ;;
                transcript-limit)
                    AGENT_FAILURE_STAGE="transcript limit"
                    AGENT_FAILURE_REASON="client transcript exceeded 33554432 bytes"
                    AGENT_RESULT_REASON="transcript-limit"
                    return 1
                    ;;
                stderr-limit)
                    AGENT_FAILURE_STAGE="stderr limit"
                    AGENT_FAILURE_REASON="client stderr exceeded 33554432 bytes"
                    AGENT_RESULT_REASON="stderr-limit"
                    return 1
                    ;;
            esac
        fi
    fi

    mark_agent_boundary_failure
    return 1
}

run_agent() {
    local client="$1"
    local alias="$2"
    local prompt="$3"
    local transcript_file="$4"
    local stderr_file="$5"
    local final_file="$6"
    local parser_error_file="$7"
    local client_base config_dir config_file config_key_command status quoted_prompt
    local bounded_result_file
    local -a client_command

    AGENT_FAILURE_STAGE="command exit"
    AGENT_FAILURE_REASON="agent invocation failed"
    AGENT_RESULT_REASON="agent-failed"
    AGENT_ABORT_MATRIX=0
    AGENT_EXIT_STATUS="NOT RUN"
    AGENT_CLIENT_BASE="http://llm.local:${port}/v1"
    client_base="$AGENT_CLIENT_BASE"
    printf -v quoted_prompt '%q' "$prompt"
    DISPLAYED_CLIENT_COMMAND=""

    case "$client" in
        pi)
            config_dir="$workspace/pi"
            config_file="$config_dir/models.json"
            DISPLAYED_CLIENT_COMMAND="PI_CODING_AGENT_DIR=<private> pi --no-session --no-extensions --no-skills --no-prompt-templates --no-context-files --tools bash -p --mode json --model llm-env/${alias} ${quoted_prompt}"
            mkdir -p "$config_dir" || return 1
            chmod 700 "$config_dir" || return 1
            printf -v config_key_command "!yq -r '.server.api_key' %q" "$CONFIG_PATH"
            jq -n \
                --arg base_url "$client_base" \
                --arg api_key_command "$config_key_command" \
                --arg alias "$alias" \
                '{providers: {"llm-env": {
                    baseUrl: $base_url,
                    api: "openai-completions",
                    apiKey: $api_key_command,
                    compat: {supportsDeveloperRole: false, supportsReasoningEffort: false},
                    models: [{id: $alias}]
                }}}' > "$config_file" || return 1
            chmod 600 "$config_file" || return 1
            client_command=(
                pi
                --no-session
                --no-extensions
                --no-skills
                --no-prompt-templates
                --no-context-files
                --tools bash
                -p
                --mode json
                --model "llm-env/${alias}"
                "$prompt"
            )
            if ! bounded_result_file="$(
                mktemp "$workspace/bounded-result.XXXXXX" 2>>"$parser_error_file"
            )" || ! chmod 600 "$bounded_result_file" 2>>"$parser_error_file"; then
                mark_agent_boundary_failure
                return 1
            fi
            if (
                cd "$workspace" || exit 1
                export PI_CODING_AGENT_DIR="$config_dir"
                uv run "${REPO_DIR}/llmenv.py" run-agent-bounded \
                    --transcript "$transcript_file" \
                    --stderr "$stderr_file" \
                    -- "${client_command[@]}" </dev/null >"$bounded_result_file"
            ) 2>>"$parser_error_file"; then
                status=0
            else
                status=$?
            fi
            if ! classify_bounded_result "$status" "$bounded_result_file" "$parser_error_file"; then
                return 1
            fi
            if ! jq -rce '
                [select(.type == "message_end" and .message.role == "assistant")
                 | [.message.content[]? | select(.type == "text") | .text] | join("")]
                | last // empty
            ' "$transcript_file" >"$final_file" 2>>"$parser_error_file"; then
                AGENT_FAILURE_STAGE="response parsing"
                return 1
            fi
            ;;
        opencode)
            config_dir="$workspace/opencode"
            config_file="$config_dir/opencode.jsonc"
            DISPLAYED_CLIENT_COMMAND="HOME=<private> XDG_CONFIG_HOME=<private> XDG_DATA_HOME=<private> XDG_STATE_HOME=<private> OPENCODE_CONFIG=<private> OPENCODE_API_KEY=<redacted> opencode run --format json --model llm-env/${alias} ${quoted_prompt}"
            mkdir -p "$config_dir" "$workspace/opencode-home" "$workspace/opencode-config" "$workspace/opencode-data" "$workspace/opencode-state" || return 1
            chmod 700 "$config_dir" "$workspace/opencode-home" "$workspace/opencode-config" "$workspace/opencode-data" "$workspace/opencode-state" || return 1
            jq -n \
                --arg base_url "$client_base" \
                --arg alias "$alias" \
                '{"$schema": "https://opencode.ai/config.json", tools: {"*": false, bash: true}, provider: {"llm-env": {
                    npm: "@ai-sdk/openai-compatible",
                    name: "llm-env",
                    options: {baseURL: $base_url, apiKey: "{env:OPENCODE_API_KEY}"},
                    models: {($alias): {name: $alias}}
                }}}' > "$config_file" || return 1
            chmod 600 "$config_file" || return 1
            client_command=(
                opencode run --format json --model "llm-env/${alias}" "$prompt"
            )
            if ! bounded_result_file="$(
                mktemp "$workspace/bounded-result.XXXXXX" 2>>"$parser_error_file"
            )" || ! chmod 600 "$bounded_result_file" 2>>"$parser_error_file"; then
                mark_agent_boundary_failure
                return 1
            fi
            if (
                cd "$workspace" || exit 1
                export HOME="$workspace/opencode-home"
                export XDG_CONFIG_HOME="$workspace/opencode-config"
                export XDG_DATA_HOME="$workspace/opencode-data"
                export XDG_STATE_HOME="$workspace/opencode-state"
                export OPENCODE_CONFIG="$config_file"
                export OPENCODE_API_KEY="$api_key"
                uv run "${REPO_DIR}/llmenv.py" run-agent-bounded \
                    --transcript "$transcript_file" \
                    --stderr "$stderr_file" \
                    -- "${client_command[@]}" </dev/null >"$bounded_result_file"
            ) 2>>"$parser_error_file"; then
                status=0
            else
                status=$?
            fi
            if ! classify_bounded_result "$status" "$bounded_result_file" "$parser_error_file"; then
                return 1
            fi
            if ! jq -rce '
                reduce (select(.type == "text" and .part.type == "text") | .part) as $part
                ({message_id: null, text: ""};
                 if .message_id == $part.messageID then .text += $part.text
                 else {message_id: $part.messageID, text: $part.text}
                 end)
                | .text
            ' "$transcript_file" >"$final_file" 2>>"$parser_error_file"; then
                AGENT_FAILURE_STAGE="response parsing"
                return 1
            fi
            ;;
        *)
            printf '%s\n' 'unsupported client' >"$stderr_file"
            return 1
            ;;
    esac
}

parse_evidence() {
    local assistant_file="$1"

    if jq -Rse 'test("^[[:space:]]*```json")' "$assistant_file" >/dev/null; then
        jq -Rrse \
            'capture("^[[:space:]]*```json\\r?\\n(?<body>[\\s\\S]*?)\\r?\\n```[[:space:]]*$").body | select(length > 0)' \
            "$assistant_file" |
            jq -sce \
            'select(length == 1 and (.[0] | type == "object")) | .[0]'
        return
    fi
    jq -sce \
        'select(length == 1 and (.[0] | type == "object")) | .[0]' \
        "$assistant_file"
}

source_evidence_differences() {
    local check_name="$1"
    local snapshot="$2"
    local evidence="$3"
    local required_fields timestamp source_date source_timezone
    local timestamp_pattern='^[0-9]{4}-[0-9]{2}-[0-9]{2}T([01][0-9]|2[0-3]):[0-5][0-9](:[0-5][0-9](\.[0-9]+)?)?(Z|[+-]([01][0-9]|2[0-3]):?[0-5][0-9])?$'

    case "$check_name" in
        weather)
            required_fields='["source_url", "temperature_2m", "weather_code"]'
            source_timezone='America/Santiago'
            ;;
        fx)
            required_fields='["source_url", "usd_to_clp"]'
            source_timezone='UTC'
            ;;
        *)
            return 1
            ;;
    esac

    if ! timestamp="$(jq -er '.source_timestamp | strings' <<<"$evidence")" \
        || ! [[ "$timestamp" =~ $timestamp_pattern ]]; then
        printf '%s\n' 'field=source_timestamp expected=ISO-8601 received="<redacted>"'
        return
    fi
    # Calendar-day comparisons must use the timezone in which the source publishes data.
    if ! source_date="$(TZ="$source_timezone" date --date "$timestamp" +%F 2>/dev/null)"; then
        printf '%s\n' 'field=source_timestamp expected=ISO-8601 received="<redacted>"'
        return
    fi

    jq -nr --argjson expected "$snapshot" --argjson received "$evidence" \
        --arg source_date "$source_date" --argjson fields "$required_fields" '
        [
          (($expected.source_date // "<missing>") as $want_date
           | $source_date as $got_date
           | select($want_date != $got_date)
           | "field=source_timestamp expected_date=\($want_date | tojson) received_date=\"<redacted>\""),
          ($fields[] as $field
           | ($expected[$field] // "<missing>") as $want
           | ($received[$field] // "<missing>") as $got
           | select($want != $got)
           | "field=\($field) expected=\($want | tojson) received=\($got | tojson)")
        ]
        | .[]
    '
}

aliases="$(fetch_models "$auth_conf" "$base")" || die "could not fetch model aliases"
[ -n "$aliases" ] || die "the server returned no model aliases"

clients=()
for client in pi opencode; do
    if command -v "$client" >/dev/null 2>&1; then
        clients+=("$client")
    else
        printf 'SKIP client=%s model=- check=- reason=not-installed\n' "$client"
    fi
done

if [ "${#clients[@]}" -eq 0 ]; then
    printf '%s\n' 'fail no supported agent is installed' >&2
    exit 1
fi

passes=0
failures=0
for client in "${clients[@]}"; do
    while IFS= read -r alias; do
        [ -n "$alias" ] || continue
        for check_name in weather fx; do
            source_stdout="$(mktemp "${diagnostic_dir}/source-stdout.XXXXXX")" || die "could not create source stdout"
            source_stderr="$(mktemp "${diagnostic_dir}/source-stderr.XXXXXX")" || die "could not create source stderr"
            source_parser_stderr="$(mktemp "${diagnostic_dir}/source-parser-stderr.XXXXXX")" || die "could not create source parser stderr"
            case "$check_name" in
                weather)
                    snapshot_for "$check_name" "$source_stdout" "$source_stderr" "$source_parser_stderr" || {
                        log_error "Verdict: FAIL stage=source fetch reason=weather snapshot unavailable"
                        failures=$((failures + 1))
                        continue
                    }
                    snapshot="$SNAPSHOT_RESULT"
                    source_url="$weather_url"
                    fields='source_url, source_timestamp, temperature_2m, and weather_code'
                    timestamp_instruction="The source_timestamp field must copy the source response's timestamp text byte-for-byte. Do not convert or normalize its timezone, add an offset, or change its date. Return source_timestamp as ISO-8601."
                    agent_expectation='exactly one JSON object whose source URL, canonical source date, and required source values match the fetched weather source'
                    ;;
                fx)
                    snapshot_for "$check_name" "$source_stdout" "$source_stderr" "$source_parser_stderr" || {
                        log_error "Verdict: FAIL stage=source fetch reason=FX snapshot unavailable"
                        failures=$((failures + 1))
                        continue
                    }
                    snapshot="$SNAPSHOT_RESULT"
                    source_url="$fx_url"
                    fields='source_url, source_timestamp, and usd_to_clp'
                    timestamp_instruction="Return source_timestamp as ISO-8601. The source_timestamp field must convert the source response's exact time_last_update_utc timestamp to ISO-8601 while preserving its UTC instant, UTC timezone (Z or +00:00), and source calendar date. Do not convert it to local time or another timezone, and do not change its date."
                    agent_expectation='exactly one JSON object whose source URL, canonical source date, and required source values match the fetched FX source'
                    ;;
            esac
            printf -v prompt '%s' "You MUST use bash to execute this exact command verbatim as the only network request: curl -fsS --max-time 20 -- '${source_url}'. The URL argument must be copied byte-for-byte from the command. Do not substitute any source, endpoint, proxy, mirror, or query. Return fields only from that command's response. The source_url field must reproduce the literal URL byte-for-byte, including percent encoding. ${timestamp_instruction} Return exactly one JSON object containing ${fields}."

            transcript_file="$(mktemp "${diagnostic_dir}/client-transcript.XXXXXX")" || die "could not create client transcript"
            client_stderr_file="$(mktemp "${diagnostic_dir}/client-stderr.XXXXXX")" || die "could not create client stderr"
            final_file="$(mktemp "${diagnostic_dir}/assistant-final.XXXXXX")" || die "could not create assistant final text"
            parser_error_file="$(mktemp "${diagnostic_dir}/agent-parser-stderr.XXXXXX")" || die "could not create agent parser stderr"
            evidence=""
            agent_failed=0
            if run_agent "$client" "$alias" "$prompt" "$transcript_file" \
                "$client_stderr_file" "$final_file" "$parser_error_file"; then
                final_bytes="$(wc -c < "$final_file")"
                if [ "$final_bytes" -gt "$final_response_limit_bytes" ]; then
                    AGENT_FAILURE_STAGE="final response limit"
                    AGENT_FAILURE_REASON="final assistant text exceeded ${final_response_limit_bytes} bytes"
                    AGENT_RESULT_REASON="final-response-limit"
                    agent_failed=1
                elif ! evidence="$(parse_evidence "$final_file" 2>>"$parser_error_file")"; then
                    AGENT_FAILURE_STAGE="agent evidence parsing"
                    agent_failed=1
                fi
            else
                agent_failed=1
            fi

            log_block "Agent" "client=${client} model=${alias} check=${check_name}"
            log_agent_configuration "$client" "$alias" "$AGENT_CLIENT_BASE"
            log_command "$DISPLAYED_CLIENT_COMMAND"
            log_block "Input" "$prompt"
            log_block "Exit status" "$AGENT_EXIT_STATUS"
            log_block "Expectation" "$agent_expectation"

            if [ "$agent_failed" -ne 0 ]; then
                if [ -s "$transcript_file" ]; then
                    log_file_excerpt "Client JSONL transcript" "$transcript_file" "$agent_diagnostic_excerpt_bytes"
                fi
                if [ -s "$client_stderr_file" ]; then
                    log_file_excerpt "Client stderr" "$client_stderr_file" "$agent_diagnostic_excerpt_bytes"
                fi
                if [ -s "$parser_error_file" ]; then
                    log_file_excerpt "Agent parser stderr" "$parser_error_file" "$agent_diagnostic_excerpt_bytes"
                fi
                if [ -s "$final_file" ]; then
                    log_file_excerpt "Final response" "$final_file" "$agent_diagnostic_excerpt_bytes"
                fi
                log_error "Verdict: FAIL stage=${AGENT_FAILURE_STAGE} client=${client} model=${alias} check=${check_name} reason=${AGENT_FAILURE_REASON}"
                printf 'FAIL client=%s model=%s check=%s reason=%s\n' \
                    "$client" "$alias" "$check_name" "$AGENT_RESULT_REASON"
                failures=$((failures + 1))
                if [ "$AGENT_ABORT_MATRIX" -ne 0 ]; then
                    break 3
                fi
                continue
            fi

            differences="$(source_evidence_differences "$check_name" "$snapshot" "$evidence")"
            if [ -z "$differences" ]; then
                if [ -s "$client_stderr_file" ]; then
                    log_file_excerpt "Client stderr" "$client_stderr_file" "$agent_diagnostic_excerpt_bytes"
                fi
                if [ -s "$parser_error_file" ]; then
                    log_file_excerpt "Agent parser stderr" "$parser_error_file" "$agent_diagnostic_excerpt_bytes"
                fi
                log_file_excerpt "Final response" "$final_file" "$agent_diagnostic_excerpt_bytes"
                log_block "Validated" "$(log_validation_facts "$check_name" "$snapshot")"
                log_info "Verdict: PASS"
                printf 'PASS client=%s model=%s check=%s reason=agent-returned-json\n' \
                    "$client" "$alias" "$check_name"
                passes=$((passes + 1))
                continue
            fi
            if [ -s "$transcript_file" ]; then
                log_file_excerpt "Client JSONL transcript" "$transcript_file" "$agent_diagnostic_excerpt_bytes"
            fi
            if [ -s "$client_stderr_file" ]; then
                log_file_excerpt "Client stderr" "$client_stderr_file" "$agent_diagnostic_excerpt_bytes"
            fi
            if [ -s "$parser_error_file" ]; then
                log_file_excerpt "Agent parser stderr" "$parser_error_file" "$agent_diagnostic_excerpt_bytes"
            fi
            if [ -s "$final_file" ]; then
                log_file_excerpt "Final response" "$final_file" "$agent_diagnostic_excerpt_bytes"
            fi
            log_block "Validated" "$(log_validation_facts "$check_name" "$snapshot")"
            while IFS= read -r difference; do
                [ -n "$difference" ] || continue
                log_error "Verdict: FAIL stage=source-evidence mismatch client=${client} model=${alias} check=${check_name} ${difference}"
                printf 'FAIL client=%s model=%s check=%s %s\n' \
                    "$client" "$alias" "$check_name" "$difference"
            done <<< "$differences"
            failures=$((failures + 1))
        done
    done <<< "$aliases"
done

printf 'Results: %d passed, %d failed\n' "$passes" "$failures"
[ "$failures" -eq 0 ]
