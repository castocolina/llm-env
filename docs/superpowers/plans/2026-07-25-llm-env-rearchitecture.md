# LLM Environment Re-architecture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the distrobox + source-build LLM environment with a prebuilt-image, podman-quadlet, YAML-configured server whose GPU device, VRAM budget, and inference backend are measured at setup rather than hardcoded.

**Architecture:** A thin `Makefile` dispatches to bash scripts that orchestrate `podman`/`systemctl`/`curl`. All parsing, schema validation, hardware detection, and VRAM arithmetic live in a Python layer (`llmenv.py` + `pylib/`) invoked as `uv run llmenv.py <subcommand>`, which emits JSON that bash consumes with `jq`. Configuration is a single user-editable `models.yml`.

**Tech Stack:** bash + shellcheck, Python 3.14 (PEP 723 single-file deps via `uv`), PyYAML 6.0.3, pytest 9.1.1, ruff 0.16.0, podman 5.8.4 + quadlet, systemd 259 user units, jq 1.8.1, yq, avahi, firewalld.

**Spec:** `docs/superpowers/specs/2026-07-25-llm-env-rearchitecture-design.md`

## Global Constraints

- All output must be in English regardless of input language.
- Every `.sh` file must pass `shellcheck -s bash` before commit (`make validate`).
- Every `.py` file must pass `uvx ruff check` before commit (`make validate`).
- Makefile target bodies longer than 3 lines MUST delegate to a `.sh` file. Trivial targets may stay inline.
- Python is invoked only as `uv run llmenv.py <subcommand>`. Never `python3 llmenv.py`.
- Tests run as `uv run --with pytest pytest tests/ -v`.
- No source builds of llama.cpp. Use the prebuilt `ghcr.io/ggml-org/llama.cpp:server-vulkan` image.
- `runtime.models_max` is ALWAYS recomputed as the count of enabled models. Never hardcoded, never silently overridden.
- `spike_headroom` is the constant `1024` MiB. Defined once in `pylib/budget.py`.
- Vulkan device indices (`Vulkan0`) are unstable and MUST NOT be persisted. Persist the PCI address (`0000:03:00.0`) and resolve the index at runtime.
- Target hardware: RX 9070 XT, PCI `0000:03:00.0`, `card1`, `renderD128`, 16304 MiB VRAM, gfx1201. iGPU Raphael, PCI `0000:0e:00.0`, `card0`, `renderD129`, 512 MiB.
- macOS is out of scope. Do not add macOS code paths or claim macOS support in docs.

---

## File Structure

| File | Responsibility |
|---|---|
| `Makefile` | Thin dispatcher only |
| `models.yml.example` | Config template committed to git |
| `llmenv.py` | argparse CLI dispatcher, PEP 723 header, JSON output |
| `pylib/__init__.py` | Empty package marker |
| `pylib/config.py` | Load/save/validate `models.yml`, enable/disable, `models_max` sync |
| `pylib/gguf.py` | GGUF magic-byte and metadata header parsing |
| `pylib/detect.py` | GPU enumeration from `/sys/class/drm`, compositor render node |
| `pylib/budget.py` | VRAM budget arithmetic and feasibility verdict |
| `pylib/presets.py` | `presets.ini` generation via `configparser` |
| `lib.sh` | Shared bash logging, colours, config path resolution |
| `setup.sh` | Interactive configurator |
| `start.sh` | Device resolution, quadlet render, unit start, health gate |
| `stop.sh` | Stop the systemd unit |
| `check-setup.sh` | Offline validation |
| `check-server.sh` | Online API contract validation |
| `benchmark.sh` | Backend benchmark with fallback chain |
| `tests/test_*.py` | pytest suites, one per `pylib` module |

**Deleted:** `models.sh`, `setup-test.sh`, `server-test.sh`, `debug-inference.sh`, `presets.ini`, `llama.cpp/` (1.5 GB untracked clone).

---

## Task 1: Repo hygiene and Makefile skeleton

**Files:**
- Modify: `.gitignore`
- Create: `Makefile` (full rewrite)
- Delete: `llama.cpp/`, `presets.ini`, `debug-inference.sh`

**Interfaces:**
- Consumes: nothing
- Produces: `make validate` target used by every later task; `make help`

- [ ] **Step 1: Remove the stray clone and dead files**

```bash
cd /var/home/bazzite/git/llm-env
rm -rf llama.cpp
rm -f presets.ini debug-inference.sh
```

- [ ] **Step 2: Write `.gitignore`**

```gitignore
tmp/
llama.cpp/
__pycache__/
*.pyc
.pytest_cache/
.ruff_cache/
models.yml
```

Note: `models.yml` is generated and machine-specific; `models.yml.example` is tracked.

- [ ] **Step 3: Write the Makefile**

```makefile
.PHONY: help setup start stop restart check-setup check-server benchmark \
        enable-boot disable-boot status logs validate test clean

UNIT = llm-server

help:
	@echo "make setup         Interactive configuration"
	@echo "make start         Start the LLM server"
	@echo "make stop          Stop the LLM server"
	@echo "make restart       Restart the LLM server"
	@echo "make check-setup   Validate config, image, models, GPU (offline)"
	@echo "make check-server  Validate the running server API (online)"
	@echo "make benchmark     Benchmark Vulkan; CPU fallback exits nonzero"
	@echo "make enable-boot   Start automatically at boot"
	@echo "make disable-boot  Do not start at boot"
	@echo "make status        Show service status"
	@echo "make logs          Follow service logs"
	@echo "make validate      Run shellcheck and ruff"
	@echo "make test          Run the Python test suite"
	@echo "make clean         Remove config, unit, and images"

setup:
	@bash setup.sh

start:
	@bash start.sh

stop:
	@bash stop.sh

restart: stop start

check-setup:
	@bash check-setup.sh

check-server:
	@bash check-server.sh

benchmark:
	@bash benchmark.sh

enable-boot:
	@loginctl enable-linger "$$USER"
	@systemctl --user enable $(UNIT).service
	@echo "Enabled at boot."

disable-boot:
	@systemctl --user disable $(UNIT).service
	@echo "Disabled at boot. Run 'loginctl disable-linger $$USER' to fully revert."

status:
	@systemctl --user status $(UNIT).service --no-pager || true

logs:
	@journalctl --user -u $(UNIT).service -f

validate:
	@shellcheck -s bash ./*.sh
	@uvx ruff check llmenv.py pylib tests
	@echo "All checks passed."

test:
	@uv run --with pytest pytest tests/ -v

clean:
	@bash clean.sh
```

- [ ] **Step 4: Verify targets exist and help runs**

Run: `make help && make -n validate`
Expected: help text prints; `make -n validate` echoes the shellcheck and ruff commands without error.

- [ ] **Step 5: Commit**

```bash
git add -A Makefile .gitignore
git commit -m "chore: rewrite Makefile as thin dispatcher, drop vendored llama.cpp

Removes the 1.5 GB untracked llama.cpp clone that setup.sh created by
cloning into the host CWD, plus the unused presets.ini and the
debug-inference.sh script whose exit-code capture was always 0."
```

---

## Task 2: Config module — load, validate, enable/disable

**Files:**
- Create: `pylib/__init__.py`, `pylib/config.py`
- Create: `models.yml.example`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `DEFAULT_CONFIG_PATH: Path`
  - `class ConfigError(Exception)`
  - `load_config(path: Path) -> dict`
  - `save_config(cfg: dict, path: Path) -> None`
  - `validate_config(cfg: dict) -> list[str]` (empty list == valid)
  - `enabled_models(cfg: dict) -> list[dict]`
  - `set_model_enabled(cfg: dict, alias: str, enabled: bool) -> dict`
  - `sync_models_max(cfg: dict) -> dict`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_config.py`:

```python
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pylib.config import (
    ConfigError,
    enabled_models,
    load_config,
    save_config,
    set_model_enabled,
    sync_models_max,
    validate_config,
)


def make_cfg(**overrides):
    cfg = {
        "version": 1,
        "server": {
            "host": "0.0.0.0",
            "port": 8000,
            "api_key": "testkey",
            "mdns_name": "llm",
            "sleep_idle_seconds": 300,
        },
        "gpu": {
            "pci_address": "0000:03:00.0",
            "device_name": "AMD Radeon RX 9070 XT (RADV GFX1201)",
            "backend": "vulkan",
            "image": "ghcr.io/ggml-org/llama.cpp:server-vulkan",
            "vram_total_mib": 16304,
            "reserve_mode": "auto",
            "reserve_floor_mib": 1024,
        },
        "runtime": {
            "models_max": 1,
            "flash_attn": True,
            "cache_type_k": "q8_0",
            "cache_type_v": "q8_0",
        },
        "models": [
            {
                "alias": "gemma4",
                "enabled": True,
                "file": "gemma-4-12B-it-Q4_K_M.gguf",
                "url": "https://example.invalid/gemma.gguf",
                "size_bytes": 7660000000,
                "vram_budget": "55%",
                "ctx_size": 8192,
                "n_gpu_layers": 99,
            },
            {
                "alias": "ornith",
                "enabled": False,
                "file": "ornith-1.0-9b-Q4_K_M.gguf",
                "url": "https://example.invalid/ornith.gguf",
                "size_bytes": 5600000000,
                "vram_budget": "40%",
                "ctx_size": 8192,
                "n_gpu_layers": 99,
            },
        ],
    }
    cfg.update(overrides)
    return cfg


def test_valid_config_has_no_errors():
    assert validate_config(make_cfg()) == []


def test_missing_top_level_section_is_reported():
    cfg = make_cfg()
    del cfg["gpu"]
    assert "missing required section: gpu" in validate_config(cfg)


def test_duplicate_alias_is_reported():
    cfg = make_cfg()
    cfg["models"][1]["alias"] = "gemma4"
    assert "duplicate model alias: gemma4" in validate_config(cfg)


def test_bad_vram_budget_is_reported():
    cfg = make_cfg()
    cfg["models"][0]["vram_budget"] = "lots"
    errors = validate_config(cfg)
    assert any("vram_budget" in e for e in errors)


def test_enabled_models_filters_disabled():
    aliases = [m["alias"] for m in enabled_models(make_cfg())]
    assert aliases == ["gemma4"]


def test_set_model_enabled_toggles_without_deleting():
    cfg = set_model_enabled(make_cfg(), "ornith", True)
    assert len(cfg["models"]) == 2
    assert [m["alias"] for m in enabled_models(cfg)] == ["gemma4", "ornith"]


def test_set_model_enabled_unknown_alias_raises():
    with pytest.raises(ConfigError):
        set_model_enabled(make_cfg(), "nope", True)


def test_sync_models_max_matches_enabled_count():
    cfg = sync_models_max(make_cfg())
    assert cfg["runtime"]["models_max"] == 1
    cfg = sync_models_max(set_model_enabled(cfg, "ornith", True))
    assert cfg["runtime"]["models_max"] == 2


def test_save_then_load_roundtrip(tmp_path):
    path = tmp_path / "models.yml"
    save_config(make_cfg(), path)
    assert load_config(path) == make_cfg()


def test_load_missing_file_raises_configerror(tmp_path):
    with pytest.raises(ConfigError):
        load_config(tmp_path / "absent.yml")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --with pytest pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pylib'`

- [ ] **Step 3: Create the package marker**

Create `pylib/__init__.py` as an empty file:

```bash
mkdir -p pylib && : > pylib/__init__.py
```

- [ ] **Step 4: Implement `pylib/config.py`**

