"""Host Prometheus metrics: fixed exposition for container and OmniRoute sources."""

from __future__ import annotations

import http.client
import json
import math
import queue
import subprocess
import threading
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pylib.omniroute_activity import (
    PRIME_GAP_S,
    Deferral,
    bounded_get,
    bounded_login,
    epoch_ms,
)
from pylib.resource_benchmark import CGROUP_ROOT
from pylib.resources import LIVE_CONTAINERS, live_resources

# Fixed families only: arbitrary field names and values from the sources are never serialized.
_CONTAINER_GAUGES = (
    ("llm_env_container_running", None),  # synthetic from state
    ("llm_env_container_memory_current_mib", "current_mib"),
    ("llm_env_container_memory_peak_mib", "peak_mib"),
    ("llm_env_container_memory_cap_mib", "cap_mib"),
    ("llm_env_container_memory_swap_mib", "swap_mib"),
    ("llm_env_container_cpu_avg_cores", "cpu_avg_cores"),
    ("llm_env_container_memory_psi_full_avg300", "psi_full_avg300"),
    ("llm_env_container_uptime_seconds", "uptime_s"),
    ("llm_env_container_vram_mib", "vram_mib"),
)
_CONTAINER_COUNTERS = (
    ("llm_env_container_memory_events_max_total", ("events", "max")),
    ("llm_env_container_oom_kills_total", ("events", "oom_kill")),
)
_HEALTH_FAMILIES = (
    ("llm_env_omniroute_active_heavy", "gauge", "chatAdmission.activeHeavy"),
    ("llm_env_omniroute_waiting", "gauge", "chatAdmission.waiting"),
    ("llm_env_omniroute_chat_sheds_total", "counter", "chatAdmission.shedTotal"),
    ("llm_env_omniroute_guard_rejects_total", "counter", "adaptiveAdmission.pressureGuardRejectCount"),
)
_CPU_HELP = ("CPU cores as a lifetime average since container start (usage_usec / 1e6 / uptime); "
             "it flattens after long uptime and is not current load.")


def _fmt(value: Any) -> str | None:
    """A Prometheus numeric literal, or None for absent/bool/non-number/nonfinite values."""
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return str(value)


def _running_value(row: Mapping[str, Any]) -> int | None:
    state = row.get("state")
    if state == "running":
        return 1
    if state == "not running":
        return 0
    return None  # unknown row: emits nothing


def _container_value(row: Mapping[str, Any], key: Any) -> Any:
    if isinstance(key, tuple):  # the only nested mapping is `events`
        parent, child = key
        nested = row.get(parent)
        return nested.get(child) if isinstance(nested, Mapping) else None
    return row.get(key)  # every other key is flat (including psi_full_avg300)


def _dig(doc: Any, dotted: str) -> Any:
    cur = doc
    for part in dotted.split("."):
        if not isinstance(cur, Mapping) or part not in cur:
            return None
        cur = cur[part]
    return cur


def render_metrics(
    live: Mapping[str, Any] | None,
    health: Mapping[str, Any] | None,
    *,
    containers_incomplete: bool = False,
) -> str:
    families: list[tuple[str, str, str | None, list[str]]] = []

    up_lines = [
        f'llm_env_source_up{{source="{src}"}} {int(up)}'
        for src, up in (
            ("containers", not (live is None or containers_incomplete)),
            ("omniroute", health is not None),
        )
    ]
    families.append(("llm_env_source_up", "gauge",
                     "Whether a metrics source was available for this collection (1) or not (0).", up_lines))

    rows: dict[str, Mapping[str, Any]] = {}
    if live is not None:
        for svc in LIVE_CONTAINERS:
            row = live.get(svc)
            if isinstance(row, Mapping) and _running_value(row) is not None:
                rows[svc] = row

    for name, key in _CONTAINER_GAUGES:
        samples = []
        for svc, row in rows.items():
            value = _running_value(row) if key is None else _container_value(row, key)
            literal = _fmt(value)
            if literal is not None:
                samples.append(f'{name}{{service="{svc}"}} {literal}')
        families.append((name, "gauge", _CPU_HELP if name == "llm_env_container_cpu_avg_cores" else None, samples))

    for name, key in _CONTAINER_COUNTERS:
        samples = []
        for svc, row in rows.items():
            if _running_value(row) == 1:  # a confirmed-stopped row has no counter samples
                literal = _fmt(_container_value(row, key))
                if literal is not None:
                    samples.append(f'{name}{{service="{svc}"}} {literal}')
        families.append((name, "counter", None, samples))

    for name, kind, key in _HEALTH_FAMILIES:
        literal = _fmt(_dig(health, key)) if health is not None else None
        samples = [f'{name}{{service="omniroute"}} {literal}'] if literal is not None else []
        families.append((name, kind, None, samples))

    out: list[str] = []
    for name, kind, help_text, samples in families:
        if not samples:
            continue  # # TYPE only for a present family
        if help_text:
            out.append(f"# HELP {name} {help_text}")
        out.append(f"# TYPE {name} {kind}")
        out.extend(samples)
    return "\n".join(out) + "\n"


