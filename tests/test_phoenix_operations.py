"""Behavioral boundaries for offline metadata and real log-follower cleanup."""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import signal
import subprocess
import time

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]


def offline_check(tmp_path: pathlib.Path, mode: str = "owned") -> subprocess.CompletedProcess[str]:
    """Run the actual offline-check function; only the Podman boundary is replaced."""
    source = (ROOT / "scripts/check-setup.sh").read_text()
    start = source.index("phoenix_offline_check() {")
    end = source.index('\n}\n\nlog_step "System One"', start) + 2
    definition = source[start:end]
    base = "sha256:" + "a" * 64
    body = ((ROOT / "docker/system-one/observer.Dockerfile").read_bytes()
            + (ROOT / "docker/system-one/phoenix_observer.py").read_bytes())
    digest = hashlib.sha256(body).hexdigest()
    key = hashlib.sha256(f"{base}\n{digest}\n".encode()).hexdigest()[:12]
    config = tmp_path / "models.yml"
    config.write_text(yaml.safe_dump({"system_one": {"image": "localhost/fixture-base:rocm"}}))
    # The offline check asks `llmenv effective-policy`; the fixture stands in for its JSON.
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps({"cpus_min": 0.25,
                                  "cpus_max": 0 if mode == "invalid-resource-policy" else 2,
                                  "memory_min_mib": 256, "memory_max_mib": 1024}))
    rendered = tmp_path / "compose.yml"
    rendered.write_text(yaml.safe_dump({"services": {"system-one": {
        "image": f"localhost/llm-env-system-one-phoenix:{'stale' if mode == 'stale-image' else key}",
        "environment": {"LAYA_PHOENIX_URL": "http://phoenix:6006/v1/projects/laya/spans"},
        "networks": ["default", "phoenix-trace"]}},
        "networks": {"phoenix-trace": {"external": True, "name": "llm-env-phoenix_internal"}}}))
    commands = tmp_path / "bin"
    commands.mkdir()
    journal = tmp_path / "commands"
    journal.touch()
    podman = commands / "podman"
    podman.write_text("""#!/usr/bin/bash
printf '%s\\n' "$*" >> "$JOURNAL"
case "$*" in
 'image exists '*) [[ "$MODE" != missing-image ]] ;;
 'image inspect localhost/fixture-base:rocm --format {{.Id}}') printf '%s\\n' "$BASE" ;;
 'image inspect localhost/llm-env-system-one-phoenix:'*)
   value="$KEY"; [[ "$MODE" == stale-label ]] && value=wrong
   printf '[{"Labels":{"llm-env.phoenix-observer-sha256":"%s"}}]\\n' "$value" ;;
 'volume exists '*|'network exists '*) [[ "$MODE" != absent ]] ;;
 'network inspect --format {{.Internal}} '*) [[ "$MODE" == external-network ]] && echo false || echo true ;;
 'volume inspect --format '*|'network inspect --format '*)
   [[ "$MODE" == absent ]] && exit 1
   [[ "$MODE" == foreign ]] && echo '{}' || echo '{"llm-env.scope":"phoenix","llm-env.project":"llm-env-phoenix"}' ;;
 *) printf 'Unexpected external operation: %s\\n' "$*" >&2; exit 64 ;;
esac
""")
    podman.chmod(0o700)
    result = subprocess.run(["bash", "-c", 'llmenv() { cat "$POLICY_JSON"; }\n' + definition + "\nphoenix_offline_check\n"],
                            env=os.environ | {"PATH": f"{commands}:{os.environ['PATH']}",
                                              "REPO_DIR": str(ROOT), "CONFIG_PATH": str(config),
                                              "POLICY_JSON": str(policy),
                                              "COMPOSE_FILE": str(rendered), "BASE": base,
                                              "KEY": key, "MODE": mode, "JOURNAL": str(journal)},
                            text=True, capture_output=True, timeout=20, check=False)
    assert not any(word in journal.read_text().split() for word in ("pull", "build", "run", "up", "down"))
    return result


@pytest.mark.parametrize("mode", ["owned", "absent"])
def test_offline_phoenix_checks_accept_owned_or_not_yet_created_resources(tmp_path, mode):
    result = offline_check(tmp_path, mode)
    assert result.returncode == 0, result.stdout + result.stderr
    if mode == "absent":
        assert "SKIP" in result.stdout


