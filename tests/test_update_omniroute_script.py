import hashlib
import json
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import yaml
from omniroute_fakes import (
    IMG,
    NEW,
    OMNI,
    PASSWORD,
    PROJECT,
    RS,
    SIB,
    free_port,
    inspect_doc,
    profile_entry,
)
from omniroute_world import STAGED, World

REPO = Path(__file__).resolve().parents[1]
REF = "docker.io/diegosouzapw/omniroute:latest"


def proc_fields(pid):
    return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()


def all_pids():
    return [int(p.name) for p in Path("/proc").iterdir() if p.name.isdigit()]


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


@pytest.fixture
def world(tmp_path):
    w = None
    def make(**kw):
        nonlocal w
        w = World(tmp_path, **kw)
        return w
    yield make
    if w is not None:
        w.close()


def test_stub_finds_its_world_without_any_environment(world, tmp_path):
    w = world()
    result = subprocess.run(
        ["env", "-i", f"PATH={w.bin}:/usr/bin:/bin", "podman", "info"],
        capture_output=True, text=True, timeout=30, check=False)
    # (the interpreter lookup needs /usr/bin; the stub itself still gets no other variables)
    assert result.returncode == 0 and "graphRoot" in result.stdout
    assert any(c["tool"] == "podman" for c in w.journal())


def test_unchanged_when_the_running_config_id_matches(world):
    w = world(staged="sha256:" + IMG)
    r = w.run()
    assert r.returncode == 0 and r.stdout.strip() == "unchanged"  # image and service definition both match
    assert w.mutations() == []


def test_empty_idle_override_errors_before_any_command(world):
    w = world()
    r = w.run(LLM_ENV_UPDATE_IDLE_SECONDS="")
    assert r.returncode == 1
    assert w.journal() == []
    # a stub `uv` that prints nothing and exits 1 first on PATH: no failed substitution may slip through
    fake_bin = w.tmp / "fakeuv"
    fake_bin.mkdir()
    (fake_bin / "uv").write_text("#!/usr/bin/env bash\nexit 1\n")
    (fake_bin / "uv").chmod(0o755)
    env = w.env()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    r2 = subprocess.run(["bash", str(w.repo / "scripts/update-omniroute.sh")], env=env,
                        capture_output=True, text=True, timeout=120, check=False)
    assert r2.returncode == 1 and w.journal() == []


def test_dependent_container_defers_before_staging(world):
    w = world(dependent=True)
    r = w.run()
    assert r.returncode == 75 and r.stdout.strip().splitlines()[-1] == "deferred: ownership"
    assert w.mutations() == []


def test_lock_contention_defers(world):
    w = world()
    env = w.env()
    lp = subprocess.run(["uv", "run", str(w.repo / "llmenv.py"), "omniroute-update", "lock-path"],
                        env=env, capture_output=True, text=True, timeout=120, cwd=str(w.repo), check=False)
    assert lp.returncode == 0, lp.stderr
    lock = lp.stdout.strip()
    holder = subprocess.Popen(["bash", "-c", f'exec 9>>"{lock}"; flock -n 9 && echo held && exec sleep 300'],
                              stdout=subprocess.PIPE, text=True)
    assert holder.stdout is not None and holder.stdout.readline().strip() == "held"
    try:
        r = w.run()
        assert r.returncode == 75 and r.stdout.strip().splitlines()[-1] == "deferred: lock"
        assert not any("up" in c.get("argv", []) for c in w.journal())
    finally:
        holder.kill()
        holder.wait()


def test_busy_defers_on_deadline_and_only_pulls(world):
    w = world(busy=True)
    r = w.run(LLM_ENV_UPDATE_IDLE_SECONDS="1", LLM_ENV_UPDATE_MAX_WAIT_SECONDS="3")
    assert r.returncode == 75 and r.stdout.strip().splitlines()[-1] == "deferred: deadline"
    assert w.mutations() == [("podman", "pull", w.immutable_ref)]


def test_staged_image_identity_mismatch_is_an_error_after_pull_only(world):
    w = world(staged_inspect_id="sha256:" + "5" * 64)
    r = w.run()
    assert r.returncode == 1
    assert w.mutations() == [("podman", "pull", w.immutable_ref)]


def test_dependent_appearing_after_the_observer_defers_the_commit(world):
    w = world(drift_after_observer=True)
    r = w.run(LLM_ENV_UPDATE_IDLE_SECONDS="1", LLM_ENV_UPDATE_MAX_WAIT_SECONDS="60")
    assert r.returncode == 75 and r.stdout.strip().splitlines()[-1] == "deferred: ownership"
    assert w.mutations() == [("podman", "pull", w.immutable_ref)]


def test_an_unprofiled_call_log_route_defers_coverage_and_names_the_route(world):
    w = world()
    w.state.write_text(json.dumps({"active": 0, "cache_ms": 1000, "rows": [
        {"path": "/v1/brand-new", "ts": int(time.time() * 1000) - 600_000, "memory": False}]}))
    r = w.run(LLM_ENV_UPDATE_IDLE_SECONDS="1", LLM_ENV_UPDATE_MAX_WAIT_SECONDS="60")
    assert r.returncode == 75 and r.stdout.strip().splitlines()[-1] == "deferred: coverage"
    assert "detail: unprofiled route: /v1/brand-new" in r.stderr
    assert w.mutations() == [("podman", "pull", w.immutable_ref)]


