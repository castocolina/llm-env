"""OmniRoute activity sampling: sanitized observer state, capability profiles, collectors, journal reader."""
from __future__ import annotations

import datetime
import hashlib
import http.client
import json
import math
import os
import re
import selectors
import stat
import subprocess
import time
import urllib.parse
from collections.abc import Callable, Mapping
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
    detail: str = ""  # why an unknown sample is unknown, for the operator (e.g. the unprofiled route); never logic

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
    if s.active > 0 or s.pending > 0 or s.admission > 0 or (s.reason is Reason.busy and not s.unknown):
        return "busy"
    if s.unknown or s.overflow or not (s.feed_ready and s.barrier_seen and s.continuous):
        return "unknown"
    return "idle"


def _first_failure(overflow, discontinuous, feed_missing, coverage_gap) -> Reason | None:
    """Structural journal/row failures in priority order."""
    for cond, reason in ((overflow, Reason.overflow), (discontinuous, Reason.continuity),
                         (feed_missing, Reason.feed), (coverage_gap, Reason.coverage)):
        if cond:
            return reason
    return None


def check_freshness(timestamp_ms, wall_ms, cache_ms, refresh_ms, tolerance_ms) -> Reason | None:
    age = wall_ms - timestamp_ms
    return None if 0 <= age <= cache_ms + refresh_ms + tolerance_ms else Reason.age


def failed_sample(container_id, started_at, sequence, reason: Reason, now, detail: str = "") -> Sample:
    return Sample(schema=1, profile_sha256="0" * 64, container_id=container_id, started_at=started_at, boot_id="",
                  sequence=sequence, sample_monotonic=now, arrival_generation=0, row_generation=0, active=0, pending=0,
                  admission=0, health_timestamp_ms=None, health_age_ms=None, feed_ready=False, barrier_seen=False,
                  continuous=False, overflow=False, unknown=True, reason=reason, detail=detail)


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

    def classify_path(self, path: str) -> str | None:
        """Class of a call-log row by path alone (in-memory rows carry an empty method); None unless unambiguous."""
        clean = str(path).split("?", 1)[0].split(" | ", 1)[0]
        kinds = {route.get("class") for route in self.entry["routes"] if route["path_pattern"] == clean}
        return kinds.pop() if len(kinds) == 1 else None


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


def local_profile_dir(env: Mapping[str, str] = os.environ) -> Path:
    """The user-local profile store: `make update-omniroute` characterizes a running image that has no shipped
    profile and keeps the complete result here (never in the git working tree)."""
    state = env.get("XDG_STATE_HOME") or str(Path(env.get("HOME") or Path.home()) / ".local/state")
    return Path(state) / "llm-env/omniroute-profiles"


def _local_entry(config_id: str, local_dir: Path) -> dict | None:
    """A local entry, bound to its evidence: `<hex>.profile.json` whose evidence `<hex>.evidence.json` (same private
    0700 directory) still hashes to the recorded sha256. Shipped entries are bound the same way by the reproduction
    test on every `make test`; a local one has no review, so the loader checks it on every load."""
    hexid = config_id.removeprefix("sha256:")
    path = local_dir / f"{hexid}.profile.json"
    if not os.path.lexists(path):
        return None
    st = os.lstat(local_dir)
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.geteuid() or st.st_mode & 0o077:
        _profile_defer(Reason.capability, f"local profile store {local_dir} is not a private 0700 directory")
    try:
        entry = json.loads(path.read_text())
        evidence = (local_dir / f"{hexid}.evidence.json").read_bytes()
    except (OSError, ValueError) as exc:
        raise Deferral(Reason.capability, f"local activity profile {path} or its evidence is unreadable") from exc
    ev = entry.get("evidence") if isinstance(entry, dict) else None
    if not isinstance(ev, dict) or entry.get("config_id") != config_id \
            or ev.get("characterization") != f"{hexid}.evidence.json" \
            or ev.get("characterization_sha256") != hashlib.sha256(evidence).hexdigest():
        _profile_defer(Reason.capability, f"local activity profile {path} does not match its evidence")
    return entry


def has_profile(config_id: str, path: Path | str = PROFILE_PATH, local_dir: Path | None = None) -> bool:
    """Whether a shipped entry, or a local one still bound to its evidence, exists (the loader then validates it)."""
    try:
        if any(p.get("config_id") == config_id for p in json.loads(Path(path).read_text()).get("profiles", [])):
            return True
        return _local_entry(config_id, local_profile_dir() if local_dir is None else local_dir) is not None
    except (OSError, ValueError, Deferral):
        return False


