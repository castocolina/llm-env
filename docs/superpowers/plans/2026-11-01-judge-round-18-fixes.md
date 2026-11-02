# Judge Round 18 Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the real defects from round 18 of the onboard-local-model native judge: the three audit Must-fix items (the first is also the judge's high), plus four cheap mediums. Nothing else.

**Architecture:** Six narrow changes, all in the untracked skill tree and its tests:
- **Download failures (D8-1 high = M1, with D8-4/S2).**
  - `fetch-gguf.sh` gets two new exit codes: 3 for a retriable failure (4 attempts used up) and 4 for HTTP 401/403. `quant-headroom.sh fetch` passes both on.
  - Onboarding step 4.3 re-runs an exit 3 detached, at most 3 times. On an exit 4 it asks for `HF_TOKEN` and re-runs only 4.3. The quant route's Q2 and the local GGUF's L4 handle the same codes.
  - `quant-headroom.sh` sends `HF_TOKEN` the way `hf-scout.sh` does. Both scripts name `HF_TOKEN` when a repo cannot be read.
  - Two minimal evals cover the new paths (D7-1).
- **Ladder contamination (M2).** `effort-run.sh` sets each finished run directory to mode 000 until it exits, so a later run of the same battery cannot read an earlier run's solution. An agent that changes such a mode back gets its row flagged `leak_suspect`.
- **Bypass and the image (M3 = D6-1).** With permissions bypassed, G3 counts as a no. Onboarding stops at G3 with the screen numbers, before `make setup` can pull an image nobody measured.
- **Routing sentence (D8-2/S3).** `SKILL.md:37` now reads only one way, and its test pin changes with it.
- **Standalone quant comparison (D8-3/S4).** It says which T steps run (S0).
- **Branch revision (S1).** `quant-headroom.sh` resolves a branch or tag url at that branch, not at main's commit.

`SKILL.md` goes 10,993 -> 10,973 (Task 4) -> 10,983 bytes (Task 5), cap 11,000. These figures come from a scratch copy with exactly those edits. Two shortenings pay for the additions.

**Tech Stack:** bash, curl, jq, yq; Python pytest. Every new network test uses a PATH stub for curl. Rubric: skill-judge v4.

**Spec:**
- `tmp/judges/r18-native-summary.txt` (87.2 % B): judge findings D8-1, D8-2, D8-3, D8-4, D7-1 and D6-1; audit Must-fix M1-M3; Should-fix S1-S4.
- The referee lane is quota-blocked, so it gives no input.
- Base state: the skill tree as the r18 judge read it. `SKILL.md` is 10,993 bytes. The suites pass (409 tests).

## Global Constraints

- English only. Edit only these:
  - `.claude/skills/onboard-local-model/**`;
  - `tests/skills/onboard-local-model/**`;
  - this plan's row in `docs/superpowers/plans/README.md` (already added; earlier rows stay as they are).
- Scope is closed. Touch nothing on the "Not planned" list: no restructuring, no refactors, no prose-wide edits, no new gates.
- No tracked code changes, so there is nothing to commit. The skill tree and `tests/skills` stay untracked.
  - If a step turns out to need a tracked file (`tools/lib.sh`, `llmenv.py`, `pylib/`, `scripts/`, `setup/`), stop and ask.
  - An approved tracked change goes in its own small commit after `make validate && make test`, with no attribution lines.
- `SKILL.md` must stay <= 11,000 bytes (`SKILL_MD_MAX_BYTES`, tested).
  - Never raise the cap. Shorten before you add, and move text into references.
  - Tasks 4 and 5 are the only tasks that edit `SKILL.md`. Run them in that order: Task 5 alone would measure 11,012.
- `jev-fast-decision` stays a black box, and ai-kit is never named. `test_skill_and_touched_docs_treat_jev_as_a_black_box` also scans `scripts/*.sh`.
- Prose holds no shell logic: a backtick span holds at most two of `;`, `&&`, `||`.
- Shared rules are stated once (`STATED_ONCE`), and retired wording stays out (`RETIRED_COPIES`).
- A reference names another only by path, and only for the pairs in `CITES`. This plan adds no pair: `local-gguf.md` -> `onboard-flow.md` is already allowed, and `tuning.md` may cite only `scoring.md`.
- Before you replace a sentence, grep the tests and `evals/evals.json` for it. A pinned phrase changes together with its pin.
- Every script keeps `--help` (exit 0) and its tests. Behaviour changes are red-first: write the test, watch it fail, then implement. Doc pins are also written first, and fail until the text lands.
- After a `.sh` edit, run `make validate`. After a test-file edit, run `bash tmp/r18/make-check.sh` (Task 0). It runs `make validate`, then `make test` with `TMPDIR` in a fresh `/var/tmp` dir.
- pytest:
  - One run at a time, never two at once. `make test` counts as a run.
  - Single files go through `tmp/r18/pytest-one.sh`. It puts `--basetemp` and `TMPDIR` in fresh `mktemp -d /var/tmp/...` dirs, never under `./tmp/`, and deletes both afterwards.
  - After every text edit, run the whole `test_onboard_local_model_scripts.py`: many of its tests pin prose.
- Scratch goes under `./tmp/r18/`; Task 6 removes it. Shell logic belongs in script files.
- Not allowed: containers, GPU, network, downloads, and any edit to the live `models.yml`, `~/.pi` or a capacity card.
- Evals:
  - `prompt` is the user's message alone (`EVAL_SETUP` must not match it).
  - `setup` is present exactly when `files` is.
  - Name reference files by path (`references/onboard-flow.md`), because `BARE_REF` scans `evals.json`.
- Line numbers are as of the r18 judge read. Find text by the quoted anchor, not by the number.

## Findings ledger (verified 2026-11-01 against the files; none dropped)

Planning used read-only checks only. Its scratch went under `tmp/r18-replan/` and is removed.

