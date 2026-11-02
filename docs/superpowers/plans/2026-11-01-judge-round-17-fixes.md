# Judge Round 17 Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the four real defects from round 17 of the onboard-local-model skill judges, plus the referee's one cheap routing note, and nothing else.

**Architecture:** Five narrow changes, all in the untracked skill tree and its tests:
- **A1 local GGUF (native high D8-1 = audit M1).** A `.gguf` that matches a disabled live entry whose file is missing (rule A1) builds its scratch config from that alias. It then runs as the Alias route, so the fit and screen keep the entry's own `n_cpu_moe`, `ctx_size` and `vram_budget`. One fixture eval covers it.
- **Router profile after a revert (D8-2 = M2).** Step 9 runs `make setup-local-llm-agents` after every rejected or reverted onboarding once 7a.7 ran, whatever the first step-1 read found.
- **Long runs (D8-3 = M3).** `fetch-gguf.sh` aborts a stalled transfer and retries transient failures from its `.part`, at most 4 attempts in all. `SKILL.md` rule 2 runs `bench-fit.sh` and every download detached and polled, and re-runs a run that was killed (exit 143) or lost.
- **Gated repos (D3-1 = M4).** On a 401 or 403, `fetch-gguf.sh` stops at once with a message naming the gate. An optional `HF_TOKEN` reaches curl in `fetch-gguf.sh` and `hf-scout.sh` only through curl's stdin config.
- **Referee note.** A tune or quant request that names the enabled alias takes its own row, not the re-measure. One clause in `SKILL.md` and one eval.

`SKILL.md` goes from 10,959 to 10,985 bytes (cap 11,000), measured on a scratch copy with exactly the Task 5 edits. Two unpinned cuts pay for the additions.

**Tech Stack:** bash, curl, jq, yq; Python pytest (curl is a PATH stub in every new test); skill-judge rubric v4.

**Spec:**
- `tmp/judges/r17-native-summary.txt`: findings D8-1, D8-2, D8-3 and D3-1, the same four as audit Must-fix M1-M4.
- `tmp/judges/r17-referee-summary.txt`: no confirmed finding. The unconfirmed note sits under its D8-3: "tune <enabled alias>" goes to served-alias.md, whose row says Do NOT load tuning.md.
- Base state: the skill tree as the r17 judges read it (newest file 2026-11-01 10:09). Plan R's changes are already in it (`fetch-gguf.sh --from`, `live-entry.sh`).

## Global Constraints

- English only. Edit only:
  - `.claude/skills/onboard-local-model/**`;
  - `tests/skills/onboard-local-model/**`;
  - this plan's row in `docs/superpowers/plans/README.md` (already added; earlier rows stay as they are).
- Scope is closed. Do not touch anything the "Not planned" list names: no restructuring, no new gates, no prose-wide edits, no refactors.
- No tracked code changes, so there is nothing to commit. The skill tree and `tests/skills` stay untracked. If a step turns out to need a tracked file (`tools/lib.sh`, `llmenv.py`, `scripts/`, `setup/`), stop and ask. Any approved tracked change goes in its own small commit after `make validate && make test`, with no attribution lines.
- `SKILL.md` must stay <= 11,000 bytes (`SKILL_MD_MAX_BYTES`, tested). Never raise the cap. Shorten before adding, and move text to references rather than growing `SKILL.md`. Task 5 makes the only `SKILL.md` edits.
- `jev-fast-decision` is a black box, and ai-kit is never named in the skill tree. `test_skill_and_touched_docs_treat_jev_as_a_black_box` scans `scripts/*.sh` too, so new script comments must not say "consent", "client timeout" or "--probe" either.
- Prose holds no shell logic: a backtick span holds at most two of `;`, `&&`, `||`. Shared rules are stated once (`STATED_ONCE`), and retired wording stays out (`RETIRED_COPIES`). Before you replace a sentence, grep the tests and `evals/evals.json` for it. A pinned phrase changes together with its pin.
- A reference names another only by path, and only for the pairs in `CITES`. This plan adds no pair.
- Every script keeps `--help` (exit 0; first line `<name>.sh — ...`) and its tests. A behaviour change is red-first: write the test, run it and watch it fail, then implement. Doc pins are written first too, and fail until the text lands.
- After a `.sh` edit run `make validate`. After a `.py` edit (test files included) run `make validate && make test` through `tmp/r17/make-check.sh` (Task 0). That script gives `make test` a fresh `TMPDIR` under `/var/tmp` and removes it. Every task that touches the test file ends with it.
- pytest: one run at a time, never two at once; `make test` counts as a run. Single files always go through `tmp/r17/pytest-one.sh` (Task 0). It puts `--basetemp` and `TMPDIR` in two fresh `mktemp -d /var/tmp/...` dirs and deletes both afterwards; neither ever goes under `./tmp/`. After every text edit, run the whole `test_onboard_local_model_scripts.py`, because many of its tests pin prose.
- Scratch goes under `./tmp/r17/`, and Task 6 removes it. Not allowed: containers, GPU, network, downloads, and any edit to the live `models.yml`, `~/.pi` or a capacity card. Shell logic belongs in script files, not long one-liners.
- Eval `prompt` is the user's message alone (`EVAL_SETUP` must not match it). `setup` is present exactly when `files` is. Every listed file's path appears in `prompt` or `setup`.
- Line numbers refer to the files as of 2026-11-01 10:09. Find text by the quoted anchor, not by the number.

## Findings ledger (verified 2026-11-01 against the files; none dropped)

Read-only planning checks used scratch under `tmp/r17-plan/`, removed afterwards.

