# Ornith 35B MoE Incorporation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Ornith 1.0 35B (a MoE model) as a selectable `models.yml` entry with CPU-offloaded routed experts (`--n-cpu-moe`) tuned to fit this host's VRAM/RAM, correct the 9B's `ctx_size` to its real native context, and add a general CPU-core ceiling to `resources.llm_server` alongside the existing RAM ceiling — reaching every call site that currently ships an uncapped `llm-server` container (`llmenv.py cmd_resources`, consumed by `setup/setup.sh`, persisted into `models.yml`, and read by `pylib/compose.py`).

**Architecture:** Extend `pylib/gguf.py` with tensor-level size parsing so `llmenv.py`'s model-cost calculation can split a MoE model's weight footprint into a VRAM part and a CPU/RAM part (mirroring the existing GGUF-metadata-driven VRAM budget). Extend `pylib/resources.py` with a CPU-core percentage ceiling (mirroring the existing RAM ceiling), and thread it through **both** `cmd_budget` (for the new RAM feasibility cross-check) and `cmd_resources` (the call `setup/setup.sh` actually persists into `models.yml`'s `resources.llm_server.cpus`, which `pylib/compose.py` writes into the container's `cpus:` limit). `make setup` and `make start` are both changed to exit nonzero when the RAM/VRAM budget is infeasible, instead of only warning.