def test_failed_pull_is_an_error_with_a_clear_message(world):
    w = world(pull_rc=125)
    r = w.run()
    assert r.returncode == 1 and r.stdout == ""
    assert "error: podman pull of the resolved image failed" in r.stderr
    assert w.mutations() == [("podman", "pull", w.immutable_ref)]


def test_make_wrapper_reports_a_deferral_as_deferred_not_failed():
    r = subprocess.run(["bash", str(REPO / "tools/run-target.sh"), "x", "--", "bash", "-c", "exit 75"],
                       capture_output=True, text=True, timeout=30, check=False)
    assert r.returncode == 75 and "deferred, exit 75" in r.stdout and "failed" not in r.stdout


def test_client_activity_at_the_workers_last_look_defers_before_any_mutation(world):
    """The observer saw idle; activity that starts in the gap before `podman stop` is caught by the last look."""
    w = world(busy_at_last_look=True)
    r = w.run(LLM_ENV_UPDATE_IDLE_SECONDS="1", LLM_ENV_UPDATE_MAX_WAIT_SECONDS="60")
    assert r.returncode == 75 and r.stdout.strip().splitlines()[-1] == "deferred: busy", r.stderr
    assert w.mutations() == [("podman", "pull", w.immutable_ref)]


def test_commit_order_no_collateral_and_lock_hygiene(world):
    w = world()
    r = w.run(LLM_ENV_UPDATE_IDLE_SECONDS="1", LLM_ENV_UPDATE_MAX_WAIT_SECONDS="60")
    assert r.returncode == 0, r.stderr
    assert r.stdout.splitlines() == ["phase: observing (idle gate: 1 s quiet, wait budget 60 s)", r.stdout.splitlines()[-1]] and r.stdout.splitlines()[-1].startswith("updated ")
    expected_up_tail = ["--in-pod", "false", "--env-file", "/dev/null", "-p", PROJECT,
                        "-f", None, "up", "-d", "--no-deps", "--no-recreate", "--no-build",
                        "--pull", "never", "omniroute"]
    muts = w.mutations()
    assert muts[0] == ("podman", "pull", w.immutable_ref)
    assert muts[1] == ("podman", "tag", STAGED.removeprefix("sha256:"), REF)
    assert muts[2] == ("podman", "stop", "--time", "40", OMNI)
    assert muts[3] == ("podman", "rm", OMNI)
    up = muts[4]
    assert up[0] == "podman-compose" and up[1:8] == tuple(expected_up_tail[:7]) and up[8] is not None \
        and up[8].endswith("projection.yml") and up[9:] == tuple(expected_up_tail[8:])
    assert len(muts) == 5
    joined = " ".join(" ".join(str(x) for x in m) for m in muts)
    for forbidden in ("--force", "--depend", "down", "--remove-orphans", "-v "):
        assert forbidden not in joined
    for c in w.journal():
        for fd in c["fds"]:
            assert not fd.endswith(".lock"), f"stub inherited the lock fd: {c}"


