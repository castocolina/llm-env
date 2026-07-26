# Setup and Runtime Lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Separate model-environment setup from server lifecycle, add confirmed prerequisite installation and offline model smoke tests, and make the interactive flow use numbered GPU and model choices.

**Architecture:** Python owns configuration mutations and parsing llama.cpp device listings. Bash orchestrates host checks, downloads, Podman, systemd, and user prompts. `setup.sh` prepares a GPU-pinned Vulkan environment only; `start.sh` owns API-key generation, service startup, and LAN exposure. A render-only unit helper is shared by start and boot enablement.

**Tech Stack:** Bash, shellcheck, Python 3.11+ through uv, PyYAML, pytest, ruff, Podman Quadlet, systemd user units, jq, Mike Farah yq v4, firewalld, Avahi.

## Global Constraints

- All output is English.
- Support Bazzite/Fedora only. Do not add macOS support.
- Use only prebuilt `ghcr.io/ggml-org/llama.cpp` images; do not source-build llama.cpp.
- Invoke Python only as `uv run llmenv.py <subcommand>`.
- Every `.sh` change requires `make validate`; every `.py` change requires `make validate && make test`.
- Makefile target bodies longer than three lines delegate to a `.sh` file.
- `runtime.models_max` always equals the enabled-model count.
- Persist GPU PCI address and llama.cpp device name, never a Vulkan index.
- Never silently choose an ambiguous GPU/device mapping or alter a host package installation.
- The API key must never be printed, passed on a process command line, or left in a world-readable file.

---

## File Structure

| File | Responsibility |
|---|---|
| `models.yml.example` | Model metadata and default generated configuration; Gemma4 and Ornith only |
| `pylib/config.py` | Validate model display metadata and replace the enabled model set |
| `llmenv.py` | JSON CLI for selecting model aliases and parsing/resolving llama.cpp devices |
| `prerequisites.sh` | Detect, explain, and install missing Bazzite/Fedora host tools after confirmation |
| `setup.sh` | Numbered GPU/model selection, downloads, Vulkan preparation, and budget check |
| `check-setup.sh` | Static validation plus disposable offline inference for every enabled model |
| `render-unit.sh` | Generate the Quadlet unit and presets without budget checks or service actions |
| `start.sh` | Generate a missing API key, budget-check, render/start service, then expose LAN access |
| `network.sh` | Firewall consent, mDNS service, and post-health usage output |
| `key-reset.sh` | Rotate the API key and restart an active service |
| `enable-boot.sh` | Enable boot using `render-unit.sh`, never `start.sh` |
| `tests/test_config.py` | Configuration and enabled-set unit tests |
| `tests/test_cli.py` | Device-list parsing and model-selection CLI tests |
| `tests/test_shell.py` | Shell lifecycle, prerequisite, and command-construction regression tests |

## Task 1: Model metadata, selection API, and device-list parsing

**Files:**
- Modify: `models.yml.example`, `pylib/config.py`, `llmenv.py`
- Modify: `tests/test_config.py`, `tests/test_cli.py`

**Interfaces:**
- Produces `set_enabled_models(cfg: dict, aliases: list[str]) -> dict`.
- Produces `parse_device_listing(text: str) -> list[dict[str, int | str]]`, returning `id`, `name`, and `total_mib`.
- Adds `uv run llmenv.py models select <alias>... --config PATH`.
- Adds `uv run llmenv.py list-devices --listing-file PATH`.

- [ ] **Step 1: Add failing configuration tests**

```python
def test_model_requires_display_metadata():
    cfg = make_cfg()
    del cfg["models"][0]["label"]
    assert "model gemma4 missing key: label" in validate_config(cfg)


def test_set_enabled_models_replaces_the_enabled_set():
    cfg = set_enabled_models(make_cfg(), ["ornith"])
    assert [m["alias"] for m in enabled_models(cfg)] == ["ornith"]
    assert cfg["runtime"]["models_max"] == 1


def test_set_enabled_models_rejects_unknown_alias():
    with pytest.raises(ConfigError, match="unknown model alias"):
        set_enabled_models(make_cfg(), ["missing"])
```

- [ ] **Step 2: Run the configuration tests and verify the new imports fail**

Run: `uv run --with pytest pytest tests/test_config.py -v`

Expected: FAIL because `set_enabled_models` is not defined.

- [ ] **Step 3: Implement metadata validation and enabled-set replacement**