**Design deviation from the sibling design doc (acknowledged, not accidental):** `docs/superpowers/specs/2026-08-10-ornith-35b-moe-incorporation-design.md` Components 2-3 assign the RAM/VRAM weight split to `pylib/budget.py` and the RAM feasibility check (raising `ResourceError`) to `pylib/resources.py`. This plan instead adds tensor parsing to `pylib/gguf.py` (matching that file's existing sole responsibility: reading GGUF-derived facts) and does the split + the RAM feasibility check in `llmenv.py::_model_costs`/`cmd_budget`, reporting `feasible: false` + a `remedies` entry — mirroring how `compute_budget()` already reports the *existing* VRAM budget (returns `feasible`/`remedies`; never raises) rather than introducing a second, inconsistent failure style via a raised exception from `pylib/resources.py`. `pylib/resources.py` itself is touched only for the CPU ceiling (Task 2), which *does* still raise `ResourceError` for the pre-existing "host too small for the fixed floors" case — that check is orthogonal to this plan's new RAM-vs-model-weights check and is left exactly as it already behaves.

**Tech stack:** Pure Python (stdlib `struct`, `re`, `math`) for the new GGUF tensor parsing — no new dependencies. Uses llama.cpp's existing `--n-cpu-moe` server flag.

## Global Constraints

- `runtime.models_max` stays `1`.
- Never silently correct an infeasible config — every new failure mode (RAM ceiling too low for a MoE model's CPU-offloaded experts, `n_cpu_moe` set on a non-MoE model) fails explicitly with an actionable remedy, matching the existing `pylib/budget.py` remedies pattern.
- `make clean` must keep downloaded models (already true — do not regress). `make prune` (new, this plan) is the only command that removes them, and only after an explicit confirmation and a safety check on the resolved path (see Task 6).
- **Confirmed by direct empirical testing this session** (see design doc `docs/superpowers/specs/2026-08-10-ornith-35b-moe-incorporation-design.md`): `--n-cpu-moe N` offloads the **first** N transformer blocks' routed-expert tensors to CPU (block indices `0..N-1`), leaving blocks `N..block_count-1` on GPU. Verified via `llama-server -v` load logs showing `tensor blk.N.ffn_*_exps.weight ... buffer type overridden to Vulkan_Host` for offloaded blocks and no such line for GPU-resident ones.
- Recommended values from the design doc's measurements (this host: RX 9070 XT, 16304 MiB VRAM, 30GB RAM, 24 CPU threads): Ornith 35B `quantization: Q4_K_M`, `n_cpu_moe: 28`, `ctx_size: 262144`, `vram_budget: 85%`; Ornith 9B `ctx_size: 262144` (corrected from 131072), `vram_budget: 55%`; `resources.llm_server.memory_ceiling_pct: 60`, new `resources.llm_server.cpu_ceiling_pct: 60`.
- Routed-expert tensor names follow the exact pattern `blk.<N>.ffn_<gate|up|down>_exps.<weight|scale|input_scale>` — the parser must match only this shape (not any `ffn_*_exps.*`), and must never match `*_shexp*` (shared expert, always GPU-resident, not part of `--n-cpu-moe`'s offload) or `*_inp*` (the routing gate itself, also never offloaded).
- Per `AGENTS.md`: after editing any `.py` file, the mandated verification is `make validate && make test`; after editing any `.sh` file, it's `make validate`. Python is invoked only as `uv run llmenv.py <subcommand>` — never as an ad hoc `uv run python -c "..."`. Every verification step below uses these exact commands (not a bare `pytest`/`ruff`/`shellcheck` invocation), including the "run to see it fail" steps, since editing a test file is itself a `.py` edit this rule applies to.
- `resources.llm_server` gates both `make setup` and `make start`: an infeasible VRAM or RAM budget makes both exit nonzero with the remedies printed, never a warning that lets setup "complete" against a config that can't actually run.

---

### Task 1: `pylib/gguf.py` — tensor-level size parsing for MoE offload budgeting

**Files:**
- Modify: `pylib/gguf.py`
- Test: `tests/test_gguf.py`

**Interfaces:**
- Consumes: nothing new (uses existing `_read_exact`, `_read_string`, `_read_value`, `_skip_exact`, `GgufError`, `GGUF_MAGIC` already in the file).
- Produces: `tensor_sizes(path: Path) -> dict[str, int]` (tensor name → on-disk byte size, alignment-validated) and `moe_expert_offload_mib(path: Path, metadata: dict[str, Any], n_cpu_moe: int) -> int` — both consumed by Task 4's `llmenv.py` changes. Also exports `EXPERT_TENSOR_RE` (used directly by this task's regex-contract tests, and available to any future caller that needs to classify a tensor name without opening the file).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_gguf.py` (the existing `write_fake_gguf` helper only writes a metadata-only file with zero tensors — these new tests need actual tensor-info entries and tensor data bytes, so add a second fixture builder alongside it). Tensor byte sizes below (512/320/224) are deliberately chosen as multiples of both 16 and 32 so the same fixture works unmodified for both the default-alignment and non-default-alignment tests, and are declared as 4-byte-per-element F32 tensors whose `dims` product actually matches the declared byte count (128/80/56 elements respectively) — a prior draft of this fixture used dims that implied 4x the declared bytes, which is not how a real GGUF file is laid out and is fixed here:

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


ROUTED_EXPERT_BYTES = 512  # 128 F32 elements; multiple of 16 and 32
SHARED_EXPERT_BYTES = 320  # 80 F32 elements
NON_EXPERT_BYTES = 224     # 56 F32 elements


def write_fake_moe_gguf(
    path: Path, *, n_cpu_moe_blocks: int = 3, alignment: int = 32
) -> Path:
    """A minimal qwen35moe-arch GGUF with tensor-info entries for
    n_cpu_moe_blocks blocks: each block has a routed-expert tensor
    (ROUTED_EXPERT_BYTES), a shared-expert tensor (SHARED_EXPERT_BYTES, must
    never be offloaded), and a non-expert tensor (NON_EXPERT_BYTES). Declares
    `general.alignment` explicitly (defaulting to GGUF's own default, 32) so
    tests can exercise a non-default value. Tensor byte sizes are multiples
    of every alignment this fixture is used with, so offsets stay aligned
    without needing separate inter-tensor padding logic.
    """
    kvs = [
        _kv_string("general.architecture", "qwen35moe"),
        _kv_uint32("general.alignment", alignment),
        _kv_uint32("qwen35moe.block_count", n_cpu_moe_blocks),
        _kv_uint32("qwen35moe.expert_count", 8),
        _kv_uint32("qwen35moe.attention.head_count_kv", 2),
        _kv_uint32("qwen35moe.attention.key_length", 32),
        _kv_uint32("qwen35moe.attention.value_length", 32),
        _kv_uint32("qwen35moe.full_attention_interval", 1),
    ]
    kv_bytes = b"".join(kvs)

    T_F32 = 0
    tensors: list[tuple[str, int, int]] = []
    offset = 0
    for block in range(n_cpu_moe_blocks):
        for name, size in (
            (f"blk.{block}.ffn_down_exps.weight", ROUTED_EXPERT_BYTES),
            (f"blk.{block}.ffn_down_shexp.weight", SHARED_EXPERT_BYTES),
            (f"blk.{block}.attn_norm.weight", NON_EXPERT_BYTES),
        ):
            tensors.append((name, size, offset))
            offset += size

    tensor_info_bytes = b"".join(
        _tensor_info(name, 1, [size // 4], T_F32, tensor_offset)
        for name, size, tensor_offset in tensors
    )

    header = (
        b"GGUF"
        + struct.pack("<I", 3)
        + struct.pack("<QQ", len(tensors), len(kvs))
    )
    # Body up to (but not including) tensor data must land on an
    # `alignment`-byte boundary for real GGUF; this fixture pads to that
    # boundary so the alignment logic under test is exercised, not skipped.
    pre_data = header + kv_bytes + tensor_info_bytes
    pad = (-len(pre_data)) % alignment
    tensor_data = b"\x00" * offset
    path.write_bytes(pre_data + b"\x00" * pad + tensor_data)
    return path


def test_tensor_sizes_computed_from_offset_deltas(tmp_path):
    from pylib.gguf import tensor_sizes

    path = write_fake_moe_gguf(tmp_path / "moe.gguf", n_cpu_moe_blocks=2)
    sizes = tensor_sizes(path)
    assert sizes["blk.0.ffn_down_exps.weight"] == ROUTED_EXPERT_BYTES
    assert sizes["blk.0.ffn_down_shexp.weight"] == SHARED_EXPERT_BYTES
    assert sizes["blk.0.attn_norm.weight"] == NON_EXPERT_BYTES
    assert sizes["blk.1.ffn_down_exps.weight"] == ROUTED_EXPERT_BYTES


def test_tensor_sizes_honors_a_non_default_general_alignment(tmp_path):
    from pylib.gguf import tensor_sizes

    path = write_fake_moe_gguf(tmp_path / "moe16.gguf", n_cpu_moe_blocks=1, alignment=16)
    sizes = tensor_sizes(path)
    assert sizes["blk.0.ffn_down_exps.weight"] == ROUTED_EXPERT_BYTES


def test_tensor_sizes_rejects_a_tensor_offset_that_violates_alignment(tmp_path):
    from pylib.gguf import GgufError, tensor_sizes

    kvs = [
        _kv_string("general.architecture", "llama"),
        _kv_uint32("llama.block_count", 1),
    ]
    # Offset 3 is not a multiple of the default 32-byte alignment -- a real
    # GGUF writer never produces this; a corrupt or hand-crafted file might.
    tensor_info_bytes = _tensor_info("weight", 1, [1], 0, 3)
    header = b"GGUF" + struct.pack("<I", 3) + struct.pack("<QQ", 1, len(kvs))
    body = b"".join(kvs) + tensor_info_bytes
    path = tmp_path / "misaligned.gguf"
    path.write_bytes(header + body + b"\x00" * 64)

    with pytest.raises(GgufError, match="alignment"):
        tensor_sizes(path)


def test_tensor_sizes_rejects_duplicate_tensor_offsets(tmp_path):
    from pylib.gguf import GgufError, tensor_sizes

    kvs = [
        _kv_string("general.architecture", "llama"),
        _kv_uint32("llama.block_count", 1),
    ]
    # Both offsets are 0 -- a multiple of the default 32-byte alignment, so
    # this is not caught by the existing alignment check. A real GGUF writer
    # never emits two tensors at the same offset; a corrupt or
    # hand-crafted file might, and naive offset-delta sizing would silently
    # compute a 0-byte size for one of them instead of rejecting the file.
    tensor_info_bytes = _tensor_info("a", 1, [8], 0, 0) + _tensor_info("b", 1, [8], 0, 0)
    header = b"GGUF" + struct.pack("<I", 3) + struct.pack("<QQ", 2, len(kvs))
    body = b"".join(kvs) + tensor_info_bytes
    path = tmp_path / "duplicate.gguf"
    path.write_bytes(header + body + b"\x00" * 64)

    with pytest.raises(GgufError, match="duplicate or out-of-order"):
        tensor_sizes(path)


def test_tensor_sizes_rejects_an_offset_beyond_the_tensor_data_span(tmp_path):
    from pylib.gguf import GgufError, tensor_sizes

    kvs = [
        _kv_string("general.architecture", "llama"),
        _kv_uint32("llama.block_count", 1),
    ]
    # Offset 1024 is a multiple of the default 32-byte alignment (passes the
    # alignment check) but the file has no 1024 bytes of tensor data after
    # the header -- a truncated or corrupt file. Without an explicit check,
    # `file_size - data_start - offset` goes negative and tensor_sizes()
    # would silently return a negative size instead of raising.
    tensor_info_bytes = _tensor_info("weight", 1, [1], 0, 1024)
    header = b"GGUF" + struct.pack("<I", 3) + struct.pack("<QQ", 1, len(kvs))
    body = b"".join(kvs) + tensor_info_bytes
    path = tmp_path / "truncated.gguf"
    path.write_bytes(header + body + b"\x00" * 64)

    with pytest.raises(GgufError, match="exceeds the tensor data span"):
        tensor_sizes(path)


def test_moe_expert_offload_mib_sums_only_offloaded_blocks_routed_experts(tmp_path):
    from pylib.gguf import moe_expert_offload_mib

    path = write_fake_moe_gguf(tmp_path / "moe.gguf", n_cpu_moe_blocks=3)
    metadata = read_gguf_header(path)["metadata"]

    # n_cpu_moe=2 offloads blocks 0 and 1's routed experts only
    # (512 + 512 = 1024 bytes), never the shared-expert or non-expert
    # tensors, and never block 2 (kept on GPU).
    result = moe_expert_offload_mib(path, metadata, n_cpu_moe=2)
    assert result == 1  # ceil(1024 / 1048576) == 1


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


@pytest.mark.parametrize(
    ("name", "expected_match"),
    [
        ("blk.0.ffn_gate_exps.weight", True),
        ("blk.0.ffn_up_exps.scale", True),
        ("blk.0.ffn_down_exps.input_scale", True),
        ("blk.12.ffn_down_exps.weight", True),
        ("blk.0.ffn_gate_exps.bias", False),  # unsupported suffix -- llama.cpp doesn't offload it
        ("blk.0.ffn_down_shexp.weight", False),  # shared expert -- always GPU-resident
        ("blk.0.ffn_gate_inp.weight", False),  # routing gate -- not a routed-expert tensor
        ("blk.0.attn_q_exps.weight", False),  # not gate/up/down
        ("blk.0.ffn_norm.weight", False),  # not an expert tensor at all
    ],
)
def test_expert_tensor_regex_matches_only_the_declared_tensor_contract(name, expected_match):
    from pylib.gguf import EXPERT_TENSOR_RE

    assert bool(EXPERT_TENSOR_RE.match(name)) is expected_match
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `make test`
Expected: FAIL — `make test` runs the full suite (`uv run pytest tests/ -v` per `tools/test.sh`) and exits nonzero because `tests/test_gguf.py` fails to collect: `ImportError: cannot import name 'tensor_sizes'` (and similarly for `moe_expert_offload_mib`, `EXPERT_TENSOR_RE`). Every other test file's tests still pass; only `tests/test_gguf.py` is red.

- [ ] **Step 3: Implement `tensor_sizes` and `moe_expert_offload_mib`**

Add near the top of `pylib/gguf.py` (after the existing imports):

```python
import math
import re
```

Add as new public functions, after `read_gguf_header`:

```python
# GGUF pads the tensor-data section (and, per the format spec, each
# individual tensor's offset) to `general.alignment` bytes -- 32 by default.
# A file that overrides this key must have that override honored, or the
# computed data-start boundary (and therefore every tensor size derived from
# it) would be wrong for that file.
DEFAULT_ALIGNMENT = 32

EXPERT_TENSOR_RE = re.compile(
    r"^blk\.(\d+)\.ffn_(?:gate|up|down)_exps\.(?:weight|scale|input_scale)$"
)


def _alignment(metadata: dict[str, Any]) -> int:
    value = metadata.get("general.alignment", DEFAULT_ALIGNMENT)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise GgufError(f"general.alignment must be a positive integer, got {value!r}")
    return value


def tensor_sizes(path: Path) -> dict[str, int]:
    """Map each tensor name to its on-disk byte size (including any
    inter-tensor alignment padding GGUF itself already applied).

    Sizes are computed from offset deltas between consecutive tensors
    (sorted by their declared offset). GGUF stores each tensor's byte offset
    directly and requires every offset to be a multiple of
    `general.alignment`, so the gap to the next tensor's offset is exact
    regardless of quantization type -- no per-ggml-type size formula is
    needed, and misaligned offsets (corrupt or hand-crafted files) are
    rejected up front rather than silently mis-sized.
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

        metadata: dict[str, Any] = {}
        for _ in range(kv_count):
            key = _read_string(fh)
            (type_code,) = struct.unpack("<I", _read_exact(fh, 4))
            metadata[key] = _read_value(fh, type_code)
        alignment = _alignment(metadata)

        infos: list[tuple[str, int]] = []
        for _ in range(tensor_count):
            name = _read_string(fh)
            (n_dims,) = struct.unpack("<I", _read_exact(fh, 4))
            _skip_exact(fh, 8 * n_dims)  # dims: n_dims x uint64
            _skip_exact(fh, 4)  # ggml tensor type: uint32
            (offset,) = struct.unpack("<Q", _read_exact(fh, 8))
            if offset % alignment != 0:
                raise GgufError(
                    f"tensor {name!r} offset {offset} is not a multiple of "
                    f"general.alignment ({alignment}); GGUF requires every "
                    "tensor's data to start on an alignment boundary"
                )
            infos.append((name, offset))

        data_start = fh.tell()

    if data_start % alignment != 0:
        data_start += alignment - (data_start % alignment)

    infos.sort(key=lambda item: item[1])

    # The alignment check above only rejects an individual offset that
    # isn't a multiple of `alignment` -- it says nothing about offsets
    # colliding or going backwards relative to each other. A corrupt or
    # hand-crafted file can still declare two tensors at the same offset
    # (every offset here already passed the alignment check, e.g. two
    # tensors both at offset 0), which the offset-delta sizing below would
    # silently turn into a 0-byte size for one of them instead of an error.
    for index in range(len(infos) - 1):
        if infos[index][1] >= infos[index + 1][1]:
            raise GgufError(
                f"tensor {infos[index + 1][0]!r} offset {infos[index + 1][1]} "
                "is not strictly greater than the preceding tensor's offset "
                f"({infos[index][1]}); duplicate or out-of-order tensor "
                "offsets indicate a corrupt or hand-crafted GGUF file"
            )

    # Similarly, the last tensor's size is computed against the end of the
    # file rather than another tensor's offset -- a truncated file (or one
    # with a corrupt final offset) can declare an offset past the actual
    # tensor-data span, which would make `file_size - data_start - offset`
    # negative instead of raising.
    data_span = file_size - data_start
    if infos and infos[-1][1] > data_span:
        raise GgufError(
            f"tensor {infos[-1][0]!r} offset {infos[-1][1]} exceeds the "
            f"tensor data span ({data_span} bytes available after the "
            "header); the file is truncated or its offsets are corrupt"
        )

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
    `blk.<i>.ffn_<gate|up|down>_exps.<weight|scale|input_scale>` for block
    index i < n_cpu_moe. Shared-expert tensors (`*_shexp*`), the routing
    gate (`*_inp*`), and non-expert tensors are never included -- they stay
    GPU-resident regardless of `--n-cpu-moe`.

    Always validates that the model actually has routed experts (raises
    GgufError otherwise), even when n_cpu_moe is 0 -- a config author who
    sets `n_cpu_moe: 0` on a dense model gets the same explicit failure as
    one who sets any positive value, rather than a silent no-op.

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

Run: `make validate && make test`
Expected: PASS (all tests, including every pre-existing test in `tests/test_gguf.py` — `test_read_header_returns_version_and_metadata` etc. — this step must not regress them). `make validate` runs shellcheck (unaffected, no `.sh` changed) and `ruff check llmenv.py pylib tests` (must be clean against the new code and tests).

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
- Produces: `compute_resource_limits(..., cpu_ceiling_pct: float = 100, cpu_ceiling_floor_pct: float = 20)` — two new optional keyword parameters. Return shape unchanged (`llm_server.cpus` is now capped, same key). This function's two call sites (`llmenv.py::cmd_resources` and `cmd_budget`) are updated in Task 4 to actually pass the configured values through — this task only adds the capability.

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


def test_cpu_ceiling_never_rounds_down_to_zero_on_a_small_host():
    """Unlike the memory ceiling (MiB values are always large enough that a
    valid 0-100% pct can never round to 0), CPU core counts are small whole
    numbers -- on a host with only 1 usable core after the fixed floor, both
    a tiny cpu_ceiling_pct AND a tiny cpu_ceiling_floor_pct can independently
    round to 0 (round(4 * 1 / 100) == 0), which compose.py then treats as
    "no cpus: limit at all" (fully uncapped), not "cap at 0 cores"."""
    result = compute_resource_limits(
        host_cpu_count=4,  # cpu_floor is 3 (HOST_CPU_FLOOR=2 + OMNIROUTE_CPU_FIXED=1)
        host_memory_total_mib=32768,
        cpu_ceiling_pct=1,
        cpu_ceiling_floor_pct=1,
    )
    assert result["llm_server"]["cpus"] == 1


@pytest.mark.parametrize("cpu_ceiling_pct", [1, 5, 10, 19])
def test_cpu_ceiling_never_rounds_down_to_zero_across_tiny_percentages(cpu_ceiling_pct):
    result = compute_resource_limits(
        host_cpu_count=4,
        host_memory_total_mib=32768,
        cpu_ceiling_pct=cpu_ceiling_pct,
        cpu_ceiling_floor_pct=1,
    )
    assert result["llm_server"]["cpus"] >= 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `make test`
Expected: FAIL — `tests/test_resources.py`'s new `cpu_ceiling` tests fail with `TypeError: compute_resource_limits() got an unexpected keyword argument 'cpu_ceiling_pct'`; every other test file still passes.

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
    # above, EXCEPT for the explicit `max(1, ...)`: memory is measured in
    # MiB, where a percentage-of-total floor can never round down to 0 on
    # any real host, but CPU core counts are small whole numbers -- on a
    # 4-core host, round(4 * 1 / 100) == 0, so both a tiny cpu_ceiling_pct
    # AND a tiny cpu_ceiling_floor_pct can independently round to 0. Without
    # this floor, compose.py's `if cpus:` treats a computed 0 as "omit the
    # limit" (fully uncapped) rather than "cap at 0 cores" -- silently
    # defeating the whole point of this ceiling on a small host.
    cpu_ceiling = max(
        1,
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

Run: `make validate && make test`
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
- Test: `tests/test_config.py` (all config validation and migration tests live here — `make_cfg(**overrides)`, defined at the top of this file, builds a full minimal valid config with `resources.llm_server` already containing `cpus`/`memory_mib`/`memory_ceiling_pct`/`memory_ceiling_floor_pct`, and two models: `gemma4` enabled, `ornith` disabled)

**Interfaces:**
- Consumes: nothing new.
- Produces: `migrate_config()` now sets `resources.llm_server.cpu_ceiling_pct` (default 60) and `resources.llm_server.cpu_ceiling_floor_pct` (default 20). `validate_config()` now validates both, plus an optional per-model `n_cpu_moe` field (non-negative integer, `0` is a valid, meaningful value — see Task 4).

- [ ] **Step 1: Update the three existing tests that assert the exact `llm_server` dict shape**

`tests/test_config.py` has three tests that assert `migrated["resources"]["llm_server"] == {...}` with an exact dict literal — adding new `migrate_config` defaults will break all three unless updated in the same commit. Update each to the exact expected dict shown:

`test_migrate_config_adds_default_resources_section` (`tests/test_config.py:561-569`):

```python
def test_migrate_config_adds_default_resources_section():
    cfg = make_cfg()
    migrated = config_module.migrate_config(copy.deepcopy(cfg))
    assert migrated["resources"]["llm_server"] == {
        "cpus": 0,
        "memory_mib": 0,
        "memory_ceiling_pct": 46,
        "memory_ceiling_floor_pct": 30,
        "cpu_ceiling_pct": 60,
        "cpu_ceiling_floor_pct": 20,
    }
```

`test_migrate_config_preserves_existing_resources_values` (`tests/test_config.py:572-580`) — its input override (`resources={"llm_server": {"cpus": 6, "memory_mib": 28672}}`) doesn't set either ceiling field, so both new keys still come from `migrate_config`'s defaults:

```python
def test_migrate_config_preserves_existing_resources_values():
    cfg = make_cfg(resources={"llm_server": {"cpus": 6, "memory_mib": 28672}})
    migrated = config_module.migrate_config(copy.deepcopy(cfg))
    assert migrated["resources"]["llm_server"] == {
        "cpus": 6,
        "memory_mib": 28672,
        "memory_ceiling_pct": 46,
        "memory_ceiling_floor_pct": 30,
        "cpu_ceiling_pct": 60,
        "cpu_ceiling_floor_pct": 20,
    }
```

`test_migrate_config_adds_default_resources_section_when_gpu_absent` (`tests/test_config.py:720-730`):

```python
def test_migrate_config_adds_default_resources_section_when_gpu_absent():
    """The resources default must not be skipped by the gpu early-return branch."""
    cfg = make_cfg()
    del cfg["gpu"]
    migrated = config_module.migrate_config(copy.deepcopy(cfg))
    assert migrated["resources"]["llm_server"] == {
        "cpus": 0,
        "memory_mib": 0,
        "memory_ceiling_pct": 46,
        "memory_ceiling_floor_pct": 30,
        "cpu_ceiling_pct": 60,
        "cpu_ceiling_floor_pct": 20,
    }
```

- [ ] **Step 2: Write the new failing tests**

Add to `tests/test_config.py`, following the exact style of the existing `test_migrate_config_adds_default_memory_ceiling_pct` / `test_validate_config_rejects_invalid_memory_ceiling_pct` pair (`tests/test_config.py:583` and `:605`):

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


def test_validate_config_accepts_n_cpu_moe_zero():
    """0 is a valid, meaningful value (see Task 4): it means 'no CPU
    offload', not 'field absent' -- the schema must not special-case it."""
    cfg = make_cfg()
    cfg["models"][0]["n_cpu_moe"] = 0
    assert validate_config(cfg) == []


@pytest.mark.parametrize("value", [-1, True, "28", 1.5])
def test_validate_config_rejects_invalid_n_cpu_moe(value):
    cfg = make_cfg()
    cfg["models"][0]["n_cpu_moe"] = value
    errors = validate_config(cfg)
    assert any("n_cpu_moe" in error for error in errors)
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `make test`
Expected: FAIL — `cpu_ceiling_pct` KeyError/assertion failures, and `n_cpu_moe` producing no validation errors yet. The three updated exact-dict tests from Step 1 also FAIL at this point (expected dict now has keys `migrate_config` doesn't produce yet) — that's correct, they turn green in the same run as everything else once Step 4 lands. Every other test file still passes.

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

Run: `make validate && make test`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add pylib/config.py tests/test_config.py
git commit -m "feat(config): add n_cpu_moe model field and cpu_ceiling_pct schema"
```

---

### Task 4: `llmenv.py` — split MoE weight cost, cross-check RAM feasibility, and wire the CPU ceiling into `cmd_resources`

**Files:**
- Modify: `llmenv.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `pylib.gguf.moe_expert_offload_mib` (Task 1), `pylib.resources.compute_resource_limits`'s new `cpu_ceiling_pct`/`cpu_ceiling_floor_pct` params (Task 2), `n_cpu_moe`/`cpu_ceiling_pct`/`cpu_ceiling_floor_pct` config fields (Task 3).
- Produces: `_model_costs()` now includes a `"ram_weights_mib"` key per model (0 for non-MoE models, and for MoE models with `n_cpu_moe` unset). `cmd_budget()`'s output JSON gains `"vram_feasible"` (the pre-existing VRAM-only verdict, preserved under its own name before the RAM check can flip the combined `"feasible"`), `"ram_feasible"`, `"ram_required_mib"`, `"ram_available_mib"`, and `"ram_shortfall_mib"` keys — a RAM-only failure must never be reported through the VRAM check's `shortfall_mib`/`available_mib`/`required_mib` fields, which stay VRAM-only. The RAM requirement is the SUM of `ram_weights_mib` across the top `runtime.models_max` enabled models ranked by `ram_weights_mib` (mirroring `compute_budget()`'s own `resident_models` selection, but ranked by RAM cost instead of VRAM cost) — not just the single largest, since `runtime.models_max` can be greater than 1 and every one of those top-ranked models can be concurrently resident; at `models_max == 1` (this plan's Global Constraint) this reduces to exactly the single largest, so the RAM check is not the same set as `resident_models` even in that case (a model cheap in VRAM but expensive in RAM can lose the VRAM ranking yet still top the RAM ranking). `cmd_resources()` now passes the configured `cpu_ceiling_pct`/`cpu_ceiling_floor_pct` through to `compute_resource_limits`, so the value `setup/setup.sh` and `scripts/start.sh` (Task 7) persist into `models.yml`'s `resources.llm_server.cpus` (and that `pylib/compose.py` writes into the container's `cpus:` limit) is actually capped.

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


def test_model_costs_calls_moe_offload_even_when_n_cpu_moe_is_zero(tmp_path, monkeypatch):
    """A model with n_cpu_moe explicitly set to 0 must still route through
    moe_expert_offload_mib -- that function validates the model actually has
    routed experts, which is how a dense model with a stray n_cpu_moe: 0
    gets caught, rather than silently ignored because 0 is falsy."""
    import llmenv

    model_path = tmp_path / "moe.gguf"
    model_path.write_bytes(b"x" * (10 * 1024 * 1024))
    cfg = {
        "runtime": {"ubatch_size": 512, "cache_type_k": "f16", "cache_type_v": "f16"},
        "models": [
            {
                "alias": "moe",
                "enabled": True,
                "file": model_path.name,
                "ctx_size": 4096,
                "n_cpu_moe": 0,
            }
        ],
    }
    metadata = {"general.architecture": "qwen35moe", "qwen35moe.expert_count": 8}
    monkeypatch.setattr(llmenv, "read_gguf_header", lambda path: {"metadata": metadata})
    calls = []
    monkeypatch.setattr(
        llmenv,
        "moe_expert_offload_mib",
        lambda path, metadata, n: calls.append(n) or 0,
    )

    result = llmenv._model_costs(cfg, tmp_path)
    assert calls == [0]
    assert result[0]["ram_weights_mib"] == 0


def test_model_costs_rejects_n_cpu_moe_zero_on_a_non_moe_model(tmp_path, monkeypatch):
    import llmenv

    model_path = tmp_path / "dense.gguf"
    model_path.write_bytes(b"x" * 1024)
    cfg = {
        "runtime": {"ubatch_size": 512, "cache_type_k": "f16", "cache_type_v": "f16"},
        "models": [
            {
                "alias": "dense",
                "enabled": True,
                "file": model_path.name,
                "ctx_size": 4096,
                "n_cpu_moe": 0,
            }
        ],
    }
    metadata = {
        "general.architecture": "llama",
        "llama.block_count": 1,
        "llama.attention.head_count_kv": 1,
        "llama.attention.key_length": 1,
        "llama.attention.value_length": 1,
    }
    monkeypatch.setattr(llmenv, "read_gguf_header", lambda path: {"metadata": metadata})

    with pytest.raises(llmenv.GgufError, match="no routed experts"):
        llmenv._model_costs(cfg, tmp_path)


def test_cmd_budget_reports_infeasible_with_an_achievable_remedy_percentage(
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
            {"alias": "a", "weights_mib": 100, "ram_weights_mib": 10000, "kv_mib": 0}
        ],
    )

    result = llmenv.cmd_budget(SimpleNamespace(config="cfg", models_dir="models"))
    output = json.loads(capsys.readouterr().out)

    # memory_ceiling_pct=10 of 32768 MiB = 3277 MiB available; 10000 MiB is
    # needed but 32768 - (host+omniroute floor) = 27648 MiB is the most this
    # host could ever provide -- so this must land in the "raise the pct"
    # branch, not the "impossible on this host" branch.
    assert result == 1
    assert output["feasible"] is False
    assert output["ram_required_mib"] == 10000
    # A RAM-only failure must not report itself through the VRAM check's
    # fields/verdict: the VRAM budget in this fixture is untouched and
    # fits, so vram_feasible stays True even though the combined
    # `feasible` is False, and ram_feasible/ram_shortfall_mib carry the
    # actual RAM-specific numbers rather than leaving the VRAM check's
    # shortfall_mib (0) to imply "short by 0 MiB".
    assert output["vram_feasible"] is True
    assert output["ram_feasible"] is False
    assert output["ram_shortfall_mib"] == 10000 - output["ram_available_mib"]
    assert any(
        "raise it to at least 31%" in remedy for remedy in output["remedies"]
    )


def test_cmd_budget_reports_infeasible_and_impossible_when_no_pct_could_ever_fit(
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
    assert output["vram_feasible"] is True
    assert output["ram_feasible"] is False
    assert output["ram_required_mib"] == 100000
    remedy = next(r for r in output["remedies"] if "memory_ceiling_pct" in r and "cpu_ceiling_pct" not in r)
    # Must never propose an impossible >100% ceiling.
    assert "%" not in remedy or all(
        int(token.rstrip("%")) <= 100
        for token in remedy.split()
        if token.rstrip("%,.").isdigit() and token.endswith("%")
    )
    assert "no resources.llm_server.memory_ceiling_pct value can fit" in remedy


def test_cmd_budget_catches_ram_infeasibility_from_a_model_the_vram_budget_would_not_pick(
    tmp_path, monkeypatch, capsys
):
    """resident_models (compute_budget's VRAM-ranked top models_max models)
    is the wrong set to sum/max RAM need over: a model that's cheap in VRAM
    (small weights_mib) but expensive in offloaded RAM (large
    ram_weights_mib) can lose the VRAM ranking to a big dense model yet
    still be an enabled model a user could select and run. The RAM check
    must cover every enabled model (compute_budget's full "models" list),
    not just the VRAM-ranked resident subset."""
    import llmenv

    cfg = yaml.safe_load(write_test_config(tmp_path).read_text())
    cfg["runtime"]["models_max"] = 1
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
            # Wins the VRAM ranking (huge weights_mib) but needs no RAM.
            {"alias": "dense", "weights_mib": 20000, "ram_weights_mib": 0, "kv_mib": 100},
            # Loses the VRAM ranking (tiny weights_mib) but needs huge RAM --
            # this is the model the old resident_models-only check would miss.
            {"alias": "moe", "weights_mib": 50, "ram_weights_mib": 100000, "kv_mib": 10},
        ],
    )

    result = llmenv.cmd_budget(SimpleNamespace(config="cfg", models_dir="models"))
    output = json.loads(capsys.readouterr().out)

    assert result == 1
    assert output["feasible"] is False
    assert output["vram_feasible"] is True
    assert output["ram_feasible"] is False
    assert output["ram_required_mib"] == 100000
    assert any("moe" in remedy for remedy in output["remedies"])


