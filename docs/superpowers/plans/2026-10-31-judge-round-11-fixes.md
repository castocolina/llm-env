# Judge Round 11 Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the confirmed findings of round 11 of the onboard-local-model skill judges (native 88.8 % B, 5 medium and 6 low; referee 87.2 % B, its two highs refuted by the lead, one medium confirmed in part, 3 lows) and plan L's executor residuals, so that the next round gives >= 90 % from both lanes with no critical or high finding.

**Architecture:** Five native mediums are real dead ends or contradictions on routes the judges walked:
- a local GGUF is copied before its authoritative fit;
- a quoted `~` path that exists is rejected;
- H2/H3 ask for a "largest fitting" quant that `hf-scout.sh` cannot report;
- `make start` on a down stack has no gate;
- two routes have no eval.

The referee's big loss (D7 9/15) is the `(Eval setup: ...)` tail on 25 of 29 eval prompts. That tail is now flagged three rounds running (r9 D7-2, r10 D7-1, r11 D7-1), so this plan moves it into a `setup` field.

Two script changes are red-first: `resolve-target.sh` (tilde, `refs/pr/N`) and `hf-scout.sh` (`split`, `--single-file`). Everything else is prose, fixtures and evals, each pinned by a test. `SKILL.md` stays a router under its 11,000-byte cap: the description rewrite and a shorter route-gate sentence pay for the H-as-sum rule.

**Tech Stack:** bash, jq, yq; pytest (`tests/skills/onboard-local-model/`); skill-judge rubric v4.

**Spec:** `tmp/judges/r11-native-summary.txt` and `tmp/judges/r11-referee-summary.txt` (the referee file holds the lead's check of its findings). Plan L (`docs/superpowers/plans/2026-10-31-judge-round-10-fixes.md`, executed 2026-11-01) is the base state, with its executor's residual lows. Read-only runs on 2026-11-01, each listed in the ledger, reproduced the tilde rejection, the `refs/pr/1` misparse and the Q6_K split pick. The planned eval figures came from the current `hf-scout.sh` run on scratch copies of the planned fixtures. The `SKILL.md` edits of Task 3 were applied to a scratch copy and measured: 10,985 -> 10,980 bytes, description 938 -> 939 characters.

## Global Constraints

- English only. Edit only `.claude/skills/onboard-local-model/**`, `tests/skills/onboard-local-model/**` and this plan's row in `docs/superpowers/plans/README.md` (added with the plan; Task 6 updates its status). The skill tree and `tests/skills` are untracked and stay uncommitted. No tracked code changes, so nothing is committed. The README rows of plans G-L stay as they are.
- `SKILL.md` <= 11,000 bytes (`SKILL_MD_MAX_BYTES`, tested). Never raise the cap. Shorten before adding; move text into references.
- A reference never names another reference file (`test_references_never_point_at_each_other`). Inside a reference, write "the effort-ladder reference", "the Local GGUF reference", "the research brief".
- `jev-fast-decision` is a black box, and ai-kit is never named in the skill tree. The guard `test_skill_and_touched_docs_treat_jev_as_a_black_box` scans `SKILL.md`, the references, every JSON under `evals/` and every script.
- Prose holds no shell logic: a backtick span holds at most two of `;`, `&&`, `||` (`test_skill_prose_has_no_long_shell_logic`). Every `--out` is named once (`test_every_benchmark_out_dir_is_named_once`).
- Every script keeps `--help` (exit 0) and its tests. A behaviour change is red-first: write the test, watch it fail, then implement. Doc and fixture tests are written first too and fail until the text lands.
- After a `.sh` edit run `make validate`. After a `.py` edit (test files included) run `make validate && make test`. Run focused pytest files one at a time, one file per command.
- After Task 5, an eval `prompt` is the user's message alone. Fixture paths, environment variables and the read-only rule go in its `setup` (`test_eval_prompts_read_as_user_turns`).
- Scratch files go under `./tmp/`, and the run that creates them removes them. No containers, no GPU, no network, no downloads, no edits to the live `models.yml` or to any capacity card.

## Findings ledger (verified 2026-11-01 against the files)

| Finding | Verdict | Task |
|---|---|---|
| native D8-1 (medium): local-gguf.md:31 runs the authoritative fit after a possibly 20 GB copy | confirmed. L5 maps "L3 at 4.4, L4 at G1 (4.3)", and onboard-flow.md:110 (4.3 G1) precedes :121 (4.4 budget). Also: L3 before 4.2 would measure the scratch entry's copied `ctx_size`, so L3 must run after 4.2 and before G1 | 4 |
| native D8-2 (medium): resolve-target.sh:66 rejects a quoted `~/.../x.gguf` that exists | confirmed by a run: `HOME=<dir> resolve-target.sh '~/Downloads/Bar-7B-Q4_K_M.gguf'` exits 2, "not found ... did you mean <dir>/Downloads/Bar-7B-Q4_K_M.gguf?" (the same file) | 1 |
| native D8-3 (medium): onboard-flow.md:50-55, H2/H3 "largest fitting quant" cannot be read from `hf-scout.sh` | confirmed by a run on the sub-folder fixture, budget 8321 and no `--file`: `fit: "no"`, `pick` `Q6_K/...-00001-of-00002.gguf` (9346 MiB, `sha256` null). hf-scout.sh:118 falls back to the largest offloadable quant, and `quants` strings carry no split flag | 2 |
| native D6-1 (medium): served-alias.md:20 runs `make start` ungated when the stack is down | confirmed. scripts/start.sh:39-40 makes `make start-llama` die with "the stack is not running; run 'make start'". The same ungated phrase is in onboard-flow.md:173 (7a.6), where G3 is asked but its facts do not name the whole stack | 4 |
| native D7-1 (medium): no eval for a sub-folder HF file URL or a quoted `~` path; incomplete sub-folder model fixture | confirmed. `model-yuxinlu1__...json` holds only `sha` and `gguf.total`, so `hf-scout.sh --repo` reads the id `null` and reports `fit: "unknown"`. Its tree exists only under the `@<sha>` name that `quant-headroom.sh` reads, while an `hf_file` URL at `main` makes `hf-scout.sh` read `@main` | 5 |
| native D1-1 (low): repeated "Gates, rules 1-3 ... are in `SKILL.md`" preambles; dated tables | taken. The clause goes from the 8 references that carry it and from tuning.md's variant; scoring.md keeps its own preamble, which names the rules it relies on. The dated tables in anchors.md and runtimes.md go under headings that carry their date and "re-check" | 4 |
| native D2-1 (low): gate codes cross-referenced across files | dropped: every flow already names a gate with its purpose where it is asked (served-alias R3 "Gate G2: free the GPU", R5; onboard 4.3, 5.4, 7a, 9; tuning T2, T3, T5). The step codes are checklist items | - |
| native D3-1 (low): "Never run `make benchmark`" sits in Gotchas | taken: it moves into NEVER. onboard 7a.5 and tuning T6 point at `SKILL.md` NEVER | 3 |
| native D4-1 (low): description 939/1024, a dense keyword list | taken, merged with referee D4-1: one rewrite that names its boundaries precisely (939 characters, all route words kept) | 3 |
| native D5-1 (low): files named indirectly | partly. H4 names section 7 and the Route row that loads it. "The effort-ladder reference" stays: `SKILL.md` defines that name, and references never name each other (a tested design rule) | 4 |
| native D8-4 (low): resolve-target.sh:80 parses `.../resolve/refs/pr/1/x.gguf` as revision `refs` | confirmed by a run (`revision "refs"`, `file "pr/1/Foo-9B-Q4_K_M.gguf"`) | 1 |
| native count | the summary says "Low: 7" but lists six; six are handled here | - |
| referee D7-1 (high): `(Eval setup: ...)` tails in eval prompts | refuted as high by the lead (D7 9 is in the 9-11 band; 25 of 29 prompts, not 28). The tail itself is real and three rounds old, so it is fixed structurally: a `setup` field per eval and a top-level `harness` note | 5 |
| referee D8-1 (high): quant route dies at Q1 without a card | refuted by the lead: `capacity show` fails only on a missing card, and P2's `make model-capacity ... DEPTH=` (or P1's interference gate) writes one | - |
| referee D8-2 (medium): A2 and A3 disagree | refuted by the lead (the rules compose; `gguf-alias-collision` expects `ornith-2`). Its optional low is taken: the parenthetical "(another quant of that model)" goes, because the test is name equality | 4 |
| referee D8-3 (medium): SKILL.md:75-76 takes "the entry" whose `comm` is `llama-server` | confirmed in part. In router mode the host lists two `llama-server` holders (the lead read `vram_mib` 0 and 12), so H is their sum. Scripts checked: `wait-gpu-release.sh` takes a computed target and reports every holder, `gpu-sample.sh` reads the device total, and `latency-profile.sh` already sums its container's processes. None assumes one holder | 3 |
| referee D1-1 (medium): challenges.md catalogs restate the generator | refuted as medium by the lead (repo-specific facts, a gated file, needed before `--check-library`). Optional low not taken, for the same reason | - |
| referee D4-1 (low): boundary words collide ("sampling", "quantizing", "other combos") | taken with native D4-1 | 3 |
| referee D6-1 (low): no single command for the ROCm A/B arm | dropped: a safe command needs a trap, which is shell logic that belongs in a script, and verifying it needs a GPU run. This plan runs no GPU work; the runtimes Gotchas already warn | - |
| referee D2-1 (low): T5 should require > 3 % over the current row | dropped: no sweep row holds the live configuration, `tune-pick.sh` ranks context first by design, and T5's apply is already gated by G-T3 on the pick's numbers | - |
| plan L residual: R4's reject sends a server found stopped through `make start-llama` and then R6's `make stop-llama` | confirmed (served-alias.md:31-32, :39) | 4 |
| plan L residual: anchors.md item 5 (haiku 401 on 2026-10-30) against the anchor fixture (haiku auth ok, luna 401) | confirmed. anchors.md is host evidence and stays. The fixture gets a dated header that names the difference | 5 |

