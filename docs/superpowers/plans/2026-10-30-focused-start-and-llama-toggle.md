# Focused Start, Full Restart, llm-server-only Lifecycle and Llama Toggle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `make start` becomes non-disruptive (it only brings up what is not running); `make restart` keeps today's full stop, re-render and start; `make stop` stays the only full stop; `make start-llama` / `make stop-llama` act on the `llm-server` container only; `make llama-enable MODEL=<alias>` / `make llama-disable` edit `~/.config/llm-env/models.yml` atomically after a timestamped backup; the project-local skill `onboard-local-model` uses these targets for its live phase, documents bypass-mode gates and the layered, isolated, delegated benchmark.

**Architecture:**
- `make restart` does not change in the Makefile (`$(MAKE) stop` then `$(MAKE) start`). `make stop` removes every container of the project in both unit states: ExecStop (`down --remove-orphans`) when the unit is active, a direct `podman compose -f $COMPOSE_FILE down --remove-orphans` when the unit is inactive and the compose file exists (Decision 8; covers a unit that failed while its containers kept running). After it nothing of the project runs, so the new non-disruptive start brings up the whole stack: stop everything, re-render, start everything, exactly what today's `make start` does. The existing restart tests stay as they are.
- `scripts/start.sh` takes an optional scope argument: none means the whole stack; `llm-server` is used by `start-llama`. Both scopes share one budget gate (the existing parser, unchanged), one render step and one OmniRoute provisioning path. Its direct `up` calls carry `--no-recreate --no-deps --pull=never` (Decision 7).
- `scripts/stop.sh` takes an optional scope `llm-server` (used by `stop-llama`); that branch runs `podman stop llm-server` and nothing else.
- New `scripts/llama-config.sh enable <alias> | disable` takes the backup, then edits a private temp copy (migrate, `models select`, `yq`) and renames it over the live file only when every step succeeded: a rejected alias writes nothing. The generated wrapper unit gets `ExecStart=podman compose -f docker-compose.yml up -d --no-recreate` and `ExecStop=podman compose -f docker-compose.yml down --remove-orphans`. New helper in `tools/lib.sh`: `container_running <name>`.

**Tech Stack:** bash + shellcheck, `podman` 5.8.9 rootless, `/usr/bin/podman-compose` 1.6.0, `yq`, `jq`, Python 3 (`uv run llmenv.py`), pytest (stub-based tests in `tests/test_shell.py`, skill tests in `tests/skills/onboard-local-model/`), ruff, pyright.

**Spec:** `/var/home/bazzite/git/llm-env/tmp/focused-start/design.md` (the design as produced by the design agent). Reviews: `/var/home/bazzite/git/llm-env/tmp/focused-start/review-round-1.md`, `/var/home/bazzite/git/llm-env/tmp/focused-start/review-round-2.md`. This plan carries every fact, test and check of all three, so it is self-contained if `tmp/` is cleaned.

## Global Constraints

Copied from `/var/home/bazzite/git/llm-env/AGENTS.md`; every task includes these.

- After editing any `.sh` file, run `make validate`. After editing any `.py` file, run `make validate && make test`.
- `make validate`'s shellcheck glob does not cover `.claude/skills/onboard-local-model/scripts/`; after editing a skill script also run `shellcheck -s bash .claude/skills/onboard-local-model/scripts/*.sh`.
- Makefile target bodies longer than 3 lines must delegate to a `.sh` file: new targets use `bash tools/run-target.sh <name> -- bash scripts/<x>.sh`.
- Python is invoked only as `uv run llmenv.py <subcommand>`.
- Never restart or recreate the omniroute container by hand (`podman restart`, `podman compose up --force-recreate`). Nothing in this plan or its tests may do that on the live stack; live acceptance is the owner's (Task 9).
- Every process, container, volume or temp directory started for a test, benchmark or experiment is stopped and removed by the same run (`trap`), on success and failure; verify with `podman ps -a` before reporting.
- Scratch output under `./tmp/` only; durable plans under `docs/superpowers/` only; never delete or rename existing plans.
- Never copy the live `models.yml` (it holds secrets) into `tmp/` or anywhere else for experiments; tests use a temp HOME.
- Never hardcode a value that can be measured (GPU device, VRAM totals); the alias reaches `llmenv models select` only as an argv value, never interpolated into a `yq` program (architecture invariant).
- All output in English. Work directly in the current checkout. Commit steps append the attribution trailer that the executing session's own instructions require.

## Decisions (open questions resolved)

The six open questions of the design are resolved with its recommended defaults, except Decision 3, which round 1 found contradictory and the replan settled with the behaviour the review coordinator recommended. Decisions 7 and 8 come from round 2.

1. `llama-enable` / `llama-disable` are config only: they do not call `stop-llama` / `start-llama`; they print the exact next command.
2. ExecStart gets `--no-recreate` and boot after an unclean shutdown may start leftover exited containers without a fresh render. Accepted and documented (`make restart` re-renders). Without it, `make start` on an inactive unit with leftover running containers could recreate them and break "never stops".
3. `start-llama` when llm-server is already running: no budget, no resources, no render, no `up`. It waits for llm-server `/health` up to `LLM_ENV_HEALTH_TIMEOUT_SECONDS`. Healthy: provisions `llm-env-local` (idempotent), prints "llm-server is already running; run 'make stop-llama' first to load a changed model", exit 0. Unhealthy: exit 1; it never stops, restarts or recreates the container. One health-failure path serves both this case and a failed fresh `start-llama`: after the existing `log_error` and `Logs:` lines it prints, on stdout, exactly this **Recover line** (the only recovery text in this plan; the table rows, Task 4 and its tests quote it verbatim):
   `  Recover: make stop-llama, restore the backup printed by make llama-enable if the model changed, then make start-llama`