Add `label`, `parameters`, and `quantization` to `REQUIRED_MODEL_KEYS`. Implement:

```python
def set_enabled_models(cfg: dict[str, Any], aliases: list[str]) -> dict[str, Any]:
    requested = set(aliases)
    known = {model["alias"] for model in cfg.get("models", [])}
    unknown = requested - known
    if unknown:
        raise ConfigError(f"unknown model alias: {sorted(unknown)[0]}")
    for model in cfg["models"]:
        model["enabled"] = model["alias"] in requested
    return sync_models_max(cfg)
```

Keep `set_model_enabled` for direct CLI compatibility; it must still call `sync_models_max` through its caller.

- [ ] **Step 4: Add failing CLI tests for selection and device parsing**

```python
def test_models_select_replaces_enabled_set(tmp_path):
    config = write_test_config(tmp_path)
    result = run("models", "select", "b", "--config", str(config))
    assert result.returncode == 0
    assert json.loads(result.stdout)["models_max"] == 1
    assert enabled_aliases(config) == ["b"]


def test_list_devices_parses_vulkan_rows(tmp_path):
    listing = tmp_path / "devices.txt"
    listing.write_text(
        "  Vulkan0: GPU A (16304 MiB, 16000 MiB free)\n"
        "  Vulkan1: GPU B (512 MiB, 500 MiB free)\n"
    )
    result = run("list-devices", "--listing-file", str(listing))
    assert json.loads(result.stdout)["devices"] == [
        {"id": "Vulkan0", "name": "GPU A", "total_mib": 16304},
        {"id": "Vulkan1", "name": "GPU B", "total_mib": 512},
    ]
```

- [ ] **Step 5: Run the CLI tests and verify they fail**

Run: `uv run --with pytest --with pyyaml pytest tests/test_cli.py -v`

Expected: FAIL because `select` and `list-devices` are absent.

- [ ] **Step 6: Implement the CLI commands**

Replace the device regex with a parser that captures the device ID, trimmed name, and total MiB. Add a `list-devices` subcommand that reads either `--listing-file` or `--list-command` and emits `{"devices": [...]}`. Add `models select` with one-or-more aliases and call `save_config(set_enabled_models(...), path)`.

- [ ] **Step 7: Update the YAML template**

Remove the OpenHermes model entirely. Add display metadata to the remaining entries:

```yaml
  - alias: gemma4
    label: Gemma 4 Instruct
    parameters: 12B
    quantization: Q4_K_M
    enabled: true
```

```yaml
  - alias: ornith
    label: Ornith 1.0
    parameters: 9B
    quantization: Q4_K_M
    enabled: false
```

Keep current URLs, filenames, sizes, contexts, and GPU-layer settings.

- [ ] **Step 8: Run verification**

Run: `make validate && make test`

Expected: all checks pass.

- [ ] **Step 9: Commit**

```bash
git add models.yml.example pylib/config.py llmenv.py tests/test_config.py tests/test_cli.py
git commit -m "feat: add numbered model selection metadata"
```

## Task 2: Confirmed prerequisite detection and installation

**Files:**
- Create: `prerequisites.sh`
- Modify: `Makefile`, `setup.sh`, `tests/test_shell.py`, `README.md`

**Interfaces:**
- Produces `make prerequisites`.
- `prerequisites.sh --check` exits 0 only when all required runtime tools are usable.
- `prerequisites.sh` lists missing commands and package names, then requires `yes` before calling `sudo rpm-ostree install`.

- [ ] **Step 1: Write failing shell regression tests**

Use command stubs in `tests/test_shell.py` to assert:

```python
def test_prerequisites_reports_missing_yq_v4_without_installing(tmp_path):
    result, calls = run_prerequisites_with_stubs(tmp_path, yq_version="yq 3.4.1")
    assert result.returncode == 1
    assert "Mike Farah yq v4" in result.stdout
    assert "rpm-ostree install" not in calls.read_text()


def test_prerequisites_installs_only_after_yes(tmp_path):
    result, calls = run_prerequisites_with_stubs(tmp_path, response="yes")
    assert result.returncode == 0
    assert "rpm-ostree install" in calls.read_text()
```

The stubbed `rpm-ostree` records arguments and exits 0; no real host command runs in tests.

- [ ] **Step 2: Run the shell tests and verify they fail**

Run: `uv run --with pytest pytest tests/test_shell.py -v`

