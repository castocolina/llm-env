# Config-Aware E2E Check Scripts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `check-setup.sh`, `check-server.sh`, and `benchmark.sh` consume each enabled model's configured offload flags, context/probe size, output-token budget, timeout, and GPU device, and store throughput on the model that produced it.

**Architecture:** Add two validated optional per-model check fields, retire the shared `gpu.benchmark` record, and render a temporary `presets.ini` through the same `llmenv presets` path used by production startup. Offline inference and benchmarking read `n-gpu-layers`, optional `n-cpu-moe`, and production `ctx-size` from that render; explicit `check_ctx_size` overrides only offline probe sizing. HTTP checks read per-model output budgets and timeouts directly from `models.yml`, while the already-running server supplies its production context window.

**Tech Stack:** Bash (`yq`, `jq`, `awk`, `base64`, `podman`), Python 3.11+ (`uv run`), pytest. No new package dependency is introduced.

## Global Constraints

- **No hardcoded values in `.sh`/`.py` scripts for anything that varies per model.** `n_cpu_moe`, `n_gpu_layers`, `ctx_size`/`check_ctx_size`, `client_max_output_tokens`, and benchmark prompt/generation sizes come from `models.yml`, directly or through rendered presets.
- **Checks validate configured production values by default.** `check_ctx_size` is optional and falls back to the model's real `ctx_size`; no script silently scales a model down.
- **`check_ctx_size` scope reconciliation:** it applies to `check-setup.sh`'s offline `llama cli` invocation and `benchmark.sh`'s prompt-processing size. It does not alter `check-server.sh` requests because OpenAI chat completions has no per-request context-window parameter; that script exercises the server whose startup preset already fixes the model's production `ctx-size`. This narrows Component 4's ambiguous wording explicitly during planning.
- **Scripts are model-count agnostic.** Iterate every enabled model and keep each model's fields together; do not assume one model or mix values between iterations.
- A missing configured GPU, failed/empty `resolve-device` result, failed preset render, or missing required preset key is a failed prerequisite. The script must print an explicit failure, skip only commands that cannot run, continue producing diagnostics where possible, and exit nonzero. It must never report success after benchmarking/checking zero models.
- Model aliases are untrusted data. Pass aliases into `yq` through `strenv()`/`--arg`, never interpolate them into an expression, pathname, or `mktemp` template. Encode complete model records when crossing a shell loop boundary so tabs/newlines cannot mix fields.
- Lazy-load plus idle-unload remains unchanged. `runtime.parallel_slots` remains `1`; eager load and `make prune` are out of scope.
- `check-with-agents.sh` is out of scope; its Pi failure is an unrelated broken local install.
- The script-level `check_timeout_seconds` fallback is permitted by the design and is not a per-model production value. Task 7 measures both the full-context offline path and a real cold-load HTTP path, then sets separate fallbacks to `max(2 × measured ceiling, measured ceiling + 120 seconds)`.
- `make benchmark` persists `migrate_config_file()` before reading or writing raw YAML, so standalone benchmarking removes the retired `gpu.benchmark` key before per-model results are recorded.
- Repository gates are mandatory before every commit: after a `.sh` edit run `make validate`; after a `.py` edit run `make validate && make test`. Because Tasks 1-6 edit Python source or Python tests, each includes `make validate && make test` explicitly.

---

## File Map

- Modify: `pylib/config.py` — validate optional check fields and drop legacy `gpu.benchmark` during migration.
- Modify: `tools/lib.sh` — render production presets and read an exact per-alias preset key.
- Modify: `scripts/check-server.sh` — use each model's output budget and timeout in both completion paths.
- Modify: `scripts/check-setup.sh` — run every offline inference with presets-derived offload flags, configured context/output size, timeout, and explicit prerequisite failures.
- Modify: `scripts/benchmark.sh` — measure every enabled model with its configured device, offload flags, and probe sizes; persist results safely per alias.
- Modify: `models.yml.example` — remove shared benchmark storage and document optional check fields.
- Modify: `tests/test_config.py`, `tests/test_shell.py` — schema, migration, multi-model, failure-path, and shell-safety coverage.
- Modify: `.agents/architecture.md`, `README.md` — document the final configuration flow and measured timeout.

---

### Task 1: Validate optional per-model check fields

**Files:**
- Modify: `pylib/config.py:382-389`
- Test: `tests/test_config.py:705-727`

**Interfaces:**
- Produces: `validate_config()` accepts absent `check_ctx_size`/`check_timeout_seconds` and rejects either field when present unless it is a positive, non-Boolean integer.

- [ ] **Step 1: Add focused schema tests**

Insert after `test_validate_config_rejects_invalid_n_cpu_moe` in `tests/test_config.py`:

```python
def test_validate_config_accepts_check_ctx_size_as_positive_int():
    cfg = make_cfg()
    cfg["models"][0]["check_ctx_size"] = 8192
    assert validate_config(cfg) == []


@pytest.mark.parametrize("value", [0, -1, True, "8192", 1.5])
def test_validate_config_rejects_invalid_check_ctx_size(value):
    cfg = make_cfg()
    cfg["models"][0]["check_ctx_size"] = value
    assert any("check_ctx_size" in error for error in validate_config(cfg))


def test_validate_config_accepts_check_timeout_seconds_as_positive_int():
    cfg = make_cfg()
    cfg["models"][0]["check_timeout_seconds"] = 600
    assert validate_config(cfg) == []


@pytest.mark.parametrize("value", [0, -1, True, "600", 1.5])
def test_validate_config_rejects_invalid_check_timeout_seconds(value):
    cfg = make_cfg()
    cfg["models"][0]["check_timeout_seconds"] = value
    assert any("check_timeout_seconds" in error for error in validate_config(cfg))


def test_validate_config_omits_check_fields_without_error():
    cfg = make_cfg()
    cfg["models"][0].pop("check_ctx_size", None)
    cfg["models"][0].pop("check_timeout_seconds", None)
    assert validate_config(cfg) == []
```

- [ ] **Step 2: Run the red tests and record the accurate baseline**

Run: `uv run pytest tests/test_config.py -k "check_ctx_size or check_timeout_seconds" -v`

Expected: 13 cases collected. The two acceptance tests and omission test already PASS because unknown optional fields are currently ignored; all 10 invalid-value cases FAIL because validation is absent.

- [ ] **Step 3: Implement validation**

Add immediately after the `n_cpu_moe` block in `validate_config()`:

```python
        for key in ("check_ctx_size", "check_timeout_seconds"):
            if key in model and not _positive_int(model[key]):
                errors.append(f"model {model_name} {key} must be a positive integer")
```

- [ ] **Step 4: Run focused tests and the mandatory Python gate**

Run:

```bash
uv run pytest tests/test_config.py -k "check_ctx_size or check_timeout_seconds" -v
make validate && make test
```

Expected: all 13 focused cases pass, then the repository gate passes.

- [ ] **Step 5: Commit**

```bash
git add pylib/config.py tests/test_config.py
git commit -m "feat(config): validate optional check fields"
```

---

### Task 2: Retire shared benchmark storage and its old-schema tests

**Files:**
- Modify: `pylib/config.py:92-101`
- Modify: `models.yml.example:11-27,54-93`
- Modify/Test: `tests/test_config.py:241-275,363-375`

**Interfaces:**
- Produces: `migrate_config()` removes `gpu.benchmark` completely, including a Vulkan-only value. Runtime results are written only to `.models[].benchmark.vulkan` by Task 6.

- [ ] **Step 1: Rewrite all three existing migration tests that encode the retired schema**

Replace the tests at current lines 241-275 and 363-375 with:

```python
def test_vulkan_only_config_removes_legacy_shared_benchmark():
    """The shared slot cannot identify which model produced a measurement."""
    cfg = make_cfg()
    migrated = config_module.migrate_config(cfg)
    assert "benchmark" not in migrated["gpu"]


def test_load_config_drops_legacy_shared_benchmark_from_yaml(tmp_path):
    path = tmp_path / "models.yml"
    path.write_text(
        "gpu:\n"
        "  benchmark:\n"
        "    rocm: {pp_tps: 1}\n"
        "    vulkan: {pp_tps: 2, tg_tps: 2, measured_at: current}\n"
    )
    assert "benchmark" not in load_config(path)["gpu"]


def test_config_save_and_load_drop_legacy_shared_benchmark(tmp_path):
    cfg = make_cfg()
    cfg["gpu"]["benchmark"]["rocm"] = {"pp_tps": 1}
    path = tmp_path / "models.yml"
    save_config(cfg, path)
    assert "benchmark" not in load_config(path)["gpu"]
```

