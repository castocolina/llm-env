# Transparent Checks and Vulkan-Only Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make setup and every check explain its exact work and results, fix the live-agent check from real evidence, and remove unsupported ROCm behavior while retaining the user’s existing host image.

**Architecture:** Bash check scripts render redacted execution records before and after each operation. A shared redaction helper removes the configured API key and bearer values from displayed or retained output. `benchmark.sh` has one supported GPU backend—Vulkan—then preserves CPU fallback. `check-with-agents.sh` fetches a fresh public-source snapshot per matrix row and compares it to one parsed, optionally fenced JSON object from each agent.

**Tech Stack:** Bash, shellcheck, curl, jq, Mike Farah yq v4, pytest, Podman, llama.cpp Vulkan image, Pi, OpenCode, Open-Meteo, ExchangeRate-API.

## Global Constraints

- Support Bazzite/Fedora only; all output is English.
- Invoke Python only as `uv run llmenv.py <subcommand>`.
- Display every check command, prompt/payload, raw redacted result, parsed result, expectation, and precise verdict by default.
- Never display or retain the local API key, curl config content, or bearer header value. Render them as `<redacted>`.
- `LLM_ENV_KEEP_CHECK_ARTIFACTS=1` retains only a mode-700 redacted diagnostic directory; default cleanup removes it.
- `make check-server` remains local and deterministic. `make check-with-agents` is the only Internet-dependent target.
- Remove ROCm from project behavior and generated configuration. Do not run `podman rmi`, alter host ROCm, or delete the existing host ROCm image.
- Every shell change requires `make validate`; every Python/test change requires `make validate && make test`.
- Makefile targets longer than three lines delegate to shell scripts.

---

## File Structure

| File | Responsibility |
|---|---|
| `lib.sh` | Shared redaction, command display, diagnostic workspace, and artifact-retention helpers |
| `setup.sh` | Numbered GPU rows with measured total, used, and free VRAM |
| `benchmark.sh` | Vulkan-only benchmark with visible command/output and CPU fallback |
| `check-setup.sh` | Detailed static, device-resolution, budget, and offline inference records |
| `check-server.sh` | Detailed redacted curl request/response records and normalized `ready` contract |
| `check-with-agents.sh` | Detailed agent matrix trace, per-row live snapshots, fenced-object parsing, and evidence comparisons |
| `models.yml.example` | Vulkan-only benchmark configuration |
| `pylib/config.py` | Accept/migrate Vulkan-only benchmark schema |
| `tests/test_shell.py` | Shell command, redaction, agent parsing, and diagnostic regression tests |
| `tests/test_config.py` | Vulkan-only configuration validation/migration tests |
| `README.md`, `QUICK_START.md` | Vulkan-only wording and diagnostic command documentation |

## Task 1: Shared safe diagnostic rendering

**Files:**
- Modify: `lib.sh`, `tests/test_shell.py`

**Interfaces:**
- `redact_text <text>` writes text with the current `.server.api_key` and `Authorization: Bearer <value>` replaced by `<redacted>`.
- `log_command <command-text>` prints `Command: <redacted command-text>`.
- `log_block <label> <text>` prints `label:` followed by an indented redacted block; an empty value prints `(empty)`.
- `prepare_diagnostic_dir <name>` returns a mode-700 directory under `mktemp -d`; `finish_diagnostic_dir <dir>` removes it unless `LLM_ENV_KEEP_CHECK_ARTIFACTS=1`, in which case it prints the redacted path.

- [ ] **Step 1: Add failing redaction and artifact tests**

Add a shell harness test that configures `api_key: fixture-secret` and invokes a tiny sourced `lib.sh` script:

```python
def test_diagnostic_helpers_redact_api_keys_and_bearer_headers(tmp_path):
    result, artifact = run_diagnostic_helper(
        tmp_path,
        "fixture-secret Authorization: Bearer another-secret",
    )
    assert result.returncode == 0
    assert "fixture-secret" not in result.stdout + result.stderr
    assert "another-secret" not in result.stdout + result.stderr
    assert "<redacted>" in result.stdout
    assert not artifact.exists()


def test_diagnostic_helper_keeps_only_private_redacted_artifacts(tmp_path):
    result, artifact = run_diagnostic_helper(tmp_path, "fixture-secret", keep=True)
    assert result.returncode == 0
    assert artifact.is_dir()
    assert stat.S_IMODE(artifact.stat().st_mode) == 0o700
    assert "fixture-secret" not in "".join(path.read_text() for path in artifact.rglob("*"))
```

- [ ] **Step 2: Verify tests fail**

Run: `uv run --with pytest pytest tests/test_shell.py -k diagnostic_helper -v`

Expected: FAIL because the helper functions do not exist.

