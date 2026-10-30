#!/usr/bin/env bash
# zai-reset-weekly.sh — one pass of the z.ai weekly-quota watchdog (run every 5 minutes by the
# llm-env-zai-reset systemd --user timer, see setup/enable-omniroute-zai-reset.sh). When an active
# z.ai connection's weekly quota reaches 99 % it redeems one WEEK reset card through OmniRoute, at
# most once per weekly window. Logic and safety notes: pylib/zai_reset.py. Pass --dry-run to only
# report what would be redeemed. Prints one JSON document; exit 1 if a redeem failed (retried next run).
set -euo pipefail
# shellcheck disable=SC1091 # Resolved from this script at runtime.
# shellcheck source=../tools/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/../tools/lib.sh"

require_cmd uv

[ -f "$CONFIG_PATH" ] || die "no config at ${CONFIG_PATH}; run 'make setup' first"
state_file="${LLM_ENV_ZAI_RESET_STATE_FILE:-${HOME}/.cache/llm-env/zai-reset-state.json}"
mkdir -p "$(dirname "$state_file")"
unset COMPOSE_FILE COMPOSE_INSPECT_DIR   # the llmenv CLI rejects every COMPOSE_* variable
llmenv --config "$CONFIG_PATH" omniroute zai-reset --state-file "$state_file" "$@"