## Owner decisions (defaults chosen)

1. **Eval setup structure.** Each eval keeps `prompt` (the user's message alone), adds `setup` (the old tail, moved verbatim) and keeps the spec's `files`. A top-level `harness` string says that a harness passes `setup` to the run apart from the prompt. Evals with no fixture files have no `setup`.
2. **Split flag.** Every `hf-scout.sh` quant object, and so `pick`, carries `split` (true when the quant has more than one shard). `--single-file` drops split quants before the fit and the pick, and it excludes `--file` (exit 2).
3. **H2/H3 fallback.** The fallback is H1's call without `--file`, with `--single-file` and `--revision` kept. Its `pick` is offered only when its `fit` is `vram`. Any other `fit` means "no single-file quant of 3.5 bpw or more fits in VRAM": say so and offer none. A low-bit or offload pick is never offered by the fallback.
4. **Tilde.** Only a leading `~` or `~/` expands, to `$HOME`, as the shell would for the current user. `~user/...` stays literal and gets the existing exit-2 message.
5. **`refs/pr/N`.** Handled, not rejected: the revision becomes `refs%2Fpr%2FN`, the form `hf-scout.sh --revision` already accepts and requests (`test_hf_scout_requests_the_revision_and_the_tree_at_its_commit`). A pasted URL that is already encoded parses the same way today. The live API was not queried (no network). If it ever refuses the encoded form, `hf-scout.sh` exits 2 naming the revision, which is still a clear message.
6. **Gate G-R1** (served-alias R1) is asked before `make start` when `make start-llama` says the stack is not running. Its facts: every stack container that is down comes up, System One reloads its weights, and R6 hands back only llm-server. Default: ask. A no reports the capacity card as it stands and stops, with nothing measured. Under bypass the `SKILL.md` rule applies: announce, then run. `make start` starts containers; it never deletes anything or switches a model, so it is not one of the G5/G-Q3 exceptions. In onboarding, 7a.6 adds no gate: G3 already covers serving, and its facts name `make start` when step 1 found the stack down.
7. **H = sum** is a prose rule in `SKILL.md`; no script changes. `preflight-served.json` gains the router's `llama-server` holder (12 MiB, listed first, as on the host), with `used_mib` 13662. The evals' 6712 then needs the sum, and a first-entry reading gives 14162.
8. **Local GGUF fit order.** For a local GGUF only, 4.4 runs after 4.2 and before G1, on the file's own directory. The Hugging Face and alias routes keep G1 first, because `validate-gguf` needs the file on disk.
9. **Description.** The merged text of Task 3: the referee's boundaries ("OmniRoute combos except my-coding-lite", "temperature/top_p", "LoRA fine-tuning", "converting safetensors to GGUF"), every route word kept, 939 characters.
10. **Anchors.** anchors.md is unchanged. The fixture `anchors/route-check.txt` starts with a dated `#` line that names the 2026-10-30 host evidence.

## Review Focus

1. `~user/...` or a bare `~`. Expected: exit 2 with the existing messages, never a crash or a search. Pinned in Task 1 (`~nobody-r11/...` in the exit-3 parametrize; bare `~` with `HOME` set in the tilde test).
2. A `refs/pr/N` URL whose file sits in a sub-folder. Expected: revision `refs%2Fpr%2FN`, and the file keeps its folder. Pinned in Task 1.
3. `--single-file` on a repo whose every quant is split. Expected: `fit: "no"`, `pick` null, exit 0, so the fallback reports that none fits. Pinned in Task 2.
4. The stack is down during a re-measure, with bypass on or off. Expected: G-R1 before `make start`; a no ends read-only; R6 restores only llm-server. Pinned in Task 4 (served-alias test).
5. A local GGUF whose fit is infeasible or errors. Expected: no copy is offered, and the original is untouched. Pinned in Task 4 (local-gguf test) and Task 5 (`quoted-tilde-gguf` eval test).

---

### Task 1: `resolve-target.sh` expands a quoted `~` and maps a `refs/pr/N` revision

**Files:**
- Modify: `.claude/skills/onboard-local-model/scripts/resolve-target.sh` (header :4-9, after :42, :82-83, :103)
- Test: `tests/skills/onboard-local-model/test_onboard_local_model_scripts.py` (`test_resolve_target_parses_hugging_face_urls` :1740, the parametrize of `test_resolve_target_exits_3_only_for_a_bare_model_name` :1832, a new test after `test_resolve_target_checks_a_local_gguf_file` :1758)

**Interfaces:**
- Produces: a `MODEL` of `~` or `~/...` is classified as `$HOME<rest>`. A `resolve|blob/refs/pr/<N>/<path>.gguf` URL gives `{kind: "hf_file", revision: "refs%2Fpr%2F<N>", file: "<path>.gguf"}`. Task 5's `quoted-tilde-gguf` eval relies on the first, and H1's `--revision <revision>` passes the second unchanged.

- [ ] **Step 1: Write the failing tests**

In `test_resolve_target_parses_hugging_face_urls`, after the `blob/{rev}` assert:

```python
    pr = {"kind": "hf_file", "repo": "o/r", "revision": "refs%2Fpr%2F1", "file": "m-Q5_K_M.gguf", "quant": "Q5_K_M"}
    assert _rt_json(tmp_path, "https://huggingface.co/o/r/resolve/refs/pr/1/m-Q5_K_M.gguf") == pr
    assert _rt_json(tmp_path, "https://huggingface.co/o/r/blob/refs%2Fpr%2F1/m-Q5_K_M.gguf") == pr  # the web UI's encoded form
    assert _rt_json(tmp_path, "https://huggingface.co/o/r/resolve/refs/pr/12/sub/m-Q5_K_M.gguf") == pr | {"revision": "refs%2Fpr%2F12", "file": "sub/m-Q5_K_M.gguf"}
```

Add `("~nobody-r11/Foo-9B-Q4_K_M.gguf", 2, None)` to the parametrize list of `test_resolve_target_exits_3_only_for_a_bare_model_name`. It already passes: this is a regression case for the literal `~user` form. Then add the new test:

```python
def test_resolve_target_expands_a_quoted_tilde_before_classifying(tmp_path: Path) -> None:
    gguf = tmp_path / "Downloads" / "Bar-7B-Q4_K_M.gguf"
    gguf.parent.mkdir()
    gguf.write_bytes(GGUF_BYTES)
    env = _rt_env(tmp_path) | {"HOME": str(tmp_path)}

    def rt(model: str) -> subprocess.CompletedProcess[str]:
        return _run("bash", SCRIPTS / "resolve-target.sh", model, env=env, cwd=tmp_path)

    found = rt("~/Downloads/Bar-7B-Q4_K_M.gguf")  # a quoted path reaches the script with its ~ unexpanded
    assert found.returncode == 0, found.stderr
    assert (json.loads(found.stdout)["kind"], Path(json.loads(found.stdout)["path"])) == ("gguf_file", gguf.resolve())
    near = rt("~/Downloads/Bar-7B-Q4_K_S.gguf")
    assert near.returncode == 2 and f"did you mean {gguf}?" in near.stderr
    bare = rt("~")
    assert (bare.returncode, bare.stdout) == (2, "") and "not a .gguf file" in bare.stderr
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/skills/onboard-local-model/test_onboard_local_model_scripts.py -q -k "hugging_face_urls or quoted_tilde or exits_3_only"`
Expected: FAIL. `refs/pr/1` gives revision `refs`, and the tilde path exits 2 "not found". The encoded-URL assert and the `~nobody-r11` case already pass.

- [ ] **Step 3: Implement in `resolve-target.sh`**

- After `[ -n "$model" ] || { echo "MODEL is required" ...; }` (:42):

```bash
# a quoted ~ arrives unexpanded; expand it as the shell would for the current user (never ~user)
case "$model" in \~|\~/*) model="$HOME${model:1}" ;; esac
```

- :103 becomes `p="$model"`, because the expansion now happens once at the top. :110's `"$model" == "~"*` test stays, for `~user`.
- After the `[[ "$repo" =~ $repo_re ]] || fail ...` line (:83), which overwrites `BASH_REMATCH`:

```bash
        # a pull-request ref holds slashes; hf-scout.sh --revision takes it URL-encoded, as the Hub's web URLs show it
        if [[ "$rev/$file" =~ ^refs/pr/([0-9]+)/(.+)$ ]]; then rev="refs%2Fpr%2F${BASH_REMATCH[1]}"; file="${BASH_REMATCH[2]}"; fi
```

- Header: at the start of the MODEL paragraph (:4), add "A leading ~ or ~/ is first expanded to $HOME (a quoted path arrives unexpanded)." In (2), after "(query string dropped)", add "; a refs/pr/N revision becomes refs%2Fpr%2FN".

- [ ] **Step 4: Run the tests and validate (no commit)**

Run: `uv run pytest tests/skills/onboard-local-model/test_onboard_local_model_scripts.py -q -k "resolve_target or eval_"`, then `make validate && make test`.
Expected: PASS and green. `test_resolve_target_exits_3_only_for_a_bare_model_name` still passes `~/no-such-dir-r8/Foo-9B`: the expanded path starts with `/`, so it is path-like and exits 2.

### Task 2: `hf-scout.sh` marks split quants and gains `--single-file`; H2/H3 read the fallback

**Files:**
- Modify: `.claude/skills/onboard-local-model/scripts/hf-scout.sh` (header :3, :12-14; :19, :31-42; the jq program :97-118)
- Modify: `.claude/skills/onboard-local-model/references/onboard-flow.md:46-56` (H2, H3)
- Test: `tests/skills/onboard-local-model/test_onboard_local_model_scripts.py` (a new test after `test_hf_scout_file_takes_a_bare_name_in_a_sub_folder` :2281; `test_onboard_step_4_writes_the_settings_into_the_scratch_candidate` :1057; `RETIRED_COPIES` :975)

**Interfaces:**
- Produces: every quant object, and so `pick`, carries `split: bool`. `--single-file` restricts `fit`, `pick` and `quants` to single-file quants, and with `--file` it exits 2. Task 5's `hf-subfolder-no-fit` eval calls `--single-file`, and its test reads `pick.split`.
- Produces: the H2 bullet "Fallback: ..." that H3 points at as "the fallback (H2)".

- [ ] **Step 1: Write the failing tests**

```python
def test_hf_scout_single_file_skips_split_quants_and_marks_split(tmp_path: Path) -> None:
    for repo, tree in (("lab__x", (("m-Q4_K_M.gguf", 5000, "4" * 64), ("Q8_0/m-Q8_0-00001-of-00002.gguf", 3000, "1" * 64),
                                   ("Q8_0/m-Q8_0-00002-of-00002.gguf", 3000, "2" * 64))),
                       ("lab__y", (("m-Q6_K-00001-of-00002.gguf", 2000, "1" * 64), ("m-Q6_K-00002-of-00002.gguf", 2000, "2" * 64)))):
        (tmp_path / f"model-{repo}.json").write_text(json.dumps(_repo(repo.replace("__", "/"), 8, 262144, "qwen35")))
        (tmp_path / f"tree-{repo}.json").write_text(json.dumps(_lfs_tree(*tree)))
    env = {"HF_FIXTURE_DIR": str(tmp_path), "PATH": "/usr/bin:/bin"}

    def scout(repo: str, *extra: str) -> subprocess.CompletedProcess[str]:
        return _run("bash", SCRIPTS / "hf-scout.sh", "--json", "--budget-mib", "10369", "--ram-mib", "14000", "--repo", repo, *extra, env=env)

    (plain,) = json.loads(scout("lab/x").stdout)
    assert (plain["fit"], plain["pick"]["file"], plain["pick"]["split"]) == ("vram", "Q8_0/m-Q8_0-00001-of-00002.gguf", True)
    (single,) = json.loads(scout("lab/x", "--single-file").stdout)
    assert (single["fit"], single["pick"]["file"], single["pick"]["split"], single["quants"]) == ("vram", "m-Q4_K_M.gguf", False, ["Q4_K_M:5000"])
    none = scout("lab/y", "--single-file")
    assert none.returncode == 0 and [(r["fit"], r["pick"], r["quants"]) for r in json.loads(none.stdout)] == [("no", None, [])]
    both = scout("lab/x", "--single-file", "--file", "m-Q4_K_M.gguf")
    assert both.returncode == 2 and "--single-file" in both.stderr and not both.stdout
```

In `test_onboard_step_4_writes_the_settings_into_the_scratch_candidate`, replace `assert "largest fitting single-file quant" in _section(flow, "H2. ", "H3. ")` with:

```python
    h2 = _section(flow, "H2. ", "H3. ")
    assert all(s in h2 for s in ("`pick.split` true", "with `--single-file`", "When its `fit` is `vram`", "largest fitting single-file quant",
                                 "offer none"))
    assert "the fallback (H2)" in _section(flow, "H3. ", "H4. ")
```

`RETIRED_COPIES` gains `"largest fitting quant (H1's call"`.

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/skills/onboard-local-model/test_onboard_local_model_scripts.py -q -k "single_file or scratch_candidate or stated_once"`
Expected: FAIL (`unknown flag: --single-file`, no `split` key, old H2/H3 text).

- [ ] **Step 3: Implement in `hf-scout.sh`**

- `single=0` joins the defaults line (:19), and the flag loop gains `--single-file) single=1; shift ;;`.
- After the `--repo`/`--query` check block (:37-42): `[ "$single" = 0 ] || [ -z "$file" ] || { echo "--single-file and --file exclude each other" >&2; exit 2; }`.
- The jq call gains `--argjson single "$single"`. The quant object (:102-104) gains `split: (length > 1)` after `sha256`. After `| map(select($f != "" or $p == 0 or .bpw >= 1.5))` (:107), add `| map(select($single == 0 or (.split | not)))`.
- Header: usage (:3) gains `[--single-file]` after the `--repo` group. After the `--file PATH` sentence (:12), add "--single-file drops split (sharded) quants before the fit and the pick, so fit, pick and quants answer for single-file quants only; it excludes --file (exit 2)." In :13-14, "carries bytes (...) and sha256 (...)" becomes "carries bytes (exact sum of the shard sizes), sha256 (lfs.oid when the quant is one file, else null) and split (true when the quant has more than one shard)".

- [ ] **Step 4: Rewrite H2 and H3 in onboard-flow.md**

Everything from H2's first bullet (`fit: "unknown"`) through H3's first bullet (`fit: "no"`) is replaced by:

```text
    - `fit: "unknown"` (the tree was unreadable): retry once, else report and stop, because G1 needs the size.
    - A null `sha`: stop before G1.
    - `pick.split` true or a null `pick.sha256` (an entry takes one checksummed file): stop before G1 and report the fallback.
      Go on with it only when the owner asks after that report.
    - Fallback: H1's call without `--file`, with `--single-file` (`--revision` kept). When its `fit` is `vram`, its `pick` is
      the repo's largest fitting single-file quant. Any other `fit` means no single-file quant of 3.5 bpw or more fits in
      VRAM: say so and offer none.
