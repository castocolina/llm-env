#!/usr/bin/env bash
# install-agent-clients.sh -- shared logic for configuring Pi and OpenCode
# to talk to an already-provisioned OmniRoute endpoint.
#
# Sourced directly by setup/setup-local-llm-agents.sh (its own repo path)
# and embedded verbatim, via heredoc, into the remote installer generated
# by pylib/remote_setup.py::render_setup_script() -- the same technique
# already used there for update-opencode-config.mjs. Self-contained: no
# `source tools/lib.sh`, no repo-path assumptions beyond what is passed to
# install_agent_clients() -- this file must work verbatim on a remote
# machine with no copy of this repo, including macOS's stock bash 3.2
# (hence every `"${arr[@]-}"` below instead of `"${arr[@]}"`: bash < 4.4
# raises "unbound variable" under `set -u` once an array is legitimately
# empty, live-reproduced on macOS during development of this file).

_iac_die() {
    echo "install-agent-clients: $1" >&2
    exit 1
}

_iac_require_cmd() {
    local cmd
    for cmd in "$@"; do
        command -v "$cmd" >/dev/null 2>&1 || _iac_die "missing required command: $cmd"
    done
}

_iac_validate_models_file() {
    local models_file="$1"
    jq -e '
        type == "array" and length > 0 and
        ([.[].id] | length == (unique | length)) and
        all(.[];
            (.id | type == "string" and length > 0) and
            (.label | type == "string" and length > 0) and
            (.ctx_size | type == "number" and . > 0 and floor == .) and
            (.client_max_output_tokens | type == "number" and . > 0 and floor == .) and
            .client_max_output_tokens <= .ctx_size)
    ' "$models_file" >/dev/null \
        || _iac_die "mapped model records require unique ids and valid context/output limits"
}

# create_agent_client_workdir
#
# Creates a private (0700) temp workspace ($workdir), initializes
# $staged_files, and installs the ONE `trap ... EXIT` this library and its
# caller need: it removes every path in $staged_files (bash-3.2-safe) plus
# $workdir itself. Call this before anything else that needs cleanup on
# failure -- neither caller installs its own trap once this has run.
create_agent_client_workdir() {
    workdir="$(mktemp -d)" || _iac_die "could not create private configuration workspace"
    chmod 700 "$workdir" || {
        rm -rf -- "$workdir"
        _iac_die "could not secure private configuration workspace"
    }
    staged_files=()
    # shellcheck disable=SC2317,SC2329 # invoked only via `trap ... EXIT`
    _iac_cleanup() {
        local status=$? path
        for path in "${staged_files[@]-}"; do
            [ -n "$path" ] && rm -f -- "$path"
        done
        rm -rf -- "$workdir"
        exit "$status"
    }
    trap _iac_cleanup EXIT
}

_iac_ensure_private_dir() {
    if [ -e "$1" ]; then
        [ -d "$1" ] || _iac_die "configuration directory path is not a directory: $1"
    else
        mkdir -p "$1" || _iac_die "could not create configuration directory: $1"
    fi
    chmod 700 "$1" || _iac_die "could not secure configuration directory: $1"
}

_iac_prepare_staged_file() {
    local directory="$1" filename="$2"
    _iac_ensure_private_dir "$directory"
    IAC_STAGED_FILE="$(mktemp "${directory}/.${filename}.XXXXXX")" \
        || _iac_die "could not stage ${filename}"
    chmod 600 "$IAC_STAGED_FILE" || {
        rm -f -- "$IAC_STAGED_FILE"
        _iac_die "could not secure staged ${filename}"
    }
    staged_files+=("$IAC_STAGED_FILE")
}

