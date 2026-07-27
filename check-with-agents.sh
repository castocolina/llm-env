#!/usr/bin/env bash
# check-with-agents.sh — ask installed coding agents to independently verify public data.
set +x
set -uo pipefail
# shellcheck source=lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
set +e

if [ "$#" -ne 0 ]; then
    printf '%s\n' 'fail check-with-agents accepts no arguments' >&2
    exit 1
fi

require_cmd curl jq yq

workspace="$(mktemp -d)" || die "could not create private workspace"
chmod 700 "$workspace" || die "could not secure private workspace"
trap 'rm -rf "$workspace"' EXIT

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

source_weather() {
    curl -fsS --max-time 20 "$weather_url" | jq -ce \
        '. as $source
         | select(($source.current.time | strings | length) > 0)
         | select($source.current.temperature_2m | numbers)
         | select($source.current.weather_code | numbers)
         | {source_url: $url, source_timestamp: $source.current.time,
            temperature_2m: $source.current.temperature_2m,
            weather_code: $source.current.weather_code}' \
        --arg url "$weather_url"
}

source_fx() {
    curl -fsS --max-time 20 "$fx_url" | jq -ce \
        '. as $source
         | select(($source.time_last_update_utc | strings | length) > 0)
         | select($source.rates.CLP | numbers)
         | {source_url: $url, source_timestamp: $source.time_last_update_utc,
            usd_to_clp: $source.rates.CLP}' \
        --arg url "$fx_url"
}

run_agent() {
    local client="$1"
    local alias="$2"
    local check_name="$3"
    local prompt="$4"
    local snapshot="$5"
    local client_base="http://llm.local:${port}/v1"
    local config_dir config_file config_key_command

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
            (
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
            ) 2>"$workspace/pi-agent-error" |
                jq -rce '
                    [select(.type == "message_end" and .message.role == "assistant")
                     | [.message.content[]? | select(.type == "text") | .text] | join("")]
                    | last // empty
                ' 2>"$workspace/pi-agent-parse-error" |
                jq -ce 'select(type == "object")' 2>>"$workspace/pi-agent-parse-error" || {
                    printf '%s\n' 'agent invocation failed' >&2
                    return 1
                }
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
            (
                cd "$workspace" || exit 1
                export HOME="$workspace/opencode-home"
                export XDG_CONFIG_HOME="$workspace/opencode-config"
                export XDG_DATA_HOME="$workspace/opencode-data"
                export XDG_STATE_HOME="$workspace/opencode-state"
                export OPENCODE_CONFIG="$config_file"
                export OPENCODE_API_KEY="$api_key"
                opencode run --format json --model "llm-env/${alias}" "$prompt"
            ) 2>"$workspace/opencode-agent-error" |
                jq -rce '
                    reduce (select(.type == "text" and .part.type == "text") | .part) as $part
                    ({message_id: null, text: ""};
                     if .message_id == $part.messageID then .text += $part.text
                     else {message_id: $part.messageID, text: $part.text}
                     end)
                    | .text
                ' 2>"$workspace/opencode-agent-parse-error" |
                jq -ce 'select(type == "object")' 2>>"$workspace/opencode-agent-parse-error" || {
                    printf '%s\n' 'agent invocation failed' >&2
                    return 1
                }
            ;;
        *)
            printf '%s\n' 'agent invocation failed' >&2
            return 1
            ;;
    esac
}

source_evidence_matches() {
    local check_name="$1"
    local snapshot="$2"
    local evidence_file="$3"

    case "$check_name" in
        weather)
            jq -ce --argjson snapshot "$snapshot" '
                select(type == "object")
                | select(.source_url == $snapshot.source_url)
                | select(.source_timestamp == $snapshot.source_timestamp)
                | select(.weather_code == $snapshot.weather_code)
                | select(.temperature_2m | type == "number")
                | select(.temperature_2m == $snapshot.temperature_2m)
            ' "$evidence_file" >/dev/null 2>&1
            ;;
        fx)
            jq -ce --argjson snapshot "$snapshot" '
                select(type == "object")
                | select(.source_url == $snapshot.source_url)
                | select(.source_timestamp == $snapshot.source_timestamp)
                | select(.usd_to_clp | type == "number")
                | select(.usd_to_clp == $snapshot.usd_to_clp)
            ' "$evidence_file" >/dev/null 2>&1
            ;;
        *)
            return 1
            ;;
    esac
}

weather_snapshot="$(source_weather)" || die "could not fetch a valid weather snapshot"
fx_snapshot="$(source_fx)" || die "could not fetch a valid FX snapshot"
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
            case "$check_name" in
                weather)
                    snapshot="$weather_snapshot"
                    fields='source_url, source_timestamp, temperature_2m, and weather_code'
                    ;;
                fx)
                    snapshot="$fx_snapshot"
                    fields='source_url, source_timestamp, and usd_to_clp'
                    ;;
            esac
            printf -v prompt '%s' \
                "Fetch the public ${check_name} source yourself with a shell network command. Return exactly one JSON object with ${fields}. Public snapshot for comparison: ${snapshot}"

            if run_agent "$client" "$alias" "$check_name" "$prompt" "$snapshot" \
                > "$workspace/agent-output.json"; then
                if source_evidence_matches "$check_name" "$snapshot" "$workspace/agent-output.json"; then
                    printf 'PASS client=%s model=%s check=%s reason=agent-returned-json\n' \
                        "$client" "$alias" "$check_name"
                else
                    printf 'fail client=%s model=%s check=%s source evidence differs\n' \
                        "$client" "$alias" "$check_name" >&2
                    printf 'FAIL client=%s model=%s check=%s reason=source-evidence-differs\n' \
                        "$client" "$alias" "$check_name"
                    failures=$((failures + 1))
                fi
            else
                printf 'FAIL client=%s model=%s check=%s reason=agent-failed\n' \
                    "$client" "$alias" "$check_name"
                failures=$((failures + 1))
            fi
        done
    done <<< "$aliases"
done

[ "$failures" -eq 0 ]
