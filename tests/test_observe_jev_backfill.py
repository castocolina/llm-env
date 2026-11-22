import json
import sys
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pylib import observe_jev
from pylib.observe_jev_backfill import build_history, run_backfill

STATE = {"jev_callers": ["direct", "known"], "jev_callers_final": True}
START = datetime(2026, 1, 1, tzinfo=UTC)
LIVE = datetime(2026, 1, 1, 0, 20, tzinfo=UTC)


def row(ts="2026-01-01T00:00:00Z", **fields):
    return {"ts": ts, "backend": "laya", "type": "choice", "caller": "direct", "top": 0.8,
            "needs_fallback": False, "error": "", **fields}


def write_log(path, rows, trailing=b""):
    with path.open("wb") as stream:
        for item in rows:
            stream.write(json.dumps(item, allow_nan=True).encode() + b"\n")
        stream.write(trailing)


def parsed_at(text, seconds):
    result = {}
    for line in text.splitlines():
        sample, value, stamp = line.rsplit(" ", 2)
        if int(stamp) == seconds * 1000:
            result[sample] = float(value)
    return result


def test_cumulative_boundaries_alignment_allowlist_and_first_seen_steps(tmp_path):
    log = tmp_path / "log.jsonl"
    write_log(log, [row(ts="2026-01-01T00:01:00Z"), row(ts="2026-01-01T00:05:00Z", caller="known"),
                    row(ts="2026-01-01T00:06:01Z", caller="not-allowed", needs_fallback=True)])
    result = build_history(log, STATE, step_seconds=300, until=datetime(2026, 1, 1, 0, 10, tzinfo=UTC), first_live_ts=LIVE)
    assert result.steps == 2
    assert result.text.count('caller="other"') == 2
    assert 'llm_env_jev_decisions_total{backend="laya",type="choice",caller="direct",outcome="answered"} 1' in result.text
    assert 'llm_env_jev_decisions_total{backend="laya",type="choice",caller="known",outcome="answered"} 1' in result.text
    assert parsed_at(result.text, int(datetime(2026, 1, 1, 0, 5, tzinfo=UTC).timestamp()))
    decisions = [line for line in result.text.splitlines() if "decisions_total" in line]
    assert all(line.endswith(tuple(str(x * 1000) for x in (1767225900, 1767226200, 1767226500))) for line in decisions)
    per_series = {}
    for line in decisions:
        name, stamp = line.rsplit(" ", 1)
        per_series.setdefault(name.rsplit(" ", 1)[0], []).append(int(stamp))
    assert all(stamps == list(range(stamps[0], stamps[-1] + 1, 300_000)) for stamps in per_series.values())
    assert all(stamp % 300_000 == 0 for stamps in per_series.values() for stamp in stamps)
    assert all(stamps[-1] == int(datetime(2026, 1, 1, 0, 10, tzinfo=UTC).timestamp()) * 1000 for stamps in per_series.values())


def test_series_persists_at_the_same_value_after_its_last_row(tmp_path):
    log = tmp_path / "log.jsonl"
    write_log(log, [row(ts="2026-01-01T00:01:00Z", caller="early"),
                    row(ts="2026-01-01T00:06:00Z", caller="direct"),
                    row(ts="2026-01-01T00:11:00Z", caller="direct")])
    result = build_history(log, {**STATE, "jev_callers": ["direct"]}, step_seconds=300,
                          until=datetime(2026, 1, 1, 0, 20, tzinfo=UTC), first_live_ts=LIVE)
    target = 'llm_env_jev_decisions_total{backend="laya",type="choice",caller="other",outcome="answered"}'
    samples = [line for line in result.text.splitlines() if line.startswith(target)]
    assert [float(line.rsplit(" ", 2)[1]) for line in samples] == [1, 1, 1]
    assert [int(line.rsplit(" ", 1)[1]) for line in samples] == [
        int(datetime(2026, 1, 1, 0, minute, tzinfo=UTC).timestamp()) * 1000
        for minute in (5, 10, 15)]


