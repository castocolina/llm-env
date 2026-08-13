#!/usr/bin/env bash
# network.sh — configure optional LAN access after the server is healthy.
set -euo pipefail
# shellcheck disable=SC1091 # Resolved from this script at runtime.
# shellcheck source=../tools/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/../tools/lib.sh"

require_cmd ip jq yq

load_server_config
# shellcheck disable=SC2153 # PORT is set by load_server_config() in ../tools/lib.sh.
port="$PORT"
mdns="$(yq -r '.server.mdns_name' "$CONFIG_PATH")"
omniroute_port="$(yq -r '.omniroute.port' "$CONFIG_PATH")"
remote_setup_port="$(yq -r '.remote_setup.port' "$CONFIG_PATH")"

if command -v firewall-cmd >/dev/null 2>&1; then
    if firewall-cmd --query-port="${port}/tcp" >/dev/null 2>&1; then
        log_info "firewall port ${port}/tcp already open"
    elif [ -t 0 ]; then
        read -rp "  Open firewall port ${port}/tcp for LAN access? (yes/no) " open_port
        if [ "$open_port" = "yes" ]; then
            sudo firewall-cmd --permanent --add-port="${port}/tcp" >/dev/null
            sudo firewall-cmd --reload >/dev/null
            log_info "opened firewall port ${port}/tcp"
        fi
    else
        log_info "firewall port ${port}/tcp remains closed; LAN access is disabled"
    fi
    log_warn "firewalld rules for OmniRoute (port ${omniroute_port}/tcp) and the remote-setup installer (port ${remote_setup_port}/tcp) are not opened automatically -- if this host has firewalld enabled and you want either reachable from other machines on the LAN, run: sudo firewall-cmd --permanent --add-port=${omniroute_port}/tcp --add-port=${remote_setup_port}/tcp && sudo firewall-cmd --reload"
fi

if command -v avahi-publish >/dev/null 2>&1; then
    log_info "mDNS is managed by ${UNIT_NAME}-mdns.service"
fi

ip="$(ip -4 -json addr show scope global 2>/dev/null \
      | jq -r '[.[].addr_info[].local] | first // "unknown"')"
api_key="$(yq -r '.server.api_key' "$CONFIG_PATH")"
omniroute_password="$(yq -r '.omniroute.initial_password' "$CONFIG_PATH")"
echo
echo "  ${BOLD}llm-server${NC}"
echo "  Local:   http://127.0.0.1:${port}/v1"
echo "  Network: http://${ip}:${port}/v1"
echo "  mDNS:    http://${mdns}.local:${port}/v1"
log_warn "some browsers (e.g. Firefox with DNS-over-HTTPS on) fail to resolve .local mDNS names and report \"Server Not Found\" even though the server is up -- use the Local/Network address above instead, or disable DNS-over-HTTPS"
echo "  API key: ${BOLD}${api_key}${NC}"
echo
echo "  ${BOLD}OmniRoute${NC}"
echo "  Local:   http://127.0.0.1:${omniroute_port}"
echo "  Network: http://${ip}:${omniroute_port}"
echo "  mDNS:    http://${mdns}.local:${omniroute_port}"
echo "  Password: ${BOLD}${omniroute_password}${NC}"
echo
echo "  ${BOLD}Remote agent setup${NC}"
echo "  On another machine on this network, run:"
echo "  curl http://${ip}:${remote_setup_port}/setup.sh | bash"
echo "  (it will prompt for the OMNI_ROUTER_MASTER_KEY from this repo's .env)"
echo
echo "  Models:"
yq -r '.models[] | select(.enabled) | "    - " + .alias' "$CONFIG_PATH"
