"""Load, validate, and mutate the models.yml configuration."""

from __future__ import annotations

import math
import os
import re
import tempfile
from pathlib import Path
from typing import Any, TypeGuard

import yaml

from pylib.resources import (
    OMNIROUTE_CPU_MAX,
    OMNIROUTE_CPU_MIN,
    OMNIROUTE_MEMORY_MAX_MIB,
    OMNIROUTE_MEMORY_MIN_MIB,
    PHOENIX_CPUS_MAX,
    PHOENIX_CPUS_MIN,
    PHOENIX_MEMORY_MAX_MIB,
    PHOENIX_MEMORY_MIN_MIB,
    REMOTE_SETUP_CPU_MAX,
    REMOTE_SETUP_CPU_MIN,
    REMOTE_SETUP_MEMORY_MAX_MIB,
    REMOTE_SETUP_MEMORY_MIN_MIB,
)

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
    "client_max_output_tokens",
    "n_gpu_layers",
)
VALID_BACKENDS = ("vulkan", "cpu")
VRAM_BUDGET_RE = re.compile(r"^\s*\d+(\.\d+)?\s*(%|GB|MiB)\s*$", re.IGNORECASE)
SAMPLING_FIELDS = frozenset(
    ("temperature", "top_p", "top_k", "repeat_penalty")
)

DEFAULT_OMNIROUTE_IMAGE = "docker.io/diegosouzapw/omniroute:latest"
DEFAULT_OMNIROUTE_PORT = 20128
DEFAULT_REMOTE_SETUP_IMAGE = "docker.io/library/python:3.13-alpine"
DEFAULT_REMOTE_SETUP_PORT = 20130
DEFAULT_SYSTEM_ONE_IMAGE = "localhost/llm-env-system-one:rocm"
DEFAULT_SYSTEM_ONE_PORT = 8010
# Mirrors laya.router canonical names (english | multilingual | typed-decisions).
LAYA_MODEL_ALIASES = {
    "en": "english", "laya": "english", "default": "english",
    "multi": "multilingual", "ml": "multilingual", "laya-multilingual": "multilingual",
    "typed": "typed-decisions", "typed_decisions": "typed-decisions",
    "laya-typed-decisions": "typed-decisions", "decisions": "typed-decisions",
}


def canonical_laya_model_name(token: str) -> str:
    key = token.strip().lower()
    if key in LAYA_MODEL_ALIASES:
        return LAYA_MODEL_ALIASES[key]
    if key in ("english", "multilingual", "typed-decisions"):
        return key
    raise ValueError(token)


class ConfigError(Exception):
    """Raised when the configuration cannot be read or is structurally invalid."""


def _positive_int(value: Any) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _validate_min_max_resources(resources: dict[str, Any], key: str) -> list[str]:
    """Validate a resources.<key> mapping shaped {cpus_min, cpus_max,
    memory_min_mib, memory_max_mib}, including that each min <= its max."""
    errors: list[str] = []
    section = resources.get(key, {})
    if not isinstance(section, dict):
        return [f"resources.{key} must be a mapping"]

    cpus_min = section.get("cpus_min", 0)
    if isinstance(cpus_min, bool) or not (_finite_number(cpus_min) and cpus_min >= 0):
        errors.append(f"resources.{key}.cpus_min must be a non-negative number")
    cpus_max = section.get("cpus_max", 0)
    if isinstance(cpus_max, bool) or not (_finite_number(cpus_max) and cpus_max >= 0):
        errors.append(f"resources.{key}.cpus_max must be a non-negative number")
    if (
        f"resources.{key}.cpus_min must be a non-negative number" not in errors
        and f"resources.{key}.cpus_max must be a non-negative number" not in errors
        and cpus_max != 0
        and cpus_min > cpus_max
    ):
        errors.append(
            f"resources.{key}.cpus_min ({cpus_min}) must not exceed "
            f"resources.{key}.cpus_max ({cpus_max})"
        )

    memory_min_mib = section.get("memory_min_mib", 0)
    if not (memory_min_mib == 0 or _positive_int(memory_min_mib)):
        errors.append(
            f"resources.{key}.memory_min_mib must be zero or a positive integer"
        )
    memory_max_mib = section.get("memory_max_mib", 0)
    if not (memory_max_mib == 0 or _positive_int(memory_max_mib)):
        errors.append(
            f"resources.{key}.memory_max_mib must be zero or a positive integer"
        )
    if (
        f"resources.{key}.memory_min_mib must be zero or a positive integer"
        not in errors
        and f"resources.{key}.memory_max_mib must be zero or a positive integer"
        not in errors
        and memory_max_mib != 0
        and memory_min_mib > memory_max_mib
    ):
        errors.append(
            f"resources.{key}.memory_min_mib ({memory_min_mib}) must not exceed "
            f"resources.{key}.memory_max_mib ({memory_max_mib})"
        )
    return errors


