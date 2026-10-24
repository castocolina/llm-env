"""Behaviour of scripts/heal-omniroute-admission.sh's memory recycle and state file.

Podman is stubbed; OmniRoute is an in-process HTTP server with the real API shapes, so the healer's curl reads and
the shared `omniroute-update activity` rule (the REAL shipped profile of the live :next image) both run for real."""

import fcntl
import json
import os
import pathlib
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from pylib.resources import DEFAULT_POLICY

CGROUP = "/user.slice/libpod-abc.scope"
MIB = 1048576
CHAT = "/v1/chat/completions"
PROBE = "/api/providers/test"
PROFILE = json.loads((REPO / "pylib/omniroute_activity_profiles.json").read_text())["profiles"][-1]
IDLE_HEALTH = {"chatAdmission": {"activeHeavy": 0, "shedTotal": 0},
               "adaptiveAdmission": {"activeCount": 0, "activeCost": 0}}
INSPECT = [{"Id": "a" * 64, "Image": PROFILE["config_id"].removeprefix("sha256:"),
            "State": {"StartedAt": "2026-10-24T00:00:00Z"},
            "Config": {"Env": [f"{k}={v}" for k, v in PROFILE["env_allowlist"].items()],
                       "Entrypoint": PROFILE["entrypoint"]},
            "HostConfig": {"LogConfig": {"Type": "journald"}}}]
PODMAN = """#!/usr/bin/env bash
STUB="${STUB:-__STUB__}"  # the update CLI runs podman under a scrubbed environment (no STUB)
echo "podman $*" >> "$STUB/calls"
case "$1" in
  info) printf '{"store":{"graphRoot":"%s/g","runRoot":"%s/r"}}' "$STUB" "$STUB" ;;
  inspect) if [[ "$*" == *CgroupPath* ]]; then cat "$STUB/cgroup-path"; else cat "$STUB/inspect.json"; fi ;;
  restart) exit 0 ;;
  *) exit 64 ;;
esac
"""


def _serve(stub: pathlib.Path) -> ThreadingHTTPServer:
    snapshot: dict = {}

    class H(BaseHTTPRequestHandler):
        def _send(self, body):
            data = body.encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            if self.path == "/api/auth/login":
                self.send_header("Set-Cookie", "auth_token=t; Path=/")
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            with open(stub / "calls", "a") as calls:
                calls.write(f"POST {self.path}\n")
            self._send("{}")

        def do_GET(self):
            with open(stub / "calls", "a") as calls:
                calls.write(f"GET {self.path}\n")
            name = {"/api/monitoring/health": "health.json", "/api/usage/history": "history.json",
                    "/api/usage/call-logs": "logs.json"}[self.path.split("?")[0]]
            body = (stub / name).read_text()
            if name == "health.json":  # stale-while-revalidate: answer the snapshot the previous request computed
                doc = json.loads(body)
                fresh = json.dumps({**doc, "timestamp": int(time.time() * 1000)} if isinstance(doc, dict) else doc)
                body = snapshot.get("body", fresh)
                snapshot["body"] = fresh
            self._send(body)

        def log_message(self, *a):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