H3. **Fit gates.**
    - `fit: "no"`: stop before G1, under bypass too. Report the fit math and the fallback (H2). Download the fallback only when
      the owner asks after that report.
```

The second H3 bullet (card, `bpw`, `ctx` gates) stays.

- [ ] **Step 5: Run the tests and validate (no commit)**

Run: `uv run pytest tests/skills/onboard-local-model/test_onboard_local_model_scripts.py -q -k "hf_scout or scratch_candidate or stated_once or shell_logic or rank_candidates"`, then `make validate && make test`.
Expected: PASS and green. `test_hf_scout_reads_gguf_files_in_sub_folders` still finds `"Q8_0:8500"` in `quants`, because the strings are unchanged. `rank-candidates.sh` reads only `fit`, `pick.bpw` and `pick.mib`.

### Task 3: `SKILL.md`: H as a sum, the `make benchmark` rule in NEVER, a precise description; the two-holder fixture

**Files:**
- Modify: `.claude/skills/onboard-local-model/SKILL.md:3` (description), `:75-76` (Freeing the GPU), `:82` (route gates), `:112-117` (NEVER, Gotchas)
- Modify: `.claude/skills/onboard-local-model/references/onboard-flow.md:172` (7a.5), `references/tuning.md:26` (T6)
- Modify: `.claude/skills/onboard-local-model/evals/files/preflight-served.json`; `evals/evals.json` (assertion text of `served-alias-argument`, `tune-capacity-card` and `enabled-alias-stopped`)
- Test: `tests/skills/onboard-local-model/test_onboard_local_model_scripts.py` (`_fake_preflight` :1157, `test_wait_gpu_release_times_out_with_the_gpu_holders` :1180, `test_skill_md_routes_near_misses_allows_inline_bench_fit_and_names_the_global_kv_key` :1002, `test_eval_enabled_alias_stopped_fixture_matches_its_assertions` :1995, `test_eval_alias_fixtures_match_their_assertions` :2008, `test_eval_tune_fixtures_match_their_assertions` :2210, `RETIRED_COPIES`; a new test after `test_skill_gates_announce_and_proceed_when_permissions_are_bypassed` :854)

**Interfaces:**
- Produces: `SKILL.md` at 10,980 bytes (measured on a scratch copy). Its route-gate sentence names G-R1, which Task 4 defines in served-alias R1.

- [ ] **Step 1: Write the failing tests**

```python
def test_freeing_the_gpu_sums_every_llama_server_holder() -> None:
    gates = _section(_md(SKILL_MD), "## Gates", "## NEVER")
    assert "H, the sum of `vram_mib` over every `.gpu.holders[]` entry whose `comm` is `llama-server`" in gates
    assert "G-T1 to G-T3, G-Q0 to G-Q3, G-R1 and G6, each asked in its route's file" in gates
    pre = json.loads((FIX / "preflight-served.json").read_text())
    held = [h["vram_mib"] for h in pre["gpu"]["holders"] if h["comm"] == "llama-server"]
    assert held == [12, 7450] and pre["gpu"]["used_mib"] - sum(held) + 512 == 6712  # router mode: the router is listed first
    assert all("router 12 + model 7450" in _asserts(i) for i in ("served-alias-argument", "tune-capacity-card"))
