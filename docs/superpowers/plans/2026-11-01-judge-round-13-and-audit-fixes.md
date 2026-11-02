# Judge Round 13 and Audit Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the confirmed findings of round 13 of the onboard-local-model skill judges (native 88.0 % B: 1 high, 4 medium, 6 low; referee 86.4 % B, every finding refuted by the lead) and of three fresh audits (ponytail, simplify, entropy). The next round should give >= 90 % from both lanes with no critical or high finding, and the ladder score should not be gameable.

**Architecture:** Four groups of change:
- **Gateway host without GPU config (the native high).** `make setup` with local inference off skips GPU detection and the Vulkan device. Two scripts now name the empty key: `preflight.sh` for `gpu.pci_address`, `bench-fit.sh` for `gpu.device_name` (red-first). Onboarding step 1 branches on them: an empty `gpu.pci_address` reports and stops; an empty `gpu.device_name` moves Gate G3 before the screen. Tuning T2 runs `make setup`. The eval fixtures get the GPU fields a real host has.
- **Score integrity (ponytail audit).** `effort-run.sh` never takes a smaller test total than the manifest's, so a solution that calls `process.exit(0)` at load no longer scores as solved. `tune-sweep.sh` benchmarks each kv/ngl/moe once instead of once per context size: the default grid drops from 24 runs to 12.
- **One source of truth (simplify audit).** One `llmenv_budget_json` helper in tracked `tools/lib.sh` replaces four workarounds, and it fixes a preflight bug: a budget error printed an object of nulls, not the `null` that rule 1 and the fixtures assume. Preflight also treats a 0 ceiling as no cap, as `compute_budget` does. `tune-sweep.sh` records the reference and full depths, and `tune-pick.sh` reads them. A new `llmenv agent-runtime-cap` replaces scraping Python source.
- **Text (native mediums).** Preflight exposes `vram_feasible`, and rule 1 leads with it. Alias reuse needs an equal size. The local GGUF gets one run order with one numbering scheme. The gateway-enabled eval gets its own preflight fixture. The safe entropy deletions are included.

`SKILL.md` gets one edit (rule 1) and one shortening (the bypass paragraph). Measured on a scratch copy: 10,983 -> 10,987 bytes, under the 11,000 cap.

**Tech Stack:** bash, jq, yq, node (challenge generator); Python (`llmenv.py`, pytest); skill-judge rubric v4.

**Spec:**
- `tmp/judges/r13-native-summary.txt`.
- `tmp/judges/r13-referee-summary.txt`, which holds the lead's check of every referee finding.
- `tmp/audits/r13-ponytail.md`, `tmp/audits/r13-simplify.md`, `tmp/audits/r13-entropy.md`.
- Base state: plan N (`docs/superpowers/plans/2026-10-31-judge-round-12-fixes.md`, executed).

Read-only planning runs on 2026-11-01:
- `gen-challenge.sh --layer 1 --seed 7`: `tests_total` 13. `--score` on the pristine challenge gives `{"pass":4,"total":13}`. The same tree with `process.exit(0);` prepended to each `src/*.js` gives `{"pass":2,"total":2}`, which counts as solved.
- preflight's `llmenv_budget` projection run on `{"error": "x"}` gives an object whose eight values are all null.
- The `SKILL.md` edits of Task 6, applied to a scratch copy, measure 10,983 -> 10,987 bytes.

## Global Constraints

- English only. Edit only:
  - `.claude/skills/onboard-local-model/**`;
  - `tests/skills/onboard-local-model/**`;
  - `tools/lib.sh` and `tests/test_shell.py` (Task 5);
  - `llmenv.py` and `tests/test_cli.py` (Task 7);
  - this plan's row in `docs/superpowers/plans/README.md` (already added).
- The skill tree and `tests/skills` are untracked and stay uncommitted. Tracked edits are committed per task, with their tests, after `make validate && make test`, with no attribution lines. The README rows of plans G-N stay as they are.
- `SKILL.md` <= 11,000 bytes (`SKILL_MD_MAX_BYTES`, tested). Never raise the cap. Shorten before adding; move text into references.
- `jev-fast-decision` is a black box, and ai-kit is never named in the skill tree (`test_skill_and_touched_docs_treat_jev_as_a_black_box`).
- Prose holds no shell logic: a backtick span holds at most two of `;`, `&&`, `||`. Every `--out` is named once, shared rules are stated once (`STATED_ONCE`), and retired wording stays out (`RETIRED_COPIES`). Changing a pinned phrase means changing its pin in the same task.
- A reference names another reference only by path, and only for the pairs in `CITES`. This plan adds no new pair.
- Every script keeps `--help` (exit 0) and its tests. A behaviour change is red-first: write the test, watch it fail, then implement. Doc and fixture tests are written first too, and fail until the text lands.
- After a `.sh` edit run `make validate`. After a `.py` edit (test files included) run `make validate && make test`.
- pytest: one file per run, never two runs at once, through `tmp/r13/pytest-one.sh` (Task 0). It puts `--basetemp` in a fresh `mktemp -d /var/tmp/pytest-r13.XXXXXX` and `TMPDIR` in a `./tmp/r13/` scratch dir, and deletes both afterwards. `make test` runs with `TMPDIR` unset: the skill conftest fails fast when pytest's temp dir is inside the repo.
- Scratch files live under `./tmp/r13/`, and the run that creates them removes them. No containers, no GPU, no network, no downloads, and no edits to the live `models.yml` or to any capacity card. Run `hf-scout.sh` by hand only with `TMPDIR` set to a `./tmp/r13/` folder.
- Eval `prompt` is the user's message alone. `setup` is present exactly when `files` is (`test_eval_prompts_read_as_user_turns`).

## Findings ledger (verified 2026-11-01 against the files)

