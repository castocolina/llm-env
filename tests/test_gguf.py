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


def _kv_string_array(key: str, values: list[str]) -> bytes:
    kb = key.encode()
    out = (
        struct.pack("<Q", len(kb))
        + kb
        + struct.pack("<I", 9)          # ARRAY
        + struct.pack("<I", T_STRING)   # element type
        + struct.pack("<Q", len(values))
    )
    for v in values:
        vb = v.encode()
        out += struct.pack("<Q", len(vb)) + vb
    return out


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


def test_kv_geometry_expands_uniform_full_attention(tmp_path):
    header = read_gguf_header(write_fake_gguf(tmp_path / "m.gguf"))
    geo = kv_geometry(header["metadata"])
    assert geo == {
        "layers": [
            {
                "kind": "full",
                "head_count_kv": 8,
                "key_length": 128,
                "value_length": 128,
            }
            for _ in range(40)
        ],
        "sliding_window": None,
    }


def test_kv_geometry_raises_when_architecture_missing():
    with pytest.raises(GgufError):
        kv_geometry({"llama.block_count": 40})


def test_large_string_array_is_summarized_not_materialized(tmp_path):
    vocab = [f"tok{i}" for i in range(200_000)]
    kvs = [
        _kv_string("general.architecture", "llama"),
        _kv_string_array("tokenizer.ggml.tokens", vocab),
        _kv_uint32("llama.block_count", 40),
    ]
    body = b"".join(kvs)
    path = tmp_path / "big.gguf"
    path.write_bytes(b"GGUF" + struct.pack("<I", 3) + struct.pack("<QQ", 0, len(kvs)) + body)

    header = read_gguf_header(path)
    assert header["metadata"]["tokenizer.ggml.tokens"] == {
        "type": "array",
        "element_type": T_STRING,
        "count": 200_000,
    }
    # Keys after the big array must still parse, proving the skip landed correctly.
    assert header["metadata"]["llama.block_count"] == 40


def test_truncated_summarized_array_is_rejected(tmp_path):
    path = tmp_path / "truncated-array.gguf"
    key = b"tokenizer.ggml.tokens"
    body = (
        struct.pack("<Q", len(key))
        + key
        + struct.pack("<I", 9)  # ARRAY
        + struct.pack("<I", T_UINT32)
        + struct.pack("<Q", 4097)
    )
    path.write_bytes(b"GGUF" + struct.pack("<I", 3) + struct.pack("<QQ", 0, 1) + body)

    ok, message = validate_gguf(path)

    assert ok is False
    assert "truncated" in message


def test_array_beyond_the_corruption_cap_is_rejected(tmp_path):
    path = tmp_path / "corrupt.gguf"
    kb = b"bad"
    body = (
        struct.pack("<Q", len(kb))
        + kb
        + struct.pack("<I", 9)
        + struct.pack("<I", T_UINT32)
        + struct.pack("<Q", 200_000_000)
    )
    path.write_bytes(b"GGUF" + struct.pack("<I", 3) + struct.pack("<QQ", 0, 1) + body)
    with pytest.raises(GgufError):
        read_gguf_header(path)


def _kv_int32_array(key: str, values: list[int]) -> bytes:
    kb = key.encode()
    out = (
        struct.pack("<Q", len(kb))
        + kb
        + struct.pack("<I", 9)   # ARRAY
        + struct.pack("<I", 5)   # INT32 element type
        + struct.pack("<Q", len(values))
    )
    for v in values:
        out += struct.pack("<i", v)
    return out


def test_per_layer_head_count_kv_array_is_materialized(tmp_path):
    kvs = [
        _kv_string("general.architecture", "llama"),
        _kv_uint32("llama.block_count", 48),
        _kv_int32_array("llama.attention.head_count_kv", [8] * 48),
        _kv_uint32("llama.attention.key_length", 128),
        _kv_uint32("llama.attention.value_length", 128),
    ]
    body = b"".join(kvs)
    path = tmp_path / "perlayer.gguf"
    path.write_bytes(b"GGUF" + struct.pack("<I", 3) + struct.pack("<QQ", 0, len(kvs)) + body)

    geo = kv_geometry(read_gguf_header(path)["metadata"])
    assert geo["layers"] == [
        {
            "kind": "full",
            "head_count_kv": 8,
            "key_length": 128,
            "value_length": 128,
        }
        for _ in range(48)
    ]


def test_kv_geometry_rejects_non_integer_head_count(tmp_path):
    kvs = [
        _kv_string("general.architecture", "llama"),
        _kv_uint32("llama.block_count", 40),
        _kv_string("llama.attention.head_count_kv", "eight"),
        _kv_uint32("llama.attention.key_length", 128),
        _kv_uint32("llama.attention.value_length", 128),
    ]
    body = b"".join(kvs)
    path = tmp_path / "badtype.gguf"
    path.write_bytes(b"GGUF" + struct.pack("<I", 3) + struct.pack("<QQ", 0, len(kvs)) + body)

    with pytest.raises(GgufError):
        kv_geometry(read_gguf_header(path)["metadata"])