```

- In `_fake_preflight`, the holders become `[{"pid":6,"comm":"llama-server","vram_mib":12},{"pid":7,"comm":"llama-server","vram_mib":8000}]`. In `test_wait_gpu_release_times_out_with_the_gpu_holders`, the last assert becomes `assert out["released"] is False and [h["vram_mib"] for h in out["holders"] if h["comm"] == "llama-server"] == [12, 8000]`. This is coverage, not red: the script already reports every holder.
- In `test_eval_alias_fixtures_match_their_assertions` and `test_eval_tune_fixtures_match_their_assertions`, `held = next(h["vram_mib"] for ... )` becomes `held = sum(h["vram_mib"] for h in pre["gpu"]["holders"] if h["comm"] == "llama-server")`. In `test_eval_enabled_alias_stopped_fixture_matches_its_assertions`, `- 7450` becomes `- 7462`.
- In `test_skill_md_routes_near_misses_allows_inline_bench_fit_and_names_the_global_kv_key`, the gotchas assert becomes the first two lines below, and the last two follow the `DESCRIPTION_ROUTES` assert:

```python
    assert "`runtime.cache_type_k/v`" in gotchas and "make benchmark" not in gotchas and "--no-recreate" not in text
    assert "NEVER run `make benchmark`: a Vulkan failure rewrites `gpu.backend`" in _section(text, "## NEVER", "## Gotchas")
    assert all(w in meta["description"] for w in ("temperature/top_p", "LoRA fine-tuning", "converting safetensors to GGUF", "except my-coding-lite"))
    assert not [w for w in ("sampling", "quantizing", "other combos") if w in meta["description"]]
```

- `RETIRED_COPIES` gains `"`SKILL.md` Gotchas"` and `"the `vram_mib` of the `.gpu.holders[]` entry"`.

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/skills/onboard-local-model/test_onboard_local_model_scripts.py -q -k "sums_every or inline_bench_fit or fixtures_match or stated_once or wait_gpu"`
Expected: FAIL (old text, one-holder fixture). The `wait_gpu` tests pass.

- [ ] **Step 3: Edit `SKILL.md`**

- The whole description value (:3) becomes:

```text
Find, fit, benchmark and onboard a local agentic-coding LLM (GGUF) for this repo's llama.cpp router beside System One (Laya). Use when the user names a models.yml alias, a Hugging Face repo or URL, a .gguf file or a model name to download, add, try, swap or re-measure; says the local model got slower; asks which model fits the VRAM or beats the current one; compares quants (Q4_K_M/Q5_K_M/Q6_K); wants tok/s at depth, ctx_size, KV or n_cpu_moe tuned, the capacity card or my-coding-lite; scores it against Haiku, Luna or Composer; asks if the llama.cpp image or Laya is outdated; or wants one ladder challenge generated or verified, even as "a faster model for pi", "what LLM can I run locally" or "modelo agéntico que quepa en mi GPU". Not for OmniRoute combos except my-coding-lite, container memory limits, gpu-status, asking Laya for a judgment, cloud providers, temperature/top_p, LoRA fine-tuning or converting safetensors to GGUF.
```

- :75-76 become:

```text
**Freeing the GPU (G2, G-T2).** From one `preflight.sh` read `.gpu.used_mib` (U) and H, the sum of `vram_mib` over every
`.gpu.holders[]` entry whose `comm` is `llama-server`; run `make stop-llama` (llm-server only), then
```

- :82 becomes `Route gates follow the same rule: G-T1 to G-T3, G-Q0 to G-Q3, G-R1 and G6, each asked in its route's file.` Every reference is still named in the route table (`test_skill_md_is_a_router_that_loads_every_reference`).
- The Gotchas line `- Never run `make benchmark`: ...` is deleted. `- NEVER run `make benchmark`: a Vulkan failure rewrites `gpu.backend` to `cpu` in the live models.yml.` becomes the last NEVER item.
- onboard-flow 7a.5 and tuning T6: "(`SKILL.md` Gotchas)" becomes "(`SKILL.md` NEVER)".

- [ ] **Step 4: Fixture and eval text**

- `preflight-served.json`: `used_mib` 13662, `free_mib` 2643, and `{"pid": 4241, "comm": "llama-server", "vram_mib": 12}` becomes the first holder. The other holders and every other field stay.
- In the assertions of `served-alias-argument` and `tune-capacity-card`, "(13650 used - 7450 held by llama-server + 512)" becomes "(13662 used - 7462 held by the two llama-server processes, router 12 + model 7450, + 512)".
- In `enabled-alias-stopped`, "(U - H + 512 with the llama-server holder present)" becomes "(U - H + 512, H summed over the llama-server holders)".

- [ ] **Step 5: Measure, run and validate (no commit)**

Run: `wc -c .claude/skills/onboard-local-model/SKILL.md` (expected 10,980, at most 11,000), then `uv run pytest tests/skills/onboard-local-model/test_onboard_local_model_scripts.py -q -k "skill_md or sums_every or description or stated_once or fixtures_match or wait_gpu or black_box"`, then `make validate && make test`.
Expected: PASS and green. `test_skill_description_fits_the_spec_limit` passes at 939 characters. `test_eval_onboard_plan_fixtures_match_their_assertions` still finds `--below-mib 6712`.

### Task 4: Flow references: fit before copy, Gate G-R1, R4/R6 as found, lows

**Files:**
- Modify: `.claude/skills/onboard-local-model/references/local-gguf.md` (:3-5, :12-13, :25-26, :31)
- Modify: `.claude/skills/onboard-local-model/references/served-alias.md` (:3-5, :13-14, :16-22, :31-32, :38-41)
- Modify: `.claude/skills/onboard-local-model/references/onboard-flow.md` (:3-4, :24-25, :57, :75, :110-111, :173-174)
- Modify: the preamble clause in `anchors.md`, `challenges.md`, `runtimes.md`, `selection-criteria.md`, `system-one.md`, `tuning.md`; the headings anchors.md:11 and runtimes.md:6 with runtimes.md:13
- Test: `tests/skills/onboard-local-model/test_onboard_local_model_scripts.py` (`test_served_alias_reference_covers_a_stopped_server_and_a_declined_g3` :1084, `test_local_gguf_reference_owns_the_name_check_copy_and_live_entry` :1097, `test_onboard_flow_names_alias_items_a1_to_a3` :1109, `test_onboard_step_4_selects_the_candidate_before_validating_it` :1046, `test_references_keep_host_specific_evidence_and_give_every_flag_a_destination` :1016, `RETIRED_COPIES`)

**Interfaces:**
- Consumes: Task 3's route-gate sentence naming G-R1.
- Produces: served-alias R1 with "**Gate G-R1**", local-gguf L3 "Fit, before any copy", and onboard-flow 4.3 "4.4 ran first". Task 5's `quoted-tilde-gguf` and `named-gguf-argument` assertions quote this order.

- [ ] **Step 1: Write the failing tests**

- In `test_served_alias_reference_covers_a_stopped_server_and_a_declined_g3`, add:

```python
    assert all(s in r1 for s in ("says the stack is not running", "**Gate G-R1** before `make start`", "A no to G-R1", "capacity show"))
    assert "`make start-llama` only when llm-server served before R1" in _section(served, "R4. ", "R5. ")
    assert "was stopped before R1 and runs now" in served.split("R6. **Report**", 1)[1]
    assert "G3's facts also say that this step runs `make start`" in _section(_md(REFS / "onboard-flow.md"), "**7a. Apply and serve", "**7b.")
```

- In `test_local_gguf_reference_owns_the_name_check_copy_and_live_entry`, add:

```python
    l3 = _section(local, "L3. ", "L4. ")
    assert all(s in l3 for s in ("Fit, before any copy", "replaces Steps 4.4", "right after 4.2", "nothing is copied"))
    assert "asked only after L3 found the fit feasible" in _section(local, "L4. ", "L5. ")
    assert "1 to 4.2, L3 (for 4.4), L4 (for 4.3), then 5 to 9" in _section(local, "L5. ", "L6. ")
```

- In `test_onboard_flow_names_alias_items_a1_to_a3`, the last assert's sentence becomes `"A local GGUF also follows its own reference at 4.4 (run before G1), G1, 7a.2 and 9"`. In `test_onboard_step_4_selects_the_candidate_before_validating_it`, add `assert "A local GGUF: 4.4 ran first, on the file's own directory" in step4 and "section 7, which the Hugging Face row" in _section(flow, "H4. ", "H5. ")`.
- In `test_references_keep_host_specific_evidence_and_give_every_flag_a_destination`, add:

```python
    assert re.search(r"^## Published figures \(retrieved 2026-10-30 from secondary coverage; re-check before quoting\)$",
                     (REFS / "anchors.md").read_text(), re.MULTILINE)
    runtimes = (REFS / "runtimes.md").read_text()
    assert "## Last seen on this host (2026-10-30; re-check)" in runtimes and "These are last-seen values" not in runtimes
```

- `RETIRED_COPIES` gains `"`make start` when the stack is down"`, `"L3 at 4.4"`, `"(another quant of that model)"`, `"per the research brief loaded at this step"`, `"and paths are in `SKILL.md`"` and `"and so are `$SK` and paths"`.

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/skills/onboard-local-model/test_onboard_local_model_scripts.py -q -k "served_alias or local_gguf or a1_to_a3 or step_4 or host_specific or stated_once"`
Expected: FAIL.

- [ ] **Step 3: Edit `local-gguf.md`**

- :3-5 become "Loaded for a `.gguf` path (`resolve-target.sh`: `kind gguf_file`), before the onboard Steps (`SKILL.md` Route). This file prepares the candidate and says what changes in Steps at 4.4 (moved before G1), G1, 7a.2 and 9."
- The checklist (:12-13) becomes "Progress checklist (copy it into your task list): L1 name check; L2 scratch; Steps 1-4.2; L3 fit; L4 copy (G1); Steps 5-9 with L6 live entry (7a.2) and L7 report (9)."
- L3 (:25) becomes:

```text
L3. **Fit, before any copy.** It replaces Steps 4.4 and runs right after 4.2 wrote the settings into `$S`:
    `uv run llmenv.py --config $S models select candidate`, then `validate-gguf` and `budget` on `$S` with `--models-dir` set
    to the file's own directory. `feasible` false, or an error: report it as 4.4 says and stop; nothing is copied.
```

- L4's first line (:26) becomes "L4. **G1 is a copy, not a download**, asked only after L3 found the fit feasible, and skipped when L1 found the file in place." Its three bullets stay.
- L5 (:31) becomes "L5. **Steps 1-9** in this order: 1 to 4.2, L3 (for 4.4), L4 (for 4.3), then 5 to 9 with L6 at 7a.2 and L7 at 9."

- [ ] **Step 4: Edit `served-alias.md`**

- :3-5: delete "Gates, rules 1-3, freeing the GPU, `$SK`, `<run>` and paths are in `SKILL.md`." In the checklist, "R1 preflight;" becomes "R1 preflight (G-R1 if the stack is down);".
- R1 (:16-22) becomes:

```text
R1. **Preflight.** `bash $SK/scripts/preflight.sh`, then the rule 3 profile check.
    - The fit is preflight's `llmenv_budget`. A false `feasible` leads the report.
    - `services.llm_server_enabled` false (a gateway-only host): not a re-measure. The alias is a candidate with no baseline:
      `SKILL.md`'s other-alias row.
    - `services.llm_server_running` false: say so, run `make start-llama`, then R1 again. Remember that it was stopped, for R6.
    - `make start-llama` says the stack is not running: **Gate G-R1** before `make start`, then R1 again. Its facts:
      `make start` brings up every stack container that is down (OmniRoute, remote-setup, System One, Phoenix with System
      One, llm-server), System One loads its weights before it is healthy, and R6 hands back only llm-server, so the rest
      stays up. A no to G-R1: report the capacity card as it stands (`uv run llmenv.py capacity show --alias <alias>`), say
      that nothing was measured, and stop.
    - `make start-llama` or `make start` fails its budget check: its output is the result. Report it and stop; never loosen
      `vram_budget`.
```

- R4's reject bullet (:31-32) ends "...under the floor: report it; `make start-llama` only when llm-server served before R1; then R6."
- R6's bullets (:39-41) become:

```text
    - llm-server was stopped before R1 and runs now: `make stop-llama` hands it back stopped. Only llm-server is restored; the
      containers a yes to G-R1 brought up stay up.
    - Last: `podman ps -a` and a `ps` by PID for everything this run started: nothing may remain but the stack containers of
      a yes to G-R1.
```

- [ ] **Step 5: Edit `onboard-flow.md`**

- :3-4: delete "Gates, rules 1-3, freeing the GPU, `$SK`, `<run>` and paths are in `SKILL.md`."
- A2 (:24-25): "When a live entry's file gives the same name (another quant of that model), the alias is" becomes "When a live entry's file gives the same name, the alias is".
- H4 (:57): "per the research brief loaded at this step." becomes "per the research brief (section 7, which the Hugging Face row of `SKILL.md` Route loads at step 3)."
- :75 becomes "A local GGUF also follows its own reference at 4.4 (run before G1), G1, 7a.2 and 9."
- In 4.3 (Gate G1), the first bullet becomes "- A local GGUF: 4.4 ran first, on the file's own directory (the Local GGUF reference, L3); G1 copies only after a feasible result." The existing bullets follow it.
- 7a.6 (:173-174) becomes:

```text
     6. `make start-llama`, then `make status`, `make check-server` and `make check-with-agents`. When step 1 found the stack
        down (preflight `services` shows OmniRoute and System One stopped), G3's facts also say that this step runs
        `make start` instead, which brings up every stack container that is down.
```

- [ ] **Step 6: Preambles and dated headings**

- Delete the clause in each file: anchors.md:4 "Gates, rules 1-3, `$SK` and paths are in `SKILL.md`."; challenges.md:4 "`$SK` and paths are in `SKILL.md`."; runtimes.md:4 "Gates, `$SK` and paths are in `SKILL.md`."; selection-criteria.md:4 (the whole line); system-one.md:4 "Gates, `$SK` and paths are in `SKILL.md`.".
- tuning.md:3-4 become "Loaded for tuning, the capacity card, `my-coding-lite` and quant comparisons."
- scoring.md keeps its preamble: it names the rules it relies on.
- anchors.md: delete "Figures retrieved 2026-10-30 from secondary coverage; re-check before quoting." from :3-4. The heading :11 becomes `## Published figures (retrieved 2026-10-30 from secondary coverage; re-check before quoting)`.
- runtimes.md: the heading :6 becomes `## Last seen on this host (2026-10-30; re-check)`, and :13 becomes "A decision quotes only the `--version` output read in this run."

- [ ] **Step 7: Run and validate (no commit)**

Run: `uv run pytest tests/skills/onboard-local-model/test_onboard_local_model_scripts.py -q -k "served_alias or local_gguf or a1_to_a3 or step_4 or host_specific or stated_once or never_point or shell_logic or ask_first or route_table or runtime_fixture"`, then `make validate && make test`.
Expected: PASS and green.

### Task 5: Eval prompts without setup tails; three new evals; fixtures

