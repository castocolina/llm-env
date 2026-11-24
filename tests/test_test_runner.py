"""tools/test.sh: capped workers, niced, quiet, offline in its own namespaces, and no opt-in lane leaks into the default suite."""
import os
import re
import signal
import time
from pathlib import Path

import pytest
from runner_helpers import (
    OPT_IN_SWITCHES,
    ROOT,
    calls,
    descendants,
    run_dir,
    run_runner,
    runner_repo,
    start_runner,
    stop_runner,
    wait_until,
)

HINT = "leak check: if another process writes under tmp/, rerun"


def test_runner_caps_workers_at_the_cpu_percentage(tmp_path):
    for pct, workers in ((None, "9"), ("10", "2"), ("1", "1"), ("100", "24")):
        run_runner(repo := runner_repo(tmp_path / str(pct)), env={} if pct is None else {"LLM_ENV_TEST_CPU_PCT": pct})
        argv = calls(repo)[-1].split()
        assert argv[argv.index("-n") + 1] == workers and "-q" in argv and "-v" not in argv and argv[-1] == "tests/"


def test_runner_rejects_a_bad_cpu_percentage(tmp_path):
    for bad in ("0", "101", "abc", "40.5", "", "010", "08", "040", "+5", "1000", "9" * 30):
        result = run_runner(repo := runner_repo(tmp_path / (bad[:8] or "empty")), env={"LLM_ENV_TEST_CPU_PCT": bad})
        assert result.returncode == 2 and "LLM_ENV_TEST_CPU_PCT" in result.stderr and calls(repo) == [], bad


def test_runner_runs_pytest_under_nice_capped_at_19_and_passes_targets(tmp_path):
    run_runner(repo := runner_repo(tmp_path / "a"), "-m", "not slow", "tests/test_x.py")
    assert f"niceness={min(19, os.nice(0) + 10)}" in calls(repo) and calls(repo)[-1].endswith("-m not slow tests/test_x.py")
    run_runner(repo := runner_repo(tmp_path / "b"), prefix=("nice", "-n", "10"))
    assert "niceness=19" in calls(repo)


def test_runner_unsets_every_opt_in_switch(tmp_path):
    run_runner(repo := runner_repo(tmp_path), env=dict.fromkeys(OPT_IN_SWITCHES, "1"))
    assert "optin=" in calls(repo)


def test_every_env_gate_in_tests_is_an_opt_in_switch_the_runner_unsets():
    gate = re.compile(r'skipif\(os\.environ\.get\("(\w+)"\)')
    assert {m for p in (ROOT / "tests").rglob("test_*.py") for m in gate.findall(p.read_text())} == set(OPT_IN_SWITCHES)


def test_runner_has_only_loopback_keeps_the_uid_and_is_offline(tmp_path):
    assert run_runner(repo := runner_repo(tmp_path)).returncode == 0
    assert {"net=lo", f"uid={os.getuid()}", "uv_offline=1"} <= set(calls(repo))


def test_runner_exits_69_when_the_namespace_probe_fails(tmp_path):
    result = run_runner(repo := runner_repo(tmp_path), env={"STUB_UNSHARE_FAIL": "1"})
    assert result.returncode == 69 and "network namespace unavailable" in result.stderr and calls(repo) == []


@pytest.mark.parametrize("rc", [3, 5])
def test_runner_passes_the_pytest_status_through_after_a_good_probe(tmp_path, rc):
    result = run_runner(runner_repo(tmp_path), env={"STUB_RC": str(rc)})
    assert result.returncode == rc


def test_runner_exits_4_with_the_hint_when_uv_cannot_resolve_offline(tmp_path):  # real uv, cold cache
    (cold := tmp_path / "cold").mkdir()
    repo = runner_repo(tmp_path, extra=("llmenv.py", *(p.relative_to(ROOT).as_posix() for p in (ROOT / "pylib").glob("*.py"))))
    assert run_runner(repo, env={"STUB_REAL_OFFLINE": "1"}).returncode == 0  # warm cache: only a cold one fails
    (repo / "calls").unlink()
    result = run_runner(repo, env={"STUB_REAL_OFFLINE": "1", "UV_CACHE_DIR": str(cold)})
    assert result.returncode == 4 and "run make dev-setup once with network" in result.stderr and calls(repo) == []


@pytest.mark.parametrize(("env", "rc", "line"), [
    ({}, 0, None),                                                                   # pytest's temp root and tmp/keep/old.log are not leaks
    ({"STUB_TOUCH": "tmp/left.log"}, 1, "leak: tmp/left.log"),
    ({"STUB_TOUCH": "tmp/keep/new.log"}, 1, "leak: tmp/keep/new.log"),               # nested, under an existing dir
    ({"STUB_MKDIR_TMPDIR": "scratch"}, 1, "leak: TMPDIR/scratch"),
    ({"STUB_MKDIR_TMPDIR": "pytest-of-x"}, 1, "leak: TMPDIR/pytest-of-x"),           # no name-prefix exemption
    ({"STUB_MKDIR_TMPDIR": "uv-0123456789abcdef.lock"}, 1, "leak: TMPDIR/uv-0123456789abcdef.lock"),  # only uv's empty file is exempt
    ({"STUB_NEW_PODMAN": "image abc"}, 1, "leak: podman image abc"),                 # same ID as the container, other class
    ({"STUB_RC": "5", "STUB_TOUCH": "tmp/x"}, 1, "leak: tmp/x"),
    ({"STUB_RC": "3", "STUB_TOUCH": "tmp/x"}, 3, "leak: tmp/x"),                     # other statuses are kept
    ({"STUB_PODMAN_FAIL": "1"}, 1, "inventory: podman unavailable"),
    ({"LOCKED": "1"}, 1, "inventory: repo tmp/ unreadable")])                        # the test makes tmp/locked mode 000
