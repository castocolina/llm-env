import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _row(**fields):
    row = {"ts": datetime.now(UTC).isoformat(), "backend": "laya", "caller": "direct",
           "type": "choice", "model": "model", "top": 0.8, "needs_fallback": False,
           "error": "", "instructions": "private", "id": "private", "choice": "private",
           "request_sha256": "private"}
    row.update(fields)
    return json.dumps(row).encode() + b"\n"


def _collect(tmp_path, rows, *, state=None, budget=10):
    from pylib.observe_jev import collect_jev

    log = tmp_path / "jev.jsonl"
    log.write_bytes(rows)
    return collect_jev(log_path=log, state_path=state or tmp_path / "state.json", deadline_s=budget)


def test_rows_metrics_fallback_errors_unknowns_and_histogram(tmp_path):
    from pylib.observe_jev import collect_jev

    log, state = tmp_path / "jev.jsonl", tmp_path / "state.json"
    log.write_bytes(b"".join([
        _row(caller="direct", top=0.3, needs_fallback=True),
        _row(backend="typesafe", type="noul", caller="other-caller", top=0.5, needs_fallback=None),
        _row(backend="laya", type="score", error="unavailable", top=0.6, needs_fallback=False),
        _row(backend="bad", type="bad", caller="odd", top=1.0, needs_fallback=False),
    ]))
    text = collect_jev(log_path=log, state_path=state)
    assert 'llm_env_jev_decisions_total{backend="laya",type="choice",caller="direct",outcome="answered"} 1' in text
    assert 'llm_env_jev_decisions_total{backend="laya",type="score",caller="direct",outcome="error"} 1' in text
    assert 'llm_env_jev_decisions_total{backend="unknown",type="unknown",caller="odd",outcome="answered"} 1' in text
    assert 'llm_env_jev_fallback_needed_total{backend="laya",type="choice",caller="direct"} 1' in text
    assert 'llm_env_jev_fallback_needed_total{backend="typesafe"' not in text
    assert 'llm_env_jev_top_confidence_bucket{backend="laya",type="choice",le="0.3"} 1' in text
    assert 'llm_env_jev_top_confidence_bucket{backend="laya",type="score",le="+Inf"} 1' in text
    assert 'llm_env_jev_source_up' not in text
    assert 'llm_env_source_up{source="jev_log"} 1' in text
    assert json.loads(state.read_text())["jev_callers"] == ["direct", "odd", "other-caller"]


def test_partial_line_is_deferred_until_newline(tmp_path):
    from pylib.observe_jev import collect_jev

    log, state = tmp_path / "jev.jsonl", tmp_path / "state.json"
    line = _row()
    log.write_bytes(line[:-1])
    collect_jev(log_path=log, state_path=state)
    assert "llm_env_jev_decisions_total" not in state.read_text()
    with log.open("ab") as stream:
        stream.write(b"\n")
    text = collect_jev(log_path=log, state_path=state)
    assert 'outcome="answered"} 1' in text


def test_rotation_truncation_future_and_malformed_rows_are_counted_once(tmp_path, monkeypatch):
    from pylib.observe_jev import collect_jev

    log, state = tmp_path / "jev.jsonl", tmp_path / "state.json"
    log.write_bytes(_row() + b"{broken}\n" + _row(ts="bad") + _row(ts="2099-01-01T00:00:00Z"))
    first = collect_jev(log_path=log, state_path=state)
    assert 'llm_env_jev_skipped_rows_total 3' in first
    second = collect_jev(log_path=log, state_path=state)
    assert 'llm_env_jev_skipped_rows_total 3' in second
    log.write_bytes(_row(backend="typesafe"))
    real_stat = Path.stat
    old_inode = json.loads(state.read_text())["jev"]["inode"]
    monkeypatch.setattr(Path, "stat", lambda path, *a, **kw: SimpleNamespace(st_ino=old_inode + 1, st_size=real_stat(path, *a, **kw).st_size) if path == log else real_stat(path, *a, **kw))
    rotated = collect_jev(log_path=log, state_path=state)
    assert 'llm_env_jev_log_resets_total 1' in rotated
    assert 'backend="typesafe"' in rotated


def test_allowlist_is_fixed_and_private_fields_never_escape(tmp_path):
    from pylib.observe_jev import collect_jev

    log, state = tmp_path / "jev.jsonl", tmp_path / "state.json"
    sentinel = "NEVER-EXPORT-UNIQUE-7f9c"
    log.write_bytes(_row(instructions=sentinel, id=sentinel, choice=sentinel, request_sha256=sentinel))
    text = collect_jev(log_path=log, state_path=state)
    with log.open("ab") as stream:
        stream.write(_row(caller="new-caller", instructions=sentinel))
    text += collect_jev(log_path=log, state_path=state)
    assert sentinel not in text + state.read_text()
    saved = json.loads(state.read_text())
    assert saved["jev_callers"] == ["direct"]
    assert 'caller="other"' in text


