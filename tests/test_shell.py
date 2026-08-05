"""Regression tests for shell-script lifecycle behavior."""

from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
import stat
import subprocess

import pytest

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


def test_makefile_dispatches_relocated_entrypoints() -> None:
    makefile = (ROOT / "Makefile").read_text()

    assert "@bash scripts/help.sh" in makefile
    assert "@bash setup/setup.sh" in makefile
    assert "@bash setup/setup-local-llm-agents.sh" in makefile
    assert "@bash scripts/check-server.sh" in makefile


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
    real_models_select: bool = False,
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
        "  *' detect') printf '%s\\n' '{\"gpus\":[{\"card\":\"card0\",\"pci_address\":\"0000:03:00.0\",\"vram_total_mib\":16384,\"vram_used_mib\":2048,\"render_node\":\"renderD128\",\"connected_outputs\":[]}]}' ;;\n"
        "  *' models list') printf '%s\\n' '{\"models\":[{\"alias\":\"gemma4\",\"label\":\"Gemma 4\",\"parameters\":\"12B\",\"quantization\":\"Q4_K_M\",\"size_bytes\":7660000000,\"enabled\":true},{\"alias\":\"ornith\",\"label\":\"Ornith\",\"parameters\":\"9B\",\"quantization\":\"Q4_K_M\",\"size_bytes\":5600000000,\"enabled\":false}]}' ;;\n"
        "  *' models select '*)\n"
        "    if [ \"$REAL_MODELS_SELECT\" = 1 ]; then exec \"$REAL_UV\" \"$@\"; fi\n"
        "    \"$REAL_YQ\" -i '.models[] |= (.enabled = (.alias == \"gemma4\" or .alias == \"ornith\")) | .runtime.models_max = 1' \"$CONFIG_PATH_TEST\"\n"
        "    printf '%s\\n' '{\"models_max\":1}' ;;\n"
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
        "REAL_MODELS_SELECT": "1" if real_models_select else "0",
        "REAL_UV": shutil.which("uv") or "uv",
        "REAL_YQ": real_yq,
    }
    result = subprocess.run(
        ["/usr/bin/bash", "setup/setup.sh"],
        cwd=ROOT,
        env=environment,
        input=selection,
        text=True,
        capture_output=True,
        check=False,
    )
    return result, calls, config


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
    assert persisted["runtime"]["models_max"] == 1
    assert [model["enabled"] for model in persisted["models"]] == [True, True]


def test_setup_rejects_invalid_numbered_model_selection_before_download(
    tmp_path: pathlib.Path,
) -> None:
    """Out-of-range model indexes must stop setup before any download starts."""
    result, calls, _ = run_setup_with_numbered_selection(tmp_path, "1\n3\n")

    assert result.returncode != 0
    assert "curl " not in calls.read_text()


def test_reverse_setup_selection_drives_client_model_order(
    tmp_path: pathlib.Path,
) -> None:
    setup_result, calls, config = run_setup_with_numbered_selection(
        tmp_path,
        "1\n2,1\n2\n",
        config_text=VALID_AGENT_SETUP_CONFIG,
        real_models_select=True,
    )

    assert setup_result.returncode == 0, setup_result.stderr
    assert "models select ornith gemma4" in calls.read_text()
    assert [model["alias"] for model in json.loads(
        subprocess.run(
            [shutil.which("yq") or "yq", "-o=json", ".", str(config)],
            text=True,
            capture_output=True,
            check=True,
        ).stdout
    )["models"]] == ["ornith", "gemma4", "old-model"]

    result, _, pi_path, settings_path, opencode_paths, state_path = (
        run_setup_local_llm_agents(tmp_path, config_text=config.read_text())
    )

    assert result.returncode == 0, result.stderr
    pi_provider = json.loads(pi_path.read_text())["providers"]["local-llm-env"]
    assert [model["id"] for model in pi_provider["models"]] == ["ornith", "gemma4"]
    assert json.loads(settings_path.read_text())["enabledModels"] == [
        "local-llm-env/ornith",
        "local-llm-env/gemma4",
    ]
    opencode_provider = json.loads(opencode_paths[2].read_text())["provider"][
        "local-llm-env"
    ]
    assert list(opencode_provider["models"]) == ["ornith", "gemma4"]
    assert json.loads(state_path.read_text())["favorite"][:2] == [
        {"providerID": "local-llm-env", "modelID": "ornith"},
        {"providerID": "local-llm-env", "modelID": "gemma4"},
    ]


