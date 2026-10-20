"""Serialized, explicitly opted-in rootless Phoenix acceptance tests.

Collection and the default test suite perform no engine, HTTP, or port probes.
"""

from __future__ import annotations

import datetime
import fcntl
import hashlib
import json
import os
import pathlib
import socket
import subprocess
import uuid
from collections.abc import Iterator

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
OPT_IN = os.environ.get("LLM_ENV_TEST_PHOENIX_TRIAL") == "1"


def _write_evidence(path: pathlib.Path, evidence: dict[str, object]) -> None:
    path.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    path.chmod(0o600)


def test_evidence_writer_round_trips_complete_saved_bytes(tmp_path: pathlib.Path) -> None:
    evidence = {"nested": {"count": 13}, "items": [1, 2, 3]}
    path = tmp_path / "evidence.json"
    _write_evidence(path, evidence)
    saved_bytes = path.read_bytes()
    assert saved_bytes.endswith(b"\n")
    assert json.loads(saved_bytes.decode("utf-8")) == evidence


def test_phoenix_trial_integration_is_opt_in() -> None:
    """The no-opt-in path is intentionally side-effect-free and collection-safe."""
    if not OPT_IN:
        assert os.environ.get("LLM_ENV_TEST_PHOENIX_TRIAL") != "1"
        return
    assert OPT_IN


