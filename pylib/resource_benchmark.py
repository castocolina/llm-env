"""Benchmark-derived container sizing.

Sample a container's cgroup while it idles, runs under a moderate load and cools down,
then size it from what was seen: reservation = the most common (modal) resident set,
or what the container keeps after the load if that is larger, plus headroom; ceiling =
the highest resident set or `memory.peak` seen, plus headroom. Working set is
`memory.current`, the resident set the application's own pressure guard divides by the
cap; swap (median, max and swap-in) is reported alongside, never added. A load phase
whose requests mostly failed (< 90 % ok) is marked saturated: it measures queued sockets,
not the service, so it counts toward the peak but not toward the reservation.
Measurements are inputs to a policy decision, not a gate.
"""
from __future__ import annotations

import json
import math
import os
import statistics
import subprocess
import threading
import time
from collections.abc import Callable
from itertools import pairwise
from pathlib import Path
from typing import Any, Self

MIB = 1024 * 1024
HEADROOM = 0.20
MEM_STEP_MIB = 32
CPU_STEP = 0.25
CGROUP_ROOT = Path("/sys/fs/cgroup")
CPU_WINDOW = 5  # samples averaged for the CPU peak, so one noisy tick is not "the peak"
SATURATED_BELOW = 0.90  # a phase with fewer ok requests than this measures the queue, not the service
PAGE = os.sysconf("SC_PAGE_SIZE")  # memory.stat pswpin counts pages
REPO = Path(__file__).resolve().parents[1]
# Default omniroute profile after the cold start: (phase, requests/s, seconds). "first" is the first
# traffic a fresh process sees; the ramp then climbs until the gateway saturates. The fake upstream
# answers in ~1 s and the gateway serialised ~3 ok requests/s against it (2026-10-24), so the 5+ rps
# steps are expected to come out saturated; they still show the memory a backlog costs.
OMNIROUTE_PHASES = (("first", 1, 30), ("ramp-2rps", 2, 30), ("ramp-5rps", 5, 30), ("ramp-10rps", 10, 30),
                    ("ramp-20rps", 20, 30), ("ramp-40rps", 40, 30), ("cooldown", 0, 60))


def _ceil_to(value: float, step: float) -> float:
    return math.ceil(round(value / step, 9)) * step


def container_pid(container: str) -> int:
    return int(subprocess.run(
        ["podman", "inspect", "-f", "{{.State.Pid}}", container],
        check=True, capture_output=True, text=True,
    ).stdout.strip())


def cgroup_dir(container: str, *, root: Path = CGROUP_ROOT) -> Path:
    pid = container_pid(container)
    line = next(x for x in Path(f"/proc/{pid}/cgroup").read_text().splitlines() if x.startswith("0::"))
    return root / line[3:].lstrip("/")


def gpu_device(*, pci: str | None = None, render: str | None = None,
               drm_root: Path = Path("/sys/class/drm")) -> tuple[str, Path]:
    """(pci address, sysfs mem_info_vram_used) of the GPU named by its PCI address or render node."""
    from pylib.detect import list_gpus
    for gpu in list_gpus(drm_root):
        if gpu["pci_address"] == pci or (render and gpu["render_node"] == render):
            return gpu["pci_address"], drm_root / gpu["card"] / "device" / "mem_info_vram_used"
    raise ValueError(f"no VRAM-backed GPU matches pci={pci!r} render={render!r}")


def _int(path: Path) -> int:
    return int(path.read_text().split()[0])


def read_point(cgroup: Path, gpu: tuple[int, str, Path] | None = None) -> dict[str, Any]:
    """One cgroup sample; `gpu` = (container pid, pci address, sysfs vram_used) adds VRAM."""
    usage = next(
        int(x.split()[1]) for x in (cgroup / "cpu.stat").read_text().splitlines() if x.startswith("usage_usec")
    )
    stat = dict(x.split() for x in (cgroup / "memory.stat").read_text().splitlines())
    point: dict[str, Any] = {
        "t": time.time(),
        "current": _int(cgroup / "memory.current"),
        "swap": _int(cgroup / "memory.swap.current"),
        "peak": _int(cgroup / "memory.peak"),
        "pswpin": int(stat["pswpin"]) if "pswpin" in stat else None,
        "cpu_usec": usage,
    }
    if gpu:
        from pylib.detect import process_vram_mib
        pid, pci, used = gpu
        point["vram_mib"] = process_vram_mib(pid, pci)
        point["device_vram_mib"] = _int(used) // MIB
    return point


