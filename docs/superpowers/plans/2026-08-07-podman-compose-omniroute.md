# OmniRoute Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a new `omniroute` compose service to the existing container stack, network-joined to `llm-server` and health-gated behind it, with a `pylib/omniroute.py` + `llmenv.py omniroute provision` command that idempotently points OmniRoute's admin API at the local router on every `make start`.

**Architecture:** Extends the compose/wrapper-unit stack already built by the `2026-08-07-compose-container-definitions.md` plan (Plan A) — this plan does not re-do prerequisites, the compose renderer, the systemd wrapper, or the `resources:` schema; it builds a second compose service and its provisioning logic on top of what Plan A shipped. `pylib/compose.py` gains a second service in the same rendered document; `pylib/omniroute.py` is new, pure-Python request/response logic (no bash), consistent with this repo's "Bash orchestrates, Python computes" split; `scripts/start.sh` calls it once both containers are healthy.

**Tech Stack:** Python 3.11 stdlib only (`urllib.request` for HTTP — no new dependency; `pyproject.toml` stays at `pyyaml>=6.0`), PyYAML, bash, `podman compose`, `yq`/`jq`.

## Global Constraints

- Podman only, no Docker (existing repo constraint).
- OmniRoute is always part of the stack — not optional, no feature flag.
- No changes to `setup/network.sh` (firewall/mDNS) or GPU/model selection.
- No multi-host or remote deployment.
- Already shipped by Plan A, reused as-is by this plan: `podman-compose` prerequisite check, `pylib/compose.py`'s `render_compose`/`write_compose`, the `~/.config/systemd/user/llm-server.service` wrapper unit, `pylib/resources.py`'s `HOST_CPU_FLOOR = 2` / `HOST_MEMORY_FLOOR_MIB = 4096`, and the `check-setup.sh` `podman compose … config` syntax step (it validates whatever is in the compose file, so it covers the new `omniroute` service automatically — no new check-setup task needed).
- New fixed reservation this plan introduces: OmniRoute gets a flat 1 CPU core / 1024 MiB RAM, taken out of the same host budget `pylib/resources.py` already computes for `llm_server`.
- `omniroute.image` default: `docker.io/diegosouzapw/omniroute:latest`. Default port: `20128`.
- The provider connection this tool owns and must never collide with a user-created one is named exactly `llm-env-local`.
- `omniroute.cli_token` / `omniroute.initial_password` are generated the same way `server.api_key` is today: `new_api_key()` in `tools/lib.sh`, written with `chmod 600`, the first time `start.sh` runs and finds them empty.
- Host-side probes use `127.0.0.1`, never `localhost` (existing repo invariant — `localhost` resolves to `::1` first on this system while podman publishes ports on `0.0.0.0`).
- After editing any `.sh` file: `make validate` (shellcheck + ruff). After editing any `.py` file: `make validate && make test`.

## Verify During Implementation