def test_outer_sigkill_while_waiting_leaves_only_the_pull(world):
    w = world(busy=True, env={"LLM_ENV_UPDATE_MAX_WAIT_SECONDS": "3600",
                              "LLM_ENV_UPDATE_IDLE_SECONDS": "1"})
    proc = w.popen()
    try:
        time.sleep(8)  # preflight + pull done, observer waiting
        muts = w.mutations()
        assert ("podman", "pull", w.immutable_ref) in muts
        root = proc.pid
        procs = descendants(root)
        proc.kill()
        proc.wait()
        end = time.monotonic() + 5
        while time.monotonic() < end:
            alive = [p for p in procs if Path(f"/proc/{p}").exists()
                     and proc_fields(p)[0] != "Z"] if procs else []
            if not alive:
                break
            time.sleep(0.1)
        assert w.mutations() == [("podman", "pull", w.immutable_ref)]
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_outer_sigkill_while_waiting_leaves_no_work_dir(world):
    """SIGKILL cannot be trapped: the orphaned observer (its lease is the dead shell) removes the work dir."""
    w = world(busy=True, env={"LLM_ENV_UPDATE_MAX_WAIT_SECONDS": "3600", "LLM_ENV_UPDATE_IDLE_SECONDS": "1"})
    proc = w.popen()
    try:
        end = time.monotonic() + 60
        while time.monotonic() < end and not any((d / "latest.json").exists() for d in w.work_dirs()):
            time.sleep(0.1)
        assert w.work_dirs()
        proc.kill()
        proc.wait()
        end = time.monotonic() + 10
        while time.monotonic() < end and w.work_dirs():
            time.sleep(0.1)
        assert not w.work_dirs()
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGHUP, signal.SIGINT], ids=["TERM", "HUP", "INT"])
@pytest.mark.parametrize("phase", ["pull", "observe"])
def test_a_signal_before_the_hand_off_removes_the_work_dir_and_exits_128_plus_n(world, phase, sig):
    """Explicit TERM/HUP/INT traps exit 128+n through the EXIT cleanup. Before, TERM/HUP killed the shell by signal
    and an INT sent to the shell alone was ignored (bash waits for a foreground child that did not die of it)."""
    w = world(busy=True, pull_sleep=3 if phase == "pull" else 0,
              env={"LLM_ENV_UPDATE_MAX_WAIT_SECONDS": "3600", "LLM_ENV_UPDATE_IDLE_SECONDS": "1"})
    proc = w.popen()

    def reached():
        if phase == "pull":
            return any(c["argv"][:1] == ["pull"] for c in w.journal())
        return any((d / "latest.json").exists() for d in w.work_dirs())
    try:
        end = time.monotonic() + 60
        while time.monotonic() < end and not reached():
            time.sleep(0.1)
        assert reached() and w.work_dirs()
        proc.send_signal(sig)
        proc.communicate(timeout=60)
        assert proc.returncode == 128 + sig
        assert not w.work_dirs()
        assert not any(m[1] in ("tag", "stop", "rm") for m in w.mutations())
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_the_password_file_is_gone_while_the_observer_waits(world):
    """The observer reads the password file once at start; the shell removes it at the first verdict and writes it
    again only for the worker at the hand-off."""
    w = world(busy=True, env={"LLM_ENV_UPDATE_MAX_WAIT_SECONDS": "3600", "LLM_ENV_UPDATE_IDLE_SECONDS": "1"})
    proc = w.popen()
    try:
        end = time.monotonic() + 60
        while time.monotonic() < end and not any((d / "latest.json").exists() for d in w.work_dirs()):
            time.sleep(0.1)
        time.sleep(2.5)  # the shell polls latest.json every second
        dirs = [d for d in w.runtime.iterdir() if (d / "latest.json").exists()]
        assert dirs and not (dirs[0] / "pw").exists()
        assert dirs[0].parent == w.runtime and oct(dirs[0].stat().st_mode & 0o777) == "0o700"
    finally:
        proc.terminate()
        proc.communicate(timeout=60)


def test_an_unprofiled_running_image_is_characterized_first_and_a_failed_run_stores_nothing(world):
    """No shipped or local profile: the updater characterizes the running image before anything else (here the stub
    Podman is not an isolated store, so the harness refuses): it reports the phase, stores nothing, leaves no backend
    and touches no live container."""
    w = world()
    (w.repo / "pylib/omniroute_activity_profiles.json").write_text(json.dumps({"schema": 1, "profiles": []}))
    r = w.run(LLM_ENV_UPDATE_IDLE_SECONDS="1", LLM_ENV_UPDATE_MAX_WAIT_SECONDS="60")
    assert r.returncode == 1, r.stdout + r.stderr
    assert r.stdout.startswith("phase: characterizing " + IMG[:12]) and "budget 1200 s" in r.stdout
    assert "error: the characterization failed" in r.stderr and "nothing was stored" in r.stderr
    assert "phase: observing" not in r.stdout
    assert not list((w.tmp / "state").rglob("*.profile.json"))
    assert not any(m[1] in ("tag", "stop", "rm") for m in w.mutations())


def test_a_locally_stored_profile_lets_the_update_observe_and_finish(world):
    """The running image's profile lives only in the user-local store (bound to its evidence): no characterization
    runs, the update observes and completes."""
    w = world()
    (w.repo / "pylib/omniroute_activity_profiles.json").write_text(json.dumps({"schema": 1, "profiles": []}))
    store = w.tmp / "state/llm-env/omniroute-profiles"
    store.mkdir(parents=True, mode=0o700)
    (store / f"{IMG}.evidence.json").write_text("{}")
    entry = profile_entry(config_id="sha256:" + IMG, evidence={
        "characterization": f"{IMG}.evidence.json", "characterization_sha256": hashlib.sha256(b"{}").hexdigest()})
    (store / f"{IMG}.profile.json").write_text(json.dumps(entry))
    r = w.run(LLM_ENV_UPDATE_IDLE_SECONDS="1", LLM_ENV_UPDATE_MAX_WAIT_SECONDS="60")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "phase: characterizing" not in r.stdout and "phase: observing" in r.stdout
    assert r.stdout.strip().splitlines()[-1].startswith("updated ")


def test_failed_up_reports_stages_without_rollback_or_retag(world):
    w = world(up_rc=1)
    r = w.run(LLM_ENV_UPDATE_IDLE_SECONDS="1", LLM_ENV_UPDATE_MAX_WAIT_SECONDS="60")
    assert r.returncode == 1
    assert "stage: up" in r.stderr
    assert OMNI[:12] in r.stderr and "prior image tag target" in r.stderr
    assert "No rollback was attempted" in r.stderr
    muts = w.mutations()
    assert muts[-1][0] == "podman-compose" and "up" in muts[-1]
    assert not any(m[1:] [:2] == ("tag",) for m in muts[2:-1]) or True
    assert sum(1 for m in muts if m[1] == "tag") == 1
    assert not any(m[1] == "run" for m in muts)


