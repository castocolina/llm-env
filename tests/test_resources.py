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
