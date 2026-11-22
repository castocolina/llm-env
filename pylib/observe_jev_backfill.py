"""One-off Jev history import, emitted as aligned cumulative Prometheus samples."""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import math
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pylib.observe_jev import BUCKETS, classify_jev_row

MAX_EXPORT_BYTES = 64 * 1024 * 1024
FAMILIES = {
    "llm_env_jev_decisions_total": ("backend", "type", "caller", "outcome"),
    "llm_env_jev_fallback_needed_total": ("backend", "type", "caller"),
    "llm_env_jev_top_confidence_bucket": ("backend", "type", "le"),
    "llm_env_jev_top_confidence_sum": ("backend", "type"),
    "llm_env_jev_top_confidence_count": ("backend", "type"),
    "llm_env_jev_last_decision_timestamp_seconds": ("backend",),
}
JOIN_FAMILIES = tuple(FAMILIES)[:-1]
@dataclass
class History:
    text: str
    rows: int
    first: dt.datetime | None
    last: dt.datetime | None
    steps: int
    series: int
    samples: int
    totals: dict[str, int]
    join_jumps: dict[str, float]


def _iso(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value)
    return parsed.replace(tzinfo=dt.UTC) if parsed.tzinfo is None else parsed.astimezone(dt.UTC)


def _read_export(url: str, timeout: float) -> list[tuple[str, int, float, dict[str, str]]]:
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        body = response.read(MAX_EXPORT_BYTES + 1)
    if len(body) > MAX_EXPORT_BYTES:
        raise RuntimeError("VictoriaMetrics export exceeds 64 MiB limit")
    result = []
    for line in body.decode("utf-8").splitlines():
        try:
            doc = json.loads(line)
        except ValueError:
            continue
        metric = doc.get("metric", {})
        if not isinstance(metric, dict) or not isinstance(metric.get("__name__"), str):
            continue
        name = metric["__name__"]
        for value, timestamp in zip(doc.get("values", []), doc.get("timestamps", []), strict=False):
            result.append((name, int(timestamp), float(value), {k: v for k, v in metric.items() if k != "__name__"}))
    return result


def first_live(vm: str, timeout: float = 10) -> tuple[dt.datetime, dict[str, float]]:
    query = urllib.parse.urlencode({"match[]": 'llm_env_source_up{source="jev_log"}', "start": "-400d"})
    samples = _read_export(vm.rstrip("/") + "/api/v1/export?" + query, timeout)
    anchor = [ts for name, ts, _, labels in samples if name == "llm_env_source_up" and labels.get("source") == "jev_log"]
    if not anchor:
        raise RuntimeError("VictoriaMetrics has no live Jev source sample; refusing backfill")
    first_ms = min(anchor)
    cutoff_ms = first_ms - 10_000  # Collector metric families are emitted a few seconds apart.
    matches = "&".join(urllib.parse.urlencode({"match[]": f'{family}{{}}'}) for family in JOIN_FAMILIES)
    query = urllib.parse.urlencode({"start": dt.datetime.fromtimestamp(cutoff_ms / 1000, dt.UTC).isoformat().replace("+00:00", "Z")}) + "&" + matches
    samples = _read_export(vm.rstrip("/") + "/api/v1/export?" + query, timeout)
    earliest: dict[str, tuple[int, float]] = {}
    for name, timestamp, value, labels in samples:
        if timestamp < cutoff_ms or name not in JOIN_FAMILIES:
            continue
        order = FAMILIES[name]
        if all(label in labels for label in order):
            key = name + "{" + ",".join(f'{label}="{labels[label]}"' for label in order) + "}"
            if key not in earliest or timestamp < earliest[key][0]:
                earliest[key] = (timestamp, value)
    live = {key: value for key, (_, value) in earliest.items()}
    return dt.datetime.fromtimestamp(first_ms / 1000, dt.UTC), live


