# Model-Specific Sampler Configuration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add validated per-model sampler settings, qualify the agentic Gemma replacement with the publisher's two supported profiles, and make the first qualifying profile the clean-setup default.

**Architecture:** `pylib/config.py` validates an optional `sampling` mapping without migrating existing records, and `pylib/presets.py` renders configured values only in the owning model's llama.cpp preset section. Repository tests establish schema, diagnostic, persistence, and rendering behavior before a private live experiment proves client requests inherit the preset and selects one literal profile for `models.yml.example`.

**Tech Stack:** YAML, Python 3.12, pytest, configparser, Bash, jq, Mike Farah yq v4, Node.js, Pi, OpenCode, llama.cpp Vulkan router, rootless Podman, systemd user services.

## Global Constraints

- `sampling` is optional per model; an absent mapping and `sampling: {}` emit no sampler directives and preserve llama.cpp defaults.
- Supported fields are exactly `temperature`, `top_p`, `top_k`, and `repeat_penalty`; unknown sampling keys are rejected.
- `temperature` is finite and non-negative; `top_p` is finite and within `0` through `1`; `top_k` is a non-negative non-Boolean integer; `repeat_penalty` is finite and greater than `0`.
- Integers are accepted for numeric fields, while Boolean values are rejected.
- Sampler keys render only in the selected model's section, never in `[*]`; disabled models receive no section.
- Do not add migration defaults, global sampler defaults, client-specific sampler settings, MTP, CPU offload, automatic fitting, cache changes, context reduction, or changes to `scripts/check-with-agents.sh`.
- Preserve alias `gemma4`, `ctx_size: 131072`, `client_max_output_tokens: 8192`, `n_gpu_layers: 99`, one slot/model, Q5_1 caches, flash attention, disabled fitting, and disabled context shifting.
- The live candidates are fixed in order: publisher profile `1.0 / 0.95 / 64 / 1.1`, then greedy profile `0.0 / 0.95 / 64 / 1.1`.
- Select the first candidate in that order that passes screening and three consecutive valid four-cell qualification rounds.
- Required machine identities are `client=pi|opencode model=gemma4 check=weather|fx`, exactly once each per valid round.
- Invalid infrastructure runs do not advance or reset a streak; a valid model-behavior failure rejects the candidate.
- Never print or retain API keys. Private directories use mode `0700`; private files use mode `0600`; retained request-projection evidence contains only allowlisted fields.
- Keep both GGUF files and rollback material until every repository, service, long-context, metadata, qualification, and final agent gate passes.
- After editing any `.py` file, run `make validate && make test`.

---

## File Map

| File | Responsibility |
|---|---|
| `pylib/config.py` | Validate the optional sampler mapping and produce model-specific actionable diagnostics. |
| `pylib/presets.py` | Map YAML sampler names to llama.cpp preset keys in deterministic order. |
| `tests/test_config.py` | Cover valid, invalid, migration-neutral, and persistence behavior. |
| `tests/test_presets.py` | Cover exact model-level rendering, zero values, empty mappings, and disabled models. |
| `tests/test_cli.py` | Prove invalid sampling blocks output and that clean setup contains the selected literal profile. |
| `tests/test_shell.py` | Prove malformed sampling stops startup before key generation, unit rendering, or service interaction. |
| `models.yml.example` | Document and install the profile selected by live qualification. |
| `scripts/check-with-agents.sh` | Unchanged source of the four required live-agent cell identities. |

### Task 1: Validate Optional Model Sampling

**Files:**
- Modify: `pylib/config.py:5-39,156-191`
- Modify: `tests/test_config.py:92-94,133-154,391-394`
- Modify: `tests/test_cli.py:307-335`
- Modify: `tests/test_shell.py:1073-1132,2506-2524`

**Interfaces:**
- Consumes: existing model dictionaries and `validate_config(cfg) -> list[str]`.
- Produces: optional validated `model["sampling"]` mappings; diagnostics naming the model alias, mapping or field, and violated constraint.

- [ ] **Step 1: Add failing configuration tests**

Add these tests after `test_valid_config_has_no_errors()` in `tests/test_config.py`:

```python
@pytest.mark.parametrize(
    "sampling",
    [
        {},
        {"temperature": 0},
        {"temperature": 1.0},
        {"top_p": 0},
        {"top_p": 0.95},
        {"top_p": 1},
        {"top_k": 0},
        {"top_k": 64},
        {"repeat_penalty": 1},
        {"repeat_penalty": 1.1},
        {
            "temperature": 1.0,
            "top_p": 0.95,
            "top_k": 64,
            "repeat_penalty": 1.1,
        },
    ],
)
def test_config_accepts_optional_sampling_fields(sampling):
    cfg = make_cfg()
    cfg["models"][0]["sampling"] = sampling

    assert validate_config(cfg) == []


@pytest.mark.parametrize(
    ("sampling", "expected"),
    [
        (None, "model gemma4 sampling must be a mapping"),
        ([], "model gemma4 sampling must be a mapping"),
        ("default", "model gemma4 sampling must be a mapping"),
        (1, "model gemma4 sampling must be a mapping"),
        (True, "model gemma4 sampling must be a mapping"),
        (
            {"typical_p": 1.0},
            "model gemma4 sampling.typical_p is not a supported field",
        ),
        (
            {1: 1.0},
            "model gemma4 sampling.1 is not a supported field",
        ),
        (
            {"temperature": "1.0"},
            "model gemma4 sampling.temperature must be a finite non-negative number",
        ),
        (
            {"temperature": -0.1},
            "model gemma4 sampling.temperature must be a finite non-negative number",
        ),
        (
            {"temperature": True},
            "model gemma4 sampling.temperature must be a finite non-negative number",
        ),
        (
            {"temperature": float("inf")},
            "model gemma4 sampling.temperature must be a finite non-negative number",
        ),
        (
            {"top_p": -0.1},
            "model gemma4 sampling.top_p must be a finite number between 0 and 1 inclusive",
        ),
        (
            {"top_p": 1.1},
            "model gemma4 sampling.top_p must be a finite number between 0 and 1 inclusive",
        ),
        (
            {"top_p": float("nan")},
            "model gemma4 sampling.top_p must be a finite number between 0 and 1 inclusive",
        ),
        (
            {"top_p": None},
            "model gemma4 sampling.top_p must be a finite number between 0 and 1 inclusive",
        ),
        (
            {"top_p": False},
            "model gemma4 sampling.top_p must be a finite number between 0 and 1 inclusive",
        ),
        (
            {"top_k": -1},
            "model gemma4 sampling.top_k must be a non-negative integer and not a Boolean",
        ),
        (
            {"top_k": 1.0},
            "model gemma4 sampling.top_k must be a non-negative integer and not a Boolean",
        ),
        (
            {"top_k": False},
            "model gemma4 sampling.top_k must be a non-negative integer and not a Boolean",
        ),
        (
            {"top_k": "64"},
            "model gemma4 sampling.top_k must be a non-negative integer and not a Boolean",
        ),
        (
            {"repeat_penalty": 0},
            "model gemma4 sampling.repeat_penalty must be a finite number greater than 0",
        ),
        (
            {"repeat_penalty": -0.1},
            "model gemma4 sampling.repeat_penalty must be a finite number greater than 0",
        ),
        (
            {"repeat_penalty": False},
            "model gemma4 sampling.repeat_penalty must be a finite number greater than 0",
        ),
        (
            {"repeat_penalty": float("-inf")},
            "model gemma4 sampling.repeat_penalty must be a finite number greater than 0",
        ),
        (
            {"repeat_penalty": "1.1"},
            "model gemma4 sampling.repeat_penalty must be a finite number greater than 0",
        ),
    ],
)
def test_config_rejects_invalid_sampling_with_actionable_error(sampling, expected):
    cfg = make_cfg()
    cfg["models"][0]["sampling"] = sampling

    assert validate_config(cfg) == [expected]
```

Extend `test_pre_feature_config_migration_is_additive_and_idempotent()` with:

```python
    assert all("sampling" not in model for model in migrated["models"])
```

Add after `test_save_then_load_roundtrip()`:

```python
def test_sampling_survives_save_load_roundtrip(tmp_path):
    cfg = make_cfg()
    cfg["models"][0]["sampling"] = {
        "temperature": 1.0,
        "top_p": 0.95,
        "top_k": 64,
        "repeat_penalty": 1.1,
    }
    path = tmp_path / "models.yml"

    save_config(cfg, path)

    assert load_config(path) == cfg
```

- [ ] **Step 2: Add failing CLI and startup-order tests**

Add after `test_operational_commands_reject_invalid_concurrency_before_output_or_save()` in `tests/test_cli.py`:

```python
def test_presets_rejects_invalid_sampling_before_output(tmp_path: Path):
    config = write_test_config(tmp_path)
    parsed = yaml.safe_load(config.read_text())
    parsed["models"][0]["sampling"] = {"temperature": -1}
    config.write_text(yaml.safe_dump(parsed, sort_keys=False))
    before = config.read_bytes()
    output = tmp_path / "presets.ini"

    result = run(
        "presets",
        "--models-dir",
        "/models",
        "--device",
        "all",
        "--output",
        str(output),
        "--config",
        str(config),
    )

    assert result.returncode == 1
    assert json.loads(result.stdout)["error"] == (
        "model a sampling.temperature must be a finite non-negative number"
    )
    assert "Traceback" not in result.stdout + result.stderr
    assert config.read_bytes() == before
    assert not output.exists()
```

Add a keyword parameter to `run_lifecycle_script()` in `tests/test_shell.py`:

```python
    sampling_temperature: str | None = None,
```

Change the end of the generated model record from:

```python
        "    n_gpu_layers: 99\n"
```

to:

```python
        "    n_gpu_layers: 99\n"
        + (
            "    sampling:\n"
            f"      temperature: {sampling_temperature}\n"
            if sampling_temperature is not None
            else ""
        )
```

Add after `test_start_rejects_invalid_concurrency_before_key_or_service_output()`:

```python
def test_start_rejects_invalid_sampling_before_key_or_service_output(
    tmp_path: pathlib.Path,
) -> None:
    result, config, calls = run_lifecycle_script(
        tmp_path,
        "scripts/start.sh",
        api_key="",
        config_mode=0o644,
        sampling_temperature="-1",
    )

    assert result.returncode != 0
    assert (
        "model test sampling.temperature must be a finite non-negative number"
        in result.stderr
    )
    assert yq_value(config, ".server.api_key") == ""
    recorded = calls.read_text()
    assert "yq -i .server.api_key" not in recorded
    assert "systemctl --user start" not in recorded
    assert f"bash {ROOT / 'setup/render-unit.sh'}" not in recorded
    assert not (config.parent / "presets.ini").exists()
```

