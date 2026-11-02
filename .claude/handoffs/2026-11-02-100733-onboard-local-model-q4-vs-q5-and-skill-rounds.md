# Handoff: onboard-local-model skill, ornith Q4 vs Q5 decision, and what is blocked

## Session Metadata
- Created: 2026-11-02 10:07:33
- Project: /var/home/bazzite/git/llm-env
- Branch: main (local only, nothing pushed)
- Baseline commit: 700f5df1073e5cea27b7b8fdcbabf24f7e155e5a
- Session duration: about 4 days of wall clock (2026-10-29 to 2026-11-02), mostly background agents

### Recent Commits (for context)
  - 700f5df feat(cli): agent-runtime-cap prints the bounded runner's runtime cap
  - 0657242 feat(lib): llmenv_budget_json reads a budget verdict whatever its exit code
  - 5c0c017 docs(agents): keep pytest's temp dir outside ./tmp
  - 4614af9 fix(capacity): report the layers a ladder ran instead of 'of 5'
  - 68cd8d8 docs: state Laya's interference limits without client details

## Handoff Chain

- **Continues from**: None (fresh start)
- **Supersedes**: None

## Current State Summary

The /goal is a local model that runs beside System One (Laya) and OmniRoute without hurting them, with a clear capacity card, a `my-coding-lite` combo, improved make targets, and the best context and tokens/s for the host (RX 9070 XT 16 GB, 31 GB RAM). That part is done and verified live: `ornith-15-9b` Q4_K_M serves at ctx 262144 (reply cap 8192), the interference gate passes, the five stack containers are untouched, the card is current, `my-coding-lite` shows in pi and opencode, `make setup-local-llm-agents` ran. Two goal conditions are NOT met and are blocked by others: the ai-kit executor routing and the `ai-kit-spec-review` writing-plans change (reference patches only; the ai-kit session deferred it), and the `onboard-local-model` skill never got both judge lanes at 90 % at once (18 rounds; last native 87.2 %). Plan T (four real defects found in round 18) was written and reviewed but its executor died on the weekly usage limit before editing anything. The wakeup cron was removed.

## Codebase Understanding

### Architecture Overview

- Local llama.cpp router (Vulkan image) in a rootless podman compose stack, fronted by OmniRoute; config in `~/.config/llm-env/models.yml`; repo targets (`make start-llama`, `stop-llama`, `llama-enable`, `llama-disable`, `local-combo`, `model-capacity`, `model-report`, `model-interference`) are the only sanctioned way to change llm-server. Only llm-server may change during tests.
- The skill (`.claude/skills/onboard-local-model/`, untracked) is a router: `SKILL.md` (capped at 11,000 bytes by a test; now 10,993) points to `references/*.md` (onboard-flow, served-alias, local-gguf, tuning, scoring, fit-math, selection-criteria, anchors, system-one, runtimes, challenges) and `scripts/*.sh`. Tests live in `tests/skills/onboard-local-model/` (untracked, about 409 tests, many pin exact prose).
- `jev-fast-decision` is treated as a black box and `ai-kit` is never named in the skill or product code (a guard test enforces it).

### Critical Files

| File | Purpose | Relevance |
|------|---------|-----------|
| `tmp/final/h2h-q4-vs-q5.txt` | Q4 vs Q5 head-to-head, 40 seeds | basis of the Q4 decision |
| `tmp/final/perf-matrix.md` | quant x cache x effort numbers and missing cells | answers "with or without cache/effort" |
| `tmp/final/tuning-final.md` | what was tuned and what is live | tuning evidence |
| `tmp/final/haiku-vs-q4.md` | Haiku run failure report and Q4 baseline | Haiku comparison is blocked |
| `tmp/research/qwen-next.md` | Qwen "Next" research (Qwen3.6-35B-A3B is the smallest viable) | owner then said to forget Qwen3 |
| `tmp/judges/r1*-summary.txt` | judge verdicts per round and lane | grade history |
| `tmp/ai-kit-lite-executor-patches/` (REPLY.md, JEV-NOTES.md, APPLY.md) | ai-kit reference patches and the ai-kit session's answer | blocked item |
| `docs/superpowers/plans/2026-11-01-judge-round-18-fixes.md` | plan T, reviewed, not executed | next work |
| `tmp/plan-t-review/review-round-1.md` | review of plan T with required amendments | apply before executing |