def load_profile(config_id: str, container_inspect: dict, path: Path | str = PROFILE_PATH,
                 local_dir: Path | None = None) -> Profile:
    """The shipped entry for `config_id`, else the user-local one; both pass the same checks below."""
    if not isinstance(config_id, str) or _CANON_RE.fullmatch(config_id) is None:
        _profile_defer(Reason.capability, "config id is not the canonical sha256:<64 hex> form")
    try:
        doc = json.loads(Path(path).read_text())
    except (OSError, ValueError) as exc:
        raise Deferral(Reason.capability, "activity profile file is unreadable") from exc
    entries = [p for p in doc.get("profiles", []) if p.get("config_id") == config_id]
    local_dir = local_profile_dir() if local_dir is None else local_dir
    entry = entries[0] if entries else _local_entry(config_id, local_dir)
    if entry is None:
        _profile_defer(Reason.capability, f"no activity profile for {config_id} (shipped or in {local_dir}); "
                                          "`make update-omniroute` characterizes a running image without one")
    for name in ("health_cache_ms", "health_refresh_bound_ms", "evidence", "api_fields", "logger"):
        if name not in entry:
            _profile_defer(Reason.capability, f"profile is missing {name}")
    for name in ("rows", "row_path", "row_timestamp", "row_memory_flag"):
        if not isinstance((entry["api_fields"] or {}).get(name), str):
            _profile_defer(Reason.capability, f"profile is missing api_fields.{name}")
    for name, value in (("health_cache_ms", entry["health_cache_ms"]),
                        ("health_refresh_bound_ms", entry["health_refresh_bound_ms"]),
                        ("api_fields.rows_page_limit", (entry["api_fields"] or {}).get("rows_page_limit"))):
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


def api_answers(port: int, password: str, budget: float = 10.0) -> bool:
    """True when the dashboard login and the authenticated `/api/monitoring/health` GET both succeed with JSON."""
    base, deadline = f"http://127.0.0.1:{port}", time.monotonic() + budget
    try:
        json.loads(bounded_get(base, "/api/monitoring/health", bounded_login(base, password, deadline), deadline))
        return True
    except (Deferral, ValueError):
        return False


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


def _inflight(value):
    """In-flight count: a non-negative integer, or an array whose length is the count (OmniRoute's
    `/api/usage/history` exposes in-flight work only as the `activeRequests` array; one entry per in-flight
    group, so the length is a lower bound that is zero exactly when nothing is in flight)."""
    return len(value) if isinstance(value, list) else _count(value)


_RFC3339_UTC = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?Z")


def epoch_ms(value):
    """Health timestamp in integer epoch milliseconds: an integer as-is, or an RFC 3339 UTC string (`...Z`)."""
    if isinstance(value, str) and _RFC3339_UTC.fullmatch(value):
        whole, _, frac = value[:-1].partition(".")
        stamp = datetime.datetime.strptime(whole, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=datetime.UTC)
        return int(stamp.timestamp()) * 1000 + int((frac + "000")[:3])
    return _count(value)


def _require(doc, pointer, kind):
    value = _ptr(doc, pointer)
    if not isinstance(value, kind):
        raise TypeError("required source is missing or mistyped")
    return value


def _route_name(method, path) -> str:
    """An unprofiled route for the operator: printable ASCII, query and access-log suffix dropped, bounded."""
    clean = str(path).split("?", 1)[0].split(" | ", 1)[0]
    name = " ".join(re.sub(r"[^\x21-\x7e]", "?", str(part)) for part in (method, clean) if part)
    return name[:120] or "(empty path)"


# Bounded call-log paging per sample: a page whose rows are all internal probes and still younger than the idle
# threshold is followed by the next page, at most this many pages; past the bound the sample is a transient unknown.
ROW_PAGES = 5
# `/api/monitoring/health` is stale-while-revalidate (measured live 2026-10-24): a response carries the snapshot the
# PREVIOUS request triggered, so a lone read's age is the gap since the last health request (6-18 s at idle). The
# sampler primes (one discarded GET starts the recompute) and reads again after this gap; capped below the profile's
# health_cache_ms so the read is served the snapshot the prime just computed.
PRIME_GAP_S = 0.2