- [ ] **Step 3: Run the new tests and verify they fail**

Run:

```bash
uv run --with pytest pytest \
  tests/test_config.py \
  tests/test_cli.py::test_presets_rejects_invalid_sampling_before_output \
  tests/test_shell.py::test_start_rejects_invalid_sampling_before_key_or_service_output \
  -v
```

Expected: the invalid sampler cases fail because the schema currently ignores `sampling`; the CLI writes a preset and the lifecycle reaches service setup.

- [ ] **Step 4: Implement finite-number and sampling validation**

Add `import math` with the standard-library imports in `pylib/config.py`.

Add beside the existing schema constants:

```python
SAMPLING_FIELDS = frozenset(
    ("temperature", "top_p", "top_k", "repeat_penalty")
)
```

Add after `_positive_int()`:

```python
def _finite_number(value: Any) -> bool:
    return (
        isinstance(value, int) and not isinstance(value, bool)
    ) or (isinstance(value, float) and math.isfinite(value))
```

Inside the per-model loop in `validate_config()`, after the `enabled` check and before `vram_budget`, add:

```python
        if "sampling" in model:
            sampling = model["sampling"]
            if not isinstance(sampling, dict):
                errors.append(f"model {model_name} sampling must be a mapping")
            else:
                for field in sampling:
                    if field not in SAMPLING_FIELDS:
                        errors.append(
                            f"model {model_name} sampling.{field} is not a supported field"
                        )

                if "temperature" in sampling and not (
                    _finite_number(sampling["temperature"])
                    and sampling["temperature"] >= 0
                ):
                    errors.append(
                        f"model {model_name} sampling.temperature must be a finite non-negative number"
                    )
                if "top_p" in sampling and not (
                    _finite_number(sampling["top_p"])
                    and 0 <= sampling["top_p"] <= 1
                ):
                    errors.append(
                        f"model {model_name} sampling.top_p must be a finite number between 0 and 1 inclusive"
                    )
                if "top_k" in sampling and not (
                    isinstance(sampling["top_k"], int)
                    and not isinstance(sampling["top_k"], bool)
                    and sampling["top_k"] >= 0
                ):
                    errors.append(
                        f"model {model_name} sampling.top_k must be a non-negative integer and not a Boolean"
                    )
                if "repeat_penalty" in sampling and not (
                    _finite_number(sampling["repeat_penalty"])
                    and sampling["repeat_penalty"] > 0
                ):
                    errors.append(
                        f"model {model_name} sampling.repeat_penalty must be a finite number greater than 0"
                    )
```

Do not modify `migrate_config()` or `REQUIRED_MODEL_KEYS`.

- [ ] **Step 5: Run focused and full verification**

Run:

```bash
uv run --with pytest pytest \
  tests/test_config.py \
  tests/test_cli.py::test_presets_rejects_invalid_sampling_before_output \
  tests/test_shell.py::test_start_rejects_invalid_sampling_before_key_or_service_output \
  -v
make validate
make test
```

Expected: focused tests pass; Shellcheck and Ruff report no errors; the full Python suite passes.

- [ ] **Step 6: Commit the validated schema**

```bash
git add pylib/config.py tests/test_config.py tests/test_cli.py tests/test_shell.py
git commit -m "feat(config): validate model sampler settings"
```

### Task 2: Render Samplers in Model Presets

**Files:**
- Modify: `pylib/presets.py:17-48`
- Modify: `tests/test_presets.py:1-8,117-140`

**Interfaces:**
- Consumes: a validated optional `model["sampling"]` mapping.
- Produces: llama.cpp keys `temp`, `top-p`, `top-k`, and `repeat-penalty` in only that enabled model's preset section.

- [ ] **Step 1: Add failing preset tests**

Add these imports to `tests/test_presets.py`:

```python
import copy

import pytest
```

Add after `test_model_section_has_absolute_path_and_settings()`:

```python
SAMPLER_KEYS = ("temp", "top-p", "top-k", "repeat-penalty")


@pytest.mark.parametrize("temperature", [1.0, 0.0])
def test_model_sampling_maps_to_llama_preset_keys(temperature):
    cfg = copy.deepcopy(CFG)
    cfg["models"][0]["sampling"] = {
        "temperature": temperature,
        "top_p": 0.95,
        "top_k": 64,
        "repeat_penalty": 1.1,
    }

    parser = parse(render_presets(cfg, "/models", "Vulkan0"))

    assert {key: parser["gemma4"][key] for key in SAMPLER_KEYS} == {
        "temp": str(temperature),
        "top-p": "0.95",
        "top-k": "64",
        "repeat-penalty": "1.1",
    }
    assert all(key not in parser["*"] for key in SAMPLER_KEYS)
    assert all(key not in parser["ornith"] for key in SAMPLER_KEYS)


def test_empty_sampling_mapping_emits_no_sampler_keys():
    cfg = copy.deepcopy(CFG)
    cfg["models"][0]["sampling"] = {}

    section = parse(render_presets(cfg, "/models", "Vulkan0"))["gemma4"]

    assert all(key not in section for key in SAMPLER_KEYS)


def test_zero_sampling_values_are_not_dropped():
    cfg = copy.deepcopy(CFG)
    cfg["models"][0]["sampling"] = {
        "temperature": 0,
        "top_p": 0,
        "top_k": 0,
        "repeat_penalty": 1,
    }

    section = parse(render_presets(cfg, "/models", "Vulkan0"))["gemma4"]

    assert {key: section[key] for key in SAMPLER_KEYS} == {
        "temp": "0",
        "top-p": "0",
        "top-k": "0",
        "repeat-penalty": "1",
    }


def test_disabled_model_sampling_emits_no_section():
    cfg = copy.deepcopy(CFG)
    cfg["models"][0]["enabled"] = False
    cfg["models"][0]["sampling"] = {"temperature": 1.0}

    assert "gemma4" not in parse(render_presets(cfg, "/models", "Vulkan0"))
```

Keep `CFG` sampler-free so `test_exact_one_slot_no_fit_preset()` continues to prove byte-for-byte compatibility for models without sampler configuration.

- [ ] **Step 2: Run preset tests and verify they fail**

Run:

```bash
uv run --with pytest pytest tests/test_presets.py -v
```

Expected: the mapping and zero-value tests fail because `render_presets()` currently ignores `sampling`; existing sampler-free tests continue to pass.

- [ ] **Step 3: Implement deterministic model-level rendering**

Add after `HEADER_COMMENT` in `pylib/presets.py`:

```python
SAMPLING_PRESET_KEYS = (
    ("temperature", "temp"),
    ("top_p", "top-p"),
    ("top_k", "top-k"),
    ("repeat_penalty", "repeat-penalty"),
)
```

Replace the enabled-model rendering loop with:

```python
    for model in enabled_models(cfg):
        section = {
            "model": str(Path(models_dir) / model["file"]),
            "ctx-size": str(model["ctx_size"]),
            "n-gpu-layers": str(model["n_gpu_layers"]),
        }
        sampling = model.get("sampling", {})
        for config_key, preset_key in SAMPLING_PRESET_KEYS:
            if config_key in sampling:
                section[preset_key] = str(sampling[config_key])
        parser[model["alias"]] = section
```

Do not call `require_valid_config()` from `render_presets()`; validation remains at the CLI boundary and the renderer retains its existing focused interface.

- [ ] **Step 4: Run focused and full verification**

Run:

```bash
uv run --with pytest pytest tests/test_presets.py -v
make validate
make test
```

Expected: all preset tests pass, including the unchanged exact sampler-free output; full validation and tests pass.

- [ ] **Step 5: Commit model-level preset rendering**

```bash
git add pylib/presets.py tests/test_presets.py
git commit -m "feat(presets): render model sampler settings"
```

### Task 3: Verify Client Requests Inherit Model Presets

**Files:**
- Create privately: `~/.local/state/llm-env/sampler-experiment/`
- Create temporarily and delete: `~/.local/state/llm-env/sampler-experiment/proxy.mjs`
- Create temporarily and delete: `~/.local/state/llm-env/sampler-experiment/preflight.sh`
- Create privately: `~/.local/state/llm-env/sampler-experiment/preflight-summary-path`
- Retain privately: `/tmp/llm-env-sampler-probe.*/assertion-summary.json`

**Interfaces:**
- Consumes: active base service, Pi, OpenCode, the exact provider definitions and CLI options from `scripts/check-with-agents.sh`.
- Produces: a secret-safe assertion summary proving each installed client omits all four request-level sampler fields through a tool-call continuation.

- [ ] **Step 1: Create the private experiment state directory**

Create the state directory through an unpredictable temporary name inside a
user-private parent, then rename it atomically:

```bash
state_parent="$HOME/.local/state/llm-env"
install -d -m 700 "$state_parent"
test "$(stat -c %U "$state_parent")" = "$(id -un)"
test "$(stat -c %a "$state_parent")" = 700
test ! -e "$state_parent/sampler-experiment"
temporary_state="$(mktemp -d "$state_parent/.sampler-experiment.XXXXXX")"
chmod 700 "$temporary_state"
mv -- "$temporary_state" "$state_parent/sampler-experiment"
test ! -L "$state_parent/sampler-experiment"
test "$(stat -c %a "$state_parent/sampler-experiment")" = 700
definition_tmp="$(mktemp "$state_parent/sampler-experiment/.harness-definition.XXXXXX")"
repo=/var/home/bazzite/git/llm-env
harness_sha256="$(sha256sum "${repo}/scripts/check-with-agents.sh")"
printf '%s\n' "${harness_sha256%% *}" >"$definition_tmp"
chmod 600 "$definition_tmp"
mv -- "$definition_tmp" \
  "$state_parent/sampler-experiment/preflight-definition-harness-sha256"
```

Expected: the fixed state path is owned by the current user, is not a symlink,
and has mode `0700`. Every script, pointer, selection record, pending attempt,
and ledger created below stays inside this directory. The reviewed harness hash
is pinned before the hardcoded preflight definitions are created.

- [ ] **Step 2: Create the localhost projection proxy**