| Id | Verdict | Evidence |
|---|---|---|
| D8-1 / M1 (high) | real | Probe on fixture copies: a 64-byte `~/Downloads/ornith-1.0-35b-Q4_K_M.gguf`, with the config's `ornith-35b` set to `size_bytes: 64`. `resolve-target.sh <file> --scratch-config` writes `candidate` with `ctx_size 131072`, `n_cpu_moe null` and `vram_budget 55%`, which are gemma4's values (resolve-target.sh:186-187). `new-alias.sh` prints `{"alias":"ornith-35b","rule":"A1"}`. `resolve-target.sh ornith-35b --scratch-config` exits 0 even though the file is absent, and its only enabled entry keeps `ctx_size 262144`, `n_cpu_moe 28` and `vram_budget 85%`. `llmenv budget` on that config with `--models-dir` set to the file's own directory runs; the header-only fixture gives `general.architecture missing from GGUF metadata`. local-gguf.md:30-31 never switches `$S` or `<alias>`, while onboard-flow.md:141 skips the settings write for A1. No eval or test covers the branch. |
| D8-2 / M2 | real | onboard-flow.md:255-257 puts "Then, once 7a.7 ran, `make setup-local-llm-agents`" inside the bullet "when the first step-1 read found llm-server serving". "A host that was gateway-only stops there" skips it. An enabled-but-stopped host never matches that condition. setup-local-llm-agents.sh:31-41 maps only enabled entries, and none when `llm_server.enabled` is false. install-agent-clients.sh:103 replaces the whole `router-env` provider. So the run would drop `llama-cpp/<candidate>`, but on those hosts it never runs. The script needs OmniRoute healthy (:107), which holds after 7a.6. |
| D8-3 / M3 | real | SKILL.md:51 says "may run inline", and test :1190 pins that wording. fetch-gguf.sh:60 runs `curl -fL --continue-at - -o ...` with no `--speed-limit`, `--speed-time` or retry. bench-fit.sh:51-52 traps INT/TERM and removes its container. The 600 s foreground cap of the harness shell tool comes from the brief and the judge; it was not measured here. |
| D3-1 / M4 | real | fetch-gguf.sh:60 answers every curl failure, a 401 included, with "re-run the same command to resume". No skill script reads `HF_TOKEN`; only the tracked `scripts/system-one-cache-check.py` does, for another purpose. `man curl` (8.18.0 here): "Authorization:" headers are not passed on when a redirect leads to another origin, unless `--location-trusted` is used. latency-profile.sh:80 is the house pattern for a secret header: `printf 'header = ...' \| curl -K -`. |
| Referee note | real, cheap | SKILL.md:37: "When two rows match, the model argument wins". Row :21 (enabled alias, re-measure) says Do NOT load `tuning.md`. So "tune gemma4" and "compare Q4_K_M vs Q5_K_M of gemma4" both go to the re-measure. None of the four tune or quant evals names an alias, so no eval catches this. The fix is one clause and one eval. |

## Not planned (out of scope, with the reason)

- Native D5-1 (split onboard-flow.md), D7-1 (replace prose-substring tests, add `.part`/`UD-` evals), D2-1 (step 9 table), D6-1 (scripted scratch settings), D4-1 (description rewrite), D1-1 (selection-criteria section 3): these are restructuring or prose-wide changes, and the brief excludes them.
- D3-1's two Gotcha lines: the byte cap leaves no room. The 401/403 message carries the HF_TOKEN fact, and rule 2 carries the 600 s fact.
- Route-walk lows and audit Should-fix: local split GGUF detection, `resolve-target.sh` exit 1 route, the `wait-gpu-release.sh` transient exit 2, racing live edits. Also the reuse items (three redaction copies, two `hf()` helpers, four quant regexes) and every entropy deletion.
- `quant-headroom.sh`'s own `hf()` tree read sends no `HF_TOKEN`. A gated repo's tree is public, and its download goes through `fetch-gguf.sh`.
- `make setup`'s own downloader (tracked code) has no `HF_TOKEN` path. G1 downloads the file first, so 7a.4 finds it present.
- A `bench-fit.sh` killed by SIGKILL (not TERM) leaves `llm-env-scout-<pid>`. Step 9's leftover check (`podman ps -a`) reports it, and testing it needs a container.

## Owner decisions (defaults chosen)

- A 401/403 exits 1, not with a new code. Every caller already routes exit 1 as "report its message and stop" (onboard-flow 4.3, local-gguf L4, tuning Q2), so no reference changes.
- Retries: at most 4 attempts in all, `sleep 5` between attempts. Curl exit 22 (an HTTP error under `-f`) retries only on 408, 429 and 5xx. Any other non-zero curl exit retries, because a stall arrives as exit 28 with HTTP 206. Stall abort: `--speed-limit 1024 --speed-time 120`, the judge's values.
- Token transport: the curl config goes on stdin (`-K -`, the latency-profile.sh pattern), with no temp file. `fetch-gguf.sh` sends the token only to `https://huggingface.co/` and `https://hf.co/` urls. A token that does not match `^[A-Za-z0-9_-]+$` exits 2 without printing it, because it would otherwise inject curl config lines. Each script validates the token only when it would send it.
- The referee clause covers quant requests too: it is the same rule and the same row, and costs about 10 more bytes.
- `SKILL.md` cuts: "paths are repo-relative; " (unpinned) and the NEVER leaderboard parenthetical. `references/anchors.md:16-17,29` already holds both spreads. Grep: no test or eval pins either.

## Review Focus

1. A Hugging Face resolve url redirects to a CDN on another host. The Bearer header must not follow it: curl drops a custom Authorization header on a cross-origin redirect, and `fetch-gguf.sh` never passes `--location-trusted`. Task 3's token test pins that.
2. An `HF_TOKEN` holding a quote or newline (`hf_x"` + newline + `url = ...`) must not add curl config lines. Expected: exit 2 before any request, with the value never printed. Pinned in Task 3 and Task 4.
3. A transfer that stalls after its 206 response (curl exit 28, HTTP 206) must retry from the `.part`, not fail as an HTTP error. Task 3's `stall` and `drop` stub modes answer 206.
4. An A1 local GGUF on a host without `gpu.device_name`, or with the `tmp/` screening copy. Expected: `<alias>` stays the A1 alias in the No-device order and in 6.2, and 7a.2's first match finds the live entry, so no `live-entry.sh add` runs. Task 1 pins "`<alias>` is the A1 alias from then on" and "no L5".
5. An agent interrupted while its detached download keeps running. Two fetches must never append to one `.part`. Expected: re-run only once its PID is gone. Task 5 pins "once its PID is gone".

---

### Task 0: Scratch pytest runner

**Files:**
- Create: `tmp/r17/pytest-one.sh`, `tmp/r17/make-check.sh` (scratch; Task 6 deletes `tmp/r17/`)

- [ ] **Step 1: Write the runners.** `pytest-one.sh`:

```bash
#!/usr/bin/env bash
# pytest-one.sh FILE [ARGS...]: one pytest file; --basetemp and TMPDIR in fresh /var/tmp dirs, both removed after.
set -euo pipefail
repo="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
bt="$(mktemp -d /var/tmp/pytest-r17.XXXXXX)"
td="$(mktemp -d /var/tmp/pytest-r17-tmpdir.XXXXXX)"
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
td="$(mktemp -d /var/tmp/make-test-r17.XXXXXX)"
trap 'rm -rf "$td"' EXIT
cd "$repo"
make validate
TMPDIR="$td" make test
```