Researched during planning (via OmniRoute's public GitHub wiki/repo — `github.com/diegosouzapw/OmniRoute`), confirmed vs. still-assumed:

**Confirmed:**
- The container's own health check is `["CMD", "node", "healthcheck.mjs"]` (not an HTTP probe) — this is what upstream's own `docker-compose.yml` uses. Task 3 uses this exact command.
- The `x-omniroute-cli-token` header is a real machine-auth mechanism: OmniRoute injects/accepts a bearer-style token via this header for host-loopback requests, auto-generated on first run or settable via `OMNIROUTE_CLI_TOKEN`. Task 4 relies on this.
- `POST /v1/chat/completions` is OpenAI-compatible; `Authorization: Bearer <key>` is only enforced when `REQUIRE_API_KEY=true` is set in the container's environment (otherwise optional) — this repo's compose service does not set `REQUIRE_API_KEY`, so Task 7's completion probe sends no `Authorization` header.
- Provider/model routing on `/v1/chat/completions` uses a provider-prefixed `model` field (upstream docs show `"cc/claude-opus-4-6"` routing to a provider named `cc`).

**Still assumed — verify against a live container before trusting in production, and fix in the one place noted if wrong:**
- The exact JSON field names for `POST /api/providers` (`provider`, `name`, `url`, `apiKey`, `isActive` — Task 4's `build_payload()`). If wrong, the fix is entirely inside `build_payload()` in `pylib/omniroute.py`; `pylib/omniroute.py`'s tests you write in Task 4 already assert this literal shape, so a live-API mismatch will show up as an HTTP 4xx from `provision()`, not a silent bad write.
- Whether updating an existing connection uses `PUT /api/providers/{id}` (used in Task 4 — more consistent with typical REST update semantics and with the endpoint list this research surfaced) rather than `PATCH` as an earlier draft of this integration assumed.
- The exact `model` prefix OmniRoute derives for a connection named `llm-env-local` — Task 7 assumes it is the connection's own `name` (`llm-env-local/<alias>`). If the live dashboard shows a different generated prefix (e.g. a slugified id), fix the one `model` field construction in `scripts/check-server.sh`'s OmniRoute section.
- `GET /api/providers`' top-level response shape — Task 4's `_extract_providers()` defensively accepts a bare JSON array or `{"providers": [...]}` / `{"data": [...]}`, so this is handled either way without a code change.

## Task 1: Config schema — `omniroute:` and `resources.omniroute:` sections

**Files:**
- Modify: `pylib/config.py` (`migrate_config()`, `validate_config()`)
- Modify: `models.yml.example`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: after `migrate_config()`, every config has `cfg["omniroute"] = {"image": str, "port": int, "cli_token": str, "initial_password": str}` and `cfg["resources"]["omniroute"] = {"cpus": int, "memory_mib": int}`. Later tasks (`pylib/compose.py`, `pylib/omniroute.py`, `scripts/start.sh`) read these keys.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_config.py` (the file already imports `migrate_config` indirectly via `config_module`; add an explicit import):

```python
from pylib.config import migrate_config
```

(place this next to the existing `from pylib.config import (...)` block at the top of the file, adding `migrate_config` to that import list rather than a second import line)

```python
def test_migrate_config_adds_default_omniroute_section():
    cfg = make_cfg()
    del cfg["omniroute"]
    migrated = migrate_config(cfg)
    assert migrated["omniroute"] == {
        "image": "docker.io/diegosouzapw/omniroute:latest",
        "port": 20128,
        "cli_token": "",
        "initial_password": "",
    }


def test_migrate_config_preserves_existing_omniroute_values():
    cfg = make_cfg(
        omniroute={
            "image": "docker.io/diegosouzapw/omniroute:latest",
            "port": 21000,
            "cli_token": "existing-token",
            "initial_password": "existing-password",
        }
    )
    migrated = migrate_config(cfg)
    assert migrated["omniroute"]["port"] == 21000
    assert migrated["omniroute"]["cli_token"] == "existing-token"


def test_migrate_config_adds_default_resources_omniroute_section():
    cfg = make_cfg()
    del cfg["resources"]["omniroute"]
    migrated = migrate_config(cfg)
    assert migrated["resources"]["omniroute"] == {"cpus": 1, "memory_mib": 1024}


def test_config_accepts_valid_omniroute_section():
    cfg = make_cfg()
    assert validate_config(cfg) == []


def test_config_without_omniroute_key_has_no_errors():
    cfg = make_cfg()
    del cfg["omniroute"]
    assert validate_config(cfg) == []


def test_config_rejects_non_mapping_omniroute_section():
    cfg = make_cfg(omniroute=[])
    errors = validate_config(cfg)
    assert any(error == "section omniroute must be a mapping" for error in errors)


@pytest.mark.parametrize(
    "field,value,expected_error",
    [
        ("image", "", "omniroute.image must be a non-empty string"),
        ("image", 5, "omniroute.image must be a non-empty string"),
        ("port", 0, "omniroute.port must be a positive integer"),
        ("port", "20128", "omniroute.port must be a positive integer"),
        ("cli_token", 5, "omniroute.cli_token must be a string"),
        ("initial_password", 5, "omniroute.initial_password must be a string"),
    ],
)
def test_config_rejects_invalid_omniroute_values(field, value, expected_error):
    omniroute = {
        "image": "docker.io/diegosouzapw/omniroute:latest",
        "port": 20128,
        "cli_token": "",
        "initial_password": "",
    }
    omniroute[field] = value
    cfg = make_cfg(omniroute=omniroute)
    errors = validate_config(cfg)
    assert expected_error in errors


@pytest.mark.parametrize(
    "omniroute_resources",
    [
        {"cpus": -1, "memory_mib": 1024},
        {"cpus": True, "memory_mib": 1024},
        {"cpus": 1, "memory_mib": -1},
        {"cpus": 1, "memory_mib": 0.5},
    ],
)
def test_config_rejects_invalid_resources_omniroute_values(omniroute_resources):
    cfg = make_cfg(
        resources={
            "llm_server": {"cpus": 0, "memory_mib": 0},
            "omniroute": omniroute_resources,
        }
    )
    errors = validate_config(cfg)
    assert any("resources.omniroute" in error for error in errors)
```

Also update `make_cfg()`'s base fixture (near the existing `"resources": {"llm_server": {"cpus": 0, "memory_mib": 0}}` block) to always include both new sections, exactly like `resources.llm_server` is already baked in — otherwise `test_save_then_load_roundtrip` and similar full-config tests will fail the same way the `resources` key addition did in the prior plan:

```python
        "resources": {
            "llm_server": {"cpus": 0, "memory_mib": 0},
            "omniroute": {"cpus": 1, "memory_mib": 1024},
        },
        "omniroute": {
            "image": "docker.io/diegosouzapw/omniroute:latest",
            "port": 20128,
            "cli_token": "test-cli-token",
            "initial_password": "test-password",
        },
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_config.py -k "omniroute" -v`
Expected: FAIL — `KeyError: 'omniroute'` or assertion mismatches, since `migrate_config`/`validate_config` don't know about this section yet.

- [ ] **Step 3: Implement the config schema changes**

In `pylib/config.py`, add two module-level constants near the top (after `SAMPLING_FIELDS`):

```python
DEFAULT_OMNIROUTE_IMAGE = "docker.io/diegosouzapw/omniroute:latest"
DEFAULT_OMNIROUTE_PORT = 20128
```

In `migrate_config()`, extend the existing `resources = cfg.setdefault("resources", {})` block and add a new `omniroute` block right after it (still before the `gpu = cfg.get("gpu")` early-return check):

```python
    resources = cfg.setdefault("resources", {})
    if isinstance(resources, dict):
        llm_server_resources = resources.setdefault("llm_server", {})
        if isinstance(llm_server_resources, dict):
            llm_server_resources.setdefault("cpus", 0)
            llm_server_resources.setdefault("memory_mib", 0)
        omniroute_resources = resources.setdefault("omniroute", {})
        if isinstance(omniroute_resources, dict):
            omniroute_resources.setdefault("cpus", 1)
            omniroute_resources.setdefault("memory_mib", 1024)

    omniroute = cfg.setdefault("omniroute", {})
    if isinstance(omniroute, dict):
        omniroute.setdefault("image", DEFAULT_OMNIROUTE_IMAGE)
        omniroute.setdefault("port", DEFAULT_OMNIROUTE_PORT)
        omniroute.setdefault("cli_token", "")
        omniroute.setdefault("initial_password", "")
```

In `validate_config()`, extend the existing `if "resources" in cfg:` block. The real code (`pylib/config.py:168-189`) currently reads:

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

Add the new `omniroute_resources` block immediately after the `memory_mib` check above, at the **same 12-space indent as `llm_server_resources = resources.get("llm_server", {})`** — i.e. as a sibling of that line inside the outer `else:`, *not* nested inside the inner `else:` of the `llm_server_resources` dict-type check (the block that starts with `cpus = llm_server_resources.get(...)`). Getting this wrong means `resources.omniroute` is only validated when `resources.llm_server` also happens to be a valid mapping:

```python
            omniroute_resources = resources.get("omniroute", {})
            if not isinstance(omniroute_resources, dict):
                errors.append("resources.omniroute must be a mapping")
            else:
                o_cpus = omniroute_resources.get("cpus", 0)
                if isinstance(o_cpus, bool) or not (
                    isinstance(o_cpus, (int, float)) and o_cpus >= 0
                ):
                    errors.append(
                        "resources.omniroute.cpus must be a non-negative number"
                    )
                o_memory_mib = omniroute_resources.get("memory_mib", 0)
                if not (o_memory_mib == 0 or _positive_int(o_memory_mib)):
                    errors.append(
                        "resources.omniroute.memory_mib must be zero or a "
                        "positive integer"
                    )
```

Add a new top-level block right after the `resources` block (still before `models = cfg["models"]`):

```python
    if "omniroute" in cfg:
        omniroute = cfg["omniroute"]
        if not isinstance(omniroute, dict):
            errors.append("section omniroute must be a mapping")
        else:
            image = omniroute.get("image")
            if not (isinstance(image, str) and image.strip()):
                errors.append("omniroute.image must be a non-empty string")
            if not _positive_int(omniroute.get("port")):
                errors.append("omniroute.port must be a positive integer")
            for key in ("cli_token", "initial_password"):
                if not isinstance(omniroute.get(key, ""), str):
                    errors.append(f"omniroute.{key} must be a string")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS (all tests, including the new ones and the pre-existing full-suite ones like `test_save_then_load_roundtrip`)

- [ ] **Step 5: Update `models.yml.example`**

Add a new `omniroute:` section right after the existing `resources:` section, and add `omniroute:` under `resources:`:

```yaml
resources:
  llm_server:
    cpus: 0
    memory_mib: 0
  omniroute:
    cpus: 1
    memory_mib: 1024

omniroute:
  image: docker.io/diegosouzapw/omniroute:latest
  port: 20128
  cli_token: ""
  initial_password: ""
```

- [ ] **Step 6: Run the full test suite**

Run: `uv run pytest -q`
Expected: PASS, no regressions.

- [ ] **Step 7: Commit**

```bash
git add pylib/config.py models.yml.example tests/test_config.py
git commit -m "feat(config): add omniroute and resources.omniroute schema sections"
```

## Task 2: Resource limits — OmniRoute's fixed reservation

**Files:**
- Modify: `pylib/resources.py`
- Modify: `setup/setup.sh` (Step 8/8)
- Test: `tests/test_resources.py`, `tests/test_shell.py`

**Interfaces:**
- Consumes: nothing new (still `host_cpu_count: int, host_memory_total_mib: int`).
- Produces: `compute_resource_limits()` now also returns `result["omniroute"] = {"cpus": OMNIROUTE_CPU_FIXED, "memory_mib": OMNIROUTE_MEMORY_FIXED_MIB}`, and `result["llm_server"]` is reduced by that fixed amount. `llmenv.py resources`'s output (`cmd_resources` already does `emit({"host": host, **limits})` — no code change needed there, it just forwards whatever `compute_resource_limits()` returns) gains the same `omniroute` key automatically.

- [ ] **Step 1: Write the failing tests**

Replace the existing assertions in `tests/test_resources.py` that assume `llm_server` gets everything after the host floor — OmniRoute's fixed 1 CPU / 1024 MiB now comes out of that remainder too:

```python
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pylib.resources import (
    HOST_CPU_FLOOR,
    HOST_MEMORY_FLOOR_MIB,
    OMNIROUTE_CPU_FIXED,
    OMNIROUTE_MEMORY_FIXED_MIB,
    ResourceError,
    compute_resource_limits,
)


def test_llm_server_gets_remainder_after_host_floor_and_omniroute():
    result = compute_resource_limits(host_cpu_count=8, host_memory_total_mib=32768)
    assert result["host_cpu_floor"] == HOST_CPU_FLOOR
    assert result["host_memory_floor_mib"] == HOST_MEMORY_FLOOR_MIB
    assert result["omniroute"] == {
        "cpus": OMNIROUTE_CPU_FIXED,
        "memory_mib": OMNIROUTE_MEMORY_FIXED_MIB,
    }
    assert result["llm_server"]["cpus"] == 8 - HOST_CPU_FLOOR - OMNIROUTE_CPU_FIXED
    assert (
        result["llm_server"]["memory_mib"]
        == 32768 - HOST_MEMORY_FLOOR_MIB - OMNIROUTE_MEMORY_FIXED_MIB
    )


def test_insufficient_cpu_raises():
    with pytest.raises(ResourceError):
        compute_resource_limits(
            host_cpu_count=HOST_CPU_FLOOR + OMNIROUTE_CPU_FIXED,
            host_memory_total_mib=32768,
        )


def test_insufficient_memory_raises():
    with pytest.raises(ResourceError):
        compute_resource_limits(
            host_cpu_count=8,
            host_memory_total_mib=HOST_MEMORY_FLOOR_MIB + OMNIROUTE_MEMORY_FIXED_MIB,
        )


def test_exact_floor_plus_one_is_feasible():
    result = compute_resource_limits(
        host_cpu_count=HOST_CPU_FLOOR + OMNIROUTE_CPU_FIXED + 1,
        host_memory_total_mib=HOST_MEMORY_FLOOR_MIB + OMNIROUTE_MEMORY_FIXED_MIB + 1,
    )
    assert result["llm_server"]["cpus"] == 1
    assert result["llm_server"]["memory_mib"] == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_resources.py -v`
Expected: FAIL — `ImportError: cannot import name 'OMNIROUTE_CPU_FIXED'`.

- [ ] **Step 3: Implement the fixed reservation**

Replace `pylib/resources.py` in full:

```python
"""Host CPU/RAM budgeting for the compose container stack.

Mirrors pylib/budget.py's shape for VRAM: fixed reservations for the host
and for OmniRoute's own process, then whatever is left goes to llm-server.
cpus is a whole CPU-core count usable directly as compose's `cpus:` service
key; memory_mib is a whole-MiB integer usable as `mem_limit: <n>m`.
"""

from __future__ import annotations

from typing import Any

# Fixed floor reserved for the host OS and other applications.
HOST_CPU_FLOOR = 2
HOST_MEMORY_FLOOR_MIB = 4096

# OmniRoute is a lightweight Node/Next.js process, not the workload driving
# resource needs here — it gets a flat cap, not a share of the remainder.
OMNIROUTE_CPU_FIXED = 1
OMNIROUTE_MEMORY_FIXED_MIB = 1024


class ResourceError(Exception):
    """Raised when the host has too few resources to reserve the fixed floors."""


def compute_resource_limits(
    host_cpu_count: int, host_memory_total_mib: int
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
    return {
        "host_cpu_floor": HOST_CPU_FLOOR,
        "host_memory_floor_mib": HOST_MEMORY_FLOOR_MIB,
        "omniroute": {
            "cpus": OMNIROUTE_CPU_FIXED,
            "memory_mib": OMNIROUTE_MEMORY_FIXED_MIB,
        },
        "llm_server": {
            "cpus": host_cpu_count - cpu_floor,
            "memory_mib": host_memory_total_mib - memory_floor_mib,
        },
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_resources.py -v`
Expected: PASS

- [ ] **Step 5: Write the failing shell test**

In `tests/test_shell.py`, find `test_setup_writes_computed_resource_limits` (added by Plan A) and the `run_setup_with_numbered_selection()` fixture's `uv` stub `*' resources')` case, which currently prints `{"llm_server": {"cpus": 6, "memory_mib": 28672}}`. Update that stub line to also include `omniroute`:

```python
'{"llm_server": {"cpus": 5, "memory_mib": 27648}, "omniroute": {"cpus": 1, "memory_mib": 1024}}'
```

This changes what the stub reports for `llm_server`, so it also invalidates the two hard-coded assertions in the pre-existing `test_setup_writes_computed_resource_limits` (`tests/test_shell.py:553-558`), which still expect the old `"6"` / `"28672"` values. Update that test in the same step so it stays in sync with the new stub:

```python
def test_setup_writes_computed_resource_limits(tmp_path: pathlib.Path) -> None:
    """Setup must persist llmenv resources output into resources.llm_server."""
    _, _, config = run_setup_with_numbered_selection(tmp_path, "1\n1,2\n2\n")

    assert yq_value(config, ".resources.llm_server.cpus") == "5"
    assert yq_value(config, ".resources.llm_server.memory_mib") == "27648"
```

Check the top of `tests/test_shell.py` for an existing `import yaml` — there is none yet — and add one to the import block (next to `import subprocess`):

```python
import yaml
```

Add a new test in `tests/test_shell.py` (near `test_setup_writes_computed_resource_limits`). `run_setup_with_numbered_selection(tmp_path, selection, *, config_text=None, real_models_select=False)` takes one positional `selection` string fed as stdin (GPU choice, model choice, Vulkan device choice, newline-separated — see `test_setup_writes_computed_resource_limits` above for the same `"1\n1,2\n2\n"` selection) and returns `(result, calls, config)`:

```python
def test_setup_writes_computed_omniroute_resource_limits(tmp_path: pathlib.Path) -> None:
    result, _, config = run_setup_with_numbered_selection(tmp_path, "1\n1,2\n2\n")
    assert result.returncode == 0, result.stdout + result.stderr
    cfg = yaml.safe_load(config.read_text())
    assert cfg["resources"]["omniroute"] == {"cpus": 1, "memory_mib": 1024}
```

- [ ] **Step 6: Run the shell test to verify it fails**

Run: `uv run pytest tests/test_shell.py -k omniroute_resource -v`
Expected: FAIL — `setup.sh` doesn't write `resources.omniroute` yet.

- [ ] **Step 7: Wire it into `setup/setup.sh`**

In `setup/setup.sh`'s Step 8/8 block, extend the `if llmenv resources > "$resources_json"; then` branch to also read and write the `omniroute` allocation. Keep any pre-existing explanatory comment above the `trap 'rm -f "$budget_json" "$resources_json"' EXIT` line as-is — the snippet below omits it only for brevity, not to signal removal:

```bash
log_step "Step 8/8  Computing resource limits"
resources_json="$(mktemp)"
trap 'rm -f "$budget_json" "$resources_json"' EXIT
if llmenv resources > "$resources_json"; then
    cpus="$(jq -r '.llm_server.cpus' "$resources_json")"
    memory_mib="$(jq -r '.llm_server.memory_mib' "$resources_json")"
    omniroute_cpus="$(jq -r '.omniroute.cpus' "$resources_json")"
    omniroute_memory_mib="$(jq -r '.omniroute.memory_mib' "$resources_json")"
    CPUS="$cpus" MEMORY_MIB="$memory_mib" \
      OMNIROUTE_CPUS="$omniroute_cpus" OMNIROUTE_MEMORY_MIB="$omniroute_memory_mib" \
      yq -i '
        .resources.llm_server.cpus = (strenv(CPUS) | tonumber) |
        .resources.llm_server.memory_mib = (strenv(MEMORY_MIB) | tonumber) |
        .resources.omniroute.cpus = (strenv(OMNIROUTE_CPUS) | tonumber) |
        .resources.omniroute.memory_mib = (strenv(OMNIROUTE_MEMORY_MIB) | tonumber)
      ' "$CONFIG_PATH"
    log_info "reserved ${cpus} CPUs, ${memory_mib} MiB RAM for llm-server"
    log_info "reserved ${omniroute_cpus} CPUs, ${omniroute_memory_mib} MiB RAM for omniroute"
else
    log_warn "$(jq -r '.error' "$resources_json")"
    log_warn "leaving resources.llm_server/omniroute uncapped (0 = no explicit limit)"
fi
```

- [ ] **Step 8: Run the shell tests to verify they pass**

Run: `shellcheck -s bash setup/setup.sh && uv run pytest tests/test_shell.py -v`
Expected: shellcheck clean; all `test_shell.py` tests PASS.

- [ ] **Step 9: Run the full test suite**

Run: `uv run pytest -q`
Expected: PASS

- [ ] **Step 10: Commit**

```bash
git add pylib/resources.py setup/setup.sh tests/test_resources.py tests/test_shell.py
git commit -m "feat(resources): reserve a fixed 1 CPU / 1024 MiB for omniroute"
```

## Task 3: Compose renderer — the `omniroute` service

**Files:**
- Modify: `pylib/compose.py`
- Test: `tests/test_compose.py`

**Interfaces:**
- Consumes: `cfg["omniroute"]` and `cfg["resources"]["omniroute"]` from Task 1.
- Produces: `render_compose()`'s output document now has `document["services"]["omniroute"]` and a top-level `document["volumes"]["omniroute-data"]`. `write_compose()`'s signature is unchanged.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_compose.py`. First extend the module-level `CFG` fixture to include an `omniroute` section:

```python
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
        "omniroute": {"cpus": 0, "memory_mib": 0},
    },
    "omniroute": {
        "image": "docker.io/diegosouzapw/omniroute:latest",
        "port": 20128,
        "cli_token": "test-cli-token",
        "initial_password": "test-password",
    },
}
```

Then add the new tests:

```python
def test_omniroute_service_uses_configured_image_and_port():
    _, document = compose_dict()
    omniroute = document["services"]["omniroute"]
    assert omniroute["image"] == "docker.io/diegosouzapw/omniroute:latest"
    assert omniroute["ports"] == ["20128:20128"]