Use `apply_patch` to create
`/home/bazzite/.local/state/llm-env/sampler-experiment/proxy.mjs` with this
complete content, then set mode `0600`:

```javascript
import fs from "node:fs";
import http from "node:http";
import { Readable } from "node:stream";

const [upstreamBase, summaryPath, portPath, client] = process.argv.slice(2);
const samplerFields = ["temperature", "top_p", "top_k", "repeat_penalty"];
let ordinal = 0;

const server = http.createServer((request, response) => {
  const chunks = [];
  request.on("data", (chunk) => chunks.push(chunk));
  request.on("end", async () => {
    try {
      const requestBody = Buffer.concat(chunks);
      const upstreamUrl = new URL(request.url, upstreamBase);

      if (upstreamUrl.pathname.endsWith("/chat/completions")) {
        const body = JSON.parse(requestBody.toString("utf8"));
        const messages = Array.isArray(body.messages) ? body.messages : [];
        const sampling = Object.fromEntries(
          samplerFields
            .filter((field) => Object.hasOwn(body, field))
            .map((field) => [field, body[field]]),
        );
        const projection = {
          client,
          ordinal: ++ordinal,
          model: body.model ?? null,
          message_roles: messages.map((message) => message.role ?? null),
          tools_present: Array.isArray(body.tools) && body.tools.length > 0,
          tool_result_present: messages.some(
            (message) =>
              message.role === "tool" ||
              (Array.isArray(message.tool_calls) && message.tool_calls.length > 0),
          ),
          sampling,
        };
        fs.appendFileSync(summaryPath, `${JSON.stringify(projection)}\n`, {
          mode: 0o600,
        });
      }

      const headers = { ...request.headers, "accept-encoding": "identity" };
      for (const header of [
        "host",
        "content-length",
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
      ]) {
        delete headers[header];
      }
      const upstreamResponse = await fetch(upstreamUrl, {
        method: request.method,
        headers,
        body: ["GET", "HEAD"].includes(request.method) ? undefined : requestBody,
      });
      const responseHeaders = Object.fromEntries(upstreamResponse.headers.entries());
      for (const header of [
        "content-length",
        "content-encoding",
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
      ]) {
        delete responseHeaders[header];
      }
      response.writeHead(upstreamResponse.status, responseHeaders);
      if (upstreamResponse.body === null) {
        response.end();
      } else {
        Readable.fromWeb(upstreamResponse.body).pipe(response);
      }
    } catch {
      response.writeHead(502, { "content-type": "text/plain" });
      response.end("proxy failure");
    }
  });
});

server.listen(0, "127.0.0.1", () => {
  const address = server.address();
  fs.writeFileSync(portPath, String(address.port), { mode: 0o600 });
});

for (const signal of ["SIGINT", "SIGTERM"]) {
  process.on(signal, () => server.close(() => process.exit(0)));
}
```

Run:

```bash
chmod 600 "$HOME/.local/state/llm-env/sampler-experiment/proxy.mjs"
```

- [ ] **Step 3: Create the private preflight runner**

Use `apply_patch` to create
`/home/bazzite/.local/state/llm-env/sampler-experiment/preflight.sh` with this
complete content, then set mode `0600`:

```bash
#!/usr/bin/env bash
set -euo pipefail
set +x
umask 077

repo=/var/home/bazzite/git/llm-env
# shellcheck source=tools/lib.sh
source "${repo}/tools/lib.sh"
state_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
config_path="$CONFIG_PATH"
api_key="$(yq -r '.server.api_key' "$config_path")"
port="$(yq -r '.server.port' "$config_path")"
[[ "$port" =~ ^[0-9]+$ ]] && [ "$port" -ge 1 ] && [ "$port" -le 65535 ] \
    || die "server port is not configured"
[ -n "$api_key" ] && [ "$api_key" != null ] || die "server API key is not configured"
definition_harness_sha256="$(< "$state_dir/preflight-definition-harness-sha256")"
current_harness_sha256="$(sha256sum "${repo}/scripts/check-with-agents.sh")"
current_harness_sha256="${current_harness_sha256%% *}"
[ "$current_harness_sha256" = "$definition_harness_sha256" ] || die \
    "agent harness changed; revalidate and update the preflight definitions before rerunning"
old_preflight_dir=""
if [ -f "$state_dir/preflight-summary-path" ]; then
    old_preflight_dir="$(< "$state_dir/preflight-summary-path")"
    case "$old_preflight_dir" in
        /tmp/llm-env-sampler-probe.*) ;;
        *) die "existing sampler preflight path is invalid" ;;
    esac
fi

diagnostic_dir="$(prepare_diagnostic_dir sampler-probe)"
workspace="$(mktemp -d /tmp/llm-env-sampler-work.XXXXXX)"
chmod 700 "$workspace"
raw_summary="$workspace/projections.jsonl"
touch "$raw_summary"
chmod 600 "$raw_summary"
proxy_pid=""

cleanup() {
    local status=$?
    if [ -n "$proxy_pid" ]; then
        kill "$proxy_pid" >/dev/null 2>&1 || true
        wait "$proxy_pid" >/dev/null 2>&1 || true
    fi
    rm -rf -- "$workspace"
    if [ "${diagnostic_finished:-0}" -eq 0 ]; then
        finish_diagnostic_dir "$diagnostic_dir"
    fi
    exit "$status"
}
trap cleanup EXIT

prompt="You MUST use bash to execute this exact command verbatim as the only network request: curl -fsS --max-time 20 -- 'https://api.open-meteo.com/v1/forecast?latitude=-33.4489&longitude=-70.6693&current=temperature_2m,weather_code&timezone=America%2FSantiago'. The URL argument must be copied byte-for-byte from the command. Do not substitute any source, endpoint, proxy, mirror, or query. Return fields only from that command's response. The source_url field must reproduce the literal URL byte-for-byte, including percent encoding. The source_timestamp field must copy the source response's timestamp text byte-for-byte. Do not convert or normalize its timezone, add an offset, or change its date. Return source_timestamp as ISO-8601. Return exactly one JSON object containing source_url, source_timestamp, temperature_2m, and weather_code."

run_probe() {
    local client port_file proxy_base config_dir config_file
    client="$1"
    port_file="$workspace/${client}.port"
    rm -f -- "$port_file"
    "$node_binary" "$state_dir/proxy.mjs" \
        "http://127.0.0.1:${port}" \
        "$raw_summary" \
        "$port_file" \
        "$client" \
        >/dev/null 2>&1 &
    proxy_pid=$!
    for _ in {1..100}; do
        [ -s "$port_file" ] && break
        kill -0 "$proxy_pid" 2>/dev/null || die "sampler proxy exited during startup"
        sleep 0.1
    done
    [ -s "$port_file" ] || die "sampler proxy did not publish a port"
    proxy_base="http://127.0.0.1:$(<"$port_file")/v1"

    case "$client" in
        pi)
            config_dir="$workspace/pi"
            config_file="$config_dir/models.json"
            mkdir -p "$config_dir"
            chmod 700 "$config_dir"
            printf -v config_key_command \
                "!yq -r '.server.api_key' %q" "$config_path"
            jq -n \
                --arg base_url "$proxy_base" \
                --arg api_key_command "$config_key_command" \
                '{providers: {"llm-env": {
                    baseUrl: $base_url,
                    api: "openai-completions",
                    apiKey: $api_key_command,
                    compat: {supportsDeveloperRole: false, supportsReasoningEffort: false},
                    models: [{id: "gemma4"}]
                }}}' >"$config_file"
            chmod 600 "$config_file"
            (
                cd "$workspace"
                PI_CODING_AGENT_DIR="$config_dir" pi \
                    --no-session \
                    --no-extensions \
                    --no-skills \
                    --no-prompt-templates \
                    --no-context-files \
                    --tools bash \
                    -p \
                    --mode json \
                    --model llm-env/gemma4 \
                    "$prompt" </dev/null
            ) >"$workspace/pi-transcript.jsonl" 2>"$workspace/pi-stderr"
            ;;
        opencode)
            config_dir="$workspace/opencode"
            config_file="$config_dir/opencode.jsonc"
            mkdir -p \
                "$config_dir" \
                "$workspace/opencode-home" \
                "$workspace/opencode-config" \
                "$workspace/opencode-data" \
                "$workspace/opencode-state"
            chmod 700 \
                "$config_dir" \
                "$workspace/opencode-home" \
                "$workspace/opencode-config" \
                "$workspace/opencode-data" \
                "$workspace/opencode-state"
            jq -n \
                --arg base_url "$proxy_base" \
                '{"$schema": "https://opencode.ai/config.json", tools: {"*": false, bash: true}, provider: {"llm-env": {
                    npm: "@ai-sdk/openai-compatible",
                    name: "llm-env",
                    options: {baseURL: $base_url, apiKey: "{env:OPENCODE_API_KEY}"},
                    models: {gemma4: {name: "gemma4"}}
                }}}' >"$config_file"
            chmod 600 "$config_file"
            (
                cd "$workspace"
                export HOME="$workspace/opencode-home"
                export XDG_CONFIG_HOME="$workspace/opencode-config"
                export XDG_DATA_HOME="$workspace/opencode-data"
                export XDG_STATE_HOME="$workspace/opencode-state"
                export OPENCODE_CONFIG="$config_file"
                export OPENCODE_API_KEY="$api_key"
                opencode run --format json --model llm-env/gemma4 "$prompt" </dev/null
            ) >"$workspace/opencode-transcript.jsonl" 2>"$workspace/opencode-stderr"
            ;;
        *) die "unsupported probe client: $client" ;;
    esac

    kill "$proxy_pid"
    wait "$proxy_pid" || true
    proxy_pid=""
}

for command in pi opencode node; do
    command -v "$command" >/dev/null || die "$command is required for sampler preflight"
done
node_binary="$(node -p 'process.execPath')"
[ -x "$node_binary" ] || die "could not resolve the Node.js executable"

run_probe pi
run_probe opencode

pi_version="$(pi --version)"
opencode_version="$(opencode --version)"
harness_sha256="$current_harness_sha256"

jq -s \
    --arg pi_version "$pi_version" \
    --arg opencode_version "$opencode_version" \
    --arg harness_sha256 "$harness_sha256" \
    '{
        pi_version: $pi_version,
        opencode_version: $opencode_version,
        harness_sha256: $harness_sha256,
        requests: .
    }
    | def has_tool_continuation($client):
        [.requests[] | select(.client == $client)] as $requests
        | ($requests | length) >= 2
          and any(
            range(0; ($requests | length));
            . as $index
            | $requests[$index].tools_present
              and any($requests[($index + 1):][]; .tool_result_present)
          );
    .assertions = {
        pi_tool_continuation: has_tool_continuation("pi"),
        opencode_tool_continuation: has_tool_continuation("opencode"),
        all_models_are_gemma4: all(.requests[]; .model == "gemma4"),
        all_sampler_fields_omitted: all(.requests[]; (.sampling | length) == 0)
    }' \
    "$raw_summary" >"$diagnostic_dir/assertion-summary.json"
chmod 600 "$diagnostic_dir/assertion-summary.json"

jq -e '
    .assertions.pi_tool_continuation
    and .assertions.opencode_tool_continuation
    and .assertions.all_models_are_gemma4
    and .assertions.all_sampler_fields_omitted
' "$diagnostic_dir/assertion-summary.json" >/dev/null

export LLM_ENV_KEEP_CHECK_ARTIFACTS=1
finish_diagnostic_dir "$diagnostic_dir"
diagnostic_finished=1
pointer_tmp="$(mktemp "$state_dir/.preflight-summary-path.XXXXXX")"
printf '%s\n' "$diagnostic_dir" >"$pointer_tmp"
chmod 600 "$pointer_tmp"
mv -f -- "$pointer_tmp" "$state_dir/preflight-summary-path"
if [ -n "$old_preflight_dir" ] && [ "$old_preflight_dir" != "$diagnostic_dir" ]; then
    rm -rf -- "$old_preflight_dir"
fi
```