_iac_stage_pi() {
    local source="$1" provider="$2" staged="$3"
    # Drops any provider still filed under router-env's pre-rename name
    # ("local-llm-env") so an already-provisioned machine ends up with one
    # entry (the current name) instead of an orphaned duplicate.
    jq -s '
      if length != 2 then error("Pi configuration must contain one JSON object")
      elif (.[0] | type) != "object" then error("Pi configuration must be an object")
      elif (.[1] | type) != "object" then error("Pi provider must be an object")
      else
        .[0] as $config | .[1] as $provider |
        ($config.providers // {}) as $providers |
        if ($providers | type) != "object" then error("Pi providers must be an object") else
          $config | .providers = ((($providers | del(.["local-llm-env"]))) + {"router-env": $provider})
        end
      end
    ' "$source" "$provider" >"$staged"
}

_iac_stage_pi_settings() {
    local source="$1" models="$2" staged="$3"
    jq --slurpfile models "$models" '
        if type != "object" then error("Pi settings must be an object") else
          .enabledModels = [$models[0][] | "router-env/\(.id)"]
        end
    ' "$source" >"$staged"
}

# install_agent_clients BASE_URL API_KEY_FILE MODELS_FILE UPDATER_PATH LIB_PATH CREATE_MISSING_OPENCODE_STATE
#
# Requires create_agent_client_workdir to have already run ($workdir /
# $staged_files must exist).
#
#   $1 BASE_URL        -- e.g. http://127.0.0.1:20128/v1
#   $2 API_KEY_FILE     -- file holding the raw API key (never an argv
#                          string -- readable via /proc/<pid>/cmdline by
#                          any other local user for as long as a process
#                          holding it as an argument runs)
#   $3 MODELS_FILE      -- JSON file: [{id, label, ctx_size,
#                          client_max_output_tokens}, ...]
#   $4 UPDATER_PATH     -- materialized copy of update-opencode-config.mjs
#   $5 LIB_PATH         -- this file's own materialized copy (repo path
#                          locally, heredoc-written temp file remotely),
#                          hashed for the "Installer version" summary line
#   $6 CREATE_MISSING_OPENCODE_STATE -- "true" or "false". If OpenCode's
#      favorites/recent-model state ($XDG_STATE_HOME/opencode/model.json)
#      does not exist: "true" requires a local `opencode` binary at exact
#      version 1.18.10 and creates the file fresh (setup-local-llm-agents.sh's
#      historical behavior); "false" silently leaves it untouched (the
#      remote installer's historical behavior -- a fresh remote machine
#      that has never run OpenCode has nothing to preserve, and requiring
#      `opencode` there just to create this file would make the installer
#      hard-fail somewhere it is fine to let OpenCode create its own
#      default state on first use).
# validate_agent_client_local_state MODELS_FILE UPDATER_PATH CREATE_MISSING_OPENCODE_STATE
#
# Performs every check install_agent_clients() would otherwise defer until
# after it has already made network calls of its own callers' -- validates
# MODELS_FILE and, when an OpenCode model-state file already exists,
# validates it via UPDATER_PATH's --validate-model-state. Entirely local
# filesystem reads: no network access, no writes outside $workdir-free
# scratch space.
#
# Callers that also perform a network reachability check before calling
# install_agent_clients() (e.g. setup-local-llm-agents.sh's OmniRoute
# health probe) should call this first, so a malformed local input fails
# fast with zero network calls -- then do their network check -- then call
# install_agent_clients() as usual. install_agent_clients() repeats this
# same validation internally, so it stays correct even for a caller that
# never calls this precheck at all.
validate_agent_client_local_state() {
    _iac_require_cmd jq node

    local models_file="$1" updater_path="$2" create_missing_state="$3"

    case "$create_missing_state" in
        true|false) ;;
        *) _iac_die "CREATE_MISSING_OPENCODE_STATE must be true or false" ;;
    esac

    _iac_validate_models_file "$models_file"

    local opencode_state_dir="${XDG_STATE_HOME:-$HOME/.local/state}/opencode"
    local opencode_state_path="${opencode_state_dir}/model.json"
    if [ -e "$opencode_state_path" ]; then
        [ -f "$opencode_state_path" ] \
            || _iac_die "OpenCode model state is not a regular file: ${opencode_state_path}"
        node "$updater_path" --validate-model-state "$opencode_state_path" \
            || _iac_die "incompatible OpenCode model state: ${opencode_state_path}"
    fi
    # When create_missing_state=true and no state file exists yet,
    # install_agent_clients() still has to query the local `opencode`
    # binary to build one from scratch -- there is no existing file to
    # validate here, so there is nothing more to fail fast on.
}