def test_omniroute_service_mounts_a_named_data_volume():
    _, document = compose_dict()
    assert document["services"]["omniroute"]["volumes"] == ["omniroute-data:/app/data"]
    assert document["volumes"] == {"omniroute-data": {}}


def test_omniroute_service_sets_its_environment():
    _, document = compose_dict()
    env = document["services"]["omniroute"]["environment"]
    assert env["PORT"] == "20128"
    assert env["OMNIROUTE_CLI_TOKEN"] == "test-cli-token"
    assert env["OMNIROUTE_ALLOW_PRIVATE_PROVIDER_URLS"] == "true"
    assert env["INITIAL_PASSWORD"] == "test-password"


def test_omniroute_service_healthcheck_runs_the_bundled_script():
    _, document = compose_dict()
    healthcheck = document["services"]["omniroute"]["healthcheck"]
    assert healthcheck["test"] == ["CMD", "node", "healthcheck.mjs"]


def test_omniroute_service_depends_on_a_healthy_llm_server():
    _, document = compose_dict()
    assert document["services"]["omniroute"]["depends_on"] == {
        "llm-server": {"condition": "service_healthy"}
    }


def test_omniroute_service_stop_grace_period_and_restart_policy():
    _, document = compose_dict()
    omniroute = document["services"]["omniroute"]
    assert omniroute["stop_grace_period"] == "40s"
    assert omniroute["restart"] == "unless-stopped"


