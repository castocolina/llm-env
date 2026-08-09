import copy
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pylib.config as config_module
from pylib.config import (
    ConfigError,
    enabled_models,
    load_config,
    migrate_config,
    save_config,
    set_enabled_models,
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
            "vram_budget_ceiling_pct": 95,
            "vram_budget_ceiling_mib": 16304,
            "vram_budget_ceiling_floor_mib": 10240,
            "benchmark": {
                "vulkan": {
                    "pp_tps": None,
                    "tg_tps": None,
                    "measured_at": None,
                }
            },
        },
        "runtime": {
            "models_max": 1,
            "parallel_slots": 1,
            "ubatch_size": 512,
            "flash_attn": True,
            "cache_type_k": "q8_0",
            "cache_type_v": "q8_0",
        },
        "resources": {
            "llm_server": {
                "cpus": 0,
                "memory_mib": 0,
                "memory_ceiling_pct": 46,
                "memory_ceiling_floor_mib": 10240,
            },
            "omniroute": {"cpus": 1, "memory_mib": 1024},
        },
        "omniroute": {
            "image": "docker.io/diegosouzapw/omniroute:latest",
            "port": 20128,
            "initial_password": "test-password",
        },
        "models": [
            {
                "alias": "gemma4",
                "label": "Gemma 4 Instruct",
                "parameters": "12B",
                "quantization": "Q4_K_M",
                "enabled": True,
                "file": "gemma-4-12B-it-Q4_K_M.gguf",
                "url": "https://example.invalid/gemma.gguf",
                "size_bytes": 7660000000,
                "vram_budget": "55%",
                "ctx_size": 8192,
                "client_max_output_tokens": 8192,
                "n_gpu_layers": 99,
            },
            {
                "alias": "ornith",
                "label": "Ornith 1.0",
                "parameters": "9B",
                "quantization": "Q4_K_M",
                "enabled": False,
                "file": "ornith-1.0-9b-Q4_K_M.gguf",
                "url": "https://example.invalid/ornith.gguf",
                "size_bytes": 5600000000,
                "vram_budget": "40%",
                "ctx_size": 8192,
                "client_max_output_tokens": 8192,
                "n_gpu_layers": 99,
            },
        ],
    }
    cfg.update(overrides)
    return cfg


def test_valid_config_has_no_errors():
    assert validate_config(make_cfg()) == []


@pytest.mark.parametrize(
    "sampling",
    [
        {},
        {"temperature": 0},
        {"temperature": 1.0},
        {"top_p": 0},
        {"top_p": 0.95},
        {"top_p": 1},
        {"top_k": 0},
        {"top_k": 64},
        {"repeat_penalty": 1},
        {"repeat_penalty": 1.1},
        {
            "temperature": 1.0,
            "top_p": 0.95,
            "top_k": 64,
            "repeat_penalty": 1.1,
        },
    ],
)
def test_config_accepts_optional_sampling_fields(sampling):
    cfg = make_cfg()
    cfg["models"][0]["sampling"] = sampling

    assert validate_config(cfg) == []


