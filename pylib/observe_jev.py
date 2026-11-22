"""Incremental Prometheus metrics for the local Jev answer log."""

from __future__ import annotations

import json
import math
import os
import re
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from pylib.observe_usage import _safe_ts

LOG_PATH = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "ai-kit/jev-answers.jsonl"
STATE_PATH = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "llm-env/observe/jev-state.json"
BUCKETS = (0.3, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 0.99, 1.0)
HELP = {
    "llm_env_jev_decisions_total": "Jev decisions by backend, type, caller and outcome.",
    "llm_env_jev_fallback_needed_total": "Answered decisions where needs_fallback is true.",
    "llm_env_jev_top_out_of_range_total": "Jev decisions with top confidence outside [0, 1].",
    "llm_env_jev_top_confidence": "Jev self-estimated top confidence (not accuracy).",
    "llm_env_jev_last_decision_timestamp_seconds": "Newest Jev decision timestamp by backend.",
    "llm_env_jev_skipped_rows_total": "Malformed Jev log rows skipped.",
    "llm_env_jev_log_resets_total": "Jev log rotations or truncations detected.",
}


def _valid_state(doc: Any) -> bool:
    if not isinstance(doc, dict) or set(doc) - {"jev", "jev_callers", "jev_callers_final", "jev_callers_initial_count", "caller_counts", "c", "h", "last"}:
        return False
    for key in ("jev", "jev_callers", "caller_counts", "c", "h", "last"):
        if doc.get(key) is None:
            doc.pop(key, None)
    if doc.get("jev_callers_final") is not None and not isinstance(doc["jev_callers_final"], bool):
        return False
    initial_count = doc.get("jev_callers_initial_count")
    if initial_count is not None and (not isinstance(initial_count, int) or isinstance(initial_count, bool) or initial_count < 0):
        return False
    cursor = doc.get("jev", {"inode": None, "offset": 0})
    if cursor is None:
        cursor = doc["jev"] = {"inode": None, "offset": 0}
    if isinstance(cursor, dict):
        if cursor.get("inode", None) is None:
            cursor["inode"] = None
        if cursor.get("offset") is None:
            if doc.get("c") or doc.get("h") or doc.get("last"):
                return False
            cursor["offset"] = 0
    if (not isinstance(cursor, dict) or set(cursor) != {"inode", "offset"}
            or cursor["inode"] is not None and (not isinstance(cursor["inode"], int) or isinstance(cursor["inode"], bool))
            or not isinstance(cursor["offset"], int) or isinstance(cursor["offset"], bool) or cursor["offset"] < 0):
        return False
    callers = doc.get("jev_callers", [])
    if not isinstance(callers, list) or len(callers) > 12 or any(not isinstance(x, str) for x in callers):
        return False
    for key in ("c", "h", "last"):
        if doc.get(key) is not None and not isinstance(doc[key], dict):
            return False
    counters = doc.get("c", {})
    if any(not isinstance(name, str) or not isinstance(family, dict)
           or any(not isinstance(k, str) or not isinstance(v, (int, float)) or isinstance(v, bool)
                  or not math.isfinite(v) or v < 0 for k, v in family.items())
           for name, family in counters.items()):
        return False
    histograms = doc.get("h", {})
    for name, family in histograms.items():
        if not isinstance(name, str) or not isinstance(family, dict):
            return False
        for key, hist in family.items():
            if (not isinstance(key, str) or not isinstance(hist, dict)
                    or set(hist) != {"b", "s", "n"} or not isinstance(hist["b"], list)
                    or len(hist["b"]) != len(BUCKETS) + 1
                    or any(not isinstance(x, (int, float)) or isinstance(x, bool) or not math.isfinite(x) or x < 0 for x in hist["b"])
                    or not isinstance(hist["s"], (int, float)) or isinstance(hist["s"], bool) or not math.isfinite(hist["s"])
                    or not isinstance(hist["n"], int) or isinstance(hist["n"], bool) or hist["n"] < 0):
                return False
    last = doc.get("last", {})
    if any(not isinstance(k, str) or not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v)
           for k, v in last.items()):
        return False
    counts = doc.get("caller_counts", {})
    return isinstance(counts, dict) and all(isinstance(k, str) and isinstance(v, int) and not isinstance(v, bool) and v >= 0 for k, v in counts.items())


def _load(path: Path) -> dict | None:
    try:
        doc = json.loads(path.read_text())
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeError, ValueError):
        return None
    return doc if _valid_state(doc) else None


