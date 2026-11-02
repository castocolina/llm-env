# Judge Round 9 Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the confirmed findings of round 9 of the onboard-local-model skill judges (native 88.0 % B with 5 medium and 9 low; referee 94.4 % A, its high and mediums refuted by the lead, 3 lows). Also fix the four defect classes behind the native lane's 87-89.6 % plateau, so that the next round gives >= 90 % from both lanes with no critical or high finding.

**Architecture:** For four rounds the native judge has walked the three routes and found new instances of the same four classes. The classes are: a setting the flow names but never writes anywhere; two lanes or steps that share an output folder; served-alias exceptions threaded through every step; and an exit code that sends a typo to a Hugging Face search. This plan fixes each class at its root and pins it with a guard test:
- Step 4 writes its settings into the scratch candidate.
- `resolve-target.sh` no longer copies the baseline's own settings into the candidate.
- The served alias gets its own six-step list.
- Every benchmark `--out` is named once (a distinct-out guard).
- `resolve-target.sh` answers a near-miss alias, a models-dir file name or a non-GGUF URL with exit 2, never exit 3.
- `quant-headroom.sh` and `hf-scout.sh` handle an entry whose file sits in a sub-folder.

`SKILL.md` stays a router under its 11,000-byte cap: what it gains is paid for by shortening it.

**Tech Stack:** bash, jq, yq; pytest (`tests/skills/onboard-local-model/`); skill-judge rubric v4.