Expected: FAIL because `prerequisites.sh` does not exist.

- [ ] **Step 3: Implement `prerequisites.sh`**

Use `set -euo pipefail` and source `lib.sh`. Check these command/package pairs:

```bash
RUNTIME=("uv:uv" "jq:jq" "yq:yq" "podman:podman" "curl:curl" "ip:iproute")
DEVELOPMENT=("git:git" "shellcheck:ShellCheck")
OPTIONAL_LAN=("firewall-cmd:firewalld" "avahi-publish:avahi")
```

Treat `yq --version` as valid only when it contains `github.com/mikefarah/yq/` and `version v4.`. Print installed/missing rows with each command’s purpose. `--check` must not prompt or install. With missing packages, print the exact proposed command:

```bash
sudo rpm-ostree install <deduplicated-packages>
```

Prompt `Install these packages? (yes/no) `. Only `yes` runs it. After a successful transaction, state that a reboot is required before rerunning setup. Optional LAN packages are reported but are not included unless the user explicitly confirms a second `Install optional LAN tools? (yes/no)` prompt.

- [ ] **Step 4: Add Makefile and setup integration**

Add `prerequisites` to `.PHONY`, help output, and a one-line target delegating to `prerequisites.sh`. At the start of `setup.sh`, run `bash "${REPO_DIR}/prerequisites.sh" --check || die "missing prerequisites; run 'make prerequisites'"` before any config mutation.

- [ ] **Step 5: Run verification**

Run: `make validate && make test && bash prerequisites.sh --check`

Expected: validation and tests pass; the local host reports its installed/missing status without installing anything.

- [ ] **Step 6: Commit**

```bash
git add prerequisites.sh setup.sh Makefile tests/test_shell.py README.md
git commit -m "feat: add confirmed host prerequisite setup"
```

## Task 3: Numbered, preparation-only setup

**Files:**
- Modify: `setup.sh`, `tests/test_shell.py`

**Interfaces:**
- Consumes Task 1 `models select` and `list-devices` JSON commands.
- Produces a configuration with selected PCI address, exact enabled aliases, synchronized `models_max`, and resolved `gpu.device_name`.
- Does not create an API key, start mDNS, alter the firewall, or print API usage.

- [ ] **Step 1: Write failing shell tests for generated selection input**

Add tests with mocked `llmenv`, `podman`, and `yq` that assert setup sends model aliases `gemma4 ornith` when input is `1,2`, and that an invalid value such as `3` exits non-zero before download.

- [ ] **Step 2: Run the shell tests and verify they fail**

Run: `uv run --with pytest pytest tests/test_shell.py -v`

Expected: FAIL because setup still accepts a PCI string and one alias toggle.

- [ ] **Step 3: Replace GPU selection with numbered rows**

Render each detected GPU as `N) <coloured card> <PCI> <VRAM> <render node> <displays>`. Default to the index with the largest measured VRAM. Validate that the entered integer selects exactly one item, then store its PCI address and VRAM total.

- [ ] **Step 4: Replace the model toggle with complete numbered selection**

Render `N) <label> — <parameters>, <quantization>, <size>` from YAML. Default to the current enabled indexes. Parse comma-separated positive integers, reject duplicates/out-of-range/empty selections, map them to aliases, and run:

```bash
llmenv --config "$CONFIG_PATH" models select "${aliases[@]}" >/dev/null
```

Before selection, remove any `openhermes` mapping from an existing generated config with `yq -i 'del(.models[] | select(.alias == "openhermes"))'` and run `llmenv ... models list` to synchronize the generated config.

- [ ] **Step 5: Prepare Vulkan and resolve the runtime device name**

Pull `ghcr.io/ggml-org/llama.cpp:server-vulkan`, capture `/app/llama-server --list-devices`, and call `llmenv list-devices`. Filter candidates whose `total_mib` equals the selected GPU’s measured total. Save the sole candidate’s `name`. For zero or multiple candidates, display numbered candidates and require a valid selection; never store `VulkanN`.

- [ ] **Step 6: Delete setup lifecycle work**

Remove the API-key block, `/tmp/llm-budget.json` use it replaces with `mktemp` plus a cleanup trap, firewall configuration, mDNS-unit generation, and usage examples. Keep downloads, GGUF validation, and budget reporting. End with `Setup complete. Next: make check-setup`.

- [ ] **Step 7: Run verification**

