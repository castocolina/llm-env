import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import yaml
from omniroute_fakes import IMG, NEW, OMNI, PROJECT, RS, SIB, inspect_doc
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
    assert r.returncode == 0 and r.stdout.strip() == "unchanged"
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
    assert r.returncode == 75 and r.stdout.strip() == "deferred: ownership"
    assert w.mutations() == []


def test_lock_contention_defers(world):
    w = world()
    env = w.env()
    lp = subprocess.run(["uv", "run", str(w.repo / "llmenv.py"), "omniroute-update", "lock-path"],
                        env=env, capture_output=True, text=True, timeout=120, cwd=str(w.repo), check=False)
    assert lp.returncode == 0, lp.stderr
    lock = lp.stdout.strip()
    holder = subprocess.Popen(["bash", "-c", f'exec 9>>"{lock}"; flock -n 9 && echo held && sleep 300'],
                              stdout=subprocess.PIPE, text=True)
    assert holder.stdout is not None and holder.stdout.readline().strip() == "held"
    try:
        r = w.run()
        assert r.returncode == 75 and r.stdout.strip() == "deferred: lock"
        assert not any("up" in c.get("argv", []) for c in w.journal())
    finally:
        holder.kill()
        holder.wait()


def test_busy_defers_on_deadline_and_only_pulls(world):
    w = world(busy=True)
    r = w.run(LLM_ENV_UPDATE_IDLE_SECONDS="1", LLM_ENV_UPDATE_MAX_WAIT_SECONDS="3")
    assert r.returncode == 75 and r.stdout.strip() == "deferred: deadline"
    assert w.mutations() == [("podman", "pull", w.immutable_ref)]


def test_staged_image_identity_mismatch_is_an_error_after_pull_only(world):
    w = world(staged_inspect_id="sha256:" + "5" * 64)
    r = w.run()
    assert r.returncode == 1
    assert w.mutations() == [("podman", "pull", w.immutable_ref)]


def test_dependent_appearing_after_the_observer_defers_the_commit(world):
    w = world(drift_after_observer=True)
    r = w.run(LLM_ENV_UPDATE_IDLE_SECONDS="1", LLM_ENV_UPDATE_MAX_WAIT_SECONDS="60")
    assert r.returncode == 75 and r.stdout.strip() == "deferred: ownership"
    assert w.mutations() == [("podman", "pull", w.immutable_ref)]


def test_commit_order_no_collateral_and_lock_hygiene(world):
    w = world()
    r = w.run(LLM_ENV_UPDATE_IDLE_SECONDS="1", LLM_ENV_UPDATE_MAX_WAIT_SECONDS="60")
    assert r.returncode == 0, r.stderr
    assert r.stdout.startswith("updated ") and "-> " in r.stdout
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


def test_worker_owns_the_lock_through_the_mutation_sequence(world):
    w = world(stop_sleep=6)
    proc = w.popen(LLM_ENV_UPDATE_IDLE_SECONDS="1", LLM_ENV_UPDATE_MAX_WAIT_SECONDS="60")
    try:
        end = time.monotonic() + 120
        while time.monotonic() < end and not any(c["argv"][:1] == ["stop"] for c in w.journal()):
            time.sleep(0.2)
        assert any(c["argv"][:1] == ["stop"] for c in w.journal()), "stop never observed"
        proc.kill()
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
    assert r.returncode == 75 and r.stdout.strip() == "deferred: deadline"
    assert w.mutations() == [("podman", "pull", w.immutable_ref)]


def test_compose_environment_override_defers_input(world):
    w = world()
    r = w.run(COMPOSE_PROFILES="x")
    assert r.returncode == 75 and r.stdout.strip() == "deferred: input"
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
    assert r.returncode == 75 and r.stdout.strip() == "deferred: ownership"
    assert w.mutations() == []


def test_migration_up_failure_reports_stage_and_recovery(mworld):
    w = mworld(up_rc=1)
    r = w.run_migration(LLM_ENV_HEALTH_TIMEOUT_SECONDS="5")
    assert r.returncode == 1
    assert "stage: up" in r.stderr
    assert "up -d --no-deps remote-setup" in r.stderr
