"""Disposable, opt-in OmniRoute characterization (LLM_ENV_UPDATE_CHARACTERIZE=1).

A bounded manual-review step: it runs the unmodified OmniRoute image in the isolated proof
backend against a fake inference provider, records sampler series and journal lines as
evidence, and derives a candidate activity profile deterministically. It never edits
pylib/omniroute_activity_profiles.json; a human reviews the evidence and adds the entry in
a separate commit.
"""
from __future__ import annotations

import hashlib
import http.client
import json
import os
import pathlib
import subprocess
import sys
import threading
import time
from typing import Any

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("LLM_ENV_UPDATE_CHARACTERIZE") != "1",
                                reason="opt-in: LLM_ENV_UPDATE_CHARACTERIZE=1 (manual-review step)")

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
import itertools

import yaml
from omniroute_fakes import PASSWORD, free_port
from proof_backend import BASE_IMAGE, ProofBackend, ProofSkip
from test_compose import CFG

import pylib.omniroute as omni
from pylib import remote_setup

OMNIROUTE_IMAGE = "docker.io/diegosouzapw/omniroute:latest"
RESEARCH = REPO / "docs/superpowers/research"
DATE = time.strftime("%Y-%m-%d")


SECRET_KEY_HINTS = ("authorization", "cookie", "token", "apikey", "api_key", "secret", "password",
                    "key")
MAX_STRING = 80


def _shape(doc: Any, prefix: str = "", leaves: dict[str, Any] | None = None) -> dict[str, Any]:
    """Bounded structural summary of a JSON document: scalar leaves keyed by JSON pointer.

    Ruling (measured 2026-10-23): recording raw bodies produced a 190 MB evidence file (the call-logs
    endpoint returns up to 1000 rows every 0.5 s tick), which is not a reviewable artifact. Every
    derivation rule reads only scalar values and list sizes, so the sampler records exactly those:
    a list becomes `<pointer>.length` plus a summary of its first element. Secret-looking scalar keys
    are replaced with `***` so no credential can reach the artifact."""
    out = leaves if leaves is not None else {}
    if isinstance(doc, dict):
        for key, value in doc.items():
            pointer = f"{prefix}/{key}"
            if any(h in str(key).lower() for h in SECRET_KEY_HINTS) and not isinstance(value, (dict, list)):
                out[pointer] = "***"
            else:
                _shape(value, pointer, out)
    elif isinstance(doc, list):
        out[f"{prefix}.length"] = len(doc)
        if doc:
            _shape(doc[0], f"{prefix}/0", out)
    elif isinstance(doc, str):
        out[prefix] = doc[:MAX_STRING]
    else:
        out[prefix] = doc
    return out


def _pointer(body: Any, pointer: str) -> Any:
    if not isinstance(body, dict):
        return None
    return body.get(pointer)


def _provider(payload: dict[str, Any], fake_port: int) -> None:
    """Register the fake llama-cpp provider so the client route is live."""
    payload = dict(payload)
    payload["providerSpecificData"]["baseUrl"] = f"http://host.containers.internal:{fake_port}/v1"
    token = omni._login
    assert callable(omni._login)
    session = token(_base(), PASSWORD)
    omni._request("POST", f"{_base()}/api/providers", session, payload)


def _base() -> str:
    return f"http://127.0.0.1:{_module_state['port']}"


_module_state: dict[str, Any] = {}


