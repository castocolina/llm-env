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


def test_memory_ceiling_caps_llm_server_below_the_uncapped_remainder():
    uncapped = compute_resource_limits(host_cpu_count=8, host_memory_total_mib=32768)
    capped = compute_resource_limits(
        host_cpu_count=8, host_memory_total_mib=32768, memory_ceiling_pct=46
    )
    assert capped["llm_server"]["memory_mib"] < uncapped["llm_server"]["memory_mib"]
    assert capped["llm_server"]["memory_mib"] == round(32768 * 46 / 100)


def test_memory_ceiling_above_the_remainder_is_a_no_op():
    result = compute_resource_limits(
        host_cpu_count=8, host_memory_total_mib=32768, memory_ceiling_pct=100
    )
    assert (
        result["llm_server"]["memory_mib"]
        == 32768 - HOST_MEMORY_FLOOR_MIB - OMNIROUTE_MEMORY_FIXED_MIB
    )


def test_memory_ceiling_does_not_affect_the_insufficient_memory_floor_check():
    with pytest.raises(ResourceError):
        compute_resource_limits(
            host_cpu_count=8,
            host_memory_total_mib=HOST_MEMORY_FLOOR_MIB + OMNIROUTE_MEMORY_FIXED_MIB,
            memory_ceiling_pct=1,
        )


def test_memory_ceiling_floor_defaults_to_20_pct_when_unspecified():
    """Code-level safety net only — the real, user-facing floor lives in
    models.yml via migrate_config's 30% default (see the config tests
    below); this proves compute_resource_limits() itself never lets an
    unspecified floor disappear, even if a caller forgets to thread the
    config value through."""
    result = compute_resource_limits(
        host_cpu_count=8, host_memory_total_mib=32768, memory_ceiling_pct=0.001
    )
    assert result["llm_server"]["memory_mib"] == round(32768 * 20 / 100)


def test_memory_ceiling_floors_at_the_configured_value_instead_of_rounding_to_zero():
    """A tiny-but-valid pct (validate_config only requires 0 < pct <= 100)
    must not round down to a near-zero cap — pylib/compose.py omits
    `mem_limit` entirely when memory_mib is falsy, which would silently
    uncap the container. Unfloored, 32768 * 0.001 / 100 rounds to 0;
    floored at the configured 30%, the ceiling becomes exactly
    round(32768 * 30 / 100), not some other small percentage-only value.
    A percentage-of-total floor can never exceed the total, unlike the old
    fixed-MiB floor which could on a small enough host."""
    result = compute_resource_limits(
        host_cpu_count=8,
        host_memory_total_mib=32768,
        memory_ceiling_pct=0.001,
        memory_ceiling_floor_pct=30,
    )
    assert result["llm_server"]["memory_mib"] == round(32768 * 30 / 100)


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


def test_llm_server_limits_are_zeroed_when_disabled():
    result = compute_resource_limits(
        host_cpu_count=8, host_memory_total_mib=32768, llm_server_enabled=False
    )
    assert result["llm_server"] == {"cpus": 0, "memory_mib": 0}
    assert result["omniroute"] == {
        "cpus": OMNIROUTE_CPU_FIXED,
        "memory_mib": OMNIROUTE_MEMORY_FIXED_MIB,
    }


def test_disabled_llm_server_only_requires_the_host_and_omniroute_floor():
    """Exactly HOST_CPU_FLOOR + OMNIROUTE_CPU_FIXED CPUs (no spare core for
    llm-server) must be feasible when llm_server_enabled=False -- today's
    enabled-path floor check raises at this exact count."""
    result = compute_resource_limits(
        host_cpu_count=HOST_CPU_FLOOR + OMNIROUTE_CPU_FIXED,
        host_memory_total_mib=HOST_MEMORY_FLOOR_MIB + OMNIROUTE_MEMORY_FIXED_MIB,
        llm_server_enabled=False,
    )
    assert result["llm_server"] == {"cpus": 0, "memory_mib": 0}


def test_disabled_llm_server_still_raises_below_the_host_and_omniroute_floor():
    with pytest.raises(ResourceError):
        compute_resource_limits(
            host_cpu_count=HOST_CPU_FLOOR + OMNIROUTE_CPU_FIXED - 1,
            host_memory_total_mib=32768,
            llm_server_enabled=False,
        )
    with pytest.raises(ResourceError):
        compute_resource_limits(
            host_cpu_count=8,
            host_memory_total_mib=HOST_MEMORY_FLOOR_MIB + OMNIROUTE_MEMORY_FIXED_MIB - 1,
            llm_server_enabled=False,
        )


def test_llm_server_enabled_defaults_to_true():
    enabled_default = compute_resource_limits(host_cpu_count=8, host_memory_total_mib=32768)
    enabled_explicit = compute_resource_limits(
        host_cpu_count=8, host_memory_total_mib=32768, llm_server_enabled=True
    )
    assert enabled_default == enabled_explicit
