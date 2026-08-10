"""Minimal GGUF header reader.

Parses only the header and metadata key/value block, which is all the VRAM
budget calculation needs. Array values (such as tokenizer vocabularies) are
summarized rather than materialized, and tensor data is never read, so this is
fast even on multi-gigabyte files.

Format reference: llama.cpp ggml/docs/gguf.md
"""

from __future__ import annotations

import math
import re
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

# The KV count is the number of metadata keys, which is small even in large models.
MAX_METADATA_ENTRIES = 100_000
# Array element counts are capped only to catch corrupt headers, not real vocabularies.
MAX_ARRAY_ELEMENTS = 100_000_000
# Arrays at or below this size are materialized; larger ones are summarized.
# Per-layer geometry arrays are tens of elements; vocabularies are 100k+.
MAX_INLINE_ARRAY_ELEMENTS = 4096


class GgufError(Exception):
    """Raised when a file is not valid GGUF or is truncated."""


def _read_exact(fh: BinaryIO, size: int) -> bytes:
    data = fh.read(size)
    if len(data) != size:
        raise GgufError(f"truncated GGUF: wanted {size} bytes, got {len(data)}")
    return data


def _skip_exact(fh: BinaryIO, size: int) -> None:
    """Advance a seekable stream, rejecting a skip past its end."""
    start = fh.tell()
    end = fh.seek(0, 2)
    fh.seek(start)
    if size > end - start:
        raise GgufError(f"truncated GGUF: wanted {size} bytes, got {end - start}")
    fh.seek(size, 1)


def _read_string(fh: BinaryIO) -> str:
    (length,) = struct.unpack("<Q", _read_exact(fh, 8))
    return _read_exact(fh, length).decode("utf-8", errors="replace")


def _skip_array_payload(fh: BinaryIO, elem_type: int, count: int) -> None:
    """Seek past an array's payload without materializing it."""
    if elem_type in _SCALAR:
        _, size = _SCALAR[elem_type]
        _skip_exact(fh, size * count)
        return
    if elem_type == STRING:
        for _ in range(count):
            (length,) = struct.unpack("<Q", _read_exact(fh, 8))
            _skip_exact(fh, length)
        return
    if elem_type == ARRAY:
        raise GgufError("nested GGUF arrays are not supported")
    raise GgufError(f"unknown GGUF array element type: {elem_type}")


def _read_value(fh: BinaryIO, type_code: int) -> Any:
    if type_code in _SCALAR:
        fmt, size = _SCALAR[type_code]
        return struct.unpack(fmt, _read_exact(fh, size))[0]
    if type_code == STRING:
        return _read_string(fh)
    if type_code == ARRAY:
        (elem_type,) = struct.unpack("<I", _read_exact(fh, 4))
        (count,) = struct.unpack("<Q", _read_exact(fh, 8))
        if count > MAX_ARRAY_ELEMENTS:
            raise GgufError(f"array too large: {count} elements")
        if elem_type != ARRAY and count <= MAX_INLINE_ARRAY_ELEMENTS:
            return [_read_value(fh, elem_type) for _ in range(count)]
        _skip_array_payload(fh, elem_type, count)
        return {"type": "array", "element_type": elem_type, "count": count}
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


def kv_geometry(metadata: dict[str, Any]) -> dict[str, Any]:
    """Expand GGUF attention metadata into uniform per-layer geometry."""
    arch = metadata.get("general.architecture")
    if not isinstance(arch, str) or not arch:
        raise GgufError("general.architecture missing from GGUF metadata")

    def need(suffix: str) -> Any:
        key = f"{arch}.{suffix}"
        if key not in metadata:
            raise GgufError(f"required metadata key missing: {key}")
        return metadata[key]

    def positive_int(suffix: str) -> int:
        value = need(suffix)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise GgufError(
                f"{arch}.{suffix} must be a positive integer, got {value!r}"
            )
        return value

    block_count = positive_int("block_count")

    def layer_values(suffix: str) -> list[int]:
        value = need(suffix)
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return [value] * block_count
        if (
            isinstance(value, list)
            and len(value) == block_count
            and all(
                isinstance(item, int) and not isinstance(item, bool) and item > 0
                for item in value
            )
        ):
            return value
        raise GgufError(
            f"{arch}.{suffix} must be a positive integer or a list of "
            f"{block_count} positive integers"
        )

    heads = layer_values("attention.head_count_kv")
    full_keys = layer_values("attention.key_length")
    full_values = layer_values("attention.value_length")
    kinds = ["full"] * block_count
    sliding_window = None
    swa_keys = full_keys
    swa_values = full_values

    if arch in ("qwen35", "qwen35moe"):
        recurrent_key = f"{arch}.attention.recurrent_layers"
        if recurrent_key in metadata:
            recurrent = metadata[recurrent_key]
            if (
                not isinstance(recurrent, list)
                or len(recurrent) != block_count
                or not all(isinstance(item, bool) for item in recurrent)
            ):
                raise GgufError(
                    f"{recurrent_key} must contain {block_count} Boolean entries"
                )
            kinds = ["recurrent" if item else "full" for item in recurrent]
        else:
            interval_key = f"{arch}.full_attention_interval"
            if interval_key not in metadata:
                raise GgufError(
                    f"required metadata missing: {recurrent_key} or {interval_key}"
                )
            interval = positive_int("full_attention_interval")
            kinds = [
                "full" if (index + 1) % interval == 0 else "recurrent"
                for index in range(block_count)
            ]

    if arch == "gemma4":
        pattern = need("attention.sliding_window_pattern")
        if (
            not isinstance(pattern, list)
            or len(pattern) != block_count
            or not all(isinstance(item, bool) for item in pattern)
        ):
            raise GgufError(
                f"{arch}.attention.sliding_window_pattern must contain "
                f"{block_count} Boolean entries"
            )
        kinds = ["swa" if item else "full" for item in pattern]
        sliding_window = positive_int("attention.sliding_window")
        swa_keys = layer_values("attention.key_length_swa")
        swa_values = layer_values("attention.value_length_swa")

    layers = []
    for index, kind in enumerate(kinds):
        layers.append(
            {
                "kind": kind,
                "head_count_kv": heads[index],
                "key_length": swa_keys[index] if kind == "swa" else full_keys[index],
                "value_length": (
                    swa_values[index] if kind == "swa" else full_values[index]
                ),
            }
        )
    return {"layers": layers, "sliding_window": sliding_window}
