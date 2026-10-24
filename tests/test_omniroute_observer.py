import fcntl
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
from omniroute_fakes import PASSWORD, mk_sample, preflight_world

from pylib.omniroute_activity import IdleGate
from pylib.omniroute_observer import (
    STATE_EXIT,
    StateWriteError,
    lease_alive,
    lock_path,
    observe_loop,
)
from pylib.omniroute_update import (
    Deferral,
    Reason,
    preflight,
    write_preflight_artifacts,
)


def make_stub_path(tmp, **scripts):
    d = tmp / "stubs"
    d.mkdir(exist_ok=True)
    for name, body in scripts.items():
        p = d / name
        p.write_text(f"#!/usr/bin/env bash\n{body}\n")
        p.chmod(0o755)
    return d


def proc_fields(pid):
    return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()  # [state, ppid, pgrp, ...]


def pid_alive(pid):
    try:
        return proc_fields(pid)[0] != "Z"
    except OSError:
        return False


def all_pids():
    return [int(p.name) for p in Path("/proc").iterdir() if p.name.isdigit()]


def any_process_in_group(pgid):
    for pid in all_pids():
        try:
            f = proc_fields(pid)
        except OSError:
            continue
        if int(f[2]) == pgid and f[0] != "Z":
            return True
    return False


def descendants(root):
    found, frontier = set(), {root}
    while frontier:
        nxt = set()
        for pid in all_pids():
            try:
                if int(proc_fields(pid)[1]) in frontier and pid not in found:
                    nxt.add(pid)
            except OSError:
                pass
        found |= nxt
        frontier = nxt
    return found


def start_tick(pid):
    return proc_fields(pid)[19]  # field 22 of /proc/<pid>/stat


def prepared_observe_args(tmp, pid, tick, lifetime, interval="0.2"):
    out = tmp / "art"
    out.mkdir(mode=0o700, exist_ok=True)
    cfg, cp, comp, prov, env, run = preflight_world(tmp)
    dep, snap, fp, eff = preflight(cfg, cp, comp, env, run, provider=prov)
    write_preflight_artifacts(str(out), dep, snap, fp, eff, snap.docs[dep.container_id])
    pw = tmp / "pw"
    pw.write_text(PASSWORD)
    pw.chmod(0o600)
    dl = tmp / "deadline"
    dl.write_text(repr(time.monotonic() + lifetime + 60))
    return ["uv", "run", str(REPO_ROOT / "llmenv.py"), "omniroute-update", "observe", "--dir", str(out), "--config", cp,
            "--compose", comp, "--password-file", str(pw), "--lease-pid", str(pid), "--lease-start-tick", tick,
            "--lifetime", str(lifetime), "--interval", interval, "--min-wait", "1", "--deadline-file", str(dl)]


def start_supervisor(tmp, parent_pid, lifetime, interval, stubs):
    argv = prepared_observe_args(tmp, parent_pid, start_tick(parent_pid), lifetime, interval)
    env = {**os.environ, "PATH": f"{stubs}:{os.environ['PATH']}"}
    return subprocess.Popen(argv, env=env, cwd=REPO_ROOT, start_new_session=True), tmp / "art" / "latest.json"


def wait_for(pred, timeout):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


def read_seq(path):
    try:
        return json.loads(path.read_text())["sequence"]
    except (OSError, ValueError, KeyError):
        return None


# ---- lock and lease ----

def info_run(tmp_path, run_name="run"):
    (tmp_path / "g").mkdir(exist_ok=True)
    (tmp_path / run_name).mkdir(exist_ok=True)
    doc = json.dumps({"store": {"graphRoot": str(tmp_path / "g"), "runRoot": str(tmp_path / run_name)}}).encode()
    return lambda argv, timeout=None: doc


def test_lock_path_converges_for_symlinked_runroot_and_never_unlinks(tmp_path):
    first = lock_path(info_run(tmp_path))
    (tmp_path / "alias").symlink_to(tmp_path / "run")
    alias_doc = json.dumps({"store": {"graphRoot": str(tmp_path / "g"), "runRoot": str(tmp_path / "alias")}}).encode()
    second = lock_path(lambda argv, timeout=None: alias_doc)
    assert first == second and first.stat().st_ino == second.stat().st_ino and first.stat().st_mode & 0o777 == 0o600


def test_lock_path_differs_for_uid_graphroot_and_container_name(tmp_path):
    run = info_run(tmp_path)
    base = lock_path(run)
    assert lock_path(run, uid=base.stat().st_uid + 1) != base  # uid is a hash input only
    assert lock_path(run, container_name="other") != base
    other = tmp_path / "g2"
    other.mkdir()
    doc = json.dumps({"store": {"graphRoot": str(other), "runRoot": str(tmp_path / "run")}}).encode()
    assert lock_path(lambda argv, timeout=None: doc) != base


