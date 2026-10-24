"""Characterize an OmniRoute image: run it unmodified in a disposable, isolated rootless Podman backend against a fake
inference provider, record sampler series and journal lines as evidence, and derive its activity profile.

`make update-omniroute` calls this (`omniroute-update characterize`) when the running image has no profile and keeps
a complete result in the user-local profile store; the opt-in `tests/test_omniroute_characterize.py` runs the same
harness and writes reviewable evidence under docs/. The backend never touches the live stack: every Podman call runs
against its own graph/run roots under the repo's `tmp/` and is ownership-checked first.
"""
from __future__ import annotations

import hashlib
import http.client
import itertools
import json
import math
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

from pylib import omniroute as omni
from pylib import remote_setup
from pylib.compose import render_compose
from pylib.omniroute_activity import PRIME_GAP_S, epoch_ms, load_profile
from pylib.omniroute_observer import lease_alive
from pylib.omniroute_update import Deferral, write_private

REPO = Path(__file__).resolve().parents[1]
FAKE_INFERENCE = Path(__file__).with_name("fake_inference.py")
BUDGET_S = 1200  # measured 2026-10-24: 5-8 min of scenarios plus image import and deploy; the CLI enforces it
TARGET_REF = "localhost/llm-env-characterize/omniroute:target"  # the image's tag inside the isolated store only


class BackendUnavailable(Exception):
    """A host prerequisite is missing; the message says what to fix."""


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start_tick(pid: int) -> str:
    return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]


class IsolatedBackend:
    """Disposable rootless Podman store (vfs graph, own run root, slirp4netns) under `parent`. Every call is
    ownership-checked; `close()` removes its containers and the whole root. A `lease` file (pid + start tick) lets a
    later run reap a root whose owner was SIGKILLed (`sweep`)."""

    project = "llmenv-characterize"
    prefix = "characterize-"

    def __init__(self, parent: Path = REPO / "tmp", root: Path | None = None) -> None:
        parent.mkdir(parents=True, exist_ok=True)
        if root is not None:  # attach to an existing root (sweep)
            self.root = root
            self._health_stop: threading.Event | None = None
            self._health_thread: threading.Thread | None = None
            return
        self.root = Path(tempfile.mkdtemp(prefix=self.prefix, dir=parent)).resolve()  # 0700
        (self.root / "lease").write_text(f"{os.getpid()} {_start_tick(os.getpid())}\n")
        for name in ("graph", "run", "netconf"):
            (self.root / name).mkdir()
        (self.root / "storage.conf").write_text(
            f'[storage]\ndriver = "vfs"\ngraphroot = "{self.root}/graph"\nrunroot = "{self.root}/run"\n')
        # Ruling: pasta cannot open its rootless-netns PID file under the repo's ./tmp mount
        # ("Permission denied"), so the backend pins slirp4netns for rootless networking.
        (self.root / "containers.conf").write_text(
            f'[containers]\nlog_driver = "journald"\n'
            f'[network]\nnetwork_config_dir = "{self.root}/netconf"\ndefault_rootless_network_cmd = "slirp4netns"\n')
        self._health_stop = None
        self._health_thread = None

    @classmethod
    def sweep(cls, parent: Path = REPO / "tmp") -> None:
        """Reap roots whose owner died without cleaning up (SIGKILL): their containers keep running otherwise."""
        for root in sorted(parent.glob(f"{cls.prefix}*")):
            if root.is_symlink() or not root.is_dir() or not (root / "storage.conf").is_file():
                continue  # only a backend root (never another file that happens to share the prefix)
            try:
                pid, tick = (root / "lease").read_text().split()
                if lease_alive(int(pid), tick):
                    continue
            except (OSError, ValueError):
                pass  # no readable lease: a root interrupted while it was being created
            cls(parent, root=root).close()

    # --- environment and guards -------------------------------------------------
    def env(self) -> dict[str, str]:
        return {"CONTAINERS_STORAGE_CONF": str(self.root / "storage.conf"),
                "CONTAINERS_CONF": str(self.root / "containers.conf")}

    def _run(self, argv, check: bool = True, **kw) -> subprocess.CompletedProcess:
        return subprocess.run(argv, env={**os.environ, **self.env()}, text=True, capture_output=True, check=check, **kw)

    def assert_owned(self) -> None:
        info = json.loads(self._run(["podman", "info", "--format", "json"], check=True).stdout)
        for key in ("graphRoot", "runRoot"):
            if not os.path.realpath(info["store"][key]).startswith(str(self.root) + os.sep):
                raise RuntimeError("refusing: the Podman store is not the isolated backend")

    def podman(self, *args: str, check: bool = True, **kw) -> subprocess.CompletedProcess:
        self.assert_owned()
        return self._run(["podman", *args], check=check, **kw)

    def import_host_image(self, ref: str) -> None:
        if subprocess.run(["podman", "image", "exists", ref], check=False).returncode != 0:
            raise BackendUnavailable(f"host image store lacks {ref}; run: podman pull {ref}")
        self.assert_owned()
        save = subprocess.Popen(["podman", "save", ref], stdout=subprocess.PIPE)  # host store: no isolated env
        subprocess.run(["podman", "load"], stdin=save.stdout, env={**os.environ, **self.env()},
                       check=True, capture_output=True)
        if save.wait() != 0:
            raise BackendUnavailable(f"podman save {ref} failed")

    def deploy(self, compose_path: Path) -> None:
        self.assert_owned()
        # The loop must run *during* `up -d`: a `depends_on: service_healthy` edge makes podman-compose
        # wait for the parent to report healthy, which only happens while the loop drives the checks.
        self.start_health_loop()
        subprocess.run([shutil.which("podman-compose") or "podman-compose", "--in-pod", "false", "--env-file", "/dev/null",
                        "-p", self.project, "-f", str(compose_path), "up", "-d"],
                       env={**os.environ, **self.env()}, check=True, capture_output=True, text=True, timeout=300)
        for name in self.project_containers():
            self.wait_healthy(name)

    def start_health_loop(self, interval: float = 2.0) -> None:
        """Drive the healthchecks explicitly. Ruling: conmon sanitizes its environment, so with an isolated
        CONTAINERS_STORAGE_CONF its automatic timer execs `podman` against the default store and can never find the
        isolated containers (journal: "no container with name or ID ... found"). `podman healthcheck run` under the
        isolated env performs the same CMD on the same cadence the timer would."""
        if self._health_stop is not None:
            return
        stop = threading.Event()
        self._health_stop = stop

        def loop() -> None:
            while not stop.wait(interval):
                # The containers churn (created/removed between tests) and `podman healthcheck run`
                # exits 125 while one is mid-creation; a transient failure must not kill the loop,
                # or a `depends_on: service_healthy` deploy would wait forever for a healthy parent.
                try:
                    names = self.project_containers()
                except (OSError, RuntimeError, subprocess.SubprocessError):  # transient store churn; the next tick retries
                    continue
                for name in names:
                    try:
                        self._run(["podman", "healthcheck", "run", name], check=False)
                    except (subprocess.SubprocessError, OSError):  # transient; the next tick retries
                        pass

        self._health_thread = threading.Thread(target=loop, daemon=True)
        self._health_thread.start()

    def stop_health_loop(self) -> None:
        stop, thread = self._health_stop, self._health_thread
        if stop is not None and thread is not None:
            stop.set()
            thread.join(timeout=30)
        self._health_stop = None
        self._health_thread = None

    def project_containers(self) -> list[str]:
        out = self.podman("ps", "--all", "--filter", f"label=io.podman.compose.project={self.project}",
                          "--format", "{{.Names}}").stdout
        return sorted(out.split())

    def wait_healthy(self, name: str, timeout: int = 90) -> None:
        # Ruling: automatic conmon healthcheck timers do not fire in this disposable backend
        # (State.Health stays "starting" with an empty log), so the harness runs the check itself.
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if self.podman("inspect", name, "--format", "{{.State.Health.Status}}").stdout.strip() == "healthy":
                return
            self.podman("healthcheck", "run", name, check=False)
            time.sleep(1)
        raise RuntimeError(f"{name} did not become healthy")

    def image_of(self, name: str) -> str:
        return "sha256:" + self.podman("inspect", name, "--format", "{{.Image}}").stdout.strip()

    def close(self) -> None:
        self.stop_health_loop()
        try:
            self.assert_owned()
            self._run(["podman", "rm", "--force", "--all", "--time", "1"])
            self._run(["podman", "unshare", "rm", "-rf", str(self.root)])
        except (OSError, RuntimeError, ValueError, KeyError, subprocess.SubprocessError):
            pass  # not (or no longer) an isolated store: never run `rm --all` against another one
        finally:
            shutil.rmtree(self.root, ignore_errors=True)
            if self.root.exists():
                # Image content extracted by a previous run can belong to the subuid range, which plain
                # rmtree cannot unlink. Retry through podman's own uid-mapped namespace, without the
                # (possibly already deleted) isolated storage conf.
                env = {k: v for k, v in os.environ.items() if k != "CONTAINERS_STORAGE_CONF"}
                subprocess.run(["podman", "unshare", "rm", "-rf", str(self.root)], env=env, check=False,
                               capture_output=True, text=True, timeout=120)
                shutil.rmtree(self.root, ignore_errors=True)