- [ ] **Step 2: Baseline.** Run `bash tmp/r17/pytest-one.sh tests/skills/onboard-local-model/test_onboard_local_model_scripts.py`, then `test_challenges.py`, then `test_latency_profile.py`, one after another. Expected: all pass (the r17 judge saw 395 in total). Record the per-file counts and any failure that was already there. Record `bash .claude/skills/onboard-local-model/scripts/gen-challenge.sh --generator-id`; Task 6 compares it.

### Task 1: An A1 local GGUF keeps its entry's settings

**Files:**
- Modify: `.claude/skills/onboard-local-model/references/local-gguf.md` (L1 second bullet, :30-31)
- Create: `.claude/skills/onboard-local-model/evals/files/models-a1-local.yml`, `.claude/skills/onboard-local-model/evals/files/home/Downloads/ornith-1.0-35b-Q4_K_M.gguf`
- Modify: `.claude/skills/onboard-local-model/evals/evals.json` (append one eval)
- Test: `tests/skills/onboard-local-model/test_onboard_local_model_scripts.py` (extend `test_alias_reuse_needs_the_same_size` :1284; new test after `test_eval_gateway_enabled_alias_fixtures_match_their_assertions` :2733)

**Interfaces:**
- Consumes: `resolve-target.sh <alias> --scratch-config FILE` and `new-alias.sh --file NAME --size BYTES --quant TOKEN`, both unchanged.
- Produces: eval id `local-gguf-a1-moe` and fixture config `models-a1-local.yml`. That fixture is `models.yml` with exactly one change: `ornith-35b` gets `size_bytes: 64`.

- [ ] **Step 1: Write the failing tests.** Append to `test_alias_reuse_needs_the_same_size`, after its `l1` line:

```python
    assert all(s in l1 for s in ("On A1, L2 is `bash $SK/scripts/resolve-target.sh <A1 alias> --scratch-config $S`",
                                 "`<alias>` is the A1 alias from then on", "4.2 writes nothing, no L5",
                                 "L4's copy is its G1, never the entry's `url`"))
```

New test:

```python
def test_eval_local_gguf_a1_fixtures_match_their_assertions(tmp_path: Path) -> None:
    base = yaml.safe_load((FIX / "models.yml").read_text())
    next(m for m in base["models"] if m["alias"] == "ornith-35b")["size_bytes"] = len(GGUF_BYTES)
    cfg = FIX / "models-a1-local.yml"
    assert yaml.safe_load(cfg.read_text()) == base
    gguf = FIX / "home" / "Downloads" / "ornith-1.0-35b-Q4_K_M.gguf"
    assert gguf.read_bytes() == GGUF_BYTES and not (FIX / "models" / gguf.name).exists()
    env = {**os.environ, "LLM_ENV_CONFIG": str(cfg), "LLM_ENV_MODELS_DIR": str(FIX / "models")}
    rt = json.loads(_run("bash", SCRIPTS / "resolve-target.sh", gguf, env=env).stdout)
    assert (rt["kind"], rt["size_bytes"], rt["in_models_dir"]) == ("gguf_file", len(GGUF_BYTES), False)
    na = _run("bash", SCRIPTS / "new-alias.sh", "--file", gguf.name, "--size", str(len(GGUF_BYTES)), "--quant", "Q4_K_M", env=env)
    assert json.loads(na.stdout) == {"alias": "ornith-35b", "rule": "A1"}
    s = _out(tmp_path, "a1-scratch.yml")
    assert _run("bash", SCRIPTS / "resolve-target.sh", "ornith-35b", "--scratch-config", s, env=env).returncode == 0
    (entry,) = [m for m in yaml.safe_load(s.read_text())["models"] if m["enabled"]]
    assert (entry["alias"], entry["n_cpu_moe"], entry["ctx_size"], entry["vram_budget"]) == ("ornith-35b", 28, 262144, "85%")
    text = _asserts("local-gguf-a1-moe")
    assert all(t in text for t in ("rule A1", "resolve-target.sh ornith-35b --scratch-config", "n_cpu_moe 28", "ctx_size 262144",
                                   "no entry candidate", "fetch-gguf.sh --from", "make llama-enable MODEL=ornith-35b"))
```

- [ ] **Step 2: Run them.** `bash tmp/r17/pytest-one.sh tests/skills/onboard-local-model/test_onboard_local_model_scripts.py -k "alias_reuse_needs_the_same_size or local_gguf_a1"`. Expected: both FAIL (the L1 text is missing, and the fixture file is missing).

- [ ] **Step 3: Fixtures.**
  - `cp -p .claude/skills/onboard-local-model/evals/files/Foo-9B-Q4_K_M.gguf .claude/skills/onboard-local-model/evals/files/home/Downloads/ornith-1.0-35b-Q4_K_M.gguf`. Foo equals `GGUF_BYTES`, 64 bytes.
  - `yq '(.models[] | select(.alias == "ornith-35b")).size_bytes = 64' .claude/skills/onboard-local-model/evals/files/models.yml > .claude/skills/onboard-local-model/evals/files/models-a1-local.yml`.

- [ ] **Step 4: Edit local-gguf.md L1.** Replace

```
    - A live entry has this `file` but its file is not in the models directory: `new-alias.sh` (A1, with this file's
      byte size) decides now, before any copy.
```

with

```
    - A live entry has this `file` but its file is not in the models directory: `new-alias.sh` (A1, with this file's
      byte size) decides now, before any copy. On A1, L2 is `bash $SK/scripts/resolve-target.sh <A1 alias> --scratch-config $S`
      (that entry alone enabled, with its own `n_cpu_moe`, `ctx_size` and `vram_budget`) and `<alias>` is the A1 alias from
      then on: Steps run as `references/onboard-flow.md`, Alias (4.2 writes nothing, no L5, G3 runs `make llama-enable`),
      L3 fits on the file's own directory and L4's copy is its G1, never the entry's `url`.
```

  onboard-flow.md:141 ("An Alias-row or A1 candidate keeps its own settings") becomes true for this route as it stands; leave it unchanged.

- [ ] **Step 5: Append the eval** to the end of the `evals` array. Use the Edit tool and keep the file's 2-space layout; `jq empty .claude/skills/onboard-local-model/evals/evals.json` must pass.