```python
"""Load, validate, and mutate the models.yml configuration."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG_PATH = Path.home() / ".config" / "llm-env" / "models.yml"

REQUIRED_SECTIONS = ("server", "gpu", "runtime", "models")
REQUIRED_MODEL_KEYS = (
    "alias",
    "enabled",
    "file",
    "url",
    "size_bytes",
    "vram_budget",
    "ctx_size",
    "n_gpu_layers",
)
VALID_BACKENDS = ("vulkan", "cpu")
VRAM_BUDGET_RE = re.compile(r"^\s*\d+(\.\d+)?\s*(%|GB|MiB)\s*$", re.IGNORECASE)


class ConfigError(Exception):
    """Raised when the configuration cannot be read or is structurally invalid."""


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> dict[str, Any]:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ConfigError(f"config not found: {path}") from exc
    except OSError as exc:
        raise ConfigError(f"cannot read config {path}: {exc}") from exc

    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {path}: {exc}") from exc

    if not isinstance(data, dict):
        raise ConfigError(f"config root must be a mapping: {path}")
    return data


def save_config(cfg: dict[str, Any], path: Path = DEFAULT_CONFIG_PATH) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(cfg, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )


def validate_config(cfg: dict[str, Any]) -> list[str]:
    errors: list[str] = []

    if cfg.get("version") != 1:
        errors.append("version must be 1")

    for section in REQUIRED_SECTIONS:
        if section not in cfg:
            errors.append(f"missing required section: {section}")
    if errors:
        return errors

    gpu = cfg["gpu"]
    if gpu.get("backend") not in VALID_BACKENDS:
        errors.append(f"gpu.backend must be one of {VALID_BACKENDS}")
    if not isinstance(gpu.get("vram_total_mib"), int):
        errors.append("gpu.vram_total_mib must be an integer")
    if gpu.get("reserve_mode") not in ("auto", "fixed"):
        errors.append("gpu.reserve_mode must be 'auto' or 'fixed'")

    models = cfg["models"]
    if not isinstance(models, list) or not models:
        errors.append("models must be a non-empty list")
        return errors

    seen: set[str] = set()
    for model in models:
        alias = model.get("alias", "<unnamed>")
        for key in REQUIRED_MODEL_KEYS:
            if key not in model:
                errors.append(f"model {alias} missing key: {key}")
        if alias in seen:
            errors.append(f"duplicate model alias: {alias}")
        seen.add(alias)
        budget = model.get("vram_budget")
        if budget is not None and not VRAM_BUDGET_RE.match(str(budget)):
            errors.append(
                f"model {alias} vram_budget must look like '55%', '7.5GB', or '512MiB'"
            )

    return errors


def enabled_models(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    return [m for m in cfg.get("models", []) if m.get("enabled")]


def set_model_enabled(cfg: dict[str, Any], alias: str, enabled: bool) -> dict[str, Any]:
    for model in cfg.get("models", []):
        if model.get("alias") == alias:
            model["enabled"] = enabled
            return cfg
    raise ConfigError(f"unknown model alias: {alias}")


def sync_models_max(cfg: dict[str, Any]) -> dict[str, Any]:
    cfg.setdefault("runtime", {})["models_max"] = len(enabled_models(cfg))
    return cfg
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run --with pytest pytest tests/test_config.py -v`
Expected: PASS — 10 passed

- [ ] **Step 6: Write `models.yml.example`**

```yaml
version: 1

server:
  host: 0.0.0.0
  port: 8000
  api_key: ""
  mdns_name: llm
  sleep_idle_seconds: 300

gpu:
  pci_address: ""
  device_name: ""
  backend: vulkan
  image: ghcr.io/ggml-org/llama.cpp:server-vulkan
  vram_total_mib: 0
  reserve_mode: auto
  reserve_floor_mib: 1024
  benchmark:
    vulkan:
      pp_tps: null
      tg_tps: null
      measured_at: null

runtime:
  models_max: 0
  flash_attn: true
  cache_type_k: q8_0
  cache_type_v: q8_0

models:
  - alias: gemma4
    enabled: true
    file: gemma-4-12B-it-Q4_K_M.gguf
    url: https://huggingface.co/bartowski/gemma-4-12B-it-GGUF/resolve/main/gemma-4-12B-it-Q4_K_M.gguf
    size_bytes: 7660000000
    vram_budget: 55%
    ctx_size: 8192
    n_gpu_layers: 99

  - alias: ornith
    enabled: true
    file: ornith-1.0-9b-Q4_K_M.gguf
    url: https://huggingface.co/deepreinforce-ai/Ornith-1.0-9B-GGUF/resolve/main/ornith-1.0-9b-Q4_K_M.gguf
    size_bytes: 5600000000
    vram_budget: 40%
    ctx_size: 8192
    n_gpu_layers: 99

  - alias: openhermes
    enabled: false
    file: openhermes-2.5-mistral-7b.Q4_K_M.gguf
    url: https://huggingface.co/TheBloke/OpenHermes-2.5-Mistral-7B-GGUF/resolve/main/openhermes-2.5-mistral-7b.Q4_K_M.gguf
    size_bytes: 4368450304
    vram_budget: 30%
    ctx_size: 8192
    n_gpu_layers: 99
```

- [ ] **Step 7: Commit**

```bash
git add pylib/__init__.py pylib/config.py tests/test_config.py models.yml.example
git commit -m "feat: add YAML config module with schema validation

Replaces models.sh globals and the write-only .config file. Models are
enabled/disabled by flag rather than deleted, and models_max is always
derived from the enabled count."
```

---

## Task 3: GGUF header parsing

**Files:**
- Create: `pylib/gguf.py`
- Test: `tests/test_gguf.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `GGUF_MAGIC: bytes` (`b"GGUF"`)
  - `class GgufError(Exception)`
  - `read_gguf_header(path: Path) -> dict` returning keys `version: int`, `tensor_count: int`, `metadata: dict[str, int|float|str|bool]`
  - `validate_gguf(path: Path) -> tuple[bool, str]` returning `(ok, message)`
  - `kv_geometry(metadata: dict) -> dict` returning `block_count: int`, `head_count_kv: int`, `key_length: int`, `value_length: int`

Rationale: the VRAM budget in Task 5 needs real model geometry. Guessing KV cache size would reproduce the class of error this project is fixing.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_gguf.py`:

```python
import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pylib.gguf import GgufError, kv_geometry, read_gguf_header, validate_gguf

# GGUF metadata value type codes used by this test.
T_UINT32 = 4
T_STRING = 8


def _kv_uint32(key: str, value: int) -> bytes:
    kb = key.encode()
    return (
        struct.pack("<Q", len(kb))
        + kb
        + struct.pack("<I", T_UINT32)
        + struct.pack("<I", value)
    )


def _kv_string(key: str, value: str) -> bytes:
    kb, vb = key.encode(), value.encode()
    return (
        struct.pack("<Q", len(kb))
        + kb
        + struct.pack("<I", T_STRING)
        + struct.pack("<Q", len(vb))
        + vb
    )


def write_fake_gguf(path: Path, *, magic=b"GGUF", version=3) -> Path:
    kvs = [
        _kv_string("general.architecture", "llama"),
        _kv_uint32("llama.block_count", 40),
        _kv_uint32("llama.attention.head_count_kv", 8),
        _kv_uint32("llama.attention.key_length", 128),
        _kv_uint32("llama.attention.value_length", 128),
    ]
    body = b"".join(kvs)
    header = magic + struct.pack("<I", version) + struct.pack("<QQ", 0, len(kvs))
    path.write_bytes(header + body)
    return path


def test_read_header_returns_version_and_metadata(tmp_path):
    header = read_gguf_header(write_fake_gguf(tmp_path / "m.gguf"))
    assert header["version"] == 3
    assert header["metadata"]["general.architecture"] == "llama"
    assert header["metadata"]["llama.block_count"] == 40


def test_validate_gguf_accepts_good_file(tmp_path):
    ok, msg = validate_gguf(write_fake_gguf(tmp_path / "m.gguf"))
    assert ok is True
    assert msg == "ok"


def test_validate_gguf_rejects_bad_magic(tmp_path):
    bad = tmp_path / "bad.gguf"
    write_fake_gguf(bad, magic=b"NOPE")
    ok, msg = validate_gguf(bad)
    assert ok is False
    assert "magic" in msg.lower()


def test_validate_gguf_reports_missing_file(tmp_path):
    ok, msg = validate_gguf(tmp_path / "absent.gguf")
    assert ok is False
    assert "not found" in msg.lower()


def test_read_header_raises_on_truncated_file(tmp_path):
    truncated = tmp_path / "t.gguf"
    truncated.write_bytes(b"GGUF\x03\x00\x00")
    with pytest.raises(GgufError):
        read_gguf_header(truncated)


def test_kv_geometry_extracts_dimensions(tmp_path):
    header = read_gguf_header(write_fake_gguf(tmp_path / "m.gguf"))
    geo = kv_geometry(header["metadata"])
    assert geo == {
        "block_count": 40,
        "head_count_kv": 8,
        "key_length": 128,
        "value_length": 128,
    }


def test_kv_geometry_raises_when_architecture_missing():
    with pytest.raises(GgufError):
        kv_geometry({"llama.block_count": 40})
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --with pytest pytest tests/test_gguf.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pylib.gguf'`

- [ ] **Step 3: Implement `pylib/gguf.py`**

```python
"""Minimal GGUF header reader.

Parses only the header and metadata key/value block, which is all the VRAM
budget calculation needs. Tensor data is never read, so this is fast even on
multi-gigabyte files.

Format reference: llama.cpp ggml/docs/gguf.md
"""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Any, BinaryIO

GGUF_MAGIC = b"GGUF"

# GGUF metadata value type codes.
UINT8, INT8, UINT16, INT16, UINT32, INT32 = 0, 1, 2, 3, 4, 5
FLOAT32, BOOL, STRING, ARRAY, UINT64, INT64, FLOAT64 = 6, 7, 8, 9, 10, 11, 12

_SCALAR = {
    UINT8: ("<B", 1),
    INT8: ("<b", 1),
    UINT16: ("<H", 2),
    INT16: ("<h", 2),
    UINT32: ("<I", 4),
    INT32: ("<i", 4),
    FLOAT32: ("<f", 4),
    BOOL: ("<?", 1),
    UINT64: ("<Q", 8),
    INT64: ("<q", 8),
    FLOAT64: ("<d", 8),
}

# Metadata is capped so a corrupt file cannot cause unbounded reads.
MAX_METADATA_ENTRIES = 100_000


class GgufError(Exception):
    """Raised when a file is not valid GGUF or is truncated."""


def _read_exact(fh: BinaryIO, size: int) -> bytes:
    data = fh.read(size)
    if len(data) != size:
        raise GgufError(f"truncated GGUF: wanted {size} bytes, got {len(data)}")
    return data


def _read_string(fh: BinaryIO) -> str:
    (length,) = struct.unpack("<Q", _read_exact(fh, 8))
    return _read_exact(fh, length).decode("utf-8", errors="replace")


def _read_value(fh: BinaryIO, type_code: int) -> Any:
    if type_code in _SCALAR:
        fmt, size = _SCALAR[type_code]
        return struct.unpack(fmt, _read_exact(fh, size))[0]
    if type_code == STRING:
        return _read_string(fh)
    if type_code == ARRAY:
        (elem_type,) = struct.unpack("<I", _read_exact(fh, 4))
        (count,) = struct.unpack("<Q", _read_exact(fh, 8))
        if count > MAX_METADATA_ENTRIES:
            raise GgufError(f"array too large: {count} elements")
        return [_read_value(fh, elem_type) for _ in range(count)]
    raise GgufError(f"unknown GGUF metadata type code: {type_code}")


def read_gguf_header(path: Path) -> dict[str, Any]:
    path = Path(path)
    with path.open("rb") as fh:
        magic = _read_exact(fh, 4)
        if magic != GGUF_MAGIC:
            raise GgufError(f"bad magic {magic!r}, expected {GGUF_MAGIC!r}")

        (version,) = struct.unpack("<I", _read_exact(fh, 4))
        tensor_count, kv_count = struct.unpack("<QQ", _read_exact(fh, 16))
        if kv_count > MAX_METADATA_ENTRIES:
            raise GgufError(f"metadata count too large: {kv_count}")

        metadata: dict[str, Any] = {}
        for _ in range(kv_count):
            key = _read_string(fh)
            (type_code,) = struct.unpack("<I", _read_exact(fh, 4))
            metadata[key] = _read_value(fh, type_code)

    return {"version": version, "tensor_count": tensor_count, "metadata": metadata}


def validate_gguf(path: Path) -> tuple[bool, str]:
    path = Path(path)
    if not path.exists():
        return False, f"not found: {path}"
    try:
        read_gguf_header(path)
    except GgufError as exc:
        return False, str(exc)
    except OSError as exc:
        return False, f"cannot read {path}: {exc}"
    return True, "ok"


def kv_geometry(metadata: dict[str, Any]) -> dict[str, int]:
    arch = metadata.get("general.architecture")
    if not arch:
        raise GgufError("general.architecture missing from GGUF metadata")

    def need(suffix: str) -> int:
        key = f"{arch}.{suffix}"
        if key not in metadata:
            raise GgufError(f"required metadata key missing: {key}")
        return int(metadata[key])

    return {
        "block_count": need("block_count"),
        "head_count_kv": need("attention.head_count_kv"),
        "key_length": need("attention.key_length"),
        "value_length": need("attention.value_length"),
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --with pytest pytest tests/test_gguf.py -v`
Expected: PASS — 7 passed

- [ ] **Step 5: Verify against a real model file on disk**

