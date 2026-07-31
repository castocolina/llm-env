# Agentic Gemma Replacement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the default and active base Gemma 4 Q4 model with `yuxinlu1`'s agentic Gemma 4 12B v2 Q4_K_M artifact while preserving the `gemma4` alias, 131,072-token context, one-model residency, and existing clients.

**Architecture:** Keep the checked-in model record as the clean-setup source of truth and update the private active record separately. Download and validate the replacement while the old service remains available, then stop the old router and require `make check-setup` to pass before starting the replacement. Retain the old config and GGUF until online server, context, and agent checks pass.

**Tech Stack:** YAML, Python 3.12, pytest, Bash, jq, Mike Farah yq v4, curl, sha256sum, llama.cpp Vulkan router, rootless Podman, systemd user services.

## Global Constraints

- Use `gemma4-v2-Q4_K_M.gguf` from Hugging Face revision `190a31365a6b80a692349be34ccdac730cad4fe4`.
- The file must contain exactly `7,381,381,664` bytes and match SHA-256 `0b9506cab36f7f818e34f9c0f5a3d6568d0b37100f3a3e1092e2eec3c4c96791`.
- Preserve alias `gemma4`, `parameters: 12B`, `quantization: Q4_K_M`, `ctx_size: 131072`, `client_max_output_tokens: 8192`, and `n_gpu_layers: 99`.
- Preserve `runtime.models_max: 1`, one request slot, Q5_1 K/V caches, flash attention, disabled fitting, and disabled context shifting.
- Preserve the enabled-model selection and every unrelated private configuration value.
- Do not enable MTP, add sampler defaults, add CPU offload, or reduce context.
- Never print, stage, or pass the API key as a command argument. Rollback directories must use mode `0700`; rollback configs must use mode `0600`.
- Keep the old base GGUF until every offline and online acceptance check passes.
- Run unattended `make setup`, then stop the router and run `make check-setup` before `make start`.
- After editing a Python file, run `make validate && make test` as required by `AGENTS.md`.

---

## File Structure

| File | Responsibility |
| --- | --- |
| `models.yml.example` | Defines the agentic Gemma artifact installed by clean setup. |
| `tests/test_cli.py` | Pins the exact default label, file, URL, size, alias, and runtime limits. |
| `README.md` | Identifies the agentic model behind the stable `gemma4` alias. |
| `QUICK_START.md` | Describes the same clean-setup default for operators. |
| `tests/test_docs.py` | Prevents user-facing documentation from reverting to an unspecified base model. |
| `~/.config/llm-env/models.yml` | Private active configuration updated during deployment; never commit it. |
| `~/llm-workspace/models/` | Holds the old and new GGUF files until acceptance, then only the replacement. |

### Task 1: Replace the Checked-In Gemma Artifact

**Files:**
- Modify: `tests/test_cli.py:229-243`
- Modify: `tests/test_docs.py:109-126`
- Modify: `models.yml.example:33-45`
- Modify: `README.md:40-49`
- Modify: `QUICK_START.md:61-73`

**Interfaces:**
- Consumes: the existing `llmenv.py init --template models.yml.example` path.
- Produces: a `gemma4` record pointing to the pinned agentic Q4_K_M artifact, with unchanged runtime limits.

- [ ] **Step 1: Add a failing exact-artifact test**

Add this test after `test_default_config_uses_128k_q5_1_runtime` in `tests/test_cli.py`:

```python
def test_default_config_uses_agentic_gemma_q4(tmp_path):
    config = tmp_path / "models.yml"
    result = run("init", "--config", str(config), "--template", "models.yml.example")

    assert result.returncode == 0, result.stderr
    parsed = yaml.safe_load(config.read_text())
    gemma = next(model for model in parsed["models"] if model["alias"] == "gemma4")
    assert {
        "label": gemma["label"],
        "file": gemma["file"],
        "url": gemma["url"],
        "size_bytes": gemma["size_bytes"],
        "parameters": gemma["parameters"],
        "quantization": gemma["quantization"],
        "ctx_size": gemma["ctx_size"],
        "client_max_output_tokens": gemma["client_max_output_tokens"],
        "n_gpu_layers": gemma["n_gpu_layers"],
    } == {
        "label": "Gemma 4 12B Agentic v2",
        "file": "gemma4-v2-Q4_K_M.gguf",
        "url": "https://huggingface.co/yuxinlu1/gemma-4-12B-agentic-fable5-composer2.5-v2-3.5x-tau2-GGUF/resolve/190a31365a6b80a692349be34ccdac730cad4fe4/gemma4-v2-Q4_K_M.gguf",
        "size_bytes": 7381381664,
        "parameters": "12B",
        "quantization": "Q4_K_M",
        "ctx_size": 131072,
        "client_max_output_tokens": 8192,
        "n_gpu_layers": 99,
    }
```

