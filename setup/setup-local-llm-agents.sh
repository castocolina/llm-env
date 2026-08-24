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
models_json="$(yq -o=json '[.models[] | select(.enabled) | {
    "alias": .alias,
    "ctx_size": .ctx_size,
    "client_max_output_tokens": .client_max_output_tokens
}]' "$CONFIG_PATH")"

jq -ne --arg port "$omniroute_port" '$port | test("^[1-9][0-9]{0,4}$") and (tonumber <= 65535)' >/dev/null \
    || die "omniroute port must be an integer from 1 to 65535"
jq -e '
    type == "array" and length > 0 and
    ([.[].alias] | length == (unique | length)) and
    all(.[];
        (.alias | type == "string" and length > 0) and
        (.ctx_size | type == "number" and . > 0 and floor == .) and
        (.client_max_output_tokens | type == "number" and . > 0 and floor == .) and
        .client_max_output_tokens <= .ctx_size)
' <<<"$models_json" >/dev/null \
    || die "enabled model records require unique aliases and valid context/output limits"

base_url="http://127.0.0.1:${omniroute_port}/v1"

workdir="$(mktemp -d)" || die "could not create private configuration workspace"
chmod 700 "$workdir" || {
    rm -rf -- "$workdir"
    die "could not secure private configuration workspace"
}
cleanup_workspace() {
    rm -rf -- "$workdir"
}
trap cleanup_workspace EXIT

api_key_file="${workdir}/api-key"
models_file="${workdir}/models.json"
pi_provider="${workdir}/pi-provider.json"
opencode_provider="${workdir}/opencode-provider.json"
printf '%s' "$api_key" >"$api_key_file" || die "could not write private API key"
chmod 600 "$api_key_file" || die "could not secure private API key"
printf '%s\n' "$models_json" >"$models_file" || die "could not write enabled models"
chmod 600 "$models_file" || die "could not secure enabled models"
models_state_file="${workdir}/models-state.json"
jq '[.[] | .alias = "llama-cpp/\(.alias)"]' "$models_file" >"$models_state_file" \
    || die "could not build prefixed model ids for OpenCode model state"
chmod 600 "$models_state_file" || die "could not secure prefixed model ids"

jq -n \
    --arg base_url "$base_url" \
    --rawfile api_key "$api_key_file" \
    --slurpfile models "$models_file" \
    '($models[0]) as $models | {
        baseUrl: $base_url,
        api: "openai-completions",
        apiKey: ($api_key | rtrimstr("\n")),
        compat: {supportsDeveloperRole: false, supportsReasoningEffort: false},
        models: [$models[] | {
            id: "llama-cpp/\(.alias)",
            contextWindow: .ctx_size,
            maxTokens: .client_max_output_tokens
        }]
    }' >"$pi_provider" || die "could not build Pi provider"
chmod 600 "$pi_provider" || die "could not secure Pi provider"

jq -n \
    --arg base_url "$base_url" \
    --rawfile api_key "$api_key_file" \
    --slurpfile models "$models_file" \
    '($models[0]) as $models | {
        npm: "@ai-sdk/openai-compatible",
        name: "local-llm-env",
        options: {baseURL: $base_url, apiKey: ($api_key | rtrimstr("\n"))},
        models: (reduce $models[] as $model ({};
            .["llama-cpp/\($model.alias)"] = {
                name: $model.alias,
                limit: {
                    context: $model.ctx_size,
                    output: $model.client_max_output_tokens
                }
            }))
    }' >"$opencode_provider" || die "could not build OpenCode provider"
chmod 600 "$opencode_provider" || die "could not secure OpenCode provider"

ensure_private_dir() {
    if [ -e "$1" ]; then
        [ -d "$1" ] || die "configuration directory path is not a directory"
    else
        mkdir -p "$1" || die "could not create configuration directory"
    fi
    chmod 700 "$1" || die "could not secure configuration directory"
}

prepare_staged_file() {
    local directory="$1" filename="$2"
    ensure_private_dir "$directory"
    STAGED_FILE="$(mktemp "${directory}/.${filename}.XXXXXX")" \
        || die "could not stage ${filename}"
    chmod 600 "$STAGED_FILE" || {
        rm -f -- "$STAGED_FILE"
        die "could not secure staged ${filename}"
    }
    staged_files+=("$STAGED_FILE")
}