Run:
```bash
uv run --with pyyaml python -c "
import sys; sys.path.insert(0, '.')
from pylib.gguf import read_gguf_header, kv_geometry
h = read_gguf_header('$HOME/llm-workspace/models/gemma-4-12B-it-Q4_K_M.gguf')
print('version', h['version'], 'arch', h['metadata'].get('general.architecture'))
print(kv_geometry(h['metadata']))
"
```
Expected: prints a version (2 or 3), an architecture string, and four positive integers. If it raises `GgufError`, the file is not valid GGUF — record the failure and stop; do not weaken the parser to accommodate it.

- [ ] **Step 6: Commit**

```bash
git add pylib/gguf.py tests/test_gguf.py
git commit -m "feat: add GGUF header parser for validation and KV geometry

Replaces the invalid 'llama-cli --list-models' call and the size-only
file check. Reads only the header, so it is fast on multi-GB files."
```

---

## Task 4: Hardware detection

**Files:**
- Create: `pylib/detect.py`
- Test: `tests/test_detect.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `list_gpus(drm_root: Path = Path("/sys/class/drm")) -> list[dict]` where each dict has `card: str`, `pci_address: str`, `render_node: str`, `vram_total_mib: int`, `vram_used_mib: int`, `connected_outputs: list[str]`
  - `compositor_render_node(proc_root: Path = Path("/proc")) -> str | None`
  - `detect(drm_root=..., proc_root=...) -> dict` with keys `gpus: list[dict]`, `compositor_render_node: str | None`

All functions take injectable roots so tests use fixtures rather than the live system.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_detect.py`:

```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pylib.detect import compositor_render_node, detect, list_gpus

MIB = 1024 * 1024


def build_drm(root: Path) -> Path:
    """Mirror the real topology: card1 = dGPU 16304 MiB, card0 = iGPU 512 MiB."""
    drm = root / "drm"
    for card, pci, render, total, used in (
        ("card0", "0000:0e:00.0", "renderD129", 512, 51),
        ("card1", "0000:03:00.0", "renderD128", 16304, 2026),
    ):
        device = root / "devices" / pci
        device.mkdir(parents=True, exist_ok=True)
        (device / "mem_info_vram_total").write_text(str(total * MIB))
        (device / "mem_info_vram_used").write_text(str(used * MIB))

        card_dir = drm / card
        card_dir.mkdir(parents=True, exist_ok=True)
        (card_dir / "device").symlink_to(device, target_is_directory=True)

        render_dir = drm / render
        render_dir.mkdir(parents=True, exist_ok=True)
        (render_dir / "device").symlink_to(device, target_is_directory=True)

    for connector, status in (
        ("card0-HDMI-A-2", "connected"),
        ("card0-DP-4", "disconnected"),
        ("card1-DP-2", "connected"),
        ("card1-DP-1", "disconnected"),
    ):
        conn = drm / connector
        conn.mkdir(parents=True, exist_ok=True)
        (conn / "status").write_text(status + "\n")
    return drm


def test_list_gpus_maps_card_to_pci_and_render_node(tmp_path):
    gpus = {g["card"]: g for g in list_gpus(build_drm(tmp_path))}
    assert gpus["card1"]["pci_address"] == "0000:03:00.0"
    assert gpus["card1"]["render_node"] == "renderD128"
    assert gpus["card0"]["pci_address"] == "0000:0e:00.0"
    assert gpus["card0"]["render_node"] == "renderD129"


def test_list_gpus_reads_vram(tmp_path):
    gpus = {g["card"]: g for g in list_gpus(build_drm(tmp_path))}
    assert gpus["card1"]["vram_total_mib"] == 16304
    assert gpus["card1"]["vram_used_mib"] == 2026
    assert gpus["card0"]["vram_total_mib"] == 512


def test_list_gpus_lists_only_connected_outputs(tmp_path):
    gpus = {g["card"]: g for g in list_gpus(build_drm(tmp_path))}
    assert gpus["card1"]["connected_outputs"] == ["card1-DP-2"]
    assert gpus["card0"]["connected_outputs"] == ["card0-HDMI-A-2"]


def test_list_gpus_is_sorted_by_card_name(tmp_path):
    assert [g["card"] for g in list_gpus(build_drm(tmp_path))] == ["card0", "card1"]


def build_proc(root: Path, comm: str, render_node: str) -> Path:
    proc = root / "proc"
    pid_dir = proc / "3021"
    (pid_dir / "fd").mkdir(parents=True)
    (pid_dir / "comm").write_text(comm + "\n")
    target = root / "dev" / "dri" / render_node
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("")
    (pid_dir / "fd" / "7").symlink_to(target)
    return proc


def test_compositor_render_node_found(tmp_path):
    proc = build_proc(tmp_path, "plasmashell", "renderD128")
    assert compositor_render_node(proc) == "renderD128"


def test_compositor_render_node_absent_returns_none(tmp_path):
    proc = build_proc(tmp_path, "bash", "renderD128")
    assert compositor_render_node(proc) is None


def test_detect_combines_both_sources(tmp_path):
    drm = build_drm(tmp_path)
    proc = build_proc(tmp_path, "kwin_wayland", "renderD128")
    result = detect(drm, proc)
    assert len(result["gpus"]) == 2
    assert result["compositor_render_node"] == "renderD128"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --with pytest pytest tests/test_detect.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pylib.detect'`

- [ ] **Step 3: Implement `pylib/detect.py`**

```python
"""Detect GPUs and which render node the compositor is using.

Reads sysfs and procfs directly. Roots are injectable so tests can supply
fixtures instead of touching the live system.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

MIB = 1024 * 1024
CARD_RE = re.compile(r"^card\d+$")
RENDER_RE = re.compile(r"^renderD\d+$")
COMPOSITOR_NAMES = ("kwin_wayland", "kwin_x11", "plasmashell", "gnome-shell", "sway")


def _read_int(path: Path) -> int:
    try:
        return int(path.read_text().strip())
    except (OSError, ValueError):
        return 0


def _pci_of(entry: Path) -> str | None:
    device = entry / "device"
    if not device.exists():
        return None
    return device.resolve().name


def list_gpus(drm_root: Path = Path("/sys/class/drm")) -> list[dict[str, Any]]:
    drm_root = Path(drm_root)
    if not drm_root.is_dir():
        return []

    entries = list(drm_root.iterdir())

    render_by_pci: dict[str, str] = {}
    for entry in entries:
        if RENDER_RE.match(entry.name):
            pci = _pci_of(entry)
            if pci:
                render_by_pci[pci] = entry.name

    outputs_by_card: dict[str, list[str]] = {}
    for entry in entries:
        status_file = entry / "status"
        if not status_file.is_file():
            continue
        if status_file.read_text().strip() != "connected":
            continue
        card = entry.name.split("-", 1)[0]
        outputs_by_card.setdefault(card, []).append(entry.name)

    gpus: list[dict[str, Any]] = []
    for entry in entries:
        if not CARD_RE.match(entry.name):
            continue
        pci = _pci_of(entry)
        if not pci:
            continue
        device = entry / "device"
        gpus.append(
            {
                "card": entry.name,
                "pci_address": pci,
                "render_node": render_by_pci.get(pci, ""),
                "vram_total_mib": _read_int(device / "mem_info_vram_total") // MIB,
                "vram_used_mib": _read_int(device / "mem_info_vram_used") // MIB,
                "connected_outputs": sorted(outputs_by_card.get(entry.name, [])),
            }
        )

    return sorted(gpus, key=lambda g: g["card"])


def compositor_render_node(proc_root: Path = Path("/proc")) -> str | None:
    proc_root = Path(proc_root)
    if not proc_root.is_dir():
        return None

    for pid_dir in proc_root.iterdir():
        if not pid_dir.name.isdigit():
            continue
        comm_file = pid_dir / "comm"
        try:
            comm = comm_file.read_text().strip()
        except OSError:
            continue
        if comm not in COMPOSITOR_NAMES:
            continue

        fd_dir = pid_dir / "fd"
        try:
            fds = list(fd_dir.iterdir())
        except OSError:
            continue
        for fd in fds:
            try:
                target = fd.resolve().name
            except OSError:
                continue
            if RENDER_RE.match(target):
                return target
    return None


def detect(
    drm_root: Path = Path("/sys/class/drm"),
    proc_root: Path = Path("/proc"),
) -> dict[str, Any]:
    return {
        "gpus": list_gpus(drm_root),
        "compositor_render_node": compositor_render_node(proc_root),
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --with pytest pytest tests/test_detect.py -v`
Expected: PASS — 8 passed

- [ ] **Step 5: Sanity-check against the live system**

Run:
```bash
uv run --with pyyaml python -c "
import json, sys; sys.path.insert(0, '.')
from pylib.detect import detect
print(json.dumps(detect(), indent=2))
"
```
Expected: two GPUs; `card1` at `0000:03:00.0` with `vram_total_mib` 16304 and `render_node` `renderD128`; `compositor_render_node` is `renderD128`.

- [ ] **Step 6: Commit**

```bash
git add pylib/detect.py tests/test_detect.py
git commit -m "feat: add GPU and compositor detection from sysfs

Replaces hardcoded device assumptions and the Linux-only 'hostname -I'
call. Injectable roots make the topology testable without real hardware."
```

---

## Task 5: VRAM budget calculation

**Files:**
- Create: `pylib/budget.py`
- Test: `tests/test_budget.py`

**Interfaces:**
- Consumes: `pylib.gguf.kv_geometry`
- Produces:
  - `SPIKE_HEADROOM_MIB: int` (`1024`)
  - `BYTES_PER_ELEMENT: dict[str, float]`
  - `class BudgetError(Exception)`
  - `parse_vram_budget(value: str, total_mib: int) -> int`
  - `kv_cache_mib(geometry: dict, ctx_size: int, cache_type_k: str, cache_type_v: str) -> int`
  - `compute_budget(vram_total_mib, compositor_used_mib, reserve_floor_mib, model_costs) -> dict` where `model_costs` is `list[dict]` with `alias: str`, `weights_mib: int`, `kv_mib: int`; returns `available_mib`, `reserve_mib`, `required_mib`, `feasible: bool`, `shortfall_mib`, `remedies: list[str]`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_budget.py`:

```python
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pylib.budget import (
    SPIKE_HEADROOM_MIB,
    BudgetError,
    compute_budget,
    kv_cache_mib,
    parse_vram_budget,
)

GEOMETRY = {
    "block_count": 40,
    "head_count_kv": 8,
    "key_length": 128,
    "value_length": 128,
}


def test_spike_headroom_is_the_documented_constant():
    assert SPIKE_HEADROOM_MIB == 1024


def test_parse_percentage():
    assert parse_vram_budget("55%", 16304) == 8967


def test_parse_gigabytes():
    assert parse_vram_budget("7.5GB", 16304) == 7680


def test_parse_mebibytes():
    assert parse_vram_budget("512MiB", 16304) == 512


def test_parse_rejects_nonsense():
    with pytest.raises(BudgetError):
        parse_vram_budget("lots", 16304)


def test_kv_cache_f16_matches_formula():
    # 8192 ctx * 40 blocks * 8 kv heads * (128+128) * 2 bytes = 1280 MiB
    assert kv_cache_mib(GEOMETRY, 8192, "f16", "f16") == 1280


def test_kv_cache_q8_0_is_about_half_of_f16():
    f16 = kv_cache_mib(GEOMETRY, 8192, "f16", "f16")
    q8 = kv_cache_mib(GEOMETRY, 8192, "q8_0", "q8_0")
    assert 0.45 < q8 / f16 < 0.60


def test_kv_cache_rejects_unknown_type():
    with pytest.raises(BudgetError):
        kv_cache_mib(GEOMETRY, 8192, "q3_k_xxl", "f16")


def test_budget_feasible_when_models_fit():
    result = compute_budget(
        vram_total_mib=16304,
        compositor_used_mib=300,
        reserve_floor_mib=1024,
        model_costs=[{"alias": "a", "weights_mib": 7305, "kv_mib": 640}],
    )
    assert result["feasible"] is True
    assert result["shortfall_mib"] == 0
    assert result["reserve_mib"] == 1024  # floor wins over the smaller measurement
    assert result["available_mib"] == 16304 - 1024 - SPIKE_HEADROOM_MIB


def test_budget_uses_measurement_when_above_floor():
    result = compute_budget(
        vram_total_mib=16304,
        compositor_used_mib=2026,
        reserve_floor_mib=1024,
        model_costs=[{"alias": "a", "weights_mib": 100, "kv_mib": 10}],
    )
    assert result["reserve_mib"] == 2026


