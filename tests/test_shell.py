"""Regression tests for shell-script lifecycle behavior."""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import stat
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _mock_command(directory: pathlib.Path, name: str) -> None:
    command = directory / name
    command.write_text("#!/usr/bin/bash\nexit 0\n")
    command.chmod(command.stat().st_mode | stat.S_IXUSR)


def _mock_dirname(directory: pathlib.Path) -> None:
    """Provide the only external utility prerequisites.sh needs to source lib.sh."""
    command = directory / "dirname"
    command.write_text(
        "#!/usr/bin/bash\ncase $1 in */*) printf '%s\\n' \"${1%/*}\" ;; *) printf '.\\n' ;; esac\n"
    )
    command.chmod(command.stat().st_mode | stat.S_IXUSR)


def run_prerequisites_with_stubs(
    tmp_path: pathlib.Path,
    *,
    yq_version: str | None = None,
    response: str = "no",
    development_available: bool = True,
    arguments: tuple[str, ...] = (),
) -> tuple[subprocess.CompletedProcess[str], pathlib.Path]:
    """Run prerequisite detection with controlled host command stubs."""
    commands = tmp_path / "bin"
    commands.mkdir()
    calls = tmp_path / "calls"

    _mock_dirname(commands)
    names = ["uv", "jq", "podman", "curl", "ip", "sudo"]
    if development_available:
        names.extend(("git", "shellcheck"))
    for name in names:
        _mock_command(commands, name)

    if yq_version is not None:
        yq = commands / "yq"
        yq.write_text(f"#!/usr/bin/bash\nprintf '%s\\n' '{yq_version}'\n")
        yq.chmod(yq.stat().st_mode | stat.S_IXUSR)

    sudo = commands / "sudo"
    sudo.write_text("#!/usr/bin/bash\nexec \"$@\"\n")
    sudo.chmod(sudo.stat().st_mode | stat.S_IXUSR)
    rpm_ostree = commands / "rpm-ostree"
    rpm_ostree.write_text(
        "#!/usr/bin/bash\nprintf '%s\\n' \"$*\" >> \"$CALLS\"\n"
    )
    rpm_ostree.chmod(rpm_ostree.stat().st_mode | stat.S_IXUSR)

    environment = os.environ | {
        "PATH": str(commands),
        "CALLS": str(calls),
    }
    result = subprocess.run(
        ["/usr/bin/bash", "prerequisites.sh", *arguments],
        cwd=ROOT,
        env=environment,
        input=f"{response}\n",
        text=True,
        capture_output=True,
        check=False,
    )
    return result, calls


def test_prerequisites_reports_missing_yq_v4_without_installing(
    tmp_path: pathlib.Path,
) -> None:
    """An obsolete yq must be reported without modifying the host."""
    result, calls = run_prerequisites_with_stubs(tmp_path, yq_version="yq 3.4.1")

    assert result.returncode == 1
    assert "Mike Farah yq v4" in result.stdout
    assert not calls.exists()


def test_prerequisites_check_reports_missing_non_runtime_rows_on_controlled_path(
    tmp_path: pathlib.Path,
) -> None:
    """Preflight must not discover host development or LAN commands."""
    result, _ = run_prerequisites_with_stubs(
        tmp_path,
        yq_version="yq (https://github.com/mikefarah/yq/) version v4.45.1",
        development_available=False,
        arguments=("--check",),
    )

    assert result.returncode == 0
    assert "missing    git              (git)        source control for updates" in result.stdout
    assert "missing    shellcheck       (ShellCheck) shell script validation" in result.stdout
    assert "missing    firewall-cmd     (firewalld)  firewall configuration for LAN access" in result.stdout
    assert "missing    avahi-publish    (avahi)      LAN service discovery" in result.stdout


