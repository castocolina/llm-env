# Judge Round 16 Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the confirmed findings of round 16 of the onboard-local-model skill judges, plus plan Q's executor residuals. The native lane gave 87.2 % B (1 high, 5 judge mediums, 6 audit must-fix). The referee gave 88.0 % B; the lead refuted every referee finding except one optional low. The next round should give >= 90 % from both lanes with no critical or high finding.

**Architecture:** Six groups of change, all inside the untracked skill tree and its tests:
- **`my-coding-lite` and the router profile after a model switch (native high D8-1 = audit A-1, D8-2 = A-6, one sibling found while verifying).** A kept onboarding runs `make local-combo` and then `make setup-local-llm-agents`. A reverted one runs `make setup-local-llm-agents`. The tuning route drops its dead "only when Q1 recorded `my-coding-lite`" guard: `make start-llama` already creates or updates the combo.
- **Budget while llm-server serves (audit A-2).** When the compositor renders on the model GPU, `preflight.sh` adds the llama-server holders' VRAM back into `llmenv_budget`. `llmenv budget` counts that VRAM as compositor reserve.
- **Leak guard scope (audit A-3).** `effort-run.sh` guards only the files that generate and score a challenge. Other sessions' edits elsewhere in the repo no longer flag ladder rows.
- **Live config edits (audit A-4).** A new skill script `live-entry.sh` adds an onboarding entry (7a.2) and pins a quant comparison's url (P0). Both go through `edit_config_atomically`: backup, `migrate-config` validation, atomic rename.
- **Local GGUF copy (native D6-1 = audit A-5, D8-3).** `fetch-gguf.sh --from PATH` copies a local file with the download's `.part`, free-disk, resume and size checks. Only step 6.2 takes `LLM_ENV_MODELS_DIR`.
- **Cheap residuals.** onboard-flow.md gets a contents line (part of D5-1). The entry template says where `quantization` comes from. Q1 shows `--quants`. A declined G-Q2 reports Q2's file. The referee's missing tune-route eval is added.

`SKILL.md` gets one edit (Task 3): 10,969 -> 10,959 bytes, measured on a scratch copy (cap 11,000).

**Tech Stack:** bash, jq, yq, curl (a PATH stub in tests, except one `file://` copy test that reads a local file); Python pytest; skill-judge rubric v4.

**Spec:**
- `tmp/judges/r16-native-summary.txt` (judge findings D8-1 to D8-7, D2-1, D5-1, D6-1, D1-1, D3-1, D4-1, D7-1; audit A-1 to A-11, R-1 to R-5, E-1 to E-6).
- `tmp/judges/r16-referee-summary.txt`, which holds the lead's check of every referee finding.
- Base state: plan Q (`docs/superpowers/plans/2026-11-01-judge-round-15-fixes.md`, executed). Its executor's residual lows are in the brief for this plan.

Read-only planning runs on 2026-11-01 (scratch under `tmp/r16-plan/`, removed afterwards):
- Concurrent edits: the 11 files the native judge saw change between 08:32 and 08:46 (SKILL.md, onboard-flow.md, served-alias.md, tuning.md, scoring.md, anchors.md, challenges.md, preflight.sh, bench-fit.sh, evals.json, the test file) are exactly plan Q's file list. Nothing in the skill tree changed after 08:46.
- D8-1, checked in the code:
  - `make start-llama` refreshes the combo through `scripts/start.sh` (it runs `omniroute-local-combo.sh` after provisioning).
  - `llmenv.py` `_ensure_local_combo` skips without a card ("no capacity card; run make model-capacity ...") or without a passed interference gate.
  - `pylib/presets.py` renders only enabled models.
  - So after 7a the combo still names `llama-cpp/<baseline>`, which the router no longer serves. Nothing after 7b refreshes it.
- Sibling defect: `setup-local-llm-agents.sh` maps only enabled models, and `install-agent-clients.sh` replaces the whole `router-env` provider. So 7a.7 drops `llama-cpp/<baseline>` from `~/.pi/agent/models.json`. Step 9's revert never runs `make setup-local-llm-agents`, so the profile keeps naming the candidate.
- D8-2: Q11 and a G-Q3 yes run `make start-llama` on an alias whose card and gate are current. `ensure_local_combo` then creates the combo when it is absent (`pylib/omniroute_combos.py`, POST branch). The Q1 record never changes an outcome.
- A-2: `cmd_budget` sets `compositor_used` to the GPU's whole `vram_used_mib` when the compositor's render node is the model GPU's. `compute_budget` then takes `available = ceiling - max(compositor_used, floor) - system_one_reserve - 1024`. Example: 16304 total, 14000 used (compositor 500, System One 4200, llama-server 9300), floor 1024, reserve 5120. That gives -3840 with llm-server serving and 5460 without it. This host's compositor is on the iGPU today, so the defect is latent.
- A-3: `guard_list` (effort-run.sh:144-151) lists every git-visible file of the repo except `.codegraph/`, `.pi/`, `.superpowers/`, `.playwright-mcp/` and `.claude/*` outside `.claude/skills/`. `SKILL.md`:57 already asks for no other writer.
- A-5/D6-1, `fetch-gguf.sh` with a `file://` url:
  - A space in the path fails ("curl: (3) URL rejected: Malformed input to a URL function").
  - A percent-encoded url copies and resumes a 1,000,000-byte `.part` of a 3,000,000-byte file (sha256 verified).
  - `#`, `?`, `%`, space and `~` round-trip through jq `split("/") | map(@uri) | join("/")` and curl.
- D8-3: `bench-fit.sh`:45 reads the baseline's file from `$MODELS_DIR`. An exported `LLM_ENV_MODELS_DIR=tmp/.../models` makes 6.1 exit 2 ("file missing").

## Global Constraints

- English only. Edit only:
  - `.claude/skills/onboard-local-model/**`;
  - `tests/skills/onboard-local-model/**`;
  - this plan's row in `docs/superpowers/plans/README.md` (already added).
- No tracked code changes, so there is nothing to commit. The skill tree and `tests/skills` stay untracked. If a step turns out to need a tracked file (`tools/lib.sh`, `llmenv.py`, `scripts/`), stop and ask; any approved tracked change goes in its own small commit after `make validate && make test`, with no attribution lines. Earlier README rows stay as they are.
- `SKILL.md` <= 11,000 bytes (`SKILL_MD_MAX_BYTES`, tested). Never raise the cap. Task 3 makes the only `SKILL.md` edit.
- `jev-fast-decision` is a black box, and ai-kit is never named in the skill tree (`test_skill_and_touched_docs_treat_jev_as_a_black_box` scans `scripts/*.sh` too).
- Prose holds no shell logic: a backtick span holds at most two of `;`, `&&`, `||`. Shared rules are stated once (`STATED_ONCE`), and retired wording stays out (`RETIRED_COPIES`). Changing a pinned phrase means changing its pin in the same task. Before replacing a sentence, grep the tests and `evals/evals.json` for it.
- A reference names another reference only by path, and only for the pairs in `CITES`. This plan adds no pair.
- Every script keeps `--help` (exit 0; first line `<name>.sh — ...`) and its tests. A new script joins `SCRIPT_NAMES`. A behaviour change is red-first: write the test, watch it fail, then implement. Doc pins are written first too, and fail until the text lands.
- After a `.sh` edit run `make validate`. After a `.py` edit (test files included) run `make validate && make test`.
- pytest: one file per run, never two runs at once, through `tmp/r16/pytest-one.sh` (Task 0). It puts `--basetemp` and `TMPDIR` in two fresh `mktemp -d /var/tmp/...` dirs and deletes both afterwards. Never put either under `./tmp/`. After each task run the whole `test_onboard_local_model_scripts.py`: many tests pin prose.
- Scratch lives under `./tmp/r16/`; Task 7 removes it. Not allowed: containers, GPU, network, downloads, and any edit to the live `models.yml` or a capacity card. Every `curl` and `podman` in a new test is a PATH stub. The one exception is Task 5's real `curl` on a `file://` url of a test file. Hugging Face answers come from `HF_FIXTURE_DIR`.
- Eval `prompt` is the user's message alone (`EVAL_SETUP` must not match it). `setup` is present exactly when `files` is, and every listed file's path appears in `prompt` or `setup`.
- Line numbers cite the files as of 2026-11-01 09:00, after plan Q. Later tasks find text by the quoted anchor, not by number.