def test_budget_infeasible_reports_shortfall_and_remedies():
    result = compute_budget(
        vram_total_mib=16304,
        compositor_used_mib=2026,
        reserve_floor_mib=1024,
        model_costs=[
            {"alias": "gemma4", "weights_mib": 7305, "kv_mib": 1280},
            {"alias": "ornith", "weights_mib": 5368, "kv_mib": 1280},
        ],
    )
    assert result["feasible"] is False
    assert result["shortfall_mib"] > 0
    assert result["remedies"]
    assert any("cache_type" in r or "ctx_size" in r for r in result["remedies"])


def test_budget_with_no_models_is_feasible():
    result = compute_budget(16304, 2026, 1024, [])
    assert result["feasible"] is True
    assert result["required_mib"] == 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --with pytest pytest tests/test_budget.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pylib.budget'`

- [ ] **Step 3: Implement `pylib/budget.py`**

```python
"""VRAM budget arithmetic.

Every quantity here is measured or derived from GGUF metadata. The single
tunable, SPIKE_HEADROOM_MIB, is a constant defined once and documented in the
design spec.
"""

from __future__ import annotations

import re
from typing import Any

# Fixed allowance for compositor, browser, and game VRAM spikes.
SPIKE_HEADROOM_MIB = 1024

MIB_PER_GB = 1024

# Approximate bytes per element for KV cache storage types.
# q8_0 stores 32 int8 values plus one f16 scale => 34/32 bytes per element.
BYTES_PER_ELEMENT: dict[str, float] = {
    "f32": 4.0,
    "f16": 2.0,
    "bf16": 2.0,
    "q8_0": 34.0 / 32.0,
    "q5_1": 24.0 / 32.0,
    "q5_0": 22.0 / 32.0,
    "q4_1": 20.0 / 32.0,
    "q4_0": 18.0 / 32.0,
}

BUDGET_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(%|GB|MiB)\s*$", re.IGNORECASE)


class BudgetError(Exception):
    """Raised when a budget value or cache type cannot be interpreted."""


def parse_vram_budget(value: str, total_mib: int) -> int:
    match = BUDGET_RE.match(str(value))
    if not match:
        raise BudgetError(
            f"cannot parse vram_budget {value!r}; expected '55%', '7.5GB', or '512MiB'"
        )
    amount, unit = float(match.group(1)), match.group(2).lower()
    if unit == "%":
        return int(total_mib * amount / 100)
    if unit == "gb":
        return int(amount * MIB_PER_GB)
    return int(amount)


def kv_cache_mib(
    geometry: dict[str, int],
    ctx_size: int,
    cache_type_k: str,
    cache_type_v: str,
) -> int:
    for name in (cache_type_k, cache_type_v):
        if name not in BYTES_PER_ELEMENT:
            raise BudgetError(
                f"unknown cache type {name!r}; known: {sorted(BYTES_PER_ELEMENT)}"
            )

    blocks = geometry["block_count"]
    kv_heads = geometry["head_count_kv"]
    elements_k = ctx_size * blocks * kv_heads * geometry["key_length"]
    elements_v = ctx_size * blocks * kv_heads * geometry["value_length"]

    total_bytes = (
        elements_k * BYTES_PER_ELEMENT[cache_type_k]
        + elements_v * BYTES_PER_ELEMENT[cache_type_v]
    )
    return int(total_bytes / (1024 * 1024))


def compute_budget(
    vram_total_mib: int,
    compositor_used_mib: int,
    reserve_floor_mib: int,
    model_costs: list[dict[str, Any]],
) -> dict[str, Any]:
    reserve = max(compositor_used_mib, reserve_floor_mib)
    available = vram_total_mib - reserve - SPIKE_HEADROOM_MIB
    required = sum(m["weights_mib"] + m["kv_mib"] for m in model_costs)
    shortfall = max(0, required - available)
    feasible = shortfall == 0

    remedies: list[str] = []
    if not feasible:
        remedies.append(
            "set runtime.cache_type_k and cache_type_v to q8_0 "
            "(roughly halves KV cache size)"
        )
        remedies.append(
            "reduce ctx_size for one or more models (KV cache scales linearly)"
        )
        remedies.append("enable runtime.flash_attn to reduce attention scratch memory")
        if len(model_costs) > 1:
            largest = max(model_costs, key=lambda m: m["weights_mib"] + m["kv_mib"])
            remedies.append(
                f"disable a model, e.g. '{largest['alias']}' "
                f"({largest['weights_mib'] + largest['kv_mib']} MiB)"
            )
        if compositor_used_mib > reserve_floor_mib:
            remedies.append(
                f"move the compositor to the iGPU with "
                f"KWIN_DRM_DEVICES=/dev/dri/card0 to reclaim ~{compositor_used_mib} MiB "
                "(this blanks displays attached to the dGPU)"
            )

    return {
        "vram_total_mib": vram_total_mib,
        "reserve_mib": reserve,
        "spike_headroom_mib": SPIKE_HEADROOM_MIB,
        "available_mib": available,
        "required_mib": required,
        "shortfall_mib": shortfall,
        "feasible": feasible,
        "models": model_costs,
        "remedies": remedies,
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --with pytest pytest tests/test_budget.py -v`
Expected: PASS — 12 passed

- [ ] **Step 5: Commit**

```bash
git add pylib/budget.py tests/test_budget.py
git commit -m "feat: add VRAM budget model with KV cache sizing

KV cache size is computed from real GGUF geometry rather than guessed.
Infeasible configurations report a shortfall and concrete remedies
instead of silently reducing models_max."
```

---

## Task 6: presets.ini generation

**Files:**
- Create: `pylib/presets.py`
- Test: `tests/test_presets.py`

**Interfaces:**
- Consumes: `pylib.config.enabled_models`
- Produces: `render_presets(cfg: dict, models_dir: str, device: str) -> str`, `write_presets(cfg, models_dir, device, path: Path) -> None`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_presets.py`:

```python
import configparser
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pylib.presets import render_presets, write_presets

CFG = {
    "version": 1,
    "runtime": {
        "models_max": 2,
        "flash_attn": True,
        "cache_type_k": "q8_0",
        "cache_type_v": "q8_0",
    },
    "models": [
        {
            "alias": "gemma4",
            "enabled": True,
            "file": "gemma-4-12B-it-Q4_K_M.gguf",
            "ctx_size": 8192,
            "n_gpu_layers": 99,
        },
        {
            "alias": "ornith",
            "enabled": False,
            "file": "ornith-1.0-9b-Q4_K_M.gguf",
            "ctx_size": 4096,
            "n_gpu_layers": 99,
        },
    ],
}


def parse(text: str) -> configparser.ConfigParser:
    parser = configparser.ConfigParser()
    parser.read_string(text)
    return parser


def test_output_declares_version_1():
    assert parse(render_presets(CFG, "/models", "Vulkan0")).defaults()["version"] == "1"


def test_global_star_section_carries_shared_settings():
    parser = parse(render_presets(CFG, "/models", "Vulkan0"))
    star = parser["*"]
    assert star["device"] == "Vulkan0"
    assert star["flash-attn"] == "on"
    assert star["cache-type-k"] == "q8_0"
    assert star["cache-type-v"] == "q8_0"


def test_only_enabled_models_get_sections():
    parser = parse(render_presets(CFG, "/models", "Vulkan0"))
    sections = [s for s in parser.sections() if s != "*"]
    assert sections == ["gemma4"]


def test_model_section_has_absolute_path_and_settings():
    parser = parse(render_presets(CFG, "/models", "Vulkan0"))
    section = parser["gemma4"]
    assert section["model"] == "/models/gemma-4-12B-it-Q4_K_M.gguf"
    assert section["ctx-size"] == "8192"
    assert section["n-gpu-layers"] == "99"


def test_flash_attn_off_renders_off():
    cfg = {**CFG, "runtime": {**CFG["runtime"], "flash_attn": False}}
    assert parse(render_presets(cfg, "/models", "Vulkan0"))["*"]["flash-attn"] == "off"


def test_write_presets_creates_parent_directories(tmp_path):
    target = tmp_path / "nested" / "presets.ini"
    write_presets(CFG, "/models", "Vulkan0", target)
    assert target.exists()
    assert "[gemma4]" in target.read_text()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --with pytest pytest tests/test_presets.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pylib.presets'`

- [ ] **Step 3: Implement `pylib/presets.py`**

```python
"""Render llama-server router presets.ini from models.yml.

Uses configparser so the output is always syntactically valid, including the
mandatory 'version = 1' and the shared '[*]' section that the previous
hand-rolled heredoc omitted.

Format reference: llama.cpp tools/server/README.md
"""

from __future__ import annotations

import configparser
import io
from pathlib import Path
from typing import Any

from pylib.config import enabled_models


def render_presets(cfg: dict[str, Any], models_dir: str, device: str) -> str:
    runtime = cfg.get("runtime", {})

    parser = configparser.ConfigParser(defaults={"version": "1"})
    parser.optionxform = str  # preserve hyphenated keys verbatim

    parser["*"] = {
        "device": device,
        "flash-attn": "on" if runtime.get("flash_attn") else "off",
        "cache-type-k": str(runtime.get("cache_type_k", "f16")),
        "cache-type-v": str(runtime.get("cache_type_v", "f16")),
    }

    for model in enabled_models(cfg):
        parser[model["alias"]] = {
            "model": str(Path(models_dir) / model["file"]),
            "ctx-size": str(model["ctx_size"]),
            "n-gpu-layers": str(model["n_gpu_layers"]),
        }

    buffer = io.StringIO()
    parser.write(buffer)
    return buffer.getvalue()


def write_presets(
    cfg: dict[str, Any], models_dir: str, device: str, path: Path
) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_presets(cfg, models_dir, device), encoding="utf-8")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --with pytest pytest tests/test_presets.py -v`
Expected: PASS — 6 passed

- [ ] **Step 5: Commit**

```bash
git add pylib/presets.py tests/test_presets.py
git commit -m "feat: generate presets.ini via configparser

Adds the required 'version = 1' and the shared [*] section, and pins the
GPU device so layers cannot land on the iGPU or llvmpipe."
```

---

## Task 7: `llmenv.py` CLI dispatcher

**Files:**
- Create: `llmenv.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: everything in `pylib`
- Produces: subcommands `detect`, `budget`, `presets`, `resolve-device`, `models`, `validate-gguf`, `init`. All emit JSON on stdout except `presets`, which writes a file and prints its path. Exit code 0 on success, 1 on handled error, 2 on usage error.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cli.py`:

```python
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(REPO / "llmenv.py"), *args],
        capture_output=True,
        text=True,
        cwd=REPO,
    )


def test_detect_emits_json_with_gpus_key():
    result = run("detect")
    assert result.returncode == 0, result.stderr
    assert "gpus" in json.loads(result.stdout)


def test_resolve_device_matches_pci_from_device_listing(tmp_path):
    listing = tmp_path / "devices.txt"
    listing.write_text(
        "Available devices:\n"
        "  Vulkan0: AMD Radeon RX 9070 XT (RADV GFX1201) (16304 MiB, 16304 MiB free)\n"
        "  Vulkan1: AMD Radeon Graphics (RADV RAPHAEL_MENDOCINO) (512 MiB, 512 MiB free)\n"
    )
    result = run(
        "resolve-device",
        "--device-name",
        "AMD Radeon RX 9070 XT (RADV GFX1201)",
        "--listing-file",
        str(listing),
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["device"] == "Vulkan0"


def test_resolve_device_reports_error_when_absent(tmp_path):
    listing = tmp_path / "devices.txt"
    listing.write_text("Available devices:\n  Vulkan0: Some Other GPU (1024 MiB)\n")
    result = run(
        "resolve-device",
        "--device-name",
        "AMD Radeon RX 9070 XT (RADV GFX1201)",
        "--listing-file",
        str(listing),
    )
    assert result.returncode == 1
    assert "error" in json.loads(result.stdout)


def test_models_list_reports_enabled_flags(tmp_path):
    config = tmp_path / "models.yml"
    config.write_text(
        "version: 1\n"
        "server: {host: 0.0.0.0, port: 8000, api_key: k, mdns_name: llm,"
        " sleep_idle_seconds: 300}\n"
        "gpu: {pci_address: '0000:03:00.0', device_name: d, backend: vulkan,"
        " image: i, vram_total_mib: 16304, reserve_mode: auto, reserve_floor_mib: 1024}\n"
        "runtime: {models_max: 1, flash_attn: true, cache_type_k: q8_0,"
        " cache_type_v: q8_0}\n"
        "models:\n"
        "  - {alias: a, enabled: true, file: a.gguf, url: u, size_bytes: 1,"
        " vram_budget: 10%, ctx_size: 4096, n_gpu_layers: 99}\n"
        "  - {alias: b, enabled: false, file: b.gguf, url: u, size_bytes: 1,"
        " vram_budget: 10%, ctx_size: 4096, n_gpu_layers: 99}\n"
    )
    result = run("models", "list", "--config", str(config))
    assert result.returncode == 0, result.stderr
    models = {m["alias"]: m["enabled"] for m in json.loads(result.stdout)["models"]}
    assert models == {"a": True, "b": False}


def test_models_enable_updates_models_max(tmp_path):
    config = tmp_path / "models.yml"
    config.write_text(
        "version: 1\n"
        "server: {host: 0.0.0.0, port: 8000, api_key: k, mdns_name: llm,"
        " sleep_idle_seconds: 300}\n"
        "gpu: {pci_address: '0000:03:00.0', device_name: d, backend: vulkan,"
        " image: i, vram_total_mib: 16304, reserve_mode: auto, reserve_floor_mib: 1024}\n"
        "runtime: {models_max: 1, flash_attn: true, cache_type_k: q8_0,"
        " cache_type_v: q8_0}\n"
        "models:\n"
        "  - {alias: a, enabled: true, file: a.gguf, url: u, size_bytes: 1,"
        " vram_budget: 10%, ctx_size: 4096, n_gpu_layers: 99}\n"
        "  - {alias: b, enabled: false, file: b.gguf, url: u, size_bytes: 1,"
        " vram_budget: 10%, ctx_size: 4096, n_gpu_layers: 99}\n"
    )
    assert run("models", "enable", "b", "--config", str(config)).returncode == 0
    result = run("models", "list", "--config", str(config))
    assert json.loads(result.stdout)["models_max"] == 2


def test_unknown_subcommand_is_usage_error():
    assert run("nonsense").returncode == 2
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --with pytest --with pyyaml pytest tests/test_cli.py -v`
Expected: FAIL — `can't open file 'llmenv.py'`