def test_prerequisites_displays_a_distinct_purpose_for_every_command(
    tmp_path: pathlib.Path,
) -> None:
    """Each status row must explain the command's specific role."""
    result, _ = run_prerequisites_with_stubs(
        tmp_path,
        yq_version="yq (https://github.com/mikefarah/yq/) version v4.45.1",
        development_available=True,
        arguments=("--check",),
    )

    assert result.returncode == 0
    expected_purposes = (
        "Python tool runner and dependency manager",
        "JSON processor for script-to-Python communication",
        "Mike Farah yq v4 configuration processor",
        "container engine for llama.cpp",
        "HTTP client for downloads and health checks",
        "network address inspection",
        "source control for updates",
        "shell script validation",
        "firewall configuration for LAN access",
        "LAN service discovery",
    )
    for purpose in expected_purposes:
        assert purpose in result.stdout


def test_prerequisites_installs_only_after_yes(tmp_path: pathlib.Path) -> None:
    """Package installation must require the exact affirmative response."""
    result, calls = run_prerequisites_with_stubs(tmp_path, response="yes")

    assert result.returncode == 0
    assert "install yq" in calls.read_text()


def test_setup_stops_for_missing_prerequisites_before_mutating_config(
    tmp_path: pathlib.Path,
) -> None:
    """Setup must direct users to install prerequisites before configuration work."""
    commands = tmp_path / "bin"
    commands.mkdir()
    for name in ("uv", "jq", "podman", "curl", "ip", "git", "shellcheck"):
        _mock_command(commands, name)
    yq = commands / "yq"
    yq.write_text("#!/usr/bin/env bash\nprintf '%s\\n' 'yq 3.4.1'\n")
    yq.chmod(yq.stat().st_mode | stat.S_IXUSR)

    config = tmp_path / "models.yml"
    config.write_text("server: {}\n")
    environment = os.environ | {
        "HOME": str(tmp_path / "home"),
        "LLM_ENV_CONFIG": str(config),
        "PATH": f"{commands}:/usr/bin:/bin",
    }
    result = subprocess.run(
        ["/usr/bin/bash", "setup.sh"],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "missing prerequisites; run 'make prerequisites'" in result.stderr
    assert config.read_text() == "server: {}\n"


def run_setup_with_numbered_selection(
    tmp_path: pathlib.Path, selection: str
) -> tuple[subprocess.CompletedProcess[str], pathlib.Path, pathlib.Path]:
    """Run setup against deterministic GPU, model, and Vulkan command stubs."""
    real_yq = shutil.which("yq")
    assert real_yq is not None

    commands = tmp_path / "bin"
    commands.mkdir()
    calls = tmp_path / "calls"
    for name in ("ip", "git", "shellcheck"):
        _mock_command(commands, name)
    curl = commands / "curl"
    curl.write_text("#!/usr/bin/bash\nprintf 'curl %s\\n' \"$*\" >> \"$CALLS\"\n")
    curl.chmod(curl.stat().st_mode | stat.S_IXUSR)

    uv = commands / "uv"
    uv.write_text(
        "#!/usr/bin/bash\n"
        "printf 'uv %s\\n' \"$*\" >> \"$CALLS\"\n"
        "case \"$*\" in\n"
        "  *' detect') printf '%s\\n' '{\"gpus\":[{\"card\":\"card0\",\"pci_address\":\"0000:03:00.0\",\"vram_total_mib\":16384,\"render_node\":\"renderD128\",\"connected_outputs\":[]}]}' ;;\n"
        "  *' models list') printf '%s\\n' '{\"models\":[{\"alias\":\"gemma4\",\"label\":\"Gemma 4\",\"parameters\":\"12B\",\"quantization\":\"Q4_K_M\",\"size_bytes\":7660000000,\"enabled\":true},{\"alias\":\"ornith\",\"label\":\"Ornith\",\"parameters\":\"9B\",\"quantization\":\"Q4_K_M\",\"size_bytes\":5600000000,\"enabled\":false}]}' ;;\n"
        "  *' models select '*)\n"
        "    \"$REAL_YQ\" -i '.models[] |= (.enabled = (.alias == \"gemma4\" or .alias == \"ornith\")) | .runtime.models_max = 2' \"$CONFIG_PATH_TEST\"\n"
        "    printf '%s\\n' '{\"models_max\":2}' ;;\n"
        "  *' validate-gguf'*) printf '%s\\n' '{\"results\":[]}' ;;\n"
        "  *' budget '*) printf '%s\\n' '{\"available_mib\":12000,\"required_mib\":10000}' ;;\n"
        "  *' list-devices '*) printf '%s\\n' '{\"devices\":[{\"id\":\"Vulkan0\",\"name\":\"Integrated GPU\",\"total_mib\":8192},{\"id\":\"Vulkan1\",\"name\":\"Fallback Radeon: \\\"safe\\\"\",\"total_mib\":32768}]}' ;;\n"
        "esac\n"
    )
    uv.chmod(uv.stat().st_mode | stat.S_IXUSR)

    yq = commands / "yq"
    yq.write_text("#!/usr/bin/bash\nexec \"$REAL_YQ\" \"$@\"\n")
    yq.chmod(yq.stat().st_mode | stat.S_IXUSR)

    podman = commands / "podman"
    podman.write_text(
        "#!/usr/bin/bash\n"
        "printf 'podman %s\\n' \"$*\" >> \"$CALLS\"\n"
        "case \"$*\" in *'--list-devices'*) printf '%s\\n' 'Vulkan0: Integrated GPU (8192 MiB, 8000 MiB free)\\nVulkan1: Fallback Radeon: \\\"safe\\\" (32768 MiB, 32000 MiB free)' ;; esac\n"
    )
    podman.chmod(podman.stat().st_mode | stat.S_IXUSR)

    config = tmp_path / "models.yml"
    config.write_text(
        "gpu: {}\n"
        "runtime:\n"
        "  models_max: 0\n"
        "models:\n"
        "  - alias: gemma4\n"
        "    label: Gemma 4\n"
        "    parameters: 12B\n"
        "    quantization: Q4_K_M\n"
        "    size_bytes: 7660000000\n"
        "    enabled: true\n"
        "    file: gemma4.gguf\n"
        "    url: https://example.invalid/gemma4.gguf\n"
        "  - alias: ornith\n"
        "    label: Ornith\n"
        "    parameters: 9B\n"
        "    quantization: Q4_K_M\n"
        "    size_bytes: 5600000000\n"
        "    enabled: false\n"
        "    file: ornith.gguf\n"
        "    url: https://example.invalid/ornith.gguf\n"
    )
    environment = os.environ | {
        "CALLS": str(calls),
        "CONFIG_PATH_TEST": str(config),
        "HOME": str(tmp_path / "home"),
        "LLM_ENV_CONFIG": str(config),
        "LLM_ENV_MODELS_DIR": str(tmp_path / "models"),
        "PATH": f"{commands}:/usr/bin:/bin",
        "REAL_YQ": real_yq,
    }
    result = subprocess.run(
        ["/usr/bin/bash", "setup.sh"],
        cwd=ROOT,
        env=environment,
        input=selection,
        text=True,
        capture_output=True,
        check=False,
    )
    return result, calls, config


def test_setup_selects_zero_match_vulkan_device_and_persists_config(
    tmp_path: pathlib.Path,
) -> None:
    """A zero-VRAM match must require and persist an explicit llama device choice."""
    result, calls, config = run_setup_with_numbered_selection(tmp_path, "1\n1,2\n2\n")

    assert result.returncode == 0, result.stderr
    assert "Integrated GPU" in result.stdout
    assert 'Fallback Radeon: "safe"' in result.stdout
    call_log = calls.read_text()
    assert "models select gemma4 ornith" in call_log
    assert (
        "podman run --rm --device /dev/dri "
        "ghcr.io/ggml-org/llama.cpp:server-vulkan --list-devices" in call_log
    )
    assert "/app/llama-server --list-devices" not in call_log
    config_json = subprocess.run(
        [shutil.which("yq") or "yq", "-o=json", ".", str(config)],
        text=True,
        capture_output=True,
        check=True,
    )
    persisted = json.loads(config_json.stdout)
    assert persisted["gpu"] == {
        "pci_address": "0000:03:00.0",
        "vram_total_mib": 16384,
        "device_name": 'Fallback Radeon: "safe"',
    }
    assert persisted["runtime"]["models_max"] == 2
    assert [model["enabled"] for model in persisted["models"]] == [True, True]


def test_setup_rejects_invalid_numbered_model_selection_before_download(
    tmp_path: pathlib.Path,
) -> None:
    """Out-of-range model indexes must stop setup before any download starts."""
    result, calls, _ = run_setup_with_numbered_selection(tmp_path, "1\n3\n")

    assert result.returncode != 0
    assert "curl " not in calls.read_text()


def test_check_setup_runs_disposable_inference_for_each_enabled_model(
    tmp_path: pathlib.Path,
) -> None:
    """Offline setup validation must resolve and smoke-test every enabled model."""
    real_yq = shutil.which("yq")
    assert real_yq is not None

    commands = tmp_path / "bin"
    commands.mkdir()
    calls = tmp_path / "calls"
    for name in ("systemctl", "curl"):
        _mock_command(commands, name)

    timeout = commands / "timeout"
    timeout.write_text(
        "#!/usr/bin/bash\n"
        "printf 'timeout %s\\n' \"$*\" >> \"$CALLS\"\n"
        "[ \"$1\" = 180 ] || exit 64\n"
        "shift\n"
        "exec \"$@\"\n"
    )
    timeout.chmod(timeout.stat().st_mode | stat.S_IXUSR)

    uv = commands / "uv"
    uv.write_text(
        "#!/usr/bin/bash\n"
        "printf 'uv %s\\n' \"$*\" >> \"$CALLS\"\n"
        "case \"$*\" in\n"
        "  *' models list'*) printf '%s\\n' '{\"models\":[]}' ;;\n"
        "  *' detect'*) printf '%s\\n' '{\"gpus\":[{\"pci_address\":\"0000:03:00.0\",\"render_node\":\"renderD128\"}]}' ;;\n"
        "  *' validate-gguf'*) printf '%s\\n' '{\"results\":[]}' ;;\n"
        "  *' budget '*) printf '%s\\n' '{\"available_mib\":12000,\"required_mib\":10000,\"models_max\":2}' ;;\n"
        "  *' resolve-device'*)\n"
        "    for argument in \"$@\"; do\n"
        "      if [ \"$previous\" = --listing-file ]; then listing_file=\"$argument\"; fi\n"
        "      previous=\"$argument\"\n"
        "    done\n"
        "    [ \"$(cat \"$listing_file\")\" = 'Vulkan7: Selected Radeon (16384 MiB, 16000 MiB free)' ] || exit 65\n"
        "    printf '%s\\n' '{\"device\":\"Vulkan7\"}' ;;\n"
        "esac\n"
    )
    uv.chmod(uv.stat().st_mode | stat.S_IXUSR)

    yq = commands / "yq"
    yq.write_text("#!/usr/bin/bash\nexec \"$REAL_YQ\" \"$@\"\n")
    yq.chmod(yq.stat().st_mode | stat.S_IXUSR)

    podman = commands / "podman"
    podman.write_text(
        "#!/usr/bin/bash\n"
        "printf 'podman %s\\n' \"$*\" >> \"$CALLS\"\n"
        "case \"$*\" in\n"
        "  *'--list-devices'*) printf '%s\\n' 'Vulkan7: Selected Radeon (16384 MiB, 16000 MiB free)' ;;\n"
        "  *' cli '*) printf '%s\\n' 'ready' ;;\n"
        "esac\n"
    )
    podman.chmod(podman.stat().st_mode | stat.S_IXUSR)

    config = tmp_path / "models.yml"
    config.write_text(
        "gpu:\n"
        "  image: example.invalid/llama:latest\n"
        "  pci_address: 0000:03:00.0\n"
        "  device_name: Selected Radeon\n"
        "runtime:\n"
        "  models_max: 2\n"
        "models:\n"
        "  - alias: first\n"
        "    enabled: true\n"
        "    file: first.gguf\n"
        "    n_gpu_layers: 42\n"
        "  - alias: skipped\n"
        "    enabled: false\n"
        "    file: skipped.gguf\n"
        "    n_gpu_layers: 99\n"
        "  - alias: second\n"
        "    enabled: true\n"
        "    file: second.gguf\n"
        "    n_gpu_layers: 17\n"
    )
    models_dir = tmp_path / "models"
    environment = os.environ | {
        "CALLS": str(calls),
        "HOME": str(tmp_path / "home"),
        "LLM_ENV_CONFIG": str(config),
        "LLM_ENV_MODELS_DIR": str(models_dir),
        "PATH": f"{commands}:/usr/bin:/bin",
        "REAL_YQ": real_yq,
    }
    result = subprocess.run(
        ["/usr/bin/bash", "check-setup.sh"],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    recorded = calls.read_text()
    check_setup = (ROOT / "check-setup.sh").read_text()
    assert 'uv run "${REPO_DIR}/llmenv.py" resolve-device' in check_setup
    assert (
        f"uv run {ROOT / 'llmenv.py'} resolve-device --device-name Selected Radeon "
        "--listing-file "
    ) in recorded
    list_devices = (
        "podman run --rm --device /dev/dri "
        "example.invalid/llama:latest --list-devices"
    )
    first_inference = (
        f"podman run --rm --device /dev/dri -v {models_dir}:/models:ro,z "
        "--entrypoint /app/llama example.invalid/llama:latest cli -m /models/first.gguf "
        "--device Vulkan7 --n-gpu-layers 42 --single-turn -p Reply with exactly: ready -n 16"
    )
    second_inference = (
        f"podman run --rm --device /dev/dri -v {models_dir}:/models:ro,z "
        "--entrypoint /app/llama example.invalid/llama:latest cli -m /models/second.gguf "
        "--device Vulkan7 --n-gpu-layers 17 --single-turn -p Reply with exactly: ready -n 16"
    )
    assert recorded.count(list_devices) == 1
    assert recorded.count(f"timeout 180 {first_inference}") == 1
    assert recorded.count(f"timeout 180 {second_inference}") == 1
    assert "/models/skipped.gguf" not in recorded
    inference_calls = [
        line.split()
        for line in recorded.splitlines()
        if line.startswith("podman run") and " cli " in line
    ]
    assert len(inference_calls) == 2
    for arguments in inference_calls:
        assert "--publish" not in arguments
        assert "-p" not in arguments[: arguments.index("example.invalid/llama:latest")]
    assert "podman exec" not in recorded


def test_enable_boot_fails_when_quadlet_rerender_fails(tmp_path: pathlib.Path) -> None:
    """A failed start.sh rerender must not be reported as boot setup success."""
    commands = tmp_path / "bin"
    commands.mkdir()
    for name in ("yq", "loginctl", "systemctl"):
        _mock_command(commands, name)

    home = tmp_path / "home"
    config = home / ".config/llm-env/models.yml"
    config.parent.mkdir(parents=True)
    config.write_text("server: {}\n")
    unit = home / ".config/containers/systemd/llm-server.container"
    unit.parent.mkdir(parents=True)
    unit.write_text("[Container]\n")

    environment = os.environ | {
        "HOME": str(home),
        "LLM_ENV_CONFIG": str(config),
        "PATH": f"{commands}:/usr/bin:/bin",
    }
    result = subprocess.run(
        ["bash", "enable-boot.sh"],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