These replace, rather than supplement, `test_vulkan_only_config_removes_legacy_rocm_benchmark`, `test_load_config_migrates_legacy_rocm_benchmark_from_yaml`, and `test_config_save_and_load_migrate_legacy_rocm_benchmarks`: those three deliberately assert that `gpu.benchmark.vulkan` survives, which is the old schema this plan retires.

- [ ] **Step 2: Run the migration tests red**

Run: `uv run pytest tests/test_config.py -k "legacy_shared_benchmark" -v`

Expected: the direct migration and YAML-load tests FAIL because current migration removes only `.rocm`; the save/load case also FAILs for the same reason.

- [ ] **Step 3: Remove the entire legacy key**

Replace the final benchmark migration block in `migrate_config()` with:

```python
    gpu.pop("benchmark", None)
    return cfg
```

- [ ] **Step 4: Update the example schema**

Delete `models.yml.example:23-27`. Add immediately above the first entry under `models:`:

```yaml
  # Optional per-model check overrides:
  # check_ctx_size defaults to this model's ctx_size.
  # check_timeout_seconds defaults to the empirically tuned script baseline.
  # `make benchmark` creates benchmark.vulkan on each measured model entry.
```

Do not add an empty per-model `benchmark` mapping; generated results remain absent until measured.

- [ ] **Step 5: Run migration coverage and the mandatory Python gate**

Run:

```bash
uv run pytest tests/test_config.py -k "benchmark or migrate" -v
uv run pytest tests/test_config.py -v
make validate && make test
```

Expected: the rewritten migration tests, the complete config suite, and the full repository gate pass.

- [ ] **Step 6: Commit**

```bash
git add pylib/config.py tests/test_config.py models.yml.example
git commit -m "feat(config): move benchmark results to model entries"
```

---

### Task 3: Share production preset rendering and lookup

**Files:**
- Modify: `tools/lib.sh:229-237`
- Test: `tests/test_shell.py:82-103`

**Interfaces:**
- Produces: `render_presets_file(device, output_path) -> command status` and `presets_value(presets_file, alias, key) -> value-or-empty`.
- Consumes: `llmenv presets --models-dir --device --output`, whose generated model sections contain `ctx-size`, `n-gpu-layers`, and optional `n-cpu-moe`.

- [ ] **Step 1: Add a round-trip helper test**

Add after `test_makefile_dispatches_relocated_entrypoints` in `tests/test_shell.py`:

```python
def test_presets_helpers_read_a_generated_ini(tmp_path: pathlib.Path) -> None:
    config = tmp_path / "models.yml"
    config.write_text(
        "version: 1\nserver: {host: 0.0.0.0, port: 8000, api_key: k}\n"
        "gpu: {backend: vulkan, pci_address: '0000:03:00.0', vram_total_mib: 16384, reserve_mode: auto}\n"
        "runtime: {models_max: 1, parallel_slots: 1, ubatch_size: 512, flash_attn: true, cache_type_k: q5_1, cache_type_v: q5_1}\n"
        "models:\n"
        "- {alias: dense, label: Dense, parameters: 1B, quantization: Q4_K_M, enabled: true, file: dense.gguf, url: 'https://example.invalid/d', size_bytes: 1, vram_budget: 50%, ctx_size: 4096, client_max_output_tokens: 2048, n_gpu_layers: 99}\n"
        "- {alias: moe, label: MoE, parameters: 8B, quantization: Q4_K_M, enabled: true, file: moe.gguf, url: 'https://example.invalid/m', size_bytes: 1, vram_budget: 50%, ctx_size: 8192, client_max_output_tokens: 2048, n_gpu_layers: 40, n_cpu_moe: 12}\n"
    )
    output = tmp_path / "presets.ini"
    result = subprocess.run(
        [
            "/usr/bin/bash", "-c",
            'source tools/lib.sh; render_presets_file "$1" "$2"; '
            'presets_value "$2" dense n-gpu-layers; '
            'presets_value "$2" moe n-gpu-layers; '
            'presets_value "$2" moe n-cpu-moe; '
            '(presets_value "$2" dense n-cpu-moe; echo "<empty>")',
            "bash", "Vulkan0", str(output),
        ],
        cwd=ROOT,
        env=os.environ | {
            "LLM_ENV_CONFIG": str(config),
            "LLM_ENV_MODELS_DIR": str(tmp_path / "models"),
        },
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["99", "40", "12", "<empty>"]
```

This fixture is intentionally complete for `llmenv presets`: `pylib/config.py:16-25,153-402` requires `version`, all four required sections, `gpu.backend`, `gpu.vram_total_mib`, `gpu.reserve_mode`, positive `runtime.models_max`/`parallel_slots`/`ubatch_size` with `parallel_slots: 1`, every key in `REQUIRED_MODEL_KEYS`, at least one enabled model, and `models_max` no greater than the enabled count. The mapping above supplies every one of those fields; optional resource and OmniRoute sections are not required.

- [ ] **Step 2: Run the helper test red**

Run: `uv run pytest tests/test_shell.py -k presets_helpers_read_a_generated_ini -v`

Expected: FAIL with `render_presets_file: command not found`.

- [ ] **Step 3: Implement the helpers**

Insert between `migrate_config_file()` and `new_api_key()` in `tools/lib.sh`:

```bash
render_presets_file() {
    local device="$1" output="$2"
    llmenv --config "$CONFIG_PATH" presets \
        --models-dir "$MODELS_DIR" --device "$device" --output "$output" >/dev/null
}

presets_value() {
    local presets_file="$1" alias="$2" key="$3"
    awk -v section="[${alias}]" -v prefix="${key} = " '
        $0 == section { in_section = 1; next }
        /^\[/ { in_section = 0 }
        in_section && index($0, prefix) == 1 {
            print substr($0, length(prefix) + 1)
            exit
        }
    ' "$presets_file"
}
```

- [ ] **Step 4: Run focused coverage and the mandatory shell/Python-test gate**

Run:

```bash
uv run pytest tests/test_shell.py -k presets_helpers_read_a_generated_ini -v
make validate && make test
```

Expected: PASS. `make validate && make test` satisfies both the `.sh` edit and the `.py` test edit.

- [ ] **Step 5: Commit**

```bash
git add tools/lib.sh tests/test_shell.py
git commit -m "feat(lib): expose production preset values to checks"
```

---

### Task 4: Make both HTTP completion loops model-aware

**Files:**
- Modify: `scripts/check-server.sh:163-221,264-329`
- Modify/Test: `tests/test_shell.py:4930-5305`

**Interfaces:**
- Consumes per enabled model: `.alias`, `.client_max_output_tokens`, and `.check_timeout_seconds // 120`.
- Does not consume `check_ctx_size`: the HTTP endpoint has no request-level context-size lever, and server startup already applied the production preset.
- Produces: one validated, materialized file of base64-encoded enabled-model JSON records, reused by both completion loops. Both loops build their body and timeout from the same decoded record; the direct loop increments `checked_models` once per attempted model.

- [ ] **Step 1: Extend the existing `run_check_server()` fixture instead of adding a parallel fixture**

Keep `run_check_server()` and every test at `tests/test_shell.py:4930-5305`. Add these keyword controls to its declaration:

```python
    model_yq_exit: int = 0,
    model_jq_exit: int = 0,
    models_enabled: bool = True,
```

Resolve the real JSON/YAML tools at the start of the fixture and fail the test immediately if either is unavailable:

```python
    real_jq = shutil.which("jq")
    real_yq = shutil.which("yq")
    assert real_jq is not None
    assert real_yq is not None
    enabled = "true" if models_enabled else "false"
```

Extend the existing config records in place; do not rename `gemma4` or `ornith`, because all of the existing response/error-path controls key on those aliases:

```python
        "  - alias: gemma4\n"
        f"    enabled: {enabled}\n"
        "    client_max_output_tokens: 2048\n"
        "  - alias: ornith\n"
        f"    enabled: {enabled}\n"
        "    client_max_output_tokens: 8192\n"
        "    check_timeout_seconds: 600\n"
```

Replace the hand-written request-construction branch in the fixture's `jq` stub (which currently emits `max_tokens: 256`) with a transparent real-tool wrapper. Replace the fixture's `yq` stub with a real-tool wrapper that can fail only the new enabled-model materialization query:

```python
    jq.write_text(
        "#!/usr/bin/bash\n"
        "for argument in \"$@\"; do\n"
        "  if [ \"$argument\" = '.[] | @base64' ]; then\n"
        "    [ \"$MODEL_JQ_EXIT\" -eq 0 ] || exit \"$MODEL_JQ_EXIT\"\n"
        "  fi\n"
        "done\n"
        "exec \"$REAL_JQ\" \"$@\"\n"
    )

    yq.write_text(
        "#!/usr/bin/bash\n"
        "for argument in \"$@\"; do\n"
        "  if [ \"$argument\" = '[.models[] | select(.enabled)]' ]; then\n"
        "    [ \"$MODEL_YQ_EXIT\" -eq 0 ] || exit \"$MODEL_YQ_EXIT\"\n"
        "  fi\n"
        "done\n"
        "exec \"$REAL_YQ\" \"$@\"\n"
    )
```

Retain the existing `curl` response selection and all curl-failure/status/body controls. Add `time_limit=""` beside `url`, `body_file`, and `data`, and add this branch to the existing `case "${previous:-}"` block:

```bash
    --max-time) time_limit="$argument" ;;
```

Then replace the initial `printf '%s\n' "$*"` with this one-line JSON record after the argument loop. This preserves `calls.read_text().count("/v1/chat/completions")` while making multiline `jq -n` payloads parseable:

```bash
jq -cn --arg argv "$*" --arg url "$url" --arg timeout "$time_limit" \
    --arg payload "$data" \
    '{argv: $argv, url: $url, timeout: $timeout, payload: $payload}' >> "$CALLS"
```

Add these environment entries:

```python
        "MODEL_JQ_EXIT": str(model_jq_exit),
        "MODEL_YQ_EXIT": str(model_yq_exit),
        "REAL_JQ": real_jq,
        "REAL_YQ": real_yq,
```

Set the existing environment entry exactly as follows so the zero-model test reaches the completion prerequisite without failing earlier on a deliberate listing mismatch:

```python
        "MODEL_LIST_BODY": '{"data":[]}' if not models_enabled else model_list_body,
```

- [ ] **Step 2: Adapt every existing assertion and add isolation/zero-work tests**

Keep all existing tests for normalized mismatch, successful normalization, verbose diagnostics, concise output, continuing after a model failure, curl failure, non-2xx, invalid JSON, missing content, scalar responses, and malformed model listing. Make only these old-value assertion changes:

```python
# test_check_server_accepts_normalized_ready_for_every_enabled_model
assert result.returncode == 0, result.stderr
assert "max_tokens: 256, stream: false" not in (
    ROOT / "scripts/check-server.sh"
).read_text()

# test_check_server_prints_a_copy_pasteable_request_response_and_curl_template
assert '"max_tokens": 2048' in result.stdout
assert '"max_tokens": 8192' in result.stdout

# test_check_server_reports_invalid_completion_json and
# test_check_server_reports_malformed_model_listing
assert "Response parsing stderr:\n  parse error:" in result.stdout
```

For those last two tests, replace the old lowercase full phrase `parse error: invalid literal` rather than adding a second assertion: the real `jq` wrapper includes line/column detail and may capitalize `Invalid`, while `parse error:` is the stable diagnostic contract. All other assertions in the block remain unchanged.

Add the model-isolation and producer/zero-count coverage onto the same fixture:

```python
def test_check_server_keeps_each_models_budget_and_timeout_together(tmp_path):
    result, calls = run_check_server(
        tmp_path, {"gemma4": "ready", "ornith": "ready"}
    )
    assert result.returncode == 0, result.stderr
    parsed = [json.loads(line) for line in calls.read_text().splitlines()]
    completion_rows = [
        row for row in parsed
        if "/v1/chat/completions" in row["url"]
        and json.loads(row["payload"])["model"] != "x"
    ]
    assert len(completion_rows) == 4
    observed = {
        (json.loads(row["payload"])["model"], json.loads(row["payload"])["max_tokens"], row["timeout"])
        for row in completion_rows
    }
    assert observed == {
        ("gemma4", 2048, "120"),
        ("llama-cpp/gemma4", 2048, "120"),
        ("ornith", 8192, "600"),
        ("llama-cpp/ornith", 8192, "600"),
    }


def test_check_server_keeps_the_invalid_key_probe_at_ten_seconds(tmp_path):
    result, calls = run_check_server(
        tmp_path, {"gemma4": "ready", "ornith": "ready"}
    )
    assert result.returncode == 0, result.stderr
    parsed = [json.loads(line) for line in calls.read_text().splitlines()]
    invalid = [
        row for row in parsed
        if row["payload"] and json.loads(row["payload"]).get("model") == "x"
    ]
    assert len(invalid) == 1
    assert invalid[0]["timeout"] == "10"


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"model_yq_exit": 41}, "could not enumerate enabled models"),
        ({"model_jq_exit": 42}, "could not enumerate enabled models"),
        ({"models_enabled": False}, "no enabled models were checked"),
    ],
)
def test_check_server_fails_when_no_direct_model_check_can_run(
    tmp_path, kwargs, message
):
    result, calls = run_check_server(
        tmp_path,
        {"gemma4": "ready", "ornith": "ready"},
        **kwargs,
    )
    assert result.returncode != 0
    assert message in result.stderr
    parsed = [json.loads(line) for line in calls.read_text().splitlines()]
    non_auth_completions = [
        row for row in parsed
        if "/v1/chat/completions" in row["url"]
        and json.loads(row["payload"]).get("model") != "x"
    ]
    assert non_auth_completions == []
```

- [ ] **Step 3: Run the adapted server block red**

Run: `uv run pytest tests/test_shell.py -k check_server -v`

Expected: the existing error-path tests still run coherently; the success/isolation assertions FAIL on hardcoded `256`/`120`, and the producer/zero-count cases FAIL because the script can currently fall through without a direct model check.

- [ ] **Step 4: Materialize and validate enabled models before either loop**

Add `base64` to `require_cmd curl jq yq`. After `ok()`/`bad()` and `LLM_SERVER_COMPLETION_OK` are declared (so `bad` is callable and `diagnostic_dir` already exists), materialize the producer pipeline into an alias-independent diagnostic file and capture its status under the script's existing `set -uo pipefail`/`set +e` mode:

```bash
enabled_models_file="$(mktemp "${diagnostic_dir}/enabled-models.XXXXXX")" \
    || die "could not create enabled-model diagnostic"
model_stream_status=0
(
    yq -o=json -I=0 '[.models[] | select(.enabled)]' "$CONFIG_PATH" |
        jq -r '.[] | @base64'
) >"$enabled_models_file" || model_stream_status=$?

models_ready=1
if [ "$model_stream_status" -ne 0 ]; then
    bad "Verdict: FAIL stage=model enumeration reason=could not enumerate enabled models (exit ${model_stream_status})"
    models_ready=0
fi
checked_models=0
```

Guard the direct loop with an `if`. Decode records from the validated file, increment `checked_models` immediately before the direct `request_record`, and read the per-model fields together:

```bash
if [ "$models_ready" -eq 1 ]; then
while IFS= read -r model_b64; do
    [ -n "$model_b64" ] || continue
    model_json="$(printf '%s' "$model_b64" | base64 --decode)"
    alias="$(jq -r '.alias' <<<"$model_json")"
    max_tokens="$(jq -r '.client_max_output_tokens' <<<"$model_json")"
    timeout_seconds="$(jq -r '.check_timeout_seconds // 120' <<<"$model_json")"
    checked_models=$((checked_models + 1))
```

Keep the current direct request/response handling immediately after that exact prefix, then replace the loop's process-substitution terminator with:

```bash
done < "$enabled_models_file"
fi

if [ "$models_ready" -eq 1 ] && [ "$checked_models" -eq 0 ]; then
    bad "Verdict: FAIL stage=model checks reason=no enabled models were checked"
fi
```

Do not use process substitution for this producer: its exit code would not control the loop. Replace the OmniRoute loop's alias-only header and process-substitution terminator with this exact guarded record decoder around its current request/response body:

```bash
if [ "$models_ready" -eq 1 ] && [ "$checked_models" -gt 0 ]; then
while IFS= read -r model_b64; do
    [ -n "$model_b64" ] || continue
    model_json="$(printf '%s' "$model_b64" | base64 --decode)"
    alias="$(jq -r '.alias' <<<"$model_json")"
    max_tokens="$(jq -r '.client_max_output_tokens' <<<"$model_json")"
    timeout_seconds="$(jq -r '.check_timeout_seconds // 120' <<<"$model_json")"
```

Keep the current `LLM_SERVER_COMPLETION_OK` skip and OmniRoute request/response handling after that prefix, then close the guarded loop with:

```bash
done < "$enabled_models_file"
fi
```

Extend `cleanup()` with `rm -f "$auth_conf" "$bad_conf" "$omniroute_cookie_jar" "${enabled_models_file:-}"` so a pre-assignment failure remains safe under `set -u`.

- [ ] **Step 5: Build request bodies and timeouts from decoded values**

In the direct loop use:

```bash
body="$(jq -n --arg m "$alias" --argjson mt "$max_tokens" \
    '{model: $m, messages: [{role: "user", content: "Reply with exactly: ready"}], max_tokens: $mt, stream: false}')"
```

In the OmniRoute loop use `--arg m "llama-cpp/${alias}"` with the same `--argjson mt` expression. Replace both completion `--max-time 120` values, including their diagnostic strings, with `"$timeout_seconds"`. Leave health, invalid-key, model-listing, login, and provider probes at 10 seconds.

- [ ] **Step 6: Run focused coverage and the mandatory shell/Python-test gate**

Run:

```bash
uv run pytest tests/test_shell.py -k check_server -v
make validate && make test
```

Expected: all pre-existing curl-failure/non-2xx/invalid-JSON/missing-content/scalar/malformed-listing paths remain covered and pass; both aliases pass through both completion paths with their own values; the invalid-key probe stays at 10 seconds; a failed producer and zero enabled models both exit nonzero; the full gate passes.

- [ ] **Step 7: Commit**

```bash
git add scripts/check-server.sh tests/test_shell.py
git commit -m "fix(check-server): use per-model completion budgets and timeouts"
```

---

### Task 5: Run offline inference with exact model configuration

**Files:**
- Modify: `scripts/check-setup.sh:83-108,173-201`
- Modify/Test: `tests/test_shell.py:2702-3036`

**Interfaces:**
- Consumes: Task 3's `render_presets_file(device, output)` and `presets_value(file, alias, key)`.
- Consumes per model: file, optional `check_ctx_size`, `client_max_output_tokens`, and `check_timeout_seconds // 180`; when the override is absent, context comes from the rendered preset's `ctx-size`.
- Produces: `inference_command(file, device, n_gpu_layers, n_cpu_moe, ctx_size, max_output_tokens, timeout_seconds)` and `record_inferences(device, skip_reason="", presets_file="")`.

- [ ] **Step 1: Extend the existing fixture with two distinct configured models**

Add `presets_exit: int = 0` and `presets_missing_alias: bool = False` keyword parameters to `run_check_setup_with_stubs`. Change its timeout stub so both fixture timeouts execute:

```bash
case "$1" in
    180|300) ;;
    *) exit 64 ;;
esac
shift
exec "$@"
```

Make the `uv` stub's `presets` case write:

```ini
[first]
ctx-size = 8192
n-gpu-layers = 42

[second]
ctx-size = 4096
n-gpu-layers = 17
n-cpu-moe = 12
```

When `PRESETS_MISSING_ALIAS=1`, omit `[second]`; always exit `$PRESETS_EXIT`. Update the enabled model configs to include:

```yaml
  - alias: first
    enabled: true
    file: first.gguf
    ctx_size: 8192
    client_max_output_tokens: 2048
    n_gpu_layers: 42
  - alias: second
    enabled: true
    file: second.gguf
    ctx_size: 4096
    check_ctx_size: 2048
    client_max_output_tokens: 1024
    check_timeout_seconds: 300
    n_gpu_layers: 17
    n_cpu_moe: 12
```

- [ ] **Step 2: Replace the happy-path assertions and add prerequisite failures**

The happy path must scope its timeout assertion to each model's own command line, not to a global substring count — a global `recorded.count("timeout 300 podman run") == 1` would break the moment the empirically-tuned baseline (Task 7) happens to equal the second model's explicit `300`-second override, since both models' commands would then legitimately contain the string `"timeout 300 podman run"` and the count would be 2, not 1, for a correct run:

```python
first_call = next(
    (line for line in recorded.splitlines() if "podman run" in line and "/models/first.gguf" in line and " cli " in line),
    None,
)
second_call = next(
    (line for line in recorded.splitlines() if "podman run" in line and "/models/second.gguf" in line and " cli " in line),
    None,
)
assert first_call is not None and first_call.startswith("timeout 180 podman run")
assert second_call is not None and second_call.startswith("timeout 300 podman run")
assert "--n-gpu-layers 42 --ctx-size 8192" in first_call
assert "--n-gpu-layers 17 --n-cpu-moe 12 --ctx-size 2048" in second_call
assert first_call.endswith("-p Reply with exactly: ready -n 2048")
assert second_call.endswith("-p Reply with exactly: ready -n 1024")
```

The `-n` assertions retire the hardcoded offline `-n 256`: each model now uses `client_max_output_tokens`.

Add explicit tests:

```python
@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"resolve_exit": 23}, "GPU device could not be resolved"),
        ({"presets_exit": 29}, "presets rendering failed"),
        ({"presets_missing_alias": True}, "missing n-gpu-layers preset for second"),
    ],
)
def test_check_setup_fails_when_inference_prerequisite_is_unavailable(tmp_path, kwargs, message):
    result, calls, _ = run_check_setup_with_stubs(tmp_path, **kwargs)
    assert result.returncode != 0
    assert message in result.stderr
    assert "Results:" in result.stdout
    if "presets" in message:
        assert "/models/second.gguf" not in calls.read_text() or " cli " not in calls.read_text()
```

Keep the existing budget failure test. Add an `empty_resolve` fixture option that returns status 0 with `{"device":""}`, and add it as a fourth parameter expecting `GPU device resolution returned no device`. This distinguishes command failure from a successful but unusable result.

Add a producer-failure test proving the enumeration fix actually matters — a broken `yq` must not let the script report success after checking zero models:

```python
def test_check_setup_fails_when_model_enumeration_fails(tmp_path):
    result, calls, _ = run_check_setup_with_stubs(tmp_path, yq_models_json_exit=17)
    assert result.returncode != 0
    assert "failed to enumerate enabled models" in result.stderr
    assert " cli " not in calls.read_text()
```

Add a `yq_models_json_exit: int = 0` keyword parameter to `run_check_setup_with_stubs`; make the fixture's `yq` stub exit with that status specifically for the `-o=json -I=0 '[.models[] | select(.enabled)]'` invocation (matched by its exact arguments, not by a blanket `case "$*"` on `-o=json` alone, so the fixture's other `yq` calls are unaffected).

- [ ] **Step 3: Expand command construction**

Add `base64` to the `for cmd in uv jq yq podman systemctl curl` tooling loop before changing `record_inferences()`.

Replace `inference_command()` with:

```bash
inference_command() {
    local file="$1" device="$2" layers="$3" n_cpu_moe="$4"
    local ctx_size="$5" max_output_tokens="$6" timeout_seconds="$7" moe_part=""
    [ -n "$n_cpu_moe" ] && moe_part="--n-cpu-moe ${n_cpu_moe} "
    printf 'timeout %q podman run --rm --device /dev/dri -v %q:/models:ro,z --entrypoint /app/llama %q cli -m %q --device %q --n-gpu-layers %q %s--ctx-size %q --single-turn --no-show-timings -p %q -n %q' \
        "$timeout_seconds" "$MODELS_DIR" "$image" "/models/${file}" "$device" \
        "$layers" "$moe_part" "$ctx_size" "Reply with exactly: ready" "$max_output_tokens"
}
```

Replace the declaration of `record_inferences()` before adding the loop. Bind all three parameters locally so `presets_file` never relies on Bash's dynamic/global scope:

```bash
record_inferences() {
    local device="$1" skip_reason="${2:-}" presets_file="${3:-}"
    local alias file model_b64 model_json check_ctx_override max_output_tokens
    local timeout_seconds n_gpu_layers n_cpu_moe preset_ctx_size missing_key
    local ctx_size command_text
```

Then decode one complete base64 model record and read all related values from that object:

```bash
while IFS= read -r model_b64; do
    [ -n "$model_b64" ] || continue
    model_json="$(printf '%s' "$model_b64" | base64 --decode)"
    alias="$(jq -r '.alias' <<<"$model_json")"
    file="$(jq -r '.file' <<<"$model_json")"
    check_ctx_override="$(jq -r '.check_ctx_size // empty' <<<"$model_json")"
    max_output_tokens="$(jq -r '.client_max_output_tokens' <<<"$model_json")"
    timeout_seconds="$(jq -r '.check_timeout_seconds // 180' <<<"$model_json")"
    if [ -n "$skip_reason" ]; then
        record_inference_skip "$alias" "not run: inference prerequisite unavailable" "$skip_reason"
        continue
    fi
    n_gpu_layers="$(presets_value "$presets_file" "$alias" "n-gpu-layers")"
    n_cpu_moe="$(presets_value "$presets_file" "$alias" "n-cpu-moe")"
    preset_ctx_size="$(presets_value "$presets_file" "$alias" "ctx-size")"
```

Absence of `n-cpu-moe` is valid, but both `n-gpu-layers` and `ctx-size` are required production preset keys:

```bash
if [ -z "$n_gpu_layers" ] || [ -z "$preset_ctx_size" ]; then
    missing_key="n-gpu-layers"
    [ -n "$n_gpu_layers" ] && missing_key="ctx-size"
    command_text="not run: missing ${missing_key} preset for ${alias}"
    log_error "missing ${missing_key} preset for ${alias}"
    FAIL=$((FAIL + 1))
    record_inference_skip "$alias" "$command_text" "missing required production preset"
    continue
fi
ctx_size="${check_ctx_override:-$preset_ctx_size}"
```

Pass every value to the real command, then close the loop. Do not fall back to raw `.n_gpu_layers`; rendered presets are the production source of truth.

```bash
moe_args=()
[ -n "$n_cpu_moe" ] && moe_args=(--n-cpu-moe "$n_cpu_moe")
command_text="$(inference_command "$file" "$device" "$n_gpu_layers" "$n_cpu_moe" \
    "$ctx_size" "$max_output_tokens" "$timeout_seconds")"
record_command "inference ${alias}" "$command_text" "Reply with exactly: ready" \
    "normalized assistant content: ready" "ready" 1 "Inference stdout" "Inference stderr" \
    timeout "$timeout_seconds" podman run --rm --device /dev/dri \
    -v "${MODELS_DIR}:/models:ro,z" --entrypoint /app/llama "$image" cli \
    -m "/models/${file}" --device "$device" --n-gpu-layers "$n_gpu_layers" \
    "${moe_args[@]}" --ctx-size "$ctx_size" --single-turn --no-show-timings \
    -p "Reply with exactly: ready" -n "$max_output_tokens" || true
done < "$model_records"
```

The producer feeding this loop must not be a bare `done < <(...)` process substitution: under that form, neither `set -uo pipefail` nor anything else makes the `while` loop's own exit status reflect a `yq`/`jq` failure, so a producer crash silently checks zero (or fewer than all) enabled models while the script still reports success. Materialize the producer's output to a file and check its own exit status *before* the loop starts, immediately after the local variable declarations at the top of `record_inferences()`:

```bash
model_records="$(mktemp "${diagnostic_dir}/inference-model-records.XXXXXX")" \
    || die "could not create model records file"
if ! yq -o=json -I=0 '[.models[] | select(.enabled)]' "$CONFIG_PATH" |
        jq -r '.[] | @base64' > "$model_records"; then
    log_error "failed to enumerate enabled models for offline inference"
    FAIL=$((FAIL + 1))
    return 1
fi
if [ ! -s "$model_records" ]; then
    log_error "no enabled models to check"
    FAIL=$((FAIL + 1))
    return 1
fi
```

- [ ] **Step 4: Make every prerequisite outcome explicit without relying on shell aborts**

Use status capture around preset rendering and safe JSON extraction:

```bash
presets_status=0
render_presets_file "$device" "$presets_file" || presets_status=$?
if [ "$presets_status" -ne 0 ]; then
    log_error "presets rendering failed for ${device} (exit ${presets_status})"
    FAIL=$((FAIL + 1))
    record_inferences "$device" "presets rendering failed" ""
else
    record_inferences "$device" "" "$presets_file"
fi
```

After the existing recorded resolution command:

```bash
device=""
if [ "$resolve_status" -eq 0 ]; then
    device="$(jq -r '.device // empty' < "$record_stdout_file" 2>/dev/null || true)"
fi
if [ "$resolve_status" -ne 0 ]; then
    record_inferences "" "GPU device could not be resolved" ""
elif [ -z "$device" ]; then
    log_error "GPU device resolution returned no device"
    FAIL=$((FAIL + 1))
    record_inferences "" "GPU device resolution returned no device" ""
else
    presets_file="$(mktemp "${diagnostic_dir}/presets.XXXXXX")" \
        || die "could not create presets diagnostic"
    presets_status=0
    render_presets_file "$device" "$presets_file" || presets_status=$?
    if [ "$presets_status" -ne 0 ]; then
        log_error "presets rendering failed for ${device} (exit ${presets_status})"
        FAIL=$((FAIL + 1))
        record_inferences "$device" "presets rendering failed" ""
    else
        record_inferences "$device" "" "$presets_file"
    fi
fi
```

Preserve `record_command`'s existing failure count for a nonzero `resolve-device`; do not increment it twice.

- [ ] **Step 5: Run focused coverage and the mandatory shell/Python-test gate**

Run:

```bash
uv run pytest tests/test_shell.py -k check_setup -v
make validate && make test
```

Expected: both enabled models use their own timeout/context/output/offload values; the 300-second stub executes; render failure, nonzero resolution, empty resolution, and missing preset section all exit nonzero with diagnostics; the full gate passes.

- [ ] **Step 6: Commit**

```bash
git add scripts/check-setup.sh tests/test_shell.py
git commit -m "fix(check-setup): exercise each model's production flags"
```

---

### Task 6: Benchmark every model safely with production flags

**Files:**
- Modify: `scripts/benchmark.sh:10-115`
- Modify/Test: `tests/test_shell.py:1804-1917`

**Interfaces:**
- Consumes: `migrate_config_file()` before any raw `yq` read; configured `.gpu.device_name`; Task 3 preset helpers; per-model `file`, optional `check_ctx_size`, and `client_max_output_tokens`. An absent check override uses the rendered preset's production `ctx-size`.
- Maps preset `n-gpu-layers` to `--n-gpu-layers`, optional `n-cpu-moe` to `--n-cpu-moe`, check context to llama-bench prompt-processing `-p`, and output budget to generation `-n`.
- Produces `.models[] | select(.alias == strenv(MODEL_ALIAS)) | .benchmark.vulkan` with numeric `pp_tps`/`tg_tps` and string `measured_at`.

- [ ] **Step 1: Replace the benchmark fixture with two configured models and controllable failures**

Give `run_benchmark` keyword parameters `resolve_exit=0`, `empty_resolve=False`, `presets_exit=0`, `presets_missing_alias=False`, and `device_name="Benchmark GPU"`. Configure:

```yaml
gpu:
  backend: vulkan
  image: ghcr.io/ggml-org/llama.cpp:server-vulkan
  device_name: Benchmark GPU
  benchmark:
    vulkan: {pp_tps: 1, tg_tps: 1, measured_at: legacy}
models:
  - alias: smallest
    enabled: true
    file: smallest.gguf
    size_bytes: 1
    ctx_size: 4096
    client_max_output_tokens: 512
  - alias: odd/"alias
    enabled: true
    file: biggest.gguf
    size_bytes: 2
    ctx_size: 8192
    check_ctx_size: 2048
    client_max_output_tokens: 1024
```

The `uv` stub must persist the same legacy-key deletion proved by Task 2 when called with `migrate-config`, return `{"device":"Vulkan0"}` for `resolve-device` (or the controlled nonzero/empty result), and render presets. Add the real `yq` path to the fixture environment as `REAL_YQ`, then place this branch before the controlled resolve/presets branches:

```bash
case "$*" in
    *' migrate-config')
        "$REAL_YQ" -i 'del(.gpu.benchmark)' "$LLM_ENV_CONFIG"
        printf '%s\n' '{"written":true}'
        exit 0
        ;;
esac
```

The rendered preset body is:

```ini
[smallest]
ctx-size = 4096
n-gpu-layers = 99

[odd/"alias]
ctx-size = 8192
n-gpu-layers = 40
n-cpu-moe = 12
```

Use alias-independent benchmark outputs from the Podman stub keyed by model filename. Do not embed the odd alias in any fixture pathname.

- [ ] **Step 2: Adapt the existing success test and add production-parity, migration, and failure tests**

Keep `test_benchmark_parses_valid_stdout_despite_vulkan_stderr_warning` at `tests/test_shell.py:1876-1901`; adapt its storage assertions to the two model entries while preserving its stdout, parsed-metrics, Vulkan-warning, parser-diagnostic, backend/image, and no-CPU-pull coverage:

```python
assert result.returncode == 0, result.stderr
assert "Benchmark stdout:" in result.stdout
assert '"avg_ts":123.4' in result.stdout
assert result.stdout.count(
    'Parsed metrics:\n  {"pp_tps":123.4,"tg_tps":56.7}'
) == 2
assert "Benchmark stderr:\n  WARNING: radv" in result.stdout
assert "Benchmark parser stderr:" not in result.stdout
assert yq_value(config, ".gpu.backend") == "vulkan"
assert yq_value(config, ".gpu.image") == "ghcr.io/ggml-org/llama.cpp:server-vulkan"
assert yq_value(
    config,
    '(.models[] | select(.alias == "smallest") | .benchmark.vulkan.pp_tps)',
) == "123.4"
assert yq_value(
    config,
    '(.models[] | select(.alias == "odd/\\\"alias") | .benchmark.vulkan.tg_tps)',
) == "56.7"
assert yq_value(config, ".gpu.benchmark") == "null"
assert "podman pull ghcr.io/ggml-org/llama.cpp:server" not in calls.read_text()
```

