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

require_cmd curl jq yq

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

fetch_models() {
    local config_file="$1"
    local server_base="$2"

    curl -fsS --max-time 20 -K "$config_file" "${server_base}/v1/models" |
        jq -er '.data[]?.id'
}

snapshot_for() {
    local check_name="$1" stdout_file="$2" stderr_file="$3" parser_stderr_file="$4"
    local url filter snapshot status

    case "$check_name" in
        weather)
            url="$weather_url"
            # shellcheck disable=SC2016 # jq variables must remain literal until jq evaluates them.
            filter='. as $source | select(($source.current.time | strings | length) > 0) | select($source.current.temperature_2m | numbers) | select($source.current.weather_code | numbers) | {source_url: $url, source_timestamp: $source.current.time, temperature_2m: $source.current.temperature_2m, weather_code: $source.current.weather_code}'
            ;;
        fx)
            url="$fx_url"
            # shellcheck disable=SC2016 # jq variables must remain literal until jq evaluates them.
            filter='. as $source | select(($source.time_last_update_utc | strings | length) > 0) | select($source.rates.CLP | numbers) | {source_url: $url, source_timestamp: $source.time_last_update_utc, usd_to_clp: $source.rates.CLP}'
            ;;
        *) return 64 ;;
    esac

    log_block "Identity" "source fetch check=${check_name}"
    log_command "curl --fail --silent --show-error --max-time 20 ${url}"
    log_block "Input" "(none)"
    if curl -fsS --max-time 20 "$url" >"$stdout_file" 2>"$stderr_file"; then
        status=0
    else
        status=$?
    fi
    log_block "Source stdout" "$(<"$stdout_file")"
    log_block "Source stderr" "$(<"$stderr_file")"
    log_block "Exit status" "$status"
    if [ "$status" -ne 0 ]; then
        log_error "Verdict: FAIL stage=source fetch reason=${check_name} source exited ${status}"
        return 1
    fi
    if ! snapshot="$(jq -ce --arg url "$url" "$filter" "$stdout_file" 2>"$parser_stderr_file")"; then
        log_block "Source parser stderr" "$(<"$parser_stderr_file")"
        log_error "Verdict: FAIL stage=source fetch reason=${check_name} source body is invalid"
        return 1
    fi
    log_block "Source parser stderr" "$(<"$parser_stderr_file")"
    log_block "Parsed result" "$snapshot"
    log_block "Expectation" "a current typed ${check_name} source object"
    log_info "Verdict: PASS"
    SNAPSHOT_RESULT="$snapshot"
}

run_agent() {
    local client="$1"
    local alias="$2"
    local prompt="$3"
    local transcript_file="$4"
    local stderr_file="$5"
    local final_file="$6"
    local parser_error_file="$7"
    local client_base="http://llm.local:${port}/v1"
    local config_dir config_file config_key_command status

    AGENT_FAILURE_STAGE="command exit"
    DISPLAYED_CLIENT_COMMAND=""

    case "$client" in
        pi)
            config_dir="$workspace/pi"
            config_file="$config_dir/models.json"
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
            DISPLAYED_CLIENT_COMMAND="PI_CODING_AGENT_DIR=<private> pi --no-session --no-extensions --no-skills --no-prompt-templates --no-context-files --tools bash -p --mode json --model llm-env/${alias} <prompt>"
            if (
                cd "$workspace" || exit 1
                PI_CODING_AGENT_DIR="$config_dir" pi \
                    --no-session \
                    --no-extensions \
                    --no-skills \
                    --no-prompt-templates \
                    --no-context-files \
                    --tools bash \
                    -p \
                    --mode json \
                    --model "llm-env/${alias}" \
                    "$prompt"
            ) 2>"$stderr_file" | tee "$transcript_file" >/dev/null; then
                status=0
            else
                status=$?
            fi
            if [ "$status" -ne 0 ]; then
                return 1
            fi
            if ! jq -rce '
                [select(.type == "message_end" and .message.role == "assistant")
                 | [.message.content[]? | select(.type == "text") | .text] | join("")]
                | last // empty
            ' "$transcript_file" >"$final_file" 2>"$parser_error_file"; then
                AGENT_FAILURE_STAGE="response parsing"
                return 1
            fi
            ;;
        opencode)
            config_dir="$workspace/opencode"
            config_file="$config_dir/opencode.jsonc"
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
            DISPLAYED_CLIENT_COMMAND="OPENCODE_CONFIG=<private> OPENCODE_API_KEY=<redacted> opencode run --format json --model llm-env/${alias} <prompt>"
            if (
                cd "$workspace" || exit 1
                export HOME="$workspace/opencode-home"
                export XDG_CONFIG_HOME="$workspace/opencode-config"
                export XDG_DATA_HOME="$workspace/opencode-data"
                export XDG_STATE_HOME="$workspace/opencode-state"
                export OPENCODE_CONFIG="$config_file"
                export OPENCODE_API_KEY="$api_key"
                opencode run --format json --model "llm-env/${alias}" "$prompt"
            ) 2>"$stderr_file" | tee "$transcript_file" >/dev/null; then
                status=0
            else
                status=$?
            fi
            if [ "$status" -ne 0 ]; then
                return 1
            fi
            if ! jq -rce '
                reduce (select(.type == "text" and .part.type == "text") | .part) as $part
                ({message_id: null, text: ""};
                 if .message_id == $part.messageID then .text += $part.text
                 else {message_id: $part.messageID, text: $part.text}
                 end)
                | .text
            ' "$transcript_file" >"$final_file" 2>"$parser_error_file"; then
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
    local assistant_text="$1" fenced fence_prefix='^[[:space:]]*```json'

    if [[ "$assistant_text" =~ $fence_prefix ]]; then
        if ! fenced="$(printf '%s' "$assistant_text" | jq -Rrse \
            'capture("^[[:space:]]*```json\\r?\\n(?<body>[\\s\\S]*?)\\r?\\n```[[:space:]]*$").body' 2>/dev/null)"; then
            return 1
        fi
        [ -n "$fenced" ] || return 1
        printf '%s' "$fenced" | jq -sce \
            'select(length == 1 and (.[0] | type == "object")) | .[0]'
        return
    fi
    printf '%s' "$assistant_text" | jq -sce \
        'select(length == 1 and (.[0] | type == "object")) | .[0]'
}

