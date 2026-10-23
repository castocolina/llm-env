"""Opt-in end-to-end proof of the updater and migration against a disposable Podman backend.

Run with: LLM_ENV_UPDATE_PROOF=1 uv run pytest tests/test_omniroute_update_proof.py -v -s
"""
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from omniroute_fakes import PASSWORD, free_port, profile_entry
from omniroute_world import make_repo_copy
from proof_backend import (
    BASE_IMAGE,
    REGISTRY_IMAGE,
    ProofBackend,
    ProofSkip,
)

pytestmark = pytest.mark.skipif(os.environ.get("LLM_ENV_UPDATE_PROOF") != "1",
                                reason="opt-in: LLM_ENV_UPDATE_PROOF=1 (needs podman, podman-compose, skopeo, journald)")


@pytest.fixture(scope="module")
def backend():
    b = ProofBackend()
    try:
        b.assert_pristine()
        b.import_host_image(BASE_IMAGE)
        b.import_host_image(REGISTRY_IMAGE)
        b.start_registry()
        b.old_id, b.new_id = b.build("old"), b.build("new")
        assert b.old_id != b.new_id
        b.shim = b.install_shims()
    except ProofSkip as exc:
        b.close()
        pytest.skip(str(exc))
    except BaseException:
        b.close()
        raise
    yield b
    b.close()


def fixture_service(backend, name, port, *, depends_on=False, volume=None):
    svc = {"image": backend.ref, "container_name": name,
           "command": ["--host", "0.0.0.0", "--port", str(port), "--state", "/app/state.json"],
           "ports": [f"0.0.0.0:{port}:{port}"], "stop_grace_period": "10s",
           "environment": {"PORT": str(port), "INITIAL_PASSWORD": PASSWORD, "REQUIRE_API_KEY": "true",
                           "NODE_ENV": "production"},
           "healthcheck": {"test": ["CMD", "python3", "-c",
                                    f"import urllib.request; urllib.request.urlopen('http://127.0.0.1:{port}/api/monitoring/health', timeout=2)"],
                           "interval": "2s", "timeout": "3s", "retries": 15}}
    if volume:
        svc["volumes"] = [f"{volume}:/app/data"]
    if depends_on:
        svc["depends_on"] = {"omniroute": {"condition": "service_healthy"}}
    return svc


def write_home(tmp_path, backend, port, services, volumes):
    cfg = tmp_path / "home/.config/llm-env"
    cfg.mkdir(parents=True)
    containers_cfg = tmp_path / "home/.config/containers"
    containers_cfg.mkdir(parents=True, exist_ok=True)
    (containers_cfg / "registries.conf").write_text(backend.home_registries_conf.read_text())
    (cfg / "models.yml").write_text(yaml.safe_dump({"version": 1, "omniroute": {
        "port": port, "image": backend.ref, "initial_password": PASSWORD}}))
    (cfg / "models.yml").chmod(0o600)
    compose = cfg / "docker-compose.yml"
    compose.write_text(yaml.safe_dump({"services": services, "volumes": {v: {} for v in volumes}}))
    return tmp_path / "home", compose


def script_env(backend, home):
    base = {k: v for k, v in os.environ.items() if not k.startswith(("COMPOSE_", "PODMAN_COMPOSE_", "CONTAINER_"))}
    return {**base, **backend.env(), "HOME": str(home), "PATH": f"{backend.shim}:{base['PATH']}",
            "UV_PROJECT_ENVIRONMENT": str(REPO / ".venv"),
            "UV_CACHE_DIR": os.environ.get("UV_CACHE_DIR", str(Path.home() / ".cache/uv")),
            "LLM_ENV_UPDATE_IDLE_SECONDS": "10", "LLM_ENV_UPDATE_MAX_WAIT_SECONDS": "180"}


def run_script(backend, tmp_path, home, script):
    repo = make_repo_copy(tmp_path, [profile_entry(config_id=backend.old_id,
                                                   entrypoint=["python3", "/app/fake_omniroute.py"],
                                                   env_allowlist={"NODE_ENV": "production"})])
    backend.clear_argv_log()
    with backend.health_pump():
        return subprocess.run(["bash", str(repo / "scripts" / script)], env=script_env(backend, home),
                              capture_output=True, text=True, timeout=600, check=False)


FORBIDDEN = {"--force", "--depend", "--remove-orphans", "--volumes", "-v", "down"}