def test_omniroute_zero_resource_limits_are_omitted():
    _, document = compose_dict()
    omniroute = document["services"]["omniroute"]
    assert "cpus" not in omniroute
    assert "mem_limit" not in omniroute


def test_omniroute_nonzero_resource_limits_are_applied():
    cfg = {**CFG, "resources": {**CFG["resources"], "omniroute": {"cpus": 1, "memory_mib": 1024}}}
    _, document = compose_dict(cfg)
    omniroute = document["services"]["omniroute"]
    assert omniroute["cpus"] == 1
    assert omniroute["mem_limit"] == "1024m"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_compose.py -k omniroute -v`
Expected: FAIL — `KeyError: 'omniroute'`, since `render_compose()` doesn't build this service yet.

- [ ] **Step 3: Implement the omniroute service**

In `pylib/compose.py`, extend `render_compose()`. Add the new lookups near the top (after `llm_server_resources`) and the new service dict + document assembly at the end:

```python
def render_compose(cfg: dict[str, Any], *, models_dir: str, presets_path: str) -> str:
    server = cfg["server"]
    gpu = cfg["gpu"]
    runtime = cfg["runtime"]
    llm_server_resources = cfg.get("resources", {}).get("llm_server", {})
    omniroute_cfg = cfg.get("omniroute", {})
    omniroute_resources = cfg.get("resources", {}).get("omniroute", {})

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
            "LLAMA_ARG_MODELS_MAX": str(runtime["models_max"]),
            "LLAMA_ARG_HOST": server["host"],
            "LLAMA_ARG_PORT": str(server["port"]),
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

    omniroute_port = omniroute_cfg.get("port", 20128)
    omniroute_service: dict[str, Any] = {
        "image": omniroute_cfg.get("image", "docker.io/diegosouzapw/omniroute:latest"),
        "container_name": "omniroute",
        "ports": [f"{omniroute_port}:{omniroute_port}"],
        "volumes": ["omniroute-data:/app/data"],
        "environment": {
            "PORT": str(omniroute_port),
            "OMNIROUTE_CLI_TOKEN": omniroute_cfg.get("cli_token", ""),
            "OMNIROUTE_ALLOW_PRIVATE_PROVIDER_URLS": "true",
            "INITIAL_PASSWORD": omniroute_cfg.get("initial_password", ""),
        },
        "healthcheck": {
            "test": ["CMD", "node", "healthcheck.mjs"],
            "interval": "30s",
            "timeout": "5s",
            "retries": 3,
            "start_period": "15s",
        },
        "stop_grace_period": "40s",
        "depends_on": {"llm-server": {"condition": "service_healthy"}},
        "restart": "unless-stopped",
    }
    omniroute_cpus = omniroute_resources.get("cpus")
    if omniroute_cpus:
        omniroute_service["cpus"] = omniroute_cpus
    omniroute_memory_mib = omniroute_resources.get("memory_mib")
    if omniroute_memory_mib:
        omniroute_service["mem_limit"] = f"{omniroute_memory_mib}m"

    document = {
        "services": {"llm-server": service, "omniroute": omniroute_service},
        "volumes": {"omniroute-data": {}},
    }
    return HEADER_COMMENT + yaml.safe_dump(
        document, sort_keys=False, default_flow_style=False
    )