Run:

```bash
state_dir="$HOME/.local/state/llm-env/sampler-experiment"
chmod 600 "$state_dir/preflight.sh"
node --check "$state_dir/proxy.mjs"
bash -n "$state_dir/preflight.sh"
shellcheck -s bash -x -P /var/home/bazzite/git/llm-env \
  "$state_dir/preflight.sh"
bash "$state_dir/preflight.sh"
```

Expected: both clients complete at least one tool call and continuation request;
every projected request has `sampling: {}`; the retained directory contains
only mode-`0600` `assertion-summary.json`; the raw workspace is removed. Keep
the proxy and preflight scripts until final cleanup so drift recovery can rerun
this exact preflight.

- [ ] **Step 4: Verify the retained preflight without exposing credentials**

Run:

```bash
state_dir="$HOME/.local/state/llm-env/sampler-experiment"
preflight_dir="$(< "$state_dir/preflight-summary-path")"
case "$preflight_dir" in
  /tmp/llm-env-sampler-probe.*) ;;
  *) printf '%s\n' 'invalid sampler preflight directory' >&2; exit 1 ;;
esac
stat -c '%a %n' "$preflight_dir" "$preflight_dir/assertion-summary.json"
jq -e '
  .assertions == {
    pi_tool_continuation: true,
    opencode_tool_continuation: true,
    all_models_are_gemma4: true,
    all_sampler_fields_omitted: true
  }
  and all(.requests[]; (.sampling | length) == 0)
' "$preflight_dir/assertion-summary.json" >/dev/null
```

Expected: modes are `700` and `600`; the assertion exits `0`. This task changes no tracked files and creates no commit.

### Task 4: Screen and Qualify the Agentic Profiles

**Files:**
- Modify privately: `~/.config/llm-env/models.yml`
- Regenerate privately: `~/.config/llm-env/presets.ini`
- Create temporarily and delete: `~/.local/state/llm-env/sampler-experiment/candidate.sh`
- Create temporarily and delete: `~/.local/state/llm-env/sampler-experiment/classify.sh`
- Create privately: `~/.local/state/llm-env/sampler-experiment/selected-profile`
- Create privately: `~/.local/state/llm-env/sampler-experiment/ledger.json`
- Create transiently: `~/.local/state/llm-env/sampler-experiment/pending-attempt.json`
- Retain privately: `/tmp/llm-env-sampler-round.*/`

**Interfaces:**
- Consumes: validated sampler schema/rendering, the preflight summary, both GGUFs, and `/tmp/llm-env-gemma-rollback.path`.
- Produces: a classified ledger for every attempt and the first profile in fixed order with one valid screening pass and three consecutive valid four-cell qualification rounds, left active in private configuration.

- [ ] **Step 1: Verify rollback viability and activate the agentic artifact once**

Perform every recovery check before stopping or changing the service:

```bash
set -euo pipefail
repo=/var/home/bazzite/git/llm-env
source "${repo}/tools/lib.sh"
state_dir="$HOME/.local/state/llm-env/sampler-experiment"
test -d "$state_dir" && test ! -L "$state_dir"
test "$(stat -c %a "$state_dir")" = 700

rollback_pointer=/tmp/llm-env-gemma-rollback.path
test -f "$rollback_pointer" && test ! -L "$rollback_pointer"
test "$(stat -c %a "$rollback_pointer")" = 600
rollback_dir="$(< "$rollback_pointer")"
case "$rollback_dir" in
  /tmp/llm-env-gemma-rollback.*) ;;
  *) printf '%s\n' 'invalid rollback directory' >&2; exit 1 ;;
esac
test -d "$rollback_dir" && test ! -L "$rollback_dir"
test "$(stat -c %a "$rollback_dir")" = 700
test -f "$rollback_dir/models.yml" && test ! -L "$rollback_dir/models.yml"
test "$(stat -c %a "$rollback_dir/models.yml")" = 600
yq -e '
  [.models[]
   | select(.alias == "gemma4"
            and .enabled == true
            and .file == "gemma-4-12B-it-Q4_K_M.gguf")]
  | length == 1
' "$rollback_dir/models.yml" >/dev/null

old_model="$HOME/llm-workspace/models/gemma-4-12B-it-Q4_K_M.gguf"
new_model="$HOME/llm-workspace/models/gemma4-v2-Q4_K_M.gguf"
test -f "$old_model" && test -f "$new_model"
printf '%s  %s\n' \
  '0b9506cab36f7f818e34f9c0f5a3d6568d0b37100f3a3e1092e2eec3c4c96791' \
  "$new_model" | sha256sum --check --status -
uv run llmenv.py models list --config "$rollback_dir/models.yml" >/dev/null
uv run llmenv.py validate-gguf \
  --config "$rollback_dir/models.yml" \
  --models-dir "$HOME/llm-workspace/models" >/dev/null
make check-server

activation_config="$(mktemp "$state_dir/.activation-models.XXXXXX")"
install -m 600 "$CONFIG_PATH" "$activation_config"
MODEL_LABEL='Gemma 4 12B Agentic v2' \
MODEL_FILE='gemma4-v2-Q4_K_M.gguf' \
MODEL_URL='https://huggingface.co/yuxinlu1/gemma-4-12B-agentic-fable5-composer2.5-v2-3.5x-tau2-GGUF/resolve/190a31365a6b80a692349be34ccdac730cad4fe4/gemma4-v2-Q4_K_M.gguf' \
MODEL_SIZE='7381381664' \
yq -i '
  (.models[] | select(.alias == "gemma4")) |= (
    .label = strenv(MODEL_LABEL) |
    .file = strenv(MODEL_FILE) |
    .url = strenv(MODEL_URL) |
    .size_bytes = (strenv(MODEL_SIZE) | tonumber) |
    del(.sampling)
  )
' "$activation_config"
uv run llmenv.py migrate-config --config "$activation_config" >/dev/null
uv run llmenv.py models list --config "$activation_config" >/dev/null
make stop
install -m 600 "$activation_config" "$CONFIG_PATH"
rm -f -- "$activation_config"
baseline_hash="$(
  yq -o=json '
    (.models[] | select(.alias == "gemma4")) |= del(.sampling)
  ' "$CONFIG_PATH" |
    jq -cS '.' |
    sha256sum
)"
baseline_hash="${baseline_hash%% *}"
baseline_tmp="$(mktemp "$state_dir/.config-baseline.XXXXXX")"
printf '%s\n' "$baseline_hash" >"$baseline_tmp"
chmod 600 "$baseline_tmp"
mv -- "$baseline_tmp" "$state_dir/config-without-sampling.sha256"
```

Expected: all recovery assets and modes are valid before mutation; the base
service contract passes; the stopped private configuration references the
validated agentic GGUF with no sampler mapping. Metadata is changed once here,
outside candidate comparison; every candidate run below changes only
`.sampling`.

- [ ] **Step 2: Create the candidate runner**

Use `apply_patch` to create
`/home/bazzite/.local/state/llm-env/sampler-experiment/candidate.sh` with this
complete content, then set mode `0600`:

```bash
#!/usr/bin/env bash
set -euo pipefail
set +x
umask 077

[ "$#" -eq 2 ] || { printf '%s\n' 'usage: candidate PROFILE RUN_ID' >&2; exit 64; }
profile="$1"
run_id="$2"
repo=/var/home/bazzite/git/llm-env
# shellcheck source=tools/lib.sh
source "${repo}/tools/lib.sh"
state_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
pending="$state_dir/pending-attempt.json"
[ ! -e "$pending" ] || {
    printf '%s\n' 'classify the pending sampler attempt before another run' >&2
    exit 75
}
case "$profile" in
    publisher) temperature=1.0 ;;
    greedy) temperature=0.0 ;;
    *) printf '%s\n' "unknown profile: $profile" >&2; exit 64 ;;
esac
intended_projection="$(jq -cSn --argjson temperature "$temperature" '{
  alias: "gemma4",
  file: "gemma4-v2-Q4_K_M.gguf",
  ctx_size: 131072,
  client_max_output_tokens: 8192,
  n_gpu_layers: 99,
  sampling: {
    temperature: $temperature,
    top_p: 0.95,
    top_k: 64,
    repeat_penalty: 1.1
  }
}')"
fingerprint="$(sha256sum <<<"$intended_projection")"
fingerprint="${fingerprint%% *}"
diagnostic_dir="$(prepare_diagnostic_dir sampler-round)"
attempt_id="$(basename -- "$diagnostic_dir")"
export LLM_ENV_KEEP_CHECK_ARTIFACTS=1
request_pid=""
diagnostic_finished=0
config_mutated=0
stage=preflight
observed=runner-error
matrix_status=-1

cleanup() {
    local status=$?
    local pending_tmp
    trap - EXIT
    if [ -n "$request_pid" ]; then
        kill "$request_pid" >/dev/null 2>&1 || true
        wait "$request_pid" >/dev/null 2>&1 || true
    fi
    if [ "$config_mutated" -eq 1 ] \
        && [ "$(config_baseline_hash)" != "$expected_baseline_hash" ]; then
        stage=config-post-run
        observed=runner-error
        status=1
    fi
    if [ "$diagnostic_finished" -eq 0 ]; then
        finish_diagnostic_dir "$diagnostic_dir"
        diagnostic_finished=1
    fi
    pending_tmp="$(mktemp "$state_dir/.pending-attempt.XXXXXX")"
    jq -n \
        --arg attempt_id "$attempt_id" \
        --arg profile "$profile" \
        --arg run_id "$run_id" \
        --arg stage "$stage" \
        --arg observed "$observed" \
        --arg fingerprint "$fingerprint" \
        --arg evidence_dir "$diagnostic_dir" \
        --argjson command_status "$status" \
        --argjson matrix_status "$matrix_status" \
        '{
          attempt_id: $attempt_id,
          profile: $profile,
          run_id: $run_id,
          stage: $stage,
          observed: $observed,
          fingerprint: $fingerprint,
          evidence_dir: $evidence_dir,
          command_status: $command_status,
          matrix_status: $matrix_status
        }' >"$pending_tmp"
    chmod 600 "$pending_tmp"
    mv -- "$pending_tmp" "$pending"
    printf 'PENDING_ATTEMPT profile=%s run=%s evidence=%s\n' \
        "$profile" "$run_id" "$diagnostic_dir"
    exit "$status"
}
trap cleanup EXIT

preflight_dir="$(< "$state_dir/preflight-summary-path")"
case "$preflight_dir" in
    /tmp/llm-env-sampler-probe.*) ;;
    *) printf '%s\n' 'invalid sampler preflight directory' >&2; exit 1 ;;
esac
summary="$preflight_dir/assertion-summary.json"
current_harness_sha256="$(sha256sum "${repo}/scripts/check-with-agents.sh")"
current_harness_sha256="${current_harness_sha256%% *}"
definition_harness_sha256="$(< "$state_dir/preflight-definition-harness-sha256")"
summary_harness_sha256="$(jq -er '.harness_sha256' "$summary")"
if [ "$current_harness_sha256" != "$definition_harness_sha256" ] \
    || [ "$current_harness_sha256" != "$summary_harness_sha256" ]; then
    printf '%s\n' \
        'agent harness changed: stop, revalidate the preflight definitions against the new harness, and review the revised plan before retrying' >&2
    stage=harness-drift
    exit 78
fi
if ! jq -e \
    --arg pi_version "$(pi --version)" \
    --arg opencode_version "$(opencode --version)" \
    '.pi_version == $pi_version
     and .opencode_version == $opencode_version
     and .assertions.pi_tool_continuation
     and .assertions.opencode_tool_continuation
     and .assertions.all_models_are_gemma4
     and .assertions.all_sampler_fields_omitted' \
    "$summary" >/dev/null; then
    printf '%s\n' \
        'client preflight drift: rerun preflight.sh, then retry this same run ID' >&2
    stage=client-drift
    exit 75
fi

port="$(yq -r '.server.port' "$CONFIG_PATH")"
[[ "$port" =~ ^[0-9]+$ ]] && [ "$port" -ge 1 ] && [ "$port" -le 65535 ] \
    || { stage=server-port; exit 1; }
config_baseline_hash() {
    local hash
    hash="$(
      yq -o=json '
        (.models[] | select(.alias == "gemma4")) |= del(.sampling)
      ' "$CONFIG_PATH" |
        jq -cS '.' |
        sha256sum
    )"
    printf '%s\n' "${hash%% *}"
}
expected_baseline_hash="$(< "$state_dir/config-without-sampling.sha256")"
[ "$(config_baseline_hash)" = "$expected_baseline_hash" ] || {
    stage=config-baseline
    exit 1
}
yq -e '
  [.models[]
   | select(.alias == "gemma4"
            and .label == "Gemma 4 12B Agentic v2"
            and .file == "gemma4-v2-Q4_K_M.gguf"
            and .url == "https://huggingface.co/yuxinlu1/gemma-4-12B-agentic-fable5-composer2.5-v2-3.5x-tau2-GGUF/resolve/190a31365a6b80a692349be34ccdac730cad4fe4/gemma4-v2-Q4_K_M.gguf"
            and .size_bytes == 7381381664)]
  | length == 1
' "$CONFIG_PATH" >/dev/null || { stage=agentic-prerequisite; exit 1; }

stage=configuration
make -C "$repo" stop
config_mutated=1
TEMPERATURE="$temperature" \
yq -i '
  (.models[] | select(.alias == "gemma4")).sampling = {
      "temperature": (strenv(TEMPERATURE) | tonumber),
      "top_p": 0.95,
      "top_k": 64,
      "repeat_penalty": 1.1
  }
' "$CONFIG_PATH"
chmod 600 "$CONFIG_PATH"
uv run llmenv.py models list --config "$CONFIG_PATH" >/dev/null
[ "$(config_baseline_hash)" = "$expected_baseline_hash" ] || {
    stage=config-invariant
    exit 1
}
safe_projection="$(yq -o=json '
  .models[]
  | select(.alias == "gemma4")
  | {
      "alias": .alias,
      "file": .file,
      "ctx_size": .ctx_size,
      "client_max_output_tokens": .client_max_output_tokens,
      "n_gpu_layers": .n_gpu_layers,
      "sampling": .sampling
    }
' "$CONFIG_PATH" | jq -cS '.')"
actual_fingerprint="$(sha256sum <<<"$safe_projection")"
actual_fingerprint="${actual_fingerprint%% *}"
[ "$actual_fingerprint" = "$fingerprint" ] || { stage=config-fingerprint; exit 1; }
stage=startup
make -C "$repo" start

stage=model-metadata
curl -fsS --max-time 10 "http://127.0.0.1:${port}/v1/models" |
    jq -e --arg temperature "$temperature" '
      [.data[] | select(.id == "gemma4")] as $gemma
      | ($gemma | length) == 1
      and ($gemma[0].status.args | index("/models/gemma4-v2-Q4_K_M.gguf")) != null
      and ($gemma[0].status.args | index("131072")) != null
      and ($gemma[0].status.args | index("q5_1")) != null
      and ($gemma[0].status.preset | contains("temp = \($temperature)\n"))
      and ($gemma[0].status.preset | contains("top-p = 0.95\n"))
      and ($gemma[0].status.preset | contains("top-k = 64\n"))
      and ($gemma[0].status.preset | contains("repeat-penalty = 1.1\n"))
    ' >/dev/null

stage=effective-sampling
curl -fsS --max-time 120 \
    --config <(yq -r '"header = \"Authorization: Bearer " + .server.api_key + "\""' "$CONFIG_PATH") \
    -H 'Content-Type: application/json' \
    --data-binary @<(jq -n '{
      model: "gemma4",
      messages: [{role: "user", content: "Explain deterministic finite automata in detail with examples."}],
      max_tokens: 512,
      stream: false
    }') \
    "http://127.0.0.1:${port}/v1/chat/completions" >/dev/null &
request_pid=$!
slot_observed=0
for _ in {1..100}; do
    slot_json="$(curl -fsS --max-time 10 \
        --config <(yq -r '"header = \"Authorization: Bearer " + .server.api_key + "\""' "$CONFIG_PATH") \
        "http://127.0.0.1:${port}/slots?model=gemma4")"
    if jq -e '.[0].is_processing == true' >/dev/null <<<"$slot_json"; then
        jq '.[0].params | {
          temperature,
          top_p,
          top_k,
          repeat_penalty
        }' <<<"$slot_json" >"$diagnostic_dir/effective-sampling.json"
        chmod 600 "$diagnostic_dir/effective-sampling.json"
        slot_observed=1
        break
    fi
    sleep 0.1
done
wait "$request_pid"
request_pid=""
[ "$slot_observed" -eq 1 ] || { printf '%s\n' 'active slot was not observed' >&2; exit 1; }
jq -e \
    --argjson temperature "$temperature" \
    '.temperature == $temperature
     and ((.top_p - 0.95) | fabs) < 0.000001
     and .top_k == 64
     and ((.repeat_penalty - 1.1) | fabs) < 0.000001' \
    "$diagnostic_dir/effective-sampling.json" >/dev/null

stage=agent-matrix
set +e
make -C "$repo" check-with-agents >"$diagnostic_dir/matrix.log" 2>&1
matrix_status=$?
set -e
chmod 600 "$diagnostic_dir/matrix.log"
[ "$(config_baseline_hash)" = "$expected_baseline_hash" ] || {
    stage=config-post-run
    observed=runner-error
    exit 1
}

if jq -Rse '
    def count($prefix): [split("\n")[] | select(startswith($prefix))] | length;
    count("PASS client=pi model=gemma4 check=weather ") == 1
    and count("PASS client=pi model=gemma4 check=fx ") == 1
    and count("PASS client=opencode model=gemma4 check=weather ") == 1
    and count("PASS client=opencode model=gemma4 check=fx ") == 1
    and count("SKIP client=pi ") == 0
    and count("SKIP client=opencode ") == 0
' "$diagnostic_dir/matrix.log" >/dev/null; then
    observed=gemma4-pass
    stage=complete
    printf 'OBSERVED_PASS profile=%s run=%s matrix_status=%s\n' \
        "$profile" "$run_id" "$matrix_status"
    exit 0
fi

observed=gemma4-nonpass
printf 'REVIEW_FAILURE profile=%s run=%s matrix_status=%s\n' \
    "$profile" "$run_id" "$matrix_status" >&2
exit 1
```

Run:

```bash
state_dir="$HOME/.local/state/llm-env/sampler-experiment"
chmod 600 "$state_dir/candidate.sh"
bash -n "$state_dir/candidate.sh"
shellcheck -s bash -x -P /var/home/bazzite/git/llm-env \
  "$state_dir/candidate.sh"
```

- [ ] **Step 3: Create the atomic attempt classifier and ledger**

Use `apply_patch` to create
`/home/bazzite/.local/state/llm-env/sampler-experiment/classify.sh` with this
complete content:

