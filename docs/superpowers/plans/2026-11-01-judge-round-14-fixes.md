# Judge Round 14 Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the confirmed findings of round 14 of the onboard-local-model skill judges. The native lane gave 88.8 % B (1 high, 4 judge mediums, 3 audit must-fix). The referee gave 84.8 % B; the lead refuted every judge finding and confirmed one audit item. The next round should give >= 90 % from both lanes with no critical or high finding.

**Architecture:** Five groups of change:
- **Resumable download (native high D8-1 = audit M1).** A new `scripts/fetch-gguf.sh` downloads into `<file>.part`, resumes it, checks the size (and the sha256 when given), then renames. Gate G1 and `quant-headroom.sh fetch` both call it, so one tested script holds the logic that is prose today.
- **GPU release (audit M2 = native D8-3).** `wait-gpu-release.sh` treats the GPU as released once no `llama-server` holder is left. Used VRAM is reported but never compared, so a System One reload during the wait no longer fails it. `--below-mib`, the U/H arithmetic and the holder sum go.
- **Ranking (audit M3).** `rank-candidates.sh --evidence` excludes a card whose scout row failed a gate (`scout gate: <gate>`).
- **Scripted naming and routes without dead ends (native mediums).**
  - `scripts/new-alias.sh` computes A1-A3.
  - A gateway-only host gets a stop with a next step on the anchor, calibration and no-argument routes.
  - The host without `gpu.device_name` gets one explicit order instead of in-place jumps.
  - Step 9 names "the first step-1 read".
- **Cheap should-fix and entropy items.** The quant check removes its sparse stand-ins (S8). A scheme-less `huggingface.co/...` argument resolves (S9). The bench-fit presets claim is corrected (S4). Two restating blocks are deleted (E1, E4). The one-test-file limit is documented and guarded; the generator id stays unchanged.

`SKILL.md` gets one edit (Freeing the GPU and the G2 row). Measured on a scratch copy: 10,987 -> 10,891 bytes.

**Tech Stack:** bash, jq, yq, curl (a PATH stub in tests); Python pytest; skill-judge rubric v4.

**Spec:**
- `tmp/judges/r14-native-summary.txt` (judge findings D1-1 to D8-4; audit M1-M3, S1-S9, E1-E5).
- `tmp/judges/r14-referee-summary.txt`, which holds the lead's check of every referee finding.
- Base state: plan O (`docs/superpowers/plans/2026-11-01-judge-round-13-and-audit-fixes.md`, executed).

Read-only planning runs on 2026-11-01 (scratch under `tmp/`, removed):
- M3 repro. Scout rows `a/ok` (vram, 4.8 bpw) and `b/lowbit` (vram-lowbit, 2.1 bpw), with identical cards. Without `--evidence`, `gates_failed` is `[{"id":"b/lowbit","gate":"fit"}]`. With `--evidence`, `ranked` is `["b/lowbit","a/ok"]` and `excluded` is `[]`.
- The eval fixtures (`scout-coder.json`, `scout-agent.json`, `evidence/`) rank `lab-a/Coder-9B-GGUF` 23 and `lab-d/MoE-30B-A3B-GGUF` 20. The three gated rows have no card, so the M3 fix changes no eval.
- `SKILL.md` with Task 2's edit applied to a scratch copy measures 10,987 -> 10,891 bytes.
- `challenges/gen.mjs`:28: the generator id is a sha256 over the whole `challenges/` tree. Counting `test(` calls in the 15 project test files and the decoy test files: only `projects/log-parser/test/report.test.js` ships a single test.

## Global Constraints

- English only. Edit only:
  - `.claude/skills/onboard-local-model/**`;
  - `tests/skills/onboard-local-model/**`;
  - this plan's row in `docs/superpowers/plans/README.md` (already added).
- This plan changes no tracked code, so there is nothing to commit. The skill tree and `tests/skills` stay untracked. If a step turns out to need a tracked file, stop and ask. Earlier README rows stay as they are.
- `SKILL.md` <= 11,000 bytes (`SKILL_MD_MAX_BYTES`, tested). Never raise the cap. Shorten before adding.
- `jev-fast-decision` is a black box, and ai-kit is never named in the skill tree (`test_skill_and_touched_docs_treat_jev_as_a_black_box` scans `scripts/*.sh` too, so the two new scripts are covered).
- Prose holds no shell logic: a backtick span holds at most two of `;`, `&&`, `||`. Shared rules are stated once (`STATED_ONCE`), and retired wording stays out (`RETIRED_COPIES`). Changing a pinned phrase means changing its pin in the same task. Before replacing a sentence, grep the tests and `evals/evals.json` for it.
- A reference names another reference only by path, and only for the pairs in `CITES`. This plan adds no new pair: `selection-criteria.md` names none; `anchors.md` and `challenges.md` name only `scoring.md`; `local-gguf.md` names `onboard-flow.md`.
- Every script keeps `--help` (exit 0; first line `<name>.sh — ...`) and its tests. `SCRIPT_NAMES` gains `fetch-gguf` and `new-alias`. A behaviour change is red-first: write the test, watch it fail, then implement. Doc tests are written first too, and fail until the text lands.
- After a `.sh` edit run `make validate`. After a `.py` edit (test files included) run `make validate && make test`.
- pytest: one file per run, never two runs at once, through `tmp/r14/pytest-one.sh` (Task 0). It puts `--basetemp` in a fresh `mktemp -d /var/tmp/pytest-r14.XXXXXX` and `TMPDIR` in a `./tmp/r14/` scratch dir, and deletes both afterwards. `make test` runs with `TMPDIR` unset: the skill conftest fails fast when pytest's temp dir is inside the repo.
- Scratch lives under `./tmp/r14/`; Task 10 removes it. Not allowed: containers, GPU, network or downloads (every `curl` in a test is a PATH stub), and any edit to the live `models.yml` or a capacity card.
- Eval `prompt` is the user's message alone. `setup` is present exactly when `files` is.
- Line numbers below cite the files as of 2026-11-01, before this plan. Later tasks find text by the quoted anchor, not by number.

## Findings ledger (verified 2026-11-01 against the files)

