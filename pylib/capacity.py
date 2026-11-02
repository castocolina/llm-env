"""Measured-only capacity card per local model alias (Decisions 1-3 of the local-combo-and-capacity plan)."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from pylib.config import enabled_models
from pylib.resources import HOST_MEMORY_FLOOR_MIB

MIB = 1048576
GATE_FIRST = "quant headroom needs a current depth sweep and a passed interference gate; finish the main sweep first"
BENCH_PROMPT_TOKENS = 512  # bench-fit.sh's -p 512 on top of each depth
ALIAS_PATTERN = r"[a-z0-9][a-z0-9._-]{0,63}"
MEASURED_SECTIONS = ("depth", "ladder", "interference", "ram", "quants", "profile")
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
    if gate is None:
        return {"context_tokens": None, "max_output_tokens": None, "basis": "interference not measured"}
    config, d = config or card["config"], max(measured)
    context = min(config["ctx_size"], d + BENCH_PROMPT_TOKENS)
    out = config.get("client_max_output_tokens")
    return {
        "context_tokens": context,
        "max_output_tokens": context if out is None else min(out, context),
        "basis": f"verified to depth {d} (interference: passed)",
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


def _profile_section(data: dict, config: dict, fp: str, at: str, source: str) -> dict:
    if data.get("kind") != "latency-profile" or not isinstance(data.get("runs"), list):
        raise CapacityError(f"{source} is not a latency-profile.sh output")
    for key, want in (("file", config["file"]), ("ctx_size", config["ctx_size"]),
                      ("client_max_output_tokens", config["client_max_output_tokens"])):
        if data.get(key) != want:
            raise CapacityError(f"{source} was measured with {key}={data.get(key)!r}, models.yml has {want!r}")
    keep = ("measured_at", "n_ctx", "output_tokens", "slack_tokens", "host_mem_total_mib", "idle", "runs", "skipped")
    return {"fingerprint": fp, "written_at": at, "source": source, **{k: data.get(k) for k in keep}}


def write_card(
    config_path: Path,
    cfg: dict,
    alias: str,
    *,
    depth: dict | None = None,
    score: dict | None = None,
    interference: dict | None = None,
    quants: dict | None = None,
    profile: dict | None = None,
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
        keys = ("passed", "failures", "notes", "regression_warning", "system_one", "omniroute", "events_changed", "vram")
        card["interference"] = {
            "fingerprint": fp, "written_at": at, "source": src.get("interference") or "",
            **{k: gate.get(k) for k in keys},
        }
        card["ram"] = {
            "fingerprint": fp, "written_at": at,
            "llm_server_peak_mib": llm.get("memory_peak_mib"), "llm_server_cap_mib": llm.get("memory_cap_mib"),
            "host_available_min_mib": host.get("mem_available_min_mib"), "swap_delta_mib": host.get("swap_delta_mib"),
        }
    if profile is not None:
        card["profile"] = _profile_section(profile, config, fp, at, src.get("profile") or "profile input")
    if quants is not None:
        gate = _current(card, "interference", fp)
        if _current(card, "depth", fp) is None or gate is None or gate.get("passed") is not True:
            raise CapacityError(GATE_FIRST)
        card["quants"] = {"fingerprint": fp, "written_at": at, **quants}
    card["usable"] = usable(card, fp, config)
    _save(path, card)
    return _view(card, config, fp)


def save_text(path: Path, text: str) -> None:
    """Atomic 0600 write inside the 0700 capacity directory."""
    path.parent.mkdir(mode=0o700, exist_ok=True)
    os.chmod(path.parent, 0o700)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(text)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _save(path: Path, card: dict) -> None:
    save_text(path, json.dumps(card, indent=2) + "\n")


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


def _weights_mib(entry: dict) -> int:
    return math.ceil(entry["size_bytes"] / MIB)


def _bpw(entry: dict) -> float | None:
    return round(entry["size_bytes"] * 8 / entry["params_total"], 2) if entry.get("params_total") else None


def _ram_verdict(ram: dict | None, delta: int) -> tuple[bool | None, str | None]:
    keys = ("llm_server_peak_mib", "llm_server_cap_mib", "host_available_min_mib")
    if not isinstance(ram, dict) or any(ram.get(k) is None for k in keys):
        return None, "ram not measured; run make model-interference"
    if ram["llm_server_peak_mib"] + delta > ram["llm_server_cap_mib"]:
        return False, "RAM: llm-server peak + delta over its cap"
    if ram["host_available_min_mib"] - delta < HOST_MEMORY_FLOOR_MIB:
        return False, f"RAM: host would drop below {HOST_MEMORY_FLOOR_MIB} MiB available"
    return True, None


def quant_headroom(
    *,
    ctx: int,
    model_vram_mib: int,
    current: dict,
    vram_available_mib: int,
    ram: dict | None,
    n_cpu_moe: int | None,
    candidates: list[dict],
) -> dict:
    """Decision 14: predicted VRAM = measured model VRAM + the weight-size delta against the tree's own sizes.

    A candidate carrying a `budget` block (`llmenv budget` on a scratch config) takes its `fits` from it; the formula stays beside it.
    """
    base = _weights_mib(current)
    rows = []
    for cand in candidates:
        row = {
            "quant": cand["quant"], "file": cand.get("file"), "size_bytes": cand.get("size_bytes"),
            "sha256": cand.get("sha256"), "weights_mib": None, "bpw": None, "delta_mib": None,
            "predicted_vram_mib": None, "vram_free_mib": None, "ram_ok": None, "fits": None, "reason": cand.get("reason"),
            "budget_required_mib": None, "budget_available_mib": None, "measured": None,
        }
        rows.append(row)
        if "size_bytes" not in cand:
            continue
        row["weights_mib"], row["bpw"] = _weights_mib(cand), _bpw(cand)
        if n_cpu_moe:
            row["reason"] = "not computed for n_cpu_moe offload"
            continue
        delta = row["weights_mib"] - base
        predicted = model_vram_mib + delta
        row.update(delta_mib=delta, predicted_vram_mib=predicted, vram_free_mib=vram_available_mib - predicted)
        row["ram_ok"], ram_reason = _ram_verdict(ram, delta)
        if ram_reason is not None and row["ram_ok"] is None:
            row["reason"] = ram_reason
        elif row["vram_free_mib"] < 0:
            row["fits"], row["reason"] = False, f"VRAM over budget by {-row['vram_free_mib']} MiB"
        elif row["ram_ok"] is False:
            row["fits"], row["reason"] = False, ram_reason
        else:
            row["fits"], row["reason"] = True, f"fits: {row['vram_free_mib']} MiB VRAM free"
        if budget := cand.get("budget"):  # `llmenv budget` on a scratch config is authoritative over the formula
            row["budget_required_mib"], row["budget_available_mib"] = budget["required_mib"], budget["available_mib"]
            row["fits"] = bool(budget["vram_feasible"] and budget["ram_feasible"])
            row["reason"] = (
                f"{'fits' if row['fits'] else 'over'} the budget gate: required {budget['required_mib']} MiB, "
                f"available {budget['available_mib']} MiB (formula predicted {predicted} MiB)"
            )
    free = vram_available_mib - model_vram_mib
    known_no_fit = bool(rows) and all(r["fits"] is False for r in rows)
    return {
        "ctx": ctx, "ref_depth": min(131072, ctx - BENCH_PROMPT_TOKENS), "vram_available_mib": vram_available_mib,
        "current": {
            "quant": current["quant"], "file": current["file"], "weights_mib": base, "bpw": _bpw(current),
            "sha256": current["sha256"],
        },
        "current_free_mib": free, "candidates": rows,
        "verdict": f"no headroom for {'/'.join(r['quant'] for r in rows)} at ctx {ctx}, free MiB {free}" if known_no_fit else None,
    }


def headroom_card(
    config_path: Path, cfg: dict, alias: str, *, vram_available_mib: int, current: dict, candidates: list[dict]
) -> dict:
    """quant_headroom over the card's current depth/ram sections, stored on the card."""
    card = read_card(config_path, cfg, alias)
    stale = (card or {}).get("stale_sections") or []
    depth = (card or {}).get("depth")
    if not card or not isinstance(depth, dict) or "depth" in stale or interference_passed(card) is not True:
        raise CapacityError(GATE_FIRST)
    ram = card.get("ram") if "ram" not in stale else None
    if depth.get("model_vram_mib") is None:
        raise CapacityError(GATE_FIRST)
    config = model_config(cfg, alias)  # the stored card["config"] may predate a revert or edit, as in `usable`
    result = quant_headroom(
        ctx=config["ctx_size"], model_vram_mib=depth["model_vram_mib"], current=current,
        vram_available_mib=vram_available_mib, ram=ram, n_cpu_moe=config.get("n_cpu_moe"), candidates=candidates,
    )
    return write_card(config_path, cfg, alias, quants=result)


