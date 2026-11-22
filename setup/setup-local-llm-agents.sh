#!/usr/bin/env bash
set -euo pipefail
umask 077
# shellcheck disable=SC1091 # Resolved from this script at runtime.
# shellcheck source=../tools/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/../tools/lib.sh"

log_warn "close Pi and OpenCode before continuing"
require_cmd uv curl jq yq node cmp
[ -f "$CONFIG_PATH" ] || die "no config at ${CONFIG_PATH}; run 'make setup' first"
migrate_config_file || die "configuration migration failed"

omniroute_port="$(yq -r '.omniroute.port // ""' "$CONFIG_PATH")"
[ -n "$omniroute_port" ] || die "omniroute.port is not set; run 'make setup' first"
key_response="$(llmenv omniroute issue-key --config "$CONFIG_PATH")" \
    || die "could not obtain an OmniRoute API key; run 'make start' first"
api_key="$(jq -r '.api_key // ""' <<<"$key_response")"
[ -n "$api_key" ] || die "OmniRoute did not return a usable API key"

# The model list is OmniRoute's combos as they are right now, asked of its API
# on every run (pylib/omniroute.py::client_models, shared with remote-setup's
# /config). Direct llama-cpp/<alias> models are not listed: the local model is
# reached through the combo that wraps it, which stays only while llm-server
# answers its /health.
client_models_response="$(llmenv omniroute client-models --config "$CONFIG_PATH")" \
    || die "$(jq -r '.error // "could not fetch OmniRoute combos"' <<<"$client_models_response")"
models_json="$(jq -c '.models' <<<"$client_models_response")"

jq -ne --arg port "$omniroute_port" '$port | test("^[1-9][0-9]{0,4}$") and (tonumber <= 65535)' >/dev/null \
    || die "omniroute port must be an integer from 1 to 65535"

base_url="http://127.0.0.1:${omniroute_port}/v1"

# shellcheck disable=SC1091 # Resolved from this script at runtime.
# shellcheck source=lib/install-agent-clients.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/install-agent-clients.sh"

create_agent_client_workdir

# shellcheck disable=SC2154 # workdir is set by create_agent_client_workdir above
api_key_file="${workdir}/api-key"
printf '%s' "$api_key" >"$api_key_file" || die "could not write private API key"
chmod 600 "$api_key_file" || die "could not secure private API key"
models_file="${workdir}/models.json"
printf '%s\n' "$models_json" >"$models_file" || die "could not write enabled models"
chmod 600 "$models_file" || die "could not secure enabled models"

# Validate local inputs (mapped models, any existing OpenCode model-state
# file) before the OmniRoute health probe below, so a malformed local
# opencode state file fails fast with zero network calls -- matching this
# script's historical ordering, before it was rewired onto the shared
# install-agent-clients.sh library.
validate_agent_client_local_state \
    "$models_file" "${REPO_DIR}/setup/update-opencode-config.mjs" "true"

# Pi/OpenCode are pointed at OmniRoute (base_url above), not llm-server
# directly, so readiness must be checked against OmniRoute's own health
# endpoint -- checking llm-server's port here would wrongly fail whenever
# llm_server.enabled is false (OmniRoute-only mode), even though OmniRoute
# itself is healthy and this script has nothing to do with llm-server.
curl -fsS --max-time 5 -o /dev/null "http://127.0.0.1:${omniroute_port}/api/monitoring/health" \
    || die "OmniRoute is not healthy; run 'make start' and retry"

install_agent_clients \
    "$base_url" "$api_key_file" "$models_file" \
    "${REPO_DIR}/setup/update-opencode-config.mjs" \
    "${REPO_DIR}/setup/lib/install-agent-clients.sh" \
    "true"

bash "$(dirname "${BASH_SOURCE[0]}")/refresh-llm-env-pi-profile.sh"
