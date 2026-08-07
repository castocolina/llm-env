# Declarative Compose Container Definitions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the bash-heredoc-generated Quadlet `.container` unit with a
Python-rendered `docker-compose.yml` and a thin systemd wrapper `.service`
unit, so `make start`/`stop`/`status`/`logs`/`enable-boot` keep working
exactly as today while the container definition itself becomes versioned,
reviewable Python source instead of a runtime string-built heredoc.

**Architecture:** `pylib/compose.py` builds the compose document as a Python
dict and dumps it with `yaml.safe_dump` — the same "structured data in,
syntactically-valid file out" shape `pylib/presets.py` already uses for
`presets.ini`, so no separate `${VAR}`-substitution template file is needed.
`setup/render-unit.sh` calls a new `llmenv render-compose` subcommand instead
of building the unit with a `cat > … <<EOF` heredoc, then writes a small
systemd wrapper unit (`Type=oneshot`, `ExecStart=podman compose … up -d`)
under the same unit name (`llm-server.service`) the rest of the lifecycle
already targets, so `scripts/start.sh`/`scripts/stop.sh` need no changes at
all.

**Tech Stack:** Python (stdlib `pathlib`/`argparse` + `pyyaml`, already a
project dependency via `llmenv.py`'s PEP 723 metadata), bash (`tools/lib.sh`
conventions), `podman compose` (new host prerequisite, provided by the
`podman-compose` Fedora package).

## Global Constraints

- Python is invoked only as `uv run llmenv.py <subcommand>` (`AGENTS.md`).
- Makefile target bodies longer than 3 lines must delegate to a `.sh` file
  (`AGENTS.md`) — no Makefile targets change in this plan, so this mainly
  bounds how `render-unit.sh` itself stays organized.
- After editing any `.sh` file, run `make validate`. After editing any `.py`
  file, run `make validate && make test` (`AGENTS.md`).
- Never hardcode a value that can be measured (`AGENTS.md`).
- Host-side probes use `127.0.0.1`, never `localhost` (`.agents/architecture.md`).
- Linux only, Bazzite/Fedora, podman — no Docker, no macOS support
  (`.agents/architecture.md`).
- Podman is the only supported compose engine; `podman compose` requires the
  `podman-compose` package as its provider — confirmed on the reference
  machine, where `podman compose version` fails with "looking up compose
  provider failed" until it is installed.
- Deviation from the design spec's literal wording, made deliberately: the
  spec (`docs/superpowers/specs/2026-08-06-podman-compose-omniroute-design.md`)
  describes a checked-in `${VAR}`-substitution template file. This plan
  builds the compose document programmatically in `pylib/compose.py`
  instead, with no separate `.tmpl` file, matching the established
  `pylib/presets.py` pattern in this codebase exactly. This is strictly
  safer (no risk of a value like an API key containing a character that
  breaks hand-substituted YAML) and achieves the same goal the spec cared
  about — a versioned, diffable, reviewable definition — since the Python
  source itself is that versioned definition.
- Scope deviation, deliberate: the design spec's Goal/Scope frames this
  change as covering **both** the llama.cpp router **and** a new OmniRoute
  gateway service — a new `omniroute` compose service, `pylib/omniroute.py`
  plus `llmenv.py omniroute provision`, an `omniroute:` config section,
  fixed OmniRoute resource sizing (1 CPU / 1024 MiB), a `check-server`
  OmniRoute section, and OmniRoute troubleshooting docs. This plan
  implements only the router half — migrating the existing Quadlet unit to
  a rendered compose file plus a thin systemd wrapper unit, and adding
  `llm_server` resource-limit plumbing. **OmniRoute integration is
  deferred to a follow-up plan**; none of Tasks 1–14 below add an
  `omniroute` compose service, module, config section, or check-setup/
  check-server step. One numeric consequence of this deferral:
  `pylib/resources.py`'s `compute_resource_limits()` (Task 2) gives
  `llm_server` the entire post-floor remainder (host total minus
  `HOST_CPU_FLOOR`/`HOST_MEMORY_FLOOR_MIB` only) rather than also
  subtracting OmniRoute's fixed allocation as the design spec's own
  arithmetic does — that subtraction is out of scope here and will need
  revisiting in the OmniRoute follow-up plan.

---

### Task 1: Host CPU/RAM detection

**Files:**
- Modify: `pylib/detect.py`
- Test: `tests/test_detect.py`

**Interfaces:**
- Produces: `host_resources(proc_root: Path = Path("/proc")) -> dict[str, int]`
  returning `{"cpu_count": int, "memory_total_mib": int}`. Raises
  `DetectError` (already defined in `pylib/detect.py`) if `cpuinfo`/`meminfo`
  cannot be read or parsed. Not folded into the existing `detect()` function
  — `detect()`'s current callers and tests (`test_detect_combines_both_sources`
  et al.) must keep working unchanged, and `detect()`'s synthetic test
  fixtures (`build_proc()` in `tests/test_detect.py`) do not create
  `cpuinfo`/`meminfo` files, so adding a hard requirement for them inside
  `detect()` would break those tests.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_detect.py`:

```python
def build_proc_resources(root: Path, cpu_count: int, memory_total_kib: int) -> Path:
    proc = root / "proc"
    proc.mkdir(parents=True, exist_ok=True)
    cpuinfo_lines = []
    for index in range(cpu_count):
        cpuinfo_lines.append(f"processor\t: {index}")
        cpuinfo_lines.append("model name\t: Fixture CPU")
        cpuinfo_lines.append("")
    (proc / "cpuinfo").write_text("\n".join(cpuinfo_lines))
    (proc / "meminfo").write_text(
        f"MemTotal:       {memory_total_kib} kB\n"
        "MemFree:         1000000 kB\n"
    )
    return proc


def test_host_resources_reads_cpu_count_and_memory(tmp_path):
    proc = build_proc_resources(tmp_path, cpu_count=8, memory_total_kib=32 * 1024 * 1024)
    result = host_resources(proc)
    assert result == {"cpu_count": 8, "memory_total_mib": 32 * 1024}


def test_host_resources_missing_cpuinfo_raises(tmp_path):
    proc = tmp_path / "proc"
    proc.mkdir()
    (proc / "meminfo").write_text("MemTotal:       1000 kB\n")
    with pytest.raises(DetectError):
        host_resources(proc)


def test_host_resources_missing_meminfo_raises(tmp_path):
    proc = build_proc_resources(tmp_path, cpu_count=4, memory_total_kib=1)
    (proc / "meminfo").unlink()
    with pytest.raises(DetectError):
        host_resources(proc)


def test_host_resources_corrupt_meminfo_raises(tmp_path):
    proc = build_proc_resources(tmp_path, cpu_count=4, memory_total_kib=1)
    (proc / "meminfo").write_text("MemTotal:       not-a-number kB\n")
    with pytest.raises(DetectError):
        host_resources(proc)


def test_host_resources_no_processor_entries_raises(tmp_path):
    proc = tmp_path / "proc"
    proc.mkdir()
    (proc / "cpuinfo").write_text("")
    (proc / "meminfo").write_text("MemTotal:       1000 kB\n")
    with pytest.raises(DetectError):
        host_resources(proc)
```

Add `host_resources` to the existing import line:
`from pylib.detect import DetectError, compositor_render_node, detect, host_resources, list_gpus`

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --with pytest pytest tests/test_detect.py -v -k host_resources`
Expected: FAIL with `ImportError` / `NameError: name 'host_resources' is not defined`.

- [ ] **Step 3: Implement `host_resources`**

Append to `pylib/detect.py`:

```python
def host_resources(proc_root: Path = Path("/proc")) -> dict[str, int]:
    """Return the host's total CPU count and total RAM, read from procfs directly."""
    proc_root = Path(proc_root)
    cpuinfo_path = proc_root / "cpuinfo"
    try:
        cpuinfo = cpuinfo_path.read_text()
    except OSError as exc:
        raise DetectError(f"cannot read {cpuinfo_path}: {exc}") from exc
    cpu_count = sum(1 for line in cpuinfo.splitlines() if line.startswith("processor"))
    if cpu_count == 0:
        raise DetectError(f"no processor entries found in {cpuinfo_path}")

    meminfo_path = proc_root / "meminfo"
    try:
        meminfo = meminfo_path.read_text()
    except OSError as exc:
        raise DetectError(f"cannot read {meminfo_path}: {exc}") from exc
    memory_total_kib: int | None = None
    for line in meminfo.splitlines():
        if line.startswith("MemTotal:"):
            parts = line.split()
            if len(parts) < 2 or not parts[1].isdigit():
                raise DetectError(f"MemTotal line is malformed in {meminfo_path}: {line!r}")
            memory_total_kib = int(parts[1])
            break
    if memory_total_kib is None:
        raise DetectError(f"MemTotal not found in {meminfo_path}")

    return {"cpu_count": cpu_count, "memory_total_mib": memory_total_kib // 1024}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --with pytest pytest tests/test_detect.py -v`
Expected: PASS, all tests including the pre-existing ones.

- [ ] **Step 5: Commit**

```bash
git add pylib/detect.py tests/test_detect.py
git commit -m "feat(detect): add host_resources for CPU/RAM detection"
```

---

### Task 2: Resource limit arithmetic

**Files:**
- Create: `pylib/resources.py`
- Test: `tests/test_resources.py`