class Sampler:
    """Reads each labelled cgroup every `interval` seconds, tagging samples with the current phase."""

    def __init__(self, cgroups: dict[str, Path], interval: float = 1.0,
                 gpus: dict[str, tuple[int, str, Path]] | None = None) -> None:
        self.cgroups, self.interval, self.phase, self.gpus = cgroups, interval, "run", gpus or {}
        self.samples: list[dict[str, Any]] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def _loop(self) -> None:
        while not self._stop.is_set():
            for label, cgroup in self.cgroups.items():
                try:
                    self.samples.append({"label": label, "phase": self.phase,
                                         **read_point(cgroup, self.gpus.get(label))})
                except OSError:  # the container went away; summarize() works with what was seen
                    pass
            self._stop.wait(self.interval)

    def __enter__(self) -> Self:
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        self._thread.join(timeout=5)


def phase_requests(requests: dict[str, dict[str, int]] | None) -> dict[str, dict[str, Any]]:
    """Per-phase ok/error counts and the saturation flag from {phase: {http status: count}}."""
    out = {}
    for phase, counts in (requests or {}).items():
        total = sum(counts.values())
        ok = counts.get("200", 0)
        out[phase] = {"ok": ok, "errors": total - ok, "saturated": total > 0 and ok / total < SATURATED_BELOW}
    return out


def summarize(samples: list[dict[str, Any]], label: str, *, headroom: float = HEADROOM,
              requests: dict[str, dict[str, int]] | None = None, lifetime_peak: bool = True) -> dict[str, Any]:
    """Size one container from its samples.

    `lifetime_peak` trusts the `memory.peak` counter as a whole: true only for a container created by
    the run. For an attached container the counter covers its whole life, so it counts only if it
    rose during the run (then its new value was reached inside the window).
    """
    rows = sorted((s for s in samples if s["label"] == label), key=lambda s: s["t"])
    if len(rows) < 2:
        raise ValueError(f"need at least 2 samples for {label!r}, got {len(rows)}")
    load = phase_requests(requests)
    saturated = {name for name, p in load.items() if p["saturated"]}
    working = [s["current"] / MIB for s in rows]
    basis = [w for w, s in zip(working, rows) if s["phase"] not in saturated] or working
    swaps = [s["swap"] / MIB for s in rows]
    mode = statistics.mode(_ceil_to(w, MEM_STEP_MIB) for w in basis)
    cooldown = [w for w, s in zip(working, rows) if s["phase"] == "cooldown"]
    retained = statistics.median(cooldown) if cooldown else 0.0
    sampled_peak = max(working)
    counter = max(s["peak"] for s in rows) / MIB
    counter_peak = counter if lifetime_peak or counter > rows[0]["peak"] / MIB else 0.0
    peak = max(sampled_peak, counter_peak)
    swapins = [s.get("pswpin") for s in rows]
    swap_in_mib = (swapins[-1] - swapins[0]) * PAGE / MIB if None not in swapins else None  # type: ignore[operator]
    cores = [
        (b["cpu_usec"] - a["cpu_usec"]) / 1e6 / (b["t"] - a["t"])
        for a, b in pairwise(rows) if b["t"] > a["t"]
    ]
    windows = [statistics.fmean(cores[i:i + CPU_WINDOW]) for i in range(max(1, len(cores) - CPU_WINDOW + 1))]
    cpu_mode = statistics.mode(_ceil_to(c, CPU_STEP) for c in cores)
    cpus_min = max(CPU_STEP, _ceil_to(cpu_mode * (1 + headroom), CPU_STEP))
    phases = {}
    for name in dict.fromkeys(s["phase"] for s in rows):
        part = [w for w, s in zip(working, rows) if s["phase"] == name]
        phases[name] = {"samples": len(part), "p50_mib": round(statistics.median(part)), "max_mib": round(max(part)),
                        **load.get(name, {})}
    memory_min = _ceil_to(max(mode, retained) * (1 + headroom), MEM_STEP_MIB)
    memory_max = max(memory_min, _ceil_to(peak * (1 + headroom), MEM_STEP_MIB))
    summary: dict[str, Any] = {
        "label": label,
        "samples": len(rows),
        "headroom": headroom,
        "resident_mode_mib": round(mode),
        "retained_after_load_mib": round(retained),
        "sampled_peak_mib": round(sampled_peak),
        "peak_mib": round(peak),
        "peak_source": "memory.current" if sampled_peak >= counter_peak else "memory.peak",
        "swap_median_mib": round(statistics.median(swaps)),
        "swap_max_mib": round(max(swaps)),
        "swap_in_mib": None if swap_in_mib is None else round(swap_in_mib),
        "swap_in_mib_per_s": None if swap_in_mib is None else round(swap_in_mib / (rows[-1]["t"] - rows[0]["t"]), 2),
        "cpu_mode_cores": cpu_mode,
        "cpu_peak_cores": round(max(windows), 2),
        "phases": phases,
        "recommend": {
            "memory_min_mib": int(memory_min),
            "memory_max_mib": int(memory_max),
            "cpus_min": cpus_min,
            "cpus_max": max(cpus_min, _ceil_to(max(windows) * (1 + headroom), CPU_STEP)),
        },
    }
    process_vram = [s["vram_mib"] for s in rows if s.get("vram_mib") is not None]
    if process_vram:
        summary["vram"] = {
            "process_p50_mib": round(statistics.median(process_vram)),
            "process_peak_mib": max(process_vram),
            "device_peak_mib": max(s["device_vram_mib"] for s in rows if "device_vram_mib" in s),
        }
        summary["recommend"]["vram_reserve_mib"] = int(_ceil_to(max(process_vram) * (1 + headroom), MEM_STEP_MIB))
    return summary


