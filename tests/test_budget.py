import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pylib.budget import (
    RUNTIME_OVERHEAD_MIB,
    SPIKE_HEADROOM_MIB,
    BudgetError,
    compute_budget,
    kv_cache_components_mib,
    pad256,
    parse_vram_budget,
)


def layer(kind: str, heads: int = 1, key: int = 32, value: int = 32):
    return {
        "kind": kind,
        "head_count_kv": heads,
        "key_length": key,
        "value_length": value,
    }


def cost(alias: str, weights_mib: int, kv_mib: int = 0):
    return {
        "alias": alias,
        "weights_mib": weights_mib,
        "full_kv_mib": kv_mib,
        "swa_kv_mib": 0,
        "kv_mib": kv_mib,
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


def test_pad256_edges():
    assert pad256(0) == 0
    assert pad256(1) == 256
    assert pad256(256) == 256
    assert pad256(257) == 512


def test_full_attention_uses_complete_context_and_rounds_up():
    result = kv_cache_components_mib(
        {"layers": [layer("full")], "sliding_window": None},
        ctx_size=257,
        ubatch_size=512,
        cache_type_k="f16",
        cache_type_v="f16",
    )
    assert result == {"full_kv_mib": 1, "swa_kv_mib": 0, "kv_mib": 1}


def test_kv_cache_rejects_unknown_type():
    with pytest.raises(BudgetError):
        kv_cache_components_mib(
            {"layers": [layer("full")], "sliding_window": None},
            8192,
            512,
            "q3_k_xxl",
            "f16",
        )


def test_recurrent_layers_have_no_context_scaled_kv():
    result = kv_cache_components_mib(
        {"layers": [layer("recurrent") for _ in range(24)], "sliding_window": None},
        131072,
        512,
        "q5_1",
        "q5_1",
    )
    assert result == {"full_kv_mib": 0, "swa_kv_mib": 0, "kv_mib": 0}


def test_ornith_eight_full_layers_allocate_1536_mib_q5_1():
    geometry = {
        "layers": [layer("recurrent", 8, 128, 128) for _ in range(24)]
        + [layer("full", 8, 128, 128) for _ in range(8)],
        "sliding_window": None,
    }
    result = kv_cache_components_mib(geometry, 131072, 512, "q5_1", "q5_1")
    assert result == {"full_kv_mib": 1536, "swa_kv_mib": 0, "kv_mib": 1536}


def test_swa_uses_padded_window_plus_ubatch_and_distinct_dimensions():
    geometry = {
        "layers": [layer("swa", heads=2, key=64, value=96)],
        "sliding_window": 257,
    }
    # SWA cells are pad256(min(131072, 257 + 512)) = 1024.
    result = kv_cache_components_mib(geometry, 131072, 512, "f16", "f16")
    assert result["full_kv_mib"] == 0
    assert result["swa_kv_mib"] == 1
    assert result["kv_mib"] == 1


def test_runtime_and_spike_allowances_are_independent():
    assert RUNTIME_OVERHEAD_MIB == 512
    assert SPIKE_HEADROOM_MIB == 1024
    result = compute_budget(
        vram_total_mib=4096,
        compositor_used_mib=256,
        reserve_floor_mib=512,
        model_costs=[cost("a", 1000, 150)],
        models_max=1,
    )
    assert result["available_mib"] == 4096 - 512 - 1024
    assert result["models"][0]["runtime_overhead_mib"] == 512
    assert result["required_mib"] == 1000 + 150 + 512


def test_budget_selects_largest_permitted_resident_set():
    costs = [cost("small", 100), cost("large", 900), cost("medium", 500)]
    result = compute_budget(10000, 0, 0, costs, models_max=2)
    assert [model["alias"] for model in result["resident_models"]] == [
        "large",
        "medium",
    ]
    assert result["required_mib"] == (900 + 512) + (500 + 512)


def test_budget_feasible_when_models_fit():
    result = compute_budget(
        vram_total_mib=16304,
        compositor_used_mib=300,
        reserve_floor_mib=1024,
        model_costs=[cost("a", 7305, 640)],
        models_max=1,
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
        model_costs=[cost("a", 100, 10)],
        models_max=1,
    )
    assert result["reserve_mib"] == 2026


def test_budget_infeasible_reports_shortfall_and_remedies():
    result = compute_budget(
        vram_total_mib=16304,
        compositor_used_mib=2026,
        reserve_floor_mib=1024,
        model_costs=[
            cost("gemma4", 7305, 1280),
            cost("ornith", 5368, 1280),
        ],
        models_max=2,
    )
    assert result["feasible"] is False
    assert result["shortfall_mib"] > 0
    assert result["remedies"]
    assert any("cache_type" in r or "ctx_size" in r for r in result["remedies"])


def test_compositor_remedy_reports_only_what_exceeds_the_floor():
    result = compute_budget(
        vram_total_mib=16304,
        compositor_used_mib=2497,
        reserve_floor_mib=1024,
        model_costs=[
            cost("a", 7307, 2788),
            cost("b", 5368, 544),
        ],
        models_max=2,
    )
    assert result["feasible"] is False
    compositor = [r for r in result["remedies"] if "KWIN_DRM_DEVICES" in r]
    assert len(compositor) == 1
    # 2497 - 1024 = 1473, not 2497.
    assert "1473 MiB" in compositor[0]
    assert "2497 MiB" not in compositor[0]


def test_no_compositor_remedy_when_usage_is_below_the_floor():
    result = compute_budget(
        vram_total_mib=16304,
        compositor_used_mib=200,
        reserve_floor_mib=1024,
        model_costs=[
            cost("a", 14000, 2000),
            cost("b", 1000, 100),
        ],
        models_max=2,
    )
    assert result["feasible"] is False
    assert not [r for r in result["remedies"] if "KWIN_DRM_DEVICES" in r]


def test_quantization_remedy_suppressed_when_already_q8_0():
    costs = [
        cost("a", 14000, 2000),
        cost("b", 1000, 100),
    ]
    default = compute_budget(16304, 2497, 1024, costs, models_max=2)
    quantized = compute_budget(
        16304,
        2497,
        1024,
        costs,
        models_max=2,
        cache_type_k="q8_0",
        cache_type_v="q8_0",
    )
    assert any("q8_0" in r and "cache_type_k" in r for r in default["remedies"])
    assert not [r for r in quantized["remedies"] if "cache_type_k" in r]
    assert quantized["cache_type_k"] == "q8_0"
