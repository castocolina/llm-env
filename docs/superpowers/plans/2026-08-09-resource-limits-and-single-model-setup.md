# Resource Limits and Single-Model Setup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Copy `presets.ini` to the repo inspection dir, restrict `make setup` to one enabled model at a time, and add two real resource ceilings (RAM: fixed % of total host RAM; VRAM: % of what's free on the dGPU at setup time) that cap what `llmenv budget`/`llmenv resources` compute as available/permitted for `llm-server`. Enforcement of that computed budget at container-start time is existing, unchanged, diagnostic-only behavior (`setup.sh` warns and continues on an infeasible budget) — this plan does not add enforcement.

**Architecture:** Four independent, additive changes to existing modules — no new files except this plan's own tests. RAM ceiling lives in `pylib/resources.py::compute_resource_limits()` (a new parameter, applied every time `llmenv resources` runs). VRAM ceiling is computed once by `setup/setup.sh` (where GPU total/used are already measured) and stored as a resolved absolute MiB value that `pylib/budget.py::compute_budget()` treats as a second cap alongside `vram_total_mib`.

**Tech Stack:** Bash (`setup/setup.sh`, `setup/render-unit.sh`), Python (`pylib/config.py`, `pylib/resources.py`, `pylib/budget.py`, `llmenv.py`), pytest, yq/jq.

## Global Constraints

- `resources.llm_server.memory_ceiling_pct` default: `46` (percent of total host RAM — 46% of a 32768 MiB host is 15073 MiB ≈ 14.72 GiB; `46` was chosen as a round configured percentage in this range, not as the exact closest whole percent to any specific GiB target).
- `gpu.vram_budget_ceiling_pct` default: `95` (percent of VRAM *free* on the configured GPU at the moment `make setup` last ran, not of the total).
- **Both computed ceilings are floored, and the floor itself is now configurable, at two tiers:**
  a config-level default of **10 GiB (10240 MiB)** — `resources.llm_server.memory_ceiling_floor_mib`
  and `gpu.vram_budget_ceiling_floor_mib`, both backfilled by `migrate_config` — and a lower
  **code-level fallback of 6 GiB (6144 MiB)**, used only when the config value is genuinely absent
  (e.g. `compute_resource_limits()`'s own `memory_ceiling_floor_mib: int = 6144` parameter default,
  and `setup.sh`'s `yq -r '.gpu.vram_budget_ceiling_floor_mib // 6144' "$CONFIG_PATH"`). This
  mirrors the existing two-tier pattern already used for `memory_ceiling_pct`/`vram_budget_ceiling_pct`
  (a permissive code-level default vs. a real, opinionated config-level default). In normal
  operation (a config that has been through `migrate_config`, i.e. any config `make setup` has
  touched) the floor is always 10240 — the 6144 code-level fallback only matters for a config that
  bypassed migration (a hand-crafted config, or a test fixture that stubs `migrate-config` as a
  no-op). Either way, a valid-but-small configured percentage (e.g. a tiny `memory_ceiling_pct` or
  `vram_budget_ceiling_pct`) can never cap `llm-server`'s planned RAM or VRAM below the floor. The
  floor applies to the *computed* ceiling only; it does not raise `vram_total_mib` or the
  RAM-floor/CPU-floor checks that already raise `ResourceError` on genuinely too-small hosts.
- `gpu.vram_budget_ceiling_mib: 0` is a documented "no cap" sentinel — the placeholder value in
  `models.yml.example` before `make setup` has ever computed a real one. `cmd_budget` must treat a
  configured `0` identically to an unset/`None` ceiling (i.e. uncapped, using the full
  `vram_total_mib`), never as a literal zero-MiB budget. In the normal flow this case becomes rare
  once the 10 GiB floor above lands (`setup.sh` will never legitimately write `0` once a GPU is
  configured), but hand-edited or pre-`make setup` configs can still have `vram_total_mib` and the
  migrated-default `vram_budget_ceiling_mib` both legitimately be `0`.
- `gpu.vram_budget_ceiling_mib` is a **resolved, absolute** value written by `setup.sh`, not evaluated live — it is a snapshot of what was free at the last `make setup` run. It does not track later changes in what else is using the GPU, in either direction: if another process starts using VRAM afterward, the stored ceiling can end up *higher* than what's actually free at that later moment, not lower. Re-run `make setup` to refresh it.
- Both ceilings are **diagnostic only**, matching this codebase's existing budget behavior: they cap what `compute_resource_limits()`/`compute_budget()` report as available/permitted, but nothing downstream clamps what `llama-server` actually loads (`pylib/presets.py` always emits the model's configured `n_gpu_layers` unchanged), and `setup/setup.sh`'s Step 7/8 VRAM-budget check only warns and continues (`log_warn`, no `die`) when the computed budget doesn't fit — this plan does not change that; it only makes the computed numbers more accurate. This is pre-existing behavior of the whole budget system, not something this plan introduces or fixes.
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

The existing render-unit fixture helper (`run_render_unit_with_legacy_rocm_config` at
`tests/test_shell.py:1129-1213`) already sets `LLM_ENV_COMPOSE_INSPECT_DIR` in its subprocess
environment and returns a 2-tuple: `return result, home / ".config/systemd/user/llm-server.service"`.
It has three other callers in this file (`tests/test_shell.py:1218`, `1225`/wrapper-unit test, and
`1341`) that all unpack that same 2-tuple. **Do not change the fixture's signature or return
value** — every path this new test needs (`presets_path`, `compose_inspect_dir`, the rendered
`config`) is already derivable from `tmp_path` without touching the fixture, since the fixture
itself builds `home = tmp_path / "home"` and `config = tmp_path / "models.yml"` and passes
`LLM_ENV_COMPOSE_INSPECT_DIR=str(tmp_path / "compose-inspect")`.

Add a new test right after `test_render_unit_writes_a_compose_file_and_wrapper_unit` (around line
1224):

```python
def test_render_unit_copies_presets_ini_to_the_inspect_dir(
    tmp_path: pathlib.Path,
) -> None:
    result, _wrapper_unit = run_render_unit_with_legacy_rocm_config(tmp_path)

    presets_path = tmp_path / "home" / ".config/llm-env/presets.ini"
    compose_inspect_dir = tmp_path / "compose-inspect"

    assert result.returncode == 0, result.stdout + result.stderr
    inspect_copy = compose_inspect_dir / "presets.ini"
    assert inspect_copy.is_file()
    assert inspect_copy.read_text() == presets_path.read_text()
```

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

First, fix the shared `uv` stub — it currently ignores its arguments. In
`run_setup_with_numbered_selection` (`tests/test_shell.py`, the `models select` case inside the
`uv` stub, ~lines 528-531), the stub hardcodes which aliases get enabled regardless of what was
actually passed to `models select`:

```python
"  *' models select '*)\n"
"    if [ \"$REAL_MODELS_SELECT\" = 1 ]; then exec \"$REAL_UV\" \"$@\"; fi\n"
"    \"$REAL_YQ\" -i '.models[] |= (.enabled = (.alias == \"gemma4\" or .alias == \"ornith\")) | .runtime.models_max = 1' \"$CONFIG_PATH_TEST\"\n"
"    printf '%s\\n' '{\"models_max\":1}' ;;\n"
```

