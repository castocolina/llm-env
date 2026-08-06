# CLI and Script Hygiene Design

## Goal

Reduce duplication and fragility across the bash scripts, make chained
`make` targets legible, cut check output down to what's relevant by
default, and give the Python dev environment a real, reproducible setup
path.

## Scope

This change covers:

- Parametrizing values currently hardcoded in more than one place
  (image references, health/timeout constants, the `UNIT`/`UNIT_NAME`
  duplication)
- Colored, bold start/end banners around every `make` target, including
  composite ones like `restart`
- Shared helpers in `tools/lib.sh` (health-wait, config-field loading) to
  remove duplicated logic
- A standing principle — logic moves to `pylib/`, bash stays orchestration
  — applied to the check scripts' output
- Concise-by-default output for `check-with-agents.sh`'s failure path,
  keeping full raw detail available via the existing
  `LLM_ENV_KEEP_CHECK_ARTIFACTS=1` escape hatch
- A `pyproject.toml` (pinned `pyyaml`, dev group of `pytest`/`ruff`) and a
  new `make dev-setup` target that bootstraps `uv` correctly and runs
  `uv sync`

This change does not cover CI/CD (explicitly out of scope per user
direction — local `make validate`/`make test` on each commit is
sufficient for now) and does not touch the container/OmniRoute work,
which is its own spec.

## Make Target Banners

True terminal font-size is not something ANSI escape codes can request —
that's a terminal-emulator/font setting, not a per-line program request —
so "distinctive and bold" is implemented as bold, bright-colored banner
text, not literally larger text.

A new `tools/run-target.sh <name> -- <command...>` wraps every target:

```
▶ start                                    (bold bright cyan, at start)
...target output...
■ start end — ok (4.2s)                    (bold bright green, on success)
■ start end — failed, exit 1 (1.8s)        (bold bright red, on failure)
```

It reuses and extends the existing `GREEN`/`YELLOW`/`BLUE`/`RED`/`NC`
constants in `tools/lib.sh` with a `BOLD` variant, times the run, and
propagates the wrapped command's exit code so `make`'s existing
stop-on-first-failure behavior across chained targets is unchanged — this
is purely a legibility layer, not a change to error handling.

Every simple target becomes a one-line recipe:

```make
start:
	@bash tools/run-target.sh start -- bash scripts/start.sh
```

Composite targets like `restart: stop start` do not get a seam to inject
a banner between phases under plain prerequisite chaining, so they switch
to explicit recursive calls:

```make
restart:
	@$(MAKE) --no-print-directory stop
	@$(MAKE) --no-print-directory start
```

Each sub-invocation carries its own start/end banner, so a chained
`make stop start check-server` (or `restart`) reads as clearly bounded
phases instead of one undifferentiated stream. This stays within the
existing "Makefile target bodies longer than 3 lines must delegate to a
`.sh` file" rule, since the banner logic itself lives in
`tools/run-target.sh`.

## Parametrization

- `UNIT`/`UNIT_NAME`: the Makefile's `status`/`logs` targets currently
  redeclare `UNIT = llm-server` independently of `tools/lib.sh`'s
  `UNIT_NAME`. `status`/`logs` become one-line delegations to new
  `scripts/status.sh`/`scripts/logs.sh` files, like every other target,
  sourcing `tools/lib.sh`'s `UNIT_NAME` instead of the Makefile's own
  copy — removing the second declaration entirely rather than keeping two
  code paths for the same value.
- Image references (`ghcr.io/ggml-org/llama.cpp:server-vulkan`,
  `:server`): currently hardcoded independently in
  `scripts/benchmark.sh`, `setup/setup.sh`, and `scripts/clean.sh`, while
  `models.yml.example` already carries `gpu.image` as the canonical
  field. `clean.sh` in particular removes hardcoded image names rather
  than reading `gpu.image` from the config it is about to delete, so a
  repointed image is never actually cleaned up. Centralize the two known
  image constants in `tools/lib.sh` (`VULKAN_IMAGE`/`CPU_IMAGE`, used
  during bootstrap before a config exists) and have `clean.sh` read
  `gpu.image` from the config first, falling back to those constants only
  when no config is present.
- Timeouts: the health-poll loop in `start.sh` (60 × 1s) and the
  independent poll loop generated into the mDNS unit in
  `render-unit.sh` both hardcode 60 with no shared source and no
  override. Centralize as `LLM_ENV_HEALTH_TIMEOUT_SECONDS` (default 60)
  in `tools/lib.sh`, consumed by both.

## Reuse

- `wait_for_health(port, timeout)` in `tools/lib.sh`, replacing the
  duplicated poll loops in `start.sh` and the systemd-unit heredoc where
  it can be sourced (the generated mDNS unit's `ExecStartPre` runs outside
  a bash context that can source this repo's files, but it should at
  least reference the same timeout constant instead of an independent
  literal).