def _base(port: int) -> str:
    return f"http://127.0.0.1:{port}"


def _provider(payload: dict[str, Any], port: int, fake_port: int, password: str) -> None:
    """Register the fake llama-cpp provider so the client route is live."""
    payload = dict(payload)
    payload["providerSpecificData"]["baseUrl"] = f"http://host.containers.internal:{fake_port}/v1"
    session = omni._login(_base(port), password)
    omni._request("POST", f"{_base(port)}/api/providers", session, payload)


def _compose(image_ref: str, port: int, password: str) -> dict[str, Any]:
    """The production omniroute service (render_compose) for this image, published on loopback only, no limits."""
    cfg = {"server": {"host": "127.0.0.1", "port": 1, "api_key": "unused", "sleep_idle_seconds": 1},
           "gpu": {"image": "unused"}, "runtime": {"models_max": 1}, "llm_server": {"enabled": False},
           "omniroute": {"image": image_ref, "port": port, "initial_password": password}}
    limits: dict[str, dict[str, Any]] = {k: {} for k in ("llm_server", "omniroute", "remote_setup", "system_one")}
    doc = yaml.safe_load(render_compose(cfg, models_dir="/nonexistent", presets_path="/nonexistent",
                                        repo_root="/nonexistent", limits=limits))
    service = doc["services"]["omniroute"]
    service["ports"] = [f"127.0.0.1:{port}:{port}"]
    return {"services": {"omniroute": service}, "volumes": {"omniroute-data": {}}}


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
    if isinstance(doc, dict) and not doc and prefix:
        out[prefix] = {}  # an empty object is a shape too (`/pending/byModel` when idle): the replay test needs it
    elif isinstance(doc, dict):
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


# The call-log page the sampler reads: the newest persisted rows plus every in-memory row (source-verified). Its
# limit is the profile's `rows_page_limit`; the derivation reads it back from this series key.
ROWS_PAGE_LIMIT = 10
LOGS_PATH = f"/api/usage/call-logs?limit={ROWS_PAGE_LIMIT}&offset=0"


