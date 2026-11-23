#!/usr/bin/env bash
# firewall.sh — make firewall: open the LAN ports of the enabled services in firewalld, from configuration.
# Ports come from models.yml (llm-server, OmniRoute, remote-setup, System One with listen: lan); the policy comes from
# the profile's network.firewall (manage, allowed_cidrs). Queries first, adds only what is missing (runtime and
# permanent), removes nothing, prints every command it runs. --dry-run (or LLM_ENV_FIREWALL_DRY_RUN=1) only prints.
# shortcut: always the default zone; add a profile zone key if the LAN interface ever sits in another zone.
set -euo pipefail
# shellcheck disable=SC1091 # Resolved from this script at runtime.
# shellcheck source=../tools/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/../tools/lib.sh"

dry_run="${LLM_ENV_FIREWALL_DRY_RUN:-0}"
case "${1:-}" in
    --dry-run) dry_run=1 ;;
    "") ;;
    *) die "usage: firewall.sh [--dry-run]" ;;
esac

require_cmd yq
if [ ! -f "$PROFILE_PATH" ]; then
    log_info "no profile at ${PROFILE_PATH}; the firewall is not managed"
    exit 0
fi
if [ "$(yq -r '.network.firewall.manage // false' "$PROFILE_PATH")" != "true" ]; then
    log_info "network.firewall.manage is false in ${PROFILE_PATH}; nothing to do"
    exit 0
fi
[ -f "$CONFIG_PATH" ] || die "no config at ${CONFIG_PATH}; run 'make setup' first"
if ! command -v firewall-cmd >/dev/null 2>&1; then
    log_info "firewall-cmd is not installed; nothing to do"
    exit 0
fi
if ! firewall-cmd --state >/dev/null 2>&1; then
    log_info "firewalld is not running; nothing to do"
    exit 0
fi

ports=()
[ "$(yq -r '.llm_server.enabled' "$CONFIG_PATH")" = "true" ] && ports+=("$(yq -r '.server.port' "$CONFIG_PATH")")
ports+=("$(yq -r '.omniroute.port' "$CONFIG_PATH")" "$(yq -r '.remote_setup.port' "$CONFIG_PATH")")
if [ "$(yq -r '.system_one.enabled' "$CONFIG_PATH")" = "true" ] && [ "$(yq -r '.system_one.listen' "$CONFIG_PATH")" = "lan" ]; then
    ports+=("$(yq -r '.system_one.port' "$CONFIG_PATH")")
fi
mapfile -t cidrs < <(yq -r '.network.firewall.allowed_cidrs // [] | .[]' "$PROFILE_PATH")
# Values are interpolated into firewall-cmd arguments: accept only digits and CIDR characters.
for port in "${ports[@]}"; do [[ "$port" =~ ^[0-9]+$ ]] || die "not a port number: ${port}"; done
for cidr in "${cidrs[@]}"; do [[ "$cidr" =~ ^[0-9A-Fa-f:.]+/[0-9]+$ ]] || die "not a CIDR: ${cidr}"; done

scopes=() adds=()
need() {  # need SCOPE QUERY ADD: queue ADD when QUERY says the rule is absent
    if ! firewall-cmd ${1:+"$1"} "$2" >/dev/null 2>&1; then
        scopes+=("$1")
        adds+=("$3")
    fi
}
for port in "${ports[@]}"; do
    if [ "${#cidrs[@]}" -eq 0 ]; then
        for scope in "" --permanent; do need "$scope" "--query-port=${port}/tcp" "--add-port=${port}/tcp"; done
        continue
    fi
    if firewall-cmd --query-port="${port}/tcp" >/dev/null 2>&1; then
        log_warn "port ${port}/tcp is open to every source in the zone (a rule this script did not add and will not remove); allowed_cidrs has no effect for it until you remove that rule yourself"
    fi
    for cidr in "${cidrs[@]}"; do
        family=ipv4
        [[ "$cidr" == *:* ]] && family=ipv6
        rule="rule family=\"${family}\" source address=\"${cidr}\" port port=\"${port}\" protocol=\"tcp\" accept"
        for scope in "" --permanent; do need "$scope" "--query-rich-rule=${rule}" "--add-rich-rule=${rule}"; done
    done
done

if [ "${#adds[@]}" -eq 0 ]; then
    log_info "firewalld already allows ${ports[*]}/tcp${cidrs[0]:+ from ${cidrs[*]}}; nothing to do"
    exit 0
fi

runner=(sudo -n)
if [ "$(id -u)" = 0 ]; then
    runner=()
elif [ -t 0 ]; then
    runner=(sudo)  # a terminal may answer the password prompt
fi
print_commands() {
    local i
    for i in "${!adds[@]}"; do
        printf '%s %s\n' "$1" "$(printf '%q ' "${runner[@]}" firewall-cmd ${scopes[i]:+"${scopes[i]}"} "${adds[i]}")"
    done
}
if [ "$dry_run" = "1" ]; then
    print_commands "would run:"
    exit 0
fi
if [ "${runner[*]}" = "sudo -n" ] && ! sudo -n true 2>/dev/null; then
    print_commands "needs:"
    die "firewall: sudo needs a password and this run has no terminal; run 'make firewall' in a terminal, or the commands above as root"
fi
for i in "${!adds[@]}"; do
    command=("${runner[@]}" firewall-cmd ${scopes[i]:+"${scopes[i]}"} "${adds[i]}")
    printf 'run: %s\n' "$(printf '%q ' "${command[@]}")"
    "${command[@]}" >/dev/null || die "firewall-cmd failed: $(printf '%q ' "${command[@]}")"
done
log_info "firewall: added ${#adds[@]} rule(s) for ${ports[*]}/tcp"