```json
{
  "id": "local-gguf-a1-moe",
  "prompt": "put \"~/Downloads/ornith-1.0-35b-Q4_K_M.gguf\" on my local llama server if it fits next to laya. just the plan for now, change nothing",
  "setup": "Run resolve-target.sh and new-alias.sh with HOME=.claude/skills/onboard-local-model/evals/files/home, so the quoted path is the file .claude/skills/onboard-local-model/evals/files/home/Downloads/ornith-1.0-35b-Q4_K_M.gguf (64 bytes). The config is .claude/skills/onboard-local-model/evals/files/models-a1-local.yml, whose disabled MoE entry ornith-35b has this file name and size_bytes 64, and its models directory .claude/skills/onboard-local-model/evals/files/models/, which does not hold that file (pass them as LLM_ENV_CONFIG and LLM_ENV_MODELS_DIR). preflight.sh already printed .claude/skills/onboard-local-model/evals/files/preflight-served.json; use that JSON as its output. DRY RUN for evaluation: read-only commands only (resolve-target.sh, new-alias.sh, validate-gguf and budget on a scratch config under ./tmp/, removed at the end, cat, yq, jq); do not copy, move or delete the file, download, edit models.yml, or start or stop a container or benchmark. Answer in English.",
  "files": [
    "evals/files/models-a1-local.yml",
    "evals/files/preflight-served.json",
    "evals/files/home/Downloads/ornith-1.0-35b-Q4_K_M.gguf"
  ],
  "expected_output": "resolve-target.sh gives kind gguf_file (quant Q4_K_M, 64 bytes, not in the models directory). L1: the live entry ornith-35b has this file and the models directory lacks it, so new-alias.sh decides: A1, ornith-35b. The scratch config comes from resolve-target.sh ornith-35b --scratch-config, so the fit uses ornith-35b's own n_cpu_moe 28, ctx_size 262144 and vram_budget 85%, never a candidate copy of gemma4's settings. budget's error for the header-only file is the result, so no copy is offered; after a feasible fit G1 would copy the file with fetch-gguf.sh --from and G3 would run make llama-enable MODEL=ornith-35b.",
  "assertions": [
    "resolve-target.sh, run with HOME at the given home directory, classifies the quoted path as kind gguf_file with quant Q4_K_M, size_bytes 64 and in_models_dir false",
    "L1 finds the live entry ornith-35b for the file name ornith-1.0-35b-Q4_K_M.gguf, which is not in the models directory, and runs new-alias.sh --file ornith-1.0-35b-Q4_K_M.gguf --size 64 --quant Q4_K_M, which prints alias ornith-35b, rule A1",
    "The scratch config is built with resolve-target.sh ornith-35b --scratch-config under ./tmp/, with ornith-35b alone enabled; it has no entry candidate",
    "The fit uses ornith-35b's own settings, n_cpu_moe 28, ctx_size 262144 and vram_budget 85%; step 4.2 writes no settings, and gemma4's ctx_size 131072 and vram_budget 55% are never used for this file",
    "validate-gguf and budget run on that scratch config with --models-dir set to the file's own directory; budget's error for the header-only file (no general.architecture) is reported as the result, with no fit verdict or VRAM figure estimated, and no copy is offered",
    "After a feasible fit, G1 would be a copy, fetch-gguf.sh --from the original path --dest ~/llm-workspace/models/ornith-1.0-35b-Q4_K_M.gguf --size 64, never a download of the entry's url, and G3 would run make llama-enable MODEL=ornith-35b without adding an entry",
    "Nothing was copied, downloaded, stopped, started or edited, and the scratch config is removed at the end"
  ]
}
```

- [ ] **Step 6: Verify.** Run the two Step 2 tests, then the whole scripts test file. Expected: PASS. The `user_turns`, `input_files_exist`, `cite_each_other` and `long_shell_logic` guards included. Then `bash tmp/r17/make-check.sh`. Expected: green.

### Task 2: The router profile is refreshed after every revert

**Files:**
- Modify: `.claude/skills/onboard-local-model/references/onboard-flow.md` (step 9, :255-257)
- Test: `test_onboard_local_model_scripts.py` (`test_a_kept_switch_moves_my_coding_lite_and_a_revert_restores_the_router_profile` :1383, assertion :1390)

- [ ] **Step 1: Write the failing pin.** Replace the assertion at :1390 (`"Then, once 7a.7 ran, ..." in _section(step9, "- After that bullet", "- Hand-back:")`) with:

```python
    after = _section(step9, "- After that bullet", "- Profile:")
    assert "setup-local-llm-agents" not in after and "stops there" not in after
    profile = _section(step9, "- Profile:", "- Hand-back:")
    assert "not kept and 7a.7 ran, whatever the first step-1 read found: `make setup-local-llm-agents`" in profile
```

- [ ] **Step 2: Run it.** `... -k a_kept_switch_moves`. Expected: FAIL, because there is no `- Profile:` bullet yet.

- [ ] **Step 3: Edit step 9.** Replace

```
   - After that bullet (a restore or nothing to restore), when the first step-1 read found llm-server serving: `make start-llama`. A host
     that was gateway-only stops there; `make restart` later applies gateway-only mode to the whole stack. Then, once 7a.7 ran,
     `make setup-local-llm-agents` (since 7a.7 the router profile names `llama-cpp/<alias>`).
```

with

```
   - After that bullet (a restore or nothing to restore), when the first step-1 read found llm-server serving: `make start-llama`. A host
     that was gateway-only starts nothing; `make restart` later applies gateway-only mode to the whole stack.
   - Profile: not kept and 7a.7 ran, whatever the first step-1 read found: `make setup-local-llm-agents` (since 7a.7 the router
     profile names `llama-cpp/<alias>`; the restored file maps only its own enabled entry, or none on a gateway-only host).
```

  The pinned "After that bullet (a restore or nothing to restore), when the first step-1 read found llm-server serving: `make start-llama`" stays word for word.

- [ ] **Step 4: Verify.** The test, then the whole scripts test file, then `bash tmp/r17/make-check.sh`. Expected: PASS and green.

### Task 3: `fetch-gguf.sh`: stall abort, bounded retry, gated stop, `HF_TOKEN`

**Files:**
- Modify: `.claude/skills/onboard-local-model/scripts/fetch-gguf.sh` (header :2-11; validation after :36; the curl call :59-61)
- Test: `test_onboard_local_model_scripts.py` (`FETCH_CURL` :2113, `_fetch` :2122, `test_fetch_gguf_resumes_an_interrupted_download` :2141; new tests after `test_fetch_gguf_rejects_a_non_integer_size` :2229)

**Interfaces:**
- Produces: the `fetch-gguf.sh (--url URL | --from PATH) --dest FILE --size BYTES [--sha256 HEX]` CLI is unchanged. Each attempt runs `curl -fL --speed-limit 1024 --speed-time 120 -w '%{http_code}' [-K -] --continue-at - -o FILE.part URL`. The tail `--continue-at - -o <part> <url>` stays contiguous and last, because an existing test pins it.
- Exit codes: 0 ok; 1 any download or verification failure (messages below); 2 usage, or a malformed `HF_TOKEN`.

- [ ] **Step 1: Write the failing tests.** Replace `FETCH_CURL` and `_fetch`:

```python
FETCH_CURL = r"""out=; p=
for a in "$@"; do [ "$p" = -o ] && out="$a"; p="$a"; done
echo "curl $* part=$(stat -c %s "$out" 2>/dev/null || echo none)" >>"$CALLS"
case " $* " in *" -K - "*) cat >>"$CALLS.stdin" ;; esac
tr '\0' ' ' <"/proc/$PPID/cmdline" >>"$CALLS.parent"; echo >>"$CALLS.parent"
n="$(cat "$CALLS.n" 2>/dev/null || echo 0)"; echo $((n + 1)) >"$CALLS.n"
case "${FETCH_MODE:-full}:$n" in
drop:0) printf 'known' >>"$out"; printf 206; exit 18 ;;
stall:*) printf 206; exit 28 ;;
http503:0|http429:0|http40[134]:*) printf '%s' "${FETCH_MODE#http}"; exit 22 ;;
esac
printf 'known-bytes' | tail -c +$(( $(stat -c %s "$out" 2>/dev/null || echo 0) + 1 )) >>"$out"
printf 200
"""
HF_TOKEN_VALUE = "hf_r17NotARealToken0123456789abcdef"


def _fetch(tmp_path: Path, *, size: int = len(KNOWN), sha: str | None = hashlib.sha256(KNOWN).hexdigest(),
           mode: str = "full", url: str = FETCH_URL, env: dict[str, str] | None = None) -> tuple[subprocess.CompletedProcess[str], Path]:
    stub = tmp_path / "bin"
    stub.mkdir(exist_ok=True)
    for name, body in (("curl", FETCH_CURL), ("sleep", 'echo "sleep $*" >>"$CALLS.sleep"\n')):
        (stub / name).write_text(f"#!/usr/bin/env bash\n{body}")
        (stub / name).chmod(0o755)
    dest = tmp_path / "models" / "m.gguf"
    args = ["--url", url, "--dest", dest, "--size", str(size), *(["--sha256", sha] if sha else [])]
    run_env = {"PATH": f"{stub}:/usr/bin:/bin", "CALLS": str(tmp_path / "calls"), "FETCH_MODE": mode, **(env or {})}
    return _run("bash", SCRIPTS / "fetch-gguf.sh", *args, env=run_env), dest


def _sleeps(tmp_path: Path) -> list[str]:
    path = tmp_path / "calls.sleep"
    return path.read_text().splitlines() if path.exists() else []
```

  Replace the body of `test_fetch_gguf_resumes_an_interrupted_download`. `drop` now recovers inside one run, so this test keeps covering the resume across runs:

```python
    part = tmp_path / "models" / "m.gguf.part"
    part.parent.mkdir()
    part.write_bytes(b"known")  # what a killed or exhausted earlier run leaves
    result, dest = _fetch(tmp_path)
    assert result.returncode == 0, result.stderr
    assert dest.read_bytes() == KNOWN and not part.exists() and len(_calls(tmp_path)) == 1 and "part=5" in _calls(tmp_path)[0]
```

  New tests:

```python
@pytest.mark.parametrize("mode", ["drop", "http503", "http429"])
def test_fetch_gguf_retries_a_transient_failure_from_its_part(tmp_path: Path, mode: str) -> None:
    result, dest = _fetch(tmp_path, mode=mode)
    assert result.returncode == 0, result.stderr
    calls = _calls(tmp_path)
    assert dest.read_bytes() == KNOWN and len(calls) == 2 and _sleeps(tmp_path) == ["sleep 5"]
    assert all("--speed-limit 1024 --speed-time 120" in c for c in calls)
    assert ("part=5" if mode == "drop" else "part=none") in calls[1]


def test_fetch_gguf_gives_up_after_four_stalled_attempts_and_keeps_the_part(tmp_path: Path) -> None:
    part = tmp_path / "models" / "m.gguf.part"
    part.parent.mkdir()
    part.write_bytes(b"known")
    result, dest = _fetch(tmp_path, mode="stall")
    assert result.returncode == 1 and "after 4 attempts" in result.stderr and "re-run the same command to resume" in result.stderr
    assert len(_calls(tmp_path)) == 4 and len(_sleeps(tmp_path)) == 3 and part.read_bytes() == b"known" and not dest.exists()


@pytest.mark.parametrize(("code", "token", "state"), [("401", None, "HF_TOKEN was not set"),
                                                      ("403", HF_TOKEN_VALUE, "HF_TOKEN was sent and refused")])
def test_fetch_gguf_stops_at_once_on_a_gated_repo(tmp_path: Path, code: str, token: str | None, state: str) -> None:
    result, _ = _fetch(tmp_path, mode=f"http{code}", env={"HF_TOKEN": token} if token else None)
    assert result.returncode == 1 and f"HTTP {code}" in result.stderr and "gated or private repo" in result.stderr
    assert state in result.stderr and "to resume" not in result.stderr
    assert len(_calls(tmp_path)) == 1 and _sleeps(tmp_path) == [] and HF_TOKEN_VALUE not in result.stdout + result.stderr


def test_fetch_gguf_stops_at_once_on_another_client_error(tmp_path: Path) -> None:
    result, _ = _fetch(tmp_path, mode="http404")
    assert result.returncode == 1 and "HTTP 404" in result.stderr and "gated" not in result.stderr
    assert len(_calls(tmp_path)) == 1 and _sleeps(tmp_path) == []


def test_fetch_gguf_sends_hf_token_only_to_hugging_face_through_curl_stdin(tmp_path: Path) -> None:
    result, _ = _fetch(tmp_path, env={"HF_TOKEN": HF_TOKEN_VALUE})
    assert result.returncode == 0, result.stderr
    (call,) = _calls(tmp_path)
    assert " -K - " in f" {call} " and HF_TOKEN_VALUE not in call and "--location-trusted" not in call
    assert (tmp_path / "calls.stdin").read_text() == f'header = "Authorization: Bearer {HF_TOKEN_VALUE}"\n'
    assert HF_TOKEN_VALUE not in (tmp_path / "calls.parent").read_text() + result.stdout + result.stderr
    for name, url, env in (("other-host", "https://example.com/m.gguf", {"HF_TOKEN": HF_TOKEN_VALUE}), ("no-token", FETCH_URL, None)):
        root = tmp_path / name
        root.mkdir()
        result, _ = _fetch(root, url=url, env=env)
        assert result.returncode == 0 and "-K" not in _calls(root)[0] and not (root / "calls.stdin").exists(), name


def test_fetch_gguf_refuses_a_malformed_hf_token_before_any_request(tmp_path: Path) -> None:
    result, dest = _fetch(tmp_path, env={"HF_TOKEN": 'hf_x"\nurl = "https://example.com/'})
    assert result.returncode == 2 and "HF_TOKEN" in result.stderr and "hf_x" not in result.stdout + result.stderr
    assert _calls(tmp_path) == [] and not dest.exists()
```

