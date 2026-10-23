"""Observer supervisor (parent lease, verdict file) and the canonical update-lock path."""
from __future__ import annotations

import hashlib
import json
import os
import signal
import stat
import threading
import time
from pathlib import Path

from pylib.omniroute_activity import classify
from pylib.omniroute_update import Deferral, Reason, Runner, UpdateError, write_private

LOCK_DIR = "llm-env-update-locks"


def _stat_fields(pid: int) -> list[str]:
    return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()


def lease_alive(pid: int, start_tick: str) -> bool:
    try:
        fields = _stat_fields(pid)
        return fields[19] == start_tick and fields[0] != "Z"
    except (OSError, IndexError):
        return False


def lock_path(run: Runner, container_name: str = "omniroute", uid: int | None = None) -> Path:
    uid = os.geteuid() if uid is None else uid
    try:
        store = json.loads(run(["podman", "info", "--format", "json"])).get("store") or {}
        graph, runroot = os.path.realpath(store["graphRoot"]), os.path.realpath(store["runRoot"])
    except (UpdateError, Deferral, ValueError, KeyError, TypeError) as exc:
        raise Deferral(Reason.io, "podman store roots unavailable") from exc
    key = hashlib.sha256("\0".join([str(uid), graph, runroot, container_name]).encode()).hexdigest()
    directory = Path(runroot, LOCK_DIR)
    try:
        directory.mkdir(mode=0o700)
    except FileExistsError:
        pass
    st = os.lstat(directory)
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.geteuid() or stat.S_IMODE(st.st_mode) != 0o700:
        raise Deferral(Reason.lock, "lock directory is not a private directory owned by this user")
    path = directory / f"omniroute-update-{key}.lock"
    try:
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    except OSError as exc:
        raise Deferral(Reason.lock, "lock file cannot be opened safely") from exc
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid() or stat.S_IMODE(st.st_mode) != 0o600:
            raise Deferral(Reason.lock, "lock file is not a private regular file")
    finally:
        os.close(fd)
    return path


def _verdict(gate, s) -> str:
    return gate.step(s.sample_monotonic, classify(s), arrival_generation=s.arrival_generation,
                     row_generation=s.row_generation, health_timestamp_ms=s.health_timestamp_ms)


def observe_loop(sample_fn, gate, latest_path, interval, stop, lifetime_end, clock=time.monotonic) -> None:
    while not stop.is_set():
        start = clock()
        s = sample_fn(min(start + 5, lifetime_end), False)
        verdict = _verdict(gate, s)
        if verdict == "resample":  # spec: complete resample (fresh barrier) before commit
            s = sample_fn(min(clock() + 5, lifetime_end), True)
            verdict = _verdict(gate, s)
        write_private(latest_path, json.dumps({
            "sequence": s.sequence, "verdict": verdict,
            "reason": "deadline" if verdict == "defer-deadline" else s.reason.value,
            "sample": json.loads(s.to_json())}))
        stop.wait(max(0.0, interval - (clock() - start)))


def _group_members() -> list[int]:
    me, pgid, found = os.getpid(), os.getpgrp(), []
    for entry in Path("/proc").iterdir():
        if entry.name.isdigit() and int(entry.name) != me:
            try:
                fields = _stat_fields(int(entry.name))
            except (OSError, IndexError):
                continue
            if int(fields[2]) == pgid and fields[0] != "Z":
                found.append(int(entry.name))
    return found


def _teardown(terminate) -> None:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    try:
        terminate()
    except Exception:  # noqa: BLE001, S110  best effort: the group kill below is the guarantee
        pass
    os.killpg(os.getpgrp(), signal.SIGTERM)
    end = time.monotonic() + 2
    while time.monotonic() < end and _group_members():
        try:
            os.waitpid(-1, os.WNOHANG)  # reap children that already exited
        except ChildProcessError:
            pass
        time.sleep(0.05)
    for pid in _group_members():
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    os._exit(0)


def supervise(work_fn, terminate, lease_pid: int, lease_tick: str, lifetime: float, clock=time.monotonic) -> None:
    stop, flags = threading.Event(), {"term": False}
    signal.signal(signal.SIGTERM, lambda *_: flags.update(term=True))
    end = clock() + lifetime
    threading.Thread(target=work_fn, args=(stop, end), daemon=True).start()
    while not flags["term"] and clock() < end and lease_alive(lease_pid, lease_tick):
        time.sleep(0.2)
    stop.set()
    _teardown(terminate)
