# Judge Round 8 Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the confirmed findings of round 8 of the onboard-local-model skill judges (native 87.2 % B with 1 high and 5 medium; referee 90.4 % A by its JSON, its one high rated medium by the lead), the residual lows of plan I, and one verified scoring defect (a ladder run without layer 1 reports `highest_layer` 0), so the next round gives >= 90 % from both lanes with no critical or high finding.

**Architecture:** Round 8 found no structural regressions. It found order bugs inside procedures, plus two input classes the scripts mis-route. Each fix is pinned by a test of the order or the input: step 4 selects the candidate before it validates it; the quant comparison runs its cheap headroom check before the 2.5 h ladder lane, and refuses a `file://` entry before any GPU step; `resolve-target.sh` treats a path-like argument as a path (exit 2 with the closest `.gguf`), never as a model name or repo id; `score.sh` counts the ladder from the lowest layer run. `SKILL.md` stays a router under its 11,000-byte cap: the bypass rule and the row tie-break are stated once there, and restated text leaves it.

**Tech Stack:** bash, jq, yq; Python 3 (`pylib/capacity_report.py`); pytest (`tests/skills/onboard-local-model/`, `tests/test_capacity.py`); skill-judge rubric v4.

**Spec:** `tmp/judges/r8-native-summary.txt` and `tmp/judges/r8-referee-summary.txt` (round 8 verdicts; the referee file holds the lead's check of its high), plan I (`docs/superpowers/plans/2026-10-31-judge-round-7-fixes.md`, executed: the base state) with the residual lows from its executor report, and the coordinator's added defect (`make model-report MODEL=ornith-15-9b` prints "Highest layer passed: 0 of 5; pass rate 85.0 %" from `~/.config/llm-env/capacity/ornith-15-9b.json` and `tmp/h2h/q4/score.json`). The ledger below restates all of them.

## Global Constraints

- English only. Edit only `.claude/skills/onboard-local-model/**`, `tests/skills/onboard-local-model/**`, `pylib/capacity_report.py`, `tests/test_capacity.py` and this plan's row in `docs/superpowers/plans/README.md` (added with the plan; Task 7 updates its status). The skill tree and `tests/skills` are untracked and stay uncommitted. The two tracked files of Task 5 are committed together, and nothing else is committed. The README rows of plans G, H and I stay as they are.
- `SKILL.md` <= 11,000 bytes (`SKILL_MD_MAX_BYTES`, tested). Never raise the cap: move text into references instead. Measured draft after Task 3: 10,906 bytes (now 10,973). If a reflow pushes the file over the cap, shorten rule 2's parenthetical "(command, out dir, stop and cleanup conditions, what to return)" first.
- Description <= 1024 characters (tested). Measured draft: 876 (now 983).
- A reference never names another reference file (`test_references_never_point_at_each_other`). Inside a reference, write "the effort-ladder reference", never `scoring.md`.
- `jev-fast-decision` is a black box, and ai-kit is never named in the skill (guard `test_skill_and_touched_docs_treat_jev_as_a_black_box`).
- Prose holds no shell logic. A backtick span may contain at most two of `;`, `&&` and `||` (`test_skill_prose_has_no_long_shell_logic`).
- Every script keeps `--help` (exit 0) and its tests. A change in behaviour is red-first: write the test, watch it fail, then implement. Pure coverage tests (marked as such) pass when written.
- After a `.sh` edit run `make validate`. After a `.py` edit (test files included) run `make validate && make test`. Run focused pytest suites one at a time, one file per command, never in parallel with each other.
- Scratch files go under `./tmp/`, and the run that creates them removes them. No containers, no GPU, no network, no downloads, no edits to the live `models.yml`. The only owner state written is the ladder section of the `ornith-15-9b` capacity card and its saved report (Task 5, owner decision 8).

## Findings ledger (verified 2026-10-31 against the files)

| Finding | Verdict | Task |
|---|---|---|
| native D8-1 (high): onboard-flow.md:90 runs `validate-gguf` on `$S` before `models select <alias>`; on the Hugging Face route `$S` still enables the baseline | confirmed: `cmd_validate_gguf` (llmenv.py:781) checks only enabled models, and H5 adds the new entry with `enabled: false` | 1 |
| native D8-2: a path-like argument that is not a `.gguf` exits 3 or becomes `hf_repo` | confirmed by a read-only run: `evals/files/Foo-9B-Q4_K_M.ggu`, `Foo-9B-Q4_K_M.ggu`, `/var/home/bazzite/Downloads/Devstral-Small-3-Q4_K_M` and `./Foo-9B` exit 3; `evals/files` (a directory) and `evals/Foo-9B-Q4_K_M` print `hf_repo`. Same as plan I residual "exit 3 also catches path-like args" | 2 |
| native D7-1: no test covers path-like inputs (test file :1620-1636) | confirmed (same gap as D8-2) | 2 |
| native D8-3: quant comparison of a `file://` entry dies at quant-headroom.sh:53; tuning.md:99-101 gives no next step | confirmed. Worse than reported: `load_entry` runs `capacity show` first, so without a card the agent spends P2/P3 (interference, a GPU bench) before `check` reaches the url | 4 |
| native D2-1: tuning.md:111-116 runs P1 (a 2.5 h ladder lane) before the cheap Q1 check | confirmed (5 x 3 x 2 x 300 s = 9,000 s) | 4 |
| native D6-1: bypass keeps a new model at G5 (SKILL.md:80) but keeps the current quant at G-Q3 (tuning.md:164) | confirmed | 3 |
| referee D8-1 (lead: medium): SKILL.md:34 has no tie-break without a model argument; rows :27/:28 ban each other's file | confirmed for the runtime + System One pair (the description names it as one clause); the quant + tune half is refuted (both rows open tuning.md, which settles it) | 3 |
| native D1-1 (low): NEVER items restate AGENTS.md (:89-91, :97-98, :102) | partly taken: :89-91 and :102 keep only their skill-specific reason. :97-98 stays: `agent_status: 0`, `stopReason: error` and `invalid_runs` are skill knowledge | 3 |
| native D3-1 (low): Gotchas holds 2 items | dropped: no bytes (10,906 after the required edits). The facts stay where they bite: rule 1 (RAM 0), onboard step 4, runtimes.md (iGPU device 1) | - |
| native D4-1 (low): the description is 983 characters, a run-on list | taken with our own text: the judge's replacement drops "a model name" (the bare-name route) and the Spanish trigger | 3 |
| native D5-1 (low): every named route loads all of onboard-flow.md | taken in onboard-flow.md's header (no SKILL.md bytes) | 1 |
| native D8-4 (low): Hugging Face sub-steps 1-5 and Steps 1-9 cross-refer by bare number | taken: sub-steps become H1-H5; "(step 2)" and "step 4's" become "Steps 2" and "Steps 4's" | 1 |
| native D8-5 (low) / plan I residual: `<run>`, `<stamp>`, `<models_dir>`, `<dir>` never defined | confirmed. `<run>` and `<stamp>` are defined once in SKILL.md; `<models_dir>` once in onboard-flow.md; `<dir>` is replaced by the path it means | 1, 3, 4, 6 |
| native D7-2 (low): eval prompts are scaffolded | taken: two casual prompts (a mistyped path; quant room for a local-file entry) | 2, 4 |
| plan I residual: H5 can duplicate an entry whose `file` the live config already has | confirmed: H5 always adds the entry, while Alias for a new entry rule 1 keeps the existing one | 1 |
| plan I residual: the 3-row `latest-fit` fixture cannot test "at most 6" (test :1694) | confirmed: `len(shortlist) <= 6` is vacuous with 3 rows, and no test passes more than 6 rows without `--top` | 6 |
| plan I residual: the route guard can match "Alias for a new entry" | confirmed: `^#+ Alias\b` also matches `### Alias for a new entry`; the exact-heading regex was checked against all six current pairs | 3 |
| coordinator: `score.sh`/`compare-runs.sh` report ladder 0 when L1 is not run, and the report prints "0 of 5" beside an 85 % pass rate | confirmed: score.sh:83 needs every layer 1..N; `effort-run.sh --layers` makes a subset run legal; `tmp/h2h/q4/score.json` has `ladder_at` 2-5 all `medium` and `ladder` 0; `test_score_ladder_is_zero_without_layer_one...` (:387) pins the defect | 5 |
| referee D6-1 (medium): selection-criteria.md:18 has no default | refuted by the lead ("else skip" is the default). Residual low taken: name the `deep-research` skill | 6 |
| referee D6-2 (medium): runtimes.md:45 flags have no default | refuted as medium by the lead. Residual low taken: `-ub` first when no flag is named | 6 |
| referee D7-1 (low after the lead): 11 + 11 trigger queries | dropped: "about 20" with 8-10 per side is a guideline; 22 is within it, and the test pins 8-12 per side | - |
| referee D7-2: evals.json:91 calls validate-gguf and budget scripts | refuted by the lead: the assertion never calls them scripts | - |
| referee D1-1: SKILL.md:50 (generated challenges in `./tmp/`) repeats NEVER :106 | dropped: the NEVER item covers only the owner's repositories; the sentence is the only statement of the generated-only, `./tmp/`-isolated rule and is pinned by `test_skill_states_that_challenges_run_only_in_isolated_dirs_under_tmp` | - |
| referee D2-1: the onboard flow is long | not a defect (the referee's own fix: leave it) | - |
| referee D3-1: the calibration log has only "not calibrated yet" | deferred: a real row needs a GPU calibration run, outside this no-GPU plan (owner's call) | - |
| referee D5-1: SKILL.md:19 "Scout pick" names no file | dropped: Scout pick is a section of the row's own flow file, `references/onboard-flow.md` (cosmetic, per the lead) | - |

## Owner decisions (defaults chosen)

1. **Path-like = a path.** After the alias check, an argument that is not a `.gguf` exits 2 when it starts with `/`, `./`, `../` or `~`; holds two or more `/`; exists as a file or directory; is `ORG/NAME` whose `ORG` is an existing directory; or ends in a `.gg*` extension (`.ggu`, `.gguff`). A real Hugging Face repo whose org name equals a local directory then needs its `https://huggingface.co/` URL, and the message says so.
2. **Closest `.gguf`:** the `*.gguf` (case-insensitive) in the argument's directory that shares the longest case-insensitive prefix with its base name, at least 3 characters, ties to the first in `LC_ALL=C` sort order. For a directory argument it is the first `.gguf` in it. With none, the message says so. The same hint is added to the existing "not found" message of a missing `.gguf`.
3. **Bypass:** G5 and G-Q3 count as a no. The run announces the comparison and keeps the baseline: G5 runs step 9's no path (rollback copy), and G-Q3 keeps `<a>`. A model switch stays the owner's call. Stated once, in `SKILL.md`.
4. **Row tie-break:** with no model argument, follow each matching row in table order and load every file they name (no row's Do NOT load cell bans a file another matching row loads).
5. **A `file://` entry in the quant route:** a new first step P0 reads the entry url. When it is not a Hugging Face resolve url, ask the owner for the repo. Under bypass, or when the owner names no repo, report that the comparison needs a Hugging Face entry and stop. Given a repo, the url is pinned (new Gate G-Q0, a live config edit after a backup) only when `pick.bytes` equals `size_bytes` and `pick.sha256` equals the local file's `sha256sum`. `quant-headroom.sh` is unchanged.
6. **Quant order:** P0 url, P1 interference, P2 depth, then Q1 `check`; P3 seeds (the 2.5 h ladder lane) only when a candidate has `fits: true`. The old P1/P2/P3 are renumbered (old P2 -> P1, old P3 -> P2, old P1 -> P3), so the labels follow the order.
7. **Ladder:** the highest layer N such that every layer from the lowest layer run through N has a `ladder_at` tier; 0 when the lowest layer run has none or no layer ran. A layer that was not run ends the climb. The report lists the layers run, from `lowest_effort`'s keys; the card gets no new field.
8. **Live card:** after Task 5, re-score `tmp/h2h/q4/effort/results.jsonl` and rewrite the `ornith-15-9b` ladder section with `make model-capacity`. The old section stays reproducible from `tmp/h2h/q4/score.json`, which is kept. The card is current (fingerprint `5edd6b54f64c1e70`; only `quants` is stale, checked read-only).
9. **Description:** our own shorter text (876 characters). It keeps "a model name", "(Vulkan/ROCm)" and the Spanish trigger.

## Review Focus

1. A Hugging Face repo whose org equals a directory in the current folder (`tools/foo`, `tests/x`): it now exits 2. Expected: the message tells the owner to pass the `https://huggingface.co/` URL, and the URL works. Pinned by the message assertion in Task 2, Step 1 (`"https://huggingface.co/" in result.stderr` for path-like cases).
2. A directory argument holding several GGUFs: the hint names the first in sort order. Expected: the agent reports the message and asks which file was meant, never picks one. Text only: SKILL.md says exit 2 means "report its message and stop".
3. A head-to-head between a layers 2-5 lane and a layers 1-5 lane: the task lists differ, so `compare-runs.sh` exits 3 (not a head-to-head) before any ladder delta is read. Already pinned by the challenge-set tests. Task 5 adds no new path.
4. A G5 "no" under bypass on a gateway-only host: step 9 restores the rollback copy (which holds `llm_server.enabled: false`) and stops llm-server. Expected: the host ends gateway-only, with the comparison reported. Text only (step 9 already covers a declined G5).
5. An external reader of `ladder.highest_layer` (the pending lite-executor-routing plan reads it as the highest layer passed): a layers 2-5 run now reads 5 instead of 0. Expected: correct, because L2-L5 passed. Covered by the score.sh test of Task 5. Full runs (1-5) give the same value as before.

---

### Task 1: Onboard flow selects the candidate before validating it

**Files:**
- Modify: `.claude/skills/onboard-local-model/references/onboard-flow.md:3-4` (header), `:8-9` (`$S` and `<models_dir>`), `:35-49` (H1-H5, H5 duplicate rule), `:90-91` (Steps 4 order)
- Modify: `.claude/skills/onboard-local-model/evals/evals.json` (`hf-repo-argument`: one assertion)
- Test: `tests/skills/onboard-local-model/test_onboard_local_model_scripts.py` (new test after `test_route_table_names_existing_sections_and_steps`)

**Interfaces:**
- Produces: `<models_dir>` (defined in Candidate by kind); sub-steps `H1.`-`H5.`; Steps 4 order `models select <alias>`, then `validate-gguf`, then `budget`.

- [ ] **Step 1: Write the failing test**

```python
def test_onboard_step_4_selects_the_candidate_before_validating_it() -> None:
    flow = _md(REFS / "onboard-flow.md")
    step4 = _section(flow, "4. **Fit and configure.**", "5. **Baseline lane")
    marks = [step4.find(m) for m in ("models select <alias>", "validate-gguf --models-dir <models_dir>", "budget --models-dir <models_dir>")]
    assert -1 not in marks and marks == sorted(marks), marks
    assert "<dir>" not in flow and re.search(r"^H5\. ", (REFS / "onboard-flow.md").read_text(), re.MULTILINE)
    h5 = _section(flow, "H5. ", "### Local GGUF")
    assert "add nothing" in h5 and "(step 2)" not in h5 and "step 4's" not in h5
    assert all(s in _asserts("hf-repo-argument") for s in ("models select foo-9b", "before validate-gguf"))
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/skills/onboard-local-model/test_onboard_local_model_scripts.py -q -k step_4_selects`
Expected: FAIL on the order (validate-gguf comes first today).

- [ ] **Step 3: Edit onboard-flow.md and the eval**

- Header (:3-4): append "Read only your kind's subsection of Candidate by kind (and Alias for a new entry when the entry is new), then Steps."
- :8-9: after the `$S` sentence, add "`<models_dir>` is `~/llm-workspace/models` (`LLM_ENV_MODELS_DIR`), except where Local GGUF names the file's own directory."
- Hugging Face list: number the items `H1.`-`H5.` (same text). In H5, "(step 2)" becomes "(Steps 2)" and "step 4's `models select`" becomes "Steps 4's `models select`". After "...to `$S` with `yq -i`, `enabled: false`." add: "When rule 1 applies (a live entry has this `file`), add nothing: that alias is the candidate, and Steps 4 selects it."
- Steps 4 (:90-91), replace "Then on `$S`: `uv run llmenv.py --config $S validate-gguf --models-dir <dir>`, `... --config $S models select <alias>` and `... --config $S budget --models-dir <dir>`" with: "Then on `$S`: `uv run llmenv.py --config $S models select <alias>` (the alias of step 3, `candidate` for a Local GGUF; on the Hugging Face route it switches `$S` from the baseline to the new entry), then `... --config $S validate-gguf --models-dir <models_dir>` and `... --config $S budget --models-dir <models_dir>`". The rest of the sentence ("(it exits 1 when infeasible but still prints JSON)...") stays.
- evals.json `hf-repo-argument`: insert before "Nothing was downloaded...": "On the scratch config, `models select foo-9b` runs before validate-gguf and budget (both with --models-dir ~/llm-workspace/models after G1), so they check the candidate file, not gemma4's". The alias is `foo-9b` by Alias for a new entry rule 2. `test_eval_hf_file_fixtures_match_their_assertions` already proves no live entry uses that alias or file.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/skills/onboard-local-model/test_onboard_local_model_scripts.py -q -k "step_4_selects or route_table or eval_"`
Expected: PASS.

- [ ] **Step 5: Validate (no commit: untracked tree)**

Run: `make validate && make test`. Expected: `All checks passed.` and the suite green.

### Task 2: `resolve-target.sh` sends path-like arguments to exit 2 with the closest `.gguf`

**Files:**
- Modify: `.claude/skills/onboard-local-model/scripts/resolve-target.sh:4-8` (header), `:83-87` (the fallback block)
- Modify: `.claude/skills/onboard-local-model/evals/evals.json` (append `mistyped-gguf-path`)
- Test: `tests/skills/onboard-local-model/test_onboard_local_model_scripts.py` (`test_resolve_target_exits_3_only_for_a_bare_model_name` :1620, a new eval test after `test_eval_missing_gguf_fixture_matches_its_assertions`)

**Interfaces:**
- Produces: exit 2, empty stdout, stderr `<MODEL>: not a .gguf file (did you mean <path>?); pass a Hugging Face repo as its https://huggingface.co/ URL`, or `(no similar .gguf in <dir>)` in place of the hint. A missing `.gguf` keeps `<MODEL>: not found (a .gguf argument must be an existing file)` and gains `; did you mean <path>?` inside the parentheses when a hint exists. `<path>` is the argument's directory as typed (the argument itself for a directory, trailing `/` dropped; `~` expanded) plus `/` plus the file name. Exit 3 stays only for a bare model name.
- Helper inside the script: `closest_gguf DIR NAME` prints that path or nothing (owner decision 2).

- [ ] **Step 1: Write the failing tests** (replace the parametrized test at :1620)

```python
@pytest.mark.parametrize(
    ("model", "code", "hint"),
    [("Qwen3.8-27B", 3, None), ("Qwen 3.8 27B", 3, None), ("nope.gguf", 2, None), ("Missing-7B.GGUF", 2, None),
     ("https://example.com/o/r", 2, None), ("https://huggingface.co/datasets/o/r", 2, None),
     ("evals/files/Foo-9B-Q4_K_M.ggu", 2, "evals/files/Foo-9B-Q4_K_M.gguf"),
     ("evals/files/Foo-9B-Q4_K_S.gguf", 2, "evals/files/Foo-9B-Q4_K_M.gguf"),
     ("{tmp}/evals/files/Foo-9B-Q4_K_M", 2, "{tmp}/evals/files/Foo-9B-Q4_K_M.gguf"),
     ("evals/files/", 2, "evals/files/Foo-9B-Q4_K_M.gguf"),
     ("evals/Foo-9B-Q4_K_M", 2, None), ("Foo-9B-Q4_K_M.ggu", 2, None), ("./Foo-9B", 2, None),
     ("~/no-such-dir-r8/Foo-9B", 2, None)],
)
def test_resolve_target_exits_3_only_for_a_bare_model_name(tmp_path: Path, model: str, code: int, hint: str | None) -> None:
    gguf = tmp_path / "evals" / "files" / "Foo-9B-Q4_K_M.gguf"
    gguf.parent.mkdir(parents=True)
    gguf.write_bytes(GGUF_BYTES)
    result = _rt(tmp_path, model.format(tmp=tmp_path))
    assert (result.returncode, result.stdout) == (code, ""), result.stderr
    assert ("search it as a model name" in result.stderr) == (code == 3)
    if hint:
        assert f"did you mean {hint.format(tmp=tmp_path)}?" in result.stderr
    if code == 2 and not model.lower().endswith(".gguf") and "://" not in model:
        assert "not a .gguf file" in result.stderr and "https://huggingface.co/" in result.stderr


def test_eval_mistyped_path_fixture_matches_its_assertions() -> None:
    path = ".claude/skills/onboard-local-model/evals/files/Foo-9B-Q4_K_M.ggu"
    env = {**os.environ, "LLM_ENV_CONFIG": str(FIX / "models.yml"), "LLM_ENV_MODELS_DIR": str(FIX / "models")}
    result = _run("bash", SCRIPTS / "resolve-target.sh", path, env=env, cwd=ROOT)
    want = f"not a .gguf file (did you mean {path}f?)"
    assert (result.returncode, result.stdout) == (2, "") and want in result.stderr
    assert all(s in _asserts("mistyped-gguf-path") for s in ("exits 2", want, "hf-scout.sh --query is not run"))
```

`test_resolve_target_reads_an_alias_before_a_repo_id` (`org/other-GGUF` -> `hf_repo`, `lab/x` -> alias) and `test_resolve_target_checks_a_local_gguf_file` stay unchanged and must still pass.

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/skills/onboard-local-model/test_onboard_local_model_scripts.py -q -k "exits_3_only or mistyped_path"`
Expected: FAIL. The path cases exit 3 or 0, and the eval is missing.

- [ ] **Step 3: Implement**

- In the `[ -z "$kind" ]` block (:83), keep the order: (a) a `.gguf` argument -> the existing "not found" message, plus the hint; (b) new: a path-like argument (owner decision 1) -> exit 2 with the Interfaces message; (c) `ORG/NAME` -> `hf_repo`; (d) exit 3. Path-like is tested after the alias lookup, so an alias such as `lab/x` still wins.
- `closest_gguf DIR NAME`: owner decision 2. Lowercase both sides for the prefix comparison only. The printed path keeps the file's own case.
- Header (:4-8): after "(4) any other .gguf argument -> exit 2 "not found" (never a repo id)", insert "(4b) a path-like argument (starts with /, ./, ../ or ~, holds two or more /, exists, has an existing directory as its first segment, or ends in a .gg* extension) -> exit 2 "not a .gguf file" with the closest .gguf of its directory;". In the sentence after (6) ("Every other failure (...) exits 2"), add "a path-like argument" to the parenthesised list.
- evals.json, append:

```json
{
  "id": "mistyped-gguf-path",
  "prompt": "can you hook up .claude/skills/onboard-local-model/evals/files/Foo-9B-Q4_K_M.ggu as my local model? Just tell me what you would do; change nothing.",
  "expected_output": "resolve-target.sh exits 2 because the path is not a .gguf file, and its message suggests .claude/skills/onboard-local-model/evals/files/Foo-9B-Q4_K_M.gguf; the answer reports that, asks whether that file was meant and stops, with no Hugging Face search.",
  "assertions": [
    "resolve-target.sh .claude/skills/onboard-local-model/evals/files/Foo-9B-Q4_K_M.ggu exits 2 with not a .gguf file (did you mean .claude/skills/onboard-local-model/evals/files/Foo-9B-Q4_K_M.gguf?)",
    "Exit 2 is treated as an error: the answer reports the message, asks whether Foo-9B-Q4_K_M.gguf was meant and stops; hf-scout.sh --query is not run",
    "No scratch config, download, gate, container or configuration change was made"
  ]
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/skills/onboard-local-model/test_onboard_local_model_scripts.py -q -k "resolve_target or eval_ or help or missing_required"`
Expected: PASS.

- [ ] **Step 5: Validate (no commit)**

Run: `make validate && make test`. Expected: shellcheck clean, suite green.

### Task 3: `SKILL.md` router: tie-break, one bypass rule, definitions (byte-measured)

**Files:**
- Modify: `.claude/skills/onboard-local-model/SKILL.md:3,8,31,34,76,80,89-91,102`
- Modify (counterparts): `.claude/skills/onboard-local-model/references/tuning.md:13` and `:164`
- Test: `tests/skills/onboard-local-model/test_onboard_local_model_scripts.py` (`STATED_ONCE` and `RETIRED_COPIES` :946-948, `test_route_table_names_existing_sections_and_steps` :969)

**Interfaces:**
- Consumes: Task 2's exit-2 semantics ("a bad path").
- Produces: Gate name `G-Q0` (SKILL.md:76, used by Task 4); `<run>` and `<stamp>` defined on SKILL.md:8; the bypass rule for G5 and G-Q3 stated only in SKILL.md.

These edits were measured on a scratch copy: 10,973 -> 10,906 bytes, description 983 -> 876 characters.

- [ ] **Step 1: Write the failing guards**

- `STATED_ONCE` gains `"follow each matching row in table order": "SKILL.md"` and `"G5 and G-Q3 count as a no": "SKILL.md"`.
- `RETIRED_COPIES` gains `"a quant switch is a change of model"` and `"under bypass, announce the facts and proceed"`.
- In `test_route_table_names_existing_sections_and_steps`, the heading check becomes exact, with a self-check:

```python
    def heading(section: str) -> str:
        return rf"^#+ {re.escape(section)}(?: \([^)]*\))?[ \t]*$"

    assert not re.search(heading("Alias"), "### Alias for a new entry", re.MULTILINE)
    for name, section in pairs:
        assert re.search(heading(section), (REFS / f"{name}.md").read_text(), re.MULTILINE), (name, section)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/skills/onboard-local-model/test_onboard_local_model_scripts.py -q -k "stated_once or route_table"`
Expected: FAIL in `test_shared_rules_are_stated_once` (new phrases missing, retired ones present in tuning.md). `route_table` passes: it is a coverage guard.

- [ ] **Step 3: Edit SKILL.md (exact replacements; reflow to about 128 columns)**

- :3 description, whole value:

```text
Find, fit, benchmark and onboard a local agentic-coding GGUF for this repo's llama.cpp router beside System One (Laya). Use when the user names a models.yml alias, a Hugging Face repo or URL, a .gguf file or a model name to add, try or swap; asks which local model fits the VRAM or beats the current one; compares quants (Q4_K_M/Q5_K_M/Q6_K) or asks if a higher one fits; wants pp/tg tok/s at depth, ctx_size, KV or n_cpu_moe tuned, the capacity card or my-coding-lite; scores the local model against Haiku, Luna or Composer; asks if the llama.cpp image (Vulkan/ROCm) or Laya is outdated; or wants one ladder challenge generated or verified, even as "a faster model for pi" or "modelo agéntico que quepa en mi GPU". Not for OmniRoute or other combos, container memory limits, gpu-status triage, asking Laya for a judgment, cloud providers, sampling, fine-tuning or quantizing.
```

- :8: "artifacts go under `tmp/model-scout/<YYYYMMDD>-<alias>/`." becomes "artifacts go under `tmp/`; `<run>` is `<YYYYMMDD>-<alias>`, `<stamp>` is `date +%Y%m%dT%H%M%S`."
- :31: "(a missing `.gguf` path, a non-model URL, usage)" becomes "(a bad path, a non-model URL, usage)".
- :34: append "With no model argument, follow each matching row in table order and load every file they name."
- :76: "G-Q1 to G-Q3" becomes "G-Q0 to G-Q3".
- :80: "When the session runs with permissions bypassed, a gate becomes announce the facts, then proceed." becomes "With permissions bypassed, a gate becomes announce the facts, then proceed; G5 and G-Q3 count as a no (keep the baseline: a model switch is the owner's call)."
- :89-91, whole item: "- NEVER free the GPU with `make stop` or `make restart`: they take OmniRoute, System One and remote-setup down with llm-server. Only `make stop-llama`, after G2 or G-T2."
- :102: "sampler or agent process running" becomes "sampler or agent running".
- tuning.md:13: "the gates are those of SKILL.md "Gates" (under bypass, announce the facts and proceed)." becomes "the gates and the bypass rule are those of `SKILL.md` Gates."
- tuning.md:164: delete "; under bypass announce it and keep `<a>`: a quant switch is a change of model" (the sentence ends after the comparison table's parenthesis).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/skills/onboard-local-model/test_onboard_local_model_scripts.py -q -k "stated_once or route_table or router or bypassed or description or black_box or never_point or shell_logic"`, then `wc -c .claude/skills/onboard-local-model/SKILL.md`
Expected: PASS; at most 11,000 bytes (about 10,906).

- [ ] **Step 5: Validate (no commit)**

Run: `make validate && make test`. Expected: green.

### Task 4: Quant comparison: url check first, headroom before the ladder lane

**Files:**
- Modify: `.claude/skills/onboard-local-model/references/tuning.md:19,25` (`<dir>`), `:100-101`, `:105-126` (Standalone names, prerequisites, order), `:143` (Q4)
- Create: `.claude/skills/onboard-local-model/evals/files/models-local-url.yml`
- Modify: `.claude/skills/onboard-local-model/evals/evals.json` (`quant-head-to-head`; append `quant-room-local-entry`)
- Test: `tests/skills/onboard-local-model/test_onboard_local_model_scripts.py` (`test_eval_quant_fixtures_match_their_assertions` :1787; three new tests)

**Interfaces:**
- Consumes: `G-Q0` from Task 3.
- Produces: steps `P0` (url), `P1` (interference), `P2` (depth), `Q1`, `P3` (seeds, only when a candidate fits). Later text and evals use these labels.

- [ ] **Step 1: Write the failing tests**

```python
def test_quant_comparison_checks_headroom_before_the_ladder_lane() -> None:
    text = _section(_md(REFS / "tuning.md"), "### Standalone quant comparison", "- Q1.")
    marks = [text.find(m) for m in ("P0. The entry url", "P1. A passed interference gate", "P2. A current depth section",
                                     "After Q1, only when a candidate has `fits: true`", "P3. `<seeds>`")]
    assert -1 not in marks and marks == sorted(marks), marks
    assert "<dir>" not in _md(REFS / "tuning.md") and "G-Q0" in text


def test_quant_headroom_check_refuses_an_entry_without_a_hugging_face_url(tmp_path: Path) -> None:
    """Coverage: the message P0 quotes (quant-headroom.sh is unchanged)."""
    cfg = json.loads(json.dumps(QH_CONFIG))
    cfg["models"][0]["url"] = "file:///x/m-Q4_K_M.gguf"
    result = _qh(_qh_env(tmp_path, config=cfg), "check", "--alias", "m", "--vram-available-mib", "8000")
    assert result.returncode == 1 and "entry url is not a Hugging Face resolve url: file:///x/m-Q4_K_M.gguf" in result.stderr
    assert not list((tmp_path / "out").glob("model-tune/quant-*/candidates.json"))


def test_eval_local_url_fixture_matches_its_assertions() -> None:
    url = "file:///var/home/bazzite/Downloads/gemma4-v2-Q4_K_M.gguf"
    base = yaml.safe_load((FIX / "models.yml").read_text())
    next(m for m in base["models"] if m["alias"] == "gemma4")["url"] = url
    assert yaml.safe_load((FIX / "models-local-url.yml").read_text()) == base
    assert all(s in _asserts("quant-room-local-entry") for s in (url, "entry url is not a Hugging Face resolve url", "G-Q0"))
```

In `test_eval_quant_fixtures_match_their_assertions`, add `"P0"` and `"no ladder lane has run"` to the checked strings, plus `assert re.fullmatch(r"https://huggingface\.co/[^/]+/[^/]+/resolve/[0-9a-f]{40}/.+", gemma["url"])`.

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/skills/onboard-local-model/test_onboard_local_model_scripts.py -q -k "headroom_before or without_a_hugging_face or local_url or eval_quant_fixtures"`
Expected: FAIL, except the coverage test, which passes.

- [ ] **Step 3: Edit tuning.md, the fixture and the evals**

- :19 and :25: `<dir>` becomes `tmp/model-tune/<stamp>`.
- :101: "else pin the url first." becomes "else pin the url first (P0)."
- :105-109 (Names): "one stamp chosen before P3 or Q1" becomes "one stamp chosen before P2 or Q1"; "Gates: G-T2," becomes "Gates: G-Q0 before a url pin, G-T2,".
- :111-122, replace the prerequisites block with:

```text
Before Q1, in this order, each only when missing (a tuning run in this session leaves P1 and P2):
- P0. The entry url: `yq '.models[] | select(.alias == "<a>") | .url' ~/.config/llm-env/models.yml`. `check` reads only a
  `https://huggingface.co/<org>/<repo>/resolve/<rev>/<file>` url; any other (a `file://` url from a local GGUF) makes it exit 1
  with "entry url is not a Hugging Face resolve url". Then ask the owner for the repo; under bypass, or with no repo named,
  report that the comparison needs a Hugging Face entry and stop. With a repo: `bash $SK/scripts/hf-scout.sh --repo <repo>
  --file <entry file> --json --budget-mib <rule 1 budget> --ram-mib <rule 1 RAM>` (figures from `bash $SK/scripts/preflight.sh`).
  When `pick.file` equals the entry's `file` (a top-level file), `pick.bytes` equals its `size_bytes` and `pick.sha256` equals
  `sha256sum ~/llm-workspace/models/<entry file>`: Gate G-Q0 (live config edit), a `cp -p` backup, then `yq -i` the url to
  `https://huggingface.co/<repo>/resolve/<sha>/<entry file>` (`<sha>` is the row's `sha`). Else report the differing values and stop.
- P1. <old P2 text, unchanged>
- P2. <old P3 text, unchanged; it ends "llm-server stays stopped for Q1.">
After Q1, only when a candidate has `fits: true`, before its Q2:
- P3. `<seeds>`: <old P1 text, with "Else, while `<a>` is served, run T8's ladder command" becoming "Else `make start-llama`,
  then, while `<a>` is served, T8's ladder command">. When P3 ran, Q4 frees the GPU again under its own Gate G-T2.
T2 runs only when `llm_server.enabled` is false; T4-T6 (sweep, apply and restart) only when the owner also asks for tuning.
```

- :125-126: "Order: the prerequisites; Q1; then Q2-Q11 for each candidate" becomes "Order: P0-P2; Q1; P3 when a candidate fits; then Q2-Q11 for each candidate".
- Q4 (:143): "(`SKILL.md`, Freeing the GPU; already free after P3 or Q1's G-T2)" becomes "(`SKILL.md`, Freeing the GPU; already free after P2 or Q1's G-T2, unless P3 served `<a>` again)".
- Fixture: `yq '(.models[] | select(.alias == "gemma4") | .url) = "file:///var/home/bazzite/Downloads/gemma4-v2-Q4_K_M.gguf"' evals/files/models.yml > evals/files/models-local-url.yml` (run from the skill directory).
- evals.json `quant-head-to-head`:
  - expected_output becomes: "capacity show finds no card for gemma4, so P0 (gemma4's url is a pinned Hugging Face resolve url), P1 interference, and P2 G-T2 plus bench-fit.sh at 0,32768,130560 with make model-capacity DEPTH run before Q1. Q1 runs check without the flag while llm-server is stopped. The gemma4 ladder lane (P3) runs only if a candidate fits. G-Q1 and G-Q2 are separate gates. Both quants run on one seed set through the router profile. The upper bound is stated. No fitting candidate means the plan ends with make start-llama. G-Q3 is the owner's call. Q11 restores gemma4 and builds my-coding-lite only if it existed before."
  - Assertion 2 becomes: "uv run llmenv.py --config <fixture> capacity show --alias gemma4 reports no capacity card, so before Q1 the steps run in order: P0 finds gemma4's url pinned to a huggingface.co resolve/<sha> url, P1 make model-interference while served, P2 G-T2, then bench-fit.sh --alias gemma4 --depths 0,32768,130560 and make model-capacity MODEL=gemma4 DEPTH=<bench json>; tune-sweep.sh and tune-apply.sh are not run".
  - Assertion 3 becomes: "After P2 llm-server stays stopped, so Q1 runs quant-headroom.sh check without --vram-available-mib, before any ladder lane".
  - Insert after it: "The gemma4 ladder lane (P3, which writes seeds.json) runs only when a candidate has fits true, after make start-llama; Q4 then asks G-T2 again before make llama-enable".
  - In assertion 5, "the P1 seeds.json" becomes "the P3 seeds.json".
  - Assertion 8 becomes: "When no candidate has fits true, the plan ends with make start-llama, because P2 stopped llm-server, and no ladder lane has run".
- evals.json, append:

```json
{
  "id": "quant-room-local-entry",
  "prompt": "is there room for a Q5 of my local model? I added it from a file in my Downloads folder. (Eval setup: the config is .claude/skills/onboard-local-model/evals/files/models-local-url.yml; pass it as LLM_ENV_CONFIG or --config; llm-server serves its gemma4. Change nothing.)",
  "files": ["evals/files/models-local-url.yml"],
  "expected_output": "The standalone quant comparison starts with P0: gemma4's url is file://, not a Hugging Face resolve url, so quant-headroom.sh check would exit 1. No GPU step runs. The answer asks for the Hugging Face repo, to pin the url under G-Q0 after the size and sha256 checks, or reports that the comparison needs a Hugging Face entry.",
  "assertions": [
    "The route is tuning.md's Standalone quant comparison, and its first step reads gemma4's url: file:///var/home/bazzite/Downloads/gemma4-v2-Q4_K_M.gguf",
    "The answer says quant-headroom.sh check cannot read a tree for that url (it exits 1 with entry url is not a Hugging Face resolve url)",
    "No interference gate, bench-fit.sh run, ladder lane, download or config edit is started or proposed before a Hugging Face url is pinned",
    "The answer asks for the Hugging Face repo and names Gate G-Q0 for the pin after the size and sha256 checks, or reports that the comparison needs a Hugging Face entry",
    "Nothing was downloaded, stopped, started or edited"
  ]
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/skills/onboard-local-model/test_onboard_local_model_scripts.py -q -k "quant or eval_ or stated_once or shell_logic or never_point"`
Expected: PASS.

- [ ] **Step 5: Validate (no commit)**

Run: `make validate && make test`. Expected: green.

### Task 5: The ladder counts from the lowest layer run (tracked report fix)

**Files:**
- Modify: `.claude/skills/onboard-local-model/scripts/score.sh:23` (header), `:83` (`ladder`)
- Modify: `.claude/skills/onboard-local-model/references/scoring.md:51` (reading item 3)
- Modify: `pylib/capacity_report.py:145` (`_ladder`)
- Test: `tests/skills/onboard-local-model/test_onboard_local_model_scripts.py` (:387 test renamed, two new tests); `tests/test_capacity.py` (`full_card` :571, assertion :614, one new test)

**Interfaces:**
- Produces: `score.sh` `ladder` per owner decision 7 (integer, never null). `compare-runs.sh` is unchanged and inherits the fix. The card keeps `highest_layer` and `lowest_effort` as they are (`pylib/capacity.py:153` unchanged). Report line: `Highest layer passed: <N | none | not measured> (layers run: <comma list of lowest_effort keys in numeric order | not measured>); pass rate ...`.

- [ ] **Step 1: Write the failing tests**

Skill suite. Replace `test_score_ladder_is_zero_without_layer_one_and_derives_layers_from_task_names` (:387) with:

```python
def test_score_ladder_derives_layers_from_task_names(tmp_path: Path) -> None:
    out = _score(tmp_path, [_row("l2-b", "low", 3, 3)])  # no `layer` field: taken from the task name
    assert out["ladder"] == 2 and out["ladder_at"] == {"2": "low"}
    legacy = _score(tmp_path, [_row("lru-cache", "low", 3, 3)])  # no layer at all: no layer rows
    assert legacy["by_layer"] == [] and legacy["ladder"] == 0 and legacy["overall"] == 100


def test_score_ladder_counts_from_the_lowest_layer_run(tmp_path: Path) -> None:
    out = _score(tmp_path, [_row(f"l{n}-s1", "medium", 3, 3, layer=n) for n in (2, 3, 4, 5)])
    assert out["ladder"] == 5 and out["ladder_at"] == {"2": "medium", "3": "medium", "4": "medium", "5": "medium"}
    assert _score(tmp_path, [_row("l2-s1", "low", 3, 3, layer=2), _row("l4-s1", "low", 3, 3, layer=4)])["ladder"] == 2
    assert _score(tmp_path, [_row("l2-s1", "low", 0, 3, layer=2), _row("l3-s1", "low", 3, 3, layer=3)])["ladder"] == 0


def test_compare_runs_reports_the_ladder_of_a_partial_layer_run(tmp_path: Path) -> None:
    rows = [_row(f"l{n}-s1", "medium", 3, 3, layer=n) for n in (2, 3, 4, 5)]
    out = json.loads(_compare(_lane(tmp_path, "a.jsonl", rows), _lane(tmp_path, "b.jsonl", rows)).stdout)
    assert [r["ladder"] for r in out["runs"]] == [5, 5] and out["delta_vs_first"][1]["ladder"] == 0
```

Tracked suite (`tests/test_capacity.py`). In `full_card`, `"ladder": 3` becomes `"ladder": 1`, which matches its `ladder_at` `{"1": "low", "2": None}`. At :614, `"Highest layer passed: 3 of 5; pass rate 50.0 %"` becomes `"Highest layer passed: 1 (layers run: 1, 2); pass rate 50.0 %"`. Add:

```python
def test_report_lists_the_layers_the_ladder_ran(env):
    from pylib import capacity_report

    score = {"ladder": 5, "ladder_at": {"2": "medium", "3": "medium", "4": "medium", "5": "medium"}, "pass_rate": 0.85,
             "overall": 93.3, "tool_use": {"calls": 288, "errors": 13, "error_rate": 0.045}, "invalid_runs": 0}
    page = capacity_report.render(write(env, score=score), 300.0)
    assert "Highest layer passed: 5 (layers run: 2, 3, 4, 5); pass rate 85.0 %" in page
    none = dict(score, ladder=0, ladder_at={"2": None, "3": "low"})
    assert "Highest layer passed: none (layers run: 2, 3); pass rate 85.0 %" in capacity_report.render(write(env, score=none), 300.0)
```

- [ ] **Step 2: Run them to verify they fail**

Run, one after the other: `uv run pytest tests/skills/onboard-local-model/test_onboard_local_model_scripts.py -q -k "ladder or partial_layer"`, then `uv run pytest tests/test_capacity.py -q -k "report"`
Expected: FAIL. The ladder is 0 for the 2-5 runs, and the report prints "of 5".

- [ ] **Step 3: Implement**

- score.sh:83: `ladder` per owner decision 7. Take the lowest layer from `$at`'s keys (numbers), then the last N from there up whose layers from the lowest through N all have a non-null `$at` entry; 0 when `$at` is empty or its lowest layer is null.
- score.sh:23 header: "ladder = the highest layer N such that ladder_at is not null for every layer from the lowest layer run (ladder_at's keys) through N; 0 when the lowest layer run has none or no layer ran (a layer not run ends the climb)."
- scoring.md:51: "`ladder` is the highest layer N where every layer 1..N has a `ladder_at` tier" becomes "`ladder` is the highest layer N where every layer from the lowest one run (the keys of `ladder_at`) through N has a `ladder_at` tier (0: the lowest layer run has none)".
- capacity_report.py `_ladder`: build `layers` and the highest-layer text per Interfaces (`0` -> `none`, `None` -> `NM`). The rest of the line is unchanged.

- [ ] **Step 4: Run the tests to verify they pass**

Run, one after the other: `uv run pytest tests/skills/onboard-local-model/test_onboard_local_model_scripts.py -q -k "score or compare or quant_switch"`, then `uv run pytest tests/test_capacity.py -q`
Expected: PASS. `test_eval_quant_switch_fixtures_match_their_assertions` still sees ladder delta 1.

- [ ] **Step 5: Validate and commit the tracked part**

Run: `make validate && make test`. Expected: green.

```bash
git add pylib/capacity_report.py tests/test_capacity.py
git commit -m "fix(capacity): report the layers a ladder ran instead of 'of 5'"
```

- [ ] **Step 6: Refresh the live card (owner decision 8)**

Run: `bash .claude/skills/onboard-local-model/scripts/score.sh tmp/h2h/q4/effort/results.jsonl > tmp/h2h/q4/score-r8.json`, then `jq '{ladder, ladder_at, pass_rate}' tmp/h2h/q4/score-r8.json`
Expected: `ladder` 5, `ladder_at` 2-5 all `medium`, `pass_rate` 0.85.
Run: `make model-capacity MODEL=ornith-15-9b SCORE=tmp/h2h/q4/score-r8.json`, then `make model-report MODEL=ornith-15-9b`
Expected: "Highest layer passed: 5 (layers run: 2, 3, 4, 5); pass rate 85.0 %". `tmp/h2h/q4/score.json` stays as it is.

### Task 6: Small lows (coverage and wording)

**Files:**
- Modify: `.claude/skills/onboard-local-model/references/selection-criteria.md:18` (deep-research), `:27` (`<dir>`)
- Modify: `.claude/skills/onboard-local-model/references/runtimes.md:47` (`-ub` default)
- Test: `tests/skills/onboard-local-model/test_onboard_local_model_scripts.py` (:1694, one new test after `test_rank_candidates_breaks_ties_by_updated_then_downloads`)

- [ ] **Step 1: Write the coverage test** (it passes when written: it pins `top=6`, which no test reached)

```python
def test_rank_candidates_shortlists_six_rows_by_default(tmp_path: Path) -> None:
    out = json.loads(_rank(tmp_path, [[_cand(f"r{n}", downloads=n) for n in range(7)]]).stdout)
    assert [r["id"] for r in out["shortlist"]] == ["r6", "r5", "r4", "r3", "r2", "r1"]
```

At :1694, drop the vacuous `len(short["shortlist"]) <= 6 and`. Keep the text check, with the comment `# the default cap is pinned by test_rank_candidates_shortlists_six_rows_by_default`.

- [ ] **Step 2: Edit the references**

- selection-criteria.md:18: "When the session lists a deep-research style skill" becomes "When the session lists a `deep-research` skill".
- selection-criteria.md:27: `--evidence <dir>` becomes `--evidence tmp/model-scout/<run>/evidence`.
- runtimes.md:47: "- `-ub` (ubatch) 512 → 1024 / 2048:" becomes "- `-ub` (ubatch) 512 → 1024 / 2048 (the first A/B when the owner names no flag):".

- [ ] **Step 3: Run and validate (no commit)**

Run: `uv run pytest tests/skills/onboard-local-model/test_onboard_local_model_scripts.py -q -k "rank_candidates or eval_scout or black_box or never_point"`, then `make validate && make test`
Expected: PASS and green.

### Task 7: Verification and the plans index row

**Files:**
- Modify: `docs/superpowers/plans/README.md` (this plan's row)

- [ ] **Step 1: Guards and suites, one file at a time**

Run, one after the other:
- `uv run pytest tests/skills/onboard-local-model/test_onboard_local_model_scripts.py -q`
- `uv run pytest tests/skills/onboard-local-model/test_challenges.py -q`
- `uv run pytest tests/skills/onboard-local-model/test_latency_profile.py -q`
- `uv run pytest tests/test_capacity.py -q`
- `make validate && make test`
Expected: all green. The guard set is `-k "black_box or stated_once or never_point or route_table or router or shell_logic"` in the first file.

- [ ] **Step 2: Byte cap and description**

Run: `wc -c .claude/skills/onboard-local-model/SKILL.md`. Expected: <= 11,000 (about 10,906). `test_skill_description_fits_the_spec_limit` passes (876).

- [ ] **Step 3: Read-only route walk with the fixtures**

From the repo root, with `SK=.claude/skills/onboard-local-model F=$SK/evals/files`, `LLM_ENV_CONFIG=$F/models.yml` and `LLM_ENV_MODELS_DIR=$F/models`. Pass `HF_FIXTURE_DIR` to hf-scout.sh:
1. Model given, alias: `bash $SK/scripts/resolve-target.sh gemma4` gives `kind alias`, `enabled true`, `present true`.
2. Model given, Hugging Face repo: `resolve-target.sh https://huggingface.co/lab/Foo-9B-GGUF` gives `hf_repo`. `HF_FIXTURE_DIR=$F/hf hf-scout.sh --repo lab/Foo-9B-GGUF --json --budget-mib 8321 --ram-mib 12000` picks `Foo-9B-Q5_K_M.gguf`. Reading onboard-flow.md H5 and Steps 4: `models select foo-9b` comes before `validate-gguf`.
3. Hugging Face file URL: `resolve-target.sh https://huggingface.co/lab/Foo-9B-GGUF/resolve/v2/Foo-9B-Q4_K_M.gguf` gives `hf_file`, revision `v2`. `hf-scout.sh --repo lab/Foo-9B-GGUF --revision v2 --file Foo-9B-Q4_K_M.gguf --json --budget-mib 8321 --ram-mib 12000` gives sha `a1b2c3d4...`, bytes 5767168000 and sha256 `c0ffee00...`.
4. Local GGUF: `resolve-target.sh $F/Foo-9B-Q4_K_M.gguf` gives `gguf_file`, quant `Q4_K_M`.
5. Mistyped local path: `resolve-target.sh $F/Foo-9B-Q4_K_M.ggu`, `resolve-target.sh $F/` and `resolve-target.sh /var/home/bazzite/Downloads/Nope-Q4_K_M` each exit 2 with "not a .gguf file". The first two say "did you mean $F/Foo-9B-Q4_K_M.gguf?". `resolve-target.sh Zeta-Coder-7B` still exits 3.
6. No model: `HF_FIXTURE_DIR=$F/hf-empty hf-scout.sh --query Zeta-Coder-7B --json --budget-mib 8321 --ram-mib 12000` prints `[]`. `rank-candidates.sh --scout $F/scout-coder.json --scout $F/scout-agent.json --evidence $F/evidence` ranks lab-a 23 and lab-d 20.
7. Quant: `uv run llmenv.py --config $F/models.yml capacity show --alias gemma4` says no capacity card. `yq '.models[] | select(.alias == "gemma4") | .url'` on `models.yml` gives a resolve/<sha> url, and on `models-local-url.yml` a `file://` url (P0 stops there). tuning.md reads P0, P1, P2, Q1, then P3.
8. Ladder: `make model-report MODEL=ornith-15-9b` prints "Highest layer passed: 5 (layers run: 2, 3, 4, 5)".
Then `podman ps -a` lists the same containers as before the walk, and no scratch file of this run is left under `./tmp/`.

- [ ] **Step 4: Plans index row**

The row below was added with this plan, after the 2026-10-31-judge-round-7-fixes row (rows of plans G, H and I unchanged). On completion, change `PENDING` to `**DONE** (executed <date>; skill edits uncommitted with the untracked skill tree; judges re-run by the orchestrator)` and add the tracked commit id of Task 5.

```text
| 2026-10-31-judge-round-8-fixes | PENDING | fixes from skill-judge round 8 of onboard-local-model (`tmp/judges/r8-native-summary.txt` 87.2 % B with 1 high, `tmp/judges/r8-referee-summary.txt` 90.4 % A by its JSON, its high rated medium by the lead; bar: >= 90 % from both lanes, no critical or high) plus plan I's residual lows and a ladder scoring defect; findings verified against the files first (refuted and dropped ones in its ledger): onboard Steps 4 runs `models select` before `validate-gguf` and `budget` (the high); `resolve-target.sh` sends path-like arguments to exit 2 with the closest `.gguf` (red-first); quant comparison checks the entry url first (P0, Gate G-Q0 pin of a `file://` entry) and runs the 2.5 h ladder lane only when a quant fits; bypass makes G5 and G-Q3 a no, stated once; row tie-break; H1-H5, `<run>`/`<stamp>`/`<models_dir>` defined; exact-heading route guard; 6-row shortlist test; two casual evals; `score.sh` ladder counts from the lowest layer run, and the model report lists the layers run (tracked: `pylib/capacity_report.py` + `tests/test_capacity.py`, one commit); `SKILL.md` stays under 11,000 bytes (measured draft 10,906); skill edits stay uncommitted with the untracked skill tree; judges re-run by the orchestrator |
```

## Round-1 review outcome (approved; 0 critical, 0 high, 3 medium)

Review round 1 (native + referee, lead-verified; tmp/plan-j-review/review-round-1.md) left no critical or high finding; the referee's
two criticals and three highs were refuted, one high downgraded to low. Plan status: approved for execution. The implementer must apply:

- M1 (Task 4): tuning.md:130 Q1 still says "(P3 did it; else Gate G-T2)" after the renumbering. It becomes "(P2 did it; else Gate
  G-T2)". `test_quant_comparison_checks_headroom_before_the_ladder_lane` also asserts `"P3 did it" not in` the tuning.md text.
- M2 (Task 5): score.sh takes the lowest layer from ALL rows (`$all`, invalid and leak-suspect rows included; layer derived the same
  way), so a layer that ran with no valid row ends the climb (ladder 0 when it is the lowest). `ladder_at` gets a null key for such a
  layer, so the report's "layers run" (from `lowest_effort`'s keys) lists it. The header and scoring.md say "the lowest layer run (any
  row, valid or not)". `test_score_ladder_counts_from_the_lowest_layer_run` adds a case where every L1 row of the lane is an API error
  and L2-L5 are solved: `ladder` 0 and `ladder_at["1"]` null.
- M3 (Task 4): P0 names the config as `LLM_ENV_CONFIG` (default `~/.config/llm-env/models.yml`) and the models directory as
  `LLM_ENV_MODELS_DIR` (default `~/llm-workspace/models`) for the `yq` read, the G-Q0 `yq -i` target and the `sha256sum`, never a
  hardcoded path.
- L1 (Task 2): an argument with no directory part gives the bare file name in the hint (`Foo.ggu` -> `did you mean Foo.gguf?`), never
  `/Foo.gguf`. Pinned by one test case with a `.gguf` in the cwd.
- L2 (Task 3): owner decision 4's parenthetical goes into SKILL.md:34 with the tie-break sentence ("no row's Do NOT load cell bans a
  file another matching row loads"), only if SKILL.md stays <= 11,000 bytes; else the sentence ships without it and the report says so.
- L3 (Task 4): a declined G-Q0 ends the comparison with a report of the differing or unpinned url; Q1 is not run.
- L7 (Task 6): selection-criteria.md:47 `budget --models-dir <dir>` becomes `--models-dir <models_dir>` (the term onboard-flow.md
  defines; the reference names no file). Task 5 Step 6's `tmp/h2h/q4/score-r8.json` is kept: it is the card's source, not scratch.
- Not taken: L4 (backup name; SKILL.md NEVER already requires a timestamped backup), L5 (pre-existing, outside the plan), L6 (the eval
  insert keeps the plan's text).
