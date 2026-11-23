"""Regression tests for shell operations retained after retired features."""
from __future__ import annotations

import hashlib
import os
import pathlib
import signal
import subprocess
import time

import pytest
import yaml
from signal_helpers import default_signals

ROOT = pathlib.Path(__file__).resolve().parents[1]


def span_check(tmp_path: pathlib.Path, mode: str) -> subprocess.CompletedProcess[str]:
    source = (ROOT / "scripts/check-setup.sh").read_text()
    start = source.index("laya_span_observer_check() {")
    end = source.index("\n}\n", start) + 2
    definition = source[start:end]
    base = "sha256:" + "a" * 64
    body = ((ROOT / "docker/system-one/observer.Dockerfile").read_bytes()
            + (ROOT / "docker/system-one/laya_span_observer.py").read_bytes())
    key = hashlib.sha256(f"{base}\n{hashlib.sha256(body).hexdigest()}\n".encode()).hexdigest()[:12]
    config = tmp_path / "models.yml"
    config.write_text(yaml.safe_dump({"system_one": {"image": "localhost/fixture-base:rocm"}}))
    rendered = tmp_path / "compose.yml"
    rendered.write_text(yaml.safe_dump({"services": {"system-one": {
        "image": f"localhost/llm-env-system-one-span-sink:{'stale' if mode == 'stale-image' else key}",
        "environment": {"LAYA_SPAN_SINK": "/var/log/laya/laya-spans.jsonl"},
        # The renderer writes the expanded home (pylib/compose.py), never a literal ${HOME}.
        "volumes": [] if mode == "missing-sink-bind" else [f"{tmp_path / 'home'}/.local/state/llm-env/laya-spans:/var/log/laya:Z"],
        "networks": ["default"]}}}))
    commands = tmp_path / "bin"
    commands.mkdir()
    podman = commands / "podman"
    podman.write_text("""#!/usr/bin/bash
case "$*" in
 'image inspect localhost/fixture-base:rocm --format {{.Id}}') echo "$BASE" ;;
 'image inspect localhost/llm-env-system-one-span-sink:'*)
   value="$KEY"; [[ "$MODE" == stale-label ]] && value=wrong
   printf '[{"Labels":{"llm-env.span-observer-sha256":"%s"}}]\\n' "$value" ;;
 *) exit 64 ;;
esac
""")
    podman.chmod(0o700)
    result = subprocess.run(["bash", "-c", 'llmenv() { echo \'{"cpus_min":0.25,"cpus_max":2,"memory_min_mib":256,"memory_max_mib":1024}\'; }\n' + definition + "\nlaya_span_observer_check\n"],
        env=os.environ | {"PATH": f"{commands}:{os.environ['PATH']}", "REPO_DIR": str(ROOT), "HOME": str(tmp_path / "home"),
                          "CONFIG_PATH": str(config), "COMPOSE_FILE": str(rendered),
                          "BASE": "sha256:" + "a" * 64, "KEY": key, "MODE": mode},
        text=True, capture_output=True, timeout=20, check=False)
    return result


def test_check_setup_offline_run_fails_and_names_stale_span_observer(tmp_path):
    result, _calls, _models = __import__("test_shell").run_check_setup_with_stubs(
        tmp_path, llm_server_enabled=True, system_one_enabled=True,
        stale_span_observer=True)
    assert result.returncode != 0, result.stdout + result.stderr
    assert "System One span observer" in result.stdout + result.stderr


@pytest.mark.parametrize("mode", ["owned", "absent"])
def test_check_setup_span_observer_freshness_accepts_owned_or_absent_resources(tmp_path, mode):
    result = span_check(tmp_path, mode)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("mode", ["stale-image", "stale-label", "missing-sink-bind"])
def test_check_setup_span_observer_freshness_rejects_stale_or_missing_sink(tmp_path, mode):
    result = span_check(tmp_path, mode)
    assert result.returncode != 0 and result.stderr.strip()
    if mode == "missing-sink-bind":
        assert "System One observer sink bind is missing" in result.stderr