- [ ] **Step 2: Run them.** `... -k fetch_gguf`. Expected: the new tests FAIL (no `--speed-limit`, no retry, no gated message, no `-K -`). The edited existing ones pass.

- [ ] **Step 3: Implement in `fetch-gguf.sh`.**
  - Token check, right after the `--sha256` check (:36): the token applies when `HF_TOKEN` is non-empty and `$url` matches `^https://(huggingface\.co|hf\.co)/`. A `--from` url is `file://`, so it never applies there. When it applies and `HF_TOKEN` does not match `^[A-Za-z0-9_-]+$`, run `echo "HF_TOKEN must hold only [A-Za-z0-9_-] (a Hugging Face access token); its value is not shown" >&2; exit 2`.
  - Replace :59-61 with an attempt loop of at most 4 attempts, each one `curl` call:

```bash
opts=(-fL --speed-limit 1024 --speed-time 120 -w '%{http_code}')
# with the token (never argv, output or a file):
code="$(printf 'header = "Authorization: Bearer %s"\n' "$HF_TOKEN" | curl "${opts[@]}" -K - --continue-at - -o "$part" "$url")"
# without it:
code="$(curl "${opts[@]}" --continue-at - -o "$part" "$url")"
```

    Decide by curl's exit code `rc`:
    - 0: done.
    - 22 with HTTP 401 or 403: `die "HTTP $code: a gated or private repo; accept its licence on its Hugging Face page, set HF_TOKEN to a read token of that account (HF_TOKEN was not set|HF_TOKEN was sent and refused), then re-run the same command"`. Pick "was not set" or "was sent and refused" by whether the token was applied.
    - 22 with 408, 429 or 5xx: retry.
    - 22 with any other HTTP code: `die "download failed: HTTP $code; $part is kept"`.
    - Any other non-zero `rc` (stall 28, partial 18, connection errors): retry.
    - A retry runs `sleep 5` first. After the 4th failed attempt: `die "download failed after 4 attempts (curl exit $rc); $part is kept: re-run the same command to resume"`.
  - Header: in :8-9, "A failed curl keeps the .part for a re-run (exit 1)" becomes "A failed download keeps the .part for a re-run (exit 1)". Before `set -`, add: each attempt aborts below 1024 B/s for 120 s; the retry rules above (4 attempts, 5 s apart); 401/403 stop at once with the gated message; any other HTTP error stops at once. Also add that `HF_TOKEN` (optional) is sent only to `https://huggingface.co/` and `https://hf.co/` urls, as an `Authorization: Bearer` header through curl's stdin config (`-K -`), never in argv, output or a file, and must match `[A-Za-z0-9_-]+` (else exit 2, value not shown). `--help` follows from the header.

- [ ] **Step 4: Verify.** `make validate`, then `... -k "fetch_gguf or quant_headroom_fetch"`, then the whole scripts test file, then `bash tmp/r17/make-check.sh`. Expected: PASS and green. The real-curl `file://` copy test still passes, because a successful attempt is decided by `rc` 0 and `-w` prints `000` there.

### Task 4: `hf-scout.sh` sends `HF_TOKEN` through curl's stdin

**Files:**
- Modify: `.claude/skills/onboard-local-model/scripts/hf-scout.sh` (header :2-19; validation after :53; `hf()` :65-68; the tree `xargs` line :95)
- Test: `test_onboard_local_model_scripts.py` (new helper and two tests after `test_hf_scout_requests_the_revision_and_the_tree_at_its_commit` :3157; that test stays as is)

**Interfaces:**
- Consumes: `HF_TOKEN_VALUE` (Task 3), plus `_repo`, `_lfs_tree` and `REV_SHA` (existing).
- Produces: in network mode, each of hf-scout's curl calls (the model read in `hf()` and every tree read) gets `-K -` with the header line on stdin when `HF_TOKEN` is set, and no `-K` otherwise. The inner `bash -c` of the tree reads takes the token from its inherited environment, never as an argument. With `HF_FIXTURE_DIR` the token is ignored.

- [ ] **Step 1: Write the failing tests.**

```python
def _hf_net(tmp_path: Path, **env: str) -> subprocess.CompletedProcess[str]:
    """hf-scout.sh in network mode against a curl stub that logs argv, stdin (with -K -) and its parent's argv."""
    calls = tmp_path / "calls"
    (tmp_path / "model.json").write_text(json.dumps(_repo("lab/x", 8, 262144, "qwen35") | {"sha": REV_SHA}))
    (tmp_path / "tree.json").write_text(json.dumps(_lfs_tree(("m-Q4_K_M.gguf", 5000, "4" * 64))))
    stub = tmp_path / "bin"
    stub.mkdir()
    (stub / "curl").write_text(f"""#!/usr/bin/env bash
echo "$*" >> {calls}
case " $* " in *" -K - "*) cat >> {calls}.stdin ;; esac
tr '\\0' ' ' < /proc/$PPID/cmdline >> {calls}.parent; echo >> {calls}.parent
url=""; out=""
while [ $# -gt 0 ]; do case "$1" in -o) out="$2"; shift ;; https://*) url="$1" ;; esac; shift; done
case "$url" in
    *"/tree/{REV_SHA}?recursive=true") cat {tmp_path}/tree.json > "$out" ;;
    *"/revision/"*) cat {tmp_path}/model.json ;;
    *) exit 22 ;;
esac
""")
    (stub / "curl").chmod(0o755)
    return _run("bash", SCRIPTS / "hf-scout.sh", "--json", "--budget-mib", "10369", "--ram-mib", "14000", "--repo", "lab/x",
                env={"PATH": f"{stub}:/usr/bin:/bin", "HOME": str(tmp_path), **env})


def test_hf_scout_sends_hf_token_only_through_curl_stdin(tmp_path: Path) -> None:
    result = _hf_net(tmp_path, HF_TOKEN=HF_TOKEN_VALUE)
    assert result.returncode == 0, result.stderr
    calls = (tmp_path / "calls").read_text().splitlines()
    assert len(calls) == 2 and all(" -K - " in f" {c} " and HF_TOKEN_VALUE not in c for c in calls)
    assert (tmp_path / "calls.stdin").read_text() == f'header = "Authorization: Bearer {HF_TOKEN_VALUE}"\n' * 2
    assert HF_TOKEN_VALUE not in (tmp_path / "calls.parent").read_text() + result.stdout + result.stderr
    plain = tmp_path / "plain"
    plain.mkdir()
    result = _hf_net(plain)
    assert result.returncode == 0 and "-K" not in (plain / "calls").read_text() and not (plain / "calls.stdin").exists()


def test_hf_scout_refuses_a_malformed_hf_token_before_any_request(tmp_path: Path) -> None:
    result = _hf_net(tmp_path, HF_TOKEN='hf_x"\nurl = "https://example.com/')
    assert result.returncode == 2 and "HF_TOKEN" in result.stderr and "hf_x" not in result.stdout + result.stderr
    assert not (tmp_path / "calls").exists()
```

