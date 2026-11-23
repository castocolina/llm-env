"""Tracked host profile: the non-secret, owner-chosen subset of models.yml.

`llm-env.profile.yml` (committed) is merged onto the host-local models.yml by `make setup` and
`make profile-apply`; `make profile-check` reports drift. Secrets and generated values are not in
the schema, so they can neither be exported nor overwritten. Design and merge rules:
docs/superpowers/plans/2026-11-23-tracked-host-profile.md.
"""

from __future__ import annotations

import copy
import ipaddress
import os
import re
import tempfile
from pathlib import Path
from typing import Any

import yaml

from pylib.config import (
    REQUIRED_MODEL_KEYS,
    ConfigError,
    _positive_int,
    enabled_models,
    migrate_config,
    require_valid_config,
    set_enabled_models,
)
from pylib.resources import validate_overrides

DEFAULT_PROFILE_PATH = Path(__file__).resolve().parents[1] / "llm-env.profile.yml"
PROFILE_VERSION = 1

# Sections copied key by key onto the same path in models.yml.
MIRRORED: dict[str, tuple[str, ...]] = {
    "server": ("port", "mdns_name", "sleep_idle_seconds"),
    "omniroute": ("image", "port"),
    "remote_setup": ("image", "port"),
    "runtime": ("models_max", "ubatch_size", "flash_attn", "cache_type_k", "cache_type_v"),
    "system_one": ("enabled", "listen", "port", "models", "vram_reserve_mib", "idle_unload_seconds"),
}
LLM_SERVER_RESOURCE_KEYS = (
    "memory_ceiling_pct", "memory_ceiling_floor_pct", "cpu_ceiling_pct", "cpu_ceiling_floor_pct",
)
# `enabled` comes from llm_server.models; `benchmark` and anything else is generated.
MODEL_KEYS = frozenset(REQUIRED_MODEL_KEYS) - {"enabled"} | {
    "sampling", "n_cpu_moe", "check_ctx_size", "check_timeout_seconds",
}
LEAF = None
SCHEMA: dict[str, Any] = {
    "version": LEAF,
    **{section: dict.fromkeys(keys, LEAF) for section, keys in MIRRORED.items()},
    "llm_server": {"enabled": LEAF, "models": LEAF},
    "models": LEAF,
    "resources": {"overrides": LEAF, "llm_server": dict.fromkeys(LLM_SERVER_RESOURCE_KEYS, LEAF)},
    "boot": {"enable": LEAF},
    "observe": {"enabled": LEAF},
    "network": {"firewall": {"manage": LEAF, "allowed_cidrs": LEAF}},
}
# Read by the shell scripts from the profile itself; they have no home in models.yml.
PROFILE_ONLY_DEFAULTS: dict[str, Any] = {
    "observe": {"enabled": False},
    "network": {"firewall": {"manage": False, "allowed_cidrs": []}},
}
SECRET_PATHS = (("server", "api_key"), ("omniroute", "initial_password"))
SECRET_NAME_RE = re.compile(r"key|password|token|secret", re.IGNORECASE)


def _unknown_keys(data: dict[str, Any], schema: dict[str, Any], prefix: str) -> list[str]:
    errors: list[str] = []
    for key, value in data.items():
        path = f"{prefix}{key}"
        if key not in schema:
            errors.append(f"unknown key: {path}")
        elif isinstance(schema[key], dict):
            if not isinstance(value, dict):
                errors.append(f"{path} must be a mapping")
            else:
                errors.extend(_unknown_keys(value, schema[key], f"{path}."))
    return errors


def _bool_at(profile: dict[str, Any], section: str, key: str, errors: list[str]) -> None:
    value = profile.get(section, {}).get(key) if isinstance(profile.get(section), dict) else None
    if value is not None and not isinstance(value, bool):
        errors.append(f"{section}.{key} must be a Boolean")


def _model_entry_errors(entries: Any) -> list[str]:
    if entries is None:
        return []
    if not isinstance(entries, list):
        return ["models must be a list of model entries"]
    errors: list[str] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict) or not isinstance(entry.get("alias"), str):
            errors.append(f"models[{index}] must be a mapping with an alias")
            continue
        errors.extend(f"unknown key: models[{entry['alias']}].{key}" for key in entry if key not in MODEL_KEYS)
        if entry["alias"] in seen:
            errors.append(f"models lists {entry['alias']} more than once")
        seen.add(entry["alias"])
    return errors


