# Unify Local and Remote Agent Client Setup Scripts — Design

**Status:** Approved by user 2026-08-25. Ready for implementation planning.

## Goal

`setup/setup-local-llm-agents.sh` (runs on this host) and the
`SETUP_SCRIPT_TEMPLATE` embedded in `pylib/remote_setup.py` (served over
HTTP and run via `curl | bash` on a remote machine) both configure Pi and
OpenCode to talk to OmniRoute. They should differ only in how they obtain
their three inputs — `base_url`, an API key, and the client-visible models
list (`models_json`) — local by calling OmniRoute directly, remote by
fetching `/config` with the master key. Today they instead carry ~260
lines of near-identical staging/validation logic maintained by hand in two
places, which has already drifted out of sync at least once this session
(the local script's `models_json`/`models_state_file`/provider-building
logic was updated for the `id`/`label` combo-mapping schema; the remote
template was updated too, but only because it was edited in the same
commit — nothing prevents the next change from touching only one side).

**Concrete motivating evidence:** while testing today's combo changes, the
user ran the remote installer against a genuinely *stale* container (see
"Staleness diagnosability" below) and hit a real crash — a macOS-specific
bash portability bug present, identically, in *both* files' independently
maintained `cleanup()` (see "Fixes already applied" below; hotfixed
directly, ahead of this refactor, since the user was blocked). Unifying
the two scripts would make that entire class of bug structurally
impossible going forward — there would be only one staging implementation
to fix, once, instead of two copies that can each silently carry the same
latent bug.

## Non-goals

- Splitting `pylib/omniroute.py` — explicitly deferred to a separate,
  smaller design (confirmed with the user 2026-08-25).
