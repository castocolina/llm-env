# Plan: stable OmniRoute limits, then usage observability for OmniRoute and Laya

Date: 2026-11-20. Evidence: `tmp/forensics-20261120/report.md` (read it first). Status: REVIEWED, execution started 2026-11-20. Referee record (read-only `pi` runs, saved under `tmp/forensics-20261120/`): round 2 referee-two APPROVE, referee-one REVISE;
round 3 referee-one REVISE; round 4 referee-two APPROVE, referee-one REVISE (three T4/deployment items and four later-task items, all folded into this text afterwards).
Per the Codex-contract rule of at most three rounds the planner proceeds and reports what remains; T4's design items were fixed before T4 starts.

## Roles and rules (owner, 2026-11-20)
- The planner (Claude, pane wF:pF) plans, delegates and verifies; it does not implement.
- Implementer per task: `pi -p --model router-env/my-coding-lite`; if unavailable, Claude Haiku. One task at a time.
- Verifier per task: a fresh Haiku subagent that receives only the task text, the diff and the evidence paths, never the implementer's
  conclusion. A task is closed only after the verifier reports PASS and the planner has read its evidence.
- Grey calls go through `jev-fast-decision` (advisory); low confidence means the planner decides and records why.
- Hard rules from `AGENTS.md`: never restart/recreate OmniRoute by hand (use `make update-omniroute`, which waits for idle);
  never run `podman system reset|prune|migrate` against the real state; delegated runs are launched detached with output under `./tmp/`
  and polled every 5 minutes; every test process or container is removed by the same run.
- No commits, pushes or merges unless the owner asks.

## Decisions already taken
- D1 (jev, TypeSafe 0.97, Laya agreed at 0.65): measure the anonymous vs file-cache memory split before changing the OmniRoute cap.
- D2 (owner, 2026-11-20; Phoenix has been stopped since P1 and is only restarted by `make start`/boot until T9): end state = everything healthy, observability meeting the initial plan's minimum
  (`docs/superpowers/specs/2026-11-19-stack-observability-parity-design.md`, Codex contract `tmp/goals/stack-observability-codex.md` in the
  `observe-prometheus` worktree) and going beyond it, and Phoenix removed once Grafana replaces it with better data. Until the
  replacement is verified, the Laya panels show a source-up stat (Phoenix was the only Laya request source; it is stopped, see D2b).
  Removal is T9, gated on T8 and on the owner's confirmation; no zombie containers or volumes may remain afterwards.
- D2b (owner, 2026-11-20, direct message in the /goal session; quote: "parity window puede ser hasta que tengamos algo y digamos que ya es
  suficiente, osea con hacer un par de pruebas y ver que ya no hace falta phoenix, de hecho lo podemos detener ahora"): no fixed parity window. Stop Phoenix now (stop only, volume preserved), then compare what
  Phoenix gave with what Grafana shows; a couple of tests that show Phoenix is no longer needed are enough ("equal or better").
  Replaces the 7-day and 30-day windows. Consequence: Laya request metrics stop updating from the moment Phoenix stops until T8 provides a new
  source; the Laya dashboard shows that through its source-up stat. Done by task P1 (export spans to `tmp/phoenix-export-20261120`, then stop).
- D3 (planner, revised): no CPU change yet. Lifetime CPU is 0.017-0.057 cores of 2 and nothing sheds, but the owner saw Node
  throttled (C33) and no throttling counter is collected today. T1 adds `cpu.stat` throttling; the CPU decision waits for that data.
- D4 (planner): the OmniRoute version is not rolled back; the outage was caused by `podman system reset -f` (F1).

## Criteria traceability
The owner's criteria mined from all transcripts are in `tmp/forensics-20261120/criteria.md` (C = owner criteria, M = initial-plan minimums,
W = working rules). Each task below lists the ids it serves. T7 ends with a table of every C/M id marked verified, deferred (with reason) or
not covered. Resolved by the owner (D2b): C36 stop Phoenix now, and C1/M13 remove it after a comparison, not a fixed window; C58 (30-day parity gate) is resolved by D2b: no fixed window.

## Deployment path (how a change reaches the running stack)
- Collector: the user timer `llm-env-observe.timer` runs `scripts/observe-publish.sh` from the worktree
  `.claude/worktrees/observe-prometheus` (path written in `~/.config/systemd/user/llm-env-observe.service`), so an edit there is live at the next 60 s
  tick. The worktree must therefore not be removed or switched to another branch while the timer exists; to move it, re-run `bash scripts/observe-stack.sh up`
  from the new location (it rewrites the unit). Rollback of a collector change: see Staged apply below; verify with the next tick's `llm_env_source_up`.