- [ ] **Step 2: Add a failing documentation test**

Add this test after `test_docs_state_exact_runtime_token_limits` in `tests/test_docs.py`:

```python
def test_docs_identify_agentic_gemma_default() -> None:
    readme = _normalized_markdown(ROOT / "README.md").lower()
    quick_start = _normalized_markdown(ROOT / "QUICK_START.md").lower()

    for document in (readme, quick_start):
        assert "agentic gemma 4 12b v2" in document
        assert "`gemma4`" in document
```

- [ ] **Step 3: Run both tests to verify they fail for the old model**

Run:

```bash
uv run --with pytest pytest \
  tests/test_cli.py::test_default_config_uses_agentic_gemma_q4 \
  tests/test_docs.py::test_docs_identify_agentic_gemma_default -v
```

Expected: two failures. The config test reports the old Bartowski file, and the documentation test cannot find `agentic gemma 4 12b v2`.

- [ ] **Step 4: Replace the checked-in model record**

Change only the Gemma record in `models.yml.example` to:

```yaml
  - alias: gemma4
    label: Gemma 4 12B Agentic v2
    parameters: 12B
    quantization: Q4_K_M
    enabled: true
    file: gemma4-v2-Q4_K_M.gguf
    url: https://huggingface.co/yuxinlu1/gemma-4-12B-agentic-fable5-composer2.5-v2-3.5x-tau2-GGUF/resolve/190a31365a6b80a692349be34ccdac730cad4fe4/gemma4-v2-Q4_K_M.gguf
    size_bytes: 7381381664
    vram_budget: 55%
    ctx_size: 131072
    client_max_output_tokens: 8192
    n_gpu_layers: 99
```

Do not change the Ornith record or runtime block.

- [ ] **Step 5: Identify the new default in user documentation**

Replace the clean-setup paragraph in `README.md` with:

```markdown
Clean setup maps `gemma4` to yuxinlu1's Agentic Gemma 4 12B v2 Q4_K_M
build. Gemma and Ornith each receive one 131,072-token context and request slot
with Q5_1 K/V caches. Pi and OpenCode advertise up to 8,192 output tokens,
so reserving the full output allowance leaves a nominal 122,880 tokens for the
prompt and history. All tokens still share the same slot. Setup reports an
explicit VRAM-budget failure instead of shrinking context or offloading layers.
```

Replace the first paragraph about limits under `## Coding clients` in `QUICK_START.md` with:

```markdown
Clean setup maps `gemma4` to yuxinlu1's Agentic Gemma 4 12B v2 Q4_K_M
build. The model and client records use an exact 131,072-token context and
8,192-token output limit. Pi's global `enabledModels` becomes exactly the setup-selected
local aliases in setup order, which defines its model cycle.
```

- [ ] **Step 6: Run the focused tests to verify they pass**

Run the command from Step 3 again.

Expected: `2 passed`.

- [ ] **Step 7: Run repository validation**

Run:

```bash
make validate
make test
git diff --check
```

Expected: validation passes, all 386 tests pass, and `git diff --check` prints nothing.

- [ ] **Step 8: Review and commit the repository change**

Run:

```bash
git status --short
git diff
git add models.yml.example README.md QUICK_START.md tests/test_cli.py tests/test_docs.py
git diff --cached
gitleaks git --staged --redact --no-banner
git commit -m "feat(models): replace base Gemma with agentic v2"
```

