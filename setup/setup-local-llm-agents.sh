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

# No llama.cpp instance exists behind OmniRoute in this mode (see
# llm_server.enabled in models.yml.example): mapping "llama-cpp/<alias>"
# model ids here would create dead provider entries with nothing to route
# to. OmniRoute combos (below) are always mapped regardless -- they route
# to real, live provider connections and are the whole point of this mode.
#
# migrate_config_file (above) always normalizes llm_server.enabled to a
# real boolean, so a bare read is used here -- not `// true`, which would
# silently coerce an explicit `false` back to "true" (yq/jq's `//`
# alternative operator treats `false` as falsy, same footgun documented in
# setup.sh and print-endpoints.sh).
llm_server_enabled="$(yq -r '.llm_server.enabled' "$CONFIG_PATH")"
if [ "$llm_server_enabled" = "true" ]; then
    llama_models_json="$(yq -o=json '[.models[] | select(.enabled) | {
        "id": "llama-cpp/\(.alias)",
        "label": .alias,
        "ctx_size": .ctx_size,
        "client_max_output_tokens": .client_max_output_tokens
    }]' "$CONFIG_PATH")"
else
    llama_models_json='[]'
fi

# OmniRoute's own /v1/models catalog and /api/combos do not reflect
# manually-corrected context windows (a real OmniRoute display bug -- see
# pylib/omniroute.py::compute_combo_context for the full writeup and live
# verification), so this uses the corrected minimum per combo instead of
# trusting OmniRoute's own numbers, matching what `make combo-context`
# reports.
combo_context_response="$(llmenv omniroute combo-context --config "$CONFIG_PATH")" \
    || die "$(jq -r '.error // "could not fetch OmniRoute combos"' <<<"$combo_context_response")"
combo_models_json="$(jq '[.combos[] | select(.min_context_window != null) | {
    id: .combo,
    label: "\(.combo) (combo)",
    ctx_size: .min_context_window,
    client_max_output_tokens: (
        if .min_max_output_tokens == null then
            ([.min_context_window, 128000] | min)
        elif .min_max_output_tokens > .min_context_window then
            .min_context_window
        else
            .min_max_output_tokens
        end
    )
}]' <<<"$combo_context_response")"

models_json="$(jq -s '.[0] + .[1]' <(printf '%s' "$llama_models_json") <(printf '%s' "$combo_models_json"))"

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