def _run(args: list[str], *, env: dict[str, str], timeout: int = 180) -> str:
    result = subprocess.run(
        args,
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    assert result.returncode == 0, f"{args!r} failed ({result.returncode}):\n{result.stdout}\n{result.stderr}"
    return result.stdout


def _production_hashes() -> dict[str, str | None]:
    candidates = (
        ROOT / "models.yml",
        ROOT / ".env",
        ROOT / "docker-compose.yml",
        ROOT / "tmp" / "docker-compose.yml",
    )
    return {
        str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
        if path.is_file()
        else None
        for path in candidates
    }


def _stats_sample(env: dict[str, str], container_id: str, phase: str) -> dict[str, str | int]:
    result = subprocess.run(
        ["podman", "stats", "--no-stream", "--format", "json", container_id],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    return {
        "phase": phase,
        "sampled_at": datetime.datetime.now(datetime.UTC).isoformat(),
        "command": "podman stats --no-stream --format json <owned-container-id>",
        "returncode": result.returncode,
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
    }


def _nontrial_containers(env: dict[str, str]) -> dict[str, str]:
    ids = _run(["podman", "ps", "-aq"], env=env).split()
    result: dict[str, str] = {}
    for container_id in ids:
        raw = _run(
            [
                "podman",
                "inspect",
                "--format",
                '{{.Id}} {{index .Config.Labels "llm-env.scope"}} {{.State.StartedAt}}',
                container_id,
            ],
            env=env,
        ).strip()
        inspected_id, scope, started = raw.split(maxsplit=2)
        if scope != "phoenix-trial":
            result[inspected_id] = started
    return result


def _private_env() -> dict[str, str]:
    runtime_tmp = ROOT / "tmp" / "phoenix-trial-implementation" / "2026-10-19-main" / "runtime-tmp"
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(runtime_tmp / "integration-home"),
        "XDG_STATE_HOME": str(runtime_tmp / "integration-state"),
        "TMPDIR": str(ROOT / "tmp" / "phoenix-trial-implementation" / "2026-10-19-main" / "runtime-tmp"),
        "LLM_ENV_TEST_PHOENIX_TRIAL": "1",
    }
    for optional in ("XDG_RUNTIME_DIR", "LANG", "LC_ALL", "TERM"):
        if optional in os.environ:
            env[optional] = os.environ[optional]
    pathlib.Path(env["HOME"]).mkdir(parents=True, exist_ok=True, mode=0o700)
    pathlib.Path(env["HOME"]).chmod(0o700)
    pathlib.Path(env["XDG_STATE_HOME"]).mkdir(parents=True, exist_ok=True, mode=0o700)
    pathlib.Path(env["TMPDIR"]).mkdir(parents=True, exist_ok=True)
    return env


@pytest.fixture
def trial_environment(tmp_path: pathlib.Path) -> Iterator[dict[str, str]]:
    if "PYTEST_XDIST_WORKER" in os.environ:
        pytest.fail(
            "Phoenix trial integration is serialized; rerun exactly with "
            "`LLM_ENV_TEST_PHOENIX_TRIAL=1 uv run pytest tests/test_phoenix_trial_integration.py -n 0 -v`"
        )
    operator_state = pathlib.Path(
        os.environ.get("XDG_STATE_HOME", str(pathlib.Path.home() / ".local/state"))
    )
    if not operator_state.is_absolute():
        pytest.fail("XDG_STATE_HOME must be absolute for Phoenix integration lock")
    operator_state = operator_state.resolve()
    lock_path = operator_state / "llm-env" / "phoenix-trial" / "integration.lock"
    for parent in reversed((lock_path, *lock_path.parents)):
        if parent.exists() and parent.is_symlink():
            pytest.fail(f"refusing symlink integration lock path: {parent}")
    lock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    for parent in reversed((lock_path, *lock_path.parents)):
        if parent.exists() and parent.is_symlink():
            pytest.fail(f"refusing symlink integration lock path: {parent}")
    lock_handle = lock_path.open("a", encoding="utf-8")
    try:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock_handle.close()
        pytest.fail(f"Phoenix integration suite lock is held: {lock_path}")

    env = _private_env()
    token = uuid.uuid4().hex[:12]
    env["LLM_ENV_PHOENIX_TRIAL_PROJECT"] = f"llm-env-phoenix-trial-it-{token}"
    env["XDG_STATE_HOME"] = str(
        pathlib.Path(env["TMPDIR"]) / "integration-state" / env["LLM_ENV_PHOENIX_TRIAL_PROJECT"]
    )
    pathlib.Path(env["XDG_STATE_HOME"]).mkdir(parents=True, exist_ok=True, mode=0o700)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        env["LLM_ENV_PHOENIX_PORT"] = str(probe.getsockname()[1])
    env["LLM_ENV_PHOENIX_RETENTION_DAYS"] = "7"
    try:
        yield env
    finally:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        lock_handle.close()


@pytest.mark.skipif(not OPT_IN, reason="set LLM_ENV_TEST_PHOENIX_TRIAL=1 for the serialized live trial")
def test_phoenix_trial_exact_persistence_and_noninterference(
    trial_environment: dict[str, str],
) -> None:
    env = trial_environment
    project = env["LLM_ENV_PHOENIX_TRIAL_PROJECT"]
    volume = f"{project}-data"
    compose = f"{project}_trial"
    rootless = _run(["podman", "info", "--format", "{{.Host.Security.Rootless}}"], env=env).strip()
    assert rootless == "true", "Phoenix trial acceptance requires rootless Podman"
    compose_env = env | {
        "PHOENIX_TRIAL_PROJECT": project,
        "PHOENIX_TRIAL_VOLUME": volume,
        "PHOENIX_TRIAL_PORT": env["LLM_ENV_PHOENIX_PORT"],
        "PHOENIX_TRIAL_RETENTION_DAYS": "7",
        "PHOENIX_TRIAL_SENDER_IMAGE": f"localhost/{project}-sender:trial",
    }
    config = _run(
        ["podman", "compose", "--env-file", "/dev/null", "-f",
         str(ROOT / "docker/phoenix-trial/compose.yml"), "-p", project, "config"],
        env=compose_env,
    )
    assert "internal: true" in config
    assert f"127.0.0.1:{env['LLM_ENV_PHOENIX_PORT']}:6006" in config
    before_hashes = _production_hashes()
    before_containers = _nontrial_containers(env)

    _run(["bash", "scripts/phoenix-trial.sh", "up"], env=env, timeout=900)
    server_id = _run(
        ["podman", "ps", "-q", "--filter", f"label=com.docker.compose.project={project}", "--filter", "label=com.docker.compose.service=phoenix"],
        env=env,
    ).strip()
    assert server_id
    server_before = json.loads(
        _run(["podman", "inspect", server_id], env=env)
    )[0]
    network = json.loads(_run(["podman", "network", "inspect", compose], env=env))[0]
    assert network["internal"] is True
    assert network["labels"]["llm-env.scope"] == "phoenix-trial"
    assert network["labels"]["llm-env.project"] == project
    assert server_before["Config"]["Labels"]["llm-env.scope"] == "phoenix-trial"
    assert server_before["Config"]["Labels"]["llm-env.project"] == project
    assert server_before["Config"]["Image"].endswith("@sha256:c542e7de1dc1fdd0f938de337d44ebefa7266e91391eed6e7978523d7f6e1f2f")
    assert server_before["Mounts"] and [m["Name"] for m in server_before["Mounts"] if m["Destination"] == "/mnt/data"] == [volume]
    bindings = server_before["HostConfig"]["PortBindings"]
    assert bindings == {"6006/tcp": [{"HostIp": "127.0.0.1", "HostPort": env["LLM_ENV_PHOENIX_PORT"]}]}
    assert set(server_before["NetworkSettings"]["Networks"]) == {compose}
    environment = dict(item.split("=", maxsplit=1) for item in server_before["Config"]["Env"])
    phoenix_environment = {key: value for key, value in environment.items() if key.startswith("PHOENIX_")}
    wasm_path = phoenix_environment.pop("PHOENIX_WASM_BINARY_PATH")
    assert wasm_path.startswith("/")
    assert phoenix_environment == {
        "PHOENIX_HOST": "0.0.0.0",
        "PHOENIX_PORT": "6006",
        "PHOENIX_WORKING_DIR": "/mnt/data",
        "PHOENIX_DEFAULT_RETENTION_POLICY_DAYS": "7",
        "PHOENIX_ALLOW_EXTERNAL_RESOURCES": "false",
        "PHOENIX_TELEMETRY_ENABLED": "false",
        "PHOENIX_ALLOWED_PROVIDERS": "NONE",
        "PHOENIX_ALLOWED_SANDBOX_PROVIDERS": "NONE",
        "PHOENIX_ENABLE_MCP_SERVER": "false",
        "PHOENIX_ENABLE_PROMETHEUS": "false",
        "PHOENIX_ENABLE_AUTH": "false",
    }
    assert set(environment) <= {
        "HOME", "HOSTNAME", "LANG", "PATH", "PYTHONPATH", "PYTHONUNBUFFERED",
        "SSL_CERT_FILE", "container", "PHOENIX_WASM_BINARY_PATH", *phoenix_environment,
    }
    assert not any(token in key.upper() for key in environment for token in ("API_KEY", "TOKEN", "PASSWORD", "SECRET"))
    assert not server_before["HostConfig"].get("Devices")
    assert all(m["Type"] == "volume" for m in server_before["Mounts"])
    sender_image = f"localhost/{project}-sender:trial"
    routes = _run(
        ["podman", "run", "--rm", "--network", compose, "--entrypoint", "/bin/sh",
         sender_image, "-c", "cat /proc/net/route; cat /proc/net/ipv6_route"],
        env=env,
    )
    route_lines = routes.splitlines()
    default_v4 = [line for line in route_lines if len(line.split()) > 1 and line.split()[1] == "00000000"]
    # /proc/net/ipv6_route includes a loopback ::/0 entry on this kernel;
    # only a default route attached to a non-loopback interface is egress.
    default_v6 = [
        line for line in route_lines
        if len(line.split()) >= 10
        and line.split()[0] == "0" * 32
        and line.split()[1] == "00"
        and line.split()[-1] != "lo"
    ]
    assert not default_v4 and not default_v6, f"sender has an external default route:\\n{routes}"

    stats_samples = [_stats_sample(env, server_id, "before-seed")]
    up_samples = list(
        pathlib.Path(env["XDG_STATE_HOME"], "llm-env", "phoenix-trial", project, "resource-samples").glob("*.json")
    )
    assert len(up_samples) == 1
    assert json.loads(up_samples[0].read_text())["phase"] == "before-seed"

    seed_result = subprocess.run(
        ["bash", "scripts/phoenix-trial.sh", "seed"], cwd=ROOT, env=env,
        capture_output=True, text=True, timeout=300, check=False
    )
    run_dirs = sorted(
        pathlib.Path(env["XDG_STATE_HOME"], "llm-env", "phoenix-trial", project, "runs").iterdir()
    )
    assert len(run_dirs) == 1
    expected = json.loads((run_dirs[0] / "expected.json").read_text())
    assert (run_dirs[0] / "attempt.json").is_file()
    assert seed_result.returncode == 0 or "outcome is uncertain" in seed_result.stderr, (
        f"Phoenix seed failed before its documented uncertain-outcome recovery:\n"
        f"{seed_result.stdout}\n{seed_result.stderr}"
    )
    receipt = json.loads(
        _run(
            ["uv", "run", "llmenv.py", "phoenix-trial", "verify", "--base-url", f"http://127.0.0.1:{env['LLM_ENV_PHOENIX_PORT']}", "--run-id", expected["run_id"], "--expected", str(run_dirs[0] / "expected.json")],
            env=env,
        )
    )
    assert receipt["run_id"] == expected["run_id"]
    assert receipt["summary"] == expected["summary"]
    assert receipt["verified_span_ids"] == sorted(s["context"]["span_id"] for s in expected["spans"])
    stats_samples.append(_stats_sample(env, server_id, "after-seed"))

    _run(["bash", "scripts/phoenix-trial.sh", "verify-persistence"], env=env, timeout=300)
    server_id_after = _run(
        ["podman", "ps", "-q", "--filter", f"label=com.docker.compose.project={project}", "--filter", "label=com.docker.compose.service=phoenix"],
        env=env,
    ).strip()
    assert server_id_after and server_id_after != server_id
    server_after = json.loads(_run(["podman", "inspect", server_id_after], env=env))[0]
    assert server_after["Image"] == server_before["Image"]
    assert [m["Name"] for m in server_after["Mounts"] if m["Destination"] == "/mnt/data"] == [volume]
    stats_samples.append(_stats_sample(env, server_id_after, "after-recreation"))
    samples = [json.loads(path.read_text()) for path in sorted((run_dirs[0].parent.parent / "resource-samples").glob("*.json"))]
    required_sample_phases = {"before-seed", "before-recreation", "after-recreation"}
    if seed_result.returncode == 0:
        required_sample_phases.add("after-seed")
    assert {sample["phase"] for sample in samples} >= required_sample_phases
    assert {sample["volume"] for sample in samples} == {volume}

    _run(["bash", "scripts/phoenix-trial.sh", "stop"], env=env, timeout=180)
    volume_info = json.loads(_run(["podman", "volume", "inspect", volume], env=env))[0]
    assert volume_info["Labels"]["llm-env.scope"] == "phoenix-trial"
    assert volume_info["Labels"]["llm-env.project"] == project
    after_hashes = _production_hashes()
    after_containers = _nontrial_containers(env)
    assert after_hashes == before_hashes
    assert after_containers == before_containers
    evidence_dir = pathlib.Path(env["TMPDIR"]) / "integration-evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    evidence_path = evidence_dir / f"{project}.json"
    evidence = {
        "project": project,
        "volume": volume,
        "server_container_before": server_id,
        "server_container_after": server_id_after,
        "server_image_id": server_before["Image"],
        "server_started_before": server_before["State"]["StartedAt"],
        "server_started_after": server_after["State"]["StartedAt"],
        "run_id": expected["run_id"],
        "seed_command_exit_code": seed_result.returncode,
        "seed_recovery_get_only": seed_result.returncode != 0,
        "after_seed_volume_sample_present": any(sample["phase"] == "after-seed" for sample in samples),
        "verified_span_ids": receipt["verified_span_ids"],
        "summary": receipt["summary"],
        "rootless_podman": rootless,
        "compose_config_network_internal": "internal: true" in config,
        "compose_config_loopback_port": f"127.0.0.1:{env['LLM_ENV_PHOENIX_PORT']}:6006" in config,
        "network_internal": network["internal"],
        "host_port_bindings": bindings,
        "sender_route_tables": routes,
        "production_hashes_before": before_hashes,
        "production_hashes_after": after_hashes,
        "nontrial_container_count_before": len(before_containers),
        "nontrial_container_count_after": len(after_containers),
        "volume_samples": samples,
        "server_stats_samples": stats_samples,
    }
    _write_evidence(evidence_path, evidence)


@pytest.mark.skipif(not OPT_IN, reason="set LLM_ENV_TEST_PHOENIX_TRIAL=1 for the serialized live trial")
def test_phoenix_trial_rejects_busy_port_before_resource_creation(
    trial_environment: dict[str, str],
) -> None:
    env = trial_environment
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as occupied:
        occupied.bind(("127.0.0.1", int(env["LLM_ENV_PHOENIX_PORT"])))
        occupied.listen()
        result = subprocess.run(
            ["bash", "scripts/phoenix-trial.sh", "up"], cwd=ROOT, env=env,
            capture_output=True, text=True, timeout=30, check=False
        )
    assert result.returncode != 0
    assert "already in use" in result.stderr
    volume_result = subprocess.run(
        ["podman", "volume", "inspect", f"{env['LLM_ENV_PHOENIX_TRIAL_PROJECT']}-data"],
        env=env, capture_output=True, text=True, check=False
    )
    assert volume_result.returncode != 0


@pytest.mark.skipif(not OPT_IN, reason="set LLM_ENV_TEST_PHOENIX_TRIAL=1 for the serialized live trial")
def test_phoenix_trial_refuses_unowned_volume_without_adopting_it(
    trial_environment: dict[str, str],
) -> None:
    env = trial_environment
    project = env["LLM_ENV_PHOENIX_TRIAL_PROJECT"]
    volume = f"{project}-data"
    _run(["podman", "volume", "create", "--label", "llm-env.scope=other", "--label", "llm-env.project=other", volume], env=env)
    result = subprocess.run(
        ["bash", "scripts/phoenix-trial.sh", "up"], cwd=ROOT, env=env,
        capture_output=True, text=True, timeout=30, check=False
    )
    assert result.returncode != 0
    assert "unowned Phoenix trial volume" in result.stderr
    info = json.loads(_run(["podman", "volume", "inspect", volume], env=env))[0]
    assert info["Labels"]["llm-env.scope"] == "other"
    assert info["Labels"]["llm-env.project"] == "other"