def test_cmd_budget_reports_infeasible_when_the_host_cant_reserve_the_fixed_floors(
    tmp_path, monkeypatch, capsys
):
    """A standalone `llmenv budget` call (not routed through `make setup`/
    `make start`, which both call `llmenv resources` directly and die on
    this exact ResourceError first) must not silently report
    "feasible": true just because compute_resource_limits() raised --
    that would contradict this plan's own Global Constraint against
    silently correcting (or silently ignoring) an infeasible config."""
    import llmenv

    cfg = yaml.safe_load(write_test_config(tmp_path).read_text())
    cfg["runtime"]["models_max"] = 1
    cfg["resources"] = {"llm_server": {}}
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
    # host_cpu_count=3 is at/below HOST_CPU_FLOOR (2) + OMNIROUTE_CPU_FIXED (1)
    # -- compute_resource_limits() raises ResourceError before returning.
    monkeypatch.setattr(
        llmenv, "host_resources", lambda: {"cpu_count": 3, "memory_total_mib": 32768}
    )
    monkeypatch.setattr(
        llmenv,
        "_model_costs",
        lambda config, path: [
            {"alias": "dense", "weights_mib": 50, "ram_weights_mib": 0, "kv_mib": 10},
        ],
    )

    result = llmenv.cmd_budget(SimpleNamespace(config="cfg", models_dir="models"))
    output = json.loads(capsys.readouterr().out)

    assert result == 1
    assert output["feasible"] is False
    assert output["ram_feasible"] is False
    assert output["ram_available_mib"] is None
    assert any(
        "cannot reserve the fixed" in remedy for remedy in output["remedies"]
    )