class Healer:
    def __init__(self, tmp: pathlib.Path):
        self.stub, self.root = tmp / "stub", tmp / "cgroup"
        (self.stub / "bin").mkdir(parents=True)
        (self.stub / "r").mkdir()
        (self.stub / "bin" / "podman").write_text(PODMAN.replace("__STUB__", str(self.stub)))
        (self.stub / "bin" / "podman").chmod(0o755)
        (self.stub / "inspect.json").write_text(json.dumps(INSPECT))
        (self.root / CGROUP.lstrip("/")).mkdir(parents=True)
        (self.stub / "cgroup-path").write_text(CGROUP + "\n")
        self.server = _serve(self.stub)
        self.config = tmp / "models.yml"
        self.config.write_text(f"omniroute:\n  port: {self.server.server_address[1]}\n  initial_password: pw\n")
        self.state = tmp / "state.json"
        self.calls = self.stub / "calls"
        self.calls.touch()
        self.set(memory_mib=2000, cap="2048")

    def set(self, *, memory_mib=None, cap=None, health=None, active=(), logs=()):
        if cap is not None:  # memory.max in MiB, or "max" for an uncapped container
            (self.root / CGROUP.lstrip("/") / "memory.max").write_text(f"{cap if cap == 'max' else int(cap) * MIB}\n")
        if memory_mib is not None:
            (self.root / CGROUP.lstrip("/") / "memory.current").write_text(f"{(memory_mib + 10) * MIB}\n")
            (self.root / CGROUP.lstrip("/") / "memory.stat").write_text(f"anon {memory_mib * MIB}\nfile {10 * MIB}\n")
        (self.stub / "health.json").write_text(json.dumps(health or IDLE_HEALTH))
        (self.stub / "history.json").write_text(json.dumps({"activeRequests": list(active), "pending": {"byModel": {}}}))
        (self.stub / "logs.json").write_text(json.dumps(list(logs)))

    def env(self, **env):
        base = {"PATH": f"{self.stub / 'bin'}:{os.environ['PATH']}", "HOME": str(self.stub), "STUB": str(self.stub),
                "LLM_ENV_CONFIG": str(self.config), "LLM_ENV_HEAL_STATE_FILE": str(self.state),
                "LLM_ENV_CGROUP_ROOT": str(self.root), "UV_PROJECT_ENVIRONMENT": str(REPO / ".venv"),
                "UV_CACHE_DIR": os.environ.get("UV_CACHE_DIR", str(pathlib.Path.home() / ".cache/uv"))}
        return {**base, **env}

    def run(self, **env):
        return subprocess.run(["bash", str(REPO / "scripts/heal-omniroute-admission.sh")], env=self.env(**env),
                              capture_output=True, text=True, timeout=60, check=False)

    def restarts(self):
        return self.calls.read_text().count("podman restart omniroute")

    def saved(self):
        return json.loads(self.state.read_text())


@pytest.fixture
def healer(tmp_path):
    h = Healer(tmp_path)
    yield h
    h.server.shutdown()
    h.server.server_close()


def _iso(epoch):
    return time.strftime("%Y-%m-%dT%H:%M:%S.123Z", time.gmtime(epoch))


@pytest.mark.parametrize("busy", [
    {"health": {**IDLE_HEALTH, "chatAdmission": {"activeHeavy": 1, "shedTotal": 0}}},
    {"health": {**IDLE_HEALTH, "adaptiveAdmission": {"activeCount": 2}}},
    {"active": [{"model": "m", "count": 1}]},
    {"logs": [{"path": CHAT, "timestamp": _iso(time.time() - 30), "active": False}]},
    {"logs": [{"path": CHAT, "timestamp": _iso(time.time() - 900), "active": True}]},
])
def test_busy_container_is_never_recycled(healer, busy):
    healer.set(**busy)
    for _ in range(5):
        r = healer.run()
        assert r.returncode == 0, r.stderr
    assert healer.restarts() == 0
    assert healer.saved()["memStrikes"] == 0


def test_idle_container_below_the_threshold_is_not_recycled(healer):
    healer.set(memory_mib=1200, logs=[{"path": CHAT, "timestamp": _iso(time.time() - 600), "active": False}])
    for _ in range(4):
        assert healer.run().returncode == 0
    assert healer.restarts() == 0


def test_idle_container_above_the_threshold_restarts_once_after_n_samples(healer):
    for _ in range(2):
        assert healer.run().returncode == 0
    assert healer.restarts() == 0 and healer.saved()["memStrikes"] == 2
    r = healer.run()
    assert r.returncode == 0, r.stderr
    assert healer.restarts() == 1 and "memory recycle" in r.stderr
    state = healer.saved()
    assert state["memStrikes"] == 0 and len(state["memRestarts"]) == 1
    for _ in range(4):  # still high: the 6 h cooldown holds it back and says so
        r = healer.run()
    assert healer.restarts() == 1 and "cooldown" in r.stderr


def test_a_strike_resets_when_any_condition_fails(healer):
    healer.run()
    healer.run()
    healer.set(active=[{"model": "m"}])
    healer.run()
    assert healer.saved()["memStrikes"] == 0
    healer.set()
    healer.run()
    assert healer.saved()["memStrikes"] == 1 and healer.restarts() == 0