def test_kv_geometry_rejects_summarized_array_head_count(tmp_path):
    """An oversized head_count_kv array is summarized, not materialized.

    kv_geometry must reject the summary dict with a clear GgufError rather than
    a TypeError or a silently wrong number.
    """
    oversized = _kv_int32_array(
        "llama.attention.head_count_kv", [8] * (4096 + 1)
    )
    kvs = [
        _kv_string("general.architecture", "llama"),
        _kv_uint32("llama.block_count", 40),
        oversized,
        _kv_uint32("llama.attention.key_length", 128),
        _kv_uint32("llama.attention.value_length", 128),
    ]
    body = b"".join(kvs)
    path = tmp_path / "summarized.gguf"
    path.write_bytes(
        b"GGUF" + struct.pack("<I", 3) + struct.pack("<QQ", 0, len(kvs)) + body
    )

    metadata = read_gguf_header(path)["metadata"]
    # Confirm the precondition: the array was summarized, not materialized.
    assert metadata["llama.attention.head_count_kv"]["type"] == "array"
    assert metadata["llama.attention.head_count_kv"]["count"] == 4097

    with pytest.raises(GgufError) as excinfo:
        kv_geometry(metadata)
    assert "list of 40 positive integers" in str(excinfo.value)


def test_qwen35_uses_recurrent_layer_metadata():
    metadata = {
        "general.architecture": "qwen35",
        "qwen35.block_count": 4,
        "qwen35.attention.head_count_kv": 8,
        "qwen35.attention.key_length": 128,
        "qwen35.attention.value_length": 128,
        "qwen35.attention.recurrent_layers": [True, True, True, False],
    }
    assert [layer["kind"] for layer in kv_geometry(metadata)["layers"]] == [
        "recurrent",
        "recurrent",
        "recurrent",
        "full",
    ]


def test_qwen35_falls_back_to_full_attention_interval():
    metadata = {
        "general.architecture": "qwen35",
        "qwen35.block_count": 8,
        "qwen35.attention.head_count_kv": 8,
        "qwen35.attention.key_length": 128,
        "qwen35.attention.value_length": 128,
        "qwen35.full_attention_interval": 4,
    }
    assert [layer["kind"] for layer in kv_geometry(metadata)["layers"]] == [
        "recurrent",
        "recurrent",
        "recurrent",
        "full",
        "recurrent",
        "recurrent",
        "recurrent",
        "full",
    ]


def test_gemma4_uses_swa_pattern_window_and_dimensions():
    metadata = {
        "general.architecture": "gemma4",
        "gemma4.block_count": 4,
        "gemma4.attention.head_count_kv": [2, 4, 2, 4],
        "gemma4.attention.key_length": 128,
        "gemma4.attention.value_length": 128,
        "gemma4.attention.sliding_window": 2048,
        "gemma4.attention.sliding_window_pattern": [True, False, True, False],
        "gemma4.attention.key_length_swa": 64,
        "gemma4.attention.value_length_swa": 80,
    }
    geometry = kv_geometry(metadata)
    assert geometry["sliding_window"] == 2048
    assert geometry["layers"] == [
        {"kind": "swa", "head_count_kv": 2, "key_length": 64, "value_length": 80},
        {
            "kind": "full",
            "head_count_kv": 4,
            "key_length": 128,
            "value_length": 128,
        },
        {"kind": "swa", "head_count_kv": 2, "key_length": 64, "value_length": 80},
        {
            "kind": "full",
            "head_count_kv": 4,
            "key_length": 128,
            "value_length": 128,
        },
    ]


@pytest.mark.parametrize(
    "metadata, message",
    [
        (
            {
                "general.architecture": "qwen35",
                "qwen35.block_count": 4,
                "qwen35.attention.head_count_kv": 8,
                "qwen35.attention.key_length": 128,
                "qwen35.attention.value_length": 128,
            },
            "recurrent_layers or qwen35.full_attention_interval",
        ),
        (
            {
                "general.architecture": "qwen35",
                "qwen35.block_count": 4,
                "qwen35.attention.head_count_kv": 8,
                "qwen35.attention.key_length": 128,
                "qwen35.attention.value_length": 128,
                "qwen35.attention.recurrent_layers": [True, False],
            },
            "4 Boolean entries",
        ),
        (
            {
                "general.architecture": "gemma4",
                "gemma4.block_count": 2,
                "gemma4.attention.head_count_kv": 4,
                "gemma4.attention.key_length": 128,
                "gemma4.attention.value_length": 128,
                "gemma4.attention.sliding_window": 2048,
                "gemma4.attention.sliding_window_pattern": [True, "false"],
                "gemma4.attention.key_length_swa": 64,
                "gemma4.attention.value_length_swa": 64,
            },
            "sliding_window_pattern",
        ),
    ],
)
def test_kv_geometry_rejects_incomplete_or_malformed_hybrid_metadata(
    metadata, message
):
    with pytest.raises(GgufError, match=message):
        kv_geometry(metadata)