Run: `make validate && make test`

Expected: all checks pass.

- [ ] **Step 8: Commit**

```bash
git add setup.sh tests/test_shell.py
git commit -m "feat: make setup prepare models without server state"
```

## Task 4: Offline inference in check-setup

**Files:**
- Modify: `check-setup.sh`, `tests/test_shell.py`

**Interfaces:**
- Consumes config fields `gpu.image`, `gpu.device_name`, and every enabled model’s file and `n_gpu_layers`.
- Resolves the persisted device name to a transient `VulkanN` ID with `llmenv resolve-device`; the ID is never saved.
- Produces one bounded disposable GPU inference per enabled model.

- [ ] **Step 1: Write failing command-construction tests**

Mock `podman` and assert that `check-setup.sh` resolves the saved device name to a transient device ID, then runs once per enabled model with `run --rm`, `--device /dev/dri`, a read-only model mount, `/app/llama cli`, the model path, transient device ID, and `n_gpu_layers`. Assert no `podman exec` command appears.

- [ ] **Step 2: Run the shell tests and verify they fail**

Run: `uv run --with pytest pytest tests/test_shell.py -v`

Expected: FAIL because no inference smoke test exists.

- [ ] **Step 3: Implement the bounded smoke loop**

After a feasible budget, capture `--list-devices`, resolve `gpu.device_name` through `llmenv resolve-device`, and retain the returned transient ID in `device`. Read each enabled model as TSV. For each, run under `timeout 180`:

```bash
podman run --rm --device /dev/dri \
  -v "${MODELS_DIR}:/models:ro,z" \
  --entrypoint /app/llama "$image" cli \
  -m "/models/${file}" --device "$device" \
  --n-gpu-layers "$layers" -p "Reply with exactly: ready" -n 16
```

Capture output. Count a pass only when the command exits 0 and output is non-empty; otherwise print the alias and a bounded diagnostic. Include these results in the existing PASS/FAIL summary. Do not bind ports, create units, require an API key, or use live-world prompts.

- [ ] **Step 4: Run verification**

Run: `make validate && make test`

Expected: all checks pass.

- [ ] **Step 5: Commit**

```bash
git add check-setup.sh tests/test_shell.py
git commit -m "test: smoke test every enabled model before start"
```

## Task 5: Runtime key, LAN, and boot lifecycle

**Files:**
- Create: `render-unit.sh`, `network.sh`, `key-reset.sh`
- Modify: `start.sh`, `enable-boot.sh`, `Makefile`, `tests/test_shell.py`

**Interfaces:**
- `render-unit.sh` renders presets and `${QUADLET_DIR}/${UNIT_NAME}.container` without starting, stopping, or budget-checking.
- `network.sh` configures optional firewall/mDNS after health succeeds.
- `key-reset.sh` restarts an active server after rotation.

- [ ] **Step 1: Write failing lifecycle tests**

Add tests that prove:

```python
def test_start_generates_key_only_when_empty(tmp_path):
    result, config, calls = run_lifecycle_script(tmp_path, "start.sh", api_key="")
    assert result.returncode == 0
    assert yq_value(config, ".server.api_key")
    assert yq_value(config, ".server.api_key") not in result.stdout
    assert "systemctl --user start llm-server.service" in calls.read_text()


def test_key_reset_restarts_an_active_server(tmp_path):
    result, _, calls = run_lifecycle_script(tmp_path, "key-reset.sh", active=True)
    assert result.returncode == 0
    assert "systemctl --user stop llm-server.service" in calls.read_text()
    assert "systemctl --user start llm-server.service" in calls.read_text()


def test_key_reset_does_not_start_an_inactive_server(tmp_path):
    result, _, calls = run_lifecycle_script(tmp_path, "key-reset.sh", active=False)
    assert result.returncode == 0
    assert "systemctl --user start llm-server.service" not in calls.read_text()


def test_enable_boot_calls_render_unit_not_start(tmp_path):
    result, _, calls = run_lifecycle_script(tmp_path, "enable-boot.sh")
    assert result.returncode == 0
    assert "render-unit.sh" in calls.read_text()
    assert "start.sh" not in calls.read_text()
```

Add `run_lifecycle_script` to `tests/test_shell.py`; it writes a minimal mode-600 YAML config, installs command stubs that append their arguments to `calls`, and returns the completed process, config path, and call log. Stub `systemctl`, `yq`, `podman`, and `curl`; assert the generated API key never appears in captured output.

