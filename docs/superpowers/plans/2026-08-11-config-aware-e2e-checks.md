# Config-Aware E2E Check Scripts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `check-setup.sh`, `check-server.sh`, and `benchmark.sh` read every model-specific value (offload split, context size, output-token budget, GPU device, probe sizing) from `models.yml` instead of hardcoding or re-deriving it, so these scripts always validate exactly the configuration that's actually deployed.

**Architecture:** Add two optional per-model config fields (`check_ctx_size`, `check_timeout_seconds`) with production-parity defaults. Add a shared `tools/lib.sh` helper pair (`render_presets_file`, `presets_value`) that lets `check-setup.sh` and `benchmark.sh` read the exact `n-gpu-layers`/`n-cpu-moe` flags from `llmenv presets`'s own output — the same source `start.sh` already uses for the real server — instead of re-deriving them. `check-server.sh` reads `client_max_output_tokens` directly from config (a client-side value that was never part of `presets.ini`). Benchmark results move from one shared `gpu.benchmark` slot to per-model `.models[].benchmark.vulkan`, and `benchmark.sh` gains a per-model, device-pinned throughput pass after its existing one-time Vulkan-vs-CPU backend probe.

**Tech Stack:** Bash (`yq`, `jq`, `awk`, `podman`), Python 3.11+ (`uv run`), pytest. No new dependencies — reuses `llmenv presets`, `llmenv resolve-device`, `llmenv list-devices`, all of which already exist.

## Global Constraints

- **No hardcoded values in `.sh`/`.py` scripts for anything that varies per model.** `n_cpu_moe`, `n_gpu_layers`, `ctx_size` (for checks: `check_ctx_size`), `client_max_output_tokens` — all come from `models.yml`, either directly or via an optional override field that defaults to the model's real configured value.
- **Checks validate exactly the configured production values by default.** The same `ctx_size`/`n_cpu_moe` the real server uses. A check is never silently scaled down. A deviation is only ever an explicit, documented, per-model config override (`check_ctx_size`), never an implicit script shortcut.
- **Scripts must be agnostic to how many models are enabled** — loop over every enabled model, never assume exactly one.
- Lazy-load (llama.cpp router mode) + idle-unload stays exactly as-is. No eager-load mode.
- `make prune` is out of scope.
- `check-with-agents.sh` is out of scope (its Pi-client failure is an unrelated broken local npm install).
- `runtime.parallel_slots` stays `1` (existing validated invariant, unaffected by this plan).
- A script-level default (e.g. the fallback baseline for `check_timeout_seconds` when unset) is allowed — it's the explicitly-permitted "script baseline" from the design's Component 1, not a per-model value.

---

## File Map

- Modify: `pylib/config.py` — schema validation for `check_ctx_size`/`check_timeout_seconds`; `migrate_config()` drops `gpu.benchmark`.
- Modify: `tools/lib.sh` — add `render_presets_file()`, `presets_value()`.
- Modify: `scripts/check-server.sh` — per-model `max_tokens`/timeout in both completion loops.
- Modify: `scripts/check-setup.sh` — presets-sourced `n-gpu-layers`/`n-cpu-moe`, config-aware `ctx_size`/timeout for the offline inference check.
- Modify: `scripts/benchmark.sh` — per-model, device-pinned throughput pass after the existing backend probe; per-model result storage.
- Modify: `models.yml.example` — drop `gpu.benchmark`; document the two new optional fields via a comment.
- Modify: `tests/test_config.py`, `tests/test_shell.py` — cover all of the above.
- Modify: `.agents/architecture.md`, `README.md` (if it documents `gpu.benchmark` or the check flow) — reflect the schema move and config-awareness.

---

### Task 1: Schema — `check_ctx_size` / `check_timeout_seconds` validation

**Files:**
- Modify: `pylib/config.py:382-389` (right after the existing `n_cpu_moe` validation block, before `enabled_count = len(enabled_models(cfg))`)
- Test: `tests/test_config.py` (near the existing `n_cpu_moe` tests at line ~705)

**Interfaces:**
- Produces: `validate_config()` now rejects `check_ctx_size`/`check_timeout_seconds` that are present but not a positive int. Both fields remain optional — absent is always valid.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_config.py`, near the existing `n_cpu_moe` tests (after `test_validate_config_rejects_invalid_n_cpu_moe`, before `def test_migrate_config_adds_default_vram_budget_ceiling`):

```python
def test_validate_config_accepts_check_ctx_size_as_positive_int():
    cfg = make_cfg()
    cfg["models"][0]["check_ctx_size"] = 8192
    assert validate_config(cfg) == []


@pytest.mark.parametrize("value", [0, -1, True, "8192", 1.5])
def test_validate_config_rejects_invalid_check_ctx_size(value):
    cfg = make_cfg()
    cfg["models"][0]["check_ctx_size"] = value
    errors = validate_config(cfg)
    assert any("check_ctx_size" in error for error in errors)


def test_validate_config_accepts_check_timeout_seconds_as_positive_int():
    cfg = make_cfg()
    cfg["models"][0]["check_timeout_seconds"] = 600
    assert validate_config(cfg) == []


@pytest.mark.parametrize("value", [0, -1, True, "600", 1.5])
def test_validate_config_rejects_invalid_check_timeout_seconds(value):
    cfg = make_cfg()
    cfg["models"][0]["check_timeout_seconds"] = value
    errors = validate_config(cfg)
    assert any("check_timeout_seconds" in error for error in errors)


def test_validate_config_omits_check_fields_without_error():
    """Both fields are optional -- absent must never produce a validation error."""
    cfg = make_cfg()
    cfg["models"][0].pop("check_ctx_size", None)
    cfg["models"][0].pop("check_timeout_seconds", None)
    assert validate_config(cfg) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_config.py -k check_ctx_size -v`
Expected: `test_validate_config_accepts_check_ctx_size_as_positive_int` and the parametrized rejection test FAIL (no validation exists yet, so invalid values pass silently and the "accepts" test also fails only if it happens to already pass — the rejection tests are the ones that must fail here since nothing currently rejects these fields).

- [ ] **Step 3: Implement the validation**

In `pylib/config.py`, immediately after the existing block:

```python
        if "n_cpu_moe" in model:
            n_cpu_moe = model["n_cpu_moe"]
            if isinstance(n_cpu_moe, bool) or not (
                isinstance(n_cpu_moe, int) and n_cpu_moe >= 0
            ):
                errors.append(
                    f"model {model_name} n_cpu_moe must be a non-negative integer"
                )
```

add:

```python
        for key in ("check_ctx_size", "check_timeout_seconds"):
            if key in model and not _positive_int(model[key]):
                errors.append(f"model {model_name} {key} must be a positive integer")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_config.py -k "check_ctx_size or check_timeout_seconds" -v`
Expected: PASS, all 5 new tests.

- [ ] **Step 5: Commit**

```bash
git add pylib/config.py tests/test_config.py
git commit -m "feat(config): validate optional check_ctx_size/check_timeout_seconds fields"
```

---

### Task 2: Schema — per-model benchmark storage, drop `gpu.benchmark`

**Files:**
- Modify: `pylib/config.py:92-101` (`migrate_config()`'s `gpu` handling block)
- Modify: `models.yml.example:11-27` (drop the `gpu.benchmark` block)
- Test: `tests/test_config.py` (near existing migration tests)

**Interfaces:**
- Produces: `migrate_config()` no longer emits/preserves `gpu.benchmark`; any pre-existing `gpu.benchmark` key (including the stale `.rocm` sub-key) is dropped entirely on migration. Per-model `benchmark.vulkan.{pp_tps,tg_tps,measured_at}` is not created by migration — it's written only by `benchmark.sh` at runtime (Task 6). No schema validation is added for it (it's generated data, like `gpu.device_name`, not user-authored config), so `validate_config()` is untouched by this task.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_config.py`, near `test_migrate_config_adds_default_vram_budget_ceiling`:

```python
def test_migrate_config_drops_the_legacy_shared_benchmark_key():
    """gpu.benchmark moved to per-model .models[].benchmark.vulkan (Task 6);
    migration must retire the old shared key entirely, not just its stale
    .rocm sub-key."""
    cfg = make_cfg()
    cfg["gpu"]["benchmark"] = {
        "vulkan": {"pp_tps": 100.0, "tg_tps": 20.0, "measured_at": "2026-01-01T00:00:00"},
        "rocm": {"pp_tps": 1.0},
    }
    migrated = migrate_config(cfg)
    assert "benchmark" not in migrated["gpu"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_config.py -k drops_the_legacy_shared_benchmark_key -v`
Expected: FAIL — current code only pops `.rocm`, leaving `gpu.benchmark.vulkan` (and now-empty `gpu.benchmark`) in place, so `"benchmark" not in migrated["gpu"]` is false.

- [ ] **Step 3: Implement**

In `pylib/config.py`, replace:

```python
    benchmark = gpu.get("benchmark")
    if isinstance(benchmark, dict):
        benchmark.pop("rocm", None)
    return cfg
```

