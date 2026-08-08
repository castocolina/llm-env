#!/usr/bin/env bash
# lib.sh — shared helpers. Source, do not execute.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_PATH="${LLM_ENV_CONFIG:-${HOME}/.config/llm-env/models.yml}"
MODELS_DIR="${LLM_ENV_MODELS_DIR:-${HOME}/llm-workspace/models}"
UNIT_NAME="llm-server"
COMPOSE_FILE="${HOME}/.config/llm-env/docker-compose.yml"
WRAPPER_UNIT_PATH="${HOME}/.config/systemd/user/${UNIT_NAME}.service"
VULKAN_IMAGE="ghcr.io/ggml-org/llama.cpp:server-vulkan"
CPU_IMAGE="ghcr.io/ggml-org/llama.cpp:server"
LLM_ENV_HEALTH_TIMEOUT_SECONDS="${LLM_ENV_HEALTH_TIMEOUT_SECONDS:-60}"

# Exported so scripts that source this file expose them to child processes, and so
# the linter does not flag them as unused (SC2034) in this library.
export REPO_DIR CONFIG_PATH MODELS_DIR UNIT_NAME COMPOSE_FILE WRAPPER_UNIT_PATH VULKAN_IMAGE CPU_IMAGE LLM_ENV_HEALTH_TIMEOUT_SECONDS

GREEN=$'\033[0;32m'; YELLOW=$'\033[1;33m'; BLUE=$'\033[0;34m'
RED=$'\033[0;31m'; NC=$'\033[0m'
# shellcheck disable=SC2034 # Consumed by tools/run-target.sh, which sources this file.
BOLD=$'\033[1m'

log_step()  { printf '%s==>%s %s\n' "$BLUE"   "$NC" "$*"; }
log_info()  { printf '%s  ok%s %s\n' "$GREEN" "$NC" "$*"; }
log_warn()  { printf '%swarn%s %s\n' "$YELLOW" "$NC" "$*" >&2; }
log_error() { printf '%s fail%s %s\n' "$RED"  "$NC" "$*" >&2; }

_redact_stream() {
    local +x key escaped
    if [ "${_LLM_ENV_REDACTION_KEY_OVERRIDE+x}" = x ]; then
        key="${_LLM_ENV_REDACTION_KEY_OVERRIDE-}"
    else
        key="$(yq -r '.server.api_key // ""' "$CONFIG_PATH" 2>/dev/null)"
    fi

    sed -E 's/(Authorization:[[:space:]]*Bearer)[[:space:]]+[^[:space:]"'"'"']+/\1 <redacted>/g' |
        if [ -n "$key" ]; then
            escaped="$(printf '%s' "$key" | sed 's/[][\\.^$*\/]/\\&/g')"
            sed -f /dev/fd/3 3<<<"s/${escaped}/<redacted>/g"
        else
            cat
        fi
}

redact_text() {
    printf '%s' "$1" | _redact_stream
}

log_command() {
    printf 'Command: '
    redact_text "$1"
    printf '\n'
}

log_block() {
    local label="$1" text="$2"
    printf '%s:\n' "$(redact_text "$label")"
    if [ -z "$text" ]; then
        printf '  (empty)\n'
    else
        redact_text "$text" | sed 's/^/  /'
        case "$text" in
            *$'\n') ;;
            *) printf '\n' ;;
        esac
    fi
}

log_file_excerpt() {
    if [ "$#" -ne 3 ]; then
        return 64
    fi

    local label="$1" file="$2" max_bytes="$3" redacted_label
    if [[ ! "$max_bytes" =~ ^[0-9]+$ ]]; then
        return 64
    fi
    if [ ! -f "$file" ] || [ ! -r "$file" ]; then
        return 66
    fi
    if ! redacted_label="$(redact_text "$label")"; then
        return 1
    fi

    printf '%s:\n' "$redacted_label"
    if [ ! -s "$file" ]; then
        printf '  (empty)\n'
        return 0
    fi
    if ! _redact_stream < "$file" |
        {
            excerpt_status=0
            head -c "$max_bytes" || excerpt_status=$?
            cat >/dev/null || excerpt_status=$?
            exit "$excerpt_status"
        } |
        sed 's/^/  /'
    then
        return 1
    fi
    printf '\n'
}

log_nonempty_block() {
    if [ -n "$2" ]; then
        log_block "$1" "$2"
    fi
}

_discard_diagnostic_dir() {
    local directory="$1"
    chmod -R u+rwx -- "$directory" >/dev/null 2>&1 || true
    rm -rf -- "$directory" >/dev/null 2>&1
}

_fail_diagnostic_dir() {
    local directory="$1" message="$2" file_list="${3:-}"
    if [ -n "$file_list" ]; then
        rm -f -- "$file_list" >/dev/null 2>&1 || true
    fi
    _discard_diagnostic_dir "$directory" || true
    die "$message"
}

prepare_diagnostic_dir() {
    local name="$1" directory
    directory="$(mktemp -d "${TMPDIR:-/tmp}/llm-env-${name}.XXXXXX" 2>/dev/null)" \
        || die "could not create private diagnostic directory"
    if ! chmod 700 -- "$directory" 2>/dev/null; then
        _fail_diagnostic_dir "$directory" "could not secure diagnostic directory"
    fi
    printf '%s\n' "$directory"
}