- [ ] **Step 2: Run them.** `... -k hf_token`. Expected: both FAIL. With the token, the argv has no `-K -`; the malformed token is not rejected.

- [ ] **Step 3: Implement in `hf-scout.sh`.**
  - After the `--revision` check (:53): if `HF_FIXTURE_DIR` is empty, `HF_TOKEN` is non-empty and it does not match `^[A-Za-z0-9_-]+$`, exit 2 with the same message as `fetch-gguf.sh`.
  - `hf()` network branch: with `HF_TOKEN`, `printf 'header = "Authorization: Bearer %s"\n' "$HF_TOKEN" | curl -fsS --max-time 30 -K - "$@" "$u"`; without it, the current call.
  - The tree reads (:95): the inner `bash -c` script does the same conditional, using `$HF_TOKEN` from its environment. Do not add it to the `xargs`/`bash -c` arguments; the existing `# shellcheck disable=SC2016` stays.
  - Header: one line: "`HF_TOKEN` (optional; network mode only) is sent as an `Authorization: Bearer` header through curl's stdin config (`-K -`), never in argv or output; it must match `[A-Za-z0-9_-]+` (exit 2 otherwise, value not shown)."

- [ ] **Step 4: Verify.** `make validate`, then `... -k hf_scout`, then the whole scripts test file, then `bash tmp/r17/make-check.sh`. Expected: PASS and green.

### Task 5: `SKILL.md`: detached long runs and the tune/quant exception

**Files:**
- Modify: `.claude/skills/onboard-local-model/SKILL.md` (:8, :37, :51-54, :101-102)
- Modify: `.claude/skills/onboard-local-model/evals/evals.json` (append one eval)
- Test: `test_onboard_local_model_scripts.py` (`STATED_ONCE` :1138, `RETIRED_COPIES` :1143, `test_skill_md_routes_near_misses_allows_inline_bench_fit_and_names_the_global_kv_key` :1187)

- [ ] **Step 1: Write the failing pins.**
  - Add `"kills a foreground run at 600 s": "SKILL.md"` to `STATED_ONCE` and `"may run inline"` to `RETIRED_COPIES`.
  - Rename the :1187 test to `test_skill_md_routes_near_misses_runs_long_commands_detached_and_names_the_global_kv_key`. Replace its line `assert "Each `bench-fit.sh` run may run inline (bounded by ..." in rule2 and "single" not in rule2` with:

```python
    assert all(s in rule2 for s in ("Run each `bench-fit.sh` and download (`fetch-gguf.sh`, `quant-headroom.sh fetch`) detached",
                                    "the shell tool kills a foreground run at 600 s", "exits 143 or is lost, once its PID is gone"))
    assert "single" not in rule2
    assert "the model argument wins (except a tune or quant request on the enabled alias)" in text
    assert "the tune row, references/tuning.md, not the re-measure" in _asserts("tune-named-enabled-alias")
```

- [ ] **Step 2: Run it.** `... -k "runs_long_commands_detached or stated_once"`. Expected: both FAIL.

- [ ] **Step 3: Edit `SKILL.md`** with these four replacements, each exact:
  1. :8: `` `SK=.claude/skills/onboard-local-model`; paths are repo-relative; artifacts go under `tmp/`; `` becomes `` `SK=.claude/skills/onboard-local-model`; artifacts go under `tmp/`; ``.
  2. :37: `- When two rows match, the model argument wins and an anchor named with it joins that flow's quality step.` becomes

```
- When two rows match, the model argument wins (except a tune or quant request on the enabled alias) and an anchor named
  with it joins that flow's quality step.
```

  3. :51-54, the whole rule 2 paragraph down to "runs alone on the GPU.", becomes

```
2. **Benchmark runs.** Run each `bench-fit.sh` and download (`fetch-gguf.sh`, `quant-headroom.sh fetch`) detached, log
   under `tmp/`, polled: the shell tool kills a foreground run at 600 s; re-run one that exits 143 or is lost, once its PID is
   gone. Every `tune-sweep.sh`, `effort-run.sh` and `latency-profile.sh` run goes to one subagent with a written brief
   (command, out dir, stop, cleanup, what to return), monitored per AGENTS.md. Each benchmark run writes to its own new
   `--out` under `tmp/` and runs alone on the GPU.
```

  4. :101-102, the NEVER leaderboard line, becomes `- NEVER trust a leaderboard number without its benchmark version, scaffold and effort level: cite them or omit the row.`

  The lines that follow rule 2 ("Benchmark challenges run only on generated challenges ..." and "No other session may edit ...") stay unchanged.

- [ ] **Step 4: Append the eval** to the end of the `evals` array. `jq empty` must pass.

```json
{
  "id": "tune-named-enabled-alias",
  "prompt": "tune gemma4 for the best context and tok/s at depth. Give me the ordered plan with its gates.",
  "setup": "The fixture config is .claude/skills/onboard-local-model/evals/files/models.yml (LLM_ENV_CONFIG; llm-server is serving its enabled gemma4) and its models directory .claude/skills/onboard-local-model/evals/files/models/ (LLM_ENV_MODELS_DIR). preflight.sh already printed .claude/skills/onboard-local-model/evals/files/preflight-served.json; use that JSON as its output. DRY RUN for evaluation: read-only commands only (resolve-target.sh, cat, yq, jq, the skill scripts' --help); do not stop or start containers, edit models.yml or start sweeps, benchmarks or ladders. Answer in English.",
  "files": [
    "evals/files/models.yml",
    "evals/files/models/gemma4-v2-Q4_K_M.gguf",
    "evals/files/preflight-served.json"
  ],
  "expected_output": "gemma4 is the enabled alias, but the request is to tune it: the model-argument rule's exception sends it to the tune row (references/tuning.md), not the re-measure. Preflight first, no G-T1, G-T2 frees the GPU, one subagent runs tune-sweep.sh, G-T3 guards tune-apply.sh.",
  "assertions": [
    "resolve-target.sh gives kind alias, enabled true for gemma4, and the plan follows the tune row, references/tuning.md, not the re-measure of references/served-alias.md",
    "Preflight comes first, and G-T1 (make llama-enable) is not asked: llm_server.enabled is true in the fixture config",
    "G-T2 frees the GPU with make stop-llama and wait-gpu-release.sh while System One stays up",
    "tune-sweep.sh goes to one subagent with a written brief, and tune-apply.sh runs under G-T3 with its backup path stated",
    "No candidate, scratch config, G1 or replacement verdict appears: the model being tuned is the one served",
    "No container was stopped or started and no configuration was edited"
  ]
}
```