**Files:**
- Modify: `.claude/skills/onboard-local-model/evals/evals.json` (all evals; `named-gguf-argument` assertion 5; the `anchor-comparison` setup; three new evals appended)
- Modify: `.claude/skills/onboard-local-model/evals/files/hf/model-yuxinlu1__gemma-4-12B-agentic-fable5-composer2.5-v2-3.5x-tau2-GGUF.json`; `evals/files/anchors/route-check.txt`
- Create: `evals/files/hf/model-yuxinlu1__gemma-4-12B-agentic-fable5-composer2.5-v2-3.5x-tau2-GGUF@main.json`, `evals/files/hf/tree-yuxinlu1__gemma-4-12B-agentic-fable5-composer2.5-v2-3.5x-tau2-GGUF@main.json`, `evals/files/home/Downloads/Bar-7B-Q4_K_M.gguf`
- Test: `tests/skills/onboard-local-model/test_onboard_local_model_scripts.py` (`EVAL_SETUP` and `test_eval_prompts_read_as_user_turns` :916-921, `test_eval_input_files_exist_and_the_gguf_fixture_resolves` :1871, `test_eval_anchor_fixture_matches_its_assertions` :1977; three new tests after `test_eval_hf_file_fixtures_match_their_assertions` :2050)

**Interfaces:**
- Consumes: Task 1 (tilde), Task 2 (`--single-file`, `pick.split`), Task 4 (L3 before G1).
- Produces: evals.json `{skill_name, harness, evals}`, where each eval holds `id, prompt[, setup], files, expected_output, assertions`.

- [ ] **Step 1: Write the failing tests**

```python
EVAL_SETUP = re.compile(r"Eval setup|^onboard-local-model|fixture|DRY RUN|LLM_ENV_|HF_FIXTURE_DIR|Premise")
EVAL_KEYS = {"id", "prompt", "setup", "files", "expected_output", "assertions"}


def test_eval_prompts_read_as_user_turns() -> None:
    """The prompt is the user's message alone; fixture paths, env vars and the read-only rule live in setup."""
    data = json.loads((SKILL / "evals" / "evals.json").read_text())
    assert "setup" in data["harness"]
    for e in data["evals"]:
        assert set(e) <= EVAL_KEYS and not EVAL_SETUP.search(e["prompt"]), e["id"]
        assert bool(e.get("setup")) == bool(e.get("files")), e["id"]
```

In `test_eval_input_files_exist_and_the_gguf_fixture_resolves`, the `prompt = ...` line becomes `prompt = next(e["prompt"] + " " + e.get("setup", "") for e in evals if e["id"] == eval_id)`.

In `test_eval_anchor_fixture_matches_its_assertions`, add:

```python
    head = (FIX / "anchors" / "route-check.txt").read_text().splitlines()[0]
    assert head.startswith("# ") and "2026-11-01" in head and "2026-10-30" in head
```

New tests:

```python
YUX = "yuxinlu1/gemma-4-12B-agentic-fable5-composer2.5-v2-3.5x-tau2-GGUF"
YUX_SHA = "190a31365a6b80a692349be34ccdac730cad4fe4"
YUX_Q5 = f"https://huggingface.co/{YUX}/resolve/main/Q5_K_M/gemma4-v2-Q5_K_M.gguf"


def _yux_scout(budget: str, ram: str, *extra: str) -> dict:
    (row,) = json.loads(_run("bash", SCRIPTS / "hf-scout.sh", "--repo", YUX, "--revision", "main", "--json", "--budget-mib", budget,
                             "--ram-mib", ram, *extra, env={"HF_FIXTURE_DIR": str(FIX / "hf"), "PATH": "/usr/bin:/bin"}).stdout)
    return row


def test_eval_hf_subfolder_file_fixtures_match_their_assertions() -> None:
    slug = YUX.replace("/", "__")
    model = json.loads((FIX / "hf" / f"model-{slug}.json").read_text())
    assert (model["id"], model["gguf"]["context_length"]) == (YUX, 262144)
    assert json.loads((FIX / "hf" / f"model-{slug}@main.json").read_text()) == model
    assert (FIX / "hf" / f"tree-{slug}@main.json").read_text() == (FIX / "hf" / f"tree-{slug}@{YUX_SHA}.json").read_text()
    env = {**os.environ, "LLM_ENV_CONFIG": str(FIX / "models-gateway.yml"), "LLM_ENV_MODELS_DIR": str(FIX / "models")}
    rt = json.loads(_run("bash", SCRIPTS / "resolve-target.sh", YUX_Q5, env=env).stdout)
    assert (rt["kind"], rt["revision"], rt["file"]) == ("hf_file", "main", "Q5_K_M/gemma4-v2-Q5_K_M.gguf")
    row = _yux_scout("10369", "14500", "--file", "Q5_K_M/gemma4-v2-Q5_K_M.gguf")
    assert (row["sha"], row["fit"], row["pick"]["bytes"], row["pick"]["sha256"]) == (YUX_SHA, "vram", 8547884576, "5" * 64)
    text = _asserts("hf-subfolder-file")
    assert all(s in text for s in (f"resolve/{YUX_SHA}/Q5_K_M/gemma4-v2-Q5_K_M.gguf", "file gemma4-v2-Q5_K_M.gguf", "8547884576",
                                   "gemma4-q5_k_m", "~/llm-workspace/models/gemma4-v2-Q5_K_M.gguf", "--budget-mib 10369 --ram-mib 14500"))


def test_eval_hf_subfolder_no_fit_fixtures_match_their_assertions() -> None:
    named = _yux_scout("8321", "12000", "--file", "Q5_K_M/gemma4-v2-Q5_K_M.gguf")
    single = _yux_scout("8321", "12000", "--single-file")
    plain = _yux_scout("8321", "12000")
    assert (named["fit"], named["pick"]["mib"], single["fit"]) == ("no", 8151, "no")
    assert (plain["pick"]["file"], plain["pick"]["mib"], plain["pick"]["split"]) == ("Q6_K/gemma4-v2-Q6_K-00001-of-00002.gguf", 9346, True)
    text = _asserts("hf-subfolder-no-fit")
    assert all(s in text for s in ("fit no", "--single-file", "offers none", "Q6_K/gemma4-v2-Q6_K-00001-of-00002.gguf", "9346", "before G1"))


def test_eval_quoted_tilde_fixture_matches_its_assertions() -> None:
    home = FIX / "home"
    assert (home / "Downloads" / "Bar-7B-Q4_K_M.gguf").read_bytes() == GGUF_BYTES
    result = _run("bash", SCRIPTS / "resolve-target.sh", "~/Downloads/Bar-7B-Q4_K_M.gguf", env={**os.environ, **FIX_ENV, "HOME": str(home)}, cwd=ROOT)
    assert result.returncode == 0, result.stderr
    rt = json.loads(result.stdout)
    assert (rt["kind"], rt["quant"], rt["in_models_dir"]) == ("gguf_file", "Q4_K_M", False)
    assert all(s in _asserts("quoted-tilde-gguf") for s in ("kind gguf_file", "in_models_dir false", "never offered", "own directory"))
    assert "only after a feasible fit" in _asserts("named-gguf-argument")
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/skills/onboard-local-model/test_onboard_local_model_scripts.py -q -k "user_turns or input_files or anchor_fixture or subfolder_file or subfolder_no_fit or quoted_tilde_fixture"`
Expected: FAIL (tails, no `harness`, missing fixtures and evals).

- [ ] **Step 3: Move the setup tails**

Run once from the repo root. `E` and `T` are shell variables for the two paths:

```bash
E=.claude/skills/onboard-local-model/evals/evals.json; T=tmp/evals-r11.json
jq --arg h "Each eval's setup is operator context for an offline run (fixture paths, environment variables, the read-only rule): a harness gives it to the run apart from the prompt, which is the user's message alone." '{skill_name, harness: $h, evals: (.evals | map(if (.prompt | test(" \\(Eval setup: ")) then (.prompt | capture("^(?<p>.*?) \\(Eval setup: (?<s>.*)\\)$")) as $m | {id, prompt: $m.p, setup: $m.s} + del(.id, .prompt) else . end))}' "$E" > "$T" && mv "$T" "$E"
```

This was checked on a scratch copy on 2026-11-01: 25 evals gained `setup`, and rejoining `prompt + " (Eval setup: " + setup + ")"` reproduces every old prompt. jq rewrites the few hand-wrapped `files` arrays one item per line (format only).

- [ ] **Step 4: Fixtures**

- In the base sub-folder model fixture, add `"id": "yuxinlu1/gemma-4-12B-agentic-fable5-composer2.5-v2-3.5x-tau2-GGUF"`, `gguf.context_length` 262144 and `gguf.architecture` `"gemma4"`. `sha` and `gguf.total` stay; `quant-headroom.sh` reads only those two. Write `model-...@main.json` as an exact copy of it, and `tree-...@main.json` as an exact copy of `tree-...@190a31365a6b80a692349be34ccdac730cad4fe4.json`. These are the names `hf-scout.sh --revision main` reads.
- `evals/files/home/Downloads/Bar-7B-Q4_K_M.gguf`: a copy of `evals/files/Foo-9B-Q4_K_M.gguf` (the 64-byte header fixture).
- `anchors/route-check.txt` gains this first line: `# Saved 2026-11-01 for the anchor-comparison eval (a fixture, not a host transcript). Route checks change: on the real host on 2026-10-30 opencode/claude-haiku-4-5 returned 401 during a lane (the anchors reference, Anchor lanes item 5).`
- In the `anchor-comparison` setup, "The route checks already ran;" becomes "The route checks ran on 2026-11-01;".