with:

```python
    gpu.pop("benchmark", None)
    return cfg
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_config.py -k drops_the_legacy_shared_benchmark_key -v`
Expected: PASS. Also run the full config suite to confirm no regression: `uv run pytest tests/test_config.py -v`

- [ ] **Step 5: Update `models.yml.example`**

Remove the `benchmark:` sub-block from the `gpu:` section. Change:

```yaml
gpu:
  pci_address: ""
  device_name: ""
  backend: vulkan
  image: ghcr.io/ggml-org/llama.cpp:server-vulkan
  vram_total_mib: 0
  reserve_mode: auto
  reserve_floor_mib: 1024
  vram_budget_ceiling_pct: 95
  # 0 = no cap; make setup computes and persists the real value
  vram_budget_ceiling_mib: 0
  vram_budget_ceiling_floor_pct: 30
  benchmark:
    vulkan:
      pp_tps: null
      tg_tps: null
      measured_at: null
```

to:

```yaml
gpu:
  pci_address: ""
  device_name: ""
  backend: vulkan
  image: ghcr.io/ggml-org/llama.cpp:server-vulkan
  vram_total_mib: 0
  reserve_mode: auto
  reserve_floor_mib: 1024
  vram_budget_ceiling_pct: 95
  # 0 = no cap; make setup computes and persists the real value
  vram_budget_ceiling_mib: 0
  vram_budget_ceiling_floor_pct: 30
  # Per-model .benchmark.vulkan.{pp_tps,tg_tps,measured_at} is written by
  # `make benchmark` directly onto each model entry below; it is not
  # declared here because it doesn't exist until a benchmark has run.
```

Then, in the `models:` list, add a comment documenting the two new optional per-model fields just above the `ornith-35b` entry (the model most likely to need an override, given its size and `262144` ctx):

```yaml
  # check_ctx_size (optional, default: this model's own ctx_size) and
  # check_timeout_seconds (optional, default: a script-level baseline) let
  # `check-setup`/`check-server`/`benchmark` diverge from full production
  # parity for a specific model -- e.g. a slower cold-load at full context.
  # Uncomment/add only when a model actually needs a probe override; do not
  # add them speculatively.
  - alias: ornith-35b
```

- [ ] **Step 6: Commit**

```bash
git add pylib/config.py tests/test_config.py models.yml.example
git commit -m "feat(config): migrate gpu.benchmark to per-model storage, drop the shared key"
```

---

### Task 3: Shared presets-reading helpers in `tools/lib.sh`

**Files:**
- Modify: `tools/lib.sh` (add two functions after `migrate_config_file()`, i.e. after line 235)
- Test: `tests/test_shell.py` (new tests near the top-level shell helper coverage, or a new section — see Step 1)

**Interfaces:**
- Produces:
  - `render_presets_file(device, output_path)` — runs `llmenv presets --models-dir "$MODELS_DIR" --device "$device" --output "$output_path"`, returns the command's exit status.
  - `presets_value(presets_file, alias, key)` — prints the value of `key` (e.g. `n-gpu-layers`, `n-cpu-moe`) inside `presets_file`'s `[alias]` section to stdout, or nothing if the key/section is absent. Never fails the script (pure text lookup).
