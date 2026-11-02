# Judge Round 12 Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the confirmed findings of round 12 of the onboard-local-model skill judges (native 88.8 % B with 5 medium and 8 low; referee 94.4 % A with its high refuted and 1 medium confirmed), and close the class of route defect behind the native lane's eight-round 87.2-89.6 % plateau, so the next round gives >= 90 % from both lanes with no critical or high finding.

**Architecture:** The native lane finds new dead ends every round because one host state is handled by one route and missed by its siblings. In r12 that happened twice:
- llm-server enabled but stopped: served-alias R1 handles it, onboarding does not;
- the named file is the served file: local-gguf L1 handles it, the Hugging Face path does not.

A file also cites a condition another file never states (onboard-flow.md:31 cites `SKILL.md` Route). The structural fix:
- **State each shared host rule once**, in a file every affected route already loads. The stopped-server rule and Gate G-R1 move into `references/scoring.md`, which every route that runs a lane loads. Served-alias, onboarding, tuning and the quant comparison cite it.
- **Name files by path**, and allow a reference to cite only the files its own route loads. A tested pair list replaces the old "never name each other" guard, so `SKILL.md` stays the only loader and one level deep still holds.
- **Split the fit math out of `selection-criteria.md`.** The onboard routes then stop loading the scout and rank sections: about 492 -> 413 reference lines.

Two scripts change, red-first:
- `resolve-target.sh` hints an early `.gguf` typo and accepts a `file://` path.
- `quant-headroom.sh fetch` never resumes onto, overwrites or deletes a file it did not download. This is a data-loss path found by the structural pass.

`SKILL.md` stays under its 11,000-byte cap: three shortenings pay for the referee's row condition (measured on a scratch copy, 10,980 -> 10,990).

**Tech Stack:** bash, jq, yq; pytest (`tests/skills/onboard-local-model/`); skill-judge rubric v4.

