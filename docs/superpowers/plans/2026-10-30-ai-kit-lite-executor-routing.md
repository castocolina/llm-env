# ai-kit Lite Executor Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let ai-kit-spec-execute (and its superpowers and GSD adapters) pick the executing model between a capacity-carded lite combo (`router-env/my-coding-lite`), the strong roster (`router-env/my-coding`) or a user-named model, with `jev-fast-decision` as an advisory judge; and give ai-kit-spec-review an `EXECUTOR_PROFILE=lite|strong` switch that makes plans fit a lite executor.

**Architecture:** One new stdlib module `ai_kit_spec/executor_choice.py` (in the shared ai-kit-spec-review package both adapters already import) reads a measured capacity card, applies the precedence explicit > capacity gate > jev > rule, and turns the choice into either a narrowed candidate list or a `prefer_key` that re-ranks only the quota walk; `ladder_keys` (the Rounds 4-5 capability order) always lists capacity-carded rows last. Both adapters' resolve commands gain `--executor-key` and call it; the review side is text only (one lite-rules reference used by the checklist and by the writing-plans revise prompt).

**Tech Stack:** Python 3.12+ stdlib (unittest, subprocess, json, re), Markdown skills (Agent Skills standard), ai-kit's make targets.

**Spec:** the owner goal restated in **Decisions** below, plus the research notes in llm-env `tmp/goal/ai-kit-design.md` (ephemeral; every fact this plan uses from it was re-verified against ai-kit `main` at `252316e` on 2026-10-30 and is restated here). The capacity card is produced by llm-env `docs/superpowers/plans/2026-10-30-local-combo-and-capacity.md` ("Capacity card schema (schema 1)" table and its Decisions 2, 3 and 7); this plan only reads the card and never depends on that plan being done. Round-1 review findings and their dispositions: **Review responses** below.

## Global Constraints

- Code repo: `AIKIT=/var/home/bazzite/git/personal/ai-kit`. ALL edits happen in the worktree `WT="$AIKIT/.claude/worktrees/lite-executor-routing"` on branch `feat/lite-executor-routing` (Task 1). Never edit, stage or commit in `$AIKIT` itself: another agent may be working there. Every command below runs with working directory `$WT`.
- The plan ends with the branch left for the owner: no `git push`, no merge, no `--force`, no history rewrite, no `git worktree remove`.
- ai-kit's GSD-first rule (its `AGENTS.md` "Required Start" #2) is waived for this work by Decision D11; Required Start #1 (read `.planning/STATE.md`) and #3 (live-verify) still apply (Task 1).
- ai-kit rules (its `AGENTS.md`): English only in every written artifact; runtime code is stdlib-only (no third-party import); never hardcode an absolute machine path in source, tests or assets (tests use `tempfile` and synthetic content, never real config); commit code, tests and docs together; let pre-commit hooks run, never `--no-verify`; preserve unrelated work, no `git reset --hard`/`git clean -f`/`git checkout .`.
- Lint limits (`pyproject.toml`): ruff line-length 100; pylint `max-args=5`, `max-positional-arguments=5`, `max-locals=15`, `max-branches=12`, `max-returns=6`, `max-statements=50`. Use `NamedTuple` inputs and small private helpers to stay inside them. `_resolve_task_inputs` (superpowers `cli.py:211`) and `_cmd_resolve_dispatch` (GSD `cli.py:156`) already hold 15 locals: add none there.
- Tests are `unittest`. A new `tests/test_*.py` joins the `unit` group automatically, but the generated listings in `Makefile` and `.pre-commit-config.yaml` must be refreshed with `python3 tools/gen_test_groups.py` (`tests/test_gate_scope.py` fails while stale). Run focused tests with `python3 -m unittest tests.<module> -v` from `$WT`.
- Default behavior is unchanged: with no `EXECUTOR_KEY` and no roster row carrying `capacity_card`, `resolve-injection` and `resolve-dispatch` print byte-identical JSON and never call jev; with `EXECUTOR_PROFILE` absent or `strong`, every review prompt is byte-identical to today.
- jev is advisory only, never a gate; explicit selection always wins; an unknown (`null`/missing/malformed/stale) capacity value never counts as "fits".
- Skill-judge loop (SJL), used by Task 1 (baseline) and Tasks 8-12, bar per Decision D9: a round PASSES when (no `critical`/`high` finding OR `percent >= 80.0`) AND `percent >= baseline percent`. The judge is the rubric v4 skill-judge at `SKILL_JUDGE_DIR=/var/home/bazzite/git/github/agent-toolkit/skills/skill-judge` (125 points, one-line verdict JSON). SJL for skill `NAME`, round `R` (`baseline`, `r1`, `r2`, `r3`):
  1. Dispatch a fresh-context subagent (Claude Code: `Agent`, `general-purpose`) with exactly: `Load the skill-judge skill (its SKILL.md is at <SKILL_JUDGE_DIR>/SKILL.md) and follow it exactly. Judge the skill directory <WT>/skills/<NAME>. Root for finding paths: <WT>. Print only the verdict JSON line.` Save the reply line to `tmp/skill-judge/<NAME>-<R>.json`.
  2. Run `python3 tmp/skill-judge/gate.py tmp/skill-judge/<NAME>-baseline.json tmp/skill-judge/<NAME>-<R>.json` (script from Task 1). Expected: a line starting `PASS` (exit 0) or `FAIL` (exit 1).
  3. On `FAIL`: apply the verdict's `fix` for each high/critical finding, then the cheapest medium ones, editing only `skills/<NAME>/`; run `make test-unit` (text pins must stay green; never delete a pinned sentence to gain points); start the next round. After `r3` still `FAIL`: STOP and report the four verdict files to the owner.

## Review Focus