- Consumes: `pylib/presets.py::render_presets()`'s exact output format (confirmed by reading the file): one `[*]` globals section, then one `[<alias>]` section per enabled model with `configparser`'s default `key = value` (space-padded `=`) formatting, keys `model`, `ctx-size`, `n-gpu-layers`, and optionally `n-cpu-moe`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_shell.py`. Place it near the top of the file, after the imports/fixtures but before the first `def test_` (check existing helper placement — if `tests/test_shell.py` has a "shared helpers" section, put it there; otherwise add right after `test_shell_scripts_use_the_approved_directories`):

```python
def test_presets_value_reads_the_generated_presets_ini(tmp_path: pathlib.Path) -> None:
    """render_presets_file/presets_value in tools/lib.sh must round-trip
    exactly what pylib/presets.py writes -- confirmed by generating a real
    presets.ini via llmenv.py, not a hand-written fixture, so a future
    change to render_presets()'s format is caught here too."""
    config = tmp_path / "models.yml"
    config.write_text(
        "version: 1\n"
        "server:\n  host: 0.0.0.0\n  port: 8000\n  api_key: k\n"
        "gpu:\n  backend: vulkan\n  pci_address: '0000:03:00.0'\n"
        "  vram_total_mib: 16384\n  reserve_mode: auto\n  reserve_floor_mib: 1024\n"
        "runtime:\n  models_max: 1\n  parallel_slots: 1\n  ubatch_size: 512\n"
        "  flash_attn: true\n  cache_type_k: q5_1\n  cache_type_v: q5_1\n"
        "models:\n"
        "  - alias: dense\n"
        "    label: Dense\n    parameters: 1B\n    quantization: Q4_K_M\n"
        "    enabled: true\n    file: dense.gguf\n    url: https://example.invalid/d\n"
        "    size_bytes: 1\n    vram_budget: 50%\n    ctx_size: 4096\n"
        "    client_max_output_tokens: 2048\n    n_gpu_layers: 99\n"
        "  - alias: moe\n"
        "    label: MoE\n    parameters: 8B\n    quantization: Q4_K_M\n"
        "    enabled: true\n    file: moe.gguf\n    url: https://example.invalid/m\n"
        "    size_bytes: 1\n    vram_budget: 50%\n    ctx_size: 8192\n"
        "    client_max_output_tokens: 2048\n    n_gpu_layers: 40\n"
        "    n_cpu_moe: 12\n"
    )
    presets_file = tmp_path / "presets.ini"
    result = subprocess.run(
        [
            "/usr/bin/bash", "-c",
            'source tools/lib.sh && render_presets_file "$1" "$2" && '
            'presets_value "$2" dense n-gpu-layers && '
            'presets_value "$2" moe n-gpu-layers && '
            'presets_value "$2" moe n-cpu-moe && '
            '(presets_value "$2" dense n-cpu-moe; echo "<empty>")',
            "bash", "Vulkan0", str(presets_file),
        ],
        cwd=ROOT,
        env=os.environ | {"LLM_ENV_CONFIG": str(config), "LLM_ENV_MODELS_DIR": str(tmp_path / "models")},
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["99", "40", "12", "<empty>"]
    assert "[dense]" in presets_file.read_text()
    assert "[moe]" in presets_file.read_text()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_shell.py -k presets_value_reads_the_generated_presets_ini -v`
Expected: FAIL — `render_presets_file: command not found` (function doesn't exist yet).

- [ ] **Step 3: Implement the helpers**

In `tools/lib.sh`, immediately after `migrate_config_file()` (after line 235, before `new_api_key()`):

```bash
render_presets_file() {
    local device="$1" output="$2"
    llmenv --config "$CONFIG_PATH" presets --models-dir "$MODELS_DIR" --device "$device" --output "$output" >/dev/null
}

# Reads one key's value out of one alias's section in a presets.ini
# generated by render_presets_file(). Prints nothing (not an error) if the
# section or key is absent, since n-cpu-moe is legitimately absent for
# dense models -- callers branch on emptiness, not on this function's exit
# status.
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

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_shell.py -k presets_value_reads_the_generated_presets_ini -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tools/lib.sh tests/test_shell.py
git commit -m "feat(lib): add render_presets_file/presets_value shared helpers"
```

---

### Task 4: `check-server.sh` — per-model `max_tokens` and timeout

**Files:**
- Modify: `scripts/check-server.sh:163-221` (direct completions loop)
- Modify: `scripts/check-server.sh:264-329` (OmniRoute completions loop)
- Test: `tests/test_shell.py` (new tests near any existing `check-server.sh` coverage; if none exists yet, add a new `run_check_server_with_stubs` fixture following the `run_check_setup_with_stubs`/`run_benchmark` pattern already in the file)

**Interfaces:**
- Consumes: `render_presets_file`/`presets_value` are NOT needed here — `client_max_output_tokens` and `check_timeout_seconds` are check-only/client-side values, read directly via `yq`, exactly like `pylib/presets.py` already excludes `client_max_output_tokens` from `presets.ini` for the same reason.
- Produces: no new shared interfaces; this task only changes `check-server.sh`'s internal per-alias loop variables from `alias` to `alias, max_tokens, timeout_seconds`.

- [ ] **Step 1: Write the failing test**

First, check whether `tests/test_shell.py` already has server-completions coverage:

Run: `grep -n "check-server\|check_server" tests/test_shell.py`

If it returns nothing, add a new fixture and tests near the `run_benchmark`/`run_check_setup_with_stubs` fixtures (same file). Add:

```python
def run_check_server_with_stubs(
    tmp_path: pathlib.Path,
    *,
    client_max_output_tokens: int = 256,
    check_timeout_seconds: int | None = None,
) -> tuple[subprocess.CompletedProcess[str], pathlib.Path]:
    """Run check-server.sh's completion loops with a stub curl that echoes
    back the request body it was sent (via --data-raw), so the test can
    assert on exactly what max_tokens/timeout the script constructed."""
    real_yq = shutil.which("yq")
    assert real_yq is not None

    commands = tmp_path / "bin"
    commands.mkdir()
    calls = tmp_path / "calls"
    calls.touch()

    for name in ("systemctl",):
        _mock_command(commands, name)

    yq = commands / "yq"
    yq.write_text("#!/usr/bin/bash\nexec \"$REAL_YQ\" \"$@\"\n")
    yq.chmod(yq.stat().st_mode | stat.S_IXUSR)

    curl = commands / "curl"
    curl.write_text(
        "#!/usr/bin/bash\n"
        "printf 'curl %s\\n' \"$*\" >> \"$CALLS\"\n"
        "out=''; code=200; time_limit=''\n"
        "args=(\"$@\")\n"
        "for i in \"${!args[@]}\"; do\n"
        "  case \"${args[$i]}\" in\n"
        "    -o) out=\"${args[$((i+1))]}\" ;;\n"
        "    --max-time) time_limit=\"${args[$((i+1))]}\" ;;\n"
        "    --data-raw) printf '%s\\n' \"${args[$((i+1))]}\" >> \"$CALLS.bodies\" ;;\n"
        "  esac\n"
        "done\n"
        "case \"$*\" in\n"
        "  *'/health'*) printf '' > \"$out\" ;;\n"
        "  *'/v1/models'*) printf '{\"data\":[{\"id\":\"only\"}]}' > \"$out\" ;;\n"
        "  *'/v1/chat/completions'*) printf '{\"choices\":[{\"message\":{\"content\":\"ready\"}}]}' > \"$out\" ;;\n"
        "  *'/api/auth/login'*) code=401 ;;\n"
        "esac\n"
        "printf '%s' \"$code\"\n"
    )
    curl.chmod(curl.stat().st_mode | stat.S_IXUSR)

    jq_path = shutil.which("jq")
    assert jq_path is not None

    config = tmp_path / "models.yml"
    check_timeout_line = (
        f"    check_timeout_seconds: {check_timeout_seconds}\n"
        if check_timeout_seconds is not None else ""
    )
    config.write_text(
        "server:\n  port: 8000\n  api_key: k\n  host: 0.0.0.0\n"
        "omniroute:\n  port: 20128\n  initial_password: p\n"
        "models:\n"
        "  - alias: only\n"
        "    enabled: true\n"
        f"    client_max_output_tokens: {client_max_output_tokens}\n"
        f"{check_timeout_line}"
    )
    environment = os.environ | {
        "CALLS": str(calls),
        "HOME": str(tmp_path / "home"),
        "LLM_ENV_CONFIG": str(config),
        "PATH": f"{commands}:{pathlib.Path(jq_path).parent}:/usr/bin:/bin",
        "REAL_YQ": real_yq,
    }
    result = subprocess.run(
        ["/usr/bin/bash", "scripts/check-server.sh"],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    return result, calls


def test_check_server_uses_the_models_own_client_max_output_tokens(tmp_path: pathlib.Path) -> None:
    """A model with an 8192 output budget must not be probed with the old
    hardcoded 256 -- that was the exact bug that produced a false FAIL
    against Ornith's reasoning traces."""
    result, calls = run_check_server_with_stubs(tmp_path, client_max_output_tokens=8192)

    bodies = (tmp_path / "calls.bodies").read_text()
    assert '"max_tokens":8192' in bodies or '"max_tokens": 8192' in bodies
    assert '"max_tokens":256' not in bodies and '"max_tokens": 256' not in bodies
    assert result.returncode == 0, result.stderr


def test_check_server_uses_check_timeout_seconds_override(tmp_path: pathlib.Path) -> None:
    result, calls = run_check_server_with_stubs(tmp_path, check_timeout_seconds=600)

    recorded = calls.read_text()
    completion_calls = [line for line in recorded.splitlines() if "/v1/chat/completions" in line]
    assert completion_calls, recorded
    assert all("--max-time 600" in line for line in completion_calls)


def test_check_server_defaults_timeout_when_unset(tmp_path: pathlib.Path) -> None:
    result, calls = run_check_server_with_stubs(tmp_path)

    recorded = calls.read_text()
    completion_calls = [line for line in recorded.splitlines() if "/v1/chat/completions" in line]
    assert completion_calls, recorded
    assert all("--max-time 120" in line for line in completion_calls)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_shell.py -k check_server -v`
Expected: `test_check_server_uses_the_models_own_client_max_output_tokens` FAILs (body still contains `"max_tokens":256`); the override test fails to find `--max-time 600`.

- [ ] **Step 3: Implement — direct completions loop**

In `scripts/check-server.sh`, replace lines 163-221 (`log_step "Completions"` through the `done < <(yq ...)` closing the direct loop) with:

```bash
log_step "Completions"
while IFS=$'\t' read -r alias max_tokens timeout_seconds; do
    [ -n "$alias" ] || continue
    body="$(jq -n --arg m "$alias" --argjson mt "$max_tokens" \
        '{model: $m,
          messages: [{role: "user", content: "Reply with exactly: ready"}],
          max_tokens: $mt, stream: false}')"

    identity="server completion model=${alias}"
    expectation="normalized assistant content: ready"
    request_record "$identity" \
        "curl --silent --show-error --max-time ${timeout_seconds} -H 'Authorization: Bearer ${api_key}' -H 'Content-Type: application/json' --data-raw '${body}' ${base}/v1/chat/completions" \
        "$body" "$expectation" -- \
        curl --silent --show-error --max-time "$timeout_seconds" \
        -K "$auth_conf" \
        -H "Content-Type: application/json" \
        --data-raw "$body" "${base}/v1/chat/completions"

    content=""
    normalized=""
    failure_stage=""
    failure_detail=""
    completion_parse_stderr="$(mktemp "${diagnostic_dir}/parse.XXXXXX")"
    if [ "$REQUEST_CURL_STATUS" -ne 0 ]; then
        failure_stage="curl failure"
        failure_detail="exit=${REQUEST_CURL_STATUS}"
    elif [[ ! "$REQUEST_HTTP_STATUS" =~ ^2[0-9][0-9]$ ]]; then
        failure_stage="HTTP response"
        failure_detail="status=${REQUEST_HTTP_STATUS}"
    elif ! jq . "$REQUEST_BODY_FILE" >/dev/null 2>"$completion_parse_stderr"; then
        failure_stage="invalid JSON"
    else
        content="$(jq -r '.choices?[0]?.message?.content? // empty' \
            < "$REQUEST_BODY_FILE" 2>>"$completion_parse_stderr")"
        normalized="$(printf '%s' "$content" | tr '[:upper:]' '[:lower:]' | \
            sed -E 's/^[[:space:][:punct:]]+//; s/[[:space:][:punct:]]+$//')"
        if [ -z "$content" ]; then
            failure_stage="missing assistant content"
        elif [ "$normalized" != ready ]; then
            failure_stage="normalized-value mismatch"
            failure_detail="${alias}: expected ready, got $(printf '%.80s' "$content")"
        fi
    fi

    log_block "Assistant content" "$content"
    log_block "Normalized content" "$normalized"
    log_nonempty_block "Response parsing stderr" "$(<"$completion_parse_stderr")"
    log_block "Expectation" "$REQUEST_EXPECTATION"
    if [ -n "$failure_stage" ]; then
        close_diagnostic_capture 1
        bad "Verdict: FAIL stage=${failure_stage} identity=${identity} ${failure_detail}"
        LLM_SERVER_COMPLETION_OK["$alias"]=0
        continue
    fi

    close_diagnostic_capture 0
    ok "Verdict: PASS identity=${identity} ${alias}: returned ready"
    LLM_SERVER_COMPLETION_OK["$alias"]=1
done < <(yq -r '.models[] | select(.enabled) | [.alias, .client_max_output_tokens, (.check_timeout_seconds // 120)] | @tsv' "$CONFIG_PATH")
```

(Only the loop driver, the `body=` construction, and the `curl --max-time` line/display string actually changed; the rest is reproduced verbatim so the replacement is unambiguous.)

- [ ] **Step 4: Implement — OmniRoute completions loop**

In `scripts/check-server.sh`, replace lines 264-329 (`log_step "OmniRoute completions"` through its closing `done < <(yq ...)`) with:

```bash
log_step "OmniRoute completions"
while IFS=$'\t' read -r alias max_tokens timeout_seconds; do
    [ -n "$alias" ] || continue
    if [ "${LLM_SERVER_COMPLETION_OK[$alias]:-1}" -eq 0 ]; then
        log_warn "Verdict: SKIP identity=omniroute completion model=${alias} reason=server completion model=${alias} already failed; fix that first, OmniRoute proxies to the same model"
        continue
    fi
    # Routing keys on the provider slug ("llama-cpp"), not the connection's
    # own name -- confirmed live via GET /v1/models, which lists synced
    # models as "llama-cpp/<alias>" regardless of the connection's name.
    body="$(jq -n --arg m "llama-cpp/${alias}" --argjson mt "$max_tokens" \
        '{model: $m,
          messages: [{role: "user", content: "Reply with exactly: ready"}],
          max_tokens: $mt, stream: false}')"

    identity="omniroute completion model=${alias}"
    expectation="normalized assistant content: ready"
    # The dashboard password doubles as the bearer token for /v1/* routes,
    # not only for the management API's cookie session -- confirmed live.
    request_record "$identity" \
        "curl --silent --show-error --max-time ${timeout_seconds} -H 'Authorization: Bearer ${omniroute_password}' -H 'Content-Type: application/json' --data-raw '${body}' ${omniroute_base}/v1/chat/completions" \
        "$body" "$expectation" -- \
        curl --silent --show-error --max-time "$timeout_seconds" \
        -H "Authorization: Bearer ${omniroute_password}" \
        -H "Content-Type: application/json" \
        --data-raw "$body" "${omniroute_base}/v1/chat/completions"

    content=""
    normalized=""
    failure_stage=""
    failure_detail=""
    omniroute_parse_stderr="$(mktemp "${diagnostic_dir}/parse.XXXXXX")"
    if [ "$REQUEST_CURL_STATUS" -ne 0 ]; then
        failure_stage="curl failure"
        failure_detail="exit=${REQUEST_CURL_STATUS}"
    elif [[ ! "$REQUEST_HTTP_STATUS" =~ ^2[0-9][0-9]$ ]]; then
        failure_stage="HTTP response"
        failure_detail="status=${REQUEST_HTTP_STATUS}"
    elif ! jq . "$REQUEST_BODY_FILE" >/dev/null 2>"$omniroute_parse_stderr"; then
        failure_stage="invalid JSON"
    else
        content="$(jq -r '.choices?[0]?.message?.content? // empty' \
            < "$REQUEST_BODY_FILE" 2>>"$omniroute_parse_stderr")"
        normalized="$(printf '%s' "$content" | tr '[:upper:]' '[:lower:]' | \
            sed -E 's/^[[:space:][:punct:]]+//; s/[[:space:][:punct:]]+$//')"
        if [ -z "$content" ]; then
            failure_stage="missing assistant content"
        elif [ "$normalized" != ready ]; then
            failure_stage="normalized-value mismatch"
            failure_detail="${alias}: expected ready, got $(printf '%.80s' "$content")"
        fi
    fi

    log_block "Assistant content" "$content"
    log_block "Normalized content" "$normalized"
    log_nonempty_block "Response parsing stderr" "$(<"$omniroute_parse_stderr")"
    log_block "Expectation" "$REQUEST_EXPECTATION"
    if [ -n "$failure_stage" ]; then
        close_diagnostic_capture 1
        bad "Verdict: FAIL stage=${failure_stage} identity=${identity} ${failure_detail}"
        continue
    fi

    close_diagnostic_capture 0
    ok "Verdict: PASS identity=${identity} ${alias}: returned ready via OmniRoute"
