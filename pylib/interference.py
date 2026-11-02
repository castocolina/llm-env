"""Interference gate: cgroup snapshots taken around a local-model load and their evaluation.

Rules (plan Decision 6, revised 2026-10-31): fail on (a) Laya non-200 during load, (b) Laya p95 under
load above an absolute service-level limit (500 ms) or p50 above 150 ms, (c) any memory.events counter
growth, (d) a counter reset, (e) no completed load request, (f) a service running before and not running
after, (g) no Laya latency sample under load, or System One running but never probed. Laya serves
advisory typed judgments with ~20 s client timeouts, so a relative p95 rise (> +50 % and > +10 ms) is
only reported as `regression_warning`. VRAM and host memory are recorded only.
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from pylib.detect import list_gpus
from pylib.resources import _locate, live_resources, read_live

EVENT_COUNTERS = ("high", "max", "oom", "oom_kill")


def percentile(values: Sequence[float], q: float) -> float | None:
    """Nearest rank, the rule of scripts/system-one-benchmark.sh."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(int(len(ordered) * q), len(ordered) - 1)]


def host_memory(meminfo: Path = Path("/proc/meminfo")) -> dict[str, int | None]:
    kb: dict[str, int] = {}
    try:
        for line in Path(meminfo).read_text().splitlines():
            parts = line.split()
            if len(parts) >= 3 and parts[2] == "kB" and parts[0] in ("MemAvailable:", "SwapTotal:", "SwapFree:"):
                kb[parts[0][:-1]] = int(parts[1])
    except (OSError, ValueError):
        pass
    swap = kb["SwapTotal"] - kb["SwapFree"] if "SwapTotal" in kb and "SwapFree" in kb else None
    return {
        "mem_available_mib": kb["MemAvailable"] // 1024 if "MemAvailable" in kb else None,
        "swap_used_mib": None if swap is None else swap // 1024,
    }


def snapshot(
    limits: Mapping[str, Any],
    pci: str | None,
    *,
    locate: Any = None,
    now: float | None = None,
    meminfo: Path = Path("/proc/meminfo"),
    drm_root: Path = Path("/sys/class/drm"),
) -> dict[str, Any]:
    locate = locate or _locate
    now = time.time() if now is None else now
    services = live_resources(limits, pci, locate=locate, now=now)
    found = locate("llm-server")
    if found is None:
        services["llm_server"] = {"state": "not running"}
    elif found[0] is None:
        services["llm_server"] = {"state": "running", "uptime_s": int(max(now - found[2], 1.0)), "verdict": "n/a"}
    else:
        services["llm_server"] = {
            "state": "running",
            **read_live(found[0], found[2], limits["llm_server"]["memory_mib"], now=now),
        }
    gpu = next((g for g in list_gpus(drm_root) if g["pci_address"] == pci), None) if pci else None
    return {
        "taken_at": now,
        "services": services,
        "host": host_memory(meminfo),
        "gpu": {
            "vram_used_mib": gpu["vram_used_mib"] if gpu else None,
            "vram_total_mib": gpu["vram_total_mib"] if gpu else None,
        },
    }


def append_sample(path: Path, snap: Mapping[str, Any]) -> None:
    with Path(path).open("a") as handle:
        handle.write(json.dumps(snap, separators=(",", ":")) + "\n")


def _lines(path: Path) -> list[list[str]]:
    try:
        return [line.split() for line in path.read_text().splitlines() if line.strip()]
    except OSError:
        return []


def _latency(path: Path) -> dict[str, Any]:
    rows = _lines(path)
    ms = []
    for r in rows:
        try:  # a sample needs a numeric second field; short or garbled rows are not samples
            ms.append(float(r[1]) * 1000)
        except (IndexError, ValueError):
            pass
    return {
        "n": len(ms),
        "p50_ms": percentile(ms, 0.5),
        "p95_ms": percentile(ms, 0.95),
        "non200": sum(r[0] != "200" for r in rows),
    }


def _latency_pair(run_dir: Path, name: str) -> dict[str, Any] | None:
    before, during = run_dir / f"{name}-before.txt", run_dir / f"{name}-during.txt"
    if not (before.is_file() and during.is_file()):
        return None
    pair = {"before": _latency(before), "during": _latency(during), "regression_pct": None}
    b, d = pair["before"]["p95_ms"], pair["during"]["p95_ms"]
    if b and d is not None:
        pair["regression_pct"] = (d - b) / b * 100
    return pair


def _jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    except OSError:
        return []


def _running(snap: Mapping[str, Any], service: str) -> bool:
    return (snap.get("services", {}).get(service) or {}).get("state") == "running"


def _min(values: list[int]) -> int | None:
    return min(values) if values else None


def _max(values: list[int]) -> int | None:
    return max(values) if values else None