**Spec:** `tmp/judges/r9-native-summary.txt` and `tmp/judges/r9-referee-summary.txt` (round 9 verdicts; the referee file holds the lead's check of its high and mediums). Plan J (`docs/superpowers/plans/2026-10-31-judge-round-8-fixes.md`, executed) is the base state. The route walks below (2026-10-31, read-only, on the eval fixtures) add the structural findings S1-S8. The ledger restates every finding.

## Global Constraints

- English only. Edit only `.claude/skills/onboard-local-model/**`, `tests/skills/onboard-local-model/**` and this plan's row in `docs/superpowers/plans/README.md` (added with the plan; Task 8 updates its status). The skill tree and `tests/skills` are untracked and stay uncommitted. No tracked code changes, so nothing is committed. The README rows of plans G, H, I and J stay as they are.
- `SKILL.md` <= 11,000 bytes (`SKILL_MD_MAX_BYTES`, tested). Never raise the cap. Shorten `SKILL.md` before adding to it, and move text into references. Measured draft of Task 5 on a scratch copy: 10,973 -> 10,971 bytes, description 876 -> 923 characters (limit 1024, tested).
- A reference never names another reference file (`test_references_never_point_at_each_other`). Inside a reference, write "the effort-ladder reference", never `scoring.md`.
- `jev-fast-decision` is a black box, and ai-kit is never named in the skill (guard `test_skill_and_touched_docs_treat_jev_as_a_black_box`, which also scans every JSON file under `evals/`, the new fixtures included).
- Prose holds no shell logic. A backtick span may contain at most two of `;`, `&&` and `||` (`test_skill_prose_has_no_long_shell_logic`). Shell logic goes into scripts.
- Every script keeps `--help` (exit 0) and its tests. A change in behaviour is red-first: write the test, watch it fail, then implement. Pure coverage tests, marked as such, pass when written.
- After a `.sh` edit run `make validate`. After a `.py` edit (test files included) run `make validate && make test`. Run focused pytest suites one at a time, one file per command.
- Every new eval prompt is a user turn followed by one trailing `(Eval setup: ...)` that holds the fixture paths, premises and dry-run rules (Task 7 pins this for all evals).
- Scratch files go under `./tmp/`, and the run that creates them removes them. No containers, no GPU, no network, no downloads, no edits to the live `models.yml` or to any capacity card.

## Findings ledger (verified 2026-10-31 against the files)

| Finding | Verdict | Task |
|---|---|---|
| native D8-1: a model onboarded from an HF sub-folder dies in `quant-headroom.sh:55` ("differs from its file; not supported"); tuning.md P0 is silent | confirmed. The entry template writes `url .../resolve/<sha>/<pick.file>` with `file` = base name, and `hf-scout.sh` emits sub-folder paths (`test_hf_scout_reads_gguf_files_in_sub_folders`). Also, P0 runs `hf-scout.sh --file <entry file>`, an exact-path match (`hf-scout.sh:108`), so a sub-folder file exits 2 there too, and P0 pins `resolve/<sha>/<entry file>`, which drops the sub-folder | 2 |
| native D8-2: step 4 settings (ctx_size, KV, n_cpu_moe, sampling) have no destination | confirmed. onboard-flow.md:81-84 names no file. `resolve-target.sh:130-132` copies the baseline's `n_cpu_moe`, `sampling`, `benchmark`, `parameters` (and its `quantization` when the file name has no token) into `candidate`. The KV type is the global `runtime.cache_type_k/v` (`tune-apply.sh:35`), so "KV `q5_1` unless `q8_0`" would change the baseline too | 1, 3 |
| native D8-3: the effort-ladder brief hardcodes `--out tmp/model-scout/<stamp>/effort` | confirmed. scoring.md:36 is the same literal as tuning.md:26 (T8); the onboard lanes use `effort-baseline` / `effort`, Q8 uses `QR/<Q>/effort`; `effort-run.sh:121-123` refuses a used folder (exit 2) | 4 |
| native D8-4: a mistyped alias exits 3 and goes to an HF search | confirmed by a read-only run on `evals/files/models.yml`: `Gemma4`, `gemma`, `GEMMA4`, `Ornith-35B` and `ornith-35` exit 3. `gemma4-v2-Q4_K_M.gguf` (in `$LLM_ENV_MODELS_DIR`, not in the cwd) gives "not found" with no hint | 1 |
| native D2-1: served-alias exceptions run through steps 4-9; H5 is one nested sentence | confirmed: served-alias clauses at onboard-flow.md:25-29, :104, :111, :115, :118, :136, :149, :164 | 3 |
| native D1-1 (low): generic evidence ladder (selection-criteria.md:76-89), "Set expectations honestly" (anchors.md:27-28) | confirmed for section 4, which restates section 7's `agentic_evidence` levels. anchors.md keeps its host-specific target (about 10 GiB) in one line | 6 |
| native D3-1 (low): Gotchas holds two items | taken for the global KV key. The other two facts it names (sub-folder url, alias typo) are fixed by Tasks 1-2 instead of documented | 5 |
| native D4-1 (low): description lacks "LLM", "download", "run locally" | taken: the judge's replacement text, 923 characters | 5 |
| native D5-1 (low): SKILL.md:35-36 override clause; references reach scoring.md by nickname | override clause: taken (dropped). Naming scoring.md by path inside references: refuted, because references must not name each other (guard test, one-level rule) and `SKILL.md` already names `references/scoring.md` in every row that needs it | 5 |
| native D6-1 (low): rule 2 sends every `bench-fit.sh` run through a subagent | confirmed. `bench-fit.sh` bounds itself (`--timeout`, default 3600) and removes its container in an EXIT trap: a single screen may run inline | 5 |
| native D7-1 (low): no eval for an HF sub-folder entry, a second lane's out dir, an alias typo | taken: three fixture-backed evals | 1, 2, 4 |
| native D8-5 (low): a split GGUF (null `pick.sha256`) stops before G1 with no next step | confirmed (H2, template note) | 3 |
| native D8-6 (low): `.../resolve/main/README.md` becomes `hf_repo` | confirmed by a run: `{"kind":"hf_repo","repo":"lab/Foo-9B-GGUF"}`, against the header's "non-model URL exits 2" | 1 |
| native D8-7 (low): step 9 never reports the downloaded GGUF | confirmed | 3 |
| referee D8-1 (high): skip Q10 on a Q7 failure | refuted by the lead and re-checked: `compare_quant` (`pylib/capacity.py:410-444`) needs only the candidate's depth section, gives null deltas for missing ladder data, and copies `interference_passed` (the record Q10 exists for). Low polish taken: "G-Q3 still runs" | 2 |
| referee D8-2 (medium): G-Q3 after "stop the larger quants" | refuted (tuning.md:137-138, :172). Same polish as above | 2 |
| referee D7-1 (medium): no train/validation split | refuted (the split is optional advice for description optimization). Residual low taken: 11 + 11 -> 10 + 10, test bound 8-10 per side | 7 |
| referee D7-2 (low): eval prompts read like test setup | confirmed: 20 of 24 prompts carry fixture paths, premises or dry-run rules in the user turn | 7 |
| referee D5-1 (low): `scripts/require-tmp-out.sh` not in the route table | dropped. A sourced helper is not a load-time reference, its header names the five scripts that source it, and `SKILL.md` has no bytes to spare | - |
| referee D1-1 (low): SKILL.md:14 partly repeats the route table | taken as a shortening. The command stays, because only one row names `resolve-target.sh` | 5 |
| S1 (walk): step 6 runs "the same command" for baseline and candidate, so both write `tmp/model-scout/<run>/bench` (`devices.txt`, `presets.ini` overwritten) | confirmed (`bench-fit.sh:42-52`) | 3 |
| S2 (walk): P3 runs "T8's ladder command", whose `<stamp>` is a sweep stamp a standalone comparison does not have | confirmed | 4 |
| S3 (walk): the scout and anchors routes use `<run>` = `<YYYYMMDD>-<alias>` with no alias; anchor lanes name no `--out` at all | confirmed | 4 |
| S4 (walk): runtimes.md says "measure with `bench-fit.sh`" for `-ub`/KV, but `bench-fit.sh` takes every flag from the rendered presets; `--no-mmap`/`--mlock` have no models.yml key | confirmed (`pylib/presets.py:41-44`, no mmap/mlock key anywhere) | 6 |
| S5 (walk): the tuning procedure has no leftover check | confirmed | 3 |
| S6 (walk): a bare model stem present in the models dir (`gemma4-v2-Q4_K_M`) exits 3 to a search | confirmed by a run | 1 |
| S7 (walk): a second onboarding of the same alias on one day reuses `<run>`, so `effort-run.sh` exits 2 | confirmed (`<run>` = `<YYYYMMDD>-<alias>`) | 4 |
| S8 (walk): step 4 "16384 if transcripts end with `stopReason: length`" when no transcript exists yet | confirmed (wording) | 3 |

## Owner decisions (defaults chosen)

1. **Sub-folder entries are mapped, not refused.** `quant-headroom.sh` accepts an entry url whose path ends in `/<file>`. The candidate path is that path with the quant token replaced (case-insensitive), and the candidate's file is the path's base name. It downloads to the models directory under that base name. When the base name would equal the current file (the token sits only in the folder name), the candidate gets the reason `quant token not in file name` and is never downloaded over the current file. `hf-scout.sh --file NAME` keeps exact matching first; a bare file name with no exact match takes the first tree file ending in `/NAME`.
2. **Step 4 settings go to the scratch candidate.** After the fit math, `yq -i` writes `ctx_size`, `client_max_output_tokens`, `n_cpu_moe` (or deletes it) and `sampling` (or deletes it) into the candidate entry of `$S`, before G1 and `models select`. 7a's live entry takes the same values. An existing alias (Alias row, or Hugging Face rule 1) keeps its own settings. The KV type is never changed by onboarding: it is global, and only the tune route changes it.
3. **The `candidate` copy keeps only host settings.** `resolve-target.sh --scratch-config` on a `.gguf` copies the baseline entry without its `n_cpu_moe`, `sampling` and `benchmark`. It writes `quantization` from the file's token, else `unknown`, and `parameters` as `unknown`. `ctx_size`, `client_max_output_tokens`, `n_gpu_layers` and `vram_budget` stay as placeholders that step 4 overwrites.
4. **Near-miss alias.** Both sides are lowercased and stripped of every character outside `[a-z0-9]`. A bare name that equals an alias that way, or that has >= 3 such characters and starts an alias, exits 2: `<MODEL>: no models.yml alias of that name; did you mean alias <names>? (if not, the owner can ask for a Hugging Face search by name)`. `<names>` is up to 3 aliases in C sort order, joined as `a`, `a or b`, `a, b or c`. The owner's "no, search it" goes to Find the repo. An edit-distance typo (`gema4`) still exits 3: `shortcut: prefix and case only, add an edit distance if real typos keep reaching the search`.
5. **Models-dir names are hinted, not accepted.** A bare `.gguf` name with no hint in the cwd takes `closest_gguf "$MODELS_DIR" NAME` as its hint. A bare stem with `$MODELS_DIR/<stem>.gguf` present exits 2: `<MODEL>: no models.yml alias of that name; did you mean <MODELS_DIR>/<MODEL>.gguf?`.
6. **Non-GGUF file URL.** A `resolve|blob/<rev>/<path>` URL whose path is not `.gguf` exits 2: `not a .gguf file URL: <MODEL>. <forms>`.
7. **Served alias.** It gets its own list R1-R6 after Steps. R5 keeps Gate G3 as a no-edit serve gate, as eval `served-alias-argument` expects.
8. **One `--out` per lane.**
   - The effort-ladder brief takes `<out>`, and a re-run takes `<out>-r2`, `-r3`...
   - The onboard baseline screen uses `tmp/model-scout/<run>/bench-baseline`, and P3 uses `QR/base/effort`.
   - Anchor lanes use `tmp/model-scout/<run>/effort-<alias>` and `.../effort-<anchor>` (the id with `/` as `-`), with `<run>` = `<YYYYMMDD>-anchors`. The scout route's `<run>` is `<YYYYMMDD>-scout`.
   - A second onboarding of one alias on one day uses `<run>-2`.
9. **Rule 2:** one `bench-fit.sh` screen may run inline. `tune-sweep.sh`, `effort-run.sh` and `latency-profile.sh` stay delegated.
10. **Description:** the native judge's replacement text (923 characters).
11. **Trigger queries 10 + 10:** merge the runtime and System One positives into one query, and drop the GGUF-header negative (it covers no boundary key).
12. **Eval prompts:** a user turn, then `(Eval setup: ...)` holding the setup text unchanged.

## Review Focus

1. A real Hugging Face name that matches an alias after normalization, or starts one (`Gemma-4`, `orni`), now exits 2 instead of searching. Expected: the agent reports the suggestion, and the owner's "no, search it" sends the name to Find the repo, never to a dead end. Pinned in Task 1 (Find the repo states that route; `gemma-4-27b-it` still exits 3).
2. A sub-folder whose quant token sits only in the folder name (`Q4_K_M/model.gguf`). Expected: the candidate gets the reason `quant token not in file name`, and nothing is fetched over the current file. Pinned in Task 2 (`test_quant_headroom_never_maps_a_candidate_onto_the_current_file_name`).
3. A dense Local GGUF screened beside a MoE baseline, or the reverse. Expected: the candidate carries no inherited `n_cpu_moe`, and step 4 sets it only for a MoE model. Pinned in Task 1 (the copy drops it) and Task 3 (`del(.n_cpu_moe)` in step 4).
4. An inline `bench-fit.sh` screen that hangs. Expected: `--timeout` ends it, its EXIT trap removes the container, and the leftover check finds nothing. Text in Task 5 (rule 2 names the bound). The trap is covered by the existing bench-fit tests.
5. The same alias onboarded twice on one day. Expected: the second run uses `<run>-2`, never a used `--out` (`effort-run.sh` would exit 2). Pinned in Task 4 (`test_every_benchmark_out_dir_is_named_once` checks the `<run>-2` sentence).

---

### Task 1: `resolve-target.sh` never routes a typo to a search, and the candidate keeps only host settings

**Files:**
- Modify: `.claude/skills/onboard-local-model/scripts/resolve-target.sh:4-17` (header), `:76-86` (URL branch), `:102-103` (hint), `:109` (exit 3 branch), `:129-132` (candidate copy)
- Modify: `.claude/skills/onboard-local-model/references/selection-criteria.md:8` (Find the repo)
- Modify: `.claude/skills/onboard-local-model/evals/evals.json` (append `alias-typo`)
- Test: `tests/skills/onboard-local-model/test_onboard_local_model_scripts.py` (`test_resolve_target_parses_hugging_face_urls` :1567; new tests after `_asserts` :1708)

**Interfaces:**
- Produces (stderr, exit 2, empty stdout):
  - `<MODEL>: no models.yml alias of that name; did you mean alias <names>? (if not, the owner can ask for a Hugging Face search by name)`
  - `<MODEL>: no models.yml alias of that name; did you mean <MODELS_DIR>/<MODEL>.gguf?`
  - `not a .gguf file URL: <MODEL>. <forms>`
  - A bare `.gguf` "not found" message gains `; did you mean <MODELS_DIR>/<file>?` when only the models dir has a close file.
- Produces: the scratch `candidate` entry has no `n_cpu_moe`, `sampling` or `benchmark`; its `quantization` is the file's token or `unknown`, and its `parameters` is `unknown` (owner decision 3). Task 3 relies on this.
- Exit 3 stays for a bare name with no near-miss alias and no models-dir file.

- [ ] **Step 1: Write the failing tests**

In `test_resolve_target_parses_hugging_face_urls`, before the `for other in` loop:

```python
    for doc in ("https://huggingface.co/o/r/resolve/main/README.md", "https://huggingface.co/o/r/blob/main/sub/config.json"):
        result = _rt(tmp_path, doc)
        assert (result.returncode, result.stdout) == (2, "") and "not a .gguf file URL" in result.stderr, doc
```

After `_asserts`:

```python
FIX_ENV = {"LLM_ENV_CONFIG": str(FIX / "models.yml"), "LLM_ENV_MODELS_DIR": str(FIX / "models")}


@pytest.mark.parametrize(("model", "code", "names"), [("Gemma4", 2, "gemma4"), ("gemma", 2, "gemma4"), ("GEMMA-4", 2, "gemma4"),
                                                      ("Ornith-35B", 2, "ornith-35b"), ("ornith-3", 2, "ornith-35b"),
                                                      ("orn", 2, "ornith or ornith-35b"), ("gemma-4-27b-it", 3, None), ("Qwen3.8-27B", 3, None)])
def test_resolve_target_names_a_near_miss_alias_instead_of_a_search(model: str, code: int, names: str | None) -> None:
    result = _run("bash", SCRIPTS / "resolve-target.sh", model, env={**os.environ, **FIX_ENV}, cwd=ROOT)
    assert (result.returncode, result.stdout) == (code, ""), result.stderr
    if names:
        assert f"no models.yml alias of that name; did you mean alias {names}?" in result.stderr
        assert "search it as a model name" not in result.stderr


def test_resolve_target_hints_the_models_dir_for_a_bare_file_name() -> None:
    want = f"did you mean {FIX / 'models' / 'gemma4-v2-Q4_K_M.gguf'}?"
    for arg in ("gemma4-v2-Q4_K_M.gguf", "gemma4-v2-Q4_K_M"):
        result = _run("bash", SCRIPTS / "resolve-target.sh", arg, env={**os.environ, **FIX_ENV}, cwd=ROOT)
        assert (result.returncode, result.stdout) == (2, "") and want in result.stderr, (arg, result.stderr)


def test_resolve_target_candidate_drops_the_baselines_own_settings(tmp_path: Path) -> None:
    env = _rt_env(tmp_path)
    cfg = yaml.safe_load(Path(env["LLM_ENV_CONFIG"]).read_text())
    cfg["models"][0]["n_cpu_moe"] = 28
    Path(env["LLM_ENV_CONFIG"]).write_text(yaml.safe_dump(cfg))
    gguf = tmp_path / "plain.gguf"
    gguf.write_bytes(GGUF_BYTES)
    scratch = _out(tmp_path, "s") / "scratch.yml"
    result = _run("bash", SCRIPTS / "resolve-target.sh", gguf, "--scratch-config", scratch, env=env)
    assert result.returncode == 0, result.stderr
    (entry,) = [m for m in yaml.safe_load(scratch.read_text())["models"] if m["alias"] == "candidate"]
    assert not {"n_cpu_moe", "sampling", "benchmark"} & set(entry)
    assert (entry["quantization"], entry["parameters"]) == ("unknown", "unknown")
    assert all(entry[k] == cfg["models"][0][k] for k in ("ctx_size", "client_max_output_tokens", "n_gpu_layers", "vram_budget"))


def test_eval_alias_typo_fixture_matches_its_assertions() -> None:
    result = _run("bash", SCRIPTS / "resolve-target.sh", "Gemma4", env={**os.environ, **FIX_ENV}, cwd=ROOT)
    want = "no models.yml alias of that name; did you mean alias gemma4?"
    assert (result.returncode, result.stdout) == (2, "") and want in result.stderr
    assert all(s in _asserts("alias-typo") for s in ("exits 2", want, "hf-scout.sh --query is not run"))
    find = _section(_md(REFS / "selection-criteria.md"), "## Find the repo", "## Scout and rank")
    assert "near-miss alias" in find and "no, search it" in find
```

`test_resolve_target_exits_3_only_for_a_bare_model_name`, `test_resolve_target_rejects_anything_else`, `test_resolve_target_writes_a_scratch_config` and the bare-name eval tests stay unchanged and must still pass.

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/skills/onboard-local-model/test_onboard_local_model_scripts.py -q -k "hugging_face_urls or near_miss or models_dir_for or own_settings or alias_typo"`
Expected: FAIL. The near-miss and stem cases exit 3, the README URL gives `hf_repo`, the candidate keeps `sampling` and `benchmark`, and the eval is missing.

- [ ] **Step 3: Implement in `resolve-target.sh`**

- URL branch (:76): match `resolve|blob/<rev>/<path>` first. A non-`.gguf` path calls `fail "not a .gguf file URL: $model. $forms"`. A `.gguf` path keeps today's `hf_file` result. Every other URL keeps the `hf_repo` / fail branches.
- Hint (:102): after `hint="$(closest_gguf "$dir" "$name")"`, an argument with no directory part (`dir` empty) and no hint takes `closest_gguf "$MODELS_DIR" "$name"`.
- Exit 3 branch (:109): for a model that is not `ORG/NAME`, check two things before exit 3, in this order. First the near-miss aliases of owner decision 4 (only when `$CONFIG_PATH` exists; read the aliases with `yq`, match and join with `jq`). Then `[ -f "$MODELS_DIR/$model.gguf" ]` (owner decision 5). Both call `fail` with the Interfaces messages.
- Candidate copy (:129-132): pipe the copied entry through `del(.n_cpu_moe, .sampling, .benchmark)`, change the quantization fallback from `$t.quantization` to `"unknown"`, and add `.parameters = "unknown"`.
- Header: in (2), "else hf_repo" becomes "a resolve|blob URL of any other file exits 2, else hf_repo". In (4), add "with the closest .gguf of its directory, or of the models dir for a bare name, as a hint". After (5), add "(5b) a bare name that matches an alias case- and separator-insensitively, or starts one (3+ characters), exits 2 naming up to 3 aliases; a bare name whose <models dir>/<name>.gguf exists exits 2 naming it". In the `--scratch-config` text, "copied from the first enabled entry (else the first)" gains "without its n_cpu_moe, sampling and benchmark", and "quantization" becomes "quantization (the file's token, else unknown), parameters unknown".

- [ ] **Step 4: Edit the Find the repo text and append the eval**

- selection-criteria.md:8: "`resolve-target.sh` exits 3 on a bare model name (exit 2 is an error: report it and stop)." becomes: "`resolve-target.sh` exits 3 on a bare model name. Its exit 2 is an error: report it and stop. When the message names a near-miss alias (`did you mean alias <x>?`), the owner's answer decides: that alias goes to `SKILL.md`'s alias row, and a "no, search it" comes here."
- evals.json, append:

```json
{
  "id": "alias-typo",
  "prompt": "re-measure Gemma4 for me, I think it got slower at long context. Just tell me what you would do; change nothing. (Eval setup: the config is .claude/skills/onboard-local-model/evals/files/models.yml and its models directory .claude/skills/onboard-local-model/evals/files/models/; pass them as LLM_ENV_CONFIG and LLM_ENV_MODELS_DIR.)",
  "files": ["evals/files/models.yml"],
  "expected_output": "resolve-target.sh Gemma4 exits 2 and names the alias gemma4; the answer asks whether gemma4 was meant and stops, with no Hugging Face search.",
  "assertions": [
    "resolve-target.sh Gemma4 exits 2 with no models.yml alias of that name; did you mean alias gemma4?",
    "Exit 2 is treated as an error: the answer asks whether gemma4 was meant and stops; hf-scout.sh --query is not run",
    "No scratch config, download, gate, container or configuration change was made"
  ]
}
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/skills/onboard-local-model/test_onboard_local_model_scripts.py -q -k "resolve_target or eval_ or help or missing_required"`
Expected: PASS.

- [ ] **Step 6: Validate (no commit: untracked tree)**

Run: `make validate && make test`. Expected: shellcheck clean, suite green.

### Task 2: The quant route handles a sub-folder entry (D8-1)

**Files:**
- Modify: `.claude/skills/onboard-local-model/scripts/quant-headroom.sh:13-15` (header), `:55` (`load_entry`), `:81-90` (`candidate_row`), `:203-211` (`cmd_fetch`), `:235-237` (`cmd_add_entry`)
- Modify: `.claude/skills/onboard-local-model/scripts/hf-scout.sh:12` (header), `:108` (`--file` match)
- Modify: `.claude/skills/onboard-local-model/references/tuning.md:98-101` (Quant headroom intro), `:112-123` (P0), `:158-159` (Q7)
- Create: `.claude/skills/onboard-local-model/evals/files/models-subfolder.yml`, `evals/files/hf/model-yuxinlu1__gemma-4-12B-agentic-fable5-composer2.5-v2-3.5x-tau2-GGUF.json`, `evals/files/hf/tree-yuxinlu1__gemma-4-12B-agentic-fable5-composer2.5-v2-3.5x-tau2-GGUF@190a31365a6b80a692349be34ccdac730cad4fe4.json`
- Modify: `.claude/skills/onboard-local-model/evals/evals.json` (append `quant-room-subfolder-entry`)
- Test: `tests/skills/onboard-local-model/test_onboard_local_model_scripts.py` (new tests after `test_quant_headroom_rejects_bad_usage` :1526, after `test_hf_scout_named_split_file_sums_bytes_and_has_no_single_checksum` :1947, and after `test_eval_local_url_fixture_matches_its_assertions` :1880)

**Interfaces:**
- Produces: a `candidates.json` row gains `path` (the tree path) only when it differs from `file` (a base name). `compare_quant` keeps matching on `file`, and the existing top-level tests see unchanged rows.
- Produces: `hf-scout.sh` `pick.file` is the tree path (unchanged). `--file NAME` also finds `<dir>/NAME`.

- [ ] **Step 1: Write the failing tests**

```python
QH_SUB_URL = f"https://huggingface.co/o/r/resolve/{QH_SHA}/Q4_K_M/m-Q4_K_M.gguf"


@pytest.mark.usefixtures("real_model_tune_untouched")
def test_quant_headroom_maps_an_entry_url_in_a_sub_folder(tmp_path: Path) -> None:
    good = hashlib.sha256(b"known-bytes").hexdigest()
    tree = _qh_tree({"Q4_K_M/m-Q4_K_M.gguf": 5512 * MIB, "Q5_K_M/m-Q5_K_M.gguf": 6334 * MIB}) \
        + [{"type": "file", "path": "Q8_0/m-Q8_0.gguf", "size": 11, "lfs": {"oid": good}}]
    entry = dict(QH_CONFIG["models"][0], url=QH_SUB_URL)  # type: ignore[index]
    env = _qh_env(tmp_path, config=QH_CONFIG | {"models": [entry]}, tree=tree, curl=True)
    result = _qh(env, "check", "--alias", "m", "--quants", "Q5_K_M", "--vram-available-mib", "8321")
    assert result.returncode == 0, result.stderr
    (q5,) = json.loads(_candidates(tmp_path)[0].read_text())["candidates"]  # json.loads, not [1]: pyright types that as object
    assert (q5["file"], q5["path"], q5["size_bytes"]) == ("m-Q5_K_M.gguf", "Q5_K_M/m-Q5_K_M.gguf", 6334 * MIB)
    assert _qh(env, "fetch", "--alias", "m", "--quant", "Q8_0").returncode == 0
    assert (tmp_path / "models" / "m-Q8_0.gguf").read_bytes() == b"known-bytes"
    assert any(f"resolve/{QH_SHA}/Q8_0/m-Q8_0.gguf" in c for c in _calls(tmp_path))
    assert _qh(env, "add-entry", "--alias", "m", "--quant", "Q5_K_M").returncode == 0
    new = next(m for m in yaml.safe_load(Path(env["LLM_ENV_CONFIG"]).read_text())["models"] if m["alias"] == "m-q5_k_m")
    assert (new["file"], new["url"]) == ("m-Q5_K_M.gguf", f"https://huggingface.co/o/r/resolve/{QH_SHA}/Q5_K_M/m-Q5_K_M.gguf")


@pytest.mark.usefixtures("real_model_tune_untouched")
def test_quant_headroom_never_maps_a_candidate_onto_the_current_file_name(tmp_path: Path) -> None:
    entry = dict(QH_CONFIG["models"][0], url=f"https://huggingface.co/o/r/resolve/{QH_SHA}/Q4_K_M/model.gguf", file="model.gguf")  # type: ignore[index]
    env = _qh_env(tmp_path, config=QH_CONFIG | {"models": [entry]}, tree=_qh_tree({"Q4_K_M/model.gguf": 5512 * MIB, "Q5_K_M/model.gguf": 6334 * MIB}))
    result = _qh(env, "check", "--alias", "m", "--quants", "Q5_K_M", "--vram-available-mib", "8321")
    assert result.returncode == 0, result.stderr
    assert _candidates(tmp_path)[1]["candidates"] == [{"quant": "Q5_K_M", "reason": "quant token not in file name"}]
    other = tmp_path / "other"
    other.mkdir()
    bad = dict(QH_CONFIG["models"][0], url=f"https://huggingface.co/o/r/resolve/{QH_SHA}/sub/other.gguf")  # type: ignore[index]
    result = _qh(_qh_env(other, config=QH_CONFIG | {"models": [bad]}), "check", "--alias", "m", "--vram-available-mib", "8321")
    assert result.returncode == 1 and "does not end in its file" in result.stderr


def test_hf_scout_file_takes_a_bare_name_in_a_sub_folder(tmp_path: Path) -> None:
    (tmp_path / "model-lab__x.json").write_text(json.dumps(_repo("lab/x", 8, 262144, "qwen35")))
    (tmp_path / "tree-lab__x.json").write_text(json.dumps(_lfs_tree(("Q4_K_M/m-Q4_K_M.gguf", 5000, "4" * 64), ("m-Q5_K_M.gguf", 6000, "5" * 64),
                                                                    ("old/m-Q5_K_M.gguf", 6000, "6" * 64))))
    base = ("bash", SCRIPTS / "hf-scout.sh", "--json", "--budget-mib", "10369", "--ram-mib", "14000", "--repo", "lab/x", "--file")
    pick = lambda f: json.loads(_run(*base, f, env={"HF_FIXTURE_DIR": str(tmp_path), "PATH": "/usr/bin:/bin"}).stdout)[0]["pick"]
    assert (pick("m-Q4_K_M.gguf")["file"], pick("m-Q4_K_M.gguf")["sha256"]) == ("Q4_K_M/m-Q4_K_M.gguf", "4" * 64)
    assert pick("m-Q5_K_M.gguf")["file"] == "m-Q5_K_M.gguf", "an exact path wins over a sub-folder copy"


def test_quant_p0_pins_the_repo_path_and_names_hf_scout_exit_2() -> None:
    tuning = _md(REFS / "tuning.md")
    p0 = _section(tuning, "P0. The entry url", "- P1.")
    assert "`<pick.file>` keeps its sub-folder" in p0 and "Exit 2 (no file of that name" in p0 and "a top-level file" not in p0
    assert "G-Q3 still runs" in tuning and "sub-folder" in _section(tuning, "## Quant headroom", "### Standalone")


@pytest.mark.usefixtures("real_model_tune_untouched")
def test_eval_subfolder_quant_fixture_matches_its_assertions(tmp_path: Path) -> None:
    base = yaml.safe_load((FIX / "models.yml").read_text())
    gemma = next(m for m in base["models"] if m["alias"] == "gemma4")
    gemma["url"] = gemma["url"].replace("/gemma4-v2-Q4_K_M.gguf", "/Q4_K_M/gemma4-v2-Q4_K_M.gguf")
    sub = yaml.safe_load((FIX / "models-subfolder.yml").read_text())
    assert sub == base
    env = _qh_env(tmp_path, config=sub) | {"HF_FIXTURE_DIR": str(FIX / "hf")}
    result = _qh(env, "check", "--alias", "gemma4", "--vram-available-mib", "8321")
    assert result.returncode == 0, result.stderr
    rows = {c["quant"]: c for c in json.loads(_candidates(tmp_path)[0].read_text())["candidates"]}
    assert (rows["Q5_K_M"]["path"], rows["Q5_K_M"]["file"], rows["Q5_K_M"]["size_bytes"]) == (
        "Q5_K_M/gemma4-v2-Q5_K_M.gguf", "gemma4-v2-Q5_K_M.gguf", 8547884576)
    assert rows["Q6_K"] == {"quant": "Q6_K", "reason": "sharded, not supported"}
    assert all(s in _asserts("quant-room-subfolder-entry") for s in (gemma["url"], "Q5_K_M/gemma4-v2-Q5_K_M.gguf", "8547884576",
                                                                       "sharded, not supported", "G-Q0 is not asked"))
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/skills/onboard-local-model/test_onboard_local_model_scripts.py -q -k "sub_folder or current_file_name or bare_name_in or p0_pins or subfolder_quant"`
Expected: FAIL. `check` exits 1 "not supported", `hf-scout.sh` exits 2 on the bare name, the P0 text and the fixtures are missing.

- [ ] **Step 3: Implement the scripts**

- `load_entry` (:55): `[ "${path##*/}" = "$file" ] || die "entry url path '$path' does not end in its file '$file'; fix the entry's url or file"`.
- `candidate_row`: pass `--arg p "$path"`. The algorithm (owner decision 1): `($p | gsub($tok; $q; "i")) as $cp | ($cp | sub(".*/"; "")) as $cf`. Look the hit up by `.path == $cp`. `$cf == $f` gives `quant token not in file name`. A hit row is `{quant, file: $cf, size_bytes, sha256, params_total}` plus `{path: $cp}` only when `$cp != $cf`. The sharded and `not in repo` branches stay.
- `cmd_fetch`: `cpath="$(jq -r '.path // .file' <<<"$row")"`. The dest stays `$MODELS_DIR/$cfile`; the URL becomes `.../resolve/$sha/$cpath`.
- `cmd_add_entry`: `F` stays the base name; `U` uses `.path // .file`.
- `budget_for` needs no change: it already uses the base name (`.file`).
- Header (:13-15): "Candidate file = the entry's file with its quantization token replaced" becomes "Candidate path = the entry's url path (a sub-folder kept) with its quantization token replaced, case-insensitively; its base name is the candidate's file, downloaded to the models dir under that name (a base name equal to the entry's file is `quant token not in file name`); candidates.json carries `path` only when it differs from `file`. An entry url whose path does not end in its file exits 1".
- hf-scout.sh:108: `| if $f == "" then . elif any(.[]; .file == $f) then map(select(.file == $f)) else map(select(.file | endswith("/" + $f))) end) as $q`. Header :12: "(the first shard of a split one)" becomes "(the first shard of a split one; a bare file name with no exact match takes the first file of that name in a sub-folder)".

- [ ] **Step 4: Edit tuning.md**

- Quant headroom intro (:101), append: "An entry url with a sub-folder (`resolve/<sha>/<dir>/<file>`) works: each candidate is looked up at that path with the quant token replaced and downloads under its base name."
- P0 (:112-123), the text after "...report that the comparison needs a Hugging Face entry and stop." becomes:

```text
With a repo: `bash $SK/scripts/hf-scout.sh --repo <repo> --file <entry file> --json --budget-mib <rule 1 budget> --ram-mib <rule 1 RAM>`
(figures from `bash $SK/scripts/preflight.sh`; the bare name also finds the file in a sub-folder). Exit 2 (no file of that name in
the repo, for example a renamed local copy): report it and stop. When `pick.bytes` equals the entry's `size_bytes` and
`pick.sha256` equals the `sha256sum` of that file in the models directory (`$LLM_ENV_MODELS_DIR`, default `~/llm-workspace/models`):
Gate G-Q0 (live config edit), a `cp -p` backup of the config, then `yq -i` the url of `<a>` in the config to
`https://huggingface.co/<repo>/resolve/<sha>/<pick.file>` (`<sha>` is the row's `sha`; `<pick.file>` keeps its sub-folder).
```

  The sentences from "A declined G-Q0 ends the comparison..." to the end of P0 stay. In P0's first sentence, "`check` reads only a `https://huggingface.co/<org>/<repo>/resolve/<rev>/<file>` url" becomes "`check` reads a `https://huggingface.co/<org>/<repo>/resolve/<rev>/<path>` url whose path ends in the entry's `file`".
- Q7 (:158): "and stop the larger quants (the card then shows" becomes "and stop the larger quants; G-Q3 still runs over the measured candidates (the card then shows".

- [ ] **Step 5: Fixtures and the eval**

- `models-subfolder.yml`: from the skill directory, `yq '(.models[] | select(.alias == "gemma4") | .url) |= sub("/gemma4-v2-Q4_K_M.gguf$"; "/Q4_K_M/gemma4-v2-Q4_K_M.gguf")' evals/files/models.yml > evals/files/models-subfolder.yml`.
- `hf/model-yuxinlu1__gemma-4-12B-agentic-fable5-composer2.5-v2-3.5x-tau2-GGUF.json`: `{"sha": "190a31365a6b80a692349be34ccdac730cad4fe4", "gguf": {"total": 11766000000}}`.
- `hf/tree-...@190a31365a6b80a692349be34ccdac730cad4fe4.json`: a list of `{"type": "file", "path", "size", "lfs": {"oid", "size"}}`:
  - `Q4_K_M/gemma4-v2-Q4_K_M.gguf`, 7381381664, oid `"4" * 64`
  - `Q5_K_M/gemma4-v2-Q5_K_M.gguf`, 8547884576, oid `"5" * 64`
  - `Q6_K/gemma4-v2-Q6_K-00001-of-00002.gguf` and `-00002-of-00002.gguf`, 4900000000 each, oids `"6" * 64` and `"7" * 64`
  - one `{"type": "directory", "path": "Q4_K_M"}`
- evals.json, append:

```json
{
  "id": "quant-room-subfolder-entry",
  "prompt": "is there VRAM for a Q5 of my local model? Its file came from a sub-folder of the Hugging Face repo. Tell me what you would run; change nothing. (Eval setup: the config is .claude/skills/onboard-local-model/evals/files/models-subfolder.yml (LLM_ENV_CONFIG); quant-headroom.sh and hf-scout.sh read Hugging Face answers from HF_FIXTURE_DIR=.claude/skills/onboard-local-model/evals/files/hf/; llm-server serves gemma4.)",
  "files": ["evals/files/models-subfolder.yml",
            "evals/files/hf/model-yuxinlu1__gemma-4-12B-agentic-fable5-composer2.5-v2-3.5x-tau2-GGUF.json",
            "evals/files/hf/tree-yuxinlu1__gemma-4-12B-agentic-fable5-composer2.5-v2-3.5x-tau2-GGUF@190a31365a6b80a692349be34ccdac730cad4fe4.json"],
  "expected_output": "P0 finds gemma4's url pinned to a resolve/<sha> path inside the Q4_K_M sub-folder that ends in the entry's file, so no pin is needed. Q1's check looks Q5_K_M up at Q5_K_M/gemma4-v2-Q5_K_M.gguf (file gemma4-v2-Q5_K_M.gguf, 8547884576 bytes) and marks Q6_K sharded, not supported.",
  "assertions": [
    "P0 reads gemma4's url https://huggingface.co/yuxinlu1/gemma-4-12B-agentic-fable5-composer2.5-v2-3.5x-tau2-GGUF/resolve/190a31365a6b80a692349be34ccdac730cad4fe4/Q4_K_M/gemma4-v2-Q4_K_M.gguf as a Hugging Face resolve url whose path ends in the entry's file, so G-Q0 is not asked",
    "quant-headroom.sh check takes Q5_K_M at path Q5_K_M/gemma4-v2-Q5_K_M.gguf with file gemma4-v2-Q5_K_M.gguf (8547884576 bytes), and Q6_K as sharded, not supported",
    "A fetch of Q5_K_M would land in the models directory as gemma4-v2-Q5_K_M.gguf, never in a sub-folder",
    "Nothing was downloaded, stopped, started or edited"
  ]
}
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/skills/onboard-local-model/test_onboard_local_model_scripts.py -q -k "quant or hf_scout or eval_ or black_box or shell_logic or never_point"`
Expected: PASS. The existing top-level quant tests see unchanged rows.

- [ ] **Step 7: Validate (no commit)**

Run: `make validate && make test`. Expected: green.

### Task 3: Onboard flow: settings with a destination, a served-alias list, progress checklists

**Files:**
- Modify: `.claude/skills/onboard-local-model/references/onboard-flow.md:23-32` (Alias), `:40-42` (H2), `:47-50` (H5), `:60` (Local GGUF at G3), `:68` (Steps head), `:81-84` (step 4), `:104` (5.2), `:108-116` (step 6), `:118-124` (7a), `:136` (7c), `:149` (8), `:161-166` (9), new section before `## Entry template`, `:191-192` (template note)
- Modify: `.claude/skills/onboard-local-model/references/tuning.md:31-32` (T9), `:105-110` (Standalone quant comparison names)
- Modify: `.claude/skills/onboard-local-model/evals/evals.json` (`hf-repo-argument`: one assertion)
- Test: `tests/skills/onboard-local-model/test_onboard_local_model_scripts.py` (two new tests after `test_onboard_step_4_selects_the_candidate_before_validating_it` :1003)

**Interfaces:**
- Consumes: Task 1's candidate keys (owner decision 3).
- Produces: section `## Served alias re-measure` with `R1.`-`R6.`; a "Progress checklist" line in Steps and in Standalone quant comparison; the baseline screen literal `--out tmp/model-scout/<run>/bench-baseline` (Task 4's guard counts it).

- [ ] **Step 1: Write the failing tests**

```python
def test_onboard_step_4_writes_the_settings_into_the_scratch_candidate() -> None:
    flow = _md(REFS / "onboard-flow.md")
    step4 = _section(flow, "4. **Fit and configure.**", "5. **Baseline lane")
    marks = [step4.find(m) for m in ("yq -i '(.models[] | select(.alias == \"<alias>\")) |=", "**Gate G1**", "models select <alias>")]
    assert -1 not in marks and marks == sorted(marks), marks
    assert "`runtime.cache_type_k/v`" in step4 and "KV `q5_1` unless" not in step4 and "del(.n_cpu_moe)" in step4
    assert "from `$S`" in _section(flow, "**7a. Apply and serve", "**7b.")
    assert "largest fitting single-file quant" in _section(flow, "H2. ", "H3. ")
    assert "--out tmp/model-scout/<run>/bench-baseline" in _section(flow, "6. **Screen throughput.**", "7. **Apply")
    assert "path and size" in _section(flow, "9. **Keep or revert", "## Served alias re-measure")
    assert all(s in _asserts("hf-repo-argument") for s in ("yq -i", "runtime.cache_type_k/v"))


def test_served_alias_has_its_own_list_and_routes_have_progress_checklists() -> None:
    flow = _md(REFS / "onboard-flow.md")
    served = _section(flow, "## Served alias re-measure", "## Entry template")
    assert all(f"R{n}. " in served for n in range(1, 7)) and "R7. " not in served
    steps = _section(flow, "## Steps", "## Served alias re-measure")
    assert "served alias" not in steps.lower() and "Progress checklist" in steps
    assert "follow Served alias re-measure" in flow
    tuning = _md(REFS / "tuning.md")
    assert "Progress checklist" in _section(tuning, "### Standalone quant comparison", "- Q1.")
    assert "`ps` by PID" in _section(tuning, "- T9.", "## Interference gate")
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/skills/onboard-local-model/test_onboard_local_model_scripts.py -q -k "settings_into or own_list"`
Expected: FAIL (no yq step, no R-list, no checklists).

- [ ] **Step 3: Edit Candidate by kind**

- Alias (:23-32), the heading stays and its two bullets become:

```text
- **The enabled, served alias is a re-measure:** follow Served alias re-measure (after Steps) instead of Steps.
- **Any other alias.** `bash $SK/scripts/resolve-target.sh <alias> --scratch-config $S` writes `$S` with that alias alone
  enabled; `validate-gguf` and `budget` take `--config $S`. With `present: false` G1 downloads the entry's `url` first. Its
  entry keeps its own settings (Steps 4 writes none). G3 adds no entry: it runs `make llama-enable MODEL=<alias>`.
```

- H2: "A null `sha` or `pick.sha256` stops before G1 (template note)." becomes "A null `sha` stops before G1. A null `pick.sha256` (a split file; an entry takes one file) stops before G1 as well: report it with the repo's largest fitting single-file quant from H3's call without `--file` (or that none exists when that pick is split too); only an owner request after that report goes on with it."
- H5, the whole item becomes:

```text
H5. Name the entry per Alias for a new entry. Build `$S` with `bash $SK/scripts/resolve-target.sh <live alias> --scratch-config $S`:
   `<live alias>` is the baseline, else any live alias (no baseline: Steps 2; Steps 4's `models select` switches `$S` to the
   candidate). Then add the entry (template below) to `$S` with `yq -i`, `enabled: false`; Steps 4 writes its settings. Under
   rule 1 (a live entry has this `file`) add nothing: that alias is the candidate. Never `cp` the live file into `tmp/`; with
   no alias in the live file, stop and report.
```

- Local GGUF at G3 (:60): "`label` is the human name;" becomes "`label` and `parameters` are the human name and size (the scratch entry says `unknown`);".
- Template note (:191-192): "A null `sha`, or a null `pick.sha256` (a split file; an entry takes one file), stops before G1: report it." becomes "A null `sha` or `pick.sha256` stops before G1 (H2)."

- [ ] **Step 4: Edit Steps**

- After `## Steps`, insert:

```text
Progress checklist (copy it into your task list; (b) = only with a baseline, Steps 2): 1 preflight; 2 baseline; 3 candidate;
4 settings, G1, select, validate, budget; 5 (b) baseline lane and build, G2; 6 (b) baseline screen, candidate screen; 7a G3
apply; 7b interference and card; 7c ladder, (b) compare; 8 verdict; 9 G5, downloaded-file report, leftover check.
```

- Step 4, the sentences from "With the fit math loaded" to "agentic settings." become:

```text
With the fit math loaded with this route (sections 1-3), compute weights + KV + buffers against the budget at the live KV
type (`runtime.cache_type_k/v`, one global key: never changed here); for MoE solve `n_cpu_moe` against RAM too. A new entry's
settings: `ctx_size` 262144 when its KV fits, else 131072; `client_max_output_tokens` 8192 (16384 when an earlier lane of this
model ended runs with `stopReason: length`); `n_cpu_moe` from the solve (MoE only); `sampling` from the model card's agentic
settings, or none. Write them into the candidate entry of `$S` (an existing entry keeps its own):
`yq -i '(.models[] | select(.alias == "<alias>")) |= (.ctx_size = <ctx> | .client_max_output_tokens = <max_out> | .n_cpu_moe = <n> | .sampling = <sampling>)' $S`,
with `del(.n_cpu_moe)` for a dense model and `del(.sampling)` when the card gives none. 7a's live entry takes the same values.
```

- 5.2 (:104): delete "The served alias: this is its only lane."
- Step 6, from its start through "runs the same command first on the live config, with the same `--depths`." becomes:

```text
6. **Screen throughput.** A benchmark run (rule 2). `make benchmark` measures only enabled models, so the candidate gets
   `LLM_ENV_CONFIG=$S bash $SK/scripts/bench-fit.sh --alias <alias> --out tmp/model-scout/<run>/bench --depths 0,32768,<ref>`
   with `<ref>` the reference depth of rule 3 (same flag presets as `make benchmark`, plus the depth sweep and load sampling it
   lacks). With a baseline it runs first, on the live config with the same depths:
   `bash $SK/scripts/bench-fit.sh --alias <baseline> --out tmp/model-scout/<run>/bench-baseline --depths 0,32768,<ref>`.
```

  The rest of step 6 ("Show loading, ...", "Reject when ...", the "unknown model architecture" sentence) stays, minus " (for the served alias, step 9 is only `make start-llama`)".
- 7a: delete "The served alias runs only `make start-llama`; the list is for a new candidate." In item 2, "Else add the entry (template below) under the alias decided at step 3, with `yq -i` (`make llama-enable` only selects an existing alias)." becomes "Else add the candidate's entry from `$S` (its Steps 4 settings, `enabled: false`) under the alias decided at step 3, with `yq -i` (`make llama-enable` only selects an existing alias); a Local GGUF's entry also takes the alias, label, parameters and url of Local GGUF."
- 7c: delete "The served alias runs only item 3, on its step 5 lane."
- 8: delete "The served alias has no verdict: report its numbers and the capacity card."
- 9: "nothing to restore before G3 or for the served alias" becomes "nothing to restore before G3". Before "Finish with `podman ps -a`", insert: "When the candidate is not kept and G1 put a file into the models directory, report its path and size and ask whether to delete it (a user-data delete: asked under bypass too); never touch a Local GGUF's original."

- [ ] **Step 5: Add the served-alias section** (after Steps, before `## Entry template`)

```text
## Served alias re-measure

For the enabled alias llm-server serves now: no scratch config, no candidate, no verdict (it cannot beat itself).

R1. Steps 1 (preflight and the rule 3 profile check). Its fit is preflight's `llmenv_budget`; a false `feasible` leads the report.
R2. While it is served: the Steps 5.2 lane (`<baseline>` is this alias; the effort-ladder reference loads here), its only
    lane, then Steps 5.3 (record the build).
R3. Gate G2: free the GPU (`SKILL.md` Freeing the GPU).
R4. Screen on the live config with Steps 6's baseline command, reported as Steps 6 says. Over budget or under the floor:
    report it, `make start-llama` and stop.
R5. Gate G3 (nothing is edited; it only serves again): `make start-llama`, then Steps 7b with the R4 bench JSON (a failed gate
    is reported), then `bash $SK/scripts/score.sh <R2 results.jsonl>` and `make model-capacity MODEL=<alias> SCORE=<score.json>`.
R6. Report the numbers and the capacity card with no verdict; G5 has nothing to revert. Finish with Steps 9's leftover check.
```

- [ ] **Step 6: Tuning checklists and the eval**

- tuning.md, after the Names paragraph of Standalone quant comparison (before "Before Q1, in this order"), insert: "Progress checklist (copy it into your task list): P0 url; P1 interference; P2 depth; Q1 check; with a fitting candidate: P3 seeds, then per candidate Q2 fetch, Q3 entry, Q4-Q6 serve, Q7 gate, Q8 ladder, Q9-Q10 record, Q11 restore; G-Q3; leftover check (`podman ps -a`, `ps` by PID)."
- T9: append "Finish with `podman ps -a` and a `ps` by PID for everything this run started."
- evals.json `hf-repo-argument`: insert before "On the scratch config, `models select foo-9b`...": "Before G1 the step 4 settings (ctx_size, client_max_output_tokens, sampling; no n_cpu_moe for this dense model) are written into foo-9b in the scratch config with yq -i; the KV type runtime.cache_type_k/v is global and left alone".

- [ ] **Step 7: Run the tests to verify they pass**

Run: `uv run pytest tests/skills/onboard-local-model/test_onboard_local_model_scripts.py -q -k "onboard or served or route_table or eval_ or shell_logic or never_point or stated_once"`
Expected: PASS. `test_onboard_step_4_selects_the_candidate_before_validating_it` still holds ("add nothing" in H5; no "(step 2)" or "step 4's").

- [ ] **Step 8: Validate (no commit)**

Run: `make validate && make test`. Expected: green.

### Task 4: One output folder per lane (D8-3 and the class)

**Files:**
- Modify: `.claude/skills/onboard-local-model/references/scoring.md:31-42` (brief), `:51` (reading item 2), `:72` (Head-to-head 3)
- Modify: `.claude/skills/onboard-local-model/references/tuning.md:105-107` (Names), `:132` (P3)
- Modify: `.claude/skills/onboard-local-model/references/anchors.md:39-41` (lane 3)
- Modify: `.claude/skills/onboard-local-model/references/selection-criteria.md:13` (Scout and rank head)
- Modify: `.claude/skills/onboard-local-model/references/onboard-flow.md:3-5` (header)
- Modify: `.claude/skills/onboard-local-model/evals/evals.json` (append `second-lane-out-dir`)
- Test: `tests/skills/onboard-local-model/test_onboard_local_model_scripts.py` (new tests after `test_only_bench_fit_mounts_the_models_directory` :1014 and after Task 2's eval test)

**Interfaces:**
- Consumes: Task 3's `--out tmp/model-scout/<run>/bench-baseline`.
- Produces: the brief's placeholder `<out>` (log `<out>.log`, re-run `<out>-r2`); `--out QR/base/effort` (P3); `--out tmp/model-scout/<run>/effort-<alias>` and `--out tmp/model-scout/<run>/effort-<anchor>` (anchors); `<run>` = `<YYYYMMDD>-scout` / `<YYYYMMDD>-anchors` / `<run>-2`.

- [ ] **Step 1: Write the failing tests**

```python
def test_every_benchmark_out_dir_is_named_once() -> None:
    """Two lanes never share an --out: effort-run.sh refuses a used one and bench-fit.sh overwrites its side files."""
    outs = [o for p in [SKILL_MD, *sorted(REFS.glob("*.md"))] for o in re.findall(r"--out ([^\s`)]+)", p.read_text()) if o != "DIR"]
    assert "<out>" in outs and len(outs) == len(set(outs)), sorted({o for o in outs if outs.count(o) > 1})
    assert "--out QR/base/effort" in _md(REFS / "tuning.md") and "`<run>-2`" in _md(REFS / "onboard-flow.md")
    assert "`<YYYYMMDD>-scout`" in _md(REFS / "selection-criteria.md") and "`<YYYYMMDD>-anchors`" in _md(REFS / "anchors.md")