- Dashboards: `docker/observe/dashboards/*.json` are bind-mounted read-only into Grafana and re-read about every 10 s; a bad JSON shows as a provisioning error in
  `podman logs llm-env-observe_grafana_1`, checked after each dashboard task.
- Compose changes for the observe project: `bash scripts/observe-stack.sh up` (recreates only observe containers; volumes kept).
- Staged apply (required for every task that edits collector or dashboard files, because the timer and Grafana read the worktree live): the implementer works in a
  scratch copy `tmp/stage/<task>/` (rsync of the worktree without `.venv`), runs `make validate` and `make test` there, and writes the diff to
  `tmp/forensics-20261120/<task>/changes.diff`. After the Haiku verifier passes, the planner applies the diff to the worktree in one `git apply`, first copying
  `~/.local/state/llm-env/observe/usage-state.json` to `tmp/forensics-20261120/<task>/usage-state.before.json`, then checks the next tick: `llm_env_source_up` and
  `podman logs llm-env-observe_grafana_1` show no errors. Rollback: `git apply -R` of the same diff and restoring the state backup.
- Source transition: the Laya reader in `pylib/observe_usage.py` reads Phoenix today. T8b switches both the metrics reader and the detail ingestion to the chosen
  source and proves fresh Laya requests and the Laya dashboard work with Phoenix unreachable, before T9 deletes anything.

## Owner directives added later on 2026-11-20 (they change the order)
- D5: "La idea de usar VictoriaMetrics era poder hacer lo que se intento con Phoenix, no me dejes con que simplemente no se ha hecho; la idea no es dejar ese dashboard vacio." The Laya replacement source (T8b)
  is therefore not optional or "later": after the design research T8a and the live probe T8c it is built, applied and shown with data in Grafana, and the Laya dashboard must not stay at n/a. The probe
  T8c (30 real requests through `jev-fast-decision`, local Laya only, from this repo) shows what the service and the skill record today.
- D6: llama.cpp metrics and a ~4B local coding model are a second workstream with its own plan: `docs/superpowers/plans/2026-11-20-small-coding-model-and-llama-metrics.md`.

## Initial-scope items that are deferred or need the owner (visible at approval, not at the end)
Each id is in `criteria.md`. "Deferred" means no task in this plan; the owner approves the deferral or asks for a task.
- M1 collector as a container in the main Compose lifecycle; M18 real Laya build progress: deferred (in the Codex contract, not restated by the owner).
- M19 migration/rollback rehearsal, M21/M22 isolated-staging exit proof, M26/M27 original MVP collector criteria: replaced here by live verification
  in T7 plus the rollback steps in T9. OWNER DECISION: accept live verification instead of an isolated staging run?
- M3 rootless cgroup/DRM access qualification: covered only by what the running collector already reads; no new qualification task. OWNER DECISION.
- M29 ai-kit caller work: belongs to the ai-kit repo and agent.
- C31 volume review (names, contents, last use): deferred; candidates for a later pass.
- `make check-server` fails at its Phoenix readiness stage while Phoenix is stopped (`scripts/check-server.sh` stage for Phoenix): expected until
  T9 removes the stage; not a regression. Agents are told.
- Delegation fallback: the owner authorised Haiku when `pi router-env/my-coding-lite` is unavailable ("si no esta disponible usa haiku"). "Unavailable" means
  three retries and three session changes failed (AGENTS.md ladder); a transient 5xx alone is retried, never a switch.

## Execution gates
- Tasks T2-T5 and T10 start now. T6 needs the owner's value. T8b needs T8a approved. T9 needs T8 accepted and the owner's yes. Items marked OWNER DECISION above are not
  assumed approved: until the owner answers, T7's traceability table lists them as deferred, never as verified.
- Every task ends with: the exact commands run and their output paths under `tmp/forensics-20261120/<task>/`, `make validate` (and `make test` when a `.py` changed) results,
  cleanup of anything started, and a rollback line. The implementer prompt for a task is written from this plan text by the planner and saved next to the evidence.

## Tasks

### P1. Export Phoenix spans, then stop Phoenix (DONE and verified 2026-11-20)
- Done by `pi` (my-coding-lite), verified by a fresh Haiku: 2,542 `laya` spans (2026-11-08 to 2026-11-20T02:44Z) and 0 `default` spans in
  `tmp/phoenix-export-20261120`; Phoenix container stopped, not removed; volume `llm-env-phoenix-data` kept; System One and OmniRoute healthy.