def read_omniroute_health(
    port: int,
    password: str,
    *,
    deadline_s: float = 10,
    clock: Any = time.monotonic,
    sleep: Any = time.sleep,
) -> dict[str, Any] | None:
    """Fresh health doc, or None when the source is down, stale or unusable. Never logs token/password/body."""
    deadline = clock() + deadline_s
    base = f"http://127.0.0.1:{port}"
    path = "/api/monitoring/health"

    def fetch() -> tuple[dict[str, Any], int]:
        doc = json.loads(bounded_get(base, path, token, deadline))
        ts = epoch_ms(doc["timestamp"])
        wall_ms = int(time.time() * 1000)  # taken at this GET
        return doc, wall_ms - ts

    def fresh(age_ms: int) -> bool:
        return -2000 <= age_ms <= 35000

    try:
        token = bounded_login(base, password, deadline)
        bounded_get(base, path, token, deadline)  # prime: stale-while-revalidate endpoint
        if clock() >= deadline:
            return None
        sleep(PRIME_GAP_S)
        doc, age = fetch()
        if not fresh(age):
            if clock() >= deadline:
                return None
            sleep(1)
            doc, age = fetch()
            if not fresh(age):
                return None
        if all(_fmt(_dig(doc, key)) is None for _, _, key in _HEALTH_FAMILIES):
            return None
        return doc
    except (Deferral, ValueError, TypeError, KeyError, AttributeError):
        return None


def collect_metrics(
    limits: Mapping[str, Any],
    pci: str | None,
    *,
    port: int,
    password: str,
    deadline_s: float = 25,
) -> str:
    """One exposition snapshot from two bounded workers; a hung source is discarded, the peer is kept."""
    deadline = time.monotonic() + deadline_s
    container_q: queue.Queue = queue.Queue(maxsize=1)
    health_q: queue.Queue = queue.Queue(maxsize=1)

    def container_worker() -> None:
        unavailable: set[str] = set()

        def locate(container: str) -> tuple[Any, int, float] | None:
            try:
                out = subprocess.run(
                    ["podman", "inspect", "--type", "container", "-f",
                     "{{.State.Running}} {{.State.Pid}} {{.State.StartedAt.Unix}}", container],
                    capture_output=True, text=True, check=True, timeout=5,
                ).stdout.split()
            except (subprocess.TimeoutExpired, subprocess.CalledProcessError, OSError):
                unavailable.add(container)
                return None
            if len(out) != 3:
                unavailable.add(container)
                return None
            state, pid_text, started_text = out
            if state == "false":
                return None  # confirmed stopped, not unknown
            if state != "true":
                unavailable.add(container)
                return None
            try:
                pid, started = int(pid_text), float(started_text)
                line = next(x for x in Path(f"/proc/{pid}/cgroup").read_text().splitlines()
                            if x.startswith("0::"))
                cgroup = CGROUP_ROOT / line[3:].lstrip("/")
            except (ValueError, OSError, StopIteration, IndexError):
                unavailable.add(container)
                return None
            return cgroup, pid, started

        try:
            raw = live_resources(limits, pci, locate=locate, now=time.time())
            live = {svc: row for svc, row in raw.items() if LIVE_CONTAINERS[svc] not in unavailable}
            container_q.put((live, unavailable))
        except Exception:  # noqa: BLE001 -- a failed worker is a down source, never a traceback with config values
            container_q.put((None, True))

    def health_worker() -> None:
        try:
            health_q.put(read_omniroute_health(port, password, deadline_s=deadline_s))
        except Exception:  # noqa: BLE001 -- a failed worker is a down source, never a traceback
            health_q.put(None)

    container_thread = threading.Thread(target=container_worker, daemon=True)
    health_thread = threading.Thread(target=health_worker, daemon=True)
    container_thread.start()
    health_thread.start()

    container_thread.join(max(0.0, deadline - time.monotonic()))
    if container_thread.is_alive():
        live, containers_incomplete = None, True
    else:
        try:
            live, unavailable = container_q.get_nowait()
            containers_incomplete = bool(unavailable)
        except queue.Empty:
            live, containers_incomplete = None, True

    health_thread.join(max(0.0, deadline - time.monotonic()))
    if health_thread.is_alive():
        health = None
    else:
        try:
            health = health_q.get_nowait()
        except queue.Empty:
            health = None

    return render_metrics(live, health, containers_incomplete=containers_incomplete)


def publish_metrics(text: str) -> None:
    """POST the exposition to the local VictoriaMetrics importer; accept only 2xx, never log the body."""
    conn = http.client.HTTPConnection("127.0.0.1", 8428, timeout=5)
    try:
        conn.request("POST", "/api/v1/import/prometheus", body=text,
                     headers={"Content-Type": "text/plain; version=0.0.4"})
        response = conn.getresponse()
        status = response.status
        response.read()
        if not 200 <= status < 300:
            raise OSError(f"metrics publish returned HTTP {status}")
    finally:
        conn.close()