- [ ] **Step 5: Evals**

In `named-gguf-argument`, assertion 5 becomes "Copying the file into ~/llm-workspace/models would be a G1 gate (size, free disk, destination) asked only after a feasible fit on the file's own directory; with budget's error it is never offered; the original directory is never mounted and the original file is never moved or deleted".

Append these evals (`<repo>` stands for `yuxinlu1/gemma-4-12B-agentic-fable5-composer2.5-v2-3.5x-tau2-GGUF`; `<F>` for `.claude/skills/onboard-local-model/evals/files`; write both out in full):

```json
{
  "id": "hf-subfolder-file",
  "prompt": "can I run https://huggingface.co/<repo>/resolve/main/Q5_K_M/gemma4-v2-Q5_K_M.gguf on this box? show me the models.yml entry you'd add",
  "setup": "The fixture config is <F>/models-gateway.yml and its models directory is <F>/models/ (scripts read them through LLM_ENV_CONFIG and LLM_ENV_MODELS_DIR). hf-scout.sh reads its Hugging Face answers from HF_FIXTURE_DIR=<F>/hf/ instead of the network. preflight.sh already printed <F>/preflight-gateway.json; do not run it, use that JSON as its output. DRY RUN for evaluation: read-only commands only (resolve-target.sh, hf-scout.sh, cat, jq, yq); do not download weights, stop containers, edit models.yml or start benchmarks. Answer in English.",
  "files": ["evals/files/models-gateway.yml", "evals/files/preflight-gateway.json", "evals/files/hf/model-yuxinlu1__gemma-4-12B-agentic-fable5-composer2.5-v2-3.5x-tau2-GGUF@main.json", "evals/files/hf/tree-yuxinlu1__gemma-4-12B-agentic-fable5-composer2.5-v2-3.5x-tau2-GGUF@main.json"],
  "expected_output": "The sub-folder URL is an hf_file at main; hf-scout.sh reads that one commit and pins its sha, size and sha256; the entry keeps the sub-folder in its url and the base name in file, its alias is gemma4-q5_k_m, and G1 downloads to the base name; this gateway-only host has no baseline.",
  "assertions": [
    "resolve-target.sh gives kind hf_file, revision main, file Q5_K_M/gemma4-v2-Q5_K_M.gguf (the sub-folder kept) and quant Q5_K_M",
    "hf-scout.sh --repo <repo> --revision main --file Q5_K_M/gemma4-v2-Q5_K_M.gguf --json --budget-mib 10369 --ram-mib 14500 gives sha 190a31365a6b80a692349be34ccdac730cad4fe4, fit vram, pick.bytes 8547884576 and pick.sha256 5555555555555555555555555555555555555555555555555555555555555555; the budget is model_vram_budget_mib because llmenv_budget is null and the RAM is ram.available_mib because ram_offload_budget_mib is 0, both said",
    "The entry has file gemma4-v2-Q5_K_M.gguf (the base name), url https://huggingface.co/<repo>/resolve/190a31365a6b80a692349be34ccdac730cad4fe4/Q5_K_M/gemma4-v2-Q5_K_M.gguf (the commit, never main, and the sub-folder kept), size_bytes 8547884576, quantization Q5_K_M and enabled false",
    "The alias is gemma4-q5_k_m: the live gemma4 entry's file gemma4-v2-Q4_K_M.gguf gives the same name, gemma4-v2 (A2)",
    "G1 downloads to ~/llm-workspace/models/gemma4-v2-Q5_K_M.gguf (a base name, never Q5_K_M/...) and checks its size and sha256",
    "There is no baseline (gateway-only host): no baseline lane or bench, and step 8 says so",
    "Nothing was downloaded, stopped, started or edited"
  ]
}
```

```json
{
  "id": "hf-subfolder-no-fit",
  "prompt": "does https://huggingface.co/<repo>/resolve/main/Q5_K_M/gemma4-v2-Q5_K_M.gguf fit next to laya? if not, which file from that repo would?",
  "setup": "The fixture config is <F>/models.yml and its models directory is <F>/models/ (scripts read them through LLM_ENV_CONFIG and LLM_ENV_MODELS_DIR). hf-scout.sh reads its Hugging Face answers from HF_FIXTURE_DIR=<F>/hf/ instead of the network. preflight.sh already printed <F>/preflight-served.json; do not run it, use that JSON as its output. DRY RUN for evaluation: read-only commands only (resolve-target.sh, hf-scout.sh, cat, jq); do not download weights, stop containers, edit models.yml or start benchmarks. Answer in English.",
  "files": ["evals/files/models.yml", "evals/files/preflight-served.json", "evals/files/hf/model-yuxinlu1__gemma-4-12B-agentic-fable5-composer2.5-v2-3.5x-tau2-GGUF@main.json", "evals/files/hf/tree-yuxinlu1__gemma-4-12B-agentic-fable5-composer2.5-v2-3.5x-tau2-GGUF@main.json"],
  "expected_output": "The named Q5_K_M (8151 MiB) is fit no against the 8321 MiB budget, so the run stops before G1; the fallback with --single-file is fit no too, so no single-file quant of 3.5 bpw or more fits in VRAM and none is offered; the split Q6_K that the plain call picks is never offered.",
  "assertions": [
    "resolve-target.sh gives kind hf_file, revision main, file Q5_K_M/gemma4-v2-Q5_K_M.gguf",
    "hf-scout.sh --repo <repo> --revision main --file Q5_K_M/gemma4-v2-Q5_K_M.gguf --json --budget-mib 8321 --ram-mib 12000 gives fit no (8151 MiB plus about 1.5 GiB for KV and buffers exceeds 8321 MiB): the run stops before G1, under bypass too",
    "The fallback is the same call without --file and with --single-file (--revision main kept); its fit is no, so the answer says no single-file quant of 3.5 bpw or more fits in VRAM and offers none",
    "The split Q6_K file (Q6_K/gemma4-v2-Q6_K-00001-of-00002.gguf, 9346 MiB, no single sha256) that the call without --single-file picks is never offered",
    "Nothing was downloaded, stopped, started or edited"
  ]
}
```

```json
{
  "id": "quoted-tilde-gguf",
  "prompt": "put \"~/Downloads/Bar-7B-Q4_K_M.gguf\" on my local llama server if it fits next to laya. just the plan for now, change nothing",
  "setup": "Run resolve-target.sh with HOME=<F>/home, so the quoted path is the file <F>/home/Downloads/Bar-7B-Q4_K_M.gguf. The config is <F>/models.yml and its models directory <F>/models/ (pass them as LLM_ENV_CONFIG and LLM_ENV_MODELS_DIR). preflight.sh already printed <F>/preflight-served.json; use that JSON as its output. DRY RUN for evaluation: read-only commands only (resolve-target.sh, validate-gguf and budget on a scratch config, cat, jq); do not copy, move or delete the file, download, edit models.yml, or start or stop a container or benchmark. Answer in English.",
  "files": ["evals/files/models.yml", "evals/files/preflight-served.json", "evals/files/home/Downloads/Bar-7B-Q4_K_M.gguf"],
  "expected_output": "The quoted ~ path resolves to the local GGUF; the Local GGUF route runs its fit on the file's own directory right after the step 4.2 settings, budget's error for the header-only file is the result, and no copy is offered.",
  "assertions": [
    "resolve-target.sh with the quoted path gives kind gguf_file, quant Q4_K_M, in_models_dir false; it is not an exit 2 \"not found\"",
    "The Local GGUF reference runs: no live entry or models-dir file has the name Bar-7B-Q4_K_M.gguf, and the scratch config comes from resolve-target.sh --scratch-config with candidate as its only enabled entry",
    "After the step 4.2 settings, models select candidate, validate-gguf and budget run on the scratch config with --models-dir set to the file's own directory, before any copy",
    "budget's error for the header-only file is the result: the flow stops there and the G1 copy is never offered",
    "The original file is never copied, moved or deleted; nothing was edited, started or stopped"
  ]
}
```

- [ ] **Step 6: Run and validate (no commit)**

Run: `uv run pytest tests/skills/onboard-local-model/test_onboard_local_model_scripts.py -q -k "eval_ or black_box or user_turns or name_only_existing"`, then `make validate && make test`.
Expected: PASS and green. `test_eval_input_files_exist_and_the_gguf_fixture_resolves` finds every new file or its directory in the setup text.

### Task 6: Verification and the plans index row

