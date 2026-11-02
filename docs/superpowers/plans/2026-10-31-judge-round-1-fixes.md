# onboard-local-model Judge Round 1 Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Lift the project-local skill `.claude/skills/onboard-local-model` over the pass bar of the skill-judge round (no critical or high finding and >= 90 % from both judges; the native judge needs about +5 points, 108 -> >= 113 of 125) with the smallest changes that really fix the lost points: a served-alias route an agent can follow, explicit load triggers, an eval input file plus branch, head-to-head and generated-challenge evals, a description with the missing triggers and a non-conflicting boundary, a 20-query trigger set, and fewer restated repo rules.

**Architecture:** Text-only changes to `SKILL.md`, three references, `evals/evals.json` and `evals/trigger-queries.json`, one 64-byte eval fixture and one structural pytest. No script changes, so nothing a running measurement uses is touched. `SKILL.md` stays under 14,500 bytes by deleting restated rules and duplicates and moving one Gotcha to the reference read at the step that runs it.

**Tech Stack:** Markdown, JSON, YAML front matter (PyYAML in the test), bash, jq, yq, pytest (+ xdist), shellcheck.

**Spec:** the owner instructions given to the plan writer on 2026-10-31 (restated in Goal, Decisions and Global Constraints) and the judge verdicts they rule on: `tmp/judges/r1-summary.txt`, `tmp/judges/r1-native.txt` (86.4 %, B: D1 16/20, D2 13, D3 15, D4 13, D5 12, D6 14, D7 13, D8 12), `tmp/judges/r1-referee-two.txt` (90.4 %, A: D1 16, D2 15, D3 15, D4 14, D5 13, D6 14, D7 11, D8 15). Rubric: `~/.agents/skills/skill-judge/references/rubric.md` (v4). Every finding used here was re-checked against the files (Facts; rejected ones in Findings rejected). Sibling plans, not edited: `2026-10-31-audit-fix-wave.md`, `2026-10-31-skill-model-or-none-and-generated-challenges.md`.

## Global Constraints

- **Skill tree rule (as in the sibling plans):** `.claude/skills/` and `tests/skills/` are untracked on purpose; nothing under them is committed. This plan file and `docs/superpowers/plans/README.md` stay uncommitted with the index's other pending edits. No task commits.
- No script under `$SK/scripts/` or `$SK/challenges/` changes. Nothing in this plan loads the GPU or starts a container; another session may be measuring, so never run `bench-fit.sh`, `effort-run.sh`, `latency-profile.sh`, `tune-sweep.sh` or `make benchmark`.
- Parallel work may edit the same files: re-read each file at execution time, keep edits you did not make, change only the passages named.
- Kept doc tests must stay green: `ISOLATION_SENTENCE` stays verbatim in `SKILL.md` step 7; `## Gates` keeps "permissions bypassed", "announce the facts, then proceed", "deletes user data", "never switch provider or model", "a yes to one never covers the next"; no backtick span in `SKILL.md` or `references/*.md` holds a loop, `; then`, or more than two `&&`/`||`/`;`; every `*.sh` named in `SKILL.md`, `references/*.md` or `evals/*.json` exists; the description parses with `yaml.safe_load` and has 1-1024 characters.
- `SKILL.md` at most 160 lines and 14,500 bytes (no test enforces the byte cap; it is the target set by `2026-10-31-audit-fix-wave.md` Task 16 Step 5). Not raised.
- No new doc-phrase tests (the owner cut them to three plus the scripts-exist test). Task 4 adds one structural test.
- After any `.py` edit: `make validate && make test`. Pytest runs use `--basetemp` under `tmp/judge-r1-fixes/` (for `make test`: `PYTEST_ADDOPTS="--basetemp=tmp/judge-r1-fixes/pytest" make test`).
- English only. Scratch: `tmp/judge-r1-fixes/` (removed in Task 5). Never delete or rename plans. Commands run from the repo root with `SK=.claude/skills/onboard-local-model`.

## Facts (read or measured 2026-10-31)

Line numbers here and in every task are from the working tree before Task 1; edits shift them, so find each passage by its quoted text.