### Key Patterns Discovered

- Process that worked: replan by opus xhigh with `superpowers:writing-plans`, review with a native lane plus the referee lane, execute task by task with a fresh implementer and reviewer, then judge. Reviewers refuted most of the referee lane's findings; only the native lane found real defects.
- Judge scores oscillate by about 4 points per lane and round, and adding text lowered D5/D8 later; do not chase 90 % with more prose.
- Pytest must use `--basetemp` outside `./tmp` (tests assert they do not run inside the repo tmp); a conftest check and an AGENTS.md bullet enforce it.

## Work Completed

### Tasks Finished

- [x] ornith-15-9b onboarded, tuned, Q4 chosen over Q5 (34/40 vs 27/40 solved, p=0.016; tg 84.7 vs 77.5 tok/s; Q5 +678 MiB VRAM)
- [x] Non-disruptive make targets, capacity card and report, interference gate, `my-coding-lite` combo
- [x] Skill built and restructured through plans G to S (all executed and verified except T); 3 tracked commits of lib/cli/AGENTS changes
- [x] Three audits (ponytail, simplify, entropy) folded into plan O and fixed
- [x] Perf matrix and Qwen research written

### Files Modified

| File | Changes | Rationale |
|------|---------|-----------|
| `docs/superpowers/plans/README.md` | rows for plans G to T | index of plans (tracked, uncommitted) |
| `.claude/skills/onboard-local-model/**`, `tests/skills/**` | the whole skill and its tests | untracked on purpose; owner has not decided to commit |
| `docs/superpowers/plans/2026-10-31-*.md`, `2026-11-01-*.md` | plans A to T | untracked; keep all plans (AGENTS.md) |
| `.claude/handoffs/` (this file) | handoff | created by the session-handoff skill |

### Decisions Made

| Decision | Options Considered | Rationale |
|----------|-------------------|-----------|
| Keep Q4_K_M at ctx 262144 | Q5_K_M, Q6_K | rule set before measuring: Q5 needs a supported benefit; it showed a supported loss |
| Stop rewriting prose to chase the judge score | continue rounds | scores did not rise; real defects only |
| Plan T: under bypass re-tag the old image on revert (do not stop at G3) | stop at G3 | owner works in bypass; stopping would block measured keeps |
| Referee lane skipped for plan T review and round 18 | retry | router-env/my-referee-two members out of quota until 2026-11-06; rule: never switch model/provider |
| Do not touch the ai-kit repo | rebase/apply patches | ai-kit session owns it and deferred the work until after its v1.3 merge |

## Pending Work

### Immediate Next Steps