def run_benchmark(
    tmp_path: pathlib.Path, benchmark_stdout: str, benchmark_stderr: str = ""
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
        "  vram_total_mib: 16384\n"
        "  benchmark:\n"
        "    vulkan:\n"
        "      pp_tps: null\n"
        "      tg_tps: null\n"
        "      measured_at: null\n"
        "models:\n"
        "  - alias: benchmark\n"
        "    enabled: true\n"
        "    file: benchmark.gguf\n"
        "    size_bytes: 1\n"
    )

    uv = commands / "uv"
    uv.write_text("#!/usr/bin/bash\nexit 0\n")
    uv.chmod(uv.stat().st_mode | stat.S_IXUSR)

    yq = commands / "yq"
    yq.write_text("#!/usr/bin/bash\nexec \"$REAL_YQ\" \"$@\"\n")
    yq.chmod(yq.stat().st_mode | stat.S_IXUSR)

    podman = commands / "podman"
    podman.write_text(
        "#!/usr/bin/bash\n"
        "printf 'podman %s\\n' \"$*\" >> \"$CALLS\"\n"
        "case \"$*\" in\n"
        "  *'help all'*) printf '%s\\n' bench ;;\n"
        "  *' bench '*) printf '%s' \"$BENCHMARK_STDOUT\"; "
        "printf '%s' \"$BENCHMARK_STDERR\" >&2 ;;\n"
        "  *'--list-devices'*) printf '%s\\n' 'Vulkan0: Benchmark GPU (16384 MiB, 16000 MiB free)' ;;\n"
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
    }
    result = subprocess.run(
        ["/usr/bin/bash", "scripts/benchmark.sh"],
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
    assert 'Parsed metrics:\n  {"pp_tps":123.4,"tg_tps":56.7}' in result.stdout
    assert "Benchmark stderr:\n  WARNING: radv" in result.stdout
    assert "Benchmark parser stderr:" not in result.stdout
    assert yq_value(config, ".gpu.backend") == "vulkan"
    assert yq_value(config, ".gpu.image") == "ghcr.io/ggml-org/llama.cpp:server-vulkan"
    assert yq_value(config, ".gpu.benchmark.vulkan.pp_tps") == "123.4"
    assert yq_value(config, ".gpu.benchmark.vulkan.tg_tps") == "56.7"
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
    assert "Vulkan benchmark failure: response parsing" in result.stderr
    assert yq_value(config, ".gpu.backend") == "cpu"
    assert yq_value(config, ".gpu.image") == "ghcr.io/ggml-org/llama.cpp:server"
    assert "podman pull ghcr.io/ggml-org/llama.cpp:server" in calls.read_text()


def run_cleanup_with_stubs(
    tmp_path: pathlib.Path,
) -> tuple[subprocess.CompletedProcess[str], pathlib.Path]:
    """Run cleanup in a temporary home and record every Podman invocation."""
    commands = tmp_path / "bin"
    commands.mkdir()
    calls = tmp_path / "calls"
    calls.touch()

    for name in ("systemctl",):
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


def run_render_unit_with_legacy_rocm_config(
    tmp_path: pathlib.Path,
) -> tuple[subprocess.CompletedProcess[str], pathlib.Path]:
    """Render a manually retained legacy backend config in an isolated home."""
    real_yq = shutil.which("yq")
    assert real_yq is not None

    commands = tmp_path / "bin"
    commands.mkdir()
    home = tmp_path / "home"
    config = tmp_path / "models.yml"
    config.write_text(
        "server:\n"
        "  host: 0.0.0.0\n"
        "  port: 8000\n"
        "  api_key: key\n"
        "  sleep_idle_seconds: 300\n"
        "  start_at_boot: false\n"
        "gpu:\n"
        "  backend: rocm\n"
        "  image: example.invalid/llama:latest\n"
        "  device_name: ''\n"
        "runtime:\n"
        "  models_max: 1\n"
    )

    for name in ("podman", "systemctl", "uv"):
        command = commands / name
        command.write_text("#!/usr/bin/bash\nexit 0\n")
        command.chmod(command.stat().st_mode | stat.S_IXUSR)

    yq = commands / "yq"
    yq.write_text("#!/usr/bin/bash\nexec \"$REAL_YQ\" \"$@\"\n")
    yq.chmod(yq.stat().st_mode | stat.S_IXUSR)

    environment = os.environ | {
        "HOME": str(home),
        "LLM_ENV_CONFIG": str(config),
        "PATH": f"{commands}:/usr/bin:/bin",
        "REAL_YQ": real_yq,
    }
    result = subprocess.run(
        ["/usr/bin/bash", "setup/render-unit.sh"],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    return result, home / ".config/containers/systemd/llm-server.container"


def test_render_unit_never_adds_the_rocm_kernel_device(tmp_path: pathlib.Path) -> None:
    """A hand-edited legacy config must not pass the ROCm kernel device through."""
    result, container = run_render_unit_with_legacy_rocm_config(tmp_path)

    assert result.returncode == 0, result.stderr
    assert "/dev/kfd" not in container.read_text()


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
    render_node: str | None = "renderD128",
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
        "  *' detect'*) printf '%s\\n' \"$DETECTED_GPU\" ;;\n"
        "  *' validate-gguf'*) printf '%s\\n' '{\"results\":[]}' ;;\n"
        "  *' budget '*) printf '%s\\n' '{\"available_mib\":12000,\"required_mib\":10000,\"models_max\":2}'; exit \"$BUDGET_EXIT\" ;;\n"
        "  *' resolve-device'*)\n"
        "    for argument in \"$@\"; do\n"
        "      if [ \"$previous\" = --listing-file ]; then listing_file=\"$argument\"; fi\n"
        "      previous=\"$argument\"\n"
        "    done\n"
        "    [ \"$(cat \"$listing_file\")\" = 'Vulkan7: Selected Radeon (16384 MiB, 16000 MiB free)' ] || exit 65\n"
        "    printf '%s\\n' '{\"device\":\"Vulkan7\"}'; exit \"$RESOLVE_EXIT\" ;;\n"
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
        "INFERENCE_STDOUT": inference_stdout,
        "INFERENCE_STDERR": inference_stderr,
        "INFERENCE_EXIT": str(inference_exit),
        "BUDGET_EXIT": str(budget_exit),
        "RESOLVE_EXIT": str(resolve_exit),
        "DETECTED_GPU": detected_gpu,
    }
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
    result, calls, models_dir = run_check_setup_with_stubs(tmp_path)

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
    first_inference = (
        f"podman run --rm --device /dev/dri -v {models_dir}:/models:ro,z "
        "--entrypoint /app/llama example.invalid/llama:latest cli -m /models/first.gguf "
        "--device Vulkan7 --n-gpu-layers 42 --single-turn --no-show-timings "
        "-p Reply with exactly: ready -n 256"
    )
    second_inference = (
        f"podman run --rm --device /dev/dri -v {models_dir}:/models:ro,z "
        "--entrypoint /app/llama example.invalid/llama:latest cli -m /models/second.gguf "
        "--device Vulkan7 --n-gpu-layers 17 --single-turn --no-show-timings "
        "-p Reply with exactly: ready -n 256"
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


def test_check_setup_prints_complete_static_and_inference_records(
    tmp_path: pathlib.Path,
) -> None:
    """Offline validation must expose complete evidence for every checked command."""
    result, _, _ = run_check_setup_with_stubs(tmp_path)

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


def test_check_setup_redacts_mixed_case_inference_secrets(tmp_path: pathlib.Path) -> None:
    """Raw inference records must redact a secret without lowercasing it first."""
    api_key = "MiXeD-Api-Key"
    result, _, _ = run_check_setup_with_stubs(
        tmp_path,
        api_key=api_key,
        inference_stdout=f"assistant content: {api_key}",
        inference_stderr=f"Authorization: Bearer {api_key}\n",
    )

    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert api_key not in combined
    assert api_key.lower() not in combined
    assert "Inference stdout:\n  assistant content: <redacted>" in result.stdout
    assert "Inference stderr:\n  Authorization: Bearer <redacted>" in result.stdout


def test_check_setup_reports_a_normalized_inference_mismatch(tmp_path: pathlib.Path) -> None:
    """A successful non-ready response must identify a normalized-value mismatch."""
    result, _, _ = run_check_setup_with_stubs(tmp_path, inference_stdout="not ready")

    assert result.returncode != 0
    assert "Parsed result:\n  not ready" in result.stdout
    assert (
        "Verdict: FAIL stage=parsed result "
        "reason=normalized assistant content mismatch expected=ready"
    ) in result.stderr


@pytest.mark.parametrize(
    ("budget_exit", "resolve_exit", "reason"),
    [
        (19, 0, "VRAM budget check failed"),
        (0, 23, "GPU device could not be resolved"),
    ],
)
def test_check_setup_skips_each_enabled_inference_after_prerequisite_failure(
    tmp_path: pathlib.Path,
    budget_exit: int,
    resolve_exit: int,
    reason: str,
) -> None:
    """Failed inference prerequisites must skip every enabled model independently."""
    result, _, _ = run_check_setup_with_stubs(
        tmp_path,
        budget_exit=budget_exit,
        resolve_exit=resolve_exit,
    )

    assert result.returncode != 0
    assert result.stdout.count("Identity: inference ") == 2
    assert result.stderr.count(f"Verdict: SKIP reason={reason}") == 2
    assert "Identity: tooling command uv" in result.stdout
    assert "Identity: GGUF validation" in result.stdout
    assert "Results:" in result.stdout


def run_lifecycle_script(
    tmp_path: pathlib.Path,
    script: str,
    *,
    api_key: str = "existing-key",
    active: bool = False,
    config_mode: int = 0o600,
    parallel_slots: int = 1,
    sampling_temperature: str | None = None,
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
        "runtime:\n"
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
    uv = commands / "uv"
    uv.write_text(
        "#!/usr/bin/bash\n"
        "printf 'uv %s\\n' \"$*\" >> \"$CALLS\"\n"
        "case \"$*\" in *' migrate-config'*) exec \"$REAL_UV\" \"$@\" ;; esac\n"
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
        "REAL_UV": real_uv,
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
            "OPENCODE_VERSION": opencode_version,
            "PI_CODING_AGENT_DIR": str(pi_path.parent),
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
            {"providerID": "local-llm-env", "modelID": "gemma4"},
            {"providerID": "local-llm-env", "modelID": "ornith"},
        ],
        "variant": {},
    }
    assert "close Pi and OpenCode" in result.stdout + result.stderr
    assert "restart Pi and OpenCode" in result.stdout
    assert "fixture-local-api-key" not in result.stdout + result.stderr


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
    assert "fixture-local-api-key" not in result.stdout + result.stderr


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
        {"providerID": "local-llm-env", "modelID": "gemma4"},
        {"providerID": "local-llm-env", "modelID": "ornith"},
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
    assert "fixture-local-api-key" not in result.stdout + result.stderr


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
    assert "fixture-local-api-key" not in failed.stdout + failed.stderr

    repaired, _, _, settings_path, _, state_path = run_setup_local_llm_agents(
        tmp_path, **kwargs
    )

    assert repaired.returncode == 0, repaired.stderr
    assert json.loads(settings_path.read_text())["enabledModels"] == [
        "local-llm-env/gemma4",
        "local-llm-env/ornith",
    ]
    assert json.loads(state_path.read_text())["favorite"][:2] == [
        {"providerID": "local-llm-env", "modelID": "gemma4"},
        {"providerID": "local-llm-env", "modelID": "ornith"},
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
        "baseUrl": "http://127.0.0.1:18123/v1",
        "api": "openai-completions",
        "apiKey": "fixture-local-api-key",
        "compat": {
            "supportsDeveloperRole": False,
            "supportsReasoningEffort": False,
        },
        "models": [
            {"id": "gemma4", "contextWindow": 131072, "maxTokens": 8192},
            {"id": "ornith", "contextWindow": 131072, "maxTokens": 8192},
        ],
    }
    assert not config_json.exists()
    assert not opencode_json.exists()
    assert json.loads(opencode_jsonc.read_text())["provider"]["local-llm-env"] == {
        "npm": "@ai-sdk/openai-compatible",
        "name": "local-llm-env",
        "options": {
            "baseURL": "http://127.0.0.1:18123/v1",
            "apiKey": "fixture-local-api-key",
        },
        "models": {
            "gemma4": {
                "name": "gemma4",
                "limit": {"context": 131072, "output": 8192},
            },
            "ornith": {
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
    assert "fixture-local-api-key" not in result.stdout + result.stderr


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
            "gemma4": {
                "name": "gemma4",
                "limit": {"context": 131072, "output": 8192},
            },
            "ornith": {
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
    assert "fixture-local-api-key" not in result.stdout + result.stderr


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
    assert "fixture-local-api-key" not in result.stdout + result.stderr


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
    assert "fixture-local-api-key" not in result.stdout + result.stderr


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
    assert "fixture-local-api-key" not in result.stdout + result.stderr


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
        "baseUrl": "http://127.0.0.1:18123/v1",
        "api": "openai-completions",
        "apiKey": "fixture-local-api-key",
        "compat": {
            "supportsDeveloperRole": False,
            "supportsReasoningEffort": False,
        },
        "models": [
            {"id": "gemma4", "contextWindow": 131072, "maxTokens": 8192},
            {"id": "ornith", "contextWindow": 131072, "maxTokens": 8192},
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
        "local-llm-env/gemma4",
        "local-llm-env/ornith",
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
    assert "fixture-local-api-key" not in result.stdout + result.stderr


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
    assert "fixture-local-api-key" not in result.stdout + result.stderr


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
        (
            VALID_AGENT_SETUP_CONFIG.replace(
                "api_key: fixture-local-api-key", "api_key: ''"
            ),
            0,
            None,
        ),
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
    assert "fixture-local-api-key" not in result.stdout + result.stderr


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
    assert "fixture-local-api-key" not in result.stdout + result.stderr


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
    assert "fixture-local-api-key" not in result.stdout + result.stderr


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


def run_log_file_excerpt(
    tmp_path: pathlib.Path,
    arguments: tuple[str, ...],
    *,
    api_key: str = "fixture-stream-secret",
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
            "_redact_stream() { cat >/dev/null; return 17; }\n"
            if fail_redaction
            else ""
        )
        + ("head() { return 17; }\n" if fail_consumer else "")
        + 'log_file_excerpt "$@"\n'
    )
    return subprocess.run(
        ["/usr/bin/bash", str(helper), *arguments],
        cwd=ROOT,
        env=os.environ
        | {"LLM_ENV_CONFIG": str(config), "TEST_REPO_DIR": str(ROOT)},
        text=True,
        capture_output=True,
        check=False,
    )


def test_log_file_excerpt_redacts_before_bounding_and_drains_large_input(
    tmp_path: pathlib.Path,
) -> None:
    api_key = "fixture-stream-secret"
    source = tmp_path / "client stderr.txt"
    source.write_bytes(api_key.encode() + b"ab" + b"x" * (1024 * 1024))

    result = run_log_file_excerpt(
        tmp_path,
        ("Client stderr", str(source), "12"),
        api_key=api_key,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "Client stderr:\n  <redacted>ab\n"
    assert api_key not in result.stdout
    assert api_key not in result.stderr


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


def test_log_file_excerpt_redacts_the_label_and_indents_each_emitted_line(
    tmp_path: pathlib.Path,
) -> None:
    source = tmp_path / "diagnostic.txt"
    source.write_text("first\nsecond")

    result = run_log_file_excerpt(
        tmp_path,
        ("Label fixture-stream-secret", str(source), "8"),
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "Label <redacted>:\n  first\n  se\n"
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


def test_log_file_excerpt_normalizes_consumer_failure_to_status_one(
    tmp_path: pathlib.Path,
) -> None:
    source = tmp_path / "diagnostic.txt"
    source.write_text("content")

    result = run_log_file_excerpt(
        tmp_path,
        ("Diagnostic", str(source), "12"),
        fail_consumer=True,
    )

    assert result.returncode == 1


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


def test_diagnostic_helpers_redact_api_keys_and_bearer_headers(
    tmp_path: pathlib.Path,
) -> None:
    """Displayed diagnostics must not expose API keys or bearer-header values."""
    api_key = "Bearer"
    bearer_token = "distinct-bearer-token"
    result, artifact, _ = run_diagnostic_helper(
        tmp_path,
        f"{api_key} Authorization: Bearer {bearer_token}",
        api_key=api_key,
    )

    assert result.returncode == 0, result.stderr
    assert api_key not in result.stdout + result.stderr
    assert bearer_token not in result.stdout + result.stderr
    assert "Command: " in result.stdout
    assert "Raw result:" in result.stdout
    assert "Empty result:\n  (empty)" in result.stdout
    assert "<redacted>" in result.stdout
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


def test_diagnostic_helper_treats_api_keys_as_fixed_text(
    tmp_path: pathlib.Path,
) -> None:
    """A regex metacharacter in an API key must not prevent its redaction."""
    api_key = "fixture+secret"
    result, artifact, _ = run_diagnostic_helper(
        tmp_path,
        api_key,
        api_key=api_key,
    )

    assert result.returncode == 0, result.stderr
    assert api_key not in result.stdout + result.stderr
    assert "<redacted>" in result.stdout
    assert not artifact.exists()


def test_diagnostic_helper_keeps_only_private_redacted_artifacts(
    tmp_path: pathlib.Path,
) -> None:
    """Retained diagnostics must be private and redact the configured API key."""
    result, artifact, _ = run_diagnostic_helper(
        tmp_path, "fixture-secret", keep=True
    )

    assert result.returncode == 0, result.stderr
    assert artifact.is_dir()
    assert stat.S_IMODE(artifact.stat().st_mode) == 0o700
    assert str(artifact) in result.stdout
    retained_files = [path for path in artifact.rglob("*") if path.is_file()]
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in retained_files)
    assert "fixture-secret" not in "".join(path.read_text() for path in retained_files)


def test_diagnostic_helper_discards_artifacts_when_traversal_fails(
    tmp_path: pathlib.Path,
) -> None:
    """An unreadable nested directory must fail without leaking or retaining data."""
    bearer_token = "traversal-bearer-token"
    result, artifact, temporary_directory = run_diagnostic_helper(
        tmp_path,
        f"Authorization: Bearer {bearer_token}",
        keep=True,
        unreadable_nested=True,
    )

    assert result.returncode != 0
    assert bearer_token not in result.stdout + result.stderr
    assert "Diagnostics retained:" not in result.stdout
    assert not artifact.exists()
    assert not any(temporary_directory.iterdir())


def test_diagnostic_helper_discards_unreadable_regular_files_without_path_leaks(
    tmp_path: pathlib.Path,
) -> None:
    """An unreadable regular file must not expose paths or leave its file list."""
    secret = "unreadable-regular-file-secret"
    result, artifact, temporary_directory = run_diagnostic_helper(
        tmp_path,
        secret,
        api_key=secret,
        keep=True,
        unreadable_regular_file=True,
    )

    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert secret not in output
    assert "unreadable-diagnostic.txt" not in output
    assert "Diagnostics retained:" not in result.stdout
    assert not artifact.exists()
    assert not list(temporary_directory.glob("llm-env-diagnostic-files.*"))
    assert not any(temporary_directory.iterdir())


def test_start_generates_key_only_when_empty(tmp_path: pathlib.Path) -> None:
    """Starting an unconfigured server must persist a secret without printing it."""
    result, config, calls = run_lifecycle_script(
        tmp_path, "scripts/start.sh", api_key="", config_mode=0o644
    )

    assert result.returncode == 0, result.stderr
    assert yq_value(config, ".server.api_key")
    assert stat.S_IMODE(config.stat().st_mode) == 0o600
    assert yq_value(config, ".server.api_key") not in result.stdout
    assert yq_value(config, ".server.api_key") not in result.stderr
    assert "systemctl --user start llm-server.service" in calls.read_text()


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
    tmp_path: pathlib.Path,
    completion_body: dict[str, str],
    *,
    completion_curl_exits: dict[str, int] | None = None,
    completion_responses: dict[str, str] | None = None,
    completion_statuses: dict[str, int] | None = None,
    model_list_body: str = '{"data":[{"id":"gemma4"},{"id":"ornith"}]}',
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
        "  api_key: fixture-secret\n"
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
        "url=\"\"\n"
        "body_file=\"\"\n"
        "data=\"\"\n"
        "auth_conf=\"\"\n"
        "for argument in \"$@\"; do\n"
        "  case \"$argument\" in http://*|https://*) url=\"$argument\" ;; esac\n"
        "  case \"${previous:-}\" in\n"
        "    -o) body_file=\"$argument\" ;;\n"
        "    -K) auth_conf=\"$argument\" ;;\n"
        "    -d|--data-raw) data=\"$argument\" ;;\n"
        "  esac\n"
        "  previous=\"$argument\"\n"
        "done\n"
        "write_response() {\n"
        "  if [ -n \"$body_file\" ]; then printf '%s\\n' \"$1\" > \"$body_file\"; else printf '%s\\n' \"$1\"; fi\n"
        "}\n"
        "case \"$url\" in\n"
        "  */health) write_response '{\"status\":\"ok\"}'; printf '200' ;;\n"
        "  */v1/models) write_response \"$MODEL_LIST_BODY\"; printf '200' ;;\n"
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
        "case \"$1\" in\n"
        "  .)\n"
        "    response=\"$(<\"$2\")\"\n"
        "    case \"$response\" in not-json) printf 'parse error: invalid literal\\n' >&2; exit 4 ;; esac\n"
        "    ;;\n"
        "  -n)\n"
        "    while [ \"$#\" -gt 0 ]; do\n"
        "      if [ \"$1\" = --arg ] && [ \"${2:-}\" = m ]; then\n"
        "        printf '{\\\"model\\\":\\\"%s\\\",\\\"messages\\\":[{\\\"role\\\":\\\"user\\\",\\\"content\\\":\\\"Reply with exactly: ready\\\"}],\\\"max_tokens\\\": 256,\\\"stream\\\":false}\\n' \"${3:-}\"\n"
        "        exit 0\n"
        "      fi\n"
        "      shift\n"
        "    done\n"
        "    printf '%s\\n' '{\"model\":\"x\",\"messages\":[{\"role\":\"user\",\"content\":\"x\"}],\"max_tokens\":1}'\n"
        "    ;;\n"
        "  -r)\n"
        "    case \"$2\" in\n"
        "      '[.data[].id] | sort | join(\",\")')\n"
        "        response=\"$(cat)\"\n"
        "        case \"$response\" in not-json) printf 'parse error: invalid literal\\n' >&2; exit 4 ;; esac\n"
        "        printf '%s\\n' 'gemma4,ornith'\n"
        "        ;;\n"
        "      '.choices?[0]?.message?.content? // empty')\n"
        "        response=\"$(cat)\"\n"
        "        case \"$response\" in *\\\"content\\\":*) ;; *) exit 0 ;; esac\n"
        "        content=\"${response#*\\\"content\\\":}\"\n"
        "        content=\"${content#\\\"}\"\n"
        "        content=\"${content%%\\\"*}\"\n"
        "        printf '%b\\n' \"$content\"\n"
        "        ;;\n"
        "      '.choices?[0]?.message?.reasoning_content? // empty') exit 0 ;;\n"
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
        "  '.server.api_key') printf '%s\\n' fixture-secret ;;\n"
        "  '[.models[] | select(.enabled) | .alias] | sort | join(\",\")')\n"
        "    printf '%s\\n' 'gemma4,ornith'\n"
        "    ;;\n"
        "  '.models[] | select(.enabled) | .alias') printf '%s\\n' gemma4 ornith ;;\n"
        "  *) exit 64 ;;\n"
        "esac\n"
    )
    yq.chmod(yq.stat().st_mode | stat.S_IXUSR)

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
    environment = os.environ | {
        "CALLS": str(calls),
        "GEMMA4_CURL_EXIT": str(completion_curl_exits.get("gemma4", 0)),
        "GEMMA4_RESPONSE": gemma4_response,
        "GEMMA4_STATUS": str(completion_statuses.get("gemma4", 200)),
        "HOME": str(tmp_path / "home"),
        "LLM_ENV_CONFIG": str(config),
        "MODEL_LIST_BODY": model_list_body,
        "ORNITH_CURL_EXIT": str(completion_curl_exits.get("ornith", 0)),
        "ORNITH_RESPONSE": ornith_response,
        "ORNITH_STATUS": str(completion_statuses.get("ornith", 200)),
        "PATH": f"{commands}:/usr/bin:/bin",
    }
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
    assert "max_tokens: 256, stream: false" in (ROOT / "scripts/check-server.sh").read_text()


def test_check_server_prints_redacted_request_response_and_curl_template(
    tmp_path: pathlib.Path,
) -> None:
    """Successful API checks must show complete, redacted diagnostic records."""
    result, _ = run_check_server(tmp_path, {"gemma4": "ready", "ornith": "ready"})
    combined = result.stdout + result.stderr

    assert result.returncode == 0, result.stderr
    assert "Command: curl --silent --show-error" in result.stdout
    assert "Authorization: Bearer <redacted>" in result.stdout
    assert '"content":"Reply with exactly: ready"' in result.stdout
    assert '"max_tokens": 256' in result.stdout
    assert "HTTP response:" in result.stdout
    assert "HTTP stderr:" not in result.stdout
    assert "Response parsing stderr:\n  (empty)" not in result.stdout
    assert "Assistant content:\n  ready" in result.stdout
    assert "Expectation:\n  normalized assistant content: ready" in result.stdout
    assert "Verdict: PASS" in result.stdout
    assert "fixture-secret" not in combined
    assert combined.count("Request payload:") == 2
    assert 'Request payload:\n  {"model":"x"' not in combined
    assert 'Request payload:\n  {"model":"gemma4"' not in combined
    assert 'Request payload:\n  {"model":"ornith"' not in combined


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
    assert "Response parsing stderr:\n  parse error: invalid literal" in result.stdout


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
    assert "Response parsing stderr:\n  parse error: invalid literal" in result.stdout


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
    agent_parser_stderr: str = "",
    fenced_parser_stderr: str = "",
    source_parser_stderr: str = "",
    tee_exit: int = 0,
    fail_pi_configuration: bool = False,
) -> tuple[subprocess.CompletedProcess[str], pathlib.Path, pathlib.Path]:
    """Run the opt-in check with an isolated client path and fake APIs."""
    real_jq = shutil.which("jq")
    real_yq = shutil.which("yq")
    real_date = shutil.which("date")
    real_tee = shutil.which("tee")
    assert real_jq is not None
    assert real_yq is not None
    assert real_date is not None
    assert real_tee is not None

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
    config = tmp_path / "models.yml"
    config.write_text(
        "server:\n"
        "  port: 8000\n"
        f"  api_key: {api_key}\n"
    )

    _mock_dirname(commands)
    for name, executable in {
        "chmod": "/usr/bin/chmod",
        "date": real_date,
        "find": "/usr/bin/find",
        "mkdir": "/usr/bin/mkdir",
        "mktemp": "/usr/bin/mktemp",
        "mv": "/usr/bin/mv",
        "rm": "/usr/bin/rm",
        "sed": "/usr/bin/sed",
        "yq": real_yq,
    }.items():
        command = commands / name
        command.write_text(f"#!/usr/bin/bash\nexec {executable!s} \"$@\"\n")
        command.chmod(command.stat().st_mode | stat.S_IXUSR)

    jq = commands / "jq"
    jq.write_text(
        "#!/usr/bin/bash\n"
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

    tee = commands / "tee"
    tee.write_text(
        "#!/usr/bin/bash\n"
        '"$REAL_TEE" "$@"\n'
        'exit "$AGENT_CHECK_TEE_EXIT"\n'
    )
    tee.chmod(tee.stat().st_mode | stat.S_IXUSR)

    curl = commands / "curl"
    curl.write_text(
        "#!/usr/bin/bash\n"
        "printf 'curl %s\\n' \"$*\" >> \"$CALLS\"\n"
        "url=\"${!#}\"\n"
        "case \"$url\" in\n"
        "  */v1/models) printf '%s\\n' \"$AGENT_CHECK_MODELS\" ;;\n"
        "  https://api.open-meteo.com/*)\n"
        "    if [ -n \"$AGENT_CHECK_WEATHER_BODY\" ]; then\n"
        "      printf '%s\\n' \"$AGENT_CHECK_WEATHER_BODY\"\n"
        "    else\n"
        "      count=$(< \"$AGENT_CHECK_SOURCE_COUNTER\")\n"
        "      count=$((count + 1))\n"
        "      printf '%s\\n' \"$count\" > \"$AGENT_CHECK_SOURCE_COUNTER\"\n"
        "      printf -v seconds '%02d' \"$count\"\n"
        "      jq -cn --arg seconds \"$seconds\" '{current:{time:(\"2026-07-27T13:00:\" + $seconds),temperature_2m:16.3,weather_code:3}}'\n"
        "    fi\n"
        "    ;;\n"
        "  https://open.er-api.com/*)\n"
        "    if [ -n \"$AGENT_CHECK_FX_BODY\" ]; then\n"
        "      printf '%s\\n' \"$AGENT_CHECK_FX_BODY\"\n"
        "    else\n"
        "      count=$(< \"$AGENT_CHECK_SOURCE_COUNTER\")\n"
        "      count=$((count + 1))\n"
        "      printf '%s\\n' \"$count\" > \"$AGENT_CHECK_SOURCE_COUNTER\"\n"
        "      printf -v seconds '%02d' \"$count\"\n"
        "      jq -cn --arg seconds \"$seconds\" '{time_last_update_utc:(\"2026-07-27T00:00:\" + $seconds),rates:{CLP:946.527902}}'\n"
        "    fi\n"
        "    ;;\n"
        "  *) exit 64 ;;\n"
        "esac\n"
    )
    curl.chmod(curl.stat().st_mode | stat.S_IXUSR)

    for name, body in clients.items():
        command = commands / name
        command.write_text(body)
        command.chmod(command.stat().st_mode | stat.S_IXUSR)

    aliases = model_aliases if model_aliases is not None else (model_alias,)
    environment = os.environ | {
        "AGENT_CHECK_MODELS": json.dumps({"data": [{"id": alias} for alias in aliases]}),
        "AGENT_CHECK_WEATHER_BODY": weather_body
        if weather_body is not None
        else "",
        "AGENT_CHECK_FX_BODY": fx_body
        if fx_body is not None
        else "",
        "AGENT_CHECK_RESPONSE_PREFIX": agent_response_prefix,
        "AGENT_CHECK_RESPONSE_SUFFIX": agent_response_suffix,
        "AGENT_CHECK_SOURCE_TIMESTAMP": agent_source_timestamp or "",
        "AGENT_CHECK_WEATHER_EVIDENCE_OVERRIDE": agent_weather_evidence_override
        or "{}",
        "AGENT_CHECK_FX_EVIDENCE_OVERRIDE": agent_fx_evidence_override or "{}",
        "AGENT_CHECK_CLIENT_STDERR": agent_client_stderr,
        "AGENT_CHECK_PARSER_STDERR": agent_parser_stderr,
        "AGENT_CHECK_FENCED_PARSER_STDERR": fenced_parser_stderr,
        "AGENT_CHECK_SOURCE_PARSER_STDERR": source_parser_stderr,
        "AGENT_CHECK_TEE_EXIT": str(tee_exit),
        "ARTIFACTS": str(artifacts),
        "AGENT_CHECK_SOURCE_COUNTER": str(source_counter),
        "CALLS": str(calls),
        "HOME": str(tmp_path / "home"),
        "LLM_ENV_CONFIG": str(config),
        "PATH": str(commands),
        "TMPDIR": str(diagnostic_tmpdir),
        "XDG_CONFIG_HOME": str(tmp_path / "host-xdg-config"),
        "REAL_JQ": real_jq,
        "REAL_TEE": real_tee,
    }
    if keep_artifacts:
        environment["LLM_ENV_KEEP_CHECK_ARTIFACTS"] = "1"
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
        "  Model: gemma4\n  Tools: bash\n  " + credentials[client]
    ) in row
    assert "Command: " in row
    assert "Input:\n  You MUST use bash to execute this exact command verbatim" in row
    assert f"Exit status:\n  {exit_status}" in row
    assert (
        "Expectation:\n  exactly one JSON object whose source URL, canonical source date, "
        "and required source values match the fetched "
    ) in row


VALID_PI_STUB = """#!/usr/bin/bash
printf 'pi %s\\n' "$*" >> "$CALLS"
if [ -n "${AGENT_CHECK_CLIENT_STDERR:-}" ]; then
    printf '%s\n' "$AGENT_CHECK_CLIENT_STDERR" >&2
fi
printf '%s\\n' "$(< "$PI_CODING_AGENT_DIR/models.json")" > "$ARTIFACTS/pi-${BASHPID}.json"
count="$(< "$AGENT_CHECK_SOURCE_COUNTER")"
printf -v seconds '%02d' "$count"
if [[ "$*" == *weather* ]]; then
    evidence="$(jq -cn --arg seconds "$seconds" --arg source_timestamp "$AGENT_CHECK_SOURCE_TIMESTAMP" --argjson evidence_override "$AGENT_CHECK_WEATHER_EVIDENCE_OVERRIDE" '{source_url:"https://api.open-meteo.com/v1/forecast?latitude=-33.4489&longitude=-70.6693&current=temperature_2m,weather_code&timezone=America%2FSantiago",source_timestamp:(if $source_timestamp == "" then ("2026-07-27T13:00:" + $seconds) else $source_timestamp end),temperature_2m:16.3,weather_code:3} | . + $evidence_override')"
else
    evidence="$(jq -cn --arg seconds "$seconds" --arg source_timestamp "$AGENT_CHECK_SOURCE_TIMESTAMP" --argjson evidence_override "$AGENT_CHECK_FX_EVIDENCE_OVERRIDE" '{source_url:"https://open.er-api.com/v6/latest/USD",source_timestamp:(if $source_timestamp == "" then ("2026-07-27T00:00:" + $seconds) else $source_timestamp end),usd_to_clp:946.527902} | . + $evidence_override')"
fi
jq -cn --argjson evidence "$evidence" '{type:"message_end",message:{role:"assistant",content:[{type:"text",text:(env.AGENT_CHECK_RESPONSE_PREFIX + ($evidence | tojson) + env.AGENT_CHECK_RESPONSE_SUFFIX)}]}}'
"""

VALID_OPENCODE_STUB = """#!/usr/bin/bash
printf 'opencode %s xdg=%s\\n' "$*" "${XDG_CONFIG_HOME:-}" >> "$CALLS"
printf '%s\\n' "$(< "$OPENCODE_CONFIG")" > "$ARTIFACTS/opencode-${BASHPID}.jsonc"
count="$(< "$AGENT_CHECK_SOURCE_COUNTER")"
printf -v seconds '%02d' "$count"
if [[ "$*" == *weather* ]]; then
    evidence="$(jq -cn --arg seconds "$seconds" --arg source_timestamp "$AGENT_CHECK_SOURCE_TIMESTAMP" --argjson evidence_override "$AGENT_CHECK_WEATHER_EVIDENCE_OVERRIDE" '{source_url:"https://api.open-meteo.com/v1/forecast?latitude=-33.4489&longitude=-70.6693&current=temperature_2m,weather_code&timezone=America%2FSantiago",source_timestamp:(if $source_timestamp == "" then ("2026-07-27T13:00:" + $seconds) else $source_timestamp end),temperature_2m:16.3,weather_code:3} | . + $evidence_override')"
else
    evidence="$(jq -cn --arg seconds "$seconds" --arg source_timestamp "$AGENT_CHECK_SOURCE_TIMESTAMP" --argjson evidence_override "$AGENT_CHECK_FX_EVIDENCE_OVERRIDE" '{source_url:"https://open.er-api.com/v6/latest/USD",source_timestamp:(if $source_timestamp == "" then ("2026-07-27T00:00:" + $seconds) else $source_timestamp end),usd_to_clp:946.527902} | . + $evidence_override')"
fi
jq -cn --argjson evidence "$evidence" '{type:"text",part:{type:"text",text:(env.AGENT_CHECK_RESPONSE_PREFIX + ($evidence | tojson) + env.AGENT_CHECK_RESPONSE_SUFFIX)}}'
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
count="$(< "$AGENT_CHECK_SOURCE_COUNTER")"
printf -v seconds '%02d' "$count"
if [[ "$*" == *weather* ]]; then
    evidence="$(jq -cn --arg seconds "$seconds" '{source_url:"https://api.open-meteo.com/v1/forecast?latitude=-33.4489&longitude=-70.6693&current=temperature_2m,weather_code&timezone=America%2FSantiago",source_timestamp:("2026-07-27T13:00:" + $seconds),temperature_2m:16.3,weather_code:3}')"
else
    evidence="$(jq -cn --arg seconds "$seconds" '{source_url:"https://open.er-api.com/v6/latest/USD",source_timestamp:("2026-07-27T00:00:" + $seconds),usd_to_clp:946.527902}')"
fi
jq -cn --argjson evidence "$evidence" '{type:"message_end",message:{role:"assistant",content:[{type:"text",text:("```json\\n" + ($evidence | tojson) + "\\n``` trailing")}]}}'
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
    result, _, _ = run_agent_check(
        tmp_path,
        clients={"pi": MALFORMED_FENCED_JSON_PI_STUB},
        fenced_parser_stderr="parse error: Authorization: Bearer test-key-not-a-secret",
    )
    combined = result.stdout + result.stderr

    assert result.returncode != 0
    rows = agent_rows(result.stdout)
    assert len(rows) == 2
    for row in rows:
        assert_common_agent_fields(row, client="pi", exit_status="0")
        assert (
            "Agent parser stderr:\n  parse error: Authorization: Bearer <redacted>"
        ) in row
        assert "Client stderr:" not in row
        assert "Final response:\n  ```json" in row
    assert "test-key-not-a-secret" not in combined
    assert "Verdict: FAIL stage=agent evidence parsing" in result.stderr


def test_agent_check_prints_redacted_transcript_and_client_failure(
    tmp_path: pathlib.Path,
) -> None:
    """Failed client rows must display redacted stderr and continue their matrix."""
    result, _, _ = run_agent_check(tmp_path, clients={"pi": FAILING_PI_STUB})
    combined = result.stdout + result.stderr

    assert result.returncode != 0
    assert "Client stderr:" in result.stdout
    assert "agent transport failed" in result.stdout
    assert "Authorization: Bearer <redacted>" in combined
    assert "test-key-not-a-secret" not in combined
    assert "Verdict: FAIL stage=command exit" in result.stderr
    rows = agent_rows(result.stdout)
    assert len(rows) == 2
    for row in rows:
        assert_common_agent_fields(row, client="pi", exit_status="17")
        assert "Client stderr:\n  Authorization: Bearer <redacted>" in row
        assert "Client JSONL transcript:" not in row
        assert "Agent parser stderr:" not in row
        assert "Final response:" not in row


@pytest.mark.parametrize(
    ("client", "stub"), (("pi", VALID_PI_STUB), ("opencode", VALID_OPENCODE_STUB))
)
def test_agent_check_reports_client_exit_when_transcript_capture_fails(
    tmp_path: pathlib.Path, client: str, stub: str
) -> None:
    result, _, _ = run_agent_check(
        tmp_path,
        clients={client: stub},
        tee_exit=41,
    )

    assert result.returncode != 0
    rows = agent_rows(result.stdout)
    assert len(rows) == 2
    for row in rows:
        assert_common_agent_fields(row, client=client, exit_status="0")
        assert "Client JSONL transcript:\n  {\"type\":" in row
        assert "Client stderr:" not in row
        assert "Agent parser stderr:" not in row
        assert "Final response:" not in row
    assert "Verdict: FAIL stage=transcript capture" in result.stderr


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
        assert "Client JSONL transcript:\n  {\"type\":\"message_end\"" in row
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
    assert "Client JSONL transcript:\n  {\"type\":\"message_end\"" in weather_row
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
    recorded = calls.read_text()
    assert "--no-session" in recorded
    assert "--no-extensions" in recorded
    assert "--no-skills" in recorded
    assert "--no-prompt-templates" in recorded
    assert "--no-context-files" in recorded
    assert "--tools bash" in recorded
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
    recorded = calls.read_text()
    assert "run --format json --model llm-env/gemma4" in recorded
    assert f"xdg={tmp_path / 'host-xdg-config'}" not in recorded
    configs = list(artifacts.glob("opencode-*.jsonc"))
    assert len(configs) == 4
    assert all(api_key not in path.read_text() for path in configs)
    assert all("{env:OPENCODE_API_KEY}" in path.read_text() for path in configs)
    assert all(json.loads(path.read_text())["tools"] == {"*": False, "bash": True} for path in configs)
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
    )

    assert result.returncode == 0, result.stderr
    rows = agent_rows(result.stdout)
    assert len(rows) == 2
    for row in rows:
        assert_common_agent_fields(row, client="pi", exit_status="0")
        assert "Client JSONL transcript:" not in row
        assert "Client stderr:\n  client warning" in row
        assert "Agent parser stderr:\n  parser warning" in row
        assert "Final response:\n  {" in row
        assert "Validated:\n  source_url=https://" in row
        assert "Verdict: PASS" in row


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
    ("weather_override", "fx_override", "expected_field"),
    (
        ('{"source_url":"https://example.invalid/weather"}', None, "source_url"),
        (None, '{"source_url":"https://example.invalid/fx"}', "source_url"),
        ('{"temperature_2m":-999999}', None, "temperature_2m"),
        ('{"weather_code":-999999}', None, "weather_code"),
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
def test_agent_check_prompt_requires_literal_source_evidence(
    tmp_path: pathlib.Path, client: str, stub: str
) -> None:
    result, calls, _ = run_agent_check(tmp_path, clients={client: stub})

    assert result.returncode == 0, result.stderr
    prompts = [line for line in calls.read_text().splitlines() if line.startswith(f"{client} ")]
    assert len(prompts) == 2
    for prompt in prompts:
        assert "You MUST use bash to execute this exact command verbatim" in prompt
        assert "as the only network request:" in prompt
        assert "The URL argument must be copied byte-for-byte" in prompt
        assert "Do not substitute any source, endpoint, proxy, mirror, or query." in prompt
        assert "Return fields only from that command's response." in prompt
        assert "The source_url field must reproduce the literal URL byte-for-byte" in prompt
        assert "Return source_timestamp as ISO-8601." in prompt
        assert "Return exactly one JSON object containing" in prompt
    weather_prompt = next(
        prompt for prompt in prompts if "https://api.open-meteo.com/v1/forecast?" in prompt
    )
    assert (
        "The source_timestamp field must copy the source response's timestamp text "
        "byte-for-byte." in weather_prompt
    )
    assert "Do not convert or normalize its timezone" in weather_prompt
    assert "add an offset" in weather_prompt
    assert "change its date" in weather_prompt
    fx_prompt = next(
        prompt for prompt in prompts if "https://open.er-api.com/v6/latest/USD" in prompt
    )
    assert (
        "The source_timestamp field must convert the source response's exact "
        "time_last_update_utc timestamp to ISO-8601" in fx_prompt
    )
    assert "preserving its UTC instant" in fx_prompt
    assert "UTC timezone (Z or +00:00)" in fx_prompt
    assert "source calendar date" in fx_prompt
    assert "Do not convert it to local time or another timezone" in fx_prompt
    assert "do not change its date" in fx_prompt
    assert any(
        "curl -fsS --max-time 20 -- 'https://api.open-meteo.com/v1/forecast?"
        in prompt
        for prompt in prompts
    )
    assert any(
        "curl -fsS --max-time 20 -- 'https://open.er-api.com/v6/latest/USD'"
        in prompt
        for prompt in prompts
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