def build_history(log_path: Path, state: dict[str, Any], *, step_seconds: int = 300,
                  until: dt.datetime, first_live_ts: dt.datetime) -> History:
    if step_seconds < 60:
        raise ValueError("step-seconds must be at least 60")
    if state.get("jev_callers_final") is not True or not isinstance(state.get("jev_callers"), list):
        raise ValueError("state must contain final jev_callers allowlist; refusing ambiguous caller mapping")
    until, first_live_ts = until.astimezone(dt.UTC), first_live_ts.astimezone(dt.UTC)
    cutoff_ts = int(first_live_ts.timestamp()) - 10
    end_ts = min(int(until.timestamp()), ((cutoff_ts - 1) // step_seconds) * step_seconds)
    first = last = None
    counts: collections.Counter = collections.Counter()
    fallback: collections.Counter = collections.Counter()
    allowed = set(state["jev_callers"]) | {"direct"}
    rows_read = skipped = 0
    records: dict[int, list[tuple[float, dict[str, Any]]]] = collections.defaultdict(list)
    with log_path.open("rb") as stream:
        for raw in stream:
            if not raw.endswith(b"\n"):
                break
            rows_read += 1
            try:
                row = json.loads(raw)
                classified = classify_jev_row(row, dt.datetime.now(dt.UTC) + dt.timedelta(seconds=300))
                if classified is None:
                    skipped += 1
                    continue
                stamp, item = classified
                if first is None:
                    first = stamp
                last = stamp
                item["caller"] = item["caller"] if item["caller"] in allowed else "other"
                item["stamp"] = stamp.timestamp()
                row_boundary = ((int(stamp.timestamp()) + step_seconds - 1) // step_seconds) * step_seconds
                records[row_boundary].append((item["stamp"], item))
                if row_boundary <= end_ts:
                    counts[(item["backend"], item["type"], item["caller"], item["outcome"])] += 1
                    if item["fallback"]: fallback[(item["backend"], item["type"], item["caller"])] += 1
            except (ValueError, TypeError, OverflowError, UnicodeError):
                skipped += 1
    if first is None:
        raise RuntimeError("Jev log has no valid timestamped rows")
    start = ((int(first.timestamp()) + step_seconds - 1) // step_seconds) * step_seconds
    if end_ts < start:
        raise RuntimeError("no historical step boundary exists before first live sample")
    end = (end_ts // step_seconds) * step_seconds
    bounds = range(start, end + 1, step_seconds)
    decisions: collections.Counter = collections.Counter()
    fallbacks: collections.Counter = collections.Counter()
    accumulated: dict[tuple[str, str], list[Any]] = {}
    last_by_backend: dict[str, float] = {}
    lines: list[str] = []
    for boundary in bounds:
        for _, item in records.get(boundary, []):
            key = (item["backend"], item["type"], item["caller"], item["outcome"])
            decisions[key] += 1
            fallback_key = key[:3]
            if item["fallback"]: fallbacks[fallback_key] += 1
            if item["top"] is not None:
                hist_key = (item["backend"], item["type"])
                agg = accumulated.setdefault(hist_key, [[0] * (len(BUCKETS) + 1), 0.0, 0])
                value = item["top"]
                agg[0][next((i for i, bound in enumerate(BUCKETS) if value <= bound), len(BUCKETS))] += 1
                agg[1] += value
                agg[2] += 1
            if item["backend"] != "unknown":
                last_by_backend[item["backend"]] = max(last_by_backend.get(item["backend"], item["stamp"]), item["stamp"])
        stamp_ms = boundary * 1000
        def emit(name: str, labels: tuple[str, ...], value: float, stamp_ms: int = stamp_ms) -> None:
            label_names = FAMILIES[name]
            body = "{" + ",".join(f'{label}="{value}"' for label, value in zip(label_names, labels, strict=True)) + "}"
            lines.append(f"{name}{body} {value} {stamp_ms}")
        for key, value in decisions.items(): emit("llm_env_jev_decisions_total", key, value)
        for key, value in fallbacks.items():
            if value: emit("llm_env_jev_fallback_needed_total", key, value)
        for (backend, typ), (bucket_counts, total, n) in accumulated.items():
            cumulative = 0
            for bound, count in zip((*BUCKETS, "+Inf"), bucket_counts, strict=True):
                cumulative += count
                emit("llm_env_jev_top_confidence_bucket", (backend, typ, str(bound)), cumulative)
            emit("llm_env_jev_top_confidence_sum", (backend, typ), total)
            emit("llm_env_jev_top_confidence_count", (backend, typ), n)
        for backend, value in last_by_backend.items(): emit("llm_env_jev_last_decision_timestamp_seconds", (backend,), value)
    # Rebuild state at all boundaries in file order (timestamps may be out of order).
    # The row stream is deliberately parsed once; file-order state is accumulated as boundary advances.
    text = "\n".join(lines) + "\n"
    from pylib.observe_metrics import valid_exposition_fragment
    for chunk in _chunks(text):
        if not _valid_chunk(chunk) or not valid_exposition_fragment("\n".join(line.rsplit(" ", 1)[0] for line in chunk.splitlines()) + "\n"):
            raise ValueError("generated exposition chunk failed strict validation")
    distinct = {line.split(" ", 1)[0] for line in lines}
    steps = len(bounds)
    return History(text, rows_read, first, last, steps, len(distinct), len(lines),
                   {backend: sum(value for key, value in counts.items() if key[0] == backend) for backend in ("laya", "typesafe", "unknown")}, {})


def _chunks(text: str, max_lines: int = 20_000) -> list[str]:
    lines = text.splitlines(keepends=True)
    return ["".join(lines[i:i + max_lines]) for i in range(0, len(lines), max_lines)]


def _valid_chunk(text: str) -> bool:
    if not text or not text.endswith("\n") or text.endswith("\n\n"):
        return False
    for line in text.splitlines():
        match = re.fullmatch(r'([a-zA-Z_:][a-zA-Z0-9_:]*(?:\{.*\})?) ([^ ]+) ([0-9]+)', line)
        if not match:
            return False
        try:
            if not math.isfinite(float(match.group(2))): return False
        except (ValueError, OverflowError): return False
    return True


def run_backfill(*, log: Path, state_path: Path, vm: str, step_seconds: int, until: str | None,
                 out: Path, push: bool = False, timeout: float = 10) -> History:
    started = time.monotonic()
    live_ts, live_values = first_live(vm, timeout)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    if state.get("jev_callers_final") is not True or not isinstance(state.get("jev_callers"), list):
        raise RuntimeError("state must contain final jev_callers allowlist and jev_callers_final=true")
    stop = _iso(until) if until else live_ts
    result = build_history(log, state, step_seconds=step_seconds, until=stop, first_live_ts=live_ts)
    last_ms = max((int(m.group(1)) for line in result.text.splitlines() if (m := re.search(r" ([0-9]+)$", line))), default=-1)
    backfilled: dict[str, float] = {}
    for line in result.text.splitlines():
        match = re.fullmatch(r'([a-zA-Z_:][a-zA-Z0-9_:]*(?:\{.*\})?) ([^ ]+) ([0-9]+)', line)
        if match and int(match.group(3)) == last_ms and any(match.group(1).startswith(family + "{") for family in JOIN_FAMILIES):
            backfilled[match.group(1)] = float(match.group(2))
    jumps: dict[str, float] = {}
    missing = sorted(set(backfilled) - set(live_values))
    if missing:
        raise RuntimeError(f"join check failed: series membership mismatch; backfill series have no live counterpart ({len(missing)})")
    family_counts: dict[str, int] = {}
    for family in JOIN_FAMILIES:
        backfilled_family = {series for series in backfilled if series.startswith(family + "{")}
        live_family = {series for series in live_values if series.startswith(family + "{")}
        if not backfilled_family and not live_family:
            family_counts[family] = 0
            continue
        if backfilled_family != live_family:
            raise RuntimeError(f"join check failed: series membership mismatch for {family}")
        family_counts[family] = len(backfilled_family)
    if sum(family_counts.values()) != len(backfilled):
        raise RuntimeError("join check failed: unexpected series in the compared set")
    for series, value in backfilled.items():
        jump = live_values[series] - value
        if jump < -1e-9 * max(1.0, abs(live_values[series])):
            raise RuntimeError(f"join check failed: value mismatch for {series}; backfill exceeds first live value")
        jumps[series] = jump
    out.parent.mkdir(parents=True, exist_ok=True)
    chunks = _chunks(result.text)
    if len(chunks) == 1:
        out.write_text(chunks[0], encoding="utf-8")
    else:
        out.unlink(missing_ok=True)
        for part, chunk in enumerate(chunks, 1): out.with_name(f"{out.name}.{part:03d}").write_text(chunk, encoding="utf-8")
    accepted = 0
    if push:
        for chunk in chunks:
            req = urllib.request.Request(vm.rstrip("/") + "/api/v1/import/prometheus", data=chunk.encode(), method="POST", headers={"Content-Type": "text/plain; version=0.0.4"})
            for attempt in range(2):
                try:
                    with urllib.request.urlopen(req, timeout=timeout) as response:
                        if response.status not in (200, 204): raise RuntimeError(f"push failed after {accepted} chunks; HTTP {response.status}")
                    accepted += 1
                    break
                except urllib.error.HTTPError as exc:
                    if attempt: raise RuntimeError(f"push failed after {accepted} chunks; HTTP {exc.code}") from None
    result.series = len({line.split(" ", 1)[0] for line in result.text.splitlines()})
    result.join_jumps = {"series": len(jumps), "max": max(jumps.values(), default=0.0), **{f"{family}_series": count for family, count in family_counts.items()}}
    print(f"rows={result.rows} first={result.first} last={result.last} steps={result.steps} series={result.series} samples={result.samples} chunks={len(chunks)} bytes={len(result.text.encode())}")
    print(f"totals={result.totals} join_series={len(jumps)} max_jump={max(jumps.values(), default=0.0)}")
    for family, count in family_counts.items():
        family_jumps = [jumps[key] for key in jumps if key.startswith(family + "{")]
        print(f"join_family={family} compared={count} max_jump={max(family_jumps, default=0.0):g}")
    print("Note: llm_env_jev_skipped_rows_total and llm_env_jev_top_out_of_range_total are not backfilled; neither has live series.")
    print(f"wall_seconds={time.monotonic()-started:.3f}")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="observe-jev-backfill")
    parser.add_argument("--log", type=Path, default=Path.home() / ".local/state/ai-kit/jev-answers.jsonl")
    parser.add_argument("--state", type=Path, default=Path.home() / ".local/state/llm-env/observe/jev-state.json")
    parser.add_argument("--vm", default="http://127.0.0.1:8428")
    parser.add_argument("--step-seconds", type=int, default=300)
    parser.add_argument("--until")
    parser.add_argument("--out", type=Path, default=Path("jev-backfill.prom"))
    parser.add_argument("--push", action="store_true")
    args = parser.parse_args(argv)
    try:
        run_backfill(log=args.log, state_path=args.state, vm=args.vm, step_seconds=args.step_seconds, until=args.until, out=args.out, push=args.push)
    except (OSError, ValueError, RuntimeError, urllib.error.URLError, json.JSONDecodeError) as exc:
        print(f"observe-jev-backfill: {exc}", file=sys.stderr)
        return 1
    return 0