- Durability gap (referee-two #2, verified): `scripts/start.sh` starts Phoenix through `scripts/phoenix.sh` whenever System One is enabled, and there is
  no separate switch, so `make start` or a reboot brings it back. Accepted until T9 removes it; the owner and agents are told not to treat a running
  Phoenix as a regression. T8a evaluates a smaller option (a `phoenix.enabled: false` setting honoured by `start.sh`/`render-unit.sh`) and T9 is
  the permanent fix.

### T1. Measure cache vs anonymous memory per container (blocks the cap decision)
- Files (worktree `.claude/worktrees/observe-prometheus`): `pylib/resources.py` or the live-resources reader it uses,
  `pylib/observe_metrics.py` (`_CONTAINER_GAUGES`), `tests/test_observe_metrics.py`, dashboard `llm-env.json`.
- Serves C4, C33, C25, M14.
- Add gauges `llm_env_container_memory_anon_mib` and `llm_env_container_memory_file_mib` from the container cgroup `memory.stat`
  (`anon`, `file`), and counters `llm_env_container_cpu_throttled_periods_total` / `_throttled_seconds_total` from `cpu.stat`
  (`nr_throttled`, `throttled_usec`), read-only, same bounded worker. Panels: "Container memory: anonymous vs file cache" stacked per
  service and "CPU throttling" per service.
- Done when: unit test for the renderer with a fake `memory.stat`; `make validate && make test` pass in the worktree; after the next
  collector run `llm_env_container_memory_anon_mib{service="omniroute"}` exists in VictoriaMetrics.
- Verifier checks: gauges absent for a stopped container; missing `memory.stat` emits nothing, not 0; no new subprocess.

### T1. STATUS (2026-11-20 13:48 applied live, verified)
- Implemented by `my-coding-lite` in a scratch copy, verified by a fresh Haiku (cgroup cross-check exact, mutation caught, queries valid), applied in one `git apply` (backup in
  `tmp/forensics-20261120/T1/apply/orig`). First live readings: OmniRoute anon 503 MiB / file cache 391 MiB (the 2048 MiB cap hit after a restart is mostly reclaimable cache); CPU throttled
  seconds OmniRoute 34.3 -> 82.2 within about 5 minutes, remote_setup 373 s over 3290 periods, system_one 33 s. Inputs for T6 and for the CPU question (C33).

### T4 / T5 STATUS (2026-11-20 15:06)
- T4 collector: T4e applied live at 14:26 (migration verified on real data: 12 rows counted = 12 expected; series count 620 after old series expired; no counter decreases). T4f and T4g
  (further hardening) are NOT applied: the T4g verifier found a spec contradiction (N2 invalid-type vs N3 ignore bad cursor) and a regression (spans without answers counted as skipped). Open findings kept
  for later: B1 skipped-counter wiped while Laya is down (diagnostic only), corrupt-state corner cases, shared skipped_seen cap. Diffs: `tmp/forensics-20261120/T4f/changes.diff`.
- T5 dashboards: the three dashboard JSON files from T5b are applied live (13/19/14 panels, loaded by Grafana, datasource healthy). The test file change of T5b is NOT applied (weakened `_family`);
  T5c fixes it, the frozen-totals panel and descriptions.
- Lesson: any two pytest runs at once corrupt each other (shared /tmp/pytest-of-bazzite rotation); the test suite itself (T13) copied nested worktrees (~285 MB per test).

### T1c. Real per-interval CPU usage (owner request via the transcript lesson, 2026-11-20)
- Why: `llm_env_container_cpu_avg_cores` is a lifetime average and flattens; it cannot show a busy-spin like the ROCR 1.21 `AsyncEventsLoop` bug that kept Laya at 100 % of a core at idle (session of 2026-10-11). The throttling counters of T1 do not show it either.
- Add counter `llm_env_container_cpu_usage_seconds_total{service}` from `cpu.stat` `usage_usec / 1e6` (None when unreadable: no sample), so `rate(...[5m])` is the real cores in use; panel "CPU cores in use (5 m rate)" per service in `llm-env.json` with a threshold line at 0.5 core and a description "Sustained use while idle means a busy-spin or a stuck loop: see the idle check in the Laya update notes". Same staged copy, verifier and atomic apply as T1 (files `pylib/resources.py`, `pylib/observe_metrics.py`, tests, `llm-env.json`, the dashboard test families set).
- Acceptance: the live series for system_one stays below 0.05 at idle (measured today: 0.0083), and a unit test with a fake cgroup proves the counter and the None case.

### T2. Clear failed units left by the outage
- `systemctl --user reset-failed llm-env-omniroute-heal.service llm-env-zai-reset.service`; confirm both timers still active and the
  next run of the healer logs `ok chatAdmission`.
- Verifier checks: `systemctl --user --failed` lists neither unit; healer journal shows an `ok` line after the reset.

### T13. Stop the test suite from filling the tmpfs quota (found during T3 verification, 2026-11-20)
- Evidence: `/tmp/pytest-of-bazzite` grew from 146 MiB to about 10-12 GiB in seconds during `make test` and hit the user's 12.4 GiB tmpfs quota, so tests errored and
  delegated runs crashed with EDQUOT. Cause: `tests/omniroute_world.py::make_repo_copy` copies the repo and its ignore list lacks `.claude`, so every copy includes the nested worktrees under
  `.claude/worktrees/` (about 870 MiB, mostly the ai-kit clone), roughly 285 MiB per test. Fix: add `.claude` to the ignore list (one item). Until T13 is verified, nobody runs the full
  suite concurrently with another run. Not covered by a criterion; protects every later verification step.
- Verifier checks: before/after size of a repo copy, no test needs `.claude` inside a copy, one small test group passes, then ONE full `make test` alone with `/tmp` usage sampled (peak well below 12 GiB).

### T13b. Make the suite and every delegated run unable to saturate the filesystem (owner, 2026-11-20: "hay que evitar saturar el filesystem", /tmp or tmp/)
- `tests/omniroute_world.py::make_repo_copy`: replace the blacklist with an allowlist (copy only the entries the tests use: `llmenv.py`, `pylib`, `scripts`, `setup`, `tools`, `Makefile`, `docker`,
  `pyproject.toml`, `uv.lock`, `tests/fixtures`, and whatever the two users of the helper read; derive the list by reading them), and remove the copy when each test ends, also on failure.
- `tools/test.sh`: a preflight that aborts with a clear message if the tmpfs used by this user is above 50 % of its quota (or free space below 6 GB) and lists leftover `/tmp/pytest-of-*/pytest-N`
  directories with sizes and ages (it deletes none); and a trap that removes this run's own basetemp when the run ends, even on failure or Ctrl-C. Measure the suite's real peak after the fix and put
  the threshold at about 1.5x that peak.
- Every delegated prompt and verifier prompt written by the planner starts with a disk guard: stop and report if `df /tmp` shows more than 70 % used or the user quota above 70 %; each task removes
  only the temp it created. Repo `tmp/` is not exempt: large artifacts (the 1.1 GB cold backup, `tmp/stop-723` at 2.8 GB from Codex) are listed for the owner, never deleted by agents, and moved to a
  non-repo path only on the owner's yes.
- T11 adds filesystem metrics to the collector and a dashboard row: used and free bytes for `/tmp` (with the user quota) and `/var`, and the size of the largest directory under repo `tmp/`, with warning
  thresholds, so saturation shows up before tests fail.
- Verifier checks: a deliberately filled scratch directory does not break the suite's preflight message; a crash mid-run leaves no numbered `pytest-of-*` directory behind after the trap; the peak is
  recorded.

### T3. Guard against a repeat of the Podman state wipe
- Serves C11, W1, W8, W23.
- Docs only: add to `AGENTS.md` (main checkout) a rule that experiments needing `podman system reset|prune|migrate` or any
  `--root/--runroot` isolation must also isolate `--tmpdir`, `--network-config-dir`, `CONTAINERS_CONF`, `CONTAINERS_STORAGE_CONF`
  and `XDG_RUNTIME_DIR` (or use a separate user) and may never run in the owner's session; include the incident in one sentence.
- DNS probe, in `scripts/podman-state-check.sh`: take the container's own resolver from `podman exec omniroute cat /etc/resolv.conf` (first `nameserver`),
  send one UDP A query with a 3 s timeout, via `timeout 10 podman exec omniroute node -e ...` (read-only), for an external hostname that is measured, not
  hardcoded: the registry host of the container's image reference (`podman inspect --format '{{.ImageName}}' omniroute`, the part before the first `/`, for example
  `docker.io`); never `localhost` or a container name. (Checked: `/api/providers` does not expose provider hostnames, so it cannot be the source.)
  First resolve the same name on the host with `getent ahosts`; if the host baseline fails, the result is UNKNOWN. Output states: OK (answer with at least one
  address), FAIL (timeout or SERVFAIL from the container resolver while the host resolves), UNKNOWN (container not running, exec unavailable, host baseline failed,
  NXDOMAIN, or a resolver that is not a UDP nameserver); UNKNOWN is never reported as OK or FAIL. Tests include the exact broken-aardvark case (resolver address
  present, no reply). Unit tests cover healthy, broken-DNS and
  container-absent cases with faked command output. Printed as a warning line by `make status`, not a failure.
