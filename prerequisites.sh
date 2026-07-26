#!/usr/bin/env bash
# prerequisites.sh — detect and optionally install Bazzite/Fedora host tools.
set -euo pipefail
# shellcheck source=lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

RUNTIME=("uv:uv" "jq:jq" "yq:yq" "podman:podman" "curl:curl" "ip:iproute")
DEVELOPMENT=("git:git" "shellcheck:ShellCheck")
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
    command -v "$command" >/dev/null 2>&1 || return 1
    if [ "$command" = "yq" ]; then
        local version
        version="$(yq --version 2>/dev/null)"
        [[ "$version" == *"github.com/mikefarah/yq/"* && "$version" == *"version v4."* ]] || return 1
    fi
}

check_group() {
    local purpose="$1" category="$2" item command package description
    shift 2
    for item in "$@"; do
        command="${item%%:*}"
        package="${item#*:}"
        description="$purpose"
        if [ "$command" = "yq" ]; then
            description="Mike Farah yq v4 configuration processor"
        fi
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
check_group "required to run llm-env" runtime "${RUNTIME[@]}"
check_group "development validation tool" development "${DEVELOPMENT[@]}"
check_group "optional LAN access tool" optional "${OPTIONAL_LAN[@]}"

if [ "$CHECK_ONLY" -eq 1 ]; then
    if [ "${#missing_runtime[@]}" -eq 0 ]; then
        exit 0
    fi
    exit 1
fi

install_packages() {
    local prompt="$1"
    shift
    local reply
    printf 'sudo rpm-ostree install'
    printf ' %s' "$@"
    printf '\n'
    read -rp "$prompt" reply
    if [ "$reply" = "yes" ]; then
        sudo rpm-ostree install "$@"
        log_info "package transaction completed; reboot is required before rerunning setup"
        return 0
    fi
    return 1
}

if [ "${#missing_packages[@]}" -gt 0 ]; then
    if ! install_packages "Install these packages? (yes/no) " "${missing_packages[@]}"; then
        exit 1
    fi
fi

if [ "${#missing_optional[@]}" -gt 0 ]; then
    install_packages "Install optional LAN tools? (yes/no) " "${missing_optional[@]}" || true
fi
