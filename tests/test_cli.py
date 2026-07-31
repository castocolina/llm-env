import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))


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
        "runtime: {models_max: 1, parallel_slots: 1, ubatch_size: 512,"
        " flash_attn: true, cache_type_k: q8_0, cache_type_v: q8_0}\n"
        "models:\n"
        "  - {alias: a, label: A, parameters: 1B, quantization: Q4_K_M, enabled: true,"
        " file: a.gguf, url: u, size_bytes: 1, vram_budget: 10%, ctx_size: 4096,"
        " client_max_output_tokens: 4096, n_gpu_layers: 99}\n"
        "  - {alias: b, label: B, parameters: 1B, quantization: Q4_K_M, enabled: false,"
        " file: b.gguf, url: u, size_bytes: 1, vram_budget: 10%, ctx_size: 4096,"
        " client_max_output_tokens: 4096, n_gpu_layers: 99}\n"
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


def test_resolve_device_rejects_duplicate_device_names(tmp_path):
    listing = tmp_path / "devices.txt"
    listing.write_text(
        "  Vulkan0: Shared GPU (16304 MiB)\n"
        "  Vulkan1: Shared GPU (16304 MiB)\n"
    )
    result = run(
        "resolve-device",
        "--device-name",
        "Shared GPU",
        "--listing-file",
        str(listing),
    )
    assert result.returncode == 1
    assert "multiple devices match" in json.loads(result.stdout)["error"]


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
    config = write_test_config(tmp_path)
    result = run("models", "list", "--config", str(config))
    assert result.returncode == 0, result.stderr
    models = {m["alias"]: m["enabled"] for m in json.loads(result.stdout)["models"]}
    assert models == {"a": True, "b": False}


def test_models_enable_preserves_models_max(tmp_path):
    config = write_test_config(tmp_path)
    result = run("models", "enable", "b", "--config", str(config))
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["models_max"] == 1
    assert enabled_aliases(config) == ["a", "b"]


def test_models_rejects_disabling_final_model_without_writing(tmp_path):
    config = write_test_config(tmp_path)
    before = config.read_text()
    result = run("models", "disable", "a", "--config", str(config))
    assert result.returncode == 1
    assert "final enabled model" in json.loads(result.stdout)["error"]
    assert config.read_text() == before


def test_models_select_replaces_enabled_set(tmp_path):
    config = write_test_config(tmp_path)
    result = run("models", "select", "b", "--config", str(config))
    assert result.returncode == 0
    assert json.loads(result.stdout)["models_max"] == 1
    assert enabled_aliases(config) == ["b"]


def test_models_select_accepts_multiple_aliases(tmp_path):
    config = write_test_config(tmp_path)
    result = run("models", "select", "a", "b", "--config", str(config))
    assert result.returncode == 0
    assert json.loads(result.stdout)["models_max"] == 1
    assert enabled_aliases(config) == ["a", "b"]


def test_models_select_persists_reverse_request_order(tmp_path):
    config = write_test_config(tmp_path)

    result = run("models", "select", "b", "a", "--config", str(config))

    assert result.returncode == 0, result.stderr
    assert [model["alias"] for model in yaml.safe_load(config.read_text())["models"]] == [
        "b",
        "a",
    ]
    assert enabled_aliases(config) == ["b", "a"]


def test_models_select_rejects_duplicate_alias_without_writing(tmp_path):
    config = write_test_config(tmp_path)
    before = config.read_bytes()

    result = run("models", "select", "b", "b", "--config", str(config))

    assert result.returncode == 1
    assert "duplicate model alias: b" in json.loads(result.stdout)["error"]
    assert config.read_bytes() == before


def test_models_select_reports_unknown_alias(tmp_path):
    config = write_test_config(tmp_path)
    before = config.read_bytes()
    result = run("models", "select", "missing", "--config", str(config))
    assert result.returncode == 1
    assert "unknown model alias: missing" in json.loads(result.stdout)["error"]
    assert config.read_bytes() == before


def test_list_devices_reports_failed_listing_command():
    result = run("list-devices", "--list-command", "exit 7")
    assert result.returncode == 1
    assert "device listing command failed with exit status 7" in json.loads(result.stdout)[
        "error"
    ]


def test_list_devices_requires_listing_source():
    result = run("list-devices")
    assert result.returncode == 1
    assert "provide --listing-file or --list-command" in json.loads(result.stdout)["error"]


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