def test_cmd_budget_sums_ram_across_concurrently_resident_models(
    tmp_path, monkeypatch, capsys
):
    """runtime.models_max may legally be > 1 (nothing in the schema forbids
    it, even though this plan's Global Constraint keeps the shipped default
    at 1) -- when it is, every one of the top models_max models by RAM cost
    can be concurrently resident, so their ram_weights_mib must be SUMMED,
    not maxed. Two models that individually fit under the RAM ceiling can
    still blow it out together; a single-model max check would miss this
    entirely."""
    import llmenv

    cfg = yaml.safe_load(write_test_config(tmp_path).read_text())
    cfg["runtime"]["models_max"] = 2
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
            # Neither alone exceeds the ~3277 MiB memory_ceiling_pct=10
            # budget on this host, but both being concurrently resident
            # (models_max=2) sums to 4000 MiB, which does.
            {"alias": "moe-a", "weights_mib": 50, "ram_weights_mib": 2000, "kv_mib": 10},
            {"alias": "moe-b", "weights_mib": 50, "ram_weights_mib": 2000, "kv_mib": 10},
        ],
    )

    result = llmenv.cmd_budget(SimpleNamespace(config="cfg", models_dir="models"))
    output = json.loads(capsys.readouterr().out)

    assert result == 1
    assert output["feasible"] is False
    assert output["ram_feasible"] is False
    assert output["ram_required_mib"] == 4000
    assert any(
        "moe-a" in remedy and "moe-b" in remedy for remedy in output["remedies"]
    )


def test_resources_cpu_ceiling_caps_llm_server_cpus(tmp_path: Path):
    config = write_test_config(tmp_path)
    parsed = yaml.safe_load(config.read_text())
    llm_server_resources = parsed.setdefault("resources", {}).setdefault("llm_server", {})
    llm_server_resources["cpu_ceiling_pct"] = 10
    llm_server_resources["cpu_ceiling_floor_pct"] = 1
    config.write_text(yaml.safe_dump(parsed, sort_keys=False))

    result = run("resources", "--config", str(config))
    payload = json.loads(result.stdout)
    if result.returncode == 0:
        host_cpu_count = payload["host"]["cpu_count"]
        uncapped = host_cpu_count - payload["host_cpu_floor"] - payload["omniroute"]["cpus"]
        # Must match compute_resource_limits()'s own floor exactly (Task 2,
        # Step 3: `max(1, round(... floor_pct ...), round(... pct ...))`) --
        # on a small test host (e.g. a 4-5 core CI runner), the bare
        # percentage math below can round to 0, which the unfloored version
        # of this formula previously asserted, even though the real
        # implementation always returns at least 1.
        expected_ceiling = max(
            1, round(host_cpu_count * 1 / 100), round(host_cpu_count * 10 / 100)
        )
        expected_cpus = min(uncapped, expected_ceiling)
        # Proves the configured pct actually reached compute_resource_limits()
        # via cmd_resources(), not just via cmd_budget() (Task 4's whole point).
        assert payload["llm_server"]["cpus"] == expected_cpus
        # A strict "<" is impossible to satisfy on a host so small that
        # uncapped already equals the 1-core floor (e.g. host_cpu_count==4,
        # cpu_floor==3 -> uncapped==1, expected_cpus==1) -- assert the
        # ceiling actually took effect only when it could possibly differ.
        if expected_ceiling < uncapped:
            assert payload["llm_server"]["cpus"] < uncapped
    else:
        assert result.returncode == 1
        assert "error" in payload
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `make test`
Expected: FAIL — `ram_weights_mib` missing from `_model_costs` output; `moe_expert_offload_mib` not yet imported/used; `cmd_budget` doesn't yet emit `ram_required_mib`; `test_resources_cpu_ceiling_caps_llm_server_cpus` fails because `cmd_resources` doesn't yet pass `cpu_ceiling_pct`/`cpu_ceiling_floor_pct` through. Every other test file still passes.

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
        # `is not None`, not truthiness: n_cpu_moe: 0 is a valid, meaningful
        # config value (explicitly "no CPU offload"), and must still route
        # through moe_expert_offload_mib so a stray n_cpu_moe on a dense
        # model (which has no routed experts at all) is still rejected,
        # rather than silently skipped because 0 is falsy.
        if n_cpu_moe is not None:
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
    # compute_budget()'s own feasible/shortfall_mib/remedies describe only
    # the VRAM check above. Preserve that verdict under its own name before
    # the RAM cross-check below can flip the combined "feasible" -- a
    # RAM-only failure must never be reported through the VRAM check's
    # fields (shortfall_mib/available_mib/required_mib stay VRAM-only, and
    # would otherwise misleadingly read "short by 0 MiB").
    result["vram_feasible"] = result["feasible"]

    # The RAM check below must cover every enabled model that could become
    # concurrently resident, not just compute_budget()'s "resident_models"
    # (its top models_max models ranked by VRAM cost) -- a model cheap in
    # VRAM but expensive in CPU-offloaded RAM can lose that ranking to a big
    # dense model and still be an enabled, selectable model. Rank
    # `result["models"]` (every enabled model's cost) by ram_weights_mib
    # instead and sum the top models_max of them: runtime.models_max can
    # legally be greater than 1 (nothing in the schema forbids it, even
    # though this plan's Global Constraint keeps the shipped default at 1),
    # in which case that many models can be concurrently resident and their
    # RAM needs add up. At models_max == 1 this reduces to exactly the
    # single largest ram_weights_mib.
    models_max = cfg["runtime"]["models_max"]
    ram_ranked = sorted(
        result.get("models", []),
        key=lambda model: model.get("ram_weights_mib", 0),
        reverse=True,
    )
    ram_resident = ram_ranked[:models_max]
    ram_required_mib = sum(model.get("ram_weights_mib", 0) for model in ram_resident)
    ram_required_aliases = [
        model["alias"] for model in ram_resident if model.get("ram_weights_mib", 0) > 0
    ]
    result["ram_required_mib"] = ram_required_mib

    llm_server_resources = cfg.get("resources", {}).get("llm_server", {})
    host = host_resources()
    ram_available_mib = None
    resource_floor_error = None
    try:
        resource_limits = compute_resource_limits(
            host["cpu_count"],
            host["memory_total_mib"],
            llm_server_resources.get("memory_ceiling_pct", 46),
            llm_server_resources.get("memory_ceiling_floor_pct", 30),
            llm_server_resources.get("cpu_ceiling_pct", 60),
            llm_server_resources.get("cpu_ceiling_floor_pct", 20),
        )
        ram_available_mib = resource_limits["llm_server"]["memory_mib"]
    except ResourceError as exc:
        # `make setup` (Step 3 below) and `make start` (Step 5d below) both
        # call `llmenv resources` directly and die loudly on this exact
        # error before ever reaching this command in their normal
        # pipelines -- but `llmenv budget` is a standalone command a caller
        # can invoke directly (e.g. a bare CLI check, or a future script
        # that never calls `resources`). On a host that can't even reserve
        # the fixed CPU/RAM floors, silently skipping the RAM check here
        # would report "feasible": true for a host that can never actually
        # run llm-server at all, which is exactly the kind of "silently
        # correct an infeasible config" this plan's Global Constraints
        # forbid. Surface it as its own explicit RAM failure instead.
        resource_floor_error = str(exc)
    result["ram_available_mib"] = ram_available_mib

    ram_feasible = True
    ram_shortfall_mib = None
    if resource_floor_error is not None:
        ram_feasible = False
        result.setdefault("remedies", [])
        result["remedies"].append(
            f"host cannot reserve the fixed CPU/RAM floors for llm-server "
            f"at all: {resource_floor_error}; this is fatal regardless of "
            "n_cpu_moe or memory_ceiling_pct"
        )
    elif ram_required_mib and ram_available_mib is not None and ram_required_mib > ram_available_mib:
        ram_feasible = False
        ram_shortfall_mib = ram_required_mib - ram_available_mib
        result.setdefault("remedies", [])
        absolute_max_limits = compute_resource_limits(
            host["cpu_count"],
            host["memory_total_mib"],
            memory_ceiling_pct=100,
            memory_ceiling_floor_pct=llm_server_resources.get("memory_ceiling_floor_pct", 30),
            cpu_ceiling_pct=llm_server_resources.get("cpu_ceiling_pct", 60),
            cpu_ceiling_floor_pct=llm_server_resources.get("cpu_ceiling_floor_pct", 20),
        )
        absolute_max_mib = absolute_max_limits["llm_server"]["memory_mib"]
        # Built without ever putting a possessive "'s" directly after a
        # closing quote mark (i.e. never "'alias''s") -- that construction
        # reads as a doubled-quote typo. "of model 'alias'" / "of models
        # 'a', 'b'" instead.
        models_desc = (
            f"model '{ram_required_aliases[0]}'"
            if len(ram_required_aliases) == 1
            else "models " + ", ".join(f"'{alias}'" for alias in ram_required_aliases)
        )
        if ram_required_mib > absolute_max_mib:
            result["remedies"].append(
                f"no resources.llm_server.memory_ceiling_pct value can fit "
                f"the CPU-offloaded MoE experts of {models_desc} on this "
                f"host ({ram_required_mib} MiB needed, {absolute_max_mib} MiB "
                "is the most the host can ever provide llm-server after its "
                "fixed floor and OmniRoute's reservation); reduce "
                "n_cpu_moe, use a smaller quantization, add RAM, or lower "
                "runtime.models_max"
            )
        else:
            needed_pct = math.ceil(ram_required_mib / host["memory_total_mib"] * 100)
            result["remedies"].append(
                f"resources.llm_server.memory_ceiling_pct is too low for "
                f"the CPU-offloaded MoE experts of {models_desc} "
                f"({ram_required_mib} MiB needed, {ram_available_mib} MiB "
                f"available); raise it to at least {needed_pct}%"
            )
    result["ram_shortfall_mib"] = ram_shortfall_mib
    result["ram_feasible"] = ram_feasible
    result["feasible"] = result["vram_feasible"] and ram_feasible
    return emit(result, 0 if result["feasible"] else 1)
```

Replace `cmd_resources`:

```python
def cmd_resources(args: argparse.Namespace) -> int:
    cfg = require_valid_config(load_config(Path(args.config)))
    host = host_resources()
    llm_server_resources = cfg["resources"]["llm_server"]
    limits = compute_resource_limits(
        host["cpu_count"],
        host["memory_total_mib"],
        llm_server_resources["memory_ceiling_pct"],
        llm_server_resources["memory_ceiling_floor_pct"],
        llm_server_resources["cpu_ceiling_pct"],
        llm_server_resources["cpu_ceiling_floor_pct"],
    )
    return emit({"host": host, **limits})
