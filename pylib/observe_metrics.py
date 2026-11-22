"""Host Prometheus metrics: fixed exposition for container and OmniRoute sources."""

from __future__ import annotations

import http.client
import json
import math
import os
import queue
import re
import subprocess
import threading
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from pathlib import Path as FilesystemPath
from typing import Any

from pylib.observe_fs import collect_fs
from pylib.observe_jev import collect_jev
from pylib.observe_laya_requests import collect_laya_requests
from pylib.observe_llama import collect_llama
from pylib.observe_native_http import collect_native_http
from pylib.observe_remote_setup import collect_remote_setup
from pylib.observe_usage import _load as load_usage_state
from pylib.observe_usage import _save as save_usage_state
from pylib.observe_usage import collect_usage
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
    ("llm_env_container_memory_anon_mib", "anon_mib"),
    ("llm_env_container_memory_file_mib", "file_mib"),
    ("llm_env_container_memory_cap_mib", "cap_mib"),
    ("llm_env_container_memory_swap_mib", "swap_mib"),
    ("llm_env_container_cpu_avg_cores", "cpu_avg_cores"),
    ("llm_env_container_memory_psi_full_avg300", "psi_full_avg300"),
    ("llm_env_container_uptime_seconds", "uptime_s"),
    ("llm_env_container_vram_mib", "vram_mib"),
)
_CONTAINER_COUNTERS = (
    ("llm_env_container_cpu_throttled_periods_total", "cpu_throttled_periods"),
    ("llm_env_container_cpu_throttled_seconds_total", "cpu_throttled_s"),
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
_NAME = re.compile(r"[a-zA-Z_:][a-zA-Z0-9_:]*\Z")
_NUMBER = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z")


def valid_exposition_fragment(text: str, declared: set[str] | None = None) -> bool:
    """Validate a standalone Prometheus fragment and reserve its declared families."""
    if not isinstance(text, str) or not text.endswith("\n") or text.endswith("\n\n"):
        return False
    for line in text.splitlines():
        quoted = escaped = False
        for index, char in enumerate(line):
            if escaped:
                if char not in '\\\\"n':
                    return False
                escaped = False
            elif char == "\\\\" and quoted:
                escaped = True
            elif char == '"':
                quoted = not quoted
            elif char == "\\\\" and not quoted:
                return False
        if escaped or quoted:
            return False
    families = declared if declared is not None else set()
    declarations: dict[str, set[str]] = {}
    lines = text[:-1].split("\n")
    if any(not line for line in lines):
        return False
    for line in lines:
        if line.startswith("# HELP "):
            parts = line.split(" ", 3)
            if len(parts) != 4 or not _NAME.fullmatch(parts[2]) or not parts[3]:
                return False
            name, kind = parts[2], "HELP"
        elif line.startswith("# TYPE "):
            parts = line.split(" ")
            if len(parts) != 4 or not _NAME.fullmatch(parts[2]) or parts[3] not in {"counter", "gauge", "histogram", "summary", "untyped"}:
                return False
            name, kind = parts[2], "TYPE"
        else:
            match = re.fullmatch(r'([a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{(.*)\})? ([^ ]+)', line)
            if not match:
                return False
            name, body, value = match.groups()
            if body is not None:
                pos, labels = 0, set()
                while pos < len(body):
                    match = re.match(r"([a-zA-Z_][a-zA-Z0-9_]*)=\"", body[pos:])
                    if not match or match.group(1) in labels:
                        return False
                    labels.add(match.group(1))
                    pos += match.end()
                    while pos < len(body) and body[pos] != '"':
                        if body[pos] == "\\":
                            pos += 1
                            if pos >= len(body) or body[pos] not in '\\"n':
                                return False
                        elif body[pos] == "\n":
                            return False
                        pos += 1
                    if pos >= len(body):
                        return False
                    pos += 1
                    if pos < len(body):
                        if body[pos] != ",":
                            return False
                        pos += 1
                if not body:
                    return False
            if value in ("+Inf", "-Inf"):
                if (body is None or not name.endswith("_bucket")
                        or not re.search(r'(?:^|,)le="(?:\+Inf|-Inf)"(?:,|$)', body)):
                    return False
            else:
                if not _NUMBER.fullmatch(value):
                    return False
                try:
                    if not math.isfinite(float(value)):
                        return False
                except (ValueError, OverflowError):
                    return False
            continue
        seen = declarations.setdefault(name, set())
        if name in families or kind in seen:
            return False
        seen.add(kind)
    families.update(declarations)
    return True


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
            running = _running_value(row)
            if key is not None and running != 1:
                continue  # a confirmed-stopped row emits only running=0, even with stale fields
            value = running if key is None else _container_value(row, key)
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
        if clock() + PRIME_GAP_S > deadline:  # never sleep a gap that cannot fit the source budget
            return None
        sleep(PRIME_GAP_S)
        doc, age = fetch()
        if not fresh(age):
            if clock() + 1 > deadline:  # same deadline: no extension for the retry
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


def _laya_requests_worker(collector: Any) -> str:
    try:
        return collector()
    except Exception:  # noqa: BLE001 -- Laya request telemetry is isolated from every other source
        return 'llm_env_source_up{source="laya_requests"} 0\n'


def collect_metrics(
    limits: Mapping[str, Any],
    pci: str | None,
    *,
    port: int,
    password: str,
    llama_url: str = "",
    llama_api_key: str = "",
    llama_enabled: bool = True,
    deadline_s: float = 25,
    remote_setup_path: Path | None = None,
) -> str:
    """One exposition snapshot from two bounded workers; a hung source is discarded, the peer is kept."""
    deadline = time.monotonic() + deadline_s
    state_home = FilesystemPath(os.environ.get("XDG_STATE_HOME") or FilesystemPath.home() / ".local/state")
    usage_state_path = state_home / "llm-env/observe/usage-state.json"
    laya_spans_path = state_home / "llm-env/laya-spans/laya-spans.jsonl"
    remote_setup_path = remote_setup_path or FilesystemPath.home() / ".local/share/containers/storage/volumes/llm-env_remote-setup-data/_data/request-counters.json"
    jev_state_path = state_home / "llm-env/observe/jev-state.json"
    jev_log_path = state_home / "ai-kit/jev-answers.jsonl"
    container_q: queue.Queue = queue.Queue(maxsize=1)
    health_q: queue.Queue = queue.Queue(maxsize=1)
    usage_q: queue.Queue = queue.Queue(maxsize=1)
    jev_q: queue.Queue = queue.Queue(maxsize=1)
    llama_q: queue.Queue = queue.Queue(maxsize=1)
    fs_q: queue.Queue = queue.Queue(maxsize=1)
    laya_requests_q: queue.Queue = queue.Queue(maxsize=1)
    remote_setup_q: queue.Queue = queue.Queue(maxsize=1)
    native_http_q: queue.Queue = queue.Queue(maxsize=1)

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
            existing = set(subprocess.run(
                ["podman", "ps", "-a", "--format", "{{.Names}}"],
                capture_output=True, text=True, check=True, timeout=5,
            ).stdout.splitlines())
            inspect = locate

            def locate(container: str) -> tuple[Any, int, float] | None:
                return inspect(container) if container in existing else None

            raw = live_resources(limits, pci, locate=locate, now=time.time())
            live = {svc: row for svc, row in raw.items() if LIVE_CONTAINERS[svc] not in unavailable}
            container_q.put((live, unavailable))
        except Exception:  # noqa: BLE001 -- a failed worker is a down source, never a traceback with config values
            container_q.put((None, True))

    def health_worker() -> None:
        try:
            remaining = max(0.0, deadline - time.monotonic())
            health_q.put(read_omniroute_health(port, password, deadline_s=min(10.0, remaining)))
        except Exception:  # noqa: BLE001 -- a failed worker is a down source, never a traceback
            health_q.put(None)

    def llama_worker() -> None:
        try:
            if not llama_enabled or not llama_url or not llama_api_key:
                llama_q.put('llm_env_source_up{source="llama"} 0\n')
            else:
                llama_q.put(collect_llama(base_url=llama_url, api_key=llama_api_key,
                                          deadline_s=max(0.0, min(3.0, deadline - time.monotonic()))))
        except Exception:  # noqa: BLE001 -- isolated; never expose config or exception text
            llama_q.put('llm_env_source_up{source="llama"} 0\n')

    def usage_worker() -> None:
        try:
            usage_q.put(collect_usage(port, password, path=usage_state_path, laya_path=laya_spans_path,
                                      deadline_s=max(1.0, deadline - time.monotonic() - 2)))
        except Exception:  # noqa: BLE001 -- usage is additive: a failed worker drops only its own families
            usage_q.put("")

    def laya_requests_worker() -> None:
        try:
            request_state_path = state_home / "llm-env/observe/laya-requests-state.json"

            def collect_requests() -> str:
                request_state = load_usage_state(request_state_path) or {}
                fragment = collect_laya_requests(laya_spans_path, request_state, now=datetime.now(UTC))
                save_usage_state(request_state_path, request_state)
                return fragment

            laya_requests_q.put(_laya_requests_worker(collect_requests))
        except Exception:  # noqa: BLE001 -- isolated from all other collectors
            laya_requests_q.put('llm_env_source_up{source="laya_requests"} 0\n')

    def remote_setup_worker() -> None:
        try:
            remote_setup_q.put(collect_remote_setup(remote_setup_path))
        except Exception:  # noqa: BLE001 -- isolated source; never expose file contents or exception text
            remote_setup_q.put('llm_env_source_up{source="remote_setup"} 0\n')

    def native_http_worker() -> None:
        try:
            native_http_q.put(collect_native_http())
        except Exception:  # noqa: BLE001 -- isolated source; never expose response contents
            native_http_q.put('llm_env_source_up{source="grafana_http"} 0\n'
                              'llm_env_source_up{source="victoriametrics_http"} 0\n')

    def fs_worker() -> None:
        try:
            fs_q.put(collect_fs())
        except Exception:  # noqa: BLE001 -- filesystem is isolated from all other sources
            fs_q.put('llm_env_source_up{source="fs"} 0\n')

    def jev_worker() -> None:
        try:
            jev_q.put(collect_jev(log_path=jev_log_path, state_path=jev_state_path,
                                  deadline_s=max(0.1, min(10.0, deadline - time.monotonic() - 2))))
        except Exception:  # noqa: BLE001 -- Jev is additive; failure drops only its own families
            jev_q.put('llm_env_source_up{source="jev_log"} 0\n')

    llama_thread = threading.Thread(target=llama_worker, daemon=True)
    usage_thread = threading.Thread(target=usage_worker, daemon=True)
    jev_thread = threading.Thread(target=jev_worker, daemon=True)
    fs_thread = threading.Thread(target=fs_worker, daemon=True)
    laya_requests_thread = threading.Thread(target=laya_requests_worker, daemon=True)
    remote_setup_thread = threading.Thread(target=remote_setup_worker, daemon=True)
    native_http_thread = threading.Thread(target=native_http_worker, daemon=True)
    llama_thread.start()
    usage_thread.start()
    jev_thread.start()
    fs_thread.start()
    laya_requests_thread.start()
    remote_setup_thread.start()
    native_http_thread.start()
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

    # Give every additive source its turn against one shared deadline; no worker can extend it.
    for worker in (llama_thread, usage_thread, jev_thread, fs_thread, laya_requests_thread, remote_setup_thread, native_http_thread):
        worker.join(max(0.0, deadline - time.monotonic()))
    try:
        remote_setup = remote_setup_q.get_nowait() if not remote_setup_thread.is_alive() else 'llm_env_source_up{source="remote_setup"} 0\n'
    except queue.Empty:
        remote_setup = 'llm_env_source_up{source="remote_setup"} 0\n'
    try:
        llama = llama_q.get_nowait() if not llama_thread.is_alive() else 'llm_env_source_up{source="llama"} 0\n'
    except queue.Empty:
        llama = 'llm_env_source_up{source="llama"} 0\n'
    try:
        usage = usage_q.get_nowait()
    except queue.Empty:
        usage = ""
    try:
        jev = jev_q.get_nowait()
    except queue.Empty:
        jev = 'llm_env_source_up{source="jev_log"} 0\n'
    try:
        fs = fs_q.get_nowait()
    except queue.Empty:
        fs = 'llm_env_source_up{source="fs"} 0\n'
    try:
        laya_requests = laya_requests_q.get_nowait() if not laya_requests_thread.is_alive() else 'llm_env_source_up{source="laya_requests"} 0\n'
    except queue.Empty:
        laya_requests = 'llm_env_source_up{source="laya_requests"} 0\n'
    base = render_metrics(live, health, containers_incomplete=containers_incomplete)
    declared: set[str] = set()
    valid_exposition_fragment(base, declared)
    pieces = [base]
    for source, fragment in (("omniroute_usage", usage), ("jev_log", jev), ("llama", llama)):
        if valid_exposition_fragment(fragment, declared):
            pieces.append(fragment)
        else:
            pieces.append(f'llm_env_source_up{{source="{source}"}} 0\n'
                          f'llm_env_source_up{{source="{source}_invalid"}} 1\n')
    laya_up = ('llm_env_source_up{source="laya_requests"} 1\n'
               if 'llm_env_source_up{source="laya_requests"} 1' in laya_requests
               else 'llm_env_source_up{source="laya_requests"} 0\n')
    laya_fragment = laya_requests.replace(laya_up, "")
    if valid_exposition_fragment(laya_fragment, declared):
        pieces.append(laya_fragment + laya_up)
    else:
        pieces.append('llm_env_source_up{source="laya_requests"} 0\n')
    remote_up = ('llm_env_source_up{source="remote_setup"} 1\n'
                 if 'llm_env_source_up{source="remote_setup"} 1' in remote_setup
                 else 'llm_env_source_up{source="remote_setup"} 0\n')
    remote_fragment = remote_setup.replace(remote_up, "")
    if valid_exposition_fragment(remote_fragment, declared):
        pieces.append(remote_fragment + remote_up)
    else:
        pieces.append('llm_env_source_up{source="remote_setup"} 0\n')
    try:
        native_http = native_http_q.get_nowait() if not native_http_thread.is_alive() else (
            'llm_env_source_up{source="grafana_http"} 0\n'
            'llm_env_source_up{source="victoriametrics_http"} 0\n')
    except queue.Empty:
        native_http = ('llm_env_source_up{source="grafana_http"} 0\n'
                       'llm_env_source_up{source="victoriametrics_http"} 0\n')
    if valid_exposition_fragment(native_http, declared):
        pieces.append(native_http)
    else:
        pieces.append('llm_env_source_up{source="grafana_http"} 0\n'
                      'llm_env_source_up{source="victoriametrics_http"} 0\n')
    fs_up = 'llm_env_source_up{source="fs"} 1\n' if 'llm_env_source_up{source="fs"} 1' in fs else 'llm_env_source_up{source="fs"} 0\n'
    fs_fragment = fs.replace(fs_up, "")
    if valid_exposition_fragment(fs_fragment, declared):
        pieces.append(fs_up + fs_fragment)
    else:
        pieces.append('llm_env_source_up{source="fs"} 0\n')
    return "".join(pieces)


def publish_metrics(text: str) -> None:
    """POST the exposition to the local VictoriaMetrics importer; accept only 2xx, never log the body."""
    conn = http.client.HTTPConnection("127.0.0.1", 8428, timeout=5)
    try:
        conn.request("POST", "/api/v1/import/prometheus", body=text,
                     headers={"Content-Type": "text/plain; version=0.0.4"})
        response = conn.getresponse()
        status = response.status  # status only: never allocate or wait on an arbitrary response body
        if not 200 <= status < 300:
            raise OSError(f"metrics publish returned HTTP {status}")
    finally:
        conn.close()
