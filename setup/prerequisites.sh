#!/usr/bin/env bash
# prerequisites.sh — detect and optionally install Bazzite/Fedora host tools.
set -euo pipefail
# shellcheck disable=SC1091 # Resolved from this script at runtime.
# shellcheck source=../tools/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/../tools/lib.sh"

RUNTIME=("jq:jq" "yq:yq" "podman:podman" "podman-compose:podman-compose" "curl:curl" "ip:iproute")
DEVELOPMENT=("git:git" "shellcheck:ShellCheck" "node:nodejs")
OPTIONAL_LAN=("firewall-cmd:firewalld" "avahi-publish:avahi")

CHECK_ONLY=0
if [ "${1:-}" = "--check" ]; then
    CHECK_ONLY=1
elif [ "$#" -ne 0 ]; then
    die "usage: $0 [--check]"
fi

missing_runtime=()
missing_packages=()
missing_optional=()

command_is_usable() {
    local command="$1"
    if [ "$command" = "podman-compose" ]; then
        podman compose version >/dev/null 2>&1 || return 1
        return 0
    fi
    command -v "$command" >/dev/null 2>&1 || return 1
    if [ "$command" = "yq" ]; then
        local version
        version="$(yq --version 2>/dev/null)"
        [[ "$version" == *"github.com/mikefarah/yq/"* && "$version" == *"version v4."* ]] || return 1
    fi
}

command_purpose() {
    case "$1" in
        uv) printf '%s\n' "Python tool runner and dependency manager" ;;
        jq) printf '%s\n' "JSON processor for script-to-Python communication" ;;
        yq) printf '%s\n' "Mike Farah yq v4 configuration processor" ;;
        podman) printf '%s\n' "container engine for llama.cpp" ;;
        podman-compose) printf '%s\n' "compose provider for 'podman compose'" ;;
        curl) printf '%s\n' "HTTP client for downloads and health checks" ;;
        ip) printf '%s\n' "network address inspection" ;;
        git) printf '%s\n' "source control for updates" ;;
        shellcheck) printf '%s\n' "shell script validation" ;;
        node) printf '%s\n' "JSONC editor for OpenCode client configuration" ;;
        firewall-cmd) printf '%s\n' "firewall configuration for LAN access" ;;
        avahi-publish) printf '%s\n' "LAN service discovery" ;;
    esac
}

check_group() {
    local category="$1" item command package description
    shift
    for item in "$@"; do
        command="${item%%:*}"
        package="${item#*:}"
        description="$(command_purpose "$command")"
        if command_is_usable "$command"; then
            printf '  installed  %-16s %s\n' "$command" "$description"
        else
            printf '  missing    %-16s %-12s %s\n' "$command" "(${package})" "$description"
            case "$category" in
                runtime)
                    missing_runtime+=("$package")
                    missing_packages+=("$package")
                    ;;
                development)
                    missing_packages+=("$package")
                    ;;
                optional)
                    missing_optional+=("$package")
                    ;;
            esac
        fi
    done
}

printf 'Checking Bazzite/Fedora prerequisites:\n'

uv_missing=0
if ! command -v uv >/dev/null 2>&1; then
    uv_missing=1
    printf '  missing    %-16s %-12s %s\n' uv "(astral.sh)" "Python tool runner and dependency manager"
else
    printf '  installed  %-16s %s\n' uv "Python tool runner and dependency manager"
fi

check_group runtime "${RUNTIME[@]}"
check_group development "${DEVELOPMENT[@]}"
check_group optional "${OPTIONAL_LAN[@]}"

if [ "$CHECK_ONLY" -eq 1 ]; then
    if [ "${#missing_runtime[@]}" -eq 0 ] && [ "$uv_missing" -eq 0 ]; then
        exit 0
    fi
    exit 1
fi

# Try to make the layered packages usable without a reboot. rpm-ostree
# apply-live only patches the running deployment's /usr in place; it
# refuses (rather than silently reboot-requiring) when the staged
# deployment also carries a pending OS update bundled in alongside the
# packages just installed, since that would remove/replace packages out
# from under the running session. That case still needs an explicit yes.
apply_live_if_possible() {
    local apply_output apply_status reply
    apply_output="$(sudo rpm-ostree apply-live 2>&1)"
    apply_status=$?
    if [ "$apply_status" -eq 0 ]; then
        log_info "applied live; no reboot needed"
        return 0
    fi
    if [[ "$apply_output" == *'packages would be removed'* ]]; then
        log_warn "the staged deployment also carries a pending OS update (packages would be removed/replaced live)"
        read -rp "Apply everything live now with --allow-replacement instead of rebooting? (yes/no) " reply
        if [ "$reply" = "yes" ]; then
            sudo rpm-ostree apply-live --allow-replacement
            log_info "applied live"
            return 0
        fi
    else
        printf '%s\n' "$apply_output" >&2
    fi
    log_info "reboot is required before rerunning setup"
    return 1
}

install_packages() {
    local prompt="$1"
    shift
    local reply
    printf 'sudo rpm-ostree install'
    printf ' %s' "$@"
    printf '\n'
    read -rp "$prompt" reply
    if [ "$reply" = "yes" ]; then
        sudo rpm-ostree install "$@" || return 1
        apply_live_if_possible || true
        return 0
    fi
    return 1
}

if [ "$uv_missing" -eq 1 ]; then
    printf 'curl -LsSf https://astral.sh/uv/install.sh | sh\n'
    read -rp "Install uv via its official installer? (yes/no) " reply
    if [ "$reply" = "yes" ]; then
        curl -LsSf https://astral.sh/uv/install.sh | sh
        log_info "uv installed; ensure \$HOME/.local/bin is on PATH"
    else
        exit 1
    fi
fi

if [ "${#missing_packages[@]}" -gt 0 ]; then
    if ! install_packages "Install these packages? (yes/no) " "${missing_packages[@]}"; then
        exit 1
    fi
fi

if [ "${#missing_optional[@]}" -gt 0 ]; then
    install_packages "Install optional LAN tools? (yes/no) " "${missing_optional[@]}" || true
fi