```

This is the complete replacement for the function body — the `service` dict is unchanged from today's implementation, shown in full above for engineers reading tasks out of order.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_compose.py -v`
Expected: PASS (all tests, including the pre-existing `llm-server`-only ones — they only assert on `document["services"]["llm-server"]`, so adding a sibling `omniroute` key doesn't break them)

- [ ] **Step 5: Run the full test suite and validate**

Run: `uv run pytest -q && uv run ruff check llmenv.py pylib tests`
Expected: PASS, no lint issues.

- [ ] **Step 6: Commit**

```bash
git add pylib/compose.py tests/test_compose.py
git commit -m "feat(compose): render the omniroute service alongside llm-server"
```

## Task 4: `pylib/omniroute.py` — idempotent provider-connection provisioning

**Files:**
- Create: `pylib/omniroute.py`
- Test: `tests/test_omniroute.py`

**Interfaces:**
- Consumes: nothing from earlier tasks (pure request/response logic).
- Produces: `OmniRouteError` (exception), `CONNECTION_NAME = "llm-env-local"` (constant), `build_payload(port: int, api_key: str) -> dict[str, Any]`, `find_connection(connections: list[dict], name: str = CONNECTION_NAME) -> dict | None`, `provision(base_url: str, cli_token: str, port: int, api_key: str) -> dict[str, Any]` (returns `{"action": "created" | "updated", "id": <str-or-None>}`). Task 5 (`llmenv.py`) imports `OmniRouteError` and `provision`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_omniroute.py`:

```python
import json
import sys
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pylib.omniroute as omniroute_module
from pylib.omniroute import (
    CONNECTION_NAME,
    OmniRouteError,
    build_payload,
    find_connection,
    provision,
)


class FakeResponse:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


def test_build_payload_uses_the_real_router_api_key():
    payload = build_payload(port=8000, api_key="secret-key")
    assert payload == {
        "provider": "llama-cpp",
        "name": CONNECTION_NAME,
        "url": "http://llm-server:8000/v1",
        "apiKey": "secret-key",
        "isActive": True,
    }


def test_find_connection_matches_by_name():
    connections = [{"id": "1", "name": "other"}, {"id": "2", "name": CONNECTION_NAME}]
    assert find_connection(connections)["id"] == "2"


def test_find_connection_returns_none_when_absent():
    assert find_connection([{"id": "1", "name": "other"}]) is None


def test_provision_creates_when_no_existing_connection(monkeypatch):
    calls = []

    def fake_urlopen(request, timeout):
        calls.append((request.get_method(), request.full_url, request.data))
        if request.get_method() == "GET":
            return FakeResponse([])
        return FakeResponse({"id": "new-id"})

    monkeypatch.setattr(omniroute_module.urllib.request, "urlopen", fake_urlopen)
    result = provision("http://127.0.0.1:20128", "cli-token", 8000, "api-key")
    assert result == {"action": "created", "id": "new-id"}
    assert calls[0][0] == "GET"
    assert calls[0][1] == "http://127.0.0.1:20128/api/providers"
    assert calls[1][0] == "POST"
    assert calls[1][1] == "http://127.0.0.1:20128/api/providers"
    posted = json.loads(calls[1][2])
    assert posted["url"] == "http://llm-server:8000/v1"
    assert posted["apiKey"] == "api-key"


def test_provision_updates_when_existing_connection(monkeypatch):
    def fake_urlopen(request, timeout):
        if request.get_method() == "GET":
            return FakeResponse([{"id": "existing-id", "name": CONNECTION_NAME}])
        assert request.get_method() == "PUT"
        assert request.full_url == "http://127.0.0.1:20128/api/providers/existing-id"
        return FakeResponse({"id": "existing-id"})

    monkeypatch.setattr(omniroute_module.urllib.request, "urlopen", fake_urlopen)
    result = provision("http://127.0.0.1:20128", "cli-token", 8000, "api-key")
    assert result == {"action": "updated", "id": "existing-id"}


def test_provision_accepts_a_providers_wrapped_listing(monkeypatch):
    def fake_urlopen(request, timeout):
        if request.get_method() == "GET":
            return FakeResponse({"providers": [{"id": "existing-id", "name": CONNECTION_NAME}]})
        return FakeResponse({"id": "existing-id"})

    monkeypatch.setattr(omniroute_module.urllib.request, "urlopen", fake_urlopen)
    result = provision("http://127.0.0.1:20128", "cli-token", 8000, "api-key")
    assert result == {"action": "updated", "id": "existing-id"}


def test_provision_sends_the_cli_token_header(monkeypatch):
    seen_headers = []

    def fake_urlopen(request, timeout):
        seen_headers.append(request.get_header("X-omniroute-cli-token"))
        if request.get_method() == "GET":
            return FakeResponse([])
        return FakeResponse({"id": "x"})

    monkeypatch.setattr(omniroute_module.urllib.request, "urlopen", fake_urlopen)
    provision("http://127.0.0.1:20128", "secret-token", 8000, "api-key")
    assert all(header == "secret-token" for header in seen_headers)


def test_provision_raises_omniroute_error_on_http_failure(monkeypatch):
    def fake_urlopen(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 500, "boom", None, None)

    monkeypatch.setattr(omniroute_module.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(OmniRouteError):
        provision("http://127.0.0.1:20128", "cli-token", 8000, "api-key")


def test_provision_raises_on_unexpected_listing_shape(monkeypatch):
    def fake_urlopen(request, timeout):
        return FakeResponse({"unexpected": "shape"})

    monkeypatch.setattr(omniroute_module.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(OmniRouteError):
        provision("http://127.0.0.1:20128", "cli-token", 8000, "api-key")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_omniroute.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pylib.omniroute'`.

- [ ] **Step 3: Implement `pylib/omniroute.py`**

```python
"""Idempotent provisioning of OmniRoute's connection to the local router.

Talks to OmniRoute's admin API using its machine-auth `x-omniroute-cli-token`
header. See this plan's "Verify During Implementation" section for what is
confirmed vs. still assumed about this API's exact shape. Uses only the
standard library so this repo's one Python dependency (pyyaml) does not grow.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

CONNECTION_NAME = "llm-env-local"


class OmniRouteError(Exception):
    """Raised when OmniRoute's admin API cannot be reached or misbehaves."""


def _request(
    method: str, url: str, cli_token: str, payload: dict[str, Any] | None = None
) -> Any:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("x-omniroute-cli-token", cli_token)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            body = response.read()
    except urllib.error.HTTPError as exc:
        raise OmniRouteError(f"{method} {url} failed: HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise OmniRouteError(f"{method} {url} failed: {exc.reason}") from exc
    return json.loads(body) if body else None


def _extract_providers(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("providers", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
    raise OmniRouteError(f"unexpected provider listing shape: {payload!r}")


def build_payload(port: int, api_key: str) -> dict[str, Any]:
    return {
        "provider": "llama-cpp",
        "name": CONNECTION_NAME,
        "url": f"http://llm-server:{port}/v1",
        "apiKey": api_key,
        "isActive": True,
    }


def find_connection(
    connections: list[dict[str, Any]], name: str = CONNECTION_NAME
) -> dict[str, Any] | None:
    return next((c for c in connections if c.get("name") == name), None)


def provision(base_url: str, cli_token: str, port: int, api_key: str) -> dict[str, Any]:
    listing = _request("GET", f"{base_url}/api/providers", cli_token)
    providers = _extract_providers(listing)
    payload = build_payload(port, api_key)

    existing = find_connection(providers)
    if existing is None:
        created = _request("POST", f"{base_url}/api/providers", cli_token, payload)
        created_id = created.get("id") if isinstance(created, dict) else None
        return {"action": "created", "id": created_id}

    provider_id = existing.get("id")
    _request("PUT", f"{base_url}/api/providers/{provider_id}", cli_token, payload)
    return {"action": "updated", "id": provider_id}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_omniroute.py -v`
Expected: PASS

- [ ] **Step 5: Run the full test suite and validate**

Run: `uv run pytest -q && uv run ruff check llmenv.py pylib tests`
Expected: PASS, no lint issues.

- [ ] **Step 6: Commit**

```bash
git add pylib/omniroute.py tests/test_omniroute.py
git commit -m "feat(omniroute): add idempotent provider-connection provisioning"
```

## Task 5: `llmenv.py omniroute provision` subcommand

**Files:**
- Modify: `llmenv.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `pylib.omniroute.OmniRouteError`, `pylib.omniroute.provision` (Task 4); `cfg["omniroute"]["port"]`, `cfg["omniroute"]["cli_token"]`, `cfg["server"]["port"]`, `cfg["server"]["api_key"]` (Task 1).
- Produces: `llmenv --config <path> omniroute provision` — emits `provision()`'s return dict as JSON on success (exit 0), or `{"error": "..."}` (exit 1) if `omniroute.cli_token`/`omniroute.port` are unset, or if `provision()` raises `OmniRouteError`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_cli.py`, near the top (with the other imports):

```python
import http.server
import threading
```

Add a small recording HTTP handler and two tests, placed near `test_render_compose_writes_a_compose_file`:

```python
class _RecordingProviderHandler(http.server.BaseHTTPRequestHandler):
    received: list[tuple] = []

    def _reply(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.received.append(("GET", self.path, self.headers.get("x-omniroute-cli-token")))
        self._reply([])

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length else {}
        self.received.append(("POST", self.path, body))
        self._reply({"id": "created-id"})

    def log_message(self, format_string, *args):
        pass


def _write_omniroute_test_config(tmp_path: Path, *, omniroute_port: int, cli_token: str) -> Path:
    config = tmp_path / "models.yml"
    config.write_text(
        "version: 1\n"
        "server: {host: 0.0.0.0, port: 8000, api_key: routerkey, mdns_name: llm,"
        " sleep_idle_seconds: 300}\n"
        "gpu: {pci_address: '0000:03:00.0', device_name: d, backend: vulkan,"
        " image: i, vram_total_mib: 16304, reserve_mode: auto, reserve_floor_mib: 1024}\n"
        "runtime: {models_max: 1, parallel_slots: 1, ubatch_size: 512,"
        " flash_attn: true, cache_type_k: q8_0, cache_type_v: q8_0}\n"
        f"omniroute: {{image: i, port: {omniroute_port}, cli_token: {cli_token},"
        " initial_password: p}}\n"
        "models:\n"
        "  - {alias: a, label: A, parameters: 1B, quantization: Q4_K_M, enabled: true,"
        " file: a.gguf, url: u, size_bytes: 1, vram_budget: 10%, ctx_size: 4096,"
        " client_max_output_tokens: 4096, n_gpu_layers: 99}\n"
    )
    return config


def test_omniroute_provision_creates_a_connection_via_the_admin_api(tmp_path):
    _RecordingProviderHandler.received = []
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _RecordingProviderHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        config = _write_omniroute_test_config(
            tmp_path, omniroute_port=server.server_address[1], cli_token="secret-token"
        )
        result = run("--config", str(config), "omniroute", "provision")
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == {"action": "created", "id": "created-id"}
        assert _RecordingProviderHandler.received[0] == ("GET", "/api/providers", "secret-token")
        method, path, body = _RecordingProviderHandler.received[1]
        assert method == "POST"
        assert path == "/api/providers"
        assert body["url"] == "http://llm-server:8000/v1"
        assert body["apiKey"] == "routerkey"
    finally:
        server.shutdown()
        thread.join()


def test_omniroute_provision_fails_cleanly_without_a_cli_token(tmp_path):
    config = _write_omniroute_test_config(tmp_path, omniroute_port=20128, cli_token='""')
    result = run("--config", str(config), "omniroute", "provision")
    assert result.returncode == 1
    assert "cli_token" in json.loads(result.stdout)["error"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -k omniroute -v`
Expected: FAIL — `argument command: invalid choice: 'omniroute'` (the subcommand doesn't exist yet).

- [ ] **Step 3: Wire the subcommand into `llmenv.py`**

Add the import (extend the existing `pylib.resources` import line's neighborhood — add a new line right after it):

```python
from pylib.omniroute import OmniRouteError, provision
```

Add the command function (near `cmd_resources`, after it):

```python
def cmd_omniroute(args: argparse.Namespace) -> int:
    cfg = require_valid_config(load_config(Path(args.config)))
    omniroute_cfg = cfg.get("omniroute") or {}
    cli_token = omniroute_cfg.get("cli_token")
    port = omniroute_cfg.get("port")
    if not cli_token or not port:
        return fail(
            "omniroute.cli_token and omniroute.port must be set; run 'make start' "
            "after 'make setup' to generate them"
        )
    base_url = f"http://127.0.0.1:{port}"
    result = provision(base_url, cli_token, cfg["server"]["port"], cfg["server"]["api_key"])
    return emit(result)
```

Add the parser (near the `resources` parser, after it, in `build_parser()`):

```python
    omniroute_parser = sub.add_parser("omniroute")
    omniroute_parser.add_argument("--config", default=argparse.SUPPRESS)
    omniroute_parser.add_argument("action", choices=["provision"])
    omniroute_parser.set_defaults(func=cmd_omniroute)
```

Extend `main()`'s exception tuple to include `OmniRouteError`:

```python
    except (ConfigError, BudgetError, GgufError, DetectError, ResourceError, OmniRouteError) as exc:
        return fail(str(exc))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Run the full test suite and validate**

Run: `uv run pytest -q && uv run ruff check llmenv.py pylib tests`
Expected: PASS, no lint issues.

- [ ] **Step 6: Commit**

```bash
git add llmenv.py tests/test_cli.py
git commit -m "feat(cli): add 'llmenv omniroute provision' subcommand"
```

## Task 6: `tools/lib.sh` secrets + `scripts/start.sh` provisioning call

**Files:**
- Modify: `tools/lib.sh`
- Modify: `scripts/start.sh`
- Test: `tests/test_shell.py`

**Interfaces:**
- Consumes: `llmenv --config "$CONFIG_PATH" omniroute provision` (Task 5); `.omniroute.port`/`.omniroute.cli_token`/`.omniroute.initial_password` config keys (Task 1).
- Produces: `ensure_omniroute_secrets()` (new function in `tools/lib.sh`, exported by being defined in a sourced file — no explicit `export -f` needed since callers `source` this file directly, matching `ensure_api_key`'s existing pattern) and `wait_for_tcp_port()` (new function, same file). `scripts/start.sh` calls both, then calls `llmenv omniroute provision` once OmniRoute is reachable.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_shell.py` (find the existing tests for `ensure_api_key`/`new_api_key` to place these nearby, matching their structure):

```python
def test_ensure_omniroute_secrets_generates_missing_cli_token_and_password(
    tmp_path: pathlib.Path,
) -> None:
    home = tmp_path / "home"
    config_dir = home / ".config" / "llm-env"
    config_dir.mkdir(parents=True)
    config = config_dir / "models.yml"
    config.write_text(
        "version: 1\n"
        "omniroute: {image: i, port: 20128, cli_token: '', initial_password: ''}\n"
    )
    script = tmp_path / "run.sh"
    script.write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\n"
        f"source {ROOT / 'tools/lib.sh'}\n"
        "ensure_omniroute_secrets\n"
    )
    script.chmod(0o755)
    env = {**os.environ, "HOME": str(home), "LLM_ENV_CONFIG": str(config)}
    result = subprocess.run(
        ["bash", str(script)], cwd=ROOT, text=True, capture_output=True, env=env, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr
    cfg = yaml.safe_load(config.read_text())
    assert cfg["omniroute"]["cli_token"]
    assert cfg["omniroute"]["initial_password"]
    assert cfg["omniroute"]["cli_token"] != cfg["omniroute"]["initial_password"]


def test_ensure_omniroute_secrets_preserves_existing_values(tmp_path: pathlib.Path) -> None:
    home = tmp_path / "home"
    config_dir = home / ".config" / "llm-env"
    config_dir.mkdir(parents=True)
    config = config_dir / "models.yml"
    config.write_text(
        "version: 1\n"
        "omniroute: {image: i, port: 20128, cli_token: existing-token,"
        " initial_password: existing-password}\n"
    )
    script = tmp_path / "run.sh"
    script.write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\n"
        f"source {ROOT / 'tools/lib.sh'}\n"
        "ensure_omniroute_secrets\n"
    )
    script.chmod(0o755)
    env = {**os.environ, "HOME": str(home), "LLM_ENV_CONFIG": str(config)}
    result = subprocess.run(
        ["bash", str(script)], cwd=ROOT, text=True, capture_output=True, env=env, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr
    cfg = yaml.safe_load(config.read_text())
    assert cfg["omniroute"]["cli_token"] == "existing-token"
    assert cfg["omniroute"]["initial_password"] == "existing-password"
```

Check the top of `tests/test_shell.py` for existing `import os`, `import subprocess`, `import yaml`, and a `ROOT` constant — reuse them; add any that are missing to the import block.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_shell.py -k ensure_omniroute_secrets -v`
Expected: FAIL — `ensure_omniroute_secrets: command not found`.

- [ ] **Step 3: Implement `ensure_omniroute_secrets()` and `wait_for_tcp_port()` in `tools/lib.sh`**

Add both functions right after the existing `ensure_api_key()` function:

```bash
ensure_omniroute_secrets() {
    local cli_token initial_password
    cli_token="$(yq -r '.omniroute.cli_token' "$CONFIG_PATH")"
    if [ -z "$cli_token" ] || [ "$cli_token" = "null" ]; then
        cli_token="$(new_api_key)"
        chmod 600 "$CONFIG_PATH"
        CLI_TOKEN="$cli_token" yq -i '.omniroute.cli_token = strenv(CLI_TOKEN)' "$CONFIG_PATH"
        log_info "generated an OmniRoute CLI token"
    fi
    initial_password="$(yq -r '.omniroute.initial_password' "$CONFIG_PATH")"
    if [ -z "$initial_password" ] || [ "$initial_password" = "null" ]; then
        initial_password="$(new_api_key)"
        chmod 600 "$CONFIG_PATH"
        INITIAL_PASSWORD="$initial_password" yq -i '.omniroute.initial_password = strenv(INITIAL_PASSWORD)' "$CONFIG_PATH"
        log_info "generated an OmniRoute dashboard password"
    fi
    chmod 600 "$CONFIG_PATH"
}

wait_for_tcp_port() {
    local port="$1" attempt
    for (( attempt = 0; attempt < LLM_ENV_HEALTH_TIMEOUT_SECONDS; attempt++ )); do
        if (exec 3<>"/dev/tcp/127.0.0.1/${port}") 2>/dev/null; then
            exec 3<&- 3>&-
            return 0
        fi
        sleep 1
    done
    return 1
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `shellcheck -s bash tools/lib.sh && uv run pytest tests/test_shell.py -k ensure_omniroute_secrets -v`
Expected: shellcheck clean; both tests PASS.

- [ ] **Step 5: Write the failing test for `scripts/start.sh`'s provisioning call**

`tests/test_shell.py` already has a `run_lifecycle_script(tmp_path, script, ...)` helper (used by the existing `test_start_retains_an_existing_key` and similar tests) that stubs `curl`/`podman`/`systemctl`/`uv`/`yq` so `scripts/start.sh` reaches its health-success branch: the stub `curl` always exits 0 (see `run_lifecycle_script`'s `curl.write_text("#!/usr/bin/bash\nprintf 'curl %s\\n' \"$*\" >> \"$CALLS\"\n")`), so `wait_for_health` succeeds immediately. There is no real OmniRoute listener in this stub environment, so `wait_for_tcp_port` will genuinely time out — use `env_overrides` to keep that fast and assert the resulting warning, which proves the new code path runs and that `start.sh` still exits 0 (a missing OmniRoute must not fail the whole start):

```python
def test_start_warns_when_omniroute_is_unreachable_but_still_succeeds(
    tmp_path: pathlib.Path,
) -> None:
    result, _config, _calls = run_lifecycle_script(
        tmp_path,
        "scripts/start.sh",
        env_overrides={"LLM_ENV_HEALTH_TIMEOUT_SECONDS": "1"},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OmniRoute did not become reachable" in result.stdout + result.stderr
```

- [ ] **Step 6: Run the test to verify it fails**

Run: `uv run pytest tests/test_shell.py -k omniroute_is_unreachable -v`
Expected: FAIL — `start.sh` doesn't call `ensure_omniroute_secrets`/`wait_for_tcp_port` yet, so no such warning is printed.

- [ ] **Step 7: Wire it into `scripts/start.sh`**

Add `ensure_omniroute_secrets` right after the existing `ensure_api_key` call:

```bash
migrate_config_file || die "configuration migration failed"
ensure_api_key
ensure_omniroute_secrets
```

Replace the health-gate block at the bottom of the file:

```bash
log_step "Waiting for health"
# Probe 127.0.0.1 explicitly: "localhost" resolves to ::1 first on this system while
# podman publishes the port on 0.0.0.0 (IPv4), so a localhost probe would never connect.
if wait_for_health "$port"; then
    log_info "server is ready"
    bash "${REPO_DIR}/setup/network.sh"

    log_step "Waiting for OmniRoute"
    omniroute_port="$(yq -r '.omniroute.port' "$CONFIG_PATH")"
    if wait_for_tcp_port "$omniroute_port"; then
        log_info "OmniRoute is ready"
        if llmenv --config "$CONFIG_PATH" omniroute provision >/dev/null; then
            log_info "OmniRoute connection configured"
        else
            log_warn "OmniRoute provisioning failed; configure it manually via the dashboard"
        fi
    else
        log_warn "OmniRoute did not become reachable within ${LLM_ENV_HEALTH_TIMEOUT_SECONDS}s; configure it manually"
    fi
    exit 0
fi

log_error "server did not become healthy within ${LLM_ENV_HEALTH_TIMEOUT_SECONDS}s"
echo "  Logs: podman compose -f ${COMPOSE_FILE} logs"
exit 1
```

- [ ] **Step 8: Run the tests to verify they pass**

Run: `shellcheck -s bash scripts/start.sh && uv run pytest tests/test_shell.py -v`
Expected: shellcheck clean; all `test_shell.py` tests PASS.

- [ ] **Step 9: Run the full test suite**

Run: `uv run pytest -q`
Expected: PASS

- [ ] **Step 10: Commit**

```bash
git add tools/lib.sh scripts/start.sh tests/test_shell.py
git commit -m "feat(start): generate omniroute secrets and provision it after health"
```

## Task 7: `check-server.sh` — OmniRoute functional check

**Files:**
- Modify: `scripts/check-server.sh`
- Test: manual verification only (this script is an integration script exercised against a live stack, not unit-tested — consistent with the rest of `check-server.sh`, which has no `tests/test_shell.py` coverage today either; its correctness is verified by running it, per the End-to-End Verification section below)

**Interfaces:**
- Consumes: `.omniroute.port`/`.omniroute.cli_token` config keys (Task 1); the `request_record`/`request_failed`/`ok`/`bad` helpers already defined earlier in this same file.
- Produces: two new `log_step` sections in `check-server.sh`'s output — "OmniRoute providers" and "OmniRoute completions" — folded into the existing `PASS`/`FAIL` counters.

- [ ] **Step 1: Add the OmniRoute temp-file and header preamble**

In `scripts/check-server.sh`, extend the existing preamble (where `auth_conf`/`bad_conf` are created, before `diagnostic_dir=...`) with an OmniRoute auth-header file and base URL:

```bash
auth_conf="$(mktemp)"
chmod 600 "$auth_conf"
printf 'header = "Authorization: Bearer %s"\n' "$api_key" > "$auth_conf"
bad_conf="$(mktemp)"
chmod 600 "$bad_conf"
printf 'header = "Authorization: Bearer definitely-not-the-key"\n' > "$bad_conf"
omniroute_conf="$(mktemp)"
chmod 600 "$omniroute_conf"
omniroute_port="$(yq -r '.omniroute.port' "$CONFIG_PATH")"
omniroute_cli_token="$(yq -r '.omniroute.cli_token' "$CONFIG_PATH")"
printf 'header = "x-omniroute-cli-token: %s"\n' "$omniroute_cli_token" > "$omniroute_conf"
omniroute_base="http://127.0.0.1:${omniroute_port}"
diagnostic_dir="$(prepare_diagnostic_dir server)"

cleanup() {
    local status=$?
    finish_diagnostic_dir "$diagnostic_dir"
    rm -f "$auth_conf" "$bad_conf" "$omniroute_conf"
    exit "$status"
}
trap cleanup EXIT
```

- [ ] **Step 2: Add the "OmniRoute providers" section**

Add this after the existing "Completions" `while read -r alias; ... done` loop, before the final `echo` / `log_step "Results..."` block:

```bash
log_step "OmniRoute providers"
request_record "omniroute provider listing" \
    "curl --silent --show-error --max-time 10 -H 'x-omniroute-cli-token: <redacted>' ${omniroute_base}/api/providers" \
    "" "HTTP status: 200" -- \
    curl --silent --show-error --max-time 10 -K "$omniroute_conf" "${omniroute_base}/api/providers"
log_block "Expectation" "$REQUEST_EXPECTATION"
if request_failed 200 "omniroute provider listing"; then
    :
else
    connection_parse_stderr="$(mktemp "${diagnostic_dir}/parse.XXXXXX")"
    is_active="$(jq -r '
        (if type == "array" then . else (.providers // .data // []) end)
        | map(select(.name == "llm-env-local"))[0].isActive // false
      ' < "$REQUEST_BODY_FILE" 2>"$connection_parse_stderr")"
    log_nonempty_block "Response parsing stderr" "$(<"$connection_parse_stderr")"
    if [ "$is_active" = "true" ]; then
        ok "Verdict: PASS identity=omniroute provider listing llm-env-local connection is present and active"
    else
        bad "Verdict: FAIL stage=connection lookup reason=llm-env-local connection missing or inactive"
    fi
fi
```

- [ ] **Step 3: Add the "OmniRoute completions" section**

```bash
log_step "OmniRoute completions"
while read -r alias; do
    [ -n "$alias" ] || continue
    body="$(jq -n --arg m "llm-env-local/${alias}" \
        '{model: $m,
          messages: [{role: "user", content: "Reply with exactly: ready"}],
          max_tokens: 256, stream: false}')"

    identity="omniroute completion model=${alias}"
    expectation="normalized assistant content: ready"
    request_record "$identity" \
        "curl --silent --show-error --max-time 120 -H 'Content-Type: application/json' --data-raw '${body}' ${omniroute_base}/v1/chat/completions" \
        "$body" "$expectation" -- \
        curl --silent --show-error --max-time 120 \
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
        bad "Verdict: FAIL stage=${failure_stage} identity=${identity} ${failure_detail}"
        continue
    fi

    ok "Verdict: PASS identity=${identity} ${alias}: returned ready via OmniRoute"
done < <(yq -r '.models[] | select(.enabled) | .alias' "$CONFIG_PATH")
```

- [ ] **Step 4: shellcheck**

Run: `shellcheck -s bash scripts/check-server.sh`
Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add scripts/check-server.sh
git commit -m "feat(check-server): verify the omniroute connection and a completion through it"
```

If the live provider-creation payload shape or the `model` prefix format turn out to differ from what "Verify During Implementation" assumed (discovered when actually running `make check-server` against a live stack), fix `pylib/omniroute.py`'s `build_payload()` (payload shape) or this task's `model` field construction (prefix format) — nothing else needs to change, since both are isolated to those single points.

## Task 8: Documentation

**Files:**
- Modify: `.agents/architecture.md`
- Modify: `AGENTS.md`
- Modify: `README.md`
- Modify: `QUICK_START.md`
- Test: none (docs only; verified by the self-review checklist below and `make validate`'s existing doc-consistency checks in `tests/test_docs.py`, if any apply — this task adds no new `test_docs.py` assertions since it doesn't introduce a stale-reference risk the way removing `QUADLET_DIR` did)

- [ ] **Step 1: Update `.agents/architecture.md`**

Add two new rows to the `## Files` table (after the existing `pylib/resources.py` row):

```markdown
| `pylib/omniroute.py` | Idempotent OmniRoute provider-connection provisioning via its admin API |
```

Extend the `## Container Lifecycle` section (after the existing paragraph about `logs.sh`/`status.sh`) with a new paragraph:

```markdown
The compose file also runs `omniroute`, network-joined to `llm-server` and
gated on its `service_healthy` condition. `scripts/start.sh` calls `llmenv
omniroute provision` once both containers are reachable, which idempotently
creates or updates a provider connection named `llm-env-local` pointing at
`http://llm-server:<port>/v1` with the router's real API key — see
`pylib/omniroute.py`. To inspect what is actually configured in OmniRoute:

```bash
curl -H "x-omniroute-cli-token: $(yq -r '.omniroute.cli_token' ~/.config/llm-env/models.yml)" \
  http://127.0.0.1:20128/api/providers
```
```

Add one line to `## Invariants`:

```markdown
- The `llm-env-local` OmniRoute provider connection is owned by this tool —
  never renamed or deleted by hand, or `make start` will create a duplicate.
```

- [ ] **Step 2: Update `AGENTS.md`**

Update the opening paragraph:

```markdown
Local llama.cpp router server on Bazzite, running as a rootless podman
compose stack with GPU acceleration, fronted by an OmniRoute gateway that
`make start` auto-configures against the local router. Configuration lives
in `models.yml`.
```

- [ ] **Step 3: Update `README.md`**

Extend the "What it does" list with one new bullet (after the mDNS bullet):

```markdown
- Runs an OmniRoute gateway alongside the router and auto-configures its
  connection to the local model on every start — no manual dashboard setup.
```

- [ ] **Step 4: Update `QUICK_START.md`**

Add a short subsection after "Start at boot":

```markdown
## OmniRoute dashboard

`make start` auto-provisions OmniRoute's connection to the local router.
The dashboard itself is at `http://127.0.0.1:20128` (or your configured
`omniroute.port`); the login password is `omniroute.initial_password` in
`~/.config/llm-env/models.yml`.
```

- [ ] **Step 5: Verify and commit**

Run: `uv run pytest -q` (docs changes shouldn't affect tests, but confirm no `test_docs.py` assertions regressed)

```bash
git add .agents/architecture.md AGENTS.md README.md QUICK_START.md
git commit -m "docs: document the omniroute gateway and its provisioning"
```

## Task 9: Final verification sweep

**Files:** none created or modified — this task only runs checks across everything Tasks 1–8 touched.

- [ ] **Step 1: Full lint sweep**

Run: `uv run ruff check llmenv.py pylib tests`
Expected: clean. If not, run `uv run ruff check --fix llmenv.py pylib tests`, re-check clean, and commit the fixes separately (`git commit -m "fix(lint): ruff cleanup"`).

- [ ] **Step 2: Full shellcheck sweep**

Run: `shellcheck -s bash tools/*.sh setup/*.sh scripts/*.sh`
Expected: clean.

- [ ] **Step 3: Full Python test suite**

Run: `uv run pytest -q`
Expected: all tests PASS, 0 failures.

- [ ] **Step 4: `make validate` and `make test`**

Run: `make validate && make test`
Expected: both succeed (this exercises the exact commands `AGENTS.md` requires after any `.sh`/`.py` edit, end to end, via the Makefile rather than calling the underlying tools directly).

- [ ] **Step 5: Report completion**

No commit needed for this task if Steps 1–4 were already clean (nothing changed). If Step 1 required `--fix`, that commit already happened in Step 1.

## End-to-End Verification (manual, requires live hardware — do not run without explicit confirmation)

This plan's automated tests use stubs/mocks for anything requiring a live container (per Tasks 4/5/6's fixtures). The real integration — a live OmniRoute container actually answering `/api/providers` and routing a completion — can only be verified by actually starting the stack:

```bash
make setup
make check-setup
make start
make check-server
podman compose -f ~/.config/llm-env/docker-compose.yml ps
make stop
```

This mutates live systemd user units, config, and running containers — get explicit user confirmation before running it, same as this repo's existing convention for any end-to-end sequence.