| Finding | Verdict | Task |
|---|---|---|
| native D8-1 (high): a gateway-only host has no GPU config, but onboard-flow.md:274-275 and evals.json:232 say screening works | confirmed. `setup.sh`:81 runs Step 2 (GPU detection, `pci_address`) only when llm-server or System One is enabled. :272-285 skip Step 6, the only writer of `device_name` (:231-240). `models.yml.example`:12-13 leaves both empty. Effects: `bench-fit.sh`:53 fails with `device '' not found` (`llmenv.py`:239-242); `preflight.sh`:16-18 exits 2 with `configured GPU  not detected`; `cmd_budget` fails (`llmenv.py`:308-312). tuning.md:22 T2 runs no `make setup`. Also found: every eval config fixture has empty `pci_address` and `device_name`, while the preflight fixtures say `0000:03:00.0`. A step-1 check would misroute every served-host eval, so the fixtures change too | 8 |
| native D8-2 (medium): SKILL.md:46-47 leads with a false `feasible` | confirmed. `feasible` = VRAM and RAM (`llmenv.py`:440). A disabled llm-server gets no RAM (`pylib/resources.py`:247-285), and preflight.sh:129 does not pass `vram_feasible`. Also found: preflight never checks `.error`. When the budget cannot answer (nothing enabled, so a `BudgetError`; or a missing enabled file), `llmenv_budget` is an object of nulls, not `null` | 5, 6 |
| native D8-3 (medium): alias reused by file name only (onboard-flow.md:21, :68; local-gguf.md:23-28) | confirmed. Suppose a non-baseline live entry has the same `file` from another revision and its file is absent. H5/A1 make it the candidate, and the G1 size check fails only after the download. L1 has the same hole | 9 |
| native D2-1 (medium): local-GGUF steps under three numbering schemes | confirmed: local-gguf.md:4, :14-16, :32; onboard-flow.md:84, :131 | 10 |
| native D7-1 (medium): gateway-enabled-alias eval pairs an enabled, present entry with `llmenv_budget: null` | confirmed. `cmd_budget` gives a verdict for any enabled entry whose file is present. The fixture GGUF is present, so the real figure is 8321 (15489 - 1024 - 5120 - 1024) | 6 |
| native lows D1-1, D3-1, D4-1, D5-1, D6-1, D7-2 | dropped. Not in this round's brief. D3-1 and D4-1 need `SKILL.md` bytes. D5-1 is the referee's D5-1, refuted by the lead (the quant section reuses T8, G-T2 and Rollback). D7-2 is the prose-snapshot layer (owner: not this round) | - |
| referee high and 5 mediums | refuted by the lead (r13 referee summary) | - |
| referee lows D1-1 (runtimes.md numbers), D1-2 (anchors table), D6-1, D7-2 | dropped. D1-1: the numbers back the NEVER line in `SKILL.md` ("88 for a 9B"), and the lead rated trimming optional. D1-2: optional, dated and flagged for re-check. D6-1: refuted by the lead. D7-2: the balance test pins 8-10 positives, and the Spanish query already names no model | - |
| ponytail 1 (must fix): `process.exit(0)` at load scores as solved | confirmed by a run (see Spec). effort-run.sh:171 builds `meta`, :201 scores, :213 writes `total`; `gen.mjs`:222 already stores `tests_total` | 2 |
| ponytail 2 (should fix): tune-sweep repeats the same llama-bench per ctx | confirmed. The audit's line numbers are wrong: tune-sweep.sh is 168 lines, stage 2 is :143-153 and stage 3 :155-166. bench-fit.sh:58-59 passes no ctx (llama-bench sizes its context from the depth). Default grid: 18 throughput runs (2 kv x 3 ctx x 3 ubatch), 6 of them distinct, plus 6 verify | 3 |
| ponytail risk: bench-fit gives System One 10 s to reload | refuted by the repo's recorded measurement: a reload takes about 0.4 s (`references/system-one.md`:13, `models.yml.example`:47). Not re-measured (no GPU) | - |
| ponytail 3-6 (cache-buster, quant stand-in, `solved` twice, prose pins) | dropped. Not in the brief. Finding 3 edits `challenges/` (a new generator id). Finding 6 is the prose-snapshot layer | - |
| simplify 2: ceiling 0 = uncapped in `pylib/budget.py`:116 and `llmenv.py`:336, but 95 % in preflight.sh:136-137 | confirmed. The configuration contract (`models.yml.example`: "0 = no cap") and `compute_budget` agree; preflight goes | 6 |
| simplify 7: four scripts work around `llmenv budget` exiting 1 with JSON | confirmed (tune-sweep.sh:95-104, quant-headroom.sh:112-115 and :139-145, preflight.sh:126-130). Only preflight is wrong (see D8-2) | 5 |
| simplify 8: `min(131072, ctx - 512)` in tune-pick.sh:26 and tune-sweep.sh:164 vs `pylib/capacity.py`:372 | confirmed for the two shell copies. The rest of finding 8 is refuted: hf-scout's +1536 screening allowance and fit-math's "~1 GiB buffers" are different quantities | 4 |
| simplify 10: `DEFAULT_RUNTIME_SECONDS` scraped by regex (effort-run.sh:75, test :24) | confirmed. The constant is already `300.0`; the scrape works only because it stops at the dot | 7 |
| simplify 1, 3-6, 9, 11-23 | dropped. See "Not planned" | - |
| entropy 1, 3, 4a, 4c (fixture comments, challenges.md duplicates, merged alias-typo tests, unused `_edit`) | confirmed (grep: no pin on the deleted text; `Gemma4` and `orinth-35b` are already in the near-miss parametrization at test :2083-2091) | 1 |
| entropy 2 (three unused event-pipeline modules) | owner decision 1 (default: leave) | - |
| entropy 4b, 5 | not requested (4b saves 5 lines, 5 is optional per the audit) | - |

## Not planned (out of scope, with the reason)

- **Prose-snapshot test layer** (simplify 1, ponytail 6, native D7-2). Owner call: not this round. Pins this plan touches are updated in place.
- **`llmenv sample`** (simplify 3). `gpu-sample.sh` uses bash builtins on purpose, so the sampler adds no load (`gpu-sample.sh`:12). A Python loop would have to be measured first, and nothing samples wrongly today.
- **`common.sh`** (simplify 5). Renaming the include would touch the sourcing of all 18 scripts and their tests for zero behaviour change. The bare `$2` reads (exit 1 instead of 2 on a missing value) are cosmetic.
- **Other duplication and performance findings** (simplify 4, 6, 9, 11-23). None is an inconsistency that gives a wrong answer. Findings 12, 13, 18 and 21 edit `challenges/`, which changes the generator id and retires every `seeds.json`.

## Owner decisions (defaults chosen)

1. **Event-pipeline modules** (entropy 2). Deleting them changes the generator id. Default: leave.
2. **Empty `gpu.pci_address` at onboarding.** Without it no budget can be sized. Enabling some alias just to run `make setup` would download that alias's file. Default: report and stop, naming AGENTS.md's switch (`make llama-enable MODEL=<alias>`, then `LLM_ENV_ASSUME_YES=1 make setup`). Tuning knows its alias, so T2 runs the switch itself.
3. **Empty `gpu.device_name` with `gpu.pci_address` set** (System One on). Gate G3 and 7a.1-7a.4 move before step 6; then the live `gpu` block is copied into `$S`. Default: as stated. The alternative is reporting and stopping as in decision 2.
4. **The process.exit fix** keeps `challenges/` untouched, so the generator id stays. Known limit: `tests_total` counts a shipped test file that fails to load (a removed feature) as one test. A reference-side total in the manifest would close that, at the cost of a new generator id. Default: not now; a `shortcut:` comment records it.
5. **`llmenv_budget_json` lives in `tools/lib.sh`.** Every skill script already sources it, and it is the repo's own command contract. `setup.sh` and `start.sh` keep their own handling, because they print remedies from the exit code.
6. **Runner cap** through a new `llmenv agent-runtime-cap`, which prints `{"runtime_seconds": 300}`. The alternative, a `--print-max-runtime` flag, would need the required remainder command of `run-agent-bounded` bypassed.
7. **Throughput pass:** the first feasible ctx of each kv/ngl/moe, in `--ctx` list order. Verify still runs per feasible ctx.
8. **Old `results.jsonl`** (no `ref_depth`/`full_depth`): `tune-pick.sh` rejects its verify rows with a reason; it never re-derives the depths.

## Review Focus

1. A host where System One is on but `make setup` ran gateway-only, so `gpu.device_name` is empty. Expected: G3 before step 6, then `$S` takes the live `gpu` block, and the screen runs. A no ends like a declined G3. Pinned in Task 8 (step-1 doc test, `bench-fit.sh` exit-2 test).
2. llm-server enabled, but the enabled entry's file was pruned. `llmenv budget` errors. Expected: `llmenv_budget` null, and rule 1 sizes against `model_vram_budget_mib`; never a "does not fit" from an object of nulls. Pinned in Task 5 (`test_preflight_reports_no_llmenv_budget_when_llmenv_budget_cannot_answer`).
3. A `--ctx` list whose first value is infeasible (`--ctx 262144,131072`). Expected: the throughput pass runs at 131072, and the verify of every feasible ctx reuses it. Pinned in Task 3 (the parametrized order case).
4. A `results.jsonl` from a sweep older than this change, given to `tune-pick.sh`. Expected: each verify row is rejected as "no recorded depths", never as a misleading "not verified to full depth". Pinned in Task 4.
5. A live entry, not the baseline, with the candidate's file name but another `size_bytes` and no file on disk. Expected: report both sizes and stop before G1, on both the Hugging Face and the local GGUF route. Pinned in Task 9.

---

### Task 0: Scratch pytest runner

**Files:**
- Create: `tmp/r13/pytest-one.sh` (scratch; Task 11 deletes `tmp/r13/`)

- [ ] **Step 1: Write the runner**