@pytest.mark.parametrize("breakage", ["mode", "symlink-dir", "foreign-owner", "symlink-file"])
def test_unsafe_lock_directory_or_file_defers_lock(tmp_path, monkeypatch, breakage):
    run = info_run(tmp_path)
    good = lock_path(run)
    directory = good.parent
    if breakage == "mode":
        directory.chmod(0o755)
    elif breakage == "symlink-dir":
        good.unlink()
        directory.rmdir()
        real = tmp_path / "elsewhere"
        real.mkdir(mode=0o700)
        directory.symlink_to(real)
    elif breakage == "foreign-owner":
        monkeypatch.setattr("pylib.omniroute_observer.os.geteuid", lambda: os.stat(directory).st_uid + 1)
    else:
        target = tmp_path / "target"
        target.write_text("")
        good.unlink()
        good.symlink_to(target)
    with pytest.raises(Deferral) as e:
        lock_path(run)
    assert e.value.reason is Reason.lock


def test_flock_is_released_when_the_holder_is_sigkilled(tmp_path):
    path = lock_path(info_run(tmp_path))
    holder = subprocess.Popen(["bash", "-c", f'exec 9>>"{path}"; flock -n 9 && echo held && exec sleep 300'],
                              stdout=subprocess.PIPE, text=True)
    assert holder.stdout is not None and holder.stdout.readline().strip() == "held"
    holder.kill()
    holder.wait()
    fd = os.open(path, os.O_RDWR)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)  # kernel released it on death
    os.close(fd)


def test_lease_alive_matches_pid_and_tick():
    assert lease_alive(os.getpid(), start_tick(os.getpid()))
    assert not lease_alive(os.getpid(), "1")
    p = subprocess.Popen(["true"])
    p.wait()
    assert not lease_alive(p.pid, "1")


# ---- verdict loop ----

class _Stop:
    def __init__(self, n):
        self.n = n

    def is_set(self):
        return self.n <= 0

    def wait(self, timeout):
        self.n -= 1


def test_observe_loop_resamples_immediately_and_publishes_only_the_final_verdict(tmp_path):
    clock_values = iter([0.0, 5.0, 10.0])  # sample_monotonic of the three scheduled samples
    calls, seq = [], iter(range(1, 20))

    def sample_fn(deadline, force_barrier=False):
        n = next(seq)
        calls.append(force_barrier)
        now = next(clock_values) if not force_barrier else 10.1
        return mk_sample(sequence=n, sample_monotonic=now, health_timestamp_ms=n)  # timestamp advances every sample

    latest = tmp_path / "latest.json"
    observe_loop(sample_fn, IdleGate(1, 1e9), str(latest), 0, _Stop(3), 1e9)
    doc = json.loads(latest.read_text())
    assert calls == [False, False, False, True] and doc["sequence"] == 4
    assert doc["verdict"] == "ready" and doc["sample"]["sequence"] == 4
    assert latest.stat().st_mode & 0o777 == 0o600


def test_observe_loop_reports_defer_unknown_with_the_sample_reason(tmp_path):
    latest = tmp_path / "latest.json"
    observe_loop(lambda d, f=False: mk_sample(unknown=True, reason=Reason.feed, sample_monotonic=1.0),
                 IdleGate(1, 1e9), str(latest), 0, _Stop(1), 1e9)
    doc = json.loads(latest.read_text())
    assert doc["verdict"] == "defer-unknown" and doc["reason"] == "feed"


def test_observe_loop_keeps_waiting_through_a_transient_unknown_sample(tmp_path):
    latest = tmp_path / "latest.json"
    observe_loop(lambda d, f=False: mk_sample(unknown=True, reason=Reason.age, sample_monotonic=1.0),
                 IdleGate(1, 1e9), str(latest), 0, _Stop(1), 1e9)
    doc = json.loads(latest.read_text())
    assert doc["verdict"] == "wait" and doc["reason"] == "age"


def test_observe_loop_reports_deadline_reason(tmp_path):
    latest = tmp_path / "latest.json"
    observe_loop(lambda d, f=False: mk_sample(sample_monotonic=50.0), IdleGate(1, 50.0), str(latest), 0, _Stop(1), 1e9)
    doc = json.loads(latest.read_text())
    assert doc["verdict"] == "defer-deadline" and doc["reason"] == "deadline"


def test_observe_loop_reports_its_own_failed_state_write_not_a_feed_problem(tmp_path):
    with pytest.raises(StateWriteError):  # e.g. disk quota: the verdict file cannot be written
        observe_loop(lambda d, f=False: mk_sample(sample_monotonic=1.0), IdleGate(1, 1e9),
                     str(tmp_path / "missing-dir" / "latest.json"), 0, _Stop(1), 1e9)