stage_pi() {
    local source="$1" provider="$2" staged="$3"
    jq -s '
      if length != 2 then error("Pi configuration must contain one JSON object")
      elif (.[0] | type) != "object" then error("Pi configuration must be an object")
      elif (.[1] | type) != "object" then error("Pi provider must be an object")
      else
        .[0] as $config | .[1] as $provider |
        ($config.providers // {}) as $providers |
        if ($providers | type) != "object" then error("Pi providers must be an object") else
          $config | .providers = ($providers + {"local-llm-env": $provider})
        end
      end
    ' "$source" "$provider" >"$staged"
}

stage_pi_settings() {
    local source="$1" models="$2" staged="$3"
    jq --slurpfile models "$models" '
        if type != "object" then error("Pi settings must be an object") else
          .enabledModels = [$models[0][] | "local-llm-env/llama-cpp/\(.alias)"]
        end
    ' "$source" >"$staged"
}

stage_opencode() {
    node "${REPO_DIR}/setup/update-opencode-config.mjs" \
        --replace-provider "$1" "$2" "$3" || die "could not update OpenCode configuration"
}

pi_dir="${PI_CODING_AGENT_DIR:-$HOME/.pi/agent}"
pi_path="${pi_dir}/models.json"
pi_settings_path="${pi_dir}/settings.json"
opencode_dir="${XDG_CONFIG_HOME:-$HOME/.config}/opencode"
opencode_state_dir="${XDG_STATE_HOME:-$HOME/.local/state}/opencode"
opencode_state_path="${opencode_state_dir}/model.json"
opencode_candidates=(
    "${opencode_dir}/config.json"
    "${opencode_dir}/opencode.json"
    "${opencode_dir}/opencode.jsonc"
)
staged_files=()
temporary_sources=()

cleanup() {
    local status=$? path
    for path in "${staged_files[@]}" "${temporary_sources[@]}"; do
        [ -n "$path" ] && rm -f -- "$path"
    done
    rm -rf -- "$workdir"
    exit "$status"
}
trap cleanup EXIT

if [ -e "$pi_path" ]; then
    [ -f "$pi_path" ] || die "Pi configuration is not a regular file: ${pi_path}"
    jq -e -s '
        if length != 1 or (.[0] | type) != "object" then false
        else ((.[0].providers // {}) | type) == "object"
        end
    ' "$pi_path" >/dev/null \
        || die "Pi configuration must contain exactly one JSON object: ${pi_path}"
    pi_source="$pi_path"
else
    pi_source="${workdir}/empty-pi.json"
fi

if [ -e "$pi_settings_path" ]; then
    [ -f "$pi_settings_path" ] \
        || die "Pi settings are not a regular file: ${pi_settings_path}"
    jq -e -s 'length == 1 and (.[0] | type) == "object"' \
        "$pi_settings_path" >/dev/null \
        || die "Pi settings must contain exactly one JSON object: ${pi_settings_path}"
    pi_settings_source="$pi_settings_path"
else
    pi_settings_source="${workdir}/empty-pi-settings.json"
fi

opencode_targets=()
opencode_sources=()
for candidate in "${opencode_candidates[@]}"; do
    [ ! -e "$candidate" ] && continue
    [ -f "$candidate" ] || die "OpenCode configuration is not a regular file: ${candidate}"
    if node "${REPO_DIR}/setup/update-opencode-config.mjs" --contains-provider "$candidate"; then
        status=0
    else
        status=$?
    fi
    case "$status" in
        0)
            opencode_targets+=("$candidate")
            opencode_sources+=("$candidate")
            ;;
        1)
            ;;
        *)
            die "could not validate OpenCode configuration: ${candidate}"
            ;;
    esac
done

if [ "${#opencode_targets[@]}" -eq 0 ]; then
    for index in 2 1 0; do
        candidate="${opencode_candidates[$index]}"
        if [ -e "$candidate" ]; then
            opencode_targets+=("$candidate")
            opencode_sources+=("$candidate")
            break
        fi
    done
fi

if [ "${#opencode_targets[@]}" -eq 0 ]; then
    opencode_source="${workdir}/empty-opencode.jsonc"
    printf '{}\n' >"$opencode_source" || die "could not prepare empty OpenCode configuration"
    chmod 600 "$opencode_source" || die "could not secure empty OpenCode configuration"
    opencode_targets+=("${opencode_candidates[2]}")
    opencode_sources+=("$opencode_source")
fi

if [ -e "$opencode_state_path" ]; then
    [ -f "$opencode_state_path" ] \
        || die "OpenCode model state is not a regular file: ${opencode_state_path}"
    opencode_state_source="$opencode_state_path"
else
    require_cmd opencode
    [ "$(opencode --version)" = "1.18.10" ] \
        || die "cannot create OpenCode model state for an unsupported version"
    debug_paths="$(opencode debug paths)" \
        || die "could not query OpenCode state path"
    debug_state=""
    while read -r label value; do
        [ "$label" = state ] && debug_state="$value"
    done <<<"$debug_paths"
    [ "$debug_state" = "$opencode_state_dir" ] \
        || die "OpenCode debug state path does not match the target directory"
    opencode_state_source="${workdir}/empty-opencode-model.json"
    printf '%s\n' '{"recent":[],"favorite":[],"variant":{}}' \
        >"$opencode_state_source" \
        || die "could not prepare empty OpenCode model state"
    chmod 600 "$opencode_state_source" \
        || die "could not secure empty OpenCode model state"
fi