def test_live_classifier_property_error_fallback_partial_and_invalid_utf8(tmp_path, monkeypatch):
    log = tmp_path / "log.jsonl"
    rows = [row(ts="2026-01-01T00:00:00Z", needs_fallback=True),
            row(ts="2026-01-01T00:01:00Z", error="oops", needs_fallback=True),
            row(ts="2026-01-01T00:02:00Z", error={"why": "bad"}),
            row(ts="2026-01-01T00:03:00Z", error=["bad"]),
            row(ts="2026-01-01T00:04:00Z", error="  "),
            row(ts="2026-01-01T00:05:00Z", backend="odd", type="bad", caller="not-allowed", top=2),
            row(ts="2026-01-01T00:06:00Z", caller="known", top=0.5)]
    write_log(log, rows, b'{"ts":"2026-01-01T00:07:00Z",\xff}\n{"ts":"2026-01-01T00:07:30Z"')
    monkeypatch.setattr(observe_jev, "_safe_ts", observe_jev._safe_ts)
    live = tmp_path / "live.json"
    live.write_text(json.dumps(STATE))
    exposition = observe_jev.collect_jev(log_path=log, state_path=live, clock=lambda: LIVE.timestamp())
    backfill = build_history(log, STATE, step_seconds=300, until=datetime(2026, 1, 1, 0, 10, tzinfo=UTC), first_live_ts=LIVE)
    end = parsed_at(backfill.text, int(datetime(2026, 1, 1, 0, 10, tzinfo=UTC).timestamp()))
    live_values = {}
    for line in exposition.splitlines():
        if line.startswith(("#", "llm_env_source_up")):
            continue
        sample, value = line.rsplit(" ", 1)
        live_values[sample] = float(value)
    expected_keys = {key for key in live_values if key.startswith(("llm_env_jev_decisions_total{", "llm_env_jev_fallback_needed_total{", "llm_env_jev_top_confidence_", "llm_env_jev_last_decision_timestamp_seconds{"))}
    assert set(end) == expected_keys
    assert {key: end[key] for key in expected_keys} == pytest.approx({key: live_values[key] for key in expected_keys}, rel=1e-9)
    assert 'llm_env_jev_decisions_total{backend="unknown",type="unknown",caller="other",outcome="answered"} 1' in backfill.text
    assert 'llm_env_jev_decisions_total{backend="laya",type="choice",caller="direct",outcome="error"} 3' in backfill.text
    assert 'llm_env_jev_decisions_total{backend="laya",type="choice",caller="known",outcome="answered"} 1' in backfill.text
    assert 'llm_env_jev_fallback_needed_total{backend="laya",type="choice",caller="direct"} 1' in backfill.text
    assert 'llm_env_jev_top_confidence_count{backend="laya",type="choice"} 1' in backfill.text
    assert "request_sha256" not in backfill.text and "instructions" not in backfill.text


def test_join_series_mismatch_in_either_direction_fails(tmp_path):
    log, state_path, out = tmp_path / "log.jsonl", tmp_path / "state.json", tmp_path / "out.prom"
    write_log(log, [row(ts="2026-01-01T00:00:00Z", needs_fallback=True)])
    state_path.write_text(json.dumps(STATE))
    anchor = int(LIVE.timestamp() * 1000)
    complete = live_values_for(anchor)
    missing_live = [item for item in complete if item[0] != "llm_env_jev_fallback_needed_total"]
    extra_live = complete + [("llm_env_jev_decisions_total", {"backend": "laya", "type": "choice", "caller": "known", "outcome": "answered"}, 1)]
    for rows, values in (([row(ts="2026-01-01T00:00:00Z", needs_fallback=True)], missing_live),
                         ([row(ts="2026-01-01T00:00:00Z", needs_fallback=True)], extra_live)):
        write_log(log, rows)
        with FakeVM(export_for(anchor, values)) as vm:
            with pytest.raises(RuntimeError, match="join check failed"):
                run_backfill(log=log, state_path=state_path, vm=vm.url, step_seconds=300, until=None, out=out)
            assert not out.exists()
    write_log(log, [row(ts="2026-01-01T00:00:00Z", needs_fallback=True)])
    with FakeVM(export_for(anchor, live_values_for(anchor))) as vm:
        run_backfill(log=log, state_path=state_path, vm=vm.url, step_seconds=300, until=None, out=out)