- [ ] **Step 3: Implement `llmenv.py`**

```python
#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6.0"]
# ///
"""llm-env control CLI.

Emits JSON on stdout so bash callers can consume output with jq.

Exit codes: 0 success, 1 handled error, 2 usage error.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pylib.budget import BudgetError, compute_budget, kv_cache_mib, parse_vram_budget
from pylib.config import (
    DEFAULT_CONFIG_PATH,
    ConfigError,
    enabled_models,
    load_config,
    save_config,
    set_model_enabled,
    sync_models_max,
    validate_config,
)
from pylib.detect import detect
from pylib.gguf import GgufError, kv_geometry, read_gguf_header, validate_gguf
from pylib.presets import write_presets

DEVICE_LINE_RE = re.compile(r"^\s*(\S+):\s+(.*?)\s*\(\d+\s*MiB", re.MULTILINE)


def emit(payload: dict[str, Any], code: int = 0) -> int:
    print(json.dumps(payload, indent=2))
    return code


def fail(message: str) -> int:
    return emit({"error": message}, 1)


def cmd_detect(args: argparse.Namespace) -> int:
    return emit(detect())


def cmd_resolve_device(args: argparse.Namespace) -> int:
    if args.listing_file:
        listing = Path(args.listing_file).read_text(encoding="utf-8")
    else:
        try:
            listing = subprocess.run(
                args.list_command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            ).stdout
        except subprocess.TimeoutExpired:
            return fail("timed out running the device listing command")

    for device_id, name in DEVICE_LINE_RE.findall(listing):
        if name.strip() == args.device_name.strip():
            return emit({"device": device_id, "name": name.strip()})

    available = [f"{d}: {n}" for d, n in DEVICE_LINE_RE.findall(listing)]
    return fail(
        f"device {args.device_name!r} not found. Available: {available or 'none'}"
    )


def _model_costs(cfg: dict[str, Any], models_dir: Path) -> list[dict[str, Any]]:
    runtime = cfg["runtime"]
    costs = []
    for model in enabled_models(cfg):
        path = models_dir / model["file"]
        header = read_gguf_header(path)
        geometry = kv_geometry(header["metadata"])
        costs.append(
            {
                "alias": model["alias"],
                "weights_mib": path.stat().st_size // (1024 * 1024),
                "kv_mib": kv_cache_mib(
                    geometry,
                    model["ctx_size"],
                    runtime["cache_type_k"],
                    runtime["cache_type_v"],
                ),
            }
        )
    return costs


def cmd_budget(args: argparse.Namespace) -> int:
    cfg = load_config(Path(args.config))
    facts = detect()
    gpu = next(
        (g for g in facts["gpus"] if g["pci_address"] == cfg["gpu"]["pci_address"]),
        None,
    )
    if gpu is None:
        return fail(f"configured GPU {cfg['gpu']['pci_address']} not present")

    compositor_used = (
        gpu["vram_used_mib"]
        if facts["compositor_render_node"] == gpu["render_node"]
        else 0
    )
    result = compute_budget(
        vram_total_mib=gpu["vram_total_mib"],
        compositor_used_mib=compositor_used,
        reserve_floor_mib=cfg["gpu"]["reserve_floor_mib"],
        model_costs=_model_costs(cfg, Path(args.models_dir)),
    )
    result["compositor_on_this_gpu"] = compositor_used > 0
    result["models_max"] = cfg["runtime"]["models_max"]
    return emit(result, 0 if result["feasible"] else 1)


def cmd_presets(args: argparse.Namespace) -> int:
    cfg = load_config(Path(args.config))
    write_presets(cfg, args.models_dir, args.device, Path(args.output))
    return emit({"written": str(args.output), "models": cfg["runtime"]["models_max"]})


def cmd_models(args: argparse.Namespace) -> int:
    path = Path(args.config)
    cfg = load_config(path)

    if args.action == "list":
        return emit(
            {
                "models_max": cfg["runtime"]["models_max"],
                "models": [
                    {
                        "alias": m["alias"],
                        "enabled": bool(m["enabled"]),
                        "file": m["file"],
                    }
                    for m in cfg["models"]
                ],
            }
        )

    cfg = sync_models_max(set_model_enabled(cfg, args.alias, args.action == "enable"))
    save_config(cfg, path)
    return emit({"alias": args.alias, "models_max": cfg["runtime"]["models_max"]})


def cmd_validate_gguf(args: argparse.Namespace) -> int:
    cfg = load_config(Path(args.config))
    results, ok = [], True
    for model in enabled_models(cfg):
        valid, message = validate_gguf(Path(args.models_dir) / model["file"])
        ok = ok and valid
        results.append({"alias": model["alias"], "valid": valid, "message": message})
    return emit({"all_valid": ok, "results": results}, 0 if ok else 1)


def cmd_init(args: argparse.Namespace) -> int:
    cfg = load_config(Path(args.template))
    errors = validate_config(cfg)
    if errors:
        return fail("; ".join(errors))
    cfg = sync_models_max(cfg)
    save_config(cfg, Path(args.config))
    return emit({"written": str(args.config)})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="llmenv")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("detect").set_defaults(func=cmd_detect)

    resolve = sub.add_parser("resolve-device")
    resolve.add_argument("--device-name", required=True)
    resolve.add_argument("--listing-file")
    resolve.add_argument("--list-command", default="")
    resolve.set_defaults(func=cmd_resolve_device)

    budget = sub.add_parser("budget")
    budget.add_argument("--models-dir", required=True)
    budget.set_defaults(func=cmd_budget)

    presets = sub.add_parser("presets")
    presets.add_argument("--models-dir", required=True)
    presets.add_argument("--device", required=True)
    presets.add_argument("--output", required=True)
    presets.set_defaults(func=cmd_presets)

    models = sub.add_parser("models")
    models.add_argument("action", choices=["list", "enable", "disable"])
    models.add_argument("alias", nargs="?")
    models.set_defaults(func=cmd_models)

    gguf = sub.add_parser("validate-gguf")
    gguf.add_argument("--models-dir", required=True)
    gguf.set_defaults(func=cmd_validate_gguf)

    init = sub.add_parser("init")
    init.add_argument("--template", required=True)
    init.set_defaults(func=cmd_init)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "models" and args.action != "list" and not args.alias:
        print(json.dumps({"error": "alias is required for enable/disable"}, indent=2))
        return 2
    try:
        return args.func(args)
    except (ConfigError, BudgetError, GgufError) as exc:
        return fail(str(exc))
    except OSError as exc:
        return fail(f"filesystem error: {exc}")


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --with pytest --with pyyaml pytest tests/test_cli.py -v`
Expected: PASS — 6 passed

- [ ] **Step 5: Run the whole suite and the linters**

Run: `make test && make validate`
Expected: all tests pass; shellcheck and ruff report no findings.

- [ ] **Step 6: Commit**

```bash
git add llmenv.py tests/test_cli.py
git commit -m "feat: add llmenv CLI dispatcher

Single PEP 723 entry point exposing detect, budget, presets,
resolve-device, models, validate-gguf, and init as JSON-emitting
subcommands for the bash layer to consume with jq."
```

---

## Task 8: Shared bash library and interactive setup

**Files:**
- Create: `lib.sh`, `setup.sh`
- Delete: `models.sh`, `setup-test.sh`, `server-test.sh`

**Interfaces:**
- Consumes: `llmenv.py init`, `models`, `detect`, `validate-gguf`
- Produces: `lib.sh` exporting `log_info`, `log_warn`, `log_error`, `log_step`, `die`, `require_cmd`, and the variables `CONFIG_PATH`, `MODELS_DIR`, `REPO_DIR`, `UNIT_NAME`

- [ ] **Step 1: Write `lib.sh`**

```bash
#!/usr/bin/env bash
# lib.sh — shared helpers. Source, do not execute.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_PATH="${LLM_ENV_CONFIG:-${HOME}/.config/llm-env/models.yml}"
MODELS_DIR="${LLM_ENV_MODELS_DIR:-${HOME}/llm-workspace/models}"
UNIT_NAME="llm-server"
QUADLET_DIR="${HOME}/.config/containers/systemd"

GREEN=$'\033[0;32m'; YELLOW=$'\033[1;33m'; BLUE=$'\033[0;34m'
RED=$'\033[0;31m'; NC=$'\033[0m'

log_step()  { printf '%s==>%s %s\n' "$BLUE"   "$NC" "$*"; }
log_info()  { printf '%s  ok%s %s\n' "$GREEN" "$NC" "$*"; }
log_warn()  { printf '%swarn%s %s\n' "$YELLOW" "$NC" "$*" >&2; }
log_error() { printf '%s fail%s %s\n' "$RED"  "$NC" "$*" >&2; }

die() { log_error "$*"; exit 1; }

require_cmd() {
    for cmd in "$@"; do
        command -v "$cmd" >/dev/null 2>&1 || die "required command not found: $cmd"
    done
}

# Run llmenv.py and return its JSON on stdout.
llmenv() { uv run "${REPO_DIR}/llmenv.py" "$@"; }
```

- [ ] **Step 2: Verify lib.sh passes shellcheck**

Run: `shellcheck -s bash lib.sh`
Expected: no output (clean).

- [ ] **Step 3: Write `setup.sh`**