done < <(yq -r '.models[] | select(.enabled) | [.alias, .client_max_output_tokens, (.check_timeout_seconds // 120)] | @tsv' "$CONFIG_PATH")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_shell.py -k check_server -v`
Expected: PASS, all 3 new tests.

- [ ] **Step 6: Commit**

```bash
git add scripts/check-server.sh tests/test_shell.py
git commit -m "fix(check-server): read max_tokens/timeout per model instead of hardcoding 256/120"
```

---

### Task 5: `check-setup.sh` — presets-sourced offload flags + config-aware ctx/timeout

**Files:**
- Modify: `scripts/check-setup.sh:83-201`
- Test: `tests/test_shell.py:2702-2874` (extend `run_check_setup_with_stubs` and its consuming tests)

**Interfaces:**
- Consumes: `render_presets_file(device, output)`, `presets_value(file, alias, key)` from Task 3.
- Produces: `inference_command(file, device, layers, n_cpu_moe, ctx_size, timeout_seconds)` (signature changed from the old 3-arg form) and `record_inferences(device, skip_reason, presets_file)` (3rd positional argument added).

- [ ] **Step 1: Write the failing test**

Extend `run_check_setup_with_stubs` in `tests/test_shell.py` so its `uv` stub also answers `presets` calls, and its config includes `check_ctx_size`/`n_cpu_moe`/`check_timeout_seconds` on one model. Replace the `uv` stub body (lines 2746-2763) with:

```python
    uv = commands / "uv"
    uv.write_text(
        "#!/usr/bin/bash\n"
        "printf 'uv %s\\n' \"$*\" >> \"$CALLS\"\n"
        "case \"$*\" in\n"
        "  *' models list'*) printf '%s\\n' '{\"models\":[]}' ;;\n"
        "  *' detect'*) printf '%s\\n' \"$DETECTED_GPU\" ;;\n"
        "  *' validate-gguf'*) printf '%s\\n' '{\"results\":[]}' ;;\n"
        "  *' budget '*) printf '%s\\n' '{\"available_mib\":12000,\"required_mib\":10000,\"models_max\":2}'; exit \"$BUDGET_EXIT\" ;;\n"
        "  *' resolve-device'*)\n"
        "    for argument in \"$@\"; do\n"
        "      if [ \"$previous\" = --listing-file ]; then listing_file=\"$argument\"; fi\n"
        "      previous=\"$argument\"\n"
        "    done\n"
        "    [ \"$(cat \"$listing_file\")\" = 'Vulkan7: Selected Radeon (16384 MiB, 16000 MiB free)' ] || exit 65\n"
        "    printf '%s\\n' '{\"device\":\"Vulkan7\"}'; exit \"$RESOLVE_EXIT\" ;;\n"
        "  *' presets '*)\n"
        "    for argument in \"$@\"; do\n"
        "      if [ \"$previous\" = --output ]; then output=\"$argument\"; fi\n"
        "      previous=\"$argument\"\n"
        "    done\n"
        "    printf '%s\\n' \\\n"
        "      '[first]' 'ctx-size = 8192' 'n-gpu-layers = 42' '' \\\n"
        "      '[second]' 'ctx-size = 4096' 'n-gpu-layers = 17' 'n-cpu-moe = 12' \\\n"
        "      > \"$output\"\n"
        "    exit \"$PRESETS_EXIT\" ;;\n"
        "esac\n"
    )
    uv.chmod(uv.stat().st_mode | stat.S_IXUSR)
```

Add `"PRESETS_EXIT": str(presets_exit),` to the `environment` dict, and add a `presets_exit: int = 0` keyword parameter to `run_check_setup_with_stubs`'s signature.

Change the config written in the same fixture (around line 2790-2803) to:

```python
    config = tmp_path / "models.yml"
    config.write_text(
        "server:\n"
        f"  api_key: {api_key}\n"
        "gpu:\n"
        "  image: example.invalid/llama:latest\n"
        "  pci_address: 0000:03:00.0\n"
        "  device_name: Selected Radeon\n"
        "runtime:\n"
        "  models_max: 2\n"
        "models:\n"
        "  - alias: first\n"
        "    enabled: true\n"
        "    file: first.gguf\n"
        "    n_gpu_layers: 42\n"
        "    ctx_size: 8192\n"
        "  - alias: skipped\n"
        "    enabled: false\n"
        "    file: skipped.gguf\n"
        "    n_gpu_layers: 99\n"
        "  - alias: second\n"
        "    enabled: true\n"
        "    file: second.gguf\n"
        "    n_gpu_layers: 17\n"
        "    ctx_size: 4096\n"
        "    check_ctx_size: 2048\n"
        "    check_timeout_seconds: 300\n"
        "    n_cpu_moe: 12\n"
    )