Keep `test_benchmark_configures_cpu_but_fails_when_vulkan_stdout_is_invalid` at `tests/test_shell.py:1904-1917`. Preserve its invalid-JSON diagnostics, CPU backend/image, and CPU-pull assertions; add `assert yq_value(config, ".gpu.benchmark") == "null"` because persisted migration happens before the failed Vulkan probe.

Add:

```python
def test_benchmark_uses_every_models_device_flags_and_probe_sizes(tmp_path):
    result, calls, config = run_benchmark(tmp_path, valid_benchmark_json)
    assert result.returncode == 0, result.stderr
    rows = [line for line in calls.read_text().splitlines() if " bench " in line]
    assert any("/models/smallest.gguf" in row and "--device Vulkan0" in row and "--n-gpu-layers 99" in row and "-p 4096 -n 512" in row for row in rows)
    assert any("/models/biggest.gguf" in row and "--device Vulkan0" in row and "--n-gpu-layers 40" in row and "--n-cpu-moe 12" in row and "-p 2048 -n 1024" in row for row in rows)
    assert yq_value(config, '(.models[] | select(.alias == "odd/\\\"alias") | .benchmark.vulkan.pp_tps)') == "90"
    assert yq_value(config, ".gpu.benchmark") == "null"


def test_make_benchmark_persists_legacy_shared_benchmark_migration(tmp_path):
    result, _, config = run_benchmark(tmp_path, valid_benchmark_json)
    assert result.returncode == 0, result.stderr
    assert yq_value(config, ".gpu.benchmark") == "null"
    assert yq_value(
        config,
        '(.models[] | select(.alias == "smallest") | .benchmark.vulkan.pp_tps)',
    ) == "90"


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"device_name": ""}, "configured gpu.device_name is empty"),
        ({"resolve_exit": 31}, "could not resolve configured GPU"),
        ({"empty_resolve": True}, "GPU resolution returned no device"),
        ({"presets_exit": 32}, "could not render production presets"),
        ({"presets_missing_alias": True}, "missing n-gpu-layers preset"),
    ],
)
def test_benchmark_fails_instead_of_skipping_all_models(tmp_path, kwargs, message):
    result, _, config = run_benchmark(tmp_path, valid_benchmark_json, **kwargs)
    assert result.returncode != 0
    assert message in result.stderr
    assert yq_value(config, ".gpu.benchmark") == "null"


def test_benchmark_fails_when_model_enumeration_fails(tmp_path):
    """A yq/jq enumeration crash must not let the script report success
    after benchmarking fewer than every enabled model -- this is what the
    materialized model_records file (Step 4) and the measured_models ==
    total_models check (Step 5) exist to catch."""
    result, calls, config = run_benchmark(
        tmp_path, valid_benchmark_json, yq_models_json_exit=19
    )
    assert result.returncode != 0
    assert "failed to enumerate enabled models" in result.stderr
    assert " bench " not in calls.read_text()
    assert yq_value(config, ".gpu.benchmark") == "null"
```

Add a `yq_models_json_exit: int = 0` keyword parameter to `run_benchmark`, mirroring Task 5's fixture change: the fixture's `yq` stub exits with that status specifically for the `-o=json -I=0 '[.models[] | select(.enabled)]'` invocation, matched by its exact arguments so the fixture's other `yq` calls (backend/image writes, migration) are unaffected.

Define `valid_benchmark_json` at module scope as `'[{"n_prompt":512,"avg_ts":90.0},{"n_gen":128,"avg_ts":30.0}]'`. Have `run_benchmark()` invoke `["/usr/bin/make", "benchmark"]`, not `scripts/benchmark.sh` directly, so the migration test exercises the public entrypoint named in the design.

- [ ] **Step 3: Resolve the configured GPU before rendering presets**

Replace VRAM-based name inference with the same configured-name path as `check-setup.sh`:

```bash
require_cmd uv jq yq podman awk base64
migrate_config_file || die "configuration migration failed"
diagnostic_dir="$(prepare_diagnostic_dir benchmark)"
trap 'status=$?; finish_diagnostic_dir "$diagnostic_dir"; exit "$status"' EXIT

device_name="$(yq -r '.gpu.device_name // ""' "$CONFIG_PATH")"
[ -n "$device_name" ] || die "configured gpu.device_name is empty"

listing_file="$(mktemp "${diagnostic_dir}/device-listing.XXXXXX")"
podman run --rm --device /dev/dri --entrypoint /app/llama-server \
    "$VULKAN_IMAGE" --list-devices >"$listing_file" 2>/dev/null \
    || die "could not list Vulkan devices"

resolve_status=0
resolved_json="$(llmenv resolve-device --device-name "$device_name" --listing-file "$listing_file")" \
    || resolve_status=$?
[ "$resolve_status" -eq 0 ] || die "could not resolve configured GPU ${device_name} (exit ${resolve_status})"
device="$(jq -r '.device // empty' <<<"$resolved_json" 2>/dev/null || true)"
[ -n "$device" ] || die "GPU resolution returned no device for ${device_name}"

presets_file="$(mktemp "${diagnostic_dir}/presets.XXXXXX")"
presets_status=0
render_presets_file "$device" "$presets_file" || presets_status=$?
[ "$presets_status" -eq 0 ] \
    || die "could not render production presets for ${device} (exit ${presets_status})"
```

Capturing status outside an `if` avoids `set -euo pipefail` aborting before the planned diagnostic branches.

- [ ] **Step 4: Define the parser and run one production-matching benchmark per encoded model record**

Move the existing validated `jq` expression out of `run_vulkan_bench()` into this exact helper before the per-model loop. It writes parser diagnostics to its second path and prints one compact metrics object on success:

```bash
parse_bench_json() {
    local stdout_file="$1" parser_stderr_file="$2"
    jq -ce '
        [(.[] | select(.n_prompt > 0) | .avg_ts),
         (.[] | select(.n_gen > 0) | .avg_ts)]
        | select(length == 2 and all(.[]; type == "number"))
        | {pp_tps: .[0], tg_tps: .[1]}
    ' "$stdout_file" 2>"$parser_stderr_file"
}
```

Materialize the enumeration into a file and check the producer's own exit status before looping — the same fix Task 5 applies to `check-setup.sh`, and for the same reason: a bare `done < <(yq | jq ...)` process substitution does not let `set -euo pipefail` (or anything else) fail the loop when the producer crashes partway through, so a `yq`/`jq` failure after emitting some records would silently benchmark only the models emitted before the crash while `measured_models` (Step 5) still ends up `> 0` and the script reports success:

```bash
model_records="$(mktemp "${diagnostic_dir}/benchmark-model-records.XXXXXX")"
if ! yq -o=json -I=0 '[.models[] | select(.enabled)]' "$CONFIG_PATH" |
        jq -r '.[] | @base64' > "$model_records"; then
    die "failed to enumerate enabled models for benchmarking"
fi
[ -s "$model_records" ] || die "no enabled models to benchmark"
```

Decode records with this exact boundary-safe loop. For each model, read the two preset offload values and fail that model explicitly if `n-gpu-layers` is empty:

```bash
per_model_status=0
measured_models=0
vulkan_probe_complete=0
while IFS= read -r model_b64; do
    [ -n "$model_b64" ] || continue
    model_json="$(printf '%s' "$model_b64" | base64 --decode)"
    alias="$(jq -r '.alias' <<<"$model_json")"
    file="$(jq -r '.file' <<<"$model_json")"
    check_ctx_override="$(jq -r '.check_ctx_size // empty' <<<"$model_json")"
    max_output_tokens="$(jq -r '.client_max_output_tokens' <<<"$model_json")"
    n_gpu_layers="$(presets_value "$presets_file" "$alias" "n-gpu-layers")"
    n_cpu_moe="$(presets_value "$presets_file" "$alias" "n-cpu-moe")"
    preset_ctx_size="$(presets_value "$presets_file" "$alias" "ctx-size")"
    if [ -z "$n_gpu_layers" ] || [ -z "$preset_ctx_size" ]; then
        missing_key="n-gpu-layers"
        [ -n "$n_gpu_layers" ] && missing_key="ctx-size"
        log_error "missing ${missing_key} preset for ${alias}"
        per_model_status=1
        continue
    fi
    check_ctx_size="${check_ctx_override:-$preset_ctx_size}"
```