```bash
#!/usr/bin/env bash
# pytest-one.sh FILE [ARGS...]: one pytest file; --basetemp in a fresh /var/tmp dir, TMPDIR in a ./tmp/r13 dir; both removed after.
set -euo pipefail
repo="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
bt="$(mktemp -d /var/tmp/pytest-r13.XXXXXX)"
td="$(mktemp -d "$repo/tmp/r13/tmpdir.XXXXXX")"
trap 'rm -rf "$bt" "$td"' EXIT
cd "$repo"
TMPDIR="$td" uv run pytest "$@" -q -p no:cacheprovider --basetemp "$bt/bt"
```

- [ ] **Step 2: Baseline run.** `bash tmp/r13/pytest-one.sh tests/skills/onboard-local-model/test_onboard_local_model_scripts.py`. Then the same for `test_challenges.py` and `test_latency_profile.py`. Expected: all pass (337 in total, as the judges saw). Record any pre-existing failure before Task 1.

### Task 1: Safe entropy deletions

**Files:**
- Modify: `.claude/skills/onboard-local-model/evals/files/models.yml`, `models-gateway.yml`, `models-gateway-enabled.yml`, `models-local-url.yml`, `models-subfolder.yml` (whole-line comments only)
- Modify: `.claude/skills/onboard-local-model/references/challenges.md` (:121-129, :143-147, step 4 at :141, :151-152, :154)
- Modify: `tests/skills/onboard-local-model/test_onboard_local_model_scripts.py` (:2123-2136)
- Modify: `tests/skills/onboard-local-model/test_challenges.py` (:372-373)

- [ ] **Step 1: Strip the copied template comments.** `sed -i '/^[[:space:]]*#/d'` on the five fixtures (19 lines each, 95 in all). Inline `key: value # ...` comments stay. Check: `yq -o=json . <file>` is unchanged for each file (compare before/after JSON in `tmp/r13/`).
- [ ] **Step 2: challenges.md.**
  - Delete the "Intended difficulty per layer" sentence and its table (:121-129); the L1-L5 table in `references/scoring.md` and the layer invariants at :93 keep it.
  - Delete `### Calibration log` and its placeholder table (:143-147), and step 4's "Add a row to the log.". Step 4 keeps its generator-id sentence.
  - Delete the first Anti-contamination bullet (:151-152): it repeats the `SKILL.md` NEVER line, and "takes no input file" is at :27.
  - Delete the parenthetical "(a tripwire, not a sandbox)" at :154; the sentence at :157 keeps it.
- [ ] **Step 3: Merge the two alias-typo eval tests.** Replace the tests at :2123-2136 with one test. It drops their `resolve-target.sh` runs, which `test_resolve_target_names_a_near_miss_alias_instead_of_a_search` already covers.

```python
@pytest.mark.parametrize(("eval_id", "alias"), [("alias-typo", "gemma4"), ("alias-edit-typo", "ornith-35b")])
def test_eval_alias_typo_fixtures_match_their_assertions(eval_id: str, alias: str) -> None:
    want = f"no models.yml alias of that name; did you mean alias {alias}?"
    assert all(s in _asserts(eval_id) for s in ("exits 2", want, "hf-scout.sh --query is not run"))
    find = _section(_md(REFS / "selection-criteria.md"), "## Find the repo", "## Scout and rank")
    assert "near-miss alias" in find and "no, search it" in find
```

- [ ] **Step 4: Delete `_edit`** from `test_challenges.py` (:372-373, never called).
- [ ] **Step 5: Run.** `make validate && make test`, plus the three skill files through `tmp/r13/pytest-one.sh`. Expected: all pass, with one test fewer in the main file.

### Task 2: `effort-run.sh` never scores a solution that exits at load as solved

**Files:**
- Modify: `.claude/skills/onboard-local-model/scripts/effort-run.sh` (header :19-20, :171, :213)
- Test: `tests/skills/onboard-local-model/test_onboard_local_model_scripts.py` (`AGENT_MISBEHAVIOUR` :438-454; new test after `test_effort_run_scores_a_hanging_solution_as_failed` :584)

