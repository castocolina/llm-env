# Resource Limits and Single-Model Setup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Copy `presets.ini` to the repo inspection dir, restrict `make setup` to one enabled model at a time, and add two real resource ceilings (RAM: fixed % of total host RAM; VRAM: % of what's free on the dGPU at setup time) so `llm-server` can never silently consume more than intended.

**Architecture:** Four independent, additive changes to existing modules — no new files except this plan's own tests. RAM ceiling lives in `pylib/resources.py::compute_resource_limits()` (a new parameter, applied every time `llmenv resources` runs). VRAM ceiling is computed once by `setup/setup.sh` (where GPU total/used are already measured) and stored as a resolved absolute MiB value that `pylib/budget.py::compute_budget()` treats as a second cap alongside `vram_total_mib`.

**Tech Stack:** Bash (`setup/setup.sh`, `setup/render-unit.sh`), Python (`pylib/config.py`, `pylib/resources.py`, `pylib/budget.py`, `llmenv.py`), pytest, yq/jq.

## Global Constraints

- `resources.llm_server.memory_ceiling_pct` default: `46` (percent of total host RAM — ≈14.4 GiB on a 32 GiB host, the closest whole percent to the 14 GiB originally requested).
- `gpu.vram_budget_ceiling_pct` default: `95` (percent of VRAM *free* on the configured GPU at the moment `make setup` last ran, not of the total).
- `gpu.vram_budget_ceiling_mib` is a **resolved, absolute** value written by `setup.sh`, not evaluated live — it goes stale (in the "conservative" direction only, since it was computed against whatever was free at setup time) until the next `make setup`.
- Both ceilings default to values that reproduce today's behavior when unset in code paths that read them (`compute_resource_limits`'s `memory_ceiling_pct` defaults to `100` — i.e. uncapped — when a caller omits it; `compute_budget`'s `vram_budget_ceiling_mib` defaults to `None`, meaning "use `vram_total_mib`" — no artificial cap).
- Every new/changed step must leave the full test suite, `ruff check .`, and `shellcheck` clean before moving to the next task.

---

### Task 1: `presets.ini` inspection copy

**Files:**
- Modify: `setup/render-unit.sh:39-59`
- Test: `tests/test_shell.py` (add near the existing compose-inspect-copy test, search for `COMPOSE_INSPECT_DIR` usages around line 1200)

**Interfaces:**
- Consumes: `$COMPOSE_INSPECT_DIR` (already exported by `tools/lib.sh`), `$presets_path` (already a local var in `render-unit.sh`, set at line 40).
- Produces: nothing consumed by later tasks in this plan.

- [ ] **Step 1: Write the failing test**

Find the existing render-unit fixture helper (`run_render_unit_with_legacy_rocm_config` at `tests/test_shell.py:1129`, which already sets `LLM_ENV_COMPOSE_INSPECT_DIR`). Add a new test right after `test_render_unit_writes_a_compose_file_and_wrapper_unit` (around line 1224):

```python
def test_render_unit_copies_presets_ini_to_the_inspect_dir(
    tmp_path: pathlib.Path,
) -> None:
    result, presets_path, _config, compose_inspect_dir = (
        run_render_unit_with_legacy_rocm_config(tmp_path)
    )

    assert result.returncode == 0, result.stdout + result.stderr
    inspect_copy = compose_inspect_dir / "presets.ini"
    assert inspect_copy.is_file()
    assert inspect_copy.read_text() == presets_path.read_text()
```

If `run_render_unit_with_legacy_rocm_config` doesn't already return `presets_path`/`compose_inspect_dir`, extend its return tuple and update its other three call sites in the same file to unpack the extra values (read the function body at `tests/test_shell.py:1129-1216` first to see its current return statement and adjust all callers accordingly — do not change its stubbing behavior, only what it returns).

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_shell.py::test_render_unit_copies_presets_ini_to_the_inspect_dir -v`
Expected: FAIL — `inspect_copy.is_file()` is `False`.

- [ ] **Step 3: Implement**

In `setup/render-unit.sh`, right after line 43 (`log_info "wrote ${presets_path}"`) and before line 45 (`log_step "Rendering the compose file"`), add:

```bash
if [ -f "$presets_path" ]; then
    mkdir -p "$COMPOSE_INSPECT_DIR"
    cp "$presets_path" "${COMPOSE_INSPECT_DIR}/presets.ini"
    log_info "wrote ${COMPOSE_INSPECT_DIR}/presets.ini (inspection copy)"