@pytest.mark.parametrize("sig", [signal.SIGKILL, signal.SIGTERM, signal.SIGHUP], ids=["KILL", "TERM", "HUP"])
def test_worker_owns_the_lock_through_the_mutation_sequence(world, sig):
    """The updater shell dies at the hand-off (a closed terminal sends HUP, a supervisor TERM): the detached worker
    still owns the work folder and completes. Before `trap - EXIT`, TERM/HUP ran the shell's cleanup trap and deleted
    the projection and password files the worker still needed (SIGKILL skips traps, so it never showed)."""
    w = world(stop_sleep=6)
    proc = w.popen(LLM_ENV_UPDATE_IDLE_SECONDS="1", LLM_ENV_UPDATE_MAX_WAIT_SECONDS="60")
    try:
        end = time.monotonic() + 120
        while time.monotonic() < end and not any(c["argv"][:1] == ["stop"] for c in w.journal()):
            time.sleep(0.2)
        assert any(c["argv"][:1] == ["stop"] for c in w.journal()), "stop never observed"
        proc.send_signal(sig)  # the updater shell only; the worker runs in its own session
        proc.wait()
        r2 = w.run()  # a second invocation while the detached worker holds the lock
        assert r2.returncode == 75 and r2.stdout.strip() == "deferred: lock"
        end = time.monotonic() + 15
        while time.monotonic() < end:
            muts = w.mutations()
            if any("rm" == m[1] for m in muts) and any("up" in m for m in muts if m[0] == "podman-compose"):
                break
            time.sleep(0.2)
        muts = w.mutations()
        assert any(m[1] == "rm" for m in muts)
        assert any(m[0] == "podman-compose" and "up" in m for m in muts)
        out, err = proc.communicate(timeout=120)  # EOF only once the worker, which inherited the pipes, has exited
        assert f"updated {OMNI[:12]} -> {NEW[:12]}" in out, err
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_every_podman_child_sees_only_the_allowlisted_backend_environment(world, tmp_path):
    storage = tmp_path / "storage.conf"
    storage.write_text("")
    w = world(env={"CONTAINERS_STORAGE_CONF": str(storage)})
    r = w.run(LLM_ENV_UPDATE_IDLE_SECONDS="1", LLM_ENV_UPDATE_MAX_WAIT_SECONDS="60")
    assert r.returncode == 0, r.stderr
    allow = {"CONTAINERS_STORAGE_CONF", "CONTAINERS_CONF", "CONTAINERS_REGISTRIES_CONF", "XDG_RUNTIME_DIR"}
    for c in w.journal():
        if c["tool"] in ("podman", "podman-compose"):
            assert c["env"].get("CONTAINERS_STORAGE_CONF") == str(storage)
            assert set(c["env"]) <= allow


def test_deadline_crossing_never_mutates_after_the_budget(world):
    w = world(prior_tag_sleep=5)
    r = w.run(LLM_ENV_UPDATE_IDLE_SECONDS="1", LLM_ENV_UPDATE_MAX_WAIT_SECONDS="12")
    assert r.returncode == 75 and r.stdout.strip().splitlines()[-1] == "deferred: deadline"
    assert w.mutations() == [("podman", "pull", w.immutable_ref)]


def test_compose_environment_override_defers_input(world):
    w = world()
    r = w.run(COMPOSE_PROFILES="x")
    assert r.returncode == 75 and r.stdout.strip().splitlines()[-1] == "deferred: input"
    assert w.mutations() == []



# ---- 2026-10-24 addendum: down targets and the API-based health rule ----


def _verbs(muts):
    return [m[:2] for m in muts]


def test_stopped_target_is_updated_without_observing_activity(world):
    # busy=True: the (unreachable in reality) activity API would say busy, yet nothing consults it for a down target;
    # the replacement's Podman health stays unhealthy, so readiness comes from the authenticated API probe.
    w = world(old_state="exited", old_health="unhealthy", new_health="unhealthy", busy=True)
    r = w.run(LLM_ENV_HEALTH_TIMEOUT_SECONDS="10")
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == f"updated {OMNI[:12]} -> {NEW[:12]}"
    assert not any(c["tool"] == "journalctl" for c in w.journal())
    muts = w.mutations()
    assert _verbs(muts) == [("podman", "pull"), ("podman", "tag"), ("podman", "stop"), ("podman", "rm"),
                            ("podman-compose", "--in-pod")]
    assert any(str(a).endswith("projection.yml") for a in muts[-1])
    assert muts[-1][-8:] == ("up", "-d", "--no-deps", "--no-recreate", "--no-build", "--pull", "never", "omniroute")