| Id | Verdict | Evidence |
|---|---|---|
| D8-1 / M1 (high) | real | `onboard-flow.md:154` says "Exit 1: report its message, remove the scratch files and stop". `fetch-gguf.sh:88` exits 1 for "download failed after 4 attempts ... re-run the same command to resume", and `:83` exits 1 for a 401/403. Tests pin both as 1 (`:2274`, `:2293`). `tuning.md:176` and `local-gguf.md:48` treat any exit 1 as final too. |
| D8-4 / S2 | real | `quant-headroom.sh:45-47` `hf()` sends no token. In `resolve_tree` (`:68`), a failed `model="$(hf ...)"` exits with curl's raw code under `set -e`, with no message. The raw code (6, 7, 22, 28...) can collide with new fetch codes. `hf-scout.sh:80` says "(no such repo or revision?)". |
| M2 | real | `effort-run.sh:183-185` puts every run under `$out/<task>-<effort>`, side by side, and its header (`:30`) admits that sibling reads go undetected. The bounded runner starts pi as the owner in a systemd scope (`pylib/agent_runner.py`). So the high run, working in `<task>-high/work`, can `cat ../../<task>-low/work/src/...`. No needle matches that, and no test covers it. |
| M3 / D6-1 | real | `SKILL.md:88-89`: under bypass, gates proceed and G5 counts as a no. `onboard-flow.md:203-205`: 7a.4 `make setup` pulls the floating Vulkan tag. Step 9 (`:253-254`) restores only `models.yml`. `onboard-flow.md:242-243` already admits that the next `make restart` serves an unmeasured build. |
| D8-2 / S3 | real | `SKILL.md:37` says "the model argument wins (a tune or quant request on the enabled alias takes that row)", and "that row" reads both ways. `test_onboard_local_model_scripts.py:1195` pins it word for word. |
| D8-3 / S4 | real | The `tuning.md:129-131` checklist starts at P0. `:158` "T2 runs only when ..." sits after P3. The Hand-back (`:209`) cites "when T1 or P0 found llm-server stopped". Nothing says whether T1 or T2 runs before P0. |
| D7-1 | real | `evals.json` (41 evals) has no eval for an exit after 4 attempts or for a 401. `anchor-401-retry` is about anchor lanes. |
| S1 | real | `quant-headroom.sh:68-73` reads `/api/models/<repo>` (main's head) for any revision that is not a sha. The size guard at `:80` passes whenever the entry's file has the same size on main. |

## Not planned (out of scope, with the reason)

- Judge D7-2 (prose-pin cleanup), D2-1 (ordering table), D1-1 and D1-2 ("Ask yourself first", "Report each step"), D3-1 (MoE numbers), D4-1 (description) and D5-1 (one hand-back rule): restructuring or prose-wide edits, which the brief excludes.
- Audit L1-L3 (`resolve-target.sh` with no models, `wait-gpu-release.sh` polling cost, exit 143 capture).
- R1-R4. The shared HF curl helper is deliberately not factored: the brief asks to copy the safe pattern.
- E1-E4 (entropy deletions).
- `SKILL.md` rule 2 is unchanged: exit 3/4 handling lives in the three flows that download.
- `make setup`'s own downloader (tracked) has no `HF_TOKEN` path. G1 downloads first, so 7a.4 finds the file already there.

## Owner decisions (defaults chosen)

- **Exit codes.** `fetch-gguf.sh` exits:
  - 3 for a retriable failure;
  - 4 for HTTP 401/403;
  - 1 only for a failure a re-run will not fix.

  Round 17 kept 401 at exit 1 because every caller stopped. Now the flow must act on 401 (ask for `HF_TOKEN`, re-run 4.3), so it branches on the code, not on parsing a message.
- **Re-runs.**
  - Exit 3: at most 3 re-runs of the same command (the AGENTS.md "retry up to 3 times" ladder). They run under the same G1 approval, with no new gate. After that, the exit is handled as exit 1.
  - Exit 4: one re-run after the owner exports the token. No token, or a second exit 4: handled as exit 1.
  - The token never appears in a command, a file under `tmp/` or the chat.
- **Hiding finished runs.** `chmod 000` on each finished run directory; the EXIT trap gives the owner rwx back. A mode change by the agent flags the row `leak_suspect`.
  - The agent runs as the owner, so this is a tripwire like the rest, but it stops a casual `cat ../...`.
  - Planning measured with a scratch script that bash runs an EXIT trap on INT, TERM and HUP while a foreground child runs. Only SIGKILL leaves directories locked.
  - Rejected: a fresh mktemp work dir (an agent can still `find` the earlier run under `tmp/`), and moving runs into `.done/` (more code, same strength).
- **Bypass: stop at G3, not re-tag.**
  - Stopping at G3 is text only, and it reuses the declined-G3 rollback that is already tested.
  - Re-tagging would need the image Id recorded before 7a.4 and a `podman tag` on four revert paths (G5 no, 6.4 reject after a No-device G3, failed 7b, failed 7c). It would also need a check that the old image still exists, and none of it can be tested without containers.
  - Consequence: under bypass, onboarding runs no ladder (not even the baseline lane) and reports the screen numbers. A measured keep needs a run without bypass.
- **Wording of the 401 message.** "no such repo or revision, or a gated or private repo: set HF_TOKEN". Without a token, Hugging Face answers 401 both for a missing repo and for a private one; `huggingface_hub`'s `RepositoryNotFoundError` text says the same. This was not checked live (no network).
- **Branch revision.** A revision that is neither `main` nor a 40-hex sha reads `/api/models/<repo>/revision/<rev>`, with fixture `model-<slug>@<rev>.json` (hf-scout's naming). `main` and shas keep `/api/models/<repo>` and `model-<slug>.json`, so the existing fixtures still hold.

## Review Focus

1. `effort-run.sh` is killed while a later run is going. Expected: the EXIT trap gives every hidden run directory back to the owner on TERM; only SIGKILL leaves them at 000, and the header says `chmod -R u+rwx <out>`. Pinned by `test_effort_run_restores_hidden_runs_when_terminated` (Task 3).
2. An agent that changes a hidden sibling's mode back and reads it. Expected: its row is `leak_suspect`, so `score.sh` drops it. Pinned by the `unlock` case of `test_effort_run_hides_finished_runs_from_later_runs_of_the_battery` (Task 3).
3. curl's raw exit code escaping `quant-headroom.sh` (`model="$(hf ...)"` under `set -e`) and colliding with fetch's 3 or 4. Expected: exit 1 with the `HF_TOKEN` hint. Pinned by `test_quant_headroom_names_hf_token_when_the_repo_is_unreadable` (`returncode == 1`, Task 2).
4. A missing repo without a token also returns 401. Expected: the message never claims the repo is private, only that it may be. Pinned by the "no such repo or revision, or a gated or private repo" assertions (Task 2).
5. A token that is set but refused. Expected: no loop; one re-run, then handled as exit 1. Pinned by the 403-with-token case (`returncode == 4`, "was sent and refused", Task 1) and by "No token, or a second exit 4: as exit 1" (Task 1 doc test).

---

### Task 0: Scratch runners and baseline

**Files:**
- Create: `tmp/r18/pytest-one.sh`, `tmp/r18/make-check.sh` (scratch; Task 6 deletes `tmp/r18/`)

- [ ] **Step 1: Write the runners.** `pytest-one.sh`:

```bash
#!/usr/bin/env bash
# pytest-one.sh FILE [ARGS...]: one pytest file; --basetemp and TMPDIR in fresh /var/tmp dirs, both removed after.
set -euo pipefail
repo="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
bt="$(mktemp -d /var/tmp/pytest-r18.XXXXXX)"
td="$(mktemp -d /var/tmp/pytest-r18-tmpdir.XXXXXX)"
trap 'rm -rf "$bt" "$td"' EXIT
cd "$repo"
TMPDIR="$td" uv run pytest "$@" -q -p no:cacheprovider --basetemp "$bt/bt"
```

`make-check.sh`:

```bash
#!/usr/bin/env bash
# make-check.sh: make validate, then make test with pytest's temp dir in a fresh /var/tmp dir, removed after.
set -euo pipefail
repo="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
td="$(mktemp -d /var/tmp/make-test-r18.XXXXXX)"
trap 'rm -rf "$td"' EXIT
cd "$repo"
make validate
TMPDIR="$td" make test
```

- [ ] **Step 2: Baseline.**
  - Run `bash tmp/r18/pytest-one.sh tests/skills/onboard-local-model/test_onboard_local_model_scripts.py`, then `test_challenges.py`, then `test_latency_profile.py`, one after another. Expected: all pass (409 in total). Record the count for each file.
  - Record `bash .claude/skills/onboard-local-model/scripts/gen-challenge.sh --generator-id` and `wc -c < .claude/skills/onboard-local-model/SKILL.md` (10993).

### Task 1: A failed download resumes or asks for HF_TOKEN before it stops

**Files:**
- Modify: `.claude/skills/onboard-local-model/scripts/fetch-gguf.sh` (header `:8-15`; `die` `:47`; `:83`; `:88`)
- Modify: `.claude/skills/onboard-local-model/scripts/quant-headroom.sh` (header `:21-22`; no code change: `cmd_fetch` ends in `bash fetch-gguf.sh`, so `set -e` passes its code on)
- Modify: `.claude/skills/onboard-local-model/references/onboard-flow.md` (4.3 `:154-155`; step 9 `:251`)
- Modify: `.claude/skills/onboard-local-model/references/local-gguf.md` (L4 `:48`)
- Modify: `.claude/skills/onboard-local-model/references/tuning.md` (Q2 `:176`)
- Modify: `.claude/skills/onboard-local-model/evals/evals.json` (append two evals)
- Test: `tests/skills/onboard-local-model/test_onboard_local_model_scripts.py`

**Interfaces:**
- Produces: the `fetch-gguf.sh` exit codes 0 fetched/present, 1 not retriable, 2 usage, 3 retriable (the `.part` is kept), 4 HTTP 401/403. `quant-headroom.sh fetch` exits with the same code. Eval ids `download-retriable-resume` and `download-gated-401`.

- [ ] **Step 1: Write the failing tests.**
  - In `test_fetch_gguf_gives_up_after_four_stalled_attempts_and_keeps_the_part`, change `result.returncode == 1` to `result.returncode == 3`.
  - In `test_fetch_gguf_stops_at_once_on_a_gated_repo`, change `result.returncode == 1` to `result.returncode == 4`.
  - Leave `..._on_another_client_error` and `..._when_the_server_cannot_resume` at 1.
  - Add after `test_quant_headroom_fetch_refuses_without_free_disk_and_for_unavailable_quants`:

```python
@pytest.mark.parametrize(("mode", "code"), [("stall", 3), ("http401", 4)])
def test_quant_headroom_fetch_passes_on_fetch_gguf_retry_and_auth_codes(tmp_path: Path, mode: str, code: int) -> None:
    good = hashlib.sha256(KNOWN).hexdigest()
    env = _qh_env(tmp_path, tree=_qh_tree() + [{"type": "file", "path": "m-Q8_0.gguf", "size": len(KNOWN), "lfs": {"oid": good}}])
    for name, body in (("curl", FETCH_CURL), ("sleep", 'echo "sleep $*" >>"$CALLS.sleep"\n')):
        (tmp_path / "bin" / name).write_text(f"#!/usr/bin/env bash\n{body}")
        (tmp_path / "bin" / name).chmod(0o755)
    result = _qh(env | {"FETCH_MODE": mode}, "fetch", "--alias", "m", "--quant", "Q8_0")
    assert result.returncode == code, result.stderr
    assert not (tmp_path / "models" / "m-Q8_0.gguf").exists()
```

  Add after `test_g1_downloads_through_fetch_gguf_and_can_resume`:

```python
def test_download_failures_resume_or_ask_for_hf_token_before_stopping() -> None:
    flow = _md(REFS / "onboard-flow.md")
    g1 = _section(_section(flow, "4. **Fit and configure.**", "5. **Baseline lane"), "**Gate G1**", "4. Select and check")
    assert all(s in g1 for s in ("Exit 3 (", "At most 3 re-runs, then as exit 1", "Exit 4 (HTTP 401 or 403", "`HF_TOKEN`",
                                 "never in a command or the chat", "re-run only 4.3", "No token, or a second exit 4: as exit 1"))
    assert g1.index("Exit 3 (") < g1.index("Exit 4 (") < g1.index("Exit 1: report its message")
    assert "an exit 3 or 4 that 4.3 stopped on" in _section(flow, "9. **Keep or revert", "## Entry template")
    assert "Exit 3: re-run it as `references/onboard-flow.md` 4.3 says" in _section(_md(REFS / "local-gguf.md"), "L4. ", "L5. ")
    q2 = _section(_md(REFS / "tuning.md"), "- Q2.", "- Q3.")
    assert all(s in q2 for s in ("A `fetch` exit 3", "at most 3 times, then as exit 1", "Exit 4 (HTTP 401 or 403)", "`HF_TOKEN`"))
    assert "At most 3 re-runs" in _asserts("download-retriable-resume")
    assert all(s in _asserts("download-gated-401") for s in ("HF_TOKEN", "never printed", "only step 4.3"))
```

- [ ] **Step 2: Run them and watch them fail.** `bash tmp/r18/pytest-one.sh tests/skills/onboard-local-model/test_onboard_local_model_scripts.py -k "four_stalled or gated_repo or retry_and_auth_codes or resume_or_ask_for_hf_token"`. Expected: FAIL. The two edited tests see exit 1; the parametrized one sees 1, not 3 or 4; the doc test fails on a missing phrase or a missing eval.

- [ ] **Step 3: Implement in `fetch-gguf.sh`.**
  - `die() { echo "fetch-gguf.sh: $1" >&2; exit "${2:-1}"; }`. Every caller already passes one quoted message.
  - `:83`: the same message, with exit code `4`.
  - `:88`: the same message, with exit code `3`.
  - Header:
    - Drop "(exit 1)" after "keeps the .part for a re-run".
    - Write "(exit 4)" after "stops at once with a gated-repo message".
    - Add one line: `# Exit 0 fetched or already present; 1 a failure a re-run will not fix (FILE taken, free disk, a size or sha256 mismatch, another HTTP error, curl exit 33); 2 usage; 3 retriable: 4 attempts failed on a stall, a dropped connection, HTTP 408, 429 or 5xx (the .part is kept: re-run the same command); 4 HTTP 401 or 403.`
  - In the `quant-headroom.sh` header, after "a different file already at the name exits 1 untouched", add: "; it exits with fetch-gguf.sh's code: 3 retriable (re-run), 4 HTTP 401/403 (set HF_TOKEN)".

- [ ] **Step 4: Write the docs.**
  - In `onboard-flow.md` 4.3, insert these two bullets before "      - Exit 1: report its message, ..." (that bullet stays unchanged):

```
      - Exit 3 (a stall or a dropped connection outlasted 4 attempts; the `.part` is kept): re-run the same command
        detached (rule 2), under the same G1; it resumes the `.part`. At most 3 re-runs, then as exit 1.
      - Exit 4 (HTTP 401 or 403: a gated or private repo): ask the owner to accept the licence on the repo's page and to
        export `HF_TOKEN` (a read token) in the environment the agent's shell starts with, never in a command or the chat,
        and never print it. Then re-run only 4.3; `$S`, the evidence card and steps 1-4.2 stay. No token, or a second exit 4: as exit 1.
```

  - In step 9 (`:251`), change "(a `fetch-gguf.sh` exit 1)" to "(a `fetch-gguf.sh` exit 1, or an exit 3 or 4 that 4.3 stopped on)".
  - In `local-gguf.md` L4, change "Exit 1: report its message and stop, as after a declined G1." to "Exit 3: re-run it as `references/onboard-flow.md` 4.3 says. Exit 1: report its message and stop, as after a declined G1."
  - In `tuning.md` Q2, after the line that starts "A declined G-Q1 or a `fetch` exit 1:" (that line stays), add:

```
    A `fetch` exit 3 (the download stalled or dropped 4 times; its `.part` is kept): re-run Q2's fetch detached, at most 3 times, then as exit 1.
    Exit 4 (HTTP 401 or 403): ask the owner to export `HF_TOKEN` in the agent's environment (never in a command or the chat), then re-run Q2's fetch once; no token, or exit 4 again: as exit 1.
```

  - Append to `evals.json`. Neither eval has `setup` or `files`, like `anchor-401-retry`. `jq empty` must pass.

```json
{
  "id": "download-retriable-resume",
  "prompt": "the Foo-9B download I approved at G1 ended with exit 3: \"fetch-gguf.sh: download failed after 4 attempts (curl exit 28); /home/u/llm-workspace/models/Foo-9B-Q4_K_M.gguf.part is kept: re-run the same command to resume\". What now? Answer in English; no commands need to be run.",
  "expected_output": "Exit 3 is retriable: re-run the same fetch-gguf.sh command detached (it resumes the .part) under the same G1, at most 3 times; the onboarding goes on at 4.4 once it succeeds. Only after the third failed re-run is it handled as exit 1: report, remove the scratch files, and ask whether to delete the .part.",
  "assertions": [
    "The answer re-runs the same fetch-gguf.sh command detached and polled (SKILL.md rule 2), which resumes the kept .part instead of starting over",
    "At most 3 re-runs; only then is it handled as exit 1 (report and stop, references/onboard-flow.md step 9)",
    "The run does not stop on the first exit 3, and G1 is not asked again for the same file",
    "The .part is not deleted; its deletion is offered only after the run stops, as a user-data delete the owner decides",
    "Nothing was executed"
  ]
},
{
  "id": "download-gated-401",
  "prompt": "G1 for lab/Foo-9B-GGUF stopped with exit 4: \"fetch-gguf.sh: HTTP 401: a gated or private repo; accept its licence on its Hugging Face page, set HF_TOKEN to a read token of that account (HF_TOKEN was not set), then re-run the same command\". I have a token. What now? Answer in English; no commands need to be run.",
  "expected_output": "Accept the licence and export HF_TOKEN in the environment the agent's shell starts with (never pasted into a command or the chat); then only step 4.3 runs again with the same fetch-gguf.sh command, while $S, the evidence card and the earlier steps are kept. A second exit 4 is handled as exit 1.",
  "assertions": [
    "Exit 4 asks the owner to accept the licence and export HF_TOKEN in the environment the agent's shell starts with, never in a command line or the chat",
    "Then only step 4.3 (the same fetch-gguf.sh command) runs again; $S, the evidence card and steps 1-4.2 are kept (references/onboard-flow.md)",
    "The token value is never printed or echoed",
    "A second exit 4 with the token set is handled as exit 1: report and stop (step 9)",
    "Nothing was executed"
  ]
}
```

- [ ] **Step 5: Run the tests and watch them pass.** Run Step 2's command, then the whole scripts test file. Expected: PASS, including the `user_turns`, `long_shell_logic`, `cite_each_other`, `stated_once` and `jev_as_a_black_box` guards.
- [ ] **Step 6: Repo checks.** `bash tmp/r18/make-check.sh`. Expected: green. Nothing to commit (untracked tree).

### Task 2: HF_TOKEN in quant-headroom, the auth hint, and branch revisions

**Files:**
- Modify: `.claude/skills/onboard-local-model/scripts/quant-headroom.sh` (header `:7-8`, `:25-26`; `hf` `:45-47`; `resolve_tree` `:66-77`; token check before the dispatch `case` at `:262`)
- Modify: `.claude/skills/onboard-local-model/scripts/hf-scout.sh` (header `:11`; message `:79-80`)
- Modify: `.claude/skills/onboard-local-model/references/onboard-flow.md` (H1 `:51`)
- Test: `tests/skills/onboard-local-model/test_onboard_local_model_scripts.py`

**Interfaces:**
- Consumes: Task 1's exit 4 wording in onboard-flow 4.3 (H1 points at it).
- Produces: `quant-headroom.sh` that reads `HF_TOKEN` (network mode only, `^[A-Za-z0-9_-]+$`, otherwise exit 2), and the hint text `no such repo or revision, or a gated or private repo: set HF_TOKEN` in both scripts.

- [ ] **Step 1: Write the failing tests.**
  - In `_hf_net`'s curl stub, change the line `    *"/revision/"*) cat {tmp_path}/model.json ;;` to `    *"/revision/"*) [ -z "${{STUB_FAIL_MODEL:-}}" ] || exit 22; cat {tmp_path}/model.json ;;` (an f-string, so the braces are doubled).
  - Add after `test_hf_scout_refuses_a_malformed_hf_token_before_any_request`:

```python
def test_hf_scout_names_hf_token_when_the_repo_is_unreadable(tmp_path: Path) -> None:
    result = _hf_net(tmp_path, STUB_FAIL_MODEL="1")
    assert result.returncode == 2 and "no such repo or revision, or a gated or private repo: set HF_TOKEN" in result.stderr
    sent = tmp_path / "sent"
    sent.mkdir()
    result = _hf_net(sent, STUB_FAIL_MODEL="1", HF_TOKEN=HF_TOKEN_VALUE)
    assert result.returncode == 2 and "HF_TOKEN, which was sent" in result.stderr and HF_TOKEN_VALUE not in result.stdout + result.stderr
    assert "its message names `HF_TOKEN`" in _section(_md(REFS / "onboard-flow.md"), "H1. ", "H2. ")
```

  Add a helper and three tests after `test_quant_headroom_fetch_passes_on_fetch_gguf_retry_and_auth_codes`:

```python
QH_NET_CURL = r"""echo "$*" >>"$CALLS.curl"
case " $* " in *" -K - "*) cat >>"$CALLS.stdin" ;; esac
tr '\0' ' ' <"/proc/$PPID/cmdline" >>"$CALLS.parent"; echo >>"$CALLS.parent"
[ -z "${STUB_FAIL_MODEL:-}" ] || exit 22
case "$*" in *"/tree/"*) cat "$HF_NET/tree.json" ;; *"/api/models/"*) cat "$HF_NET/model.json" ;; *) exit 22 ;; esac
"""


def _qh_net(tmp_path: Path, **extra: str) -> subprocess.CompletedProcess[str]:
    """quant-headroom.sh check in network mode (no HF_FIXTURE_DIR) against a curl stub that logs argv, -K stdin and its parent's argv."""
    env = {k: v for k, v in _qh_env(tmp_path).items() if k != "HF_FIXTURE_DIR"}
    net = tmp_path / "net"
    net.mkdir()
    (net / "model.json").write_text(json.dumps({"sha": QH_SHA, "gguf": {"total": QH_TOTAL}}))
    (net / "tree.json").write_text(json.dumps(_qh_tree()))
    (tmp_path / "bin" / "curl").write_text(f"#!/usr/bin/env bash\n{QH_NET_CURL}")
    (tmp_path / "bin" / "curl").chmod(0o755)
    return _qh(env | {"HF_NET": str(net), **extra}, "check", "--alias", "m", "--vram-available-mib", "8321")


def test_quant_headroom_sends_hf_token_only_through_curl_stdin(tmp_path: Path) -> None:
    result = _qh_net(tmp_path, HF_TOKEN=HF_TOKEN_VALUE)
    assert result.returncode == 0, result.stderr
    calls = (tmp_path / "calls.curl").read_text().splitlines()
    assert len(calls) == 2 and all(" -K - " in f" {c} " and HF_TOKEN_VALUE not in c and "https://huggingface.co/api/models/o/r" in c for c in calls)
    assert (tmp_path / "calls.stdin").read_text() == f'header = "Authorization: Bearer {HF_TOKEN_VALUE}"\n' * 2
    assert HF_TOKEN_VALUE not in (tmp_path / "calls.parent").read_text() + result.stdout + result.stderr
    plain = tmp_path / "plain"
    plain.mkdir()
    result = _qh_net(plain)
    assert result.returncode == 0 and "-K" not in (plain / "calls.curl").read_text() and not (plain / "calls.stdin").exists()


def test_quant_headroom_refuses_a_malformed_hf_token_before_any_request(tmp_path: Path) -> None:
    result = _qh_net(tmp_path, HF_TOKEN='hf_x"\nurl = "https://example.com/')
    assert result.returncode == 2 and "HF_TOKEN" in result.stderr and "hf_x" not in result.stdout + result.stderr
    assert not (tmp_path / "calls.curl").exists()


def test_quant_headroom_names_hf_token_when_the_repo_is_unreadable(tmp_path: Path) -> None:
    result = _qh_net(tmp_path, STUB_FAIL_MODEL="1")
    assert result.returncode == 1, (result.returncode, result.stderr)  # never curl's own code, which could read as fetch's 3 or 4
    assert "no such repo or revision, or a gated or private repo: set HF_TOKEN" in result.stderr
```

  Add after `test_quant_headroom_check_resolves_an_unpinned_entry_only_when_the_file_matches`:

```python
def test_quant_headroom_check_resolves_a_branch_at_that_branch(tmp_path: Path) -> None:
    v2 = "fedcba9876543210fedcba9876543210fedcba98"
    entry = dict(QH_CONFIG["models"][0], url="https://huggingface.co/o/r/resolve/v2/m-Q4_K_M.gguf", size_bytes=5512 * MIB)  # type: ignore[index]
    env = _qh_env(tmp_path, config=QH_CONFIG | {"models": [entry]})
    hf = Path(env["HF_FIXTURE_DIR"])
    (hf / "model-o__r@v2.json").write_text(json.dumps({"sha": v2, "gguf": {"total": QH_TOTAL}}))
    (hf / f"tree-o__r@{v2}.json").write_text(json.dumps(_qh_tree({"m-Q4_K_M.gguf": 5512 * MIB, "m-Q5_K_M.gguf": 6400 * MIB})))
    result = _qh(env, "check", "--alias", "m", "--vram-available-mib", "8321")
    assert result.returncode == 0, result.stderr
    assert f"resolved v2 to {v2}" in result.stdout  # main's QH_SHA tree has the same Q4 size, so the old code passed its guard
    cand = _candidates(tmp_path)[1]
    assert cand["sha"] == v2 and next(c for c in cand["candidates"] if c["quant"] == "Q5_K_M")["size_bytes"] == 6400 * MIB  # type: ignore[union-attr]
```

- [ ] **Step 2: Run them and watch them fail.** Run `-k "names_hf_token or hf_token_only_through_curl_stdin or malformed_hf_token or branch_at_that_branch"`. Expected FAIL:
  - The hf-scout message is still "(no such repo or revision?)".
  - The quant token test sees no `-K`.
  - The unreadable test sees exit 22 and no message.
  - The branch test sees "resolved v2 to 0123...".
  - The existing hf-scout malformed-token test still passes. The new quant-headroom one fails, because the script currently ignores the token.

- [ ] **Step 3: Implement.**
  - In `quant-headroom.sh` `hf()`, add a network branch, copied from `hf-scout.sh:72`: when `HF_TOKEN` is set and `$2` matches `^https://huggingface\.co/`, run `printf 'header = "Authorization: Bearer %s"\n' "$HF_TOKEN" | curl -fsS --max-time 60 -K - "$2"`. Otherwise run the current curl.
  - Before the dispatch `case "$sub" in` (after the `--help` loop), add one line, with the same text as `hf-scout.sh:56-57`: `[ -n "${HF_FIXTURE_DIR:-}" ] || [ -z "${HF_TOKEN:-}" ] || [[ "$HF_TOKEN" =~ ^[A-Za-z0-9_-]+$ ]] || fail_usage "HF_TOKEN must hold only [A-Za-z0-9_-] (a Hugging Face access token); its value is not shown"`.
  - In `resolve_tree`:
    - Start with `murl="https://huggingface.co/api/models/$repo"` and `fx="model-$slug.json"`. When `$rev` is neither `main` nor 40 hex characters, append `/revision/$rev` to `murl` and set `fx="model-$slug@$rev.json"`.
    - Read the model with `model="$(hf "$fx" "$murl")" || die "cannot read $repo at $rev from the Hugging Face API (no such repo or revision, or a gated or private repo: set HF_TOKEN${HF_TOKEN:+, which was sent: check that its account has access})"`.
    - Append the same parenthesis to the tree read's `die` message.
  - `quant-headroom.sh` header:
    - `:8`: write "an unpinned resolve/<branch or tag>/ url is resolved at that revision only when ..." in place of "an unpinned resolve/main/ url is resolved only when ...".
    - Env line: add "HF_TOKEN (optional; network mode only) is sent to https://huggingface.co/ urls as an Authorization: Bearer header through curl's stdin config (-K -), never in argv or output; it must match [A-Za-z0-9_-]+ (exit 2 otherwise, value not shown)".
  - `hf-scout.sh:80`: replace "(no such repo or revision?)" with "(no such repo or revision, or a gated or private repo: set HF_TOKEN${HF_TOKEN:+, which was sent: check that its account has access})". In the header at `:11`, after "an unreadable repo or revision exits 2", add " (its message names HF_TOKEN)".
  - `onboard-flow.md` H1, the bullet "Exit 2 (...): report and stop." becomes:

```
    - Exit 2 (the file is not in that commit, or the repo or revision is unreadable): report and stop. A private or gated
      repo exits 2 too and its message names `HF_TOKEN`: once the owner has exported it as 4.3's exit 4 says, re-run H1.
```

- [ ] **Step 4: Run the tests and watch them pass.** Run Step 2's command, then the whole scripts test file. Expected: PASS. The existing `test_hf_scout_reads_a_named_file_at_a_pinned_revision` ("nope" in stderr) and every `_qh_env` test are unchanged and green.
- [ ] **Step 5: Repo checks.** `bash tmp/r18/make-check.sh`. Expected: green.

### Task 3: Finished ladder runs are hidden from later runs of the battery

**Files:**
- Modify: `.claude/skills/onboard-local-model/scripts/effort-run.sh` (header `:30-31`; trap `:133`; run loop `:182-229`)
- Modify: `.claude/skills/onboard-local-model/references/challenges.md` (`:125`)
- Test: `tests/skills/onboard-local-model/test_onboard_local_model_scripts.py` (`AGENT_MISBEHAVIOUR`, `test_effort_run_aborts_when_the_pristine_challenge_changes`, two new tests after it)

**Interfaces:**
- Produces: once its row is written, each run directory `$out/<task>-<effort>` has mode 000 until the script exits. When the script exits, it has mode 700. The `results.jsonl` row schema is unchanged.

- [ ] **Step 1: Write the failing tests.** Append to `AGENT_MISBEHAVIOUR`:

```bash
if [ -n "${SIBLING:-}" ]; then
  case "$*" in
  *"--thinking low"*) echo low-solution >src/low_only.js ;;
  *"--thinking high"*) [ -z "${UNLOCK:-}" ] || chmod u+rwx ../../l1-s7-low
    cat ../../l1-s7-low/work/src/low_only.js >src/copied.js 2>/dev/null || echo blocked >src/copied.js ;;
  esac
fi
[ -z "${SLOW_HIGH:-}" ] || case "$*" in *"--thinking high"*) touch "$SLOW_HIGH"; /bin/sleep 30 ;; esac
```

  In `test_effort_run_aborts_when_the_pristine_challenge_changes`, append `assert os.access(_out(tmp_path, "out") / "l1-s7-low", os.R_OK | os.X_OK)`. New tests:

```python
@NEEDS_NODE
@pytest.mark.parametrize(("unlock", "leak"), [("", False), ("1", True)])
def test_effort_run_hides_finished_runs_from_later_runs_of_the_battery(tmp_path: Path, unlock: str, leak: bool) -> None:
    seeds = _seeds_file(tmp_path, {"1": [7]})
    result = _effort_run(tmp_path, "--layers", "1", "--seeds", str(seeds), "--efforts", "low,high", env={"SIBLING": "1", "UNLOCK": unlock})
    assert result.returncode == 0, result.stderr
    out = _out(tmp_path, "out")
    assert (out / "l1-s7-high" / "work" / "src" / "copied.js").read_text() == ("low-solution\n" if unlock else "blocked\n")
    assert [(r["effort"], r["leak_suspect"]) for r in _rows(tmp_path)] == [("low", False), ("high", leak)]
    assert all(os.access(out / f"l1-s7-{e}", os.R_OK | os.X_OK) for e in ("low", "high")), "given back when the script exits"
    assert (out / "l1-s7-low" / "work" / "src" / "low_only.js").read_text() == "low-solution\n"


@NEEDS_NODE
def test_effort_run_restores_hidden_runs_when_terminated(tmp_path: Path) -> None:
    marker, out = tmp_path / "high-started", _out(tmp_path, "out")
    path = ":".join(p for p in (str(_stub_bin(tmp_path)), NODE_DIR, "/usr/bin", "/bin") if p)
    env = {"PATH": path, "HOME": str(tmp_path / "home"), "ONBOARD_GUARD_ROOT": str(_guard_root(tmp_path)),
           "LLM_ENV_CONFIG": str(tmp_path / "models.yml"), "STUB_ARGV": str(tmp_path / "argv.txt"), "SLOW_HIGH": str(marker)}
    args = ["bash", str(SCRIPTS / "effort-run.sh"), "--model", "p/m", "--out", str(out), "--efforts", "low,high", "--layers", "1",
            "--seeds", str(_seeds_file(tmp_path, {"1": [7]}))]
    proc = subprocess.Popen(args, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        deadline = time.monotonic() + 120
        while not marker.exists() and proc.poll() is None and time.monotonic() < deadline:
            time.sleep(0.1)
        assert marker.exists() and (out / "l1-s7-low").stat().st_mode & 0o777 == 0, "hidden while the high run works"
        proc.send_signal(signal.SIGTERM)  # the main shell only, as a subagent's kill would
        proc.wait(timeout=60)
        assert os.access(out / "l1-s7-low", os.R_OK | os.X_OK)
    finally:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()
```

- [ ] **Step 2: Run them and watch them fail.** Run `-k "hides_finished_runs or restores_hidden_runs or pristine_challenge_changes"`. Expected FAIL:
  - `unlock=""`: `copied.js` holds `low-solution`.
  - `unlock="1"`: `leak_suspect` is false.
  - The terminate test: mode is 0o755, not 0.
  - The pristine test still passes (nothing is locked yet). That is fine: its new line guards the exit-1 path after Step 3.

- [ ] **Step 3: Implement in `effort-run.sh`.**
  - Before the run loop, set `locked=()`. Extend the EXIT trap (`:133`) so that after the sampler kill it also runs `[ ${#locked[@]} -eq 0 ] || chmod u+rwx "${locked[@]}" 2>/dev/null || true`.
  - After the agent and before the row: for each `d` in `locked`, if `stat -c %a "$d"` is not `0`, set `leak=true` (the agent changed a hidden run's mode back).
  - Right after the row's `tee -a` and before `[ "$pristine_changed" = 0 ] || ...`: `chmod 000 "$run"; locked+=("$run")`. Locking before that exit check means an aborting run is locked too, and the trap gives it back.
  - Header `:30-31`:
    - Replace "Not detected: reads or writes of sibling run directories under <out>, reads that never name ..." with a new sentence: `# Each finished run directory is mode 000 until the script exits (the EXIT trap gives the owner rwx back, also on failure, INT, TERM and HUP), so a later run of the battery cannot read an earlier run's solution without changing that mode back, which flags its row leak_suspect. A SIGKILL leaves them at 000: chmod -R u+rwx <out> before reading or removing it.`
    - Follow it with `# Not detected: reads that never name the paths above, ...` (the rest unchanged).
  - `challenges.md:125`:
    - Replace "- What is not detected: reads or writes of sibling run directories under the out directory, reads that never name the paths above," with two bullets.
    - First bullet: "- Finished run directories under the out directory are mode 000 until `effort-run.sh` exits, so a later run of the battery cannot read an earlier run's solution without changing that mode back, which flags its row `leak_suspect`. After a SIGKILL they stay 000: `chmod -R u+rwx <out>` before reading or removing it."
    - Second bullet: "- What is not detected: reads that never name the paths above," followed by the rest of the old bullet.

- [ ] **Step 4: Run the tests and watch them pass.** Run Step 2's command, then the whole scripts test file. Expected: PASS. Every other `test_effort_run_*` test is unchanged (single effort: nothing is locked while an agent runs).
- [ ] **Step 5: Repo checks.** `bash tmp/r18/make-check.sh`. Expected: green. Then `find tmp -maxdepth 4 -perm 000` prints nothing.

### Task 4: With permissions bypassed, onboarding stops at G3

**Files:**
- Modify: `.claude/skills/onboard-local-model/SKILL.md` (`:88-89`)
- Modify: `.claude/skills/onboard-local-model/references/onboard-flow.md` (5.1 `:166`; 7a heading `:189`)
- Test: `tests/skills/onboard-local-model/test_onboard_local_model_scripts.py` (`test_skill_gates_announce_and_proceed_when_permissions_are_bypassed` `:923`)

**Interfaces:**
- Consumes: step 9's declined-G3 rollback, unchanged (`make stop-llama`; nothing to restore before G3; `make start-llama` when the first step-1 read found llm-server serving).

- [ ] **Step 1: Write the failing test.** Append to `test_skill_gates_announce_and_proceed_when_permissions_are_bypassed`:

```python
    assert "G3, G5 and G-Q3 count as a no (a model or image switch is the owner's call)" in gates
    flow = _md(REFS / "onboard-flow.md")
    assert "Under bypass it is empty, because G3 counts as a no" in _section(flow, "5. **Baseline lane", "6. **Screen")
    assert "Under bypass G3 counts as a no: report the step 6 screen numbers" in _section(flow, "**7a. Apply and serve", "**7b.")
```

- [ ] **Step 2: Run it and watch it fail.** `-k permissions_are_bypassed`. Expected: FAIL on the first new line.
- [ ] **Step 3: Edit the text.**
  - `SKILL.md:88-89`: the two lines starting "**Permissions bypassed.** With permissions bypassed, ..." become the lines below. The lowercase "permissions bypassed" stays, which the existing assertion needs. `STATED_ONCE`'s "G5 and G-Q3 count as a no" stays a substring.

```
**Bypass.** With permissions bypassed, a gate becomes announce the facts, then proceed; G3, G5 and G-Q3 count as a no
(a model or image switch is the owner's call).
```

  - `onboard-flow.md` 5.1 becomes:

```
   1. Decide the lane set first (baseline, candidate, any anchor the owner named), all on the router profile (rule 3).
      Under bypass it is empty, because G3 counts as a no (`SKILL.md` Gates): go to 5.4.
```

  - `onboard-flow.md` 7a: after "**7a. Apply and serve (Gate G3).**" on the same line, add "Under bypass G3 counts as a no: report the step 6 screen numbers as the result (the No-device order has none: say the candidate was not screened), then step 9." Wrap it as the file wraps.

- [ ] **Step 4: Verify.** `wc -c < .claude/skills/onboard-local-model/SKILL.md`. Expected: 10973. Then run Step 2's test and the whole scripts test file. Expected: PASS.
- [ ] **Step 5: Repo checks.** `bash tmp/r18/make-check.sh`. Expected: green.

### Task 5: The routing sentence reads one way; the standalone quant comparison names its T steps

**Files:**
- Modify: `.claude/skills/onboard-local-model/SKILL.md` (`:37-38`, `:82`)
- Modify: `.claude/skills/onboard-local-model/references/tuning.md` (checklist `:129`; new S0 line before "Before Q1" `:133`; delete `:158`)
- Test: `tests/skills/onboard-local-model/test_onboard_local_model_scripts.py` (`:1195`; one new test)

- [ ] **Step 1: Write the failing tests.**
  - Replace the assertion at `:1195` with:

```python
    assert "the model argument's row wins, but a tune or quant request on the enabled alias takes the tune or quant row" in text
    assert "the model argument wins (" not in text
```

  - Add after `test_g1_downloads_through_fetch_gguf_and_can_resume`:

```python
def test_standalone_quant_comparison_says_which_t_steps_run() -> None:
    tuning = _md(REFS / "tuning.md")
    standalone = _section(tuning, "### Standalone quant comparison", "- Q1.")
    assert "Progress checklist (copy it into your task list): S0 preflight; P0 url;" in standalone
    assert ("S0. T1 (preflight and profile check); T2 only when `llm_server.enabled` is false; T4-T6 (sweep, apply and restart) "
            "only when the owner also asks for tuning.") in standalone
    assert standalone.index("S0. T1") < standalone.index("- P0.") and "T2 runs only when" not in tuning
```

- [ ] **Step 2: Run them and watch them fail.** `-k "routes_near_misses or says_which_t_steps_run"`. Expected: FAIL.
- [ ] **Step 3: Edit the text.**
  - `SKILL.md:37-38` becomes:

```
- When two rows match, the model argument's row wins, but a tune or quant request on the enabled alias takes the tune or
  quant row; an anchor named with it joins that flow's quality step.
```

  - `SKILL.md:82`: change "restart fails (logged) and never reloads it" to "restart fails and never reloads it". No test pins it, and it pays for the routing clause.
  - `tuning.md:129`: change "Progress checklist (copy it into your task list): P0 url;" to "Progress checklist (copy it into your task list): S0 preflight; P0 url;".
  - Insert this line right before "Before Q1, in this order, ...":

```
- S0. T1 (preflight and profile check); T2 only when `llm_server.enabled` is false; T4-T6 (sweep, apply and restart) only when the owner also asks for tuning.
```

  - Delete the line "T2 runs only when `llm_server.enabled` is false; T4-T6 (sweep, apply and restart) only when the owner also asks for tuning." (`:158`).

- [ ] **Step 4: Verify.** `wc -c < .claude/skills/onboard-local-model/SKILL.md`. Expected: 10983. Then run Step 2's tests and the whole scripts test file. Expected: PASS, the `router_that_loads` cap test and `stated_once` included. The eval `tune-named-enabled-alias` stays as it is: its "exception" wording still fits.
- [ ] **Step 5: Repo checks.** `bash tmp/r18/make-check.sh`. Expected: green.

### Task 6: Verification and cleanup

- [ ] **Step 1: Byte cap.** `wc -c < .claude/skills/onboard-local-model/SKILL.md`. Expected: 10983.
- [ ] **Step 2: Guards.** Run `bash tmp/r18/pytest-one.sh tests/skills/onboard-local-model/test_onboard_local_model_scripts.py -k "jev_as_a_black_box or stated_once or cite_each_other or router_that_loads or only_existing_scripts or long_shell_logic or user_turns or input_files_exist or nickname"`. Expected: PASS.
- [ ] **Step 3: Skill suites, one at a time.** Run Task 0's three files in turn. Expected:
  - All pass, with 12 more items in the scripts file than the baseline:
    - Task 1: 3 (one parametrized test with 2 cases, plus the doc test);
    - Task 2: 5;
    - Task 3: 3 (one parametrized test with 2 cases, plus the terminate test);
    - Task 5: 1.

    `test_challenges.py` and `test_latency_profile.py` keep their counts.
  - `gen-challenge.sh --generator-id` prints Task 0's id.
- [ ] **Step 4: Repo checks.** `bash tmp/r18/make-check.sh`, with no other pytest running. Expected: "All checks passed." and a green suite.
- [ ] **Step 5: Route walk** (read only; nothing runs against the host). Read as a new agent who has only `SKILL.md`. Every branch must end in a step, a gate or a stop:
  1. A 20 GB G1 that ends with exit 3. The agent re-runs the same command detached, up to 3 times; it resumes from `.part`; then 4.4 runs.
  2. A gated repo, exit 4. The agent asks for `HF_TOKEN` (export it, never paste it) and re-runs only 4.3. A second exit 4 goes to step 9, which asks about the `.part`.
  3. A private repo at H1: exit 2, and the message names `HF_TOKEN`. Once the token is exported, H1 runs again.
  4. Bypass with a served baseline: no lane at step 5, then G2, the screens, the stop at G3 with the numbers. Step 9 (declined G3) runs `make start-llama` and offers to delete the file.
  5. Bypass on a host with no `gpu.device_name`: G3 comes before the screen. The run stops and says the candidate was not screened.
  6. "tune gemma4" on a served host goes to `tuning.md`; "is gemma4 slower" goes to `served-alias.md`.
  7. A standalone quant comparison on a gateway-only host: S0 runs T1, then T2, then P0.

  Report any branch without a next step as a finding; do not fix it in this plan.
- [ ] **Step 6: Cleanup.**
  - `rm -rf tmp/r18`.
  - `ls /var/tmp | grep -e pytest-r18 -e make-test-r18` prints nothing.
  - `find tmp -maxdepth 4 -perm 000` prints nothing.
  - `podman ps -a` shows nothing this plan started (it starts nothing), and `ps` shows no process left by a test, including no `sleep 30` from the terminate test.
  - `git status --short` shows no tracked change besides `docs/superpowers/plans/README.md` with its new row. This plan file stays untracked, like the earlier judge-round plans.
  - The orchestrator re-runs the judges; this plan does not.
