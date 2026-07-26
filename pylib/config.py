"""Load, validate, and mutate the models.yml configuration."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG_PATH = Path.home() / ".config" / "llm-env" / "models.yml"

REQUIRED_SECTIONS = ("server", "gpu", "runtime", "models")
REQUIRED_MODEL_KEYS = (
    "alias",
    "label",
    "parameters",
    "quantization",
    "enabled",
    "file",
    "url",
    "size_bytes",
    "vram_budget",
    "ctx_size",
    "n_gpu_layers",
)
VALID_BACKENDS = ("vulkan", "rocm", "cpu")
VRAM_BUDGET_RE = re.compile(r"^\s*\d+(\.\d+)?\s*(%|GB|MiB)\s*$", re.IGNORECASE)


class ConfigError(Exception):
    """Raised when the configuration cannot be read or is structurally invalid."""


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> dict[str, Any]:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ConfigError(f"config not found: {path}") from exc
    except OSError as exc:
        raise ConfigError(f"cannot read config {path}: {exc}") from exc

    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {path}: {exc}") from exc

    if not isinstance(data, dict):
        raise ConfigError(f"config root must be a mapping: {path}")
    return data


def save_config(cfg: dict[str, Any], path: Path = DEFAULT_CONFIG_PATH) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(cfg, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )


def validate_config(cfg: dict[str, Any]) -> list[str]:
    errors: list[str] = []

    if cfg.get("version") != 1:
        errors.append("version must be 1")

    for section in REQUIRED_SECTIONS:
        if section not in cfg:
            errors.append(f"missing required section: {section}")
    if errors:
        return errors

    gpu = cfg["gpu"]
    if gpu.get("backend") not in VALID_BACKENDS:
        errors.append(f"gpu.backend must be one of {VALID_BACKENDS}")
    if not isinstance(gpu.get("vram_total_mib"), int):
        errors.append("gpu.vram_total_mib must be an integer")
    if gpu.get("reserve_mode") not in ("auto", "fixed"):
        errors.append("gpu.reserve_mode must be 'auto' or 'fixed'")

    models = cfg["models"]
    if not isinstance(models, list) or not models:
        errors.append("models must be a non-empty list")
        return errors

    seen: set[str] = set()
    for model in models:
        alias = model.get("alias", "<unnamed>")
        for key in REQUIRED_MODEL_KEYS:
            if key not in model:
                errors.append(f"model {alias} missing key: {key}")
        if alias in seen:
            errors.append(f"duplicate model alias: {alias}")
        seen.add(alias)
        budget = model.get("vram_budget")
        if budget is not None and not VRAM_BUDGET_RE.match(str(budget)):
            errors.append(
                f"model {alias} vram_budget must look like '55%', '7.5GB', or '512MiB'"
            )

    return errors


def enabled_models(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    return [m for m in cfg.get("models", []) if m.get("enabled")]


def set_enabled_models(cfg: dict[str, Any], aliases: list[str]) -> dict[str, Any]:
    requested = set(aliases)
    known = {model["alias"] for model in cfg.get("models", [])}
    unknown = requested - known
    if unknown:
        raise ConfigError(f"unknown model alias: {min(unknown)}")
    for model in cfg["models"]:
        model["enabled"] = model["alias"] in requested
    return sync_models_max(cfg)


def set_model_enabled(cfg: dict[str, Any], alias: str, enabled: bool) -> dict[str, Any]:
    for model in cfg.get("models", []):
        if model.get("alias") == alias:
            model["enabled"] = enabled
            return cfg
    raise ConfigError(f"unknown model alias: {alias}")


def sync_models_max(cfg: dict[str, Any]) -> dict[str, Any]:
    cfg.setdefault("runtime", {})["models_max"] = len(enabled_models(cfg))
    return cfg