Expected: only the five listed files are committed, and Gitleaks reports no leaks.

### Task 2: Download and Validate the Active Replacement

**Files:**
- Modify privately: `~/.config/llm-env/models.yml`
- Create temporarily: `/tmp/llm-env-gemma-rollback.*/models.yml`
- Create temporarily: `/tmp/llm-env-gemma-rollback.path`
- Download: `~/llm-workspace/models/gemma4-v2-Q4_K_M.gguf`

**Interfaces:**
- Consumes: the pinned artifact record from Task 1 and the existing `make setup`, `make stop`, and `make check-setup` workflows.
- Produces: a validated replacement GGUF and a stopped service ready to start from an unloaded VRAM baseline.

- [ ] **Step 1: Record the starting state without exposing credentials**

Run:

```bash
systemctl --user is-active llm-server.service
yq -o=json '.' "$HOME/.config/llm-env/models.yml" | jq '{runtime, models: [.models[] | {alias, label, enabled, file, url, size_bytes, ctx_size, client_max_output_tokens, n_gpu_layers}]}'
```

Expected: the service is `active`; the output contains no `server` object or API key; `gemma4` still points to `gemma-4-12B-it-Q4_K_M.gguf`.

- [ ] **Step 2: Create a private rollback copy**

Run:

```bash
rollback_dir="$(mktemp -d /tmp/llm-env-gemma-rollback.XXXXXX)"
chmod 700 "$rollback_dir"
install -m 600 "$HOME/.config/llm-env/models.yml" "$rollback_dir/models.yml"
printf '%s\n' "$rollback_dir" > /tmp/llm-env-gemma-rollback.path
chmod 600 /tmp/llm-env-gemma-rollback.path
```

Expected: the rollback directory has mode `0700`, and both files have mode `0600`.

- [ ] **Step 3: Update only the active Gemma record**

Run:

```bash
MODEL_LABEL='Gemma 4 12B Agentic v2' \
MODEL_FILE='gemma4-v2-Q4_K_M.gguf' \
MODEL_URL='https://huggingface.co/yuxinlu1/gemma-4-12B-agentic-fable5-composer2.5-v2-3.5x-tau2-GGUF/resolve/190a31365a6b80a692349be34ccdac730cad4fe4/gemma4-v2-Q4_K_M.gguf' \
MODEL_SIZE='7381381664' \
yq -i '
  (.models[] | select(.alias == "gemma4")) |= (
    .label = strenv(MODEL_LABEL) |
    .file = strenv(MODEL_FILE) |
    .url = strenv(MODEL_URL) |
    .size_bytes = (strenv(MODEL_SIZE) | tonumber)
  )
' "$HOME/.config/llm-env/models.yml"
chmod 600 "$HOME/.config/llm-env/models.yml"
```

Verify only non-secret fields:

```bash
yq -o=json '.' "$HOME/.config/llm-env/models.yml" | jq -e '
  .runtime.models_max == 1 and
  .runtime.parallel_slots == 1 and
  .runtime.cache_type_k == "q5_1" and
  .runtime.cache_type_v == "q5_1" and
  ([.models[] | select(.alias == "gemma4")][0] | {
    label, file, url, size_bytes, ctx_size, client_max_output_tokens, n_gpu_layers
  }) == {
    label: "Gemma 4 12B Agentic v2",
    file: "gemma4-v2-Q4_K_M.gguf",
    url: "https://huggingface.co/yuxinlu1/gemma-4-12B-agentic-fable5-composer2.5-v2-3.5x-tau2-GGUF/resolve/190a31365a6b80a692349be34ccdac730cad4fe4/gemma4-v2-Q4_K_M.gguf",
    size_bytes: 7381381664,
    ctx_size: 131072,
    client_max_output_tokens: 8192,
    n_gpu_layers: 99
  }
' >/dev/null
```

Expected: exit status `0`; enabled flags and unrelated fields remain unchanged.

- [ ] **Step 4: Run unattended setup to download and validate the new GGUF**

Run:

```bash
LLM_ENV_ASSUME_YES=1 make setup
```

