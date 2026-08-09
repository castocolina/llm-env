#!/usr/bin/env bash
# show-secrets.sh — print the locally-generated credentials.
#
# These only ever protect requests to localhost/the compose network, never a
# remote endpoint, so there is no reason to keep them out of stdout.
set -euo pipefail
# shellcheck disable=SC1091 # Resolved from this script at runtime.
# shellcheck source=../tools/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/../tools/lib.sh"

require_cmd yq

[ -f "$CONFIG_PATH" ] || die "no config at ${CONFIG_PATH}; run 'make setup' first"

printf 'llm-server API key:         %s\n' "$(yq -r '.server.api_key // "(not set)"' "$CONFIG_PATH")"
printf 'OmniRoute dashboard password: %s\n' "$(yq -r '.omniroute.initial_password // "(not set)"' "$CONFIG_PATH")"