def test_no_zero_only_extra_series_and_stats_use_actual_series(tmp_path):
    log = tmp_path / "log.jsonl"
    write_log(log, [row()])
    result = build_history(log, STATE, until=datetime(2026, 1, 1, 0, 5, tzinfo=UTC), first_live_ts=LIVE)
    distinct = {line.split(" ", 1)[0] for line in result.text.splitlines()}
    assert result.series == len(distinct)
    assert "llm_env_jev_skipped_rows_total" not in result.text
    assert "llm_env_jev_top_out_of_range_total" not in result.text
    assert "llm_env_jev_log_resets_total" not in result.text


def test_rejects_unfinalized_allowlist_and_small_step(tmp_path):
    log = tmp_path / "log.jsonl"
    write_log(log, [row()])
    with pytest.raises(ValueError, match="final jev_callers"):
        build_history(log, {}, until=LIVE, first_live_ts=LIVE)
    with pytest.raises(ValueError, match="at least 60"):
        build_history(log, STATE, step_seconds=59, until=LIVE, first_live_ts=LIVE)


class FakeVM:
    def __init__(self, export, post_codes=()):
        self.export = export
        self.post_codes = list(post_codes)
        self.posts = []
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_): pass
            def do_GET(self):
                body = owner.export.encode()
                self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
            def do_POST(self):
                owner.posts.append(self.rfile.read(int(self.headers["Content-Length"])).decode())
                code = owner.post_codes.pop(0) if owner.post_codes else 204
                self.send_response(code); self.end_headers()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
    def __enter__(self):
        self.thread.start(); return self
    def __exit__(self, *_):
        self.server.shutdown(); self.server.server_close(); self.thread.join()
    @property
    def url(self): return f"http://127.0.0.1:{self.server.server_port}"


def live_values_for(anchor, decision_value=1):
    values: list[tuple[str, dict[str, str], float]] = []
    labels = {"backend": "laya", "type": "choice", "caller": "direct", "outcome": "answered"}
    values = [("llm_env_jev_decisions_total", labels, float(decision_value))]
    for bound in (0.3, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 0.99, 1.0, "+Inf"):
        values.append(("llm_env_jev_top_confidence_bucket", {"backend": "laya", "type": "choice", "le": str(bound)}, int(bound in (0.8, 0.9, 0.95, 0.99, 1.0, "+Inf"))))
    values.extend([("llm_env_jev_top_confidence_sum", {"backend": "laya", "type": "choice"}, 0.8),
                   ("llm_env_jev_top_confidence_count", {"backend": "laya", "type": "choice"}, 1),
                   ("llm_env_jev_fallback_needed_total", {"backend": "laya", "type": "choice", "caller": "direct"}, 1)])
    return values


def export_for(anchor, values):
    samples = [{"metric": {"__name__": "llm_env_source_up", "source": "jev_log"}, "values": [1], "timestamps": [anchor]}]
    for index, (name, labels, value) in enumerate(values):
        offset = -1 if name == "llm_env_jev_decisions_total" else (index + 1)
        samples.append({"metric": {"__name__": name, **labels}, "values": [value], "timestamps": [anchor + offset]})
    # Simulate previously backfilled history: it must never become the live anchor.
    samples.append({"metric": {"__name__": "llm_env_jev_decisions_total", "backend": "laya", "type": "choice", "caller": "direct", "outcome": "answered"}, "values": [0], "timestamps": [anchor - 86_400_000]})
    return "".join(json.dumps(sample) + "\n" for sample in samples)