```bash
#!/usr/bin/env bash
# setup.sh — interactive configurator.
set -euo pipefail
# shellcheck source=lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

require_cmd uv jq podman curl

log_step "Step 1/7  Creating configuration"
if [ -f "$CONFIG_PATH" ]; then
    log_info "using existing config at ${CONFIG_PATH}"
else
    llmenv --config "$CONFIG_PATH" init --template "${REPO_DIR}/models.yml.example" >/dev/null
    log_info "created ${CONFIG_PATH} from template"
fi

log_step "Step 2/7  Detecting GPUs"
facts="$(llmenv detect)"
echo "$facts" | jq -r '
  .gpus[] |
  "  \(.card)  \(.pci_address)  \(.vram_total_mib) MiB  " +
  (if (.connected_outputs | length) > 0 then "displays: \(.connected_outputs | join(","))" else "headless" end)'

default_pci="$(echo "$facts" | jq -r '[.gpus[]] | max_by(.vram_total_mib) | .pci_address')"
read -rp "  PCI address to use for inference [${default_pci}]: " pci
pci="${pci:-$default_pci}"

gpu="$(echo "$facts" | jq --arg p "$pci" '.gpus[] | select(.pci_address == $p)')"
[ -n "$gpu" ] || die "no GPU with PCI address ${pci}"
vram_total="$(echo "$gpu" | jq -r '.vram_total_mib')"
log_info "selected ${pci} with ${vram_total} MiB VRAM"

log_step "Step 3/7  Selecting models"
llmenv --config "$CONFIG_PATH" models list \
  | jq -r '.models[] | "  \(if .enabled then "[x]" else "[ ]" end) \(.alias)  \(.file)"'
echo "  Toggle with: make setup, or 'uv run llmenv.py models enable <alias>'"
read -rp "  Toggle any alias now (blank to keep current): " alias_toggle
if [ -n "$alias_toggle" ]; then
    current="$(llmenv --config "$CONFIG_PATH" models list \
      | jq -r --arg a "$alias_toggle" '.models[] | select(.alias==$a) | .enabled')"
    [ -n "$current" ] || die "unknown alias: ${alias_toggle}"
    if [ "$current" = "true" ]; then action=disable; else action=enable; fi
    llmenv --config "$CONFIG_PATH" models "$action" "$alias_toggle" >/dev/null
    log_info "${action}d ${alias_toggle}"
fi

log_step "Step 4/7  Downloading models"
mkdir -p "$MODELS_DIR"
while IFS=$'\t' read -r file url; do
    if [ -f "${MODELS_DIR}/${file}" ]; then
        log_info "${file} already present"
        continue
    fi
    log_info "downloading ${file}"
    curl -fL --continue-at - --progress-bar "$url" -o "${MODELS_DIR}/${file}" \
      || die "download failed: ${url}"
done < <(uv run "${REPO_DIR}/llmenv.py" --config "$CONFIG_PATH" models list \
         | jq -r '.models[] | select(.enabled) | [.file, .url] | @tsv' \
         | while IFS=$'\t' read -r f _; do
               url="$(yq -r ".models[] | select(.file == \"$f\") | .url" "$CONFIG_PATH")"
               printf '%s\t%s\n' "$f" "$url"
           done)

log_step "Step 5/7  Validating model files"
llmenv --config "$CONFIG_PATH" validate-gguf --models-dir "$MODELS_DIR" \
  | jq -r '.results[] | "  \(if .valid then "ok  " else "FAIL" end) \(.alias): \(.message)"' \
  || die "one or more model files are not valid GGUF"

log_step "Step 6/7  Generating API key and storing GPU selection"
api_key="$(head -c 24 /dev/urandom | base64 | tr -d '/+=' | head -c 32)"
device_name="$(echo "$gpu" | jq -r '.card')"
yq -i ".gpu.pci_address = \"${pci}\"" "$CONFIG_PATH"
yq -i ".gpu.vram_total_mib = ${vram_total}" "$CONFIG_PATH"
yq -i ".server.api_key = \"${api_key}\"" "$CONFIG_PATH"
log_info "api key stored in ${CONFIG_PATH}"
log_warn "device_name is set during 'make benchmark'; run it before first start (card: ${device_name})"

log_step "Step 7/7  Checking the VRAM budget"
if llmenv --config "$CONFIG_PATH" budget --models-dir "$MODELS_DIR" > /tmp/llm-budget.json; then
    jq -r '"  available \(.available_mib) MiB, required \(.required_mib) MiB — fits"' /tmp/llm-budget.json
else
    jq -r '"  available \(.available_mib) MiB, required \(.required_mib) MiB — SHORT BY \(.shortfall_mib) MiB"' /tmp/llm-budget.json
    jq -r '.remedies[] | "    - \(.)"' /tmp/llm-budget.json
    log_warn "models_max=$(jq -r .models_max /tmp/llm-budget.json) exceeds the VRAM budget"
fi

echo
log_info "Setup complete. Next: make benchmark, then make start"
```

- [ ] **Step 4: Delete the superseded scripts**

```bash
git rm -f models.sh setup-test.sh server-test.sh
```

- [ ] **Step 5: Verify shellcheck passes**

Run: `shellcheck -s bash lib.sh setup.sh`
Expected: no output.

- [ ] **Step 6: Commit**

```bash
git add lib.sh setup.sh
git commit -m "feat: add shared bash library and interactive setup

Replaces models.sh globals, the hardcoded 1/2/3 model menu, and the
write-only .config file. Model selection, GPU choice, and the VRAM
budget check are all driven from models.yml."
```

---

## Task 9: Backend benchmark with fallback chain

**Files:**
- Create: `benchmark.sh`

**Interfaces:**
- Consumes: `lib.sh`, `llmenv.py resolve-device`
- Produces: writes `gpu.backend`, `gpu.image`, `gpu.device_name`, and `gpu.benchmark.<backend>` into `models.yml`

- [ ] **Step 1: Write `benchmark.sh`**

```bash
#!/usr/bin/env bash
# benchmark.sh — measure Vulkan and configure CPU fallback on failure.
set -euo pipefail
# shellcheck source=lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

require_cmd uv jq yq podman

VULKAN_IMAGE="ghcr.io/ggml-org/llama.cpp:server-vulkan"

bench_model="$(yq -r '[.models[] | select(.enabled)] | sort_by(.size_bytes) | .[0].file' "$CONFIG_PATH")"
[ -n "$bench_model" ] && [ "$bench_model" != "null" ] || die "no enabled models to benchmark"
log_info "benchmarking with the smallest enabled model: ${bench_model}"

# Runs llama-bench in a container. Echoes "pp_tps tg_tps" or returns non-zero.
run_bench() {
    local image="$1"; shift
    local devices=("$@")
    local args=()
    for dev in "${devices[@]}"; do args+=(--device "$dev"); done

    podman run --rm "${args[@]}" \
        -v "${MODELS_DIR}:/models:ro,z" \
        --entrypoint /app/llama-bench \
        "$image" -m "/models/${bench_model}" -p 512 -n 128 -r 2 -o json 2>/dev/null \
      | jq -r '
          [ (.[] | select(.n_prompt > 0) | .avg_ts),
            (.[] | select(.n_gen    > 0) | .avg_ts) ] | @tsv'
}

record() {
    local backend="$1" pp="$2" tg="$3"
    yq -i ".gpu.benchmark.${backend}.pp_tps = ${pp}" "$CONFIG_PATH"
    yq -i ".gpu.benchmark.${backend}.tg_tps = ${tg}" "$CONFIG_PATH"
    yq -i ".gpu.benchmark.${backend}.measured_at = \"$(date -Iseconds)\"" "$CONFIG_PATH"
}

winner_backend=""; winner_image=""

log_step "Trying Vulkan"
log_info "pulling ${VULKAN_IMAGE} (0.31 GB)"
if podman pull "$VULKAN_IMAGE" >/dev/null 2>&1 \
   && result="$(run_bench "$VULKAN_IMAGE" /dev/dri)" \
   && [ -n "$result" ]; then
    pp="$(cut -f1 <<<"$result")"; tg="$(cut -f2 <<<"$result")"
    record vulkan "$pp" "$tg"
    log_info "Vulkan: ${pp} tok/s prompt, ${tg} tok/s generation"
    winner_backend=vulkan; winner_image="$VULKAN_IMAGE"
else
    log_warn "Vulkan benchmark failed"
fi

if [ -z "$winner_backend" ]; then
    log_warn "no GPU backend worked; falling back to CPU. Expect very slow inference."
    winner_backend=cpu; winner_image="ghcr.io/ggml-org/llama.cpp:server"
    podman pull "$winner_image" >/dev/null || die "cannot pull the CPU image either"
fi

log_step "Resolving the GPU device name"
listing="$(podman run --rm --device /dev/dri --entrypoint /app/llama-server \
           "$winner_image" --list-devices 2>/dev/null || true)"
pci="$(yq -r '.gpu.pci_address' "$CONFIG_PATH")"
vram="$(yq -r '.gpu.vram_total_mib' "$CONFIG_PATH")"
device_name="$(grep -oP '^\s*\S+:\s+\K.*?(?=\s+\(\d+\s*MiB)' <<<"$listing" \
               | awk -v v="$vram" 'NR==1{print}' || true)"

if [ -n "$device_name" ]; then
    yq -i ".gpu.device_name = \"${device_name}\"" "$CONFIG_PATH"
    log_info "device name recorded: ${device_name}  (pci ${pci})"
else
    log_warn "could not resolve a device name; start.sh will offload to all devices"
fi

yq -i ".gpu.backend = \"${winner_backend}\"" "$CONFIG_PATH"
yq -i ".gpu.image = \"${winner_image}\"" "$CONFIG_PATH"
log_info "backend set to ${winner_backend} (${winner_image})"
```

- [ ] **Step 2: Verify shellcheck passes**

Run: `shellcheck -s bash benchmark.sh`
Expected: no output.

- [ ] **Step 3: Verify the Vulkan failure fallback path**

Run: `LLM_ENV_CONFIG="$HOME/.config/llm-env/models.yml" bash -n benchmark.sh && echo "syntax ok"`
Expected: `syntax ok`. Full execution is deferred to Task 14 acceptance, since it pulls 8 GB.

- [ ] **Step 4: Commit**

```bash
git add benchmark.sh
git commit -m "feat: benchmark Vulkan with CPU fallback

Records Vulkan prompt and generation throughput in models.yml so
the configured result is evidence rather than assumption. Falls back
Vulkan failures configure CPU fallback and log a reason."
```

---

## Task 10: Quadlet service, start and stop

**Files:**
- Create: `start.sh`, `stop.sh`, `clean.sh`
- Delete: nothing (old `start.sh`/`stop.sh` are overwritten)

**Interfaces:**
- Consumes: `lib.sh`, `llmenv.py resolve-device`, `llmenv.py presets`, `llmenv.py budget`
- Produces: `${HOME}/.config/containers/systemd/llm-server.container`

- [ ] **Step 1: Write `start.sh`**

```bash
#!/usr/bin/env bash
# start.sh — render the quadlet unit and start the server.
set -euo pipefail
# shellcheck source=lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

require_cmd uv jq yq podman systemctl

[ -f "$CONFIG_PATH" ] || die "no config at ${CONFIG_PATH}; run 'make setup' first"

backend="$(yq -r '.gpu.backend' "$CONFIG_PATH")"
image="$(yq -r '.gpu.image' "$CONFIG_PATH")"
device_name="$(yq -r '.gpu.device_name' "$CONFIG_PATH")"
host="$(yq -r '.server.host' "$CONFIG_PATH")"
port="$(yq -r '.server.port' "$CONFIG_PATH")"
api_key="$(yq -r '.server.api_key' "$CONFIG_PATH")"
sleep_idle="$(yq -r '.server.sleep_idle_seconds' "$CONFIG_PATH")"
models_max="$(yq -r '.runtime.models_max' "$CONFIG_PATH")"

[ "$models_max" -gt 0 ] || die "no models enabled; run 'make setup'"

log_step "Checking the VRAM budget"
if ! budget="$(llmenv --config "$CONFIG_PATH" budget --models-dir "$MODELS_DIR")"; then
    echo "$budget" | jq -r '"  short by \(.shortfall_mib) MiB"'
    echo "$budget" | jq -r '.remedies[] | "    - \(.)"'
    die "VRAM budget exceeded for models_max=${models_max}; adjust the config and retry"
fi
echo "$budget" | jq -r '"  available \(.available_mib) MiB, required \(.required_mib) MiB"'

log_step "Resolving the GPU device"
device="all"
if [ "$backend" != "cpu" ] && [ -n "$device_name" ] && [ "$device_name" != "null" ]; then
    listing_file="$(mktemp)"
    podman run --rm --device /dev/dri --entrypoint /app/llama-server \
        "$image" --list-devices >"$listing_file" 2>/dev/null || true
    if resolved="$(llmenv resolve-device --device-name "$device_name" \
                   --listing-file "$listing_file")"; then
        device="$(echo "$resolved" | jq -r '.device')"
        log_info "pinned to ${device} (${device_name})"
    else
        log_warn "could not resolve ${device_name}; offloading to all devices"
    fi
    rm -f "$listing_file"
fi

log_step "Generating presets.ini"
presets_path="${HOME}/.config/llm-env/presets.ini"
llmenv --config "$CONFIG_PATH" presets \
    --models-dir /models --device "$device" --output "$presets_path" >/dev/null
log_info "wrote ${presets_path}"

log_step "Rendering the quadlet unit"
mkdir -p "$QUADLET_DIR"
device_lines="AddDevice=/dev/dri"

cat > "${QUADLET_DIR}/${UNIT_NAME}.container" <<EOF
# Generated by start.sh from ${CONFIG_PATH}. Edits will be overwritten.
[Unit]
Description=llama.cpp router server (${backend})
After=network-online.target

[Container]
Image=${image}
ContainerName=${UNIT_NAME}
${device_lines}
Volume=${MODELS_DIR}:/models:ro,z
Volume=${presets_path}:/etc/llama/presets.ini:ro,z
PublishPort=${port}:${port}
Environment=LLAMA_ARG_MODELS_PRESET=/etc/llama/presets.ini
Environment=LLAMA_ARG_MODELS_MAX=${models_max}
Environment=LLAMA_ARG_HOST=${host}
Environment=LLAMA_ARG_PORT=${port}
Environment=LLAMA_API_KEY=${api_key}
Exec=--sleep-idle-seconds ${sleep_idle}
HealthCmd=curl -fsS http://localhost:${port}/health || exit 1
HealthInterval=10s
HealthRetries=30
HealthStartPeriod=20s

[Service]
Restart=on-failure
TimeoutStartSec=300

[Install]
WantedBy=default.target
EOF
log_info "wrote ${QUADLET_DIR}/${UNIT_NAME}.container"

log_step "Starting the service"
systemctl --user daemon-reload
systemctl --user start "${UNIT_NAME}.service"

log_step "Waiting for health"
for _ in $(seq 1 60); do
    if curl -fsS -o /dev/null "http://localhost:${port}/health" 2>/dev/null; then
        log_info "server is ready"
        ip="$(ip -4 -json addr show scope global 2>/dev/null \
              | jq -r '[.[].addr_info[].local] | first // "unknown"')"
        mdns="$(yq -r '.server.mdns_name' "$CONFIG_PATH")"
        echo
        echo "  Local:   http://localhost:${port}/v1"
        echo "  Network: http://${ip}:${port}/v1"
        echo "  mDNS:    http://${mdns}.local:${port}/v1"
        echo "  API key: ${api_key}"
        echo
        echo "  Models:"
        yq -r '.models[] | select(.enabled) | "    - " + .alias' "$CONFIG_PATH"
        exit 0
    fi
    sleep 1
done

log_error "server did not become healthy within 60s"
echo "  Logs: journalctl --user -u ${UNIT_NAME}.service -n 50"
exit 1
```

