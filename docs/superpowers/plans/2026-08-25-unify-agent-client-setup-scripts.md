# Unify Local and Remote Agent Client Setup Scripts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract the ~260 lines of near-duplicated Pi/OpenCode config-staging logic in `setup/setup-local-llm-agents.sh` and `pylib/remote_setup.py`'s `SETUP_SCRIPT_TEMPLATE` into one shared bash library (`setup/lib/install-agent-clients.sh`), sourced locally and heredoc-embedded remotely (same technique already used for `update-opencode-config.mjs`), while adding three approved features: context size in the summary output, 7-day master-key caching with `--rm-key` on the remote path, and a version-marker line for staleness diagnosability.

**Architecture:** One new self-contained bash file exports `create_agent_client_workdir()` (owns the one `EXIT` trap) and `install_agent_clients(...)` (all staging/validation/atomic-swap logic + summary output). `setup-local-llm-agents.sh` keeps only genuinely-local preamble (config read, combo-context call, OmniRoute health check) then sources and calls the library directly from its repo path. `pylib/remote_setup.py`'s `SETUP_SCRIPT_TEMPLATE` keeps only genuinely-remote preamble (master-key prompt/cache, `/config` fetch) then embeds the library via the same `@@PLACEHOLDER@@` heredoc mechanism as the JS updater, writes it to a temp file, sources that, and calls the identical function.