def test_eval_second_lane_fixture_matches_its_assertions() -> None:
    flow = _md(REFS / "onboard-flow.md")
    assert "--out tmp/model-scout/<run>/effort-baseline" in flow and "--out tmp/model-scout/<run>/effort --efforts low,high --seeds" in flow
    assert "--out <out> <flags> > <out>.log" in _md(REFS / "scoring.md")
    text = _asserts("second-lane-out-dir")
    assert all(s in text for s in ("--out tmp/model-scout/20261031-foo-9b/effort --efforts low,high --seeds tmp/model-scout/20261031-foo-9b/effort-baseline/seeds.json",
                                   "exits 2", "tmp/model-scout/20261031-foo-9b/effort.log"))
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/skills/onboard-local-model/test_onboard_local_model_scripts.py -q -k "named_once or second_lane"`
Expected: FAIL. `tmp/model-scout/<stamp>/effort` appears twice (scoring.md:36, tuning.md:26), there is no `<out>`, and the eval is missing.

- [ ] **Step 3: Edit the references**

- scoring.md:31, the intro becomes "Hand the subagent this block, with `<m>` (`router-env/llama-cpp/<alias>` or the anchor's id), `<out>` (the `--out` the route step names: one new folder per lane, since `effort-run.sh` exits 2 on a folder that holds a run; a re-run takes `<out>-r2`, `-r3`...) and `<flags>` filled in."
- In the brief, `--out tmp/model-scout/<stamp>/effort <flags> > tmp/model-scout/<stamp>/effort.log` becomes `--out <out> <flags> > <out>.log`, and `tmp/model-scout/<stamp>/effort/results.jsonl` becomes `<out>/results.jsonl`.
- scoring.md:51 "re-run that lane on the same seeds with a fresh `--out`" becomes "re-run that lane on the same seeds into `<out>-r2`". In Head-to-head item 3, "(fresh `--out`)" becomes "(into `<out>-r2`)".
- tuning.md Names: "`<seeds>` is the `seeds.json` of `<a>`'s ladder lane" gains "(T8's, or `QR/base/effort/seeds.json` when P3 ran)". In P3, "T8's ladder command" becomes "T8's ladder command with `--out QR/base/effort`".
- anchors.md lane 3, append: "Their `--out` (the effort-ladder brief's `<out>`): `--out tmp/model-scout/<run>/effort-<alias>` for the local lane and `--out tmp/model-scout/<run>/effort-<anchor>` per anchor (`<anchor>`: its model id with `/` as `-`); `<run>` is `<YYYYMMDD>-anchors`, or the onboarding run's when one has lanes (its baseline lane keeps its own folder)."
- selection-criteria.md, first line under `## Scout and rank (no argument)`: "Here `<run>` is `<YYYYMMDD>-scout`; a pick onboarded later runs under its own `<run>` (`SKILL.md`), and its evidence card stays here."
- onboard-flow.md header, append: "A second onboarding of the same alias on one day uses `<run>-2` (`effort-run.sh` refuses a used `--out`)."