- [ ] **Step 5: Verify.** Run `wc -c < .claude/skills/onboard-local-model/SKILL.md`. Expected: 10985, which is <= 11000. If it is over, fix the wording; never raise the cap. Then the Step 2 tests, the whole scripts test file and `bash tmp/r17/make-check.sh`. Expected: PASS, the `router_that_loads`, `only_existing_scripts`, `long_shell_logic` and `user_turns` guards included, and green.

### Task 6: Verification and cleanup

- [ ] **Step 1: Byte cap.** `wc -c < .claude/skills/onboard-local-model/SKILL.md`. Expected: 10985.
- [ ] **Step 2: Guards.** `bash tmp/r17/pytest-one.sh tests/skills/onboard-local-model/test_onboard_local_model_scripts.py -k "jev_as_a_black_box or stated_once or cite_each_other or router_that_loads or only_existing_scripts or long_shell_logic or user_turns or input_files_exist"`. Expected: PASS.
- [ ] **Step 3: Skill suites, one at a time.** Run Task 0's three files in turn. Expected: all pass, with 12 test items more than the baseline: 1 in Task 1, 9 in Task 3 (3 + 1 + 2 + 1 + 1 + 1) and 2 in Task 4. The other test changes are edits. `gen-challenge.sh --generator-id` prints Task 0's id.
- [ ] **Step 4: Repo checks.** `bash tmp/r17/make-check.sh`, with no other pytest running. Expected: "All checks passed." and a green suite.
- [ ] **Step 5: Route walk.** Read the routes as a new agent who has only `SKILL.md`; nothing runs against the host. Check that every branch ends in a step, a gate or a stop:
  1. `~/Downloads/ornith-1.0-35b-Q4_K_M.gguf` with the `ornith-35b` file absent. L1 gives A1, L2 builds `$S` from `ornith-35b`, L3 fits with `n_cpu_moe 28`, the L4 copy is G1, there is no L5, and G3 runs `make llama-enable MODEL=ornith-35b`.
  2. A gateway-only host, G3 yes, then a 6.4 reject. Step 9 runs `make stop-llama` and the restore, starts nothing, then runs the Profile bullet.
  3. An enabled-but-stopped host, then a no to G5. Restore, Profile, then Hand-back `make stop-llama`.
  4. A 15 GB G1 run detached that the tool kills (exit 143). The agent re-runs it once the PID is gone, and the download resumes from its `.part`.
  5. A gated repo: G1 stops on the 401 message, and the report names the licence and `HF_TOKEN`; no loop.
  6. "tune gemma4" on a served host goes to `tuning.md`, and "is gemma4 slower" goes to `served-alias.md`.

  Report any branch without a next step as a finding; do not fix it in this plan.
- [ ] **Step 6: Cleanup.**
  - `rm -rf tmp/r17`.
  - `ls /var/tmp | grep -e pytest-r17 -e make-test-r17` prints nothing.
  - `podman ps -a` shows nothing this plan started (it starts nothing), and `ps` shows no process left by a test.
  - `git status --short` shows no tracked change besides `docs/superpowers/plans/README.md`, with its new row. This plan file stays untracked, like plans G-R.
  - Judges are re-run by the orchestrator, not by this plan.

## Round-1 review outcome (approved: 0 critical, 0 high)

Review round 1 (tmp/plan-s-review/review-round-1.md): the native lane applied Tasks 0-5 to a work copy and found 0 critical, 0 high,
1 medium and 6 low. The referee lane (`my-referee-two`) produced no report: every member was out of quota; it is not retried. Plan
status: approved for execution. The implementers must apply these amendments, which override the task text above where they differ:

- N-M1 (Task 5, medium): the routing exception names the row that wins. SKILL.md :37 reads "- When two rows match, the model
  argument wins (a tune or quant request on the enabled alias takes that row) and an anchor named with it joins that flow's quality
  step." (wrapped as the file wraps). The Task 5 pin becomes `"the model argument wins (a tune or quant request on the enabled alias
  takes that row)" in text` (on the unwrapped text if the clause wraps). `SKILL.md` measures 10,994 bytes (cap 11,000); Task 5
  Step 5 and Task 6 Step 1 expect 10994.
- N-L1 (Task 3, low): `fetch-gguf.sh` sends `HF_TOKEN` only to `https://huggingface.co/` urls, never `hf.co`: an `hf.co` url
  redirects to huggingface.co, another host, and curl drops the header on that hop. The script header (and so `--help`) states
  this. `test_fetch_gguf_sends_hf_token_only_to_hugging_face_through_curl_stdin` adds an `("hf-co", "https://hf.co/lab/x/resolve/main/m.gguf",
  {"HF_TOKEN": HF_TOKEN_VALUE})` case to its loop: no `-K`, no stdin. No skill path builds an `hf.co` download url
  (`resolve-target.sh` and `quant-headroom.sh` build huggingface.co urls).
- N-L2 (Task 3, low): capture curl's exit code safely under `set -euo pipefail`: `rc=0; code="$(... curl ...)" || rc=$?`. Never a
  bare `code="$(...)"` followed by `rc=$?`.
- N-L5 (Task 3, low): curl exit 33 (the server cannot resume a range) stops at once, with no retry: `die "download failed: the
  server cannot resume from $part (curl exit 33); remove it and re-run"`. Stub mode `norange` (`printf 200; exit 33`) and one test,
  `test_fetch_gguf_stops_at_once_when_the_server_cannot_resume`: exit 1, "cannot resume" and "remove it" in stderr, one call, no sleep,
  the `.part` kept. Task 6 Step 3 expects 13 test items more than the baseline (10 in Task 3).
- N-L6 (Task 2 Step 2, low): the red run fails on the first new assertion (`setup-local-llm-agents` is still in the "After that
  bullet" slice), the same root cause as the missing `- Profile:` bullet. Either is a correct red.
- N-L3 and N-L4 (low): not changed. The two soft citations left by the Task 5 cuts (`references/scoring.md:4`,
  `references/selection-criteria.md:50`) still point at something real. `make setup` downloading in tuning T2 needs SKILL.md bytes
  the cap does not leave; Task 6 records it as a residual.