source_evidence_differences() {
    local check_name="$1"
    local snapshot="$2"
    local evidence="$3"
    local required_fields

    case "$check_name" in
        weather)
            required_fields='["source_url", "source_timestamp", "temperature_2m", "weather_code"]'
            ;;
        fx)
            required_fields='["source_url", "source_timestamp", "usd_to_clp"]'
            ;;
        *)
            return 1
            ;;
    esac

    jq -nr --argjson expected "$snapshot" --argjson received "$evidence" \
        --argjson fields "$required_fields" '
        $fields[] as $field
        | ($expected[$field] // "<missing>") as $want
        | ($received[$field] // "<missing>") as $got
        | select($want != $got)
        | "field=\($field) expected=\($want | tojson) received=\($got | tojson)"
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
                    ;;
            esac
            printf -v prompt '%s' \
                "Use a shell network command to fetch current data from ${source_url}. Return exactly one JSON object containing ${fields}."

            transcript_file="$(mktemp "${diagnostic_dir}/client-transcript.XXXXXX")" || die "could not create client transcript"
            client_stderr_file="$(mktemp "${diagnostic_dir}/client-stderr.XXXXXX")" || die "could not create client stderr"
            final_file="$(mktemp "${diagnostic_dir}/assistant-final.XXXXXX")" || die "could not create assistant final text"
            parser_error_file="$(mktemp "${diagnostic_dir}/agent-parser-stderr.XXXXXX")" || die "could not create agent parser stderr"
            evidence=""
            agent_failed=0
            if run_agent "$client" "$alias" "$prompt" "$transcript_file" \
                "$client_stderr_file" "$final_file" "$parser_error_file"; then
                if ! evidence="$(parse_evidence "$(<"$final_file")" 2>>"$parser_error_file")"; then
                    AGENT_FAILURE_STAGE="agent evidence parsing"
                    agent_failed=1
                fi
            else
                agent_failed=1
            fi

            log_block "Identity" "client=${client} model=${alias} check=${check_name}"
            log_command "$DISPLAYED_CLIENT_COMMAND"
            log_block "Input" "$prompt"
            log_block "Client JSONL transcript" "$(<"$transcript_file")"
            log_block "Client stderr" "$(<"$client_stderr_file")"
            log_block "Assistant final text" "$(<"$final_file")"
            log_block "Agent parser stderr" "$(<"$parser_error_file")"
            log_block "Agent evidence" "$evidence"
            log_block "Fresh source snapshot" "$snapshot"

            if [ "$agent_failed" -ne 0 ]; then
                log_error "Verdict: FAIL stage=${AGENT_FAILURE_STAGE} client=${client} model=${alias} check=${check_name} reason=agent invocation failed"
                printf 'FAIL client=%s model=%s check=%s reason=agent-failed\n' \
                    "$client" "$alias" "$check_name"
                failures=$((failures + 1))
                continue
            fi

            differences="$(source_evidence_differences "$check_name" "$snapshot" "$evidence")"
            if [ -z "$differences" ]; then
                log_info "Verdict: PASS"
                printf 'PASS client=%s model=%s check=%s reason=agent-returned-json\n' \
                    "$client" "$alias" "$check_name"
                continue
            fi
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

[ "$failures" -eq 0 ]
