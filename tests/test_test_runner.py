"""tools/test.sh: capped workers, niced, quiet, offline in its own namespaces, and no opt-in lane leaks into the default suite."""
import os
import re

import pytest
from runner_helpers import OPT_IN_SWITCHES, ROOT, calls, run_runner, runner_repo


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
    repo = runner_repo(tmp_path, extra=("llmenv.py",))
    result = run_runner(repo, env={"STUB_REAL_OFFLINE": "1", "UV_CACHE_DIR": str(cold)})
    assert result.returncode == 4 and "run make dev-setup once with network" in result.stderr and calls(repo) == []