1. Weekly usage limit resets 2026-11-05 22:00 (America/Santiago). Then execute plan T: resume or restart the executor with the amendments in `tmp/plan-t-review/review-round-1.md` (flag mode-000 trick via `stat %Z` change time plus a transcript scan for other runs' folder names; bypass: record and re-tag the previous image Id instead of stopping at G3; exit-code mapping for detached downloads; stop before G1 under bypass with no GPU device; `locked=()` before the trap; chmod before cleanup).
2. Run the skill test file once (`uv run pytest tests/skills -q` with `--basetemp` under /var/tmp, TMPDIR outside ./tmp) to confirm 409 passing before and after.
3. Ask the owner which Haiku 4.5 route to use, then run the same 40 seeds (tmp/h2h/seeds.json, generator id g-aa7e81c15f73) with `tmp/final/haiku-vs-q4.md` as the report.

### Blockers/Open Questions

- [ ] Haiku 4.5: the only pi route `opencode/claude-haiku-4-5` returns 401 although `pi auth check` says ready; alternatives `ddgw/claude-haiku-4-5` and `aug/haiku4.5` are other providers (owner's call).
- [ ] ai-kit: executor routing and writing-plans change wait for the ai-kit session's v1.3 merge; it asked for patches to be remade against the new main (REPLY.md).
- [ ] Referee lane quota (until 2026-11-06 for the zai members; grok out of credits).
- [ ] Owner decisions: commit the untracked skill/tests/plans or not; leave the three unused event-pipeline modules (default) since deleting changes the generator id; plan G Decision 9(c) was read as "keep open".

### Deferred Items

- Real-effort (reasoning-enabled profile) and cache-off Q4/Q5 cells of the perf matrix (about 15 GPU minutes per quant for L5 x8; needs a model swap through repo targets).
- KV q8_0 (computed offline 2026-11-02 from `llmenv budget`: at ctx 262144 q8_0 KV is about 4352 MiB against 3072 MiB for q5_1, required about 10377 MiB vs 10288 available, so it misses by about 89 MiB; by the repo gate it fits up to about 256,000 ctx; an older sweep measured about 44 % faster prefill at 131K with q8_0, never re-measured; the cheapest experiment is llama-bench with q8_0 at ctx 256000 after `make stop-llama` and a fresh models.yml backup) and batch-size tuning; the `llmenv sample` and shared `common.sh` refactors; rewriting the prose-snapshot tests (about 25 % of the skill test file). All judged not worth the risk now.

## Context for Resuming Agent

### Important Context

- Do not claim the 90 % goal. Report the latest honest grade: native 87.2 % (round 18), referee 81.6 to 94.4 % across rounds with most of its findings refuted; the best past numbers came from earlier states of the skill.
- Never restart or recreate omniroute by hand; only llm-server may change; no push; commit only tracked repo code; keep `tmp/aa-key.txt` and `tmp/typesafe.txt`.
- The owner wants short status messages and a single audit agent applying the principles of ponytail/simplify/reducing-entropy, not three separate skill runs.
- Failure ladder for external runs: retry the same lane 3 times, then change session up to 3 times, then report with evidence; never switch harness, provider or model.

### Assumptions Made

- "q4 vs 45" meant Q4_K_M vs Q5_K_M; "Qwen 4 Next" research is moot because the owner said to forget Qwen3.
- Host facts used in decisions: Laya takes about 4.2 GiB VRAM, about 10.8 GiB usable for llama.cpp, RAM floor about 14 GiB available.

### Potential Gotchas

- `pi auth check` can say "ready" while live requests return 401; only a live request proves a route.
- `SKILL.md` has 7 bytes of margin under its test cap; new text must move to references.
- Many tests pin exact prose; run the whole skill test file after every text edit.
- Agents that run two pytest suites at once have caused spurious failures; run one at a time.

## Environment State

### Tools/Services Used

- Stack: omniroute, remote-setup, system-one, phoenix (up 5 days), llm-server (Q4 serving, healthy), via rootless podman and repo make targets.
- Cross-AI referee runner is read-only via `cross-ai.mjs --prefer opencode --model-opencode router-env/my-referee-two` (currently quota-blocked).

### Active Processes

- None started by this session are still running. The session-only wakeup cron was deleted.

### Environment Variables

- `HF_TOKEN` (optional, planned for gated repos, never printed), `LLM_ENV_MODELS_DIR`, `LLM_ENV_CONFIG`, `LLM_ENV_ASSUME_YES`, `LLM_ENV_SCOUT_OUT`, `TMPDIR`.

## Update 2026-11-02 10:20 (owner asked to stop all work)

- Owner asked to stop: no more tasks, no schedules, everything left here. The KV q8_0 sweep was started and stopped after its first two throughput rows; llm-server was restored with the repo targets (`make start-llama`, `make check-server` 16 passed) and no scout container or process remains. Backup of the config taken before: `~/.config/llm-env/models.yml.20261102T101045.kvq8.bak` (gpu.backend/image unchanged). `models.yml` was not edited.
- Partial result (ctx 256000, ubatch 512, fa on; `tmp/final/kvq8-partial-results.jsonl`): q5_1 pp/tg 2502/88.9 at depth 0 and 1300/79.3 at 32K, 5833 MiB; q8_0 2547/88.5 and 1513/77.8, 6126 MiB. So q8_0 gives about +16 % prefill at 32K and about -2 % output speed, at +293 MiB. Not measured at 131K. Not applied; the live config stays q5_1 at ctx 262144.
- No cron or wakeup is scheduled (`CronList` empty). The Haiku comparison was never run (route returns 401), so there is no measured Haiku vs Q4 number.

## Update 2026-11-02 11:30: reboot analysis, verification and commits

- Verification before committing: `make validate` clean; `make test` 2596 passed, 7 skipped on the rerun (one earlier run had 6 failures that did not repeat and whose names were not captured; earlier runs also saw a flaky `tests/test_update_omniroute_script.py` port clash: treat as flaky, look at it if it recurs); skill tests 409 passed; `make check-server` 16 passed; all five containers healthy.
- Reboot finding (not fixed, owner asked only to check): at the last boot (2026-10-28 05:44) `llm-server.service` FAILED within 1 s (journal: `External network [llm-env-phoenix_internal] does not exist`); the stack did not come up by itself and only started when `make start` was run at 05:50:28 (unit active 05:50:47, OmniRoute logged ready at 05:50:29). Cause: `docker-compose.yml` declares the Phoenix network `external: true` but only `phoenix.sh start` (ExecStartPost, runs after ExecStart) or `make start` creates it, and 2026-10-28 was the first boot after Phoenix landed (2026-10-20). The network config is now persisted on btrfs (`~/.local/share/containers/storage/networks/llm-env-phoenix_internal.json`, created 2026-10-28 05:50) and it is `external` in both compose files, so a normal shutdown should not remove it and the next boot should work; this was reasoned from files and logs, NOT tested (no reboot was done). Hardening to consider: an `ExecStartPre` that runs the network creation from `phoenix.sh ensure_internal_network`.
- Expected time to usable after a reboot if the unit succeeds: OmniRoute container `start_period` 10 s with 5 s checks (about 10 to 15 s after container start, observed ready 1 s after start), System One `start_period` 2 min (model load), llm-server 20 s plus model load; `make start` itself took 19 s. Unit has `After=network-online.target` but that target does not order anything in the user manager; `Linger=yes` is set so the user manager starts at boot. `llm-env-omniroute-heal.timer` and `llm-env-zai-reset.timer` are enabled. A failed transient scope `llm-env-agent-*.scope` (a pi run) sits in `systemctl --user` and is harmless.
- Check after a reboot before running `make start`: `systemctl --user status llm-server.service` and `journalctl --user -b 0 -u llm-server.service | head`, then `podman ps`.
- Commits: the 39 unpushed commits of this work were compacted into 6 logical commits plus the 4 latest kept (tree identical, verified with `git diff`; backup tag `backup/pre-compact-20261102` points to the old tip), then the skill and tests, and the plans, were committed. Nothing is pushed (17 commits ahead of origin/main in total).

## Update 2026-11-02 12:30: boot unit fixed, tests fixed, pushed

- Boot: `llm-server.service` now runs `scripts/start.sh` (exactly what `make start` runs) with `LLM_ENV_BOOT_UNIT=1`, so what is enabled in models.yml decides what starts. In boot mode start.sh does not re-render and does not `systemctl start` the unit it is running in; it runs `phoenix.sh prepare` first (the compose file references the Phoenix network as external, which was the 2026-10-28 boot failure) and then `podman compose up -d --no-recreate --no-deps --pull=never`. The unit sets PATH to the directories of uv, jq, yq, curl, podman and systemctl plus the system ones, `Restart=on-failure` with `RestartSec=30`, at most 4 starts per 15 minutes, `TimeoutStartSec=900`. Phoenix start moved out of `ExecStartPost` (start.sh does it); `ExecStopPost` still stops it. Verified: `systemd-analyze --user verify`, boot mode dry run on the live stack (exit 0, containers unchanged). NOT verified by an actual reboot.
- After a reboot: `systemctl --user status llm-server.service`, `journalctl --user -b 0 -u llm-server.service | head -30`, then `podman ps`. If the unit shows `activating (auto-restart)` it is retrying; do not run `make start` until it settles or fails for good.
- Tests: the six failures seen earlier were not flakiness; HUP/INT tests failed when pytest was launched with `nohup` or as a background job (those signals are inherited as ignored and a shell cannot trap them). `tests/signal_helpers.py` now starts those processes with default signal handling. `make test`: 2600 passed, 7 skipped, run from the hostile launcher twice.
- Git: everything committed in logical commits and pushed to origin/main; the local tag `backup/pre-compact-20261102` (old pre-compaction history) was not pushed.

## Related Resources

- `AGENTS.md` (repo rules), `docs/superpowers/plans/README.md` (plan index), `tmp/judges/` (all verdicts), `tmp/audits/r13-*.md`, `tmp/ai-kit-lite-executor-patches/REPLY.md`, `~/.config/llm-env/capacity/ornith-15-9b.json` and its `.report.md`.

---

**Security Reminder**: Before finalizing, run `validate_handoff.py` to check for accidental secret exposure.
