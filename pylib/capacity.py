"""Measured-only capacity card per local model alias (Decisions 1-3 of the local-combo-and-capacity plan)."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from pylib.config import enabled_models

BENCH_PROMPT_TOKENS = 512  # bench-fit.sh's -p 512 on top of each depth
ALIAS_PATTERN = r"[a-z0-9][a-z0-9._-]{0,63}"
MEASURED_SECTIONS = ("depth", "ladder", "interference", "ram", "quants")
FLAG_KEYS = ("n_gpu_layers", "n_cpu_moe", "cache_type_k", "cache_type_v", "ubatch_size", "flash_attn")


class CapacityError(Exception):
    pass


def _entry(cfg: dict, alias: str) -> dict:
    if not isinstance(alias, str) or not re.fullmatch(ALIAS_PATTERN, alias):
        raise CapacityError(f"alias {alias!r} cannot name a capacity card (allowed: {ALIAS_PATTERN})")
    for model in cfg.get("models") or []:
        if isinstance(model, dict) and model.get("alias") == alias:
            return model
    raise CapacityError(f"alias {alias!r} is not in models.yml")


def card_path(config_path: Path, cfg: dict, alias: str) -> Path:
    _entry(cfg, alias)
    return Path(config_path).parent / "capacity" / f"{alias}.json"


def model_config(cfg: dict, alias: str) -> dict:
    model, runtime = _entry(cfg, alias), cfg.get("runtime") or {}
    return {
        "file": model.get("file"),
        "quantization": model.get("quantization"),
        "ctx_size": model.get("ctx_size"),
        "client_max_output_tokens": model.get("client_max_output_tokens"),
        "n_gpu_layers": model.get("n_gpu_layers"),
        "n_cpu_moe": model.get("n_cpu_moe") or None,  # 0 means no offload, as presets.py omits it
        "cache_type_k": runtime.get("cache_type_k"),
        "cache_type_v": runtime.get("cache_type_v"),
        "ubatch_size": runtime.get("ubatch_size"),
        "flash_attn": runtime.get("flash_attn"),
    }


def fingerprint(config: dict) -> str:
    return hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()[:16]


def read_input(path: str) -> dict:
    try:
        data = json.loads(Path(path).read_text())
    except json.JSONDecodeError as exc:
        raise CapacityError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise CapacityError(f"{path} is not a JSON object")
    return data


def _current(card: dict, name: str, fp: str) -> dict | None:
    section = card.get(name)
    return section if isinstance(section, dict) and section.get("fingerprint") == fp else None


def usable(card: dict, current_fingerprint: str, config: dict | None = None) -> dict:
    # `config` is the CURRENT models.yml view; the stored card["config"] may predate a revert or edit.
    depth = _current(card, "depth", current_fingerprint)
    measured = [p["depth"] for p in (depth or {}).get("points", []) if p.get("tg_tps") is not None]
    if not measured:
        return {"context_tokens": None, "max_output_tokens": None, "basis": "no current depth sweep"}
    gate = _current(card, "interference", current_fingerprint)
    if gate is not None and gate.get("passed") is False:
        return {"context_tokens": None, "max_output_tokens": None, "basis": "interference gate failed"}
    config, d = config or card["config"], max(measured)
    context = min(config["ctx_size"], d + BENCH_PROMPT_TOKENS)
    out = config.get("client_max_output_tokens")
    return {
        "context_tokens": context,
        "max_output_tokens": context if out is None else min(out, context),
        "basis": f"verified to depth {d} (interference: {'passed' if gate is not None else 'not measured'})",
    }


def _load(path: Path) -> dict | None:
    try:
        card = json.loads(path.read_text())
    except FileNotFoundError:
        return None
    except json.JSONDecodeError as exc:
        raise CapacityError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(card, dict) or not isinstance(card.get("config"), dict):
        raise CapacityError(f"{path} is not a capacity card")
    return card


def _now_iso(now: datetime | None) -> str:
    return (now or datetime.now(UTC)).isoformat(timespec="seconds")


def _depth_section(data: dict, config: dict, fp: str, at: str, source: str) -> dict:
    if not isinstance(data.get("tps"), list) or not isinstance(data.get("gpu"), dict):
        raise CapacityError(f"{source} is not a bench-fit.sh output")
    flags = data.get("flags")
    if not isinstance(flags, dict):
        raise CapacityError(f"{source} has no flags; re-run bench-fit.sh")
    if data.get("file") != config["file"]:
        raise CapacityError(f"{source} was measured with file {data.get('file')!r}, models.yml has {config['file']!r}")
    if not all(isinstance(p, dict) and isinstance(p.get("depth"), int) for p in data["tps"]):
        raise CapacityError(f"{source} has a tps point without an integer depth")
    for key in FLAG_KEYS:
        measured = (flags.get(key) or None) if key == "n_cpu_moe" else flags.get(key)
        if measured != config[key]:
            raise CapacityError(f"{source} was measured with {key}={measured!r}, models.yml has {config[key]!r}")
    gpu = data["gpu"]
    return {
        "fingerprint": fp, "written_at": at, "source": source,
        "points": sorted(
            ({"depth": p["depth"], "pp_tps": p.get("pp_tps"), "tg_tps": p.get("tg_tps")} for p in data["tps"]),
            key=lambda p: p["depth"],
        ),
        "model_vram_mib": gpu.get("model_vram_mib"),
        "peak_temp_c": gpu.get("peak_temp_c"),
        "avg_sclk_mhz": gpu.get("avg_sclk_mhz"),
        "limits": data.get("limits"),
    }


SCORE_KEYS = ("ladder", "ladder_at", "pass_rate", "overall", "tool_use", "invalid_runs")


def _ladder_section(score: dict, fp: str, at: str, source: str) -> dict:
    missing = [k for k in SCORE_KEYS if k not in score]
    if missing:
        raise CapacityError(f"{source} is not a score.sh output (missing {', '.join(missing)})")
    tool = score.get("tool_use") or {}
    return {
        "fingerprint": fp, "written_at": at, "source": source,
        "highest_layer": score.get("ladder"), "lowest_effort": score.get("ladder_at"),
        "pass_rate": score.get("pass_rate"), "overall_score": score.get("overall"),
        "tool_calls": tool.get("calls"), "tool_errors": tool.get("errors"),
        "tool_error_rate": tool.get("error_rate"), "invalid_runs": score.get("invalid_runs"),
    }


def write_card(
    config_path: Path,
    cfg: dict,
    alias: str,
    *,
    depth: dict | None = None,
    score: dict | None = None,
    interference: dict | None = None,
    sources: dict[str, str] | None = None,
    now: datetime | None = None,
) -> dict:
    path = card_path(config_path, cfg, alias)
    config = model_config(cfg, alias)
    fp, at, src = fingerprint(config), _now_iso(now), sources or {}
    card = _load(path) or {}
    card.update(schema=1, alias=alias, written_at=at, fingerprint=fp, config=config)
    bench = (_entry(cfg, alias).get("benchmark") or {}).get("vulkan")
    card["benchmark"] = (
        {k: bench.get(k) for k in ("pp_tps", "tg_tps", "measured_at")} if isinstance(bench, dict) else None
    )
    for name in MEASURED_SECTIONS:
        card.setdefault(name, None)
    if depth is not None:
        card["depth"] = _depth_section(depth, config, fp, at, src.get("depth") or "depth input")
    if score is not None:
        card["ladder"] = _ladder_section(score, fp, at, src.get("score") or "score input")
    if interference is not None:
        if not isinstance(interference.get("passed"), bool):
            raise CapacityError(
                f"{src.get('interference') or 'interference input'} is not a model-interference report (no boolean passed)"
            )
        gate, host, llm = interference, interference.get("host") or {}, interference.get("llm_server") or {}
        keys = ("passed", "failures", "notes", "system_one", "omniroute", "events_changed", "vram")
        card["interference"] = {
            "fingerprint": fp, "written_at": at, "source": src.get("interference") or "",
            **{k: gate.get(k) for k in keys},
        }
        card["ram"] = {
            "fingerprint": fp, "written_at": at,
            "llm_server_peak_mib": llm.get("memory_peak_mib"), "llm_server_cap_mib": llm.get("memory_cap_mib"),
            "host_available_min_mib": host.get("mem_available_min_mib"), "swap_delta_mib": host.get("swap_delta_mib"),
        }
    card["usable"] = usable(card, fp, config)
    path.parent.mkdir(mode=0o700, exist_ok=True)
    os.chmod(path.parent, 0o700)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{alias}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(card, handle, indent=2)
            handle.write("\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return _view(card, config, fp)


def _view(card: dict, config: dict, fp: str) -> dict:
    """The card as read against the current config: stale_sections and usable are never stored."""
    return {
        **card,
        "stale_sections": [
            n for n in MEASURED_SECTIONS if isinstance(card.get(n), dict) and card[n].get("fingerprint") != fp
        ],
        "usable": usable(card, fp, config),
    }


def read_card(config_path: Path, cfg: dict, alias: str) -> dict | None:
    card = _load(card_path(config_path, cfg, alias))
    if card is None:
        return None
    config = model_config(cfg, alias)
    return _view(card, config, fingerprint(config))


def current_usable(config_path: Path, cfg: dict, alias: str) -> dict:
    card = read_card(config_path, cfg, alias)
    return card["usable"] if card else {"context_tokens": None, "max_output_tokens": None, "basis": "no capacity card"}


def interference_passed(card: dict) -> bool | None:
    section = card.get("interference")
    # A card without stale_sections was not checked against the current config: not current.
    stale = card.get("stale_sections")
    if not isinstance(section, dict) or not isinstance(stale, list) or "interference" in stale:
        return None
    return section.get("passed")


def local_limits(config_path: Path, cfg: dict) -> dict[str, tuple[int, int]]:
    if not (cfg.get("llm_server") or {}).get("enabled"):
        return {}
    limits: dict[str, tuple[int, int]] = {}
    for model in enabled_models(cfg):
        alias = model.get("alias")
        if not isinstance(alias, str):
            continue
        try:
            now = current_usable(config_path, cfg, alias)
        except (CapacityError, OSError, ValueError, KeyError, TypeError, AttributeError):
            continue  # a corrupt or hand-edited card must never break the clients setup; ValueError covers JSON/Unicode
        if now["context_tokens"] is not None and now["max_output_tokens"] is not None:
            limits[alias] = (now["context_tokens"], now["max_output_tokens"])
    return limits