- [ ] **Step 2: Run shell tests and verify they fail**

Run: `uv run --with pytest pytest tests/test_shell.py -v`

Expected: FAIL because these scripts do not exist and boot enablement calls `start.sh`.

- [ ] **Step 3: Extract render-only unit generation**

Move device resolution, presets generation, Quadlet heredoc, mode `0600`, and `systemctl --user daemon-reload` from `start.sh` into `render-unit.sh`. `render-unit.sh` reads configuration but does not run the budget command or start a service.

- [ ] **Step 4: Move missing-key generation and LAN work to runtime**

In `start.sh`, generate and save a random key only when `.server.api_key` is empty, run the budget check before service startup, call `render-unit.sh`, start and health-check the unit, then call `network.sh`.

Move firewall and Avahi logic plus usage examples from `setup.sh` to `network.sh`. Firewall remains opt-in when closed; mDNS starts after health. Print `127.0.0.1`, LAN IP, and `.local` endpoints only after successful health.

- [ ] **Step 5: Add key-reset and safe boot enablement**

Implement `key-reset.sh`: generate/save a new key with mode `0600`; if `systemctl --user is-active --quiet` succeeds, call `stop.sh` then `start.sh`; otherwise report that the key applies on the next start. Add `key-reset` to Makefile help and `.PHONY`.

Change `enable-boot.sh` to set `server.start_at_boot = true`, call `render-unit.sh`, then enable lingering. It must not call `start.sh`, so it cannot see the running model’s VRAM as compositor usage.

- [ ] **Step 6: Run verification**

Run: `make validate && make test`

Expected: all checks pass.

- [ ] **Step 7: Commit**

```bash
git add render-unit.sh network.sh key-reset.sh start.sh enable-boot.sh Makefile tests/test_shell.py
git commit -m "feat: separate server key and network lifecycle"
```

## Task 6: Documentation and full acceptance

**Files:**
- Modify: `README.md`, `QUICK_START.md`, `AGENTS.md`, `.agents/architecture.md`

- [ ] **Step 1: Update command documentation**

Document this first-run order exactly:

```bash
make prerequisites
make setup
make check-setup
make benchmark
make start
make check-server
make stop
```

Document `make key-reset`, that setup does not start a server, and that `.local` requires mDNS support on the client or use of the printed LAN IP. Remove all OpenHermes references.

- [ ] **Step 2: Update architecture documentation**

Describe `prerequisites.sh`, `render-unit.sh`, `network.sh`, and `key-reset.sh`. State that setup uses the Vulkan image for disposable pre-benchmark smoke tests and benchmark selects the persistent backend.

- [ ] **Step 3: Run static verification**

Run: `make validate && make test`

Expected: all checks pass.

- [ ] **Step 4: Run the full acceptance sequence**

Run:

```bash
make prerequisites
LLM_ENV_ASSUME_YES=1 make setup
make check-setup
make benchmark
make start
make check-server
make stop
```

Expected: setup selects the default numbered GPU/model set without creating an API key or LAN service; check-setup completes a disposable inference for each enabled model; benchmark records a backend; start creates a missing key, reaches health, and then exposes LAN access; check-server passes; stop leaves the service inactive.

- [ ] **Step 5: Test key reset and boot enablement with the server active**

Run:

```bash
make start
make key-reset
make enable-boot
make disable-boot
make stop
```

Expected: key reset restarts the service, enable boot succeeds without a false VRAM shortfall, disable boot updates config, and stop leaves the service inactive.

- [ ] **Step 6: Commit**

```bash
git add README.md QUICK_START.md AGENTS.md .agents/architecture.md
git commit -m "docs: document separated setup and server lifecycle"
```

## Self-Review

- Spec coverage: Task 1 covers YAML-driven display metadata and model/device parsing; Task 2 covers confirmed prerequisites; Task 3 covers numbered preparation-only setup; Task 4 covers each-model offline inference; Task 5 covers API keys, LAN, and boot rendering; Task 6 covers documentation and both acceptance flows.
- No placeholders: every task names exact files, commands, interfaces, and verification steps.
- Contract consistency: Task 1 produces the model selection and device-list commands consumed by Task 3. Task 3 persists the device name consumed by Task 4 and Task 5. Task 5 creates the key and LAN service only after start, as required by Task 6.