## Findings ledger (verified 2026-11-01 against the files)

| Finding | Verdict | Task |
|---|---|---|
| native D8-1 (high) = audit A-1: a kept onboarding leaves `my-coding-lite` on the disabled baseline | confirmed (Spec runs); no test or eval covers it | 1 |
| sibling (found while verifying D8-1): a reverted onboarding leaves the router profile on the candidate | confirmed (Spec runs) | 1 |
| native D8-2 = audit A-6, E-1: the Q1 `my-coding-lite` guard is dead | confirmed: tuning.md:163-164, :191-192, :203-204; eval `quant-head-to-head` even asserts "on a host without it the combo is not created", which is false | 1 |
| audit A-2: preflight's `llmenv_budget` while llm-server holds VRAM | confirmed in the code (Spec), latent on this host | 2 |
| audit A-3: the leak guard watches the whole repo | confirmed: effort-run.sh:144-151. `SKILL.md`:57 already states the no-other-writer rule, so the defect is the scope, not the documentation | 3 |
| audit A-4 = R-2: raw `yq -i` on the live file at 7a.2 and P0 | confirmed: onboard-flow.md:196-197, tuning.md:142-143. `edit_config_atomically` (tools/lib.sh:300) is already used by tune-apply.sh, quant-headroom.sh, llama-config.sh and start.sh. No llmenv subcommand adds an entry | 4 |
| native D6-1 = audit A-5 = R-4: the local copy has no command | confirmed: local-gguf.md:41-45 | 5 |
| native D8-3: `LLM_ENV_MODELS_DIR` for all of step 6 breaks 6.1 | confirmed (Spec) | 5 |
| native D5-1: two-deep chains, no index in onboard-flow.md | the chains are route-row loads (`SKILL.md` loads scoring.md and fit-math.md for onboard rows; selection-criteria.md section 4 goes to a subagent), all `CITES` pairs: not a defect. The contents line is cheap. Inlining the Serving rule needs `SKILL.md` bytes: dropped | 6 |
| native D2-1: interleaved run orders | dropped: a restructure under prose pins (`test_onboarding_and_tuning_branch_on_a_host_without_gpu_config` pins the No-device order), as in plan Q | - |
| native D7-1: no eval of a kept switch; sentence-pinning asserts | the keep eval is Task 1; the prose-snapshot rewrite is excluded by the brief | 1 |
| plan Q residuals: template `quantization`, Q1 `--quants`, a declined G-Q2 | confirmed: onboard-flow.md:263 has no source comment; tuning.md:164 shows no `--quants`; Q3 (:177-178) has no declined branch, so `SKILL.md`'s "a no skips that step" leaves Q4's `make llama-enable MODEL=<b>` without an entry | 6 |
| referee optional low: the tune route with llm-server enabled and an empty `gpu.pci_address` has no eval | confirmed (`gateway-only-host` covers `llm_server.enabled` false) | 6 |
| native lows D8-4, D8-5, D8-6, D8-7, D1-1, D4-1 | dropped: lows outside this round's brief (D4-1 would also drop the pinned "got slower") | - |
| native D3-1 (gotcha: `make start-llama` provisions the combo) | dropped from `SKILL.md` (no bytes); Task 1 states the fact in tuning.md | 1 |
| audit A-7 to A-11, R-1, R-3, E-2 to E-4, E-6 | not planned: outside the brief, or lib/prose-snapshot refactors the brief excludes | - |
| audit E-5 (the bench-fit gotcha repeats Freeing the GPU) | applied in Task 3 to pay for rule 2's longer sentence | 3 |
| referee judge findings D8-1, D8-2, D8-3, D5-1, D6-1, D7-1 and must-fix M1, M2 | refuted by the lead (r16 referee summary) | - |
| referee lows D5-2, D6-2, D1-1, D1-2, D3-1, D7-2 | dropped: not verified by the lead and not in the brief | - |

## Not planned (out of scope, with the reason)

- **Prose-snapshot test layer rewrite.** Per the brief. Pins this plan touches are updated in place.
- **`llmenv sample` and `common.sh` refactors, the unused event-pipeline modules.** Per the brief.
- **Anything that needs `SKILL.md` bytes** beyond Task 3's net -10. No reference text moves into `SKILL.md`.
- **Changing what `make start-llama` does to the combo** (tracked `scripts/start.sh`). The skill documents the behaviour instead.

## Owner decisions (defaults chosen)

1. **Keep moves `my-coding-lite`.** A yes to G5 points the combo at the kept model (`make local-combo`). Alternative: leave the combo and only report it. Then the combo fails until the next `make start-llama`.
2. **A revert refreshes the router profile.** `make setup-local-llm-agents` runs once 7a.7 has run, whatever llm-server's state.
3. **Quant comparison combo.** The skill relies on `make start-llama`, which creates `my-coding-lite` for the served alias even on a host that never had it. That is repo behaviour; changing it is a tracked change.
4. **Preflight add-back.** Add back `min(llama-server holders' VRAM, reserve_mib - gpu.reserve_floor_mib)`, recompute the VRAM verdict and record `llama_server_added_back_mib`. Alternative: emit `llmenv_budget: null`. Rule 1 would then fall back to `model_vram_budget_mib`, which ignores the compositor.
5. **Leak guard scope.** Guard what generates and scores a challenge: `.claude/skills/onboard-local-model/scripts/`, `.claude/skills/onboard-local-model/challenges/` and `tools/lib.sh` (gen-challenge.sh sources it). An agent's write elsewhere in the repo is no longer detected, like a write under `$HOME` today. Alternative: keep the repo-wide guard and rely on rule 2.
6. **`live-entry.sh`** is a skill script over the tracked `edit_config_atomically`, with no tracked change. Its backups (`.pre-entry-` and `.pre-url-`) come on top of 7a.1's rollback copy, which step 9 still restores.
7. **`fetch-gguf.sh --from`** checks the copy's size only. Hashing the source would read a multi-GB file twice. An optional `--sha256` still works.
8. **`SKILL.md`.** Rule 2's last sentence names the guarded paths. The bench-fit gotcha drops "exits 3 while llm-server runs, and", which Freeing the GPU already states.

## Review Focus

1. Keep when `make local-combo` prints `skipped` (a stale card or gate). Expected: the reason is reported and nothing else changes. Pinned in Task 1 ("a `skipped` line: report its reason").
2. A compositor on the model GPU while llm-server is stopped (no llama-server holder). Expected: `llmenv_budget` passes through unchanged. Pinned in Task 2 (case `llm-server-stopped`).
3. A ladder during which another session edits the skill's references, `SKILL.md`, tests or a repo file. Expected: no row flagged. An edit under `challenges/`, `scripts/` or `tools/lib.sh` still flags. Pinned in Task 3.
4. A P0 pin to a branch url (`resolve/main/`) or to another file name. Expected: exit 2 and the live file untouched. Pinned in Task 4.
5. A local GGUF whose path holds a space, `#` or `%`, copied after an interrupted first copy. Expected: the copy resumes, equals the original, and the original is untouched. Pinned in Task 5.