def test_daily_cap_stops_a_third_memory_restart(healer):
    now = int(time.time())
    healer.state.write_text(json.dumps({"shedTotal": 0, "strikes": 0, "memStrikes": 0,
                                        "memRestarts": [now - 80000, now - 40000, now - 90000]}))
    for _ in range(3):
        r = healer.run(OMNIROUTE_HEAL_MEMORY_COOLDOWN_HOURS="1")
    assert healer.restarts() == 0 and "cap of 2" in r.stderr
    assert len(healer.saved()["memRestarts"]) == 2  # the 25 h old one aged out


def test_old_state_file_without_memory_fields_still_works(healer):
    healer.state.write_text(json.dumps({"shedTotal": 4, "strikes": 1}))
    assert healer.run(OMNIROUTE_HEAL_MEMORY_SAMPLES="1").returncode == 0
    assert healer.restarts() == 1


@pytest.mark.parametrize("break_it", ["memory", "cgroup-path", "history", "logs", "health-field"])
def test_unreadable_memory_or_activity_never_restarts(healer, break_it):
    if break_it == "memory":
        (healer.root / CGROUP.lstrip("/") / "memory.current").unlink()
    elif break_it == "cgroup-path":
        (healer.stub / "cgroup-path").write_text("\n")
    elif break_it == "history":
        (healer.stub / "history.json").write_text("not json")
    elif break_it == "logs":
        (healer.stub / "logs.json").write_text(json.dumps([{"path": CHAT, "timestamp": "yesterday", "active": False}]))
    else:
        healer.set(health={"chatAdmission": {"activeHeavy": 0, "shedTotal": 0}})  # adaptiveAdmission missing
    for _ in range(4):
        healer.run(OMNIROUTE_HEAL_MEMORY_SAMPLES="1")
    assert healer.restarts() == 0


def test_zero_disables_the_memory_recycle(healer):
    for _ in range(4):
        assert healer.run(OMNIROUTE_HEAL_MEMORY_MIB="0").returncode == 0
    assert healer.restarts() == 0
    assert "podman inspect" not in healer.calls.read_text()


@pytest.mark.parametrize(("name", "value"), [
    ("OMNIROUTE_HEAL_MEMORY_MIB", "-1"), ("OMNIROUTE_HEAL_MEMORY_MIB", "1.5G"),
    ("OMNIROUTE_HEAL_MEMORY_SAMPLES", "0"), ("OMNIROUTE_HEAL_MEMORY_COOLDOWN_HOURS", "six"),
])
def test_invalid_settings_error_out_before_touching_omniroute(healer, name, value):
    r = healer.run(**{name: value})
    assert r.returncode == 1 and name in r.stderr
    assert healer.calls.read_text() == ""


def test_provider_test_probe_rows_are_not_activity(healer):
    """The updater's client-vs-probe rule: internal connection tests (even in flight) never keep it busy."""
    healer.set(logs=[{"path": PROBE, "timestamp": _iso(time.time() - 5), "active": True},
                     {"path": PROBE, "timestamp": _iso(time.time() - 20), "active": False},
                     {"path": CHAT, "timestamp": _iso(time.time() - 600), "active": False}])
    for _ in range(3):
        r = healer.run()
        assert r.returncode == 0, r.stderr
    assert healer.restarts() == 1, r.stderr


def test_an_unprofiled_row_route_is_unknown_and_never_recycles(healer):
    healer.set(logs=[{"path": "/v1/brand-new", "timestamp": _iso(time.time() - 600), "active": False}])
    for _ in range(4):
        r = healer.run(OMNIROUTE_HEAL_MEMORY_SAMPLES="1")
    assert healer.restarts() == 0 and "unknown(coverage)" in r.stdout


def test_an_unprofiled_image_is_unknown_and_never_recycles(healer):
    (healer.stub / "inspect.json").write_text(json.dumps([{**INSPECT[0], "Image": "e" * 64}]))
    r = healer.run(OMNIROUTE_HEAL_MEMORY_SAMPLES="1")
    assert healer.restarts() == 0 and "unknown(capability)" in r.stdout