| Finding | Verdict | Task |
|---|---|---|
| native D8-1 (high) = audit M1: G1 downloads to the final name | confirmed. onboard-flow.md:142 runs `curl -fL --continue-at - -o <models_dir>/<entry file>`. Then :141 ("already in `<models_dir>`: no G1") and :149 ("already there: never delete or overwrite it; report both values and stop") turn a dropped download into a file no re-run can resume. quant-headroom.sh:198-223 already does `.part`, resume, sha256 and `mv` (with no size check) | 1 |
| audit M2 = native D8-3: `U - H + 512` false-fails | confirmed. wait-gpu-release.sh:32 compares `used_mib` with the target. System One (the `python3` holder in every preflight fixture) reloads about 3.7 GiB on the next Laya request (SKILL.md:94). Used VRAM can therefore stay above the target with llm-server gone | 2 |
| audit M3: `--evidence` ranks cards of scout-gated rows | confirmed by a run (see Spec). rank-candidates.sh:65 checks card ids against `$scout.rows`, and :75-80 rank every card without a false card gate; `$scout.failed` is never read. This breaks selection-criteria.md:78 ("a candidate that fails one never ranks") and the sub-3.5-bit NEVER | 3 |
| native D8-2 (medium) = audit S1: no next step on a gateway-only host | confirmed. scoring.md:25-37 covers enabled-but-stopped, stack down and a budget failure. anchors.md:46-47 and challenges.md:126-128 apply it only when "enabled but stopped". selection-criteria.md:21 (Scout step 1) has no branch for preflight's exit 2 (preflight.sh:17) | 5 |
| native D6-1 (medium): A1-A3 and the holder sum are prose | confirmed for A1-A3 (onboard-flow.md:18-29: strip, lowercase, runs to `-`, 60-character cap, sibling-quant rule, `-2`/`-3`). The holder sum goes with Task 2. `resolve-target.sh` cannot carry a `new_alias`, because an `hf_file` has no size and a scout pick has no file until `hf-scout.sh` runs. Hence a separate script | 4, 2 |
| native D2-1 (medium): in-place jumps | confirmed: onboard-flow.md:110-113 ("7a resumes at 7a.5") and local-gguf.md:19-20 | 6 |
| native D8-4 (low) = audit S6: "when step 1 found llm-server serving" | confirmed. onboard-flow.md:243 and :245 become ambiguous once step 1 re-runs after serving | 6 |
| native lows D1-1, D4-1, D5-1, D7-1 | dropped: not in this round's brief. D4-1's text drops the pinned "ladder challenge" (`DESCRIPTION_ROUTES`) and adds about 60 bytes. D1-1's phrases are pinned, and the bytes are not needed. D5-1 is a restructure; Task 6 removes the jumps it names. D7-1's behaviours get script tests (Task 1) and doc tests (Task 5) | - |
| referee high D8-1 (A1); mediums D2-1, D5-1, D7-1 and D7-2 (A2) | refuted by the lead. scripts/start.sh:36-40 prints "the stack is not running", and `printf` is a builtin | - |
| referee A3 (D7-3): a one-test file can score as solved | confirmed. effort-run.sh:215-217 takes `max(verdict.total, tests_total)`, and `log-parser/test/report.test.js` is the only shipped file with one test. A second test there changes the generator id (gen.mjs:28) | 9 (owner decision 1) |
| referee lows D1-1, D4-1, D6-1; Should A4, A5; Delete A6 | dropped: not in this round's brief, and the lead did not verify them | - |
| S2: guard root defaults to the whole repo | dropped. The guard is the tripwire for writes to tracked files the scorer sources (`tools/lib.sh` and the skill scripts outside `challenges/`), so narrowing it weakens score integrity. Rule 2 already bars concurrent sessions, and `ONBOARD_GUARD_ROOT` exists | - |
| S3: config redaction written three times | dropped. The three copies agree on both secrets `models.yml.example` has (`server.api_key`, `omniroute.initial_password`). A shared helper would mean a tracked `tools/lib.sh` edit for one yq expression | - |
| S4: bench-fit duplicates benchmark.sh; "same flag presets" | the refactor is dropped (it edits tracked `scripts/benchmark.sh`). The wording error is confirmed: benchmark.sh:106-110 passes only `--n-gpu-layers` and `--n-cpu-moe`, while bench-fit.sh:59-60 adds `-fa`, `-ctk`, `-ctv` and `-ub` | 8 |
| S5: two `hf()` helpers, two fixture schemes | dropped: it renames fixtures across `evals/files/hf` for no behaviour change | - |
| S7: speed term budget 600 s against a 300 s cap | dropped (owner decision 6): it changes every score's scale | - |
| S8: sparse copies left by the quant check | confirmed (quant-headroom.sh:102-107). Only `budget-<q>/models/` goes, because the test at :1713 reads `budget-Q5_K_M/models.yml` | 7 |
| S9: scheme-less `huggingface.co/<org>/<repo>` exits 2 | confirmed (resolve-target.sh:129-132 treats it as path-like) | 7 |
| E1: unreachable `llm_server.enabled: false` bullet | confirmed: resolve-target.sh:175 sets it true on every `$S` | 8 |
| E2: hf-scout table branch | refuted in part. `test_hf_scout_defaults_to_the_smaller_preflight_budget` reads the table's first line (`budget: VRAM 9000 MiB`), the only output that shows a defaulted budget | - |
| E3: U/H arithmetic | goes with Task 2 | 2 |
| E4: "Where things live" restates | confirmed. AGENTS.md has the config, the models dir and clean/prune, and step 1 and NEVER have the gateway-only text. Only the capacity-card bullet stays | 8 |
| E5: prose-snapshot test layer | not planned (brief) | - |

## Not planned (out of scope, with the reason)

- **Prose-snapshot test layer** (E5). Owner call: not this round. Pins this plan touches are updated in place.
- **`llmenv sample` and `common.sh` refactors.** Per the brief.
- **The unused event-pipeline modules.** Deleting them changes the generator id.
- **Anything that needs `SKILL.md` bytes** beyond the 109 that Task 2 frees. Nothing in this plan adds to `SKILL.md`.

## Owner decisions (defaults chosen)