def render_summary(summary: dict[str, Any]) -> str:
    r = summary["recommend"]
    swap_in = ("n/a" if summary.get("swap_in_mib") is None
               else f"{summary['swap_in_mib']} MiB ({summary['swap_in_mib_per_s']} MiB/s)")
    lines = [
        f"== {summary['label']} ({summary['samples']} samples)",
        (f"   resident mode {summary['resident_mode_mib']} MiB, retained after load "
         f"{summary['retained_after_load_mib']} MiB, peak {summary['peak_mib']} MiB "
         f"(from {summary.get('peak_source', 'memory.current')}; sampled {summary.get('sampled_peak_mib')} MiB)"),
        (f"   swap median {summary['swap_median_mib']} MiB, max {summary.get('swap_max_mib')} MiB, "
         f"swapped in {swap_in} (reported, not added)"),
        f"   cpu mode {summary['cpu_mode_cores']} cores, peak {summary['cpu_peak_cores']} cores",
    ]
    if "vram" in summary:
        v = summary["vram"]
        lines.append(f"   vram process p50 {v['process_p50_mib']} MiB, peak {v['process_peak_mib']} MiB; "
                     f"device peak {v['device_peak_mib']} MiB")
    for n, p in summary["phases"].items():
        load = (f"  ok {p['ok']} err {p['errors']}{'  SATURATED (not in the reservation)' if p['saturated'] else ''}"
                if "ok" in p else "")
        lines.append(f"   phase {n:<10} p50 {p['p50_mib']:>5} MiB  max {p['max_mib']:>5} MiB  ({p['samples']} samples){load}")
    extra = f" vram_reserve_mib={r['vram_reserve_mib']}" if "vram_reserve_mib" in r else ""
    lines.append(f"   => memory_min_mib={r['memory_min_mib']} memory_max_mib={r['memory_max_mib']} "
                 f"cpus_min={r['cpus_min']} cpus_max={r['cpus_max']}{extra}  "
                 f"(+{round(summary['headroom'] * 100)}% headroom)")
    return "\n".join(lines)


def _chat(port: int, api_key: str, stream: bool, body: str) -> int:
    import http.client
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    try:
        conn.request("POST", "/v1/chat/completions", body=body,
                     headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"})
        response = conn.getresponse()
        response.read()
        return response.status
    except OSError:
        return 0
    finally:
        conn.close()


def drive(port: int, api_key: str, rps: float, seconds: float, prompt_kb: int,
          *, clock: Callable[[], float] = time.monotonic) -> dict[str, int]:
    """Open `rps` requests per second for `seconds` (half streaming); each runs in its own thread."""
    from concurrent.futures import ThreadPoolExecutor
    counts: dict[str, int] = {}
    lock = threading.Lock()
    body = {"model": "llama-cpp/fake-model", "messages": [{"role": "user", "content": "x" * prompt_kb * 1024}]}

    def one(stream: bool) -> None:
        status = str(_chat(port, api_key, stream, json.dumps({**body, "stream": stream})))
        with lock:
            counts[status] = counts.get(status, 0) + 1

    end = clock() + seconds
    with ThreadPoolExecutor(max_workers=256) as pool:
        n = 0
        while rps > 0 and clock() < end:
            pool.submit(one, n % 2 == 0)
            n += 1
            time.sleep(1 / rps)
        time.sleep(0 if rps > 0 else seconds)
    return counts


def start_omniroute(image: str, port: int, password: str, name: str) -> None:
    """The production service's environment with generous caps (4 CPUs, 4 GiB) so its natural peak shows."""
    from pylib.resources import OMNIROUTE_CHAT_ADMISSION_QUEUE_MS
    subprocess.run(
        ["podman", "run", "-d", "--rm", "--name", name, "-p", f"127.0.0.1:{port}:{port}",
         "--cpus", "4", "--memory", "4096m",
         "-e", f"PORT={port}", "-e", "OMNIROUTE_ALLOW_PRIVATE_PROVIDER_URLS=true",
         "-e", "REQUIRE_API_KEY=true", "-e", f"INITIAL_PASSWORD={password}",
         "-e", f"OMNIROUTE_CHAT_ADMISSION_QUEUE_MS={OMNIROUTE_CHAT_ADMISSION_QUEUE_MS}", image],
        check=True, capture_output=True,
    )


def _save(out: Path, document: dict[str, Any]) -> dict[str, Any]:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(document, indent=1))
    print("\n".join(render_summary(s) for s in document["summary"].values()))
    return {"report": str(out), **{k: v["recommend"] for k, v in document["summary"].items()}}