```

Then replace `test_check_setup_runs_disposable_inference_for_each_enabled_model`'s body (the assertions) with:

```python
def test_check_setup_runs_disposable_inference_for_each_enabled_model(
    tmp_path: pathlib.Path,
) -> None:
    """Offline setup validation must resolve and smoke-test every enabled
    model using presets.ini's own n-gpu-layers/n-cpu-moe (Task 3's shared
    source of truth) and each model's check_ctx_size/check_timeout_seconds
    (falling back to ctx_size/180s)."""
    result, calls, models_dir = run_check_setup_with_stubs(tmp_path)

    assert result.returncode == 0, result.stderr
    recorded = calls.read_text()
    check_setup = (ROOT / "scripts/check-setup.sh").read_text()
    assert 'uv run "${REPO_DIR}/llmenv.py" resolve-device' in check_setup
    assert (
        f"uv run {ROOT / 'llmenv.py'} resolve-device --device-name Selected Radeon "
        "--listing-file "
    ) in recorded
    list_devices = (
        "podman run --rm --device /dev/dri "
        "example.invalid/llama:latest --list-devices"
    )
    # "first" has no check_ctx_size override (falls back to its real
    # ctx_size 8192) and no check_timeout_seconds override (falls back to
    # the script's 180s baseline), and presets.ini has no n-cpu-moe for it
    # (dense model) -- so no --n-cpu-moe flag appears.
    first_inference = (
        f"podman run --rm --device /dev/dri -v {models_dir}:/models:ro,z "
        "--entrypoint /app/llama example.invalid/llama:latest cli -m /models/first.gguf "
        "--device Vulkan7 --n-gpu-layers 42 --ctx-size 8192 --single-turn --no-show-timings "
        "-p Reply with exactly: ready -n 256"
    )
    # "second" has an explicit check_ctx_size=2048 override, an explicit
    # check_timeout_seconds=300 override, and presets.ini reports
    # n-cpu-moe=12 for it (MoE model) -- all three must appear.
    second_inference = (
        f"podman run --rm --device /dev/dri -v {models_dir}:/models:ro,z "
        "--entrypoint /app/llama example.invalid/llama:latest cli -m /models/second.gguf "
        "--device Vulkan7 --n-gpu-layers 17 --n-cpu-moe 12 --ctx-size 2048 "
        "--single-turn --no-show-timings -p Reply with exactly: ready -n 256"
    )
    assert recorded.count(list_devices) == 1
    assert recorded.count(f"timeout 180 {first_inference}") == 1
    assert recorded.count(f"timeout 300 {second_inference}") == 1
    assert "/models/skipped.gguf" not in recorded
    inference_calls = [
        line.split()
        for line in recorded.splitlines()
        if line.startswith("podman run") and " cli " in line
    ]
    assert len(inference_calls) == 2
    for arguments in inference_calls:
        assert "--publish" not in arguments
        assert "-p" not in arguments[: arguments.index("example.invalid/llama:latest")]
    assert "podman exec" not in recorded
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_shell.py -k test_check_setup_runs_disposable_inference_for_each_enabled_model -v`
Expected: FAIL — current script never calls `presets`, never adds `--n-cpu-moe`/`--ctx-size`, and hardcodes `timeout 180` for every model regardless of `check_timeout_seconds`.

- [ ] **Step 3: Implement**

In `scripts/check-setup.sh`, replace the `inference_command()` function (lines 83-87) with:

```bash
inference_command() {
    local file="$1" device="$2" layers="$3" n_cpu_moe="$4" ctx_size="$5" timeout_seconds="$6"
    local moe_part=""
    [ -n "$n_cpu_moe" ] && moe_part="--n-cpu-moe ${n_cpu_moe} "
    printf 'timeout %q podman run --rm --device /dev/dri -v %q:/models:ro,z --entrypoint /app/llama %q cli -m %q --device %q --n-gpu-layers %q %s--ctx-size %q --single-turn --no-show-timings -p %q -n 256' \
        "$timeout_seconds" "$MODELS_DIR" "$image" "/models/${file}" "$device" "$layers" "$moe_part" "$ctx_size" "Reply with exactly: ready"
}
```

Replace `record_inferences()` (lines 89-108) with:

```bash
record_inferences() {
    local device="$1" skip_reason="${2:-}" presets_file="${3:-}"
    local alias file ctx_size timeout_seconds layers n_cpu_moe command_text moe_args

    log_step "Offline inference"
    while IFS=$'\t' read -r alias file ctx_size timeout_seconds; do
        layers=""
        n_cpu_moe=""
        if [ -n "$presets_file" ]; then
            layers="$(presets_value "$presets_file" "$alias" "n-gpu-layers")"
            n_cpu_moe="$(presets_value "$presets_file" "$alias" "n-cpu-moe")"
        fi
        if [ -z "$layers" ]; then
            layers="$(yq -r --arg a "$alias" '.models[] | select(.alias == $a) | .n_gpu_layers' "$CONFIG_PATH")"
        fi
        command_text="$(inference_command "$file" "$device" "$layers" "$n_cpu_moe" "$ctx_size" "$timeout_seconds")"
        if [ -n "$skip_reason" ]; then
            record_inference_skip "$alias" "$command_text" "$skip_reason"
        else
            moe_args=()
            [ -n "$n_cpu_moe" ] && moe_args=(--n-cpu-moe "$n_cpu_moe")
            record_command "inference ${alias}" "$command_text" "Reply with exactly: ready" \
                "normalized assistant content: ready" "ready" 1 "Inference stdout" "Inference stderr" \
                timeout "$timeout_seconds" podman run --rm --device /dev/dri \
                -v "${MODELS_DIR}:/models:ro,z" \
                --entrypoint /app/llama "$image" cli \
                -m "/models/${file}" --device "$device" \
                --n-gpu-layers "$layers" "${moe_args[@]}" --ctx-size "$ctx_size" \
                --single-turn --no-show-timings \
                -p "Reply with exactly: ready" -n 256 || true
        fi
    done < <(yq -r '.models[] | select(.enabled) | [.alias, .file, (.check_ctx_size // .ctx_size), (.check_timeout_seconds // 180)] | @tsv' "$CONFIG_PATH")
}
```

Replace the VRAM-budget block that calls `record_inferences` (lines 173-201) with:

```bash
log_step "VRAM budget"
record_command "VRAM budget" \
    "uv run ${REPO_DIR}/llmenv.py --config ${CONFIG_PATH} budget --models-dir ${MODELS_DIR}" "" \
    "exit status: 0" "" 0 "Command stdout" "Command stderr" \
    uv run "${REPO_DIR}/llmenv.py" --config "$CONFIG_PATH" budget --models-dir "$MODELS_DIR"
budget_status=$?
if [ "$budget_status" -ne 0 ]; then
    record_inferences "" "VRAM budget check failed" ""
else
    device_name="$(yq -r '.gpu.device_name' "$CONFIG_PATH" 2>/dev/null || true)"
    record_command "GPU device listing" \
        "podman run --rm --device /dev/dri ${image} --list-devices" "" \
        "exit status: 0" "" 0 "Command stdout" "Command stderr" \
        podman run --rm --device /dev/dri "$image" --list-devices
    listing_status=$?
    listing_file="$record_stdout_file"
    record_command "GPU device resolution" \
        "uv run ${REPO_DIR}/llmenv.py resolve-device --device-name ${device_name} --listing-file ${listing_file}" \
        "device name: ${device_name}" "exit status: 0" "" 0 "Command stdout" "Command stderr" \
        uv run "${REPO_DIR}/llmenv.py" resolve-device --device-name "$device_name" --listing-file "$listing_file"
    resolve_status=$?
    resolved="$(<"$record_stdout_file")"
    device="$(jq -r '.device // empty' <<<"$resolved")"
    if [ "$listing_status" -eq 0 ] && [ "$resolve_status" -eq 0 ] && [ -n "$device" ]; then
        presets_file="$(mktemp "${diagnostic_dir}/presets.XXXXXX")"
        if render_presets_file "$device" "$presets_file"; then
            record_inferences "$device" "" "$presets_file"
        else
            record_inferences "$device" "presets rendering failed" ""
        fi
    else
        record_inferences "" "GPU device could not be resolved" ""
    fi