@pytest.mark.parametrize(
    ("sampling", "expected"),
    [
        (None, "model gemma4 sampling must be a mapping"),
        ([], "model gemma4 sampling must be a mapping"),
        ("default", "model gemma4 sampling must be a mapping"),
        (1, "model gemma4 sampling must be a mapping"),
        (True, "model gemma4 sampling must be a mapping"),
        (
            {"typical_p": 1.0},
            "model gemma4 sampling.typical_p is not a supported field",
        ),
        (
            {1: 1.0},
            "model gemma4 sampling.1 is not a supported field",
        ),
        (
            {"temperature": "1.0"},
            "model gemma4 sampling.temperature must be a finite non-negative number",
        ),
        (
            {"temperature": -0.1},
            "model gemma4 sampling.temperature must be a finite non-negative number",
        ),
        (
            {"temperature": True},
            "model gemma4 sampling.temperature must be a finite non-negative number",
        ),
        (
            {"temperature": float("inf")},
            "model gemma4 sampling.temperature must be a finite non-negative number",
        ),
        (
            {"top_p": -0.1},
            "model gemma4 sampling.top_p must be a finite number between 0 and 1 inclusive",
        ),
        (
            {"top_p": 1.1},
            "model gemma4 sampling.top_p must be a finite number between 0 and 1 inclusive",
        ),
        (
            {"top_p": float("nan")},
            "model gemma4 sampling.top_p must be a finite number between 0 and 1 inclusive",
        ),
        (
            {"top_p": None},
            "model gemma4 sampling.top_p must be a finite number between 0 and 1 inclusive",
        ),
        (
            {"top_p": False},
            "model gemma4 sampling.top_p must be a finite number between 0 and 1 inclusive",
        ),
        (
            {"top_k": -1},
            "model gemma4 sampling.top_k must be a non-negative integer and not a Boolean",
        ),
        (
            {"top_k": 1.0},
            "model gemma4 sampling.top_k must be a non-negative integer and not a Boolean",
        ),
        (
            {"top_k": False},
            "model gemma4 sampling.top_k must be a non-negative integer and not a Boolean",
        ),
        (
            {"top_k": "64"},
            "model gemma4 sampling.top_k must be a non-negative integer and not a Boolean",
        ),
        (
            {"repeat_penalty": 0},
            "model gemma4 sampling.repeat_penalty must be a finite number greater than 0",
        ),
        (
            {"repeat_penalty": -0.1},
            "model gemma4 sampling.repeat_penalty must be a finite number greater than 0",
        ),
        (
            {"repeat_penalty": False},
            "model gemma4 sampling.repeat_penalty must be a finite number greater than 0",
        ),
        (
            {"repeat_penalty": float("-inf")},
            "model gemma4 sampling.repeat_penalty must be a finite number greater than 0",
        ),
        (
            {"repeat_penalty": "1.1"},
            "model gemma4 sampling.repeat_penalty must be a finite number greater than 0",
        ),
    ],
)
def test_config_rejects_invalid_sampling_with_actionable_error(sampling, expected):
    cfg = make_cfg()
    cfg["models"][0]["sampling"] = sampling

    assert validate_config(cfg) == [expected]


def test_vulkan_only_config_removes_legacy_rocm_benchmark():
    """Migration must discard obsolete ROCm measurements from existing configs."""
    cfg = make_cfg()
    cfg["gpu"]["benchmark"]["rocm"] = {
        "pp_tps": 1,
        "tg_tps": 1,
        "measured_at": "old",
    }

    migrate_config = getattr(config_module, "migrate_config", None)
    assert migrate_config is not None, "configuration migration is missing"
    migrated = migrate_config(cfg)

    assert set(migrated["gpu"]["benchmark"]) == {"vulkan"}


def test_load_config_migrates_legacy_rocm_benchmark_from_yaml(tmp_path):
    """Loading a legacy YAML file must discard only its ROCm measurement."""
    path = tmp_path / "models.yml"
    path.write_text(
        "gpu:\n"
        "  benchmark:\n"
        "    rocm:\n"
        "      pp_tps: 1\n"
        "      tg_tps: 1\n"
        "      measured_at: old\n"
        "    vulkan:\n"
        "      pp_tps: 2\n"
        "      tg_tps: 2\n"
        "      measured_at: current\n"
    )

    assert load_config(path)["gpu"]["benchmark"] == {
        "vulkan": {"pp_tps": 2, "tg_tps": 2, "measured_at": "current"}
    }


