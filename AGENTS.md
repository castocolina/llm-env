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
make resources               # Show the per-service CPU/RAM policy, active overrides and live cgroup counters (OK/WATCH/ALERT)
make resources-benchmark SERVICE=omniroute  # Measure a container to re-derive its limits (omniroute|attach|report)
make start                   # Start the server (or omniroute + remote-setup only); installs the OmniRoute healer timer
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
make update-omniroute          # Idle-aware OmniRoute-only update (defers unless idle and verified; first characterizes an unprofiled running image into ~/.local/state/llm-env/omniroute-profiles; a stopped/absent container is updated without waiting)
make migrate-remote-setup      # One-time remote-setup recreation without its OmniRoute dependency
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
- `OMNIROUTE_HEAL_MEMORY_MIB` (default: 60 % of the running container's
  `memory.max`, 1228 MiB at the 2048 MiB default; an absolute MiB value
  overrides it; `0` disables),
  `OMNIROUTE_HEAL_MEMORY_SAMPLES` (default 3) and
  `OMNIROUTE_HEAL_MEMORY_COOLDOWN_HOURS` (default 6) — the memory recycle in
  `scripts/heal-omniroute-admission.sh` (the 5-minute `llm-env-omniroute-heal`
  timer `make start` installs). Besides clearing a stuck admission lease, it
  restarts OmniRoute only when it is idle and its host-read cgroup memory
  (minus file cache) stays above the threshold for that many runs, at most
  once per cooldown and twice per rolling 24 h. Invariant (tested): threshold
  < guard high (0.85·cap) < guard critical (0.92·cap) < cap, so an idle
  bloated process is recycled before a burst pushes it into 503 shedding. Unknown activity or
  unreadable memory never restarts. Set them with a drop-in
  (`systemctl --user edit llm-env-omniroute-heal.service`, `[Service]`
  `Environment=` lines); `make start` regenerates the unit itself.
- `LLM_ENV_SYSTEM_ONE_HEALTH_TIMEOUT_SECONDS` — seconds `make start` waits
  for System One to become healthy (default 120; positive integer). Weights
  are on disk after `make setup`; this bounds load-to-GPU only.

### Resource overrides

Per-service CPU/RAM policy defaults live in `pylib/resources.py`
(`DEFAULT_POLICY`). Persistent changes go in `models.yml` under
`resources.overrides.<service>` (`omniroute`, `remote_setup`, `system_one`,
`phoenix`). The old generated `resources.<service>` mirror is retired and
dropped on load (`resources.llm_server` stays).
Manage overrides with `uv run llmenv.py resource-override show|set|check|clear`.
`make resources` also prints, per running container and read-only from its host
cgroup (no exec), uptime, memory current/peak/cap and their ratios, swap, memory.events
high/max/oom/oom_kill, memory PSI, average CPU cores and (System One) VRAM, with one
verdict: ALERT when `oom_kill > 0`, peak/cap >= 0.92 or PSI full avg300 >= 5; WATCH when
events `max > 0`, peak/cap >= 0.85, PSI some avg300 >= 10 or the configured cap differs
from the cgroup's `memory.max` (restart pending); otherwise OK. Counters start over when a
container is recreated, hence the uptime column. `uv run llmenv.py resources --live`
adds the same data to the JSON under `live`.
Values must be positive (0 would mean "no limit"). `set` and `clear` only edit
`models.yml`; running containers keep their current limits until you apply the
change. `bash setup/render-unit.sh` re-renders the compose file and unit for the
next start or boot and does not touch running containers (boot starts the last
rendered file). To apply to the running stack, run `make restart`: it stops and
starts OmniRoute, remote-setup, System One and (when System One is enabled)
Phoenix, so in-flight requests are interrupted, System One reloads its weights
before it is healthy, and every container begins a new cgroup lifetime
(`memory.peak` and counters start over). Run it when no agent
session is using OmniRoute.

Sizing rule (owner, 2026-10-24): `memory_min_mib` = the resident
`memory.current` level kept under use (modal, not mean) + 20 %;
`memory_max_mib` = the highest observed resident level or `memory.peak` + 20 %.
Swap is reported, never added. Lowering a cap below its default is allowed:
`resource-override set|check` and `make resources` print a warning pointing at
`make resources-benchmark`. A lower cap releases no RAM -- it only bounds growth.

`memory_min_mib` renders as `mem_reservation` (cgroup `memory.low`). On this host
it has no effect: the ancestor cgroups have `memory.low` 0, so the protection
never propagates. It only feeds the budget arithmetic (sum of reservations
against host RAM).

`system_one` is an explicit exception to the sizing rule: its `memory.peak`
is clipped by its own 4608 MiB cap (166 `memory.events` max), so "peak + 20 %"
cannot be measured, and raising the cap only adds host pressure while its hot
set is ~3.87 GiB. Revisit it with a benchmark under a temporarily higher cap.

#### Re-deriving limits

1. Run `make resources-benchmark SERVICE=omniroute` (a throwaway container with
   a fake upstream: cold start -> first requests -> rps ramp -> cooldown), or
   `make resources-benchmark SERVICE=attach CONTAINERS=<name> RUN='<cmd>'` for
   a running container (the peak is the run window only). Results land in
   `tmp/resource-benchmark/`; `SERVICE=report FILE=<json>` recomputes them.
2. Phases under 90 % ok are marked SATURATED: they still count for the peak but
   not for the reservation. The fake upstream serves ~3 req/s, so faster ramp
   phases are saturation, not load.
3. Read `recommend` (+20 % already applied). Apply it with
   `uv run llmenv.py resource-override set --service <service> --memory-max-mib N ...`, or
   for everyone by editing `DEFAULT_POLICY` in `pylib/resources.py` (plus its
   tests and the research doc).
4. `bash setup/render-unit.sh`, then recreate only the affected service when it
   is idle (or `make restart` when no agent session uses OmniRoute).

## Rules

After editing any `.sh` file, run `make validate`.
After editing any `.py` file, run `make validate && make test`.

Makefile target bodies longer than 3 lines must delegate to a `.sh` file.

Python is invoked only as `uv run llmenv.py <subcommand>`.

### External Agent Runs

Monitor every delegated run (`pi -p`, `dispatch-reviewer`, background
subagents) until it ends: launch detached with output under `./tmp/`, poll
output files and `ps` by PID at least every 5 minutes (never `pgrep -f` /
`pkill -f` with a self-matching pattern), and treat a stalled file, an exited
process or a process question as a stall to act on at once. Report run state
to the user in every status message.

### Test Processes and Containers

Every process, container, volume or temp directory started for a test,
benchmark or experiment is stopped and removed by the same run before it
ends, on success and on failure (`try`/`finally` or a trap). Verify nothing
is left (`podman ps -a`, `ps` by PID) before reporting; never ask whether to
clean up what you started. Only resources you did not start need approval.

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