class Collector:
    """One instance per observation: keeps the reader, the newest client trace, generation counters and clocks."""

    def __init__(self, *, deployment, profile: Profile, reader, fetch: Callable, recheck: Callable,
                 clock: Callable[[], float] = time.monotonic, wall: Callable[[], float] = time.time,
                 sleep: Callable[[float], None] = time.sleep, tolerance_ms: int, min_wait: float):
        self.deployment, self.profile, self.reader, self.fetch, self.recheck = deployment, profile, reader, fetch, recheck
        self.clock, self.wall, self.sleep, self.tolerance_ms = clock, wall, sleep, tolerance_ms
        self.min_wait_ms = int(min_wait * 1000)
        self.sequence = self.arrival_gen = self.row_gen = 0
        self.newest = None
        self.last = None

    def _call_logs(self, deadline: float, wall_ms: int):
        """(newest client trace ms or None, a client row is in flight, an unclassified row path, transient failure).

        Source-verified in both characterized images (`/api/usage/call-logs` route and `getCallLogs`): a page is every
        in-memory row (in flight first, then completed but not yet persisted), followed by the persisted rows
        `ORDER BY timestamp DESC LIMIT limit OFFSET offset`. So in-flight rows are on every page, the newest trace is
        the maximum over client rows, and the last row of a page is the oldest persisted row on it (when the page has
        none, every persisted row was already seen). A page shorter than `limit` holds every remaining persisted row."""
        f = self.profile.api_fields
        limit, horizon = f["rows_page_limit"], wall_ms - self.min_wait_ms
        newest, inflight, gap, failure = None, False, "", None
        for page in range(ROW_PAGES):
            body = json.loads(self.fetch(f"/api/usage/call-logs?limit={limit}&offset={page * limit}", deadline))
            rows = _require(body, f["rows"], list)  # a missing/null/mistyped page is schema unknown, never zero rows
            stamp = None
            for row in rows:
                kind = self.profile.classify_path(row[f["row_path"]])
                try:
                    stamp = epoch_ms(row[f["row_timestamp"]])
                except ValueError:
                    stamp, failure = None, failure or Reason.age  # an unreadable trace time proves no age
                if stamp is not None and stamp > wall_ms + self.tolerance_ms:
                    stamp, failure = None, failure or Reason.clock  # a future trace proves nothing, not even activity
                if kind is None:
                    gap = gap or _route_name("", row[f["row_path"]])  # never characterized: coverage, as for the journal
                elif kind == "inference":
                    inflight = inflight or bool(row.get(f["row_memory_flag"]))
                    if stamp is not None:
                        newest = stamp if newest is None else max(newest, stamp)
            if inflight or (newest is not None and newest > horizon) or len(rows) < limit:
                return newest, inflight, gap, failure  # activity found, or every persisted row has been seen
            if stamp is not None and stamp <= horizon:
                return newest, inflight, gap, failure  # the oldest persisted row on the page is past the threshold
        return newest, inflight, gap, failure or Reason.age  # bound reached with internal rows still too young

    def sample(self, deadline: float, force_barrier: bool = False) -> Sample:
        self.sequence += 1
        s = {"schema": 1, "profile_sha256": self.profile.sha256, "container_id": self.deployment.container_id,
                 "started_at": self.deployment.started_at, "boot_id": "", "sequence": self.sequence,
                 "sample_monotonic": self.clock(), "arrival_generation": self.arrival_gen, "row_generation": self.row_gen,
                 "active": 0, "pending": 0, "admission": 0, "health_timestamp_ms": None, "health_age_ms": None,
                 "feed_ready": False, "barrier_seen": False, "continuous": False, "overflow": False,
                 "unknown": True, "reason": Reason.io}
        # Activity seen this sample from sources independent of the health timestamp (journal arrivals, counters,
        # in-memory call-log rows). It only ever turns a transient failure into `busy`, which delays an update.
        evidence = False
        structural = None
        try:
            self.recheck(deadline)  # inventory + fingerprint, shared budget
            b = self.reader.poll(deadline, force_barrier)
            s.update(boot_id=b.boot_id, feed_ready=b.ready, barrier_seen=b.barrier_seen,
                     continuous=b.continuous, overflow=b.overflow)
            gap = ""
            for rec in b.records:
                kind = self.profile.classify_route(rec.method, rec.path)
                if kind == "inference":
                    self.arrival_gen += 1
                    evidence = True
                elif kind is None:
                    gap = gap or _route_name(rec.method, rec.path)
            s["arrival_generation"] = self.arrival_gen
            structural = _first_failure(b.overflow, not b.continuous, not (b.ready and b.barrier_seen), gap)
            prime_gap = min(PRIME_GAP_S, self.profile.health_cache_ms / 2000)
            try:
                self.fetch("/api/monitoring/health", deadline)  # prime: answers the old snapshot, recomputes behind it
            except Deferral as exc:
                raise Deferral(Reason.io, f"health prime failed ({exc.reason.value})") from exc
            if self.clock() + prime_gap >= deadline:
                raise Deferral(Reason.io, "sample deadline reached before the health read")
            self.sleep(prime_gap)
            t0, w0 = self.clock(), self.wall()  # exactly two wall() reads per sample
            health = json.loads(self.fetch("/api/monitoring/health", deadline))
            t1, w1 = self.clock(), self.wall()
            history = json.loads(self.fetch("/api/usage/history", deadline))
            wall_ms = int(w1 * 1000)
            newest, inflight, row_gap, row_failure = self._call_logs(deadline, wall_ms)
            f = self.profile.api_fields
            s["admission"] = sum(_count(_ptr(health, p)) for p in f["admission"])
            s["active"] = _inflight(_ptr(history, f["active"]))
            s["pending"] = sum(_count(v) for v in _require(history, f["pending_by_model"], dict).values())
            ts = epoch_ms(_ptr(health, f["health_timestamp"]))
            if self.last is not None and newest != self.newest:
                self.row_gen += 1  # a new client trace: activity even when both samples look idle
            self.newest = newest
            s.update(row_generation=self.row_gen, health_timestamp_ms=ts)
            s["health_age_ms"] = max(0, wall_ms - ts)
            previous, self.last = self.last, (t1, w1, ts)
            recent = newest is not None and wall_ms - newest < self.min_wait_ms
            busy = s["active"] > 0 or s["pending"] > 0 or s["admission"] > 0 or inflight or recent
            evidence = evidence or busy
            # Structural failures first: they stay unknown even with activity evidence.
            structural = _first_failure(b.overflow, not b.continuous, not (b.ready and b.barrier_seen), gap or row_gap)
            if structural:
                unprofiled = gap or row_gap
                raise Deferral(structural, f"unprofiled route: {unprofiled}" if structural is Reason.coverage else "")
            if row_failure:
                raise Deferral(row_failure, "call-log trace time is unreadable, in the future, or past the page bound")
            if abs((w1 - w0) - (t1 - t0)) * 1000 > self.tolerance_ms or (t1 - t0) * 1000 > 5000:
                raise Deferral(Reason.clock, "wall/monotonic bracket mismatch")
            if previous is not None:
                slack_ms = self.tolerance_ms + 10 * (t1 - previous[0])  # tolerance plus 1% drift over the interval
                if abs((w1 - previous[1]) - (t1 - previous[0])) * 1000 > slack_ms or ts < previous[2]:
                    raise Deferral(Reason.clock, "clock jumped or health timestamp went backward between samples")
            if (reason := check_freshness(ts, wall_ms, self.profile.health_cache_ms,
                                          self.profile.health_refresh_bound_ms, self.tolerance_ms)):
                limit = self.profile.health_cache_ms + self.profile.health_refresh_bound_ms + self.tolerance_ms
                raise Deferral(reason, f"health timestamp age {wall_ms - ts} ms outside 0..{limit} ms")
            s.update(unknown=False, reason=Reason.busy if busy else Reason.ready)
        except Deferral as exc:
            if exc.reason in TRANSIENT_REASONS and evidence and structural is None:
                # The health clock lags by seconds under load (observed 11 s): independent activity wins.
                s.update(unknown=False, reason=Reason.busy)
            else:
                s.update(unknown=True, reason=exc.reason, detail=exc.detail)
        except (ValueError, KeyError, TypeError, AttributeError):
            s.update(unknown=True, reason=Reason.schema)
        return Sample(**s)


