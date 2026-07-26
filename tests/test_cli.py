import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(REPO / "llmenv.py"), *args],
        capture_output=True,
        text=True,
        cwd=REPO,
        check=False,
    )


def write_test_config(tmp_path: Path) -> Path:
    config = tmp_path / "models.yml"
    config.write_text(
        "version: 1\n"
        "server: {host: 0.0.0.0, port: 8000, api_key: k, mdns_name: llm,"
        " sleep_idle_seconds: 300}\n"
        "gpu: {pci_address: '0000:03:00.0', device_name: d, backend: vulkan,"
        " image: i, vram_total_mib: 16304, reserve_mode: auto, reserve_floor_mib: 1024}\n"
        "runtime: {models_max: 1, flash_attn: true, cache_type_k: q8_0,"
        " cache_type_v: q8_0}\n"
        "models:\n"
        "  - {alias: a, label: A, parameters: 1B, quantization: Q4_K_M, enabled: true,"
        " file: a.gguf, url: u, size_bytes: 1, vram_budget: 10%, ctx_size: 4096, n_gpu_layers: 99}\n"
        "  - {alias: b, label: B, parameters: 1B, quantization: Q4_K_M, enabled: false,"
        " file: b.gguf, url: u, size_bytes: 1, vram_budget: 10%, ctx_size: 4096, n_gpu_layers: 99}\n"
    )
    return config


def enabled_aliases(config: Path) -> list[str]:
    import yaml

    return [
        model["alias"]
        for model in yaml.safe_load(config.read_text())["models"]
        if model["enabled"]
    ]


def test_detect_emits_json_with_gpus_key():
    result = run("detect")
    assert result.returncode == 0, result.stderr
    assert "gpus" in json.loads(result.stdout)


def test_resolve_device_matches_pci_from_device_listing(tmp_path):
    listing = tmp_path / "devices.txt"
    listing.write_text(
        "Available devices:\n"
        "  Vulkan0: AMD Radeon RX 9070 XT (RADV GFX1201) (16304 MiB, 16304 MiB free)\n"
        "  Vulkan1: AMD Radeon Graphics (RADV RAPHAEL_MENDOCINO) (512 MiB, 512 MiB free)\n"
    )
    result = run(
        "resolve-device",
        "--device-name",
        "AMD Radeon RX 9070 XT (RADV GFX1201)",
        "--listing-file",
        str(listing),
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["device"] == "Vulkan0"


def test_resolve_device_reports_error_when_absent(tmp_path):
    listing = tmp_path / "devices.txt"
    listing.write_text("Available devices:\n  Vulkan0: Some Other GPU (1024 MiB)\n")
    result = run(
        "resolve-device",
        "--device-name",
        "AMD Radeon RX 9070 XT (RADV GFX1201)",
        "--listing-file",
        str(listing),
    )
    assert result.returncode == 1
    assert "error" in json.loads(result.stdout)


def test_models_list_reports_enabled_flags(tmp_path):
    config = tmp_path / "models.yml"
    config.write_text(
        "version: 1\n"
        "server: {host: 0.0.0.0, port: 8000, api_key: k, mdns_name: llm,"
        " sleep_idle_seconds: 300}\n"
        "gpu: {pci_address: '0000:03:00.0', device_name: d, backend: vulkan,"
        " image: i, vram_total_mib: 16304, reserve_mode: auto, reserve_floor_mib: 1024}\n"
        "runtime: {models_max: 1, flash_attn: true, cache_type_k: q8_0,"
        " cache_type_v: q8_0}\n"
        "models:\n"
        "  - {alias: a, enabled: true, file: a.gguf, url: u, size_bytes: 1,"
        " vram_budget: 10%, ctx_size: 4096, n_gpu_layers: 99}\n"
        "  - {alias: b, enabled: false, file: b.gguf, url: u, size_bytes: 1,"
        " vram_budget: 10%, ctx_size: 4096, n_gpu_layers: 99}\n"
    )
    result = run("models", "list", "--config", str(config))
    assert result.returncode == 0, result.stderr
    models = {m["alias"]: m["enabled"] for m in json.loads(result.stdout)["models"]}
    assert models == {"a": True, "b": False}


def test_models_enable_updates_models_max(tmp_path):
    config = tmp_path / "models.yml"
    config.write_text(
        "version: 1\n"
        "server: {host: 0.0.0.0, port: 8000, api_key: k, mdns_name: llm,"
        " sleep_idle_seconds: 300}\n"
        "gpu: {pci_address: '0000:03:00.0', device_name: d, backend: vulkan,"
        " image: i, vram_total_mib: 16304, reserve_mode: auto, reserve_floor_mib: 1024}\n"
        "runtime: {models_max: 1, flash_attn: true, cache_type_k: q8_0,"
        " cache_type_v: q8_0}\n"
        "models:\n"
        "  - {alias: a, enabled: true, file: a.gguf, url: u, size_bytes: 1,"
        " vram_budget: 10%, ctx_size: 4096, n_gpu_layers: 99}\n"
        "  - {alias: b, enabled: false, file: b.gguf, url: u, size_bytes: 1,"
        " vram_budget: 10%, ctx_size: 4096, n_gpu_layers: 99}\n"
    )
    assert run("models", "enable", "b", "--config", str(config)).returncode == 0
    result = run("models", "list", "--config", str(config))
    assert json.loads(result.stdout)["models_max"] == 2


def test_models_select_replaces_enabled_set(tmp_path):
    config = write_test_config(tmp_path)
    result = run("models", "select", "b", "--config", str(config))
    assert result.returncode == 0
    assert json.loads(result.stdout)["models_max"] == 1
    assert enabled_aliases(config) == ["b"]


def test_list_devices_parses_vulkan_rows(tmp_path):
    listing = tmp_path / "devices.txt"
    listing.write_text(
        "  Vulkan0: GPU A (16304 MiB, 16000 MiB free)\n"
        "  Vulkan1: GPU B (512 MiB, 500 MiB free)\n"
    )
    result = run("list-devices", "--listing-file", str(listing))
    assert json.loads(result.stdout)["devices"] == [
        {"id": "Vulkan0", "name": "GPU A", "total_mib": 16304},
        {"id": "Vulkan1", "name": "GPU B", "total_mib": 512},
    ]


def test_unknown_subcommand_is_usage_error():
    assert run("nonsense").returncode == 2


def test_config_flag_works_before_the_subcommand(tmp_path):
    config = tmp_path / "models.yml"
    result = run("--config", str(config), "init", "--template", "models.yml.example")
    assert result.returncode == 0, result.stderr
    assert config.exists()


def test_config_flag_works_after_the_subcommand(tmp_path):
    config = tmp_path / "models.yml"
    result = run("init", "--config", str(config), "--template", "models.yml.example")
    assert result.returncode == 0, result.stderr
    assert config.exists()


def test_budget_reports_actionable_error_when_pci_address_unset(tmp_path):
    config = tmp_path / "models.yml"
    assert (
        run(
            "init", "--config", str(config), "--template", "models.yml.example"
        ).returncode
        == 0
    )
    result = run("budget", "--config", str(config), "--models-dir", str(tmp_path))
    assert result.returncode == 1
    message = json.loads(result.stdout)["error"]
    assert "gpu.pci_address is not set" in message
    assert "  " not in message