---

### Task 0: Scratch pytest runner

**Files:**
- Create: `tmp/r16/pytest-one.sh` (scratch; Task 7 deletes `tmp/r16/`)

- [ ] **Step 1: Write the runner.** Plan Q's Task 0 script, with `r15` replaced by `r16` in the paths and the `mktemp` templates (`/var/tmp/pytest-r16.XXXXXX`, `/var/tmp/pytest-r16-tmpdir.XXXXXX`).
- [ ] **Step 2: Baseline.** Run `bash tmp/r16/pytest-one.sh tests/skills/onboard-local-model/test_onboard_local_model_scripts.py`, then `test_challenges.py`, then `test_latency_profile.py`, one after another. Expected: all pass (the r16 judge saw 380 in total); record the exact count and any pre-existing failure. Record `bash .claude/skills/onboard-local-model/scripts/gen-challenge.sh --generator-id`; Task 7 compares it. Expected change: none, because `references/`, `scripts/` and `evals/` are outside the hashed library tree.

### Task 1: `my-coding-lite` and the router profile after a switch

**Files:**
- Modify: `.claude/skills/onboard-local-model/references/onboard-flow.md` (step 9 Keep :241-243; the "After that bullet" :248-249)
- Modify: `.claude/skills/onboard-local-model/references/tuning.md` (Q1 :163-164; Q11 :191-192; the `my-coding-lite` bullet :197; G-Q3 yes :203-205)
- Modify: `.claude/skills/onboard-local-model/evals/evals.json` (`quant-head-to-head` expected_output and its Q11 assertion; `quant-switch-yes` setup, expected_output, assertions[2] and [3]; new eval `onboard-keep` after `onboard-baseline-server-stopped`)
- Test: `test_onboard_local_model_scripts.py` (two new tests after `test_one_rule_serves_a_stopped_llm_server_and_each_route_hands_it_back` :1281; the pin in `test_eval_quant_switch_fixtures_match_their_assertions` :2813; `RETIRED_COPIES` :1096)

- [ ] **Step 1: Write the failing tests.**

```python
def test_a_kept_switch_moves_my_coding_lite_and_a_revert_restores_the_router_profile() -> None:
    assert 'bash "${REPO_DIR}/scripts/omniroute-local-combo.sh"' in (ROOT / "scripts" / "start.sh").read_text()  # start-llama ensures it
    assert '_local_combo_skip("interference gate not passed; run make model-interference")' in (ROOT / "llmenv.py").read_text()
    step9 = _section(_md(REFS / "onboard-flow.md"), "9. **Keep or revert", "## Entry template")
    keep = _section(step9, "- Keep:", "- A declined G1")
    assert keep.index("`make local-combo`") < keep.index("`make setup-local-llm-agents`")
    assert "7a.6's `make start-llama` skipped `my-coding-lite`" in keep and "a `skipped` line: report its reason" in keep
    assert "Then, once 7a.7 ran, `make setup-local-llm-agents`" in _section(step9, "- After that bullet", "- Hand-back:")
    tuning = _md(REFS / "tuning.md")
    assert "`make start-llama` creates or updates `my-coding-lite` for the served alias" in tuning
    assert "make local-combo" not in _section(tuning, "- Q11.", "- A failed Q4-Q6") + _section(tuning, "- G-Q3 yes:", "- Hand-back,")
    evals = (SKILL / "evals" / "evals.json").read_text()
    assert "Q1 recorded" not in evals and "the Q1 record" not in evals and "the combo is not created" not in evals


def test_eval_onboard_keep_fixtures_match_their_assertions() -> None:
    pre = json.loads((FIX / "preflight-served.json").read_text())
    assert pre["services"]["llm_server_running"] is True and pre["runtime"]["enabled_models"] == ["gemma4"]
    text = _asserts("onboard-keep")
    assert text.index("make local-combo") < text.index("make setup-local-llm-agents")
    assert all(s in text for s in ("llama-cpp/gemma4", "llama-cpp/foo-9b", "no capacity card", "no make stop-llama", "models.yml.example"))
```

  - At :2813 replace `"make local-combo"` with `"moves my-coding-lite to gemma4-q5_k_m"` and `"no separate make local-combo"`.
  - `RETIRED_COPIES` gains "record whether `my-coding-lite` exists", "only when Q1 recorded" and "`my-coding-lite`, when it exists, is unavailable".

- [ ] **Step 2: Run them.** `bash tmp/r16/pytest-one.sh tests/skills/onboard-local-model/test_onboard_local_model_scripts.py -k "kept_switch or onboard_keep or quant_switch_fixtures or stated_once"`. Expected: FAIL. The start.sh and llmenv.py assertions already pass, which shows the code the text must describe.

- [ ] **Step 3: Edit onboard-flow.md step 9.**
  - The Keep bullet becomes: "Keep: `make local-combo`, then `make setup-local-llm-agents`. 7a.6's `make start-llama` skipped `my-coding-lite` (the candidate had no card yet), so it still names `llama-cpp/<baseline>`, which the router no longer serves. 7b's card and interference gate let it move to `llama-cpp/<alias>` now (a `skipped` line: report its reason). Add the entry to `models.yml.example` with `enabled: false` only when the owner wants a repo default." Then the existing gateway-only `make restart` sentence, unchanged.
  - Append to the "After that bullet ..." bullet: "Then, once 7a.7 ran, `make setup-local-llm-agents` (since 7a.7 the router profile names `llama-cpp/<alias>`)." The pinned first sentence stays.

- [ ] **Step 4: Edit tuning.md.**
  - Q1: delete the first sentence ("First record whether ... = absent)."); "Then `bash ...`" becomes "`bash ...`".
  - Q11: "Restore: `make stop-llama`, `make llama-enable MODEL=<a>`, `make start-llama`, `make setup-local-llm-agents`."
  - The `my-coding-lite` bullet becomes: "`make start-llama` creates or updates `my-coding-lite` for the served alias once that alias has a current card and a passed interference gate (`scripts/start.sh`), so Q11 and a G-Q3 yes need no `make local-combo`. While a candidate without a card is served (Q6-Q9), `my-coding-lite` still names `<a>` and fails; Q11 brings it back."
  - G-Q3 yes: delete ", then `make local-combo` only when Q1 recorded `my-coding-lite`".