Construct the command in that loop:

```bash
bench_args=(
    bench -m "/models/${file}" --device "$device"
    --n-gpu-layers "$n_gpu_layers"
)
[ -n "$n_cpu_moe" ] && bench_args+=(--n-cpu-moe "$n_cpu_moe")
bench_args+=(-p "$check_ctx_size" -n "$max_output_tokens" -r 2 -o json)
```

Create diagnostics only with alias-independent templates:

```bash
model_stdout="$(mktemp "${diagnostic_dir}/model-bench-stdout.XXXXXX")"
model_stderr="$(mktemp "${diagnostic_dir}/model-bench-stderr.XXXXXX")"
model_parser_stderr="$(mktemp "${diagnostic_dir}/model-bench-parser-stderr.XXXXXX")"
```

Run and parse the command with explicit status capture; do not allow `set -e` to bypass diagnostics:

```bash
log_command "podman run --rm --device /dev/dri -v ${MODELS_DIR}:/models:ro,z --entrypoint /app/llama ${VULKAN_IMAGE} ${bench_args[*]}"
model_status=0
podman run --rm --device /dev/dri \
    -v "${MODELS_DIR}:/models:ro,z" --entrypoint /app/llama \
    "$VULKAN_IMAGE" "${bench_args[@]}" >"$model_stdout" 2>"$model_stderr" \
    || model_status=$?
log_block "Benchmark stdout" "$(<"$model_stdout")"
log_nonempty_block "Benchmark stderr" "$(<"$model_stderr")"
log_block "Exit status" "$model_status"
if [ "$model_status" -ne 0 ]; then
    log_error "Vulkan benchmark failure for ${alias}: command exit ${model_status}"
    per_model_status=1
    continue
fi
model_result=""
if ! model_result="$(parse_bench_json "$model_stdout" "$model_parser_stderr")"; then
    log_nonempty_block "Benchmark parser stderr" "$(<"$model_parser_stderr")"
    log_error "Vulkan benchmark failure for ${alias}: response parsing"
    per_model_status=1
    continue
fi
log_nonempty_block "Benchmark parser stderr" "$(<"$model_parser_stderr")"
log_block "Parsed metrics" "$model_result"
pp="$(jq -er '.pp_tps' <<<"$model_result")"
tg="$(jq -er '.tg_tps' <<<"$model_result")"
```

- [ ] **Step 5: Persist a result without interpolating aliases into `yq` source**

Use one atomic expression with aliases and values passed as data:

```bash
measured_at="$(date -Iseconds)"
MODEL_ALIAS="$alias" PP_TPS="$pp" TG_TPS="$tg" MEASURED_AT="$measured_at" \
    yq -i '
        (.models[] | select(.alias == strenv(MODEL_ALIAS)) | .benchmark.vulkan) = {
            "pp_tps": env(PP_TPS),
            "tg_tps": env(TG_TPS),
            "measured_at": strenv(MEASURED_AT)
        }
    ' "$CONFIG_PATH"
measured_models=$((measured_models + 1))
done < "$model_records"
```

