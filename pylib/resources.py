"""Host CPU/RAM budgeting for the compose container stack.

Mirrors pylib/budget.py's shape for VRAM: fixed reservations for the host,
for OmniRoute's own process, and for remote-setup's own process, then
whatever is left goes to llm-server. cpus_min/cpus_max are whole CPU-core
counts usable directly as compose's `cpu_shares:`/`cpus:` service keys;
memory_min_mib/memory_max_mib are whole-MiB integers usable as
`mem_reservation: <n>m`/`mem_limit: <n>m`.
"""

from __future__ import annotations

from typing import Any

# Fixed floor reserved for the host OS and other applications.
HOST_CPU_FLOOR = 2
HOST_MEMORY_FLOOR_MIB = 4096

# OmniRoute is a lightweight Node/Next.js process, not the workload driving
# resource needs here -- it gets a flat min/max range, not a share of the
# remainder. The MIN values are what's actually reserved from the host's
# perspective (subtracted from the floor available to llm_server below); the
# MAX values are just a ceiling the container's cgroup cannot exceed, they
# don't need to be pre-reserved from other consumers.
#
# A flat 1024 MiB cap proved too tight in practice: OmniRoute's own internal
# resourcePressure guard (cgroup_ratio) was observed tripping and returning
# 503s under concurrent cross-AI/tool-heavy sessions (my-coding,
# my-plan-review combos with 11-17 tools each) while sitting at only ~700MB
# baseline -- on a host with tens of GB free, so the cap (not host pressure)
# was the bottleneck. A min/max range instead guarantees OmniRoute a real
# floor while capping how far it can grow under a burst.
OMNIROUTE_CPU_MIN = 1
OMNIROUTE_CPU_MAX = 4
OMNIROUTE_MEMORY_MIN_MIB = 1024
OMNIROUTE_MEMORY_MAX_MIB = 3072

# remote-setup is a small stdlib-only Python HTTP server used solely for the
# one-time /setup.sh and /config remote-agent bootstrap flow -- observed
# real usage is ~15MB baseline, ~41.8MB peak, essentially idle. Same
# min/max shape as OmniRoute, scaled down to what it actually needs: 64 MiB
# is an intentionally tight-but-sufficient cap (~22MB of headroom over the
# observed ~41.8MB peak), not a generous one, but still capped rather than
# left fully uncapped (its previous state) so a bug in it can't consume the
# host floor meant for everything else.
REMOTE_SETUP_CPU_MIN = 1
REMOTE_SETUP_CPU_MAX = 1
REMOTE_SETUP_MEMORY_MIN_MIB = 32
REMOTE_SETUP_MEMORY_MAX_MIB = 64

# OmniRoute's own chat-admission gate (src/shared/middleware/chatBodyAdmission.ts
# upstream) bounds concurrent "heavy" requests (large tool/context bodies --
# routine for coding agents) to avoid heap amplification. The queue wait is
# extended to 8000ms from the 2s default so a legitimate burst of concurrent
# heavy requests serializes through the gate instead of failing immediately.
OMNIROUTE_CHAT_ADMISSION_QUEUE_MS = 8000


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
    cpu_floor = HOST_CPU_FLOOR + OMNIROUTE_CPU_MIN + REMOTE_SETUP_CPU_MIN
    memory_floor_mib = (
        HOST_MEMORY_FLOOR_MIB + OMNIROUTE_MEMORY_MIN_MIB + REMOTE_SETUP_MEMORY_MIN_MIB
    )
    if llm_server_enabled:
        if host_cpu_count <= cpu_floor:
            raise ResourceError(
                f"host has {host_cpu_count} CPUs; more than {cpu_floor} are "
                "required to reserve the host floor and OmniRoute's/"
                "remote-setup's fixed allocations and still run llm-server"
            )
        if host_memory_total_mib <= memory_floor_mib:
            raise ResourceError(
                f"host has {host_memory_total_mib} MiB RAM; more than "
                f"{memory_floor_mib} MiB is required to reserve the host floor "
                "and OmniRoute's/remote-setup's fixed allocations and still "
                "run llm-server"
            )
    else:
        if host_cpu_count < cpu_floor:
            raise ResourceError(
                f"host has {host_cpu_count} CPUs; at least {cpu_floor} are "
                "required to reserve the host floor and OmniRoute's/"
                "remote-setup's fixed allocations"
            )
        if host_memory_total_mib < memory_floor_mib:
            raise ResourceError(
                f"host has {host_memory_total_mib} MiB RAM; at least "
                f"{memory_floor_mib} MiB is required to reserve the host floor "
                "and OmniRoute's/remote-setup's fixed allocations"
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
            "cpus_min": OMNIROUTE_CPU_MIN,
            "cpus_max": OMNIROUTE_CPU_MAX,
            "memory_min_mib": OMNIROUTE_MEMORY_MIN_MIB,
            "memory_max_mib": OMNIROUTE_MEMORY_MAX_MIB,
        },
        "remote_setup": {
            "cpus_min": REMOTE_SETUP_CPU_MIN,
            "cpus_max": REMOTE_SETUP_CPU_MAX,
            "memory_min_mib": REMOTE_SETUP_MEMORY_MIN_MIB,
            "memory_max_mib": REMOTE_SETUP_MEMORY_MAX_MIB,
        },
        "llm_server": {
            "cpus": llm_server_cpus,
            "memory_mib": llm_server_memory_mib,
        },
    }