- Any config-writing behavior change beyond the three explicitly-scoped
  additions below (richer summary output, master key caching, version
  marker) — the staging/validation logic itself (what gets written to Pi
  and OpenCode's config files, and how) is a pure internal refactor. Every
  existing `test_shell.py` and `test_remote_setup.py` assertion about
  *that* (file paths, JSON shapes, error messages) must keep passing
  unchanged; tests that assert on today's summary *output text* are
  expected to need updating, since that output is intentionally changing.

## Precedent already in this codebase

`setup/update-opencode-config.mjs` is already shared exactly this way:
authored once, and `pylib/remote_setup.py::render_setup_script()` reads
its content at request time and embeds it verbatim into the served bash
script via a quoted heredoc (`@@OPENCODE_UPDATER_JS@@` placeholder, see
`pylib/remote_setup.py:174-181`). This design applies the identical
pattern to the staging logic that isn't shared yet.

## Architecture

### New file: `setup/lib/install-agent-clients.sh`

A self-contained bash library — **no `source tools/lib.sh`, no repo-path
assumptions beyond what's passed in** — because it must work verbatim when
embedded into a script that runs on a machine with no copy of this repo.

Contents: everything from `setup-local-llm-agents.sh`'s current lines
~143-400 and the equivalent block in `SETUP_SCRIPT_TEMPLATE` (~150-421) —
the `ensure_private_dir`/`prepare_staged_file` helpers, `pi_provider`/
`opencode_provider` construction, OpenCode candidate-file detection,
Pi/OpenCode/model-state staging and validation, the atomic `mv -f` swap
sequence, and the final `echo` summary lines — refactored into one
function:

```bash
# install_agent_clients BASE_URL API_KEY_FILE MODELS_FILE UPDATER_PATH LIB_PATH
#
# Reads:
#   $1 BASE_URL       -- e.g. http://127.0.0.1:20128/v1
#   $2 API_KEY_FILE   -- path to a file containing the raw API key (never
#                        passed as an argv string -- see existing comment
#                        in both source files about /proc/<pid>/cmdline)
#   $3 MODELS_FILE    -- path to a JSON file: [{id, label, ctx_size,
#                        client_max_output_tokens}, ...]
#   $4 UPDATER_PATH   -- path to a materialized copy of
#                        update-opencode-config.mjs (repo path locally;
#                        a heredoc-written temp file remotely)
#   $5 LIB_PATH       -- path to install-agent-clients.sh's own materialized
#                        copy (repo path locally; heredoc-written temp file
#                        remotely) -- hashed (sha256, first 8 hex chars) for
#                        the "Installer version" summary line, see below
#
# Honors PI_CODING_AGENT_DIR / XDG_CONFIG_HOME / XDG_STATE_HOME overrides
# (unchanged from today). Uses its own die() (echo to stderr + exit 1) --
# not tools/lib.sh's -- so behavior is identical whether sourced locally
# or embedded remotely. Exits non-zero via die() on any failure; the
# caller's own `trap ... EXIT` (workdir cleanup) is unaffected since this
# is a function in the caller's shell, not a subprocess.
install_agent_clients() { ... }
```

It also defines and populates `staged_files=()` internally, and the
cleanup trap for `staged_files` moves *into this library* too (currently
duplicated as the two nearly-identical `cleanup()` functions) — the caller
no longer needs its own `staged_files` bookkeeping.

**Trap ownership (confirmed with the user 2026-08-25):** the library owns
the *one* `EXIT` trap for everything from `workdir` creation onward, not
each caller. The library exports a `create_agent_client_workdir()`
function that both callers use to create `$workdir` (instead of each
doing its own `mktemp -d` + `chmod 700` + ad hoc trap as today); that
function installs the library's `cleanup()` as the `EXIT` trap at the same
point. Every array reference inside `cleanup()` uses the
`"${staged_files[@]-}"` bash-3.2-safe idiom (see "Fixes already applied"
below) since the remote path runs on arbitrary client bash, notably
macOS's stock bash 3.2.

### `setup/setup-local-llm-agents.sh` (trimmed)

Keeps exactly what's genuinely local:
- `require_cmd uv curl jq yq node cmp`
- config read, `migrate_config_file`, `llmenv omniroute issue-key`
- `llm_server.enabled` branch + `llama_models_json` (via `yq`)
- `llmenv omniroute combo-context` + `combo_models_json` (via `jq`)
- merge into `models_json`, write to a private file
- OmniRoute health check (`curl .../api/monitoring/health`)

Then: `source "${REPO_DIR}/setup/lib/install-agent-clients.sh"` and one
call: `install_agent_clients "$base_url" "$api_key_file" "$models_file" "${REPO_DIR}/setup/update-opencode-config.mjs" "${REPO_DIR}/setup/lib/install-agent-clients.sh"`.

### `pylib/remote_setup.py` (trimmed `SETUP_SCRIPT_TEMPLATE`)

Keeps exactly what's genuinely remote:
- command-availability checks (`curl jq node mktemp` — no `yq`/`uv`, those
  aren't needed remotely)
- master-key prompt, `auth.conf`, `curl` to `/config`, parse
  `base_url`/`api_key`/`models_json`/`omniroute_dashboard_*` out of the
  response

Then: the same heredoc-embedding mechanism as the JS updater — a new
`@@INSTALL_LIB_SH@@` placeholder, `render_setup_script()` reads
`setup/lib/install-agent-clients.sh` from disk (already available inside
the container: `pylib/` and `setup/update-opencode-config.mjs` are already
bind-mounted read-only; `setup/lib/` gets the same treatment in
`pylib/compose.py`'s `remote_setup_service["volumes"]`) and substitutes it
in. The generated script writes that embedded content to
`${workdir}/install-agent-clients.sh` (same pattern already used for the
JS updater's own heredoc), sources it from there, and calls
`install_agent_clients` with the remote-obtained values plus that path as
`$5`. Then the OmniRoute dashboard banner lines (which stay remote-only —
the local script has no analogous "here's the admin password" step).

`--rm-key` argument parsing happens before any of this, at the very top of
the generated script (see "Master key caching" below).

## Fixes already applied (hotfixed live 2026-08-25, ahead of this refactor)

Two real bugs surfaced while testing today's combo changes on a genuinely
stale `remote-setup` container and were fixed directly in the current
(pre-unification) files, since the user was actively blocked:

1. **`Done. Model(s): ` printed empty** — the final summary line in
   `SETUP_SCRIPT_TEMPLATE` read a `.alias` field that no longer exists on
   the `id`/`label` model shape (a leftover from the earlier combo-mapping
   refactor that only updated the *other* `.alias` reads). Fixed to `.id`,
   matching the local script's own per-model log line.
2. **`staged_files[@]: unbound variable` crash on macOS** — bash < 4.4
   (macOS's stock `/bin/bash` is 3.2) raises "unbound variable" for
   `"${staged_files[@]}"` under `set -u` once the array is legitimately
   empty (which it is by the time `cleanup()` fires on a successful exit,
   after every element has been consumed by the `staged_files=(
   "${staged_files[@]:1}")` slices). Fixed in both `cleanup()`
   implementations to the portable `"${staged_files[@]-}"` idiom.

Both fixes carry forward as-is into `setup/lib/install-agent-clients.sh` —
no further action needed beyond moving the already-fixed code into the
shared file.

## Richer, unified summary output

Confirmed with the user 2026-08-25: neither script's final summary today
prints per-model context size, and the two summaries should look — and be
generated by — the same code, not just superficially similar text. Since
the final `echo` summary block is already moving into
`install_agent_clients()` (see Architecture above), this is a content
change to that one block rather than a second parallel change:

- Per-model lines gain context size, sourced from the existing `ctx_size`
  field already in `$MODELS_FILE` — e.g.
  `enabled model: my-planning (context 500000)` instead of today's bare
  `enabled model: my-planning`.
- The remote-only final `Done. Model(s): ...` line is retired in favor of
  the same per-model loop the local script already uses (today's
  single-line joined summary is the one piece of remote-specific
  formatting that has no local equivalent; unifying it means deleting it,
  not porting it).
- Config-file-path lines (`configured Pi provider: ...`, `configured
  OpenCode provider: ...`, etc.) are unchanged in content, just now
  emitted by the shared function on both paths instead of duplicated.

## Master key caching (remote-only)

Confirmed with the user 2026-08-25: today's remote installer prompts for
`OMNI_ROUTER_MASTER_KEY` on every single run, which is unnecessary
friction for routine re-runs (e.g. picking up a combo context-size
change). This is remote-only — the local script never has a master-key
prompt at all, since it talks to OmniRoute directly via
`llmenv omniroute issue-key`.

- **Cache location:** `${XDG_CACHE_HOME:-$HOME/.cache}/llm-env/master-key`
  on the remote client machine, `chmod 600`, directory `chmod 700` —
  matching this script's existing file-permission discipline for
  sensitive material (the `umask 077` at the top plus explicit `chmod`
  everywhere else).
- **TTL: 7 days**, checked via the cache file's mtime. On every run
  (absent `--rm-key`): if the cache file exists and its mtime is within 7
  days, use its contents directly (no prompt); otherwise prompt via
  `/dev/tty` as today and write the freshly entered key to the cache
  (creating the directory if needed) before proceeding.
- **`--rm-key` flag:** `curl http://host/setup.sh | bash -s -- --rm-key`.
  Semantics (confirmed with the user): delete any existing cached key,
  prompt fresh via `/dev/tty` for this run, use that value to complete
  setup normally, and do **not** write it back to the cache — the run
  starts and ends with no cached key on disk. (Argument parsing needs
  `bash -s --` in the documented invocation, called out in `network.sh`'s
  existing remote-install instructions and in the script's own `--help`
  text, both updated as part of the plan.)
- The cached value is used for the `/config` HTTP call exactly as today's
  freshly typed key is — `/config` itself is still called live on every
  run regardless of cache hit, so combo/context changes are always picked
  up; only the *prompt* is skipped.

## Data flow (both paths converge on the same function)

```
LOCAL:  models.yml + OmniRoute (live) -> base_url/api_key/models_json (bash vars/files)
                                                    |
REMOTE: /config (HTTP, master-key auth) -> base_url/api_key/models_json (bash vars/files)
                                                    |
                                                    v
                                install_agent_clients(...)  [setup/lib/install-agent-clients.sh]
                                                    |
                                                    v
                    Pi models.json / Pi settings.json / OpenCode config(s) / OpenCode model state
```

## Testing

- `setup/lib/install-agent-clients.sh` gets its own `shellcheck` pass (add
  to `tools/validate.sh`'s existing glob if it isn't picked up
  automatically).
- No behavioral test changes expected: `tests/test_shell.py`'s
  `setup-local-llm-agents.sh` tests and `tests/test_remote_setup.py`'s
  end-to-end pty-driven `setup.sh` execution test both exercise the
  *observable* outcome (files written, their content, error messages) —
  unchanged by this refactor, so they serve as the regression net. Any
  test currently asserting on internal script structure (e.g. matching a
  specific jq snippet by substring, per the current
  `test_render_setup_script_sends_omniroute_routed_model_ids` style) needs
  updating to match wherever that logic now lives (the shared library
  file's content) instead of `SETUP_SCRIPT_TEMPLATE`'s.
- New test: a small direct test that sources
  `setup/lib/install-agent-clients.sh` in isolation (no full script run)
  and calls `install_agent_clients` with fixture inputs, to catch breakage
  in the shared library without needing to run either full wrapper script.

## Staleness diagnosability: version marker

Today's live bug (crash on a container running yesterday's code) was
purely operational — `make start` was not re-run after code/config
changes, and the `Type=oneshot`/`RemainAfterExit=yes` wrapper unit means a
`systemctl --user start` on an already-active unit is a no-op, silently
leaving the old container running. This is orthogonal to the duplication
problem (unifying the scripts doesn't prevent running stale code) but is
directly related to what surfaced it today. **Confirmed with the user
2026-08-25: include.**

- One mechanism, computed inside `install_agent_clients()` itself from its
  own `$5 LIB_PATH` argument (already materialized to a real file on both
  paths — see the function signature above) — not a second, separate
  computation in `render_setup_script()`. `git rev-parse` was considered
  and rejected: the container has no `.git` directory available (only
  `pylib/` and two specific files are bind-mounted, not the whole repo),
  so it wouldn't work remotely anyway, and a content hash of the one file
  that actually determines behavior is more precise than a repo-wide
  commit hash regardless.
- Hash command: `sha256sum` (GNU coreutils, present on this Linux host)
  with a `shasum -a 256` fallback — macOS ships `shasum`, not
  `sha256sum`, another instance of the same bash-3.2-style portability
  concern already driving the `staged_files` fix above. First 8 hex chars
  of the digest.
- Because both paths materialize `install-agent-clients.sh` to a real file
  before sourcing it (the repo file locally, the heredoc-written temp file
  remotely) and pass that same path as `$5`, the *identical* hash
  computation runs on both paths by construction — a mismatch between what
  a remote machine reports and what the local repo currently has is
  visible by comparing the two outputs directly, with no separate
  server-side/client-side logic to keep in sync.
- Output line: `Installer version: <8-hex-char-hash>` in the final summary
  block.