- [ ] **Step 2: Write `stop.sh`**

```bash
#!/usr/bin/env bash
# stop.sh — stop the server.
set -euo pipefail
# shellcheck source=lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

require_cmd systemctl

if ! systemctl --user list-unit-files "${UNIT_NAME}.service" >/dev/null 2>&1; then
    log_warn "unit ${UNIT_NAME}.service is not installed; nothing to stop"
    exit 0
fi

if systemctl --user is-active --quiet "${UNIT_NAME}.service"; then
    systemctl --user stop "${UNIT_NAME}.service"
    log_info "server stopped"
else
    log_warn "server is not running"
fi
```

- [ ] **Step 3: Write `clean.sh`**

```bash
#!/usr/bin/env bash
# clean.sh — remove the unit, config, and images. Keeps downloaded models.
set -euo pipefail
# shellcheck source=lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

echo "This removes:"
echo "  unit    ${QUADLET_DIR}/${UNIT_NAME}.container"
echo "  config  ${CONFIG_PATH}"
echo "  images  ghcr.io/ggml-org/llama.cpp:server-*"
echo "Downloaded models in ${MODELS_DIR} are KEPT."
read -rp "Proceed? (yes/no) " confirm
[ "$confirm" = "yes" ] || { echo "Aborted."; exit 1; }

systemctl --user stop "${UNIT_NAME}.service" 2>/dev/null || true
systemctl --user disable "${UNIT_NAME}.service" 2>/dev/null || true
rm -f "${QUADLET_DIR}/${UNIT_NAME}.container"
systemctl --user daemon-reload
rm -f "$CONFIG_PATH" "${HOME}/.config/llm-env/presets.ini"
podman rmi -f ghcr.io/ggml-org/llama.cpp:server-vulkan \
                ghcr.io/ggml-org/llama.cpp:server 2>/dev/null || true
log_info "cleanup complete"
```

- [ ] **Step 4: Verify shellcheck passes**

Run: `shellcheck -s bash start.sh stop.sh clean.sh`
Expected: no output.

- [ ] **Step 5: Verify the quadlet renders to a valid unit**

Run:
```bash
/usr/libexec/podman/quadlet -dryrun -user 2>&1 | head -30
```
Expected: after `make start` has run once, this prints a generated `llm-server.service`. Before that it prints "No files parsed", which is also correct.

- [ ] **Step 6: Commit**

```bash
git add start.sh stop.sh clean.sh
git commit -m "feat: replace PID-file lifecycle with a podman quadlet unit

Removes the PID file, stale-PID handling, the kill/kill -9 escalation,
and the manual curl health loop. Adds device pinning, API-key auth, and
sleep-idle model eviction."
```

---

## Task 11: Offline validation

**Files:**
- Create: `check-setup.sh`

**Interfaces:**
- Consumes: `lib.sh`, `llmenv.py validate-gguf`, `llmenv.py budget`, `llmenv.py detect`
- Produces: exit 0 when every check passes, 1 otherwise

- [ ] **Step 1: Write `check-setup.sh`**

```bash
#!/usr/bin/env bash
# check-setup.sh — offline validation. No server required.
set -uo pipefail
# shellcheck source=lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
set +e

PASS=0; FAIL=0

check() {
    local label="$1"; shift
    if "$@" >/dev/null 2>&1; then
        log_info "$label"; PASS=$((PASS + 1))
    else
        log_error "$label"; FAIL=$((FAIL + 1))
    fi
}

log_step "Tooling"
for cmd in uv jq yq podman systemctl curl; do
    check "command available: ${cmd}" command -v "$cmd"
done

log_step "Configuration"
check "config exists at ${CONFIG_PATH}" test -f "$CONFIG_PATH"
check "config parses and validates" bash -c \
    "uv run '${REPO_DIR}/llmenv.py' --config '${CONFIG_PATH}' models list"

log_step "GPU access"
check "/dev/dri exists" test -d /dev/dri
check "a render node is readable" bash -c 'ls /dev/dri/renderD* >/dev/null 2>&1 && head -c0 /dev/dri/renderD128'

log_step "Configured GPU is present"
pci="$(yq -r '.gpu.pci_address' "$CONFIG_PATH" 2>/dev/null || echo "")"
check "GPU ${pci} detected" bash -c \
    "uv run '${REPO_DIR}/llmenv.py' detect | jq -e --arg p '${pci}' '.gpus[] | select(.pci_address==\$p)'"

log_step "Container image"
image="$(yq -r '.gpu.image' "$CONFIG_PATH" 2>/dev/null || echo "")"
check "image ${image} present locally" podman image exists "$image"

log_step "Model files"
if out="$(uv run "${REPO_DIR}/llmenv.py" --config "$CONFIG_PATH" \
          validate-gguf --models-dir "$MODELS_DIR" 2>/dev/null)"; then
    echo "$out" | jq -r '.results[] | "  ok   \(.alias)"'
    PASS=$((PASS + 1))
else
    echo "$out" | jq -r '.results[]? | "  \(if .valid then "ok  " else "FAIL" end) \(.alias): \(.message)"'
    log_error "one or more model files are invalid"
    FAIL=$((FAIL + 1))
fi

log_step "VRAM budget"
if out="$(uv run "${REPO_DIR}/llmenv.py" --config "$CONFIG_PATH" \
          budget --models-dir "$MODELS_DIR" 2>/dev/null)"; then
    echo "$out" | jq -r '"  available \(.available_mib) MiB, required \(.required_mib) MiB"'
    log_info "budget is feasible for models_max=$(echo "$out" | jq -r .models_max)"
    PASS=$((PASS + 1))
else
    echo "$out" | jq -r '"  short by \(.shortfall_mib) MiB"' 2>/dev/null
    echo "$out" | jq -r '.remedies[]? | "    - \(.)"' 2>/dev/null
    log_error "budget exceeded"
    FAIL=$((FAIL + 1))
fi

echo
log_step "Results: ${PASS} passed, ${FAIL} failed"
[ "$FAIL" -eq 0 ]
```

- [ ] **Step 2: Verify shellcheck passes**

Run: `shellcheck -s bash check-setup.sh`
Expected: no output.

- [ ] **Step 3: Run it against the current system**

Run: `make check-setup; echo "exit=$?"`
Expected: a pass/fail table. Before `make setup` has run, config checks fail and the exit code is 1 — that is correct behaviour, not a defect.

- [ ] **Step 4: Commit**

```bash
git add check-setup.sh
git commit -m "feat: add offline setup validation

Verifies tooling, config schema, GPU presence, render-node readability,
image availability, GGUF validity, and VRAM feasibility. Checks device
access directly rather than assuming render/video group membership."
```

---

## Task 12: Online API contract validation

**Files:**
- Create: `check-server.sh`

**Interfaces:**
- Consumes: `lib.sh`
- Produces: exit 0 when every assertion passes, 1 otherwise

- [ ] **Step 1: Write `check-server.sh`**

```bash
#!/usr/bin/env bash
# check-server.sh — assert the running server honours its API contract.
set -uo pipefail
# shellcheck source=lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
set +e

require_cmd curl jq yq

port="$(yq -r '.server.port' "$CONFIG_PATH")"
api_key="$(yq -r '.server.api_key' "$CONFIG_PATH")"
base="http://localhost:${port}"

PASS=0; FAIL=0
ok()   { log_info "$1"; PASS=$((PASS + 1)); }
bad()  { log_error "$1"; FAIL=$((FAIL + 1)); }

log_step "Health"
if curl -fsS -o /dev/null "${base}/health"; then
    ok "/health responds"
else
    bad "/health did not respond; is the server running?"
    log_step "Results: ${PASS} passed, ${FAIL} failed"
    exit 1
fi

log_step "Authentication"
code="$(curl -s -o /dev/null -w '%{http_code}' \
        -H "Authorization: Bearer definitely-not-the-key" "${base}/v1/models")"
if [ "$code" = "401" ]; then
    ok "an invalid API key is rejected (401)"
else
    bad "an invalid API key returned HTTP ${code}, expected 401"
fi

log_step "Model listing"
listed="$(curl -fsS -H "Authorization: Bearer ${api_key}" "${base}/v1/models" \
          | jq -r '[.data[].id] | sort | join(",")')"
expected="$(yq -r '[.models[] | select(.enabled) | .alias] | sort | join(",")' "$CONFIG_PATH")"
if [ "$listed" = "$expected" ]; then
    ok "/v1/models lists exactly: ${expected}"
else
    bad "/v1/models listed '${listed}', expected '${expected}'"
fi

log_step "Completions"
while read -r alias; do
    [ -n "$alias" ] || continue
    body="$(jq -n --arg m "$alias" \
        '{model: $m,
          messages: [{role: "user", content: "Reply with the single word: ready"}],
          max_tokens: 16, stream: false}')"

    response="$(curl -fsS --max-time 120 \
        -H "Authorization: Bearer ${api_key}" \
        -H "Content-Type: application/json" \
        -d "$body" "${base}/v1/chat/completions")"

    if [ -z "$response" ]; then
        bad "${alias}: request failed"
        continue
    fi

    content="$(jq -r '.choices[0].message.content // empty' <<<"$response")"
    if [ -n "$content" ]; then
        ok "${alias}: returned $(printf '%q' "$(head -c 40 <<<"$content")")"
    else
        bad "${alias}: empty content — $(jq -c '.error // .' <<<"$response" | head -c 120)"
    fi
done < <(yq -r '.models[] | select(.enabled) | .alias' "$CONFIG_PATH")

echo
log_step "Results: ${PASS} passed, ${FAIL} failed"
[ "$FAIL" -eq 0 ]
```

Note the arithmetic: counters use `$((PASS + 1))`, never `${PASS + 1}`, which is a bad substitution and aborts the script under `set -e`.

- [ ] **Step 2: Verify shellcheck passes**

Run: `shellcheck -s bash check-server.sh`
Expected: no output.

- [ ] **Step 3: Verify the summary arithmetic cannot regress**

Run: `bash -c 'A=1; B=2; echo "${A}/$((A + B))"'`
Expected: `1/3`. Confirm no `${X + Y}` form appears: `! grep -nE '\$\{[A-Za-z_]+ +\+' ./*.sh`

- [ ] **Step 4: Commit**

```bash
git add check-server.sh
git commit -m "test: replace capability test with an API contract test

The previous server-test.sh asserted the model knew live weather, which
a bare llama-server cannot do, and its regex matched the substring 'am'
so it passed on hallucinations. This asserts health, auth rejection,
model listing, and non-empty completions, with jq-built request bodies."
```

---

## Task 13: Network exposure — firewall and mDNS

**Files:**
- Modify: `setup.sh` (append a network step; renumber the step banners to 1/8..8/8)

**Interfaces:**
- Consumes: `lib.sh`
- Produces: an open firewall port and an avahi alias

- [ ] **Step 1: Append the network step to `setup.sh`**

Insert before the final `log_info "Setup complete..."` line:

```bash
log_step "Step 8/8  Network exposure"
port="$(yq -r '.server.port' "$CONFIG_PATH")"
mdns="$(yq -r '.server.mdns_name' "$CONFIG_PATH")"

if command -v firewall-cmd >/dev/null 2>&1; then
    if firewall-cmd --query-port="${port}/tcp" >/dev/null 2>&1; then
        log_info "firewall port ${port}/tcp already open"
    else
        read -rp "  Open firewall port ${port}/tcp for LAN access? (yes/no) " open_port
        if [ "$open_port" = "yes" ]; then
            sudo firewall-cmd --permanent --add-port="${port}/tcp" >/dev/null \
              && sudo firewall-cmd --reload >/dev/null \
              && log_info "opened ${port}/tcp" \
              || log_warn "could not open the port; do it manually"
        else
            log_warn "port not opened; other machines will not be able to connect"
        fi
    fi
else
    log_warn "firewall-cmd not found; skipping firewall configuration"
fi

if command -v avahi-publish >/dev/null 2>&1; then
    cat > "${HOME}/.config/systemd/user/llm-mdns.service" <<EOF
[Unit]
Description=Publish ${mdns}.local for the LLM server
After=network-online.target

[Service]
ExecStart=/usr/bin/avahi-publish -a -R ${mdns}.local \$(ip -4 -json addr show scope global | jq -r '[.[].addr_info[].local] | first')
Restart=on-failure

[Install]
WantedBy=default.target
EOF
    mkdir -p "${HOME}/.config/systemd/user"
    systemctl --user daemon-reload
    systemctl --user enable --now llm-mdns.service 2>/dev/null \
      && log_info "publishing ${mdns}.local" \
      || log_warn "could not start mDNS publishing; use the IP address instead"
else
    log_warn "avahi-publish not found; use the IP address instead of ${mdns}.local"
fi

echo
log_step "Usage examples"
api_key="$(yq -r '.server.api_key' "$CONFIG_PATH")"
first_alias="$(yq -r '[.models[] | select(.enabled)] | .[0].alias' "$CONFIG_PATH")"
cat <<EOF
  From this machine:
    curl http://localhost:${port}/v1/chat/completions \\
      -H "Authorization: Bearer ${api_key}" \\
      -H "Content-Type: application/json" \\
      -d '{"model":"${first_alias}","messages":[{"role":"user","content":"hello"}]}'

  From another machine on the LAN:
    curl http://${mdns}.local:${port}/v1/chat/completions \\
      -H "Authorization: Bearer ${api_key}" \\
      -H "Content-Type: application/json" \\
      -d '{"model":"${first_alias}","messages":[{"role":"user","content":"hello"}]}'

  OpenAI-compatible client settings:
    base_url = http://${mdns}.local:${port}/v1
    api_key  = ${api_key}
    model    = ${first_alias}
EOF
```

- [ ] **Step 2: Renumber the earlier step banners**

Change `Step 1/7` through `Step 7/7` to `Step 1/8` through `Step 7/8`:

```bash
sed -i 's|Step \([1-7]\)/7|Step \1/8|g' setup.sh
grep -n "Step .*/8" setup.sh
```
Expected: eight banners, numbered 1/8 through 8/8.

- [ ] **Step 3: Verify shellcheck passes**

Run: `shellcheck -s bash setup.sh`
Expected: no output.

- [ ] **Step 4: Commit**

```bash
git add setup.sh
git commit -m "feat: add firewall, mDNS, and usage examples to setup

Opens the port with consent, publishes <name>.local via avahi so LAN
clients survive DHCP changes, and prints ready-to-paste curl and
OpenAI-client configuration."
```

---

## Task 14: Documentation and end-to-end acceptance

**Files:**
- Modify: `AGENTS.md`, `README.md`, `QUICK_START.md`
- Delete: `.agents/setup-dev.md`
- Create: `.agents/architecture.md`

**Interfaces:**
- Consumes: everything
- Produces: documentation that matches the implementation

- [ ] **Step 1: Rewrite `AGENTS.md`**

```markdown
# Project Agent Instructions

## LLM Environment

Local llama.cpp router server on Bazzite, running as a rootless podman
quadlet with GPU acceleration. Configuration lives in `models.yml`.

## Commands

```bash
make setup         # Interactive configuration
make benchmark     # Measure Vulkan; CPU fallback exits nonzero
make start         # Start the server
make stop          # Stop the server
make check-setup   # Offline validation
make check-server  # Online API contract validation
make enable-boot   # Start at boot (opt-in)
make validate      # shellcheck + ruff
make test          # Python test suite
```

## Rules

After editing any `.sh` file, run `make validate`.
After editing any `.py` file, run `make validate && make test`.

Makefile target bodies longer than 3 lines must delegate to a `.sh` file.

Python is invoked only as `uv run llmenv.py <subcommand>`.

### Language

All output must be in English regardless of input language.

### Research Before Implementation

Do not assume. Before implementing anything that is not 100% clear:

1. Research the platform, API, or tool behaviour first
2. Verify assumptions with documentation or experiments
3. If something is unclear or you are guessing, ask the user

Silent failures from unresearched assumptions waste more time than asking.

Never hardcode a value that can be measured. GPU device, VRAM totals, and
compositor usage are all detected at runtime. Benchmarking uses Vulkan only;
an unsuccessful Vulkan benchmark configures CPU fallback and exits nonzero.

## Detailed Instructions

- [Architecture](.agents/architecture.md)
```

- [ ] **Step 2: Write `.agents/architecture.md`**

```markdown
# Architecture

## Layers

Bash orchestrates (`podman`, `systemctl`, `curl`, `yq`). Python parses and
computes (`uv run llmenv.py`). The two communicate over JSON via `jq`.

## Files

| File | Responsibility |
|---|---|
| `Makefile` | Thin dispatcher, no logic beyond 3 lines |
| `lib.sh` | Logging, paths, `require_cmd`, the `llmenv` wrapper |
| `setup.sh` | Interactive configuration, downloads, network exposure |
| `benchmark.sh` | Vulkan-only measurement with CPU fallback |
| `start.sh` | Budget check, device resolution, quadlet render, health gate |
| `stop.sh` / `clean.sh` | Lifecycle |
| `check-setup.sh` | Offline validation |
| `check-server.sh` | Online API contract validation |
| `llmenv.py` | CLI dispatcher, JSON out |
| `pylib/config.py` | Schema, enable/disable, `models_max` sync |
| `pylib/gguf.py` | GGUF header parsing, KV geometry |
| `pylib/detect.py` | GPU and compositor detection from sysfs |
| `pylib/budget.py` | VRAM arithmetic and remedies |
| `pylib/presets.py` | `presets.ini` via configparser |

## Invariants

- `runtime.models_max` always equals the count of enabled models.
- Vulkan device indices are never persisted; the PCI address is.
- `spike_headroom` is 1024 MiB, defined only in `pylib/budget.py`.
- An infeasible VRAM budget is reported, never silently corrected.

## Platform

Linux only. Bazzite/Fedora with podman and rootless quadlets. There is no
macOS support and none is planned.
```

- [ ] **Step 3: Rewrite `README.md`**

```markdown
# llm-env

Local llama.cpp router server with GPU acceleration, running as a rootless
podman quadlet on Bazzite.

## Quick start

```bash
make setup       # choose GPU and models, download, generate config
make benchmark   # measure Vulkan throughput; CPU fallback exits nonzero
make start       # start the server
make check-server
```

## What it does

- Serves multiple models from one endpoint using llama.cpp router mode.
  Clients pick a model with the `model` field.
- Detects your GPU, VRAM, and compositor usage, then computes a VRAM budget
  and refuses to start a configuration that cannot fit.
- Benchmarks Vulkan and records tokens/sec in `models.yml`. A Vulkan failure
  configures CPU fallback and exits nonzero.
- Runs as a systemd user service. Manual by default; `make enable-boot`
  makes it start with the machine.
- Exposes an OpenAI-compatible API on the LAN with an API key and an
  mDNS `.local` name.

## Configuration

Everything lives in `~/.config/llm-env/models.yml`. Models are enabled and
disabled with a flag, so nothing is lost when you stop using one:

```bash
uv run llmenv.py models list
uv run llmenv.py models disable ornith
uv run llmenv.py models enable openhermes
```

`models_max` always follows the number of enabled models.

## Requirements

Bazzite or Fedora, podman, uv, jq, yq, curl. No compiler needed — the
llama.cpp server comes from a prebuilt image.

## Commands

Run `make help`.
```

- [ ] **Step 4: Rewrite `QUICK_START.md`**

```markdown
# Quick Start

## First run

```bash
make setup       # 1. pick GPU + models, download, write config
make benchmark   # 2. measure backends (pulls up to 8 GB, once)
make start       # 3. start
make check-server
```

## Daily use

```bash
make start
make stop
make status
make logs
```

## Start at boot

```bash
make enable-boot     # loginctl enable-linger + systemctl --user enable
make disable-boot
```

## Using it

```bash
curl http://llm.local:8000/v1/chat/completions \
  -H "Authorization: Bearer $(yq -r .server.api_key ~/.config/llm-env/models.yml)" \
  -H "Content-Type: application/json" \
  -d '{"model":"gemma4","messages":[{"role":"user","content":"hello"}]}'
```

## Changing models

```bash
uv run llmenv.py models list
uv run llmenv.py models enable openhermes
make restart
```

## When something breaks

```bash
make check-setup     # config, GPU, images, model files, VRAM budget
make check-server    # health, auth, model listing, completions
make logs
```
```

- [ ] **Step 5: Delete the stale platform doc**

```bash
git rm .agents/setup-dev.md
```

It documents `setup-dev.sh`, `detect_os()`, `get_file_size`, `get_cpu_cores`,
`download_file`, and a macOS/Metal path, none of which exist.

- [ ] **Step 6: Run the full acceptance sequence**

```bash
make validate
make test
make setup
make benchmark
make start
make check-setup
make check-server
make stop
```

Expected: `make validate` and `make test` clean. `make setup` completes and
prints usage examples. `make benchmark` records numbers under
`gpu.benchmark` in `models.yml` and sets `gpu.backend`. `make start` reports
the resolved device and reaches health. `make check-server` passes every
assertion. Confirm the recorded backend:

```bash
yq '.gpu.backend, .gpu.benchmark' ~/.config/llm-env/models.yml
```

- [ ] **Step 7: Commit**

```bash
git add -A AGENTS.md README.md QUICK_START.md .agents/
git commit -m "docs: rewrite documentation to match the implementation

Removes references to make setup-dev, setup-dev.sh, detect_os,
get_file_size, get_cpu_cores, download_file, and the claimed macOS/Metal
support, none of which existed."
```

---

## Self-Review

**Spec coverage.** Every spec section maps to a task: §3 architecture → Tasks 1, 7, 8; §4 schema → Task 2; §5 budget → Tasks 3, 5; §6 backend → Task 9; §7 service → Task 10; §8 network → Task 13; §9 validation → Tasks 11, 12; §10 defects → all; §10 doc drift → Task 14.

**Every defect in spec §10 has an owning task.** `${A + B}` → Task 12 Step 3 asserts the pattern is absent repo-wide. Wrong-CWD clone → Task 1. `.gitignore` → Task 1. `debug-inference.sh` → Task 1. Capability test → Task 12. `--list-models` → Task 3. `git pull` precedence → Task 1 (deleted). Hardcoded menu → Task 8. Dead `.config` → Task 2. `stat -c%s` → Task 5. `hostname -I` → Task 10 uses `ip -json` + jq. JSON injection → Task 12. `cd` leak → Task 8. Missing `version = 1` → Task 6. Device pinning → Tasks 7, 10. `models-max` vs VRAM → Task 5. No auth → Tasks 10, 12.

**Type consistency.** `enabled_models`, `sync_models_max`, `set_model_enabled`, `load_config`, `save_config`, `validate_config` are defined in Task 2 and used identically in Tasks 6 and 7. `kv_geometry` returns the same four keys in Task 3 that Task 5 consumes. `compute_budget` returns `available_mib`/`required_mib`/`shortfall_mib`/`feasible`/`remedies` in Task 5, consumed with those exact names in Tasks 7, 8, 10, 11. `SPIKE_HEADROOM_MIB` is defined once. `UNIT_NAME`, `CONFIG_PATH`, `MODELS_DIR`, `QUADLET_DIR` come from `lib.sh` in Task 8 and are used unchanged thereafter.

**Two gaps found and fixed inline:** `clean.sh` was referenced by the Makefile in Task 1 but had no task — added to Task 10. `models.yml.example` was referenced by `llmenv.py init` but unwritten — added to Task 2 Step 6.
