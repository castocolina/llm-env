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

    t_f32 = 0
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
        _tensor_info(name, 1, [size // 4], t_f32, tensor_offset)
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