- Add `scripts/podman-state-check.sh` (read-only, 20 lines): compares `crun --root /run/user/$UID/crun list` running ids with
  `podman ps` and exits nonzero with the list when a container runs in crun but Podman says exited. Wire it as a warning line in `make status`.
- Done when: `make validate` passes; the script exits 0 now; a unit test feeds fake outputs for the mismatch case.
- Verifier checks: the script writes nothing, never calls `kill`/`stop`/`rm`.

### T4. Review the existing usage metrics code (written before this plan)
- Serves C6, C7, M4, M7, M8, M30.
- Scope: `pylib/observe_usage.py`, `tests/test_observe_usage.py`, the 3-line wiring in `pylib/observe_metrics.py`.
- The implementer does not rewrite it; a Haiku verifier answers each of these 7 numbered items (and 3b) with evidence, and the implementer fixes only confirmed defects:
  1. cursor correctness when two events share a timestamp; first-run backfill is bounded to 1000 spans and 300 call-log rows (document it);
  2. no prompt, response, key or account identifier is ever put into labels or the state file (`usage-state.json` is mode 0600);
  3. a down OmniRoute or Phoenix leaves counters unchanged and emits `llm_env_source_up{source=...} 0`;
  3b. REQUIRED FIXES, confirmed in the code by referee review (add a test for each). T4a measured the deployed API (`tmp/forensics-20261120/T4a/api-ordering.md`):
     rows are ordered by `timestamp` descending with active rows (status 0) interleaved by their start time, not first; `limit` up to 5000 is honoured so offset paging is not needed
     and is unsafe (new rows shift older ones: 99 of 100 ids moved to a higher offset within 3 s); stuck active rows are hours old, so `timestamp` is the request START; finished
     durations reach 132 s (p99 103 s); probe rows (`requestType` upstream_http_attempt, model-sync) share the list. Therefore: (a) one GET with `limit=1500`, no `offset`;
     finished rows only (status > 0, no `requestType`); a lookback of 10 minutes before the newest finished timestamp so a request that started earlier and finished later is still
     counted, de-duplicated by an id->timestamp map pruned to that window; overflow = the oldest row of the response is still newer than (cursor - lookback) -> the gap is made
     explicit with `llm_env_usage_gap_total` and the cursor advances; a failed fetch changes nothing and sets source_up 0. Also: (b) the de-duplication map replaces the old 50-id list in `_fold`, so no id is lost at a tie; (c) bounded labels, monotonic by construction: an allowlist of up to 40 models is fixed in the state file at first use from the lifetime request counts and is never demoted or
     rebuilt (a model never moves between its own series and `other`, so published counters never decrease); models first seen later map to `other`; route is in {configured combo names,
     `direct`}; the same allowlist applies to every usage family including `history_lines` (requests, tokens, cost) so no family emits an unbounded label; tests cover restart, a new model
     appearing, and a rate query over the boundary; the worst-case series count (routes x models x 10 buckets x 2 histograms plus history families) goes into the T4 verifier report; (d) `history_lines` and the Laya parser emit no sample, not 0, when a usage field is
     missing (M4: unknown is never zero); (e) a restart test: counters after re-reading the same rows from the state file do not change.
  4. label cardinality: project the series count from the current label values now, and re-check after 24 h in T7 (must stay below 3000;
     measure with `count({__name__=~"llm_env_(omniroute|laya)_.*"})`); this item is a deferred checkpoint closed in T7;
  5. the call-log `requestType` filter excludes probes (model-sync, provider tests) and counts only client calls;
  6. collector wall time stays under the 45 s `observe-publish.sh` timeout (measure 5 runs);
  7. record evidence paths for two items that may already be done: VictoriaMetrics memory cap 128 MiB (C32: compose, test and live limit) and
     Laya process VRAM kept separate from device totals (M15).