```

`host_resources` and `ResourceError` are already imported (`from pylib.detect import ... host_resources ...`; `from pylib.resources import ResourceError, compute_resource_limits`).

- [ ] **Step 5: Run tests to verify they pass**

Run: `make validate && make test`
Expected: PASS — including every pre-existing `cmd_budget`/`_model_costs`/`cmd_resources` test. The three pre-existing `cmd_budget` tests that mock `compute_budget` to return bare dicts like `{"feasible": True}` (no `"models"` key) still pass: `result.get("models", [])` defaults to `[]`, `ram_ranked`/`ram_resident` are both `[]`, `ram_required_mib` is `0`, and the `if ram_required_mib and ...` guard short-circuits before any RAM check runs — so `result["resident_models"]` (which the earlier draft of this plan mistakenly read directly, `KeyError`-ing against these exact mocks) is never touched at all.

- [ ] **Step 6: Commit**

```bash
git add llmenv.py tests/test_cli.py
git commit -m "feat(budget): split MoE weight cost, cross-check RAM feasibility, and cap CPU via cmd_resources"
```

---

### Task 5: `pylib/presets.py` — emit `--n-cpu-moe`

**Files:**
- Modify: `pylib/presets.py`
- Test: `tests/test_presets.py`

**Interfaces:**
- Consumes: `n_cpu_moe` model field (Task 3's schema).
- Produces: `render_presets()` emits `n-cpu-moe = <n>` in a model's `presets.ini` section when `model.get("n_cpu_moe")` is truthy. (Unlike `_model_costs` in Task 4, this stays a truthiness check on purpose: `n_cpu_moe: 0` and "field absent" are both "pass no `--n-cpu-moe` flag to llama.cpp" from the server's point of view — the distinction that matters, "was this set on a model with no routed experts at all," is validated once, upstream, in `cmd_budget` before presets are ever rendered; see `scripts/start.sh`'s existing ordering, unchanged by this plan, which runs the budget check before rendering presets.)

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


def test_n_cpu_moe_omitted_when_zero():
    cfg = copy.deepcopy(CFG)
    cfg["models"][1]["n_cpu_moe"] = 0
    text = render_presets(cfg, "/models", "Vulkan0")
    parsed = parse(text)
    assert "n-cpu-moe" not in parsed["ornith"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `make test`
Expected: FAIL — `KeyError: 'n-cpu-moe'` on the first test (never emitted yet). The other two already pass trivially; keep them as regression guards.

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

Run: `make validate && make test`
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
- Modify: `setup/setup.sh` (Step 4 — leaves the `.llm-env-managed` marker `scripts/prune.sh` requires)
- Modify: `scripts/help.sh` (list `make prune`)
- Test: `tests/test_shell.py`

**Interfaces:**
- Consumes: `tools/lib.sh`'s `MODELS_DIR`, `HOME`, `REPO_DIR`, `LLM_ENV_ASSUME_YES` convention (same as `scripts/clean.sh`).
- Produces: `make prune` — validates `$MODELS_DIR` resolves to a plausible, bounded models directory (never `/`, `$HOME`, or the repository itself, at least 2 path segments deep, AND containing a `.llm-env-managed` marker file — see Step 3 below for why path shape alone isn't proof), calls `scripts/clean.sh`, then removes everything under `$MODELS_DIR` (including nested directories and dotfiles). `setup/setup.sh`'s Step 4 now touches that marker the first time it creates/uses `$MODELS_DIR`. `scripts/help.sh` lists `make prune` alongside every other target.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_shell.py` (mirror the stub-command helper pattern from the existing `clean.sh` tests just above/below `run_cleanup_with_stubs` — reuse `_mock_command` for `systemctl`/`yq`, and a `podman` stub that logs calls to `$CALLS`, exactly as those tests already do). Note the success-path test below creates `${HOME}/.config/llm-env/docker-compose.yml` before running — `scripts/clean.sh` only invokes `podman compose ... down` when that file exists (see `scripts/clean.sh:48`), so without it the "clean.sh ran underneath" assertion would trivially pass for the wrong reason (no `podman compose` call happening at all, not because prune correctly delegated to clean):

```python
def test_prune_lists_model_count_and_size_before_confirming(tmp_path: pathlib.Path) -> None:
    commands = tmp_path / "bin"
    commands.mkdir()
    for name in ("systemctl", "yq", "podman", "numfmt"):
        _mock_command(commands, name)
    (commands / "numfmt").write_text("#!/usr/bin/bash\necho '3KB'\n")

    home = tmp_path / "home"
    models_dir = tmp_path / "models"
    models_dir.mkdir()
    (models_dir / ".llm-env-managed").touch()  # proves this is llm-env's dir
    (models_dir / "a.gguf").write_bytes(b"x" * 1000)
    nested = models_dir / "nested"
    nested.mkdir()
    (nested / "b.gguf").write_bytes(b"y" * 1000)
    (models_dir / ".hidden.gguf").write_bytes(b"z" * 1000)

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
    # Counts nested and hidden files too, not just top-level entries, but
    # never the .llm-env-managed marker itself -- that's bookkeeping, not a
    # downloaded model.
    assert "3 downloaded model file(s)" in result.stdout
    assert (models_dir / "a.gguf").exists()  # aborted -- nothing removed
    assert (nested / "b.gguf").exists()


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
    compose_file = home / ".config/llm-env/docker-compose.yml"
    compose_file.parent.mkdir(parents=True)
    compose_file.write_text("services: {llm-server: {}}\n")

    models_dir = tmp_path / "models"
    models_dir.mkdir()
    (models_dir / ".llm-env-managed").touch()  # proves this is llm-env's dir
    (models_dir / "a.gguf").write_bytes(b"x" * 1000)
    nested = models_dir / "nested"
    nested.mkdir()
    (nested / "b.gguf").write_bytes(b"y" * 1000)
    (models_dir / ".hidden.gguf").write_bytes(b"z" * 1000)

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
    assert not nested.exists()  # nested directories are removed too
    assert not (models_dir / ".hidden.gguf").exists()  # dotfiles are removed too
    # The marker is removed along with everything else -- `make prune`
    # removes ALL of $MODELS_DIR's contents. The next `make setup` recreates
    # it (Step 4), so this is not a re-prune hazard.
    assert not (models_dir / ".llm-env-managed").exists()
    assert models_dir.exists()  # directory itself survives, only contents removed
    assert "podman compose" in calls.read_text()  # clean.sh actually ran, not skipped


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


def test_prune_refuses_to_run_against_the_repository_directory(tmp_path: pathlib.Path) -> None:
    """LLM_ENV_MODELS_DIR is operator-controlled; a value that resolves to
    the repository itself (or '/' or $HOME) must be rejected before
    anything is deleted, not passed straight to `rm -rf`."""
    commands = tmp_path / "bin"
    commands.mkdir()
    for name in ("systemctl", "yq", "podman", "numfmt"):
        _mock_command(commands, name)

    home = tmp_path / "home"
    environment = os.environ | {
        "HOME": str(home),
        "LLM_ENV_MODELS_DIR": str(ROOT),
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

    assert result.returncode != 0
    assert "refusing to prune" in result.stderr
    assert (ROOT / "scripts" / "prune.sh").exists()  # the repo itself must survive


def test_prune_refuses_a_directory_without_the_llm_env_managed_marker(
    tmp_path: pathlib.Path,
) -> None:
    """Path shape alone (not '/', $HOME, or $REPO_DIR, and at least 2 path
    segments deep) is not proof this is really llm-env's models directory --
    any pre-existing, unrelated directory at a plausible depth (e.g.
    /etc/ssh) would otherwise pass every check above and get recursively
    deleted. Require the marker `make setup` leaves behind in `$MODELS_DIR`
    the first time it creates/uses it (Step 3 below)."""
    commands = tmp_path / "bin"
    commands.mkdir()
    for name in ("systemctl", "yq", "podman", "numfmt"):
        _mock_command(commands, name)

    home = tmp_path / "home"
    models_dir = tmp_path / "some" / "unrelated" / "directory"
    models_dir.mkdir(parents=True)
    (models_dir / "a.gguf").write_bytes(b"x" * 1000)  # looks like a model, but unmanaged

    environment = os.environ | {
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

    assert result.returncode != 0
    assert "missing .llm-env-managed marker" in result.stderr
    assert (models_dir / "a.gguf").exists()  # refused before deleting anything
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `make test`
Expected: FAIL with "No such file or directory: scripts/prune.sh" (the script doesn't exist yet); every other test file still passes.

- [ ] **Step 3: Implement `scripts/prune.sh`**

```bash
#!/usr/bin/env bash
# prune.sh — remove everything clean.sh removes, plus all downloaded models.
set -euo pipefail
# shellcheck disable=SC1091 # Resolved from this script at runtime.
# shellcheck source=../tools/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/../tools/lib.sh"

require_cmd numfmt

resolved_models_dir=""
if [ -d "$MODELS_DIR" ]; then
    resolved_models_dir="$(cd "$MODELS_DIR" && pwd -P)"
    case "$resolved_models_dir" in
        "/" | "$HOME" | "$REPO_DIR")
            die "refusing to prune ${resolved_models_dir}: this looks like the filesystem root, the home directory, or the repository, not a models directory"
            ;;
    esac
    # Reject anything too shallow to plausibly be a dedicated models
    # directory (e.g. "/" has depth 0, "/home" has depth 1); the default,
    # ${HOME}/llm-workspace/models, has depth 3 or more.
    depth="$(printf '%s' "$resolved_models_dir" | tr -cd '/' | wc -c)"
    [ "$depth" -ge 2 ] \
        || die "refusing to prune ${resolved_models_dir}: path is too shallow to be a models directory"
    # Path shape alone (not /, $HOME, or $REPO_DIR, and deep enough) is
    # NOT proof this directory is actually the one llm-env manages -- any
    # pre-existing, unrelated directory at a plausible depth (e.g.
    # /etc/ssh) would otherwise pass every check above and get recursively
    # deleted. setup/setup.sh's Step 4 touches this marker the first time
    # it creates or uses $MODELS_DIR; require it here so prune only ever
    # deletes a directory llm-env itself created for this purpose.
    [ -f "${resolved_models_dir}/.llm-env-managed" ] \
        || die "refusing to prune ${resolved_models_dir}: missing .llm-env-managed marker (only a models directory created by 'make setup' is eligible; if this genuinely is your models directory, run: touch ${resolved_models_dir}/.llm-env-managed)"
fi

model_count=0
model_bytes=0
if [ -n "$resolved_models_dir" ]; then
    # -mindepth 1 with no -maxdepth: counts nested files and dotfiles too,
    # matching what the deletion step below actually removes. The
    # .llm-env-managed marker itself is bookkeeping, not a downloaded
    # model, so it's excluded from the count and size shown to the user.
    while IFS= read -r -d '' file; do
        model_count=$((model_count + 1))
        size="$(stat -c %s "$file" 2>/dev/null || echo 0)"
        model_bytes=$((model_bytes + size))
    done < <(find "$resolved_models_dir" -mindepth 1 -type f -not -name '.llm-env-managed' -print0)
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
if [ -n "$resolved_models_dir" ]; then
    # -delete (not `rm -rf .../*`): a glob misses dotfiles and requires a
    # separate `rm -rf` per top-level entry to also remove nested
    # directories cleanly; find -mindepth 1 -delete removes everything
    # under the directory, depth-first, in one pass, and leaves the
    # directory itself in place.
    find "$resolved_models_dir" -mindepth 1 -delete
fi
log_info "pruned ${model_count} model file(s) (${model_human})"
```

```bash
chmod +x scripts/prune.sh
```

- [ ] **Step 3b: Write the failing test for the setup marker and the help listing**

Add to `tests/test_shell.py`, near the other `run_setup_with_numbered_selection`-based tests and near `test_make_help_lists_check_with_agents`:

```python
def test_setup_creates_the_prune_marker_in_the_models_directory(
    tmp_path: pathlib.Path,
) -> None:
    """scripts/prune.sh (this task) refuses to run without this marker --
    setup must actually create it during Step 4's download, not just
    document the convention."""
    result, _, _ = run_setup_with_numbered_selection(tmp_path, "1\n1\n1\n")
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "models" / ".llm-env-managed").exists()