def test_a_leak_turns_0_or_5_into_1_and_keeps_other_statuses(tmp_path, env, rc, line):
    (repo := runner_repo(tmp_path)).joinpath("podman-state").write_text("container abc\n")
    (locked := repo / "tmp/locked").mkdir(mode=0 if env.get("LOCKED") else 0o700)
    try:
        result = run_runner(repo, env=env)
    finally:
        locked.chmod(0o700)                                                         # so pytest can clean tmp_path
    assert result.returncode == rc and "leak: podman container" not in result.stderr
    assert line in result.stderr if line else "leak:" not in result.stderr
    assert (HINT in result.stderr) == (line or "").startswith("leak: tmp/")      # a concurrent tmp/ writer looks like a leak
    assert not run_dir(result.stderr).exists()                                       # this run's own TMPDIR is gone


def test_runner_drops_inherited_pytest_settings(tmp_path):                           # spec R10
    result = run_runner(repo := runner_repo(tmp_path), env={"PYTEST_ADDOPTS": "-k nothing", "PYTEST_DEBUG_TEMPROOT": str(tmp_path)})
    assert "addopts=unset" in calls(repo) and f"temproot={run_dir(result.stderr)}/pytest" in calls(repo)


def test_runner_skips_the_podman_diff_when_podman_is_absent(tmp_path):
    result = run_runner(runner_repo(tmp_path), podman=False)
    assert result.returncode == 0 and (result.stdout + result.stderr).count("podman not found") == 1


@pytest.mark.parametrize(("sig", "rc"), [(signal.SIGTERM, 143), (signal.SIGINT, 130)])
def test_a_signal_to_the_runner_reaps_every_descendant(tmp_path, sig, rc):
    proc = start_runner(repo := runner_repo(tmp_path), env={"STUB_SLEEP": "1"})
    wait_until(lambda: b"sleep\x00300\x00" in descendants(proc.pid).values())         # the stub's new-session child
    tree = descendants(proc.pid)
    proc.send_signal(sig)                                                           # the runner's PID only
    assert proc.wait(timeout=10) == rc and "term" in calls(repo)                     # forwarded, and waited for
    wait_until(lambda: not any(Path(f"/proc/{p}").exists() for p in tree), timeout=5)
    assert not run_dir(proc.communicate()[1].decode()).exists()


def test_runner_rejects_a_bad_timeout(tmp_path):
    for bad in ("0", "-1", "abc", "1.5", "", "010", "+5", " 5"):
        result = run_runner(repo := runner_repo(tmp_path / (bad[:8] or "empty")), env={"LLM_ENV_TEST_TIMEOUT_SECONDS": bad})
        assert result.returncode == 2 and "LLM_ENV_TEST_TIMEOUT_SECONDS" in result.stderr and calls(repo) == [], bad


def test_a_run_past_the_timeout_exits_124_and_reaps_every_descendant(tmp_path):  # a hung node once held a hook for 78 min
    proc = start_runner(runner_repo(tmp_path), env={"STUB_SLEEP": "1", "LLM_ENV_TEST_TIMEOUT_SECONDS": "2"})
    seen: dict[int, bytes] = {}
    try:
        deadline = time.monotonic() + 30
        while proc.poll() is None and time.monotonic() < deadline:                   # 2 s may expire before the stub starts
            seen |= descendants(proc.pid)
            time.sleep(0.05)
        err = proc.communicate(timeout=10)[1].decode()
    finally:
        stop_runner(proc, seen)
    assert proc.returncode == 124 and "timeout: test run exceeded 2 s" in err.splitlines()
    assert b"sleep\x00300\x00" in seen.values()                                      # the stub's sleeper existed to be reaped
    wait_until(lambda: not any(Path(f"/proc/{p}").exists() for p in seen), timeout=5)
    assert not run_dir(err).exists()


@pytest.mark.parametrize("rc", [0, 5])
def test_a_timer_expiring_after_the_chain_keeps_the_pytest_status(tmp_path, rc):
    podman = (repo := runner_repo(tmp_path)) / "stub/podman"                         # the second `ps` (snapshot after) outlasts the 2 s bound
    podman.write_text(podman.read_text().replace("bash\n", 'bash\nif [ "$1" = ps ]; then [ ! -e "$PODMAN_STATE.ps" ] || sleep 3; touch "$PODMAN_STATE.ps"; fi\n', 1))
    result = run_runner(repo, env={"STUB_RC": str(rc), "LLM_ENV_TEST_TIMEOUT_SECONDS": "2"})
    assert result.returncode == rc and "timeout:" not in result.stderr and "leak:" not in result.stderr
