# Judge Round 6 Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the confirmed defects behind round 6 of the onboard-local-model skill judges (native 88.0 %, referee 88.8 %, no critical or high), so the next round gives >= 90 % from both lanes with no critical or high finding.

**Architecture:** One real script defect is fixed in code: an `hf_file` URL is today sized, pinned and checked against `main`. `hf-scout.sh` gains `--revision` and `--file`, reads the model and its tree at one commit, and emits `sha`, `pick.bytes` and `pick.sha256`. The entry template then takes all three from that one row. The other findings are dead ends and contradictions in the flow references. They are fixed in prose, and each fix is pinned by an eval that uses a fixture (tests check that the eval assertions match the script output). `SKILL.md` stays a router under its 11,000-byte cap: restated text goes and the route rows name `scoring.md`.

**Tech Stack:** bash, jq, yq; pytest under `tests/skills/onboard-local-model/`; skill-judge rubric v4.

**Spec:** `tmp/judges/r6-native-summary.txt` and `tmp/judges/r6-referee-summary.txt` (round 6 verdicts; the referee file holds the lead's verification of its two highs).

## Global Constraints

- English only. Edit only `.claude/skills/onboard-local-model/**` and `tests/skills/onboard-local-model/**`. Both trees are untracked and stay uncommitted. No tracked repo code changes in this plan.
- `SKILL.md` <= 11,000 bytes (`SKILL_MD_MAX_BYTES`, tested). Never raise the cap; move content out instead. Measured draft of Task 6: 10,811 bytes.
- The description stays <= 1024 characters (tested). Draft: 860.
- A reference never names another reference file (`test_references_never_point_at_each_other`). Inside a reference, write "the effort-ladder reference", never `scoring.md`.
- `jev-fast-decision` is a black box, and no skill file names ai-kit or any word in `JEV_FORBIDDEN` (guard `test_skill_and_touched_docs_treat_jev_as_a_black_box`, plan 2026-10-31-skill-navigation-restructure Task 0).
- Prose holds no shell logic. A backtick span may contain at most two of `;`, `&&` and `||` (`test_skill_prose_has_no_long_shell_logic`).
- Every script under `scripts/` keeps `--help` (exit 0) and its tests. A change in script behavior is red-first: write the test, see it fail, then implement.
- Run `make validate` after any `.sh` edit, and `make validate && make test` after any `.py` edit (the test files are `.py`). The skill suite runs as `uv run pytest tests/skills/onboard-local-model -q`.
- Scratch goes under `./tmp/` and is removed by the same run. No containers, no GPU, no downloads, no live `models.yml` edits.

## Findings ledger (verified 2026-10-31 against the files)

| Finding | Verdict | Task |
|---|---|---|
| referee D8-1 (verified medium): `hf_file` revision ignored; hf-scout reads `tree/main` (hf-scout.sh:69), and the template pins the sha of `main` (onboard-flow.md:134,145-147) | confirmed. The HF API was checked live on 2026-10-31: `api/models/<repo>/revision/<rev>?expand[]=sha` resolves a branch, a full sha or a short sha to the full sha, and a missing revision gives 404. `tree/<sha>?recursive=true` returns `size` and `lfs.oid` (sha256). Search accepts `expand[]=sha` | 1, 2 |
| native D8-2: `<baseline alias>` is undefined on a gateway-only host (onboard-flow.md:27) | confirmed | 2 |
| native D6-1: under bypass a failed `fit` gate still downloads (onboard-flow.md:26) | confirmed | 2 |
| native D8-5: `make setup` is run without `LLM_ENV_ASSUME_YES` (onboard-flow.md:96) | confirmed. setup.sh:23 runs `read -rp`. ASSUME_YES takes the same defaults (setup.sh:44-67,180, which follow the persisted state), so the change only removes the hang | 3 |
| native D8-6: the floating tag pulled in 7a sits between the two ladder lanes | partly refuted. `make start-llama` runs `up -d --no-recreate --pull=never` (start.sh:143), so both ladder lanes run on the same container image. What remains: rule (c) may compare a card number from an unknown build (the card and the bench JSON record no image), and the container is recreated when the stack is down | 3 |
| native D8-3 / referee: scout calls pass no budget flags (selection-criteria.md:8,18), and the hf-scout default takes `llmenv_budget` first, not the smaller value (hf-scout.sh:36) | confirmed | 1, 4 |
| native D8-4: the MoE admission rule needs expert bytes that no scout row has (selection-criteria.md:21) | confirmed. The fix is an estimate built from existing fields, with the same arithmetic as hf-scout's `offload` class (hf-scout.sh:89) | 4 |
| native D8-9: "at most 3 go on" does not say how the seed set is shared (selection-criteria.md:27) | confirmed | 4 |
| native D8-1: G-Q3 has no yes branch and no decision rule (tuning.md:144) | confirmed | 5 |
| referee D2-1 (low): the quant phase has no order line | confirmed, cheap | 5 |
| native D5-1, D5-2 / referee D8-3: "the effort-ladder reference" is never mapped to `scoring.md`, and the route rows omit it | confirmed (the referee called it weak; both lanes lose points on it) | 6 |
| native D2-1, D3-1, D1-1 (make benchmark gotcha); referee D1-1 (goal and tool list), D4-1 (no trigger for challenge generation) | confirmed, cheap; they also free the bytes Task 6 needs | 6 |
| native D8-8: effort-run.sh:119 defaults to the legacy `~/.config/llm-env/pi` | confirmed | 7 |
| native D7-1: evals lack a G-Q3 yes case, a gateway-only HF case and a bare name with no repo | confirmed. Covered by four fixture-pinned evals (Tasks 2, 4, 5) | 2, 4, 5 |
| referee D7-1 (medium per referee): two easy negatives in trigger-queries | negatives confirmed, cheap. The train/validation split is dropped: rubric v4 D7 does not score it (`rubric.md` D7), and no description-optimizer loop runs that could overfit | 7 |
| referee D8-2 (high): a failed screen of the served alias leaves llm-server stopped | refuted by the lead. Step 9 runs `make start-llama` whenever llm-server served before G2 (onboard-flow.md:117-119). Step 6 gets one clause so the misreading cannot recur | 3 |
| referee D5-1: section 7 (research brief) is not loaded where it is used | refuted. SKILL.md:21 loads sections 1-5 and 7 on the Hugging Face row, the only row that dispatches the research subagent; the alias and GGUF rows never reach it. Dropped | - |
| referee D6-1 (low): screen and head-to-head `--per-layer` defaults conflict (scoring.md:25) | refuted. These are two modes (screen: the default of 1; head-to-head: 3), and every comparing lane uses 3. A clarifier of a few words is added | 7 |
| native D8-7 (low): challenges.md:51 says `data/run.log`, but the project ships `data/app.log` | refuted. The generator writes `data/run.log` (log-parser/fixtures.mjs:23); only the shipped reference test reads `app.log`. A clarifier is added | 7 |
| native D1-1, second half: the bypass/retry text restates AGENTS.md | dropped. It holds the skill's own gate semantics, and `test_skill_gates_announce_and_proceed_when_permissions_are_bypassed` pins it | - |
| (user list) resolve-target revision/sha handling | no change. resolve-target stays offline and already emits `revision` (test_resolve_target_parses_hugging_face_urls). Resolving the sha belongs in hf-scout, which already talks to the API | - |

## Owner decisions (defaults chosen)

1. **G-Q3 rule.** A quant switch uses the one replace rule (scoring.md item 6) plus Q7 passed plus pp/tg within ~3 %. The skill states only one replace rule, and run noise is about 10 points.
2. **`fit: "no"` stops before G1, under bypass too.** Step 4's budget would reject the download anyway. Only an explicit owner request, made after the fit report, downloads it.
3. **Rule (c) always uses step 6 numbers for both models.** step 6 benches the baseline beside the candidate. Neither the card nor the bench JSON records the llama.cpp build, so a card number cannot be shown to be like for like. The cost is one bench run, measured in minutes.
4. **G-Q3 is asked once, after the loop. Q11 still restores `<a>` after every candidate.** The yes branch is its own swap. This keeps the existing eval `quant-head-to-head` true, so the judge's "skip Q11" is not needed.
5. **No train/validation split** of the trigger queries (see the ledger).

## Review Focus

1. A revision that is a branch name or a URL-encoded ref (`refs%2Fpr%2F3`): it goes into the API path unchanged, and the entry pins the resolved `sha`, never the branch name. Tested in Task 1 (`test_hf_scout_requests_the_revision_and_the_tree_at_its_commit`).
2. A named file that is one shard of a split GGUF: `pick.bytes` is the sum of the shards and `pick.sha256` is null, and the flow stops before G1. Tested in Task 1 (`test_hf_scout_named_split_file_sums_bytes_and_has_no_single_checksum`).
3. An API answer without `sha` (older fixtures, odd mirrors): the row's `sha` is null, never "main", and the template text stops before G1. Tested in Task 1 (same test).
4. `LLM_ENV_ASSUME_YES=1 make setup` on a host where two Vulkan devices match: setup dies at device selection (setup.sh:227). 7a tells the agent to hand `make setup` to the owner. Covered by text in Task 3, untested (setup.sh is not changed).
5. A gateway-only host with no llm-server container: `make start-llama` creates the container from the pulled tag, and there is no step-5 Id to compare. 7c only records the Id. Covered by text in Task 3 and by the gateway eval in Task 2.

---

### Task 1: hf-scout reads one commit (`--revision`, `--file`, `sha`, `bytes`, `sha256`, rule-1 default)

**Files:**
- Modify: `.claude/skills/onboard-local-model/scripts/hf-scout.sh` (header 2-10, flags 13-28, checks 29-32, defaults 33-38, fetch 48-55, trees 64-69, rows 71-99)
- Test: `tests/skills/onboard-local-model/test_onboard_local_model_scripts.py` (new tests after `test_hf_scout_repo_screens_one_named_repo`, line 1621; extract `_preflight_env` from `test_preflight_takes_the_ram_budget_from_llmenv`, lines 1742-1786)

**Interfaces:**
- Produces: `hf-scout.sh --repo ORG/NAME [--revision REV] [--file PATH]`. `--revision` and `--file` without `--repo` exit 2. Every row gains `sha` (string or null). Every quant object, and so `pick`, gains `bytes` (exact sum of sizes) and `sha256` (`lfs.oid` when the quant is one file, else null). With `--file`, `pick` is that file whatever the fit, and a file absent from a tree that was read exits 2, with the file and the revision named on stderr. An unreadable repo or revision exits 2, with the revision named. In fixture mode with `--revision REV`, the script reads `model-<o>__<n>@REV.json` and `tree-<o>__<n>@REV.json` (the names without a suffix stay for no `--revision`). The default budget is the smaller non-null value of `llmenv_budget.vram_available_mib` and `model_vram_budget_mib`.

- [ ] **Step 1: Write the failing tests**

```python
REV_SHA = "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"


def _lfs_tree(*files: tuple[str, int, str | None]) -> list[dict[str, object]]:
    return [{"path": n, "size": mib * 1048576, **({"lfs": {"oid": oid, "size": mib * 1048576}} if oid else {})} for n, mib, oid in files]


def test_hf_scout_reads_a_named_file_at_a_pinned_revision(tmp_path: Path) -> None:
    (tmp_path / "model-lab__x@v2.json").write_text(json.dumps(_repo("lab/x", 8, 262144, "qwen35") | {"sha": REV_SHA}))
    (tmp_path / "tree-lab__x@v2.json").write_text(json.dumps(_lfs_tree(("m-Q4_K_M.gguf", 5000, "4" * 64), ("m-Q8_0.gguf", 8500, "8" * 64))))
    (tmp_path / "model-lab__x.json").write_text(json.dumps(_repo("lab/x", 8, 262144, "qwen35") | {"sha": "f" * 40}))
    (tmp_path / "tree-lab__x.json").write_text(json.dumps(_lfs_tree(("m-Q4_K_M.gguf", 6000, "0" * 64))))
    env = {"HF_FIXTURE_DIR": str(tmp_path), "PATH": "/usr/bin:/bin"}
    base = ("bash", SCRIPTS / "hf-scout.sh", "--json", "--budget-mib", "10369", "--ram-mib", "14000", "--repo", "lab/x")
    result = _run(*base, "--revision", "v2", "--file", "m-Q4_K_M.gguf", env=env)
    assert result.returncode == 0, result.stderr
    (row,) = json.loads(result.stdout)
    assert row["sha"] == REV_SHA, "the commit the revision resolved to, never the branch name"
    assert row["fit"] == "vram"
    assert row["pick"] == {"file": "m-Q4_K_M.gguf", "quant": "Q4_K_M", "mib": 5000, "bpw": 5.2, "bytes": 5000 * 1048576, "sha256": "4" * 64}, \
        "the named file wins although Q8_0 (8500 + 1536 <= 10369) is the scout's choice"
    (main,) = json.loads(_run(*base, env=env).stdout)
    assert (main["sha"], main["pick"]["mib"]) == ("f" * 40, 6000), "no --revision reads the main fixtures"
    absent = _run(*base, "--revision", "v2", "--file", "m-Q5_K_M.gguf", env=env)
    assert absent.returncode == 2 and "m-Q5_K_M.gguf" in absent.stderr and "v2" in absent.stderr
    missing = _run(*base, "--revision", "nope", env=env)
    assert missing.returncode == 2 and "nope" in missing.stderr
    for flag in (("--revision", "v2"), ("--file", "m-Q4_K_M.gguf")):
        assert _run("bash", SCRIPTS / "hf-scout.sh", "--json", "--budget-mib", "1", "--ram-mib", "1", *flag, env=env).returncode == 2, flag


def test_hf_scout_named_split_file_sums_bytes_and_has_no_single_checksum(tmp_path: Path) -> None:
    (tmp_path / "model-lab__x.json").write_text(json.dumps(_repo("lab/x", 8, 262144, "qwen35")))  # no "sha" in the answer
    shards = (("Q8_0/m-Q8_0-00001-of-00002.gguf", 4250, "1" * 64), ("Q8_0/m-Q8_0-00002-of-00002.gguf", 4250, "2" * 64))
    (tmp_path / "tree-lab__x.json").write_text(json.dumps(_lfs_tree(*shards)))
    result = _run("bash", SCRIPTS / "hf-scout.sh", "--json", "--budget-mib", "10369", "--ram-mib", "14000", "--repo", "lab/x",
                  "--file", "Q8_0/m-Q8_0-00001-of-00002.gguf", env={"HF_FIXTURE_DIR": str(tmp_path), "PATH": "/usr/bin:/bin"})
    assert result.returncode == 0, result.stderr
    (row,) = json.loads(result.stdout)
    assert row["sha"] is None, "an answer without sha is null, never main"
    assert (row["pick"]["bytes"], row["pick"]["sha256"]) == (8500 * 1048576, None)


def test_hf_scout_requests_the_revision_and_the_tree_at_its_commit(tmp_path: Path) -> None:
    calls = tmp_path / "calls"
    (tmp_path / "model.json").write_text(json.dumps(_repo("lab/x", 8, 262144, "qwen35") | {"sha": REV_SHA}))
    (tmp_path / "tree.json").write_text(json.dumps(_lfs_tree(("m-Q4_K_M.gguf", 5000, "4" * 64))))
    stub = tmp_path / "bin"
    stub.mkdir()
    (stub / "curl").write_text(f"""#!/usr/bin/env bash
echo "$*" >> {calls}
url=""; out=""
while [ $# -gt 0 ]; do case "$1" in -o) out="$2"; shift ;; https://*) url="$1" ;; esac; shift; done
case "$url" in
    *"/tree/{REV_SHA}?recursive=true") cat {tmp_path}/tree.json > "$out" ;;
    *"/revision/"*) cat {tmp_path}/model.json ;;
    *) exit 22 ;;
esac
""")
    (stub / "curl").chmod(0o755)
    result = _run("bash", SCRIPTS / "hf-scout.sh", "--json", "--budget-mib", "10369", "--ram-mib", "14000", "--repo", "lab/x",
                  "--revision", "refs%2Fpr%2F3", env={"PATH": f"{stub}:/usr/bin:/bin", "HOME": str(tmp_path)})
    assert result.returncode == 0, result.stderr
    text = calls.read_text()
    assert "/api/models/lab/x/revision/refs%2Fpr%2F3" in text and "expand[]=sha" in text
    assert f"/api/models/lab/x/tree/{REV_SHA}?recursive=true" in text, "the tree is read at the resolved commit"
    assert json.loads(result.stdout)[0]["pick"]["sha256"] == "4" * 64


@pytest.mark.parametrize(
    ("budget", "extra"),
    [('{"feasible": true, "available_mib": 9500, "required_mib": 1, "ram_available_mib": 1024, "ram_feasible": true}', ()),
     (None, ("--ram-mib", "0"))],
    ids=["llmenv-larger", "llmenv-null"],
)
def test_hf_scout_defaults_to_the_smaller_preflight_budget(tmp_path: Path, budget: str | None, extra: tuple[str, ...]) -> None:
    fixtures = tmp_path / "hf"
    fixtures.mkdir()
    (fixtures / "models.json").write_text("[]")
    env = _preflight_env(tmp_path, budget, gpu={"vram_budget_ceiling_mib": 9000}) | {"HF_FIXTURE_DIR": str(fixtures)}
    result = _run("bash", SCRIPTS / "hf-scout.sh", *extra, env=env)
    assert result.returncode == 0, result.stderr
    assert "VRAM 9000 MiB" in result.stdout.splitlines()[0], "rule 1: the smaller of the repo verdict and ceiling minus reserve"
```

Extract `_preflight_env(tmp_path: Path, budget: str | None, resources: str | None = None, gpu: dict[str, object] | None = None) -> dict[str, str]` from `test_preflight_takes_the_ram_budget_from_llmenv` (cfg with `gpu | (gpu or {})` merged and `system_one.enabled: False`, the stubs, PATH, `BUDGET_JSON`/`RESOURCES_JSON`), and call it from that test as well.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/skills/onboard-local-model -q -k "pinned_revision or split_file or tree_at_its_commit or smaller_preflight_budget"`
Expected: FAIL. `unknown flag: --revision` and `--file` exit 2, and `llmenv-larger` prints `VRAM 9500 MiB`. `llmenv-null` already passes; it guards the fallback.

- [ ] **Step 3: Implement in `hf-scout.sh`**

- Flags `--revision REV` and `--file PATH` (store `revision`, `file`). Next to the `--repo` checks, `[ -n "$repo" ] || { [ -z "$revision$file" ] || { echo "--revision and --file need --repo" >&2; exit 2; }; }`.
- Default budget (line 36): `jq '[.budget.llmenv_budget.vram_available_mib, .budget.model_vram_budget_mib] | map(select(. != null)) | min'`. Update the comment to "the smaller of the repo verdict and ceiling minus reserve (SKILL.md rule 1)".
- Add `sha` to the `expand[]` list (line 49). With `--repo`: `sfx=""; [ -z "$revision" ] || sfx="@$revision"`. The model comes from `hf "model-${repo//\//__}$sfx.json" "$api/models/$repo/revision/${revision:-main}" -G "${x[@]}"`. Assign it on its own line with `|| { echo "cannot read $repo at revision ${revision:-main} from the Hugging Face API (no such repo or revision?)" >&2; exit 2; }`.
- Trees: network mode fetches `tree/<row sha, else main>?recursive=true` (feed `jq -r '.[] | "\(.id) \(.sha // "main")"'` to the xargs `bash -c`) and saves it as `$tmp/tree-<id>$sfx.json` (pass `$sfx` as an argument). Both modes then read `$trees/tree-<id>$sfx.json`, so a single file name serves fixture and network mode.
- In the per-quant `map({file, quant, mib})`, add `bytes: (map(.size) | add)` and `sha256: (if length == 1 then (.[0].lfs.oid // null) else null end)`. Pass `--arg f "$file"`. Keep a named file through the 1.5-bpw filter (`select($f != "" or $p == 0 or .bpw >= 1.5)`), then `| if $f == "" then . else map(select(.file == $f)) end`. Set `pick: (if $f != "" then ($q[0] // null) else <current expression> end)`. Add `sha: (.sha // null)` to both row shapes (the tree-less one at line 75 too).
- After `out` is built: with `--file`, a row whose `fit` is not `unknown` and whose `pick` is null exits 2 with `<file> is not a GGUF in <repo> at revision <rev>`.
- Header: document `--revision`, `--file`, the fixture suffix, `sha`, `bytes`, `sha256` and the new default.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/skills/onboard-local-model -q -k "hf_scout or preflight or eval_scout"`
Expected: PASS, including the existing `test_hf_scout_*` tests (whose rows now carry extra keys) and `test_eval_scout_fixtures_match_their_assertions`.

- [ ] **Step 5: Validate (no commit: untracked tree)**

Run: `make validate && make test`
Expected: `All checks passed.` and the suite green.

### Task 2: Hugging Face section, entry template, gateway-only scratch alias, fit gate under bypass (+ 2 evals)

**Files:**
- Modify: `.claude/skills/onboard-local-model/references/onboard-flow.md:22-28` (Hugging Face), `:44-46` (Scout pick), `:63-65` (step 4 download check), `:133-135` and `:144-147` (template)
- Modify: `.claude/skills/onboard-local-model/evals/evals.json` (append 2 cases; reword `hf-repo-argument` assertion 3)
- Modify: `.claude/skills/onboard-local-model/evals/files/hf/model-lab__Foo-9B-GGUF.json` (add `"sha": "0f1e2d3c4b5a69788796a5b4c3d2e1f00f1e2d3c"`) and `tree-lab__Foo-9B-GGUF.json` (add `lfs: {oid, size}` to each file, oids `"4" * 64`, `"5" * 64`, `"8" * 64`), so the existing `hf-repo-argument` flow can pin
- Create: `evals/files/hf/model-lab__Foo-9B-GGUF@v2.json` (the main model JSON plus `"sha": "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"`), `evals/files/hf/tree-lab__Foo-9B-GGUF@v2.json` (`Foo-9B-Q4_K_M.gguf` size 5767168000, lfs oid `"c0ffee00" * 8`; `Foo-9B-Q5_K_M.gguf` size 6815744000, oid `"5" * 64`), `evals/files/models-gateway.yml` (a copy of `evals/files/models.yml` with `llm_server.enabled: false` and every `enabled: false`), `evals/files/preflight-gateway.json` (a copy of `preflight-served.json` with `budget.llmenv_budget: null`, `budget.ram_offload_budget_mib: 0`, `services.llm_server_running: false`, `gpu.holders: []`)
- Test: `tests/skills/onboard-local-model/test_onboard_local_model_scripts.py` (after `test_eval_scout_fixtures_match_their_assertions`)

**Interfaces:**
- Consumes: Task 1 flags and fields (`--revision`, `--file`, `sha`, `pick.bytes`, `pick.sha256`).

- [ ] **Step 1: Write the failing pin tests**

```python
FOO_SHA = "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"
FOO_OID = "c0ffee00" * 8


def test_eval_hf_file_fixtures_match_their_assertions() -> None:
    url = "https://huggingface.co/lab/Foo-9B-GGUF/resolve/v2/Foo-9B-Q4_K_M.gguf"
    env = {**os.environ, "LLM_ENV_CONFIG": str(FIX / "models.yml"), "LLM_ENV_MODELS_DIR": str(FIX / "models")}
    rt = json.loads(_run("bash", SCRIPTS / "resolve-target.sh", url, env=env).stdout)
    assert (rt["kind"], rt["revision"], rt["file"]) == ("hf_file", "v2", "Foo-9B-Q4_K_M.gguf")
    (row,) = json.loads(_run("bash", SCRIPTS / "hf-scout.sh", "--repo", "lab/Foo-9B-GGUF", "--revision", "v2", "--file", "Foo-9B-Q4_K_M.gguf",
                             "--json", "--budget-mib", "8321", "--ram-mib", "12000",
                             env={"HF_FIXTURE_DIR": str(FIX / "hf"), "PATH": "/usr/bin:/bin"}).stdout)
    assert (row["sha"], row["pick"]["mib"], row["pick"]["bytes"], row["pick"]["sha256"]) == (FOO_SHA, 5500, 5767168000, FOO_OID)
    text = _asserts("hf-file-pinned-revision")
    assert all(s in text for s in (f"resolve/{FOO_SHA}/Foo-9B-Q4_K_M.gguf", "5767168000", FOO_OID,
                                   "--revision v2 --file Foo-9B-Q4_K_M.gguf", "Foo-9B-Q5_K_M.gguf", "tree/main"))


def test_eval_gateway_hf_fixtures_match_their_assertions(tmp_path: Path) -> None:
    pre = json.loads((FIX / "preflight-gateway.json").read_text())
    assert pre["budget"]["llmenv_budget"] is None and pre["budget"]["ram_offload_budget_mib"] == 0
    budget = pre["budget"]["model_vram_budget_mib"]
    (row,) = json.loads(_run("bash", SCRIPTS / "hf-scout.sh", "--repo", "lab/Foo-9B-GGUF", "--json", "--budget-mib", str(budget), "--ram-mib", "0",
                             env={"HF_FIXTURE_DIR": str(FIX / "hf"), "PATH": "/usr/bin:/bin"}).stdout)
    assert (budget, row["pick"]["file"]) == (10369, "Foo-9B-Q5_K_M.gguf")
    cfg = yaml.safe_load((FIX / "models-gateway.yml").read_text())
    assert cfg["llm_server"]["enabled"] is False and not [m for m in cfg["models"] if m["enabled"]]
    scratch = _out(tmp_path, "s") / "gateway.yml"
    env = {**os.environ, "LLM_ENV_CONFIG": str(FIX / "models-gateway.yml"), "LLM_ENV_MODELS_DIR": str(FIX / "models")}
    result = _run("bash", SCRIPTS / "resolve-target.sh", "gemma4", "--scratch-config", scratch, env=env)
    assert result.returncode == 0, result.stderr
    s = yaml.safe_load(scratch.read_text())
    assert s["llm_server"]["enabled"] is True and [m["alias"] for m in s["models"] if m["enabled"]] == ["gemma4"]
    text = _asserts("hf-repo-gateway-only")
    assert all(x in text for x in ("10369", "--ram-mib 0", "resolve-target.sh gemma4 --scratch-config", "LLM_ENV_ASSUME_YES=1 make setup", "no baseline"))
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/skills/onboard-local-model -q -k "eval_hf_file or eval_gateway_hf"`
Expected: FAIL (fixtures and eval ids missing).

- [ ] **Step 3: Create the fixtures listed under Files, and append the two evals to `evals.json`**

In the existing `hf-repo-argument` case, assertion 3 reads "... and its false gates or a failed hard gate are stated as G1 facts". Change it to "... and its false gates or a failed `bpw` or `ctx` gate are stated as G1 facts (`fit: no` would stop before G1)", which matches the new section text.

```json
{
  "id": "hf-file-pinned-revision",
  "prompt": "onboard-local-model https://huggingface.co/lab/Foo-9B-GGUF/resolve/v2/Foo-9B-Q4_K_M.gguf. The fixture config is .claude/skills/onboard-local-model/evals/files/models.yml and its models directory is .claude/skills/onboard-local-model/evals/files/models/ (scripts read them through LLM_ENV_CONFIG and LLM_ENV_MODELS_DIR). hf-scout.sh reads its Hugging Face answers from HF_FIXTURE_DIR=.claude/skills/onboard-local-model/evals/files/hf/ instead of the network. preflight.sh already printed .claude/skills/onboard-local-model/evals/files/preflight-served.json; do not run it, use that JSON as its output. DRY RUN for evaluation: read-only commands only (resolve-target.sh, hf-scout.sh, cat, jq); do not download weights, stop containers, edit models.yml or start benchmarks. Show the entry you would add. Answer in English.",
  "files": ["evals/files/models.yml", "evals/files/preflight-served.json", "evals/files/hf/model-lab__Foo-9B-GGUF@v2.json", "evals/files/hf/tree-lab__Foo-9B-GGUF@v2.json"],
  "expected_output": "kind hf_file at revision v2; hf-scout.sh --repo with --revision v2 --file Foo-9B-Q4_K_M.gguf reads that commit, keeps the named file over the scout's Q5_K_M, and the entry pins the resolved sha with the exact size and sha256 from that one row.",
  "assertions": [
    "resolve-target.sh classifies the URL as kind hf_file with repo lab/Foo-9B-GGUF, revision v2 and file Foo-9B-Q4_K_M.gguf",
    "hf-scout.sh runs with --repo lab/Foo-9B-GGUF --revision v2 --file Foo-9B-Q4_K_M.gguf --json --budget-mib 8321 --ram-mib 12000",
    "The named Foo-9B-Q4_K_M.gguf is the one file (5500 MiB) even though the scout alone would pick Foo-9B-Q5_K_M.gguf",
    "The entry url is https://huggingface.co/lab/Foo-9B-GGUF/resolve/a1b2c3d4e5f60718293a4b5c6d7e8f9012345678/Foo-9B-Q4_K_M.gguf (the resolved sha, not v2 or main) with size_bytes 5767168000",
    "The download check is sha256 c0ffee00c0ffee00c0ffee00c0ffee00c0ffee00c0ffee00c0ffee00c0ffee00 from the same row; nothing is read from tree/main",
    "Nothing was downloaded, stopped, started or edited"
  ]
}
```

```json
{
  "id": "hf-repo-gateway-only",
  "prompt": "set up https://huggingface.co/lab/Foo-9B-GGUF on this box. The fixture config is .claude/skills/onboard-local-model/evals/files/models-gateway.yml and its models directory is .claude/skills/onboard-local-model/evals/files/models/ (scripts read them through LLM_ENV_CONFIG and LLM_ENV_MODELS_DIR). hf-scout.sh reads its Hugging Face answers from HF_FIXTURE_DIR=.claude/skills/onboard-local-model/evals/files/hf/. preflight.sh already printed .claude/skills/onboard-local-model/evals/files/preflight-gateway.json; use it as its output. DRY RUN for evaluation: only resolve-target.sh, hf-scout.sh, cat, yq and jq; a scratch config under ./tmp/ is allowed and removed at the end. Give me the ordered plan with its gates. Answer in English.",
  "files": ["evals/files/models-gateway.yml", "evals/files/preflight-gateway.json", "evals/files/hf/model-lab__Foo-9B-GGUF.json", "evals/files/hf/tree-lab__Foo-9B-GGUF.json"],
  "expected_output": "A gateway-only host: the budget is model_vram_budget_mib because llmenv_budget is null; the scratch config comes from any alias of the live file; there is no baseline, so no baseline lane or bench; G3 enables local inference and runs setup unattended.",
  "assertions": [
    "The budget is model_vram_budget_mib 10369 because llmenv_budget is null, and the answer says so",
    "hf-scout.sh --repo lab/Foo-9B-GGUF --json --budget-mib 10369 --ram-mib 0 picks Foo-9B-Q5_K_M.gguf (6500 MiB), and the RAM budget of 0 is explained (llm-server disabled)",
    "The scratch config comes from resolve-target.sh gemma4 --scratch-config under ./tmp/ (any alias of the live file; none is enabled), with the entry added by yq; the live file is not copied",
    "There is no baseline: no baseline lane, no baseline bench; the candidate lane is the first lane with --per-layer 3, and step 8 skips (b) and (c) and says so",
    "G3 lists the entry add, make llama-enable MODEL=<alias> and LLM_ENV_ASSUME_YES=1 make setup, each after its gate, with a rollback copy first",
    "The scratch config is removed at the end; nothing was downloaded, stopped, started or edited"
  ]
}
```

- [ ] **Step 4: Rewrite onboard-flow.md lines 24-28 (Hugging Face body) as:**

```text
`bash $SK/scripts/hf-scout.sh --repo <repo> --json --budget-mib <rule 1 budget> --ram-mib <budget.ram_offload_budget_mib>`
(null: pass `ram.available_mib` and say so), plus `--revision <revision> --file <file>` from the `hf_file` JSON, or
`--file <pick file>` for a scout pick. The row is read at one commit, `sha` (the revision resolved, `main` by default). `pick` is the
named file whatever the scout would choose (exit 2: the file is not in that commit; report and stop), else the scout's choice.
`sha`, `pick.bytes` and `pick.sha256` fill the entry (template below); never re-read `tree/main` for them. `fit: "unknown"` (the
tree was unreadable): retry once, else report and stop, because G1 needs the size. One research subagent writes its evidence card
per the research brief loaded with this route. A false card gate, or a failed `bpw` or `ctx` gate, is a G1 fact; under bypass it
proceeds, because the owner named the model. `fit: "no"` stops before G1, under bypass too: report the fit math and the repo's
largest fitting quant (`hf-scout.sh --repo <repo>` without `--file`); only an explicit owner request after that report downloads it.
Scratch config: `bash $SK/scripts/resolve-target.sh <alias> --scratch-config $S` with any alias of the live file (the baseline
when one is enabled; on a gateway-only host any entry, since step 4's `models select` switches to the candidate), then add the
entry (template below) to `$S` with `yq -i`, `enabled: false`. Never `cp` the live file into `tmp/`; with no alias in the live file,
stop and report.
```

Scout pick (line 46) becomes: As Hugging Face, with the pick's repo and `--file <pick file>`; its evidence card from the ranking replaces the research subagent.

Step 4 (line 65): after "confirm the byte size equals `size_bytes`", add "and, for a Hugging Face file, `sha256sum <models_dir>/<file>` equals the row's `pick.sha256`".

Template lines 133-135:

```yaml
  file: <name>.gguf                   # the base name of pick.file
  url: https://huggingface.co/<org>/<repo>/resolve/<sha>/<pick.file>   # the scout row's sha, never "main" or a branch
  size_bytes: 21155768832             # exact: the scout row's pick.bytes
```

Lines 145-147 ("Sha and size: ... (sha256).") become: "Sha, size and checksum come from the one `hf-scout.sh --repo` row of the Hugging Face section (`sha`, `pick.bytes`, `pick.sha256`, all read at one commit). A null `sha`, or a null `pick.sha256` (a split file; an entry takes one file), stops before G1: report it."

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/skills/onboard-local-model -q -k "eval_ or references or prose or name_only or black_box"`
Expected: PASS. Then `rg -n "baseline alias|tree/main|named file, else" .claude/skills/onboard-local-model/references/onboard-flow.md` prints nothing.

### Task 3: Onboard steps: unattended setup, one build per comparison, served-alias restore

**Files:**
- Modify: `.claude/skills/onboard-local-model/references/onboard-flow.md:56-58` (step 2), `:76-77` (step 5), `:82-87` (step 6), `:96-98` (7a), `:104` (7c), `:111-113` (step 8)

No script changes. Verification is by grep plus the Task 8 route walk.

- [ ] **Step 1: Step 2 (lines 56-57).** Replace "Depth numbers come from its capacity card (`uv run llmenv.py capacity show --alias <a>`, depth sweep section, when not listed stale), else step 6 benches it." with "Its capacity card's depth numbers (`uv run llmenv.py capacity show --alias <a>`) are reported, never compared: step 6 benches it beside the candidate on one image, because neither the card nor the bench JSON records the llama.cpp build."

- [ ] **Step 2: Step 5.** After "For the served alias this lane is its only one." add "Record the build it ran on: `podman inspect --format '{{.Image}}' llm-server`."

- [ ] **Step 3: Step 6.** Replace "A baseline without a current card runs the same command first, with the same `--depths`." with "The baseline (when there is one and it is not the served alias itself) runs the same command first on the live config, with the same `--depths`." After "restore per step 9 and stop" add "(for the served alias, step 9 is only `make start-llama`)".

- [ ] **Step 4: 7a (lines 96-98).** Replace "`make setup`\n     (downloads a missing enabled file, validates the GGUFs, pulls the floating Vulkan tag, runs the budget check; its GPU prompt\n     defaults to yes now; `make check-setup` validates offline)" with:

```text
`LLM_ENV_ASSUME_YES=1 make setup`
     (unattended per AGENTS.md: every prompt takes its default from the live file, so the GPU prompt answers yes after
     `make llama-enable`; it downloads a missing enabled file, validates the GGUFs, pulls the floating Vulkan tag and runs the budget
     check; a Vulkan device selection error means two GPUs match: hand `make setup` to the owner; `make check-setup` validates offline)
```

- [ ] **Step 5: 7c (line 104).** Before "The candidate lane takes" insert: "First read the same `podman inspect` Id. `make start-llama` never recreates the container (`--no-recreate`), so the pull in 7a leaves it equal to step 5's. A different Id (the container was recreated, for example by `make start`) means the lanes ran on two llama.cpp builds, and step 8 reports (b) as unproven. Without a baseline lane, only record it."

- [ ] **Step 6: Step 8 (lines 111-113).** (b) becomes "it passes the replace rule (Score, item 6, of the effort-ladder reference) on one seed set and one build (the step 5 and 7c Ids are equal; unproven: no replace, and offer to re-run the baseline lane on the 7c build through its own gated swap)". (c) becomes "pp and tg at the reference depth from step 6 (one image for both) stay within about 3 % ...". After the table sentence, add: "When `podman image inspect --format '{{.Id}}' <gpu.image>` after 7a differs from the 7c Id, the table says the next container recreation (`make restart`) serves a build these numbers did not measure."

- [ ] **Step 7: Verify**

Run: `rg -n "LLM_ENV_ASSUME_YES=1 make setup|--no-recreate|podman inspect --format|step 9 is only" .claude/skills/onboard-local-model/references/onboard-flow.md` (4+ hits) and `rg -n "without a current card|defaults to yes now" .claude/skills/onboard-local-model/references/onboard-flow.md` (no hits). Then `uv run pytest tests/skills/onboard-local-model -q -k "prose or references or name_only"`. Expected: PASS.

### Task 4: Scout calls with budget flags, pre-download offload estimate, one seed set across picks (+ 1 eval)

**Files:**
- Modify: `.claude/skills/onboard-local-model/references/selection-criteria.md:8-10`, `:18-19`, `:20-22`, `:27-28`, section 2 (after line 52), speed row (line 111)
- Create: `.claude/skills/onboard-local-model/evals/files/hf-empty/models.json` (content `[]`)
- Modify: `evals/evals.json` (append 1 case)
- Test: `tests/skills/onboard-local-model/test_onboard_local_model_scripts.py`

- [ ] **Step 1: Write the failing pin test**

```python
def test_eval_bare_name_fixture_matches_its_assertions() -> None:
    env = {**os.environ, "LLM_ENV_CONFIG": str(FIX / "models.yml"), "LLM_ENV_MODELS_DIR": str(FIX / "models")}
    assert _run("bash", SCRIPTS / "resolve-target.sh", "Zeta-Coder-7B", env=env).returncode == 2
    out = _run("bash", SCRIPTS / "hf-scout.sh", "--query", "Zeta-Coder-7B", "--json", "--budget-mib", "8321", "--ram-mib", "12000",
               env={"HF_FIXTURE_DIR": str(FIX / "hf-empty"), "PATH": "/usr/bin:/bin"})
    assert out.returncode == 0 and json.loads(out.stdout) == []
    assert all(s in _asserts("bare-name-no-repo") for s in ("exits 2", "--budget-mib 8321 --ram-mib 12000", "[]", "stops"))
```

- [ ] **Step 2: Run it to verify it fails** with `uv run pytest tests/skills/onboard-local-model -q -k eval_bare_name`. Expected: FAIL (no such eval).

- [ ] **Step 3: Append the eval**

```json
{
  "id": "bare-name-no-repo",
  "prompt": "onboard-local-model Zeta-Coder-7B. The fixture config is .claude/skills/onboard-local-model/evals/files/models.yml (LLM_ENV_CONFIG, models directory .claude/skills/onboard-local-model/evals/files/models/ as LLM_ENV_MODELS_DIR). hf-scout.sh reads its Hugging Face answers from HF_FIXTURE_DIR=.claude/skills/onboard-local-model/evals/files/hf-empty/. preflight.sh already printed .claude/skills/onboard-local-model/evals/files/preflight-served.json; use it as its output. DRY RUN for evaluation: read-only commands only. Answer in English.",
  "files": ["evals/files/models.yml", "evals/files/preflight-served.json", "evals/files/hf-empty/models.json"],
  "expected_output": "resolve-target.sh exits 2 (a bare name), the Find the repo search returns no GGUF repo, and the run reports that and stops without a download or a gate.",
  "assertions": [
    "resolve-target.sh Zeta-Coder-7B exits 2: it is none of the four forms",
    "The search is hf-scout.sh --query Zeta-Coder-7B --json --budget-mib 8321 --ram-mib 12000, and it prints []",
    "The answer says [] means no GGUF repo, or every match is under 131072 context or a modded variant, and asks for a repo id or URL",
    "The run stops there: no G1, no download, no scratch config, and nothing was stopped, started or edited"
  ]
}
```

- [ ] **Step 4: Edit selection-criteria.md**

- Lines 8-10: the search becomes `bash $SK/scripts/hf-scout.sh --query "<name>" --json --budget-mib <rule 1 budget> --ram-mib <budget.ram_offload_budget_mib>` (null: pass `ram.available_mib` and say so), and "None: report and stop." becomes "None (`[]`: no GGUF repo, or every match is under 131072 context or a modded variant): report that, ask for a repo id or URL, and stop."
- Lines 18-19: the same two flags go after `--json` in the per-term call, and the same null note follows.
- Lines 20-22: "only with `ram.available_mib` >= its expert bytes + 4 GiB" becomes "only with `ram.available_mib` >= its offload RAM estimate (section 2) + 4 GiB".
- Section 2, a new bullet after line 52: "- Before download, a scout row's offload RAM estimate is `pick.mib + 2048 - <rule 1 budget>` MiB: what does not fit in VRAM once about 2 GiB is left for KV cache and buffers (the arithmetic of `hf-scout.sh`'s `offload` class). Once the GGUF is on disk, the solve above replaces it."
- Speed row, level 0: "MoE offload with under 4 GiB RAM spare" becomes "MoE offload with under 4 GiB RAM spare beyond its section 2 estimate".
- Lines 27-28: replace "At most 3 go on, and only when the owner asked to onboard: each continues on `SKILL.md`'s Hugging Face row as a scout pick." with "At most 3 go on, and only when the owner asked to onboard, one at a time in rank order, each on `SKILL.md`'s Hugging Face row as a scout pick and all on one seed set. The first pick's step 5 baseline lane writes `seeds.json` and its step 6 benches the baseline. Later picks skip that lane and pass `--seeds` of it to their candidate lane. They reuse that bench only while `podman image inspect --format '{{.Id}}' <gpu.image>` is unchanged; otherwise their step 6 benches the baseline again. Each pick's step 8 runs one `compare-runs.sh` with the baseline first over every lane so far. A kept pick (G5 yes) ends the list."

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/skills/onboard-local-model -q -k "eval_ or references or prose"`, then `rg -n "hf-scout.sh --query" .claude/skills/onboard-local-model/references/selection-criteria.md`. Expected: PASS, and every hit contains `--budget-mib`.

### Task 5: G-Q3 decision rule, yes branch and quant order line (+ 1 eval)

**Files:**
- Modify: `.claude/skills/onboard-local-model/references/tuning.md:111` (order line after it), `:144-145` (G-Q3)
- Create: `.claude/skills/onboard-local-model/evals/files/quant/q4.jsonl`, `q5.jsonl`
- Modify: `evals/evals.json` (append 1 case)
- Test: `tests/skills/onboard-local-model/test_onboard_local_model_scripts.py`

**Interfaces:**
- Consumes: `compare-runs.sh` output `delta_vs_first[1].{ladder, overall}` (unchanged script).

- [ ] **Step 1: Write the failing pin test**

```python
def test_eval_quant_switch_fixtures_match_their_assertions() -> None:
    out = json.loads(_run("bash", SCRIPTS / "compare-runs.sh", FIX / "quant" / "q4.jsonl", FIX / "quant" / "q5.jsonl").stdout)
    delta = out["delta_vs_first"][1]
    assert (delta["ladder"], delta["overall"]) == (1, 15.6) and out["warnings"] == []
    text = _asserts("quant-switch-yes")
    assert all(s in text for s in ("ladder delta 1", "overall delta 15.6", "make llama-enable MODEL=gemma4-q5_k_m", "make local-combo",
                                   "make llama-enable MODEL=gemma4,"))
```

- [ ] **Step 2: Run it to verify it fails** with `uv run pytest tests/skills/onboard-local-model -q -k eval_quant_switch`. Expected: FAIL (missing files).

- [ ] **Step 3: Create the lanes.** Each file has three rows, one per task `l1-s1`, `l2-s1`, `l3-s1`, all with effort `low`, `total` 3, `seconds` 60, `api_error` false, `gpu` `{"peak_temp_c": 60, "avg_cpu_pct": 0, "peak_vram_mib": 1}`, `generator` `g-fixture` and `timeout_s` 300. In `q4.jsonl` (`model` `gemma4`) the passes are 3, 3, 1. In `q5.jsonl` (`model` `gemma4-q5_k_m`) they are 3, 3, 3. A dry run in scratch on 2026-10-31 gave ladders 2 and 3, overall 82.9 and 98.5, and delta ladder 1 and overall 15.6. Then append:

```json
{
  "id": "quant-switch-yes",
  "prompt": "the Q4_K_M vs Q5_K_M comparison of gemma4 is done. Lanes: .claude/skills/onboard-local-model/evals/files/quant/q4.jsonl (gemma4, first) and .claude/skills/onboard-local-model/evals/files/quant/q5.jsonl (gemma4-q5_k_m), same seeds. quant-headroom.sh record printed tg -2.1 % and pp -1.4 % at the reference depth for gemma4-q5_k_m; its Q7 interference gate passed; Q1 recorded that my-coding-lite exists. Should I switch? If yes, I approve G-Q3. DRY RUN for evaluation: run only compare-runs.sh on the two files; list the commands you would run, in order, without running them. Answer in English.",
  "files": ["evals/files/quant/q4.jsonl", "evals/files/quant/q5.jsonl"],
  "expected_output": "The candidate qualifies under the G-Q3 rule (replace rule passed, pp/tg within 3 %, interference passed); the yes branch serves gemma4-q5_k_m, refreshes the router profile and my-coding-lite, and keeps gemma4 disabled as the rollback.",
  "assertions": [
    "compare-runs.sh with q4.jsonl first gives ladder delta 1 and overall delta 15.6 for gemma4-q5_k_m: the replace rule passes (ladder delta >= 0 and overall delta > 10)",
    "The decision also checks tg and pp within about 3 % of gemma4 (-2.1 % and -1.4 %) and the passed Q7 interference gate",
    "The yes branch, in order: make stop-llama, make llama-enable MODEL=gemma4-q5_k_m, make start-llama, make check-server, make setup-local-llm-agents, make local-combo (Q1 recorded my-coding-lite)",
    "gemma4 stays in models.yml, disabled, as the rollback: make stop-llama, make llama-enable MODEL=gemma4, make start-llama",
    "Nothing was executed besides compare-runs.sh"
  ]
}
```

- [ ] **Step 4: Edit tuning.md**

- After line 111 ("... exit 3 of `compare-runs.sh` means it is not a head-to-head."), add: "Order: Q1; then Q2-Q11 for each candidate with `fits: true`, in ascending size (Q11 always restores `<a>`); then G-Q3 once over every measured candidate."
- Replace lines 144-145 (the G-Q3 bullet) with:

```text
- Gate G-Q3 (switch the default quant), once after the loop. A candidate `<b>` qualifies only when its Q7 gate passed, the Q8
  `compare-runs.sh` (`<a>` first) passes the replace rule (Score, item 6, of the effort-ladder reference), and its Q10 tg and pp at the
  reference depth stay within about 3 % of `<a>` or the slowdown is stated as the trade. None qualifies: keep `<a>` and name the part
  each one failed. Several: the highest `overall`, ties to the smaller file. Ask with the comparison table (quant, bpw, ctx, pp and tg
  at the reference depth, VRAM, ladder and score deltas); under bypass announce it and keep `<a>` (`SKILL.md` Gates).
- G-Q3 yes: `make stop-llama`, `make llama-enable MODEL=<b>` (it backs up models.yml first), `make start-llama`, `make check-server`,
  `make setup-local-llm-agents`, then `make local-combo` only when Q1 recorded `my-coding-lite`. `<a>` stays in models.yml, disabled,
  as the rollback: Q11's first three commands bring it back.
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/skills/onboard-local-model -q -k "eval_ or references or prose"`. Expected: PASS (`tuning.md` names no other reference file).

### Task 6: SKILL.md router: name `scoring.md` per row, list the cross-route rules, drop restatements, add the challenge trigger

**Files:**
- Modify: `.claude/skills/onboard-local-model/SKILL.md:3,9-11,20-22,24,31-34,60,110-112`

The exact edits were measured on a scratch copy (10,907 -> 10,811 bytes; description 813 -> 860 characters):

- [ ] **Step 1: Line 3 (description).** Replace "; or asks whether the llama.cpp image (Vulkan/ROCm) or Laya is outdated, even as" with "; asks whether the llama.cpp image (Vulkan/ROCm) or Laya is outdated; or asks to generate or verify one ladder challenge, even as".

- [ ] **Step 2: Lines 9-11.** Replace them with:

```text
Goal: a coding model for pi (>= 128K context) that fits beside a resident System One, proven by measurement, not model-card
claims. The ladder needs `git node pi`.
```

- [ ] **Step 3: Route rows.** On rows 20 and 22, append "; `references/scoring.md` at step 5" after "sections 1-3 at step 4". On row 21, append it after "sections 1-5 and 7". On row 24, replace "once the owner picks a row to onboard, continue on that row's flow (the Hugging Face row), which loads `references/scoring.md` before its ladder" with "each row the owner picks to onboard continues on the Hugging Face row (Scout pick)".

- [ ] **Step 4: Lines 31-34.** Replace them with:

```text
- Every route that runs `effort-run.sh` reads `references/scoring.md` (the effort-ladder reference) before its first lane.
- `references/challenges.md` is only for generating or verifying one challenge, for authoring, debugging or calibrating the
  challenge library, and for a `leak_suspect` row.
- When two rows match, the model argument wins and an anchor named with it joins that flow's quality step.
- A read-only question never starts a gated step. Before the first gate name the number that will decide, the baseline it is
  compared with (or none) and the depth (throughput decisions only); without them, stay read-only.
```

- [ ] **Step 5: G1 row (line 60).** Replace "any failed hard gate of a model the owner named" with "any failed card, `bpw` or `ctx` gate of a model the owner named (`fit: no` stops before G1)".

- [ ] **Step 6: Gotchas.** Delete line 110 (the `hf-scout.sh` modded line; selection-criteria.md:84-86 already states it). Replace lines 111-112 with "- `make benchmark` only as AGENTS.md allows: after `make stop-llama` and a fresh backup, then check `gpu.backend`/`gpu.image`."

- [ ] **Step 7: Verify**

Run: `wc -c .claude/skills/onboard-local-model/SKILL.md` (<= 11,000; expected about 10,811), then `uv run pytest tests/skills/onboard-local-model -q -k "router or description or gates or isolated or black_box or name_only"`. Expected: PASS.

### Task 7: Lows: effort-run router-profile default, two near-miss negatives, two clarifiers

**Files:**
- Modify: `.claude/skills/onboard-local-model/scripts/effort-run.sh:30-31,119`
- Modify: `.claude/skills/onboard-local-model/evals/trigger-queries.json` (entries 17 and 19, 0-based)
- Modify: `.claude/skills/onboard-local-model/references/challenges.md:51`, `references/scoring.md:26`
- Test: `tests/skills/onboard-local-model/test_onboard_local_model_scripts.py` (`_stub_bin` uv stub, line ~447; a new test after `test_effort_run_defaults_to_the_runner_cap_and_refuses_a_larger_timeout`)

- [ ] **Step 1: Write the failing test.** In `_stub_bin`'s uv stub, inside the `*run-agent-bounded*` branch, add `[ -z "${STUB_ENV:-}" ] || printf 'PI_CODING_AGENT_DIR=%s\n' "$PI_CODING_AGENT_DIR" >>"$STUB_ENV"`. Then:

```python
@NEEDS_NODE
def test_effort_run_defaults_to_the_router_profile(tmp_path: Path) -> None:
    log = tmp_path / "env.txt"
    result = _effort_run(tmp_path, "--layers", "1", "--seeds", str(_seeds_file(tmp_path, {"1": [1]})), env={"STUB_ENV": str(log)})
    assert result.returncode == 0, result.stderr
    assert set(log.read_text().splitlines()) == {f"PI_CODING_AGENT_DIR={tmp_path / 'home' / '.pi' / 'agent'}"}
```

- [ ] **Step 2: Run it to verify it fails** with `uv run pytest tests/skills/onboard-local-model -q -k router_profile`. Expected: FAIL (`.../home/.config/llm-env/pi`).

- [ ] **Step 3: Implement.** Change line 119 to `export PI_CODING_AGENT_DIR="${PI_CODING_AGENT_DIR:-$HOME/.pi/agent}"`. Change header lines 30-31 to "Uses the router profile written by `make setup-local-llm-agents` (PI_CODING_AGENT_DIR defaults to ~/.pi/agent) ...".

- [ ] **Step 4: Text lows.**
- trigger-queries entry 17 becomes `{"query": "write a python script that reads the header of the gguf llama-server serves right now and prints its tensor sizes", "should_trigger": false}`, and entry 19 becomes `{"query": "fine-tune gemma4, the model llm-server serves, with LoRA on our commit history so pi gets better at our code", "should_trigger": false}`.
- challenges.md:51 notes: "data file `data/run.log`" becomes "seeded data file `data/run.log` (the shipped reference test reads `data/app.log`)".
- scoring.md:26: "Screen with `--efforts low,high`" becomes "Screen with `--efforts low,high` (`--per-layer 1`, the default)".

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/skills/onboard-local-model -q -k "effort_run or help or prose"`, then `make validate && make test`. Expected: PASS and `All checks passed.`

### Task 8: Verification, route walk, cleanup, index row

**Files:**
- Modify: `docs/superpowers/plans/README.md` (this plan's row: status, executed date)

- [ ] **Step 1: Full checks.** Run `make validate && make test` and `uv run pytest tests/skills -q`. Expected: all green. The skill suite gains 10 test functions (11 cases).
- [ ] **Step 2: Guard and cap.** Run `uv run pytest tests/skills/onboard-local-model -q -k "black_box or router or references_never"` and `wc -c .claude/skills/onboard-local-model/SKILL.md`. Expected: PASS, and <= 11,000 bytes. Run `rg -n -i "ai-kit" .claude/skills/onboard-local-model`; expected: no hits.
- [ ] **Step 3: Route walk.** Read each route as the agent would, and confirm that every placeholder is defined, every named section exists and every branch ends in a determined outcome:
  1. **Model given.** `resolve-target.sh` gives one of four kinds. For `alias`, the Alias section then steps 1-9. For `hf_repo`, the Hugging Face section (no `--file`). For `hf_file`, the same section with `--revision`/`--file`: one commit, pin `sha`, bytes and sha256 from the row, exit 2 on an absent file, stop on `fit: no`, a null `sha` or a null `sha256`. For `gguf_file`, the Local GGUF section. Each row loads `scoring.md` at step 5. Check that steps 5/7c/8 compare one build and that step 6 benches the baseline beside the candidate. Check that 7a runs `LLM_ENV_ASSUME_YES=1 make setup` and that a failed screen of the served alias ends in `make start-llama`. On a gateway-only host: any alias for `$S`, no baseline, (b)/(c) skipped.
  2. **None given.** Scout and rank: hf-scout calls with both budget flags (null RAM: `ram.available_mib`), the offload estimate from section 2, rank, cards, `disagreements`, then at most 3 picks on one seed set, one at a time, ending at a kept pick, each on the Hugging Face row with `--file <pick file>`. A bare name: `[]` means report and stop.
  3. **Compare quants.** Standalone quant comparison: the order line, T1-T8 when the card or seeds are missing, Q1, Q2-Q11 per `fits: true` candidate, then G-Q3: the rule, none/several/one, bypass keeps `<a>`, and the yes branch with `<a>` as the rollback.
  Expected: no undefined name (`rg -n "baseline alias|expert bytes|tree/main" .claude/skills/onboard-local-model/references` prints nothing) and no contradiction between SKILL.md G1 and the Hugging Face section.
- [ ] **Step 4: Cleanup.** `podman ps -a` shows nothing started by this plan. Remove any scratch this run created under `./tmp/` (tests clean their own `_out` dirs). Do not touch `tmp/judges/`.
- [ ] **Step 5: Index.** Set this plan's row in `docs/superpowers/plans/README.md` to **DONE** with the execution date. Add "skill edits uncommitted with the untracked skill tree; judges re-run by the orchestrator". Commit nothing unless the owner asks.

## Round-1 review outcome (approved; 1 confirmed high, 5 mediums)

Review round 1 (native + referee-two, lead-verified against the code; tmp/plan-h-review/review-round-1.md): the referee's two critical
and four of its highs were refuted, its README high was downgraded to a medium. Plan status: approved for execution. These amendments
override the task text above where they disagree:

- H1 (Task 2, new Step 0, red-first): `resolve-target.sh <alias> --scratch-config` exits 1 on a gateway-only config where every model
  is disabled, because it writes `.llm_server.enabled = true` before `llmenv models select`, and `pylib/config.py` (~490-492) rejects
  zero enabled models while llm-server is enabled. The ledger row "resolve-target: no change" is void. Write a test
  (`test_resolve_target_scratch_config_on_a_gateway_only_config`) on `evals/files/models-gateway.yml` that expects exit 0, the target
  alone enabled and `llm_server.enabled: true` in the scratch file; see it fail; then change the order in `resolve-target.sh`: write the
  redacted copy without the flip, add the gguf `candidate`, run `models select <target>`, and only then set `.llm_server.enabled = true`
  (`yq -i`). Update its header. `resolve-target.sh` is inside the untracked skill tree (no commit).
- M1 (Task 2 Step 5, Task 8 Step 3): the greps must not forbid the new "never re-read `tree/main`" sentence. Drop `tree/main` from both
  no-hit greps, and instead check that `rg -n "tree/main" .claude/skills/onboard-local-model/references` prints only that one sentence.
- M2 (Task 2 fixtures and text): `preflight-gateway.json` models a real gateway-only host: `services.llm_server_enabled: false`,
  `services.llm_server_running: false`, `runtime.enabled_models: []`, `gpu.used_mib` 6200 (13650 - the 7450 MiB llama-server),
  `gpu.free_mib` 10105, `gpu.holders` only the System One `python3` entry, `budget.llmenv_budget: null`,
  `budget.ram_offload_budget_mib: 0`. "No enabled entry" is the wrong gateway test, because `make llama-disable` keeps the enabled
  entry: onboard-flow step 2 and step 8 key "no baseline" on `llm_server.enabled: false` (preflight `services.llm_server_enabled`
  false), even when an entry stays enabled, or on no enabled entry. The gateway pin test also asserts
  `pre["services"]["llm_server_enabled"] is False`.
- M3 (Task 1): validate `--revision` at the trust boundary (it comes from a pasted URL and reaches the API path and the fixture name):
  `^[A-Za-z0-9._%-]+$` and no `..` (this rejects `..`, `?`, `/` and whitespace), else exit 2 naming `--revision`. Test:
  `test_hf_scout_rejects_an_unsafe_revision`, parametrized over `..`, `../../evil`, `v2?x=1`, `a/b`, `v 2`.
- M4 (Global Constraints): the one allowed tracked edit is this plan's row in `docs/superpowers/plans/README.md` (Task 8 Step 5), the
  same exception plan 2026-10-31-skill-navigation-restructure listed. No other tracked file changes; nothing is committed unless the
  owner asks.
- M5 (Task 8 Step 1): the skill suite gains 11 test functions (16 cases): Task 1 five (10 cases), Task 2 three, Tasks 4, 5 and 7 one each.
- Lows taken: (a) 7c spells out `podman inspect --format '{{.Image}}' llm-server` (the image Id, comparable with
  `podman image inspect --format '{{.Id}}' <gpu.image>`); (b) 7a says the GPU prompt takes its default from the live file, and a Vulkan
  device selection error means no or several matching devices; (c) the G-Q3 rollback is all of Q11, not its first three commands;
  (d) the `llmenv-null` case id becomes `llmenv-error` (the stub makes `llmenv_budget` an object with null fields); (e) the network
  tree fetch uses `xargs -n2` with fixed leading args (`"$api" "$tmp" "$sfx"`), since `-I{}` passes the whole line as one argument.