```bash
#!/usr/bin/env bash
set -euo pipefail
set +x
umask 077

[ "$#" -eq 1 ] || {
    printf '%s\n' 'usage: classify pass|model-failure|infra-invalid|candidate-regression' >&2
    exit 64
}
classification="$1"
case "$classification" in
    pass|model-failure|infra-invalid|candidate-regression) ;;
    *) printf '%s\n' "invalid classification: $classification" >&2; exit 64 ;;
esac

state_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
pending="$state_dir/pending-attempt.json"
ledger="$state_dir/ledger.json"
test -f "$pending" && test ! -L "$pending"
test -f "$ledger" && test ! -L "$ledger"
test "$(stat -c %a "$pending")" = 600
test "$(stat -c %a "$ledger")" = 600
attempt_id="$(jq -er '.attempt_id' "$pending")"
jq -e '
  (.attempt_id | test("^llm-env-sampler-round\\.[A-Za-z0-9]+$"))
  and (.fingerprint | test("^[0-9a-f]{64}$"))
' "$pending" >/dev/null
if jq -e --arg attempt_id "$attempt_id" \
    'any(.[]; .attempt_id == $attempt_id)' "$ledger" >/dev/null; then
    jq -e \
        --arg attempt_id "$attempt_id" \
        --arg classification "$classification" \
        'any(.[];
          .attempt_id == $attempt_id
          and .classification == $classification)' \
        "$ledger" >/dev/null
    rm -f -- "$pending"
    jq -c --arg attempt_id "$attempt_id" \
        '.[] | select(.attempt_id == $attempt_id)' "$ledger"
    exit 0
fi
evidence_dir="$(jq -er '.evidence_dir' "$pending")"
case "$evidence_dir" in
    /tmp/llm-env-sampler-round.*) ;;
    *) printf '%s\n' 'invalid sampler evidence directory' >&2; exit 1 ;;
esac
test -d "$evidence_dir" && test ! -L "$evidence_dir"
test "$(stat -c %a "$evidence_dir")" = 700

if [ "$classification" = pass ]; then
    jq -e '
      .observed == "gemma4-pass"
      and (.fingerprint | length) == 64
      and .stage == "complete"
    ' "$pending" >/dev/null
fi
if [ "$classification" = model-failure ]; then
    jq -e '
      .observed == "gemma4-nonpass"
      and .stage == "agent-matrix"
      and (.fingerprint | length) == 64
    ' "$pending" >/dev/null
fi
if [ "$classification" = candidate-regression ]; then
    jq -e '
      .observed == "runner-error"
      and .stage != "preflight"
      and .stage != "client-drift"
      and .stage != "harness-drift"
      and (.fingerprint | length) == 64
    ' "$pending" >/dev/null
fi

entry_tmp="$(mktemp "$state_dir/.classified-entry.XXXXXX")"
jq \
    --arg classification "$classification" \
    --arg classified_at "$(date -Iseconds)" \
    '. + {
      classification: $classification,
      classified_at: $classified_at
    }' "$pending" >"$entry_tmp"
chmod 600 "$entry_tmp"

ledger_tmp="$(mktemp "$state_dir/.ledger.XXXXXX")"
jq --slurpfile entry "$entry_tmp" '. + $entry' "$ledger" >"$ledger_tmp"
chmod 600 "$ledger_tmp"
mv -- "$ledger_tmp" "$ledger"
rm -f -- "$entry_tmp" "$pending"
jq -c 'last | {
  profile,
  run_id,
  classification,
  fingerprint,
  evidence_dir
}' "$ledger"
```

Create the ledger atomically and validate both scripts:

```bash
state_dir="$HOME/.local/state/llm-env/sampler-experiment"
chmod 600 "$state_dir/classify.sh"
ledger_tmp="$(mktemp "$state_dir/.ledger.XXXXXX")"
printf '%s\n' '[]' >"$ledger_tmp"
chmod 600 "$ledger_tmp"
mv -- "$ledger_tmp" "$state_dir/ledger.json"
bash -n "$state_dir/classify.sh"
shellcheck -s bash "$state_dir/classify.sh"
```

Expected: no attempt can start while `pending-attempt.json` exists. The
candidate runner finalizes its redacted evidence first, then atomically creates
one pending record. `classify.sh` atomically appends exactly one classification
and removes the pending record before another attempt can run.

- [ ] **Step 4: Screen both candidates in fixed order**

Run candidate 1, then candidate 2 even if candidate 1 passes:

```bash
bash "$HOME/.local/state/llm-env/sampler-experiment/candidate.sh" \
  publisher screening-1
```

Inspect the finalized evidence and classify it with exactly one of:

```bash
bash "$HOME/.local/state/llm-env/sampler-experiment/classify.sh" pass
bash "$HOME/.local/state/llm-env/sampler-experiment/classify.sh" model-failure
bash "$HOME/.local/state/llm-env/sampler-experiment/classify.sh" infra-invalid
bash "$HOME/.local/state/llm-env/sampler-experiment/classify.sh" candidate-regression
```

Run only the one command matching the evidence. If infrastructure-invalid,
rerun publisher with the same `screening-1` run ID after repair and classify
the new attempt. Once publisher has a non-infrastructure classification, run
and classify greedy the same way:

```bash
bash "$HOME/.local/state/llm-env/sampler-experiment/candidate.sh" \
  greedy screening-1
```

If the pending record has `stage: "client-drift"`, classify it
`infra-invalid`, rerun the retained preflight atomically, and retry the same
profile and run ID without advancing qualification:

```bash
state_dir="$HOME/.local/state/llm-env/sampler-experiment"
bash "$state_dir/classify.sh" infra-invalid
bash "$state_dir/preflight.sh"
test -f "$state_dir/preflight-summary-path"
```

If the stage is `harness-drift`, classify the attempt `infra-invalid` and stop.
Do not rerun the retained preflight: revise its provider definitions, CLI
arguments, isolation, and prompt against the changed `run_agent()` code; rerun
`review-spec`; then atomically replace
`preflight-definition-harness-sha256` with the reviewed harness hash before a
new preflight. This prevents stale hardcoded definitions from certifying a new
harness.

After that revised plan is approved, pin and test its definitions with:

```bash
state_dir="$HOME/.local/state/llm-env/sampler-experiment"
repo=/var/home/bazzite/git/llm-env
definition_tmp="$(mktemp "$state_dir/.harness-definition.XXXXXX")"
harness_sha256="$(sha256sum "${repo}/scripts/check-with-agents.sh")"
printf '%s\n' "${harness_sha256%% *}" >"$definition_tmp"
chmod 600 "$definition_tmp"
mv -- "$definition_tmp" "$state_dir/preflight-definition-harness-sha256"
node --check "$state_dir/proxy.mjs"
bash -n "$state_dir/preflight.sh"
shellcheck -s bash -x -P /var/home/bazzite/git/llm-env \
  "$state_dir/preflight.sh"
bash "$state_dir/preflight.sh"
```

For each run:

| Evidence | Classification | Action |
|---|---|---|
| `OBSERVED_PASS` and exactly the four required `gemma4` `PASS` identities | `pass` | Candidate may enter qualification; failures from additional aliases do not affect this classification. |
| Required model invoked; diagnostics show changed commands, extra requests, invalid final JSON, altered source values, non-convergence, wrong source URL/timestamp, or failed generated actions | Valid model-behavior failure | Reject candidate. |
| Source fetch/parser failure before model invocation, missing client, API/credential connectivity failure, client crash before usable model response, transcript capture failure, or harness parser failure | Infrastructure-invalid | Repair infrastructure and rerun the same candidate without changing its profile. |
| Evidence cannot distinguish infrastructure from model behavior | Infrastructure-invalid | Rerun the same candidate after diagnosis. |
| Reproducible validation, preset, startup, or API regression caused by the candidate | Candidate failure | Reject candidate. |

Expected: both candidates have one non-infrastructure screening classification
in `ledger.json`. Preserve all finalized redacted evidence directories.

- [ ] **Step 5: Qualify the first screening candidate**

If publisher passed screening, run:

```bash
bash "$HOME/.local/state/llm-env/sampler-experiment/candidate.sh" \
  publisher qualification-1
```

Classify the finalized pending attempt. An infrastructure-invalid result is
recorded and rerun with the same run ID; a model failure or candidate
regression rejects publisher. After a `pass`, run:

```bash
bash "$HOME/.local/state/llm-env/sampler-experiment/candidate.sh" \
  publisher qualification-2
```

Apply the same classification rule. After a second `pass`, run:

```bash
bash "$HOME/.local/state/llm-env/sampler-experiment/candidate.sh" \
  publisher qualification-3
```

Count only three consecutive ledger classifications of `pass`. If an
infrastructure-invalid run occurs, record it and repeat that same qualification
number after repair; it neither advances nor resets the streak. If a valid
model-behavior failure occurs, reject publisher immediately and continue to
Step 6.

If all three publisher rounds pass, run:

```bash
state_dir="$HOME/.local/state/llm-env/sampler-experiment"
selection_tmp="$(mktemp "$state_dir/.selected-profile.XXXXXX")"
printf '%s\n' publisher >"$selection_tmp"
chmod 600 "$selection_tmp"
mv -- "$selection_tmp" "$state_dir/selected-profile"
```

Expected: publisher is selected and remains active. Skip Step 6.

- [ ] **Step 6: Qualify greedy only if publisher does not qualify**

This step is allowed only when greedy passed screening and publisher was rejected. Run:

```bash
bash "$HOME/.local/state/llm-env/sampler-experiment/candidate.sh" \
  greedy qualification-1
```

Classify the finalized attempt. After a `pass`, run:

```bash
bash "$HOME/.local/state/llm-env/sampler-experiment/candidate.sh" \
  greedy qualification-2
```

After classification and a second `pass`, run:

```bash
bash "$HOME/.local/state/llm-env/sampler-experiment/candidate.sh" \
  greedy qualification-3
```

Apply the same invalid-run and model-failure rules. If all three rounds pass, run:

```bash
state_dir="$HOME/.local/state/llm-env/sampler-experiment"
selection_tmp="$(mktemp "$state_dir/.selected-profile.XXXXXX")"
printf '%s\n' greedy >"$selection_tmp"
chmod 600 "$selection_tmp"
mv -- "$selection_tmp" "$state_dir/selected-profile"
```

Expected: greedy is selected and remains active.

- [ ] **Step 7: Roll back if no candidate qualifies; otherwise verify selection**

If neither candidate qualifies, run and stop execution:

