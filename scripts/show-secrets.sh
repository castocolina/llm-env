#!/usr/bin/env bash
# show-secrets.sh — print the locally-generated credentials.
#
# llm-server and OmniRoute both publish LAN-reachable ports (no
# 127.0.0.1-only binding on either), and OMNI_ROUTER_MASTER_KEY gates an
# endpoint that hands out real OmniRoute API keys to any LAN machine that
# has it -- these are not localhost-only secrets. Still printed here since
# this command is meant to be run interactively by the machine's own
# owner, not exposed remotely.
set -euo pipefail
# shellcheck disable=SC1091 # Resolved from this script at runtime.
# shellcheck source=../tools/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/../tools/lib.sh"

require_cmd yq

[ -f "$CONFIG_PATH" ] || die "no config at ${CONFIG_PATH}; run 'make setup' first"

printf 'llm-server API key:           %s\n' "$(yq -r '.server.api_key // "(not set)"' "$CONFIG_PATH")"
printf 'OmniRoute dashboard password: %s\n' "$(yq -r '.omniroute.initial_password // "(not set)"' "$CONFIG_PATH")"

env_file="${LLM_ENV_ENV_FILE:-${REPO_DIR}/.env}"
master_key="(not set)"
if [ -f "$env_file" ]; then
    line="$(grep -m1 '^OMNI_ROUTER_MASTER_KEY=' "$env_file" || true)"
    [ -n "$line" ] && master_key="${line#OMNI_ROUTER_MASTER_KEY=}"
fi
printf 'OMNI_ROUTER_MASTER_KEY:        %s\n' "$master_key"