**Interfaces:**
- Consumes: nothing (pure arithmetic, mirrors `pylib/budget.py`'s shape).
- Produces:
  `HOST_CPU_FLOOR: int`, `HOST_MEMORY_FLOOR_MIB: int` module constants;
  `class ResourceError(Exception)`;
  `compute_resource_limits(host_cpu_count: int, host_memory_total_mib: int) -> dict[str, Any]`
  returning
  `{"host_cpu_floor": int, "host_memory_floor_mib": int, "llm_server": {"cpus": int, "memory_mib": int}}`.
  Raises `ResourceError` if the host has too few resources to reserve the
  floor and still leave something for `llm_server`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_resources.py`:

```python
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pylib.resources import HOST_CPU_FLOOR, HOST_MEMORY_FLOOR_MIB, ResourceError, compute_resource_limits


def test_llm_server_gets_remainder_after_host_floor():
    result = compute_resource_limits(host_cpu_count=8, host_memory_total_mib=32768)
    assert result["host_cpu_floor"] == HOST_CPU_FLOOR
    assert result["host_memory_floor_mib"] == HOST_MEMORY_FLOOR_MIB
    assert result["llm_server"]["cpus"] == 8 - HOST_CPU_FLOOR
    assert result["llm_server"]["memory_mib"] == 32768 - HOST_MEMORY_FLOOR_MIB


def test_insufficient_cpu_raises():
    with pytest.raises(ResourceError):
        compute_resource_limits(host_cpu_count=HOST_CPU_FLOOR, host_memory_total_mib=32768)


def test_insufficient_memory_raises():
    with pytest.raises(ResourceError):
        compute_resource_limits(host_cpu_count=8, host_memory_total_mib=HOST_MEMORY_FLOOR_MIB)


def test_exact_floor_plus_one_is_feasible():
    result = compute_resource_limits(
        host_cpu_count=HOST_CPU_FLOOR + 1,
        host_memory_total_mib=HOST_MEMORY_FLOOR_MIB + 1,
    )
    assert result["llm_server"]["cpus"] == 1
    assert result["llm_server"]["memory_mib"] == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --with pytest pytest tests/test_resources.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pylib.resources'`.

- [ ] **Step 3: Implement `pylib/resources.py`**

```python
"""Host CPU/RAM budgeting for the compose container stack.

Mirrors pylib/budget.py's shape for VRAM: a fixed reserved floor for the
host, then whatever is left goes to llm-server. cpus is a whole CPU-core
count usable directly as compose's `cpus:` service key; memory_mib is a
whole-MiB integer usable as `mem_limit: <n>m`.
"""

from __future__ import annotations

from typing import Any

# Fixed floor reserved for the host OS and other applications.
HOST_CPU_FLOOR = 2
HOST_MEMORY_FLOOR_MIB = 4096


class ResourceError(Exception):
    """Raised when the host has too few resources to reserve the fixed floor."""


def compute_resource_limits(
    host_cpu_count: int, host_memory_total_mib: int
) -> dict[str, Any]:
    if host_cpu_count <= HOST_CPU_FLOOR:
        raise ResourceError(
            f"host has {host_cpu_count} CPUs; more than {HOST_CPU_FLOOR} are "
            "required to reserve the host floor and still run llm-server"
        )
    if host_memory_total_mib <= HOST_MEMORY_FLOOR_MIB:
        raise ResourceError(
            f"host has {host_memory_total_mib} MiB RAM; more than "
            f"{HOST_MEMORY_FLOOR_MIB} MiB is required to reserve the host "
            "floor and still run llm-server"
        )
    return {
        "host_cpu_floor": HOST_CPU_FLOOR,
        "host_memory_floor_mib": HOST_MEMORY_FLOOR_MIB,
        "llm_server": {
            "cpus": host_cpu_count - HOST_CPU_FLOOR,
            "memory_mib": host_memory_total_mib - HOST_MEMORY_FLOOR_MIB,
        },
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --with pytest pytest tests/test_resources.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pylib/resources.py tests/test_resources.py
git commit -m "feat(resources): add host CPU/RAM limit arithmetic"
```

---

### Task 3: `llmenv.py resources` subcommand

**Files:**
- Modify: `llmenv.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `host_resources()` (Task 1), `compute_resource_limits()` (Task 2).
- Produces: `llmenv resources` CLI subcommand emitting
  `{"host": {...}, "host_cpu_floor": int, "host_memory_floor_mib": int, "llm_server": {"cpus": int, "memory_mib": int}}`
  on success (exit 0), or `{"error": str}` (exit 1) if the host has too few
  resources.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cli.py`:

```python
def test_resources_emits_host_and_llm_server_limits():
    result = run("resources")
    payload = json.loads(result.stdout)
    if result.returncode == 0:
        assert payload["host"]["cpu_count"] > 0
        assert payload["llm_server"]["cpus"] >= 0
        assert payload["llm_server"]["memory_mib"] >= 0
    else:
        assert result.returncode == 1
        assert "error" in payload
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --with pytest pytest tests/test_cli.py -v -k test_resources_emits`
Expected: FAIL with `argparse` error (`invalid choice: 'resources'`), nonzero
exit not equal to 0 or 1 (argparse usage errors exit 2).

- [ ] **Step 3: Wire the subcommand**

In `llmenv.py`, update the import block:

```python
from pylib.detect import DetectError, detect, host_resources
```

and

```python
from pylib.resources import ResourceError, compute_resource_limits
```

Add the command function, near `cmd_budget`:

```python
def cmd_resources(args: argparse.Namespace) -> int:
    host = host_resources()
    limits = compute_resource_limits(host["cpu_count"], host["memory_total_mib"])
    return emit({"host": host, **limits})
```

In `build_parser()`, alongside the other simple subparsers:

```python
sub.add_parser("resources").set_defaults(func=cmd_resources)
```

In `main()`, extend the caught-exception tuple:

```python
except (ConfigError, BudgetError, GgufError, DetectError, ResourceError) as exc:
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --with pytest pytest tests/test_cli.py -v -k test_resources_emits`
Expected: PASS.

- [ ] **Step 5: Run the full test suite**

Run: `uv run --with pytest pytest tests/ -v`
Expected: PASS (no regressions in `test_detect.py`/`test_cli.py`/others).

- [ ] **Step 6: Commit**

```bash
git add llmenv.py tests/test_cli.py
git commit -m "feat(cli): add llmenv resources subcommand"
```

---

### Task 4: Config schema — `resources.llm_server`

**Files:**
- Modify: `pylib/config.py`
- Modify: `models.yml.example`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `migrate_config()` additively sets
  `cfg["resources"]["llm_server"] = {"cpus": 0, "memory_mib": 0}` when
  absent (0 means "not yet computed by `make setup`"). `validate_config()`
  validates `resources.llm_server.cpus` (non-negative number) and
  `resources.llm_server.memory_mib` (zero or a positive integer) **only
  when the `resources` key is present** — it stays optional so existing
  fixtures/tests that build a config dict without it (e.g.
  `tests/test_config.py::make_cfg`) are unaffected.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config.py`:

```python
def test_migrate_config_adds_default_resources_section():
    cfg = make_cfg()
    migrated = config_module.migrate_config(copy.deepcopy(cfg))
    assert migrated["resources"]["llm_server"] == {"cpus": 0, "memory_mib": 0}


def test_migrate_config_preserves_existing_resources_values():
    cfg = make_cfg(resources={"llm_server": {"cpus": 6, "memory_mib": 28672}})
    migrated = config_module.migrate_config(copy.deepcopy(cfg))
    assert migrated["resources"]["llm_server"] == {"cpus": 6, "memory_mib": 28672}


def test_migrate_config_adds_default_resources_section_when_gpu_absent():
    """The resources default must not be skipped by the gpu early-return branch."""
    cfg = make_cfg()
    del cfg["gpu"]
    migrated = config_module.migrate_config(copy.deepcopy(cfg))
    assert migrated["resources"]["llm_server"] == {"cpus": 0, "memory_mib": 0}


def test_config_without_resources_key_has_no_errors():
    assert validate_config(make_cfg()) == []


def test_config_accepts_valid_resources_section():
    cfg = make_cfg(resources={"llm_server": {"cpus": 6, "memory_mib": 28672}})
    assert validate_config(cfg) == []


def test_config_accepts_zero_sentinel_resources():
    cfg = make_cfg(resources={"llm_server": {"cpus": 0, "memory_mib": 0}})
    assert validate_config(cfg) == []


@pytest.mark.parametrize(
    "llm_server",
    [
        {"cpus": -1, "memory_mib": 0},
        {"cpus": "six", "memory_mib": 0},
        {"cpus": True, "memory_mib": 0},
        {"cpus": 0, "memory_mib": -1},
        {"cpus": 0, "memory_mib": "lots"},
    ],
)
def test_config_rejects_invalid_resources_values(llm_server):
    cfg = make_cfg(resources={"llm_server": llm_server})
    errors = validate_config(cfg)
    assert any("resources.llm_server" in error for error in errors)


def test_config_rejects_non_mapping_resources_section():
    cfg = make_cfg(resources=[])
    errors = validate_config(cfg)
    assert any(error == "section resources must be a mapping" for error in errors)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --with pytest pytest tests/test_config.py -v -k resources`
Expected: FAIL — `migrate_config` does not add the section; `validate_config`
does not reject the invalid-values cases (no matching error string).

- [ ] **Step 3: Implement the schema changes**

In `pylib/config.py`, `migrate_config()` currently has an early return partway
through — `gpu = cfg.get("gpu")` followed by `if not isinstance(gpu, dict):
return cfg` (lines 68–70) — before its final `return cfg` (line 74). Insert
the new resources-defaulting block **before that early-return check**, not
after the `gpu` block, so it runs unconditionally even when `gpu` is
absent/malformed:

```python
    models = cfg.get("models")
    if isinstance(models, list):
        for model in models:
            if not isinstance(model, dict) or "client_max_output_tokens" in model:
                continue
            ctx_size = model.get("ctx_size")
            if _positive_int(ctx_size):
                model["client_max_output_tokens"] = min(ctx_size, 8192)

    resources = cfg.setdefault("resources", {})
    if isinstance(resources, dict):
        llm_server_resources = resources.setdefault("llm_server", {})
        if isinstance(llm_server_resources, dict):
            llm_server_resources.setdefault("cpus", 0)
            llm_server_resources.setdefault("memory_mib", 0)

    gpu = cfg.get("gpu")
    if not isinstance(gpu, dict):
        return cfg
```

That is, the new block goes immediately after the existing `models` loop and
immediately before the existing `gpu = cfg.get("gpu")` line — the `gpu`
block and final `return cfg` below it are otherwise unchanged.

In `validate_config()`, after the `runtime` validation block and before the
`models = cfg["models"]` line, add:

```python
    if "resources" in cfg:
        resources = cfg["resources"]
        if not isinstance(resources, dict):
            errors.append("section resources must be a mapping")
        else:
            llm_server_resources = resources.get("llm_server", {})
            if not isinstance(llm_server_resources, dict):
                errors.append("resources.llm_server must be a mapping")
            else:
                cpus = llm_server_resources.get("cpus", 0)
                if isinstance(cpus, bool) or not (
                    isinstance(cpus, (int, float)) and cpus >= 0
                ):
                    errors.append(
                        "resources.llm_server.cpus must be a non-negative number"
                    )
                memory_mib = llm_server_resources.get("memory_mib", 0)
                if not (memory_mib == 0 or _positive_int(memory_mib)):
                    errors.append(
                        "resources.llm_server.memory_mib must be zero or a "
                        "positive integer"
                    )
```

In `models.yml.example`, add a new section after `runtime:` and before
`models:`:

```yaml
resources:
  llm_server:
    cpus: 0
    memory_mib: 0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --with pytest pytest tests/test_config.py -v`
Expected: PASS, including all pre-existing tests.

- [ ] **Step 5: Run the full test suite**

Run: `uv run --with pytest pytest tests/ -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add pylib/config.py models.yml.example tests/test_config.py
git commit -m "feat(config): add resources.llm_server schema with migration"
```

---

### Task 5: `pylib/compose.py` renderer

**Files:**
- Create: `pylib/compose.py`
- Test: `tests/test_compose.py`

**Interfaces:**
- Consumes: a config dict shaped like `pylib/config.py`'s schema (`server`,
  `gpu`, `runtime`, `resources.llm_server`).
- Produces:
  `render_compose(cfg: dict[str, Any], *, models_dir: str, presets_path: str) -> str`
  and
  `write_compose(cfg: dict[str, Any], *, models_dir: str, presets_path: str, path: Path) -> None`.
  Mirrors `pylib/presets.py`'s `render_presets`/`write_presets` shape
  exactly, including a "generated, do not edit" header comment.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_compose.py`:

```python
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pylib.compose import render_compose, write_compose

CFG = {
    "version": 1,
    "server": {
        "host": "0.0.0.0",
        "port": 8000,
        "api_key": "test-api-key",
        "sleep_idle_seconds": 300,
    },
    "gpu": {
        "image": "ghcr.io/ggml-org/llama.cpp:server-vulkan",
    },
    "runtime": {
        "models_max": 1,
    },
    "resources": {
        "llm_server": {"cpus": 0, "memory_mib": 0},
    },
}


def compose_dict(cfg=CFG):
    text = render_compose(cfg, models_dir="/home/user/llm-workspace/models", presets_path="/home/user/.config/llm-env/presets.ini")
    return text, yaml.safe_load(text)


def test_output_starts_with_a_comment_header():
    text, _ = compose_dict()
    assert text.startswith("# Generated by llm-env")


def test_service_uses_configured_image():
    _, document = compose_dict()
    assert document["services"]["llm-server"]["image"] == "ghcr.io/ggml-org/llama.cpp:server-vulkan"


def test_service_publishes_configured_port():
    _, document = compose_dict()
    assert document["services"]["llm-server"]["ports"] == ["8000:8000"]


def test_service_mounts_models_and_presets_read_only_with_selinux_label():
    _, document = compose_dict()
    volumes = document["services"]["llm-server"]["volumes"]
    assert "/home/user/llm-workspace/models:/models:ro,z" in volumes
    assert "/home/user/.config/llm-env/presets.ini:/etc/llama/presets.ini:ro,z" in volumes


def test_service_passes_the_dri_device():
    _, document = compose_dict()
    assert document["services"]["llm-server"]["devices"] == ["/dev/dri:/dev/dri"]


def test_service_sets_router_environment():
    _, document = compose_dict()
    env = document["services"]["llm-server"]["environment"]
    assert env["LLAMA_ARG_MODELS_PRESET"] == "/etc/llama/presets.ini"
    assert env["LLAMA_ARG_MODELS_MAX"] == 1
    assert env["LLAMA_ARG_HOST"] == "0.0.0.0"
    assert env["LLAMA_ARG_PORT"] == 8000
    assert env["LLAMA_API_KEY"] == "test-api-key"


def test_service_healthcheck_probes_127_0_0_1():
    _, document = compose_dict()
    healthcheck = document["services"]["llm-server"]["healthcheck"]
    assert healthcheck["test"] == [
        "CMD-SHELL",
        "curl -fsS http://127.0.0.1:8000/health || exit 1",
    ]


def test_service_restarts_on_failure():
    _, document = compose_dict()
    assert document["services"]["llm-server"]["restart"] == "on-failure"


def test_zero_resource_limits_are_omitted():
    _, document = compose_dict()
    service = document["services"]["llm-server"]
    assert "cpus" not in service
    assert "mem_limit" not in service


def test_nonzero_resource_limits_are_applied():
    cfg = {**CFG, "resources": {"llm_server": {"cpus": 6, "memory_mib": 28672}}}
    _, document = compose_dict(cfg)
    service = document["services"]["llm-server"]
    assert service["cpus"] == 6
    assert service["mem_limit"] == "28672m"


def test_sleep_idle_seconds_becomes_a_command_flag():
    _, document = compose_dict()
    assert document["services"]["llm-server"]["command"] == ["--sleep-idle-seconds", "300"]


def test_write_compose_creates_parent_directories(tmp_path):
    target = tmp_path / "nested" / "docker-compose.yml"
    write_compose(CFG, models_dir="/models", presets_path="/presets.ini", path=target)
    assert target.exists()
    assert "llm-server" in yaml.safe_load(target.read_text())["services"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --with pytest pytest tests/test_compose.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pylib.compose'`.

- [ ] **Step 3: Implement `pylib/compose.py`**

```python
"""Render docker-compose.yml from models.yml.

Builds the compose document as a Python dict and dumps it with
yaml.safe_dump, so the output is always syntactically valid — the same
"structured data in, generated file out" shape pylib/presets.py uses for
presets.ini via configparser.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

HEADER_COMMENT = "# Generated by llm-env. Do not edit; regenerated on every start.\n"


def render_compose(cfg: dict[str, Any], *, models_dir: str, presets_path: str) -> str:
    server = cfg["server"]
    gpu = cfg["gpu"]
    runtime = cfg["runtime"]
    llm_server_resources = cfg.get("resources", {}).get("llm_server", {})

    service: dict[str, Any] = {
        "image": gpu["image"],
        "container_name": "llm-server",
        # /dev/dri is always passed through; the specific GPU device is
        # selected inside presets.ini, not at the container level.
        "devices": ["/dev/dri:/dev/dri"],
        "volumes": [
            # `,z` relabels the bind mount for SELinux — required on Fedora
            # Atomic hosts, understood by the podman compose provider.
            f"{models_dir}:/models:ro,z",
            f"{presets_path}:/etc/llama/presets.ini:ro,z",
        ],
        "ports": [f"{server['port']}:{server['port']}"],
        "environment": {
            "LLAMA_ARG_MODELS_PRESET": "/etc/llama/presets.ini",
            "LLAMA_ARG_MODELS_MAX": runtime["models_max"],
            "LLAMA_ARG_HOST": server["host"],
            "LLAMA_ARG_PORT": server["port"],
            "LLAMA_API_KEY": server["api_key"],
        },
        "command": ["--sleep-idle-seconds", str(server["sleep_idle_seconds"])],
        "healthcheck": {
            "test": [
                "CMD-SHELL",
                f"curl -fsS http://127.0.0.1:{server['port']}/health || exit 1",
            ],
            "interval": "10s",
            "retries": 30,
            "start_period": "20s",
        },
        "restart": "on-failure",
    }

    cpus = llm_server_resources.get("cpus")
    if cpus:
        service["cpus"] = cpus
    memory_mib = llm_server_resources.get("memory_mib")
    if memory_mib:
        service["mem_limit"] = f"{memory_mib}m"

    document = {"services": {"llm-server": service}}
    return HEADER_COMMENT + yaml.safe_dump(
        document, sort_keys=False, default_flow_style=False
    )


def write_compose(
    cfg: dict[str, Any], *, models_dir: str, presets_path: str, path: Path
) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        render_compose(cfg, models_dir=models_dir, presets_path=presets_path),
        encoding="utf-8",
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --with pytest pytest tests/test_compose.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pylib/compose.py tests/test_compose.py
git commit -m "feat(compose): render docker-compose.yml from models.yml"
```

---

### Task 6: `llmenv.py render-compose` subcommand

**Files:**
- Modify: `llmenv.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `write_compose()` (Task 5).
- Produces: `llmenv render-compose --models-dir <path> --presets-path <path> --output <path>`
  CLI subcommand, emitting `{"written": "<output path>"}`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cli.py`:

```python
def test_render_compose_writes_a_compose_file(tmp_path):
    config = write_test_config(tmp_path)
    output = tmp_path / "docker-compose.yml"
    result = run(
        "--config", str(config),
        "render-compose",
        "--models-dir", "/models",
        "--presets-path", "/presets.ini",
        "--output", str(output),
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["written"] == str(output)
    assert output.exists()
    assert "llm-server" in output.read_text()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --with pytest pytest tests/test_cli.py -v -k test_render_compose`
Expected: FAIL — `argparse` rejects the unknown `render-compose` subcommand.

- [ ] **Step 3: Wire the subcommand**

In `llmenv.py`, add to the import block:

```python
from pylib.compose import write_compose
```

Add the command function near `cmd_presets`:

```python
def cmd_render_compose(args: argparse.Namespace) -> int:
    cfg = require_valid_config(load_config(Path(args.config)))
    write_compose(
        cfg,
        models_dir=args.models_dir,
        presets_path=args.presets_path,
        path=Path(args.output),
    )
    return emit({"written": str(args.output)})
```

In `build_parser()`, alongside `presets`:

```python
    render_compose = sub.add_parser("render-compose")
    render_compose.add_argument("--config", default=argparse.SUPPRESS)
    render_compose.add_argument("--models-dir", required=True)
    render_compose.add_argument("--presets-path", required=True)
    render_compose.add_argument("--output", required=True)
    render_compose.set_defaults(func=cmd_render_compose)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --with pytest pytest tests/test_cli.py -v -k test_render_compose`
Expected: PASS.

- [ ] **Step 5: Run the full test suite**

Run: `uv run --with pytest pytest tests/ -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add llmenv.py tests/test_cli.py
git commit -m "feat(cli): add llmenv render-compose subcommand"
```

---

### Task 7: `tools/lib.sh` — compose file and wrapper unit paths

**Files:**
- Modify: `tools/lib.sh`

**Interfaces:**
- Produces: `COMPOSE_FILE` (`${HOME}/.config/llm-env/docker-compose.yml`)
  and `WRAPPER_UNIT_PATH` (`${HOME}/.config/systemd/user/${UNIT_NAME}.service`)
  exported globals, replacing `QUADLET_DIR`. Any other file in this repo
  referencing `QUADLET_DIR` must be updated in the same commit — grep for
  it first.

- [ ] **Step 1: Find every reference to `QUADLET_DIR`**

Run: `grep -rn "QUADLET_DIR" --include='*.sh' --include='*.py' .`
Expected output: 4 files — `tools/lib.sh` (the definition and export line),
`setup/render-unit.sh` (consumed in Task 9), `setup/disable-boot.sh:14`
(fixed in Task 11), and `scripts/clean.sh` (lines 20 and 33, fixed in
Task 10). Task 9, Task 10, and Task 11 are the only ones expected to need
changes in this plan for these references.

- [ ] **Step 2: Replace the constant**

In `tools/lib.sh`, replace:

```bash
QUADLET_DIR="${HOME}/.config/containers/systemd"
```

with:

```bash
COMPOSE_FILE="${HOME}/.config/llm-env/docker-compose.yml"
WRAPPER_UNIT_PATH="${HOME}/.config/systemd/user/${UNIT_NAME}.service"
```

Update the export line immediately below. The current line (added by a
sibling CLI/Script Hygiene plan, already merged) is:

```bash
export REPO_DIR CONFIG_PATH MODELS_DIR UNIT_NAME QUADLET_DIR VULKAN_IMAGE CPU_IMAGE LLM_ENV_HEALTH_TIMEOUT_SECONDS
```

Replace it with (swapping `QUADLET_DIR` for the two new names and keeping
`VULKAN_IMAGE`/`CPU_IMAGE`/`LLM_ENV_HEALTH_TIMEOUT_SECONDS` exported exactly
as before — they are consumed by `setup/setup.sh`, `scripts/benchmark.sh`,
`scripts/clean.sh`, `tools/lib.sh`'s own `wait_for_health()`, and
`render-unit.sh`'s mDNS heredoc; dropping them from `export` would make
`shellcheck -s bash tools/lib.sh` flag them as unused, SC2034):

```bash
export REPO_DIR CONFIG_PATH MODELS_DIR UNIT_NAME COMPOSE_FILE WRAPPER_UNIT_PATH VULKAN_IMAGE CPU_IMAGE LLM_ENV_HEALTH_TIMEOUT_SECONDS
```

- [ ] **Step 3: Run shellcheck**

Run: `shellcheck -s bash tools/lib.sh`
Expected: no new warnings. `tools/lib.sh` will not fully validate stand-alone
until Tasks 9–11 remove the remaining `QUADLET_DIR` references in the files
that source it — that is expected at this point in the plan; `make validate`
(which lints every `.sh` file together) is deferred to the end of Task 11.

- [ ] **Step 4: Commit**

```bash
git add tools/lib.sh
git commit -m "refactor(lib): replace QUADLET_DIR with compose file/unit paths"
```

---

### Task 8: `setup/prerequisites.sh` — add the `podman compose` provider

**Files:**
- Modify: `setup/prerequisites.sh`
- Test: `tests/test_shell.py`

**Interfaces:**
- Produces: `podman-compose` added to the `RUNTIME` group, verified via
  `podman compose version` actually succeeding (not just a binary existing
  on `PATH`) — mirroring the existing `yq` v4 special case in
  `command_is_usable()`, since "a similarly named incompatible thing does
  not satisfy the prerequisite" applies here too: a `podman` binary alone is
  not sufficient, the compose provider must actually resolve.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_shell.py` (near the other prerequisites tests; uses
the same `commands`/`PATH` stubbing style already established in this
file):

```python
def test_prerequisites_reports_missing_podman_compose_provider(tmp_path):
    commands = tmp_path / "bin"
    commands.mkdir()
    _mock_dirname(commands)
    for name in ("uv", "jq", "yq", "curl", "ip", "sudo"):
        _mock_command(commands, name)
    podman = commands / "podman"
    podman.write_text("#!/usr/bin/bash\nexit 1\n")  # "compose" subcommand fails: no provider
    podman.chmod(podman.stat().st_mode | stat.S_IXUSR)

    environment = os.environ | {"PATH": str(commands)}
    result = subprocess.run(
        ["/usr/bin/bash", "setup/prerequisites.sh", "--check"],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 1
    assert "podman-compose" in result.stdout


def test_prerequisites_accepts_a_working_podman_compose_provider(tmp_path):
    commands = tmp_path / "bin"
    commands.mkdir()
    _mock_dirname(commands)
    for name in ("uv", "jq", "yq", "curl", "ip", "sudo"):
        _mock_command(commands, name)
    podman = commands / "podman"
    podman.write_text(
        "#!/usr/bin/bash\n"
        "case \"$*\" in\n"
        "  'compose version') exit 0 ;;\n"
        "  *) exit 0 ;;\n"
        "esac\n"
    )
    podman.chmod(podman.stat().st_mode | stat.S_IXUSR)

    environment = os.environ | {"PATH": str(commands)}
    result = subprocess.run(
        ["/usr/bin/bash", "setup/prerequisites.sh", "--check"],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "installed  podman-compose" in result.stdout
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --with pytest pytest tests/test_shell.py -v -k podman_compose`
Expected: FAIL — `podman-compose` does not yet appear in `prerequisites.sh`'s
output at all (neither "missing" nor "installed" line), so both assertions
fail.

- [ ] **Step 3: Update `setup/prerequisites.sh`**

Add `podman-compose` to the `RUNTIME` array. The current array (line 8) is:

```bash
RUNTIME=("jq:jq" "yq:yq" "podman:podman" "curl:curl" "ip:iproute")
```

Note it does **not** contain `uv:uv` — `uv` was deliberately pulled out of
`RUNTIME`/`check_group` by an already-merged sibling fix and now has its own
bespoke check-and-bootstrap block above `check_group` (the `uv_missing`
variable, the `command -v uv` check at lines 79–84, and the dedicated
official-installer prompt at lines 113–122). Do **not** add `uv:uv` back —
doing so would revive a dead `command_purpose "uv")` case, print `uv`'s
status twice (once from the dedicated block, once from `check_group`), and
break the pre-existing tests `test_prerequisites_reports_missing_uv_without_rpm_ostree`
and `test_prerequisites_installs_uv_via_official_installer_not_rpm_ostree`.
Replace the array with:

```bash
RUNTIME=("jq:jq" "yq:yq" "podman:podman" "podman-compose:podman-compose" "curl:curl" "ip:iproute")
```

Extend `command_is_usable()`:

```bash
command_is_usable() {
    local command="$1"
    if [ "$command" = "podman-compose" ]; then
        podman compose version >/dev/null 2>&1 || return 1
        return 0
    fi
    command -v "$command" >/dev/null 2>&1 || return 1
    if [ "$command" = "yq" ]; then
        local version
        version="$(yq --version 2>/dev/null)"
        [[ "$version" == *"github.com/mikefarah/yq/"* && "$version" == *"version v4."* ]] || return 1
    fi
}
```

`podman-compose` names a *capability* ("`podman compose` resolves and
works"), not a literal binary on `PATH` — unlike `yq`, there is no
`podman-compose` executable this repo expects to exist. The generic
`command -v "$command"` gate would look for a binary literally named
`podman-compose`, which is neither installed nor required (the `podman compose`
*subcommand* is what's being verified, provided by `podman` itself, whose own
presence is already covered by the separate `podman:podman` entry in
`RUNTIME`). So this case skips the `command -v` gate entirely and goes
straight to the functional check.

Extend `command_purpose()`:

```bash
        podman-compose) printf '%s\n' "compose provider for 'podman compose'" ;;
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --with pytest pytest tests/test_shell.py -v -k podman_compose`
Expected: PASS.

- [ ] **Step 5: Run shellcheck**

Run: `shellcheck -s bash setup/prerequisites.sh`
Expected: no new warnings.

- [ ] **Step 6: Commit**

```bash
git add setup/prerequisites.sh tests/test_shell.py
git commit -m "feat(prerequisites): require a working podman compose provider"
```

---

### Task 9: `setup/render-unit.sh` — compose file + systemd wrapper unit

**Files:**
- Modify: `setup/render-unit.sh`
- Modify: `tests/test_shell.py` (update two existing tests that assert on
  the now-removed Quadlet `.container` file; add new tests for the compose
  file and wrapper unit)

**Interfaces:**
- Consumes: `llmenv render-compose` (Task 6), `COMPOSE_FILE`/`WRAPPER_UNIT_PATH`
  (Task 7).
- Produces: `${COMPOSE_FILE}` and `${WRAPPER_UNIT_PATH}` on disk;
  `systemctl --user daemon-reload` afterward — the same effective contract
  the current Quadlet-based `render-unit.sh` has (renders, does not start),
  consumed unchanged by `scripts/start.sh`, `setup/enable-boot.sh`, and
  `setup/disable-boot.sh`.

- [ ] **Step 1: Update the two existing tests that read the Quadlet file**

In `tests/test_shell.py`, `run_render_unit_with_legacy_rocm_config()`
currently returns
`home / ".config/containers/systemd/llm-server.container"`. Change the
return to the wrapper unit path instead:

```python
    return result, home / ".config/systemd/user/llm-server.service"
```

That same helper's config fixture (currently `tests/test_shell.py:830-843`)
is missing `version: 1` and a `models:` section. Once `uv` is wired to
forward to the real binary (below), `render-unit.sh`'s
`migrate_config_file || die ...` (`render-unit.sh:11`) runs `llmenv
migrate-config` for real, which calls `require_valid_config(migrate_config(...))`
(`llmenv.py:268-273`) — `validate_config()` (`pylib/config.py:129-134`)
requires `cfg.get("version") == 1` and a non-empty `"models"` list, neither
of which this fixture has today, so the script would `die` before ever
reaching the presets/compose-rendering steps this task tests.

Adding `version`/`models` alone is not sufficient, though: this fixture's
`gpu.backend: rocm` also fails real validation, independently of
`version`/`models` — `VALID_BACKENDS` (`pylib/config.py:31`) is
`("vulkan", "cpu")` only, and always has been; `rocm` is not a member.
`migrate_config()` never rewrites `gpu.backend` (it only pops the obsolete
`gpu.benchmark.rocm` key, `pylib/config.py:71-73`), so a config with a
literal `backend: rocm` value is genuinely rejected by real validation no
matter what else the fixture contains — confirmed by running
`validate_config(migrate_config(cfg))` directly against this fixture's text
plus a `version`/`models` block. This was already true in production before
this plan; the fixture only ever "worked" in this test because the test's
own `uv` stub was a no-op that skipped validation entirely. Since this
fixture's actual regression target
(`test_render_unit_never_adds_the_rocm_kernel_device`) only asserts that
`/dev/kfd` never appears in the rendered output — a property that holds for
any backend value, because neither `render-unit.sh` nor `pylib/compose.py`
has a ROCm-specific device branch — switch `backend` to `vulkan` (a value
`validate_config()` accepts) and add the missing `version`/`models` fields,
mirroring the shape `run_lifecycle_script`'s fixture already uses
(`tests/test_shell.py:1299-1334`).

`version`/`models`/`backend` are not the whole gap, though: once this
fixture reaches real validation and then real rendering, `runtime:` must
also carry `flash_attn`, `cache_type_k`, and `cache_type_v`. Confirmed
against the current `pylib/presets.py` (`render_presets()`, lines 38–44):
it does direct dict access — `runtime["flash_attn"]`,
`runtime["cache_type_k"]`, `runtime["cache_type_v"]` — with no `.get()` and
no default, and neither `migrate_config()` nor `validate_config()`
(`pylib/config.py`) defaults or requires these keys. So a `runtime:` block
containing only `models_max`/`parallel_slots`/`ubatch_size` — which is all
this fixture had — passes `require_valid_config(migrate_config(cfg))`
cleanly but then raises `KeyError: 'flash_attn'` inside `render_presets()`,
which `render-unit.sh`'s "Generating presets.ini" step (Task 9 Step 5) calls
under `set -euo pipefail`. That crash would fail this test and both new
tests in Step 3 below, all of which assert `result.returncode == 0`. Add
the same three keys, in the same types (`flash_attn` a bool,
`cache_type_k`/`cache_type_v` strings), that `run_lifecycle_script`'s
already-working fixture uses (`tests/test_shell.py:1320-1322`). Change the
fixture's `config.write_text(...)` call to:

```python
    config.write_text(
        "version: 1\n"
        "server:\n"
        "  host: 0.0.0.0\n"
        "  port: 8000\n"
        "  api_key: key\n"
        "  sleep_idle_seconds: 300\n"
        "  start_at_boot: false\n"
        "gpu:\n"
        # validate_config() only accepts vulkan/cpu (pylib/config.py:31); this
        # fixture predates ROCm removal but must still pass real validation
        # now that `uv` forwards to the real binary below. The regression
        # this test guards — /dev/kfd never appears in the rendered output —
        # does not depend on which backend value is used.
        "  backend: vulkan\n"
        "  image: example.invalid/llama:latest\n"
        "  device_name: ''\n"
        "  vram_total_mib: 8192\n"
        "  reserve_mode: auto\n"
        "  reserve_floor_mib: 1024\n"
        "runtime:\n"
        "  models_max: 1\n"
        "  parallel_slots: 1\n"
        "  ubatch_size: 512\n"
        "  flash_attn: true\n"
        "  cache_type_k: q8_0\n"
        "  cache_type_v: q8_0\n"
        "models:\n"
        "  - alias: test\n"
        "    label: Test\n"
        "    parameters: 1B\n"
        "    quantization: Q4_K_M\n"
        "    enabled: true\n"
        "    file: test.gguf\n"
        "    url: https://example.invalid/test.gguf\n"
        "    size_bytes: 1\n"
        "    vram_budget: 10%\n"
        "    ctx_size: 8192\n"
        "    client_max_output_tokens: 8192\n"
        "    n_gpu_layers: 99\n"
    )
```

In that same helper, the current `uv` stub is a bare no-op (part of
`for name in ("podman", "systemctl", "uv"): ... "#!/usr/bin/bash\nexit 0\n"`).
The new `render-unit.sh` (Step 5 below) writes `$COMPOSE_FILE` by shelling
out to `llmenv render-compose` via `tools/lib.sh`'s `llmenv() { uv run
"${REPO_DIR}/llmenv.py" "$@"; }` — real Python execution. A no-op `uv` stub
would make that call silently succeed without ever writing the compose
file, so Step 3's new
`test_render_unit_writes_a_compose_file_and_wrapper_unit` would fail no
matter what `render-unit.sh` does. Pull `uv` out of that no-op loop and
give it its own stub that forwards to the real binary, matching the
`REAL_UV`-forwarding convention already used elsewhere in this file (e.g.
`tests/test_shell.py:378`, `:1383`, `:3860`):

```python
    for name in ("podman", "systemctl"):
        command = commands / name
        command.write_text("#!/usr/bin/bash\nexit 0\n")
        command.chmod(command.stat().st_mode | stat.S_IXUSR)

    real_uv = shutil.which("uv")
    assert real_uv is not None
    uv = commands / "uv"
    uv.write_text("#!/usr/bin/bash\nexec \"$REAL_UV\" \"$@\"\n")
    uv.chmod(uv.stat().st_mode | stat.S_IXUSR)
```

(replaces the existing `for name in ("podman", "systemctl", "uv"): ...`
loop) and add `"REAL_UV": real_uv,` to the `environment` dict a few lines
below, alongside the existing `"REAL_YQ": real_yq,` entry.

`test_render_unit_never_adds_the_rocm_kernel_device` needs no change beyond
that — it already just asserts `"/dev/kfd" not in container.read_text()`
against whatever path the helper returns, and the wrapper unit (which
contains no per-GPU device directives at all — those live in the compose
file, not the wrapper — trivially satisfies this) preserves the same
regression protection.

`test_enable_boot_renders_a_health_gated_mdns_user_unit` currently reads
`container_unit = (config.parent.parent / "containers/systemd/llm-server.container").read_text()`
and asserts `"Wants=llm-server-mdns.service" in container_unit`. Replace
that block:

```python
def test_enable_boot_renders_a_health_gated_mdns_user_unit(
    tmp_path: pathlib.Path,
) -> None:
    """Boot setup must make mDNS discoverable as a reloaded user unit."""
    result, config, calls = run_lifecycle_script(tmp_path, "setup/enable-boot.sh")
    mdns_unit = config.parent.parent / "systemd/user/llm-server-mdns.service"
    wrapper_unit = config.parent.parent / "systemd/user/llm-server.service"

    assert result.returncode == 0, result.stderr
    unit = mdns_unit.read_text()
    wrapper = wrapper_unit.read_text()
    assert "Wants=llm-server-mdns.service" in wrapper
    assert "Requires=llm-server.service" in unit
    assert "After=llm-server.service" in unit
    assert "PartOf=llm-server.service" in unit
    assert "Restart=on-failure" in unit
    assert "ExecStart=podman compose" in wrapper
    assert "ExecStartPre=" in unit
    assert "http://127.0.0.1:8000/health" in unit
    assert "avahi-publish -s llm _http._tcp 8000" in unit
    assert "systemctl --user daemon-reload" in calls.read_text()
```

Note: the pre-existing assertion this replaces (`tests/test_shell.py:3157`,
`assert "Restart=on-failure" in unit`) checks `unit = mdns_unit.read_text()`
— the **mDNS** unit, not the wrapper/container unit. The mDNS heredoc's own
`Restart=on-failure`/`RestartSec=2` lines (current `setup/render-unit.sh:74-75`)
are carried into the rewritten mDNS heredoc in Step 5 below unchanged, so
this assertion is reinstated above (against `unit`, i.e. `mdns_unit`, not
`wrapper`) rather than dropped — it is still true and still a real
regression check. It is the container/wrapper unit's own `[Service]`
`Restart=on-failure` (current `setup/render-unit.sh:117`) — a separate,
unrelated occurrence — that genuinely moves to the compose file's
`restart:` key (see `pylib/compose.py`); nothing in this test ever asserted
on that one.

- [ ] **Step 2: Run the updated tests against the *old* render-unit.sh to confirm they now fail for the right reason**

Run: `uv run --with pytest pytest tests/test_shell.py -v -k "render_unit_never_adds_the_rocm_kernel_device or enable_boot_renders_a_health_gated_mdns_user_unit"`
Expected: FAIL — the old script still writes `containers/systemd/llm-server.container`,
so `home / ".config/systemd/user/llm-server.service"` does not exist yet
(`FileNotFoundError` on `.read_text()`).

- [ ] **Step 3: Write the new compose/wrapper-unit tests**

Append to `tests/test_shell.py`:

```python
def test_render_unit_writes_a_compose_file_and_wrapper_unit(tmp_path: pathlib.Path) -> None:
    result, wrapper_unit = run_render_unit_with_legacy_rocm_config(tmp_path)
    compose_file = wrapper_unit.parent.parent.parent / "llm-env/docker-compose.yml"

    assert result.returncode == 0, result.stderr
    assert compose_file.exists()
    assert "llm-server" in compose_file.read_text()
    assert "ExecStart=podman compose -f docker-compose.yml up -d" in wrapper_unit.read_text()
    assert "ExecStop=podman compose -f docker-compose.yml down" in wrapper_unit.read_text()


def test_render_unit_wrapper_unit_omits_install_section_by_default(
    tmp_path: pathlib.Path,
) -> None:
    _, wrapper_unit = run_render_unit_with_legacy_rocm_config(tmp_path)
    assert "[Install]" not in wrapper_unit.read_text()
```

- [ ] **Step 4: Run the new tests to verify they fail**

Run: `uv run --with pytest pytest tests/test_shell.py -v -k test_render_unit_writes_a_compose_file`
Expected: FAIL — the compose file and wrapper unit do not exist yet at the
new paths.

- [ ] **Step 5: Rewrite `setup/render-unit.sh`**

Replace the file's contents from `log_step "Generating presets.ini"` (the
point where today's file still applies unchanged above this) through the
end with:

```bash
log_step "Generating presets.ini"
presets_path="${HOME}/.config/llm-env/presets.ini"
llmenv --config "$CONFIG_PATH" presets \
    --models-dir /models --device "$device" --output "$presets_path" >/dev/null
log_info "wrote ${presets_path}"

log_step "Rendering the compose file"
llmenv --config "$CONFIG_PATH" render-compose \
    --models-dir "$MODELS_DIR" --presets-path "$presets_path" --output "$COMPOSE_FILE" >/dev/null
log_info "wrote ${COMPOSE_FILE}"

log_step "Rendering the systemd wrapper unit"
mkdir -p "$(dirname "$WRAPPER_UNIT_PATH")"

mdns_unit="${HOME}/.config/systemd/user/${UNIT_NAME}-mdns.service"
mdns_wants=""
if avahi_publish="$(command -v avahi-publish 2>/dev/null)"; then
    mkdir -p "$(dirname "$mdns_unit")"
    mdns_name="$(yq -r '.server.mdns_name' "$CONFIG_PATH")"
    curl_path="$(command -v curl)"
    cat > "$mdns_unit" <<EOF
# Generated by render-unit.sh. Publishes only after the router is healthy.
[Unit]
Description=llama.cpp router mDNS publication
Requires=${UNIT_NAME}.service
After=${UNIT_NAME}.service
BindsTo=${UNIT_NAME}.service
PartOf=${UNIT_NAME}.service

[Service]
Type=simple
ExecStartPre=/usr/bin/bash -c 'i=0; while [ \$\$i -lt ${LLM_ENV_HEALTH_TIMEOUT_SECONDS} ]; do ${curl_path} -fsS -o /dev/null http://127.0.0.1:${port}/health && exit 0; i=\$\$((i + 1)); sleep 1; done; exit 1'
ExecStart=${avahi_publish} -s ${mdns_name} _http._tcp ${port}
Restart=on-failure
RestartSec=2
EOF
    chmod 600 "$mdns_unit"
    mdns_wants="Wants=${UNIT_NAME}-mdns.service"
    log_info "wrote ${mdns_unit}"
else
    rm -f "$mdns_unit"
fi

start_at_boot="$(yq -r '.server.start_at_boot // false' "$CONFIG_PATH")"
if [ "$start_at_boot" = "true" ]; then
    install_section=$'\n[Install]\nWantedBy=default.target'
else
    install_section=""
fi

cat > "$WRAPPER_UNIT_PATH" <<EOF
# Generated by render-unit.sh from ${CONFIG_PATH}. Edits will be overwritten.
[Unit]
Description=llm-env compose stack (${UNIT_NAME})
After=network-online.target
${mdns_wants}

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=$(dirname "$COMPOSE_FILE")
ExecStart=podman compose -f $(basename "$COMPOSE_FILE") up -d
ExecStop=podman compose -f $(basename "$COMPOSE_FILE") down
TimeoutStartSec=300
${install_section}
EOF
log_info "wrote ${WRAPPER_UNIT_PATH}"
chmod 600 "$WRAPPER_UNIT_PATH"
systemctl --user daemon-reload
```

(The now-unused `device_lines="AddDevice=/dev/dri"` line, current
`setup/render-unit.sh:52` — device passthrough is now hardcoded inside
`pylib/compose.py`, not assembled in bash — falls inside the range this
replacement already covers wholesale; it does not need a separate edit.)

`host`, `api_key`, and `sleep_idle` become dead once the Quadlet heredoc is
gone: `pylib/compose.py` reads `server.host`/`server.api_key`/
`server.sleep_idle_seconds` straight out of the config dict passed to
`render_compose()` (Task 5), not from bash variables, since
`cmd_render_compose` (Task 6) loads the config itself
(`require_valid_config(load_config(Path(args.config)))`). Left in place,
`host="$HOST"` and `api_key="$API_KEY"` trip shellcheck's SC2034
(unused variable), and `sleep_idle="$(yq -r '.server.sleep_idle_seconds' …)"`
does a needless `yq` call for a value nothing reads. `port` stays — the
mDNS block still needs it. Change the block at the top of the file
(currently lines 16–24):

```bash
load_server_config
# shellcheck disable=SC2153 # HOST/PORT/API_KEY are set by load_server_config() in ../tools/lib.sh.
host="$HOST"
# shellcheck disable=SC2153
port="$PORT"
# shellcheck disable=SC2153
api_key="$API_KEY"
sleep_idle="$(yq -r '.server.sleep_idle_seconds' "$CONFIG_PATH")"
models_max="$(yq -r '.runtime.models_max' "$CONFIG_PATH")"
```

to:

```bash
load_server_config
# shellcheck disable=SC2153 # PORT is set by load_server_config() in ../tools/lib.sh.
port="$PORT"
models_max="$(yq -r '.runtime.models_max' "$CONFIG_PATH")"
```

(`models_max` stays too — the `[ "$models_max" -gt 0 ] || die …` check right
below still consumes it directly; `pylib/compose.py` reads its own copy of
`runtime.models_max` from the config dict, same as `server.*`.)

- [ ] **Step 6: Run the render-unit tests to verify they pass**

Run: `uv run --with pytest pytest tests/test_shell.py -v -k "render_unit or enable_boot"`
Expected: PASS, all of them, including the two updated pre-existing tests
AND the pre-existing, unmodified
`test_render_unit_mdns_execstartpre_uses_the_configured_health_timeout`
(added by the sibling CLI/Script Hygiene plan) — the mDNS `ExecStartPre`
line in Step 5's rewrite above interpolates
`${LLM_ENV_HEALTH_TIMEOUT_SECONDS}`, not a hardcoded `60`, specifically so
this test keeps passing unmodified.

- [ ] **Step 7: Run shellcheck**

Run: `shellcheck -s bash setup/render-unit.sh`
Expected: no new warnings.

- [ ] **Step 8: Commit**

```bash
git add setup/render-unit.sh tests/test_shell.py
git commit -m "feat(render-unit): generate a compose file and systemd wrapper unit"
```

---

### Task 10: `scripts/clean.sh` — remove the compose stack instead of the Quadlet unit

**Files:**
- Modify: `scripts/clean.sh`
- Test: `tests/test_shell.py`

**Interfaces:**
- Consumes: `COMPOSE_FILE`/`WRAPPER_UNIT_PATH` (Task 7).
- Produces: `make clean` runs `podman compose … down` before removing
  files, removes the wrapper unit and compose file instead of the Quadlet
  `.container` file. The existing `configured_image` selection logic is
  preserved unchanged and merged into this flow: `gpu.image` is still read
  via `yq` from `$CONFIG_PATH` **before** the config file is deleted: if set,
  only that image is removed; otherwise `${VULKAN_IMAGE}`/`${CPU_IMAGE}` are
  removed as the default pair. The confirmation prompt and
  `LLM_ENV_ASSUME_YES` behavior are otherwise unchanged.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_shell.py`:

```python
def test_cleanup_removes_the_compose_file_and_wrapper_unit(tmp_path: pathlib.Path) -> None:
    commands = tmp_path / "bin"
    commands.mkdir()
    calls = tmp_path / "calls"
    for name in ("systemctl",):
        _mock_command(commands, name)
    podman = commands / "podman"
    podman.write_text(
        "#!/usr/bin/bash\nprintf 'podman %s\\n' \"$*\" >> \"$CALLS\"\n"
    )
    podman.chmod(podman.stat().st_mode | stat.S_IXUSR)

    home = tmp_path / "home"
    compose_file = home / ".config/llm-env/docker-compose.yml"
    wrapper_unit = home / ".config/systemd/user/llm-server.service"
    compose_file.parent.mkdir(parents=True)
    compose_file.write_text("services: {llm-server: {}}\n")
    wrapper_unit.parent.mkdir(parents=True)
    wrapper_unit.write_text("[Unit]\n")

    environment = os.environ | {
        "CALLS": str(calls),
        "HOME": str(home),
        "LLM_ENV_ASSUME_YES": "1",
        "PATH": f"{commands}:/usr/bin:/bin",
    }
    result = subprocess.run(
        ["/usr/bin/bash", "scripts/clean.sh"],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert f"podman compose -f {compose_file} down" in calls.read_text()
    assert not compose_file.exists()
    assert not wrapper_unit.exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --with pytest pytest tests/test_shell.py -v -k test_cleanup_removes_the_compose_file`
Expected: FAIL — the current `clean.sh` never calls `podman compose … down`
and removes `${QUADLET_DIR}/${UNIT_NAME}.container`, not these paths, so
neither the `calls.read_text()` assertion nor the removal assertions hold
(the fixture-created files remain on disk).

- [ ] **Step 3: Update `scripts/clean.sh`**

The current file (unchanged by any earlier task in this plan) already reads
`gpu.image` via `yq` before deleting `$CONFIG_PATH`, and removes either that
configured image or the `${VULKAN_IMAGE}`/`${CPU_IMAGE}` default pair — this
`configured_image` logic is preserved verbatim below, with the removed-file
list and a `podman compose … down` step added ahead of it for the new
compose/wrapper-unit layout. Replace the file's contents:

```bash
#!/usr/bin/env bash
# clean.sh — remove the compose stack, unit, config, and images. Keeps downloaded models.
set -euo pipefail
# shellcheck disable=SC1091 # Resolved from this script at runtime.
# shellcheck source=../tools/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/../tools/lib.sh"

configured_image=""
if [ -f "$CONFIG_PATH" ]; then
    configured_image="$(yq -r '.gpu.image // ""' "$CONFIG_PATH" 2>/dev/null || true)"
fi
if [ -n "$configured_image" ] && [ "$configured_image" != null ]; then
    images_to_remove="$configured_image"
else
    configured_image=""
    images_to_remove="${VULKAN_IMAGE} and ${CPU_IMAGE} (default; no configured gpu.image found)"
fi

echo "This removes:"
echo "  compose stack  ${COMPOSE_FILE}"
echo "  unit           ${WRAPPER_UNIT_PATH}"
echo "  config         ${CONFIG_PATH}"
echo "  images         ${images_to_remove}"
echo "Downloaded models in ${MODELS_DIR} are KEPT."
if [ "${LLM_ENV_ASSUME_YES:-0}" = "1" ]; then
    confirm=yes
else
    read -rp "Proceed? (yes/no) " confirm
fi
[ "$confirm" = "yes" ] || { echo "Aborted."; exit 1; }

if [ -f "$COMPOSE_FILE" ]; then
    podman compose -f "$COMPOSE_FILE" down 2>/dev/null || true
fi
systemctl --user stop "${UNIT_NAME}.service" 2>/dev/null || true
systemctl --user disable "${UNIT_NAME}.service" 2>/dev/null || true
rm -f "$WRAPPER_UNIT_PATH"
systemctl --user daemon-reload
rm -f "$CONFIG_PATH" "$COMPOSE_FILE" "${HOME}/.config/llm-env/presets.ini"
if [ -n "$configured_image" ]; then
    podman rmi -f "$configured_image" 2>/dev/null || true
else
    podman rmi -f "$VULKAN_IMAGE" "$CPU_IMAGE" 2>/dev/null || true
fi
log_info "cleanup complete"
```

Note the `configured_image` read still happens before `rm -f "$CONFIG_PATH"`
— same ordering constraint as the pre-existing script, just now interleaved
with the compose-down/wrapper-unit-removal steps rather than a Quadlet
removal step.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --with pytest pytest tests/test_shell.py -v -k "cleanup"`
Expected: PASS, including the pre-existing
`test_cleanup_preserves_the_host_rocm_image`,
`test_cleanup_removes_the_configured_gpu_image_not_a_hardcoded_one`,
`test_cleanup_falls_back_to_default_images_without_a_config`, and
`test_cleanup_confirmation_prompt_reflects_the_configured_gpu_image` — none
of these pre-existing tests create a `$COMPOSE_FILE`, so the new
`podman compose … down` step is skipped for them (the `[ -f "$COMPOSE_FILE" ]`
guard is false) and their assertions exercise the same `configured_image`
branch as before, unaffected by the new compose/wrapper-unit lines.

- [ ] **Step 5: Run shellcheck**

Run: `shellcheck -s bash scripts/clean.sh`
Expected: no new warnings.

- [ ] **Step 6: Commit**

```bash
git add scripts/clean.sh tests/test_shell.py
git commit -m "feat(clean): tear down the compose stack instead of the quadlet unit"
```

---

### Task 11: `setup/disable-boot.sh` — point at the wrapper unit

**Files:**
- Modify: `setup/disable-boot.sh`
- Test: `tests/test_shell.py`

**Interfaces:**
- Produces: `disable-boot.sh` edits `$WRAPPER_UNIT_PATH` instead of the
  removed `${QUADLET_DIR}/${UNIT_NAME}.container`. `setup/enable-boot.sh`
  needs no code change — it only calls `render-unit.sh`, whose own output
  path already moved in Task 9.

- [ ] **Step 1: Write the failing test**

`run_lifecycle_script` (used by the enable-boot/start/key-reset tests above
it in this file) builds its fixture and executes the target script in one
call, with no hook to pre-seed a wrapper unit file first — so this test
uses its own small fixture instead, following the same direct-stubbing
style `run_render_unit_with_legacy_rocm_config` already uses elsewhere in
this file. Append to `tests/test_shell.py`:

```python
def test_disable_boot_removes_install_section_from_the_wrapper_unit(
    tmp_path: pathlib.Path,
) -> None:
    real_yq = shutil.which("yq")
    assert real_yq is not None

    commands = tmp_path / "bin"
    commands.mkdir()
    _mock_command(commands, "systemctl")
    yq = commands / "yq"
    yq.write_text("#!/usr/bin/bash\nexec \"$REAL_YQ\" \"$@\"\n")
    yq.chmod(yq.stat().st_mode | stat.S_IXUSR)

    home = tmp_path / "home"
    config = home / ".config/llm-env/models.yml"
    config.parent.mkdir(parents=True)
    config.write_text("version: 1\nserver:\n  start_at_boot: true\n")

    wrapper_unit = home / ".config/systemd/user/llm-server.service"
    wrapper_unit.parent.mkdir(parents=True)
    wrapper_unit.write_text(
        "[Unit]\nDescription=x\n\n[Service]\nExecStart=x\n\n[Install]\nWantedBy=default.target\n"
    )

    environment = os.environ | {
        "HOME": str(home),
        "LLM_ENV_CONFIG": str(config),
        "PATH": f"{commands}:/usr/bin:/bin",
        "REAL_YQ": real_yq,
    }
    result = subprocess.run(
        ["/usr/bin/bash", "setup/disable-boot.sh"],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "[Install]" not in wrapper_unit.read_text()
    assert yq_value(config, ".server.start_at_boot") == "false"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --with pytest pytest tests/test_shell.py -v -k test_disable_boot_removes_install_section`
Expected: FAIL — the current script edits
`${QUADLET_DIR}/${UNIT_NAME}.container`, which does not exist at the
fixture's wrapper-unit path, so the `sed` guard (`if [ -f "$unit" ]`) skips
entirely and `[Install]` remains in the fixture file untouched.

- [ ] **Step 3: Update `setup/disable-boot.sh`**

Change:

```bash
unit="${QUADLET_DIR}/${UNIT_NAME}.container"
```

to:

```bash
unit="$WRAPPER_UNIT_PATH"
```

The rest of the file (the `sed -i '/^\[Install\]$/,$d' "$unit"` removal and
`systemctl --user daemon-reload`) is unchanged — the wrapper unit ends with
an `[Install]` block in exactly the same shape the Quadlet file did.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --with pytest pytest tests/test_shell.py -v -k test_disable_boot_removes_install_section`
Expected: PASS.

- [ ] **Step 5: Run the full shell test suite and shellcheck**

Run: `uv run --with pytest pytest tests/test_shell.py -v`
Run: `shellcheck -s bash ./tools/*.sh ./setup/*.sh ./scripts/*.sh`
Expected: PASS; no shellcheck warnings anywhere (this is the first point in
the plan where every `.sh` file has had its `QUADLET_DIR` references
removed, so this is the first full `make validate`-equivalent sweep).

- [ ] **Step 6: Commit**

```bash
git add setup/disable-boot.sh tests/test_shell.py
git commit -m "fix(disable-boot): edit the wrapper unit instead of the removed quadlet file"
```

---

### Task 12: `scripts/check-setup.sh` — validate the rendered compose file

**Files:**
- Modify: `scripts/check-setup.sh`
- Test: `tests/test_shell.py`

**Interfaces:**
- Produces: a new offline check, "Compose file", that runs
  `podman compose -f "$COMPOSE_FILE" config` and records PASS/FAIL through
  the existing `record_command` helper — no running container required,
  consistent with every other check in this file.

- [ ] **Step 1: Write the failing test**

`run_check_setup_with_stubs` returns `(result, calls, models_dir)` and fixes
`HOME` to `tmp_path / "home"` internally, so the compose file path is
derived the same way. Its `podman` stub only special-cases `--list-devices`
and `cli`; any other invocation (including `compose … config`) falls
through the `case` with no matching arm and exits 0, so this test exercises
the step's success path. Append to `tests/test_shell.py`:

```python
def test_check_setup_validates_the_rendered_compose_file(tmp_path: pathlib.Path) -> None:
    result, calls, _ = run_check_setup_with_stubs(tmp_path)
    compose_file = tmp_path / "home/.config/llm-env/docker-compose.yml"

    assert "Compose file" in result.stdout
    assert f"podman compose -f {compose_file} config" in calls.read_text()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --with pytest pytest tests/test_shell.py -v -k test_check_setup_validates_the_rendered_compose_file`
Expected: FAIL — `check-setup.sh` has no "Compose file" step and never
invokes `podman compose ... config`.

- [ ] **Step 3: Add the check**

In `scripts/check-setup.sh`, after the existing "Container image" step and
before "Model files", add:

```bash
log_step "Compose file"
record_command "compose file syntax" \
    "podman compose -f ${COMPOSE_FILE} config" "" \
    "exit status: 0" "" 0 "Command stdout" "Command stderr" \
    podman compose -f "$COMPOSE_FILE" config || true
```

This follows the exact same `record_command` contract every other check in
this file already uses (see `test_shell.py`'s reading of the "Container
image" step for the parameter shape) — success is `exit status: 0`, no
parsed-result comparison needed, so `show_parsed=0`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --with pytest pytest tests/test_shell.py -v -k test_check_setup_validates_the_rendered_compose_file`
Expected: PASS.

- [ ] **Step 5: Run the full shell test suite and shellcheck**

Run: `uv run --with pytest pytest tests/test_shell.py -v`
Run: `shellcheck -s bash scripts/check-setup.sh`
Expected: PASS; no new warnings.

- [ ] **Step 6: Commit**

```bash
git add scripts/check-setup.sh tests/test_shell.py
git commit -m "feat(check-setup): validate the rendered compose file"
```

---

### Task 13: `setup/setup.sh` — compute and persist resource limits

**Files:**
- Modify: `setup/setup.sh`
- Test: `tests/test_shell.py`

**Interfaces:**
- Consumes: `llmenv resources` (Task 3).
- Produces: a new step in `setup.sh`'s flow that runs `llmenv resources`
  and, on success, writes `resources.llm_server.cpus`/`memory_mib` into the
  config via `yq`, matching how `gpu.pci_address`/`gpu.vram_total_mib`/
  `gpu.device_name` are already written directly in this script (not
  through a `save_config` CLI call). On failure (host has too few
  resources — see `pylib/resources.py`'s `ResourceError`), the step warns
  and leaves the existing `0`/`0` sentinel values in place rather than
  aborting setup, since `pylib/compose.py` already treats `0` as "no
  explicit cap." The new `resources_json` temp file's `EXIT` cleanup is
  combined with the pre-existing `budget_json` `EXIT` trap from Step 7/7
  into a single `trap` statement (bash `trap … EXIT` replaces rather than
  accumulates), so both temp files are still removed on exit.

- [ ] **Step 1: Extend the shared setup fixture and write the failing test**

`run_setup_with_numbered_selection` (defined earlier in `tests/test_shell.py`,
already used by the GPU/model-selection tests) stubs `uv` with a `case`
statement keyed on the trailing words of the invoked command
(`*' detect')`, `*' models list')`, `*' budget '*)`, etc.) and is driven by
a `selection` stdin string answering `setup.sh`'s prompts in order. A known
full-flow selection already used elsewhere in this file is `"1\n1,2\n2\n"`
(GPU #1, models 1 and 2, Vulkan device #2 — the stub's two listed devices
never exactly match the stubbed 16384 MiB GPU total, so the numbered
fallback prompt fires and needs an answer). Add a `*' resources')` arm to
that fixture's `uv` stub, alongside the existing `*' list-devices '*)` arm:

```python
"  *' resources')\n"
"    printf '%s\\n' '{\"llm_server\": {\"cpus\": 6, \"memory_mib\": 28672}}' ;;\n"
```

Then append a new test to `tests/test_shell.py`:

```python
def test_setup_writes_computed_resource_limits(tmp_path: pathlib.Path) -> None:
    """Setup must persist llmenv resources output into resources.llm_server."""
    _, _, config = run_setup_with_numbered_selection(tmp_path, "1\n1,2\n2\n")

    assert yq_value(config, ".resources.llm_server.cpus") == "6"
    assert yq_value(config, ".resources.llm_server.memory_mib") == "28672"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --with pytest pytest tests/test_shell.py -v -k test_setup_writes_computed_resource_limits`
Expected: FAIL — `setup.sh` never calls `llmenv resources` today, and the
fixture config built by `run_setup_with_numbered_selection` has no
`resources` key at all yet, so `yq_value` returns `null`, not `"6"`/`"28672"`.

- [ ] **Step 3: Add the step to `setup/setup.sh`**

After "Step 7/7  Checking the VRAM budget" (the script's last existing
step), add an eighth step:

Bash `trap … EXIT` statements are not additive — a second one silently
replaces the first, which would disable the existing `Step 7/7` block's
`trap 'rm -f "$budget_json"' EXIT` (`setup/setup.sh:128`, unmodified by this
task) and leak `$budget_json` on exit. Set a single trap here that cleans up
both temp files instead of adding a second, competing one:

```bash
log_step "Step 8/8  Computing resource limits"
resources_json="$(mktemp)"
# Replaces the Step 7/7 trap above with one that cleans up both temp
# files — a second `trap … EXIT` overwrites rather than adds to the first.
trap 'rm -f "$budget_json" "$resources_json"' EXIT
if llmenv resources > "$resources_json"; then
    cpus="$(jq -r '.llm_server.cpus' "$resources_json")"
    memory_mib="$(jq -r '.llm_server.memory_mib' "$resources_json")"
    CPUS="$cpus" MEMORY_MIB="$memory_mib" yq -i '
        .resources.llm_server.cpus = (strenv(CPUS) | tonumber) |
        .resources.llm_server.memory_mib = (strenv(MEMORY_MIB) | tonumber)
      ' "$CONFIG_PATH"
    log_info "reserved ${cpus} CPUs, ${memory_mib} MiB RAM for llm-server"