1. **One-test file** (referee A3). A second test in `log-parser/test/report.test.js` changes the generator id and retires every `seeds.json`. Default: leave the file. The `shortcut:` comment names the case, and a guard test pins the set of one-test files, so the fix rides on the next library edit.
2. **Gateway-only host on the anchor and calibration routes.** Default: report that nothing local was measured, name AGENTS.md's switch to local inference, and stop before any lane or G4. These routes never enable local inference themselves: that downloads a file and changes the host mode, which G3 and tuning's T2 own.
3. **Release = no `llama-server` holder.** Used VRAM is reported, never compared. The alternative (target plus System One's reserve) is rejected: Laya's load state during the wait cannot be known.
4. **A card for a scout-gated row** is excluded with reason `scout gate: <gate>`, not an exit 2. One stray card must not void the ranking. A card for an id absent from every scout file still exits 2.
5. **`new-alias.sh` is a separate script.** The quant token comes from the caller (no third copy of the quant regex). A live entry's token is its own `quantization`.
6. **Score speed budget:** leave 600 s.
7. **`fetch-gguf.sh` free disk** counts what the `.part` already holds: it needs BYTES - `.part` + 1024 MiB.
8. **Guard root:** leave the whole repo.

## Review Focus

1. A `.part` larger than the expected size (another revision's partial under the same name). Expected: removed and downloaded again, never "verified". Pinned in Task 1 (`test_fetch_gguf_reuses_a_complete_part_and_restarts_an_oversized_one`).
2. A Laya request reloads System One during the wait, so used VRAM rises while llm-server leaves. Expected: released. Pinned in Task 2 (`test_wait_gpu_release_returns_once_no_llama_server_holds_the_gpu_even_when_system_one_reloads`).
3. Little free disk, but a large `.part` already on disk. Expected: the resume runs when the remainder fits. Pinned in Task 1 (`test_fetch_gguf_needs_free_disk_only_for_what_the_part_lacks`, with a sparse `.part`).
4. An argument that looks like a scheme-less URL but is an existing local path (`hf.co/x-Q4_K_M.gguf` in the cwd). Expected: it stays a local GGUF. Pinned in Task 7 (`test_resolve_target_keeps_an_existing_path_that_looks_like_a_scheme_less_url`).
5. A local GGUF without a quant token (`MyModel.gguf`, no `--quant`) whose name is taken. Expected: an alias from the whole name, with `-2`, `-3`... appended. Pinned in Task 4 (`test_new_alias_appends_a_counter_for_a_taken_name_and_works_without_a_quant`).

---

### Task 0: Scratch pytest runner

**Files:**
- Create: `tmp/r14/pytest-one.sh` (scratch; Task 10 deletes `tmp/r14/`)

- [ ] **Step 1: Write the runner**

```bash
#!/usr/bin/env bash
# pytest-one.sh FILE [ARGS...]: one pytest file; --basetemp in a fresh /var/tmp dir, TMPDIR in a ./tmp/r14 dir; both removed after.
set -euo pipefail
repo="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
bt="$(mktemp -d /var/tmp/pytest-r14.XXXXXX)"
td="$(mktemp -d "$repo/tmp/r14/tmpdir.XXXXXX")"
trap 'rm -rf "$bt" "$td"' EXIT
cd "$repo"
TMPDIR="$td" uv run pytest "$@" -q -p no:cacheprovider --basetemp "$bt/bt"
```

- [ ] **Step 2: Baseline run.** Run `bash tmp/r14/pytest-one.sh tests/skills/onboard-local-model/test_onboard_local_model_scripts.py`, then the same for `test_challenges.py` and `test_latency_profile.py`. Expected: all pass (348 in total, as the native judge saw). Record any pre-existing failure before Task 1. Also record the output of `bash .claude/skills/onboard-local-model/scripts/gen-challenge.sh --generator-id`; Task 9 compares it.

### Task 1: One tested download script for G1 and the quant fetch

**Files:**
- Create: `.claude/skills/onboard-local-model/scripts/fetch-gguf.sh`
- Modify: `.claude/skills/onboard-local-model/scripts/quant-headroom.sh` (header :19-21; `cmd_fetch` :198-223)
- Modify: `.claude/skills/onboard-local-model/references/onboard-flow.md` (4.3 at :139-149; step 9 at :239-240 and :246-247)
- Modify: `.claude/skills/onboard-local-model/references/tuning.md` (Q2 at :173-174)
- Test: `tests/skills/onboard-local-model/test_onboard_local_model_scripts.py` (`SCRIPT_NAMES` :30, the missing-arguments list :226, the assertion at :1790, `RETIRED_COPIES` :1062, new tests after the quant fetch tests at :1827)

**Interfaces:**
- Produces: `fetch-gguf.sh --url URL --dest FILE --size BYTES [--sha256 HEX]`.
  - Exit 0 prints `already present: FILE` or `fetched FILE (size verified)` (`, sha256 verified` appended when `--sha256` was given).
  - Exit 1 messages contain `size mismatch`, `sha256 mismatch`, `free disk`, `never` (another file at FILE) or `re-run` (curl failed, `.part` kept).
  - Exit 2: usage.

- [ ] **Step 1: Write the failing tests** (after `test_quant_headroom_fetch_refuses_without_free_disk_and_for_unavailable_quants`). The curl stub really resumes: it appends what the `.part` lacks, and in mode `drop` its first call stops after 5 bytes.

```python
KNOWN = b"known-bytes"
FETCH_URL = f"https://huggingface.co/o/r/resolve/{QH_SHA}/m.gguf"
FETCH_CURL = r"""out=; p=
for a in "$@"; do [ "$p" = -o ] && out="$a"; p="$a"; done
echo "curl $* part=$(stat -c %s "$out" 2>/dev/null || echo none)" >>"$CALLS"
n="$(cat "$CALLS.n" 2>/dev/null || echo 0)"; echo $((n + 1)) >"$CALLS.n"
if [ "${FETCH_MODE:-full}:$n" = drop:0 ]; then printf 'known' >>"$out"; exit 18; fi
printf 'known-bytes' | tail -c +$(( $(stat -c %s "$out" 2>/dev/null || echo 0) + 1 )) >>"$out"
"""


def _fetch(tmp_path: Path, *, size: int = len(KNOWN), sha: str | None = hashlib.sha256(KNOWN).hexdigest(),
           mode: str = "full") -> tuple[subprocess.CompletedProcess[str], Path]:
    stub = tmp_path / "bin"
    stub.mkdir(exist_ok=True)
    (stub / "curl").write_text(f"#!/usr/bin/env bash\n{FETCH_CURL}")
    (stub / "curl").chmod(0o755)
    dest = tmp_path / "models" / "m.gguf"
    args = ["--url", FETCH_URL, "--dest", dest, "--size", str(size), *(["--sha256", sha] if sha else [])]
    env = {"PATH": f"{stub}:/usr/bin:/bin", "CALLS": str(tmp_path / "calls"), "FETCH_MODE": mode}
    return _run("bash", SCRIPTS / "fetch-gguf.sh", *args, env=env), dest


def test_fetch_gguf_downloads_into_a_part_file_and_renames_it_once_verified(tmp_path: Path) -> None:
    result, dest = _fetch(tmp_path)
    assert result.returncode == 0, result.stderr
    assert dest.read_bytes() == KNOWN and not dest.with_name("m.gguf.part").exists() and "sha256 verified" in result.stdout
    assert any(f"--continue-at - -o {dest}.part {FETCH_URL}" in c for c in _calls(tmp_path))


def test_fetch_gguf_resumes_an_interrupted_download(tmp_path: Path) -> None:
    first, dest = _fetch(tmp_path, mode="drop")
    part = dest.with_name("m.gguf.part")
    assert first.returncode == 1 and "re-run" in first.stderr and part.read_bytes() == b"known" and not dest.exists()
    second, _ = _fetch(tmp_path, mode="drop")
    assert second.returncode == 0, second.stderr
    assert dest.read_bytes() == KNOWN and not part.exists() and "part=5" in _calls(tmp_path)[-1]


def test_fetch_gguf_reuses_a_complete_part_and_restarts_an_oversized_one(tmp_path: Path) -> None:
    part = tmp_path / "models" / "m.gguf.part"
    part.parent.mkdir()
    part.write_bytes(KNOWN)
    result, dest = _fetch(tmp_path)
    assert result.returncode == 0 and dest.read_bytes() == KNOWN and _calls(tmp_path) == []
    dest.unlink()
    part.write_bytes(KNOWN + b"-of-another-revision")
    result, _ = _fetch(tmp_path)
    assert result.returncode == 0, result.stderr
    assert dest.read_bytes() == KNOWN and "part=none" in _calls(tmp_path)[-1]


@pytest.mark.parametrize(("size", "sha", "message"), [(12, hashlib.sha256(KNOWN).hexdigest(), "size mismatch"),
                                                      (len(KNOWN), "0" * 64, "sha256 mismatch")])
def test_fetch_gguf_removes_only_its_part_on_a_mismatch(tmp_path: Path, size: int, sha: str, message: str) -> None:
    result, dest = _fetch(tmp_path, size=size, sha=sha)
    assert result.returncode == 1 and message in result.stderr
    assert not dest.exists() and not dest.with_name("m.gguf.part").exists()


def test_fetch_gguf_never_touches_a_file_already_at_the_destination(tmp_path: Path) -> None:
    dest = tmp_path / "models" / "m.gguf"
    dest.parent.mkdir()
    dest.write_bytes(KNOWN)
    result, _ = _fetch(tmp_path)
    assert result.returncode == 0 and "already present" in result.stdout
    dest.write_bytes(b"owner")
    result, _ = _fetch(tmp_path)
    assert result.returncode == 1 and str(dest) in result.stderr and "never" in result.stderr
    assert dest.read_bytes() == b"owner" and not dest.with_name("m.gguf.part").exists() and _calls(tmp_path) == []


def test_fetch_gguf_checks_the_size_alone_without_a_sha256(tmp_path: Path) -> None:
    result, _ = _fetch(tmp_path, sha=None)
    assert result.returncode == 0 and "size verified" in result.stdout and "sha256" not in result.stdout


def test_fetch_gguf_needs_free_disk_only_for_what_the_part_lacks(tmp_path: Path) -> None:
    (tmp_path / "models").mkdir()
    result, _ = _fetch(tmp_path, size=1 << 60)
    assert result.returncode == 1 and "free disk" in result.stderr and _calls(tmp_path) == []
    size = shutil.disk_usage(tmp_path / "models").free + 512 * MIB  # size + 1024 MiB never fits; the remainder does
    with (tmp_path / "models" / "m.gguf.part").open("wb") as handle:
        handle.truncate(size - 1)  # sparse: no disk used
    result, _ = _fetch(tmp_path, size=size)
    assert "free disk" not in result.stderr and len(_calls(tmp_path)) == 1
    assert result.returncode == 1 and "size mismatch" in result.stderr  # the stub has nothing to append
```

Also add `"fetch-gguf"` to `SCRIPT_NAMES` and `"fetch-gguf.sh"` to the `test_missing_required_arguments_exit_two` list. Add one usage case: `_run("bash", SCRIPTS / "fetch-gguf.sh", "--url", "u", "--dest", "d", "--size", "x").returncode == 2`.

- [ ] **Step 2: Run to verify they fail.** Run `bash tmp/r14/pytest-one.sh tests/skills/onboard-local-model/test_onboard_local_model_scripts.py -k fetch_gguf`. Expected: every new test fails (no such script).

- [ ] **Step 3: Implement `fetch-gguf.sh`.** It is standalone: it sources nothing and needs curl, sha256sum and df. The header is the spec, verbatim:

```bash
#!/usr/bin/env bash
# fetch-gguf.sh — download one model file into FILE.part, verify it, then rename it to FILE; a re-run resumes an interrupted download.
# usage: fetch-gguf.sh --url URL --dest FILE --size BYTES [--sha256 HEX]
# FILE already there: an equal size (and sha256, when given) prints `already present: FILE` and exits 0 without a download; any
# other file there exits 1 untouched: it is never resumed onto, overwritten or deleted. Otherwise FILE.part (this script's own
# scratch) is resumed with `curl -fL --continue-at - -o FILE.part URL`: a .part of exactly BYTES skips curl, a larger one is
# removed first, and the free disk under FILE's directory must hold BYTES minus the .part plus 1024 MiB. A failed curl keeps the
# .part for a re-run (exit 1). The size, then the sha256 when given, must match: a mismatch removes only the .part (exit 1). Then
# FILE.part is renamed to FILE and `fetched FILE (size verified[, sha256 verified])` is printed. Exit 2 on usage errors.
# Needs curl, sha256sum, df. Writes only FILE.part and FILE.
set -euo pipefail
```

Approach: parse the flags as `wait-gpu-release.sh` does: a non-integer `--size`, or a `--sha256` that is not 64 hex characters, exits 2. Order of checks: FILE, then `mkdir -p` of its directory, then the `.part` size (0 when absent), then the free disk from `df --output=avail -B1`, then curl only while `.part` < BYTES, then size, sha256 and `mv`.

- [ ] **Step 4: `quant-headroom.sh fetch` calls it.**
  - In `cmd_fetch`, keep everything up to and including the `[ -n "$oid" ] || die ...` line. Replace the rest with one line: `bash "$here/fetch-gguf.sh" --url "https://huggingface.co/$repo/resolve/$sha/$cpath" --dest "$MODELS_DIR/$cfile" --size "$size" --sha256 "$oid"`. Drop the locals that are no longer used (`dest part free have`).
  - Header :19-21 becomes: `# fetch: downloads one candidate file into the models dir through fetch-gguf.sh (verified against the tree's size and lfs.oid; an interrupted fetch resumes in its .part; a different file already at the name exits 1 untouched).`
  - Test :1790 changes from `"sha256 mismatch" in result.stderr` to `"mismatch" in result.stderr`, because the size now fails first.

- [ ] **Step 5: Run the fetch tests.** Run `bash tmp/r14/pytest-one.sh tests/skills/onboard-local-model/test_onboard_local_model_scripts.py -k "fetch"`. Expected: PASS, the four `test_quant_headroom_fetch_*` tests included.

- [ ] **Step 6: Write the failing doc test**

```python
def test_g1_downloads_through_fetch_gguf_and_can_resume() -> None:
    flow = _md(REFS / "onboard-flow.md")
    g1 = _section(_section(flow, "4. **Fit and configure.**", "5. **Baseline lane"), "**Gate G1**", "4. Select and check")
    assert "`bash $SK/scripts/fetch-gguf.sh --url <url> --dest <models_dir>/<entry file> --size <size_bytes>`" in g1
    assert all(s in g1 for s in ("`--sha256 <pick.sha256>`", "`<entry file>.part`", "size only")) and "curl" not in g1
    assert "left a file or a `.part` in the models directory" in _section(flow, "9. **Keep or revert", "## Entry template")
    assert "fetch-gguf.sh" in _section(_md(REFS / "tuning.md"), "- Q2.", "- Q3.")
```

Add to `RETIRED_COPIES`: `"A mismatch on a file that was already there"` and `"curl -fL --continue-at - -o <models_dir>"`. Run it: FAIL.

- [ ] **Step 7: Rewrite G1, step 9 and Q2.**
  - onboard-flow.md 4.3: keep its first sentence ("**Gate G1** fetches exactly one file: ..."). Replace its six bullets (:141-149) with:

```markdown
      - `bash $SK/scripts/fetch-gguf.sh --url <url> --dest <models_dir>/<entry file> --size <size_bytes>`, plus
        `--sha256 <pick.sha256>` for a Hugging Face file (an alias `url`: size only, since models.yml stores no checksum; the
        report says so). `<entry file>` is a base name, never a sub-folder path. It downloads into `<entry file>.part`, so a
        re-run resumes an interrupted download, verifies the file, then renames it.
      - The entry's file is already in `<models_dir>`: no G1; the same command only verifies it. A `<entry file>.part` there
        is a resume: G1's facts give its size.
      - Exit 1: report its message, remove the scratch files and stop, as after a declined G1 (step 9). It never touches a
        file it did not download, and it removes its `.part` only on a mismatch.
```

  - Step 9 :239: change "(a download size or sha256 mismatch)" to "(a `fetch-gguf.sh` exit 1)".
  - Step 9 :246: change "Not kept, and G1 put a file into the models directory:" to "Not kept, and G1 left a file or a `.part` in the models directory:".
  - tuning.md Q2 :174: change its first sentence ("It downloads into `<file>.part` ... renames it;") to "It runs `fetch-gguf.sh` (size and the tree's sha256; an interrupted fetch resumes in `<file>.part`);". Keep the rest of the line.

- [ ] **Step 8: Verify.** Run `make validate`, then `bash tmp/r14/pytest-one.sh tests/skills/onboard-local-model/test_onboard_local_model_scripts.py`. Expected: PASS.

### Task 2: The GPU is released when no llama-server holds it

**Files:**
- Modify: `.claude/skills/onboard-local-model/scripts/wait-gpu-release.sh` (all of it)
- Modify: `.claude/skills/onboard-local-model/SKILL.md` (G2 row :70; Freeing the GPU :75-80)
- Modify: `.claude/skills/onboard-local-model/references/scoring.md` (:27-28), `references/served-alias.md` (R3 at :26)
- Modify: `.claude/skills/onboard-local-model/evals/evals.json`: evals `served-alias-argument`, `onboard-plan-with-server-running`, `tune-capacity-card`, `enabled-alias-stopped`, `onboard-baseline-server-stopped`, `gateway-enabled-alias`
- Test: `test_onboard_local_model_scripts.py` (:226, :883-893, the wait tests :1335-1390, :2230, :2295, :2307-2311, :2572-2575, `RETIRED_COPIES`)

**Interfaces:**
- Produces: `wait-gpu-release.sh [--timeout S] [--interval S]`.
  - It prints `{"released": true, "used_mib": X, "waited_s": T}` (exit 0), or `{"released": false, "used_mib": X, "holders": [...]}` (exit 1).
  - `--below-mib` is an unknown flag (exit 2). The `ONBOARD_PREFLIGHT` seam stays.

- [ ] **Step 1: Write the failing tests.** Replace `_fake_preflight` and the five `test_wait_gpu_release_*` tests (:1335-1390) with:

```python
LS, PY = "llama-server", "python3"


def _fake_preflight(tmp_path: Path, *states: tuple[int | None, list[str] | None]) -> Path:
    """One state per run (the last one repeats): used_mib and the comm of each GPU holder; None leaves the holders key out."""
    lines = tmp_path / "states.jsonl"
    lines.write_text("".join(json.dumps({"gpu": {"used_mib": used} | ({} if comms is None else {
        "holders": [{"pid": n, "comm": c, "vram_mib": 100} for n, c in enumerate(comms)]})}) + "\n" for used, comms in states))
    script = tmp_path / "preflight.sh"
    script.write_text(f'#!/usr/bin/env bash\nhead -n1 {lines}\n[ "$(wc -l < {lines})" -le 1 ] || sed -i 1d {lines}\n')
    script.chmod(0o755)
    return script


def _wait(tmp_path: Path, *states: tuple[int | None, list[str] | None], extra: tuple[str, ...] = (),
          timeout: str = "30") -> subprocess.CompletedProcess[str]:
    env = {"PATH": "/usr/bin:/bin", "ONBOARD_PREFLIGHT": str(_fake_preflight(tmp_path, *states))}
    return _run("bash", SCRIPTS / "wait-gpu-release.sh", *extra, "--interval", "0", "--timeout", timeout, env=env)


def test_wait_gpu_release_returns_once_no_llama_server_holds_the_gpu_even_when_system_one_reloads(tmp_path: Path) -> None:
    # llm-server stops while a Laya request reloads System One: used VRAM rises, and the GPU is still released
    result = _wait(tmp_path, (13662, [LS, LS, PY]), (6300, [LS, PY]), (10000, [PY]))
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert out["released"] is True and out["used_mib"] == 10000


def test_wait_gpu_release_times_out_with_the_gpu_holders(tmp_path: Path) -> None:
    result = _wait(tmp_path, (9000, [LS, LS, PY]), timeout="0")
    assert result.returncode == 1
    out = json.loads(result.stdout)
    assert out["released"] is False and [h["comm"] for h in out["holders"]] == [LS, LS, PY]


def test_wait_gpu_release_rejects_unknown_flags_and_the_retired_target(tmp_path: Path) -> None:
    assert _run("bash", SCRIPTS / "wait-gpu-release.sh", "--nope").returncode == 2
    assert _wait(tmp_path, (1200, [PY]), extra=("--below-mib", "2000"), timeout="0").returncode == 2


@pytest.mark.parametrize("args", [["--timeout"], ["--timeout", "-1"], ["--timeout", "x"], ["--interval", "1.5"]])
def test_wait_gpu_release_rejects_missing_or_non_numeric_values(args: list[str]) -> None:
    result = _run("bash", SCRIPTS / "wait-gpu-release.sh", *args)
    assert result.returncode == 2 and result.stderr.strip()


@pytest.mark.parametrize("used", [None, 1200])
def test_wait_gpu_release_treats_a_missing_holder_list_as_unreadable(tmp_path: Path, used: int | None) -> None:
    result = _wait(tmp_path, (used, None), timeout="0")
    assert result.returncode == 1
    assert json.loads(result.stdout) == {"released": False, "used_mib": used, "holders": []}
```

Remove `"wait-gpu-release.sh"` from the `test_missing_required_arguments_exit_two` list: no argument is valid now.

- [ ] **Step 2: Run to verify they fail.** Run `bash tmp/r14/pytest-one.sh tests/skills/onboard-local-model/test_onboard_local_model_scripts.py -k wait_gpu_release`. Expected: the System One case, the retired-target case and both missing-holder cases fail. The old script needs `--below-mib` and accepts it.

- [ ] **Step 3: Rewrite `wait-gpu-release.sh`.** The release test is `jq -e '(.holders | type) == "array" and all(.holders[]; .comm != "llama-server")'` on the state the loop already reads. Keep the timeout, interval, seam and output shapes. The header, verbatim:

```bash
#!/usr/bin/env bash
# wait-gpu-release.sh — poll preflight.sh until no GPU holder is a llama-server process, e.g. after `make stop-llama`.
# usage: wait-gpu-release.sh [--timeout SECONDS (default 60)] [--interval SECONDS (default 5)]
# Prints {"released": true, "used_mib": X, "waited_s": T} and exits 0 once .gpu.holders lists no `llama-server` comm. Used VRAM
# is reported, never compared: System One (a python3 holder) may reload about 3.7 GiB during the wait. After --timeout seconds
# (the GPU is always read at least once) prints {"released": false, "used_mib": X, "holders": [...]} and exits 1. A null or
# missing holders list is unreadable: it never releases.
# ONBOARD_PREFLIGHT overrides the preflight command (test seam). Read-only: it never stops or starts anything.
set -euo pipefail
```

- [ ] **Step 4: Run the wait tests.** Same command as Step 2. Expected: PASS.

- [ ] **Step 5: Update the doc and eval pins first.**
  - Rename `test_freeing_the_gpu_sums_every_llama_server_holder` to `test_freeing_the_gpu_waits_for_every_llama_server_holder_to_leave`, with this body:

```python
    gates = _section(_md(SKILL_MD), "## Gates", "## NEVER")
    assert "a `.gpu.holders[]` entry whose `comm` is `llama-server`" in gates
    assert "`bash $SK/scripts/wait-gpu-release.sh` (it returns once no `llama-server` holder is left)" in gates
    assert "G-T1 to G-T3, G-Q0 to G-Q3, G-R1 and G6, each asked in the file that names it" in gates
    pre = json.loads((FIX / "preflight-served.json").read_text())
    assert [h["vram_mib"] for h in pre["gpu"]["holders"] if h["comm"] == "llama-server"] == [12, 7450]
    assert all("router 12 + model 7450" in _asserts(i) for i in ("served-alias-argument", "tune-capacity-card"))
    evals = (SKILL / "evals" / "evals.json").read_text()
    assert "--below-mib" not in evals and "U - H" not in evals and "H = 0" not in evals
```

  - Other pins in the same file:
    - :2230: `"--below-mib 6712"` becomes `"wait-gpu-release.sh"`.
    - :2295: `"H = 0"` becomes `"no llama-server holds the GPU"`.
    - :2307-2311: drop the `held` line. Assert `budget == 8321` and `"8321" in _asserts("served-alias-argument")`.
    - :2572-2575: drop the arithmetic. Assert `"wait-gpu-release.sh"` and `"G-T1 (make llama-enable) is not asked"` in `_asserts("tune-capacity-card")`.
  - `RETIRED_COPIES` gains `"--below-mib"`, `"U - H"` and `"U and H"`. Run the file: the doc tests FAIL.

- [ ] **Step 6: Edit `SKILL.md`, the references and the evals.**
  - `SKILL.md` G2 row: delete `, the `wait-gpu-release.sh` target`, so the row ends `that System One stays up |`.
  - `SKILL.md` Freeing the GPU: replace the whole paragraph (from `**Freeing the GPU (G2, G-T2).**` up to the blank line before `Route gates follow`) with:

```markdown
**Freeing the GPU (G2, G-T2).** When the last `preflight.sh` read lists a `.gpu.holders[]` entry whose `comm` is
`llama-server`, run `make stop-llama` (llm-server only), then `bash $SK/scripts/wait-gpu-release.sh` (it returns once no
`llama-server` holder is left); on exit 1 report the printed holders and stop. None (gateway-only, or llm-server down):
skip both. System One stays resident, as budgeted: measuring without it flatters the result. While llm-server is stopped
the healer's OmniRoute restart fails (logged) and never reloads it.
```

  - scoring.md :28: "(U and H of `SKILL.md` Freeing the GPU come from that read)" becomes "(`SKILL.md` Freeing the GPU reads its holders from that read)".
  - served-alias.md R3: "with U and H from the last R1 preflight" becomes "on the holders of the last R1 preflight".
  - evals.json (only these strings change):
    - `served-alias-argument` expected_output: "G2 frees the GPU down to 6712 MiB with make stop-llama" becomes "G2 stops llm-server with make stop-llama and waits until no llama-server holds the GPU".
    - `served-alias-argument` assertion: "G2 runs make stop-llama, then wait-gpu-release.sh, which returns once neither llama-server holder (router 12 + model 7450) is left; it never uses make stop, make restart or an omniroute restart, and System One stays up".
    - `onboard-plan-with-server-running` assertion: delete ` --below-mib 6712` (the rest stays).
    - `tune-capacity-card` assertion: "G-T2 frees the GPU with make stop-llama and wait-gpu-release.sh (it returns once neither llama-server holder, router 12 + model 7450, is left) while System One stays up".
    - `enabled-alias-stopped` and `onboard-baseline-server-stopped` assertion: "G2 follows the preflight read after make start-llama, which lists the llama-server holders; the stopped JSON has none and would skip make stop-llama".
    - `onboard-baseline-server-stopped` expected_output: "with the G2 target from that second read" becomes "and G2 reads the llama-server holders from that second read".
    - `gateway-enabled-alias` assertion: "There is no baseline (gateway-only host): no baseline lane or bench, and no make stop-llama or wait-gpu-release.sh, because no llama-server holds the GPU".

- [ ] **Step 7: Verify.** Run `make validate`, then the scripts test file. Then `wc -c < .claude/skills/onboard-local-model/SKILL.md`. Expected: PASS, and 10891 bytes.

### Task 3: A scout-gated row never ranks

**Files:**
- Modify: `.claude/skills/onboard-local-model/scripts/rank-candidates.sh` (header :9-15; jq :75-84)
- Test: `test_onboard_local_model_scripts.py` (after `test_rank_candidates_scores_evidence_and_flags_jev_disagreements`)

**Interfaces:**
- Produces: `--evidence` output `excluded` entries `{"id", "reason": "scout gate: <gate>"}` for a card whose row is in the scout `gates_failed`. They come in card order, mixed with the existing `gate false: ...` entries. Such a card gets no `ranked` and no `disagreements` entry.

- [ ] **Step 1: Write the failing test**

```python
def test_rank_candidates_never_ranks_a_card_for_a_scout_gated_row(tmp_path: Path) -> None:
    rows = [_cand("a/ok"), _cand("b/lowbit", fit="vram-lowbit", bpw=2.1, mib=3000)]
    jev = {"speed": {"level": None, "needs_fallback": True}}
    cards = _cards(tmp_path, _card("a/ok"), _card("b/lowbit", jev=jev))
    out = json.loads(_rank(tmp_path, [rows], "--evidence", cards).stdout)
    assert [r["id"] for r in out["ranked"]] == ["a/ok"]
    assert out["excluded"] == [{"id": "b/lowbit", "reason": "scout gate: fit"}]
    assert out["disagreements"] == []
```

- [ ] **Step 2: Run to verify it fails.** `-k never_ranks_a_card_for_a_scout_gated_row`. Expected: FAIL; `ranked` is `["b/lowbit", "a/ok"]`.

- [ ] **Step 3: Implement.** In `$cs`, add `sg: ([$scout.failed[] | select(.id == $c.id) | .gate] | first)`. `ranked` and `disagreements` take only `.off == [] and .sg == null`. `excluded` takes `.sg != null or .off != []`, with reason `scout gate: \(.sg)` when `sg` is set, else the existing `gate false: ...`. The card id check against `$scout.rows` (:65) stays. Header :11 becomes `excluded: [{id, reason}] (a card whose scout row failed a gate: "scout gate: <gate>"; a false card gate: "gate false: <gates>")`.

- [ ] **Step 4: Verify.** Run `make validate`, then `-k rank_candidates`. Expected: PASS (all rank tests).

### Task 4: `new-alias.sh` computes A1-A3

**Files:**
- Create: `.claude/skills/onboard-local-model/scripts/new-alias.sh`
- Modify: `.claude/skills/onboard-local-model/references/onboard-flow.md` (Alias for a new entry, :18-29)
- Modify: `.claude/skills/onboard-local-model/references/local-gguf.md` (L1, second bullet, :27-28)
- Test: `test_onboard_local_model_scripts.py` (`SCRIPT_NAMES`; the missing-arguments list; new tests after `test_onboard_flow_names_alias_items_a1_to_a3`; that test; `RETIRED_COPIES`)

**Interfaces:**
- Consumes: the live config through `tools/lib.sh` (`CONFIG_PATH`, from `LLM_ENV_CONFIG`), sourced as `resolve-target.sh` does.
- Produces: `new-alias.sh --file NAME --size BYTES [--quant TOKEN]`.
  - Exit 0 prints `{"alias": A, "rule": "A1"|"A2"|"A3"}`.
  - Exit 1: the same file with another `size_bytes` (stderr names both sizes).
  - Exit 2: usage.

- [ ] **Step 1: Write the failing tests**

```python
def _alias(tmp_path: Path, *args: str, models: list[dict] | None = None) -> subprocess.CompletedProcess[str]:
    cfg = tmp_path / "models.yml"
    cfg.write_text(yaml.safe_dump({"models": models or []}))
    return _run("bash", SCRIPTS / "new-alias.sh", *args, env={**os.environ, "LLM_ENV_CONFIG": str(cfg)})


def test_new_alias_names_the_eval_candidates_from_the_fixture_config() -> None:
    def alias(*args: str) -> dict:
        result = _run("bash", SCRIPTS / "new-alias.sh", *args, env={**os.environ, **FIX_ENV})
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)
    assert alias("--file", "Qwen3.8-Coder-9B-Q5_K_M.gguf", "--size", "6710886400", "--quant", "Q5_K_M") == {"alias": "qwen3-8-coder-9b", "rule": "A2"}
    assert alias("--file", "sub/gemma4-v2-Q5_K_M.gguf", "--size", "1", "--quant", "Q5_K_M") == {"alias": "gemma4-q5_k_m", "rule": "A2"}
    assert alias("--file", "gemma4-v2-Q4_K_M.gguf", "--size", "7381381664", "--quant", "Q4_K_M") == {"alias": "gemma4", "rule": "A1"}
    assert alias("--file", "Foo-9B-Q5_K_M.gguf", "--size", "1", "--quant", "Q5_K_M") == {"alias": "foo-9b", "rule": "A2"}


def test_new_alias_stops_on_the_same_file_name_with_another_size() -> None:
    result = _run("bash", SCRIPTS / "new-alias.sh", "--file", "gemma4-v2-Q4_K_M.gguf", "--size", "5", "--quant", "Q4_K_M",
                  env={**os.environ, **FIX_ENV})
    assert (result.returncode, result.stdout) == (1, "") and "7381381664" in result.stderr and "gemma4" in result.stderr


def test_new_alias_appends_a_counter_for_a_taken_name_and_works_without_a_quant(tmp_path: Path) -> None:
    taken = [{"alias": "mymodel", "file": "other.gguf", "quantization": "Q8_0", "size_bytes": 1},
             {"alias": "mymodel-2", "file": "other2.gguf", "quantization": "Q8_0", "size_bytes": 1}]
    assert json.loads(_alias(tmp_path, "--file", "/home/x/MyModel.gguf", "--size", "9", models=taken).stdout) == {"alias": "mymodel-3", "rule": "A3"}
    assert json.loads(_alias(tmp_path, "--file", "MyModel.gguf", "--size", "9").stdout) == {"alias": "mymodel", "rule": "A2"}


def test_new_alias_caps_the_name_at_60_characters(tmp_path: Path) -> None:
    out = json.loads(_alias(tmp_path, "--file", "Ab_" * 30 + "Q4_K_M.gguf", "--size", "1", "--quant", "Q4_K_M").stdout)
    assert out == {"alias": ("ab-" * 20)[:-1], "rule": "A2"}  # 59 characters: the cut at 60 ends on a '-'


@pytest.mark.parametrize("args", [["--file", "m.gguf", "--size", "x"], ["--file", "m.gguf", "--size", "1", "--quant", "Q4 K"], ["--size", "1"]])
def test_new_alias_rejects_bad_arguments(tmp_path: Path, args: list[str]) -> None:
    assert _alias(tmp_path, *args).returncode == 2
```

Add `"new-alias"` to `SCRIPT_NAMES` and `"new-alias.sh"` to the missing-arguments list.

- [ ] **Step 2: Run to verify they fail.** `-k new_alias`. Expected: FAIL (no such script).

- [ ] **Step 3: Implement `new-alias.sh`.** It is one jq program over `yq -o=json '.models // []'`. The header is the spec, verbatim:

```bash
#!/usr/bin/env bash
# new-alias.sh — the models.yml alias a candidate file gets (onboarding's Alias for a new entry, A1-A3), read from the live config.
# usage: new-alias.sh --file NAME --size BYTES [--quant TOKEN]
# NAME is the candidate's file (its base name is used), BYTES its exact size, TOKEN its quant token (resolve-target.sh's quant or
# hf-scout.sh's pick.quant; leave it out when null). Prints {"alias": A, "rule": "A1"|"A2"|"A3"}.
# A1: a live entry has this file and size_bytes BYTES: its alias. The same file with another size_bytes exits 1 naming that alias
#     and both sizes (a different file under that name).
# A2: NAME without .gguf and without TOKEN (case-insensitive), lowercased, every run outside [a-z0-9] turned into -, leading and
#     trailing - dropped, cut to 60 characters (a trailing - dropped again). When a live entry's file gives the same name with that
#     entry's own quantization as the token, the alias is <that alias>-<TOKEN lowercase> instead (quant-headroom.sh add-entry's name).
# A3: while a live alias already has the A2 name, -2, -3... is appended.
# Env: LLM_ENV_CONFIG. Exit 2 on usage, a non-integer BYTES, a TOKEN outside [A-Za-z0-9_-], a missing config or an empty name.
set -euo pipefail
```

- [ ] **Step 4: Run the tests.** `-k new_alias`. Expected: PASS.

- [ ] **Step 5: Write the failing doc pin.**
  - In `test_onboard_flow_names_alias_items_a1_to_a3`, add `assert "`bash $SK/scripts/new-alias.sh --file <entry file> --size <bytes> --quant <quant>`" in " ".join(new_entry.split())`.
  - Add to the local-gguf test: `assert "`new-alias.sh` (A1, with this file's byte size) decides now" in l1`.
  - `RETIRED_COPIES` gains `"each run of characters outside"`.
  - Run: FAIL.

- [ ] **Step 6: Rewrite the section.**
  - onboard-flow.md: replace the body of `### Alias for a new entry` (its intro line and A1-A3) with:

```markdown
Decided at step 3 (a local GGUF: L5, run order 7) and used unchanged in `$S`, the lanes and 7a:
`bash $SK/scripts/new-alias.sh --file <entry file> --size <bytes> --quant <quant>` reads the live models.yml and prints
`{alias, rule}`. `<bytes>` is `pick.bytes` (a local GGUF: `size_bytes` of `resolve-target.sh`); `<quant>` is `pick.quant` or
the `quant` of `resolve-target.sh` (leave `--quant` out when it is null).
- A1. A live entry has that `file` and `size_bytes`: its alias. One GGUF never gets a second entry. Exit 1, the same `file`
  with another size (a different file under that name; the models directory holds one file per name): report both sizes it
  prints and stop before G1.
- A2. Else a name made from the file name (its quant token dropped; the script header states the rule), or
  `<that alias>-<quant lowercase>` when a live entry's file gives the same name: the name `quant-headroom.sh add-entry` gives
  (`gemma4-q5_k_m`).
- A3. A2's name is already a live alias: `-2`, `-3`... appended. A name is never reused for a different file.
```

  - local-gguf.md L1, second bullet: "A1's size check (its `size_bytes` against this file's byte size) decides now, before any copy." becomes "`new-alias.sh` (A1, with this file's byte size) decides now, before any copy."

- [ ] **Step 7: Verify.** Run `make validate`, then the scripts test file. Expected: PASS.

### Task 5: Gateway-only hosts get a stop with a next step

**Files:**
- Modify: `.claude/skills/onboard-local-model/references/scoring.md` (Serving the lane's model, :25-37)
- Modify: `.claude/skills/onboard-local-model/references/anchors.md` (:46-47), `references/challenges.md` (:126-128), `references/selection-criteria.md` (Scout and rank step 1, :21-23)
- Test: `test_onboard_local_model_scripts.py` (`test_one_rule_serves_a_stopped_llm_server_and_each_route_hands_it_back`, `test_onboarding_and_tuning_branch_on_a_host_without_gpu_config`)

- [ ] **Step 1: Write the failing doc tests**
  - In `test_one_rule_serves_...`, after `rule = ...`, add:

```python
    assert all(s in rule for s in ("`gpu.pci_address is empty`", "`services.llm_server_enabled` false", "no local lane",
                                   "before any lane or G4", "`make llama-enable MODEL=<alias>`"))
    for name in ("anchors", "challenges"):
        assert "enabled but stopped: serve it per" not in _md(REFS / f"{name}.md"), name
```

  - In `test_onboarding_and_tuning_branch_on_a_host_without_gpu_config`, add:

```python
    scout = _section(_md(REFS / "selection-criteria.md"), "## Scout and rank", "## 1.")
    assert "`gpu.pci_address is empty`" in scout and "report its message" in scout
```

- [ ] **Step 2: Run to verify they fail.** `-k "one_rule_serves or without_gpu_config"`. Expected: FAIL.

- [ ] **Step 3: Edit the four references.**
  - scoring.md, Serving the lane's model: insert as the first bullet:

```markdown
- Preflight exits 2 with `gpu.pci_address is empty`, or says `services.llm_server_enabled` false (a gateway-only host):
  there is no served model, so no local lane. A route whose lane is the served model (the anchor comparison, the
  calibration) reports that nothing local was measured, names AGENTS.md's switch to local inference as the owner's call
  (`make llama-enable MODEL=<alias>`, then `LLM_ENV_ASSUME_YES=1 make setup`), and stops before any lane or G4.
```

  - anchors.md :46-47: "... `bash $SK/scripts/preflight.sh` first; enabled but stopped: serve it per `references/scoring.md`, Serving the lane's model, and `make stop-llama` after the last local lane." becomes "... `bash $SK/scripts/preflight.sh` first, then `references/scoring.md`, Serving the lane's model (a gateway-only host stops there), and `make stop-llama` after the last local lane when that rule started it."
  - challenges.md :126-128: the same change, ending "and `make stop-llama` after that lane when that rule started it."
  - selection-criteria.md, Scout and rank step 1: append "Preflight exits 2 (`gpu.pci_address is empty`: a gateway-only `make setup` skipped GPU detection): no budget can be sized; report its message, which names the switch, and stop."

- [ ] **Step 4: Verify.** Run the scripts test file. Expected: PASS. `test_references_cite_each_other_only_by_path_and_only_on_their_route` stays green: no new reference is named.

### Task 6: One explicit order for a host without `gpu.device_name`; the first step-1 read

**Files:**
- Modify: `.claude/skills/onboard-local-model/references/onboard-flow.md` (checklist line :94; a new paragraph between the checklist and step 1; step 1 bullet :110-113; step 9 :243 and :245)
- Modify: `.claude/skills/onboard-local-model/references/local-gguf.md` (run order items 8-9 :19-20; one line after item 10)
- Test: `test_onboard_local_model_scripts.py` (`test_onboarding_and_tuning_branch_on_a_host_without_gpu_config`, `test_local_gguf_reference_owns_the_name_check_copy_and_live_entry`, `test_one_rule_serves_...`, `RETIRED_COPIES`)

- [ ] **Step 1: Write the failing doc tests.**
  - In `test_onboarding_and_tuning_branch_...`, the `step1` list loses `"before step 6"` and the yq command and gains `"the No-device order"`. Then add:

```python
    order = _section(flow, "**No-device order**", "1. **Preflight (read-only).**")
    marks = [order.find(m) for m in ("steps 1-4", "7a.1-7a.4 (Gate G3)", "L=~/.config/llm-env/models.yml yq -i '.gpu = load(strenv(L)).gpu' $S",
                                      "6.2-6.5", "7a.5-7a.7", "7b, 7c, 8, 9")]
    assert -1 not in marks and marks == sorted(marks), marks
```

  - In the local-gguf test, add `assert "the No-device order of `references/onboard-flow.md` from 7a.1 on" in local`.
  - In `test_one_rule_serves_...`, the two step 9 pins become `"Hand-back: the first step-1 read found llm-server stopped and it runs now (a yes to G5 included): `make stop-llama`"` and `"After that bullet (a restore or nothing to restore), when the first step-1 read found llm-server serving: `make start-llama`"`.
  - `RETIRED_COPIES` gains `"7a resumes at 7a.5"`, `"from 7a.5 when item 8"` and `"when step 1 moved Gate G3"`.

- [ ] **Step 2: Run to verify they fail.** Expected: FAIL.

- [ ] **Step 3: Edit onboard-flow.md.**
  - Checklist :94: `- 6 (b) baseline screen, then candidate screen (no `gpu.device_name`: the No-device order below)`.
  - After the checklist's last line (`- 9 G5, ...`), add a blank line and this paragraph. It must not start with `- <digit> `, because `test_routes_have_progress_checklists` counts those.

```markdown
**No-device order** (step 1 found `gpu.device_name` empty; such a host has no baseline, so step 5 runs no lane and G2 stops
nothing): steps 1-4, then 7a.1-7a.4 (Gate G3), then `L=~/.config/llm-env/models.yml yq -i '.gpu = load(strenv(L)).gpu' $S`
(it gives `$S` the device `make setup` just found), then 6.2-6.5, then 7a.5-7a.7, then 7b, 7c, 8, 9.
```

  - Step 1, the `gpu.device_name` bullet (:110-113): after "...but no Vulkan device):", the text becomes "`bench-fit.sh` cannot screen until G3's `make setup`. Follow the No-device order above. Step 2 still finds no baseline. A no to its G3 ends the run as a declined G3 does (step 9)."
  - Step 9 :243 and :245: "when step 1 found llm-server serving" becomes "when the first step-1 read found llm-server serving". "Hand-back: step 1 found llm-server stopped" becomes "Hand-back: the first step-1 read found llm-server stopped".

- [ ] **Step 4: Edit local-gguf.md.** Item 8 becomes `8. Step 6 (screens `candidate` on `$S`)` and item 9 becomes `9. Steps 7a to 8 (L5's entry is the one 7a.2 writes)`. After item 10 add: "No `gpu.device_name` (step 1): items 1-7, then the No-device order of `references/onboard-flow.md` from 7a.1 on (L5's entry is the one 7a.2 writes; step 6 still screens `candidate` on `$S`), then item 10."

- [ ] **Step 5: Verify.** Run the scripts test file. Expected: PASS, `test_routes_have_progress_checklists` included.

### Task 7: Small script fixes (S8, S9)

**Files:**
- Modify: `.claude/skills/onboard-local-model/scripts/quant-headroom.sh` (header :9-12; `cmd_check` after the `rows=` loop, :149-156)
- Modify: `.claude/skills/onboard-local-model/scripts/resolve-target.sh` (header :4-5; after the `file://` line :45)
- Test: `test_onboard_local_model_scripts.py` (`test_quant_headroom_check_takes_the_verdict_...` :1701, `test_resolve_target_parses_hugging_face_urls` :1958, one new test)

- [ ] **Step 1: Write the failing tests**
  - In `test_quant_headroom_check_takes_the_verdict_...`, after the `entry = ...` assertion, add `assert not list((tmp_path / "out" / "model-tune").glob("quant-*/budget-*/models")), "the sparse stand-ins are scratch"`.
  - In `test_resolve_target_parses_hugging_face_urls`, add `assert _rt_json(tmp_path, "huggingface.co/o/r") == {"kind": "hf_repo", "repo": "o/r"}` and `assert _rt_json(tmp_path, "HF.co/o/r/resolve/main/sub/m-Q5_K_M.gguf?download=true") == want`.
  - New guard (Review Focus 4; it passes before and after, and pins the prefix's condition):

```python
def test_resolve_target_keeps_an_existing_path_that_looks_like_a_scheme_less_url(tmp_path: Path) -> None:
    (tmp_path / "hf.co").mkdir()
    (tmp_path / "hf.co" / "x-Q4_K_M.gguf").write_bytes(GGUF_BYTES)
    assert _rt_json(tmp_path, "hf.co/x-Q4_K_M.gguf")["kind"] == "gguf_file"
```

- [ ] **Step 2: Run to verify they fail.** `-k "takes_the_verdict or parses_hugging_face or scheme_less"`. Expected: the first two FAIL; the guard passes.

- [ ] **Step 3: Implement.**
  - quant-headroom.sh: right after the `rows="$(...)"` loop, add `rm -rf "$run"/budget-*/models`. rm does not follow the symlink `budget_for` makes for a downloaded file. Header :10-12: after "...the tree's size)" add "(removed once the budgets are read; budget-<q>/models.yml stays)".
  - resolve-target.sh: after the `file://` line, prefix `https://` when the argument is not an existing path and starts, case-insensitively, with `huggingface.co/` or `hf.co/`. Header :4: after "(2) an http(s) Hugging Face URL" add "(a scheme-less huggingface.co/... or hf.co/... argument that is not an existing path counts as https://)".

- [ ] **Step 4: Verify.** Run `make validate`, then the scripts test file. Expected: PASS.

### Task 8: Prose corrections and deletions (S4, E1, E4)

**Files:**
- Modify: `.claude/skills/onboard-local-model/scripts/bench-fit.sh` (header :6-8)
- Modify: `.claude/skills/onboard-local-model/references/onboard-flow.md` (step 6 :171-172; 4.4 bullet :158-159; Where things live :275-285)
- Test: `test_onboard_local_model_scripts.py` (`RETIRED_COPIES`; the assertion at :980)

- [ ] **Step 1: Write the failing pins.**
  - `RETIRED_COPIES` gains `"screens with the same flag presets"`, `"`budget` reports 0 MiB of RAM for llm-server"` and `"`make clean` keeps them; `make prune` deletes them"`.
  - At :980, drop `and "only when \`gpu.device_name\` is set" in flow`, since that bullet is deleted. Keep the "still works in a throwaway container" check.
  - Run: FAIL.

- [ ] **Step 2: Edit.**
  - bench-fit.sh :6-8: `# Flags (gpu layers, n-cpu-moe, KV types, ubatch, flash attention) come from the rendered production presets, so numbers match production (flash attention follows the preset; --threads adds -t N). `make benchmark` renders the same presets but passes only gpu layers and n-cpu-moe, measures only enabled entries and has no depth sweep.` (wrapped at the file's width).
  - onboard-flow.md step 6 first sentence: "**Screen throughput.** `bench-fit.sh` screens with the production presets (KV types, ubatch and flash attention included, which `make benchmark` leaves out), plus the depth sweep and load sampling `make benchmark` lacks; `make benchmark` also measures only enabled models."
  - onboard-flow.md 4.4: delete the bullet "`llm_server.enabled: false` in the config: `budget` reports 0 MiB ... not a verdict on the model." (E1: `resolve-target.sh` sets it true on every `$S`).
  - onboard-flow.md `## Where things live`: keep only the Capacity card bullet (E4).

- [ ] **Step 3: Verify.** Run `make validate`, then the scripts test file. Expected: PASS.

### Task 9: The one-test-file limit, documented and guarded

**Files:**
- Modify: `.claude/skills/onboard-local-model/scripts/effort-run.sh` (the `shortcut:` comment, :215-216)
- Test: `tests/skills/onboard-local-model/test_challenges.py` (one new test)

`challenges/` is not touched, so the generator id stays (owner decision 1).

- [ ] **Step 1: Write the guard test.** It passes now: it pins today's set.

```python
def test_only_known_library_test_files_ship_a_single_test() -> None:
    # effort-run.sh's one-test shortcut: a new one-test file widens it; give it a second test in the same library edit
    files = [*CHALLENGES.glob("projects/*/test/*.test.js"), *CHALLENGES.glob("decoys/*/test/*.test.js")]
    single = sorted(str(p.relative_to(CHALLENGES)) for p in files if len(re.findall(r"^\s*test\(", p.read_text(), re.MULTILINE)) < 2)
    assert single == ["projects/log-parser/test/report.test.js"]
```

- [ ] **Step 2: Rewrite the comment** (two lines, inside the jq program as now): `# shortcut: tests_total counts a shipped test file that fails to load as one test, so a file with exactly one test can still be cheated (exit 0 only in that file's process = 1 pass). Only challenges/projects/log-parser/test/report.test.js ships one test (test_only_known_library_test_files_ship_a_single_test); give it a second at the next library edit, which changes the generator id anyway.`

- [ ] **Step 3: Verify.** Run `make validate`, then `bash tmp/r14/pytest-one.sh tests/skills/onboard-local-model/test_challenges.py`. Expected: PASS. `bash .claude/skills/onboard-local-model/scripts/gen-challenge.sh --generator-id` prints the id recorded in Task 0 Step 2.

### Task 10: Verification and cleanup

- [ ] **Step 1: Byte cap.** `wc -c < .claude/skills/onboard-local-model/SKILL.md`. Expected: 10891 (<= 11000).
- [ ] **Step 2: Guard and shared-rule tests.** `bash tmp/r14/pytest-one.sh tests/skills/onboard-local-model/test_onboard_local_model_scripts.py -k "jev_as_a_black_box or stated_once or cite_each_other or router_that_loads or nickname"`. Expected: PASS.
- [ ] **Step 3: Skill suites, one at a time.** Run the three files of Task 0 Step 2, in turn. Expected: all pass. The count is the baseline plus this plan's new tests.
- [ ] **Step 4: Repo checks.** With `TMPDIR` unset, run `make validate && make test`. Expected: "All checks passed." and a green suite.
- [ ] **Step 5: Route walk** (read as a new agent who has only `SKILL.md`; nothing runs against the host). For each route, follow the route table row, load only the files it names, and confirm every branch ends in a step, a gate or a stop:
  1. Hugging Face file on a served host, with an interrupted earlier G1 (`<entry file>.part` in the models dir). Expected path: step 1, H1-H5 (`new-alias.sh`), 4.3 (`fetch-gguf.sh` resumes; G1 states the `.part` size), 4.4, 5, G2 (Freeing the GPU, no arithmetic), 6, 7, 8, 9 (the file-or-`.part` report).
  2. Gateway-only host without GPU config: onboarding stops at step 1. The anchor comparison and the calibration stop at scoring.md's first bullet, before any lane or G4. The no-argument scout stops at its step 1. Each names the switch.
  3. Gateway-only host with System One on (empty `gpu.device_name`): the No-device order, then the same for a local GGUF through its run order line.
  4. The served-alias re-measure: R1, R3 (holders of the last R1 read), R6.
  5. The no-argument scout where a research card exists for a 2.1-bpw row: step 6 lists it under `excluded` with `scout gate: fit`.
  6. The quant comparison Q2: `quant-headroom.sh fetch` through `fetch-gguf.sh`.
  7. Enabled-but-stopped onboarding: step 9 hands back on "the first step-1 read".
  Report any branch without a next step as a finding; do not fix it in this plan.
- [ ] **Step 6: Cleanup.** `rm -rf tmp/r14`. Check that `ls /var/tmp | grep pytest-r14` prints nothing and that `podman ps -a` shows nothing this plan started (it starts nothing). `git status --short` shows no tracked change besides `docs/superpowers/plans/README.md` (its new row); this plan file stays untracked like plans G-O. Judges are re-run by the orchestrator, not by this plan.

## Round-1 review outcome (approved after fixes; 4 confirmed high, 4 medium)

Review round 1 (native + referee-two, lead-verified against the code; tmp/plan-p-review/review-round-1.md) left four CONFIRMED
highs and four mediums; the referee's other critical and high findings were refuted. Plan status: approved for execution with
these amendments. The implementer must apply:

- H1 (Task 1 Step 6): the G1 rewrite removes "never delete or overwrite it", which
  `test_onboard_step_4_writes_the_settings_into_the_scratch_candidate` still pins. Change that pin to
  `"never touches a file it did not download"`.
- H2 (Task 2 Step 6): the new `enabled-alias-stopped` / `onboard-baseline-server-stopped` assertion drops "no llama-server
  holder", which `test_eval_enabled_alias_stopped_fixture_matches_its_assertions` pins (not in the Test list). Write "G2 follows
  the preflight read after make start-llama, which lists the llama-server holders; the stopped JSON has no llama-server holder
  and would skip make stop-llama".
- H3 (Task 4 Step 5): the new A1 text breaks `test_alias_reuse_needs_the_same_size` (not in the Test list). Update its three
  phrases in Step 5, e.g. to "that `file` and `size_bytes`: its alias" and "report both sizes it prints and stop before G1".
- H4 (Task 9 Step 2): "that file's process" ends the single-quoted jq program in `effort-run.sh` (`bash -n` fails). Write
  "the process of that file".
- M1 (Task 2 Step 5): dropping the arithmetic at :2572-2575 leaves `pre` unused (ruff F841). Drop the `pre` line too.
- M2 (Task 0, Global Constraints): `TMPDIR` goes in a second `mktemp -d /var/tmp/...` dir, never under `./tmp/` (AGENTS.md).
- M3 (Task 5 Step 3): the switch to local inference ends with AGENTS.md's final `make restart`:
  `make llama-enable MODEL=<alias>`, then `LLM_ENV_ASSUME_YES=1 make setup`, then `make restart`.
- M4 (Task 5 Step 3): "there is no served model, so no local lane" is too broad (onboarding cites the same rule and still runs
  a candidate lane after G3). Narrow it to the served-model routes: "there is no served model, so the anchor comparison and the
  calibration have no local lane".
