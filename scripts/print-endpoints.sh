#!/usr/bin/env bash
# print-endpoints.sh — print llm-server/OmniRoute/remote-setup endpoints,
# credentials, and the remote-agent-setup one-liners (domain + LAN IP
# forms). Shared by setup/network.sh (after `make start`) and
# scripts/status.sh, so both stay in sync with a single source of truth.
set -euo pipefail
# shellcheck disable=SC1091 # Resolved from this script at runtime.
# shellcheck source=../tools/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/../tools/lib.sh"

require_cmd ip jq yq

[ -f "$CONFIG_PATH" ] || die "no config at ${CONFIG_PATH}; run 'make setup' first"

load_server_config
# shellcheck disable=SC2153 # PORT is set by load_server_config() in ../tools/lib.sh.
port="$PORT"
mdns="$(yq -r '.server.mdns_name' "$CONFIG_PATH")"
omniroute_port="$(yq -r '.omniroute.port' "$CONFIG_PATH")"
remote_setup_port="$(yq -r '.remote_setup.port' "$CONFIG_PATH")"
# Direct read + string comparison, not `.llm_server.enabled // true` --
# yq/jq's `//` alternative operator treats a real `false` as falsy too, so
# that pattern would silently collapse an explicit `false` back to `true`.
llm_server_enabled="$(yq -r '.llm_server.enabled' "$CONFIG_PATH")"
[ "$llm_server_enabled" = "false" ] || llm_server_enabled="true"

ip="$(ip -4 -json addr show scope global 2>/dev/null \
      | jq -r '[.[].addr_info[].local] | first // "unknown"')"
api_key="$(yq -r '.server.api_key' "$CONFIG_PATH")"
omniroute_password="$(yq -r '.omniroute.initial_password' "$CONFIG_PATH")"

if [ "$llm_server_enabled" = "true" ]; then
    echo
    echo "  ${BOLD}llm-server${NC}"
    echo "  Local:   http://127.0.0.1:${port}/v1"
    echo "  Network: http://${ip}:${port}/v1"
    echo "  mDNS:    http://${mdns}.local:${port}/v1"
    log_warn "some browsers (e.g. Firefox with DNS-over-HTTPS on) fail to resolve .local mDNS names and report \"Server Not Found\" even though the server is up -- use the Local/Network address above instead, or disable DNS-over-HTTPS"
    echo "  API key: ${BOLD}${api_key}${NC}"
fi
echo
echo "  ${BOLD}OmniRoute${NC}"
echo "  Local:   http://127.0.0.1:${omniroute_port}"
echo "  Network: http://${ip}:${omniroute_port}"
echo "  mDNS:    http://${mdns}.local:${omniroute_port}"
echo "  Password: ${BOLD}${omniroute_password}${NC}"
echo
echo "  ${BOLD}Remote agent setup${NC}"
echo "  Local:   http://127.0.0.1:${remote_setup_port}"
echo "  Network: http://${ip}:${remote_setup_port}"
echo "  mDNS:    http://${mdns}.local:${remote_setup_port}"
echo "  On another machine on this network, run one of:"
echo "  curl http://${mdns}.local:${remote_setup_port}/setup.sh | bash"
echo "  curl http://${ip}:${remote_setup_port}/setup.sh | bash"
echo "  (add ' -s -- --rm-key' before the final 'bash' to force a fresh"
echo "  master key prompt without caching it)"
echo "  (it will prompt for the OMNI_ROUTER_MASTER_KEY from this repo's .env;"
echo "  it also hands back the OmniRoute dashboard password shown above)"
if [ "$llm_server_enabled" = "true" ]; then
    echo
    echo "  Models:"
    yq -r '.models[] | select(.enabled) | "    - " + .alias' "$CONFIG_PATH"
fi