def test_pre_feature_config_migration_is_additive_and_idempotent():
    fixture = Path(__file__).parent / "fixtures/models-v1-pre-feature.yml"
    original = yaml.safe_load(fixture.read_text())
    migrate_config = getattr(config_module, "migrate_config", None)
    require_valid_config = getattr(config_module, "require_valid_config", None)

    assert migrate_config is not None, "configuration migration is missing"
    assert require_valid_config is not None, "shared configuration enforcement is missing"
    migrated = migrate_config(copy.deepcopy(original))

    assert migrated["runtime"]["parallel_slots"] == 1
    assert migrated["runtime"]["ubatch_size"] == 512
    assert [model["client_max_output_tokens"] for model in migrated["models"]] == [
        8192,
        4096,
    ]
    assert migrated["server"]["api_key"] == "fixture-private-migration-key"
    assert migrated["server"]["custom_server_field"] == "preserved"
    assert migrated["models"][0]["custom_model_field"] == "preserved"
    assert migrated["custom_top_level"] == {"retained": True}
    assert all("sampling" not in model for model in migrated["models"])
    assert migrate_config(copy.deepcopy(migrated)) == migrated
    assert require_valid_config(migrated) is migrated


def test_migration_reports_all_actionable_errors_when_defaults_are_not_safe():
    cfg = make_cfg()
    del cfg["runtime"]["parallel_slots"]
    del cfg["models"][0]["client_max_output_tokens"]
    cfg["models"][0]["ctx_size"] = True
    require_valid_config = getattr(config_module, "require_valid_config", None)

    assert require_valid_config is not None, "shared configuration enforcement is missing"
    migrated = config_module.migrate_config(cfg)

    with pytest.raises(ConfigError) as error:
        require_valid_config(migrated)
    assert "model gemma4 missing key: client_max_output_tokens" in str(error.value)
    assert "model gemma4 ctx_size must be a positive integer" in str(error.value)
    assert "model gemma4 client_max_output_tokens must be a positive integer" in str(
        error.value
    )


def test_shared_validation_reports_non_mapping_model_records():
    cfg = make_cfg()
    cfg["models"].append("invalid-model-record")
    require_valid_config = getattr(config_module, "require_valid_config", None)

    assert require_valid_config is not None, "shared configuration enforcement is missing"
    with pytest.raises(ConfigError, match="each model must be a mapping"):
        require_valid_config(cfg)


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("alias", ["gemma4"], "model at index 0 alias must be a non-empty string"),
        ("alias", {"name": "gemma4"}, "model at index 0 alias must be a non-empty string"),
        ("enabled", "true", "model gemma4 enabled must be a Boolean"),
        ("enabled", 1, "model gemma4 enabled must be a Boolean"),
    ],
)
def test_validation_is_total_for_malformed_alias_and_enabled_values(
    field, value, expected
):
    cfg = make_cfg()
    cfg["models"][0][field] = value

    assert expected in validate_config(cfg)


def test_enabled_models_requires_mapping_records_with_literal_true():
    cfg = make_cfg()
    cfg["models"].extend(
        [
            "not-a-mapping",
            {"alias": "string-enabled", "enabled": "true"},
            {"alias": "integer-enabled", "enabled": 1},
        ]
    )

    assert [model["alias"] for model in enabled_models(cfg)] == ["gemma4"]


def test_config_save_and_load_migrate_legacy_rocm_benchmarks(tmp_path):
    """Persisting a legacy config must remove its obsolete benchmark result."""
    cfg = make_cfg()
    cfg["gpu"]["benchmark"]["rocm"] = {
        "pp_tps": 1,
        "tg_tps": 1,
        "measured_at": "old",
    }

    path = tmp_path / "models.yml"
    save_config(cfg, path)

    assert set(load_config(path)["gpu"]["benchmark"]) == {"vulkan"}


def test_vulkan_only_schema_rejects_rocm_backend():
    """A legacy backend must not remain an accepted configuration choice."""
    cfg = make_cfg()
    cfg["gpu"]["backend"] = "rocm"

    assert "gpu.backend must be one of ('vulkan', 'cpu')" in validate_config(cfg)


def test_model_requires_display_metadata():
    cfg = make_cfg()
    del cfg["models"][0]["label"]
    assert "model gemma4 missing key: label" in validate_config(cfg)