fi
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_shell.py::test_render_unit_copies_presets_ini_to_the_inspect_dir -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add setup/render-unit.sh tests/test_shell.py
git commit -m "feat(render-unit): copy presets.ini to the compose inspection dir"
```

---

### Task 2: Single-model `make setup`

**Files:**
- Modify: `setup/setup.sh:54-75`
- Modify: `tests/test_shell.py` (four call-site updates + one test restructure, all found via `grep -n '"1,2"' tests/test_shell.py`)

**Interfaces:**
- Consumes: nothing new.
- Produces: nothing consumed by later tasks in this plan (Task 4 also touches `setup.sh`, in a different step, and the two edits don't overlap).

- [ ] **Step 1: Write the failing test**

Add this test near `test_setup_rejects_invalid_numbered_model_selection_before_download` (`tests/test_shell.py:676`):

```python
def test_setup_rejects_comma_separated_model_selection(
    tmp_path: pathlib.Path,
) -> None:
    """Only one model may be enabled by the guided setup flow — VRAM for two
    models at once was never guaranteed to fit."""
    result, calls, _ = run_setup_with_numbered_selection(tmp_path, "1\n1,2\n2\n")

    assert result.returncode != 0
    assert "curl " not in calls.read_text()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_shell.py::test_setup_rejects_comma_separated_model_selection -v`
Expected: FAIL — today's regex accepts `1,2` and setup proceeds to `returncode == 0`.

- [ ] **Step 3: Implement**

In `setup/setup.sh`, replace lines 63-73:

```bash
model_choice="$(ask "  Model numbers [${default_models}]: " "$default_models")"
[[ "$model_choice" =~ ^[1-9][0-9]*(,[1-9][0-9]*)*$ ]] || die "model selection must be comma-separated positive integers"
IFS=',' read -r -a model_indexes <<< "$model_choice"
declare -A selected_indexes=()
aliases=()
for index in "${model_indexes[@]}"; do
    [ "$index" -le "$model_count" ] || die "model selection is out of range"
    [ -z "${selected_indexes[$index]:-}" ] || die "model selection contains duplicate index ${index}"
    selected_indexes[$index]=1
    aliases+=("$(echo "$models" | jq -r --argjson index "$index" '.models[$index - 1].alias')")
done
```

with:

```bash
model_choice="$(ask "  Model number [${default_models}]: " "$default_models")"
[[ "$model_choice" =~ ^[1-9][0-9]*$ ]] || die "model selection must be a single positive integer"
[ "$model_choice" -le "$model_count" ] || die "model selection is out of range"
aliases=("$(echo "$models" | jq -r --argjson index "$model_choice" '.models[$index - 1].alias')")
```

And change line 62 (`default_models=...`) from joining every enabled index to just the first one:

```bash
default_models="$(echo "$models" | jq -r '[.models | to_entries[] | select(.value.enabled) | (.key + 1 | tostring)] | first // "1"')"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_shell.py::test_setup_rejects_comma_separated_model_selection -v`
Expected: PASS

- [ ] **Step 5: Fix the four tests that relied on multi-select**

Run the full file to see what else breaks:

Run: `uv run pytest tests/test_shell.py -k setup -v`
Expected: several FAILs in tests that pass `"1,2"` as the model-selection input.

Update each in place:

`test_setup_selects_zero_match_vulkan_device_and_persists_config` (`tests/test_shell.py:615`): change the input from `"1\n1,2\n2\n"` to `"1\n1\n2\n"`, and update:
```python
    call_log = calls.read_text()
    assert "models select gemma4" in call_log
```
(was `"models select gemma4 ornith"`), and:
```python
    assert persisted["gpu"] == {
        "pci_address": "0000:03:00.0",
        "vram_total_mib": 16384,
        "device_name": 'Fallback Radeon: "safe"',
        # vram_budget_ceiling_mib added by Task 4 — leave this assertion as
        # today's shape for now; Task 4's own steps update it.
    }
    assert persisted["runtime"]["models_max"] == 1
    assert [model["enabled"] for model in persisted["models"]] == [True, False]
