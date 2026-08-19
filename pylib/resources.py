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
    memory_ceiling_floor_pct: float = 20,
    cpu_ceiling_pct: float = 100,
    cpu_ceiling_floor_pct: float = 20,
    llm_server_enabled: bool = True,
) -> dict[str, Any]:
    cpu_floor = HOST_CPU_FLOOR + OMNIROUTE_CPU_FIXED
    memory_floor_mib = HOST_MEMORY_FLOOR_MIB + OMNIROUTE_MEMORY_FIXED_MIB
    if llm_server_enabled:
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
    else:
        if host_cpu_count < cpu_floor:
            raise ResourceError(
                f"host has {host_cpu_count} CPUs; at least {cpu_floor} are "
                "required to reserve the host floor and OmniRoute's fixed "
                "allocation"
            )
        if host_memory_total_mib < memory_floor_mib:
            raise ResourceError(
                f"host has {host_memory_total_mib} MiB RAM; at least "
                f"{memory_floor_mib} MiB is required to reserve the host floor "
                "and OmniRoute's fixed allocation"
            )

    if llm_server_enabled:
        # See the comment above the previous version of this function for why a
        # percentage-of-total floor is used instead of a bare pct: it can never
        # be a no-op on any host, unlike a fixed-MiB (or, for CPU, fixed-core)
        # floor could be.
        memory_ceiling_mib = max(
            round(host_memory_total_mib * memory_ceiling_floor_pct / 100),
            round(host_memory_total_mib * memory_ceiling_pct / 100),
        )
        llm_server_memory_mib = min(
            host_memory_total_mib - memory_floor_mib, memory_ceiling_mib
        )
        # MoE CPU-offload inference (see docs/superpowers/specs/2026-08-10-
        # ornith-35b-moe-incorporation-design.md) measured sustained ~44% CPU
        # utilization across all threads. Uncapped, llm-server previously got
        # every core minus the fixed floor -- fine for GPU-resident dense
        # models, but not something a heavier future MoE config should be able
        # to grow into unbounded. Same floor/ceiling shape as the memory cap
        # above, EXCEPT for the explicit `max(1, ...)`: memory is measured in
        # MiB, where a percentage-of-total floor can never round down to 0 on
        # any real host, but CPU core counts are small whole numbers -- on a
        # 4-core host, round(4 * 1 / 100) == 0, so both a tiny cpu_ceiling_pct
        # AND a tiny cpu_ceiling_floor_pct can independently round to 0. Without
        # this floor, compose.py's `if cpus:` treats a computed 0 as "omit the
        # limit" (fully uncapped) rather than "cap at 0 cores" -- silently
        # defeating the whole point of this ceiling on a small host.
        cpu_ceiling = max(
            1,
            round(host_cpu_count * cpu_ceiling_floor_pct / 100),
            round(host_cpu_count * cpu_ceiling_pct / 100),
        )
        llm_server_cpus = min(host_cpu_count - cpu_floor, cpu_ceiling)
    else:
        llm_server_memory_mib = 0
        llm_server_cpus = 0

    return {
        "host_cpu_floor": HOST_CPU_FLOOR,
        "host_memory_floor_mib": HOST_MEMORY_FLOOR_MIB,
        "omniroute": {
            "cpus": OMNIROUTE_CPU_FIXED,
            "memory_mib": OMNIROUTE_MEMORY_FIXED_MIB,
        },
        "llm_server": {
            "cpus": llm_server_cpus,
            "memory_mib": llm_server_memory_mib,
        },
    }
