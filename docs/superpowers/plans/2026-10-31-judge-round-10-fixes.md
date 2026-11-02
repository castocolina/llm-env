# Judge Round 10 Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the confirmed findings of round 10 of the onboard-local-model skill judges (native 89.6 % B, 6 medium and 6 low; referee 91.2 % A, its high and mediums refuted by the lead, one wording residual and 3 lows) and the structural causes of the native lane's five-round plateau (87.2-89.6 %), so that the next round gives >= 90 % from both lanes with no critical or high finding.

**Architecture:** The native losses are structural, not new defects. `references/onboard-flow.md` runs three routes through one 225-line file whose steps chain 3-5 conditions per line and interleave served-alias and local-GGUF exceptions; "rule 1" names two rules; the flows hold procedure but almost no thinking guidance; four evals read the live host. This plan:
- moves the served alias and the local GGUF into their own reference files, each read top to bottom, and routes them from `SKILL.md`;
- rewrites the onboard Candidate and Steps one condition per line, condition first, with alias items A1-A3;
- opens every flow with 3-4 "Ask yourself first" questions;
- closes two dead ends (an enabled alias while llm-server is stopped; an alias typo two edits away) with red-first tests and evals;
- gives the four live-host evals fixtures, each pinned by a test.

`SKILL.md` stays a router under its 11,000-byte cap: Task 2 frees the bytes that Task 3's route rows spend.

**Tech Stack:** bash, jq, yq; pytest (`tests/skills/onboard-local-model/`); skill-judge rubric v4.