Once model selection is single-alias-only (this task's Step 3), a test that selects only
`gemma4` must persist `gemma4.enabled == True` and `ornith.enabled == False` — but this stub
always enables both, no matter which alias was passed. Replace it with a version that reads the
alias actually given after `select` and enables only that one.

Note: the real `yq` binary in this codebase is mikefarah/yq v4, which has no `--arg`/`--argjson`
flag (that's `jq` syntax, not `yq`'s) — do not use `--argjson` here. Use the same env-var +
`strenv()` pattern already used everywhere else in this codebase's `yq` calls (e.g.
`setup/setup.sh`'s `PCI_ADDRESS="$pci" ... yq -i '.gpu.pci_address = strenv(PCI_ADDRESS) | ...'`,
and this same plan's own Task 4 Step 10
`VRAM_BUDGET_CEILING_MIB=... yq -i '... strenv(VRAM_BUDGET_CEILING_MIB) | tonumber ...'`). Task 2
Step 3 restricts `setup.sh` to pass exactly one alias to `models select`, so the stub only ever
needs to capture the single trailing alias argument and compare it with a plain `strenv()`
equality — no membership/subset check across multiple aliases is needed:

```python
"  *' models select '*)\n"
"    if [ \"$REAL_MODELS_SELECT\" = 1 ]; then exec \"$REAL_UV\" \"$@\"; fi\n"
"    for arg in \"$@\"; do selected_alias=\"$arg\"; done\n"
"    SELECTED_ALIAS=\"$selected_alias\" \"$REAL_YQ\" -i \\\n"
"      '.models[] |= (.enabled = (.alias == strenv(SELECTED_ALIAS))) | .runtime.models_max = 1' \\\n"
"      \"$CONFIG_PATH_TEST\"\n"
"    printf '%s\\n' '{\"models_max\":1}' ;;\n"
```

