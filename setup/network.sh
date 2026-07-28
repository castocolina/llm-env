#!/usr/bin/env bash
# network.sh — configure optional LAN access after the server is healthy.
set -euo pipefail
# shellcheck disable=SC1091 # Resolved from this script at runtime.
# shellcheck source=../tools/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/../tools/lib.sh"

require_cmd ip jq yq

port="$(yq -r '.server.port' "$CONFIG_PATH")"
mdns="$(yq -r '.server.mdns_name' "$CONFIG_PATH")"

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
fi

if command -v avahi-publish >/dev/null 2>&1; then
    log_info "mDNS is managed by ${UNIT_NAME}-mdns.service"
fi

ip="$(ip -4 -json addr show scope global 2>/dev/null \
      | jq -r '[.[].addr_info[].local] | first // "unknown"')"
echo
echo "  Local:   http://127.0.0.1:${port}/v1"
echo "  Network: http://${ip}:${port}/v1"
echo "  mDNS:    http://${mdns}.local:${port}/v1"
echo "  API key: read it with  yq -r '.server.api_key' ${CONFIG_PATH}"
echo
echo "  Models:"
yq -r '.models[] | select(.enabled) | "    - " + .alias' "$CONFIG_PATH"