finish_diagnostic_dir() {
    local directory="$1" file file_list temporary_file error_message
    if [ "${LLM_ENV_KEEP_CHECK_ARTIFACTS:-}" != "1" ]; then
        if ! _discard_diagnostic_dir "$directory"; then
            die "could not remove diagnostic directory"
        fi
        return
    fi

    if ! chmod 700 -- "$directory" 2>/dev/null; then
        _fail_diagnostic_dir "$directory" "could not secure diagnostic directory"
    fi

    if ! file_list="$(mktemp "${TMPDIR:-/tmp}/llm-env-diagnostic-files.XXXXXX" 2>/dev/null)"; then
        _fail_diagnostic_dir "$directory" "could not prepare diagnostic artifact list"
    fi
    if ! chmod 600 -- "$file_list" 2>/dev/null; then
        _fail_diagnostic_dir "$directory" "could not secure diagnostic artifact list" "$file_list"
    fi
    if ! find "$directory" -type f -print0 > "$file_list" 2>/dev/null; then
        _fail_diagnostic_dir "$directory" "could not traverse diagnostic artifacts" "$file_list"
    fi

    error_message=""
    while IFS= read -r -d '' file; do
        if [[ "$file" != "$directory/"* ]] || [ ! -f "$file" ]; then
            error_message="could not verify diagnostic artifact"
            break
        fi
        if ! temporary_file="$(mktemp "${file}.XXXXXX" 2>/dev/null)"; then
            error_message="could not prepare diagnostic artifact"
            break
        fi
        if ! _redact_stream 2>/dev/null < "$file" > "$temporary_file"; then
            rm -f -- "$temporary_file" >/dev/null 2>&1 || true
            error_message="could not redact diagnostic artifact"
            break
        fi
        if ! mv -- "$temporary_file" "$file" 2>/dev/null; then
            rm -f -- "$temporary_file" >/dev/null 2>&1 || true
            error_message="could not retain diagnostic artifact"
            break
        fi
        if ! chmod 600 -- "$file" 2>/dev/null; then
            error_message="could not secure diagnostic artifact"
            break
        fi
    done < "$file_list"

    if [ -n "$error_message" ]; then
        _fail_diagnostic_dir "$directory" "$error_message" "$file_list"
    fi
    if ! rm -f -- "$file_list" 2>/dev/null; then
        _fail_diagnostic_dir "$directory" "could not remove diagnostic artifact list" "$file_list"
    fi

    printf 'Diagnostics retained: '
    redact_text "$directory"
    printf '\n'
}

die() { log_error "$*"; exit 1; }

require_cmd() {
    for cmd in "$@"; do
        command -v "$cmd" >/dev/null 2>&1 || die "required command not found: $cmd"
    done
}

# Run llmenv.py and return its JSON on stdout.
llmenv() { uv run "${REPO_DIR}/llmenv.py" "$@"; }

migrate_config_file() {
    local response
    if ! response="$(llmenv --config "$CONFIG_PATH" migrate-config)"; then
        jq -r '.error // "configuration migration failed"' <<<"$response" >&2
        return 1
    fi
}

new_api_key() {
    head -c 32 /dev/urandom | base64 | tr -d '/+=\n'
}

ensure_api_key() {
    local api_key
    api_key="$(yq -r '.server.api_key' "$CONFIG_PATH")"
    if [ -z "$api_key" ] || [ "$api_key" = "null" ]; then
        api_key="$(new_api_key)"
        chmod 600 "$CONFIG_PATH"
        API_KEY="$api_key" yq -i '.server.api_key = strenv(API_KEY)' "$CONFIG_PATH"
        log_info "generated an API key"
    fi
    chmod 600 "$CONFIG_PATH"
}

ensure_omniroute_secrets() {
    local cli_token initial_password
    cli_token="$(yq -r '.omniroute.cli_token' "$CONFIG_PATH")"
    if [ -z "$cli_token" ] || [ "$cli_token" = "null" ]; then
        cli_token="$(new_api_key)"
        chmod 600 "$CONFIG_PATH"
        CLI_TOKEN="$cli_token" yq -i '.omniroute.cli_token = strenv(CLI_TOKEN)' "$CONFIG_PATH"
        log_info "generated an OmniRoute CLI token"
    fi
    initial_password="$(yq -r '.omniroute.initial_password' "$CONFIG_PATH")"
    if [ -z "$initial_password" ] || [ "$initial_password" = "null" ]; then
        initial_password="$(new_api_key)"
        chmod 600 "$CONFIG_PATH"
        INITIAL_PASSWORD="$initial_password" yq -i '.omniroute.initial_password = strenv(INITIAL_PASSWORD)' "$CONFIG_PATH"
        log_info "generated an OmniRoute dashboard password"
    fi
    chmod 600 "$CONFIG_PATH"
}

wait_for_tcp_port() {
    local port="$1" attempt
    for (( attempt = 0; attempt < LLM_ENV_HEALTH_TIMEOUT_SECONDS; attempt++ )); do
        if (exec 3<>"/dev/tcp/127.0.0.1/${port}") 2>/dev/null; then
            exec 3<&- 3>&-
            return 0
        fi
        sleep 1
    done
    return 1
}

reset_api_key() {
    local api_key
    api_key="$(new_api_key)"
    chmod 600 "$CONFIG_PATH"
    API_KEY="$api_key" yq -i '.server.api_key = strenv(API_KEY)' "$CONFIG_PATH"
    chmod 600 "$CONFIG_PATH"
}

# shellcheck disable=SC2034 # PORT/API_KEY/HOST are consumed by this function's callers.
load_server_config() {
    PORT="$(yq -r '.server.port' "$CONFIG_PATH")"
    API_KEY="$(yq -r '.server.api_key' "$CONFIG_PATH")"
    HOST="$(yq -r '.server.host' "$CONFIG_PATH")"
}

wait_for_health() {
    local port="$1" attempt
    for (( attempt = 0; attempt < LLM_ENV_HEALTH_TIMEOUT_SECONDS; attempt++ )); do
        curl -fsS -o /dev/null "http://127.0.0.1:${port}/health" 2>/dev/null && return 0
        sleep 1
    done
    return 1
}