def test_reload_is_idempotent_and_missing_or_bad_state_fails_closed(tmp_path):
    from pylib.observe_jev import collect_jev

    log, state = tmp_path / "jev.jsonl", tmp_path / "state.json"
    log.write_bytes(_row())
    text1 = collect_jev(log_path=log, state_path=state)
    before = state.read_bytes()
    text2 = collect_jev(log_path=log, state_path=state)
    assert text1 == text2
    assert state.read_bytes() == before
    state.write_text("[]")
    assert 'llm_env_source_up{source="jev_state"} 0' in collect_jev(log_path=log, state_path=state)
    state.unlink()
    missing = collect_jev(log_path=tmp_path / "absent", state_path=state)
    assert 'llm_env_source_up{source="jev_log"} 0' in missing


def test_budget_resumes_and_inode_change_resets(tmp_path, monkeypatch):
    from pylib.observe_jev import collect_jev

    log, state = tmp_path / "jev.jsonl", tmp_path / "state.json"
    log.write_bytes(_row() * 20)
    ticks = iter([0.0, 0.0, 0.1, 0.6])
    monkeypatch.setattr("pylib.observe_jev.time.monotonic", lambda: next(ticks, 0.6))
    one = collect_jev(log_path=log, state_path=state, deadline_s=0.5, clock=lambda: datetime.now(UTC).timestamp(), monotonic=lambda: next(ticks, 0.6))
    assert 'outcome="answered"} 2' in one
    assert json.loads(state.read_text())["jev"]["offset"] < log.stat().st_size
    monkeypatch.undo()
    collect_jev(log_path=log, state_path=state)
    assert 'outcome="answered"} 20' in collect_jev(log_path=log, state_path=state)
    replacement = tmp_path / "replacement"
    replacement.write_bytes(_row(backend="typesafe"))
    os.replace(replacement, log)
    reset = collect_jev(log_path=log, state_path=state)
    assert 'llm_env_jev_log_resets_total 1' in reset
    assert 'backend="typesafe"' in reset


def test_numeric_and_malformed_shapes_are_skipped_once(tmp_path):
    log = tmp_path / "jev.jsonl"
    log.write_bytes(b"".join([_row(ts=None), _row(ts="2026-11-20T12:00:00Z", top=True),
                               b"[]\n", b"{\"ts\":\"2026-11-20T12:00:00Z\"}\n"]))
    text = _collect(tmp_path, log.read_bytes())
    assert 'llm_env_jev_skipped_rows_total 2' in text


def test_null_state_keys_are_absent_but_wrong_non_null_types_fail_closed(tmp_path):
    from pylib.observe_jev import collect_jev

    log, state = tmp_path / "jev.jsonl", tmp_path / "state.json"
    log.write_bytes(_row())
    for key in ("jev_callers", "caller_counts", "jev", "inode", "offset"):
        # inode and offset are validated as fields of the jev cursor below.
        if key in ("inode", "offset"):
            value = {"jev": {"inode": None, "offset": 0} if key == "inode" else
                     {"inode": 1, "offset": 0} if key == "offset" else None}
        else:
            value = {key: None}
        state.write_text(json.dumps(value))
        text = collect_jev(log_path=log, state_path=state)
        assert 'llm_env_source_up{source="jev_state"} 1' in text
        assert 'outcome="answered"} 1' in text
    for key, value in (("jev_callers", 1), ("caller_counts", []), ("jev", []),
                       ("inode", "bad"), ("offset", -1)):
        doc = {key: value} if key not in ("inode", "offset") else {
            "jev": {"inode": value if key == "inode" else None,
                    "offset": value if key == "offset" else 0}}
        raw = json.dumps(doc)
        state.write_text(raw)
        text = collect_jev(log_path=log, state_path=state)
        assert 'llm_env_source_up{source="jev_state"} 0' in text
        assert state.read_text() == raw


def test_top_values_are_validated_before_counting_and_errors_do_not_fallback(tmp_path):
    from pylib.observe_jev import collect_jev

    log, state = tmp_path / "jev.jsonl", tmp_path / "state.json"
    log.write_bytes(b"".join(_row(top=x, error="failed" if x == 10**400 else "",
                                needs_fallback=True)
                                for x in (10**400, 1e999, "nan", "inf", True, "0.5", -1, 2)))
    text = collect_jev(log_path=log, state_path=state)
    assert 'llm_env_jev_decisions_total{backend="laya",type="choice",caller="direct",outcome="error"} 1' in text
    assert 'llm_env_jev_decisions_total{backend="laya",type="choice",caller="direct",outcome="answered"} 7' in text
    assert 'llm_env_jev_top_confidence_count' not in text
    assert 'llm_env_jev_top_out_of_range_total 4' in text
    assert 'llm_env_jev_fallback_needed_total{backend="laya",type="choice",caller="direct"} 7' in text
    assert 'llm_env_jev_skipped_rows_total' not in text