- [ ] **Step 5: Edit evals.json.**
  - `quant-head-to-head`:
    - expected_output: replace "Q11 restores gemma4 and builds my-coding-lite only if it existed before." with "Q11 restores gemma4, and its make start-llama creates or updates my-coding-lite for gemma4."
    - The Q11 assertion: "Q11 restores the original alias (make stop-llama, make llama-enable MODEL=gemma4, make start-llama, make setup-local-llm-agents); its make start-llama creates or updates my-coding-lite for gemma4 once gemma4's card and interference gate are current, so no separate make local-combo runs".
  - `quant-switch-yes`:
    - setup: drop "Q1 recorded that my-coding-lite exists; ".
    - expected_output: "...; the yes branch serves gemma4-q5_k_m (its make start-llama moves my-coding-lite to it), refreshes the router profile, and keeps gemma4 disabled as the rollback."
    - assertions[2]: "The yes branch, in order: make stop-llama, make llama-enable MODEL=gemma4-q5_k_m, make start-llama (it moves my-coding-lite to gemma4-q5_k_m: its card and Q7 gate are current), make check-server, make setup-local-llm-agents; no separate make local-combo".
    - assertions[3]: "gemma4 stays in models.yml, disabled, as the rollback, and all of Q11 restores it: make stop-llama, make llama-enable MODEL=gemma4, make start-llama, make setup-local-llm-agents".
  - New eval `onboard-keep`:
    - prompt: "foo-9b beat gemma4 in today's onboarding and I approve G5: keep foo-9b. What runs now, in order?"
    - files: `["evals/files/models.yml", "evals/files/preflight-served.json"]`.
    - setup: "Premise: steps 1-8 of an onboarding of foo-9b (a Hugging Face candidate) ran on this host with the fixture config .claude/skills/onboard-local-model/evals/files/models.yml. The first step-1 read was .claude/skills/onboard-local-model/evals/files/preflight-served.json (llm-server serving gemma4). 7a added foo-9b, ran make llama-enable MODEL=foo-9b and make setup, and its make start-llama printed `my-coding-lite: skipped (no capacity card; run make model-capacity MODEL=foo-9b and make model-interference)`. 7a.7 ran make setup-local-llm-agents. 7b's interference gate passed and make model-capacity wrote foo-9b's card. DRY RUN for evaluation: read-only commands only (cat, yq, jq); list the commands you would run, in order, without running them. Answer in English."
    - expected_output: "Keep: make local-combo moves my-coding-lite from llama-cpp/gemma4 (no longer served) to llama-cpp/foo-9b now that foo-9b has a card and a passed interference gate, then make setup-local-llm-agents refreshes the router profile; nothing is rolled back and llm-server keeps serving foo-9b."
    - assertions:
      - "Keep runs make local-combo, then make setup-local-llm-agents"
      - "The answer says why: 7a.6's make start-llama skipped my-coding-lite (no capacity card for foo-9b yet), so my-coding-lite still routes to llama-cpp/gemma4, which the router no longer serves once foo-9b is the enabled model; 7b's card and gate let it move to llama-cpp/foo-9b"
      - "Nothing is rolled back and no make stop-llama runs: the first step-1 read (preflight-served.json) found llm-server serving"
      - "models.yml.example gets the foo-9b entry with enabled false only when the owner wants a repo default"
      - "Nothing was executed"

- [ ] **Step 6: Verify.** `make validate`, the Step 2 selection, then the whole scripts test file. Expected: PASS, including `test_eval_prompts_read_as_user_turns` and `test_eval_input_files_exist_and_the_gguf_fixture_resolves`.

### Task 2: The preflight budget while llm-server serves on the compositor's GPU

**Files:**
- Modify: `.claude/skills/onboard-local-model/scripts/preflight.sh` (header :2-4; the budget block :21-28)
- Test: `test_onboard_local_model_scripts.py` (`_PREFLIGHT_STUB_UV` :3167; one new test after `test_preflight_passes_vram_feasible_through` :3238)

**Interfaces:**
- Produces: `budget.llmenv_budget` gains `llama_server_added_back_mib` (int, 0 when nothing is added back). When it is above 0, `vram_available_mib`, `vram_shortfall_mib`, `vram_feasible`, `feasible` and `remedies` are recomputed. Every other case passes llmenv's fields through unchanged.

- [ ] **Step 1: Write the failing test.** In `_PREFLIGHT_STUB_UV`, the `processes-on-render-node` branch prints `$PROCESSES_JSON` when it is set, else `{"processes": []}`.

```python
def _verdict(available: int, reserve: int, compositor: bool) -> str:
    return json.dumps({"feasible": False, "vram_feasible": False, "available_mib": available, "required_mib": 5000,
                       "shortfall_mib": max(0, 5000 - available), "reserve_mib": reserve, "compositor_on_this_gpu": compositor,
                       "ram_available_mib": 9000, "ram_feasible": True, "ram_required_mib": 0, "remedies": ["move the compositor"]})


LLAMA_HOLDERS = [{"pid": 2, "comm": "llama-server", "vram_mib": 12}, {"pid": 3, "comm": "llama-server", "vram_mib": 9288}]


@pytest.mark.parametrize(("verdict", "procs", "expected"), [
    (_verdict(-3840, 14000, True), LLAMA_HOLDERS, (9300, 5460, 0, True, True, [])),
    (_verdict(-3840, 9800, True), LLAMA_HOLDERS, (8776, 4936, 64, False, False, ["move the compositor"])),  # capped at reserve - floor
    (_verdict(-3840, 14000, True), [{"pid": 4, "comm": "kwin_wayland", "vram_mib": 500}], (0, -3840, 8840, False, False, ["move the compositor"])),
    (_verdict(1000, 1024, False), LLAMA_HOLDERS, (0, 1000, 4000, False, False, ["move the compositor"])),
], ids=["compositor-gpu", "floor-cap", "llm-server-stopped", "compositor-elsewhere"])
def test_preflight_adds_back_llama_server_vram_on_the_compositor_gpu(
    tmp_path: Path, verdict: str, procs: list[dict[str, object]], expected: tuple[object, ...]
) -> None:
    env = _preflight_env(tmp_path, verdict, gpu={"reserve_floor_mib": 1024}) | {"PROCESSES_JSON": json.dumps({"processes": procs})}
    result = _run("bash", SCRIPTS / "preflight.sh", env=env)
    assert result.returncode == 0, result.stderr
    b = json.loads(result.stdout)["budget"]["llmenv_budget"]
    got = (b["llama_server_added_back_mib"], b["vram_available_mib"], b["vram_shortfall_mib"], b["vram_feasible"], b["feasible"], b["remedies"])
    assert got == expected
```

- [ ] **Step 2: Run it.** Expected: FAIL (KeyError `llama_server_added_back_mib`).

- [ ] **Step 3: Implement in `preflight.sh`.**
  - After `holders=`, compute `llama_mib`, the sum of `vram_mib` over holders whose `comm` is `llama-server`. Read `floor` with `yq -r '.gpu.reserve_floor_mib // 0'`.
  - The `repo_budget` jq takes `--argjson l --argjson floor`. It computes `add = min(l, reserve_mib - floor)`, clamped at 0, only when `.compositor_on_this_gpu == true` and `l > 0`; otherwise `add = 0`.
  - With `add > 0`:
    - `vram_available_mib = available_mib + add`;
    - `vram_shortfall_mib = max(0, required_mib - that)`;
    - `vram_feasible = (shortfall == 0)`;
    - `feasible = vram_feasible and ram_feasible`, as `llmenv.py` `cmd_budget` combines them;
    - `remedies = []` when the new `feasible` is true, else llmenv's list.
  - With `add == 0`: today's mapping, unchanged.
  - Both add `llama_server_added_back_mib: add`.
  - Header, a sentence after "the budgets a candidate model must fit": "While llm-server serves on the GPU the compositor renders on, `llmenv budget` counts the llama-server holders' VRAM as compositor reserve. `llmenv_budget` adds it back (at most `reserve_mib` minus `gpu.reserve_floor_mib`), recomputes the VRAM verdict and records `llama_server_added_back_mib`, so it gives what a fresh load would get."

- [ ] **Step 4: Verify.** `make validate`, the new test, then the whole scripts test file. Expected: PASS. The existing preflight tests and the eval fixture tests still pass, because the fixtures are static JSON.

### Task 3: The leak guard covers what generates and scores a challenge

