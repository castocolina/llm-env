# onboard-local-model Navigation Restructure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `.claude/skills/onboard-local-model` score >= 90 % from both skill-judge lanes (native Opus and `router-env/my-referee-two`, rubric v4, 125 points) with no critical or high finding, stably, by fixing the causes of the recurring losses (D8 dead ends and contradictions, D1/D2/D5 duplicated steps and reference chains, D7 host-bound evals) instead of rewording them. On the way, the skill treats `jev-fast-decision` as a black box and the product code it touches stops naming the ai-kit project (owner mandate, 2026-10-31).

**Architecture:** `SKILL.md` becomes a navigation entry: description, one route table keyed on what the user gave (with load and do-NOT-load columns), three cross-route rules, the gates G1-G5 with their facts, the GPU-freeing procedure, NEVER and Gotchas. All step-by-step detail moves into one flow reference per route family; each fact has one owning file; no reference points at another reference (a pytest enforces it). `models-yml.md` is merged into `onboard-flow.md`. Evals become fixture-driven where a script has a fixture seam, and one pytest pins each fixture's script output to the numbers the eval assertions name. `jev-fast-decision` is named only where the skill asks it, always with one black-box sentence; a guard pytest (Task 0, first) keeps its backend knowledge and the ai-kit name out of the skill and the three tracked docs this plan touches.

**Tech Stack:** Markdown, JSON, YAML front matter, bash, jq, yq, pytest (+ xdist), shellcheck.

**Spec:** the owner instructions given to the plan writer on 2026-10-31 (restated in Goal, Global Constraints and Decisions) and the five judge rounds in `tmp/judges/` (`r1-summary.txt` ... `r5-summary.txt`, raw `r5-native.txt`, `r5-referee-two.txt`): native 86.4, 89.6, 92.0, 88.0, 88.8; referee 90.4, 90.4, 87.2, 89.6, 91.2. Recurring losses: D8 10-13/15, D1 16-18/20, D2/D5 13-14/15, D7 11-14/15, D4 14-15. Rubric: `~/.agents/skills/skill-judge/references/rubric.md` (v4). Sibling plans, not edited: `2026-10-31-judge-round-1-fixes.md`, `2026-10-31-audit-fix-wave.md`, `2026-10-31-skill-model-or-none-and-generated-challenges.md`. Owner mandate of 2026-10-31 (jev black box, no ai-kit name, Decisions 6 and 9 decided) with the read-only audit `tmp/goal/jev-agnostic-audit.sh` (Fact 11).

## Global Constraints