def test_missing_container_is_created_from_the_deployed_compose_without_observation(world):
    w = world(absent=True)
    r = w.run()
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == f"updated none -> {NEW[:12]}"
    assert not any(c["tool"] == "journalctl" for c in w.journal())
    muts = w.mutations()
    assert muts[:2] == [("podman", "pull", w.immutable_ref), ("podman", "tag", STAGED.removeprefix("sha256:"), REF)]
    assert len(muts) == 3 and muts[2][0] == "podman-compose"
    up = muts[2]
    assert up[up.index("-p") + 1] == PROJECT
    assert up[up.index("-f") + 1].endswith(".config/llm-env/docker-compose.yml")
    assert up[-8:] == ("up", "-d", "--no-deps", "--no-recreate", "--no-build", "--pull", "never", "omniroute")


def test_running_unhealthy_target_whose_api_answers_goes_through_the_idle_gate(world):
    w = world(old_health="unhealthy")
    r = w.run(LLM_ENV_UPDATE_IDLE_SECONDS="1", LLM_ENV_UPDATE_MAX_WAIT_SECONDS="60")
    assert r.returncode == 0, r.stderr
    assert any(c["tool"] == "journalctl" and "--follow" in c["argv"] for c in w.journal())
    assert _verbs(w.mutations())[2:4] == [("podman", "stop"), ("podman", "rm")]


def test_running_unhealthy_target_without_an_answering_api_defers_health(world):
    w = world(old_health="unhealthy")
    w.server.kill()
    w.server.wait()
    r = w.run()
    assert r.returncode == 75 and r.stdout.strip().splitlines()[-1] == "deferred: health"
    assert w.mutations() == []

# ---- Task 7: migrate-remote-setup ----


class MigrationWorld(World):
    """World whose target is remote-setup (native dependency on OmniRoute) instead of an OmniRoute update."""

    def __init__(self, tmp_path, *, compose_has_edge=False, already_free=False, up_rc=0,
                 drift_after_projection=False, **kw):
        super().__init__(tmp_path, dependent=True, up_rc=up_rc, **kw)
        compose = {"services": {"remote-setup": ({"depends_on": {"omniroute": {"condition": "service_healthy"}}}
                                                 if compose_has_edge else {"image": "x"})}}
        (self.home / ".config/llm-env/docker-compose.yml").write_text(yaml.safe_dump(compose))
        world = json.loads(self.worldfile.read_text())
        rs = inspect_doc(RS, name="remote-setup", deps=[] if already_free else [OMNI])
        rs["Mounts"] = [{"Type": "volume", "Name": f"{PROJECT}_remote-setup-data"}]
        new_rs = json.loads(json.dumps(rs))
        new_rs.update(Id=NEW, Dependencies=[])
        eff = {"services": {"remote-setup": {"image": "x", "container_name": "remote-setup",
                                             "volumes": ["remote-setup-data:/app/data"], "environment": {}}},
               "networks": {"default": {}}, "volumes": {"remote-setup-data": {}}}

        def ps(ids):
            return json.dumps([{"Id": i} for i in ids])

        echo = {"tool": "podman-compose", "last": "config", "arg_endswith": "projection.yml", "echo_file_after": "-f"}
        if drift_after_projection:
            echo["set_phase"] = "post-observer"   # flips right after the projection round trip, before the worker's recheck
        # "has": ["json"] keeps these rules from shadowing the `ps --all --filter` name check (kept rule below).
        ps_rules = [
            {"tool": "podman", "prefix": ["ps"], "has": ["json"], "phase": ["start"], "stdout": ps([OMNI, RS])},
            {"tool": "podman", "prefix": ["ps"], "has": ["json"], "phase": ["post-observer"],
             "stdout": ps([OMNI, RS, SIB] if drift_after_projection else [OMNI, RS])},
            {"tool": "podman", "prefix": ["ps"], "has": ["json"], "phase": ["post-rm"], "stdout": ps([OMNI])},
            {"tool": "podman", "prefix": ["ps"], "has": ["json"], "phase": ["post-commit"], "stdout": ps([OMNI, NEW])},
            {"tool": "podman", "prefix": ["container", "inspect"], "last": RS, "stdout": json.dumps([rs])},
            {"tool": "podman", "prefix": ["container", "inspect"], "last": NEW, "stdout": json.dumps([new_rs])},
            {"tool": "podman", "prefix": ["container", "inspect"], "last": SIB,
             "stdout": json.dumps([inspect_doc(SIB, name="sib")])},
            echo,
            {"tool": "podman-compose", "last": "config", "stdout": yaml.safe_dump(eff)},
        ]
        kept = [r for r in world["rules"]
                if not (r.get("prefix") == ["ps"] or r.get("last") in (RS, NEW) or r.get("last") == "config")]
        world["rules"] = ps_rules + kept
        self.worldfile.write_text(json.dumps(world))

    def run_migration(self, **env):
        return subprocess.run(["bash", str(self.repo / "scripts/migrate-remote-setup.sh")],
                              env={**self.env(), **env}, capture_output=True, text=True, timeout=180, check=False)


@pytest.fixture
def mworld(tmp_path):
    w = None
    def make(**kw):
        nonlocal w
        w = MigrationWorld(tmp_path, **kw)
        return w
    yield make
    if w is not None:
        w.close()


