"""Regression tests for shell-script lifecycle behavior."""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import stat
import subprocess

import pytest

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


def run_lifecycle_script(
    tmp_path: pathlib.Path,
    script: str,
    *,
    api_key: str = "existing-key",
    active: bool = False,
    config_mode: int = 0o600,
) -> tuple[subprocess.CompletedProcess[str], pathlib.Path, pathlib.Path]:
    """Run a lifecycle script with real configuration writes and external stubs."""
    real_yq = shutil.which("yq")
    assert real_yq is not None

    commands = tmp_path / "bin"
    commands.mkdir()
    calls = tmp_path / "calls"
    calls.touch()
    home = tmp_path / "home"
    config = home / ".config/llm-env/models.yml"
    config.parent.mkdir(parents=True)
    config.write_text(
        "server:\n"
        "  host: 0.0.0.0\n"
        "  port: 8000\n"
        f"  api_key: {api_key!r}\n"
        "  mdns_name: llm\n"
        "  sleep_idle_seconds: 300\n"
        "  start_at_boot: false\n"
        "gpu:\n"
        "  backend: cpu\n"
        "  image: example.invalid/llama:latest\n"
        "  device_name: ''\n"
        "runtime:\n"
        "  models_max: 1\n"
        "models:\n"
        "  - alias: test\n"
        "    enabled: true\n"
    )
    config.chmod(config_mode)

    bash = commands / "bash"
    bash.write_text(
        "#!/usr/bin/bash\n"
        "printf 'bash %s\\n' \"$*\" >> \"$CALLS\"\n"
        "exec /usr/bin/bash \"$@\"\n"
    )
    bash.chmod(bash.stat().st_mode | stat.S_IXUSR)
    avahi_publish = commands / "avahi-publish"
    avahi_publish.write_text("#!/usr/bin/bash\nexit 0\n")
    avahi_publish.chmod(avahi_publish.stat().st_mode | stat.S_IXUSR)

    for name in ("loginctl", "ip"):
        command = commands / name
        command.write_text(
            "#!/usr/bin/bash\n"
            "printf '%s %s\\n' \"$(basename \"$0\")\" \"$*\" >> \"$CALLS\"\n"
            "[ \"$(basename \"$0\")\" != ip ] || printf '%s\\n' '[{\"addr_info\":[{\"local\":\"192.0.2.1\"}]}]'\n"
        )
        command.chmod(command.stat().st_mode | stat.S_IXUSR)

    yq = commands / "yq"
    yq.write_text(
        "#!/usr/bin/bash\n"
        "printf 'yq %s\\n' \"$*\" >> \"$CALLS\"\n"
        "exec \"$REAL_YQ\" \"$@\"\n"
    )
    yq.chmod(yq.stat().st_mode | stat.S_IXUSR)
    chmod = commands / "chmod"
    chmod.write_text(
        "#!/usr/bin/bash\n"
        "printf 'chmod %s\\n' \"$*\" >> \"$CALLS\"\n"
        "exec /usr/bin/chmod \"$@\"\n"
    )
    chmod.chmod(chmod.stat().st_mode | stat.S_IXUSR)
    uv = commands / "uv"
    uv.write_text(
        "#!/usr/bin/bash\n"
        "printf 'uv %s\\n' \"$*\" >> \"$CALLS\"\n"
        "case \"$*\" in *' budget '*) printf '%s\\n' '{\"available_mib\":12000,\"required_mib\":10000}' ;; esac\n"
    )
    uv.chmod(uv.stat().st_mode | stat.S_IXUSR)
    podman = commands / "podman"
    podman.write_text("#!/usr/bin/bash\nprintf 'podman %s\\n' \"$*\" >> \"$CALLS\"\n")
    podman.chmod(podman.stat().st_mode | stat.S_IXUSR)
    curl = commands / "curl"
    curl.write_text("#!/usr/bin/bash\nprintf 'curl %s\\n' \"$*\" >> \"$CALLS\"\n")
    curl.chmod(curl.stat().st_mode | stat.S_IXUSR)
    systemctl = commands / "systemctl"
    systemctl.write_text(
        "#!/usr/bin/bash\n"
        "printf 'systemctl %s\\n' \"$*\" >> \"$CALLS\"\n"
        "case \"$*\" in\n"
        "  *'daemon-reload'*) [ -f \"$MDNS_UNIT\" ] || exit 66 ;;\n"
        "  *'is-active --quiet'*) [ \"$ACTIVE\" = 1 ] ;;\n"
        "esac\n"
    )
    systemctl.chmod(systemctl.stat().st_mode | stat.S_IXUSR)

    environment = os.environ | {
        "ACTIVE": "1" if active else "0",
        "CALLS": str(calls),
        "HOME": str(home),
        "LLM_ENV_CONFIG": str(config),
        "LLM_ENV_MODELS_DIR": str(tmp_path / "models"),
        "MDNS_UNIT": str(home / ".config/systemd/user/llm-server-mdns.service"),
        "PATH": f"{commands}:/usr/bin:/bin",
        "REAL_YQ": real_yq,
    }
    result = subprocess.run(
        ["/usr/bin/bash", script],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    return result, config, calls


def yq_value(config: pathlib.Path, expression: str) -> str:
    """Read one scalar from a test configuration with the real yq binary."""
    return subprocess.run(
        [shutil.which("yq") or "yq", "-r", expression, str(config)],
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()


def test_start_generates_key_only_when_empty(tmp_path: pathlib.Path) -> None:
    """Starting an unconfigured server must persist a secret without printing it."""
    result, config, calls = run_lifecycle_script(
        tmp_path, "start.sh", api_key="", config_mode=0o644
    )

    assert result.returncode == 0, result.stderr
    assert yq_value(config, ".server.api_key")
    assert stat.S_IMODE(config.stat().st_mode) == 0o600
    assert yq_value(config, ".server.api_key") not in result.stdout
    assert yq_value(config, ".server.api_key") not in result.stderr
    assert "systemctl --user start llm-server.service" in calls.read_text()


def test_key_writes_secure_config_before_persisting_a_secret(
    tmp_path: pathlib.Path,
) -> None:
    """A generated or reset key must never be written to a mode-0644 config."""
    for script, api_key in (("start.sh", ""), ("key-reset.sh", "existing-key")):
        case_path = tmp_path / script
        case_path.mkdir()
        result, config, calls = run_lifecycle_script(
            case_path, script, api_key=api_key, config_mode=0o644
        )

        assert result.returncode == 0, result.stderr
        log = calls.read_text().splitlines()
        chmod_index = log.index(f"chmod 600 {config}")
        write_index = next(
            index
            for index, call in enumerate(log)
            if call.startswith("yq -i .server.api_key = strenv(API_KEY)")
        )
        assert chmod_index < write_index


def test_start_retains_an_existing_key(tmp_path: pathlib.Path) -> None:
    """Starting with a configured key must not replace that credential."""
    result, config, _ = run_lifecycle_script(tmp_path, "start.sh")

    assert result.returncode == 0, result.stderr
    assert yq_value(config, ".server.api_key") == "existing-key"


def test_key_reset_restarts_an_active_server(tmp_path: pathlib.Path) -> None:
    """Rotating an active server key must load the replacement immediately."""
    result, config, calls = run_lifecycle_script(
        tmp_path, "key-reset.sh", active=True, config_mode=0o644
    )

    assert result.returncode == 0, result.stderr
    assert yq_value(config, ".server.api_key") != "existing-key"
    assert stat.S_IMODE(config.stat().st_mode) == 0o600
    assert "systemctl --user stop llm-server.service" in calls.read_text()
    assert "systemctl --user start llm-server.service" in calls.read_text()


def test_key_reset_does_not_start_an_inactive_server(tmp_path: pathlib.Path) -> None:
    """Rotating a stopped server key must preserve its stopped state."""
    result, _, calls = run_lifecycle_script(tmp_path, "key-reset.sh", active=False)

    assert result.returncode == 0, result.stderr
    assert "systemctl --user start llm-server.service" not in calls.read_text()


def test_enable_boot_prepares_a_secure_key_without_starting(tmp_path: pathlib.Path) -> None:
    """Boot setup must create a private key without starting or budget-checking."""
    result, config, calls = run_lifecycle_script(
        tmp_path, "enable-boot.sh", api_key="", config_mode=0o644
    )

    assert result.returncode == 0, result.stderr
    assert yq_value(config, ".server.api_key")
    assert stat.S_IMODE(config.stat().st_mode) == 0o600
    assert "render-unit.sh" in calls.read_text()
    assert "start.sh" not in calls.read_text()
    assert " budget " not in calls.read_text()


def test_enable_boot_renders_a_health_gated_mdns_user_unit(
    tmp_path: pathlib.Path,
) -> None:
    """Boot setup must make mDNS discoverable as a reloaded user unit."""
    result, config, calls = run_lifecycle_script(tmp_path, "enable-boot.sh")
    mdns_unit = config.parent.parent / "systemd/user/llm-server-mdns.service"

    assert result.returncode == 0, result.stderr
    unit = mdns_unit.read_text()
    container_unit = (
        config.parent.parent / "containers/systemd/llm-server.container"
    ).read_text()
    assert "Wants=llm-server-mdns.service" in container_unit
    assert "Requires=llm-server.service" in unit
    assert "After=llm-server.service" in unit
    assert "PartOf=llm-server.service" in unit
    assert "Restart=on-failure" in unit
    assert "ExecStartPre=" in unit
    assert "http://127.0.0.1:8000/health" in unit
    assert "avahi-publish -s llm _http._tcp 8000" in unit
    assert "systemctl --user daemon-reload" in calls.read_text()


def run_check_server(
    tmp_path: pathlib.Path, completion_body: dict[str, str]
) -> tuple[subprocess.CompletedProcess[str], pathlib.Path]:
    """Run the online contract check with deterministic API command stubs."""
    commands = tmp_path / "bin"
    commands.mkdir()
    calls = tmp_path / "calls"
    calls.touch()
    config = tmp_path / "models.yml"
    config.write_text(
        "server:\n"
        "  port: 8000\n"
        "models:\n"
        "  - alias: gemma4\n"
        "    enabled: true\n"
        "  - alias: ornith\n"
        "    enabled: true\n"
    )

    curl = commands / "curl"
    curl.write_text(
        "#!/usr/bin/bash\n"
        "printf '%s\\n' \"$*\" >> \"$CALLS\"\n"
        "url=\"${!#}\"\n"
        "case \"$url\" in\n"
        "  */health) exit 0 ;;\n"
        "  */v1/models) printf '%s\\n' '{\"data\":[{\"id\":\"gemma4\"},{\"id\":\"ornith\"}]}' ;;\n"
        "  */v1/chat/completions)\n"
        "    for argument in \"$@\"; do\n"
        "      [ \"$argument\" != '%{http_code}' ] || { printf '401'; exit 0; }\n"
        "    done\n"
        "    for argument in \"$@\"; do\n"
        "      if [ \"${previous:-}\" = -d ]; then model=\"$argument\"; fi\n"
        "      previous=\"$argument\"\n"
        "    done\n"
        "    case \"$model\" in\n"
        "      gemma4) content=\"$GEMMA4_CONTENT\" ;;\n"
        "      ornith) content=\"$ORNITH_CONTENT\" ;;\n"
        "      *) exit 64 ;;\n"
        "    esac\n"
        "    printf '{\"choices\":[{\"message\":{\"content\":%s}}]}\\n' \"$content\" ;;\n"
        "  *) exit 64 ;;\n"
        "esac\n"
    )
    curl.chmod(curl.stat().st_mode | stat.S_IXUSR)

    jq = commands / "jq"
    jq.write_text(
        "#!/usr/bin/bash\n"
        "case \"$1\" in\n"
        "  -n)\n"
        "    while [ \"$#\" -gt 0 ]; do\n"
        "      if [ \"$1\" = --arg ] && [ \"${2:-}\" = m ]; then\n"
        "        printf '%s\\n' \"${3:-}\"\n"
        "        exit 0\n"
        "      fi\n"
        "      shift\n"
        "    done\n"
        "    ;;\n"
        "  -r)\n"
        "    case \"$2\" in\n"
        "      '[.data[].id] | sort | join(\",\")') printf '%s\\n' 'gemma4,ornith' ;;\n"
        "      '.choices[0].message.content // empty')\n"
        "        response=\"$(cat)\"\n"
        "        content=\"${response#*\\\"content\\\":}\"\n"
        "        content=\"${content#\\\"}\"\n"
        "        content=\"${content%%\\\"*}\"\n"
        "        printf '%b\\n' \"$content\"\n"
        "        ;;\n"
        "      *) exit 64 ;;\n"
        "    esac\n"
        "    ;;\n"
        "  *) exit 64 ;;\n"
        "esac\n"
    )
    jq.chmod(jq.stat().st_mode | stat.S_IXUSR)

    yq = commands / "yq"
    yq.write_text(
        "#!/usr/bin/bash\n"
        "case \"$2\" in\n"
        "  '.server.port') printf '%s\\n' 8000 ;;\n"
        "  '.server.api_key') printf '\\n' ;;\n"
        "  '[.models[] | select(.enabled) | .alias] | sort | join(\",\")')\n"
        "    printf '%s\\n' 'gemma4,ornith'\n"
        "    ;;\n"
        "  '.models[] | select(.enabled) | .alias') printf '%s\\n' gemma4 ornith ;;\n"
        "  *) exit 64 ;;\n"
        "esac\n"
    )
    yq.chmod(yq.stat().st_mode | stat.S_IXUSR)

    environment = os.environ | {
        "CALLS": str(calls),
        "GEMMA4_CONTENT": json.dumps(completion_body["gemma4"]),
        "HOME": str(tmp_path / "home"),
        "LLM_ENV_CONFIG": str(config),
        "ORNITH_CONTENT": json.dumps(completion_body["ornith"]),
        "PATH": f"{commands}:/usr/bin:/bin",
    }
    result = subprocess.run(
        ["/usr/bin/bash", "check-server.sh"],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    return result, calls


def test_check_server_requires_normalized_ready_for_every_enabled_model(
    tmp_path: pathlib.Path,
) -> None:
    """An enabled model returning another phrase must fail the contract."""
    result, calls = run_check_server(
        tmp_path,
        {"gemma4": " Ready!\n", "ornith": "not ready"},
    )

    assert result.returncode != 0
    assert "gemma4: returned ready" in result.stdout
    assert "ornith: expected ready" in result.stderr
    assert calls.read_text().count("/v1/chat/completions") == 3


def test_check_server_accepts_normalized_ready_for_every_enabled_model(
    tmp_path: pathlib.Path,
) -> None:
    """Punctuation and case must not prevent a valid ready response."""
    result, _ = run_check_server(
        tmp_path,
        {"gemma4": "READY.", "ornith": " ready "},
    )

    assert result.returncode == 0, result.stderr


def run_agent_check(
    tmp_path: pathlib.Path,
    *,
    clients: dict[str, str],
    arguments: tuple[str, ...] = (),
    model_alias: str = "gemma4",
    weather_body: str | None = None,
    fx_body: str | None = None,
    api_key: str = "test-key-not-a-secret",
    xtrace: bool = False,
) -> tuple[subprocess.CompletedProcess[str], pathlib.Path, pathlib.Path]:
    """Run the opt-in check with an isolated client path and fake APIs."""
    real_jq = shutil.which("jq")
    real_yq = shutil.which("yq")
    assert real_jq is not None
    assert real_yq is not None

    commands = tmp_path / "bin"
    commands.mkdir()
    calls = tmp_path / "calls"
    calls.touch()
    config = tmp_path / "models.yml"
    config.write_text(
        "server:\n"
        "  port: 8000\n"
        f"  api_key: {api_key}\n"
    )

    _mock_dirname(commands)
    for name, executable in {
        "chmod": "/usr/bin/chmod",
        "jq": real_jq,
        "mktemp": "/usr/bin/mktemp",
        "rm": "/usr/bin/rm",
        "yq": real_yq,
    }.items():
        command = commands / name
        command.write_text(f"#!/usr/bin/bash\nexec {executable!s} \"$@\"\n")
        command.chmod(command.stat().st_mode | stat.S_IXUSR)

    curl = commands / "curl"
    curl.write_text(
        "#!/usr/bin/bash\n"
        "printf 'curl %s\\n' \"$*\" >> \"$CALLS\"\n"
        "url=\"${!#}\"\n"
        "case \"$url\" in\n"
        "  */v1/models) printf '%s\\n' \"$AGENT_CHECK_MODELS\" ;;\n"
        "  https://api.open-meteo.com/*)\n"
        "    printf '%s\\n' \"$AGENT_CHECK_WEATHER_BODY\"\n"
        "    ;;\n"
        "  https://open.er-api.com/*)\n"
        "    printf '%s\\n' \"$AGENT_CHECK_FX_BODY\"\n"
        "    ;;\n"
        "  *) exit 64 ;;\n"
        "esac\n"
    )
    curl.chmod(curl.stat().st_mode | stat.S_IXUSR)

    for name, body in clients.items():
        command = commands / name
        command.write_text(body)
        command.chmod(command.stat().st_mode | stat.S_IXUSR)

    environment = os.environ | {
        "AGENT_CHECK_MODELS": json.dumps({"data": [{"id": model_alias}]}),
        "AGENT_CHECK_WEATHER_BODY": weather_body
        if weather_body is not None
        else '{"current":{"time":"2026-07-27T13:00","temperature_2m":16.3,"weather_code":3}}',
        "AGENT_CHECK_FX_BODY": fx_body
        if fx_body is not None
        else '{"time_last_update_utc":"Mon, 27 Jul 2026 00:02:31 +0000","rates":{"CLP":946.527902}}',
        "CALLS": str(calls),
        "HOME": str(tmp_path / "home"),
        "LLM_ENV_CONFIG": str(config),
        "PATH": str(commands),
    }
    command = ["/usr/bin/bash"]
    if xtrace:
        command.append("-x")
    command.extend(("check-with-agents.sh", *arguments))
    result = subprocess.run(
        command,
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    return result, config, calls


def test_make_help_lists_check_with_agents() -> None:
    assert "make check-with-agents" in (ROOT / "Makefile").read_text()


def test_agent_check_fails_when_no_supported_client_is_installed(
    tmp_path: pathlib.Path,
) -> None:
    """The opt-in check must reject a PATH without Pi or OpenCode."""
    result, _, _ = run_agent_check(tmp_path, clients={})

    assert result.returncode != 0
    assert "fail no supported agent is installed" in result.stderr
    assert result.stdout.count("SKIP client=") == 2


def test_agent_check_fetches_public_snapshots_and_models_before_no_client_failure(
    tmp_path: pathlib.Path,
) -> None:
    """The no-client gate must still validate all matrix inputs first."""
    result, _, calls = run_agent_check(tmp_path, clients={})

    assert result.returncode != 0
    recorded = calls.read_text()
    assert "https://api.open-meteo.com/" in recorded
    assert "https://open.er-api.com/" in recorded
    assert "http://127.0.0.1:8000/v1/models" in recorded


def test_agent_check_rejects_arguments_without_echoing_them(
    tmp_path: pathlib.Path,
) -> None:
    """Secrets supplied as arguments must be rejected without disclosure."""
    forbidden_argument = "must-not-be-accepted-or-printed"
    result, _, _ = run_agent_check(
        tmp_path,
        clients={},
        arguments=(forbidden_argument,),
    )

    assert result.returncode != 0
    assert "accepts no arguments" in result.stderr
    assert forbidden_argument not in result.stdout
    assert forbidden_argument not in result.stderr


@pytest.mark.parametrize(
    ("weather_body", "fx_body", "expected_error"),
    [
        ("{}", None, "could not fetch a valid weather snapshot"),
        (None, "{}", "could not fetch a valid FX snapshot"),
        (
            '{"current":{"time":null,"temperature_2m":16.3,"weather_code":3}}',
            None,
            "could not fetch a valid weather snapshot",
        ),
        (
            '{"current":{"time":"2026-07-27T13:00","temperature_2m":"16.3","weather_code":3}}',
            None,
            "could not fetch a valid weather snapshot",
        ),
        (
            '{"current":{"time":"2026-07-27T13:00","temperature_2m":16.3,"weather_code":"3"}}',
            None,
            "could not fetch a valid weather snapshot",
        ),
        (
            None,
            '{"time_last_update_utc":null,"rates":{"CLP":946.527902}}',
            "could not fetch a valid FX snapshot",
        ),
        (
            None,
            '{"time_last_update_utc":"Mon, 27 Jul 2026 00:02:31 +0000","rates":{"CLP":"946.527902"}}',
            "could not fetch a valid FX snapshot",
        ),
    ],
)
def test_agent_check_rejects_malformed_source_bodies(
    tmp_path: pathlib.Path,
    weather_body: str | None,
    fx_body: str | None,
    expected_error: str,
) -> None:
    """Public-source snapshots must contain typed timestamp and value fields."""
    result, _, _ = run_agent_check(
        tmp_path,
        clients={},
        weather_body=weather_body,
        fx_body=fx_body,
    )

    assert result.returncode != 0
    assert expected_error in result.stderr


def test_agent_check_hides_api_key_when_shell_tracing_is_enabled(
    tmp_path: pathlib.Path,
) -> None:
    """Shell tracing must never expose the curl authentication secret."""
    api_key = "xtrace-fixture-api-key"
    result, _, _ = run_agent_check(
        tmp_path,
        clients={},
        api_key=api_key,
        xtrace=True,
    )

    assert result.returncode != 0
    assert api_key not in result.stdout
    assert api_key not in result.stderr


def test_agent_check_builds_a_failure_row_for_each_present_client_matrix_cell(
    tmp_path: pathlib.Path,
) -> None:
    """An unimplemented detected client must fail every model-source cell."""
    result, _, _ = run_agent_check(
        tmp_path,
        clients={"pi": "#!/usr/bin/bash\\nexit 0\\n"},
    )

    assert result.returncode != 0
    assert "SKIP client=opencode" in result.stdout
    assert "FAIL client=pi model=gemma4 check=weather reason=agent-failed" in result.stdout
    assert "FAIL client=pi model=gemma4 check=fx reason=agent-failed" in result.stdout
    assert "unsupported client" in result.stderr


def test_agent_check_dispatches_an_alias_that_cannot_form_an_output_path(
    tmp_path: pathlib.Path,
) -> None:
    """A model alias must not prevent dispatch through an unsafe output filename."""
    model_alias = "nested/model"
    result, _, _ = run_agent_check(
        tmp_path,
        clients={"pi": "#!/usr/bin/bash\\nexit 0\\n"},
        model_alias=model_alias,
    )

    assert result.returncode != 0
    assert f"FAIL client=pi model={model_alias} check=weather reason=agent-failed" in result.stdout
    assert f"FAIL client=pi model={model_alias} check=fx reason=agent-failed" in result.stdout
    assert "unsupported client: pi" in result.stderr