def test_anchor_join_dry_run_and_rerun_are_stable(tmp_path):
    log, state_path = tmp_path / "log.jsonl", tmp_path / "state.json"
    write_log(log, [row(ts="2026-01-01T00:00:00Z", needs_fallback=True)])
    state_path.write_text(json.dumps(STATE))
    anchor = int(LIVE.timestamp() * 1000)
    labels = {"backend": "laya", "type": "choice", "caller": "direct", "outcome": "answered"}
    values = live_values_for(anchor)
    values.append(("llm_env_jev_fallback_needed_total", {k: labels[k] for k in ("backend", "type", "caller")}, 1))
    with FakeVM(export_for(anchor, values)) as vm:
        out = tmp_path / "out.prom"
        first = run_backfill(log=log, state_path=state_path, vm=vm.url, step_seconds=300, until=None, out=out)
        text = out.read_text()
        second = run_backfill(log=log, state_path=state_path, vm=vm.url, step_seconds=300, until=None, out=out)
        assert text == out.read_text()
        assert not vm.posts
        assert first.join_jumps["series"] == 14
        assert first.join_jumps["llm_env_jev_decisions_total_series"] == 1
        assert first.join_jumps["llm_env_jev_fallback_needed_total_series"] == 1
        assert first.join_jumps["llm_env_jev_top_confidence_bucket_series"] == 10
        assert first.join_jumps["llm_env_jev_top_confidence_count_series"] == 1
        assert first.join_jumps["llm_env_jev_top_confidence_sum_series"] == 1
        assert second.steps == first.steps


@pytest.mark.parametrize(("family", "labels", "live_value", "fallback"), [
    ("llm_env_jev_decisions_total", {"backend": "laya", "type": "choice", "caller": "direct", "outcome": "answered"}, 0, False),
    ("llm_env_jev_fallback_needed_total", {"backend": "laya", "type": "choice", "caller": "direct"}, 0, True),
    ("llm_env_jev_top_confidence_bucket", {"backend": "laya", "type": "choice", "le": "0.8"}, 0, False),
    ("llm_env_jev_top_confidence_sum", {"backend": "laya", "type": "choice"}, 0.7, False),
    ("llm_env_jev_top_confidence_count", {"backend": "laya", "type": "choice"}, 0, False),
])
def test_join_value_mismatch_writes_no_output_and_never_posts(tmp_path, family, labels, live_value, fallback):
    log, state_path, out = tmp_path / "log.jsonl", tmp_path / "state.json", tmp_path / "out.prom"
    write_log(log, [row(ts="2026-01-01T00:00:00Z", needs_fallback=fallback)])
    state_path.write_text(json.dumps(STATE))
    anchor = int(LIVE.timestamp() * 1000)
    values = live_values_for(anchor)
    if fallback:
        values.append(("llm_env_jev_fallback_needed_total", {"backend": "laya", "type": "choice", "caller": "direct"}, 1))
    else:
        values = [(name, series_labels, value) for name, series_labels, value in values
                  if name != "llm_env_jev_fallback_needed_total"]
    values = [(name, series_labels, live_value if name == family and series_labels == labels else value)
              for name, series_labels, value in values]
    with FakeVM(export_for(anchor, values)) as vm:
        with pytest.raises(RuntimeError, match="value mismatch"):
            run_backfill(log=log, state_path=state_path, vm=vm.url, step_seconds=300, until=None, out=out, push=True)
        assert not out.exists() and not vm.posts