```

`test_setup_writes_computed_resource_limits` (`tests/test_shell.py:647`) and `test_setup_writes_computed_omniroute_resource_limits` (`tests/test_shell.py:655`): change the input from `"1\n1,2\n2\n"` to `"1\n1\n2\n"` — no assertion changes needed, they only check `resources.*`.

`test_setup_fails_instead_of_leaving_resources_uncapped` (`tests/test_shell.py:662`): change the input from `"1\n1,2\n2\n"` to `"1\n1\n2\n"`.

- [ ] **Step 6: Restructure the reorder test to bypass the (now single-select) interactive prompt**

`test_reverse_setup_selection_drives_client_model_order` (`tests/test_shell.py:686`) exercises multi-model *ordering*, which `pylib/config.py::set_enabled_models` still supports for direct/scripted use (only the interactive prompt narrowed) — `set_enabled_models`'s ordering behavior already has its own dedicated unit tests in `tests/test_config.py` (`test_set_enabled_models_preserves_requested_order_before_unselected_models`). Rewrite this integration test to drive the reorder via a direct `llmenv models select` call instead of through `setup.sh`'s prompt:

```python
def test_reverse_setup_selection_drives_client_model_order(
    tmp_path: pathlib.Path,
) -> None:
    setup_result, _calls, config = run_setup_with_numbered_selection(
        tmp_path, "1\n1\n2\n", config_text=VALID_AGENT_SETUP_CONFIG
    )
    assert setup_result.returncode == 0, setup_result.stderr

    real_uv = shutil.which("uv")
    assert real_uv is not None
    reorder = subprocess.run(
        [real_uv, "run", str(ROOT / "llmenv.py"), "--config", str(config),
         "models", "select", "ornith", "gemma4"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert reorder.returncode == 0, reorder.stderr

    assert [model["alias"] for model in json.loads(
        subprocess.run(
            [shutil.which("yq") or "yq", "-o=json", ".", str(config)],
            text=True,
            capture_output=True,
            check=True,
        ).stdout
    )["models"]] == ["ornith", "gemma4", "old-model"]

    result, _, pi_path, settings_path, opencode_paths, state_path = (
        run_setup_local_llm_agents(tmp_path, config_text=config.read_text())
    )

    assert result.returncode == 0, result.stderr
    pi_provider = json.loads(pi_path.read_text())["providers"]["local-llm-env"]
    assert [model["id"] for model in pi_provider["models"]] == ["ornith", "gemma4"]
```

Keep whatever assertions follow this point in the original test body unchanged (only the setup/reorder prelude changes) — read the rest of the original test (continues past `tests/test_shell.py:713`) and preserve it verbatim under the new prelude.

- [ ] **Step 7: Run the full setup test slice**

Run: `uv run pytest tests/test_shell.py -k setup -v`
Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
git add setup/setup.sh tests/test_shell.py
git commit -m "feat(setup): restrict guided model selection to a single model"
```

---

### Task 3: Explicit RAM ceiling

**Files:**
- Modify: `pylib/resources.py`
- Modify: `pylib/config.py:55-94` (`migrate_config`), `pylib/config.py:146-324` (`validate_config`)
- Modify: `llmenv.py:211-214` (`cmd_resources`), `llmenv.py:351-357` (parser setup)
- Modify: `models.yml.example`
- Modify: `setup/setup.sh:142` (pass `--config` explicitly)
- Test: `tests/test_resources.py`, `tests/test_config.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: nothing from Tasks 1-2.
- Produces: `compute_resource_limits(host_cpu_count: int, host_memory_total_mib: int, memory_ceiling_pct: float = 100) -> dict` (new third parameter, default preserves old behavior). Config keys `resources.llm_server.memory_ceiling_pct`.

- [ ] **Step 1: Write the failing `pylib/resources.py` unit test**

Add to `tests/test_resources.py`:

```python
def test_memory_ceiling_caps_llm_server_below_the_uncapped_remainder():
    uncapped = compute_resource_limits(host_cpu_count=8, host_memory_total_mib=32768)
    capped = compute_resource_limits(
        host_cpu_count=8, host_memory_total_mib=32768, memory_ceiling_pct=46
    )
    assert capped["llm_server"]["memory_mib"] < uncapped["llm_server"]["memory_mib"]
    assert capped["llm_server"]["memory_mib"] == round(32768 * 46 / 100)


def test_memory_ceiling_above_the_remainder_is_a_no_op():
    result = compute_resource_limits(
        host_cpu_count=8, host_memory_total_mib=32768, memory_ceiling_pct=100
    )
    assert (
        result["llm_server"]["memory_mib"]
        == 32768 - HOST_MEMORY_FLOOR_MIB - OMNIROUTE_MEMORY_FIXED_MIB
    )


def test_memory_ceiling_does_not_affect_the_insufficient_memory_floor_check():
    with pytest.raises(ResourceError):
        compute_resource_limits(
            host_cpu_count=8,
            host_memory_total_mib=HOST_MEMORY_FLOOR_MIB + OMNIROUTE_MEMORY_FIXED_MIB,
            memory_ceiling_pct=1,
        )
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_resources.py -v`
Expected: FAIL with `TypeError: compute_resource_limits() got an unexpected keyword argument 'memory_ceiling_pct'`.

- [ ] **Step 3: Implement in `pylib/resources.py`**

Replace the function body (lines 27-55) with:

```python
def compute_resource_limits(
    host_cpu_count: int,
    host_memory_total_mib: int,
    memory_ceiling_pct: float = 100,
) -> dict[str, Any]:
    cpu_floor = HOST_CPU_FLOOR + OMNIROUTE_CPU_FIXED
    memory_floor_mib = HOST_MEMORY_FLOOR_MIB + OMNIROUTE_MEMORY_FIXED_MIB
    if host_cpu_count <= cpu_floor:
        raise ResourceError(
            f"host has {host_cpu_count} CPUs; more than {cpu_floor} are "
            "required to reserve the host floor and OmniRoute's fixed "
            "allocation and still run llm-server"
        )
    if host_memory_total_mib <= memory_floor_mib:
        raise ResourceError(
            f"host has {host_memory_total_mib} MiB RAM; more than "
            f"{memory_floor_mib} MiB is required to reserve the host floor "
            "and OmniRoute's fixed allocation and still run llm-server"
        )
    memory_ceiling_mib = round(host_memory_total_mib * memory_ceiling_pct / 100)
    llm_server_memory_mib = min(
        host_memory_total_mib - memory_floor_mib, memory_ceiling_mib
    )
    return {
        "host_cpu_floor": HOST_CPU_FLOOR,
        "host_memory_floor_mib": HOST_MEMORY_FLOOR_MIB,
        "omniroute": {
            "cpus": OMNIROUTE_CPU_FIXED,
            "memory_mib": OMNIROUTE_MEMORY_FIXED_MIB,
        },
        "llm_server": {
            "cpus": host_cpu_count - cpu_floor,
            "memory_mib": llm_server_memory_mib,
        },
    }
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_resources.py -v`
Expected: PASS (all tests, old and new).

- [ ] **Step 5: Write the failing `pylib/config.py` tests**

Add to `tests/test_config.py`, right after `test_migrate_config_preserves_existing_resources_values` (line 559-562):

```python
def test_migrate_config_adds_default_memory_ceiling_pct():
    cfg = make_cfg()
    cfg["resources"]["llm_server"].pop("memory_ceiling_pct", None)
    migrated = config_module.migrate_config(copy.deepcopy(cfg))
    assert migrated["resources"]["llm_server"]["memory_ceiling_pct"] == 46


def test_migrate_config_preserves_existing_memory_ceiling_pct():
    cfg = make_cfg(resources={"llm_server": {"cpus": 6, "memory_mib": 28672, "memory_ceiling_pct": 30}})
    migrated = config_module.migrate_config(copy.deepcopy(cfg))
    assert migrated["resources"]["llm_server"]["memory_ceiling_pct"] == 30


@pytest.mark.parametrize("value", [0, -1, 101, float("inf"), "lots", True])
def test_validate_config_rejects_invalid_memory_ceiling_pct(value):
    cfg = make_cfg(
        resources={"llm_server": {"cpus": 0, "memory_mib": 0, "memory_ceiling_pct": value}}
    )
    errors = validate_config(cfg)
    assert any("memory_ceiling_pct" in error for error in errors)


def test_validate_config_accepts_boundary_memory_ceiling_pct():
    cfg = make_cfg(
        resources={"llm_server": {"cpus": 0, "memory_mib": 0, "memory_ceiling_pct": 100}}
    )
    assert validate_config(cfg) == []
```

Also update `make_cfg`'s default `resources.llm_server` dict (`tests/test_config.py:59`) from `{"cpus": 0, "memory_mib": 0}` to `{"cpus": 0, "memory_mib": 0, "memory_ceiling_pct": 46}`, and fix the two now-broken exact-equality assertions:
- `test_migrate_config_adds_default_resources_section` (line 553-556): expected dict becomes `{"cpus": 0, "memory_mib": 0, "memory_ceiling_pct": 46}`.
- `test_migrate_config_adds_default_resources_section_when_gpu_absent` (line 575-580): same change.

- [ ] **Step 6: Run to verify it fails**

Run: `uv run pytest tests/test_config.py -k memory_ceiling -v`
Expected: FAIL — `migrate_config` doesn't set the key yet; `validate_config` doesn't reject bad values yet.

- [ ] **Step 7: Implement in `pylib/config.py`**

In `migrate_config` (around line 75), change:

```python
        llm_server_resources = resources.setdefault("llm_server", {})
        if isinstance(llm_server_resources, dict):
            llm_server_resources.setdefault("cpus", 0)
            llm_server_resources.setdefault("memory_mib", 0)
```

to:

```python
        llm_server_resources = resources.setdefault("llm_server", {})
        if isinstance(llm_server_resources, dict):
            llm_server_resources.setdefault("cpus", 0)
            llm_server_resources.setdefault("memory_mib", 0)
            llm_server_resources.setdefault("memory_ceiling_pct", 46)
```

In `validate_config` (around line 190-196, inside the `llm_server_resources` `isinstance` branch), add after the existing `memory_mib` check:

```python
                memory_ceiling_pct = llm_server_resources.get("memory_ceiling_pct", 46)
                if isinstance(memory_ceiling_pct, bool) or not (
                    _finite_number(memory_ceiling_pct)
                    and 0 < memory_ceiling_pct <= 100
                ):
                    errors.append(
                        "resources.llm_server.memory_ceiling_pct must be a "
                        "finite number greater than 0 and at most 100"
                    )
```

- [ ] **Step 8: Run to verify it passes**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS (full file — check no other exact-equality test broke; fix any the same way as Step 5 if so).

- [ ] **Step 9: Wire `llmenv.py`'s `resources` command to config**

Write the failing CLI test first. Replace `test_resources_emits_host_and_llm_server_limits` in `tests/test_cli.py` (line 837-846):

```python
def test_resources_emits_host_and_llm_server_limits(tmp_path: Path):
    config = write_test_config(tmp_path)
    result = run("resources", "--config", str(config))
    payload = json.loads(result.stdout)
    assert result.returncode == 0, result.stderr
    assert payload["host"]["cpu_count"] > 0
    assert payload["llm_server"]["cpus"] >= 0
    assert payload["llm_server"]["memory_mib"] >= 0
```

Run: `uv run pytest tests/test_cli.py::test_resources_emits_host_and_llm_server_limits -v`
Expected: FAIL — `resources` subcommand doesn't accept `--config` yet (argparse "unrecognized arguments").

Implement in `llmenv.py`. Change `cmd_resources` (lines 211-214):

```python
def cmd_resources(args: argparse.Namespace) -> int:
    cfg = require_valid_config(load_config(Path(args.config)))
    host = host_resources()
    memory_ceiling_pct = cfg["resources"]["llm_server"]["memory_ceiling_pct"]
    limits = compute_resource_limits(
        host["cpu_count"], host["memory_total_mib"], memory_ceiling_pct
    )
    return emit({"host": host, **limits})
```

Change the parser registration (line 356):

```python
    resources_parser = sub.add_parser("resources")
    resources_parser.add_argument("--config", default=argparse.SUPPRESS)
    resources_parser.set_defaults(func=cmd_resources)
```

Run: `uv run pytest tests/test_cli.py::test_resources_emits_host_and_llm_server_limits -v`
Expected: PASS.

- [ ] **Step 10: Point `setup.sh`'s Step 8 at the real config path**

`setup/setup.sh:142` currently calls `llmenv resources > "$resources_json"` with no `--config` — now that `cmd_resources` loads config, it must target `$CONFIG_PATH` explicitly (matches every other `llmenv` call in this script). Change line 142 to:

```bash
if llmenv --config "$CONFIG_PATH" resources > "$resources_json"; then
```

Run: `uv run pytest tests/test_shell.py -k setup -v`
Expected: PASS — the `uv` stub in `run_setup_with_numbered_selection` matches on `*' resources')`, a suffix match unaffected by the added `--config` prefix.

- [ ] **Step 11: Update `models.yml.example`**

Add `memory_ceiling_pct: 46` to the `resources.llm_server` block:

```yaml
resources:
  llm_server:
    cpus: 0
    memory_mib: 0
    memory_ceiling_pct: 46
  omniroute:
    cpus: 1
    memory_mib: 1024
```

- [ ] **Step 12: Full-file regression check**

Run: `uv run pytest tests/test_resources.py tests/test_config.py tests/test_cli.py tests/test_shell.py -v`
Expected: all PASS.

- [ ] **Step 13: Commit**

```bash
git add pylib/resources.py pylib/config.py llmenv.py setup/setup.sh models.yml.example \
  tests/test_resources.py tests/test_config.py tests/test_cli.py tests/test_shell.py
git commit -m "feat(resources): cap llm-server RAM at a configurable percent of host total"
```

---

### Task 4: Explicit VRAM ceiling

**Files:**
- Modify: `pylib/budget.py:103-178` (`compute_budget`)
- Modify: `pylib/config.py:55-94` (`migrate_config`), `pylib/config.py:146-324` (`validate_config`)
- Modify: `llmenv.py:177-208` (`cmd_budget`)
- Modify: `setup/setup.sh:35-52` (Step 2)
- Modify: `models.yml.example`
- Test: `tests/test_budget.py`, `tests/test_config.py`, `tests/test_shell.py`

**Interfaces:**
- Consumes: nothing from Tasks 1-3.
- Produces: `compute_budget(..., vram_budget_ceiling_mib: int | None = None)` — `None` means uncapped (today's behavior). Config keys `gpu.vram_budget_ceiling_pct`, `gpu.vram_budget_ceiling_mib`.

- [ ] **Step 1: Write the failing `pylib/budget.py` unit tests**

Add to `tests/test_budget.py`, after `test_budget_feasible_when_models_fit` (line 146-157):

```python
def test_vram_ceiling_below_total_reduces_available_mib():
    uncapped = compute_budget(
        vram_total_mib=16304,
        compositor_used_mib=300,
        reserve_floor_mib=1024,
        model_costs=[cost("a", 7305, 640)],
        models_max=1,
    )
    capped = compute_budget(
        vram_total_mib=16304,
        compositor_used_mib=300,
        reserve_floor_mib=1024,
        model_costs=[cost("a", 7305, 640)],
        models_max=1,
        vram_budget_ceiling_mib=13619,
    )
    assert capped["available_mib"] == 13619 - 1024 - SPIKE_HEADROOM_MIB
    assert capped["available_mib"] < uncapped["available_mib"]


def test_vram_ceiling_above_total_is_a_no_op():
    result = compute_budget(
        vram_total_mib=16304,
        compositor_used_mib=300,
        reserve_floor_mib=1024,
        model_costs=[cost("a", 7305, 640)],
        models_max=1,
        vram_budget_ceiling_mib=20000,
    )
    assert result["available_mib"] == 16304 - 1024 - SPIKE_HEADROOM_MIB
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_budget.py -k ceiling -v`
Expected: FAIL with `TypeError: compute_budget() got an unexpected keyword argument 'vram_budget_ceiling_mib'`.

- [ ] **Step 3: Implement in `pylib/budget.py`**

Change the `compute_budget` signature (line 103-111):

```python
def compute_budget(
    vram_total_mib: int,
    compositor_used_mib: int,
    reserve_floor_mib: int,
    model_costs: list[dict[str, Any]],
    models_max: int,
    cache_type_k: str = "f16",
    cache_type_v: str = "f16",
    vram_budget_ceiling_mib: int | None = None,
) -> dict[str, Any]:
```

Change line 114-115 from:

```python
    reserve = max(compositor_used_mib, reserve_floor_mib)
    available = vram_total_mib - reserve - SPIKE_HEADROOM_MIB
```

to:

```python
    ceiling_mib = (
        vram_total_mib if vram_budget_ceiling_mib is None
        else min(vram_total_mib, vram_budget_ceiling_mib)
    )
    reserve = max(compositor_used_mib, reserve_floor_mib)
    available = ceiling_mib - reserve - SPIKE_HEADROOM_MIB
```

The return dict (lines 164-178) is unchanged — `available_mib` already reflects the new arithmetic through the existing `"available_mib": available` line.

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_budget.py -v`
Expected: PASS (full file).

- [ ] **Step 5: Write the failing `pylib/config.py` tests**

Add to `tests/test_config.py`, after the RAM-ceiling tests from Task 3:

```python
def test_migrate_config_adds_default_vram_budget_ceiling():
    cfg = make_cfg()
    del cfg["gpu"]["vram_budget_ceiling_pct"]
    del cfg["gpu"]["vram_budget_ceiling_mib"]
    migrated = config_module.migrate_config(copy.deepcopy(cfg))
    assert migrated["gpu"]["vram_budget_ceiling_pct"] == 95
    assert migrated["gpu"]["vram_budget_ceiling_mib"] == cfg["gpu"]["vram_total_mib"]


def test_migrate_config_preserves_existing_vram_budget_ceiling():
    cfg = make_cfg()
    cfg["gpu"]["vram_budget_ceiling_pct"] = 80
    cfg["gpu"]["vram_budget_ceiling_mib"] = 12000
    migrated = config_module.migrate_config(copy.deepcopy(cfg))
    assert migrated["gpu"]["vram_budget_ceiling_pct"] == 80
    assert migrated["gpu"]["vram_budget_ceiling_mib"] == 12000


@pytest.mark.parametrize("value", [0, -1, 101, float("inf"), "lots", True])
def test_validate_config_rejects_invalid_vram_ceiling_pct(value):
    cfg = make_cfg()
    cfg["gpu"]["vram_budget_ceiling_pct"] = value
    errors = validate_config(cfg)
    assert any("vram_budget_ceiling_pct" in error for error in errors)


@pytest.mark.parametrize("value", [-1, 1.5, "lots", True])
def test_validate_config_rejects_invalid_vram_ceiling_mib(value):
    cfg = make_cfg()
    cfg["gpu"]["vram_budget_ceiling_mib"] = value
    errors = validate_config(cfg)
    assert any("vram_budget_ceiling_mib" in error for error in errors)
```

Also add the two new keys to `make_cfg`'s default `gpu` dict (`tests/test_config.py:34-49`): `"vram_budget_ceiling_pct": 95` and `"vram_budget_ceiling_mib": 16304` (matching the fixture's `vram_total_mib`).