def test_config_requires_positive_non_boolean_runtime_and_client_limits():
    cases = [
        ("models_max", 0),
        ("models_max", True),
        ("parallel_slots", False),
        ("ubatch_size", -1),
    ]
    for key, value in cases:
        cfg = make_cfg()
        cfg["runtime"][key] = value
        assert any(key in error for error in validate_config(cfg))

    for key, value in (("ctx_size", True), ("client_max_output_tokens", 0)):
        cfg = make_cfg()
        cfg["models"][0][key] = value
        assert any(key in error for error in validate_config(cfg))


def test_config_enforces_output_residency_and_single_slot_constraints():
    cfg = make_cfg()
    cfg["models"][0]["client_max_output_tokens"] = 8193
    cfg["models"][0]["ctx_size"] = 8192
    assert (
        "model gemma4 client_max_output_tokens must not exceed ctx_size"
        in validate_config(cfg)
    )

    cfg = make_cfg()
    cfg["runtime"]["models_max"] = 2
    assert "runtime.models_max must not exceed enabled model count (1)" in validate_config(
        cfg
    )

    cfg = make_cfg()
    cfg["runtime"]["parallel_slots"] = 2
    assert "runtime.parallel_slots must be 1" in validate_config(cfg)


def test_config_rejects_zero_enabled_models():
    cfg = make_cfg()
    for model in cfg["models"]:
        model["enabled"] = False
    assert "at least one model must be enabled" in validate_config(cfg)


def test_set_enabled_models_replaces_the_enabled_set():
    cfg = set_enabled_models(make_cfg(), ["ornith"])
    assert [m["alias"] for m in enabled_models(cfg)] == ["ornith"]
    assert cfg["runtime"]["models_max"] == 1


def test_set_enabled_models_preserves_requested_order_before_unselected_models():
    cfg = make_cfg()
    for alias in ("third", "fourth"):
        model = copy.deepcopy(cfg["models"][1])
        model["alias"] = alias
        cfg["models"].append(model)

    set_enabled_models(cfg, ["ornith", "gemma4"])

    assert [model["alias"] for model in cfg["models"]] == [
        "ornith",
        "gemma4",
        "third",
        "fourth",
    ]
    assert [model["alias"] for model in enabled_models(cfg)] == ["ornith", "gemma4"]


def test_set_enabled_models_rejects_duplicate_request_without_mutation():
    cfg = make_cfg()
    original = copy.deepcopy(cfg)

    with pytest.raises(ConfigError, match="duplicate model alias: ornith"):
        set_enabled_models(cfg, ["ornith", "ornith"])

    assert cfg == original


def test_set_enabled_models_rejects_unknown_alias():
    with pytest.raises(ConfigError, match="unknown model alias"):
        set_enabled_models(make_cfg(), ["missing"])


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


def test_enabling_model_preserves_residency_limit():
    cfg = sync_models_max(set_model_enabled(make_cfg(), "ornith", True))
    assert [model["alias"] for model in enabled_models(cfg)] == ["gemma4", "ornith"]
    assert cfg["runtime"]["models_max"] == 1


def test_selection_clamps_residency_only_when_enabled_count_shrinks():
    cfg = make_cfg()
    cfg["models"][1]["enabled"] = True
    cfg["runtime"]["models_max"] = 2
    cfg = set_enabled_models(cfg, ["ornith"])
    assert cfg["runtime"]["models_max"] == 1


def test_empty_selection_and_final_disable_are_atomic():
    cfg = make_cfg()
    original = copy.deepcopy(cfg)
    with pytest.raises(ConfigError, match="at least one model"):
        set_enabled_models(cfg, [])
    assert cfg == original

    with pytest.raises(ConfigError, match="final enabled model"):
        set_model_enabled(cfg, "gemma4", False)
    assert cfg == original


def test_save_then_load_roundtrip(tmp_path):
    path = tmp_path / "models.yml"
    save_config(make_cfg(), path)
    assert load_config(path) == make_cfg()