def validate_profile(profile: Any) -> list[str]:
    """Structural checks the merged models.yml cannot express; value checks run in apply_profile."""
    if not isinstance(profile, dict):
        return ["profile root must be a mapping"]
    errors = _unknown_keys(profile, SCHEMA, "") + _model_entry_errors(profile.get("models"))
    if errors:
        return errors
    if profile.get("version") != PROFILE_VERSION:
        errors.append(f"version must be {PROFILE_VERSION}")
    for section, key in (("boot", "enable"), ("observe", "enabled"), ("llm_server", "enabled")):
        _bool_at(profile, section, key, errors)
    firewall = profile.get("network", {}).get("firewall", {})
    if "manage" in firewall and not isinstance(firewall["manage"], bool):
        errors.append("network.firewall.manage must be a Boolean")
    cidrs = firewall.get("allowed_cidrs", [])
    if not isinstance(cidrs, list):
        errors.append("network.firewall.allowed_cidrs must be a list")
    else:
        for cidr in cidrs:
            try:
                if not isinstance(cidr, str) or "/" not in cidr:
                    raise ValueError
                ipaddress.ip_network(cidr, strict=False)
            except ValueError:
                errors.append(f"network.firewall.allowed_cidrs: not a CIDR: {cidr!r}")
    server = profile.get("server", {})
    if "port" in server and not _positive_int(server["port"]):
        errors.append("server.port must be a positive integer")
    if "sleep_idle_seconds" in server and (
        isinstance(server["sleep_idle_seconds"], bool)
        or not isinstance(server["sleep_idle_seconds"], int)
        or server["sleep_idle_seconds"] < 0
    ):
        errors.append("server.sleep_idle_seconds must be a non-negative integer")
    if "mdns_name" in server and not (isinstance(server["mdns_name"], str) and server["mdns_name"].strip()):
        errors.append("server.mdns_name must be a non-empty string")
    aliases = profile.get("llm_server", {}).get("models")
    if aliases is not None and (
        not isinstance(aliases, list) or not aliases
        or not all(isinstance(alias, str) and alias.strip() for alias in aliases)
        or len(set(aliases)) != len(aliases)
    ):
        errors.append("llm_server.models must be a non-empty list of distinct model aliases")
    overrides = profile.get("resources", {}).get("overrides")
    if overrides is not None:
        errors.extend(validate_overrides(overrides))
    return errors