# ---- process lifecycle ----

def _kill_group_quietly(pid):
    try:
        os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def test_supervisor_dies_within_5s_when_lease_parent_is_killed_even_with_hung_podman(tmp_path):
    stubs = make_stub_path(tmp_path, podman="sleep 300", journalctl="sleep 300")  # both hang forever
    parent = subprocess.Popen(["sleep", "300"])
    sup, _latest = start_supervisor(tmp_path, parent.pid, 120, "0.2", stubs)
    assert wait_for(lambda: pid_alive(sup.pid), 5)
    time.sleep(1.5)
    parent.kill()
    parent.wait()
    assert wait_for(lambda: not any_process_in_group(sup.pid), 5), "supervisor group still alive after 5 s"


def test_lifetime_expiry_exits(tmp_path):
    stubs = make_stub_path(tmp_path, podman="exit 1", journalctl="exit 1")
    parent = subprocess.Popen(["sleep", "300"])
    sup, _latest = start_supervisor(tmp_path, parent.pid, 2, "0.2", stubs)
    try:
        assert wait_for(lambda: not any_process_in_group(sup.pid), 9)
    finally:
        parent.kill()
        parent.wait()
        _kill_group_quietly(sup.pid)


def test_missing_profile_publishes_a_defer_unknown_capability_verdict(tmp_path):
    stubs = make_stub_path(tmp_path, podman="exit 1", journalctl="exit 1")
    parent = subprocess.Popen(["sleep", "300"])
    sup, latest = start_supervisor(tmp_path, parent.pid, 60, "0.2", stubs)
    try:
        assert wait_for(lambda: read_seq(latest) is not None, 20)
        doc = json.loads(latest.read_text())
        assert doc["verdict"] == "defer-unknown" and doc["reason"] == "capability" and doc["sample"]["unknown"] is True
    finally:
        parent.kill()
        parent.wait()
        _kill_group_quietly(sup.pid)


def test_latest_json_is_never_half_written(tmp_path):
    stubs = make_stub_path(tmp_path, podman="exit 1", journalctl="exit 1")
    parent = subprocess.Popen(["sleep", "300"])
    sup, latest = start_supervisor(tmp_path, parent.pid, 30, "0.05", stubs)
    try:
        assert wait_for(lambda: read_seq(latest) is not None, 20)
        seen = set()
        for _ in range(500):
            seen.add(json.loads(latest.read_text())["sequence"])  # raises JSONDecodeError on a torn write
            time.sleep(0.01)
        assert len(seen) > 1
    finally:
        parent.kill()
        parent.wait()
        _kill_group_quietly(sup.pid)


def test_observer_does_not_inherit_lock_fd(tmp_path):
    stubs = make_stub_path(tmp_path, podman="exit 1", journalctl="exit 1")
    parent = subprocess.Popen(["sleep", "300"])
    lockfile = tmp_path / "lock"
    argv = prepared_observe_args(tmp_path, parent.pid, start_tick(parent.pid), 30)
    script = 'exec 9>>"$0"; flock -n 9 || exit 3; setsid "$@" 9>&- & wait'  # the exact shape Task 6 uses
    env = {**os.environ, "PATH": f"{stubs}:{os.environ['PATH']}"}
    sh = subprocess.Popen(["bash", "-c", script, str(lockfile), *argv], env=env, cwd=REPO_ROOT)
    try:
        assert wait_for(lambda: len(descendants(sh.pid)) >= 2, 10)
        time.sleep(1.0)
        inode = lockfile.stat().st_ino
        for pid in descendants(sh.pid):
            for fd in Path(f"/proc/{pid}/fd").iterdir():
                try:
                    assert os.stat(fd).st_ino != inode, f"pid {pid} inherited the lock fd"
                except OSError:
                    pass
    finally:
        parent.kill()
        parent.wait()
        for pid in descendants(sh.pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        sh.kill()


def test_supervisor_exits_with_the_state_code_when_its_verdict_file_cannot_be_written(tmp_path):
    stubs = make_stub_path(tmp_path, podman="exit 1", journalctl="exit 1")
    parent = subprocess.Popen(["sleep", "300"])
    sup, latest = start_supervisor(tmp_path, parent.pid, 60, "0.2", stubs)
    try:
        assert wait_for(lambda: read_seq(latest) is not None, 20)
        latest.parent.chmod(0o500)  # the next write_private cannot create its temp file
        assert sup.wait(timeout=20) == STATE_EXIT
    finally:
        latest.parent.chmod(0o700)
        parent.kill()
        parent.wait()
        _kill_group_quietly(sup.pid)