def test_join_live_higher_than_backfill_passes_for_every_family(tmp_path):
    log, state_path, out = tmp_path / "log.jsonl", tmp_path / "state.json", tmp_path / "out.prom"
    write_log(log, [row(ts="2026-01-01T00:00:00Z", needs_fallback=True)])
    state_path.write_text(json.dumps(STATE))
    anchor = int(LIVE.timestamp() * 1000)
    values = [(name, labels, value + 84) for name, labels, value in live_values_for(anchor)]
    values.append(("llm_env_jev_fallback_needed_total", {"backend": "laya", "type": "choice", "caller": "direct"}, 85))
    with FakeVM(export_for(anchor, values)) as vm:
        result = run_backfill(log=log, state_path=state_path, vm=vm.url, step_seconds=300, until=None, out=out)
    assert result.join_jumps["max"] == 84
    assert all(result.join_jumps[f"{family}_series"] > 0 for family in (
        "llm_env_jev_decisions_total", "llm_env_jev_fallback_needed_total",
        "llm_env_jev_top_confidence_bucket", "llm_env_jev_top_confidence_sum",
        "llm_env_jev_top_confidence_count"))


@pytest.mark.parametrize("direction", ["live-higher", "backfill-higher"])
@pytest.mark.parametrize("family", [
    "llm_env_jev_decisions_total", "llm_env_jev_fallback_needed_total",
    "llm_env_jev_top_confidence_bucket", "llm_env_jev_top_confidence_sum",
    "llm_env_jev_top_confidence_count",
])
def test_join_relative_tolerance_passes_for_every_family(tmp_path, direction, family):
    log, state_path, out = tmp_path / "log.jsonl", tmp_path / "state.json", tmp_path / "out.prom"
    write_log(log, [row(ts="2026-01-01T00:00:00Z", needs_fallback=True)])
    state_path.write_text(json.dumps(STATE))
    anchor = int(LIVE.timestamp() * 1000)
    values = live_values_for(anchor)
    values.append(("llm_env_jev_fallback_needed_total", {"backend": "laya", "type": "choice", "caller": "direct"}, 1))
    sign = 1 if direction == "live-higher" else -1
    values = [(name, labels, value * (1 + sign * 1e-12) if name == family else value)
              for name, labels, value in values]
    with FakeVM(export_for(anchor, values)) as vm:
        run_backfill(log=log, state_path=state_path, vm=vm.url, step_seconds=300, until=None, out=out)


def test_push_retries_transient_and_stops_on_persistent_500(tmp_path):
    log, state_path = tmp_path / "log.jsonl", tmp_path / "state.json"
    write_log(log, [row(ts="2026-01-01T00:00:00Z", needs_fallback=True)]); state_path.write_text(json.dumps(STATE))
    anchor = int(LIVE.timestamp() * 1000)
    with FakeVM(export_for(anchor, live_values_for(anchor)), [500, 204]) as vm:
        run_backfill(log=log, state_path=state_path, vm=vm.url, step_seconds=300, until=None, out=tmp_path / "a.prom", push=True)
        assert len(vm.posts) == 2 and vm.posts[0] == vm.posts[1]
    with FakeVM(export_for(anchor, live_values_for(anchor)), [500, 500]) as vm:
        with pytest.raises(RuntimeError, match="HTTP 500"):
            run_backfill(log=log, state_path=state_path, vm=vm.url, step_seconds=300, until=None, out=tmp_path / "b.prom", push=True)
        assert len(vm.posts) == 2


def test_until_timezone_conversion_and_invalid_utf8_are_handled(tmp_path):
    log = tmp_path / "log.jsonl"
    write_log(log, [row(ts="2026-01-01T00:01:00Z")], b'\xff\n')
    result = build_history(log, STATE, step_seconds=300,
                           until=datetime.fromisoformat("2026-01-01T02:10:00+02:00"), first_live_ts=LIVE)
    assert result.steps == 2
    stamps = {int(line.rsplit(" ", 1)[1]) for line in result.text.splitlines()}
    assert max(stamps) == int(datetime(2026, 1, 1, 0, 10, tzinfo=UTC).timestamp()) * 1000