- `load_server_config()` in `tools/lib.sh`, setting `PORT`/`API_KEY`/
  `HOST` from a single pass over the config, replacing roughly a dozen
  separate `yq -r '.server.port'`/`.server.api_key'` invocations spread
  across `start.sh`, `render-unit.sh`, `check-server.sh`,
  `check-with-agents.sh`, `setup-local-llm-agents.sh`, and `network.sh`.

## Bash/Python Boundary

Standing principle, applied here rather than treated as a separate
rewrite pass: bash stays thin process-orchestration glue (`podman`,
`systemctl`, `curl`, control flow); anything that is actually logic —
parsing, validation, shaping data, deciding what to extract from a
response — moves into `pylib/`. This extends the split
`.agents/architecture.md` already documents; it does not replace working
bash with Python wholesale.

## Check Output Simplification

`check-with-agents.sh`'s success path already does the right thing: the
JSONL-to-final-text extraction (`check-with-agents.sh:431-435` for Pi,
`487-494` for OpenCode) already discards tool-call/reasoning framing and
keeps only the assistant's final answer. The verbosity complaint is
specifically the failure path — `log_file_excerpt "Client JSONL
transcript"` (`check-with-agents.sh:706`) dumps up to 256KB of raw stream
on any failure, mixing genuine diagnostic signal with routine stream
framing.

A new `pylib/transcript.py` classifies JSONL transcript lines by client
type (Pi vs. OpenCode) into: the final assistant text (already extracted),
error/tool-error events, and everything else. On failure, the terminal
output shows only the final text plus any error/tool-error events found —
not the full transcript. The complete raw transcript remains on disk
exactly as it does today via `LLM_ENV_KEEP_CHECK_ARTIFACTS=1`
(`tools/lib.sh:131-190`), so this is strictly "concise by default, full
detail one env var away," not a regression back to showing nothing.

## `uv` Bootstrap and `dev-setup`

On the reference machine, `uv` is actually installed at
`~/.local/bin/uv` — not as an rpm-ostree package, despite
`prerequisites.sh` currently treating it as one (`RUNTIME=("uv:uv" ...)`,
falling back to `sudo rpm-ostree install uv` if missing). That fallback
does not reflect how `uv` is actually obtained and would impose an
unnecessary reboot even if a `uv` rpm-ostree package exists. `uv` is
pulled out of the rpm-ostree-managed `RUNTIME` array; a new prerequisites
step checks `command -v uv` and, if missing, runs the official installer
(`curl -LsSf https://astral.sh/uv/install.sh | sh`) behind the same
confirm-before-installing prompt every other prerequisite uses. Everything
else in `RUNTIME`/`DEVELOPMENT` (podman, jq, yq, curl, iproute, git,
shellcheck, node) stays on rpm-ostree, since those genuinely need to be OS
packages.

A new `pyproject.toml` declares `pyyaml>=6.0` as a project dependency
(matching what `llmenv.py`'s inline PEP 723 metadata already declares) and
a dev dependency group of `pytest`/`ruff`. This gives `uv sync` something
real to materialize into `.venv` and produces a `uv.lock`, so tool
versions stop silently drifting between machines and runs — today,
`uvx ruff check` and `uv run --with pytest` both run completely unpinned.

A new target, chained after the existing one:

```make
dev-setup: prerequisites
	@bash tools/run-target.sh dev-setup -- bash setup/dev-setup.sh
```

`setup/dev-setup.sh` bootstraps `uv` per above if missing, then runs
`uv sync`, which also provisions the pinned Python interpreter itself —
no system `python3` package required. This stays separate from
`prerequisites`, since regular `make setup`/`make start` users need the
runtime tools but never need a synced dev venv with `pytest`/`ruff` in it.

Once `pyproject.toml` exists, `make test` and `make validate` simplify
from their current ad hoc invocations (`uv run --with pytest pytest
tests/ -v`, `uvx ruff check llmenv.py pylib tests`) to plain `uv run
pytest tests/ -v` / `uv run ruff check llmenv.py pylib tests`, running
against the synced, version-locked `.venv`.

## Tests

Add or update tests for:

- `tools/run-target.sh` banner output and exit-code propagation on both
  success and failure
- `wait_for_health()` and `load_server_config()` behavior (timeout,
  malformed config)
- `clean.sh` removing the image actually recorded in `gpu.image`, not a
  hardcoded literal
- `pylib/transcript.py` classification: final text extraction unchanged,
  error/tool-error events surfaced, routine framing events dropped
- `check-with-agents.sh` failure output is the filtered subset by
  default, and the full raw transcript is still written to disk under
  `LLM_ENV_KEEP_CHECK_ARTIFACTS=1`
- `dev-setup` bootstrapping `uv` only when absent, and `uv sync` producing
  a working `.venv` with `pytest`/`ruff` available

Run `make validate` and `make test` after implementation.
