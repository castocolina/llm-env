#!/usr/bin/env bash
# network.sh — configure optional LAN access after the server is healthy.
set -euo pipefail
# shellcheck disable=SC1091 # Resolved from this script at runtime.
# shellcheck source=../tools/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/../tools/lib.sh"

require_cmd ip jq yq

load_server_config
migrate_config_file || die "configuration migration failed"
# shellcheck disable=SC2153 # PORT is set by load_server_config() in ../tools/lib.sh.
port="$PORT"
omniroute_port="$(yq -r '.omniroute.port' "$CONFIG_PATH")"
remote_setup_port="$(yq -r '.remote_setup.port' "$CONFIG_PATH")"
llm_server_enabled="$(yq -r '.llm_server.enabled' "$CONFIG_PATH")"

offer_firewall_port() {
    local p="$1" open_port
    if firewall-cmd --query-port="${p}/tcp" >/dev/null 2>&1; then
        log_info "firewall port ${p}/tcp already open"
    elif [ -t 0 ]; then
        read -rp "  Open firewall port ${p}/tcp for LAN access? (yes/no) " open_port
        if [ "$open_port" = "yes" ]; then
            sudo firewall-cmd --permanent --add-port="${p}/tcp" >/dev/null
            sudo firewall-cmd --reload >/dev/null
            log_info "opened firewall port ${p}/tcp"
        fi
    else
        log_info "firewall port ${p}/tcp remains closed; LAN access is disabled"
    fi
}

if command -v firewall-cmd >/dev/null 2>&1; then
    if [ "$llm_server_enabled" = "true" ]; then
        offer_firewall_port "$port"
    fi
    # System One (Laya) is LAN-published only with system_one.listen: lan.
    if [ "$(yq -r '.system_one.enabled' "$CONFIG_PATH")" = "true" ] && [ "$(yq -r '.system_one.listen' "$CONFIG_PATH")" = "lan" ]; then
        offer_firewall_port "$(yq -r '.system_one.port' "$CONFIG_PATH")"
    fi
    log_warn "firewalld rules for OmniRoute (port ${omniroute_port}/tcp) and the remote-setup installer (port ${remote_setup_port}/tcp) are not opened automatically -- if this host has firewalld enabled and you want either reachable from other machines on the LAN, run: sudo firewall-cmd --permanent --add-port=${omniroute_port}/tcp --add-port=${remote_setup_port}/tcp && sudo firewall-cmd --reload"
fi

if command -v avahi-publish >/dev/null 2>&1; then
    log_info "mDNS is managed by ${UNIT_NAME}-mdns.service"
fi

exec "$(dirname "${BASH_SOURCE[0]}")/../scripts/print-endpoints.sh"