def _finite_number(value: Any) -> bool:
    return (
        isinstance(value, int) and not isinstance(value, bool)
    ) or (isinstance(value, float) and math.isfinite(value))


def migrate_config(cfg: dict[str, Any]) -> dict[str, Any]:
    """Apply additive defaults and remove obsolete generated benchmark data."""
    runtime = cfg.get("runtime")
    if isinstance(runtime, dict):
        runtime.setdefault("parallel_slots", 1)
        runtime.setdefault("ubatch_size", 512)

    models = cfg.get("models")
    if isinstance(models, list):
        for model in models:
            if not isinstance(model, dict) or "client_max_output_tokens" in model:
                continue
            ctx_size = model.get("ctx_size")
            if _positive_int(ctx_size):
                model["client_max_output_tokens"] = min(ctx_size, 8192)

    resources = cfg.setdefault("resources", {})
    if isinstance(resources, dict):
        llm_server_resources = resources.setdefault("llm_server", {})
        if isinstance(llm_server_resources, dict):
            llm_server_resources.setdefault("cpus", 0)
            llm_server_resources.setdefault("memory_mib", 0)
            llm_server_resources.setdefault("memory_ceiling_pct", 46)
            llm_server_resources.setdefault("memory_ceiling_floor_pct", 30)
            llm_server_resources.setdefault("cpu_ceiling_pct", 60)
            llm_server_resources.setdefault("cpu_ceiling_floor_pct", 20)
        omniroute_resources = resources.setdefault("omniroute", {})
        if isinstance(omniroute_resources, dict):
            omniroute_resources.setdefault("cpus_min", OMNIROUTE_CPU_MIN)
            omniroute_resources.setdefault("cpus_max", OMNIROUTE_CPU_MAX)
            omniroute_resources.setdefault("memory_min_mib", OMNIROUTE_MEMORY_MIN_MIB)
            omniroute_resources.setdefault("memory_max_mib", OMNIROUTE_MEMORY_MAX_MIB)
        remote_setup_resources = resources.setdefault("remote_setup", {})
        if isinstance(remote_setup_resources, dict):
            remote_setup_resources.setdefault("cpus_min", REMOTE_SETUP_CPU_MIN)
            remote_setup_resources.setdefault("cpus_max", REMOTE_SETUP_CPU_MAX)
            remote_setup_resources.setdefault(
                "memory_min_mib", REMOTE_SETUP_MEMORY_MIN_MIB
            )
            remote_setup_resources.setdefault(
                "memory_max_mib", REMOTE_SETUP_MEMORY_MAX_MIB
            )
        # Phoenix is not part of the rendered main-stack compose file (it
        # owns a separate compose project); its policy constants live here
        # so setup/start persist the same values phoenix.sh renders.
        phoenix_resources = resources.setdefault("phoenix", {})
        if isinstance(phoenix_resources, dict):
            phoenix_resources.setdefault("cpus_min", PHOENIX_CPUS_MIN)
            phoenix_resources.setdefault("cpus_max", PHOENIX_CPUS_MAX)
            phoenix_resources.setdefault("memory_min_mib", PHOENIX_MEMORY_MIN_MIB)
            phoenix_resources.setdefault("memory_max_mib", PHOENIX_MEMORY_MAX_MIB)

    omniroute = cfg.setdefault("omniroute", {})
    if isinstance(omniroute, dict):
        omniroute.setdefault("image", DEFAULT_OMNIROUTE_IMAGE)
        omniroute.setdefault("port", DEFAULT_OMNIROUTE_PORT)
        omniroute.setdefault("initial_password", "")

    remote_setup = cfg.setdefault("remote_setup", {})
    if isinstance(remote_setup, dict):
        remote_setup.setdefault("image", DEFAULT_REMOTE_SETUP_IMAGE)
        remote_setup.setdefault("port", DEFAULT_REMOTE_SETUP_PORT)

    llm_server = cfg.setdefault("llm_server", {})
    if isinstance(llm_server, dict):
        llm_server.setdefault("enabled", True)

    system_one = cfg.setdefault("system_one", {})
    if isinstance(system_one, dict):
        system_one.setdefault("enabled", False)
        system_one.setdefault("port", DEFAULT_SYSTEM_ONE_PORT)
        system_one.setdefault("image", DEFAULT_SYSTEM_ONE_IMAGE)
        # Measured 2026-10-12 (RX 9070 XT, english+multilingual): ~4.26 GB steady VRAM.
        system_one.setdefault("models", "english,multilingual")
        system_one.setdefault("vram_reserve_mib", 5120)
        # 0 disables unload. Laya 0.4.2 drops checkpoints after this many idle seconds.
        system_one.setdefault("idle_unload_seconds", 600)

    gpu = cfg.get("gpu")
    if not isinstance(gpu, dict):
        return cfg
    gpu.setdefault("vram_budget_ceiling_pct", 95)
    gpu.setdefault("vram_budget_ceiling_mib", gpu.get("vram_total_mib", 0))
    gpu.setdefault("vram_budget_ceiling_floor_pct", 30)
    gpu.pop("benchmark", None)
    return cfg