**Spec:** `tmp/judges/r10-native-summary.txt` and `tmp/judges/r10-referee-summary.txt` (round 10 verdicts; the referee file holds the lead's check of its high and mediums). Plan K (`docs/superpowers/plans/2026-10-31-judge-round-9-fixes.md`, executed) is the base state. A read-only route walk on the eval fixtures (2026-10-31) adds S1-S2. The texts in Tasks 2-3 were drafted on scratch copies and checked against the guard tests' rules (one level deep, shell logic, distinct `--out`, section markers, black box); `SKILL.md` measured 10,996 -> 10,955 (Task 2) -> 10,985 bytes (Task 3).

## Global Constraints

- English only. Edit only `.claude/skills/onboard-local-model/**`, `tests/skills/onboard-local-model/**` and this plan's row in `docs/superpowers/plans/README.md` (added with the plan; Task 7 updates its status). The skill tree and `tests/skills` are untracked and stay uncommitted. No tracked code changes, so nothing is committed. The README rows of plans G, H, I, J and K stay as they are.
- `SKILL.md` <= 11,000 bytes (`SKILL_MD_MAX_BYTES`, tested). Never raise the cap. Shorten `SKILL.md` before adding to it; move text into references.
- A reference never names another reference file (`test_references_never_point_at_each_other`). Inside a reference write "the effort-ladder reference", "the Local GGUF reference", "the quant route", never their file names.
- `jev-fast-decision` is a black box, and ai-kit is never named (guard `test_skill_and_touched_docs_treat_jev_as_a_black_box`; it scans `SKILL.md`, every reference, every JSON under `evals/` and every script).
- Prose holds no shell logic: a backtick span holds at most two of `;`, `&&`, `||` (`test_skill_prose_has_no_long_shell_logic`).
- Every script keeps `--help` (exit 0) and its tests. A behaviour change is red-first: write the test, watch it fail, then implement. Doc tests are written first too and fail until the text lands.
- After a `.sh` edit run `make validate`. After a `.py` edit (test files included) run `make validate && make test`. Run focused pytest files one at a time, one file per command.
- Every eval prompt is a user turn followed by one trailing `(Eval setup: ...)` (`test_eval_prompts_read_as_user_turns`).
- Scratch files go under `./tmp/`, and the run that creates them removes them. No containers, no GPU, no network, no downloads, no edits to the live `models.yml` or to any capacity card.

## Findings ledger (verified 2026-10-31 against the files)

| Finding | Verdict | Task |
|---|---|---|
| native D8-1 (medium): an enabled alias while llm-server is stopped is a dead end | confirmed. onboard-flow.md:25 and :179 cover only the alias "llm-server serves now". With `services.llm_server_running` false the alias falls into "Any other alias": step 2 makes it its own baseline, and 5.2 runs a lane "while llm-server still serves the baseline". tuning.md:107 already answers the same case with `make start-llama` | 3 (served-alias R1, R6), 5 (eval) |
| native D8-2 (medium): typos beyond case and separator go to a search | confirmed by a read-only run on `evals/files/models.yml`: `gema4`, `orinth-35b` and `gemma3` exit 3 "search it as a model name"; resolve-target.sh:117 says "shortcut: prefix and case only" | 1 |
| native D8-3 (medium): "rule 1" names two rules | confirmed. onboard-flow.md:47 "Under rule 1" and :87 "rule-1 candidate" mean item 1 of Alias for a new entry. Every other "rule 1" means `SKILL.md`'s Budget: onboard-flow.md:32, :73; selection-criteria.md:9, :17, :22, :42, :66; tuning.md:121; hf-scout.sh:5, :49; the `hf-repo-gateway-only` assertion in evals.json | 3 |
| native D8-4 (medium): Steps too dense | confirmed. H2 (:36-39) carries 5 conditions in 4 lines, G1 (:91-96) 6, step 9 (:169-175) 7. Local GGUF exceptions sit at :52-60, :88, :97, :131-132, :174; served-alias ones at :25 and :177-189 | 3 |
| native D7-1 (medium): four evals read the live host; phrase pins (low) | confirmed: `latest-fit`, `onboard-plan-with-server-running`, `anchor-comparison` and `runtime-image-review` list no files, and their setup allows curl, `pi auth check`, `cursor-agent models` and `podman images`. Phrase pins: taken where this plan rewrites the pinned text (the description words and the rule-2 sentence become route and contract checks); the other doc tests pin contracts (gate names, commands, out dirs) and stay | 2, 5 |
| native D2-1 (medium): thin thinking guidance | confirmed: one expert question (onboard-flow.md:165) against about 300 lines of steps | 3, 4 |
| native D1-1 (low): rule 2 and a NEVER line restate AGENTS.md | NEVER line taken: AGENTS.md "Test Processes and Containers" says it, and every flow ends with a leftover check (onboard 9, served R6, T9, the quant checklist). Rule 2 is shortened by the Task 2 rewrite; the brief's contents stay (AGENTS.md names none) | 2 |
| native D3-1 (low): Gotchas holds three items | taken: the `make benchmark` gotcha, stated once (onboard 7a.5 and tuning T6 point at it; stated-once test) | 2 |
| native D4-1 (low): description lacks re-measure | taken: the judge's replacement text, 938 characters | 2 |
| native D5-1 (low): "Read only your kind's subsection" saves nothing | taken by the split; the sentence goes | 3 |
| native D6-1 (low): step 6 runs two screens against "a single screen" | confirmed; the same defect as the referee's D8-1 residual, so one fix (the lead's wording) | 2 |
| native D8-5 (low): `in_models_dir` is never read; G1 copies a file onto itself | confirmed. Also: it is true for a file in a sub-folder of the models dir, which llama-server cannot find by base name | 1 (script), 3 (local-gguf L1) |
| native D8-6 (low, a): a local `.gguf` quant comparison gets alias `gemma4-v2` and gate G5, the quant route `gemma4-q5_k_m` and G-Q3 | confirmed. Alias aligned (A2). Gate kept, with the reason stated: step 8 applies the same three conditions as G-Q3, and a yes leaves the old alias disabled as G-Q3's does | 3 |
| native D8-6 (low, b): a declined G3 in the re-measure restarts llm-server | confirmed: SKILL.md:81-82 sends a no to G3 to onboard step 9, whose last action serves llm-server again | 3 (R5) |
| native D8-6 (low, c): P0 compares `pick.bytes` after `fit: unknown` | confirmed: tuning.md:117-129 has no branch for it (the judge's fixture run gave `pick: null`) | 6 |
| native walk note (low; not in its findings list): the owner's "yes, I meant <alias>" path is written only in selection-criteria.md:8 | dropped: the exit-2 message itself asks "did you mean alias <x>?", a yes names an alias that `resolve-target.sh` routes like any other, and `SKILL.md` has no bytes for it | - |
| referee D8-1 (high): "a single `bench-fit.sh` screen" is unclear | refuted as high by the lead (either reading is safe); the ambiguity is real, so SKILL.md:50-51 takes the lead's wording | 2 |
| referee D6-1 (medium): the same sentence as a menu | refuted: the same defect deducted twice, and it is one default with one escape hatch. Covered by the Task 2 rewording | - |
| referee D7-1 (medium): setup text in eval prompts | refuted as medium (no expected figure in any prompt; the head is user text, pinned by a test). Residual low (23 of 27 prompts end with an `(Eval setup: ...)` tail) dropped: the evals.json schema has no setup field (evaluating-skills.md:13-38) and no harness exists here, so the premises ride in the prompt; new and rewritten evals keep the pinned form | - |
| referee D5-1 (low): challenges.md:8 names `gen.mjs` | taken | 6 |
| referee D1-1 (low): the `--no-recreate` flag name | taken (Gotchas rewrite) | 2 |
| S1 (walk): a local `.gguf` whose base name is already in the models dir | new. Local GGUF G1 "copies the file into `~/llm-workspace/models`" even over an existing file of that name (a model file: user data). A path to the enabled entry's own file reaches A1, which makes the candidate its own baseline (the D8-1 dead end again) | 3 (local-gguf L1) |
| S2 (walk): G1 on a file already present | new. Step 4 skips G1 "for an alias whose file is present", so no size check runs, and "A mismatch: delete that file" also covers a file G1 did not download | 3 (step 4.3) |

## Owner decisions (defaults chosen)

1. **Near-miss alias.** A bare name also exits 2 when the Levenshtein distance between its lowercased alphanumerics and an alias's is <= 2, on top of today's equal-or-prefix match. Same message, same exit code, up to 3 aliases in C sort order. A real model name two edits from an alias (`gemma3` against `gemma4`) now gets the question too; the owner's "no, search it" still reaches Find the repo. No minimum length: a 1-3 character name is not a real model name.
2. **`in_models_dir`** is true only for a file directly in the models directory, where llama-server finds `/models/<file>`.
3. **New references:** `references/served-alias.md` and `references/local-gguf.md`. The route table gets a row for the enabled alias ahead of "any other models.yml alias"; the `.gguf` row names `local-gguf.md`, then `onboard-flow.md` Steps.
4. **Stopped enabled alias:** `make start-llama` (`make start` when the stack is down), announced, no new gate: it restores the configured state, as tuning.md:107 does. R6 hands llm-server back stopped. A budget failure of `make start-llama` is the result.
5. **A no to G3 in the re-measure** leaves llm-server stopped and says how to serve it again.
6. **Alias items:** A1 (same file keeps its alias), A2 (the name; another quant of a live entry takes `<that alias>-<quant lowercase>`, the quant route's name), A3 (the `-2` suffix). "rule 1" means `SKILL.md`'s Budget only.
7. **A local GGUF that is another quant of the enabled model** keeps gate G5 (reason stated in local-gguf L6).
8. **G1 never deletes or overwrites a file it did not download.** A file already present still gets the size check (and the sha256 check when the row has one).
9. **Rule 2:** the lead's wording. `latency-profile.sh` stays delegated.
10. **Eval fixtures:** `latest-fit` reuses the scout files of `no-argument-ranking` as outputs that "already printed"; `onboard-plan-with-server-running` gets its own `hf-qwen/` directory; the anchor and runtime evals read saved command transcripts. The ids stay.
11. **Eval setup tails stay** (ledger, referee D7-1).
12. **Description:** the native judge's replacement text.

## Review Focus

1. A real model name within two edits of an alias (`gemma3` against `gemma4`) now exits 2 instead of searching. Expected: the message still offers the search ("if not, the owner can ask for a Hugging Face search by name"), and "no, search it" reaches Find the repo. Pinned in Task 1 (`("gemma3", 2, "gemma4")`; the existing message check).
2. A `.gguf` in a sub-folder of the models dir. Expected: `in_models_dir` false, so local-gguf L1 does not skip the copy for a file llama-server cannot find. Pinned in Task 1 (the nested-file assertion).
3. An entry left enabled on a gateway-only host. Expected: not a re-measure; served-alias R1 sends it to the other-alias row with no baseline. Pinned in Task 3 (`test_served_alias_reference_covers_a_stopped_server_and_a_declined_g3` checks the R1 line).
4. A stopped llm-server whose `make start-llama` fails its budget check (System One grew). Expected: that output is the result, the run stops, `vram_budget` is never loosened. Pinned in Task 3 (same test, "fails its budget check").
5. A local GGUF named like the file the models dir already holds. Expected: a different file is never copied over; the same file of a live entry routes to that alias's row. Pinned in Task 3 (`test_local_gguf_reference_owns_the_name_check_copy_and_live_entry`).

---

### Task 1: `resolve-target.sh` names an alias two edits away, and `in_models_dir` means "llama-server finds it"

**Files:**
- Modify: `.claude/skills/onboard-local-model/scripts/resolve-target.sh:10-15` (header), `:69` (inside), `:116-123` (near-miss jq)
- Modify: `.claude/skills/onboard-local-model/evals/evals.json` (append `alias-edit-typo`)
- Test: `tests/skills/onboard-local-model/test_onboard_local_model_scripts.py` (`test_resolve_target_checks_a_local_gguf_file` :1686, `test_resolve_target_names_a_near_miss_alias_instead_of_a_search` :1820; a new test after `test_eval_alias_typo_fixture_matches_its_assertions` :1854)

**Interfaces:**
- Produces: exit 2, empty stdout, stderr `<MODEL>: no models.yml alias of that name; did you mean alias <names>? (if not, the owner can ask for a Hugging Face search by name)` (unchanged text) for a name equal to, starting (3+ characters) or within 2 edits of an alias after `norm`.
- Produces: `in_models_dir` true only when the file's resolved directory equals the resolved models dir. Task 3's local-gguf L1 reads it.
- No caller text changes: `SKILL.md`'s exit-2 bullet ("a near-miss alias") and selection-criteria.md:8 already quote the message. `rg -n "near-miss|did you mean alias" .claude/skills/onboard-local-model` must show no other wording.

- [ ] **Step 1: Write the failing tests**

The parametrize list of `test_resolve_target_names_a_near_miss_alias_instead_of_a_search` becomes:

```python
[("Gemma4", 2, "gemma4"), ("gemma", 2, "gemma4"), ("GEMMA-4", 2, "gemma4"), ("Ornith-35B", 2, "ornith-35b"),
 ("ornith-3", 2, "ornith or ornith-35b"), ("orn", 2, "ornith or ornith-35b"), ("gema4", 2, "gemma4"), ("orinth-35b", 2, "ornith-35b"),
 ("gemma3", 2, "gemma4"), ("ornth", 2, "ornith"), ("gemma-4-27b-it", 3, None), ("Qwen3.8-27B", 3, None), ("Zeta-Coder-7B", 3, None)]
```

At the end of `test_resolve_target_checks_a_local_gguf_file`:

```python
    nested = tmp_path / "models" / "sub" / "y-Q4_K_M.gguf"
    nested.parent.mkdir()
    nested.write_bytes(GGUF_BYTES)
    assert _rt_json(tmp_path, nested)["in_models_dir"] is False  # llama-server finds a model only at <models_dir>/<file>
```

After `test_eval_alias_typo_fixture_matches_its_assertions`:

```python
def test_eval_alias_edit_typo_fixture_matches_its_assertions() -> None:
    result = _run("bash", SCRIPTS / "resolve-target.sh", "orinth-35b", env={**os.environ, **FIX_ENV}, cwd=ROOT)
    want = "no models.yml alias of that name; did you mean alias ornith-35b?"
    assert (result.returncode, result.stdout) == (2, "") and want in result.stderr
    assert all(s in _asserts("alias-edit-typo") for s in ("exits 2", want, "hf-scout.sh --query is not run"))
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/skills/onboard-local-model/test_onboard_local_model_scripts.py -q -k "near_miss or local_gguf_file or edit_typo"`
Expected: FAIL. `gema4`, `orinth-35b`, `gemma3` and `ornth` exit 3, `ornith-3` names only `ornith-35b` (now also `ornith`, one edit away), the nested file gives `in_models_dir` true, and the eval is missing. (These expectations were checked on 2026-10-31 by running the planned jq on the fixture aliases.)

- [ ] **Step 3: Implement in `resolve-target.sh`**

- :69 becomes `case "$(dirname "$abs")" in "$(readlink -f "$MODELS_DIR")") inside=true ;; *) inside=false ;; esac` (a `case`, not `A && B || C`, for shellcheck SC2015).
- :117 (the `shortcut:` comment) is deleted. In the jq program, add this definition after `def norm`, and extend the `select` with `or lev(norm; $n) <= 2` inside the `($n | length) > 0 and (...)` group:

```jq
def lev($a; $b): ($a | explode) as $s | ($b | explode) as $t
  | reduce range(0; $s | length) as $i ([range(0; ($t | length) + 1)];
      . as $p | reduce range(0; $t | length) as $j ([$i + 1];
        . + [[$p[$j + 1] + 1, .[$j] + 1, $p[$j] + (if $s[$i] == $t[$j] then 0 else 1 end)] | min]))
  | .[-1];
```

  (Checked with jq on 2026-10-31: `gema4`/`gemma4` 1, `orinth35b`/`ornith35b` 2, `gemma427bit`/`gemma4` 5, `qwen3827b`/`ornith35b` 8.)
- Header (5b): "a bare name that matches an alias case- and separator-insensitively, or starts one (3+ characters)" becomes "a bare name that matches an alias case- and separator-insensitively, starts one (3+ characters) or is within 2 edits of one (Levenshtein on the lowercased letters and digits)". Header output line: "in_models_dir" becomes "in_models_dir (true only for a file directly in the models dir)".

- [ ] **Step 4: Append the eval**

```json
{
  "id": "alias-edit-typo",
  "prompt": "swap my local model to orinth-35b and tell me if it beats gemma4. Just the plan; change nothing. (Eval setup: the config is .claude/skills/onboard-local-model/evals/files/models.yml and its models directory .claude/skills/onboard-local-model/evals/files/models/; pass them as LLM_ENV_CONFIG and LLM_ENV_MODELS_DIR.)",
  "files": ["evals/files/models.yml"],
  "expected_output": "resolve-target.sh orinth-35b exits 2 and names the alias ornith-35b (two edits away); the answer asks whether ornith-35b was meant and stops, with no Hugging Face search.",
  "assertions": [
    "resolve-target.sh orinth-35b exits 2 with no models.yml alias of that name; did you mean alias ornith-35b?",
    "Exit 2 is treated as an error: the answer asks whether ornith-35b was meant and stops; hf-scout.sh --query is not run",
    "No scratch config, download, gate, container or configuration change was made"
  ]
}
```

- [ ] **Step 5: Run the tests and validate (no commit)**

Run: `uv run pytest tests/skills/onboard-local-model/test_onboard_local_model_scripts.py -q -k "resolve_target or eval_"`, then `make validate && make test`.
Expected: PASS and green. `test_resolve_target_exits_3_only_for_a_bare_model_name`, `test_resolve_target_rejects_anything_else`, `test_eval_bare_name_fixture_matches_its_assertions` and `test_eval_alias_fixtures_match_their_assertions` still pass (their names are 5+ edits from every alias).

### Task 2: `SKILL.md` router edits that free bytes (rule 2, Gotchas, description)

**Files:**
- Modify: `.claude/skills/onboard-local-model/SKILL.md:3` (description), `:49-52` (rule 2), `:60-61` (profile check), `:106` (NEVER line), `:115-116` (Gotchas)
- Modify: `.claude/skills/onboard-local-model/references/onboard-flow.md:138` (7a.5), `references/tuning.md:21-23` (T6)
- Test: `tests/skills/onboard-local-model/test_onboard_local_model_scripts.py` (`STATED_ONCE` :970; `test_skill_md_routes_near_misses_allows_one_inline_screen_and_names_the_global_kv_key` :995-1002 replaced)

**Interfaces:**
- Produces: `SKILL.md` at 10,955 bytes (measured on a scratch copy), 45 under the cap; Task 3's route rows take 30 of them. Also the `make benchmark` gotcha that onboard 7a.5 and tuning T6 point at.

- [ ] **Step 1: Write the failing tests**

`STATED_ONCE` gains `"rewrites `gpu.backend`": "SKILL.md"`. Replace the test at :995-1002 with:

```python
DESCRIPTION_ROUTES = ("models.yml alias", "Hugging Face", ".gguf", "re-measure", "got slower", "quants", "capacity card", "Haiku",
                      "llama.cpp image", "Laya", "ladder challenge", "Not for OmniRoute")


def test_skill_md_routes_near_misses_allows_inline_bench_fit_and_names_the_global_kv_key() -> None:
    text = _md(SKILL_MD)
    assert "a near-miss alias" in text and '"no, search it" after a near-miss' in text
    rule2 = _section(text, "2. **Benchmark runs.**", "3. **Like for like.**")
    assert "Each `bench-fit.sh` run may run inline (bounded by `--timeout`, default 3600 s" in rule2 and "single" not in rule2
    assert all(s in rule2 for s in ("`tune-sweep.sh`, `effort-run.sh` and `latency-profile.sh` run goes to one subagent", "own new `--out`"))
    gotchas = text.split("## Gotchas", 1)[1]
    assert "`runtime.cache_type_k/v`" in gotchas and "Never run `make benchmark`" in gotchas and "--no-recreate" not in text
    assert "NEVER leave a bench container" not in text
    assert "overrides another row's Do NOT load cell" not in text and "also serves authoring" not in text
    meta = yaml.safe_load(SKILL_MD.read_text().split("---")[1])
    assert not [w for w in DESCRIPTION_ROUTES if w not in meta["description"]]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/skills/onboard-local-model/test_onboard_local_model_scripts.py -q -k "inline_bench_fit or stated_once"`
Expected: FAIL (old rule 2, no gotcha, no "re-measure"; "rewrites `gpu.backend`" sits in onboard-flow.md and tuning.md).

- [ ] **Step 3: Edit `SKILL.md`**

- Description (:3), the whole value becomes:

```text
Find, fit, benchmark and onboard a local agentic-coding LLM (GGUF) for this repo's llama.cpp router beside System One (Laya). Use when the user names a models.yml alias, a Hugging Face repo or URL, a .gguf file or a model name to download, add, try, swap or re-measure; says the local model got slower; asks which local model fits the VRAM or beats the current one; compares quants (Q4_K_M/Q5_K_M/Q6_K); wants pp/tg tok/s at depth, ctx_size, KV or n_cpu_moe tuned, the capacity card or my-coding-lite; scores the local model against Haiku, Luna or Composer; asks if the llama.cpp image (Vulkan/ROCm) or Laya is outdated; or wants one ladder challenge generated or verified, even as "a faster model for pi", "what LLM can I run locally" or "modelo agéntico que quepa en mi GPU". Not for OmniRoute or other combos, container memory limits, gpu-status triage, asking Laya for a judgment, cloud providers, sampling, fine-tuning or quantizing.
```

- Rule 2 (:49-52), its first two sentences become:

```text
2. **Benchmark runs.** Each `bench-fit.sh` run may run inline (bounded by `--timeout`, default 3600 s; container removed on
   exit); every `tune-sweep.sh`, `effort-run.sh` and `latency-profile.sh` run goes to one subagent with a written brief
   (command, out dir, stop, cleanup, what to return), monitored per AGENTS.md. Each of these runs writes to its own new `--out`
   under `tmp/` and runs alone on the GPU.
```

  The isolation sentence and the `leak_suspect` sentence after it stay unchanged.
- Rule 3 (:60): "under `router-env`; when it does not, run" becomes "under `router-env`, else run".
- NEVER (:106): delete "- NEVER leave a bench container, sampler or agent running: it holds the GPU and falsifies the next measurement."
- Gotchas (:115-116), the first item becomes two:

```text
- `make start-llama` never recreates the container: a pulled image is served only after `make restart`.
- Never run `make benchmark`: a Vulkan failure rewrites `gpu.backend` to `cpu` in the live models.yml.
```

- [ ] **Step 4: Point the two restatements at the gotcha**

- onboard-flow.md 7a.5 (:138) becomes "5. No `make benchmark` (`SKILL.md` Gotchas): step 6 measured this model at depth."
- tuning.md T6 (:21-23) becomes "- T6. `make start-llama`, then `make check-server`. No `make benchmark` (`SKILL.md` Gotchas): the verify run already measured this configuration with llm-server stopped."

- [ ] **Step 5: Measure, run and validate (no commit)**

Run: `wc -c .claude/skills/onboard-local-model/SKILL.md` (expected 10,955, at most 11,000), then `uv run pytest tests/skills/onboard-local-model/test_onboard_local_model_scripts.py -q -k "skill_md or stated_once or description or bypassed or isolated or black_box"`, then `make validate && make test`.
Expected: PASS and green (the `tune-capacity-card` assertion about `gpu.backend` still has its source: the gotcha).

### Task 3: Split the served alias and the local GGUF out; rewrite the onboard flow one condition per line

**Files:**
- Create: `.claude/skills/onboard-local-model/references/served-alias.md`, `.claude/skills/onboard-local-model/references/local-gguf.md`
- Modify: `.claude/skills/onboard-local-model/references/onboard-flow.md` (everything above `## Entry template` is replaced; `## Entry template` and `## Where things live` stay as they are)
- Modify: `.claude/skills/onboard-local-model/SKILL.md:16` (Route intro), `:20-22` and `:24` (rows), `:81-82` (rollback sentence)
- Test: `tests/skills/onboard-local-model/test_onboard_local_model_scripts.py` (`REF_NAMES` :957, `RETIRED_COPIES` :973, `test_route_table_names_existing_sections_and_steps` :1013, `test_onboard_step_4_selects_the_candidate_before_validating_it` :1035, `test_onboard_step_4_writes_the_settings_into_the_scratch_candidate` :1049, `test_served_alias_has_its_own_list_and_routes_have_progress_checklists` :1053 replaced; three new tests after it)

**Interfaces:**
- Consumes: Task 1's `in_models_dir` meaning; Task 2's byte headroom.
- Produces: `references/served-alias.md` with `R1.`-`R6.` and the outs `tmp/model-scout/<run>/effort-served`, `tmp/model-scout/<run>/bench-served`; `references/local-gguf.md` with `L1.`-`L7.`; onboard-flow.md with alias items `A1.`-`A3.`, a Steps "Names" line and "A local GGUF also follows its own reference at G1, 7a.2 and 9". Each file opens with an "Ask yourself first:" block (Task 4's test counts them). Task 5's `enabled-alias-stopped` eval quotes served-alias R1-R6.

- [ ] **Step 1: Write the failing tests**

- `REF_NAMES = ("anchors", "challenges", "local-gguf", "onboard-flow", "runtimes", "scoring", "selection-criteria", "served-alias", "system-one", "tuning")`.
- `RETIRED_COPIES` gains `"rule 1 (a live entry"`, `"rule-1 candidate"` and `"Read only your kind's subsection"`.
- `test_route_table_names_existing_sections_and_steps`: the comment after `len(pairs) >= 6` reads `# Alias, Hugging Face, Steps, Find the repo, Scout and rank, Standalone quant comparison`; the step loop and the last assert become:

```python
    flow = (REFS / "onboard-flow.md").read_text()
    for step in sorted(set(re.findall(r"at step (\d)", text))):
        assert re.search(rf"^{step}\. \*\*", flow, re.MULTILINE), step
    served = (REFS / "served-alias.md").read_text()
    for step in sorted(set(re.findall(r"at R(\d)", text))):
        assert re.search(rf"^R{step}\. \*\*", served, re.MULTILINE), step
    assert re.search(r"^### Scout pick", flow, re.MULTILINE) and "| `references/challenges.md` |" in rows
    assert "| `references/served-alias.md` |" in rows and "`references/local-gguf.md`, then `references/onboard-flow.md`, Steps" in rows
```

- In `test_onboard_step_4_selects_the_candidate_before_validating_it`, the H5 end marker `"### Local GGUF"` becomes `"### Scout pick"`.
- In `test_onboard_step_4_writes_the_settings_into_the_scratch_candidate`, the step-9 end marker `"## Served alias re-measure"` becomes `"## Entry template"`, and add `assert "never delete or overwrite it" in step4`.
- Replace `test_served_alias_has_its_own_list_and_routes_have_progress_checklists` with:

```python
def test_routes_have_progress_checklists() -> None:
    flow = _md(REFS / "onboard-flow.md")
    steps = _section(flow, "## Steps", "## Entry template")
    assert "served alias" not in steps.lower() and "Progress checklist" in steps
    for name in ("served-alias", "local-gguf"):
        assert "Progress checklist" in _md(REFS / f"{name}.md"), name
    alias = re.split(r"^### Alias\n", (REFS / "onboard-flow.md").read_text(), flags=re.MULTILINE)[1].split("### Hugging Face")[0]
    assert "is a re-measure (`SKILL.md` Route)" in _md(alias)
    tuning = _md(REFS / "tuning.md")
    assert "Progress checklist" in _section(tuning, "### Standalone quant comparison", "- Q1.")
    assert "`ps` by PID" in _section(tuning, "- T9.", "## Interference gate")


def test_served_alias_reference_covers_a_stopped_server_and_a_declined_g3() -> None:
    served = _md(REFS / "served-alias.md")
    assert all(f"R{n}. " in served for n in range(1, 7)) and "R7. " not in served
    r1 = _section(served, "R1. ", "R2. ")
    assert all(s in r1 for s in ("`services.llm_server_enabled` false", "`services.llm_server_running` false", "make start-llama",
                                 "then R1 again", "fails its budget check"))
    assert "A no to G3: llm-server stays stopped" in _section(served, "R5. ", "R6. ")
    assert "`make stop-llama` hands it back" in served.split("R6. ", 1)[1] and "G5 has nothing to revert" in served
    assert "--out tmp/model-scout/<run>/effort-served" in served and "--out tmp/model-scout/<run>/bench-served" in served
    flow = _md(REFS / "onboard-flow.md")
    assert "Served alias re-measure" not in flow and "R1. " not in flow


def test_local_gguf_reference_owns_the_name_check_copy_and_live_entry() -> None:
    local = _md(REFS / "local-gguf.md")
    assert all(f"L{n}. " in local for n in range(1, 8)) and "L8. " not in local
    l1 = _section(local, "L1. ", "L2. ")
    assert all(s in l1 for s in ("`in_models_dir` true", "never copy over it", "the owner named that alias"))
    assert "Never move or delete the original" in _section(local, "L4. ", "L5. ")
    l6 = _section(local, "L6. ", "L7. ")
    assert "file://<original path>" in l6 and "G-Q3" in l6
    assert not re.search(r"^### Local GGUF", (REFS / "onboard-flow.md").read_text(), re.MULTILINE)


def test_onboard_flow_names_alias_items_a1_to_a3() -> None:
    raw = (REFS / "onboard-flow.md").read_text()
    new_entry = raw.split("### Alias for a new entry", 1)[1].split("\n### ", 1)[0]
    assert re.findall(r"^- (A\d)\. ", new_entry, re.MULTILINE) == ["A1", "A2", "A3"]
    assert "`<that alias>-<quant lowercase>`" in _md(new_entry)
    flow = _md(raw)
    assert "Under A1" in _section(flow, "H5. ", "### Scout pick") and "An Alias-row or A1 candidate" in flow
    assert "A local GGUF also follows its own reference at G1, 7a.2 and 9" in flow
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/skills/onboard-local-model/test_onboard_local_model_scripts.py -q -k "never_point or route_table or step_4 or checklists or served_alias or local_gguf or a1_to_a3 or stated_once or router"`
Expected: FAIL (two reference files missing, old sections and labels present).

- [ ] **Step 3: Create `references/served-alias.md`**

```text
# Re-measure the served alias

Loaded for the enabled alias (`resolve-target.sh`: `kind alias`, `enabled: true`) when the owner wants it measured again, for
example because it "got slower". Gates, rules 1-3, freeing the GPU, `$SK`, `<run>` and paths are in `SKILL.md`. No scratch
config, no candidate and no verdict: a model cannot beat itself.

Ask yourself first:
- Is the slowdown real? Only a number at the reference depth, on the image that serves now, can show it (rule 3).
- What changed since its capacity card: the llama.cpp image, `ctx_size`, the KV type, or System One's load?
- How long is the local lane offline while I measure, and is a session using it now?
- In which state do I hand llm-server back: serving, or stopped as I found it?

Progress checklist (copy it into your task list): R1 preflight; R2 lane and build; R3 G2; R4 screen; R5 G3, interference, card;
R6 report, leftover check.

R1. **Preflight.** `bash $SK/scripts/preflight.sh`, then the rule 3 profile check.
    - The fit is preflight's `llmenv_budget`. A false `feasible` leads the report.
    - `services.llm_server_enabled` false (a gateway-only host): not a re-measure. The alias is a candidate with no baseline:
      `SKILL.md`'s other-alias row.
    - `services.llm_server_running` false: say so, run `make start-llama` (`make start` when the stack is down), then R1 again.
      Remember that it was stopped, for R6.
    - `make start-llama` fails its budget check: its output is the result. Report it and stop; never loosen `vram_budget`.
R2. **Lane, while it is served** (a benchmark run, rule 2; its upper bound per the effort-ladder reference, Running a lane):
    `PI_CODING_AGENT_DIR=$HOME/.pi/agent bash $SK/scripts/effort-run.sh --model router-env/llama-cpp/<alias> --out tmp/model-scout/<run>/effort-served --efforts low,high --per-layer 3`.
    Then record the build: `podman inspect --format '{{.Image}}' llm-server`.
R3. **Gate G2:** free the GPU (`SKILL.md` Freeing the GPU) with U and H from the last R1 preflight.
    - A no to G2: score R2's lane (R5's `score.sh` and `SCORE` card steps), report it and stop.
R4. **Screen** on the live config (a benchmark run, rule 2; `<ref>` is the reference depth of rule 3):
    `bash $SK/scripts/bench-fit.sh --alias <alias> --out tmp/model-scout/<run>/bench-served --depths 0,32768,<ref>`.
    Show pp/tg per depth, `gpu.model_vram_mib`, peak temperature and average clock.
    - `model_vram_mib` over the budget, or `tg` at `<ref>` under the floor: report it, `make start-llama`, then R6.
R5. **Gate G3** (nothing is edited; it only serves again): `make start-llama`, then `make model-interference`, then
    `make model-capacity MODEL=<alias> DEPTH=<R4 bench json>`, `bash $SK/scripts/score.sh <R2 results.jsonl>` and
    `make model-capacity MODEL=<alias> SCORE=<score.json>`. A failed interference gate is reported, not reverted.
    - A no to G3: llm-server stays stopped. Report that the local lane is offline until `make start-llama`, then R6.
R6. **Report** the numbers and the capacity card, with no verdict; G5 has nothing to revert.
    - llm-server was stopped before R1: `make stop-llama` hands it back as it was found.
    - Last: `podman ps -a` and a `ps` by PID for everything this run started: nothing of yours may remain.
```

- [ ] **Step 4: Create `references/local-gguf.md`**

```text
# Local GGUF candidate

Loaded for a `.gguf` path (`resolve-target.sh`: `kind gguf_file`), before the onboard Steps (`SKILL.md` Route). Gates,
rules 1-3, `$SK`, `<run>` and paths are in `SKILL.md`. This file prepares the candidate and says what changes in Steps at
G1, 7a.2 and 9.

Ask yourself first:
- Is this file what its name claims? Size and quant come from the file and `resolve-target.sh`, not from the name.
- Is it a new model, another quant of one in models.yml, or a file models.yml already serves?
- What may I write? The original stays where the owner put it; only a copy in the models directory is ever served.

Progress checklist (copy it into your task list): L1 name check; L2 scratch; L3 read-only fit; L4 copy (G1); L5 Steps;
L6 live entry (7a.2); L7 report (9).

L1. **Name check** (read-only) of the file's base name in `~/llm-workspace/models`:
    - `in_models_dir` true: it is already there. No copy and no G1 at L4.
    - Another file of that name is there: compare size and `sha256sum`. The same: no copy and no G1. Different: never copy
      over it; report both and stop.
    - A live entry has this `file` (`yq '.models[] | select(.file == "<base name>") | .alias' ~/.config/llm-env/models.yml`)
      and the file is the same: the owner named that alias. Follow `SKILL.md`'s row for it (the enabled alias: the
      re-measure) and stop here.
L2. **Scratch config.** `bash $SK/scripts/resolve-target.sh <file> --scratch-config $S` (`S=tmp/model-scout/<run>/scratch.yml`)
    writes `$S` with the entry `candidate` (`url: file://<original path>`) as the only one enabled. In Steps, `<alias>` is
    `candidate` until 7a.2.
L3. **Read-only fit.** `validate-gguf` and `budget` run with `--models-dir` set to the file's own directory.
L4. **G1 is a copy, not a download**, unless L1 found the file in place.
    - G1 copies it into `~/llm-workspace/models`, the directory 7a serves from, before any benchmark or serve step.
    - The owner wants that directory untouched while screening: copy into `tmp/model-scout/<run>/models` instead
      (`LLM_ENV_MODELS_DIR` for step 6); G3 copies it on, and G3's facts name that second copy.
    - Never move or delete the original.
L5. **Steps 1-9** as written, with L6 at 7a.2 and L7 at 9.
L6. **The live entry (7a.2).** It never keeps the alias `candidate`.
    - `alias`: per Alias for a new entry (A1-A3).
    - `label` and `parameters`: the human name and size (the scratch entry says `unknown`).
    - `quantization`: the file's quant token, else `unknown`.
    - `url`: the pinned `resolve/<sha>/` Hugging Face url when the file came from there, else `file://<original path>`.
      `make setup` fetches only a `file` missing from the models directory, so the url is unused while the copy exists; with
      the copy pruned (`make prune`) and the original gone, setup fails with "download failed".
    - Another quant of the enabled model (A2 names it `<alias>-<quant lowercase>`, as the quant route does): the decision
      stays step 8 and G5. They apply the same three conditions as the quant route's G-Q3 (fit, the replace rule on one seed
      set and build, pp/tg within about 3 %), and a yes leaves the old alias in models.yml, disabled, as G-Q3's yes does.
L7. **Step 9.** Never touch the original. The downloaded-file report covers only the copy G1 made (none when L1 found it in place).
```

- [ ] **Step 5: Replace everything above `## Entry template` in `references/onboard-flow.md`**

Every fact of the old text survives here or in the two new files; nothing below `## Entry template` changes.

```text
# Onboard a named model

Loaded for any alias but the enabled one, a Hugging Face repo or file, a scout pick, and the Steps of a local GGUF (`SKILL.md`
Route). Gates, rules 1-3, freeing the GPU, `$SK`, `<run>` and paths are in `SKILL.md`. Report each step as it ends.

Ask yourself first:
- What must the candidate beat: the enabled model, measured today on the same image, or nothing (no baseline)?
- Will the number I decide on come from the reference depth, one llama.cpp build and a resident System One?
- Which step is the first to change the host, and what must its gate state?
- If I stop halfway, what stays behind: a downloaded file, a stopped llm-server, an edited models.yml?

## Candidate by kind

`S=tmp/model-scout/<run>/scratch.yml` is the scratch config (API key and dashboard password redacted). The live file stays
untouched until G3. `<models_dir>` is `~/llm-workspace/models` (`LLM_ENV_MODELS_DIR`), except where the Local GGUF reference
names the file's own directory.

### Alias for a new entry

Decided at step 3 and used unchanged in `$S`, the lanes and 7a. A1 wins over A2; A3 applies to the A2 name.
- A1. A live entry has the same `file`: keep its alias. One GGUF never gets a second entry.
- A2. Name: the file's base name without `.gguf` and without its quant token (the `quant` of `resolve-target.sh`, or
  `pick.quant`), lowercased, each run of characters outside `[a-z0-9]` turned into `-`, leading and trailing `-` dropped, at
  most 60 characters. When a live entry's file gives the same name (another quant of that model), the alias is
  `<that alias>-<quant lowercase>` instead, the name the quant route gives (`gemma4-q5_k_m`).
- A3. While a live `models[].alias` already has that name for another file, append `-2`, `-3`... A name is never reused for a
  different file.

### Alias

The enabled alias on a host with llm-server enabled is a re-measure (`SKILL.md` Route), not this section. Every other alias
(an entry left enabled on a gateway-only host included):
- `bash $SK/scripts/resolve-target.sh <alias> --scratch-config $S` writes `$S` with that alias alone enabled.
- `validate-gguf` and `budget` take `--config $S`.
- `present: false`: G1 downloads the entry's `url` first.
- Its entry keeps its own settings: step 4 writes none.
- G3 adds no entry: it runs `make llama-enable MODEL=<alias>`.

### Hugging Face

H1. **Scout the one repo:** `bash $SK/scripts/hf-scout.sh --repo <repo> --json --budget-mib <rule 1 budget> --ram-mib <rule 1 RAM>`.
    - An `hf_file` URL adds `--revision <revision> --file <file>` from its JSON.
    - A scout pick adds `--file <pick file>`.
    - The row is read at one commit, `sha` (the revision resolved, `main` by default).
    - Exit 2 (the file is not in that commit, or the repo or revision is unreadable): report and stop.
H2. **Read the row.** `pick` is the named file whatever the scout would choose, else the scout's choice. `sha`, `pick.bytes`
    and `pick.sha256` fill the entry (template below); never re-read `tree/main` for them.
    - `fit: "unknown"` (the tree was unreadable): retry once, else report and stop, because G1 needs the size.
    - A null `sha`: stop before G1.
    - A null `pick.sha256` (a split file; an entry takes one file): stop before G1. Report it with the repo's largest fitting
      single-file quant (H1's call without `--file`, `--revision` kept), or that none exists. Go on with it only when the
      owner asks after that report.
H3. **Fit gates.**
    - `fit: "no"`: stop before G1, under bypass too. Report the fit math and the repo's largest fitting quant (H1's call
      without `--file`, `--revision` kept). Download it only when the owner asks after that report.
    - A false card gate, or a failed `bpw` or `ctx` gate: a G1 fact. Under bypass it proceeds, because the owner named the model.
H4. **Evidence.** One research subagent writes the evidence card (`evidence/1.json`) per the research brief loaded at this step.
H5. **Scratch config.** Name the entry per Alias for a new entry.
    - Under A1 (a live entry has this `file`) add nothing: that alias is the candidate.
    - Build `$S` with `bash $SK/scripts/resolve-target.sh <live alias> --scratch-config $S`. `<live alias>` is the baseline;
      with no baseline (Steps 2), any live alias.
    - Add the entry (template below) to `$S` with `yq -i`, `enabled: false`. Step 4 writes its settings, and its
      `models select` switches `$S` to the new entry.
    - No alias in the live file: stop and report. Never `cp` the live file into `tmp/`.

### Scout pick

As Hugging Face, with the pick's repo and `--file <pick file>`; its evidence card from the ranking replaces the research
subagent of H4.

## Steps

Names: `<alias>` is the candidate's alias in `$S` (`candidate` for a local GGUF until 7a.2), `<entry file>` its `file` (a base
name), `<baseline>` the enabled alias of step 2, `<ref>` the reference depth of rule 3.
A local GGUF also follows its own reference at G1, 7a.2 and 9.

Progress checklist (copy it into your task list; (b) = only with a baseline, Steps 2): 1 preflight; 2 baseline; 3 candidate;
4 settings, G1, select, validate, budget; 5 (b) baseline lane and build, G2; 6 (b) baseline screen, candidate screen; 7a G3
apply; 7b interference and card; 7c ladder, (b) compare; 8 verdict; 9 G5, downloaded-file report, leftover check.

1. **Preflight (read-only).** `bash $SK/scripts/preflight.sh` prints VRAM, budgets, RAM, swap, GPU holders, temperature and
   clocks.
   - Size per rule 1; it also says which RAM figure `hf-scout.sh` takes.
   - Flag `services.llm_server_running` and any non-zero `gpu.holders`.
   - `budget.ram_offload_budget_mib` is the llm-server memory cap from `llmenv budget` (`budget.ram_budget_source` names its
     origin).
   - Run the rule 3 profile check now, for the lane of step 5.
2. **Baseline.** The enabled entry of `~/.config/llm-env/models.yml`: a candidate must beat it, not an abstract bar.
   - Its recorded `benchmark.vulkan` numbers are depth 0 only.
   - Its capacity card's depth numbers (`uv run llmenv.py capacity show --alias <baseline>`) are reported, never compared:
     step 6 benches it beside the candidate on one image, because neither the card nor the bench JSON records the llama.cpp
     build.
   - A gateway-only host (`llm_server.enabled: false`, preflight `services.llm_server_enabled` false, even when
     `make llama-disable` left an entry enabled), or no enabled entry: no baseline. Then there is no baseline lane or bench,
     and step 8 decides without one.
3. **Candidate.** The section above for its kind; a local GGUF: its own reference.
4. **Fit and configure.**
   1. Fit, with the fit math loaded with this route (sections 1-3): weights + KV + buffers against the budget at the live KV
      type (`runtime.cache_type_k/v`, one global key: never changed here). An MoE model: solve `n_cpu_moe` against RAM too.
   2. Settings of a new entry:
      - `ctx_size`: 262144 when its KV fits, else 131072.
      - `client_max_output_tokens`: 8192; 16384 when an earlier lane of this model ended runs with `stopReason: length`.
      - `n_cpu_moe`: from the solve (MoE only).
      - `sampling`: the model card's agentic settings, or none.
      An Alias-row or A1 candidate keeps its own settings: skip this write. Otherwise write them into the candidate entry
      of `$S`:
      `yq -i '(.models[] | select(.alias == "<alias>")) |= (.ctx_size = <ctx> | .client_max_output_tokens = <max_out> | .n_cpu_moe = <n> | .sampling = <sampling>)' $S`,
      with `del(.n_cpu_moe)` for a dense model and `del(.sampling)` when the card gives none. 7a's live entry takes the same
      values.
   3. **Gate G1** fetches exactly one file: the top-ranked fit, or the file the owner named (a second candidate only if the
      user asks).
      - The entry's file is already in `<models_dir>`: no G1 and no download; the checks below still run on it.
      - Download: `curl -fL --continue-at - -o <models_dir>/<entry file> <url>` (`<entry file>` is a base name, never a
        sub-folder path).
      - Check that the byte size equals `size_bytes`.
      - A Hugging Face file: also check that `sha256sum <models_dir>/<entry file>` equals the row's `pick.sha256`.
      - An alias `url`: the size check only (models.yml stores no checksum), and the report says so.
      - A mismatch on a file this G1 downloaded: delete it, report both values, remove the scratch files and stop, as after a
        declined G1 (step 9; no `make stop-llama`).
      - A mismatch on a file that was already there: never delete or overwrite it; report both values and stop.
   4. Select and check on `$S`, in this order:
      - `uv run llmenv.py --config $S models select <alias>` (on the Hugging Face route it switches `$S` from the baseline to
        the new entry);
      - `... --config $S validate-gguf --models-dir <models_dir>`;
      - `... --config $S budget --models-dir <models_dir>` (it exits 1 when infeasible but still prints JSON).
      - `feasible` false: stop and report its `remedies` (what System One could give back: `SKILL.md` System One row). Never
        loosen `vram_budget` to pass.
      - `budget` errors on a wrong `--models-dir` or a missing enabled file.
      - `llm_server.enabled: false` in the config: `budget` reports 0 MiB of RAM for llm-server, so an MoE offload shows
        `ram_feasible: false` until local inference is enabled: a precondition, not a verdict on the model.
      After G3, `make setup` repeats the check on the live file (the authoritative one).
5. **Baseline lane, then free the GPU.**
   1. Decide the lane set first (baseline, candidate, any anchor the owner named), all on the router profile (rule 3).
   2. With a baseline, run its lane while llm-server still serves it (a benchmark run, rule 2; upper bound per the
      effort-ladder reference, Running a lane):
      `PI_CODING_AGENT_DIR=$HOME/.pi/agent bash $SK/scripts/effort-run.sh --model router-env/llama-cpp/<baseline> --out tmp/model-scout/<run>/effort-baseline --efforts low,high --per-layer 3`.
      - It writes the `seeds.json` every later lane takes. Once G2 stops llm-server, this lane would need a swap back under
        its own gate.
      - A recorded baseline lane counts only when its `challenge_set` matches.
   3. When this lane ran, record its build: `podman inspect --format '{{.Image}}' llm-server`.
   4. **Gate G2**: free the GPU per `SKILL.md` Freeing the GPU (`bench-fit.sh` needs `llm_server_running` false). Remember
      the prior state (serving, or gateway-only) for step 9.
6. **Screen throughput.** `make benchmark` measures only enabled models, so `bench-fit.sh` screens with the same flag presets,
   plus the depth sweep and load sampling it lacks. Each run is a benchmark run (rule 2).
   1. With a baseline, first, on the live config:
      `bash $SK/scripts/bench-fit.sh --alias <baseline> --out tmp/model-scout/<run>/bench-baseline --depths 0,32768,<ref>`.
   2. The candidate, on `$S`:
      `LLM_ENV_CONFIG=$S bash $SK/scripts/bench-fit.sh --alias <alias> --out tmp/model-scout/<run>/bench --depths 0,32768,<ref>`.
   3. Show loading, benchmarking, done, then pp/tg per depth, `gpu.model_vram_mib`, peak temperature and average clock.
   4. Reject the candidate when either holds; it then gets no G3 and no ladder: report the screen numbers, restore per step 9
      and stop.
      - `model_vram_mib` exceeds the budget (the raw peak includes System One, which the budget already excludes).
      - `tg` at `<ref>` is below the floor (rule 3).
   5. A load error "unknown model architecture" goes to the runtime route (a newer llama.cpp build), not to a broken GGUF.
7. **Apply and measure with the repo's own targets.**
   - **7a. Apply and serve (Gate G3).**
     1. Rollback copy, before the first live edit:
        `cp -p ~/.config/llm-env/models.yml ~/.config/llm-env/models.yml.pre-<alias>-<stamp>`. Step 9 restores this copy,
        never the backup `make llama-enable` takes for itself.
     2. Entry, first match wins:
        - An Alias-row candidate already has one: go to 3.
        - A live entry with the same file
          (`yq '.models[] | select(.file == "<entry file>") | .alias' ~/.config/llm-env/models.yml`): use its alias, never a
          duplicate.
        - Otherwise add the candidate's entry from `$S` (its step 4 settings, `enabled: false`) under the alias decided at
          step 3, with `yq -i` (`make llama-enable` only selects an existing alias).
     3. `make llama-enable MODEL=<alias>` (validates the alias, sets `llm_server.enabled`, writes atomically).
     4. `LLM_ENV_ASSUME_YES=1 make setup` (unattended per AGENTS.md: the GPU prompt takes its default from the live file, so it
        answers yes after `make llama-enable`). It downloads a missing enabled file, validates the GGUFs, pulls the floating
        Vulkan tag and runs the budget check; `make check-setup` validates offline.
        - A Vulkan device selection error (no or several matching devices): hand `make setup` to the owner.
     5. No `make benchmark` (`SKILL.md` Gotchas): step 6 measured this model at depth.
     6. `make start-llama` (`make start` when the stack is down), then `make status`, `make check-server` and
        `make check-with-agents`.
     7. `make setup-local-llm-agents` (the router profile gets `llama-cpp/<alias>`).
   - **7b. Interference and capacity.** `make model-interference`, then `make model-capacity MODEL=<alias> DEPTH=<bench json>`
     (step 6). A failed gate goes to step 9.
   - **7c. Quality ladder.**
     1. Build: `podman inspect --format '{{.Image}}' llm-server` (the image Id, comparable with
        `podman image inspect --format '{{.Id}}' <gpu.image>`). `make start-llama` never recreates the container, so it
        equals step 5's.
        - A different Id (the container was recreated, for example by `make start`): the lanes ran on two llama.cpp builds,
          and step 8 reports (b) as unproven.
        - No baseline lane: only record it.
     2. Candidate lane (a benchmark run, rule 2):
        `PI_CODING_AGENT_DIR=$HOME/.pi/agent bash $SK/scripts/effort-run.sh --model router-env/llama-cpp/<alias> --out tmp/model-scout/<run>/effort --efforts low,high --seeds <baseline seeds.json>`.
        - No baseline lane: it is the first lane, with `--per-layer 3` instead of `--seeds`.
     3. `bash $SK/scripts/score.sh <results.jsonl>`, then `make model-capacity MODEL=<alias> SCORE=<score.json>`.
     4. `bash $SK/scripts/compare-runs.sh` with the baseline first (Head-to-head of the effort-ladder reference).
        - No baseline lane and no anchor: nothing to compare; skip it.
        - An anchor the owner named joins here per its route row, after **Gate G4**.
     5. Classify failures per the effort-ladder reference.
8. **Verdict.** Lead with the verdict and one reason. Before recommending, ask: would the owner notice this in a pi session?
   The candidate replaces the baseline only when all three hold:
   - (a) it fits `llmenv budget` with System One resident;
   - (b) it passes the replace rule (Score, item 6, of the effort-ladder reference) on one seed set and one build (the step 5
     and 7c Ids are equal). Unproven: no replace; offer to re-run the baseline lane on the 7c build through its own gated swap;
   - (c) pp and tg at `<ref>` from step 6 (one image for both) stay within about 3 % (run noise) of the baseline, or the
     quality gain justifies the slowdown and the trade is stated.
   - No baseline (step 2): the verdict rests on (a), the 20 t/s floor and the ladder reached; (b) and (c) are skipped and said
     so; G5 still applies.
   Then the table: candidate vs baseline vs anchors (score, solved/total, pp/tg at depth, VRAM at ctx, RAM, temperature) and
   what was not verified.
   - `podman image inspect --format '{{.Id}}' <gpu.image>` after 7a differs from the 7c Id: the table says the next container
     recreation (`make restart`) serves a build these numbers did not measure.
9. **Keep or revert (Gate G5).**
   - Keep: optionally add the entry to `models.yml.example` with `enabled: false`.
   - A declined G1 or G2, or a failure before G2 (a download size or sha256 mismatch): report it, remove the scratch files and
     stop. No `make stop-llama`: G2 has not stopped it.
   - A no to G5, a declined G3 or a failed step after G2: `make stop-llama`, then restore the rollback copy
     (`cp -p <rollback copy> ~/.config/llm-env/models.yml`; nothing to restore before G3).
   - Then, when llm-server served before G2: `make start-llama`. A host that was gateway-only stops there; `make restart`
     later applies gateway-only mode to the whole stack.
   - Not kept, and G1 put a file into the models directory: report its path and size and ask whether to delete it (a
     user-data delete: asked under bypass too).
   - Last: `podman ps -a` and a `ps` by PID for everything this run started: nothing of yours may remain.
```

- [ ] **Step 6: Route the new files from `SKILL.md`**

- :16, after "load the others only at the step that names them." append " Rows marked onboard load" and a new line "`references/selection-criteria.md` sections 1-3 at step 4 and `references/scoring.md` at step 5.".
- :20-22, the alias, Hugging Face and `.gguf` rows become:

```text
| the enabled alias (`alias`, `enabled: true`): re-measure | `references/served-alias.md` | `references/scoring.md` at R2 | `onboard-flow.md`, `tuning.md` |
| any other models.yml alias (`alias`) | `references/onboard-flow.md`, Alias | onboard | `tuning.md`, `challenges.md` |
| a Hugging Face repo or URL (`hf_repo`, `hf_file`) | `references/onboard-flow.md`, Hugging Face (a ranked pick: Scout pick) | onboard; `references/selection-criteria.md` section 7 at step 3 | `tuning.md`, `challenges.md` |
| a `.gguf` path (`gguf_file`) | `references/local-gguf.md`, then `references/onboard-flow.md`, Steps | onboard | `tuning.md`, `challenges.md` |
```

- :24, the scout row's third cell becomes "a pick to onboard: the Hugging Face row (Scout pick)".
- :81-82: "runs the flow's rollback (`onboard-flow.md` step 9)," becomes "runs the flow's own rollback step,".

- [ ] **Step 7: Measure, run and validate (no commit)**

Run: `wc -c .claude/skills/onboard-local-model/SKILL.md` (expected 10,985, at most 11,000), then `uv run pytest tests/skills/onboard-local-model/test_onboard_local_model_scripts.py -q`, then `make validate && make test`.
Expected: PASS and green, including `test_every_benchmark_out_dir_is_named_once` (the two new outs are distinct), `test_skill_prose_has_no_long_shell_logic`, `test_shared_rules_are_stated_once`, `test_eval_second_lane_fixture_matches_its_assertions` and the black-box guard.

### Task 4: Every flow opens with questions to ask first

**Files:**
- Modify: `.claude/skills/onboard-local-model/references/tuning.md` (after :4, and after the Standalone quant comparison paragraph that ends "G-Q3 for the verdict.")
- Modify: `.claude/skills/onboard-local-model/references/selection-criteria.md` (after :15, the `<run>` line of Scout and rank)
- Modify: `.claude/skills/onboard-local-model/references/anchors.md` (after :4)
- Test: `tests/skills/onboard-local-model/test_onboard_local_model_scripts.py` (new test after `test_onboard_flow_names_alias_items_a1_to_a3`)

**Interfaces:**
- Consumes: the blocks Task 3 already wrote into served-alias.md, local-gguf.md and onboard-flow.md.
- Produces: one "Ask yourself first:" line followed by 3-4 one-line `- ` questions, per flow. Questions guide judgment; they never add a step or a command.

- [ ] **Step 1: Write the failing test**

```python
ASK_BLOCKS = {"onboard-flow": 1, "served-alias": 1, "local-gguf": 1, "tuning": 2, "selection-criteria": 1, "anchors": 1}


def test_every_flow_opens_with_questions_to_ask_first() -> None:
    """Thinking guidance, not more steps: 3-4 one-line questions after each "Ask yourself first:" line."""
    for name, count in ASK_BLOCKS.items():
        lines = (REFS / f"{name}.md").read_text().splitlines()
        starts = [i for i, line in enumerate(lines) if line == "Ask yourself first:"]
        assert len(starts) == count, name
        for i in starts:
            block = []
            for line in lines[i + 1:]:
                if not line.startswith("- "):
                    break
                block.append(line)
            assert 3 <= len(block) <= 4 and all("?" in line for line in block), (name, block)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/skills/onboard-local-model/test_onboard_local_model_scripts.py -q -k "ask_first"`
Expected: FAIL for tuning, selection-criteria and anchors (0 blocks).

- [ ] **Step 3: Insert the blocks** (each after a blank line, followed by a blank line)

tuning.md, after :4:

```text
Ask yourself first:
- Which limit binds first for this model: VRAM at the target context, RAM for offload, or decode speed at depth?
- Is the gain larger than run noise (about 3 %)? A smaller one is no reason to touch the live config.
- Would a faster row that keeps less context make pi worse over a long session?
```

tuning.md, after the Standalone quant comparison paragraph that ends "G-Q3 for the verdict.":

```text
Ask yourself first:
- Does the higher quant fit with System One resident at the same context? If not, the comparison ends before any download.
- Can one seed set show the quality difference I expect, and is it worth the slower decode?
- Which alias serves at the end of every branch? Q11 always brings `<a>` back.
```

selection-criteria.md, after :15:

```text
Ask yourself first:
- Is a newer model better for agent loops on this card, or only newer? A leaderboard without version and effort proves neither.
- Does it fit in VRAM beside a resident System One, or does it need an offload this host measured as slow?
- Which claims on its card are unverified, and what evidence would change the ranking?
```

anchors.md, after :4:

```text
Ask yourself first:
- Is the comparison fair: one seed set, the same efforts and timeout, correctness only?
- Does a working route exist for each anchor before any quota is spent?
- Will a published number be quoted with its benchmark version and effort, or not at all?
```

- [ ] **Step 4: Run and validate (no commit)**

Run: `uv run pytest tests/skills/onboard-local-model/test_onboard_local_model_scripts.py -q -k "ask_first or quant_comparison or checklists or shell_logic or black_box"`, then `make validate && make test`.
Expected: PASS and green.

### Task 5: Fixtures for the four live-host evals, and the stopped-server eval

**Files:**
- Create: `.claude/skills/onboard-local-model/evals/files/preflight-stopped.json`
- Create: `.claude/skills/onboard-local-model/evals/files/hf-qwen/models.json`, `hf-qwen/model-qwen__Qwen3.8-Coder-9B-GGUF.json`, `hf-qwen/tree-qwen__Qwen3.8-27B-GGUF.json`, `hf-qwen/tree-qwen__Qwen3.8-Coder-9B-GGUF.json`
- Create: `.claude/skills/onboard-local-model/evals/files/anchors/route-check.txt`, `.claude/skills/onboard-local-model/evals/files/runtime/images.txt`
- Modify: `.claude/skills/onboard-local-model/evals/evals.json` (replace `latest-fit`, `onboard-plan-with-server-running`, `anchor-comparison`, `runtime-image-review`; append `enabled-alias-stopped`)
- Test: `tests/skills/onboard-local-model/test_onboard_local_model_scripts.py` (five new tests after `test_eval_alias_edit_typo_fixture_matches_its_assertions`)

**Interfaces:**
- Consumes: served-alias R1-R6 (Task 3) for `enabled-alias-stopped`; `hf-scout.sh`'s fixture mode (`HF_FIXTURE_DIR`: `models.json` for `--query`, `model-<owner>__<name>.json` for `--repo`, `tree-<owner>__<name>.json` for both).
- Produces: six evals with fixtures; no eval reads the live host or the network.

- [ ] **Step 1: Write the failing tests**

```python
QWEN_SHA = "9c" * 20


def test_eval_latest_fit_fixtures_match_their_assertions() -> None:
    pre = json.loads((FIX / "preflight-served.json").read_text())
    b = pre["budget"]
    assert (b["llmenv_budget"]["vram_available_mib"], b["model_vram_budget_mib"], b["llmenv_budget"]["vram_required_mib"],
            b["ram_offload_budget_mib"], pre["ram"]["swap_used_mib"]) == (8321, 10369, 7300, 12000, 3100)
    assert [m["alias"] for m in yaml.safe_load((FIX / "models.yml").read_text())["models"] if m["enabled"]] == ["gemma4"]
    text = _asserts("latest-fit")
    assert all(s in text for s in ("gemma4", "8321", "10369", "7300", "12000", "3100", "lab-a/Coder-9B-GGUF 23",
                                   "lab-d/MoE-30B-A3B-GGUF 20", "lab-c/Coder-27B-IQ2-GGUF", "(gemma)"))


def test_eval_onboard_plan_fixtures_match_their_assertions() -> None:
    hf = {"HF_FIXTURE_DIR": str(FIX / "hf-qwen"), "PATH": "/usr/bin:/bin"}
    flags = ("--json", "--budget-mib", "8321", "--ram-mib", "12000")
    rows = {r["id"]: r for r in json.loads(_run("bash", SCRIPTS / "hf-scout.sh", "--query", "Qwen3.8-27B", *flags, env=hf).stdout)}
    assert {k: (r["fit"], r["pick"]["quant"], r["pick"]["mib"]) for k, r in rows.items()} == {
        "qwen/Qwen3.8-27B-GGUF": ("no", "Q4_K_M", 16500), "qwen/Qwen3.8-Coder-9B-GGUF": ("vram", "Q5_K_M", 6400)}
    (row,) = json.loads(_run("bash", SCRIPTS / "hf-scout.sh", "--repo", "qwen/Qwen3.8-Coder-9B-GGUF", *flags, env=hf).stdout)
    assert (row["sha"], row["pick"]["file"], row["pick"]["bytes"]) == (QWEN_SHA, "Qwen3.8-Coder-9B-Q5_K_M.gguf", 6710886400)
    text = _asserts("onboard-plan-with-server-running")
    assert all(s in text for s in ("exits 3", "fit no", "16500", "Qwen3.8-Coder-9B-Q5_K_M.gguf", "6710886400", "qwen3-8-coder-9b",
                                   "gemma4 baseline lane", "--below-mib 6712"))


def test_eval_anchor_fixture_matches_its_assertions() -> None:
    out = dict(re.findall(r"^\$ (.+)\n(.+)$", (FIX / "anchors" / "route-check.txt").read_text(), re.MULTILINE))
    pi = "PI_CODING_AGENT_DIR=$HOME/.pi/agent pi auth check --model "
    assert out[pi + "opencode/claude-haiku-4-5"].startswith("auth ok") and "401" in out[pi + "openai/gpt-5.6-luna"]
    assert out["cursor-agent models"] == "Error: Authentication required"
    text = _asserts("anchor-comparison")
    assert all(s in text for s in ("Authentication required", "401", "opencode/claude-haiku-4-5", "never switches", "published numbers only"))


def test_eval_runtime_fixture_matches_its_assertions() -> None:
    saved = (FIX / "runtime" / "images.txt").read_text()
    vulkan, rocm = re.findall(r"^version: (\d+) ", saved, re.MULTILINE)
    upstream = re.findall(r"^b(\d+)$", saved, re.MULTILINE)[0]
    assert (vulkan, rocm, upstream) == ("10871", "10143", "12290")
    assert "| 10454 |" in (REFS / "runtimes.md").read_text()  # the last-seen table differs from this run's output
    assert all(s in _asserts("runtime-image-review") for s in ("10871", "10143", "1419", "not the last-seen 10454"))


def test_eval_enabled_alias_stopped_fixture_matches_its_assertions() -> None:
    served = json.loads((FIX / "preflight-served.json").read_text())
    stopped = json.loads((FIX / "preflight-stopped.json").read_text())
    assert (stopped["services"]["llm_server_enabled"], stopped["services"]["llm_server_running"]) == (True, False)
    assert not [h for h in stopped["gpu"]["holders"] if h["comm"] == "llama-server"]
    assert stopped["gpu"]["used_mib"] == served["gpu"]["used_mib"] - 7450
    rt = json.loads(_run("bash", SCRIPTS / "resolve-target.sh", "gemma4", env={**os.environ, **FIX_ENV}).stdout)
    assert {k: rt[k] for k in ("kind", "enabled", "present")} == {"kind": "alias", "enabled": True, "present": True}
    text = _asserts("enabled-alias-stopped")
    assert all(s in text for s in ("llm_server_running false", "make start-llama", "effort-served", "130560", "make stop-llama",
                                   "no llama-server holder"))
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/skills/onboard-local-model/test_onboard_local_model_scripts.py -q -k "latest_fit_fixtures or onboard_plan_fixtures or anchor_fixture or runtime_fixture or alias_stopped"`
Expected: FAIL (fixtures and assertions missing).

- [ ] **Step 3: Create the fixtures**

- `preflight-stopped.json`: a copy of `preflight-served.json` with `gpu.used_mib` 6200, `gpu.free_mib` 10105, the `llama-server` holder removed from `gpu.holders`, and `services.llm_server_running` false. Nothing else changes.
- `hf-qwen/models.json` (the `--query` listing; the query term is not read in fixture mode):

```json
[
  {"id": "qwen/Qwen3.8-27B-GGUF", "downloads": 5400, "likes": 210, "lastModified": "2026-10-25T00:00:00.000Z",
   "gguf": {"total": 27000000000, "context_length": 262144, "architecture": "qwen38"},
   "baseModels": {"models": [{"id": "qwen/Qwen3.8-27B"}]}, "tags": ["gguf"], "sha": "2727272727272727272727272727272727272727"},
  {"id": "qwen/Qwen3.8-Coder-9B-GGUF", "downloads": 3100, "likes": 150, "lastModified": "2026-10-27T00:00:00.000Z",
   "gguf": {"total": 9000000000, "context_length": 262144, "architecture": "qwen38"},
   "baseModels": {"models": [{"id": "qwen/Qwen3.8-Coder-9B"}]}, "tags": ["gguf"], "sha": "9c9c9c9c9c9c9c9c9c9c9c9c9c9c9c9c9c9c9c9c"}
]
```

- `hf-qwen/model-qwen__Qwen3.8-Coder-9B-GGUF.json`: the second object above, alone.
- `hf-qwen/tree-qwen__Qwen3.8-27B-GGUF.json`: one file, `Qwen3.8-27B-Q4_K_M.gguf`, size 17301504000 (16500 MiB), `lfs` `{"oid": "27" * 32, "size": 17301504000}` written out.
- `hf-qwen/tree-qwen__Qwen3.8-Coder-9B-GGUF.json`: `Qwen3.8-Coder-9B-Q4_K_M.gguf` 5767168000, `Qwen3.8-Coder-9B-Q5_K_M.gguf` 6710886400, `Qwen3.8-Coder-9B-Q8_0.gguf` 9961472000, each with `lfs` `{"oid": <"c4", "c5", "c8" repeated 32 times>, "size": <same>}`. With budget 8321 the scout picks Q5_K_M (6400 + 1536 <= 8321); the dense 27B is `fit: no`.
- `anchors/route-check.txt` (the outcomes recorded in anchors.md for 2026-10-30, in one transcript):

```text
$ PI_CODING_AGENT_DIR=$HOME/.pi/agent pi auth check --model opencode/claude-haiku-4-5
auth ok: opencode/claude-haiku-4-5
$ PI_CODING_AGENT_DIR=$HOME/.pi/agent pi auth check --model openai/gpt-5.6-luna
auth failed: openai/gpt-5.6-luna: HTTP 401 Unauthorized
$ cursor-agent status
Logged in
$ cursor-agent models
Error: Authentication required
```

- `runtime/images.txt` (build numbers differ from runtimes.md's last-seen table on purpose):

```text
$ podman images ghcr.io/ggml-org/llama.cpp
REPOSITORY                  TAG            IMAGE ID      CREATED      SIZE
ghcr.io/ggml-org/llama.cpp  server-vulkan  3f2a9c1d7e44  9 days ago   1.21 GB
ghcr.io/ggml-org/llama.cpp  server-rocm    9c1b4e2a0f81  7 weeks ago  6.83 GB
$ podman run --rm --entrypoint /app/llama-server ghcr.io/ggml-org/llama.cpp:server-vulkan --version
version: 10871 (4be1f2a)
built with cc (GCC) 14.2.0 for x86_64-linux-gnu
$ podman run --rm --entrypoint /app/llama-server ghcr.io/ggml-org/llama.cpp:server-rocm --version
version: 10143 (7d09c3e)
built with cc (GCC) 13.3.0 for x86_64-linux-gnu
$ gh api "repos/ggml-org/llama.cpp/releases?per_page=1" --jq ".[0].tag_name"
b12290
```

- [ ] **Step 4: Rewrite the four evals and append the fifth**

```json
{
  "id": "latest-fit",
  "prompt": "cual es el ultimo modelo agentico que cabe ahora en mi equipo? busca en hugging face si hay algo mejor que el que tengo en models.yml y dime por que. (Eval setup: the config is .claude/skills/onboard-local-model/evals/files/models.yml (pass it as LLM_ENV_CONFIG). preflight.sh already printed .claude/skills/onboard-local-model/evals/files/preflight-served.json; use that JSON as its output. The Hugging Face scouts for coder and agent already printed .claude/skills/onboard-local-model/evals/files/scout-coder.json and .claude/skills/onboard-local-model/evals/files/scout-agent.json, and the research subagents returned the cards in .claude/skills/onboard-local-model/evals/files/evidence/; do not run hf-scout.sh, curl or subagents. DRY RUN for evaluation: read-only commands only; do not stop containers, download model files, edit configuration or start benchmarks. Answer in the final message, in English.)",
  "files": ["evals/files/models.yml", "evals/files/preflight-served.json", "evals/files/scout-coder.json", "evals/files/scout-agent.json",
            "evals/files/evidence/1.json", "evals/files/evidence/2.json"],
  "expected_output": "A read-only report on the fixtures: gemma4 is the current model and fits the 8321 MiB budget with System One resident; rank-candidates.sh shortlists three rows and ranks lab-a 23 and lab-d 20; at most three go on; the IQ2 row is not recommended; the offload MoE is held back by swap in use; no candidate is called better than gemma4 before a measured run; what was not verified is listed.",
  "assertions": [
    "The current model is gemma4 (Gemma 4 12B Agentic v2), the enabled entry of the fixture models.yml, read from the file, not assumed",
    "The VRAM budget is 8321 MiB, the smaller of llmenv_budget.vram_available_mib 8321 and model_vram_budget_mib 10369, named; never the 16305 MiB card total or live free VRAM",
    "gemma4 fits with System One resident: llmenv_budget feasible true, 7300 of 8321 MiB",
    "RAM for MoE offload is 12000 MiB (budget.ram_offload_budget_mib), and 3100 MiB of swap in use holds back the offload row lab-d/MoE-30B-A3B-GGUF",
    "rank-candidates.sh over both scout files shortlists at most 6 rows (3 here) and at most three are recommended to go on, ranked lab-a/Coder-9B-GGUF 23 then lab-d/MoE-30B-A3B-GGUF 20, with lab-b/Agent-14B-GGUF reported unresearched; each row gives lab, parameters, quant and context, and the answer says the scout rows carry no base model",
    "lab-c/Coder-27B-IQ2-GGUF (2.1 bits per weight) is not recommended: it failed the bpw gate",
    "The answer names the baseline-family scout term (gemma) it would also run, and says no candidate is proven better than gemma4 before a measured run on the onboarding route",
    "The report separates measured facts from model-card claims and lists at least one thing it did not verify",
    "No container was stopped, no file downloaded and no configuration edited"
  ]
},
{
  "id": "onboard-plan-with-server-running",
  "prompt": "quiero probar Qwen3.8-27B (o su variante coder mas chica si no cabe) en mi GPU y compararlo con el modelo actual. Give me the exact ordered plan with the commands you would run, where you would ask me to confirm, and how you would show progress. (Eval setup: the config is .claude/skills/onboard-local-model/evals/files/models.yml and its models directory .claude/skills/onboard-local-model/evals/files/models/ (pass them as LLM_ENV_CONFIG and LLM_ENV_MODELS_DIR); llm-server is RUNNING and serving gemma4. preflight.sh already printed .claude/skills/onboard-local-model/evals/files/preflight-served.json; use that JSON as its output. Hugging Face reads come from .claude/skills/onboard-local-model/evals/files/hf-qwen/ (pass it as HF_FIXTURE_DIR to hf-scout.sh); no other network. DRY RUN for evaluation: do not execute any step that changes the host; read-only inspection is fine. Answer in English.)",
  "files": ["evals/files/models.yml", "evals/files/preflight-served.json", "evals/files/hf-qwen/models.json",
            "evals/files/hf-qwen/model-qwen__Qwen3.8-Coder-9B-GGUF.json", "evals/files/hf-qwen/tree-qwen__Qwen3.8-27B-GGUF.json",
            "evals/files/hf-qwen/tree-qwen__Qwen3.8-Coder-9B-GGUF.json"],
  "expected_output": "The bare name exits 3 and hf-scout.sh --query finds the 27B (fit no) and the coder 9B (fit vram, Q5_K_M), so the plan follows the owner's fallback; then an ordered plan with the gemma4 baseline lane before G2, separate gates for the download, for stopping only llm-server and for the live config edit, a cp -p rollback copy, System One left running and a leftover check at the end.",
  "assertions": [
    "resolve-target.sh Qwen3.8-27B exits 3 (a bare name), so the repo comes from hf-scout.sh --query Qwen3.8-27B --json --budget-mib 8321 --ram-mib 12000 before any gate",
    "qwen/Qwen3.8-27B-GGUF is fit no (its only quant, Q4_K_M at 16500 MiB, does not fit 8321 MiB, and a dense model is never offloaded), so the plan takes the owner's fallback qwen/Qwen3.8-Coder-9B-GGUF (fit vram) and says why",
    "H1 scouts that repo alone; G1 states Qwen3.8-Coder-9B-Q5_K_M.gguf, 6710886400 bytes (6400 MiB), 182000 MiB free disk and the destination ~/llm-workspace/models; the entry alias is qwen3-8-coder-9b",
    "The gemma4 baseline lane runs before G2, on the router profile (router-env/llama-cpp/gemma4, --per-layer 3)",
    "A separate confirmation step (G2) comes before stopping llm-server: make stop-llama, then wait-gpu-release.sh --below-mib 6712; never make stop, make restart or an omniroute restart",
    "System One is left running and the plan explains why (it is budgeted as resident)",
    "A cp -p rollback copy of the live models.yml is taken before it is edited; make llama-enable's own backup is not the rollback copy",
    "The plan includes numbered progress stages covering setup, unload, load, checks and benchmarks",
    "The plan benchmarks throughput at context depth (for example 32K and 128K), not only at depth 0",
    "The plan ends with verifying that no containers or processes started by the run remain"
  ]
},
{
  "id": "anchor-comparison",
  "prompt": "compara el modelo local que tengo ahora contra haiku 4.5, gpt-5.6 luna y composer 2.5 en tareas de codigo y dame un score. Say what you would do and what you found about the availability of each anchor. (Eval setup: the config is .claude/skills/onboard-local-model/evals/files/models.yml and preflight.sh already printed .claude/skills/onboard-local-model/evals/files/preflight-served.json. The route checks already ran; their output is saved in .claude/skills/onboard-local-model/evals/files/anchors/route-check.txt; use it instead of pi or cursor-agent. DRY RUN for evaluation: read-only commands only (cat, jq, yq); do not launch benchmark or agent task runs. Answer in English.)",
  "files": ["evals/files/models.yml", "evals/files/preflight-served.json", "evals/files/anchors/route-check.txt"],
  "expected_output": "Each anchor is handled from the saved route checks: haiku is the one runnable anchor lane after G4; luna (401) and Composer 2.5 (no pi route, cursor-agent unauthenticated) are compared by published numbers only, never switched to another provider; the gemma4 lane runs first and writes seeds.json; correctness-only comparison; the seed set is retired.",
  "assertions": [
    "The local model is gemma4, the enabled and served entry of the fixture config",
    "Composer 2.5 has no pi route, and cursor-agent models fails with Authentication required in the saved output, so there is no verified route: Composer 2.5 is compared by published numbers only, with benchmark version and effort, and the OmniRoute grok-composer variant is never counted as the anchor",
    "openai/gpt-5.6-luna fails its auth check with 401 on PI_CODING_AGENT_DIR=$HOME/.pi/agent: no verified route, so it is compared by published numbers only (version and effort stated, the conflicting SWE-bench figures not quoted) until the owner fixes its credentials; the answer never switches it to another provider or model",
    "opencode/claude-haiku-4-5 passes its auth check, so it is the one anchor lane, after G4 states provider, model, runs, the upper bound and that it spends quota",
    "The local lane (router-env/llama-cpp/gemma4, --per-layer 3) runs first and writes seeds.json; the haiku lane passes --seeds with the same --efforts and --timeout",
    "compare-runs.sh --correctness-only gives local overall over anchor overall with both ladders, local lane first",
    "The seed set is retired after the cloud lane",
    "No benchmark or agent run was launched"
  ]
},
{
  "id": "runtime-image-review",
  "prompt": "deberiamos actualizar la imagen docker de llama.cpp o pasarnos a rocm para este modelo? (Eval setup: the read-only image commands already ran; their output is saved in .claude/skills/onboard-local-model/evals/files/runtime/images.txt; use it instead of podman or gh. The config is .claude/skills/onboard-local-model/evals/files/models.yml. DRY RUN for evaluation: do not pull images, run benchmarks or change configuration. Answer in English.)",
  "files": ["evals/files/runtime/images.txt", "evals/files/models.yml"],
  "expected_output": "Reports the builds from the saved output (Vulkan 10871, ROCm 10143, upstream b12290), recommends an A/B with identical flags at context depth rather than deciding from community reports, notes the ROCm image is older and was unusable here, and applies image changes only through render-unit plus a gated restart, never by restarting omniroute.",
  "assertions": [
    "The answer reports Vulkan build 10871 and ROCm build 10143 from the saved --version output, not the last-seen 10454 of the reference table",
    "It says the production Vulkan image (gpu.image in the fixture config) is 1419 builds behind upstream b12290",
    "It notes that the local ROCm image is older than the Vulkan image and was not usable as-is on this host (the iGPU enumerates as device 1)",
    "It proposes an A/B with identical flags on both images at context depth (32K and 128K) before any decision, the ROCm side by hand after freeing the GPU (G2) and wrapped in timeout",
    "It does not change gpu.image, pull an image or restart any container; any apply step goes through setup/render-unit.sh and a gated make restart, never an omniroute restart"
  ]
},
{
  "id": "enabled-alias-stopped",
  "prompt": "how fast is gemma4 now at long context? measure it again and give me the ordered plan with its gates. (Eval setup: the config is .claude/skills/onboard-local-model/evals/files/models.yml and its models directory .claude/skills/onboard-local-model/evals/files/models/ (pass them as LLM_ENV_CONFIG and LLM_ENV_MODELS_DIR). preflight.sh already printed .claude/skills/onboard-local-model/evals/files/preflight-stopped.json; use that JSON as its output. DRY RUN for evaluation: read-only commands only (resolve-target.sh with those variables, cat, yq, jq); do not start or stop containers, edit models.yml or start benchmarks. Answer in English.)",
  "files": ["evals/files/models.yml", "evals/files/models/gemma4-v2-Q4_K_M.gguf", "evals/files/preflight-stopped.json"],
  "expected_output": "gemma4 is the enabled alias but llm-server is stopped: the plan starts it with make start-llama, reads preflight again, then re-measures (lane while served, G2, bench-fit at depth, G3 serve, interference and card) with no scratch config and no verdict, and hands llm-server back stopped with make stop-llama.",
  "assertions": [
    "resolve-target.sh gemma4 with the fixture variables gives kind alias, enabled true, present true, and the preflight JSON says llm_server_enabled true and llm_server_running false: this is the re-measure with llm-server stopped, not an onboarding of gemma4 against itself",
    "The plan first runs make start-llama (the stack is up: System One and OmniRoute run), never make start, make restart, make llama-enable or make setup, and then reads preflight again",
    "No scratch config, candidate or replacement verdict appears",
    "The ladder lane (router-env/llama-cpp/gemma4 on PI_CODING_AGENT_DIR=$HOME/.pi/agent, --per-layer 3, --out tmp/model-scout/<run>/effort-served) runs while gemma4 is served, before G2",
    "G2's wait-gpu-release.sh --below-mib comes from the preflight read after make start-llama (U - H + 512 with the llama-server holder present), not from the stopped JSON, which has no llama-server holder",
    "bench-fit.sh runs after G2 at depths that include 32768 and 130560 (the fixture's ctx_size 131072)",
    "At the end llm-server is handed back stopped with make stop-llama, as it was found, and the leftover check (podman ps -a, PIDs) runs",
    "No container was started or stopped, no configuration was edited and no benchmark was started"
  ]
}
```

- [ ] **Step 5: Run and validate (no commit)**

Run: `uv run pytest tests/skills/onboard-local-model/test_onboard_local_model_scripts.py -q -k "eval_ or black_box or user_turns or name_only_existing"`, then `make validate && make test`.
Expected: PASS and green. `test_eval_scout_fixtures_match_their_assertions` still finds "at most 6" and "at most three are recommended to go on" in `latest-fit`; `test_eval_alias_fixtures_match_their_assertions` still finds "exits 3" in `onboard-plan-with-server-running`; `test_eval_input_files_exist_and_the_gguf_fixture_resolves` finds every new file (or its directory) in its prompt.

### Task 6: Reference lows

**Files:**
- Modify: `.claude/skills/onboard-local-model/references/challenges.md:8`
- Modify: `.claude/skills/onboard-local-model/references/tuning.md` (P0, after "the bare name also finds the file in a sub-folder).")
- Test: `tests/skills/onboard-local-model/test_onboard_local_model_scripts.py` (`test_quant_p0_pins_the_repo_path_and_names_hf_scout_exit_2` :2146, `test_references_keep_host_specific_evidence_and_give_every_flag_a_destination` :1005)

- [ ] **Step 1: Write the failing assertions**

- In the P0 test: `assert '`fit: "unknown"`' in p0 and "report a read failure" in p0`.
- In the host-evidence test: `assert "it wraps `challenges/gen.mjs`" not in _md(CHALLENGES_MD)`.

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/skills/onboard-local-model/test_onboard_local_model_scripts.py -q -k "p0_pins or host_specific"`
Expected: FAIL.

- [ ] **Step 3: Edit**

- challenges.md:8: "`bash $SK/scripts/gen-challenge.sh` is the only entry point; it wraps `challenges/gen.mjs` (node ESM, ..." becomes "`bash $SK/scripts/gen-challenge.sh` is the only entry point (node ESM, ...". The path table keeps `gen.mjs`.
- tuning.md P0, insert after "the bare name also finds the file in a sub-folder).": "`fit: \"unknown\"` (the tree was unreadable, `pick` null): retry once, else report a read failure and stop."

- [ ] **Step 4: Run and validate (no commit)**

Run: `uv run pytest tests/skills/onboard-local-model/test_onboard_local_model_scripts.py -q -k "p0_pins or host_specific or quant or shell_logic"`, then `make validate && make test`.
Expected: PASS and green.

### Task 7: Verification and the plans index row

**Files:**
- Modify: `docs/superpowers/plans/README.md` (this plan's row)

- [ ] **Step 1: Guards and suites, one file at a time**

Run, one after the other:
- `uv run pytest tests/skills/onboard-local-model/test_onboard_local_model_scripts.py -q`
- `uv run pytest tests/skills/onboard-local-model/test_challenges.py -q`
- `uv run pytest tests/skills/onboard-local-model/test_latency_profile.py -q`
- `make validate && make test`

Expected: all green. The guard set in the first file: `-k "black_box or stated_once or never_point or route_table or router or shell_logic or named_once or user_turns or ask_first or a1_to_a3"`.

- [ ] **Step 2: Byte cap and description**

Run: `wc -c .claude/skills/onboard-local-model/SKILL.md`. Expected: at most 11,000 (about 10,985). `test_skill_description_fits_the_spec_limit` passes (938 characters).

- [ ] **Step 3: Read-only route walk with the fixtures**

From the repo root: `SK=.claude/skills/onboard-local-model F=$SK/evals/files`, `LLM_ENV_CONFIG=$F/models.yml`, `LLM_ENV_MODELS_DIR=$F/models`; `HF_FIXTURE_DIR` only for hf-scout.sh. Scratch files go to `tmp/plan-l-walk/`, removed at the end. For each route, run the commands and read the route's file as a new agent would.

1. Served alias, running: `resolve-target.sh gemma4` gives `alias`, `enabled true`, `present true`; the route table sends it to served-alias.md. With `preflight-served.json`, R3's target is `--below-mib 6712`; R5 asks G3 and edits nothing; R6 has no verdict.
2. Served alias, stopped: with `preflight-stopped.json`, R1 runs `make start-llama`, then R1 again (U and H from the new read); R6 ends with `make stop-llama`. A no to G3 leaves it stopped.
3. Typo aliases: `Gemma4`, `gema4`, `gemma3` and `GEMMA-4` exit 2 naming `gemma4`; `orinth-35b` and `Ornith-35B` exit 2 naming `ornith-35b`; `ornth` names `ornith`. `Qwen3.8-27B`, `Zeta-Coder-7B` and `gemma-4-27b-it` exit 3.
4. Other alias: `resolve-target.sh ornith-35b` gives `enabled false`, `present false`; onboard-flow Alias: G1 downloads its url, step 4 writes no settings, G3 runs `make llama-enable`.
5. Hugging Face: `.../lab/Foo-9B-GGUF/resolve/v2/Foo-9B-Q4_K_M.gguf` gives `hf_file` at `v2`; `hf-scout.sh --repo lab/Foo-9B-GGUF --revision v2 --file Foo-9B-Q4_K_M.gguf` pins the sha; H1-H5 read one condition per line; the alias is `foo-9b` (A2).
6. Bare name: `Qwen3.8-27B` exits 3; `HF_FIXTURE_DIR=$F/hf-qwen hf-scout.sh --query Qwen3.8-27B --json --budget-mib 8321 --ram-mib 12000` gives the 27B `no` and the coder 9B `vram` (Q5_K_M).
7. Local GGUF: `resolve-target.sh $F/Foo-9B-Q4_K_M.gguf --scratch-config tmp/plan-l-walk/s.yml` gives `in_models_dir false` and a `candidate`; local-gguf L1-L7 then Steps. `resolve-target.sh $F/models/gemma4-v2-Q4_K_M.gguf` gives `in_models_dir true`, and L1 sends it to gemma4's own row (A1: the enabled alias, the re-measure). `$F/Ornith-Q5_K_M.gguf` takes alias `ornith-2` (A2, then A3). The live models.yml is unchanged.
8. Scout: `rank-candidates.sh --scout $F/scout-coder.json --scout $F/scout-agent.json --evidence $F/evidence` ranks lab-a 23 and lab-d 20.
9. Quant: `models-local-url.yml` sends P0 to the repo question; `models-subfolder.yml` passes `test_eval_subfolder_quant_fixture_matches_its_assertions`; a local `gemma4-v2-Q5_K_M.gguf` would be named `gemma4-q5_k_m` by A2 (the quant route's name) and decided at G5.
10. Anchors and runtime: reading `anchors/route-check.txt` with anchors.md leaves one runnable anchor lane (haiku); reading `runtime/images.txt` with runtimes.md quotes 10871 and 10143, not the table's 10454.
11. Out dirs: `test_every_benchmark_out_dir_is_named_once` passes.

Then `podman ps -a` lists the same containers as before the walk, and `tmp/plan-l-walk/` is gone.

- [ ] **Step 4: Plans index row**

The row below was added with this plan, after the 2026-10-31-judge-round-9-fixes row (rows of plans G, H, I, J and K unchanged). On completion, change `PENDING` to `**DONE** (executed <date>; skill edits uncommitted with the untracked skill tree; judges re-run by the orchestrator)`.

```text
| 2026-10-31-judge-round-10-fixes | PENDING | fixes from skill-judge round 10 of onboard-local-model (`tmp/judges/r10-native-summary.txt` 89.6 % B, 6 medium, 6 low; `tmp/judges/r10-referee-summary.txt` 91.2 % A, its high and mediums refuted by the lead, one wording residual and 3 lows; bar: >= 90 % from both lanes, no critical or high) plus the structural causes of the native lane's 87.2-89.6 % plateau; findings verified against the files first (refuted and dropped ones in its ledger): `resolve-target.sh` names an alias within two edits (`gema4`, `orinth-35b`) with exit 2 instead of a Hugging Face search, and `in_models_dir` is true only for a file directly in the models dir (red-first); the served alias and the local GGUF move into `served-alias.md` (llm-server stopped: `make start-llama`, then the re-measure, handed back stopped; a declined G3 leaves it stopped) and `local-gguf.md` (name check, never copies over an existing file), both routed from `SKILL.md`; `onboard-flow.md` rewritten one condition per line with alias items A1-A3 (another quant of a live entry takes `<alias>-<quant>`, the quant route's name; G1 never deletes a file it did not download); every flow opens with 3-4 "Ask yourself first" questions; `SKILL.md` rule 2 says each `bench-fit.sh` run may run inline, the `make benchmark` gotcha is stated once, the description adds re-measure (stays under 11,000 bytes, measured drafts 10,955 then 10,985); fixtures for the four live-host evals (latest-fit, onboard-plan-with-server-running, anchor-comparison, runtime-image-review) and two new evals (enabled-alias-stopped, alias-edit-typo), each pinned by a test; no tracked code changes; skill edits stay uncommitted with the untracked skill tree; judges re-run by the orchestrator |
```

## Round-1 review outcome (approved, 1 confirmed high, 3 mediums)

Review round 1 (native Opus lane plus the referee lane, lead-verified against a work copy; tmp/plan-l-review/review-round-1.md) left one
CONFIRMED high and three mediums; the referee's 2 critical and 5 high findings were refuted. Plan status: approved for execution. The
implementer must apply these amendments, which override the task text above where they differ:

- H1 (Task 3 tests): `_md()` takes a `Path`. The three calls on a `str` (`_md(alias)` in `test_routes_have_progress_checklists`,
  `_md(new_entry)` and `flow = _md(raw)` in `test_onboard_flow_names_alias_items_a1_to_a3`) become `" ".join(alias.split())`,
  `" ".join(new_entry.split())` and `" ".join(raw.split())`.
- M1 (Task 1, owner decision 1 narrowed): the edit distance applies only when both names carry the same digits. The jq `select` adds
  `or ((norm | gsub("[^0-9]"; "")) == ($n | gsub("[^0-9]"; "")) and lev(norm; $n) <= 2)` instead of a bare `or lev(norm; $n) <= 2`, so a
  real model name that differs in a version digit still reaches the search. Parametrize changes: `("gemma3", 3, None)`;
  `("ornith-3", 2, "ornith-35b")` (the prefix rule only; `ornith` has no digit 3); regression cases `gemma2`, `gemma7`, `gemma-2b`,
  `gemma-3n`, `gemma-4-9b`, `ornith-2`, `ornith-7b`, `ornith-25b` all `3, None`. The header says "or is within 2 edits of one with the
  same digits". Review Focus 1 and walk item 3: `gemma3` exits 3 (a search), not 2.
- M2 (Task 3, served-alias R6): one choice, stated once: R1 still runs `make start` on a down stack, and R6 says that only llm-server is
  handed back as it was found; the containers `make start` brought up stay up. The R6 bullet keeps "`make stop-llama` hands it back".
- M3 (Task 3, local-gguf L1): the live-entry bullet ("A live entry has this `file` ... the owner named that alias") comes first, before
  the `in_models_dir` true bullet, so `models/gemma4-v2-Q4_K_M.gguf` routes to gemma4's row. `test_local_gguf_reference_owns_the_name_check_copy_and_live_entry`
  asserts that order.
- Lows L1-L4 of the review are not taken in this plan.