def test_make_help_lists_prune() -> None:
    assert "make prune" in (SCRIPT_DIR / "help.sh").read_text()
```

- [ ] **Step 3c: Run the tests to verify they fail**

Run: `make test`
Expected: FAIL — `test_setup_creates_the_prune_marker_in_the_models_directory` fails because `setup/setup.sh` doesn't yet touch the marker; `test_make_help_lists_prune` fails because `scripts/help.sh` doesn't yet mention `make prune`. Every other test file still passes.

- [ ] **Step 3d: Add the marker to `setup/setup.sh` and list `make prune` in `scripts/help.sh`**

In `setup/setup.sh`'s Step 4 (`Downloading models`), right after `mkdir -p "$MODELS_DIR"`:

```bash
mkdir -p "$MODELS_DIR"
# scripts/prune.sh (make prune) refuses to delete a directory without this
# marker -- proof it's actually the models directory llm-env created, not
# an unrelated existing path an operator's LLM_ENV_MODELS_DIR happens to
# point at.
touch "${MODELS_DIR}/.llm-env-managed"
```

In `scripts/help.sh`, add a line right after the existing `make clean` line:

```bash
echo "make prune         Remove config, unit, images, AND downloaded models (destructive)"
```

- [ ] **Step 3e: Run the tests to verify they pass**

Run: `make validate && make test`
Expected: PASS.

- [ ] **Step 3f: Commit**

```bash
git add setup/setup.sh scripts/help.sh tests/test_shell.py
git commit -m "feat(prune): mark the models directory as llm-env-managed and list make prune in help"
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

Run: `make validate && make test`
Expected: PASS. `make validate` shellchecks `scripts/prune.sh` along with every other `.sh` file.

- [ ] **Step 5: Commit**

```bash
git add scripts/prune.sh Makefile tests/test_shell.py
git commit -m "feat(prune): add make prune to clean plus safely remove downloaded models"
```

---

### Task 7: Gate `make setup` and `make start` on budget feasibility, add `ornith-35b` to `models.yml.example`, and update docs