(The last positional argument to `models select <alias>` is always the alias itself, since
Task 2's `setup.sh` change only ever invokes it with one. `SELECTED_ALIAS=... "$REAL_YQ" -i
'... strenv(SELECTED_ALIAS) ...'` sets the env var only for that command, matching the pattern
used by every other `yq -i` call already in this codebase.)

Run: `uv run pytest tests/test_shell.py -k setup -v`
Expected: several FAILs — both from the stub change and from tests that still pass `"1,2"` as the
model-selection input.

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

Keep whatever assertions follow this point in the original test body unchanged (only the setup/reorder prelude changes) — read the rest of the original test (continues past `tests/test_shell.py:707`) and preserve it verbatim under the new prelude.

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
- Produces: `compute_resource_limits(host_cpu_count: int, host_memory_total_mib: int, memory_ceiling_pct: float = 100, memory_ceiling_floor_mib: int = 6144) -> dict` (two new parameters; defaults preserve old behavior / provide a conservative code-level floor). Config keys `resources.llm_server.memory_ceiling_pct`, `resources.llm_server.memory_ceiling_floor_mib`.

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


def test_memory_ceiling_floor_defaults_to_6_gib_when_unspecified():
    """Code-level safety net only — the real, user-facing floor lives in
    models.yml via migrate_config's 10 GiB default (see the config tests
    below); this proves compute_resource_limits() itself never lets an
    unspecified floor disappear, even if a caller forgets to thread the
    config value through."""
    result = compute_resource_limits(
        host_cpu_count=8, host_memory_total_mib=32768, memory_ceiling_pct=0.001
    )
    assert result["llm_server"]["memory_mib"] == 6144


def test_memory_ceiling_floors_at_the_configured_value_instead_of_rounding_to_zero():
    """A tiny-but-valid pct (validate_config only requires 0 < pct <= 100)
    must not round down to a near-zero cap — pylib/compose.py omits
    `mem_limit` entirely when memory_mib is falsy, which would silently
    uncap the container. Unfloored, 32768 * 0.001 / 100 rounds to 0;
    floored at the configured 10240, the ceiling becomes exactly that, not
    some other small percentage-only value."""
    result = compute_resource_limits(
        host_cpu_count=8,
        host_memory_total_mib=32768,
        memory_ceiling_pct=0.001,
        memory_ceiling_floor_mib=10240,
    )
    assert result["llm_server"]["memory_mib"] == 10240
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
    memory_ceiling_floor_mib: int = 6144,
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
    # pylib/compose.py omits `mem_limit` entirely when memory_mib is
    # falsy/0, which would silently leave the container fully uncapped for
    # a valid-but-tiny memory_ceiling_pct. memory_ceiling_floor_mib's
    # real, user-facing default (10 GiB) lives in models.yml via
    # migrate_config; the 6 GiB default here is only a code-level safety
    # net for callers that don't thread the configured value through.
    memory_ceiling_mib = max(
        memory_ceiling_floor_mib,
        round(host_memory_total_mib * memory_ceiling_pct / 100),
    )
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
    cfg = make_cfg(
        resources={
            "llm_server": {
                "cpus": 6,
                "memory_mib": 28672,
                "memory_ceiling_pct": 30,
                "memory_ceiling_floor_mib": 10240,
            }
        }
    )
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


def test_migrate_config_adds_default_memory_ceiling_floor_mib():
    cfg = make_cfg()
    cfg["resources"]["llm_server"].pop("memory_ceiling_floor_mib", None)
    migrated = config_module.migrate_config(copy.deepcopy(cfg))
    assert migrated["resources"]["llm_server"]["memory_ceiling_floor_mib"] == 10240


def test_migrate_config_preserves_existing_memory_ceiling_floor_mib():
    cfg = make_cfg(
        resources={
            "llm_server": {
                "cpus": 6,
                "memory_mib": 28672,
                "memory_ceiling_pct": 46,
                "memory_ceiling_floor_mib": 2048,
            }
        }
    )
    migrated = config_module.migrate_config(copy.deepcopy(cfg))
    assert migrated["resources"]["llm_server"]["memory_ceiling_floor_mib"] == 2048


@pytest.mark.parametrize("value", [0, -1, 1.5, "lots", True])
def test_validate_config_rejects_invalid_memory_ceiling_floor_mib(value):
    cfg = make_cfg(
        resources={
            "llm_server": {"cpus": 0, "memory_mib": 0, "memory_ceiling_floor_mib": value}
        }
    )
    errors = validate_config(cfg)
    assert any("memory_ceiling_floor_mib" in error for error in errors)
```

Also update `make_cfg`'s default `resources.llm_server` dict (`tests/test_config.py:59`) from
`{"cpus": 0, "memory_mib": 0}` to
`{"cpus": 0, "memory_mib": 0, "memory_ceiling_pct": 46, "memory_ceiling_floor_mib": 10240}`, and
fix the three now-broken exact-equality assertions:
- `test_migrate_config_adds_default_resources_section` (line 553-556): expected dict becomes
  `{"cpus": 0, "memory_mib": 0, "memory_ceiling_pct": 46, "memory_ceiling_floor_mib": 10240}`.
- `test_migrate_config_adds_default_resources_section_when_gpu_absent` (line 575-580): same change.
- `test_migrate_config_preserves_existing_resources_values` (line 559-562): this one passes an
  explicit `resources={"llm_server": {"cpus": 6, "memory_mib": 28672}}` override that omits both
  new keys, so `migrate_config`'s new `setdefault` calls add them here too — change:
  ```python
  def test_migrate_config_preserves_existing_resources_values():
      cfg = make_cfg(resources={"llm_server": {"cpus": 6, "memory_mib": 28672}})
      migrated = config_module.migrate_config(copy.deepcopy(cfg))
      assert migrated["resources"]["llm_server"] == {
          "cpus": 6,
          "memory_mib": 28672,
          "memory_ceiling_pct": 46,
          "memory_ceiling_floor_mib": 10240,
      }
  ```

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
            llm_server_resources.setdefault("memory_ceiling_floor_mib", 10240)
```

In `validate_config` (the `cpus` check is at pylib/config.py:190-196; the `memory_mib` check —
the actual "after" anchor for this addition — is at lines 197-202, inside the same
`llm_server_resources` `isinstance` branch), add after the existing `memory_mib` check:

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
                memory_ceiling_floor_mib = llm_server_resources.get(
                    "memory_ceiling_floor_mib", 10240
                )
                if isinstance(memory_ceiling_floor_mib, bool) or not _positive_int(
                    memory_ceiling_floor_mib
                ):
                    errors.append(
                        "resources.llm_server.memory_ceiling_floor_mib must be "
                        "a positive integer"
                    )
```

- [ ] **Step 8: Run to verify it passes**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS (full file — check no other exact-equality test broke; fix any the same way as Step 5 if so).

- [ ] **Step 9: Wire `llmenv.py`'s `resources` command to config**

Write the failing CLI test first. Replace `test_resources_emits_host_and_llm_server_limits` in
`tests/test_cli.py` (line 837-846). The original version tolerated `ResourceError` (returncode 1,
`"error"` in payload) on hosts too small to clear `HOST_CPU_FLOOR`/`HOST_MEMORY_FLOOR_MIB` —
keep that tolerance so this test stays portable to constrained CI runners. Pin
`memory_ceiling_pct` to a known low value in the fixture config so the success branch actually
proves the config value is threaded into `compute_resource_limits()` (an implementation that adds
`--config` to the parser but forgets to pass `memory_ceiling_pct` through would still return a
`memory_mib >= 0`, so a loose bound doesn't catch that bug):

`write_test_config`'s fixture YAML has no `resources:` section on disk — `load_config` only adds
the `memory_ceiling_pct`/`memory_ceiling_floor_mib` defaults in memory via `migrate_config`, it
doesn't persist them back to the file — so set the section explicitly with `setdefault` rather
than indexing straight into `parsed["resources"]`. Pin `memory_ceiling_floor_mib` explicitly too
(rather than relying on the 10240 migration default) so the test's own `expected_ceiling_mib`
arithmetic doesn't have to guess which floor is in effect:

```python
def test_resources_emits_host_and_llm_server_limits(tmp_path: Path):
    config = write_test_config(tmp_path)
    parsed = yaml.safe_load(config.read_text())
    llm_server_resources = parsed.setdefault("resources", {}).setdefault("llm_server", {})
    llm_server_resources["memory_ceiling_pct"] = 10
    llm_server_resources["memory_ceiling_floor_mib"] = 1
    config.write_text(yaml.safe_dump(parsed, sort_keys=False))

    result = run("resources", "--config", str(config))
    payload = json.loads(result.stdout)
    if result.returncode == 0:
        assert payload["host"]["cpu_count"] > 0
        assert payload["llm_server"]["cpus"] >= 0
        host_total_mib = payload["host"]["memory_total_mib"]
        # floor pinned to 1 MiB (below any realistic 10% of host RAM) so
        # this test proves memory_ceiling_pct reached
        # compute_resource_limits() and isn't masked by the floor.
        expected_ceiling_mib = max(1, round(host_total_mib * 10 / 100))
        assert payload["llm_server"]["memory_mib"] <= expected_ceiling_mib
        # A pct this low always caps below the uncapped remainder on any
        # host that clears the floor checks, so this also proves the
        # config value reached compute_resource_limits() rather than the
        # implementation silently defaulting to pct=100.
        assert payload["llm_server"]["memory_mib"] == expected_ceiling_mib
    else:
        assert result.returncode == 1
        assert "error" in payload
```

Add one more CLI test proving the *floor* itself is threaded from config, not hardcoded — without
it, an implementation that reads `memory_ceiling_pct` correctly but ignores
`memory_ceiling_floor_mib` (always using the 6144 code-level default) would still pass every other
test in this task:

```python
def test_resources_respects_the_configured_memory_ceiling_floor(tmp_path: Path):
    config = write_test_config(tmp_path)
    parsed = yaml.safe_load(config.read_text())
    llm_server_resources = parsed.setdefault("resources", {}).setdefault("llm_server", {})
    # A pct tiny enough that, unfloored, it would round to far below the
    # configured floor -- this only passes if the floor is actually read
    # from config rather than defaulting to 6144.
    llm_server_resources["memory_ceiling_pct"] = 0.001
    llm_server_resources["memory_ceiling_floor_mib"] = 9000
    config.write_text(yaml.safe_dump(parsed, sort_keys=False))

    result = run("resources", "--config", str(config))
    payload = json.loads(result.stdout)
    if result.returncode == 0:
        # Computed relative to the real host's own reported total (like the
        # neighboring test above), not a bare `== 9000` -- on a small-but-valid
        # CI host, host_total_mib - memory_floor_mib can be below 9000, in
        # which case min() keeps the ordinary remainder, not the floor.
        host_total_mib = payload["host"]["memory_total_mib"]
        memory_floor_mib = payload["host_memory_floor_mib"] + payload["omniroute"]["memory_mib"]
        expected_ceiling_mib = min(host_total_mib - memory_floor_mib, 9000)
        assert payload["llm_server"]["memory_mib"] == expected_ceiling_mib
    else:
        assert result.returncode == 1
        assert "error" in payload
```

Run:
```bash
uv run pytest tests/test_cli.py::test_resources_emits_host_and_llm_server_limits -v
uv run pytest tests/test_cli.py::test_resources_respects_the_configured_memory_ceiling_floor -v
```
Expected: both FAIL — `resources` subcommand doesn't accept `--config` yet (argparse "unrecognized arguments").

Implement in `llmenv.py`. Change `cmd_resources` (lines 211-214):

```python
def cmd_resources(args: argparse.Namespace) -> int:
    cfg = require_valid_config(load_config(Path(args.config)))
    host = host_resources()
    llm_server_resources = cfg["resources"]["llm_server"]
    limits = compute_resource_limits(
        host["cpu_count"],
        host["memory_total_mib"],
        llm_server_resources["memory_ceiling_pct"],
        llm_server_resources["memory_ceiling_floor_mib"],
    )
    return emit({"host": host, **limits})
```

Change the parser registration (line 356):

```python
    resources_parser = sub.add_parser("resources")
    resources_parser.add_argument("--config", default=argparse.SUPPRESS)
    resources_parser.set_defaults(func=cmd_resources)
```

Run:
```bash
uv run pytest tests/test_cli.py::test_resources_emits_host_and_llm_server_limits -v
uv run pytest tests/test_cli.py::test_resources_respects_the_configured_memory_ceiling_floor -v
```
Expected: both PASS.

- [ ] **Step 10: Point `setup.sh`'s Step 8 at the real config path**

`setup/setup.sh:142` currently calls `llmenv resources > "$resources_json"` with no `--config` — now that `cmd_resources` loads config, it must target `$CONFIG_PATH` explicitly (matches every other `llmenv` call in this script). (Line 142 is accurate against the file before Task 2 Step 3's edit; if Task 2 has already landed by the time this step executes, the same line reads approximately 135 — find it by the `llmenv resources >` text, which is unambiguous either way.) Change it to:

```bash
if llmenv --config "$CONFIG_PATH" resources > "$resources_json"; then
```

Run: `uv run pytest tests/test_shell.py -k setup -v`
Expected: PASS — the `uv` stub in `run_setup_with_numbered_selection` matches on `*' resources')`, a suffix match unaffected by the added `--config` prefix.

- [ ] **Step 11: Update `models.yml.example`**

Add `memory_ceiling_pct: 46` and `memory_ceiling_floor_mib: 10240` to the `resources.llm_server`
block:

```yaml
resources:
  llm_server:
    cpus: 0
    memory_mib: 0
    memory_ceiling_pct: 46
    memory_ceiling_floor_mib: 10240
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
- Test: `tests/test_budget.py`, `tests/test_config.py`, `tests/test_shell.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: nothing from Tasks 1-3.
- Produces: `compute_budget(..., vram_budget_ceiling_mib: int | None = None)` — `None` means uncapped (today's behavior). Config keys `gpu.vram_budget_ceiling_pct`, `gpu.vram_budget_ceiling_mib`, `gpu.vram_budget_ceiling_floor_mib`.

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


@pytest.mark.parametrize("value", [-1, 1.5, "lots", True, False])
def test_validate_config_rejects_invalid_vram_ceiling_mib(value):
    cfg = make_cfg()
    cfg["gpu"]["vram_budget_ceiling_mib"] = value
    errors = validate_config(cfg)
    assert any("vram_budget_ceiling_mib" in error for error in errors)


def test_migrate_config_adds_default_vram_budget_ceiling_floor_mib():
    cfg = make_cfg()
    del cfg["gpu"]["vram_budget_ceiling_floor_mib"]
    migrated = config_module.migrate_config(copy.deepcopy(cfg))
    assert migrated["gpu"]["vram_budget_ceiling_floor_mib"] == 10240


def test_migrate_config_preserves_existing_vram_budget_ceiling_floor_mib():
    cfg = make_cfg()
    cfg["gpu"]["vram_budget_ceiling_floor_mib"] = 2048
    migrated = config_module.migrate_config(copy.deepcopy(cfg))
    assert migrated["gpu"]["vram_budget_ceiling_floor_mib"] == 2048


@pytest.mark.parametrize("value", [0, -1, 1.5, "lots", True])
def test_validate_config_rejects_invalid_vram_ceiling_floor_mib(value):
    cfg = make_cfg()
    cfg["gpu"]["vram_budget_ceiling_floor_mib"] = value
    errors = validate_config(cfg)
    assert any("vram_budget_ceiling_floor_mib" in error for error in errors)
```

`False` is included in `test_validate_config_rejects_invalid_vram_ceiling_mib`'s parametrize list
deliberately: in Python, `False == 0`, so a naive `ceiling_mib != 0` check treats `False` as the
valid "0 means uncapped" sentinel and silently accepts it. Step 7 below adds an explicit
`isinstance(..., bool)` guard to catch this — see that step for why.

Also add the three new keys to `make_cfg`'s default `gpu` dict (`tests/test_config.py:34-49`):
`"vram_budget_ceiling_pct": 95`, `"vram_budget_ceiling_mib": 16304` (matching the fixture's
`vram_total_mib`), and `"vram_budget_ceiling_floor_mib": 10240`.

- [ ] **Step 6: Run to verify it fails**

Run: `uv run pytest tests/test_config.py -k vram_ceiling -v`
Expected: FAIL — fields don't exist / aren't validated yet.

- [ ] **Step 7: Implement in `pylib/config.py`**

In `migrate_config`, inside the existing `if isinstance(gpu, dict):` block (after the early-return check, before the `benchmark` handling — see lines 88-93), add:

```python
    gpu.setdefault("vram_budget_ceiling_pct", 95)
    gpu.setdefault("vram_budget_ceiling_mib", gpu.get("vram_total_mib", 0))
    gpu.setdefault("vram_budget_ceiling_floor_mib", 10240)
```

so the full block reads:

```python
    gpu = cfg.get("gpu")
    if not isinstance(gpu, dict):
        return cfg
    gpu.setdefault("vram_budget_ceiling_pct", 95)
    gpu.setdefault("vram_budget_ceiling_mib", gpu.get("vram_total_mib", 0))
    gpu.setdefault("vram_budget_ceiling_floor_mib", 10240)
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
    if isinstance(ceiling_mib, bool) or not (
        _positive_int(ceiling_mib) or ceiling_mib == 0
    ):
        errors.append("gpu.vram_budget_ceiling_mib must be a non-negative integer")
    ceiling_floor_mib = gpu.get("vram_budget_ceiling_floor_mib", 10240)
    if isinstance(ceiling_floor_mib, bool) or not _positive_int(ceiling_floor_mib):
        errors.append(
            "gpu.vram_budget_ceiling_floor_mib must be a positive integer"
        )
```

The explicit `isinstance(ceiling_mib, bool)` guard matters because Python's `False == 0`: without
it, `vram_budget_ceiling_mib: false` would fall into the `ceiling_mib == 0` branch and pass
validation as if `0` (uncapped-sentinel) had been configured, even though `False` was never a
valid value. This matches how this codebase's existing `cpus`/`o_cpus` checks already guard
against bool (`isinstance(cpus, bool) or not (...)` at `pylib/config.py:191-192` and `208-209`).

- [ ] **Step 8: Run to verify it passes**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS (full file).

- [ ] **Step 9: Wire `cmd_budget` to the new field**

**Files:** also add `tests/test_cli.py` to this task's Files/Test list — this step both fixes an
existing test that Step 7's direct-indexing change would otherwise break, and adds a new one.

First, fix the existing test that direct-indexing breaks.
`test_cmd_budget_passes_configured_models_max` (`tests/test_cli.py:792-822`) monkeypatches
`llmenv.load_config` to return a raw `yaml.safe_load(write_test_config(...))` dict that is never
passed through `migrate_config` — so it has no `gpu.vram_budget_ceiling_mib` key. If `cmd_budget`
indexes `cfg["gpu"]["vram_budget_ceiling_mib"]` directly (as the implementation below does), this
test raises `KeyError` and starts failing. Use `.get(...)` with a fallback to `vram_total_mib`
instead of a bare index, in both the implementation and anywhere a test needs to reason about the
default. This also means `test_cmd_budget_passes_configured_models_max` needs no change — verify
it after Step 7's implementation:

Run: `uv run pytest tests/test_cli.py::test_cmd_budget_passes_configured_models_max -v`
Expected: PASS (unaffected — `.get()` doesn't raise on the missing key).

Now write the failing test for the new behavior — add to `tests/test_cli.py` near the existing
budget tests, following the monkeypatch pattern `test_cmd_budget_passes_configured_models_max`
already uses (real hardware/filesystem state is not available in CI: `cmd_budget` calls the real
`detect()` and `_model_costs()` reads real GGUF headers from `--models-dir`, so both must be
monkeypatched rather than relying on `write_test_config`'s fixture PCI address or a real `.gguf`
file):

```python
def test_budget_respects_the_configured_vram_ceiling(tmp_path, monkeypatch):
    import llmenv

    cfg = yaml.safe_load(write_test_config(tmp_path).read_text())
    cfg["gpu"]["vram_budget_ceiling_mib"] = 100
    facts = {
        "compositor_render_node": "/dev/dri/renderD128",
        "gpus": [
            {
                "pci_address": "0000:03:00.0",
                "render_node": "/dev/dri/renderD129",
                "vram_total_mib": 16304,
                "vram_used_mib": 200,
            }
        ],
    }
    captured = {}

    monkeypatch.setattr(llmenv, "load_config", lambda path: cfg)
    monkeypatch.setattr(llmenv, "detect", lambda: facts)
    monkeypatch.setattr(llmenv, "_model_costs", lambda config, path: [{"alias": "a"}])

    def fake_compute_budget(**kwargs):
        captured.update(kwargs)
        return {"feasible": True, "available_mib": 100}

    monkeypatch.setattr(llmenv, "compute_budget", fake_compute_budget)

    result = llmenv.cmd_budget(SimpleNamespace(config="cfg", models_dir="models"))

    assert result == 0
    assert captured["vram_budget_ceiling_mib"] == 100
```

Run: `uv run pytest tests/test_cli.py::test_budget_respects_the_configured_vram_ceiling -v`
Expected: FAIL — `cmd_budget` doesn't pass `vram_budget_ceiling_mib` to `compute_budget` yet, so
`captured` has no such key (`KeyError`).

Before implementing, also write the failing test for the `0`-sentinel case that `cmd_budget` must
handle at the same time — `gpu.vram_budget_ceiling_mib: 0` is documented (see Global Constraints)
as the same "no cap" sentinel used as `models.yml.example`'s placeholder before `make setup` has
run; `compute_budget()`'s contract is that only `None` means "no cap", so a naive implementation
that passes a configured `0` straight through would make `compute_budget()`'s
`min(vram_total_mib, 0)` collapse the ceiling to `0` and produce a negative `available_mib`
instead of the documented uncapped behavior. Add this test right after
`test_budget_respects_the_configured_vram_ceiling`, following the same monkeypatch pattern:

```python
def test_budget_treats_a_configured_zero_vram_ceiling_as_uncapped(tmp_path, monkeypatch):
    import llmenv

    cfg = yaml.safe_load(write_test_config(tmp_path).read_text())
    cfg["gpu"]["vram_budget_ceiling_mib"] = 0
    facts = {
        "compositor_render_node": "/dev/dri/renderD128",
        "gpus": [
            {
                "pci_address": "0000:03:00.0",
                "render_node": "/dev/dri/renderD129",
                "vram_total_mib": 16304,
                "vram_used_mib": 200,
            }
        ],
    }
    captured = {}

    monkeypatch.setattr(llmenv, "load_config", lambda path: cfg)
    monkeypatch.setattr(llmenv, "detect", lambda: facts)
    monkeypatch.setattr(llmenv, "_model_costs", lambda config, path: [{"alias": "a"}])

    def fake_compute_budget(**kwargs):
        captured.update(kwargs)
        return {"feasible": True, "available_mib": 100}

    monkeypatch.setattr(llmenv, "compute_budget", fake_compute_budget)

    result = llmenv.cmd_budget(SimpleNamespace(config="cfg", models_dir="models"))

    assert result == 0
    # A configured 0 must reach compute_budget() as None (uncapped), never
    # as a literal 0 — passing 0 straight through would make
    # compute_budget()'s min(vram_total_mib, 0) == 0, producing a negative
    # available_mib instead of the documented "no cap" behavior.
    assert captured["vram_budget_ceiling_mib"] is None
```

Run: `uv run pytest tests/test_cli.py::test_budget_treats_a_configured_zero_vram_ceiling_as_uncapped -v`
Expected: FAIL — `cmd_budget` doesn't convert `0` to `None` yet, so `captured["vram_budget_ceiling_mib"] == 0`.

Implement in `llmenv.py`'s `cmd_budget` (line 197-205), add a `vram_budget_ceiling_mib` argument
to the `compute_budget(...)` call, read via `.get()` (not a bare index) so configs that predate
`migrate_config`'s default — like the monkeypatched raw dict in
`test_cmd_budget_passes_configured_models_max` — don't raise `KeyError`, and fold a configured `0`
into the same `None` sentinel so it can never reach `compute_budget()` as a literal zero:

```python
    result = compute_budget(
        vram_total_mib=gpu["vram_total_mib"],
        compositor_used_mib=compositor_used,
        reserve_floor_mib=cfg["gpu"]["reserve_floor_mib"],
        model_costs=_model_costs(cfg, Path(args.models_dir)),
        models_max=runtime["models_max"],
        cache_type_k=runtime["cache_type_k"],
        cache_type_v=runtime["cache_type_v"],
        # `or None` folds both "key absent" (None from .get()) and a
        # configured `0` into the same "uncapped" sentinel compute_budget()
        # already understands, instead of letting a literal 0 through.
        vram_budget_ceiling_mib=cfg["gpu"].get("vram_budget_ceiling_mib") or None,
    )
```

Run:
```bash
uv run pytest tests/test_cli.py::test_budget_respects_the_configured_vram_ceiling -v
uv run pytest tests/test_cli.py::test_budget_treats_a_configured_zero_vram_ceiling_as_uncapped -v
uv run pytest tests/test_cli.py::test_cmd_budget_passes_configured_models_max -v
```
Expected: all three PASS.

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
# The 6144 fallback here is only a code-level safety net for a config that
# bypassed migrate_config (which backfills the real, 10240 default) — see
# Global Constraints. A valid-but-tiny vram_budget_ceiling_pct must not
# leave llm-server planning against a near-zero VRAM ceiling either way.
ceiling_floor_mib="$(yq -r '.gpu.vram_budget_ceiling_floor_mib // 6144' "$CONFIG_PATH")"
vram_budget_ceiling_mib="$(jq -n --argjson free "$vram_free" --argjson pct "$ceiling_pct" \
    --argjson floor "$ceiling_floor_mib" \
    '[(($free * $pct / 100) | round), $floor] | max')"
```

Then extend the existing yq write block (`setup/setup.sh:117-122` before this task's earlier
steps run; Task 2 Step 3 removes 7 lines earlier in this same file, so by the time this step
executes the block has shifted up to approximately lines 110-115 — the code below is otherwise
unchanged) to also persist it:

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

Add one more test in the same location that pins a non-default
`vram_budget_ceiling_pct` end-to-end. Without it, every ceiling test in this plan uses the
default `95%`, so an implementation that silently hardcodes `95` instead of reading
`gpu.vram_budget_ceiling_pct` from config would still pass everything else:

```python
def test_setup_persists_a_vram_ceiling_using_the_configured_pct_not_the_default(
    tmp_path: pathlib.Path,
) -> None:
    """Regression guard: a hardcoded 95 instead of reading
    gpu.vram_budget_ceiling_pct would still pass every other ceiling test in
    this plan, since they all use the default 95%."""
    config_text = (
        "gpu:\n"
        "  vram_budget_ceiling_pct: 80\n"
        "runtime:\n"
        "  models_max: 0\n"
        "models:\n"
        "  - alias: gemma4\n"
        "    label: Gemma 4\n"
        "    parameters: 12B\n"
        "    quantization: Q4_K_M\n"
        "    size_bytes: 7660000000\n"
        "    enabled: true\n"
        "    file: gemma4.gguf\n"
        "    url: https://example.invalid/gemma4.gguf\n"
        "  - alias: ornith\n"
        "    label: Ornith\n"
        "    parameters: 9B\n"
        "    quantization: Q4_K_M\n"
        "    size_bytes: 5600000000\n"
        "    enabled: false\n"
        "    file: ornith.gguf\n"
        "    url: https://example.invalid/ornith.gguf\n"
    )
    result, _, config = run_setup_with_numbered_selection(
        tmp_path, "1\n1\n2\n", config_text=config_text
    )

    assert result.returncode == 0, result.stderr
    # fixture GPU: vram_total_mib=16384, vram_used_mib=2048, pct 80 (not the default 95)
    # (16384 - 2048) * 80 / 100 = 11468.8 -> round to 11469
    assert yq_value(config, ".gpu.vram_budget_ceiling_mib") == "11469"
```

Run: `uv run pytest tests/test_shell.py::test_setup_persists_a_vram_ceiling_using_the_configured_pct_not_the_default -v`
Expected: PASS (the Step 10 implementation above already reads `gpu.vram_budget_ceiling_pct`
from config via `yq -r '.gpu.vram_budget_ceiling_pct // 95' "$CONFIG_PATH"`, so no further
implementation change is needed here — this step only adds the missing regression coverage).

Add two more tests in the same location covering the floor itself — without them, every ceiling
test above (`13619`, `11469`) lands well above either floor tier, so an implementation that
computes the ceiling correctly but omits the floor entirely would still pass all of them. The
first pins `vram_budget_ceiling_floor_mib` explicitly to prove the *configured* floor is read from
config; the second omits it to prove the code-level `// 6144` fallback still protects a config
that bypassed migration:

```python
def test_setup_floors_the_vram_ceiling_at_the_configured_floor(
    tmp_path: pathlib.Path,
) -> None:
    """A tiny configured pct must not leave llm-server planning against a
    near-zero VRAM ceiling — floored at the configured floor (10 GiB /
    10240 MiB here, matching migrate_config's real default)."""
    config_text = (
        "gpu:\n"
        "  vram_budget_ceiling_pct: 1\n"
        "  vram_budget_ceiling_floor_mib: 10240\n"
        "runtime:\n"
        "  models_max: 0\n"
        "models:\n"
        "  - alias: gemma4\n"
        "    label: Gemma 4\n"
        "    parameters: 12B\n"
        "    quantization: Q4_K_M\n"
        "    size_bytes: 7660000000\n"
        "    enabled: true\n"
        "    file: gemma4.gguf\n"
        "    url: https://example.invalid/gemma4.gguf\n"
        "  - alias: ornith\n"
        "    label: Ornith\n"
        "    parameters: 9B\n"
        "    quantization: Q4_K_M\n"
        "    size_bytes: 5600000000\n"
        "    enabled: false\n"
        "    file: ornith.gguf\n"
        "    url: https://example.invalid/ornith.gguf\n"
    )
    result, _, config = run_setup_with_numbered_selection(
        tmp_path, "1\n1\n2\n", config_text=config_text
    )

    assert result.returncode == 0, result.stderr
    # fixture GPU: vram_total_mib=16384, vram_used_mib=2048, pct 1
    # (16384 - 2048) * 1 / 100 = 143.36 -> round 143, floored up to 10240
    assert yq_value(config, ".gpu.vram_budget_ceiling_mib") == "10240"


def test_setup_floors_the_vram_ceiling_at_the_code_default_when_unconfigured(
    tmp_path: pathlib.Path,
) -> None:
    """Without an explicit vram_budget_ceiling_floor_mib in config (this
    fixture's config_text never sets it, and the uv stub used by
    run_setup_with_numbered_selection doesn't run real migration to
    backfill the 10240 default either), setup.sh's `// 6144` fallback is
    the only thing preventing a near-zero VRAM ceiling."""
    config_text = (
        "gpu:\n"
        "  vram_budget_ceiling_pct: 1\n"
        "runtime:\n"
        "  models_max: 0\n"
        "models:\n"
        "  - alias: gemma4\n"
        "    label: Gemma 4\n"
        "    parameters: 12B\n"
        "    quantization: Q4_K_M\n"
        "    size_bytes: 7660000000\n"
        "    enabled: true\n"
        "    file: gemma4.gguf\n"
        "    url: https://example.invalid/gemma4.gguf\n"
        "  - alias: ornith\n"
        "    label: Ornith\n"
        "    parameters: 9B\n"
        "    quantization: Q4_K_M\n"
        "    size_bytes: 5600000000\n"
        "    enabled: false\n"
        "    file: ornith.gguf\n"
        "    url: https://example.invalid/ornith.gguf\n"
    )
    result, _, config = run_setup_with_numbered_selection(
        tmp_path, "1\n1\n2\n", config_text=config_text
    )

    assert result.returncode == 0, result.stderr
    # fixture GPU: vram_total_mib=16384, vram_used_mib=2048, pct 1
    # (16384 - 2048) * 1 / 100 = 143.36 -> round 143, floored up to the
    # code-level default 6144 (no vram_budget_ceiling_floor_mib in config)
    assert yq_value(config, ".gpu.vram_budget_ceiling_mib") == "6144"
```

Run:
```bash
uv run pytest tests/test_shell.py::test_setup_floors_the_vram_ceiling_at_the_configured_floor -v
uv run pytest tests/test_shell.py::test_setup_floors_the_vram_ceiling_at_the_code_default_when_unconfigured -v
```
Expected: both FAIL before the floor is added to Step 10's `jq` expression above (the persisted
`.gpu.vram_budget_ceiling_mib` would be `"143"`, the unfloored value in both cases), both PASS
after it.

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
  vram_budget_ceiling_floor_mib: 10240
  benchmark:
    vulkan:
      pp_tps: null
      tg_tps: null
      measured_at: null
```

(`vram_budget_ceiling_mib: 0` mirrors `vram_total_mib: 0`'s existing convention — a placeholder until `make setup` computes the real value. `vram_budget_ceiling_floor_mib: 10240` is the real, user-facing floor default — the lower `6144` value only ever shows up as `setup.sh`'s own code-level fallback for a config that bypassed migration.)

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
- **Type consistency:** `compute_resource_limits(host_cpu_count, host_memory_total_mib, memory_ceiling_pct=100, memory_ceiling_floor_mib=6144)` and `compute_budget(..., vram_budget_ceiling_mib=None)` signatures are used identically across every task/step that calls them.
- **Review fixes applied (iteration 1, all 8 findings from `/tmp/review-spec-report-iter1.md`):**
  - CRITICAL #1: Task 4 Step 9's `cmd_budget` implementation now reads
    `cfg["gpu"].get("vram_budget_ceiling_mib", cfg["gpu"].get("vram_total_mib"))` instead of a
    bare index, so `test_cmd_budget_passes_configured_models_max`'s un-migrated raw-dict fixture
    (`tests/test_cli.py:792-820`) no longer raises `KeyError`; the step now explicitly lists that
    test and adds a verification run for it.
  - CRITICAL #2: Task 4 Step 9's new `test_budget_respects_the_configured_vram_ceiling` now
    follows `test_cmd_budget_passes_configured_models_max`'s monkeypatch pattern (`load_config`,
    `detect`, `_model_costs`, `compute_budget`) instead of relying on real hardware/filesystem
    state that doesn't exist on a test host. `tests/test_cli.py` was added to Task 4's Files/Test
    list.
  - HIGH #3: Task 1 Step 1 no longer extends or touches
    `run_render_unit_with_legacy_rocm_config`'s 2-tuple return or its three other call sites —
    the new test derives `presets_path` and `compose_inspect_dir` directly from `tmp_path`,
    matching values the fixture already passes into the subprocess environment.
  - HIGH #4: Task 2 Step 5 now includes an explicit fix to the shared `uv` stub in
    `run_setup_with_numbered_selection` (parses the alias(es) actually passed to `models select`
    via `$@` and enables only those, instead of hardcoding `gemma4`+`ornith`) before the
    single-select assertion updates that depend on it.
  - HIGH #5: Task 3 Step 5 now also fixes
    `test_migrate_config_preserves_existing_resources_values` (`tests/test_config.py:559-562`),
    whose exact-equality assertion breaks once `migrate_config` adds the third
    `memory_ceiling_pct` key.
  - MEDIUM #6: Task 3 Step 9's CLI test now pins `resources.llm_server.memory_ceiling_pct` to a
    known low value and asserts the exact expected `memory_mib`, so an implementation that adds
    `--config` but forgets to thread the pct through `compute_resource_limits()` no longer passes.
  - MEDIUM #7: Task 3 Step 9's CLI test restores the tolerant `if/else` shape (accepting
    `ResourceError`/returncode 1 on hosts below the CPU/memory floors) instead of asserting
    unconditional success.
  - MEDIUM #8: Task 3 Step 7's line reference corrected — `cpus` check at
    pylib/config.py:190-196, `memory_mib` check (the real "after" anchor) at lines 197-202.
- **Review fixes applied (iteration 2, all 5 findings from `/tmp/review-spec-report-iter2.md`):**
  - Scope decision applied verbatim: Global Constraints and the Goal paragraph no longer claim
    the ceilings are anything `llm-server` "can never" exceed. Both now state plainly that the
    ceilings cap what `compute_resource_limits()`/`compute_budget()` *compute* as
    available/permitted, that enforcement of that computed budget at container-start time is
    existing, unchanged, diagnostic-only behavior (`setup.sh` Step 7/8 warns via `log_warn` and
    continues, no `die`, on an infeasible budget), and that this plan does not add enforcement —
    that's pre-existing behavior of the whole budget system, not a regression.
  - CRITICAL #1: Task 2 Step 5's `uv`/`models select` stub no longer uses `yq --argjson` (which
    doesn't exist in the real mikefarah/yq v4 binary — that's `jq` syntax). Since Task 2 restricts
    `setup.sh` to pass exactly one alias to `models select`, the stub now captures the single
    trailing alias argument and compares it with `strenv(SELECTED_ALIAS)`, matching the env-var +
    `strenv()` pattern already used by every other `yq -i` call in this codebase (e.g.
    `setup/setup.sh`'s `PCI_ADDRESS=... yq -i '... strenv(PCI_ADDRESS) ...'` and this plan's own
    Task 4 Step 10).
  - HIGH #2: `compute_resource_limits()` (Task 3 Step 3) now floors `memory_ceiling_mib` at
    `max(1, round(...))` so a valid-but-tiny `memory_ceiling_pct` can never round to `0` MiB and
    silently disable the RAM cap (`pylib/compose.py:72-74` omits `mem_limit` entirely when
    `memory_mib` is falsy). Task 3 Step 1 adds
    `test_memory_ceiling_never_rounds_down_to_a_fully_uncapped_zero` covering `memory_ceiling_pct=0.001`.
  - MEDIUM #3: Task 4 Step 7's `vram_budget_ceiling_mib` validation now explicitly rejects
    `bool` (`isinstance(ceiling_mib, bool) or not (_positive_int(ceiling_mib) or ceiling_mib == 0)`),
    since Python's `False == 0` would otherwise let `vram_budget_ceiling_mib: false` slip through
    the `== 0` branch. `False` was added to Task 4 Step 5's
    `test_validate_config_rejects_invalid_vram_ceiling_mib` parametrize list.
  - MEDIUM #4: Two factual corrections and one citation fix. Global Constraints' RAM-ceiling
    rationale no longer claims `46`% is "the closest whole percent to 14 GiB" (46% of 32768 MiB
    is 15073 MiB ≈ 14.72 GiB, not 14.4 GiB; the prose was corrected, the `46` default kept
    unchanged since it's used consistently elsewhere in the plan/tests). Global Constraints'
    `vram_budget_ceiling_mib` staleness claim no longer says it goes stale only in the
    "conservative" direction — it now says a later process using more VRAM can leave the stored
    ceiling *higher* than what's actually free, in either direction. Task 4 Step 9's citation of
    `test_cmd_budget_passes_configured_models_max` corrected from `tests/test_cli.py:792-820` to
    the actual `792-822`.
  - LOW #5: Task 4 Step 10 adds
    `test_setup_persists_a_vram_ceiling_using_the_configured_pct_not_the_default`, an end-to-end
    `setup.sh` test pinning `gpu.vram_budget_ceiling_pct: 80` — without it, every ceiling test in
    the plan used the default `95%`, so a regression hardcoding `95` would have passed everything.
- **Review fixes applied (iteration 3, from `/tmp/review-spec-report-iter3.md` plus the plan
  author's direct instruction):**
  - User instruction (10 GiB / 10240 MiB floor on both computed ceilings): Global Constraints
    gained two new bullets — one stating the floor applies to `compute_resource_limits()`'s
    `memory_ceiling_mib` and `setup.sh`'s computed `vram_budget_ceiling_mib`, and one documenting
    the `gpu.vram_budget_ceiling_mib: 0` sentinel's contract (see HIGH finding below). Task 3 Step
    3's `compute_resource_limits()` floor changed from `max(1, round(...))` to
    `max(10240, round(...))`. Task 3 Step 1's `test_memory_ceiling_never_rounds_down_to_a_fully_uncapped_zero`
    was renamed to `test_memory_ceiling_floors_at_10_gib_instead_of_rounding_to_zero` and its
    assertion tightened from `>= 1` to the exact `== 10240` (verified: `host_memory_total_mib=32768`,
    `memory_floor_mib=HOST_MEMORY_FLOOR_MIB(4096)+OMNIROUTE_MEMORY_FIXED_MIB(1024)=5120`, so
    `min(32768-5120, max(10240, round(32768*0.001/100)=0)) == min(27648, 10240) == 10240`). Task 4
    Step 10's `setup.sh` `jq` computation changed from `(($free * $pct / 100) | round)` to
    `[(($free * $pct / 100) | round), 10240] | max`, and a new
    `test_setup_floors_the_vram_ceiling_at_10_gib` test was added (fixture free VRAM `14336` MiB,
    `pct=1` → `143.36` rounds to `143`, floored up to `10240`). Verified against the plan's actual
    current fixture numbers, not just the review report's arithmetic: the existing default-pct test
    (`14336 * 95 / 100 = 13619`) and the non-default-pct test from Task 4 Step 10
    (`14336 * 80 / 100 = 11469`) both stay above `10240` and needed no changes, matching what the
    report predicted.
  - HIGH (Codex, new in iteration 3): `gpu.vram_budget_ceiling_mib: 0`'s "no cap" contract is now
    honored by `cmd_budget`. Task 4 Step 9's implementation changed from
    `cfg["gpu"].get("vram_budget_ceiling_mib", cfg["gpu"].get("vram_total_mib"))` (which still
    passed a configured literal `0` straight through) to `cfg["gpu"].get("vram_budget_ceiling_mib") or None`,
    so `compute_budget()`'s `min(vram_total_mib, ...)` sees `None` and applies its own existing
    "no cap" behavior instead of collapsing to a zero/negative budget. A new
    `test_budget_treats_a_configured_zero_vram_ceiling_as_uncapped` test was added, written and
    shown failing before the implementation change, asserting
    `captured["vram_budget_ceiling_mib"] is None` when the config has
    `vram_budget_ceiling_mib: 0`.
  - Non-blocking line-number citations (Claude, iteration 3, fixed opportunistically while already
    touching nearby steps): Task 2 Step 6's "continues past `tests/test_shell.py:713`" corrected to
    `:707` (the real anchor line where the reorder test's config-assertion block ends in the
    current file). Task 3 Step 10 and Task 4 Step 10's `setup/setup.sh` line citations (`142` and
    `117-122`) now note explicitly that they're accurate against the file *before* Task 2 Step 3
    removes 7 lines earlier in the same file, and give the shifted approximate lines (`~135`,
    `~110-115`) an implementer would see if Task 2 has already landed — each step's code block
    still gives exact old-code-text as the unambiguous anchor either way, so this was a
    clarification, not a blocking fix.
- **Manual edit (post-iteration-3, direct author instruction): the 10 GiB floor became a
  two-tier default instead of a bare constant.** The author asked for a code-level default of
  6 GiB alongside a configurable, models.yml-level default of 10 GiB, mirroring the existing
  `memory_ceiling_pct`/`vram_budget_ceiling_pct` two-tier pattern (permissive code default vs.
  opinionated config default):
  - Global Constraints rewritten to describe both tiers and when each applies (config-level
    10240 in normal operation; code-level 6144 only for a config that bypassed
    `migrate_config`, e.g. a hand-crafted config or a test fixture stubbing `migrate-config` as
    a no-op).
  - Task 3: `compute_resource_limits()` gained a fourth parameter,
    `memory_ceiling_floor_mib: int = 6144`, replacing the bare `10240` constant. Step 1's test
    was split into `test_memory_ceiling_floor_defaults_to_6_gib_when_unspecified` (proves the
    code-level default) and `test_memory_ceiling_floors_at_the_configured_value_instead_of_rounding_to_zero`
    (passes `memory_ceiling_floor_mib=10240` explicitly). Step 5 added
    `resources.llm_server.memory_ceiling_floor_mib` migrate/validate tests (default `10240`,
    must be a positive integer) and updated all three exact-equality `make_cfg`-derived
    assertions (Step 5's own note) to include the new key. Step 7 added the corresponding
    `migrate_config`/`validate_config` implementation. Step 9's `cmd_resources` now reads
    `llm_server_resources["memory_ceiling_floor_mib"]` from config and threads it through; its
    CLI test now pins the floor to `1` (so the pct-driven assertion isn't masked by the 10240
    default) and a new `test_resources_respects_the_configured_memory_ceiling_floor` test proves
    the floor itself (not just the pct) is read from config, not hardcoded. Step 11's
    `models.yml.example` block gained `memory_ceiling_floor_mib: 10240`.
  - Task 4: `setup.sh`'s Step 2 computation now reads
    `yq -r '.gpu.vram_budget_ceiling_floor_mib // 6144' "$CONFIG_PATH"` instead of using a bare
    `10240` in the `jq` expression. The single floor test was split into
    `test_setup_floors_the_vram_ceiling_at_the_configured_floor` (config sets
    `vram_budget_ceiling_floor_mib: 10240` explicitly, expects `10240`) and
    `test_setup_floors_the_vram_ceiling_at_the_code_default_when_unconfigured` (config omits the
    field, expects the code-level `6144` — this fixture's `uv` stub doesn't run real migration,
    so the config-level 10240 default never gets backfilled here, making this the correct
    coverage for the `// 6144` fallback path). Step 5's migrate/validate tests, Step 7's
    implementation, and Step 12's `models.yml.example` gpu block all gained the matching
    `vram_budget_ceiling_floor_mib` field (migrate default `10240`, validate as a positive
    integer). Verified against the plan's current fixture numbers that this change is a
    no-op for every *other* ceiling test in the plan: default-pct (`13619`) and non-default-pct
    (`11469`) both stay well above either floor tier.
- **Final verification round (both a Claude reviewer and Codex, independently, on the manually
  edited two-tier-floor version): 2 findings, both confirmed by both reviewers, both fixed:**
  - HIGH: Task 3 Step 9's `test_resources_respects_the_configured_memory_ceiling_floor` no
    longer asserts a bare `== 9000`. `cmd_resources` runs against the real host via a subprocess
    (`resources --config …`), so a hardcoded absolute expectation breaks on any legitimate
    small-but-valid host where `host_total_mib - memory_floor_mib < 9000`. The assertion now
    computes `expected_ceiling_mib = min(host_total_mib - memory_floor_mib, 9000)` from the
    response's own `payload["host"]["memory_total_mib"]`,
    `payload["host_memory_floor_mib"]`, and `payload["omniroute"]["memory_mib"]` — the same
    host-relative pattern the neighboring test in the same step already used.
  - Cross-Document Consistency: the sibling design doc
    (`docs/superpowers/specs/2026-08-09-resource-limits-and-single-model-setup-design.md`) §3
    and §4 were updated to describe the two-tier floor mechanism (`memory_ceiling_floor_mib`/
    `vram_budget_ceiling_floor_mib`, the 10240/6144 defaults, `compute_resource_limits()`'s
    fourth parameter, and the `0`-sentinel-to-`None` translation in `cmd_budget`) — this
    mechanism was added to the plan via direct manual edits after the design doc was written and
    approved, and the design doc had drifted out of sync with what the plan now implements.