- [ ] **Step 4: Append the eval**

```json
{
  "id": "second-lane-out-dir",
  "prompt": "the gemma4 baseline lane of today's foo-9b onboarding finished in tmp/model-scout/20261031-foo-9b/effort-baseline and foo-9b is served now. What exact command runs the foo-9b ladder lane? Run nothing.",
  "expected_output": "The candidate lane writes a new folder, tmp/model-scout/20261031-foo-9b/effort, never the baseline's (effort-run.sh exits 2 on a folder that holds a run); it reuses the baseline seeds with the same efforts on the router profile and goes to one subagent with the effort-ladder brief.",
  "assertions": [
    "The command is PI_CODING_AGENT_DIR=$HOME/.pi/agent bash .claude/skills/onboard-local-model/scripts/effort-run.sh --model router-env/llama-cpp/foo-9b --out tmp/model-scout/20261031-foo-9b/effort --efforts low,high --seeds tmp/model-scout/20261031-foo-9b/effort-baseline/seeds.json",
    "It never reuses tmp/model-scout/20261031-foo-9b/effort-baseline as --out: effort-run.sh exits 2 on a folder that already holds a run",
    "The lane is delegated with the effort-ladder brief, <out> filled with tmp/model-scout/20261031-foo-9b/effort and the log at tmp/model-scout/20261031-foo-9b/effort.log",
    "Nothing was run, stopped, started or edited"
  ]
}
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/skills/onboard-local-model/test_onboard_local_model_scripts.py -q -k "named_once or second_lane or quant or stated_once or never_point or shell_logic or eval_"`
Expected: PASS.