def test_grace_cutoff_before_step_boundary_ends_on_previous_step(tmp_path):
    log = tmp_path / "log.jsonl"
    write_log(log, [row(ts="2026-01-01T00:00:00Z")])
    anchor = datetime(2026, 1, 1, 0, 10, 3, tzinfo=UTC)
    result = build_history(log, STATE, step_seconds=300, until=anchor, first_live_ts=anchor)
    timestamps = {int(line.rsplit(" ", 1)[1]) for line in result.text.splitlines()}
    assert max(timestamps) == int(datetime(2026, 1, 1, 0, 5, tzinfo=UTC).timestamp()) * 1000
    assert all(stamp % 300_000 == 0 for stamp in timestamps)


def test_privacy_sentinels_never_appear_in_any_written_chunk(tmp_path, monkeypatch):
    sentinels = {"instructions": "INSTR_SENTINEL", "id": "ID_SENTINEL",
                 "choice": "CHOICE_SENTINEL", "score": "817263.123456",
                 "request_sha256": "HASH_SENTINEL", "caller": "WEIRD_CALLER_SENTINEL",
                 "model": "WEIRD_MODEL_SENTINEL"}
    log, state_path, out = tmp_path / "log.jsonl", tmp_path / "state.json", tmp_path / "out.prom"
    write_log(log, [row(ts=f"2026-01-01T00:{minute:02}:00Z", **sentinels) for minute in (0, 1, 2, 3, 4)])
    state_path.write_text(json.dumps(STATE))
    from pylib import observe_jev_backfill as module
    original_chunks = module._chunks
    monkeypatch.setattr(module, "_chunks", lambda text: original_chunks(text, max_lines=8))
    reference = build_history(log, STATE, until=LIVE, first_live_ts=LIVE)
    anchor = int(LIVE.timestamp() * 1000)
    final_stamp = max(int(line.rsplit(" ", 1)[1]) for line in reference.text.splitlines()
                      if any(line.startswith(family + "{") for family in module.JOIN_FAMILIES))
    live = []
    for line in reference.text.splitlines():
        metric, value, stamp = line.rsplit(" ", 2)
        name, label_text = metric.split("{", 1)
        if int(stamp) == final_stamp and name in module.JOIN_FAMILIES:
            labels = dict(part.split('="', 1) for part in label_text[:-1].split(','))
            live.append((name, {key: label.rstrip('"') for key, label in labels.items()}, float(value)))
    with FakeVM(export_for(anchor, live)) as vm:
        run_backfill(log=log, state_path=state_path, vm=vm.url, step_seconds=300, until=None, out=out)
    parts = sorted(tmp_path.glob("out.prom.*"))
    assert len(parts) > 1 and not out.exists()
    emitted = [part.read_text() for part in parts]
    for text in emitted:
        for sentinel in sentinels.values():
            assert sentinel not in text
        assert "817263.123456" not in text
    assert any('type="choice"' in text for text in emitted)
    # The live classifier ignores model entirely; unlike backend/type/caller, it has no model allowlist labels.
    classified = observe_jev.classify_jev_row(row(model="UNLISTED_MODEL_SENTINEL"), LIVE)
    assert classified is not None and "model" not in classified[1]


def test_missing_state_and_log_errors_are_actionable(tmp_path):
    with pytest.raises(OSError):
        (tmp_path / "missing.json").read_text()
    log = tmp_path / "empty.jsonl"; log.write_text("")
    with pytest.raises(RuntimeError, match="no valid timestamped rows"):
        build_history(log, STATE, until=LIVE, first_live_ts=LIVE)


def test_no_live_samples_and_invalid_chunk_rejected(tmp_path, monkeypatch):
    log = tmp_path / "log.jsonl"; write_log(log, [row()])
    from pylib.observe_jev_backfill import first_live
    with FakeVM("") as vm, pytest.raises(RuntimeError, match="no live Jev source"):
        first_live(vm.url)
    from pylib import observe_jev_backfill as module
    monkeypatch.setattr(module, "_valid_chunk", lambda _: False)
    with pytest.raises(ValueError, match="strict validation"):
        build_history(log, STATE, until=datetime(2026, 1, 1, 0, 5, tzinfo=UTC), first_live_ts=LIVE)