def test_sampling_survives_save_load_roundtrip(tmp_path):
    cfg = make_cfg()
    cfg["models"][0]["sampling"] = {
        "temperature": 1.0,
        "top_p": 0.95,
        "top_k": 64,
        "repeat_penalty": 1.1,
    }
    path = tmp_path / "models.yml"

    save_config(cfg, path)

    assert load_config(path) == cfg


def test_load_missing_file_raises_configerror(tmp_path):
    with pytest.raises(ConfigError):
        load_config(tmp_path / "absent.yml")


def test_migrate_config_adds_default_resources_section():
    cfg = make_cfg()
    migrated = config_module.migrate_config(copy.deepcopy(cfg))
    assert migrated["resources"]["llm_server"] == {
        "cpus": 0,
        "memory_mib": 0,
        "memory_ceiling_pct": 46,
        "memory_ceiling_floor_mib": 10240,
    }


def test_migrate_config_preserves_existing_resources_values():
    cfg = make_cfg(resources={"llm_server": {"cpus": 6, "memory_mib": 28672}})
    migrated = config_module.migrate_config(copy.deepcopy(cfg))
    assert migrated["resources"]["llm_server"] == {
        "cpus": 6,
        "memory_mib": 28672,
        "memory_ceiling_pct": 46,
        "memory_ceiling_floor_mib": 10240,
    }


def test_migrate_config_adds_default_memory_ceiling_pct():
    cfg = make_cfg()
    cfg["resources"]["llm_server"].pop("memory_ceiling_pct", None)
    migrated = config_module.migrate_config(copy.deepcopy(cfg))
    assert migrated["resources"]["llm_server"]["memory_ceiling_pct"] == 46


def test_migrate_config_preserves_existing_memory_ceiling_pct():
    cfg = make_cfg(
        resources={
            "llm_server": {
                "cpus": 6,
                "memory_mib": 28672,
                "memory_ceiling_pct": 30,
                "memory_ceiling_floor_mib": 10240,
            }
        }
    )
    migrated = config_module.migrate_config(copy.deepcopy(cfg))
    assert migrated["resources"]["llm_server"]["memory_ceiling_pct"] == 30


@pytest.mark.parametrize("value", [0, -1, 101, float("inf"), "lots", True])
def test_validate_config_rejects_invalid_memory_ceiling_pct(value):
    cfg = make_cfg(
        resources={"llm_server": {"cpus": 0, "memory_mib": 0, "memory_ceiling_pct": value}}
    )
    errors = validate_config(cfg)
    assert any("memory_ceiling_pct" in error for error in errors)


def test_validate_config_accepts_boundary_memory_ceiling_pct():
    cfg = make_cfg(
        resources={"llm_server": {"cpus": 0, "memory_mib": 0, "memory_ceiling_pct": 100}}
    )
    assert validate_config(cfg) == []


def test_migrate_config_adds_default_memory_ceiling_floor_mib():
    cfg = make_cfg()
    cfg["resources"]["llm_server"].pop("memory_ceiling_floor_mib", None)
    migrated = config_module.migrate_config(copy.deepcopy(cfg))
    assert migrated["resources"]["llm_server"]["memory_ceiling_floor_mib"] == 10240


def test_migrate_config_preserves_existing_memory_ceiling_floor_mib():
    cfg = make_cfg(
        resources={
            "llm_server": {
                "cpus": 6,
                "memory_mib": 28672,
                "memory_ceiling_pct": 46,
                "memory_ceiling_floor_mib": 2048,
            }
        }
    )
    migrated = config_module.migrate_config(copy.deepcopy(cfg))
    assert migrated["resources"]["llm_server"]["memory_ceiling_floor_mib"] == 2048


@pytest.mark.parametrize("value", [0, -1, 1.5, "lots", True])
def test_validate_config_rejects_invalid_memory_ceiling_floor_mib(value):
    cfg = make_cfg(
        resources={
            "llm_server": {"cpus": 0, "memory_mib": 0, "memory_ceiling_floor_mib": value}
        }
    )
    errors = validate_config(cfg)
    assert any("memory_ceiling_floor_mib" in error for error in errors)