- [ ] **Step 3: Implement redaction and artifact helpers**

Add safe helpers to `lib.sh`. `redact_text` must read the key without printing it, then redact fixed-string values using `sed` with escaped input. It must also redact generic bearer values:

```bash
redact_text() {
    local text="$1" key escaped
    key="$(yq -r '.server.api_key // empty' "$CONFIG_PATH" 2>/dev/null)"
    if [ -n "$key" ]; then
        escaped="$(printf '%s' "$key" | sed 's/[][\\.^$*+?{}|()/]/\\&/g')"
        text="$(printf '%s' "$text" | sed "s/${escaped}/<redacted>/g")"
    fi
    printf '%s' "$text" | sed -E 's/(Authorization:[[:space:]]*Bearer)[[:space:]]+[^[:space:]"'"']+/\1 <redacted>/g'
}
```

Keep raw command output only in private temporary files until it is redacted. `finish_diagnostic_dir` must redact every retained regular file before printing its path.

- [ ] **Step 4: Run focused tests and full verification**

Run: `uv run --with pytest pytest tests/test_shell.py -k diagnostic_helper -v`

Expected: PASS.

Run: `make validate && make test`

- [ ] **Step 5: Commit**

```bash
git add lib.sh tests/test_shell.py
git commit -m "feat: add redacted diagnostic helpers"
```

## Task 2: Measured VRAM selection and Vulkan-only benchmark

**Files:**
- Modify: `setup.sh`, `benchmark.sh`, `models.yml.example`, `pylib/config.py`
- Modify: `tests/test_shell.py`, `tests/test_config.py`

**Interfaces:**
- GPU menu row format: `N) cardN PCI total <MiB> used <MiB> free <MiB> render node displays`.
- `benchmark.sh` consumes the smallest enabled model and runs only `ghcr.io/ggml-org/llama.cpp:server-vulkan` before CPU fallback.
- `gpu.benchmark` contains only `vulkan` after initialization or migration.

- [ ] **Step 1: Add failing GPU-row and ROCm-schema tests**

```python
def test_setup_gpu_rows_include_measured_used_and_free_vram(tmp_path):
    result, _, _ = run_setup_with_numbered_selection(tmp_path, "1\n1\n1\n")
    assert "16384 MiB total" in result.stdout
    assert "2048 MiB used" in result.stdout
    assert "14336 MiB free" in result.stdout


def test_vulkan_only_config_removes_legacy_rocm_benchmark():
    cfg = make_valid_config()
    cfg["gpu"]["benchmark"]["rocm"] = {"pp_tps": 1, "tg_tps": 1, "measured_at": "old"}
    migrated = migrate_config(cfg)
    assert set(migrated["gpu"]["benchmark"]) == {"vulkan"}
```

- [ ] **Step 2: Verify the tests fail**

Run: `uv run --with pytest pytest tests/test_shell.py -k setup_gpu_rows -v && uv run --with pytest pytest tests/test_config.py -k vulkan_only -v`

Expected: FAIL because setup hides used/free VRAM and no migration exists.

- [ ] **Step 3: Display free VRAM before selection**

Change the existing `jq` GPU row renderer in `setup.sh` to use the detector’s existing `vram_used_mib` field and calculate `vram_total_mib - vram_used_mib`. Include those three measured values in the selection confirmation. Preserve default selection by largest measured total VRAM.

- [ ] **Step 4: Remove ROCm from schema/template and migrate generated configs**

Delete `benchmark.rocm` from `models.yml.example`. Add `migrate_config(cfg)` in `pylib/config.py`; it removes `cfg["gpu"]["benchmark"]["rocm"]` and returns the config. Call it from config load/save paths before validation. Update valid configuration fixtures to include only Vulkan.

- [ ] **Step 5: Rewrite benchmark as Vulkan-only and transparent**

Delete `ROCM_IMAGE`, `/dev/kfd` checks, ROCm pull/run/record branches, ROCm winner comparisons, and all ROCm messages. Before execution print:

```bash
log_step "Vulkan benchmark"
log_info "model: ${bench_model}"
log_command "podman run --rm --device /dev/dri -v ${MODELS_DIR}:/models:ro,z --entrypoint /app/llama ${VULKAN_IMAGE} bench -m /models/${bench_model} -p 512 -n 128 -r 2 -o json"
```

Capture combined output in a private diagnostic file, then `log_block "Raw benchmark output" "$(cat "$output")"`. Parse throughput only after output is captured. On failure print `Vulkan benchmark exited <status>` plus complete redacted output, then perform the existing CPU fallback. Do not pull or remove a ROCm image.

- [ ] **Step 6: Add benchmark command tests and verify**