class Sampler:
    """Records raw dashboard JSON (structure only) and journal access-log lines."""

    PATHS = ("/api/monitoring/health", "/api/usage/history", "/api/usage/call-logs?limit=1000&offset=0")

    def __init__(self, session: str, container_id: str) -> None:
        self.series: dict[str, list[Any]] = {p: [] for p in self.PATHS}
        self.journal: list[str] = []
        self.session = session
        self.last_tick: float | None = None
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._loop, daemon=True)
        proc = subprocess.Popen(["journalctl", "--output=json", "--no-pager", "--follow",
                                 f"CONTAINER_ID_FULL={container_id}"],
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        self.proc = proc
        self.reader = threading.Thread(target=self._read_journal, daemon=True)

    TICK_SECONDS = 0.5

    def _fetch(self, path: str) -> Any:
        conn = http.client.HTTPConnection("127.0.0.1", _module_state["port"], timeout=10)
        try:
            conn.request("GET", path, headers={"Cookie": f"auth_token={self.session}"})
            body = conn.getresponse().read()
            try:
                return _shape(json.loads(body))
            except ValueError:
                return {"_raw": body[:200].decode("utf-8", "replace")[:MAX_STRING]}
        finally:
            conn.close()

    def _loop(self) -> None:
        while not self.stop.is_set():
            tick = {"t": time.time()}
            self.last_tick = tick["t"]
            for path in self.PATHS:
                try:
                    self.series[path].append({**tick, "body": self._fetch(path)})
                except OSError as exc:
                    self.series[path].append({**tick, "error": repr(exc)})
            self.stop.wait(self.TICK_SECONDS)

    def next_tick_in(self) -> float:
        """Seconds until the next sampler tick (0.5 s cadence), for the boundary-arrival scenario."""
        if self.last_tick is None:
            return self.TICK_SECONDS
        return max(0.0, self.TICK_SECONDS - (time.time() - self.last_tick) % self.TICK_SECONDS)

    @staticmethod
    def _mask(value: Any) -> Any:
        if isinstance(value, dict):
            return {k: ("***" if any(h in str(k).lower() for h in SECRET_KEY_HINTS)
                        and not isinstance(v, (dict, list)) else Sampler._mask(v)) for k, v in value.items()}
        if isinstance(value, list):
            return [Sampler._mask(v) for v in value[:4]]
        if isinstance(value, str):
            return value[:MAX_STRING]
        return value

    def _redact(self, line: str) -> str:
        """Keep the journal record's own JSON shape (so `method`/`path` stay addressable) with
        secret-looking scalar values masked and strings truncated; drop unrelated journal metadata."""
        try:
            record = json.loads(line)
        except ValueError:
            return line[:MAX_STRING]
        message = record.get("MESSAGE")
        out = {k: v for k, v in record.items() if k in ("MESSAGE", "__CURSOR", "PRIORITY", "_PID")}
        if isinstance(message, str) and message.startswith("{"):
            try:
                out["MESSAGE"] = json.dumps(self._mask(json.loads(message)), sort_keys=True)
            except ValueError:
                pass
        return json.dumps(out, sort_keys=True)

    def _read_journal(self) -> None:
        if self.proc.stdout is None:
            return
        while True:
            line = self.proc.stdout.readline()
            if not line:
                break
            # Ruling: journalctl --follow never reaches EOF; a blocking read on the test thread hung
            # the whole run (28 minutes in anon_pipe_read), so this is a daemon reader and drain() is
            # a no-op.
            self.journal.append(self._redact(line.strip()))

    def start(self) -> None:
        self.thread.start()
        self.reader.start()

    def drain(self) -> None:
        """No-op: the reader thread keeps `journal` current.

        Ruling (measured 2026-10-23): `journalctl --follow` never reaches EOF, so reading it on the
        test thread blocks the whole scenario run (the characterization hung 28 minutes in
        `anon_pipe_read`). The daemon reader makes the record asynchronous instead."""
        return

    def close(self) -> None:
        self.stop.set()
        self.thread.join(timeout=5)
        self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        self.reader.join(timeout=5)


def _chat(api_key: str, *, stream: bool = False, timeout: float = 60.0) -> http.client.HTTPResponse:
    conn = http.client.HTTPConnection("127.0.0.1", _module_state["port"], timeout=timeout)
    body = json.dumps({"model": "llama-cpp/fake-model", "stream": stream,
                       "messages": [{"role": "user", "content": "x"}]})
    conn.request("POST", "/v1/chat/completions", body=body,
                 headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"})
    return conn.getresponse()


def _drive(api_key: str, count: int, *, stream: bool, delay: float = 0.0) -> int:
    ok = 0
    for _ in range(count):
        try:
            response = _chat(api_key, stream=stream)
            if stream:
                while response.readline() and not response.readline().startswith(b"data: [DONE]"):
                    pass
            response.read()
            if response.status == 200:
                ok += 1
        except OSError:
            pass
        if delay:
            time.sleep(delay)
    return ok


@pytest.fixture(scope="module")
def characterizer(tmp_path_factory):
    backend = ProofBackend()
    state: dict[str, Any] = {}
    try:
        if subprocess.run(["podman", "image", "exists", OMNIROUTE_IMAGE], check=False).returncode != 0:
            raise ProofSkip(f"host image store lacks {OMNIROUTE_IMAGE}; run: podman pull {OMNIROUTE_IMAGE}")
        backend.import_host_image(OMNIROUTE_IMAGE)
        backend.import_host_image(BASE_IMAGE)
        port = free_port()
        fake_port = free_port()
        cfg = json.loads(json.dumps(CFG))
        cfg["llm_server"] = {"enabled": False}
        cfg["omniroute"] = {"image": OMNIROUTE_IMAGE, "port": port, "initial_password": PASSWORD}
        text = yaml.safe_load(pylib_render(cfg))
        compose = {"services": {"omniroute": text["services"]["omniroute"]},
                   "volumes": {k: v for k, v in text.get("volumes", {}).items() if k == "omniroute-data"}}
        path = backend.root / "docker-compose.yml"
        path.write_text(yaml.safe_dump(compose))
        backend.deploy(path)
        config_id = backend.image_of("omniroute")
        full_id = backend.podman("inspect", "omniroute", "--format", "{{.Id}}").stdout.strip()

        # the container must be able to reach the host-side fake provider
        probe = backend._run(["podman", "run", "--rm", BASE_IMAGE, "python3", "-c",
                              "import socket; socket.gethostbyname('host.containers.internal')"])
        assert probe.returncode == 0, "host.containers.internal does not resolve from the proof backend"

        state_file = backend.root / "fake-inference.json"
        state_file.write_text("{}")
        fake = subprocess.Popen([sys.executable, str(REPO / "tests/fixtures/fake_inference.py"),
                                 "--host", "0.0.0.0", "--port", str(fake_port), "--state", str(state_file)])
        time.sleep(0.5)
        state.update(port=port, fake_port=fake_port, fake=fake, state_file=state_file,
                     config_id=config_id, full_id=full_id, backend=backend)
        _module_state.update(port=port)

        _provider(omni.build_payload(fake_port, "fixture-key"), fake_port)
        api_key = remote_setup.ensure_api_key(_base(), PASSWORD, backend.root / "key.json")
        state["api_key"] = api_key

        # Precondition gate: the client route must be live before anything is recorded.
        # Ruling (measured 2026-10-23, this OmniRoute build): a freshly registered provider's models are
        # not listed in /v1/models until a sync cycle runs, so the listing is only informational; the
        # authoritative liveness proof is the `llama-cpp/fake-model` chat completion returning 200
        # (verified: 200 with the prefix, 400 "Unable to determine provider" without it).
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        conn.request("GET", "/v1/models", headers={"Authorization": f"Bearer {api_key}"})
        models = json.loads(conn.getresponse().read())
        conn.close()
        listed = [str(m.get("id", "")) for m in models.get("data", []) if "llama-cpp/" in str(m.get("id", ""))]
        response = _chat(api_key)
        assert response.status == 200, (
            f"precondition failed: chat completion returned {response.status} "
            f"(listed llama-cpp models: {listed})")
        response.read()
        yield state
    except ProofSkip as exc:
        backend.close()
        pytest.skip(str(exc))
    except BaseException:
        backend.close()
        raise
    backend.close()


def pylib_render(cfg: dict[str, Any]) -> str:
    from pylib.compose import render_compose
    return render_compose(cfg, models_dir="/home/user/llm-workspace/models",
                          presets_path="/home/user/.config/llm-env/presets.ini",
                          repo_root="/home/user/llm-env")


def _run_scenario(characterizer: dict[str, Any], sampler: Sampler, name: str, *, mode: str = "ok",
                  chunks: int = 3, delay: float = 0.05, count: int = 1, stream: bool = False,
                  concurrent: int = 1, idle: float = 5.0,
                  arrival: bool = False) -> dict[str, Any]:
    state_file: pathlib.Path = characterizer["state_file"]
    state_file.write_text(json.dumps({"mode": mode, "chunks": chunks, "delay": delay}))
    if arrival:  # fire within 50 ms after a sampler tick
        time.sleep(max(0.0, sampler.next_tick_in() - 0.02))
        arrival_offset_ms = round((0.0 - sampler.next_tick_in()) * 1000, 1)
    else:
        arrival_offset_ms = None
    started = time.time()
    window_start = started
    journal_before = len(sampler.journal)
    if concurrent > 1:
        results: list[int] = []
        lock = threading.Lock()

        def one() -> None:
            ok = _drive(characterizer["api_key"], count, stream=stream)
            with lock:
                results.append(ok)

        threads = [threading.Thread(target=one) for _ in range(concurrent)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(120)
    else:
        results = [_drive(characterizer["api_key"], count, stream=stream)]
    requests = count * concurrent
    time.sleep(idle)
    window = sampler.journal[journal_before:]
    client_lines = sum(1 for line in window if _is_client_log(line))
    return {"scenario": name, "mode": mode, "requests": requests, "ok": sum(results),
            "duration_s": round(time.time() - started, 3),
            "window_start": round(window_start, 3), "window_end": round(time.time(), 3),
            "sampler_records": sum(len(v) for v in sampler.series.values()),
            "journal_lines": len(window), "client_log_lines": client_lines,
            "arrival_offset_ms": arrival_offset_ms}


def _bodies(series: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sampler ticks that produced a body (fetch failures are recorded with `error` and skipped).

    Ruling: the sampler stores the shape summary keyed by JSON pointer (ruling in `_shape`), so a
    "body" *is* the pointer map; the derivation rules read pointers straight out of it."""
    return [r for r in series if isinstance(r.get("body"), dict)
            and any(str(p).startswith("/") for p in r["body"])]


def _route(method: Any, path: Any) -> tuple[str, str] | None:
    """Normalize a measured access-log pair the way Profile.classify_route does at runtime."""
    if not isinstance(method, str) or not isinstance(path, str):
        return None
    clean = path.split("?", 1)[0].split(" | ", 1)[0]
    return method, clean


CLIENT_PATH = "/v1/chat/completions"


def _is_client_log(line: str) -> bool:
    try:
        message = json.loads(line).get("MESSAGE", "")
        parsed = json.loads(message) if isinstance(message, str) else message
    except (ValueError, TypeError):
        return False
    if not isinstance(parsed, dict):
        return False
    pair = _route(parsed.get("method"), parsed.get("path"))
    return pair is not None and pair == ("POST", CLIENT_PATH)


def _ticks(series: list[dict[str, Any]]) -> list[tuple[float, dict[str, Any]]]:
    return [(r["t"], r["body"]) for r in _bodies(series)]


def _count_value(value: Any) -> Any:
    """Values the runtime `_count` accepts: a non-negative int (bools are not ints there)."""
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _window(ticks: list[tuple[float, dict[str, Any]]], start: float, end: float):
    before = [b for t, b in ticks if t <= start]
    during = [b for t, b in ticks if start < t <= end]
    after = [b for t, b in ticks if t > end]
    return (before[-1] if before else {}), during, (after[0] if after else {})


def _pulse_pointers(ticks, start, end, *, prefix_in=None, prefix_not=(),
                    include_lengths: bool = False) -> list[str]:
    """Pointers that read 0 before the window, >= 1 inside it and 0 again after it.

    The return-to-zero requirement is what excludes cumulative counters: an in-flight signal must be
    able to go quiet, or the idle gate could never open."""
    before, during, after = _window(ticks, start, end)
    if not before or not after or not during:
        return []
    hits = []
    for pointer in sorted(set(before) & set(after)):
        if not include_lengths and pointer.endswith(".length"):
            continue
        if any(pointer.startswith(p) for p in prefix_not):
            continue
        if prefix_in and not pointer.startswith(prefix_in):
            continue
        if _count_value(before[pointer]) != 0 or _count_value(after[pointer]) != 0:
            continue
        if any((_count_value(b.get(pointer)) or 0) >= 1 for b in during):
            hits.append(pointer)
    return hits


def derive_candidate(evidence: dict[str, Any]) -> dict[str, Any]:
    """Deterministic derivation rules over the sampler's shape summaries.

    Only pointers the runtime contract can actually consume are emitted: `_count` needs a
    non-negative int, `_require(..., dict)` needs an object and `_require(..., list)` needs an array.
    Anything a rule cannot derive lands in `incomplete` (and, where a near-miss was measured, in
    `review_notes`) so `load_profile` defers with `capability` instead of guessing."""
    candidate: dict[str, Any] = {}
    incomplete: list[str] = []
    review_notes: dict[str, Any] = {}
    series = evidence["series"]
    health = _ticks(series["/api/monitoring/health"])
    usage = _ticks(series["/api/usage/history"])
    logs = _ticks(series["/api/usage/call-logs?limit=1000&offset=0"])
    windows = {sc["scenario"]: sc for sc in evidence["scenarios"] if "window_start" in sc}

    def pulse(name, ticks, **kwargs):
        sc = windows.get(name)
        if not sc:
            return []
        return _pulse_pointers(ticks, sc["window_start"], sc["window_end"], **kwargs)

    fields: dict[str, Any] = {}
    # pending_by_model first: the /pending/byModel object whose model counters pulse during a long stream
    PENDING_BY_MODEL = "/pending/byModel"
    pending_children = pulse("long_stream", usage, prefix_in=f"{PENDING_BY_MODEL}/")
    parents = [PENDING_BY_MODEL] if pending_children else []
    if len(parents) == 1:
        fields["pending_by_model"] = parents[0]
    else:
        incomplete.append(f"api_fields.pending_by_model (candidates: {sorted(set(pending_children))})")
        review_notes["pending_children"] = pending_children

    # active: an integer in-flight counter in /api/usage/history during a long stream. Pointers already
    # claimed by another field are excluded, so an ambiguous measurement is never guessed at.
    claimed = tuple(sorted({p.rsplit("/", 1)[0] + "/" for p in pending_children} | set(parents)))
    active = pulse("long_stream", usage, prefix_not=claimed)
    if len(active) == 1:
        fields["active"] = active[0]
    else:
        incomplete.append(f"api_fields.active (integer candidates: {active})")
        review_notes["active_list_only"] = [p for p in pulse("long_stream", usage, include_lengths=True,
                                                             prefix_not=claimed) if p.endswith(".length")]

    # admission: integer admission counters in health during the queued scenario
    admission = sorted(set(pulse("queued_request", health, prefix_in="/chatAdmission/")
                           + pulse("queued_request", health, prefix_in="/adaptiveAdmission/")))
    if admission:
        fields["admission"] = admission
    else:
        incomplete.append("api_fields.admission")
    # rows: the call-logs list itself, its row id and its in-memory flag
    root = logs[0][1] if logs else {}
    if any(p == ".length" for p in root):
        rows_pointer = ""  # RFC 6901 root pointer: the response document is the row array itself
    elif any(p == "/rows.length" for p in root):
        rows_pointer = "/rows"
    else:
        rows_pointer = None
    row_keys = {p[len("/rows/0/"): ] if p.startswith("/rows/0/") else p[3:]
                for p in root if p.startswith(("/0/", "/rows/0/"))}
    row_id = next((k for k in sorted(row_keys) if k == "id"), None)
    memory_flag = next((k for k in sorted(row_keys) if k in ("active", "inMemory", "memory")), None)
    if rows_pointer is not None and row_id and memory_flag and any(
            (b.get(".length") or b.get("/rows.length") or 0) >= 1 for _, b in logs):
        fields["rows"] = rows_pointer
        fields["row_id"] = row_id
        fields["row_memory_flag"] = memory_flag
    else:
        incomplete.append("api_fields.rows")
    candidate["api_fields"] = fields

    # health_timestamp: an integer epoch-millisecond counter that advances across ticks
    ms_like = sorted({p for _, b in health for p, v in b.items()
                      if isinstance(v, int) and not isinstance(v, bool) and v > 10 ** 12})
    advancing = []
    for pointer in ms_like:
        values = [b[pointer] for _, b in health if pointer in b]
        if len(values) >= 3 and all(x <= y for x, y in itertools.pairwise(values)) and len(set(values)) >= 2:
            advancing.append(pointer)
    if len(advancing) == 1:
        candidate["health_timestamp"] = advancing[0]
    else:
        candidate["health_timestamp"] = None
        incomplete.append(f"health_timestamp (integer-ms candidates: {advancing})")
        review_notes["health_timestamp_strings"] = sorted(
            {p for p in (health[0][1] if health else {}) if ("timestamp" in p.lower() or "time" in p.lower())
             and isinstance(dict(health[0][1]).get(p), str)})[:8]

    # logger fields and routes from the access-log MESSAGE keys
    messages: list[dict[str, Any]] = []
    for line in evidence["journal"]:
        try:
            message = json.loads(line).get("MESSAGE", "")
            parsed = json.loads(message) if isinstance(message, str) else message
        except (ValueError, TypeError):
            continue
        if isinstance(parsed, dict) and parsed.get("method") and parsed.get("path"):
            messages.append(parsed)
    if messages:
        # `logger.fields` maps the runtime role to the *name of the journal field* holding it
        # (pylib reads `inner[fields["method"]]`), so the rule emits key names, not the measured values.
        client = next((m for m in messages
                       if _route(m.get("method"), m.get("path")) == ("POST", CLIENT_PATH)), messages[0])
        driven = _route(client.get("method"), client.get("path"))
        name_for = {"method": driven[0] if driven else None, "path": CLIENT_PATH}
        fields_map = {}
        for role, value in name_for.items():
            keys = sorted({k for m in messages for k, v in m.items()
                           if v == value or (role == "path" and k == "path"
                                             and _route(m.get("method"), m.get("path")) == driven)})
            fields_map[role] = keys[0] if len(keys) == 1 else None
        if all(fields_map.values()):
            logger = {"fields": fields_map}
        else:
            logger = None
            incomplete.append(f"logger.fields (ambiguous key names: {fields_map})")
        routes = sorted({pair for m in messages if (pair := _route(m.get("method"), m.get("path")))})
        candidate["routes"] = [{"method": a, "path_pattern": b} for a, b in routes]
        candidate["reachable_routes"] = [{"method": a, "path_pattern": b} for a, b in routes]
        candidate["unobserved_routes_unreviewed"] = True
    else:
        logger = None
        incomplete.append("logger.fields")

    # cache dwell and refresh bound from the measured timestamp
    stamp = candidate.get("health_timestamp")
    # Measure the cache behaviour only while the deployment was still serving: the destructive
    # shutdown scenario stalls the health timestamp and would otherwise dominate the bound.
    ends = [sc["window_end"] for sc in evidence["scenarios"] if "window_end" in sc]
    cutoff = max(ends) if ends else None
    if stamp:
        windowed = [pair for pair in health if cutoff is None or pair[0] <= cutoff] or health
        values = [(b.get(stamp), t) for t, b in windowed if stamp in b]
        # dwell = how long the same timestamp is re-served; step = time between the first observation of
        # one distinct value and the first observation of the next (the plan's refresh bound).
        dwells, steps = [], []
        last_value, onset = values[0]
        last_t = onset
        for value, tick in values[1:]:
            if value == last_value:
                dwells.append(tick - last_t)
            else:
                steps.append(tick - onset)
                onset = tick
                last_value = value
            last_t = tick
        # `load_profile` requires both bounds to be positive integers, so a non-positive measurement is
        # reported as missing rather than emitted as something the loader would only defer on.
        cache = int(max(dwells) * 1000) if dwells else 0
        bound = int(-(-((max(steps) - max(dwells)) * 1000) // 100 * 100)) if steps and dwells else 0
        candidate["health_cache_ms"] = cache if cache > 0 else None
        candidate["health_refresh_bound_ms"] = bound if bound > 0 else None
        if candidate["health_cache_ms"] is None or candidate["health_refresh_bound_ms"] is None:
            incomplete.append(f"health_cache_ms/health_refresh_bound_ms (measured: {cache}, {bound})")
    else:
        incomplete.append("health_cache_ms/health_refresh_bound_ms")

    # journald suppression: a burst whose client-route lines match the requests validates the reader
    burst = next((sc for sc in evidence["scenarios"] if sc["scenario"] == "burst"), None)
    if burst is not None and logger is not None:
        lines, requests = burst.get("client_log_lines", 0), burst["requests"]
        if lines >= requests:
            logger["loss_detection"] = {"requests": requests, "lines": lines, "loss_detected": False}
        else:
            # Ruling (per plan): a feed that lost burst lines cannot prove the reader is loss-free, so the
            # logger profile and the routes measured through it are withheld and left for review.
            incomplete.append(f"logger (journald suppressed {requests - lines} of {requests} burst lines)")
            logger = None
    if logger is not None:
        candidate["logger"] = logger
    else:
        for key in ("routes", "reachable_routes", "unobserved_routes_unreviewed"):
            candidate.pop(key, None)

    for name in ("native_shutdown_drain", "boundary_arrival"):
        sc = next((x for x in evidence["scenarios"] if x["scenario"] == name), None)
        if sc:
            candidate[name] = sc
    candidate["review_notes"] = review_notes
    candidate["incomplete"] = sorted(incomplete)
    return candidate


def test_characterize_and_derive_candidate(characterizer, tmp_path):
    backend: ProofBackend = characterizer["backend"]
    session = omni._login(_base(), PASSWORD)
    sampler = Sampler(session, characterizer["full_id"])
    sampler.start()
    scenarios: list[dict[str, Any]] = []
    try:
        scenarios.append(_run_scenario(characterizer, sampler, "long_stream", chunks=20, delay=0.5, stream=True))
        scenarios.append(_run_scenario(characterizer, sampler, "short_between_polls", chunks=1, delay=0.15))
        scenarios.append(_run_scenario(characterizer, sampler, "upstream_failure", mode="fail"))
        scenarios.append(_run_scenario(characterizer, sampler, "queued_request", chunks=3, delay=0.5,
                                       count=8, concurrent=8))
        scenarios.append(_run_scenario(characterizer, sampler, "boundary_arrival", chunks=1, delay=0.1,
                                       arrival=True))
        scenarios.append(_run_scenario(characterizer, sampler, "burst", chunks=1, delay=0.0,
                                       count=200, concurrent=1, idle=5.0))
        # destructive, last: native stop while a long stream is open
        state_file: pathlib.Path = characterizer["state_file"]
        state_file.write_text(json.dumps({"mode": "ok", "chunks": 20, "delay": 0.5}))
        stream_response = _chat(characterizer["api_key"], stream=True)
        stop_started = time.time()
        backend.assert_owned()
        stop = backend._run(["podman", "stop", "--time", "40", "omniroute"])
        drained = b"" != stream_response.readline()
        drain = {"scenario": "native_shutdown_drain", "stop_rc": stop.returncode,
                 "stop_duration_s": round(time.time() - stop_started, 3), "stream_completed": drained}
        scenarios.append(drain)
        time.sleep(5)
    finally:
        sampler.close()

    evidence = {
        "config_id": characterizer["config_id"],
        "versions": {
            "podman": backend._run(["podman", "--version"]).stdout.strip(),
            "podman-compose": backend._run(["podman-compose", "--version"]).stdout.strip(),
            "journalctl": subprocess.run(["journalctl", "--version"], capture_output=True, text=True,
                                         check=False).stdout.splitlines()[0],
        },
        "series": sampler.series,
        "journal": sampler.journal,
        "scenarios": scenarios,
    }
    RESEARCH.mkdir(parents=True, exist_ok=True)
    config12 = characterizer["config_id"].removeprefix("sha256:")[:12]
    ev_path = RESEARCH / f"{DATE}-omniroute-characterization-{config12}.json"
    ev_path.write_text(json.dumps(evidence, indent=2, sort_keys=True))
    ev_path.with_suffix(".json.sha256").write_text(
        hashlib.sha256(ev_path.read_bytes()).hexdigest() + "\n")

    candidate = derive_candidate(evidence)
    cand_path = RESEARCH / f"{DATE}-omniroute-candidate-{config12}.json"
    cand_path.write_text(json.dumps(candidate, indent=2, sort_keys=True))
    print(json.dumps({"evidence": str(ev_path), "candidate": str(cand_path),
                      "incomplete": candidate["incomplete"], "scenarios": scenarios}, indent=2))
    assert len(scenarios) == 7