def test_migrate_config_adds_default_vram_budget_ceiling():
    cfg = make_cfg()
    del cfg["gpu"]["vram_budget_ceiling_pct"]
    del cfg["gpu"]["vram_budget_ceiling_mib"]
    migrated = config_module.migrate_config(copy.deepcopy(cfg))
    assert migrated["gpu"]["vram_budget_ceiling_pct"] == 95
    assert migrated["gpu"]["vram_budget_ceiling_mib"] == cfg["gpu"]["vram_total_mib"]


def test_migrate_config_preserves_existing_vram_budget_ceiling():
    cfg = make_cfg()
    cfg["gpu"]["vram_budget_ceiling_pct"] = 80
    cfg["gpu"]["vram_budget_ceiling_mib"] = 12000
    migrated = config_module.migrate_config(copy.deepcopy(cfg))
    assert migrated["gpu"]["vram_budget_ceiling_pct"] == 80
    assert migrated["gpu"]["vram_budget_ceiling_mib"] == 12000


@pytest.mark.parametrize("value", [0, -1, 101, float("inf"), "lots", True])
def test_validate_config_rejects_invalid_vram_ceiling_pct(value):
    cfg = make_cfg()
    cfg["gpu"]["vram_budget_ceiling_pct"] = value
    errors = validate_config(cfg)
    assert any("vram_budget_ceiling_pct" in error for error in errors)


@pytest.mark.parametrize("value", [-1, 1.5, "lots", True, False])
def test_validate_config_rejects_invalid_vram_ceiling_mib(value):
    cfg = make_cfg()
    cfg["gpu"]["vram_budget_ceiling_mib"] = value
    errors = validate_config(cfg)
    assert any("vram_budget_ceiling_mib" in error for error in errors)


def test_migrate_config_adds_default_vram_budget_ceiling_floor_mib():
    cfg = make_cfg()
    del cfg["gpu"]["vram_budget_ceiling_floor_mib"]
    migrated = config_module.migrate_config(copy.deepcopy(cfg))
    assert migrated["gpu"]["vram_budget_ceiling_floor_mib"] == 10240


def test_migrate_config_preserves_existing_vram_budget_ceiling_floor_mib():
    cfg = make_cfg()
    cfg["gpu"]["vram_budget_ceiling_floor_mib"] = 2048
    migrated = config_module.migrate_config(copy.deepcopy(cfg))
    assert migrated["gpu"]["vram_budget_ceiling_floor_mib"] == 2048


@pytest.mark.parametrize("value", [0, -1, 1.5, "lots", True])
def test_validate_config_rejects_invalid_vram_ceiling_floor_mib(value):
    cfg = make_cfg()
    cfg["gpu"]["vram_budget_ceiling_floor_mib"] = value
    errors = validate_config(cfg)
    assert any("vram_budget_ceiling_floor_mib" in error for error in errors)


def test_validate_config_rejects_infinite_llm_server_cpus():
    cfg = make_cfg(resources={"llm_server": {"cpus": float("inf"), "memory_mib": 0}})
    assert "resources.llm_server.cpus must be a non-negative number" in validate_config(cfg)


def test_validate_config_rejects_infinite_omniroute_cpus():
    cfg = make_cfg(resources={"omniroute": {"cpus": float("inf"), "memory_mib": 1024}})
    assert "resources.omniroute.cpus must be a non-negative number" in validate_config(cfg)


def test_migrate_config_adds_default_resources_section_when_gpu_absent():
    """The resources default must not be skipped by the gpu early-return branch."""
    cfg = make_cfg()
    del cfg["gpu"]
    migrated = config_module.migrate_config(copy.deepcopy(cfg))
    assert migrated["resources"]["llm_server"] == {
        "cpus": 0,
        "memory_mib": 0,
        "memory_ceiling_pct": 46,
        "memory_ceiling_floor_mib": 10240,
    }


def test_config_without_resources_key_has_no_errors():
    assert validate_config(make_cfg()) == []


