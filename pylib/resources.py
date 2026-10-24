"""Conservative host CPU/RAM budget and per-service Compose policy.

The host floor reserves budget in this calculation; it does not allocate
resources at the OS/cgroup level. ``cpus_min`` is a relative CPU-share
weight input (converted to Compose ``cpu_shares``), not a core reservation.
``cpus_max`` is a hard quota. Memory minima are soft Compose reservations;
maxima are hard cgroup limits. Neither minimum is guaranteed allocation.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any, TypeGuard

# Fixed floor reserved for the host OS and other applications.
HOST_CPU_FLOOR = 2
HOST_MEMORY_FLOOR_MIB = 4096

# Sizing rule (owner, 2026-10-24; data and limits in
# docs/superpowers/research/2026-10-24-benchmark-derived-sizing.md):
#   memory_min_mib = resident memory (memory.current) the container keeps under use + 20 %
#   memory_max_mib = highest observed resident memory or memory.peak + 20 %
# preferring the modal level to the mean; swap is reported, never added. Re-derive
# with `make resources-benchmark`. memory_min_mib renders to mem_reservation
# (cgroup memory.low), a soft hint: on this host every ancestor cgroup has
# memory.low 0, so it protects nothing and only feeds the budget arithmetic below.
# cpus_min renders to cpu_shares (a relative weight); cpus_max and memory_max_mib
# are hard cgroup caps.

# OmniRoute (Node/Next.js gateway): fresh loaded process ~640 MiB resident
# (min 768); the live lifetime peak of 1,651 MiB after 5 days of retention sets
# the cap (1,651 * 1.2 -> 2048). The benchmark alone cannot derive this cap: its
# fake upstream understates tool-heavy traffic. The admission healer recycles an
# idle process above 60 % of this cap (scripts/heal-omniroute-admission.sh).
OMNIROUTE_CPU_MIN = 1
OMNIROUTE_CPU_MAX = 2
OMNIROUTE_MEMORY_MIN_MIB = 768
OMNIROUTE_MEMORY_MAX_MIB = 2048

# remote-setup (stdlib Python HTTP server for the one-time remote bootstrap):
# 17 MiB resident, lifetime peak 39 MiB (39 * 1.2 = 47 -> kept at 64).
REMOTE_SETUP_CPU_MIN = 0.25
REMOTE_SETUP_CPU_MAX = 0.5
REMOTE_SETUP_MEMORY_MIN_MIB = 32
REMOTE_SETUP_MEMORY_MAX_MIB = 64

# System One (Laya) is an EXPLICIT EXCEPTION to the sizing rule. Its hot resident
# set is ~3.87 GiB (rule: min 4,640) and its lifetime memory.peak (4,587 MiB) was
# clipped at the old 4608 MiB cap (166 memory.events max), so no ceiling could be
# derived from it. The cap was raised 4608 -> 5120 on 2026-10-24 to let the true
# peak show; the peak is a model-load transient (idle ~2.5 GiB, loaded ~3.9 GiB
# resident, peak at (re)load). The reservation stays at 2560. Revisit after a few
# days of `make resources-snapshot` data.
SYSTEM_ONE_CPUS_MIN = 1
SYSTEM_ONE_CPUS = 2  # hard cap; CPU peak 1.07-1.55 cores
SYSTEM_ONE_MEMORY_MIN_MIB = 2560
SYSTEM_ONE_MEMORY_MIB = 5120  # hard cap; see the exception above

# Phoenix (pinned arizephoenix/phoenix), started with System One in its own
# compose project: ~150 MiB resident under a short benchmark, but 483 MiB
# resident and a 804 MiB peak 2 h after a restart with real Laya traffic
# (804 * 1.2 = 965 -> 1024). Re-derive the reservation over hours, not minutes.
PHOENIX_CPUS_MIN = 0.25  # -> cpu_shares 256
PHOENIX_CPUS_MAX = 1
PHOENIX_MEMORY_MIN_MIB = 192
PHOENIX_MEMORY_MAX_MIB = 1024

# OmniRoute's own chat-admission gate (src/shared/middleware/chatBodyAdmission.ts
# upstream) bounds concurrent "heavy" requests (large tool/context bodies --
# routine for coding agents) to avoid heap amplification. The queue wait is
# extended to 8000ms from the 2s default so a legitimate burst of concurrent
# heavy requests serializes through the gate instead of failing immediately.
OMNIROUTE_CHAT_ADMISSION_QUEUE_MS = 8000

# Built-in per-service policy (benchmark-derived, see the sizing rule above).
# Persistent `resources.overrides.<service>` entries in models.yml merge over it.
DEFAULT_POLICY: dict[str, dict[str, Any]] = {
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
    "system_one": {
        "cpus_min": SYSTEM_ONE_CPUS_MIN,
        "cpus_max": SYSTEM_ONE_CPUS,
        "memory_min_mib": SYSTEM_ONE_MEMORY_MIN_MIB,
        "memory_max_mib": SYSTEM_ONE_MEMORY_MIB,
    },
    "phoenix": {
        "cpus_min": PHOENIX_CPUS_MIN,
        "cpus_max": PHOENIX_CPUS_MAX,
        "memory_min_mib": PHOENIX_MEMORY_MIN_MIB,
        "memory_max_mib": PHOENIX_MEMORY_MAX_MIB,
    },
}
OVERRIDABLE_SERVICES = tuple(DEFAULT_POLICY)
POLICY_KEYS = ("cpus_min", "cpus_max", "memory_min_mib", "memory_max_mib")


def _is_finite_number(value: object) -> TypeGuard[int | float]:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _is_int_at_least(value: object, minimum: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= minimum


def validate_overrides(overrides: object) -> list[str]:
    """Return human-readable errors for a `resources.overrides` value.

    Every policy value must be strictly positive: 0 means "no limit" to
    pylib/compose.py, so an override of 0 would silently uncap a container.
    A memory_max_mib below the built-in default is allowed; summarize_policy
    and `resource-override set` warn about it (see lowered_cap_warnings).
    """
    if overrides is None:
        return []
    if not isinstance(overrides, Mapping):
        return ["resources.overrides must be a mapping"]
    errors: list[str] = []
    for service, override in overrides.items():
        prefix = f"resources.overrides.{service}"
        if service not in DEFAULT_POLICY:
            errors.append(
                f"{prefix} is not an overridable service "
                f"(expected one of {', '.join(OVERRIDABLE_SERVICES)})"
            )
            continue
        if not isinstance(override, Mapping):
            errors.append(f"{prefix} must be a mapping")
            continue
        service_errors: list[str] = []
        for key, value in override.items():
            if key not in POLICY_KEYS:
                service_errors.append(
                    f"{prefix}.{key} is not an overridable field "
                    f"(expected one of {', '.join(POLICY_KEYS)})"
                )
            elif key.startswith("cpus"):
                if not (_is_finite_number(value) and value > 0):
                    service_errors.append(
                        f"{prefix}.{key} must be a positive number (0 would remove the limit)"
                    )
            elif not _is_int_at_least(value, 1):
                service_errors.append(
                    f"{prefix}.{key} must be a positive integer (0 would remove the limit)"
                )
        errors.extend(service_errors)
        if service_errors:
            continue
        merged = effective_policy(service, {service: override})
        for low, high in (("cpus_min", "cpus_max"), ("memory_min_mib", "memory_max_mib")):
            if merged[low] > merged[high]:
                errors.append(
                    f"{prefix}: effective {low} ({merged[low]}) must not exceed "
                    f"effective {high} ({merged[high]})"
                )
    return errors


def effective_policy(
    service: str, overrides: Mapping[str, Any] | None
) -> dict[str, Any]:
    """Built-in policy for `service` with validated overrides merged over it."""
    override = (overrides or {}).get(service) or {}
    return {
        **DEFAULT_POLICY[service],
        **{key: value for key, value in override.items() if key in POLICY_KEYS},
    }


class ResourceError(Exception):
    """Raised when the conservative host budget leaves too little for services."""


def _floor_words(
    system_one_enabled: bool,
    system_one: Mapping[str, Any],
    phoenix: Mapping[str, Any],
) -> str:
    words = "the conservative host budget and OmniRoute/remote-setup policy minima"
    if system_one_enabled:
        words += (
            f", System One's budgeted minimum ({system_one['cpus_min']} CPU-share unit, "
            f"{system_one['memory_min_mib']} MiB) and Phoenix's budgeted minimum "
            f"({phoenix['cpus_min']} CPU share units, {phoenix['memory_min_mib']} MiB)"
        )
    return words


def compute_resource_limits(
    host_cpu_count: float,
    host_memory_total_mib: int,
    memory_ceiling_pct: float = 100,
    memory_ceiling_floor_pct: float = 20,
    cpu_ceiling_pct: float = 100,
    cpu_ceiling_floor_pct: float = 20,
    llm_server_enabled: bool = True,
    system_one_enabled: bool = False,
    overrides: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    override_errors = validate_overrides(overrides)
    if override_errors:
        raise ResourceError("; ".join(override_errors))
    omniroute = effective_policy("omniroute", overrides)
    remote_setup = effective_policy("remote_setup", overrides)
    system_one = effective_policy("system_one", overrides)
    phoenix = effective_policy("phoenix", overrides)
    cpu_floor = HOST_CPU_FLOOR + omniroute["cpus_min"] + remote_setup["cpus_min"]
    memory_floor_mib = (
        HOST_MEMORY_FLOOR_MIB
        + omniroute["memory_min_mib"]
        + remote_setup["memory_min_mib"]
    )
    if system_one_enabled:
        cpu_floor += system_one["cpus_min"]
        memory_floor_mib += system_one["memory_min_mib"]
        # Phoenix shares the System One enable flag: it is charged whenever
        # System One is on, including the llm-server-disabled gateway mode.
        cpu_floor += phoenix["cpus_min"]
        memory_floor_mib += phoenix["memory_min_mib"]
    if llm_server_enabled:
        if host_cpu_count <= cpu_floor:
            raise ResourceError(
                f"host has {host_cpu_count} CPUs; more than {cpu_floor} are "
                f"required to reserve {_floor_words(system_one_enabled, system_one, phoenix)} and still "
                "run llm-server"
            )
        if host_memory_total_mib <= memory_floor_mib:
            raise ResourceError(
                f"host has {host_memory_total_mib} MiB RAM; more than "
                f"{memory_floor_mib} MiB is required to reserve "
                f"{_floor_words(system_one_enabled, system_one, phoenix)} and still run llm-server"
            )
    else:
        if host_cpu_count < cpu_floor:
            raise ResourceError(
                f"host has {host_cpu_count} CPUs; at least {cpu_floor} are "
                f"required to reserve {_floor_words(system_one_enabled, system_one, phoenix)}"
            )
        if host_memory_total_mib < memory_floor_mib:
            raise ResourceError(
                f"host has {host_memory_total_mib} MiB RAM; at least "
                f"{memory_floor_mib} MiB is required to reserve "
                f"{_floor_words(system_one_enabled, system_one, phoenix)}"
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

    system_one_policy = (
        system_one if system_one_enabled else dict.fromkeys(POLICY_KEYS, 0)
    )
    phoenix_policy = phoenix if system_one_enabled else dict.fromkeys(POLICY_KEYS, 0)
    active = ["omniroute", "remote_setup"] + (
        ["system_one", "phoenix"] if system_one_enabled else []
    )
    overrides_applied = {
        service: sorted(key for key in overrides[service] if key in POLICY_KEYS)
        for service in active
        if overrides and overrides.get(service)
    }
    policies = [omniroute, remote_setup, system_one_policy, phoenix_policy]
    return {
        "host_cpu_floor": HOST_CPU_FLOOR,
        "host_memory_floor_mib": HOST_MEMORY_FLOOR_MIB,
        "omniroute": omniroute,
        "remote_setup": remote_setup,
        "llm_server": {
            "cpus": llm_server_cpus,
            "memory_mib": llm_server_memory_mib,
        },
        "system_one": system_one_policy,
        "phoenix": phoenix_policy,
        "overrides_applied": overrides_applied,
        "totals": {
            "hard_memory_cap_mib": llm_server_memory_mib
            + sum(policy["memory_max_mib"] for policy in policies),
            "soft_memory_reservation_mib": sum(
                policy["memory_min_mib"] for policy in policies
            ),
        },
    }


def summarize_policy(limits: Mapping[str, Any]) -> list[str]:
    totals = limits["totals"]
    lines = [
        (
            f"hard memory caps total {totals['hard_memory_cap_mib']} MiB; "
            f"soft reservations (memory.low, not allocated RAM) total "
            f"{totals['soft_memory_reservation_mib']} MiB"
        )
    ]
    applied = limits["overrides_applied"]
    if applied:
        lines.append(
            "resource overrides applied: "
            + "; ".join(f"{service}={','.join(keys)}" for service, keys in applied.items())
        )
    return lines + lowered_cap_warnings(limits)


def lowered_cap_warnings(policies: Mapping[str, Any]) -> list[str]:
    """One warning per service whose memory cap is below its built-in default.

    Lowering is allowed (one owner, rare action); the warning keeps it visible
    and points at the tool that re-derives the numbers. Disabled services carry
    an all-zero policy and are skipped.
    """
    return [
        (
            f"WARNING: {service} memory_max_mib {policy['memory_max_mib']} MiB is below its "
            f"benchmark-derived default {DEFAULT_POLICY[service]['memory_max_mib']} MiB; "
            "re-derive it with `make resources-benchmark` before relying on it"
        )
        for service in OVERRIDABLE_SERVICES
        if (policy := policies.get(service))
        and 0 < policy["memory_max_mib"] < DEFAULT_POLICY[service]["memory_max_mib"]
    ]


# --- Live counters read from each container's host cgroup (`make resources-snapshot`) ---
# Verdict thresholds (documented in LIVE_FOOTER and AGENTS.md):
#   ALERT: oom_kill > 0, peak/cap >= 0.92 (the guard's criticalRatio) or PSI full avg300 >= 5
#   WATCH: events max > 0, peak/cap >= 0.85 (the guard's highRatio), PSI some avg300 >= 10,
#          or the configured cap differs from the cgroup's memory.max (restart pending)
ALERT_PEAK_RATIO = 0.92
ALERT_PSI_FULL_AVG300 = 5.0
WATCH_PEAK_RATIO = 0.85
WATCH_PSI_SOME_AVG300 = 10.0
LIVE_CONTAINERS = {
    "omniroute": "omniroute",
    "remote_setup": "remote-setup",
    "system_one": "system-one",
    "phoenix": "llm-env-phoenix_phoenix_1",
}
LIVE_FOOTER = [
    "Counters start over when a container is recreated (`make restart`, update); uptime is the age of the current counters.",
    "events h/m/o/k = memory.events high/max/oom/oom_kill; psi = memory.pressure avg10/avg300 (some, full); cpu = average cores since start.",
    ("Verdict: ALERT = oom_kill > 0, peak/cap >= 0.92 or PSI full avg300 >= 5; WATCH = events max > 0, peak/cap >= 0.85, "
     "PSI some avg300 >= 10 or cap != cgroup memory.max (restart pending); OK otherwise."),
]
_MIB = 1024 * 1024


def _locate(container: str) -> tuple[Any, int, float] | None:
    """(cgroup dir or None, pid, StartedAt epoch) of a running container, else None."""
    import subprocess

    from pylib.resource_benchmark import cgroup_dir

    try:
        out = subprocess.run(
            ["podman", "inspect", "-f", "{{.State.Running}} {{.State.Pid}} {{.State.StartedAt.Unix}}", container],
            capture_output=True, text=True, check=True,
        ).stdout.split()
        if out[0] != "true":
            return None
    except (OSError, subprocess.CalledProcessError, IndexError):
        return None
    try:
        cgroup = cgroup_dir(container)
    except (OSError, subprocess.CalledProcessError, StopIteration, ValueError):
        cgroup = None
    return cgroup, int(out[1]), float(out[2])


def _kv(path: Any) -> dict[str, str]:
    try:
        return dict(line.split(None, 1) for line in path.read_text().splitlines() if line.strip())
    except OSError:
        return {}


def _num(value: Any) -> int | None:
    try:
        return int(str(value).split()[0])
    except (ValueError, IndexError):
        return None


def _psi(cgroup: Any) -> dict[str, float | None]:
    out: dict[str, float | None] = {}
    for kind, text in _kv(cgroup / "memory.pressure").items():
        fields = dict(f.split("=") for f in text.split())
        for window in ("avg10", "avg300"):
            try:
                out[f"psi_{kind}_{window}"] = float(fields[window])
            except (KeyError, ValueError):
                out[f"psi_{kind}_{window}"] = None
    return out


def live_verdict(row: Mapping[str, Any]) -> str:
    peak = row.get("peak_over_cap")
    full300 = row.get("psi_full_avg300")
    some300 = row.get("psi_some_avg300")
    events = row.get("events") or {}
    if (events.get("oom_kill") or 0) > 0 or (peak or 0) >= ALERT_PEAK_RATIO or (full300 or 0) >= ALERT_PSI_FULL_AVG300:
        return "ALERT"
    if (
        (events.get("max") or 0) > 0
        or (peak or 0) >= WATCH_PEAK_RATIO
        or (some300 or 0) >= WATCH_PSI_SOME_AVG300
        or row.get("restart_pending")
    ):
        return "WATCH"
    return "OK"


def _mib(value: int | None) -> int | None:
    return None if value is None else value // _MIB


def _read(path: Any) -> str | None:
    try:
        return path.read_text().strip()
    except OSError:
        return None


def read_live(
    cgroup: Any, started: float, configured_cap_mib: int, *, now: float, vram: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Counters of one running container from its cgroup dir; unreadable values are None."""
    uptime = max(now - started, 1.0)
    current, peak = _num(_read(cgroup / "memory.current")), _num(_read(cgroup / "memory.peak"))
    cap_raw = _read(cgroup / "memory.max")
    cap = _num(cap_raw)  # "max" (unlimited) parses to None
    usec = _num(_kv(cgroup / "cpu.stat").get("usage_usec"))
    events = _kv(cgroup / "memory.events")
    row: dict[str, Any] = {
        "uptime_s": int(uptime),
        "current_mib": _mib(current),
        "peak_mib": _mib(peak),
        "cap_mib": _mib(cap),
        "current_over_cap": current / cap if current is not None and cap else None,
        "peak_over_cap": peak / cap if peak is not None and cap else None,
        "swap_mib": _mib(_num(_read(cgroup / "memory.swap.current"))),
        "swap_peak_mib": _mib(_num(_read(cgroup / "memory.swap.peak"))),
        "events": {k: _num(events.get(k)) for k in ("high", "max", "oom", "oom_kill")},
        "cpu_avg_cores": None if usec is None else usec / 1e6 / uptime,
        "restart_pending": cap_raw is not None and cap != configured_cap_mib * _MIB,
        **_psi(cgroup),
    }
    row["verdict"] = live_verdict(row)
    return {**row, **(vram or {})}