def _save(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    raw = json.dumps(state, sort_keys=True, separators=(",", ":"))
    try:
        if path.read_text() == raw:
            return
    except FileNotFoundError:
        pass
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def classify_jev_row(row: Any, latest_allowed: datetime) -> tuple[datetime, dict[str, Any]] | None:
    """Return the live collector's validated, privacy-safe row classification."""
    if not isinstance(row, dict):
        return None
    stamp = _safe_ts(row.get("ts"))
    if stamp is None or stamp > latest_allowed:
        return None
    raw_backend, raw_type = row.get("backend"), row.get("type")
    backend = raw_backend if isinstance(raw_backend, str) and raw_backend in ("laya", "typesafe") else "unknown"
    typ = raw_type if isinstance(raw_type, str) and raw_type in ("choice", "noul", "score") else "unknown"
    raw_caller = row.get("caller")
    caller = raw_caller if isinstance(raw_caller, str) else "unknown"
    if not caller:
        caller = "other"
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,40}", caller):
        caller = "other"
    error = row.get("error")
    has_error = (isinstance(error, str) and bool(error.strip())) or (isinstance(error, (dict, list)) and bool(error))
    top_value = None
    top_out_of_range = False
    top = row.get("top")
    if isinstance(top, (int, float)) and not isinstance(top, bool):
        try:
            value = float(top)
        except (OverflowError, ValueError):
            value = math.nan
        if not math.isfinite(value) or not 0 <= value <= 1:
            top_out_of_range = True
        else:
            top_value = value
    return stamp, {"backend": backend, "type": typ, "caller": caller,
                   "outcome": "error" if has_error else "answered",
                   "fallback": not has_error and row.get("needs_fallback") is True,
                   "top": top_value, "top_out_of_range": top_out_of_range}


def _key(labels: dict[str, str]) -> str:
    return json.dumps(sorted(labels.items()), separators=(",", ":"))


def _count(state: dict, name: str, labels: dict[str, str], n: int = 1) -> None:
    fam = state.setdefault("c", {}).setdefault(name, {})
    key = _key(labels)
    fam[key] = fam.get(key, 0) + n


def _observe(state: dict, labels: dict[str, str], value: float) -> None:
    fam = state.setdefault("h", {}).setdefault("llm_env_jev_top_confidence", {})
    hist = fam.setdefault(_key(labels), {"b": [0] * (len(BUCKETS) + 1), "s": 0.0, "n": 0})
    hist["b"][next((i for i, bound in enumerate(BUCKETS) if value <= bound), len(BUCKETS))] += 1
    hist["s"] += value
    hist["n"] += 1


def _render(state: dict, up: int) -> str:
    lines = []
    for name, family in state.get("c", {}).items():
        if not family:
            continue
        lines += [f"# HELP {name} {HELP[name]}", f"# TYPE {name} counter"]
        merged = {}
        allowed = set(state.get("jev_callers", [])) | {"direct"}
        for key, value in family.items():
            labels = dict(json.loads(key))
            if "caller" in labels and labels["caller"] not in allowed:
                labels["caller"] = "other"
            rendered = "{" + ",".join(f'{k}="{labels[k]}"' for k in ("backend", "type", "caller", "outcome") if k in labels) + "}" if labels else ""
            merged[rendered] = merged.get(rendered, 0) + value
        if name in ("llm_env_jev_log_resets_total", "llm_env_jev_skipped_rows_total"):
            merged = {"": sum(merged.values())}
        lines.extend(f"{name}{labels} {value}" for labels, value in merged.items())
    family = state.get("h", {}).get("llm_env_jev_top_confidence", {})
    if family:
        name = "llm_env_jev_top_confidence"
        lines += [f"# HELP {name} {HELP[name]}", f"# TYPE {name} histogram"]
        for key, hist in family.items():
            labels = dict(json.loads(key))
            inner = ",".join(f'{k}="{v}"' for k, v in labels.items())
            running = 0
            for bound, count in zip((*BUCKETS, "+Inf"), hist["b"], strict=True):
                running += count
                lines.append(f'{name}_bucket{{{inner},le="{bound}"}} {running}')
            lines += [f'{name}_sum{{{inner}}} {hist["s"]}', f'{name}_count{{{inner}}} {hist["n"]}']
    if state.get("last"):
        name = "llm_env_jev_last_decision_timestamp_seconds"
        lines += [f"# HELP {name} {HELP[name]}", f"# TYPE {name} gauge"]
        for backend, stamp in state["last"].items():
            lines.append(f"{name}{{backend=\"{backend}\"}} {stamp}")
    lines += [f"llm_env_source_up{{source=\"jev_log\"}} {up}", "llm_env_source_up{source=\"jev_state\"} 1"]
    return "\n".join(lines) + "\n"