def test_update_replaces_only_omniroute_and_is_idempotent(backend, tmp_path):
    backend.reset_deployment()
    backend.publish("old")
    port = free_port()
    home, compose = write_home(tmp_path, backend, port,
                               {"omniroute": fixture_service(backend, "omniroute", port, volume="omniroute-data")},
                               ["omniroute-data"])
    backend.deploy(compose)
    assert backend.image_of("omniroute") == backend.old_id, "push/pull changed the config id; the fixture must be built and pushed as OCI"
    before = backend.snapshot()
    backend.publish("new")                                              # the mutable tag now resolves to a different config id
    result = run_script(backend, tmp_path, home, "update-omniroute.sh")
    assert result.returncode == 0, result.stdout + result.stderr
    # Ruling: the real podman-compose prints pull/create progress to the inherited stdout, so the
    # updater's own verdict is the LAST line (the stub world stays exact in tests/test_update_omniroute_script.py).
    assert re.fullmatch(r"updated [0-9a-f]{12} -> [0-9a-f]{12}", result.stdout.strip().splitlines()[-1])
    after = backend.snapshot()
    assert backend.image_of("omniroute") == backend.new_id
    assert after["containers"]["omniroute"] != before["containers"]["omniroute"]
    assert {k: v for k, v in after["containers"].items() if k != "omniroute"} == \
        {k: v for k, v in before["containers"].items() if k != "omniroute"}
    assert after["volumes"] == before["volumes"] and after["networks"] == before["networks"]
    calls = backend.recorded_argv()
    assert not any(FORBIDDEN & set(call) for call in calls)
    again = run_script(backend, tmp_path, home, "update-omniroute.sh")        # a second run finds nothing to do
    assert again.returncode == 0 and again.stdout.strip().splitlines()[-1] == "unchanged"


def test_dependent_container_defers_and_nothing_changes(backend, tmp_path):
    backend.reset_deployment()
    backend.publish("old")
    port, port2 = free_port(), free_port()
    services = {"omniroute": fixture_service(backend, "omniroute", port, volume="omniroute-data"),
                "dependent": fixture_service(backend, "dependent", port2, depends_on=True)}
    home, compose = write_home(tmp_path, backend, port, services, ["omniroute-data"])
    backend.deploy(compose)
    before = backend.snapshot()
    backend.publish("new")
    result = run_script(backend, tmp_path, home, "update-omniroute.sh")
    assert result.returncode == 75 and result.stdout == "deferred: ownership\n", result.stdout + result.stderr
    assert backend.snapshot() == before
    assert backend.image_of("omniroute") == backend.old_id
    assert backend.podman("image", "inspect", backend.ref, "--format", "{{.Id}}").stdout.strip() == backend.old_id.removeprefix("sha256:")
    assert not any(call[1:2] in (["pull"], ["tag"], ["stop"], ["rm"]) for call in backend.recorded_argv())


def test_migration_clears_only_the_remote_setup_dependency(backend, tmp_path):
    backend.reset_deployment()
    backend.publish("old")
    port, port2 = free_port(), free_port()
    with_edge = {"omniroute": fixture_service(backend, "omniroute", port, volume="omniroute-data"),
                 "remote-setup": fixture_service(backend, "remote-setup", port2, depends_on=True,
                                                 volume="remote-setup-data")}
    home, compose = write_home(tmp_path, backend, port, with_edge, ["omniroute-data", "remote-setup-data"])
    backend.deploy(compose)
    assert backend.podman("inspect", "remote-setup", "--format", "{{json .Dependencies}}").stdout.strip() != "[]"
    before = backend.snapshot()
    deployed = {"omniroute": with_edge["omniroute"],
                "remote-setup": {k: v for k, v in with_edge["remote-setup"].items() if k != "depends_on"}}
    compose.write_text(yaml.safe_dump({"services": deployed,
                                       "volumes": {"omniroute-data": {}, "remote-setup-data": {}}}))   # the "re-rendered" file
    result = run_script(backend, tmp_path, home, "migrate-remote-setup.sh")
    assert result.returncode == 0, result.stdout + result.stderr
    after = backend.snapshot()
    assert backend.podman("inspect", "remote-setup", "--format", "{{json .Dependencies}}").stdout.strip() == "[]"
    assert after["containers"]["omniroute"] == before["containers"]["omniroute"]
    assert after["containers"]["remote-setup"] != before["containers"]["remote-setup"]
    assert after["volumes"] == before["volumes"] and after["networks"] == before["networks"]
    assert not any(FORBIDDEN & set(call) for call in backend.recorded_argv())