node "${REPO_DIR}/setup/update-opencode-config.mjs" \
    --validate-model-state "$opencode_state_source" \
    || die "incompatible OpenCode model state: ${opencode_state_source}"

# Pi/OpenCode are pointed at OmniRoute (base_url above), not llm-server
# directly, so readiness must be checked against OmniRoute's own health
# endpoint -- checking llm-server's port here would wrongly fail whenever
# llm_server.enabled is false (OmniRoute-only mode), even though OmniRoute
# itself is healthy and this script has nothing to do with llm-server.
curl -fsS --max-time 5 -o /dev/null "http://127.0.0.1:${omniroute_port}/api/monitoring/health" \
    || die "OmniRoute is not healthy; run 'make start' and retry"

if [ ! -e "$pi_path" ]; then
    printf '{}\n' >"$pi_source" || die "could not prepare empty Pi configuration"
    chmod 600 "$pi_source" || die "could not secure empty Pi configuration"
fi
if [ ! -e "$pi_settings_path" ]; then
    printf '{}\n' >"$pi_settings_source" || die "could not prepare empty Pi settings"
    chmod 600 "$pi_settings_source" || die "could not secure empty Pi settings"
fi

ensure_private_dir "$pi_dir"
ensure_private_dir "$opencode_dir"

prepare_staged_file "$pi_dir" "models.json"
pi_staged="$STAGED_FILE"
stage_pi "$pi_source" "$pi_provider" "$pi_staged" \
    || die "could not update Pi configuration"
jq -e '.providers["local-llm-env"] | type == "object"' "$pi_staged" >/dev/null \
    || die "could not validate staged Pi configuration"
chmod 600 "$pi_staged" || die "could not secure staged Pi configuration"

prepare_staged_file "$pi_dir" "settings.json"
pi_settings_staged="$STAGED_FILE"
stage_pi_settings "$pi_settings_source" "$models_file" "$pi_settings_staged" \
    || die "could not update Pi settings"
jq -e '.enabledModels | type == "array" and all(.[]; type == "string")' \
    "$pi_settings_staged" >/dev/null || die "could not validate staged Pi settings"
chmod 600 "$pi_settings_staged" || die "could not secure staged Pi settings"

opencode_staged=()
for index in "${!opencode_targets[@]}"; do
    opencode_target="${opencode_targets[$index]}"
    opencode_source="${opencode_sources[$index]}"
    filename="${opencode_target##*/}"
    prepare_staged_file "$opencode_dir" "$filename"
    opencode_staged+=("$STAGED_FILE")
    stage_opencode "$opencode_source" "$opencode_provider" "$STAGED_FILE"
    node "${REPO_DIR}/setup/update-opencode-config.mjs" \
        --contains-provider "$STAGED_FILE" \
        || die "could not validate staged OpenCode configuration"
    chmod 600 "$STAGED_FILE" || die "could not secure staged ${filename}"
done

prepare_staged_file "$opencode_state_dir" "model.json"
opencode_state_staged="$STAGED_FILE"
node "${REPO_DIR}/setup/update-opencode-config.mjs" \
    --update-model-state "$opencode_state_source" "$models_state_file" "$opencode_state_staged" \
    || die "could not update OpenCode model state"
state_check="${workdir}/checked-opencode-model.json"
node "${REPO_DIR}/setup/update-opencode-config.mjs" \
    --update-model-state "$opencode_state_staged" "$models_state_file" "$state_check" \
    || die "could not validate staged OpenCode model state"
cmp -s "$opencode_state_staged" "$state_check" \
    || die "staged OpenCode model state is not idempotent"
chmod 600 "$opencode_state_staged" \
    || die "could not secure staged OpenCode model state"

replacement_error="client replacement failed; close Pi and OpenCode, then rerun make setup-local-llm-agents to repair partial updates"
mv -f -- "$pi_staged" "$pi_path" || die "$replacement_error"
staged_files=("${staged_files[@]:1}")
mv -f -- "$pi_settings_staged" "$pi_settings_path" \
    || die "$replacement_error"
staged_files=("${staged_files[@]:1}")
for index in "${!opencode_targets[@]}"; do
    mv -f -- "${opencode_staged[$index]}" "${opencode_targets[$index]}" \
        || die "$replacement_error"
    staged_files=("${staged_files[@]:1}")
done
mv -f -- "$opencode_state_staged" "$opencode_state_path" \
    || die "$replacement_error"
staged_files=("${staged_files[@]:1}")

log_info "configured Pi provider: ${pi_path}"
log_info "configured Pi model cycle: ${pi_settings_path}"
for opencode_target in "${opencode_targets[@]}"; do
    log_info "configured OpenCode provider: ${opencode_target}"
done
log_info "configured OpenCode favorites: ${opencode_state_path}"
while IFS= read -r alias; do
    log_info "enabled model: ${alias}"
done < <(jq -r '.[].alias' "$models_file")
log_info "restart Pi and OpenCode to load the updated configuration"
