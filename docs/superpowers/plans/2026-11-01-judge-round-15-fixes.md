# Judge Round 15 Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the confirmed findings of round 15 of the onboard-local-model skill judges, plus the route gaps plan P's executor left. The native lane gave 88.8 % B (1 high, 7 mediums, 3 audit must-fix). The referee gave 85.6 % B; the lead refuted every referee judge finding and must-fix. The next round should give >= 90 % from both lanes with no critical or high finding.

**Architecture:** Five groups of change, all inside the untracked skill tree and its tests:
- **Quant comparison inputs (native high D8-1 = audit M1, D8-2 = M2, executor residual).** `quant-headroom.sh` accepts `UD-` quant tokens and finds a candidate in the tree case-insensitively, using the tree's own spelling. An EXIT trap removes its sparse stand-ins when a check is interrupted.
- **`org/repo:QUANT` (native D8-4 = audit S2).** `resolve-target.sh` splits a trailing `:QUANT` (or Ollama's `:latest`) off a repo id or Hugging Face URL and returns `hf_repo` with a `quant`. `hf-scout.sh --quant Q` picks that quant, so H1 honours it.
- **Replace rule on correctness (audit M3).** Every head-to-head compare passes `--correctness-only`. The full score's speed, thermal and CPU terms (30 points) no longer let a cooler, faster model of equal quality pass.
- **Freeing the GPU (native D8-3 = audit S1).** `SKILL.md` keys G2 and G-T2 on `services.llm_server_running`, the container check `bench-fit.sh`, `tune-sweep.sh` and `quant-headroom.sh check` enforce. Release still means no `llama-server` holder (`wait-gpu-release.sh`, unchanged).
- **Route residuals and cheap cleanups.** Tuning Q2 gets a next step and a hand-back. Step 9's rollback is keyed on G2 or G3. The anchor route reads preflight before its route check. The switch to local inference names AGENTS.md's final `make restart`. `challenges.md` loses two restated blocks. The served-alias serve step gets its own gate id, G-R2.

`SKILL.md` gets two edits (Freeing the GPU, the route-gate list). Measured on an in-memory copy: 10,891 -> 10,963 bytes after Task 5, and 10,969 after Task 7 (cap 11,000).

**Tech Stack:** bash, jq, yq, curl (a PATH stub in tests), podman (a PATH stub in tests); Python pytest; skill-judge rubric v4.

**Spec:**
- `tmp/judges/r15-native-summary.txt` (judge findings D8-1 to D7-1; audit M1-M3, S1-S7, reuse and entropy lists).
- `tmp/judges/r15-referee-summary.txt`, which holds the lead's check of every referee finding.
- Base state: plan P (`docs/superpowers/plans/2026-11-01-judge-round-14-fixes.md`, executed), and its executor's residual route gaps (in the brief for this plan).

Read-only planning runs on 2026-11-01 (nothing written):
- D8-2 repro on `candidate_row`'s jq (quant-headroom.sh:84-94): entry file `qwen3-8b-q4_k_m.gguf`, a tree with `qwen3-8b-q5_k_m.gguf`, `--quants Q5_K_M` gives `{"cp":"qwen3-8b-Q5_K_M.gguf","reason":"not in repo"}`.
- D8-1: `[[ UD-Q4_K_XL =~ ^[A-Za-z0-9_]+$ ]]` is false. `gsub("UD-Q4_K_XL"; "UD-Q5_K_XL"; "i")` on `Qwen3-8B-UD-Q4_K_XL.gguf` gives `Qwen3-8B-UD-Q5_K_XL.gguf`. `pylib/capacity.py` `quant_headroom` passes `quant` through without validation, so no tracked change is needed.
- D8-4 on the eval fixtures: `lab/Foo-9B-GGUF:Q4_K_M` exits 3 (a name search). `hf.co/lab/Foo-9B-GGUF:Q4_K_M` and `https://huggingface.co/lab/Foo-9B-GGUF:q4_k_m` exit 2 ("not a Hugging Face repo URL").
- `hf-scout.sh --repo lab/Foo-9B-GGUF --file Foo-9B-Q4_K_M.gguf --json --budget-mib 8321 --ram-mib 12000` with `HF_FIXTURE_DIR=evals/files/hf` gives fit `vram`, pick `Foo-9B-Q4_K_M.gguf`, 5600 MiB, quants `["Q4_K_M:5600"]`. `preflight-served.json` has `disk.free_mib` 182000.
- M3 on the eval fixtures: `compare-runs.sh q4.jsonl q5.jsonl` gives ladder delta 1, overall 15.6. With `--correctness-only` it gives ladder 1, overall 22.2, so `quant-switch-yes` still passes. Arithmetic of the audit case at score.sh's weights: equal correctness 0.5, baseline 200 s / 80 C / 50 % CPU, candidate 60 s / 65 C / 20 % CPU gives 49.5 vs 60.5 (+11.0) in the full score and 50 vs 50 with `--correctness-only`.
- D8-3: bench-fit.sh:41, tune-sweep.sh:62 and quant-headroom.sh:134 refuse on `container_running llm-server` (`podman inspect .State.Running`, tools/lib.sh:275). preflight.sh reports the same fact as `services.llm_server_running` (`podman ps` names). SKILL.md:75-77 decides on `.gpu.holders[].comm == llama-server`.
- `scripts/start.sh:139-212` (`make start-llama`) re-renders the units, starts llm-server and provisions OmniRoute. `setup/render-unit.sh:135` (mDNS port) and `setup/network.sh:19` (firewall, `make start`/`restart` only) differ by `llm_server.enabled`. So `make restart` completes a mode switch for the whole stack, but a flow that ends in `make start-llama` serves the model without it.
- bash 5 runs an EXIT trap when the shell dies of SIGTERM (`bash -c 'trap "echo bye" EXIT; sleep 3'` killed with TERM prints `bye`, status 143).

## Global Constraints

- English only. Edit only:
  - `.claude/skills/onboard-local-model/**`;
  - `tests/skills/onboard-local-model/**`;
  - this plan's row in `docs/superpowers/plans/README.md` (already added).
- This plan changes no tracked code, so there is nothing to commit. The skill tree and `tests/skills` stay untracked. If a step turns out to need a tracked file, stop and ask. Earlier README rows stay as they are.
- `SKILL.md` <= 11,000 bytes (`SKILL_MD_MAX_BYTES`, tested). Never raise the cap. Nothing moves into `SKILL.md` beyond Tasks 5 and 7.
- `jev-fast-decision` is a black box, and ai-kit is never named in the skill tree (`test_skill_and_touched_docs_treat_jev_as_a_black_box` scans `scripts/*.sh` too).
- Prose holds no shell logic: a backtick span holds at most two of `;`, `&&`, `||`. Shared rules are stated once (`STATED_ONCE`), and retired wording stays out (`RETIRED_COPIES`, which scans `SKILL.md` and every reference). Changing a pinned phrase means changing its pin in the same task. Before replacing a sentence, grep the tests and `evals/evals.json` for it.
- A reference names another reference only by path, and only for the pairs in `CITES`. This plan adds no new pair.
- Every script keeps `--help` (exit 0; first line `<name>.sh — ...`) and its tests. A behaviour change is red-first: write the test, watch it fail, then implement. Doc pins are written first too, and fail until the text lands.
- After a `.sh` edit run `make validate`. After a `.py` edit (test files included) run `make validate && make test`.
- pytest: one file per run, never two runs at once, through `tmp/r15/pytest-one.sh` (Task 0). It puts `--basetemp` and `TMPDIR` in two fresh `mktemp -d /var/tmp/...` dirs and deletes both afterwards. Never put either under `./tmp/` (AGENTS.md). After each task run the whole `test_onboard_local_model_scripts.py`: many tests pin prose.
- Scratch lives under `./tmp/r15/`; Task 8 removes it. Not allowed: containers, GPU, network or downloads (every `curl` and `podman` in a new test is a PATH stub, and Hugging Face answers come from `HF_FIXTURE_DIR`), and any edit to the live `models.yml` or a capacity card.
- Eval `prompt` is the user's message alone. `setup` is present exactly when `files` is.
- Line numbers below cite the files as of 2026-11-01, before this plan. Later tasks find text by the quoted anchor, not by number.

## Findings ledger (verified 2026-11-01 against the files)

| Finding | Verdict | Task |
|---|---|---|
| native D8-1 (high) = audit M1: UD- quant entries cannot be compared | confirmed: quant-headroom.sh:58 and :116 accept only `^[A-Za-z0-9_]+$`; fit-math.md:37 recommends UD-Q4_K_XL; hf-scout.sh:105 and resolve-target.sh:59 emit `UD-` tokens | 1 |
| native D8-2 = audit M2: lower-case quant files reported "not in repo" | confirmed by the repro above (case-insensitive `gsub`, case-sensitive `.path == $cp`, upper-case insert) | 1 |
| executor residual: no cleanup trap on abort | confirmed: the stand-ins are removed only by quant-headroom.sh:156, which an interrupted or failing `rows=$(...)` never reaches | 2 |
| native D8-4 = audit S2: `org/repo:Q4_K_M` mis-routed | confirmed by the runs above. hf-scout.sh has no quant selector, so H1 cannot honour the quant without one | 3 |
| audit M3: replace rule on `overall` | confirmed: scoring.md:77-82 reads `overall` from a compare without `--correctness-only` (scoring.md:89-94, onboard-flow.md:222, tuning.md:184); score.sh:4 weights speed 15, thermal 10, CPU 5 | 4 |
| native D8-3 = audit S1: G2 keyed on holders, scripts on the container | confirmed in the code (see Spec runs). The premise that an idle router shows no holder is not verified on hardware, but the fix holds either way. Fix the text, not the scripts: a running, idle llm-server can load a model on the next request and contend with a benchmark, so the container check is the safe one | 5 |
| executor residual: tuning Q2 has no next step after a failed fetch or a declined G-Q1, and nothing hands llm-server back | confirmed: tuning.md:173-174 stop at the fetch; the Hand-back list (:202-204) does not name this end | 6 |
| executor residual: step 9 rollback keyed on "after G2" | confirmed: the No-device order (onboard-flow.md:100-102) runs 1-4, then 7a.1-7a.4 (G3), never step 5, so a 6.4 reject after G3 reads as "a failure before G2: nothing is rolled back" (:241-244) and leaves the live edit | 6 |
| executor residual: anchors route check before its preflight stop | confirmed: anchors.md:36 runs `pi auth check` and `cursor-agent models` in item 1; the gateway-only stop comes in item 3 (:46-47) | 6 |
| executor residual: the switch to local inference lacks `make restart` | confirmed in onboard-flow.md:112-114, tuning.md:25-27, preflight.sh:17 and bench-fit.sh:47 (the same message). scoring.md:29-30 already has it | 6 |
| native D1-1 (medium): challenges.md restates code | confirmed in part: the CLI list (:18-25) restates `gen-challenge.sh --help` (:3-12); the Operators table (:63-76) restates `lib/operators.mjs` and has drifted (the code has more hint words, e.g. "off by one", "round up"). The referee's Delete list names the same two blocks. Library, Validity gate and the authoring lint list stay: they state invariants and retry rules no code header states (gate.mjs:1 lists V1-V6; V7 lives elsewhere) | 7 |
| native D2-1 (medium) = audit S7: served-alias reuses "Gate G3" | confirmed for the gate id (served-alias.md:13, :33, :36 vs SKILL.md:71's G3 facts) | 7 |
| native D2-1 rest, D5-1 (medium): interleaved run orders, chains deeper than one level, split onboard-flow.md | dropped: a restructure of a 280-line file under many prose pins and the `CITES` pairs, not cheap or safe this round | - |
| native D7-1 (medium): no eval or test for the three inputs, nor for a resumed download | the three inputs get script tests (Tasks 1, 3) and one eval (Task 3). The resume eval is dropped: the `fetch-gguf.sh` script tests already cover it | 1, 3 |
| native lows D1-2, D4-1, D6-1 | dropped: lows, not in this round's brief. D4-1's text also drops the pinned "got slower" (`DESCRIPTION_ROUTES`) | - |
| audit S3-S6 (tag revision via `api/models`, speed budget 600 s, preflight per poll, no fetch lock) | dropped: not in the brief. S3 is guarded by the size-equality check, S4 was owner decision 6 of plan P | - |
| audit reuse and entropy items (shared quant regex in `tools/lib.sh`, redaction helper, `resolve_tree` via `hf-scout.sh`, the prose-snapshot test layer, hand-back restatements) | not planned (brief: no `common.sh`/lib refactors, no prose-snapshot rewrite) | - |
| referee judge findings D8-1, D8-2, D8-3, D6-1, D7-1, D5-1 and must-fix M1-M3 | refuted by the lead (r15 referee summary) | - |
| referee should S3: the one-test-file cheat (effort-run.sh:215) | stays open as the documented shortcut, guarded by `test_only_known_library_test_files_ship_a_single_test` (plan P owner decision 1) | - |
| referee should S1, S2, S4; lows D1-1, D3-1, D4-1 | dropped: not verified by the lead and not in the brief. S2 is already a `SKILL.md` Gotcha; S4 is already handled by "the first stderr line is the exact bound" | - |

## Not planned (out of scope, with the reason)

- **Prose-snapshot test layer rewrite.** Per the brief. Pins this plan touches are updated in place.
- **`llmenv sample` and `common.sh` refactors, the unused event-pipeline modules.** Per the brief.
- **Anything that needs `SKILL.md` bytes** beyond the 78 Tasks 5 and 7 use. No text moves from references into `SKILL.md`.
- **The description rewrite (D4-1).** The `repo:quant` form already classifies as a Hugging Face repo, which the description names.

## Owner decisions (defaults chosen)

1. **Replace threshold.** Keep "`overall` delta > 10" on the correctness-only compare. That is now 10 points of correctness; at the old weight of 70 it took about 14.3, so the bar is a little lower for quality but gives nothing for speed, temperature or CPU. Alternative: > 14 to keep the old correctness bar.
2. **`hf_repo` shape.** It always carries `quant`, null without a suffix, like `hf_file`. Four existing assertions gain `"quant": None`.
3. **A `:QUANT` suffix on anything but a repo** (an alias such as `lab/x`, a file URL) exits 2. `:latest` (Ollama's default tag) means no quant.
4. **`--quant` matching** is case-insensitive equality on `hf-scout.sh`'s quant token, so `:Q4_K_XL` does not match `UD-Q4_K_XL`.
5. **Freeing the GPU** keys on the container (`services.llm_server_running`). Release still means no `llama-server` holder.
6. **`make restart` in the switch to local inference.** It is named where the owner switches modes (the step-1 stop, the preflight and bench-fit messages). Tuning T2 keeps serving through T6's `make start-llama` and only names `make restart` in its report: a mid-run restart takes OmniRoute and System One down.
7. **challenges.md.** Delete the CLI list and the Operators table; keep Library, Validity gate and the authoring lint list.
8. **Served-alias serve gate** is renamed G-R2. It costs 6 `SKILL.md` bytes.

## Review Focus

1. A UD entry compared with a plain quant (`--quants Q5_K_M` on `m-UD-Q4_K_XL.gguf`). Expected: candidate `m-Q5_K_M.gguf`. Pinned in Task 1 (`test_quant_headroom_takes_ud_quant_tokens`).
2. A quant argument that is not a token (`-rf`, `Q4.K`). Expected: exit 2 before any call. Pinned in Task 1 (same test).
3. A check interrupted by a timeout or a closed terminal (SIGTERM) while it holds sparse stand-ins. Expected: no `budget-*/models` left. Pinned in Task 2 (`test_quant_headroom_check_removes_its_stand_ins_when_interrupted`).
4. Ollama's default tag, `hf.co/org/repo:latest`. Expected: `hf_repo` with `quant` null, not exit 2. Pinned in Task 3 (`test_resolve_target_splits_a_quant_suffix_off_a_repo`).
5. A slash alias with a quant suffix (`lab/x:Q4_K_M`). Expected: exit 2 naming kind `alias`, never a Hugging Face lookup. Pinned in Task 3 (same test).

---

### Task 0: Scratch pytest runner

**Files:**
- Create: `tmp/r15/pytest-one.sh` (scratch; Task 8 deletes `tmp/r15/`)

- [ ] **Step 1: Write the runner**

```bash
#!/usr/bin/env bash
# pytest-one.sh FILE [ARGS...]: one pytest file; --basetemp and TMPDIR in fresh /var/tmp dirs, both removed after.
set -euo pipefail
repo="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
bt="$(mktemp -d /var/tmp/pytest-r15.XXXXXX)"
td="$(mktemp -d /var/tmp/pytest-r15-tmpdir.XXXXXX)"
trap 'rm -rf "$bt" "$td"' EXIT
cd "$repo"
TMPDIR="$td" uv run pytest "$@" -q -p no:cacheprovider --basetemp "$bt/bt"
```

- [ ] **Step 2: Baseline run.** Run `bash tmp/r15/pytest-one.sh tests/skills/onboard-local-model/test_onboard_local_model_scripts.py`, then the same for `test_challenges.py` and `test_latency_profile.py`, one after the other. Expected: all pass (373 in total, as the native judge saw). Record any pre-existing failure before Task 1. Record `bash .claude/skills/onboard-local-model/scripts/gen-challenge.sh --generator-id`; Task 8 compares it.

### Task 1: UD- tokens and lower-case quant files in the quant comparison

**Files:**
- Modify: `.claude/skills/onboard-local-model/scripts/quant-headroom.sh` (header :13-17; `load_entry` :58; `candidate_row` :83-94; `check_quant_arg` :116)
- Modify: `.claude/skills/onboard-local-model/references/tuning.md` (Quant headroom, :112)
- Test: `tests/skills/onboard-local-model/test_onboard_local_model_scripts.py` (two new tests after `test_quant_headroom_never_maps_a_candidate_onto_the_current_file_name`, :2086)

**Interfaces:**
- Produces: a quant token is `^[A-Za-z0-9][A-Za-z0-9_-]*$`, held once in a script variable `quant_re` and used by `load_entry` and `check_quant_arg`. `candidates.json` rows carry the tree's own `file` and `path` spelling.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.usefixtures("real_model_tune_untouched")
def test_quant_headroom_takes_ud_quant_tokens(tmp_path: Path) -> None:
    entry = dict(QH_CONFIG["models"][0], quantization="UD-Q4_K_XL", file="m-UD-Q4_K_XL.gguf",  # type: ignore[index]
                 url=f"https://huggingface.co/o/r/resolve/{QH_SHA}/m-UD-Q4_K_XL.gguf")
    files = {"m-UD-Q4_K_XL.gguf": 5600 * MIB, "m-UD-Q5_K_XL.gguf": 6400 * MIB, "m-Q5_K_M.gguf": 6334 * MIB}
    env = _qh_env(tmp_path, config=QH_CONFIG | {"models": [entry]}, tree=_qh_tree(files))
    result = _qh(env, "check", "--alias", "m", "--quants", "UD-Q5_K_XL,Q5_K_M", "--vram-available-mib", "8321")
    assert result.returncode == 0, result.stderr
    rows = json.loads(_candidates(tmp_path)[0].read_text())["candidates"]
    assert [(r["quant"], r.get("file")) for r in rows] == [("UD-Q5_K_XL", "m-UD-Q5_K_XL.gguf"), ("Q5_K_M", "m-Q5_K_M.gguf")]
    assert _qh(env, "add-entry", "--alias", "m", "--quant", "UD-Q5_K_XL").returncode == 0
    new = next(m for m in yaml.safe_load(Path(env["LLM_ENV_CONFIG"]).read_text())["models"] if m["alias"] == "m-ud-q5_k_xl")
    assert (new["file"], new["quantization"]) == ("m-UD-Q5_K_XL.gguf", "UD-Q5_K_XL")
    for bad in ("-rf", "Q4.K", "Q5;rm"):
        assert _qh(env, "check", "--alias", "m", "--quants", bad).returncode == 2, bad


@pytest.mark.usefixtures("real_model_tune_untouched")
def test_quant_headroom_matches_lower_case_quant_files(tmp_path: Path) -> None:
    good = hashlib.sha256(b"known-bytes").hexdigest()
    entry = dict(QH_CONFIG["models"][0], file="m-q4_k_m.gguf", url=f"https://huggingface.co/o/r/resolve/{QH_SHA}/m-q4_k_m.gguf")  # type: ignore[index]
    tree = _qh_tree({"m-q4_k_m.gguf": 5512 * MIB}) + [{"type": "file", "path": "m-q5_k_m.gguf", "size": 11, "lfs": {"oid": good}}]
    env = _qh_env(tmp_path, config=QH_CONFIG | {"models": [entry]}, tree=tree, curl=True)
    result = _qh(env, "check", "--alias", "m", "--quants", "Q5_K_M", "--vram-available-mib", "8321")
    assert result.returncode == 0, result.stderr
    (q5,) = json.loads(_candidates(tmp_path)[0].read_text())["candidates"]
    assert (q5["file"], q5["size_bytes"], "path" in q5) == ("m-q5_k_m.gguf", 11, False)
    assert _qh(env, "fetch", "--alias", "m", "--quant", "Q5_K_M").returncode == 0
    assert (tmp_path / "models" / "m-q5_k_m.gguf").read_bytes() == b"known-bytes"
    assert any(f"resolve/{QH_SHA}/m-q5_k_m.gguf" in c for c in _calls(tmp_path))
```

- [ ] **Step 2: Run them.** `bash tmp/r15/pytest-one.sh tests/skills/onboard-local-model/test_onboard_local_model_scripts.py -k "ud_quant_tokens or lower_case_quant_files"`. Expected: FAIL. The first dies with "not a plain quant token"; the second lists `{"quant": "Q5_K_M", "reason": "not in repo"}`.

- [ ] **Step 3: Implement.**
  - Near `entry()`: `quant_re='^[A-Za-z0-9][A-Za-z0-9_-]*$'  # UD-Q4_K_XL; no regex metacharacter, so gsub below matches the token literally`. `load_entry` (:58) and `check_quant_arg` (:116) test `=~ $quant_re`, with the same messages.
  - `candidate_row`: keep `$cp` from the case-insensitive `gsub`. Then look the hit up exactly first, else case-insensitively, and use the tree's spelling:

```jq
($p | gsub($tok; $q; "i")) as $cp
| ($tree[0] | map(select(.type == "file")) | (map(select(.path == $cp)) + map(select((.path | ascii_downcase) == ($cp | ascii_downcase)))) | .[0]) as $hit
| ($hit.path // $cp) as $rp
| ($rp | sub(".*/"; "")) as $cf
| if ($cf | ascii_downcase) == ($f | ascii_downcase) then {quant: $q, reason: "quant token not in file name"}
  elif $hit != null then {quant: $q, file: $cf, size_bytes: $hit.size, sha256: ($hit.lfs.oid // null), params_total: $params}
        + (if $rp != $cf then {path: $rp} else {} end)
  # the sharded and "not in repo" branches stay as they are
```

  - Header :13-17: the candidate path is the entry's url path with its quantization token replaced, case-insensitively, then looked up in the tree in any case (the tree's spelling is kept). A quant token is letters, digits, `_` and `-` (`UD-Q4_K_XL`), starting with a letter or digit.
  - tuning.md :112, after "with the quant token replaced": add "(in any case; `UD-` tokens such as `UD-Q5_K_XL` too)".

- [ ] **Step 4: Verify.** `make validate`, then the two tests, then the whole scripts test file. Expected: PASS. `test_quant_headroom_never_maps_a_candidate_onto_the_current_file_name` and `test_quant_headroom_rejects_bad_usage` still pass.

### Task 2: Cleanup trap for the quant check

**Files:**
- Modify: `.claude/skills/onboard-local-model/scripts/quant-headroom.sh` (`cmd_check` :119, :141-143, :156)
- Test: `test_onboard_local_model_scripts.py` (`QH_UV` :1683-1692; one new test after the Task 1 tests)

- [ ] **Step 1: Write the failing test.** In `QH_UV`, make the budget branch able to stall: `*" budget "*) [ -z "${QH_BUDGET_SLEEP:-}" ] || { touch "$CALLS.budget"; sleep "$QH_BUDGET_SLEEP"; }; if [ -n "${QH_BUDGET:-}" ] ...` (the rest of the branch unchanged).

```python
@pytest.mark.usefixtures("real_model_tune_untouched")
def test_quant_headroom_check_removes_its_stand_ins_when_interrupted(tmp_path: Path) -> None:
    env = _qh_env(tmp_path) | {"QH_BUDGET_SLEEP": "30"}
    (tmp_path / "models" / "m-Q4_K_M.gguf").write_bytes(b"GGUF-header")
    cmd = ["bash", str(SCRIPTS / "quant-headroom.sh"), "check", "--alias", "m", "--quants", "Q5_K_M", "--vram-available-mib", "8321"]
    proc = subprocess.Popen(cmd, env=env, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    tune = tmp_path / "out" / "model-tune"
    try:
        deadline = time.monotonic() + 30
        while not Path(env["CALLS"] + ".budget").exists():
            assert proc.poll() is None and time.monotonic() < deadline
            time.sleep(0.1)
        assert list(tune.glob("quant-*/budget-Q5_K_M/models/m-Q5_K_M.gguf"))
        os.killpg(proc.pid, signal.SIGTERM)
        assert proc.wait(timeout=10) != 0
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
    assert not list(tune.glob("quant-*/budget-*/models"))
```

- [ ] **Step 2: Run it.** Expected: FAIL on the last assertion (the killed script leaves `budget-Q5_K_M/models`).

- [ ] **Step 3: Implement.** In `cmd_check`, drop `run` from the `local` list (the EXIT trap reads it after `cmd_check` returns). Right after `mkdir -p "$run"`, set `trap 'rm -rf "$run"/budget-*/models' EXIT  # the stand-ins, also on abort; rm drops a symlink budget_for made, never its target`. Delete the `rm -rf "$run"/budget-*/models` line (:156): the trap replaces it. bash runs the EXIT trap on SIGTERM and SIGINT too (Spec), so no signal traps are needed. Header :11: "(removed once the budgets are read; ...)" becomes "(removed when the check exits, also when it is interrupted; budget-<q>/models.yml stays)".

- [ ] **Step 4: Verify.** `make validate`, the new test, then the whole scripts test file. Expected: PASS, including the existing "the sparse stand-ins are scratch" assertion. `ps` shows no leftover `sleep 30`.

### Task 3: `org/repo:QUANT` as a repo plus a quant selector

**Files:**
- Modify: `.claude/skills/onboard-local-model/scripts/resolve-target.sh` (header :4-15; after the definitions :54-66; after the classification :152)
- Modify: `.claude/skills/onboard-local-model/scripts/hf-scout.sh` (header :3, :9-13; flags :20-45; jq :100-122; check :126-128)
- Modify: `.claude/skills/onboard-local-model/references/onboard-flow.md` (H1 :44-46; H2 :49; fallback :59)
- Modify: `.claude/skills/onboard-local-model/evals/evals.json` (one new eval, `hf-repo-quant`, after `hf-repo-argument`)
- Test: `test_onboard_local_model_scripts.py` (assertions :2127, :2139, :2140, :2142; new tests after `test_resolve_target_parses_hugging_face_urls` and after `test_hf_scout_single_file_skips_split_quants_and_marks_split`; a new eval test)

**Interfaces:**
- Produces: `resolve-target.sh` prints `{kind: "hf_repo", repo, quant}` (`quant` null without a suffix). `hf-scout.sh --repo R --quant Q` picks the quant whose token equals `Q` case-insensitively, whatever the fit (the first shard of a split one). It exits 2 with `no quant <Q> in <repo> at revision <rev>` when the tree that was read has none. `--quant` needs `--repo` and excludes `--file` and `--single-file` (exit 2).

- [ ] **Step 1: Write the failing tests.** Add `"quant": None` to the four `{"kind": "hf_repo", ...}` assertions. New tests:

```python
def test_resolve_target_splits_a_quant_suffix_off_a_repo(tmp_path: Path) -> None:
    assert _rt_json(tmp_path, "org/other-GGUF:Q4_K_M") == {"kind": "hf_repo", "repo": "org/other-GGUF", "quant": "Q4_K_M"}
    assert _rt_json(tmp_path, "hf.co/o/r:q4_k_m") == {"kind": "hf_repo", "repo": "o/r", "quant": "q4_k_m"}
    assert _rt_json(tmp_path, "https://huggingface.co/o/r:UD-Q4_K_XL") == {"kind": "hf_repo", "repo": "o/r", "quant": "UD-Q4_K_XL"}
    assert _rt_json(tmp_path, "hf.co/o/r:latest") == {"kind": "hf_repo", "repo": "o/r", "quant": None}
    alias = _rt(tmp_path, "lab/x:Q4_K_M")
    assert (alias.returncode, alias.stdout) == (2, "") and "only selects a quant of a Hugging Face repo" in alias.stderr
    assert "kind alias" in alias.stderr


def test_hf_scout_quant_picks_that_quant_whatever_the_fit() -> None:
    env = {"HF_FIXTURE_DIR": str(FIX / "hf"), "PATH": "/usr/bin:/bin"}
    base = ("--repo", "lab/Foo-9B-GGUF", "--json", "--budget-mib", "8321", "--ram-mib", "12000")
    scout = lambda *a: _run("bash", SCRIPTS / "hf-scout.sh", *a, env=env)
    (row,) = json.loads(scout(*base, "--quant", "q4_k_m").stdout)
    assert (row["pick"]["file"], row["pick"]["mib"], row["quants"]) == ("Foo-9B-Q4_K_M.gguf", 5600, ["Q4_K_M:5600"])
    missing = scout(*base, "--quant", "Q3_K_S")
    assert missing.returncode == 2 and "no quant Q3_K_S in lab/Foo-9B-GGUF" in missing.stderr
    for extra in (("--file", "Foo-9B-Q4_K_M.gguf"), ("--single-file",)):
        assert scout(*base, "--quant", "Q4_K_M", *extra).returncode == 2, extra
    assert scout("--query", "x", "--quant", "Q4_K_M", "--budget-mib", "1", "--ram-mib", "1").returncode == 2


def test_eval_hf_repo_quant_fixture_matches_its_assertions() -> None:
    rt = json.loads(_run("bash", SCRIPTS / "resolve-target.sh", "lab/Foo-9B-GGUF:Q4_K_M", env={**os.environ, **FIX_ENV}).stdout)
    assert rt == {"kind": "hf_repo", "repo": "lab/Foo-9B-GGUF", "quant": "Q4_K_M"}
    (row,) = json.loads(_run("bash", SCRIPTS / "hf-scout.sh", "--repo", "lab/Foo-9B-GGUF", "--quant", "Q4_K_M", "--json", "--budget-mib",
                             "8321", "--ram-mib", "12000", env={"HF_FIXTURE_DIR": str(FIX / "hf"), "PATH": "/usr/bin:/bin"}).stdout)
    assert (row["fit"], row["pick"]["file"], row["pick"]["mib"]) == ("vram", "Foo-9B-Q4_K_M.gguf", 5600)
    text = _asserts("hf-repo-quant")
    assert all(s in text for s in ("kind hf_repo", "quant Q4_K_M", "--quant Q4_K_M", "Foo-9B-Q4_K_M.gguf (5600 MiB)", "182000",
                                   "not a bare-name search"))
```

- [ ] **Step 2: Run them.** Expected: FAIL (exit 3 or 2 from `resolve-target.sh`; `unknown flag: --quant` from `hf-scout.sh`; no eval `hf-repo-quant`).

- [ ] **Step 3: Implement `resolve-target.sh`.** After `lev_jq` (before `kind=""; result=""`), split the suffix. The code is given because the order matters (`BASH_REMATCH` is overwritten by the second `=~`):

```bash
# llama.cpp -hf and Ollama put a quant after the repo (org/repo:Q4_K_M, hf.co/org/repo:q4_k_m); :latest is Ollama's default tag
qsel=""; tag=""
shopt -s nocasematch
if [ ! -e "$model" ] && [[ "$model" =~ ^(.+):((UD-)?(I?Q[0-9][A-Za-z0-9_]*|BF16|F16|F32)|latest)$ ]]; then
    base="${BASH_REMATCH[1]}" t="${BASH_REMATCH[2]}"
    if [[ "$base" =~ $repo_re || "$base" =~ ^https?://(huggingface\.co|hf\.co)/ ]]; then
        model="$base"; tag="$t"; [[ "$t" == latest ]] || qsel="$t"
    fi
fi
shopt -u nocasematch
```

  After the classification block (before `if [ -n "$scratch" ]`):
  - `[ -z "$tag" ] || [ "$kind" = hf_repo ] || fail "$model:$tag: a :<quant> suffix only selects a quant of a Hugging Face repo, and $model resolves to kind $kind"`;
  - `[ "$kind" != hf_repo ] || result="$(jq -c --arg q "$qsel" '. + {quant: (if $q == "" then null else $q end)}' <<<"$result")"`.

  Header: add "A trailing `:<quant>` (hf-scout.sh's quant regex, any case) or `:latest` after an ORG/NAME or a Hugging Face URL that is not an existing path is split off first. The result must be hf_repo, which carries it as `quant` (`latest`: null); any other kind exits 2." The Output line reads `{kind: hf_repo, repo, quant}` (quant: the suffix, else null).

- [ ] **Step 4: Implement `hf-scout.sh --quant Q`.**
  - Usage :3: `--repo ORG/NAME [--revision REV] [--file PATH | --quant Q] [--single-file]`. Header: one sentence with the Interfaces contract above.
  - Flag `--quant) qsel="$2"; shift 2 ;;`. The `--repo` check becomes "--revision, --file and --quant need --repo". Add the two exclusions (`--file` with `--quant`; `--single-file` with either).
  - jq gets `--arg qs "$qsel"`. The bpw filter (:110) keeps a named quant like a named file: `select($f != "" or $qs != "" or $p == 0 or .bpw >= 1.5)`. The selection (:112): with `$qs` set, `map(select((.quant | ascii_downcase) == ($qs | ascii_downcase)))`; `--file` stays as now. `pick` (:122): `if $f != "" or $qs != "" then ($q[0] // null) else ...`.
  - The check (:126) also runs for `--quant`, with the message `no quant $qsel in $repo at revision ${revision:-main}`.

- [ ] **Step 5: Docs and eval.**
  - onboard-flow.md H1, a bullet after "An `hf_file` URL adds ...": "An `hf_repo` with a `quant` (`org/repo:Q4_K_M`, the llama.cpp `-hf` and Ollama form) adds `--quant <quant>`."
  - H2 first sentence: "`pick` is the named file or quant whatever the scout would choose, else the scout's choice."
  - Fallback (:59): "H1's call without `--file` or `--quant`, with `--single-file` (`--revision` kept)."
  - evals.json, `hf-repo-quant`:
    - prompt `try lab/Foo-9B-GGUF:Q4_K_M as my local model.`
    - setup and files copied from `hf-repo-argument`.
    - expected_output: "The argument is hf_repo lab/Foo-9B-GGUF with quant Q4_K_M; hf-scout.sh --repo with --quant Q4_K_M picks Foo-9B-Q4_K_M.gguf (5600 MiB, fit vram) instead of the scout's Q5_K_M; G1 states that file."
    - assertions:
      - "resolve-target.sh classifies lab/Foo-9B-GGUF:Q4_K_M as kind hf_repo, repo lab/Foo-9B-GGUF, quant Q4_K_M, not a bare-name search"
      - "hf-scout.sh --repo lab/Foo-9B-GGUF --quant Q4_K_M --json --budget-mib 8321 --ram-mib 12000 with HF_FIXTURE_DIR gives fit vram and the pick Foo-9B-Q4_K_M.gguf (5600 MiB), not the default pick Foo-9B-Q5_K_M.gguf"
      - "G1 states Foo-9B-Q4_K_M.gguf, 5600 MiB, 182000 MiB free disk and the destination ~/llm-workspace/models"
      - "Nothing was downloaded, stopped, started or edited"

- [ ] **Step 6: Verify.** `make validate`, the new tests, then the whole scripts test file. Expected: PASS, including `test_eval_input_files_exist_and_the_gguf_fixture_resolves` and the setup-with-files rule.

### Task 4: The replace rule reads a correctness-only compare

**Files:**
- Modify: `.claude/skills/onboard-local-model/references/scoring.md` (Score item 6, :77-82; Head-to-head item 3, :89-94)
- Modify: `.claude/skills/onboard-local-model/references/onboard-flow.md` (7c.4, :222)
- Modify: `.claude/skills/onboard-local-model/references/tuning.md` (Q8, :184; G-Q3, :197)
- Modify: `.claude/skills/onboard-local-model/scripts/compare-runs.sh` (header :4), `scripts/score.sh` (header :10-11)
- Modify: `.claude/skills/onboard-local-model/evals/evals.json` (`quant-switch-yes`: setup, assertions[0])
- Test: `test_onboard_local_model_scripts.py` (`test_eval_quant_switch_fixtures_match_their_assertions` :2675-2681; one new test after `test_compare_runs_reports_deltas_on_one_challenge_set`)

- [ ] **Step 1: Write the failing tests.**

```python
def test_replace_rule_reads_a_correctness_only_compare(tmp_path: Path) -> None:
    kw = {"generator": "g-abc", "timeout_s": 300}
    hot = {"seconds": 200, "gpu": {"peak_temp_c": 80, "avg_cpu_pct": 50, "peak_vram_mib": 1}}
    cool = {"seconds": 60, "gpu": {"peak_temp_c": 65, "avg_cpu_pct": 20, "peak_vram_mib": 1}}
    base = _lane(tmp_path, "a.jsonl", [_row(t, "low", 1, 2, model="a", **kw, **hot) for t in ("l1-s7", "l2-s8")])
    cand = _lane(tmp_path, "b.jsonl", [_row(t, "low", 1, 2, model="b", **kw, **cool) for t in ("l1-s7", "l2-s8")])
    full = json.loads(_compare(base, cand).stdout)["delta_vs_first"][1]
    only = json.loads(_compare(base, cand, flag=("--correctness-only",)).stdout)["delta_vs_first"][1]
    assert full["overall"] > 10 and only["overall"] == 0  # equal quality: only the correctness-only compare keeps the baseline
    rule = _section(_md(REFS / "scoring.md"), "(6) **the one replace rule**", "## Head-to-head")
    assert "`compare-runs.sh --correctness-only`" in rule
    assert "`bash $SK/scripts/compare-runs.sh --correctness-only` with the baseline first" in _md(REFS / "onboard-flow.md")
    assert "`bash $SK/scripts/compare-runs.sh --correctness-only <a-results> <results.jsonl>`" in _md(REFS / "tuning.md")
```

  In `test_eval_quant_switch_fixtures_match_their_assertions`, call `compare-runs.sh --correctness-only` and expect `(1, 22.2)`, with `"overall delta 22.2"` and `"compare-runs.sh --correctness-only"` in the pinned strings (replacing `"overall delta 15.6"`).

- [ ] **Step 2: Run them.** Expected: FAIL on the doc assertions and the eval pin (the arithmetic assertion already holds and shows the defect).

- [ ] **Step 3: Edit.**
  - scoring.md item 6: "put the baseline first in `compare-runs.sh --correctness-only` and read `delta_vs_first`...". "strictly more than 10 points" becomes "strictly more than 10 points of correctness". Keep the pinned phrase `delta > 10` and the rest of the rule. Add one closing sentence: "Correctness only: speed already counts at depth in the verdict step, and the full score's thermal and CPU terms follow the room and the host on lanes run hours apart (equal correctness, a cooler and faster candidate gains about 11 points there)."
  - Head-to-head item 3: `bash $SK/scripts/compare-runs.sh --correctness-only A.jsonl B.jsonl ...` over all result files, anchors included. Drop "(`--correctness-only` when an anchor is among them)" and the sentence "The replace rule reads a compare without the anchor; the anchor gets its own `--correctness-only` compare."
  - onboard-flow.md 7c.4: "`bash $SK/scripts/compare-runs.sh --correctness-only` with the baseline first (`references/scoring.md`, Head-to-head)."
  - tuning.md Q8: `bash $SK/scripts/compare-runs.sh --correctness-only <a-results> <results.jsonl>`. G-Q3: "Several: the highest `overall` of its Q8 compare, ties to the smaller file."
  - compare-runs.sh :4: "(--correctness-only is passed through; the skill passes it on every head-to-head)". score.sh :10-11: "Use it for every head-to-head (compare-runs.sh): thermal and CPU follow the room and the host, not the model, and a cloud anchor never loads them."
  - evals.json `quant-switch-yes`: setup "run only compare-runs.sh --correctness-only on the two files". assertions[0]: "compare-runs.sh --correctness-only with q4.jsonl first gives ladder delta 1 and overall delta 22.2 for gemma4-q5_k_m: the replace rule passes (ladder delta >= 0 and overall delta > 10)".

- [ ] **Step 4: Verify.** `make validate`, the two tests, then the whole scripts test file. Expected: PASS (`test_shared_rules_are_stated_once` included: "delta > 10" stays only in scoring.md).

### Task 5: Freeing the GPU keys on the condition the scripts enforce

**Files:**
- Modify: `.claude/skills/onboard-local-model/SKILL.md` (Freeing the GPU, :75-77)
- Modify: `.claude/skills/onboard-local-model/references/scoring.md` (:31-32), `references/served-alias.md` (R3, :26)
- Modify: `.claude/skills/onboard-local-model/evals/evals.json` (`enabled-alias-stopped` assertion :523; `onboard-baseline-server-stopped` expected_output :607 and assertion :612)
- Test: `test_onboard_local_model_scripts.py` (`test_freeing_the_gpu_waits_for_every_llama_server_holder_to_leave` :887-895; `RETIRED_COPIES` :1072; `test_eval_enabled_alias_stopped_fixture_matches_its_assertions` :2442-2443)

- [ ] **Step 1: Write the failing pins.**
  - At :889, replace the holders assertion with `assert "has \`services.llm_server_running\` true (the container check" in gates`, and add:

```python
    for name in ("bench-fit.sh", "tune-sweep.sh", "quant-headroom.sh"):
        assert "if container_running llm-server; then" in (SCRIPTS / name).read_text(), name
    assert "llm_server_running: $llm_up" in (SCRIPTS / "preflight.sh").read_text()
```

  - `RETIRED_COPIES` gains "a `.gpu.holders[]` entry whose `comm` is", "reads its holders from that read" and "on the holders of the last R1 preflight".
  - At :2442, replace `"no llama-server holder"` with `"llm_server_running true"`.
  - Run: FAIL.

- [ ] **Step 2: Edit.**
  - SKILL.md: "When the last `preflight.sh` read lists a `.gpu.holders[]` entry whose `comm` is `llama-server`, run" becomes "When the last `preflight.sh` read has `services.llm_server_running` true (the container check `bench-fit.sh` and `tune-sweep.sh` refuse on, whatever the holders show), run". "None (gateway-only, or llm-server down): skip both." becomes "False (gateway-only, or llm-server down): skip both." Rewrap at the file's width. The `wait-gpu-release.sh` clause stays.
  - scoring.md :32: "(`SKILL.md` Freeing the GPU reads `services.llm_server_running` from that read)".
  - served-alias.md R3: "free the GPU (`SKILL.md` Freeing the GPU) on the last R1 preflight read."
  - evals.json :523 and :612 (the same string): "G2 follows the preflight read after make start-llama, which has llm_server_running true; the stopped JSON has llm_server_running false and would skip make stop-llama". :607: "G2 reads llm_server_running true from that second read" in place of "G2 reads the llama-server holders from that second read".

- [ ] **Step 3: Verify.** `wc -c < .claude/skills/onboard-local-model/SKILL.md` prints 10963 (<= 11000). Then the whole scripts test file. Expected: PASS.

### Task 6: Route residuals (tuning Q2, step 9, anchors, the switch to local inference)

**Files:**
- Modify: `.claude/skills/onboard-local-model/references/tuning.md` (T2 :25-27; Q2 :173-174; after "A failed Q4-Q6" :191; Hand-back :202-204)
- Modify: `.claude/skills/onboard-local-model/references/onboard-flow.md` (step 1 :112-114; step 9 :241-244)
- Modify: `.claude/skills/onboard-local-model/references/anchors.md` (item 1 :36; item 3 :46-47)
- Modify: `.claude/skills/onboard-local-model/scripts/preflight.sh` (:17), `scripts/bench-fit.sh` (:47)
- Test: `test_onboard_local_model_scripts.py` (`test_bench_fit_names_an_empty_device_before_any_container` :322-325; `test_preflight_names_an_empty_pci_address` :3087-3089; `test_one_rule_serves_a_stopped_llm_server_and_each_route_hands_it_back` :1254-1294; `test_onboarding_and_tuning_branch_on_a_host_without_gpu_config` :978-996; `RETIRED_COPIES`)

- [ ] **Step 1: Write the failing tests and pins.**
  - :324 and :3089: also `"then make restart" in result.stderr`.
  - In `test_one_rule_...`:
    - add "every candidate skipped at Q2" to the Hand-back tuple at :1284;
    - `assert "A declined G-Q1 or a \`fetch\` exit 1: report it and skip this candidate" in _section(tuning, "- Q2.", "- Q3.")`;
    - `assert "Every candidate skipped at Q2 (none measured): no G-Q3; \`make start-llama\` when llm-server was serving at P0 (or T1), then the Hand-back bullet" in tuning`;
    - for step 9: `assert "a failure while neither G2 nor G3 has run" in step9 and "a failed step once G2 or G3 has run (the No-device order runs G3 without G2)" in step9`;
    - for anchors: `item1 = _section(_md(REFS / "anchors.md"), "1. **Route check**", "2. **Gate G4**")`, then `assert item1.index("\`bash $SK/scripts/preflight.sh\` comes first") < item1.index("pi auth check")`, and `assert "preflight.sh\` first" not in _section(_md(REFS / "anchors.md"), "3. The first local lane", "4. `bash $SK/scripts/compare-runs.sh")`.
  - In `test_onboarding_and_tuning_branch_...`:
    - `assert "then \`make restart\`) and stop; nothing changed" in step1`;
    - `assert "the report names AGENTS.md's last step of the switch, \`make restart\`" in _section(tuning, "- T2.", "- T3.")`.
  - `RETIRED_COPIES` gains "or a failed step after G2:" and "G2 stopped nothing".
  - Run: FAIL.

- [ ] **Step 2: Edit the messages.** preflight.sh :17 and bench-fit.sh :47: "(a gateway-only setup skips it; make llama-enable MODEL=<alias>, then make setup, then make restart)".

- [ ] **Step 3: Edit the references.**
  - onboard-flow.md step 1: "(`make llama-enable MODEL=<alias>`, then `LLM_ENV_ASSUME_YES=1 make setup`, which downloads that alias's file when it is missing, then `make restart`) and stop; nothing changed."
  - onboard-flow.md step 9, the two bullets become:
    - "A declined G1 or G2, or a failure while neither G2 nor G3 has run (a `fetch-gguf.sh` exit 1): report it and remove the scratch files; nothing is rolled back, and nothing was stopped."
    - "A no to G5, a declined G3, or a failed step once G2 or G3 has run (the No-device order runs G3 without G2): `make stop-llama`, then restore the rollback copy (`cp -p <rollback copy> ~/.config/llm-env/models.yml`; nothing to restore before G3)."
    - The pinned "After that bullet (a restore or nothing to restore), ..." stays.
  - tuning.md T2, appended: "T6's `make start-llama` serves the model without a restart; the report names AGENTS.md's last step of the switch, `make restart`, for the whole stack (run it when no agent session uses OmniRoute)."
  - tuning.md Q2, a continuation line: "A declined G-Q1 or a `fetch` exit 1: report it and skip this candidate (no Q3-Q11 for it); the next candidate asks its own G-Q1."
  - tuning.md, a bullet after "A failed Q4-Q6 ...": "Every candidate skipped at Q2 (none measured): no G-Q3; `make start-llama` when llm-server was serving at P0 (or T1), then the Hand-back bullet."
  - Hand-back list: "... a declined G-T2, Q1's no-fit verdict, or every candidate skipped at Q2)".
  - anchors.md item 1: replace "after the rule 3 profile check (`SKILL.md`):" with "after the rule 3 profile check (`SKILL.md`). When the comparison runs a local lane itself, `bash $SK/scripts/preflight.sh` comes first: a gateway-only host stops there (`references/scoring.md`, Serving the lane's model), before any route check. The check:". The `pi auth check` command and the Composer text follow unchanged, so preflight comes before the route check.
  - anchors.md item 3: "A local lane the comparison runs itself needs llm-server serving it: `references/scoring.md`, Serving the lane's model, on item 1's preflight read, and `make stop-llama` after the last local lane when that rule started it." The pinned cite and "`make stop-llama` after" stay.

- [ ] **Step 4: Verify.** `make validate`, then the whole scripts test file. Expected: PASS.

### Task 7: challenges.md restatements and the served-alias gate id

**Files:**
- Modify: `.claude/skills/onboard-local-model/references/challenges.md` (CLI list :18-25; Operators :58-76)
- Modify: `.claude/skills/onboard-local-model/references/served-alias.md` (checklist :13-14; R5 :33, :36)
- Modify: `.claude/skills/onboard-local-model/SKILL.md` (route gates, :81)
- Modify: `.claude/skills/onboard-local-model/evals/evals.json` (`served-alias-argument` expected_output :14 and assertion :22; `enabled-alias-stopped` expected_output :517)
- Test: `test_onboard_local_model_scripts.py` (:890; :1243; `RETIRED_COPIES`)

- [ ] **Step 1: Write the failing pins.**
  - :890 becomes "G-T1 to G-T3, G-Q0 to G-Q3, G-R1, G-R2 and G6, each asked in the file that names it".
  - :1243 becomes "A no to G-R2: llm-server stays stopped".
  - `RETIRED_COPIES` gains "CLI (every `--out` must resolve", "| `drop-minus-one` |" and "R5 G3, interference".
  - Run: FAIL.

- [ ] **Step 2: Edit.**
  - challenges.md: delete the "CLI (every `--out` ...):" paragraph and its four bullets; the Generator paragraph already points at `gen-challenge.sh --help`. Under `## Operators`, keep the two intro sentences and replace the table with: "Ids, their change and the hint words kept out of prompts: `challenges/lib/operators.mjs` (`OPERATORS`)."
  - served-alias.md:
    - checklist "R5 G-R2, interference, card";
    - R5 "**Gate G-R2** (serve again; nothing is edited). Its facts: `make start-llama` serves `<alias>` again, so the local OmniRoute lane comes back; the interference gate and the card follow." The commands stay;
    - "A no to G-R2: llm-server stays stopped. ..." (the rest unchanged).
  - SKILL.md: "G-R1 and G6, each" becomes "G-R1, G-R2 and G6, each".
  - evals.json: "G3 only runs make start-llama" -> "G-R2 only runs make start-llama"; "G3 edits nothing and serves ..." -> "G-R2 edits nothing and serves ..."; "G3 serve" -> "G-R2 serve".
  - `grep -n "G3" .claude/skills/onboard-local-model/references/served-alias.md` prints nothing.

- [ ] **Step 3: Verify.** `wc -c < .claude/skills/onboard-local-model/SKILL.md` prints 10969. The whole scripts test file passes. `gen-challenge.sh --generator-id` still prints Task 0's id (`references/` is outside the hashed tree).

### Task 8: Verification and cleanup

- [ ] **Step 1: Byte cap.** `wc -c < .claude/skills/onboard-local-model/SKILL.md`. Expected: 10969 (<= 11000).
- [ ] **Step 2: Guard and shared-rule tests.** `bash tmp/r15/pytest-one.sh tests/skills/onboard-local-model/test_onboard_local_model_scripts.py -k "jev_as_a_black_box or stated_once or cite_each_other or router_that_loads or only_existing_scripts or long_shell_logic"`. Expected: PASS.
- [ ] **Step 3: Skill suites, one at a time.** Run the three files of Task 0 Step 2, in turn. Expected: all pass. The count is the baseline plus 7 new tests (Task 1: 2, Task 2: 1, Task 3: 3, Task 4: 1; the other test changes are edits).
- [ ] **Step 4: Repo checks.** With `TMPDIR` unset (pytest's default temp dir is outside the repo), run `make validate && make test` alone. Expected: "All checks passed." and a green suite.
- [ ] **Step 5: Route walk** (read as a new agent who has only `SKILL.md`; nothing runs against the host). For each route, follow the route table row, load only the files it names, and confirm every branch ends in a step, a gate or a stop:
  1. `unsloth/X-GGUF:UD-Q4_K_XL` on a served host: `hf_repo` with `quant`, H1 with `--quant`, H2-H5, steps 4-9; the replace rule on `--correctness-only` at 7c.4 and 8(b).
  2. The quant comparison on that UD entry: P0-P2, Q1 with `UD-Q5_K_XL`, a declined G-Q1, then "Every candidate skipped at Q2", the Hand-back.
  3. The quant comparison on a lower-case-token repo: Q1 finds the candidate, Q2 fetches the tree's spelling.
  4. An enabled llm-server that runs with no model loaded: G2 still stops it (`services.llm_server_running` true), then `bench-fit.sh` runs.
  5. A gateway-only host on the anchor route: the stop comes before any `pi auth check`, and its message names `make restart`.
  6. The No-device order with a 6.4 reject after G3: step 9 restores the rollback copy.
  7. The served-alias re-measure: R1, R3 (the last R1 read), R5 under G-R2, R6.
  Report any branch without a next step as a finding; do not fix it in this plan.
- [ ] **Step 6: Cleanup.** `rm -rf tmp/r15`. Check that `ls /var/tmp | grep pytest-r15` prints nothing, that no `sleep 30` from Task 2 runs (`ps` by the PIDs the test started), and that `podman ps -a` shows nothing this plan started (it starts nothing). `git status --short` shows no tracked change besides `docs/superpowers/plans/README.md` (its new row); this plan file stays untracked like plans G-P. Judges are re-run by the orchestrator, not by this plan.

## Round-1 review outcome (approved, 0 critical/high, 1 confirmed medium, 5 lows)

Review round 1 (native + referee-two, lead-verified against the code; tmp/plan-q-review/review-round-1.md) confirmed no critical or
high finding; every referee finding was refuted. Plan status: approved for execution. The implementer must apply:

- M1 (Task 6): the switch to local inference on onboarding is not finished on a keep. A gateway-only host runs G3 (7a.3-7a.4:
  `make llama-enable` plus `make setup`) and serves with 7a.6's `make start-llama`; a yes to G5 keeps that switch, yet step 9's Keep
  bullet never names `make restart`. Fix: append to step 9's Keep bullet "A host that was gateway-only before G3: the report names
  AGENTS.md's last step of the switch, `make restart` (run it when no agent session uses OmniRoute)." Pin in
  `test_onboarding_and_tuning_branch_on_a_host_without_gpu_config`:
  `assert "A host that was gateway-only before G3: the report names AGENTS.md's last step of the switch, \`make restart\`" in step9`
  (red first).
- L3 (Task 4): `selection-criteria.md` section 7 ("runs one `compare-runs.sh` with the baseline first") and eval `quant-head-to-head`
  ("The lanes are compared with compare-runs.sh, ...") name `compare-runs.sh --correctness-only` like 7c.4 and Q8. Pin both strings
  in the new `test_replace_rule_reads_a_correctness_only_compare`.
- L4 (Task 2): if SIGTERM reaches only the main bash PID, the `rows=$(...)` subshell can outlive the EXIT trap and write a later
  quant's stand-in. Fix: inside the loop, remove each quant's stand-in right after its budget is read
  (`rm -rf "$run/budget-$q/models"`), so a subshell that outlives the main shell leaves nothing; the EXIT trap still covers a group
  kill mid-budget. Extend the Task 2 test with a run that signals only the main PID (`os.kill(proc.pid, SIGTERM)`), waits for the
  stalled budget to finish, and asserts no `budget-*/models` remains.
- Not applied: L1 (the plan already states that Task 4's arithmetic assertion is green before the edit), L2 (`o/r:Q4_K_M:Q5_K_M`
  falls through to a name search that exits 3; harmless), L5 (a load flake in tracked `test_update_omniroute_script.py`, which this
  plan does not touch).