@pytest.mark.parametrize("mode", ["foreign", "missing-image", "stale-image", "stale-label", "external-network", "invalid-resource-policy"])
def test_offline_phoenix_checks_reject_unsafe_or_stale_resources_with_actionable_stderr(tmp_path, mode):
    result = offline_check(tmp_path, mode)
    assert result.returncode != 0, result.stdout + result.stderr
    assert result.stderr.strip(), "metadata failure must identify a corrective action without exposing secrets"


@pytest.mark.parametrize("cgroup_path", ["", None, "/"])
def test_status_unknown_cgroup_and_validated_phoenix_endpoint(tmp_path, cgroup_path):
    commands = tmp_path / "bin"
    commands.mkdir()
    journal = tmp_path / "network"
    podman = commands / "podman"
    podman.write_text("""#!/usr/bin/bash
if [[ "$1" == container && "$2" == exists ]]; then exit 0; fi
if [[ "$1" == inspect ]]; then printf '%s\\n' "$INSPECT_JSON"; exit 0; fi
exit 0
""")
    podman.chmod(0o700)
    for name, body in {
        "systemctl": "#!/usr/bin/bash\nexit 0\n",
        "curl": "#!/usr/bin/bash\nprintf '%s\\n' \"$*\" >> \"$JOURNAL\"\nexit 7\n",
    }.items():
        path = commands / name
        path.write_text(body)
        path.chmod(0o700)

    def run(port: str):
        return subprocess.run(["bash", "scripts/status.sh"], cwd=ROOT,
                              env=os.environ | {"PATH": f"{commands}:{os.environ['PATH']}",
                                                "HOME": str(tmp_path), "TMPDIR": str(tmp_path),
                                                "JOURNAL": str(journal), "LLM_ENV_PHOENIX_PORT": port,
                                                "INSPECT_JSON": '[{"State":{' + ('' if cgroup_path is None else '"CgroupPath":' + json.dumps(cgroup_path)) + '}}]'},
                              text=True, capture_output=True, timeout=10, check=False)

    invalid = run("not-a-port")
    assert "cgroup memory.current=UNKNOWN" in invalid.stdout
    assert not journal.exists() or journal.read_text() == ""
    valid = run("6123")
    assert valid.returncode == 0, valid.stdout + valid.stderr
    assert "http://127.0.0.1:6123/readyz" in journal.read_text()


@pytest.mark.parametrize(("received_signal", "expected_exit"), [(signal.SIGINT, 130), (signal.SIGTERM, 143)])
def test_logs_follow_both_streams_and_reap_their_process_groups(tmp_path, received_signal, expected_exit):
    commands = tmp_path / "bin"
    commands.mkdir()
    journal = tmp_path / "pids"
    journal.touch()
    podman = commands / "podman"
    podman.write_text("""#!/usr/bin/bash
if [[ "$1" == ps ]]; then echo fixture-phoenix-id; exit 0; fi
case "$1" in compose) label=main ;; logs) label=phoenix ;; *) exit 64 ;; esac
sleep 60 & child=$!
printf '%s %s %s\\n' "$label" "$$" "$child" >> "$JOURNAL"
printf 'owned-%s-fixture-log\\n' "$label"
trap 'wait "$child" 2>/dev/null; exit 143' TERM
wait "$child"
""")
    podman.chmod(0o700)
    output = tmp_path / "logs"
    with output.open("w") as stream:
        proc = subprocess.Popen(["bash", "scripts/logs.sh"], cwd=ROOT,
                                env=os.environ | {"PATH": f"{commands}:{os.environ['PATH']}",
                                                  "HOME": str(tmp_path), "JOURNAL": str(journal)},
                                stdout=stream, stderr=stream)
        groups: list[int] = []
        try:
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline and len(journal.read_text().splitlines()) != 2:
                assert proc.poll() is None, output.read_text()
                time.sleep(0.02)
            rows = [row.split() for row in journal.read_text().splitlines()]
            assert {row[0] for row in rows} == {"main", "phoenix"}, output.read_text()
            groups = [int(row[1]) for row in rows]
            proc.send_signal(received_signal)
            assert proc.wait(timeout=5) == expected_exit
            for row in rows:
                for pid in map(int, row[1:]):
                    state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)],
                                           text=True, capture_output=True, check=False).stdout.strip()
                    assert not state or state.startswith("Z"), f"owned follower still live: {pid} {state}"
            assert "owned-main-fixture-log" in output.read_text()
            assert "owned-phoenix-fixture-log" in output.read_text()
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)
                for group in groups:
                    try:
                        os.killpg(group, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