- [ ] **Step 6: Validate (no commit)**

Run: `make validate && make test`. Expected: green.

### Task 5: `SKILL.md` router edits (byte-measured)

**Files:**
- Modify: `.claude/skills/onboard-local-model/SKILL.md:3,14-15,29,31,33,35-36,43-46,49-51,99-100,111-116`
- Test: `tests/skills/onboard-local-model/test_onboard_local_model_scripts.py` (one new test after `test_skill_md_is_a_router_that_loads_every_reference` :979)

**Interfaces:**
- Consumes: "a near-miss alias" (Task 1).

These edits were measured on a scratch copy: 10,973 -> 10,971 bytes, description 923 characters. If a reflow pushes the file over the cap, shorten the gotcha first, to "- KV cache type is one global key (`runtime.cache_type_k/v`); only the tune route changes it."

- [ ] **Step 1: Write the failing test**

```python
def test_skill_md_routes_near_misses_allows_one_inline_screen_and_names_the_global_kv_key() -> None:
    text = _md(SKILL_MD)
    assert "a near-miss alias" in text and "All but a single `bench-fit.sh` screen (bounded by its `--timeout`" in text
    assert "`runtime.cache_type_k/v`" in text.split("## Gotchas", 1)[1]
    assert "overrides another row's Do NOT load cell" not in text and "also serves authoring" not in text
    meta = yaml.safe_load(SKILL_MD.read_text().split("---")[1])
    assert all(w in meta["description"] for w in ("LLM (GGUF)", "to download, add, try or swap", "what LLM can I run locally"))
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/skills/onboard-local-model/test_onboard_local_model_scripts.py -q -k "near_misses_allows"`
Expected: FAIL.

