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

require_cmd curl jq yq date uv systemd-run systemctl wc head cat sed grep

workspace=""
diagnostic_dir=""

cleanup() {
    local status=$? finalizer_status=0 workspace_status=0
    trap - EXIT

    if [ -n "$diagnostic_dir" ]; then
        (finish_diagnostic_dir "$diagnostic_dir") || finalizer_status=$?
    fi
    if [ -n "$workspace" ]; then
        rm -rf "$workspace" || workspace_status=$?
    fi
    if [ "$status" -eq 0 ]; then
        if [ "$finalizer_status" -ne 0 ]; then
            status="$finalizer_status"
        elif [ "$workspace_status" -ne 0 ]; then
            status="$workspace_status"
        fi
    fi
    exit "$status"
}

if ! workspace="$(mktemp -d)"; then
    die "could not create private workspace"
fi
trap cleanup EXIT
chmod 700 "$workspace" || die "could not secure private workspace"
if ! diagnostic_dir="$(prepare_diagnostic_dir agents)"; then
    die "could not prepare diagnostic directory"
fi
[ -n "$diagnostic_dir" ] || die "diagnostic directory is empty"

auth_conf="$(mktemp "$workspace/auth.XXXXXX")" || die "could not create curl authentication config"
chmod 600 "$auth_conf" || die "could not secure curl authentication config"

load_server_config
# shellcheck disable=SC2153 # PORT/API_KEY are set by load_server_config() in ../tools/lib.sh.
port="$PORT"
# shellcheck disable=SC2153
api_key="$API_KEY"
[ -n "$port" ] && [ "$port" != null ] || die "server port is not configured"
[ -n "$api_key" ] && [ "$api_key" != null ] || die "server API key is not configured"
printf 'header = "Authorization: Bearer %s"\n' "$api_key" > "$auth_conf"

# 127.0.0.1 rather than localhost: localhost resolves to ::1 first on this system
# while podman publishes the port on 0.0.0.0 (IPv4), so localhost never connects.
base="http://127.0.0.1:${port}"
weather_url='https://api.open-meteo.com/v1/forecast?latitude=-33.4489&longitude=-70.6693&current=temperature_2m,weather_code&timezone=America%2FSantiago'
fx_url='https://open.er-api.com/v6/latest/USD'
agent_diagnostic_excerpt_bytes=262144
agent_stream_limit_bytes=33554432
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
            filter='def safe_timestamp:
                    type == "string"
                    and length > 0
                    and length <= 64
                    and all(explode[]; . >= 32 and . <= 126);
                . as $source
                | select($source.current.time | safe_timestamp)
                | select($source.current.time | test("^[0-9]{4}-[0-9]{2}-[0-9]{2}T"))
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
            filter='def safe_timestamp:
                    type == "string"
                    and length > 0
                    and length <= 64
                    and all(explode[]; . >= 32 and . <= 126);
                . as $source
                | select($source.time_last_update_utc | safe_timestamp)
                | select($source.rates.CLP | numbers)
                | {source_url: $url, source_timestamp: $source.time_last_update_utc, usd_to_clp: $source.rates.CLP}'
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
Tools: client default (untouched -- the check trusts the client's own tooling)
${credential}"
}

