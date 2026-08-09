"""Host CPU/RAM budgeting for the compose container stack.

Mirrors pylib/budget.py's shape for VRAM: fixed reservations for the host
and for OmniRoute's own process, then whatever is left goes to llm-server.
cpus is a whole CPU-core count usable directly as compose's `cpus:` service
key; memory_mib is a whole-MiB integer usable as `mem_limit: <n>m`.
"""

from __future__ import annotations

from typing import Any

# Fixed floor reserved for the host OS and other applications.
HOST_CPU_FLOOR = 2
HOST_MEMORY_FLOOR_MIB = 4096

# OmniRoute is a lightweight Node/Next.js process, not the workload driving
# resource needs here — it gets a flat cap, not a share of the remainder.
OMNIROUTE_CPU_FIXED = 1
OMNIROUTE_MEMORY_FIXED_MIB = 1024


class ResourceError(Exception):
    """Raised when the host has too few resources to reserve the fixed floors."""


def compute_resource_limits(
    host_cpu_count: int,
    host_memory_total_mib: int,
    memory_ceiling_pct: float = 100,
    memory_ceiling_floor_mib: int = 6144,
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
    # pylib/compose.py omits `mem_limit` entirely when memory_mib is
    # falsy/0, which would silently leave the container fully uncapped for
    # a valid-but-tiny memory_ceiling_pct. memory_ceiling_floor_mib's
    # real, user-facing default (10 GiB) lives in models.yml via
    # migrate_config; the 6 GiB default here is only a code-level safety
    # net for callers that don't thread the configured value through.
    memory_ceiling_mib = max(
        memory_ceiling_floor_mib,
        round(host_memory_total_mib * memory_ceiling_pct / 100),
    )
    llm_server_memory_mib = min(
        host_memory_total_mib - memory_floor_mib, memory_ceiling_mib
    )
    return {
        "host_cpu_floor": HOST_CPU_FLOOR,
        "host_memory_floor_mib": HOST_MEMORY_FLOOR_MIB,
        "omniroute": {
            "cpus": OMNIROUTE_CPU_FIXED,
            "memory_mib": OMNIROUTE_MEMORY_FIXED_MIB,
        },
        "llm_server": {
            "cpus": host_cpu_count - cpu_floor,
            "memory_mib": llm_server_memory_mib,
        },
    }
