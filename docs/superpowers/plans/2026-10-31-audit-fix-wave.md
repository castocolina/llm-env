# onboard-local-model Audit Fix Wave Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix what three independent audits of the onboard-local-model skill found, as decided by the owner: the agent's solution is scored under a time limit, the interference gate fails when Laya was never measured under load, the bundled fixed tasks go (generated challenges only), and a set of smaller hardening and entropy items, without disturbing the Q4-vs-Q5 campaign that is still running on this host.

**Architecture:**
- Scoring moves into the challenge generator: `gen-challenge.sh --score DIR` runs `gate.mjs`'s per-file test runner (process-group kill on timeout) and replaces `tasks/verify.sh`. The interference rule stays in `pylib/interference.py`, which gains the two "never measured" failures.
- The bundled `tasks/` tree, its reference solutions and the `--challenges`/`--tasks` flags are deleted; one cheap offline regression (seeds 1-3 of every layer through the validity gate) replaces them.
- One shared shell helper, `edit_config_atomically` in `tools/lib.sh`, becomes the only way scripts rewrite the live `models.yml` (backup, private copy, `llmenv migrate-config` validation, atomic rename); `start.sh` uses it first, and only when its values change.
- Tiers: P0 = Tasks 1-3, P1 = Tasks 4-8, P2 = Tasks 9-16 (each droppable on its own, except that Task 16 needs Task 15). Task 17 (final verification) runs last.

**Tech Stack:** bash + shellcheck, node >= 20.14 ESM (`--test-force-exit`; host has v24.21.0), jq, yq, Python 3.11 with pytest (+ xdist), ruff, pyright.

**Spec:** the owner decisions given to the plan writer on 2026-10-31 (restated in Decisions and Global Constraints, so this plan stands alone) and the three audits they rule on: `tmp/audits/ponytail.md`, `tmp/audits/simplify.md`, `tmp/audits/entropy.md` (scratch, git-ignored; every finding this plan relies on was re-checked against the code, see Facts and Findings rejected). Sibling plans, not edited here: `2026-10-30-focused-start-and-llama-toggle.md`, `2026-10-30-local-combo-and-capacity.md`, `2026-10-31-skill-model-or-none-and-generated-challenges.md`.

## Global Constraints