- Done when: verifier report with 7 answers saved under `tmp/forensics-20261120/t4-verifier.md`, all PASS or fixed.

### T5. Dashboards: finish and verify (OmniRoute, Laya, container)
- Files: `docker/observe/dashboards/{omniroute,laya,llm-env}.json`; the JSON files are the durable artifact and are edited directly (the one-off
  generator that produced the first version is not kept).
- Serves C5, C45, C53, C55, M2, M14, M15.
- Add a "Container inventory" table to `llm-env.json`: one row per service enabled in the rendered Compose file, with state
  enabled-and-running / enabled-and-stopped / unknown (unknown is never shown as 0), uptime, memory current/peak/cap, from
  `llm_env_container_running` plus the enabled-service list; monitoring services included, trial containers excluded.
- Add memory p95 and headroom, swap, pressure (PSI), cap events/OOMs and a cache-hit-rate panel (cached input tokens / prompt tokens,
  a saving indicator) for OmniRoute.
- Apply and record dashboard principles (RED for services, USE for resources, one question per panel, units and thresholds on every
  panel, no panel that is empty without a stated reason); list them at the top of `tmp/forensics-20261120/t5-panels.tsv`.
- After at least 20 minutes of collection, every panel query returns series (or an explicit documented empty state); record per-panel
  result counts in `tmp/forensics-20261120/t5-panels.tsv`.