fi
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_shell.py -k check_setup -v`
Expected: PASS on all `check-setup` tests, including `test_check_setup_never_requires_the_rocm_kernel_device`, `test_check_setup_prints_complete_static_and_inference_records`, `test_check_setup_prints_concise_pass_rows_by_default`, `test_check_setup_skips_unresolved_gpu_render_node_without_parsed_result`, `test_check_setup_accepts_ready_after_visible_reasoning`, `test_check_setup_ignores_llama_exit_footer_after_ready`. If any of these hardcode the old 3-field TSV/command shape, update them the same way as Step 1 (add `ctx_size`/`check_ctx_size`/`check_timeout_seconds` to their fixture configs and expected command strings) before re-running.

- [ ] **Step 5: Commit**

```bash
git add scripts/check-setup.sh tests/test_shell.py
git commit -m "fix(check-setup): source n-cpu-moe/n-gpu-layers from presets.ini, ctx/timeout from config"
```

---

### Task 6: `benchmark.sh` — per-model, device-pinned throughput

**Files:**
- Modify: `scripts/benchmark.sh` (full rewrite of the post-probe section, lines 57-115)
- Test: `tests/test_shell.py:1804-1911` (extend `run_benchmark` and its consuming tests)

**Interfaces:**
- Consumes: `render_presets_file`, `presets_value` (Task 3); `llmenv resolve-device`/`list-devices` (existing, same pattern `check-setup.sh` already uses).
- Produces: no new shared interfaces. Writes results to `.models[<alias>].benchmark.vulkan.{pp_tps,tg_tps,measured_at}` per enabled model instead of the retired `.gpu.benchmark.vulkan`.

**Design note carried into this task:** `benchmark.sh` has two genuinely different jobs that must not be conflated: (1) a one-time Vulkan-vs-CPU backend probe + `gpu.device_name` resolution, using one representative (smallest) model purely to answer "does Vulkan work on this hardware" — this stays as today, since it's a hardware capability check, not a throughput measurement of the deployed config; (2) a per-model, production-config-matching throughput measurement, which is the part that must become model-aware and device-pinned. Task 6 adds (2) after (1) succeeds, without changing (1)'s existing behavior or its `gpu.backend`/`gpu.image`/`gpu.device_name` side effects.

- [ ] **Step 1: Write the failing tests**

Extend `run_benchmark` in `tests/test_shell.py` (lines 1804-1872): add a second enabled model to the config, make the `uv` stub answer `resolve-device` and `presets`, and let the `podman` stub answer per-model `bench` calls with per-model JSON via an env var keyed by model filename. Replace the fixture with:

```python
def run_benchmark(
    tmp_path: pathlib.Path,
    probe_stdout: str,
    probe_stderr: str = "",
    *,
    resolve_exit: int = 0,
    model_bench_stdout: dict[str, str] | None = None,
) -> tuple[subprocess.CompletedProcess[str], pathlib.Path, pathlib.Path]:
    """Run the benchmark with controlled Vulkan streams and record Podman calls.

    model_bench_stdout maps a model's .gguf filename to the JSON its
    per-model bench call should return; the probe (smallest model) always
    uses probe_stdout/probe_stderr regardless of this map.
    """
    real_yq = shutil.which("yq")
    assert real_yq is not None
    model_bench_stdout = model_bench_stdout or {}

    commands = tmp_path / "bin"
    commands.mkdir()
    calls = tmp_path / "calls"
    calls.touch()
    config = tmp_path / "models.yml"
    config.write_text(
        "gpu:\n"
        "  backend: vulkan\n"
        "  image: ghcr.io/ggml-org/llama.cpp:server-vulkan\n"
        "  pci_address: '0000:03:00.0'\n"
        "  vram_total_mib: 16384\n"
        "models:\n"
        "  - alias: smallest\n"
        "    enabled: true\n"
        "    file: smallest.gguf\n"
        "    size_bytes: 1\n"
        "  - alias: biggest\n"
        "    enabled: true\n"
        "    file: biggest.gguf\n"
        "    size_bytes: 2\n"
    )

    uv = commands / "uv"
    uv.write_text(
        "#!/usr/bin/bash\n"
        "printf 'uv %s\\n' \"$*\" >> \"$CALLS\"\n"
        "case \"$*\" in\n"
        "  *' resolve-device'*) printf '%s\\n' '{\"device\":\"Vulkan0\"}'; exit \"$RESOLVE_EXIT\" ;;\n"
        "  *' presets '*)\n"
        "    for argument in \"$@\"; do\n"
        "      if [ \"$previous\" = --output ]; then output=\"$argument\"; fi\n"
        "      previous=\"$argument\"\n"
        "    done\n"
        "    printf '%s\\n' \\\n"
        "      '[smallest]' 'n-gpu-layers = 99' '' \\\n"
        "      '[biggest]' 'n-gpu-layers = 40' 'n-cpu-moe = 12' \\\n"
        "      > \"$output\" ;;\n"
        "esac\n"
    )
    uv.chmod(uv.stat().st_mode | stat.S_IXUSR)

    yq = commands / "yq"
    yq.write_text("#!/usr/bin/bash\nexec \"$REAL_YQ\" \"$@\"\n")
    yq.chmod(yq.stat().st_mode | stat.S_IXUSR)

    bench_map_file = tmp_path / "bench_map.env"
    bench_map_file.write_text(
        "\n".join(f"{name}={json}" for name, json in model_bench_stdout.items())
    )

    podman = commands / "podman"
    podman.write_text(
        "#!/usr/bin/bash\n"
        "printf 'podman %s\\n' \"$*\" >> \"$CALLS\"\n"
        "case \"$*\" in\n"
        "  *'help all'*) printf '%s\\n' bench ;;\n"
        "  *'/models/smallest.gguf'*' bench '*|*' bench '*'-m /models/smallest.gguf'*)\n"
        "    printf '%s' \"$PROBE_STDOUT\"; printf '%s' \"$PROBE_STDERR\" >&2 ;;\n"
        "  *' bench '*'/models/biggest.gguf'*)\n"
        "    line=\"$(grep '^biggest\\.gguf=' \"$BENCH_MAP_FILE\" || true)\"\n"
        "    printf '%s' \"${line#*=}\" ;;\n"
        "  *'--list-devices'*) printf '%s\\n' 'Vulkan0: Benchmark GPU (16384 MiB, 16000 MiB free)' ;;\n"
        "esac\n"
    )
    podman.chmod(podman.stat().st_mode | stat.S_IXUSR)

    environment = os.environ | {
        "CALLS": str(calls),
        "HOME": str(tmp_path / "home"),
        "LLM_ENV_CONFIG": str(config),
        "LLM_ENV_MODELS_DIR": str(tmp_path / "models"),
        "PATH": f"{commands}:/usr/bin:/bin",
        "REAL_YQ": real_yq,
        "PROBE_STDOUT": probe_stdout,
        "PROBE_STDERR": probe_stderr,
        "RESOLVE_EXIT": str(resolve_exit),
        "BENCH_MAP_FILE": str(bench_map_file),
    }
    result = subprocess.run(
        ["/usr/bin/bash", "scripts/benchmark.sh"],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    return result, calls, config
```

Then replace the two existing consuming tests' bodies to match the renamed "probe" terminology and add the new per-model coverage:

```python
def test_benchmark_parses_valid_probe_stdout_despite_vulkan_stderr_warning(
    tmp_path: pathlib.Path,
) -> None:
    """Vulkan warnings must not corrupt an otherwise valid probe JSON."""
    result, calls, config = run_benchmark(
        tmp_path,
        '[{"n_prompt":512,"avg_ts":123.4},{"n_gen":128,"avg_ts":56.7}]',
        "WARNING: radv is not a conformant Vulkan implementation\n",
        model_bench_stdout={
            "biggest.gguf": '[{"n_prompt":512,"avg_ts":90.0},{"n_gen":128,"avg_ts":30.0}]',
        },
    )

    assert result.returncode == 0, result.stderr
    assert "Probe stdout:" in result.stdout
    assert '"avg_ts":123.4' in result.stdout
    assert "Probe stderr:\n  WARNING: radv" in result.stdout
    assert yq_value(config, ".gpu.backend") == "vulkan"
    assert yq_value(config, ".gpu.image") == "ghcr.io/ggml-org/llama.cpp:server-vulkan"
    assert "podman pull ghcr.io/ggml-org/llama.cpp:server" not in calls.read_text()


def test_benchmark_configures_cpu_but_fails_when_probe_stdout_is_invalid(
    tmp_path: pathlib.Path,
) -> None:
    """An invalid Vulkan probe response must configure CPU and fail the benchmark."""
    result, calls, config = run_benchmark(tmp_path, "not benchmark JSON\n")

    assert result.returncode != 0
    assert "Probe stdout:\n  not benchmark JSON" in result.stdout
    assert "Probe parser stderr:" in result.stdout
    assert "parse error:" in result.stdout
    assert "Vulkan probe failure: response parsing" in result.stderr
    assert yq_value(config, ".gpu.backend") == "cpu"
    assert yq_value(config, ".gpu.image") == "ghcr.io/ggml-org/llama.cpp:server"
    assert "podman pull ghcr.io/ggml-org/llama.cpp:server" in calls.read_text()


def test_benchmark_measures_every_enabled_model_pinned_to_the_resolved_device(
    tmp_path: pathlib.Path,
) -> None:
    """Every enabled model gets its own device-pinned, n-cpu-moe-aware
    throughput measurement, written to its own .models[].benchmark.vulkan
    -- this is the direct fix for the real acceptance-run bug where a
    single un-pinned bench call auto-spread across both GPUs."""
    result, calls, config = run_benchmark(
        tmp_path,
        '[{"n_prompt":512,"avg_ts":123.4},{"n_gen":128,"avg_ts":56.7}]',
        model_bench_stdout={
            "biggest.gguf": '[{"n_prompt":512,"avg_ts":90.0},{"n_gen":128,"avg_ts":30.0}]',
        },
    )

    assert result.returncode == 0, result.stderr
    recorded = calls.read_text()
    assert "uv run" in recorded and "resolve-device" in recorded
    biggest_call = [
        line for line in recorded.splitlines()
        if line.startswith("podman run") and "/models/biggest.gguf" in line and " bench " in line
    ]
    assert len(biggest_call) == 1, recorded
    assert "--device Vulkan0" in biggest_call[0]
    assert "--n-cpu-moe 12" in biggest_call[0]
    assert yq_value(config, '(.models[] | select(.alias == "biggest") | .benchmark.vulkan.pp_tps)') == "90"
    assert yq_value(config, '(.models[] | select(.alias == "biggest") | .benchmark.vulkan.tg_tps)') == "30"
    assert yq_value(config, '(.models[] | select(.alias == "biggest") | .benchmark.vulkan.measured_at)') != "null"
    assert yq_value(config, '(.models[] | select(.alias == "smallest") | .benchmark.vulkan.pp_tps)') == "123.4"
    assert yq_value(config, ".gpu.benchmark") == "null"
```

(`yq_value` is the existing helper already used throughout `test_shell.py` for reading back config values — confirm its exact call signature with `grep -n "^def yq_value" tests/test_shell.py` before using it; adapt the calls above to match if it takes the config path first or as a keyword.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_shell.py -k benchmark -v`
Expected: the two renamed tests FAIL (old code still writes "Benchmark stdout:"/"Vulkan benchmark failure" text, not "Probe..."); the new per-model test FAILs (`biggest_call` is empty — today's script never benches the second model).

- [ ] **Step 3: Implement**

Replace `scripts/benchmark.sh` in full with:

```bash
#!/usr/bin/env bash
# benchmark.sh — probe Vulkan-vs-CPU once, then measure every enabled
# model's own throughput pinned to the resolved GPU device.
set -euo pipefail
# shellcheck disable=SC1091 # Resolved from this script at runtime.
# shellcheck source=../tools/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/../tools/lib.sh"

require_cmd uv jq yq podman awk

probe_model="$(yq -r '[.models[] | select(.enabled)] | sort_by(.size_bytes) | .[0].file' "$CONFIG_PATH")"
[ -n "$probe_model" ] && [ "$probe_model" != "null" ] || die "no enabled models to benchmark"

parse_bench_json() {
    local stdout_file="$1" parser_stderr_file="$2"
    jq -ce '
        [(.[] | select(.n_prompt > 0) | .avg_ts),
         (.[] | select(.n_gen > 0) | .avg_ts)]
        | select(length == 2 and all(.[]; type == "number"))
        | {pp_tps: .[0], tg_tps: .[1]}
    ' "$stdout_file" 2>"$parser_stderr_file"
}

run_vulkan_probe() {
    local stdout_file="$1" stderr_file="$2" parser_stderr_file="$3" status result

    log_command "podman run --rm --device /dev/dri -v ${MODELS_DIR}:/models:ro,z --entrypoint /app/llama ${VULKAN_IMAGE} bench -m /models/${probe_model} -p 512 -n 128 -r 2 -o json"
    if podman run --rm --device /dev/dri \
        -v "${MODELS_DIR}:/models:ro,z" \
        --entrypoint /app/llama \
        "$VULKAN_IMAGE" bench -m "/models/${probe_model}" -p 512 -n 128 -r 2 -o json \
        >"$stdout_file" 2>"$stderr_file"; then
        status=0
    else
        status=$?
    fi
    log_block "Probe stdout" "$(<"$stdout_file")"
    log_nonempty_block "Probe stderr" "$(<"$stderr_file")"
    log_block "Exit status" "$status"
    if [ "$status" -ne 0 ]; then
        log_nonempty_block "Probe parser stderr" "$(<"$parser_stderr_file")"
        log_error "Vulkan probe failure: command exit ${status}"
        return 1
    fi
    if ! result="$(parse_bench_json "$stdout_file" "$parser_stderr_file")"; then
        log_nonempty_block "Probe parser stderr" "$(<"$parser_stderr_file")"
        log_error "Vulkan probe failure: response parsing"
        return 1
    fi
    log_nonempty_block "Probe parser stderr" "$(<"$parser_stderr_file")"
    log_block "Parsed metrics" "$result"
    PROBE_RESULT="$result"
}

log_step "Vulkan probe"
log_info "probe model: ${probe_model}"
diagnostic_dir="$(prepare_diagnostic_dir benchmark)"
trap 'status=$?; finish_diagnostic_dir "$diagnostic_dir"; exit "$status"' EXIT
vulkan_stdout="$(mktemp "${diagnostic_dir}/vulkan-probe-stdout.XXXXXX")" \
    || die "could not create Vulkan probe stdout diagnostic"
vulkan_stderr="$(mktemp "${diagnostic_dir}/vulkan-probe-stderr.XXXXXX")" \
    || die "could not create Vulkan probe stderr diagnostic"
vulkan_parser_stderr="$(mktemp "${diagnostic_dir}/vulkan-probe-parser-stderr.XXXXXX")" \
    || die "could not create Vulkan probe parser diagnostic"

winner_backend="cpu"
winner_image="$CPU_IMAGE"
PROBE_RESULT=""
probe_status=1
if run_vulkan_probe "$vulkan_stdout" "$vulkan_stderr" "$vulkan_parser_stderr" && [ -n "$PROBE_RESULT" ]; then
    winner_backend="vulkan"
    winner_image="$VULKAN_IMAGE"
    probe_status=0
    log_info "Vulkan: $(jq -r '.pp_tps' <<<"$PROBE_RESULT") tok/s prompt, $(jq -r '.tg_tps' <<<"$PROBE_RESULT") tok/s generation (probe model)"
else
    log_warn "Vulkan probe failed; falling back to CPU. Expect very slow inference."
    podman pull "$winner_image" >/dev/null || die "cannot pull the CPU image"
fi

log_step "Resolving the GPU device name"
listing_file="$(mktemp)"
podman run --rm --device /dev/dri --entrypoint /app/llama-server \
    "$winner_image" --list-devices >"$listing_file" 2>/dev/null || true
log_info "device listing:"
sed 's/^/    /' "$listing_file" || true

vram="$(yq -r '.gpu.vram_total_mib' "$CONFIG_PATH")"
device_name="$(awk -v want="$vram" '
    match($0, /^[[:space:]]*[^:]+:[[:space:]]+/) {
        rest = substr($0, RLENGTH + 1)
        if (match(rest, /[[:space:]]+\([0-9]+[[:space:]]*MiB/)) {
            name = substr(rest, 1, RSTART - 1)
            mib  = rest
            sub(/^.*\(/, "", mib); sub(/[[:space:]]*MiB.*$/, "", mib)
            if (mib + 0 == want + 0) { print name; exit }
        }
    }' "$listing_file")"

if [ -n "$device_name" ]; then
    yq -i ".gpu.device_name = \"${device_name}\"" "$CONFIG_PATH"
    log_info "device name recorded: ${device_name} (pci $(yq -r '.gpu.pci_address' "$CONFIG_PATH"))"
else
    log_warn "could not match a device with ${vram} MiB; start.sh will offload to all devices"
fi

yq -i ".gpu.backend = \"${winner_backend}\"" "$CONFIG_PATH"
yq -i ".gpu.image = \"${winner_image}\"" "$CONFIG_PATH"
log_info "backend set to ${winner_backend} (${winner_image})"

per_model_status=0
if [ "$winner_backend" = vulkan ] && [ -n "$device_name" ]; then
    log_step "Resolving Vulkan device index"
    device="$(uv run "${REPO_DIR}/llmenv.py" resolve-device --device-name "$device_name" --listing-file "$listing_file" | jq -r '.device // empty')"
    if [ -z "$device" ]; then
        log_warn "could not resolve a Vulkan device index for ${device_name}; skipping per-model benchmarks"
    else
        presets_file="$(mktemp "${diagnostic_dir}/presets.XXXXXX")"
        if render_presets_file "$device" "$presets_file"; then
            log_step "Per-model Vulkan benchmark"
            while IFS=$'\t' read -r alias file; do
                [ -n "$alias" ] || continue
                n_cpu_moe="$(presets_value "$presets_file" "$alias" "n-cpu-moe")"
                moe_args=()
                [ -n "$n_cpu_moe" ] && moe_args=(--n-cpu-moe "$n_cpu_moe")

                model_stdout="$(mktemp "${diagnostic_dir}/${alias}-bench-stdout.XXXXXX")"
                model_stderr="$(mktemp "${diagnostic_dir}/${alias}-bench-stderr.XXXXXX")"
                model_parser_stderr="$(mktemp "${diagnostic_dir}/${alias}-bench-parser-stderr.XXXXXX")"

                log_command "podman run --rm --device /dev/dri -v ${MODELS_DIR}:/models:ro,z --entrypoint /app/llama ${VULKAN_IMAGE} bench -m /models/${file} --device ${device} ${moe_args[*]} -p 512 -n 128 -r 2 -o json"
                if podman run --rm --device /dev/dri \
                    -v "${MODELS_DIR}:/models:ro,z" \
                    --entrypoint /app/llama \
                    "$VULKAN_IMAGE" bench -m "/models/${file}" --device "$device" "${moe_args[@]}" \
                    -p 512 -n 128 -r 2 -o json \
                    >"$model_stdout" 2>"$model_stderr"; then
                    model_status=0
                else
                    model_status=$?
                fi
                log_block "Benchmark stdout" "$(<"$model_stdout")"
                log_nonempty_block "Benchmark stderr" "$(<"$model_stderr")"
                log_block "Exit status" "$model_status"
                if [ "$model_status" -ne 0 ]; then
                    log_error "Vulkan benchmark failure for ${alias}: command exit ${model_status}"
                    per_model_status=1
                    continue
                fi
                if ! model_result="$(parse_bench_json "$model_stdout" "$model_parser_stderr")"; then
                    log_nonempty_block "Benchmark parser stderr" "$(<"$model_parser_stderr")"
                    log_error "Vulkan benchmark failure for ${alias}: response parsing"
                    per_model_status=1
                    continue
                fi
                pp="$(jq -er '.pp_tps' <<<"$model_result")"
                tg="$(jq -er '.tg_tps' <<<"$model_result")"
                yq -i "(.models[] | select(.alias == \"${alias}\") | .benchmark.vulkan.pp_tps) = ${pp}" "$CONFIG_PATH"
                yq -i "(.models[] | select(.alias == \"${alias}\") | .benchmark.vulkan.tg_tps) = ${tg}" "$CONFIG_PATH"
                yq -i "(.models[] | select(.alias == \"${alias}\") | .benchmark.vulkan.measured_at) = \"$(date -Iseconds)\"" "$CONFIG_PATH"
                log_info "${alias}: ${pp} tok/s prompt, ${tg} tok/s generation"
            done < <(yq -r '.models[] | select(.enabled) | [.alias, .file] | @tsv' "$CONFIG_PATH")
        else
            log_warn "could not render presets.ini for device ${device}; skipping per-model benchmarks"
        fi
        rm -f "$presets_file"
    fi
fi
rm -f "$listing_file"

exit_status="$probe_status"
[ "$per_model_status" -eq 0 ] || exit_status=1
exit "$exit_status"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_shell.py -k benchmark -v`
Expected: PASS, all 3 tests (2 renamed + 1 new).

- [ ] **Step 5: Update the Makefile help text if it names the old behavior**

Run: `grep -n "benchmark" Makefile scripts/help.sh`

If `scripts/help.sh` or the Makefile's own comments describe `benchmark` as testing "the smallest model" or similar single-model language, update that line to reflect the new per-model behavior. `test_make_help_describes_a_vulkan_only_benchmark` (existing, `tests/test_shell.py:2626`) must still pass unmodified — read it first (`sed -n '2626,2650p' tests/test_shell.py`) to confirm the exact substring it checks for isn't being changed, and only touch adjacent unrelated text.

- [ ] **Step 6: Commit**

```bash
git add scripts/benchmark.sh tests/test_shell.py Makefile scripts/help.sh
git commit -m "feat(benchmark): measure every enabled model, pinned to the resolved GPU device"
```

---

### Task 7: Empirical timeout tuning + acceptance re-run + docs

**Files:**
- Modify: `models.yml.example` (possibly add `check_timeout_seconds` to `ornith-35b` if cold-load measurement warrants it)
- Modify: `.agents/architecture.md` and/or `README.md` (wherever `gpu.benchmark`, the check scripts, or their flag derivation is documented — locate with `grep -rn "gpu.benchmark\|n_cpu_moe" .agents/architecture.md README.md` first)
- No code changes beyond what Tasks 1-6 already made; this task is measurement + config/doc updates + a real hardware re-run.

**Interfaces:** None (this task consumes everything built in Tasks 1-6; it produces no new interface).

- [ ] **Step 1: Run the full unit/shell test suite once, end to end**

Run: `uv run pytest tests/ -v`
Expected: PASS, 0 failures. This is the gate before touching real hardware.

- [ ] **Step 2: Measure real cold-load time for the largest model**

On the real hardware host (AMD RX 9070 XT, per this plan's originating acceptance run), with `ornith-35b` enabled at its full `ctx_size: 262144` and configured `n_cpu_moe: 28`, time the exact command `check-setup.sh` now builds for it end-to-end:

```bash
time timeout 600 podman run --rm --device /dev/dri \
  -v "${LLM_ENV_MODELS_DIR}:/models:ro,z" \
  --entrypoint /app/llama "$(yq -r '.gpu.image' ~/.config/llm-env/models.yml)" cli \
  -m /models/ornith-1.0-35b-Q4_K_M.gguf --device Vulkan0 \
  --n-gpu-layers 99 --n-cpu-moe 28 --ctx-size 262144 \
  --single-turn --no-show-timings -p "Reply with exactly: ready" -n 256
```

(Substitute the real resolved `--device` value from `make check-setup`'s own "GPU device resolution" diagnostic if it differs from `Vulkan0`.) Record the wall-clock time.

- [ ] **Step 3: Set the scripts' default timeout baseline from the measurement**

If the measured cold-load time (with a safety margin — double it, or add at least 120s, whichever is larger) exceeds the current script defaults (`180` in `check-setup.sh`'s `record_inferences` TSV query, `120` in both of `check-server.sh`'s TSV queries), update those two literal fallback defaults in the already-modified code from Tasks 4 and 5 to the new baseline. These are the one place per script where a literal number is allowed by this plan's Global Constraints (the explicitly-permitted script-level baseline). If the measurement is within the existing defaults, leave them unchanged and note the measurement in the commit message instead.

- [ ] **Step 4: Decide whether `ornith-35b` needs an explicit override**

If, even after Step 3's baseline update, `ornith-35b` specifically still needs more headroom than every other model (e.g. because its cold-load is an outlier, not representative of the general baseline), add `check_timeout_seconds: <value>` to its entry in `models.yml.example` (and to the live `~/.config/llm-env/models.yml` used for the acceptance re-run in Step 5) rather than inflating the shared script baseline for every model. Otherwise, skip this step — the tuned baseline from Step 3 already covers it.

- [ ] **Step 5: Re-run the full acceptance chain against real hardware**

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

with `ornith-35b` as the enabled model. Confirm:
- `check-setup`'s offline inference check now runs with `--n-cpu-moe 28 --ctx-size 262144` (visible in its diagnostic output with `LLM_ENV_CHECK_VERBOSE=1` if needed) — i.e., it is now actually exercising the `n_cpu_moe: 28` profile the earlier acceptance run silently skipped.
- `check-server`'s completion probe no longer hardcodes `max_tokens: 256` — confirm via its diagnostic output that the request body's `max_tokens` matches `client_max_output_tokens` (8192).
- `benchmark`'s per-model bench call for `ornith-35b` is pinned to a single `--device` and includes `--n-cpu-moe 28` — confirm via its diagnostic output there is exactly one Vulkan device listed in the bench command's own stderr (no more "Found 2 Vulkan devices" auto-spread), and that `.models[] | select(.alias == "ornith-35b") | .benchmark.vulkan` in `~/.config/llm-env/models.yml` is populated.
- All `make` targets exit 0 except any pre-existing, out-of-scope failure (the Pi CLI's broken local install, confirmed unrelated in the original research).

If anything fails, treat it as a new bug: capture the diagnostic output (`LLM_ENV_KEEP_CHECK_ARTIFACTS=1`), fix the specific script involved (revisit the relevant Task 3-6), and re-run this step — do not proceed to Step 6 with a red acceptance run.

- [ ] **Step 6: Update documentation**

In `.agents/architecture.md` and/or `README.md` wherever `gpu.benchmark` or the check scripts' flag derivation is described, update the text to describe: (a) per-model `benchmark.vulkan` storage instead of the shared `gpu.benchmark` key, (b) `check-setup.sh`/`benchmark.sh` reading `n-gpu-layers`/`n-cpu-moe` from `llmenv presets`'s own output rather than re-deriving them, (c) the two new optional per-model fields `check_ctx_size`/`check_timeout_seconds` and their production-parity defaults. Keep edits scoped to the sections that already discuss this flow — do not add new unrelated sections.

- [ ] **Step 7: Commit**

```bash
git add models.yml.example .agents/architecture.md README.md scripts/check-setup.sh scripts/check-server.sh
git commit -m "docs+tune: set check timeout baseline from measured ornith-35b cold-load, document per-model benchmark storage"
```

(Omit any path from the `git add` that Step 3/4/6 didn't actually touch.)

---

## Self-Review Notes

- **Spec coverage:** All 7 design components are covered — Component 1 (Task 1), Component 2 (Task 3), Component 3 (Task 4), Component 4 (Tasks 4 & 5), Component 5 (Task 6), Component 6 (Task 2), Component 7 (Task 7).
- **Placeholder scan:** No TBD/TODO markers; every step includes literal code, exact commands, or a fully specified manual verification procedure (Task 7, which is inherently a real-hardware measurement step that cannot be scripted in advance).
- **Type consistency:** `inference_command()`'s signature (`file, device, layers, n_cpu_moe, ctx_size, timeout_seconds`) and `record_inferences()`'s signature (`device, skip_reason, presets_file`) are used identically at both their definition (Task 5, Step 3) and call sites (Task 5, Step 3's VRAM-budget block). `render_presets_file(device, output)` and `presets_value(file, alias, key)` (Task 3) are called with matching argument order and count in both Task 5 and Task 6.
- **Ordering dependency:** Task 3 (shared `tools/lib.sh` helpers) must land before Tasks 5 and 6, which consume it — reflected in file/task ordering above. Tasks 1 and 2 (schema) have no code dependency on Task 3 and can run first or in parallel with it.