def live_resources(
    limits: Mapping[str, Any], pci: str | None = None, *, locate: Any = None, now: float | None = None
) -> dict[str, Any]:
    """Per enabled service: {"state": "not running"|"running", ...counters}. Never raises on missing files."""
    import time

    locate = locate or _locate
    now = time.time() if now is None else now
    out: dict[str, Any] = {}
    for service, container in LIVE_CONTAINERS.items():
        policy = limits[service]
        if policy["memory_max_mib"] <= 0:  # disabled service
            continue
        found = locate(container)
        if found is None:
            out[service] = {"state": "not running"}
            continue
        cgroup, pid, started = found
        if cgroup is None:
            out[service] = {"state": "running", "uptime_s": int(max(now - started, 1.0)), "verdict": "n/a"}
            continue
        vram = _vram(pid, pci) if service == "system_one" and pci else None
        out[service] = {"state": "running", **read_live(cgroup, started, policy["memory_max_mib"], now=now, vram=vram)}
    return out


def _vram(pid: int, pci: str) -> dict[str, Any]:
    """Laya process VRAM and the whole GPU's used VRAM (MiB); None when unreadable."""
    from pylib.detect import process_vram_mib
    from pylib.resource_benchmark import gpu_device

    try:
        device_used = int(gpu_device(pci=pci)[1].read_text().split()[0]) // _MIB
    except (ValueError, OSError):
        device_used = None
    return {"vram_mib": process_vram_mib(pid, pci), "device_vram_mib": device_used}


