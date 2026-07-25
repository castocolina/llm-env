import configparser
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pylib.presets import render_presets, write_presets

CFG = {
    "version": 1,
    "runtime": {
        "models_max": 2,
        "flash_attn": True,
        "cache_type_k": "q8_0",
        "cache_type_v": "q8_0",
    },
    "models": [
        {
            "alias": "gemma4",
            "enabled": True,
            "file": "gemma-4-12B-it-Q4_K_M.gguf",
            "ctx_size": 8192,
            "n_gpu_layers": 99,
        },
        {
            "alias": "ornith",
            "enabled": False,
            "file": "ornith-1.0-9b-Q4_K_M.gguf",
            "ctx_size": 4096,
            "n_gpu_layers": 99,
        },
    ],
}


def parse(text: str) -> configparser.ConfigParser:
    parser = configparser.ConfigParser()
    parser.read_string(text)
    return parser


def test_output_declares_version_1():
    assert parse(render_presets(CFG, "/models", "Vulkan0")).defaults()["version"] == "1"


def test_global_star_section_carries_shared_settings():
    parser = parse(render_presets(CFG, "/models", "Vulkan0"))
    star = parser["*"]
    assert star["device"] == "Vulkan0"
    assert star["flash-attn"] == "on"
    assert star["cache-type-k"] == "q8_0"
    assert star["cache-type-v"] == "q8_0"


def test_only_enabled_models_get_sections():
    parser = parse(render_presets(CFG, "/models", "Vulkan0"))
    sections = [s for s in parser.sections() if s != "*"]
    assert sections == ["gemma4"]


def test_model_section_has_absolute_path_and_settings():
    parser = parse(render_presets(CFG, "/models", "Vulkan0"))
    section = parser["gemma4"]
    assert section["model"] == "/models/gemma-4-12B-it-Q4_K_M.gguf"
    assert section["ctx-size"] == "8192"
    assert section["n-gpu-layers"] == "99"


def test_flash_attn_off_renders_off():
    cfg = {**CFG, "runtime": {**CFG["runtime"], "flash_attn": False}}
    assert parse(render_presets(cfg, "/models", "Vulkan0"))["*"]["flash-attn"] == "off"


def test_write_presets_creates_parent_directories(tmp_path):
    target = tmp_path / "nested" / "presets.ini"
    write_presets(CFG, "/models", "Vulkan0", target)
    assert target.exists()
    assert "[gemma4]" in target.read_text()