- [ ] **Step 6: Run to verify it fails**

Run: `uv run pytest tests/test_config.py -k vram_ceiling -v`
Expected: FAIL — fields don't exist / aren't validated yet.

- [ ] **Step 7: Implement in `pylib/config.py`**

In `migrate_config`, inside the existing `if isinstance(gpu, dict):` block (after the early-return check, before the `benchmark` handling — see lines 88-93), add:

```python
    gpu.setdefault("vram_budget_ceiling_pct", 95)
    gpu.setdefault("vram_budget_ceiling_mib", gpu.get("vram_total_mib", 0))
```

so the full block reads:

```python
    gpu = cfg.get("gpu")
    if not isinstance(gpu, dict):
        return cfg
    gpu.setdefault("vram_budget_ceiling_pct", 95)
    gpu.setdefault("vram_budget_ceiling_mib", gpu.get("vram_total_mib", 0))
    benchmark = gpu.get("benchmark")
    if isinstance(benchmark, dict):
        benchmark.pop("rocm", None)
    return cfg
```

In `validate_config`, inside the `gpu = cfg["gpu"]` block (after line 172's `reserve_mode` check), add:

```python
    ceiling_pct = gpu.get("vram_budget_ceiling_pct", 95)
    if isinstance(ceiling_pct, bool) or not (
        _finite_number(ceiling_pct) and 0 < ceiling_pct <= 100
    ):
        errors.append(
            "gpu.vram_budget_ceiling_pct must be a finite number greater "
            "than 0 and at most 100"
        )
    ceiling_mib = gpu.get("vram_budget_ceiling_mib", gpu.get("vram_total_mib", 0))
    if not _positive_int(ceiling_mib) and ceiling_mib != 0:
        errors.append("gpu.vram_budget_ceiling_mib must be a non-negative integer")
```

- [ ] **Step 8: Run to verify it passes**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS (full file).

- [ ] **Step 9: Wire `cmd_budget` to the new field**

Write the failing test first — add to `tests/test_cli.py` near the existing budget tests:

```python
def test_budget_respects_the_configured_vram_ceiling(tmp_path: Path):
    config = write_test_config(tmp_path)
    parsed = yaml.safe_load(config.read_text())
    parsed["gpu"]["vram_budget_ceiling_mib"] = 100
    config.write_text(yaml.safe_dump(parsed, sort_keys=False))

    result = run("budget", "--config", str(config), "--models-dir", str(tmp_path))
    payload = json.loads(result.stdout)
    assert payload["available_mib"] <= 100
```

Run: `uv run pytest tests/test_cli.py::test_budget_respects_the_configured_vram_ceiling -v`
Expected: FAIL — `cmd_budget` doesn't read the field yet, `available_mib` reflects the full 16304 MiB total.

Implement in `llmenv.py`'s `cmd_budget` (line 197-205), add `vram_budget_ceiling_mib=cfg["gpu"]["vram_budget_ceiling_mib"],` to the `compute_budget(...)` call:

```python
    result = compute_budget(
        vram_total_mib=gpu["vram_total_mib"],
        compositor_used_mib=compositor_used,
        reserve_floor_mib=cfg["gpu"]["reserve_floor_mib"],
        model_costs=_model_costs(cfg, Path(args.models_dir)),
        models_max=runtime["models_max"],
        cache_type_k=runtime["cache_type_k"],
        cache_type_v=runtime["cache_type_v"],
        vram_budget_ceiling_mib=cfg["gpu"]["vram_budget_ceiling_mib"],
    )
```

Run: `uv run pytest tests/test_cli.py::test_budget_respects_the_configured_vram_ceiling -v`
Expected: PASS.

- [ ] **Step 10: `setup.sh` computes and persists the ceiling in Step 2**

Write the failing shell test first. Add to `tests/test_shell.py` near `test_setup_gpu_rows_include_measured_used_and_free_vram` (line 602-612):

```python
def test_setup_persists_a_vram_ceiling_computed_from_free_vram_at_setup_time(
    tmp_path: pathlib.Path,
) -> None:
    """Ceiling = pct * (total - used) at the moment setup ran, not of total."""
    result, _, config = run_setup_with_numbered_selection(tmp_path, "1\n1\n2\n")

    assert result.returncode == 0, result.stderr
    # fixture GPU: vram_total_mib=16384, vram_used_mib=2048, default pct 95
    # (16384 - 2048) * 95 / 100 = 13619.2 -> round to 13619
    assert yq_value(config, ".gpu.vram_budget_ceiling_mib") == "13619"
```

(`yq_value` is the existing helper already used elsewhere in this file, e.g. Task 3 Step 10's context — confirm its exact signature by finding its definition with `grep -n "^def yq_value" tests/test_shell.py` and match the call style already used by neighboring tests.)

Run: `uv run pytest tests/test_shell.py::test_setup_persists_a_vram_ceiling_computed_from_free_vram_at_setup_time -v`
Expected: FAIL — `.gpu.vram_budget_ceiling_mib` doesn't exist in the persisted config.

Implement in `setup/setup.sh`. After line 51 (`vram_free="$((vram_total - vram_used))"`) and before line 52's `log_info`, add:

```bash
ceiling_pct="$(yq -r '.gpu.vram_budget_ceiling_pct // 95' "$CONFIG_PATH")"
vram_budget_ceiling_mib="$(jq -n --argjson free "$vram_free" --argjson pct "$ceiling_pct" \
    '(($free * $pct / 100) | round)')"
```

Then extend the existing yq write block at lines 117-122 to also persist it:

```bash
PCI_ADDRESS="$pci" VRAM_TOTAL_MIB="$vram_total" DEVICE_NAME="$device_name" \
  VRAM_BUDGET_CEILING_MIB="$vram_budget_ceiling_mib" \
  yq -i '
    .gpu.pci_address = strenv(PCI_ADDRESS) |
    .gpu.vram_total_mib = (strenv(VRAM_TOTAL_MIB) | tonumber) |
    .gpu.device_name = strenv(DEVICE_NAME) |
    .gpu.vram_budget_ceiling_mib = (strenv(VRAM_BUDGET_CEILING_MIB) | tonumber)
  ' "$CONFIG_PATH"
```

Note this write happens later in the script (Step 6, around line 117) than where `vram_budget_ceiling_mib` is computed (Step 2, around line 51) — `vram_budget_ceiling_mib` is a plain bash variable so it stays in scope across the intervening steps; no restructuring needed.

Run: `uv run pytest tests/test_shell.py::test_setup_persists_a_vram_ceiling_computed_from_free_vram_at_setup_time -v`
Expected: PASS.

- [ ] **Step 11: Fix the exact-equality test Task 2 deferred**

`test_setup_selects_zero_match_vulkan_device_and_persists_config` (`tests/test_shell.py:615`, already updated in Task 2 Step 5 for single-select) needs its `persisted["gpu"]` assertion completed:

```python
    assert persisted["gpu"] == {
        "pci_address": "0000:03:00.0",
        "vram_total_mib": 16384,
        "device_name": 'Fallback Radeon: "safe"',
        "vram_budget_ceiling_mib": 13619,
    }
```

Run: `uv run pytest tests/test_shell.py::test_setup_selects_zero_match_vulkan_device_and_persists_config -v`
Expected: PASS.

- [ ] **Step 12: Update `models.yml.example`**

Add to the `gpu:` block:

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
  vram_budget_ceiling_mib: 0
  benchmark:
    vulkan:
      pp_tps: null
      tg_tps: null
      measured_at: null
```

(`vram_budget_ceiling_mib: 0` mirrors `vram_total_mib: 0`'s existing convention — a placeholder until `make setup` computes the real value.)

- [ ] **Step 13: Full regression check**

Run: `uv run pytest tests/test_budget.py tests/test_config.py tests/test_cli.py tests/test_shell.py -v`
Expected: all PASS.

- [ ] **Step 14: Commit**

```bash
git add pylib/budget.py pylib/config.py llmenv.py setup/setup.sh models.yml.example \
  tests/test_budget.py tests/test_config.py tests/test_cli.py tests/test_shell.py
git commit -m "feat(budget): cap VRAM planning at a configurable percent of what's free at setup time"
```

---

### Task 5: Final verification and docs

**Files:**
- Modify: `.agents/architecture.md` (Invariants section)

**Interfaces:**
- Consumes: nothing new — this is a wrap-up pass.

- [ ] **Step 1: Full suite, lint, and shellcheck**

Run:
```bash
uv run pytest -q
uv run ruff check .
shellcheck setup/*.sh scripts/*.sh tools/*.sh
```
Expected: all clean. Fix anything that surfaces before continuing.

- [ ] **Step 2: Document the two ceilings in the Invariants section**

In `.agents/architecture.md`, in the `## Invariants` list (around line 171-192), add two lines:

```markdown
- `resources.llm_server.memory_ceiling_pct` (default 46) caps `llm-server`'s
  RAM at that percent of total host RAM, regardless of how much is otherwise
  free — computed live by `compute_resource_limits()` on every `llmenv
  resources` call.
- `gpu.vram_budget_ceiling_mib` caps VRAM planning at `gpu.vram_budget_ceiling_pct`
  (default 95%) of whatever was free on the dGPU the last time `make setup`
  ran — a resolved snapshot, not re-measured on every `make start`.
```

- [ ] **Step 3: Commit**

```bash
git add .agents/architecture.md
git commit -m "docs: document the RAM and VRAM ceiling invariants"
```

## Self-Review Notes

- **Spec coverage:** presets.ini copy (Task 1) ✓; single-model setup (Task 2) ✓; RAM ceiling (Task 3) ✓; VRAM ceiling (Task 4) ✓.
- **Placeholder scan:** none found — every step has concrete code or an exact command.
- **Type consistency:** `compute_resource_limits(host_cpu_count, host_memory_total_mib, memory_ceiling_pct=100)` and `compute_budget(..., vram_budget_ceiling_mib=None)` signatures are used identically across every task/step that calls them.