**Tech Stack:** Bash (`setup/`, embedded in `pylib/remote_setup.py`'s Python string literal), `jq` for all JSON manipulation, Node (`update-opencode-config.mjs`, unchanged), Python stdlib (`pylib/remote_setup.py`, `pylib/compose.py`), pytest for both the Python unit tests and the subprocess/pty-driven shell tests in `tests/test_shell.py` / `tests/test_remote_setup.py`.

**Spec:** `docs/superpowers/specs/2026-08-25-unify-agent-client-setup-scripts-design.md`

## Global Constraints

- The shared library is self-contained: **no `source tools/lib.sh`**, no assumption the repo exists on the machine running it — it is embedded verbatim into a script that runs on an arbitrary remote machine (confirmed: macOS's stock `/bin/bash` is 3.2, which is why `"${staged_files[@]-}"`, not `"${staged_files[@]}"`, is required everywhere an array might be legitimately empty under `set -u`).
- The library's own error reporting uses a private `_iac_die()` (echo to stderr + `exit 1`), never `tools/lib.sh`'s `die`/`log_info`/`log_warn`.
- Every existing `test_shell.py` and `test_remote_setup.py` assertion about **file-writing behavior** (paths, JSON shapes, staging/validation error messages) must keep passing unchanged. Assertions about **summary output text** are expected to change per the three approved features — see each task.
- `install_agent_clients` signature is fixed across every task that calls it: `install_agent_clients BASE_URL API_KEY_FILE MODELS_FILE UPDATER_PATH LIB_PATH CREATE_MISSING_OPENCODE_STATE`.
- `create_agent_client_workdir` must run, and its `trap ... EXIT` must be installed, before either caller does anything else that needs cleanup on failure.
- `pylib/omniroute.py` is explicitly **not** touched by this plan (deferred to a separate design per the spec's Non-goals).

---

### Task 1: Create the shared library `setup/lib/install-agent-clients.sh`

**Files:**
- Create: `setup/lib/install-agent-clients.sh`
- Test: `tests/test_shell.py` (new tests appended near the end of the file)

**Interfaces:**
- Produces: `create_agent_client_workdir()` (no args; sets `$workdir` and `$staged_files`, installs the `EXIT` trap) and `install_agent_clients BASE_URL API_KEY_FILE MODELS_FILE UPDATER_PATH LIB_PATH CREATE_MISSING_OPENCODE_STATE` (return 0 on success, calls `_iac_die` — `exit 1` — on any failure). Both are consumed by Task 2 (local) and Task 3 (remote).

This task ports the staging/validation logic that exists identically (modulo the two already-hotfixed bugs and the `CREATE_MISSING_OPENCODE_STATE` divergence documented in the design spec) in `setup/setup-local-llm-agents.sh` (current lines ~143-400) and `pylib/remote_setup.py`'s `SETUP_SCRIPT_TEMPLATE` (current lines ~150-421), and adds the three approved features (context in the summary, version marker; master-key caching is remote-preamble-only, handled in Task 4).

- [ ] **Step 1: Create the directory and write the library file**

```bash
mkdir -p setup/lib
```

Write `setup/lib/install-agent-clients.sh` with exactly this content:

```bash
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
    # shellcheck disable=SC2317 # invoked only via `trap ... EXIT`
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

_iac_stage_pi_settings() {
    local source="$1" models="$2" staged="$3"
    jq --slurpfile models "$models" '
        if type != "object" then error("Pi settings must be an object") else
          .enabledModels = [$models[0][] | "local-llm-env/\(.id)"]
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
install_agent_clients() {
    _iac_require_cmd jq node mktemp cmp

    local base_url="$1" api_key_file="$2" models_file="$3"
    local updater_path="$4" lib_path="$5" create_missing_state="$6"

    case "$create_missing_state" in
        true|false) ;;
        *) _iac_die "CREATE_MISSING_OPENCODE_STATE must be true or false" ;;
    esac

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
            name: "local-llm-env",
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
    jq -e '.providers["local-llm-env"] | type == "object"' "$pi_staged" >/dev/null \
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
    while IFS=$'\t' read -r model_id model_ctx; do
        echo "enabled model: ${model_id} (context ${model_ctx})"
    done < <(jq -r '.[] | [.id, (.ctx_size | tostring)] | join("\t")' "$models_file")
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
```

- [ ] **Step 2: shellcheck the new file**

Run: `shellcheck -s bash setup/lib/install-agent-clients.sh`
Expected: no warnings. If shellcheck flags the unreachable-looking `_iac_cleanup` definition (SC2317, "not invoked"), confirm the `# shellcheck disable=SC2317` comment is directly above it (already included above).

- [ ] **Step 3: Write a standalone smoke test that sources the library directly**

This test does not run either wrapper script — it sources the library in isolation and calls `install_agent_clients` with fixture inputs, catching breakage in the shared code without needing a full end-to-end run. Add to `tests/test_shell.py`:

```python
def test_install_agent_clients_writes_expected_files_and_summary(tmp_path):
    """Sources setup/lib/install-agent-clients.sh directly (no wrapper
    script involved) and calls install_agent_clients with fixture inputs.
    Fastest possible regression net for the shared library itself."""
    home = tmp_path / "home"
    home.mkdir()
    api_key_file = tmp_path / "api-key"
    api_key_file.write_text("sk-test-key")
    models_file = tmp_path / "models.json"
    models_file.write_text(json.dumps([
        {"id": "my-planning", "label": "my-planning (combo)",
         "ctx_size": 500000, "client_max_output_tokens": 128000},
    ]))
    updater_path = REPO_DIR / "setup" / "update-opencode-config.mjs"
    lib_path = REPO_DIR / "setup" / "lib" / "install-agent-clients.sh"

    script = f'''
set -euo pipefail
export HOME="{home}"
source "{lib_path}"
create_agent_client_workdir
install_agent_clients "http://127.0.0.1:20128/v1" "{api_key_file}" "{models_file}" "{updater_path}" "{lib_path}" "false"
'''
    result = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr

    pi_models = json.loads((home / ".pi" / "agent" / "models.json").read_text())
    assert pi_models["providers"]["local-llm-env"]["models"][0]["id"] == "my-planning"
    opencode_config = json.loads(
        (home / ".config" / "opencode" / "opencode.jsonc").read_text()
    )
    assert "my-planning" in opencode_config["provider"]["local-llm-env"]["models"]

    assert "enabled model: my-planning (context 500000)" in result.stdout
    assert "restart Pi and OpenCode to load the updated configuration" in result.stdout
    assert "Installer version: " in result.stdout
    # create_missing_state="false" and no prior OpenCode state file: must
    # not be created, and must not be mentioned in the summary.
    assert not (home / ".local" / "state" / "opencode" / "model.json").exists()
    assert "configured OpenCode favorites" not in result.stdout


def test_install_agent_clients_creates_missing_state_when_requested(tmp_path, monkeypatch):
    """create_missing_state="true" without a local `opencode` binary must
    fail clearly rather than silently skip -- the local script's contract."""
    home = tmp_path / "home"
    home.mkdir()
    api_key_file = tmp_path / "api-key"
    api_key_file.write_text("sk-test-key")
    models_file = tmp_path / "models.json"
    models_file.write_text(json.dumps([
        {"id": "llama-cpp/a", "label": "a", "ctx_size": 8192, "client_max_output_tokens": 4096},
    ]))
    updater_path = REPO_DIR / "setup" / "update-opencode-config.mjs"
    lib_path = REPO_DIR / "setup" / "lib" / "install-agent-clients.sh"
    empty_path_dir = tmp_path / "empty-path"
    empty_path_dir.mkdir()

    script = f'''
set -euo pipefail
export HOME="{home}"
export PATH="{empty_path_dir}:/usr/bin:/bin"
source "{lib_path}"
create_agent_client_workdir
install_agent_clients "http://127.0.0.1:20128/v1" "{api_key_file}" "{models_file}" "{updater_path}" "{lib_path}" "true"
'''
    result = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, check=False
    )
    assert result.returncode == 1
    assert "missing required command: opencode" in result.stdout + result.stderr
```

Confirm `REPO_DIR` and `subprocess`/`json` are already imported at the top of `tests/test_shell.py` (they are, used throughout the file) — no new imports needed.

- [ ] **Step 4: Run the new tests**

Run: `cd /var/home/bazzite/git/llm-env && uv run pytest tests/test_shell.py -k install_agent_clients -v`
Expected: both PASS.

- [ ] **Step 5: Commit**

```bash
git add setup/lib/install-agent-clients.sh tests/test_shell.py
git commit -m "$(cat <<'EOF'
Add shared install-agent-clients.sh library for Pi/OpenCode client setup

Ports the staging/validation logic duplicated between
setup-local-llm-agents.sh and remote_setup.py's SETUP_SCRIPT_TEMPLATE into
one self-contained, bash-3.2-safe file; not yet wired into either caller.
EOF
)"
```

---

### Task 2: Rewire `setup/setup-local-llm-agents.sh` onto the shared library

**Files:**
- Modify: `setup/setup-local-llm-agents.sh`
- Test: `tests/test_shell.py`

**Interfaces:**
- Consumes: `create_agent_client_workdir`, `install_agent_clients` (Task 1)

- [ ] **Step 1: Read the current file in full to confirm exact line ranges before editing**

Run: `sed -n '1,400p' setup/setup-local-llm-agents.sh` and identify the exact boundary between the local-only preamble (config read through `models_json` construction and the OmniRoute health check) and the staging logic that Task 1 already ported (the `workdir`/`api_key_file`/`models_file`/provider-building/staging/atomic-swap/summary block).

- [ ] **Step 2: Replace everything from the old `workdir="$(mktemp -d)"` line through the final `jq -r '.[].id]' "$models_file"` summary loop with the shared-library call**

Delete lines implementing: `workdir` creation, `cleanup_workspace`/`cleanup` traps, `api_key_file`/`models_file`/`pi_provider`/`opencode_provider` construction, `ensure_private_dir`/`prepare_staged_file`/`stage_pi`/`stage_pi_settings`/`stage_opencode` function definitions, the Pi/OpenCode candidate-detection and staging blocks, the OpenCode model-state block, the atomic `mv -f` sequence, and the final summary echo loop.

Keep everything before that (the config/health preamble through building `models_json`, plus the `jq -ne`/`jq -e` validation of `$omniroute_port` and `$models_json` shape) **and** keep line 8's `log_warn "close Pi and OpenCode before continuing"` (it stays local-only, per the design spec).

Replace the deleted block with:

```bash
# shellcheck disable=SC1091 # Resolved from this script at runtime.
# shellcheck source=lib/install-agent-clients.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/install-agent-clients.sh"

create_agent_client_workdir

api_key_file="${workdir}/api-key"
printf '%s' "$api_key" >"$api_key_file" || die "could not write private API key"
chmod 600 "$api_key_file" || die "could not secure private API key"
models_file="${workdir}/models.json"
printf '%s\n' "$models_json" >"$models_file" || die "could not write enabled models"
chmod 600 "$models_file" || die "could not secure enabled models"

install_agent_clients \
    "$base_url" "$api_key_file" "$models_file" \
    "${REPO_DIR}/setup/update-opencode-config.mjs" \
    "${REPO_DIR}/setup/lib/install-agent-clients.sh" \
    "true"
```

(`$base_url`, computed earlier in the kept preamble as `base_url="http://127.0.0.1:${omniroute_port}/v1"`, and `$api_key`/`$models_json` from the kept preamble, are all still in scope.)

- [ ] **Step 3: shellcheck the trimmed script**

Run: `shellcheck -s bash setup/setup-local-llm-agents.sh`
Expected: no warnings.

- [ ] **Step 4: Run the full existing local-agent test suite and fix any that now fail on summary text**

Run: `cd /var/home/bazzite/git/llm-env && uv run pytest tests/test_shell.py -k setup_local_llm_agents -v`

Expected: the vast majority PASS unchanged (file-writing behavior is byte-for-byte identical — only the shared library's *source location* changed). Two categories may need a one-line fix:
- Any test asserting the exact old bare line `"enabled model: <id>"` (no parenthetical) must be updated to `"enabled model: <id> (context <ctx_size>)"` — grep first: `grep -n '"enabled model:' tests/test_shell.py`.
- Any test asserting an exact total line count of stdout must account for the new `"Installer version: ..."` line.

If a test fails for a different reason (a real behavioral regression, not a text-format update), stop and investigate before changing the test — do not paper over a genuine regression by loosening an assertion.

- [ ] **Step 5: Run the full shell test suite as a regression check**

Run: `cd /var/home/bazzite/git/llm-env && uv run pytest tests/test_shell.py -q`
Expected: PASS (same count as before this task, plus/minus only the fixes from Step 4).

- [ ] **Step 6: Commit**

```bash
git add setup/setup-local-llm-agents.sh tests/test_shell.py
git commit -m "$(cat <<'EOF'
Rewire setup-local-llm-agents.sh onto the shared install-agent-clients.sh library

Trims the script to its genuinely local preamble (config read, combo
context, OmniRoute health check); all Pi/OpenCode staging now runs through
the shared library.
EOF
)"
```

---

### Task 3: Rewire the remote installer onto the shared library

**Files:**
- Modify: `pylib/remote_setup.py`
- Modify: `pylib/compose.py`
- Test: `tests/test_remote_setup.py`
- Test: `tests/test_compose.py`

**Interfaces:**
- Consumes: `setup/lib/install-agent-clients.sh` (Task 1), read from disk by `render_setup_script()`
- Produces: `render_setup_script()`'s output now sources the embedded library instead of duplicating its logic; `remote_setup_service["volumes"]` in `pylib/compose.py` gains a bind mount for `setup/lib`

- [ ] **Step 1: Mount `setup/lib` into the remote-setup container**

In `pylib/compose.py`, find the `remote_setup_service["volumes"]` list (currently mounting `pylib/`, `setup/update-opencode-config.mjs`, and the `remote-setup-data` named volume). Add, alongside the existing `update-opencode-config.mjs` mount:

```python
f"{_dollar_escape(repo_root)}/setup/lib:/app/setup/lib:ro,z",
```

- [ ] **Step 2: Run the compose test suite to confirm the new mount**

Run: `cd /var/home/bazzite/git/llm-env && uv run pytest tests/test_compose.py -v`
Expected: PASS (no existing test currently enumerates every volume entry exhaustively; if one does, add the new mount to its expected list rather than loosening the assertion).

Add this test right after the existing remote-setup volume test in `tests/test_compose.py` (find it via `grep -n "update-opencode-config" tests/test_compose.py` to match its exact style and the `CFG`/`compose_dict` fixtures already used there):

```python
def test_remote_setup_mounts_the_shared_agent_client_library():
    _, document = compose_dict(CFG)
    volumes = document["services"]["remote-setup"]["volumes"]
    assert any(v.endswith("/setup/lib:/app/setup/lib:ro,z") for v in volumes)
```

- [ ] **Step 3: Add the new placeholder constant and update `render_setup_script()`**

In `pylib/remote_setup.py`, near the existing `_HOST_PLACEHOLDER`/`_UPDATER_JS_PLACEHOLDER` constants and `UPDATE_OPENCODE_CONFIG_PATH`, add:

```python
# Mounted read-only by pylib/compose.py's remote-setup service (Task 3 of
# docs/superpowers/plans/2026-08-25-unify-agent-client-setup-scripts.md)
# alongside pylib/ and update-opencode-config.mjs. Not served over HTTP --
# embedded into /setup.sh's response via a bash heredoc, exactly like the
# JS updater.
INSTALL_AGENT_CLIENTS_LIB_PATH = Path("/app/setup/lib/install-agent-clients.sh")
_INSTALL_LIB_PLACEHOLDER = "@@INSTALL_LIB_SH@@"
```

Update `render_setup_script()`:

```python
def render_setup_script(host: str) -> str:
    updater_source = UPDATE_OPENCODE_CONFIG_PATH.read_text(encoding="utf-8")
    lib_source = INSTALL_AGENT_CLIENTS_LIB_PATH.read_text(encoding="utf-8")
    script = SETUP_SCRIPT_TEMPLATE.replace(_HOST_PLACEHOLDER, host)
    script = script.replace(_UPDATER_JS_PLACEHOLDER, updater_source)
    return script.replace(_INSTALL_LIB_PLACEHOLDER, lib_source)
```

- [ ] **Step 4: Replace `SETUP_SCRIPT_TEMPLATE`'s staging logic with the embedded library**

Read the current `SETUP_SCRIPT_TEMPLATE` in full (`sed -n '69,421p' pylib/remote_setup.py`) to confirm exact current line numbers before editing — Task 1/earlier hotfixes may have shifted them slightly from what's quoted below.

Keep everything from the top (`#!/usr/bin/env bash` through the command-availability loop) through `models_file="${workdir}/models.json"; printf '%s' "$models_json" >"$models_file"; chmod 600 "$models_file"` — **except**:
- Delete the standalone `workdir="$(mktemp -d)" ...` / `staged_files=()` / `cleanup() { ... }` / `trap cleanup EXIT` block entirely (replaced below).
- Add `cmp` to the command-availability loop (`for cmd in curl jq node mktemp cmp; do`) — the shared library needs it whenever OpenCode model state already exists remotely, a gap in today's list.

Then delete everything from `pi_dir="${PI_CODING_AGENT_DIR:-$HOME/.pi/agent}"` through the final `echo "  Keep it as private as the master key.)"` **except** the OmniRoute dashboard banner lines at the very end (those stay, remote-only, after the library call) — and except the `updater="${workdir}/update-opencode-config.mjs"` heredoc-write block, which stays (unchanged) since `install_agent_clients` still needs a materialized updater path.

Replace the deleted staging block with:

```bash
lib="${workdir}/install-agent-clients.sh"
cat >"$lib" <<'INSTALL_LIB_EOF'
@@INSTALL_LIB_SH@@
INSTALL_LIB_EOF
# shellcheck disable=SC1090 # heredoc-written above, not a static path
source "$lib"

create_agent_client_workdir

install_agent_clients \
    "$base_url" "$api_key_file" "$models_file" "$updater" "$lib" "false"
```

(`create_agent_client_workdir` replaces the deleted `workdir`/`trap` block entirely — note `$workdir` itself was already created earlier, at the very top of the script, before the master-key prompt; **that** earlier `workdir="$(mktemp -d)"` block must also be deleted, and `create_agent_client_workdir` called once, at this later point instead, since the library now owns workdir creation. Everything above that already uses `$workdir` — `auth_conf`, `response_file`, `api_key_file`, `models_file`, `updater` — must move to *after* the `create_agent_client_workdir` call, or `create_agent_client_workdir` must run first, before the master-key prompt. Resolve by calling `create_agent_client_workdir` immediately after the command-availability loop, before the master-key prompt, and sourcing the library at that same point — `source "$lib"` requires `$lib` to be materialized first via the heredoc, so the heredoc-write block also needs to move up to right after `create_agent_client_workdir`. Final order: command-availability loop → heredoc-write `$updater` and `$lib` → `source "$lib"` → `create_agent_client_workdir` → master-key prompt → `/config` fetch → `api_key_file`/`models_file` writes → `log_warn`-style "close Pi and OpenCode" line (see Step 5) → `install_agent_clients` call → dashboard banner.)

Immediately after the final `install_agent_clients` call, keep the existing dashboard banner:

```bash
echo
echo "OmniRoute dashboard: ${omniroute_dashboard_url}"
echo "  Password: ${omniroute_dashboard_password}"
echo "  (this is the OmniRoute ADMIN password -- full access to add/remove"
echo "  provider connections and revoke API keys, not just chat access."
echo "  Keep it as private as the master key.)"
```

- [ ] **Step 5: Add the "close Pi and OpenCode" warning to the remote preamble too**

For parity with the local script (which prints this before touching anything), add near the top of `SETUP_SCRIPT_TEMPLATE`, right after the command-availability loop:

```bash
echo "close Pi and OpenCode before continuing" >&2
```

- [ ] **Step 6: Run `render_setup_script` against a fixture library file to sanity-check assembly**

Run: `cd /var/home/bazzite/git/llm-env && uv run python3 -c "
from pylib.remote_setup import render_setup_script
import pylib.remote_setup as m
m.INSTALL_AGENT_CLIENTS_LIB_PATH = m.Path('setup/lib/install-agent-clients.sh')
m.UPDATE_OPENCODE_CONFIG_PATH = m.Path('setup/update-opencode-config.mjs')
script = render_setup_script('llm.local:20130')
assert 'install_agent_clients \"\$base_url\"' in script
assert 'create_agent_client_workdir' in script
assert '@@' not in script
print('ok')
"`
Expected: prints `ok`.

- [ ] **Step 7: Update `tests/test_remote_setup.py`'s fixture/mock plumbing to serve the new library file**

Find `_use_real_opencode_updater(monkeypatch)` (used by every test that calls `render_setup_script` and needs a real `UPDATE_OPENCODE_CONFIG_PATH`) via `grep -n "_use_real_opencode_updater" tests/test_remote_setup.py`. Add an equivalent fixture right next to it:

```python
def _use_real_install_agent_clients_lib(monkeypatch):
    monkeypatch.setattr(
        remote_setup,
        "INSTALL_AGENT_CLIENTS_LIB_PATH",
        REPO_DIR / "setup" / "lib" / "install-agent-clients.sh",
    )
```

(Match `REPO_DIR`'s existing definition/import in this test file — it is already used for the updater fixture.) Every call site of `_use_real_opencode_updater(monkeypatch)` in this file must now also call `_use_real_install_agent_clients_lib(monkeypatch)` — grep for every call site and add the second call immediately after the first. This includes `_run_generated_setup_sh`'s own setup, if it calls the updater fixture internally — check its definition first.

- [ ] **Step 8: Update the two tests hotfixed earlier this session to match the moved code**

`test_setup_sh_cleanup_trap_survives_an_empty_staged_files_array` currently asserts `'for path in "${staged_files[@]-}"; do' in script` directly against `SETUP_SCRIPT_TEMPLATE`'s own text. That exact string now lives in the embedded library instead, but since `render_setup_script()` embeds the library verbatim, the assertion still holds against the *rendered* script — no change needed, but rerun it explicitly to confirm:

Run: `cd /var/home/bazzite/git/llm-env && uv run pytest tests/test_remote_setup.py -k cleanup_trap_survives -v`
Expected: PASS.

`test_setup_sh_executed_end_to_end_configures_pi_and_opencode` asserts `"Done. Model(s): llama-cpp/a" in output` (added this session as a regression test for the `.alias` bug). That exact summary line no longer exists after this task — replaced by the shared library's `"enabled model: llama-cpp/a (context ...)"` line. Update the assertion:

```python
    assert "enabled model: llama-cpp/a (context" in output
    assert "Installer version: " in output
```

(replacing the old `assert "Done. Model(s): llama-cpp/a" in output` line).

- [ ] **Step 9: Run the full remote-setup test suite**

Run: `cd /var/home/bazzite/git/llm-env && uv run pytest tests/test_remote_setup.py -v`
Expected: PASS. Any remaining failure asserting on old `SETUP_SCRIPT_TEMPLATE`-internal text (e.g. a test matching a specific jq snippet by substring against the template directly rather than the rendered output) needs updating to look for that text in the embedded-library section instead — grep `tests/test_remote_setup.py` for `SETUP_SCRIPT_TEMPLATE` direct references to find these.

- [ ] **Step 10: Commit**

```bash
git add pylib/remote_setup.py pylib/compose.py tests/test_remote_setup.py tests/test_compose.py
git commit -m "$(cat <<'EOF'
Rewire the remote installer onto the shared install-agent-clients.sh library

SETUP_SCRIPT_TEMPLATE now embeds and sources the same library
setup-local-llm-agents.sh uses, via the same heredoc technique already
used for the OpenCode updater; setup/lib is bind-mounted read-only into
the remote-setup container alongside pylib/.
EOF
)"
```

---

### Task 4: Master key caching (7-day TTL) and `--rm-key`

**Files:**
- Modify: `pylib/remote_setup.py` (`SETUP_SCRIPT_TEMPLATE`)
- Modify: `setup/network.sh` (documented invocation)
- Test: `tests/test_remote_setup.py`

**Interfaces:**
- Consumes: nothing new from earlier tasks besides the template structure Task 3 left in place
- Produces: cached key at `${XDG_CACHE_HOME:-$HOME/.cache}/llm-env/master-key`, `--rm-key` CLI flag on the generated script

- [ ] **Step 1: Read the current preamble order (post-Task-3) to confirm exact insertion point**

Run: `sed -n '69,140p' pylib/remote_setup.py` to see the current command-availability loop → `create_agent_client_workdir` → master-key prompt sequence left by Task 3.

- [ ] **Step 2: Add argument parsing and the cache read/write around the master-key prompt**

Replace the existing:

```bash
# `curl ... | bash` leaves stdin attached to the script pipe, not the
# terminal -- `read` must go to /dev/tty explicitly or the prompt below
# silently reads EOF instead of actually asking the user.
read -r -s -p "OMNI_ROUTER_MASTER_KEY: " master_key < /dev/tty
echo
```

with:

```bash
rm_key=0
for arg in "$@"; do
    case "$arg" in
        --rm-key) rm_key=1 ;;
        *)
            echo "remote-setup: unknown argument: $arg" >&2
            exit 1
            ;;
    esac
done

master_key_cache_dir="${XDG_CACHE_HOME:-$HOME/.cache}/llm-env"
master_key_cache="${master_key_cache_dir}/master-key"

# `curl ... | bash` leaves stdin attached to the script pipe, not the
# terminal -- `read` must go to /dev/tty explicitly or the prompt below
# silently reads EOF instead of actually asking the user.
if [ "$rm_key" -eq 1 ]; then
    rm -f -- "$master_key_cache"
    read -r -s -p "OMNI_ROUTER_MASTER_KEY: " master_key < /dev/tty
    echo
elif [ -f "$master_key_cache" ] && \
     [ "$(find "$master_key_cache" -mtime -7 2>/dev/null)" = "$master_key_cache" ]; then
    master_key="$(cat "$master_key_cache")"
else
    read -r -s -p "OMNI_ROUTER_MASTER_KEY: " master_key < /dev/tty
    echo
    mkdir -p "$master_key_cache_dir"
    chmod 700 "$master_key_cache_dir"
    printf '%s' "$master_key" >"$master_key_cache"
    chmod 600 "$master_key_cache"
fi
```

(`find "$file" -mtime -7` matches files modified within the last 7 days, portable across GNU findutils and macOS/BSD find — both accept `-mtime -N`; comparing its output to the exact path confirms a match without a second stat call. `--rm-key` explicitly skips the cache-write branch per the confirmed semantics: prompt fresh, use it for this run, leave no cached key on disk.)

- [ ] **Step 3: Document `--rm-key` in `setup/network.sh`'s existing remote-install instructions**

Find the existing `curl http://llm.local:20130/setup.sh | bash` line (via `grep -n "setup.sh | bash" setup/network.sh`) and add, immediately after it:

```bash
echo "  (add ' -s -- --rm-key' before the final 'bash' to force a fresh"
echo "  master key prompt without caching it)"
```

- [ ] **Step 4: Add tests for the cache and the flag**

Add to `tests/test_remote_setup.py`, near the other end-to-end tests (find `_run_generated_setup_sh` via `grep -n "_run_generated_setup_sh" tests/test_remote_setup.py` and match its exact signature/pty-driving style before writing these):

```python
def test_setup_sh_caches_the_master_key_for_seven_days(tmp_path, monkeypatch):
    """A second run within the cache window must not prompt at all --
    drive it with no pty input available and confirm it still succeeds."""
    _, _, home_dir = _run_generated_setup_sh(tmp_path, monkeypatch)
    cache_path = home_dir / ".cache" / "llm-env" / "master-key"
    assert cache_path.read_text() == "test-master-key"
    assert (cache_path.stat().st_mode & 0o777) == 0o600

    exit_code, output, _ = _run_generated_setup_sh(
        tmp_path, monkeypatch, home_dir=home_dir, send_master_key=False
    )
    assert exit_code == 0, output
    assert "OMNI_ROUTER_MASTER_KEY:" not in output


def test_setup_sh_rm_key_prompts_fresh_and_leaves_no_cache(tmp_path, monkeypatch):
    _, _, home_dir = _run_generated_setup_sh(tmp_path, monkeypatch)
    cache_path = home_dir / ".cache" / "llm-env" / "master-key"
    assert cache_path.exists()

    exit_code, output, _ = _run_generated_setup_sh(
        tmp_path, monkeypatch, home_dir=home_dir, extra_args=["--rm-key"]
    )
    assert exit_code == 0, output
    assert "OMNI_ROUTER_MASTER_KEY:" in output
    assert not cache_path.exists()
```

`_run_generated_setup_sh` needs two new optional parameters to support these: `home_dir: Path | None = None` (reuse an existing `$HOME` instead of always creating a fresh one — check its current signature first; if it already accepts a prepared home directory via `prepare_home`, reuse that mechanism instead of adding a new parameter) and `extra_args: list[str] | None = None` (appended to the `bash -s --` invocation) plus `send_master_key: bool = True` (skip writing the master key to the pty when `False`, needed for the cached-run case where no prompt should appear at all). Read `_run_generated_setup_sh`'s current implementation in full before adding these — its exact pty-driving mechanism (likely `pty.fork()` + writing `master_key + "\n"` to the child's stdin) determines exactly how to make `send_master_key` skip that write cleanly rather than hanging on a prompt that never appears.

- [ ] **Step 5: Run the new tests and the full remote-setup suite**

Run: `cd /var/home/bazzite/git/llm-env && uv run pytest tests/test_remote_setup.py -v`
Expected: PASS, including the two new tests.

- [ ] **Step 6: shellcheck the rendered template one more time**

The template lives inside a Python string, so `shellcheck` cannot run on it directly. Instead, extract and check it via the same fixture used by Step 6 of Task 3:

Run: `cd /var/home/bazzite/git/llm-env && uv run python3 -c "
import pylib.remote_setup as m
m.INSTALL_AGENT_CLIENTS_LIB_PATH = m.Path('setup/lib/install-agent-clients.sh')
m.UPDATE_OPENCODE_CONFIG_PATH = m.Path('setup/update-opencode-config.mjs')
open('/tmp/rendered-setup.sh', 'w').write(m.render_setup_script('llm.local:20130'))
"` then `shellcheck -s bash /tmp/rendered-setup.sh`
Expected: no warnings (the embedded JS updater section is not valid bash and will be flagged if shellcheck tries to parse it as such — if so, this check was already impossible before this plan for the same reason and can be skipped; confirm by running it against the *current* `main` branch's `render_setup_script` output first to see whether it already fails there).

- [ ] **Step 7: Commit**

```bash
git add pylib/remote_setup.py setup/network.sh tests/test_remote_setup.py
git commit -m "$(cat <<'EOF'
Cache the remote installer's master key for 7 days; add --rm-key

Avoids re-prompting on routine re-runs (e.g. picking up a combo
context-size change) while still fetching /config live every time;
--rm-key forces a fresh prompt and leaves nothing cached.
EOF
)"
```

---

### Task 5: Final validation sweep

**Files:**
- Modify: `tools/validate.sh`

**Interfaces:**
- Consumes: nothing new
- Produces: `make validate` covering `setup/lib/*.sh`

- [ ] **Step 1: Add `setup/lib/*.sh` to the shellcheck glob**

In `tools/validate.sh`, change:

```bash
shellcheck -s bash ./tools/*.sh ./setup/*.sh ./scripts/*.sh
```

to:

```bash
shellcheck -s bash ./tools/*.sh ./setup/*.sh ./setup/lib/*.sh ./scripts/*.sh
```

- [ ] **Step 2: Run full validation**

Run: `cd /var/home/bazzite/git/llm-env && bash tools/validate.sh`
Expected: `All checks passed!` / `All checks passed.`

- [ ] **Step 3: Run the full test suite**

Run: `cd /var/home/bazzite/git/llm-env && uv run pytest tests/ -q`
Expected: PASS, full suite green.

- [ ] **Step 4: Live smoke test — regenerate and redeploy, confirm both paths end to end**

```bash
make start
curl -s http://127.0.0.1:20130/setup.sh | grep -E 'Installer version|enabled model:|create_agent_client_workdir'
```
Expected: the served script contains `create_agent_client_workdir` (proving the shared library embedded correctly) and the summary-line templates. Then, if a second machine or a scratch `$HOME` is available, actually run the curl-piped install end to end and confirm output shows `enabled model: <id> (context <n>)` and `Installer version: <hash>` lines, and that a second run within 7 days does not prompt for the master key.

- [ ] **Step 5: Commit**

```bash
git add tools/validate.sh
git commit -m "$(cat <<'EOF'
Add setup/lib/*.sh to the shellcheck glob in tools/validate.sh
EOF
)"
```