Add a Podman stub test that asserts no command/call/text contains `server-rocm`, `/dev/kfd`, or `rocm`, and that Vulkan output appears when the stub returns malformed benchmark JSON. Run:

`uv run --with pytest pytest tests/test_shell.py -k 'benchmark or setup_gpu_rows' -v`

Expected: PASS.

- [ ] **Step 7: Run full verification and commit**

Run: `make validate && make test`

```bash
git add setup.sh benchmark.sh models.yml.example pylib/config.py tests/test_shell.py tests/test_config.py
git commit -m "refactor: use Vulkan as the sole GPU backend"
```

## Task 3: Transparent offline and server inference

**Files:**
- Modify: `check-setup.sh`, `check-server.sh`, `tests/test_shell.py`

**Interfaces:**
- Every enabled-model inference prints command, prompt/payload, raw output, parsed output, expectation, and verdict.
- `check-server` retains `max_tokens: 256`, prompt `Reply with exactly: ready`, and normalized `ready` comparison.

- [ ] **Step 1: Add failing output-contract tests**

```python
def test_check_setup_prints_offline_command_prompt_and_model_output(tmp_path):
    result, _, _ = run_check_setup_with_stubs(tmp_path, inference_output="ready")
    assert "Command: timeout 180 podman run" in result.stdout
    assert "Prompt: Reply with exactly: ready" in result.stdout
    assert "Raw inference output:" in result.stdout
    assert "Parsed result: ready" in result.stdout


def test_check_server_prints_redacted_request_response_and_expectation(tmp_path):
    result, _ = run_check_server(tmp_path, {"gemma4": "ready", "ornith": "ready"})
    assert "Request payload:" in result.stdout
    assert '"max_tokens": 256' in result.stdout
    assert "Raw HTTP response:" in result.stdout
    assert "Expected normalized content: ready" in result.stdout
    assert "fixture-secret" not in result.stdout + result.stderr
```

- [ ] **Step 2: Verify tests fail**

Run: `uv run --with pytest pytest tests/test_shell.py -k 'prints_offline or prints_redacted_request' -v`

Expected: FAIL because current checks print only summaries.

- [ ] **Step 3: Add full records to check-setup**

Print the configured PCI/render node, image, model GGUF validator JSON, budget JSON, Vulkan list-devices command/output, and resolution JSON. For each inference print the exact command and prompt before execution. Capture output and exit status, print complete redacted output, then print parsed nonempty result and verdict. Replace the current 1000-character failure truncation.

- [ ] **Step 4: Add full records to check-server**

For the invalid-key probe print a redacted curl command, payload, and HTTP status. For each model print the redacted curl command, formatted request body, raw HTTP JSON, assistant content, reasoning content when present, normalized value, expected `ready`, and verdict. Preserve `-K` auth-file use and do not display its contents.

- [ ] **Step 5: Run focused and full verification**

Run: `uv run --with pytest pytest tests/test_shell.py -k 'check_setup or check_server' -v`

Run: `make validate && make test`

- [ ] **Step 6: Commit**

```bash
git add check-setup.sh check-server.sh tests/test_shell.py
git commit -m "feat: show complete redacted inference diagnostics"
```

## Task 4: Correct and expose live agent checks

**Files:**
- Modify: `check-with-agents.sh`, `tests/test_shell.py`

**Interfaces:**
- `snapshot_for <weather|fx>` fetches authoritative JSON immediately before a single client/model/check row.
- `parse_evidence <assistant-text>` returns exactly one object from either bare JSON or one `json` fence.
- Each row prints command, prompt, raw JSONL transcript, final assistant text, parsed evidence, snapshot, field comparison, and verdict.

- [ ] **Step 1: Add failing live-defect tests**

```python
def test_agent_check_accepts_one_json_fence_and_rejects_other_text(tmp_path):
    result, _, _ = run_agent_check(tmp_path, clients={"pi": fenced_json_pi_stub})
    assert result.returncode == 0
    result, _, _ = run_agent_check(tmp_path, clients={"pi": prose_plus_json_pi_stub})
    assert result.returncode != 0
    assert "agent evidence parsing" in result.stderr


def test_agent_check_fetches_snapshot_per_matrix_row_without_leaking_it_to_prompt(tmp_path):
    result, calls, _ = run_agent_check(tmp_path, clients={"pi": valid_pi_stub})
    assert calls.read_text().count("api.open-meteo.com") == 2
    assert "Public snapshot for comparison" not in calls.read_text()
```

Use one model and two checks in the second test. Make source stubs return a unique timestamp per request, proving each row uses a fresh value.

- [ ] **Step 2: Verify tests fail**

Run: `uv run --with pytest pytest tests/test_shell.py -k 'json_fence or snapshot_per_matrix' -v`

Expected: FAIL because the current parser requires bare JSON and snapshots are global/prompt-visible.