def test_default_config_uses_128k_q5_1_runtime(tmp_path):
    config = tmp_path / "models.yml"
    result = run("init", "--config", str(config), "--template", "models.yml.example")

    assert result.returncode == 0, result.stderr
    parsed = yaml.safe_load(config.read_text())
    assert parsed["runtime"]["models_max"] == 1
    assert parsed["runtime"]["cache_type_k"] == "q5_1"
    assert parsed["runtime"]["cache_type_v"] == "q5_1"
    assert [model["ctx_size"] for model in parsed["models"]] == [131072, 131072]
    assert [model["client_max_output_tokens"] for model in parsed["models"]] == [
        8192,
        8192,
    ]


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


def test_malformed_yaml_reports_location_without_source_text(tmp_path: Path):
    config = tmp_path / "models.yml"
    secret = "fixture-yaml-parser-secret"
    config.write_text(f"server:\n  api_key: {secret}: exposed\n")

    result = run("models", "list", "--config", str(config))

    assert result.returncode == 1
    error = json.loads(result.stdout)["error"]
    assert str(config) in error
    assert "mapping values are not allowed here" in error
    assert "line 2" in error
    assert "column" in error
    assert secret not in result.stdout + result.stderr
    assert "Traceback" not in result.stdout + result.stderr


@pytest.mark.parametrize(
    "command",
    [
        ("budget", "--models-dir", "."),
        ("presets", "--models-dir", "/models", "--device", "all", "--output", "presets.ini"),
        ("models", "list"),
        ("models", "select", "b"),
        ("models", "enable", "b"),
        ("models", "disable", "a"),
        ("validate-gguf", "--models-dir", "."),
    ],
)
def test_operational_commands_reject_invalid_concurrency_before_output_or_save(
    tmp_path: Path, command: tuple[str, ...]
):
    config = write_test_config(tmp_path)
    parsed = yaml.safe_load(config.read_text())
    parsed["runtime"]["parallel_slots"] = 2
    config.write_text(yaml.safe_dump(parsed, sort_keys=False))
    before = config.read_bytes()
    output = tmp_path / "presets.ini"
    resolved = tuple(str(output) if value == "presets.ini" else value for value in command)

    result = run(*resolved, "--config", str(config))

    assert result.returncode == 1
    assert "runtime.parallel_slots must be 1" in json.loads(result.stdout)["error"]
    assert config.read_bytes() == before
    assert not output.exists()


def test_migrate_config_writes_once_without_exposing_secrets(tmp_path: Path):
    fixture = REPO / "tests/fixtures/models-v1-pre-feature.yml"
    config = tmp_path / "models.yml"
    config.write_bytes(fixture.read_bytes())
    secret = "fixture-private-migration-key"

    first = run("migrate-config", "--config", str(config))

    assert first.returncode == 0, first.stderr
    assert json.loads(first.stdout)["written"] is True
    assert secret not in first.stdout + first.stderr
    migrated = yaml.safe_load(config.read_text())
    assert migrated["runtime"]["parallel_slots"] == 1
    assert migrated["runtime"]["ubatch_size"] == 512
    assert [model["client_max_output_tokens"] for model in migrated["models"]] == [
        8192,
        4096,
    ]
    assert migrated["server"]["api_key"] == secret
    assert migrated["custom_top_level"] == {"retained": True}
    assert oct(config.stat().st_mode & 0o777) == "0o600"

    stat_before = config.stat()
    second = run("migrate-config", "--config", str(config))

    assert second.returncode == 0, second.stderr
    assert json.loads(second.stdout)["written"] is False
    assert secret not in second.stdout + second.stderr
    stat_after = config.stat()
    assert (stat_after.st_ino, stat_after.st_mtime_ns) == (
        stat_before.st_ino,
        stat_before.st_mtime_ns,
    )