log_validation_facts() {
    local check_name="$1" snapshot_file="$2"

    case "$check_name" in
        weather) jq -r '"source_url=\(.source_url)\nsource_date=\(.source_date)\ntemperature_2m=\(.temperature_2m)\nweather_code=\(.weather_code)"' "$snapshot_file" ;;
        fx) jq -r '"source_url=\(.source_url)\nsource_date=\(.source_date)\nusd_to_clp=\(.usd_to_clp)"' "$snapshot_file" ;;
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
    jq -Rrse --arg stream_limit "$agent_stream_limit_bytes" '
        def exactly_one_value($pattern):
            [scan($pattern)]
            | select(length == 1 and (.[0] | length) == 1)
            | .[0][0];

        def at_most($limit):
            length < ($limit | length)
            or (length == ($limit | length) and . <= $limit);

        exactly_one_value("\"outcome\"[[:space:]]*:[[:space:]]*\"(completed|timeout|transcript-limit|stderr-limit|boundary-failure)\"[[:space:]]*[,}]") as $outcome
        | exactly_one_value("\"exit_status\"[[:space:]]*:[[:space:]]*(null|0|-?[1-9][0-9]*)[[:space:]]*[,}]") as $exit_status
        | exactly_one_value("\"cleanup_proved\"[[:space:]]*:[[:space:]]*(true|false)[[:space:]]*[,}]") as $cleanup_proved
        | exactly_one_value("\"transcript_bytes\"[[:space:]]*:[[:space:]]*(0|[1-9][0-9]*)[[:space:]]*[,}]") as $transcript_bytes
        | exactly_one_value("\"stderr_bytes\"[[:space:]]*:[[:space:]]*(0|[1-9][0-9]*)[[:space:]]*[,}]") as $stderr_bytes
        | select($transcript_bytes | at_most($stream_limit))
        | select($stderr_bytes | at_most($stream_limit))
        | $outcome, $exit_status, $cleanup_proved, $transcript_bytes, $stderr_bytes
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
    local transcript_file="$4" stderr_file="$5"
    local parsed_result outcome exit_status cleanup_proved
    local transcript_bytes stderr_bytes actual_transcript_bytes actual_stderr_bytes
    local -a result_fields

    if [ "$runner_status" -eq 0 ] \
        && [ -f "$result_file" ] \
        && [ -f "$transcript_file" ] \
        && [ -f "$stderr_file" ] \
        && parsed_result="$(parse_bounded_result "$result_file" 2>>"$parser_error_file")" \
        && actual_transcript_bytes="$(wc -c < "$transcript_file" 2>>"$parser_error_file")" \
        && actual_stderr_bytes="$(wc -c < "$stderr_file" 2>>"$parser_error_file")"; then
        mapfile -t result_fields <<< "$parsed_result"
        outcome="${result_fields[0]}"
        exit_status="${result_fields[1]}"
        cleanup_proved="${result_fields[2]}"
        transcript_bytes="${result_fields[3]}"
        stderr_bytes="${result_fields[4]}"

        if [[ "$actual_transcript_bytes" =~ ^(0|[1-9][0-9]*)$ ]] \
            && [[ "$actual_stderr_bytes" =~ ^(0|[1-9][0-9]*)$ ]] \
            && [ "$transcript_bytes" = "$actual_transcript_bytes" ] \
            && [ "$stderr_bytes" = "$actual_stderr_bytes" ] \
            && [ "$outcome" != boundary-failure ] \
            && [ "$cleanup_proved" = true ]; then
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
                    AGENT_FAILURE_REASON="client transcript exceeded ${agent_stream_limit_bytes} bytes"
                    AGENT_RESULT_REASON="transcript-limit"
                    return 1
                    ;;
                stderr-limit)
                    AGENT_FAILURE_STAGE="stderr limit"
                    AGENT_FAILURE_REASON="client stderr exceeded ${agent_stream_limit_bytes} bytes"
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
            DISPLAYED_CLIENT_COMMAND="PI_CODING_AGENT_DIR=<private> pi --no-session --no-extensions --no-skills --no-prompt-templates --no-context-files -p --mode json --model llm-env/${alias} ${quoted_prompt}"
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
                UV_CACHE_DIR="$uv_cache_dir" uv run --offline \
                    "${REPO_DIR}/llmenv.py" run-agent-bounded \
                    --transcript "$transcript_file" \
                    --stderr "$stderr_file" \
                    -- "${client_command[@]}" </dev/null >"$bounded_result_file"
            ) 2>>"$parser_error_file"; then
                status=0
            else
                status=$?
            fi
            if ! classify_bounded_result "$status" "$bounded_result_file" \
                "$parser_error_file" "$transcript_file" "$stderr_file"; then
                return 1
            fi
            if ! jq -rjce '
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
                '{"$schema": "https://opencode.ai/config.json", provider: {"llm-env": {
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
                UV_CACHE_DIR="$uv_cache_dir" uv run --offline \
                    "${REPO_DIR}/llmenv.py" run-agent-bounded \
                    --transcript "$transcript_file" \
                    --stderr "$stderr_file" \
                    -- "${client_command[@]}" </dev/null >"$bounded_result_file"
            ) 2>>"$parser_error_file"; then
                status=0
            else
                status=$?
            fi
            if ! classify_bounded_result "$status" "$bounded_result_file" \
                "$parser_error_file" "$transcript_file" "$stderr_file"; then
                return 1
            fi
            if ! jq -rjce '
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

    # Trusting the agent's own tooling (see the prompt above) means it may narrate
    # what it did before or after the JSON, not return the JSON as the entire
    # message -- extract the first fenced ```json block from anywhere in the
    # response rather than requiring the whole response to be exactly that block.
    if grep -q '```json' "$assistant_file" 2>/dev/null; then
        # shellcheck disable=SC2016 # Literal sed address, not variable expansion.
        sed -n '/```json/,/```/p' "$assistant_file" | sed '1d;$d' |
            jq -sce 'select(length == 1 and (.[0] | type == "object")) | .[0]'
        return
    fi
    jq -sce \
        'select(length == 1 and (.[0] | type == "object")) | .[0]' \
        "$assistant_file"
}