**Interfaces:**
- Produces: every results row carries `tests_total` (the manifest's), and `total` = `max(verdict.total, tests_total)`. `score.sh` is unchanged: solved is still `pass == total and total > 0`.

- [ ] **Step 1: Add the misbehaviour switch** to `AGENT_MISBEHAVIOUR`: `[ -z "${EXIT0:-}" ] || sed -i '1i process.exit(0);' src/*.js`.
- [ ] **Step 2: Write the failing test**

```python
@NEEDS_NODE
def test_effort_run_never_scores_a_solution_that_exits_at_load_as_solved(tmp_path: Path) -> None:
    seeds = _seeds_file(tmp_path, {"1": [7]})
    result = _effort_run(tmp_path, "--layers", "1", "--seeds", str(seeds), env={"EXIT0": "1"})
    assert result.returncode == 0, result.stderr
    (row,) = _rows(tmp_path)
    manifest = json.loads((_out(tmp_path, "out") / "challenges" / "l1-s7" / "manifest.json").read_text())
    assert manifest["tests_total"] == 13 and row["tests_total"] == 13
    assert row["total"] == 13 and row["pass"] < row["total"]
```

- [ ] **Step 3: Run it red.** Expected: FAIL (`row["total"]` is 2 and there is no `tests_total` key).
- [ ] **Step 4: Implement.**
  - :171: `meta` becomes `{generator: $g, project, operators: [.operators[].op], tests_total}`.
  - :213: `total: ([$verdict.total, $meta.tests_total] | max)`. A hung file's extra failure survives `max`, and a missing `tests_total` (null) leaves the verdict's total.
  - Above :213 add: `# shortcut: tests_total counts a shipped test file that fails to load as one test; a reference-side total in the manifest closes that, at the cost of a new generator id.`
  - Header after :20: "The row's `total` is never below the manifest's `tests_total`, so a solution that exits before its tests run (`process.exit` at load) never counts as solved."
- [ ] **Step 5: Run green.** Run the new test, then the whole file through `tmp/r13/pytest-one.sh`, then `make validate`. Expected: PASS.

### Task 3: `tune-sweep.sh` runs one throughput pass per kv/ngl/moe

**Files:**
- Modify: `.claude/skills/onboard-local-model/scripts/tune-sweep.sh` (header :5-8, upper bound :65-70, stage 2 :143-153, stage 3 select :158-162)
- Test: `tests/skills/onboard-local-model/test_onboard_local_model_scripts.py` (`TUNE_UV` :1340-1351, `test_tune_sweep_skips_infeasible_fits_and_commits_each_run` :1384, new test after it)

**Interfaces:**
- Produces: throughput rows exist for the first feasible ctx of each `kv|ngl|moe`, in `--ctx` order. A verify row takes `ubatch`, `fa` and `threads` from the best throughput row with the same kv, ngl and moe, whatever its ctx. The row and plan formats are otherwise unchanged.

- [ ] **Step 1: Stub switch.** In `TUNE_UV`, `f=false; [ "$ctx" = 131072 ] && f=true` becomes `f=false; { [ "$ctx" = 131072 ] || [ -n "${ALL_FIT:-}" ]; } && f=true`.
- [ ] **Step 2: Write the failing tests**

```python
@pytest.mark.parametrize(("ctx", "env_extra", "bound", "stages"), [
    ("131072,196608", {"ALL_FIT": "1"}, 3, [("throughput", 131072), ("verify", 131072), ("verify", 196608)]),
    ("262144,131072", {}, 3, [("throughput", 131072), ("verify", 131072)]),  # the first feasible ctx runs the pass
])
def test_tune_sweep_benchmarks_each_kv_ngl_moe_once_across_context_sizes(
    tmp_path: Path, ctx: str, env_extra: dict[str, str], bound: int, stages: list[tuple[str, int]]
) -> None:
    out = _out(tmp_path, "sweep")
    result = _run("bash", SCRIPTS / "tune-sweep.sh", "--alias", "m", "--out", out, "--ctx", ctx, "--kv", "q5_1", "--ubatch", "512",
                  env=_tune_env(tmp_path) | env_extra)
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith(f"upper bound: {bound} runs x 3600s"), result.stdout
    rows = _jsonl(out / "results.jsonl")
    assert [(r["stage"], r["ctx"]) for r in rows] == stages
    assert all(r["ubatch"] == 512 and r["fa"] == "on" for r in rows)
```

  In the existing test (:1384), `"upper bound: 6 runs x 3600s"` becomes `"upper bound: 4 runs x 3600s"`. Its rows and depths stay, because only ctx 131072 is feasible there.
- [ ] **Step 3: Run red.** Expected: FAIL. The first case gets 4 rows with a second throughput row, and its bound is 4.
- [ ] **Step 4: Implement.**
  - Upper bound: `runs=$((${#kvs[@]} * ${#ngls[@]} * tcount * ${#ubs[@]} * ${#fas[@]} + fits_max * ${#moes[@]}))`.
  - Stage 2: keep a `swept=" "` string. Skip a fit whose `"$kv|$ngl|$moe"` is already in it, else append it. One comment line: llama-bench sizes its context from the depth (bench-fit.sh passes no ctx), so one pass serves every ctx.
  - Stage 3: drop `--argjson ctx "$ctx"` and `and .ctx == $ctx` from the `best` select. `$ctx` is still needed for the depths.
  - Header: throughput runs "per kv x ngl x moe at its first feasible ctx, x ubatch x fa x threads". Verify takes "the best throughput row of the fit's kv, ngl and moe".
- [ ] **Step 5: Run green.** Run the tune tests, then the whole file through `tmp/r13/pytest-one.sh`, then `make validate`. Expected: PASS. Default grid by the new formula: 2·1·1·3·1 + 6 = 12 runs (was 24).

### Task 4: The reference and full depths are derived once

**Files:**
- Modify: `.claude/skills/onboard-local-model/scripts/tune-sweep.sh` (`bench_run` :117-141, stage 3 :164-165)
- Modify: `.claude/skills/onboard-local-model/scripts/tune-pick.sh` (header :5-8, jq :24-37)
- Test: `tests/skills/onboard-local-model/test_onboard_local_model_scripts.py` (`_verify_row` :1450, `test_tune_pick_ranks_context_first_then_decode_at_reference_depth` :1457, `test_tune_sweep_skips_infeasible_fits_and_commits_each_run`)

**Interfaces:**
- Consumes: Task 3's stage 3.
- Produces: verify rows carry `ref_depth` (`min(131072, ctx - 512)`) and `full_depth` (`ctx - 512`); throughput rows carry both as null. `tune-pick.sh` reads them and derives nothing. Its output keys are unchanged (ranked rows keep `ref_depth`, `tg_at_ref`, `pp_at_ref`).

- [ ] **Step 1: Write the failing tests.**
  - `_verify_row` gains `"ref_depth": min(131072, ctx - 512), "full_depth": ctx - 512` (the test's copy of the rule).
  - The tune-pick test appends `json.dumps({"id": "G", "stage": "verify", "ctx": 131072, "status": "ok", "vram_available_mib": 8321, "model_vram_mib": 7000, "tps": [{"depth": 130560, "pp_tps": 2000, "tg_tps": 80}]})`.
  - Its expected `rejected` list ends with `{"id": "G", "reason": "no recorded depths (a results.jsonl older than tune-pick: re-run the sweep)"}`.
  - The sweep test adds `assert (rows[2]["ref_depth"], rows[2]["full_depth"]) == (130560, 130560) and rows[0]["ref_depth"] is None`.
- [ ] **Step 2: Run red.** Expected: FAIL (G is ranked, the sweep rows have no `ref_depth`).
- [ ] **Step 3: Implement.**
  - tune-sweep: one variable `prompt=512` with the comment "bench-fit.sh's -p 512 rides on top of each depth (BENCH_PROMPT_TOKENS in pylib/capacity.py)". Stage 3 computes `ref`, `full` and the depth list in its one jq call. `bench_run` takes two optional trailing arguments, REF and FULL, and writes them as `ref_depth`/`full_depth` (`num`, so "" becomes null).
  - tune-pick: `def ref: .ref_depth;` and F = `.full_depth`. A new second reason, right after `failed`: `.ref_depth == null or .full_depth == null` gives the reason string above. Header: "R and F are the row's ref_depth and full_depth, written by tune-sweep.sh".
- [ ] **Step 4: Run green.** Run the file through `tmp/r13/pytest-one.sh`, then `make validate`. Expected: PASS. `grep -n '131072' scripts/tune-pick.sh` finds only the header text.

### Task 5: One `llmenv budget` reader, and preflight's null on an error

**Files:**
- Modify: `tools/lib.sh` (after `llmenv()` :261), tracked
- Test: `tests/test_shell.py` (after `test_presets_helpers_read_a_generated_ini` :184), tracked
- Modify: `.claude/skills/onboard-local-model/scripts/tune-sweep.sh` (:95-104), `scripts/quant-headroom.sh` (:112-115, :139-145), `scripts/preflight.sh` (:126-130)
- Test: `tests/skills/onboard-local-model/test_onboard_local_model_scripts.py` (after `test_preflight_takes_the_ram_budget_from_llmenv` :2802)

**Interfaces:**
- Produces: `llmenv_budget_json CONFIG MODELS_DIR` in `tools/lib.sh`.
  - It prints the `llmenv budget` JSON and returns 0 whether the fit is feasible or not.
  - It returns 1, with `llmenv budget failed: <error>` or `llmenv budget printed no JSON object: <output>` on stderr, when there is no verdict.
  - It does not silence llmenv's own stderr; a caller adds `2>/dev/null`.
- Produces: preflight's `budget.llmenv_budget` is `null` whenever the helper returns 1.

- [ ] **Step 1: Write the failing tracked test**

```python
@pytest.mark.parametrize(("out", "rc", "code", "stderr_part"), [
    ('{"feasible": false, "available_mib": 1}', 1, 0, ""),
    ('{"error": "gpu.pci_address is not set"}', 1, 1, "llmenv budget failed: gpu.pci_address is not set"),
    ("", 1, 1, "llmenv budget printed no JSON object"),
])
def test_llmenv_budget_json_reads_a_verdict_whatever_its_exit_code(tmp_path: pathlib.Path, out: str, rc: int, code: int, stderr_part: str) -> None:
    stub = tmp_path / "bin"
    stub.mkdir()
    (stub / "uv").write_text('#!/usr/bin/bash\nprintf "%s" "$STUB_OUT"\nexit "$STUB_RC"\n')
    (stub / "uv").chmod(0o755)
    result = subprocess.run(
        ["/usr/bin/bash", "-c", 'source tools/lib.sh; llmenv_budget_json "$1" "$2"', "bash", str(tmp_path / "models.yml"), str(tmp_path)],
        cwd=ROOT, env=os.environ | {"PATH": f"{stub}:{os.environ['PATH']}", "STUB_OUT": out, "STUB_RC": str(rc)},
        text=True, capture_output=True, check=False,
    )
    assert result.returncode == code and stderr_part in result.stderr
    if code == 0:
        assert json.loads(result.stdout) == json.loads(out)
    else:
        assert result.stdout == ""
```

- [ ] **Step 2: Run red** (`tmp/r13/pytest-one.sh tests/test_shell.py -k llmenv_budget_json`). Expected: FAIL, `llmenv_budget_json: command not found`.
- [ ] **Step 3: Implement `llmenv_budget_json`.** Run `llmenv --config "$1" budget --models-dir "$2"` with `|| true`. Then require `jq -e 'type == "object"'`, then an empty `.error`. One comment line: an infeasible fit exits 1 but is still a verdict.
- [ ] **Step 4: Green, validate, commit.** Run the test, then `make validate && make test` (TMPDIR unset), then:

```bash
git add tools/lib.sh tests/test_shell.py
git commit -m "feat(lib): llmenv_budget_json reads a budget verdict whatever its exit code"
```

- [ ] **Step 5: Write the failing skill test**

```python
def test_preflight_reports_no_llmenv_budget_when_llmenv_budget_cannot_answer(tmp_path: Path) -> None:
    result = _run("bash", SCRIPTS / "preflight.sh", env=_preflight_env(tmp_path, None, '{"llm_server": {"memory_mib": 900}}'))
    assert result.returncode == 0, result.stderr
    budget = json.loads(result.stdout)["budget"]
    assert budget["llmenv_budget"] is None and budget["ram_budget_source"] == "llmenv resources"
```

  Run red. Expected: FAIL, an object of nulls.
- [ ] **Step 6: Switch the four callers.**
  - tune-sweep stage 1: `budget_json="$(llmenv_budget_json "$scratch/fit.yml" "$MODELS_DIR")" || exit 1`.
  - quant-headroom `cmd_check`: the same with `"$CONFIG_PATH" "$MODELS_DIR"`. Keep the `available_mib` check.
  - quant-headroom `budget_for`: `out="$(llmenv_budget_json "$sd/models.yml" "$sd/models" 2>/dev/null)" || return 0`, then the existing projection.
  - preflight: `if b="$(llmenv_budget_json "$cfg" "$MODELS_DIR" 2>/dev/null)"; then repo_budget="$(jq -c '{...}' <<<"$b")"; else repo_budget=null; fi`. Its comment at :126-127 says "null when llmenv budget gives no verdict (nothing enabled, a missing file)".
- [ ] **Step 7: Green.** Run the skill file through `tmp/r13/pytest-one.sh`, then `make validate`. Expected: PASS, including `test_tune_sweep_stops_on_a_budget_error` (rc 1, "not present").

### Task 6: Preflight's ceiling and `vram_feasible`, rule 1, the gateway-enabled eval

**Files:**
- Modify: `.claude/skills/onboard-local-model/scripts/preflight.sh` (:136-137, the projection from Task 5)
- Modify: `.claude/skills/onboard-local-model/SKILL.md` (:46-50, :89-90)
- Modify: `.claude/skills/onboard-local-model/references/tuning.md` (:19)
- Modify: `.claude/skills/onboard-local-model/evals/files/preflight-served.json`, `preflight-stopped.json`; Create: `evals/files/preflight-gateway-enabled.json`
- Modify: `.claude/skills/onboard-local-model/evals/evals.json` (eval `gateway-enabled-alias`)
- Test: `tests/skills/onboard-local-model/test_onboard_local_model_scripts.py` (:857, `test_eval_gateway_enabled_alias_fixtures_match_their_assertions` :2212, new preflight tests)

**Interfaces:**
- Consumes: Task 5's preflight block.
- Produces: `budget.llmenv_budget.vram_feasible`. `budget.vram_ceiling_mib` is the configured `gpu.vram_budget_ceiling_mib`, or the GPU total when that is 0 or missing.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.parametrize(("ceiling", "expected"), [(0, 16304), (15000, 15000)])
def test_preflight_treats_a_zero_ceiling_as_no_cap(tmp_path: Path, ceiling: int, expected: int) -> None:
    result = _run("bash", SCRIPTS / "preflight.sh", env=_preflight_env(tmp_path, None, None, gpu={"vram_budget_ceiling_mib": ceiling}))
    assert result.returncode == 0, result.stderr
    budget = json.loads(result.stdout)["budget"]
    assert budget["vram_ceiling_mib"] == expected and budget["model_vram_budget_mib"] == expected  # System One off: no reserve


def test_preflight_passes_vram_feasible_through(tmp_path: Path) -> None:
    verdict = '{"feasible": false, "vram_feasible": true, "available_mib": 9000, "required_mib": 8000, "ram_available_mib": 0, "ram_feasible": false}'
    budget = json.loads(_run("bash", SCRIPTS / "preflight.sh", env=_preflight_env(tmp_path, verdict)).stdout)["budget"]["llmenv_budget"]
    assert (budget["feasible"], budget["vram_feasible"], budget["ram_feasible"]) == (False, True, False)
```

  The pin at :857: `"never switch provider or model"` becomes `"any change of provider, harness or model after a failure"`.

  `test_eval_gateway_enabled_alias_fixtures_match_their_assertions` changes:
  - read `preflight-gateway-enabled.json`;
  - assert `llmenv_budget["vram_available_mib"] == 8321`, `llmenv_budget["ram_available_mib"] == 0` and `min(8321, model_vram_budget_mib) == 8321`;
  - in its tokens, `"10369"` becomes `"8321"`.

  Add `assert pre["budget"]["llmenv_budget"]["vram_feasible"] is True` to the latest-fit fixture test (:2142).
- [ ] **Step 2: Run red.** Expected: FAIL. The ceiling is 15488 instead of 16304, `vram_feasible` is absent, and the eval file does not exist yet.
- [ ] **Step 3: Implement the script.**
  - Ceiling: `ceiling="$(yq -r '.gpu.vram_budget_ceiling_mib // 0' "$cfg")"; [ "$ceiling" != 0 ] || ceiling="$total"`, with the comment "0 = no cap, as compute_budget (pylib/budget.py) and models.yml.example say". The `vram_budget_ceiling_pct` read goes.
  - The projection adds `vram_feasible` after `feasible`.
- [ ] **Step 4: Edit `SKILL.md`.** These replacements were measured on a scratch copy: 10,983 -> 10,987 bytes.
  - `(nothing enabled, as on a gateway-only host)` -> `(no verdict: nothing enabled or its file missing)`.
  - `A false\n   \`llmenv_budget.feasible\` means` -> `A false\n   \`vram_feasible\` there means`.
  - `(llm-server disabled: a\n   precondition, not a verdict)` -> `(llm-server disabled: a\n   precondition, like a false \`ram_feasible\` then, not a verdict)`.
  - The bypass paragraph's ` after\na failure: never switch provider or model (AGENTS.md retry ladder).` -> ` after\na failure (AGENTS.md retry ladder).`.

  Check: `wc -c < .claude/skills/onboard-local-model/SKILL.md` prints `10987`.
- [ ] **Step 5: tuning.md T1.** `a false \`llmenv_budget.feasible\`` -> `a false \`llmenv_budget.vram_feasible\``.
- [ ] **Step 6: Fixtures and eval.**
  - Add `"vram_feasible": true` to `llmenv_budget` in `preflight-served.json` and `preflight-stopped.json`.
  - `preflight-gateway-enabled.json` = `preflight-gateway.json` with:
    - `"ram_budget_source": "llmenv budget"`;
    - `"llmenv_budget": {"feasible": true, "vram_feasible": true, "vram_available_mib": 8321, "vram_required_mib": 7300, "vram_shortfall_mib": 0, "ram_available_mib": 0, "ram_feasible": true, "ram_required_mib": 0, "remedies": []}`.
  - Eval `gateway-enabled-alias`:
    - `files` and `setup` name `preflight-gateway-enabled.json`.
    - `expected_output` says "sized against llmenv_budget.vram_available_mib 8321".
    - Its third assertion becomes: "The budget is llmenv_budget.vram_available_mib 8321, the smaller of it and model_vram_budget_mib 10369 (llmenv budget gives a verdict for an enabled entry whose file is present, even with llm-server disabled), and the RAM is ram.available_mib 14500 because ram_offload_budget_mib is 0, both said".
- [ ] **Step 7: Green.** Run the skill file through `tmp/r13/pytest-one.sh`, then `make validate`. Expected: PASS, including `test_shared_rules_are_stated_once` and the byte cap.

### Task 7: The runner cap comes from `llmenv agent-runtime-cap`

**Files:**
- Modify: `llmenv.py` (a `cmd_agent_runtime_cap` near `cmd_run_agent_bounded` :856; its parser next to `run-agent-bounded` :1090), tracked
- Test: `tests/test_cli.py`, tracked
- Modify: `.claude/skills/onboard-local-model/scripts/effort-run.sh` (header :12-13, :75-76)
- Test: `tests/skills/onboard-local-model/test_onboard_local_model_scripts.py` (:24, `_stub_bin` :457-478)

**Interfaces:**
- Produces: `uv run llmenv.py agent-runtime-cap` prints `{"runtime_seconds": 300}` (`int(DEFAULT_RUNTIME_SECONDS)`) and exits 0.

- [ ] **Step 1: Write the failing tracked test**

```python
def test_agent_runtime_cap_prints_the_bounded_runner_default() -> None:
    from pylib.agent_runner import DEFAULT_RUNTIME_SECONDS
    result = run("agent-runtime-cap")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"runtime_seconds": int(DEFAULT_RUNTIME_SECONDS)}
```

- [ ] **Step 2: Run red.** Expected: FAIL, argparse "invalid choice".
- [ ] **Step 3: Implement** `cmd_agent_runtime_cap(args: argparse.Namespace) -> int` returning `emit({"runtime_seconds": int(DEFAULT_RUNTIME_SECONDS)})`, plus `sub.add_parser("agent-runtime-cap")` with that `func`.
- [ ] **Step 4: Green, validate, commit.** `make validate && make test` (TMPDIR unset), then:

```bash
git add llmenv.py tests/test_cli.py
git commit -m "feat(cli): agent-runtime-cap prints the bounded runner's runtime cap"
```

- [ ] **Step 5: Switch the skill.**
  - effort-run.sh :75 becomes `cap="$(llmenv agent-runtime-cap | jq -r .runtime_seconds)"`. Keep the integer check; its message becomes "cannot read the bounded runner's cap from llmenv agent-runtime-cap". The header names the command instead of the Python file.
  - Test file :24: after the `skill_helpers` imports, `sys.path.insert(0, str(ROOT))` (the repo's pattern, as in `tests/test_budget.py`). Then `from pylib.agent_runner import DEFAULT_RUNTIME_SECONDS` and `RUNNER_CAP = int(DEFAULT_RUNTIME_SECONDS)`.
  - `_stub_bin`'s `uv` body begins with `case "$*" in *agent-runtime-cap*) echo '{"runtime_seconds": <RUNNER_CAP>}'; exit 0 ;; esac`, as an f-string. That answers the new call before the stub's agent line could write `src/agent_added.js` into the cwd.
- [ ] **Step 6: Green.** Run the skill file through `tmp/r13/pytest-one.sh`, then `make validate`. Expected: PASS (`test_effort_run_defaults_to_the_runner_cap_and_refuses_a_larger_timeout` unchanged).

### Task 8: A gateway-only host without GPU config (native high)

**Files:**
- Modify: `.claude/skills/onboard-local-model/scripts/preflight.sh` (after :16), `scripts/bench-fit.sh` (header :14, after :44, :53)
- Modify: `.claude/skills/onboard-local-model/references/onboard-flow.md` (progress line 6 :92, step 1 :97-106, Where things live :274-276)
- Modify: `.claude/skills/onboard-local-model/references/tuning.md` (T1 :19-21, T2 :22)
- Modify: `.claude/skills/onboard-local-model/evals/files/models.yml`, `models-local-url.yml`, `models-subfolder.yml`, `models-gateway-enabled.yml`, `models-gateway.yml` (`gpu` block)
- Modify: `.claude/skills/onboard-local-model/evals/evals.json` (evals `gateway-only-host`, `hf-repo-gateway-only`)
- Test: `tests/skills/onboard-local-model/test_onboard_local_model_scripts.py` (`_bench_fit` :258, :940-944, :2212-2216, :2227, :2346-2364, new tests)

**Interfaces:**
- Produces:
  - `preflight.sh` with an empty or missing `gpu.pci_address` exits 2 with `gpu.pci_address is empty`, before `llmenv detect`.
  - `bench-fit.sh` with an empty or missing `gpu.device_name` exits 2 with `gpu.device_name is empty`, before any `podman run` (no `devices.txt`).
  - Both messages name `make llama-enable MODEL=<alias>`, then `make setup`.

- [ ] **Step 1: Write the failing script tests.**
  - `_bench_fit` gains `device: str = "d"`, written into its config.

```python
def test_preflight_names_an_empty_pci_address(tmp_path: Path) -> None:
    result = _run("bash", SCRIPTS / "preflight.sh", env=_preflight_env(tmp_path, None, None, gpu={"pci_address": ""}))
    assert result.returncode == 2 and "gpu.pci_address is empty" in result.stderr and "make setup" in result.stderr


def test_bench_fit_names_an_empty_device_before_any_container(tmp_path: Path) -> None:
    result, calls, _ = _bench_fit(tmp_path, "{enabled: false}", device="")
    assert result.returncode == 2 and "gpu.device_name is empty" in result.stderr
    assert not (_out(tmp_path, "out") / "devices.txt").exists() and not calls.exists()
```

- [ ] **Step 2: Write the failing doc and fixture tests**

```python
def test_onboarding_and_tuning_branch_on_a_host_without_gpu_config() -> None:
    flow = _md(REFS / "onboard-flow.md")
    step1 = _section(flow, "1. **Preflight (read-only).**", "2. **Baseline.**")
    assert all(s in step1 for s in ("`gpu.pci_address is empty`", "and stop; nothing changed", ".gpu.device_name", "before step 6",
                                    "L=~/.config/llm-env/models.yml yq -i '.gpu = load(strenv(L)).gpu' $S", "as a declined G3 does"))
    assert "still works in a throwaway container" not in flow and "only when `gpu.device_name` is set" in flow
    tuning = _md(REFS / "tuning.md")
    assert "`gpu.pci_address is empty`" in _section(tuning, "- T1.", "- T2.")
    assert "LLM_ENV_ASSUME_YES=1 make setup" in _section(tuning, "- T2.", "- T3.")
```

  Fixture test changes:
  - `test_eval_gateway_host_fixtures_match_their_assertions`: assert `cfg["gpu"]["pci_address"] == pre["gpu"]["pci"]`, `cfg["gpu"]["device_name"] == ""`, and that `"gpu.device_name is empty"` is in `_asserts("gateway-only-host")`.
  - `test_eval_gateway_hf_fixtures_match_their_assertions`: assert `"before step 6"` is in `_asserts("hf-repo-gateway-only")`.
  - `test_eval_gateway_enabled_alias_fixtures_match_their_assertions`: the base also gets `base["gpu"]["device_name"] = SERVED_DEVICE`. The new module constant `SERVED_DEVICE = "AMD Radeon RX 9070 XT (RADV GFX1201)"` sits beside `FIX_ENV`.
  - A new assertion in `test_eval_alias_fixtures_match_their_assertions`: `yaml.safe_load((FIX / "models.yml").read_text())["gpu"]["device_name"] == SERVED_DEVICE`.
- [ ] **Step 3: Run red.** Expected: FAIL on the messages, the doc text and the fixture fields.
- [ ] **Step 4: Implement the scripts.**
  - preflight :16 reads `yq -r '.gpu.pci_address // ""'`, then `[ -n "$pci" ] || { echo "gpu.pci_address is empty: make setup has not detected a GPU (a gateway-only setup skips it; make llama-enable MODEL=<alias>, then make setup)" >&2; exit 2; }`.
  - bench-fit, after :44: `dev_name="$(yq -r '.gpu.device_name // ""' "$CONFIG_PATH")"`, guarded the same way with "gpu.device_name is empty: make setup has not picked a Vulkan device (a gateway-only setup skips it; make llama-enable MODEL=<alias>, then make setup)". :53 uses `"$dev_name"`. The header's exit line adds the empty device.
- [ ] **Step 5: onboard-flow step 1.** After the G-R1 bullet (:101-103), insert this bullet verbatim:

```markdown
   - GPU not configured (a gateway-only `make setup` skips GPU detection and the Vulkan device):
     - `preflight.sh` exits 2 with `gpu.pci_address is empty`: no budget can be sized. Report that local inference must be set
       up first (`make llama-enable MODEL=<alias>`, then `LLM_ENV_ASSUME_YES=1 make setup`, which downloads that alias's file
       when it is missing) and stop; nothing changed.
     - `yq '.gpu.device_name' ~/.config/llm-env/models.yml` prints an empty string (System One on: setup found the GPU but no
       Vulkan device): `bench-fit.sh` cannot screen yet. Go on; after step 5, Gate G3 and 7a.1-7a.4 run before step 6
       instead of after it, then `L=~/.config/llm-env/models.yml yq -i '.gpu = load(strenv(L)).gpu' $S` gives `$S` the device,
       and 7a resumes at 7a.5. Step 2 still finds no baseline. A no to that G3 ends the run as a declined G3 does (step 9).
```

  Progress line 6 becomes `- 6 (b) baseline screen, then candidate screen (no \`gpu.device_name\`: G3 first, step 1)`.

  "Where things live" (:274-276) becomes:
  "`llm_server.enabled: false` means a gateway-only host: no local llama.cpp runs. Screening (`bench-fit.sh`) runs in a throwaway container only when `gpu.device_name` is set (a host that went gateway-only through `make llama-disable` keeps it); a gateway-only `make setup` leaves it empty (step 1). Serving needs it `true`, a GPU opt-in that only `make llama-enable MODEL=<alias>` / `make llama-disable` flip, after G3; `make setup` then re-detects the GPU."
- [ ] **Step 6: tuning.md.**
  - T1 appends: "Preflight exits 2 with `gpu.pci_address is empty`, or `gpu.device_name` is empty in the live config (a gateway-only `make setup` skipped them): run T2 first, then T1 again."
  - T2 becomes: "T2. Only if `llm_server.enabled` is false (gate G-T1; its facts name the download below): `make llama-enable MODEL=<alias>`, then `LLM_ENV_ASSUME_YES=1 make setup` (AGENTS.md: it detects the GPU and the Vulkan device a gateway-only setup skipped, and downloads the entry's file when it is missing)."
- [ ] **Step 7: Fixtures.** Use `yq -i`, so Task 1's comment-free files stay comment-free.
  - `models.yml`, `models-local-url.yml`, `models-subfolder.yml`, `models-gateway-enabled.yml`: `gpu.pci_address "0000:03:00.0"`, `gpu.device_name "AMD Radeon RX 9070 XT (RADV GFX1201)"`, `gpu.vram_total_mib 16305`, `gpu.vram_budget_ceiling_mib 15489`.
  - `models-gateway.yml`: the same, with `gpu.device_name ""` (System One on, so Step 2 ran and Step 6 did not).
  - Check the `$S` command on copies: `models.yml` as L, `models-gateway.yml` as S, under `tmp/r13/`. After it, `yq '.gpu.device_name'` of S prints the device. Remove the copies.
- [ ] **Step 8: Evals.**
  - `gateway-only-host`:
    - `expected_output` says nothing can run yet, because `gpu.device_name` is empty.
    - Its third assertion becomes: "The answer says nothing can run yet: gpu.device_name is empty in the fixture config (a gateway-only setup skipped the Vulkan device), so bench-fit.sh screening also waits for make llama-enable MODEL=<alias> and LLM_ENV_ASSUME_YES=1 make setup after the user approves".
  - `hf-repo-gateway-only`:
    - Its G3 assertion appends: ", and G3 runs before step 6's screen because gpu.device_name is empty; the scratch config then takes the live gpu block".
    - `expected_output` says the same in one clause.
- [ ] **Step 9: Green.** Run the three skill files through `tmp/r13/pytest-one.sh`, then `make validate`. Expected: PASS, including `test_skill_prose_has_no_long_shell_logic` and `test_routes_have_progress_checklists`.

### Task 9: Alias reuse needs an equal size

**Files:**
- Modify: `.claude/skills/onboard-local-model/references/onboard-flow.md` (A1 :21, H5 :68, 7a.2 :182-184)
- Modify: `.claude/skills/onboard-local-model/references/local-gguf.md` (L1 :22-28)
- Test: `tests/skills/onboard-local-model/test_onboard_local_model_scripts.py` (:1143, new test)

- [ ] **Step 1: Write the failing test.** Delete the H5 pin at :1143: the new test pins the new wording. The `"Under A1"` pin at :1244 still matches.

```python
def test_alias_reuse_needs_the_same_size() -> None:
    raw = (REFS / "onboard-flow.md").read_text()
    a1 = " ".join(raw.split("- A1. ", 1)[1].split("- A2. ", 1)[0].split())
    assert all(s in a1 for s in ("`size_bytes` equal to the candidate's", "`pick.bytes`", "report both sizes and stop before G1"))
    flow = _md(REFS / "onboard-flow.md")
    assert "Under A1 (a live entry other than the baseline has this `file` and size)" in _section(flow, "H5. ", "### Scout pick")
    assert "(A1 checked its size)" in _section(flow, "**7a. Apply and serve", "**7b.")
    l1 = _section(_md(REFS / "local-gguf.md"), "L1. ", "L2. ")
    assert "its file is not in the models directory" in l1 and "before any copy" in l1
```

- [ ] **Step 2: Run red.** Expected: FAIL.
- [ ] **Step 3: Edit.**
  - A1: "A live entry has the same `file` and a `size_bytes` equal to the candidate's (`pick.bytes`; a local GGUF: its byte size): keep its alias. One GGUF never gets a second entry. The same `file` with another size is a different file under that name: report both sizes and stop before G1 (the models directory holds one file per name)."
  - H5: "Under A1 (a live entry other than the baseline has this `file` and size) add nothing: that alias is the candidate."
  - 7a.2: "...: use its alias (A1 checked its size), never a duplicate."
  - L1, a new second bullet: "A live entry has this `file` but its file is not in the models directory: A1's size check (its `size_bytes` against this file's byte size) decides now, before any copy."
- [ ] **Step 4: Green.** Run the main skill file through `tmp/r13/pytest-one.sh`. Expected: PASS.

### Task 10: The local GGUF has one run order

**Files:**
- Modify: `.claude/skills/onboard-local-model/references/local-gguf.md` (:3-4, run order :11-20, L3 :32-34, L5 :40)
- Modify: `.claude/skills/onboard-local-model/references/onboard-flow.md` (:84, delete :131-132)
- Test: `tests/skills/onboard-local-model/test_onboard_local_model_scripts.py` (:1117, :1222-1223, :1229, :1245)

**Interfaces:**
- Consumes: Task 8's step-1 branch (run order item 8 names it).

- [ ] **Step 1: Update the pins first.**
  - :1117 asserts `"4.4 ran first" not in step4`.
  - The :1222 marks become `("L1 name check", "L2 scratch config", "Steps 1 to 4.2", "L3 fit", "L4 copy", "Step 5 (", "L5 live entry", "Step 6 (", "Steps 7a to 8", "Step 9 with L6")`.
  - The :1229 tokens become `("Fit, before any copy", "right after Steps 4.2", "nothing is copied")`.
  - :1245 asserts `"A local GGUF runs these Steps in the run order of \`references/local-gguf.md\`." in flow and "L items replace" not in flow`.
  - Add `assert not [s for s in ("in place of", "replaces Steps", "L items replace") if s in local]`.
- [ ] **Step 2: Run red.** Expected: FAIL.
- [ ] **Step 3: Edit local-gguf.md.**
  - :3-4: "... before the Steps of `references/onboard-flow.md` (`SKILL.md` Route). The run order below is the whole run: a Step it does not list (4.3, 4.4) is not run."
  - The run order, verbatim:

```markdown
Run order (copy it into your task list; top to bottom, one line at a time):
1. L1 name check
2. L2 scratch config
3. Steps 1 to 4.2 (preflight, baseline, candidate, settings into `$S`)
4. L3 fit (Steps 4.4's checks, on the file's own directory)
5. L4 copy (Gate G1)
6. Step 5 (baseline lane, G2)
7. L5 live entry: decide every key now, because Gate G3 shows it as its diff
8. Step 6 (screens; when step 1 moved Gate G3 before step 6, 7a.1-7a.4 run first and write L5's entry at 7a.2)
9. Steps 7a to 8 (L5's entry is the one 7a.2 writes)
10. Step 9 with L6
```

  - L3: "**Fit, before any copy**, right after Steps 4.2 wrote the settings into `$S`: ..." The rest stays, ending "report it as Steps 4.4 says and stop; nothing is copied."
  - L5: "decided before G3 (run order 7) and written at 7a.2".
- [ ] **Step 4: Edit onboard-flow.md.** :84 becomes "A local GGUF runs these Steps in the run order of `references/local-gguf.md`." Delete the 4.3 bullet "A local GGUF: 4.4 ran first, ..." (:131-132).
- [ ] **Step 5: Green.** Run the main skill file through `tmp/r13/pytest-one.sh`. Expected: PASS, including `test_references_cite_each_other_only_by_path_and_only_on_their_route`.

### Task 11: Verification and cleanup

- [ ] **Step 1: Guard and caps.**
  - `bash tmp/r13/pytest-one.sh tests/skills/onboard-local-model/test_onboard_local_model_scripts.py -k "jev_as_a_black_box or stated_once or router_that_loads"` passes.
  - `wc -c < .claude/skills/onboard-local-model/SKILL.md` prints `10987`.
  - `grep -rn "ai-kit" .claude/skills/onboard-local-model` prints nothing.
- [ ] **Step 2: Skill tests, one file per run.** Run `test_onboard_local_model_scripts.py`, `test_challenges.py` and `test_latency_profile.py` through `tmp/r13/pytest-one.sh`. Expected: all pass (Task 0's baseline minus 1, plus the new tests).
- [ ] **Step 3: Repo gates.** `make validate && make test` with TMPDIR unset. Expected: pass. `git log -2 --format=%B` shows the two commits of Tasks 5 and 7, without attribution lines.
- [ ] **Step 4: Route walk, read as a new agent.** Each walk ends at a named next step, with no dead end:
  - **(a) Gateway-only host without GPU config.**
    - A scratch copy of `models-gateway.yml` with `gpu.pci_address ""` and `system_one.enabled false`. `LLM_ENV_CONFIG=<copy> bash $SK/scripts/preflight.sh` exits 2 with `gpu.pci_address is empty`. Step 1 says report and stop; T1 says T2 first.
    - With `models-gateway.yml` (device empty): step 1 leads to G3 after step 5, then the `$S` copy, then step 6, then 7a.5. The local GGUF run order item 8 says the same. Step 9's declined-G3 bullet restores the rollback copy.
  - **(b) Enabled-but-stopped onboarding** (`preflight-stopped.json`): step 1 leads to scoring's "Serving the lane's model" (G-R1), and step 9 hands it back. Unchanged by this plan; the `onboard-baseline-server-stopped` eval test passes.
  - **(c) A fake agent calling `process.exit(0)`:** `test_effort_run_never_scores_a_solution_that_exits_at_load_as_solved` passes. Feeding its row to `score.sh` gives `solved` `0/1`.
  - **(d) The gateway-enabled alias:** budget 8321, RAM 14500, no `make stop-llama`, G3 runs `make llama-enable MODEL=gemma4`.
  - **(e) A same-name, other-size live entry:** A1 stops before G1, and L1 stops before the copy.
- [ ] **Step 5: Leftovers.**
  - `rm -rf tmp/r13`.
  - `ls -d /var/tmp/pytest-r13.* 2>/dev/null` prints nothing.
  - `podman ps -a` lists only the containers that were there before (this plan starts none).
  - `git status --short` shows only `tools/lib.sh`, `tests/test_shell.py`, `llmenv.py` and `tests/test_cli.py` as committed, plus the untracked skill tree and plan files.

## Round-1 review outcome (approved, 1 confirmed high)

Review round 1 (native lane applied Tasks 0-10 to a work copy and ran them; referee lane lead-verified) left one CONFIRMED high
and six mediums; every referee critical and high was refuted or downgraded (tmp/plan-o-review/review-round-1.md). Plan status:
approved for execution. The implementer must apply:

- H1 (Task 2): the new test `test_effort_run_never_scores_a_solution_that_exits_at_load_as_solved` fails pyright
  (`row["pass"] < row["total"]` on `object` types), which blocks `make validate` before the Task 5 and Task 7 commits. Add
  `assert isinstance(row["pass"], int) and isinstance(row["total"], int)` before the comparison, as
  `test_effort_run_scores_a_hanging_solution_as_failed` does.
- M1 (Task 6): `preflight-gateway-enabled.json` keeps `runtime.enabled_models` equal to what preflight prints for
  `models-gateway-enabled.yml`: `["gemma4"]`. The gateway-enabled fixture test asserts it.
- M2 (all tasks): preflight.sh has 68 lines; `:126-130`, `:129` and `:136-137` do not exist. The budget projection and its
  comment are at `:21-25`, the ceiling at `:31-32`. Re-derive every line target in this plan against the current files
  before editing; locate edits by content, not by number.
- M3 (Task 7 Step 5): add `import sys` to the skill test file before `sys.path.insert(0, str(ROOT))`.
- M4 (Task 8 Step 8): the `gateway-only-host` eval's `expected_output` is replaced verbatim with: "Detects that
  llm_server.enabled is false (gateway-only) and that gpu.device_name is empty (a gateway-only make setup skipped the Vulkan
  device), so nothing can run yet: neither bench-fit.sh screening nor effort runs. Asks before enabling local inference via
  make llama-enable MODEL=<alias> and LLM_ENV_ASSUME_YES=1 make setup, and before editing the live config." The old claim that
  throughput works now in a throwaway container is removed everywhere (grep evals and references; the bench-fit.sh header
  describing its own container stays): the `gateway-only-host` assertion "The answer separates what works now (bench-fit.sh
  throughput in a throwaway container) from what needs the stack" is the third assertion Step 8 replaces. The gateway host
  fixture test asserts `"throughput works through a throwaway container" not in` that eval's text (expected_output and
  assertions).
- M5 (Task 2 Step 4): the `shortcut:` comment above the `total` floor also says that a failing test file with exactly one
  test can still be cheated (a solution that exits 0 only in that file's process counts that file as 1 pass); the root fix
  is in the challenge gate and changes the generator id.
- M6 (Task 8 Step 6): tuning T1's branch must not dead-end when `llm_server.enabled` is already true but
  `gpu.device_name` (or `gpu.pci_address`) is empty: then run `LLM_ENV_ASSUME_YES=1 make setup` directly (gate G-T1),
  without T2's `make llama-enable`; otherwise run T2 first. T2 stays "only if `llm_server.enabled` is false".
- L1 (Task 1 Step 5, Task 11 Step 2): the merged alias-typo test is one parametrized test with two cases, so the collected
  test count does not change in Task 1. Task 11's expected count is Task 0's baseline plus the new tests (no minus 1).
- L2 (Task 4 Step 4): after the header rewrite, `grep -n 131072 scripts/tune-pick.sh` prints nothing.
- L7 (Task 11 Step 5): check the commits with `git log -2 --stat`, not `git status --short`.