**Files:**
- Modify: `docs/superpowers/plans/README.md` (this plan's row)

- [ ] **Step 1: Guards and suites, one file at a time**

Run, one after the other:
- `uv run pytest tests/skills/onboard-local-model/test_onboard_local_model_scripts.py -q`
- `uv run pytest tests/skills/onboard-local-model/test_challenges.py -q`
- `uv run pytest tests/skills/onboard-local-model/test_latency_profile.py -q`
- `make validate && make test`

Expected: all green. The guard set in the first file is `-k "black_box or stated_once or never_point or route_table or router or shell_logic or named_once or user_turns or ask_first or a1_to_a3 or sums_every"`.

- [ ] **Step 2: Byte cap and description**

Run: `wc -c .claude/skills/onboard-local-model/SKILL.md`. Expected: at most 11,000 (about 10,980). `test_skill_description_fits_the_spec_limit` passes (939 characters).

- [ ] **Step 3: Read-only route walk with the fixtures**

From the repo root: `SK=.claude/skills/onboard-local-model F=$SK/evals/files`, with `LLM_ENV_CONFIG=$F/models.yml` and `LLM_ENV_MODELS_DIR=$F/models` unless a line says otherwise. `HF_FIXTURE_DIR=$F/hf` is set only for `hf-scout.sh`. Scratch files go to `tmp/plan-m-walk/`, removed at the end. For each route, run the commands and read the route's file as a new agent would.

1. Served alias, running: `resolve-target.sh gemma4` gives `alias`, `enabled true`, `present true`. With `preflight-served.json`, R3's target is `--below-mib 6712` = 13662 - (12 + 7450) + 512. A first-entry reading would give 14162.
2. Served alias, stopped: with `preflight-stopped.json`, R1 runs `make start-llama`, then R1 again. An R4 reject runs no `make start-llama`, and R6 ends with `make stop-llama`. If `make start-llama` says the stack is not running, G-R1 states its facts, and a no reports `capacity show` and stops.
3. Quoted `~` path: `HOME=$F/home resolve-target.sh "~/Downloads/Bar-7B-Q4_K_M.gguf"` gives `gguf_file` with `in_models_dir false`. local-gguf runs L1, L2, Steps 1-4.2, then L3. The header-only file's `budget` error stops the flow before L4 (no copy).
4. Sub-folder URL on a gateway host (`LLM_ENV_CONFIG=$F/models-gateway.yml`): `resolve-target.sh https://huggingface.co/yuxinlu1/gemma-4-12B-agentic-fable5-composer2.5-v2-3.5x-tau2-GGUF/resolve/main/Q5_K_M/gemma4-v2-Q5_K_M.gguf` gives `hf_file` at `main` with `file Q5_K_M/gemma4-v2-Q5_K_M.gguf`. `hf-scout.sh --repo <repo> --revision main --file Q5_K_M/gemma4-v2-Q5_K_M.gguf --json --budget-mib 10369 --ram-mib 14500` gives `vram`, sha `190a31...`, 8547884576 bytes and sha256 `5555...`. A2 names it `gemma4-q5_k_m`. The entry url keeps `Q5_K_M/` after `resolve/<sha>/`, and G1 writes the base name.
5. The same URL beside System One (`preflight-served.json`, `--budget-mib 8321 --ram-mib 12000`): `fit no`, so H3 stops. The fallback with `--single-file` is `fit no`, so H2 says none fits and offers nothing. The plain call's split Q6_K pick is not offered.
6. `refs/pr`: `resolve-target.sh https://huggingface.co/lab/Foo-9B-GGUF/resolve/refs/pr/1/Foo-9B-Q4_K_M.gguf` gives revision `refs%2Fpr%2F1` and file `Foo-9B-Q4_K_M.gguf`. `hf-scout.sh --revision refs%2Fpr%2F1` passes its revision check; with no fixture for it, it exits 2 "cannot read lab/Foo-9B-GGUF at revision refs%2Fpr%2F1".
7. Regressions: `Gemma4` and `orinth-35b` exit 2; `Qwen3.8-27B` exits 3; `$F/Foo-9B-Q4_K_M.gguf` gives `gguf_file`; `$F/Ornith-Q5_K_M.gguf` takes `ornith-2`; `rank-candidates.sh` on the scout fixtures ranks lab-a 23 and lab-d 20; `models-local-url.yml` sends the quant route's P0 to the repo question.
8. Other routes, unchanged by this plan but walked once:
   - Tune: T3's target is the same 6712.
   - Anchors: `anchors/route-check.txt` read with anchors.md leaves one runnable lane (haiku), and its dated header explains the 2026-10-30 evidence.
   - Runtime: `runtime/images.txt` read with runtimes.md quotes 10871 and 10143, not the last-seen 10454.
   - System One: `system-one-check.sh` on its fixture gives 0.4.2 against 0.5.0.
   - Challenges: `gen-challenge.sh --help` exits 0.
9. Evals: `jq -r '.evals[].prompt' $SK/evals/evals.json` shows no `(Eval setup:`, and each eval with `files` has a `setup`.

Then `podman ps -a` lists the same containers as before the walk, and `tmp/plan-m-walk/` and `tmp/evals-r11.json` are gone.

- [ ] **Step 4: Plans index row**

The row below was added with this plan, after the 2026-10-31-judge-round-10-fixes row; the rows of plans G-L are unchanged. On completion, change `PENDING` to `**DONE** (executed <date>; skill edits uncommitted with the untracked skill tree; judges re-run by the orchestrator)`.

```text
| 2026-10-31-judge-round-11-fixes | PENDING | fixes from skill-judge round 11 of onboard-local-model (`tmp/judges/r11-native-summary.txt` 88.8 % B, 5 medium, 6 low; `tmp/judges/r11-referee-summary.txt` 87.2 % B, its two highs refuted by the lead, one medium confirmed in part, 3 lows; bar: >= 90 % from both lanes, no critical or high) plus plan L's executor residuals; findings verified against the files first (refuted and dropped ones in its ledger): `resolve-target.sh` expands a quoted leading `~` and maps a `refs/pr/N` revision to `refs%2Fpr%2FN` (red-first); `hf-scout.sh` marks `split` quants and gains `--single-file`, so H2/H3 offer the largest fitting single-file quant only when its `fit` is `vram` (red-first); a local GGUF's fit runs before its G1 copy; `make start` on a down stack is its own gate G-R1 in the served-alias re-measure, and an R4 reject no longer starts and stops a server found stopped; `SKILL.md` sums `vram_mib` over every `llama-server` holder (router mode lists two), moves the `make benchmark` rule into NEVER and takes a description with precise boundaries (stays under 11,000 bytes, measured draft 10,980); eval prompts become the user's message alone, with fixture context in a `setup` field; three fixture-backed evals (a sub-folder HF file on a gateway host, the same file not fitting beside System One, a quoted `~` path) and the sub-folder model fixture completed; no tracked code changes; skill edits stay uncommitted with the untracked skill tree; judges re-run by the orchestrator |
```

## Round-1 review outcome (approved; 1 confirmed high, 2 medium, lows)

Review round 1 (native lane on a scratch copy + referee `router-env/my-referee-two`, lead-verified; tmp/plan-m-review/review-round-1.md)
left one CONFIRMED high; the referee's critical and highs were refuted. Plan status: approved for execution. The implementer must apply:

- H1 (Task 2): the new `split` key breaks `test_hf_scout_reads_a_named_file_at_a_pinned_revision`, whose exact `row["pick"] == {...}`
  comparison is the only one in the file. In Task 2 Step 1, add `"split": False` to that expected dict; the test joins Task 2's Files.
- M1 (Task 5): `setup` and `harness` are outside the agentskills eval schema (`prompt`, `expected_output`, `files`, `assertions`). A
  spec runner that passes only `prompt` and `files` would act on the live host. The `harness` string (Task 5 Step 3's `--arg h`) also
  says: "A run without setup is unsafe and not a valid run: it would act on the live host. Use the fixtures and the DRY RUN rule only."
  Every in-repo reader of evals.json (the tests) keeps handling both fields.
- M2 (Task 4): G-R1's facts also say that `make start` renders the units, migrates the config and (re)installs the OmniRoute healer and
  z.ai weekly-reset timers, besides bringing up every stack container that is down.
- L1 (Task 5): `named-gguf-argument`'s `expected_output` no longer promises a G1 copy: "copying into ~/llm-workspace/models is a G1 gate
  before any benchmark" becomes "with budget's error no copy is offered (G1 is asked only after a feasible fit)".
- L2 (Task 3): Step 2's and Step 5's `-k` also select `alias_stopped`
  (`test_eval_enabled_alias_stopped_fixture_matches_its_assertions`).
- L6 (Task 6): walk item 6 runs `hf-scout.sh --repo lab/Foo-9B-GGUF --revision 'refs%2Fpr%2F1' --json --budget-mib 1 --ram-mib 1`
  (with `HF_FIXTURE_DIR=$F/hf`), so it never calls the live `preflight.sh`.
- L3-L5 are accepted as they stand (harmless or cosmetic).
