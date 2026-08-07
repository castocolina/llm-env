"""Host CPU/RAM budgeting for the compose container stack.

Mirrors pylib/budget.py's shape for VRAM: a fixed reserved floor for the
host, then whatever is left goes to llm-server. cpus is a whole CPU-core
count usable directly as compose's `cpus:` service key; memory_mib is a
whole-MiB integer usable as `mem_limit: <n>m`.
"""

from __future__ import annotations

from typing import Any

# Fixed floor reserved for the host OS and other applications.
HOST_CPU_FLOOR = 2
HOST_MEMORY_FLOOR_MIB = 4096


class ResourceError(Exception):
    """Raised when the host has too few resources to reserve the fixed floor."""


def compute_resource_limits(
    host_cpu_count: int, host_memory_total_mib: int
) -> dict[str, Any]:
    if host_cpu_count <= HOST_CPU_FLOOR:
        raise ResourceError(
            f"host has {host_cpu_count} CPUs; more than {HOST_CPU_FLOOR} are "
            "required to reserve the host floor and still run llm-server"
        )
    if host_memory_total_mib <= HOST_MEMORY_FLOOR_MIB:
        raise ResourceError(
            f"host has {host_memory_total_mib} MiB RAM; more than "
            f"{HOST_MEMORY_FLOOR_MIB} MiB is required to reserve the host "
            "floor and still run llm-server"
        )
    return {
        "host_cpu_floor": HOST_CPU_FLOOR,
        "host_memory_floor_mib": HOST_MEMORY_FLOOR_MIB,
        "llm_server": {
            "cpus": host_cpu_count - HOST_CPU_FLOOR,
            "memory_mib": host_memory_total_mib - HOST_MEMORY_FLOOR_MIB,
        },
    }