# ---- idle gate and override validation (Task 4) ----

# Unknown-sample reasons caused by timing or load that clear on their own: the gate keeps waiting through them for
# up to UNKNOWN_BUDGET_S of continuous unknown samples. Every other reason (capability, coverage, logging, feed,
# overflow, continuity, auth, schema, ownership, input, lock, ...) is structural and defers on its first sample.
TRANSIENT_REASONS = frozenset({Reason.age, Reason.io, Reason.clock})
UNKNOWN_BUDGET_S = 60


class IdleGate:
    """In-memory only: owned by the observer; there is no gate file and no JSON persistence."""

    def __init__(self, min_wait: int, deadline: float):
        self.min_wait, self.deadline = min_wait, deadline
        self.base_ts = None
        self.advanced = False
        self.quiet_since = None
        self.quiet_gens = None
        self.resampling = False
        self.unknown_since = None

    def remaining(self, now) -> float:
        return max(0.0, self.deadline - now)

    def step(self, now, state, *, arrival_generation, row_generation, health_timestamp_ms, reason=None):
        if now >= self.deadline:  # exclusive; checked first
            return "defer-deadline"
        if state == "unknown":
            if self.unknown_since is None:
                self.unknown_since = now
            if reason not in TRANSIENT_REASONS or now - self.unknown_since > UNKNOWN_BUDGET_S:
                return "defer-unknown"
            # A transient unknown proves nothing about activity: drop the quiet window, never commit across it.
            self.quiet_since = self.quiet_gens = None
            self.resampling = False
            return "wait"
        self.unknown_since = None
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