def load_config(
    path: Path = DEFAULT_CONFIG_PATH, *, migrate: bool = True
) -> dict[str, Any]:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ConfigError(f"config not found: {path}") from exc
    except OSError as exc:
        raise ConfigError(f"cannot read config {path}: {exc}") from exc

    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        problem = getattr(exc, "problem", None)
        if not isinstance(problem, str) or not problem:
            problem = "parser error"
        mark = getattr(exc, "problem_mark", None)
        line = mark.line + 1 if mark is not None else "unknown"
        column = mark.column + 1 if mark is not None else "unknown"
        raise ConfigError(
            f"invalid YAML in {path}: {problem}; line {line}, column {column}"
        ) from exc

    if not isinstance(data, dict):
        raise ConfigError(f"config root must be a mapping: {path}")
    return migrate_config(data) if migrate else data


def save_config(cfg: dict[str, Any], path: Path = DEFAULT_CONFIG_PATH) -> None:
    cfg = migrate_config(cfg)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    content = yaml.safe_dump(cfg, sort_keys=False, default_flow_style=False)
    file_descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}."
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as temporary_file:
            temporary_file.write(content)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.chmod(temporary_path, 0o600)
        os.replace(temporary_path, path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def validate_config(cfg: dict[str, Any]) -> list[str]:
    errors: list[str] = []

    if cfg.get("version") != 1:
        errors.append("version must be 1")

    for section in REQUIRED_SECTIONS:
        if section not in cfg:
            errors.append(f"missing required section: {section}")
    if errors:
        return errors

    for section in ("server", "gpu", "runtime"):
        if not isinstance(cfg[section], dict):
            errors.append(f"section {section} must be a mapping")
    if not isinstance(cfg["models"], list):
        errors.append("section models must be a list")
    if errors:
        return errors

    gpu = cfg["gpu"]
    if gpu.get("backend") not in VALID_BACKENDS:
        errors.append(f"gpu.backend must be one of {VALID_BACKENDS}")
    if not isinstance(gpu.get("vram_total_mib"), int):
        errors.append("gpu.vram_total_mib must be an integer")
    if gpu.get("reserve_mode") not in ("auto", "fixed"):
        errors.append("gpu.reserve_mode must be 'auto' or 'fixed'")
    ceiling_pct = gpu.get("vram_budget_ceiling_pct", 95)
    if isinstance(ceiling_pct, bool) or not (
        _finite_number(ceiling_pct) and 0 < ceiling_pct <= 100
    ):
        errors.append(
            "gpu.vram_budget_ceiling_pct must be a finite number greater "
            "than 0 and at most 100"
        )
    ceiling_mib = gpu.get("vram_budget_ceiling_mib", gpu.get("vram_total_mib", 0))
    if isinstance(ceiling_mib, bool) or not (
        _positive_int(ceiling_mib) or ceiling_mib == 0
    ):
        errors.append("gpu.vram_budget_ceiling_mib must be a non-negative integer")
    ceiling_floor_pct = gpu.get("vram_budget_ceiling_floor_pct", 30)
    if isinstance(ceiling_floor_pct, bool) or not (
        _finite_number(ceiling_floor_pct) and 0 < ceiling_floor_pct <= 100
    ):
        errors.append(
            "gpu.vram_budget_ceiling_floor_pct must be a finite number "
            "greater than 0 and at most 100"
        )

    runtime = cfg["runtime"]
    for key in ("models_max", "parallel_slots", "ubatch_size"):
        if not _positive_int(runtime.get(key)):
            errors.append(f"runtime.{key} must be a positive integer")
    if runtime.get("parallel_slots") != 1:
        errors.append("runtime.parallel_slots must be 1")

    if "resources" in cfg:
        resources = cfg["resources"]
        if not isinstance(resources, dict):
            errors.append("section resources must be a mapping")
        else:
            llm_server_resources = resources.get("llm_server", {})
            if not isinstance(llm_server_resources, dict):
                errors.append("resources.llm_server must be a mapping")
            else:
                cpus = llm_server_resources.get("cpus", 0)
                if isinstance(cpus, bool) or not (
                    _finite_number(cpus) and cpus >= 0
                ):
                    errors.append(
                        "resources.llm_server.cpus must be a non-negative number"
                    )
                memory_mib = llm_server_resources.get("memory_mib", 0)
                if not (memory_mib == 0 or _positive_int(memory_mib)):
                    errors.append(
                        "resources.llm_server.memory_mib must be zero or a "
                        "positive integer"
                    )
                memory_ceiling_pct = llm_server_resources.get("memory_ceiling_pct", 46)
                if isinstance(memory_ceiling_pct, bool) or not (
                    _finite_number(memory_ceiling_pct)
                    and 0 < memory_ceiling_pct <= 100
                ):
                    errors.append(
                        "resources.llm_server.memory_ceiling_pct must be a "
                        "finite number greater than 0 and at most 100"
                    )
                memory_ceiling_floor_pct = llm_server_resources.get(
                    "memory_ceiling_floor_pct", 30
                )
                if isinstance(memory_ceiling_floor_pct, bool) or not (
                    _finite_number(memory_ceiling_floor_pct)
                    and 0 < memory_ceiling_floor_pct <= 100
                ):
                    errors.append(
                        "resources.llm_server.memory_ceiling_floor_pct must be a "
                        "finite number greater than 0 and at most 100"
                    )
                cpu_ceiling_pct = llm_server_resources.get("cpu_ceiling_pct", 60)
                if isinstance(cpu_ceiling_pct, bool) or not (
                    _finite_number(cpu_ceiling_pct) and 0 < cpu_ceiling_pct <= 100
                ):
                    errors.append(
                        "resources.llm_server.cpu_ceiling_pct must be a "
                        "finite number greater than 0 and at most 100"
                    )
                cpu_ceiling_floor_pct = llm_server_resources.get(
                    "cpu_ceiling_floor_pct", 20
                )
                if isinstance(cpu_ceiling_floor_pct, bool) or not (
                    _finite_number(cpu_ceiling_floor_pct)
                    and 0 < cpu_ceiling_floor_pct <= 100
                ):
                    errors.append(
                        "resources.llm_server.cpu_ceiling_floor_pct must be a "
                        "finite number greater than 0 and at most 100"
                    )
            errors.extend(
                _validate_min_max_resources(resources, "omniroute")
            )
            errors.extend(
                _validate_min_max_resources(resources, "remote_setup")
            )
            errors.extend(_validate_min_max_resources(resources, "phoenix"))

    if "omniroute" in cfg:
        omniroute = cfg["omniroute"]
        if not isinstance(omniroute, dict):
            errors.append("section omniroute must be a mapping")
        else:
            image = omniroute.get("image")
            if not (isinstance(image, str) and image.strip()):
                errors.append("omniroute.image must be a non-empty string")
            if not _positive_int(omniroute.get("port")):
                errors.append("omniroute.port must be a positive integer")
            if not isinstance(omniroute.get("initial_password", ""), str):
                errors.append("omniroute.initial_password must be a string")

    if "remote_setup" in cfg:
        remote_setup = cfg["remote_setup"]
        if not isinstance(remote_setup, dict):
            errors.append("section remote_setup must be a mapping")
        else:
            rs_image = remote_setup.get("image")
            if not (isinstance(rs_image, str) and rs_image.strip()):
                errors.append("remote_setup.image must be a non-empty string")
            if not _positive_int(remote_setup.get("port")):
                errors.append("remote_setup.port must be a positive integer")

    llm_server_enabled = True
    if "llm_server" in cfg:
        llm_server = cfg["llm_server"]
        if not isinstance(llm_server, dict):
            errors.append("section llm_server must be a mapping")
        else:
            if "enabled" in llm_server:
                if not isinstance(llm_server["enabled"], bool):
                    errors.append("llm_server.enabled must be a Boolean")
                else:
                    llm_server_enabled = llm_server["enabled"]

    if "system_one" in cfg:
        system_one = cfg["system_one"]
        if not isinstance(system_one, dict):
            errors.append("section system_one must be a mapping")
        else:
            if not isinstance(system_one.get("enabled", False), bool):
                errors.append("system_one.enabled must be a Boolean")
            if not _positive_int(system_one.get("port", DEFAULT_SYSTEM_ONE_PORT)):
                errors.append("system_one.port must be a positive integer")
            for key in ("image", "models"):
                value = system_one.get(key, "x")
                if not isinstance(value, str) or not value.strip():
                    errors.append(f"system_one.{key} must be a non-empty string")
            if not _positive_int(system_one.get("vram_reserve_mib", 5120)):
                errors.append("system_one.vram_reserve_mib must be a positive integer")
            idle_unload_seconds = system_one.get("idle_unload_seconds", 600)
            if (
                isinstance(idle_unload_seconds, bool)
                or not isinstance(idle_unload_seconds, int)
                or idle_unload_seconds < 0
            ):
                errors.append(
                    "system_one.idle_unload_seconds must be a non-negative integer"
                )
            models_raw = system_one.get("models", "english")
            if isinstance(models_raw, str):
                parts = [p.strip() for p in models_raw.split(",")]
                if not parts or any(not p for p in parts):
                    errors.append("system_one.models must be a comma-separated list with no empty members")
                else:
                    seen: set[str] = set()
                    for part in parts:
                        try:
                            canonical = canonical_laya_model_name(part)
                        except ValueError:
                            errors.append(f"system_one.models contains unknown checkpoint name: {part}")
                            continue
                        if canonical in seen:
                            errors.append(f"system_one.models lists {canonical} more than once")
                        seen.add(canonical)

    models = cfg["models"]
    if not isinstance(models, list) or not models:
        errors.append("models must be a non-empty list")
        return errors

    seen: set[str] = set()
    for index, model in enumerate(models):
        if not isinstance(model, dict):
            errors.append("each model must be a mapping")
            continue
        alias = model.get("alias")
        valid_alias = isinstance(alias, str) and bool(alias.strip())
        model_name = alias if valid_alias else f"at index {index}"
        for key in REQUIRED_MODEL_KEYS:
            if key not in model:
                errors.append(f"model {model_name} missing key: {key}")
        if not valid_alias:
            errors.append(f"model at index {index} alias must be a non-empty string")
        elif alias in seen:
            errors.append(f"duplicate model alias: {alias}")
        else:
            assert isinstance(alias, str)
            seen.add(alias)
        if "enabled" in model and not isinstance(model["enabled"], bool):
            errors.append(f"model {model_name} enabled must be a Boolean")
        if "sampling" in model:
            sampling = model["sampling"]
            if not isinstance(sampling, dict):
                errors.append(f"model {model_name} sampling must be a mapping")
            else:
                for field in sampling:
                    if field not in SAMPLING_FIELDS:
                        errors.append(
                            f"model {model_name} sampling.{field} is not a supported field"
                        )

                if "temperature" in sampling and not (
                    _finite_number(sampling["temperature"])
                    and sampling["temperature"] >= 0
                ):
                    errors.append(
                        f"model {model_name} sampling.temperature must be a finite non-negative number"
                    )
                if "top_p" in sampling and not (
                    _finite_number(sampling["top_p"])
                    and 0 <= sampling["top_p"] <= 1
                ):
                    errors.append(
                        f"model {model_name} sampling.top_p must be a finite number between 0 and 1 inclusive"
                    )
                if "top_k" in sampling and not (
                    isinstance(sampling["top_k"], int)
                    and not isinstance(sampling["top_k"], bool)
                    and sampling["top_k"] >= 0
                ):
                    errors.append(
                        f"model {model_name} sampling.top_k must be a non-negative integer and not a Boolean"
                    )
                if "repeat_penalty" in sampling and not (
                    _finite_number(sampling["repeat_penalty"])
                    and sampling["repeat_penalty"] > 0
                ):
                    errors.append(
                        f"model {model_name} sampling.repeat_penalty must be a finite number greater than 0"
                    )
        budget = model.get("vram_budget")
        if budget is not None and not VRAM_BUDGET_RE.match(str(budget)):
            errors.append(
                f"model {model_name} vram_budget must look like '55%', '7.5GB', or '512MiB'"
            )

        for key in ("ctx_size", "client_max_output_tokens"):
            if not _positive_int(model.get(key)):
                errors.append(f"model {model_name} {key} must be a positive integer")
        if (
            _positive_int(model.get("ctx_size"))
            and _positive_int(model.get("client_max_output_tokens"))
            and model["client_max_output_tokens"] > model["ctx_size"]
        ):
            errors.append(
                f"model {model_name} client_max_output_tokens must not exceed ctx_size"
            )
        if "n_cpu_moe" in model:
            n_cpu_moe = model["n_cpu_moe"]
            if isinstance(n_cpu_moe, bool) or not (
                isinstance(n_cpu_moe, int) and n_cpu_moe >= 0
            ):
                errors.append(
                    f"model {model_name} n_cpu_moe must be a non-negative integer"
                )
        for key in ("check_ctx_size", "check_timeout_seconds"):
            if key in model and not _positive_int(model[key]):
                errors.append(f"model {model_name} {key} must be a positive integer")

    enabled_count = len(enabled_models(cfg))
    if llm_server_enabled:
        if enabled_count == 0:
            errors.append("at least one model must be enabled")
        if _positive_int(runtime.get("models_max")) and runtime["models_max"] > enabled_count:
            errors.append(
                f"runtime.models_max must not exceed enabled model count ({enabled_count})"
            )

    return errors


def require_valid_config(cfg: dict[str, Any]) -> dict[str, Any]:
    """Return cfg or raise one actionable error containing every validation issue."""
    errors = validate_config(cfg)
    if errors:
        raise ConfigError("; ".join(errors))
    return cfg


def enabled_models(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        model
        for model in cfg.get("models", [])
        if isinstance(model, dict) and model.get("enabled") is True
    ]


def set_enabled_models(cfg: dict[str, Any], aliases: list[str]) -> dict[str, Any]:
    requested: set[str] = set()
    for alias in aliases:
        if alias in requested:
            raise ConfigError(f"duplicate model alias: {alias}")
        requested.add(alias)
    known = {model["alias"] for model in cfg.get("models", [])}
    unknown = requested - known
    if unknown:
        raise ConfigError(f"unknown model alias: {min(unknown)}")
    if not requested:
        raise ConfigError("at least one model must remain enabled")

    runtime = cfg.get("runtime")
    configured = runtime.get("models_max", 1) if isinstance(runtime, dict) else 1
    if not _positive_int(configured):
        raise ConfigError("runtime.models_max must be a positive integer")

    models_by_alias = {model["alias"]: model for model in cfg["models"]}
    reordered = [models_by_alias[alias] for alias in aliases]
    reordered.extend(model for model in cfg["models"] if model["alias"] not in requested)
    for model in reordered:
        model["enabled"] = model["alias"] in requested
    cfg["models"] = reordered
    return sync_models_max(cfg)


def set_model_enabled(cfg: dict[str, Any], alias: str, enabled: bool) -> dict[str, Any]:
    model = next(
        (item for item in cfg.get("models", []) if item.get("alias") == alias), None
    )
    if model is None:
        raise ConfigError(f"unknown model alias: {alias}")
    if not enabled and model.get("enabled") and len(enabled_models(cfg)) == 1:
        raise ConfigError("cannot disable the final enabled model")
    model["enabled"] = enabled
    return cfg


def sync_models_max(cfg: dict[str, Any]) -> dict[str, Any]:
    enabled_count = len(enabled_models(cfg))
    if enabled_count == 0:
        raise ConfigError("at least one model must remain enabled")
    runtime = cfg.setdefault("runtime", {})
    configured = runtime.get("models_max", 1)
    if not _positive_int(configured):
        raise ConfigError("runtime.models_max must be a positive integer")
    runtime["models_max"] = min(configured, enabled_count)
    return cfg
