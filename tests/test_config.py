import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pylib.config import (
    ConfigError,
    enabled_models,
    load_config,
    save_config,
    set_model_enabled,
    sync_models_max,
    validate_config,
)


def make_cfg(**overrides):
    cfg = {
        "version": 1,
        "server": {
            "host": "0.0.0.0",
            "port": 8000,
            "api_key": "testkey",
            "mdns_name": "llm",
            "sleep_idle_seconds": 300,
        },
        "gpu": {
            "pci_address": "0000:03:00.0",
            "device_name": "AMD Radeon RX 9070 XT (RADV GFX1201)",
            "backend": "vulkan",
            "image": "ghcr.io/ggml-org/llama.cpp:server-vulkan",
            "vram_total_mib": 16304,
            "reserve_mode": "auto",
            "reserve_floor_mib": 1024,
        },
        "runtime": {
            "models_max": 1,
            "flash_attn": True,
            "cache_type_k": "q8_0",
            "cache_type_v": "q8_0",
        },
        "models": [
            {
                "alias": "gemma4",
                "enabled": True,
                "file": "gemma-4-12B-it-Q4_K_M.gguf",
                "url": "https://example.invalid/gemma.gguf",
                "size_bytes": 7660000000,
                "vram_budget": "55%",
                "ctx_size": 8192,
                "n_gpu_layers": 99,
            },
            {
                "alias": "ornith",
                "enabled": False,
                "file": "ornith-1.0-9b-Q4_K_M.gguf",
                "url": "https://example.invalid/ornith.gguf",
                "size_bytes": 5600000000,
                "vram_budget": "40%",
                "ctx_size": 8192,
                "n_gpu_layers": 99,
            },
        ],
    }
    cfg.update(overrides)
    return cfg


def test_valid_config_has_no_errors():
    assert validate_config(make_cfg()) == []


def test_missing_top_level_section_is_reported():
    cfg = make_cfg()
    del cfg["gpu"]
    assert "missing required section: gpu" in validate_config(cfg)


def test_duplicate_alias_is_reported():
    cfg = make_cfg()
    cfg["models"][1]["alias"] = "gemma4"
    assert "duplicate model alias: gemma4" in validate_config(cfg)


def test_bad_vram_budget_is_reported():
    cfg = make_cfg()
    cfg["models"][0]["vram_budget"] = "lots"
    errors = validate_config(cfg)
    assert any("vram_budget" in e for e in errors)


def test_enabled_models_filters_disabled():
    aliases = [m["alias"] for m in enabled_models(make_cfg())]
    assert aliases == ["gemma4"]


def test_set_model_enabled_toggles_without_deleting():
    cfg = set_model_enabled(make_cfg(), "ornith", True)
    assert len(cfg["models"]) == 2
    assert [m["alias"] for m in enabled_models(cfg)] == ["gemma4", "ornith"]


def test_set_model_enabled_unknown_alias_raises():
    with pytest.raises(ConfigError):
        set_model_enabled(make_cfg(), "nope", True)


def test_sync_models_max_matches_enabled_count():
    cfg = sync_models_max(make_cfg())
    assert cfg["runtime"]["models_max"] == 1
    cfg = sync_models_max(set_model_enabled(cfg, "ornith", True))
    assert cfg["runtime"]["models_max"] == 2


def test_save_then_load_roundtrip(tmp_path):
    path = tmp_path / "models.yml"
    save_config(make_cfg(), path)
    assert load_config(path) == make_cfg()


def test_load_missing_file_raises_configerror(tmp_path):
    with pytest.raises(ConfigError):
        load_config(tmp_path / "absent.yml")