def collect_jev(*, log_path: Path | None = None, state_path: Path | None = None, deadline_s: float = 10,
                clock: Any = time.time, monotonic: Any = time.monotonic) -> str:
    state_path = state_path or Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "llm-env/observe/jev-state.json"
    log_path = log_path or Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "ai-kit/jev-answers.jsonl"
    state = _load(state_path)
    if state is None:
        return 'llm_env_source_up{source="jev_state"} 0\n'
    if "jev" not in state and (state.get("c") or state.get("h") or state.get("last")):
        return 'llm_env_source_up{source="jev_state"} 0\n'
    try:
        stream = log_path.open("rb")
        stat = os.fstat(stream.fileno())
    except OSError:
        return _render(state, 0).replace('source="jev_log\"} 1', 'source="jev_log\"} 0')
    cursor = state.setdefault("jev", {"inode": None, "offset": 0})
    for optional in ("c", "h", "last"):
        if state.get(optional) is None:
            state.pop(optional, None)
    if cursor["inode"] is not None and (cursor["inode"] != stat.st_ino or stat.st_size < cursor["offset"]):
        cursor["offset"] = 0
        fam = state.setdefault("c", {}).setdefault("llm_env_jev_log_resets_total", {})
        fam["[]"] = fam.get("[]", 0) + 1
    cursor["inode"] = stat.st_ino
    deadline = monotonic() + min(10.0, max(0.0, deadline_s))
    latest_allowed = datetime.fromtimestamp(clock(), UTC) + timedelta(seconds=300)
    with stream:
        stream.seek(cursor["offset"])
        while monotonic() < deadline:
            line_start = stream.tell()
            raw = stream.readline(max(0, stat.st_size - line_start))
            if not raw or not raw.endswith(b"\n"):
                stream.seek(line_start)
                break
            cursor["offset"] = stream.tell()
            try:
                row = json.loads(raw)
                classified = classify_jev_row(row, latest_allowed)
                if classified is None:
                    raise ValueError
                stamp, item = classified
                backend, typ, raw_caller = item["backend"], item["type"], item["caller"]
                if not state.get("jev_callers_final", False):
                    counts = state.setdefault("caller_counts", {})
                    counts[raw_caller] = counts.get(raw_caller, 0) + 1
                allowed = state.get("jev_callers")
                caller = raw_caller if allowed is None or raw_caller == "direct" or raw_caller in allowed else "other"
                labels = {"backend": backend, "type": typ, "caller": caller, "outcome": item["outcome"]}
                _count(state, "llm_env_jev_decisions_total", labels)
                if item["fallback"]:
                    _count(state, "llm_env_jev_fallback_needed_total", {k: labels[k] for k in ("backend", "type", "caller")})
                if item["top_out_of_range"]:
                    _count(state, "llm_env_jev_top_out_of_range_total", {})
                elif item["top"] is not None:
                    _observe(state, {"backend": backend, "type": typ}, item["top"])
                if backend != "unknown":
                    seconds = stamp.timestamp()
                    state.setdefault("last", {})[backend] = max(seconds, state.setdefault("last", {}).get(backend, seconds))
            except Exception:  # noqa: BLE001 -- every malformed complete line advances and is counted once
                _count(state, "llm_env_jev_skipped_rows_total", {})
    decisions = sum(sum(family.values()) for name, family in state.get("c", {}).items()
                    if name == "llm_env_jev_decisions_total")
    if state.get("jev_callers") is None and decisions > 0:
        ranked = sorted(state.get("caller_counts", {}).items(), key=lambda item: (-item[1], item[0]))
        state["jev_callers"] = ["direct", *[name for name, _ in ranked if name != "direct"][:11]]
        state["jev_callers_initial_count"] = decisions
        state["jev_callers_final"] = decisions >= 50
        # shortcut: rows counted before the one-time allowlist rebuild stay labelled other
    if (state.get("jev_callers") is not None and not state.get("jev_callers_final", False)
            and decisions >= 500):
        ranked = sorted(state.get("caller_counts", {}).items(), key=lambda item: (-item[1], item[0]))
        state["jev_callers"] = ["direct", *[name for name, _ in ranked if name != "direct"][:11]]
        state["jev_callers_final"] = True
    if state.get("jev_callers_final"):
        state.pop("caller_counts", None)
    try:
        _save(state_path, state)
    except Exception:  # noqa: BLE001 -- state failure is reported without exposing exception data
        return _render(state, 1).replace('source="jev_state\"} 1', 'source="jev_state\"} 0')
    return _render(state, 1)