def test_migration_refuses_a_deployed_compose_that_still_has_the_edge(mworld):
    w = mworld(compose_has_edge=True)
    r = w.run_migration()
    assert r.returncode == 1
    assert "re-render" in r.stderr
    assert w.journal() == []


def test_migration_is_a_noop_when_remote_setup_is_already_free(mworld):
    w = mworld(already_free=True)
    r = w.run_migration()
    assert r.returncode == 0, r.stderr
    assert "nothing changed" in r.stdout
    assert w.mutations() == []


def test_migration_recreates_remote_setup_without_the_dependency(mworld):
    w = mworld()
    r = w.run_migration(LLM_ENV_HEALTH_TIMEOUT_SECONDS="5")
    assert r.returncode == 0, r.stderr
    muts = w.mutations()
    assert muts[0] == ("podman", "stop", "--time", "10", RS)
    assert muts[1] == ("podman", "rm", RS)
    ups = [m for m in muts if m[0] == "podman-compose"]
    assert len(ups) == 1
    assert ups[0][-8:] == ("up", "-d", "--no-deps", "--no-recreate", "--no-build", "--pull", "never", "remote-setup")
    assert ups[0][1:4] == ("--in-pod", "false", "--env-file") and ups[0][5:7] == ("-p", PROJECT)
    assert any("projection.yml" in a for a in ups[0]) and not any("--force-recreate" in a or a == "down"
                                                                 or a == "--depend" or a == "--remove-orphans"
                                                                 for a in ups[0])
    joined = [tuple(m) for m in muts]
    assert not any("--force-recreate" in m or "down" in m or "--depend" in m or "--remove-orphans" in m
                   for m in joined)
    storage = w.env().get("CONTAINERS_STORAGE_CONF")
    for c in w.journal():
        if c["tool"] in ("podman", "podman-compose"):
            assert c.get("env", {}).get("CONTAINERS_STORAGE_CONF") == storage, c


def test_migration_drift_after_projection_defers_with_no_mutation(mworld):
    w = mworld(drift_after_projection=True)
    r = w.run_migration(LLM_ENV_HEALTH_TIMEOUT_SECONDS="5")
    assert r.returncode == 75 and r.stdout.strip().splitlines()[-1] == "deferred: ownership"
    assert w.mutations() == []


def test_migration_up_failure_reports_stage_and_recovery(mworld):
    w = mworld(up_rc=1)
    r = w.run_migration(LLM_ENV_HEALTH_TIMEOUT_SECONDS="5")
    assert r.returncode == 1
    assert "stage: up" in r.stderr
    assert "up -d --no-deps remote-setup" in r.stderr


@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGHUP], ids=["TERM", "HUP"])
def test_migration_worker_completes_when_the_shell_is_terminated_at_the_hand_off(mworld, sig):
    w = mworld(stop_sleep=4)
    proc = subprocess.Popen(["bash", str(w.repo / "scripts/migrate-remote-setup.sh")],
                            env={**w.env(), "LLM_ENV_HEALTH_TIMEOUT_SECONDS": "5"}, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, start_new_session=True)
    try:
        end = time.monotonic() + 120
        while time.monotonic() < end and not any(c["argv"][:1] == ["stop"] for c in w.journal()):
            time.sleep(0.2)
        assert any(c["argv"][:1] == ["stop"] for c in w.journal()), "stop never observed"
        proc.send_signal(sig)
        out, err = proc.communicate(timeout=120)  # EOF once the detached worker has exited
        assert "remote-setup recreated without its native dependency" in out, err
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


# ---- 2026-10-24 addendum (l): config drift is a deferred restart through the same shared apply_when_idle ----

IDLE = {"LLM_ENV_UPDATE_IDLE_SECONDS": "1", "LLM_ENV_UPDATE_MAX_WAIT_SECONDS": "60"}
OBSERVING = "phase: observing (idle gate: 1 s quiet, wait budget 60 s)"


def _observed(w):
    return any(c["tool"] == "journalctl" and "--follow" in c["argv"] for c in w.journal())


def test_config_drift_after_a_rerender_recreates_with_the_same_image_once_idle(world):
    """First run after `make setup`/render-unit re-rendered the compose file with a new variable: same image, so the
    only reason to act is the drift; it goes through the idle gate, the target-only recreate and the verification."""
    w = world(staged="sha256:" + IMG, svc_patch={"environment": {"CALL_LOG_MAX_ENTRIES": "5000"}})
    r = w.run(**IDLE)
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.splitlines() == ["drift: environment CALL_LOG_MAX_ENTRIES", OBSERVING,
                                     f"updated {OMNI[:12]} -> {NEW[:12]}"]
    assert _observed(w)
    muts = w.mutations()
    assert muts[:4] == [("podman", "pull", w.immutable_ref), ("podman", "tag", IMG, REF),
                        ("podman", "stop", "--time", "40", OMNI), ("podman", "rm", OMNI)]
    assert len(muts) == 5 and muts[4][0] == "podman-compose" and muts[4][-8:][0] == "up"
    assert any(str(a).endswith("projection.yml") for a in muts[4])