**Spec:** `tmp/judges/r12-native-summary.txt` and `tmp/judges/r12-referee-summary.txt` (the referee file holds the lead's check of its findings). Plan M (`docs/superpowers/plans/2026-10-31-judge-round-11-fixes.md`, executed) is the base state. Read-only planning runs on 2026-11-01 against the eval fixtures:
- `resolve-target.sh .../Fo-9B-Q4_K_M.gguf` and `file://<abs>/Foo-9B-Q4_K_M.gguf` both exit 2 with "not found" and no hint (the second file exists).
- `hf-scout.sh --repo yuxinlu1/... --revision main --file Q4_K_M/gemma4-v2-Q4_K_M.gguf --budget-mib 8321 --ram-mib 12000` gives `fit: "no"` and `pick.bytes` 7381381664 for the file the enabled gemma4 entry serves. So the served-file check must run before H3.
- `hf-scout.sh --repo lab/Foo-9B-GGUF` (same budget) gives `fit: "vram"`, pick `Foo-9B-Q5_K_M.gguf` at 6500 MiB.
- A gateway config with gemma4 left enabled resolves `gemma4` to `alias`, `enabled true`, `present true`.
- The `SKILL.md` edits of Tasks 3 and 4, applied to a scratch copy, measure 10,980 -> 10,990 bytes.

## Global Constraints

- English only. Edit only:
  - `.claude/skills/onboard-local-model/**`;
  - `tests/skills/onboard-local-model/**`;
  - one bullet in `AGENTS.md` (Task 7, committed alone);
  - this plan's row in `docs/superpowers/plans/README.md`.
- The skill tree and `tests/skills` are untracked and stay uncommitted. The README rows of plans G-M stay as they are.
- `SKILL.md` <= 11,000 bytes (`SKILL_MD_MAX_BYTES`, tested). Never raise the cap. Shorten before adding; move text into references. After Task 3: 10,908 bytes. After Task 4: 10,990.
- One level deep: `SKILL.md` alone says when to load a reference. A reference names another reference only by its path (`` `references/<name>.md` ``, plus a section), and only for the pairs in `CITES` (Task 3). A handoff to another route names the `SKILL.md` Route row, not a file.
- `jev-fast-decision` is a black box, and ai-kit is never named in the skill tree (`test_skill_and_touched_docs_treat_jev_as_a_black_box`). `onboard-flow.md` must not name `jev-fast-decision`: cite scoring's last section as "Fast classification".
- Prose holds no shell logic: a backtick span holds at most two of `;`, `&&`, `||`. Every `--out` is named once. Shared rules are stated once (`STATED_ONCE`), and retired wording stays out (`RETIRED_COPIES`).
- Every script keeps `--help` (exit 0) and its tests. A behaviour change is red-first: write the test, watch it fail, then implement. Doc and fixture tests are written first too and fail until the text lands.
- After a `.sh` edit run `make validate`. After a `.py` edit (test files included) run `make validate && make test`.
- Run pytest one file per command. pytest's temp dir stays at its default, outside the repo: never pass `--basetemp` under `./tmp/`. Seven skill tests assert that `tmp_path` lies outside `<repo>/tmp` and outside any git tree. The native judge's first r12 run failed exactly those 7 for this reason alone; Task 7 writes the rule down.
- Eval `prompt` is the user's message alone. Fixture paths, environment variables and the read-only rule go in `setup`, which is present exactly when `files` is (`test_eval_prompts_read_as_user_turns`).
- Scratch files go under `./tmp/`, and the run that creates them removes them. No containers, no GPU, no network, no downloads, no edits to the live `models.yml` or any capacity card. Set `TMPDIR` to a `./tmp/` scratch folder when you run `hf-scout.sh` by hand: it makes a `mktemp -d` working dir.

## Findings ledger (verified 2026-11-01 against the files)

| Finding | Verdict | Task |
|---|---|---|
| native D8-1 (medium): onboarding with a baseline while llm-server is enabled but stopped has no step (onboard-flow.md:87, :140) | confirmed. Step 1 only flags it. Step 9 restarts llm-server "when llm-server served before G2", which would leave a server found stopped running. The same gap exists in tuning T1/T9 and in the quant comparison, which starts the server but never hands it back. The stack-down case (`make start-llama` dies) has a gate only in served-alias R1 | 4 |
| native D8-2 (medium): an HF URL, repo or scout pick whose file is the enabled entry's file becomes candidate and baseline (onboard-flow.md:21, :62) | confirmed, and worse than reported. The served Q4_K_M gets `fit: "no"` from hf-scout's screening at 8321 MiB, so H3 would call the running model "does not fit" before H5 is reached. The check goes into H2, the first point where `pick.bytes` is known; H5 keeps a pointer | 5 |
| native D8-7 (medium): local-gguf.md:37 L6 pins an HF url with no procedure | confirmed. Also an order defect: L6 runs at 7a.2, after Gate G3, whose facts are "the diff", so the url must be decided before G3 | 6 |
| native D5-1 (medium): files point at each other by nickname; the onboard route loads about 490 lines | confirmed: about 23 nickname sites in 7 references plus one in `SKILL.md`, 8 eval strings, 1 fixture line. Measured loads: onboard-flow 259 + selection-criteria 141 + scoring 92 = 492 reference lines | 3 |
| native D2-1 (medium): local-GGUF order written as patches over the Steps | confirmed (local-gguf.md:11-12 checklist plus :32 L5). The judge's optional six-line summary at the top of onboard-flow.md is not added: the Steps' progress checklist becomes that list instead | 6 |
| referee D8-2 (medium): SKILL.md:21 enabled-alias row lacks the llm-server-enabled condition, while onboard-flow.md:31 cites it | confirmed by the lead and by a fixture run. No eval covers it: every gateway fixture has no enabled entry | 4 |
| referee D8-1 (high): A2 alias formula | refuted by the lead (A2 already says "that alias", the live entry's) | - |
| native D8-3 (low): no hint for an early `.gguf` typo | confirmed by a run (`Fo-9B-Q4_K_M.gguf`) | 1 |
| native D8-4 (low): a `file://` URL is reported as "not found" | confirmed by a run on an existing file | 1 |
| structural pass: `quant-headroom.sh fetch` with a different file already at the candidate name | found while reading cmd_fetch (:210-219). `curl --continue-at -` resumes onto the owner's file, then `rm -f` deletes it on the sha256 mismatch. With the test's curl stub, the owner's file is silently overwritten. The onboarding G1 and local-gguf L1 already forbid this; the quant fetch does not | 2 |
| structural pass: SKILL.md:82 says each route gate is "asked in its route's file" | becomes false once G-R1 lives in scoring.md, so the sentence changes | 4 |
| structural pass: selection-criteria.md:7 "`SKILL.md`'s alias row" | ambiguous once there are two alias rows; becomes "alias rows" | 4 |
| structural pass: the challenge row says Do NOT load "every other file", but its calibration runs `effort-run.sh`, which needs scoring.md (SKILL.md:36) | confirmed; the row gains "`references/scoring.md` to calibrate" | 3 |
| referee D1-1 (low): scoring.md:20 `<think>` sentence | taken: the generic clause goes, the host measurement stays | 3 |
| pytest temp dir: 7 failures with `--basetemp` under `./tmp` | not a skill defect. AGENTS.md's "ephemeral artifacts go under `./tmp/`" rule pushes agents into it and no doc states the exception, so Task 7 adds the rule and a fail-fast check | 7 |
| native D1-1 (anchors table), D3-1 (gotchas into SKILL.md), D4-1 (description rewrite) | dropped. Not in this round's brief; D3-1 also needs bytes `SKILL.md` does not have | - |
| native D6-1 (R1 starts ungated, R5 gates the same command) and D8-6 (G3's facts for the re-measure) | dropped. A gate redesign is an owner call and was not requested. Task 4 states the ungated start once, which makes it consistent across routes | - |
| native D7-1 (eval vocabulary) | partly. The four new evals use user words; rewording a dozen old prompts was not requested. Its tmp_path half is Task 7 | 7 |
| native D8-5 (latency-profile brief) | dropped: not requested | - |
| referee D2-1 (empty calibration log), D6-1 (repo tie-break), D7-1 (graded iteration) | dropped. No calibration run exists, and a made-up row would be false. The line already gives a default (first-party, else the named quantizer). A graded iteration needs live agent runs | - |

## Owner decisions (defaults chosen)

1. **Where the stopped-server rule lives.** In `references/scoring.md`, a new section "Serving the lane's model", with Gate G-R1. Every route that serves llm-server or runs a local lane loads that file. `SKILL.md` has 20 free bytes, which is too few for the rule and G-R1's facts.
2. **Hand back as found, in every route.** When a route's first preflight found llm-server enabled but stopped, its last step runs `make stop-llama`, even after a kept change (G5 yes, a tuning apply). The report says that `make start-llama` serves it. This matches served-alias R6 today.
3. **A declined G-R1 in onboarding** ends the run at step 1, with nothing changed. It never falls back to a run without a baseline.
4. **Served-file check in H2, not H5.** The brief says H5, but H3's screening fit and H4's research subagent run before H5. The check is: same base name, and `pick.bytes` equal to the baseline's `size_bytes`; models.yml stores no checksum, as G1's alias check already says. A same-name file of another size stops with both sizes.
5. **Citations.** `CITES` is the allowed pair list: a citing file names only files its `SKILL.md` route row loads. The one exception is onboard-flow -> selection-criteria, the research subagent's brief, handed over by path. The Hugging Face row stops loading selection-criteria section 7, and H3 states its `bpw` and `ctx` floors (3.5, 131072) inline.
6. **Numbering.** `fit-math.md` holds sections 1-3; `selection-criteria.md` renumbers its remaining sections 1-4. The pre-download offload RAM estimate stays with the scout (Scout and rank, step 4), its only user.
7. **`quant-headroom.sh fetch`** downloads into `<file>.part`, so an interrupted fetch still resumes. It renames the file only after the sha256 matches. A different file already at the final name exits 1 untouched.
8. **`resolve-target.sh`.** The 3-character prefix rule stays first; a Levenshtein distance <= 2 on the lowercased stems is its fallback. The judge proposed the reverse; this order keeps every current hint. A leading `file://` is dropped, and percent-escapes are not decoded (a `shortcut:` comment).
9. **pytest temp dir.** A `pytest_configure` UsageError in the skill tests' conftest, plus one `AGENTS.md` bullet in "Scratch vs Durable Artifacts", committed alone.

## Review Focus

1. A Hugging Face file with the baseline's file name but another size (another commit or quantizer). Expected: stop with both sizes before H3 and H4; no research, no G1. Pinned in Task 5 (H2 prose test).
2. llm-server enabled but stopped, and `make start-llama` says the stack is down, at onboarding step 1. Expected: G-R1 through the scoring rule; a no ends the run with nothing changed. Pinned in Task 4 (`A no to G-R1 ends the run here` in step 1).
3. A yes to G5 after step 1 found llm-server stopped. Expected: the kept change stays in models.yml, and `make stop-llama` hands llm-server back stopped. Pinned in Task 4 (step 9 prose test and the `onboard-baseline-server-stopped` eval).
4. An interrupted `quant-headroom.sh fetch` (a `.part` left behind), or an owner's file at the candidate name. Expected: the fetch resumes into `.part`; the owner's file is refused untouched (exit 1). Pinned in Task 2.
5. A `file://` URL with percent-escapes, or one naming a missing file. Expected: exit 2 naming the path without `file://`, never a search. Pinned in Task 1.

## What each route loads (reference lines, `SKILL.md`'s 120 lines not counted)

| Route | Before (measured) | After (expected; Task 8 measures) |
|---|---|---|
| alias, Hugging Face, scout pick | 492 (onboard-flow 259, selection-criteria 141, scoring 92) | about 413 (onboard-flow ~271, fit-math ~38, scoring ~104) |
| local GGUF | 535 (+ local-gguf 43) | about 468 (+ local-gguf ~55) |
| served alias | 139 | about 146 (served-alias ~42, scoring ~104) |
| tune, quant comparison | 284 | about 298 |
| scout and rank (no argument) | 141 | about 106 |
| bare name, then onboarding | 492 | about 519 (selection-criteria and fit-math are now two files) |
| anchors | 146 | about 158 |
| challenge (calibration) | 182 | 182 + scoring ~104 |

---

### Task 1: `resolve-target.sh` hints an early `.gguf` typo and accepts a `file://` path

**Files:**
- Modify: `.claude/skills/onboard-local-model/scripts/resolve-target.sh` (header :4, :6-7; after :44; `closest_gguf` :54-65; the alias near-miss jq :121-131)
- Modify: `.claude/skills/onboard-local-model/evals/evals.json` (append `early-typo-gguf-path`)
- Test: `tests/skills/onboard-local-model/test_onboard_local_model_scripts.py` (two tests after `test_resolve_target_hints_a_bare_file_name_for_an_argument_without_a_directory` :1918; one after `test_eval_mistyped_path_fixture_matches_its_assertions` :2237)

**Interfaces:**
- Produces: a `MODEL` starting with `file://` is classified as the rest of the string. `closest_gguf DIR NAME` falls back to the closest `.gguf` within 2 edits when no name shares a 3-character prefix. Task 6's L5 writes `file://` urls that can now be pasted back.

- [ ] **Step 1: Write the failing tests**

```python
def test_resolve_target_hints_a_gguf_name_within_two_edits(tmp_path: Path) -> None:
    d = tmp_path / "dl"
    d.mkdir()
    for name in ("Foo-9B-Q4_K_M.gguf", "Zed-1B-Q8_0.gguf"):
        (d / name).write_bytes(GGUF_BYTES)
    early = _rt(tmp_path, d / "Fo-9B-Q4_K_M.gguf")  # the first characters differ: the 3-character prefix rule finds nothing
    assert early.returncode == 2 and f"did you mean {d}/Foo-9B-Q4_K_M.gguf?" in early.stderr, early.stderr
    far = _rt(tmp_path, d / "Fxx-9C-Q4_K_M.gguf")  # 3 edits
    assert far.returncode == 2 and "did you mean" not in far.stderr
    assert f"did you mean {d}/Foo-9B-Q4_K_M.gguf?" in _rt(tmp_path, d / "Foo-9B-Q4_K_S.gguf").stderr  # the prefix rule still wins


def test_resolve_target_takes_a_file_url_as_its_path(tmp_path: Path) -> None:
    gguf = tmp_path / "Bar-7B-Q4_K_M.gguf"
    gguf.write_bytes(GGUF_BYTES)
    out = _rt_json(tmp_path, f"file://{gguf}")
    assert (out["kind"], out["path"]) == ("gguf_file", str(gguf.resolve()))
    for missing in (f"{tmp_path}/Gone-Q4_K_M.gguf", f"{tmp_path}/My%20Model-Q4_K_M.gguf"):  # percent-escapes stay encoded
        result = _rt(tmp_path, f"file://{missing}")
        assert result.returncode == 2 and f"{missing}: not found" in result.stderr and "file://" not in result.stderr
```

The eval pin test:

```python
def test_eval_early_typo_fixture_matches_its_assertions() -> None:
    arg = ".claude/skills/onboard-local-model/evals/files/Fo-9B-Q4_K_M.gguf"
    result = _run("bash", SCRIPTS / "resolve-target.sh", arg, env={**os.environ, **FIX_ENV}, cwd=ROOT)
    hint = "did you mean .claude/skills/onboard-local-model/evals/files/Foo-9B-Q4_K_M.gguf?"
    assert result.returncode == 2 and "not found" in result.stderr and hint in result.stderr
    assert hint in _asserts("early-typo-gguf-path")
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/skills/onboard-local-model/test_onboard_local_model_scripts.py -q -k "within_two_edits or file_url_as or early_typo"`
Expected: FAIL. There is no hint for `Fo-9B`; `file://` gives "file://...: not found"; the eval is missing.

- [ ] **Step 3: Implement in `resolve-target.sh`**

- After the `~` expansion (:44): `case "$model" in file://*) model="${model#file://}" ;; esac`, with the comment `# shortcut: a file:// url's percent-escapes stay encoded; decode them when a pasted url ever carries one`.
- Move `def lev($a; $b): ...;` out of the alias near-miss program (:123-127) into one shell variable, `lev_jq`, defined once above `closest_gguf`. Both jq programs use it.
- `closest_gguf`: after the prefix loop, when `best` is empty and `name` is not: take the file whose lowercased name, without `.gguf`, is within Levenshtein 2 of `name` without `.gguf`. Pick the smallest distance; ties go to the first in C sort order (feed the same sorted `find` list to `jq -Rn`; `sort_by` is stable). Its comment says: "longest common prefix (>= 3), else within 2 edits".
- Header: in the MODEL line (:4), after "expanded to $HOME", add "and a leading file:// dropped". In (4) (:6-7), "with the closest .gguf of its directory" becomes "with the closest .gguf of its directory (a common prefix of 3+ characters, else within 2 edits)".

- [ ] **Step 4: The eval**

Append to `evals` in evals.json, with the same keys as `mistyped-gguf-path` (no `setup`, no `files`):

```json
{
  "id": "early-typo-gguf-path",
  "prompt": "use .claude/skills/onboard-local-model/evals/files/Fo-9B-Q4_K_M.gguf as my local model",
  "expected_output": "resolve-target.sh exits 2 with not found and a did-you-mean hint for Foo-9B-Q4_K_M.gguf (an early typo); the answer reports it, asks whether that file was meant and stops.",
  "assertions": [
    "resolve-target.sh .claude/skills/onboard-local-model/evals/files/Fo-9B-Q4_K_M.gguf exits 2 with not found (did you mean .claude/skills/onboard-local-model/evals/files/Foo-9B-Q4_K_M.gguf?)",
    "Exit 2 is treated as an error: the answer reports the message, asks whether Foo-9B-Q4_K_M.gguf was meant and stops; hf-scout.sh --query is not run",
    "No scratch config, download, gate, container or configuration change was made"
  ]
}
```

- [ ] **Step 5: Run the tests and validate (no commit)**

Run: `uv run pytest tests/skills/onboard-local-model/test_onboard_local_model_scripts.py -q -k "resolve_target or eval_"`, then `make validate && make test`.
Expected: PASS and green. The `hint=None` cases of `test_resolve_target_exits_3_only_for_a_bare_model_name` still pass: they assert no hint text.

### Task 2: `quant-headroom.sh fetch` never resumes onto, overwrites or deletes a file it did not download

**Files:**
- Modify: `.claude/skills/onboard-local-model/scripts/quant-headroom.sh` (header :19-20; `cmd_fetch` :209-221)
- Modify: `.claude/skills/onboard-local-model/references/tuning.md` (Q2, the sentence "The file is verified against the tree's sha256; a mismatching download is removed.")
- Test: `tests/skills/onboard-local-model/test_onboard_local_model_scripts.py` (`test_quant_headroom_fetch_verifies_sha256_and_removes_a_mismatch` :1615; a new test after `test_quant_headroom_fetch_skips_a_present_verified_file` :1630)

**Interfaces:**
- Produces: `fetch` writes `<models_dir>/<file>.part`, verifies it, then renames it to `<file>`. A different `<file>` already present exits 1 and is left as it is.

- [ ] **Step 1: Write the failing test and adjust the existing one**

```python
def test_quant_headroom_fetch_never_resumes_onto_or_deletes_a_file_it_did_not_download(tmp_path: Path) -> None:
    good = hashlib.sha256(b"known-bytes").hexdigest()
    env = _qh_env(tmp_path, tree=_qh_tree() + [{"type": "file", "path": "m-Q8_0.gguf", "size": 11, "lfs": {"oid": good}}], curl=True)
    dest, part = tmp_path / "models" / "m-Q8_0.gguf", tmp_path / "models" / "m-Q8_0.gguf.part"
    dest.write_bytes(b"owner")  # the owner's own, smaller file under the candidate's name
    result = _qh(env, "fetch", "--alias", "m", "--quant", "Q8_0")
    assert result.returncode == 1 and str(dest) in result.stderr and "never" in result.stderr
    assert dest.read_bytes() == b"owner" and not any(c.startswith("curl") for c in _calls(tmp_path))
    dest.unlink()
    part.write_bytes(b"known")  # an interrupted fetch resumes into its own .part
    result = _qh(env, "fetch", "--alias", "m", "--quant", "Q8_0")
    assert result.returncode == 0, result.stderr
    assert dest.read_bytes() == b"known-bytes" and not part.exists()
    assert any("--continue-at -" in c and f"-o {part} " in c for c in _calls(tmp_path))
```

In `test_quant_headroom_fetch_verifies_sha256_and_removes_a_mismatch`, `-o {tmp_path}/models/m-Q8_0.gguf https://` becomes `-o {tmp_path}/models/m-Q8_0.gguf.part https://`. The final assert becomes `assert not (bad / "models" / "m-Q5_K_M.gguf").exists() and not (bad / "models" / "m-Q5_K_M.gguf.part").exists()`.

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/skills/onboard-local-model/test_onboard_local_model_scripts.py -q -k "quant_headroom_fetch"`
Expected: FAIL. The owner's file is overwritten with `known-bytes` (exit 0), and curl writes to the final name.

- [ ] **Step 3: Implement `cmd_fetch`**

- A present `dest` with the tree's sha256: "already present" (unchanged). A present `dest` with another sha256: `die "$dest is already in the models directory with another sha256; it is never resumed onto, overwritten or deleted: move or rename it first"`.
- `part="$dest.part"`: curl writes `-o "$part"` with `--continue-at -`. The sha256 is taken from `$part`. A mismatch runs `rm -f "$part"` and dies with "...; the partial download was removed". A match runs `mv "$part" "$dest"`, then prints "fetched".
- Header (:19-20): "fetch: downloads one candidate file into `<file>.part` in the models dir (free disk >= size + 1024 MiB; an interrupted fetch resumes there), verifies it against the tree's lfs.oid and renames it; a mismatch removes only the .part, and a different file already at the name exits 1 untouched."
- tuning.md Q2: the sentence becomes "It downloads into `<file>.part` (an interrupted fetch resumes there), checks the tree's sha256 and only then renames it; a mismatch removes only that `.part`, and a different file already under that name stops the fetch untouched (exit 1): report it."

- [ ] **Step 4: Run the tests and validate (no commit)**

Run: `uv run pytest tests/skills/onboard-local-model/test_onboard_local_model_scripts.py -q -k "quant_headroom or stated_once or shell_logic"`, then `make validate && make test`.
Expected: PASS and green.

### Task 3: Real paths, the fit math in its own file, and a citation guard

**Files:**
- Create: `.claude/skills/onboard-local-model/references/fit-math.md`
- Modify: `SKILL.md` (:17; the Hugging Face row :23; the challenge row :32; :36)
- Modify: `references/selection-criteria.md` (:3, :29-36, delete :44-81, renumber, :84, :98, :121)
- Modify: the nickname sites in `onboard-flow.md`, `served-alias.md`, `local-gguf.md`, `challenges.md`, `tuning.md`, `anchors.md`, `scoring.md` (:20-21), listed in Step 4
- Modify: `evals/evals.json` (lines 127, 186, 280, 445, 486, 490, 578, 581) and `evals/files/anchors/route-check.txt` (line 1)
- Test: `tests/skills/onboard-local-model/test_onboard_local_model_scripts.py` (`REF_NAMES` :973; replace `test_references_never_point_at_each_other` :977; `RETIRED_COPIES` :991; `test_references_keep_host_specific_evidence_and_give_every_flag_a_destination` :1039; `test_onboard_step_4_selects_the_candidate_before_validating_it` :1081)

**Interfaces:**
- Produces: `references/fit-math.md` (`## 1. Fit math`, `## 2. MoE offload`, `## 3. Quant floor`). `selection-criteria.md` sections are now `## 1.` (evidence ladder) to `## 4.` (ranking; the Research brief is in section 4). `CITES` and `BARE_REF` in the test file. The citation form is `` `references/<name>.md`, <section> ``, which Tasks 4-6 use.

- [ ] **Step 1: Write the failing tests**

`REF_NAMES` gains `"fit-math"` (after `"challenges"`). Replace `test_references_never_point_at_each_other` with:

```python
# One level deep: SKILL.md alone says when to load a reference. A reference names another only by its path, and only these pairs:
# files its SKILL.md route row loads, plus onboard-flow -> selection-criteria (the research subagent's brief, handed over by path).
CITES = {"onboard-flow": {"fit-math", "local-gguf", "scoring", "selection-criteria"}, "local-gguf": {"onboard-flow"},
         "served-alias": {"scoring"}, "tuning": {"scoring"}, "anchors": {"scoring"}, "challenges": {"scoring"}}
BARE_REF = re.compile(rf"(?<!references/)\b(?:{'|'.join(map(re.escape, REF_NAMES))})\.md\b")  # evals write the path without backticks
NICKNAMES = ("effort-ladder reference", "effort-ladder brief", "Local GGUF reference", "Local GGUF route", "anchors reference",
             "research brief", "fit math loaded", "the runtime route", "the quant route", "section 7 brief", "the reference table")


def test_references_cite_each_other_only_by_path_and_only_on_their_route() -> None:
    assert sorted(p.stem for p in REFS.glob("*.md")) == sorted(REF_NAMES)
    for path in sorted(REFS.glob("*.md")):
        text = path.read_text()
        named = {n for n in REF_NAMES if n != path.stem and re.search(rf"\b{re.escape(n)}\.md\b", text)}
        assert named == CITES.get(path.stem, set()), (path.name, sorted(named))
        assert not BARE_REF.findall(text), (path.name, BARE_REF.findall(text))


def test_skill_files_name_other_files_by_path_not_by_nickname() -> None:
    files = [SKILL_MD, *sorted(REFS.glob("*.md")), SKILL / "evals" / "evals.json", SKILL / "evals" / "files" / "anchors" / "route-check.txt"]
    assert not [(p.name, n) for p in files for n in NICKNAMES if n in _md(p)]
    assert not BARE_REF.findall((SKILL / "evals" / "evals.json").read_text())


def test_fit_math_has_its_own_file_and_the_onboard_rows_skip_the_ranking_sections() -> None:
    fit, sel = (REFS / "fit-math.md").read_text(), (REFS / "selection-criteria.md").read_text()
    assert re.findall(r"^## (\d)\. ", fit, re.MULTILINE) == ["1", "2", "3"] and "pick.mib + 2048" not in fit
    assert re.findall(r"^## (\d)\. ", sel, re.MULTILINE) == ["1", "2", "3", "4"] and "Fit math" not in sel
    assert "`pick.mib + 2048 - <rule 1 budget>`" in _section(_md(REFS / "selection-criteria.md"), "## Scout and rank", "## 1.")
    text = _md(SKILL_MD)
    assert "`references/fit-math.md` at step 4" in text and "section 7" not in text and "sections 1-3" not in text
    assert "| `references/challenges.md` | `references/scoring.md` to calibrate |" in text
    flow = _md(REFS / "onboard-flow.md")
    assert "`references/selection-criteria.md`, section 4, Research brief" in _section(flow, "H4. ", "H5. ")
    assert "a `pick.bpw` under 3.5 or a `ctx` under 131072" in _section(flow, "H3. ", "H4. ")
    assert "1,117 output tokens" in _md(REFS / "scoring.md")
```

- In `test_references_keep_host_specific_evidence_and_give_every_flag_a_destination`, `"## 4. Is it really agentic?", "## 5."` becomes `"## 1. Is it really agentic?", "## 2."`.
- In `test_onboard_step_4_selects_the_candidate_before_validating_it`, `"section 7, which the Hugging Face row" in _section(flow, "H4. ", "H5. ")` is removed; the new fit-math test covers H4.
- `RETIRED_COPIES` gains `"Reasoning models spend output tokens"`.

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/skills/onboard-local-model/test_onboard_local_model_scripts.py -q -k "cite_each_other or nickname or fit_math or host_specific or step_4_selects or stated_once"`
Expected: FAIL (no fit-math.md, nicknames present).

- [ ] **Step 3: Split the fit math**

- `fit-math.md`: title `# Fitting a candidate`, then the line "Loaded at onboarding step 4 (`SKILL.md` Route). `llmenv budget` gives the verdict; this is the arithmetic behind it." Then selection-criteria.md :44-80 (`## 1. Fit math` to the end of `## 3. Quant floor`) moved verbatim, with one change. In section 2, the bullet "Before download, a scout row's offload RAM estimate is ... the solve above replaces it." becomes "- Before download, `hf-scout.sh`'s `offload` class is the only estimate; once the GGUF is on disk, the solve above replaces it."
- `selection-criteria.md`:
  - :3 becomes "Loaded for a bare model name and the no-argument route. A research subagent reads it for its brief (section 4, Research brief)."
  - Scout step 4: drop " (section 2)", and append "The offload RAM estimate of a scout row is `pick.mib + 2048 - <rule 1 budget>` MiB: what does not fit in VRAM once about 2 GiB is left for KV cache and buffers (the arithmetic of `hf-scout.sh`'s `offload` class)."
  - Step 5: "(brief in section 7), then section 7's optional jev items" becomes "(the Research brief, section 4), then section 4's optional jev items".
  - Step 7: "Report per section 6." becomes "Report per section 3."
  - Delete :44-81 (the moved sections and the blank line after them).
  - Renumber `## 4.`-`## 7.` to `## 1.`-`## 4.`, and "Section 7's `agentic_evidence` levels" becomes "Section 4's".
  - "(the runtime route)" becomes "(`SKILL.md` Route, llama.cpp image row)".
  - In the levels table, "beyond its section 2 estimate" becomes "beyond its offload RAM estimate (Scout and rank, step 4)".
- `SKILL.md`:
  - :17: "`references/selection-criteria.md` sections 1-3 at step 4 and" becomes "`references/fit-math.md` at step 4 and".
  - The Hugging Face row's Also-load cell "onboard; `references/selection-criteria.md` section 7 at step 3" becomes "onboard".
  - The challenge row's empty Also-load cell becomes "`references/scoring.md` to calibrate".
  - :36 drops " (the effort-ladder reference)".
  - Measure: `wc -c` gives 10,908.

- [ ] **Step 4: Paths for every nickname**

| File | Phrase | Becomes |
|---|---|---|
| onboard-flow.md :15 | "except where the Local GGUF reference names" | "except where `references/local-gguf.md` names" |
| onboard-flow.md A2 | "the name the quant route gives" | "the name `quant-headroom.sh add-entry` gives" |
| onboard-flow.md H3 2nd bullet | "A false card gate, or a failed `bpw` or `ctx` gate: a G1 fact." | "A false card gate (H4), a `pick.bpw` under 3.5 or a `ctx` under 131072: a G1 fact." |
| onboard-flow.md H4 | everything after "**Evidence.**" | "One research subagent writes the evidence card (`tmp/model-scout/<run>/evidence/1.json`). Hand it the repo id, its H1 row and its brief: `references/selection-criteria.md`, section 4, Research brief. Read back the card's `gates` for H3." |
| onboard-flow.md 4.1 | "Fit, with the fit math loaded with this route (sections 1-3):" | "Fit, per `references/fit-math.md`:" |
| onboard-flow.md 4.3 | "(the Local GGUF reference, L3)" | "(`references/local-gguf.md`, L3)" |
| onboard-flow.md 5.2 | "upper bound per the effort-ladder reference, Running a lane" | "upper bound per `references/scoring.md`, Running a lane" |
| onboard-flow.md 6.5 | "goes to the runtime route" | "goes to the `SKILL.md` Route row for the llama.cpp image" |
| onboard-flow.md 7c.4 | "(Head-to-head of the effort-ladder reference)" | "(`references/scoring.md`, Head-to-head)" |
| onboard-flow.md 7c.5 | "Classify failures per the effort-ladder reference." | "Classify failures per `references/scoring.md`, Fast classification." |
| onboard-flow.md 8 (b) | "(Score, item 6, of the effort-ladder reference)" | "(`references/scoring.md`, Score, item 6)" |
| served-alias.md R2 | "its upper bound per the effort-ladder reference, Running a lane" | "its upper bound per `references/scoring.md`, Running a lane" |
| local-gguf.md :3 | "before the onboard Steps (`SKILL.md` Route)" | "before the Steps of `references/onboard-flow.md` (`SKILL.md` Route)" |
| local-gguf.md L6 | "as the quant route does" / "as the quant route's G-Q3" | "as `quant-headroom.sh add-entry` does" / "as Gate G-Q3" |
| challenges.md :134 | "the effort-ladder reference, Running a lane" | "`references/scoring.md`, Running a lane" |
| challenges.md :166 | "(the effort-ladder brief's re-run folder)" | "(the re-run folder of `references/scoring.md`, Running a lane)" |
| tuning.md T8 | "(the effort-ladder reference, Running a lane)" (spans :31-32) | "(`references/scoring.md`, Running a lane)" |
| tuning.md :149 | "a head-to-head lane of the effort-ladder reference on `<seeds>`" | "a head-to-head lane (`references/scoring.md`, Head-to-head) on `<seeds>`" |
| tuning.md Q8 | "(Head-to-head of the effort-ladder reference)" | "(`references/scoring.md`, Head-to-head)" |
| tuning.md G-Q3 | "(Score, item 6, of the effort-ladder reference)" | "(`references/scoring.md`, Score, item 6)" |
| anchors.md :3 | "with the effort-ladder reference." | "with `references/scoring.md`." |
| anchors.md item 3 | "(the effort-ladder brief's `<out>`)" | "(the `<out>` of `references/scoring.md`, Running a lane)" |
| scoring.md :20-21 | "Reasoning models spend output tokens on `<think>` before answering: on this host Ornith 9B used 1,117 tokens against an 8,000 budget on a trivial bug, so" | "On this host Ornith 9B spent 1,117 output tokens thinking through a trivial bug (budget 8,000), so" |

Evals and fixture:
- :127 "(the section 7 brief)" becomes "(the Research brief, `references/selection-criteria.md` section 4)".
- :186 "tuning.md Quant headroom" becomes "`references/tuning.md`, Quant headroom".
- :280 "of the reference table" becomes "of the table in `references/runtimes.md`".
- :445 "tuning.md's Standalone quant comparison" becomes "`references/tuning.md`, Standalone quant comparison".
- :486 and :490: "the effort-ladder brief" becomes "the brief in `references/scoring.md`, Running a lane".
- :578 "the Local GGUF route runs" becomes "`references/local-gguf.md` runs".
- :581 "The Local GGUF reference runs:" becomes "`references/local-gguf.md` runs:".
- In route-check.txt line 1, "(the anchors reference, Anchor lanes item 5)" becomes "(`references/anchors.md`, Anchor lanes item 5)".

Every string a test reads through `_asserts(...)` stays as it is (`not the last-seen 10454`, `effort.log`, ...). The nickname and citation tests of Step 1 are the check that none is left.

- [ ] **Step 5: Run and validate (no commit)**

Run: `uv run pytest tests/skills/onboard-local-model/test_onboard_local_model_scripts.py -q -k "cite_each_other or nickname or fit_math or host_specific or step_4 or stated_once or router or route_table or black_box or shell_logic or named_once or ask_first or eval_"`, then `make validate && make test`.
Expected: PASS and green. `wc -c SKILL.md` gives 10,908.

### Task 4: One rule for a stopped llm-server, every route hands it back; the enabled-alias row's host condition

**Files:**
- Modify: `references/scoring.md` (:3-4 header; a new section before `## Running a lane`)
- Modify: `references/served-alias.md` (:3; R1 bullets :19-27)
- Modify: `references/onboard-flow.md` (:3; step 1 :87; 4.3's mismatch bullet; 5.4 :146-147; step 9 :215-220)
- Modify: `references/tuning.md` (T1 :19; T9 :33-35; the quant comparison :108-109 and its progress checklist :119-121)
- Modify: `references/selection-criteria.md` (:7 "`SKILL.md`'s alias row")
- Modify: `SKILL.md` (row :21; :82)
- Create: `evals/files/models-gateway-enabled.yml`
- Modify: `evals/evals.json` (append `onboard-baseline-server-stopped`, `gateway-enabled-alias`)
- Test: `test_onboard_local_model_scripts.py` (`STATED_ONCE`, `RETIRED_COPIES`, `test_freeing_the_gpu_sums_every_llama_server_holder` :864, `test_served_alias_reference_covers_a_stopped_server_and_a_declined_g3` :1118-1123; new tests after `test_served_alias_reference_covers_...` and after `test_eval_enabled_alias_stopped_fixture_matches_its_assertions`)

**Interfaces:**
- Consumes: Task 3's citation form.
- Produces: the scoring.md section `## Serving the lane's model`, cited as "`references/scoring.md`, Serving the lane's model". Task 6's Steps checklist names its hand-back.

- [ ] **Step 1: Write the failing tests**

```python
def test_one_rule_serves_a_stopped_llm_server_and_each_route_hands_it_back() -> None:
    rule = _section(_md(REFS / "scoring.md"), "## Serving the lane's model", "## Running a lane")
    assert all(s in rule for s in ("`services.llm_server_running` false", "make start-llama", "says the stack is not running",
                                   "**Gate G-R1** before `make start`", "A no to G-R1", "`make stop-llama` last",
                                   "a kept change included", "fails its budget check"))
    cite = "`references/scoring.md`, Serving the lane's model"
    flow = _md(REFS / "onboard-flow.md")
    step1 = _section(flow, "1. **Preflight (read-only).**", "2. **Baseline.**")
    assert cite in step1 and "A no to G-R1 ends the run here" in step1
    step9 = _section(flow, "9. **Keep or revert", "## Entry template")
    assert "Hand-back: step 1 found llm-server stopped and it runs now (a yes to G5 included): `make stop-llama`" in step9
    assert "Then, when step 1 found llm-server serving: `make start-llama`" in step9
    assert cite in _section(_md(REFS / "served-alias.md"), "R1. ", "R2. ")
    tuning = _md(REFS / "tuning.md")
    assert cite in _section(tuning, "- T1.", "- T2.")
    assert "When T1 found llm-server stopped: `make stop-llama`" in _section(tuning, "- T9.", "## Interference gate")
    assert cite in _section(tuning, "### Standalone quant comparison", "Ask yourself first:")
    text = _md(SKILL_MD)
    assert "(`alias`, `enabled: true`) on a host with llm-server enabled (preflight `services.llm_server_enabled`): re-measure" in text
```

- `test_freeing_the_gpu_sums_every_llama_server_holder`: "each asked in its route's file" becomes "each asked in the file that names it".
- `test_served_alias_reference_covers_a_stopped_server_and_a_declined_g3`:
  - The r1 tuple (:1118-1119) becomes `("`services.llm_server_enabled` false", "`services.llm_server_running` false", "Serving the lane's model", "then R1 again")`.
  - The r1 tuple at :1123 becomes `("A no to G-R1", "capacity show")`.
- `STATED_ONCE` gains `"**Gate G-R1** before `make start`": "references/scoring.md"` and `"says the stack is not running": "references/scoring.md"`.
- `RETIRED_COPIES` gains `"Remember that it was stopped, for R6"`, `"when llm-server served before G2"`, `"G2 has not stopped it"`, `"the prior state (serving, or gateway-only)"`, `"(step 9; no `make stop-llama`)"`, `"enabled but stopped: `make start-llama` after P0"` and `"Flag `services.llm_server_running`"`.

Eval pin tests:

```python
def test_eval_onboard_baseline_server_stopped_fixtures_match_their_assertions() -> None:
    stopped = json.loads((FIX / "preflight-stopped.json").read_text())
    s = stopped["services"]
    assert (s["llm_server_enabled"], s["llm_server_running"], s["omniroute_running"], s["system_one_running"]) == (True, False, True, True)
    assert not [h for h in stopped["gpu"]["holders"] if h["comm"] == "llama-server"]
    (row,) = json.loads(_run("bash", SCRIPTS / "hf-scout.sh", "--repo", "lab/Foo-9B-GGUF", "--json", "--budget-mib", "8321", "--ram-mib",
                             "12000", env={"HF_FIXTURE_DIR": str(FIX / "hf"), "PATH": "/usr/bin:/bin"}).stdout)
    assert (row["fit"], row["pick"]["file"], row["pick"]["mib"]) == ("vram", "Foo-9B-Q5_K_M.gguf", 6500)
    text = _asserts("onboard-baseline-server-stopped")
    assert all(t in text for t in ("make start-llama", "reads preflight again", "effort-baseline", "after make start-llama",
                                   "make stop-llama", "a yes to G5"))


def test_eval_gateway_enabled_alias_fixtures_match_their_assertions() -> None:
    base = yaml.safe_load((FIX / "models-gateway.yml").read_text())
    next(m for m in base["models"] if m["alias"] == "gemma4")["enabled"] = True
    cfg = FIX / "models-gateway-enabled.yml"
    assert yaml.safe_load(cfg.read_text()) == base and base["llm_server"]["enabled"] is False
    env = {**os.environ, "LLM_ENV_CONFIG": str(cfg), "LLM_ENV_MODELS_DIR": str(FIX / "models")}
    rt = json.loads(_run("bash", SCRIPTS / "resolve-target.sh", "gemma4", env=env).stdout)
    assert (rt["kind"], rt["enabled"], rt["present"]) == ("alias", True, True)
    pre = json.loads((FIX / "preflight-gateway.json").read_text())
    assert pre["services"]["llm_server_enabled"] is False and pre["budget"]["llmenv_budget"] is None
    assert not [h for h in pre["gpu"]["holders"] if h["comm"] == "llama-server"]
    text = _asserts("gateway-enabled-alias")
    assert all(t in text for t in ("other-alias row", "not a re-measure", "10369", "14500", "H = 0", "make llama-enable MODEL=gemma4"))
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/skills/onboard-local-model/test_onboard_local_model_scripts.py -q -k "stopped_llm_server or freeing_the_gpu or served_alias or stated_once or baseline_server_stopped or gateway_enabled"`
Expected: FAIL.

- [ ] **Step 3: The rule in scoring.md**

The header (:3-4) becomes "Loaded by every route that runs `effort-run.sh`, before its first lane, and at the first preflight of a route that finds llm-server stopped. Profile, seeds and reference depth: `SKILL.md` rule 3; delegation: rule 2; `$SK` and paths as defined there." Insert before `## Running a lane`:

```text
## Serving the lane's model

A local lane (`router-env/llama-cpp/<alias>`) needs llm-server serving that alias. A route applies this at its first
preflight and names the step that hands llm-server back.
- `services.llm_server_enabled` true and `services.llm_server_running` false: say so, run `make start-llama`, then
  `preflight.sh` again (U and H of `SKILL.md` Freeing the GPU come from that read). Remember that it was stopped: the
  route's hand-back step runs `make stop-llama` last, so llm-server ends as it was found, a kept change included (the
  report says that `make start-llama` serves it).
- `make start-llama` says the stack is not running: **Gate G-R1** before `make start`, then `preflight.sh` again. Its
  facts: `make start` renders the units, migrates the config, (re)installs the OmniRoute healer and z.ai weekly-reset
  timers, and brings up every stack container that is down (OmniRoute, remote-setup, System One, Phoenix with System One,
  llm-server); System One loads its weights before it is healthy; the hand-back stops only llm-server, so the rest stays
  up. A no to G-R1: no local lane runs; the route reports what it has, says that nothing was measured, and stops.
- `make start-llama` or `make start` fails its budget check: its output is the result. Report it and stop; never loosen
  `vram_budget`.
```

- [ ] **Step 4: Each route cites it and hands back**

- served-alias.md:
  - :3: after "`enabled: true`)" insert " on a host with llm-server enabled,".
  - R1's bullets from "`services.llm_server_running` false:" through the budget-check bullet (:19-27) become:

```text
    - `services.llm_server_running` false: serve it per `references/scoring.md`, Serving the lane's model (Gate G-R1 when
      the stack is down), then R1 again; R6 hands it back. A no to G-R1 also reports the capacity card as it stands
      (`uv run llmenv.py capacity show --alias <alias>`).
```

- onboard-flow.md:
  - :3: "Loaded for any alias but the enabled one," becomes "Loaded for any alias except the enabled one on a host with llm-server enabled,".
  - Step 1: "- Flag `services.llm_server_running` and any non-zero `gpu.holders`." becomes:

```text
   - Flag any non-zero `gpu.holders`.
   - `services.llm_server_enabled` true and `services.llm_server_running` false: serve it per `references/scoring.md`,
     Serving the lane's model (it may ask Gate G-R1), then step 1 again. Remember that it was stopped: step 9 hands it
     back. A no to G-R1 ends the run here.
```

  - 4.3: "(step 9; no `make stop-llama`)" becomes "(step 9)".
  - 5.4: delete " Remember the prior state (serving, or gateway-only) for step 9."
  - Step 9: its bullets from "A declined G1 or G2" through "applies gateway-only mode to the whole stack." become:

```text
   - A declined G1 or G2, or a failure before G2 (a download size or sha256 mismatch): report it and remove the scratch
     files; nothing is rolled back, and G2 stopped nothing.
   - A no to G5, a declined G3 or a failed step after G2: `make stop-llama`, then restore the rollback copy
     (`cp -p <rollback copy> ~/.config/llm-env/models.yml`; nothing to restore before G3).
   - Then, when step 1 found llm-server serving: `make start-llama`. A host that was gateway-only stops there; `make restart`
     later applies gateway-only mode to the whole stack.
   - Hand-back: step 1 found llm-server stopped and it runs now (a yes to G5 included): `make stop-llama`.
```

- tuning.md:
  - T1 gains, at its end: " Enabled but stopped: serve it per `references/scoring.md`, Serving the lane's model; T9 hands it back."
  - T9: before "Finish with", insert "When T1 found llm-server stopped: `make stop-llama`."
  - In the quant comparison, "(enabled but stopped: `make start-llama` after P0)" becomes "(enabled but stopped: after P0, serve it per `references/scoring.md`, Serving the lane's model; the program then ends with `make stop-llama`)".
  - Its progress checklist's "G-Q3; leftover check" becomes "G-Q3; `make stop-llama` when found stopped; leftover check".
- selection-criteria.md :7: "goes to `SKILL.md`'s alias row" becomes "goes to `SKILL.md`'s alias rows".
- `SKILL.md`:
  - Row :21's first cell becomes "the enabled alias (`alias`, `enabled: true`) on a host with llm-server enabled (preflight `services.llm_server_enabled`): re-measure".
  - :82 "each asked in its route's file." becomes "each asked in the file that names it.".
  - Measure: `wc -c` gives 10,990.

- [ ] **Step 5: Fixture and evals**

Copy `evals/files/models-gateway.yml` to `evals/files/models-gateway-enabled.yml` byte for byte. Change only gemma4's `    enabled: false` (line 75, under `quantization: Q4_K_M`) to `    enabled: true`; leave line 32 (`llm_server`) alone. Use an editor; not yq, which re-indents comments. `<F>` below stands for `.claude/skills/onboard-local-model/evals/files`. Write it out in full.

```json
{
  "id": "onboard-baseline-server-stopped",
  "prompt": "could https://huggingface.co/lab/Foo-9B-GGUF beat the model I run locally? walk me through what you'd do, step by step, and where you'd stop to ask me first",
  "setup": "The fixture config is <F>/models.yml and its models directory is <F>/models/ (scripts read them through LLM_ENV_CONFIG and LLM_ENV_MODELS_DIR). hf-scout.sh reads its Hugging Face answers from HF_FIXTURE_DIR=<F>/hf/ instead of the network. preflight.sh already printed <F>/preflight-stopped.json; do not run it, use that JSON as its output. DRY RUN for evaluation: read-only commands only (resolve-target.sh, hf-scout.sh, cat, jq, yq); do not download weights, start or stop containers, edit models.yml or start benchmarks. Answer in English.",
  "files": ["evals/files/models.yml", "evals/files/preflight-stopped.json", "evals/files/hf/model-lab__Foo-9B-GGUF.json", "evals/files/hf/tree-lab__Foo-9B-GGUF.json"],
  "expected_output": "lab/Foo-9B-GGUF is onboarded against the enabled gemma4 baseline, which is enabled but stopped: step 1 serves it with make start-llama and reads preflight again, the gemma4 lane runs before G2 with the G2 target from that second read, and step 9 hands llm-server back stopped with make stop-llama whatever G5 decides.",
  "assertions": [
    "resolve-target.sh gives kind hf_repo for lab/Foo-9B-GGUF, and hf-scout.sh --repo lab/Foo-9B-GGUF --json --budget-mib 8321 --ram-mib 12000 gives fit vram with the pick Foo-9B-Q5_K_M.gguf (6500 MiB)",
    "The preflight JSON says llm_server_enabled true and llm_server_running false while OmniRoute and System One run: gemma4 is the baseline, and step 1 says llm-server is stopped, runs make start-llama (never make start, make restart or make setup) and reads preflight again before the baseline lane",
    "The gemma4 baseline lane (router-env/llama-cpp/gemma4, --per-layer 3, --out tmp/model-scout/<run>/effort-baseline) runs while gemma4 is served, before G2",
    "G2's wait-gpu-release.sh --below-mib comes from the preflight read after make start-llama (U - H + 512, H summed over the llama-server holders), not from the stopped JSON, which has no llama-server holder",
    "Step 9 hands llm-server back stopped with make stop-llama, as step 1 found it, after a yes to G5 as well as after a no (a no also restores the cp -p rollback copy)",
    "Nothing was downloaded, started, stopped or edited"
  ]
}
```

```json
{
  "id": "gateway-enabled-alias",
  "prompt": "gemma4 feels slow on this machine. measure it again and tell me where it stands",
  "setup": "The fixture config is <F>/models-gateway-enabled.yml and its models directory is <F>/models/ (scripts read them through LLM_ENV_CONFIG and LLM_ENV_MODELS_DIR). preflight.sh already printed <F>/preflight-gateway.json; do not run it, use that JSON as its output. DRY RUN for evaluation: read-only commands only (resolve-target.sh with those variables, cat, yq, jq); do not download, start or stop containers, edit models.yml or start benchmarks. Answer in English.",
  "files": ["evals/files/models-gateway-enabled.yml", "evals/files/models/gemma4-v2-Q4_K_M.gguf", "evals/files/preflight-gateway.json"],
  "expected_output": "gemma4 is an entry left enabled on a gateway-only host, so it is not a re-measure: the other-alias row onboards it with no baseline on a scratch config, sized against model_vram_budget_mib, with no make stop-llama, and it is served only after G3's make llama-enable.",
  "assertions": [
    "resolve-target.sh gemma4 with the fixture variables gives kind alias, enabled true, present true, and the preflight JSON says llm_server_enabled false: the enabled-alias row needs llm-server enabled, so this is not a re-measure but SKILL.md's other-alias row (references/onboard-flow.md, Alias)",
    "The scratch config comes from resolve-target.sh gemma4 --scratch-config, and validate-gguf and budget take --config of it",
    "The budget is model_vram_budget_mib 10369 because llmenv_budget is null, and the RAM is ram.available_mib 14500 because ram_offload_budget_mib is 0, both said",
    "There is no baseline (gateway-only host): no baseline lane or bench, and no make stop-llama, because no llama-server holds the GPU (H = 0)",
    "Serving it needs Gate G3: a cp -p rollback copy, then make llama-enable MODEL=gemma4 (no new entry) and LLM_ENV_ASSUME_YES=1 make setup",
    "Nothing was downloaded, stopped, started or edited"
  ]
}
```

- [ ] **Step 6: Run and validate (no commit)**

Run: `uv run pytest tests/skills/onboard-local-model/test_onboard_local_model_scripts.py -q -k "stopped or served_alias or freeing or stated_once or cite_each_other or nickname or router or route_table or eval_ or user_turns or shell_logic or black_box"`, then `make validate && make test`.
Expected: PASS and green. `test_eval_enabled_alias_stopped_fixture_matches_its_assertions` still passes: served-alias still starts the server at R1, now through the rule.

### Task 5: A Hugging Face file that is the baseline's own file goes to the re-measure

**Files:**
- Modify: `references/onboard-flow.md` (H2, a first bullet; H5's A1 bullet)
- Modify: `evals/evals.json` (append `hf-file-is-the-served-file`)
- Test: `test_onboard_local_model_scripts.py` (a new test after `test_onboard_step_4_writes_the_settings_into_the_scratch_candidate` :1084; an eval test after `test_eval_hf_subfolder_no_fit_fixtures_match_their_assertions` :2156)

**Interfaces:**
- Consumes: the `SKILL.md` enabled-alias row from Task 4.

- [ ] **Step 1: Write the failing tests**

```python
def test_hugging_face_route_sends_the_baselines_own_file_to_the_re_measure() -> None:
    flow = _md(REFS / "onboard-flow.md")
    h2 = _section(flow, "H2. ", "H3. ")
    assert all(s in h2 for s in ("is the `file` of the baseline (step 2)", "`pick.bytes` equal to its `size_bytes`",
                                 "`SKILL.md`'s enabled-alias row", "before H3 and H4", "With another size", "report both sizes and stop"))
    assert h2.index("the `file` of the baseline") < h2.index('`fit: "unknown"`')
    assert "Under A1 (a live entry other than the baseline has this `file`)" in _section(flow, "H5. ", "### Scout pick")


def test_eval_hf_file_is_the_served_file_fixtures_match_their_assertions() -> None:
    gemma = next(m for m in yaml.safe_load((FIX / "models.yml").read_text())["models"] if m["alias"] == "gemma4")
    row = _yux_scout("8321", "12000", "--file", "Q4_K_M/gemma4-v2-Q4_K_M.gguf")
    assert (Path(row["pick"]["file"]).name, row["pick"]["bytes"], row["fit"]) == (gemma["file"], gemma["size_bytes"], "no")
    assert gemma["enabled"] is True and json.loads((FIX / "preflight-served.json").read_text())["budget"]["llmenv_budget"]["feasible"] is True
    url = f"https://huggingface.co/{YUX}/resolve/main/Q4_K_M/gemma4-v2-Q4_K_M.gguf"
    rt = json.loads(_run("bash", SCRIPTS / "resolve-target.sh", url, env={**os.environ, **FIX_ENV}).stdout)
    assert (rt["kind"], rt["revision"], rt["file"]) == ("hf_file", "main", "Q4_K_M/gemma4-v2-Q4_K_M.gguf")
    text = _asserts("hf-file-is-the-served-file")
    assert all(t in text for t in ("7381381664", "enabled-alias row", "references/served-alias.md", "gemma4-2", "feasible true"))
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/skills/onboard-local-model/test_onboard_local_model_scripts.py -q -k "baselines_own_file or served_file"`
Expected: FAIL.

- [ ] **Step 3: Edit H2 and H5**

Insert as H2's first bullet, before "`fit: "unknown"`":

```text
    - The base name of `pick.file` is the `file` of the baseline (step 2): with `pick.bytes` equal to its `size_bytes` it is
      the served model, not a candidate, so follow `SKILL.md`'s enabled-alias row (the re-measure) and stop here, before H3
      and H4. With another size it is a different file under the served file's name: report both sizes and stop (the
      models directory holds one file per name).
```

H5: "Under A1 (a live entry has this `file`) add nothing" becomes "Under A1 (a live entry other than the baseline has this `file`) add nothing".

- [ ] **Step 4: The eval**

Write `<repo>` out in full as `yuxinlu1/gemma-4-12B-agentic-fable5-composer2.5-v2-3.5x-tau2-GGUF`, and `<F>` as in Task 4.

```json
{
  "id": "hf-file-is-the-served-file",
  "prompt": "is https://huggingface.co/<repo>/resolve/main/Q4_K_M/gemma4-v2-Q4_K_M.gguf any better than what I run now?",
  "setup": "The fixture config is <F>/models.yml and its models directory is <F>/models/ (scripts read them through LLM_ENV_CONFIG and LLM_ENV_MODELS_DIR). hf-scout.sh reads its Hugging Face answers from HF_FIXTURE_DIR=<F>/hf/ instead of the network. preflight.sh already printed <F>/preflight-served.json; do not run it, use that JSON as its output. DRY RUN for evaluation: read-only commands only (resolve-target.sh, hf-scout.sh, cat, jq, yq); do not download weights, stop containers, edit models.yml or start benchmarks. Answer in English.",
  "files": ["evals/files/models.yml", "evals/files/preflight-served.json", "evals/files/hf/model-yuxinlu1__gemma-4-12B-agentic-fable5-composer2.5-v2-3.5x-tau2-GGUF@main.json", "evals/files/hf/tree-yuxinlu1__gemma-4-12B-agentic-fable5-composer2.5-v2-3.5x-tau2-GGUF@main.json"],
  "expected_output": "The file is the one the enabled gemma4 entry already serves (same base name and size), so it is not a candidate: the answer moves to the enabled-alias row and offers a re-measure of gemma4, with no research subagent, scratch config, new entry, lane against itself or replace verdict, and it never reports the served model as not fitting from hf-scout's screening class.",
  "assertions": [
    "resolve-target.sh gives kind hf_file, revision main, file Q4_K_M/gemma4-v2-Q4_K_M.gguf",
    "hf-scout.sh --repo <repo> --revision main --file Q4_K_M/gemma4-v2-Q4_K_M.gguf --json --budget-mib 8321 --ram-mib 12000 gives pick.bytes 7381381664: its base name gemma4-v2-Q4_K_M.gguf and its size equal the file and size_bytes of the enabled gemma4 entry, the baseline (H2)",
    "It is the served model, not a candidate: the answer follows SKILL.md's enabled-alias row (llm_server_enabled true in the preflight JSON) and offers the re-measure of gemma4 per references/served-alias.md",
    "No research subagent, scratch config, new entry or alias (no gemma4-2), candidate lane, bench of gemma4 against itself or replace verdict appears",
    "The fit no of that hf-scout call (7039 MiB plus about 1.5 GiB over 8321 MiB, a screening class) is not reported as gemma4 failing to fit: preflight's llmenv_budget says feasible true",
    "Nothing was downloaded, stopped, started or edited"
  ]
}
```

- [ ] **Step 5: Run and validate (no commit)**

Run: `uv run pytest tests/skills/onboard-local-model/test_onboard_local_model_scripts.py -q -k "baselines_own_file or served_file or step_4 or a1_to_a3 or eval_ or user_turns"`, then `make validate && make test`.
Expected: PASS and green.

### Task 6: The local GGUF as one linear run order, its url procedure, and the Steps checklist as a list

**Files:**
- Modify: `references/local-gguf.md` (whole file, text below)
- Modify: `references/onboard-flow.md` (Steps: the "Names" paragraph's last sentence :78; the progress checklist :80-82)
- Test: `test_onboard_local_model_scripts.py` (`test_local_gguf_reference_owns_the_name_check_copy_and_live_entry` :1131; `test_onboard_flow_names_alias_items_a1_to_a3` :1154; `test_routes_have_progress_checklists` :1101; `RETIRED_COPIES`)

**Interfaces:**
- Consumes: Task 3's paths, and Task 4's hand-back (checklist item 9).
- Produces: local-gguf items L1-L6 (L5 is the live entry, L6 the step 9 report). onboard-flow 4.3 still cites "`references/local-gguf.md`, L3", which is unchanged.

- [ ] **Step 1: Write the failing tests**

`test_local_gguf_reference_owns_the_name_check_copy_and_live_entry` becomes:

```python
def test_local_gguf_reference_owns_the_name_check_copy_and_live_entry() -> None:
    local = _md(REFS / "local-gguf.md")
    assert all(f"L{n}. " in local for n in range(1, 7)) and "L7. " not in local
    order = _section(local, "Run order", "L1. ")
    marks = [order.find(m) for m in ("L1 name check", "L2 scratch config", "Steps 1 to 4.2", "L3 fit, in place of Steps 4.4",
                                      "L4 copy, in place of Steps 4.3", "Steps 5 and 6", "L5 live entry", "Steps 7a to 8", "Step 9 with L6")]
    assert -1 not in marks and marks == sorted(marks), marks
    l1 = _section(local, "L1. ", "L2. ")
    assert all(s in l1 for s in ("`in_models_dir` true", "never copy over it", "the owner named that alias"))
    assert l1.index("A live entry has this `file`") < l1.index("`in_models_dir` true")
    l3 = _section(local, "L3. ", "L4. ")
    assert all(s in l3 for s in ("Fit, before any copy", "replaces Steps 4.4", "right after 4.2", "nothing is copied"))
    assert "asked only after L3 found the fit feasible" in _section(local, "L4. ", "L5. ")
    assert "Never move or delete the original" in _section(local, "L4. ", "L5. ")
    l5 = _section(local, "L5. ", "L6. ")
    assert all(s in l5 for s in ("decided before G3", "file://<original path>", "G-Q3", "`pick.sha256` equals its `sha256sum`",
                                 "`https://huggingface.co/<repo>/resolve/<sha>/<pick.file>`"))
    assert not re.search(r"^### Local GGUF", (REFS / "onboard-flow.md").read_text(), re.MULTILINE)
```

- In `test_onboard_flow_names_alias_items_a1_to_a3`, the last assert's sentence becomes `"A local GGUF runs these Steps in the run order of `references/local-gguf.md`, whose L items replace 4.3, 4.4 and 7a.2"`.
- In `test_routes_have_progress_checklists`, add:

```python
    raw = (REFS / "onboard-flow.md").read_text().split("## Steps", 1)[1].split("\n1. **Preflight", 1)[0]
    assert re.findall(r"^- (\d) ", raw, re.MULTILINE) == [str(n) for n in range(1, 10)] and "hand-back" in raw
```

- `RETIRED_COPIES` gains `"**Steps 1-9** in this order"`, `"with L6 live entry (7a.2)"`, `"at 4.4 (run before G1), G1, 7a.2 and 9"` and `"(the enabled alias: the re-measure)"`.

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/skills/onboard-local-model/test_onboard_local_model_scripts.py -q -k "local_gguf or a1_to_a3 or progress_checklists or stated_once"`
Expected: FAIL.

- [ ] **Step 3: Replace `local-gguf.md`**

The whole file becomes the text below. L1's first bullet, L2, L3, L4 and L6 keep their current wording, apart from the marked changes. No run-order line ends in a period, so no stray "L6. " lands in the folded text.

```text
# Local GGUF candidate

Loaded for a `.gguf` path (`resolve-target.sh`: `kind gguf_file`), before the Steps of `references/onboard-flow.md`
(`SKILL.md` Route). The run order below is the whole run: its L items replace Steps 4.3, 4.4 and 7a.2.

Ask yourself first:
- Is this file what its name claims? Size and quant come from the file and `resolve-target.sh`, not from the name.
- Is it a new model, another quant of one in models.yml, or a file models.yml already serves?
- What may I write? The original stays where the owner put it; only a copy in the models directory is ever served.

Run order (copy it into your task list; top to bottom, one line at a time):
1. L1 name check
2. L2 scratch config
3. Steps 1 to 4.2 (preflight, baseline, candidate, settings into `$S`)
4. L3 fit, in place of Steps 4.4
5. L4 copy, in place of Steps 4.3 (Gate G1)
6. Steps 5 and 6 (baseline lane, G2, screens)
7. L5 live entry: decide every key now, because Gate G3 shows it as its diff and Steps 7a.2 writes it
8. Steps 7a to 8
9. Step 9 with L6

L1. **Name check** (read-only) of the file's base name in `~/llm-workspace/models`:
    - A live entry has this `file` (`yq '.models[] | select(.file == "<base name>") | .alias' ~/.config/llm-env/models.yml`)
      and the file is the same (the models-dir file itself, or equal in size and `sha256sum`): the owner named that alias.
      Follow `SKILL.md`'s row for that alias and stop here.
    - `in_models_dir` true: it is already there. No copy and no G1 at L4.
    - Another file of that name is there: compare size and `sha256sum`. The same: no copy and no G1. Different: never copy
      over it; report both and stop.
L2. **Scratch config.** (unchanged)
L3. **Fit, before any copy.** (unchanged)
L4. **G1 is a copy, not a download**, ... (unchanged, with its three bullets)
L5. **The live entry**, decided before G3 and written at 7a.2. It never keeps the alias `candidate`.
    - `alias`: per `references/onboard-flow.md`, Alias for a new entry (A1-A3).
    - `label` and `parameters`: the human name and size (the scratch entry says `unknown`).
    - `quantization`: the file's quant token, else `unknown`.
    - `url`: ask the owner which Hugging Face repo the file came from. None named, or permissions bypassed:
      `file://<original path>`. With a repo, run
      `bash $SK/scripts/hf-scout.sh --repo <repo> --file <base name> --json --budget-mib <rule 1 budget> --ram-mib <rule 1 RAM>`
      (the bare name also finds a sub-folder file). When `pick.bytes` equals the file's size and `pick.sha256` equals its
      `sha256sum`, the url is `https://huggingface.co/<repo>/resolve/<sha>/<pick.file>` (`<sha>` is the row's `sha`;
      `<pick.file>` keeps its sub-folder). Exit 2, `fit: "unknown"` or a value that differs: report it and use
      `file://<original path>`.
    - `make setup` fetches only a `file` missing from the models directory, so the url is unused while the copy exists; with
      the copy pruned (`make prune`) and the original gone, setup fails with "download failed". A `file://` url also stops
      a later quant comparison until a Hugging Face url is pinned.
    - Another quant of the enabled model (A2 names it `<alias>-<quant lowercase>`, as `quant-headroom.sh add-entry` does):
      the decision stays step 8 and G5. They apply the same three conditions as Gate G-Q3 (fit, the replace rule on one seed
      set and build, pp/tg within about 3 %), and a yes leaves the old alias in models.yml, disabled, as G-Q3's yes does.
L6. **Step 9.** Never touch the original. The downloaded-file report covers only the copy G1 made (none when L1 found it in place).
```

(Write L2, L3 and L4 out from the current file. "(unchanged)" is this plan's shorthand only.)

- [ ] **Step 4: Steps in onboard-flow.md**

- The Names paragraph's last sentence, "A local GGUF also follows its own reference at 4.4 (run before G1), G1, 7a.2 and 9.", becomes "A local GGUF runs these Steps in the run order of `references/local-gguf.md`, whose L items replace 4.3, 4.4 and 7a.2."
- The one-line progress checklist becomes:

```text
Progress checklist (copy it into your task list; (b) = only with a baseline, step 2):
- 1 preflight (llm-server enabled but stopped: serve it first)
- 2 baseline
- 3 candidate
- 4 settings, G1, select, validate, budget
- 5 (b) baseline lane and build, then G2
- 6 (b) baseline screen, then candidate screen
- 7 G3 apply and serve; interference and card; ladder, (b) compare
- 8 verdict
- 9 G5, hand-back, downloaded-file report, leftover check
```

- [ ] **Step 5: Run and validate (no commit)**

Run: `uv run pytest tests/skills/onboard-local-model/test_onboard_local_model_scripts.py -q -k "local_gguf or a1_to_a3 or progress_checklists or stated_once or cite_each_other or nickname or ask_first or shell_logic or named_once or quoted_tilde or named_gguf"`, then `make validate && make test`.
Expected: PASS and green. `test_routes_have_progress_checklists` still finds no "served alias" in the Steps.

### Task 7: pytest's temp dir stays outside the repo (the rule written down, and a fail-fast check)

**Files:**
- Modify: `tests/skills/onboard-local-model/conftest.py`
- Modify: `AGENTS.md` ("### Scratch vs Durable Artifacts", a new last bullet)

- [ ] **Step 1: The check in conftest.py**

Add `pytest_configure(config: pytest.Config) -> None`. When `config.option.basetemp` is set and `Path(basetemp).resolve().is_relative_to(ROOT)` (`ROOT` from `skill_helpers`), raise `pytest.UsageError`. The message names the given basetemp and says: "these tests need pytest's temp dir outside the repo (they assert tmp_path is outside <repo>/tmp and outside any git tree): drop --basetemp or point it outside the repo". Docstring, one line: why the check exists.

- [ ] **Step 2: Verify it fails fast, then passes as usual**

Run: `uv run pytest tests/skills/onboard-local-model/test_latency_profile.py -q --basetemp=tmp/plan-n-basetemp; echo "exit $?"`
Expected: a usage error naming the rule, non-zero exit, no test run. Then `rm -rf tmp/plan-n-basetemp`.
Run: `uv run pytest tests/skills/onboard-local-model/test_latency_profile.py -q`
Expected: PASS.

- [ ] **Step 3: The rule in AGENTS.md**

New last bullet of "### Scratch vs Durable Artifacts":

```text
- pytest's own temp dir (`tmp_path`) is the exception: leave it at its default, outside the repo, and never pass
  `--basetemp` under `./tmp/`. Some tests assert that `tmp_path` lies outside `<repo>/tmp` and outside any git tree.
```

- [ ] **Step 4: Validate and commit the tracked line**

Run: `make validate && make test`. Expected: green.

```bash
git add AGENTS.md
git commit -m "docs(agents): keep pytest's temp dir outside ./tmp"
```

### Task 8: Verification and the plans index row

**Files:**
- Modify: `docs/superpowers/plans/README.md` (this plan's row)

- [ ] **Step 1: Guards and suites, one file at a time, default temp dir**

Run, one after the other:
- `uv run pytest tests/skills/onboard-local-model/test_onboard_local_model_scripts.py -q`
- `uv run pytest tests/skills/onboard-local-model/test_challenges.py -q`
- `uv run pytest tests/skills/onboard-local-model/test_latency_profile.py -q`
- `make validate && make test`

Expected: all green. The guard set in the first file is `-k "black_box or stated_once or cite_each_other or nickname or fit_math or route_table or router or shell_logic or named_once or user_turns or ask_first or a1_to_a3 or sums_every or stopped_llm_server"`.

- [ ] **Step 2: Byte cap and loads**

Run: `wc -c .claude/skills/onboard-local-model/SKILL.md`. Expected: 10,990, at most 11,000. Then `wc -l` on every reference. Fill the "What each route loads" table with the measured numbers; report any route more than 15 lines above its expected value.

- [ ] **Step 3: Read-only route walk with the fixtures**

From the repo root: `SK=.claude/skills/onboard-local-model F=$SK/evals/files`, with `LLM_ENV_CONFIG=$F/models.yml` and `LLM_ENV_MODELS_DIR=$F/models` unless a line says otherwise. `HF_FIXTURE_DIR=$F/hf` and `TMPDIR=tmp/plan-n-walk` apply to `hf-scout.sh`. For each route, run the commands and read only the files its `SKILL.md` row names, as a new agent would. Confirm that every file a step cites is one that row loads.

1. **Enabled but stopped, onboarding with a baseline** (`preflight-stopped.json`):
   - `resolve-target.sh https://huggingface.co/lab/Foo-9B-GGUF` gives `hf_repo`.
   - Step 1 cites the scoring rule: `make start-llama`, preflight again.
   - 5.2 runs the gemma4 lane, and G2 takes its target from the second read.
   - Step 9's hand-back runs `make stop-llama` after a G5 yes and after a no.
   - With `make start-llama` reporting the stack down, G-R1 states its facts, and a no ends at step 1.
2. **A candidate that is the served file** (`preflight-served.json`):
   - `resolve-target.sh https://huggingface.co/yuxinlu1/gemma-4-12B-agentic-fable5-composer2.5-v2-3.5x-tau2-GGUF/resolve/main/Q4_K_M/gemma4-v2-Q4_K_M.gguf` gives `hf_file`.
   - `hf-scout.sh --repo <that repo> --revision main --file Q4_K_M/gemma4-v2-Q4_K_M.gguf --json --budget-mib 8321 --ram-mib 12000` gives `pick.bytes` 7381381664 = gemma4's `size_bytes`.
   - H2 sends it to the enabled-alias row before H3 (whose `fit: "no"` is never reported) and before H4.
3. **Gateway-only host with an entry left enabled** (`LLM_ENV_CONFIG=$F/models-gateway-enabled.yml`, `preflight-gateway.json`):
   - `resolve-target.sh gemma4` gives `alias`, `enabled true`.
   - The row condition fails (`llm_server_enabled` false), so the other-alias row applies.
   - onboard-flow Alias: no baseline, budget 10369, RAM 14500, H = 0, G3 = `make llama-enable MODEL=gemma4`.
   - served-alias.md is never loaded.
4. **Served alias**:
   - Running (`preflight-served.json`): R3's target is `--below-mib 6712`.
   - Stopped (`preflight-stopped.json`): R1 cites the scoring rule, then R1 again; R6 ends with `make stop-llama`; an R4 reject runs no `make start-llama`.
5. **Local GGUF**:
   - `HOME=$F/home resolve-target.sh "~/Downloads/Bar-7B-Q4_K_M.gguf"` gives `gguf_file`.
   - The run order reaches L3, where the header-only file's `budget` error stops it before L4.
   - L5's url procedure decides the url before G3.
   - `resolve-target.sh "file://$PWD/$F/Foo-9B-Q4_K_M.gguf"` gives `gguf_file`.
   - `resolve-target.sh $F/Fo-9B-Q4_K_M.gguf` exits 2 with "did you mean $F/Foo-9B-Q4_K_M.gguf?".
6. **Quant comparison and tuning**:
   - `models-local-url.yml` still sends P0 to the repo question.
   - Enabled but stopped: the quant section cites the scoring rule after P0 and ends with `make stop-llama`.
   - T1/T9 do the same for tuning.
   - The fetch behaviour is pinned by Task 2's test, not walked.
7. **Scout, bare name, anchors, runtime, System One, challenges**:
   - `rank-candidates.sh` on the scout fixtures ranks lab-a 23 and lab-d 20, and selection-criteria step 4 carries the offload estimate.
   - `Qwen3.8-27B` exits 3, and Find the repo hands over to the Hugging Face row.
   - anchors.md cites only `references/scoring.md`.
   - Runtime and System One are unchanged.
   - The challenge row loads `references/scoring.md` to calibrate.
8. **Evals**: `jq -r '.evals[].id' $SK/evals/evals.json | wc -l` gives 36. Each eval with `files` has a `setup`.

Then `podman ps -a` lists the same containers as before the walk, and `tmp/plan-n-walk/` and `tmp/plan-n-basetemp/` are gone.

- [ ] **Step 4: Plans index row**

The row below was added with this plan, after the 2026-10-31-judge-round-11-fixes row; the rows of plans G-M are unchanged. On completion, change `PENDING` to `**DONE** (executed <date>; skill edits uncommitted with the untracked skill tree; judges re-run by the orchestrator; tracked commit <sha>)`.

```text
| 2026-10-31-judge-round-12-fixes | PENDING | fixes from skill-judge round 12 of onboard-local-model (`tmp/judges/r12-native-summary.txt` 88.8 % B, 5 medium, 8 low; `tmp/judges/r12-referee-summary.txt` 94.4 % A, its high refuted by the lead, one medium confirmed; bar: >= 90 % from both lanes, no critical or high) plus a structural pass over the class behind the native plateau (one host state handled by one route and missed by its siblings); findings verified against the files first (refuted and dropped ones in its ledger): a stopped llm-server and Gate G-R1 stated once in `scoring.md` ("Serving the lane's model") and cited by onboarding, the served-alias re-measure, tuning and the quant comparison, each handing llm-server back as found; the `SKILL.md` enabled-alias row requires llm-server enabled; a Hugging Face file that is the baseline's own file goes to the re-measure at H2; files named by path, with a tested pair list replacing the "never name each other" guard; fit math moved into `references/fit-math.md` (onboard routes about 492 -> 413 reference lines); the local GGUF as one linear run order with its url pinned by an `hf-scout.sh` size and sha256 check before G3; `resolve-target.sh` hints an early `.gguf` typo and accepts `file://`, and `quant-headroom.sh fetch` never resumes onto or deletes a file it did not download (red-first); four fixture-backed evals; `SKILL.md` stays under 11,000 bytes (measured draft 10,990); pytest's temp dir rule in `AGENTS.md` (tracked, one commit); skill edits stay uncommitted with the untracked skill tree; judges re-run by the orchestrator |
```

## Round-1 review outcome (approved, 1 confirmed high, 3 mediums)

Review round 1 (native lane on a scratch copy + referee-two, lead-verified; `tmp/plan-n-review/review-round-1.md`) confirmed one
high and three mediums; the referee's two criticals and one high were refuted (one critical kept as M2). Plan status: approved for
execution. The implementer applies these amendments in the task named; they override the task text where they differ.

- H1 (Task 6 Step 1): `test_routes_have_progress_checklists` still requires "Progress checklist" in `local-gguf.md`, whose new
  text says "Run order". Limit that loop to `served-alias` and assert
  `"Run order (copy it into your task list" in _md(REFS / "local-gguf.md")`.
- M1 (Task 4 Step 4, `SKILL.md`): the new rule is applied at the first preflight, so `SKILL.md` must load `scoring.md` there:
  "`references/scoring.md` at step 5" -> "at step 1" (:17), "at R2" -> "at R1" (row :21), "before T8 or Q8" -> "after P0"
  (quant row :27), "at T8" -> "at T1" (tune row :28). Saves 7 bytes (Task 4's measure becomes about 10,983). Task 4's test
  also asserts the four new phrases in `SKILL.md` and that none of "`references/scoring.md` at step 5", "at R2", "at T8",
  "before T8 or Q8" remains.
- M2 (Task 4 Step 4, tuning.md): the hand-back is the last step of whichever program runs, so llm-server always ends as found.
  - T9 gets no `make stop-llama` (the full program continues with Quant headroom, whose Q6, Q11 and G-Q3 yes start it again).
  - The standalone intro reads "(enabled but stopped: after P0, serve it per `references/scoring.md`, Serving the lane's
    model; the Hand-back bullet stops it again)".
  - Q1's no-fit branch: "`make start-llama` when llm-server is stopped, then the program ends" -> "`make start-llama` when
    llm-server was serving at P0 (or T1), then the Hand-back bullet".
  - A new last bullet after "G-Q3 yes": "Hand-back, the last step of the tuning program and of the standalone comparison
    (after G-Q3, or after Q1's no-fit verdict): when T1 or P0 found llm-server stopped, `make stop-llama`, so it ends as
    found; the report says that `make start-llama` serves it."
  - T1's sentence ends "...; the Hand-back bullet of Quant headroom stops it again." (not "T9 hands it back").
  - Checklist: "G-Q3; hand-back; leftover check".
  - Task 4's test: replace the T9 assertion with `"when T1 or P0 found llm-server stopped, `make stop-llama`"` in the section
    from "- G-Q3 yes" to the end, "when llm-server was serving at P0" in Q1, and `make stop-llama` absent from the T9 section.
- M3 (Task 7): the conftest check must also cover `TMPDIR`: check `config.option.basetemp`, else
  `PYTEST_DEBUG_TEMPROOT`, else `tempfile.gettempdir()`. Under xdist every worker gets a `--basetemp` from the controller
  (`<controller basetemp>/popen-gwN`): the message must name the path and say it comes from `--basetemp` or `TMPDIR`, never
  blame a flag the user did not pass, and the run exits non-zero with that message shown once (no per-worker tracebacks).
  Verify: without `-n` and with `-n 2`, each with `TMPDIR` under `./tmp/`, with `--basetemp` under `./tmp/`, and with neither
  (passes). The AGENTS.md bullet also says to keep `TMPDIR` outside `./tmp/` when running pytest.
- L2 (Task 1 Step 3): `lev_jq='def lev(...'` needs `# shellcheck disable=SC2016` on the line above it.
- L3/L4 (Task 7): the AGENTS.md bullet wraps at the section's ~76 columns and says what the tests do: they use paths under
  `tmp_path` as their "outside the repo tmp" and "not a git tree" cases.
- L5 (Task 4 Step 4): step 9's third bullet reads "After that restore, when step 1 found llm-server serving: `make start-llama`."
  (a declined G1 or G2 never stopped it). The test string follows.
- L6 (Task 2 Step 3): tuning.md Q2's sentence ends "...stops the fetch untouched (exit 1): report it, and ask the owner to move
  or delete it (a partial file an older fetch left there counts too)."
- D8-5 (taken, tuning.md only; Task 4): Latency profile's "in the background, polled at least every 5 minutes" becomes a run by one
  subagent (`SKILL.md` rule 2) with the Subagent brief adapted: its goal and command are this latency-profile run, llm-server
  stays serving `<a>`, and it returns the out dir, the profile JSON, the skipped sizes and the elapsed time. The `--out` stays
  named once.
- Not taken: L1 (harmless wording), L7 (T1 serving then T3 stopping is churn without harm; a gate redesign is the owner's call).