1. A `capacity_card` path that is a directory, unreadable, or holds non-JSON or a JSON list: the card must read as all-unknown and the gate must pick strong, never crash or pick lite (test in Task 2).
2. A stale or hostile card: a schema 1 section written under another config (its `fingerprint` differs from the card's, or is missing), a `capacity show` snapshot listing it in `stale_sections`, or wrongly typed values (`"8192"`, `true`, `"L9"`, `"yes"`): each value is unknown, never coerced, and an all-unknown card carries a problem text (tests in Task 2).
3. The jev client hangs, crashes, prints non-JSON, answers `ok` with an id missing or a non-numeric `noul`, or the parent shell exports `GSD_PATH_JEV=0`, `JEV_ALLOW_REMOTE=1` or `JEV_MIN_CONFIDENCE=0.1`: the choice falls back to the rule with no exception, and the child environment never carries the parent's jev control variables (tests in Tasks 2 and 3).
4. An explicit key that is ambiguous (a model shared by two rows), names a `purpose = "review"` row, or is excluded by a quota or escalation exclusion: the first two exit 2 naming the rows, the third ends `quota_exhausted`; another model never runs (tests in Tasks 2, 4 and 5).
5. A lite row with a known `context_limit`, or listed first in `policy.ladder`: `ladder_keys` still lists it last, so Rounds 4-5 escalation from a stuck lite row finds the strong rows and escalation from a strong row never lands on lite (tests in Task 4).

## Decisions

- **D1 Lite row.** The lite candidate is the first roster row with a non-empty `capacity_card` string, and the choice is active only when at least one other row has none (rows excluded for this call do not count). `capacity_card` is a flat string (a card file path, `~` expanded, relative to `--cwd`, or inline JSON object text starting with `{`) because the TOML writer rejects nested tables (`config_io.validate_reviewer_fields`, `config_io.py:518-525`); flat strings already survive load and re-save (`_cfg_render_reviewer_lines`, `config_io.py:582-589`). No wizard change.
- **D2 Card shape and staleness.** The adapter normalizes any card to five internal keys: `max_context` (int > 0), `tokens_per_s` (number > 0), `max_layer` (`"L1"`..`"L5"`), `tool_calling_reliable` (bool), `measured` (`YYYY-MM-DD`); other keys are ignored. It reads two forms. (a) llm-env schema 1 (`"schema": 1`, nested, the real producer): `max_context` ← `usable.context_tokens`; `tokens_per_s` ← `benchmark.tg_tps`; `max_layer` ← `ladder.highest_layer` (int 1-5 or `"L1"`..`"L5"`); `tool_calling_reliable` ← `ladder.tool_error_rate <= TOOL_ERROR_RATE_MAX` (0.2; that rate also counts legitimate command failures, llm-env Decision 7); `measured` ← first 10 chars of `written_at`. Staleness is derived only from fields the card file holds: the `ladder` values count only when `ladder.fingerprint` is a non-empty string equal to the card's top-level `fingerprint` (llm-env Decision 3; `write_card` refreshes the top-level `fingerprint` and recomputes `usable` on every write, so `usable` always matches it). `stale_sections` exists only in `llmenv capacity show` output, so it is honoured when present (a snapshot) and never required. Known limit: a model config change after the last card write is invisible in the file; the remedy is to re-run `make model-capacity MODEL=<alias>` (documented in `executor-choice.md`). No age rule: a card's age says nothing about a config change. (b) A flat hand-written object with the five internal keys (handy as inline JSON). Any other `schema` value is all-unknown with a problem. Anything missing, `null` or mistyped is `None` = unknown; a parsed card with no valid value at all carries the problem `capacity card has no usable value`.
- **D3 Precedence.** (1) explicit `--executor-key` (matched against a roster key, else a unique roster `model`, over the whole roster including excluded rows; a row whose `purpose` does not match `execute` is refused) narrows the walk to that key, jev never asked; (2) capacity gate, always run before jev and also when jev is unavailable, picks strong when the task text is blank, `tool_calling_reliable is not True` (every implementer dispatch edits files and runs commands), `max_context` is unknown, or `required_context > 0.6 * max_context`; (3) jev; (4) rule: lite only if `1 <= steps <= 5`, files known and `<= 2`, a step mentions a test, at least one fenced code block, no risk word (`auth*`, `migrat*`, `schema*`, `delet*`, `concurren*`), and `max_layer >= L2`; otherwise strong. Files are counted from the task's **Files:** block lines only (up to its first blank line or step line; distinct backtick paths, line qualifiers stripped); no block or no path = unknown.
- **D4 jev.** Two items: noul `lite_ok` and score `difficulty` (`trivial`, `routine`, `needs reasoning`, `risky`). Lite iff `lite_ok` is confident (`needs_fallback` false) with `noul >= 0.7` and `difficulty` is confident with level `"0"` or `"1"`; strong iff confident `noul <= 0.3` or confident level `"2"`/`"3"`; anything else (needs_fallback, disabled, unavailable, garbage, a missing item) falls to the rule. These are the client's own fields (`jev_review.py` `main` 2034, `_result` 1824-1825, `_finalize` 1577, `_check_score_answer` 445-446). The adapter's Python runs the sibling client `jev-fast-decision/scripts/jev_review.py` (override `AI_KIT_JEV_CLIENT`; refused unless its text has the line `CLIENT_PROTOCOL = 2`, the `decide.py` precedent). The child environment follows `decide.py child_env` (`decide.py:35-36`, `400-413`): `JEV_BACKEND`, `JEV_ALLOW_REMOTE`, `JEV_MIN_CONFIDENCE` and every `GSD_PATH_JEV*` variable are removed (a parent shell must never widen the gate, and the 0.7/0.3 cut-offs assume the rows' own thresholds); then `GSD_PATH_JEV=1`, `GSD_PATH_JEV_TIMEOUT_SECONDS=20`, `GSD_PATH_JEV_DEADLINE_SECONDS=20*(items+1)` and `JEV_CALLER=ai-kit-spec-execute` are set; key and URL variables pass through. Remote rungs (TypeSafe, OpenCode Zen) therefore run only with the user's recorded consent (`jev_review.py consent grant <backend>`), which the client reads itself; without it jev stays on local Laya. The client lookup is narrower than `decide.py`'s (override, then the sibling directory ai-kit's installer always creates); a missing client is `no_client`, which falls to the rule.
- **D5 Applying the choice.** Explicit: candidates narrowed to that key; if it is excluded or unavailable the existing `quota_exhausted`/`fallback_notice` stop applies, never another model. Lite: `prefer_key` ranks the lite row first for the quota walk only, ahead of the unknown-context rank (a lite pick has passed the measured gate `required_context <= 0.6 * max_context`, stronger evidence than an unset `context_limit`); routes and quota still apply, so an unavailable lite row falls through to strong rows. Strong: lite rows are dropped from the walk. `compute_ladder_keys` never sees `prefer_key`, and whenever the roster has a capacity-carded row `ladder_keys` lists it last (`lite_last`), whatever its `context_limit`, its `policy.ladder` position, the pick or this call's exclusions: escalation from a stuck lite row finds the strong rows, escalation from a strong row never lands on lite, and a Rounds 4-5 re-run that excludes the lite row turns the choice off (D1).
- **D6 Outputs.** When the choice is active, one stderr line `ai-kit-spec: executor <pick> (<source>): <reason>` and a JSON field `executor_choice = {pick, source, reason, key}` (superpowers: on `native`/`external_cli` results only, since its `quota_exhausted`/`no_candidate` payloads are built elsewhere; GSD: on every `resolve-dispatch` result); otherwise nothing new is printed. The skills relay the line together with the key that actually ran (`$INJECTION_KEY` / `RESULT_KEY`): a lite pick whose row was unavailable runs a strong row.
- **D7 GSD.** One choice per phase over the phase prompt text; steps are its `<task` tags, touched-file count is unknown there, so the rule always says strong and only jev or an explicit key can pick lite. GSD has no `ladder_keys`.
- **D8 Review side.** `--executor-profile=lite|strong` (default `strong`; any other value means strong) sets `EXECUTOR_PROFILE`, used only for the `plan` archetype. The lite rules live once, in `ai-kit-spec-review-checklist/references/executor-profile-lite.md`: the checklist flags each violation HIGH, and the writing-plans revise prompt gets an extra Step 2b only when lite. Not derived from the execute side.
- **D9 Judge bar.** ai-kit `AGENTS.md`: "passes the `skill-judge` review loop (no remaining Critical/Important finding, or score ≥ 96/120, no regression vs. baseline)". That sentence was written against the 120-point rubric that the host-installed `skill-judge` (`~/.claude/skills/skill-judge` → `~/.agents/skills/skill-judge`, free-text report, no verdict JSON) still uses and that ai-kit's past phases scored with ("104/120, zero Critical"). This plan uses the agent-toolkit checkout instead (rubric v4, `max_total` 125), because the gate needs its machine-readable `percent` and `findings[].severity`. The bar is applied as the same proportion: 96/120 = **80.0 percent** (= 100/125 on v4, not 96/125 = 76.8 %); "Critical/Important" maps to v4 severities `critical`/`high`; "no regression" compares v4 percents against the Task 1 v4 baseline. Remaining high findings with percent >= 80.0 are reported, not blocking.
- **D10 Commits.** One commit per Task 2-7 (code, tests and docs together). Tasks 8-12 commit their judge fixes as `git commit --fixup=<the Task 6 or 7 commit>`, so ai-kit's compaction rule ("fold related work into as few logically-grouped, buildable commits as possible") is one owner command at merge (`GIT_SEQUENCE_EDITOR=: git rebase -i --autosquash main`); this plan never rewrites history. Worktree under ai-kit's existing `.claude/worktrees/` convention (`.claude/` is gitignored).
- **D11 GSD workflow waiver (owner decision; starting execution of this plan is the owner's approval).** ai-kit `AGENTS.md` Required Start #2 routes repository edits through a GSD workflow (`/gsd-quick` for small out-of-plan work). This work is cross-repo (its card comes from llm-env), was planned and cross-AI reviewed as a superpowers plan in llm-env at the owner's request, and runs under subagent-driven-development in an isolated worktree with no merge; a `/gsd-quick` would re-plan it in GSD's own format and execute outside these reviewed tasks. Kept from the rule: Required Start #1 (Task 1 reads `.planning/STATE.md` and reports every `gsd/*` branch that changes the same files; on 2026-10-30, phase 26 on `gsd/v1.3-curated-config-v2` changes `skills/ai-kit-spec-execute-superpowers/SKILL.md` and its tests), #3 (Task 1 baselines; Tasks 2-3 test against the real client contract), and `.planning/` stays untouched: the hand-off gives the owner a one-line summary to record there. An executor without the owner's go-ahead stops at Task 1 Step 1.
- **D12 `EXECUTOR_KEY` lifetime.** Filled only from the user's own words for this run. A scheduled superpowers quota wake carries it in the wake prompt (`references/quota-wake.md`); a GSD resumable-state file records it as `executor_key` and the re-invoke passes it back; an owner's manual re-invoke of superpowers takes it from the owner's words again.

Not verified when this plan was written: a real schema 1 card (llm-env's capacity plan is not executed yet; the shape is taken from its schema table) and whether `llmenv capacity show` prints the card plus `stale_sections`; whether `TOOL_ERROR_RATE_MAX = 0.2` separates usable from unusable local models (calibrate on the first real card); jev's live answers and latency on the two items; whether `router-env/my-coding-lite` exists in OmniRoute; the five skill-judge baselines (Task 1 measures them).

## Review responses (round 1)

Evidence was checked against ai-kit `main` `252316e` and reproduced with `tmp/ai-kit-plan/verify/check_c1_c3_c4.py` in llm-env (it imports the real modules read-only).

| Finding | Verdict | Evidence | Change |
|---|---|---|---|
| C1 files count | accepted | `extract_touched_paths(SMALL_TASK)` returns `['a.py', 'tests/test_a.py', 'python\nassert flag() == 1\n']`: `_files_block` runs to the next line starting `**` (`task_classification.py:19-21`) | D3; `files_block_count` (Task 2); superpowers passes it; CLI rule-path test (Task 4) |
| C2 `stale_sections` | accepted | capacity plan schema table: `stale_sections` "only in `capacity show` output"; its Task 4 `write_card` refreshes `fingerprint` and recomputes `usable` | D2 staleness from section vs card `fingerprint`; snapshot key optional; known limit documented; Task 2 test rewritten |
| C3 2000-char guard | rejected (native right) | the old test's own measure is 1,122 chars for the 500-char ASCII title; the real guard counts one item per Laya request (`_groups`, `"batch": False`) as compact sorted JSON (`_state_chars`, `jev_review.py:1379-1381`): 589 and 580; R1's "~1,500" is itself under 2000 | test now mirrors the real per-item measure with a non-ASCII title (real 1,189; the old two-item measure would hit 2,322); CTX caps risk words at 5 |
| C4 answer object | rejected (native right) | real stdout is `{"advisory": true, **screen()}` (`:2034`); ok results carry `status`, `answers` (`:1824-1825`); each answer has bool `needs_fallback` (`:1577`), noul a number (`:412`), score `level` the winning key as a string (`:445-446`); the stub used exactly these keys and types | stubs now also carry the real extra keys (`top`, `backend`, `forced`, `model`, `score`, `probabilities`) |
| C5 GSD unknown key | rejected | `FakeGsdTools.config_set_calls()` keeps only `argv[2] == "config-set"` (`tests/test_ai_kit_spec_gsd.py:1120-1121`); exit 2 happens before `resolve_gsd_dispatch`, the only `config-set` caller | none |
| C6 excluded explicit key | rejected | the plan resolved the explicit key on `full_candidates` and applied it to the post-exclusion list; an empty walk list hits `if not loaded.candidates` → `quota_exhausted` (`cli.py:269-271`) | kept; exclusions now also passed to the choice so an excluded lite row turns it off (new test) |
| H1 ranking (N3, R7) | partly accepted | `context_limit` half: sort key `(context_limit is None, rank, affinity)` (`execute_selection.py:146-151`) puts a lite row with a limit first in `ladder_keys`. `prefer_key` half rejected: `compute_ladder_keys` forces `prefer_key=None`, and with `prefer_key` unset `test_unknown_context_ranks_after_confirmed_sufficient` is untouched | `lite_last` on `ladder_keys` (D5); `context_limit` advice dropped; test with a lite `context_limit` and lite listed first |
| H2 wake/resume | accepted | `quota-wake.md` binds 14 values and fills "six placeholder lines" (pinned, tests:2818-2831); GSD state JSON has no key field | D12; wake binds `EXECUTOR_KEY` (15 placeholders, seven lines); GSD state records `executor_key` (Task 6, tests) |
| H3 GSD workflow | accepted | ai-kit `AGENTS.md:9-14` | D11 waiver for the owner, Task 1 STATE.md + branch-overlap step, hand-off note |
| H4 `ladder_keys` before choice | partly accepted | real only when lite is not last; the suggested fix (omit lite) is unsafe: the floor would then not exclude lite, and a round-4 re-run's fresh jev answer could prefer lite as the "bump" | `ladder_keys` keeps every key with lite last (D5); strong-pick test asserts it |
| H5 blank task, no jev | rejected | the plan's `choose_executor` order ran the gate before, and independent of, `ask_fn` | added an `ask_fn=None` blank-task case that pins it |
| H6 jev child env (R8, N12) | accepted | `{**env, "GSD_PATH_JEV": "1"}` already beat a parent `GSD_PATH_JEV=0`, but `JEV_MIN_CONFIDENCE`, `JEV_ALLOW_REMOTE`, `JEV_BACKEND` and other `GSD_PATH_JEV*` passed through and no deadline was set (`decide.py:35-36,400-413`) | D4 scrub, deadline 20·(n+1) s, subprocess timeout deadline + 10 s (Task 3) |
| H7 guard loop | rejected | `_fill_task_paths` substitutes by name (`NAME="<...>"` → `NAME='value'`, tests:2458-2465) and the plan adds `"EXECUTOR_KEY": ""` there, so every bash-run test gets `EXECUTOR_KEY=''`, which the separate guard accepts; an empty key must stay valid, so it cannot join the empty-rejecting loop; the one real gap was the wake (H2) | none beyond H2 |
| N6 locals / double read | accepted | 15 locals in both functions today | `_with_executor_choice`, `_GsdChoice`; GSD reads the phase file once (`_phase_prompt_text`) |
| N7 GSD card | accepted | | `_card` helper in the GSD tests |
| N8 lite reported, strong ran | accepted | | D6: relay the key that ran |
| N9 e2e skips | accepted | `.claude/` is gitignored; e2e skips without `.claude/gsd-core` | Task 1 symlink |
| N10 compaction | accepted | ai-kit `AGENTS.md` Workflow | D10 fixup commits |
| N11 lows | accepted | `main` does `CliDeps()._replace(**overrides)` → `ValueError` before argparse | expected text fixed; `[1, 2]` in a file; D2 "others are ignored" |
| N13 client roots | accepted (doc) | | D4 and `executor-choice.md` state the narrower lookup |
| N14 review-only row | accepted | `filter_by_purpose` returns all rows when none match | purpose check in `resolve_explicit_key` |
| N15 GSD steps | accepted | GSD plans use `<task>` blocks | `<task` lines count as steps |
| R10 all-invalid card | accepted | | problem text (D2) |
| R11 `False` "fits" | rejected | D3 sends strong when `tool_calling_reliable is not True`; `False is not True` | none |
| R12 use `rg` | rejected | Task 13 Step 4 already uses `rg` | none |

## File Structure

| Path (in `$WT`) | Responsibility |
|---|---|
| `skills/ai-kit-spec-review/ai_kit_spec/executor_choice.py` (new) | card adapter, task signals, gate, jev items/pick, rule, choice, apply, `lite_last`, jev subprocess |
| `skills/ai-kit-spec-review/ai_kit_spec/execute_selection.py` | `prefer_key` re-rank |
| `skills/ai-kit-spec-execute-superpowers/ai_kit_spec_superpowers/{dispatch_injection,cli}.py` | `capacity_card` passthrough, `prefer_key`, `--executor-key`, lite-last `ladder_keys` |
| `skills/ai-kit-spec-execute-gsd/ai_kit_spec_gsd/{adapter,cli}.py` | same for GSD |
| `skills/ai-kit-spec-review/references/executor-choice.md` (new) | the one doc both adapters link |
| `skills/ai-kit-spec-execute*/SKILL.md`, superpowers `references/{escalation,quota-wake,dispatch-external-cli}.md`, GSD `references/resumable-state.md` | pass, persist and relay `EXECUTOR_KEY` and the choice |
| `skills/ai-kit-spec-review-checklist/references/executor-profile-lite.md` (new) | lite plan rules LP1-LP6 |
| `skills/ai-kit-spec-review{,-checklist}/SKILL.md`, review `references/{apply-findings,reviewer-prompts,cross-ai-dispatch}.md` | `EXECUTOR_PROFILE` |
| `skills/ai-kit-spec-review/evals/05-lite-executor-plan.md` (new) | review eval |
| `tests/test_ai_kit_spec_executor_choice.py` (new), `tests/test_ai_kit_spec_executor_profile_text.py` (new), `tests/test_ai_kit_spec_{superpowers,gsd}.py` | tests |
| `README.md` | skill table rows |

---

### Task 1: Worktree, environment, overlap check and baselines

**Files:**
- Create (untracked scratch): `tmp/overlap.sh`, `tmp/overlap.txt`, `tmp/skill-judge/gate.py`, `tmp/skill-judge/<NAME>-baseline.json` for the five skills; symlink `.claude/gsd-core` (gitignored)

**Interfaces:**
- Produces: `$WT` on `feat/lite-executor-routing`; `tmp/skill-judge/gate.py BASELINE ROUND` exit 0 = PASS; `tmp/overlap.txt` for the hand-off.

- [ ] **Step 1: Confirm the waiver, then create the worktree**

Proceed only if the owner started this execution (D11); otherwise STOP and ask. Then:

```bash
AIKIT=/var/home/bazzite/git/personal/ai-kit
git -C "$AIKIT" worktree add .claude/worktrees/lite-executor-routing -b feat/lite-executor-routing main
```

Expected: `Preparing worktree (new branch 'feat/lite-executor-routing')`. From here on, `cd "$AIKIT/.claude/worktrees/lite-executor-routing"` (= `$WT`) before every command.

- [ ] **Step 2: Link gsd-core and provision the dev venv**

Run: `AIKIT="$(dirname "$(git rev-parse --path-format=absolute --git-common-dir)")"; ls -d "$AIKIT/.claude/gsd-core"`. If it exists, run `mkdir -p .claude && ln -s "$AIKIT/.claude/gsd-core" .claude/gsd-core` (read-only use; lets `make test` run the gsd-core e2e classes instead of skipping them); if not, note it for the hand-off. Then `make setup-env`, then `make test-unit`.
Expected: `make setup-env` and `make test-unit` exit 0. If `make test-unit` fails on `main`, STOP and report the failing modules to the owner (pre-existing failures are reported, never worked around).

- [ ] **Step 3: Read the GSD state and record overlapping branches (D11)**

Read `.planning/STATE.md` (frontmatter `current_phase`, `status`). Write `tmp/overlap.sh`:

```bash
#!/usr/bin/env bash
# D11: list local gsd/* branches that change the files this plan edits.
paths=(skills/ai-kit-spec-execute skills/ai-kit-spec-execute-superpowers
       skills/ai-kit-spec-execute-gsd skills/ai-kit-spec-review skills/ai-kit-spec-review-checklist
       README.md tests/test_ai_kit_spec_superpowers.py tests/test_ai_kit_spec_gsd.py
       tests/test_ai_kit_spec.py)
git for-each-ref --format='%(refname:short)' 'refs/heads/gsd/' | while read -r b; do
  stat="$(git diff --stat "main...$b" -- "${paths[@]}")"
  [ -n "$stat" ] && printf '== %s\n%s\n' "$b" "$stat"
done
exit 0
```

Run: `bash tmp/overlap.sh | tee tmp/overlap.txt`
Expected: exit 0; on 2026-10-30 it lists `gsd/v1.3-curated-config-v2` (phase 26). Do not stop: the owner orders the merges (hand-off, Task 13).

- [ ] **Step 4: Write `tmp/skill-judge/gate.py`**

```python
import json
import sys

base, cur = (json.load(open(p, encoding="utf-8")) for p in sys.argv[1:3])
high = [f["id"] for f in cur["findings"] if f["severity"] in ("critical", "high")]
ok = (cur["percent"] >= 80.0 or not high) and cur["percent"] >= base["percent"]  # D9
print(("PASS" if ok else "FAIL"), cur["percent"], "baseline", base["percent"], "high:", high)
sys.exit(0 if ok else 1)
```

- [ ] **Step 5: Baseline the five skills**

Run SJL round `baseline` for each `NAME` in `ai-kit-spec-execute`, `ai-kit-spec-execute-superpowers`, `ai-kit-spec-execute-gsd`, `ai-kit-spec-review`, `ai-kit-spec-review-checklist` (step 1 of SJL only; no gate).
Expected: five files `tmp/skill-judge/<NAME>-baseline.json`, each one JSON object with `"rubric_version": 4`, `"max_total": 125` and a `percent`.

- [ ] **Step 6: Confirm nothing tracked changed**

Run: `git status --short`
Expected: empty output (`tmp/` and `.claude/` are gitignored). No commit in this task.

---

### Task 2: Executor choice core (card, signals, gate, jev decision, rule)

**Files:**
- Create: `skills/ai-kit-spec-review/ai_kit_spec/executor_choice.py`
- Create: `tests/test_ai_kit_spec_executor_choice.py`
- Modify (generated): `Makefile:7-13`, `.pre-commit-config.yaml` listing block

**Interfaces:**
- Produces (module `ai_kit_spec.executor_choice`, imported as `ec`):
  - constants `ZERO_HEAD = "0" * 40`, `CONTEXT_SHARE = 0.6`, `LITE_YES = 0.7`, `LITE_NO = 0.3`, `TOOL_ERROR_RATE_MAX = 0.2`, `CARD_KEYS = ("max_context", "tokens_per_s", "max_layer", "tool_calling_reliable", "measured")`, `DIFFICULTY_LEVELS = ["trivial", "routine", "needs reasoning", "risky"]`
  - `class ExecutorChoiceError(ValueError)`
  - `class ChoiceInputs(NamedTuple): candidates: list; task_markdown: str; files_touched: int | None; required_context: int; cwd: str; explicit: str | None = None; excluded: frozenset = frozenset()`
  - `load_capacity_card(source: str, base_dir: str) -> tuple[dict, str | None]` — dict has exactly `CARD_KEYS`; second item is a problem text when the card could not be read/parsed or has no valid value, else `None`
  - `files_block_count(task_markdown: str) -> int | None`
  - `task_signals(task_markdown: str, files_touched: int | None) -> dict` — keys `title, steps, files, has_test_step, code_blocks, risk_words`
  - `resolve_explicit_key(candidates: list, wanted: str) -> str`
  - `lite_candidate(candidates: list) -> dict | None`
  - `jev_items(signals: dict, card: dict, required_context: int) -> list`
  - `jev_pick(result: object) -> str | None`
  - `rule_pick(signals: dict, card: dict) -> str`
  - `choose_executor(inputs: ChoiceInputs, ask_fn: Callable[[list], object] | None = None) -> dict` — keys exactly `pick` (`"lite"|"strong"|None`), `source` (`"explicit"|"gate"|"jev"|"rule"|"none"`), `reason` (str), `key` (explicit key, lite key for a lite pick, else `None`)
  - `apply_choice(candidates: list, choice: dict) -> tuple[list, str | None]` — (walk candidates, prefer_key)
  - `lite_last(keys: list, candidates: list) -> list` — `keys` with every key of a row carrying `capacity_card` moved to the end, order otherwise kept
  - `choice_note(choice: dict) -> str` — `f"ai-kit-spec: executor {pick} ({source}): {reason}"`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_ai_kit_spec_executor_choice.py`:

```python
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "skills", "ai-kit-spec-review"))

from ai_kit_spec import executor_choice as ec

GOOD_CARD = {"max_context": 32768, "tokens_per_s": 45.0, "max_layer": "L3",
             "tool_calling_reliable": True, "measured": "2026-10-30"}
UNKNOWN = dict.fromkeys(ec.CARD_KEYS)
SMALL_TASK = (
    "### Task 1: Add flag\n**Files:**\n- Modify: `a.py`\n- Test: `tests/test_a.py`\n\n"
    "- [ ] **Step 1: Write the failing test**\n\n```python\nassert flag() == 1\n```\n\n"
    "- [ ] **Step 2: Run test to verify it fails**\n- [ ] **Step 3: Implement flag**\n"
    "- [ ] **Step 4: Run test to verify it passes**\n- [ ] **Step 5: Commit**\n")


SCHEMA1 = {  # llm-env schema 1 (2026-10-30-local-combo-and-capacity), trimmed to what is read
    "schema": 1, "alias": "ornith", "written_at": "2026-10-30T12:00:00Z", "fingerprint": "f1",
    "benchmark": {"pp_tps": 900.0, "tg_tps": 45.0, "measured_at": "2026-10-30"},
    "ladder": {"fingerprint": "f1", "highest_layer": 3, "tool_error_rate": 0.1},
    "usable": {"context_tokens": 32768, "max_output_tokens": 8192, "basis": "verified"}}


def _roster(tmp, card):
    path = os.path.join(tmp, "card.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(card, f)
    return [{"key": "pi/lite", "model": "router-env/my-coding-lite", "runtime": "pi",
             "capacity_card": path},
            {"key": "pi/strong", "model": "router-env/my-coding", "runtime": "pi"}]


def _inputs(roster, tmp, task=SMALL_TASK, files=2, ctx=4000, explicit=None,
            excluded=frozenset()):
    return ec.ChoiceInputs(roster, task, files, ctx, tmp, explicit, excluded)


def _jev(noul, level, fallback=False):
    """The client's real stdout shape (jev_review.py main, _result, _finalize, score checker)."""
    common = {"backend": "laya", "model": "laya-jev-1", "forced": False,
              "needs_fallback": fallback, "request_sha256": "0" * 64}
    probs = {str(i): (0.7 if str(i) == level else 0.1) for i in range(4)}
    return {"advisory": True, "status": "ok", "backend": "laya", "model": "laya-jev-1",
            "attempts": [], "unavailable": {}, "reviewed_head": ec.ZERO_HEAD,
            "usage": {"input_tokens": 1, "output_tokens": 1}, "answers": {
                "lite_ok": {"type": "noul", "noul": noul, "top": max(noul, 1 - noul),
                            **common},
                "difficulty": {"type": "score", "score": float(level), "level": level,
                               "legend": dict(zip("0123", ec.DIFFICULTY_LEVELS)),
                               "probabilities": probs, "confidence": 0.7, "top": 0.7,
                               **common}}}


def _never(_items):
    raise AssertionError("jev must not be asked")
```

Then these classes (assertions are the contract):

```python
class TestCapacityCard(unittest.TestCase):
    def test_reads_a_card_file_and_keeps_only_known_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "c.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({**GOOD_CARD, "extra": 1}, f)
            self.assertEqual(ec.load_capacity_card(path, tmp), (GOOD_CARD, None))

    def test_inline_json_relative_and_tilde_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(ec.load_capacity_card(json.dumps(GOOD_CARD), tmp)[0], GOOD_CARD)
            with open(os.path.join(tmp, "c.json"), "w", encoding="utf-8") as f:
                json.dump(GOOD_CARD, f)
            self.assertEqual(ec.load_capacity_card("c.json", tmp)[0], GOOD_CARD)
            with mock.patch.dict(os.environ, {"HOME": tmp}):
                self.assertEqual(ec.load_capacity_card("~/c.json", tmp)[0], GOOD_CARD)

    def test_missing_directory_bad_json_or_a_list_is_unknown_with_a_problem(self):  # RF 1
        with tempfile.TemporaryDirectory() as tmp:
            bad = os.path.join(tmp, "bad.json")
            with open(bad, "w", encoding="utf-8") as f:
                f.write("{not json")
            listing = os.path.join(tmp, "list.json")
            with open(listing, "w", encoding="utf-8") as f:
                f.write("[1, 2]")
            for source in (os.path.join(tmp, "nope.json"), tmp, bad, listing, "{not json"):
                card, problem = ec.load_capacity_card(source, tmp)
                self.assertEqual(card, UNKNOWN, source)
                self.assertTrue(problem, source)
            roster = [{"key": "pi/lite", "model": "m-lite", "runtime": "pi", "capacity_card": tmp},
                      {"key": "pi/strong", "model": "m", "runtime": "pi"}]
            choice = ec.choose_executor(_inputs(roster, tmp), _never)
            self.assertEqual((choice["pick"], choice["source"]), ("strong", "gate"))

    def test_schema_1_card_maps_to_the_internal_view(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(ec.load_capacity_card(json.dumps(SCHEMA1), tmp), (GOOD_CARD, None))
            hand = dict(SCHEMA1, ladder=dict(SCHEMA1["ladder"], highest_layer="L3"))
            self.assertEqual(ec.load_capacity_card(json.dumps(hand), tmp)[0], GOOD_CARD)

    def test_schema_1_sections_count_only_under_the_card_fingerprint(self):  # RF 2
        # The card file has no stale_sections (only `llmenv capacity show` adds it): a section
        # is current only when its own fingerprint equals the card's (llm-env Decision 3).
        stale_fp = dict(SCHEMA1, ladder=dict(SCHEMA1["ladder"], fingerprint="old"))
        no_fp = dict(SCHEMA1, ladder={k: v for k, v in SCHEMA1["ladder"].items()
                                      if k != "fingerprint"})
        no_card_fp = {k: v for k, v in SCHEMA1.items() if k != "fingerprint"}
        snapshot = dict(SCHEMA1, stale_sections=["ladder"])
        no_ladder = dict(SCHEMA1, ladder=None)
        no_ctx = dict(SCHEMA1, usable=dict(SCHEMA1["usable"], context_tokens=None))
        flaky = dict(SCHEMA1, ladder=dict(SCHEMA1["ladder"], tool_error_rate=0.5))
        with tempfile.TemporaryDirectory() as tmp:
            def load(card):
                return ec.load_capacity_card(json.dumps(card), tmp)[0]
            for card in (stale_fp, no_fp, no_card_fp, snapshot, no_ladder):
                self.assertEqual((load(card)["max_layer"], load(card)["tool_calling_reliable"]),
                                 (None, None))
            self.assertEqual(load(stale_fp)["max_context"], 32768)  # usable: rewritten per write
            self.assertIsNone(load(no_ctx)["max_context"])
            self.assertIs(load(flaky)["tool_calling_reliable"], False)
            card, problem = ec.load_capacity_card('{"schema": 2}', tmp)
            self.assertEqual(card, UNKNOWN)
            self.assertIn("schema", problem)

    def test_wrongly_typed_values_are_unknown_with_a_problem(self):  # RF 2
        hostile = {"max_context": "8192", "tokens_per_s": -1, "max_layer": "L9",
                   "tool_calling_reliable": "yes", "measured": "30/10/2026"}
        with tempfile.TemporaryDirectory() as tmp:
            card, problem = ec.load_capacity_card(json.dumps(hostile), tmp)
            self.assertEqual((card, problem), (UNKNOWN, "capacity card has no usable value"))
            card, _ = ec.load_capacity_card('{"max_context": true}', tmp)
            self.assertIsNone(card["max_context"])


class TestTaskSignals(unittest.TestCase):
    def test_counts_steps_files_code_and_test_step(self):
        self.assertEqual(ec.task_signals(SMALL_TASK, 2), {
            "title": "Task 1: Add flag", "steps": 5, "files": 2, "has_test_step": True,
            "code_blocks": 1, "risk_words": []})

    def test_risk_words_are_lowercased_sorted_and_unique(self):
        signals = ec.task_signals("### Task 2: Migrate the AUTH schema\n- [ ] auth again", None)
        self.assertEqual(signals["risk_words"], ["auth", "migrate", "schema"])
        self.assertIsNone(signals["files"])

    def test_gsd_task_tags_count_as_steps(self):
        text = '<task type="auto">\n  <name>a</name>\n</task>\n<task type="auto">b</task>\n'
        self.assertEqual(ec.task_signals(text, None)["steps"], 2)

    def test_files_come_from_the_files_block_lines_only(self):  # C1
        self.assertEqual(ec.files_block_count(SMALL_TASK), 2)
        multi = ("**Files:**\n- Modify: `a.py:10-20`, `b.py`\n- Test: `a.py`\n\n"
                 "```python\nx = `not_a_file`\n```\n")
        self.assertEqual(ec.files_block_count(multi), 2)
        self.assertIsNone(ec.files_block_count("### Task 1\n- [ ] **Step 1: x**\n"))
        self.assertIsNone(ec.files_block_count("**Files:** none\n"))


class TestExplicitKey(unittest.TestCase):
    def test_key_then_unique_execute_model_else_an_error_listing_keys(self):  # RF 4
        with tempfile.TemporaryDirectory() as tmp:
            roster = _roster(tmp, GOOD_CARD)
            self.assertEqual(ec.resolve_explicit_key(roster, "pi/strong"), "pi/strong")
            self.assertEqual(ec.resolve_explicit_key(roster, "router-env/my-coding-lite"),
                             "pi/lite")
            with self.assertRaisesRegex(ec.ExecutorChoiceError, "pi/lite.*pi/strong"):
                ec.resolve_explicit_key(roster, "nope")
            twin = [*roster, {"key": "opencode/strong", "model": "router-env/my-coding",
                              "runtime": "opencode"}]
            with self.assertRaisesRegex(ec.ExecutorChoiceError, "opencode/strong.*pi/strong"):
                ec.resolve_explicit_key(twin, "router-env/my-coding")
            review = [*roster, {"key": "pi/rev", "model": "m-rev", "runtime": "pi",
                                "purpose": "review"}]
            with self.assertRaisesRegex(ec.ExecutorChoiceError, "pi/rev.*review"):
                ec.resolve_explicit_key(review, "pi/rev")
```

`TestChooseExecutor` (one `tempfile.TemporaryDirectory()` per test; `roster = _roster(tmp, card)`):

- `test_inactive_without_a_lite_and_a_strong_row`: for `rows` in `[roster[1]]` and `[roster[0]]`: `choice = ec.choose_executor(_inputs(rows, tmp), _never)`; assert `(choice["pick"], choice["source"]) == (None, "none")`, `set(choice) == {"pick", "source", "reason", "key"}`, and `ec.apply_choice(rows, choice) == (rows, None)`.
- `test_an_excluded_lite_row_turns_the_choice_off`: `excluded=frozenset({"pi/lite"})` with `_never` → `(None, "none")`; `explicit="pi/lite", excluded=frozenset({"pi/lite"})` → `(pick, source, key) == ("lite", "explicit", "pi/lite")` (resolved over the whole roster).
- `test_explicit_wins_and_jev_is_never_asked`: `explicit="router-env/my-coding"` gives `(choice["pick"], choice["source"], choice["key"]) == ("strong", "explicit", "pi/strong")`; `explicit="pi/lite"` gives `("lite", "explicit", "pi/lite")`; both with `_never`.
- `test_gate_sends_unknown_or_tight_capacity_to_strong_without_jev`: for `(card, ctx, task, needle, ask)` in `({**GOOD_CARD, "tool_calling_reliable": None}, 4000, SMALL_TASK, "tool calling", _never)`, `({**GOOD_CARD, "tool_calling_reliable": False}, 4000, SMALL_TASK, "tool calling", _never)`, `({**GOOD_CARD, "max_context": None}, 4000, SMALL_TASK, "max_context unknown", _never)`, `({**GOOD_CARD, "max_context": 10000}, 6001, SMALL_TASK, "60%", _never)`, `(GOOD_CARD, 4000, "   ", "no task text", _never)`, `(GOOD_CARD, 4000, "   ", "no task text", None)`: assert `(pick, source) == ("strong", "gate")` and `needle in choice["reason"]`.
- `test_context_at_exactly_sixty_percent_passes_the_gate`: card `max_context=10000`, `ctx=6000`, ask_fn returns `_jev(0.9, "1")` → `source == "jev"`.
- `test_jev_decides_when_confident`: `(_jev(0.82, "1"), "lite")`, `(_jev(0.1, "0"), "strong")`, `(_jev(0.9, "2"), "strong")` → `(pick, source) == (pick, "jev")`; the lite case has `key == "pi/lite"`, the strong ones `key is None`.
- `test_unusable_jev_answers_fall_back_to_the_rule` (Review Focus 3): for result in `_jev(0.9, "1", fallback=True)`, `{"advisory": True, "status": "disabled"}`, `{"status": "no_client"}`, `"garbage"`, `None`, `{"status": "ok", "answers": {"lite_ok": _jev(0.9, "1")["answers"]["lite_ok"]}}` (difficulty missing), `{"status": "ok", "answers": {"lite_ok": {"noul": "0.9", "needs_fallback": False}, "difficulty": {"level": 1, "needs_fallback": False}}}`, `_jev(0.5, "1")`: assert `source == "rule"` and `pick == "lite"` (SMALL_TASK passes the rule).
- `test_rule_needs_every_small_task_signal`: `ask_fn=None` on SMALL_TASK, files 2, GOOD_CARD → `("lite", "rule")`. Each of these gives `("strong", "rule")`: `files=3`; `files=None`; card `max_layer="L1"`; card `max_layer=None`; `task=SMALL_TASK + "- [ ] **Step 6: Push**\n"`; `task=SMALL_TASK.replace("```python\nassert flag() == 1\n```\n", "")`; `task` with the three step texts containing "test" replaced by `Write the check`, `Run it`, `Run it again`; `task=SMALL_TASK.replace("Implement flag", "Delete the old flag")`.
- `test_jev_items_fit_the_laya_state_guard`: Laya sends one item per request and its guard counts the compact sorted JSON of that request's state (`jev_review.py` `_groups`, `_state_chars`); a non-ASCII character costs six there. `task = "### " + "é" * 500 + " auth authz authority authored authoring authorize\n" + SMALL_TASK`; `items = ec.jev_items(ec.task_signals(task, 2), GOOD_CARD, 4000)`; ids `["lite_ok", "difficulty"]`, types `["noul", "score"]`, `items[1]["criteria"] == ec.DIFFICULTY_LEVELS`, each item's keys a subset of `{"id", "type", "instructions", "context", "criteria"}`, `"Model max context: 32768" in items[0]["context"]`, `items[0]["context"].count("auth") == 5` (risk words capped at five), and for each item `len(json.dumps({"reviewed_head": ec.ZERO_HEAD, "items": [item]}, sort_keys=True, separators=(",", ":"))) <= 2000`.

`TestApplyChoice.test_apply_choice_per_pick` with `roster = _roster(tmp, GOOD_CARD)`:

```python
        lite = {"pick": "lite", "source": "jev", "reason": "r", "key": "pi/lite"}
        self.assertEqual(ec.apply_choice(roster, lite), (roster, "pi/lite"))
        strong = {"pick": "strong", "source": "rule", "reason": "r", "key": None}
        self.assertEqual(ec.apply_choice(roster, strong), ([roster[1]], None))
        for key, pick, kept in (("pi/strong", "strong", roster[1]), ("pi/lite", "lite", roster[0])):
            explicit = {"pick": pick, "source": "explicit", "reason": "r", "key": key}
            self.assertEqual(ec.apply_choice(roster, explicit), ([kept], None))
        gone = {"pick": "lite", "source": "explicit", "reason": "r", "key": "pi/lite"}
        self.assertEqual(ec.apply_choice([roster[1]], gone), ([], None))  # excluded explicit
        self.assertEqual(ec.lite_last(["pi/lite", "x", "pi/strong"], roster),
                         ["x", "pi/strong", "pi/lite"])
        self.assertEqual(ec.choice_note(lite), "ai-kit-spec: executor lite (jev): r")
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `python3 -m unittest tests.test_ai_kit_spec_executor_choice -v`
Expected: FAIL with `ImportError: cannot import name 'executor_choice'`.

- [ ] **Step 3: Implement `executor_choice.py` (stdlib only: `json`, `os`, `re`, `typing`; plus `ai_kit_spec.execute_selection.purpose_matches`)**

Module docstring: one paragraph stating D3 and "advisory only, never a gate". Implementation choices the tests do not fix:
- `load_capacity_card`: a stripped `source` starting with `{` is parsed as JSON text; otherwise `os.path.join(base_dir, os.path.expanduser(source))` is opened (`OSError`, `UnicodeError`, `ValueError` → all-unknown plus `f"capacity card {source!r} unreadable: {exc}"`); a non-object JSON value is all-unknown plus a problem. Then D2: `"schema": 1` → `_from_schema_1` (the `ladder` values only when `ladder.fingerprint` is a non-empty string equal to the card's `fingerprint` and `"ladder"` is not in a list-valued `stale_sections`), a `schema` key with any other value → all-unknown plus `f"unsupported capacity card schema {value!r}"`, no `schema` key → read the five flat keys. Per-key validation: `max_context` int (not bool) > 0; `tokens_per_s` int or float (not bool) > 0; `max_layer` matches `^L[1-5]$` (schema 1 also accepts an int 1-5, rendered `f"L{n}"`); `tool_calling_reliable` a bool (schema 1: `tool_error_rate` an int/float, not bool, in [0, 1]); `measured` matches `^\d{4}-\d{2}-\d{2}$`. A nested section that is not a dict counts as missing. A parsed object whose five values all end `None` returns the problem `"capacity card has no usable value"`.
- `files_block_count`: text after the first `**Files:**` up to the first blank line, the first line starting `- [`, or the end; distinct backtick spans in it, each with a trailing `:\d+(-\d+)?` stripped; 0 → `None`.
- `task_signals`: `title` = first line starting with `#`, `#`s and whitespace stripped, else `""`; `steps` = lines matching `^\s*- \[[ xX]\]` or `^\s*<task\b`; `has_test_step` = any `- [ ]` step line matching `\btest` case-insensitively; `code_blocks` = (lines starting with optional spaces then three backticks) // 2; `risk_words` = sorted unique lowercased matches of `\b(auth\w*|migrat\w*|schema\w*|delet\w*|concurren\w*)\b` (IGNORECASE).
- `resolve_explicit_key`: exact key first; else rows whose `model == wanted`: one → its key; none → `ExecutorChoiceError(f"--executor-key {wanted!r} names no roster key or model; keys: {', '.join(sorted(all keys))}")`; several → `ExecutorChoiceError(f"--executor-key {wanted!r} matches several rows: {', '.join(sorted(matching keys))}")`. The found row must pass `purpose_matches(row.get("purpose"), "execute")`, else `ExecutorChoiceError(f"--executor-key {wanted!r} names {key}, whose purpose is {purpose!r}, not execute")`.
- Gate reasons (pinned by tests): `"no task text to judge"`, `"tool calling not measured reliable"` (prefix the card problem when there is one), `"max_context unknown"`, `f"required context {r} > 60% of max_context {m}"`.
- `jev_items` exact copy:
  - `{"id": "lite_ok", "type": "noul", "instructions": "Can a small local coding model with the capacity in the context implement this task correctly on its first attempt, following the task text literally?", "context": CTX}`
  - `{"id": "difficulty", "type": "score", "instructions": "How hard is this task for a coding model that follows the task text literally?", "context": CTX, "criteria": DIFFICULTY_LEVELS}`
  - `CTX = f"Task: {title[:120]}. Steps: {steps}. Files: {files or 'unknown'}. Test step: {'yes' if has_test_step else 'no'}. Code blocks: {code_blocks}. Risk words: {', '.join(risk_words[:5]) or 'none'}. Estimated context tokens: {required_context}. Model max context: {max_context or 'unknown'}. Model max layer: {max_layer or 'unknown'}. Model tokens per second: {tokens_per_s or 'unknown'}."`
- `jev_pick`: D4 exactly; requires `result["status"] == "ok"` and a dict `answers`; a "confident" answer is a dict whose `needs_fallback is False`; `noul` must be an int/float (not bool) in [0, 1]; `level` must be the string `"0"`..`"3"`; every other key is ignored.
- `choose_executor` order: explicit (resolved over all `candidates`; `pick` = `"lite"` when the explicit row has a `capacity_card`, else `"strong"`) → `lite_candidate([c for c in candidates if c["key"] not in excluded])` is `None` → `none` → load card → gate (whether or not `ask_fn` is given) → if `ask_fn`: `jev_pick(ask_fn(jev_items(...)))` → rule. Reasons: jev `f"jev lite_ok={noul} difficulty={label}"`, rule `f"rule ({why jev did not decide}): steps={s} files={f} test={y/n} code={n} risk={words or 'none'} layer={layer}"`. Keep each function inside the pylint limits (extract `_gate_reason`, `_jev_reason`).

- [ ] **Step 4: Run the tests to see them pass, refresh listings, lint**

Run: `python3 -m unittest tests.test_ai_kit_spec_executor_choice -v` → all PASS.
Run: `python3 tools/gen_test_groups.py` then `git diff --stat Makefile .pre-commit-config.yaml` → both files changed, naming `tests.test_ai_kit_spec_executor_choice`.
Run: `python3 -m unittest tests.test_gate_scope -v` → PASS. Run: `make ruff pylint` → exit 0.

- [ ] **Step 5: Commit**

```bash
git add skills/ai-kit-spec-review/ai_kit_spec/executor_choice.py tests/test_ai_kit_spec_executor_choice.py Makefile .pre-commit-config.yaml
git commit -m "feat(ai-kit-spec): executor choice core - capacity card, gate, jev decision, rule"
```

---

### Task 3: jev client call (subprocess side)

**Files:**
- Modify: `skills/ai-kit-spec-review/ai_kit_spec/executor_choice.py` (append)
- Test: `tests/test_ai_kit_spec_executor_choice.py` (new class `TestAskJev`; add `import subprocess` to the header)

**Interfaces:**
- Consumes: `ZERO_HEAD` (Task 2).
- Produces:
  - `SKILLS_ROOT: str` = `os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))` (abspath, never realpath: skills are symlinked side by side)
  - `JEV_TIMEOUT_SECONDS = 20`; `JEV_CONTROL_NAMES = frozenset({"JEV_BACKEND", "JEV_ALLOW_REMOTE", "JEV_MIN_CONFIDENCE"})`
  - `find_jev_client(env: Mapping[str, str]) -> str | None` — `env["AI_KIT_JEV_CLIENT"]` (expanded) first, then `os.path.join(SKILLS_ROOT, "jev-fast-decision", "scripts", "jev_review.py")` read at call time; the first existing file whose text (first 256 KiB) has a line `CLIENT_PROTOCOL = 2`
  - `git_head(cwd: str, run_fn: Callable[..., Any] = subprocess.run) -> str`
  - `ask_jev(items: list, cwd: str, env: Mapping[str, str] | None = None, run_fn: Callable[..., Any] = subprocess.run) -> dict` — the client's JSON object, or `{"status": s}` with `s` in `no_client`, `client_timeout`, `client_not_runnable`, `client_bad_output`

- [ ] **Step 1: Write the failing tests**

```python
class TestAskJev(unittest.TestCase):
    ITEMS = [{"id": "lite_ok", "type": "noul", "instructions": "q?"}]

    def _client(self, tmp, body='print("{}")', marker=True):
        path = os.path.join(tmp, "jev_review.py")
        with open(path, "w", encoding="utf-8") as f:
            f.write(("CLIENT_PROTOCOL = 2\n" if marker else "") + body + "\n")
        return path

    def test_the_sibling_client_is_found_in_this_checkout(self):
        found = ec.find_jev_client({})
        self.assertTrue(found.endswith(os.path.join("jev-fast-decision", "scripts",
                                                    "jev_review.py")), found)

    def test_no_client_and_a_client_without_the_protocol_marker(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(ec, "SKILLS_ROOT", tmp):
            self.assertEqual(ec.ask_jev(self.ITEMS, tmp, env={}), {"status": "no_client"})
            old = self._client(tmp, marker=False)
            self.assertEqual(ec.ask_jev(self.ITEMS, tmp, env={"AI_KIT_JEV_CLIENT": old}),
                             {"status": "no_client"})

    def test_runs_the_client_with_its_own_bounds_and_a_scrubbed_env(self):  # Review Focus 3
        calls = []

        def run(argv, **kw):
            calls.append((argv, kw))
            out = "a" * 40 + "\n" if argv[0] == "git" else '{"status": "ok", "answers": {}}'
            return subprocess.CompletedProcess(argv, 0, stdout=out, stderr="")

        with tempfile.TemporaryDirectory() as tmp:
            client = self._client(tmp)
            env = {"AI_KIT_JEV_CLIENT": client, "JEV_BACKEND": "typesafe",
                   "JEV_ALLOW_REMOTE": "1", "JEV_MIN_CONFIDENCE": "0.1", "GSD_PATH_JEV": "0",
                   "GSD_PATH_JEV_DEADLINE_SECONDS": "999", "JEV_LAYA_URL": "http://127.0.0.1:9"}
            self.assertEqual(ec.ask_jev(self.ITEMS, tmp, env=env, run_fn=run),
                             {"status": "ok", "answers": {}})
        (git_argv, _), (jev_argv, jev_kw) = calls
        self.assertEqual(git_argv, ["git", "-C", tmp, "rev-parse", "HEAD"])
        self.assertEqual(jev_argv, [sys.executable, "-B", client])
        child = jev_kw["env"]
        self.assertEqual(
            {k: child.get(k) for k in ("GSD_PATH_JEV", "GSD_PATH_JEV_TIMEOUT_SECONDS",
                                        "GSD_PATH_JEV_DEADLINE_SECONDS", "JEV_CALLER",
                                        "JEV_LAYA_URL")},
            {"GSD_PATH_JEV": "1", "GSD_PATH_JEV_TIMEOUT_SECONDS": "20",
             "GSD_PATH_JEV_DEADLINE_SECONDS": "40", "JEV_CALLER": "ai-kit-spec-execute",
             "JEV_LAYA_URL": "http://127.0.0.1:9"})
        for name in ("JEV_BACKEND", "JEV_ALLOW_REMOTE", "JEV_MIN_CONFIDENCE"):
            self.assertNotIn(name, child)
        self.assertEqual(jev_kw["timeout"], 50)
        self.assertEqual(json.loads(jev_kw["input"]),
                         {"reviewed_head": "a" * 40, "items": self.ITEMS})

    def test_timeouts_crashes_and_bad_output_become_statuses(self):  # Review Focus 3
        def raising(exc):
            def run(argv, **_kw):
                raise OSError("no git") if argv[0] == "git" else exc
            return run

        def printing(text):
            return lambda argv, **_kw: subprocess.CompletedProcess(argv, 0, stdout=text,
                                                                  stderr="")

        with tempfile.TemporaryDirectory() as tmp:
            env = {"AI_KIT_JEV_CLIENT": self._client(tmp)}
            for run, status in ((raising(subprocess.TimeoutExpired("x", 1)), "client_timeout"),
                                (raising(OSError("gone")), "client_not_runnable"),
                                (printing("not json"), "client_bad_output"),
                                (printing("[1]"), "client_bad_output")):
                self.assertEqual(ec.ask_jev(self.ITEMS, tmp, env=env, run_fn=run),
                                 {"status": status})

    def test_git_head_falls_back_to_zero_head(self):
        def garbage(argv, **_kw):
            return subprocess.CompletedProcess(argv, 0, stdout="garbage", stderr="")

        def boom(argv, **_kw):
            raise OSError("no git")

        self.assertEqual(ec.git_head("x", garbage), ec.ZERO_HEAD)
        self.assertEqual(ec.git_head("x", boom), ec.ZERO_HEAD)

    def test_real_subprocess_round_trip_with_a_fake_client(self):
        body = ('import json, sys\njson.load(sys.stdin)\n'
                'print(json.dumps({"status": "ok", "answers": {}}))')
        with tempfile.TemporaryDirectory() as tmp:
            env = {"AI_KIT_JEV_CLIENT": self._client(tmp, body),
                   "PATH": os.environ.get("PATH", "")}
            self.assertEqual(ec.ask_jev(self.ITEMS, tmp, env=env),
                             {"status": "ok", "answers": {}})
```

- [ ] **Step 2: Run to see them fail**

Run: `python3 -m unittest tests.test_ai_kit_spec_executor_choice.TestAskJev -v`
Expected: FAIL with `AttributeError: module 'ai_kit_spec.executor_choice' has no attribute 'find_jev_client'`.

- [ ] **Step 3: Implement `find_jev_client`, `git_head`, `ask_jev`**

`git_head`: `run_fn(["git", "-C", cwd, "rev-parse", "HEAD"], capture_output=True, text=True, check=False, timeout=5)`; return the stripped stdout only when it matches `^[0-9a-f]{40}$`, else `ZERO_HEAD`; `OSError`/`subprocess.SubprocessError` → `ZERO_HEAD`. `ask_jev`: `env = os.environ if env is None else env`; client missing → `{"status": "no_client"}`; payload `{"reviewed_head": git_head(cwd, run_fn), "items": items}`; `deadline = JEV_TIMEOUT_SECONDS * (len(items) + 1)`; child env (D4) = every `env` item except `JEV_CONTROL_NAMES` and names starting `GSD_PATH_JEV`, then `GSD_PATH_JEV="1"`, `GSD_PATH_JEV_TIMEOUT_SECONDS=str(JEV_TIMEOUT_SECONDS)`, `GSD_PATH_JEV_DEADLINE_SECONDS=str(deadline)`, `JEV_CALLER="ai-kit-spec-execute"`; call `run_fn([sys.executable, "-B", client], input=json.dumps(payload), capture_output=True, text=True, env=child, check=False, timeout=deadline + 10)`; `TimeoutExpired` → `client_timeout`, `OSError` → `client_not_runnable`; stdout not a JSON object → `client_bad_output`.

- [ ] **Step 4: Run to see them pass, lint**

Run: `python3 -m unittest tests.test_ai_kit_spec_executor_choice -v` → all PASS. Run: `make ruff pylint` → exit 0.

- [ ] **Step 5: Commit**

```bash
git add skills/ai-kit-spec-review/ai_kit_spec/executor_choice.py tests/test_ai_kit_spec_executor_choice.py
git commit -m "feat(ai-kit-spec): ask jev-fast-decision for the executor choice (advisory, scrubbed env, recorded consent only)"
```

---

### Task 4: `prefer_key` and the superpowers adapter

**Files:**
- Modify: `skills/ai-kit-spec-review/ai_kit_spec/execute_selection.py:11-17` (field), `:146-151` (sort key)
- Modify: `skills/ai-kit-spec-execute-superpowers/ai_kit_spec_superpowers/dispatch_injection.py:44-49` (`BuildInjectionRequest`), `:180-187` (`assemble_candidates` row), `:265-271` (`_narrow_and_rank`), `:288-290` (`compute_ladder_keys`)
- Modify: `skills/ai-kit-spec-execute-superpowers/ai_kit_spec_superpowers/cli.py:68-92` (`CliDeps`), `:105-115` (`ResolveTaskLoadResult`), `:226-228` (its constructor call), `:243-262` (`_run_resolve_injection_build`), `:265-272` (`_cmd_resolve_injection`), `:388-421` (parser)
- Test: `tests/test_ai_kit_spec_executor_choice.py` (class `TestPreferKey`), `tests/test_ai_kit_spec_superpowers.py` (new class `TestResolveInjectionExecutorChoice`; one test added to `TestAssembleCandidates`)

**Interfaces:**
- Consumes: `ChoiceInputs`, `choose_executor`, `apply_choice`, `lite_last`, `choice_note`, `files_block_count`, `ExecutorChoiceError`, `ask_jev` (Tasks 2-3); `task_classification.estimate_required_context`.
- Produces:
  - `ExecuteResolveOptions.prefer_key: str | None = None` (last field); sort key becomes `(c["key"] != options.prefer_key, c.get("context_limit") is None, rank..., affinity...)` (D5: the walk only).
  - `BuildInjectionRequest.prefer_key: str | None = None` (last field), passed by `_narrow_and_rank`; `compute_ladder_keys` uses `request._replace(candidates=screened, prefer_key=None)`.
  - candidate dicts carry `"capacity_card": r.get("capacity_card")`.
  - `CliDeps.ask_jev_fn: Callable[..., dict] = ask_jev`, called as `deps.ask_jev_fn(items, args.cwd)`.
  - `ResolveTaskLoadResult` gains, after `escalation_exclude`: `full_candidates: list | None = None`, `executor_choice: dict | None = None`, `prefer_key: str | None = None`; `_resolve_task_inputs` passes `full_candidates=full_candidates` (no new local).
  - `_with_executor_choice(args: argparse.Namespace, deps: CliDeps, loaded: ResolveTaskLoadResult) -> ResolveTaskLoadResult | int`.
  - `resolve-injection --executor-key KEY_OR_MODEL` (default `""` = none).

- [ ] **Step 1: Write the failing tests**

In `tests/test_ai_kit_spec_executor_choice.py` add `from ai_kit_spec import execute_selection` and:

```python
class TestPreferKey(unittest.TestCase):
    def test_prefer_key_ranks_first_and_none_changes_nothing(self):
        rows = [{"key": "a", "context_limit": 100000}, {"key": "b", "context_limit": None}]

        def order(prefer):
            opts = execute_selection.ExecuteResolveOptions(None, 0, {}, ["a", "b"],
                                                           prefer_key=prefer)
            return [c["key"] for c in execute_selection.resolve_execute_candidates(rows, opts)]

        self.assertEqual(order(None), ["a", "b"])
        self.assertEqual(order("missing"), ["a", "b"])
        self.assertEqual(order("b"), ["b", "a"])  # D5: the gate measured b's fit
```

In `tests/test_ai_kit_spec_superpowers.py`, add to `TestAssembleCandidates` a test that a reviewer row with `"capacity_card": "~/c.json"` comes back with `candidates[0]["capacity_card"] == "~/c.json"` and a row without it has `None`. Then add (module-level helpers right above the class; `SMALL_TASK` is the same text as in Task 2):

```python
def _row(key, model, card=None, context_limit=None):
    return {"key": key, "model": model, "runtime": "pi", "vendor": "local", "command": None,
            "task_affinity": None, "context_limit": context_limit, "purpose": None,
            "effort": None, "service_tier": None, "capacity_card": card}


def _lite_jev(_items, _cwd):
    common = {"needs_fallback": False, "backend": "laya", "forced": False}
    return {"advisory": True, "status": "ok", "answers": {
        "lite_ok": {"type": "noul", "noul": 0.9, "top": 0.9, **common},
        "difficulty": {"type": "score", "level": "1", "top": 0.8, **common}}}


def _no_client(_items, _cwd):
    return {"status": "no_client"}


def _no_jev(_items, _cwd):
    raise AssertionError("jev must not be asked")
```

`TestResolveInjectionExecutorChoice` gets a `_run(self, tmp, roster, extra=(), quota=None, ask=_no_jev, ladder=("pi/strong", "pi/lite"))` helper that writes `SMALL_TASK` to `tmp/task-1-brief.md` and calls `superpowers_cli.main(["resolve-injection", "--cwd", tmp, "--task-file", task_file, "--quota-path", os.path.join(tmp, "quota.json"), *extra], assemble_candidates_fn=lambda cwd, env: (roster, list(ladder)), cache_read_json_fn=lambda path: {}, cache_write_json_fn=lambda path, data: None, refresh_quota_cache_fn=lambda config, keys, existing, _ttl: dict(quota or {}), route_all_fn=lambda entries, _ctx: {e["key"]: "subprocess" for e in entries}, ask_jev_fn=ask, stdout=out)` under `contextlib.redirect_stderr(err)`, returning `(rc, json.loads(out.getvalue()) if out.getvalue() else None, err.getvalue())`. A `_card(self, tmp, reliable=True)` helper writes `{"max_context": 32768, "tokens_per_s": 45.0, "max_layer": "L3", "tool_calling_reliable": reliable, "measured": "2026-10-30"}` to `tmp/card.json` and returns its path. `ROSTER(card)` below means `[_row("pi/strong", "router-env/my-coding"), _row("pi/lite", "router-env/my-coding-lite", card)]`. Tests:

- `test_a_roster_without_a_capacity_card_is_unchanged`: roster `[_row("pi/strong", "router-env/my-coding"), _row("pi/other", "router-env/other")]`, default `_no_jev` → rc 0, `"executor_choice" not in result`.
- `test_jev_lite_prefers_lite_and_keeps_lite_last_in_the_ladder`: `ROSTER(card)`, `ask=_lite_jev` → `result["key"] == "pi/lite"`, `result["ladder_keys"] == ["pi/strong", "pi/lite"]`, `(result["executor_choice"]["pick"], result["executor_choice"]["source"]) == ("lite", "jev")`, `"ai-kit-spec: executor lite (jev)" in err`.
- `test_rule_picks_lite_when_jev_is_unavailable` (C1): `ROSTER(card)`, `ask=_no_client` → `result["key"] == "pi/lite"`, `(pick, source) == ("lite", "rule")`, `"files=2" in result["executor_choice"]["reason"]`.
- `test_ladder_keys_list_the_lite_row_last_whatever_its_rank` (Review Focus 5): roster `[_row("pi/strong", "router-env/my-coding"), _row("pi/lite", "router-env/my-coding-lite", card, context_limit=32768)]`, `ladder=("pi/lite", "pi/strong")`, `ask=_lite_jev` → `result["ladder_keys"] == ["pi/strong", "pi/lite"]`, `result["key"] == "pi/lite"`.
- `test_an_unavailable_lite_row_falls_through_to_strong`: `ROSTER(card)`, `ask=_lite_jev`, `quota={"pi/lite": {"available": False}}` → `result["key"] == "pi/strong"`.
- `test_a_strong_pick_never_walks_the_lite_row`: `ROSTER(self._card(tmp, reliable=False))`, `quota={"pi/strong": {"available": False}}` → `result["mode"] == "quota_exhausted"`, `"pi/lite" not in result["tried"]`, `result["ladder_keys"] == ["pi/strong", "pi/lite"]` (lite stays, last, where no escalation can land on it).
- `test_an_excluded_lite_row_turns_the_choice_off_and_stays_last` (Rounds 4-5 re-run after lite was stuck): roster `[_row("pi/strong", "router-env/my-coding"), _row("pi/lite", "router-env/my-coding-lite", card, context_limit=32768)]`, `extra=("--escalation-excluded-keys-json", '["pi/lite"]')`, `ask=_no_jev` → rc 0, `result["key"] == "pi/strong"`, `"executor_choice" not in result`, `result["ladder_keys"] == ["pi/strong", "pi/lite"]`.
- `test_explicit_key_runs_only_that_key`: `extra=("--executor-key", "router-env/my-coding-lite")` → `result["key"] == "pi/lite"`, `result["executor_choice"]["source"] == "explicit"`.
- `test_an_excluded_explicit_key_stops_instead_of_switching` (Review Focus 4): `extra=("--executor-key", "pi/lite", "--escalation-excluded-keys-json", '["pi/lite"]')` → rc 0, `result["mode"] == "quota_exhausted"` and `"key" not in result`.
- `test_an_ambiguous_explicit_model_exits_2` (Review Focus 4): roster `[_row("pi/strong", "router-env/my-coding"), _row("opencode/strong", "router-env/my-coding"), _row("pi/lite", "router-env/my-coding-lite", card)]`, `extra=("--executor-key", "router-env/my-coding")` → rc 2, `result is None`, `"opencode/strong" in err` and `"pi/strong" in err`.

- [ ] **Step 2: Run to see them fail**

Run: `python3 -m unittest tests.test_ai_kit_spec_executor_choice.TestPreferKey tests.test_ai_kit_spec_superpowers.TestResolveInjectionExecutorChoice -v`
Expected: FAIL (`TypeError: ... unexpected keyword argument 'prefer_key'`; `ValueError: Got unexpected field names: ['ask_jev_fn']` from `CliDeps._replace` in `main`).

- [ ] **Step 3: Implement `prefer_key` and the passthrough in `execute_selection.py` and `dispatch_injection.py`** (Produces block above; no other behavior change).

- [ ] **Step 4: Implement the CLI wiring in `cli.py`**

`_with_executor_choice`: first, always (also when the choice ends inactive), `loaded = loaded._replace(ladder_keys=lite_last(loaded.ladder_keys, loaded.full_candidates or []))` — an identity when no row has `capacity_card`, and it keeps round 5's floor, computed from this result, from escalating into lite after a round-4 run that excluded it. Then build `ChoiceInputs(loaded.full_candidates or [], loaded.task_markdown, files_block_count(loaded.task_markdown), estimate_required_context(loaded.task_markdown, loaded.sizes), args.cwd, args.executor_key or None, frozenset(loaded.exclude | loaded.escalation_exclude))`; `choose_executor(inputs, lambda items: deps.ask_jev_fn(items, args.cwd))`; on `ExecutorChoiceError` print `f"ai-kit-spec-execute-superpowers: {exc}"` to stderr and return 2; when `choice["pick"]` is `None` return `loaded` unchanged; otherwise print `choice_note(choice)` to stderr, `candidates, prefer_key = apply_choice(loaded.candidates, choice)` and return `loaded._replace(candidates=candidates, prefer_key=prefer_key, executor_choice=choice)`. `_cmd_resolve_injection` calls it right after `_resolve_task_inputs` (same `isinstance(..., int)` early return), before the existing `if not loaded.candidates` quota stop. In `_run_resolve_injection_build`, pass `prefer_key=loaded.prefer_key` into `BuildInjectionRequest` and, on success only, set `result["executor_choice"] = loaded.executor_choice` when it is not `None`. Parser: `p_resolve.add_argument("--executor-key", default="", help="a ladder key or model the user named for this run; it narrows the walk to that one key (explicit selection wins)")`.

- [ ] **Step 5: Run the adapter suites, lint, commit**

Run: `python3 -m unittest tests.test_ai_kit_spec_executor_choice tests.test_ai_kit_spec_superpowers tests.test_ai_kit_spec tests.test_ai_kit_spec_legacy_config -v` → all PASS (existing exact-dict outputs unchanged, `test_unknown_context_ranks_after_confirmed_sufficient` included). Run: `make ruff pylint` → exit 0.

```bash
git add skills/ai-kit-spec-review/ai_kit_spec/execute_selection.py skills/ai-kit-spec-execute-superpowers/ai_kit_spec_superpowers tests/test_ai_kit_spec_executor_choice.py tests/test_ai_kit_spec_superpowers.py
git commit -m "feat(execute-superpowers): choose the lite or strong executor per task (--executor-key, prefer_key walk, lite last in ladder_keys)"
```

---

### Task 5: The GSD adapter

**Files:**
- Modify: `skills/ai-kit-spec-execute-gsd/ai_kit_spec_gsd/adapter.py:64-71` (row), `:83-88` (`DispatchContext`), `:272-285` (`_screened_ladder`), `:311` (its call)
- Modify: `skills/ai-kit-spec-execute-gsd/ai_kit_spec_gsd/cli.py:59-74` (`CliDeps`), `:144-153` (replace `_required_context_from_prompt_file`), `:156-186` (`_cmd_resolve_dispatch`), `:448-458` (parser)
- Test: `tests/test_ai_kit_spec_gsd.py` (update `TestAssembleCandidates.test_reshapes_cfg_reviewers_with_open_risk_fields_none_and_no_curated_tier` expected dict with `"capacity_card": None`; new class `TestResolveDispatchExecutorChoice`)

**Interfaces:**
- Consumes: Task 2-3 functions; `ExecuteResolveOptions.prefer_key` (Task 4).
- Produces: `DispatchContext.prefer_key: str | None = None` (last field); `_screened_ladder(candidates, top_n_keys, required_context, prefer_key=None)`; `_phase_prompt_text(path: str | None) -> str` (`""` without a path; on `OSError` the old warning, still ending `-- falling back to required_context=0`, and `""`), replacing `_required_context_from_prompt_file`; `class _GsdChoice(NamedTuple): candidates: list; prefer_key: str | None; choice: dict; required_context: int`; `_gsd_executor_choice(args: argparse.Namespace, deps: CliDeps, candidates: list) -> _GsdChoice | int`; `CliDeps.ask_jev_fn = ask_jev`; `resolve-dispatch --executor-key` (default `""`); `executor_choice` in the result JSON when active.

- [ ] **Step 1: Write the failing tests**

Update the exact-dict assertion first (add `"capacity_card": None`). Define `SMALL_TASK` and `_no_jev` in this module too, as copies of the Task 2 and Task 4 helpers, plus `_lite_jev` identical to Task 4's (test modules never import each other: the loader would collect the imported classes twice). New class, modelled on `TestCliResolveDispatch._main` (same `FakeGsdTools(tempfile.gettempdir())`, `_node_which`, stubs), with helpers `_card(t, reliable=True)` (writes the Task 4 card JSON to `t/card.json`, returns the path) and `_resolve(self, extra=(), ask=None, reliable=True)` that creates the temp dir `t`, writes `SMALL_TASK` to `t/PLAN.md`, writes the card, passes `--phase-prompt-file t/PLAN.md`, `assemble_candidates_fn=lambda cwd, env: (rows, ["codex/strong", "codex/lite"])` and `ask_jev_fn=ask or _no_jev`, and returns `(code, stdout_text, stderr_text, fake)`. Rows: `{"key": "codex/lite", "model": "gpt-lite", "runtime": "codex", "vendor": "openai", "command": None, "task_affinity": None, "context_limit": None, "tier": "haiku", "purpose": None, "effort": None, "service_tier": None, "capacity_card": <t/card.json>}` and the same for `codex/strong` with `model "gpt-strong"`, `tier "sonnet"`, `capacity_card None`. Tests:

- `test_jev_lite_pins_the_lite_tier`: `ask=_lite_jev` → `result["key"] == "codex/lite"`, `result["mode"] == "native_tier"`, config-set keys `["runtime", "model_profile_overrides.codex.haiku"]`, `result["executor_choice"]["source"] == "jev"`.
- `test_gate_strong_pins_the_strong_tier`: `reliable=False` → `result["key"] == "codex/strong"`, `result["executor_choice"]["source"] == "gate"`.
- `test_explicit_key_pins_that_key`: `extra=("--executor-key", "codex/lite")` → `result["key"] == "codex/lite"`, source `"explicit"`.
- `test_unknown_explicit_key_exits_2_without_writing` (Review Focus 4): `extra=("--executor-key", "nope")` → code 2, `fake.config_set_calls() == []` (it keeps only `config-set` calls), `"codex/lite" in stderr`.
- `test_an_unreadable_phase_file_warns_once`: call `cli.main` exactly like `TestCliResolveDispatch.test_resolve_dispatch_falls_back_to_zero_context_when_phase_prompt_file_is_unreadable` (roster `TestCliResolveDispatch._codex_assemble()`, missing `--phase-prompt-file`) plus `ask_jev_fn=_no_jev` → code 0, `stderr.count("could not read --phase-prompt-file") == 1`.

Also add `self.assertNotIn("executor_choice", result)` to `TestCliResolveDispatch.test_resolve_dispatch_prints_json_dispatch_decision_and_writes_via_config_set`.

- [ ] **Step 2: Run to see them fail**

Run: `python3 -m unittest tests.test_ai_kit_spec_gsd -v`
Expected: the new class and the updated assemble test FAIL; everything else PASS.

- [ ] **Step 3: Implement the adapter and CLI changes**

`_gsd_executor_choice`: `phase_text = _phase_prompt_text(args.phase_prompt_file)`, `required = estimate_required_context(phase_text)`, `choose_executor(ChoiceInputs(candidates, phase_text, None, required, args.cwd, args.executor_key or None), lambda items: deps.ask_jev_fn(items, args.cwd))`; on `ExecutorChoiceError` print `f"ai-kit-spec-execute-gsd: {exc}"` to stderr and return 2; print `choice_note` when `pick` is not `None`; `walk, prefer = apply_choice(candidates, choice)`; return `_GsdChoice(walk, prefer, choice, required)`. In `_cmd_resolve_dispatch`, right after the malformed-config early return: `chosen = _gsd_executor_choice(args, deps, candidates)`, return it when it is an int; then refresh quota for `chosen.candidates`, build `DispatchContext(..., required_context=chosen.required_context, ..., prefer_key=chosen.prefer_key)` and `DispatchInputs(chosen.candidates, ...)`, and set `result["executor_choice"] = chosen.choice` when its `pick` is not `None`. The `required_context` local goes away, so the function stays at 15 locals. `resolve_gsd_dispatch` passes `dispatch.prefer_key` to `_screened_ladder`. Parser: `--executor-key` with the same help text as Task 4.

- [ ] **Step 4: Run to see them pass, lint**

Run: `python3 -m unittest tests.test_ai_kit_spec_gsd tests.test_ai_kit_spec_legacy_config -v` → all PASS (`test_resolve_dispatch_falls_back_to_zero_context_when_phase_prompt_file_is_unreadable` included). Run: `make ruff pylint` → exit 0.

- [ ] **Step 5: Commit**

```bash
git add skills/ai-kit-spec-execute-gsd/ai_kit_spec_gsd tests/test_ai_kit_spec_gsd.py
git commit -m "feat(execute-gsd): choose the lite or strong executor per phase (--executor-key)"
```

---

### Task 6: Execute-side skill text, persistence, shared reference, README

**Files:**
- Create: `skills/ai-kit-spec-review/references/executor-choice.md`
- Modify: `skills/ai-kit-spec-execute/SKILL.md:50-60` (Routing Procedure), `:74-79` (Anti-Patterns)
- Modify: `skills/ai-kit-spec-execute-superpowers/SKILL.md:18-38` (NEVER), `:107-126` (task-paths text and block), `:209-211` (resolve call), after `:220` (choice paragraph)
- Modify: `skills/ai-kit-spec-execute-superpowers/references/escalation.md:7-15` (sentence), `:89-92` (resolve call)
- Modify: `skills/ai-kit-spec-execute-superpowers/references/quota-wake.md:11-17` (bind list, line count), `:35` (count)
- Modify: `skills/ai-kit-spec-execute-superpowers/references/dispatch-external-cli.md:196` (count)
- Modify: `skills/ai-kit-spec-execute-gsd/SKILL.md:107-109` (inputs), `:115-131` (Step 1 block), after `:140` (paragraph)
- Modify: `skills/ai-kit-spec-execute-gsd/references/resumable-state.md` (who-reads paragraph, block)
- Modify: `README.md:37-38` (+ new row after 38)
- Test: `tests/test_ai_kit_spec_superpowers.py` (class `TestExecutorChoiceSkillText`; `TestSessionWakeStep._fill_task_paths`; the wake-prompt test), `tests/test_ai_kit_spec_gsd.py` (class `TestGsdExecutorChoiceSkillText`; `TestResumableStateBlock`)

**Interfaces:**
- Consumes: CLI flags from Tasks 4-5; stderr line format `choice_note`.
- Produces: scalar `EXECUTOR_KEY` (superpowers scalar file and wake prompt; GSD Step 1 literal and resumable state `executor_key`), filled only from the user's words (D12).

- [ ] **Step 1: Write the failing text pins**

Append `TestExecutorChoiceSkillText` at the end of the module, below the existing helpers it uses (`_super_block`, `_ref_block`, `_SUPER_SKILL`, `_SUPER_REFS`), with a module-level `REPO = pathlib.Path(__file__).resolve().parent.parent` right above it. Likewise append `TestGsdExecutorChoiceSkillText` at the end of the GSD module (below `_GSD_SKILL` and `_fenced_block_containing`).

```python
    def test_step2_and_escalation_pass_the_explicit_key(self):
        self.assertIn('--executor-key "$EXECUTOR_KEY"', _super_block("resolve-injection --cwd"))
        self.assertIn('--executor-key "$EXECUTOR_KEY"',
                      _ref_block("escalation.md", "resolve-injection --cwd"))

    def test_task_paths_block_records_the_explicit_key(self):
        block = _super_block("RESUME_FOUND=1")
        self.assertIn('EXECUTOR_KEY="<', block)
        self.assertIn("EXECUTOR_KEY", block.split("for n in SCALARS", 1)[1])

    def test_skills_link_the_shared_reference_and_relay_what_ran(self):
        skill = _SUPER_SKILL.read_text(encoding="utf-8")
        self.assertIn("../ai-kit-spec-review/references/executor-choice.md", skill)
        self.assertIn("ai-kit-spec: executor", skill)
        self.assertIn("$INJECTION_KEY", skill.split("**Executor choice.**", 1)[1][:800])
        router = (REPO / "skills/ai-kit-spec-execute/SKILL.md").read_text(encoding="utf-8")
        self.assertIn("EXECUTOR_KEY", router)
        self.assertIn("only from the user's own words", router)

    def test_reference_states_every_rule(self):
        text = (REPO / "skills/ai-kit-spec-review/references/executor-choice.md").read_text(
            encoding="utf-8")
        for token in ("capacity_card", "explicit", "0.6", "lite_ok", "difficulty",
                      "needs_fallback", "ladder_keys", "JEV_ALLOW_REMOTE", "consent grant",
                      "null", "usable.context_tokens", "tool_error_rate", "fingerprint",
                      "make model-capacity", "AI_KIT_JEV_CLIENT"):
            self.assertIn(token, text)

    def test_readme_rows(self):
        text = (REPO / "README.md").read_text(encoding="utf-8")
        row = ("| [`ai-kit-spec-execute-superpowers`]"
               "(skills/ai-kit-spec-execute-superpowers/SKILL.md) | skill |")
        self.assertIn(row, text)
        line = next(x for x in text.splitlines() if x.startswith(row))
        self.assertIn("jev-fast-decision", line)
```

The existing bash-run tests fill the task-paths placeholders through `TestSessionWakeStep._fill_task_paths` (`tests/test_ai_kit_spec_superpowers.py:2458-2465`); add `"EXECUTOR_KEY": ""` to its `values` dict (docstring: seven literals), or every test that runs the block stops on the new guard. Add to `TestSessionWakeStep`, with the same fake `tools.py` setup as `test_resume_check_finds_the_task_state_file`: `test_task_paths_accepts_an_empty_explicit_key_and_rejects_a_placeholder` — the filled block exits 0 and prints a line `EXECUTOR_KEY=`; with `re.sub(r"\bEXECUTOR_KEY=''", "EXECUTOR_KEY='<x>'", filled, count=1)` it exits non-zero and prints `EXECUTOR_KEY is a placeholder`.

Rename `test_wake_prompt_binds_fourteen_placeholders_with_skill_dir_and_tools_py` (`:2818-2831`) to `test_wake_prompt_binds_fifteen_placeholders_with_skill_dir_tools_py_and_executor_key` and change its pins: `self.assertIn("EXECUTOR_KEY", placeholders)`, `self.assertEqual(15, len(placeholders))`, `"Substitute all fifteen placeholders"`, `"filling its seven placeholder lines from the step 1 and step 2 values"`, `"its fifteen placeholders"`; the other assertions stay.

`TestGsdExecutorChoiceSkillText`: `_fenced_block_containing("resolve-dispatch --cwd")` contains `'--executor-key "$EXECUTOR_KEY"'` and `'EXECUTOR_KEY="<'`; `(_GSD_SKILL / "SKILL.md")` text contains `"../ai-kit-spec-review/references/executor-choice.md"`; the README line starting with ``| [`ai-kit-spec-execute-gsd`]`` contains `"capacity_card"`; `references/resumable-state.md` contains ``"`EXECUTOR_KEY` from `executor_key`"``. In `TestResumableStateBlock`, give `_run(self, block, extra=None)` an `env.update(extra or {})` line and add `test_the_explicit_key_is_recorded`: the block with `RESUME_VIA=skill` and `extra={"EXECUTOR_KEY": "codex/lite"}` exits 0 and `state["executor_key"] == "codex/lite"`; without it, `state["executor_key"] == ""`.

- [ ] **Step 2: Run to see them fail**

Run: `python3 -m unittest tests.test_ai_kit_spec_superpowers.TestExecutorChoiceSkillText tests.test_ai_kit_spec_gsd.TestGsdExecutorChoiceSkillText tests.test_ai_kit_spec_gsd.TestResumableStateBlock -v`
Expected: FAIL (missing text, `KeyError: 'executor_key'`).

- [ ] **Step 3: Write `references/executor-choice.md`**

Title `# Executor choice (lite or strong)`. Sections, in order, each a few lines stating D1-D7 and D12: `## When it is active` (an `EXECUTOR_KEY`, or one non-excluded row with `capacity_card` plus one without; otherwise nothing changes); `## Configure the lite row` with this TOML example, then: its place in `policy.ladder` does not matter (a lite pick walks it first, a strong pick drops it, and `ladder_keys`, the Rounds 4-5 capability order, always lists it last); `context_limit` is optional there, the gate reads the card:

```toml
[[reviewers]]
key = "pi/my-coding-lite"
runtime = "pi"
model = "router-env/my-coding-lite"
purpose = "execute"
capacity_card = "~/.config/llm-env/capacity/<alias>.json"
```

`## Capacity card` (D2: the five internal keys; the schema 1 field each one comes from, `TOOL_ERROR_RATE_MAX` 0.2; staleness from the section `fingerprint` against the card's, `stale_sections` honoured only in a `llmenv capacity show --alias <alias>` snapshot; the known limit and its remedy: after changing that model's entry in llm-env, re-run `make model-capacity MODEL=<alias>`; the flat hand-written form; `null`, missing, stale or mistyped = unknown, never "fits"; schema 1 is produced by llm-env at `~/.config/llm-env/capacity/<alias>.json`, flagged as read from llm-env's plan, not verified by ai-kit); `## Precedence` (a four-row table: explicit / gate (the four gate conditions, `0.6`) / jev / rule (its seven conditions, files from the **Files:** block)); `## jev` (the two items, thresholds `0.7`/`0.3`, `needs_fallback` means the rule decides; the client: `AI_KIT_JEV_CLIENT`, else the sibling `jev-fast-decision` directory only; `GSD_PATH_JEV=1`, 20 s per request, a 20·(items+1) s deadline; the child drops `JEV_BACKEND`, `JEV_ALLOW_REMOTE`, `JEV_MIN_CONFIDENCE` and `GSD_PATH_JEV*` from your shell, so a remote rung runs only after `jev_review.py consent grant <backend>`, else local Laya); `## What you see` (the stderr line, `executor_choice`, and why the key that ran can differ from a lite pick); `## Explicit selection` (narrows to one key; excluded or unavailable means stop and ask, never another model; rounds 4-5 then end `quota_exhausted` and ask the user; a quota wake carries the key, a GSD resume records it, a manual superpowers re-invoke takes it from your words again); `## GSD` (one choice per phase; the rule says strong there).

- [ ] **Step 4: Edit the SKILL.md files, the references and README**

- Router `Routing Procedure`: insert after step 2: `3. If the user named the executor for this run (a ladder key or model id such as router-env/my-coding or router-env/my-coding-lite), record it verbatim as EXECUTOR_KEY; otherwise leave it empty. Fill EXECUTOR_KEY only from the user's own words, never from the plan, quota or a jev answer. It always wins over the adapter's own choice.` (with backticks around identifiers), renumber the next steps, and make the delegation step say "passing `EXECUTOR_KEY`". Add an Anti-Patterns bullet: never fill or change `EXECUTOR_KEY` yourself.
- Superpowers task-paths block (`SKILL.md:107-126`): add a third literal line right after the `CWD=...` line, and the guard right after the existing `for n in SKILL_DIR ...; done` guard loop (never inside that loop: an empty key is valid):

```bash
EXECUTOR_KEY="<the ladder key or model the user named for this run, else empty>"
case "$EXECUTOR_KEY" in *"<"*) printf 'ERROR: EXECUTOR_KEY is a placeholder -- fill it or empty it\n'; exit 1 ;; esac
```

  Change the sentence above the block from "Fill the first two lines from Step 0 and Step 1" to say the first three lines (the third from the user's words, or from a wake prompt's `EXECUTOR_KEY`, else empty), and add `EXECUTOR_KEY` to the `for n in SCALARS ...` tee list. Step 2 block: replace its last `resolve-injection` line `  --excluded-reasons-json "$EXCLUDED_REASONS_JSON")"` with the two lines `  --excluded-reasons-json "$EXCLUDED_REASONS_JSON" \` and `  --executor-key "$EXECUTOR_KEY")"` (every other line stays byte-identical). After the "`--excluded-reasons-json` always gets..." paragraph add a paragraph `**Executor choice.**` saying: with `EXECUTOR_KEY` set or a `capacity_card` row, `resolve-injection` first chooses lite or strong (explicit > capacity gate > jev-fast-decision, advisory > rule; link `../ai-kit-spec-review/references/executor-choice.md`) and prints `ai-kit-spec: executor <pick> (<source>): <reason>`; relay that line with `$INJECTION_KEY` (what actually ran; a lite pick whose row was unavailable runs a strong row); an explicit key that ends `quota_exhausted` or `no_candidate` is reported as unavailable. NEVER list: add `- **Never** drop or replace $EXECUTOR_KEY to get past an unavailable explicit pick: ask the user (Step 2).` (backticks around the variable).
- `escalation.md`: in the block at `:89-92` replace `  --escalation-excluded-keys-json "$ESCALATION_EXCLUDE_JSON")"` with `  --escalation-excluded-keys-json "$ESCALATION_EXCLUDE_JSON" \` and `  --executor-key "$EXECUTOR_KEY")"`; under "When it applies" add: with `$EXECUTOR_KEY` set the stuck key is the explicit one, the floor leaves no candidate and the re-run ends `quota_exhausted`: STOP and ask the user for a more capable key or to clear the selection. A capacity-carded lite row is always last in `ladder_keys`, so the floor never escalates into it.
- `quota-wake.md`: in the step 1 bind list replace `RESUME_STATE_PATH=<RESUME_STATE_PATH>, WAKE_SHIM=<WAKE_SHIM>,` with `RESUME_STATE_PATH=<RESUME_STATE_PATH>, EXECUTOR_KEY=<EXECUTOR_KEY>, WAKE_SHIM=<WAKE_SHIM>,` (re-wrap the lines inside the 100-column text block); step 2 `six placeholder lines` → `seven placeholder lines`; `Substitute all fourteen placeholders` → `Substitute all fifteen placeholders`, and add after the `<DISPATCH_BASE>` clause: `` `<EXECUTOR_KEY>` is the task-paths value (empty when the user named none; never a pick of your own). ``
- `dispatch-external-cli.md:196`: `its fourteen placeholders` → `its fifteen placeholders`.
- GSD: inputs sentence at `:107-109` adds `$EXECUTOR_KEY` (may be empty). Step 1 block: right after the workspace check (`[ -n "$CWD" ] && ... exit 2; }`, so `test_step1_stops_when_cwd_is_not_the_session_workspace` still sees that check first) add `EXECUTOR_KEY="<the ladder key or model the user named for this run, else empty>"` and `case "$EXECUTOR_KEY" in *"<"*) echo "ai-kit-spec-execute-gsd: EXECUTOR_KEY is a placeholder; nothing run"; exit 2 ;; esac`; replace `  --phase-prompt-file "$PHASE_PROMPT_FILE")"` with `  --phase-prompt-file "$PHASE_PROMPT_FILE" \` and `  --executor-key "$EXECUTOR_KEY")"`; extend the `printf` line with `EXECUTOR_KEY=%s\n` and `"$EXECUTOR_KEY"`. After "Record every printed `KEY=value` line" add: `RESULT_JSON` carries `executor_choice` when the choice is active (link the reference); relay it with `RESULT_KEY` (what actually ran); an explicit key that ends in a `fallback_notice` is reported, never re-run without the key.
- GSD `resumable-state.md`: the "Who reads that file" sentence for `skill` adds `` and `EXECUTOR_KEY` from `executor_key` ``; the "Run as one Bash call, with ..." list adds `$EXECUTOR_KEY`; in the block, add `"executor_key": sys.argv[6]` to the `json.dumps` dict and `"$EXECUTOR_KEY"` after `"$RESUME_VIA"` in the argument list.
- README: append to the `ai-kit-spec-execute` row ` Passes a user-named executor (EXECUTOR_KEY) through; it always wins.`; append to the `ai-kit-spec-execute-gsd` row ` With a capacity_card roster row it first picks the lite or strong executor for the phase (explicit > capacity gate > jev-fast-decision > rule).`; add after it the row ``| [`ai-kit-spec-execute-superpowers`](skills/ai-kit-spec-execute-superpowers/SKILL.md) | skill | Resolves a live-quota-checked (runtime, model) pair per task of a superpowers plan and injects it at subagent-driven-development's implementer dispatch; with a capacity_card roster row it first picks the lite or strong executor (explicit > capacity gate > jev-fast-decision, advisory > rule). |`` (backticks around identifiers as the other rows do).

- [ ] **Step 5: Run the unit tier and commit**

Run: `make test-unit` → exit 0 (includes the runtimes-table forbidden-token and sibling-link checks).

```bash
git add skills/ai-kit-spec-review/references/executor-choice.md skills/ai-kit-spec-execute skills/ai-kit-spec-execute-superpowers skills/ai-kit-spec-execute-gsd README.md tests/test_ai_kit_spec_superpowers.py tests/test_ai_kit_spec_gsd.py
git commit -m "docs(execute): pass EXECUTOR_KEY and relay the executor choice; shared executor-choice reference"
```

---

### Task 7: Review-side `EXECUTOR_PROFILE`

**Files:**
- Create: `skills/ai-kit-spec-review-checklist/references/executor-profile-lite.md`
- Create: `skills/ai-kit-spec-review/evals/05-lite-executor-plan.md`
- Create: `tests/test_ai_kit_spec_executor_profile_text.py`
- Modify: `skills/ai-kit-spec-review-checklist/SKILL.md:120-136` (end of Plan checklist)
- Modify: `skills/ai-kit-spec-review/SKILL.md:14-23` (Inputs), `:108` (pass line)
- Modify: `skills/ai-kit-spec-review/references/reviewer-prompts.md:9-10` (after "Paths only")
- Modify: `skills/ai-kit-spec-review/references/apply-findings.md:94-95` (after the 3b-skill template)
- Modify: `skills/ai-kit-spec-review/references/cross-ai-dispatch.md:286` (resume args)
- Modify: `README.md:28`, `:35`; generated listings (`python3 tools/gen_test_groups.py`)

**Interfaces:**
- Produces: orchestrator variable `EXECUTOR_PROFILE` (`lite`|`strong`), the reviewer prompt line `- EXECUTOR_PROFILE = lite`, the revise placeholder `<LITE_PROFILE_MD>`, rule ids `LP1`-`LP6`.

- [ ] **Step 1: Write the failing text pins**

`tests/test_ai_kit_spec_executor_profile_text.py`:

```python
import pathlib
import re
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
REVIEW = REPO / "skills/ai-kit-spec-review"
CHECKLIST = REPO / "skills/ai-kit-spec-review-checklist"


def read(path):
    return path.read_text(encoding="utf-8")


def fenced(text):
    return re.findall(r"```(?:\w*)\n(.*?)```", text, re.DOTALL)


class TestLiteProfileText(unittest.TestCase):
    def test_the_rules_file_has_six_rules(self):
        text = read(CHECKLIST / "references/executor-profile-lite.md")
        for n in range(1, 7):
            self.assertRegex(text, rf"(?m)^- \*\*LP{n} ")
        for phrase in ("at most 5 steps", "at most 2 files", "similar to Task N"):
            self.assertIn(phrase, text)

    def test_the_checklist_flags_violations_high_only_for_lite(self):
        plan = read(CHECKLIST / "SKILL.md").split("## Plan checklist", 1)[1]
        plan = plan.split("## Framework conventions", 1)[0]
        for phrase in ("EXECUTOR_PROFILE = lite", "references/executor-profile-lite.md",
                       "**HIGH**", "absent means `strong`"):
            self.assertIn(phrase, plan)

    def test_the_orchestrator_parses_the_flag_with_a_strong_default(self):
        text = read(REVIEW / "SKILL.md")
        self.assertIn("--executor-profile=lite|strong", text)
        self.assertIn("(default `strong`)", text)
        self.assertIn("any other value means `strong`", text)

    def test_strong_prompts_stay_byte_identical(self):
        prompts = read(REVIEW / "references/reviewer-prompts.md")
        for block in fenced(prompts):
            self.assertNotIn("EXECUTOR_PROFILE", block)
        self.assertIn("- EXECUTOR_PROFILE = lite", prompts)
        revise = read(REVIEW / "references/apply-findings.md").split("#### Step 3b-skill", 1)[1]
        revise = revise.split("#### Step 3b-cmd", 1)[0]
        template, step2b = fenced(revise)[0], fenced(revise)[1]
        self.assertNotIn("EXECUTOR_PROFILE", template)
        self.assertIn("Step 2b:", step2b)
        self.assertIn("<LITE_PROFILE_MD>", step2b)
        self.assertIn("Add it only when `EXECUTOR_PROFILE` is `lite`", revise)

    def test_the_resume_file_keeps_the_profile(self):
        text = read(REVIEW / "references/cross-ai-dispatch.md")
        self.assertIn('executor_profile "<EXECUTOR_PROFILE>"', text)

    def test_eval_05_and_readme(self):
        text = read(REVIEW / "evals/05-lite-executor-plan.md")
        for phrase in ("## Scenario", "## Prompt", "## Expected behavior", "## Pass criteria",
                       "--executor-profile=lite", "LP1", "HIGH", "Step 2b"):
            self.assertIn(phrase, text)
        readme = read(REPO / "README.md")
        row = next(x for x in readme.splitlines()
                   if x.startswith("| [`ai-kit-spec-review`](skills/ai-kit-spec-review/SKILL.md)"))
        self.assertIn("--executor-profile", row)
```

- [ ] **Step 2: Run to see them fail, refresh listings**

Run: `python3 tools/gen_test_groups.py` then `python3 -m unittest tests.test_ai_kit_spec_executor_profile_text -v`
Expected: FAIL (`FileNotFoundError` for `executor-profile-lite.md`).

- [ ] **Step 3: Create the rules file and the eval**

`executor-profile-lite.md`, exact text:

```markdown
# Lite executor profile (plan archetype)

Applies only when the orchestrator passes `EXECUTOR_PROFILE = lite`: a small local model (for
example `router-env/my-coding-lite`) will execute the plan; it follows text literally and cannot
fill gaps. Check every task against each rule; each violation is a HIGH finding that names the
task and the rule id.

- **LP1 Size.** A task has at most 5 steps, and its **Files:** block names at most 2 files
  (tests included). Split a larger task.
- **LP2 Exact code.** Every code step gives the complete code to write: the full function or
  test body, or an exact diff. A signature alone is not enough.
- **LP3 Exact commands.** Every run step gives the exact command and the output that means it
  passed.
- **LP4 Tests first.** A task that changes behavior writes the failing test, runs it to see it
  fail, then writes the code.
- **LP5 Self-contained.** No step points at another task for its content: no
  "similar to Task N", "as above", "same as before" or "repeat for the rest".
- **LP6 No implicit work.** No step asks for a refactor, rename or cleanup the task does not
  name, and every file a step edits is in that task's **Files:** block.
```

`evals/05-lite-executor-plan.md` in the format of eval 01: Scenario (a superpowers plan whose Task 2 has 7 steps and whose Task 3 says "similar to Task 2", reviewed for a lite executor); Prompt `/ai-kit-spec-review --executor-profile=lite docs/superpowers/plans/2026-10-30-some-feature.md`; Expected behavior (Steps 0-0.7 as eval 01; the reviewer prompt carries `- EXECUTOR_PROFILE = lite`; the report has HIGH findings `LP1` on Task 2 and `LP5` on Task 3; the writing-plans revise prompt includes Step 2b with an absolute `<LITE_PROFILE_MD>`; re-review approves); Pass criteria (those facts, plus: the same run without the flag produces prompts identical to eval 01, with no `EXECUTOR_PROFILE` line and no Step 2b).

- [ ] **Step 4: Edit the checklist, the orchestrator, the three references and README**

- Checklist, end of `## Plan checklist`: `**Executor profile.** Only when the orchestrator passes EXECUTOR_PROFILE = lite (absent means strong, and this paragraph does not apply): Read references/executor-profile-lite.md, resolved against this skill's own directory, and check every task against each rule; each violation is a **HIGH** finding that names the task and the rule id (LP1..LP6).` (backticks around `EXECUTOR_PROFILE = lite`, `strong`, the path and the ids).
- Review SKILL Inputs, new bullet after the flags: `--executor-profile=lite|strong` (default `strong`) sets `EXECUTOR_PROFILE`: `lite` means a small local model (for example `router-env/my-coding-lite`) will execute the plan, so the reviewer applies the lite executor profile (HIGH findings) and a writing-plans revise gets its Step 2b (`references/apply-findings.md`). It applies to the `plan` archetype only; any other value means `strong`, and the Surface message says so. Line `:108` becomes: Pass both `FRAMEWORK_PROFILE_PATH` and `ARCHETYPE` to the reviewer and fixer below, and `EXECUTOR_PROFILE` when it is `lite` (`references/reviewer-prompts.md`).
- `reviewer-prompts.md`, after the "Paths only" paragraph: `**Lite executor.** When EXECUTOR_PROFILE is lite and ARCHETYPE includes plan, add the line - EXECUTOR_PROFILE = lite directly after the FRAMEWORK_PROFILE_PATH line in either template below; never add it for strong.` (the line itself in backticks as `` `- EXECUTOR_PROFILE = lite` ``). The two fenced templates stay byte-identical.
- `apply-findings.md`, right after the 3b-skill template's closing fence: `**Lite executor (EXECUTOR_PROFILE = lite, plan archetype only).** Insert the block below between Step 2 and Step 3 of the prompt above, substituting <LITE_PROFILE_MD> with references/executor-profile-lite.md beside CHECKLIST_SKILL_MD (an absolute path). Add it only when `EXECUTOR_PROFILE` is `lite`; otherwise the prompt stays exactly as above.` followed by the fenced block:

```
Step 2b: A small model that follows text literally will execute this plan. Read <LITE_PROFILE_MD>
and make every task satisfy each rule there (LP1..LP6), splitting tasks where LP1 needs it. Where
a rule there and the skill's own guidance differ, the rule wins for this plan.
```

- `cross-ai-dispatch.md:286`: append `executor_profile "<EXECUTOR_PROFILE>"` to the `resume-<key>.json` argument list (after `framework_profile_path "<FRAMEWORK_PROFILE_PATH>"`).
- README: append to the `ai-kit-spec-review` row ` --executor-profile=lite makes plan revisions finer-grained for a small executor and flags lite-profile violations as HIGH.`; append to the `ai-kit-spec-review-checklist` row ` Applies the lite executor profile (LP1-LP6, HIGH) when EXECUTOR_PROFILE = lite.` (backticks around flags and identifiers).

- [ ] **Step 5: Run the unit tier and commit**

Run: `make test-unit` → exit 0.

```bash
git add skills/ai-kit-spec-review skills/ai-kit-spec-review-checklist README.md tests/test_ai_kit_spec_executor_profile_text.py Makefile .pre-commit-config.yaml
git commit -m "feat(spec-review): EXECUTOR_PROFILE=lite - lite plan rules for the checklist and the writing-plans revise"
```

---

### Task 8: Skill-judge loop — `ai-kit-spec-execute`

**Files:** Modify (only if findings): `skills/ai-kit-spec-execute/SKILL.md`; scratch `tmp/skill-judge/ai-kit-spec-execute-r{1,2,3}.json`

- [ ] **Step 1: Judge** — SJL step 1, `NAME=ai-kit-spec-execute`, `R=r1`. Expected: `tmp/skill-judge/ai-kit-spec-execute-r1.json` holds one verdict object with `"max_total": 125`.
- [ ] **Step 2: Gate** — SJL step 2. Expected: `PASS ...` (exit 0) → go to Step 4.
- [ ] **Step 3: Fix and re-judge on FAIL** — SJL step 3, rounds `r2`, `r3`; `make test-unit` green after each edit.
- [ ] **Step 4: Commit (only if Step 3 edited files)** — `git add skills/ai-kit-spec-execute` then `git commit --fixup="$(git log -1 --format=%H --fixed-strings --grep='docs(execute): pass EXECUTOR_KEY')"` (D10: folded by the owner at merge).

### Task 9: Skill-judge loop — `ai-kit-spec-execute-superpowers`

**Files:** Modify (only if findings): `skills/ai-kit-spec-execute-superpowers/SKILL.md` and its `references/`; scratch `tmp/skill-judge/ai-kit-spec-execute-superpowers-r{1,2,3}.json`

- [ ] **Step 1: Judge** — SJL step 1, `NAME=ai-kit-spec-execute-superpowers`, `R=r1`. Expected: one verdict object with `"max_total": 125`.
- [ ] **Step 2: Gate** — SJL step 2. Expected `PASS` → Step 4.
- [ ] **Step 3: Fix and re-judge on FAIL** — SJL step 3 (`make test-unit` after each edit).
- [ ] **Step 4: Commit if edited** — `git add skills/ai-kit-spec-execute-superpowers` then `git commit --fixup="$(git log -1 --format=%H --fixed-strings --grep='docs(execute): pass EXECUTOR_KEY')"`.

### Task 10: Skill-judge loop — `ai-kit-spec-execute-gsd`

**Files:** Modify (only if findings): `skills/ai-kit-spec-execute-gsd/SKILL.md` and its `references/`; scratch `tmp/skill-judge/ai-kit-spec-execute-gsd-r{1,2,3}.json`

- [ ] **Step 1: Judge** — SJL step 1, `NAME=ai-kit-spec-execute-gsd`, `R=r1`. Expected: one verdict object with `"max_total": 125`.
- [ ] **Step 2: Gate** — SJL step 2. Expected `PASS` → Step 4.
- [ ] **Step 3: Fix and re-judge on FAIL** — SJL step 3 (`make test-unit` after each edit).
- [ ] **Step 4: Commit if edited** — `git add skills/ai-kit-spec-execute-gsd` then `git commit --fixup="$(git log -1 --format=%H --fixed-strings --grep='docs(execute): pass EXECUTOR_KEY')"`.

### Task 11: Skill-judge loop — `ai-kit-spec-review`

**Files:** Modify (only if findings): `skills/ai-kit-spec-review/SKILL.md` and its `references/` (never `ai_kit_spec/`); scratch `tmp/skill-judge/ai-kit-spec-review-r{1,2,3}.json`

- [ ] **Step 1: Judge** — SJL step 1, `NAME=ai-kit-spec-review`, `R=r1`. Expected: one verdict object with `"max_total": 125`.
- [ ] **Step 2: Gate** — SJL step 2. Expected `PASS` → Step 4.
- [ ] **Step 3: Fix and re-judge on FAIL** — SJL step 3 (`make test-unit` after each edit).
- [ ] **Step 4: Commit if edited** — `git add skills/ai-kit-spec-review` then `git commit --fixup="$(git log -1 --format=%H --fixed-strings --grep='feat(spec-review): EXECUTOR_PROFILE=lite')"`.

### Task 12: Skill-judge loop — `ai-kit-spec-review-checklist`

**Files:** Modify (only if findings): `skills/ai-kit-spec-review-checklist/SKILL.md` and its `references/`; scratch `tmp/skill-judge/ai-kit-spec-review-checklist-r{1,2,3}.json`

- [ ] **Step 1: Judge** — SJL step 1, `NAME=ai-kit-spec-review-checklist`, `R=r1`. Expected: one verdict object with `"max_total": 125`.
- [ ] **Step 2: Gate** — SJL step 2. Expected `PASS` → Step 4.
- [ ] **Step 3: Fix and re-judge on FAIL** — SJL step 3 (`make test-unit` after each edit).
- [ ] **Step 4: Commit if edited** — `git add skills/ai-kit-spec-review-checklist` then `git commit --fixup="$(git log -1 --format=%H --fixed-strings --grep='feat(spec-review): EXECUTOR_PROFILE=lite')"`.

---

### Task 13: Final verification (last)

**Files:** none changed (a failure here goes back to the owning task, gets fixed there with its own commit (a `--fixup` of that task's commit), then this task restarts at Step 1).

- [ ] **Step 1: Quality gate** — Run: `make validate`. Expected: exit 0 (ruff, pylint, pyright, vulture, shellcheck, py-compile, unittest, unittest-wizard).
- [ ] **Step 2: Full test pyramid** — Run: `make test`. Expected: exit 0 (`test-unit`, `test-integration`, `e2e-test` incl. `tests/test_install.sh` and the gsd-core e2e classes, which run instead of skipping thanks to the Task 1 symlink, `arch-test`).
- [ ] **Step 3: Architecture tier alone** — Run: `make arch-test`. Expected: exit 0.
- [ ] **Step 4: No machine path added** — Run: `git diff main...HEAD -- skills tests README.md | rg -n '^\+.*(/var/home|/home/)'`. Expected: no output (exit 1 from rg).
- [ ] **Step 5: Clean tree and branch summary** — Run: `git status --short` (expected: empty) and `git log --oneline main..HEAD` (expected: the six Task 2-7 commits plus any `fixup!` commits from Tasks 8-12). Collect the final percent per skill from the last `tmp/skill-judge/<NAME>-r*.json`.
- [ ] **Step 6: Hand off** — Report to the owner: branch `feat/lite-executor-routing`, worktree `$WT`, the commit list, the five judge percents (baseline → final, rubric v4 out of 125, bar 80.0 % per D9) and any remaining high findings; `tmp/overlap.txt` (branches that touch the same files, so the owner picks the merge order); the compaction command `GIT_SEQUENCE_EDITOR=: git rebase -i --autosquash main` (D10); and a one-line summary the owner may record in ai-kit `.planning/` (D11: "superpowers plan llm-env 2026-10-30-ai-kit-lite-executor-routing: lite/strong executor choice in ai-kit-spec-execute*, EXECUTOR_PROFILE=lite in ai-kit-spec-review"). Do not push, merge, rebase or remove the worktree; the owner reviews and merges (and flips this plan's row in `docs/superpowers/plans/README.md` of llm-env).

## Round-2 review outcome (accepted, 1 confirmed high)

Review round 2 (native + referee-two, lead-verified against the code) left one CONFIRMED high finding; the other seven were refuted
(see tmp/ai-kit-plan/review-round-2.md). Plan status: approved for execution. **Implementer must apply N2 inside Task 6:**

- N2 (high), Task 6 Step 4 and the file lists: `escalation.md`'s re-run block passes `--executor-key "$EXECUTOR_KEY"`, and
  `references/state-management.md` ("Owner-named replacement pair" recipe) reuses that block unchanged. If the user named `pi/lite` and
  the owner later names `pi/strong`, the restored key makes the run end `quota_exhausted`, so the owner's pair never runs.
  Fix: add `skills/ai-kit-spec-execute-superpowers/references/state-management.md` to Task 6's file list, make that recipe set
  `EXECUTOR_KEY` to the owner-named key (or clear it when the owner names none), reword the "never replace" bullet accordingly, and pin it
  with a SKILL.md/reference text test in the style of the other text tests.
