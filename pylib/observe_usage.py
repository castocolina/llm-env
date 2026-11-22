"""OmniRoute usage and Laya decision metrics.

Both sources fold recent events into cumulative counters/histograms kept in a small private state file. Each collection folds the
new events into cumulative counters/histograms kept in a small private state file. A cursor on the
event timestamp prevents double counting across the 60 s collections. Labels are fixed low-cardinality
fields only; prompts, responses and identifiers are never read into the state.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from pylib.omniroute_activity import bounded_get, bounded_login

STATE_PATH = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "llm-env/observe/usage-state.json"
LAYA_SPANS_PATH = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "llm-env/laya-spans/laya-spans.jsonl"


def _state_path(name: str) -> Path:
    return Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "llm-env" / name
MAX_LAYA_BYTES = 256 * 1024
MAX_LAYA_RECORDS = 1000
MAX_LAYA_LINE = 8192
OMNI_MS = (250, 500, 1000, 2000, 4000, 8000, 16000, 32000, 64000)
LAYA_MS = (10, 25, 50, 100, 250, 500, 1000, 2500, 10000)
CONFIDENCE = (0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 0.99)
# shortcut: a request running longer than the 600 s lookback is not counted late, raise LOOKBACK_S if durations grow
LOOKBACK_S = 600
_HELP = {
    "llm_env_omniroute_requests_total": "OmniRoute lifetime requests by provider and model (usage history).",
    "llm_env_omniroute_tokens_total": "OmniRoute lifetime tokens by provider, model and kind (prompt, completion, cached).",
    "llm_env_omniroute_cost_usd_total": "OmniRoute lifetime estimated cost in USD by provider and model.",
    "llm_env_omniroute_calls_total": "Client calls seen in the call log by route (combo or direct), model and status class; probes excluded.",
    "llm_env_usage_gap_total": "OmniRoute call-log fetches where the bounded response may have omitted unseen requests.",
    "llm_env_usage_skipped_rows_total": "Malformed usage rows skipped during collection.",
    "llm_env_omniroute_ttft_ms": "Time to first token per client call, milliseconds (only calls that report it).",
    "llm_env_omniroute_duration_ms": "Total client call duration, milliseconds.",
    "llm_env_laya_requests_total": "Laya requests by endpoint, batch and HTTP status class.",
    "llm_env_laya_duration_ms": "Laya request duration, milliseconds.",
    "llm_env_laya_answers_total": "Laya answers by decision type (noul, choice, score).",
    "llm_env_laya_confidence": "Laya reported answer confidence by decision type; a self-estimate, not measured accuracy.",
    "llm_env_laya_skipped_records_total": "Invalid or oversized Laya span records skipped by reason.",
}


def _key(labels: Mapping[str, str]) -> str:
    return json.dumps(sorted(labels.items()))


def _count(state: dict, name: str, labels: Mapping[str, str], n: float = 1) -> None:
    fam = state.setdefault("c", {}).setdefault(name, {})
    fam[_key(labels)] = fam.get(_key(labels), 0) + n


def _observe(state: dict, name: str, labels: Mapping[str, str], value: float, bounds: tuple) -> None:
    fam = state.setdefault("h", {}).setdefault(name, {})
    hist = fam.setdefault(_key(labels), {"b": [0] * (len(bounds) + 1), "s": 0.0})
    hist["b"][next((i for i, le in enumerate(bounds) if value <= le), len(bounds))] += 1
    hist["s"] += value


def _fmt_labels(pairs: list[tuple[str, str]], extra: str = "") -> str:
    inner = ",".join(f'{k}="{str(v).replace(chr(92), "").replace(chr(34), "")}"' for k, v in pairs)
    return "{" + inner + (("," if inner else "") + extra if extra else "") + "}"


def render_state(state: Mapping[str, Any]) -> list[str]:
    out: list[str] = []
    emitted: set[str] = set()
    for name, fam in (state.get("c") or {}).items():
        if name not in emitted:
            out += [f"# HELP {name} {_HELP.get(name, name)}", f"# TYPE {name} counter"]
            emitted.add(name)
        out += [f"{name}{_fmt_labels(json.loads(k))} {v}" for k, v in fam.items()]
    bounds_of = {"llm_env_omniroute_ttft_ms": OMNI_MS, "llm_env_omniroute_duration_ms": OMNI_MS,
                 "llm_env_laya_duration_ms": LAYA_MS, "llm_env_laya_confidence": CONFIDENCE}
    for name, fam in (state.get("h") or {}).items():
        if name not in emitted:
            out += [f"# HELP {name} {_HELP.get(name, name)}", f"# TYPE {name} histogram"]
            emitted.add(name)
        for k, hist in fam.items():
            pairs, running = json.loads(k), 0
            for le, n in zip((*bounds_of[name], "+Inf"), hist["b"], strict=True):
                running += n
                out.append(f'{name}_bucket{_fmt_labels(pairs, "le=" + chr(34) + str(le) + chr(34))} {running}')
            out += [f"{name}_sum{_fmt_labels(pairs)} {hist['s']}", f"{name}_count{_fmt_labels(pairs)} {running}"]
    return out


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _allowlist(history: Mapping[str, Any]) -> list[str]:
    by_model = history.get("byModel") if isinstance(history, Mapping) else None
    if not isinstance(by_model, Mapping):
        return []
    if any(not isinstance(entry, Mapping) for entry in by_model.values()):
        return []
    ranked = []
    for entry in by_model.values():
        if entry.get("rawModel") and _number(entry.get("requests")):
            ranked.append((entry["requests"], str(entry["rawModel"])))
    return list(dict.fromkeys(model for _, model in sorted(ranked, reverse=True)[:40]))


def history_lines(history: Mapping[str, Any], allow: Sequence[str] | None = None) -> list[str]:
    """Authoritative lifetime counters from /api/usage/history (byModel entries)."""
    by_model = history.get("byModel") if isinstance(history, Mapping) else None
    if not isinstance(by_model, Mapping):
        return []
    # Legacy model series stay frozen; new unlisted calls roll up to other.
    allowed = set(allow if allow is not None else _allowlist(history))
    totals: dict[tuple[str, str, str], float] = {}
    incomplete: set[tuple[str, str]] = set()
    if any(not isinstance(entry, Mapping) for entry in by_model.values()):
        return []
    for entry in by_model.values():
        provider = str(entry.get("provider") or "unknown")
        raw_model = str(entry.get("rawModel") or "unknown")
        model = raw_model if raw_model in allowed else "other"
        for metric, field, kind in (("requests", "requests", ""), ("cost", "cost", ""),
                                    ("prompt", "promptTokens", "prompt"), ("completion", "completionTokens", "completion"),
                                    ("cached", "cachedInputTokens", "cached")):
            value = entry.get(field)
            if model == "other" and not _number(value):
                incomplete.add((provider, metric))
            if not _number(value):
                continue
            key = (metric, provider, model)
            totals[key] = totals.get(key, 0) + value
    for provider, metric in incomplete:
        totals.pop((metric, provider, "other"), None)
    groups = {
        "llm_env_omniroute_requests_total": [("provider", p, "model", m, v) for (metric, p, m), v in totals.items() if metric == "requests"],
        "llm_env_omniroute_cost_usd_total": [("provider", p, "model", m, v) for (metric, p, m), v in totals.items() if metric == "cost"],
        "llm_env_omniroute_tokens_total": [("provider", p, "model", m, kind, v) for (metric, p, m), v in totals.items() if metric in ("prompt", "completion", "cached") for kind in [metric]],
    }
    out: list[str] = []
    for name, values in groups.items():
        lines = []
        for item in values:
            if name.endswith("tokens_total"):
                p, provider, m, model, kind, value = item
                labels = [(p, provider), (m, model), ("kind", kind)]
            else:
                p, provider, m, model, value = item
                labels = [(p, provider), (m, model)]
            lines.append(f"{name}{_fmt_labels(labels)} {value}")
        if lines:
            out += [f"# HELP {name} {_HELP[name]}", f"# TYPE {name} counter", *lines]
    return out


def _safe_ts(value: Any) -> datetime | None:
    try:
        if not isinstance(value, str):
            return None
        stamp = datetime.fromisoformat(value)
        return (stamp.replace(tzinfo=UTC) if stamp.tzinfo is None else stamp).astimezone(UTC)
    except Exception:  # noqa: BLE001 -- malformed timestamps must not stall collection
        return None


def _dt(value: str) -> datetime:
    stamp = _safe_ts(value)
    if stamp is None:
        raise ValueError("invalid timestamp")
    return stamp


def _fold(state: dict, cursor_key: str, events: list[tuple[str, str, Any]], lookback: int = 0,
          now: datetime | None = None) -> list[Any]:
    """Fold timestamped events once, retaining IDs within the late-arrival window."""
    cur = state.get(cursor_key) or {"ts": "", "seen": {}}
    ts = cur.get("ts", "")
    invalid_cursor = bool(ts) and (not isinstance(ts, str) or _safe_ts(ts) is None)
    if invalid_cursor:
        ts = ""
        cur = {"ts": "", "seen": {}}
    seen = cur.get("seen")
    legacy = not isinstance(seen, dict) and isinstance(cur.get("ids"), list)
    if legacy:
        seen = dict.fromkeys(cur["ids"], ts)
    if not isinstance(seen, dict):
        seen = {}
    seen = {key: val for key, val in seen.items() if isinstance(val, str)}
    newest = _safe_ts(ts)
    now = now or datetime.now(UTC)
    threshold = newest.timestamp() - lookback if newest and not legacy else float("-inf")
    if invalid_cursor:
        valid_stamps = [_safe_ts(event_ts) for event_ts, _, _ in events]
        valid_stamps = [stamp for stamp in valid_stamps if stamp is not None]
        threshold = max(valid_stamps).timestamp() - lookback if valid_stamps else float("-inf")
    fresh = []
    for event_ts, event_id, payload in events:
        stamp = _safe_ts(event_ts)
        if stamp is None or stamp > now + timedelta(seconds=300):
            continue
        already_counted = bool(legacy and newest and (stamp < newest or (stamp == newest and event_id in seen)))
        if event_id not in seen and not already_counted and stamp.timestamp() >= threshold:
            fresh.append((stamp, event_ts, event_id, payload))
            seen[event_id] = event_ts
        elif legacy and already_counted:
            seen[event_id] = event_ts
    if fresh:
        newest = max([newest, *(e[0] for e in fresh)]) if newest else max(e[0] for e in fresh)
        newest = min(newest, now)
        ts = newest.isoformat().replace("+00:00", "Z")
    if newest:
        prune_before = newest.timestamp() - lookback - 60
        seen = {key: val for key, val in seen.items() if isinstance(val, str) and (_safe_ts(val) or datetime.min.replace(tzinfo=UTC)).timestamp() >= prune_before}
    state[cursor_key] = {"ts": ts, "seen": seen}
    return [event[3] for event in fresh]


def _skipped(state: dict, source: str, row: Any) -> None:
    identity = row.get("id") if isinstance(row, Mapping) else None
    if not isinstance(identity, (str, int, float)) or isinstance(identity, str) and not identity:
        identity = hashlib.sha1(json.dumps(row, sort_keys=True, default=str)[:200].encode()).hexdigest()[:12]
    key = f"{source}:{identity}"
    seen = state.setdefault("skipped_seen", [])
    if key not in seen:
        _count(state, "llm_env_usage_skipped_rows_total", {"source": source})
        seen.append(key)
        del seen[:-200]


def ingest_call_logs(state: dict, rows: Sequence[Mapping[str, Any]], now: datetime | None = None) -> None:
    if not isinstance(state.get("allow"), list) or not state["allow"]:
        return
    events = []
    for row in rows:
        try:
            if not isinstance(row, Mapping) or row.get("id") is None or row.get("id") == "" or not isinstance(row.get("status"), int) or isinstance(row.get("status"), bool):
                _skipped(state, "omniroute", row)
                continue
            stamp = _safe_ts(row.get("timestamp"))
            if stamp is None or stamp > (now or datetime.now(UTC)) + timedelta(seconds=300):
                _skipped(state, "omniroute", row)
                continue
            if row["status"] > 0 and not row.get("requestType"):
                events.append((str(row["timestamp"]), str(row["id"]), row))
        except Exception:  # noqa: BLE001 -- isolate malformed call-log rows
            _skipped(state, "omniroute", row)
    for row in _fold(state, "omni_cursor", events, lookback=600, now=now):
        combo = row.get("comboName")
        routes = state.setdefault("routes", [])
        route = "direct" if not combo else str(combo)
        if combo and route not in routes:
            # 20 named routes plus direct and other keeps route cardinality at 22.
            if len(routes) < 20:
                routes.append(route)
            else:
                route = "other"
        model = str(row.get("model") or "unknown")
        allow = state["allow"]
        if model not in allow:
            model = "other"
        _count(state, "llm_env_omniroute_calls_total",
               {"route": route, "model": model, "status": f"{row['status'] // 100}xx"})
        for name, field in (("llm_env_omniroute_ttft_ms", "ttft"), ("llm_env_omniroute_duration_ms", "duration")):
            value = row.get(field)
            if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
                _observe(state, name, {"route": route, "model": model}, value, OMNI_MS)


def _laya_record(record: Any) -> tuple[dict, str | None]:
    if not isinstance(record, dict) or type(record.get("v")) is not int or record["v"] != 1:
        return {}, "version"
    stamp = _safe_ts(record.get("ts"))
    if stamp is None or not isinstance(record.get("ts"), str) or not record["ts"].endswith("Z"):
        return {}, "timestamp"
    endpoint = record.get("endpoint")
    if endpoint not in ("/v1/systemone", "/v1/systemone/batch"):
        return {}, "endpoint"
    status, duration = record.get("http_status"), record.get("duration_ms")
    if not isinstance(status, int) or isinstance(status, bool) or not 100 <= status <= 599:
        return {}, "status"
    if not isinstance(duration, (int, float)) or isinstance(duration, bool) or not math.isfinite(duration) or not 0 <= duration <= 3.6e6:
        return {}, "duration"
    answers, confs = record.get("answers"), record.get("confidences")
    if not isinstance(answers, dict) or any(k not in ("noul", "choice", "score") or not isinstance(v, int) or isinstance(v, bool) or v < 0 for k, v in answers.items()):
        return {}, "answers"
    if not isinstance(confs, list) or len(confs) > 256 or any(not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v) or not 0 <= v <= 1 for v in confs):
        return {}, "confidences"
    if not isinstance(record.get("span_id"), str) or re.fullmatch(r"[0-9a-f]{16}", record["span_id"]) is None:
        return {}, "span_id"
    batch = record.get("batch")
    if not isinstance(batch, bool) or batch != endpoint.endswith("/batch"):
        return {}, "batch"
    return {"endpoint": endpoint, "batch": str(batch).lower(), "status": f"{status // 100}xx",
            "duration": float(duration), "answers": answers, "confidences": confs}, None


def _ingest_laya_record(state: dict, item: Mapping[str, Any]) -> None:
    _count(state, "llm_env_laya_requests_total", {k: item[k] for k in ("endpoint", "batch", "status")})
    _observe(state, "llm_env_laya_duration_ms", {"endpoint": item["endpoint"]}, item["duration"], LAYA_MS)
    types = [typ for typ in ("noul", "choice", "score") if item["answers"].get(typ, 0)]
    if len(types) == 1:
        for value in item["confidences"]:
            _observe(state, "llm_env_laya_confidence", {"type": types[0]}, float(value), CONFIDENCE)
    else:
        offset = 0
        for typ in ("noul", "choice", "score"):
            count = item["answers"].get(typ, 0)
            for value in item["confidences"][offset:offset + count]:
                _observe(state, "llm_env_laya_confidence", {"type": typ}, float(value), CONFIDENCE)
            offset += count
    for typ, count in item["answers"].items():
        _count(state, "llm_env_laya_answers_total", {"type": typ}, count)


def collect_laya_file(state: dict, path: Path, *, deadline_s: float = 10,
                      monotonic: Any = time.monotonic) -> int:
    """Fold bounded complete JSONL records without retaining source text."""
    try:
        stat = path.stat()
        if not path.is_file():
            return int(state.get("laya_file_cursor") is not None)
        inode = stat.st_ino
        cursor = state.get("laya_file_cursor", {"inode": None, "offset": 0})
        old_inode, old_offset = cursor.get("inode"), cursor.get("offset", 0)
        rotated = path.with_name(path.name + ".1")
        start, bytes_left, records_left = monotonic(), MAX_LAYA_BYTES, MAX_LAYA_RECORDS
        discarding = bool(cursor.get("discarding", False))
        seen_ids = set(state.get("laya_file_seen", []))

        def consume(file_path: Path, offset: int, expected_inode: int | None) -> int:
            nonlocal bytes_left, records_left, discarding
            with file_path.open("rb") as stream:
                st = os.fstat(stream.fileno())
                if expected_inode is not None and st.st_ino != expected_inode or st.st_size < offset:
                    offset = 0
                stream.seek(offset)
                if discarding:
                    while bytes_left > 0 and stream.tell() < st.st_size:
                        chunk = stream.readline(min(bytes_left, MAX_LAYA_LINE + 1))
                        if not chunk:
                            break
                        bytes_left -= len(chunk)
                        if chunk.endswith(b"\n"):
                            discarding = False
                            break
                    if discarding:
                        return stream.tell()
                while bytes_left > 0 and records_left > 0 and monotonic() - start < deadline_s:
                    line_start = stream.tell()
                    remaining = st.st_size - line_start
                    if remaining > MAX_LAYA_LINE:
                        probe = stream.read(MAX_LAYA_LINE + 1)
                        if b"\n" not in probe:
                            stream.seek(line_start)
                            budget_end = line_start + bytes_left
                            consumed = 0
                            chunk = b""
                            while stream.tell() < budget_end:
                                chunk = stream.readline(min(budget_end - stream.tell(), MAX_LAYA_LINE + 1))
                                if not chunk:
                                    break
                                consumed += len(chunk)
                                if chunk.endswith(b"\n"):
                                    break
                            bytes_left -= consumed
                            discarding = not chunk.endswith(b"\n") if consumed else True
                            _count(state, "llm_env_laya_skipped_records_total", {"reason": "too_long"})
                            records_left -= 1
                            continue
                    stream.seek(line_start)
                    raw = stream.readline(min(MAX_LAYA_LINE + 1, bytes_left + 1))
                    if not raw or not raw.endswith(b"\n"):
                        stream.seek(line_start)
                        break
                    bytes_left -= len(raw)
                    records_left -= 1
                    if len(raw) > MAX_LAYA_LINE:
                        _count(state, "llm_env_laya_skipped_records_total", {"reason": "too_long"})
                        continue
                    try:
                        record = json.loads(raw)
                        item, reason = _laya_record(record)
                        if reason:
                            _count(state, "llm_env_laya_skipped_records_total", {"reason": reason})
                        else:
                            seen = state.setdefault("laya_file_seen", [])
                            if record["span_id"] not in seen_ids:
                                _ingest_laya_record(state, item)
                                seen.append(record["span_id"])
                                seen_ids.add(record["span_id"])
                                if len(seen) > 10000:
                                    del seen[:-10000]
                                    seen_ids.intersection_update(seen)
                    except Exception:  # noqa: BLE001 -- malformed complete records advance without exposing content
                        _count(state, "llm_env_laya_skipped_records_total", {"reason": "malformed"})
                return stream.tell()

        pending_inode = cursor.get("rotated_inode")
        pending_offset = cursor.get("rotated_offset", 0)
        if pending_inode is None and old_inode is not None and (old_inode != inode or stat.st_size < old_offset):
            try:
                if rotated.stat().st_ino == old_inode:
                    pending_inode, pending_offset = old_inode, old_offset
            except FileNotFoundError:
                pass
        if pending_inode is not None:
            try:
                if rotated.stat().st_ino == pending_inode:
                    pending_offset = consume(rotated, pending_offset, pending_inode)
                    if pending_offset >= rotated.stat().st_size:
                        pending_inode, pending_offset = None, 0
                        discarding = False
            except FileNotFoundError:
                pending_inode, pending_offset = None, 0
                discarding = False
        offset = old_offset if old_inode == inode and stat.st_size >= old_offset else 0
        if old_inode != inode or stat.st_size < old_offset:
            discarding = False
        new_offset = consume(path, offset, inode)
        state["laya_file_cursor"] = {"inode": inode, "offset": new_offset,
                                     "rotated_inode": pending_inode, "rotated_offset": pending_offset,
                                     "discarding": discarding}
        state.pop("laya_cursor", None)
        return 1
    except (OSError, ValueError, TypeError, AttributeError):
        return int(state.get("laya_file_cursor") is not None)


def _load(path: Path) -> dict | None:
    try:
        state = json.loads(path.read_text())
    except FileNotFoundError:
        return {}
    except (OSError, ValueError, UnicodeError):
        return None
    return state if isinstance(state, dict) else None


def _save(path: Path, state: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.write(json.dumps(state))
    os.replace(tmp, path)


def collect_usage(port: int, password: str, *, path: Path | None = None,
                  laya_path: Path | None = None, deadline_s: float = 20) -> str:
    """Exposition text for the two usage sources; a down source emits source_up 0."""
    path = path or _state_path("observe/usage-state.json")
    loaded = _load(path)
    deadline = time.monotonic() + deadline_s
    up = {"omniroute_usage": 0, "laya": 0, "usage_state": 1}
    if loaded is None:
        return 'llm_env_source_up{source="usage_state"} 0\n'
    snapshot = copy.deepcopy(loaded)
    state = copy.deepcopy(snapshot)
    lines: list[str] = []
    base = f"http://127.0.0.1:{port}"
    original_omni, original_laya = snapshot, snapshot
    try:
        token = bounded_login(base, password, deadline)
        history = json.loads(bounded_get(base, "/api/usage/history", token, deadline))
        if not isinstance(history, Mapping) or not isinstance(history.get("byModel"), Mapping) or any(not isinstance(v, Mapping) for v in history["byModel"].values()):
            raise TypeError("invalid usage history")
        up["omniroute_usage"] = 1
        if not state.get("allow"):
            allowed = _allowlist(history)
            if allowed:
                state["allow"] = allowed
        if state.get("allow") is not None and state.get("allow"):
            lines += history_lines(history, state["allow"])
            rows = json.loads(bounded_get(base, "/api/usage/call-logs?limit=1500", token, deadline))
            if not isinstance(rows, list):
                raise TypeError("invalid call logs")
            cursor = (state.get("omni_cursor") or {}).get("ts")
            cursor = cursor if isinstance(cursor, str) and _safe_ts(cursor) else ""
            finished = []
            for row in rows:
                if not isinstance(row, Mapping) or not isinstance(row.get("status"), int) or isinstance(row.get("status"), bool) or row["status"] <= 0 or row.get("requestType") or not row.get("timestamp"):
                    continue
                try:
                    _dt(row["timestamp"])
                except (TypeError, ValueError):
                    continue
                finished.append(row)
            if len(rows) >= 1500 and cursor and finished and min(_dt(row["timestamp"]) for row in finished) > _dt(cursor) - timedelta(seconds=LOOKBACK_S):
                _count(state, "llm_env_usage_gap_total", {"source": "omniroute"})
            ingest_call_logs(state, rows)
        else:
            up["omniroute_usage"] = 1
    except Exception:  # noqa: BLE001 -- malformed source data must not break exposition
        up["omniroute_usage"] = 0
        state = _restore_source(state, original_omni, "omni")
        lines = []
    try:
        before_laya = copy.deepcopy(state)
        up["laya"] = collect_laya_file(state, laya_path or _state_path("laya-spans/laya-spans.jsonl"),
                                       deadline_s=max(0.0, min(10.0, deadline - time.monotonic())))
        if up["laya"] == 0:
            state = _restore_source(state, before_laya, "laya")
    except AssertionError:
        raise
    except Exception:  # noqa: BLE001 -- Laya source failure must not affect OmniRoute
        up["laya"] = 0
        state = _restore_source(state, original_laya, "laya")
    changed = json.dumps(state, sort_keys=True) != json.dumps(snapshot, sort_keys=True)
    if changed:
        try:
            _save(path, state)
        except Exception:  # noqa: BLE001 -- state persistence must not break exposition
            up["usage_state"] = 0
    try:
        lines += render_state(state)
    except Exception:  # noqa: BLE001
        up["usage_state"] = 0
        lines = []
    lines += [f'llm_env_source_up{{source="{key}"}} {value}' for key, value in up.items()]
    return "\n".join(lines) + "\n"


def _restore_source(state: dict, original: dict, source: str) -> dict:
    prefixes = ("llm_env_omniroute_", "llm_env_usage_gap", "llm_env_usage_skipped_rows_total") if source == "omni" else ("llm_env_laya_", "llm_env_usage_skipped_rows_total")
    keys = {"omni_cursor", "allow", "routes"} if source == "omni" else {"laya_cursor", "laya_file_cursor", "laya_file_seen"}
    for key in keys:
        if key in original:
            state[key] = copy.deepcopy(original[key])
        else:
            state.pop(key, None)
    for key in ("c", "h"):
        current, before = state.get(key, {}), original.get(key, {})
        for name in set(current) | set(before):
            if name.startswith(prefixes):
                if name in before:
                    current[name] = copy.deepcopy(before[name])
                else:
                    current.pop(name, None)
        if key in original:
            state[key] = copy.deepcopy(original[key]) if not current else current
        elif current:
            state[key] = current
        else:
            state.pop(key, None)
    if "skipped_seen" in state or "skipped_seen" in original:
        seen = state.get("skipped_seen", [])
        old_seen = original.get("skipped_seen", [])
        prefix = "omniroute:" if source == "omni" else "laya:"
        state["skipped_seen"] = ([item for item in seen if not item.startswith(prefix)] +
                                  [item for item in old_seen if item.startswith(prefix)])[-200:]
    return state