else
    log_warn "$(jq -r '.error' "$resources_json")"
    log_warn "leaving resources.llm_server uncapped (0 = no explicit limit)"
fi
```

Adding an eighth step changes the total step count, so every existing
`log_step "Step N/7  …"` header in the script must be renumbered to `N/8` —
not just the last one — or the script would print `Step 1/7` … `Step 6/7`,
then jump to `Step 7/8`, `Step 8/8`: an internally inconsistent progress
sequence. Update all seven existing headers (confirmed against the current
file at these exact lines):

```
setup/setup.sh:26   log_step "Step 1/7  Creating configuration"     -> "Step 1/8  Creating configuration"
setup/setup.sh:35   log_step "Step 2/7  Detecting GPUs"              -> "Step 2/8  Detecting GPUs"
setup/setup.sh:54   log_step "Step 3/7  Selecting models"            -> "Step 3/8  Selecting models"
setup/setup.sh:77   log_step "Step 4/7  Downloading models"          -> "Step 4/8  Downloading models"
setup/setup.sh:90   log_step "Step 5/7  Validating model files"      -> "Step 5/8  Validating model files"
setup/setup.sh:95   log_step "Step 6/7  Preparing Vulkan"            -> "Step 6/8  Preparing Vulkan"
setup/setup.sh:126  log_step "Step 7/7  Checking the VRAM budget"    -> "Step 7/8  Checking the VRAM budget"
```

Only the `N/7` -> `N/8` numerator/denominator changes; the rest of each
header string (the descriptive text after the two spaces) stays exactly as
it is today. The new eighth step added above already reads
`"Step 8/8  Computing resource limits"`. The final summary line at the end
of the script (`Setup complete. Next: make check-setup`) is unchanged.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --with pytest pytest tests/test_shell.py -v -k test_setup_writes_computed_resource_limits`
Expected: PASS.