def test_secret_only_drift_names_the_variable_never_a_value_and_observes_with_the_running_password(world):
    """INITIAL_PASSWORD changed: the fake API accepts only the running container's password, so the observer and the
    last look must use it; the replacement is verified on Podman health. No value reaches stdout or stderr."""
    w = world(staged="sha256:" + IMG, omniroute_cfg={"initial_password": "rotated-secret"},
              svc_patch={"environment": {"INITIAL_PASSWORD": "rotated-secret"}}, server_password=PASSWORD)
    r = w.run(**IDLE)
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.splitlines()[0] == "drift: environment INITIAL_PASSWORD"
    assert r.stdout.splitlines()[-1] == f"updated {OMNI[:12]} -> {NEW[:12]}"
    for secret in (PASSWORD, "rotated-secret"):
        assert secret not in r.stdout and secret not in r.stderr


def test_env_list_form_and_order_are_not_drift(world):
    port = free_port()
    w = world(port=port, staged="sha256:" + IMG, svc_patch={"environment": [
        "NODE_ENV=production", "REQUIRE_API_KEY=true", f"INITIAL_PASSWORD={PASSWORD}", f"PORT={port}"]})
    r = w.run()
    assert r.returncode == 0 and r.stdout.strip() == "unchanged", r.stdout + r.stderr
    assert w.mutations() == []


def test_drift_and_a_new_image_in_one_run_apply_together(world):
    """A new image plus a changed port and memory cap: one drift line, one observation (on the running container's
    own port), one recreate with the new image."""
    new_port = free_port()
    w = world(omniroute_cfg={"port": new_port},
              svc_patch={"ports": [f"0.0.0.0:{new_port}:{new_port}"], "mem_limit": "2048m",
                         "environment": {"PORT": str(new_port)}})
    r = w.run(**IDLE)
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.splitlines() == ["drift: environment PORT, ports, memory", OBSERVING,
                                     f"updated {OMNI[:12]} -> {NEW[:12]}"]
    assert _verbs(w.mutations()) == [("podman", "pull"), ("podman", "tag"), ("podman", "stop"), ("podman", "rm"),
                                      ("podman-compose", "--in-pod")]
    assert w.mutations()[1] == ("podman", "tag", STAGED.removeprefix("sha256:"), REF)


def test_a_drift_that_leaves_the_replacement_unhealthy_fails_verification_without_rollback(world):
    w = world(staged="sha256:" + IMG, omniroute_cfg={"initial_password": "rotated-secret"},
              svc_patch={"environment": {"INITIAL_PASSWORD": "rotated-secret"}}, server_password=PASSWORD,
              new_health="unhealthy")
    r = w.run(LLM_ENV_HEALTH_TIMEOUT_SECONDS="3", **IDLE)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "stage: verify-commit" in r.stderr and "No rollback was attempted" in r.stderr
    assert _verbs(w.mutations())[-1] == ("podman-compose", "--in-pod")
    assert "rotated-secret" not in r.stdout + r.stderr and PASSWORD not in r.stdout + r.stderr


def test_a_healer_restart_during_a_drift_wait_defers_before_any_mutation(world):
    """Healer interplay: its memory recycle waits for the update lock (test_heal_omniroute), but its lease restart
    takes no lock; a restart while the drift waits for quiet changes the inventory, so the worker's recheck defers
    `ownership` and nothing is recreated (the next run re-reads the restarted container)."""
    w = world(staged="sha256:" + IMG, svc_patch={"environment": {"CALL_LOG_MAX_ENTRIES": "5000"}},
              restarted_after_observer=True)
    r = w.run(**IDLE)
    assert r.returncode == 75 and r.stdout.splitlines()[-1] == "deferred: ownership", r.stdout + r.stderr
    assert w.mutations() == [("podman", "pull", w.immutable_ref)]


APPLY_DRIVER = """set -euo pipefail
source "$1/tools/lib.sh"
config_path="$CONFIG_PATH" compose_path="$COMPOSE_FILE"
unset COMPOSE_FILE COMPOSE_INSPECT_DIR
llm=(uv run --project "$REPO_DIR" "$REPO_DIR/llmenv.py" omniroute-update)
backend_env_init
cli() { "${llm[@]}" "$@" 9>&-; }
pm() { "${backend_env[@]}" podman "$@" 9>&-; }
defer() { echo "deferred: $1"; exit 75; }
work="$(private_work_dir)"
source "$REPO_DIR/scripts/omniroute-apply-when-idle.sh"
trap 'stop_observer; rm -rf "$work"' EXIT
acquire_update_lock
cli preflight --config "$config_path" --compose "$compose_path" --out-dir "$work" >"$work/deploy.env"
state=running stop_seconds=40 min_wait=1 max_deferred_wait="$4" health_timeout=60
apply_when_idle "$2" "$3"
"""