Expected: setup selects the existing GPU and enabled aliases, downloads `gemma4-v2-Q4_K_M.gguf`, validates the GGUF, and exits `0`. Because the old router is still active, the final setup budget may warn about current VRAM use; the stopped-service gate in Step 6 is authoritative.

- [ ] **Step 5: Verify the published byte size and SHA-256**

Run:

```bash
new_model="$HOME/llm-workspace/models/gemma4-v2-Q4_K_M.gguf"
test "$(stat -c %s "$new_model")" = 7381381664
printf '%s  %s\n' \
  '0b9506cab36f7f818e34f9c0f5a3d6568d0b37100f3a3e1092e2eec3c4c96791' \
  "$new_model" | sha256sum --check -
```

Expected: `gemma4-v2-Q4_K_M.gguf: OK`.

- [ ] **Step 6: Stop the old router, then run the required offline gate**

Run these commands in this order:

```bash
make stop
make check-setup
```

Expected: the service stops before VRAM detection; configuration, GGUF validation, budget, Vulkan device resolution, and disposable `gemma4` inference all pass. The final summary reports zero failures.

- [ ] **Step 7: Inspect the authoritative stopped-service budget**

Run:

```bash
uv run llmenv.py budget --models-dir "$HOME/llm-workspace/models" | jq -e '
  .feasible == true and
  .models_max == 1 and
  .resident_models == [.models[] | select(.alias == "gemma4")]
' >/dev/null

uv run llmenv.py budget --models-dir "$HOME/llm-workspace/models" | jq '{
  available_mib,
  required_mib,
  resident_models: [.resident_models[] | {
    alias, weights_mib, full_kv_mib, swa_kv_mib, runtime_overhead_mib, required_mib
  }]
}'
```

Expected: the assertion exits `0`; the projection records the complete non-secret budget evidence.

- [ ] **Step 8: Use this rollback if any Task 2 step fails**

Run:

```bash
rollback_dir="$(< /tmp/llm-env-gemma-rollback.path)"
case "$rollback_dir" in
  /tmp/llm-env-gemma-rollback.*) ;;
  *) printf '%s\n' 'invalid rollback directory' >&2; exit 1 ;;
esac
new_model="$HOME/llm-workspace/models/gemma4-v2-Q4_K_M.gguf"
if [ -f "$new_model" ] && ! printf '%s  %s\n' \
  '0b9506cab36f7f818e34f9c0f5a3d6568d0b37100f3a3e1092e2eec3c4c96791' \
  "$new_model" | sha256sum --check --status -; then
  rm -f -- "$new_model"
fi
install -m 600 "$rollback_dir/models.yml" "$HOME/.config/llm-env/models.yml"
make start
```

Expected: `/v1/models` returns the base `gemma4` preset again. An invalid partial download is removed; a valid replacement remains available for diagnosis. Stop execution after rollback.

Task 2 changes no tracked files and creates no commit.

### Task 3: Activate, Verify, and Remove the Base GGUF

**Files:**
- Regenerate privately: `~/.config/llm-env/presets.ini`
- Refresh privately: `~/.pi/agent/models.json`, `~/.pi/agent/settings.json`
- Refresh privately: `~/.config/opencode/opencode.jsonc`, `~/.local/state/opencode/model.json`
- Delete after acceptance: `~/llm-workspace/models/gemma-4-12B-it-Q4_K_M.gguf`
- Delete after acceptance: `/tmp/llm-env-gemma-rollback.*`, `/tmp/llm-env-gemma-rollback.path`

**Interfaces:**
- Consumes: the stopped service and validated agentic GGUF from Task 2.
- Produces: an active one-resident `gemma4` route backed by the agentic model, synchronized clients, and no obsolete base GGUF.

- [ ] **Step 1: Start the replacement from the unloaded baseline**

Run:

```bash
make start
systemctl --user is-active llm-server.service
```

Expected: the budget passes, the generated preset points to `gemma4-v2-Q4_K_M.gguf`, health becomes ready, and the service reports `active`.

- [ ] **Step 2: Verify the online API contract**

Run:

```bash
make check-server
```

Expected: health, authentication, exact model listing, and the `gemma4` completion all pass with zero failures.

Verify the loaded preset without authentication:

```bash
curl -fsS --max-time 10 http://127.0.0.1:8000/v1/models | jq -e '
  (.data | length) == 1 and
  .data[0].id == "gemma4" and
  (.data[0].status.args | index("/models/gemma4-v2-Q4_K_M.gguf")) != null and
  (.data[0].status.args | index("131072")) != null and
  (.data[0].status.args | index("q5_1")) != null
' >/dev/null
```

Expected: exit status `0`.

- [ ] **Step 3: Verify a prompt above the former 8K limit**

Run:

```bash
curl --fail-with-body --silent --show-error --max-time 300 \
  -K <(yq -r '"header = \"Authorization: Bearer " + .server.api_key + "\""' "$HOME/.config/llm-env/models.yml") \
  -H 'Content-Type: application/json' \
  --data-binary @<(jq -n '{model:"gemma4",messages:[{role:"user",content:("hello " * 12000)}],max_tokens:1,stream:false}') \
  http://127.0.0.1:8000/v1/chat/completions |
  jq -e 'select((.choices | length) == 1 and .usage.prompt_tokens > 8192) | {prompt_tokens: .usage.prompt_tokens, completion_tokens: .usage.completion_tokens}'
```

Expected: HTTP success, one completion choice, and `prompt_tokens` above `8192`. The API key never appears in the command line or output.

- [ ] **Step 4: Refresh and verify normal client profiles**

Run:

```bash
make setup-local-llm-agents
```

Expected: Pi and OpenCode report `gemma4` as the enabled local model. Restart both clients after this command so they reload the profile.

Verify projections that exclude credentials:

```bash
jq -e '
  .providers["local-llm-env"].models == [
    {"id":"gemma4","contextWindow":131072,"maxTokens":8192}
  ]
' "$HOME/.pi/agent/models.json" >/dev/null

jq -e '
  .provider["local-llm-env"].models.gemma4.limit ==
    {"context":131072,"output":8192}
' "$HOME/.config/opencode/opencode.jsonc" >/dev/null
```

Expected: both commands exit `0` without printing provider options or keys.

- [ ] **Step 5: Verify the embedded agentic tool template**

Run:

```bash
make check-with-agents
```

Expected: every installed Pi/OpenCode weather and FX matrix cell passes. This exercises Bash tool calls through the model's embedded Gemma 4 Jinja template.

- [ ] **Step 6: Run final repository and service verification**

Run:

```bash
make validate
make test
git diff --check
git status --short
systemctl --user is-active llm-server.service
```

Expected: validation passes, all 386 tests pass, diff check and Git status print nothing, and the service reports `active`.

- [ ] **Step 7: Delete the old model and rollback material**

Only after Steps 1-6 pass, run:

```bash
old_model="$HOME/llm-workspace/models/gemma-4-12B-it-Q4_K_M.gguf"
rollback_dir="$(< /tmp/llm-env-gemma-rollback.path)"
case "$rollback_dir" in
  /tmp/llm-env-gemma-rollback.*) ;;
  *) printf '%s\n' 'invalid rollback directory' >&2; exit 1 ;;
esac
rm -f -- "$old_model"
rm -rf -- "$rollback_dir"
rm -f -- /tmp/llm-env-gemma-rollback.path
test ! -e "$old_model"
test -f "$HOME/llm-workspace/models/gemma4-v2-Q4_K_M.gguf"
```

Expected: the obsolete base GGUF and private rollback files are absent; the validated agentic GGUF remains.

- [ ] **Step 8: Use this rollback if any online check fails before Step 7**

Run:

```bash
make stop
rollback_dir="$(< /tmp/llm-env-gemma-rollback.path)"
case "$rollback_dir" in
  /tmp/llm-env-gemma-rollback.*) ;;
  *) printf '%s\n' 'invalid rollback directory' >&2; exit 1 ;;
esac
install -m 600 "$rollback_dir/models.yml" "$HOME/.config/llm-env/models.yml"
make start
make check-server
```

Expected: the base model returns to service and `make check-server` passes. Keep both GGUF files and diagnose the replacement failure before retrying.

Task 3 changes no tracked files and creates no commit.
