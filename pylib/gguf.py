"""Minimal GGUF header reader.

Parses only the header and metadata key/value block, which is all the VRAM
budget calculation needs. Array values (such as tokenizer vocabularies) are
summarized rather than materialized, and tensor data is never read, so this is
fast even on multi-gigabyte files.

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

# The KV count is the number of metadata keys, which is small even in large models.
MAX_METADATA_ENTRIES = 100_000
# Array element counts are capped only to catch corrupt headers, not real vocabularies.
MAX_ARRAY_ELEMENTS = 100_000_000


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


def _skip_array_payload(fh: BinaryIO, elem_type: int, count: int) -> None:
    """Seek past an array's payload without materializing it."""
    if elem_type in _SCALAR:
        _, size = _SCALAR[elem_type]
        fh.seek(size * count, 1)
        return
    if elem_type == STRING:
        for _ in range(count):
            (length,) = struct.unpack("<Q", _read_exact(fh, 8))
            fh.seek(length, 1)
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
        val = metadata[key]
        # Arrays in geometry (like per-layer head counts) are represented as
        # dict summaries. For VRAM calculation, use the first element.
        if isinstance(val, dict) and val.get("type") == "array":
            if val["element_type"] in _SCALAR:
                # For arrays of scalars, we need to read the actual array data.
                # This is a limitation: we skipped the array payload.
                raise GgufError(
                    f"{key} is a {val['count']}-element array; "
                    "cannot extract value without re-reading file"
                )
            raise GgufError(f"{key} is an array of unsupported type")
        return int(val)

    return {
        "block_count": need("block_count"),
        "head_count_kv": need("attention.head_count_kv"),
        "key_length": need("attention.key_length"),
        "value_length": need("attention.value_length"),
    }