def _at(section: dict | None, depth: int, key: str):
    return next((p.get(key) for p in (section or {}).get("points") or [] if p.get("depth") == depth), None)


def _diff(new, old):
    return None if new is None or old is None else round(new - old, 2)


def compare_quant(config_path: Path, cfg: dict, alias: str, candidate_alias: str) -> dict:
    """Fill the candidate's `measured` block in card A's quants section from the candidate's own card (values at ref_depth)."""
    path = card_path(config_path, cfg, alias)
    card, a, b = _load(path), read_card(config_path, cfg, alias), read_card(config_path, cfg, candidate_alias)
    if card is None or a is None or b is None:
        raise CapacityError(f"both capacity cards are needed; none for {alias if a is None else candidate_alias}")
    quants, stale_a, stale_b = a.get("quants"), a["stale_sections"], b["stale_sections"]
    if not isinstance(quants, dict) or "quants" in stale_a or "depth" in stale_a:
        raise CapacityError(f"the card of {alias} has no current quants and depth sections; run quant-headroom.sh check first")
    if not isinstance(b.get("depth"), dict) or "depth" in stale_b:
        raise CapacityError(f"the card of {candidate_alias} has no current depth sweep; run make model-capacity MODEL={candidate_alias}")
    file = model_config(cfg, candidate_alias)["file"]
    index = next((i for i, c in enumerate(quants["candidates"]) if c.get("file") == file), None)
    if index is None:
        raise CapacityError(f"no quant candidate with file {file} in the card of {alias}; run quant-headroom.sh check first")
    ref = quants["ref_depth"]
    ladder_a = None if "ladder" in stale_a else a.get("ladder")
    ladder_b = None if "ladder" in stale_b else b.get("ladder")
    ladder_a, ladder_b = ladder_a or {}, ladder_b or {}
    entry = quants["candidates"][index]
    card["quants"]["candidates"][index]["measured"] = {
        "alias": candidate_alias, "quant": entry["quant"], "bpw": entry.get("bpw"), "ctx": model_config(cfg, candidate_alias)["ctx_size"],
        "ref_depth": ref, "points": b["depth"]["points"], "model_vram_mib": b["depth"].get("model_vram_mib"),
        "interference_passed": interference_passed(b), "highest_layer": ladder_b.get("highest_layer"),
        "overall_score": ladder_b.get("overall_score"), "pass_rate": ladder_b.get("pass_rate"),
        "delta": {
            "pp_at_ref": _diff(_at(b["depth"], ref, "pp_tps"), _at(a["depth"], ref, "pp_tps")),
            "tg_at_ref": _diff(_at(b["depth"], ref, "tg_tps"), _at(a["depth"], ref, "tg_tps")),
            "model_vram_mib": _diff(b["depth"].get("model_vram_mib"), a["depth"].get("model_vram_mib")),
            "highest_layer": _diff(ladder_b.get("highest_layer"), ladder_a.get("highest_layer")),
            "overall_score": _diff(ladder_b.get("overall_score"), ladder_a.get("overall_score")),
        },
    }
    _save(path, card)
    return read_card(config_path, cfg, alias) or {}