4. Budget double counting (Fact 8) when System One runs and the compositor is on the dGPU: accepted (fails safe: refuses, never over-commits) and documented. Fixing it needs a `pylib/budget.py` change subtracting System One's live VRAM (`system_one_process_vram` exists in `tools/lib.sh`); a separate task if the owner wants it, out of scope here.
5. Definition drift of running containers: one fixed hint line, printed at the end of both `start` and `start-llama`, no drift computation (podman-compose 1.6 has no CLI printing the config hash; `pylib/omniroute_update.py` drift logic could be generalized later).
6. Whether llama.cpp router mode re-reads `presets.ini` while running: assumed only at process start. Task 0 records a check; if it is wrong, render `presets.ini` only when llm-server is not running (Task 3 adds that guard conditionally, see Task 0 acceptance).
7. The direct `up` calls in `start.sh` (active unit in `make start`, and `start-llama`) pass `--pull=never` (Fact 12): podman-compose then skips its pre-pull of every service's image, so an active `make start` makes no registry call and cannot fail on an unreachable registry for containers it will not create. A container that must be created still gets its image through `podman create`'s own default (`missing`). The unit's ExecStart (inactive unit, boot) keeps the default pre-pull, as today. Task 0 (h) gates it.
8. `make stop` with the unit inactive and the compose file present runs `podman compose -f "$COMPOSE_FILE" down --remove-orphans` directly. Without it, a unit that failed (ExecStart hit `TimeoutStartSec=300` while podman-compose waited on `depends_on`, Fact 2) leaves running containers that `make stop` ignores and the new `--no-recreate` ExecStart never recreates, so `make restart` would succeed without applying any config (today's ExecStart recreates them). Task 0 (g) gates it, with the unit flags.

## Facts this plan relies on

Read from the installed sources: podman 5.8.9, `/usr/bin/podman-compose` 1.6.0 (`/usr/lib/python3.14/site-packages/podman_compose.py`), `pylib/compose.py`, `setup/render-unit.sh`, `scripts/start.sh`. Re-check them if code has moved (Task 0 re-checks the behavioural ones).

1. **`up` without `--no-recreate` is disruptive** (`compose_up` starts at line 3733; its recreate branch is lines 3757-3796). Any existing container whose `io.podman.compose.config-hash` label differs from the current render is torn down and recreated, and **its running dependents are torn down with it**. omniroute depends on llm-server, so `podman compose up -d llm-server` after a render that changed llm-server would also recreate OmniRoute. With `--no-recreate` existing containers are never torn down; missing ones are created and every non-excluded container gets `podman start`, which does nothing to a running container (Task 0 confirms).
2. **`depends_on` waits without a timeout.** `run_container` calls `check_dep_conditions`, which loops `podman wait --condition=healthy` with no timeout (lines 3519-3542). If llm-server never becomes healthy, a bare `up` that starts omniroute hangs. Inside the unit this is capped by `TimeoutStartSec=300`; a direct call from `start.sh` has no cap. `--no-deps` skips that wait (`deps_from_container`) and creates new containers without `--requires` (line 1315).
3. **`down <service>` also stops dependents.** `compose_down` excludes by `DEPENDENTS` (line 3956), so `podman compose down llm-server` also stops and removes omniroute. Never use it here. `compose stop <svc>` stops only the named service but fails when the service is missing from the rendered file (for example after `llama-disable` plus a render); that is why `stop-llama` calls `podman stop llm-server` directly.
4. **Native podman dependency.** podman-compose creates omniroute with `--requires=llm-server` unless `--no-deps` was used (line 1317). The unit's `up -d` and today's start create it that way; `make update-omniroute`'s commit worker recreates it with `--no-deps`, without the dependency. So the running omniroute may or may not carry `--requires`. When it does: `podman rm llm-server` is refused while omniroute exists; `podman start omniroute` on a stopped omniroute starts llm-server first (on a running omniroute: Task 0 (c2)); the effect of `podman restart omniroute` (what the healer runs, `scripts/heal-omniroute-admission.sh:143,244`) while llm-server is stopped is checked in Task 0.
5. **The unit's state is not container state.** `llm-server.service` is `Type=oneshot` with `RemainAfterExit=yes`. `systemctl --user start` on an active unit does nothing (ExecStart does not run again). `start-llama` and `stop-llama` never touch the unit: it stays `active` whether llm-server runs or not, as it already does when a container crashes. `make stop` still works because it checks `is-active` and runs ExecStop; when the unit is inactive (never started, or `failed` after a start timeout with containers still running) it now runs `down --remove-orphans` directly (Decision 8).
6. **The compose project is `llm-env`**, the name of the compose file's directory, for the unit (`WorkingDirectory` plus `-f basename`) and for `podman compose -f "$COMPOSE_FILE"`. Direct calls and the unit manage the same containers.
7. **`presets.ini` is rewritten in place** (`Path.write_text`, same inode) and bind-mounted read-only into llm-server. A render while llm-server runs changes the file the running container sees. llama-server reads it when the process starts (assumption, Decision 6), so model changes apply on the next llm-server start.
8. **Budget arithmetic** (`llmenv.py cmd_budget`): `compositor_used` is the GPU's whole `vram_used_mib` when the compositor uses the same GPU, and `extra_reserved_mib` always adds `system_one.vram_reserve_mib`. If System One runs during the measurement and the compositor is on the dGPU, System One's VRAM is counted twice. This fails safe (refusal), never over-commit.
9. **Render is not files only.** `setup/render-unit.sh` rewrites `presets.ini`, the compose file, the wrapper and mDNS units and runs `systemctl --user daemon-reload`; when `gpu.device_name` is set (and the backend is not cpu) it runs a throwaway `podman run --rm --device /dev/dri ... --list-devices` probe container; when System One is enabled it runs `phoenix.sh prepare` and builds the observer image. None of these stops or recreates a running container.
10. **`LLAMA_ARG_MODELS_MAX` is baked into the llm-server container** (`pylib/compose.py:103`) from `runtime.models_max`. `models select <one alias>` sets `models_max = min(configured, 1)` (`pylib/config.py` `sync_models_max`), so a single-alias `llama-enable` can only keep or lower it. `start-llama` reuses a stopped container (`--no-recreate`), so the stale value stays until `make restart`; harmless here, and covered by the Decision 5 hint.
11. **`models select` validates before saving.** An unknown alias raises `ConfigError` in `set_enabled_models`; `llmenv.py` prints `{"error": "unknown model alias: <alias>"}` and exits 1 without writing (verified on a fixture copy). A successful select writes atomically (`save_config`: temp file in the same directory as the `--config` path, `os.replace` onto that path, mode 0600) and prints `{"models_max": N}`. `migrate-config` prints `{"written": <bool>, "path": ...}` and saves through the same `save_config` onto its `--config` path only. With `--config <temp copy>` neither command can touch the live file (reproduced 2026-10-30 with the lifecycle helper's stubs; the uv stub logs to `$CALLS`, never to stdout).
12. **`up` pre-pulls images before it looks at existing containers.** podman-compose 1.6 `compose_up` calls `prepare_images` (line 3736) before the recreate branch. On podman >= 5.6 (host: 5.8.9) that runs `podman pull --policy <p>` for every non-excluded service whose image is not `localhost/...` (`pull_images`, lines 3614-3648), with `p` = the `--pull` option, else the service's `pull_policy`, else `missing`. The rendered compose sets no `pull_policy`, so an image present locally is never re-fetched, but a tag missing locally (for example `docker.io/diegosouzapw/omniroute:latest`, `pylib/compose.py:130-131`) is fetched, and an unreachable registry fails the whole `up` before `--no-recreate` is considered. `--pull=never` skips that pre-pull (`pull_image`, line 3604); `--pull` only reaches the pre-pull and build, never `podman create`, which pulls a missing image itself with its own default `missing` (`container_to_args` adds `--pull` only from a service's `pull_policy`, line 1443).

## Target names and semantics

| Target | Script | Semantics |
|---|---|---|
| `make start` | `scripts/start.sh` | Non-disruptive. Prechecks: migrate config, key and secrets, System One image/volume/weights check (read-only). **Never stops a container** (the old "Stopping the active router" block and the System-One-disabled `phoenix.sh stop` are removed). Budget gate (existing parser, `start.sh:52-73`, unchanged) runs only when llm-server is enabled **and not running**; failure stops before render and start and nothing changes. Then resources and render (Fact 9: rewrites files and units, may run a throwaway GPU probe container, never touches a running container). If the unit is active: `podman compose -f $COMPOSE_FILE up -d --no-recreate --no-deps --pull=never` (Decision 7: no registry call). If inactive: `systemctl --user start llm-server.service` (its ExecStart now has `--no-recreate`). Then the health waits, Phoenix start (idempotent), `network.sh`, timers, OmniRoute provisioning and codex-provision, as today. Ends with one hint: "containers already running keep their definition; run 'make restart' to apply config changes". |
| `make restart` | unchanged (`stop` then `start`) | Full stop (Phoenix, then compose `down --remove-orphans` through ExecStop or, with the unit inactive, directly), re-render, start everything. Interrupts in-flight requests. |
| `make stop` | `scripts/stop.sh` | The only full stop. Unit active: unchanged (ExecStop, now `down --remove-orphans`). Unit inactive and compose file present: `podman compose -f "$COMPOSE_FILE" down --remove-orphans` directly, after `log_warn "server unit is not active; removing any containers it left running"` (Decision 8). Unit inactive, no compose file: the warning only. |
| `make start-llama` | `scripts/start.sh llm-server` | Refuses unless `llm_server.enabled=true` ("run 'make llama-enable MODEL=<alias>' first") and the unit is active ("the stack is not running; run 'make start'"). Not running: same budget gate, resources and render, then `podman compose -f $COMPOSE_FILE up -d --no-recreate --no-deps --pull=never llm-server`. Already running: Decision 3 (no budget, render or `up`). Both: wait for llm-server `/health`; on failure exit 1 and print the Recover line of Decision 3 (`  Recover: make stop-llama, restore the backup printed by make llama-enable if the model changed, then make start-llama`; container left as it is, never restarted); on success wait for the OmniRoute port and provision `llm-env-local` (idempotent). Skips the System One check and wait, Phoenix, `network.sh`, timers and codex-provision. Ends with the Decision 5 hint. Leaves OmniRoute, remote-setup, System One and Phoenix alone. |
| `make stop-llama` | `scripts/stop.sh llm-server` | `podman stop llm-server` if running (`log_info "llm-server stopped"`), otherwise `log_warn "llm-server is not running"` (stderr) and exit 0. Never `rm` (refused under `--requires`; the stopped container is reused by `start-llama`). Unit, OmniRoute, remote-setup, System One and Phoenix untouched. |
| `make llama-enable MODEL=<alias>` | `scripts/llama-config.sh enable <alias>` | Usage check (empty alias: usage naming `MODEL=`, no backup). Then `cp -p models.yml models.yml.<YYYYmmddTHHMMSS>.bak` (0600; if that name exists it dies with exactly `backup <path> already exists; retry in a second`, exit 1, nothing written) and prints its path. Then, on a private temp copy in the same directory: `llmenv migrate-config`, `llmenv models select <alias>` (validates the alias), `yq` `.llm_server.enabled = true`; only if all three succeed, `chmod 600` and `mv` the copy over `models.yml` (atomic rename). Any failure removes the copy, prints llmenv's `.error`, exits 1 and leaves `models.yml` byte-identical. Config only; prints the next step. |
| `make llama-disable` | `scripts/llama-config.sh disable` | Same backup and temp-copy flow with `.llm_server.enabled = false` (no select). Config only; prints: "llm-server keeps running until `make stop-llama` (frees the GPU now) or `make restart` (applies gateway-only mode to the whole stack)". |

Model swap without touching the gateway: `make stop-llama && make llama-enable MODEL=<alias> && make start-llama`.

### How OmniRoute's depends_on behaves when only llm-server stops or starts

- The compose `depends_on: service_healthy` is checked only when podman-compose itself starts omniroute. A running OmniRoute never checks it again, and `--no-deps` skips it. Stopping llm-server leaves OmniRoute up; requests to `llama-cpp/*` fail upstream until llm-server is healthy again (combos fall through to other members if they have any).
- Native `--requires` (when present): `podman stop llm-server` is expected to leave omniroute running (Task 0 confirms). Because `podman rm llm-server` is refused, llm-server cannot be recreated without OmniRoute. Changes to llm-server's container definition (`LLAMA_ARG_MODELS_MAX`, `LLAMA_API_KEY`, image, port, cpus/memory) therefore apply only through `make restart`. A model swap changes `presets.ini`, a bind mount re-read when the process starts, so `start-llama` covers it; `LLAMA_ARG_MODELS_MAX` keeps its baked value (Fact 10), which is why `start-llama` ends with the `make restart` hint.
- Healer: with llm-server stopped and `--requires` present, the healer's `podman restart omniroute` either fails (no restart, logged) or starts llm-server too. Task 0 (d) decides which and the docs (Task 7) and the skill's benchmark window (Task 8) state it. Either way `stop-llama` is a temporary stop; persistent gateway-only mode is `make llama-disable && make restart`.
- `make start` after `stop-llama`: with llm-server still enabled, the `--no-recreate` up starts it again by name (by design: start brings up what is not running). After `stop-llama` plus `llama-disable`, llm-server is no longer in the compose file, but the up runs `podman start omniroute` on the running OmniRoute; whether that starts a stopped `--requires` dependency is Task 0 (c2). Either way gateway-only mode is applied by `make restart`, which `llama-disable` already prints.
- Gateway-only to local: if OmniRoute was created while `llm_server.enabled=false` it has no dependency. `start-llama` creates llm-server on the same `llm-env_default` network, so OmniRoute reaches it by DNS name; provisioning creates the connection. OmniRoute's definition then drifts (`depends_on`), which is harmless and applied by the next `make restart` or `make update-omniroute`.

## Failure modes and rollbacks

| Failure | Behaviour | Rollback / recovery |
|---|---|---|
| Budget infeasible (start or start-llama) | Prints the shortfall and remedies, dies before render and start; no container touched | `make llama-enable MODEL=<smaller>` and retry. False refusal from double counting (Fact 8): `make restart` measures with everything stopped. |
| llm-server unhealthy after start-llama, or already running but unhealthy | Waits up to the health timeout, exit 1, prints the `Logs:` line and the Recover line; container left as it is (never stopped, restarted or recreated); OmniRoute untouched | The Recover line (Decision 3): `Recover: make stop-llama, restore the backup printed by make llama-enable if the model changed, then make start-llama` |
| `models select` rejects the alias | Dies with llmenv's error; `models.yml` byte-identical (the temp copy is discarded); backup printed | none needed; the backup is there for later edits |
| `make start` with pending definition drift (key regenerated, limits changed, flag flipped) | Running containers keep their old definition; final hint printed | `make restart` (or `make update-omniroute` for OmniRoute alone) |
| `llama-disable` then `make start`, llm-server still running and now missing from the compose file | Left running (non-disruptive) | `make stop-llama` now; `make stop` / `make restart` remove it through `--remove-orphans` |
| System One disabled, then `make start` while Phoenix runs | Phoenix keeps running (start no longer stops it) | `make restart` (`stop.sh` stops Phoenix even when System One is disabled) |
| `make restart` precheck fails after the stop (missing System One image, budget) | Stack stays down; same as today's `make restart` | Fix and `make start` |
| Unit inactive, containers left running (unit failed earlier, e.g. ExecStart timed out) | `make start`: `systemctl start` then `up -d --no-recreate`, nothing recreated, running containers keep their old definition | `make restart`: `make stop` sees the inactive unit and runs `down --remove-orphans` directly (Decision 8), then start recreates everything |
| `stop-llama`, `llama-disable`, then `make start` while OmniRoute carries `--requires` | Depends on Task 0 (c2): llm-server stays stopped, or `podman start omniroute` starts it again | `make restart` applies gateway-only mode (printed by `llama-disable`) |
| Active `make start` while an image tag is missing locally or the registry is unreachable | No pre-pull (`--pull=never`, Decision 7): running containers untouched, exit 0; a container that must be created pulls its image through `podman create` and fails only if that image is unavailable | Retry when the registry is reachable |
| Boot after an unclean shutdown leaves exited containers | ExecStart `--no-recreate` restarts them as they were, without a render made since | `make restart` (Decision 2) |
| mDNS unit restarts while llm-server is stopped | Its ExecStartPre polls llm-server `/health` and fails after the timeout | Clears on `start-llama`; documented |

## Review Focus

Failure modes the design implies that are most likely to bite. Each has its test in the task in brackets.

1. `make start` on an active stack where llm-server's definition changed must not recreate llm-server or OmniRoute and must make no registry call: the recorded call is exactly `up -d --no-recreate --no-deps --pull=never`, with no `systemctl --user stop|start` and no compose `down` [Task 3].
2. A budget refusal in `start` or `start-llama` must leave no `render-unit.sh` call, no `up`, no `systemctl --user start|stop`; both tests assert all three [Task 3, Task 4].
3. `llama-enable` with an unknown alias must leave the live `models.yml` byte-identical to the printed backup and leave no temp file; with an empty alias no backup at all [Task 5].
4. `start-llama` on a running-but-unhealthy llm-server must wait, exit 1 with the full Recover line on stdout, and never `podman stop|restart` it or call `up` [Task 4].
5. `make restart` after a unit failure that left containers running must still recreate them: `make stop` with an inactive unit and a compose file records `podman compose -f <file> down --remove-orphans` [Task 2].

The round-2 item 5 (`stop-llama` with llm-server absent from the rendered compose file) stays pinned by `test_stop_llama_does_not_need_llm_server_in_the_compose_file` [Task 1].

Round-3 reviewers, check also the round-2 changes: Decision 7 (`--pull=never` on the direct `up` calls, Fact 12, Task 0 (h)); Decision 8 (`stop.sh` inactive-unit `down --remove-orphans`, two Task 2 tests); the single Recover line (Decision 3, the start-llama row, the failure table, Task 4 Step 3 and both unhealthy tests quote it verbatim); Task 0 (c2) and its doc consequence; `llama-config.sh` capturing `migrate-config` output; the `_APPLY_HINT` wording in `llmenv.py` (Task 7); Task 8 commits nothing (the skill tree stays untracked) and its G2 threshold; the rejected findings R-C1, R-C2 and R-H3 and their evidence in "Review responses (round 2)".

## Review responses (round 1)

| Finding | Verdict | Evidence | Plan change |
|---|---|---|---|
| N-F1 key-reset test breaks after Task 3 | Agree | Stub answers `is-active` from fixed `ACTIVE` (`tests/test_shell.py:5165,5171`); `key-reset.sh:16-18` runs `stop.sh` then `start.sh` | Task 1 makes the stub stateful (`UNIT_ACTIVE_FILE`; `stop` removes it, `start` creates it); Task 3 keeps the test and strengthens it as a guard (stop before start, no direct `up`) |
| N-F2 backup-collision test unbuildable | Agree | `commands.mkdir()` and `config.parent.mkdir(parents=True)` without `exist_ok` (`tests/test_shell.py:5000,5005`); no `date` stub | Task 1: `exist_ok=True` on both and a `date` stub driven by `FIXED_DATE_STAMP`; Task 5 test calls the helper twice with the same stamp |
| N-F3 `evals.json` missing, `rg` gate cannot pass | Agree | `evals/evals.json:22,26` contain "compose service stop" and "podman compose stop llm-server" | Task 8 adds `evals.json` to Files, rewrites both, adds `test_evals_use_the_lifecycle_targets`; gate uses fixed strings |
| N-F4 final check before skill task | Agree | Old Task 8 Step 2 listed Task 9's files | Skill update is Task 8, final check is Task 9 |
| N-F5 Task 4 budget test weaker than Review Focus 2 | Agree | Old Task 4 test asserted only "no `up -d`" | Task 4 test also asserts no `render-unit.sh` and no `systemctl --user start|stop` |
| R-1 yq write runs after a rejected alias | Agree | `pylib/config.py` `set_enabled_models` raises; `llmenv.py` `fail()` exits 1 without saving; old Step 3 ran `yq -i` afterwards | Task 5: validate-then-write on a temp copy, atomic `mv`, nothing written on rejection, backup still printed; test asserts byte identity and no leftover file |
| R-2 undefined `warn` | Agree | Only `log_warn` exists (`tools/lib.sh:30`), writes to stderr | Task 1 names `log_warn`; the no-op test reads `result.stderr` |
| R-3 running-but-unhealthy contradiction | Agree | `container_running` is true for an unhealthy container; old Decision 3 and old Task 4 Step 3 disagreed | Coordinator-recommended behaviour in Decision 3: wait for health, then exit 1 with `make stop-llama`, never restart (the hint is the recovery path, the script itself never stops it); two Task 4 tests |
| Referee medium: "budget exceeded" may never print because `jq -e` on a missing `.remedies` aborts | Partly disagree | `start.sh:69` is `jq -r '.remedies // [] \| .[] ...'` (no `-e`, missing key handled); the stub JSON reaches `die "budget exceeded ..."` at `start.sh:70` | Task 3 states the parser is kept verbatim and its test also asserts the "VRAM short by 2000 MiB" line |
| N-F6 red count | Agree | `test_start_inactive_stack_starts_the_unit` passes today | Task 3 Step 2: five fail, two guards pass |
| N-F7 "render (files only)" | Agree | `setup/render-unit.sh:36,77-78,201` | Fact 9 and the `make start` row reworded |
| N-F8 `--remove-orphans` unverified | Agree | New ExecStop changes every `make stop` | Task 0 check (g); Task 2 gated on it |
| N-F9 stale `LLAMA_ARG_MODELS_MAX` | Agree, low impact | `pylib/compose.py:103`; single-alias select only keeps or lowers `models_max` | Fact 10; `start-llama` prints the restart hint (tested); Task 7 documents it |
| N-F10 vacuous doc test | Agree | AGENTS.md never said `make start` stops the stack | Task 7 tests assert exact strings, including the "for the next start or boot" removal (line-wrapped, so normalized) |
| N-F11 dead `rg` pattern | Agree | `models-yml.md:57` has a backtick after the comma | Gate uses `rg -F` fixed strings; Task 8 test asserts the exact old text is gone |
| N-F12 `a && b \|\| warn` | Agree | `podman stop` failure would print "not running" | Task 1: explicit if/else and `require_cmd podman` in the branch |

## Review responses (round 2)

Reproductions ran with the real `run_lifecycle_script` helper and its stubs, from scratch scripts under `tmp/focused-start/replan2/` (`r_c1.sh`, `r_c2.sh`, `test_repro.py`, `test_guard.py`); all three reproduction tests and the guard check passed on 2026-10-30.

| Finding | Verdict | Evidence | Plan change |
|---|---|---|---|
| R-C1 (critical) resources EXIT trap fires in the stub `sleep` and kills an active start at `jq` | Disagree | (1) The `sleep` stub (`tests/test_shell.py:5152-5158`) has a `#!/usr/bin/bash` shebang, so bash forks and `exec`s it; the child clears the EXIT trap before exec, so the parent's `trap 'rm -f "$resources_json"' EXIT` never runs there. Reproduced: `r_c1.sh` sets that trap, calls `wait_for_health` with `CURL_HEALTH_FAIL=1` and a 2 s timeout (two stub sleeps), then finds the file and reads it with `jq` (`FILE_STILL_THERE`, `REACHED_AFTER_WAIT`). (2) `start.sh` reads `$resources_json` last at line 105, before the render at 109; nothing reads it after any health wait. Decision 3's already-running path computes no resources at all | None to the trap (do not move or reset it). The sequence stays proven by `test_start_never_stops_an_active_stack` and the restart-hint test (active path reaches the `up` and the hint) and by `test_start_llama_leaves_an_unhealthy_server_running` (a stub `sleep` runs with the trap set, then the Recover line prints) |
| R-C2 (critical) `llama-enable` parses stub-polluted stdout and leaves the live file migrated | Disagree | The uv stub logs with `>> "$CALLS"` (`tests/test_shell.py:5112`), never to stdout; `models select` stdout is clean `{"models_max": 1}`. The plan parses stdout only on a non-zero exit. `save_config` replaces the `--config` path (the temp copy), never the live file (Fact 11). Reproduced with the Task 1 passthrough inserted after the logging line (`r_c2.sh`): alias `test` exits 0 and leaves only `models.yml` plus the backup; alias `nope` exits 1, stderr has `unknown model alias: nope`, live file byte-identical to the backup, no leftover file | Task 5 Step 3 also captures `migrate-config`'s JSON (it printed `{"written": true, ...}` to stdout) and dies with its `.error`; Fact 11 records the reproduction |
| N-H1 (high) inactive unit with running containers: `make restart` applies nothing | Agree | `stop.sh:18-23` only acts when `is-active`; the new ExecStart has `--no-recreate`; today's ExecStart recreates | Decision 8; Task 2 adds the direct `down --remove-orphans` to `stop.sh` with two tests; Architecture, restart and stop rows, failure table, Task 7 docs |
| R-H1 (high) active `make start` pre-pulls images | Partly agree | `prepare_images` runs before the recreate branch (Fact 12). Policy `missing` never replaces an image present locally, so the "image the next restart uses" changes only when the tag is already missing locally, which the next restart would fetch anyway; but an active start still makes registry calls and fails on an unreachable registry | Decision 7: `--pull=never` on both direct `up` calls, asserted by the exact recorded strings in Tasks 3 and 4; Task 0 (h) proves it offline and gates it |
| R-H2 (high) three recovery texts | Agree | Decision 3, Task 4 Step 3 and the failure table differed | One Recover line in Decision 3, quoted verbatim in the start-llama row, the failure table and Task 4 Step 3; both unhealthy tests assert the full line on stdout |
| R-H3 (high) prose guard rejects spans the plan keeps | Disagree | The guard, run on today's skill files (`test_guard.py`), finds 0 violations. The cited span at `SKILL.md:58-59` (`LLM_ENV_CONFIG=<scratch models.yml> bash $SK/scripts/bench-fit.sh ... --depths 0,32768,131072`) has no `;`, `&&` or `\|\|`; the spans in `models-yml.md:50-51` are `llm_server.enabled`, `false`, `LLM_ENV_ASSUME_YES=1`, `llm_server.enabled: true`. No span Task 8 writes has any of those tokens either | Task 8 Step 7 re-runs the guard after the edits and says how to fix a span that trips it (split it or move the logic into a script, never relax the guard) |
| M1 `podman start` of a running dependent may start its stopped `--requires` dependency | Agree | Not covered by Task 0 (c) | Task 0 (c2); semantics note and failure-table row; Task 7 wording |
| M2 `_APPLY_HINT` keeps "for the next start or boot" | Agree | `llmenv.py:473-478` | Task 7 updates it and its comment; `tests/test_cli.py` assertion |
| M3 Task 8 commits a test whose subjects are untracked | Agree | `make test` runs `pytest tests/` (`tools/test.sh`), which collects `tests/skills/` | Task 8 commits nothing; Task 9 reports the skill tree and its tests as untracked, with this plan's edits in them |
| M4 `wait-gpu-release.sh` threshold ambiguous | Agree | "the model's weights" had two readings and no margin | Task 8 Step 4: threshold = `.gpu.used_mib` minus the `llama-server` holder's `.vram_mib`, both from one `preflight.sh` run before `make stop-llama`, plus 512 MiB |
| M5 healer finding (d) not passed to the skill | Agree | `bench-fit.sh` checks llm-server only at launch | Task 8 consumes Task 0 (d) for step 5 and "Benchmark runs" |
| L1 Task 4 red/green count | Agree | `-k "... or unknown_scope"` also selects `test_stop_llama_rejects_an_unknown_scope` | Task 4 Step 2 names both passing tests |
| L2 Task 9 git status expectation, plan index | Agree | Pre-existing untracked and modified plan files | Task 9 Step 2 lists them; new step updates `docs/superpowers/plans/README.md` |
| L3 architecture "host reboot" row | Agree | `.agents/architecture.md:210` says "recreated" | Task 7 rewrites that row |
| Referee medium: `test_start_llama_refuses_when_stack_inactive` passes before Task 4 | Disagree | Before Task 4 the script ignores the scope and runs today's inactive start, exit 0; the test asserts `returncode != 0`, so it fails (red) | None (the second passer is the Task 1 test, see L1) |
| Referee medium: `MODEL=enable` accepted | Disagree | An empty `MODEL` expands to `enable ""` (usage error, exit 2, tested); `MODEL=enable` reaches `models select enable`, which rejects an unknown alias with nothing written (Fact 11), the same as any wrong alias | None |
| Referee medium: collision message not fixed in the table | Agree | Only Task 5 Step 3 had the string | Semantics table quotes it exactly |

---

### Task 0: Research spike (no repo changes)

Verifies the podman behaviours the design inferred from source, on throwaway containers only. No file in the repo changes; the only output is `./tmp/focused-start-spike/findings.txt`.

**Files:**
- Create: `./tmp/focused-start-spike/spike.sh` (throwaway, under `tmp/`, ignored), `./tmp/focused-start-spike/findings.txt`

**Interfaces:**
- Produces: findings (a)-(h) below, read by Tasks 2, 3, 4, 7 (doc wording) and 8 (finding (d)) and by Decisions 6 and 7.

- [ ] **Step 1: Write `spike.sh`** with a `trap` that removes every container, network and compose project it created and the spike image tag on success and failure. It uses throwaway names (`spike-a`, `spike-b`, `spike-orphan`, `spike-outside`, compose project `-p spike`), a tiny image already on the host (`podman images` first; never pull a large one) tagged `example.invalid/spike-img:latest` with `podman tag` (a registry-looking name, so podman-compose's pre-pull considers it; `localhost/` images are skipped, Fact 12), and a two-service compose file under `./tmp/focused-start-spike/` using that tag (B `depends_on: A` with `condition: service_healthy`). It must never reference `omniroute`, `llm-server`, `system-one` or the `llm-env` project.
- [ ] **Step 2: Run it and record each result in `findings.txt`:**
  (a) `podman stop A` while B (created with `--requires=A`) runs: B keeps running, exit 0.
  (b) `podman rm A` is refused while B exists.
  (c) `podman start` on a running container returns 0 and leaves `StartedAt` unchanged.
  (c2) `podman start B` while B (created with `--requires=A`) runs and A is stopped: does A stay stopped? Record A's state and B's `StartedAt`.
  (d) `podman restart B` while A is stopped: an error, or A started?
  (e) `podman-compose -p spike up -d --no-recreate --no-deps svc` with a changed definition keeps the container ID and starts an exited container; the same command without a service name creates a missing service, starts an exited one and leaves running ones with the same ID and `StartedAt`.
  (f) read-only check of llama.cpp: does router mode re-read the preset file while running (`llama-server --help` text, upstream README for `--models-preset`)? Record the finding or "not determinable offline".
  (g) `podman-compose -p spike down --remove-orphans` removes `spike-orphan` (a container labelled `io.podman.compose.project=spike` that the compose file does not define), leaves `spike-outside` (no project label) untouched, and a second `down --remove-orphans` with nothing left exits 0.
  (h) Pre-pull, through `podman compose -p spike -f <file>` (the wrapper `start.sh` uses), with the project up: remove only the tag (`podman untag <image> example.invalid/spike-img:latest`; the original tag keeps the image). (h1) `up -d --no-recreate --no-deps` without `--pull`: record the exit code and whether a `podman pull` of `example.invalid/spike-img:latest` was attempted (expected: attempted, fails on DNS, nonzero exit). (h2) The same with `--pull=never`: exit 0, no pull attempted, container IDs and `StartedAt` unchanged. (h3) Re-tag, `podman rm -f spike-b`, then `up -d --no-recreate --no-deps --pull=never`: exit 0 and `spike-b` exists again (`podman create` still works under `--pull=never`).
- [ ] **Step 3: Verify cleanup:** `podman ps -a --format '{{.Names}}'` shows no `spike-*` container; `podman network ls` and `podman volume ls` show no `spike` project resources; `podman images` shows no `example.invalid/spike-img` tag; check by PID anything the script backgrounded (no `pgrep -f`).
- [ ] **Step 4: Gate.** If (a), (c), (e), (g), (h2) or (h3) contradict this plan, stop and report; do not continue ((g) gates Task 2; (h2) and (h3) gate the `--pull=never` flag of Tasks 3 and 4, Decision 7). (c2) and (d) only change doc wording: (c2) in Task 7 (failure-table row "stop-llama, llama-disable, then make start"), (d) in Task 7 and Task 8. (h1) only confirms Fact 12. If (f) shows presets are re-read while running, Task 3 additionally skips the `presets.ini` render when `container_running llm-server` (add the test `test_start_skips_preset_render_when_llm_server_running`); otherwise Decision 6 stands.

No commit (nothing tracked changes).

---

### Task 1: Test scaffolding, `container_running`, `stop-llama` branch

**Files:**
- Modify: `tools/lib.sh`, `scripts/stop.sh`, `tests/test_shell.py` (`run_lifecycle_script` at line 4975 and new tests)

**Interfaces:**
- Produces: `container_running <name>` in `tools/lib.sh`: returns 0 when `podman inspect --format '{{.State.Running}}' <name>` prints `true` (non-existent container: 1, no stderr). Helper parameter `args: tuple[str, ...] = ()` running `["/usr/bin/bash", script, *args]`. Stub knobs: `PODMAN_LLM_SERVER_RUNNING=1` (podman), `BUDGET_INFEASIBLE=1` (uv), `FIXED_DATE_STAMP=<stamp>` (date), the stateful unit marker `UNIT_ACTIVE_FILE`; `uv ... models ...` runs the real llmenv; a second helper call on the same `tmp_path` works, and a test may create files under `tmp_path / "home/.config/llm-env"` before calling the helper. Used by Tasks 2-5.
- Consumes: the existing `run_lifecycle_script` helper and its stubs.

- [ ] **Step 1: Extend the helper and stubs** (no new assertions yet):
  - `args` parameter as above.
  - `commands.mkdir(exist_ok=True)` and `config.parent.mkdir(parents=True, exist_ok=True)`: a second call on the same `tmp_path` re-creates the stubs and rewrites the fixture config (Task 5 relies on it), and a pre-created config directory is accepted (Task 2 relies on it).
  - Stateful unit: drop the `ACTIVE` env var; set `UNIT_ACTIVE_FILE=str(tmp_path / "unit-active")` in the environment and create that file when `active=True`. The systemctl stub `case` becomes (after the `daemon-reload` arm): `*'is-active --quiet'*) [ -f "$UNIT_ACTIVE_FILE" ] ;;`, `*'--user stop llm-server.service'*) rm -f "$UNIT_ACTIVE_FILE" ;;`, `*'--user start llm-server.service'*) : > "$UNIT_ACTIVE_FILE" ;;`. Only two tests pass `active=True` today (`tests/test_shell.py:7461,7476`); both stay green.
  - podman stub: add `*'inspect --format'*'llm-server'*) [ "${PODMAN_LLM_SERVER_RUNNING:-}" = 1 ] && printf '%s\n' true ;;` to the existing `{{.State.Pid}}` / `system-one` `case`.
  - uv stub: right after the call-logging line add `case "$*" in *' models '*) exec "$REAL_UV" "$@" ;; esac` (needs spaces on both sides, so `--models-dir .../models` never matches); replace the existing `budget` line with one that, when `BUDGET_INFEASIBLE=1`, prints `{"available_mib":8000,"required_mib":10000,"shortfall_mib":2000,"vram_feasible":false,"ram_feasible":true,"remedies":["lower models_max"]}` and `exit 1`, else prints today's feasible JSON.
  - New `date` stub in `commands/`: when `FIXED_DATE_STAMP` is set print it and exit 0, else `exec /usr/bin/date "$@"`.

  Run `uv run pytest tests/test_shell.py -n auto -q`: all existing tests pass.
- [ ] **Step 2: Write the failing tests:**

```python
def test_stop_llama_stops_only_the_llm_server_container(tmp_path: pathlib.Path) -> None:
    result, _config, calls = run_lifecycle_script(
        tmp_path, "scripts/stop.sh", args=("llm-server",), active=True,
        env_overrides={"PODMAN_LLM_SERVER_RUNNING": "1"},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    recorded = calls.read_text()
    assert "podman stop llm-server\n" in recorded
    for absent in ("systemctl --user stop", "phoenix.sh stop", "podman rm", "compose"):
        assert absent not in recorded


def test_stop_llama_is_a_noop_when_not_running(tmp_path: pathlib.Path) -> None:
    result, _config, calls = run_lifecycle_script(
        tmp_path, "scripts/stop.sh", args=("llm-server",), active=True
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "llm-server is not running" in result.stderr  # log_warn writes to stderr
    assert "podman stop" not in calls.read_text()


def test_stop_llama_does_not_need_llm_server_in_the_compose_file(tmp_path: pathlib.Path) -> None:
    result, config, calls = run_lifecycle_script(
        tmp_path, "scripts/stop.sh", args=("llm-server",), llm_server_enabled=False,
        env_overrides={"PODMAN_LLM_SERVER_RUNNING": "1"},
    )
    assert not (config.parent / "docker-compose.yml").exists()
    assert result.returncode == 0, result.stdout + result.stderr
    recorded = calls.read_text()
    assert "podman stop llm-server\n" in recorded and "compose" not in recorded


def test_stop_llama_rejects_an_unknown_scope(tmp_path: pathlib.Path) -> None:
    result, _config, calls = run_lifecycle_script(
        tmp_path, "scripts/stop.sh", args=("omniroute",), active=True
    )
    assert result.returncode == 2
    assert "usage: stop.sh [llm-server]" in result.stderr
    assert "systemctl --user stop" not in calls.read_text()


def test_stop_stops_phoenix_without_removing_its_volume(tmp_path: pathlib.Path) -> None:
    result, _config, calls = run_lifecycle_script(
        tmp_path, "scripts/stop.sh", active=True, env_overrides={"PHOENIX_VOLUME_EXISTS": "1"}
    )
    assert result.returncode == 0, result.stdout + result.stderr
    recorded = calls.read_text()
    assert "scripts/phoenix.sh stop" in recorded
    assert "systemctl --user stop llm-server.service" in recorded
    assert "down -v" not in recorded and "volume rm" not in recorded
```

- [ ] **Step 3: Run** `uv run pytest tests/test_shell.py -k "stop_llama or stop_stops_phoenix" -q`. Expected: the four `stop_llama` tests FAIL (no scope handling in `stop.sh`); `test_stop_stops_phoenix_without_removing_its_volume` passes already (guard for the unchanged full stop).
- [ ] **Step 4: Implement** `container_running()` in `tools/lib.sh` (one line: compare the inspect output to `true`, stderr to `/dev/null`). In `scripts/stop.sh`, before `require_cmd systemctl`, a `case "${1:-}"`: empty runs the current body unchanged; `llm-server` runs `require_cmd podman`, then an explicit `if container_running llm-server; then podman stop llm-server >/dev/null; log_info "llm-server stopped"; else log_warn "llm-server is not running"; fi` and `exit 0`; anything else prints `usage: stop.sh [llm-server]` to stderr and exits 2.
- [ ] **Step 5: Verify:** `uv run pytest tests/test_shell.py -k "stop_llama or stop_stops_phoenix" -q` passes; `make validate && make test` passes.
- [ ] **Step 6: Commit**

```bash
git add tools/lib.sh scripts/stop.sh tests/test_shell.py
git commit -m "feat(lifecycle): add stop-llama scope to stop.sh and container_running helper"
```

---

### Task 2: Wrapper unit flags and the inactive-unit full stop

Gated on Task 0 finding (g).

**Files:**
- Modify: `setup/render-unit.sh` (unit heredoc, lines 193-194), `scripts/stop.sh` (full-stop branch, today lines 18-23), `tests/test_shell.py` (`test_render_unit_writes_a_compose_file_and_wrapper_unit`, line 4177, and two new tests)

**Interfaces:**
- Consumes: Task 1's helper (`exist_ok=True` config directory, stateful unit stub) and `stop.sh` scope `case`.
- Produces: unit text `ExecStart=podman compose -f docker-compose.yml up -d --no-recreate` and `ExecStop=podman compose -f docker-compose.yml down --remove-orphans` (Task 3 relies on ExecStart not recreating); `stop.sh` with no scope and an inactive unit runs `podman compose -f "$COMPOSE_FILE" down --remove-orphans` when that file exists (Decision 8; Task 7 documents it).

- [ ] **Step 1: Tighten one test, add two:**
  - `test_render_unit_writes_a_compose_file_and_wrapper_unit` asserts the exact lines `"ExecStart=podman compose -f docker-compose.yml up -d --no-recreate\n"` and `"ExecStop=podman compose -f docker-compose.yml down --remove-orphans\n"` are in the unit text.

```python
def test_stop_downs_leftover_containers_when_the_unit_is_inactive(tmp_path: pathlib.Path) -> None:
    compose_file = tmp_path / "home/.config/llm-env/docker-compose.yml"
    compose_file.parent.mkdir(parents=True)
    compose_file.write_text("services: {}\n")
    result, _config, calls = run_lifecycle_script(tmp_path, "scripts/stop.sh", active=False)
    assert result.returncode == 0, result.stdout + result.stderr
    recorded = calls.read_text()
    assert f"podman compose -f {compose_file} down --remove-orphans\n" in recorded
    assert "systemctl --user stop" not in recorded
    assert "server unit is not active" in result.stderr


def test_stop_inactive_without_a_compose_file_runs_no_compose_down(tmp_path: pathlib.Path) -> None:
    result, _config, calls = run_lifecycle_script(tmp_path, "scripts/stop.sh", active=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "down --remove-orphans" not in calls.read_text()
```

- [ ] **Step 2: Run** `uv run pytest tests/test_shell.py -k "render_unit or stop_downs or stop_inactive" -q`. Expected: `test_render_unit_writes_a_compose_file_and_wrapper_unit` and `test_stop_downs_leftover_containers_when_the_unit_is_inactive` FAIL; `test_stop_inactive_without_a_compose_file_runs_no_compose_down` passes already (guard: no compose file, no `down`).
- [ ] **Step 3: Implement** the two flags in the unit template (`--remove-orphans` is needed because llm-server can now outlive its compose entry after `llama-disable`). In `stop.sh`'s full-stop branch the `else` of the `is-active` check becomes: `log_warn "server unit is not active; removing any containers it left running"`, then, only when `[ -f "$COMPOSE_FILE" ]`, `require_cmd podman` and `podman compose -f "$COMPOSE_FILE" down --remove-orphans` (one-line comment: a failed unit can leave running containers that the `--no-recreate` ExecStart would never recreate). The active branch and the Phoenix stop are unchanged.
- [ ] **Step 4: Verify:** `uv run pytest tests/test_shell.py -k "render_unit or stop_" -q` passes (Task 1's `stop_llama` and Phoenix tests included); `make validate && make test`.
- [ ] **Step 5: Commit**

```bash
git add setup/render-unit.sh scripts/stop.sh tests/test_shell.py
git commit -m "feat(lifecycle): render the unit with --no-recreate and --remove-orphans; make stop downs containers an inactive unit left running"
```

---

### Task 3: Non-disruptive `make start`

**Files:**
- Modify: `scripts/start.sh`, `tests/test_shell.py` (tests at lines 5315, 7458, 7473 and new ones)

**Interfaces:**
- Consumes: `container_running`, the stub knobs and the stateful unit stub (Task 1), unit flags (Task 2), Task 0 (h2)/(h3) for `--pull=never`.
- Produces: `scripts/start.sh` behaviour of the "make start" table row; shell variable `llm_server_running` (0/1) from `container_running llm-server`; the final hint text "run 'make restart' to apply config changes" on stdout (asserted here, reused by Task 4 and Task 7 docs).

- [ ] **Step 1: Rewrite two existing tests, add four, strengthen one guard:**

```python
def test_start_never_stops_an_active_stack(tmp_path: pathlib.Path) -> None:
    """Replaces test_start_stops_active_router_before_budgeting."""
    result, config, calls = run_lifecycle_script(tmp_path, "scripts/start.sh", active=True)
    assert result.returncode == 0, result.stdout + result.stderr
    recorded = calls.read_text().splitlines()
    up = recorded.index(
        f"podman compose -f {config.parent / 'docker-compose.yml'} up -d --no-recreate --no-deps --pull=never"
    )
    budget = next(i for i, call in enumerate(recorded) if " budget " in call)
    assert budget < up
    assert not any(call.startswith(("systemctl --user stop", "systemctl --user start")) for call in recorded)
    assert not any(call.startswith("podman compose") and " down" in call for call in recorded)
```

  - `test_start_disabled_transition_stops_existing_phoenix_volume` becomes `test_start_does_not_stop_phoenix_when_system_one_disabled` (`llm_server_enabled=False`, `PHOENIX_VOLUME_EXISTS=1`): rc 0; `"scripts/phoenix.sh stop" not in calls`.
  - New `test_start_inactive_stack_starts_the_unit` (`active=False`): `"systemctl --user start llm-server.service" in calls`; no line starting with `podman compose -f` contains ` up `.
  - New `test_start_skips_budget_when_llm_server_already_running` (`active=True`, `PODMAN_LLM_SERVER_RUNNING=1`): rc 0; `" budget " not in calls`; stdout contains "llm-server is already running".
  - New `test_start_refuses_to_start_on_an_infeasible_budget` (`active=True`, `BUDGET_INFEASIBLE=1`): rc != 0; stderr contains "budget exceeded"; stdout contains "VRAM short by 2000 MiB" (the kept parser); calls contain no `up -d`, no `render-unit.sh`, no line starting with `systemctl --user start` or `systemctl --user stop` [Review Focus 2].
  - New `test_start_prints_restart_hint_for_running_containers` (`active=True`): stdout contains "run 'make restart' to apply config changes".
  - Guard `test_key_reset_restarts_an_active_server` keeps its assertions and adds: the index of `systemctl --user stop llm-server.service` is lower than the index of `systemctl --user start llm-server.service`, and no recorded line contains `--no-recreate` (start.sh saw the unit inactive after stop.sh, thanks to Task 1's stateful stub).
- [ ] **Step 2: Run** `uv run pytest tests/test_shell.py -k "start_ or key_reset" -q`. Expected: five FAIL (`never_stops_an_active_stack`, `does_not_stop_phoenix_when_system_one_disabled`, `skips_budget_when_llm_server_already_running`, `refuses_to_start_on_an_infeasible_budget`, `prints_restart_hint_for_running_containers`); `test_start_inactive_stack_starts_the_unit` and `test_key_reset_restarts_an_active_server` pass already (guards).
- [ ] **Step 3: Implement in `scripts/start.sh`:** remove the "Stopping the active router" block (lines 47-50) and the System-One-disabled `phoenix.sh stop` (lines 23-26); compute `llm_server_running` via `container_running` when llm-server is enabled; keep the budget block (lines 52-73) verbatim, only its guard becomes "enabled and `llm_server_running` is 0", and print "llm-server is already running; skipping the VRAM budget check" (`log_info`) otherwise; after resources and render, an active unit runs `podman compose -f "$COMPOSE_FILE" up -d --no-recreate --no-deps --pull=never` (Decision 7), an inactive one runs `systemctl --user start llm-server.service`; print the final hint (`log_info "containers already running keep their definition; run 'make restart' to apply config changes"`). Keep health waits, Phoenix start, `network.sh`, timers, provisioning and codex-provision as today. Leave the `resources_json` `mktemp` and its EXIT trap exactly as they are (external commands such as the stub `sleep` do not inherit EXIT traps, and the file is last read before the render; see R-C1 in "Review responses (round 2)"). If Task 0 Step 4 required it, also skip the `presets.ini` render while llm-server runs.
- [ ] **Step 4: Verify:** `uv run pytest tests/test_shell.py -k "start_ or key_reset" -q` passes; also green and unchanged: `test_makefile_restart_chains_two_recursive_make_calls`, `test_make_restart_runs_stop_then_start_with_distinct_banners`, `test_key_reset_does_not_start_an_inactive_server`, all other `scripts/start.sh` tests (they run with `active=False`). Then `make validate && make test`.
- [ ] **Step 5: Commit**

```bash
git add scripts/start.sh tests/test_shell.py
git commit -m "feat(lifecycle): make start non-disruptive, never stops or recreates a running container"
```

---

### Task 4: `start-llama` scope in `start.sh`

**Files:**
- Modify: `scripts/start.sh`, `tests/test_shell.py`

**Interfaces:**
- Consumes: Task 3's `start.sh` (`llm_server_running`, the hint), Task 1's knobs.
- Produces: `scripts/start.sh [llm-server]` with `scope="${1:-all}"` checked as the first statement after sourcing `tools/lib.sh` (anything else: `usage: start.sh [llm-server]` on stderr, exit 2); the `up` call in scope `llm-server` ends with `up -d --no-recreate --no-deps --pull=never llm-server`; the health-failure output in scope `llm-server` ends with the Recover line of Decision 3 on stdout (used by Task 7 and Task 8 docs). Tests use one module constant: `RECOVER_LINE = "Recover: make stop-llama, restore the backup printed by make llama-enable if the model changed, then make start-llama"`.

- [ ] **Step 1: Write the failing tests** (all `scripts/start.sh`, `args=("llm-server",)` unless stated):
  - `test_start_llama_starts_only_llm_server` (`active=True`, `system_one_enabled=True`, `omniroute_port` = a bound listening ephemeral socket as in `test_start_chains_provider_provision_once_omniroute_is_ready`): rc 0; the last recorded `podman compose` call equals `f"podman compose -f {compose_file} up -d --no-recreate --no-deps --pull=never llm-server"`; budget index before it; `"omniroute provision" in calls`; stdout contains "server is ready" and "make restart"; absent from calls: `systemctl --user start`, `systemctl --user stop`, `phoenix.sh start`, `system-one-cache-check.py verify`, `enable-omniroute-heal.sh`, `enable-omniroute-zai-reset.sh`, `network.sh`, `provider-provision.sh`; absent from stdout: "Waiting for System One".
  - `test_start_llama_refuses_when_stack_inactive` (`active=False`): rc != 0; stderr contains "run 'make start'"; no ` budget `, no `render-unit.sh`, no `up -d`, no `systemctl --user start`.
  - `test_start_llama_refuses_when_llm_server_disabled` (`active=True`, `llm_server_enabled=False`): rc != 0; stderr contains "make llama-enable MODEL="; no ` budget `, no `render-unit.sh`, no `up -d`.
  - `test_start_llama_refuses_on_infeasible_budget` (`active=True`, `BUDGET_INFEASIBLE=1`): rc != 0; stderr contains "budget exceeded"; calls contain no `up -d`, no `render-unit.sh`, no line starting with `systemctl --user start` or `systemctl --user stop` [Review Focus 2].
  - `test_start_llama_is_a_noop_when_already_running` (`active=True`, `PODMAN_LLM_SERVER_RUNNING=1`): rc 0; stdout contains "llm-server is already running" and "make stop-llama"; `/health` in calls; no ` budget `, no `render-unit.sh`, no `up -d` [Decision 3].
  - `test_start_llama_fails_on_a_running_but_unhealthy_server` (`active=True`, `PODMAN_LLM_SERVER_RUNNING=1`, `CURL_HEALTH_FAIL=1`): rc 1; `RECOVER_LINE in result.stdout`; `/health` in calls; no `up -d`, no ` budget `, no `render-unit.sh`, no `podman stop`, no `podman restart`, no `podman rm` [Review Focus 4].
  - `test_start_llama_leaves_an_unhealthy_server_running` (`active=True`, `CURL_HEALTH_FAIL=1`): rc 1; `RECOVER_LINE in result.stdout`; the `up -d --no-recreate --no-deps --pull=never llm-server` call is present; no `podman stop`, no `podman restart`, no `podman rm`. (This path also runs the stub `sleep` inside `wait_for_health` with the resources EXIT trap set, and still prints the Recover line afterwards.)
  - `test_start_rejects_an_unknown_scope` (`args=("omniroute",)`): rc 2; stderr contains "usage: start.sh [llm-server]"; `"migrate-config" not in calls`.
- [ ] **Step 2: Run** `uv run pytest tests/test_shell.py -k "start_llama or unknown_scope" -q`. Expected: seven FAIL (the other seven new tests); two pass: `test_start_llama_refuses_on_infeasible_budget` (Task 3's budget gate ignores the argument and refuses the same way; it stays as the scope's Review Focus 2 guard) and `test_stop_llama_rejects_an_unknown_scope` (Task 1, selected by `unknown_scope`).
- [ ] **Step 3: Implement:** scope parsing and usage check at the top; in scope `llm-server` die unless `llm_server_enabled` is `true` ("llm-server is disabled; run 'make llama-enable MODEL=<alias>' first") and the unit is active ("the stack is not running; run 'make start'"); scope guards around the System One check, the System One wait, Phoenix, `network.sh`, timers and codex-provision; when `llm_server_running` is 1 in scope `llm-server` skip budget, resources, render and `up` (Decision 3); otherwise the `up` call is `podman compose -f "$COMPOSE_FILE" up -d --no-recreate --no-deps --pull=never llm-server`; the llm-server health wait is shared, and on failure in scope `llm-server` it prints, after the existing `log_error` and `Logs:` lines, `echo "  Recover: make stop-llama, restore the backup printed by make llama-enable if the model changed, then make start-llama"` (stdout) and exits 1 without touching the container (scope `all` keeps today's failure output); then the OmniRoute port wait and `llm-env-local` provisioning as today; the Decision 5 hint ends both scopes.
- [ ] **Step 4: Verify:** `uv run pytest tests/test_shell.py -k "start_llama or unknown_scope or start_ or key_reset" -q`; `make validate && make test`.
- [ ] **Step 5: Commit**

```bash
git add scripts/start.sh tests/test_shell.py
git commit -m "feat(lifecycle): add llm-server-only scope to start.sh for start-llama"
```

---

### Task 5: `scripts/llama-config.sh`

**Files:**
- Create: `scripts/llama-config.sh`
- Modify: `tests/test_shell.py`

**Interfaces:**
- Consumes: `llmenv` (`uv run llmenv.py`), `die`, `log_info`, `require_cmd`, `CONFIG_PATH` from `tools/lib.sh`; `llmenv --config <file> migrate-config`; `llmenv --config <file> models select <alias>` (Fact 11: JSON with `.error` and exit 1 on rejection, nothing written); Task 1's `models` passthrough, `FIXED_DATE_STAMP` stub and repeatable helper.
- Produces: `bash scripts/llama-config.sh enable <alias>` and `bash scripts/llama-config.sh disable`; exit 0 on success, 1 on a failed edit, 2 on usage errors; backup `<config dir>/models.yml.<YYYYmmddTHHMMSS>.bak` (stamp from `date +%Y%m%dT%H%M%S`), mode 0600, path printed to stdout. Used by Task 6.

- [ ] **Step 1: Write the failing tests:**

```python
def _backups(config: pathlib.Path) -> list[pathlib.Path]:
    return sorted(config.parent.glob("models.yml.*.bak"))


def test_llama_enable_backs_up_and_selects_the_model(tmp_path: pathlib.Path) -> None:
    result, config, calls = run_lifecycle_script(
        tmp_path, "scripts/llama-config.sh", args=("enable", "test"), llm_server_enabled=False
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert yq_value(config, ".llm_server.enabled") == "true"
    assert yq_value(config, '.models[] | select(.alias == "test") | .enabled') == "true"
    [backup] = _backups(config)
    assert yq_value(backup, ".llm_server.enabled") == "false"
    assert stat.S_IMODE(backup.stat().st_mode) == 0o600
    assert stat.S_IMODE(config.stat().st_mode) == 0o600
    assert str(backup) in result.stdout and "make start-llama" in result.stdout
    assert sorted(p.name for p in config.parent.iterdir()) == sorted(["models.yml", backup.name])
    assert "systemctl" not in calls.read_text() and "podman" not in calls.read_text()


def test_llama_enable_rejects_an_unknown_alias(tmp_path: pathlib.Path) -> None:
    result, config, _calls = run_lifecycle_script(
        tmp_path, "scripts/llama-config.sh", args=("enable", "nope"), llm_server_enabled=False
    )
    assert result.returncode == 1
    assert "unknown model alias: nope" in result.stderr
    [backup] = _backups(config)
    assert str(backup) in result.stdout
    assert config.read_bytes() == backup.read_bytes()  # nothing written, not even migrate output
    assert sorted(p.name for p in config.parent.iterdir()) == sorted(["models.yml", backup.name])


def test_llama_enable_refuses_an_existing_backup_name(tmp_path: pathlib.Path) -> None:
    stamp = {"FIXED_DATE_STAMP": "20261030T120000"}
    first, config, _ = run_lifecycle_script(
        tmp_path, "scripts/llama-config.sh", args=("enable", "test"),
        llm_server_enabled=False, env_overrides=stamp,
    )
    assert first.returncode == 0, first.stdout + first.stderr
    [backup] = _backups(config)
    assert backup.name == "models.yml.20261030T120000.bak"
    saved = backup.read_bytes()
    # The helper rewrites the fixture config (enabled: false) and stubs on the same tmp_path.
    second, config, _ = run_lifecycle_script(
        tmp_path, "scripts/llama-config.sh", args=("enable", "test"),
        llm_server_enabled=False, env_overrides=stamp,
    )
    assert second.returncode == 1
    assert "already exists" in second.stderr
    assert _backups(config) == [backup] and backup.read_bytes() == saved
    assert yq_value(config, ".llm_server.enabled") == "false"  # refused before any write
```

  - `test_llama_enable_requires_a_model` (`args=("enable", "")`): rc 2; stderr contains "MODEL="; `_backups(config) == []`.
  - `test_llama_disable_backs_up_and_sets_gateway_only` (`args=("disable",)`, default `llm_server_enabled=True`): rc 0; `.llm_server.enabled == "false"`; `yq_value(config, ".models")` equals `yq_value(backup, ".models")`; one backup, mode 0600, its path in stdout; stdout contains "make stop-llama" and "make restart"; no `systemctl` or `podman` in calls; config dir holds only `models.yml` and the backup.
  - `test_llama_config_rejects_an_unknown_action` (`args=("toggle",)`): rc 2; stderr contains "usage: llama-config.sh enable <alias> | disable"; `_backups(config) == []`.
- [ ] **Step 2: Run** `uv run pytest tests/test_shell.py -k "llama_enable or llama_disable or llama_config" -q`. Expected: all six FAIL (script missing).
- [ ] **Step 3: Implement `scripts/llama-config.sh`** (`set -euo pipefail`, source `tools/lib.sh`, `require_cmd uv jq yq`; die if `CONFIG_PATH` is missing). Order, each step only if the previous succeeded:
  1. Usage: `enable` needs a non-empty alias (`usage: make llama-enable MODEL=<alias>`, exit 2); `disable` takes none; anything else prints `usage: llama-config.sh enable <alias> | disable`, exit 2. No file is touched on a usage error.
  2. Backup: `backup="${CONFIG_PATH}.$(date +%Y%m%dT%H%M%S).bak"`; `die "backup ${backup} already exists; retry in a second"` if it exists (the test asserts `already exists`); `cp -p`, `chmod 600`, print `backup: ${backup}` to stdout.
  3. Temp copy: `work="$(mktemp "$(dirname "$CONFIG_PATH")/.models.yml.llama-config.XXXXXX")"` (0600, same filesystem), `trap 'rm -f "$work"' EXIT`, `cp "$CONFIG_PATH" "$work"`.
  4. Edit the copy, capturing each llmenv call's stdout (both print JSON; keep it off the script's stdout) and parsing it only on a non-zero exit: `if ! output="$(llmenv --config "$work" migrate-config)"; then die "$(jq -r '.error // "configuration migration failed"' <<<"$output")"; fi`; for `enable`, the same pattern with `llmenv --config "$work" models select "$alias"` (the alias only as argv) and the fallback `"models select failed"`; then `yq -i '.llm_server.enabled = true'` (or `false` for `disable`) on the copy. Both llmenv calls save only onto `$work` (Fact 11). Any failure dies with the copy removed by the trap and `models.yml` untouched.
  5. Commit: `chmod 600 "$work"` then `mv -f "$work" "$CONFIG_PATH"` (atomic rename).
  6. Print the next step: enable: "run 'make start-llama' (or 'make start' when the stack is down)"; disable: the sentence in the semantics table.
- [ ] **Step 4: Verify:** `uv run pytest tests/test_shell.py -k "llama_enable or llama_disable or llama_config" -q` passes; `make validate && make test`.
- [ ] **Step 5: Commit**

```bash
git add scripts/llama-config.sh tests/test_shell.py
git commit -m "feat(lifecycle): add llama-config.sh to enable or disable the local llama server with an atomic, backed-up edit"
```

---

### Task 6: Makefile targets and help

**Files:**
- Modify: `Makefile` (`.PHONY` line 1, targets after `restart`), `scripts/help.sh` (lines 13-15 and new lines), `tests/test_shell.py`

**Interfaces:**
- Consumes: Tasks 1, 4, 5 scripts. Produces: the four targets, with exact recipe lines (used by Task 7 docs and Task 8 skill):
  - `start-llama`: `@bash tools/run-target.sh start-llama -- bash scripts/start.sh llm-server`
  - `stop-llama`: `@bash tools/run-target.sh stop-llama -- bash scripts/stop.sh llm-server`
  - `llama-enable`: `@bash tools/run-target.sh llama-enable -- bash scripts/llama-config.sh enable "$(MODEL)"`
  - `llama-disable`: `@bash tools/run-target.sh llama-disable -- bash scripts/llama-config.sh disable`
  `restart` stays unchanged.

- [ ] **Step 1: Write the failing test** `test_makefile_exposes_llama_targets`: each target name followed by `:\n\t` and its exact recipe line is in the Makefile; each name appears in the `.PHONY` block; `_help_line "<name>"` exists in `scripts/help.sh` for all four; `/usr/bin/make --no-print-directory -n llama-enable MODEL=x` (cwd `ROOT`) exits 0 and its stdout contains `bash scripts/llama-config.sh enable "x"`.
- [ ] **Step 2: Run** `uv run pytest tests/test_shell.py -k "makefile or make_help or make_restart" -q`. Expected: `test_makefile_exposes_llama_targets` FAILS, the rest pass.
- [ ] **Step 3: Implement** the targets and `.PHONY` entries in `Makefile`; in `scripts/help.sh` add the four `_help_line` entries and reword `start` ("Start whatever is not running (never stops a running container); installs the OmniRoute healer and z.ai reset timers"), `stop` ("Stop the whole stack"), `restart` ("Stop everything, re-render and start (applies config changes; interrupts requests)").
- [ ] **Step 4: Verify:** `make help` lists the new targets; `make -n start-llama` prints the `run-target.sh` line; `uv run pytest tests/test_shell.py -k "makefile or make_help or make_restart" -q`; `make validate && make test`.
- [ ] **Step 5: Commit**

```bash
git add Makefile scripts/help.sh tests/test_shell.py
git commit -m "feat(lifecycle): expose start-llama, stop-llama, llama-enable and llama-disable targets"
```

---

### Task 7: Documentation

**Files:**
- Modify: `AGENTS.md`, `README.md`, `QUICK_START.md`, `.agents/architecture.md`, `tests/test_docs.py`, `llmenv.py` (`_APPLY_HINT` and its comment, lines 473-483), `tests/test_cli.py` (`test_override_raising_a_cap_has_no_warning`, line 2841)

**Interfaces:**
- Consumes: Task 0 findings (c2) and (d) (wording of the `--requires` and healer notes), the semantics and failure tables above, the hint texts of Tasks 3-5 (including the Recover line), Decisions 7 and 8.

- [ ] **Step 1: Write the failing tests** in `tests/test_docs.py` (use the existing `_normalized_markdown`, because the old AGENTS.md wording wraps across lines):
  - `test_agents_commands_list_focused_lifecycle_targets`: normalized `AGENTS.md` contains `make start-llama`, `make stop-llama`, `make llama-enable MODEL=` and `make llama-disable`; the raw Commands-block line that starts with `make start ` contains "never stops a running container"; the raw line that starts with `make restart ` contains "re-render"; normalized text contains "for the next `make restart` or boot" and does not contain "for the next start or boot".
  - `test_readme_and_architecture_describe_the_llama_toggle`: `README.md` has the heading `## Local inference toggle and model swap` and its normalized text contains `make stop-llama && make llama-enable MODEL=<alias> && make start-llama`; normalized `.agents/architecture.md` contains `up -d --no-recreate`, `down --remove-orphans`, `--pull=never` and `make stop-llama`; normalized `QUICK_START.md` contains `make start-llama` and `make llama-enable MODEL=`.

  - In `tests/test_cli.py`, `test_override_raising_a_cap_has_no_warning` adds: ``assert "for the next `make restart` or boot" in apply`` and `assert "next start or boot" not in apply`.

  Run `uv run pytest tests/test_docs.py tests/test_cli.py -k "focused_lifecycle or llama_toggle or raising_a_cap" -q`; expect all three to FAIL.
- [ ] **Step 2: Edit the docs:**
  - `AGENTS.md`: Commands block (`make start` "Start whatever is not running (never stops a running container); installs the OmniRoute healer and z.ai weekly-reset timers", `make stop` "Stop the whole stack", `make restart` "Stop everything, re-render and start (applies config changes)", plus the four targets with one-line descriptions); `LLM_ENV_NO_GPU` bullet (switch modes later with `make llama-enable MODEL=<alias>` / `make llama-disable` plus `make restart`); Resource overrides paragraph ("for the next start or boot" becomes "for the next `make restart` or boot"; `make start` no longer applies changes to running containers).
  - `llmenv.py`: in `_APPLY_HINT` "re-renders the compose file and unit for the next start or boot" becomes "re-renders the compose file and unit for the next `make restart` or boot"; the comment above it says "for the next restart" instead of "for the next start". No other wording changes (the existing assertions on "make restart", "does not touch running containers", "interrupted" and "new cgroup lifetime" stay true).
  - `README.md`: Quick start comment for `make start`; "GPU-optional mode" text (flip with `make llama-enable` / `make llama-disable`, apply with `make restart`); new section "Local inference toggle and model swap" right after "GPU-optional mode" with the swap recipe, the backup and atomic-edit note, the healer/`--requires` caveat (Task 0 (d)), the `stop-llama` + `llama-disable` + `make start` note (Task 0 (c2)), the definition-drift caveat including `LLAMA_ARG_MODELS_MAX` (Fact 10), and the running-but-unhealthy behaviour with the Recover line (Decision 3).
  - `QUICK_START.md`: "Daily use" (add `make restart`, `make start-llama`, `make stop-llama`); "Changing models" becomes `uv run llmenv.py models list` then the swap recipe, with one line that several models at once still use `uv run llmenv.py models select <a> <b>` plus `make restart`.
  - `.agents/architecture.md`: Files table (start.sh scopes, `scripts/llama-config.sh`); Container Lifecycle (ExecStart `--no-recreate`, ExecStop `--remove-orphans`, `make stop`'s direct `down --remove-orphans` when the unit is inactive (Decision 8), the direct `up` calls' `--pull=never` and the unit's default pre-pull (Decision 7, Fact 12), unit state under start-llama/stop-llama, the depends_on / `--requires` notes, Decision 2 boot note, Fact 9 render note); "What survives what" table (`make start` row: creates missing, starts exited, never recreates or stops; new `make restart` row: recreated; new `make stop-llama` / `make start-llama` row: only llm-server stops or starts, everything else untouched; the `make stop` row also names the inactive-unit `down --remove-orphans`; the "host reboot" row becomes: after a clean shutdown (ExecStop ran `down`) recreated; after an unclean shutdown the exited containers are started as they were, without a fresh render (Decision 2), `make restart` applies the current config).
  - Document Decision 4 (double counting fails safe), the mDNS ExecStartPre failure while llm-server is stopped, and the failure-table rows on Phoenix-on-disable, the inactive unit with running containers, `stop-llama` + `llama-disable` + `make start`, and the missing-tag/unreachable-registry start.
- [ ] **Step 3: Verify:** `uv run pytest tests/test_docs.py tests/test_cli.py -q` passes; `make validate && make test`.
- [ ] **Step 4: Commit**

```bash
git add AGENTS.md README.md QUICK_START.md .agents/architecture.md tests/test_docs.py llmenv.py tests/test_cli.py
git commit -m "docs(lifecycle): document focused start, full restart and the llama toggle"
```

---

### Task 8: Update the `onboard-local-model` skill

The skill tree is untracked and other work edits it in parallel (the layered tasks arrived during round 1). Re-read every file below at execution time and change only the passages named here; where an equivalent passage already exists, keep it and add only what the tests require. This task commits nothing: its test module exercises skill scripts (`preflight.sh`, `hf-scout.sh`, `effort-run.sh`, `score.sh`, `gpu-sample.sh`, `system-one-check.sh`, `tasks/`) that stay untracked, and `make test` collects `tests/skills/`, so committing the test alone would break `make test` on any clean checkout. Committing the whole skill tree is the owner's call (Task 9 reports it).

**Files:**
- Modify: `.claude/skills/onboard-local-model/SKILL.md` (line 21 tasks note, steps 5-7 and 9, Gates, NEVER bullets on `make stop` and `llm_server.enabled`, Gotchas bullet on `llm_server.enabled`)
- Modify: `.claude/skills/onboard-local-model/references/models-yml.md` ("Where things live" bullet on `llm_server.enabled`, live phase steps 4-8 and its heading)
- Modify: `.claude/skills/onboard-local-model/evals/evals.json` (evals `onboard-plan-with-server-running` and `gateway-only-host`)
- Modify: `.claude/skills/onboard-local-model/scripts/bench-fit.sh` (refusal message, line 30)
- Create: `.claude/skills/onboard-local-model/scripts/wait-gpu-release.sh`
- Test: `tests/skills/onboard-local-model/test_onboard_local_model_scripts.py`

**Interfaces:**
- Consumes: the targets of Task 6 (`make llama-enable MODEL=<alias>`, `make llama-disable`, `make start-llama`, `make stop-llama`) and existing `make status`, `make benchmark`, `make check-server`, `make check-with-agents`, `make setup`, `make setup-local-llm-agents`; the swap recipe; the existing skill scripts `preflight.sh` (JSON with `.gpu.used_mib` and `.gpu.holders[]` = `{pid, comm, vram_mib}`), `effort-run.sh --layers`, `score.sh` (`ladder`, `ladder_at`); Task 0 finding (d) (does the healer's `podman restart omniroute` start a stopped llm-server).
- Produces: `wait-gpu-release.sh --below-mib <N> [--timeout <s>, default 60] [--interval <s>, default 5]`: polls `${ONBOARD_PREFLIGHT:-$here/preflight.sh}` (test seam, same pattern as `HF_FIXTURE_DIR` in `hf-scout.sh`); prints `{"released": true, "used_mib": X, "waited_s": T}` and exits 0 once `.gpu.used_mib <= N`; after `--timeout` seconds (checked at least once) prints `{"released": false, "used_mib": X, "holders": [...]}` and exits 1; missing `--below-mib` or an unknown flag exits 2; `--help` prints its header like the sibling scripts (first line `wait-gpu-release.sh — ...`).

- [ ] **Step 1: Write the failing tests** in `tests/skills/onboard-local-model/test_onboard_local_model_scripts.py`:
  - Add `"wait-gpu-release"` to `SCRIPT_NAMES` (help test) and `"wait-gpu-release.sh"` to the `test_missing_required_arguments_exit_two` parameters; `test_bench_fit_refuses_while_llm_server_holds_the_gpu` also asserts `"make stop-llama" in result.stderr`.
  - New tests (helper `_md(path)` returns `" ".join(path.read_text().split())`; helper `_section(text, start, end)` returns the text between two headings):

```python
SKILL_MD = SKILL / "SKILL.md"
MODELS_MD = SKILL / "references" / "models-yml.md"


def test_skill_live_phase_uses_the_lifecycle_targets() -> None:
    text = _md(SKILL_MD)
    for target in ("make stop-llama", "make llama-enable MODEL=<alias>", "make start-llama", "make status",
                   "make check-server", "make check-with-agents", "make benchmark", "wait-gpu-release.sh"):
        assert target in text, target
    assert "compose -f ~/.config/llm-env/docker-compose.yml stop llm-server" not in text
    assert "stops an active unit" not in text


def test_models_yml_reference_uses_the_lifecycle_targets() -> None:
    text = _md(MODELS_MD)
    for target in ("make llama-enable MODEL=<alias>", "make start-llama", "make stop-llama", "make status",
                   "Live phase (SKILL.md step 7"):
        assert target in text, target
    assert "stops an active unit" not in text
    assert "`bash setup/render-unit.sh`, `make start`" not in text


def test_evals_use_the_lifecycle_targets() -> None:
    evals = json.loads((SKILL / "evals" / "evals.json").read_text())
    text = json.dumps(evals)
    assert "compose stop llm-server" not in text and "compose service stop" not in text
    by_id = {e["id"]: e for e in evals["evals"]}
    plan = by_id["onboard-plan-with-server-running"]
    assert "make stop-llama" in plan["expected_output"]
    assert any("make stop-llama" in a and "make stop, make restart" in a for a in plan["assertions"])
    assert any("make llama-enable" in a for a in plan["assertions"])
    assert any("make llama-enable" in a for a in by_id["gateway-only-host"]["assertions"])


def test_skill_gates_announce_and_proceed_when_permissions_are_bypassed() -> None:
    gates = _section(_md(SKILL_MD), "## Gates", "## NEVER")
    for phrase in ("permissions bypassed", "announce the facts, then proceed", "deletes user data",
                   "never switch provider or model", "a yes to one never covers the next"):
        assert phrase in gates, phrase


def test_skill_documents_layered_isolated_delegated_benchmarks() -> None:
    text = _md(SKILL_MD)
    for phrase in ("tasks/l1-", "--layers", "ladder", "subagent", "haiku", ".claude/worktrees/",
                   "own git repository", "never as a long one-liner"):
        assert phrase in text, phrase
    assert "three coding tasks" not in text and "9 x 900" not in text


def test_skill_prose_has_no_long_shell_logic() -> None:
    """Shell logic (loops, waits, chains of more than two commands) lives in scripts/, not in prose."""
    for path in [SKILL_MD, *sorted((SKILL / "references").glob("*.md"))]:
        prose = re.sub(r"```.*?```", "", path.read_text(), flags=re.S)
        for span in re.findall(r"`([^`]+)`", prose):
            assert not re.search(r"; do\b|; then\b|\bwhile |\buntil ", span), (path.name, span)
            assert len(re.findall(r"&&|\|\||;", span)) <= 2, (path.name, span)


def _fake_preflight(tmp_path: Path, *used: int) -> Path:
    """One preflight-shaped JSON per call, walking through `used` and repeating the last value."""
    values = tmp_path / "used.txt"
    values.write_text("".join(f"{v}\n" for v in used))
    script = tmp_path / "preflight.sh"
    script.write_text(
        "#!/usr/bin/env bash\n"
        f'v="$(head -n1 {values})"; [ "$(wc -l < {values})" -gt 1 ] && sed -i 1d {values}\n'
        'printf \'{"gpu":{"used_mib":%s,"holders":[{"pid":7,"comm":"llama-server","vram_mib":8000}]}}\\n\' "$v"\n'
    )
    script.chmod(0o755)
    return script


def test_wait_gpu_release_returns_once_used_vram_drops(tmp_path: Path) -> None:
    env = {"PATH": "/usr/bin:/bin", "ONBOARD_PREFLIGHT": str(_fake_preflight(tmp_path, 9000, 9000, 1200))}
    result = _run("bash", SCRIPTS / "wait-gpu-release.sh", "--below-mib", "2000",
                  "--interval", "0", "--timeout", "30", env=env)
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert out["released"] is True and out["used_mib"] == 1200


def test_wait_gpu_release_times_out_with_the_gpu_holders(tmp_path: Path) -> None:
    env = {"PATH": "/usr/bin:/bin", "ONBOARD_PREFLIGHT": str(_fake_preflight(tmp_path, 9000))}
    result = _run("bash", SCRIPTS / "wait-gpu-release.sh", "--below-mib", "2000",
                  "--interval", "0", "--timeout", "0", env=env)
    assert result.returncode == 1
    out = json.loads(result.stdout)
    assert out["released"] is False and out["holders"][0]["comm"] == "llama-server"
```

  (`re` joins the module's imports.)
- [ ] **Step 2: Run** `uv run pytest tests/skills -q`. Expected: FAIL for the five doc/eval tests, both `wait_gpu_release` tests, the two new parameter cases and the extended bench-fit test; `test_skill_prose_has_no_long_shell_logic` passes already (guard: it measured clean on 2026-10-30 and keeps the edits below clean).
- [ ] **Step 3: Create `scripts/wait-gpu-release.sh`** per the Interfaces block (`set -euo pipefail`, header comment with usage in the sibling style, `jq` only, elapsed time from bash `SECONDS`). Change `bench-fit.sh:30` to `llm-server is running and holds the GPU; stop it first with make stop-llama (the skill asks the user)`.
- [ ] **Step 4: Edit `SKILL.md`:**
  - Line 21: `$SK/tasks/` holds the benchmark ladder `tasks/l1-*` .. `tasks/l5-*` (rising complexity) used by `effort-run.sh`.
  - Step 5 (G2): on yes, run `preflight.sh` once and read `.gpu.used_mib` (U) and the `vram_mib` of the `.gpu.holders[]` entry whose `comm` is `llama-server` (H); run `make stop-llama` (stops only the llm-server container; OmniRoute, System One and remote-setup keep running; the local OmniRoute lane goes offline meanwhile), then `bash $SK/scripts/wait-gpu-release.sh --below-mib <U - H + 512>` (the served model's measured VRAM, plus 512 MiB for compositor and System One allocation noise); on exit 1 report the printed holders and stop. If Task 0 (d) found that `podman restart` of a dependent starts its stopped dependency, add: "the OmniRoute healer timer may restart omniroute and with it llm-server; re-run `preflight.sh` before each benchmark run and stop if `llama-server` holds the GPU again"; otherwise add: "the healer's restart of omniroute fails while llm-server is stopped (logged); it never reloads llm-server". Keep "Leave System One running ..." and "Remember the prior state for step 9".
  - Step 6: the `bench-fit.sh` screen is a benchmark run (see "Benchmark runs" below); keep its command, depths and rejection rule.
  - Step 7 (G3): replace the raw edit-and-start sequence with: add the entry per `references/models-yml.md` step 4, `make llama-enable MODEL=<alias>` (takes the timestamped backup itself, validates the alias, writes atomically, prints the backup path), `make setup` for download, GGUF validation, image pull and budget, `make start-llama` (or `make start` when the stack is down), `make status`, `make check-server`, `make check-with-agents`, `make benchmark` (records `benchmark.vulkan`), then `make setup-local-llm-agents`. Quality: `effort-run.sh` runs the ladder; `--layers 1,2,3` limits it; `score.sh` reports `ladder` (highest layer N with every layer 1..N solved) and `ladder_at` (cheapest effort per layer); state the upper bound first as layers x tiers x `--timeout` (today 5 x 3 x 900 s; the default screen `--efforts low,high` is 5 x 2 x 900 s). Keep the polling, baseline, anchors, G4 and `/jev-fast-decision` text.
  - New short subsection "Benchmark runs" under Workflow (or after step 7): every `bench-fit.sh` and `effort-run.sh` run, anchor runs included, is isolated and delegated. Isolation: a new directory per run under `tmp/model-scout/<run>/`, never reused; agent runs work in their own git repository (`effort-run.sh` makes one per task and effort and saves `agent.diff`); a run that needs its own repo checkout uses a git worktree under `.claude/worktrees/<run>`, removed at the end. Delegation: write the brief first (exact command, output directory, poll cadence, stop and cleanup conditions, what to report), then hand it to one subagent per run, preferably on `haiku` once the brief is mechanical; the orchestrator still monitors it per AGENTS.md (output file and PID at least every 5 minutes, run state in every status message). One GPU benchmark at a time; when Task 0 (d) found the healer can reload llm-server, each brief starts with a `preflight.sh` holder check. `make benchmark` stays inline in step 7: it is the repo target that records into the live config. Add the rule: shell logic longer than one command (loops, waits, branches, chains of more than two commands) lives in `$SK/scripts/<name>.sh` with `--help` and a test, never as a long one-liner in prose.
  - Step 9 decline path: `make stop-llama`, restore the backup (`cp -p <backup> ~/.config/llm-env/models.yml`), then `make start-llama` if llm-server served before G2; for a host that was gateway-only, stop there and note that `make restart` later applies gateway-only mode to the whole stack.
  - Gates: keep the paragraph ("a yes to one never covers the next" stays) and add: "**Permissions bypassed.** When the session runs with permissions bypassed, a gate becomes announce the facts, then proceed: print the facts the question would state, then continue. Two things still stop and ask: any step that deletes user data (model files, `make prune`, `make clean`, volumes, the locally built System One image, or overwriting the live models.yml without a backup), and any change of provider, harness or model after a failure: never switch provider or model; retry the same lane, then change session, then report (AGENTS.md)."
  - NEVER: "NEVER run `make stop`, `make restart` or `podman restart` on omniroute for a benchmark" keeps its reasoning and ends with "`make stop-llama` is the supported way to free the GPU: it stops only llm-server, and only through G2". "NEVER flip `llm_server.enabled` ..." becomes: flip it only with `make llama-enable MODEL=<alias>` / `make llama-disable` (they back up first and write atomically), and only after G3.
  - Gotchas: the `llm_server.enabled: false` bullet ends "ask before enabling it with `make llama-enable MODEL=<alias>`".
- [ ] **Step 5: Edit `references/models-yml.md`:** "Where things live": `llm_server.enabled` is flipped by `make llama-enable MODEL=<alias>` / `make llama-disable` (still ask first). Live-phase heading becomes "**Live phase (SKILL.md step 7, only after G3).**". Steps: (4) add the new entry to the live file with a gated `yq -i` after a manual timestamped backup (`llama-enable` only selects an existing alias); (5) `make llama-enable MODEL=<alias>` (own backup, validates the alias, sets `llm_server.enabled`, atomic); (6) `make setup` for download, GGUF validation, image pull and budget check (the GPU prompt now defaults to yes because step 5 persisted `llm_server.enabled: true`; keep the floating-image warning and `make check-setup`); (7) `make start-llama` (budget gate, render, starts only llm-server, never touches OmniRoute, System One or remote-setup; `make start` instead when the stack is down), then `make status`, `make check-server`, `make check-with-agents`, `make benchmark`; (8) rollback: `make stop-llama`, `cp -p <backup> ~/.config/llm-env/models.yml`, `make start-llama`.
- [ ] **Step 6: Edit `evals/evals.json`:** in `onboard-plan-with-server-running`, `expected_output` says "stopping uses make stop-llama (only the llm-server container), not make stop or restarting omniroute"; assertion 3 becomes "The stop command is make stop-llama, which stops only the llm-server container; it does not use make stop, make restart or restart omniroute"; assertion 5 becomes "The plan backs up the live models.yml with a timestamped copy before editing it (make llama-enable MODEL=<alias> takes that backup itself)". In `gateway-only-host`, assertion 2 becomes "The answer says effort runs need the model served and that enabling local inference goes through make llama-enable MODEL=<alias> (then make setup for the download and make start-llama) after the user approves". Keep every other eval and assertion unchanged.
- [ ] **Step 7: Verify:** `uv run pytest tests/skills -q` passes, including `test_skill_prose_has_no_long_shell_logic` on the edited text (none of the spans Steps 4-5 write contains `;`, `&&`, `||`, `while` or `until`; if a span written here or by parallel work trips the guard, split it into separate spans or move the logic into a `$SK/scripts/` file with `--help` and a test, never relax the guard); `shellcheck -s bash .claude/skills/onboard-local-model/scripts/*.sh` is clean; `rg -n -F -e 'stop llm-server' -e 'stops an active unit' -e 'compose service stop' .claude/skills/onboard-local-model` prints nothing (exit 1); `make validate && make test`.
- [ ] **Step 8: No commit.** `git status --short .claude/skills tests/skills` still shows both trees untracked (`??`); record the six files this task touched (`SKILL.md`, `references/models-yml.md`, `evals/evals.json`, `scripts/bench-fit.sh`, the new `scripts/wait-gpu-release.sh`, `tests/skills/onboard-local-model/test_onboard_local_model_scripts.py`) for Task 9's report.

---

### Task 9: Final check and live acceptance hand-off

**Files:**
- Modify: `docs/superpowers/plans/README.md` (this plan's index row)

- [ ] **Step 1:** `make validate && make test` on the final tree, plus `shellcheck -s bash .claude/skills/onboard-local-model/scripts/*.sh`. Both clean.
- [ ] **Step 2:** `git log --stat` for this work's commits (Tasks 1-7) shows only: `tools/lib.sh`, `scripts/start.sh`, `scripts/stop.sh`, `scripts/llama-config.sh`, `scripts/help.sh`, `setup/render-unit.sh`, `Makefile`, `llmenv.py`, `tests/test_shell.py`, `tests/test_docs.py`, `tests/test_cli.py`, `AGENTS.md`, `README.md`, `QUICK_START.md`, `.agents/architecture.md`. `git status --short` shows nothing else modified by this work; these entries were there before it started and are expected: ` M docs/superpowers/plans/README.md`, `?? docs/superpowers/plans/2026-10-24-observe-mvp.md`, `?? docs/superpowers/plans/2026-10-30-ai-kit-lite-executor-routing.md`, `?? docs/superpowers/plans/2026-10-30-focused-start-and-llama-toggle.md`, `?? docs/superpowers/plans/2026-10-30-local-combo-and-capacity.md`, `?? .claude/skills/`, `?? tests/skills/` (other untracked plan files added by parallel work are expected too; none of this work's commits may include them). Report that `.claude/skills/onboard-local-model/` and `tests/skills/` stay untracked and contain Task 8's six edited files.
- [ ] **Step 3:** `podman ps -a` shows no `spike-*` container, `podman images` no `example.invalid/spike-img` tag, and no process started by this work remains (check by PID).
- [ ] **Step 4:** In `docs/superpowers/plans/README.md`, set this plan's row (`2026-10-30-focused-start-and-llama-toggle`) to `DONE` with the note "live acceptance (Task 9 Step 5) is the owner's; skill edits uncommitted with the untracked skill tree". Leave it uncommitted with the index's other pending edits.
- [ ] **Step 5: Live acceptance is the owner's only** (it touches the running stack; the agent must not run it). Record `podman inspect --format '{{.Name}} {{.State.StartedAt}} {{.Image}}' omniroute llm-server remote-setup` (plus `system-one` when enabled) before and after each step: `make start` on the running stack (every `StartedAt` and image ID unchanged); `make stop-llama` (only llm-server stops; OmniRoute `StartedAt` unchanged); `make start-llama` (llm-server healthy, OmniRoute unchanged); `make check-server`.

No commit.

---

## Self-Review

- Spec coverage: non-disruptive start (Task 3), restart unchanged in the Makefile and honest in both unit states (Task 2 inactive-unit `down`, Task 3 verify step), explicit stop (Task 1 Phoenix guard, Task 2), start-llama/stop-llama (Tasks 1, 4), llama-enable/disable (Task 5), unit flags (Task 2, gated on Task 0 (g)), no pre-pull on direct `up` (Tasks 3, 4, gated on Task 0 (h)), targets/help (Task 6), docs and the CLI apply hint (Task 7), research spike (Task 0), skill update with the owner's requirements (a)-(e) (Task 8: live phase targets, bypass gates announce-then-proceed, scripts-not-one-liners with `wait-gpu-release.sh`, layered/isolated/delegated benchmarks, evals), final check (Task 9, last), six open questions plus two round-2 decisions (Decisions 1-8).
- Round-1 findings: all eight critical/high and the eight mediums are resolved or answered in "Review responses (round 1)". Round-2: three of six critical/high rejected with reproduced evidence (R-C1, R-C2, R-H3), three fixed (N-H1, R-H1, R-H2); mediums and lows answered in "Review responses (round 2)".
- Test counts: Task 1 five new (four red, one guard); Task 2 one tightened plus two new (two red, one guard); Task 3 two rewritten plus four new plus one strengthened guard (five red); Task 4 eight new (seven red, one guard; the Step 2 selection also passes Task 1's `test_stop_llama_rejects_an_unknown_scope`); Task 5 six new; Task 6 one new; Task 7 two new doc tests plus one strengthened CLI test (three red); Task 8 eight new (seven red, one guard) plus two parameter cases and one extended assertion.
- Names: `container_running`, `llm_server_running`, `PODMAN_LLM_SERVER_RUNNING`, `BUDGET_INFEASIBLE`, `FIXED_DATE_STAMP`, `UNIT_ACTIVE_FILE`, `ONBOARD_PREFLIGHT`, `RECOVER_LINE`, `log_warn`, `scripts/llama-config.sh enable|disable`, `wait-gpu-release.sh --below-mib`, the `up` strings `up -d --no-recreate --no-deps --pull=never` and `... --pull=never llm-server`, the stop warning "server unit is not active; removing any containers it left running", the hint "run 'make restart' to apply config changes" and the Recover line are used identically across tasks.