install_agent_clients() {
    _iac_require_cmd jq node mktemp cmp

    local base_url="$1" api_key_file="$2" models_file="$3"
    local updater_path="$4" lib_path="$5" create_missing_state="$6"

    case "$create_missing_state" in
        true|false) ;;
        *) _iac_die "CREATE_MISSING_OPENCODE_STATE must be true or false" ;;
    esac

    _iac_validate_models_file "$models_file"

    local pi_dir="${PI_CODING_AGENT_DIR:-$HOME/.pi/agent}"
    local pi_path="${pi_dir}/models.json"
    local pi_settings_path="${pi_dir}/settings.json"
    local opencode_dir="${XDG_CONFIG_HOME:-$HOME/.config}/opencode"
    local opencode_state_dir="${XDG_STATE_HOME:-$HOME/.local/state}/opencode"
    local opencode_state_path="${opencode_state_dir}/model.json"
    local opencode_candidates=(
        "${opencode_dir}/config.json"
        "${opencode_dir}/opencode.json"
        "${opencode_dir}/opencode.jsonc"
    )

    local models_state_file="${workdir}/models-state.json"
    jq '[.[] | .alias = .id]' "$models_file" >"$models_state_file" \
        || _iac_die "could not build model ids for OpenCode model state"
    chmod 600 "$models_state_file" || _iac_die "could not secure model ids"

    local pi_provider="${workdir}/pi-provider.json"
    jq -n --arg base_url "$base_url" --rawfile api_key "$api_key_file" \
        --slurpfile models "$models_file" \
        '($models[0]) as $models | {
            baseUrl: $base_url,
            api: "openai-completions",
            apiKey: ($api_key | rtrimstr("\n")),
            compat: {supportsDeveloperRole: false, supportsReasoningEffort: false},
            models: [$models[] | {
                id: .id,
                contextWindow: .ctx_size,
                maxTokens: .client_max_output_tokens
            }]
        }' >"$pi_provider" || _iac_die "could not build Pi provider"
    chmod 600 "$pi_provider" || _iac_die "could not secure Pi provider"

    local opencode_provider="${workdir}/opencode-provider.json"
    jq -n --arg base_url "$base_url" --rawfile api_key "$api_key_file" \
        --slurpfile models "$models_file" \
        '($models[0]) as $models | {
            npm: "@ai-sdk/openai-compatible",
            name: "router-env",
            options: {baseURL: $base_url, apiKey: ($api_key | rtrimstr("\n"))},
            models: (reduce $models[] as $model ({};
                .[$model.id] = {
                    name: $model.label,
                    limit: {
                        context: $model.ctx_size,
                        output: $model.client_max_output_tokens
                    }
                }))
        }' >"$opencode_provider" || _iac_die "could not build OpenCode provider"
    chmod 600 "$opencode_provider" || _iac_die "could not secure OpenCode provider"

    local pi_source pi_settings_source
    if [ -e "$pi_path" ]; then
        [ -f "$pi_path" ] || _iac_die "Pi configuration is not a regular file: ${pi_path}"
        jq -e -s '
            if length != 1 or (.[0] | type) != "object" then false
            else ((.[0].providers // {}) | type) == "object"
            end
        ' "$pi_path" >/dev/null 2>&1 \
            || _iac_die "Pi configuration must contain exactly one JSON object with object providers: ${pi_path}"
        pi_source="$pi_path"
    else
        pi_source="${workdir}/empty-pi.json"
        printf '{}\n' >"$pi_source" || _iac_die "could not prepare empty Pi configuration"
        chmod 600 "$pi_source" || _iac_die "could not secure empty Pi configuration"
    fi

    if [ -e "$pi_settings_path" ]; then
        [ -f "$pi_settings_path" ] || _iac_die "Pi settings are not a regular file: ${pi_settings_path}"
        jq -e -s 'length == 1 and (.[0] | type) == "object"' "$pi_settings_path" >/dev/null 2>&1 \
            || _iac_die "Pi settings must contain exactly one JSON object: ${pi_settings_path}"
        pi_settings_source="$pi_settings_path"
    else
        pi_settings_source="${workdir}/empty-pi-settings.json"
        printf '{}\n' >"$pi_settings_source" || _iac_die "could not prepare empty Pi settings"
        chmod 600 "$pi_settings_source" || _iac_die "could not secure empty Pi settings"
    fi

    local opencode_targets=() opencode_sources=()
    local candidate contains_status
    for candidate in "${opencode_candidates[@]}"; do
        [ -e "$candidate" ] || continue
        [ -f "$candidate" ] || _iac_die "OpenCode configuration is not a regular file: ${candidate}"
        if node "$updater_path" --contains-provider "$candidate"; then
            contains_status=0
        else
            contains_status=$?
        fi
        case "$contains_status" in
            0) opencode_targets+=("$candidate"); opencode_sources+=("$candidate") ;;
            1) ;;
            *) _iac_die "could not validate OpenCode configuration: ${candidate}" ;;
        esac
    done
    if [ "${#opencode_targets[@]}" -eq 0 ]; then
        local index
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
        local empty_opencode="${workdir}/empty-opencode.jsonc"
        printf '{}\n' >"$empty_opencode" || _iac_die "could not prepare empty OpenCode configuration"
        chmod 600 "$empty_opencode" || _iac_die "could not secure empty OpenCode configuration"
        opencode_targets+=("${opencode_candidates[2]}")
        opencode_sources+=("$empty_opencode")
    fi

    local opencode_state_source=""
    if [ -e "$opencode_state_path" ]; then
        [ -f "$opencode_state_path" ] \
            || _iac_die "OpenCode model state is not a regular file: ${opencode_state_path}"
        opencode_state_source="$opencode_state_path"
    elif [ "$create_missing_state" = "true" ]; then
        _iac_require_cmd opencode
        [ "$(opencode --version)" = "1.18.10" ] \
            || _iac_die "cannot create OpenCode model state for an unsupported version"
        local debug_paths debug_state="" label value
        debug_paths="$(opencode debug paths)" || _iac_die "could not query OpenCode state path"
        while read -r label value; do
            [ "$label" = state ] && debug_state="$value"
        done <<<"$debug_paths"
        [ "$debug_state" = "$opencode_state_dir" ] \
            || _iac_die "OpenCode debug state path does not match the target directory"
        opencode_state_source="${workdir}/empty-opencode-model.json"
        printf '%s\n' '{"recent":[],"favorite":[],"variant":{}}' >"$opencode_state_source" \
            || _iac_die "could not prepare empty OpenCode model state"
        chmod 600 "$opencode_state_source" || _iac_die "could not secure empty OpenCode model state"
    fi
    # create_missing_state=false and the state file doesn't exist:
    # opencode_state_source stays "" and is left untouched, per the
    # parameter documentation above.

    if [ -n "$opencode_state_source" ]; then
        node "$updater_path" --validate-model-state "$opencode_state_source" \
            || _iac_die "incompatible OpenCode model state: ${opencode_state_source}"
    fi

    _iac_ensure_private_dir "$pi_dir"
    _iac_ensure_private_dir "$opencode_dir"

    _iac_prepare_staged_file "$pi_dir" "models.json"
    local pi_staged="$IAC_STAGED_FILE"
    _iac_stage_pi "$pi_source" "$pi_provider" "$pi_staged" || _iac_die "could not update Pi configuration"
    jq -e '.providers["router-env"] | type == "object"' "$pi_staged" >/dev/null \
        || _iac_die "could not validate staged Pi configuration"
    chmod 600 "$pi_staged" || _iac_die "could not secure staged Pi configuration"

    _iac_prepare_staged_file "$pi_dir" "settings.json"
    local pi_settings_staged="$IAC_STAGED_FILE"
    _iac_stage_pi_settings "$pi_settings_source" "$models_file" "$pi_settings_staged" \
        || _iac_die "could not update Pi settings"
    jq -e '.enabledModels | type == "array" and all(.[]; type == "string")' "$pi_settings_staged" >/dev/null \
        || _iac_die "could not validate staged Pi settings"
    chmod 600 "$pi_settings_staged" || _iac_die "could not secure staged Pi settings"

    local opencode_staged=() index filename opencode_target opencode_source staged_file
    for index in "${!opencode_targets[@]}"; do
        opencode_target="${opencode_targets[$index]}"
        opencode_source="${opencode_sources[$index]}"
        filename="${opencode_target##*/}"
        _iac_prepare_staged_file "$opencode_dir" "$filename"
        staged_file="$IAC_STAGED_FILE"
        opencode_staged+=("$staged_file")
        node "$updater_path" --replace-provider "$opencode_source" "$opencode_provider" "$staged_file" \
            || _iac_die "could not update OpenCode configuration"
        node "$updater_path" --contains-provider "$staged_file" \
            || _iac_die "could not validate staged OpenCode configuration"
        chmod 600 "$staged_file" || _iac_die "could not secure staged ${filename}"
    done

    local opencode_state_staged=""
    if [ -n "$opencode_state_source" ]; then
        _iac_prepare_staged_file "$opencode_state_dir" "model.json"
        opencode_state_staged="$IAC_STAGED_FILE"
        node "$updater_path" --update-model-state "$opencode_state_source" "$models_state_file" "$opencode_state_staged" \
            || _iac_die "could not update OpenCode model state"
        local state_check="${workdir}/checked-opencode-model.json"
        node "$updater_path" --update-model-state "$opencode_state_staged" "$models_state_file" "$state_check" \
            || _iac_die "could not validate staged OpenCode model state"
        cmp -s "$opencode_state_staged" "$state_check" || _iac_die "staged OpenCode model state is not idempotent"
        chmod 600 "$opencode_state_staged" || _iac_die "could not secure staged OpenCode model state"
    fi

    local replacement_error="client replacement failed; close Pi and OpenCode, then rerun the installer to repair partial updates"
    mv -f -- "$pi_staged" "$pi_path" || _iac_die "$replacement_error"
    staged_files=("${staged_files[@]:1}")
    mv -f -- "$pi_settings_staged" "$pi_settings_path" || _iac_die "$replacement_error"
    staged_files=("${staged_files[@]:1}")
    for index in "${!opencode_targets[@]}"; do
        mv -f -- "${opencode_staged[$index]}" "${opencode_targets[$index]}" || _iac_die "$replacement_error"
        staged_files=("${staged_files[@]:1}")
    done
    if [ -n "$opencode_state_staged" ]; then
        mv -f -- "$opencode_state_staged" "$opencode_state_path" || _iac_die "$replacement_error"
        staged_files=("${staged_files[@]:1}")
    fi

    echo "configured Pi provider: ${pi_path}"
    echo "configured Pi model cycle: ${pi_settings_path}"
    for opencode_target in "${opencode_targets[@]}"; do
        echo "configured OpenCode provider: ${opencode_target}"
    done
    if [ -n "$opencode_state_staged" ]; then
        echo "configured OpenCode favorites: ${opencode_state_path}"
    fi
    while IFS=$'\t' read -r model_id model_ctx limiting_model; do
        if [ -n "$limiting_model" ]; then
            echo "enabled model: ${model_id} (context ${model_ctx} -> [from ${limiting_model}])"
        else
            echo "enabled model: ${model_id} (context ${model_ctx})"
        fi
    done < <(jq -r '.[] | [.id, (.ctx_size | tostring), (.limiting_model // "")] | join("\t")' "$models_file")
    echo "restart Pi and OpenCode to load the updated configuration"

    local hash_cmd=""
    if command -v sha256sum >/dev/null 2>&1; then
        hash_cmd="sha256sum"
    elif command -v shasum >/dev/null 2>&1; then
        hash_cmd="shasum -a 256"
    fi
    if [ -n "$hash_cmd" ]; then
        echo "Installer version: $($hash_cmd "$lib_path" | cut -c1-8)"
    fi
}