def apply_profile(cfg: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    """Return a new, validated config with the profile merged in; cfg is not modified."""
    errors = validate_profile(profile)
    if errors:
        raise ConfigError("profile: " + "; ".join(errors))
    new = copy.deepcopy(cfg)
    for section, keys in MIRRORED.items():
        for key in keys:
            if key in profile.get(section, {}):
                new.setdefault(section, {})[key] = copy.deepcopy(profile[section][key])
    if "enabled" in profile.get("llm_server", {}):
        new.setdefault("llm_server", {})["enabled"] = profile["llm_server"]["enabled"]
    if "enable" in profile.get("boot", {}):
        new.setdefault("server", {})["start_at_boot"] = profile["boot"]["enable"]
    resources = profile.get("resources", {})
    if "overrides" in resources:
        if resources["overrides"]:
            new.setdefault("resources", {})["overrides"] = copy.deepcopy(resources["overrides"])
        elif isinstance(new.get("resources"), dict):
            new["resources"].pop("overrides", None)  # absent and empty mean the same: no overrides
    for key, value in resources.get("llm_server", {}).items():
        new.setdefault("resources", {}).setdefault("llm_server", {})[key] = value
    models = new.setdefault("models", [])
    for entry in profile.get("models", []):
        index = next((i for i, m in enumerate(models) if isinstance(m, dict) and m.get("alias") == entry["alias"]), None)
        live = models[index] if index is not None else {}
        replacement = copy.deepcopy(entry) | {"enabled": live.get("enabled", False)}
        if "benchmark" in live:
            replacement["benchmark"] = live["benchmark"]
        if index is None:
            models.append(replacement)
        else:
            models[index] = replacement
    if "models" in profile.get("llm_server", {}):
        try:
            new = set_enabled_models(new, list(profile["llm_server"]["models"]))
        except ConfigError as exc:
            raise ConfigError(f"profile: llm_server.models: {exc}") from exc
    try:
        return require_valid_config(migrate_config(new))
    except ConfigError as exc:
        raise ConfigError(f"profile makes models.yml invalid: {exc}") from exc


def _diff(old: Any, new: Any, path: str, out: list[dict[str, Any]]) -> None:
    if old == new:
        return
    if isinstance(old, dict) and isinstance(new, dict):
        for key in list(old) + [k for k in new if k not in old]:
            _diff(old.get(key), new.get(key), f"{path}.{key}" if path else str(key), out)
        return
    if isinstance(old, list) and isinstance(new, list) and len(old) == len(new):
        for index, (a, b) in enumerate(zip(old, new, strict=True)):
            label = b.get("alias") if isinstance(b, dict) and isinstance(b.get("alias"), str) else index
            _diff(a, b, f"{path}[{label}]", out)
        return
    item: dict[str, Any] = {"path": path}
    if not SECRET_NAME_RE.search(path) and not isinstance(old, (dict, list)) and not isinstance(new, (dict, list)):
        item |= {"live": old, "profile": new}
    out.append(item)


def differences(old: dict[str, Any], new: dict[str, Any]) -> list[dict[str, Any]]:
    """Paths that differ, with scalar values unless the path names a secret."""
    out: list[dict[str, Any]] = []
    _diff(old, new, "", out)
    return out


def _string_leaves(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [s for v in value.values() for s in _string_leaves(v)]
    if isinstance(value, list):
        return [s for v in value for s in _string_leaves(v)]
    return [value] if isinstance(value, str) else []


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def export_profile(
    cfg: dict[str, Any], existing: dict[str, Any] | None = None, extra_secrets: list[str] | None = None
) -> dict[str, Any]:
    """The allowlisted, non-secret subset of cfg; profile-only sections come from `existing`."""
    cfg = migrate_config(copy.deepcopy(cfg))
    profile: dict[str, Any] = {"version": PROFILE_VERSION}
    for section, keys in MIRRORED.items():
        source = _mapping(cfg.get(section))
        picked = {key: copy.deepcopy(source[key]) for key in keys if key in source}
        if picked:
            profile[section] = picked
    enabled = enabled_models(cfg)
    profile["llm_server"] = {"enabled": _mapping(cfg.get("llm_server")).get("enabled", True)}
    if enabled:
        profile["llm_server"]["models"] = [model["alias"] for model in enabled]
        profile["models"] = [
            {key: copy.deepcopy(value) for key, value in model.items() if key in MODEL_KEYS} for model in enabled
        ]
    resources = _mapping(cfg.get("resources"))
    llm_resources = _mapping(resources.get("llm_server"))
    profile["resources"] = {"overrides": copy.deepcopy(resources.get("overrides") or {})}
    picked_resources = {key: llm_resources[key] for key in LLM_SERVER_RESOURCE_KEYS if key in llm_resources}
    if picked_resources:
        profile["resources"]["llm_server"] = picked_resources
    profile["boot"] = {"enable": _mapping(cfg.get("server")).get("start_at_boot", False) is True}
    existing = _mapping(existing)
    for section, default in PROFILE_ONLY_DEFAULTS.items():
        profile[section] = copy.deepcopy(existing.get(section) or default)

    secrets = [_mapping(cfg.get(a)).get(b) for a, b in SECRET_PATHS] + list(extra_secrets or [])
    secrets = [s for s in secrets if isinstance(s, str) and s]
    if any(secret in leaf for leaf in _string_leaves(profile) for secret in secrets):
        raise ConfigError("export would write a secret value into the profile; refusing")
    errors = validate_profile(profile)
    if errors:
        raise ConfigError("exported profile is invalid: " + "; ".join(errors))
    return profile


def load_profile(path: Path) -> dict[str, Any]:
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"profile not found: {path}") from exc
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"cannot read profile {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"profile root must be a mapping: {path}")
    return data


PROFILE_HEADER = (
    "# llm-env host profile (tracked, non-secret). Written by `uv run llmenv.py profile export`;\n"
    "# merged onto ~/.config/llm-env/models.yml by `make setup` and `make profile-apply`.\n"
    "# Every key is documented in llm-env.profile.example.yml. Never add secrets here.\n"
)


def write_profile(profile: dict[str, Any], path: Path) -> None:
    path = Path(path)
    content = PROFILE_HEADER + yaml.safe_dump(profile, sort_keys=False, default_flow_style=False)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.chmod(temporary, 0o644)  # tracked, non-secret file
        os.replace(temporary, path)
    except Exception:
        Path(temporary).unlink(missing_ok=True)
        raise
