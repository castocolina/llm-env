# Ornith 35B MoE Incorporation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Ornith 1.0 35B (a MoE model) as a selectable `models.yml` entry with CPU-offloaded routed experts (`--n-cpu-moe`) tuned to fit this host's VRAM/RAM, correct the 9B's `ctx_size` to its real native context, and add a general CPU-core ceiling to `resources.llm_server` alongside the existing RAM ceiling.

**Architecture:** Extend `pylib/gguf.py` with tensor-level size parsing so `llmenv.py`'s model-cost calculation can split a MoE model's weight footprint into a VRAM part and a CPU/RAM part (mirroring the existing GGUF-metadata-driven VRAM budget). Extend `pylib/resources.py` with a CPU-core percentage ceiling (mirroring the existing RAM ceiling). Wire both into `llmenv.py cmd_budget`, which already gates `make start`/`make setup` on feasibility — no changes needed to the calling shell scripts, since they already treat a nonzero exit + `remedies` array as "don't start."

**Tech stack:** Pure Python (stdlib `struct`, `re`, `math`) for the new GGUF tensor parsing — no new dependencies. Uses llama.cpp's existing `--n-cpu-moe` server flag.

## Global Constraints

- `runtime.models_max` stays `1`.
- Never silently correct an infeasible config — every new failure mode (RAM ceiling too low for a MoE model's CPU-offloaded experts, `n_cpu_moe` set on a non-MoE model) fails explicitly with an actionable remedy, matching the existing `pylib/budget.py` remedies pattern.
- `make clean` must keep downloaded models (already true — do not regress).
- **Confirmed by direct empirical testing this session** (see design doc `docs/superpowers/specs/2026-08-10-ornith-35b-moe-incorporation-design.md`): `--n-cpu-moe N` offloads the **first** N transformer blocks' routed-expert tensors to CPU (block indices `0..N-1`), leaving blocks `N..block_count-1` on GPU. Verified via `llama-server -v` load logs showing `tensor blk.N.ffn_*_exps.weight ... buffer type overridden to Vulkan_Host` for offloaded blocks and no such line for GPU-resident ones.
- Recommended values from the design doc's measurements (this host: RX 9070 XT, 16304 MiB VRAM, ~31GB RAM, 24 CPU threads): Ornith 35B `quantization: Q4_K_M`, `n_cpu_moe: 28`, `ctx_size: 262144`, `vram_budget: 85%`; Ornith 9B `ctx_size: 262144` (corrected from 131072), `vram_budget: 55%`; `resources.llm_server.memory_ceiling_pct: 60`, new `resources.llm_server.cpu_ceiling_pct: 60`.
- Routed-expert tensor names follow the pattern `blk.<N>.ffn_<gate|up|down>_exps.<weight|scale|input_scale>` — never match `*_shexp*` (shared expert, always GPU-resident, not part of `--n-cpu-moe`'s offload).

---

### Task 1: `pylib/gguf.py` — tensor-level size parsing for MoE offload budgeting

**Files:**
- Modify: `pylib/gguf.py`
- Test: `tests/test_gguf.py`

**Interfaces:**
- Consumes: nothing new (uses existing `_read_exact`, `_read_string`, `_read_value`, `_skip_exact`, `GgufError`, `GGUF_MAGIC` already in the file).
- Produces: `tensor_sizes(path: Path) -> dict[str, int]` (tensor name → on-disk byte size) and `moe_expert_offload_mib(path: Path, metadata: dict[str, Any], n_cpu_moe: int) -> int` — both consumed by Task 4's `llmenv.py` changes.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_gguf.py` (the existing `write_fake_gguf` helper only writes a metadata-only file with zero tensors — these new tests need actual tensor-info entries and tensor data bytes, so add a second fixture builder alongside it):

```python
def _tensor_info(name: str, n_dims: int, dims: list[int], ggml_type: int, offset: int) -> bytes:
    nb = name.encode()
    out = struct.pack("<Q", len(nb)) + nb
    out += struct.pack("<I", n_dims)
    for d in dims:
        out += struct.pack("<Q", d)
    out += struct.pack("<I", ggml_type)
    out += struct.pack("<Q", offset)
    return out


def write_fake_moe_gguf(path: Path, *, n_cpu_moe_blocks: int = 3) -> Path:
    """A minimal qwen35moe-arch GGUF with tensor-info entries for 3 blocks:
    each block has a routed-expert tensor (500 bytes), a shared-expert
    tensor (300 bytes, must never be offloaded), and a non-expert tensor
    (200 bytes). Tensor data is padded so on-disk sizes are exact multiples
    of these amounts, computable via offset deltas.
    """
    kvs = [
        _kv_string("general.architecture", "qwen35moe"),
        _kv_uint32("qwen35moe.block_count", n_cpu_moe_blocks),
        _kv_uint32("qwen35moe.expert_count", 8),
        _kv_uint32("qwen35moe.attention.head_count_kv", 2),
        _kv_uint32("qwen35moe.attention.key_length", 32),
        _kv_uint32("qwen35moe.attention.value_length", 32),
        _kv_uint32("qwen35moe.full_attention_interval", 1),
    ]
    kv_bytes = b"".join(kvs)

    tensors = []
    offset = 0
    T_F32 = 0
    for block in range(n_cpu_moe_blocks):
        tensors.append((f"blk.{block}.ffn_down_exps.weight", 500))
        tensors.append((f"blk.{block}.ffn_down_shexp.weight", 300))
        tensors.append((f"blk.{block}.attn_norm.weight", 200))
    tensor_info_bytes = b""
    for name, size in tensors:
        tensor_info_bytes += _tensor_info(name, 1, [size], T_F32, offset)
        offset += size

    header = (
        b"GGUF"
        + struct.pack("<I", 3)
        + struct.pack("<QQ", len(tensors), len(kvs))
    )
    # Body up to (but not including) tensor data must land on a 32-byte
    # boundary for real GGUF; this fixture pads to that boundary so the
    # alignment logic under test is exercised, not skipped.
    pre_data = header + kv_bytes + tensor_info_bytes
    pad = (-len(pre_data)) % 32
    tensor_data = b"\x00" * offset
    path.write_bytes(pre_data + b"\x00" * pad + tensor_data)
    return path


def test_tensor_sizes_computed_from_offset_deltas(tmp_path):
    from pylib.gguf import tensor_sizes

    path = write_fake_moe_gguf(tmp_path / "moe.gguf", n_cpu_moe_blocks=2)
    sizes = tensor_sizes(path)
    assert sizes["blk.0.ffn_down_exps.weight"] == 500
    assert sizes["blk.0.ffn_down_shexp.weight"] == 300
    assert sizes["blk.0.attn_norm.weight"] == 200
    assert sizes["blk.1.ffn_down_exps.weight"] == 500


def test_moe_expert_offload_mib_sums_only_offloaded_blocks_routed_experts(tmp_path):
    from pylib.gguf import moe_expert_offload_mib

    path = write_fake_moe_gguf(tmp_path / "moe.gguf", n_cpu_moe_blocks=3)
    metadata = read_gguf_header(path)["metadata"]

    # n_cpu_moe=2 offloads blocks 0 and 1's routed experts only (500 + 500
    # bytes = 1000 bytes), never the shared-expert or non-expert tensors,
    # and never block 2 (kept on GPU).
    result = moe_expert_offload_mib(path, metadata, n_cpu_moe=2)
    assert result == 1  # ceil(1000 / 1048576) == 1


def test_moe_expert_offload_mib_zero_when_n_cpu_moe_is_zero(tmp_path):
    from pylib.gguf import moe_expert_offload_mib

    path = write_fake_moe_gguf(tmp_path / "moe.gguf", n_cpu_moe_blocks=3)
    metadata = read_gguf_header(path)["metadata"]
    assert moe_expert_offload_mib(path, metadata, n_cpu_moe=0) == 0


def test_moe_expert_offload_mib_rejects_non_moe_architecture(tmp_path):
    from pylib.gguf import GgufError, moe_expert_offload_mib

    path = write_fake_gguf(tmp_path / "dense.gguf")  # arch "llama", no expert_count
    metadata = read_gguf_header(path)["metadata"]
    with pytest.raises(GgufError, match="no routed experts"):
        moe_expert_offload_mib(path, metadata, n_cpu_moe=1)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_gguf.py -v -k "tensor_sizes or moe_expert_offload"`
Expected: FAIL with `ImportError: cannot import name 'tensor_sizes'` (and similarly for `moe_expert_offload_mib`).

- [ ] **Step 3: Implement `tensor_sizes` and `moe_expert_offload_mib`**

Add near the top of `pylib/gguf.py` (after the existing imports):

```python
import math
import re
```

Add after `_skip_array_payload` and before `_read_value` (or anywhere below the existing helpers — exact position doesn't matter, just keep it grouped with the other private read helpers):

```python
def _skip_metadata(fh: BinaryIO, kv_count: int) -> None:
    for _ in range(kv_count):
        _read_string(fh)  # key
        (type_code,) = struct.unpack("<I", _read_exact(fh, 4))
        _read_value(fh, type_code)
```

Add as new public functions, after `read_gguf_header`:

```python
# GGUF pads the tensor-data section to this byte boundary by default.
# `general.alignment` can override it, but this codebase only uses these
# functions for its own MoE offload budgeting math (not for loading tensor
# data), so a slight misalignment on a nonstandard file would only shift
# the MiB estimate, never corrupt anything — not worth threading the
# override through for that.
DEFAULT_ALIGNMENT = 32

EXPERT_TENSOR_RE = re.compile(r"^blk\.(\d+)\.ffn_\w+_exps\.")


def tensor_sizes(path: Path) -> dict[str, int]:
    """Map each tensor name to its on-disk byte size.

    Sizes are computed from offset deltas between consecutive tensors (sorted
    by their declared offset), which is exact regardless of quantization type
    or padding — GGUF stores each tensor's byte offset directly, so no
    per-type size formula is needed.
    """
    path = Path(path)
    file_size = path.stat().st_size
    with path.open("rb") as fh:
        magic = _read_exact(fh, 4)
        if magic != GGUF_MAGIC:
            raise GgufError(f"bad magic {magic!r}, expected {GGUF_MAGIC!r}")
        struct.unpack("<I", _read_exact(fh, 4))  # version, unused here
        tensor_count, kv_count = struct.unpack("<QQ", _read_exact(fh, 16))
        if kv_count > MAX_METADATA_ENTRIES:
            raise GgufError(f"metadata count too large: {kv_count}")
        _skip_metadata(fh, kv_count)

        infos: list[tuple[str, int]] = []
        for _ in range(tensor_count):
            name = _read_string(fh)
            (n_dims,) = struct.unpack("<I", _read_exact(fh, 4))
            _skip_exact(fh, 8 * n_dims)  # dims: n_dims x uint64
            _skip_exact(fh, 4)  # ggml tensor type: uint32
            (offset,) = struct.unpack("<Q", _read_exact(fh, 8))
            infos.append((name, offset))

        data_start = fh.tell()

    if data_start % DEFAULT_ALIGNMENT != 0:
        data_start += DEFAULT_ALIGNMENT - (data_start % DEFAULT_ALIGNMENT)

    infos.sort(key=lambda item: item[1])
    sizes: dict[str, int] = {}
    for index, (name, offset) in enumerate(infos):
        next_offset = (
            infos[index + 1][1] if index + 1 < len(infos) else file_size - data_start
        )
        sizes[name] = next_offset - offset
    return sizes


def moe_expert_offload_mib(
    path: Path, metadata: dict[str, Any], n_cpu_moe: int
) -> int:
    """Bytes (rounded up to MiB) of routed-expert tensors that
    `--n-cpu-moe n_cpu_moe` sends to CPU: every tensor matching
    `blk.<i>.ffn_*_exps.*` for block index i < n_cpu_moe. Shared-expert
    tensors (`*_shexp*`) and non-expert tensors are never included — they
    stay GPU-resident regardless of `--n-cpu-moe`.

    Confirmed empirically (not from documentation) that `--n-cpu-moe N`
    offloads the FIRST N blocks (ascending index), via llama-server's
    verbose load log: see this plan's Global Constraints section.
    """
    arch = metadata.get("general.architecture")
    if not isinstance(arch, str) or not arch:
        raise GgufError("general.architecture missing from GGUF metadata")
    if f"{arch}.expert_count" not in metadata:
        raise GgufError(
            f"n_cpu_moe is set but {arch} has no routed experts (missing "
            f"{arch}.expert_count metadata) -- n_cpu_moe only applies to MoE models"
        )
    if n_cpu_moe <= 0:
        return 0

    sizes = tensor_sizes(path)
    offload_bytes = 0
    for name, size in sizes.items():
        match = EXPERT_TENSOR_RE.match(name)
        if match and int(match.group(1)) < n_cpu_moe:
            offload_bytes += size
    return math.ceil(offload_bytes / (1024 * 1024))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_gguf.py -v`
Expected: PASS (all tests, including the pre-existing ones — this step must not regress `test_read_header_returns_version_and_metadata` etc.)

- [ ] **Step 5: Commit**

```bash
git add pylib/gguf.py tests/test_gguf.py
git commit -m "feat(gguf): add tensor-size parsing for MoE CPU-offload budgeting"
```

---

### Task 2: `pylib/resources.py` — CPU core ceiling

**Files:**
- Modify: `pylib/resources.py`
- Test: `tests/test_resources.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `compute_resource_limits(..., cpu_ceiling_pct: float = 100, cpu_ceiling_floor_pct: float = 20)` — two new optional keyword parameters. Return shape unchanged (`llm_server.cpus` is now capped, same key).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_resources.py`:

```python
def test_cpu_ceiling_caps_llm_server_below_the_uncapped_remainder():
    uncapped = compute_resource_limits(host_cpu_count=24, host_memory_total_mib=32768)
    capped = compute_resource_limits(
        host_cpu_count=24, host_memory_total_mib=32768, cpu_ceiling_pct=60
    )
    assert capped["llm_server"]["cpus"] < uncapped["llm_server"]["cpus"]
    assert capped["llm_server"]["cpus"] == 14  # round(24 * 0.60) == 14.4 -> 14


def test_cpu_ceiling_above_the_remainder_is_a_no_op():
    result = compute_resource_limits(
        host_cpu_count=8, host_memory_total_mib=32768, cpu_ceiling_pct=100
    )
    assert result["llm_server"]["cpus"] == 8 - HOST_CPU_FLOOR - OMNIROUTE_CPU_FIXED


def test_cpu_ceiling_floor_defaults_to_20_pct_when_unspecified():
    result = compute_resource_limits(
        host_cpu_count=24, host_memory_total_mib=32768, cpu_ceiling_pct=0.001
    )
    assert result["llm_server"]["cpus"] == round(24 * 20 / 100)


def test_cpu_ceiling_floors_at_the_configured_value_instead_of_rounding_to_zero():
    result = compute_resource_limits(
        host_cpu_count=24,
        host_memory_total_mib=32768,
        cpu_ceiling_pct=0.001,
        cpu_ceiling_floor_pct=60,
    )
    assert result["llm_server"]["cpus"] == round(24 * 60 / 100)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_resources.py -v -k cpu_ceiling`
Expected: FAIL with `TypeError: compute_resource_limits() got an unexpected keyword argument 'cpu_ceiling_pct'`

- [ ] **Step 3: Implement the CPU ceiling**

Replace the body of `compute_resource_limits` in `pylib/resources.py`:

```python
def compute_resource_limits(
    host_cpu_count: int,
    host_memory_total_mib: int,
    memory_ceiling_pct: float = 100,
    memory_ceiling_floor_pct: float = 20,
    cpu_ceiling_pct: float = 100,
    cpu_ceiling_floor_pct: float = 20,
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
    # See the comment above the previous version of this function for why a
    # percentage-of-total floor is used instead of a bare pct: it can never
    # be a no-op on any host, unlike a fixed-MiB (or, for CPU, fixed-core)
    # floor could be.
    memory_ceiling_mib = max(
        round(host_memory_total_mib * memory_ceiling_floor_pct / 100),
        round(host_memory_total_mib * memory_ceiling_pct / 100),
    )
    llm_server_memory_mib = min(
        host_memory_total_mib - memory_floor_mib, memory_ceiling_mib
    )
    # MoE CPU-offload inference (see docs/superpowers/specs/2026-08-10-
    # ornith-35b-moe-incorporation-design.md) measured sustained ~44% CPU
    # utilization across all threads. Uncapped, llm-server previously got
    # every core minus the fixed floor -- fine for GPU-resident dense
    # models, but not something a heavier future MoE config should be able
    # to grow into unbounded. Same floor/ceiling shape as the memory cap
    # above, for the same reason: a percentage-of-total floor can't
    # round a valid pct down to zero cores.
    cpu_ceiling = max(
        round(host_cpu_count * cpu_ceiling_floor_pct / 100),
        round(host_cpu_count * cpu_ceiling_pct / 100),
    )
    llm_server_cpus = min(host_cpu_count - cpu_floor, cpu_ceiling)
    return {
        "host_cpu_floor": HOST_CPU_FLOOR,
        "host_memory_floor_mib": HOST_MEMORY_FLOOR_MIB,
        "omniroute": {
            "cpus": OMNIROUTE_CPU_FIXED,
            "memory_mib": OMNIROUTE_MEMORY_FIXED_MIB,
        },
        "llm_server": {
            "cpus": llm_server_cpus,
            "memory_mib": llm_server_memory_mib,
        },
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_resources.py -v`
Expected: PASS (all tests, including every pre-existing test — `test_llm_server_gets_remainder_after_host_floor_and_omniroute` and `test_exact_floor_plus_one_is_feasible` must still pass unchanged, since the new parameters default to values that reproduce the old uncapped behavior).

- [ ] **Step 5: Commit**

```bash
git add pylib/resources.py tests/test_resources.py
git commit -m "feat(resources): add a CPU core ceiling mirroring the RAM ceiling"
```

---

### Task 3: `pylib/config.py` — schema for `n_cpu_moe` and CPU ceiling settings

**Files:**
- Modify: `pylib/config.py`
- Test: `tests/test_cli.py` (config validation tests live here — search for `def test_.*memory_ceiling` to find the existing pattern to mirror)

**Interfaces:**
- Consumes: nothing new.
- Produces: `migrate_config()` now sets `resources.llm_server.cpu_ceiling_pct` (default 60) and `resources.llm_server.cpu_ceiling_floor_pct` (default 20). `validate_config()` now validates both, plus an optional per-model `n_cpu_moe` field.

- [ ] **Step 1: Update the three existing tests that assert the exact `llm_server` dict shape**

`tests/test_config.py` has three tests that assert `migrated["resources"]["llm_server"] == {...}` with an exact dict literal — adding new `migrate_config` defaults will break all three unless updated in the same commit:

- `test_migrate_config_adds_default_resources_section` (around line 561)
- `test_migrate_config_preserves_existing_resources_values` (around line 572)
- `test_migrate_config_adds_default_resources_section_when_gpu_absent` (around line 720)

In each of the three, add two keys to the expected dict literal:

```python
        "cpu_ceiling_pct": 60,
        "cpu_ceiling_floor_pct": 20,
```

right after the existing `"memory_ceiling_floor_pct": 30,` (or `15`/other overridden value, for `test_migrate_config_preserves_existing_resources_values` if it overrides that field — check each one's actual expected value before editing, don't blindly paste `30`).

- [ ] **Step 2: Write the new failing tests**

Add to `tests/test_config.py`, following the exact style of the existing `test_migrate_config_adds_default_memory_ceiling_pct` / `test_validate_config_rejects_invalid_memory_ceiling_pct` pair (around line 583 and 605) — this file's `make_cfg(**overrides)` helper (defined at the top of the file) builds a full minimal valid config, with `resources.llm_server` already containing `cpus`/`memory_mib`/`memory_ceiling_pct`/`memory_ceiling_floor_pct`, and two models (`gemma4` enabled, `ornith` disabled):

```python
def test_migrate_config_adds_default_cpu_ceiling_pct():
    cfg = make_cfg()
    migrated = config_module.migrate_config(copy.deepcopy(cfg))
    assert migrated["resources"]["llm_server"]["cpu_ceiling_pct"] == 60
    assert migrated["resources"]["llm_server"]["cpu_ceiling_floor_pct"] == 20


def test_migrate_config_preserves_existing_cpu_ceiling_pct():
    cfg = make_cfg(
        resources={
            "llm_server": {
                "cpus": 6,
                "memory_mib": 28672,
                "memory_ceiling_pct": 46,
                "memory_ceiling_floor_pct": 30,
                "cpu_ceiling_pct": 80,
                "cpu_ceiling_floor_pct": 25,
            }
        }
    )
    migrated = config_module.migrate_config(copy.deepcopy(cfg))
    assert migrated["resources"]["llm_server"]["cpu_ceiling_pct"] == 80
    assert migrated["resources"]["llm_server"]["cpu_ceiling_floor_pct"] == 25


@pytest.mark.parametrize("value", [0, -1, 101, float("inf"), "lots", True])
def test_validate_config_rejects_invalid_cpu_ceiling_pct(value):
    cfg = make_cfg(
        resources={"llm_server": {"cpus": 0, "memory_mib": 0, "cpu_ceiling_pct": value}}
    )
    errors = validate_config(cfg)
    assert any("cpu_ceiling_pct" in error for error in errors)


@pytest.mark.parametrize("value", [0, -1, 101, float("inf"), "lots", True])
def test_validate_config_rejects_invalid_cpu_ceiling_floor_pct(value):
    cfg = make_cfg(
        resources={
            "llm_server": {"cpus": 0, "memory_mib": 0, "cpu_ceiling_floor_pct": value}
        }
    )
    errors = validate_config(cfg)
    assert any("cpu_ceiling_floor_pct" in error for error in errors)


def test_validate_config_accepts_n_cpu_moe_as_non_negative_int():
    cfg = make_cfg()
    cfg["models"][0]["n_cpu_moe"] = 28
    assert validate_config(cfg) == []


@pytest.mark.parametrize("value", [-1, True, "28", 1.5])
def test_validate_config_rejects_invalid_n_cpu_moe(value):
    cfg = make_cfg()
    cfg["models"][0]["n_cpu_moe"] = value
    errors = validate_config(cfg)
    assert any("n_cpu_moe" in error for error in errors)
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_config.py -v -k "cpu_ceiling or n_cpu_moe"`
Expected: FAIL — `cpu_ceiling_pct` KeyError/assertion failures, and `n_cpu_moe` producing no validation errors yet. The three updated exact-dict tests from Step 1 also FAIL at this point (expected dict now has keys `migrate_config` doesn't produce yet) — that's correct, they turn green in the same run as everything else once Step 4 lands.

- [ ] **Step 4: Implement the schema changes**

In `pylib/config.py`'s `migrate_config()`, extend the `llm_server_resources` block:

```python
            llm_server_resources.setdefault("cpus", 0)
            llm_server_resources.setdefault("memory_mib", 0)
            llm_server_resources.setdefault("memory_ceiling_pct", 46)
            llm_server_resources.setdefault("memory_ceiling_floor_pct", 30)
            llm_server_resources.setdefault("cpu_ceiling_pct", 60)
            llm_server_resources.setdefault("cpu_ceiling_floor_pct", 20)
```

In `validate_config()`, extend the `llm_server_resources` validation block (right after the existing `memory_ceiling_floor_pct` check):

```python
                cpu_ceiling_pct = llm_server_resources.get("cpu_ceiling_pct", 60)
                if isinstance(cpu_ceiling_pct, bool) or not (
                    _finite_number(cpu_ceiling_pct) and 0 < cpu_ceiling_pct <= 100
                ):
                    errors.append(
                        "resources.llm_server.cpu_ceiling_pct must be a "
                        "finite number greater than 0 and at most 100"
                    )
                cpu_ceiling_floor_pct = llm_server_resources.get(
                    "cpu_ceiling_floor_pct", 20
                )
                if isinstance(cpu_ceiling_floor_pct, bool) or not (
                    _finite_number(cpu_ceiling_floor_pct)
                    and 0 < cpu_ceiling_floor_pct <= 100
                ):
                    errors.append(
                        "resources.llm_server.cpu_ceiling_floor_pct must be a "
                        "finite number greater than 0 and at most 100"
                    )
```

In the per-model validation loop (right after the `client_max_output_tokens` / `ctx_size` block), add:

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

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_config.py tests/test_cli.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add pylib/config.py tests/test_config.py
git commit -m "feat(config): add n_cpu_moe model field and cpu_ceiling_pct schema"
```

---

### Task 4: `llmenv.py` — split MoE weight cost and cross-check RAM feasibility

**Files:**
- Modify: `llmenv.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `pylib.gguf.moe_expert_offload_mib` (Task 1), `pylib.resources.compute_resource_limits`'s new `cpu_ceiling_pct`/`cpu_ceiling_floor_pct` params (Task 2), `n_cpu_moe`/`cpu_ceiling_pct`/`cpu_ceiling_floor_pct` config fields (Task 3).
- Produces: `_model_costs()` now includes a `"ram_weights_mib"` key per model (0 for non-MoE models). `cmd_budget()`'s output JSON gains `"ram_required_mib"` and `"ram_available_mib"` keys, and now reports `feasible: false` with an added remedy when the resident model's CPU-offloaded RAM need exceeds the computed `resources.llm_server.memory_mib` — even though `start.sh`/`setup.sh` don't change, they already gate on `feasible`/`remedies` from this same JSON, so this failure mode is caught automatically.

- [ ] **Step 1: Update the existing `_model_costs` test for the new key**

`tests/test_cli.py::test_model_costs_round_weights_up_and_report_cache_components` currently asserts the returned cost dict has no `ram_weights_mib` key. Update its expected dict to include it:

```python
    assert llmenv._model_costs(cfg, tmp_path) == [
        {
            "alias": "gemma",
            "weights_mib": 2,
            "ram_weights_mib": 0,
            "full_kv_mib": 0,
            "swa_kv_mib": 1,
            "kv_mib": 1,
        }
    ]
```

- [ ] **Step 2: Write the new failing tests**

Add to `tests/test_cli.py`:

```python
def test_model_costs_splits_weights_for_moe_offload(tmp_path, monkeypatch):
    import llmenv

    model_path = tmp_path / "moe.gguf"
    model_path.write_bytes(b"x" * (10 * 1024 * 1024))  # 10 MiB total file
    cfg = {
        "runtime": {"ubatch_size": 512, "cache_type_k": "f16", "cache_type_v": "f16"},
        "models": [
            {
                "alias": "moe",
                "enabled": True,
                "file": model_path.name,
                "ctx_size": 4096,
                "n_cpu_moe": 2,
            }
        ],
    }
    metadata = {
        "general.architecture": "qwen35moe",
        "qwen35moe.block_count": 1,
        "qwen35moe.expert_count": 8,
        "qwen35moe.attention.head_count_kv": 1,
        "qwen35moe.attention.key_length": 1,
        "qwen35moe.attention.value_length": 1,
        "qwen35moe.full_attention_interval": 1,
    }
    monkeypatch.setattr(llmenv, "read_gguf_header", lambda path: {"metadata": metadata})
    monkeypatch.setattr(
        llmenv, "moe_expert_offload_mib", lambda path, metadata, n: 3
    )

    result = llmenv._model_costs(cfg, tmp_path)
    assert result[0]["ram_weights_mib"] == 3
    assert result[0]["weights_mib"] == 10 - 3


def test_cmd_budget_reports_infeasible_when_moe_ram_exceeds_the_ceiling(
    tmp_path, monkeypatch, capsys
):
    import llmenv

    cfg = yaml.safe_load(write_test_config(tmp_path).read_text())
    cfg["resources"] = {
        "llm_server": {
            "memory_ceiling_pct": 10,
            "memory_ceiling_floor_pct": 10,
            "cpu_ceiling_pct": 60,
            "cpu_ceiling_floor_pct": 20,
        }
    }
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
    monkeypatch.setattr(llmenv, "load_config", lambda path: cfg)
    monkeypatch.setattr(llmenv, "detect", lambda: facts)
    monkeypatch.setattr(
        llmenv, "host_resources", lambda: {"cpu_count": 24, "memory_total_mib": 32768}
    )
    monkeypatch.setattr(
        llmenv,
        "_model_costs",
        lambda config, path: [
            {"alias": "a", "weights_mib": 100, "ram_weights_mib": 100000, "kv_mib": 0}
        ],
    )

    result = llmenv.cmd_budget(SimpleNamespace(config="cfg", models_dir="models"))
    output = json.loads(capsys.readouterr().out)

    assert result == 1
    assert output["feasible"] is False
    assert output["ram_required_mib"] == 100000
    assert any(
        "cpu_ceiling_pct" not in r and "memory_ceiling_pct" in r
        for r in output["remedies"]
    )
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -v -k "model_costs or cmd_budget_reports_infeasible_when_moe"`
Expected: FAIL — `ram_weights_mib` missing from `_model_costs` output; `moe_expert_offload_mib` not yet imported/used; `cmd_budget` doesn't yet emit `ram_required_mib`.

- [ ] **Step 4: Implement**

In `llmenv.py`, add to the import from `pylib.gguf`:

```python
from pylib.gguf import GgufError, kv_geometry, moe_expert_offload_mib, read_gguf_header, validate_gguf
```

Replace `_model_costs`:

```python
def _model_costs(cfg: dict[str, Any], models_dir: Path) -> list[dict[str, Any]]:
    runtime = cfg["runtime"]
    costs = []
    for model in enabled_models(cfg):
        path = models_dir / model["file"]
        metadata = read_gguf_header(path)["metadata"]
        geometry = kv_geometry(metadata)
        weights_mib = math.ceil(path.stat().st_size / (1024 * 1024))
        ram_weights_mib = 0
        n_cpu_moe = model.get("n_cpu_moe")
        if n_cpu_moe:
            ram_weights_mib = moe_expert_offload_mib(path, metadata, n_cpu_moe)
            weights_mib -= ram_weights_mib
        costs.append(
            {
                "alias": model["alias"],
                "weights_mib": weights_mib,
                "ram_weights_mib": ram_weights_mib,
                **kv_cache_components_mib(
                    geometry,
                    model["ctx_size"],
                    runtime["ubatch_size"],
                    runtime["cache_type_k"],
                    runtime["cache_type_v"],
                ),
            }
        )
    return costs
```

Replace `cmd_budget`'s body (keep the existing GPU/pci lookup and `compute_budget` call unchanged; add the RAM cross-check after `result["models_max"] = cfg["runtime"]["models_max"]`):

```python
def cmd_budget(args: argparse.Namespace) -> int:
    cfg = require_valid_config(load_config(Path(args.config)))
    facts = detect()
    pci = cfg["gpu"]["pci_address"]
    if not pci:
        return fail(
            "gpu.pci_address is not set. Run 'make setup' to choose a GPU, or copy "
            "a pci_address from 'llmenv.py detect' into the config."
        )
    gpu = next((g for g in facts["gpus"] if g["pci_address"] == pci), None)
    if gpu is None:
        detected = [g["pci_address"] for g in facts["gpus"]]
        return fail(f"configured GPU {pci} not present; detected: {detected}")

    compositor_used = (
        gpu["vram_used_mib"]
        if facts["compositor_render_node"] == gpu["render_node"]
        else 0
    )
    runtime = cfg["runtime"]
    result = compute_budget(
        vram_total_mib=gpu["vram_total_mib"],
        compositor_used_mib=compositor_used,
        reserve_floor_mib=cfg["gpu"]["reserve_floor_mib"],
        model_costs=_model_costs(cfg, Path(args.models_dir)),
        models_max=runtime["models_max"],
        cache_type_k=runtime["cache_type_k"],
        cache_type_v=runtime["cache_type_v"],
        vram_budget_ceiling_mib=cfg["gpu"].get("vram_budget_ceiling_mib") or None,
    )
    result["compositor_on_this_gpu"] = compositor_used > 0
    result["models_max"] = cfg["runtime"]["models_max"]

    llm_server_resources = cfg.get("resources", {}).get("llm_server", {})
    host = host_resources()
    resource_limits = compute_resource_limits(
        host["cpu_count"],
        host["memory_total_mib"],
        llm_server_resources.get("memory_ceiling_pct", 46),
        llm_server_resources.get("memory_ceiling_floor_pct", 30),
        llm_server_resources.get("cpu_ceiling_pct", 60),
        llm_server_resources.get("cpu_ceiling_floor_pct", 20),
    )
    ram_required_mib = sum(
        model.get("ram_weights_mib", 0) for model in result["resident_models"]
    )
    ram_available_mib = resource_limits["llm_server"]["memory_mib"]
    result["ram_required_mib"] = ram_required_mib
    result["ram_available_mib"] = ram_available_mib
    if ram_required_mib > ram_available_mib:
        result["feasible"] = False
        needed_pct = math.ceil(ram_required_mib / host["memory_total_mib"] * 100)
        result.setdefault("remedies", [])
        result["remedies"].append(
            "resources.llm_server.memory_ceiling_pct is too low for this "
            f"model's CPU-offloaded MoE experts ({ram_required_mib} MiB needed, "
            f"{ram_available_mib} MiB available); raise it to at least {needed_pct}%"
        )
    return emit(result, 0 if result["feasible"] else 1)
```

`host_resources` is already imported (`from pylib.detect import ... host_resources ...`); `compute_resource_limits` is already imported.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -v`
Expected: PASS — including every pre-existing `cmd_budget`/`_model_costs` test. None of them set `n_cpu_moe` or `ram_weights_mib` in their mocked `_model_costs` return values, so `model.get("ram_weights_mib", 0)` resolves to `0` for every resident model and `ram_required_mib` is always `0` — the new RAM check can never trip regardless of what the real `host_resources()` (uncalled-out, genuinely reading this machine's `/proc`) returns. No changes to any pre-existing test are needed.

- [ ] **Step 6: Commit**

```bash
git add llmenv.py tests/test_cli.py
git commit -m "feat(budget): split MoE weight cost and cross-check RAM feasibility"
```

---

### Task 5: `pylib/presets.py` — emit `--n-cpu-moe`

**Files:**
- Modify: `pylib/presets.py`
- Test: `tests/test_presets.py`

**Interfaces:**
- Consumes: `n_cpu_moe` model field (Task 3's schema).
- Produces: `render_presets()` emits `n-cpu-moe = <n>` in a model's `presets.ini` section when `model.get("n_cpu_moe")` is truthy.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_presets.py`:

```python
def test_n_cpu_moe_emitted_when_configured():
    cfg = copy.deepcopy(CFG)
    cfg["models"][1]["n_cpu_moe"] = 28  # ornith
    text = render_presets(cfg, "/models", "Vulkan0")
    parsed = parse(text)
    assert parsed["ornith"]["n-cpu-moe"] == "28"
    assert "n-cpu-moe" not in parsed["gemma4"]


def test_n_cpu_moe_omitted_when_not_configured():
    text = render_presets(CFG, "/models", "Vulkan0")
    parsed = parse(text)
    assert "n-cpu-moe" not in parsed["gemma4"]
    assert "n-cpu-moe" not in parsed["ornith"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_presets.py -v -k n_cpu_moe`
Expected: FAIL — `KeyError: 'n-cpu-moe'` on the first test (never emitted yet). Second test already passes trivially; keep it anyway as a regression guard.

- [ ] **Step 3: Implement**

In `pylib/presets.py`'s `render_presets`, inside the `for model in enabled_models(cfg):` loop, right after building `section`:

```python
        section = {
            "model": str(Path(models_dir) / model["file"]),
            "ctx-size": str(model["ctx_size"]),
            "n-gpu-layers": str(model["n_gpu_layers"]),
        }
        if model.get("n_cpu_moe"):
            section["n-cpu-moe"] = str(model["n_cpu_moe"])
        sampling = model.get("sampling", {})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_presets.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pylib/presets.py tests/test_presets.py
git commit -m "feat(presets): emit --n-cpu-moe when a model configures it"
```

---

### Task 6: `make prune` — opt-in destructive cleanup that also removes models

**Files:**
- Create: `scripts/prune.sh`
- Modify: `Makefile`
- Test: `tests/test_shell.py`

**Interfaces:**
- Consumes: `tools/lib.sh`'s `MODELS_DIR`, `LLM_ENV_ASSUME_YES` convention (same as `scripts/clean.sh`).
- Produces: `make prune` — calls `scripts/clean.sh`, then removes everything under `$MODELS_DIR`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_shell.py` (mirror the stub-command helper pattern from the existing `clean.sh` tests just above/below `run_cleanup_with_stubs` — reuse `_mock_command` for `systemctl`/`yq`, and a `podman` stub that logs calls to `$CALLS`, exactly as those tests already do):

```python
def test_prune_lists_model_count_and_size_before_confirming(tmp_path: pathlib.Path) -> None:
    commands = tmp_path / "bin"
    commands.mkdir()
    for name in ("systemctl", "yq", "podman", "numfmt"):
        _mock_command(commands, name)
    (commands / "numfmt").write_text("#!/usr/bin/bash\necho '2KB'\n")

    home = tmp_path / "home"
    models_dir = tmp_path / "models"
    models_dir.mkdir()
    (models_dir / "a.gguf").write_bytes(b"x" * 1000)
    (models_dir / "b.gguf").write_bytes(b"y" * 1000)

    environment = os.environ | {
        "HOME": str(home),
        "LLM_ENV_MODELS_DIR": str(models_dir),
        "LLM_ENV_ASSUME_YES": "0",
        "PATH": f"{commands}:/usr/bin:/bin",
    }
    result = subprocess.run(
        ["/usr/bin/bash", "scripts/prune.sh"],
        cwd=ROOT,
        env=environment,
        input="no\n",
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "2 downloaded model file(s)" in result.stdout
    assert (models_dir / "a.gguf").exists()  # aborted -- nothing removed


def test_prune_removes_models_and_runs_clean_after_confirmation(
    tmp_path: pathlib.Path,
) -> None:
    commands = tmp_path / "bin"
    commands.mkdir()
    for name in ("systemctl", "yq", "numfmt"):
        _mock_command(commands, name)
    calls = tmp_path / "calls"
    podman = commands / "podman"
    podman.write_text("#!/usr/bin/bash\nprintf 'podman %s\\n' \"$*\" >> \"$CALLS\"\n")
    podman.chmod(podman.stat().st_mode | stat.S_IXUSR)

    home = tmp_path / "home"
    models_dir = tmp_path / "models"
    models_dir.mkdir()
    (models_dir / "a.gguf").write_bytes(b"x" * 1000)

    environment = os.environ | {
        "CALLS": str(calls),
        "HOME": str(home),
        "LLM_ENV_MODELS_DIR": str(models_dir),
        "LLM_ENV_ASSUME_YES": "1",
        "PATH": f"{commands}:/usr/bin:/bin",
    }
    result = subprocess.run(
        ["/usr/bin/bash", "scripts/prune.sh"],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert not (models_dir / "a.gguf").exists()
    assert models_dir.exists()  # directory itself survives, only contents removed
    assert "podman compose" in calls.read_text()  # clean.sh ran underneath


def test_prune_handles_a_missing_models_dir_without_error(tmp_path: pathlib.Path) -> None:
    commands = tmp_path / "bin"
    commands.mkdir()
    for name in ("systemctl", "yq", "podman", "numfmt"):
        _mock_command(commands, name)

    home = tmp_path / "home"
    environment = os.environ | {
        "HOME": str(home),
        "LLM_ENV_MODELS_DIR": str(tmp_path / "does-not-exist"),
        "LLM_ENV_ASSUME_YES": "1",
        "PATH": f"{commands}:/usr/bin:/bin",
    }
    result = subprocess.run(
        ["/usr/bin/bash", "scripts/prune.sh"],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "0 downloaded model file(s)" in result.stdout
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_shell.py -v -k test_prune`
Expected: FAIL with "No such file or directory: scripts/prune.sh" (the script doesn't exist yet).

- [ ] **Step 3: Implement `scripts/prune.sh`**

```bash
#!/usr/bin/env bash
# prune.sh — remove everything clean.sh removes, plus all downloaded models.
set -euo pipefail
# shellcheck disable=SC1091 # Resolved from this script at runtime.
# shellcheck source=../tools/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/../tools/lib.sh"

require_cmd numfmt

model_count=0
model_bytes=0
if [ -d "$MODELS_DIR" ]; then
    while IFS= read -r -d '' file; do
        model_count=$((model_count + 1))
        size="$(stat -c %s "$file" 2>/dev/null || echo 0)"
        model_bytes=$((model_bytes + size))
    done < <(find "$MODELS_DIR" -maxdepth 1 -type f -print0)
fi
model_human="$(numfmt --to=iec --suffix=B "$model_bytes")"

echo "This removes everything 'make clean' removes, PLUS:"
echo "  ${model_count} downloaded model file(s) in ${MODELS_DIR} (${model_human})"
echo "This cannot be undone — models must be re-downloaded to use again."
if [ "${LLM_ENV_ASSUME_YES:-0}" = "1" ]; then
    confirm=yes
else
    read -rp "Proceed? (yes/no) " confirm
fi
[ "$confirm" = "yes" ] || { echo "Aborted."; exit 1; }

LLM_ENV_ASSUME_YES=1 bash "$(dirname "${BASH_SOURCE[0]}")/clean.sh"
if [ -d "$MODELS_DIR" ]; then
    rm -rf "${MODELS_DIR:?}"/*
fi
log_info "pruned ${model_count} model file(s) (${model_human})"
```

```bash
chmod +x scripts/prune.sh
```

In `Makefile`, add `prune` to the `.PHONY` line (first line):

```makefile
.PHONY: help prerequisites dev-setup setup setup-local-llm-agents start stop restart check-setup check-server check-with-agents benchmark \
        key-reset show-secrets enable-boot disable-boot status gpu-status logs validate test clean prune
```

And add the target, right after `clean:`:

```makefile
prune:
	@bash tools/run-target.sh prune -- bash scripts/prune.sh
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_shell.py -v -k test_prune`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/prune.sh Makefile tests/test_shell.py
git commit -m "feat(prune): add make prune to clean plus remove downloaded models"
```

---

### Task 7: `models.yml.example`, README, and architecture docs

**Files:**
- Modify: `models.yml.example`
- Modify: `README.md`
- Modify: `.agents/architecture.md`

**Interfaces:**
- Consumes: all prior tasks' schema (`n_cpu_moe`, `cpu_ceiling_pct`/`cpu_ceiling_floor_pct`).
- Produces: nothing consumed by later tasks — this is the last task.

- [ ] **Step 1: Update `models.yml.example`**

Change the existing `ornith` entry's `vram_budget` and `ctx_size`:

```yaml
  - alias: ornith
    label: Ornith 1.0
    parameters: 9B
    quantization: Q4_K_M
    enabled: false
    file: ornith-1.0-9b-Q4_K_M.gguf
    url: https://huggingface.co/deepreinforce-ai/Ornith-1.0-9B-GGUF/resolve/main/ornith-1.0-9b-Q4_K_M.gguf
    size_bytes: 5600000000
    vram_budget: 55%
    ctx_size: 262144
    client_max_output_tokens: 8192
    n_gpu_layers: 99
```

Add a new entry after it:

```yaml
  - alias: ornith-35b
    label: Ornith 1.0 35B (MoE)
    parameters: 35B (3B active)
    quantization: Q4_K_M
    enabled: false
    file: ornith-1.0-35b-Q4_K_M.gguf
    url: https://huggingface.co/ornith-ai/Ornith-1.0-35B-GGUF/resolve/main/ornith-1.0-35b-Q4_K_M.gguf
    size_bytes: 21155768832
    vram_budget: 85%
    ctx_size: 262144
    client_max_output_tokens: 8192
    n_gpu_layers: 99
    n_cpu_moe: 28
```

Change the `resources.llm_server` block's `memory_ceiling_pct` and add `cpu_ceiling_pct`:

```yaml
resources:
  llm_server:
    cpus: 0
    memory_mib: 0
    memory_ceiling_pct: 60
    memory_ceiling_floor_pct: 30
    cpu_ceiling_pct: 60
    cpu_ceiling_floor_pct: 20
```

- [ ] **Step 2: Update `README.md`**

Replace this paragraph (search for `Clean setup maps` in the Configuration section):

> Clean setup maps `gemma4` to yuxinlu1's Agentic Gemma 4 12B v2 Q4_K_M
> build. Gemma and Ornith each receive one 131,072-token context and request slot
> with Q5_1 K/V caches. Pi and OpenCode advertise up to 8,192 output tokens,
> so reserving the full output allowance leaves a nominal 122,880 tokens for the
> prompt and history. All tokens still share the same slot. Setup reports an
> explicit VRAM-budget failure instead of shrinking context or offloading layers.

with:

```markdown
Clean setup maps `gemma4` to yuxinlu1's Agentic Gemma 4 12B v2 Q4_K_M
build with a 131,072-token context. Ornith 1.0 9B uses its full native
262,144-token context (confirmed via its `config.json`'s
`max_position_embeddings`). Both get one request slot with Q5_1 K/V caches.
Pi and OpenCode advertise up to 8,192 output tokens, so reserving the full
output allowance leaves the rest of each model's context for prompt and
history. All tokens still share the same slot. Setup reports an explicit
VRAM-budget failure instead of shrinking context or offloading layers.

An optional `ornith-35b` entry adds Ornith 1.0 35B, a mixture-of-experts
model (35B total / ~3B active parameters per token) too large to fit fully
in most consumer GPUs' VRAM. It uses llama.cpp's `--n-cpu-moe` flag (set via
the model's `n_cpu_moe` field) to keep routed-expert weights for its first N
transformer blocks in host RAM while the rest of the model stays on GPU —
`pylib/gguf.py` computes exactly how many bytes that is from the GGUF's own
tensor layout, and `make setup`/`make start` refuse to start if
`resources.llm_server.memory_ceiling_pct` isn't high enough to hold it, the
same way they already refuse an infeasible VRAM budget.
```

Add a new bullet to the `## Configuration` section's model-management command list, right after the existing `enable`/`disable` examples:

```markdown
Reclaim disk space from downloaded models (they are NOT removed by
`make clean`) with:

```bash
make prune
```
```

- [ ] **Step 3: Update `.agents/architecture.md`**

Search for the existing VRAM budget invariants section (the one documenting `vram_budget_ceiling_mib`/`vram_budget_ceiling_pct`) and add a paragraph after it:

```markdown
- MoE models (`n_cpu_moe` set on a model entry) split their weight cost
  between VRAM and host RAM: `pylib/gguf.py::moe_expert_offload_mib()`
  reads the GGUF's own tensor byte offsets to compute exactly how many MiB
  of routed-expert tensors `--n-cpu-moe` sends to CPU for that model's first
  `n_cpu_moe` transformer blocks (confirmed empirically that `--n-cpu-moe N`
  offloads ascending block indices, not descending). `llmenv budget` cross-
  checks that RAM figure against `resources.llm_server.memory_mib` and
  reports the same kind of explicit, remedied infeasibility as the existing
  VRAM check — never silently corrected.
- `resources.llm_server.cpu_ceiling_pct` (default 60%) caps how many host
  CPU cores `llm-server` can use, the same way `memory_ceiling_pct` caps
  RAM — added because MoE CPU-offload inference genuinely uses multiple
  cores (measured ~44% of all threads sustained), unlike GPU-resident dense
  models which barely touch the CPU.
```

- [ ] **Step 4: Validate the config still passes schema validation**

Run: `uv run python -c "
import yaml
from pylib.config import validate_config
cfg = yaml.safe_load(open('models.yml.example'))
cfg['version'] = 1
errors = validate_config(cfg)
assert errors == [], errors
print('models.yml.example is valid')
"`

Expected: prints `models.yml.example is valid` with no assertion error. (This isn't a real config — it's missing `server`/`gpu`/`runtime` top-level values that only exist in a generated config — if this fails on missing sections unrelated to this task's changes, that's pre-existing and fine; the check that matters here is that no error mentions `n_cpu_moe`, `cpu_ceiling_pct`, `vram_budget`, or `ctx_size`.)

- [ ] **Step 5: Run the full test suite and lints**

Run: `uv run pytest tests/ -q && uv run ruff check . && shellcheck scripts/prune.sh`
Expected: all green, zero findings.

- [ ] **Step 6: Commit**

```bash
git add models.yml.example README.md .agents/architecture.md
git commit -m "docs: add ornith-35b to models.yml.example, document MoE offload and make prune"
```