- Open the three dashboards through Grafana's API and render one screenshot per dashboard if a renderer exists; otherwise state it was not possible.
- Verifier checks: legends are `table` + `right` on every timeseries; confidence panels carry the "self-estimate, not accuracy" note;
  Laya panels show the Phoenix source-up stat.

### T6. Decide the OmniRoute memory cap (gate, owner approves the value)
- Input: at least 24 h of T1 data including one burst; if no burst occurs, extend the window and record "no burst observed" as the evidence.
  The current `memory.peak` of 2048 MiB is a clipped lower bound, not the true peak. If no unclipped peak is observed, either leave sizing blocked or propose, for the
  owner's approval, a temporary higher cap (for example 3072 MiB) solely to reveal the true peak; never size from a clipped number. Verify the modal floor
  and the M23 thresholds (0.80 pre-guard warning, swap, restarts, host memory) from data. The owner's sizing rule (C25, AGENTS.md: cap = observed peak + 20 %) counts file cache because `memory.peak` includes it; if the recommendation
  uses anonymous peak + 20 % instead, say so explicitly and propose the AGENTS.md wording change for the owner to approve. Output: a one-page recommendation with anon peak, file-cache peak and the cap
  from the sizing rule (anon peak + 20 %, or document why file cache must be counted).
- Before applying: announce in herdr to every agent that uses OmniRoute (C15) and confirm none is mid-run; then apply; after the idle-gated update, tell them they may resume.
- Apply only through `uv run llmenv.py resource-override set --service omniroute ...`, `bash setup/render-unit.sh`, then `make update-omniroute`
  (waits for idle). Planner does not run it without the owner's yes.

### T8. Replace Phoenix as the Laya request source (design first, then build)
- T8a (design, delegated read-only research, output `tmp/forensics-20261120/t8-options.md`): compare, for Laya request detail
  (request table, per-request detail, batch vs single, requested/completed questions, status, latency, routing model, usage, question type,
  confidence, truncation, fallback needs) these sources: (1) VictoriaLogs fed by a structured line Laya/the observer already emits, and verify what the running System One image really embeds (`scripts/check-setup.sh` `phoenix_offline_check`; the running image is the observer derivative `localhost/llm-env-system-one-phoenix:<key>` with `LAYA_PHOENIX_URL`), because T9 depends on it,
  (2) the Laya container logs read by the collector, (3) the observer derivative writing a bounded JSONL that the collector tails,
  (4) keeping metrics only (no per-request detail). For each: what fields exist today (verify against the live system, never assume),
  loss during restarts, privacy (hash and shape by default; redacted payload local, opt-in, at most 7 days), memory cost, and minimum
  change to the System One image. The planner and the owner pick one; jev may screen the options but does not decide.