def _stamped(name: str) -> Path:
    return REPO / "tmp/resource-benchmark" / f"{name}-{time.strftime('%Y%m%dT%H%M%S')}.json"


def run_omniroute(image: str, scale: float, prompt_kb: int, out: Path | None) -> dict[str, Any]:
    """Benchmark a throwaway OmniRoute (same image and environment as production) against a fake upstream."""
    import secrets
    import socket
    import sys
    import tempfile

    from pylib import omniroute as omni
    from pylib import remote_setup

    def free_port() -> int:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            return int(s.getsockname()[1])

    name, port, fake_port, password = f"omniroute-bench-{secrets.token_hex(3)}", free_port(), free_port(), secrets.token_urlsafe(12)
    base = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryDirectory() as tmp:
        state = Path(tmp) / "fake.json"
        state.write_text(json.dumps({"chunks": 10, "delay": 0.1}))
        fake = subprocess.Popen([sys.executable, str(REPO / "pylib/fake_inference.py"), "--host", "0.0.0.0",
                                 "--port", str(fake_port), "--state", str(state)])
        try:
            start_omniroute(image, port, password, name)
            requests: dict[str, dict[str, int]] = {}
            with Sampler({"omniroute": cgroup_dir(name)}) as sampler:
                sampler.phase = "boot"  # cold start: from container creation until the dashboard answers
                deadline = time.monotonic() + 120
                while True:
                    try:
                        omni._login(base, password)
                        break
                    except (omni.OmniRouteError, OSError):
                        if time.monotonic() > deadline:
                            raise
                        time.sleep(1)
                payload = omni.build_payload(fake_port, "fixture-key")
                payload["providerSpecificData"]["baseUrl"] = f"http://host.containers.internal:{fake_port}/v1"
                omni._request("POST", f"{base}/api/providers", omni._login(base, password), payload)
                api_key = remote_setup.ensure_api_key(base, password, Path(tmp) / "key.json")
                for phase, rps, seconds in OMNIROUTE_PHASES:
                    sampler.phase = phase
                    requests[phase] = drive(port, api_key, rps, seconds * scale, prompt_kb)
            summary = summarize(sampler.samples, "omniroute", requests=requests)
        finally:
            # rm -f also removes a container that was created but never started (e.g. a port race).
            subprocess.run(["podman", "rm", "-f", "-t", "5", name], capture_output=True, check=False)
            fake.terminate()
            try:
                fake.wait(timeout=5)
            except subprocess.TimeoutExpired:
                fake.kill()
                fake.wait()
    return _save(out or _stamped("omniroute"), {
        "schema": 1, "image": image, "prompt_kb": prompt_kb, "requests": requests,
        "samples": sampler.samples, "summary": {"omniroute": summary},
    })


def run_attach(containers: list[str], command: str | None, seconds: float, cooldown: float, out: Path | None,
               gpu: tuple[str, Path] | None = None) -> dict[str, Any]:
    """Sample running containers while an external load (`command`, or just `seconds`) runs, then cool down.

    `gpu` = (pci address, sysfs vram_used) also samples each container's process VRAM and the device total.
    """
    gpus = {c: (container_pid(c), *gpu) for c in containers} if gpu else None
    with Sampler({c: cgroup_dir(c) for c in containers}, gpus=gpus) as sampler:
        sampler.phase = "idle"
        time.sleep(15)
        sampler.phase = "run"
        if command:
            subprocess.run(command, shell=True, check=False)
        else:
            time.sleep(seconds)
        sampler.phase = "cooldown"
        time.sleep(cooldown)
    return _save(out or _stamped("attach"), {
        "schema": 1, "samples": sampler.samples,
        "summary": {c: summarize(sampler.samples, c, lifetime_peak=False) for c in containers},
    })


def report(path: Path, headroom: float) -> dict[str, Any]:
    document = json.loads(path.read_text())
    labels = list(dict.fromkeys(s["label"] for s in document["samples"]))
    # Only `omniroute` runs carry an image: a container the run created, so its memory.peak is the run's.
    document["summary"] = {c: summarize(document["samples"], c, headroom=headroom, requests=document.get("requests"),
                                        lifetime_peak="image" in document) for c in labels}
    print("\n".join(render_summary(s) for s in document["summary"].values()))
    return {k: v["recommend"] for k, v in document["summary"].items()}