- [ ] **Step 3: Fetch per-row snapshots and remove source leakage**

Move `source_weather`/`source_fx` calls into the matrix loop immediately before `run_agent`. Construct prompts with only the source URL, required field names, and command requirement. Do not include a snapshot or its values in the prompt.

- [ ] **Step 4: Parse one bare object or one JSON fence**

Implement parsing with a strict two-stage jq/sed pipeline:

```bash
parse_evidence() {
    local text="$1" body
    body="$(printf '%s' "$text" | sed -n '/^```json[[:space:]]*$/,/^```[[:space:]]*$/p')"
    if [ -n "$body" ]; then
        [ "$(printf '%s\n' "$body" | grep -c '^```')" -eq 2 ] || return 1
        text="$(printf '%s\n' "$body" | sed '1d;$d')"
    fi
    printf '%s' "$text" | jq -sce 'select(length == 1 and (.[0] | type == "object")) | .[0]'
}
```

Before this function, reject a nonempty prefix/suffix outside the sole fenced block. Print the assistant final text and parsed object after redaction.

- [ ] **Step 5: Print complete row diagnostics**

Print a section header for each row, a redacted client invocation, exact prompt, raw JSONL transcript, final assistant text, parsed evidence, expected fresh snapshot, and either `PASS` or one field-by-field failure line:

```text
FAIL client=pi model=ornith check=weather field=source_timestamp expected=... received=...
```

On a client command failure, print exit status and full redacted stderr/parser diagnostics, not `agent invocation failed` alone.

- [ ] **Step 6: Add retention/redaction tests**

Add a test with `LLM_ENV_KEEP_CHECK_ARTIFACTS=1` that asserts retained transcript files are mode 700 parent/mode 600 files and do not contain the fixture API key or bearer header. Add a test with a nonzero agent stub that asserts its redacted stderr appears in console output.

- [ ] **Step 7: Verify and commit**

Run: `make validate && make test`

```bash
git add check-with-agents.sh tests/test_shell.py
git commit -m "fix: expose and validate live agent evidence"
```

## Task 5: Documentation and live acceptance

**Files:**
- Modify: `README.md`, `QUICK_START.md`, `docs/client-compatibility.md`

- [ ] **Step 1: Update documentation assertions first**

Add to `tests/test_docs.py`:

```python
def test_docs_describe_transparent_vulkan_checks():
    readme = (ROOT / "README.md").read_text().lower()
    quick = (ROOT / "QUICK_START.md").read_text().lower()
    assert "vulkan" in readme
    assert "rocm" not in readme
    assert "llm_env_keep_check_artifacts=1" in quick
    assert "check-with-agents" in quick
```

- [ ] **Step 2: Verify the documentation test fails**

Run: `uv run --with pytest pytest tests/test_docs.py -v`

Expected: FAIL because current documents describe ROCm and lack diagnostic artifacts.

- [ ] **Step 3: Document Vulkan-only and transparent checks**

Rewrite benchmark language as Vulkan-only with CPU fallback. State that the project does not remove existing host images. Document setup’s total/used/free GPU rows; check output sections; redacted diagnostics; and `LLM_ENV_KEEP_CHECK_ARTIFACTS=1`.

Update the client compatibility evidence with real Pi/OpenCode live results only after Task 4 passes. State that each agent row shows its command, prompt, transcript, parsed evidence, and source comparison.

- [ ] **Step 4: Run full acceptance**

Run the lifecycle for Gemma4 only and Ornith only:

```bash
make setup
make check-setup
make benchmark
make start
make check-server
LLM_ENV_KEEP_CHECK_ARTIFACTS=1 make check-with-agents
make stop
```

Verify each command displays the required records, no output/artifact contains the API key, Vulkan remains selected, and existing `server-rocm` remains present locally without a project command touching it.

- [ ] **Step 5: Final verification and commit**

Run: `make validate && make test && git diff --check`

```bash
git add README.md QUICK_START.md docs/client-compatibility.md tests/test_docs.py
git commit -m "docs: explain transparent Vulkan inference checks"
```

## Self-Review

- **Spec coverage:** Task 1 supplies safe rendering. Task 2 covers measured setup VRAM and removes ROCm without host deletion. Task 3 adds offline/server transparency. Task 4 fixes actual live-agent failures and diagnostics. Task 5 documents behavior and runs two-model acceptance.
- **Placeholder scan:** Every code-changing task has explicit files, failing tests, commands, expected result, implementation behavior, and commit command.
- **Interface consistency:** Task 1 produces helpers used by Tasks 2–4. Task 4 consumes per-row snapshots and passes parsed evidence to the existing comparison function. Task 5 validates the public behavior created by all prior tasks.