- T8b (build, tasks split after T8a is approved): the chosen source, a Grafana "Laya requests" table with a detail view and session timeline
  where caller-supplied session or run ids exist (never invented), and the comparison defined in the acceptance paragraph below.
  Serves C7, C42, C44, C47, C48, C59, M10, M11, M12, M28. Constraints: do not change the `jev-fast-decision` skill (C59; infer a fallback
  from caller session and output, and label it inferred); show `needs_fallback` and low-confidence counts so "how many Laya answers were low
  confidence and went to Jev" (C44) is answerable; import the Laya history from the verified Phoenix export (spans 2026-11-08 to 2026-11-20T02:44Z in
  `tmp/phoenix-export-20261120`; the owner asked for 14 Oct, so say that 14 Oct to 8 Nov is not recoverable) into the detail store chosen in T8a
  (VictoriaLogs first, M9), preserving timestamps and without duplicate counts; numeric aggregates stay in VictoriaMetrics. Label the collection gap
  (Phoenix and its exporter were down 11-20 00:04 to 08:20 local and stopped again by P1) instead of implying complete history. "Fallback taken" needs client
  evidence; show `needs_fallback` and any inferred outcome as separate, labelled series, and mark "fallback taken" blocked, not verified, where no client evidence exists.
  Test single, batch, error, partial and truncated records and a real caller before T9.
- Acceptance (owner D2b): a short comparison, not a fixed window: from the exported Phoenix spans (`tmp/phoenix-export-20261120`) list
  what Phoenix showed per request (the M10 fields) and show for each field the Grafana panel that gives the same or better view, plus a
  few live test requests (single, batch, error) that appear in the new source. Evidence under `tmp/`. The owner then confirms.

### T9. Retire Phoenix (gated: T8 accepted; Phoenix is already stopped by P1 and stays stopped, volume preserved)
- Ordered cutover, stop on any failure or deferral, never touch OmniRoute: (1) verify the export against Phoenix (`bash scripts/phoenix.sh start`, compare span ids and timestamps per project, `bash scripts/phoenix.sh stop`; never a bare `podman start`)
  and restore-test the volume backup into a throwaway volume; (2) edit the System One build and update scripts to remove only the Phoenix-exporter pieces (`LAYA_PHOENIX_URL`, `phoenix_observer.py`, the trace network) and keep whatever
  instrumentation the source chosen in T8 requires (an instrumented derivative may remain); remove
  `check-setup.sh`'s `phoenix_offline_check` and the Phoenix stage of `check-server.sh` in the same change, with tests; (3) `bash setup/render-unit.sh`;
  (4) idle-gated System One replacement through `LAYA_VERSION=<the current pin from docker/system-one/Dockerfile> make update-system-one` (an unset LAYA_VERSION
  takes the newest PyPI version, which is not wanted here); its config-drift path has no automatic rollback, so before step (4) keep the previous image tag and rendered
  compose file, rehearse failure and rollback once on disposable resources (a separately named throwaway container, removed in the same run), and stop if the
  rollback cannot be shown to bring back a healthy System One that answers a real Laya request through an idle-aware recreation. Because `scripts/update-system-one.sh` re-renders from current
  code and config and its same-version drift path has no automatic rollback, the rollback is rehearsed through that same path with the previous commit of the build scripts, the previous
  `models.yml` and the previous image restored together; if that cannot be rehearsed, T9 stops before any deletion; (5) after the actual System One swap, fresh single, batch and error Laya requests appear in the new source and the Laya dashboard; Phoenix data is deleted only after that; (6) only then remove
  the ownership-verified Phoenix objects below. The export stays under `tmp/` with owner-only permissions until the owner deletes it; its completeness is checked in step (1) by comparing the paginated span ids and the earliest/latest timestamps per project between Phoenix and the export (counts alone are
  not enough), `stat -c '%a %n'` on every artifact, and a restore test; the known collection gap is recorded in the summary, not hidden.
- Remove the Phoenix service, its volume, its network (`llm-env-phoenix_internal`), the observer derivative image and tag, the compose and render
  code, Make targets, README/AGENTS.md mentions, tests and the `phoenix`/`phoenix-trial` docker directories; keep a final backup of the Phoenix data
  volume under `tmp/` until the owner deletes it. Before choosing the rollout path, T8a verifies whether System One's image embeds the Phoenix exporter (the observer derivative
  `llm-env-system-one-phoenix`); if it does, use the idle-aware `make update-system-one`; removing only the Phoenix compose entry needs
  `bash setup/render-unit.sh` and no restart because Phoenix is already stopped.
- Acceptance: `podman ps -a`, `podman volume ls`, `podman network ls` list no Phoenix object; `make check-server` passes without the Phoenix stage;
  `rg -i phoenix` finds only historical docs and the changelog.