def test_caller_allowlist_waits_for_rows_and_rebuilds_once_at_500(tmp_path):
    from pylib.observe_jev import collect_jev

    log, state = tmp_path / "jev.jsonl", tmp_path / "state.json"
    log.write_bytes(b"")
    collect_jev(log_path=log, state_path=state)
    assert "jev_callers" not in json.loads(state.read_text())
    with log.open("ab") as stream:
        stream.write(_row(caller="first"))
    collect_jev(log_path=log, state_path=state)
    assert json.loads(state.read_text())["jev_callers"] == ["direct", "first"]
    with log.open("ab") as stream:
        stream.write(_row(caller="late") * 499)
    text = collect_jev(log_path=log, state_path=state)
    saved = json.loads(state.read_text())
    assert saved["jev_callers_final"] is True
    assert "late" in saved["jev_callers"]
    with log.open("ab") as stream:
        stream.write(_row(caller="after-final"))
    text = collect_jev(log_path=log, state_path=state)
    assert json.loads(state.read_text())["jev_callers"] == saved["jev_callers"]
    assert 'caller="other"' in text


def test_inode_change_to_larger_file_restarts_once(tmp_path):
    from pylib.observe_jev import collect_jev

    log, state = tmp_path / "jev.jsonl", tmp_path / "state.json"
    log.write_bytes(_row() * 2)
    collect_jev(log_path=log, state_path=state)
    old_offset = json.loads(state.read_text())["jev"]["offset"]
    replacement = tmp_path / "replacement"
    replacement.write_bytes(_row(backend="typesafe") * 10)
    assert replacement.stat().st_size > old_offset
    os.replace(replacement, log)
    text = collect_jev(log_path=log, state_path=state)
    assert 'llm_env_jev_log_resets_total 1' in text
    assert 'backend="typesafe"' in text
    assert sum(int(line.rsplit(" ", 1)[1]) for line in text.splitlines()
               if line.startswith("llm_env_jev_decisions_total")) == 12
    assert 'llm_env_jev_log_resets_total 1' in collect_jev(log_path=log, state_path=state)


def test_state_write_mode_and_byte_identical_without_changes(tmp_path):
    from pylib.observe_jev import collect_jev

    log, state = tmp_path / "jev.jsonl", tmp_path / "state.json"
    log.write_bytes(_row())
    collect_jev(log_path=log, state_path=state)
    assert state.stat().st_mode & 0o777 == 0o600
    raw = state.read_bytes()
    collect_jev(log_path=log, state_path=state)
    assert state.read_bytes() == raw


@pytest.mark.parametrize("caller", ['bad"quote', "bad\nnewline", "back\\slash", "unicode-λ", "", "x" * 100])
def test_invalid_caller_is_mapped_to_other(caller, tmp_path):
    text = _collect(tmp_path, _row(caller=caller))
    assert 'caller="other"' in text
    if caller:
        assert caller not in text


@pytest.mark.parametrize("error", [False, 0, [], {}, " \t\n"])
def test_empty_error_values_are_answered(error, tmp_path):
    text = _collect(tmp_path, _row(error=error))
    assert 'outcome="answered"} 1' in text
    assert 'outcome="error"' not in text


@pytest.mark.parametrize("top", [1e308, 2, -1, 1.0000001])
def test_top_outside_unit_interval_is_not_observed(top, tmp_path):
    text = _collect(tmp_path, _row(top=top))
    assert "llm_env_jev_top_confidence" not in text
    assert "llm_env_jev_top_out_of_range_total 1" in text


def test_state_inner_types_fail_closed_without_overwrite(tmp_path):
    from pylib.observe_jev import collect_jev
    log, state = tmp_path / "jev.jsonl", tmp_path / "state.json"
    log.write_bytes(_row())
    for bad in ({"c": {"llm_env_jev_decisions_total": {"[]": "bad"}}},
                {"h": {"llm_env_jev_top_confidence": {"[]": {"b": [], "s": 0, "n": 0}}}},
                {"last": {"laya": "bad"}}):
        raw = json.dumps(bad)
        state.write_text(raw)
        text = collect_jev(log_path=log, state_path=state)
        assert 'source="jev_state"} 0' in text
        assert state.read_text() == raw
