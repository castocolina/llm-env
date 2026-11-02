# Judge Round 7 Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the confirmed findings of round 7 of the onboard-local-model skill judges (native 89.6 %, referee 86.4 %, no critical or high after the lead check) and the residuals of plan H, and remove the structural causes of the recurring D8 and D7 losses, so the next round gives >= 90 % from both lanes with no critical or high finding.

**Architecture:** Over seven rounds the scores moved between 86 and 92. Most lost points came from three structural causes. (1) A rule is stated in several places, and the copies drift: the RAM flag, the upper bound, `compare-runs.sh` exit 3. Each rule now lives in one place, and a guard test pins this. (2) Steps are dense paragraphs: onboard steps 5, 7a, 7c and 8 and the Hugging Face section. They become numbered checklists, one command per line. (3) Route behaviour is not pinned by tests. `resolve-target.sh` gets a distinct exit code for a bare name. More evals get frozen fixtures, each pinned by a test. The route table and the trigger queries get coverage tests. `SKILL.md` stays a router under its 11,000-byte cap: restated text leaves it and moves into the reference that owns it.

**Tech Stack:** bash, jq, yq; pytest under `tests/skills/onboard-local-model/`; skill-judge rubric v4.

**Spec:** `tmp/judges/r7-native-summary.txt` and `tmp/judges/r7-referee-summary.txt` (round 7 verdicts; the referee file holds the lead's refutation of its one high), `tmp/judges/r6-*-summary.txt` (round 6, for recurring themes), and plan H (`docs/superpowers/plans/2026-10-31-judge-round-6-fixes.md`) with the residuals the orchestrator listed. The ledger below restates the residuals.

## Global Constraints

- English only. Edit only `.claude/skills/onboard-local-model/**` and `tests/skills/onboard-local-model/**`, plus this plan's row in `docs/superpowers/plans/README.md` (Task 8). The skill tree and `tests/skills` are untracked and stay uncommitted. This plan changes no tracked repo code, so nothing is committed unless the owner asks. The README rows of plans G (2026-10-31-skill-navigation-restructure) and H (2026-10-31-judge-round-6-fixes) stay as they are.
- `SKILL.md` <= 11,000 bytes (`SKILL_MD_MAX_BYTES`, tested). Never raise the cap; move content into references instead. Measured draft after Tasks 1-2: 10,891 bytes. If a reflow pushes the file over the cap, shorten rule 2's parenthetical "(command, out dir, stop and cleanup conditions, what to return)" (66 bytes) first.
- Description <= 1024 characters (tested). Measured draft: 982.
- A reference never names another reference file (`test_references_never_point_at_each_other`). Inside a reference, write "the effort-ladder reference", never `scoring.md`.
- `jev-fast-decision` is a black box, and ai-kit is never named (guard `test_skill_and_touched_docs_treat_jev_as_a_black_box`). `SKILL.md` is not in `JEV_NAMED_IN`, so the description says "asking Laya for a judgment" and never names the skill.
- Prose holds no shell logic. A backtick span may contain at most two of `;`, `&&` and `||` (`test_skill_prose_has_no_long_shell_logic`).
- Every script keeps `--help` (exit 0) and its tests. A change in script behaviour is red-first: write the test, watch it fail, then implement.
- After a `.sh` edit run `make validate`. After a `.py` edit (the test files are `.py`) run `make validate && make test`. Run the skill suite alone with `uv run pytest tests/skills/onboard-local-model -q`.
- Scratch files go under `./tmp/`, and the run that creates them removes them. No containers, no GPU, no network, no downloads, no edits to the live `models.yml`.

## Findings ledger (verified 2026-10-31 against the files)

| Finding | Verdict | Task |
|---|---|---|
| native D8-1: `resolve-target.sh` exits 2 for a bare name and for every error (missing `.gguf` :82, host :55, non-model URL :58, scratch outside tmp, usage), so a mistyped `.gguf` path is sent to `hf-scout.sh --query` | confirmed. Also plan H residual "missing .gguf path routed to repo search" (duplicate) | 1 |
| native D8-2: RAM flag. selection-criteria.md:10,22 say "0 while disabled: pass `ram.available_mib`"; onboard-flow.md:24-25,33-35 say pass 0, then re-scout an MoE `no`; eval `hf-repo-gateway-only` asserts `--ram-mib 0` | confirmed (a contradiction in the same run) | 2, 3, 4 |
| native D7-1: `latest-fit` asserts "at most three shortlisted"; rank-candidates.sh `top=6` (:18) and selection-criteria step 4 say 6 | confirmed | 4 |
| native D6-1: Standalone quant comparison needs T1-T8 (a full sweep and a live apply) when the card or the run is missing (tuning.md:108) | confirmed. `capacity headroom` needs only a current depth section and a passed interference gate (`GATE_FIRST`, pylib/capacity.py:18), and Q8 needs seeds | 5 |
| native D2-1: step 7a is one 14-line bullet; steps 5, 7c and 8 are as dense | confirmed | 3 |
| native D5-1: Scout step 1 needs "the onboard flow's step 2", which the "nothing" row never loads; the HF row names no step for selection-criteria | confirmed | 2, 4 |
| native D1-1 (low): the `make benchmark` gotcha and the bypass text restate AGENTS.md | gotcha confirmed: 7a stops running `make benchmark` (owner decision 3), so the gotcha goes. The bypass retry phrase stays, because `test_skill_gates_announce_and_proceed_when_permissions_are_bypassed` pins it; its G-Q3 sentence moves to tuning.md, which states it already | 2 |
| native D3-1 (low): three hard-won facts appear only in references | partly taken. The `start-llama --no-recreate` gotcha moves into SKILL.md. RAM 0 is now part of rule 1. "Unknown model architecture" stays in step 6 and runtimes.md (no bytes left) | 2 |
| native D4-1 / referee D4-1 (low): the description lacks container memory limits, gpu-status triage, the Laya-judgment boundary and Q6 | confirmed; the jev skill name is left out (guard) | 2 |
| native D8-3 (low): `-o <models_dir>/<file>` while an `hf_file` can be `sub/x.gguf` | confirmed (test_resolve_target_parses_hugging_face_urls) | 3 |
| native D8-4 (low): no action on a size or sha256 mismatch | confirmed | 3 |
| native D8-5 (low) / plan H residual: H is undefined in Freeing the GPU when no holder is `llama-server` | confirmed (duplicate) | 2 |
| native D8-6 (low) / plan H residual: the evidence card is `<rank>.json`, but a named model has no rank | confirmed (duplicate) | 3, 4 |
| native D8-7 (low): Q8 reuses "the main run's" T8 seeds, possibly retired or from another generator | confirmed. effort-run.sh refuses seeds of another generator (exit 2, :93), so the text only needs the retirement rule | 5 |
| plan H residual: the HF route has no rule for naming the new alias, and 7a matches `.alias == <alias> or .file == <file>`, so it can reuse an alias whose file differs | confirmed. Under the GGUF rule two quants of one model derive the same base name | 3 |
| plan H residual: quant route. When Q1 stopped llm-server and no candidate goes on, nothing restarts it ("the program ends", tuning.md:120-121) | confirmed | 5 |
| plan H residual: Q8 uses `<stamp>` for two values (the quant run and the T8 run); Q1 saves under `quant-<stamp>/` | confirmed | 5 |
| referee D8-1 (high): 7a's `make benchmark` can run for the served alias | refuted by the lead (onboard-flow.md:14 and :116-117 limit the served alias to `make start-llama`). The residual low is taken: 7a now opens with the served-alias line, and `make benchmark` leaves 7a | 3 |
| referee D8-2 (medium): a disabled alias's `url` download gets no sha256 check | refuted as a defect: models.yml entries carry no checksum (template; `pylib/config.py` has no sha256 key), so the referee's own fix ("checksum when the entry has a sha256") changes nothing. One clause makes the size-only check explicit | 3 |
| referee D8-3 (medium): after T1-T8, Q1 asks G-T2 again because T6 started llm-server | mostly refuted. After T1-T8, T5 ran in this session, so Q1 takes T5's `vram_available_mib` while llm-server serves (tuning.md:98-99,115). The real gap is the path without T5, which the prerequisites fix (P3 leaves llm-server stopped for Q1) | 5 |
| referee D8-4 (medium): `5 x 3 x 2 x 300 s` is hardcoded beside the real cap | partly confirmed. The formula and "relay the script's first stderr line" exist (scoring.md:25,41), but three copies hardcode 300 s (tuning T8 :29, Standalone :111, scoring Head-to-head :66). Now stated once, with `T` | 5, 6 |
| referee D8-5 (low): the effort-run default `low,medium,high` sits beside lanes that use `low,high` | confirmed, one clause | 6 |
| referee D6-1 (medium): a dated budget pair beside rule 1 (selection-criteria.md:41) | confirmed, delete | 4 |
| referee D6-2 (low): dated build numbers in runtimes.md | confirmed, one sentence | 6 |
| referee D7-1 (medium): no near-miss negatives for challenge, GGUF or Laya words | partly refuted: a GGUF-header negative (entry 17) and a Laya memory-cap negative (entry 12) exist. Confirmed: no challenge or Laya-judgment near-miss, and no positive for the alias or challenge routes | 7 |
| referee D7-2 (low): seven evals have no fixtures | partly taken. `gateway-only-host`, `quant-head-to-head` and `tune-capacity-card` get frozen fixtures (Tasks 5, 7). `latest-fit` reads the live config by design (its first assertion). The anchor, runtime and 401 evals check live CLIs and images or need no input. `generate-one-challenge`'s generator is deterministic and covered by test_challenges.py. `onboard-plan-with-server-running` would need a search fixture of several repos; it gets only the exit-3 fix | 1, 5, 7 |
| referee D5-1 (low): no route row for one challenge | confirmed | 2 |
| referee D1-1 (low): rule 2's delegation sentence repeats AGENTS.md | dropped: AGENTS.md says how to monitor delegated runs, not that every benchmark run is delegated. That rule belongs to the skill | - |

## Owner decisions (defaults chosen)

1. **Exit 3 = a bare model name** (`resolve-target.sh` rule 6). Every other failure, usage errors included, stays exit 2, and the route reports it and stops.
2. **One RAM rule, in `SKILL.md` rule 1.** Every `hf-scout.sh` call passes `budget.ram_offload_budget_mib`, or `ram.available_mib` (said) when that value is null or 0. The two-step re-scout of the HF section goes, because it gives the same result as scouting with `ram.available_mib` at once. Eval `hf-repo-gateway-only` moves from `--ram-mib 0` to `--ram-mib 14500`; the pick does not change (checked: Foo-9B-Q5_K_M.gguf both ways). Step 4's `llmenv budget` stays the authoritative verdict.
3. **7a runs no `make benchmark`**, the same as tuning T6. Step 6 already measured at depth. A new entry's card `benchmark` field (informational, pylib/capacity.py:189) stays null until the owner runs it per AGENTS.md.
4. **One alias rule for every new entry.** An entry with the same `file` keeps its alias. Otherwise the alias is the base name without `.gguf` and without its quant token, lowercased, `[a-z0-9-]`, at most 60 characters, with `-2`, `-3`... while a live alias for another file has the name. The quant token is stripped (today's GGUF rule, short aliases). Two quants of one model then differ by suffix. The quant route keeps its own `<a>-<q>` names.
5. **Quant comparison prerequisites** replace T1-T8: P1 seeds, P2 interference, P3 depth. T4-T6 run only when tuning is asked; T2 runs only when `llm_server.enabled` is false.
6. **Seeds reuse:** an earlier lane of `<a>` counts only while no Q8 lane has used its seeds. When in doubt, run P1.
7. **Trigger queries: 11 positives and 11 negatives**, pinned by a coverage test. No train/validation split (as plan H decided).

## Review Focus

1. A bare name that is a typo of a live alias (`gemma5`): exit 3 sends it to a Hugging Face search. The expected outcome is that Find the repo lists the plausible repos and asks, and never onboards the first hit silently. This is covered by existing text ("Several plausible repos: list them and ask"). Untested, because the scripts cannot see intent.
2. A quant comparison when `<a>` is enabled but llm-server is stopped: P1 and P2 need it served. The Standalone names line says `make start-llama` first (Task 5). Text only.
3. `ram.available_mib` overstates the cap llm-server gets once it is enabled (`memory_ceiling_pct`, clamped to MemAvailable). A scouted `offload` class can then fail step 4's `llmenv budget` after the download. Expected: that budget decides, and the fit is reported as screening. This is covered by rule 1 ("a precondition, not a verdict") and step 4. Text only.
4. A card whose `interference.passed` is false: P2 reruns the gate, and a second failure ends the comparison, because `check` refuses without a passed gate. Covered by the P2 text (Task 5). Pinned only through the script (`GATE_FIRST`).
5. Two long file names that are the same after the 60-character cut: the `-2` suffix keeps them apart, because uniqueness is checked after the cut. Pinned for the collision case by `test_eval_gguf_alias_collision_fixture_matches_its_assertions` (Task 3). The cut itself is text only.

---

### Task 1: `resolve-target.sh` exits 3 only for a bare model name

**Files:**
- Modify: `.claude/skills/onboard-local-model/scripts/resolve-target.sh:6` (header rule 6), `:83` (the bare-name failure)
- Modify: `.claude/skills/onboard-local-model/SKILL.md:22` (route row) and the bullets under the route table
- Modify: `.claude/skills/onboard-local-model/references/selection-criteria.md:8` (first sentence of Find the repo)
- Modify: `.claude/skills/onboard-local-model/evals/evals.json` (`onboard-plan-with-server-running` assertion 1, `bare-name-no-repo` expected_output and assertion 1, append `missing-gguf-path`)
- Test: `tests/skills/onboard-local-model/test_onboard_local_model_scripts.py` (`test_resolve_target_rejects_anything_else` :1569, `test_eval_alias_fixtures_match_their_assertions` :1606, `test_eval_bare_name_fixture_matches_its_assertions` :1682, two new tests)

**Interfaces:**
- Produces: `resolve-target.sh MODEL` exit 3 with stderr `<MODEL>: <forms>; search it as a model name (exit 3)` and no stdout, for a MODEL that is not a GGUF file, a URL, a live alias, a `.gguf` argument or ORG/NAME. Every other failure exits 2 as before.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.parametrize(
    ("model", "code"),
    [("Qwen3.8-27B", 3), ("Qwen 3.8 27B", 3), ("nope.gguf", 2), ("Missing-7B.GGUF", 2),
     ("https://example.com/o/r", 2), ("https://huggingface.co/datasets/o/r", 2)],
)
def test_resolve_target_exits_3_only_for_a_bare_model_name(tmp_path: Path, model: str, code: int) -> None:
    result = _rt(tmp_path, model)
    assert (result.returncode, result.stdout) == (code, ""), result.stderr
    assert ("search it as a model name" in result.stderr) == (code == 3)


def test_eval_missing_gguf_fixture_matches_its_assertions() -> None:
    path = ".claude/skills/onboard-local-model/evals/files/Missing-7B-Q4_K_M.gguf"
    assert not (ROOT / path).exists()
    env = {**os.environ, "LLM_ENV_CONFIG": str(FIX / "models.yml"), "LLM_ENV_MODELS_DIR": str(FIX / "models")}
    result = _run("bash", SCRIPTS / "resolve-target.sh", path, env=env, cwd=ROOT)
    assert (result.returncode, result.stdout) == (2, "") and "not found" in result.stderr
    assert all(s in _asserts("missing-gguf-path") for s in ("exits 2", "not found", "hf-scout.sh --query is not run"))
```

Change the existing tests: in `test_resolve_target_rejects_anything_else`, `assert result.returncode == 2` becomes `== 3` (the three words stay). At :1606, `returncode == 2 and "exits 2"` becomes `returncode == 3 and "exits 3"`. At :1682, `returncode == 2` becomes `== 3`, and the `"exits 2"` in the tuple becomes `"exits 3"`.

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/skills/onboard-local-model -q -k "exits_3 or missing_gguf or rejects_anything or eval_alias or eval_bare"`
Expected: FAIL. The bare names exit 2, and the `missing-gguf-path` eval is missing.

- [ ] **Step 3: Implement**

- resolve-target.sh:83 becomes `[[ "$model" =~ $repo_re ]] || { echo "$model: $forms; search it as a model name (exit 3)" >&2; exit 3; }`.
- Header :6: "(5) ORG/NAME -> hf_repo; (6) exit 2." becomes "(5) ORG/NAME -> hf_repo; (6) exit 3: a bare model name, to search on Hugging Face. Every other failure (a missing .gguf, an unsupported or non-model URL, --scratch-config misuse, usage) exits 2."
- SKILL.md:22: "(`resolve-target.sh` exits 2)" becomes "(`resolve-target.sh` exits 3)". Insert this as the first bullet under the route table: "- `resolve-target.sh` exit 2 is an error (a missing `.gguf` path, a non-model URL, usage): report its message and stop."
- selection-criteria.md:8: "`resolve-target.sh` exits 2 on a name that is none of its four forms." becomes "`resolve-target.sh` exits 3 on a bare model name (exit 2 is an error: report it and stop)."
- evals.json `onboard-plan-with-server-running` assertion 1: "exits 2 (a bare name)" becomes "exits 3 (a bare name)". `bare-name-no-repo`: expected_output "resolve-target.sh exits 2 (a bare name)" becomes "exits 3 (a bare name)", and assertion 1 becomes "resolve-target.sh Zeta-Coder-7B exits 3: a bare model name, none of the four forms". Append:

```json
{
  "id": "missing-gguf-path",
  "prompt": "onboard-local-model .claude/skills/onboard-local-model/evals/files/Missing-7B-Q4_K_M.gguf. The fixture config is .claude/skills/onboard-local-model/evals/files/models.yml and its models directory is .claude/skills/onboard-local-model/evals/files/models/ (scripts read them through LLM_ENV_CONFIG and LLM_ENV_MODELS_DIR). DRY RUN for evaluation: read-only commands only. Answer in English.",
  "files": ["evals/files/models.yml"],
  "expected_output": "resolve-target.sh exits 2 with not found: a .gguf argument that does not exist is an error, not a model name; the run reports the message, asks for the right path and stops without a Hugging Face search.",
  "assertions": [
    "resolve-target.sh .claude/skills/onboard-local-model/evals/files/Missing-7B-Q4_K_M.gguf exits 2 and its message says not found",
    "Exit 2 is treated as an error: the message is reported and the run stops; hf-scout.sh --query is not run (only exit 3 means a bare model name)",
    "The answer asks for the correct path to the .gguf file, or a Hugging Face repo id or URL",
    "No scratch config, download, gate, container or configuration change was made"
  ]
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/skills/onboard-local-model -q -k "resolve_target or eval_ or help or missing_required"`
Expected: PASS. `test_missing_required_arguments_exit_two` still sees exit 2 when no MODEL is given.

- [ ] **Step 5: Validate (no commit: untracked tree)**

Run: `make validate && make test`
Expected: `All checks passed.` and the suite green.

### Task 2: `SKILL.md` router and shared rules (byte-measured)

**Files:**
- Modify: `.claude/skills/onboard-local-model/SKILL.md:3,20,28-32,43,52-55,67-69,73-75,80-81,89,109-111`
- Modify (counterparts of text that leaves SKILL.md): `references/tuning.md:149`, `references/onboard-flow.md:139-141` (step 9) and `:178-179` (Where things live), `references/scoring.md:71-72` (Head-to-head item 3)
- Test: `tests/skills/onboard-local-model/test_onboard_local_model_scripts.py` (a new test after `test_skill_md_is_a_router_that_loads_every_reference`)

**Interfaces:**
- Produces: "the rule 1 RAM" (rule 1). References write `--budget-mib <rule 1 budget> --ram-mib <rule 1 RAM>` (Tasks 3, 4). The challenge route row. `SKILL.md` no longer states the `compare-runs.sh` exit 3 (scoring.md owns it), the G-Q3 bypass rule (tuning.md) or the declined-G1/G2 ending (onboard-flow.md step 9).

These edits were measured on a scratch copy with Task 1's two edits: 10,811 -> 10,891 bytes, and the description went from 860 to 982 characters. The router, prose, gates and guard checks pass on that copy.

- [ ] **Step 1: Write the route-table guard test** (a guard on navigation, green once written; route rows must keep naming headings and steps that exist)

```python
def test_route_table_names_existing_sections_and_steps() -> None:
    text = SKILL_MD.read_text()
    rows = "\n".join(line for line in text.splitlines() if line.startswith("| ") and "references/" in line)
    pairs = re.findall(r"`references/([a-z-]+)\.md`, ([A-Z][A-Za-z ]+?)(?: \(| \|)", rows)
    assert len(pairs) >= 6, pairs  # Alias, Hugging Face, Local GGUF, Find the repo, Scout and rank, Standalone quant comparison
    for name, section in pairs:
        assert re.search(rf"^#+ {re.escape(section)}\b", (REFS / f"{name}.md").read_text(), re.M), (name, section)
    flow = (REFS / "onboard-flow.md").read_text()
    for step in sorted(set(re.findall(r"at step (\d)", rows))):
        assert re.search(rf"^{step}\. \*\*", flow, re.M), step
    assert re.search(r"^### Scout pick", flow, re.M) and "| `references/challenges.md` |" in rows
```

- [ ] **Step 2: Run it to verify it fails** with `uv run pytest tests/skills/onboard-local-model -q -k route_table`. Expected: FAIL on the last assertion (there is no challenge row yet).

- [ ] **Step 3: Edit SKILL.md (exact replacements; quoted old text is given unwrapped, so match the words across the file's line breaks and reflow to about 128 columns)**

- Line 3: replace "compares quants (Q4_K_M vs Q5_K_M); wants" with "compares quants (Q4_K_M, Q5_K_M or Q6_K) or asks if a higher quant fits; wants". Replace "Not for OmniRoute updates or other combos, picking a cloud provider," with "Not for OmniRoute updates or other combos, container memory limits, GPU-holder triage (gpu-status), asking Laya for a judgment, picking a cloud provider,".
- Line 20 (HF row), Also load: replace "`references/selection-criteria.md` sections 1-5 and 7; `references/scoring.md` at step 5" with "`references/selection-criteria.md` section 7 at step 3 (research brief), sections 1-3 at step 4; `references/scoring.md` at step 5".
- After the System One row (line 28) add the row:

```text
| one ladder challenge: generate, verify or debug it | `references/challenges.md` | | every other file |
```

- Replace the challenges bullet (lines 31-32) with "- `references/challenges.md` also serves authoring or calibrating the challenge library and a `leak_suspect` row."
- Rule 1, after "lead with that.", append: "Every `hf-scout.sh` call takes `--budget-mib` with that budget and `--ram-mib` with the rule 1 RAM: `budget.ram_offload_budget_mib`, or `ram.available_mib` (say so) when that is null or 0 (llm-server disabled: a precondition, not a verdict)."
- Rule 3: replace "anchor's id), the one profile `make setup-local-llm-agents` writes, on one `seeds.json`" with "anchor's id) on one `seeds.json`", and delete the last sentence ("`compare-runs.sh` exit 3 means the lanes are not a head-to-head: no verdict.").
- Freeing the GPU: after "on exit 1 report the printed holders and stop." insert "No `llama-server` holder (gateway-only, or llm-server down): H = 0, skip `make stop-llama`."
- Lines 74-75: replace "Each gate is its own question and a yes to one never covers the next. A declined G1 or G2 ends with the fit math and no scratch files; a declined G3 or G5, or a failed step after G2, runs the flow's rollback (`onboard-flow.md` step 9, `tuning.md` Rollback)." with "Each gate is its own question and a yes to one never covers the next; a no runs the flow's rollback."
- Lines 80-81: delete "G-Q3 (switching the default quant) is a change of model: under bypass announce the comparison table and keep the current quant."
- Line 89: replace "`make stop-llama` is the supported way to free the GPU: it stops only llm-server, and only through G2." with "Free the GPU only with `make stop-llama` (llm-server alone), after G2 or G-T2."
- Gotchas: delete the `llm_server.enabled` item (lines 109-110) and the `make benchmark` item (line 111). Add as the first item: "- `make start-llama` never recreates the container (`--no-recreate`): an image `make setup` pulled is served only after a recreation (`make restart`)."

- [ ] **Step 4: Counterpart edits in the references**

- tuning.md:149: "under bypass announce it and keep `<a>` (`SKILL.md` Gates)." becomes "under bypass announce it and keep `<a>`: a quant switch is a change of model."
- onboard-flow.md step 9: after "with `enabled: false`." insert "A declined G1 or G2: report the fit math, remove the scratch files and stop."
- onboard-flow.md Where things live, last bullet: "no local llama.cpp runs. Serving needs it `true`" becomes "no local llama.cpp runs, but screening (`bench-fit.sh`) still works in a throwaway container. Serving needs it `true`".
- scoring.md Head-to-head item 3: "exits 3 with `not a head-to-head` when the sets differ." becomes "exits 3 with `not a head-to-head` when the sets differ: no verdict."

- [ ] **Step 5: Verify**

Run: `wc -c .claude/skills/onboard-local-model/SKILL.md` (<= 11,000; about 10,891), then `uv run pytest tests/skills/onboard-local-model -q -k "route_table or router or description or gates or prose or black_box or name_only or references_never"`.
Expected: PASS. Then run `make validate && make test`.

### Task 3: Onboard flow: checklists, one alias rule, download checks (+ 1 new eval, 2 changed)

**Files:**
- Modify: `.claude/skills/onboard-local-model/references/onboard-flow.md`: insert after :9; replace :24-39 (Hugging Face body), :49-51 (GGUF naming sentence), :61-65 (step 1), the download sentence in :77-78, :85-93 (step 5), :104-117 (7a), :120-129 (7c), :130-138 (step 8), :150 (template alias comment)
- Create: `.claude/skills/onboard-local-model/evals/files/Ornith-Q5_K_M.gguf` (the bytes `GGUF` + 60 zero bytes, the same as `Foo-9B-Q4_K_M.gguf`)
- Modify: `evals/evals.json` (`hf-repo-gateway-only` assertion 2; `hf-file-pinned-revision` one new assertion; append `gguf-alias-collision`)
- Test: `tests/skills/onboard-local-model/test_onboard_local_model_scripts.py` (`test_eval_gateway_hf_fixtures_match_their_assertions`, `test_eval_hf_file_fixtures_match_their_assertions`, a new test after them)

**Interfaces:**
- Consumes: Task 2's "rule 1 RAM" and Task 1's exit codes.
- Produces: the section name "Alias for a new entry" and the 7a/7c item numbers (Task 8's route walk reads them).

- [ ] **Step 1: Write the failing pin tests**

```python
def test_eval_gguf_alias_collision_fixture_matches_its_assertions() -> None:
    fixture = FIX / "Ornith-Q5_K_M.gguf"
    assert fixture.read_bytes() == GGUF_BYTES
    env = {**os.environ, "LLM_ENV_CONFIG": str(FIX / "models.yml"), "LLM_ENV_MODELS_DIR": str(FIX / "models")}
    rt = json.loads(_run("bash", SCRIPTS / "resolve-target.sh", fixture, env=env).stdout)
    assert (rt["kind"], rt["quant"]) == ("gguf_file", "Q5_K_M")
    by_alias = {m["alias"]: m["file"] for m in yaml.safe_load((FIX / "models.yml").read_text())["models"]}
    assert by_alias["ornith"] == "ornith-1.0-9b-Q4_K_M.gguf" and fixture.name not in by_alias.values() and "ornith-2" not in by_alias
    assert all(s in _asserts("gguf-alias-collision") for s in ("ornith-2", "Q5_K_M", "ornith-1.0-9b-Q4_K_M.gguf"))
```

In `test_eval_gateway_hf_fixtures_match_their_assertions`: read `ram = pre["ram"]["available_mib"]`, pass `"--ram-mib", str(ram)` instead of `"0"`, assert `(budget, ram, row["pick"]["file"]) == (10369, 14500, "Foo-9B-Q5_K_M.gguf")`, and in the final tuple replace `"--ram-mib 0"` with `"--ram-mib 14500"`.
In `test_eval_hf_file_fixtures_match_their_assertions` add:

```python
    models = yaml.safe_load((FIX / "models.yml").read_text())["models"]
    assert "foo-9b" not in {m["alias"] for m in models} and "Foo-9B-Q4_K_M.gguf" not in {m["file"] for m in models}
    assert "The entry alias is foo-9b" in text
```

- [ ] **Step 2: Run them to verify they fail** with `uv run pytest tests/skills/onboard-local-model -q -k "alias_collision or eval_gateway_hf or eval_hf_file"`. Expected: FAIL (fixture, eval and assertion texts missing).

- [ ] **Step 3: Fixture and evals**

Create the fixture. In `hf-repo-gateway-only`, assertion 2 becomes "hf-scout.sh --repo lab/Foo-9B-GGUF --json --budget-mib 10369 --ram-mib 14500 picks Foo-9B-Q5_K_M.gguf (6500 MiB): the rule 1 RAM is ram.available_mib 14500 because ram_offload_budget_mib is 0 while llm-server is disabled, and the answer says so". In `hf-file-pinned-revision`, before the last assertion insert "The entry alias is foo-9b (Alias for a new entry: no live entry has the file Foo-9B-Q4_K_M.gguf and no live alias is foo-9b)". Append:

```json
{
  "id": "gguf-alias-collision",
  "prompt": "onboard-local-model .claude/skills/onboard-local-model/evals/files/Ornith-Q5_K_M.gguf. The fixture config is .claude/skills/onboard-local-model/evals/files/models.yml and its models directory is .claude/skills/onboard-local-model/evals/files/models/ (scripts read them through LLM_ENV_CONFIG and LLM_ENV_MODELS_DIR). DRY RUN for evaluation: read-only commands only (resolve-target.sh, yq, jq); a scratch config under ./tmp/ is allowed and removed at the end. Say which alias the live entry would get at G3 and why. Answer in English.",
  "files": ["evals/files/Ornith-Q5_K_M.gguf", "evals/files/models.yml"],
  "expected_output": "kind gguf_file, quant Q5_K_M; no live entry has the file Ornith-Q5_K_M.gguf; the derived name ornith is the alias of another file (ornith-1.0-9b-Q4_K_M.gguf), so the entry becomes ornith-2 and the existing ornith entry is never reused.",
  "assertions": [
    "resolve-target.sh classifies the argument as kind gguf_file with quant Q5_K_M",
    "No entry of the fixture config has the file Ornith-Q5_K_M.gguf, so no existing alias is reused for it",
    "The derived name is ornith (the base name without .gguf and the quant token Q5_K_M, lowercased, the trailing - dropped), which the live alias ornith already has for ornith-1.0-9b-Q4_K_M.gguf",
    "The G3 alias is therefore ornith-2; the ornith entry is never edited or enabled for this file",
    "The scratch config keeps the alias candidate and is removed at the end; nothing was copied, downloaded, stopped, started or edited"
  ]
}
```

- [ ] **Step 4: Edit onboard-flow.md (exact text; line numbers are those of the file when this plan was written. Task 2's step 9 insertion can shift the template line by one, so find each block by its first words and apply the edits bottom-up)**

Insert after line 9 (before `### Alias`):

```text
### Alias for a new entry

One rule for every new entry, decided at step 3 and used unchanged in `$S`, the lanes and 7a:
1. An entry of the live file with the same `file` keeps its alias: one GGUF never gets a second entry.
2. Otherwise take the file's base name without `.gguf` and without its quant token (the `quant` of `resolve-target.sh`, or
   `pick.quant`), lowercase it, turn each run of characters outside `[a-z0-9]` into `-`, drop leading and trailing `-`, and keep
   at most 60 characters.
3. While a live `models[].alias` already has that name (an entry for another file), append `-2`, `-3`... A matching name is
   never reused for a different file.
A Local GGUF stays `candidate` in `$S` (the script names it) and takes this alias at 7a.
```

Replace lines 24-39 (the Hugging Face body) with:

```text
1. Scout the one repo: `bash $SK/scripts/hf-scout.sh --repo <repo> --json --budget-mib <rule 1 budget> --ram-mib <rule 1 RAM>`,
   plus `--revision <revision> --file <file>` from the `hf_file` JSON, or `--file <pick file>` for a scout pick. The row is read
   at one commit, `sha` (the revision resolved, `main` by default). Exit 2 (the file is not in that commit, or the repo or
   revision is unreadable): report and stop.
2. `pick` is the named file whatever the scout would choose, else the scout's choice. `sha`, `pick.bytes` and `pick.sha256`
   fill the entry (template below); never re-read `tree/main` for them. A null `sha` or `pick.sha256` stops before G1 (template
   note). `fit: "unknown"` (the tree was unreadable): retry once, else report and stop, because G1 needs the size.
3. `fit: "no"` stops before G1, under bypass too: report the fit math and the repo's largest fitting quant (the same call
   without `--file`, `--revision` kept); only an explicit owner request after that report downloads it. A false card gate, or a
   failed `bpw` or `ctx` gate, is a G1 fact instead; under bypass it proceeds, because the owner named the model.
4. One research subagent writes the evidence card (`evidence/1.json`) per the research brief loaded at this step.
5. Name the entry per Alias for a new entry. Scratch config: `bash $SK/scripts/resolve-target.sh <live alias> --scratch-config $S`
   with any alias of the live file (the baseline when there is one; with no baseline (step 2) any entry, since step 4's
   `models select` switches to the candidate), then add the entry (template below) to `$S` with `yq -i`, `enabled: false`.
   Never `cp` the live file into `tmp/`; with no alias in the live file, stop and report.
```

Lines 49-51: replace "At G3 the live entry gets a real alias, never `candidate`: the file name without `.gguf` and the quant suffix, lowercased, every run outside `[a-z0-9]` turned into `-`, then `-2`, `-3`... until it is unique in the live `models[].alias`; `label` is the human name;" with "At G3 the live entry takes the alias of Alias for a new entry, never `candidate`; `label` is the human name;".

Replace lines 61-65 (step 1) with:

```text
1. **Preflight (read-only).** `bash $SK/scripts/preflight.sh` prints VRAM, budgets, RAM, swap, GPU holders, temperature and
   clocks; size per rule 1 (it also says which RAM figure `hf-scout.sh` takes). Flag `services.llm_server_running` and any
   non-zero `gpu.holders`. `budget.ram_offload_budget_mib` is the llm-server memory cap from `llmenv budget`
   (`budget.ram_budget_source` names its origin). Run the rule 3 profile check now, for the lane of step 5.
```

Step 4: replace line 78, from "Download with `curl -fL --continue-at - -o <models_dir>/<file> <url>`" to the end of the line ("... equals the row's `pick.sha256`."), with:

```text
Download with `curl -fL --continue-at - -o <models_dir>/<entry file> <url>` (`<entry file>` is the entry's `file`, a
   base name, never a sub-folder path). Check the byte size equals `size_bytes` and, for a Hugging Face file, that
   `sha256sum <models_dir>/<entry file>` equals the row's `pick.sha256`; an alias `url` gets the size check only (models.yml
   stores no checksum), and the report says so. A mismatch: delete that file, report both values and stop (step 9).
```

Replace lines 85-93 (step 5) with:

```text
5. **Baseline lane, then free the GPU.**
   1. Decide the lane set first (baseline, candidate, any anchor the owner named), all on the router profile (rule 3).
   2. While llm-server still serves the baseline, run its lane (a benchmark run, rule 2; upper bound per the effort-ladder
      reference, Running a lane):
      `PI_CODING_AGENT_DIR=$HOME/.pi/agent bash $SK/scripts/effort-run.sh --model router-env/llama-cpp/<baseline> --out tmp/model-scout/<run>/effort-baseline --efforts low,high --per-layer 3`.
      It writes the `seeds.json` every later lane takes; once G2 stops llm-server, this lane would need a swap back under its
      own gate. A recorded baseline lane counts only when its `challenge_set` matches. The served alias: this is its only lane.
   3. When this lane ran, record its build: `podman inspect --format '{{.Image}}' llm-server`.
   4. **Gate G2**: free the GPU per `SKILL.md` Freeing the GPU (`bench-fit.sh` needs `llm_server_running` false). Remember
      the prior state (serving, or gateway-only) for step 9.
```

Replace lines 104-117 (7a) with:

```text
   - **7a. Apply and serve (Gate G3).** The served alias runs only `make start-llama`; the list is for a new candidate.
     1. Rollback copy, before the first live edit: `cp -p ~/.config/llm-env/models.yml ~/.config/llm-env/models.yml.pre-<alias>-<stamp>`.
        Step 9 restores this copy, never the backup `make llama-enable` takes for itself.
     2. Entry. An Alias-row candidate already has one: go to 3. Else, an entry with the same file
        (`yq '.models[] | select(.file == "<entry file>") | .alias' ~/.config/llm-env/models.yml`) is never duplicated: use its
        alias. Else add the entry (template below) under the alias decided at step 3, with `yq -i` (`make llama-enable` only
        selects an existing alias).
     3. `make llama-enable MODEL=<alias>` (validates the alias, sets `llm_server.enabled`, writes atomically).
     4. `LLM_ENV_ASSUME_YES=1 make setup` (unattended per AGENTS.md: the GPU prompt takes its default from the live file, so it
        answers yes after `make llama-enable`). It downloads a missing enabled file, validates the GGUFs, pulls the floating Vulkan
        tag and runs the budget check; `make check-setup` validates offline. A Vulkan device selection error means no or several
        matching devices: hand `make setup` to the owner.
     5. No `make benchmark`: step 6 measured this model at depth, and on a Vulkan failure it rewrites `gpu.backend` (AGENTS.md).
     6. `make start-llama` (`make start` when the stack is down), then `make status`, `make check-server` and
        `make check-with-agents`.
     7. `make setup-local-llm-agents` (the router profile gets `llama-cpp/<alias>`).
```

Replace lines 120-129 (7c) with:

```text
   - **7c. Quality ladder.** The served alias runs only item 3, on its step 5 lane.
     1. Build: `podman inspect --format '{{.Image}}' llm-server` (the image Id, comparable with
        `podman image inspect --format '{{.Id}}' <gpu.image>`). `make start-llama` never recreates the container, so it equals
        step 5's; a different Id (the container was recreated, for example by `make start`) means the lanes ran on two llama.cpp
        builds, and step 8 reports (b) as unproven. Without a baseline lane, only record it.
     2. Candidate lane (a benchmark run, rule 2):
        `PI_CODING_AGENT_DIR=$HOME/.pi/agent bash $SK/scripts/effort-run.sh --model router-env/llama-cpp/<alias> --out tmp/model-scout/<run>/effort --efforts low,high --seeds <baseline seeds.json>`;
        without a baseline lane it is the first lane: `--per-layer 3` instead of `--seeds`.
     3. `bash $SK/scripts/score.sh <results.jsonl>`, then `make model-capacity MODEL=<alias> SCORE=<score.json>`.
     4. `bash $SK/scripts/compare-runs.sh` with the baseline first (Head-to-head of the effort-ladder reference); with no
        baseline lane and no anchor there is nothing to compare: skip it. An anchor the owner named joins here per its route row,
        after **Gate G4**.
     5. Classify failures per the effort-ladder reference.
```

Replace lines 130-138 (step 8) with:

```text
8. **Verdict.** Lead with the verdict and one reason. The served alias has no verdict: report its numbers and the capacity card.
   The candidate replaces the baseline only when all three hold:
   - (a) it fits `llmenv budget` with System One resident;
   - (b) it passes the replace rule (Score, item 6, of the effort-ladder reference) on one seed set and one build (the step 5
     and 7c Ids are equal; unproven: no replace, and offer to re-run the baseline lane on the 7c build through its own gated swap);
   - (c) pp and tg at the reference depth from step 6 (one image for both) stay within about 3 % (run noise) of the baseline,
     or the quality gain justifies the slowdown and the trade is stated.
   Without a baseline (step 2), the verdict rests on (a), the 20 t/s floor and the ladder reached; (b) and (c) are skipped and
   said so; G5 still asks. Before recommending, ask: would the owner notice this in a pi session? Then the table: candidate vs
   baseline vs anchors (score, solved/total, pp/tg at depth, VRAM at ctx, RAM, temperature) and what was not verified. When
   `podman image inspect --format '{{.Id}}' <gpu.image>` after 7a differs from the 7c Id, the table says the next container
   recreation (`make restart`) serves a build these numbers did not measure.
```

Template line 150: the comment "# unique; becomes llama-cpp/<alias> in the router profile" becomes "# per Alias for a new entry; becomes llama-cpp/<alias> in the router profile".

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/skills/onboard-local-model -q -k "eval_ or references or prose or name_only or route_table"`, then
`rg -n "re-scouted|SKILL.md\` Gotchas|\.alias == \"<alias>\" or|<models_dir>/<file>" .claude/skills/onboard-local-model/references/onboard-flow.md` (no hits) and `rg -c "tree/main" .claude/skills/onboard-local-model/references/onboard-flow.md` (1: the "never re-read" sentence).
Expected: PASS, no hits, 1. Then run `make validate && make test`.

### Task 4: Selection criteria: one RAM rule, baseline inline, named-model card, `latest-fit`

**Files:**
- Modify: `.claude/skills/onboard-local-model/references/selection-criteria.md:9-10`, `:16-17`, `:21-22`, `:41`, `:130`, `:143`
- Modify: `evals/evals.json` (`latest-fit` expected_output and assertion 5)
- Test: `tests/skills/onboard-local-model/test_onboard_local_model_scripts.py` (`test_eval_scout_fixtures_match_their_assertions`)

**Interfaces:**
- Consumes: "the rule 1 RAM" (Task 2), the exit-3 sentence (Task 1).

- [ ] **Step 1: Write the failing assertion.** At the end of `test_eval_scout_fixtures_match_their_assertions` add:

```python
    assert len(short["shortlist"]) <= 6 and all(s in _asserts("latest-fit") for s in ("at most 6", "at most three are recommended to go on"))
```

- [ ] **Step 2: Run it to verify it fails** with `uv run pytest tests/skills/onboard-local-model -q -k eval_scout`. Expected: FAIL.

- [ ] **Step 3: Edit**

- Lines 9-10: the command becomes `bash $SK/scripts/hf-scout.sh --query "<name>" --json --budget-mib <rule 1 budget> --ram-mib <rule 1 RAM>`. Delete the parenthetical "(null, or 0 while llm-server is disabled: pass `ram.available_mib` and say so)".
- Lines 16-17: "and the baseline per the onboard flow's step 2 (none when" becomes "and the baseline: the enabled entry of `~/.config/llm-env/models.yml` (none when".
- Lines 21-22: `--ram-mib <budget.ram_offload_budget_mib>` becomes `--ram-mib <rule 1 RAM>`. Delete "(null, or 0 while llm-server is disabled: pass `ram.available_mib` and say so)".
- Line 41: "- Budget: `SKILL.md` rule 1 (the repo verdict was 8,321 MiB against 10,369 screening on 2026-10-30)." becomes "- Budget: `SKILL.md` rule 1."
- Line 130: "(`tmp/model-scout/<run>/evidence/<rank>.json`)" becomes "(`tmp/model-scout/<run>/evidence/<rank>.json`; a model the owner named: `1.json`)". Line 143: "write only the card at `tmp/model-scout/<run>/evidence/<rank>.json`." becomes "write only the card at `tmp/model-scout/<run>/evidence/<rank>.json` (`1.json` for a model the owner named)."
- evals.json `latest-fit`: in expected_output, "a shortlist of at most three Hugging Face candidates with" becomes "a ranked shortlist (at most 6, rank-candidates.sh's default) of Hugging Face candidates, at most three recommended to go on, with". Assertion 5 becomes "The ranked shortlist holds at most 6 rows (rank-candidates.sh default --top 6) and at most three are recommended to go on; each lists base model, lab or org, parameter count, quantisation and context length".

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/skills/onboard-local-model -q -k "eval_ or references or prose or black_box"`, then `rg -n "onboard flow's step|8,321|ram_offload_budget_mib>" .claude/skills/onboard-local-model/references/selection-criteria.md` (no hits).
Expected: PASS, no hits. Then run `make validate && make test`.

### Task 5: Quant route: prerequisites instead of T1-T8, restart when nothing fits, one name per path (+ 2 fixture-backed evals)

**Files:**
- Modify: `.claude/skills/onboard-local-model/references/tuning.md:28-30` (T8), `:98-99` (Quant headroom intro), `:106-112` (Standalone), `:114-121` (Q1), `:127-129` (Q4, Q5), `:133-136` (Q8)
- Modify: `evals/evals.json` (`quant-head-to-head`, `tune-capacity-card`)
- Test: `tests/skills/onboard-local-model/test_onboard_local_model_scripts.py` (two new tests after `test_eval_quant_switch_fixtures_match_their_assertions`)

**Interfaces:**
- Consumes: `uv run llmenv.py --config <cfg> capacity show --alias <a>` (exit 1 with `{"error": "no capacity card for <a>; ..."}` when the card is missing; measured on the fixture config 2026-10-31). The card's `interference.passed` and `stale_sections` (pylib/capacity.py:246-281).
- Produces: names used in tuning.md: `<seeds>`, `<a-results>`, `QR`, P1-P3, verify depths.

- [ ] **Step 1: Write the failing pin tests**

```python
def test_eval_quant_fixtures_match_their_assertions() -> None:
    show = _run("uv", "run", "llmenv.py", "--config", FIX / "models.yml", "capacity", "show", "--alias", "gemma4", cwd=ROOT)
    assert show.returncode == 1 and "no capacity card for gemma4" in show.stdout
    gemma = next(m for m in yaml.safe_load((FIX / "models.yml").read_text())["models"] if m["alias"] == "gemma4")
    depths = f"0,32768,{min(131072, gemma['ctx_size'] - 512)}"
    text = _asserts("quant-head-to-head")
    assert depths == "0,32768,130560"
    assert all(s in text for s in ("no capacity card", depths, "without --vram-available-mib", "make start-llama", "5 x 3 x 2 x 300 s"))


def test_eval_tune_fixtures_match_their_assertions() -> None:
    cfg = yaml.safe_load((FIX / "models.yml").read_text())
    pre = json.loads((FIX / "preflight-served.json").read_text())
    held = next(h["vram_mib"] for h in pre["gpu"]["holders"] if h["comm"] == "llama-server")
    assert cfg["llm_server"]["enabled"] is True and pre["gpu"]["used_mib"] - held + 512 == 6712
    assert all(s in _asserts("tune-capacity-card") for s in ("--below-mib 6712", "G-T1 (make llama-enable) is not asked"))
```

- [ ] **Step 2: Run them to verify they fail** with `uv run pytest tests/skills/onboard-local-model -q -k "eval_quant_fixtures or eval_tune"`. Expected: FAIL (the assertion texts are missing).

- [ ] **Step 3: Evals.** Both evals get `"files": ["evals/files/models.yml", "evals/files/preflight-served.json"]`.

`quant-head-to-head`. The prompt becomes: "compare Q4_K_M and Q5_K_M of the local model head to head and tell me which one to keep. The fixture config is .claude/skills/onboard-local-model/evals/files/models.yml (LLM_ENV_CONFIG; pass it to llmenv as --config; llm-server is serving its enabled gemma4) and preflight.sh already printed .claude/skills/onboard-local-model/evals/files/preflight-served.json; use that JSON as its output. DRY RUN for evaluation: read-only commands only (cat, yq, jq, uv run llmenv.py --config <that file> capacity show, the skill scripts' --help); do not download, stop or start containers, edit models.yml or start benchmark or agent runs. Give me the ordered plan with its gates and how long it would take. Answer in English." The expected_output becomes: "capacity show finds no card for gemma4, so the prerequisites run (P1 ladder lane, P2 interference, P3 G-T2 and bench-fit.sh at 0,32768,130560 with make model-capacity DEPTH) instead of a tune sweep; Q1 runs check without the flag while llm-server is stopped; G-Q1 and G-Q2 are separate gates; both quants run on one seed set through the router profile; the upper bound is stated; no fitting candidate ends with make start-llama; G-Q3 is the owner's call; Q11 restores gemma4 and builds my-coding-lite only if it existed before." The assertions, in order:
1. "The plan follows tuning.md Quant headroom: quant-headroom.sh check --alias gemma4, and only a candidate with fits true goes on (fits false or null is not fetched)"
2. "uv run llmenv.py --config <fixture> capacity show --alias gemma4 reports no capacity card, so the prerequisites run in order: P1 the gemma4 ladder lane while it is served (it writes seeds.json), P2 make model-interference while served, P3 G-T2 then bench-fit.sh --alias gemma4 --depths 0,32768,130560 and make model-capacity MODEL=gemma4 DEPTH=<bench json>; tune-sweep.sh and tune-apply.sh are not run"
3. "After P3 llm-server stays stopped, so Q1 runs quant-headroom.sh check without --vram-available-mib"
4. The current G-Q1/G-Q2 assertion (unchanged).
5. "Both quants run effort-run.sh on the P1 seeds.json through the router profile: the Q5_K_M lane passes --seeds with the same --efforts and --timeout"
6. "The lanes are compared with compare-runs.sh, gemma4's results.jsonl first, and its exit 3 (different challenge_set) is treated as not a head-to-head instead of comparing numbers"
7. The current upper-bound assertion (unchanged; it contains "5 x 3 x 2 x 300 s").
8. "When no candidate has fits true, the plan ends with make start-llama, because P3 stopped llm-server"
9.-11. The current G-Q3, Q11 and "Nothing was downloaded" assertions (unchanged, with `<a>` written as gemma4 in the Q11 one).

`tune-capacity-card`. The prompt becomes: "write the capacity card for the local model and set up my-coding-lite. The fixture config is .claude/skills/onboard-local-model/evals/files/models.yml (LLM_ENV_CONFIG; llm-server is serving its enabled gemma4) and preflight.sh already printed .claude/skills/onboard-local-model/evals/files/preflight-served.json; use that JSON as its output. DRY RUN for evaluation: read-only commands only (cat, yq, jq, the skill scripts' --help); do not stop or start containers, edit models.yml or start sweeps, benchmarks or ladders. Give me the ordered plan with its gates. Answer in English." Assertion 1 becomes "Preflight comes first, and G-T1 (make llama-enable) is not asked: llm_server.enabled is true in the fixture config". Assertion 2 becomes "G-T2 frees the GPU with make stop-llama and wait-gpu-release.sh --below-mib 6712 (13650 used - 7450 held by llama-server + 512) while System One stays up".

- [ ] **Step 4: Edit tuning.md (exact text)**

T8, lines 28-30: replace "The run writes `tmp/model-scout/<stamp>/effort/seeds.json`, the seed set every quant trial reuses. It is the first lane of the quant head-to-head: upper bound 5 x 3 x 2 x 300 s (layers x challenges per layer x tiers x timeout), stated before dispatch. The ladder is a benchmark run (`SKILL.md` rule 2), with its upper bound stated first." with "The run writes `tmp/model-scout/<stamp>/effort/seeds.json`, the seed set every quant lane reuses (Standalone quant comparison, `<seeds>`). The ladder is a benchmark run (`SKILL.md` rule 2): state its upper bound first (the effort-ladder reference, Running a lane)."

Lines 98-99: delete "Q1 runs either with llm-server serving `<a>` and T5's `vram_available_mib`, or after G-T2 with the flag omitted." (Q1 states it once.)

Replace lines 106-112 (the Standalone body) with:

```text
For "Q4_K_M vs Q5_K_M" or "is there room for Q5 or Q6" without a tuning run in this session. `<a>` is the enabled, served alias
(enabled but stopped: `make start-llama` first). Names: `<seeds>` is the `seeds.json` of `<a>`'s ladder lane and `<a-results>`
that lane's `results.jsonl`; `QR` is `tmp/model-tune/quant-<stamp>`, one stamp chosen before P3 or Q1; the verify depths are
`0,32768,min(131072, ctx - 512),ctx - 512` (ctx 131072 gives `0,32768,130560`). Gates: G-T2, G-Q1 before `fetch`, G-Q2 before
`add-entry`, G-Q3 for the verdict.

Prerequisites, in this order, each only when missing (a tuning run in this session leaves all three); `check` needs P2 and P3,
Q8 needs P1:
- P1. `<seeds>`: T8's lane of this session, or an earlier lane of `<a>` whose seeds no Q8 lane has used (on seeds of another
  generator `effort-run.sh` exits 2: then run this step). Else, while `<a>` is served, run T8's ladder command, `score.sh` and
  `make model-capacity MODEL=<a> SCORE=<score.json>`.
- P2. A passed interference gate: `uv run llmenv.py capacity show --alias <a>` has `interference` passed and not in
  `stale_sections`. Else `make model-interference` while `<a>` is served; a failed gate ends the comparison (report it).
- P3. A current depth section (`capacity show` says no capacity card, or `depth` is null or in `stale_sections`): Gate G-T2,
  then `bash $SK/scripts/bench-fit.sh --alias <a> --out QR/base --depths <verify depths>` (a benchmark run) and
  `make model-capacity MODEL=<a> DEPTH=<bench json>`. llm-server stays stopped for Q1.
T2 runs only when `llm_server.enabled` is false; T3-T6 (sweep and apply) only when the owner also asks for tuning.

Every lane is a head-to-head lane of the effort-ladder reference on `<seeds>` (the same `--efforts`, `--timeout` and layers,
none omitted), one at a time, its upper bound stated first. Order: the prerequisites; Q1; then Q2-Q11 for each candidate with
`fits: true`, in ascending size (Q11 always restores `<a>`); then G-Q3 once over every measured candidate.
```

Q1 (lines 114-121): "saved under `tmp/model-tune/quant-<stamp>/`" becomes "saved under `QR/`". Replace "where N is the `vram_available_mib` of the applied T5 row; without a T5 in this session, stop llm-server first (G-T2) and omit the flag, and the script then reads" with "with N the `vram_available_mib` of the T5 row applied in this session; without one, llm-server must be stopped (P3 did it; else Gate G-T2) and the flag omitted, and the script reads". Replace the closing "When the verdict is `no headroom for <quants> at ctx <N>, free MiB <X>`, it is in the card and the program ends. `fits: null` means unknown (RAM not measured, `n_cpu_moe` offload, sharded or absent file), never a guess." with "`fits: null` means unknown (RAM not measured, `n_cpu_moe` offload, sharded or absent file), never a guess. No candidate with `fits: true` (the verdict `no headroom for <quants> at ctx <N>, free MiB <X>`, or every `fits` false or null): the verdict is in the card; `make start-llama` when llm-server is stopped, then the program ends."

Q4: "Free the GPU (`SKILL.md`, Freeing the GPU), then" becomes "Free the GPU (`SKILL.md`, Freeing the GPU; already free after P3 or Q1's G-T2), then". Q5 becomes "Q5. The same flags and verify depths as `<a>`'s depth section: `bash $SK/scripts/bench-fit.sh --alias <b> --out QR/<Q> --depths <verify depths>`."

Q8 (lines 133-136) becomes:

```text
  - Q8. The ladder with the `--efforts` of `<a>`'s lane:
    `PI_CODING_AGENT_DIR=$HOME/.pi/agent bash $SK/scripts/effort-run.sh --model router-env/llama-cpp/<b> --out QR/<Q>/effort --efforts <same> --seeds <seeds>`,
    then `bash $SK/scripts/score.sh <results.jsonl>` into `<score.json>`, then `bash $SK/scripts/compare-runs.sh <a-results> <results.jsonl>`
    (Head-to-head of the effort-ladder reference).
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/skills/onboard-local-model -q -k "eval_ or references or prose or name_only"`, then `rg -n "quant-<stamp>/|5 x 3 x 2|not a head-to-head|T1-T8 first|main run" .claude/skills/onboard-local-model/references/tuning.md` (no hits; the intro's `quant-<stamp>-<pid>/` is the script's own run dir and stays).
Expected: PASS, no hits. Then run `make validate && make test`.

### Task 6: Scoring and runtimes: the bound stated once with `T`, the effort default, last-seen builds (+ stated-once guard)

**Files:**
- Modify: `.claude/skills/onboard-local-model/references/scoring.md:8-9`, `:25-27`, `:41-42`, `:65-66`
- Modify: `.claude/skills/onboard-local-model/references/runtimes.md` (after the table, line 11)
- Test: `tests/skills/onboard-local-model/test_onboard_local_model_scripts.py` (after `test_references_never_point_at_each_other`)

- [ ] **Step 1: Write the failing test**

```python
# A rule that lives in one file: copies drift (rounds 1-7 lost points to them). Phrases match after whitespace folding.
STATED_ONCE = {"5 x 3 x 2 x T": "references/scoring.md", "not a head-to-head": "references/scoring.md",
               "delta > 10": "references/scoring.md", "`ram.available_mib` (say so)": "SKILL.md"}
RETIRED_COPIES = ("5 x 3 x 2 x 300", "re-scouted with", "the onboard flow's step 2")


def test_shared_rules_are_stated_once() -> None:
    files = {"SKILL.md": SKILL_MD, **{f"references/{p.name}": p for p in sorted(REFS.glob("*.md"))}}
    for phrase, owner in STATED_ONCE.items():
        assert sorted(rel for rel, p in files.items() if phrase in _md(p)) == [owner], phrase
    assert not [(rel, c) for rel, p in files.items() for c in RETIRED_COPIES if c in _md(p)]
```

- [ ] **Step 2: Run it to verify it fails** with `uv run pytest tests/skills/onboard-local-model -q -k stated_once`. Expected: FAIL on `5 x 3 x 2 x T` (not written yet) and on the retired `5 x 3 x 2 x 300` in scoring.md.

- [ ] **Step 3: Edit**

- scoring.md:9: "(default `--efforts low,medium,high`)" becomes "(the script default, `--efforts low,medium,high`, is the full run; every lane of this skill passes `--efforts low,high` unless the owner asks for the full run, and all lanes of one head-to-head pass the same value)".
- scoring.md:25-27: replace "State the upper bound first: layers x per-layer x tiers x `--timeout`. `--timeout` follows the runner cap (300 s today, also the default; a larger value exits 2). Screen with `--efforts low,high` (`--per-layer 1`, the default): 5 x 1 x 2 x 300 s. Head-to-head with `--per-layer 3`: 5 x 3 x 2 x 300 s." with "State the upper bound first: layers x per-layer x tiers x T, where T is the lane's `--timeout` (default and maximum: the runner cap, 300 s today; a larger value exits 2). Screen with `--efforts low,high` (`--per-layer 1`, the default): 5 x 1 x 2 x T. Head-to-head with `--per-layer 3`: 5 x 3 x 2 x T (9000 s, 2.5 h, at T = 300 s). Once the lane starts, its first stderr line (`effort-run: <challenges> challenges x <efforts> efforts x <timeout> s upper bound`) is the exact bound: relay it."
- scoring.md:41-42: delete "The script prints its upper bound (`<challenges> challenges x <efforts> efforts x <timeout> s`) first; relay it before dispatching." (now in Running a lane).
- scoring.md:65-66: "Upper bound, stated before dispatch: 5 x 3 x 2 x 300 s (layers x per-layer x tiers x the runner cap) with `--efforts low,high`." becomes "Its upper bound, stated before dispatch: Running a lane."
- runtimes.md, after the table: add "These are last-seen values (2026-10-30), not facts for this run: a decision quotes only the `--version` output read in this run."

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/skills/onboard-local-model -q -k "stated_once or references or prose or eval_ or black_box"`.
Expected: PASS. The `quant-head-to-head` eval still asserts "5 x 3 x 2 x 300 s": T is 300 s today. Then run `make validate && make test`.

### Task 7: Trigger queries and the gateway-only eval (+ coverage test)

**Files:**
- Modify: `.claude/skills/onboard-local-model/evals/trigger-queries.json`
- Modify: `evals/evals.json` (`gateway-only-host`)
- Test: `tests/skills/onboard-local-model/test_onboard_local_model_scripts.py` (two new tests after `test_skill_description_fits_the_spec_limit`)

- [ ] **Step 1: Write the failing tests**

```python
# Every route the description promises has a positive; every Not-for boundary has a near-miss negative.
TRIGGER_ROUTES = {"alias": r"models\.yml", "hf": r"huggingface\.co", "gguf": r"\.gguf", "quant": r"\bq[4-6]\b",
                  "challenge": r"ladder challenge", "depth": r"tok/s", "card": r"capacity card", "anchor": r"haiku|luna|composer",
                  "runtime": r"llama\.cpp image", "system one": r"laya is", "scout": r"quepa|fits"}
TRIGGER_BOUNDARIES = {"omniroute": r"omniroute", "combo": r"\bcombo\b", "memory limits": r"memory cap", "gpu-status": r"gpu-status",
                      "laya judgment": r"ask laya", "cloud": r"openrouter", "sampling": r"temperature", "fine-tuning": r"fine-tune",
                      "quantizing": r"quantize", "challenge": r"challenge"}


def test_trigger_queries_are_balanced_and_cover_routes_and_boundaries() -> None:
    rows = json.loads((SKILL / "evals" / "trigger-queries.json").read_text())
    pos = [r["query"].lower() for r in rows if r["should_trigger"]]
    neg = [r["query"].lower() for r in rows if not r["should_trigger"]]
    assert 8 <= len(pos) <= 12 and 8 <= len(neg) <= 12 and len(set(pos + neg)) == len(rows)
    assert not [k for k, rx in TRIGGER_ROUTES.items() if not any(re.search(rx, q) for q in pos)]
    assert not [k for k, rx in TRIGGER_BOUNDARIES.items() if not any(re.search(rx, q) for q in neg)]


def test_eval_gateway_host_fixtures_match_their_assertions() -> None:
    cfg = yaml.safe_load((FIX / "models-gateway.yml").read_text())
    pre = json.loads((FIX / "preflight-gateway.json").read_text())
    assert cfg["llm_server"]["enabled"] is False and pre["services"]["llm_server_enabled"] is False
    assert all(s in _asserts("gateway-only-host") for s in ("llm_server.enabled false", "5 x 1 x 3 x 300 s", "5 x 1 x 2 x 300 s"))
```

- [ ] **Step 2: Run them to verify they fail** with `uv run pytest tests/skills/onboard-local-model -q -k "trigger_queries or eval_gateway_host"`. Expected: FAIL. The alias and challenge routes are missing, the Laya-judgment and challenge negatives are missing, and so are the eval texts.

- [ ] **Step 3: Edit trigger-queries.json** (resulting counts: 11 positive, 11 negative)

- Replace positives 6 and 7 (0-based 5 and 6) with one entry: `{"query": "pi crawls in long sessions since the 256k change: what tok/s do we actually get at 128k depth? then write the capacity card and set up my-coding-lite", "should_trigger": true}`.
- Add two positives after it: `{"query": "swap the local model to ornith-35b from models.yml and tell me if it actually beats gemma4", "should_trigger": true}` and `{"query": "generate one layer-3 ladder challenge with seed 7 and check it is intact before I hand it to pi", "should_trigger": true}`.
- Replace the negative "pi can't reach the router, check-server returns 401 after I rotated the key" with `{"query": "ask laya whether this commit message matches its diff, just a yes or no", "should_trigger": false}`. Add `{"query": "write a coding challenge for our interview loop: an LRU cache kata with a hidden test suite", "should_trigger": false}`.

- [ ] **Step 4: Edit the `gateway-only-host` eval.** The prompt becomes: "quiero correr el benchmark de effort low/medium/high del modelo nuevo ahora mismo. The fixture config is .claude/skills/onboard-local-model/evals/files/models-gateway.yml (scripts read it through LLM_ENV_CONFIG) and preflight.sh already printed .claude/skills/onboard-local-model/evals/files/preflight-gateway.json; use that JSON as its output. DRY RUN for evaluation: read-only commands only; do not start anything. Tell me what is possible on this host as it is configured and what I must approve first. Answer in English." Add `"files": ["evals/files/models-gateway.yml", "evals/files/preflight-gateway.json"]`. Assertion 1 becomes "The answer reads llm_server.enabled false from the fixture config (preflight services.llm_server_enabled false) and reports a gateway-only host". Assertion 3 becomes "The answer separates what works now (bench-fit.sh throughput in a throwaway container) from what needs the stack". Assertion 5 becomes "The requested full run (low, medium and high) is bounded by 5 x 1 x 3 x 300 s (layers x challenges per layer x tiers x timeout), and the answer offers the quicker screen --efforts low,high (5 x 1 x 2 x 300 s)".

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/skills/onboard-local-model -q -k "trigger_queries or eval_ or name_only"`. Expected: PASS. Then run `make validate && make test`.

### Task 8: Verification, route walk, cleanup, index row

**Files:**
- Modify: `docs/superpowers/plans/README.md` (this plan's row only; the rows of plans G and H stay as they are)

- [ ] **Step 1: Full checks.** Run `make validate && make test` and `uv run pytest tests/skills -q`. Expected: all green. The skill suite gains 9 test functions (14 cases): Task 1 two (7 cases), Tasks 2, 3 and 6 one each, Tasks 5 and 7 two each.
- [ ] **Step 2: Guard and cap.** Run `uv run pytest tests/skills/onboard-local-model -q -k "black_box or router or references_never or route_table or stated_once"`, `wc -c .claude/skills/onboard-local-model/SKILL.md` and `rg -n -i "ai-kit" .claude/skills/onboard-local-model`. Expected: PASS, <= 11,000 bytes, and no hits.
- [ ] **Step 3: Route walk (offline, scratch under `tmp/r7-walk/`, removed at the end).** Read each route as the agent would. Every placeholder must be defined, every named section must exist, and every branch must end in a determined outcome. Run the commands with `LLM_ENV_CONFIG=$SK/evals/files/models.yml LLM_ENV_MODELS_DIR=$SK/evals/files/models` and `HF_FIXTURE_DIR=$SK/evals/files/hf`.
  1. **Model given.** Run `resolve-target.sh` on `gemma4` (alias, served: Alias list, 7a only `make start-llama`), `ornith-35b` (alias, `present: false`: G1 downloads the url, size check only), `$SK/evals/files/Ornith-Q5_K_M.gguf` (gguf_file: alias `ornith-2` at 7a), `nope.gguf` (exit 2: report and stop) and `Zeta-Coder-7B` (exit 3: Find the repo). **HF file URL:** `resolve-target.sh 'https://huggingface.co/lab/Foo-9B-GGUF/resolve/v2/Foo-9B-Q4_K_M.gguf?download=true'` gives kind `hf_file` and revision `v2`. Then `hf-scout.sh --repo lab/Foo-9B-GGUF --revision v2 --file Foo-9B-Q4_K_M.gguf --json --budget-mib 8321 --ram-mib 12000` keeps the named Q4_K_M (5500 MiB) with sha `a1b2...` and `pick.sha256`. The HF list items 1-5 then apply: alias `foo-9b`, `curl -o <models_dir>/Foo-9B-Q4_K_M.gguf`, the sha256 check, the action on a mismatch, `resolve-target.sh gemma4 --scratch-config tmp/r7-walk/s.yml`, then steps 5, 7a items 1-7 (no `make benchmark`), 7c items 1-5 and step 8 (a), (b), (c). On the gateway fixture, `--ram-mib 14500` (rule 1 RAM) gives the same pick, and there is no baseline.
  2. **None given.** Scout and rank: the baseline comes from step 1 itself (no onboard-flow load), hf-scout calls take `<rule 1 RAM>`, rank-candidates returns at most 6, evidence cards `<rank>.json`, at most 3 go on as scout picks on one seed set.
  3. **Compare quants.** Standalone: `uv run llmenv.py --config $SK/evals/files/models.yml capacity show --alias gemma4` reports no card, so P1, P2 and P3 run (verify depths `0,32768,130560`). Q1 runs without the flag (llm-server stopped by P3). No `fits: true` ends with `make start-llama`. Q2-Q11 use `QR` and `<seeds>`; Q8 compares `<a-results>` first; G-Q3 under bypass keeps `<a>`.
  Expected: no undefined name, and `rg -n "exits 2\)|sections 1-5 and 7|re-scouted|5 x 3 x 2 x 300" .claude/skills/onboard-local-model/SKILL.md .claude/skills/onboard-local-model/references` prints nothing.
- [ ] **Step 4: Cleanup.** `podman ps -a` shows nothing started by this plan. Remove `tmp/r7-walk/` and any other scratch this run created under `./tmp/` (the tests clean their own `_out` dirs). Do not touch `tmp/judges/`.
- [ ] **Step 5: Index.** Set this plan's row in `docs/superpowers/plans/README.md` to **DONE** with the execution date, keeping "skill edits uncommitted with the untracked skill tree; judges re-run by the orchestrator". Leave the rows of plans G and H unchanged. Commit nothing unless the owner asks.

## Round-1 review outcome (approved, 1 confirmed high, 4 mediums)

Review round 1 (native + referee-two, lead-verified; tmp/plan-i-review/review-round-1.md) found no critical and one confirmed high, in a
verification step; three referee highs were refuted or downgraded. Plan status: approved for execution. The implementer must apply:

- HIGH (Task 8 step 3): `rg "exits 2\)"` cannot print nothing, because challenges.md:118 and scoring.md (also after Task 6) correctly say
  "a larger value exits 2)". Match `resolve-target.sh` exits 2 instead of `exits 2\)` in that `rg` pattern.
- M1 (Task 5 P1, owner decision 6): seed reuse accepts only seeds never used in a finished comparison, consistent with the effort-ladder
  reference's "retire the set" and the SKILL.md NEVER rule. P1 reads: "`<seeds>`: T8's lane of this session, or an earlier lane of `<a>`
  whose seeds were never in a finished comparison (no `compare-runs.sh` and no Q8 lane used them; on seeds of another generator
  `effort-run.sh` exits 2: then run this step). When in doubt, run this step." Owner decision 6 is read the same way.
- M2 (Task 2 step 3, lines 74-75): the replacement is "Each gate is its own question and a yes to one never covers the next; a no to G1-G3
  or G5 runs the flow's rollback (`onboard-flow.md` step 9), a no to any other gate skips that step." (G4, G-Q1, G-Q3 and G6: skip or keep;
  tuning.md Rollback is only for a T5 apply.)
- M3 (Tasks 2 and 3): a failure before G2 has its own case. Task 2's step 9 insertion reads "A declined G1 or G2, or a failure before G2
  (a download size or sha256 mismatch): report it, remove the scratch files and stop; no `make stop-llama` (G2 has not stopped it)."
  Task 3's download sentence ends "A mismatch: delete that file, report both values, remove the scratch files and stop, as after a
  declined G1 (step 9; no `make stop-llama`)."
- M4 (Task 6): challenges.md:135 holds a fourth copy of the bound, "upper bound 5 x 5 x 2 x 300 s". It becomes "upper bound
  5 x 5 x 2 x T (the effort-ladder reference, Running a lane)" (challenges.md joins Task 6's files), and `RETIRED_COPIES` gains
  `"x 300 s"` so the stated-once guard catches any hardcoded product (scoring.md's "at T = 300 s" does not match). The Task 6 red step
  then also fails on challenges.md.