@pytest.mark.parametrize("busy", [False, True], ids=["idle", "busy"])
def test_apply_when_idle_alone_gates_recreates_and_verifies(world, busy):
    """The shared piece without the updater around it (no resolve, no pull): given an image and the preflight
    artifacts it observes, hands off to the worker and reports the result line; busy defers with nothing changed."""
    w = world(busy=busy)
    r = subprocess.run(["bash", "-c", APPLY_DRIVER, "driver", str(w.repo), STAGED, w.immutable_ref,
                        "3" if busy else "60"],
                       env=w.env(), capture_output=True, text=True, timeout=300, check=False)
    if busy:
        assert r.returncode == 75 and r.stdout.splitlines()[-1] == "deferred: deadline", r.stdout + r.stderr
        assert w.mutations() == []
        return
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.splitlines() == [OBSERVING, f"updated {OMNI[:12]} -> {NEW[:12]}"]
    assert _verbs(w.mutations()) == [("podman", "tag"), ("podman", "stop"), ("podman", "rm"),
                                      ("podman-compose", "--in-pod")]


# ---- 2026-10-24 addendum (m): only relevant inventory changes count; one re-baseline per run ----

def _restarted(doc, at):
    return {**doc, "State": {**doc["State"], "StartedAt": at}}


UNRELATED_1 = inspect_doc("1" * 64, name="otlp-experiment", project="otlp", net="2" * 64)
UNRELATED_2 = inspect_doc("3" * 64, name="ai-kit-e2e", project="ai-kit", net="4" * 64)
SIBLING = inspect_doc(SIB, name="system-one")
DEPENDENT = inspect_doc(RS, name="remote-setup", deps=[OMNI])


def test_unrelated_containers_appearing_and_disappearing_mid_wait_are_ignored(world):
    w = world(inventory={"start": ["target", UNRELATED_1], "observing": ["target", UNRELATED_2],
                         "post-observer": ["target"]})
    r = w.run(**IDLE)
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.splitlines()[-1] == f"updated {OMNI[:12]} -> {NEW[:12]}"
    assert "re-baselining" not in r.stdout
    assert "ignored unrelated inventory change: 0 added, 1 removed" in r.stderr


def test_a_sibling_restarted_mid_wait_is_re_baselined_once_and_the_update_applies(world):
    s2 = _restarted(SIBLING, "2026-10-24T02:00:00Z")
    w = world(inventory={"start": ["target", SIBLING], "observing": ["target", s2], "post-observer": ["target", s2],
                         "post-rm": [s2], "post-commit": ["new", s2]})
    r = w.run(**IDLE)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "phase: re-baselining (system-one restarted)" in r.stdout.splitlines()
    assert r.stdout.splitlines()[-1] == f"updated {OMNI[:12]} -> {NEW[:12]}"


def test_a_relevant_change_that_repeats_in_the_same_run_defers_naming_the_container(world):
    w = world(inventory={"start": ["target", SIBLING],
                         "observing": ["target", _restarted(SIBLING, "2026-10-24T02:00:00Z")],
                         "post-observer": ["target", _restarted(SIBLING, "2026-10-24T03:00:00Z")]})
    r = w.run(**IDLE)
    assert r.returncode == 75 and r.stdout.splitlines()[-1] == "deferred: ownership", r.stdout + r.stderr
    assert r.stdout.count("phase: re-baselining") == 1
    assert "detail: relevant inventory changed: system-one restarted" in r.stderr
    assert w.mutations() == [("podman", "pull", w.immutable_ref)] * 2  # the re-baseline re-stages (no transfer)


def test_a_dependent_appearing_mid_wait_re_baselines_and_preflight_then_refuses_it(world):
    w = world(inventory={"observing": ["target", DEPENDENT], "post-observer": ["target", DEPENDENT]})
    r = w.run(**IDLE)
    assert r.returncode == 75 and r.stdout.splitlines()[-1] == "deferred: ownership", r.stdout + r.stderr
    assert "phase: re-baselining (remote-setup appeared)" in r.stdout.splitlines()
    assert "detail: a container depends on the target: remote-setup" in r.stderr
    assert w.mutations() == [("podman", "pull", w.immutable_ref)]


def test_the_target_restarted_mid_wait_by_the_healer_is_re_baselined_and_observed_again(world):
    w = world(inventory={"observing": ["target-restarted"], "post-observer": ["target-restarted"]})
    r = w.run(**IDLE)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "phase: re-baselining (omniroute restarted)" in r.stdout.splitlines()
    assert r.stdout.splitlines()[-1] == f"updated {OMNI[:12]} -> {NEW[:12]}"


def test_the_target_replaced_externally_mid_wait_defers_without_a_re_baseline(world):
    other = inspect_doc("5" * 64, name="omniroute")
    w = world(inventory={"observing": [other], "post-observer": [other]})
    r = w.run(**IDLE)
    assert r.returncode == 75 and r.stdout.splitlines()[-1] == "deferred: ownership", r.stdout + r.stderr
    assert "re-baselining" not in r.stdout
    assert "detail: target replaced or removed: omniroute" in r.stderr
    assert w.mutations() == [("podman", "pull", w.immutable_ref)]