def test_config_accepts_valid_resources_section():
    cfg = make_cfg(resources={"llm_server": {"cpus": 6, "memory_mib": 28672}})
    assert validate_config(cfg) == []


def test_config_accepts_zero_sentinel_resources():
    cfg = make_cfg(resources={"llm_server": {"cpus": 0, "memory_mib": 0}})
    assert validate_config(cfg) == []


@pytest.mark.parametrize(
    "llm_server",
    [
        {"cpus": -1, "memory_mib": 0},
        {"cpus": "six", "memory_mib": 0},
        {"cpus": True, "memory_mib": 0},
        {"cpus": 0, "memory_mib": -1},
        {"cpus": 0, "memory_mib": "lots"},
    ],
)
def test_config_rejects_invalid_resources_values(llm_server):
    cfg = make_cfg(resources={"llm_server": llm_server})
    errors = validate_config(cfg)
    assert any("resources.llm_server" in error for error in errors)


def test_config_rejects_non_mapping_resources_section():
    cfg = make_cfg(resources=[])
    errors = validate_config(cfg)
    assert any(error == "section resources must be a mapping" for error in errors)


def test_migrate_config_adds_default_omniroute_section():
    cfg = make_cfg()
    del cfg["omniroute"]
    migrated = migrate_config(cfg)
    assert migrated["omniroute"] == {
        "image": "docker.io/diegosouzapw/omniroute:latest",
        "port": 20128,
        "initial_password": "",
    }


def test_migrate_config_preserves_existing_omniroute_values():
    cfg = make_cfg(
        omniroute={
            "image": "docker.io/diegosouzapw/omniroute:latest",
            "port": 21000,
            "initial_password": "existing-password",
        }
    )
    migrated = migrate_config(cfg)
    assert migrated["omniroute"]["port"] == 21000
    assert migrated["omniroute"]["initial_password"] == "existing-password"


def test_migrate_config_adds_default_resources_omniroute_section():
    cfg = make_cfg()
    del cfg["resources"]["omniroute"]
    migrated = migrate_config(cfg)
    assert migrated["resources"]["omniroute"] == {"cpus": 1, "memory_mib": 1024}


def test_config_accepts_valid_omniroute_section():
    cfg = make_cfg()
    assert validate_config(cfg) == []


def test_config_without_omniroute_key_has_no_errors():
    cfg = make_cfg()
    del cfg["omniroute"]
    assert validate_config(cfg) == []


def test_config_rejects_non_mapping_omniroute_section():
    cfg = make_cfg(omniroute=[])
    errors = validate_config(cfg)
    assert any(error == "section omniroute must be a mapping" for error in errors)


@pytest.mark.parametrize(
    "field,value,expected_error",
    [
        ("image", "", "omniroute.image must be a non-empty string"),
        ("image", 5, "omniroute.image must be a non-empty string"),
        ("port", 0, "omniroute.port must be a positive integer"),
        ("port", "20128", "omniroute.port must be a positive integer"),
        ("initial_password", 5, "omniroute.initial_password must be a string"),
    ],
)
def test_config_rejects_invalid_omniroute_values(field, value, expected_error):
    omniroute = {
        "image": "docker.io/diegosouzapw/omniroute:latest",
        "port": 20128,
        "initial_password": "",
    }
    omniroute[field] = value
    cfg = make_cfg(omniroute=omniroute)
    errors = validate_config(cfg)
    assert expected_error in errors


@pytest.mark.parametrize(
    "omniroute_resources",
    [
        {"cpus": -1, "memory_mib": 1024},
        {"cpus": True, "memory_mib": 1024},
        {"cpus": 1, "memory_mib": -1},
        {"cpus": 1, "memory_mib": 0.5},
    ],
)
def test_config_rejects_invalid_resources_omniroute_values(omniroute_resources):
    cfg = make_cfg(
        resources={
            "llm_server": {"cpus": 0, "memory_mib": 0},
            "omniroute": omniroute_resources,
        }
    )
    errors = validate_config(cfg)
    assert any("resources.omniroute" in error for error in errors)
