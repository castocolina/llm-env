#!/usr/bin/env bash
# lib.sh — shared helpers. Source, do not execute.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_PATH="${LLM_ENV_CONFIG:-${HOME}/.config/llm-env/models.yml}"
MODELS_DIR="${LLM_ENV_MODELS_DIR:-${HOME}/llm-workspace/models}"
UNIT_NAME="llm-server"
QUADLET_DIR="${HOME}/.config/containers/systemd"

# Exported so scripts that source this file expose them to child processes, and so
# the linter does not flag them as unused (SC2034) in this library.
export REPO_DIR CONFIG_PATH MODELS_DIR UNIT_NAME QUADLET_DIR

GREEN=$'\033[0;32m'; YELLOW=$'\033[1;33m'; BLUE=$'\033[0;34m'
RED=$'\033[0;31m'; NC=$'\033[0m'

log_step()  { printf '%s==>%s %s\n' "$BLUE"   "$NC" "$*"; }
log_info()  { printf '%s  ok%s %s\n' "$GREEN" "$NC" "$*"; }
log_warn()  { printf '%swarn%s %s\n' "$YELLOW" "$NC" "$*" >&2; }
log_error() { printf '%s fail%s %s\n' "$RED"  "$NC" "$*" >&2; }

_redact_stream() {
    local key escaped
    key="$(yq -r '.server.api_key // ""' "$CONFIG_PATH" 2>/dev/null)"
    if [ -n "$key" ]; then
        escaped="$(printf '%s' "$key" | sed 's/[][\\.^$*\/]/\\&/g')"
        sed "s/${escaped}/<redacted>/g"
    else
        cat
    fi | sed -E 's/(Authorization:[[:space:]]*Bearer)[[:space:]]+[^[:space:]"'"'"']+/\1 <redacted>/g'
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

prepare_diagnostic_dir() {
    local name="$1" directory
    directory="$(mktemp -d "${TMPDIR:-/tmp}/llm-env-${name}.XXXXXX")" \
        || die "could not create private diagnostic directory"
    chmod 700 "$directory"
    printf '%s\n' "$directory"
}

finish_diagnostic_dir() {
    local directory="$1" file temporary_file
    if [ "${LLM_ENV_KEEP_CHECK_ARTIFACTS:-}" != "1" ]; then
        rm -rf "$directory"
        return
    fi

    if ! chmod 700 "$directory"; then
        rm -rf "$directory"
        die "could not secure diagnostic directory"
    fi

    while IFS= read -r -d '' file; do
        if ! temporary_file="$(mktemp "${file}.XXXXXX")"; then
            rm -rf "$directory"
            die "could not prepare diagnostic artifact"
        fi
        if ! _redact_stream < "$file" > "$temporary_file"; then
            rm -f "$temporary_file"
            rm -rf "$directory"
            die "could not redact diagnostic artifact"
        fi
        if ! mv "$temporary_file" "$file"; then
            rm -f "$temporary_file"
            rm -rf "$directory"
            die "could not retain diagnostic artifact"
        fi
    done < <(find "$directory" -type f -print0)

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

reset_api_key() {
    local api_key
    api_key="$(new_api_key)"
    chmod 600 "$CONFIG_PATH"
    API_KEY="$api_key" yq -i '.server.api_key = strenv(API_KEY)' "$CONFIG_PATH"
    chmod 600 "$CONFIG_PATH"
}