def evaluate(
    run_dir: Path,
    *,
    max_p95_ms: float = 500.0,
    max_p50_ms: float = 150.0,
    warn_regression_pct: float = 50.0,
    warn_regression_ms: float = 10.0,
) -> dict[str, Any]:
    run_dir = Path(run_dir)
    failures: list[str] = []
    notes: list[str] = []
    regression_warning: str | None = None

    before = json.loads((run_dir / "live-before.json").read_text())
    after = json.loads((run_dir / "live-after.json").read_text())
    system_one = _latency_pair(run_dir, "laya")
    omniroute = _latency_pair(run_dir, "omniroute")
    if system_one is None:
        if _running(before, "system_one"):
            failures.append("system_one was running but its latency was not measured")
        else:
            notes.append("system_one latency not measured (System One disabled)")
    else:
        if not system_one["during"]["n"]:
            failures.append("system_one: no latency sample under load")
        if system_one["during"]["non200"]:
            failures.append(f"system_one returned {system_one['during']['non200']} non-200 response(s) during load")
        b, d = system_one["before"]["p95_ms"], system_one["during"]["p95_ms"]
        p50 = system_one["during"]["p50_ms"]
        if d is not None and d > max_p95_ms:
            failures.append(f"system_one p95 {d:.1f} ms under load exceeds the {max_p95_ms:g} ms limit")
        if p50 is not None and p50 > max_p50_ms:
            failures.append(f"system_one p50 {p50:.1f} ms under load exceeds the {max_p50_ms:g} ms limit")
        if b is not None and d is not None and d > b * (1 + warn_regression_pct / 100) and d - b > warn_regression_ms:
            regression_warning = (
                f"system_one p95 rose from {b:.1f} ms to {d:.1f} ms during load "
                f"(warning above {warn_regression_pct:g} % and {warn_regression_ms:g} ms; not a failure)"
            )

    services_before = before.get("services", {})
    services_after = after.get("services", {})

    stopped = [s for s in services_before if _running(before, s) and not _running(after, s)]
    failures += [f"{s} stopped during the window" for s in stopped]

    events_changed: list[dict[str, Any]] = []
    reset: list[str] = []
    for service, row_before in services_before.items():
        if service in stopped or not _running(before, service):
            continue
        row_after = services_after[service]
        up_b, up_a = row_before.get("uptime_s"), row_after.get("uptime_s")
        if up_b is not None and up_a is not None and up_a < up_b:
            reset.append(service)
            failures.append(f"{service} restarted between snapshots (uptime {up_b} s -> {up_a} s)")
            continue
        for counter in EVENT_COUNTERS:
            b = (row_before.get("events") or {}).get(counter)
            a = (row_after.get("events") or {}).get(counter)
            if (a or 0) > (b or 0):
                events_changed.append({"service": service, "counter": counter, "before": b, "after": a})
                failures.append(f"{service} memory.events {counter} grew from {b or 0} to {a}")

    samples = _jsonl(run_dir / "samples.jsonl")

    def series(group: str, key: str) -> list[int]:
        return [s[group][key] for s in samples if (s.get(group) or {}).get(key) is not None]

    vram_total = _max(series("gpu", "vram_total_mib"))
    vram_peak = _max(series("gpu", "vram_used_mib"))
    swap_before = (before.get("host") or {}).get("swap_used_mib")
    swap_max = _max(series("host", "swap_used_mib"))
    llm = services_after.get("llm_server") or {}

    load = _jsonl(run_dir / "load.jsonl")
    ok = [r for r in load if str(r.get("status")) == "200"]
    if not ok:
        failures.append("no load request completed within the window")

    return {
        "schema": 1,
        "passed": not failures,
        "failures": failures,
        "notes": notes,
        "regression_warning": regression_warning,
        "thresholds": {
            "max_p95_ms": max_p95_ms,
            "max_p50_ms": max_p50_ms,
            "warn_regression_pct": warn_regression_pct,
            "warn_regression_ms": warn_regression_ms,
        },
        "system_one": system_one,
        "omniroute": omniroute,
        "events_changed": events_changed,
        "counters_reset": reset,
        "services_stopped": stopped,
        "vram": {
            "total_mib": vram_total,
            "peak_used_mib": vram_peak,
            "headroom_min_mib": None if vram_total is None or vram_peak is None else vram_total - vram_peak,
        },
        "host": {
            "mem_available_before_mib": (before.get("host") or {}).get("mem_available_mib"),
            "mem_available_min_mib": _min(series("host", "mem_available_mib")),
            "swap_used_before_mib": swap_before,
            "swap_used_max_mib": swap_max,
            "swap_delta_mib": None if swap_before is None or swap_max is None else swap_max - swap_before,
        },
        "llm_server": {"memory_peak_mib": llm.get("peak_mib"), "memory_cap_mib": llm.get("cap_mib")},
        "load": {
            "requests": len(load),
            "ok": len(ok),
            "prompt_tokens_max": max((r.get("prompt_tokens") or 0 for r in load), default=0),
            "completion_tokens_total": sum(r.get("completion_tokens") or 0 for r in load),
        },
    }