def _fmt(value: Any, spec: str = "") -> str:
    return "n/a" if value is None else format(value, spec)


def _uptime(seconds: int) -> str:
    days, rest = divmod(seconds, 86400)
    return f"{days}d{rest // 3600:02d}h" if days else f"{rest // 3600}h{rest % 3600 // 60:02d}m"


def format_live(live: Mapping[str, Any]) -> list[list[str]]:
    """Two tab-separated tables (header first) for `column -t -s <tab>`: memory, then pressure and activity."""
    memory: list[tuple[str, ...]] = [(
        "service", "uptime", "cur_mib", "peak_mib", "cap_mib", "cur/cap", "peak/cap", "swap_mib", "swap_peak", "verdict",
    )]
    activity: list[tuple[str, ...]] = [(
        "service", "events h/m/o/k", "psi some 10/300", "psi full 10/300", "cpu_cores", "vram_mib proc/gpu",
    )]
    for service, row in live.items():
        if row["state"] != "running" or "cap_mib" not in row:
            note = "not running" if row["state"] != "running" else "n/a (cgroup unreadable)"
            memory.append((service, _uptime(row["uptime_s"]) if "uptime_s" in row else "-") + ("-",) * 7 + (note,))
            activity.append((service,) + ("-",) * 5)
            continue
        ev = row["events"]
        memory.append((
            service, _uptime(row["uptime_s"]), _fmt(row["current_mib"]), _fmt(row["peak_mib"]), _fmt(row["cap_mib"]),
            _fmt(row["current_over_cap"], ".2f"), _fmt(row["peak_over_cap"], ".2f"), _fmt(row["swap_mib"]), _fmt(row["swap_peak_mib"]),
            row["verdict"] + (" (restart pending)" if row["restart_pending"] else ""),
        ))
        activity.append((
            service,
            "/".join(_fmt(ev[k]) for k in ("high", "max", "oom", "oom_kill")),
            f"{_fmt(row.get('psi_some_avg10'), '.2f')}/{_fmt(row.get('psi_some_avg300'), '.2f')}",
            f"{_fmt(row.get('psi_full_avg10'), '.2f')}/{_fmt(row.get('psi_full_avg300'), '.2f')}",
            _fmt(row["cpu_avg_cores"], ".2f"),
            f"{_fmt(row['vram_mib'])}/{_fmt(row['device_vram_mib'])}" if "vram_mib" in row else "-",
        ))
    return [["\t".join(r) for r in table] for table in (memory, activity)]