class Sampler:
    """Records raw dashboard JSON (structure only) and journal access-log lines."""

    PATHS = ("/api/monitoring/health", "/api/usage/history", LOGS_PATH)

    def __init__(self, session: str, container_id: str, port: int) -> None:
        self.port = port
        self.series: dict[str, list[Any]] = {p: [] for p in self.PATHS}
        self.row_table: dict[str, dict[str, Any]] = {}
        self.journal: list[str] = []
        self.session = session
        self.last_tick: float | None = None
        self.stop = threading.Event()
        self.lock = threading.Lock()  # held by a scenario that must be the only API client (idle_poll)
        self.thread = threading.Thread(target=self._loop, daemon=True)
        proc = subprocess.Popen(["journalctl", "--output=json", "--no-pager", "--follow",
                                 f"CONTAINER_ID_FULL={container_id}"],
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        self.proc = proc
        self.reader = threading.Thread(target=self._read_journal, daemon=True)

    TICK_SECONDS = 0.5

    def _fetch(self, path: str) -> Any:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.request("GET", path, headers={"Cookie": f"auth_token={self.session}"})
            body = conn.getresponse().read()
            try:
                doc = json.loads(body)
                return self._page(doc) if path == LOGS_PATH else _shape(doc)
            except ValueError:
                return {"_raw": body[:200].decode("utf-8", "replace")[:MAX_STRING]}
        finally:
            conn.close()

    def _loop(self) -> None:
        while not self.stop.is_set():
            with self.lock:
                tick = {"t": time.time()}
                self.last_tick = tick["t"]
                for path in self.PATHS:
                    try:
                        self.series[path].append({**tick, "body": self._fetch(path)})
                    except OSError as exc:
                        self.series[path].append({**tick, "error": repr(exc)})
            self.stop.wait(self.TICK_SECONDS)

    def _page(self, doc: Any) -> dict[str, Any]:
        """A call-log page: its usual shape plus `#rows`, every row as a digest into `row_table`.

        Ruling (measured 2026-10-24): recording every row of every tick made an 80 MB file (in-memory rows pile up to
        ~190 per page under the burst), but a row repeats across many ticks, so each distinct row is stored once."""
        out = _shape(doc)
        rows = doc if isinstance(doc, list) else doc.get("rows") if isinstance(doc, dict) else None
        digests = []
        for row in rows if isinstance(rows, list) else []:
            shape = _shape(row)
            digest = hashlib.sha256(json.dumps(shape, sort_keys=True).encode()).hexdigest()[:16]
            self.row_table.setdefault(digest, shape)
            digests.append(digest)
        out["#rows"] = digests
        return out

    def settle(self, after: float, cap: float = 75.0) -> bool:
        """Wait until the sampler has recorded a health snapshot computed after `after` (epoch s), at most `cap` s.
        Ruling (2026-10-24): 7d2169fb2bd6 caches the health payload 30 s (source-verified), so a fixed idle tail
        ended scenarios while health still showed the finished load and no admission counter returned to zero."""
        end = time.time() + cap
        while time.time() < end:
            for tick in self.series["/api/monitoring/health"][-3:]:
                ms = _epoch_ms_or_none((tick.get("body") or {}).get("/timestamp"))
                if ms is not None and ms > after * 1000:
                    return True
            time.sleep(self.TICK_SECONDS)
        return False

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



def _chat(port: int, api_key: str, *, stream: bool = False, timeout: float = 60.0) -> http.client.HTTPResponse:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    body = json.dumps({"model": "llama-cpp/fake-model", "stream": stream,
                       "messages": [{"role": "user", "content": "x"}]})
    conn.request("POST", "/v1/chat/completions", body=body,
                 headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"})
    return conn.getresponse()


def _drive(port: int, api_key: str, count: int, *, stream: bool, delay: float = 0.0) -> int:
    ok = 0
    for _ in range(count):
        try:
            response = _chat(port, api_key, stream=stream)
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


def _stop(proc: subprocess.Popen[bytes] | None) -> None:
    if proc is not None:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def _run_scenario(characterizer: dict[str, Any], sampler: Sampler, name: str, *, mode: str = "ok",
                  chunks: int = 3, delay: float = 0.05, count: int = 1, stream: bool = False,
                  concurrent: int = 1, idle: float = 5.0,
                  arrival: bool = False) -> dict[str, Any]:
    state_file: Path = characterizer["state_file"]
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
            ok = _drive(characterizer["port"], characterizer["api_key"], count, stream=stream)
            with lock:
                results.append(ok)

        threads = [threading.Thread(target=one) for _ in range(concurrent)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(120)
    else:
        results = [_drive(characterizer["port"], characterizer["api_key"], count, stream=stream)]
    requests = count * concurrent
    load_end = time.time()
    time.sleep(idle)
    settled = sampler.settle(load_end)
    window = sampler.journal[journal_before:]
    client_lines = sum(1 for line in window if _is_client_log(line))
    return {"scenario": name, "mode": mode, "requests": requests, "ok": sum(results),
            "duration_s": round(time.time() - started, 3),
            "window_start": round(window_start, 3), "window_end": round(time.time(), 3),
            "sampler_records": sum(len(v) for v in sampler.series.values()),
            "journal_lines": len(window), "client_log_lines": client_lines,
            "arrival_offset_ms": arrival_offset_ms, "health_settled": settled}


def _provider_test_scenario(characterizer: dict[str, Any], sampler: Sampler, *, count: int = 3,
                            idle: float = 5.0) -> dict[str, Any]:
    """Internal connection tests with no client request: OmniRoute writes each one as a call-log row of its own
    (source-verified in both images: `POST /api/providers/test`, model `connection-test`). The rows that appear only
    here are what the derivation classifies as internal probe rows."""
    session = omni._login(_base(characterizer["port"]), characterizer["password"])
    connection = omni.find_connection(omni._extract_providers(omni._request("GET", f"{_base(characterizer['port'])}/api/providers", session)))
    assert connection is not None, "the fake provider connection is not registered"
    journal_before = len(sampler.journal)
    started = time.time()
    results = []
    for _ in range(count):
        try:
            omni._request("POST", f"{_base(characterizer['port'])}/api/providers/{connection['id']}/test", session, {})
            results.append("ok")
        except omni.OmniRouteError as exc:
            results.append(str(exc).rsplit(" failed: ", 1)[-1][:MAX_STRING])
        time.sleep(1.0)
    time.sleep(idle)
    window = sampler.journal[journal_before:]
    return {"scenario": "provider_test", "requests": count, "ok": results.count("ok"), "results": results,
            "window_start": round(started, 3), "window_end": round(time.time(), 3),
            "journal_lines": len(window), "client_log_lines": sum(1 for line in window if _is_client_log(line))}


# The other client routes agents call (review item 6, 2026-10-24). In both images the shared chat handler is the only
# access-log call site and serves /v1/chat/completions, /v1/completions, /v1/messages(/count_tokens), /v1/responses
# (source-verified); /v1/embeddings and /v1/models have handlers of their own.
CLIENT_ROUTES = (
    ("POST", "/v1/messages", {"model": "llama-cpp/fake-model", "max_tokens": 16,
                              "messages": [{"role": "user", "content": "x"}]}),
    ("POST", "/v1/responses", {"model": "llama-cpp/fake-model", "input": "x"}),
    ("POST", "/v1/embeddings", {"model": "llama-cpp/fake-model", "input": "x"}),
    ("GET", "/v1/models", None),
)


def _client_routes_scenario(characterizer: dict[str, Any], sampler: Sampler, *, idle: float = 3.0) -> dict[str, Any]:
    """One request per extra client route, each followed by an idle gap, so the derivation can classify every route
    from what it leaves behind: its own access-log line (the arrival signal) and/or call-log rows."""
    characterizer["state_file"].write_text(json.dumps({"mode": "ok", "chunks": 1, "delay": 0.05}))
    started, routes = time.time(), []
    for method, path, body in CLIENT_ROUTES:
        before = len(sampler.journal)
        conn = http.client.HTTPConnection("127.0.0.1", characterizer["port"], timeout=60)
        try:
            conn.request(method, path, body=json.dumps(body) if body is not None else None,
                         headers={"Authorization": f"Bearer {characterizer['api_key']}",
                                  "Content-Type": "application/json"})
            response = conn.getresponse()
            response.read()
            status = response.status
        except OSError:
            status = 0
        finally:
            conn.close()
        time.sleep(idle)
        routes.append({"method": method, "path": path, "status": status, "ok": 200 <= status < 300,
                       "access_lines": sum(1 for line in sampler.journal[before:] if _access_pair(line) == (method, path))})
    return {"scenario": "client_routes", "routes": routes,
            "window_start": round(started, 3), "window_end": round(time.time(), 3)}


def _idle_poll_scenario(sampler: Sampler, *, polls: int = 6, interval: float = 5.0) -> dict[str, Any]:
    """The observer's idle regime: health polled every `interval` s with no traffic and no other API client (the
    sampler is held). `/api/monitoring/health` is stale-while-revalidate (measured live 2026-10-24), so each poll
    records a lone read (`lone`, primes the recompute) and the read the runtime uses `prime_gap_s` later (`read`),
    with their receipt times; the derivation checks the primed ages against the derived bounds."""
    out: list[dict[str, Any]] = []
    with sampler.lock:
        start = time.time()
        for _ in range(polls):
            time.sleep(interval)
            lone = sampler._fetch("/api/monitoring/health")
            t_lone = time.time()
            time.sleep(PRIME_GAP_S)
            read = sampler._fetch("/api/monitoring/health")
            out.append({"t_lone": t_lone, "lone": lone, "t_read": time.time(), "read": read})
    return {"scenario": "idle_poll", "interval_s": interval, "prime_gap_s": PRIME_GAP_S, "start": start,
            "end": time.time(), "polls": out}



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


def _rows_of(body: dict[str, Any], table: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """The recorded rows of one call-log page, in page order, rebuilt from the row table (top-level keys only)."""
    return [{k[1:]: v for k, v in table[d].items() if k.count("/") == 1} for d in body.get("#rows", [])]


def _row_order(pages, stamp: str, memory_flag: str) -> dict[str, int]:
    """Review note: pages whose persisted rows (neither in flight nor `detailState: in-memory`, source-verified to
    follow every in-memory row) are not newest-first. The runtime reads the last row as the oldest persisted one."""
    def persisted(page):
        return [epoch_ms(r[stamp]) for r in page if not r.get(memory_flag) and r.get("detailState") != "in-memory"]
    late = sum(1 for _, page in pages if any(a < b for a, b in itertools.pairwise(persisted(page))))
    return {"pages": len(pages), "persisted_not_newest_first": late}


def _access_pair(line: str) -> tuple[str, str] | None:
    try:
        message = json.loads(line).get("MESSAGE", "")
        parsed = json.loads(message) if isinstance(message, str) else message
    except (ValueError, TypeError, AttributeError):
        return None
    return _route(parsed.get("method"), parsed.get("path")) if isinstance(parsed, dict) else None


def _ticks(series: list[dict[str, Any]]) -> list[tuple[float, dict[str, Any]]]:
    return [(r["t"], r["body"]) for r in _bodies(series)]


def _count_value(value: Any) -> Any:
    """Values the runtime `_count` accepts: a non-negative int (bools are not ints there)."""
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _window(ticks: list[tuple[float, dict[str, Any]]], start: float, end: float):
    """(baseline, during, return-to-idle). A scenario window closes after its own idle tail, so its last tick is
    the return-to-idle observation. Ruling (measured 2026-10-23): the first tick after the window already belongs
    to the next scenario (boundary_arrival fired 0.1 s after queued_request ended and that tick saw it)."""
    before = [b for t, b in ticks if t <= start]
    inside = [b for t, b in ticks if start < t <= end]
    return (before[-1] if before else {}), inside[:-1], (inside[-1] if inside else {})


def _pulse_pointers(ticks, start, end, *, prefix_in=None, prefix_not=(),
                    include_lengths: bool = False, absent_is_zero: bool = False) -> list[str]:
    """Pointers that read 0 before the window, >= 1 inside it and 0 again after it.

    The return-to-zero requirement is what excludes cumulative counters: an in-flight signal must be
    able to go quiet, or the idle gate could never open. A pointer that ever measured a value the runtime
    `_count` rejects (a fraction such as `/adaptiveAdmission/utilization`) is excluded: under load it would
    turn a busy sample into a schema error. `absent_is_zero` is for children of a map whose zero entries are
    deleted (measured 2026-10-23: `/pending/byModel/<model>` disappears when its count drops to zero)."""
    before, during, after = _window(ticks, start, end)
    if not before or not after or not during:
        return []

    def value(body, pointer):
        if pointer not in body:
            return 0 if absent_is_zero else None
        return _count_value(body[pointer])

    pool = set(before) | set(after) | {p for b in during for p in b} if absent_is_zero else set(before) & set(after)
    hits = []
    for pointer in sorted(pool):
        if not include_lengths and pointer.endswith(".length"):
            continue
        if any(pointer.startswith(p) for p in prefix_not):
            continue
        if prefix_in and not pointer.startswith(prefix_in):
            continue
        if any(pointer in b and _count_value(b[pointer]) is None for _, b in ticks):
            continue
        if value(before, pointer) != 0 or value(after, pointer) != 0:
            continue
        if any((value(b, pointer) or 0) >= 1 for b in during):
            hits.append(pointer)
    return hits


def _epoch_ms_or_none(value: Any) -> int | None:
    """The runtime health-timestamp reading (`pylib.omniroute_activity.epoch_ms`), None when it would reject."""
    try:
        return epoch_ms(value)
    except ValueError:
        return None


def _refresh_steps(values: list[tuple[int, float]]) -> tuple[list[float], list[float]]:
    """(dwells, steps): how long one value is re-served, and the time between first observations of successive
    distinct values (the plan's refresh bound)."""
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
    return dwells, steps


# Signals the promotion step adds to every profile (facts of the OmniRoute build, not of one measurement):
# the logger's level variable (source-verified with `grep -r process.env.APP_LOG_LEVEL /app` in the 038b2aba95a6
# image) and the minimum level the access-log lines need (they are emitted at pino level 30 = info).
LOGGER_LEVEL_ENV, LOGGER_MIN_LEVEL = "APP_LOG_LEVEL", "info"
# Scenarios that carry in-flight work. Ruling (measured 2026-10-23): the sampler stamps a tick before its fetches,
# so the long_stream baseline tick already saw the request; queued_request has a clean idle baseline.
INFLIGHT_SCENARIOS = ("long_stream", "queued_request")


def promote_candidate(candidate: dict[str, Any], *, config_id: str, evidence_name: str, evidence_sha256: str,
                      entrypoint: list[str]) -> dict[str, Any]:
    """Turn a complete candidate into a `pylib/omniroute_activity_profiles.json` entry, deterministically.

    Refuses (ValueError) when any rule left a field incomplete, or when an observed route is not the driven
    client route: only that route's class and signals were measured, and an unprofiled reachable route would
    make the loader defer `coverage` anyway."""
    if candidate.get("incomplete"):
        raise ValueError(f"candidate is incomplete: {candidate['incomplete']}")
    routes = [(r["method"], r["path_pattern"]) for r in candidate["routes"]]
    inference = [("POST", CLIENT_PATH), *((r["method"], r["path_pattern"]) for r in candidate.get("client_routes", []))]
    if ("POST", CLIENT_PATH) not in routes or set(routes) - set(inference):
        raise ValueError(f"observed routes without a measured class: {sorted(set(routes) - set(inference))}")
    fields = dict(candidate["api_fields"], health_timestamp=candidate["health_timestamp"])
    signals = {"arrival": "journal-access-log", "inflight": f"usage-history{fields['active']}",
               "queue": "health-admission+usage-pending-by-model"}
    inference_routes = [{"method": m, "path_pattern": p, "class": "inference",
                         **{k: {"signal": v, "evidence": evidence_sha256} for k, v in signals.items()}}
                        for m, p in inference]
    probes = [{"method": r["method"], "path_pattern": r["path_pattern"], "class": "probe", "evidence": evidence_sha256}
              for r in candidate["row_probe_routes"]]
    return {"config_id": config_id, "entrypoint": list(entrypoint), "env_allowlist": {},
            "logger": {**candidate["logger"], "level_env": LOGGER_LEVEL_ENV, "min_level": LOGGER_MIN_LEVEL},
            "routes": [*inference_routes, *probes],
            "reachable_routes": [f"{r['method']} {r['path_pattern']}" for r in (*inference_routes, *probes)],
            "api_fields": fields, "health_cache_ms": candidate["health_cache_ms"],
            "health_refresh_bound_ms": candidate["health_refresh_bound_ms"],
            "evidence": {"characterization": evidence_name, "characterization_sha256": evidence_sha256}}


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
    logs_key = next((k for k in series if k.startswith("/api/usage/call-logs?")), "")
    logs = _ticks(series.get(logs_key, []))
    windows = {sc["scenario"]: sc for sc in evidence["scenarios"] if "window_start" in sc}

    def pulse(names, ticks, **kwargs):
        hits: set[str] = set()
        for name in (names,) if isinstance(names, str) else names:
            sc = windows.get(name)
            if sc:
                hits.update(_pulse_pointers(ticks, sc["window_start"], sc["window_end"], **kwargs))
        return sorted(hits)

    fields: dict[str, Any] = {}
    # pending_by_model first: the /pending/byModel object whose model counters pulse while work is in flight
    PENDING_BY_MODEL = "/pending/byModel"
    pending_children = pulse(INFLIGHT_SCENARIOS, usage, prefix_in=f"{PENDING_BY_MODEL}/", absent_is_zero=True)
    parents = [PENDING_BY_MODEL] if pending_children else []
    if len(parents) == 1:
        fields["pending_by_model"] = parents[0]
    else:
        incomplete.append(f"api_fields.pending_by_model (candidates: {sorted(set(pending_children))})")
        review_notes["pending_children"] = pending_children

    # active: an integer in-flight counter in /api/usage/history during a long stream. Pointers already
    # claimed by another field are excluded, so an ambiguous measurement is never guessed at.
    claimed = tuple(sorted({p.rsplit("/", 1)[0] + "/" for p in pending_children} | set(parents)))
    active = pulse(INFLIGHT_SCENARIOS, usage, prefix_not=claimed)
    if not active:
        # No integer counter: an array whose length pulses is the in-flight signal (the runtime counts its length).
        arrays = [p for p in pulse(INFLIGHT_SCENARIOS, usage, include_lengths=True, prefix_not=claimed)
                  if p.endswith(".length")]
        review_notes["active_list_only"] = arrays
        active = [p.removesuffix(".length") for p in arrays]
    if len(active) == 1:
        fields["active"] = active[0]
    else:
        incomplete.append(f"api_fields.active (candidates: {active})")

    # admission: integer admission counters in health during the queued scenario
    admission = sorted(set(pulse("queued_request", health, prefix_in="/chatAdmission/")
                           + pulse("queued_request", health, prefix_in="/adaptiveAdmission/")))
    if admission:
        fields["admission"] = admission
    else:
        incomplete.append("api_fields.admission")
    # rows: the call-log page itself, its page limit, and per row the in-flight flag, the path and the trace time
    root = logs[0][1] if logs else {}
    if any(p == ".length" for p in root):
        rows_pointer = ""  # RFC 6901 root pointer: the response document is the row array itself
    elif any(p == "/rows.length" for p in root):
        rows_pointer = "/rows"
    else:
        rows_pointer = None
    table = evidence.get("row_table") or {}
    pages = [(t, _rows_of(b, table)) for t, b in logs] if rows_pointer is not None else []
    rows = [(t, row) for t, page in pages for row in page]
    row_keys = sorted({k for _, row in rows for k in row})
    memory_flag = next((k for k in row_keys if k in ("active", "inMemory", "memory")), None)
    # the path key: the one carrying the measured client route on client rows (in-memory rows have no method)
    path_keys = [k for k in row_keys if any(row.get(k) == CLIENT_PATH for _, row in rows)]
    # the trace time: a key every row carries as an epoch-ms clock the runtime reads, never ahead of the sampler tick
    stamp_keys = [k for k in row_keys if rows and all(
        (ms := _epoch_ms_or_none(row.get(k))) is not None and 10 ** 12 < ms <= t * 1000 + 1000 for t, row in rows)]
    limit = urllib.parse.parse_qs(urllib.parse.urlsplit(logs_key).query).get("limit", [""])[0]
    if rows_pointer is not None and memory_flag and len(path_keys) == 1 and len(stamp_keys) == 1 \
            and limit.isdigit() and int(limit) > 0:
        fields.update(rows=rows_pointer, rows_page_limit=int(limit), row_memory_flag=memory_flag,
                      row_path=path_keys[0], row_timestamp=stamp_keys[0])
        review_notes["row_order"] = _row_order(pages, stamp_keys[0], memory_flag)
    else:
        incomplete.append(f"api_fields.rows (paths: {path_keys}, trace times: {stamp_keys}, limit: {limit!r})")
    # internal probe rows: a non-client path whose rows were written inside the provider_test window and that the
    # client access log never shows; any other row path stays unclassified (the runtime defers `coverage`)
    probe_window = windows.get("provider_test")
    probe_routes: set[tuple[str, str]] = set()
    if probe_window and "row_path" in fields:
        journal_paths = {pair[1] for line in evidence["journal"] if (pair := _access_pair(line))}
        start, end = probe_window["window_start"] * 1000, probe_window["window_end"] * 1000
        for _, row in rows:
            path, method = row.get(fields["row_path"]), row.get("method")
            if path == CLIENT_PATH or path in journal_paths or not isinstance(path, str) or not path.startswith("/"):
                continue
            if isinstance(method, str) and method and start <= epoch_ms(row[fields["row_timestamp"]]) <= end:
                probe_routes.add((method, path))
    if not probe_routes:
        incomplete.append("row probe routes (no internal row was measured in the provider_test window)")
    candidate["row_probe_routes"] = [{"method": a, "path_pattern": b} for a, b in sorted(probe_routes)]
    # extra client routes, each driven once in `client_routes`: a route that answered 2xx and wrote its own access-log
    # line is served by the shared chat handler (the one access-log call site) and is an inference route with the chat
    # route's signals; a route with neither an access line nor a call-log row is unlogged (no activity signal at all,
    # e.g. GET /v1/models: nothing to classify); anything else stays unprofiled and the runtime defers `coverage`
    # naming it whenever one of its rows or lines shows up.
    driven = next((sc for sc in evidence["scenarios"] if sc["scenario"] == "client_routes"), None)
    row_paths = {str(row.get(fields["row_path"])).split("?", 1)[0] for _, row in rows} if "row_path" in fields else set()
    client_routes, by_kind = [], {"inference": [], "unlogged": [], "unprofiled": []}
    for r in (driven or {}).get("routes", []):
        key = {"method": r["method"], "path_pattern": r["path"]}
        kind = "inference" if r["ok"] and r["access_lines"] > 0 else \
            "unlogged" if r["access_lines"] == 0 and r["path"] not in row_paths else "unprofiled"
        by_kind[kind].append(f"{r['method']} {r['path']} (HTTP {r['status']})")
        if kind == "inference":
            client_routes.append(key)
    candidate["client_routes"] = client_routes
    if driven is not None:
        review_notes["client_routes"] = by_kind
    candidate["api_fields"] = fields

    # health_timestamp: an epoch-millisecond clock (integer, or an RFC 3339 UTC string the runtime converts) that
    # advances across ticks. With several, the one the endpoint refreshes most reliably (shortest worst refresh
    # step) is the document's own clock: a timestamp that only moves around traffic (measured 2026-10-23:
    # /adaptiveAdmission/resourceObservedAtMs, 40 s gaps) cannot prove freshness through the idle window. Ties (a
    # cached payload refreshes every clock in it at once: 7d2169fb2bd6 caches the health payload 30 s, source-verified)
    # go to the clock that tracks the wall clock: the smallest worst |sampler time - value| when a new value is first
    # seen. The snapshot's own computation time is about one tick old then; an activity clock is as old as the activity.
    ends = [sc["window_end"] for sc in evidence["scenarios"] if "window_end" in sc]
    cutoff = max(ends) if ends else None
    # Measure only while the deployment was still serving: the destructive shutdown scenario stalls every clock.
    serving = [pair for pair in health if cutoff is None or pair[0] <= cutoff] or health
    ms_like = sorted({p for _, b in health for p, v in b.items()
                      if (ms := _epoch_ms_or_none(v)) is not None and ms > 10 ** 12})
    worst: dict[str, tuple[float, float]] = {}
    for pointer in ms_like:
        raw = [_epoch_ms_or_none(b[pointer]) for _, b in health if pointer in b]
        values = [v for v in raw if v is not None]
        if len(values) >= 3 and len(values) == len(raw) and all(x <= y for x, y in itertools.pairwise(values)) \
                and len(set(values)) >= 2:
            seen = [(v, t) for t, b in serving if pointer in b and (v := _epoch_ms_or_none(b[pointer])) is not None]
            _, steps = _refresh_steps(seen)
            lags = [abs(t * 1000 - v) for (v, t), (prev, _) in zip(seen[1:], seen) if v != prev]
            worst[pointer] = (max(steps) if steps else math.inf, max(lags) if lags else math.inf)
    advancing = sorted(worst)
    best = [p for p in advancing if worst[p] == min(worst.values())]
    if len(best) == 1:
        candidate["health_timestamp"] = best[0]
        if len(advancing) > 1:
            review_notes["health_timestamp_worst_step_ms"] = {p: round(worst[p][0] * 1000) for p in advancing}
            review_notes["health_timestamp_worst_first_seen_lag_ms"] = {p: round(worst[p][1]) for p in advancing}
    else:
        candidate["health_timestamp"] = None
        incomplete.append(f"health_timestamp (epoch-ms candidates: {advancing})")
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
    if stamp:
        dwells, steps = _refresh_steps([(v, t) for t, b in serving
                                         if stamp in b and (v := _epoch_ms_or_none(b[stamp])) is not None])
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

    # The 5 s idle regime (stale-while-revalidate): the primed read the runtime uses must be fresh within the derived
    # bounds, and the prime gap must stay below the cache window (so the read is served the snapshot the prime made).
    idle = next((sc for sc in evidence["scenarios"] if sc["scenario"] == "idle_poll"), None)
    if idle is None:
        incomplete.append("idle_poll (no idle-regime health evidence)")
    elif stamp and isinstance(c_ms := candidate.get("health_cache_ms"), int) \
            and isinstance(b_ms := candidate.get("health_refresh_bound_ms"), int):
        def ages(key: str) -> list[int | None]:
            return [None if (v := _epoch_ms_or_none(p[key].get(stamp))) is None else round(p[f"t_{key}"] * 1000) - v
                    for p in idle["polls"]]
        limit = c_ms + b_ms
        primed = ages("read")
        review_notes["idle_poll"] = {"interval_s": idle["interval_s"], "prime_gap_s": idle["prime_gap_s"],
                                     "lone_age_ms": ages("lone"), "primed_age_ms": primed, "limit_ms": limit}
        if not primed or any(a is None or not 0 <= a <= limit for a in primed) \
                or idle["prime_gap_s"] * 1000 >= c_ms:
            incomplete.append(f"idle_poll (primed health ages {primed} ms vs 0..{limit} ms, "
                              f"prime gap {idle['prime_gap_s']} s vs cache {c_ms} ms)")

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


def collect(image: str, *, parent: Path = REPO / "tmp", log: Callable[[str], None] = lambda _m: None) -> dict[str, Any]:
    """Run every scenario against `image` (a host-store reference or config id) and return the evidence document.
    Everything it starts (the isolated store and its containers, the fake provider, the journal follower) is gone
    when it returns or raises; a root left by a SIGKILLed run is reaped by the next one."""
    IsolatedBackend.sweep(parent)
    found = subprocess.run(["podman", "image", "inspect", "--format", "{{.Id}}", image],
                           capture_output=True, text=True, check=False)
    if found.returncode != 0:
        raise BackendUnavailable(f"host image store lacks {image}; run: podman pull {image}")
    host_id = "sha256:" + found.stdout.strip().removeprefix("sha256:")
    backend = IsolatedBackend(parent)
    fake: subprocess.Popen[bytes] | None = None
    sampler: Sampler | None = None
    try:
        log("importing the image into the isolated backend")
        backend.import_host_image(image)
        backend.podman("tag", host_id.removeprefix("sha256:"), TARGET_REF)
        password, port, fake_port = secrets.token_urlsafe(18), free_port(), free_port()  # throwaway credential
        path = backend.root / "docker-compose.yml"
        path.write_text(yaml.safe_dump(_compose(TARGET_REF, port, password)))
        log("deploying")
        backend.deploy(path)
        config_id = backend.image_of("omniroute")
        if config_id != host_id:
            raise BackendUnavailable(f"the isolated copy is {config_id}, not {host_id}")
        full_id = backend.podman("inspect", "omniroute", "--format", "{{.Id}}").stdout.strip()
        entrypoint = json.loads(backend.podman("inspect", "omniroute", "--format", "{{json .Config.Entrypoint}}").stdout)

        state_file = backend.root / "fake-inference.json"
        state_file.write_text("{}")
        # 0.0.0.0: the container reaches the host through host.containers.internal, not loopback. No secrets inside.
        fake = subprocess.Popen([sys.executable, str(FAKE_INFERENCE), "--host", "0.0.0.0", "--port", str(fake_port),
                                 "--state", str(state_file)])
        time.sleep(0.5)
        _provider(omni.build_payload(fake_port, "fixture-key"), port, fake_port, password)
        api_key = remote_setup.ensure_api_key(_base(port), password, backend.root / "key.json")
        ctx = {"port": port, "password": password, "state_file": state_file, "api_key": api_key}

        # Precondition gate: the client route must be live before anything is recorded.
        # Ruling (measured 2026-10-23, this OmniRoute build): a freshly registered provider's models are
        # not listed in /v1/models until a sync cycle runs, so the listing is only informational; the
        # authoritative liveness proof is the `llama-cpp/fake-model` chat completion returning 200
        # (verified: 200 with the prefix, 400 "Unable to determine provider" without it).
        response = _chat(port, api_key)
        response.read()
        if response.status != 200:
            raise RuntimeError(f"precondition failed: chat completion through the fake provider returned "
                               f"{response.status} (the container may not reach host.containers.internal)")

        sampler = Sampler(omni._login(_base(port), password), full_id, port)
        sampler.start()
        scenarios: list[dict[str, Any]] = []
        log("running the scenarios")
        scenarios.append(_run_scenario(ctx, sampler, "long_stream", chunks=20, delay=0.5, stream=True))
        scenarios.append(_run_scenario(ctx, sampler, "short_between_polls", chunks=1, delay=0.15))
        scenarios.append(_run_scenario(ctx, sampler, "upstream_failure", mode="fail"))
        scenarios.append(_run_scenario(ctx, sampler, "queued_request", chunks=3, delay=0.5, count=8, concurrent=8))
        scenarios.append(_run_scenario(ctx, sampler, "boundary_arrival", chunks=1, delay=0.1, arrival=True))
        scenarios.append(_provider_test_scenario(ctx, sampler))
        scenarios.append(_client_routes_scenario(ctx, sampler))
        scenarios.append(_run_scenario(ctx, sampler, "burst", chunks=1, delay=0.0, count=200, concurrent=1, idle=5.0))
        scenarios.append(_idle_poll_scenario(sampler))
        # destructive, last: native stop while a long stream is open
        state_file.write_text(json.dumps({"mode": "ok", "chunks": 20, "delay": 0.5}))
        stream_response = _chat(port, api_key, stream=True)
        stop_started = time.time()
        backend.assert_owned()
        stop = backend._run(["podman", "stop", "--time", "40", "omniroute"])
        drained = b"" != stream_response.readline()
        scenarios.append({"scenario": "native_shutdown_drain", "stop_rc": stop.returncode,
                          "stop_duration_s": round(time.time() - stop_started, 3), "stream_completed": drained})
        time.sleep(5)
        sampler.close()
        return {
            "config_id": config_id,
            "image": image,
            "entrypoint": entrypoint,
            "versions": {
                "podman": backend._run(["podman", "--version"]).stdout.strip(),
                "podman-compose": backend._run(["podman-compose", "--version"]).stdout.strip(),
                "journalctl": subprocess.run(["journalctl", "--version"], capture_output=True, text=True,
                                             check=False).stdout.splitlines()[0],
            },
            "series": sampler.series,
            "journal": sampler.journal,
            "row_table": sampler.row_table,
            "scenarios": scenarios,
        }
    finally:
        if sampler is not None:
            sampler.close()
        _stop(fake)
        backend.close()


def derive_and_store(evidence: dict[str, Any], store_dir: Path,
                     log: Callable[[str], None] = lambda _m: None) -> tuple[dict[str, Any], Path, Path | None]:
    """Keep the evidence and its candidate in the private store, derive from the stored bytes, and keep the profile
    only when the candidate is complete and the runtime loader accepts it from the store.
    Returns (candidate, evidence path, profile path or None)."""
    store_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    store_dir.chmod(0o700)
    hexid = evidence["config_id"].removeprefix("sha256:")
    ev_path, cand_path = store_dir / f"{hexid}.evidence.json", store_dir / f"{hexid}.candidate.json"
    profile_path = store_dir / f"{hexid}.profile.json"
    profile_path.unlink(missing_ok=True)  # an entry never outlives the evidence it was bound to
    write_private(str(ev_path), json.dumps(evidence, indent=2, sort_keys=True))
    raw = ev_path.read_bytes()
    log("deriving the profile from the stored evidence")
    candidate = derive_candidate(json.loads(raw))
    entry = None
    if not candidate["incomplete"]:
        try:
            entry = promote_candidate(candidate, config_id=evidence["config_id"], evidence_name=ev_path.name,
                                      evidence_sha256=hashlib.sha256(raw).hexdigest(), entrypoint=evidence["entrypoint"])
        except ValueError as exc:
            candidate["incomplete"].append(f"promotion: {exc}")
    if entry is not None:
        log(f"storing {profile_path}")
        write_private(str(profile_path), json.dumps(entry, indent=2, sort_keys=True) + "\n")
        container = {"Config": {"Env": [], "Entrypoint": entry["entrypoint"]},
                     "HostConfig": {"LogConfig": {"Type": "journald"}}}
        try:
            load_profile(evidence["config_id"], container, local_dir=store_dir)
        except Deferral as exc:
            profile_path.unlink()
            candidate["incomplete"].append(f"runtime loader rejected the entry: {exc.reason.value} ({exc.detail})")
            entry = None
    write_private(str(cand_path), json.dumps(candidate, indent=2, sort_keys=True))
    return candidate, ev_path, profile_path if entry is not None else None