def test_migrate_config_rejects_unsafe_default_without_rewriting(tmp_path: Path):
    config = write_test_config(tmp_path)
    parsed = yaml.safe_load(config.read_text())
    parsed["models"][0].pop("client_max_output_tokens")
    parsed["models"][0]["ctx_size"] = True
    config.write_text(yaml.safe_dump(parsed, sort_keys=False))
    before = config.read_bytes()

    result = run("migrate-config", "--config", str(config))

    assert result.returncode == 1
    assert "ctx_size must be a positive integer" in json.loads(result.stdout)["error"]
    assert config.read_bytes() == before


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("alias", ["a"], "model at index 0 alias must be a non-empty string"),
        ("alias", {"name": "a"}, "model at index 0 alias must be a non-empty string"),
        ("enabled", "true", "model a enabled must be a Boolean"),
        ("enabled", 1, "model a enabled must be a Boolean"),
    ],
)
@pytest.mark.parametrize(
    "command",
    [
        ("migrate-config",),
        ("budget", "--models-dir", "."),
        ("presets", "--models-dir", "/models", "--device", "all", "--output", "OUTPUT"),
        ("models", "list"),
        ("models", "select", "b"),
        ("models", "enable", "b"),
        ("models", "disable", "a"),
        ("validate-gguf", "--models-dir", "."),
    ],
)
def test_config_commands_report_malformed_model_records_without_traceback(
    tmp_path: Path,
    field: str,
    value: object,
    expected: str,
    command: tuple[str, ...],
):
    config = write_test_config(tmp_path)
    parsed = yaml.safe_load(config.read_text())
    parsed["server"]["api_key"] = "fixture-malformed-record-secret"
    parsed["models"][0][field] = value
    config.write_text(yaml.safe_dump(parsed, sort_keys=False))
    before = config.read_bytes()
    output = tmp_path / "presets.ini"
    resolved = tuple(str(output) if value == "OUTPUT" else value for value in command)

    result = run(*resolved, "--config", str(config))

    assert result.returncode == 1
    assert expected in json.loads(result.stdout)["error"]
    assert "Traceback" not in result.stdout + result.stderr
    assert "fixture-malformed-record-secret" not in result.stdout + result.stderr
    assert config.read_bytes() == before
    assert not output.exists()


def test_model_costs_round_weights_up_and_report_cache_components(
    tmp_path, monkeypatch
):
    import llmenv

    model_path = tmp_path / "gemma.gguf"
    model_path.write_bytes(b"x" * (1024 * 1024 + 1))
    cfg = {
        "runtime": {
            "ubatch_size": 512,
            "cache_type_k": "f16",
            "cache_type_v": "f16",
        },
        "models": [
            {
                "alias": "gemma",
                "enabled": True,
                "file": model_path.name,
                "ctx_size": 131072,
            }
        ],
    }
    metadata = {
        "general.architecture": "gemma4",
        "gemma4.block_count": 1,
        "gemma4.attention.head_count_kv": 2,
        "gemma4.attention.key_length": 128,
        "gemma4.attention.value_length": 128,
        "gemma4.attention.sliding_window": 257,
        "gemma4.attention.sliding_window_pattern": [True],
        "gemma4.attention.key_length_swa": 64,
        "gemma4.attention.value_length_swa": 96,
    }
    monkeypatch.setattr(
        llmenv, "read_gguf_header", lambda path: {"metadata": metadata}
    )

    assert llmenv._model_costs(cfg, tmp_path) == [
        {
            "alias": "gemma",
            "weights_mib": 2,
            "full_kv_mib": 0,
            "swa_kv_mib": 1,
            "kv_mib": 1,
        }
    ]


def test_cmd_budget_passes_configured_models_max(tmp_path, monkeypatch):
    import llmenv

    cfg = yaml.safe_load(write_test_config(tmp_path).read_text())
    facts = {
        "compositor_render_node": "/dev/dri/renderD128",
        "gpus": [
            {
                "pci_address": "0000:03:00.0",
                "render_node": "/dev/dri/renderD129",
                "vram_total_mib": 16304,
                "vram_used_mib": 200,
            }
        ],
    }
    captured = {}

    monkeypatch.setattr(llmenv, "load_config", lambda path: cfg)
    monkeypatch.setattr(llmenv, "detect", lambda: facts)
    monkeypatch.setattr(llmenv, "_model_costs", lambda config, path: [{"alias": "a"}])

    def fake_compute_budget(**kwargs):
        captured.update(kwargs)
        return {"feasible": True}

    monkeypatch.setattr(llmenv, "compute_budget", fake_compute_budget)

    result = llmenv.cmd_budget(SimpleNamespace(config="cfg", models_dir="models"))

    assert result == 0
    assert captured["models_max"] == 1