- [ ] **Step 5: Run the full shell test suite and shellcheck**

Run: `uv run --with pytest pytest tests/test_shell.py -v`
Run: `shellcheck -s bash setup/setup.sh`
Expected: PASS; no new warnings.

- [ ] **Step 6: Commit**

```bash
git add setup/setup.sh tests/test_shell.py
git commit -m "feat(setup): compute and persist llm-server resource limits"
```

---

### Task 14: Documentation — generated files and troubleshooting

**Files:**
- Modify: `.agents/architecture.md`
- Modify: `AGENTS.md`
- Modify: `README.md`
- Modify: `QUICK_START.md`
- Test: `tests/test_docs.py`

**Interfaces:**
- Produces: a documented, exact answer to "which file is generated, where,
  and what do I run to check it" for the new compose-based lifecycle,
  replacing any stale mention of the Quadlet `.container` path.

- [ ] **Step 1: Confirm the stale references this task fixes**

Run: `grep -rn "containers/systemd\|\.container\b\|Quadlet\|quadlet" AGENTS.md README.md QUICK_START.md .agents/architecture.md`

At the time this task is written, that matches exactly five lines, all
describing the now-removed Quadlet mechanism (none are unrelated
false-positives): `AGENTS.md:6`, `README.md:4`, `QUICK_START.md:24`,
`.agents/architecture.md:16` (the `scripts/start.sh` row), and
`.agents/architecture.md:50` (the `## Platform` paragraph). Steps 4 and 5
below fix all five. If a rerun of this grep at implementation time turns up
additional matches (e.g. from other work landing first), triage each one
the same way before continuing.