**Files:**
- Modify: `.claude/skills/onboard-local-model/scripts/effort-run.sh` (header :26-33; `guard_list` :143-151)
- Modify: `.claude/skills/onboard-local-model/references/challenges.md` (Anti-contamination, :119-130)
- Modify: `.claude/skills/onboard-local-model/SKILL.md` (rule 2 :57; Gotchas :119)
- Test: `test_onboard_local_model_scripts.py` (`AGENT_MISBEHAVIOUR` :451; `_guard_root` :495; `test_effort_run_flags_writes_outside_the_run` :703; `test_effort_run_ignores_a_tracked_file_deleted_before_the_run` :722; one new test after :722; `RETIRED_COPIES`)

**Interfaces:**
- Produces: guarded paths, relative to the guard root, are `.claude/skills/onboard-local-model/scripts`, `.claude/skills/onboard-local-model/challenges` and `tools/lib.sh`, held once in an array `guard_paths`. `outside_writes` keeps its shape (sorted, at most 20).

- [ ] **Step 1: Write the failing test and update the fixtures.**
  - `_guard_root` also creates `.claude/skills/onboard-local-model/scripts/guarded.sh` (content `g\n`), idempotently.
  - `AGENT_MISBEHAVIOUR`:
    - The `OUTSIDE` branch writes `.claude/skills/onboard-local-model/challenges/planted.js`, `.claude/skills/onboard-local-model/scripts/planted.sh` and `tools/lib.sh`, and removes `.claude/skills/onboard-local-model/scripts/guarded.sh`.
    - A new `CONCURRENT` branch writes `owner-file.txt`, `tests/skills/x.py`, `.claude/skills/onboard-local-model/SKILL.md` and `.claude/skills/onboard-local-model/references/x.md`, and removes `owner.txt`.
    - Both branches write each file after `mkdir -p` of its directory under `$ONBOARD_GUARD_ROOT`, as the `BENIGN` loop does.
  - `test_effort_run_flags_writes_outside_the_run` expects `[".claude/skills/onboard-local-model/challenges/planted.js", ".claude/skills/onboard-local-model/scripts/guarded.sh", ".claude/skills/onboard-local-model/scripts/planted.sh", "tools/lib.sh"]`.
  - `test_effort_run_ignores_a_tracked_file_deleted_before_the_run` commits and deletes `.claude/skills/onboard-local-model/scripts/tracked.sh` instead of `tracked.txt`, so it still tests a guarded path.

```python
@NEEDS_NODE
def test_effort_run_ignores_concurrent_edits_outside_the_generator_and_scorer(tmp_path: Path) -> None:
    seeds = _seeds_file(tmp_path, {"1": [7]})
    result = _effort_run(tmp_path, "--layers", "1", "--seeds", str(seeds), env={"CONCURRENT": "1"})
    assert result.returncode == 0, result.stderr
    (row,) = _rows(tmp_path)
    assert row["outside_writes"] == [] and row["leak_suspect"] is False
```

  - `RETIRED_COPIES` gains "No other session may write the repo" and "A live benchmark needs no other writer on the repo".

- [ ] **Step 2: Run them.** `... -k "effort_run or stated_once"`. Expected: FAIL. The new test lists `owner-file.txt` and the others. The outside-writes test lacks the new paths (the old list held `owner-file.txt`).

- [ ] **Step 3: Implement.**
  - `guard_list`: `git -C "$guard_root" ls-files -z --cached --others --exclude-standard -- "${guard_paths[@]}"`. Drop the `case` exclusion line; the existence check stays. Define `guard_paths` beside the function with a one-line comment: "what generates and scores a challenge (gen-challenge.sh sources tools/lib.sh); other writers elsewhere never flag a row".
  - Header :29-31: "Guarded files are the git-visible files (tracked plus untracked not ignored) under .claude/skills/onboard-local-model/scripts/ and challenges/ and tools/lib.sh: what generates and scores a challenge." Add to "Not detected": "writes elsewhere in the guard root (another session's edits there never flag a row)". The last sentence becomes "An edit of a guarded path by another session also flags a row, so ...".
  - challenges.md: the same guarded-path sentence replaces "Guarded files are ... except `.claude/skills/`". Add the not-detected clause. The no-other-writer bullet becomes: "A live benchmark needs no other session editing the guarded paths: such an edit flags the row. ..." The re-run sentence stays.
  - SKILL.md rule 2, last sentence: "No other session may edit `$SK/scripts`, `$SK/challenges` or `tools/lib.sh` during a ladder run: it flags rows `leak_suspect`."
  - SKILL.md gotcha: delete "exits 3 while llm-server runs, and " from "- `bench-fit.sh` exits 3 while llm-server runs, and mounts the models directory ...". Keep the existing line break after "never point it at": the measured 10,959 assumes it.

- [ ] **Step 4: Verify.** `wc -c < .claude/skills/onboard-local-model/SKILL.md` prints 10959. Then `make validate`, the Step 2 selection, then the whole scripts test file. Expected: PASS (`BENIGN` still unflagged).

### Task 4: `live-entry.sh` for the gated live config edits

**Files:**
- Create: `.claude/skills/onboard-local-model/scripts/live-entry.sh`
- Modify: `.claude/skills/onboard-local-model/references/onboard-flow.md` (7a.2, third bullet :196-197)
- Modify: `.claude/skills/onboard-local-model/references/local-gguf.md` (L5 :46-49)
- Modify: `.claude/skills/onboard-local-model/references/tuning.md` (P0 :142-143)
- Test: `test_onboard_local_model_scripts.py` (`SCRIPT_NAMES` :30-32 gains `"live-entry"`; three new tests after `test_tune_apply_refuses_a_throughput_row` :1709)

