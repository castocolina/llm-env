"""Conservative host CPU/RAM budget and per-service Compose policy.

The host floor reserves budget in this calculation; it does not allocate
resources at the OS/cgroup level. ``cpus_min`` is a relative CPU-share
weight input (converted to Compose ``cpu_shares``), not a core reservation.
``cpus_max`` is a hard quota. Memory minima are soft Compose reservations;
maxima are hard cgroup limits. Neither minimum is guaranteed allocation.
"""

from __future__ import annotations

from typing import Any

# Fixed floor reserved for the host OS and other applications.
HOST_CPU_FLOOR = 2
HOST_MEMORY_FLOOR_MIB = 4096

# OmniRoute is a lightweight Node/Next.js process, not the workload driving
# resource needs here -- it gets a flat min/max policy, not a share of the
# remainder. Minima are included in the conservative llm_server host-budget
# calculation, but cpu_shares is only a relative contention weight and
# mem_reservation is soft policy; neither is an OS-guaranteed allocation.
#
# A flat 1024 MiB cap proved too tight in practice: OmniRoute's own internal
# resourcePressure guard (cgroup_ratio) was observed tripping and returning
# 503s under concurrent cross-AI/tool-heavy sessions (my-coding,
# my-plan-review combos with 11-17 tools each) while sitting at only ~700MB
# baseline -- on a host with tens of GB free, so the cap (not host pressure)
# was the bottleneck. The min/max policy supplies contention weight and a
# soft memory reservation while capping growth under a burst; it does not
# guarantee an absolute CPU or memory allocation.
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

SYSTEM_ONE_CPUS = 2  # hard cap; 16 clients used ~1.3 cores
SYSTEM_ONE_MEMORY_MIB = 4608  # hard cap; memory.peak was 3155 MiB
# Conservative host budget includes these policy minima. Steady RSS is ~2.4 GB; idle CPU is ~0.
SYSTEM_ONE_CPUS_MIN = 1
SYSTEM_ONE_MEMORY_MIN_MIB = 2560

# Phoenix dashboard (pinned arizephoenix/phoenix:20.23.0), started with
# System One on its own internal compose project. Resource POLICY starting
# point, justified by the 2026-10-19 measurements of a live private
# instance: ~128 MiB current / ~526 MiB lifetime-peak memory, ~0.17% of one
# core over a 2 s idle sample, plus short synthetic-ingestion headroom.
# This is NOT a supported minimum and NOT a load benchmark; if a capped
# startup/ingestion/browser workflow fails, record the measurement before
# adjusting. cpus_min is in compose "share units" (baseline 1024 = 1 unit):
# it renders to cpu_shares, a RELATIVE WEIGHT under contention, not
# guaranteed cores; cpus_max/memory_max_mib are hard cgroup caps.
# memory_min_mib is a soft Compose reservation; this host's observed
# memory.min was zero, so do not claim an enforced cgroup floor.
PHOENIX_CPUS_MIN = 0.25  # -> cpu_shares 256
PHOENIX_CPUS_MAX = 2  # hard cap
PHOENIX_MEMORY_MIN_MIB = 256  # soft reservation
PHOENIX_MEMORY_MAX_MIB = 1024  # hard cap

# OmniRoute's own chat-admission gate (src/shared/middleware/chatBodyAdmission.ts
# upstream) bounds concurrent "heavy" requests (large tool/context bodies --
# routine for coding agents) to avoid heap amplification. The queue wait is
# extended to 8000ms from the 2s default so a legitimate burst of concurrent
# heavy requests serializes through the gate instead of failing immediately.
OMNIROUTE_CHAT_ADMISSION_QUEUE_MS = 8000


class ResourceError(Exception):
    """Raised when the conservative host budget leaves too little for services."""


def _floor_words(system_one_enabled: bool) -> str:
    words = "the conservative host budget and OmniRoute/remote-setup policy minima"
    if system_one_enabled:
        words += (
            f", System One's budgeted minimum ({SYSTEM_ONE_CPUS_MIN} CPU-share unit, "
            f"{SYSTEM_ONE_MEMORY_MIN_MIB} MiB) and Phoenix's budgeted minimum "
            f"({PHOENIX_CPUS_MIN} CPU share units, {PHOENIX_MEMORY_MIN_MIB} MiB)"
        )
    return words


def compute_resource_limits(
    host_cpu_count: int,
    host_memory_total_mib: int,
    memory_ceiling_pct: float = 100,
    memory_ceiling_floor_pct: float = 20,
    cpu_ceiling_pct: float = 100,
    cpu_ceiling_floor_pct: float = 20,
    llm_server_enabled: bool = True,
    system_one_enabled: bool = False,
) -> dict[str, Any]:
    cpu_floor = HOST_CPU_FLOOR + OMNIROUTE_CPU_MIN + REMOTE_SETUP_CPU_MIN
    memory_floor_mib = (
        HOST_MEMORY_FLOOR_MIB + OMNIROUTE_MEMORY_MIN_MIB + REMOTE_SETUP_MEMORY_MIN_MIB
    )
    if system_one_enabled:
        cpu_floor += SYSTEM_ONE_CPUS_MIN
        memory_floor_mib += SYSTEM_ONE_MEMORY_MIN_MIB
        # Phoenix shares the System One enable flag: it is charged whenever
        # System One is on, including the llm-server-disabled gateway mode.
        cpu_floor += PHOENIX_CPUS_MIN
        memory_floor_mib += PHOENIX_MEMORY_MIN_MIB
    if llm_server_enabled:
        if host_cpu_count <= cpu_floor:
            raise ResourceError(
                f"host has {host_cpu_count} CPUs; more than {cpu_floor} are "
                f"required to reserve {_floor_words(system_one_enabled)} and still "
                "run llm-server"
            )
        if host_memory_total_mib <= memory_floor_mib:
            raise ResourceError(
                f"host has {host_memory_total_mib} MiB RAM; more than "
                f"{memory_floor_mib} MiB is required to reserve "
                f"{_floor_words(system_one_enabled)} and still run llm-server"
            )
    else:
        if host_cpu_count < cpu_floor:
            raise ResourceError(
                f"host has {host_cpu_count} CPUs; at least {cpu_floor} are "
                f"required to reserve {_floor_words(system_one_enabled)}"
            )
        if host_memory_total_mib < memory_floor_mib:
            raise ResourceError(
                f"host has {host_memory_total_mib} MiB RAM; at least "
                f"{memory_floor_mib} MiB is required to reserve "
                f"{_floor_words(system_one_enabled)}"
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
        "system_one": {
            "cpus": SYSTEM_ONE_CPUS if system_one_enabled else 0,
            "memory_mib": SYSTEM_ONE_MEMORY_MIB if system_one_enabled else 0,
        },
        "phoenix": {
            "cpus_min": PHOENIX_CPUS_MIN if system_one_enabled else 0,
            "cpus_max": PHOENIX_CPUS_MAX if system_one_enabled else 0,
            "memory_min_mib": PHOENIX_MEMORY_MIN_MIB if system_one_enabled else 0,
            "memory_max_mib": PHOENIX_MEMORY_MAX_MIB if system_one_enabled else 0,
        },
    }