- [ ] **Step 3: Edit SKILL.md (exact replacements)**

- :3 description: "agentic-coding GGUF for" becomes "agentic-coding LLM (GGUF) for"; "a model name to add, try or swap" becomes "a model name to download, add, try or swap"; `even as "a faster model for pi" or "modelo` becomes `even as "a faster model for pi", "what LLM can I run locally" or "modelo`.
- :14-15 become:

```text
A model argument goes first through `bash $SK/scripts/resolve-target.sh <model>`: its `kind` or exit code picks the row.
Follow the row's flow file; load the others only at the step that names them.
```

- :29: "| one ladder challenge: generate, verify or debug it |" becomes "| one ladder challenge (generate, verify, debug), the challenge library, a `leak_suspect` row |". Delete :33 ("- `references/challenges.md` also serves authoring or calibrating the challenge library and a `leak_suspect` row.").
- :31: "(a bad path, a non-model URL, usage)" becomes "(a bad path, a near-miss alias, a non-model URL, usage)".
- :35-36: delete " (a matching row's file overrides another row's Do NOT load cell)". The sentence ends "...load every file they name."
- :43-46: "when `llmenv_budget` is null (for example a gateway-only host with nothing enabled), size against `budget.model_vram_budget_mib` and say so." becomes "with `llmenv_budget` null (nothing enabled, as on a gateway-only host) use the second and say so."
- :49-51, rule 2's first sentence becomes:

```text
2. **Benchmark runs.** Every `bench-fit.sh`, `tune-sweep.sh`, `effort-run.sh` and `latency-profile.sh` run writes to its own new
   `--out` under `tmp/` and runs alone on the GPU. All but a single `bench-fit.sh` screen (bounded by its `--timeout`, its
   container removed on exit) go to one subagent with a written brief (command, out dir, stop, cleanup, what to return),
   monitored per AGENTS.md.
```

- :99-100: "- NEVER read `agent_status: 0` as success: a 401 or usage limit completes with `stopReason: error`, which `score.sh` counts as `invalid_runs`; re-run the lane per AGENTS.md."
- Gotchas, insert before the `bench-fit.sh` item: "- The KV cache type is one global key (`runtime.cache_type_k/v`): only the tune route changes it, for every model at once."
- Reflow to about 128 columns.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/skills/onboard-local-model/test_onboard_local_model_scripts.py -q -k "near_misses_allows or stated_once or route_table or router or bypassed or description or black_box or never_point or shell_logic or trigger"`, then `wc -c .claude/skills/onboard-local-model/SKILL.md`
Expected: PASS; at most 11,000 bytes (about 10,971).

- [ ] **Step 5: Validate (no commit)**

Run: `make validate && make test`. Expected: green.

### Task 6: Reference lows: host-specific evidence, a destination for every flag

**Files:**
- Modify: `.claude/skills/onboard-local-model/references/selection-criteria.md:76-89` (section 4)
- Modify: `.claude/skills/onboard-local-model/references/anchors.md:27-28`
- Modify: `.claude/skills/onboard-local-model/references/runtimes.md:45-52` (flags)
- Test: `tests/skills/onboard-local-model/test_onboard_local_model_scripts.py` (one new test after Task 5's)

- [ ] **Step 1: Write the failing test**

```python
def test_references_keep_host_specific_evidence_and_give_every_flag_a_destination() -> None:
    ev = _section(_md(REFS / "selection-criteria.md"), "## 4. Is it really agentic?", "## 5.")
    assert "Rank evidence from strongest to weakest" not in ev and "gguf.chat_template" in ev and "agentic_evidence" in ev
    assert "Set expectations honestly" not in _md(REFS / "anchors.md")
    flags = _section(_md(REFS / "runtimes.md"), "## Flags worth an A/B", "## Other runtimes")
    assert "tune-sweep.sh" in flags and "measure with `bench-fit.sh`" not in flags and "no models.yml key" in flags
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/skills/onboard-local-model/test_onboard_local_model_scripts.py -q -k "flag_a_destination"`
Expected: FAIL.

- [ ] **Step 3: Edit the references**

- selection-criteria.md section 4: the heading stays, and the body (:78-89) becomes:

```text
Section 7's `agentic_evidence` levels rank the evidence; the generated challenge ladder measured here outranks all of it. Host
specifics: a leaderboard counts only with its harness and version (Terminal-Bench 2.x, SWE-bench Verified / Pro / Multilingual,
tau2-bench, BFCL v4, LiveCodeBench, Aider polyglot; the same model moves 2x across versions and effort, `SKILL.md` NEVER); HF
exposes `gguf.chat_template`, so grep it for `tools` to check tool handling; an unknown fine-tuner must show a gain over its own
base model's official instruct (a first-party release deserves a head start).
```

- anchors.md:27-28 become: "- Target for a model of about 10 GiB: the anchor's verifier pass rate on small, well-specified edits at lower effort, not its Terminal-Bench or SWE-bench score."
- runtimes.md: the heading becomes "## Flags worth an A/B (one at a time)", followed by: "`bench-fit.sh` takes every flag from the config's rendered presets, so an A/B runs as `tune-sweep.sh` axes (`--ubatch`, `--kv`, `--moe`, `--threads`; one scratch config per cell, `tune-sweep.sh --help`) after freeing the GPU (G2, `SKILL.md`). A value worth keeping is applied only through the tune route of the `SKILL.md` route table, which backs up and edits the live file." Then:
  - The KV item gains "; one global key for every model".
  - The thread item gains "(reported only: models.yml has no key for it)".
  - The `--no-mmap` / `--mlock` item gains ": no models.yml key exists, so report the finding only".

- [ ] **Step 4: Run and validate (no commit)**

Run: `uv run pytest tests/skills/onboard-local-model/test_onboard_local_model_scripts.py -q -k "flag_a_destination or eval_scout or black_box or never_point or shell_logic"`, then `make validate && make test`
Expected: PASS and green.

### Task 7: Eval prompts as user turns; 10 + 10 trigger queries

**Files:**
- Modify: `.claude/skills/onboard-local-model/evals/evals.json` (prompts only)
- Modify: `.claude/skills/onboard-local-model/evals/trigger-queries.json`
- Test: `tests/skills/onboard-local-model/test_onboard_local_model_scripts.py` (`test_trigger_queries_are_balanced_and_cover_routes_and_boundaries` :907; one new test after it)

- [ ] **Step 1: Write the failing tests**

```python
EVAL_SETUP = re.compile(r"^onboard-local-model|fixture|DRY RUN|LLM_ENV_|HF_FIXTURE_DIR|Premise")


def test_eval_prompts_read_as_user_turns() -> None:
    for e in json.loads((SKILL / "evals" / "evals.json").read_text())["evals"]:
        assert not EVAL_SETUP.search(e["prompt"].split("(Eval setup:", 1)[0]), e["id"]
```

In the trigger test, `8 <= len(pos) <= 12 and 8 <= len(neg) <= 12` becomes `8 <= len(pos) <= 10 and 8 <= len(neg) <= 10`.

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/skills/onboard-local-model/test_onboard_local_model_scripts.py -q -k "user_turns or trigger_queries"`
Expected: FAIL (20 prompts carry setup text; 11 + 11 queries).

- [ ] **Step 3: Rewrite the prompts**

Rule for every prompt the test flags: keep the user's ask as the head. Move every setup sentence (fixture paths, "Premise: ...", "preflight.sh already printed ...", "DRY RUN ...", env variables, "Answer in English") verbatim, in order, into one trailing `(Eval setup: ...)`. Assertions, `files` and `expected_output` stay unchanged. A prompt that starts with the skill name gets this head instead:

| id | head |
|---|---|
| served-alias-argument | gemma4 feels slower lately: re-measure it and tell me where it stands. Give me the ordered plan with its gates. |
| disabled-alias-argument | try ornith-35b from my models.yml instead of gemma4 and tell me if it is better. Give me the ordered plan with its gates. |
| hf-repo-argument | set up https://huggingface.co/lab/Foo-9B-GGUF as my local model if it fits. |
| named-gguf-argument | I downloaded .claude/skills/onboard-local-model/evals/files/Foo-9B-Q4_K_M.gguf: does it fit next to laya? |
| no-argument-ranking | which local models should pi try next? Give me a shortlist. |
| hf-file-pinned-revision | can we run https://huggingface.co/lab/Foo-9B-GGUF/resolve/v2/Foo-9B-Q4_K_M.gguf locally? |
| bare-name-no-repo | try Zeta-Coder-7B as the local model. |
| missing-gguf-path | use .claude/skills/onboard-local-model/evals/files/Missing-7B-Q4_K_M.gguf as my local model. |
| gguf-alias-collision | add .claude/skills/onboard-local-model/evals/files/Ornith-Q5_K_M.gguf as a local model next to the ornith I have. |