def test_memory_recycle_waits_while_the_update_lock_is_held(healer):
    path = subprocess.run(["uv", "run", "--project", str(REPO), str(REPO / "llmenv.py"), "omniroute-update",
                           "lock-path"], env={**os.environ, **healer.env()}, capture_output=True, text=True,
                          check=True).stdout.strip()
    with open(path, "a") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        r = healer.run(OMNIROUTE_HEAL_MEMORY_SAMPLES="1")
        assert healer.restarts() == 0 and "update lock" in r.stderr
        assert healer.saved()["memRestarts"] == []  # a skipped recycle is not counted toward the cap
    r = healer.run(OMNIROUTE_HEAL_MEMORY_SAMPLES="1")
    assert healer.restarts() == 1, r.stderr


@pytest.mark.parametrize("content", ["", "{", "garbage", "[]", "null", '{"a": 1}{"b": 2}',
                                     '{"shedTotal": "x", "strikes": 1.5, "memStrikes": -3, "memRestarts": "no"}',
                                     '{"memRestarts": [1e300, -5, "x", RECENT]}'])
def test_a_broken_state_file_recovers_and_the_stuck_lease_check_still_runs(healer, content):
    recent = int(time.time()) - 100  # a valid entry inside the 24 h window survives next to the garbage
    healer.state.write_text(content.replace("RECENT", str(recent)))
    healer.set(health={**IDLE_HEALTH, "chatAdmission": {"activeHeavy": 1, "shedTotal": 9}})
    r = healer.run(OMNIROUTE_HEAL_STRIKE_THRESHOLD="1")
    assert r.returncode == 0, r.stderr
    assert healer.restarts() == 1 and "stuck" in r.stderr
    assert healer.saved() == {"shedTotal": 0, "strikes": 0, "memStrikes": 0,
                              "memRestarts": [recent] if "RECENT" in content else []}
    assert not pathlib.Path(f"{healer.state}.tmp").exists()


def test_the_stuck_lease_check_reads_the_primed_health_snapshot_not_the_stale_one(healer):
    port = healer.server.server_address[1]
    urllib.request.urlopen(f"http://127.0.0.1:{port}/api/monitoring/health", timeout=5).read()  # last read: idle
    healer.state.write_text(json.dumps({"shedTotal": 0, "strikes": 0}))
    healer.set(health={**IDLE_HEALTH, "chatAdmission": {"activeHeavy": 1, "shedTotal": 9}})  # stuck since then
    r = healer.run(OMNIROUTE_HEAL_STRIKE_THRESHOLD="1")  # a lone read would answer the idle snapshot above
    assert r.returncode == 0, r.stderr
    assert healer.restarts() == 1 and "stuck" in r.stderr


def test_default_threshold_is_sixty_percent_of_the_cap_below_the_guard_marks(healer):
    """threshold < guard high (0.85 cap) < guard critical (0.92 cap) < cap, for the shipped OmniRoute cap."""
    cap = DEFAULT_POLICY["omniroute"]["memory_max_mib"]
    healer.set(memory_mib=1000, cap=cap)
    r = healer.run()
    assert r.returncode == 0, r.stderr
    threshold = cap * 60 // 100
    assert f"threshold={threshold}MiB cap={cap}MiB" in r.stdout
    assert threshold == 1228 and threshold < 0.85 * cap < 0.92 * cap < cap


def test_the_threshold_follows_a_raised_cap(healer):
    healer.set(memory_mib=2000, cap=4096)  # 2000 MiB is under 60 % of 4096
    for _ in range(4):
        r = healer.run()
    assert healer.restarts() == 0 and "threshold=2457MiB" in r.stdout


def test_an_explicit_threshold_wins_and_warns_when_at_the_guard_mark(healer):
    healer.set(memory_mib=1300)
    r = healer.run(OMNIROUTE_HEAL_MEMORY_MIB="1400")
    assert "threshold=1400MiB" in r.stdout and "high mark" not in r.stderr and healer.restarts() == 0
    r = healer.run(OMNIROUTE_HEAL_MEMORY_MIB="1800")  # >= 0.85 * 2048
    assert "high mark" in r.stderr


def test_an_uncapped_container_without_an_explicit_threshold_never_restarts(healer):
    healer.set(cap="max")
    for _ in range(4):
        r = healer.run(OMNIROUTE_HEAL_MEMORY_SAMPLES="1")
    assert healer.restarts() == 0 and "no memory cap" in r.stderr
    assert healer.run(OMNIROUTE_HEAL_MEMORY_SAMPLES="1", OMNIROUTE_HEAL_MEMORY_MIB="1500").returncode == 0
    assert healer.restarts() == 1