**Files:**
- Modify: `setup/setup.sh`
- Modify: `scripts/start.sh`
- Modify: `models.yml.example`
- Modify: `README.md`
- Modify: `.agents/architecture.md`
- Test: `tests/test_shell.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: all prior tasks' schema (`n_cpu_moe`, `cpu_ceiling_pct`/`cpu_ceiling_floor_pct`) and `cmd_budget`'s `vram_feasible`/`ram_feasible`/`ram_shortfall_mib`/`remedies` output (Task 4).
- Produces: `make setup` exits nonzero when the VRAM/RAM budget is infeasible. `make start` gains a NEW "Computing resource limits" step (mirroring `setup/setup.sh`'s existing Step 8 exactly) that calls `llmenv resources` and persists its `cpus`/`memory_mib` into `models.yml` on every start, not just once at setup time — this closes two gaps together: (1) `compute_resource_limits`'s fixed-floor `ResourceError` (host too small to reserve `HOST_CPU_FLOOR`/`HOST_MEMORY_FLOOR_MIB`/OmniRoute's fixed allocation) is currently only ever surfaced by `make setup`, which a user might have run once, months ago, on different hardware or a different `resources.llm_server` config than what's persisted today -- `make start` previously never called `llmenv resources` at all and would launch straight from an infeasible or simply stale persisted `cpus`/`memory_mib`; (2) because `pylib/compose.py::render_compose()` reads `resources.llm_server.cpus`/`memory_mib` directly out of the config file at render time, re-persisting the freshly computed values immediately before `setup/render-unit.sh` runs guarantees the container that actually starts uses the SAME numbers `cmd_budget`'s RAM cross-check just validated against (both calls hit `compute_resource_limits` against the same host, moments apart, with the same configured ceiling/floor percentages) -- never a stale number left over from a previous `make setup` on different hardware. Nothing consumed by later tasks — this is the last task.

- [ ] **Step 1: Write the failing test for the setup gate**

`setup/setup.sh`'s Step 7 (`setup/setup.sh:132-141`) currently only `log_warn`s on an infeasible budget and continues to Step 8 — it must instead `die`, exactly like `scripts/start.sh` already does. Add to `tests/test_shell.py`, alongside the other `run_setup_with_numbered_selection`-based tests (the existing `uv` stub's `*' budget '*)` case at `tests/test_shell.py:537` always returns `{"available_mib":12000,"required_mib":10000}` with an implicit exit 0 today — this test overrides that case with an explicit nonzero exit to prove the gate fires):

```python
def test_setup_fails_when_the_budget_is_infeasible(tmp_path: pathlib.Path) -> None:
    """Step 7 must stop setup on an infeasible budget, the same way
    scripts/start.sh already stops `make start` -- a config that reports
    'available < required' must never reach Step 8 and be reported as
    'Setup complete'."""
    real_yq = shutil.which("yq")
    assert real_yq is not None

    commands = tmp_path / "bin"
    commands.mkdir()
    calls = tmp_path / "calls"
    for name in ("ip", "git", "shellcheck"):
        _mock_command(commands, name)
    curl = commands / "curl"
    curl.write_text("#!/usr/bin/bash\nprintf 'curl %s\\n' \"$*\" >> \"$CALLS\"\n")
    curl.chmod(curl.stat().st_mode | stat.S_IXUSR)

    uv = commands / "uv"
    uv.write_text(
        "#!/usr/bin/bash\n"
        "printf 'uv %s\\n' \"$*\" >> \"$CALLS\"\n"
        "case \"$*\" in\n"
        "  *' detect') printf '%s\\n' '{\"gpus\":[{\"card\":\"card0\",\"pci_address\":\"0000:03:00.0\",\"vram_total_mib\":16384,\"vram_used_mib\":2048,\"render_node\":\"renderD128\",\"connected_outputs\":[]}]}' ;;\n"
        "  *' models list') printf '%s\\n' '{\"models\":[{\"alias\":\"gemma4\",\"label\":\"Gemma 4\",\"parameters\":\"12B\",\"quantization\":\"Q4_K_M\",\"size_bytes\":7660000000,\"enabled\":true}]}' ;;\n"
        "  *' models select '*)\n"
        "    for arg in \"$@\"; do selected_alias=\"$arg\"; done\n"
        "    SELECTED_ALIAS=\"$selected_alias\" \"$REAL_YQ\" -i \\\n"
        "      '.models[] |= (.enabled = (.alias == strenv(SELECTED_ALIAS))) | .runtime.models_max = 1' \\\n"
        "      \"$CONFIG_PATH_TEST\"\n"
        "    printf '%s\\n' '{\"models_max\":1}' ;;\n"
        "  *' validate-gguf'*) printf '%s\\n' '{\"results\":[]}' ;;\n"
        "  *' budget '*)\n"
        "    printf '%s\\n' '{\"available_mib\":9000,\"required_mib\":12000,\"shortfall_mib\":3000,\"vram_feasible\":false,\"ram_feasible\":true,\"remedies\":[\"reduce ctx_size\"]}'\n"
        "    exit 1 ;;\n"
        "  *' list-devices '*) printf '%s\\n' '{\"devices\":[{\"id\":\"Vulkan0\",\"name\":\"Integrated GPU\",\"total_mib\":16384}]}' ;;\n"
        "  *' resources') printf '%s\\n' '{\"llm_server\": {\"cpus\": 5, \"memory_mib\": 27648}, \"omniroute\": {\"cpus\": 1, \"memory_mib\": 1024}}' ;;\n"
        "esac\n"
    )
    uv.chmod(uv.stat().st_mode | stat.S_IXUSR)

    yq = commands / "yq"
    yq.write_text("#!/usr/bin/bash\nexec \"$REAL_YQ\" \"$@\"\n")
    yq.chmod(yq.stat().st_mode | stat.S_IXUSR)

    podman = commands / "podman"
    podman.write_text(
        "#!/usr/bin/bash\n"
        "printf 'podman %s\\n' \"$*\" >> \"$CALLS\"\n"
        "case \"$*\" in *'--list-devices'*) printf '%s\\n' 'Vulkan0: Integrated GPU (16384 MiB, 16000 MiB free)' ;; esac\n"
    )
    podman.chmod(podman.stat().st_mode | stat.S_IXUSR)

    config = tmp_path / "models.yml"
    config.write_text(
        "gpu: {}\n"
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
    )
    environment = os.environ | {
        "CALLS": str(calls),
        "CONFIG_PATH_TEST": str(config),
        "HOME": str(tmp_path / "home"),
        "LLM_ENV_CONFIG": str(config),
        "LLM_ENV_MODELS_DIR": str(tmp_path / "models"),
        "PATH": f"{commands}:/usr/bin:/bin",
        "REAL_UV": shutil.which("uv") or "uv",
        "REAL_YQ": real_yq,
    }
    result = subprocess.run(
        ["/usr/bin/bash", "setup/setup.sh"],
        cwd=ROOT,
        env=environment,
        input="1\n1\n1\n",
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "VRAM short by 3000 MiB" in result.stdout
    assert "reduce ctx_size" in result.stdout
    # Never reaches Step 8 / "Setup complete" once the budget gate fires.
    assert "Setup complete" not in result.stdout
    # No recorded invocation actually ran `uv run llmenv.py ... resources`
    # (Step 8 never executed) -- checked against the real recorded command
    # log, not the stub script's own source text (a literal
    # "*' resources')" is the case-pattern source inside uv's stub script
    # and can never appear in $CALLS, which only ever receives the actual
    # `printf 'uv %s\n' "$*"` invocation lines; asserting its absence from
    # calls.read_text() would trivially pass even if Step 8 ran).
    assert not any(
        call.rstrip().endswith(" resources") for call in calls.read_text().splitlines()
    )
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `make test`
Expected: FAIL — `setup/setup.sh` currently only warns on an infeasible budget and continues to Step 8/"Setup complete", so `"Setup complete" not in result.stdout` fails.

- [ ] **Step 3: Gate Step 7 on budget feasibility**

Replace `setup/setup.sh`'s Step 7 block (`setup/setup.sh:132-141`):

```bash
log_step "Step 7/8  Checking the VRAM budget"
budget_json="$(mktemp)"
trap 'rm -f "$budget_json"' EXIT
if llmenv --config "$CONFIG_PATH" budget --models-dir "$MODELS_DIR" > "$budget_json"; then
    jq -r '"  available \(.available_mib) MiB, required \(.required_mib) MiB — fits"' "$budget_json"
else
    # cmd_budget can exit nonzero two different ways: an OPERATIONAL error
    # (e.g. gpu.pci_address unset, or the configured GPU not detected --
    # llmenv.py's `fail()` helper) whose payload is only `{"error": "..."}`
    # with no `.remedies`/`.models_max` keys at all, or a genuine budget
    # INFEASIBILITY (`.vram_feasible`/`.ram_feasible`/`.remedies` present).
    # Check for `.error` first: under `set -euo pipefail`, piping a bare
    # `{"error": ...}` payload into `jq -r '.remedies[] | ...'` makes jq
    # itself fail ("Cannot iterate over null"), which would crash this
    # script with jq's own cryptic message instead of the actual, much more
    # actionable error `cmd_budget` already produced.
    if jq -e '.error' "$budget_json" > /dev/null 2>&1; then
        die "$(jq -r '.error' "$budget_json")"
    fi
    if [ "$(jq -r '.vram_feasible // true' "$budget_json")" = "false" ]; then
        jq -r '"  VRAM short by \(.shortfall_mib) MiB (available \(.available_mib) MiB, required \(.required_mib) MiB)"' "$budget_json"
    fi
    if [ "$(jq -r '.ram_feasible // true' "$budget_json")" = "false" ]; then
        jq -r '"  RAM short by \(.ram_shortfall_mib) MiB (available \(.ram_available_mib) MiB, required \(.ram_required_mib) MiB)"' "$budget_json"
    fi
    jq -r '.remedies // [] | .[] | "    - \(.)"' "$budget_json"
    die "budget infeasible for models_max=$(jq -r '.models_max // "?"' "$budget_json"); adjust the config and retry"
fi
```

(the previous version unconditionally printed `.shortfall_mib`/`.available_mib`/`.required_mib`, which are VRAM-only fields — a RAM-only failure would have printed "SHORT BY 0 MiB" and no useful numbers at all. The `else` branch now also matches `scripts/start.sh`'s Task 4/Task 7 behavior exactly: `log_warn ... models_max=... exceeds the VRAM budget` becomes `die "budget infeasible for models_max=...; adjust the config and retry"`, so a config that fails setup's check would also have failed start's. The `.error`/`// []`/`// "?"` defaults mean an operational failure surfaces its own real message instead of crashing this script under `set -euo pipefail`).

- [ ] **Step 4: Run the test to verify it passes**

Run: `make validate && make test`
Expected: PASS — including every pre-existing `setup/setup.sh` test (`run_setup_with_numbered_selection`'s default `uv` stub returns `{"available_mib":12000,"required_mib":10000}` with an implicit exit 0, which is the `if` branch, unaffected by this change).

- [ ] **Step 5: Commit the setup gate**

```bash
git add setup/setup.sh tests/test_shell.py
git commit -m "fix(setup): exit nonzero on an infeasible budget instead of warning"
```

- [ ] **Step 5b: Extend `run_lifecycle_script`'s `uv` stub with a `resources` case, and write the failing tests for `make start`'s own resource gate**

`run_lifecycle_script` (`tests/test_shell.py:2686`, used by every `scripts/start.sh` test) currently has no `*' resources')` case in its `uv` stub at all — `scripts/start.sh` never calls `llmenv resources` today, so the case was never needed. Once Step 5c below adds that call, every existing `scripts/start.sh` test would otherwise see a silent empty response from the unmatched case and fail parsing it as JSON. Add the case (and a `resources_failure` parameter, mirroring `run_setup_with_numbered_selection`'s) to the existing function:

Change the signature:

```python
def run_lifecycle_script(
    tmp_path: pathlib.Path,
    script: str,
    *,
    api_key: str = "existing-key",
    active: bool = False,
    config_mode: int = 0o600,
    parallel_slots: int = 1,
    sampling_temperature: str | None = None,
    env_overrides: dict[str, str] | None = None,
    omniroute_port: int | None = None,
    resources_failure: bool = False,
) -> tuple[subprocess.CompletedProcess[str], pathlib.Path, pathlib.Path]:
```

Change the `uv` stub body:

```python
    resources_case = (
        "printf '%s\\n' '{\"error\": \"host has 3 CPUs; more than 3 are required\"}'; exit 1"
        if resources_failure
        else "printf '%s\\n' '{\"llm_server\": {\"cpus\": 4, \"memory_mib\": 8000}, \"omniroute\": {\"cpus\": 1, \"memory_mib\": 1024}}'"
    )
    uv = commands / "uv"
    uv.write_text(
        "#!/usr/bin/bash\n"
        "printf 'uv %s\\n' \"$*\" >> \"$CALLS\"\n"
        "case \"$*\" in *' migrate-config'*) exec \"$REAL_UV\" \"$@\" ;; esac\n"
        "case \"$*\" in *' budget '*) printf '%s\\n' '{\"available_mib\":12000,\"required_mib\":10000,\"vram_feasible\":true,\"ram_feasible\":true}' ;; esac\n"
        f"case \"$*\" in *' resources') {resources_case} ;; esac\n"
    )
```

(the `budget` case gains `vram_feasible`/`ram_feasible` so Step 5d's new print-formatting code has something to read on the success path too, matching what the real `cmd_budget` now always emits per Task 4.)

Add to `tests/test_shell.py`:

```python
def test_start_computes_and_persists_resource_limits_before_rendering(
    tmp_path: pathlib.Path,
) -> None:
    """make start (this task) must compute and persist resources.llm_server
    on every run, not just once at `make setup` -- otherwise a stale value
    left over from a previous setup (different host, or a config edited by
    hand) silently reaches pylib/compose.py unchanged."""
    result, config, calls = run_lifecycle_script(tmp_path, "scripts/start.sh")

    assert result.returncode == 0, result.stderr
    assert yq_value(config, ".resources.llm_server.cpus") == "4"
    assert yq_value(config, ".resources.llm_server.memory_mib") == "8000"
    recorded = calls.read_text().splitlines()
    resources_call = next(i for i, call in enumerate(recorded) if call.endswith(" resources"))
    render_unit_call = next(i for i, call in enumerate(recorded) if "render-unit.sh" in call)
    assert resources_call < render_unit_call


def test_start_overwrites_a_stale_persisted_resource_limit(
    tmp_path: pathlib.Path,
) -> None:
    """A config carrying resources.llm_server values computed on different
    (e.g. bigger) hardware during a past `make setup` must not reach the
    rendered container unchanged -- start must recompute against THIS
    host and overwrite them every time."""
    result, config, _ = run_lifecycle_script(tmp_path, "scripts/start.sh")

    assert result.returncode == 0, result.stderr
    # The fixture config (run_lifecycle_script) has no resources.llm_server
    # section at all going in -- migrate_config's defaults (cpus: 0,
    # memory_mib: 0) stand in for "stale/never computed". After a
    # successful start, the persisted values must be the freshly computed
    # ones the resources stub returned, not the migrate_config defaults.
    assert yq_value(config, ".resources.llm_server.cpus") == "4"
    assert yq_value(config, ".resources.llm_server.memory_mib") == "8000"


def test_start_dies_loudly_when_the_host_is_below_the_fixed_resource_floors(
    tmp_path: pathlib.Path,
) -> None:
    """compute_resource_limits() raises ResourceError when the host can't
    even reserve the fixed CPU/RAM floors -- previously only `make setup`
    ever surfaced this (by calling `llmenv resources`); `make start` skipped
    straight from budget to rendering and could launch an uncapped
    container on exactly the host where that's most dangerous."""
    result, config, calls = run_lifecycle_script(
        tmp_path, "scripts/start.sh", resources_failure=True
    )

    assert result.returncode != 0
    assert "host has 3 CPUs; more than 3 are required" in (result.stdout + result.stderr)
    recorded = calls.read_text()
    assert "systemctl --user start llm-server.service" not in recorded
    assert f"bash {ROOT / 'setup/render-unit.sh'}" not in recorded
```

- [ ] **Step 5c: Run the tests to verify they fail**

Run: `make test`
Expected: FAIL — all three new tests fail (`scripts/start.sh` never calls `llmenv resources` yet, so nothing is ever written to `.resources.llm_server.cpus`/`.memory_mib`, and a `resources_failure=True` stub is never invoked at all). Every other test file still passes; the `uv`/`budget` stub change from Step 5b does not by itself break any pre-existing `scripts/start.sh` test, since none of them inspect `.resources.llm_server.*` yet.

- [ ] **Step 5d: Add `make start`'s own resource-limit gate**

Replace `scripts/start.sh`'s VRAM-budget block and the line that follows it:

```bash
log_step "Checking the VRAM budget"
if ! budget="$(llmenv --config "$CONFIG_PATH" budget --models-dir "$MODELS_DIR")"; then
    # See setup/setup.sh's identical Step 7 gate for why `.error` is
    # checked first: an OPERATIONAL failure (`{"error": "..."}`, no
    # `.remedies`/`.vram_feasible`) piped into `jq -r '.remedies[] | ...'`
    # would crash this script under `set -euo pipefail` instead of
    # surfacing the real, more actionable message.
    if echo "$budget" | jq -e '.error' > /dev/null 2>&1; then
        die "$(echo "$budget" | jq -r '.error')"
    fi
    if [ "$(echo "$budget" | jq -r '.vram_feasible // true')" = "false" ]; then
        echo "$budget" | jq -r '"  VRAM short by \(.shortfall_mib) MiB (available \(.available_mib) MiB, required \(.required_mib) MiB)"'
    fi
    if [ "$(echo "$budget" | jq -r '.ram_feasible // true')" = "false" ]; then
        echo "$budget" | jq -r '"  RAM short by \(.ram_shortfall_mib) MiB (available \(.ram_available_mib) MiB, required \(.ram_required_mib) MiB)"'
    fi
    echo "$budget" | jq -r '.remedies // [] | .[] | "    - \(.)"'
    die "budget exceeded for models_max=${models_max}; adjust the config and retry"
fi
echo "$budget" | jq -r '"  available \(.available_mib) MiB, required \(.required_mib) MiB"'

log_step "Computing resource limits"
resources_json="$(mktemp)"
trap 'rm -f "$resources_json"' EXIT
if llmenv --config "$CONFIG_PATH" resources > "$resources_json"; then
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
else
    # A host too small to reserve the fixed floors is exactly the host
    # where an uncapped container is most dangerous -- render_compose()
    # treats cpus/memory_mib == 0 as "no explicit limit", so proceeding to
    # render with a stale or absent persisted value here would silently
    # disable the safety mechanism precisely when it matters most. Fail
    # loudly instead, exactly like setup/setup.sh's Step 8 already does.
    die "$(jq -r '.error' "$resources_json")"
fi

bash "${REPO_DIR}/setup/render-unit.sh"
```

This replaces the old block (which only printed `shortfall_mib`/`remedies` unconditionally and went straight to `render-unit.sh` on success) two ways at once: the VRAM/RAM failure messages now read the right fields for whichever check actually failed (Task 4's `vram_feasible`/`ram_feasible`/`ram_shortfall_mib`/`ram_available_mib`/`ram_required_mib`, never falling back to the VRAM-only `shortfall_mib` for a RAM-only failure), and a brand new "Computing resource limits" step runs — using the exact same `compute_resource_limits()` call, against the exact same host, moments after the budget check that just validated against it — and persists the result into `$CONFIG_PATH` immediately before `setup/render-unit.sh` renders the compose file from that same file. `require_cmd` at the top of the script already lists `jq`; no new command dependency.

- [ ] **Step 5e: Run the tests to verify they pass**

Run: `make validate && make test`
Expected: PASS — including every pre-existing `scripts/start.sh` test in `tests/test_shell.py` (Step 5b's stub change is additive: none of those tests assert on `.resources.llm_server.*` or on the exact wording of the VRAM-failure message).

- [ ] **Step 5f: Commit**

```bash
git add scripts/start.sh tests/test_shell.py
git commit -m "fix(start): recompute and persist resource limits every start, and report RAM-only failures with their own fields"
```

- [ ] **Step 6: Update `models.yml.example`**

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

- [ ] **Step 6b: Fix the now-broken `test_default_config_uses_128k_q5_1_runtime`**

`tests/test_cli.py:520-533` asserts `[model["ctx_size"] for model in parsed["models"]] == [131072, 131072]` and the matching `client_max_output_tokens` list — a positional, count-hardcoded check against exactly 2 models in `models.yml.example`. Step 6 above changes `ornith`'s `ctx_size` to `262144` and adds a third model (`ornith-35b`, also `262144`), so this assertion breaks the moment `models.yml.example` is edited, independent of any other code change in this plan — nothing currently exercises this in CI until `make test` is run. Replace the two positional assertions with alias-keyed ones so the test stays correct regardless of how many models the template lists or what order they're in (this is also what the design's "params should be configurable/driven from models.yml, not hardcoded assumptions about the file's shape" principle calls for):

```python
def test_default_config_uses_128k_q5_1_runtime(tmp_path):
    config = tmp_path / "models.yml"
    result = run("init", "--config", str(config), "--template", "models.yml.example")

    assert result.returncode == 0, result.stderr
    parsed = yaml.safe_load(config.read_text())
    assert parsed["runtime"]["models_max"] == 1
    assert parsed["runtime"]["cache_type_k"] == "q5_1"
    assert parsed["runtime"]["cache_type_v"] == "q5_1"
    ctx_size_by_alias = {model["alias"]: model["ctx_size"] for model in parsed["models"]}
    output_tokens_by_alias = {
        model["alias"]: model["client_max_output_tokens"] for model in parsed["models"]
    }
    assert ctx_size_by_alias == {
        "gemma4": 131072,
        "ornith": 262144,
        "ornith-35b": 262144,
    }
    assert output_tokens_by_alias == {
        "gemma4": 8192,
        "ornith": 8192,
        "ornith-35b": 8192,
    }
```

Run `make test` first to confirm this fails against the template as it stood *before* Step 6 (proving the old assertion was tied to the old 2-model/131072 shape), then re-run after Step 6's edit to confirm it now passes. Include this file in Step 11's commit below.

- [ ] **Step 7: Update `README.md`**

Replace both paragraphs together (search for `Clean setup maps` in the Configuration section — `README.md:47-57`), since the second paragraph's "one 131,072-token slot" claim becomes false once Ornith moves to 262,144 tokens while Gemma stays at 131,072:

> Clean setup maps `gemma4` to yuxinlu1's Agentic Gemma 4 12B v2 Q4_K_M
> build. Gemma and Ornith each receive one 131,072-token context and request slot
> with Q5_1 K/V caches. Pi and OpenCode advertise up to 8,192 output tokens,
> so reserving the full output allowance leaves a nominal 122,880 tokens for the
> prompt and history. All tokens still share the same slot. Setup reports an
> explicit VRAM-budget failure instead of shrinking context or offloading layers.
>
> The measured llama.cpp build applies a strict admission rule: post-template
> prompt tokens must be less than `n_ctx`. With one 131,072-token slot, 131,071
> is admitted with `max_tokens: 1`; 131,072 and above are rejected. This
> practical boundary does not change the configured 131,072-token client context.

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
tensor layout, and `make setup`/`make start` both refuse to start if
`resources.llm_server.memory_ceiling_pct` isn't high enough to hold it, the
same way they already refuse an infeasible VRAM budget.

The measured llama.cpp build applies a strict admission rule: post-template
prompt tokens must be less than `n_ctx`. With Gemma's 131,072-token slot,
131,071 is admitted with `max_tokens: 1`; 131,072 and above are rejected.
Ornith's 262,144-token slot follows the same rule at its own boundary. This
practical boundary does not change either model's configured client context.
```

Add a new bullet to the `## Configuration` section's model-management command list, right after the existing `enable`/`disable` examples (`README.md:36-40`):

```markdown
Reclaim disk space from downloaded models (they are NOT removed by
`make clean`) with:

```bash
make prune
```
```

Add a new `## Ornith 35B acceptance check` section at the end of `README.md` (the sibling design doc, `docs/superpowers/specs/2026-08-10-ornith-35b-moe-incorporation-design.md`, requires this executable procedure be documented in the README, not only in the implementation plan — this is the same sequence as this plan's Task 7 Step 12, kept in sync):

```markdown
## Ornith 35B acceptance check

`ornith-35b` (opt-in, disabled by default) is a mixture-of-experts model too
large to fit fully in most consumer GPUs' VRAM; it uses `--n-cpu-moe` to keep
some expert weights in host RAM. Unit tests cover the RAM/VRAM split
arithmetic but cannot verify the actual container image's flag handling or
real hybrid CPU/GPU loading, so verify it manually after any change that
touches MoE offload, the resource ceilings, or the Ornith 35B model entry:

1. Select ONLY `ornith-35b` (`select`, not `enable` — `enable` leaves
   whatever was previously selected also enabled, and `make setup` defaults
   to the first enabled model, which can silently re-pick it instead):
   ```bash
   uv run llmenv.py --config ~/.config/llm-env/models.yml models select ornith-35b
   uv run llmenv.py --config ~/.config/llm-env/models.yml models list \
     | jq -r '.models[] | select(.enabled) | .alias'
   # must print exactly: ornith-35b
   ```
2. `make setup` — must complete without the VRAM/RAM budget gate firing, at
   the recommended `n_cpu_moe: 28` / `memory_ceiling_pct: 60`.
3. `make start` — must reach a healthy state (`make check-server` passes).
4. A throughput spot-check, run directly against the model file the same
   way `scripts/benchmark.sh` does (there is no standalone `llama-bench`
   binary, and this does not run against the already-started `llm-server`
   container):
   ```bash
   podman run --rm --device /dev/dri \
     -v "${LLM_ENV_MODELS_DIR:-${HOME}/llm-workspace/models}:/models:ro,z" \
     --entrypoint /app/llama \
     ghcr.io/ggml-org/llama.cpp:server-vulkan \
     bench -m /models/ornith-1.0-35b-Q4_K_M.gguf -ngl 99 --n-cpu-moe 28 -p 512 -n 128 -d 0,32768 -r 2 -o json
   ```
   Compare the `avg_ts` of the `n_prompt > 0` row (pp) and the `n_gen > 0`
   row (tg) against the baseline recorded in
   `docs/superpowers/specs/2026-08-10-ornith-35b-moe-incorporation-design.md`
   (`n_cpu_moe 28`: pp @ depth 0 ≈ 441 tok/s, tg @ depth 0 ≈ 39 tok/s). A
   large regression indicates the offload split, quantization, or
   `n_gpu_layers` changed, not just the budgeting arithmetic.
5. During a generation request against the running server, confirm
   sustained CPU utilization (`podman stats llm-server`) is in the same
   ballpark as the design doc's measurement (~43-44% average across host
   threads) — much higher suggests `cpu_ceiling_pct` isn't actually being
   applied (compare `podman inspect llm-server`'s `cpus`/`NanoCpus` against
   `resources.llm_server.cpus` in `models.yml`).
```

- [ ] **Step 8: Update `.agents/architecture.md`**

Insert the following two bullets right after the existing `resources.llm_server.memory_ceiling_pct` bullet (`.agents/architecture.md:192-197`, ending "...computed live by `compute_resource_limits()` on every `llmenv resources` call.") and before the `gpu.vram_budget_ceiling_mib` bullet:

```markdown
- `resources.llm_server.cpu_ceiling_pct` (default 60%) caps how many host
  CPU cores `llm-server` can use, the same way `memory_ceiling_pct` caps
  RAM, floored at `cpu_ceiling_floor_pct` (default 20%) — added because MoE
  CPU-offload inference genuinely uses multiple cores (measured ~44% of all
  threads sustained), unlike GPU-resident dense models which barely touch
  the CPU. Reaches `models.yml` the same way `memory_ceiling_pct` does:
  `llmenv resources` computes it live, and `make setup` persists the result
  into `resources.llm_server.cpus`, which `pylib/compose.py` writes into the
  container's `cpus:` limit.
- MoE models (`n_cpu_moe` set on a model entry) split their weight cost
  between VRAM and host RAM: `pylib/gguf.py::moe_expert_offload_mib()`
  reads the GGUF's own tensor byte offsets to compute exactly how many MiB
  of routed-expert tensors `--n-cpu-moe` sends to CPU for that model's first
  `n_cpu_moe` transformer blocks (confirmed empirically that `--n-cpu-moe N`
  offloads ascending block indices, not descending). `llmenv budget`
  cross-checks the largest such requirement across every enabled model
  (not just the one compute_budget() would rank highest by VRAM cost)
  against `resources.llm_server.memory_mib`, and reports the same kind of
  explicit, remedied infeasibility as the existing VRAM check — never
  silently corrected. `n_cpu_moe: 0` on a model with no routed experts at
  all is still rejected explicitly, not silently ignored.
```

- [ ] **Step 9: Validate `models.yml.example` still passes schema validation**

`models.yml.example` is itself a complete, valid config (it already has `server`/`gpu`/`runtime`/`models` sections) — validate it the same way `setup/setup.sh` does, through `llmenv.py`'s `init` subcommand (`require_valid_config` runs on the template unmodified), per this repo's rule that Python is invoked only as `uv run llmenv.py <subcommand>`:

```bash
scratch="$(mktemp -u)"
uv run llmenv.py --config "$scratch" init --template models.yml.example
rm -f "$scratch"
```

Expected: exits 0 and prints `{"written": "<scratch path>"}`. Any validation error at all — including one naming `n_cpu_moe`, `cpu_ceiling_pct`, `vram_budget`, or `ctx_size` — is a failure of this step; `models.yml.example` is a complete config, not a partial template that's allowed to fail unrelated checks.

- [ ] **Step 10: Run the full verification suite**

Run: `make validate && make test`
Expected: all green, zero findings.

- [ ] **Step 11: Commit**

```bash
git add models.yml.example README.md .agents/architecture.md tests/test_cli.py
git commit -m "docs: add ornith-35b to models.yml.example, document MoE offload, CPU ceiling, and make prune"
```

- [ ] **Step 12: Manual acceptance (documented, not automated)**

Unit tests cover the arithmetic; they cannot verify the actual container image's flag handling or real hybrid CPU/GPU loading. After the above is merged, on a host matching (or close to) this plan's target hardware (AMD RX 9070 XT class dGPU, ~16GB+ VRAM, ~30GB+ RAM), run the exact procedure below (also added to `README.md` in Step 7 above, since the design doc requires it be documented there, not only here):

1. Select ONLY `ornith-35b` — `uv run llmenv.py --config ~/.config/llm-env/models.yml models select ornith-35b`. Use `select`, not `enable`: `enable` only adds the alias to the enabled set and leaves `gemma4` (or whatever was previously selected) enabled too, and `make setup`'s Step 3 default-selects the *first* enabled model in `models.yml` order — with both enabled, a subsequent unattended `make setup` could silently re-pick `gemma4` and this acceptance run would test the wrong model without any error. `select` replaces the enabled set outright. Verify it took effect before continuing:
   ```bash
   uv run llmenv.py --config ~/.config/llm-env/models.yml models list \
     | jq -r '.models[] | select(.enabled) | .alias'
   ```
   Expected output: exactly `ornith-35b` (one line, nothing else).
2. `make setup` — re-run it (even though the model is already selected) so the GPU/Vulkan device detection and the recommended `n_cpu_moe: 28` / `memory_ceiling_pct: 60` values above are actually written to the config; it must complete without the budget gate firing (Step 4/Step 5 above).
3. `make start` — must reach a healthy state (`make check-server` passes). This is also where Step 5d's "Computing resource limits" gate runs for the first time on this config; it must not die with a `ResourceError`.
4. A throughput spot-check using this repo's own benchmark invocation (`scripts/benchmark.sh:24-29`) directly against the Ornith 35B model file — NOT `llama-bench` (this repo has no standalone `llama-bench` binary; `llama bench` is a subcommand of the same `/app/llama` entrypoint the image already uses, per `.agents/architecture.md:15`) and NOT "against the running server" (the benchmark spins up its own one-shot container against the model file on disk; `llm-server`'s already-running container is not involved and does not need to be stopped first):
   ```bash
   podman run --rm --device /dev/dri \
     -v "${LLM_ENV_MODELS_DIR:-${HOME}/llm-workspace/models}:/models:ro,z" \
     --entrypoint /app/llama \
     ghcr.io/ggml-org/llama.cpp:server-vulkan \
     bench -m /models/ornith-1.0-35b-Q4_K_M.gguf -ngl 99 --n-cpu-moe 28 -p 512 -n 128 -d 0,32768 -r 2 -o json
   ```
   Read `avg_ts` from the row with `n_prompt > 0` (prompt-processing tok/s) and the row with `n_gen > 0` (generation tok/s) — the same shape `scripts/benchmark.sh`'s own `jq` parser reads. Compare against the design doc's recorded baseline (`docs/superpowers/specs/2026-08-10-ornith-35b-moe-incorporation-design.md`, `n_cpu_moe 28`: ~13268 MiB VRAM (Q4_K_M), pp @ depth 0 ≈ 441 tok/s, tg @ depth 0 ≈ 39 tok/s). A large regression from these numbers indicates the offload split, quantization, or `n_gpu_layers` regressed, not just this plan's arithmetic.
5. Confirm sustained CPU utilization during generation (against the running `llm-server` container, e.g. `podman stats llm-server` while sending a generation request) is in the same ballpark as the design doc's measurement (~43-44% average across host threads) — a much higher number suggests `cpu_ceiling_pct` isn't actually being applied to the running container (re-check `podman inspect llm-server` for the `NanoCpus`/`cpus` limit against `resources.llm_server.cpus` in `models.yml`).