`test_eval_input_files_exist_and_the_gguf_fixture_resolves` still finds every `files` path in the prompt, now inside the setup.

- [ ] **Step 4: Trigger queries 10 + 10**

- Replace the two positives "our llama.cpp image is ~1500 builds behind, ..." and "laya is a couple releases behind on pypi, ..." with one: `{"query": "our llama.cpp image is ~1500 builds behind and laya is a couple releases behind on pypi: worth pulling a new vulkan build, trying rocm, or upgrading laya to free vram?", "should_trigger": true}`.
- Delete the negative "write a python script that reads the header of the gguf llama-server serves right now and prints its tensor sizes".

- [ ] **Step 5: Run and validate (no commit)**

Run: `uv run pytest tests/skills/onboard-local-model/test_onboard_local_model_scripts.py -q -k "user_turns or trigger_queries or eval_ or black_box"`, then `make validate && make test`
Expected: PASS and green.

### Task 8: Verification and the plans index row

**Files:**
- Modify: `docs/superpowers/plans/README.md` (this plan's row)

- [ ] **Step 1: Guards and suites, one file at a time**

Run, one after the other:
- `uv run pytest tests/skills/onboard-local-model/test_onboard_local_model_scripts.py -q`
- `uv run pytest tests/skills/onboard-local-model/test_challenges.py -q`
- `uv run pytest tests/skills/onboard-local-model/test_latency_profile.py -q`
- `make validate && make test`

Expected: all green. The guard set in the first file is `-k "black_box or stated_once or never_point or route_table or router or shell_logic or named_once or user_turns"`.

- [ ] **Step 2: Byte cap and description**

Run: `wc -c .claude/skills/onboard-local-model/SKILL.md`. Expected: <= 11,000 (about 10,971). `test_skill_description_fits_the_spec_limit` passes (923 characters).

- [ ] **Step 3: Read-only route walk with the fixtures**

From the repo root, set `SK=.claude/skills/onboard-local-model F=$SK/evals/files`, `LLM_ENV_CONFIG=$F/models.yml` and `LLM_ENV_MODELS_DIR=$F/models`, and pass `HF_FIXTURE_DIR` to hf-scout.sh. Scratch files go to `tmp/plan-k-walk/`, removed at the end.

1. Model given, alias: `resolve-target.sh gemma4` gives `kind alias`, `enabled true`, `present true`. Reading Alias, the served alias goes to R1-R6.
2. Mistyped alias: `Gemma4`, `gemma` and `Ornith-35B` exit 2 with "did you mean alias gemma4?" (`ornith-35b` for the last). `Qwen3.8-27B`, `Zeta-Coder-7B` and `gemma-4-27b-it` exit 3.
3. Models-dir name: `gemma4-v2-Q4_K_M.gguf` and `gemma4-v2-Q4_K_M` exit 2 with "did you mean $F/models/gemma4-v2-Q4_K_M.gguf?".
4. HF file URL:
   - `.../lab/Foo-9B-GGUF/resolve/v2/Foo-9B-Q4_K_M.gguf` gives `hf_file`, revision `v2`.
   - `.../blob/main/sub/Foo-9B-Q4_K_M.gguf` gives file `sub/Foo-9B-Q4_K_M.gguf`.
   - `.../resolve/main/README.md` exits 2 "not a .gguf file URL".
   - A split-shard URL gives `hf_file`. H2 then offers the largest fitting single-file quant.
5. Local GGUF: `resolve-target.sh $F/Foo-9B-Q4_K_M.gguf --scratch-config tmp/plan-k-walk/s.yml` gives a `candidate` with quantization `Q4_K_M`, `parameters unknown`, and no `sampling` or `benchmark`. Reading Steps 4, `yq -i` comes before G1 and `models select`. The live models.yml is unchanged.
6. Mistyped local path: `resolve-target.sh $F/Foo-9B-Q4_K_M.ggu` exits 2 with "did you mean $F/Foo-9B-Q4_K_M.gguf?".
7. No model: `HF_FIXTURE_DIR=$F/hf-empty hf-scout.sh --query Zeta-Coder-7B --json --budget-mib 8321 --ram-mib 12000` prints `[]`. `rank-candidates.sh --scout $F/scout-coder.json --scout $F/scout-agent.json --evidence $F/evidence` ranks lab-a 23 and lab-d 20. The scout `<run>` is `<YYYYMMDD>-scout`.
8. Quant comparison:
   - `models-local-url.yml` gives a `file://` url, so P0 asks for the repo.
   - `models-subfolder.yml` gives a sub-folder resolve url. `test_eval_subfolder_quant_fixture_matches_its_assertions` passes (Q5_K_M at `Q5_K_M/gemma4-v2-Q5_K_M.gguf`).
   - tuning.md reads P0, P1, P2, Q1, then P3 with `--out QR/base/effort`.
9. Out dirs: `test_every_benchmark_out_dir_is_named_once` passes.

Then `podman ps -a` lists the same containers as before the walk, and `tmp/plan-k-walk/` is gone.

- [ ] **Step 4: Plans index row**

The row below was added with this plan, after the 2026-10-31-judge-round-8-fixes row (rows of plans G, H, I and J unchanged). On completion, change `PENDING` to `**DONE** (executed <date>; skill edits uncommitted with the untracked skill tree; judges re-run by the orchestrator)`.

```text
| 2026-10-31-judge-round-9-fixes | PENDING | fixes from skill-judge round 9 of onboard-local-model (`tmp/judges/r9-native-summary.txt` 88.0 % B, 5 medium, 9 low; `tmp/judges/r9-referee-summary.txt` 94.4 % A, its high and mediums refuted by the lead, 3 lows; bar: >= 90 % from both lanes, no critical or high) plus a structural pass over the walked routes (settings with no destination, output-folder clashes, scattered served-alias exceptions, typos routed to a search); findings verified against the files first (refuted and dropped ones in its ledger): `resolve-target.sh` exits 2 for a near-miss alias, a models-dir file name and a non-GGUF file URL, and its `candidate` no longer inherits the baseline's `n_cpu_moe`/`sampling`/`benchmark` (red-first); `quant-headroom.sh` maps a sub-folder entry url and `hf-scout.sh --file` finds a bare name in a sub-folder (red-first); onboard Steps 4 writes its settings into the scratch candidate with `yq -i` (KV stays global), the served alias gets its own R1-R6 list, progress checklists for the onboard and quant routes, split-file fallback, downloaded-file report; one `--out` per lane (`<out>` in the effort-ladder brief, `bench-baseline`, `QR/base/effort`, scout/anchors `<run>`) pinned by a distinct-out guard; `SKILL.md` description with LLM/download/run locally, KV gotcha, one inline `bench-fit.sh` screen (stays under 11,000 bytes, measured draft 10,971); three fixture-backed evals, eval prompts as user turns, 10 + 10 trigger queries; no tracked code changes; skill edits stay uncommitted with the untracked skill tree; judges re-run by the orchestrator |
```

## Round-1 review outcome (approved, 1 confirmed high, 2 mediums)

Review round 1 (native + referee-two, lead-verified on a scratch copy; `tmp/plan-k-review/review-round-1.md`) confirmed one high and
two mediums; every referee critical and high was refuted or downgraded. Plan status: approved for execution. The implementer applies:

- H1 (Task 2 Step 4): the Quant headroom intro writes `resolve/<sha>/<folder>/<file>`, never `<dir>`:
  `test_quant_comparison_checks_headroom_before_the_ladder_lane` asserts `"<dir>" not in tuning`.
- M1 (Task 5): SKILL.md:31 (exit 2) must reach the search after a near-miss alias. The bullet becomes "`resolve-target.sh` exit 2 is
  an error (a bad path, a near-miss alias, a non-model URL, usage): report its message and stop; a "no, search it" after a near-miss
  alias takes the bare-name row." Task 5's test also asserts `"no, search it" after a near-miss` in `SKILL.md`. Paid for by the
  plan's short KV gotcha: "- KV cache type is one global key (`runtime.cache_type_k/v`); only the tune route changes it."
- M2 (Tasks 4-5): every route's out folder is collision-safe, with the rule stated once. SKILL.md:8 defines `<run>` as
  "`<YYYYMMDD>-<alias>` (`-2`, `-3`... when that folder exists)"; the scout and anchors `<run>` (`<YYYYMMDD>-scout`,
  `<YYYYMMDD>-anchors`) follow it, and `QR`, T8 and the latency profile already carry `<stamp>`. Task 4 drops its onboard-flow
  header sentence (`<run>-2`). `test_every_benchmark_out_dir_is_named_once` replaces its onboard-flow `<run>-2` check with: the
  `<run>` definition in `SKILL.md` names the `-2` suffix, and every `--out` literal holds `<run>`, `<stamp>` or `<out>`, or starts
  with `QR` (whose definition in tuning.md holds `<stamp>`). Review Focus 5 reads that rule in `SKILL.md`. Task 5 finds the extra
  bytes by shortening, never by raising the cap.
- Lows:
  - L1: the review counted 21 of 24 prompts with setup text; Task 7's rewrite script measured 20 on the executed state (the
    ledger and Task 7 Step 2 keep 20, now measured).
  - L2 (Task 2): in `cmd_add_entry`, assign the path to a variable before the `export` (shellcheck SC2155).
  - L3 (Task 4): challenges.md "re-run a flagged lane on the same seeds with a fresh `--out`" becomes "... into `<out>-r2` (the
    effort-ladder brief's re-run folder)".
  - L4 (Task 3): 7a's Local GGUF clause lists "alias, label, parameters, quantization and url".
  - L5 (Task 4): the anchors `--out` sentence names `effort-<alias>` only for a local lane the comparison runs itself; inside an
    onboarding run it reuses that run's lanes and adds only `effort-<anchor>` lanes, never a second local lane.
  - L6 (Task 3): step 4's skip is its own sentence: "An Alias-row or rule-1 candidate keeps its own settings: skip this write."
  - L7: no change.