- `.claude/skills/` and `tests/skills/` are untracked on purpose: nothing under them is committed. This plan and its row in `docs/superpowers/plans/README.md` stay uncommitted with the index's other pending edits. The only commit is Task 0 Step 8 (three tracked docs); no other task commits.
- Allowed edits: `$SK/SKILL.md`, `$SK/references/*.md`, `$SK/evals/**` (JSON and fixtures), `tests/skills/onboard-local-model/test_onboard_local_model_scripts.py`, and, in Task 0 only, three tracked spots: the repo-root `README.md` interference-gate bullet, the `pylib/interference.py` row of `.agents/architecture.md`, and the `_view` docstring of `pylib/capacity.py`. No file under `$SK/scripts/` or `$SK/challenges/` changes: the one `--help` that names a doc (`bench-fit.sh`: "`references/onboard-flow.md` step 4") stays true because step 4 keeps its name ("Fit and configure") and its scratch-config content.
- Nothing loads the GPU or starts a container; another session is measuring. Never run `bench-fit.sh`, `effort-run.sh`, `latency-profile.sh`, `tune-sweep.sh`, `make benchmark`, or `preflight.sh` (its output is a fixture here).
- Parallel work may touch the same files: before rewriting a file, `diff` it against the Task 0 snapshot and carry over edits you did not make (Task 0's own edits included).
- Kept doc tests stay green: `ISOLATION_SENTENCE` verbatim in `SKILL.md` and absent from `tuning.md`, `scoring.md`, `challenges.md`; `## Gates` (up to `## NEVER`) keeps "permissions bypassed", "announce the facts, then proceed", "deletes user data", "never switch provider or model", "a yes to one never covers the next"; no backtick span with a loop, `; then`, or more than two `&&`/`||`/`;`; every `*.sh` named in `SKILL.md`, `references/*.md` or `evals/*.json` exists; description 1-1024 characters; the Task 0 guard (`-k black_box`).
- **`jev-fast-decision` is a black box** (owner, 2026-10-31; Decision 14). New or rewritten text says only: ask the `jev-fast-decision` skill (follow its own SKILL.md for how to call it); its answers are advisory; `needs_fallback` or no answer means we decide ourselves. It never says how that skill chooses or reaches a backend (local vs remote, Laya as its backend, consent, TypeSafe, probing, env variables, per-item call rules, timeouts), so a change to it is inherited without edits here; nothing in the skill or the product code names the ai-kit project. Every task that writes skill text runs the guard.
- `SKILL.md` <= 11,000 bytes, pinned by a new test (the 14,500 target of the sibling plan was never a test; it is lowered, not raised).
- Pytest: `--basetemp=$HOME/.cache/llm-env-nav-pt` (outside the repo `tmp/`), removed in Task 4. After any `.py` edit: `make validate` plus the task's pytest; `make test` in Task 0 (AGENTS.md rule for its tracked `.py` edit) and Task 4.
- English only. Scratch: `tmp/skill-nav/`. Never delete or rename plans. Commands from the repo root with `SK=.claude/skills/onboard-local-model`.

## Facts (read or measured 2026-10-31)

Line numbers are from the files before Task 0 (its edits shift a few lines by one or two); find passages by their quoted text.

1. `SKILL.md` 145 lines, 14,356 bytes, description 879 characters; it holds two routers (`## Input` routes, `## Pick the mode first`) plus a 10-step workflow whose steps 4-9 summarize `onboard-flow.md` (which says "moved here intact"). References: anchors 3.8K, challenges 17.4K, models-yml 6.6K, onboard-flow 11.0K, runtimes 3.9K, scoring 4.3K, selection-criteria 8.8K, system-one 4.6K, tuning 12.7K.
2. Cross-references between references (the chains judges flag): challenges -> onboard-flow (:5, :181, :189); models-yml -> onboard-flow (:43, :57); onboard-flow -> selection-criteria, models-yml, tuning, challenges, scoring, anchors, runtimes (:5-86); runtimes -> selection-criteria (:40); scoring -> onboard-flow, challenges (:5, :12, :37); selection-criteria -> challenges, anchors, runtimes (:47, :50, :67, :108); tuning -> onboard-flow, challenges (:5, :18, :21, :31, :35, :113, :142).
3. `resolve-target.sh Qwen3.8-27B` exits 2: "MODEL is none of the four accepted forms". No route handles a bare model name.
4. `rank-candidates.sh --evidence` ranks only rows that have a card: a shortlisted row without one is absent from `ranked` and `excluded` (measured on the fixtures of Task 3).
5. `setup/setup-local-llm-agents.sh` writes the router profile (`~/.pi/agent`, provider `router-env`, ids `llama-cpp/<alias>`); the default profile `~/.config/llm-env/pi` is refreshed only if it already exists, never created (`.agents/architecture.md:15`). `2026-10-30-local-combo-and-capacity.md` Decision 8 already runs ladders on the router profile. On this host `router-env/llama-cpp/ornith-15-9b` exists.
6. `make local-combo` "creates or updates" `my-coding-lite` (`.agents/architecture.md:51`), so `tuning.md` Q11 creates the combo even when the standalone quant route skipped T9.
7. `tune-sweep.sh --help`: default grid `--ctx 131072,196608,262144 --kv q5_1,q8_0 --ubatch 512,1024,2048 --fa on`; `tune-pick.sh --min-tg` defaults to 20 (the floor's code owner).
8. `system-one.md` trial step 3 runs a second Laya (steady about 4.26 GB) next to production System One and the served model on a 16 GB card, with no gate.
9. Fixture outputs measured in a scratch directory with the real scripts: `rank-candidates.sh` over the Task 3 scout files -> shortlist `lab-a/Coder-9B-GGUF`, `lab-b/Agent-14B-GGUF`, `lab-d/MoE-30B-A3B-GGUF`; `gates_failed` `lab-c/Coder-27B-IQ2-GGUF` bpw, `lab-e/Short-8B-GGUF` ctx, `lab-f/Dense-32B-GGUF` fit; with the two cards -> ranked `lab-a` 23, `lab-d` 20 (`tool_template` unverified), disagreement `lab-a` `agentic_evidence` card 2 vs jev 0, `lab-b` absent. `hf-scout.sh --repo lab/Foo-9B-GGUF --json --budget-mib 8321 --ram-mib 12000` -> fit `vram`, pick `Foo-9B-Q5_K_M.gguf` 6500 MiB. `system-one-check.sh` -> pinned Laya 0.4.2, torch 2.14.1 `rocm7.2`; latest 0.5.0, `releases_behind` 2 (yanked 0.4.4 not counted); `rocm7.14` 2.15.0. `resolve-target.sh` on the fixture config: `gemma4` -> enabled true, present true; `ornith-35b` -> enabled false, present false.
10. A scratch draft of the Task 2 `SKILL.md` measured 107 lines, 10,219 bytes; its description 811 characters and parses with `yq`.
11. `tmp/goal/jev-agnostic-audit.sh` (read-only) finds `jev-fast-decision` internals at `scoring.md:51-52` ("local Laya, no consent needed", "run its `--probe` first") and `:56` ("One call per item (Laya bleeds state across batched items)"); `selection-criteria.md:101` ("one `type: score` call per level, one call per item"); `tuning.md:46-47`, the repo-root `README.md:255-256` and `.agents/architecture.md:47` (the Laya p95/p50 limits justified by jev client timeouts of about 20 s); `system-one.md:8-9` ("The `/jev-fast-decision` skill's Laya backend"), `:23` ("the Jev client could use") and `:46` ("Run the `/jev-fast-decision` eval cases against both ports"); and the ai-kit name at `pylib/capacity.py:250`. `SKILL.md:34` and `onboard-flow.md:60` name `jev-fast-decision` outside the files that ask it; `evals.json` mentions it only as advisory. Not findings: `scripts/print-endpoints.sh:52` (the user-facing jev env line, pinned by `tests/test_shell.py`) and the `"model":"jev-1"` request bodies (System One's own API). No test pins the README, architecture or capacity lines. `grep -E` of the Task 0 regex over the skill tree and those three files hits exactly these places.
12. The replace rule is already unified in `scoring.md` Score item 6 ("**the one replace rule** (the only statement of it; ...)"); `onboard-flow.md` step 9(b) still restates its numbers and names `scoring.md`, and `SKILL.md` step 9 names it.

## Route walk: dead ends and contradictions (each fixed in the named task)

| # | Route | Finding | Fix |
|---|---|---|---|
| R1 | bare model name | `resolve-target.sh` exit 2, no route (Fact 3) | `selection-criteria.md` Find the repo; route row (Tasks 1, 2) |
| R2 | anchors, no candidate | "score the local model vs Haiku" has no mode row; anchors live only in onboarding step 7 | `anchors.md` becomes the anchor flow; route row (Tasks 1, 2) |
| R3 | served alias | step 5 runs "the baseline ladder" before G2, step 7 says it is "not here", and the served alias has no distinct baseline; its scratch config and budget re-check add nothing | served alias = re-measure, one lane before G2, no scratch config (Task 1) |
| R4 | none/ranking | a row without a card vanishes silently (Fact 4) | report it unresearched (Task 1) |
| R5 | none/ranking | `SKILL.md:32` "findings as extra terms" vs `:72` "add a term only when no row has fit: vram"; `:72` orders shortlist, then read criteria, then research | one ordered procedure (Task 1) |
| R6 | verdict | the replace rule is unified in `scoring.md` (6) (Fact 12), but `onboard-flow.md` step 9(b) restates its numbers and names `scoring.md` (a reference-to-reference pointer) | the rule stays only in `scoring.md` Score (6) (Decision 9); `onboard-flow.md` step 8 names it without numbers or file name (Task 1) |
| R7 | every ladder | default profile not created on a fresh host (Fact 5); profile rule has an anchor exception | one router profile, `SKILL.md` rule 3 (Tasks 1, 2) |
| R8 | quant | "after T9" (`tuning.md:102`) vs "T1-T8, then Q1-Q11"; Q11 runs `make local-combo` unasked (Fact 6); intro "serving the tuned alias" vs Q1's stop-first path | Task 1 |
| R9 | G3 | two backups (`cp -p`, then `make llama-enable`'s) and no statement which one step 9 restores | the `cp -p` copy is the rollback copy (Task 1) |
| R10 | no baseline | gateway-only host with no enabled entry: steps 2 and 9 assume a baseline | verdict without (b)/(c) (Task 1) |
| R11 | all | benchmark-run rules, 20 t/s floor, client profile and GPU-freeing are reached through 2-3 reference hops | `SKILL.md` rules and Freeing the GPU (Tasks 1, 2) |
| R12 | System One | trial container without a gate (Fact 8) | G2 before the trial (Task 1) |
| R13 | tuning | `<axes>` has no default (Fact 7) | Task 1 |
| R14 | entry | `$SK` used at `SKILL.md:16`, defined at `:52` | first line of the body (Task 2) |
| R15 | evals | `anchor-comparison` opens "ok y comparalo" with no antecedent | Task 3 |
| R16 | ranking, ladder, System One, interference | skill text describes how `jev-fast-decision` works (Laya as its backend, consent, `--probe`, per-item calls, client timeouts) and `pylib/capacity.py` names ai-kit (Fact 11) | black box (Decision 14), guard pytest (Task 0) |

## Decisions

1. **Navigation entry.** `SKILL.md` keeps no numbered workflow: the route table replaces `## Input`, `## Pick the mode first` and steps 1-10. Text in Task 2.
2. **One flow file per family:** `onboard-flow.md` (alias, Hugging Face, local GGUF, scout pick), `selection-criteria.md` (bare name, scout and rank, fit math), `tuning.md` (tuning, capacity card, `my-coding-lite`, quants), `anchors.md` (anchor lanes), `runtimes.md`, `system-one.md`. Shared: `scoring.md` (run, score, compare, head-to-head; loaded by every route that runs `effort-run.sh`); `challenges.md` (generator internals only).
3. **`models-yml.md` merged into `onboard-flow.md` and deleted:** its apply sequence duplicated onboarding step 7, and only onboarding writes an entry by hand (`tune-apply.sh`, `quant-headroom.sh add-entry` write theirs).
4. **One level deep, tested:** a reference may point at `SKILL.md`, never at another reference; the route table names everything a route loads.
5. **Owners:** budget, benchmark runs, like-for-like (depth, floor, profile, seeds) -> `SKILL.md` rules 1-3; freeing the GPU -> `SKILL.md` Gates; gate facts -> `SKILL.md` Gates; replace rule -> `scoring.md` Score item 6 (Decision 9), verdict conditions (a) and (c) -> `onboard-flow.md` step 8; jev black-box sentence -> `scoring.md` Fast classification and `selection-criteria.md` section 7 (Decision 14); head-to-head and lane commands -> `scoring.md`; fit math -> `selection-criteria.md` 1-3; `make benchmark` side effect -> `SKILL.md` Gotchas; ROCm image gotcha -> `runtimes.md` only.
6. **Router profile for every ladder lane. DECIDED (owner, 2026-10-31).** Every ladder lane (baseline, candidate, served alias, tuning, quant, anchor) runs through the router profile (Fact 5): `PI_CODING_AGENT_DIR=$HOME/.pi/agent`, `router-env/llama-cpp/<alias>` or the anchor's id; one profile per comparison, so no lane of a comparison runs on another profile. Cost: OmniRoute is in the path; a 503 or admission error is an invalid run handled by the AGENTS.md ladder.
7. **Served alias = re-measure:** fit from `preflight.sh` `llmenv_budget`, no scratch config, `bench-fit.sh` on the live config after G2, its ladder lane before G2 is the only lane, step 7a is `make start-llama`, 7c is skipped, step 8 reports numbers and the card with no verdict, G5 has nothing to revert.
8. **Rollback copy at G3:** `cp -p ~/.config/llm-env/models.yml ~/.config/llm-env/models.yml.pre-<alias>-<stamp>` before the `yq -i` add; `make llama-enable`'s own backup is a second copy, never the one step 9 restores.
9. **Replace rule. DECIDED (owner, 2026-10-31): keep the single replace rule already unified in `scoring.md`** Score item 6 (Fact 12) as its only statement: the quality part passes only with `ladder` delta >= 0 and `overall` delta > 10 on one seed set. It is not moved into or restated in `onboard-flow.md` or `SKILL.md`; they name it. The 9(c) trade-off rule this plan proposed (slower by more than 3 %, replace only at or above the 20 t/s floor with a higher ladder) is not adopted: condition (c) keeps its current wording (pp and tg at depth within about 3 % of the baseline, or the quality gain justifies the slowdown and the trade is stated), and G5 stays the owner's call.
10. **No baseline** (no enabled entry): the verdict rests on budget fit, the floor and the ladder reached; G5 still asks.
11. **Bare model name:** `hf-scout.sh --query "<name>" --json`; the first-party repo, else the quantizer the owner named; several plausible repos -> list them and ask; none -> report.
12. **jev in ranking is optional:** only for a level the research card's `notes` mark uncertain, in the black-box form of Decision 14; no answer or `needs_fallback` leaves the level to us. The skill says nothing about when that skill can or cannot answer (no "when Laya is down").
13. **Evals:** fixture-driven where a seam exists (`LLM_ENV_CONFIG`/`LLM_ENV_MODELS_DIR`, `HF_FIXTURE_DIR`, offline `rank-candidates.sh`, `SO_FIXTURE_DIR`/`SO_DOCKERFILE`, a saved `preflight.sh` JSON as premise); `latest-fit` and `runtime-image-review` stay deliberately live-host. `no-argument-research` is replaced by `no-argument-ranking`. No `split` field on trigger queries: the rubric has no split criterion and skill-creator splits by itself (sibling plan Fact 14).
14. **`jev-fast-decision` is a black box; no ai-kit name** (owner, 2026-10-31). The skill and the docs this plan touches say only: ask the `jev-fast-decision` skill (follow its own SKILL.md for how to call it); its answers are advisory; `needs_fallback` or no answer means we decide ourselves. They never say how it chooses or reaches a backend (Global Constraints), so a change to that skill is inherited without edits here. It is named only where the skill asks it (`scoring.md` Fast classification, `selection-criteria.md` section 7 jev items) and in `evals.json` (advisory, never enters the score); the llm-env side keeps only the question it asks and the evidence card's `jev` block format. Kept because they are System One's own API, not jev's internals: `system-one.md`'s Jev-compatible `/v1/systemone` contract and the `"model":"jev-1"` request bodies that probe Laya. Left alone: `scripts/print-endpoints.sh`. The Laya p95/p50 limits are justified by Laya serving advisory, non-interactive typed judgments, with no client details. The guard pytest scans the skill's markdown, evals JSON and scripts plus the three tracked docs Task 0 edits for `JEV_BACKEND`, `JEV_ALLOW_REMOTE`, `GSD_PATH_JEV`, `consent`, `typesafe`, `--probe`, `ai-kit` and the audit's phrases (`laya backend`, `remote backend`, `jev client`, `call per item`, `client timeouts`/`clients time out`); its allowlist inside the scan is empty, and what stays outside the scan is listed in the test.

## Review Focus

Failure modes the owner's input implies that no pytest exercises; each has its eval in Task 3.

1. "quiero probar Qwen3.8-27B": a bare name must reach the Hugging Face route through `hf-scout.sh --query`, not stall on exit 2 [`onboard-plan-with-server-running`, assertion 1].
2. A research subagent that wrote no card: the row is unresearched, never eliminated [`no-argument-ranking`, assertion 2].
3. "How does our model compare to Haiku" with no candidate: local lane first on the router profile, G4, correctness-only [`anchor-comparison`, assertions 3-5].
4. Standalone quant comparison on a host without `my-coding-lite`: Q11 must not create the combo [`quant-head-to-head`, assertion 7].
5. A System One trial while llm-server serves: free the GPU (G2) before the trial container [`system-one-check`, assertion 4].
6. `jev-fast-decision` gives no answer, answers `needs_fallback`, or has changed how it picks its backend: the ranking still goes on from the card's own levels and sources, and the agent adds no backend step of its own (no probe, consent or environment variable it read about here) [`no-argument-ranking`, its last assertion; the guard pytest covers only the text].

---

### Task 0: `jev-fast-decision` as a black box, no ai-kit name (R16; first)

**Files:**
- Modify (untracked skill): `$SK/references/scoring.md` (Fast classification, `:48-57`), `$SK/references/selection-criteria.md` (jev items, `:101-104`), `$SK/references/tuning.md` (Interference gate, `:46-47`), `$SK/references/system-one.md` (`:8-9`, `:23`, `:46-47`), `$SK/SKILL.md` (one clause at `:34`; Task 2 rewrites the file), `$SK/references/onboard-flow.md` (one clause at `:59-60`; Task 1 rewrites the file)
- Modify (tracked, committed in Step 8): repo-root `README.md:255-256`, `.agents/architecture.md:47`, `pylib/capacity.py:250`
- Test: `tests/skills/onboard-local-model/test_onboard_local_model_scripts.py` (new test after `test_skill_description_fits_the_spec_limit`)

**Interfaces:**
- Consumes: the module's `ROOT`, `SKILL`, `SCRIPTS`, `SKILL_MD`, `REFS`, `_md`.
- Produces: `JEV_RULE`, `JEV_FORBIDDEN`, `JEV_NAMED_IN`, `JEV_ALLOWLIST`, `test_skill_and_touched_docs_treat_jev_as_a_black_box` (select with `-k black_box`); the two black-box paragraphs of Steps 4-5, which Tasks 1-3 carry verbatim.

- [ ] **Step 1: Snapshot and baseline.** `mkdir -p tmp/skill-nav && tar -C .claude/skills -czf tmp/skill-nav/onboard-local-model-before.tgz onboard-local-model` (the only rollback for the untracked tree); `wc -c $SK/SKILL.md $SK/references/*.md > tmp/skill-nav/sizes-before.txt`; `uv run pytest tests/skills -q --basetemp=$HOME/.cache/llm-env-nav-pt | tail -1 > tmp/skill-nav/tests-before.txt`; `make validate`; `bash tmp/goal/jev-agnostic-audit.sh > tmp/skill-nav/jev-audit-before.txt`. Expected: all pass, `All checks passed.`; otherwise stop and report.
- [ ] **Step 2: Write the failing test**
  ```python
  JEV_RULE = "follow its own SKILL.md for how to call it"
  JEV_FORBIDDEN = re.compile(r"JEV_BACKEND|JEV_ALLOW_REMOTE|GSD_PATH_JEV|consent|typesafe|--probe|ai-kit"
                             r"|laya backend|remote backend|jev client|call per item|clients? time ?outs?", re.IGNORECASE)
  JEV_NAMED_IN = {"references/scoring.md", "references/selection-criteria.md", "evals/evals.json"}  # the files that ask it
  # Allowed matches inside the scan, as {"<relative path>": {"<lowercased match>"}}: none. Outside the scan by design:
  # scripts/print-endpoints.sh (the user-facing jev env line) and the "jev-1" request bodies that call System One's own API.
  JEV_ALLOWLIST: dict[str, set[str]] = {}


  def test_skill_and_touched_docs_treat_jev_as_a_black_box() -> None:
      """jev-fast-decision is asked, never described: no backend, consent, probe, env, call-rule or timeout knowledge; no ai-kit."""
      skill_files = [SKILL_MD, *sorted(REFS.glob("*.md")), *sorted((SKILL / "evals").rglob("*.json")), *sorted(SCRIPTS.glob("*.sh"))]
      docs = [ROOT / "README.md", ROOT / ".agents" / "architecture.md", ROOT / "pylib" / "capacity.py"]
      offenders = []
      for path in skill_files + docs:
          rel = str(path.relative_to(SKILL if path in skill_files else ROOT))
          text = _md(path)
          hits = {m.lower() for m in JEV_FORBIDDEN.findall(text)} - JEV_ALLOWLIST.get(rel, set())
          if "jev-fast-decision" in text and rel not in JEV_NAMED_IN:
              hits.add("jev-fast-decision")
          if hits:
              offenders.append((rel, sorted(hits)))
      offenders += [(f"references/{n}.md", ["rule missing"]) for n in ("scoring", "selection-criteria") if JEV_RULE not in _md(REFS / f"{n}.md")]
      assert not offenders, offenders
  ```
- [ ] **Step 3: Run** `uv run pytest tests/skills/onboard-local-model -q --basetemp=$HOME/.cache/llm-env-nav-pt -k black_box`. Expected: FAIL listing exactly Fact 11's places: `SKILL.md` and `references/onboard-flow.md` (`jev-fast-decision`), `references/scoring.md` (`--probe`, `call per item`, `consent`; rule missing), `references/selection-criteria.md` (`call per item`; rule missing), `references/system-one.md` (`jev client`, `jev-fast-decision`, `laya backend`), `references/tuning.md` (`client timeouts`), `README.md` (`clients time out`), `.agents/architecture.md` (`client timeouts`), `pylib/capacity.py` (`ai-kit`). Any other entry is new parallel text: add it to Step 6 before going on.
- [ ] **Step 4: Rewrite the body of `scoring.md` `## Fast classification with jev-fast-decision`** (heading kept) with this text (rewrap freely; words fixed):
  ```text
  The verifier cannot judge free-text parts (did the reply name the real root cause, what kind of failure was it). For those,
  ask the `jev-fast-decision` skill (follow its own SKILL.md for how to call it). Its answers are advisory: the verifier result
  stands when they disagree, and a `needs_fallback` answer or no answer means you decide that item yourself from the transcript.
  - Failure mode of a failed run: one choice among `no-edit`, `wrong-fix`, `partial-fix`, `tool-call-error`, `loop-or-gave-up`,
    `context-overflow`. Evidence: the last assistant text plus the `llmenv classify-transcript --client pi` excerpt.
  - Root cause named: yes or no on "The reply identifies the actual defect in the changed file".
  Never send raw source or secrets.
  ```
- [ ] **Step 5: Replace the `**jev items**` paragraph of `selection-criteria.md` section 7** (the card format line `jev: {<level>: {level, needs_fallback}}` above it stays) with:
  ```text
  **jev items** (optional, advisory): when a card's `notes` mark a level uncertain, you may ask the `jev-fast-decision` skill
  (follow its own SKILL.md for how to call it) which of the four level descriptions above fits that criterion, given the
  candidate's facts (never secrets), and record the answer in the card's `jev` block. Its answers never enter the score; a
  `needs_fallback` answer or no answer means you decide that level yourself. A jev level that differs from the card level by 2 or
  more, or a `needs_fallback` answer, is a disagreement: re-read the sources yourself and record the decision in `notes`. Every
  `disagreements` entry is re-checked before the shortlist goes on.
  ```
- [ ] **Step 6: One-phrase edits** (old text, quoted from the current file -> new text; rewrap the surrounding lines):
  - `tuning.md:46-47`: "Laya serves advisory typed judgments (`jev`) with client timeouts of about 20 s, so only" -> "Laya (System One) serves advisory, non-interactive typed judgments, so only".
  - `system-one.md:8-9`: "(8010). The `/jev-fast-decision` skill's Laya backend and the Phoenix observer depend on it." -> "(8010): System One's own API, which its clients depend on (among them `make check-server`, `make model-interference` and the Phoenix observer)." ("Jev-compatible" stays: it names System One's contract.)
  - `system-one.md:23`: "new typed primitives the Jev client could use" -> "new typed primitives in the `/v1/systemone` contract".
  - `system-one.md:46-47`: "latency for the same typed requests, the idle CPU check above. Run the `/jev-fast-decision` eval cases against both ports when the answers must not drift." -> "latency and answers for the same typed requests on both ports, the idle CPU check above."
  - `SKILL.md:34`: "ask `jev-fast-decision` for a `score` per level (advisory);" -> "its optional jev items (advisory);".
  - `onboard-flow.md:59-60`: "Classify failures with `/jev-fast-decision` per `references/scoring.md`." -> "Classify failures per `references/scoring.md`."
  - repo-root `README.md:255-256`: "Laya serves advisory typed judgments (`jev`) whose clients time out after about 20 s, so these are service-level limits;" -> "Laya serves advisory, non-interactive typed judgments, so these are service-level limits;".
  - `.agents/architecture.md:47`: "Laya (System One) serves advisory typed `jev` judgments with ~20 s client timeouts, so the gate fails on" -> "Laya (System One) serves advisory, non-interactive typed judgments, so the gate fails on".
  - `pylib/capacity.py:250`: "(kept for readers of the file, such as ai-kit)" -> "(kept for external readers of the file)".
- [ ] **Step 7: Verify.** The Step 3 command passes; `uv run pytest tests/skills/onboard-local-model -q --basetemp=$HOME/.cache/llm-env-nav-pt -k "isolated_dirs or long_shell or name_only_existing or bypassed or spec_limit"` passes; `uv run pytest tests/test_docs.py tests/test_capacity.py -q --basetemp=$HOME/.cache/llm-env-nav-pt` passes; `bash tmp/goal/jev-agnostic-audit.sh` prints under "jev backend/consent knowledge" only `scripts/print-endpoints.sh:52` and the guard's own lines (regex, docstring) in the test module, and under "ai-kit mentions" only those guard lines; `make validate` prints `All checks passed.`; `PYTEST_ADDOPTS="--basetemp=$HOME/.cache/llm-env-nav-pt" make test` passes.
- [ ] **Step 8: Commit the tracked docs.** `git diff --stat README.md .agents/architecture.md pylib/capacity.py` shows only the three Step 6 edits (if it shows anyone else's edits, never revert or commit them: stop and report instead); `git add README.md .agents/architecture.md pylib/capacity.py && git commit -m "docs: state Laya's interference limits without client details"`; `git show --stat HEAD` lists exactly those three files. No test joins the commit: none pins these lines, and the guard lives in the untracked skill test module.

---

### Task 1: Flow references, one level deep (R1-R13)

**Files:**
- Modify: `$SK/references/onboard-flow.md` (rewrite), `selection-criteria.md`, `tuning.md`, `scoring.md`, `challenges.md`, `anchors.md`, `runtimes.md`, `system-one.md`
- Delete: `$SK/references/models-yml.md` (content merged)
- Test: `tests/skills/onboard-local-model/test_onboard_local_model_scripts.py` (new test after Task 0's `test_skill_and_touched_docs_treat_jev_as_a_black_box`)

**Interfaces:**
- Consumes: Task 0's two black-box paragraphs (`scoring.md` Fast classification, `selection-criteria.md` section 7 jev items), carried verbatim, and its guard. `SKILL.md` anchors that Task 2 creates: `Route`, rules `1` Budget, `2` Benchmark runs, `3` Like for like, `Gates` with `Freeing the GPU`, `Gotchas`. Until Task 2 the old `SKILL.md` still points at old step numbers; expected.
- Produces: section names `SKILL.md` and the evals cite: `onboard-flow.md` Alias, Hugging Face, Local GGUF, Scout pick, steps 1-9 (7a, 7b, 7c); `selection-criteria.md` Find the repo, Scout and rank, sections 1-7; `scoring.md` Running a lane, Score (item 6 = the replace rule), Head-to-head; `anchors.md` Anchor lanes; `tuning.md` Standalone quant comparison, T1-T9, Q1-Q11; the constant `REF_NAMES`.

- [ ] **Step 1: Baseline.** Task 0 took the snapshot, `sizes-before.txt` and `tests-before.txt`. `uv run pytest tests/skills/onboard-local-model -q --basetemp=$HOME/.cache/llm-env-nav-pt -k black_box` and `make validate`. Expected: pass and `All checks passed.`; otherwise stop and report.
- [ ] **Step 2: Write the failing test**
  ```python
  REF_NAMES = ("anchors", "challenges", "onboard-flow", "runtimes", "scoring", "selection-criteria", "system-one", "tuning")


  def test_references_never_point_at_each_other() -> None:
      """One level deep: a reference may point at SKILL.md, never at another reference."""
      assert sorted(p.stem for p in REFS.glob("*.md")) == sorted(REF_NAMES)
      for path in sorted(REFS.glob("*.md")):
          text = path.read_text()
          others = [n for n in REF_NAMES if n != path.stem and re.search(rf"\b{re.escape(n)}\.md\b", text)]
          assert not others, (path.name, others)
  ```
- [ ] **Step 3: Run** `uv run pytest tests/skills/onboard-local-model -q --basetemp=$HOME/.cache/llm-env-nav-pt -k point_at_each_other`. Expected: FAIL (`models-yml` in the listing).
- [ ] **Step 4: Rewrite `onboard-flow.md`** as "# Onboard a named model" with this outline; move the quoted sources, drop what `SKILL.md` now owns (Decision 5), and name no other reference file:
  - Header (2 lines): loaded for an alias, a Hugging Face repo or file, a local GGUF or a scout pick (`SKILL.md` Route); gates, rules 1-3 and freeing the GPU are in `SKILL.md`; report each step as it ends (no stage-label example).
  - `## Candidate by kind` with `### Alias` (from `SKILL.md:18-22`, `models-yml.md:44-45`, `onboard-flow.md:43-46`; served alias per Decision 7; any other alias: scratch config from `resolve-target.sh <alias> --scratch-config tmp/model-scout/<run>/scratch.yml`, `present: false` -> G1 downloads the entry's `url` first, G3 adds no entry and runs `make llama-enable MODEL=<alias>`), `### Hugging Face` (from `SKILL.md:23-25`, `models-yml.md:44-48`: `hf-scout.sh --repo <repo> --json` with `--budget-mib` from rule 1 and `--ram-mib` = `budget.ram_offload_budget_mib`, the named file or the scout's pick is the one file; one research subagent writes the card per the research brief loaded with this route; a false card gate or a failed hard gate is a G1 fact and under bypass it proceeds because the owner named the model; scratch config from `resolve-target.sh <baseline alias> --scratch-config $S`, entry added with `yq -i`, `enabled: false`; never `cp` the live file; no alias in the live file -> stop and report), `### Local GGUF` (from `SKILL.md:26-29`, `onboard-flow.md:12-17`, `models-yml.md:58-65`), `### Scout pick` (as Hugging Face with the pick's repo and file).
  - `## Steps`: `1. Preflight` (`SKILL.md:60-66` minus the budget sentences, now rule 1); `2. Baseline` (`SKILL.md:67-70`, `onboard-flow.md:31-33`, plus Decision 10); `3. Candidate` (one line: the section for its kind); `4. Fit and configure` (`onboard-flow.md:5-11` and `models-yml.md:48-55`: download and size check, `validate-gguf`, `models select`, `budget`, stop on infeasible with `remedies`, the gateway-only RAM note; KV `q5_1` unless `q8_0` still leaves >= 8 % VRAM free; **Gate G1**); `5. Baseline lane, then free the GPU` (`onboard-flow.md:18-27`: decide the lane set first, anchors included, all on the router profile; a different served baseline runs its lane here with `--per-layer 3` and writes `seeds.json`; for the served alias this lane is its only one; **Gate G2** per `SKILL.md` Freeing the GPU; remember the prior state); `6. Screen throughput` (`onboard-flow.md:28-37`; floor and reference depth by rule 3; a load error "unknown model architecture" goes to the runtime route, not a broken GGUF); `7a. Apply and serve` (**Gate G3**; `onboard-flow.md:38-46` with `models-yml.md:57-76` deduplicated: rollback copy per Decision 8, existing-entry lookup, entry add, `make llama-enable`, `make setup`, `make benchmark` only per `SKILL.md` Gotchas, `make start-llama` or `make start`, `make status`, `make check-server`, `make check-with-agents`, `make setup-local-llm-agents`); `7b. Interference and capacity` (`make model-interference`, then `make model-capacity MODEL=<alias> DEPTH=<bench json>`; a failed gate goes to step 9); `7c. Quality ladder` (candidate lane `--seeds <baseline seeds.json>`, or the first lane `--per-layer 3` without a baseline lane; `score.sh`, `compare-runs.sh` with the baseline first; a named anchor: its route row, **Gate G4**); `8. Verdict` (from `onboard-flow.md:77-82`; Decision 9: the replace rule stays stated only in the effort-ladder reference, so this step names it as "the replace rule (Score, item 6, of the effort-ladder reference)", never restates its numbers and never names that file. The candidate replaces the baseline only if (a) it fits `llmenv budget` with System One resident; (b) it passes the replace rule on one seed set; (c) pp and tg at the reference depth stay within about 3 % of the baseline, or the quality gain justifies the slowdown and the trade is stated (current wording). Before recommending ask: would the owner notice this in a pi session? Then the table); `9. Keep or revert` (**Gate G5**; `onboard-flow.md:83-87`, `models-yml.md:77`; a no, a declined G3 or a failed step after G2 restores the rollback copy; leftover check).
  - `## Entry template` (`models-yml.md:17-39`) and `## Where things live` (`models-yml.md:5-15`).
  - Deleted: the old step 8 (runtime review; it is a route) and "Benchmark runs" (`:62-74`, now rule 2; the worktree and scripts-only sentences are AGENTS.md rules and the prose test).
- [ ] **Step 5: Edit `selection-criteria.md`.** Insert before `## 1. Fit math`: `## Find the repo (a bare model name)` per Decision 11, ending "then `SKILL.md`'s Hugging Face row"; and `## Scout and rank (no argument)` as one ordered list built from `SKILL.md:31-35`, `:71-78` and mode row `:41`: (1) preflight and baseline (enabled entry of the live `models.yml`); (2) a deep-research style skill once when the session lists one, else skip; (3) `hf-scout.sh --query <term> --json > tmp/model-scout/<run>/scout-<term>.json` for `coder`, `agent`, the baseline's family and every family the research named, plus another term only when no row has `fit: vram`; (4) `rank-candidates.sh --scout <f1> --scout <f2> ...` (top 6); full-VRAM rows first, an offload MoE only as challenger to the best full-VRAM row and only with `ram.available_mib` >= its expert bytes + 4 GiB and no swap in use, said when one is left out; (5) one research subagent per shortlisted row in parallel (brief in section 7), then section 7's optional jev items (Task 0's paragraph, Decision 12); (6) `--evidence <dir>`: a shortlisted row without a card is absent from `ranked` and `excluded`, so re-dispatch it or report it unresearched, never eliminated; re-check every `disagreements` entry from the sources and record it in `notes`; (7) report per section 6: a read-only request ends here; none fits or beats the baseline -> null result; at most 3 go on, only when the owner asked to onboard. Section 1 first bullet becomes "Budget: `SKILL.md` rule 1 (the repo verdict was 8,321 MiB against 10,369 screening on 2026-10-30)". Pointers: `:47` "(`references/challenges.md`)" -> "(the effort ladder)"; `:50` "(see `anchors.md`)" -> "(`SKILL.md` NEVER)"; `:67` "(`runtimes.md`)" -> "(the runtime route)"; `:79` -> "Used by Scout and rank."; `:108` "per `anchors.md`" -> "per `SKILL.md` NEVER". Header `:3` -> loaded for a bare name, the no-argument route, the fit math of onboarding step 4 and the Hugging Face research brief.
- [ ] **Step 6: Edit `tuning.md`.** `:3-5` -> loaded for tuning, the capacity card, `my-coding-lite` and quant comparisons; benchmark runs, like-for-like and freeing the GPU are in `SKILL.md`. T3 -> "Gate G-T2: free the GPU (`SKILL.md`, Freeing the GPU)." T4 and T8's tail -> "a benchmark run (`SKILL.md` rule 2)"; drop "(router profile, why: ...)". `:39` -> "The full program continues with Quant headroom." `:78-79` -> "`<axes>`: omit them for the default grid (`tune-sweep.sh --help`) unless the owner names axes; else `--ctx`, `--kv`, ...". Quant headroom `:102` -> "after T8 (T9 only builds `my-coding-lite`)", and "Run it with llm-server serving" -> "Q1 runs either with llm-server serving `<a>` and T5's `vram_available_mib`, or after G-T2 with the flag omitted". `:111-114` keep the specifics (T8 seeds, same efforts, timeout and layers, one lane at a time, 5 x 3 x 2 x 300 s per lane, exit 3) without "(`challenges.md`)". Q4 -> "free the GPU (`SKILL.md`), then `make llama-enable MODEL=<b>`". Q11 -> `make local-combo` only when `my-coding-lite` existed before Q1. `:142` -> "Q5 and Q8 are benchmark runs (`SKILL.md` rule 2), one at a time." `:143` -> "`my-coding-lite`, when it exists, ...".
- [ ] **Step 7: Rebuild `scoring.md`** as "# Effort ladder: run, score, compare": header (loaded by every route that runs `effort-run.sh`; profile, seeds and depth: `SKILL.md` rule 3); `## Layers and effort tiers` (`:7-23`, drop "defined in `challenges.md`"); `## Running a lane` (upper-bound text from `onboard-flow.md:51-55`: layers x per-layer x tiers x `--timeout`, runner cap 300 s, screen `--efforts low,high` 5 x 1 x 2 x 300 s, head-to-head `--per-layer 3` 5 x 3 x 2 x 300 s, full run only on request; plus the subagent brief from `challenges.md:207-221`); `## Score` (`:25-42` items 1-6; item 6 stays the replace rule word for word (Decision 9) except its two pointers: "(the only statement of it; `SKILL.md` and `onboard-flow.md` step 9 point here)" -> "(the only statement of it; the onboarding verdict step names it)", and "The other conditions of step 9 (budget fit, pp/tg at depth) still apply." -> "The route's verdict step adds budget fit and pp/tg at depth."); `## Head-to-head` (`challenges.md:176-190` without the client-profile paragraph `:180-185`, plus anti-contamination bullets `challenges.md:152-155`; step 4 reads "decide in the route's verdict step"); `## Fast classification with jev-fast-decision` (Task 0's text, unchanged).
- [ ] **Step 8: Trim `challenges.md`** to the generator: `:3` -> "Loaded only to author, debug or calibrate the challenge library, or to investigate a `leak_suspect` row; a normal ladder run does not need it."; delete `:5`, the moved head-to-head (`:174-190`), seed bullets (`:152-155`) and subagent brief (`:207-221`); calibration step 1 runs on the router profile.
- [ ] **Step 9: Restructure `anchors.md`.** `:3` -> loaded when the user names a cloud anchor, with the ladder reference; figures retrieved 2026-10-30, re-check before quoting. Keep the figures table, sources and the two expectation bullets. Replace "Prefer the local head-to-head" and its sub-bullets with `## Anchor lanes`: (1) route check on the router profile: `pi auth check --model <provider/id>`; Composer 2.5 via `cursor-agent models` (text `:38-42`); (2) **Gate G4** before the first anchor run and before any switch of anchor model or provider; (3) the local lane first (the served model, or the onboarding candidate) with `--per-layer 3`, writing `seeds.json`; a recorded lane counts only when its `challenge_set` matches; each anchor lane `--seeds` that file, same `--efforts` and `--timeout`, one at a time; (4) `compare-runs.sh --correctness-only` with the local lane first; report local overall / anchor overall and both ladders (why: `:30-31`); (5) a 401 or usage limit is `api_error` (`:34-37`): AGENTS.md retry ladder, never switch anchor or provider, published numbers only as a labelled, version-caveated fallback; (6) retire the seed set after any cloud lane.
- [ ] **Step 10: Edit `runtimes.md` and `system-one.md`.** `runtimes.md:3-4` -> no step number; `:40` -> "`n_cpu_moe` for MoE models: sweep it with `tune-sweep.sh --moe`."; Updating step 2 -> "after freeing the GPU (G2, `SKILL.md`)". `system-one.md:3-4` -> "Loaded when the user asks whether Laya ... or when VRAM is the limit for a candidate."; trial step 3 starts "Free the GPU first (G2, `SKILL.md`): the model budget leaves no room for a second Laya (about 4.26 GB steady)."
- [ ] **Step 11: Delete** `$SK/references/models-yml.md` (merged in Step 4; kept in the Task 0 Step 1 tarball).
- [ ] **Step 12: Verify.** The Step 3 command passes; `uv run pytest tests/skills/onboard-local-model -q --basetemp=$HOME/.cache/llm-env-nav-pt -k "isolated_dirs or long_shell or name_only_existing or bypassed or black_box"` passes; `grep -rn "T7-T9\|Decision 9\|stated only here\|Benchmark runs" $SK/references` prints nothing; `grep -rl "delta > 10" $SK/references` prints only `.claude/skills/onboard-local-model/references/scoring.md`; `make validate` prints `All checks passed.`.

---

### Task 2: SKILL.md as the navigation entry (R1, R2, R7, R11, R14; D1, D2, D4, D5)

**Files:**
- Modify: `$SK/SKILL.md` (rewrite)
- Test: `tests/skills/onboard-local-model/test_onboard_local_model_scripts.py` (new test after Task 1's)

**Interfaces:**
- Consumes: Task 1 section names and `REF_NAMES`.
- Produces: the anchors Task 1 cites (Route, rules 1-3, Gates, Freeing the GPU, NEVER, Gotchas).

- [ ] **Step 1: Write the failing test**
  ```python
  SKILL_MD_MAX_BYTES = 11_000  # lowered from the 14,500 target of 2026-10-31-judge-round-1-fixes; raise only with a reason


  def test_skill_md_is_a_router_that_loads_every_reference() -> None:
      text = SKILL_MD.read_text()
      assert len(text.encode()) <= SKILL_MD_MAX_BYTES
      for name in REF_NAMES:
          assert f"references/{name}.md" in text, name
      assert set(re.findall(r"\b([a-z][a-z-]*)\.md\b", text)) <= set(REF_NAMES)
  ```
- [ ] **Step 2: Run** `uv run pytest tests/skills/onboard-local-model -q --basetemp=$HOME/.cache/llm-env-nav-pt -k router_that_loads`. Expected: FAIL (14,356 bytes).
- [ ] **Step 3: Replace `SKILL.md` from the front matter through `## Gates`** with this text (keep the wrapping or rewrap; words fixed):
  ```text
  ---
  name: onboard-local-model
  description: Find, fit, benchmark and onboard a local agentic-coding GGUF for this repo's llama.cpp router while it shares the GPU with System One (Laya). Use when the user names a models.yml alias, a Hugging Face repo or URL, a .gguf file or a model name to add, try or swap; asks which local model fits the VRAM or beats the current one; compares quants (Q4_K_M vs Q5_K_M); wants pp/tg tok/s at context depth, ctx_size, KV cache or n_cpu_moe tuned, the capacity card or my-coding-lite; scores the local model against Haiku, Luna or Composer; or asks whether the llama.cpp image (Vulkan/ROCm) or Laya is outdated, even as "is there a faster model for pi" or "modelo agéntico que quepa en mi GPU". Not for OmniRoute updates or other combos, picking a cloud provider, sampling settings, fine-tuning or quantizing checkpoints.
  ---

  # Onboard a local model

  `SK=.claude/skills/onboard-local-model`; paths are relative to the repo root; artifacts go under `tmp/model-scout/<YYYYMMDD>-<alias>/`.
  Goal: a model that gives pi a usable coding agent (>= 128K context, 256K preferred, fast decode at depth) inside the VRAM and RAM
  this host has with System One resident, proven by measurement, not model-card claims. Needs `yq jq podman uv` (`preflight.sh`
  checks them), plus `git node pi` for the ladder.

  ## Route

  Classify a model argument first: `bash $SK/scripts/resolve-target.sh <model>` prints one JSON object whose `kind` picks the row.
  Open the flow file of the row and follow it; load the other files only at the step that names them.

  | The user gave or asked | Flow file, section | Also load | Do NOT load |
  |---|---|---|---|
  | a models.yml alias (`alias`) | `references/onboard-flow.md`, Alias | `references/selection-criteria.md` sections 1-3 at step 4 | `tuning.md`, `challenges.md` |
  | a Hugging Face repo or URL (`hf_repo`, `hf_file`) | `references/onboard-flow.md`, Hugging Face | `references/selection-criteria.md` sections 1-5 and 7 | `tuning.md`, `challenges.md` |
  | a `.gguf` path (`gguf_file`) | `references/onboard-flow.md`, Local GGUF | `references/selection-criteria.md` sections 1-3 at step 4 | `tuning.md`, `challenges.md` |
  | a bare model name (`resolve-target.sh` exits 2) | `references/selection-criteria.md`, Find the repo | then the Hugging Face row | |
  | nothing: what fits, anything newer or better | `references/selection-criteria.md`, Scout and rank | `references/onboard-flow.md` only once the owner picks a row to onboard | `scoring.md`, `anchors.md`, `challenges.md` |
  | quants of the current model (Q4_K_M vs Q5_K_M, room for Q6) | `references/tuning.md`, Standalone quant comparison | `references/scoring.md` before Q8 | `onboard-flow.md`, `selection-criteria.md` |
  | tune it: context, KV, tok/s at depth, capacity card, `my-coding-lite` | `references/tuning.md` | `references/scoring.md` at T8 | `onboard-flow.md`, `selection-criteria.md` |
  | score a local model against Haiku, Luna or Composer | `references/anchors.md` | `references/scoring.md` | `onboard-flow.md` unless a candidate is named |
  | llama.cpp image, Vulkan or ROCm, server flags | `references/runtimes.md` | | every other file |
  | System One (Laya) outdated, or VRAM it could give back | `references/system-one.md` | | every other file |

  Every route that runs `effort-run.sh` reads `references/scoring.md` first. `references/challenges.md` is only for authoring,
  debugging or calibrating the challenge library or investigating a `leak_suspect` row. When two rows match, the model argument wins
  and an anchor named with it joins that flow's quality step; a read-only question never starts a gated step. Before the first gate
  name the number that will decide, the baseline it is compared with and the depth; if one is missing, stay read-only.

  ## Rules for every measurement

  1. **Budget.** Size against the smaller of `budget.llmenv_budget.vram_available_mib` (the repo's verdict) and
     `budget.model_vram_budget_mib` (ceiling minus the System One reserve) from `preflight.sh`, and say which one. A false
     `llmenv_budget.feasible` means the current model already does not fit with System One resident: lead with that.
  2. **Benchmark runs.** Every `bench-fit.sh`, `tune-sweep.sh`, `effort-run.sh` and `latency-profile.sh` run writes to a new
     `--out` under `tmp/` (the scripts exit 2 otherwise), runs one at a time on the GPU, and is delegated to one subagent with a
     written brief (command, out dir, stop and cleanup conditions, what to return) and monitored per AGENTS.md.
     Benchmark challenges run only on generated challenges (`gen-challenge.sh`), copied into isolated directories under `./tmp/`, and never in or against the owner's project.
     No other session may write the repo during a ladder run: a concurrent edit flags rows `leak_suspect`.
  3. **Like for like.** Decode counts at the reference depth `min(131072, ctx - 512)`, with a 20 t/s floor (`tune-pick.sh
     --min-tg`); a depth-0 number is never compared with a depth number, because agents re-read their context every turn. Every
     ladder lane uses the router profile (`PI_CODING_AGENT_DIR=$HOME/.pi/agent`, `--model router-env/llama-cpp/<alias>` or the
     anchor's id), the one profile `make setup-local-llm-agents` writes, on one `seeds.json` with the same `--efforts` and
     `--timeout`. `compare-runs.sh` exit 3 means the lanes are not a head-to-head: no verdict.

  ## Gates

  | Gate | Asked before | Facts the question states |
  |---|---|---|
  | G1 | the one download or copy | file, size, free disk, destination `~/llm-workspace/models`; any failed hard gate of a model the owner named |
  | G2 | `make stop-llama` | the served model, that the local OmniRoute lane goes offline, that System One stays up, the `wait-gpu-release.sh` target |
  | G3 | editing the live models.yml and serving | the diff, the rollback copy, that `make setup` pulls the floating Vulkan tag (the llama.cpp build can change) and llm-server restarts |
  | G4 | the first anchor run | provider and model, runs and upper bound, that it spends quota |
  | G5 | keeping the change | the comparison table; a no restores the rollback copy |

  **Freeing the GPU (G2, G-T2).** From one `preflight.sh` read `.gpu.used_mib` (U) and the `vram_mib` of the `.gpu.holders[]`
  entry whose `comm` is `llama-server` (H); run `make stop-llama` (llm-server only), then
  `bash $SK/scripts/wait-gpu-release.sh --below-mib <U - H + 512>`; on exit 1 report the printed holders and stop. System One stays
  resident: it is budgeted as resident, so measuring without it flatters the result. While llm-server is stopped the healer's
  OmniRoute restart fails (logged) and never reloads it.

  Route gates follow the same rule: G-T1 to G-T3 and G-Q1 to G-Q3 (`references/tuning.md`), G6 (`references/system-one.md`).
  Each gate is its own question and a yes to one never covers the next. A declined G1 or G2 ends with the fit math and no scratch
  files; a declined G3 or G5, or a failed step after G2, runs the flow's rollback (`onboard-flow.md` step 9, `tuning.md` Rollback).

  **Permissions bypassed.** When the session runs with permissions bypassed, a gate becomes announce the facts, then proceed.
  Two things still stop and ask: any step that deletes user data (model files, `make prune`, `make clean`, volumes, the locally
  built System One image, or overwriting the live models.yml without a backup), and any change of provider, harness or model after
  a failure: never switch provider or model (AGENTS.md retry ladder). G-Q3 (switching the default quant) is a change of model:
  under bypass announce the comparison table and keep the current quant.
  ```
- [ ] **Step 4: `## NEVER` and `## Gotchas`** (deltas against the current items, quoted by their start):
  - Keep as is: "NEVER size against live free VRAM", "NEVER trust a leaderboard number", "NEVER recommend a sub-3.5-bit quant".
  - "NEVER run `make stop`": keep the reason, drop "(verified in `scripts/stop.sh` and the generated unit's `ExecStop`)".
  - "NEVER prefer an MoE offload": add the measured numbers moved from step 3 (35B at `n_cpu_moe 28`: tg 39 t/s against 88 for a 9B, RAM pressure and swap).
  - "NEVER read `agent_status: 0`": unchanged wording.
  - "NEVER leave containers": becomes "NEVER leave a bench container, sampler or agent process running: it holds the GPU and falsifies the next measurement."
  - "NEVER flip `llm_server.enabled`": becomes "NEVER flip `llm_server.enabled` or edit the live config except through `make llama-enable MODEL=<alias>` / `make llama-disable` or the gated steps of the flow files, each after its gate and a timestamped backup: `make setup` owns that file and a bad edit has no other rollback."
  - Delete "NEVER compare throughput at depth 0 only" (rule 3) and "NEVER score from files the agent could touch" (a script-maintenance note, kept in `effort-run.sh --help`).
  - "NEVER build a challenge": end with ": a challenge a model has seen measures recall, not skill." instead of the pointer.
  - Gotchas: keep the gateway-only item (ending "serving needs `make llama-enable MODEL=<alias>` after its gate"), the `make benchmark` item (drop its `make setup` sentence, now a G3 fact) and the `hf-scout.sh` item; delete the ROCm item (owned by `runtimes.md`); add "`bench-fit.sh` exits 3 while llm-server runs, and mounts the models directory with `:z` (an SELinux relabel): never point it at a directory the owner uses otherwise, such as `~/Downloads`."
- [ ] **Step 5: Verify.** The Step 2 command passes; `uv run pytest tests/skills/onboard-local-model -q --basetemp=$HOME/.cache/llm-env-nav-pt -k "bypassed or isolated_dirs or long_shell or name_only_existing or spec_limit or point_at_each_other or black_box"` passes; `grep -c "delta > 10\|jev" $SK/SKILL.md` prints 0 (the replace rule and jev live in the references); `wc -lc $SK/SKILL.md` about 107 lines and 10,200 bytes; `yq -r --front-matter=extract '.description' $SK/SKILL.md | tr -d '\n' | wc -m` prints 811; `grep -n "## Input\|## Pick the mode\|## Workflow\|\[3/10" $SK/SKILL.md` prints nothing; `make validate`.

---

### Task 3: Fixture-driven evals and realistic trigger queries (D7, R15)

**Files:**
- Create under `$SK/evals/files/`: `models.yml`, `models/gemma4-v2-Q4_K_M.gguf`, `preflight-served.json`, `hf/model-lab__Foo-9B-GGUF.json`, `hf/tree-lab__Foo-9B-GGUF.json`, `scout-coder.json`, `scout-agent.json`, `evidence/1.json`, `evidence/2.json`, `system-one/Dockerfile`, `system-one/pypi.json`, `system-one/torch-index.html`
- Modify: `$SK/evals/evals.json`, `$SK/evals/trigger-queries.json`
- Test: `tests/skills/onboard-local-model/test_onboard_local_model_scripts.py` (`test_eval_input_files_exist_and_the_gguf_fixture_resolves` plus three new tests after it)

**Interfaces:**
- Consumes: Task 1 and 2 names (served alias rules, rule 1 budget, `U - H + 512`, Find the repo, unresearched rows, Anchor lanes, Q11, System One G2); the module's `_run`, `SCRIPTS`, `SKILL`, `GGUF_BYTES`.
- Produces: `_asserts(eval_id) -> str` (the eval's assertions joined by spaces).

- [ ] **Step 1: Write the failing tests.** In the existing eval test, relax the prompt check to `assert name in prompt or f"{Path(name).parent}/" in prompt, eval_id`. Add:
  ```python
  FIX = SKILL / "evals" / "files"


  def _asserts(eval_id: str) -> str:
      evals = json.loads((SKILL / "evals" / "evals.json").read_text())["evals"]
      return " ".join(next(e for e in evals if e["id"] == eval_id)["assertions"])


  def test_eval_alias_fixtures_match_their_assertions() -> None:
      env = {**os.environ, "LLM_ENV_CONFIG": str(FIX / "models.yml"), "LLM_ENV_MODELS_DIR": str(FIX / "models")}

      def rt(model: str) -> subprocess.CompletedProcess[str]:
          return _run("bash", SCRIPTS / "resolve-target.sh", model, env=env)

      assert {k: json.loads(rt("gemma4").stdout)[k] for k in ("kind", "enabled", "present")} == {"kind": "alias", "enabled": True, "present": True}
      assert {k: json.loads(rt("ornith-35b").stdout)[k] for k in ("enabled", "present")} == {"enabled": False, "present": False}
      assert rt("Qwen3.8-27B").returncode == 2 and "exits 2" in _asserts("onboard-plan-with-server-running")
      pre = json.loads((FIX / "preflight-served.json").read_text())
      budget = min(pre["budget"]["llmenv_budget"]["vram_available_mib"], pre["budget"]["model_vram_budget_mib"])
      held = next(h["vram_mib"] for h in pre["gpu"]["holders"] if h["comm"] == "llama-server")
      assert (budget, pre["gpu"]["used_mib"] - held + 512) == (8321, 6712)
      assert "8321" in _asserts("served-alias-argument") and "6712" in _asserts("served-alias-argument")
      assert "present false" in _asserts("disabled-alias-argument")


  def test_eval_scout_fixtures_match_their_assertions() -> None:
      (row,) = json.loads(_run("bash", SCRIPTS / "hf-scout.sh", "--repo", "lab/Foo-9B-GGUF", "--json", "--budget-mib", "8321", "--ram-mib", "12000",
                               env={"HF_FIXTURE_DIR": str(FIX / "hf"), "PATH": "/usr/bin:/bin"}).stdout)
      assert (row["fit"], row["pick"]["file"]) == ("vram", "Foo-9B-Q5_K_M.gguf") and "Foo-9B-Q5_K_M.gguf" in _asserts("hf-repo-argument")
      scouts = ("--scout", FIX / "scout-coder.json", "--scout", FIX / "scout-agent.json")
      short = json.loads(_run("bash", SCRIPTS / "rank-candidates.sh", *scouts).stdout)
      ranked = json.loads(_run("bash", SCRIPTS / "rank-candidates.sh", *scouts, "--evidence", FIX / "evidence").stdout)
      assert [r["id"] for r in short["shortlist"]] == ["lab-a/Coder-9B-GGUF", "lab-b/Agent-14B-GGUF", "lab-d/MoE-30B-A3B-GGUF"]
      assert [(g["id"], g["gate"]) for g in short["gates_failed"]] == [("lab-c/Coder-27B-IQ2-GGUF", "bpw"), ("lab-e/Short-8B-GGUF", "ctx"), ("lab-f/Dense-32B-GGUF", "fit")]
      assert [(r["id"], r["score"]) for r in ranked["ranked"]] == [("lab-a/Coder-9B-GGUF", 23), ("lab-d/MoE-30B-A3B-GGUF", 20)]
      assert ranked["disagreements"] == [{"id": "lab-a/Coder-9B-GGUF", "criterion": "agentic_evidence", "level": 2, "jev": 0}]
      text = _asserts("no-argument-ranking")
      assert all(s in text for s in ("lab-b/Agent-14B-GGUF", "lab-c/Coder-27B-IQ2-GGUF", "lab-e/Short-8B-GGUF", "lab-f/Dense-32B-GGUF", "23", "20", "3100",
                                     "its own SKILL.md"))


  def test_eval_system_one_fixtures_match_their_assertions() -> None:
      env = {"SO_FIXTURE_DIR": str(FIX / "system-one"), "SO_DOCKERFILE": str(FIX / "system-one" / "Dockerfile"), "PATH": "/usr/bin:/bin"}
      out = json.loads(_run("bash", SCRIPTS / "system-one-check.sh", env=env).stdout)
      assert (out["pinned"]["laya"], out["latest"]["laya"], out["latest"]["releases_behind"]) == ("0.4.2", "0.5.0", 2)
      assert out["torch_latest_per_rocm_index"]["rocm7.14"] == "2.15.0"
      assert all(s in _asserts("system-one-check") for s in ("0.4.2", "0.5.0", "2 releases behind", "rocm7.14"))
  ```
- [ ] **Step 2: Run** `uv run pytest tests/skills/onboard-local-model -q --basetemp=$HOME/.cache/llm-env-nav-pt -k "eval_"`. Expected: FAIL (fixtures and eval ids missing).
- [ ] **Step 3: Create the fixtures** (contents fixed; `scout-*` rows carry `id, params_b, ctx, fit, pick {quant, mib, bpw}, updated, downloads`):
  - `models.yml`: `cp models.yml.example`, then `yq -i '.system_one.enabled = true'`. `models/gemma4-v2-Q4_K_M.gguf`: `GGUF` + 60 zero bytes.
  - `preflight-served.json`: `{"gpu": {"pci": "0000:03:00.0", "render_node": "renderD128", "total_mib": 16305, "used_mib": 13650, "free_mib": 2655, "temp_c": 52, "sclk_mhz": 2400, "busy_pct": 3, "holders": [{"pid": 4242, "comm": "llama-server", "vram_mib": 7450}, {"pid": 4343, "comm": "python3", "vram_mib": 4300}]}, "budget": {"vram_ceiling_mib": 15489, "system_one_reserve_mib": 5120, "model_vram_budget_mib": 10369, "ram_offload_budget_mib": 12000, "ram_budget_source": "llmenv budget", "llmenv_budget": {"feasible": true, "vram_available_mib": 8321, "vram_required_mib": 7300, "vram_shortfall_mib": 0, "ram_available_mib": 12000, "ram_feasible": true, "ram_required_mib": 0, "remedies": []}}, "ram": {"total_mib": 31800, "available_mib": 14500, "swap_used_mib": 3100}, "services": {"llm_server_enabled": true, "llm_server_running": true, "system_one_enabled": true, "system_one_running": true, "omniroute_running": true}, "runtime": {"backend": "vulkan", "image": "ghcr.io/ggml-org/llama.cpp:server-vulkan", "enabled_models": ["gemma4"]}, "disk": {"models_dir": "/home/user/llm-workspace/models", "free_mib": 182000}}`
  - `hf/model-lab__Foo-9B-GGUF.json`: `{"id": "lab/Foo-9B-GGUF", "downloads": 1200, "likes": 40, "lastModified": "2026-10-22T00:00:00.000Z", "gguf": {"total": 9000000000, "context_length": 262144, "architecture": "qwen35"}, "baseModels": {"models": [{"id": "lab/Foo-9B"}]}, "tags": ["gguf"]}`; `hf/tree-lab__Foo-9B-GGUF.json`: files `Foo-9B-Q4_K_M.gguf` 5872025600, `Foo-9B-Q5_K_M.gguf` 6815744000, `Foo-9B-Q8_0.gguf` 9961472000 bytes (`[{"path", "size"}]`).
  - `scout-coder.json`: `lab-a/Coder-9B-GGUF` (9, 262144, vram, Q4_K_M 5600 4.9, 2026-10-20, 900); `lab-b/Agent-14B-GGUF` (14, 131072, vram, Q4_K_M 8100 4.8, 2026-10-25, 400); `lab-c/Coder-27B-IQ2-GGUF` (27, 262144, vram, IQ2_XXS 7000 2.1, 2026-10-27, 300). `scout-agent.json`: `lab-a/Coder-9B-GGUF` again with ctx 32768; `lab-d/MoE-30B-A3B-GGUF` (30, 262144, offload, Q4_K_M 17500 4.8, 2026-10-28, 700); `lab-e/Short-8B-GGUF` (8, 32768, vram, Q4_K_M 4900 4.9, 2026-10-29, 100); `lab-f/Dense-32B-GGUF` (32, 131072, no, Q4_K_M 19000 4.8, 2026-10-26, 200).
  - `evidence/1.json`: `lab-a/Coder-9B-GGUF`, gates all true, levels 2/3/3/3/2, `jev: {"agentic_evidence": {"level": 0, "needs_fallback": false}}`, one source URL with a retrieval date. `evidence/2.json`: `lab-d/MoE-30B-A3B-GGUF`, `tool_template: null`, others true, levels 2/1/3/3/3, `jev: {}`.
  - `system-one/Dockerfile`: `ARG TORCH_VERSION=2.14.1`, `ARG TORCH_INDEX=rocm7.2`, `ARG LAYA_VERSION=0.4.2`; `pypi.json`: releases 0.4.1, 0.4.2, 0.4.3, 0.4.4 (`yanked: true`), 0.5.0, each one `{upload_time, yanked}`; `torch-index.html`: wheel links `torch-2.14.0%2Brocm7.2`, `torch-2.14.1%2Brocm7.2`, `torch-2.15.0%2Brocm7.14`, `torch-2.15.0%2Bcpu`, each `-cp311-cp311-manylinux_2_28_x86_64.whl` (shapes as in `test_system_one_check_counts_releases_behind_and_newest_torch_per_index`).
- [ ] **Step 4: Rewrite `evals.json`** in route order. Fixture prompts name the files under `.claude/skills/onboard-local-model/evals/files/`, say how scripts read them (`LLM_ENV_CONFIG`/`LLM_ENV_MODELS_DIR`, `HF_FIXTURE_DIR`, `SO_FIXTURE_DIR`/`SO_DOCKERFILE`, "preflight.sh already printed ...; do not run it"), keep the DRY RUN clause and "Answer in English", and list their files in `files` as `evals/files/<path>` (relative to the skill root, as the existing GGUF entry does). `expected_output` is one sentence summarizing the assertions. jev appears only as advisory and never entering the score, and no eval text uses a Task 0 forbidden term (the guard scans `evals/**/*.json`).
  - Unchanged: `named-gguf-argument`, `runtime-image-review`, `anchor-401-retry`, `gateway-only-host`, `generate-one-challenge`.
  - `latest-fit`: drop "(about 10.1 GiB, not 16 GiB)"; assertion 2 -> "The VRAM budget is the smaller of llmenv_budget.vram_available_mib and model_vram_budget_mib from preflight.sh, named, never the 16 GB card total or live free VRAM".
  - `served-alias-argument` (`onboard-local-model gemma4`; files `models.yml`, `models/gemma4-v2-Q4_K_M.gguf`, `preflight-served.json`; llm-server RUNNING gemma4): resolve-target.sh with the fixture env gives kind alias, enabled true, present true, before any other step; it sizes against 8321 MiB (llmenv_budget.vram_available_mib, the smaller of 8321 and 10369) and says which; no scratch config: the fit is the preflight's llmenv_budget; its one ladder lane runs before G2 while it is served, on the router profile (router-env/llama-cpp/gemma4, --per-layer 3); G2 runs make stop-llama, then wait-gpu-release.sh --below-mib 6712 (13650 - 7450 + 512), never make stop, make restart or an omniroute restart, and System One stays up; bench-fit.sh runs after G2 at depths including 32768 and 131072; G3 edits nothing and serves it again with make start-llama, without make llama-enable, make setup or make benchmark; no replacement verdict: measured numbers and the capacity card, G5 has nothing to revert, then a leftover check; nothing was stopped, started, edited or benchmarked.
  - `disabled-alias-argument` (new; `onboard-local-model ornith-35b`, same fixtures, llm-server serving gemma4): resolve-target.sh gives enabled false and present false; because of present false, G1 downloads the entry's url (21155768832 bytes, about 19.7 GiB, free disk 182000 MiB) before validate-gguf or budget; the fit includes the RAM side (n_cpu_moe experts against ram_offload_budget_mib 12000) and states the 3100 MiB of swap in use as a G1 fact; the scratch config comes from resolve-target.sh --scratch-config and G3 adds no entry: make llama-enable MODEL=ornith-35b after the rollback copy; the gemma4 baseline lane runs before G2 on the router profile with --per-layer 3 and the candidate later takes its seeds.json; the verdict uses ladder delta >= 0 and overall delta > 10 on the same seeds, plus pp/tg at depth within about 3 %; nothing changed.
  - `hf-repo-argument` (`onboard-local-model https://huggingface.co/lab/Foo-9B-GGUF`; files `hf/*`, `preflight-served.json`): kind hf_repo for lab/Foo-9B-GGUF first; hf-scout.sh --repo lab/Foo-9B-GGUF --json --budget-mib 8321 --ram-mib 12000 with HF_FIXTURE_DIR gives fit vram and the pick Foo-9B-Q5_K_M.gguf (6500 MiB), not a --query scout; exactly one research subagent writes the evidence card, and its false gates or a failed hard gate are G1 facts; G1 states Foo-9B-Q5_K_M.gguf, 6500 MiB, free disk 182000 MiB and the destination ~/llm-workspace/models; the scratch config comes from resolve-target.sh gemma4 --scratch-config (redacted) with the entry added by yq, enabled false, never a cp of the live file; llmenv budget on the scratch config after the download is the authoritative fit, the hf-scout class is screening; nothing changed.
  - `onboard-plan-with-server-running` (prompt unchanged): resolve-target.sh Qwen3.8-27B exits 2 (a bare name), so the repo comes from hf-scout.sh --query Qwen3.8-27B --json (the first-party repo, or the owner picks among several) before any gate; then the existing assertions on G1 with size, a separate G2 via make stop-llama only, System One left running with the reason, depth throughput, leftover check; the G3 assertion becomes "a cp -p rollback copy is taken before the live models.yml is edited; make llama-enable's own backup is not the rollback copy"; add "the ornith-35b baseline lane runs before G2 on the router profile".
  - `no-argument-ranking` (replaces `no-argument-research`; files `scout-coder.json`, `scout-agent.json`, `evidence/1.json`, `evidence/2.json`, `preflight-served.json`; no network, no subagent dispatch, describe what would be sent): rank-candidates.sh over both scout files shortlists lab-a/Coder-9B-GGUF, lab-b/Agent-14B-GGUF and lab-d/MoE-30B-A3B-GGUF and names the gate each dropped row failed (lab-c/Coder-27B-IQ2-GGUF bpw, lab-e/Short-8B-GGUF ctx, lab-f/Dense-32B-GGUF fit); lab-b/Agent-14B-GGUF has no card, so --evidence leaves it out of ranked and excluded: it is reported unresearched with one research subagent to dispatch (section-7 brief), never as eliminated; lab-d/MoE-30B-A3B-GGUF (offload) does not go on because 3100 MiB of swap are in use, and the answer says so; ranked: lab-a/Coder-9B-GGUF 23, lab-d/MoE-30B-A3B-GGUF 20 with tool_template unverified; the lab-a agentic_evidence disagreement (card 2, jev 0) is re-checked from the card's sources and jev never changes the score; at most three go on, and nothing was downloaded, dispatched, stopped or edited; lab-d/MoE-30B-A3B-GGUF's empty jev block (no answer) leaves its card levels as they are, a jev question goes to the jev-fast-decision skill as its own SKILL.md says, and the answer adds no step about how that skill picks or reaches its backend.
  - `quant-head-to-head` (prompt unchanged): quant-headroom.sh check --alias <a> first, only fits true goes on; without a current capacity card or T8's seeds.json, T1-T8 run first (T9 not needed), then Q1; G-Q1 before fetch and G-Q2 before add-entry, separate; both quants run the ladder on T8's seeds.json through the router profile with the same --efforts and --timeout, and compare-runs.sh exit 3 means not a head-to-head; 5 x 3 x 2 x 300 s per lane stated before dispatch; G-Q3 is the owner's call and under bypass the current quant stays; Q11 restores the original alias and runs make local-combo only when my-coding-lite existed before; nothing changed.
  - `tune-capacity-card` (new; "write the capacity card for the local model and set up my-coding-lite", DRY RUN): preflight first and G-T1 (make llama-enable) only when llm_server.enabled is false; G-T2 frees the GPU with make stop-llama and wait-gpu-release.sh (U - H + 512) while System One stays; tune-sweep.sh goes to one subagent with the brief, on the default grid unless axes are named, its upper bound relayed first; tune-pick ranks ctx first, then tg at min(131072, ctx - 512) with a 20 t/s floor, and tune-apply.sh runs under G-T3 with its backup path; make benchmark is not run in this procedure because it can rewrite gpu.backend to cpu; make model-interference, then make model-capacity with DEPTH and with SCORE from a router-profile ladder (--efforts low,high --per-layer 3); make local-combo and make setup-local-llm-agents, confirmed with the jq check of my-coding-lite; nothing changed.
  - `anchor-comparison` (prompt -> "compara el modelo local que tengo ahora contra haiku 4.5, gpt-5.6 luna y composer 2.5 en tareas de codigo y dame un score. DRY RUN ..."): Composer 2.5 has no pi route: cursor-agent models is checked or it is compared by published numbers only, never counting the OmniRoute grok-composer variant; credentials are checked with pi auth check --model <provider/id> on PI_CODING_AGENT_DIR=$HOME/.pi/agent before any run; G4 is asked before the first anchor run with provider, model, runs and upper bound; the local lane runs first on the router profile (router-env/llama-cpp/<alias>, --per-layer 3) and writes seeds.json, and every anchor lane passes --seeds with the same --efforts and --timeout; compare-runs.sh --correctness-only gives local overall over anchor overall with both ladders, and published numbers appear only with version and effort; the seed set is retired after the cloud lanes; no run was launched.
  - `system-one-check` (new; "is laya outdated? anything that would use less vram or fix the busy cpu core?"; files `system-one/*`): system-one-check.sh with SO_FIXTURE_DIR and SO_DOCKERFILE reports Laya pinned 0.4.2 and latest 0.5.0, 2 releases behind (the yanked 0.4.4 not counted), torch 2.14.1 on rocm7.2; the report is a table of pin, latest, gap, what the gap contains and risk, and the release notes it could not read offline (github.since_pin null) are listed as not verified; torch 2.15.0 on rocm7.14 is not an upgrade by version: the rocm7.2 pin works around the ROCR busy-spin and moves only with the fix released and the 10-minute idle check; a trial builds under another tag on port 8011 only after freeing the GPU (G2) and is removed afterwards, and replacing production is G6; the /v1/systemone contract stays and the value is measured against 4.26 GB steady VRAM; nothing was built, pulled, run or recreated.
  - `dense-27b-verdict`: add `preflight-served.json` to `files` and the prompt; assertion 2 -> "The answer compares the Q4_K_M file size with the 8321 MiB budget from the fixture and shows the arithmetic"; assertion 5 adds "and notes the 3100 MiB of swap in use before offering an MoE offload".
- [ ] **Step 5: Rewrite `trigger-queries.json`** (20 objects `{"query", "should_trigger"}`, this order).
  - true: "want to try Qwen3.8-27B, or the smaller coder one if it doesn't fit, against what pi uses now"; "https://huggingface.co/unsloth/Qwen3.8-9B-Instruct-GGUF would this run next to laya? set it up if it does"; "a friend sent me ~/Downloads/Devstral-Small-3-Q4_K_M.gguf, can we swap the local model for it?"; "hay algun modelo agentico mas nuevo que quepa en mi gpu con laya cargado? necesito 128k de contexto minimo"; "is q5 or q6 of the current model worth it over q4, and is there even vram for it?"; "pi crawls in long sessions since the 256k change, what tok/s do we actually get at 128k depth?"; "set up my-coding-lite and write the capacity card for the local model"; "how does our local model compare to haiku 4.5 on coding tasks? i want an actual number, not leaderboard stuff"; "our llama.cpp image is ~1500 builds behind, worth pulling a new vulkan build or trying rocm for prompt speed?"; "laya is a couple releases behind on pypi, anything in there that frees vram for a bigger model?".
  - false: "make gpu-status says steamwebhelper holds VRAM, which process should I move to the iGPU?"; "update omniroute to the latest image without interrupting running agents"; "system-one's memory cap keeps getting clipped at 5120 MiB, how do I re-derive the limit"; "ornith rambles, tune temperature and top_p in models.yml"; "convert my safetensors fine-tune to gguf and quantize it to q4_k_m"; "add kimi to the my-coding combo and drop grok from it"; "which cloud model is cheapest for a coding agent through openrouter?"; "write a python function that reads a GGUF header and lists the tensor sizes"; "pi can't reach the router, check-server returns 401 after I rotated the key"; "fine-tune qwen3.5-9b with LoRA on our commit history on this GPU".
- [ ] **Step 6: Verify.** The Step 2 command passes; `jq '.evals | length' $SK/evals/evals.json` prints 16 and `jq -r '.evals[].id' $SK/evals/evals.json | sort | uniq -d` prints nothing; `jq 'length, (map(select(.should_trigger)) | length)' $SK/evals/trigger-queries.json` prints 20 and 10; `grep -c onboard-local-model $SK/evals/trigger-queries.json` prints 0; `uv run pytest tests/skills/onboard-local-model -q --basetemp=$HOME/.cache/llm-env-nav-pt -k black_box` passes; `make validate`.

---

### Task 4: Route walk and final verification (last)

**Files:**
- Modify: `docs/superpowers/plans/README.md` (this plan's row; uncommitted)

- [ ] **Step 1: Route walk.** Read `SKILL.md` and follow each route to its end in the new files: served alias, disabled alias, Hugging Face repo, local GGUF, bare name, none (read-only and onboard), quant head-to-head, tuning, anchors without a candidate, runtime, System One. For each of R1-R16 write one line `R<n>: closed at <file>:<section>` to `tmp/skill-nav/route-walk.txt`; any route that needs a fact from a reference its row does not load, or a step whose next step is undefined, is a failure to fix in the owning task's file before continuing.
- [ ] **Step 2:** `make validate`. Expected: `All checks passed.`
- [ ] **Step 3:** `PYTEST_ADDOPTS="--basetemp=$HOME/.cache/llm-env-nav-pt" make test`, then `uv run pytest tests/skills -q --basetemp=$HOME/.cache/llm-env-nav-pt | tail -1`. Expected: all pass, six more tests than `tmp/skill-nav/tests-before.txt` (one each from Tasks 0, 1 and 2, three from Task 3).
- [ ] **Step 4: Scripts untouched.** `mkdir -p tmp/skill-nav/before && tar -C tmp/skill-nav/before -xzf tmp/skill-nav/onboard-local-model-before.tgz && diff -r tmp/skill-nav/before/onboard-local-model/scripts $SK/scripts && diff -r tmp/skill-nav/before/onboard-local-model/challenges $SK/challenges`. Expected: no output. Then `rm -rf tmp/skill-nav/before`.
- [ ] **Step 5: Leftovers.** `rm -rf $HOME/.cache/llm-env-nav-pt`; `ls tmp | grep skill-test-` prints nothing; `ps -eo pid,args | grep -E "onboard-local-model/scripts/|node --test" | grep -v grep` shows nothing this work started; `podman ps -a` lists nothing new. Keep `tmp/skill-nav/onboard-local-model-before.tgz` and `route-walk.txt` for the orchestrator.
- [ ] **Step 6: Git.** `git status --short` shows no tracked change beyond this plan's row in `docs/superpowers/plans/README.md` (plus parallel work); `git log --oneline -1 -- pylib/capacity.py` shows Task 0's commit. Report the changed, created and deleted files under `$SK` and `tests/skills/onboard-local-model/` for the owner's decision on committing the skill tree.
- [ ] **Step 7:** Set this plan's row in `docs/superpowers/plans/README.md` to **DONE** with "skill edits uncommitted with the untracked skill tree; judges re-run by the orchestrator". Leave it uncommitted.

No commit in this task.

---

## Judge protocol (orchestrator, after Task 4; not a task of this plan)

- Run both lanes as in rounds 1-5 (native Opus subagent with clean context and no earlier scores; `router-env/my-referee-two` through ai-kit cross-ai with the r5 prompt), rubric v4, outputs `tmp/judges/r6-*`, then a second round `r7-*` on the unchanged skill to measure stability.
- Pass: both lanes >= 90.0 % in r6 and r7, and no critical or confirmed high (a single-lane high is checked against the files before it counts, as the r1-r5 summaries did). With lane noise of about +-2 points, a stable pass needs about 92 % expected per lane.
- On a pass, delete `tmp/skill-nav/`. On a fail, findings go to a new plan; restore from the tarball only if the owner asks.
- Expected lift (estimate, not a promise): D8 +2 to +3 (R1-R13 closed, one owner per rule), D5 +1 to +2 (one level deep, do-NOT-load column), D2 +1 (route table, before-gate question; 9(c) keeps its open wording by Decision 9, so the r5 low D2-1 may recur), D1 +1 (no duplicated steps or restated rules), D7 +1 (fixture-driven evals pinned by tests).

## Self-Review

- Coverage: jev black box and no ai-kit name, guard first, tracked docs committed (Task 0; Decisions 12, 14; R16; Review Focus 6); Decisions 6 and 9 recorded as decided and carried into Task 1 (profile rule, replace rule kept in `scoring.md`); navigation entry, description, route table, rules, gates, NEVER with reasons (Task 2); one flow per family, merged reference, load triggers, one level deep (Task 1); route walk (table above, Task 4 Step 1); evals with fixtures and 20 trigger queries (Task 3); doc tests updated in the tasks that change their files; final verification last (Task 4); judge re-run outside the plan.
- Names across tasks: `JEV_RULE`, `JEV_FORBIDDEN`, `JEV_NAMED_IN`, `JEV_ALLOWLIST`, `test_skill_and_touched_docs_treat_jev_as_a_black_box` (`-k black_box`), `REF_NAMES`, `SKILL_MD_MAX_BYTES`, `FIX`, `_asserts`; "the replace rule (Score, item 6, of the effort-ladder reference)"; sections Alias, Hugging Face, Local GGUF, Scout pick, Find the repo, Scout and rank, Running a lane, Score, Head-to-head, Anchor lanes, Freeing the GPU; eval ids `served-alias-argument`, `disabled-alias-argument`, `hf-repo-argument`, `onboard-plan-with-server-running`, `no-argument-ranking`, `quant-head-to-head`, `tune-capacity-card`, `anchor-comparison`, `system-one-check`, `dense-27b-verdict`.
- Not verified when written: the judges' scores; the new tests themselves (code reviewed, not run; the fixture outputs they assert were measured with the scripts, Fact 9); PyYAML on the new description (`yq` parses it); router-profile lanes under OmniRoute load (Decision 6); whether `make start-llama` recreates llm-server with a newly pulled image (`runtimes.md` keeps its owner-approved `make restart` apply path); the guard test itself (not run; a bash emulation of its scan over the current files, whitespace collapsed as `_md` does, listed exactly Step 3's expected failures; a future rewording outside its phrase list is caught only by review); that `make test` is green at Task 0 (it depends on the rest of the suite and on parallel work); that Decision 9's "not adopted" reading of the 9(c) proposal matches the owner's intent.

## Round-1 review outcome (approved, 0 confirmed critical/high, 8 medium applied as amendments)

Review round 1 (native + referee-two, lead-verified; `tmp/plan-g-review/review-round-1.md`) confirmed no critical or high finding; the
five referee critical/highs were refuted. Plan status: approved for execution. The implementer applies these amendments; where one
conflicts with the text above, the amendment wins. Owner rules unchanged: `jev-fast-decision` stays a black box (only the fixed
sentence), no ai-kit name anywhere in the skill or product code, Decision 9's single replace rule stays only in `scoring.md`.

- M1 (Task 2 rule 1): add the null fallback: "when `llmenv_budget` is null (for example a gateway-only host with nothing enabled),
  size against `budget.model_vram_budget_mib` and say so". The `selection-criteria.md` section 1 pointer to rule 1 stays.
- M2 (R7, fresh host; Task 1 `onboard-flow.md` step 1 Preflight, `tuning.md` T1, `anchors.md` Anchor lanes (1); Task 2 rule 3):
  before the first ladder lane check that `~/.pi/agent/models.json` exists and lists `llama-cpp/<alias>` under `router-env`; when it
  does not, run `make setup-local-llm-agents` first. Rule 3 states this once; the flow steps name it ("rule 3 profile check").
- M3 (Task 2 route table, no-argument row): the "Also load" cell becomes "once the owner picks a row to onboard, continue on that
  row's flow (the Hugging Face row), which loads `references/scoring.md` before its ladder", so the row never blocks `scoring.md`
  for a route that runs `effort-run.sh`. Its do-NOT-load cell keeps `anchors.md`, `challenges.md` and drops `scoring.md`.
- M4 (Task 3 fixture vs Decision 12): `evidence/1.json`'s `notes` mark `agentic_evidence` uncertain (for example
  "agentic_evidence uncertain: one vendor claim, no third-party run"), so its `jev` answer follows Decision 12. Fact 9's numbers stay.
- M5 (Decision 8, Task 1 step 7a): the `cp -p` rollback copy is taken before the first live models.yml edit at G3, whichever it is
  (the `yq -i` add for a new entry, or `make llama-enable` for an existing or disabled alias); `make llama-enable`'s own backup is
  never the one step 9 restores.
- M6 (Task 1 step 8 Verdict): add Decision 10 there too: "without a baseline (no enabled entry), the verdict rests on (a), the 20 t/s
  floor and the ladder reached; (b) and (c) are skipped and said so; G5 still asks".
- M7 (Task 1 `tuning.md` Q1/Q11): Q1 records whether `my-coding-lite` existed, with the T9 check
  `jq '.providers["router-env"].models[] | select(.id == "my-coding-lite")' ~/.pi/agent/models.json` (empty output = absent), in the
  run directory; Q11 runs `make local-combo` only when that record says it existed.
- M8 (Global Constraints, Allowed edits): add `docs/superpowers/plans/README.md` (this plan's row only, uncommitted; Task 4 Step 7).