source_evidence_differences() {
    # Compares the agent's self-reported evidence against the independently-fetched
    # snapshot with TOLERANCE, not exact equality. The agent picks its own source and
    # tool (see the prompt built above), so a different upstream provider, slightly
    # different fetch instant, or different weather-code taxonomy is expected and not
    # a failure -- what must hold is that the values are well-formed and plausible
    # (a real "right now" reading), which is strong evidence against hallucination.
    local check_name="$1"
    local snapshot_file="$2"
    local evidence_file="$3"
    local source_timezone timestamp source_date
    local timestamp_pattern='^[0-9]{4}-[0-9]{2}-[0-9]{2}T([01][0-9]|2[0-3]):[0-5][0-9](:[0-5][0-9](\.[0-9]+)?)?(Z|[+-]([01][0-9]|2[0-3]):?[0-5][0-9])?$'

    case "$check_name" in
        weather) source_timezone='America/Santiago' ;;
        fx) source_timezone='UTC' ;;
        *) return 1 ;;
    esac

    if ! jq -e '(type == "object") and (.source_url | type == "string") and (.source_url | test("^https?://"))' \
        "$evidence_file" >/dev/null 2>&1; then
        printf '%s\n' 'field=source_url expected="a well-formed http(s) URL" received="<redacted>"'
    fi

    if ! timestamp="$(jq -nr --arg timestamp_pattern "$timestamp_pattern" \
        --slurpfile received "$evidence_file" '
        def safe_timestamp:
            type == "string"
            and length > 0
            and length <= 64
            and all(explode[]; . >= 32 and . <= 126)
            and test($timestamp_pattern);

        if ($received | length) == 1 and ($received[0] | type) == "object" then
            $received[0].source_timestamp | select(safe_timestamp)
        else
            error("validated evidence file must contain exactly one object")
        end
    ')"; then
        return 1
    fi
    if [ -z "$timestamp" ]; then
        printf '%s\n' 'field=source_timestamp expected=ISO-8601 received="<redacted>"'
        return 0
    fi
    # Calendar-day comparisons must use the timezone in which the source publishes data.
    if ! source_date="$(TZ="$source_timezone" date --date "$timestamp" +%F 2>/dev/null)"; then
        printf '%s\n' 'field=source_timestamp expected=ISO-8601 received="<redacted>"'
        return 0
    fi

    jq -nr --slurpfile expected "$snapshot_file" \
        --slurpfile received "$evidence_file" --arg source_date "$source_date" \
        --arg check_name "$check_name" '
        def field_abs_diff($expected; $received; $field; $tolerance):
            ($received[$field]) as $got
            | if ($got | type) != "number" then
                "field=\($field) expected=\"a number\" received=\"<redacted>\""
              elif (($expected[$field] - $got) | fabs) > $tolerance then
                "field=\($field) expected=within \($tolerance) of source_snapshot received=\"<redacted>\""
              else empty
              end;

        def field_rel_diff($expected; $received; $field; $fraction):
            ($received[$field]) as $got
            | ($expected[$field]) as $want
            | if ($got | type) != "number" then
                "field=\($field) expected=\"a number\" received=\"<redacted>\""
              elif ((($want - $got) | fabs) > ($want * $fraction)) then
                "field=\($field) expected=within \($fraction * 100)% of source_snapshot received=\"<redacted>\""
              else empty
              end;

        ($expected
         | if length == 1 and (.[0] | type) == "object" then .[0]
           else error("source snapshot file must contain exactly one object")
           end) as $expected
        | ($received
           | if length == 1 and (.[0] | type) == "object" then .[0]
             else error("validated evidence file must contain exactly one object")
             end) as $received
        |
        [
          (($expected.source_date // "<missing>") as $want_date
           | $source_date as $got_date
           | select($want_date != $got_date)
           | "field=source_timestamp expected_date=\($want_date | tojson) received_date=\"<redacted>\""),
          (if $check_name == "weather" then
             field_abs_diff($expected; $received; "temperature_2m"; 8),
             # Not range-bound to WMO codes (0-99): the agent may use any public
             # weather API, and providers publish condition codes in different
             # taxonomies (e.g. wttr.in codes exceed the WMO range). Only the
             # type is checked here.
             ($received.weather_code as $code
              | if ($code | type) != "number" then
                  "field=weather_code expected=\"a number\" received=\"<redacted>\""
                else empty
                end)
           elif $check_name == "fx" then
             field_rel_diff($expected; $received; "usd_to_clp"; 0.05)
           else empty
           end)
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

if ! uv_cache_dir="$(uv cache dir)"; then
    die "could not resolve uv cache directory"
fi
[ -n "$uv_cache_dir" ] || die "uv cache directory is empty"

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
                    source_description="the current weather (temperature in Celsius and WMO weather code) for Santiago, Chile, from a public weather API"
                    fields='source_url, source_timestamp, temperature_2m, and weather_code'
                    agent_expectation='exactly one JSON object with a plausible, current weather reading for Santiago, Chile, within tolerance of the independently-fetched source'
                    ;;
                fx)
                    snapshot_for "$check_name" "$source_stdout" "$source_stderr" "$source_parser_stderr" || {
                        log_error "Verdict: FAIL stage=source fetch reason=FX snapshot unavailable"
                        failures=$((failures + 1))
                        continue
                    }
                    snapshot="$SNAPSHOT_RESULT"
                    source_description="the current USD to CLP exchange rate, from a public FX API"
                    fields='source_url, source_timestamp, and usd_to_clp'
                    agent_expectation='exactly one JSON object with a plausible, current USD/CLP rate, within tolerance of the independently-fetched source'
                    ;;
            esac
            snapshot_file="$(mktemp "${diagnostic_dir}/source-snapshot.XXXXXX")" || die "could not create source snapshot"
            chmod 600 "$snapshot_file" || die "could not secure source snapshot"
            printf '%s\n' "$snapshot" > "$snapshot_file" || die "could not write source snapshot"
            # Deliberately does not name a URL or a fetch method: this check verifies the
            # agent+model pairing can resolve and use whatever tooling the client provides
            # on its own, not that it can copy a literal command. source_evidence_differences()
            # below validates the result against the independently-fetched snapshot with
            # tolerance, since a different upstream source is expected to differ slightly.
            printf -v prompt '%s' "Using your own tools, find ${source_description}. Return exactly one JSON object containing ${fields}. source_url must be the exact URL of the API endpoint you used. source_timestamp must be the source's own observation/update time, in ISO-8601."

            transcript_file="$(mktemp "${diagnostic_dir}/client-transcript.XXXXXX")" || die "could not create client transcript"
            client_stderr_file="$(mktemp "${diagnostic_dir}/client-stderr.XXXXXX")" || die "could not create client stderr"
            final_file="$(mktemp "${diagnostic_dir}/assistant-final.XXXXXX")" || die "could not create assistant final text"
            evidence_file="$(mktemp "${diagnostic_dir}/agent-evidence.XXXXXX")" || die "could not create normalized agent evidence"
            chmod 600 "$evidence_file" || die "could not secure normalized agent evidence"
            parser_error_file="$(mktemp "${diagnostic_dir}/agent-parser-stderr.XXXXXX")" || die "could not create agent parser stderr"
            differences=""
            agent_failed=0
            if run_agent "$client" "$alias" "$prompt" "$transcript_file" \
                "$client_stderr_file" "$final_file" "$parser_error_file"; then
                if ! final_bytes="$(wc -c < "$final_file" 2>>"$parser_error_file")" \
                    || ! [[ "$final_bytes" =~ ^(0|[1-9][0-9]*)$ ]]; then
                    mark_agent_boundary_failure
                    agent_failed=1
                elif [ "${#final_bytes}" -gt "${#final_response_limit_bytes}" ] \
                    || { [ "${#final_bytes}" -eq "${#final_response_limit_bytes}" ] \
                        && [ "$final_bytes" -gt "$final_response_limit_bytes" ]; }; then
                    AGENT_FAILURE_STAGE="final response limit"
                    AGENT_FAILURE_REASON="final assistant text exceeded ${final_response_limit_bytes} bytes"
                    AGENT_RESULT_REASON="final-response-limit"
                    agent_failed=1
                elif ! parse_evidence "$final_file" > "$evidence_file" \
                    2>>"$parser_error_file"; then
                    AGENT_FAILURE_STAGE="agent evidence parsing"
                    agent_failed=1
                fi
            else
                agent_failed=1
            fi
            if [ "$agent_failed" -eq 0 ] \
                && ! differences="$(source_evidence_differences "$check_name" \
                    "$snapshot_file" "$evidence_file" 2>>"$parser_error_file")"; then
                AGENT_FAILURE_STAGE="source-evidence comparison"
                AGENT_FAILURE_REASON="validated evidence comparison failed"
                AGENT_RESULT_REASON="evidence-comparison-failure"
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
                    classified_json="$(llmenv classify-transcript --client "$client" --transcript "$transcript_file" 2>/dev/null)" || classified_json=""
                    excerpt="$(printf '%s' "$classified_json" | jq -r '.excerpt // empty' 2>/dev/null | head -c "$agent_diagnostic_excerpt_bytes")"
                    if [ -n "$excerpt" ]; then
                        log_block "Relevant transcript excerpt" "$excerpt"
                    else
                        log_file_excerpt "Client JSONL transcript" "$transcript_file" "$agent_diagnostic_excerpt_bytes"
                    fi
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

            if [ -z "$differences" ]; then
                if [ -s "$client_stderr_file" ]; then
                    log_file_excerpt "Client stderr" "$client_stderr_file" "$agent_diagnostic_excerpt_bytes"
                fi
                if [ -s "$parser_error_file" ]; then
                    log_file_excerpt "Agent parser stderr" "$parser_error_file" "$agent_diagnostic_excerpt_bytes"
                fi
                log_file_excerpt "Final response" "$final_file" "$agent_diagnostic_excerpt_bytes"
                log_block "Validated" "$(log_validation_facts "$check_name" "$snapshot_file")"
                log_info "Verdict: PASS"
                printf 'PASS client=%s model=%s check=%s reason=agent-returned-json\n' \
                    "$client" "$alias" "$check_name"
                passes=$((passes + 1))
                continue
            fi
            if [ -s "$transcript_file" ]; then
                classified_json="$(llmenv classify-transcript --client "$client" --transcript "$transcript_file" 2>/dev/null)" || classified_json=""
                excerpt="$(printf '%s' "$classified_json" | jq -r '.excerpt // empty' 2>/dev/null | head -c "$agent_diagnostic_excerpt_bytes")"
                if [ -n "$excerpt" ]; then
                    log_block "Relevant transcript excerpt" "$excerpt"
                else
                    log_file_excerpt "Client JSONL transcript" "$transcript_file" "$agent_diagnostic_excerpt_bytes"
                fi
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
            log_block "Validated" "$(log_validation_facts "$check_name" "$snapshot_file")"
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
