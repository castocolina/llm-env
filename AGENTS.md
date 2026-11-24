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
make resources-snapshot      # Point-in-time snapshot: per-service CPU/RAM policy, overrides and live cgroup counters (OK/WATCH/ALERT, read-only, no load)
make resources-benchmark SERVICE=omniroute  # Measure a container to re-derive its limits (omniroute|attach|report)
make start                   # Start whatever is not running (never stops a running container); installs the OmniRoute healer timer
make stop                    # Stop the whole stack
make restart                 # Stop everything, re-render and start (applies config changes)
make start-llama            # Start llm-server only; needs the stack running (unit active) and llm_server.enabled true; the rest is left alone
make stop-llama             # Stop llm-server only; the container is kept (never removed)
make llama-enable MODEL=<alias>  # Config only: enable local llm-server with one model, backing up models.yml first
make llama-disable          # Config only: disable local llm-server, backing up models.yml first (apply with make restart)
make check-setup             # Offline validation (includes the profile drift check)
make check-server            # Online API contract validation
make profile-apply           # Merge the tracked llm-env.profile.yml onto models.yml (backup; no-op when equal)
make profile-check           # Read-only drift report between models.yml and the profile (exit 1 on drift)
make firewall [DRY_RUN=1]    # Open the LAN ports of enabled services per profile network.firewall (needs sudo; adds only, never removes)
make check-with-agents       # Live Pi/OpenCode weather and USD-to-CLP checks
make benchmark                # Measure Vulkan throughput; CPU fallback exits nonzero; run only after make stop-llama (see below)
make gpu-status                # Show which process (if any) holds the GPU
make codex-provision           # Import local Codex CLI session into OmniRoute (match by account email)
make provider-provision        # Alias for codex-provision (Codex-only)
make enable-boot              # Start at boot (opt-in)
make disable-boot             # Disable start at boot
make status                   # Show service status and print endpoints/credentials
make show-secrets              # Print endpoints/credentials without starting anything
make update-omniroute          # Idle-aware OmniRoute-only update; applies a changed omniroute service definition too, once idle (defers unless idle and verified; first characterizes an unprofiled running image into ~/.local/state/llm-env/omniroute-profiles; a stopped/absent container is updated without waiting)
make update-system-one [LAYA_VERSION=x.y.z]  # Idle-aware Laya update: build candidate, CPU smoke, swap system-one when /health is idle, roll back on failure, rewrite the Dockerfile pin; at the pinned version it applies a re-rendered system_one config change (drift) once idle
make migrate-remote-setup      # One-time remote-setup recreation without its OmniRoute dependency
make logs                      # Tail service logs
make setup-local-llm-agents    # Configure normal Pi/OpenCode client profiles
make key-reset                 # Rotate the server API key
make model-report MODEL=<alias>  # One-page plain-text account of the model from its card (also saved as capacity/<alias>.report.md)
make model-capacity MODEL=<alias> [DEPTH=<bench.json>] [SCORE=<score.json>] [PROFILE=<latency.json>]  # Write/show the measured capacity card (~/.config/llm-env/capacity/<alias>.json)
make model-interference  # Gate: does the local model slow System One or raise container memory events
make local-combo         # Create/update the local-only OmniRoute combo my-local-worker from the card (also stores its output limit in OmniRoute and registers the local models on the llama-cpp provider page)
make backup                    # Back up OmniRoute combos + provider connection metadata (before prune/clean)
make prune                     # Delete downloaded model files (not removed by clean)
make prune-stale-images [DRY_RUN=1] [STALE_DAYS=90]  # Remove this stack's stale images plus other projects' unused images created >= STALE_DAYS days ago (creation date stands in for last use; 0 = stack only), one plain podman rmi at a time; DRY_RUN=1 only lists (other projects' images untouched)
make dev-setup                 # Install dev-only tooling, pre-commit's commit hook and the .git/hooks/pre-push link
uv tool install pre-commit && make dev-setup  # one-time: enable the git hooks below
make validate                  # shellcheck + ruff + pyright
make test                      # The only command whose purpose is the full suite (capped, offline, leak-checked)
make clean                     # Remove everything
```

`make benchmark` rewrites `gpu.backend` and `gpu.image` in the live `models.yml` when the
Vulkan benchmark fails, and gives contended or failing results while llm-server holds the GPU.
Run it only after `make stop-llama`. Then take a fresh timestamped backup right before the benchmark: `cp -p ~/.config/llm-env/models.yml ~/.config/llm-env/models.yml.$(date +%Y%m%dT%H%M%S).bak`. Do not reuse the backup `make llama-enable` took earlier: it may be stale, and restoring it would undo the enable edit,
and afterwards verify that
`yq '.gpu.backend, .gpu.image' ~/.config/llm-env/models.yml` is unchanged (restore from the
backup and report if it is not). Prefer the skill's `bench-fit.sh` for screening.

### Hooks

`make dev-setup` installs pre-commit's commit hook and links `.git/hooks/pre-push` to `tools/pre-push.sh`
(it exits 1 when `core.hooksPath` is set). At commit: shellcheck and ruff on the staged files in
`tools/validate.sh`'s scope, pyright on the whole project when the index adds, changes, deletes or renames a `.py`
file, `pyproject.toml` or `uv.lock`, then the tests selected from the staged diff without the slow ones
(`tools/test-changed.sh --stage commit`). At both stages `tools/test-changed.sh` runs the ownership lint
(`uv run llmenv.py selection-lint`) in parallel with the selected tests, every commit and push, and fails when
either fails. At commit that last hook fails with "run make dev-setup" while the pre-push link is
missing or not executable. At push: `tools/validate.sh`, then the tests selected from the pushed range for a
fast-forward of the checked-out branch (by name or `HEAD`) to itself, or `tools/test.sh` (the full suite) for a new
branch. A dirty index or worktree fails with "push refused: the index or worktree is dirty: commit or stash your
changes, then push again" (`make test` would test the worktree, not the pushed commit). Any other push (several
refs, a tag or other destination, a rewind) fails with "push shape not supported by the hook: run make test, then git
push --no-verify". The only bypasses are `git commit|push
--no-verify` and, at commit, `SKIP=<hook-id>`. `SELECT_ALL=1` makes either stage select every test file.

### Environment variables

`llm-env.profile.yml` (tracked, non-secret; see `llm-env.profile.example.yml` and
`docs/new-host.md`) wins over the `LLM_ENV_*` seeds below for every key it names; `make setup`
does not prompt for those keys. `LLM_ENV_PROFILE` and `LLM_ENV_ENV_FILE` relocate the profile and
`<repo>/.env` (tests point them away from the checkout).

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
  `llm_server.enabled` in `models.yml`; switch modes later with `make llama-disable`, then
  `make restart`; to go back to local, `make llama-enable MODEL=<alias>`, then
  `make setup` (gateway-only setup skipped GPU detection, model download and
  validation), then `make restart`.
- `LLM_ENV_SYSTEM_ONE=1|0` — seed `make setup`'s System One prompt for
  unattended runs. Persisted as `system_one.enabled` in `models.yml`.
  Requires `/dev/kfd` (AMD ROCm). `system_one.listen` (`loopback` default,
  or `lan`) publishes Laya on `127.0.0.1:<port>` or `0.0.0.0:<port>`; Laya
  has no authentication, so `lan` exposes it to the whole network. Apply with
  `bash setup/render-unit.sh`, then `make update-system-one` (idle-gated drift).
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
- `LLM_ENV_TEST_CPU_PCT` — share of the CPUs `make test` / `tools/test.sh` gives pytest
  workers (`max(1, floor(nproc * pct / 100))`, default 40; an integer 1-100 without leading
  zeros, else exit 2).
- `LLM_ENV_TEST_TIMEOUT_SECONDS` — bound on the pytest chain of one `tools/test.sh` run, from its
  start until it ends (default 1800; a positive integer without leading zeros, else exit 2). On
  expiry the chain is stopped and the runner prints `timeout: test run exceeded <N> s` and exits
  124. The Podman inventory snapshots before and after it, pyright and `validate.sh` are outside the bound. A node that cannot start
  its threads (the Konsole scope's `pids.max` hit by other sessions) aborts or waits forever.
- `LLM_ENV_SCOUT_OUT` — output root for the run directories that
  `scripts/model-interference.sh` (`model-interference/<stamp>-<pid>`) and the
  skill's `quant-headroom.sh check` (`model-tune/quant-<stamp>-<pid>`) write.
  Default `<repo>/tmp`; the tests point it at a temp dir.

### Resource overrides

Per-service CPU/RAM policy defaults live in `pylib/resources.py`
(`DEFAULT_POLICY`). Persistent changes go in `models.yml` under
`resources.overrides.<service>` (`omniroute`, `remote_setup`, `system_one`). The old generated `resources.<service>` mirror is retired and
dropped on load (`resources.llm_server` stays).
Manage overrides with `uv run llmenv.py resource-override show|set|check|clear`.
`make resources-snapshot` also prints, per running container and read-only from its host
cgroup (no exec), in two live tables (memory; pressure and activity), uptime, memory current/peak/cap and their ratios, swap, memory.events
high/max/oom/oom_kill, memory PSI, average CPU cores and (System One) VRAM, with one
verdict: ALERT when `oom_kill > 0`, peak/cap >= 0.92 or PSI full avg300 >= 5; WATCH when
events `max > 0`, peak/cap >= 0.85, PSI some avg300 >= 10 or the configured cap differs
from the cgroup's `memory.max` (restart pending); otherwise OK. Counters start over when a
container is recreated, hence the uptime column. `uv run llmenv.py resources --live`
adds the same data to the JSON under `live`.
Values must be positive (0 would mean "no limit"). `set` and `clear` only edit
`models.yml`; running containers keep their current limits until you apply the
change. `bash setup/render-unit.sh` re-renders the compose file and unit for the
next `make restart` or boot and does not touch running containers (boot starts the last
rendered file). For an `omniroute` change, run `make update-omniroute`: it
recreates only OmniRoute, once it has no load. To apply to the whole running stack, run `make restart`: it stops and
starts OmniRoute, remote-setup and System One, so in-flight requests are interrupted, System One reloads its weights
before it is healthy, and every container begins a new cgroup lifetime
(`memory.peak` and counters start over). Run it when no agent
session is using OmniRoute.

Sizing rule (owner, 2026-10-24): `memory_min_mib` = the resident
`memory.current` level kept under use (modal, not mean) + 20 %;
`memory_max_mib` = the highest observed resident level or `memory.peak` + 20 %.
Swap is reported, never added. Lowering a cap below its default is allowed:
`resource-override set|check` and `make resources-snapshot` print a warning pointing at
`make resources-benchmark`. A lower cap releases no RAM -- it only bounds growth.

`memory_min_mib` renders as `mem_reservation` (cgroup `memory.low`). On this host
it has no effect: the ancestor cgroups have `memory.low` 0, so the protection
never propagates. It only feeds the budget arithmetic (sum of reservations
against host RAM).

`system_one` is an explicit exception to the sizing rule: its lifetime
`memory.peak` (4,587 MiB) was clipped at the old 4608 MiB cap (166
`memory.events` max), so "peak + 20 %" could not be measured. The cap was raised
4608 -> 5120 MiB on 2026-10-24 to let the true peak show. The peak is a
model-load transient (idle ~2.5 GiB, loaded ~3.9 GiB resident, peak at
(re)load). Revisit it after a few days of `make resources-snapshot` data.

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
4. `bash setup/render-unit.sh`, then `make update-omniroute` for OmniRoute (it
   waits for idle), `make update-system-one` for System One (same idle-gated
   drift path); for another service, recreate it when it is idle (or
   `make restart` when no agent session uses OmniRoute).

## Rules

Experiments using `podman system reset|prune|migrate` or any `--root`/`--runroot` isolation must also isolate `--tmpdir`, `--network-config-dir`, `CONTAINERS_CONF`, `CONTAINERS_STORAGE_CONF` and `XDG_RUNTIME_DIR` (or use a separate user), and never run in the owner's session. On 2026-11-20, containers kept running while Podman reported them exited; container DNS broke.
Such an experiment removes its private graph root in the same run's trap with `podman unshare rm -rf <dir>`: a plain `rm -rf` cannot enter the overlay layers, which belong to subuids (this left a 2.8 GB `tmp/stop-723` that the owner could not delete).
Local image builds (`system-one-build.sh`, `system-one-observer-build.sh`, `update-system-one.sh`) call `prune_superseded_build_images` from `tools/lib.sh` when they finish; it removes only untagged images carrying the `llm-env.gpu-arch` or `llm-env.span-observer-sha256` label, never a blanket `podman image prune` (the untagged previous OmniRoute image is a rollback copy).

After editing any `.sh` file, run `make validate`.
After editing any `.py` file, run `make validate && make test`.

Makefile target bodies longer than 3 lines must delegate to a `.sh` file.

Python is invoked only as `uv run llmenv.py <subcommand>`.

Never restart or recreate the omniroute container by hand (`podman restart`, `podman compose up --force-recreate`).
After an OmniRoute config change (template, env, limits, ports) run `bash setup/render-unit.sh`, then
`make update-omniroute`: it applies the change once OmniRoute has no load and may defer for hours.

### External Agent Runs

Monitor every delegated run (`pi -p`, `dispatch-reviewer`, background
subagents) until it ends: launch detached with output under `./tmp/`, poll
output files and `ps` by PID at least every 5 minutes (never `pgrep -f` /
`pkill -f` with a self-matching pattern), and treat a stalled file, an exited
process or a process question as a stall to act on at once. Report run state
to the user in every status message.

When an external run fails against the router (transient error or quota/rate-limit), the orchestrator
manages the session itself: retry the same lane up to 3 times, then change session (the combo picks
a member again), up to 3 session changes. Only if the error persists after those 3 changes report it
with evidence (status, combo, model, timestamps), then wait several minutes before trying or
reporting again. Never switch harness, provider or model on your own initiative; that is the user's
call. The detailed ladder lives in the runner (ai-kit's cross-ai executor), not here.

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
- pytest's own temp dir (`tmp_path`) is the exception: `tools/test.sh` gives
  each run its own `TMPDIR` under `/tmp/pytest-of-$USER/` (outside the repo).
  Never pass `--basetemp` under `./tmp/`, and keep `TMPDIR` outside `./tmp/`.

## Detailed Instructions

- Retain all plans, including dormant and superseded plans; delete a plan only with explicit user approval.
- Work directly in the current checkout unless isolation is necessary; if a worktree is necessary, use `.claude/worktrees/`.
- [Architecture](.agents/architecture.md)
