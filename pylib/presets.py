"""Render llama-server router presets.ini from models.yml.

Uses configparser so the output is always syntactically valid, including the
mandatory 'version = 1' and the shared '[*]' section that the previous
hand-rolled heredoc omitted.

Format reference: llama.cpp tools/server/README.md
"""

from __future__ import annotations

import configparser
import io
from pathlib import Path
from typing import Any

from pylib.config import enabled_models


def render_presets(cfg: dict[str, Any], models_dir: str, device: str) -> str:
    runtime = cfg.get("runtime", {})

    parser = configparser.ConfigParser(defaults={"version": "1"})
    parser.optionxform = str  # preserve hyphenated keys verbatim

    parser["*"] = {
        "device": device,
        "flash-attn": "on" if runtime.get("flash_attn") else "off",
        "cache-type-k": str(runtime.get("cache_type_k", "f16")),
        "cache-type-v": str(runtime.get("cache_type_v", "f16")),
    }

    for model in enabled_models(cfg):
        parser[model["alias"]] = {
            "model": str(Path(models_dir) / model["file"]),
            "ctx-size": str(model["ctx_size"]),
            "n-gpu-layers": str(model["n_gpu_layers"]),
        }

    buffer = io.StringIO()
    parser.write(buffer)
    return buffer.getvalue()


def write_presets(
    cfg: dict[str, Any], models_dir: str, device: str, path: Path
) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_presets(cfg, models_dir, device), encoding="utf-8")