```bash
set -euo pipefail
repo=/var/home/bazzite/git/llm-env
source "${repo}/tools/lib.sh"
make -C "$repo" stop
rollback_dir="$(< /tmp/llm-env-gemma-rollback.path)"
case "$rollback_dir" in
  /tmp/llm-env-gemma-rollback.*) ;;
  *) printf '%s\n' 'invalid rollback directory' >&2; exit 1 ;;
esac
install -m 600 "$rollback_dir/models.yml" "$CONFIG_PATH"
make -C "$repo" start
make -C "$repo" check-server
```

If a candidate qualifies, run:

```bash
state_dir="$HOME/.local/state/llm-env/sampler-experiment"
selected="$(< "$state_dir/selected-profile")"
case "$selected" in
  publisher) expected_temperature=1.0 ;;
  greedy) expected_temperature=0.0 ;;
  *) printf '%s\n' 'invalid selected sampler profile' >&2; exit 1 ;;
esac
EXPECTED_TEMPERATURE="$expected_temperature" yq -e \
  '[.models[]
    | select(.alias == "gemma4"
             and .file == "gemma4-v2-Q4_K_M.gguf"
             and (.sampling | length) == 4
             and .sampling.temperature == (strenv(EXPECTED_TEMPERATURE) | tonumber)
             and .sampling.top_p == 0.95
             and .sampling.top_k == 64
             and .sampling.repeat_penalty == 1.1)]
   | length == 1' \
  "$CONFIG_PATH" >/dev/null
test ! -e "$state_dir/pending-attempt.json"
jq -e '
  (map(.attempt_id) | length) == (map(.attempt_id) | unique | length)
  and all(.[]; (.fingerprint | test("^[0-9a-f]{64}$")))
' "$state_dir/ledger.json" >/dev/null
jq -e --arg selected "$selected" '
  [to_entries[]
   | select(.value.profile == "publisher"
            and .value.run_id == "screening-1"
            and .value.classification != "infra-invalid")
   | .key] as $publisher_screen
  | [to_entries[]
     | select(.value.profile == "greedy"
              and .value.run_id == "screening-1"
              and .value.classification != "infra-invalid")
     | .key] as $greedy_screen
  | [to_entries[]
     | select(.value.profile == $selected
              and .value.run_id == "qualification-1"
              and .value.classification != "infra-invalid")
     | .key] as $qualification_1
  | [to_entries[]
     | select(.value.profile == $selected
              and .value.run_id == "qualification-2"
              and .value.classification != "infra-invalid")
     | .key] as $qualification_2
  | [to_entries[]
     | select(.value.profile == $selected
              and .value.run_id == "qualification-3"
              and .value.classification != "infra-invalid")
     | .key] as $qualification_3
  | [to_entries[]
     | select(.value.profile == "publisher"
              and (.value.classification == "model-failure"
                   or .value.classification == "candidate-regression"))
     | .key] as $publisher_rejection
  | [to_entries[]
     | select((.value.run_id | startswith("qualification-"))
              and .value.classification != "infra-invalid")
     | .key] as $all_qualification
  | (if $selected == "publisher"
     then $publisher_screen[0]
     else $greedy_screen[0]
     end) as $selected_screen
  | ($publisher_screen | length) == 1
  and ($greedy_screen | length) == 1
  and ($qualification_1 | length) == 1
  and ($qualification_2 | length) == 1
  and ($qualification_3 | length) == 1
  and ($all_qualification | length) >= 3
  and $publisher_screen[0] < $greedy_screen[0]
  and $greedy_screen[0] < ($all_qualification | min)
  and $greedy_screen[0] < $qualification_1[0]
  and $qualification_1[0] < $qualification_2[0]
  and $qualification_2[0] < $qualification_3[0]
  and .[$selected_screen].classification == "pass"
  and .[$qualification_1[0]].classification == "pass"
  and .[$qualification_2[0]].classification == "pass"
  and .[$qualification_3[0]].classification == "pass"
  and ([.[]
        | select(.profile == $selected
                 and (.run_id | startswith("qualification-"))
                 and .classification != "infra-invalid")]
       | length) == 3
  and ([.[]
        | select(.profile == $selected and .classification == "pass")
        | .fingerprint]
       | unique | length) == 1
  and (
    $selected == "publisher"
    or (
      ($publisher_rejection | length) >= 1
      and ($publisher_rejection | min) < $qualification_1[0]
    )
  )
' "$state_dir/ledger.json" >/dev/null
while IFS= read -r evidence_dir; do
  case "$evidence_dir" in /tmp/llm-env-sampler-round.*) ;; *) exit 1 ;; esac
  test -d "$evidence_dir" && test ! -L "$evidence_dir"
  test "$(stat -c %a "$evidence_dir")" = 700
done < <(jq -r '.[].evidence_dir' "$state_dir/ledger.json")
systemctl --user is-active llm-server.service
```

Expected: the selected literal profile is active and the service reports `active`. This task changes no tracked files and creates no commit.

### Task 5: Publish the Qualified Default

**Files:**
- Modify: `models.yml.example:34-58`
- Modify: `tests/test_cli.py:245-272`

**Interfaces:**
- Consumes: `~/.local/state/llm-env/sampler-experiment/selected-profile` from Task 4.
- Produces: a clean-setup Gemma record containing the exact qualified profile while Ornith remains sampler-free.

- [ ] **Step 1: Add the failing clean-setup assertion for the selected branch**

Read the selected profile:

```bash
selected="$(< "$HOME/.local/state/llm-env/sampler-experiment/selected-profile")"
case "$selected" in publisher|greedy) ;; *) exit 1 ;; esac
```

In `test_default_config_uses_agentic_gemma_q4()`, add:

```python
    ornith = next(model for model in parsed["models"] if model["alias"] == "ornith")
```

If `selected` is `publisher`, add these assertions after the existing Gemma projection assertion:

```python
    assert gemma["sampling"] == {
        "temperature": 1.0,
        "top_p": 0.95,
        "top_k": 64,
        "repeat_penalty": 1.1,
    }
    assert "sampling" not in ornith
```

If `selected` is `greedy`, add instead:

```python
    assert gemma["sampling"] == {
        "temperature": 0.0,
        "top_p": 0.95,
        "top_k": 64,
        "repeat_penalty": 1.1,
    }
    assert "sampling" not in ornith
```

- [ ] **Step 2: Run the clean-setup test and verify it fails**

Run:

```bash
uv run --with pytest pytest tests/test_cli.py::test_default_config_uses_agentic_gemma_q4 -v
```

Expected: FAIL because `models.yml.example` does not yet contain `sampling`.

- [ ] **Step 3: Add the selected literal mapping to the sample config**

If publisher was selected, add after `n_gpu_layers: 99` in the `gemma4` record:

```yaml
    sampling:
      temperature: 1.0
      top_p: 0.95
      top_k: 64
      repeat_penalty: 1.1
```

If greedy was selected, add instead:

```yaml
    sampling:
      temperature: 0.0
      top_p: 0.95
      top_k: 64
      repeat_penalty: 1.1
```

Do not add `sampling` to the Ornith record.

- [ ] **Step 4: Run focused and full verification**

Run:

```bash
uv run --with pytest pytest tests/test_cli.py::test_default_config_uses_agentic_gemma_q4 -v
make validate
make test
```

Expected: the focused clean-setup test passes; validation and the full test suite pass.

- [ ] **Step 5: Commit the qualified default**

```bash
git add models.yml.example tests/test_cli.py
git commit -m "feat(models): configure qualified Gemma samplers"
```

### Task 6: Run Final Gates and Remove Rollback Assets

**Files:**
- Refresh privately: `~/.pi/agent/models.json`, `~/.pi/agent/settings.json`
- Refresh privately: `~/.config/opencode/opencode.jsonc`, `~/.local/state/opencode/model.json`
- Delete after all gates: `~/llm-workspace/models/gemma-4-12B-it-Q4_K_M.gguf`
- Delete after all gates: `/tmp/llm-env-gemma-rollback.*`, `/tmp/llm-env-gemma-rollback.path`
- Delete after all gates: `/tmp/llm-env-sampler-probe.*`
- Delete after all gates: recorded `/tmp/llm-env-sampler-round.*` evidence directories
- Delete after all gates: `~/.local/state/llm-env/sampler-experiment/`

**Interfaces:**
- Consumes: selected active private profile, three valid qualification rounds, and matching tracked clean-setup defaults.
- Produces: active agentic `gemma4`, passing final repository/service/agent gates, and no obsolete base GGUF or rollback secrets.

- [ ] **Step 1: Verify the API and long-context contract**

Run:

```bash
make check-server
repo=/var/home/bazzite/git/llm-env
source "${repo}/tools/lib.sh"
port="$(yq -r '.server.port' "$CONFIG_PATH")"
curl --fail-with-body --silent --show-error --max-time 300 \
  -K <(yq -r '"header = \"Authorization: Bearer " + .server.api_key + "\""' "$CONFIG_PATH") \
  -H 'Content-Type: application/json' \
  --data-binary @<(jq -n '{model:"gemma4",messages:[{role:"user",content:("hello " * 12000)}],max_tokens:1,stream:false}') \
  "http://127.0.0.1:${port}/v1/chat/completions" |
  jq -e 'select((.choices | length) == 1 and .usage.prompt_tokens > 8192)
         | {prompt_tokens: .usage.prompt_tokens, completion_tokens: .usage.completion_tokens}'
```

Expected: `make check-server` passes all four checks; the completion succeeds with more than `8192` prompt tokens without printing the API key.

- [ ] **Step 2: Refresh and verify normal client metadata**

Run:

```bash
make setup-local-llm-agents
jq -e '
  [.providers["local-llm-env"].models[]
   | select(.id == "gemma4"
            and .contextWindow == 131072
            and .maxTokens == 8192)]
  | length == 1
' "$HOME/.pi/agent/models.json" >/dev/null
jq -e '
  .provider["local-llm-env"].models.gemma4.limit ==
    {"context":131072,"output":8192}
' "$HOME/.config/opencode/opencode.jsonc" >/dev/null
```

Expected: both credential-free metadata projections exit `0`; no sampler settings are added to client profiles.

- [ ] **Step 3: Run the final unchanged all-alias agent matrix**

Run:

```bash
set -euo pipefail
state_dir="$HOME/.local/state/llm-env/sampler-experiment"
final_log="$(mktemp "$state_dir/.final-agent-matrix.XXXXXX")"
chmod 600 "$final_log"
set +e
LLM_ENV_KEEP_CHECK_ARTIFACTS=1 make check-with-agents >"$final_log" 2>&1
matrix_status=$?
set -e
test "$matrix_status" -eq 0
jq -Rse '
  def count($prefix): [split("\n")[] | select(startswith($prefix))] | length;
  count("PASS client=pi model=gemma4 check=weather ") == 1
  and count("PASS client=pi model=gemma4 check=fx ") == 1
  and count("PASS client=opencode model=gemma4 check=weather ") == 1
  and count("PASS client=opencode model=gemma4 check=fx ") == 1
  and count("SKIP client=") == 0
  and count("FAIL client=") == 0
' "$final_log" >/dev/null
```

Expected: no client is skipped; every weather and `fx` cell for every alias returned by `/v1/models` passes. For `gemma4`, the output contains exactly one of each required machine identity and no `FAIL` line.

- [ ] **Step 4: Run final repository and service verification**

Run:

```bash
make validate
make test
git diff --check
systemctl --user is-active llm-server.service
repo=/var/home/bazzite/git/llm-env
source "${repo}/tools/lib.sh"
state_dir="$HOME/.local/state/llm-env/sampler-experiment"
selected="$(< "$state_dir/selected-profile")"
case "$selected" in
  publisher) expected_temperature=1.0 ;;
  greedy) expected_temperature=0.0 ;;
  *) printf '%s\n' 'invalid selected sampler profile' >&2; exit 1 ;;
esac
for config in "$CONFIG_PATH" "$repo/models.yml.example"; do
  EXPECTED_TEMPERATURE="$expected_temperature" yq -e '
    [.models[]
     | select(.alias == "gemma4"
              and (.sampling | length) == 4
              and .sampling.temperature == (strenv(EXPECTED_TEMPERATURE) | tonumber)
              and .sampling.top_p == 0.95
              and .sampling.top_k == 64
              and .sampling.repeat_penalty == 1.1)]
    | length == 1
  ' "$config" >/dev/null
  yq -e '
    [.models[] | select(.alias == "ornith" and has("sampling"))]
    | length == 0
  ' "$config" >/dev/null
done
port="$(yq -r '.server.port' "$CONFIG_PATH")"
curl -fsS --max-time 10 "http://127.0.0.1:${port}/v1/models" |
  jq -e --arg temperature "$expected_temperature" '
    [.data[] | select(.id == "gemma4")] as $gemma
    | ($gemma | length) == 1
    and ($gemma[0].status.args | index("/models/gemma4-v2-Q4_K_M.gguf")) != null
    and ($gemma[0].status.args | index("131072")) != null
    and ($gemma[0].status.args | index("q5_1")) != null
    and ($gemma[0].status.preset | contains("temp = \($temperature)\n"))
    and ($gemma[0].status.preset | contains("top-p = 0.95\n"))
    and ($gemma[0].status.preset | contains("top-k = 64\n"))
    and ($gemma[0].status.preset | contains("repeat-penalty = 1.1\n"))
  ' >/dev/null
test ! -e "$state_dir/pending-attempt.json"
jq -e '
  (map(.attempt_id) | length) == (map(.attempt_id) | unique | length)
  and all(.[]; (.fingerprint | test("^[0-9a-f]{64}$")))
' "$state_dir/ledger.json" >/dev/null
jq -e --arg selected "$selected" '
  [to_entries[]
   | select(.value.profile == "publisher"
            and .value.run_id == "screening-1"
            and .value.classification != "infra-invalid")
   | .key] as $publisher_screen
  | [to_entries[]
     | select(.value.profile == "greedy"
              and .value.run_id == "screening-1"
              and .value.classification != "infra-invalid")
     | .key] as $greedy_screen
  | [to_entries[]
     | select(.value.profile == $selected
              and .value.run_id == "qualification-1"
              and .value.classification != "infra-invalid")
     | .key] as $qualification_1
  | [to_entries[]
     | select(.value.profile == $selected
              and .value.run_id == "qualification-2"
              and .value.classification != "infra-invalid")
     | .key] as $qualification_2
  | [to_entries[]
     | select(.value.profile == $selected
              and .value.run_id == "qualification-3"
              and .value.classification != "infra-invalid")
     | .key] as $qualification_3
  | [to_entries[]
     | select(.value.profile == "publisher"
              and (.value.classification == "model-failure"
                   or .value.classification == "candidate-regression"))
     | .key] as $publisher_rejection
  | [to_entries[]
     | select((.value.run_id | startswith("qualification-"))
              and .value.classification != "infra-invalid")
     | .key] as $all_qualification
  | (if $selected == "publisher"
     then $publisher_screen[0]
     else $greedy_screen[0]
     end) as $selected_screen
  | ($publisher_screen | length) == 1
  and ($greedy_screen | length) == 1
  and ($qualification_1 | length) == 1
  and ($qualification_2 | length) == 1
  and ($qualification_3 | length) == 1
  and ($all_qualification | length) >= 3
  and $publisher_screen[0] < $greedy_screen[0]
  and $greedy_screen[0] < ($all_qualification | min)
  and $greedy_screen[0] < $qualification_1[0]
  and $qualification_1[0] < $qualification_2[0]
  and $qualification_2[0] < $qualification_3[0]
  and .[$selected_screen].classification == "pass"
  and .[$qualification_1[0]].classification == "pass"
  and .[$qualification_2[0]].classification == "pass"
  and .[$qualification_3[0]].classification == "pass"
  and ([.[]
        | select(.profile == $selected
                 and (.run_id | startswith("qualification-"))
                 and .classification != "infra-invalid")]
       | length) == 3
  and ([.[]
        | select(.profile == $selected and .classification == "pass")
        | .fingerprint]
       | unique | length) == 1
  and (
    $selected == "publisher"
    or (
      ($publisher_rejection | length) >= 1
      and ($publisher_rejection | min) < $qualification_1[0]
    )
  )
' "$state_dir/ledger.json" >/dev/null
```

Expected: validation, all tests, and diff check pass; the service is `active`; the expected agentic model, context, caches, and sampler arguments are reported.

- [ ] **Step 5: Use this rollback if any final gate fails**

If any of Steps 1-4 fails, run this branch before any destructive cleanup and
stop execution:

```bash
set -euo pipefail
repo=/var/home/bazzite/git/llm-env
source "${repo}/tools/lib.sh"
rollback_dir="$(< /tmp/llm-env-gemma-rollback.path)"
case "$rollback_dir" in
  /tmp/llm-env-gemma-rollback.*) ;;
  *) printf '%s\n' 'invalid rollback directory' >&2; exit 1 ;;
esac
test -d "$rollback_dir" && test ! -L "$rollback_dir"
test -f "$rollback_dir/models.yml" && test ! -L "$rollback_dir/models.yml"
make stop
install -m 600 "$rollback_dir/models.yml" "$CONFIG_PATH"
make start
make check-server
```

Expected: the base model returns to service and `make check-server` passes.
Keep both GGUF files and all rollback and qualification evidence, and do not
claim completion.

- [ ] **Step 6: Delete the obsolete base model and private rollback material**

Only after Steps 1-4 pass and the rollback branch is no longer needed, run this
single fail-fast cleanup script:

```bash
set -euo pipefail
repo=/var/home/bazzite/git/llm-env
source "${repo}/tools/lib.sh"
state_dir="$HOME/.local/state/llm-env/sampler-experiment"
old_model="$HOME/llm-workspace/models/gemma-4-12B-it-Q4_K_M.gguf"
new_model="$HOME/llm-workspace/models/gemma4-v2-Q4_K_M.gguf"
rollback_pointer=/tmp/llm-env-gemma-rollback.path

test -d "$state_dir" && test ! -L "$state_dir"
test "$(stat -c %a "$state_dir")" = 700
test -f "$rollback_pointer" && test ! -L "$rollback_pointer"
rollback_dir="$(< "$rollback_pointer")"
preflight_dir="$(< "$state_dir/preflight-summary-path")"
case "$rollback_dir" in
  /tmp/llm-env-gemma-rollback.*) ;;
  *) printf '%s\n' 'invalid rollback directory' >&2; exit 1 ;;
esac
case "$preflight_dir" in
  /tmp/llm-env-sampler-probe.*) ;;
  *) printf '%s\n' 'invalid sampler preflight directory' >&2; exit 1 ;;
esac
test -d "$rollback_dir" && test ! -L "$rollback_dir"
test -f "$rollback_dir/models.yml" && test ! -L "$rollback_dir/models.yml"
test -d "$preflight_dir" && test ! -L "$preflight_dir"
test -f "$old_model" && test -f "$new_model"
round_dirs_json="$(jq -ce '
  [.[].evidence_dir]
  | select(length > 0 and all(.[]; type == "string"))
' "$state_dir/ledger.json")"
round_dirs_output="$(jq -er '.[]' <<<"$round_dirs_json")"
mapfile -t round_dirs <<<"$round_dirs_output"
test "${#round_dirs[@]}" -gt 0
for round_dir in "${round_dirs[@]}"; do
  case "$round_dir" in
    /tmp/llm-env-sampler-round.*) ;;
    *) printf '%s\n' 'invalid sampler round directory' >&2; exit 1 ;;
  esac
  test -d "$round_dir" && test ! -L "$round_dir"
done
printf '%s  %s\n' \
  '0b9506cab36f7f818e34f9c0f5a3d6568d0b37100f3a3e1092e2eec3c4c96791' \
  "$new_model" | sha256sum --check --status -
systemctl --user is-active llm-server.service
port="$(yq -r '.server.port' "$CONFIG_PATH")"
curl -fsS --max-time 10 "http://127.0.0.1:${port}/v1/models" |
  jq -e '
    [.data[] | select(.id == "gemma4")] as $gemma
    | ($gemma | length) == 1
    and ($gemma[0].status.args | index("/models/gemma4-v2-Q4_K_M.gguf")) != null
  ' >/dev/null

rm -f -- "$old_model"
test ! -e "$old_model"
rm -rf -- "$rollback_dir" "$preflight_dir" "${round_dirs[@]}"
rm -f -- "$rollback_pointer"
rm -rf -- "$state_dir"
test ! -e "$rollback_dir"
test ! -e "$rollback_pointer"
test ! -e "$state_dir"
test -f "$new_model"
systemctl --user is-active llm-server.service
```

Expected: every path and the replacement checksum are validated before the old
GGUF is removed; the old GGUF is verified absent before rollback evidence is
deleted; the validated replacement remains and the service remains `active`.