1. `SKILL.md`: 160 lines, 14,494 bytes; description 421 characters. Tests: `test_skill_description_fits_the_spec_limit` (<= 1024 characters), no byte test.
2. D8-1: `SKILL.md:18-20` routes an alias through "4 ... then 6-7 ... (no G2/G3 when it is the enabled, served model)"; `bench-fit.sh:40-42` exits 3 while the `llm-server` container runs; the route never reaches step 9 (G5, rollback).
3. D5-1: `SKILL.md` never names `models-yml.md`, `scoring.md` or `anchors.md`; only `onboard-flow.md:9,35,42` do.
4. D7-1: `evals.json:6` names `~/Downloads/Foo-9B-Q4_K_M.gguf`; no `files` key, no `evals/files/`. No eval covers the `alias` or `hf_repo` route, a quant head-to-head or generating a challenge.
5. D7-2: `trigger-queries.json` has 29 queries, 17 should-trigger and 12 should-not (the referee's 18/12 is off by one); the first names the skill.
6. D8-3: `SKILL.md:139-140` and `:150-151` allow `make llama-enable` "only after G3"; `tuning.md:17` runs it under G-T1, and Quant headroom Q3/Q4 under G-Q2.
7. D8-6: `models-yml.md:43-44` sets `S=tmp/model-scout/<run>/models.yml` and `cp`s the live file (API key included). `resolve-target.sh:97` writes a redacted copy but refuses Hugging Face targets (`:87`). Measured: `resolve-target.sh ornith-15-9b --scratch-config <tmp file>` wrote `server.api_key: redacted`, an empty dashboard password, only that alias enabled.
8. D8-2: `rank-candidates.sh:18` defaults to `top=6`; `SKILL.md:34` researches "per shortlisted candidate", `:71` "Shortlist at most three". The sibling plan's Decision 12: at most 6 researched, at most 3 shortlisted.
9. D8-4/D6-1: `SKILL.md:27-28` offers two copy destinations with no default; `tools/lib.sh:8` serves models from `LLM_ENV_MODELS_DIR`, default `~/llm-workspace/models`.
10. D8-5: `SKILL.md:70` runs `hf-scout.sh` without `--json`; `rank-candidates.sh` reads `--json` scout files (header line 4).
11. Own finding: `SKILL.md:107` says `preflight.sh` checks `yq jq podman uv git node pi`; `preflight.sh:12` checks only `yq jq podman uv` (`gen-challenge.sh:61` checks `node git`; nothing checks `pi`).
12. Fixture measured on a scratch copy: `GGUF` + 60 zero bytes (the tests' `GGUF_BYTES`) named `Foo-9B-Q4_K_M.gguf` -> `resolve-target.sh` prints `kind: gguf_file, size_bytes: 64, quant: Q4_K_M`; on its `--scratch-config` copy `validate-gguf` prints `valid: true`, and `budget` prints `{"error": "general.architecture missing from GGUF metadata"}` with exit 1.
13. `gen-challenge.sh --layer 2 --seed 11 --out <tmp dir>` exits 0 (project `paginate`), `--verify <dir>` prints `{"verified":true,"changed":[]}`, `challenge/` has 1 commit and no `reference/`.
14. skill-creator's `run_loop.py:24-38` (`split_eval_set`) makes its own seeded, stratified train/test split; a hand-marked hold-out is not read.
15. `make test` runs `uv run pytest tests/ -n auto -v` (`tools/test.sh`); pytest reads extra options from `PYTEST_ADDOPTS`.
16. A scratch copy of `SKILL.md` with every edit of Tasks 1-3 measured 148 lines and 14,158 bytes; the new description is 879 characters.

## Decisions

1. **Served alias** keeps G2 and `bench-fit.sh`: its depth numbers must come from the same tool as the candidates' step 6. G3 edits nothing for it and G5 has nothing to revert.
2. **Local GGUF copy default:** `~/llm-workspace/models` (one copy, where a download lands and step 7 serves from); `tmp/model-scout/<stamp>/models` only when the owner wants the models directory untouched, and then G3 copies it on.
3. **Research count:** up to 6 rows (the `rank-candidates.sh` default), at most 3 go on to step 4.
4. **Scout default:** one saved `--json` file per term `coder`, `agent`, the baseline's family name; add a term only when no row has `fit: vram`.
5. **Scratch config:** one name, `scratch.yml`; always a redacted copy from `resolve-target.sh --scratch-config` (for a Hugging Face candidate, on the baseline alias); never `cp` the live file; with no alias to copy from, stop and report.
6. **Load triggers** live in `SKILL.md` at the step that needs each reference, plus one "do NOT load" line for the read-only modes (not one line inside each reference, which is read only after loading).
7. **Size:** room comes from deleting `## Progress` (one format line kept), `## Helpers`, the two AGENTS.md ladder restatements, the bounded-runner Gotcha (no agent action) and the anchor-profile Gotcha (duplicate of `anchors.md:28-34`, which step 7 now loads for anchors), and from moving the `budget` Gotcha into `models-yml.md` Apply step 3, where `budget` runs.
8. **Description:** the native lane's coverage (quant head-to-head, depth tok/s, cloud anchors on generated challenges, image updates, two quoted user phrasings) with the referee's keywords (capacity card, effort ladder, my-coding-lite); the boundary names "picking a cloud provider", not "cloud providers", so the anchor query triggers. Text in Task 3.
9. **Trigger set:** 10 should-trigger and 10 should-not (lists in Task 3); no hold-out field (Fact 14).
10. **Eval fixture:** the 64-byte header-only GGUF of Fact 12. It exercises routing and gates; its `budget` error adds an honest-error check. A full-metadata fixture needs a binary generator for no extra coverage. No fixture `models.yml` (Findings rejected).

## Review Focus

Failure modes the owner's input implies that no pytest exercises; each has its eval or query in the owning task.

1. The owner names the alias already being served while llm-server runs: the run must ask G2, not die on `bench-fit.sh` exit 3, and must not run `make setup` (image pull) for it [Task 4 eval `served-alias-argument`].
2. The owner names a GGUF in a personal directory: never mount that directory (`:z` relabels it), copy at G1 into the models directory [Task 4 eval `named-gguf-argument`, assertion 5].
3. A Hugging Face candidate on a host whose live config holds the API key: the scratch copy must be redacted [Task 4 eval `hf-repo-argument`, assertion 5].
4. Two lanes run on different seeds or efforts: `compare-runs.sh` exit 3 means "not a head-to-head", never a verdict [Task 4 eval `quant-head-to-head`, assertion 4].
5. "Compare with Haiku and Luna, I want a score" must trigger despite the cloud boundary [Task 3, kept should-trigger query 6].

---

### Task 1: Load triggers and expert-only text (D5-1, D2-1, D1-1, D1-2, referee D5-2)

**Files:**
- Modify: `$SK/SKILL.md` (after line 48; 54-59; 79; 86; 95-102; 112-113; 131-132; 152-158)
- Modify: `$SK/references/models-yml.md` (Apply step 3, lines 46-48), `$SK/references/challenges.md` (line 10)

**Interfaces:**
- Produces: `SKILL.md` names every optional reference with its trigger; Task 2 edits other lines of the same file.

- [ ] **Step 1: Baseline.** `mkdir -p tmp/judge-r1-fixes`; `wc -lc $SK/SKILL.md > tmp/judge-r1-fixes/size-before.txt` (expect `160 14494`); `uv run pytest tests/skills -q --basetemp=tmp/judge-r1-fixes/pytest | tail -1 > tmp/judge-r1-fixes/skill-tests-before.txt`; `make validate`. Expected: tests pass, `All checks passed.` If not, stop and report.
- [ ] **Step 2: Edit `SKILL.md`.**
  - After the mode table (line 48), a blank line, then:
    ```text
    A read-only row loads only the reference its steps name (`selection-criteria.md`, `runtimes.md`, `system-one.md`); do NOT load
    `onboard-flow.md`, `models-yml.md`, `scoring.md`, `challenges.md` or `anchors.md` for it.
    ```
  - Delete `## Progress` with its paragraph (54-57). Under `## Workflow`, before step 1, add (blank line after it):
    ```text
    A route that reaches step 4 reads `references/onboard-flow.md` (steps 4-9 in full) first. Report stages as `[3/10 scout] done: 3 shortlisted`.
    ```
  - Step 4, line 79 becomes:
    ```text
       scratch config under `tmp/` per `references/models-yml.md` (read it before any config edit, here and at G3). **Gate G1: download**
       the top-ranked fit only. Detail: `references/onboard-flow.md` step 4.
    ```
  - Step 7: after "Detail: `references/onboard-flow.md` step 7." insert "Read `references/scoring.md` before reading `score.sh` output, `references/challenges.md` before a head-to-head, and `references/anchors.md` only when the user names a cloud anchor." The isolation sentence stays as it is.
  - Delete `## Helpers` and its five bullets (95-102).
  - Gates, lines 112-113: "never switch provider or model; retry the same lane, then change session, then report (AGENTS.md)." becomes "never switch provider or model (AGENTS.md retry ladder)."
  - NEVER `agent_status`, lines 131-132: after "`score.sh` excludes those runs as `invalid_runs`" the rest becomes "; re-run the lane per the AGENTS.md ladder."
  - Gotchas: delete the `uv run llmenv.py ... budget` item (152-155, moved below), the bounded-runner item (156) and the anchor-profile item (157-158).
- [ ] **Step 3: Edit the references.**
  - `models-yml.md` Apply step 3, after "do not loosen `vram_budget` to make it pass.":
    ```text
       The same command on the live file (`--config ~/.config/llm-env/models.yml --models-dir ~/llm-workspace/models`) is the
       authoritative check. `budget` errors on a wrong `--models-dir` or a missing enabled file. On a config with
       `llm_server.enabled: false` it reports 0 MiB of RAM for llm-server, so an MoE offload shows `ram_feasible: false` until local
       inference is enabled: report that as a precondition, not as a verdict on the model.
    ```
  - `challenges.md` line 10, after "One challenge is a library project plus a seed.": "Run it per `bash $SK/scripts/gen-challenge.sh --help`; open the files below only when authoring or debugging the library."
- [ ] **Step 4: Verify.** `grep -n "references/models-yml.md\|references/scoring.md\|references/anchors.md\|do NOT load" $SK/SKILL.md` shows the step 4 line, the step 7 line and the read-only line; `grep -n "## Progress\|## Helpers\|change session" $SK/SKILL.md` prints nothing; `wc -c $SK/SKILL.md` < 14494; `uv run pytest tests/skills/onboard-local-model -q --basetemp=tmp/judge-r1-fixes/pytest -k "bypassed or isolated_dirs or long_shell or name_only_existing or spec_limit"` passes.
- [ ] **Step 5: No commit.** Record the touched files in `tmp/judge-r1-fixes/touched.txt`.

---

### Task 2: Routes, counts and gates an agent can follow (D8-1 to D8-6, D6-1 of both lanes, Fact 11)

**Files:**
- Modify: `$SK/SKILL.md` (the `## Input` routes, step 3, the Gates prerequisite line, the NEVER `llm_server.enabled` item, the first Gotcha; line numbers below are pre-Task-1)
- Modify: `$SK/references/onboard-flow.md` (lines 9-10, 21, 30), `$SK/references/models-yml.md` (lines 43-44)

**Interfaces:**
- Consumes: Task 1's `SKILL.md`.
- Produces (Task 4's evals assert on these): served alias = G2 kept, G3 edits nothing, `make start-llama`, G5 nothing to revert; GGUF copy default `~/llm-workspace/models`; research up to 6, at most 3 on; one `scout-<term>.json` per term; scratch file `scratch.yml`, redacted, made by `resolve-target.sh`.

- [ ] **Step 1: Edit `SKILL.md`.**
  - `alias` route (18-20) becomes:
    ```text
    - `alias` -> steps 1, 2, 4 on the scratch config from `bash $SK/scripts/resolve-target.sh <model> --scratch-config tmp/model-scout/<stamp>/scratch.yml`
      (that alias alone enabled; `validate-gguf` and `budget` take `--config` = that file), then 5-9. When it is the enabled, served
      model, G2 still applies (`bench-fit.sh` exits 3 while llm-server runs), G3 edits nothing (step 7 restarts it with
      `make start-llama`) and G5 has nothing to revert.
    ```
  - `gguf_file` route: "steps 1, 2, then 4 on the scratch config from the same `--scratch-config` call (entry `candidate`, the only one enabled)." becomes "steps 1, 2, 4 on the scratch config from the same `--scratch-config` call (entry `candidate`, the only one enabled), then 5-9."; and from "G1 announces it" to "never move or delete the original." (27-29) becomes:
    ```text
      G1 announces it (file size, free disk, destination) and copies the file into `~/llm-workspace/models`, the models directory step 7
      serves from; only if the owner wants that directory untouched while screening, copy into `tmp/model-scout/<stamp>/models`
      (`LLM_ENV_MODELS_DIR` for step 6) and G3 copies it on. Never move or delete the original.
    ```
    The `:z` sentence after it stays.
  - "Without a model" (33-35): "scout (step 3) with several `--query` terms plus its findings; `bash $SK/scripts/rank-candidates.sh --scout <file>` gates and orders the rows; dispatch one research subagent per shortlisted candidate in parallel" becomes "scout (step 3) with its findings as extra terms; `bash $SK/scripts/rank-candidates.sh --scout <f1> --scout <f2> ...` gates and orders the rows (top 6); dispatch one research subagent per row, in parallel".
  - Step 3 (70-73): the opening up to "then read `references/selection-criteria.md`" becomes:
    ```text
    3. **Scout.** `bash $SK/scripts/hf-scout.sh --query <term> --json > tmp/model-scout/<run>/scout-<term>.json` per term: `coder`,
       `agent` and the baseline's family name; add a term only when no row has `fit: vram`. Shortlist at most three, after researching up to 6 in the no-argument route (none fits or beats the baseline: report a null result and stop before step 4), then read `references/selection-criteria.md` and research each: model
    ```
    and delete "One research subagent per candidate is fine; pass it the criteria file path."
  - Gates (107): "Prerequisites: `yq jq podman uv git node pi` on PATH (`preflight.sh` checks)." becomes "Prerequisites: `yq jq podman uv` (`preflight.sh` checks them), plus `git node pi` for the ladder."
  - NEVER `llm_server.enabled` (140): "and only after G3." becomes "and only after its gate (G3 here; G-T1, or G-Q2 for a quant trial, in `references/tuning.md`)."
  - First Gotcha (150-151): ", which is flipped only after G3 with `make llama-enable MODEL=<alias>` (ask first)." becomes " (flip it per the NEVER item above)."
- [ ] **Step 2: Edit `onboard-flow.md`.** Line 10: "on a scratch copy of the config under `tmp/`" becomes "on the scratch config `$S` (`tmp/model-scout/<run>/scratch.yml`, redacted)". Line 21: `LLM_ENV_CONFIG=<scratch models.yml>` becomes `LLM_ENV_CONFIG=$S`. Line 30, after "so pi has `llm-env/<alias>`.": "For the enabled, served alias (SKILL.md `## Input`) G3 edits nothing: skip the entry, `make llama-enable`, `make setup` and `make benchmark`, and serve it again with `make start-llama`."
- [ ] **Step 3: Edit `models-yml.md`.** Line 43: `S=tmp/model-scout/<run>/models.yml` becomes `S=tmp/model-scout/<run>/scratch.yml`. Step 1 (44) becomes:
  ```text
  1. `$S` is the `--scratch-config` file of SKILL.md `## Input` (alias, local GGUF). For a Hugging Face candidate create it with
     `bash $SK/scripts/resolve-target.sh <baseline alias> --scratch-config $S` (API key and dashboard password redacted; never `cp`
     the live file into `tmp/`; with no alias in the live file, stop and report), then add the entry to `$S` with `yq -i`, `enabled: false`.
  ```
- [ ] **Step 4: Verify.** `grep -rn "cp ~/.config/llm-env/models.yml\|<scratch models.yml>" $SK` and `grep -n "only after G3\|6-7 under" $SK/SKILL.md` print nothing (`models-yml.md`'s "Live phase (... only after G3)" heading is correct and stays); `grep -n "then 5-9" $SK/SKILL.md` shows the alias and gguf routes; `wc -lc $SK/SKILL.md` at most 160 lines and 14500 bytes; the Task 1 Step 4 pytest command passes.
- [ ] **Step 5: No commit.** Append the touched files to `tmp/judge-r1-fixes/touched.txt`.

---

### Task 3: Description and trigger queries (D4-1, D7-2)

**Files:**
- Modify: `$SK/SKILL.md` (line 3), `$SK/evals/trigger-queries.json`
- Test: `tests/skills/onboard-local-model/test_onboard_local_model_scripts.py::test_skill_description_fits_the_spec_limit` (existing)

- [ ] **Step 1: Replace the description** (one plain YAML scalar on line 3; no `: ` or ` #` inside):
  ```text
  Find, fit, benchmark, tune and onboard a local agentic-coding GGUF for this repo's llama.cpp router, sharing the GPU with System One (Laya). Use when the user asks which local model fits the VRAM or beats the one in models.yml, wants to add, try or swap a GGUF, alias or Hugging Face model, compares quants (Q4_K_M vs Q5_K_M) head to head, wants pp/tg tokens per second at context depth, tunes ctx_size, KV cache or n_cpu_moe, wants the capacity card, effort ladder or my-coding-lite, scores the local model against cloud anchors (Haiku, Luna, Composer) on generated challenges, or asks whether the llama.cpp image (Vulkan/ROCm) or Laya is outdated, even as "is there a faster model for pi" or "modelo agéntico que quepa en mi GPU". Not for OmniRoute updates or combos other than my-coding-lite, picking a cloud provider, sampling settings, fine-tuning or quantizing checkpoints.
  ```
- [ ] **Step 2: Rewrite `trigger-queries.json`** as 20 objects `{"query", "should_trigger"}`, the existing strings verbatim, in this order.
  - true: "compare Q4_K_M and Q5_K_M of the local model head to head on fresh challenges"; "what's the newest agentic coding model I can run on my 9070 XT with the 10 GB or so that System One leaves free? check hugging face for updates"; "cual es el ultimo modelo agentico que cabe ahora en mi equipo"; "is there anything better than ornith-35b in our models.yml for pi? needs at least 128k context"; "add this gguf from hugging face to models.yml, figure out n_cpu_moe and context, and benchmark it against what we have"; "compare the local model with haiku 4.5 and gpt-5.6 luna on a few coding tasks, I want a score"; "should we move the llama.cpp image to a newer build or switch from vulkan to rocm? what tps would we get"; "pi feels slow on the local model since I moved to the 256k context, is there a faster setup that still fits?"; "write the capacity card for ornith-35b"; "find the best ctx_size and KV cache for the local model without slowing Laya".
  - false: "omniroute is routing my combo to the wrong provider, the smart strategy isn't picking kimi"; "make gpu-status says steamwebhelper holds VRAM, which process should I migrate to the iGPU?"; "update omniroute to the latest image without interrupting running agents"; "which cloud model is cheapest for a coding agent through openrouter?"; "system-one's memory cap keeps getting clipped at 5120 MiB, how do I re-derive the limit"; "write a python function that parses a GGUF header and returns the tensor sizes"; "fine-tune qwen3.5-9b on our commit history with LoRA on this GPU"; "pi can't reach the router, check-server returns 401 after I rotated the key"; "convert my own safetensors checkpoint to gguf and quantize it to q4_k_m"; "tune the sampling temperature and top_p for ornith in models.yml".
  - Dropped: the query that names the skill, the effort-comparison, 27B-quant, unload-and-ubatch, Spanish HF-search and newer-qwen-coder positives (near-duplicates of kept ones), the SKILL.md-grading and key-rotation negatives (far or duplicate).
- [ ] **Step 3: Verify.** `yq -r --front-matter=extract '.description' $SK/SKILL.md | tr -d '\n' | wc -m` prints 879; `jq 'length, (map(select(.should_trigger)) | length)' $SK/evals/trigger-queries.json` prints 20 and 10; `grep -c onboard-local-model $SK/evals/trigger-queries.json` prints 0; `wc -lc $SK/SKILL.md` about 148 lines and 14158 bytes, at most 14500; `uv run pytest tests/skills/onboard-local-model -q --basetemp=tmp/judge-r1-fixes/pytest -k spec_limit` passes.
- [ ] **Step 4: No commit.** Append the touched files to `tmp/judge-r1-fixes/touched.txt`.

---

### Task 4: Eval input file and coverage of every route (D7-1, referee D7)

**Files:**
- Create: `$SK/evals/files/Foo-9B-Q4_K_M.gguf`
- Modify: `$SK/evals/evals.json`
- Test: `tests/skills/onboard-local-model/test_onboard_local_model_scripts.py` (new test after `test_resolve_target_rejects_anything_else`)

**Interfaces:**
- Consumes: Task 2's wording (Produces list); the module's `SKILL`, `GGUF_BYTES`, `_rt_json`.
- Produces: `evals.json` entries may carry `"files": [<path relative to the skill root>]`, and each listed path appears in that eval's prompt.

- [ ] **Step 1: Write the failing test**
  ```python
  def test_eval_input_files_exist_and_the_gguf_fixture_resolves(tmp_path: Path) -> None:
      evals = json.loads((SKILL / "evals" / "evals.json").read_text())["evals"]
      listed = {(e["id"], f) for e in evals for f in e.get("files", [])}
      assert ("named-gguf-argument", "evals/files/Foo-9B-Q4_K_M.gguf") in listed
      for eval_id, name in listed:
          assert (SKILL / name).is_file(), name
          assert name in next(e["prompt"] for e in evals if e["id"] == eval_id), eval_id
      fixture = SKILL / "evals" / "files" / "Foo-9B-Q4_K_M.gguf"
      assert fixture.read_bytes() == GGUF_BYTES
      out = _rt_json(tmp_path, fixture)
      assert (out["kind"], out["quant"], out["size_bytes"]) == ("gguf_file", "Q4_K_M", len(GGUF_BYTES))
  ```
- [ ] **Step 2: Run** `uv run pytest tests/skills/onboard-local-model -q --basetemp=tmp/judge-r1-fixes/pytest -k eval_input_files`. Expected: FAIL on the first assert (no eval lists a file).
- [ ] **Step 3: Create the fixture:** `mkdir -p $SK/evals/files && { printf 'GGUF'; head -c 60 /dev/zero; } > $SK/evals/files/Foo-9B-Q4_K_M.gguf` (64 bytes).
- [ ] **Step 4: Edit `evals.json`.**
  - `named-gguf-argument`: prompt starts `onboard-local-model .claude/skills/onboard-local-model/evals/files/Foo-9B-Q4_K_M.gguf.` (the DRY RUN rest unchanged); add `"files": ["evals/files/Foo-9B-Q4_K_M.gguf"]`; expected_output: "The argument is classified as a local GGUF with resolve-target.sh; a fit check runs on a redacted scratch config whose only enabled entry is `candidate`; budget's error for the header-only fixture is reported, not replaced by an estimate; copying into ~/llm-workspace/models is a G1 gate before any benchmark." Assertions, in order:
    1. "The answer classifies the argument as a local GGUF file (kind gguf_file, quant Q4_K_M) with resolve-target.sh before any other step"
    2. "The fit check runs on the --scratch-config copy under ./tmp/ with entry `candidate` as the only enabled model, not on the live models.yml"
    3. "validate-gguf and budget run with --models-dir = the fixture's own directory, and no file is downloaded"
    4. "budget's error for the header-only fixture (no model metadata such as general.architecture) is reported as the result; no fit verdict or VRAM figure is estimated for it"
    5. "Copying the file into ~/llm-workspace/models (the default; a tmp/model-scout copy only if the owner wants the models directory untouched) is a G1 gate stating size, free disk and destination, and comes before bench-fit.sh or any serve step; the original directory is never mounted and the original file is never moved or deleted"
    6. "No container was stopped, no configuration was edited and no benchmark was started"
  - `no-argument-research`: expected_output "Scouts Hugging Face with one saved --json query per default term, gates and ranks the rows with rank-candidates.sh (up to 6), dispatches one research subagent per row in parallel, takes advisory jev answers and re-checks disagreements, and passes at most three to step 4."; assertion 1 "The scout saves hf-scout.sh --query <term> --json output per term (coder, agent and the baseline's family name by default) and gates and ranks the files with rank-candidates.sh --scout <f1> --scout <f2> ..."; assertion 2 "One research subagent per ranked row (up to 6, the rank-candidates.sh default) is dispatched in parallel, each briefed with selection-criteria.md section 7 and writing one evidence card under tmp/model-scout/<run>/evidence/". The others stay.
  - Append four evals (`expected_output` = one sentence summarizing the assertions, in the style of the existing ones):
    - `served-alias-argument`, prompt "onboard-local-model ornith-15-9b. Premise: it is the enabled model in the live models.yml and llm-server is RUNNING and serving it. DRY RUN for evaluation: read-only commands only (resolve-target.sh, preflight.sh, cat, yq); do not stop or start containers, edit models.yml or start benchmarks. Give me the ordered plan with its gates. Answer in English." Assertions: "The answer classifies the argument with resolve-target.sh as kind alias before any other step"; "The fit check runs on the --scratch-config copy under ./tmp/, not on the live models.yml"; "The plan asks gate G2 and frees the GPU with make stop-llama and wait-gpu-release.sh before bench-fit.sh, because bench-fit.sh exits 3 while llm-server runs; it never uses make stop, make restart or a restart of omniroute"; "Throughput is measured with bench-fit.sh at context depth (32768 and 131072 among the depths), not only at depth 0"; "G3 edits nothing for the served alias: step 7 serves it again with make start-llama, without make llama-enable, make setup or make benchmark"; "The plan ends with a verdict, says G5 has nothing to revert, and checks that nothing it started remains (podman ps -a, PIDs)"; "No container was stopped or started, no configuration was edited and no benchmark was started".
    - `hf-repo-argument`, prompt "onboard-local-model https://huggingface.co/unsloth/Qwen3.8-9B-Instruct-GGUF DRY RUN for evaluation: read-only commands only (resolve-target.sh, hf-scout.sh, curl GET); do not download weights, stop containers, edit models.yml or start benchmarks. Answer in English." Assertions: "The answer classifies the URL with resolve-target.sh as kind hf_repo before any other step"; "The repo is screened with hf-scout.sh --repo unsloth/Qwen3.8-9B-Instruct-GGUF --json, not a general --query scout, and the pick comes from that repo's files"; "Exactly one research subagent is briefed with selection-criteria.md sections 1-5 and 7"; "A failed hard gate (fit, at least 3.5 bpw, at least 131072 context) is stated as a fact for G1 instead of dropping the model, and the download is a G1 question stating file size and free disk"; "The scratch config for the fit check comes from resolve-target.sh --scratch-config (API key redacted), never from copying the live models.yml into tmp/"; "No weight was downloaded, no container was stopped and no configuration was edited".
    - `quant-head-to-head`, prompt "compare Q4_K_M and Q5_K_M of the local model head to head and tell me which one to keep. DRY RUN for evaluation: read-only commands only (cat, yq, jq, the skill scripts' --help); do not download, stop or start containers, edit models.yml or start benchmark or agent runs. Give me the ordered plan with its gates and how long it would take. Answer in English." Assertions: "The plan follows tuning.md Quant headroom: quant-headroom.sh check --alias <a> first, and only a candidate with fits true goes on (fits false or null is not fetched)"; "Separate gates: G-Q1 before quant-headroom.sh fetch (file, size, free disk) and G-Q2 before quant-headroom.sh add-entry (backup path)"; "Both quants run effort-run.sh on one generated seed set: the first lane writes seeds.json and the second passes --seeds with the same --efforts and --timeout"; "The lanes are compared with compare-runs.sh over the two results.jsonl files, and its exit 3 (different challenge_set) is treated as not a head-to-head instead of comparing numbers"; "The upper bound is stated before dispatch as 5 x 3 x 2 x 300 s (layers x challenges per layer x tiers x timeout) per lane"; "Switching the default quant is gate G-Q3, the owner's call; with permissions bypassed the current quant is kept and the comparison table announced"; "The plan restores the original alias at the end (make stop-llama, make llama-enable MODEL=<a>, make start-llama) and checks that nothing it started remains"; "Nothing was downloaded, no container was stopped or started and no configuration was edited".
    - `generate-one-challenge`, prompt "generate one layer-2 ladder challenge with seed 11 the way the effort ladder would, show me its prompt and prove it is intact. Do not run any agent, effort run or benchmark. Answer in English." Assertions: "The challenge is made with gen-challenge.sh --layer 2 --seed 11 --out DIR, DIR a new or empty directory under ./tmp/"; "--with-reference is not passed, so no reference/ directory is written next to the challenge"; "The answer shows DIR/challenge/prompt.md, and DIR/challenge is a git repository with exactly one commit"; "Integrity is shown with gen-challenge.sh --verify DIR, which prints verified true with no changed files"; "Nothing is written outside ./tmp/, nothing runs in or against the owner's project, and DIR is removed at the end unless the user asks to keep it"; "No agent, effort-run.sh or bench-fit.sh run was started".
- [ ] **Step 5: Run** the Step 2 command. Expected: PASS. Then `jq '.evals | length' $SK/evals/evals.json` prints 13 and `jq -r '.evals[].id' $SK/evals/evals.json | sort | uniq -d` prints nothing.
- [ ] **Step 6:** `make validate && PYTEST_ADDOPTS="--basetemp=tmp/judge-r1-fixes/pytest" make test`. Expected: `All checks passed.`, all tests pass.
- [ ] **Step 7: No commit.** Append the touched files to `tmp/judge-r1-fixes/touched.txt`.

---

### Task 5: Final verification and hand-off (last)

**Files:**
- Modify: `docs/superpowers/plans/README.md` (this plan's row, left uncommitted)

- [ ] **Step 1:** `make validate`. Expected: `All checks passed.`
- [ ] **Step 2:** `PYTEST_ADDOPTS="--basetemp=tmp/judge-r1-fixes/pytest" make test`. Expected: all pass. Then `uv run pytest tests/skills -q --basetemp=tmp/judge-r1-fixes/pytest | tail -1` shows one more passed test than `tmp/judge-r1-fixes/skill-tests-before.txt`.
- [ ] **Step 3:** `shellcheck -x -s bash $SK/scripts/*.sh`. Expected: no output (no script changed).
- [ ] **Step 4: Numbers.** `wc -lc $SK/SKILL.md`: at most 160 lines and 14500 bytes (expect about 148 and 14158); description 879 characters (Task 3 Step 3 command); trigger set 20/10; 13 evals.
- [ ] **Step 5: Leftovers.** `ls tmp/ | grep skill-test-` prints nothing; `ps -eo pid,args | awk '/onboard-local-model\/scripts\/|node [-]-test/'` shows nothing this work started (anything else belongs to another session: leave it); `podman ps -a` lists nothing new. Then `rm -rf tmp/judge-r1-fixes` (copy `touched.txt` into the report first).
- [ ] **Step 6: Git.** `git status --short` shows no new tracked change beyond this plan's README row (plus edits by parallel work). Report the untracked files changed or created under `$SK` and `tests/skills/onboard-local-model/` (from `touched.txt`) for the owner's decision on committing the skill tree.
- [ ] **Step 7:** In `docs/superpowers/plans/README.md` set this plan's row to **DONE**, noting "skill edits uncommitted with the untracked skill tree; judges re-run by the orchestrator". Leave it uncommitted.
- [ ] **Step 8: Hand-off.** The orchestrator, not this plan, re-runs both judges (native and referee-two, rubric v4) on the changed skill and applies the pass bar (no critical or high finding, >= 90 % from both).

No commit.

---

## Findings rejected

| Source | Claim | Evidence | Outcome |
|---|---|---|---|
| native D8-1 (alternative fix) | for the served alias, skip step 6 and use the recorded `benchmark.vulkan` plus `latency-profile.sh` | `make benchmark` has no depth sweep (`bench-fit.sh:6-8`), so `benchmark.vulkan` is not a depth number; `latency-profile.sh` measures HTTP requests through the server, not llama-bench, so step 9 would compare different quantities | Rejected; G2 + `bench-fit.sh` kept (Decision 1) |
| native D2-1 (first fix) | inline each step's gate command and pass condition in `SKILL.md` | re-duplicates `onboard-flow.md`, which the audit-fix-wave plan moved out on purpose, and breaks the 14,500-byte target | Rejected; its second fix taken (Task 1 required-reading line) |
| referee D5-1 | `scoring.md:5` restates the isolation rule; add "Do NOT load" inside each optional reference | the line is a one-line pointer, not a restatement; a line inside a file is read only after the file is loaded | Rejected as worded; one do-NOT-load line in `SKILL.md` instead (Decision 6) |
| referee D7-1 (part) | add a scratch `models.yml` fixture and point the dry-run prompts at it | the evals are host-bound dry runs by design (`latest-fit` asserts the model is read from `~/.config/llm-env/models.yml`); using a fixture config would need `LLM_ENV_CONFIG` in a user prompt; `resolve-target.sh --scratch-config` already redacts the live file | Rejected; GGUF fixture added (Task 4) |
| referee D7-2 (part) | mark the held-out trigger queries | `run_loop.py:24-38` splits by itself (Fact 14); a mark is not read | Rejected; trim to 10/10 kept |
| referee D7-2 | 18 should-trigger, 12 should-not | 17 and 12 (Fact 5) | Count corrected; finding kept |
| referee D4-1 (boundary) | "Not for cloud providers" | contradicts the should-trigger anchor query (native D4-1) | Replaced by "picking a cloud provider" (Decision 8) |

Accepted narrowly: referee D5-2 (style per the summary) as one phrase in `challenges.md`; native D8-4 folded into the copy default (Decision 2).

## Self-Review

- Coverage: D8-1 alias route, onboard-flow step 7 (Task 2); D5-1 triggers and read-only line (Task 1); D7-1 fixture, `files`, alias/HF/GGUF/none routes, head-to-head and generated challenge (Task 4); D4-1 description (Task 3); lows: tuning gate wording, redacted scratch config, research count, scout chain and default terms, copy default (Task 2), trigger trim (Task 3), Progress/Helpers and ladder restatements (Task 1), D2-1 (Task 1); own finding Fact 11 (Task 2); final verification last (Task 5); judge re-run left to the orchestrator (Task 5 Step 8).
- Expected lift: native D8 +2, D5 +2, D4 +1, D7 +1, D1 +1, D6 +1, D2 +1 (estimate, not a promise); the referee loses nothing it scored (no removed eval, NEVER item or step).
- Names across tasks: `scratch.yml`, `$S`, `scout-<term>.json`, `Foo-9B-Q4_K_M.gguf`, `GGUF_BYTES`, `_rt_json`, eval ids `served-alias-argument`, `hf-repo-argument`, `quant-head-to-head`, `generate-one-challenge`.
- Not verified when written: the judges' re-scores; PyYAML parsing of the new description (yq parsed it); that `unsloth/Qwen3.8-9B-Instruct-GGUF` exists on Hugging Face (the eval's assertions hold either way); the exact line and byte count after the implementer's wrapping (measured 148 lines, 14,158 bytes on a scratch copy).