**Interfaces:**
- Produces:
  - `live-entry.sh add --from SCRATCH --alias A [--as LIVE]` appends entry `A` of `SCRATCH` to the live config (`CONFIG_PATH`) as `LIVE` (default `A`), with `enabled: false` and no `benchmark`. It prints `backup: <path>`, then `added: LIVE`.
  - `live-entry.sh set-url --alias A --url URL` sets live entry `A`'s url and prints `backup: <path>`, then `url: A`.
  - Both use `edit_config_atomically pre-entry ...` / `pre-url ...`.
  - Exit 1, live file untouched, with these messages:
    - no entry `A` in `SCRATCH`: `no entry <A> in <SCRATCH>`;
    - `LIVE` already a live alias: `alias <LIVE> is already in <config>`;
    - `A`'s `file` already the file of a live entry: `<file> is already the file of <alias>; use that alias`;
    - `set-url` on an alias that is not live: `no live entry <A>`;
    - a failed `migrate-config`: its own error (its backup stays, like tune-apply's).
  - Exit 2: usage; `LIVE` = `candidate` (`the live alias cannot be candidate`); a url that is not `^https://huggingface\.co/[^/]+/[^/]+/resolve/[0-9a-f]{40}/.+`, or whose base name differs from `A`'s `file`.
  - `set-url` checks in this order: the url's form (exit 2), then that `A` is live (exit 1), then the base name (exit 2).

- [ ] **Step 1: Write the failing tests.**

```python
LIVE_CANDIDATE = dict(QH_CONFIG["models"][0], alias="candidate", label="Foo 9B", file="Foo-9B-Q4_K_M.gguf",  # type: ignore[index]
                      url="file:///home/u/Downloads/Foo-9B-Q4_K_M.gguf", size_bytes=5000000000)


def _live(root: Path, *args: str, scratch: list[dict[str, object]] | None = None) -> tuple[subprocess.CompletedProcess[str], Path, str]:
    root.mkdir(parents=True, exist_ok=True)
    cfg = root / "models.yml"
    cfg.write_text(yaml.safe_dump(json.loads(json.dumps(QH_CONFIG))))
    s = root / "scratch.yml"
    s.write_text(yaml.safe_dump(json.loads(json.dumps(QH_CONFIG | {"models": scratch or [LIVE_CANDIDATE]}))))
    tools = {Path(shutil.which(t) or "/usr/bin/" + t).parent for t in ("uv", "yq", "jq")}
    env = {"PATH": f"/usr/bin:/bin:{':'.join(map(str, tools))}", "HOME": str(root), "LLM_ENV_CONFIG": str(cfg)}
    original = cfg.read_text()
    argv = [str(s) if a == "<S>" else a for a in args]
    return _run("bash", SCRIPTS / "live-entry.sh", *argv, env=env), cfg, original


def test_live_entry_add_copies_the_scratch_entry_disabled_after_a_backup(tmp_path: Path) -> None:
    result, cfg, original = _live(tmp_path, "add", "--from", "<S>", "--alias", "candidate", "--as", "foo-9b")
    assert result.returncode == 0, result.stderr
    backup = Path(result.stdout.splitlines()[0].removeprefix("backup: "))
    assert ".pre-entry-" in backup.name and backup.read_text() == original and backup.stat().st_mode & 0o777 == 0o600
    m, new = yaml.safe_load(cfg.read_text())["models"]
    assert (m["alias"], m["enabled"]) == ("m", True)
    assert (new["alias"], new["enabled"], "benchmark" in new) == ("foo-9b", False, False)
    assert (new["file"], new["url"], new["size_bytes"]) == ("Foo-9B-Q4_K_M.gguf", LIVE_CANDIDATE["url"], 5000000000)
    assert result.stdout.splitlines()[-1] == "added: foo-9b" and cfg.stat().st_mode & 0o777 == 0o600


def test_live_entry_add_refuses_and_leaves_the_live_file(tmp_path: Path) -> None:
    cases = [
        (("add", "--from", "<S>", "--alias", "nope", "--as", "x"), None, 1, "no entry nope"),
        (("add", "--from", "<S>", "--alias", "candidate", "--as", "m"), None, 1, "already"),
        (("add", "--from", "<S>", "--alias", "candidate"), None, 2, "candidate"),
        (("add", "--from", "<S>", "--alias", "candidate", "--as", "x"), [dict(LIVE_CANDIDATE, file="m-Q4_K_M.gguf")], 1, "already the file of m"),
        (("add", "--from", "<S>", "--alias", "candidate", "--as", "x"), [dict(LIVE_CANDIDATE, ctx_size=4096)], 1,
         "client_max_output_tokens must not exceed ctx_size"),
    ]
    for i, (args, scratch, code, message) in enumerate(cases):
        result, cfg, original = _live(tmp_path / str(i), *args, scratch=scratch)
        assert (result.returncode, message in result.stderr, cfg.read_text() == original) == (code, True, True), (args, result.stderr)
        assert not list(cfg.parent.glob(".models.yml.*")), args


def test_live_entry_set_url_pins_a_resolve_url_of_the_same_file(tmp_path: Path) -> None:
    url = f"https://huggingface.co/o/r/resolve/{QH_SHA}/Q4/m-Q4_K_M.gguf"
    result, cfg, _ = _live(tmp_path / "ok", "set-url", "--alias", "m", "--url", url)
    assert result.returncode == 0, result.stderr
    assert ".pre-url-" in result.stdout and yaml.safe_load(cfg.read_text())["models"][0]["url"] == url
    for i, (alias, bad, code) in enumerate([("m", "https://huggingface.co/o/r/resolve/main/m-Q4_K_M.gguf", 2),
                                            ("m", f"https://huggingface.co/o/r/resolve/{QH_SHA}/other.gguf", 2),
                                            ("m", "file:///x/m-Q4_K_M.gguf", 2), ("nope", url, 1)]):
        result, cfg, original = _live(tmp_path / str(i), "set-url", "--alias", alias, "--url", bad)
        assert result.returncode == code and cfg.read_text() == original, (alias, bad, result.stderr)
```

- [ ] **Step 2: Run them.** Expected: FAIL (no such script; the `live-entry` help item fails too).

- [ ] **Step 3: Implement `live-entry.sh`** in the shape of `tune-apply.sh`: a header with the Interfaces contract, `--help`, `source` of `tools/lib.sh`, `require_cmd uv jq yq`. The pre-checks read the files with `yq` before any backup. The callbacks:
  - `add`: `yq -i '.models += [load(strenv(S)).models[] | select(.alias == strenv(A)) | .alias = strenv(B) | .enabled = false | del(.benchmark)]' "$1"`, with `S`, `A`, `B` exported.
  - `set-url`: `(.models[] | select(.alias == strenv(A)) | .url) = strenv(U)`.

- [ ] **Step 4: Edit the references.**
  - onboard-flow.md 7a.2, third bullet: "Otherwise add the candidate's entry from `$S` (its step 4 settings) under the alias decided at step 3: `bash $SK/scripts/live-entry.sh add --from $S --alias <alias>` (a local GGUF: `--alias candidate --as <alias>`). It writes `enabled: false` and backs up, validates and renames the live file. `make llama-enable` only selects an existing alias."
  - local-gguf.md L5, a bullet after its first line: "Before G3, write the keys below (all but `alias`) into the `candidate` entry of `$S` with `yq -i`. It is a scratch edit, and step 6 still screens `candidate`. 7a.2 copies it live with `--as <alias>`."
  - tuning.md P0: replace "a `cp -p` backup of the config, then `yq -i` the url of `<a>` in the config to `https://huggingface.co/<repo>/resolve/<sha>/<pick.file>`" with "then `bash $SK/scripts/live-entry.sh set-url --alias <a> --url https://huggingface.co/<repo>/resolve/<sha>/<pick.file>` (it backs up, validates and renames)". The `(<sha> is the row's sha; ...)` clause stays.

- [ ] **Step 5: Verify.** `make validate`, the new tests, then the whole scripts test file. Expected: PASS, including `test_skill_docs_name_only_existing_scripts`, the help test and the L5 pins of `test_local_gguf_reference_owns_the_name_check_copy_and_live_entry`.

### Task 5: The local GGUF copy through `fetch-gguf.sh --from`

**Files:**
- Modify: `.claude/skills/onboard-local-model/scripts/fetch-gguf.sh` (header :2-10; flags :13-28)
- Modify: `.claude/skills/onboard-local-model/references/local-gguf.md` (L4 :41-45)
- Test: `test_onboard_local_model_scripts.py` (two new tests after `test_fetch_gguf_checks_the_size_alone_without_a_sha256` :2058)

**Interfaces:**
- Produces: `fetch-gguf.sh (--url URL | --from PATH) --dest FILE --size BYTES [--sha256 HEX]`. `--from` takes an existing regular file and builds `url` as `file://` plus the absolute path with each segment percent-encoded (jq `split("/") | map(@uri) | join("/")`). Every later step is unchanged: `.part`, resume, free disk, size and sha256 checks, rename. Exit 2: `--from` and `--url` together, or `--from` not a regular file. The original is only read.

- [ ] **Step 1: Write the failing tests.**

```python
@pytest.mark.skipif(shutil.which("curl") is None, reason="curl not installed")
def test_fetch_gguf_copies_a_local_file_and_resumes_it(tmp_path: Path) -> None:  # real curl on a file:// url: no network
    src = tmp_path / "Down loads" / "Bar#1 %x.gguf"
    src.parent.mkdir()
    data = os.urandom(300_000)
    src.write_bytes(data)
    dest = tmp_path / "models" / "Bar-7B-Q4_K_M.gguf"
    dest.parent.mkdir()
    dest.with_name(dest.name + ".part").write_bytes(data[:100_000])  # an interrupted earlier copy
    path = f"/usr/bin:/bin:{Path(shutil.which('jq') or '/usr/bin/jq').parent}"  # --from encodes the path with jq
    result = _run("bash", SCRIPTS / "fetch-gguf.sh", "--from", src, "--dest", dest, "--size", str(len(data)), env={"PATH": path})
    assert result.returncode == 0, result.stderr
    assert dest.read_bytes() == data and src.read_bytes() == data and not dest.with_name(dest.name + ".part").exists()
    assert "size verified" in result.stdout


def test_fetch_gguf_from_needs_one_regular_file(tmp_path: Path) -> None:
    dest = tmp_path / "models" / "m.gguf"
    for args in (("--from", tmp_path / "missing.gguf"), ("--from", tmp_path), ("--from", tmp_path / "x", "--url", FETCH_URL)):
        result = _run("bash", SCRIPTS / "fetch-gguf.sh", *args, "--dest", dest, "--size", "1", env={"PATH": "/usr/bin:/bin"})
        assert result.returncode == 2 and not dest.parent.exists(), (args, result.stderr)
```

- [ ] **Step 2: Run them.** Expected: FAIL (`unknown flag: --from`).

- [ ] **Step 3: Implement.** Add `--from` to the flag loop. After parsing:
  - exactly one of `--url` and `--from`, else exit 2;
  - `--from` must be a regular file (`[ -f ]`), else exit 2 naming it;
  - make the path absolute (`cd "$(dirname ...)" && pwd`), then build `url`.
  - Header :2-3: the usage line, plus "`--from PATH` copies a local file the same way, through curl's `file://` (the path percent-encoded); the original is only read." The "Needs" line adds "jq (for --from)".
  - `--help` text follows from the header.

- [ ] **Step 4: Edit local-gguf.md L4.** The pinned phrases "asked only after L3 found the fit feasible" and "Never move or delete the original" stay.
  - First bullet: "G1 copies it into `~/llm-workspace/models`, the directory 7a serves from, before any benchmark or serve step: `bash $SK/scripts/fetch-gguf.sh --from <original path> --dest ~/llm-workspace/models/<base name> --size <size_bytes>` (`size_bytes` of `resolve-target.sh`). It copies into `<base name>.part` after a free-disk check, resumes an interrupted copy, checks the size and renames. Exit 1: report its message and stop, as after a declined G1."
  - Second bullet: "The owner wants that directory untouched while screening: the same command with `--dest tmp/model-scout/<run>/models/<base name>`. Set `LLM_ENV_MODELS_DIR` to that directory for step 6.2 only: 6.1 benches the baseline from the default directory. G3 copies it on with the same command from that copy, and G3's facts name that second copy."

- [ ] **Step 5: Verify.** `make validate`, the new tests, then the whole scripts test file. Expected: PASS (the existing `fetch-gguf` tests are unchanged).

### Task 6: Contents line, plan Q residuals and the tune-route eval

**Files:**
- Modify: `.claude/skills/onboard-local-model/references/onboard-flow.md` (after the intro, :3-4; the entry template :263)
- Modify: `.claude/skills/onboard-local-model/references/tuning.md` (Q1 :164-166; Q3 :177-178; the skipped bullet :194-195; Hand-back :206-207)
- Create: `.claude/skills/onboard-local-model/evals/files/models-enabled-no-gpu.yml`
- Modify: `.claude/skills/onboard-local-model/evals/evals.json` (new eval `tune-enabled-no-gpu-config` after `tune-capacity-card`)
- Test: `test_onboard_local_model_scripts.py` (pins :1315 and :1317; two new tests after `test_eval_gateway_host_fixtures_match_their_assertions` :988)

- [ ] **Step 1: Write the failing tests and pins.**

```python
def test_onboard_flow_lists_its_sections_and_the_quant_steps_name_their_inputs() -> None:
    flow = _md(REFS / "onboard-flow.md")
    assert "Sections: Candidate by kind (Alias for a new entry, Alias, Hugging Face H1-H5, Scout pick); Steps" in flow
    assert "quantization: Q4_K_M # pick.quant of the scout row (a local GGUF: L5)" in flow
    tuning = _md(REFS / "tuning.md")
    assert "--vram-available-mib <N> --quants <list>`" in _section(tuning, "- Q1.", "- For each candidate")
    q3 = _section(tuning, "- Q3.", "- Q4.")
    assert "A declined G-Q2: report the file Q2 fetched (its path and size) and ask whether to delete it" in q3


def test_eval_tune_enabled_no_gpu_config_fixture_matches_its_assertions(tmp_path: Path) -> None:
    fixture = FIX / "models-enabled-no-gpu.yml"
    cfg = yaml.safe_load(fixture.read_text())
    assert cfg["llm_server"]["enabled"] is True and cfg["gpu"]["pci_address"] == "" and cfg["gpu"]["device_name"] == ""
    result = _run("bash", SCRIPTS / "preflight.sh", env=_preflight_env(tmp_path, None) | {"LLM_ENV_CONFIG": str(fixture)})
    assert (result.returncode, result.stdout) == (2, "") and "gpu.pci_address is empty" in result.stderr
    text = _asserts("tune-enabled-no-gpu-config")
    assert all(s in text for s in ("exits 2", "gpu.pci_address is empty", "Gate G-T1", "LLM_ENV_ASSUME_YES=1 make setup",
                                   "T2 (make llama-enable) is not run", "T1 runs again"))
```

  - Pins :1315 and :1317: "every candidate skipped at Q2 or Q3" and "Every candidate skipped at Q2 or Q3 (none measured): no G-Q3; ...".
  - `RETIRED_COPIES` gains "skipped at Q2 (none measured)".

- [ ] **Step 2: Run them.** Expected: FAIL.

- [ ] **Step 3: Edit onboard-flow.md.**
  - After the intro paragraph, a new paragraph: "Sections: Candidate by kind (Alias for a new entry, Alias, Hugging Face H1-H5, Scout pick); Steps (progress checklist, No-device order, steps 1-9 with gates G1-G5); Entry template; Where things live."
  - It must not contain `### `, `H<n>. `, `**No-device order**` or a numbered bold step: `_section` anchors take the first match.
  - Template line: `quantization: Q4_K_M                # pick.quant of the scout row (a local GGUF: L5)`.

- [ ] **Step 4: Edit tuning.md.**
  - Q1's command: "`bash $SK/scripts/quant-headroom.sh check --alias <a> --vram-available-mib <N> --quants <list>`, with `<list>` the quants the owner named (comma-separated, `UD-Q5_K_XL` too; leave `--quants` out for the default `Q5_K_M,Q6_K`) and N the `vram_available_mib` ...". The rest is unchanged.
  - Q3, a continuation line: "A declined G-Q2: report the file Q2 fetched (its path and size) and ask whether to delete it (a user-data delete: asked under bypass too); then skip this candidate as a declined G-Q1 does."
  - "Every candidate skipped at Q2 (none measured)" becomes "Every candidate skipped at Q2 or Q3 (none measured)". In the Hand-back list, "every candidate skipped at Q2" becomes "every candidate skipped at Q2 or Q3".

- [ ] **Step 5: Fixture and eval.**
  - `yq '.gpu.pci_address = "" | .gpu.device_name = ""' .claude/skills/onboard-local-model/evals/files/models.yml > .claude/skills/onboard-local-model/evals/files/models-enabled-no-gpu.yml` (this writes the new fixture only).
  - Eval `tune-enabled-no-gpu-config`:
    - prompt: "tune the context and KV cache of my local model for the best speed at depth. Give me the ordered plan with its gates."
    - files: `["evals/files/models-enabled-no-gpu.yml"]`.
    - setup: "The fixture config is .claude/skills/onboard-local-model/evals/files/models-enabled-no-gpu.yml (LLM_ENV_CONFIG): llm_server.enabled is true, but gpu.pci_address and gpu.device_name are empty (make llama-enable ran, make setup did not). preflight.sh with that LLM_ENV_CONFIG exits 2 before it reads the GPU; running it is allowed. DRY RUN for evaluation: read-only commands only (preflight.sh with that variable, cat, yq, jq, the skill scripts' --help); do not start or stop containers, run make targets, edit models.yml or start sweeps, benchmarks or ladders. Answer in English."
    - expected_output: "T1's preflight exits 2 with gpu.pci_address is empty and prints no JSON; llm_server.enabled is true, so Gate G-T1 asks before LLM_ENV_ASSUME_YES=1 make setup (no make llama-enable), then T1 runs again, and only that read decides whether llm-server must be served before G-T2."
    - assertions:
      - "preflight.sh with the fixture LLM_ENV_CONFIG exits 2 with gpu.pci_address is empty and prints no JSON, so no services or budget figures are read from it"
      - "llm_server.enabled is true in the fixture, so T2 (make llama-enable) is not run; Gate G-T1 asks before LLM_ENV_ASSUME_YES=1 make setup, which detects the GPU and the Vulkan device"
      - "After make setup, T1 runs again (preflight.sh), and only that read decides whether llm-server is enabled but stopped and must be served before G-T2"
      - "No make start-llama or make stop-llama runs before make setup and the second T1 read"
      - "Nothing was started, stopped or edited"

- [ ] **Step 6: Verify.** `make validate`, the new tests, then the whole scripts test file. Expected: PASS, including the eval setup/files rules and `test_eval_input_files_exist_and_the_gguf_fixture_resolves`.

### Task 7: Verification and cleanup

- [ ] **Step 1: Byte cap.** `wc -c < .claude/skills/onboard-local-model/SKILL.md`. Expected: 10959 (<= 11000).
- [ ] **Step 2: Guard and shared-rule tests.** `bash tmp/r16/pytest-one.sh tests/skills/onboard-local-model/test_onboard_local_model_scripts.py -k "jev_as_a_black_box or stated_once or cite_each_other or router_that_loads or only_existing_scripts or long_shell_logic or user_turns"`. Expected: PASS.
- [ ] **Step 3: Skill suites, one at a time.** Run Task 0's three files in turn. Expected: all pass. The count is the baseline plus 15 test items: Task 1: 2; Task 2: 4 (parametrized); Task 3: 1; Task 4: 3, plus 1 help item; Task 5: 2; Task 6: 2. The other test changes are edits. `gen-challenge.sh --generator-id` prints Task 0's id.
- [ ] **Step 4: Repo checks.** With `TMPDIR` unset (pytest's default temp dir is outside the repo), run `make validate && make test` alone. Expected: "All checks passed." and a green suite.
- [ ] **Step 5: Route walk.** Read as a new agent who has only `SKILL.md`; nothing runs against the host. Follow each route's table row, load only the files it names, and confirm every branch ends in a step, a gate or a stop:
  1. A Hugging Face candidate on a served host, G5 yes. 7a.2 uses `live-entry.sh add`; the Keep runs `make local-combo`, then `make setup-local-llm-agents`.
  2. The same with G5 no. Step 9 runs `make stop-llama`, restores the rollback copy, runs `make start-llama` and then `make setup-local-llm-agents`.
  3. A local GGUF with a space in its path, screened from `tmp/.../models`. The L4 copies use `--from`; 6.1 runs without `LLM_ENV_MODELS_DIR`; L5's keys go into `$S`, and 7a.2 adds it with `--as`.
  4. A quant comparison on a `resolve/main/` entry: P0 pins with `set-url`, Q1 runs with `--quants`, a G-Q2 no reports the file and skips the candidate, then Q11 and the Hand-back follow, with no `make local-combo` anywhere.
  5. The tune route with llm-server enabled and an empty `gpu.pci_address`: preflight exits 2, then G-T1, `make setup` and T1 again.
  6. A ladder lane while another session edits `references/`: no `leak_suspect` row.

  Report any branch without a next step as a finding; do not fix it in this plan.
- [ ] **Step 6: Cleanup.**
  - `rm -rf tmp/r16`.
  - `ls /var/tmp | grep pytest-r16` prints nothing.
  - `podman ps -a` shows nothing this plan started (it starts nothing), and `ps` shows no process left by a test.
  - `git status --short` shows no tracked change besides `docs/superpowers/plans/README.md` (its new row). This plan file stays untracked like plans G-Q.
  - Judges are re-run by the orchestrator, not by this plan.

## Round-1 review outcome (approved after one fix)

Review round 1 (native + referee, lead-verified; tmp/plan-r-review/review-round-1.md) left one confirmed high, one medium and lows; every
referee critical/high was refuted. Plan status: approved for execution with these amendments:

- N-H1 (high, Task 4): `LIVE_CANDIDATE` reads `QH_CONFIG`, which is defined after `test_tune_apply_refuses_a_throughput_row`, so the block
  inserted there makes the scripts test file fail collection (NameError). Put Task 4's test block after the `QH_*` constants, just before
  `def _qh_tree(`.
- N-M1 (medium, Task 3): the narrowed guard no longer records an agent's writes into the owner's project. `effort-run.sh` keeps a second
  listing of the repo-wide git-visible files (the old `guard_list` scope with its exclusions, minus the guarded paths) and records changes
  there in a new row field `other_writes` (same shape as `outside_writes`: sorted, at most 20, relative to the guard root). It never flags a
  row. The CONCURRENT test asserts `other_writes` == the five concurrent paths (sorted) and `leak_suspect` false; the header and
  challenges.md state the field in one clause each.
- N-L2 (Task 3 Step 2): `test_effort_run_flags_writes_outside_the_run` is not red on its own. It also asserts `row["other_writes"] == []`,
  which goes red (KeyError) before the change.
- N-L5 (Task 3): once the gotcha drops "exits 3 while llm-server runs", no prose states the exit code. In `SKILL.md` Freeing the GPU,
  "`tune-sweep.sh` refuse on," becomes "`tune-sweep.sh` exit 3 on," (same byte count; 10,959 still holds).
- N-L1 (Task 1 Step 5): the `quant-switch-yes` setup text is "...; its Q7 interference gate passed; Q1 recorded that my-coding-lite
  exists." Drop "; Q1 recorded that my-coding-lite exists" and keep the closing period.
- N-L4 (Task 4): `require_cmd` in `tools/lib.sh` loops with a non-local `cmd`, so `live-entry.sh` keeps its subcommand in `action`, not `cmd`.
- N-L3 (Task 5): `test_fetch_gguf_from_needs_one_regular_file` passes before the change. It also asserts the message: "not a regular file"
  for the missing file and the directory, "exclude each other" for `--from` with `--url`, so it goes red first. `fetch-gguf.sh` prints
  `--from <path> is not a regular file` and `--url and --from exclude each other` (both flags are checked first).
- N-L6 (cosmetic remedies text after a partial add-back): not changed.
- Task 7's count stays baseline + 15 (the amendments add assertions, no test items).
