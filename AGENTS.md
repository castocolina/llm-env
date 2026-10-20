# Project Agent Instructions

## LLM Environment

Local llama.cpp router server on Bazzite, running as a rootless podman
compose stack with GPU acceleration, fronted by an OmniRoute gateway that
`make start` auto-configures against the local router. Configuration lives
in `models.yml`.

## Commands

```bash
make help                    # List all targets
make prerequisites           # Confirm and install Bazzite/Fedora host tools
make setup                   # Interactive configuration (GPU inference is opt-in)
make system-one-build        # Build the local System One (Laya) GPU image
make system-one-benchmark    # Benchmark the running System One server and record the load table
make start                   # Start the server (or omniroute + remote-setup only)
make stop                    # Stop the server
make restart                 # Stop then start
make check-setup             # Offline validation
make check-server            # Online API contract validation
make check-with-agents       # Live Pi/OpenCode weather and USD-to-CLP checks
make benchmark                # Measure Vulkan throughput; CPU fallback exits nonzero
make gpu-status                # Show which process (if any) holds the GPU
make codex-provision           # Import local Codex CLI session into OmniRoute (match by account email)
make provider-provision        # Alias for codex-provision (Codex-only)
make enable-boot              # Start at boot (opt-in)
make disable-boot             # Disable start at boot
make status                   # Show service status and print endpoints/credentials
make show-secrets              # Print endpoints/credentials without starting anything
make logs                      # Tail service logs
make setup-local-llm-agents    # Configure normal Pi/OpenCode client profiles
make key-reset                 # Rotate the server API key
make backup                    # Back up OmniRoute combos + provider connection metadata (before prune/clean)
make prune                     # Delete downloaded model files (not removed by clean)
make dev-setup                 # Install dev-only tooling
uv tool install pre-commit && pre-commit install --hook-type pre-commit --hook-type pre-push  # one-time: enable the git hooks below
make validate                  # shellcheck + ruff + pyright
make test                      # Python test suite
make clean                     # Remove everything
```

### Environment variables

- `LLM_ENV_ASSUME_YES=1` — run `make setup` / `make clean` unattended,
  accepting defaults instead of prompting. `make gpu-status` is the
  exception: for its two remediation prompts specifically, this means
  "always decline", not "auto-accept" — those are the only prompts in the
  codebase that mutate the host outside the repo's own config (desktop
  launcher overrides, environment.d files).
- `LLM_ENV_ROTATE_KEY=1` — force `make setup` to rotate the API key instead
  of keeping the existing one.
- `LLM_ENV_NO_GPU=1` — seed `make setup`'s GPU-inference prompt default to
  "no" for unattended runs, configuring this host as an OmniRoute gateway +
  remote installer only (no local llama.cpp instance). Persisted as
  `llm_server.enabled` in `models.yml`; flip it and re-run `make setup`/
  `make start` to change modes later.
- `LLM_ENV_SYSTEM_ONE=1|0` — seed `make setup`'s System One prompt for
  unattended runs. Persisted as `system_one.enabled` in `models.yml`.
  Requires `/dev/kfd` (AMD ROCm).
- `LLM_ENV_SYSTEM_ONE_HEALTH_TIMEOUT_SECONDS` — seconds `make start` waits
  for System One to become healthy (default 120; positive integer). Weights
  are on disk after `make setup`; this bounds load-to-GPU only.

## Rules

After editing any `.sh` file, run `make validate`.
After editing any `.py` file, run `make validate && make test`.

Makefile target bodies longer than 3 lines must delegate to a `.sh` file.

Python is invoked only as `uv run llmenv.py <subcommand>`.

### Language

All output must be in English regardless of input language.

### Research Before Implementation

Do not assume. Before implementing anything that is not 100% clear:

1. Research the platform, API, or tool behavior first
2. Verify assumptions with documentation or experiments
3. If something is unclear or you are guessing, ask the user

Silent failures from unresearched assumptions waste more time than asking.

Never hardcode a value that can be measured. GPU device, VRAM totals, and
compositor usage are all detected at runtime. Benchmarking uses Vulkan only;
an unsuccessful Vulkan benchmark configures CPU fallback and exits nonzero.

### Scratch vs Durable Artifacts

- `.pi/` is local agent metadata and is gitignored by default. Any important
  shared exception requires explicit justification and approval; none is
  needed for the current session's agent definitions.
- Durable Superpowers designs, plans, specs, research, and review docs live
  ONLY under `docs/superpowers/`.
- Ephemeral artifacts — investigation reports, prompts, fixtures, logs,
  snapshots, diffs, session handoffs, and scratch assets generated for a
  task — must be created under the repository's `./tmp/` (already ignored),
  not scattered in `.claude/`, `.superpowers/`, or host `/tmp/` and other
  external state. This applies to NEW task artifacts.
- Checked-in product test fixtures are durable code/test assets, not
  ephemeral. They stay in their proper tests/docker paths and must not be
  moved into `./tmp/`.

## Detailed Instructions

- Retain all plans, including dormant and superseded plans; delete a plan only with explicit user approval.
- Work directly in the current checkout unless isolation is necessary; if a worktree is necessary, use `.claude/worktrees/`.
- [Architecture](.agents/architecture.md)