Never place `$alias` in a yq program or filename. At the end, require every enumerated model to have been measured — not merely "at least one" — so a partial-producer-crash scenario (caught upstream by Step 4's materialization) can never combine with a later per-model failure to look like a clean run:

```bash
total_models="$(wc -l < "$model_records")"
[ "$measured_models" -gt 0 ] || die "no enabled model benchmark completed successfully"
if [ "$measured_models" -ne "$total_models" ]; then
    log_error "measured ${measured_models} of ${total_models} enabled models"
    per_model_status=1
fi
[ "$per_model_status" -eq 0 ] || exit 1
```

- [ ] **Step 6: Preserve Vulkan-to-CPU fallback semantics**

Treat the first configured, device-pinned model command as both the Vulkan capability probe and its per-model measurement; do not run a separate fixed `-p 512 -n 128` command. Initialize `vulkan_probe_complete=0` before the model loop and add these helpers:

```bash
record_backend() {
    local backend="$1" image="$2"
    WINNER_BACKEND="$backend" WINNER_IMAGE="$image" \
        yq -i '.gpu.backend = strenv(WINNER_BACKEND) | .gpu.image = strenv(WINNER_IMAGE)' "$CONFIG_PATH"
}

fall_back_to_cpu() {
    record_backend cpu "$CPU_IMAGE"
    podman pull "$CPU_IMAGE" >/dev/null || die "cannot pull the CPU image"
}
```

In both the nonzero-command and parse-failure branches from Step 4, before the ordinary `per_model_status=1; continue`, use:

```bash
if [ "$vulkan_probe_complete" -eq 0 ]; then
    fall_back_to_cpu
    exit 1
fi
```

Immediately after the first successful parse and before persistence, use:

```bash
if [ "$vulkan_probe_complete" -eq 0 ]; then
    record_backend vulkan "$VULKAN_IMAGE"
    vulkan_probe_complete=1
fi
```

This keeps the existing CPU fallback for an unsuccessful Vulkan probe, removes hardcoded probe sizes, and avoids measuring the first model twice. A missing preset or device is a configuration prerequisite failure, not evidence that Vulkan itself failed, so those branches exit nonzero without rewriting the backend to CPU.

- [ ] **Step 7: Run focused coverage and the mandatory shell/Python-test gate**

Run:

```bash
uv run pytest tests/test_shell.py -k benchmark -v
make validate && make test
```

Expected: both models are measured with `--n-gpu-layers`; the MoE model also has `--n-cpu-moe`; prompt/generation sizes come from config; the odd alias persists safely; every prerequisite failure exits nonzero; CPU fallback remains covered; the full gate passes.

- [ ] **Step 8: Commit**

```bash
git add scripts/benchmark.sh tests/test_shell.py
git commit -m "feat(benchmark): measure configured models on the configured GPU"
```

---

### Task 7: Tune timeout on hardware, run acceptance, and document the flow

**Files:**
- Modify: `scripts/check-setup.sh:83-108`
- Modify: `scripts/check-server.sh:163-221,264-329`
- Modify/Test: `tests/test_shell.py:2702-3036,4930-5305`
- Modify: `.agents/architecture.md:9-24,147-179`
- Modify: `README.md:128-143`

**Interfaces:**
- Consumes all Tasks 1-6; produces separate empirically justified offline and HTTP fallback baselines. The largest configured model defines the shared defaults, so no Ornith-only example override is added.

- [ ] **Step 1: Run the full automated gate before hardware work**

Run: `make validate && make test`

Expected: PASS with zero failures.

- [ ] **Step 2: Snapshot live state and select Ornith temporarily**

Use a fixed, visible backup path so restoration does not depend on shell-local variables. Refuse to overwrite a leftover backup from an interrupted run:

```bash
live_config="$HOME/.config/llm-env/models.yml"
acceptance_backup="/tmp/llm-env-models.yml.before-config-aware-acceptance"
active_state="/tmp/llm-env-service-state.before-config-aware-acceptance"
test ! -e "$acceptance_backup"
test ! -e "$active_state"
install -m 600 "$live_config" "$acceptance_backup"
if systemctl --user is-active --quiet llm-server.service; then
    printf '%s\n' active > "$active_state"
else
    printf '%s\n' inactive > "$active_state"
fi
MODEL_ALIAS=ornith-35b yq -i '
    with(.models[]; .enabled = (.alias == strenv(MODEL_ALIAS))) |
    .runtime.models_max = 1 |
    (.models[] | select(.alias == strenv(MODEL_ALIAS)) | .check_timeout_seconds) = 1800
' "$live_config"
yq -e '
    (.runtime.models_max == 1) and
    ([.models[] | select(.enabled) | .alias] == ["ornith-35b"]) and
    (.models[] | select(.alias == "ornith-35b") |
        .ctx_size == 262144 and
        .client_max_output_tokens == 8192 and
        .n_gpu_layers == 99 and
        .n_cpu_moe == 28 and
        .check_timeout_seconds == 1800)
' "$live_config"
```

The temporary 1800-second per-model override prevents the old fallback from truncating the measurement. Step 8 restores the complete prior YAML, including enabled models, backend, image, and device name, and restores the service's original active/inactive state. Run Step 8 even if any acceptance command fails.

- [ ] **Step 3: Measure the full-context offline check path**

Run:

```bash
/usr/bin/time -f '%e' -o /tmp/llm-env-check-setup-seconds \
    env LLM_ENV_CHECK_VERBOSE=1 make check-setup
awk '{ seconds = int($1); if ($1 > seconds) seconds++; print seconds }' \
    /tmp/llm-env-check-setup-seconds
```

Expected: `check-setup` passes and its diagnostic command shows `--n-gpu-layers 99 --n-cpu-moe 28 --ctx-size 262144 -n 8192` on one resolved Vulkan device.

- [ ] **Step 4: Measure the real cold-load HTTP path**

Restart the router so its lazy-loaded model begins unloaded, then time the actual HTTP check. Its first direct completion request is the cold-load request; the later OmniRoute request remains part of the end-to-end script measurement:

```bash
make start
/usr/bin/time -f '%e' -o /tmp/llm-env-check-server-seconds \
    env LLM_ENV_CHECK_VERBOSE=1 make check-server
awk '{ seconds = int($1); if ($1 > seconds) seconds++; print seconds }' \
    /tmp/llm-env-check-server-seconds
```

Expected: `check-server` passes; the direct and OmniRoute bodies both contain `max_tokens: 8192`, and the timed run includes a real cold model load through `/v1/chat/completions`.

- [ ] **Step 5: Calculate and apply both timeout baselines, including their tests**

Calculate each path independently. For each fallback, use the larger of twice the measured ceiling and the measured ceiling plus 120 seconds:

```bash
offline_measured="$(awk '{ n = int($1); if ($1 > n) n++; print n }' /tmp/llm-env-check-setup-seconds)"
server_measured="$(awk '{ n = int($1); if ($1 > n) n++; print n }' /tmp/llm-env-check-server-seconds)"
offline_baseline="$((offline_measured * 2))"
[ "$offline_baseline" -ge "$((offline_measured + 120))" ] || \
    offline_baseline="$((offline_measured + 120))"
server_baseline="$((server_measured * 2))"
[ "$server_baseline" -ge "$((server_measured + 120))" ] || \
    server_baseline="$((server_measured + 120))"
printf 'offline measured=%s baseline=%s\nserver measured=%s baseline=%s\n' \
    "$offline_measured" "$offline_baseline" "$server_measured" "$server_baseline"
```

Always replace the offline `.check_timeout_seconds // 180` fallback with the printed numeric `offline_baseline`, and replace the HTTP `.check_timeout_seconds // 120` fallback used by both completion paths with the printed numeric `server_baseline`. Do not keep an older default merely because it is larger, and do not add `check_timeout_seconds` to `models.yml.example`: the measurements use the largest configured model and therefore define the shared defaults directly.

Update the fallback-sensitive tests in the same edit:

- In `run_check_setup_with_stubs`, make the timeout stub accept the printed `offline_baseline` and `300`, and update Task 5's `first_call`/`second_call` assertions so `first_call.startswith("timeout 180 podman run")` becomes `first_call.startswith(f"timeout {offline_baseline} podman run")` — keep the per-model-scoped form (not a global `recorded.count(...)`), since `offline_baseline` and the second model's explicit `300` can coincide.
- In `run_check_server`, keep Ornith's explicit `600`; update the expected timeout for both `gemma4` and `llama-cpp/gemma4` from `"120"` to the printed `server_baseline`.
- Search the affected block with `rg -n '180|120|check_timeout_seconds' tests/test_shell.py scripts/check-setup.sh scripts/check-server.sh` and update only fallback-sensitive occurrences; health, authentication, listing, login, and provider requests remain fixed at 10 seconds.

Remove the temporary live override so the acceptance run exercises the new fallbacks:

```bash
MODEL_ALIAS=ornith-35b yq -i \
    'del(.models[] | select(.alias == strenv(MODEL_ALIAS)) | .check_timeout_seconds)' \
    "$HOME/.config/llm-env/models.yml"
make validate && make test
```

Expected: the mandatory `.sh`/`.py` gate passes with the measured fallback literals and their paired assertions in sync.

- [ ] **Step 6: Re-run real hardware acceptance**

Run:

```bash
make setup
make check-setup
make start
make status
make gpu-status
make check-server
make benchmark
make check-with-agents
```

Confirm from verbose diagnostics/config:

- `check-setup`: `--n-gpu-layers 99 --n-cpu-moe 28 --ctx-size 262144 -n 8192` and the measured timeout.
- `check-server`: both direct and OmniRoute bodies use `max_tokens: 8192`; there is intentionally no HTTP context-size field.
- `benchmark`: one resolved `--device`, `--n-gpu-layers 99`, `--n-cpu-moe 28`, `-p 262144`, `-n 8192`, and populated `.models[] | select(.alias == "ornith-35b") | .benchmark.vulkan`.
- All in-scope targets exit zero. Record the known Pi CLI failure from `check-with-agents` separately; it remains out of scope and must not mask the other results.

- [ ] **Step 7: Update architecture and README documentation**

Document all of the following in the existing benchmark/check sections:

- shared `gpu.benchmark` is retired; results live under each model's `benchmark.vulkan`;
- offline check and benchmark offload flags come from the production preset render;
- offline `-n` and benchmark generation size come from `client_max_output_tokens`;
- `check_ctx_size` defaults to `ctx_size` and applies only to offline CLI/benchmark sizing, because HTTP chat completions has no per-request context-size control;
- separate offline and HTTP `check_timeout_seconds` fallback values, their measured cold-path durations, and the exact margin formula from Step 5;
- prerequisite failures are nonzero, and aliases are passed to `yq` as data.

- [ ] **Step 8: Restore the complete live configuration and prior service state**

Run these commands whether acceptance passed or failed:

```bash
live_config="$HOME/.config/llm-env/models.yml"
acceptance_backup="/tmp/llm-env-models.yml.before-config-aware-acceptance"
active_state="/tmp/llm-env-service-state.before-config-aware-acceptance"
test -f "$acceptance_backup"
test -f "$active_state"
install -m 600 "$acceptance_backup" "$live_config"
if [ "$(<"$active_state")" = active ]; then
    make restart
else
    make stop
fi
rm -f "$acceptance_backup" "$active_state"
```

Expected: the previously enabled model set, `gpu.backend`, `gpu.image`, `gpu.device_name`, and every other live setting exactly match the saved file; the service is active only if it was active before Step 2.

- [ ] **Step 9: Run the final gate and commit**

Run: `make validate && make test`

Expected: PASS. This is mandatory because Task 7 edits `.sh` files and their Python tests.

```bash
git add .agents/architecture.md README.md scripts/check-setup.sh scripts/check-server.sh tests/test_shell.py
git commit -m "docs+tune: record config-aware check timeout and benchmark flow"
```

---

## Self-Review Notes

- **Spec coverage:** Component 1 is Task 1; Component 2 is Task 3; Component 3 is Task 4; reconciled Component 4 is Tasks 4-5 plus the explicit HTTP rationale in Global Constraints; Component 5 is Task 6; Component 6 is Task 2; Component 7 is Task 7.
- **Review coverage:** Task 3's helper fixture now satisfies every `require_valid_config()` requirement, including `gpu.reserve_mode`. Task 4 extends the real `run_check_server()` fixture and preserves every existing success/failure test while adding per-model isolation, yq/jq producer-failure, zero-model, and 10-second auth-probe coverage. Task 5 binds all three `record_inferences()` parameters locally, materializes its model enumeration to a file with an explicit producer-failure check (rather than an unchecked `done < <(...)` process substitution) so a `yq`/`jq` crash can never look like a zero-model clean run, and scopes its timeout assertions per-model instead of by global string count so a coincidental baseline/override collision can't produce a false failure. Task 6 defines `parse_bench_json()`, adapts the existing successful/invalid benchmark tests, persists legacy-key migration before standalone `make benchmark` writes, applies the same materialized-enumeration fix as Task 5, and requires `measured_models == total_models` (not merely `> 0`) so a mid-enumeration producer crash can't masquerade as a complete run. Task 7 measures both cold paths, updates fallback-sensitive tests (including the per-model-scoped assertions from Task 5), runs the full gate, and restores live config/service state.
- **Repository gates:** Tasks 1-6 explicitly run `make validate && make test` after their `.py`/`.sh` edits. Task 7 runs `make validate && make test` both immediately after fallback/test edits and again before commit.
- **Placeholder scan:** No TBD/TODO/future implementation markers or undefined helper references remain. Empirical values are generated by exact commands and have exact replacement sites.
- **Interface consistency:** `render_presets_file(device, output)` and `presets_value(file, alias, key)` have identical signatures in Tasks 3, 5, and 6. `record_inferences()` binds `device`, `skip_reason`, and `presets_file`; offline inference carries seven arguments consistently. Both HTTP loops decode the same materialized model-record shape, and `parse_bench_json(stdout_file, parser_stderr_file)` matches every call site.
- **Task right-sizing:** Each task owns one independently reviewable behavior and its red/green cycle. Shared helpers land before their two consumers; empirical tuning follows all automated behavior.