### T10. Request accounting inventory for every HTTP service (initial plan M5/M6)
- Output `tmp/forensics-20261120/t10-http-inventory.md`: for each enabled listener (OmniRoute 20128, System One 8010, remote-setup 20130,
  Phoenix 6006 (stopped, pending T9: list it as retired, not as a gap), Grafana 3000, VictoriaMetrics 8428, llm-server when enabled) the verified native signal for arrivals, completions, status
  class and latency, what is missing, and the smallest instrumentation (no new gateway container for monitoring, C46; a proxy only if it
  provably misses no direct ingress). remote-setup needs a counter because successful requests are absent from its access logs; never
  read its `/config` body. The planner reviews the inventory and names ordered implementation tasks (T10a, T10b...) before T7; each has controlled success and failure requests, expected counter deltas, and
  an exact acceptance command.

### T11. Retention, disk budget and free-space guard (M16, M24, M25, C49)
- Measure collector and store overhead; confirm VictoriaMetrics retention 365 d (M24: metrics 365 d / 2 GB) and, for every store selected in T8a, the M24 retention and size
  (logs 90 d / 5 GB, detail records 30 d / 2 GB, combined budget 10 GB) with enforcement that actually bounds disk (store flags such as `-retentionPeriod` and size limits, tested
  by reading the effective flags from the running container), not an alert alone; plus a free-space alert under 100 GB or 15 % free on `/var`, shown on a dashboard; confirm OmniRoute call-log retention env (180 d, 20000 entries) matches M25; document the combined budget.
  No OmniRoute change is applied except through `make update-omniroute`.

### T12. Operator surface and health (M17, C20, C21, C22)
- Add health checks to Grafana and VictoriaMetrics in the observe compose; `make status` prints the Grafana URL and status and
  `make show-secrets` prints the labelled initial password from the existing private file (never in startup logs); Grafana stays
  authenticated and loopback-only. Verify, without rebooting, that the observe timers and containers return after a reboot
  (linger enabled, restart policy, units enabled) and record the OmniRoute time-to-ready seen at the last start (about 18 s).

### T7. Final acceptance (planner, no delegation)
- Run: collector 5 times, query each panel, `make resources-snapshot` (expect no ALERT after T6), `make check-server` (after T9 the Phoenix stage is gone).
- Blocked until the T10 implementation tasks (named after the planner reviews the inventory) have per-service controlled success/failure reconciliation, probe separation
  and explicit unknowns, or the owner approves a named deferral.
- After at least 24 h: `count({__name__=~"llm_env_(omniroute|laya)_.*"})` below 3000; save the query output path in the traceability table (closes T4 item 4).
- Resource health over 7 days after T6 (all M23 thresholds, each with its query saved under `tmp/`): peak/cap at most 0.83; zero cap hits (`memory.events max`); zero OOM;
  no memory stall (PSI full avg300 below 5); swap creep below 0.5; admission shedding zero or explained; the 0.80 pre-guard warning present as a panel threshold;
  healer restarts at most one per day (the initial criterion; the 6 h cooldown is only the mechanism); no unexplained container restarts (`llm_env_container_uptime_seconds` resets match healer or updater records);
  host available memory above the initial plan's 2 GiB; peak/cap at most 0.83 is evaluated per service and System One is the declared exception (report its ratio separately, AGENTS.md). Any threshold changed from the initial plan is listed for owner approval before M23 is marked verified.
- One advisory `jev-fast-decision` pass over the traceability table (C9); the planner decides every low-confidence item and records why.
- Traceability table: every id in `criteria.md` marked verified / deferred (reason) / not covered, with its evidence path.
- Report to the owner: each requirement as verified / blocked / not run, with evidence paths and what remains
  (Phoenix retirement, 30-day parity, T6 value, Codex staging worktree disposition).

## Order
P1 and T2 are done. T13, then T13b, and T3 first (small; they unblock safe full-suite verification), T4 first (it also edits `pylib/observe_metrics.py`), then T1 (same file; different functions), never in parallel, then T10, T5, T8a, T12, T11, T6 (needs 24 h of T1 data),
T8b, T9, T7 (T9 before T7 so the final report closes the Phoenix items). One implementer at a time per file scope; a Haiku verifier closes each task before the next starts.

## Not in this plan (flagged for the owner)
- The Codex staging worktree `observe-parity-20261119T214610Z` (119 uncommitted changes) and its receipts/payload-capture hardening:
  review what is reusable for T8, then decide its disposition; nothing is deleted without the owner.
- Collector as a container in the main Compose lifecycle (M1; C61 prefers containers) and Laya build-progress streaming (M18): both are in the
  Codex contract and not restated by the owner; deferred until T1-T12 are done, listed in the T7 traceability table as deferred.
- ai-kit-side caller work (M29: session/run/caller headers, escalation ladder logging) belongs to the ai-kit repo and agent; T8b only records
  what the collector can infer without it.