- **Precondition (owner, ordering):** Task 1 runs first and blocks every other task until `tmp/final/report-q4-vs-q5.md` exists and no `effort-run.sh`, `latency-profile.sh`, `bench-fit.sh` or `model-interference.sh` process runs. The live campaign uses the bundled tasks through `effort-run.sh`, and bash reads a running script incrementally, so no file it uses may change before then.
- **Skill tree rule (same as the sibling plans 2026-10-30-* and 2026-10-31-skill-model-or-none-and-generated-challenges):** `.claude/skills/` and `tests/skills/` are untracked on purpose. `make test` collects `tests/skills/`, and those tests exercise scripts that stay untracked, so committing part of either tree breaks `make test` on a clean checkout. No task commits anything under `.claude/skills/` or `tests/skills/`; Task 17 lists the files this plan touched there for the owner, who decides whether the whole tree is committed. Tracked files are committed explicitly (`git add <file>`), with the attribution trailer the executing session requires.
- Parallel work may edit the same files: every task re-reads what it touches at execution time, keeps edits it did not make, and changes only the passages it names.
- After editing a skill `.sh`: `shellcheck -x -s bash .claude/skills/onboard-local-model/scripts/*.sh` and `make validate`. After editing any `.py`: `make validate && make test`.
- Tests never run node through the Volta shim: they use `NODE`/`NODE_DIR` (the real binary from `node -p process.execPath`) as the existing skill tests do, and every subprocess that runs node gets `NODE_DIR` first on `PATH`.
- Python only as `uv run llmenv.py <subcommand>`; this plan adds no subcommand. Model aliases reach `yq` only through `strenv()`.
- Any edit under `challenges/` changes the generator id, so a `seeds.json` drawn before Task 2 is refused afterwards (`effort-run.sh` exits 2); draw fresh seeds.
- Every process, container or temp directory a test or run starts is removed by the same run (trap / `finally`); nothing in this plan starts a container. Verify with `ps -p <pid>` and `podman ps -a` before reporting.
- All output in English. Scratch under `./tmp/` (this plan's scratch: `tmp/audit-fix-wave/`). Never delete or rename plans; never edit the sibling plans.
- Commands run from the repo root with `SK=.claude/skills/onboard-local-model` set; `$SK/...` in Files blocks means that path.

## Facts (read or measured 2026-10-31)

`SK=.claude/skills/onboard-local-model`. Line numbers are from the working tree on that date.

1. `effort-run.sh:226` scores with `bash "$here/../tasks/verify.sh"`, which runs `node --test --test-reporter=tap` with no time limit (`verify.sh:3`); the bounded runner limits only the agent (`effort-run.sh:208-211`). `score.sh:49` counts a run as solved when `pass == total and total > 0`.
2. `gate.mjs` `runOne` (43-70) spawns `process.execPath --test --test-reporter=tap <absolute file>` detached, kills the process group with SIGKILL after `ONBOARD_GEN_TEST_TIMEOUT_MS` (default 20000, line 15), and counts only the TAP `# pass` and `# fail` lines (64). `runTests` (73-78) runs every `test/**/*.test.js` serially; `gen.mjs` already exports `verify` and parses modes in `parse` (407-422).
3. Measured on node v24.21.0 in a scratch directory (removed): a test awaiting a 1e9 ms timer under `--test-timeout=1500` alone kept the file alive until an outer kill at 20 s; with `--test-force-exit` added the file ended after 1.5 s and printed `# pass 1`, `# fail 0`, `# cancelled 1`; a synchronous `for (;;) {}` test ignored both flags until killed and printed no summary. So a `# pass`/`# fail` count drops a cancelled test, and only the process-group kill stops a busy loop. `--test-force-exit` exists since node v20.14.0 (Node docs; not run on 20).
4. `pylib/interference.py` `_latency` (94-102) reads `r[1]` and `float(r[1])` for every row; `evaluate` (148-165) has no check on `during.n`; `_latency_pair` returns `None` when either Laya file is missing, which is reported as the note "System One disabled". `uv run python -B tmp/audits/probe_interference.py` printed `passed: True failures: [] during n: 0` and `truncated line raises: IndexError list index out of range`.
5. A disabled System One has no `system_one` key in snapshots: `live_resources` skips services whose `memory_max_mib <= 0` (`pylib/resources.py:520`), and `compute_resource_limits` zeroes System One's policy when it is disabled (310). `model-interference.sh` writes `laya-*.txt` only with `>>` inside `probe_laya`, and only when System One is enabled (105-108, 160, 166). `tests/test_interference.py::test_evaluate_without_system_one_gates_on_events_only` uses snapshots that still list `system_one` as running.
6. Bundled-task consumers: `effort-run.sh` (flags 43, 46; branches 55-64, 86-96, 184-200; header 5-19), `score.sh:16` (comment), `require-tmp-out.sh:3` (comment), `SKILL.md` 52, 110, 122, `references/scoring.md` 5, 11, 14-20, `references/challenges.md` 5, 39, 59, 148, `references/tuning.md` 5, the scripts test module (`TASKS` 21, `SOLUTIONS`/`LAYER_TASKS`/`FIXES` 180-240, bundled effort-run tests 525-571, 641-642, 1142, 1149-1153, phrase lists 884 and 906), `tests/skills/onboard-local-model/solutions/`, and the tracked `README.md:325` with `tests/test_docs.py:248-255`.
7. `tools/validate.sh:5` shellchecks `./tools/*.sh ./setup/*.sh ./setup/lib/*.sh ./scripts/*.sh` only. The same command plus `./.claude/skills/*/scripts/*.sh` exits 0 today. On a checkout without the skill tree that glob stays literal and shellcheck fails.
8. `preflight.sh:38` computes the RAM budget as `MemTotal * (memory_ceiling_pct // 60) / 100`; `pylib/config.py:99` defaults that key to 46, and `limits_for()` also applies floors (`llmenv.py:280-301`). `llmenv budget` prints the llm-server memory cap as `ram_available_mib` (`llmenv.py:375-393`; 0 while `llm_server.enabled` is false, as SKILL.md Gotchas says), and preflight already reads that JSON (23-24) but drops the key. `llmenv resources` prints the same cap as `.llm_server.memory_mib` and needs no GGUF file. `hf-scout.sh:37` takes `.budget.ram_offload_budget_mib`; a null there makes `$b + $r - 2048` ignore RAM silently.
9. `effort-run.sh:84` splits `--efforts` unchecked; each run directory `$out/${task}-${effort}` is `rm -rf`ed (193-194). Valid tiers: `score.sh:50`.
10. `start.sh:112-118` runs `yq -i` on the live config on every start that renders. Its tests (`tests/test_shell.py`, `run_lifecycle_script` at 4999) use real `migrate-config` through `REAL_UV`, a `date` stub that honours `FIXED_DATE_STAMP` for every call, and render the compose file and `presets.ini` into the config directory.
11. Three copies of backup + private copy + rename: `scripts/llama-config.sh:25-50` (backup `<config>.<stamp>.bak`, `migrate-config` before the edit), `tune-apply.sh:33-47` (`<config>.pre-tune-<stamp>.bak`, no validation), `quant-headroom.sh:230-242` (`<config>.pre-quant-<stamp>.bak`, `migrate-config` after the edit). Tests pin the llama backup name and refusal (`tests/test_shell.py:13311-13396`), the quant validation failure (scripts module 1489-1497: exit 1, `sampling` in stderr, file unchanged, no temp left) and the tune-apply backup as the first stdout line (1203-1222; its config is not a valid `models.yml` and its `PATH` has no `uv`). `tools/lib.sh` has `trap_signals_to_exit` (391) and `die` (226).
12. `hf-scout.sh:66` lists `.../tree/main` without `?recursive=true`; `quant-headroom.sh:70` uses `?recursive=true`.
13. `setup/refresh-llm-env-pi-profile.sh` (24 lines) has no test.
14. `gpu-sample.sh:35-39` sets `trap 'exit 0' TERM INT` and then sleeps in the foreground, so bash runs the trap only after the sleep ends (up to one interval).
15. `pylib/capacity.py:222` stores `card["usable"]`, while the `_view` docstring (247) says usable is never stored. The schema requires `usable` and `benchmark`, `tests/test_capacity.py:229` pins the stored keys, and the ai-kit adapter reads `usable.context_tokens` and `benchmark.tg_tps` straight from the card file (plan 2026-10-30-ai-kit-lite-executor-routing, D2).
16. Duplicated test helpers: `_real_node`, `NODE`, `NODE_DIR`, `_run`, `_scratch`, `_out`, `_scout_out_cleanup` in `test_challenges.py:28-80` and the scripts module 29-67, plus `_node_env` (challenges 46-49) and a hand-rolled scratch path in `test_latency_profile.py:83-85`. The repo's pattern for shared non-fixture helpers is a sibling module imported by bare name (`tests/omniroute_fakes.py`).
17. `SKILL.md` has 215 lines; its description has 1013 characters. Thirteen tests only pin phrases of the skill docs (listed in Task 15).
18. On 2026-10-31 the campaign was running (`latency-profile.sh` processes) and `tmp/final/report-q4-vs-q5.md` did not exist.

## Decisions

1. **Scoring lives in the generator.** `gen.mjs --score DIR` (through `gen-challenge.sh --score DIR`) runs `runTests` on `DIR` and prints `{"pass":P,"total":T,"timed_out":[files]}`. `tasks/verify.sh` is deleted with its TAP parser. Only `test/**/*.test.js` of the verify copy run, so a test the agent plants under `src/` no longer counts (the old `node --test` discovery ran it).
2. **Time limits.** Per test file: `ONBOARD_GEN_TEST_TIMEOUT_MS` (default 20000) with the existing process-group SIGKILL. Per test: `--test-timeout=<max(1, floor(file timeout / 2))>` plus `--test-force-exit` (Fact 3). Counting: `fail` = `# fail` + `# cancelled`; in `score`, a timed-out file adds one failure; `total = pass + fail`. No outer `timeout` wrapper: the per-file bound makes the total finite, and a SIGTERM to `gen.mjs` already kills live groups (`gate.mjs` `hookExit`). The generation gate uses the same `runOne`, so the generator id changes.
3. **Interference.** A probe row is a latency sample only with at least 2 fields and a numeric second field; `non200` counts every non-empty row whose first field is not `200` (a short `503` row still fails). New failures: `system_one: no latency sample under load` when `during.n == 0`, and `system_one was running but its latency was not measured` when `live-before.json` shows `system_one` running but no `laya-*.txt` pair exists. Unchanged: Laya p95 <= 500 ms and p50 <= 150 ms absolute, non-200 fails, `memory.events` growth, counter reset and stopped services fail, a relative p95 rise is only `regression_warning`.
4. **Bundled deletion.** `$SK/tasks/`, `tests/skills/onboard-local-model/solutions/`, `--challenges` and `--tasks` go (an unknown flag exits 2). `score.sh`'s layer-from-task-name fallback stays, for old result files. The regression set is seeds 1-3 of every layer through the validity gate.
5. **Shellcheck glob** through a `nullglob` array, so `make validate` still passes on a checkout without the skill tree.
6. **RAM budget.** `budget.ram_offload_budget_mib` = `ram_available_mib` from the `llmenv budget` JSON preflight already reads; when the budget did not run, `.llm_server.memory_mib` from `llmenv resources`; both clamped to MemAvailable (a live measurement); `null` when neither answers. A new field `budget.ram_budget_source` says which (`"llmenv budget"`, `"llmenv resources"` or null). `hf-scout.sh` exits 2 when its VRAM or RAM budget is not a non-negative integer.
7. **Efforts:** each element in `off|minimal|low|medium|high|xhigh`, no repeats, checked right after the required-flag check (before `require_tmp_out` and any `rm -rf`).
8. **Shared config edit.** `edit_config_atomically TAG FUNC [ARGS...]` (body in Task 8). Backup names stay: llama-config passes an empty tag (`<config>.<stamp>.bak`), tune-apply `pre-tune`, quant-headroom `pre-quant`; start.sh uses `pre-start`. The helper validates after the edit for every caller. `start.sh` calls it only when the persisted `resources.llm_server.cpus` or `memory_mib` differs, as a string, from the computed value.
9. **Test helpers** go to `tests/skills/onboard-local-model/skill_helpers.py` (plain functions and constants, imported by bare name like `tests/omniroute_fakes.py`) and `tests/skills/onboard-local-model/conftest.py` (the autouse cleanup fixture). The owner wrote "tests/conftest.py"; this deviates on purpose: pytest conftest modules are not importable helpers (`from conftest import` resolves whichever conftest was imported last), and the tracked `tests/conftest.py` would otherwise carry untracked-tree paths and wrap every repo test in the skill's cleanup fixture. The owner can override.
10. **Doc tests:** keep exactly three phrase tests (the isolation sentence, the bypass-gates paragraph, `test_skill_prose_has_no_long_shell_logic`), delete the other thirteen (Task 15), add `test_skill_docs_name_only_existing_scripts`, keep the 1024-character limit as `test_skill_description_fits_the_spec_limit`, and keep the code scan of the deleted N2-H1 test as `test_only_bench_fit_mounts_the_models_directory`.
11. **SKILL.md:** steps 4-9 move to `references/onboard-flow.md`; SKILL.md keeps one to three lines per step with its gate; the isolation sentence appears once, in SKILL.md; description text fixed in Task 16 (421 characters).
12. **Capacity card contract stays:** the file keeps `usable` and `benchmark` (ai-kit reads both, Fact 15); only the docstring changes.
13. **Skipped by the owner:** micro-batching jq/yq calls (milliseconds each); shell-to-Python rewrites (`latency-profile.sh`, `quant-headroom.sh`, `score.sh`, `tune-*`, the leak guard), which are large and touch scripts a live campaign uses; committing the untracked skill tree (the owner decides).

## Review Focus

Failure modes the owner's decisions imply that the main tests do not name; each has its test in the owning task.

1. A solution that hangs or crashes while scored (busy loop at import, an async test holding a timer): scoring must end within files x timeout, leave no process behind and never count a missing or cancelled test as passed [Task 2 `test_score_counts_a_cancelled_test_as_failed`, `test_score_kills_a_busy_loop_and_counts_the_file_as_failed`, `test_effort_run_scores_a_hanging_solution_as_failed`].
2. A partial Laya probe: empty file, a truncated line, a non-numeric time, a missing file while System One runs [Task 3, all three new tests].
3. A live-config rewrite that would change nothing, that produces an invalid config, or whose backup name exists: no write, no partial file, the backup refused [Task 8 `test_start_leaves_the_config_alone_when_resource_limits_are_unchanged`, `test_edit_config_atomically_refuses_an_existing_backup_and_an_invalid_result`; Task 9 `test_tune_apply_leaves_the_live_config_when_the_result_fails_validation`].
4. A clean checkout without the untracked skill tree: `make validate` must not fail on the empty glob [Task 5 `test_validate_shellchecks_skill_scripts_only_when_present`].
5. A mistyped or path-like `--efforts` value: exit 2 before any directory is created or removed [Task 7 `test_effort_run_rejects_unknown_or_repeated_efforts`].

---

### Task 1 (P0): Precondition: wait for the Q4-vs-Q5 campaign to end

**Files:**
- Create (scratch, git-ignored): `tmp/audit-fix-wave/precondition.sh`, `tmp/audit-fix-wave/status-before.txt`, `tmp/audit-fix-wave/gid-before.txt`

- [ ] **Step 1: Write the check script** (shell logic belongs in a file):

```bash
#!/usr/bin/env bash
# precondition.sh — exit 0 only when the Q4-vs-Q5 campaign is over: its report exists and no benchmark script runs.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
[ -f tmp/final/report-q4-vs-q5.md ] || { echo "waiting: tmp/final/report-q4-vs-q5.md does not exist"; exit 1; }
busy="$(ps -eo pid=,args= | awk '/(effort-run|latency-profile|bench-fit|model-interference)\.sh/')"
[ -z "$busy" ] || { printf 'waiting: benchmark processes still run:\n%s\n' "$busy"; exit 1; }
echo "precondition met"
```

(The awk program does not match its own arguments: in its text a `)\` sits between each name and `.sh`.)
- [ ] **Step 2: Wait.** Run `bash tmp/audit-fix-wave/precondition.sh` at least every 5 minutes (a Monitor until-loop is fine) and report the waiting reason in every status message. Expected end state: `precondition met`. Never stop, kill or edit anything of the campaign.
- [ ] **Step 3: Baseline.** `git status --short > tmp/audit-fix-wave/status-before.txt`; `bash .claude/skills/onboard-local-model/scripts/gen-challenge.sh --generator-id > tmp/audit-fix-wave/gid-before.txt`; `make validate && make test`. Expected: `All checks passed.` and all tests pass. If anything fails, stop and report it; do not fix unrelated failures inside this plan.
- [ ] **Step 4: No commit.**

---

### Task 2 (P0): Score the agent's solution under a time limit (ponytail 1)

**Files:**
- Modify: `$SK/challenges/lib/gate.mjs` (`runOne`, comment at 72, new export `score`)
- Modify: `$SK/challenges/gen.mjs` (import `score`; `parse` and `main` gain `--score DIR`)
- Modify: `$SK/scripts/gen-challenge.sh` (mode `--score DIR`; header lines 2-10; unknown-flag usage text)
- Modify: `$SK/scripts/effort-run.sh` (line 226; header line 20)
- Modify: `$SK/references/challenges.md` ("Validity gate": the timeout paragraph gains the per-test limit, `--test-force-exit`, the cancelled count and that scoring uses the same limits)
- Delete: `$SK/tasks/verify.sh`
- Test: `tests/skills/onboard-local-model/test_challenges.py`, `tests/skills/onboard-local-model/test_onboard_local_model_scripts.py`

**Interfaces:**
- Produces (`gate.mjs`): `runOne` spawns `process.execPath` with the arguments `--test`, `--test-reporter=tap`, `--test-timeout=<Math.max(1, Math.floor(timeoutMs / 2))>`, `--test-force-exit`, `<absolute test file>` (same `cwd`, `detached`, group kill as today) and returns `{ file, pass, fail /* # fail + # cancelled */, timedOut }`.
- Produces (`gate.mjs`): `export async function score(dir, { timeoutMs = timeoutDefault() } = {}) -> { pass, total, timed_out }` (keys in that order; `timed_out` = sorted files; a timed-out file adds one failure; `total = pass + fail`).
- Produces (CLI): `gen.mjs --score DIR` prints `JSON.stringify(result) + "\n"`, exit 0 when it scored, 1 on an error (for example no `DIR/test`). `gen-challenge.sh --score DIR`: exit 2 without a directory; no `require_tmp_out` (it writes nothing); requires node like the other modes; `exec node ... --score DIR`.
- Produces (`effort-run.sh`): `verdict="$(bash "$here/gen-challenge.sh" --score "$run/verify")"`; the row still reads `$verdict.pass` and `$verdict.total`.
- Test helpers: `test_challenges.py` `_verify(directory, **env) -> dict` now runs `bash GEN --score <directory>` with `_node_env(**env)` and `timeout=180`; `VERIFY_SH` goes. The scripts module's `_effort_run` gains `timeout: float | None = None`, passed to `_run`.

- [ ] **Step 1: Write the failing tests**
  - `test_gen_challenge_rejects_bad_usage`: add the case `["--score"]` (exit 2).
  - `test_score_counts_a_cancelled_test_as_failed` (challenges module): `_gen(tmp_path, 1, 3, "--with-reference")`, `d = _overlay_reference(out, tmp_path)`, `base = _verify(d)` with `base["pass"] == base["total"] > 0`; write `d/test/zz-hang.test.js`:

    ```js
    import test from "node:test";
    test("ok", () => {});
    test("hang", async () => { await new Promise((r) => setTimeout(r, 1e9)); });
    ```

    then `r = _verify(d, ONBOARD_GEN_TEST_TIMEOUT_MS="4000")`: `r["timed_out"] == []`, `r["total"] == base["total"] + 2`, `r["pass"] == base["pass"] + 1`, and the call returns within 30 s (`time.monotonic()`).
  - `test_score_kills_a_busy_loop_and_counts_the_file_as_failed`: same overlay `d`; append `for (;;) {}` to `d / manifest["operators"][0]["file"]` (the site file, which a failing test imports directly); `r = _verify(d, ONBOARD_GEN_TEST_TIMEOUT_MS="2000")`: `len(r["timed_out"]) >= 1`, `r["pass"] < r["total"]`; afterwards no line of `ps -eo args` contains `str(d)`.
  - Scripts module: `AGENT_MISBEHAVIOUR` gains `[ -z "${HANG:-}" ] || for f in src/*.js; do echo 'for (;;) {}' >>"$f"; done`. New `@NEEDS_NODE test_effort_run_scores_a_hanging_solution_as_failed`: `_effort_run(tmp_path, "--layers", "1", "--seeds", str(_seeds_file(tmp_path, {"1": [7]})), env={"HANG": "1", "ONBOARD_GEN_TEST_TIMEOUT_MS": "3000"}, timeout=300)`: returncode 0; one row with `0 < row["total"]` and `row["pass"] < row["total"]`; no line of `ps -eo args` contains `str(_out(tmp_path, "out"))`.
  - Scripts module, `test_task_fails_as_shipped_and_passes_with_the_reference_solution` (deleted by Task 4): its two `verify.sh` calls become `_run("bash", SCRIPTS / "gen-challenge.sh", "--score", work)`.
- [ ] **Step 2: Run** `uv run pytest tests/skills/onboard-local-model -q -k "bad_usage or score_counts or busy_loop or hanging_solution or task_fails"`. Expected: the new tests and `task_fails` FAIL (`--score` is an unknown flag, exit 2).
- [ ] **Step 3: Implement** per the Interfaces block. `score` maps `runTests(dir, { timeoutMs })`; `gen-challenge.sh` validates `--score` like `--verify` (an empty or missing directory exits 2). Delete `$SK/tasks/verify.sh`. In `effort-run.sh`'s header, say that the verdict comes from `gen-challenge.sh --score` (per test file `ONBOARD_GEN_TEST_TIMEOUT_MS`, default 20 s, process group killed; a hung file counts as failed).
- [ ] **Step 4: Verify:** the Step 2 command passes; `uv run pytest tests/skills/onboard-local-model -q`; `shellcheck -x -s bash .claude/skills/onboard-local-model/scripts/*.sh`; `make validate && make test`; `bash $SK/scripts/gen-challenge.sh --generator-id` differs from `tmp/audit-fix-wave/gid-before.txt` (expected, Global Constraints).
- [ ] **Step 5: No commit** (skill tree rule). Record the touched files for Task 17.

---

### Task 3 (P0): The interference gate fails when Laya was never measured under load (ponytail 2)

**Files:**
- Modify: `pylib/interference.py` (`_latency`, `evaluate`; module docstring rule list)
- Modify: `.agents/architecture.md:47` (the gate's failure list gains "no Laya latency sample under load, or System One running but not probed")
- Modify (untracked): `$SK/references/tuning.md` "## Interference gate" (same two conditions)
- Test: `tests/test_interference.py`

**Interfaces:**
- Produces: `_latency(path) -> {"n", "p50_ms", "p95_ms", "non200"}` with the Decision 3 row rule; `evaluate` loads `live-before.json`/`live-after.json` before the Laya block and adds the two Decision 3 failures. Report shape and thresholds unchanged.

- [ ] **Step 1: Write the failing tests** (`tests/test_interference.py`)
  - `test_evaluate_fails_without_a_laya_sample_under_load` reproduces `tmp/audits/probe_interference.py` with its exact inputs, in `tmp_path`: `laya-before.txt` = `"200 0.050\n200 0.050\n"`, `laya-during.txt` = `""`, `live-before.json` and `live-after.json` = `{"services": {"system-one": {"state": "running", "uptime_s": 100, "events": {}}}, "host": {}}`, `load.jsonl` = one row `{"status": 200, "prompt_tokens": 10, "completion_tokens": 5}`. Assert `report["passed"] is False`, `"system_one: no latency sample under load" in report["failures"]`, `report["system_one"]["during"]["n"] == 0`. Then write `laya-during.txt` = `"200\n"` (the probe's truncated line): `evaluate` raises nothing and the same failure is present.
  - `test_evaluate_skips_short_or_malformed_probe_lines`: `make_run(tmp_path)`, then `laya-during.txt` = `"200 0.040\n200\n200 abc\n503\n"`: `during == {"n": 1, "p50_ms": 40.0, "p95_ms": 40.0, "non200": 1}`, `passed is False`, and a failure contains `non-200`.
  - `test_evaluate_fails_when_system_one_ran_but_was_not_probed`: `evaluate(make_run(tmp_path, laya=None))` (the default `snap()` lists `system_one` running): `passed is False` and `"system_one was running but its latency was not measured" in report["failures"]`.
  - Edit `test_evaluate_without_system_one_gates_on_events_only`: pass `before` and `after` snapshots built by `snap()` with the `system_one` key deleted (a disabled System One is absent, Fact 5); its assertions stay.
- [ ] **Step 2: Run** `uv run pytest tests/test_interference.py -q`. Expected: the three new tests FAIL (the first and third report `passed True`, the second raises `IndexError`); the edited test passes.
- [ ] **Step 3: Implement** `_latency` (a sample needs `len(row) >= 2` and a `float()`-parsable second field; `non200` over all non-empty rows) and the two failures in `evaluate`; update the module docstring and the two docs.
- [ ] **Step 4: Verify:** `uv run pytest tests/test_interference.py tests/test_capacity.py tests/test_docs.py -q`; `uv run python -B tmp/audits/probe_interference.py` (when that scratch file still exists) prints `passed: False` and no IndexError line; `make validate && make test`.
- [ ] **Step 5: Commit the tracked files**

```bash
git add pylib/interference.py tests/test_interference.py .agents/architecture.md
git commit -m "fix(interference): fail the gate when Laya was never measured under load"
```

---

### Task 4 (P1): Delete the bundled tasks; generated challenges only

**Files:**
- Delete: `$SK/tasks/` (whole tree; `verify.sh` already went in Task 2), `tests/skills/onboard-local-model/solutions/`
- Modify: `$SK/scripts/effort-run.sh` (drop `--challenges`, `--tasks`, the `challenges` variable and every bundled branch, Fact 6; header), `$SK/scripts/score.sh:16`, `$SK/scripts/require-tmp-out.sh:3`, `$SK/scripts/gen-challenge.sh:2` ("from the skill's challenge library")
- Modify: `$SK/SKILL.md` (52, 110, 122), `$SK/references/scoring.md` (5, 11, the table becomes `| Layer | Generated challenge |`), `$SK/references/challenges.md` (5; 39 "end with the shared sentence"; 59 deleted; 148 drops "or `tasks/`"), `$SK/references/tuning.md:5`
- Modify (tracked): `README.md:325`, `tests/test_docs.py:248-255`
- Test: both skill test modules

**Interfaces:**
- Produces: `ISOLATION_SENTENCE` = "Benchmark challenges run only on generated challenges (`gen-challenge.sh`), copied into isolated directories under `./tmp/`, and never in or against the owner's project." in `SKILL.md`, `tuning.md`, `scoring.md`, `challenges.md` (Task 16 later keeps it in SKILL.md only).
- Produces (scripts module): `_effort_run(tmp_path, *extra, outcome=..., env=None, timeout=None, out_name="out")` and `_rows(tmp_path, out_name="out")`; `SEEDS_ALL = {"1": [1], "2": [1], "3": [1], "4": [1], "5": [1]}` (seed 1 of every layer, proven by the new regression test).

- [ ] **Step 1: Write the failing tests**
  - `test_challenges.py`: `@pytest.mark.parametrize("layer", [1, 2, 3, 4, 5]) test_seeds_one_to_three_pass_the_validity_gate`: for seeds 1, 2, 3: `_gen(tmp_path, layer, seed)` exits 0 and its manifest's `validity` has `fails_as_shipped`, `passes_with_reference`, `leak_free`, `self_contained` all `True`; remove each out after the check.
  - Scripts module, re-pointed from bundled to generated (assertions otherwise kept):
    - `test_effort_run_defaults_to_every_layer_and_isolates_each_run_in_a_git_repo`: `--seeds <SEEDS_ALL file>` without `--layers`: row layers `[1, 2, 3, 4, 5]`, every row `total > 0` and `outside_writes == []`; in run `l1-s1-low`, `git rev-list --count HEAD` is `1` and `re.findall(r"^diff --git a/(\S+)", diff, re.M) == ["src/agent_added.js"]`; `out/challenges/l1-s1/challenge/src/agent_added.js` does not exist.
    - `test_effort_run_layers_flag_filters_the_task_list`: `--layers 2,3 --seeds {"2": [1], "3": [1]}` gives layers `[2, 3]`.
    - `test_effort_run_counts_tool_calls_and_errors_from_the_transcript`, `test_effort_run_records_the_bounded_outcome`: `--layers 1 --seeds {"1": [7]}`.
    - `test_effort_run_defaults_to_the_runner_cap_and_refuses_a_larger_timeout`: the same seeds; the first and third calls use `out_name="a"` and `out_name="c"` (a used out is refused, `effort-run.sh:135`).
    - `test_effort_run_rejects_mixed_seed_options`: extras `("--per-layer", "2", "--seeds", <file>)`, `("--tasks", "l1-x")`, `("--challenges", "bundled")`: each exit 2; the last two print `unknown flag`.
    - `test_benchmark_scripts_accept_an_out_under_the_repo_tmp`: effort-run's extra becomes `("--timeout", str(RUNNER_CAP + 1))` (checked after the `--out` guard, `effort-run.sh:77`); expectations unchanged; its docstring says "effort-run its --timeout check (2)".
    - `ISOLATION_SENTENCE` constant updated; `test_skill_states_that_challenges_run_only_in_isolated_dirs_under_tmp` also asserts `"--challenges bundled" not in _md(path)`.
    - `test_skill_documents_model_argument_modes` drops `"--challenges bundled"` from its list; `test_skill_documents_layered_isolated_delegated_benchmarks` drops `"tasks/l1-"` (both are deleted later by Task 15; these edits keep this task green on its own).
    - Delete `TASKS`, `SOLUTIONS`, `LAYER_TASKS`, `_solution_files`, `FIXES`, `test_every_layer_task_has_a_reference_solution_and_the_shared_ending`, `test_task_fails_as_shipped_and_passes_with_the_reference_solution`, `test_effort_run_tasks_cannot_name_a_directory_outside_the_bundled_tasks`; the module docstring drops "and benchmark tasks".
  - `tests/test_docs.py::test_readme_says_ladder_challenges_are_generated_and_isolated`: keep `generated per run` and the `--out` clause, replace the `--challenges bundled` fragment with `assert "--challenges bundled" not in readme`.
- [ ] **Step 2: Run** `uv run pytest tests/skills tests/test_docs.py -q -k "seeds_one_to_three or effort_run or isolated or model_argument or layered or ladder_challenges or accept_an_out"`. Expected: the isolation-sentence, README and mixed-option tests FAIL (the docs still name the bundled tasks and `--tasks`/`--challenges` are still accepted); the regression test and the re-pointed effort-run tests already pass (generated mode exists).
- [ ] **Step 3: Implement:** delete the two trees; strip the bundled mode from `effort-run.sh` (one `pristine`/`meta` path, the pristine re-check unconditional, no `git init` branch); update the comments and docs of the Files block; in `README.md:325` write: "The ladder challenges are generated per run from the skill's reference projects (`gen-challenge.sh`, seeded, each proven to fail as shipped and to pass with its reference fix before a model sees it), copied into isolated directories under `<repo>/tmp/`; the scripts refuse an `--out` outside it, and nothing runs in or against the owner's project."
- [ ] **Step 4: Verify:** `grep -rn "bundled\|tasks/" .claude/skills/onboard-local-model README.md` shows no match that names the deleted mode, flag or directory (review each remaining match); `ls .claude/skills/onboard-local-model/tasks tests/skills/onboard-local-model/solutions` fails with "No such file"; `uv run pytest tests/skills tests/test_docs.py -q`; shellcheck; `make validate && make test`.
- [ ] **Step 5: Commit the tracked files only**

```bash
git add README.md tests/test_docs.py
git commit -m "docs(skill): the ladder runs only on generated challenges"
```

---

### Task 5 (P1): `make validate` shellchecks the skill scripts (ponytail 4)

**Files:**
- Modify: `tools/validate.sh:5`
- Test: `tests/test_shell.py`

**Interfaces:**
- Produces: `validate.sh` builds `skill_scripts=(./.claude/skills/*/scripts/*.sh)` under `shopt -s nullglob` (comment: the untracked skill tree is absent on a clean checkout) and appends `"${skill_scripts[@]}"` to the existing shellcheck line.

- [ ] **Step 1: Write the failing test** `test_validate_shellchecks_skill_scripts_only_when_present(tmp_path)`: for `with_skill` in `(True, False)`, build `root = tmp_path / str(with_skill)` with a copy of `tools/validate.sh` at `root/tools/validate.sh` and one empty `a.sh` in each of `tools/`, `setup/`, `setup/lib/`, `scripts/`, plus `root/.claude/skills/s/scripts/y.sh` when `with_skill`; stub `shellcheck` (`printf '%s\n' "$@" >"$ARGS"`) and `uv` (`exit 0`) in `root/bin`; run `bash tools/validate.sh` with `cwd=root`, `PATH=<root/bin>:/usr/bin:/bin`, `ARGS=<file>`. Assert returncode 0, `("./.claude/skills/s/scripts/y.sh" in args) == with_skill`, and no argument contains `*`.
- [ ] **Step 2: Run** `uv run pytest tests/test_shell.py -q -k validate_shellchecks`. Expected: FAIL (no skill path with the tree present).
- [ ] **Step 3: Implement** per the Interfaces block.
- [ ] **Step 4: Verify:** the Step 2 command passes; `make validate` (now covers the 18 skill scripts); `make test`.
- [ ] **Step 5: Commit**

```bash
git add tools/validate.sh tests/test_shell.py
git commit -m "build(validate): shellcheck the project skill scripts when present"
```

---

### Task 6 (P1): The RAM budget comes from llmenv, not a hardcoded 60

**Files:**
- Modify: `$SK/scripts/preflight.sh` (keep `ram_available_mib` in `llmenv_budget`; replace line 38's formula per Decision 6; new output field `budget.ram_budget_source`)
- Modify: `$SK/scripts/hf-scout.sh` (after line 38: exit 2 unless `budget` and `ram` match `^[0-9]+$`, naming the flag to pass; header line 5)
- Modify: `$SK/SKILL.md` step 1 and `$SK/references/selection-criteria.md` (the `ram_offload_budget_mib` sentence: the llm-server memory cap from `llmenv budget`, set by `resources.llm_server.memory_ceiling_pct`, clamped to MemAvailable, 0 while llm-server is disabled)
- Test: scripts module

**Interfaces:**
- Produces (`preflight.sh` JSON): `budget.ram_offload_budget_mib` (integer or null), `budget.ram_budget_source` (`"llmenv budget"`, `"llmenv resources"` or null), `budget.llmenv_budget.ram_available_mib`. The resources fallback runs `llmenv --config "$cfg" resources` only when the budget JSON has no integer `ram_available_mib`.

- [ ] **Step 1: Write the failing tests**
  - `@pytest.mark.parametrize test_preflight_takes_the_ram_budget_from_llmenv(case)`: config `{"gpu": {"pci_address": "0000:00:00.0", "backend": "vulkan", "image": "i"}, "resources": {"llm_server": {"memory_ceiling_pct": 60}}, "llm_server": {"enabled": True}, "system_one": {"enabled": False}, "models": [{"alias": "m", "enabled": True}]}`; stub `podman` (prints nothing) and `uv` answering `detect` (`{"gpus": [{"pci_address": "0000:00:00.0", "vram_total_mib": 16304, "vram_used_mib": 1000, "render_node": "renderD128"}]}`), `processes-on-render-node` (`{"processes": []}`), `budget` (`$BUDGET_JSON`, else `{"error": "x"}` and exit 1) and `resources` (`$RESOURCES_JSON`, else `{"error": "y"}` and exit 1); `PATH` = stub dir, the real `yq`/`jq` directories, `/usr/bin:/bin`; `HOME`, `LLM_ENV_CONFIG`, `LLM_ENV_MODELS_DIR` under `tmp_path`. Cases: budget `{"feasible": true, "available_mib": 9000, "required_mib": 8000, "ram_available_mib": 1024, "ram_feasible": true}` gives `(1024, "llmenv budget")`; failing budget plus resources `{"llm_server": {"memory_mib": 900}}` gives `(900, "llmenv resources")`; both failing gives `(None, None)`. (1024 and 900 assume the test host has more MemAvailable, true on any dev host; the old formula would give 60 % of MemTotal.)
  - `test_hf_scout_refuses_a_budget_that_is_not_a_number`: `hf-scout.sh --json --budget-mib 10369 --ram-mib null` with `HF_FIXTURE_DIR` set: exit 2 and `--ram-mib` in stderr.
- [ ] **Step 2: Run** `uv run pytest tests/skills/onboard-local-model -q -k "preflight_takes or refuses_a_budget"`. Expected: FAIL.
- [ ] **Step 3: Implement** per the Interfaces block and Decision 6; update the two docs.
- [ ] **Step 4: Verify:** the Step 2 command passes; the existing hf-scout tests pass; shellcheck; `make validate && make test`.
- [ ] **Step 5: No commit.**

---

### Task 7 (P1): Validate `--efforts` before it names a directory (ponytail 9)

**Files:**
- Modify: `$SK/scripts/effort-run.sh` (the check right after `usage()` is defined; drop the later split at line 84; header line 4 lists the tiers)
- Test: scripts module

**Interfaces:**
- Produces: `effort_list` is split once and checked per Decision 7; a failure calls `usage` (exit 2) with a message containing `--efforts`.

- [ ] **Step 1: Write the failing test** `@pytest.mark.parametrize test_effort_run_rejects_unknown_or_repeated_efforts(value)` over `"hgih"`, `"low/../x"`, `"low,low"`, `",low"`, `""`: `_effort_run(tmp_path, "--efforts", value)` exits 2, stderr contains `--efforts`, and `_out(tmp_path, "out")` does not exist.
- [ ] **Step 2: Run** `uv run pytest tests/skills/onboard-local-model -q -k rejects_unknown_or_repeated`. Expected: FAIL (the values reach generation or `rm -rf`).
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Verify:** the Step 2 command and `-k effort_run` pass; shellcheck; `make validate && make test`.
- [ ] **Step 5: No commit.**

---

### Task 8 (P1): One atomic config edit in `tools/lib.sh`; `start.sh` writes only on change (ponytail 6)

**Files:**
- Modify: `tools/lib.sh` (new function after `migrate_config_file`)
- Modify: `scripts/start.sh:112-118`
- Modify: `.agents/architecture.md` (`tools/lib.sh` row: "`edit_config_atomically`, the one way scripts rewrite the live `models.yml`"; `scripts/start.sh` row: "persists `resources.llm_server` only when the computed limits change, through `edit_config_atomically`")
- Test: `tests/test_shell.py`

**Interfaces:**
- Produces `edit_config_atomically TAG FUNC [ARGS...]`. The body decides naming, order and failure handling, so it is fixed here; it does not rely on `set -e` (callers may run it inside a condition):

```bash
# edit_config_atomically TAG FUNC [ARGS...]: the one way scripts rewrite the live models.yml.
# Backs CONFIG_PATH up to <config>.<TAG>-<YYYYmmddTHHMMSS>.bak (<config>.<stamp>.bak for an empty TAG; mode 0600; an existing
# name is refused) and prints `backup: <path>`, runs `FUNC <work> [ARGS...]` on a private copy in the config directory,
# validates the copy with `llmenv migrate-config` (its .error goes to stderr), then renames it over CONFIG_PATH with mode 0600.
# Any failure exits nonzero and leaves CONFIG_PATH as it was. A subshell, so its traps never replace the caller's.
edit_config_atomically() (
    local tag="$1" fn="$2" backup work output msg
    shift 2
    backup="${CONFIG_PATH}.${tag:+${tag}-}$(date +%Y%m%dT%H%M%S).bak"
    [[ ! -e "$backup" ]] || die "backup ${backup} already exists; retry in a second"
    install -p -m 600 "$CONFIG_PATH" "$backup" || exit 1
    printf 'backup: %s\n' "$backup"
    work="$(mktemp "$(dirname "$CONFIG_PATH")/.$(basename "$CONFIG_PATH").XXXXXX")" || exit 1
    trap 'rm -f "$work"' EXIT
    trap_signals_to_exit
    cp "$CONFIG_PATH" "$work" || exit 1
    "$fn" "$work" "$@" || exit 1
    if ! output="$(llmenv --config "$work" migrate-config)"; then
        msg="$(jq -r '.error // empty' <<<"$output" 2>/dev/null || true)"
        die "${msg:-configuration validation failed}"
    fi
    chmod 600 "$work" && mv -f "$work" "$CONFIG_PATH" || exit 1
)
```

- Produces (`start.sh`): a function `set_llm_server_limits WORK CPUS MEMORY_MIB` (the existing `yq -i` expression, applied to `WORK`); the persisted values are read with two `yq -r` calls (`.resources.llm_server.cpus`, `.resources.llm_server.memory_mib`), and only when either string differs from the computed `cpus`/`memory_mib` does it run `edit_config_atomically pre-start set_llm_server_limits "$cpus" "$memory_mib"`. The `log_info` policy line stays unconditional.

- [ ] **Step 1: Write the failing tests** (`tests/test_shell.py`, `run_lifecycle_script`)
  - `test_start_writes_changed_resource_limits_through_a_backup_and_rename`: default fixture (no `resources`): returncode 0; `.resources.llm_server.cpus == "4"` and `memory_mib == "8000"`; exactly one `models.yml.pre-start-*.bak` in the config directory, mode 0600, whose `cpus` is not `"4"`; the config is 0600; no `.models.yml.*` file is left; `f"backup: {backup}"` in stdout. (No `FIXED_DATE_STAMP`: the stub would answer every `date` call of `start.sh` with it.)
  - `test_start_leaves_the_config_alone_when_resource_limits_are_unchanged`: `extra_config="resources:\n  llm_server:\n    cpus: 4\n    memory_mib: 8000\n"`: returncode 0, no `models.yml.pre-start-*.bak`, no `.models.yml.*` file.
  - `test_edit_config_atomically_refuses_an_existing_backup_and_an_invalid_result`: write `tmp_path / "edit.sh"` = `source "<ROOT>/tools/lib.sh"`, `bad() { yq -i '.version = 2' "$1"; }`, `edit_config_atomically pre-test bad`, and run it through `run_lifecycle_script(tmp_path, str(tmp_path / "edit.sh"))`: returncode 1, `version must be 1` in stderr, the config equals the one `models.yml.pre-test-*.bak` byte for byte, no `.models.yml.*` file. Second run with `env_overrides={"FIXED_DATE_STAMP": "20261031T120000"}` after creating `models.yml.pre-test-20261031T120000.bak` in the config directory: returncode 1, `already exists` in stderr, the set of `.bak` files is the same as before this run, no temp file.
- [ ] **Step 2: Run** `uv run pytest tests/test_shell.py -q -k "resource_limits or edit_config_atomically or start_computes or start_overwrites"`. Expected: the three new tests FAIL; the two existing resource tests still pass.
- [ ] **Step 3: Implement** the helper and the `start.sh` change; update the architecture rows.
- [ ] **Step 4: Verify:** the Step 2 command passes; `uv run pytest tests/test_shell.py tests/test_docs.py -q`; `make validate && make test`.
- [ ] **Step 5: Commit**

```bash
git add tools/lib.sh scripts/start.sh tests/test_shell.py .agents/architecture.md
git commit -m "fix(start): persist llm-server limits only on change, through one atomic config edit"
```

---

### Task 9 (P2): llama-config, tune-apply and quant-headroom use the shared edit (ponytail 5)

**Files:**
- Modify (tracked): `scripts/llama-config.sh:25-50`
- Modify: `$SK/scripts/tune-apply.sh:33-49` (adds `require_cmd uv jq yq`; drops its own `backup:` echo), `$SK/scripts/quant-headroom.sh:230-245` and the global `work=""` (265)
- Test: scripts module (`_apply`, new tune-apply test); `tests/test_shell.py` llama-config tests unchanged

**Interfaces:**
- Consumes: Task 8 `edit_config_atomically TAG FUNC [ARGS...]`.
- Produces: `llama-config.sh` defines `apply_llama WORK` (enable: `models select` on `WORK` with `die_with_error` on failure, then `.llm_server.enabled = true`; disable: `false`) and calls `edit_config_atomically "" apply_llama`; its separate pre-edit `migrate-config` call goes (the helper validates after the edit). `tune-apply.sh` defines `apply_row WORK` (`yq -i "$expr"`) and calls `edit_config_atomically pre-tune apply_row`. `quant-headroom.sh` defines `add_copy WORK` (the existing `.models += [...]` edit) and calls `edit_config_atomically pre-quant add_copy`, then prints `alias: <new>`; its EXIT trap keeps only `$tree`.

- [ ] **Step 1: Write the failing tests** (scripts module)
  - `_apply` builds its config from a deep copy of `QH_CONFIG` with `runtime` = `{"models_max": 1, "parallel_slots": 1, "ubatch_size": 512, "flash_attn": False, "cache_type_k": "q5_1", "cache_type_v": "q5_1"}` (so every tuned key visibly changes) and models `m` (the QH entry) and `o` (a copy with `alias: o`, `enabled: false`, `file: o.gguf`, `ctx_size: 8192`), and puts the real `uv` directory on `PATH` (`APPLY_CONFIG` goes: `QH_CONFIG` is defined further down the module). `test_tune_apply_backs_up_and_writes_only_the_tuned_keys` then asserts: first stdout line is the backup, equal to the original bytes, mode 0600; `runtime` has `cache_type_k == cache_type_v == "q8_0"`, `ubatch_size == 1024`, `flash_attn is True`; model `m` has `ctx_size == 196608`, `n_cpu_moe == 28`, `n_gpu_layers == 99`; model `o` and `server` equal their originals; the last three lines are unchanged.
  - `test_tune_apply_leaves_the_live_config_when_the_result_fails_validation`: an ok verify row with `ctx: 4096` (below `client_max_output_tokens` 8192): returncode 1, `client_max_output_tokens must not exceed ctx_size` in stderr, the config bytes unchanged, one `models.yml.pre-tune-*.bak`, no `.models.yml.*` file.
- [ ] **Step 2: Run** `uv run pytest tests/skills/onboard-local-model -q -k "tune_apply or add_entry"` and `uv run pytest tests/test_shell.py -q -k llama_`. Expected: the validation test FAILS (tune-apply writes the invalid row); the others pass.
- [ ] **Step 3: Implement** per the Interfaces block.
- [ ] **Step 4: Verify:** both Step 2 commands pass (the llama-config tests unchanged, including the backup name `models.yml.20261030T120000.bak` and the refusal); shellcheck; `make validate && make test`.
- [ ] **Step 5: Commit the tracked file**

```bash
git add scripts/llama-config.sh
git commit -m "refactor(config): llama-config uses the shared atomic config edit"
```

---

### Task 10 (P2): hf-scout lists GGUF files in sub-folders (ponytail 8)

**Files:**
- Modify: `$SK/scripts/hf-scout.sh:66` (`.../tree/main?recursive=true`)
- Test: scripts module

- [ ] **Step 1: Write the failing test** `test_hf_scout_reads_gguf_files_in_sub_folders`: no `HF_FIXTURE_DIR`; a stub `curl` on `PATH` (`stub:/usr/bin:/bin`) appends `$*` to `$CALLS`, finds the URL argument and the `-o` target, and serves: for a URL ending `tree/main?recursive=true`, the tree `[{"path": "Q8_0", "type": "directory"}, {"path": "Q8_0/m-Q8_0-00001-of-00002.gguf", "size": 4250 MiB}, {"path": "Q8_0/m-Q8_0-00002-of-00002.gguf", "size": 4250 MiB}, {"path": "m-Q4_K_M.gguf", "size": 5000 MiB}]` into the `-o` file; for one ending `tree/main`, only the directory entry and the Q4 file; otherwise (the search) `[_repo("lab/dense-8b-GGUF", 8, 262144, "qwen35")]` on stdout. `hf-scout.sh --json --budget-mib 10369 --ram-mib 14000`: the row's `quants` contains `"Q8_0:8500"`, `pick.quant == "Q8_0"`, and `$CALLS` contains `tree/main?recursive=true`.
- [ ] **Step 2: Run** `uv run pytest tests/skills/onboard-local-model -q -k sub_folders`. Expected: FAIL (only Q4 seen).
- [ ] **Step 3: Implement** the one-line URL change.
- [ ] **Step 4: Verify:** `-k hf_scout` passes; shellcheck; `make validate && make test`.
- [ ] **Step 5: No commit.**

---

### Task 11 (P2): Test `setup/refresh-llm-env-pi-profile.sh` (ponytail 7)

**Files:**
- Test: `tests/test_shell.py` (the script is unchanged)

- [ ] **Step 1: Write the tests** (real `yq`/`jq`; env `LLM_ENV_CONFIG`, `LLM_ENV_PI_PROFILE_DIR`, `HOME` under `tmp_path`; run `bash setup/refresh-llm-env-pi-profile.sh` with `cwd=ROOT`)
  - `test_refresh_llm_env_pi_profile_rewrites_only_the_model_list`: config with `llm_server.enabled: true` and models `a` (enabled), `b` (disabled), `c` (enabled); profile `models.json` = `{"providers": {"llm-env": {"baseUrl": "x", "models": [{"id": "old"}]}, "other": {"models": [{"id": "keep"}]}}, "extra": 1}` with mode 0640: returncode 0; the JSON equals the original with `providers["llm-env"]["models"] == [{"id": "a"}, {"id": "c"}]`; mode still 0640; the directory holds only `models.json`.
  - `test_refresh_llm_env_pi_profile_changes_nothing_when_absent_disabled_or_foreign`: (a) no profile: returncode 0 and no file created; (b) `llm_server.enabled: false`: bytes unchanged; (c) a profile without the `llm-env` provider: bytes unchanged and `no llm-env provider` in stderr.
- [ ] **Step 2: Run** `uv run pytest tests/test_shell.py -q -k refresh_llm_env_pi_profile`. Expected: PASS (behavior tests for an existing script; if one fails, the script has a bug: report it before changing anything).
- [ ] **Step 3: Verify:** `make validate && make test`.
- [ ] **Step 4: Commit**

```bash
git add tests/test_shell.py
git commit -m "test(setup): cover refresh-llm-env-pi-profile.sh"
```

---

### Task 12 (P2): gpu-sample stops at once (simplify 4)

**Files:**
- Modify: `$SK/scripts/gpu-sample.sh:35-39` (`sleep "$every" & sleep_pid=$!; wait "$sleep_pid"`; the trap becomes `kill "$sleep_pid" 2>/dev/null || true; exit 0`, with `sleep_pid=""` set before the trap: `set -e` also applies inside a trap action)
- Test: scripts module

- [ ] **Step 1: Write the failing test** `test_gpu_sample_start_stops_within_a_second_and_leaves_no_sleep`: `Popen(["bash", SCRIPTS / "gpu-sample.sh", "start", "0000:00:00.0", csv, "7.31"])`; wait (max 5 s) until `csv` has two lines; send SIGTERM; `proc.wait(timeout=5)` returns within 1.0 s; afterwards no line of `ps -eo args` equals `sleep 7.31`; `finally` kills the process if it is still alive.
- [ ] **Step 2: Run** `uv run pytest tests/skills/onboard-local-model -q -k gpu_sample`. Expected: the new test FAILS (about 7 s); the summary test passes.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Verify:** `-k "gpu_sample or effort_run or bench_fit or latency"` passes; shellcheck; `make validate && make test`.
- [ ] **Step 5: No commit.**

---

### Task 13 (P2): capacity.py says what is stored (ponytail 12)

**Files:**
- Modify: `pylib/capacity.py:247` (docstring only)

- [ ] **Step 1: Edit** the `_view` docstring to: "The card as read against the current config: stale_sections exists only in this view; usable is recomputed here, while the stored copy (kept for readers of the file, such as ai-kit) reflects the config at write time." No behavior change: `usable` and `benchmark` stay in the file (Decision 12), pinned by `tests/test_capacity.py::test_card_keys_match_the_schema`.
- [ ] **Step 2: Verify:** `uv run pytest tests/test_capacity.py -q`; `make validate && make test`.
- [ ] **Step 3: Commit**

```bash
git add pylib/capacity.py
git commit -m "docs(capacity): the card file stores usable for direct readers"
```

---

### Task 14 (P2): One copy of the skill test helpers

**Files:**
- Create: `tests/skills/onboard-local-model/skill_helpers.py`, `tests/skills/onboard-local-model/conftest.py`
- Modify: `test_challenges.py`, `test_onboard_local_model_scripts.py`, `test_latency_profile.py` (same directory)

**Interfaces:**
- Produces (`skill_helpers.py`, Decision 9): `ROOT`, `SKILL`, `SCRIPTS`, `NODE`, `NODE_DIR`, `NODE_SKIP_REASON` (the existing reason text), `run(*args, **kwargs)`, `scratch(tmp_path) -> Path`, `out(tmp_path, name) -> Path`, `node_env(**extra) -> dict[str, str]`: the existing bodies, moved unchanged.
- Produces (`conftest.py`): the autouse `_scout_out_cleanup` fixture (imports `scratch`).
- The modules import with aliases so call sites stay, each importing only the names it uses (ruff flags unused imports), from `from skill_helpers import NODE, NODE_DIR, NODE_SKIP_REASON, ROOT, SCRIPTS, SKILL, node_env as _node_env, out as _out, run as _run, scratch as _scratch`. `NEEDS_NODE` and the challenges module's `pytestmark` use `NODE_SKIP_REASON`. `test_latency_profile.py`'s `env` fixture uses `out(tmp_path, "latency")` and drops its own `rmtree`.

- [ ] **Step 1: Record the baseline:** `uv run pytest tests/skills -q | tail -1` into `tmp/audit-fix-wave/skill-tests-before.txt`.
- [ ] **Step 2: Move** the helpers and the fixture; delete the copies.
- [ ] **Step 3: Verify:** `uv run pytest tests/skills -q | tail -1` shows the same passed and skipped counts as Step 1; `ls tmp/` shows no `skill-test-*` left; `make validate` (pyright resolves the bare import as it does for `tests/omniroute_fakes.py`; if it does not, stop and report rather than adding config); `make test`.
- [ ] **Step 4: No commit.**

---

### Task 15 (P2): Keep three doc-phrase tests, test that named scripts exist

**Files:**
- Modify: `tests/skills/onboard-local-model/test_onboard_local_model_scripts.py`

**Interfaces:**
- Kept unchanged: `test_skill_gates_announce_and_proceed_when_permissions_are_bypassed`, `test_skill_states_that_challenges_run_only_in_isolated_dirs_under_tmp` (as Task 4 left it), `test_skill_prose_has_no_long_shell_logic`, and the helpers `_md`, `_section`, `SKILL_MD`, `REFS`.
- Deleted (13): `test_skill_live_phase_uses_the_lifecycle_targets`, `test_make_benchmark_runs_only_with_llm_server_stopped_and_checks_the_gpu_keys`, `test_models_yml_reference_uses_the_lifecycle_targets`, `test_evals_use_the_lifecycle_targets`, `test_skill_documents_layered_isolated_delegated_benchmarks`, `test_skill_documents_model_argument_modes`, `test_skill_runs_benchmarks_of_a_named_gguf_file_only_on_a_copy`, `test_challenges_reference_covers_generator_gate_calibration_and_protocol`, `test_selection_criteria_defines_the_no_argument_ranking`, `test_tuning_reuses_the_ladder_seeds_for_quant_trials`, `test_evals_cover_the_model_argument_modes`, `test_skill_documents_tuning_gates_and_procedure`, `test_skill_documents_the_quant_headroom_procedure`; plus the constants only they use (`MODELS_MD`, `BENCHMARK_GUARD`, `CHALLENGES_MD` if unused).

- [ ] **Step 1: Write the replacement tests**
  - `test_skill_docs_name_only_existing_scripts`: over `SKILL.md`, `references/*.md` and `evals/*.json`, every match of `r"(?:[\w.-]+/)*[\w-]+\.sh\b"` resolves, either as `SCRIPTS / Path(token).name` or as `ROOT / token`; the set of matches contains `effort-run.sh` (the test cannot pass vacuously).
  - `test_skill_description_fits_the_spec_limit`: the front matter parses with `yaml.safe_load`, `name == "onboard-local-model"`, `0 < len(description) <= 1024`.
  - `test_only_bench_fit_mounts_the_models_directory`: the `mounts == ["bench-fit.sh"]` scan of the deleted N2-H1 test, unchanged.
- [ ] **Step 2: Run** `uv run pytest tests/skills/onboard-local-model -q -k "name_only_existing or fits_the_spec or only_bench_fit"`. Expected: PASS on the current docs (if a token does not resolve, the doc names a missing script: fix the doc).
- [ ] **Step 3: Delete** the 13 tests and their constants.
- [ ] **Step 4: Verify:** `grep -n "^def test_skill_\|^def test_evals_\|^def test_models_yml_\|^def test_make_benchmark_\|^def test_challenges_reference\|^def test_selection_criteria\|^def test_tuning_reuses" tests/skills/onboard-local-model/test_onboard_local_model_scripts.py` lists exactly `test_skill_gates_announce_and_proceed_when_permissions_are_bypassed`, `test_skill_states_that_challenges_run_only_in_isolated_dirs_under_tmp`, `test_skill_prose_has_no_long_shell_logic`, `test_skill_docs_name_only_existing_scripts` and `test_skill_description_fits_the_spec_limit`; `uv run pytest tests/skills -q`; `make validate && make test`.
- [ ] **Step 5: No commit.**

---

### Task 16 (P2, needs Task 15): Slim SKILL.md and merge its duplicated docs

**Files:**
- Create: `$SK/references/onboard-flow.md`
- Modify: `$SK/SKILL.md`, `$SK/references/tuning.md`, `$SK/references/scoring.md`, `$SK/references/challenges.md`, `$SK/references/models-yml.md`, any reference or eval that points to "SKILL.md step 4-9" or SKILL.md "Benchmark runs"
- Test: the scripts module (`test_skill_states_that_challenges_run_only_in_isolated_dirs_under_tmp`)

**Interfaces:**
- Produces: `references/onboard-flow.md` = "# Onboarding flow, steps 4-9" holding steps 4-9 and the "Benchmark runs" paragraph moved from SKILL.md, with three duplicates replaced by pointers: the head-to-head lanes, comparison and retry rules (to `challenges.md`, "Head-to-head protocol"; keep the `effort-run.sh` command, the upper-bound rule and the poll cadence), the `make benchmark` GPU-key guard (to `models-yml.md`, "Apply sequence" step 7), and the score reading (to `scoring.md`).
- Produces (SKILL.md): steps 1-3 and 10 unchanged; steps 4-9 each one to three lines naming what the step does, its gate as the bold `**Gate Gn: ...**` marker (so "Each `**Gate Gn:**` above" in `## Gates` stays true) and "detail: `references/onboard-flow.md` step N"; one line "Benchmark runs are isolated and delegated (`references/onboard-flow.md`, Benchmark runs)." followed by `ISOLATION_SENTENCE`, which no reference repeats. Helpers, Gates, NEVER and Gotchas stay.
- Produces (front matter `description`, 421 characters, a plain YAML scalar):

```text
Find, fit, bench, tune and onboard a local agentic coding GGUF for this repo's llama.cpp router beside System One (Laya), with HF scouting, pp/tg, effort ladders, ctx/KV/MoE tuning, the capacity card and llama.cpp or Laya updates. Use when asked which model fits the GPU or beats models.yml, or to swap one; also "modelo agéntico que quepa en mi GPU". Not for cloud providers, OmniRoute beyond my-coding-lite or sampling.
```

- [ ] **Step 1: Change the test:** `test_skill_states_that_challenges_run_only_in_isolated_dirs_under_tmp` asserts `ISOLATION_SENTENCE` in `SKILL.md` and that `tuning.md`, `scoring.md` and `challenges.md` do not contain it.
- [ ] **Step 2: Run** `uv run pytest tests/skills/onboard-local-model -q -k isolated_dirs`. Expected: FAIL (the references still repeat it).
- [ ] **Step 3: Record sizes:** `wc -lc $SK/SKILL.md` into `tmp/audit-fix-wave/skill-size-before.txt`.
- [ ] **Step 4: Move and merge** per the Interfaces block; repoint every `SKILL.md step 4`..`9` and SKILL.md "Benchmark runs" reference in `references/*.md` and `evals/*.json` to `onboard-flow.md`.
- [ ] **Step 5: Verify:** the Step 2 command passes; `uv run pytest tests/skills -q` (the kept prose guard and Task 15's tests cover the new file); `wc -l $SK/SKILL.md` is at most 160 and `wc -c` at most 14500 (today 215 lines, 21878 bytes, of which steps 4-9 are 71 lines, 8698 bytes); `yq -r --front-matter=extract '.description' $SK/SKILL.md | tr -d '\n' | wc -m` prints 421; `grep -rn "SKILL.md steps\? [4-9]\|SKILL.md \"Benchmark runs\"" $SK` prints nothing; `make validate && make test`.
- [ ] **Step 6: No commit.**

---

### Task 17: Final verification and hand-off (last)

**Files:**
- Modify: `docs/superpowers/plans/README.md` (this plan's row, left uncommitted)

- [ ] **Step 1:** `make validate`. Expected: `All checks passed.` (it now includes the skill scripts, Task 5).
- [ ] **Step 2:** `make test`. Expected: all tests pass.
- [ ] **Step 3:** `shellcheck -x -s bash .claude/skills/onboard-local-model/scripts/*.sh`. Expected: no output.
- [ ] **Step 4:** `bash .claude/skills/onboard-local-model/scripts/gen-challenge.sh --check-library --out tmp/audit-fix-wave/lib-check`. Expected: exit 0, `lint` empty, every row valid. Then `rm -rf tmp/audit-fix-wave/lib-check`.
- [ ] **Step 5: Leftovers:** `ls tmp/` shows no `skill-test-*` directory; `ps -eo pid,args | awk '/onboard-local-model\/scripts\/|node [-]-test/'` (the bracket keeps awk from matching itself) shows nothing this work started (anything else belongs to another session: leave it); every PID this work started is gone (`ps -p <pid>`); `podman ps -a` lists nothing new.
- [ ] **Step 6: Git:** `git status --short` shows only the entries of `tmp/audit-fix-wave/status-before.txt`, this plan file, and files parallel work changed; `git log --stat` for this work shows the commits of Tasks 3, 4, 5, 8, 9, 11 and 13 (those that ran), each with only its listed tracked files. Report the untracked files this plan created, edited or deleted under `.claude/skills/onboard-local-model/` and `tests/skills/onboard-local-model/` (the lists recorded per task) for the owner's decision on committing the skill tree. Remove `tmp/audit-fix-wave/`.
- [ ] **Step 7:** In `docs/superpowers/plans/README.md`, set this plan's row to **DONE** (or **PARTIAL** naming the dropped P2 tasks), noting "skill edits uncommitted with the untracked skill tree; replaces the bundled regression set kept by 2026-10-31-skill-model-or-none-and-generated-challenges". Leave it uncommitted with the index's other pending edits.

No commit.

---

## Findings rejected

| Source | Claim | Evidence | Outcome |
|---|---|---|---|
| ponytail 1 (fix) | `timeout -k 5 120 node --test --test-timeout=30000` bounds scoring | Fact 3: with `--test-timeout` alone a test that leaves a timer keeps its file alive until the outer `timeout` (120 s per run), and the killed run prints no TAP summary, so the whole verdict becomes 0/0 and the other tests' results are lost; adding `--test-force-exit` ends the file at the test timeout but reports the hung test as `# cancelled`, which a `# pass`/`# fail` count drops (a false solve) | Finding kept, fix replaced (Decisions 1-2) |
| ponytail 2 (fix) | `if len(r) >= 2` makes `_latency` safe | `float(r[1])` still raises `ValueError` on a non-numeric field; a short `503` row would drop a non-200; a missing `laya-during.txt` while System One runs passes as "disabled" (Fact 5) | Finding kept, fix widened (Decision 3) |
| simplify 6 | drop the stored `usable` (and its schema `required`) and the unread `card["benchmark"]` | ai-kit's adapter reads `usable.context_tokens` and `benchmark.tg_tps` from the card file (Fact 15) | Rejected; docstring fixed instead (Task 13) |
| simplify 6 | make `budget` required in `quant_headroom`, delete the formula path | deliberate design (commit 5e54253; README: the formula is "shown beside it and never override[s] it") | Rejected |
| simplify 2 | the `// 95` and `// 5120` fallbacks in `preflight.sh` are wrong | they equal `pylib/config.py` defaults (lines 143 and 136); only `// 60` is wrong (config.py:99 says 46) | Narrowed to the RAM budget (Task 6) |
| simplify 4 | the foreground sleep leaves an orphan sleep | bash runs the TERM trap only after the foreground child exits, so nothing is orphaned; the latency part is right (Fact 14). The orphan risk belongs to the `sleep & wait` fix, so Task 12 kills the sleep in the trap | Latency kept, orphan claim rejected |
| ponytail 5 | `llama-config.sh` validates its edit with `migrate-config` like quant-headroom | it migrates before the edit (Fact 11), so its `yq` edit is unvalidated today | Corrected: the helper validates after the edit for every caller |
| ponytail 5, 6 | "AGENTS.md says" never rewrite the live config without a backup / "no other rollback" | that text is in SKILL.md NEVER (lines 194-196), not AGENTS.md | Attribution corrected; findings kept |
| ponytail 4 | 19 skill scripts | 18 `.sh` files in `$SK/scripts/` | Count corrected; finding kept |
| entropy 1 | keep `verify.sh`, move it to `scripts/` | owner decision: merge it into the generator with a time limit | Superseded (Task 2) |
| simplify 1, ponytail 14 | keep `tasks/` as a frozen set, or rebuild `solutions/` from projects | owner decision: delete the bundled tasks | Superseded (Task 4) |

Not in this wave (not on the owner's list, so not planned; verified only where noted): ponytail 13 (the card fingerprint ignores `gpu.image` and the GGUF hash); simplify 5 (per-sample `uv run` forks in the interference sampler), 9-12, 15-17, 20-31; entropy 3 (`_unmeasured` list) and 4 (`_min`/`_max`); `ensure_api_key`, `ensure_omniroute_secrets` and `reset_api_key` in `tools/lib.sh` also `yq -i` the live config (only when a secret is missing or reset; found while checking ponytail 6).

## Self-Review

- Owner decisions covered: precondition (Task 1); ponytail 1 with timeout, per-test limit and the group kill (Task 2); ponytail 2 test-first with the exact probe, the n == 0 failure and malformed rows (Task 3); bundled deletion with docs, evals, tests and the seeds 1-3 regression (Task 4); shellcheck glob (Task 5); RAM budget and its hf-scout consequence (Task 6); `--efforts` (Task 7); atomic change-only `start.sh` write with a backup like llama-config's (Task 8); one shared helper with migrate-config for tune-apply (Tasks 8-9); hf-scout recursive (Task 10); refresh-profile test (Task 11); gpu-sample latency (Task 12); capacity docstring with the card contract kept (Task 13); test helpers (Task 14, location per Decision 9); phrase tests to three plus the scripts-exist test (Task 15); SKILL.md slimming, description and doc merge (Task 16); skips stated (Decision 13); final verification last (Task 17).
- Names across tasks: `score`, `--score`, `_verify(directory, **env)`, `timed_out`, `ONBOARD_GEN_TEST_TIMEOUT_MS`; `edit_config_atomically`, tags `""`, `pre-tune`, `pre-quant`, `pre-start`, `pre-test` (test only); `set_llm_server_limits`, `apply_llama`, `apply_row`, `add_copy`; `ram_offload_budget_mib`, `ram_budget_source`; `ISOLATION_SENTENCE`; `_effort_run(..., timeout=, out_name=)`, `_rows(tmp_path, out_name=)`, `SEEDS_ALL`; `skill_helpers`.
- Not verified when this plan was written: `--test-force-exit` on node 20.14 (measured on v24.21.0 only); the HF tree API's paging of `recursive=true` for very large repos (quant-headroom already relies on it); that pyright resolves `from skill_helpers import` (repo precedent says yes; Task 14 Step 3 checks); test runtimes (seeds 1-3 x 5 layers, the five-layer effort-run test); when the campaign ends (Task 1 waits).

## Round-1 review outcome (accepted, 3 confirmed high)

Review round 1 (native + referee-two, lead-verified against the code) left three CONFIRMED findings, at the approval threshold; the two
referee-two highs were refuted (tmp/audit-fix-plan/review-round-1.md). Plan status: approved for execution. The implementer must apply:

- N-H1 (Task 2 red run): the hanging-solution test hangs in the red step because the current verify has no time limit, and the pytest timeout
  kills only the effort-run shell, leaving `node --test` and the `gpu-sample.sh` sampler spinning. Fix: do not run that test in the red step
  (assert red with a different, bounded test), and make the test harness kill the whole process group (`killpg`) on timeout; leftover
  processes after the test are asserted absent.
- N-H2 (Task 8): the "limits unchanged" test already passes against today's `start.sh`, so it is not red. Add an assertion from the yq call
  log that no `.resources.llm_server.cpus =` (or other limits) write happened, so the test goes red before the change and green after.
- N-H3 (Task 4): "assertions otherwise kept" leaves the `--tasks fix-pagination` unknown-task check inside the layers-flag test, which fails
  once the flag is deleted. Delete that sub-check in the same task.
