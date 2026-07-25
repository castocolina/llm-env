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


def test_kv_cache_sums_per_layer_head_counts():
    """Gemma varies KV heads per layer; summing is not the same as multiplying."""
    per_layer = {
        "block_count": 4,
        "head_count_kv": [1, 8, 1, 8],
        "key_length": 128,
        "value_length": 128,
    }
    uniform = {
        "block_count": 4,
        "head_count_kv": 8,
        "key_length": 128,
        "value_length": 128,
    }
    summed = kv_cache_mib(per_layer, 8192, "f16", "f16")
    naive = kv_cache_mib(uniform, 8192, "f16", "f16")
    # 18 heads summed vs 32 if every layer were assumed to have 8.
    assert summed * 32 == naive * 18


def test_kv_cache_scalar_and_equivalent_list_agree():
    scalar = {
        "block_count": 4,
        "head_count_kv": 8,
        "key_length": 128,
        "value_length": 128,
    }
    as_list = {**scalar, "head_count_kv": [8, 8, 8, 8]}
    assert kv_cache_mib(scalar, 8192, "f16", "f16") == kv_cache_mib(
        as_list, 8192, "f16", "f16"
    )


def test_kv_cache_rejects_per_layer_list_of_wrong_length():
    geometry = {
        "block_count": 48,
        "head_count_kv": [8, 8, 8],
        "key_length": 128,
        "value_length": 128,
    }
    with pytest.raises(BudgetError) as excinfo:
        kv_cache_mib(geometry, 8192, "f16", "f16")
    assert "3 entries" in str(excinfo.value)


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