- [ ] **Step 2: Write the failing test**

Append to `tests/test_docs.py`:

```python
def test_architecture_documents_the_compose_lifecycle_files() -> None:
    architecture = (ROOT / ".agents/architecture.md").read_text().lower()

    assert "~/.config/llm-env/docker-compose.yml" in architecture
    assert "~/.config/systemd/user/llm-server.service" in architecture
    assert "podman compose" in architecture
    assert "containers/systemd/llm-server.container" not in architecture
    assert "quadlet" not in architecture


def test_no_stale_quadlet_references_outside_architecture() -> None:
    for relative_path in ("AGENTS.md", "README.md", "QUICK_START.md"):
        text = (ROOT / relative_path).read_text().lower()
        assert "quadlet" not in text, f"{relative_path} still mentions quadlet"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run --with pytest pytest tests/test_docs.py -v -k "compose_lifecycle_files or stale_quadlet"`
Expected: FAIL — `.agents/architecture.md`, `AGENTS.md`, `README.md`, and
`QUICK_START.md` all still say "quadlet" (see Step 1's grep), and
`.agents/architecture.md` does not yet mention the compose/wrapper-unit
paths.

- [ ] **Step 4: Update `.agents/architecture.md`**

In the `## Files` table, the current `scripts/start.sh` row (line 16) reads:

```markdown
| `scripts/start.sh` | Budget check, device resolution, quadlet render, health gate |
```

Replace it, and add new `pylib/compose.py` and `pylib/resources.py` rows
directly below it (the latter parallels the existing `pylib/budget.py` row
— `pylib/resources.py` is a first-class new module from Task 2, wired into
`llmenv.py` in Task 3 and `setup.sh` in Task 13):

```markdown
| `scripts/start.sh` | Budget check, device resolution, compose+wrapper-unit render, health gate |
| `pylib/compose.py` | `docker-compose.yml` rendering from `models.yml` |
| `pylib/resources.py` | Host CPU/RAM budgeting for the compose container stack |
```

In the `## Platform` section, the current paragraph (line 50) reads:

```markdown
Linux only. Bazzite/Fedora with podman and rootless quadlets. There is no
macOS support and none is planned.
```

Replace it with:

```markdown
Linux only. Bazzite/Fedora with podman, running as a rootless compose
stack. There is no macOS support and none is planned.
```

Add a new `## Container Lifecycle` section (after `## Invariants`, before
`## Platform`):

```markdown
## Container Lifecycle

`setup/render-unit.sh` writes two generated files, both regenerated on
every `make start`:

- `~/.config/llm-env/docker-compose.yml` — the container definition
  (`pylib/compose.py`). Not for hand editing.
- `~/.config/systemd/user/llm-server.service` — a thin systemd wrapper unit
  whose `ExecStart`/`ExecStop` run `podman compose … up -d`/`down`. This is
  the unit `make start`/`stop`/`status`/`logs`/`enable-boot` all operate on
  by name (`llm-server.service`); systemd supervises "is the compose stack
  up," while each compose service's own `restart:` policy handles
  crash-restart.

To check state directly: `systemctl --user status llm-server.service`,
`podman compose -f ~/.config/llm-env/docker-compose.yml ps`,
`journalctl --user -u llm-server -f`.
```

- [ ] **Step 5: Update `AGENTS.md`, `README.md`, and `QUICK_START.md`**

In `AGENTS.md`, the current lines 5–6 read:

```markdown
Local llama.cpp router server on Bazzite, running as a rootless podman
quadlet with GPU acceleration. Configuration lives in `models.yml`.
```

Replace with:

```markdown
Local llama.cpp router server on Bazzite, running as a rootless podman
compose stack with GPU acceleration. Configuration lives in `models.yml`.
```

In `README.md`, the current lines 3–4 read:

```markdown
Local llama.cpp router server with GPU acceleration, running as a rootless
podman quadlet on Bazzite.
```

Replace with:

```markdown
Local llama.cpp router server with GPU acceleration, running as a rootless
podman compose stack on Bazzite.
```

In `QUICK_START.md`, the current line 24 reads:

```
make enable-boot     # enable lingering and render the quadlet [Install] section
```

Replace with:

```
make enable-boot     # enable lingering and render the wrapper unit's [Install] section
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run --with pytest pytest tests/test_docs.py -v -k "compose_lifecycle_files or stale_quadlet"`
Expected: PASS.

- [ ] **Step 7: Run the full test suite**

Run: `uv run --with pytest pytest tests/ -v`
Run: `shellcheck -s bash ./tools/*.sh ./setup/*.sh ./scripts/*.sh`
Run: `uvx ruff check llmenv.py pylib tests`
Expected: PASS — this is the plan's final full `make validate && make test`
equivalent sweep.

- [ ] **Step 8: Commit**

```bash
git add .agents/architecture.md AGENTS.md README.md QUICK_START.md tests/test_docs.py
git commit -m "docs: document the compose lifecycle and drop stale quadlet references"
```

---

## End-to-End Verification

After Task 14, run the real lifecycle by hand (not stubbed) to confirm the
compose-based stack actually starts on this machine, matching the existing
verification sequence this repo uses after lifecycle changes:

```bash
make setup
make check-setup
make start
make check-server
make status
podman compose -f ~/.config/llm-env/docker-compose.yml ps
make stop
```

Confirm `podman-compose` is installed first (`make prerequisites`) — without
it, `podman compose` has no provider and `make start` will fail at the
`ExecStart=` step, exactly as reproduced in Task 8's tests.