def test_status_reports_unknown_cgroup_memory_without_reading_counters(tmp_path):
    commands = tmp_path / "bin"
    commands.mkdir()
    podman = commands / "podman"
    podman.write_text("#!/usr/bin/bash\nif [[ $1 == container && $2 == exists ]]; then exit 0; fi\nif [[ $1 == inspect ]]; then echo '[{\"State\":{\"CgroupPath\":\"\"}}]'; exit 0; fi\nexit 0\n")
    podman.chmod(0o700)
    for name in ("systemctl", "curl"):
        path = commands / name
        path.write_text("#!/usr/bin/bash\nexit 0\n")
        path.chmod(0o700)
    result = subprocess.run(["bash", "scripts/status.sh"], cwd=ROOT,
        env=os.environ | {"PATH": f"{commands}:{os.environ['PATH']}", "HOME": str(tmp_path), "TMPDIR": str(tmp_path)},
        text=True, capture_output=True, timeout=10, check=False)
    assert "cgroup memory.current=UNKNOWN" in result.stdout


@pytest.mark.parametrize(("received_signal", "expected_exit"), [(signal.SIGINT, 130), (signal.SIGTERM, 143)])
def test_logs_signal_reaps_owned_follower_process_group(tmp_path, received_signal, expected_exit):
    commands = tmp_path / "bin"
    commands.mkdir()
    journal = tmp_path / "pids"
    podman = commands / "podman"
    podman.write_text("""#!/usr/bin/bash
case "$1" in compose) sleep 60 & child=$!; echo "$$ $child" >> "$JOURNAL"; trap 'wait "$child" 2>/dev/null; exit 143' TERM; wait "$child" ;; *) exit 64 ;; esac
""")
    podman.chmod(0o700)
    output = tmp_path / "logs"
    with output.open("w") as stream:
        proc = subprocess.Popen(default_signals(["bash", "scripts/logs.sh"]), cwd=ROOT,
            env=os.environ | {"PATH": f"{commands}:{os.environ['PATH']}", "HOME": str(tmp_path), "JOURNAL": str(journal)},
            stdout=stream, stderr=stream)
        try:
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline and not journal.exists():
                assert proc.poll() is None, output.read_text()
                time.sleep(.02)
            assert journal.exists()
            pids = list(map(int, journal.read_text().split()))
            proc.send_signal(received_signal)
            follower_pid = pids[-1]
            deadline = time.monotonic() + 3
            state = ""
            while time.monotonic() < deadline:
                state = subprocess.run(["ps", "-o", "stat=", "-p", str(follower_pid)], text=True, capture_output=True, check=False).stdout.strip()
                if not state or state.startswith("Z"):
                    break
                time.sleep(.02)
            follower_reaped = not state or state.startswith("Z")
            if not follower_reaped:
                os.kill(follower_pid, signal.SIGTERM)
            assert proc.wait(timeout=5) == expected_exit
            assert follower_reaped, f"owned follower still live after signal: {follower_pid} {state}"
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)


def test_start_prepares_laya_span_directory_before_starting_compose(tmp_path):
    compose = tmp_path / "home/.config/llm-env/docker-compose.yml"
    compose.parent.mkdir(parents=True)
    compose.write_text("services: {}\\n")
    result, _config, calls = __import__("test_shell").run_lifecycle_script(
        tmp_path, "scripts/start.sh", system_one_enabled=True, active=True,
        llm_server_enabled=False, env_overrides={"LLM_ENV_BOOT_UNIT": "1"})
    assert result.returncode == 0, result.stdout + result.stderr
    actions = calls.read_text().splitlines()
    prepare = next(i for i, line in enumerate(actions) if "prepare-laya-spans.sh" in line)
    compose = next(i for i, line in enumerate(actions) if "podman compose" in line and " up " in line)
    assert prepare < compose

