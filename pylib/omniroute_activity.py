"""OmniRoute activity sampling: sanitized observer state, capability profiles, collectors, journal reader."""
from __future__ import annotations

import hashlib
import http.client
import json
import math
import os
import re
import selectors
import subprocess
import time
import urllib.parse
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal, NoReturn

from pylib.omniroute_update import Deferral, Reason, UpdateError

PROFILE_PATH = Path(__file__).with_name("omniroute_activity_profiles.json")
MAX_BODY = 8 * 1024 * 1024
OVERFLOW = object()
_LEVELS = {"debug": 0, "info": 1, "warn": 2, "error": 3}
_CANON_RE = re.compile(r"sha256:[0-9a-f]{64}")


# ---- sanitized observer state ----

@dataclass(frozen=True)
class Sample:
    schema: int
    profile_sha256: str
    container_id: str
    started_at: str
    boot_id: str
    sequence: int
    sample_monotonic: float
    arrival_generation: int
    row_generation: int
    active: int
    pending: int
    admission: int
    health_timestamp_ms: int | None
    health_age_ms: int | None
    feed_ready: bool
    barrier_seen: bool
    continuous: bool
    overflow: bool
    unknown: bool
    reason: Reason

    def validate(self) -> None:
        for name in ("active", "pending", "admission", "arrival_generation", "row_generation", "sequence"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative non-bool integer")
        if isinstance(self.sample_monotonic, bool) or not isinstance(self.sample_monotonic, (int, float)) \
                or not math.isfinite(self.sample_monotonic):
            raise ValueError("sample_monotonic must be a finite number")
        for name in ("health_timestamp_ms", "health_age_ms"):
            value = getattr(self, name)
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
                raise ValueError(f"{name} must be None or a non-negative integer")

    def to_json(self) -> str:
        doc = asdict(self)
        doc["reason"] = self.reason.value
        return json.dumps(doc, sort_keys=True)

    @staticmethod
    def from_json(text: str) -> Sample:
        doc = json.loads(text)
        doc["reason"] = Reason(doc["reason"])
        return Sample(**doc)


def classify(s: Sample) -> Literal["idle", "busy", "unknown"]:
    if s.active > 0 or s.pending > 0 or s.admission > 0:
        return "busy"
    if s.unknown or s.overflow or not (s.feed_ready and s.barrier_seen and s.continuous):
        return "unknown"
    return "idle"


def check_freshness(timestamp_ms, wall_ms, cache_ms, refresh_ms, tolerance_ms) -> Reason | None:
    age = wall_ms - timestamp_ms
    return None if 0 <= age <= cache_ms + refresh_ms + tolerance_ms else Reason.age


def failed_sample(container_id, started_at, sequence, reason: Reason, now) -> Sample:
    return Sample(schema=1, profile_sha256="0" * 64, container_id=container_id, started_at=started_at, boot_id="",
                  sequence=sequence, sample_monotonic=now, arrival_generation=0, row_generation=0, active=0, pending=0,
                  admission=0, health_timestamp_ms=None, health_age_ms=None, feed_ready=False, barrier_seen=False,
                  continuous=False, overflow=False, unknown=True, reason=reason)


# ---- capability profiles ----

@dataclass(frozen=True)
class Profile:
    sha256: str
    entry: dict

    @property
    def health_cache_ms(self) -> int:
        return self.entry["health_cache_ms"]

    @property
    def health_refresh_bound_ms(self) -> int:
        return self.entry["health_refresh_bound_ms"]

    @property
    def api_fields(self) -> dict:
        return self.entry["api_fields"]

    def classify_route(self, method: str, path: str) -> str | None:
        clean = path.split("?", 1)[0].split(" | ", 1)[0]
        for route in self.entry["routes"]:
            if route["method"] == method and route["path_pattern"] == clean:
                return route.get("class")
        return None


def _profile_defer(reason: Reason, detail: str) -> NoReturn:
    raise Deferral(reason, detail)


def _verify_coverage(entry: dict) -> None:
    routes = entry.get("routes")
    if not isinstance(routes, list):
        _profile_defer(Reason.capability, "routes missing")
    by_key = set()
    for route in routes:  # narrowed to list above
        if not isinstance(route, dict) or "method" not in route or "path_pattern" not in route:
            _profile_defer(Reason.capability, "malformed route entry")
        by_key.add(f"{route['method']} {route['path_pattern']}")
        kind = route.get("class")
        if kind == "inference":
            signals = [route.get(name) for name in ("arrival", "inflight", "queue")]
            complete = all(isinstance(sig, dict) and sig.get("signal") and sig.get("evidence") for sig in signals)
            unreachable = isinstance(route.get("unreachable"), dict) and route["unreachable"].get("evidence")
            if not (complete or unreachable):
                _profile_defer(Reason.coverage, f"inference route lacks independent signals: {route['path_pattern']}")
        elif kind == "probe":
            if not route.get("evidence"):
                _profile_defer(Reason.coverage, f"probe route lacks evidence: {route['path_pattern']}")
        elif kind is None:
            _profile_defer(Reason.coverage, f"route without a class: {route['path_pattern']}")
    for key in entry.get("reachable_routes") or []:
        if key not in by_key:
            _profile_defer(Reason.coverage, f"reachable route has no entry: {key}")


def load_profile(config_id: str, container_inspect: dict, path: Path | str = PROFILE_PATH) -> Profile:
    if not isinstance(config_id, str) or _CANON_RE.fullmatch(config_id) is None:
        _profile_defer(Reason.capability, "config id is not the canonical sha256:<64 hex> form")
    try:
        doc = json.loads(Path(path).read_text())
    except (OSError, ValueError) as exc:
        raise Deferral(Reason.capability, "activity profile file is unreadable") from exc
    entries = [p for p in doc.get("profiles", []) if p.get("config_id") == config_id]
    if not entries:
        _profile_defer(Reason.capability, f"no reviewed activity profile for {config_id}")
    entry = entries[0]
    for name in ("health_cache_ms", "health_refresh_bound_ms", "evidence", "api_fields", "logger"):
        if name not in entry:
            _profile_defer(Reason.capability, f"profile is missing {name}")
    for name in ("health_cache_ms", "health_refresh_bound_ms"):
        value = entry[name]
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            _profile_defer(Reason.capability, f"{name} must be a positive integer")
    _verify_coverage(entry)
    cfg = container_inspect.get("Config") or {}
    env = dict(item.partition("=")[::2] for item in cfg.get("Env") or [])
    for name, expected in (entry.get("env_allowlist") or {}).items():
        if env.get(name) != expected:
            _profile_defer(Reason.logging, f"container env {name} does not match the profile")
    if list(cfg.get("Entrypoint") or []) != list(entry.get("entrypoint") or []):
        _profile_defer(Reason.logging, "container entrypoint does not match the profile")
    logger = entry.get("logger") or {}
    level_env = logger.get("level_env")
    if level_env and level_env in env:
        actual = _LEVELS.get(str(env[level_env]).lower())
        minimum = _LEVELS.get(str(logger.get("min_level", "info")).lower())
        if actual is None or minimum is None or actual > minimum:
            _profile_defer(Reason.logging, f"{level_env} is less verbose than the profile requires")
    if ((container_inspect.get("HostConfig") or {}).get("LogConfig") or {}).get("Type") != "journald":
        _profile_defer(Reason.feed, "container logs are not on journald")
    sha = hashlib.sha256(json.dumps(entry, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return Profile(sha, entry)


# ---- bounded HTTP ----

def _bounded_request(base_url, method, path, headers, body, deadline) -> tuple[int, list[str], bytes]:
    """One HTTP exchange that cannot outlive `deadline`, even against a trickling server.

    The body is read through bounded `read1` chunks: bytes that keep arriving never reset the
    budget, and a socket timeout only bounds a single stalled recv (socket timeouts alone cannot
    enforce an aggregate deadline when data keeps flowing)."""
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise Deferral(Reason.io, "deadline reached before request")
    parsed = urllib.parse.urlsplit(base_url)
    conn = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=min(remaining, 5))
    try:
        conn.request(method, path, body=body, headers=headers)
        resp = conn.getresponse()
        chunks: list[bytes] = []
        total = 0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise Deferral(Reason.io, "deadline reached while reading the response")
            sock = getattr(getattr(resp, "fp", None), "raw", None)
            sock = getattr(sock, "_sock", None)
            if sock is not None:
                try:
                    sock.settimeout(min(remaining, 5))
                except OSError:
                    pass
            try:
                chunk = resp.read1(65536)
            except (OSError, http.client.HTTPException) as exc:
                raise Deferral(Reason.io, type(exc).__name__) from exc
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_BODY:
                raise Deferral(Reason.io, "response exceeds the size limit")
            chunks.append(chunk)
        if time.monotonic() >= deadline:
            raise Deferral(Reason.io, "deadline reached")
        return resp.status, resp.headers.get_all("Set-Cookie") or [], b"".join(chunks)
    except Deferral:
        raise
    except (OSError, http.client.HTTPException) as exc:
        raise Deferral(Reason.io, type(exc).__name__) from exc
    finally:
        conn.close()


def bounded_login(base_url: str, password: str, deadline: float) -> str:
    status, cookies, _ = _bounded_request(base_url, "POST", "/api/auth/login",
                                          {"Content-Type": "application/json"},
                                          json.dumps({"password": password}).encode(), deadline)
    if status != 200:
        raise Deferral(Reason.auth, f"login HTTP {status}")
    for header in cookies:
        for part in header.split(";"):
            if part.strip().startswith("auth_token="):
                return part.strip().removeprefix("auth_token=")
    raise Deferral(Reason.auth, "no session cookie")


def bounded_get(base_url: str, path: str, token: str, deadline: float, _retry: bool = True) -> bytes:
    status, _, data = _bounded_request(base_url, "GET", path, {"Cookie": f"auth_token={token}"}, None, deadline)
    if status == 401:
        raise Deferral(Reason.auth, "session rejected")  # caller re-logins once via Collector (below)
    if status != 200:
        raise Deferral(Reason.io, f"HTTP {status}")
    return data


# ---- journal ----

@dataclass(frozen=True)
class JournalRecord:
    method: str
    path: str


@dataclass(frozen=True)
class JournalBatch:
    records: list
    boot_id: str
    ready: bool
    barrier_seen: bool
    continuous: bool
    overflow: bool


def parse_journal_line(line: bytes, profile: Profile):
    if len(line) > 65536:
        return OVERFLOW
    try:
        doc = json.loads(line)
        inner = json.loads(doc["MESSAGE"])
        fields = profile.entry["logger"]["fields"]
        return JournalRecord(str(inner[fields["method"]]), str(inner[fields["path"]]))
    except (ValueError, KeyError, TypeError):
        return None


class JournalctlReader:
    """Seeds a cursor with a finite `journalctl -n 1` capture, then follows. Children see only `env`."""

    def __init__(self, container_id: str, popen: Callable[..., Any] = subprocess.Popen, argv0: str = "journalctl", env=None):
        self.container_id, self.popen, self.argv0, self.env = container_id, popen, argv0, env
        self.follower = None
        self.seed_cursor = None
        self.seed_boot = None
        self.retained: list[str] = []

    def _seed(self):
        try:
            proc = self.popen([self.argv0, "--output=json", "--no-pager", "-n", "1",
                               f"CONTAINER_ID_FULL={self.container_id}"],
                              stdout=subprocess.PIPE, env=self.env, close_fds=True)
        except OSError:
            return None
        if proc.stdout is None:  # pragma: no cover - Popen(stdout=PIPE) always sets it
            proc.kill()
            proc.wait()
            return None
        with proc.stdout:
            out = proc.stdout.read()
        proc.wait()
        lines = [line for line in out.splitlines() if line.strip()]
        if not lines:
            return None
        try:
            doc = json.loads(lines[-1])
            return str(doc["__CURSOR"]), str(doc["_BOOT_ID"])
        except (ValueError, KeyError):
            return None

    def _spawn_follower(self):
        try:
            self.follower = self.popen([self.argv0, "--output=json", "--no-pager", "--follow",
                                        f"--after-cursor={self.seed_cursor}",
                                        f"CONTAINER_ID_FULL={self.container_id}"],
                                       stdout=subprocess.PIPE, env=self.env, close_fds=True)
            if self.follower.stdout is not None:  # pragma: no cover - Popen(stdout=PIPE) always sets it
                os.set_blocking(self.follower.stdout.fileno(), False)
        except OSError:
            self.follower = None

    def poll(self, deadline: float, force_barrier: bool = False) -> JournalBatch:
        barrier = self._seed()
        if barrier is None:
            return JournalBatch([], self.seed_boot or "", False, False, self.seed_boot is not None, False)
        cursor, boot = barrier
        if self.seed_cursor is None:
            self.seed_cursor, self.seed_boot = cursor, boot
        elif boot != self.seed_boot:
            return JournalBatch([], boot, True, False, False, False)
        if self.follower is None:
            self._spawn_follower()
        records, overflow = [], False
        consumed = False
        continuous = True
        if self.follower is not None and self.follower.stdout is not None:
            stdout = self.follower.stdout
            sel = selectors.DefaultSelector()
            sel.register(stdout, selectors.EVENT_READ)
            # Ruling: the barrier wait shares the sample budget instead of consuming it (cap 2 s), so a
            # follower that never prints the barrier cursor cannot starve the health/history fetches.
            wait_end = min(deadline, time.monotonic() + 2)
            try:
                while time.monotonic() < wait_end and not consumed:
                    events = sel.select(min(0.5, max(0.0, wait_end - time.monotonic())))
                    if not events:
                        continue
                    try:
                        line = stdout.readline()
                    except (BlockingIOError, OSError):
                        continue
                    if not line:
                        break
                    if len(line) > 65536:
                        overflow = True
                        continue
                    try:
                        doc = json.loads(line)
                    except ValueError:
                        continue
                    line_cursor = doc.get("__CURSOR")
                    if doc.get("_BOOT_ID") != self.seed_boot:
                        continuous = False
                    if line_cursor is not None:
                        if line_cursor == cursor or str(line_cursor) in self.retained:
                            consumed = True
                        self.retained.append(str(line_cursor))
                        if len(self.retained) > 10000:
                            overflow = True
                            self.retained = self.retained[-10000:]
                    try:
                        inner = json.loads(doc["MESSAGE"])
                        parsed = json.loads(inner) if isinstance(inner, str) else inner
                        records.append(JournalRecord(str(parsed["method"]), str(parsed["path"])))
                    except (ValueError, KeyError, TypeError):
                        pass
            finally:
                sel.close()
        # Ruling (per plan): a barrier equal to the seed or any retained cursor counts as consumed even when the
        # follower printed nothing new this interval (a quiet feed is not a broken feed).
        consumed = consumed or cursor == self.seed_cursor or cursor in self.retained
        return JournalBatch(records, boot, True, consumed, continuous, overflow)

    def terminate(self) -> None:
        proc, self.follower = self.follower, None
        if proc is None:
            return
        try:
            proc.terminate()
        except OSError:
            pass
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except OSError:
                pass
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass


# ---- clock tolerance ----

def measure_clock_tolerance_ms(wall, clock, samples: int = 20) -> int:
    worst, resolution = 0.0, None
    previous = None
    for _ in range(samples):
        w0, c0, w1, c1 = wall(), clock(), wall(), clock()
        worst = max(worst, abs((w1 - w0) - (c1 - c0)))
        for value in (c0, c1):
            if previous is not None:
                delta = abs(value - previous)
                if delta > 0 and (resolution is None or delta < resolution):
                    resolution = delta
            previous = value
    if resolution is None:
        resolution = 0.001
    return math.ceil((worst + resolution) * 1000)


# ---- collector ----

_MISSING = object()


def _ptr(doc, pointer):
    """RFC 6901 walk; `_MISSING` when any step is absent (a JSON null stays None)."""
    cur = doc
    for part in [p.replace("~1", "/").replace("~0", "~") for p in pointer.split("/")[1:]]:
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return _MISSING
    return cur


def _count(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("not a non-negative count")
    return value


def _require(doc, pointer, kind):
    value = _ptr(doc, pointer)
    if not isinstance(value, kind):
        raise TypeError("required source is missing or mistyped")
    return value


class Collector:
    """One instance per observation: keeps the reader, the previous row hashes, generation counters and clocks."""

    def __init__(self, *, deployment, profile: Profile, reader, fetch: Callable, recheck: Callable,
                 clock: Callable[[], float] = time.monotonic, wall: Callable[[], float] = time.time,
                 tolerance_ms: int):
        self.deployment, self.profile, self.reader, self.fetch, self.recheck = deployment, profile, reader, fetch, recheck
        self.clock, self.wall, self.tolerance_ms = clock, wall, tolerance_ms
        self.sequence = self.arrival_gen = self.row_gen = 0
        self.rows = None
        self.last = None

    def sample(self, deadline: float, force_barrier: bool = False) -> Sample:
        self.sequence += 1
        s = {"schema": 1, "profile_sha256": self.profile.sha256, "container_id": self.deployment.container_id,
                 "started_at": self.deployment.started_at, "boot_id": "", "sequence": self.sequence,
                 "sample_monotonic": self.clock(), "arrival_generation": self.arrival_gen, "row_generation": self.row_gen,
                 "active": 0, "pending": 0, "admission": 0, "health_timestamp_ms": None, "health_age_ms": None,
                 "feed_ready": False, "barrier_seen": False, "continuous": False, "overflow": False,
                 "unknown": True, "reason": Reason.io}
        try:
            self.recheck(deadline)  # inventory + fingerprint, shared budget
            b = self.reader.poll(deadline, force_barrier)
            s.update(boot_id=b.boot_id, feed_ready=b.ready, barrier_seen=b.barrier_seen,
                     continuous=b.continuous, overflow=b.overflow)
            gap = False
            for rec in b.records:
                kind = self.profile.classify_route(rec.method, rec.path)
                if kind == "inference":
                    self.arrival_gen += 1
                elif kind is None:
                    gap = True
            s["arrival_generation"] = self.arrival_gen
            t0, w0 = self.clock(), self.wall()  # exactly two wall() reads per sample
            health = json.loads(self.fetch("/api/monitoring/health", deadline))
            t1, w1 = self.clock(), self.wall()
            history = json.loads(self.fetch("/api/usage/history", deadline))
            logs = json.loads(self.fetch("/api/usage/call-logs?limit=1000&offset=0", deadline))
            f = self.profile.api_fields
            s["admission"] = sum(_count(_ptr(health, p)) for p in f["admission"])
            s["active"] = _count(_ptr(history, f["active"]))
            s["pending"] = sum(_count(v) for v in _require(history, f["pending_by_model"], dict).values())
            ts = _count(_ptr(health, f["health_timestamp"]))
            persisted, memory = set(), set()
            for row in _require(logs, f["rows"], list):  # a missing/null/mistyped source is schema unknown, never zero rows
                digest = hashlib.sha256(str(row[f["row_id"]]).encode()).hexdigest()
                (memory if row.get(f["row_memory_flag"]) else persisted).add(digest)
            current = (frozenset(persisted), frozenset(memory))
            # Explained changes only: a previously seen persisted row must still be there (a page under 1000 rows cannot
            # evict it), and an in-flight row must still be in flight or have become a persisted row.
            lost = self.rows is not None and bool((self.rows[0] - current[0]) or (self.rows[1] - current[0] - current[1]))
            if self.rows is not None and current != self.rows:
                self.row_gen += 1
            self.rows = current
            s.update(row_generation=self.row_gen, health_timestamp_ms=ts)
            wall_ms = int(w1 * 1000)
            s["health_age_ms"] = max(0, wall_ms - ts)
            if len(persisted) >= 1000:
                s.update(overflow=True)
            previous, self.last = self.last, (t1, w1, ts)
            # counts above stay visible so busy still wins; the reasons below are in priority order
            if abs((w1 - w0) - (t1 - t0)) * 1000 > self.tolerance_ms or (t1 - t0) * 1000 > 5000:
                raise Deferral(Reason.clock, "wall/monotonic bracket mismatch")
            if previous is not None:
                slack_ms = self.tolerance_ms + 10 * (t1 - previous[0])  # tolerance plus 1% drift over the interval
                if abs((w1 - previous[1]) - (t1 - previous[0])) * 1000 > slack_ms or ts < previous[2]:
                    raise Deferral(Reason.clock, "clock jumped or health timestamp went backward between samples")
            if (reason := check_freshness(ts, wall_ms, self.profile.health_cache_ms,
                                          self.profile.health_refresh_bound_ms, self.tolerance_ms)):
                raise Deferral(reason, "stale or future health timestamp")
            for cond, reason in ((b.overflow or s["overflow"], Reason.overflow),
                                 (not b.continuous or lost, Reason.continuity),
                                 (not (b.ready and b.barrier_seen), Reason.feed), (gap, Reason.coverage)):
                if cond:
                    raise Deferral(reason, "")
            busy = s["active"] > 0 or s["pending"] > 0 or s["admission"] > 0
            s.update(unknown=False, reason=Reason.busy if busy else Reason.ready)
        except Deferral as exc:
            s.update(unknown=True, reason=exc.reason)
        except (ValueError, KeyError, TypeError, AttributeError):
            s.update(unknown=True, reason=Reason.schema)
        return Sample(**s)


# ---- idle gate and override validation (Task 4) ----

class IdleGate:
    """In-memory only: owned by the observer; there is no gate file and no JSON persistence."""

    def __init__(self, min_wait: int, deadline: float):
        self.min_wait, self.deadline = min_wait, deadline
        self.base_ts = None
        self.advanced = False
        self.quiet_since = None
        self.quiet_gens = None
        self.resampling = False

    def remaining(self, now) -> float:
        return max(0.0, self.deadline - now)

    def step(self, now, state, *, arrival_generation, row_generation, health_timestamp_ms):
        if now >= self.deadline:  # exclusive; checked first
            return "defer-deadline"
        if state == "unknown":
            return "defer-unknown"
        gens = (arrival_generation, row_generation)
        if health_timestamp_ms is not None:
            if self.base_ts is None:
                self.base_ts = health_timestamp_ms
            elif health_timestamp_ms > self.base_ts:
                self.advanced = True
        if state == "busy" or (self.quiet_gens is not None and gens != self.quiet_gens):
            self.quiet_since = self.quiet_gens = None
            self.resampling = False
            return "wait"  # the activity-bearing sample never starts a window
        if not self.advanced:  # spec: quiet needs an observed health advance
            return "wait"
        if self.quiet_since is None:  # quiet begins on the next complete nonbusy sample
            self.quiet_since, self.quiet_gens = now, gens
            return "wait"
        if now - self.quiet_since < self.min_wait:
            return "wait"
        if not self.resampling:  # spec: complete resample before commit
            self.resampling = True
            return "resample"
        return "ready"


def validate_overrides(idle: str | None, max_wait: str | None) -> tuple[int, int]:
    """`None` = unset (defaults 300/10800); `""` or anything non-numeric is invalid; `max >= min`."""
    def parse(value, default):
        if value is None:
            return default
        if not isinstance(value, str) or not value.isascii() or not value.isdecimal() or len(value) > 10:
            raise UpdateError("override must be a positive integer of at most 10 digits")
        number = int(value)
        if not 1 <= number <= 2147483647:
            raise UpdateError("override must be between 1 and 2147483647")
        return number

    min_wait, max_deferred_wait = parse(idle, 300), parse(max_wait, 10800)
    if max_deferred_wait < min_wait:
        raise UpdateError("max_deferred_wait must be >= min_wait")
    return min_wait, max_deferred_wait
