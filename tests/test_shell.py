"""Regression tests for shell-script lifecycle behavior."""

from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
import socket
import stat
import subprocess

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "scripts"
SETUP_DIR = ROOT / "setup"
TOOLS_DIR = ROOT / "tools"

VALID_AGENT_SETUP_CONFIG = """\
version: 1
server:
  host: 0.0.0.0
  port: 18123
  api_key: fixture-local-api-key
  mdns_name: llm
  sleep_idle_seconds: 300
omniroute:
  port: 20128
  initial_password: fixture-dashboard-password
gpu:
  pci_address: 0000:03:00.0
  device_name: Test GPU
  backend: vulkan
  image: example.invalid/llama:latest
  vram_total_mib: 16384
  reserve_mode: auto
  reserve_floor_mib: 1024
runtime:
  models_max: 1
  parallel_slots: 1
  ubatch_size: 512
  flash_attn: true
  cache_type_k: q8_0
  cache_type_v: q8_0
models:
  - alias: gemma4
    label: Gemma 4
    parameters: 12B
    quantization: Q4_K_M
    enabled: true
    file: gemma4.gguf
    url: https://example.invalid/gemma4.gguf
    size_bytes: 1
    vram_budget: 55%
    ctx_size: 131072
    client_max_output_tokens: 8192
    n_gpu_layers: 99
  - alias: ornith
    label: Ornith
    parameters: 9B
    quantization: Q4_K_M
    enabled: true
    file: ornith.gguf
    url: https://example.invalid/ornith.gguf
    size_bytes: 1
    vram_budget: 40%
    ctx_size: 131072
    client_max_output_tokens: 8192
    n_gpu_layers: 99
  - alias: old-model
    label: Old model
    parameters: 1B
    quantization: Q4_K_M
    enabled: false
    file: old.gguf
    url: https://example.invalid/old.gguf
    size_bytes: 1
    vram_budget: 5%
    ctx_size: 8192
    client_max_output_tokens: 2048
    n_gpu_layers: 99
"""


def test_shell_scripts_use_the_approved_directories() -> None:
    assert not list(ROOT.glob("*.sh"))
    assert (TOOLS_DIR / "lib.sh").is_file()
    assert (SETUP_DIR / "setup.sh").is_file()
    assert (SETUP_DIR / "setup-local-llm-agents.sh").is_file()
    assert (SCRIPT_DIR / "check-server.sh").is_file()
    assert (SCRIPT_DIR / "gpu-status.sh").is_file()


def test_makefile_dispatches_relocated_entrypoints() -> None:
    makefile = (ROOT / "Makefile").read_text()

    assert "bash scripts/help.sh" in makefile
    assert "bash setup/setup.sh" in makefile
    assert "bash setup/setup-local-llm-agents.sh" in makefile
    assert "bash scripts/check-server.sh" in makefile
    assert "bash scripts/gpu-status.sh" in makefile


def test_presets_helpers_read_a_generated_ini(tmp_path: pathlib.Path) -> None:
    config = tmp_path / "models.yml"
    config.write_text(
        "version: 1\nserver: {host: 0.0.0.0, port: 8000, api_key: k}\n"
        "gpu: {backend: vulkan, pci_address: '0000:03:00.0', vram_total_mib: 16384, reserve_mode: auto}\n"
        "runtime: {models_max: 1, parallel_slots: 1, ubatch_size: 512, flash_attn: true, cache_type_k: q5_1, cache_type_v: q5_1}\n"
        "models:\n"
        "- {alias: dense, label: Dense, parameters: 1B, quantization: Q4_K_M, enabled: true, file: dense.gguf, url: 'https://example.invalid/d', size_bytes: 1, vram_budget: 50%, ctx_size: 4096, client_max_output_tokens: 2048, n_gpu_layers: 99}\n"
        "- {alias: moe, label: MoE, parameters: 8B, quantization: Q4_K_M, enabled: true, file: moe.gguf, url: 'https://example.invalid/m', size_bytes: 1, vram_budget: 50%, ctx_size: 8192, client_max_output_tokens: 2048, n_gpu_layers: 40, n_cpu_moe: 12}\n"
    )
    output = tmp_path / "presets.ini"
    result = subprocess.run(
        [
            "/usr/bin/bash", "-c",
            (
                'source tools/lib.sh; render_presets_file "$1" "$2"; '
                'presets_value "$2" dense n-gpu-layers; '
                'presets_value "$2" moe n-gpu-layers; '
                'presets_value "$2" moe n-cpu-moe; '
                '(presets_value "$2" dense n-cpu-moe; echo "<empty>")'
            ),
            "bash", "Vulkan0", str(output),
        ],
        cwd=ROOT,
        env=os.environ | {
            "LLM_ENV_CONFIG": str(config),
            "LLM_ENV_MODELS_DIR": str(tmp_path / "models"),
        },
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["99", "40", "12", "<empty>"]


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
        ["/usr/bin/bash", "setup/prerequisites.sh", *arguments],
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
        "JSONC editor for OpenCode client configuration",
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


def test_prerequisites_applies_live_when_purely_additive(tmp_path: pathlib.Path) -> None:
    """A pure-additive layering must apply live, with no reboot message."""
    commands = tmp_path / "bin"
    commands.mkdir()
    _mock_dirname(commands)
    for name in ("uv", "jq", "podman", "curl", "ip", "git", "shellcheck"):
        _mock_command(commands, name)
    yq = commands / "yq"
    yq.write_text(
        "#!/usr/bin/bash\nprintf '%s\\n' 'yq (https://github.com/mikefarah/yq/) version v4.45.1'\n"
    )
    yq.chmod(yq.stat().st_mode | stat.S_IXUSR)
    sudo = commands / "sudo"
    sudo.write_text("#!/usr/bin/bash\nexec \"$@\"\n")
    sudo.chmod(sudo.stat().st_mode | stat.S_IXUSR)
    calls = tmp_path / "calls"
    rpm_ostree = commands / "rpm-ostree"
    rpm_ostree.write_text(
        "#!/usr/bin/bash\n"
        "printf '%s\\n' \"$*\" >> \"$CALLS\"\n"
        "case \"$*\" in apply-live) exit 0 ;; esac\n"
    )
    rpm_ostree.chmod(rpm_ostree.stat().st_mode | stat.S_IXUSR)

    environment = os.environ | {"PATH": str(commands), "CALLS": str(calls)}
    result = subprocess.run(
        ["/usr/bin/bash", "setup/prerequisites.sh"],
        cwd=ROOT,
        env=environment,
        input="yes\n",
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "apply-live" in calls.read_text()
    assert "applied live; no reboot needed" in result.stdout
    assert "reboot is required" not in result.stdout


def test_prerequisites_falls_back_to_reboot_when_apply_live_declined(
    tmp_path: pathlib.Path,
) -> None:
    """A bundled OS update must ask before forcing --allow-replacement live,
    and fall back to a reboot message when declined."""
    commands = tmp_path / "bin"
    commands.mkdir()
    _mock_dirname(commands)
    for name in ("uv", "jq", "podman", "curl", "ip", "git", "shellcheck"):
        _mock_command(commands, name)
    yq = commands / "yq"
    yq.write_text(
        "#!/usr/bin/bash\nprintf '%s\\n' 'yq (https://github.com/mikefarah/yq/) version v4.45.1'\n"
    )
    yq.chmod(yq.stat().st_mode | stat.S_IXUSR)
    sudo = commands / "sudo"
    sudo.write_text("#!/usr/bin/bash\nexec \"$@\"\n")
    sudo.chmod(sudo.stat().st_mode | stat.S_IXUSR)
    calls = tmp_path / "calls"
    rpm_ostree = commands / "rpm-ostree"
    rpm_ostree.write_text(
        "#!/usr/bin/bash\n"
        "printf '%s\\n' \"$*\" >> \"$CALLS\"\n"
        "case \"$*\" in\n"
        "  apply-live) printf 'error: packages would be removed: 6, allow replacement to override\\n' >&2; exit 1 ;;\n"
        "esac\n"
    )
    rpm_ostree.chmod(rpm_ostree.stat().st_mode | stat.S_IXUSR)

    environment = os.environ | {"PATH": str(commands), "CALLS": str(calls)}
    result = subprocess.run(
        ["/usr/bin/bash", "setup/prerequisites.sh"],
        cwd=ROOT,
        env=environment,
        input="yes\nno\n",
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "apply-live --allow-replacement" not in calls.read_text()
    assert "reboot is required" in result.stdout


def test_prerequisites_reports_missing_uv_without_rpm_ostree(tmp_path: pathlib.Path) -> None:
    commands = tmp_path / "bin"
    commands.mkdir()
    _mock_dirname(commands)
    for name in ("jq", "yq", "podman", "curl", "ip", "sudo"):
        _mock_command(commands, name)
    yq = commands / "yq"
    yq.write_text(
        "#!/usr/bin/bash\nprintf '%s\\n' 'yq (https://github.com/mikefarah/yq/) version v4.45.1'\n"
    )
    yq.chmod(yq.stat().st_mode | stat.S_IXUSR)

    environment = os.environ | {"PATH": str(commands)}
    result = subprocess.run(
        ["/usr/bin/bash", "setup/prerequisites.sh", "--check"],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 1
    assert "missing    uv" in result.stdout


def test_prerequisites_installs_uv_via_official_installer_not_rpm_ostree(
    tmp_path: pathlib.Path,
) -> None:
    commands = tmp_path / "bin"
    commands.mkdir()
    _mock_dirname(commands)
    for name in ("jq", "podman", "curl", "ip", "git", "shellcheck"):
        _mock_command(commands, name)
    yq = commands / "yq"
    yq.write_text(
        "#!/usr/bin/bash\nprintf '%s\\n' 'yq (https://github.com/mikefarah/yq/) version v4.45.1'\n"
    )
    yq.chmod(yq.stat().st_mode | stat.S_IXUSR)
    calls = tmp_path / "calls"
    rpm_ostree = commands / "rpm-ostree"
    rpm_ostree.write_text(
        "#!/usr/bin/bash\nprintf '%s\\n' \"$*\" >> \"$CALLS\"\n"
    )
    rpm_ostree.chmod(rpm_ostree.stat().st_mode | stat.S_IXUSR)
    sudo = commands / "sudo"
    sudo.write_text("#!/usr/bin/bash\nexec \"$@\"\n")
    sudo.chmod(sudo.stat().st_mode | stat.S_IXUSR)
    uv_installer_log = tmp_path / "uv-install-invoked"
    curl = commands / "curl"
    curl.write_text(
        "#!/usr/bin/bash\n"
        # Log invocation to $CALLS, not real stdout — real stdout here is
        # what gets piped into `sh` by the script under test, so it must be
        # valid (harmless) shell content, not a log line.
        "printf '%s\\n' \"$*\" >> \"$CALLS\"\n"
        f"touch {uv_installer_log}\n"
        "printf 'true\\n'\n"
        "exit 0\n"
    )
    curl.chmod(curl.stat().st_mode | stat.S_IXUSR)

    environment = os.environ | {
        "CALLS": str(calls),
        # commands must come first so the mocked curl/rpm-ostree/etc. win,
        # but /usr/bin:/bin must also be present — the real `sh` (the
        # pipeline destination of `curl ... | sh`) and `touch` (used inside
        # the curl stub above) are not mocked and must resolve to the real
        # system binaries, the same convention run_lifecycle_script and
        # run_cleanup_with_stubs already use elsewhere in this file.
        "PATH": f"{commands}:/usr/bin:/bin",
    }
    result = subprocess.run(
        ["/usr/bin/bash", "setup/prerequisites.sh"],
        cwd=ROOT,
        env=environment,
        input="yes\nyes\n",
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert uv_installer_log.exists()
    assert "install uv" not in calls.read_text()


def test_prerequisites_reports_missing_podman_compose_provider(tmp_path):
    commands = tmp_path / "bin"
    commands.mkdir()
    _mock_dirname(commands)
    for name in ("uv", "jq", "yq", "curl", "ip", "sudo"):
        _mock_command(commands, name)
    podman = commands / "podman"
    podman.write_text("#!/usr/bin/bash\nexit 1\n")  # "compose" subcommand fails: no provider
    podman.chmod(podman.stat().st_mode | stat.S_IXUSR)

    environment = os.environ | {"PATH": str(commands)}
    result = subprocess.run(
        ["/usr/bin/bash", "setup/prerequisites.sh", "--check"],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 1
    assert "podman-compose" in result.stdout


def test_prerequisites_accepts_a_working_podman_compose_provider(tmp_path):
    commands = tmp_path / "bin"
    commands.mkdir()
    _mock_dirname(commands)
    for name in ("uv", "jq", "curl", "ip", "sudo"):
        _mock_command(commands, name)
    yq = commands / "yq"
    yq.write_text(
        "#!/usr/bin/bash\nprintf '%s\\n' 'yq (https://github.com/mikefarah/yq/) version v4.45.1'\n"
    )
    yq.chmod(yq.stat().st_mode | stat.S_IXUSR)
    podman = commands / "podman"
    podman.write_text(
        "#!/usr/bin/bash\n"
        "case \"$*\" in\n"
        "  'compose version') exit 0 ;;\n"
        "  *) exit 0 ;;\n"
        "esac\n"
    )
    podman.chmod(podman.stat().st_mode | stat.S_IXUSR)

    environment = os.environ | {"PATH": str(commands)}
    result = subprocess.run(
        ["/usr/bin/bash", "setup/prerequisites.sh", "--check"],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "installed  podman-compose" in result.stdout


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
        ["/usr/bin/bash", "setup/setup.sh"],
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
    tmp_path: pathlib.Path,
    selection: str,
    *,
    config_text: str | None = None,
    vram_used_mib: int = 2048,
    resources_failure: bool = False,
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

    resources_case = (
        "printf '%s\\n' '{\"error\": \"host has 3 CPUs; more than 3 are required\"}'; exit 1"
        if resources_failure
        else "printf '%s\\n' '{\"llm_server\": {\"cpus\": 5, \"memory_mib\": 27648}, \"omniroute\": {\"cpus\": 1, \"memory_mib\": 1024}}'"
    )
    uv = commands / "uv"
    uv.write_text(
        "#!/usr/bin/bash\n"
        "printf 'uv %s\\n' \"$*\" >> \"$CALLS\"\n"
        "case \"$*\" in\n"
        f"  *' detect') printf '%s\\n' '{{\"gpus\":[{{\"card\":\"card0\",\"pci_address\":\"0000:03:00.0\",\"vram_total_mib\":16384,\"vram_used_mib\":{vram_used_mib},\"render_node\":\"renderD128\",\"connected_outputs\":[]}}]}}' ;;\n"
        "  *' models list') printf '%s\\n' '{\"models\":[{\"alias\":\"gemma4\",\"label\":\"Gemma 4\",\"parameters\":\"12B\",\"quantization\":\"Q4_K_M\",\"size_bytes\":7660000000,\"enabled\":true},{\"alias\":\"ornith\",\"label\":\"Ornith\",\"parameters\":\"9B\",\"quantization\":\"Q4_K_M\",\"size_bytes\":5600000000,\"enabled\":false}]}' ;;\n"
        "  *' models select '*)\n"
        "    for arg in \"$@\"; do selected_alias=\"$arg\"; done\n"
        "    SELECTED_ALIAS=\"$selected_alias\" \"$REAL_YQ\" -i \\\n"
        "      '.models[] |= (.enabled = (.alias == strenv(SELECTED_ALIAS))) | .runtime.models_max = 1' \\\n"
        "      \"$CONFIG_PATH_TEST\"\n"
        "    printf '%s\\n' '{\"models_max\":1}' ;;\n"
        "  *' validate-gguf'*) printf '%s\\n' '{\"results\":[]}' ;;\n"
        "  *' budget '*) printf '%s\\n' '{\"available_mib\":12000,\"required_mib\":10000}' ;;\n"
        "  *' list-devices '*) printf '%s\\n' '{\"devices\":[{\"id\":\"Vulkan0\",\"name\":\"Integrated GPU\",\"total_mib\":8192},{\"id\":\"Vulkan1\",\"name\":\"Fallback Radeon: \\\"safe\\\"\",\"total_mib\":32768}]}' ;;\n"
        "  *' resources')\n"
        f"    {resources_case} ;;\n"
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
        config_text
        or (
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
    )
    environment = os.environ | {
        "CALLS": str(calls),
        "CONFIG_PATH_TEST": str(config),
        "HOME": str(tmp_path / "home"),
        "LLM_ENV_CONFIG": str(config),
        "LLM_ENV_MODELS_DIR": str(tmp_path / "models"),
        "PATH": f"{commands}:/usr/bin:/bin",
        "REAL_UV": shutil.which("uv") or "uv",
        "REAL_YQ": real_yq,
    }
    result = subprocess.run(
        ["/usr/bin/bash", "setup/setup.sh"],
        cwd=ROOT,
        env=environment,
        # A leading blank line answers Step 1's "enable llm-server?" prompt
        # with its default (Y), so every pre-existing `selection` string
        # keeps driving Steps 2-8's prompts exactly as before.
        input="\n" + selection,
        text=True,
        capture_output=True,
        check=False,
    )
    return result, calls, config


def run_setup_no_gpu(
    tmp_path: pathlib.Path,
    *,
    no_gpu_env: str = "1",
    config_text: str | None = None,
) -> tuple[subprocess.CompletedProcess[str], pathlib.Path, pathlib.Path]:
    """Run setup with LLM_ENV_NO_GPU set, stubbing only what Steps 1 and 8 need
    -- Steps 2-7 must be skipped entirely, so no GPU/model/Vulkan stubs exist."""
    real_yq = shutil.which("yq")
    assert real_yq is not None

    commands = tmp_path / "bin"
    commands.mkdir()
    calls = tmp_path / "calls"
    for name in ("ip", "git", "shellcheck", "curl", "podman"):
        _mock_command(commands, name)

    uv = commands / "uv"
    uv.write_text(
        "#!/usr/bin/bash\n"
        "printf 'uv %s\\n' \"$*\" >> \"$CALLS\"\n"
        "case \"$*\" in\n"
        "  *' resources') printf '%s\\n' "
        "'{\"omniroute\": {\"cpus\": 1, \"memory_mib\": 1024}, "
        "\"llm_server\": {\"cpus\": 0, \"memory_mib\": 0}}' ;;\n"
        "esac\n"
    )
    uv.chmod(uv.stat().st_mode | stat.S_IXUSR)

    yq = commands / "yq"
    yq.write_text("#!/usr/bin/bash\nexec \"$REAL_YQ\" \"$@\"\n")
    yq.chmod(yq.stat().st_mode | stat.S_IXUSR)

    config = tmp_path / "models.yml"
    config.write_text(
        config_text
        or (
            "gpu: {}\n"
            "runtime:\n"
            "  models_max: 0\n"
            "models: []\n"
        )
    )
    environment = os.environ | {
        "CALLS": str(calls),
        "CONFIG_PATH_TEST": str(config),
        "HOME": str(tmp_path / "home"),
        "LLM_ENV_CONFIG": str(config),
        "LLM_ENV_MODELS_DIR": str(tmp_path / "models"),
        "LLM_ENV_NO_GPU": no_gpu_env,
        "PATH": f"{commands}:/usr/bin:/bin",
        "REAL_UV": shutil.which("uv") or "uv",
        "REAL_YQ": real_yq,
    }
    result = subprocess.run(
        ["/usr/bin/bash", "setup/setup.sh"],
        cwd=ROOT,
        env=environment,
        input="\n",
        text=True,
        capture_output=True,
        check=False,
    )
    return result, calls, config


def test_setup_persists_llm_server_disabled_via_env_var(tmp_path: pathlib.Path) -> None:
    result, _calls, config = run_setup_no_gpu(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert yq_value(config, ".llm_server.enabled") == "false"


def test_setup_no_gpu_skips_gpu_model_and_vulkan_steps(tmp_path: pathlib.Path) -> None:
    result, calls, _config = run_setup_no_gpu(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    recorded = calls.read_text() if calls.exists() else ""
    assert "detect" not in recorded
    assert "--list-devices" not in recorded
    assert "(skipped -- llm-server disabled)" in result.stdout


def test_setup_no_gpu_still_reserves_omniroute_resources(tmp_path: pathlib.Path) -> None:
    result, _calls, config = run_setup_no_gpu(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert yq_value(config, ".resources.omniroute.cpus") == "1"
    assert yq_value(config, ".resources.omniroute.memory_mib") == "1024"


def test_setup_defaults_to_gpu_enabled_when_no_gpu_env_unset(tmp_path: pathlib.Path) -> None:
    # Steps 2-7 are not stubbed here (this helper only stubs what the
    # no-GPU path needs) so setup is expected to fail once it reaches GPU
    # detection -- what matters is that Step 1 already persisted the
    # enabled decision before that happens.
    result, _calls, config = run_setup_no_gpu(tmp_path, no_gpu_env="0")
    assert yq_value(config, ".llm_server.enabled") == "true"


def test_setup_fails_when_the_budget_is_infeasible(tmp_path: pathlib.Path) -> None:
    """Step 7 must stop setup on an infeasible budget, the same way
    scripts/start.sh already stops `make start` -- a config that reports
    'available < required' must never reach Step 8 and be reported as
    'Setup complete'."""
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
        "  *' detect') printf '%s\\n' '{\"gpus\":[{\"card\":\"card0\",\"pci_address\":\"0000:03:00.0\",\"vram_total_mib\":16384,\"vram_used_mib\":2048,\"render_node\":\"renderD128\",\"connected_outputs\":[]}]}' ;;\n"
        "  *' models list') printf '%s\\n' '{\"models\":[{\"alias\":\"gemma4\",\"label\":\"Gemma 4\",\"parameters\":\"12B\",\"quantization\":\"Q4_K_M\",\"size_bytes\":7660000000,\"enabled\":true}]}' ;;\n"
        "  *' models select '*)\n"
        "    for arg in \"$@\"; do selected_alias=\"$arg\"; done\n"
        "    SELECTED_ALIAS=\"$selected_alias\" \"$REAL_YQ\" -i \\\n"
        "      '.models[] |= (.enabled = (.alias == strenv(SELECTED_ALIAS))) | .runtime.models_max = 1' \\\n"
        "      \"$CONFIG_PATH_TEST\"\n"
        "    printf '%s\\n' '{\"models_max\":1}' ;;\n"
        "  *' validate-gguf'*) printf '%s\\n' '{\"results\":[]}' ;;\n"
        "  *' budget '*)\n"
        "    printf '%s\\n' '{\"available_mib\":9000,\"required_mib\":12000,\"shortfall_mib\":3000,\"vram_feasible\":false,\"ram_feasible\":true,\"remedies\":[\"reduce ctx_size\"]}'\n"
        "    exit 1 ;;\n"
        "  *' list-devices '*) printf '%s\\n' '{\"devices\":[{\"id\":\"Vulkan0\",\"name\":\"Integrated GPU\",\"total_mib\":16384}]}' ;;\n"
        "  *' resources') printf '%s\\n' '{\"llm_server\": {\"cpus\": 5, \"memory_mib\": 27648}, \"omniroute\": {\"cpus\": 1, \"memory_mib\": 1024}}' ;;\n"
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
        "case \"$*\" in *'--list-devices'*) printf '%s\\n' 'Vulkan0: Integrated GPU (16384 MiB, 16000 MiB free)' ;; esac\n"
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
    )
    environment = os.environ | {
        "CALLS": str(calls),
        "CONFIG_PATH_TEST": str(config),
        "HOME": str(tmp_path / "home"),
        "LLM_ENV_CONFIG": str(config),
        "LLM_ENV_MODELS_DIR": str(tmp_path / "models"),
        "PATH": f"{commands}:/usr/bin:/bin",
        "REAL_UV": shutil.which("uv") or "uv",
        "REAL_YQ": real_yq,
    }
    result = subprocess.run(
        ["/usr/bin/bash", "setup/setup.sh"],
        cwd=ROOT,
        env=environment,
        input="1\n1\n1\n",
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "VRAM short by 3000 MiB" in result.stdout
    assert "reduce ctx_size" in result.stdout
    assert "Setup complete" not in result.stdout
    assert not any(
        call.rstrip().endswith(" resources") for call in calls.read_text().splitlines()
    )


def test_setup_backfills_ornith_35b_into_a_pre_existing_config(
    tmp_path: pathlib.Path,
) -> None:
    """A config written by `make setup` before ornith-35b existed in the
    template has no ornith-35b alias at all -- migrate_config() only
    backfills missing top-level keys, never new model list entries. Step 3
    ("Selecting models") must add it from the shipped template, the same
    way it already deletes the legacy 'openhermes' alias, so the model is
    selectable without the user hand-editing their config."""
    config_text = (
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
        "    ctx_size: 4096\n"  # deliberately non-default, to prove it survives untouched
    )
    result, _, config = run_setup_with_numbered_selection(
        tmp_path, "1\n1\n1\n", config_text=config_text
    )

    assert result.returncode == 0, result.stderr
    assert yq_value(config, '.models[] | select(.alias == "ornith-35b") | .alias') == "ornith-35b"
    assert yq_value(config, '.models[] | select(.alias == "ornith-35b") | .n_cpu_moe') == "28"
    # The pre-existing ornith entry's hand-set value must survive untouched
    # -- this step only ADDS the missing alias, never edits an existing one.
    assert yq_value(config, '.models[] | select(.alias == "ornith") | .ctx_size') == "4096"


def test_setup_creates_the_prune_marker_in_the_models_directory(
    tmp_path: pathlib.Path,
) -> None:
    """scripts/prune.sh (this task) refuses to run without this marker --
    setup must actually create it during Step 4's download, not just
    document the convention."""
    result, _, _ = run_setup_with_numbered_selection(tmp_path, "1\n1\n1\n")
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "models" / ".llm-env-managed").exists()


def test_setup_gpu_rows_include_measured_used_and_free_vram(
    tmp_path: pathlib.Path,
) -> None:
    """The GPU menu must disclose the total, used, and free VRAM before selection."""
    result, _, _ = run_setup_with_numbered_selection(tmp_path, "1\n1\n1\n")

    assert result.returncode == 0, result.stderr
    assert "16384 MiB total" in result.stdout
    assert "2048 MiB used" in result.stdout
    assert "14336 MiB free" in result.stdout
    assert "selected 0000:03:00.0 with 16384 MiB total, 2048 MiB used, 14336 MiB free" in result.stdout


def test_setup_persists_a_vram_ceiling_computed_from_total_vram_at_setup_time(
    tmp_path: pathlib.Path,
) -> None:
    """Ceiling = pct * total at the moment setup ran, not of free VRAM."""
    result, _, config = run_setup_with_numbered_selection(tmp_path, "1\n1\n2\n")

    assert result.returncode == 0, result.stderr
    # fixture GPU: vram_total_mib=16384, default pct 95
    # 16384 * 95 / 100 = 15564.8 -> round to 15565
    assert yq_value(config, ".gpu.vram_budget_ceiling_mib") == "15565"


def test_setup_vram_ceiling_does_not_double_count_live_vram_usage(
    tmp_path: pathlib.Path,
) -> None:
    """Regression guard for the double-counting fix: the ceiling must derive
    from vram_total alone, not vram_total - vram_used. Live VRAM contention
    is already handled separately, downstream, by compute_budget()'s
    `reserve`. Changing vram_used_mib in the fixture (with vram_total_mib
    held fixed) must not change the computed ceiling."""
    tmp_path_low = tmp_path / "low"
    tmp_path_low.mkdir()
    tmp_path_high = tmp_path / "high"
    tmp_path_high.mkdir()
    result_low_usage, _, config_low = run_setup_with_numbered_selection(
        tmp_path_low, "1\n1\n2\n", vram_used_mib=0
    )
    result_high_usage, _, config_high = run_setup_with_numbered_selection(
        tmp_path_high, "1\n1\n2\n", vram_used_mib=8000
    )

    assert result_low_usage.returncode == 0, result_low_usage.stderr
    assert result_high_usage.returncode == 0, result_high_usage.stderr
    # fixture GPU: vram_total_mib=16384 (unchanged), default pct 95
    # 16384 * 95 / 100 = 15564.8 -> round to 15565, regardless of vram_used
    assert yq_value(config_low, ".gpu.vram_budget_ceiling_mib") == "15565"
    assert yq_value(config_high, ".gpu.vram_budget_ceiling_mib") == "15565"


def test_setup_persists_a_vram_ceiling_using_the_configured_pct_not_the_default(
    tmp_path: pathlib.Path,
) -> None:
    """Regression guard: a hardcoded 95 instead of reading
    gpu.vram_budget_ceiling_pct would still pass every other ceiling test in
    this plan, since they all use the default 95%."""
    config_text = (
        "gpu:\n"
        "  vram_budget_ceiling_pct: 80\n"
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
    result, _, config = run_setup_with_numbered_selection(
        tmp_path, "1\n1\n2\n", config_text=config_text
    )

    assert result.returncode == 0, result.stderr
    # fixture GPU: vram_total_mib=16384, pct 80 (not the default 95)
    # 16384 * 80 / 100 = 13107.2 -> round to 13107
    assert yq_value(config, ".gpu.vram_budget_ceiling_mib") == "13107"


def test_setup_floors_the_vram_ceiling_at_the_configured_floor(
    tmp_path: pathlib.Path,
) -> None:
    """A tiny configured pct must not leave llm-server planning against a
    near-zero VRAM ceiling — floored at the configured floor (30% of total
    here, matching migrate_config's real default). Unlike the old fixed-MiB
    floor, a percentage-of-total floor can never exceed vram_total_mib."""
    config_text = (
        "gpu:\n"
        "  vram_budget_ceiling_pct: 1\n"
        "  vram_budget_ceiling_floor_pct: 30\n"
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
    result, _, config = run_setup_with_numbered_selection(
        tmp_path, "1\n1\n2\n", config_text=config_text
    )

    assert result.returncode == 0, result.stderr
    # fixture GPU: vram_total_mib=16384, pct 1
    # 16384 * 1 / 100 = 163.84 -> round 164, floored up to
    # 16384 * 30 / 100 = 4915.2 -> round 4915, which is <= vram_total_mib
    # by construction.
    assert yq_value(config, ".gpu.vram_budget_ceiling_mib") == "4915"


def test_setup_floors_the_vram_ceiling_at_the_code_default_when_unconfigured(
    tmp_path: pathlib.Path,
) -> None:
    """Without an explicit vram_budget_ceiling_floor_pct in config (this
    fixture's config_text never sets it, and the uv stub used by
    run_setup_with_numbered_selection doesn't run real migration to
    backfill the 30% default either), setup.sh's `// 20` fallback is the
    only thing preventing a near-zero VRAM ceiling."""
    config_text = (
        "gpu:\n"
        "  vram_budget_ceiling_pct: 1\n"
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
    result, _, config = run_setup_with_numbered_selection(
        tmp_path, "1\n1\n2\n", config_text=config_text
    )

    assert result.returncode == 0, result.stderr
    # fixture GPU: vram_total_mib=16384, pct 1
    # 16384 * 1 / 100 = 163.84 -> round 164, floored up to the code-level
    # default 16384 * 20 / 100 = 3276.8 -> round 3277 (no
    # vram_budget_ceiling_floor_pct in config)
    assert yq_value(config, ".gpu.vram_budget_ceiling_mib") == "3277"


def test_setup_selects_zero_match_vulkan_device_and_persists_config(
    tmp_path: pathlib.Path,
) -> None:
    """A zero-VRAM match must require and persist an explicit llama device choice."""
    result, calls, config = run_setup_with_numbered_selection(tmp_path, "1\n1\n2\n")

    assert result.returncode == 0, result.stderr
    assert "Integrated GPU" in result.stdout
    assert 'Fallback Radeon: "safe"' in result.stdout
    call_log = calls.read_text()
    assert "models select gemma4" in call_log
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
        "vram_budget_ceiling_mib": 15565,
    }
    assert persisted["runtime"]["models_max"] == 1
    assert {model["alias"]: model["enabled"] for model in persisted["models"]} == {
        "gemma4": True,
        "ornith": False,
        "ornith-35b": False,
    }


def test_setup_writes_computed_resource_limits(tmp_path: pathlib.Path) -> None:
    """Setup must persist llmenv resources output into resources.llm_server."""
    _, _, config = run_setup_with_numbered_selection(tmp_path, "1\n1\n2\n")

    assert yq_value(config, ".resources.llm_server.cpus") == "5"
    assert yq_value(config, ".resources.llm_server.memory_mib") == "27648"


def test_setup_writes_computed_omniroute_resource_limits(tmp_path: pathlib.Path) -> None:
    result, _, config = run_setup_with_numbered_selection(tmp_path, "1\n1\n2\n")
    assert result.returncode == 0, result.stdout + result.stderr
    cfg = yaml.safe_load(config.read_text())
    assert cfg["resources"]["omniroute"] == {"cpus": 1, "memory_mib": 1024}


def test_setup_fails_instead_of_leaving_resources_uncapped(
    tmp_path: pathlib.Path,
) -> None:
    """A host too small to reserve the fixed floors must stop setup, not silently
    leave llm-server uncapped (render_compose treats 0 as no explicit limit)."""
    result, _, config = run_setup_with_numbered_selection(
        tmp_path, "1\n1\n2\n", resources_failure=True
    )

    assert result.returncode != 0
    assert "host has 3 CPUs" in result.stderr
    assert yq_value(config, ".resources.llm_server.cpus // \"\"") in ("", "null")


def test_setup_rejects_invalid_numbered_model_selection_before_download(
    tmp_path: pathlib.Path,
) -> None:
    """Out-of-range model indexes must stop setup before any download starts."""
    result, calls, _ = run_setup_with_numbered_selection(tmp_path, "1\n3\n")

    assert result.returncode != 0
    assert "curl " not in calls.read_text()


def test_setup_rejects_comma_separated_model_selection(
    tmp_path: pathlib.Path,
) -> None:
    """Only one model may be enabled by the guided setup flow — VRAM for two
    models at once was never guaranteed to fit."""
    result, calls, _ = run_setup_with_numbered_selection(tmp_path, "1\n1,2\n2\n")

    assert result.returncode != 0
    assert "curl " not in calls.read_text()


def test_reverse_setup_selection_drives_client_model_order(
    tmp_path: pathlib.Path,
) -> None:
    setup_result, _calls, config = run_setup_with_numbered_selection(
        tmp_path, "1\n1\n2\n", config_text=VALID_AGENT_SETUP_CONFIG
    )
    assert setup_result.returncode == 0, setup_result.stderr

    real_uv = shutil.which("uv")
    assert real_uv is not None
    reorder = subprocess.run(
        [real_uv, "run", str(ROOT / "llmenv.py"), "--config", str(config),
         "models", "select", "ornith", "gemma4"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert reorder.returncode == 0, reorder.stderr

    assert [model["alias"] for model in json.loads(
        subprocess.run(
            [shutil.which("yq") or "yq", "-o=json", ".", str(config)],
            text=True,
            capture_output=True,
            check=True,
        ).stdout
    # setup/setup.sh's Task 7 backfill step adds the missing ornith-35b
    # alias from the shipped template before model selection ever runs
    # (VALID_AGENT_SETUP_CONFIG has no ornith-35b entry) -- it lands after
    # every pre-existing alias, so it's last here too.
    )["models"]] == ["ornith", "gemma4", "old-model", "ornith-35b"]

    result, _, pi_path, settings_path, opencode_paths, state_path = (
        run_setup_local_llm_agents(tmp_path, config_text=config.read_text())
    )

    assert result.returncode == 0, result.stderr
    pi_provider = json.loads(pi_path.read_text())["providers"]["local-llm-env"]
    assert [model["id"] for model in pi_provider["models"]] == [
        "llama-cpp/ornith",
        "llama-cpp/gemma4",
    ]
    assert json.loads(settings_path.read_text())["enabledModels"] == [
        "local-llm-env/llama-cpp/ornith",
        "local-llm-env/llama-cpp/gemma4",
    ]
    opencode_provider = json.loads(opencode_paths[2].read_text())["provider"][
        "local-llm-env"
    ]
    assert list(opencode_provider["models"]) == ["llama-cpp/ornith", "llama-cpp/gemma4"]
    assert json.loads(state_path.read_text())["favorite"][:2] == [
        {"providerID": "local-llm-env", "modelID": "llama-cpp/ornith"},
        {"providerID": "local-llm-env", "modelID": "llama-cpp/gemma4"},
    ]


def run_gpu_status_with_stubs(
    tmp_path: pathlib.Path,
    *,
    processes_json: str = '{"processes":[]}',
    config_text: str | None = None,
    input_text: str = "",
    system_applications_dir: pathlib.Path | None = None,
    extra_env: dict[str, str] | None = None,
) -> tuple[subprocess.CompletedProcess[str], pathlib.Path, pathlib.Path]:
    """Run gpu-status.sh against a stubbed llmenv/detect/budget/processes pipeline."""
    commands = tmp_path / "bin"
    commands.mkdir(exist_ok=True)

    uv = commands / "uv"
    uv.write_text(
        "#!/usr/bin/bash\n"
        "case \"$*\" in\n"
        "  *' detect')\n"
        "    printf '%s\\n' '{\"gpus\":["
        "{\"card\":\"card1\",\"pci_address\":\"0000:03:00.0\",\"vram_total_mib\":16384,"
        "\"vram_used_mib\":2048,\"render_node\":\"renderD128\",\"connected_outputs\":[]},"
        "{\"card\":\"card0\",\"pci_address\":\"0000:0e:00.0\",\"vram_total_mib\":512,"
        "\"vram_used_mib\":51,\"render_node\":\"renderD129\",\"connected_outputs\":[]}]}' ;;\n"
        "  *' budget '*) printf '%s\\n' '{\"available_mib\":9000,\"required_mib\":6000,\"feasible\":true}' ;;\n"
        f"  *' processes-on-render-node '*) printf '%s\\n' '{processes_json}' ;;\n"
        "esac\n"
    )
    uv.chmod(uv.stat().st_mode | stat.S_IXUSR)

    real_yq = shutil.which("yq")
    assert real_yq is not None
    yq = commands / "yq"
    yq.write_text(f"#!/usr/bin/bash\nexec {real_yq} \"$@\"\n")
    yq.chmod(yq.stat().st_mode | stat.S_IXUSR)

    home = tmp_path / "home"
    config = tmp_path / "models.yml"
    config.write_text(
        config_text
        or 'gpu:\n  pci_address: "0000:03:00.0"\n  vram_budget_ceiling_mib: 15565\n'
    )

    environment = os.environ | {
        "HOME": str(home),
        "LLM_ENV_CONFIG": str(config),
        "LLM_ENV_MODELS_DIR": str(tmp_path / "models"),
        "PATH": f"{commands}:/usr/bin:/bin",
    }
    if system_applications_dir is not None:
        environment["LLM_ENV_SYSTEM_APPLICATIONS_DIR"] = str(system_applications_dir)
    if extra_env:
        environment |= extra_env

    result = subprocess.run(
        ["/usr/bin/bash", "scripts/gpu-status.sh"],
        cwd=ROOT,
        env=environment,
        input=input_text,
        text=True,
        capture_output=True,
        check=False,
    )
    return result, config, home


def test_gpu_status_reports_total_used_ceiling_and_headroom(tmp_path: pathlib.Path) -> None:
    result, _, _ = run_gpu_status_with_stubs(tmp_path)
    assert result.returncode == 0
    assert "total VRAM:         16384 MiB" in result.stdout
    assert "used (system-wide): 2048 MiB" in result.stdout
    assert "llm-env ceiling:    15565 MiB" in result.stdout
    assert "budget headroom:    9000 MiB" in result.stdout


def test_gpu_status_reports_uncapped_ceiling_as_uncapped_not_zero(tmp_path: pathlib.Path) -> None:
    result, _, _ = run_gpu_status_with_stubs(
        tmp_path,
        config_text='gpu:\n  pci_address: "0000:03:00.0"\n  vram_budget_ceiling_mib: 0\n',
    )
    assert "llm-env ceiling:    uncapped" in result.stdout


def test_gpu_status_migrates_the_config_before_reading_the_ceiling(tmp_path: pathlib.Path) -> None:
    """A config that predates gpu.vram_budget_ceiling_mib (a real,
    anticipated upgrade path -- pylib/config.py's migrate_config()
    persists gpu.setdefault("vram_budget_ceiling_mib", ...) to disk) must
    not make the script read a raw, unmigrated field via `yq`'s `// 0`
    default and print "uncapped" while the very next line (budget
    headroom, via `llmenv budget`, which migrates in-memory by default
    through pylib/config.py's load_config()) reflects the real, non-zero
    ceiling -- two adjacent lines of the same diagnostic must never
    disagree about whether a cap exists. This uv stub's migrate-config
    case actually rewrites $LLM_ENV_CONFIG on disk with `yq -i`, mirroring
    cmd_migrate_config's real persistence behavior, so the test proves
    gpu-status.sh calls migrate_config_file (which shells out to
    `llmenv migrate-config`) before reading the ceiling with `yq` -- not
    merely that it tolerates the call being made."""
    commands = tmp_path / "bin"
    commands.mkdir()
    uv = commands / "uv"
    uv.write_text(
        "#!/usr/bin/bash\n"
        "case \"$*\" in\n"
        "  *' migrate-config')\n"
        "    yq -i '.gpu.vram_budget_ceiling_mib = 15565' \"$LLM_ENV_CONFIG\"\n"
        "    printf '{\"written\":true,\"path\":\"%s\"}\\n' \"$LLM_ENV_CONFIG\" ;;\n"
        "  *' detect')\n"
        "    printf '%s\\n' '{\"gpus\":["
        "{\"card\":\"card1\",\"pci_address\":\"0000:03:00.0\",\"vram_total_mib\":16384,"
        "\"vram_used_mib\":2048,\"render_node\":\"renderD128\",\"connected_outputs\":[]},"
        "{\"card\":\"card0\",\"pci_address\":\"0000:0e:00.0\",\"vram_total_mib\":512,"
        "\"vram_used_mib\":51,\"render_node\":\"renderD129\",\"connected_outputs\":[]}]}' ;;\n"
        "  *' budget '*) printf '%s\\n' '{\"available_mib\":9000,\"required_mib\":6000,\"feasible\":true}' ;;\n"
        "  *' processes-on-render-node '*) printf '%s\\n' '{\"processes\":[]}' ;;\n"
        "esac\n"
    )
    uv.chmod(uv.stat().st_mode | stat.S_IXUSR)
    real_yq = shutil.which("yq")
    assert real_yq is not None
    yq = commands / "yq"
    yq.write_text(f"#!/usr/bin/bash\nexec {real_yq} \"$@\"\n")
    yq.chmod(yq.stat().st_mode | stat.S_IXUSR)

    config = tmp_path / "models.yml"
    config.write_text('gpu:\n  pci_address: "0000:03:00.0"\n')  # no vram_budget_ceiling_mib at all

    environment = os.environ | {
        "HOME": str(tmp_path / "home"),
        "LLM_ENV_CONFIG": str(config),
        "LLM_ENV_MODELS_DIR": str(tmp_path / "models"),
        "PATH": f"{commands}:/usr/bin:/bin",
    }
    result = subprocess.run(
        ["/usr/bin/bash", "scripts/gpu-status.sh"],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0
    assert "llm-env ceiling:    15565 MiB" in result.stdout
    assert "budget headroom:    9000 MiB" in result.stdout


def test_gpu_status_prints_only_the_top_three_processes_by_vram(tmp_path: pathlib.Path) -> None:
    processes = json.dumps(
        {
            "processes": [
                {"pid": 1, "comm": "a", "exe": "a", "vram_mib": 100},
                {"pid": 2, "comm": "b", "exe": "b", "vram_mib": 400},
                {"pid": 3, "comm": "c", "exe": "c", "vram_mib": 200},
                {"pid": 4, "comm": "d", "exe": "d", "vram_mib": 300},
            ]
        }
    )
    result, _, _ = run_gpu_status_with_stubs(tmp_path, processes_json=processes)
    assert "400 MiB" in result.stdout
    assert "300 MiB" in result.stdout
    assert "200 MiB" in result.stdout
    assert "100 MiB" not in result.stdout
    b_index = result.stdout.index("400 MiB")
    d_index = result.stdout.index("300 MiB")
    c_index = result.stdout.index("200 MiB")
    assert b_index < d_index < c_index


def test_gpu_status_excludes_the_llm_env_stack_from_the_table(tmp_path: pathlib.Path) -> None:
    """Excludes by `comm` name, which is inherently approximate, by-name
    best-effort matching -- a legitimately-named user process called e.g.
    `podman` would be wrongly excluded too, a documented, accepted
    limitation, not fixed here. This is deliberately *not* a self-exclusion
    mechanism: a running bash script's own `comm` (as read from
    /proc/<pid>/comm by Task 1's processes_on_render_node()) is always
    `bash`, never the script's filename, so no name-based entry could ever
    match gpu-status.sh's own process -- and in practice this essentially
    never matters, since a plain bash script doing procfs reads does not
    hold an open fd on a DRM render node."""
    processes = json.dumps(
        {
            "processes": [
                {"pid": 1, "comm": "llama-server", "exe": "llama-server", "vram_mib": 9000},
                {"pid": 2, "comm": "conmon", "exe": "conmon", "vram_mib": 10},
                {"pid": 3, "comm": "podman", "exe": "podman", "vram_mib": 5},
                {"pid": 4, "comm": "firefox", "exe": "firefox", "vram_mib": 500},
                {"pid": 5, "comm": "man", "exe": "man", "vram_mib": 50},
            ]
        }
    )
    result, _, _ = run_gpu_status_with_stubs(tmp_path, processes_json=processes)
    assert "firefox" in result.stdout
    assert "llama-server" not in result.stdout
    assert "conmon" not in result.stdout
    assert "podman" not in result.stdout
    # `man` is a literal substring of the excluded name `podman`, but the
    # script's exclusion filter is an exact `comm` match (`. == $c`), not a
    # substring match (`inside()`). This locks in that exact-match
    # semantics: if the filter ever regressed to `inside()`, `man` would be
    # wrongly excluded here too.
    assert "man" in result.stdout


def test_gpu_status_exits_cleanly_with_no_other_processes(tmp_path: pathlib.Path) -> None:
    result, _, _ = run_gpu_status_with_stubs(tmp_path, processes_json='{"processes":[]}')
    assert result.returncode == 0
    assert "no other processes using the dGPU" in result.stdout


def test_gpu_status_requires_an_existing_config(tmp_path: pathlib.Path) -> None:
    result, config, _ = run_gpu_status_with_stubs(tmp_path)
    config.unlink()
    commands = tmp_path / "bin"
    environment = os.environ | {
        "HOME": str(tmp_path / "home"),
        "LLM_ENV_CONFIG": str(config),
        "LLM_ENV_MODELS_DIR": str(tmp_path / "models"),
        "PATH": f"{commands}:/usr/bin:/bin",
    }
    result = subprocess.run(
        ["/usr/bin/bash", "scripts/gpu-status.sh"],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0
    assert "no config found" in result.stderr


def test_gpu_status_requires_a_configured_gpu(tmp_path: pathlib.Path) -> None:
    result, _, _ = run_gpu_status_with_stubs(tmp_path, config_text="gpu: {}\n")
    assert result.returncode != 0
    assert "gpu.pci_address is not set" in result.stderr


def test_gpu_status_dies_clearly_when_detect_fails(tmp_path: pathlib.Path) -> None:
    commands = tmp_path / "bin"
    commands.mkdir()
    uv = commands / "uv"
    uv.write_text("#!/usr/bin/bash\ncase \"$*\" in\n  *' detect') exit 1 ;;\nesac\n")
    uv.chmod(uv.stat().st_mode | stat.S_IXUSR)
    real_yq = shutil.which("yq")
    assert real_yq is not None
    yq = commands / "yq"
    yq.write_text(f"#!/usr/bin/bash\nexec {real_yq} \"$@\"\n")
    yq.chmod(yq.stat().st_mode | stat.S_IXUSR)
    config = tmp_path / "models.yml"
    config.write_text('gpu:\n  pci_address: "0000:03:00.0"\n  vram_budget_ceiling_mib: 15565\n')
    environment = os.environ | {
        "HOME": str(tmp_path / "home"),
        "LLM_ENV_CONFIG": str(config),
        "LLM_ENV_MODELS_DIR": str(tmp_path / "models"),
        "PATH": f"{commands}:/usr/bin:/bin",
    }
    result = subprocess.run(
        ["/usr/bin/bash", "scripts/gpu-status.sh"],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0
    assert "could not detect GPUs" in result.stderr


def test_gpu_status_shows_headroom_even_when_budget_is_infeasible(tmp_path: pathlib.Path) -> None:
    """cmd_budget emits a full, valid JSON payload (including available_mib)
    even when the budget doesn't fit, but exits nonzero in that case
    (existing, correct, diagnostic-only behavior). gpu-status.sh must still
    show the headroom number in exactly this scenario -- an infeasible
    budget is when an operator most needs to see it. Builds its own uv
    stub directly (rather than via run_gpu_status_with_stubs) so the budget
    case can both print a payload and exit 1."""
    commands = tmp_path / "bin"
    commands.mkdir()
    uv = commands / "uv"
    uv.write_text(
        "#!/usr/bin/bash\n"
        "case \"$*\" in\n"
        "  *' detect')\n"
        "    printf '%s\\n' '{\"gpus\":["
        "{\"card\":\"card1\",\"pci_address\":\"0000:03:00.0\",\"vram_total_mib\":16384,"
        "\"vram_used_mib\":2048,\"render_node\":\"renderD128\",\"connected_outputs\":[]},"
        "{\"card\":\"card0\",\"pci_address\":\"0000:0e:00.0\",\"vram_total_mib\":512,"
        "\"vram_used_mib\":51,\"render_node\":\"renderD129\",\"connected_outputs\":[]}]}' ;;\n"
        "  *' budget '*)\n"
        "    printf '%s\\n' '{\"available_mib\":9000,\"required_mib\":12000,\"feasible\":false}'\n"
        "    exit 1 ;;\n"
        "  *' processes-on-render-node '*) printf '%s\\n' '{\"processes\":[]}' ;;\n"
        "esac\n"
    )
    uv.chmod(uv.stat().st_mode | stat.S_IXUSR)
    real_yq = shutil.which("yq")
    assert real_yq is not None
    yq = commands / "yq"
    yq.write_text(f"#!/usr/bin/bash\nexec {real_yq} \"$@\"\n")
    yq.chmod(yq.stat().st_mode | stat.S_IXUSR)

    config = tmp_path / "models.yml"
    config.write_text('gpu:\n  pci_address: "0000:03:00.0"\n  vram_budget_ceiling_mib: 15565\n')
    environment = os.environ | {
        "HOME": str(tmp_path / "home"),
        "LLM_ENV_CONFIG": str(config),
        "LLM_ENV_MODELS_DIR": str(tmp_path / "models"),
        "PATH": f"{commands}:/usr/bin:/bin",
    }
    result = subprocess.run(
        ["/usr/bin/bash", "scripts/gpu-status.sh"],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert "budget headroom:    9000 MiB" in result.stdout


def test_gpu_status_shows_headroom_unavailable_on_a_budget_error(tmp_path: pathlib.Path) -> None:
    """cmd_budget can also fail outright (e.g. a configured GGUF file is
    missing) and emit `{"error": "..."}` -- a non-empty JSON object with no
    `available_mib` key at all -- while exiting 1. gpu-status.sh must fall
    through to the "unavailable" default in exactly this scenario, not
    render the literal string "null" by blindly interpolating a missing
    field."""
    commands = tmp_path / "bin"
    commands.mkdir()
    uv = commands / "uv"
    uv.write_text(
        "#!/usr/bin/bash\n"
        "case \"$*\" in\n"
        "  *' detect')\n"
        "    printf '%s\\n' '{\"gpus\":["
        "{\"card\":\"card1\",\"pci_address\":\"0000:03:00.0\",\"vram_total_mib\":16384,"
        "\"vram_used_mib\":2048,\"render_node\":\"renderD128\",\"connected_outputs\":[]},"
        "{\"card\":\"card0\",\"pci_address\":\"0000:0e:00.0\",\"vram_total_mib\":512,"
        "\"vram_used_mib\":51,\"render_node\":\"renderD129\",\"connected_outputs\":[]}]}' ;;\n"
        "  *' budget '*)\n"
        "    printf '%s\\n' '{\"error\": \"some failure\"}'\n"
        "    exit 1 ;;\n"
        "  *' processes-on-render-node '*) printf '%s\\n' '{\"processes\":[]}' ;;\n"
        "esac\n"
    )
    uv.chmod(uv.stat().st_mode | stat.S_IXUSR)
    real_yq = shutil.which("yq")
    assert real_yq is not None
    yq = commands / "yq"
    yq.write_text(f"#!/usr/bin/bash\nexec {real_yq} \"$@\"\n")
    yq.chmod(yq.stat().st_mode | stat.S_IXUSR)

    config = tmp_path / "models.yml"
    config.write_text('gpu:\n  pci_address: "0000:03:00.0"\n  vram_budget_ceiling_mib: 15565\n')
    environment = os.environ | {
        "HOME": str(tmp_path / "home"),
        "LLM_ENV_CONFIG": str(config),
        "LLM_ENV_MODELS_DIR": str(tmp_path / "models"),
        "PATH": f"{commands}:/usr/bin:/bin",
    }
    result = subprocess.run(
        ["/usr/bin/bash", "scripts/gpu-status.sh"],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert "budget headroom:    unavailable" in result.stdout
    assert "null" not in result.stdout


def _desktop_file(directory: pathlib.Path, filename: str, exec_line: str) -> pathlib.Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
    path.write_text(f"[Desktop Entry]\nType=Application\nName=Test App\nExec={exec_line}\nIcon=test\n")
    return path


def test_gpu_status_prompts_once_for_all_flagged_processes(tmp_path: pathlib.Path) -> None:
    """A single combined confirmation covers every flagged process, never
    one prompt per process. This can't be proven by asserting on the
    prompt's literal text: under piped, non-terminal stdin (exactly how
    every test in this file invokes the script), bash's `read -rp` prompt
    is written to neither stdout nor stderr at all -- per the bash manual,
    "the prompt is displayed only if input is coming from a terminal."
    (This codebase's own `test_cleanup_confirmation_prompt_...` in this
    file follows the same rule: it never asserts on `clean.sh`'s `read -rp`
    prompt text.) Prove it by *effect* instead: two flagged processes, and
    exactly one "y" line for the (first) migration prompt. If the script
    prompted once per process, that single "y" would only accept the first
    process, leaving the second line ("n") to be consumed by a second
    per-process prompt -- and the later, independent default-preference
    prompt (Task 4) would then have no input left. Since both processes end
    up migrated from that one "y" line, and the run still exits cleanly
    after consuming the second line for the separate default-preference
    prompt, the confirmation must be single and combined.
    """
    processes = json.dumps(
        {
            "processes": [
                {"pid": 1, "comm": "firefox", "exe": "firefox", "vram_mib": 500},
                {"pid": 2, "comm": "thunderbird", "exe": "thunderbird", "vram_mib": 400},
            ]
        }
    )
    system_apps = tmp_path / "system-apps"
    _desktop_file(system_apps, "firefox.desktop", "firefox %u")
    _desktop_file(system_apps, "thunderbird.desktop", "thunderbird %u")
    result, _, home = run_gpu_status_with_stubs(
        tmp_path,
        processes_json=processes,
        input_text="y\nn\n",
        system_applications_dir=system_apps,
    )
    assert result.returncode == 0
    assert (home / ".local/share/applications/firefox.desktop").is_file()
    assert (home / ".local/share/applications/thunderbird.desktop").is_file()
    assert "migration summary: 2 overridden, 0 skipped" in result.stdout


def test_gpu_status_writes_a_user_override_on_confirmed_migration(tmp_path: pathlib.Path) -> None:
    processes = json.dumps(
        {"processes": [{"pid": 1, "comm": "firefox", "exe": "firefox", "vram_mib": 500}]}
    )
    system_apps = tmp_path / "system-apps"
    _desktop_file(system_apps, "firefox.desktop", "firefox %u")
    result, _, home = run_gpu_status_with_stubs(
        tmp_path,
        processes_json=processes,
        input_text="y\nn\n",
        system_applications_dir=system_apps,
    )
    override = home / ".local/share/applications/firefox.desktop"
    assert override.is_file()
    content = override.read_text()
    assert "Exec=env DRI_PRIME=pci-0000_0e_00_0 firefox %u" in content
    assert "overridden -> firefox.desktop" in result.stdout
    assert "migration summary: 1 overridden, 0 skipped" in result.stdout


def test_gpu_status_never_touches_the_system_desktop_file(tmp_path: pathlib.Path) -> None:
    processes = json.dumps(
        {"processes": [{"pid": 1, "comm": "firefox", "exe": "firefox", "vram_mib": 500}]}
    )
    system_apps = tmp_path / "system-apps"
    system_file = _desktop_file(system_apps, "firefox.desktop", "firefox %u")
    original = system_file.read_text()
    run_gpu_status_with_stubs(
        tmp_path,
        processes_json=processes,
        input_text="y\nn\n",
        system_applications_dir=system_apps,
    )
    assert system_file.read_text() == original


def test_gpu_status_skips_a_process_with_no_matching_launcher(tmp_path: pathlib.Path) -> None:
    processes = json.dumps(
        {"processes": [{"pid": 1, "comm": "unmatched-app", "exe": "unmatched-app", "vram_mib": 500}]}
    )
    system_apps = tmp_path / "system-apps"
    system_apps.mkdir()
    result, _, home = run_gpu_status_with_stubs(
        tmp_path,
        processes_json=processes,
        input_text="y\nn\n",
        system_applications_dir=system_apps,
    )
    assert "no launcher found, skipped" in result.stdout
    assert "migration summary: 0 overridden, 1 skipped" in result.stdout
    assert not (home / ".local/share/applications").exists() or not list(
        (home / ".local/share/applications").glob("*.desktop")
    )


def test_gpu_status_declines_migration_writes_nothing(tmp_path: pathlib.Path) -> None:
    processes = json.dumps(
        {"processes": [{"pid": 1, "comm": "firefox", "exe": "firefox", "vram_mib": 500}]}
    )
    system_apps = tmp_path / "system-apps"
    _desktop_file(system_apps, "firefox.desktop", "firefox %u")
    result, _, home = run_gpu_status_with_stubs(
        tmp_path,
        processes_json=processes,
        input_text="n\nn\n",
        system_applications_dir=system_apps,
    )
    assert not (home / ".local/share/applications").exists() or not list(
        (home / ".local/share/applications").glob("*.desktop")
    )
    assert "overridden" not in result.stdout


def test_gpu_status_migration_is_idempotent_on_repeat_runs(tmp_path: pathlib.Path) -> None:
    """A second confirmed migration for the same app must not stack a
    second `env DRI_PRIME=...` prefix onto the first run's already-written
    override. find_desktop_file() searches the user override directory
    before the system one, so the second run finds its own prior output as
    input -- apply_igpu_override()'s awk substitution must replace an
    existing `env DRI_PRIME=...` prefix rather than prepending another."""
    processes = json.dumps(
        {"processes": [{"pid": 1, "comm": "firefox", "exe": "firefox", "vram_mib": 500}]}
    )
    system_apps = tmp_path / "system-apps"
    _desktop_file(system_apps, "firefox.desktop", "firefox %u")
    run_gpu_status_with_stubs(
        tmp_path,
        processes_json=processes,
        input_text="y\nn\n",
        system_applications_dir=system_apps,
    )
    _result, _, home = run_gpu_status_with_stubs(
        tmp_path,
        processes_json=processes,
        input_text="y\nn\n",
        system_applications_dir=system_apps,
    )
    override = home / ".local/share/applications/firefox.desktop"
    content = override.read_text()
    exec_line = next(line for line in content.splitlines() if line.startswith("Exec="))
    assert exec_line.count("DRI_PRIME") == 1
    assert exec_line == "Exec=env DRI_PRIME=pci-0000_0e_00_0 firefox %u"


def test_gpu_status_finds_and_reprefixes_an_existing_override_when_system_file_is_gone(
    tmp_path: pathlib.Path,
) -> None:
    """find_desktop_file() must genuinely recognize its own previously-written
    override, not merely appear idempotent because it fell through to a
    still-present, untouched system file. Pre-seed the HOME override
    directory directly (bypassing the script) with an already-applied
    `Exec=env DRI_PRIME=pci-old-value firefox %u` line, then point
    LLM_ENV_SYSTEM_APPLICATIONS_DIR at a directory that doesn't exist at
    all -- so the system file is genuinely unreachable and the only way
    this run can find a launcher for firefox is by matching the existing
    HOME override. Confirms two things at once: find_desktop_file() strips
    the "env DRI_PRIME=..." prefix before extracting its match token (the
    fix for HIGH #2), and apply_igpu_override() safely rewrites a target
    file that is the SAME path as its input via a temp-file-then-rename
    (the fix for CRITICAL #1) -- producing exactly one DRI_PRIME
    assignment with the new value, not zero, not two, and not an empty
    file."""
    processes = json.dumps(
        {"processes": [{"pid": 1, "comm": "firefox", "exe": "firefox", "vram_mib": 500}]}
    )
    home = tmp_path / "home"
    apps_dir = home / ".local" / "share" / "applications"
    apps_dir.mkdir(parents=True)
    (apps_dir / "firefox.desktop").write_text(
        "[Desktop Entry]\nType=Application\nName=Test App\n"
        "Exec=env DRI_PRIME=pci-old-value firefox %u\nIcon=test\n"
    )
    system_apps = tmp_path / "system-apps-does-not-exist"
    result, _, home = run_gpu_status_with_stubs(
        tmp_path,
        processes_json=processes,
        input_text="y\nn\n",
        system_applications_dir=system_apps,
    )
    assert result.returncode == 0
    override = home / ".local/share/applications/firefox.desktop"
    content = override.read_text()
    exec_line = next(line for line in content.splitlines() if line.startswith("Exec="))
    assert exec_line.count("DRI_PRIME") == 1
    assert exec_line == "Exec=env DRI_PRIME=pci-0000_0e_00_0 firefox %u"
    assert "overridden -> firefox.desktop" in result.stdout
    assert "migration summary: 1 overridden, 0 skipped" in result.stdout


def test_gpu_status_skips_a_process_when_override_write_fails(tmp_path: pathlib.Path) -> None:
    """One process's .desktop override write failing (e.g. an unwritable
    target directory) must be reported as skipped and must not abort the
    run under `set -euo pipefail` -- the run must still exit 0 overall."""
    processes = json.dumps(
        {"processes": [{"pid": 1, "comm": "firefox", "exe": "firefox", "vram_mib": 500}]}
    )
    system_apps = tmp_path / "system-apps"
    _desktop_file(system_apps, "firefox.desktop", "firefox %u")
    home = tmp_path / "home"
    share_dir = home / ".local" / "share"
    share_dir.mkdir(parents=True)
    share_dir.chmod(0o555)  # read-only: mkdir of "applications" inside it fails
    try:
        result, _, _ = run_gpu_status_with_stubs(
            tmp_path,
            processes_json=processes,
            input_text="y\nn\n",
            system_applications_dir=system_apps,
        )
    finally:
        share_dir.chmod(0o755)
    assert result.returncode == 0
    assert "override failed, skipped" in result.stdout
    assert "migration summary: 0 overridden, 1 skipped" in result.stdout


def test_gpu_status_warns_and_skips_migration_with_no_alternate_gpu(tmp_path: pathlib.Path) -> None:
    """Asserted against result.stderr, not result.stdout: this message is
    emitted via tools/lib.sh's log_warn(), whose `printf ... >&2` is an
    unconditional redirect to stderr regardless of tty state -- unlike the
    `read -rp` prompt text elsewhere in this file (which bash only ever
    writes to a terminal, never to a pipe), log_warn's output always lands
    on stderr in a piped/non-terminal test run."""
    processes = json.dumps(
        {"processes": [{"pid": 1, "comm": "firefox", "exe": "firefox", "vram_mib": 500}]}
    )
    commands = tmp_path / "bin"
    commands.mkdir()
    uv = commands / "uv"
    uv.write_text(
        "#!/usr/bin/bash\n"
        "case \"$*\" in\n"
        "  *' detect') printf '%s\\n' '{\"gpus\":["
        "{\"card\":\"card1\",\"pci_address\":\"0000:03:00.0\",\"vram_total_mib\":16384,"
        "\"vram_used_mib\":2048,\"render_node\":\"renderD128\",\"connected_outputs\":[]}]}' ;;\n"
        "  *' budget '*) printf '%s\\n' '{\"available_mib\":9000,\"required_mib\":6000,\"feasible\":true}' ;;\n"
        f"  *' processes-on-render-node '*) printf '%s\\n' '{processes}' ;;\n"
        "esac\n"
    )
    uv.chmod(uv.stat().st_mode | stat.S_IXUSR)
    real_yq = shutil.which("yq")
    assert real_yq is not None
    yq = commands / "yq"
    yq.write_text(f"#!/usr/bin/bash\nexec {real_yq} \"$@\"\n")
    yq.chmod(yq.stat().st_mode | stat.S_IXUSR)

    config = tmp_path / "models.yml"
    config.write_text('gpu:\n  pci_address: "0000:03:00.0"\n  vram_budget_ceiling_mib: 15565\n')
    environment = os.environ | {
        "HOME": str(tmp_path / "home"),
        "LLM_ENV_CONFIG": str(config),
        "LLM_ENV_MODELS_DIR": str(tmp_path / "models"),
        "PATH": f"{commands}:/usr/bin:/bin",
    }
    result = subprocess.run(
        ["/usr/bin/bash", "scripts/gpu-status.sh"],
        cwd=ROOT,
        env=environment,
        input="n\n",
        text=True,
        capture_output=True,
        check=False,
    )
    assert "no alternate GPU detected; skipping migration" in result.stderr


def test_gpu_status_writes_environment_d_default_on_confirmation(tmp_path: pathlib.Path) -> None:
    processes = json.dumps(
        {"processes": [{"pid": 1, "comm": "firefox", "exe": "firefox", "vram_mib": 500}]}
    )
    system_apps = tmp_path / "system-apps"
    _desktop_file(system_apps, "firefox.desktop", "firefox %u")
    result, _, home = run_gpu_status_with_stubs(
        tmp_path,
        processes_json=processes,
        input_text="n\ny\n",
        system_applications_dir=system_apps,
    )
    conf = home / ".config/environment.d/60-llm-env-igpu-default.conf"
    assert conf.is_file()
    assert conf.read_text() == "DRI_PRIME=pci-0000_0e_00_0\n"
    assert "wrote" in result.stdout
    assert "best-effort" in result.stdout
    assert "DRI_PRIME convention" in result.stdout
    assert "next login" in result.stdout
    assert "llm-server itself" in result.stdout


def test_gpu_status_default_preference_declined_writes_nothing(tmp_path: pathlib.Path) -> None:
    processes = json.dumps(
        {"processes": [{"pid": 1, "comm": "firefox", "exe": "firefox", "vram_mib": 500}]}
    )
    system_apps = tmp_path / "system-apps"
    _desktop_file(system_apps, "firefox.desktop", "firefox %u")
    _result, _, home = run_gpu_status_with_stubs(
        tmp_path,
        processes_json=processes,
        input_text="n\nn\n",
        system_applications_dir=system_apps,
    )
    assert not (home / ".config/environment.d/60-llm-env-igpu-default.conf").exists()


def test_gpu_status_default_preference_is_idempotent_on_repeat_runs(tmp_path: pathlib.Path) -> None:
    processes = json.dumps(
        {"processes": [{"pid": 1, "comm": "firefox", "exe": "firefox", "vram_mib": 500}]}
    )
    system_apps = tmp_path / "system-apps"
    _desktop_file(system_apps, "firefox.desktop", "firefox %u")
    run_gpu_status_with_stubs(
        tmp_path,
        processes_json=processes,
        input_text="n\ny\n",
        system_applications_dir=system_apps,
    )
    _result, _, home = run_gpu_status_with_stubs(
        tmp_path,
        processes_json=processes,
        input_text="n\ny\n",
        system_applications_dir=system_apps,
    )
    conf_dir = home / ".config/environment.d"
    assert len(list(conf_dir.glob("*llm-env*"))) == 1


def test_gpu_status_assume_yes_never_writes_any_override(tmp_path: pathlib.Path) -> None:
    """LLM_ENV_ASSUME_YES=1 means "auto-decline" for gpu-status.sh's two
    prompts (see the plan's Global Constraints) -- confirm this holds even
    when stdin would answer "y" to both prompts if they were ever read,
    proving ASSUME_YES short-circuits confirm() before either prompt is
    reached at all, not merely that "y" happens not to arrive."""
    processes = json.dumps(
        {"processes": [{"pid": 1, "comm": "firefox", "exe": "firefox", "vram_mib": 500}]}
    )
    system_apps = tmp_path / "system-apps"
    _desktop_file(system_apps, "firefox.desktop", "firefox %u")
    result, _, home = run_gpu_status_with_stubs(
        tmp_path,
        processes_json=processes,
        input_text="y\ny\n",
        system_applications_dir=system_apps,
        extra_env={"LLM_ENV_ASSUME_YES": "1"},
    )
    assert result.returncode == 0
    assert not (home / ".local/share/applications").exists() or not list(
        (home / ".local/share/applications").glob("*.desktop")
    )
    assert not (home / ".config/environment.d/60-llm-env-igpu-default.conf").exists()


valid_benchmark_json = (
    '[{"n_prompt":512,"avg_ts":90.0},{"n_gen":128,"avg_ts":30.0}]'
)


def run_benchmark(
    tmp_path: pathlib.Path,
    benchmark_stdout: str,
    benchmark_stderr: str = "",
    *,
    resolve_exit: int = 0,
    empty_resolve: bool = False,
    presets_exit: int = 0,
    presets_missing_alias: bool = False,
    device_name: str = "Benchmark GPU",
    yq_models_json_exit: int = 0,
) -> tuple[subprocess.CompletedProcess[str], pathlib.Path, pathlib.Path]:
    """Run the benchmark with controlled Vulkan streams and record Podman calls."""
    real_yq = shutil.which("yq")
    assert real_yq is not None

    commands = tmp_path / "bin"
    commands.mkdir()
    calls = tmp_path / "calls"
    calls.touch()
    config = tmp_path / "models.yml"
    config.write_text(
        "gpu:\n"
        "  backend: vulkan\n"
        "  image: ghcr.io/ggml-org/llama.cpp:server-vulkan\n"
        f"  device_name: {device_name}\n"
        "  benchmark:\n"
        "    vulkan: {pp_tps: 1, tg_tps: 1, measured_at: legacy}\n"
        "models:\n"
        "  - alias: smallest\n"
        "    enabled: true\n"
        "    file: smallest.gguf\n"
        "    size_bytes: 1\n"
        "    ctx_size: 4096\n"
        "    client_max_output_tokens: 512\n"
        "  - alias: 'odd/\"alias'\n"
        "    enabled: true\n"
        "    file: biggest.gguf\n"
        "    size_bytes: 2\n"
        "    ctx_size: 8192\n"
        "    check_ctx_size: 2048\n"
        "    client_max_output_tokens: 1024\n"
    )

    uv = commands / "uv"
    uv.write_text(
        "#!/usr/bin/bash\n"
        "case \"$*\" in\n"
        "    *' migrate-config')\n"
        "        \"$REAL_YQ\" -i 'del(.gpu.benchmark)' \"$LLM_ENV_CONFIG\"\n"
        "        printf '%s\\n' '{\"written\":true}'\n"
        "        exit 0\n"
        "        ;;\n"
        "    *' resolve-device'*)\n"
        "        if [ \"$EMPTY_RESOLVE\" = 1 ]; then\n"
        "            printf '%s\\n' '{\"device\":\"\"}'\n"
        "        else\n"
        "            printf '%s\\n' '{\"device\":\"Vulkan0\"}'\n"
        "        fi\n"
        "        exit \"$RESOLVE_EXIT\"\n"
        "        ;;\n"
        "    *' presets '*)\n"
        "        previous=\n"
        "        for argument in \"$@\"; do\n"
        "            if [ \"$previous\" = --output ]; then presets_file=\"$argument\"; fi\n"
        "            previous=\"$argument\"\n"
        "        done\n"
        "        printf '%s\\n' '[smallest]' 'ctx-size = 4096' 'n-gpu-layers = 99' '' > \"$presets_file\"\n"
        "        if [ \"$PRESETS_MISSING_ALIAS\" != 1 ]; then\n"
        "            printf '%s\\n' '[odd/\"alias]' 'ctx-size = 8192' 'n-gpu-layers = 40' 'n-cpu-moe = 12' >> \"$presets_file\"\n"
        "        fi\n"
        "        exit \"$PRESETS_EXIT\"\n"
        "        ;;\n"
        "esac\n"
    )
    uv.chmod(uv.stat().st_mode | stat.S_IXUSR)

    yq = commands / "yq"
    yq.write_text(
        "#!/usr/bin/bash\n"
        "if [ \"$#\" -eq 4 ] && [ \"$1\" = '-o=json' ] && [ \"$2\" = '-I=0' ] && "
        "[ \"$3\" = '[.models[] | select(.enabled)]' ] && [ \"$4\" = \"$LLM_ENV_CONFIG\" ]; then\n"
        "  \"$REAL_YQ\" \"$@\" || exit $?\n"
        "  exit \"$YQ_MODELS_JSON_EXIT\"\n"
        "fi\n"
        "exec \"$REAL_YQ\" \"$@\"\n"
    )
    yq.chmod(yq.stat().st_mode | stat.S_IXUSR)

    podman = commands / "podman"
    podman.write_text(
        "#!/usr/bin/bash\n"
        "printf 'podman %s\\n' \"$*\" >> \"$CALLS\"\n"
        "case \"$*\" in\n"
        "  *'help all'*) printf '%s\\n' bench ;;\n"
        "  *'--list-devices'*) printf '%s\\n' 'Vulkan0: Benchmark GPU (16384 MiB, 16000 MiB free)' ;;\n"
        "  *' bench '*' /models/smallest.gguf '*) printf '%s' \"$BENCHMARK_STDOUT\"; printf '%s' \"$BENCHMARK_STDERR\" >&2 ;;\n"
        "  *' bench '*' /models/biggest.gguf '*) printf '%s' \"$BENCHMARK_STDOUT\"; printf '%s' \"$BENCHMARK_STDERR\" >&2 ;;\n"
        "esac\n"
    )
    podman.chmod(podman.stat().st_mode | stat.S_IXUSR)

    environment = os.environ | {
        "CALLS": str(calls),
        "HOME": str(tmp_path / "home"),
        "LLM_ENV_CONFIG": str(config),
        "LLM_ENV_MODELS_DIR": str(tmp_path / "models"),
        "PATH": f"{commands}:/usr/bin:/bin",
        "REAL_YQ": real_yq,
        "BENCHMARK_STDOUT": benchmark_stdout,
        "BENCHMARK_STDERR": benchmark_stderr,
        "RESOLVE_EXIT": str(resolve_exit),
        "EMPTY_RESOLVE": "1" if empty_resolve else "0",
        "PRESETS_EXIT": str(presets_exit),
        "PRESETS_MISSING_ALIAS": "1" if presets_missing_alias else "0",
        "YQ_MODELS_JSON_EXIT": str(yq_models_json_exit),
    }
    result = subprocess.run(
        ["/usr/bin/make", "benchmark"],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    return result, calls, config


def test_benchmark_parses_valid_stdout_despite_vulkan_stderr_warning(
    tmp_path: pathlib.Path,
) -> None:
    """Vulkan warnings must not corrupt an otherwise valid JSON benchmark."""
    result, calls, config = run_benchmark(
        tmp_path,
        '[{"n_prompt":512,"avg_ts":123.4},{"n_gen":128,"avg_ts":56.7}]',
        "WARNING: radv is not a conformant Vulkan implementation\n",
    )

    assert result.returncode == 0, result.stderr
    assert "Benchmark stdout:" in result.stdout
    assert '"avg_ts":123.4' in result.stdout
    assert result.stdout.count(
        'Parsed metrics:\n  {"pp_tps":123.4,"tg_tps":56.7}'
    ) == 2
    assert "Benchmark stderr:\n  WARNING: radv" in result.stdout
    assert "Benchmark parser stderr:" not in result.stdout
    assert yq_value(config, ".gpu.backend") == "vulkan"
    assert yq_value(config, ".gpu.image") == "ghcr.io/ggml-org/llama.cpp:server-vulkan"
    assert yq_model_benchmark_value(config, "smallest", "pp_tps") == "123.4"
    assert yq_model_benchmark_value(config, 'odd/"alias', "tg_tps") == "56.7"
    assert yq_value(config, ".gpu.benchmark") == "null"
    assert "podman pull ghcr.io/ggml-org/llama.cpp:server" not in calls.read_text()


def test_benchmark_configures_cpu_but_fails_when_vulkan_stdout_is_invalid(
    tmp_path: pathlib.Path,
) -> None:
    """An invalid Vulkan response must configure CPU and fail the benchmark."""
    result, calls, config = run_benchmark(tmp_path, "not benchmark JSON\n")

    assert result.returncode != 0
    assert "Benchmark stdout:\n  not benchmark JSON" in result.stdout
    assert "Benchmark parser stderr:" in result.stdout
    assert "parse error:" in result.stdout
    assert "Vulkan benchmark failure for smallest: response parsing" in result.stderr
    assert yq_value(config, ".gpu.backend") == "cpu"
    assert yq_value(config, ".gpu.image") == "ghcr.io/ggml-org/llama.cpp:server"
    assert yq_value(config, ".gpu.benchmark") == "null"
    assert "podman pull ghcr.io/ggml-org/llama.cpp:server" in calls.read_text()


def test_benchmark_uses_every_models_device_flags_and_probe_sizes(
    tmp_path: pathlib.Path,
) -> None:
    result, calls, config = run_benchmark(tmp_path, valid_benchmark_json)
    assert result.returncode == 0, result.stderr
    rows = [line for line in calls.read_text().splitlines() if " bench " in line]
    assert any(
        "/models/smallest.gguf" in row
        and "--device Vulkan0" in row
        and "--n-gpu-layers 99" in row
        and "-p 4096 -n 512" in row
        for row in rows
    )
    assert any(
        "/models/biggest.gguf" in row
        and "--device Vulkan0" in row
        and "--n-gpu-layers 40" in row
        and "--n-cpu-moe 12" in row
        and "-p 2048 -n 1024" in row
        for row in rows
    )
    assert yq_model_benchmark_value(config, 'odd/"alias', "pp_tps") == "90.0"
    assert yq_value(config, ".gpu.benchmark") == "null"


def test_make_benchmark_persists_legacy_shared_benchmark_migration(
    tmp_path: pathlib.Path,
) -> None:
    result, _, config = run_benchmark(tmp_path, valid_benchmark_json)
    assert result.returncode == 0, result.stderr
    assert yq_value(config, ".gpu.benchmark") == "null"
    assert yq_model_benchmark_value(config, "smallest", "pp_tps") == "90.0"


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"device_name": ""}, "configured gpu.device_name is empty"),
        ({"resolve_exit": 31}, "could not resolve configured GPU"),
        ({"empty_resolve": True}, "GPU resolution returned no device"),
        ({"presets_exit": 32}, "could not render production presets"),
        ({"presets_missing_alias": True}, "missing n-gpu-layers preset"),
    ],
)
def test_benchmark_fails_instead_of_skipping_all_models(
    tmp_path: pathlib.Path, kwargs: dict[str, object], message: str
) -> None:
    result, _, config = run_benchmark(tmp_path, valid_benchmark_json, **kwargs)
    assert result.returncode != 0
    assert message in result.stderr
    assert yq_value(config, ".gpu.benchmark") == "null"


def test_benchmark_fails_when_model_enumeration_fails(tmp_path: pathlib.Path) -> None:
    """A yq/jq enumeration crash must not let the script report success
    after benchmarking fewer than every enabled model -- this is what the
    materialized model_records file (Step 4) and the measured_models ==
    total_models check (Step 5) exist to catch."""
    result, calls, config = run_benchmark(
        tmp_path, valid_benchmark_json, yq_models_json_exit=19
    )
    assert result.returncode != 0
    assert "failed to enumerate enabled models" in result.stderr
    assert " bench " not in calls.read_text()
    assert yq_value(config, ".gpu.benchmark") == "null"


def test_prune_lists_model_count_and_size_before_confirming(tmp_path: pathlib.Path) -> None:
    commands = tmp_path / "bin"
    commands.mkdir()
    for name in ("systemctl", "yq", "podman", "numfmt"):
        _mock_command(commands, name)
    (commands / "numfmt").write_text("#!/usr/bin/bash\necho '3KB'\n")

    home = tmp_path / "home"
    models_dir = tmp_path / "models"
    models_dir.mkdir()
    (models_dir / ".llm-env-managed").touch()  # proves this is llm-env's dir
    (models_dir / "a.gguf").write_bytes(b"x" * 1000)
    nested = models_dir / "nested"
    nested.mkdir()
    (nested / "b.gguf").write_bytes(b"y" * 1000)
    (models_dir / ".hidden.gguf").write_bytes(b"z" * 1000)

    environment = os.environ | {
        "HOME": str(home),
        "LLM_ENV_MODELS_DIR": str(models_dir),
        "LLM_ENV_ASSUME_YES": "0",
        "PATH": f"{commands}:/usr/bin:/bin",
    }
    result = subprocess.run(
        ["/usr/bin/bash", "scripts/prune.sh"],
        cwd=ROOT,
        env=environment,
        input="no\n",
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    # Counts nested and hidden files too, not just top-level entries, but
    # never the .llm-env-managed marker itself -- that's bookkeeping, not a
    # downloaded model.
    assert "3 downloaded model file(s)" in result.stdout
    assert (models_dir / "a.gguf").exists()  # aborted -- nothing removed
    assert (nested / "b.gguf").exists()


def test_prune_removes_models_and_runs_clean_after_confirmation(
    tmp_path: pathlib.Path,
) -> None:
    commands = tmp_path / "bin"
    commands.mkdir()
    for name in ("systemctl", "yq", "numfmt"):
        _mock_command(commands, name)
    calls = tmp_path / "calls"
    podman = commands / "podman"
    podman.write_text("#!/usr/bin/bash\nprintf 'podman %s\\n' \"$*\" >> \"$CALLS\"\n")
    podman.chmod(podman.stat().st_mode | stat.S_IXUSR)

    home = tmp_path / "home"
    compose_file = home / ".config/llm-env/docker-compose.yml"
    compose_file.parent.mkdir(parents=True)
    compose_file.write_text("services: {llm-server: {}}\n")

    models_dir = tmp_path / "models"
    models_dir.mkdir()
    (models_dir / ".llm-env-managed").touch()  # proves this is llm-env's dir
    (models_dir / "a.gguf").write_bytes(b"x" * 1000)
    nested = models_dir / "nested"
    nested.mkdir()
    (nested / "b.gguf").write_bytes(b"y" * 1000)
    (models_dir / ".hidden.gguf").write_bytes(b"z" * 1000)

    environment = os.environ | {
        "CALLS": str(calls),
        "HOME": str(home),
        "LLM_ENV_MODELS_DIR": str(models_dir),
        "LLM_ENV_ASSUME_YES": "1",
        "PATH": f"{commands}:/usr/bin:/bin",
    }
    result = subprocess.run(
        ["/usr/bin/bash", "scripts/prune.sh"],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert not (models_dir / "a.gguf").exists()
    assert not nested.exists()  # nested directories are removed too
    assert not (models_dir / ".hidden.gguf").exists()  # dotfiles are removed too
    # The marker is removed along with everything else -- `make prune`
    # removes ALL of $MODELS_DIR's contents. The next `make setup` recreates
    # it (Step 4), so this is not a re-prune hazard.
    assert not (models_dir / ".llm-env-managed").exists()
    assert models_dir.exists()  # directory itself survives, only contents removed
    assert "podman compose" in calls.read_text()  # clean.sh actually ran, not skipped


def test_prune_handles_a_missing_models_dir_without_error(tmp_path: pathlib.Path) -> None:
    commands = tmp_path / "bin"
    commands.mkdir()
    for name in ("systemctl", "yq", "podman", "numfmt"):
        _mock_command(commands, name)

    home = tmp_path / "home"
    environment = os.environ | {
        "HOME": str(home),
        "LLM_ENV_MODELS_DIR": str(tmp_path / "does-not-exist"),
        "LLM_ENV_ASSUME_YES": "1",
        "PATH": f"{commands}:/usr/bin:/bin",
    }
    result = subprocess.run(
        ["/usr/bin/bash", "scripts/prune.sh"],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "0 downloaded model file(s)" in result.stdout


def test_prune_refuses_to_run_against_the_repository_directory(tmp_path: pathlib.Path) -> None:
    """LLM_ENV_MODELS_DIR is operator-controlled; a value that resolves to
    the repository itself (or '/' or $HOME) must be rejected before
    anything is deleted, not passed straight to `rm -rf`."""
    commands = tmp_path / "bin"
    commands.mkdir()
    for name in ("systemctl", "yq", "podman", "numfmt"):
        _mock_command(commands, name)

    home = tmp_path / "home"
    environment = os.environ | {
        "HOME": str(home),
        "LLM_ENV_MODELS_DIR": str(ROOT),
        "LLM_ENV_ASSUME_YES": "1",
        "PATH": f"{commands}:/usr/bin:/bin",
    }
    result = subprocess.run(
        ["/usr/bin/bash", "scripts/prune.sh"],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "refusing to prune" in result.stderr
    assert (ROOT / "scripts" / "prune.sh").exists()  # the repo itself must survive


def test_prune_refuses_a_directory_without_the_llm_env_managed_marker(
    tmp_path: pathlib.Path,
) -> None:
    """Path shape alone (not '/', $HOME, or $REPO_DIR, and at least 2 path
    segments deep) is not proof this is really llm-env's models directory --
    any pre-existing, unrelated directory at a plausible depth (e.g.
    /etc/ssh) would otherwise pass every check above and get recursively
    deleted. Require the marker `make setup` leaves behind in `$MODELS_DIR`
    the first time it creates/uses it (Step 3 below)."""
    commands = tmp_path / "bin"
    commands.mkdir()
    for name in ("systemctl", "yq", "podman", "numfmt"):
        _mock_command(commands, name)

    home = tmp_path / "home"
    models_dir = tmp_path / "some" / "unrelated" / "directory"
    models_dir.mkdir(parents=True)
    (models_dir / "a.gguf").write_bytes(b"x" * 1000)  # looks like a model, but unmanaged

    environment = os.environ | {
        "HOME": str(home),
        "LLM_ENV_MODELS_DIR": str(models_dir),
        "LLM_ENV_ASSUME_YES": "1",
        "PATH": f"{commands}:/usr/bin:/bin",
    }
    result = subprocess.run(
        ["/usr/bin/bash", "scripts/prune.sh"],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "missing .llm-env-managed marker" in result.stderr
    assert (models_dir / "a.gguf").exists()  # refused before deleting anything


def run_cleanup_with_stubs(
    tmp_path: pathlib.Path,
) -> tuple[subprocess.CompletedProcess[str], pathlib.Path]:
    """Run cleanup in a temporary home and record every Podman invocation."""
    commands = tmp_path / "bin"
    commands.mkdir()
    calls = tmp_path / "calls"
    calls.touch()

    for name in ("systemctl", "yq"):
        command = commands / name
        command.write_text("#!/usr/bin/bash\nexit 0\n")
        command.chmod(command.stat().st_mode | stat.S_IXUSR)

    podman = commands / "podman"
    podman.write_text(
        "#!/usr/bin/bash\n"
        "printf 'podman %s\\n' \"$*\" >> \"$CALLS\"\n"
    )
    podman.chmod(podman.stat().st_mode | stat.S_IXUSR)

    home = tmp_path / "home"
    environment = os.environ | {
        "CALLS": str(calls),
        "HOME": str(home),
        "LLM_ENV_ASSUME_YES": "1",
        "PATH": f"{commands}:/usr/bin:/bin",
    }
    result = subprocess.run(
        ["/usr/bin/bash", "scripts/clean.sh"],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    return result, calls


def test_cleanup_preserves_the_host_rocm_image(tmp_path: pathlib.Path) -> None:
    """Project cleanup must not remove or otherwise reference the host ROCm image."""
    result, calls = run_cleanup_with_stubs(tmp_path)

    assert result.returncode == 0, result.stderr
    assert "rocm" not in (result.stdout + result.stderr + calls.read_text()).lower()


def test_cleanup_removes_the_configured_gpu_image_not_a_hardcoded_one(
    tmp_path: pathlib.Path,
) -> None:
    real_yq = shutil.which("yq")
    assert real_yq is not None

    commands = tmp_path / "bin"
    commands.mkdir()
    _mock_command(commands, "systemctl")
    yq = commands / "yq"
    yq.write_text("#!/usr/bin/bash\nexec \"$REAL_YQ\" \"$@\"\n")
    yq.chmod(yq.stat().st_mode | stat.S_IXUSR)
    calls = tmp_path / "calls"
    podman = commands / "podman"
    podman.write_text("#!/usr/bin/bash\nprintf 'podman %s\\n' \"$*\" >> \"$CALLS\"\n")
    podman.chmod(podman.stat().st_mode | stat.S_IXUSR)

    home = tmp_path / "home"
    config = home / ".config/llm-env/models.yml"
    config.parent.mkdir(parents=True)
    config.write_text(
        "gpu:\n  image: example.invalid/custom-build:pinned\n"
    )

    environment = os.environ | {
        "CALLS": str(calls),
        "HOME": str(home),
        "LLM_ENV_CONFIG": str(config),
        "LLM_ENV_ASSUME_YES": "1",
        "PATH": f"{commands}:/usr/bin:/bin",
        "REAL_YQ": real_yq,
    }
    result = subprocess.run(
        ["/usr/bin/bash", "scripts/clean.sh"],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "example.invalid/custom-build:pinned" in calls.read_text()


def test_cleanup_removes_the_configured_remote_setup_image(
    tmp_path: pathlib.Path,
) -> None:
    real_yq = shutil.which("yq")
    assert real_yq is not None

    commands = tmp_path / "bin"
    commands.mkdir()
    _mock_command(commands, "systemctl")
    yq = commands / "yq"
    yq.write_text("#!/usr/bin/bash\nexec \"$REAL_YQ\" \"$@\"\n")
    yq.chmod(yq.stat().st_mode | stat.S_IXUSR)
    calls = tmp_path / "calls"
    podman = commands / "podman"
    podman.write_text("#!/usr/bin/bash\nprintf 'podman %s\\n' \"$*\" >> \"$CALLS\"\n")
    podman.chmod(podman.stat().st_mode | stat.S_IXUSR)

    home = tmp_path / "home"
    config = home / ".config/llm-env/models.yml"
    config.parent.mkdir(parents=True)
    config.write_text(
        "remote_setup:\n  image: example.invalid/custom-remote-setup:pinned\n  port: 20130\n"
    )

    environment = os.environ | {
        "CALLS": str(calls),
        "HOME": str(home),
        "LLM_ENV_CONFIG": str(config),
        "LLM_ENV_ASSUME_YES": "1",
        "LLM_ENV_REMOVE_IMAGES": "1",
        "PATH": f"{commands}:/usr/bin:/bin",
        "REAL_YQ": real_yq,
    }
    result = subprocess.run(
        ["/usr/bin/bash", "scripts/clean.sh"],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "example.invalid/custom-remote-setup:pinned" in calls.read_text()


def test_cleanup_banner_mentions_remote_setup_data(tmp_path: pathlib.Path) -> None:
    commands = tmp_path / "bin"
    commands.mkdir()
    _mock_command(commands, "systemctl")
    _mock_command(commands, "podman")
    real_yq = shutil.which("yq")
    assert real_yq is not None
    yq = commands / "yq"
    yq.write_text("#!/usr/bin/bash\nexec \"$REAL_YQ\" \"$@\"\n")
    yq.chmod(yq.stat().st_mode | stat.S_IXUSR)

    home = tmp_path / "home"
    config = home / ".config/llm-env/models.yml"
    config.parent.mkdir(parents=True)
    config.write_text("version: 1\n")

    result = subprocess.run(
        ["/usr/bin/bash", "scripts/clean.sh"],
        cwd=ROOT,
        env=os.environ
        | {
            "HOME": str(home),
            "LLM_ENV_CONFIG": str(config),
            "LLM_ENV_ASSUME_YES": "1",
            "PATH": f"{commands}:/usr/bin:/bin",
            "REAL_YQ": real_yq,
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "remote-setup-data" in result.stdout


def test_cleanup_removes_the_local_omniroute_api_key_cache(tmp_path, monkeypatch):
    home = tmp_path / "home"
    config = home / ".config" / "llm-env" / "models.yml"
    config.parent.mkdir(parents=True)
    config.write_text("gpu:\n  image: i\n", encoding="utf-8")
    cache = config.parent / "omniroute-api-key.json"
    cache.write_text('{"id": "x", "key": "y"}\n', encoding="utf-8")
    commands = tmp_path / "bin"
    commands.mkdir()
    real_yq = shutil.which("yq") or "yq"
    yq = commands / "yq"
    yq.write_text("#!/usr/bin/bash\nexec \"$REAL_YQ\" \"$@\"\n")
    yq.chmod(0o755)
    for name in ("podman", "systemctl"):
        command = commands / name
        command.write_text("#!/usr/bin/bash\nexit 0\n")
        command.chmod(0o755)
    result = subprocess.run(
        ["/usr/bin/bash", "scripts/clean.sh"],
        cwd=ROOT,
        env=os.environ
        | {
            "HOME": str(home),
            "LLM_ENV_CONFIG": str(config),
            "LLM_ENV_ASSUME_YES": "1",
            "PATH": f"{commands}:/usr/bin:/bin",
            "REAL_YQ": real_yq,
        },
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert not cache.exists()


def test_cleanup_falls_back_to_default_images_without_a_config(
    tmp_path: pathlib.Path,
) -> None:
    commands = tmp_path / "bin"
    commands.mkdir()
    _mock_command(commands, "systemctl")
    _mock_command(commands, "yq")
    calls = tmp_path / "calls"
    podman = commands / "podman"
    podman.write_text("#!/usr/bin/bash\nprintf 'podman %s\\n' \"$*\" >> \"$CALLS\"\n")
    podman.chmod(podman.stat().st_mode | stat.S_IXUSR)

    environment = os.environ | {
        "CALLS": str(calls),
        "HOME": str(tmp_path / "home"),
        "LLM_ENV_CONFIG": str(tmp_path / "home/.config/llm-env/models.yml"),  # absent
        "LLM_ENV_ASSUME_YES": "1",
        "PATH": f"{commands}:/usr/bin:/bin",
    }
    result = subprocess.run(
        ["/usr/bin/bash", "scripts/clean.sh"],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "ghcr.io/ggml-org/llama.cpp:server-vulkan" in calls.read_text()


def test_cleanup_fails_loudly_when_yq_cannot_read_an_existing_config(
    tmp_path: pathlib.Path,
) -> None:
    """A config that exists but can't be parsed must abort, not silently fall back."""
    commands = tmp_path / "bin"
    commands.mkdir()
    _mock_command(commands, "systemctl")
    _mock_command(commands, "podman")
    yq = commands / "yq"
    yq.write_text("#!/usr/bin/bash\nexit 1\n")
    yq.chmod(yq.stat().st_mode | stat.S_IXUSR)

    home = tmp_path / "home"
    config = home / ".config/llm-env/models.yml"
    config.parent.mkdir(parents=True)
    config.write_text("gpu:\n  image: example.invalid/custom-build:pinned\n")

    environment = os.environ | {
        "HOME": str(home),
        "LLM_ENV_CONFIG": str(config),
        "LLM_ENV_ASSUME_YES": "1",
        "PATH": f"{commands}:/usr/bin:/bin",
    }
    result = subprocess.run(
        ["/usr/bin/bash", "scripts/clean.sh"],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "could not read gpu.image" in result.stderr


def test_cleanup_confirmation_prompt_reflects_the_configured_gpu_image(
    tmp_path: pathlib.Path,
) -> None:
    real_yq = shutil.which("yq")
    assert real_yq is not None

    commands = tmp_path / "bin"
    commands.mkdir()
    _mock_command(commands, "systemctl")
    _mock_command(commands, "podman")
    yq = commands / "yq"
    yq.write_text("#!/usr/bin/bash\nexec \"$REAL_YQ\" \"$@\"\n")
    yq.chmod(yq.stat().st_mode | stat.S_IXUSR)

    home = tmp_path / "home"
    config = home / ".config/llm-env/models.yml"
    config.parent.mkdir(parents=True)
    config.write_text("gpu:\n  image: example.invalid/custom-build:pinned\n")

    environment = os.environ | {
        "HOME": str(home),
        "LLM_ENV_CONFIG": str(config),
        "LLM_ENV_ASSUME_YES": "1",
        "PATH": f"{commands}:/usr/bin:/bin",
        "REAL_YQ": real_yq,
    }
    result = subprocess.run(
        ["/usr/bin/bash", "scripts/clean.sh"],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "example.invalid/custom-build:pinned" in result.stdout
    assert "ghcr.io/ggml-org/llama.cpp:server-vulkan and server" not in result.stdout


def test_cleanup_removes_the_compose_file_and_wrapper_unit(tmp_path: pathlib.Path) -> None:
    commands = tmp_path / "bin"
    commands.mkdir()
    calls = tmp_path / "calls"
    for name in ("systemctl", "yq"):
        _mock_command(commands, name)
    podman = commands / "podman"
    podman.write_text(
        "#!/usr/bin/bash\nprintf 'podman %s\\n' \"$*\" >> \"$CALLS\"\n"
    )
    podman.chmod(podman.stat().st_mode | stat.S_IXUSR)

    home = tmp_path / "home"
    compose_file = home / ".config/llm-env/docker-compose.yml"
    wrapper_unit = home / ".config/systemd/user/llm-server.service"
    compose_file.parent.mkdir(parents=True)
    compose_file.write_text("services: {llm-server: {}}\n")
    wrapper_unit.parent.mkdir(parents=True)
    wrapper_unit.write_text("[Unit]\n")

    environment = os.environ | {
        "CALLS": str(calls),
        "HOME": str(home),
        "LLM_ENV_ASSUME_YES": "1",
        "PATH": f"{commands}:/usr/bin:/bin",
    }
    result = subprocess.run(
        ["/usr/bin/bash", "scripts/clean.sh"],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert f"podman compose -f {compose_file} down" in calls.read_text()
    assert not compose_file.exists()
    assert not wrapper_unit.exists()


def test_disable_boot_removes_install_section_from_the_wrapper_unit(
    tmp_path: pathlib.Path,
) -> None:
    real_yq = shutil.which("yq")
    assert real_yq is not None

    commands = tmp_path / "bin"
    commands.mkdir()
    calls = tmp_path / "calls"
    calls.touch()
    systemctl = commands / "systemctl"
    systemctl.write_text('#!/usr/bin/bash\nprintf \'systemctl %s\\n\' "$*" >> "$CALLS"\n')
    systemctl.chmod(systemctl.stat().st_mode | stat.S_IXUSR)
    yq = commands / "yq"
    yq.write_text("#!/usr/bin/bash\nexec \"$REAL_YQ\" \"$@\"\n")
    yq.chmod(yq.stat().st_mode | stat.S_IXUSR)

    home = tmp_path / "home"
    config = home / ".config/llm-env/models.yml"
    config.parent.mkdir(parents=True)
    config.write_text("version: 1\nserver:\n  start_at_boot: true\n")

    wrapper_unit = home / ".config/systemd/user/llm-server.service"
    wrapper_unit.parent.mkdir(parents=True)
    wrapper_unit.write_text(
        "[Unit]\nDescription=x\n\n[Service]\nExecStart=x\n\n[Install]\nWantedBy=default.target\n"
    )

    environment = os.environ | {
        "CALLS": str(calls),
        "HOME": str(home),
        "LLM_ENV_CONFIG": str(config),
        "PATH": f"{commands}:/usr/bin:/bin",
        "REAL_YQ": real_yq,
    }
    result = subprocess.run(
        ["/usr/bin/bash", "setup/disable-boot.sh"],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "[Install]" not in wrapper_unit.read_text()
    assert yq_value(config, ".server.start_at_boot") == "false"
    assert "systemctl --user disable llm-server.service" in calls.read_text()


def run_render_unit_with_legacy_rocm_config(
    tmp_path: pathlib.Path,
    llm_server_enabled: bool = True,
) -> tuple[subprocess.CompletedProcess[str], pathlib.Path]:
    """Render a manually retained legacy backend config in an isolated home."""
    real_yq = shutil.which("yq")
    assert real_yq is not None

    commands = tmp_path / "bin"
    commands.mkdir()
    home = tmp_path / "home"
    config = tmp_path / "models.yml"
    config.write_text(
        "version: 1\n"
        "server:\n"
        "  host: 0.0.0.0\n"
        "  port: 8000\n"
        "  api_key: key\n"
        "  sleep_idle_seconds: 300\n"
        "  start_at_boot: false\n"
        "llm_server:\n"
        f"  enabled: {'true' if llm_server_enabled else 'false'}\n"
        "gpu:\n"
        # validate_config() only accepts vulkan/cpu (pylib/config.py:31); this
        # fixture predates ROCm removal but must still pass real validation
        # now that `uv` forwards to the real binary below. The regression
        # this test guards — /dev/kfd never appears in the rendered output —
        # does not depend on which backend value is used.
        "  backend: vulkan\n"
        "  image: example.invalid/llama:latest\n"
        "  device_name: ''\n"
        "  vram_total_mib: 8192\n"
        "  reserve_mode: auto\n"
        "  reserve_floor_mib: 1024\n"
        "runtime:\n"
        "  models_max: 1\n"
        "  parallel_slots: 1\n"
        "  ubatch_size: 512\n"
        "  flash_attn: true\n"
        "  cache_type_k: q8_0\n"
        "  cache_type_v: q8_0\n"
        "models:\n"
        "  - alias: test\n"
        "    label: Test\n"
        "    parameters: 1B\n"
        "    quantization: Q4_K_M\n"
        "    enabled: true\n"
        "    file: test.gguf\n"
        "    url: https://example.invalid/test.gguf\n"
        "    size_bytes: 1\n"
        "    vram_budget: 10%\n"
        "    ctx_size: 8192\n"
        "    client_max_output_tokens: 8192\n"
        "    n_gpu_layers: 99\n"
    )

    for name in ("podman", "systemctl"):
        command = commands / name
        command.write_text("#!/usr/bin/bash\nexit 0\n")
        command.chmod(command.stat().st_mode | stat.S_IXUSR)

    real_uv = shutil.which("uv")
    assert real_uv is not None
    uv = commands / "uv"
    uv.write_text("#!/usr/bin/bash\nexec \"$REAL_UV\" \"$@\"\n")
    uv.chmod(uv.stat().st_mode | stat.S_IXUSR)

    yq = commands / "yq"
    yq.write_text("#!/usr/bin/bash\nexec \"$REAL_YQ\" \"$@\"\n")
    yq.chmod(yq.stat().st_mode | stat.S_IXUSR)

    environment = os.environ | {
        "HOME": str(home),
        "LLM_ENV_CONFIG": str(config),
        "LLM_ENV_COMPOSE_INSPECT_DIR": str(tmp_path / "compose-inspect"),
        "PATH": f"{commands}:/usr/bin:/bin",
        "REAL_YQ": real_yq,
        "REAL_UV": real_uv,
    }
    result = subprocess.run(
        ["/usr/bin/bash", "setup/render-unit.sh"],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    return result, home / ".config/systemd/user/llm-server.service"


def test_render_unit_never_adds_the_rocm_kernel_device(tmp_path: pathlib.Path) -> None:
    """A hand-edited legacy config must not pass the ROCm kernel device through."""
    result, container = run_render_unit_with_legacy_rocm_config(tmp_path)

    assert result.returncode == 0, result.stderr
    assert "/dev/kfd" not in container.read_text()


def test_render_unit_writes_a_compose_file_and_wrapper_unit(tmp_path: pathlib.Path) -> None:
    result, wrapper_unit = run_render_unit_with_legacy_rocm_config(tmp_path)
    compose_file = wrapper_unit.parent.parent.parent / "llm-env/docker-compose.yml"

    assert result.returncode == 0, result.stderr
    assert compose_file.exists()
    assert "llm-server" in compose_file.read_text()
    assert "ExecStart=podman compose -f docker-compose.yml up -d" in wrapper_unit.read_text()
    assert "ExecStop=podman compose -f docker-compose.yml down" in wrapper_unit.read_text()


def test_render_unit_no_gpu_skips_device_resolution_and_presets(
    tmp_path: pathlib.Path,
) -> None:
    result, wrapper_unit = run_render_unit_with_legacy_rocm_config(
        tmp_path, llm_server_enabled=False
    )
    compose_file = wrapper_unit.parent.parent.parent / "llm-env/docker-compose.yml"

    assert result.returncode == 0, result.stdout + result.stderr
    assert compose_file.exists()
    assert "llm-server" not in yaml.safe_load(compose_file.read_text())["services"]
    presets_path = tmp_path / "home" / ".config/llm-env/presets.ini"
    assert not presets_path.exists()


def test_render_unit_no_gpu_still_writes_the_wrapper_unit(
    tmp_path: pathlib.Path,
) -> None:
    result, wrapper_unit = run_render_unit_with_legacy_rocm_config(
        tmp_path, llm_server_enabled=False
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert wrapper_unit.exists()
    assert "ExecStart=podman compose" in wrapper_unit.read_text()


def test_render_unit_no_gpu_skips_mdns_unit_generation(
    tmp_path: pathlib.Path,
) -> None:
    result, wrapper_unit = run_render_unit_with_legacy_rocm_config(
        tmp_path, llm_server_enabled=False
    )
    mdns_unit = wrapper_unit.parent / "llm-server-mdns.service"

    assert result.returncode == 0, result.stdout + result.stderr
    assert not mdns_unit.exists()


def test_render_unit_copies_presets_ini_to_the_inspect_dir(
    tmp_path: pathlib.Path,
) -> None:
    result, _wrapper_unit = run_render_unit_with_legacy_rocm_config(tmp_path)

    presets_path = tmp_path / "home" / ".config/llm-env/presets.ini"
    compose_inspect_dir = tmp_path / "compose-inspect"

    assert result.returncode == 0, result.stdout + result.stderr
    inspect_copy = compose_inspect_dir / "presets.ini"
    assert inspect_copy.is_file()
    assert inspect_copy.read_text() == presets_path.read_text()


def test_render_unit_retires_the_legacy_static_ip_mdns_unit(
    tmp_path: pathlib.Path,
) -> None:
    """A pre-rename "llm-mdns.service" ran a one-shot
    `avahi-publish -a -R llm.local <ip>` with the IP baked in at start time,
    so it goes stale on any DHCP lease change. render-unit.sh must retire it
    on every run -- its replacement (llm-server-mdns.service, driven by
    tools/publish-mdns-hostname.sh) now owns republishing that same alias
    whenever the address actually changes, so removing the legacy unit does
    not regress hostname resolution."""
    real_yq = shutil.which("yq")
    assert real_yq is not None
    real_uv = shutil.which("uv")
    assert real_uv is not None

    commands = tmp_path / "bin"
    commands.mkdir()
    calls = tmp_path / "calls"
    calls.touch()
    home = tmp_path / "home"
    legacy_unit = home / ".config/systemd/user/llm-mdns.service"
    legacy_unit.parent.mkdir(parents=True)
    legacy_unit.write_text(
        "[Service]\nExecStart=/usr/bin/avahi-publish -a -R llm.local 192.0.2.219\n"
    )
    config = tmp_path / "models.yml"
    config.write_text(
        "version: 1\n"
        "server:\n"
        "  host: 0.0.0.0\n"
        "  port: 8000\n"
        "  api_key: key\n"
        "  sleep_idle_seconds: 300\n"
        "  start_at_boot: false\n"
        "gpu:\n"
        "  backend: vulkan\n"
        "  image: example.invalid/llama:latest\n"
        "  device_name: ''\n"
        "  vram_total_mib: 8192\n"
        "  reserve_mode: auto\n"
        "  reserve_floor_mib: 1024\n"
        "runtime:\n"
        "  models_max: 1\n"
        "  parallel_slots: 1\n"
        "  ubatch_size: 512\n"
        "  flash_attn: true\n"
        "  cache_type_k: q8_0\n"
        "  cache_type_v: q8_0\n"
        "models:\n"
        "  - alias: test\n"
        "    label: Test\n"
        "    parameters: 1B\n"
        "    quantization: Q4_K_M\n"
        "    enabled: true\n"
        "    file: test.gguf\n"
        "    url: https://example.invalid/test.gguf\n"
        "    size_bytes: 1\n"
        "    vram_budget: 10%\n"
        "    ctx_size: 8192\n"
        "    client_max_output_tokens: 8192\n"
        "    n_gpu_layers: 99\n"
    )

    podman = commands / "podman"
    podman.write_text("#!/usr/bin/bash\nexit 0\n")
    podman.chmod(podman.stat().st_mode | stat.S_IXUSR)
    systemctl = commands / "systemctl"
    systemctl.write_text(
        "#!/usr/bin/bash\nprintf 'systemctl %s\\n' \"$*\" >> \"$CALLS\"\nexit 0\n"
    )
    systemctl.chmod(systemctl.stat().st_mode | stat.S_IXUSR)
    uv = commands / "uv"
    uv.write_text("#!/usr/bin/bash\nexec \"$REAL_UV\" \"$@\"\n")
    uv.chmod(uv.stat().st_mode | stat.S_IXUSR)
    yq = commands / "yq"
    yq.write_text("#!/usr/bin/bash\nexec \"$REAL_YQ\" \"$@\"\n")
    yq.chmod(yq.stat().st_mode | stat.S_IXUSR)

    environment = os.environ | {
        "CALLS": str(calls),
        "HOME": str(home),
        "LLM_ENV_CONFIG": str(config),
        "LLM_ENV_COMPOSE_INSPECT_DIR": str(tmp_path / "compose-inspect"),
        "PATH": f"{commands}:/usr/bin:/bin",
        "REAL_YQ": real_yq,
        "REAL_UV": real_uv,
    }
    result = subprocess.run(
        ["/usr/bin/bash", "setup/render-unit.sh"],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert not legacy_unit.exists()
    recorded = calls.read_text()
    assert "systemctl --user stop llm-mdns.service" in recorded
    assert "systemctl --user disable llm-mdns.service" in recorded


def test_render_unit_wrapper_unit_omits_install_section_by_default(
    tmp_path: pathlib.Path,
) -> None:
    _, wrapper_unit = run_render_unit_with_legacy_rocm_config(tmp_path)
    assert "[Install]" not in wrapper_unit.read_text()


def test_make_help_describes_a_vulkan_only_benchmark() -> None:
    """The public benchmark command must not advertise an unsupported backend."""
    result = subprocess.run(
        ["make", "help"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "rocm" not in result.stdout.lower()


def run_check_setup_with_legacy_rocm_config(
    tmp_path: pathlib.Path,
) -> subprocess.CompletedProcess[str]:
    """Run the offline check far enough to expose any legacy ROCm requirement."""
    real_yq = shutil.which("yq")
    assert real_yq is not None

    commands = tmp_path / "bin"
    commands.mkdir()
    config = tmp_path / "models.yml"
    config.write_text(
        "server: {}\n"
        "gpu:\n"
        "  pci_address: 0000:03:00.0\n"
        "  backend: rocm\n"
        "  image: example.invalid/llama:latest\n"
        "  device_name: ''\n"
        "runtime:\n"
        "  models_max: 1\n"
        "models: []\n"
    )

    for name in ("curl", "podman", "systemctl"):
        command = commands / name
        command.write_text("#!/usr/bin/bash\nexit 0\n")
        command.chmod(command.stat().st_mode | stat.S_IXUSR)

    uv = commands / "uv"
    uv.write_text(
        "#!/usr/bin/bash\n"
        "case \"$*\" in *' budget '*) exit 1 ;; esac\n"
        "exit 0\n"
    )
    uv.chmod(uv.stat().st_mode | stat.S_IXUSR)

    yq = commands / "yq"
    yq.write_text("#!/usr/bin/bash\nexec \"$REAL_YQ\" \"$@\"\n")
    yq.chmod(yq.stat().st_mode | stat.S_IXUSR)

    environment = os.environ | {
        "HOME": str(tmp_path / "home"),
        "LLM_ENV_CONFIG": str(config),
        "PATH": f"{commands}:/usr/bin:/bin",
        "REAL_YQ": real_yq,
    }
    return subprocess.run(
        ["/usr/bin/bash", "scripts/check-setup.sh"],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )


def test_check_setup_never_requires_the_rocm_kernel_device(tmp_path: pathlib.Path) -> None:
    """Offline validation must not advertise a legacy ROCm device requirement."""
    result = run_check_setup_with_legacy_rocm_config(tmp_path)

    assert "rocm" not in (result.stdout + result.stderr).lower()


def run_check_setup_with_stubs(
    tmp_path: pathlib.Path,
    *,
    api_key: str = "mixed-case-api-key",
    inference_stdout: str = "ready",
    inference_stderr: str = "",
    inference_exit: int = 0,
    budget_exit: int = 0,
    resolve_exit: int = 0,
    empty_resolve: bool = False,
    presets_exit: int = 0,
    presets_missing_alias: bool = False,
    yq_models_json_exit: int = 0,
    render_node: str | None = "renderD128",
    verbose: bool = False,
) -> tuple[subprocess.CompletedProcess[str], pathlib.Path, pathlib.Path]:
    """Run offline setup validation with controlled command output."""
    real_yq = shutil.which("yq")
    assert real_yq is not None

    detected_gpu = json.dumps(
        {
            "gpus": [
                {
                    "pci_address": "0000:03:00.0",
                    **({"render_node": render_node} if render_node is not None else {}),
                }
            ]
        }
    )

    commands = tmp_path / "bin"
    commands.mkdir()
    calls = tmp_path / "calls"
    for name in ("systemctl", "curl"):
        _mock_command(commands, name)

    timeout = commands / "timeout"
    timeout.write_text(
        "#!/usr/bin/bash\n"
        "printf 'timeout %s\\n' \"$*\" >> \"$CALLS\"\n"
        "case \"$1\" in\n"
        "    140|300) ;;\n"
        "    *) exit 64 ;;\n"
        "esac\n"
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
        "  *' detect'*) printf '%s\\n' \"$DETECTED_GPU\" ;;\n"
        "  *' validate-gguf'*) printf '%s\\n' '{\"results\":[]}' ;;\n"
        "  *' budget '*) printf '%s\\n' '{\"available_mib\":12000,\"required_mib\":10000,\"models_max\":2}'; exit \"$BUDGET_EXIT\" ;;\n"
        "  *' presets '*)\n"
        "    for argument in \"$@\"; do\n"
        "      if [ \"$previous\" = --output ]; then presets_file=\"$argument\"; fi\n"
        "      previous=\"$argument\"\n"
        "    done\n"
        "    printf '%s\\n' '[first]' 'ctx-size = 8192' 'n-gpu-layers = 42' '' > \"$presets_file\"\n"
        "    if [ \"$PRESETS_MISSING_ALIAS\" != 1 ]; then\n"
        "      printf '%s\\n' '[second]' 'ctx-size = 4096' 'n-gpu-layers = 17' 'n-cpu-moe = 12' >> \"$presets_file\"\n"
        "    fi\n"
        "    exit \"$PRESETS_EXIT\" ;;\n"
        "  *' resolve-device'*)\n"
        "    for argument in \"$@\"; do\n"
        "      if [ \"$previous\" = --listing-file ]; then listing_file=\"$argument\"; fi\n"
        "      previous=\"$argument\"\n"
        "    done\n"
        "    [ \"$(cat \"$listing_file\")\" = 'Vulkan7: Selected Radeon (16384 MiB, 16000 MiB free)' ] || exit 65\n"
        "    if [ \"$EMPTY_RESOLVE\" = 1 ]; then\n"
        "      printf '%s\\n' '{\"device\":\"\"}'\n"
        "    else\n"
        "      printf '%s\\n' '{\"device\":\"Vulkan7\"}'\n"
        "    fi\n"
        "    exit \"$RESOLVE_EXIT\" ;;\n"
        "esac\n"
    )
    uv.chmod(uv.stat().st_mode | stat.S_IXUSR)

    yq = commands / "yq"
    yq.write_text(
        "#!/usr/bin/bash\n"
        "if [ \"$#\" -eq 4 ] && [ \"$1\" = '-o=json' ] && [ \"$2\" = '-I=0' ] && "
        "[ \"$3\" = '[.models[] | select(.enabled)]' ] && [ \"$4\" = \"$LLM_ENV_CONFIG\" ]; then\n"
        "  \"$REAL_YQ\" \"$@\" || exit $?\n"
        "  exit \"$YQ_MODELS_JSON_EXIT\"\n"
        "fi\n"
        "exec \"$REAL_YQ\" \"$@\"\n"
    )
    yq.chmod(yq.stat().st_mode | stat.S_IXUSR)

    podman = commands / "podman"
    podman.write_text(
        "#!/usr/bin/bash\n"
        "printf 'podman %s\\n' \"$*\" >> \"$CALLS\"\n"
        "case \"$*\" in\n"
        "  *'--list-devices'*) printf '%s\\n' 'Vulkan7: Selected Radeon (16384 MiB, 16000 MiB free)' ;;\n"
        "  *' cli '*) printf '%s' \"$INFERENCE_STDOUT\"; printf '%s' \"$INFERENCE_STDERR\" >&2; exit \"$INFERENCE_EXIT\" ;;\n"
        "esac\n"
    )
    podman.chmod(podman.stat().st_mode | stat.S_IXUSR)

    config = tmp_path / "models.yml"
    config.write_text(
        "server:\n"
        f"  api_key: {api_key}\n"
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
        "    ctx_size: 8192\n"
        "    client_max_output_tokens: 2048\n"
        "    n_gpu_layers: 42\n"
        "  - alias: skipped\n"
        "    enabled: false\n"
        "    file: skipped.gguf\n"
        "    n_gpu_layers: 99\n"
        "  - alias: second\n"
        "    enabled: true\n"
        "    file: second.gguf\n"
        "    ctx_size: 4096\n"
        "    check_ctx_size: 2048\n"
        "    client_max_output_tokens: 1024\n"
        "    check_timeout_seconds: 300\n"
        "    n_gpu_layers: 17\n"
        "    n_cpu_moe: 12\n"
    )
    models_dir = tmp_path / "models"
    environment = os.environ | {
        "CALLS": str(calls),
        "HOME": str(tmp_path / "home"),
        "LLM_ENV_CONFIG": str(config),
        "LLM_ENV_MODELS_DIR": str(models_dir),
        "PATH": f"{commands}:/usr/bin:/bin",
        "REAL_YQ": real_yq,
        "INFERENCE_STDOUT": inference_stdout,
        "INFERENCE_STDERR": inference_stderr,
        "INFERENCE_EXIT": str(inference_exit),
        "BUDGET_EXIT": str(budget_exit),
        "RESOLVE_EXIT": str(resolve_exit),
        "EMPTY_RESOLVE": "1" if empty_resolve else "0",
        "PRESETS_EXIT": str(presets_exit),
        "PRESETS_MISSING_ALIAS": "1" if presets_missing_alias else "0",
        "YQ_MODELS_JSON_EXIT": str(yq_models_json_exit),
        "DETECTED_GPU": detected_gpu,
    } | ({"LLM_ENV_CHECK_VERBOSE": "1"} if verbose else {})
    result = subprocess.run(
        ["/usr/bin/bash", "scripts/check-setup.sh"],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    return result, calls, models_dir


def test_check_setup_runs_disposable_inference_for_each_enabled_model(
    tmp_path: pathlib.Path,
) -> None:
    """Offline setup validation must resolve and smoke-test every enabled model."""
    result, calls, _ = run_check_setup_with_stubs(tmp_path)

    assert result.returncode == 0, result.stderr
    recorded = calls.read_text()
    check_setup = (ROOT / "scripts/check-setup.sh").read_text()
    assert 'uv run "${REPO_DIR}/llmenv.py" resolve-device' in check_setup
    assert (
        f"uv run {ROOT / 'llmenv.py'} resolve-device --device-name Selected Radeon "
        "--listing-file "
    ) in recorded
    list_devices = (
        "podman run --rm --device /dev/dri "
        "example.invalid/llama:latest --list-devices"
    )
    assert recorded.count(list_devices) == 1
    first_call = next(
        (
            line
            for line in recorded.splitlines()
            if "podman run" in line
            and "/models/first.gguf" in line
            and " cli " in line
        ),
        None,
    )
    second_call = next(
        (
            line
            for line in recorded.splitlines()
            if "podman run" in line
            and "/models/second.gguf" in line
            and " cli " in line
        ),
        None,
    )
    assert first_call is not None and first_call.startswith("timeout 140 podman run")
    assert second_call is not None and second_call.startswith("timeout 300 podman run")
    assert "--n-gpu-layers 42 --ctx-size 8192" in first_call
    assert "--n-gpu-layers 17 --n-cpu-moe 12 --ctx-size 2048" in second_call
    assert first_call.endswith("-p Reply with exactly: ready -n 2048")
    assert second_call.endswith("-p Reply with exactly: ready -n 1024")
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


def test_check_setup_prints_complete_static_and_inference_records(
    tmp_path: pathlib.Path,
) -> None:
    """Offline validation must expose complete evidence for every checked command
    when LLM_ENV_CHECK_VERBOSE=1 is set; a default run only prints a concise
    verdict per PASS -- see test_check_setup_prints_concise_pass_rows_by_default."""
    result, _, _ = run_check_setup_with_stubs(tmp_path, verbose=True)

    assert result.returncode == 0, result.stderr
    assert "Identity: tooling command uv" in result.stdout
    assert "Command: command -v uv" in result.stdout
    tooling_record = result.stdout.split("Identity: tooling command uv", 1)[1].split(
        "Identity:", 1
    )[0]
    assert "Command stdout:" in tooling_record
    assert "Parsed result:" not in tooling_record
    assert "Command stderr:" not in tooling_record
    assert "Command: podman run --rm --device /dev/dri" in result.stdout
    assert "Input:\n  Reply with exactly: ready" in result.stdout
    assert "Inference stdout:\n  ready" in result.stdout
    assert "Parsed result:\n  ready" in result.stdout
    assert "Inference stderr:" not in result.stdout
    assert "Expectation:\n  normalized assistant content: ready" in result.stdout
    assert "Verdict: PASS" in result.stdout


def test_check_setup_prints_concise_pass_rows_by_default(
    tmp_path: pathlib.Path,
) -> None:
    """A default (non-verbose) run must not dump the Identity/Command/stdout/
    Exit status/Expectation block for a PASS -- only the one-line verdict."""
    result, _, _ = run_check_setup_with_stubs(tmp_path)

    assert result.returncode == 0, result.stderr
    assert "Identity:" not in result.stdout
    assert "Command:" not in result.stdout
    assert "Command stdout:" not in result.stdout
    assert "Inference stdout:" not in result.stdout
    assert "Parsed result:" not in result.stdout
    assert "Expectation:" not in result.stdout
    assert "Verdict: PASS identity=tooling command uv" in result.stdout
    assert "Verdict: PASS identity=inference " in result.stdout
    assert "Results: " in result.stdout


def test_check_setup_skips_unresolved_gpu_render_node_without_parsed_result(
    tmp_path: pathlib.Path,
) -> None:
    result, _, _ = run_check_setup_with_stubs(tmp_path, render_node=None)
    gpu_record = result.stdout.split("Identity: GPU render node", 1)[1].split(
        "Identity:", 1
    )[0]

    assert result.returncode == 0, result.stderr
    assert "Command: test -r /dev/dri/<resolved-render-node>" in gpu_record
    assert "Input:\n  configured PCI address: 0000:03:00.0" in gpu_record
    assert "Command stdout:\n  (empty)" in gpu_record
    assert "Command stderr:" not in gpu_record
    assert "Exit status:\n  SKIP" in gpu_record
    assert "Parsed result:" not in gpu_record
    assert "Expectation:\n  a detected readable render node" in gpu_record
    assert "Verdict: SKIP reason=GPU detection did not provide a render node" in result.stderr


def test_check_setup_accepts_ready_after_visible_reasoning(tmp_path: pathlib.Path) -> None:
    """Offline inference accepts the final answer after visible reasoning."""
    result, _, _ = run_check_setup_with_stubs(
        tmp_path,
        inference_stdout="[Start thinking]\nThe user requires one word.\nready\n",
        verbose=True,
    )

    assert result.returncode == 0, result.stderr
    assert "Inference stdout:\n  [Start thinking]" in result.stdout
    assert "  The user requires one word." in result.stdout
    assert "Parsed result:\n  ready" in result.stdout
    assert "Verdict: PASS" in result.stdout


def test_check_setup_ignores_llama_exit_footer_after_ready(
    tmp_path: pathlib.Path,
) -> None:
    result, calls, _ = run_check_setup_with_stubs(
        tmp_path,
        inference_stdout="[Start thinking]\nreasoning\nready\n\nExiting...\n",
        verbose=True,
    )

    assert result.returncode == 0, result.stderr
    assert "Parsed result:\n  ready" in result.stdout
    assert "Exiting..." in result.stdout
    assert "--no-show-timings" in calls.read_text()


def test_check_setup_keeps_independent_records_after_an_inference_failure(
    tmp_path: pathlib.Path,
) -> None:
    """An inference failure must not hide its evidence or the final result."""
    result, _, _ = run_check_setup_with_stubs(
        tmp_path,
        inference_stdout="",
        inference_stderr="loader failed\n",
        inference_exit=17,
    )

    assert result.returncode != 0
    assert "Exit status:\n  17" in result.stdout
    assert "Inference stderr:\n  loader failed" in result.stdout
    assert "Verdict: FAIL stage=command exit reason=inference exited 17" in result.stderr
    assert "Results:" in result.stdout


def test_check_setup_reports_a_normalized_inference_mismatch(tmp_path: pathlib.Path) -> None:
    """A successful non-ready response must identify a normalized-value mismatch."""
    result, _, _ = run_check_setup_with_stubs(tmp_path, inference_stdout="not ready")

    assert result.returncode != 0
    assert "Parsed result:\n  not ready" in result.stdout
    assert (
        "Verdict: FAIL stage=parsed result "
        "reason=normalized assistant content mismatch expected=ready"
    ) in result.stderr


def test_check_setup_validates_the_rendered_compose_file(tmp_path: pathlib.Path) -> None:
    result, calls, _ = run_check_setup_with_stubs(tmp_path)
    compose_file = tmp_path / "home/.config/llm-env/docker-compose.yml"

    assert "Compose file" in result.stdout
    assert f"podman compose -f {compose_file} config" in calls.read_text()


def test_check_setup_skips_each_enabled_inference_after_budget_failure(
    tmp_path: pathlib.Path,
) -> None:
    """Failed inference prerequisites must skip every enabled model independently."""
    result, _, _ = run_check_setup_with_stubs(
        tmp_path,
        budget_exit=19,
    )

    assert result.returncode != 0
    assert result.stdout.count("Identity: inference ") == 2
    assert result.stderr.count("Verdict: SKIP reason=VRAM budget check failed") == 2
    assert "Verdict: PASS identity=tooling command uv" in result.stdout
    assert "Verdict: PASS identity=GGUF validation" in result.stdout
    assert "Results:" in result.stdout


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"resolve_exit": 23}, "GPU device could not be resolved"),
        ({"empty_resolve": True}, "GPU device resolution returned no device"),
        ({"presets_exit": 29}, "presets rendering failed"),
        ({"presets_missing_alias": True}, "missing n-gpu-layers preset for second"),
    ],
)
def test_check_setup_fails_when_inference_prerequisite_is_unavailable(
    tmp_path: pathlib.Path,
    kwargs: dict[str, object],
    message: str,
) -> None:
    result, calls, _ = run_check_setup_with_stubs(tmp_path, **kwargs)

    assert result.returncode != 0
    assert message in result.stderr
    assert "Results:" in result.stdout
    if "presets" in message:
        recorded = calls.read_text()
        assert "/models/second.gguf" not in recorded or " cli " not in recorded


def test_check_setup_fails_when_model_enumeration_fails(
    tmp_path: pathlib.Path,
) -> None:
    result, calls, _ = run_check_setup_with_stubs(tmp_path, yq_models_json_exit=17)

    assert result.returncode != 0
    assert "failed to enumerate enabled models" in result.stderr
    assert " cli " not in calls.read_text()


def run_lifecycle_script(
    tmp_path: pathlib.Path,
    script: str,
    *,
    api_key: str = "existing-key",
    active: bool = False,
    config_mode: int = 0o600,
    parallel_slots: int = 1,
    sampling_temperature: str | None = None,
    env_overrides: dict[str, str] | None = None,
    omniroute_port: int | None = None,
    resources_failure: bool = False,
    llm_server_enabled: bool = True,
) -> tuple[subprocess.CompletedProcess[str], pathlib.Path, pathlib.Path]:
    """Run a lifecycle script with real configuration writes and external stubs."""
    real_yq = shutil.which("yq")
    assert real_yq is not None
    real_uv = shutil.which("uv")
    assert real_uv is not None

    commands = tmp_path / "bin"
    commands.mkdir()
    calls = tmp_path / "calls"
    calls.touch()
    home = tmp_path / "home"
    config = home / ".config/llm-env/models.yml"
    config.parent.mkdir(parents=True)
    config.write_text(
        "version: 1\n"
        "llm_server:\n"
        f"  enabled: {str(llm_server_enabled).lower()}\n"
        "server:\n"
        "  host: 0.0.0.0\n"
        "  port: 8000\n"
        f"  api_key: {api_key!r}\n"
        "  mdns_name: llm\n"
        "  sleep_idle_seconds: 300\n"
        "  start_at_boot: false\n"
        "gpu:\n"
        "  pci_address: ''\n"
        "  backend: cpu\n"
        "  image: example.invalid/llama:latest\n"
        "  device_name: ''\n"
        "  vram_total_mib: 0\n"
        "  reserve_mode: auto\n"
        "  reserve_floor_mib: 1024\n"
        + (f"omniroute:\n  port: {omniroute_port}\n" if omniroute_port is not None else "")
        + "runtime:\n"
        "  models_max: 1\n"
        f"  parallel_slots: {parallel_slots}\n"
        "  ubatch_size: 512\n"
        "  flash_attn: true\n"
        "  cache_type_k: q8_0\n"
        "  cache_type_v: q8_0\n"
        "models:\n"
        "  - alias: test\n"
        "    label: Test\n"
        "    parameters: 1B\n"
        "    quantization: Q4_K_M\n"
        "    enabled: true\n"
        "    file: test.gguf\n"
        "    url: https://example.invalid/test.gguf\n"
        "    size_bytes: 1\n"
        "    vram_budget: 10%\n"
        "    ctx_size: 8192\n"
        "    client_max_output_tokens: 8192\n"
        "    n_gpu_layers: 99\n"
        + (
            "    sampling:\n"
            f"      temperature: {sampling_temperature}\n"
            if sampling_temperature is not None
            else ""
        )
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
    resources_case = (
        "printf '%s\\n' '{\"error\": \"host has 3 CPUs; more than 3 are required\"}'; exit 1"
        if resources_failure
        else "printf '%s\\n' '{\"llm_server\": {\"cpus\": 4, \"memory_mib\": 8000}, \"omniroute\": {\"cpus\": 1, \"memory_mib\": 1024}}'"
    )
    uv = commands / "uv"
    uv.write_text(
        "#!/usr/bin/bash\n"
        "printf 'uv %s\\n' \"$*\" >> \"$CALLS\"\n"
        "case \"$*\" in *' migrate-config'*) exec \"$REAL_UV\" \"$@\" ;; esac\n"
        "case \"$*\" in *' budget '*) printf '%s\\n' '{\"available_mib\":12000,\"required_mib\":10000,\"vram_feasible\":true,\"ram_feasible\":true}' ;; esac\n"
        f"case \"$*\" in *' resources') {resources_case} ;; esac\n"
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
        "LLM_ENV_COMPOSE_INSPECT_DIR": str(tmp_path / "compose-inspect"),
        "MDNS_UNIT": str(home / ".config/systemd/user/llm-server-mdns.service"),
        "PATH": f"{commands}:/usr/bin:/bin",
        "REAL_YQ": real_yq,
        "REAL_UV": real_uv,
        "LLM_ENV_HEALTH_TIMEOUT_SECONDS": "1",
    } | (env_overrides or {})
    result = subprocess.run(
        ["/usr/bin/bash", script],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    return result, config, calls


def test_start_no_gpu_skips_budget_check_and_llm_server_wait(tmp_path: pathlib.Path) -> None:
    result, _config, calls = run_lifecycle_script(
        tmp_path, "scripts/start.sh", llm_server_enabled=False
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert " budget " not in calls.read_text()
    assert "server is ready" not in result.stdout


def test_start_no_gpu_still_waits_for_omniroute_and_runs_network_sh(
    tmp_path: pathlib.Path,
) -> None:
    result, _config, calls = run_lifecycle_script(
        tmp_path, "scripts/start.sh", llm_server_enabled=False
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "network.sh" in calls.read_text()


def test_start_no_gpu_skips_omniroute_provisioning(tmp_path: pathlib.Path) -> None:
    result, _config, calls = run_lifecycle_script(
        tmp_path, "scripts/start.sh", llm_server_enabled=False
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "omniroute provision" not in calls.read_text()
    assert (
        "llm-server disabled -- skipping local OmniRoute provider provisioning"
        in result.stdout
    )


def test_start_no_gpu_still_starts_the_compose_stack(tmp_path: pathlib.Path) -> None:
    result, _config, calls = run_lifecycle_script(
        tmp_path, "scripts/start.sh", llm_server_enabled=False
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "systemctl --user start" in calls.read_text()


def test_start_enabled_still_waits_for_health_and_provisions(
    tmp_path: pathlib.Path,
) -> None:
    """Regression guard: llm_server_enabled=True (the default) must be
    unaffected."""
    result, _config, calls = run_lifecycle_script(tmp_path, "scripts/start.sh")
    assert result.returncode == 0, result.stdout + result.stderr
    assert " budget " in calls.read_text()
    assert "server is ready" in result.stdout


def run_opencode_config_editor(
    tmp_path: pathlib.Path, config_text: str, provider: dict[str, object]
) -> tuple[subprocess.CompletedProcess[str], pathlib.Path]:
    config = tmp_path / "opencode.jsonc"
    provider_path = tmp_path / "provider.json"
    output = tmp_path / "updated.jsonc"
    config.write_text(config_text)
    provider_path.write_text(json.dumps(provider))
    result = subprocess.run(
        [
            shutil.which("node") or "node",
            "setup/update-opencode-config.mjs",
            "--replace-provider",
            str(config),
            str(provider_path),
            str(output),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    return result, output


def run_opencode_state_editor(
    tmp_path: pathlib.Path,
    state_text: str,
    models: list[dict[str, object]] | object,
) -> tuple[subprocess.CompletedProcess[str], pathlib.Path]:
    source = tmp_path / "model.json"
    models_path = tmp_path / "models.json"
    output = tmp_path / "updated-model.json"
    source.write_text(state_text)
    models_path.write_text(json.dumps(models))
    result = subprocess.run(
        [
            shutil.which("node") or "node",
            "setup/update-opencode-config.mjs",
            "--update-model-state",
            str(source),
            str(models_path),
            str(output),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    return result, output


def test_opencode_state_editor_replaces_local_favorites_and_preserves_other_state(
    tmp_path: pathlib.Path,
) -> None:
    state = {
        "recent": [{"providerID": "other", "modelID": "recent"}],
        "favorite": [
            {"providerID": "other", "modelID": "first"},
            {"providerID": "local-llm-env", "modelID": "stale"},
            {"providerID": "other", "modelID": "second"},
        ],
        "variant": {"other/model": "high"},
    }
    models = [
        {
            "alias": "gemma4",
            "ctx_size": 131072,
            "client_max_output_tokens": 8192,
        },
        {
            "alias": "ornith",
            "ctx_size": 131072,
            "client_max_output_tokens": 8192,
        },
    ]

    result, output = run_opencode_state_editor(
        tmp_path, json.dumps(state), models
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(output.read_text()) == {
        "recent": [{"providerID": "other", "modelID": "recent"}],
        "favorite": [
            {"providerID": "local-llm-env", "modelID": "gemma4"},
            {"providerID": "local-llm-env", "modelID": "ornith"},
            {"providerID": "other", "modelID": "first"},
            {"providerID": "other", "modelID": "second"},
        ],
        "variant": {"other/model": "high"},
    }


@pytest.mark.parametrize(
    "state",
    [
        {},
        {"recent": [], "favorite": "bad", "variant": {}},
        {
            "recent": [{"providerID": 1, "modelID": "x"}],
            "favorite": [],
            "variant": {},
        },
        {
            "recent": [],
            "favorite": [{"providerID": "other", "modelID": "x", "extra": True}],
            "variant": {},
        },
        {"recent": [], "favorite": [], "variant": []},
        {"recent": [], "favorite": [], "variant": {"other/model": 1}},
        {"recent": [], "favorite": [], "variant": {}, "unknown": True},
    ],
)
def test_opencode_state_editor_rejects_incompatible_shapes(
    tmp_path: pathlib.Path, state: object
) -> None:
    result, output = run_opencode_state_editor(
        tmp_path, json.dumps(state), [{"alias": "gemma4"}]
    )

    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr == ""
    assert not output.exists()


@pytest.mark.parametrize(
    "state_text",
    [
        '{"recent":[],"favorite":[],"variant":{},"variant":{}}',
        '{"recent":[],"favorite":[],"variant":{"secret":"one","secret":"two"}}',
        '// state-secret\n{"recent":[],"favorite":[],"variant":{}}',
        '{"recent":[],"favorite":[],"variant":{},}',
    ],
)
def test_opencode_state_editor_rejects_non_strict_or_duplicate_json(
    tmp_path: pathlib.Path, state_text: str
) -> None:
    result, output = run_opencode_state_editor(
        tmp_path, state_text, [{"alias": "gemma4"}]
    )

    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr == ""
    assert "state-secret" not in result.stdout + result.stderr
    assert not output.exists()


@pytest.mark.parametrize(
    "models",
    [
        [],
        [{"alias": ""}],
        [{"alias": "gemma4"}, {"alias": "gemma4"}],
        [{"alias": 1}],
    ],
)
def test_opencode_state_editor_rejects_invalid_enabled_model_records(
    tmp_path: pathlib.Path, models: list[dict[str, object]]
) -> None:
    result, output = run_opencode_state_editor(
        tmp_path,
        '{"recent":[],"favorite":[],"variant":{}}',
        models,
    )

    assert result.returncode == 2
    assert not output.exists()


def test_opencode_config_editor_replaces_one_provider_and_keeps_comments(
    tmp_path: pathlib.Path,
) -> None:
    result, output = run_opencode_config_editor(
        tmp_path,
        "// keep this comment\n{\n  \"agent\": {\"review\": {\"mode\": \"subagent\"}},\n"
        "  \"provider\": {\n    // replace only this value\n"
        "    \"local-llm-env\": {\"models\": {\"old\": {\"name\": \"old\"}}},\n"
        "    \"other\": {\"name\": \"other\"},\n  },\n}\n",
        {
            "npm": "@ai-sdk/openai-compatible",
            "models": {"ornith": {"name": "ornith"}},
        },
    )

    assert result.returncode == 0, result.stderr
    text = output.read_text()
    assert "// keep this comment" in text
    assert "// replace only this value" in text
    parsed = json.loads(
        re.sub(r"//[^\n]*", "", text)
        .replace(",\n  }", "\n  }")
        .replace(",\n}", "\n}")
    )
    assert parsed["agent"]["review"]["mode"] == "subagent"
    assert parsed["provider"]["other"] == {"name": "other"}
    assert parsed["provider"]["local-llm-env"] == {
        "npm": "@ai-sdk/openai-compatible",
        "models": {"ornith": {"name": "ornith"}},
    }


def test_opencode_config_editor_rejects_invalid_jsonc_without_output(
    tmp_path: pathlib.Path,
) -> None:
    result, output = run_opencode_config_editor(
        tmp_path, '{"provider": {"broken": }', {"models": {}}
    )

    assert result.returncode == 2
    assert not output.exists()


@pytest.mark.parametrize(
    ("invalid_whitespace", "whitespace_name"),
    [("\v", "vertical tab"), ("\N{NO-BREAK SPACE}", "NBSP")],
)
def test_opencode_config_editor_rejects_non_jsonc_whitespace_without_output(
    tmp_path: pathlib.Path,
    invalid_whitespace: str,
    whitespace_name: str,
) -> None:
    result, output = run_opencode_config_editor(
        tmp_path,
        f'{{"provider":{invalid_whitespace}{{"other":{{}}}}}}\n',
        {"models": {}},
    )

    assert result.returncode == 2, whitespace_name
    assert result.stdout == ""
    assert result.stderr == ""
    assert not output.exists()


def test_opencode_config_editor_rejects_duplicate_local_provider_keys(
    tmp_path: pathlib.Path,
) -> None:
    config = tmp_path / "opencode.jsonc"
    config.write_text('{"provider":{"local-llm-env":{},"local-llm-env":{}}}\n')

    result = subprocess.run(
        [
            shutil.which("node") or "node",
            "setup/update-opencode-config.mjs",
            "--contains-provider",
            str(config),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr == ""


def test_opencode_config_editor_adds_provider_to_root_with_comments_and_trailing_comma(
    tmp_path: pathlib.Path,
) -> None:
    provider = {
        "npm": "@ai-sdk/openai-compatible",
        "models": {"ornith": {"name": "ornith"}},
    }
    result, output = run_opencode_config_editor(
        tmp_path,
        "// root comment\n{\n  \"agent\": {},\n  // provider belongs before this brace\n}\n",
        provider,
    )

    assert result.returncode == 0, result.stderr
    text = output.read_text()
    assert "// root comment" in text
    assert "// provider belongs before this brace" in text
    parsed = json.loads(re.sub(r"//[^\n]*", "", text).replace(",\n}", "\n}"))
    assert parsed["agent"] == {}
    assert parsed["provider"]["local-llm-env"] == provider


def test_opencode_config_editor_adds_provider_member_with_comments_and_trailing_comma(
    tmp_path: pathlib.Path,
) -> None:
    provider = {
        "npm": "@ai-sdk/openai-compatible",
        "models": {"gemma4": {"name": "gemma4"}},
    }
    result, output = run_opencode_config_editor(
        tmp_path,
        "{\n  \"provider\": {\n    \"other\": {},\n"
        "    // local provider belongs before this brace\n  },\n}\n",
        provider,
    )

    assert result.returncode == 0, result.stderr
    text = output.read_text()
    assert "// local provider belongs before this brace" in text
    parsed = json.loads(
        re.sub(r"//[^\n]*", "", text)
        .replace(",\n  }", "\n  }")
        .replace(",\n}", "\n}")
    )
    assert parsed["provider"]["other"] == {}
    assert parsed["provider"]["local-llm-env"] == provider


def run_setup_local_llm_agents(
    tmp_path: pathlib.Path,
    *,
    config_text: str = VALID_AGENT_SETUP_CONFIG,
    pi_text: str | None = None,
    pi_settings_text: str | None = None,
    opencode_config_json_text: str | None = None,
    opencode_json_text: str | None = None,
    opencode_jsonc_text: str | None = None,
    opencode_state_text: str | None = None,
    opencode_version: str = "1.18.10",
    omniroute_api_key: str = "fixture-omniroute-api-key",
    debug_state_dir: pathlib.Path | None = None,
    fail_move_number: int | None = None,
    health_exit: int = 0,
    failing_command: str | None = None,
) -> tuple[
    subprocess.CompletedProcess[str],
    pathlib.Path,
    pathlib.Path,
    pathlib.Path,
    tuple[pathlib.Path, pathlib.Path, pathlib.Path],
    pathlib.Path,
]:
    commands = tmp_path / "bin"
    commands.mkdir(exist_ok=True)
    calls = tmp_path / "calls"
    calls.write_text("")
    home = tmp_path / "home"
    config = tmp_path / "models.yml"
    pi_path = home / ".pi/agent/models.json"
    pi_settings_path = home / ".pi/agent/settings.json"
    opencode_dir = home / ".config/opencode"
    opencode_paths = (
        opencode_dir / "config.json",
        opencode_dir / "opencode.json",
        opencode_dir / "opencode.jsonc",
    )
    state_path = home / ".local/state/opencode/model.json"
    resolved_debug_state_dir = debug_state_dir or state_path.parent
    config.write_text(config_text)
    for path, text in (
        (pi_path, pi_text),
        (pi_settings_path, pi_settings_text),
        (opencode_paths[0], opencode_config_json_text),
        (opencode_paths[1], opencode_json_text),
        (opencode_paths[2], opencode_jsonc_text),
        (state_path, opencode_state_text),
    ):
        if text is not None and not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
    curl = commands / "curl"
    curl.write_text(
        "#!/usr/bin/bash\n"
        "printf 'curl %s\\n' \"$*\" >> \"$CALLS\"\n"
        "[ \"${!#}\" = 'http://127.0.0.1:18123/health' ] || exit 64\n"
        "exit \"$HEALTH_EXIT\"\n"
    )
    curl.chmod(0o755)
    mktemp = commands / "mktemp"
    mktemp.write_text(
        "#!/usr/bin/bash\n"
        "printf 'mktemp %s\\n' \"$*\" >> \"$CALLS\"\n"
        "exec /usr/bin/mktemp \"$@\"\n"
    )
    mktemp.chmod(0o755)
    opencode = commands / "opencode"
    opencode.write_text(
        "#!/usr/bin/bash\n"
        "case \"$*\" in\n"
        "  --version) printf '%s\\n' \"$OPENCODE_VERSION\" ;;\n"
        "  'debug paths')\n"
        "    printf 'home       %s\\n' \"$HOME\"\n"
        "    printf 'state      %s\\n' \"$DEBUG_STATE_DIR\"\n"
        "    ;;\n"
        "  *) exit 64 ;;\n"
        "esac\n"
    )
    opencode.chmod(0o755)
    move_counter = tmp_path / "move-counter"
    move_counter.write_text("0\n")
    move_counter.chmod(0o600)
    mv = commands / "mv"
    mv.write_text(
        "#!/usr/bin/bash\n"
        "printf 'mv %s\\n' \"$*\" >> \"$CALLS\"\n"
        "count=$(( $(cat \"$MOVE_COUNTER\") + 1 ))\n"
        "printf '%s\\n' \"$count\" > \"$MOVE_COUNTER\"\n"
        "if [ -n \"$FAIL_MOVE_NUMBER\" ] && [ \"$count\" -eq \"$FAIL_MOVE_NUMBER\" ]; then\n"
        "  exit 70\n"
        "fi\n"
        "exec /usr/bin/mv \"$@\"\n"
    )
    mv.chmod(0o755)
    real_uv = shutil.which("uv")
    assert real_uv is not None
    uv = commands / "uv"
    uv.write_text(
        "#!/usr/bin/bash\n"
        "printf 'uv %s\\n' \"$*\" >> \"$CALLS\"\n"
        "case \"$*\" in\n"
        "  *'omniroute issue-key'*) printf '{\"api_key\": \"%s\"}\\n' \"$OMNIROUTE_API_KEY\" ;;\n"
        "  *) exec \"$REAL_UV\" \"$@\" ;;\n"
        "esac\n"
    )
    uv.chmod(0o755)
    if failing_command is not None:
        command = commands / failing_command
        command.write_text("#!/usr/bin/bash\nexit 70\n")
        command.chmod(0o755)
    result = subprocess.run(
        ["/usr/bin/bash", "setup/setup-local-llm-agents.sh"],
        cwd=ROOT,
        env=os.environ
        | {
            "CALLS": str(calls),
            "DEBUG_STATE_DIR": str(resolved_debug_state_dir),
            "FAIL_MOVE_NUMBER": "" if fail_move_number is None else str(fail_move_number),
            "HEALTH_EXIT": str(health_exit),
            "HOME": str(home),
            "LLM_ENV_CONFIG": str(config),
            "MOVE_COUNTER": str(move_counter),
            "OMNIROUTE_API_KEY": omniroute_api_key,
            "OPENCODE_VERSION": opencode_version,
            "PI_CODING_AGENT_DIR": str(pi_path.parent),
            "REAL_UV": real_uv,
            "REAL_YQ": shutil.which("yq") or "yq",
            "XDG_CONFIG_HOME": str(home / ".config"),
            "XDG_STATE_HOME": str(home / ".local/state"),
            "PATH": f"{commands}:{os.environ['PATH']}",
        },
        text=True,
        capture_output=True,
        check=False,
    )
    return result, calls, pi_path, pi_settings_path, opencode_paths, state_path


def test_setup_creates_missing_opencode_state_only_for_compatible_1_18_10(
    tmp_path: pathlib.Path,
) -> None:
    result, _, _, _, _, state_path = run_setup_local_llm_agents(tmp_path)

    assert result.returncode == 0, result.stderr
    assert json.loads(state_path.read_text()) == {
        "recent": [],
        "favorite": [
            {"providerID": "local-llm-env", "modelID": "llama-cpp/gemma4"},
            {"providerID": "local-llm-env", "modelID": "llama-cpp/ornith"},
        ],
        "variant": {},
    }
    assert "close Pi and OpenCode" in result.stdout + result.stderr
    assert "restart Pi and OpenCode" in result.stdout
    assert "fixture-omniroute-api-key" not in result.stdout + result.stderr


@pytest.mark.parametrize("version", ["1.18.9", "1.19.0", "invalid"])
def test_setup_rejects_missing_state_for_unsupported_opencode(
    tmp_path: pathlib.Path, version: str
) -> None:
    result, calls, pi_path, _, opencode_paths, state_path = (
        run_setup_local_llm_agents(tmp_path, opencode_version=version)
    )

    assert result.returncode != 0
    assert not pi_path.parent.exists()
    assert not opencode_paths[0].parent.exists()
    assert not state_path.parent.exists()
    assert "mv " not in calls.read_text()
    assert "fixture-omniroute-api-key" not in result.stdout + result.stderr


def test_setup_rejects_debug_state_path_mismatch_before_staging(
    tmp_path: pathlib.Path,
) -> None:
    result, calls, pi_path, _, opencode_paths, state_path = (
        run_setup_local_llm_agents(
            tmp_path, debug_state_dir=tmp_path / "wrong-state"
        )
    )

    assert result.returncode != 0
    assert not pi_path.parent.exists()
    assert not opencode_paths[0].parent.exists()
    assert not state_path.parent.exists()
    assert "mv " not in calls.read_text()


def test_setup_existing_state_does_not_require_version_or_path_compatibility(
    tmp_path: pathlib.Path,
) -> None:
    result, _, _, _, _, state_path = run_setup_local_llm_agents(
        tmp_path,
        opencode_state_text='{"recent":[],"favorite":[],"variant":{}}',
        opencode_version="unsupported",
        debug_state_dir=tmp_path / "wrong-state",
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(state_path.read_text())["favorite"][:2] == [
        {"providerID": "local-llm-env", "modelID": "llama-cpp/gemma4"},
        {"providerID": "local-llm-env", "modelID": "llama-cpp/ornith"},
    ]


def test_setup_rejects_malformed_opencode_state_before_health(
    tmp_path: pathlib.Path,
) -> None:
    malformed_state = '{"recent":[],"favorite":{},"variant":{}}\n'
    result, calls, pi_path, _, opencode_paths, state_path = (
        run_setup_local_llm_agents(
            tmp_path,
            opencode_state_text=malformed_state,
        )
    )

    assert result.returncode != 0
    assert "curl " not in calls.read_text()
    assert "mv " not in calls.read_text()
    assert state_path.read_text() == malformed_state
    assert not pi_path.parent.exists()
    assert not opencode_paths[0].parent.exists()
    assert "fixture-omniroute-api-key" not in result.stdout + result.stderr


def test_setup_stages_every_target_before_first_replacement(
    tmp_path: pathlib.Path,
) -> None:
    existing_provider = (
        '{"provider":{"local-llm-env":{"models":{"old":{"name":"old"}}}}}\n'
    )
    result, calls, pi_path, settings_path, opencode_paths, state_path = (
        run_setup_local_llm_agents(
            tmp_path,
            opencode_config_json_text=existing_provider,
            opencode_json_text=existing_provider,
            opencode_jsonc_text=existing_provider,
            opencode_state_text='{"recent":[],"favorite":[],"variant":{}}',
        )
    )

    assert result.returncode == 0, result.stderr
    lines = calls.read_text().splitlines()
    first_move = next(
        index for index, line in enumerate(lines) if line.startswith("mv ")
    )
    targets = (pi_path, settings_path, *opencode_paths, state_path)
    for target in targets:
        pattern = f"{target.parent}/.{target.name}.XXXXXX"
        assert any(pattern in line for line in lines[:first_move])


def test_partial_rename_failure_is_explicit_and_idempotent_rerun_repairs_it(
    tmp_path: pathlib.Path,
) -> None:
    kwargs = {
        "pi_text": '{"providers":{"other":{}}}',
        "pi_settings_text": '{"theme":"dark"}',
        "opencode_config_json_text": '{"provider":{"other":{}}}',
        "opencode_state_text": '{"recent":[],"favorite":[],"variant":{}}',
    }
    failed, _, *_ = run_setup_local_llm_agents(
        tmp_path, fail_move_number=2, **kwargs
    )

    assert failed.returncode != 0
    assert "rerun make setup-local-llm-agents" in failed.stderr
    assert "fixture-omniroute-api-key" not in failed.stdout + failed.stderr

    repaired, _, _, settings_path, _, state_path = run_setup_local_llm_agents(
        tmp_path, **kwargs
    )

    assert repaired.returncode == 0, repaired.stderr
    assert json.loads(settings_path.read_text())["enabledModels"] == [
        "local-llm-env/llama-cpp/gemma4",
        "local-llm-env/llama-cpp/ornith",
    ]
    assert json.loads(state_path.read_text())["favorite"][:2] == [
        {"providerID": "local-llm-env", "modelID": "llama-cpp/gemma4"},
        {"providerID": "local-llm-env", "modelID": "llama-cpp/ornith"},
    ]


def test_setup_local_llm_agents_creates_private_provider_files(
    tmp_path: pathlib.Path,
) -> None:
    pi_path = tmp_path / "home/.pi/agent/models.json"
    assert not pi_path.parent.exists()
    result, calls, pi_path, settings_path, opencode_paths, state_path = (
        run_setup_local_llm_agents(tmp_path)
    )
    config_json, opencode_json, opencode_jsonc = opencode_paths

    assert result.returncode == 0, result.stderr
    assert json.loads(pi_path.read_text())["providers"]["local-llm-env"] == {
        "baseUrl": "http://127.0.0.1:20128/v1",
        "api": "openai-completions",
        "apiKey": "fixture-omniroute-api-key",
        "compat": {
            "supportsDeveloperRole": False,
            "supportsReasoningEffort": False,
        },
        "models": [
            {"id": "llama-cpp/gemma4", "contextWindow": 131072, "maxTokens": 8192},
            {"id": "llama-cpp/ornith", "contextWindow": 131072, "maxTokens": 8192},
        ],
    }
    assert not config_json.exists()
    assert not opencode_json.exists()
    assert json.loads(opencode_jsonc.read_text())["provider"]["local-llm-env"] == {
        "npm": "@ai-sdk/openai-compatible",
        "name": "local-llm-env",
        "options": {
            "baseURL": "http://127.0.0.1:20128/v1",
            "apiKey": "fixture-omniroute-api-key",
        },
        "models": {
            "llama-cpp/gemma4": {
                "name": "gemma4",
                "limit": {"context": 131072, "output": 8192},
            },
            "llama-cpp/ornith": {
                "name": "ornith",
                "limit": {"context": 131072, "output": 8192},
            },
        },
    }
    assert stat.S_IMODE(pi_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(settings_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(opencode_jsonc.stat().st_mode) == 0o600
    assert stat.S_IMODE(state_path.stat().st_mode) == 0o600
    calls_made = calls.read_text().splitlines()
    health_index = next(
        index for index, call in enumerate(calls_made) if call.startswith("curl ")
    )
    first_target_stage = next(
        index
        for index, call in enumerate(calls_made)
        if call.startswith("mktemp ") and ".XXXXXX" in call
    )
    assert health_index < first_target_stage
    assert "fixture-omniroute-api-key" not in result.stdout + result.stderr


def test_setup_local_llm_agents_secures_existing_client_directories_and_files(
    tmp_path: pathlib.Path,
) -> None:
    pi_dir = tmp_path / "home/.pi/agent"
    opencode_dir = tmp_path / "home/.config/opencode"
    pi_dir.mkdir(parents=True)
    opencode_dir.mkdir(parents=True)
    pi_dir.chmod(0o755)
    opencode_dir.chmod(0o755)

    result, _, pi_path, settings_path, opencode_paths, state_path = (
        run_setup_local_llm_agents(tmp_path)
    )

    assert result.returncode == 0, result.stderr
    assert stat.S_IMODE(pi_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(opencode_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(state_path.parent.stat().st_mode) == 0o700
    for path in (pi_path, settings_path, opencode_paths[2], state_path):
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_setup_local_llm_agents_updates_every_global_file_that_defines_the_provider(
    tmp_path: pathlib.Path,
) -> None:
    old_provider = '{"local-llm-env":{"models":{"old-model":{"name":"old"}}}}'
    result, _, _, _, opencode_paths, _ = run_setup_local_llm_agents(
        tmp_path,
        opencode_config_json_text=f'{{"configOnly":true,"provider":{old_provider}}}\n',
        opencode_json_text=f'{{"model":"other/model","provider":{old_provider}}}\n',
        opencode_jsonc_text=(
            f'// retain this comment\n{{"agent":{{}},"provider":{old_provider}}}\n'
        ),
    )

    assert result.returncode == 0, result.stderr
    for path in opencode_paths:
        text = path.read_text()
        parsed = json.loads(re.sub(r"(?m)^\s*//[^\n]*", "", text))
        provider = parsed["provider"]["local-llm-env"]
        assert provider["models"] == {
            "llama-cpp/gemma4": {
                "name": "gemma4",
                "limit": {"context": 131072, "output": 8192},
            },
            "llama-cpp/ornith": {
                "name": "ornith",
                "limit": {"context": 131072, "output": 8192},
            },
        }
        assert "old-model" not in provider["models"]
    assert json.loads(opencode_paths[0].read_text())["configOnly"] is True
    assert json.loads(opencode_paths[1].read_text())["model"] == "other/model"
    assert "// retain this comment" in opencode_paths[2].read_text()


@pytest.mark.parametrize(
    ("config_text", "json_text", "jsonc_text", "expected_name"),
    [
        ('{"configOnly":true}\n', None, None, "config.json"),
        ('{"configOnly":true}\n', '{"jsonOnly":true}\n', None, "opencode.json"),
        (
            '{"configOnly":true}\n',
            '{"jsonOnly":true}\n',
            '// jsonc preferred\n{"jsoncOnly":true}\n',
            "opencode.jsonc",
        ),
    ],
)
def test_setup_local_llm_agents_adds_provider_to_preferred_existing_global_file(
    tmp_path: pathlib.Path,
    config_text: str | None,
    json_text: str | None,
    jsonc_text: str | None,
    expected_name: str,
) -> None:
    result, _, _, _, opencode_paths, _ = run_setup_local_llm_agents(
        tmp_path,
        opencode_config_json_text=config_text,
        opencode_json_text=json_text,
        opencode_jsonc_text=jsonc_text,
    )

    assert result.returncode == 0, result.stderr
    for path, original in zip(
        opencode_paths, (config_text, json_text, jsonc_text), strict=True
    ):
        if path.name == expected_name:
            assert '"local-llm-env"' in path.read_text()
        elif original is None:
            assert not path.exists()
        else:
            assert path.read_text() == original


def test_setup_local_llm_agents_rejects_concatenated_pi_json_without_replacement(
    tmp_path: pathlib.Path,
) -> None:
    original_pi = '{"providers":{}}\n{}\n'
    result, calls, pi_path, _, opencode_paths, _ = run_setup_local_llm_agents(
        tmp_path,
        pi_text=original_pi,
    )

    assert result.returncode != 0
    assert pi_path.read_text() == original_pi
    assert not opencode_paths[0].parent.exists()
    assert "mv " not in calls.read_text()
    assert "fixture-omniroute-api-key" not in result.stdout + result.stderr


def test_setup_local_llm_agents_rejects_non_object_pi_providers_before_creating_opencode_directory(
    tmp_path: pathlib.Path,
) -> None:
    original_pi = '{"providers":[]}\n'
    result, calls, pi_path, _, opencode_paths, _ = run_setup_local_llm_agents(
        tmp_path,
        pi_text=original_pi,
    )

    assert result.returncode != 0
    assert pi_path.read_text() == original_pi
    assert not opencode_paths[0].parent.exists()
    assert "mv " not in calls.read_text()
    assert "fixture-omniroute-api-key" not in result.stdout + result.stderr


def test_setup_local_llm_agents_rejects_duplicate_opencode_provider_before_creating_pi_directory(
    tmp_path: pathlib.Path,
) -> None:
    duplicate_provider = (
        '{"provider":{"local-llm-env":{},"local-llm-env":{}}}\n'
    )
    result, calls, pi_path, _, opencode_paths, _ = run_setup_local_llm_agents(
        tmp_path,
        opencode_jsonc_text=duplicate_provider,
    )

    assert result.returncode != 0
    assert not pi_path.parent.exists()
    assert opencode_paths[2].read_text() == duplicate_provider
    assert "mv " not in calls.read_text()
    assert "fixture-omniroute-api-key" not in result.stdout + result.stderr


@pytest.mark.parametrize("invalid_whitespace", ["\v", "\N{NO-BREAK SPACE}"])
def test_setup_local_llm_agents_rejects_non_jsonc_whitespace_before_creating_pi_directory(
    tmp_path: pathlib.Path, invalid_whitespace: str
) -> None:
    invalid_source = f'{{"provider":{invalid_whitespace}{{"other":{{}}}}}}\n'
    result, calls, pi_path, _, opencode_paths, _ = run_setup_local_llm_agents(
        tmp_path,
        opencode_jsonc_text=invalid_source,
    )

    assert result.returncode != 0
    assert not pi_path.parent.exists()
    assert opencode_paths[2].read_bytes() == invalid_source.encode()
    assert "mv " not in calls.read_text()


@pytest.mark.parametrize("bad_global_index", [0, 1, 2])
def test_setup_local_llm_agents_rejects_each_invalid_global_before_creating_pi_directory(
    tmp_path: pathlib.Path, bad_global_index: int
) -> None:
    bad_text = '{"provider": }\n'
    global_texts: list[str | None] = [None, None, None]
    global_texts[bad_global_index] = bad_text
    result, calls, pi_path, _, opencode_paths, _ = run_setup_local_llm_agents(
        tmp_path,
        opencode_config_json_text=global_texts[0],
        opencode_json_text=global_texts[1],
        opencode_jsonc_text=global_texts[2],
    )

    assert result.returncode != 0
    assert not pi_path.parent.exists()
    assert opencode_paths[bad_global_index].read_text() == bad_text
    assert "mv " not in calls.read_text()
    assert "fixture-omniroute-api-key" not in result.stdout + result.stderr


def test_setup_local_llm_agents_replaces_only_the_local_pi_provider(
    tmp_path: pathlib.Path,
) -> None:
    result, _, pi_path, _, _, _ = run_setup_local_llm_agents(
        tmp_path,
        pi_text=(
            '{"providers":{"other":{"baseUrl":"https://example.invalid/v1"},'
            '"local-llm-env":{"models":[{"id":"old-model"}],"stale":true}},'
            '"otherSetting":true}\n'
        ),
    )

    assert result.returncode == 0, result.stderr
    updated = json.loads(pi_path.read_text())
    assert updated["otherSetting"] is True
    assert updated["providers"]["other"] == {
        "baseUrl": "https://example.invalid/v1"
    }
    assert updated["providers"]["local-llm-env"] == {
        "baseUrl": "http://127.0.0.1:20128/v1",
        "api": "openai-completions",
        "apiKey": "fixture-omniroute-api-key",
        "compat": {
            "supportsDeveloperRole": False,
            "supportsReasoningEffort": False,
        },
        "models": [
            {"id": "llama-cpp/gemma4", "contextWindow": 131072, "maxTokens": 8192},
            {"id": "llama-cpp/ornith", "contextWindow": 131072, "maxTokens": 8192},
        ],
    }


@pytest.mark.parametrize(
    "settings_text",
    [None, '{"enabledModels":[]}\n', '{"theme":"dark","enabledModels":["other/model"]}\n'],
)
def test_setup_local_llm_agents_sets_exact_pi_cycle(
    tmp_path: pathlib.Path, settings_text: str | None
) -> None:
    result, _, _, settings_path, _, _ = run_setup_local_llm_agents(
        tmp_path, pi_settings_text=settings_text
    )

    assert result.returncode == 0, result.stderr
    settings = json.loads(settings_path.read_text())
    assert settings["enabledModels"] == [
        "local-llm-env/llama-cpp/gemma4",
        "local-llm-env/llama-cpp/ornith",
    ]
    if settings_text and "theme" in settings_text:
        assert settings["theme"] == "dark"


def test_setup_local_llm_agents_preserves_explicit_pi_defaults(
    tmp_path: pathlib.Path,
) -> None:
    defaults = {
        "defaultProvider": "other",
        "defaultModel": "model",
        "defaultThinkingLevel": "high",
        "enabledModels": ["old/model"],
    }
    result, _, _, settings_path, _, _ = run_setup_local_llm_agents(
        tmp_path, pi_settings_text=json.dumps(defaults)
    )

    assert result.returncode == 0, result.stderr
    updated = json.loads(settings_path.read_text())
    assert {key: updated[key] for key in defaults if key != "enabledModels"} == {
        key: value for key, value in defaults.items() if key != "enabledModels"
    }


@pytest.mark.parametrize("settings_text", ["[]\n", "{}\n{}\n"])
def test_setup_local_llm_agents_rejects_invalid_pi_settings_without_replacement(
    tmp_path: pathlib.Path, settings_text: str
) -> None:
    original_pi = '{"providers":{"other":{}}}\n'
    original_opencode = '{"provider":{"other":{}}}\n'
    result, calls, pi_path, settings_path, opencode_paths, _ = (
        run_setup_local_llm_agents(
            tmp_path,
            pi_text=original_pi,
            pi_settings_text=settings_text,
            opencode_config_json_text=original_opencode,
        )
    )

    assert result.returncode != 0
    assert pi_path.read_text() == original_pi
    assert settings_path.read_text() == settings_text
    assert opencode_paths[0].read_text() == original_opencode
    assert "mv " not in calls.read_text()
    assert "fixture-omniroute-api-key" not in result.stdout + result.stderr


@pytest.mark.parametrize(
    "config_text",
        [
            VALID_AGENT_SETUP_CONFIG.replace("    ctx_size: 131072\n", "", 1),
            VALID_AGENT_SETUP_CONFIG.replace("  - alias: ornith", "  - alias: gemma4"),
        VALID_AGENT_SETUP_CONFIG.replace("    ctx_size: 131072", "    ctx_size: true", 1),
        VALID_AGENT_SETUP_CONFIG.replace(
            "    client_max_output_tokens: 8192",
            "    client_max_output_tokens: 0",
            1,
        ),
        VALID_AGENT_SETUP_CONFIG.replace("    ctx_size: 131072", "    ctx_size: 4096", 1),
    ],
)
def test_setup_local_llm_agents_rejects_invalid_model_records_before_health(
    tmp_path: pathlib.Path, config_text: str
) -> None:
    result, calls, pi_path, settings_path, opencode_paths, _ = (
        run_setup_local_llm_agents(tmp_path, config_text=config_text)
    )

    assert result.returncode != 0
    assert "curl " not in calls.read_text()
    assert "mv " not in calls.read_text()
    assert not pi_path.parent.exists()
    assert not settings_path.exists()
    assert not opencode_paths[0].parent.exists()
    assert "fixture-omniroute-api-key" not in result.stdout + result.stderr


def test_setup_local_llm_agents_migrates_missing_output_limit_before_replacement(
    tmp_path: pathlib.Path,
) -> None:
    config_text = VALID_AGENT_SETUP_CONFIG.replace(
        "    client_max_output_tokens: 8192\n", "", 1
    )

    result, _, pi_path, _, _, _ = run_setup_local_llm_agents(
        tmp_path, config_text=config_text
    )

    assert result.returncode == 0, result.stderr
    provider = json.loads(pi_path.read_text())["providers"]["local-llm-env"]
    assert provider["models"][0]["maxTokens"] == 8192


@pytest.mark.parametrize(
    ("config_text", "health_exit", "pi_text"),
    [
        (VALID_AGENT_SETUP_CONFIG.replace("port: 18123", "port: 0"), 0, None),
        (
            VALID_AGENT_SETUP_CONFIG.replace("enabled: true", "enabled: false"),
            0,
            None,
        ),
        (VALID_AGENT_SETUP_CONFIG, 1, None),
        (VALID_AGENT_SETUP_CONFIG, 0, '{"providers": }\n'),
    ],
)
def test_setup_local_llm_agents_rejects_invalid_inputs_without_replacing_files(
    tmp_path: pathlib.Path,
    config_text: str,
    health_exit: int,
    pi_text: str | None,
) -> None:
    original_pi = pi_text or '{"providers":{"other":{}}}\n'
    original_opencode = '{"provider":{"other":{}}}\n'
    result, calls, pi_path, _, opencode_paths, _ = run_setup_local_llm_agents(
        tmp_path,
        config_text=config_text,
        pi_text=original_pi,
        opencode_config_json_text=original_opencode,
        health_exit=health_exit,
    )

    assert result.returncode != 0
    assert pi_path.read_text() == original_pi
    assert opencode_paths[0].read_text() == original_opencode
    assert "mv " not in calls.read_text()
    assert "fixture-omniroute-api-key" not in result.stdout + result.stderr


@pytest.mark.parametrize("failing_command", ("mktemp", "chmod"))
def test_setup_local_llm_agents_staging_failures_leave_existing_files_unchanged(
    tmp_path: pathlib.Path, failing_command: str
) -> None:
    original_pi = '{"providers":{"other":{}}}\n'
    original_opencode = '{"provider":{"other":{}}}\n'
    result, calls, pi_path, settings_path, opencode_paths, _ = run_setup_local_llm_agents(
        tmp_path,
        pi_text=original_pi,
        opencode_config_json_text=original_opencode,
        failing_command=failing_command,
    )

    assert result.returncode != 0
    assert pi_path.read_text() == original_pi
    assert not settings_path.exists()
    assert opencode_paths[0].read_text() == original_opencode
    assert "mv " not in calls.read_text()
    assert "fixture-omniroute-api-key" not in result.stdout + result.stderr


def test_setup_local_llm_agents_does_not_create_client_directories_when_mkdir_fails(
    tmp_path: pathlib.Path,
) -> None:
    result, calls, pi_path, _, opencode_paths, _ = run_setup_local_llm_agents(
        tmp_path, failing_command="mkdir"
    )

    assert result.returncode != 0
    assert not pi_path.parent.exists()
    assert not opencode_paths[0].parent.exists()
    assert "mv " not in calls.read_text()
    assert "fixture-omniroute-api-key" not in result.stdout + result.stderr


def test_setup_local_llm_agents_stages_each_replacement_with_its_target(
    tmp_path: pathlib.Path,
) -> None:
    existing_provider = (
        '{"provider":{"local-llm-env":{"models":{"old":{"name":"old"}}}}}\n'
    )
    result, calls, pi_path, settings_path, opencode_paths, state_path = run_setup_local_llm_agents(
        tmp_path,
        opencode_config_json_text=existing_provider,
        opencode_json_text=existing_provider,
        opencode_jsonc_text=existing_provider,
    )

    assert result.returncode == 0, result.stderr
    moves = [
        line.split()
        for line in calls.read_text().splitlines()
        if line.startswith("mv ")
    ]
    targets = (pi_path, settings_path, *opencode_paths, state_path)
    assert len(moves) == len(targets)
    for move, target in zip(moves, targets, strict=True):
        assert pathlib.Path(move[-2]).parent == target.parent
        assert pathlib.Path(move[-1]) == target


def yq_value(config: pathlib.Path, expression: str) -> str:
    """Read one scalar from a test configuration with the real yq binary."""
    return subprocess.run(
        [shutil.which("yq") or "yq", "-r", expression, str(config)],
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()


def yq_model_benchmark_value(
    config: pathlib.Path, alias: str, metric: str
) -> str:
    """Read one model benchmark scalar with query values passed as data."""
    return subprocess.run(
        [
            shutil.which("yq") or "yq",
            "-r",
            (
                "(.models[] | select(.alias == strenv(MODEL_ALIAS)) "
                "| .benchmark.vulkan[strenv(METRIC)])"
            ),
            str(config),
        ],
        env=os.environ | {"MODEL_ALIAS": alias, "METRIC": metric},
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()


def run_log_file_excerpt(
    tmp_path: pathlib.Path,
    arguments: tuple[str, ...],
    *,
    api_key: str = "fixture-stream-secret",
    completion_marker: pathlib.Path | None = None,
    fail_consumer: bool = False,
    fail_redaction: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Invoke the streaming diagnostic helper with an isolated private config."""
    config = tmp_path / "models.yml"
    config.write_text(f"server:\n  api_key: {api_key}\n")
    config.chmod(0o600)
    helper = tmp_path / "log-file-excerpt.sh"
    helper.write_text(
        "#!/usr/bin/env bash\n"
        'source "$TEST_REPO_DIR/tools/lib.sh"\n'
        + (
            "redact_text() { printf '%s' \"$1\"; }\n"
            "_redact_stream() { cat && printf '%s\\n' complete > "
            '"$COMPLETION_MARKER"; }\n'
            if completion_marker is not None
            else ""
        )
        + (
            "redact_text() { printf '%s' \"$1\"; }\n"
            "_redact_stream() { cat >/dev/null; return 17; }\n"
            if fail_redaction
            else ""
        )
        + ("head() { return 17; }\n" if fail_consumer else "")
        + 'log_file_excerpt "$@"\n'
    )
    environment = os.environ | {
        "LLM_ENV_CONFIG": str(config),
        "TEST_REPO_DIR": str(ROOT),
    }
    if completion_marker is not None:
        environment["COMPLETION_MARKER"] = str(completion_marker)
    return subprocess.run(
        ["/usr/bin/bash", str(helper), *arguments],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )


@pytest.mark.parametrize(
    "arguments",
    [(), ("Label", "file"), ("Label", "file", "1", "extra")],
)
def test_log_file_excerpt_requires_exactly_three_arguments(
    tmp_path: pathlib.Path, arguments: tuple[str, ...]
) -> None:
    result = run_log_file_excerpt(tmp_path, arguments)

    assert result.returncode == 64


@pytest.mark.parametrize("max_bytes", ["", "-1", "+1", "1.0", " 1", "1x"])
def test_log_file_excerpt_rejects_invalid_max_bytes(
    tmp_path: pathlib.Path, max_bytes: str
) -> None:
    source = tmp_path / "diagnostic.txt"
    source.write_text("content")

    result = run_log_file_excerpt(
        tmp_path, ("Diagnostic", str(source), max_bytes)
    )

    assert result.returncode == 64


@pytest.mark.parametrize("source_kind", ["missing", "directory"])
def test_log_file_excerpt_rejects_missing_or_nonregular_input(
    tmp_path: pathlib.Path, source_kind: str
) -> None:
    source = tmp_path / "diagnostic"
    if source_kind == "directory":
        source.mkdir()

    result = run_log_file_excerpt(tmp_path, ("Diagnostic", str(source), "12"))

    assert result.returncode == 66


def test_log_file_excerpt_rejects_an_unreadable_regular_file(
    tmp_path: pathlib.Path,
) -> None:
    source = tmp_path / "diagnostic.txt"
    source.write_text("content")
    source.chmod(0)
    if os.access(source, os.R_OK):
        pytest.skip("effective user can read mode-000 files")

    result = run_log_file_excerpt(tmp_path, ("Diagnostic", str(source), "12"))

    assert result.returncode == 66


def test_log_file_excerpt_marks_an_empty_file(tmp_path: pathlib.Path) -> None:
    source = tmp_path / "empty.txt"
    source.touch()

    result = run_log_file_excerpt(tmp_path, ("Empty diagnostic", str(source), "12"))

    assert result.returncode == 0, result.stderr
    assert result.stdout == "Empty diagnostic:\n  (empty)\n"


def test_log_file_excerpt_allows_a_zero_byte_excerpt(tmp_path: pathlib.Path) -> None:
    source = tmp_path / "diagnostic.txt"
    source.write_text("content")

    result = run_log_file_excerpt(tmp_path, ("Zero excerpt", str(source), "0"))

    assert result.returncode == 0, result.stderr
    assert result.stdout == "Zero excerpt:\n\n"


def test_log_file_excerpt_bounds_and_drains_large_input(tmp_path: pathlib.Path) -> None:
    source = tmp_path / "client stderr.txt"
    source.write_bytes(b"ab" + b"x" * (1024 * 1024))

    result = run_log_file_excerpt(tmp_path, ("Client stderr", str(source), "2"))

    assert result.returncode == 0, result.stderr
    assert result.stdout == "Client stderr:\n  ab\n"


def test_log_file_excerpt_indents_each_emitted_line(tmp_path: pathlib.Path) -> None:
    source = tmp_path / "diagnostic.txt"
    source.write_text("first\nsecond")

    result = run_log_file_excerpt(tmp_path, ("Label", str(source), "8"))

    assert result.returncode == 0, result.stderr
    assert result.stdout == "Label:\n  first\n  se\n"
    assert "fixture-stream-secret" not in result.stdout + result.stderr


def test_log_file_excerpt_normalizes_redaction_failure_to_status_one(
    tmp_path: pathlib.Path,
) -> None:
    source = tmp_path / "diagnostic.txt"
    source.write_text("content")

    result = run_log_file_excerpt(
        tmp_path,
        ("Diagnostic", str(source), "12"),
        fail_redaction=True,
    )

    assert result.returncode == 1


def test_log_file_excerpt_failed_consumer_still_drains_complete_stream(
    tmp_path: pathlib.Path,
) -> None:
    source = tmp_path / "diagnostic.txt"
    with source.open("wb") as source_stream:
        source_stream.truncate(2 * 1024 * 1024)
    completion_marker = tmp_path / "excerpt-drained"

    result = run_log_file_excerpt(
        tmp_path,
        ("Diagnostic", str(source), "12"),
        completion_marker=completion_marker,
        fail_consumer=True,
    )

    assert result.returncode == 1
    assert completion_marker.is_file()
    assert completion_marker.read_text() == "complete\n"


def run_diagnostic_helper(
    tmp_path: pathlib.Path,
    text: str,
    *,
    api_key: str = "fixture-secret",
    keep: bool = False,
    unreadable_nested: bool = False,
    unreadable_regular_file: bool = False,
) -> tuple[subprocess.CompletedProcess[str], pathlib.Path, pathlib.Path]:
    """Run shared diagnostics with an isolated configuration and temporary directory."""
    config = tmp_path / "models.yml"
    config.write_text(f"server:\n  api_key: {api_key}\n")
    temporary_directory = tmp_path / "temporary"
    temporary_directory.mkdir()
    artifact_path_file = tmp_path / "artifact-path"
    helper = tmp_path / "diagnostic-helper.sh"
    helper.write_text(
        "#!/usr/bin/env bash\n"
        "source \"$TEST_REPO_DIR/tools/lib.sh\"\n"
        "diagnostic_directory=\"$(prepare_diagnostic_dir diagnostic-helper)\"\n"
        "printf '%s' \"$DIAGNOSTIC_TEXT\" > \"$diagnostic_directory/raw.txt\"\n"
        "if [ \"$UNREADABLE_NESTED\" = 1 ]; then\n"
        "  nested_directory=\"${diagnostic_directory}/unreadable-${DIAGNOSTIC_TEXT}\"\n"
        "  mkdir \"$nested_directory\"\n"
        "  printf '%s' \"$DIAGNOSTIC_TEXT\" > \"$nested_directory/raw.txt\"\n"
        "  chmod 000 \"$nested_directory\"\n"
        "fi\n"
        "if [ \"$UNREADABLE_REGULAR_FILE\" = 1 ]; then\n"
        "  secret_directory=\"${diagnostic_directory}/secret-directory-${DIAGNOSTIC_TEXT}\"\n"
        "  mkdir \"$secret_directory\"\n"
        "  chmod 755 \"$secret_directory\"\n"
        "  unreadable_file=\"${secret_directory}/unreadable-diagnostic.txt\"\n"
        "  printf '%s' \"$DIAGNOSTIC_TEXT\" > \"$unreadable_file\"\n"
        "  chmod 000 \"$unreadable_file\"\n"
        "fi\n"
        "log_command \"$DIAGNOSTIC_TEXT\"\n"
        "log_block 'Raw result' \"$DIAGNOSTIC_TEXT\"\n"
        "log_block 'Empty result' ''\n"
        "finish_diagnostic_dir \"$diagnostic_directory\"\n"
        "printf '%s' \"$diagnostic_directory\" > \"$ARTIFACT_PATH\"\n"
    )
    environment = os.environ | {
        "ARTIFACT_PATH": str(artifact_path_file),
        "DIAGNOSTIC_TEXT": text,
        "LLM_ENV_CONFIG": str(config),
        "LLM_ENV_KEEP_CHECK_ARTIFACTS": "1" if keep else "",
        "TEST_REPO_DIR": str(ROOT),
        "UNREADABLE_NESTED": "1" if unreadable_nested else "0",
        "UNREADABLE_REGULAR_FILE": "1" if unreadable_regular_file else "0",
        "TMPDIR": str(temporary_directory),
    }
    result = subprocess.run(
        ["/usr/bin/bash", str(helper)],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    artifact = (
        pathlib.Path(artifact_path_file.read_text())
        if artifact_path_file.exists()
        else tmp_path / "missing-artifact"
    )
    return result, artifact, temporary_directory


def test_diagnostic_helper_discards_artifacts_by_default(
    tmp_path: pathlib.Path,
) -> None:
    """Without LLM_ENV_KEEP_CHECK_ARTIFACTS, the raw diagnostic directory must not survive."""
    result, artifact, _ = run_diagnostic_helper(tmp_path, "diagnostic text")

    assert result.returncode == 0, result.stderr
    assert "Command: " in result.stdout
    assert "Raw result:" in result.stdout
    assert "Empty result:\n  (empty)" in result.stdout
    assert not artifact.exists()


def test_log_nonempty_block_omits_empty_text_and_returns_zero_under_set_e(
    tmp_path: pathlib.Path,
) -> None:
    config = tmp_path / "models.yml"
    config.write_text("server:\n  api_key: fixture-secret\n")
    helper = tmp_path / "nonempty-block.sh"
    helper.write_text(
        "#!/usr/bin/env bash\n"
        "set -e\n"
        'source "$TEST_REPO_DIR/tools/lib.sh"\n'
        'log_nonempty_block "Empty stderr" ""\n'
        'log_nonempty_block "Client stderr" "client warning"\n'
        "printf 'continued\\n'\n"
    )
    helper.chmod(helper.stat().st_mode | stat.S_IXUSR)

    result = subprocess.run(
        ["/usr/bin/bash", str(helper)],
        cwd=ROOT,
        env=os.environ
        | {"LLM_ENV_CONFIG": str(config), "TEST_REPO_DIR": str(ROOT)},
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Empty stderr:" not in result.stdout
    assert "Client stderr:\n  client warning" in result.stdout
    assert "continued" in result.stdout


def test_diagnostic_helper_keeps_only_private_retained_artifacts(
    tmp_path: pathlib.Path,
) -> None:
    """Retained diagnostics must stay mode-0700/0600 private to the invoking user."""
    result, artifact, _ = run_diagnostic_helper(
        tmp_path, "fixture-secret", keep=True
    )

    assert result.returncode == 0, result.stderr
    assert artifact.is_dir()
    assert stat.S_IMODE(artifact.stat().st_mode) == 0o700
    assert str(artifact) in result.stdout
    retained_files = [path for path in artifact.rglob("*") if path.is_file()]
    assert retained_files
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in retained_files)


def test_diagnostic_helper_discards_artifacts_when_traversal_fails(
    tmp_path: pathlib.Path,
) -> None:
    """An unreadable nested directory must fail without retaining any artifact."""
    result, artifact, temporary_directory = run_diagnostic_helper(
        tmp_path,
        "diagnostic text",
        keep=True,
        unreadable_nested=True,
    )

    assert result.returncode != 0
    assert "Diagnostics retained:" not in result.stdout
    assert not artifact.exists()
    assert not any(temporary_directory.iterdir())


def test_diagnostic_helper_discards_unreadable_regular_files_without_path_leaks(
    tmp_path: pathlib.Path,
) -> None:
    """An unreadable regular file must not expose its path or leave its file list."""
    result, artifact, temporary_directory = run_diagnostic_helper(
        tmp_path,
        "diagnostic text",
        keep=True,
        unreadable_regular_file=True,
    )

    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "unreadable-diagnostic.txt" not in output
    assert "Diagnostics retained:" not in result.stdout
    assert not artifact.exists()
    assert not list(temporary_directory.glob("llm-env-diagnostic-files.*"))
    assert not any(temporary_directory.iterdir())


def test_start_generates_key_only_when_empty(tmp_path: pathlib.Path) -> None:
    """Starting an unconfigured server must persist a secret with restrictive
    file permissions. setup/network.sh's post-start banner intentionally
    prints the generated key (and the OmniRoute dashboard password) to
    stdout for the operator running `make start` interactively -- see
    scripts/show-secrets.sh's header comment for why these are no longer
    treated as localhost-only secrets -- so this test only asserts the
    persisted-file protection, not stdout silence."""
    result, config, calls = run_lifecycle_script(
        tmp_path, "scripts/start.sh", api_key="", config_mode=0o644
    )

    assert result.returncode == 0, result.stderr
    assert yq_value(config, ".server.api_key")
    assert stat.S_IMODE(config.stat().st_mode) == 0o600
    assert yq_value(config, ".server.api_key") not in result.stderr
    assert "systemctl --user start llm-server.service" in calls.read_text()


def test_start_computes_and_persists_resource_limits_before_rendering(
    tmp_path: pathlib.Path,
) -> None:
    """make start (this task) must compute and persist resources.llm_server
    on every run, not just once at `make setup` -- otherwise a stale value
    left over from a previous setup (different host, or a config edited by
    hand) silently reaches pylib/compose.py unchanged."""
    result, config, calls = run_lifecycle_script(tmp_path, "scripts/start.sh")

    assert result.returncode == 0, result.stderr
    assert yq_value(config, ".resources.llm_server.cpus") == "4"
    assert yq_value(config, ".resources.llm_server.memory_mib") == "8000"
    recorded = calls.read_text().splitlines()
    resources_call = next(i for i, call in enumerate(recorded) if call.endswith(" resources"))
    render_unit_call = next(i for i, call in enumerate(recorded) if "render-unit.sh" in call)
    assert resources_call < render_unit_call


def test_start_overwrites_a_stale_persisted_resource_limit(
    tmp_path: pathlib.Path,
) -> None:
    """A config carrying resources.llm_server values computed on different
    (e.g. bigger) hardware during a past `make setup` must not reach the
    rendered container unchanged -- start must recompute against THIS
    host and overwrite them every time."""
    result, config, _ = run_lifecycle_script(tmp_path, "scripts/start.sh")

    assert result.returncode == 0, result.stderr
    # The fixture config (run_lifecycle_script) has no resources.llm_server
    # section at all going in -- migrate_config's defaults (cpus: 0,
    # memory_mib: 0) stand in for "stale/never computed". After a
    # successful start, the persisted values must be the freshly computed
    # ones the resources stub returned, not the migrate_config defaults.
    assert yq_value(config, ".resources.llm_server.cpus") == "4"
    assert yq_value(config, ".resources.llm_server.memory_mib") == "8000"


def test_start_dies_loudly_when_the_host_is_below_the_fixed_resource_floors(
    tmp_path: pathlib.Path,
) -> None:
    """compute_resource_limits() raises ResourceError when the host can't
    even reserve the fixed CPU/RAM floors -- previously only `make setup`
    ever surfaced this (by calling `llmenv resources`); `make start` skipped
    straight from budget to rendering and could launch an uncapped
    container on exactly the host where that's most dangerous."""
    result, _, calls = run_lifecycle_script(
        tmp_path, "scripts/start.sh", resources_failure=True
    )

    assert result.returncode != 0
    assert "host has 3 CPUs; more than 3 are required" in (result.stdout + result.stderr)
    recorded = calls.read_text()
    assert "systemctl --user start llm-server.service" not in recorded
    assert f"bash {ROOT / 'setup/render-unit.sh'}" not in recorded


def test_start_rejects_invalid_concurrency_before_key_or_service_output(
    tmp_path: pathlib.Path,
) -> None:
    result, config, calls = run_lifecycle_script(
        tmp_path,
        "scripts/start.sh",
        api_key="",
        config_mode=0o644,
        parallel_slots=2,
    )

    assert result.returncode != 0
    assert "runtime.parallel_slots must be 1" in result.stderr
    assert yq_value(config, ".server.api_key") == ""
    recorded = calls.read_text()
    assert "yq -i .server.api_key" not in recorded
    assert "systemctl --user start" not in recorded
    assert f"bash {ROOT / 'setup/render-unit.sh'}" not in recorded
    assert not (config.parent / "presets.ini").exists()


def test_start_rejects_invalid_sampling_before_key_or_service_output(
    tmp_path: pathlib.Path,
) -> None:
    result, config, calls = run_lifecycle_script(
        tmp_path,
        "scripts/start.sh",
        api_key="",
        config_mode=0o644,
        sampling_temperature="-1",
    )

    assert result.returncode != 0
    assert (
        "model test sampling.temperature must be a finite non-negative number"
        in result.stderr
    )
    assert yq_value(config, ".server.api_key") == ""
    recorded = calls.read_text()
    assert "yq -i .server.api_key" not in recorded
    assert "systemctl --user start" not in recorded
    assert f"bash {ROOT / 'setup/render-unit.sh'}" not in recorded
    assert not (config.parent / "presets.ini").exists()


def test_start_migrates_config_before_reading_runtime_or_generating_key(
    tmp_path: pathlib.Path,
) -> None:
    result, _, calls = run_lifecycle_script(
        tmp_path, "scripts/start.sh", api_key=""
    )

    assert result.returncode == 0, result.stderr
    recorded = calls.read_text().splitlines()
    migration = next(index for index, call in enumerate(recorded) if "migrate-config" in call)
    runtime_read = next(index for index, call in enumerate(recorded) if ".runtime.models_max" in call)
    key_write = next(
        index
        for index, call in enumerate(recorded)
        if call.startswith("yq -i .server.api_key")
    )
    assert migration < runtime_read
    assert migration < key_write


@pytest.mark.parametrize(
    ("script", "api_key"),
    [("setup/enable-boot.sh", ""), ("scripts/key-reset.sh", "existing-key")],
)
def test_key_mutating_entrypoints_reject_invalid_config_before_changing_key(
    tmp_path: pathlib.Path, script: str, api_key: str
) -> None:
    result, config, calls = run_lifecycle_script(
        tmp_path, script, api_key=api_key, parallel_slots=2
    )

    assert result.returncode != 0
    assert "runtime.parallel_slots must be 1" in result.stderr
    assert yq_value(config, ".server.api_key") == api_key
    recorded = calls.read_text()
    assert "yq -i .server.api_key" not in recorded
    assert f"bash {ROOT / 'setup/render-unit.sh'}" not in recorded


def test_key_writes_secure_config_before_persisting_a_secret(
    tmp_path: pathlib.Path,
) -> None:
    """A generated or reset key must never be written to a mode-0644 config."""
    for script, api_key in (("scripts/start.sh", ""), ("scripts/key-reset.sh", "existing-key")):
        case_path = tmp_path / pathlib.Path(script).stem
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
    result, config, _ = run_lifecycle_script(tmp_path, "scripts/start.sh")

    assert result.returncode == 0, result.stderr
    assert yq_value(config, ".server.api_key") == "existing-key"


def test_start_warns_when_omniroute_is_unreachable_but_still_succeeds(
    tmp_path: pathlib.Path,
) -> None:
    """Must not depend on the default OmniRoute port (20128) actually being
    closed on the machine running the tests -- a dev box with the real
    stack up has something genuinely listening there, which previously
    made wait_for_tcp_port() succeed for real and this test flake/fail
    outside a fully isolated CI sandbox. Bind an ephemeral port instead, so
    "unreachable" is guaranteed true regardless of the host's own state."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        unreachable_port = probe.getsockname()[1]

    result, _config, _calls = run_lifecycle_script(
        tmp_path,
        "scripts/start.sh",
        env_overrides={"LLM_ENV_HEALTH_TIMEOUT_SECONDS": "1"},
        omniroute_port=unreachable_port,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OmniRoute did not become reachable" in result.stdout + result.stderr


def test_start_stops_active_router_before_budgeting(tmp_path: pathlib.Path) -> None:
    """A config change must unload the old router before measuring available VRAM."""
    result, _, calls = run_lifecycle_script(
        tmp_path, "scripts/start.sh", active=True
    )

    assert result.returncode == 0, result.stderr
    recorded = calls.read_text().splitlines()
    active_check = recorded.index("systemctl --user is-active --quiet llm-server.service")
    stop = recorded.index("systemctl --user stop llm-server.service")
    budget = next(index for index, call in enumerate(recorded) if " budget " in call)
    start = recorded.index("systemctl --user start llm-server.service")
    assert active_check < stop < budget < start


def test_key_reset_restarts_an_active_server(tmp_path: pathlib.Path) -> None:
    """Rotating an active server key must load the replacement immediately."""
    result, config, calls = run_lifecycle_script(
        tmp_path, "scripts/key-reset.sh", active=True, config_mode=0o644
    )

    assert result.returncode == 0, result.stderr
    assert yq_value(config, ".server.api_key") != "existing-key"
    assert stat.S_IMODE(config.stat().st_mode) == 0o600
    assert "systemctl --user stop llm-server.service" in calls.read_text()
    assert "systemctl --user start llm-server.service" in calls.read_text()


def test_key_reset_does_not_start_an_inactive_server(tmp_path: pathlib.Path) -> None:
    """Rotating a stopped server key must preserve its stopped state."""
    result, _, calls = run_lifecycle_script(tmp_path, "scripts/key-reset.sh", active=False)

    assert result.returncode == 0, result.stderr
    assert "systemctl --user start llm-server.service" not in calls.read_text()


def test_show_secrets_prints_the_api_key_and_dashboard_password(
    tmp_path: pathlib.Path,
) -> None:
    result, _config, _calls = run_lifecycle_script(
        tmp_path, "scripts/show-secrets.sh", api_key="existing-key"
    )

    assert result.returncode == 0, result.stderr
    assert "existing-key" in result.stdout
    assert "(not set)" in result.stdout


def test_show_secrets_fails_without_a_config(tmp_path: pathlib.Path) -> None:
    missing_config = tmp_path / "home/.config/llm-env/models.yml"
    result = subprocess.run(
        ["bash", str(SCRIPT_DIR / "show-secrets.sh")],
        env={**os.environ, "LLM_ENV_CONFIG": str(missing_config)},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "no config at" in result.stderr


def test_show_secrets_prints_the_master_key(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    config = home / ".config" / "llm-env" / "models.yml"
    config.parent.mkdir(parents=True)
    config.write_text(
        "server:\n  api_key: sk-test\n"
        "omniroute:\n  initial_password: dash-test\n",
        encoding="utf-8",
    )
    env_file = tmp_path / ".env"
    env_file.write_text("OMNI_ROUTER_MASTER_KEY=master-test\n", encoding="utf-8")
    result = subprocess.run(
        ["/usr/bin/bash", "scripts/show-secrets.sh"],
        cwd=ROOT,
        env=os.environ
        | {"HOME": str(home), "LLM_ENV_CONFIG": str(config), "LLM_ENV_ENV_FILE": str(env_file)},
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "master-test" in result.stdout


def test_repo_gitignores_the_secret_bearing_files() -> None:
    """`.env` holds OMNI_ROUTER_MASTER_KEY (which mints OmniRoute API keys)
    and `models.yml` holds the server API key / dashboard password -- a
    routine `git add -A` after setup must never be able to commit either."""
    entries = (ROOT / ".gitignore").read_text(encoding="utf-8").split()
    assert "models.yml" in entries
    assert ".env" in entries


def test_enable_boot_prepares_a_secure_key_without_starting(tmp_path: pathlib.Path) -> None:
    """Boot setup must create a private key without starting or budget-checking."""
    result, config, calls = run_lifecycle_script(
        tmp_path, "setup/enable-boot.sh", api_key="", config_mode=0o644
    )

    assert result.returncode == 0, result.stderr
    assert yq_value(config, ".server.api_key")
    assert stat.S_IMODE(config.stat().st_mode) == 0o600
    assert f"bash {ROOT / 'setup/render-unit.sh'}" in calls.read_text()
    assert "start.sh" not in calls.read_text()
    assert " budget " not in calls.read_text()


def test_enable_boot_renders_a_health_gated_mdns_user_unit(
    tmp_path: pathlib.Path,
) -> None:
    """Boot setup must make mDNS discoverable as a reloaded user unit."""
    result, config, calls = run_lifecycle_script(tmp_path, "setup/enable-boot.sh")
    mdns_unit = config.parent.parent / "systemd/user/llm-server-mdns.service"
    wrapper_unit = config.parent.parent / "systemd/user/llm-server.service"

    assert result.returncode == 0, result.stderr
    unit = mdns_unit.read_text()
    wrapper = wrapper_unit.read_text()
    assert "Wants=llm-server-mdns.service" in wrapper
    assert "Requires=llm-server.service" in unit
    assert "After=llm-server.service" in unit
    assert "PartOf=llm-server.service" in unit
    assert "Restart=on-failure" in unit
    assert "ExecStart=podman compose" in wrapper
    assert "ExecStartPre=" in unit
    assert "http://127.0.0.1:8000/health" in unit
    assert "ExecStart=/usr/bin/bash" in unit
    assert "tools/publish-mdns-hostname.sh llm.local llm 8000" in unit
    assert "systemctl --user daemon-reload" in calls.read_text()
    assert "systemctl --user enable llm-server.service" in calls.read_text()


def run_check_server(
    tmp_path: pathlib.Path,
    completion_body: dict[str, str],
    *,
    completion_curl_exits: dict[str, int] | None = None,
    completion_responses: dict[str, str] | None = None,
    completion_statuses: dict[str, int] | None = None,
    model_list_body: str = '{"data":[{"id":"gemma4"},{"id":"ornith"}]}',
    model_yq_exit: int = 0,
    model_jq_exit: int = 0,
    models_enabled: bool = True,
    verbose: bool = False,
    omniroute_api_key: str = "sk-fixture-omniroute-scoped-key",
    omniroute_issue_key_exit: int = 0,
) -> tuple[subprocess.CompletedProcess[str], pathlib.Path]:
    """Run the online contract check with deterministic API command stubs."""
    real_jq = shutil.which("jq")
    real_yq = shutil.which("yq")
    assert real_jq is not None
    assert real_yq is not None
    enabled = "true" if models_enabled else "false"
    commands = tmp_path / "bin"
    commands.mkdir()
    calls = tmp_path / "calls"
    calls.touch()
    config = tmp_path / "models.yml"
    config.write_text(
        "server:\n"
        "  port: 8000\n"
        "  api_key: fixture-secret\n"
        "omniroute:\n"
        "  port: 20128\n"
        "  initial_password: omniroute-dashboard-password\n"
        "models:\n"
        "  - alias: gemma4\n"
        f"    enabled: {enabled}\n"
        "    client_max_output_tokens: 2048\n"
        "  - alias: ornith\n"
        f"    enabled: {enabled}\n"
        "    client_max_output_tokens: 8192\n"
        "    check_timeout_seconds: 600\n"
    )

    curl = commands / "curl"
    curl.write_text(
        "#!/usr/bin/bash\n"
        "url=\"\"\n"
        "body_file=\"\"\n"
        "data=\"\"\n"
        "time_limit=\"\"\n"
        "auth_conf=\"\"\n"
        "cookie_write=\"\"\n"
        "cookie_read=\"\"\n"
        "for argument in \"$@\"; do\n"
        "  case \"$argument\" in http://*|https://*) url=\"$argument\" ;; esac\n"
        "  case \"${previous:-}\" in\n"
        "    -o) body_file=\"$argument\" ;;\n"
        "    -K) auth_conf=\"$argument\" ;;\n"
        "    -c) cookie_write=\"$argument\" ;;\n"
        "    -b) cookie_read=\"$argument\" ;;\n"
        "    -d|--data-raw) data=\"$argument\" ;;\n"
        "    --max-time) time_limit=\"$argument\" ;;\n"
        "  esac\n"
        "  previous=\"$argument\"\n"
        "done\n"
        "jq -cn --arg argv \"$*\" --arg url \"$url\" --arg timeout \"$time_limit\" \\\n"
        "    --arg payload \"$data\" \\\n"
        "    '{argv: $argv, url: $url, timeout: $timeout, payload: $payload}' >> \"$CALLS\"\n"
        "write_response() {\n"
        "  if [ -n \"$body_file\" ]; then printf '%s\\n' \"$1\" > \"$body_file\"; else printf '%s\\n' \"$1\"; fi\n"
        "}\n"
        "case \"$url\" in\n"
        "  */health) write_response '{\"status\":\"ok\"}'; printf '200' ;;\n"
        "  */v1/models) write_response \"$MODEL_LIST_BODY\"; printf '200' ;;\n"
        "  http://127.0.0.1:20128/api/auth/login)\n"
        "    [ -n \"$cookie_write\" ] && printf 'omniroute-session-cookie\\n' > \"$cookie_write\"\n"
        "    write_response '{\"success\":true}'\n"
        "    printf '200'\n"
        "    ;;\n"
        "  http://127.0.0.1:20128/api/providers)\n"
        "    if [ -n \"$cookie_read\" ] && [ \"$(<\"$cookie_read\")\" = omniroute-session-cookie ]; then\n"
        "      write_response '{\"connections\":[{\"name\":\"llm-env-local\",\"isActive\":true}]}'\n"
        "      printf '200'\n"
        "    else\n"
        "      write_response '{\"error\":\"unauthorized\"}'\n"
        "      printf '401'\n"
        "    fi\n"
        "    ;;\n"
        "  http://127.0.0.1:20128/v1/chat/completions)\n"
        # REQUIRE_API_KEY=true: only an issued scoped key is accepted here.
        "    if [ -z \"$auth_conf\" ] || [[ \"$(<\"$auth_conf\")\" != *\"$OMNIROUTE_API_KEY\"* ]]; then\n"
        "      write_response '{\"error\":\"Invalid API key\"}'; printf '401'; exit 0\n"
        "    fi\n"
        "    case \"$data\" in\n"
        "      *gemma4*|*ornith*)\n"
        "        write_response '{\"choices\":[{\"message\":{\"content\":\"ready\"}}]}'\n"
        "        printf '200'\n"
        "        ;;\n"
        "      *) write_response '{\"error\":\"unknown model\"}'; printf '400' ;;\n"
        "    esac\n"
        "    ;;\n"
        "  */v1/chat/completions)\n"
        "    if [ -n \"$auth_conf\" ] && [[ \"$(<\"$auth_conf\")\" == *definitely-not-the-key* ]]; then\n"
        "      write_response '{\"error\":\"unauthorized\"}'; printf '401'; exit 0\n"
        "    fi\n"
        "    case \"$data\" in\n"
        "      *gemma4*) response=\"$GEMMA4_RESPONSE\"; status=\"$GEMMA4_STATUS\"; exit_code=\"$GEMMA4_CURL_EXIT\" ;;\n"
        "      *ornith*) response=\"$ORNITH_RESPONSE\"; status=\"$ORNITH_STATUS\"; exit_code=\"$ORNITH_CURL_EXIT\" ;;\n"
        "      *) write_response '{\"error\":\"unknown model\"}'; printf '400'; exit 0 ;;\n"
        "    esac\n"
        "    write_response \"$response\"\n"
        "    printf '%s' \"$status\"\n"
        "    [ \"$exit_code\" = 0 ] || { printf 'completion curl failed\\n' >&2; exit \"$exit_code\"; }\n"
        "    ;;\n"
        "  *) exit 64 ;;\n"
        "esac\n"
    )
    curl.chmod(curl.stat().st_mode | stat.S_IXUSR)

    jq = commands / "jq"
    jq.write_text(
        "#!/usr/bin/bash\n"
        "for argument in \"$@\"; do\n"
        "  if [ \"$argument\" = '.[] | @base64' ]; then\n"
        "    [ \"$MODEL_JQ_EXIT\" -eq 0 ] || exit \"$MODEL_JQ_EXIT\"\n"
        "  fi\n"
        "done\n"
        "exec \"$REAL_JQ\" \"$@\"\n"
    )
    jq.chmod(jq.stat().st_mode | stat.S_IXUSR)

    yq = commands / "yq"
    yq.write_text(
        "#!/usr/bin/bash\n"
        "for argument in \"$@\"; do\n"
        "  if [ \"$argument\" = '[.models[] | select(.enabled)]' ]; then\n"
        "    [ \"$MODEL_YQ_EXIT\" -eq 0 ] || exit \"$MODEL_YQ_EXIT\"\n"
        "  fi\n"
        "done\n"
        "exec \"$REAL_YQ\" \"$@\"\n"
    )
    yq.chmod(yq.stat().st_mode | stat.S_IXUSR)

    # tools/lib.sh's llmenv() shells out to `uv run llmenv.py ...`; the
    # OmniRoute completions probe uses it to mint/reuse the scoped API key
    # that REQUIRE_API_KEY=true makes mandatory on /v1/*.
    uv = commands / "uv"
    uv.write_text(
        "#!/usr/bin/bash\n"
        "printf '%s\\n' \"uv $*\" >> \"$CALLS_PLAIN\"\n"
        "case \"$*\" in\n"
        "  *'omniroute issue-key'*)\n"
        "    [ \"$OMNIROUTE_ISSUE_KEY_EXIT\" -eq 0 ] || {\n"
        "      printf 'could not reach OmniRoute\\n' >&2\n"
        "      exit \"$OMNIROUTE_ISSUE_KEY_EXIT\"\n"
        "    }\n"
        "    printf '{\"api_key\": \"%s\"}\\n' \"$OMNIROUTE_API_KEY\"\n"
        "    ;;\n"
        "  *) exit 64 ;;\n"
        "esac\n"
    )
    uv.chmod(uv.stat().st_mode | stat.S_IXUSR)

    completion_responses = completion_responses or {}
    completion_statuses = completion_statuses or {}
    completion_curl_exits = completion_curl_exits or {}
    gemma4_response = completion_responses.get(
        "gemma4",
        json.dumps(
            {"choices": [{"message": {"content": completion_body["gemma4"]}}]},
            separators=(",", ":"),
        ),
    )
    ornith_response = completion_responses.get(
        "ornith",
        json.dumps(
            {"choices": [{"message": {"content": completion_body["ornith"]}}]},
            separators=(",", ":"),
        ),
    )
    plain_calls = tmp_path / "calls-plain"
    plain_calls.touch()
    environment = os.environ | {
        "CALLS": str(calls),
        "CALLS_PLAIN": str(plain_calls),
        "OMNIROUTE_API_KEY": omniroute_api_key,
        "OMNIROUTE_ISSUE_KEY_EXIT": str(omniroute_issue_key_exit),
        "GEMMA4_CURL_EXIT": str(completion_curl_exits.get("gemma4", 0)),
        "GEMMA4_RESPONSE": gemma4_response,
        "GEMMA4_STATUS": str(completion_statuses.get("gemma4", 200)),
        "HOME": str(tmp_path / "home"),
        "LLM_ENV_CONFIG": str(config),
        "MODEL_JQ_EXIT": str(model_jq_exit),
        "MODEL_LIST_BODY": '{"data":[]}' if not models_enabled else model_list_body,
        "MODEL_YQ_EXIT": str(model_yq_exit),
        "ORNITH_CURL_EXIT": str(completion_curl_exits.get("ornith", 0)),
        "ORNITH_RESPONSE": ornith_response,
        "ORNITH_STATUS": str(completion_statuses.get("ornith", 200)),
        "PATH": f"{commands}:/usr/bin:/bin",
        "REAL_JQ": real_jq,
        "REAL_YQ": real_yq,
    } | ({"LLM_ENV_CHECK_VERBOSE": "1"} if verbose else {})
    result = subprocess.run(
        ["/usr/bin/bash", "scripts/check-server.sh"],
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
    # ornith's direct completion already failed, so its OmniRoute completion
    # is skipped rather than re-probed -- one less call than a naive 2+2+1.
    parsed = [json.loads(line) for line in calls.read_text().splitlines()]
    assert sum("/v1/chat/completions" in row["url"] for row in parsed) == 4
    assert (
        "Verdict: SKIP identity=omniroute completion model=ornith "
        "reason=server completion model=ornith already failed"
    ) in result.stderr


def test_check_server_accepts_normalized_ready_for_every_enabled_model(
    tmp_path: pathlib.Path,
) -> None:
    """Punctuation and case must not prevent a valid ready response."""
    result, _ = run_check_server(
        tmp_path,
        {"gemma4": "READY.", "ornith": " ready "},
    )

    assert result.returncode == 0, result.stderr
    assert "max_tokens: 256, stream: false" not in (
        ROOT / "scripts/check-server.sh"
    ).read_text()


def test_check_server_probes_omniroute_with_an_issued_scoped_key(
    tmp_path: pathlib.Path,
) -> None:
    """With REQUIRE_API_KEY=true OmniRoute's /v1/* validates the bearer
    against its issued-key table, so the dashboard password is no longer a
    usable token -- the probe must mint/reuse a scoped key instead, and must
    never put either secret on curl's command line."""
    result, calls = run_check_server(tmp_path, {"gemma4": "ready", "ornith": "ready"})

    assert result.returncode == 0, result.stderr
    assert "Verdict: PASS identity=omniroute api key" in result.stdout
    assert "Verdict: PASS identity=omniroute completion model=gemma4" in result.stdout
    plain = (tmp_path / "calls-plain").read_text()
    assert "omniroute issue-key" in plain
    parsed = [json.loads(line) for line in calls.read_text().splitlines()]
    omniroute_calls = [
        row for row in parsed if row["url"] == "http://127.0.0.1:20128/v1/chat/completions"
    ]
    assert omniroute_calls
    for row in omniroute_calls:
        assert "omniroute-dashboard-password" not in row["argv"]
        assert "sk-fixture-omniroute-scoped-key" not in row["argv"]
        assert "-K" in row["argv"]


def test_check_server_skips_omniroute_completions_without_an_api_key(
    tmp_path: pathlib.Path,
) -> None:
    """A failed key issuance must fail loudly and skip the completions rather
    than silently probe /v1/* with no usable credential."""
    result, calls = run_check_server(
        tmp_path, {"gemma4": "ready", "ornith": "ready"}, omniroute_issue_key_exit=1
    )

    assert result.returncode != 0
    assert "Verdict: FAIL stage=api key issuance" in result.stderr
    assert "reason=no OmniRoute API key available" in result.stderr
    parsed = [json.loads(line) for line in calls.read_text().splitlines()]
    assert not [
        row for row in parsed if row["url"] == "http://127.0.0.1:20128/v1/chat/completions"
    ]


def test_check_server_prints_a_copy_pasteable_request_response_and_curl_template(
    tmp_path: pathlib.Path,
) -> None:
    """Successful API checks must show complete, directly-runnable diagnostic
    records when LLM_ENV_CHECK_VERBOSE=1 is set -- a default run only prints a
    concise verdict per PASS, see test_check_server_prints_concise_pass_rows_by_default."""
    result, _ = run_check_server(
        tmp_path, {"gemma4": "ready", "ornith": "ready"}, verbose=True
    )
    combined = result.stdout + result.stderr

    assert result.returncode == 0, result.stderr
    assert "Command: curl --silent --show-error" in result.stdout
    assert "Authorization: Bearer fixture-secret" in result.stdout
    assert '"content": "Reply with exactly: ready"' in result.stdout
    assert '"max_tokens": 2048' in result.stdout
    assert '"max_tokens": 8192' in result.stdout
    assert "HTTP response:" in result.stdout
    assert "HTTP stderr:" not in result.stdout
    assert "Response parsing stderr:\n  (empty)" not in result.stdout
    assert "Assistant content:\n  ready" in result.stdout
    assert "Expectation:\n  normalized assistant content: ready" in result.stdout
    assert "Verdict: PASS" in result.stdout
    assert combined.count("Request payload:") == 3
    assert 'Request payload:\n  {"model":"x"' not in combined
    assert 'Request payload:\n  {"model":"gemma4"' not in combined
    assert 'Request payload:\n  {"model":"ornith"' not in combined


def test_check_server_prints_concise_pass_rows_by_default(
    tmp_path: pathlib.Path,
) -> None:
    """A default (non-verbose) run must not dump the Identity/Command/HTTP
    response/Expectation block for a PASS -- only the one-line verdict."""
    result, _ = run_check_server(tmp_path, {"gemma4": "ready", "ornith": "ready"})

    assert result.returncode == 0, result.stderr
    assert "Identity:" not in result.stdout
    assert "Command:" not in result.stdout
    assert "HTTP response:" not in result.stdout
    assert "Assistant content:" not in result.stdout
    assert "Expectation:" not in result.stdout
    assert "Verdict: PASS identity=server health" in result.stdout
    assert "Verdict: PASS identity=server completion model=gemma4" in result.stdout
    assert "Verdict: PASS identity=omniroute completion model=ornith" in result.stdout
    assert "Results: " in result.stdout


def test_check_server_reports_a_completion_failure_without_hiding_other_models(
    tmp_path: pathlib.Path,
) -> None:
    """A failed completion must not prevent records for later enabled models."""
    result, _ = run_check_server(
        tmp_path, {"gemma4": "ready", "ornith": "not ready"}
    )

    assert result.returncode != 0
    assert "gemma4" in result.stdout and "Verdict: PASS" in result.stdout
    assert "ornith" in result.stdout
    assert "Verdict: FAIL stage=normalized-value mismatch" in result.stderr
    assert "Results:" in result.stdout


def test_check_server_reports_completion_curl_failure(tmp_path: pathlib.Path) -> None:
    """A curl exit must retain its captured stderr and fail that completion."""
    result, _ = run_check_server(
        tmp_path,
        {"gemma4": "ready", "ornith": "ready"},
        completion_curl_exits={"gemma4": 7},
    )

    assert result.returncode != 0
    assert "Verdict: FAIL stage=curl failure" in result.stderr
    assert "HTTP stderr:\n  completion curl failed" in result.stdout
    assert "server completion model=ornith" in result.stdout


def test_check_server_reports_non_2xx_completion(tmp_path: pathlib.Path) -> None:
    """An HTTP failure must be distinct from a curl failure."""
    result, _ = run_check_server(
        tmp_path,
        {"gemma4": "ready", "ornith": "ready"},
        completion_responses={"gemma4": '{"error":"unavailable"}'},
        completion_statuses={"gemma4": 503},
    )

    assert result.returncode != 0
    assert "Verdict: FAIL stage=HTTP response" in result.stderr
    assert "status=503" in result.stderr


def test_check_server_reports_invalid_completion_json(tmp_path: pathlib.Path) -> None:
    """Malformed completion JSON must expose the parsing failure."""
    result, _ = run_check_server(
        tmp_path,
        {"gemma4": "ready", "ornith": "ready"},
        completion_responses={"gemma4": "not-json"},
    )

    assert result.returncode != 0
    assert "Verdict: FAIL stage=invalid JSON" in result.stderr
    assert "Response parsing stderr:\n  jq: parse error:" in result.stdout


def test_check_server_reports_missing_assistant_content(tmp_path: pathlib.Path) -> None:
    """A valid completion object without content must fail explicitly."""
    result, _ = run_check_server(
        tmp_path,
        {"gemma4": "ready", "ornith": "ready"},
        completion_responses={"gemma4": '{"choices":[{"message":{}}]}'},
    )

    assert result.returncode != 0
    assert "Verdict: FAIL stage=missing assistant content" in result.stderr


@pytest.mark.parametrize("response", ["null", "false"])
def test_check_server_treats_valid_scalar_responses_as_missing_content(
    tmp_path: pathlib.Path, response: str
) -> None:
    """Valid scalar JSON is not malformed, but cannot contain assistant content."""
    result, _ = run_check_server(
        tmp_path,
        {"gemma4": "ready", "ornith": "ready"},
        completion_responses={"gemma4": response},
    )

    assert result.returncode != 0
    assert "Verdict: FAIL stage=missing assistant content" in result.stderr
    assert "Verdict: FAIL stage=invalid JSON" not in result.stderr


def test_check_server_reports_malformed_model_listing(tmp_path: pathlib.Path) -> None:
    """A malformed successful model list must retain its jq diagnostic."""
    result, _ = run_check_server(
        tmp_path,
        {"gemma4": "ready", "ornith": "ready"},
        model_list_body="not-json",
    )

    assert result.returncode != 0
    assert "Verdict: FAIL stage=response parsing identity=server model listing" in result.stderr
    assert "Response parsing stderr:\n  jq: parse error:" in result.stdout


def test_check_server_keeps_each_models_budget_and_timeout_together(tmp_path):
    result, calls = run_check_server(
        tmp_path, {"gemma4": "ready", "ornith": "ready"}
    )
    assert result.returncode == 0, result.stderr
    parsed = [json.loads(line) for line in calls.read_text().splitlines()]
    completion_rows = [
        row
        for row in parsed
        if "/v1/chat/completions" in row["url"]
        and json.loads(row["payload"])["model"] != "x"
    ]
    assert len(completion_rows) == 4
    observed = {
        (
            json.loads(row["payload"])["model"],
            json.loads(row["payload"])["max_tokens"],
            row["timeout"],
        )
        for row in completion_rows
    }
    assert observed == {
        ("gemma4", 2048, "131"),
        ("llama-cpp/gemma4", 2048, "131"),
        ("ornith", 8192, "600"),
        ("llama-cpp/ornith", 8192, "600"),
    }


def test_check_server_keeps_the_invalid_key_probe_at_ten_seconds(tmp_path):
    result, calls = run_check_server(
        tmp_path, {"gemma4": "ready", "ornith": "ready"}
    )
    assert result.returncode == 0, result.stderr
    parsed = [json.loads(line) for line in calls.read_text().splitlines()]
    invalid = [
        row
        for row in parsed
        if row["payload"] and json.loads(row["payload"]).get("model") == "x"
    ]
    assert len(invalid) == 1
    assert invalid[0]["timeout"] == "10"


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"model_yq_exit": 41}, "could not enumerate enabled models"),
        ({"model_jq_exit": 42}, "could not enumerate enabled models"),
        ({"models_enabled": False}, "no enabled models were checked"),
    ],
)
def test_check_server_fails_when_no_direct_model_check_can_run(
    tmp_path, kwargs, message
):
    result, calls = run_check_server(
        tmp_path,
        {"gemma4": "ready", "ornith": "ready"},
        **kwargs,
    )
    assert result.returncode != 0
    assert message in result.stderr
    parsed = [json.loads(line) for line in calls.read_text().splitlines()]
    non_auth_completions = [
        row
        for row in parsed
        if "/v1/chat/completions" in row["url"]
        and json.loads(row["payload"]).get("model") != "x"
    ]
    assert non_auth_completions == []


def run_agent_check(
    tmp_path: pathlib.Path,
    *,
    clients: dict[str, str],
    arguments: tuple[str, ...] = (),
    model_alias: str = "gemma4",
    model_aliases: tuple[str, ...] | None = None,
    weather_body: str | None = None,
    fx_body: str | None = None,
    api_key: str = "test-key-not-a-secret",
    agent_source_timestamp: str | None = None,
    agent_weather_evidence_override: str | None = None,
    agent_fx_evidence_override: str | None = None,
    agent_response_prefix: str = "",
    agent_response_suffix: str = "",
    keep_artifacts: bool = False,
    xtrace: bool = False,
    agent_client_stderr: str = "",
    agent_client_stderr_file: pathlib.Path | None = None,
    agent_parser_stderr: str = "",
    fenced_parser_stderr: str = "",
    source_parser_stderr: str = "",
    bounded_results: tuple[str | bytes, ...] = (),
    bounded_exit_statuses: tuple[int, ...] = (),
    bounded_cli_stderr: str = "",
    bounded_result_fault: str = "",
    raw_bounded_results: bool = False,
    bounded_counter_mismatch: str = "",
    bounded_wc_override: str = "",
    final_size_fault: str = "",
    config_after_startup: str = "",
    inherited_redaction_override: str | None = None,
    finalizer_failure: bool = False,
    workspace_setup_fault: str = "",
    fail_pi_configuration: bool = False,
    oversized_source: str = "",
    oversized_final_client: str = "",
    source_at_limit: str = "",
    final_at_limit_client: str = "",
    comparator_failure_once: bool = False,
    uv_cache_result: str | None = None,
    offline_bootstrap_failure: bool = False,
) -> tuple[subprocess.CompletedProcess[str], pathlib.Path, pathlib.Path]:
    """Run the opt-in check with an isolated client path and fake APIs."""
    real_jq = shutil.which("jq")
    real_yq = shutil.which("yq")
    real_date = shutil.which("date")
    assert real_jq is not None
    assert real_yq is not None
    assert real_date is not None
    assert bounded_result_fault in {"", "mktemp", "chmod", "write", "read"}
    assert bounded_counter_mismatch in {"", "transcript", "stderr"}
    assert bounded_wc_override in {"", "transcript_bytes", "stderr_bytes"}
    assert final_size_fault in {
        "",
        "failure",
        "empty",
        "malformed",
        "negative",
        "noncanonical",
    }
    assert config_after_startup in {"", "remove", "rotate"}
    assert workspace_setup_fault in {"", "chmod", "diagnostic"}
    assert oversized_source in {"", "models", "weather", "fx"}
    assert oversized_final_client in {"", "pi", "opencode"}
    assert source_at_limit in {"", "models"}
    assert final_at_limit_client in {"", "pi", "opencode"}

    source_response_limit_bytes = 1_048_576
    aliases = model_aliases if model_aliases is not None else (model_alias,)

    fixture_bodies = tmp_path / "fixture-bodies"
    fixture_bodies.mkdir()
    fixture_bodies.chmod(0o700)

    def write_sized_json(
        path: pathlib.Path,
        prefix: bytes,
        suffix: bytes,
        size: int,
        *,
        sentinel: bytes,
    ) -> None:
        filler_bytes = size - len(prefix) - len(sentinel) - len(suffix)
        assert filler_bytes >= 0
        path.write_bytes(prefix + (b"x" * filler_bytes) + sentinel + suffix)
        path.chmod(0o600)
        assert path.stat().st_size == size

    def write_left_padded_json(
        path: pathlib.Path, body: dict[str, object], size: int
    ) -> None:
        encoded = json.dumps(body, separators=(",", ":")).encode()
        assert len(encoded) <= size
        path.write_bytes((b" " * (size - len(encoded))) + encoded)
        path.chmod(0o600)
        assert path.stat().st_size == size

    response_prefix_file = fixture_bodies / "response-prefix"
    response_prefix_file.write_text(agent_response_prefix)
    response_prefix_file.chmod(0o600)

    source_limit_file = fixture_bodies / "source-at-limit"
    if source_at_limit == "models":
        model_data = json.dumps(
            [{"id": alias} for alias in aliases], separators=(",", ":")
        ).encode()
        write_sized_json(
            source_limit_file,
            b'{"data":' + model_data + b',"padding":"',
            b'"}',
            source_response_limit_bytes,
            sentinel=b"source-at-limit-sentinel",
        )

    oversized_source_file = fixture_bodies / "oversized-source"
    if oversized_source:
        source_prefixes = {
            "models": b'{"data":'
            + json.dumps(
                [{"id": alias} for alias in aliases], separators=(",", ":")
            ).encode()
            + b',"padding":"',
            "weather": (
                b'{"current":{"time":"2026-07-27T13:00:01",'
                b'"temperature_2m":16.3,"weather_code":3},"padding":"'
            ),
            "fx": (
                b'{"time_last_update_utc":"2026-07-27T00:00:02",'
                b'"rates":{"CLP":946.527902},"padding":"'
            ),
        }
        write_sized_json(
            oversized_source_file,
            source_prefixes[oversized_source],
            b'"}',
            source_response_limit_bytes + 1,
            sentinel=b"oversized-source-sentinel",
        )

    weather_evidence = {
        "source_url": (
            "https://api.open-meteo.com/v1/forecast?latitude=-33.4489&"
            "longitude=-70.6693&current=temperature_2m,weather_code&"
            "timezone=America%2FSantiago"
        ),
        "source_timestamp": "2026-07-27T13:00:01",
        "temperature_2m": 16.3,
        "weather_code": 3,
    }
    oversized_final_file = fixture_bodies / "oversized-final"
    if oversized_final_client:
        write_left_padded_json(
            oversized_final_file,
            weather_evidence | {"fixture": "oversized-final-sentinel"},
            source_response_limit_bytes,
        )
        with oversized_final_file.open("ab") as stream:
            stream.write(b" ")
        assert oversized_final_file.stat().st_size == source_response_limit_bytes + 1

    final_limit_file = fixture_bodies / "final-at-limit"
    if final_at_limit_client:
        write_left_padded_json(
            final_limit_file,
            weather_evidence | {"fixture": "final-at-limit-sentinel"},
            source_response_limit_bytes,
        )

    commands = tmp_path / "bin"
    commands.mkdir()
    calls = tmp_path / "calls"
    calls.touch()
    artifacts = tmp_path / "agent-artifacts"
    artifacts.mkdir()
    diagnostic_tmpdir = tmp_path / "diagnostic-tmp"
    diagnostic_tmpdir.mkdir()
    source_counter = tmp_path / "source-counter"
    source_counter.write_text("0\n")
    bounded_counter = tmp_path / "bounded-counter"
    bounded_counter.write_text("0\n")
    bounded_calls = tmp_path / "bounded-calls"
    bounded_calls.touch()
    workspace_record = tmp_path / "workspace-path"
    evidence_parser_calls = tmp_path / "evidence-parser-calls"
    evidence_parser_calls.touch()
    comparator_calls = tmp_path / "comparator-calls"
    comparator_calls.touch()
    comparator_failure_marker = tmp_path / "comparator-failed"
    uv_calls = tmp_path / "uv-calls"
    uv_calls.touch()
    uv_cache = tmp_path / "uv-cache"
    uv_cache.mkdir()
    resolved_uv_cache = str(uv_cache) if uv_cache_result is None else uv_cache_result
    bounded_results_dir = tmp_path / "bounded-results"
    bounded_results_dir.mkdir()
    bounded_emitted_results_dir = tmp_path / "bounded-emitted-results"
    bounded_emitted_results_dir.mkdir()
    for index, bounded_result in enumerate(bounded_results):
        result_bytes = (
            bounded_result.encode() if isinstance(bounded_result, str) else bounded_result
        )
        (bounded_results_dir / str(index)).write_bytes(result_bytes)
    bounded_statuses_dir = tmp_path / "bounded-statuses"
    bounded_statuses_dir.mkdir()
    for index, bounded_status in enumerate(bounded_exit_statuses):
        (bounded_statuses_dir / str(index)).write_text(f"{bounded_status}\n")
    config = tmp_path / "models.yml"
    config.write_text(
        "server:\n"
        "  port: 8000\n"
        f"  api_key: {api_key}\n"
    )

    _mock_dirname(commands)
    for name, executable in {
        "cat": "/usr/bin/cat",
        "chmod": "/usr/bin/chmod",
        "date": real_date,
        "find": "/usr/bin/find",
        "grep": "/usr/bin/grep",
        "head": "/usr/bin/head",
        "mkdir": "/usr/bin/mkdir",
        "mktemp": "/usr/bin/mktemp",
        "mv": "/usr/bin/mv",
        "rm": "/usr/bin/rm",
        "sed": "/usr/bin/sed",
        "systemctl": "/usr/bin/true",
        "systemd-run": "/usr/bin/true",
        "wc": "/usr/bin/wc",
        "yq": real_yq,
    }.items():
        command = commands / name
        command.write_text(f"#!/usr/bin/bash\nexec {executable!s} \"$@\"\n")
        command.chmod(command.stat().st_mode | stat.S_IXUSR)

    mktemp = commands / "mktemp"
    mktemp.write_text(
        "#!/usr/bin/bash\n"
        'if [ "$#" -eq 1 ] && [ "$1" = -d ]; then\n'
        '    workspace="$(/usr/bin/mktemp -d)" || exit $?\n'
        '    printf "%s" "$workspace" > "$AGENT_CHECK_WORKSPACE_RECORD" || exit $?\n'
        '    printf "%s\\n" "$workspace"\n'
        "    exit 0\n"
        "fi\n"
        'if [ "$AGENT_CHECK_WORKSPACE_SETUP_FAULT" = diagnostic ] '
        '&& [[ "${!#}" == "$TMPDIR"/llm-env-agents.XXXXXX ]]; then\n'
        "    exit 74\n"
        "fi\n"
        'exec /usr/bin/mktemp "$@"\n'
    )
    mktemp.chmod(mktemp.stat().st_mode | stat.S_IXUSR)

    if bounded_result_fault == "mktemp":
        mktemp.write_text(
            "#!/usr/bin/bash\n"
            'if [ "$#" -eq 1 ] && [ "$1" = -d ]; then\n'
            '    workspace="$(/usr/bin/mktemp -d)" || exit $?\n'
            '    printf "%s" "$workspace" > "$AGENT_CHECK_WORKSPACE_RECORD" || exit $?\n'
            '    printf "%s\\n" "$workspace"\n'
            "    exit 0\n"
            "fi\n"
            'if [ "$AGENT_CHECK_WORKSPACE_SETUP_FAULT" = diagnostic ] '
            '&& [[ "${!#}" == "$TMPDIR"/llm-env-agents.XXXXXX ]]; then\n'
            "    exit 74\n"
            "fi\n"
            'if [[ "${!#}" == */bounded-result.XXXXXX ]]; then\n'
            "    printf '%s\\n' 'bounded result mktemp failed' >&2\n"
            "    exit 71\n"
            "fi\n"
            'exec /usr/bin/mktemp "$@"\n'
        )
        mktemp.chmod(mktemp.stat().st_mode | stat.S_IXUSR)

    find = commands / "find"
    find.write_text(
        "#!/usr/bin/bash\n"
        'if [ "$AGENT_CHECK_FINALIZER_FAILURE" = 1 ] '
        '&& [[ "$1" == "$TMPDIR"/llm-env-agents.* ]]; then\n'
        "    printf '%s\\n' 'injected diagnostic traversal failure' >&2\n"
        "    exit 79\n"
        "fi\n"
        'exec /usr/bin/find "$@"\n'
    )
    find.chmod(find.stat().st_mode | stat.S_IXUSR)

    wc = commands / "wc"
    wc.write_text(
        "#!/usr/bin/bash\n"
        'input="$(/usr/bin/readlink "/proc/$$/fd/0")"\n'
        'if [[ "$input" == */assistant-final.* ]]; then\n'
        '    case "$AGENT_CHECK_FINAL_SIZE_FAULT" in\n'
        "        failure) printf '%s\\n' 'injected final size measurement failure' >&2; exit 73 ;;\n"
        "        empty) exit 0 ;;\n"
        "        malformed) printf '%s\\n' not-a-count; exit 0 ;;\n"
        "        negative) printf '%s\\n' -1; exit 0 ;;\n"
        "        noncanonical) printf '%s\\n' 01; exit 0 ;;\n"
        "    esac\n"
        "fi\n"
        'if [ "$AGENT_CHECK_BOUNDED_WC_OVERRIDE" = transcript_bytes ] '
        '&& [[ "$input" == */client-transcript.* ]]; then\n'
        "    printf '%s\\n' 33554433\n"
        "    exit 0\n"
        "fi\n"
        'if [ "$AGENT_CHECK_BOUNDED_WC_OVERRIDE" = stderr_bytes ] '
        '&& [[ "$input" == */client-stderr.* ]]; then\n'
        "    printf '%s\\n' 33554433\n"
        "    exit 0\n"
        "fi\n"
        'exec /usr/bin/wc "$@"\n'
    )
    wc.chmod(wc.stat().st_mode | stat.S_IXUSR)
    if bounded_result_fault in {"chmod", "write"} or workspace_setup_fault == "chmod":
        chmod = commands / "chmod"
        chmod.write_text(
            "#!/usr/bin/bash\n"
            'if [ "$AGENT_CHECK_WORKSPACE_SETUP_FAULT" = chmod ] '
            '&& [ "$1" = 700 ] '
            '&& [ "$2" = "$(< "$AGENT_CHECK_WORKSPACE_RECORD")" ]; then\n'
            "    printf '%s\\n' 'injected workspace chmod failure' >&2\n"
            "    exit 76\n"
            "fi\n"
            'if [ "$1" = 600 ] && [[ "$2" == */bounded-result.* ]]; then\n'
            '    if [ "$AGENT_CHECK_BOUNDED_RESULT_FAULT" = chmod ]; then\n'
            "        printf '%s\\n' 'bounded result chmod failed' >&2\n"
            "        exit 72\n"
            "    fi\n"
            '    /usr/bin/chmod "$@" || exit $?\n'
            '    /usr/bin/rm -f -- "$2" || exit $?\n'
            '    /usr/bin/mkdir -- "$2" || exit $?\n'
            "    printf '%s\\n' 'bounded result write path replaced' >&2\n"
            "    exit 0\n"
            "fi\n"
            'exec /usr/bin/chmod "$@"\n'
        )
        chmod.chmod(chmod.stat().st_mode | stat.S_IXUSR)

    jq = commands / "jq"
    jq.write_text(
        "#!/usr/bin/bash\n"
        'if [[ "$*" == *\'select(length == 1 and (.[0] | type == "object"))\'* ]]; then\n'
        '    printf "%s\\n" "$*" >> "$AGENT_CHECK_EVIDENCE_PARSER_CALLS"\n'
        "fi\n"
        'if [[ "$*" == *\'field=source_timestamp expected_date\'* ]]; then\n'
        '    printf jq >> "$AGENT_CHECK_COMPARATOR_CALLS"\n'
        '    printf " %q" "$@" >> "$AGENT_CHECK_COMPARATOR_CALLS"\n'
        '    printf "\\n" >> "$AGENT_CHECK_COMPARATOR_CALLS"\n'
        '    if [ "$AGENT_CHECK_COMPARATOR_FAILURE_ONCE" = 1 ] '
        '&& [ ! -e "$AGENT_CHECK_COMPARATOR_FAILURE_MARKER" ]; then\n'
        '        printf x > "$AGENT_CHECK_COMPARATOR_FAILURE_MARKER"\n'
        "        printf '%s\\n' 'injected evidence comparator failure' >&2\n"
        "        exit 74\n"
        "    fi\n"
        "fi\n"
        '"$REAL_JQ" "$@"\n'
        "status=$?\n"
        'if [ -n "$AGENT_CHECK_PARSER_STDERR" ] '
        '&& [[ "$*" == *\'[select(.type == "message_end"\'* ]]; then\n'
        '    printf "%s\\n" "$AGENT_CHECK_PARSER_STDERR" >&2\n'
        "fi\n"
        'if [ -n "$AGENT_CHECK_FENCED_PARSER_STDERR" ] '
        '&& [[ "$*" == *\'capture("^[[:space:]]*```json\'* ]]; then\n'
        '    printf "%s\\n" "$AGENT_CHECK_FENCED_PARSER_STDERR" >&2\n'
        "fi\n"
        'if [ -n "$AGENT_CHECK_SOURCE_PARSER_STDERR" ] '
        '&& [[ "${!#}" == *source-stdout.* ]]; then\n'
        '    printf "%s\\n" "$AGENT_CHECK_SOURCE_PARSER_STDERR" >&2\n'
        "fi\n"
        'exit "$status"\n'
    )
    jq.chmod(jq.stat().st_mode | stat.S_IXUSR)

    uv = commands / "uv"
    uv.write_text(
        "#!/usr/bin/bash\n"
        'printf "uv %s\\n" "$*" >> "$AGENT_CHECK_UV_CALLS"\n'
        'if [ "$#" -eq 2 ] && [ "$1" = cache ] && [ "$2" = dir ]; then\n'
        '    printf "%s\\n" "$AGENT_CHECK_UV_CACHE_RESULT"\n'
        "    exit 0\n"
        "fi\n"
        'if [ "$1" = run ] && [ "$2" = "$AGENT_CHECK_REPO_DIR/llmenv.py" ] '
        '&& [ "$3" = classify-transcript ]; then\n'
        '    exec "$REAL_UV" "$@"\n'
        "fi\n"
        'count="$(< "$AGENT_CHECK_BOUNDED_COUNTER")"\n'
        'printf "%s\\n" "$((count + 1))" > "$AGENT_CHECK_BOUNDED_COUNTER"\n'
        'printf "uv UV_CACHE_DIR=%s %s\\n" "${UV_CACHE_DIR:-}" "$*" '
        '>> "$AGENT_CHECK_BOUNDED_CALLS"\n'
        'if [ -z "${UV_CACHE_DIR:-}" ] '
        '|| [ "$UV_CACHE_DIR" != "$AGENT_CHECK_UV_CACHE_RESULT" ]; then\n'
        "    printf '%s\\n' 'bounded runner used an unstable uv cache' >&2\n"
        "    exit 95\n"
        "fi\n"
        'if [ "$#" -lt 10 ] '
        '|| [ "$1" != run ] '
        '|| [ "$2" != --offline ] '
        '|| [ "$3" != "$AGENT_CHECK_REPO_DIR/llmenv.py" ] '
        '|| [ "$4" != run-agent-bounded ] '
        '|| [ "$5" != --transcript ] '
        '|| [ "$7" != --stderr ] '
        '|| [ "$9" != -- ]; then\n'
        "    printf '%s\\n' 'invalid bounded CLI shape' >&2\n"
        "    exit 97\n"
        "fi\n"
        'transcript_file="$6"\n'
        'stderr_file="$8"\n'
        "shift 9\n"
        'if [ "$AGENT_CHECK_OFFLINE_BOOTSTRAP_FAILURE" = 1 ]; then\n'
        "    printf '%s\\n' 'offline cache bootstrap failed' >&2\n"
        "    exit 75\n"
        "fi\n"
        'if IFS= read -r _; then\n'
        "    printf '%s\\n' 'bounded runner inherited readable stdin' >&2\n"
        "    exit 96\n"
        "fi\n"
        'case "$AGENT_CHECK_CONFIG_AFTER_STARTUP" in\n'
        '    remove) /usr/bin/rm -f -- "$LLM_ENV_CONFIG" || exit $? ;;\n'
        '    rotate) printf "%s\\n" "server:" "  port: 8000" '
        '"  api_key: rotated-fixture-key" > "$LLM_ENV_CONFIG" || exit $? ;;\n'
        "esac\n"
        '"$@" </dev/null >"$transcript_file" 2>"$stderr_file"\n'
        "client_status=$?\n"
        'transcript_bytes="$(/usr/bin/wc -c <"$transcript_file")"\n'
        'stderr_bytes="$(/usr/bin/wc -c <"$stderr_file")"\n'
        'reported_transcript_bytes="$transcript_bytes"\n'
        'reported_stderr_bytes="$stderr_bytes"\n'
        'case "$AGENT_CHECK_BOUNDED_COUNTER_MISMATCH" in\n'
        '    transcript) reported_transcript_bytes="$((transcript_bytes + 1))" ;;\n'
        '    stderr) reported_stderr_bytes="$((stderr_bytes + 1))" ;;\n'
        "esac\n"
        'result_file="$AGENT_CHECK_BOUNDED_RESULTS_DIR/$count"\n'
        'emitted_result_file="$AGENT_CHECK_BOUNDED_EMITTED_RESULTS_DIR/$count"\n'
        'if [ -f "$result_file" ]; then\n'
        '    if [ "$AGENT_CHECK_RAW_BOUNDED_RESULTS" = 1 ]; then\n'
        '        /usr/bin/cp -- "$result_file" "$emitted_result_file" || exit $?\n'
        "    else\n"
        '        "$REAL_JQ" -c '
        '--argjson transcript_bytes "$reported_transcript_bytes" '
        '--argjson stderr_bytes "$reported_stderr_bytes" '
        "'if .transcript_bytes == 0 then .transcript_bytes = $transcript_bytes else . end "
        "| if .stderr_bytes == 0 then .stderr_bytes = $stderr_bytes else . end' "
        '"$result_file" > "$emitted_result_file" || exit $?\n'
        "    fi\n"
        "else\n"
        '    "$REAL_JQ" -cn '
        '--argjson exit_status "$client_status" '
        '--argjson transcript_bytes "$reported_transcript_bytes" '
        '--argjson stderr_bytes "$reported_stderr_bytes" '
        "'{schema:1,outcome:\"completed\",exit_status:$exit_status,"
        "transcript_bytes:$transcript_bytes,stderr_bytes:$stderr_bytes,"
        "cleanup_proved:true}' > \"$emitted_result_file\" || exit $?\n"
        "fi\n"
        '/usr/bin/cat "$emitted_result_file"\n'
        'if [ "$AGENT_CHECK_BOUNDED_RESULT_FAULT" = read ]; then\n'
        '    bounded_output="$(/usr/bin/readlink "/proc/$$/fd/1")"\n'
        '    /usr/bin/rm -f -- "$bounded_output" || exit $?\n'
        '    /usr/bin/mkdir -- "$bounded_output" || exit $?\n'
        "    printf '%s\\n' 'bounded result read path replaced' >&2\n"
        "fi\n"
        'if [ -n "$AGENT_CHECK_BOUNDED_CLI_STDERR" ]; then\n'
        '    printf "%s\\n" "$AGENT_CHECK_BOUNDED_CLI_STDERR" >&2\n'
        "fi\n"
        'status_file="$AGENT_CHECK_BOUNDED_STATUSES_DIR/$count"\n'
        'if [ -f "$status_file" ]; then\n'
        '    exit "$(<"$status_file")"\n'
        "fi\n"
        "exit 0\n"
    )
    uv.chmod(uv.stat().st_mode | stat.S_IXUSR)

    curl = commands / "curl"
    curl.write_text(
        "#!/usr/bin/bash\n"
        "printf 'curl %s\\n' \"$*\" >> \"$CALLS\"\n"
        "max_filesize=\n"
        "max_filesize_count=0\n"
        "for ((index = 1; index <= $#; index++)); do\n"
        "  if [ \"${!index}\" = --max-filesize ]; then\n"
        "    max_filesize_count=$((max_filesize_count + 1))\n"
        "    value_index=$((index + 1))\n"
        "    max_filesize=\"${!value_index:-}\"\n"
        "  fi\n"
        "done\n"
        "url=\"${!#}\"\n"
        "if [ \"$max_filesize_count\" -ne 1 ] || [ \"$max_filesize\" != 1048576 ]; then\n"
        "  printf '%s\\n' 'harness curl omitted exact --max-filesize 1048576' >&2\n"
        "  exit 98\n"
        "fi\n"
        "selected_file=\n"
        "case \"$url\" in\n"
        "  */v1/models)\n"
        "    if [ \"$AGENT_CHECK_OVERSIZED_SOURCE\" = models ]; then\n"
        "      selected_file=\"$AGENT_CHECK_OVERSIZED_SOURCE_FILE\"\n"
        "    elif [ \"$AGENT_CHECK_SOURCE_AT_LIMIT\" = models ]; then\n"
        "      selected_file=\"$AGENT_CHECK_SOURCE_LIMIT_FILE\"\n"
        "    else\n"
        "      printf '%s\\n' \"$AGENT_CHECK_MODELS\"\n"
        "      exit 0\n"
        "    fi\n"
        "    ;;\n"
        "  https://api.open-meteo.com/*)\n"
        "    count=$(< \"$AGENT_CHECK_SOURCE_COUNTER\")\n"
        "    count=$((count + 1))\n"
        "    printf '%s\\n' \"$count\" > \"$AGENT_CHECK_SOURCE_COUNTER\"\n"
        "    if [ \"$AGENT_CHECK_OVERSIZED_SOURCE\" = weather ]; then\n"
        "      selected_file=\"$AGENT_CHECK_OVERSIZED_SOURCE_FILE\"\n"
        "    elif [ -n \"$AGENT_CHECK_WEATHER_BODY\" ]; then\n"
        "      printf '%s\\n' \"$AGENT_CHECK_WEATHER_BODY\"\n"
        "      exit 0\n"
        "    else\n"
        "      printf -v seconds '%02d' \"$count\"\n"
        "      jq -cn --arg seconds \"$seconds\" '{current:{time:(\"2026-07-27T13:00:\" + $seconds),temperature_2m:16.3,weather_code:3}}'\n"
        "      exit 0\n"
        "    fi\n"
        "    ;;\n"
        "  https://open.er-api.com/*)\n"
        "    count=$(< \"$AGENT_CHECK_SOURCE_COUNTER\")\n"
        "    count=$((count + 1))\n"
        "    printf '%s\\n' \"$count\" > \"$AGENT_CHECK_SOURCE_COUNTER\"\n"
        "    if [ \"$AGENT_CHECK_OVERSIZED_SOURCE\" = fx ]; then\n"
        "      selected_file=\"$AGENT_CHECK_OVERSIZED_SOURCE_FILE\"\n"
        "    elif [ -n \"$AGENT_CHECK_FX_BODY\" ]; then\n"
        "      printf '%s\\n' \"$AGENT_CHECK_FX_BODY\"\n"
        "      exit 0\n"
        "    else\n"
        "      printf -v seconds '%02d' \"$count\"\n"
        "      jq -cn --arg seconds \"$seconds\" '{time_last_update_utc:(\"2026-07-27T00:00:\" + $seconds),rates:{CLP:946.527902}}'\n"
        "      exit 0\n"
        "    fi\n"
        "    ;;\n"
        "  *) exit 64 ;;\n"
        "esac\n"
        "selected_bytes=$(/usr/bin/wc -c < \"$selected_file\")\n"
        "if [ -n \"$max_filesize\" ] && [ \"$selected_bytes\" -gt \"$max_filesize\" ]; then\n"
        "  /usr/bin/head -c \"$max_filesize\" \"$selected_file\"\n"
        "  printf '%s\\n' 'curl: (63) Authorization: Bearer test-key-not-a-secret maximum file size exceeded' >&2\n"
        "  exit 63\n"
        "fi\n"
        "/usr/bin/cat \"$selected_file\"\n"
    )
    curl.chmod(curl.stat().st_mode | stat.S_IXUSR)

    for name, body in clients.items():
        command = commands / name
        command.write_text(body)
        command.chmod(command.stat().st_mode | stat.S_IXUSR)

    environment = os.environ | {
        "AGENT_CHECK_MODELS": json.dumps({"data": [{"id": alias} for alias in aliases]}),
        "AGENT_CHECK_WEATHER_BODY": weather_body
        if weather_body is not None
        else "",
        "AGENT_CHECK_FX_BODY": fx_body
        if fx_body is not None
        else "",
        "AGENT_CHECK_RESPONSE_PREFIX_FILE": str(response_prefix_file),
        "AGENT_CHECK_RESPONSE_SUFFIX": agent_response_suffix,
        "AGENT_CHECK_SOURCE_TIMESTAMP": agent_source_timestamp or "",
        "AGENT_CHECK_WEATHER_EVIDENCE_OVERRIDE": agent_weather_evidence_override
        or "{}",
        "AGENT_CHECK_FX_EVIDENCE_OVERRIDE": agent_fx_evidence_override or "{}",
        "AGENT_CHECK_CLIENT_STDERR": agent_client_stderr,
        "AGENT_CHECK_CLIENT_STDERR_FILE": str(agent_client_stderr_file or ""),
        "AGENT_CHECK_PARSER_STDERR": agent_parser_stderr,
        "AGENT_CHECK_FENCED_PARSER_STDERR": fenced_parser_stderr,
        "AGENT_CHECK_SOURCE_PARSER_STDERR": source_parser_stderr,
        "AGENT_CHECK_BOUNDED_COUNTER": str(bounded_counter),
        "AGENT_CHECK_BOUNDED_CALLS": str(bounded_calls),
        "AGENT_CHECK_BOUNDED_RESULTS_DIR": str(bounded_results_dir),
        "AGENT_CHECK_BOUNDED_EMITTED_RESULTS_DIR": str(
            bounded_emitted_results_dir
        ),
        "AGENT_CHECK_BOUNDED_STATUSES_DIR": str(bounded_statuses_dir),
        "AGENT_CHECK_BOUNDED_CLI_STDERR": bounded_cli_stderr,
        "AGENT_CHECK_BOUNDED_RESULT_FAULT": bounded_result_fault,
        "AGENT_CHECK_RAW_BOUNDED_RESULTS": "1" if raw_bounded_results else "0",
        "AGENT_CHECK_BOUNDED_COUNTER_MISMATCH": bounded_counter_mismatch,
        "AGENT_CHECK_BOUNDED_WC_OVERRIDE": bounded_wc_override,
        "AGENT_CHECK_FINAL_SIZE_FAULT": final_size_fault,
        "AGENT_CHECK_CONFIG_AFTER_STARTUP": config_after_startup,
        "AGENT_CHECK_FINALIZER_FAILURE": "1" if finalizer_failure else "0",
        "AGENT_CHECK_WORKSPACE_RECORD": str(workspace_record),
        "AGENT_CHECK_WORKSPACE_SETUP_FAULT": workspace_setup_fault,
        "AGENT_CHECK_EVIDENCE_PARSER_CALLS": str(evidence_parser_calls),
        "AGENT_CHECK_COMPARATOR_CALLS": str(comparator_calls),
        "AGENT_CHECK_COMPARATOR_FAILURE_MARKER": str(comparator_failure_marker),
        "AGENT_CHECK_COMPARATOR_FAILURE_ONCE": "1" if comparator_failure_once else "0",
        "AGENT_CHECK_UV_CALLS": str(uv_calls),
        "AGENT_CHECK_UV_CACHE_RESULT": resolved_uv_cache,
        "AGENT_CHECK_OFFLINE_BOOTSTRAP_FAILURE": "1"
        if offline_bootstrap_failure
        else "0",
        "AGENT_CHECK_OVERSIZED_SOURCE": oversized_source,
        "AGENT_CHECK_OVERSIZED_SOURCE_FILE": str(oversized_source_file),
        "AGENT_CHECK_SOURCE_AT_LIMIT": source_at_limit,
        "AGENT_CHECK_SOURCE_LIMIT_FILE": str(source_limit_file),
        "AGENT_CHECK_OVERSIZED_FINAL_CLIENT": oversized_final_client,
        "AGENT_CHECK_OVERSIZED_FINAL_FILE": str(oversized_final_file),
        "AGENT_CHECK_FINAL_AT_LIMIT_CLIENT": final_at_limit_client,
        "AGENT_CHECK_FINAL_LIMIT_FILE": str(final_limit_file),
        "AGENT_CHECK_REPO_DIR": str(ROOT),
        "ARTIFACTS": str(artifacts),
        "AGENT_CHECK_SOURCE_COUNTER": str(source_counter),
        "CALLS": str(calls),
        "HOME": str(tmp_path / "home"),
        "LLM_ENV_CONFIG": str(config),
        "PATH": str(commands),
        "TMPDIR": str(diagnostic_tmpdir),
        "XDG_CONFIG_HOME": str(tmp_path / "host-xdg-config"),
        "REAL_JQ": real_jq,
        "REAL_UV": shutil.which("uv") or "uv",
    }
    if keep_artifacts:
        environment["LLM_ENV_KEEP_CHECK_ARTIFACTS"] = "1"
    if inherited_redaction_override is not None:
        environment["_LLM_ENV_REDACTION_KEY_OVERRIDE"] = inherited_redaction_override
    if fail_pi_configuration:
        command = commands / "mkdir"
        command.write_text(
            "#!/usr/bin/bash\n"
            'if [[ "${*: -1}" == */pi ]]; then exit 1; fi\n'
            'exec /usr/bin/mkdir "$@"\n'
        )
        command.chmod(command.stat().st_mode | stat.S_IXUSR)
    command = ["/usr/bin/bash"]
    if xtrace:
        command.append("-x")
    command.extend(("scripts/check-with-agents.sh", *arguments))
    result = subprocess.run(
        command,
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    return result, calls, artifacts


def count_rows(output: str, prefix: str) -> int:
    """Count result rows with the requested client prefix."""
    return sum(line.startswith(prefix) for line in output.splitlines())


def bounded_call_count(calls: pathlib.Path) -> int:
    """Return the fake bounded runner's independent invocation count."""
    return int(calls.with_name("bounded-counter").read_text())


def bounded_invocations(calls: pathlib.Path) -> list[str]:
    """Return the fake bounded runner's recorded argument vectors."""
    return calls.with_name("bounded-calls").read_text().splitlines()


def bounded_emitted_results(calls: pathlib.Path) -> list[pathlib.Path]:
    """Return fake bounded-runner results in invocation order."""
    directory = calls.with_name("bounded-emitted-results")
    return sorted(directory.iterdir(), key=lambda path: int(path.name))


def evidence_parser_invocations(calls: pathlib.Path) -> list[str]:
    """Return calls to the exact-one-object evidence validator."""
    return calls.with_name("evidence-parser-calls").read_text().splitlines()


def comparator_invocations(calls: pathlib.Path) -> list[str]:
    """Return calls to the source-evidence difference comparator."""
    return calls.with_name("comparator-calls").read_text().splitlines()


def uv_invocations(calls: pathlib.Path) -> list[str]:
    """Return every fake uv invocation, including cache discovery."""
    return calls.with_name("uv-calls").read_text().splitlines()


def bounded_result_json(**overrides: object) -> str:
    """Build one exact bounded-runner result for fixture queues."""
    result: dict[str, object] = {
        "schema": 1,
        "outcome": "completed",
        "exit_status": 0,
        "transcript_bytes": 0,
        "stderr_bytes": 0,
        "cleanup_proved": True,
    }
    result.update(overrides)
    return json.dumps(result, separators=(",", ":"))


def agent_rows(output: str) -> list[str]:
    """Return the details of each structured agent row."""
    return output.split("Agent:\n")[1:]


def assert_common_agent_fields(
    row: str, *, client: str, exit_status: str
) -> None:
    """Assert the fields required before agent success or failure evidence."""
    credentials = {
        "pi": "Credential: <private>/models.json configures the Pi apiKey",
        "opencode": "Credential: OPENCODE_API_KEY=<redacted> from the environment",
    }

    assert f"client={client} model=gemma4 check=" in row
    assert (
        "Configuration:\n  Provider: llm-env\n  Base URL: http://llm.local:8000/v1\n"
        "  Model: gemma4\n  Tools: client default (untouched -- the check trusts "
        "the client's own tooling)\n  " + credentials[client]
    ) in row
    assert "Command: " in row
    assert "Input:\n  Using your own tools, find the current " in row
    assert f"Exit status:\n  {exit_status}" in row
    assert (
        "Expectation:\n  exactly one JSON object with a plausible, current "
    ) in row


VALID_PI_STUB = """#!/usr/bin/bash
printf 'pi %s\\n' "$*" >> "$CALLS"
if [[ -v _LLM_ENV_REDACTION_KEY_OVERRIDE ]]; then
    printf '%s' "$_LLM_ENV_REDACTION_KEY_OVERRIDE" > "$ARTIFACTS/redaction-override-pi-${BASHPID}"
fi
printf '%s' "${!#}" > "$ARTIFACTS/prompt-pi-${BASHPID}"
if [ -n "${AGENT_CHECK_CLIENT_STDERR_FILE:-}" ]; then
    /usr/bin/cat "$AGENT_CHECK_CLIENT_STDERR_FILE" >&2
elif [ -n "${AGENT_CHECK_CLIENT_STDERR:-}" ]; then
    printf '%s\n' "$AGENT_CHECK_CLIENT_STDERR" >&2
fi
printf '%s\\n' "$(< "$PI_CODING_AGENT_DIR/models.json")" > "$ARTIFACTS/pi-${BASHPID}.json"
count="$(< "$AGENT_CHECK_SOURCE_COUNTER")"
printf -v seconds '%02d' "$count"
if [ "$AGENT_CHECK_OVERSIZED_FINAL_CLIENT" = pi ] && [[ "$*" == *weather* ]]; then
    jq -cn --rawfile response "$AGENT_CHECK_OVERSIZED_FINAL_FILE" '{type:"message_end",message:{role:"assistant",content:[{type:"text",text:$response}]}}'
    exit
fi
if [ "$AGENT_CHECK_FINAL_AT_LIMIT_CLIENT" = pi ] && [[ "$*" == *weather* ]]; then
    jq -cn --rawfile response "$AGENT_CHECK_FINAL_LIMIT_FILE" '{type:"message_end",message:{role:"assistant",content:[{type:"text",text:$response}]}}'
    exit
fi
if [[ "$*" == *weather* ]]; then
    evidence="$(jq -cn --arg seconds "$seconds" --arg source_timestamp "$AGENT_CHECK_SOURCE_TIMESTAMP" --argjson evidence_override "$AGENT_CHECK_WEATHER_EVIDENCE_OVERRIDE" '{source_url:"https://api.open-meteo.com/v1/forecast?latitude=-33.4489&longitude=-70.6693&current=temperature_2m,weather_code&timezone=America%2FSantiago",source_timestamp:(if $source_timestamp == "" then ("2026-07-27T13:00:" + $seconds) else $source_timestamp end),temperature_2m:16.3,weather_code:3} | . + $evidence_override')"
else
    evidence="$(jq -cn --arg seconds "$seconds" --arg source_timestamp "$AGENT_CHECK_SOURCE_TIMESTAMP" --argjson evidence_override "$AGENT_CHECK_FX_EVIDENCE_OVERRIDE" '{source_url:"https://open.er-api.com/v6/latest/USD",source_timestamp:(if $source_timestamp == "" then ("2026-07-27T00:00:" + $seconds) else $source_timestamp end),usd_to_clp:946.527902} | . + $evidence_override')"
fi
jq -cn --rawfile response_prefix "$AGENT_CHECK_RESPONSE_PREFIX_FILE" --argjson evidence "$evidence" '{type:"message_end",message:{role:"assistant",content:[{type:"text",text:($response_prefix + ($evidence | tojson) + env.AGENT_CHECK_RESPONSE_SUFFIX)}]}}'
"""

VALID_OPENCODE_STUB = """#!/usr/bin/bash
printf 'opencode %s xdg=%s\\n' "$*" "${XDG_CONFIG_HOME:-}" >> "$CALLS"
if [[ -v _LLM_ENV_REDACTION_KEY_OVERRIDE ]]; then
    printf '%s' "$_LLM_ENV_REDACTION_KEY_OVERRIDE" > "$ARTIFACTS/redaction-override-opencode-${BASHPID}"
fi
printf '%s' "${!#}" > "$ARTIFACTS/prompt-opencode-${BASHPID}"
printf '%s\\n' "$(< "$OPENCODE_CONFIG")" > "$ARTIFACTS/opencode-${BASHPID}.jsonc"
if [ -n "${AGENT_CHECK_CLIENT_STDERR_FILE:-}" ]; then
    /usr/bin/cat "$AGENT_CHECK_CLIENT_STDERR_FILE" >&2
elif [ -n "${AGENT_CHECK_CLIENT_STDERR:-}" ]; then
    printf '%s\n' "$AGENT_CHECK_CLIENT_STDERR" >&2
fi
count="$(< "$AGENT_CHECK_SOURCE_COUNTER")"
printf -v seconds '%02d' "$count"
if [ "$AGENT_CHECK_OVERSIZED_FINAL_CLIENT" = opencode ] && [[ "$*" == *weather* ]]; then
    jq -cn --rawfile response "$AGENT_CHECK_OVERSIZED_FINAL_FILE" '{type:"text",part:{type:"text",text:$response}}'
    exit
fi
if [ "$AGENT_CHECK_FINAL_AT_LIMIT_CLIENT" = opencode ] && [[ "$*" == *weather* ]]; then
    jq -cn --rawfile response "$AGENT_CHECK_FINAL_LIMIT_FILE" '{type:"text",part:{type:"text",text:$response}}'
    exit
fi
if [[ "$*" == *weather* ]]; then
    evidence="$(jq -cn --arg seconds "$seconds" --arg source_timestamp "$AGENT_CHECK_SOURCE_TIMESTAMP" --argjson evidence_override "$AGENT_CHECK_WEATHER_EVIDENCE_OVERRIDE" '{source_url:"https://api.open-meteo.com/v1/forecast?latitude=-33.4489&longitude=-70.6693&current=temperature_2m,weather_code&timezone=America%2FSantiago",source_timestamp:(if $source_timestamp == "" then ("2026-07-27T13:00:" + $seconds) else $source_timestamp end),temperature_2m:16.3,weather_code:3} | . + $evidence_override')"
else
    evidence="$(jq -cn --arg seconds "$seconds" --arg source_timestamp "$AGENT_CHECK_SOURCE_TIMESTAMP" --argjson evidence_override "$AGENT_CHECK_FX_EVIDENCE_OVERRIDE" '{source_url:"https://open.er-api.com/v6/latest/USD",source_timestamp:(if $source_timestamp == "" then ("2026-07-27T00:00:" + $seconds) else $source_timestamp end),usd_to_clp:946.527902} | . + $evidence_override')"
fi
jq -cn --rawfile response_prefix "$AGENT_CHECK_RESPONSE_PREFIX_FILE" --argjson evidence "$evidence" '{type:"text",part:{type:"text",text:($response_prefix + ($evidence | tojson) + env.AGENT_CHECK_RESPONSE_SUFFIX)}}'
"""

STDIN_CONSUMING_PI_STUB = VALID_PI_STUB.replace(
    "#!/usr/bin/bash\n", "#!/usr/bin/bash\nIFS= read -r _ || true\n", 1
)
STDIN_CONSUMING_OPENCODE_STUB = VALID_OPENCODE_STUB.replace(
    "#!/usr/bin/bash\n", "#!/usr/bin/bash\nIFS= read -r _ || true\n", 1
)

FENCED_JSON_PI_STUB = """#!/usr/bin/bash
printf 'pi %s\\n' "$*" >> "$CALLS"
count="$(< "$AGENT_CHECK_SOURCE_COUNTER")"
printf -v seconds '%02d' "$count"
if [[ "$*" == *weather* ]]; then
    evidence="$(jq -cn --arg seconds "$seconds" '{source_url:"https://api.open-meteo.com/v1/forecast?latitude=-33.4489&longitude=-70.6693&current=temperature_2m,weather_code&timezone=America%2FSantiago",source_timestamp:("2026-07-27T13:00:" + $seconds),temperature_2m:16.3,weather_code:3}')"
else
    evidence="$(jq -cn --arg seconds "$seconds" '{source_url:"https://open.er-api.com/v6/latest/USD",source_timestamp:("2026-07-27T00:00:" + $seconds),usd_to_clp:946.527902}')"
fi
jq -cn --argjson evidence "$evidence" '{type:"message_end",message:{role:"assistant",content:[{type:"text",text:("```json\\n" + ($evidence | tojson) + "\\n```")}]}}'
"""

MALFORMED_FENCED_JSON_PI_STUB = """#!/usr/bin/bash
printf 'pi %s\\n' "$*" >> "$CALLS"
jq -cn --arg body '```json
{not valid json
```' '{type:"message_end",message:{role:"assistant",content:[{type:"text",text:$body}]}}'
"""

FAILING_PI_STUB = """#!/usr/bin/bash
printf 'pi %s\\n' "$*" >> "$CALLS"
printf 'Authorization: Bearer test-key-not-a-secret\\nagent transport failed\\n' >&2
exit 17
"""

INVALID_FINAL_PI_STUB = """#!/usr/bin/bash
printf 'pi %s\\n' "$*" >> "$CALLS"
printf '%s\\n' '{"type":"message_end","message":{"role":"assistant","content":[{"type":"text","text":"not JSON"}]}}'
"""

STALE_PI_STUB = """#!/usr/bin/bash
printf 'pi %s\\n' "$*" >> "$CALLS"
printf '%s\\n' "$(< "$PI_CODING_AGENT_DIR/models.json")" > "$ARTIFACTS/pi-${BASHPID}.json"
count="$(< "$AGENT_CHECK_SOURCE_COUNTER")"
printf -v seconds '%02d' "$count"
if [[ "$*" == *weather* ]]; then
    evidence="$(jq -cn --arg seconds "$seconds" '{source_url:"https://api.open-meteo.com/v1/forecast?latitude=-33.4489&longitude=-70.6693&current=temperature_2m,weather_code&timezone=America%2FSantiago",source_timestamp:"stale",temperature_2m:16.3,weather_code:3}')"
else
    evidence="$(jq -cn --arg seconds "$seconds" '{source_url:"https://open.er-api.com/v6/latest/USD",source_timestamp:("2026-07-27T00:00:" + $seconds),usd_to_clp:946.527902}')"
fi
jq -cn --argjson evidence "$evidence" '{type:"message_end",message:{role:"assistant",content:[{type:"text",text:($evidence | tojson)}]}}'
"""


def test_make_help_lists_check_with_agents() -> None:
    assert "make check-with-agents" in (SCRIPT_DIR / "help.sh").read_text()


def test_make_help_lists_prune() -> None:
    assert "make prune" in (SCRIPT_DIR / "help.sh").read_text()


def test_agent_check_fails_when_no_supported_client_is_installed(
    tmp_path: pathlib.Path,
) -> None:
    """The opt-in check must reject a PATH without Pi or OpenCode."""
    result, _, _ = run_agent_check(tmp_path, clients={})

    assert result.returncode != 0
    assert "fail no supported agent is installed" in result.stderr
    assert result.stdout.count("SKIP client=") == 2


def test_agent_check_fetches_models_before_no_client_failure(
    tmp_path: pathlib.Path,
) -> None:
    """The no-client gate needs aliases but must not fetch unused public data."""
    result, calls, _ = run_agent_check(tmp_path, clients={})

    assert result.returncode != 0
    recorded = calls.read_text()
    assert "http://127.0.0.1:8000/v1/models" in recorded
    assert "https://api.open-meteo.com/" not in recorded
    assert "https://open.er-api.com/" not in recorded


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


def test_agent_check_input_bounds_reject_oversized_model_discovery_before_matrix(
    tmp_path: pathlib.Path,
) -> None:
    result, calls, _ = run_agent_check(
        tmp_path,
        clients={"pi": VALID_PI_STUB},
        oversized_source="models",
    )
    combined = result.stdout + result.stderr

    oversized_file = calls.with_name("fixture-bodies") / "oversized-source"
    assert stat.S_IMODE(oversized_file.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(oversized_file.stat().st_mode) == 0o600
    assert oversized_file.stat().st_size == 1_048_577
    assert result.returncode != 0
    assert "could not fetch model aliases" in result.stderr
    assert bounded_call_count(calls) == 0
    assert "Agent:" not in result.stdout
    assert "pi " not in calls.read_text()
    assert "https://api.open-meteo.com/" not in calls.read_text()
    assert "https://open.er-api.com/" not in calls.read_text()
    assert "oversized-source-sentinel" not in combined


@pytest.mark.parametrize(
    ("source", "failed_field", "continued_field"),
    (
        ("weather", "weather_code", "usd_to_clp"),
        ("fx", "usd_to_clp", "weather_code"),
    ),
)
def test_agent_check_input_bounds_reject_oversized_public_source_without_client(
    tmp_path: pathlib.Path,
    source: str,
    failed_field: str,
    continued_field: str,
) -> None:
    result, calls, _ = run_agent_check(
        tmp_path,
        clients={"pi": VALID_PI_STUB},
        oversized_source=source,
    )
    combined = result.stdout + result.stderr

    oversized_file = calls.with_name("fixture-bodies") / "oversized-source"
    assert stat.S_IMODE(oversized_file.stat().st_mode) == 0o600
    assert oversized_file.stat().st_size == 1_048_577
    assert result.returncode != 0
    assert f"stage=source fetch reason={source} source exited 63" in result.stderr
    assert "Source stdout:" in result.stdout
    assert (
        "Source stderr:\n  curl: (63) Authorization: Bearer test-key-not-a-secret "
        "maximum file size exceeded"
    ) in result.stdout
    assert "oversized-source-sentinel" not in combined
    assert len(combined.encode()) < 400_000
    client_calls = [
        line for line in calls.read_text().splitlines() if line.startswith("pi ")
    ]
    assert len(client_calls) == 1
    assert failed_field not in client_calls[0]
    assert continued_field in client_calls[0]
    assert bounded_call_count(calls) == 1
    assert "Results: 1 passed, 1 failed" in result.stdout


@pytest.mark.parametrize(
    ("client", "stub"),
    (("pi", VALID_PI_STUB), ("opencode", VALID_OPENCODE_STUB)),
)
def test_agent_check_input_bounds_gate_oversized_final_before_parser_and_continue(
    tmp_path: pathlib.Path,
    client: str,
    stub: str,
) -> None:
    result, calls, _ = run_agent_check(
        tmp_path,
        clients={client: stub},
        oversized_final_client=client,
        keep_artifacts=True,
    )
    combined = result.stdout + result.stderr

    oversized_file = calls.with_name("fixture-bodies") / "oversized-final"
    assert stat.S_IMODE(oversized_file.stat().st_mode) == 0o600
    assert oversized_file.stat().st_size == 1_048_577
    assert result.returncode != 0
    assert (
        f"Verdict: FAIL stage=final response limit client={client} model=gemma4 "
        "check=weather reason=final assistant text exceeded 1048576 bytes"
    ) in result.stderr
    assert (
        f"FAIL client={client} model=gemma4 check=weather "
        "reason=final-response-limit"
    ) in result.stdout
    assert "stage=agent evidence parsing" not in result.stderr
    assert len(evidence_parser_invocations(calls)) == 1
    assert bounded_call_count(calls) == 2
    assert (
        f"PASS client={client} model=gemma4 check=fx reason=agent-returned-json"
    ) in result.stdout
    assert "Results: 1 passed, 1 failed" in result.stdout
    weather_row = next(row for row in agent_rows(result.stdout) if "check=weather" in row)
    displayed_final = weather_row.split("Final response:\n  ", 1)[1].split(
        f"\nFAIL client={client}", 1
    )[0]
    assert len(displayed_final.encode()) == 262_144
    assert "oversized-final-sentinel" not in combined
    assert len(combined.encode()) < 800_000
    retained_line = next(
        line for line in result.stdout.splitlines() if line.startswith("Diagnostics retained: ")
    )
    retained_dir = pathlib.Path(retained_line.removeprefix("Diagnostics retained: "))
    final_sizes = [path.stat().st_size for path in retained_dir.glob("assistant-final.*")]
    assert 1_048_577 in final_sizes


def test_agent_check_input_bounds_accept_source_response_at_exact_limit(
    tmp_path: pathlib.Path,
) -> None:
    result, calls, _ = run_agent_check(
        tmp_path,
        clients={},
        source_at_limit="models",
    )

    source_file = calls.with_name("fixture-bodies") / "source-at-limit"
    assert source_file.stat().st_size == 1_048_576
    assert stat.S_IMODE(source_file.stat().st_mode) == 0o600
    assert result.returncode != 0
    assert "could not fetch model aliases" not in result.stderr
    assert "fail no supported agent is installed" in result.stderr


def test_agent_check_input_bounds_accept_final_response_at_exact_limit(
    tmp_path: pathlib.Path,
) -> None:
    result, calls, _ = run_agent_check(
        tmp_path,
        clients={"pi": VALID_PI_STUB},
        final_at_limit_client="pi",
        keep_artifacts=True,
    )

    assert result.returncode == 0, result.stderr
    assert count_rows(result.stdout, "PASS client=pi") == 2
    assert len(evidence_parser_invocations(calls)) == 2
    retained_line = next(
        line for line in result.stdout.splitlines() if line.startswith("Diagnostics retained: ")
    )
    retained_dir = pathlib.Path(retained_line.removeprefix("Diagnostics retained: "))
    final_sizes = [path.stat().st_size for path in retained_dir.glob("assistant-final.*")]
    assert 1_048_576 in final_sizes
    fixture_file = calls.with_name("fixture-bodies") / "final-at-limit"
    assert fixture_file.stat().st_size == 1_048_576


@pytest.mark.parametrize(
    ("fault", "measurement_diagnostic"),
    (
        ("failure", "injected final size measurement failure"),
        ("empty", None),
        ("malformed", None),
        ("negative", None),
        ("noncanonical", None),
    ),
)
def test_agent_check_aborts_matrix_on_final_size_measurement_fault(
    tmp_path: pathlib.Path,
    fault: str,
    measurement_diagnostic: str | None,
) -> None:
    result, calls, _ = run_agent_check(
        tmp_path,
        clients={"pi": VALID_PI_STUB, "opencode": VALID_OPENCODE_STUB},
        model_aliases=("gemma4", "ornith"),
        final_size_fault=fault,
        agent_parser_stderr="response parser warning",
    )
    combined = result.stdout + result.stderr
    rows = agent_rows(result.stdout)

    assert result.returncode != 0
    assert bounded_call_count(calls) == 1
    assert evidence_parser_invocations(calls) == []
    assert len(rows) == 1
    assert_common_agent_fields(rows[0], client="pi", exit_status="0")
    assert "Agent parser stderr:\n  response parser warning" in rows[0]
    if measurement_diagnostic is not None:
        assert f"  {measurement_diagnostic}" in rows[0]
    assert result.stderr.count("Verdict: FAIL stage=resource boundary") == 1
    assert count_rows(result.stdout, "FAIL client=") == 1
    assert "FAIL client=pi model=gemma4 check=weather reason=boundary-failure" in result.stdout
    assert "client=pi model=gemma4 check=fx" not in result.stdout
    assert "model=ornith" not in result.stdout
    assert "client=opencode" not in result.stdout
    assert "Results: 0 passed, 1 failed" in result.stdout
    assert "integer expression expected" not in combined


def test_agent_check_input_bounds_cap_every_harness_owned_source_curl(
    tmp_path: pathlib.Path,
) -> None:
    result, calls, _ = run_agent_check(tmp_path, clients={"pi": VALID_PI_STUB})

    assert result.returncode == 0, result.stderr
    curl_calls = [
        line.removeprefix("curl ")
        for line in calls.read_text().splitlines()
        if line.startswith("curl ")
    ]
    assert len(curl_calls) == 3
    for call in curl_calls:
        assert call.split().count("--max-filesize") == 1
        assert "--max-filesize 1048576" in call
    assert result.stdout.count(
        "Command: curl --fail --silent --show-error --max-time 20 "
        "--max-filesize 1048576 https://"
    ) == 2


def test_agent_check_integration_fails_closed_when_comparator_errors(
    tmp_path: pathlib.Path,
) -> None:
    result, calls, _ = run_agent_check(
        tmp_path,
        clients={"pi": VALID_PI_STUB},
        comparator_failure_once=True,
    )

    assert result.returncode != 0
    assert bounded_call_count(calls) == 2
    assert (
        "Verdict: FAIL stage=source-evidence comparison client=pi model=gemma4 "
        "check=weather reason=validated evidence comparison failed"
    ) in result.stderr
    assert (
        "FAIL client=pi model=gemma4 check=weather "
        "reason=evidence-comparison-failure"
    ) in result.stdout
    assert "PASS client=pi model=gemma4 check=weather" not in result.stdout
    assert "PASS client=pi model=gemma4 check=fx reason=agent-returned-json" in result.stdout
    weather_row = next(row for row in agent_rows(result.stdout) if "check=weather" in row)
    assert "Agent parser stderr:\n  injected evidence comparator failure" in weather_row
    assert "Results: 1 passed, 1 failed" in result.stdout


def test_agent_check_integration_comparator_uses_only_private_json_files(
    tmp_path: pathlib.Path,
) -> None:
    result, calls, _ = run_agent_check(
        tmp_path,
        clients={"pi": VALID_PI_STUB},
        keep_artifacts=True,
    )

    assert result.returncode == 0, result.stderr
    invocations = comparator_invocations(calls)
    assert len(invocations) == 2
    for invocation in invocations:
        assert "--slurpfile expected " in invocation
        assert "--slurpfile received " in invocation
        assert "source-snapshot." in invocation
        assert "agent-evidence." in invocation
        assert "--argjson expected" not in invocation
        assert "--argjson received" not in invocation
        assert '"source_url"' not in invocation
    retained_line = next(
        line for line in result.stdout.splitlines() if line.startswith("Diagnostics retained: ")
    )
    retained_dir = pathlib.Path(retained_line.removeprefix("Diagnostics retained: "))
    snapshot_files = list(retained_dir.glob("source-snapshot.*"))
    evidence_files = list(retained_dir.glob("agent-evidence.*"))
    assert len(snapshot_files) == 2
    assert len(evidence_files) == 2
    assert all(
        stat.S_IMODE(path.stat().st_mode) == 0o600
        for path in snapshot_files + evidence_files
    )


@pytest.mark.parametrize(
    ("weather_override", "fx_override", "field", "received"),
    (
        # source_url is only format-checked now (the agent may use any source), and
        # weather_code is only type-checked (providers use different taxonomies) --
        # see source_evidence_differences() in check-with-agents.sh. Only the
        # tolerance-compared numeric fields can still produce a mismatch here.
        ('{"temperature_2m":-990001}', None, "temperature_2m", "-990001"),
        (None, '{"usd_to_clp":-990003}', "usd_to_clp", "-990003"),
    ),
)
def test_agent_check_integration_redacts_every_required_received_value(
    tmp_path: pathlib.Path,
    weather_override: str | None,
    fx_override: str | None,
    field: str,
    received: str,
) -> None:
    result, _, _ = run_agent_check(
        tmp_path,
        clients={"pi": VALID_PI_STUB},
        agent_weather_evidence_override=weather_override,
        agent_fx_evidence_override=fx_override,
    )

    stdout_rows = [
        line
        for line in result.stdout.splitlines()
        if line.startswith("FAIL client=pi") and f"field={field}" in line
    ]
    stderr_rows = [
        line
        for line in result.stderr.splitlines()
        if "stage=source-evidence mismatch" in line and f"field={field}" in line
    ]
    assert result.returncode != 0
    assert len(stdout_rows) == 1
    assert len(stderr_rows) == 1
    assert all('received="<redacted>"' in line for line in stdout_rows + stderr_rows)
    assert all(received not in line for line in stdout_rows + stderr_rows)


def test_agent_check_mismatch_summary_never_echoes_the_received_value(
    tmp_path: pathlib.Path,
) -> None:
    """The structured mismatch line must redact the received value (raw diagnostic
    dumps below it, like the transcript excerpt, are shown verbatim by design)."""
    model_controlled_value = "model-controlled-not-a-url"
    result, _, _ = run_agent_check(
        tmp_path,
        clients={"opencode": VALID_OPENCODE_STUB},
        agent_weather_evidence_override=json.dumps({"source_url": model_controlled_value}),
    )
    combined = result.stdout + result.stderr
    mismatch_rows = [
        line
        for line in combined.splitlines()
        if "source-evidence mismatch" in line or line.startswith("FAIL client=opencode")
    ]

    assert result.returncode != 0
    assert any("field=source_url" in line for line in mismatch_rows)
    assert all('received="<redacted>"' in line for line in mismatch_rows)
    assert all(model_controlled_value not in line for line in mismatch_rows)


def test_agent_check_integration_extracts_exact_assistant_text_bytes(
    tmp_path: pathlib.Path,
) -> None:
    script = (SCRIPT_DIR / "check-with-agents.sh").read_text()
    result, calls, _ = run_agent_check(
        tmp_path,
        clients={"pi": VALID_PI_STUB},
        final_at_limit_client="pi",
        keep_artifacts=True,
    )

    assert script.count("jq -rjce '") == 2
    assert "$response[:-1]" not in VALID_PI_STUB
    assert "$response[:-1]" not in VALID_OPENCODE_STUB
    fixture_file = calls.with_name("fixture-bodies") / "final-at-limit"
    assert fixture_file.stat().st_size == 1_048_576
    assert result.returncode == 0, result.stderr
    retained_line = next(
        line for line in result.stdout.splitlines() if line.startswith("Diagnostics retained: ")
    )
    retained_dir = pathlib.Path(retained_line.removeprefix("Diagnostics retained: "))
    assert 1_048_576 in [
        path.stat().st_size for path in retained_dir.glob("assistant-final.*")
    ]


@pytest.mark.parametrize(
    ("client", "stub"),
    (("pi", VALID_PI_STUB), ("opencode", VALID_OPENCODE_STUB)),
)
def test_agent_check_integration_uses_one_stable_offline_uv_cache(
    tmp_path: pathlib.Path,
    client: str,
    stub: str,
) -> None:
    result, calls, _ = run_agent_check(tmp_path, clients={client: stub})

    assert result.returncode == 0, result.stderr
    all_uv_calls = uv_invocations(calls)
    assert all_uv_calls.count("uv cache dir") == 1
    assert len(all_uv_calls) == 3
    assert bounded_call_count(calls) == 2
    assert len(bounded_invocations(calls)) == 2
    expected_prefix = f"uv UV_CACHE_DIR={tmp_path / 'uv-cache'} run --offline {ROOT / 'llmenv.py'}"
    assert all(call.startswith(expected_prefix) for call in bounded_invocations(calls))


def test_agent_check_integration_rejects_an_empty_uv_cache_before_matrix(
    tmp_path: pathlib.Path,
) -> None:
    result, calls, _ = run_agent_check(
        tmp_path,
        clients={"pi": VALID_PI_STUB},
        uv_cache_result="",
    )

    assert result.returncode != 0
    assert "uv cache directory is empty" in result.stderr
    assert bounded_call_count(calls) == 0
    assert "Agent:" not in result.stdout
    assert uv_invocations(calls) == ["uv cache dir"]


def test_agent_check_integration_offline_bootstrap_failure_aborts_matrix(
    tmp_path: pathlib.Path,
) -> None:
    result, calls, _ = run_agent_check(
        tmp_path,
        clients={"pi": VALID_PI_STUB, "opencode": VALID_OPENCODE_STUB},
        model_aliases=("gemma4", "ornith"),
        offline_bootstrap_failure=True,
    )

    assert result.returncode != 0
    assert uv_invocations(calls)[0] == "uv cache dir"
    assert bounded_call_count(calls) == 1
    assert len(agent_rows(result.stdout)) == 1
    assert "Agent parser stderr:\n  offline cache bootstrap failed" in result.stdout
    assert "Verdict: FAIL stage=resource boundary" in result.stderr
    assert "reason=boundary-failure" in result.stdout
    assert "client=pi model=gemma4 check=fx" not in result.stdout
    assert "client=opencode" not in result.stdout


def test_agent_check_integration_preflights_helpers_and_documents_diagnostics() -> None:
    script = (SCRIPT_DIR / "check-with-agents.sh").read_text()
    prerequisite_line = next(
        line for line in script.splitlines() if line.startswith("require_cmd ")
    )
    quick_start = " ".join((ROOT / "QUICK_START.md").read_text().split()).lower()

    assert {"head", "cat", "sed"}.issubset(prerequisite_line.split())
    assert "displayed diagnostics are bounded, redacted excerpts." in quick_start
    assert (
        "explicitly retained private artifacts are redacted before retention."
        in quick_start
    )
    assert (
        "without it, the checks remove raw artifacts after displaying bounded, "
        "redacted excerpts."
        in quick_start
    )
    assert "after printing their contents" not in quick_start


@pytest.mark.parametrize(
    ("weather_body", "fx_body", "expected_error"),
    [
        ("{}", None, "source fetch reason=weather source body is invalid"),
        (None, "{}", "source fetch reason=fx source body is invalid"),
        (
            '{"current":{"time":null,"temperature_2m":16.3,"weather_code":3}}',
            None,
            "source fetch reason=weather source body is invalid",
        ),
        (
            '{"current":{"time":"2026-07-27T13:00","temperature_2m":"16.3","weather_code":3}}',
            None,
            "source fetch reason=weather source body is invalid",
        ),
        (
            '{"current":{"time":"2026-07-27T13:00","temperature_2m":16.3,"weather_code":"3"}}',
            None,
            "source fetch reason=weather source body is invalid",
        ),
        (
            None,
            '{"time_last_update_utc":null,"rates":{"CLP":946.527902}}',
            "source fetch reason=fx source body is invalid",
        ),
        (
            None,
            '{"time_last_update_utc":"Mon, 27 Jul 2026 00:02:31 +0000","rates":{"CLP":"946.527902"}}',
            "source fetch reason=fx source body is invalid",
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
        clients={"pi": VALID_PI_STUB},
        weather_body=weather_body,
        fx_body=fx_body,
    )

    assert result.returncode != 0
    assert expected_error in result.stderr
    assert "Source stdout:" in result.stdout


@pytest.mark.parametrize("source", ("weather", "fx"))
@pytest.mark.parametrize(
    "unsafe_suffix",
    (pytest.param("\x00", id="nul"), pytest.param("\n", id="newline"), pytest.param("\t", id="tab")),
)
def test_agent_check_rejects_unsafe_public_timestamp_before_client_launch(
    tmp_path: pathlib.Path,
    source: str,
    unsafe_suffix: str,
) -> None:
    timestamps = {
        "weather": "2026-07-27T13:00:01",
        "fx": "Mon, 27 Jul 2026 00:02:31 +0000",
    }
    bodies = {
        "weather": json.dumps(
            {
                "current": {
                    "time": timestamps[source] + unsafe_suffix,
                    "temperature_2m": 16.3,
                    "weather_code": 3,
                }
            }
        ),
        "fx": json.dumps(
            {
                "time_last_update_utc": timestamps[source] + unsafe_suffix,
                "rates": {"CLP": 946.527902},
            }
        ),
    }
    result, calls, _ = run_agent_check(
        tmp_path,
        clients={"pi": VALID_PI_STUB},
        weather_body=bodies[source] if source == "weather" else None,
        fx_body=bodies[source] if source == "fx" else None,
    )

    assert result.returncode != 0
    assert f"source fetch reason={source} source body is invalid" in result.stderr
    assert bounded_call_count(calls) == 1
    client_calls = [
        line for line in calls.read_text().splitlines() if line.startswith("pi ")
    ]
    assert len(client_calls) == 1
    # The prompt no longer names a literal source URL (the agent picks its own
    # tooling/source) -- distinguish the surviving check by its distinct field name.
    assert ("usd_to_clp" in client_calls[0]) is (source == "weather")
    assert ("weather_code" in client_calls[0]) is (source == "fx")


@pytest.mark.parametrize("source", ("weather", "fx"))
def test_agent_check_rejects_overlong_public_timestamp_before_client_launch(
    tmp_path: pathlib.Path,
    source: str,
) -> None:
    timestamp = "2026-07-27T13:00:01." + ("1" * 45)
    assert len(timestamp) == 65
    weather_body = json.dumps(
        {
            "current": {
                "time": timestamp,
                "temperature_2m": 16.3,
                "weather_code": 3,
            }
        }
    )
    fx_body = json.dumps(
        {"time_last_update_utc": timestamp, "rates": {"CLP": 946.527902}}
    )
    result, calls, _ = run_agent_check(
        tmp_path,
        clients={"pi": VALID_PI_STUB},
        weather_body=weather_body if source == "weather" else None,
        fx_body=fx_body if source == "fx" else None,
    )

    assert result.returncode != 0
    assert f"source fetch reason={source} source body is invalid" in result.stderr
    assert bounded_call_count(calls) == 1


@pytest.mark.parametrize(
    "weather_body",
    (
        '{"current":{"time":"2026-02-30T13:00","temperature_2m":16.3,"weather_code":3}}',
        '{"current":{"time":"2026/07/27T13:00","temperature_2m":16.3,"weather_code":3}}',
    ),
)
def test_agent_check_rejects_noncanonical_weather_source_dates(
    tmp_path: pathlib.Path, weather_body: str
) -> None:
    result, _, _ = run_agent_check(
        tmp_path,
        clients={"pi": VALID_PI_STUB},
        weather_body=weather_body,
    )

    assert result.returncode != 0
    assert "source fetch reason=weather source body is invalid" in result.stderr


def test_agent_check_accepts_rfc_style_fx_source_timestamp(
    tmp_path: pathlib.Path,
) -> None:
    result, _, _ = run_agent_check(
        tmp_path,
        clients={"pi": VALID_PI_STUB},
        fx_body=(
            '{"time_last_update_utc":"Mon, 27 Jul 2026 00:02:31 +0000",'
            '"rates":{"CLP":946.527902}}'
        ),
    )

    assert result.returncode == 0, result.stderr
    assert count_rows(result.stdout, "PASS client=pi") == 2
    assert '"source_date":"2026-07-27"' in result.stdout


def test_agent_check_rejects_unparseable_typed_fx_source_timestamp(
    tmp_path: pathlib.Path,
) -> None:
    result, _, _ = run_agent_check(
        tmp_path,
        clients={"pi": VALID_PI_STUB},
        fx_body='{"time_last_update_utc":"not-a-date","rates":{"CLP":946.527902}}',
    )

    assert result.returncode != 0
    assert "source fetch reason=fx source body is invalid" in result.stderr
    assert "Source stdout:" in result.stdout
    assert "Source parser stderr:" in result.stdout
    assert "Source parser stderr:\n  (empty)" not in result.stdout


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


def test_agent_check_fetches_snapshot_immediately_before_each_matrix_row(
    tmp_path: pathlib.Path,
) -> None:
    """Each client-model-check row must take a source snapshot just before its agent."""
    result, calls, _ = run_agent_check(
        tmp_path, clients={"pi": VALID_PI_STUB}, model_aliases=("gemma4", "ornith")
    )

    assert result.returncode == 0, result.stderr
    source_calls = [
        line
        for line in calls.read_text().splitlines()
        if line.startswith("curl ") and ("open-meteo" in line or "er-api" in line)
    ]
    assert len(source_calls) == 4
    prompts = [line for line in calls.read_text().splitlines() if line.startswith("pi ")]
    assert all("Public snapshot for comparison" not in line for line in prompts)


@pytest.mark.parametrize(
    ("client", "stub"),
    (
        ("pi", STDIN_CONSUMING_PI_STUB),
        ("opencode", STDIN_CONSUMING_OPENCODE_STUB),
    ),
)
def test_agent_check_clients_cannot_consume_the_alias_loop_stdin(
    tmp_path: pathlib.Path, client: str, stub: str
) -> None:
    """A child reading stdin must not silently remove later model aliases."""
    result, _, _ = run_agent_check(
        tmp_path,
        clients={client: stub},
        model_aliases=("gemma4", "ornith"),
    )

    assert result.returncode == 0, result.stderr
    assert {
        line.removeprefix(f"PASS client={client} ").removesuffix(
            " reason=agent-returned-json"
        )
        for line in result.stdout.splitlines()
        if line.startswith(f"PASS client={client} ")
    } == {
        "model=gemma4 check=weather",
        "model=gemma4 check=fx",
        "model=ornith check=weather",
        "model=ornith check=fx",
    }


def test_agent_check_accepts_exactly_one_json_fence(tmp_path: pathlib.Path) -> None:
    """One JSON-marked fence is valid agent evidence."""
    result, _, _ = run_agent_check(tmp_path, clients={"pi": FENCED_JSON_PI_STUB})

    assert result.returncode == 0, result.stderr


def test_agent_check_retains_malformed_fenced_response_parser_diagnostics(
    tmp_path: pathlib.Path,
) -> None:
    """Invalid JSON inside a properly-closed fence must still fail with diagnostics."""
    result, _, _ = run_agent_check(
        tmp_path,
        clients={"pi": MALFORMED_FENCED_JSON_PI_STUB},
    )

    assert result.returncode != 0
    rows = agent_rows(result.stdout)
    assert len(rows) == 2
    for row in rows:
        assert_common_agent_fields(row, client="pi", exit_status="0")
        assert "Agent parser stderr:\n  jq: parse error:" in row
        assert "Client stderr:" not in row
        assert "Final response:\n  ```json" in row
    assert "Verdict: FAIL stage=agent evidence parsing" in result.stderr


def test_agent_check_prints_client_transcript_and_failure(
    tmp_path: pathlib.Path,
) -> None:
    """Failed client rows must display stderr and continue their matrix."""
    result, _, _ = run_agent_check(tmp_path, clients={"pi": FAILING_PI_STUB})

    assert result.returncode != 0
    assert "Client stderr:" in result.stdout
    assert "agent transport failed" in result.stdout
    assert "Verdict: FAIL stage=command exit" in result.stderr
    rows = agent_rows(result.stdout)
    assert len(rows) == 2
    for row in rows:
        assert_common_agent_fields(row, client="pi", exit_status="17")
        assert "Client stderr:\n  Authorization: Bearer test-key-not-a-secret" in row
        assert "Client JSONL transcript:" not in row
        assert "Agent parser stderr:" not in row
        assert "Final response:" not in row


@pytest.mark.parametrize(
    ("outcome", "stage", "diagnostic_reason", "row_reason"),
    (
        (
            "timeout",
            "agent timeout",
            "bounded client runtime expired",
            "timeout",
        ),
        (
            "transcript-limit",
            "transcript limit",
            "client transcript exceeded 33554432 bytes",
            "transcript-limit",
        ),
        (
            "stderr-limit",
            "stderr limit",
            "client stderr exceeded 33554432 bytes",
            "stderr-limit",
        ),
    ),
)
def test_agent_check_classifies_proved_resource_outcomes_and_continues(
    tmp_path: pathlib.Path,
    outcome: str,
    stage: str,
    diagnostic_reason: str,
    row_reason: str,
) -> None:
    result, calls, _ = run_agent_check(
        tmp_path,
        clients={"pi": VALID_PI_STUB},
        bounded_results=(
            bounded_result_json(outcome=outcome, exit_status=None),
        ),
        agent_client_stderr="bounded client warning",
        bounded_cli_stderr="bounded parser warning",
    )

    assert result.returncode != 0
    rows = agent_rows(result.stdout)
    assert len(rows) == 2
    failed_row = next(row for row in rows if "check=weather" in row)
    assert_common_agent_fields(failed_row, client="pi", exit_status="NOT REPORTED")
    assert "Relevant transcript excerpt:\n  Final assistant text:\n  {" in failed_row
    assert "Client stderr:\n  bounded client warning" in failed_row
    assert "Agent parser stderr:\n  bounded parser warning" in failed_row
    assert "Final response:" not in failed_row
    assert (
        f"Verdict: FAIL stage={stage} client=pi model=gemma4 check=weather "
        f"reason={diagnostic_reason}"
    ) in result.stderr
    assert f"FAIL client=pi model=gemma4 check=weather reason={row_reason}" in result.stdout
    assert "PASS client=pi model=gemma4 check=fx reason=agent-returned-json" in result.stdout
    assert bounded_call_count(calls) == 2
    assert "Results: 1 passed, 1 failed" in result.stdout


@pytest.mark.parametrize(
    ("outcome", "exit_status"),
    (
        ("completed", 0),
        ("timeout", None),
        ("transcript-limit", None),
        ("stderr-limit", None),
        ("boundary-failure", None),
    ),
)
def test_agent_check_valid_bounded_fixtures_report_exact_capture_sizes(
    tmp_path: pathlib.Path,
    outcome: str,
    exit_status: int | None,
) -> None:
    result, calls, _ = run_agent_check(
        tmp_path,
        clients={"pi": VALID_PI_STUB},
        bounded_results=(
            bounded_result_json(outcome=outcome, exit_status=exit_status),
        ),
        agent_client_stderr="fixture counter stderr",
        keep_artifacts=True,
    )

    emitted = json.loads(bounded_emitted_results(calls)[0].read_text())
    retained_line = next(
        line for line in result.stdout.splitlines() if line.startswith("Diagnostics retained: ")
    )
    retained_dir = pathlib.Path(retained_line.removeprefix("Diagnostics retained: "))
    transcript_sizes = [
        path.stat().st_size for path in retained_dir.glob("client-transcript.*")
    ]
    stderr_sizes = [
        path.stat().st_size for path in retained_dir.glob("client-stderr.*")
    ]

    assert emitted["outcome"] == outcome
    assert emitted["transcript_bytes"] > 0
    assert emitted["stderr_bytes"] > 0
    assert emitted["transcript_bytes"] in transcript_sizes
    assert emitted["stderr_bytes"] in stderr_sizes


@pytest.mark.parametrize(
    ("case", "bounded_result"),
    (
        (
            "explicit-boundary-failure",
            bounded_result_json(
                outcome="boundary-failure",
                exit_status=None,
            ),
        ),
        (
            "completed-unproved-cleanup",
            bounded_result_json(cleanup_proved=False),
        ),
        (
            "timeout-unproved-cleanup",
            bounded_result_json(
                outcome="timeout",
                exit_status=None,
                cleanup_proved=False,
            ),
        ),
        (
            "transcript-limit-unproved-cleanup",
            bounded_result_json(
                outcome="transcript-limit",
                exit_status=None,
                cleanup_proved=False,
            ),
        ),
        (
            "stderr-limit-unproved-cleanup",
            bounded_result_json(
                outcome="stderr-limit",
                exit_status=None,
                cleanup_proved=False,
            ),
        ),
    ),
)
def test_agent_check_aborts_the_complete_matrix_when_cleanup_is_uncertain(
    tmp_path: pathlib.Path,
    case: str,
    bounded_result: str,
) -> None:
    del case
    result, calls, _ = run_agent_check(
        tmp_path,
        clients={"pi": VALID_PI_STUB, "opencode": VALID_OPENCODE_STUB},
        model_aliases=("gemma4", "ornith"),
        bounded_results=(bounded_result,),
    )

    assert result.returncode != 0
    assert bounded_call_count(calls) == 1
    assert len(agent_rows(result.stdout)) == 1
    assert "Agent:\n  client=pi model=gemma4 check=weather" in result.stdout
    assert "client=pi model=gemma4 check=fx" not in result.stdout
    assert "model=ornith" not in result.stdout
    assert "client=opencode" not in result.stdout
    assert (
        "Verdict: FAIL stage=resource boundary client=pi model=gemma4 check=weather "
        "reason=scope setup or cleanup could not be proved"
    ) in result.stderr
    assert (
        "FAIL client=pi model=gemma4 check=weather reason=boundary-failure"
    ) in result.stdout
    assert "Results: 0 passed, 1 failed" in result.stdout


def test_agent_check_keeps_completed_nonzero_as_an_ordinary_client_failure(
    tmp_path: pathlib.Path,
) -> None:
    result, calls, _ = run_agent_check(
        tmp_path,
        clients={"pi": VALID_PI_STUB},
        bounded_results=(bounded_result_json(exit_status=17),),
    )

    assert result.returncode != 0
    assert bounded_call_count(calls) == 2
    assert "Verdict: FAIL stage=command exit" in result.stderr
    assert "stage=resource boundary" not in result.stderr
    assert "FAIL client=pi model=gemma4 check=weather reason=agent-failed" in result.stdout
    assert "PASS client=pi model=gemma4 check=fx reason=agent-returned-json" in result.stdout
    assert "Exit status:\n  17" in agent_rows(result.stdout)[0]


def test_agent_check_accepts_a_negative_integral_completed_exit_status(
    tmp_path: pathlib.Path,
) -> None:
    result, calls, _ = run_agent_check(
        tmp_path,
        clients={"pi": VALID_PI_STUB},
        bounded_results=(bounded_result_json(exit_status=-15),),
    )

    assert result.returncode != 0
    assert bounded_call_count(calls) == 2
    assert "Exit status:\n  -15" in agent_rows(result.stdout)[0]
    assert "Verdict: FAIL stage=command exit" in result.stderr
    assert "stage=resource boundary" not in result.stderr


@pytest.mark.parametrize("exit_status", (9007199254740993, -9007199254740993))
def test_agent_check_preserves_large_canonical_exit_status_text(
    tmp_path: pathlib.Path,
    exit_status: int,
) -> None:
    result, calls, _ = run_agent_check(
        tmp_path,
        clients={"pi": VALID_PI_STUB},
        bounded_results=(bounded_result_json(exit_status=exit_status),),
    )

    assert result.returncode != 0
    assert bounded_call_count(calls) == 2
    assert f"Exit status:\n  {exit_status}" in agent_rows(result.stdout)[0]
    assert "Verdict: FAIL stage=command exit" in result.stderr
    assert "stage=resource boundary" not in result.stderr
    assert "FAIL client=pi model=gemma4 check=weather reason=agent-failed" in result.stdout
    assert "PASS client=pi model=gemma4 check=fx reason=agent-returned-json" in result.stdout
    assert "Results: 1 passed, 1 failed" in result.stdout


def test_agent_check_treats_completed_null_status_as_boundary_uncertainty(
    tmp_path: pathlib.Path,
) -> None:
    result, calls, _ = run_agent_check(
        tmp_path,
        clients={"pi": VALID_PI_STUB},
        model_aliases=("gemma4", "ornith"),
        bounded_results=(bounded_result_json(exit_status=None),),
    )

    assert result.returncode != 0
    assert bounded_call_count(calls) == 1
    assert len(agent_rows(result.stdout)) == 1
    assert "Exit status:\n  NOT REPORTED" in result.stdout
    assert "Verdict: FAIL stage=resource boundary" in result.stderr
    assert "reason=boundary-failure" in result.stdout


def test_agent_check_treats_bounded_cli_failure_as_boundary_uncertainty(
    tmp_path: pathlib.Path,
) -> None:
    result, calls, _ = run_agent_check(
        tmp_path,
        clients={"pi": VALID_PI_STUB, "opencode": VALID_OPENCODE_STUB},
        model_aliases=("gemma4", "ornith"),
        bounded_exit_statuses=(73,),
        bounded_cli_stderr="bounded CLI setup failed",
    )

    assert result.returncode != 0
    assert bounded_call_count(calls) == 1
    assert len(agent_rows(result.stdout)) == 1
    assert "Exit status:\n  NOT RUN" in result.stdout
    assert "Agent parser stderr:\n  bounded CLI setup failed" in result.stdout
    assert "Verdict: FAIL stage=resource boundary" in result.stderr
    assert "reason=boundary-failure" in result.stdout


@pytest.mark.parametrize(
    ("fault", "bounded_calls", "diagnostic"),
    (
        ("mktemp", 0, "bounded result mktemp failed"),
        ("chmod", 0, "bounded result chmod failed"),
        ("write", 0, "bounded result write path replaced"),
        ("read", 1, "bounded result read path replaced"),
    ),
)
def test_agent_check_aborts_on_bounded_result_file_fault(
    tmp_path: pathlib.Path,
    fault: str,
    bounded_calls: int,
    diagnostic: str,
) -> None:
    result, calls, _ = run_agent_check(
        tmp_path,
        clients={"pi": VALID_PI_STUB, "opencode": VALID_OPENCODE_STUB},
        model_aliases=("gemma4", "ornith"),
        bounded_result_fault=fault,
    )

    assert result.returncode != 0
    assert bounded_call_count(calls) == bounded_calls
    rows = agent_rows(result.stdout)
    assert len(rows) == 1
    assert_common_agent_fields(rows[0], client="pi", exit_status="NOT RUN")
    assert f"Agent parser stderr:\n  {diagnostic}" in rows[0]
    assert "client=pi model=gemma4 check=fx" not in result.stdout
    assert "model=ornith" not in result.stdout
    assert "client=opencode" not in result.stdout
    assert (
        "Verdict: FAIL stage=resource boundary client=pi model=gemma4 check=weather "
        "reason=scope setup or cleanup could not be proved"
    ) in result.stderr
    assert (
        "FAIL client=pi model=gemma4 check=weather reason=boundary-failure"
    ) in result.stdout
    assert "Results: 0 passed, 1 failed" in result.stdout


@pytest.mark.parametrize("counter", ("transcript_bytes", "stderr_bytes"))
def test_agent_check_aborts_when_runner_counter_exceeds_stream_limit(
    tmp_path: pathlib.Path,
    counter: str,
) -> None:
    result, calls, _ = run_agent_check(
        tmp_path,
        clients={"pi": VALID_PI_STUB, "opencode": VALID_OPENCODE_STUB},
        model_aliases=("gemma4", "ornith"),
        bounded_results=(bounded_result_json(**{counter: 33_554_433}),),
        bounded_wc_override=counter,
    )

    assert result.returncode != 0
    assert bounded_call_count(calls) == 1
    assert len(agent_rows(result.stdout)) == 1
    assert "Verdict: FAIL stage=resource boundary" in result.stderr
    assert "reason=boundary-failure" in result.stdout
    assert "client=pi model=gemma4 check=fx" not in result.stdout
    assert "client=opencode" not in result.stdout


@pytest.mark.parametrize("stream", ("transcript", "stderr"))
def test_agent_check_aborts_when_runner_counter_mismatches_capture_size(
    tmp_path: pathlib.Path,
    stream: str,
) -> None:
    result, calls, _ = run_agent_check(
        tmp_path,
        clients={"pi": VALID_PI_STUB, "opencode": VALID_OPENCODE_STUB},
        model_aliases=("gemma4", "ornith"),
        bounded_counter_mismatch=stream,
    )

    assert result.returncode != 0
    assert bounded_call_count(calls) == 1
    assert len(agent_rows(result.stdout)) == 1
    assert "Verdict: FAIL stage=resource boundary" in result.stderr
    assert "reason=boundary-failure" in result.stdout
    assert "client=pi model=gemma4 check=fx" not in result.stdout
    assert "client=opencode" not in result.stdout


_VALID_BOUNDED_RESULT = bounded_result_json()
_MISSING_BOUNDED_RESULT_KEY = json.loads(_VALID_BOUNDED_RESULT)
del _MISSING_BOUNDED_RESULT_KEY["stderr_bytes"]


@pytest.mark.parametrize(
    "bounded_result",
    (
        pytest.param("", id="empty"),
        pytest.param("{", id="malformed"),
        pytest.param(
            _VALID_BOUNDED_RESULT.replace('"exit_status":0', '"exit_status":NaN'),
            id="non-json-nan",
        ),
        pytest.param(
            _VALID_BOUNDED_RESULT.replace(
                '"transcript_bytes":0',
                '"transcript_bytes":01',
            ),
            id="non-json-leading-zero",
        ),
        pytest.param(
            _VALID_BOUNDED_RESULT.replace('"schema":1', '"schema":1e+0'),
            id="schema-exponent",
        ),
        pytest.param(
            _VALID_BOUNDED_RESULT.replace('"schema":1', '"schema":1.0'),
            id="schema-decimal",
        ),
        pytest.param(
            _VALID_BOUNDED_RESULT.replace('"exit_status":0', '"exit_status":1e-999'),
            id="exit-positive-underflow",
        ),
        pytest.param(
            _VALID_BOUNDED_RESULT.replace(
                '"exit_status":0',
                '"exit_status":9007199254740992.5',
            ),
            id="exit-rounded-fraction",
        ),
        pytest.param(
            _VALID_BOUNDED_RESULT.replace('"exit_status":0', '"exit_status":1e+0'),
            id="exit-exponent",
        ),
        pytest.param(
            _VALID_BOUNDED_RESULT.replace('"exit_status":0', '"exit_status":1.0'),
            id="exit-decimal",
        ),
        pytest.param(
            _VALID_BOUNDED_RESULT.replace(
                '"transcript_bytes":0',
                '"transcript_bytes":-1e-999',
            ),
            id="transcript-negative-underflow",
        ),
        pytest.param(
            _VALID_BOUNDED_RESULT.replace(
                '"transcript_bytes":0',
                '"transcript_bytes":0e+0',
            ),
            id="transcript-exponent",
        ),
        pytest.param(
            _VALID_BOUNDED_RESULT.replace(
                '"transcript_bytes":0',
                '"transcript_bytes":0.0',
            ),
            id="transcript-decimal",
        ),
        pytest.param(
            _VALID_BOUNDED_RESULT.replace('"stderr_bytes":0', '"stderr_bytes":0e+0'),
            id="stderr-exponent",
        ),
        pytest.param(
            _VALID_BOUNDED_RESULT.replace('"stderr_bytes":0', '"stderr_bytes":0.0'),
            id="stderr-decimal",
        ),
        pytest.param(
            b"\x00" + _VALID_BOUNDED_RESULT.encode(),
            id="nul-before-object",
        ),
        pytest.param(
            _VALID_BOUNDED_RESULT.encode() + b"\x00",
            id="nul-after-object",
        ),
        pytest.param(
            _VALID_BOUNDED_RESULT.replace('"schema":1', '"schema":1,"schema":1'),
            id="duplicate-key",
        ),
        pytest.param(
            f"{_VALID_BOUNDED_RESULT}\n{_VALID_BOUNDED_RESULT}",
            id="multiple-top-level-values",
        ),
        pytest.param("[]", id="top-level-array"),
        pytest.param(bounded_result_json(extra=True), id="extra-key"),
        pytest.param(
            json.dumps(_MISSING_BOUNDED_RESULT_KEY),
            id="missing-key",
        ),
        pytest.param(bounded_result_json(schema=2), id="wrong-schema"),
        pytest.param(bounded_result_json(schema="1"), id="schema-string"),
        pytest.param(bounded_result_json(outcome="unknown"), id="unknown-outcome"),
        pytest.param(bounded_result_json(outcome=1), id="outcome-number"),
        pytest.param(bounded_result_json(exit_status="0"), id="exit-string"),
        pytest.param(bounded_result_json(exit_status=True), id="exit-boolean"),
        pytest.param(bounded_result_json(exit_status=[]), id="exit-array"),
        pytest.param(bounded_result_json(exit_status={}), id="exit-object"),
        pytest.param(bounded_result_json(exit_status=1.5), id="exit-fraction"),
        pytest.param(
            bounded_result_json(transcript_bytes="0"),
            id="transcript-string",
        ),
        pytest.param(
            bounded_result_json(transcript_bytes=True),
            id="transcript-boolean",
        ),
        pytest.param(
            bounded_result_json(transcript_bytes=[]),
            id="transcript-array",
        ),
        pytest.param(
            bounded_result_json(transcript_bytes={}),
            id="transcript-object",
        ),
        pytest.param(
            bounded_result_json(transcript_bytes=1.5),
            id="transcript-fraction",
        ),
        pytest.param(
            bounded_result_json(transcript_bytes=-1),
            id="transcript-negative",
        ),
        pytest.param(bounded_result_json(stderr_bytes="0"), id="stderr-string"),
        pytest.param(bounded_result_json(stderr_bytes=True), id="stderr-boolean"),
        pytest.param(bounded_result_json(stderr_bytes=[]), id="stderr-array"),
        pytest.param(bounded_result_json(stderr_bytes={}), id="stderr-object"),
        pytest.param(bounded_result_json(stderr_bytes=1.5), id="stderr-fraction"),
        pytest.param(bounded_result_json(stderr_bytes=-1), id="stderr-negative"),
        pytest.param(bounded_result_json(cleanup_proved=1), id="cleanup-number"),
        pytest.param(
            bounded_result_json(cleanup_proved="true"),
            id="cleanup-string",
        ),
        pytest.param(bounded_result_json(cleanup_proved=None), id="cleanup-null"),
        pytest.param(bounded_result_json(cleanup_proved=[]), id="cleanup-array"),
        pytest.param(bounded_result_json(cleanup_proved={}), id="cleanup-object"),
    ),
)
def test_agent_check_rejects_every_invalid_bounded_result(
    tmp_path: pathlib.Path,
    bounded_result: str | bytes,
) -> None:
    result, calls, _ = run_agent_check(
        tmp_path,
        clients={"pi": VALID_PI_STUB, "opencode": VALID_OPENCODE_STUB},
        model_aliases=("gemma4", "ornith"),
        bounded_results=(bounded_result,),
        raw_bounded_results=True,
    )

    assert result.returncode != 0
    assert bounded_call_count(calls) == 1
    assert len(agent_rows(result.stdout)) == 1
    assert "Exit status:\n  NOT RUN" in result.stdout
    assert "Verdict: FAIL stage=resource boundary" in result.stderr
    assert "reason=boundary-failure" in result.stdout
    assert "Results: 0 passed, 1 failed" in result.stdout
    expected_result = (
        bounded_result.encode() if isinstance(bounded_result, str) else bounded_result
    )
    assert bounded_emitted_results(calls)[0].read_bytes() == expected_result


def test_agent_check_never_evaluates_runner_controlled_result_text(
    tmp_path: pathlib.Path,
) -> None:
    marker = tmp_path / "runner-output-was-evaluated"
    result, calls, _ = run_agent_check(
        tmp_path,
        clients={"pi": VALID_PI_STUB},
        bounded_results=(
            bounded_result_json(exit_status=f"$(touch {marker})"),
        ),
        raw_bounded_results=True,
    )

    assert result.returncode != 0
    assert bounded_call_count(calls) == 1
    assert not marker.exists()
    assert "Verdict: FAIL stage=resource boundary" in result.stderr


def test_agent_check_retains_final_response_parser_diagnostics(
    tmp_path: pathlib.Path,
) -> None:
    result, _, _ = run_agent_check(tmp_path, clients={"pi": INVALID_FINAL_PI_STUB})

    assert result.returncode != 0
    rows = agent_rows(result.stdout)
    assert len(rows) == 2
    for row in rows:
        assert_common_agent_fields(row, client="pi", exit_status="0")
        assert "Final response:\n  not JSON" in row
        assert "Relevant transcript excerpt:\n  Final assistant text:\n  not JSON" in row
        assert "Agent parser stderr:\n  " in row
        assert "parse error:" in row
        assert "Client stderr:" not in row
    assert "Verdict: FAIL stage=agent evidence parsing" in result.stderr


def test_agent_check_renders_concise_source_evidence_mismatch(
    tmp_path: pathlib.Path,
) -> None:
    result, _, _ = run_agent_check(
        tmp_path,
        clients={"pi": VALID_PI_STUB},
        agent_weather_evidence_override='{"temperature_2m":-999999}',
    )

    assert result.returncode != 0
    rows = agent_rows(result.stdout)
    assert len(rows) == 2
    weather_row = next(row for row in rows if "check=weather" in row)
    assert_common_agent_fields(weather_row, client="pi", exit_status="0")
    assert "Relevant transcript excerpt:\n  Final assistant text:\n  {" in weather_row
    assert "Final response:\n  {" in weather_row
    assert (
        "Validated:\n  source_url=https://api.open-meteo.com/v1/forecast?"
        "latitude=-33.4489&longitude=-70.6693&current=temperature_2m,weather_code&"
        "timezone=America%2FSantiago\n  source_date=2026-07-27\n  "
        "temperature_2m=16.3\n  weather_code=3"
    ) in weather_row
    for legacy_block in (
        "Identity:",
        "Assistant final text:",
        "Agent evidence:",
        "Fresh source snapshot:",
    ):
        assert legacy_block not in weather_row


def test_agent_check_marks_unlaunched_client_as_not_run(
    tmp_path: pathlib.Path,
) -> None:
    result, _, _ = run_agent_check(
        tmp_path,
        clients={"pi": VALID_PI_STUB},
        fail_pi_configuration=True,
    )

    assert result.returncode != 0
    rows = agent_rows(result.stdout)
    assert len(rows) == 2
    for row in rows:
        assert_common_agent_fields(row, client="pi", exit_status="NOT RUN")
        assert "Client JSONL transcript:" not in row
        assert "Client stderr:" not in row
        assert "Agent parser stderr:" not in row
        assert "Final response:" not in row
    assert "Verdict: FAIL stage=command exit" in result.stderr


def test_agent_check_retains_only_redacted_private_diagnostics(
    tmp_path: pathlib.Path,
) -> None:
    """Opt-in retained evidence must remain private and omit client credentials."""
    result, _, _ = run_agent_check(
        tmp_path,
        clients={"pi": VALID_PI_STUB},
        api_key="retained-fixture-key",
        keep_artifacts=True,
    )

    assert result.returncode == 0, result.stderr
    retained_line = next(
        line for line in result.stdout.splitlines() if line.startswith("Diagnostics retained: ")
    )
    diagnostic_dir = pathlib.Path(retained_line.removeprefix("Diagnostics retained: "))
    assert stat.S_IMODE(diagnostic_dir.stat().st_mode) == 0o700
    files = [path for path in diagnostic_dir.rglob("*") if path.is_file()]
    assert files
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in files)
    retained_text = "".join(path.read_text() for path in files)
    assert "retained-fixture-key" not in retained_text
    assert "Authorization: Bearer retained-fixture-key" not in retained_text


def test_agent_check_removes_workspace_when_diagnostic_finalization_fails(
    tmp_path: pathlib.Path,
) -> None:
    result, calls, _ = run_agent_check(
        tmp_path,
        clients={"pi": VALID_PI_STUB},
        keep_artifacts=True,
        finalizer_failure=True,
    )
    workspace = pathlib.Path(calls.with_name("workspace-path").read_text())

    assert result.returncode != 0
    assert "could not traverse diagnostic artifacts" in result.stderr
    assert "Diagnostics retained:" not in result.stdout
    assert not workspace.exists()


@pytest.mark.parametrize(
    ("fault", "diagnostic"),
    (
        ("chmod", "could not secure private workspace"),
        ("diagnostic", "could not prepare diagnostic directory"),
    ),
)
def test_agent_check_removes_workspace_after_early_setup_failure(
    tmp_path: pathlib.Path,
    fault: str,
    diagnostic: str,
) -> None:
    result, calls, _ = run_agent_check(
        tmp_path,
        clients={"pi": VALID_PI_STUB},
        workspace_setup_fault=fault,
    )
    workspace = pathlib.Path(calls.with_name("workspace-path").read_text())

    assert result.returncode != 0
    assert diagnostic in result.stderr
    assert not workspace.exists()
    assert calls.read_text() == ""
    assert bounded_call_count(calls) == 0
    assert "Agent:" not in result.stdout
    assert "Diagnostics retained:" not in result.stdout


def test_agent_check_runs_pi_for_each_model_and_live_check(tmp_path: pathlib.Path) -> None:
    """Pi must use an isolated JSON-mode invocation for each matrix cell."""
    api_key = "pi-adapter-fixture-secret"
    result, calls, artifacts = run_agent_check(
        tmp_path,
        clients={"pi": VALID_PI_STUB},
        model_aliases=("gemma4", "ornith"),
        api_key=api_key,
    )

    assert result.returncode == 0, result.stderr
    assert count_rows(result.stdout, "PASS client=pi") == 4
    assert bounded_call_count(calls) == 4
    assert len(bounded_invocations(calls)) == 4
    assert all("run-agent-bounded" in call for call in bounded_invocations(calls))
    assert all("--runtime-seconds" not in call for call in bounded_invocations(calls))
    assert all("--grace-seconds" not in call for call in bounded_invocations(calls))
    assert all("--stream-limit-bytes" not in call for call in bounded_invocations(calls))
    recorded = calls.read_text()
    assert "--no-session" in recorded
    assert "--no-extensions" in recorded
    assert "--no-skills" in recorded
    assert "--no-prompt-templates" in recorded
    assert "--no-context-files" in recorded
    assert "--tools bash" not in recorded
    assert "-p" in recorded and "--mode json" in recorded
    assert "llm-env/gemma4" in recorded
    configs = list(artifacts.glob("pi-*.json"))
    assert len(configs) == 4
    assert all(api_key not in path.read_text() for path in configs)
    assert all('"apiKey": "!yq -r' in path.read_text() for path in configs)
    assert api_key not in result.stdout
    assert api_key not in result.stderr
    assert api_key not in recorded


def test_agent_check_runs_opencode_for_each_model_and_live_check(
    tmp_path: pathlib.Path,
) -> None:
    """OpenCode must use an isolated JSON-mode invocation for each matrix cell."""
    api_key = "opencode-adapter-fixture-secret"
    result, calls, artifacts = run_agent_check(
        tmp_path,
        clients={"opencode": VALID_OPENCODE_STUB},
        model_aliases=("gemma4", "ornith"),
        api_key=api_key,
    )

    assert result.returncode == 0, result.stderr
    assert count_rows(result.stdout, "PASS client=opencode") == 4
    assert bounded_call_count(calls) == 4
    assert len(bounded_invocations(calls)) == 4
    assert all("run-agent-bounded" in call for call in bounded_invocations(calls))
    assert all("--runtime-seconds" not in call for call in bounded_invocations(calls))
    assert all("--grace-seconds" not in call for call in bounded_invocations(calls))
    assert all("--stream-limit-bytes" not in call for call in bounded_invocations(calls))
    recorded = calls.read_text()
    assert "run --format json --model llm-env/gemma4" in recorded
    assert f"xdg={tmp_path / 'host-xdg-config'}" not in recorded
    configs = list(artifacts.glob("opencode-*.jsonc"))
    assert len(configs) == 4
    assert all(api_key not in path.read_text() for path in configs)
    assert all("{env:OPENCODE_API_KEY}" in path.read_text() for path in configs)
    assert all("tools" not in json.loads(path.read_text()) for path in configs)
    assert api_key not in result.stdout
    assert api_key not in result.stderr
    assert api_key not in recorded


@pytest.mark.parametrize(
    ("client", "stub"), (("pi", VALID_PI_STUB), ("opencode", VALID_OPENCODE_STUB))
)
def test_agent_check_renders_concise_success_rows(
    tmp_path: pathlib.Path, client: str, stub: str
) -> None:
    result, _, _ = run_agent_check(tmp_path, clients={client: stub})

    assert result.returncode == 0, result.stderr
    assert "Source stderr:" not in result.stdout
    assert "Source stdout:" not in result.stdout
    rows = agent_rows(result.stdout)
    assert len(rows) == 2
    for row in rows:
        assert_common_agent_fields(row, client=client, exit_status="0")
        assert "Final response:\n  {" in row
        assert "Validated:\n  source_url=https://" in row
        assert "Verdict: PASS" in row
        assert "Client JSONL transcript:" not in row
        assert "Client stderr:" not in row
        assert "Agent parser stderr:" not in row
        assert "Agent evidence:" not in row
        assert "Fresh source snapshot:" not in row
    if client == "pi":
        assert "Command: PI_CODING_AGENT_DIR=<private> pi --no-session" in result.stdout
        assert "OPENCODE_API_KEY=" not in result.stdout
    else:
        assert (
            "Command: HOME=<private> XDG_CONFIG_HOME=<private> XDG_DATA_HOME=<private> "
            "XDG_STATE_HOME=<private> OPENCODE_CONFIG=<private> "
            "OPENCODE_API_KEY=<redacted> opencode run"
        ) in result.stdout


def test_agent_check_keeps_nonempty_success_source_parser_diagnostics(
    tmp_path: pathlib.Path,
) -> None:
    result, _, _ = run_agent_check(
        tmp_path,
        clients={"pi": VALID_PI_STUB},
        source_parser_stderr="source parser warning",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.count("Source parser stderr:\n  source parser warning") == 2


def test_agent_check_omits_empty_success_source_parser_diagnostics(
    tmp_path: pathlib.Path,
) -> None:
    result, _, _ = run_agent_check(tmp_path, clients={"pi": VALID_PI_STUB})

    assert result.returncode == 0, result.stderr
    assert "Source parser stderr:" not in result.stdout
    assert "Source parser stderr:\n  (empty)" not in result.stdout


def test_agent_check_keeps_nonempty_success_diagnostics(
    tmp_path: pathlib.Path,
) -> None:
    result, _, _ = run_agent_check(
        tmp_path,
        clients={"pi": VALID_PI_STUB},
        agent_client_stderr="client warning",
        agent_parser_stderr="parser warning",
        bounded_cli_stderr="bounded runner warning",
    )

    assert result.returncode == 0, result.stderr
    rows = agent_rows(result.stdout)
    assert len(rows) == 2
    for row in rows:
        assert_common_agent_fields(row, client="pi", exit_status="0")
        assert "Client JSONL transcript:" not in row
        assert "Client stderr:\n  client warning" in row
        assert "Agent parser stderr:\n  bounded runner warning\n  parser warning" in row
        assert "Final response:\n  {" in row
        assert "Validated:\n  source_url=https://" in row
        assert "Verdict: PASS" in row


def test_agent_check_caps_file_backed_client_stderr_for_each_success_row(
    tmp_path: pathlib.Path,
) -> None:
    sentinel = "client-stderr-cap-sentinel"
    client_stderr = tmp_path / "large-client-stderr"
    client_stderr.write_bytes(b"x" * 262_144 + sentinel.encode())

    result, _, _ = run_agent_check(
        tmp_path,
        clients={"pi": VALID_PI_STUB},
        agent_client_stderr_file=client_stderr,
    )

    assert result.returncode == 0, result.stderr
    assert sentinel not in result.stdout
    assert sentinel not in result.stderr
    rows = agent_rows(result.stdout)
    assert len(rows) == 2
    for row in rows:
        assert row.count("Client stderr:") == 1
        displayed_body = row.split("Client stderr:\n", 1)[1].split(
            "Final response:\n", 1
        )[0]
        assert displayed_body.count("x") == 262_144, result.stderr


def test_agent_check_routes_every_file_backed_display_through_excerpt_helper() -> None:
    script = (SCRIPT_DIR / "check-with-agents.sh").read_text()
    display_files = {
        "Source stdout": "$stdout_file",
        "Source stderr": "$stderr_file",
        "Source parser stderr": "$parser_stderr_file",
        "Client JSONL transcript": "$transcript_file",
        "Client stderr": "$client_stderr_file",
        "Agent parser stderr": "$parser_error_file",
        "Final response": "$final_file",
    }
    call_pattern = re.compile(
        r'^\s*(log_(?:block|nonempty_block|file_excerpt))\s+"([^"]+)"\s+(.+)$',
        re.MULTILINE,
    )
    display_calls = [
        call for call in call_pattern.findall(script) if call[1] in display_files
    ]

    assert {label for _, label, _ in display_calls} == set(display_files)
    for function, label, arguments in display_calls:
        assert function == "log_file_excerpt"
        assert arguments == (
            f'"{display_files[label]}" "$agent_diagnostic_excerpt_bytes"'
        )


@pytest.mark.parametrize(
    ("client", "stub"),
    (("pi", VALID_PI_STUB), ("opencode", VALID_OPENCODE_STUB)),
)
@pytest.mark.parametrize(
    ("timestamp", "passes", "expected_difference"),
    (
        ("2026-07-27T13:00:45.123Z", True, ""),
        ("2026-07-27T09:00:45-04:00", True, ""),
        ("2026-07-27Tnot-a-time", False, "expected=ISO-8601"),
        ("not-a-timestamp", False, "expected=ISO-8601"),
        ("2026-07-28T04:00:00Z", False, 'expected_date="2026-07-27"'),
    ),
)
def test_agent_check_requires_full_iso_timestamp_and_matching_source_date(
    tmp_path: pathlib.Path,
    client: str,
    stub: str,
    timestamp: str,
    passes: bool,
    expected_difference: str,
) -> None:
    result, _, _ = run_agent_check(
        tmp_path,
        clients={client: stub},
        agent_source_timestamp=timestamp,
    )

    if passes:
        assert result.returncode == 0, result.stderr
        assert count_rows(result.stdout, f"PASS client={client}") == 2
    else:
        assert result.returncode != 0
        assert count_rows(result.stdout, f"FAIL client={client}") == 2
        assert "field=source_timestamp" in result.stdout
        assert expected_difference in result.stdout


@pytest.mark.parametrize(
    ("weather_override", "fx_override"),
    (
        ('{"source_timestamp":"2026-07-28T03:30:00Z"}', None),
        (None, '{"source_timestamp":"2026-07-26T20:30:00-04:00"}'),
    ),
)
def test_agent_check_compares_timestamp_dates_in_the_source_timezone(
    tmp_path: pathlib.Path,
    weather_override: str | None,
    fx_override: str | None,
) -> None:
    """Boundary offsets must resolve to the weather or FX source calendar date."""
    result, _, _ = run_agent_check(
        tmp_path,
        clients={"pi": VALID_PI_STUB},
        agent_weather_evidence_override=weather_override,
        agent_fx_evidence_override=fx_override,
    )

    assert result.returncode == 0, result.stderr
    assert count_rows(result.stdout, "PASS client=pi") == 2


@pytest.mark.parametrize(
    ("timestamp", "expected_diagnostic", "failure_count"),
    (
        ("agent-supplied-invalid-timestamp", 'received="<redacted>"', 2),
        ("2026-07-28T00:00:00Z", 'received_date="<redacted>"', 1),
    ),
)
def test_agent_check_redacts_agent_timestamps_from_mismatch_rows(
    tmp_path: pathlib.Path,
    timestamp: str,
    expected_diagnostic: str,
    failure_count: int,
) -> None:
    """Failure rows must not repeat agent-controlled timestamp values."""
    result, _, _ = run_agent_check(
        tmp_path,
        clients={"pi": VALID_PI_STUB},
        agent_source_timestamp=timestamp,
    )

    mismatch_rows = "\n".join(
        line for line in result.stdout.splitlines() if line.startswith("FAIL client=pi")
    )
    assert result.returncode != 0
    assert count_rows(result.stdout, "FAIL client=pi") == failure_count
    assert timestamp not in mismatch_rows
    assert expected_diagnostic in mismatch_rows


@pytest.mark.parametrize(
    ("client", "stub"),
    (("pi", VALID_PI_STUB), ("opencode", VALID_OPENCODE_STUB)),
)
@pytest.mark.parametrize(
    "unsafe_suffix",
    (pytest.param("\x00", id="nul"), pytest.param("\n", id="newline"), pytest.param("\t", id="tab")),
)
def test_agent_check_classifies_unsafe_evidence_timestamp_as_redacted_mismatch(
    tmp_path: pathlib.Path,
    client: str,
    stub: str,
    unsafe_suffix: str,
) -> None:
    override = json.dumps(
        {"source_timestamp": "2026-07-27T13:00:01" + unsafe_suffix}
    )
    result, _, _ = run_agent_check(
        tmp_path,
        clients={client: stub},
        agent_weather_evidence_override=override,
        agent_fx_evidence_override=override,
    )
    mismatch_rows = [
        line
        for line in result.stdout.splitlines()
        if line.startswith(f"FAIL client={client}")
    ]

    assert result.returncode != 0
    assert count_rows(result.stdout, f"PASS client={client}") == 0
    assert len(mismatch_rows) == 2
    assert all("field=source_timestamp" in line for line in mismatch_rows)
    assert all('received="<redacted>"' in line for line in mismatch_rows)
    assert "stage=source-evidence comparison" not in result.stderr
    assert "ignored null byte" not in result.stderr


@pytest.mark.parametrize(
    ("client", "stub"),
    (("pi", VALID_PI_STUB), ("opencode", VALID_OPENCODE_STUB)),
)
def test_agent_check_rejects_overlong_evidence_timestamp(
    tmp_path: pathlib.Path,
    client: str,
    stub: str,
) -> None:
    timestamp = "2026-07-27T13:00:01." + ("1" * 45)
    assert len(timestamp) == 65
    override = json.dumps({"source_timestamp": timestamp})
    result, _, _ = run_agent_check(
        tmp_path,
        clients={client: stub},
        agent_weather_evidence_override=override,
        agent_fx_evidence_override=override,
    )
    mismatch_rows = [
        line
        for line in result.stdout.splitlines()
        if line.startswith(f"FAIL client={client}")
    ]

    assert result.returncode != 0
    assert count_rows(result.stdout, f"PASS client={client}") == 0
    assert len(mismatch_rows) == 2
    assert all("field=source_timestamp" in line for line in mismatch_rows)
    assert all('received="<redacted>"' in line for line in mismatch_rows)


@pytest.mark.parametrize(
    ("client", "stub"),
    (("pi", VALID_PI_STUB), ("opencode", VALID_OPENCODE_STUB)),
)
@pytest.mark.parametrize(
    ("weather_override", "fx_override", "expected_field"),
    (
        ('{"source_url":"not-a-url"}', None, "source_url"),
        (None, '{"source_url":"not-a-url"}', "source_url"),
        ('{"temperature_2m":-999999}', None, "temperature_2m"),
        ('{"weather_code":"not-a-number"}', None, "weather_code"),
        (None, '{"usd_to_clp":-999999}', "usd_to_clp"),
    ),
)
def test_agent_check_keeps_exact_source_url_and_numeric_evidence_checks(
    tmp_path: pathlib.Path,
    client: str,
    stub: str,
    weather_override: str | None,
    fx_override: str | None,
    expected_field: str,
) -> None:
    result, _, _ = run_agent_check(
        tmp_path,
        clients={client: stub},
        agent_weather_evidence_override=weather_override,
        agent_fx_evidence_override=fx_override,
    )

    assert result.returncode != 0
    assert count_rows(result.stdout, f"FAIL client={client}") == 1
    assert count_rows(result.stdout, f"PASS client={client}") == 1
    assert f"field={expected_field}" in result.stdout


@pytest.mark.parametrize(
    ("client", "stub"),
    (("pi", VALID_PI_STUB), ("opencode", VALID_OPENCODE_STUB)),
)
def test_agent_check_prompt_trusts_the_agents_own_tooling(
    tmp_path: pathlib.Path, client: str, stub: str
) -> None:
    """The prompt must name a source and output shape, not dictate a literal command."""
    result, calls, artifacts = run_agent_check(tmp_path, clients={client: stub})

    assert result.returncode == 0, result.stderr
    prompts = [line for line in calls.read_text().splitlines() if line.startswith(f"{client} ")]
    assert len(prompts) == 2
    for prompt in prompts:
        assert "--max-filesize" not in prompt
        assert "Using your own tools, find" in prompt
        assert "curl" not in prompt
        assert "You MUST use bash" not in prompt
        assert "byte-for-byte" not in prompt
        assert "Return exactly one JSON object containing" in prompt
        assert "source_url must be the exact URL of the API endpoint you used." in prompt
        assert "source_timestamp must be the source's own observation/update time, in ISO-8601." in prompt

    weather_prompt = next(prompt for prompt in prompts if "weather_code" in prompt)
    assert "the current weather" in weather_prompt
    assert "Santiago, Chile" in weather_prompt
    assert "source_url, source_timestamp, temperature_2m, and weather_code" in weather_prompt

    fx_prompt = next(prompt for prompt in prompts if "usd_to_clp" in prompt)
    assert "the current USD to CLP exchange rate" in fx_prompt
    assert "source_url, source_timestamp, and usd_to_clp" in fx_prompt

    expected_weather_prompt = (
        "Using your own tools, find the current weather (temperature in Celsius and "
        "WMO weather code) for Santiago, Chile, from a public weather API. Return "
        "exactly one JSON object containing source_url, source_timestamp, "
        "temperature_2m, and weather_code. source_url must be the exact URL of the "
        "API endpoint you used. source_timestamp must be the source's own "
        "observation/update time, in ISO-8601."
    )
    expected_fx_prompt = (
        "Using your own tools, find the current USD to CLP exchange rate, from a "
        "public FX API. Return exactly one JSON object containing source_url, "
        "source_timestamp, and usd_to_clp. source_url must be the exact URL of the "
        "API endpoint you used. source_timestamp must be the source's own "
        "observation/update time, in ISO-8601."
    )
    recorded_prompt_bytes = {
        path.read_bytes() for path in artifacts.glob(f"prompt-{client}-*")
    }
    assert recorded_prompt_bytes == {
        expected_weather_prompt.encode(),
        expected_fx_prompt.encode(),
    }
    assert all(
        "--max-filesize" not in invocation for invocation in bounded_invocations(calls)
    )


def test_agent_check_reports_final_pass_and_fail_totals(tmp_path: pathlib.Path) -> None:
    """The matrix summary must make mixed outcomes immediately visible."""
    result, _, _ = run_agent_check(
        tmp_path,
        clients={"pi": VALID_PI_STUB},
        agent_weather_evidence_override='{"temperature_2m":-999999}',
    )

    assert result.returncode != 0
    assert "Results: 1 passed, 1 failed" in result.stdout


@pytest.mark.parametrize(
    ("client", "stub"),
    (("pi", VALID_PI_STUB), ("opencode", VALID_OPENCODE_STUB)),
)
def test_agent_check_permits_only_surrounding_agent_json_whitespace(
    tmp_path: pathlib.Path, client: str, stub: str
) -> None:
    """Whitespace around one agent JSON object must remain valid."""
    result, _, _ = run_agent_check(
        tmp_path,
        clients={client: stub},
        agent_response_prefix=" \n\t",
        agent_response_suffix="\r\n ",
    )

    assert result.returncode == 0, result.stderr
    assert count_rows(result.stdout, f"PASS client={client}") == 2


@pytest.mark.parametrize(
    ("client", "stub"),
    (("pi", VALID_PI_STUB), ("opencode", VALID_OPENCODE_STUB)),
)
@pytest.mark.parametrize(
    "additional_value", ("{}", "[]", "0", '{"second": true}')
)
def test_agent_check_rejects_every_extra_agent_json_value(
    tmp_path: pathlib.Path, client: str, stub: str, additional_value: str
) -> None:
    """A parser accepting any trailing JSON value would pass this invalid reply."""
    result, _, _ = run_agent_check(
        tmp_path,
        clients={client: stub},
        agent_response_suffix=additional_value,
    )

    assert result.returncode != 0
    assert count_rows(result.stdout, f"FAIL client={client}") == 2
    assert "agent invocation failed" in result.stderr


def test_agent_check_rejects_stale_or_hardcoded_source_evidence(
    tmp_path: pathlib.Path,
) -> None:
    """Evidence differing from the fresh source must fail its matrix row."""
    api_key = "stale-evidence-fixture-secret"
    result, calls, artifacts = run_agent_check(
        tmp_path,
        clients={"pi": STALE_PI_STUB},
        api_key=api_key,
    )

    assert result.returncode != 0
    assert "FAIL client=pi model=gemma4 check=weather field=source_timestamp" in result.stdout
    assert 'received="<redacted>"' in result.stdout
    assert api_key not in result.stdout
    assert api_key not in result.stderr
    assert api_key not in calls.read_text()
    assert all(api_key not in path.read_text() for path in artifacts.iterdir())


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
    assert "agent invocation failed" in result.stderr


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
    assert "agent invocation failed" in result.stderr


def test_run_target_prints_start_banner_before_running_the_command(
    tmp_path: pathlib.Path,
) -> None:
    result = subprocess.run(
        ["/usr/bin/bash", "tools/run-target.sh", "demo", "--", "true"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0
    # The start-banner printf leads with a blank line (spacing between
    # chained make targets), then a full-width separator rule, so stdout's
    # line 0 is empty, line 1 is the separator, and the banner is line 2.
    lines = result.stdout.splitlines()
    assert lines[0] == ""
    assert "-" * 20 in lines[1]
    assert "demo" in lines[2]


def test_run_target_prints_ok_end_banner_on_success(tmp_path: pathlib.Path) -> None:
    result = subprocess.run(
        ["/usr/bin/bash", "tools/run-target.sh", "demo", "--", "true"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0
    assert "demo" in result.stdout
    assert "ok" in result.stdout


def test_run_target_prints_failed_end_banner_and_propagates_exit_status(
    tmp_path: pathlib.Path,
) -> None:
    result = subprocess.run(
        ["/usr/bin/bash", "tools/run-target.sh", "demo", "--", "bash", "-c", "exit 7"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 7
    assert "failed" in result.stdout
    assert "exit 7" in result.stdout


def test_run_target_runs_the_wrapped_commands_own_output(tmp_path: pathlib.Path) -> None:
    result = subprocess.run(
        ["/usr/bin/bash", "tools/run-target.sh", "demo", "--", "echo", "hello"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0
    assert "hello" in result.stdout


def test_makefile_wraps_every_target_with_run_target_banners() -> None:
    makefile = (ROOT / "Makefile").read_text()
    assert "UNIT = llm-server" not in makefile
    assert "@bash tools/run-target.sh start -- bash scripts/start.sh" in makefile
    assert "@bash tools/run-target.sh stop -- bash scripts/stop.sh" in makefile
    assert "@bash tools/run-target.sh status -- bash scripts/status.sh" in makefile
    assert "@bash tools/run-target.sh logs -- bash scripts/logs.sh" in makefile


def test_makefile_restart_chains_two_recursive_make_calls() -> None:
    makefile = (ROOT / "Makefile").read_text()
    assert "restart:\n\t@$(MAKE) --no-print-directory stop\n\t@$(MAKE) --no-print-directory start\n" in makefile


def test_status_and_logs_scripts_reference_unit_name_and_compose_file_from_lib(tmp_path: pathlib.Path) -> None:
    status_text = (ROOT / "scripts/status.sh").read_text()
    logs_text = (ROOT / "scripts/logs.sh").read_text()
    assert "source" in status_text and "tools/lib.sh" in status_text
    assert "${UNIT_NAME}" in status_text
    assert "$COMPOSE_FILE" in status_text
    assert "source" in logs_text and "tools/lib.sh" in logs_text
    assert "$COMPOSE_FILE" in logs_text


def test_render_unit_mdns_execstartpre_uses_the_configured_health_timeout(
    tmp_path: pathlib.Path,
) -> None:
    """The generated mDNS unit's health poll must track the shared timeout,
    not an independent hardcoded 60 — same requirement wait_for_health()
    (Task 4) exists to satisfy for start.sh."""
    result, config, _ = run_lifecycle_script(
        tmp_path,
        "setup/enable-boot.sh",
        env_overrides={"LLM_ENV_HEALTH_TIMEOUT_SECONDS": "77"},
    )
    mdns_unit = config.parent.parent / "systemd/user/llm-server-mdns.service"

    assert result.returncode == 0, result.stderr
    unit = mdns_unit.read_text()
    assert "-lt 77" in unit
    assert "-lt 60" not in unit


def test_load_server_config_sets_port_api_key_and_host(tmp_path: pathlib.Path) -> None:
    config = tmp_path / "models.yml"
    config.write_text(
        "server:\n  host: 0.0.0.0\n  port: 9001\n  api_key: fixture-key\n"
    )
    script = tmp_path / "probe.sh"
    script.write_text(
        "#!/usr/bin/bash\nset -euo pipefail\n"
        f"source {ROOT / 'tools/lib.sh'}\n"
        "load_server_config\n"
        "printf 'PORT=%s API_KEY=%s HOST=%s\\n' \"$PORT\" \"$API_KEY\" \"$HOST\"\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)

    result = subprocess.run(
        ["/usr/bin/bash", str(script)],
        env=os.environ | {"LLM_ENV_CONFIG": str(config)},
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "PORT=9001 API_KEY=fixture-key HOST=0.0.0.0" in result.stdout


def test_ensure_omniroute_secrets_generates_missing_password(
    tmp_path: pathlib.Path,
) -> None:
    home = tmp_path / "home"
    config_dir = home / ".config" / "llm-env"
    config_dir.mkdir(parents=True)
    config = config_dir / "models.yml"
    config.write_text(
        "version: 1\n"
        "omniroute: {image: i, port: 20128, initial_password: ''}\n"
    )
    script = tmp_path / "run.sh"
    script.write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\n"
        f"source {ROOT / 'tools/lib.sh'}\n"
        "ensure_omniroute_secrets\n"
    )
    script.chmod(0o755)
    env = {**os.environ, "HOME": str(home), "LLM_ENV_CONFIG": str(config)}
    result = subprocess.run(
        ["bash", str(script)], cwd=ROOT, text=True, capture_output=True, env=env, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr
    cfg = yaml.safe_load(config.read_text())
    assert cfg["omniroute"]["initial_password"]


def test_ensure_omniroute_secrets_preserves_existing_values(tmp_path: pathlib.Path) -> None:
    home = tmp_path / "home"
    config_dir = home / ".config" / "llm-env"
    config_dir.mkdir(parents=True)
    config = config_dir / "models.yml"
    config.write_text(
        "version: 1\n"
        "omniroute: {image: i, port: 20128, initial_password: existing-password}\n"
    )
    script = tmp_path / "run.sh"
    script.write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\n"
        f"source {ROOT / 'tools/lib.sh'}\n"
        "ensure_omniroute_secrets\n"
    )
    script.chmod(0o755)
    env = {**os.environ, "HOME": str(home), "LLM_ENV_CONFIG": str(config)}
    result = subprocess.run(
        ["bash", str(script)], cwd=ROOT, text=True, capture_output=True, env=env, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr
    cfg = yaml.safe_load(config.read_text())
    assert cfg["omniroute"]["initial_password"] == "existing-password"


def test_wait_for_health_succeeds_once_curl_reports_healthy(tmp_path: pathlib.Path) -> None:
    commands = tmp_path / "bin"
    commands.mkdir()
    curl = commands / "curl"
    curl.write_text("#!/usr/bin/bash\nexit 0\n")
    curl.chmod(curl.stat().st_mode | stat.S_IXUSR)

    script = tmp_path / "probe.sh"
    script.write_text(
        "#!/usr/bin/bash\nset -euo pipefail\n"
        f"source {ROOT / 'tools/lib.sh'}\n"
        "wait_for_health 8000\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)

    result = subprocess.run(
        ["/usr/bin/bash", str(script)],
        env=os.environ | {"PATH": f"{commands}:/usr/bin:/bin"},
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_wait_for_health_times_out_when_curl_never_succeeds(tmp_path: pathlib.Path) -> None:
    commands = tmp_path / "bin"
    commands.mkdir()
    curl = commands / "curl"
    curl.write_text("#!/usr/bin/bash\nexit 1\n")
    curl.chmod(curl.stat().st_mode | stat.S_IXUSR)

    script = tmp_path / "probe.sh"
    script.write_text(
        "#!/usr/bin/bash\nset -uo pipefail\n"
        f"source {ROOT / 'tools/lib.sh'}\n"
        "wait_for_health 8000\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)

    result = subprocess.run(
        ["/usr/bin/bash", str(script)],
        env=os.environ | {"PATH": f"{commands}:/usr/bin:/bin", "LLM_ENV_HEALTH_TIMEOUT_SECONDS": "2"},
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0


def test_lib_rejects_a_non_numeric_health_timeout(tmp_path: pathlib.Path) -> None:
    script = tmp_path / "probe.sh"
    script.write_text(
        "#!/usr/bin/bash\nset -uo pipefail\n"
        f"source {ROOT / 'tools/lib.sh'}\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)

    result = subprocess.run(
        ["/usr/bin/bash", str(script)],
        env=os.environ | {"LLM_ENV_HEALTH_TIMEOUT_SECONDS": "60; rm -rf /"},
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0
    assert "LLM_ENV_HEALTH_TIMEOUT_SECONDS must be a positive integer" in result.stderr


def test_lib_rejects_a_zero_health_timeout(tmp_path: pathlib.Path) -> None:
    script = tmp_path / "probe.sh"
    script.write_text(
        "#!/usr/bin/bash\nset -uo pipefail\n"
        f"source {ROOT / 'tools/lib.sh'}\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)

    result = subprocess.run(
        ["/usr/bin/bash", str(script)],
        env=os.environ | {"LLM_ENV_HEALTH_TIMEOUT_SECONDS": "0"},
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0
    assert "LLM_ENV_HEALTH_TIMEOUT_SECONDS must be a positive integer" in result.stderr


def test_make_restart_runs_stop_then_start_with_distinct_banners() -> None:
    # `-n` is a dry run: make never executes any recipe line, so there is
    # nothing here for a fake `make` stub to intercept — a stub would only
    # matter for a real (non-`-n`) invocation. Instead, call the real `make`
    # binary directly (unmodified PATH) and rely on GNU make's documented
    # `-n` behavior: it still expands `$(MAKE)` to the make program's own
    # name and prints each nested recipe line it would run, which is enough
    # to prove `restart` triggers two independent `make` invocations (stop,
    # then start) instead of chaining through prerequisites.
    result = subprocess.run(
        ["/usr/bin/make", "--no-print-directory", "-n", "restart"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "make --no-print-directory stop" in result.stdout
    assert "make --no-print-directory start" in result.stdout


def test_check_with_agents_shows_classified_excerpt_not_raw_transcript_on_failure(
    tmp_path: pathlib.Path,
) -> None:
    real_yq = shutil.which("yq")
    real_uv = shutil.which("uv")
    assert real_yq is not None
    assert real_uv is not None

    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(
        '{"type": "message_start"}\n'
        '{"type": "tool_error", "message": "boom-diagnostic-marker"}\n'
    )

    script = tmp_path / "probe.sh"
    script.write_text(
        "#!/usr/bin/bash\nset -uo pipefail\n"
        f"source {ROOT / 'tools/lib.sh'}\n"
        f"classified_json=\"$(llmenv classify-transcript --client pi --transcript {transcript})\"\n"
        "excerpt=\"$(echo \"$classified_json\" | jq -r '.excerpt')\"\n"
        "log_block \"Relevant transcript excerpt\" \"$excerpt\"\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)

    result = subprocess.run(
        ["/usr/bin/bash", str(script)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "boom-diagnostic-marker" in result.stdout
    assert "message_start" not in result.stdout


def test_dev_setup_runs_uv_sync(tmp_path: pathlib.Path) -> None:
    commands = tmp_path / "bin"
    commands.mkdir()
    _mock_dirname(commands)
    calls = tmp_path / "calls"
    uv = commands / "uv"
    uv.write_text("#!/usr/bin/bash\nprintf 'uv %s\\n' \"$*\" >> \"$CALLS\"\n")
    uv.chmod(uv.stat().st_mode | stat.S_IXUSR)

    environment = os.environ | {"CALLS": str(calls), "PATH": f"{commands}:/usr/bin:/bin"}
    result = subprocess.run(
        ["/usr/bin/bash", "setup/dev-setup.sh"],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "uv sync" in calls.read_text()


def test_makefile_dev_setup_chains_after_prerequisites() -> None:
    makefile = (ROOT / "Makefile").read_text()
    assert "dev-setup: prerequisites\n\t@bash tools/run-target.sh dev-setup -- bash setup/dev-setup.sh" in makefile


def test_network_sh_prints_the_firewall_warning_and_the_remote_setup_one_liner(
    tmp_path: pathlib.Path,
) -> None:
    real_yq = shutil.which("yq")
    real_jq = shutil.which("jq")
    real_ip = shutil.which("ip")
    assert real_yq is not None
    assert real_jq is not None
    assert real_ip is not None

    commands = tmp_path / "bin"
    commands.mkdir()
    for name, real in (("yq", real_yq), ("jq", real_jq), ("ip", real_ip)):
        stub = commands / name
        stub.write_text(f"#!/usr/bin/bash\nexec {real} \"$@\"\n")
        stub.chmod(0o755)
    firewall_cmd = commands / "firewall-cmd"
    firewall_cmd.write_text("#!/usr/bin/bash\nexit 1\n")
    firewall_cmd.chmod(0o755)

    home = tmp_path / "home"
    config = home / ".config/llm-env/models.yml"
    config.parent.mkdir(parents=True)
    config.write_text(
        "server:\n"
        "  port: 8000\n"
        "  mdns_name: llm\n"
        "  api_key: fixture-api-key\n"
        "omniroute:\n"
        "  port: 20128\n"
        "  initial_password: fixture-omniroute-password\n"
        "remote_setup:\n"
        "  port: 20130\n"
        "models:\n"
        "  - alias: a\n"
        "    enabled: true\n"
    )

    result = subprocess.run(
        ["/usr/bin/bash", "setup/network.sh"],
        cwd=ROOT,
        env=os.environ
        | {
            "HOME": str(home),
            "LLM_ENV_CONFIG": str(config),
            "PATH": f"{commands}:/usr/bin:/bin",
        },
        stdin=subprocess.DEVNULL,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    combined = result.stdout + result.stderr
    assert (
        "firewalld rules for OmniRoute (port 20128/tcp) and the remote-setup "
        "installer (port 20130/tcp) are not opened automatically" in combined
    )
    assert "sudo firewall-cmd --permanent --add-port=20128/tcp --add-port=20130/tcp" in combined
    assert "the OmniRoute container only binds 127.0.0.1" not in combined
    assert "curl http://" in result.stdout
    assert ":20130/setup.sh | bash" in result.stdout


def test_network_sh_skips_the_llm_server_firewall_prompt_when_disabled(
    tmp_path: pathlib.Path,
) -> None:
    """When llm_server.enabled is false, network.sh must not query or prompt
    for opening the firewall port for the llm-server. The OmniRoute/remote-setup
    warnings and print-endpoints.sh output must be unaffected."""
    real_yq = shutil.which("yq")
    real_jq = shutil.which("jq")
    real_ip = shutil.which("ip")
    assert real_yq is not None
    assert real_jq is not None
    assert real_ip is not None

    commands = tmp_path / "bin"
    commands.mkdir()
    for name, real in (("yq", real_yq), ("jq", real_jq), ("ip", real_ip)):
        stub = commands / name
        stub.write_text(f"#!/usr/bin/bash\nexec {real} \"$@\"\n")
        stub.chmod(0o755)
    # Stub firewall-cmd to track if it's called with the llm-server port
    firewall_cmd = commands / "firewall-cmd"
    firewall_calls_file = tmp_path / "firewall_calls.txt"
    firewall_cmd.write_text(
        f"#!/usr/bin/bash\n"
        f'echo "$@" >> "{firewall_calls_file}"\n'
        f"exit 1\n"
    )
    firewall_cmd.chmod(0o755)

    home = tmp_path / "home"
    config = home / ".config/llm-env/models.yml"
    config.parent.mkdir(parents=True)
    config.write_text(
        "server:\n"
        "  port: 8000\n"
        "  mdns_name: llm\n"
        "  api_key: fixture-api-key\n"
        "llm_server:\n"
        "  enabled: false\n"
        "omniroute:\n"
        "  port: 20128\n"
        "  initial_password: fixture-omniroute-password\n"
        "remote_setup:\n"
        "  port: 20130\n"
        "models:\n"
        "  - alias: a\n"
        "    enabled: true\n"
    )

    result = subprocess.run(
        ["/usr/bin/bash", "setup/network.sh"],
        cwd=ROOT,
        env=os.environ
        | {
            "HOME": str(home),
            "LLM_ENV_CONFIG": str(config),
            "PATH": f"{commands}:/usr/bin:/bin",
        },
        stdin=subprocess.DEVNULL,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    combined = result.stdout + result.stderr

    # Verify that firewall-cmd was NOT called with the llm-server port
    if firewall_calls_file.exists():
        firewall_calls = firewall_calls_file.read_text()
        assert "--query-port=8000/tcp" not in firewall_calls, (
            f"firewall-cmd should not query port 8000/tcp when llm_server.enabled is false"
        )
        assert "--add-port=8000/tcp" not in firewall_calls, (
            f"firewall-cmd should not add port 8000/tcp when llm_server.enabled is false"
        )

    # Verify that the output does not contain the firewall prompt for llm-server
    assert "Open firewall port 8000/tcp for LAN access" not in combined

    # Verify that OmniRoute and remote-setup warnings ARE present
    assert (
        "firewalld rules for OmniRoute (port 20128/tcp) and the remote-setup "
        "installer (port 20130/tcp) are not opened automatically" in combined
    )
    assert "sudo firewall-cmd --permanent --add-port=20128/tcp --add-port=20130/tcp" in combined

    # Verify that print-endpoints.sh output is present
    assert "curl http://" in result.stdout
    assert ":20130/setup.sh | bash" in result.stdout


def test_status_prints_the_same_endpoint_banner_as_network_sh(
    tmp_path: pathlib.Path,
) -> None:
    """`make status` must be as useful as the post-`make start` banner:
    the same Local/Network/mDNS endpoints and credentials, plus the
    remote-agent-setup one-liners in both domain (mDNS) and LAN-IP form."""
    real_yq = shutil.which("yq")
    real_jq = shutil.which("jq")
    real_ip = shutil.which("ip")
    assert real_yq is not None
    assert real_jq is not None
    assert real_ip is not None

    commands = tmp_path / "bin"
    commands.mkdir()
    for name, real in (("yq", real_yq), ("jq", real_jq), ("ip", real_ip)):
        stub = commands / name
        stub.write_text(f"#!/usr/bin/bash\nexec {real} \"$@\"\n")
        stub.chmod(0o755)
    for name in ("systemctl", "podman"):
        stub = commands / name
        stub.write_text("#!/usr/bin/bash\nexit 0\n")
        stub.chmod(0o755)

    home = tmp_path / "home"
    config = home / ".config/llm-env/models.yml"
    config.parent.mkdir(parents=True)
    config.write_text(
        "server:\n"
        "  port: 8000\n"
        "  mdns_name: llm\n"
        "  api_key: fixture-api-key\n"
        "omniroute:\n"
        "  port: 20128\n"
        "  initial_password: fixture-omniroute-password\n"
        "remote_setup:\n"
        "  port: 20130\n"
        "models:\n"
        "  - alias: a\n"
        "    enabled: true\n"
    )

    result = subprocess.run(
        ["/usr/bin/bash", "scripts/status.sh"],
        cwd=ROOT,
        env=os.environ
        | {
            "HOME": str(home),
            "LLM_ENV_CONFIG": str(config),
            "PATH": f"{commands}:/usr/bin:/bin",
        },
        stdin=subprocess.DEVNULL,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "fixture-api-key" in result.stdout
    assert "fixture-omniroute-password" in result.stdout
    assert "mDNS:    http://llm.local:20130" in result.stdout
    assert "curl http://llm.local:20130/setup.sh | bash" in result.stdout
    assert "curl http://" in result.stdout and ":20130/setup.sh | bash" in result.stdout


def test_status_skips_the_endpoint_banner_when_unconfigured(
    tmp_path: pathlib.Path,
) -> None:
    commands = tmp_path / "bin"
    commands.mkdir()
    for name in ("systemctl", "podman"):
        stub = commands / name
        stub.write_text("#!/usr/bin/bash\nexit 0\n")
        stub.chmod(0o755)

    home = tmp_path / "home"
    config = home / ".config/llm-env/models.yml"

    result = subprocess.run(
        ["/usr/bin/bash", "scripts/status.sh"],
        cwd=ROOT,
        env=os.environ
        | {
            "HOME": str(home),
            "LLM_ENV_CONFIG": str(config),
            "PATH": f"{commands}:/usr/bin:/bin",
        },
        stdin=subprocess.DEVNULL,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "run 'make setup' first" in result.stdout + result.stderr
    assert "curl http://" not in result.stdout
