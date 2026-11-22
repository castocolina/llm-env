import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import json

from pylib.observe_usage import (
    collect_usage,
    history_lines,
    ingest_call_logs,
    render_state,
)


def _row(i, ts, status=200, ttft: int | None = 800, combo: str | None = "my-coding", **kw):
    return {"id": i, "timestamp": ts, "status": status, "ttft": ttft, "duration": 3000, "model": "m", "comboName": combo, **kw}


def _counter(state):
    return state.get("c", {}).get("llm_env_omniroute_calls_total", {})


def test_collect_usage_resolves_laya_path_from_current_state_home(monkeypatch, tmp_path):
    import pylib.observe_usage as usage

    monkeypatch.setattr(usage, "bounded_login", lambda *args: "token")
    monkeypatch.setattr(usage, "bounded_get", lambda *args: json.dumps({"byModel": {}}))
    first_home, second_home = tmp_path / "first", tmp_path / "second"
    first_path = first_home / "llm-env/laya-spans/laya-spans.jsonl"
    second_path = second_home / "llm-env/laya-spans/laya-spans.jsonl"
    first_path.parent.mkdir(parents=True)
    second_path.parent.mkdir(parents=True)
    first_path.write_text(json.dumps({**_file_record(), "ts": "2026-01-01T00:00:01Z"}) + "\n")
    second_path.write_text(json.dumps({**_file_record("fedcba9876543210"), "ts": "2026-01-01T00:00:02Z", "endpoint": "/v1/systemone/batch", "batch": True}) + "\n")

    monkeypatch.setenv("XDG_STATE_HOME", str(first_home))
    first_text = usage.collect_usage(1, "secret", path=tmp_path / "first-state.json")
    monkeypatch.setenv("XDG_STATE_HOME", str(second_home))
    second_text = usage.collect_usage(1, "secret", path=tmp_path / "second-state.json")

    assert 'llm_env_source_up{source="laya"} 1' in first_text
    assert 'llm_env_source_up{source="laya"} 1' in second_text
    assert 'llm_env_laya_requests_total{batch="false",endpoint="/v1/systemone",status="2xx"} 1' in first_text
    assert 'llm_env_laya_requests_total{batch="true",endpoint="/v1/systemone/batch",status="2xx"} 1' in second_text


def test_collect_usage_propagates_state_guard_failure(monkeypatch, tmp_path):
    import pytest

    import pylib.observe_usage as usage

    monkeypatch.setattr(usage, "bounded_login", lambda *args: "token")
    monkeypatch.setattr(usage, "bounded_get", lambda *args: json.dumps({"byModel": {}}))
    monkeypatch.setattr(usage, "collect_laya_file", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("state guard")))
    with pytest.raises(AssertionError, match="state guard"):
        usage.collect_usage(1, "secret", path=tmp_path / "state.json", laya_path=tmp_path / "spans.jsonl")


def test_observe_state_guard_rejects_real_home(monkeypatch, tmp_path):
    import pytest
    from observe_state_guard import REAL_LLM_ENV_STATE

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state-home"))
    with pytest.raises(AssertionError, match="test opened real state"):
        (REAL_LLM_ENV_STATE / "laya-spans/laya-spans.jsonl").open()


def test_call_logs_count_once_across_collections_and_skip_probes_and_active():
    state: dict = {"allow": ["m"]}
    rows = [_row("a", "2026-01-01T00:00:01Z"), _row("b", "2026-01-01T00:00:02Z", status=502, ttft=None),
            _row("p", "2026-01-01T00:00:03Z", requestType="model-sync"), _row("x", "2026-01-01T00:00:04Z", status=0)]
    ingest_call_logs(state, rows)
    ingest_call_logs(state, [*rows, _row("c", "2026-01-01T00:00:05Z")])
    text = "\n".join(render_state(state))
    assert 'llm_env_omniroute_calls_total{model="m",route="my-coding",status="2xx"} 2' in text
    assert 'status="5xx"} 1' in text
    assert 'llm_env_omniroute_ttft_ms_count{model="m",route="my-coding"} 2' in text
    assert 'ttft_ms_bucket{model="m",route="my-coding",le="1000"} 2' in text


def test_history_lines_emit_lifetime_counters_per_provider_model():
    lines = history_lines({"byModel": {"k": {"provider": "codex", "rawModel": "gpt", "requests": 5,
                                              "promptTokens": 10, "completionTokens": 2, "cachedInputTokens": 1, "cost": 0.5}}}, allow=["gpt"])
    assert 'llm_env_omniroute_requests_total{provider="codex",model="gpt"} 5' in lines
    assert 'llm_env_omniroute_tokens_total{provider="codex",model="gpt",kind="completion"} 2' in lines


def test_started_before_cursor_finished_later_is_counted_once():
    state = {"omni_cursor": {"ts": "2026-01-01T00:10:00Z", "seen": {}}, "allow": ["m"]}
    row = _row("late", "2026-01-01T00:01:00Z")
    ingest_call_logs(state, [row])
    ingest_call_logs(state, [row])
    assert sum(_counter(state).values()) == 1


def test_identical_timestamps_count_both_rows():
    state: dict = {"allow": ["m"]}
    ingest_call_logs(state, [_row("a", "2026-01-01T00:00:00Z"), _row("b", "2026-01-01T00:00:00+00:00")])
    assert sum(_counter(state).values()) == 2


def test_old_format_state_migrates_without_double_counting():
    state = {"omni_cursor": {"ts": "2026-01-01T00:00:02Z", "ids": ["a"]}, "allow": ["m"]}
    ingest_call_logs(state, [_row("a", "2026-01-01T00:00:02Z")])
    assert sum(_counter(state).values()) == 0
    assert state["omni_cursor"]["seen"]["a"] == "2026-01-01T00:00:02Z"


def test_duplicate_rows_remain_ignored_after_state_reload(tmp_path):
    from pylib.observe_usage import _load, _save

    path = tmp_path / "state.json"
    state: dict = {}
    rows = [_row("a", "2026-01-01T00:00:00Z"), _row("b", "2026-01-01T00:00:00Z")]
    ingest_call_logs(state, rows)
    _save(path, state)
    restored = _load(path)
    assert restored is not None
    before = json.dumps(restored, sort_keys=True)
    ingest_call_logs(restored, rows)
    assert json.dumps(restored, sort_keys=True) == before


def test_allowlist_stays_monotonic_and_41st_model_uses_other():
    state = {"allow": [f"m{i}" for i in range(40)]}
    rows = [_row("a", "2026-01-01T00:00:00Z", model="m0"), _row("b", "2026-01-01T00:00:01Z", model="m40")]
    ingest_call_logs(state, rows)
    ingest_call_logs(state, [_row("c", "2026-01-01T00:00:02Z", model="m0")])
    text = "\n".join(render_state(state))
    assert 'llm_env_omniroute_calls_total{model="m0",route="my-coding",status="2xx"} 2' in text
    assert 'llm_env_omniroute_calls_total{model="other",route="my-coding",status="2xx"} 1' in text
    assert len(state["allow"]) == 40


def test_history_lines_omit_missing_usage_field_and_roll_up_other():
    history = {"byModel": {str(i): {"provider": "p", "rawModel": f"m{i}", "requests": i + 1,
                                     "promptTokens": 2, "cost": 1.0} for i in range(41)}}
    history["byModel"]["40"].pop("promptTokens")
    lines = history_lines(history, allow=[f"m{i}" for i in range(40)])
    assert any('model="m0",kind="prompt"} 2' in line for line in lines)
    assert 'model="other"} 41' in next(line for line in lines if "requests_total" in line and 'model="other"' in line)
    assert any("tokens_total" in line and 'model="m0",kind="prompt"} 2' in line for line in lines)
    assert not any('model="other",kind="prompt"' in line for line in lines)


def test_history_invalid_shapes_and_empty_emit_nothing():
    from pylib.observe_usage import _allowlist
    for history in ({}, {"byModel": "invalid"}):
        assert history_lines(history) == []
        assert _allowlist(history) == []
    assert history_lines({"byModel": {"bad": None}}) == []
    assert _allowlist({"byModel": {"bad": None}}) == []


def test_cost_preserves_float_precision():
    lines = history_lines({"byModel": {"m": {"provider": "p", "rawModel": "m", "cost": 1234.5678912}}}, allow=["m"])
    assert any(line.endswith(" 1234.5678912") for line in lines)


def test_allowlist_ranks_top_40_by_requests():
    from pylib.observe_usage import _allowlist
    history = {"byModel": {str(i): {"rawModel": f"m{i}", "requests": i + 1} for i in range(45)}}
    allowed = _allowlist(history)
    assert len(allowed) == 40
    assert "m44" in allowed and "m5" in allowed and "m4" not in allowed


def test_missing_bad_call_log_fields_are_skipped():
    state: dict = {"allow": ["m"]}
    ingest_call_logs(state, [_row("ok", "2026-01-01T00:00:00Z"), {"id": "no-ts", "status": 200},
                             {"timestamp": "2026-01-01T00:00:00Z", "status": 200}, _row("bad-time", "not-a-time"),
                             {**_row("bad-status", "2026-01-01T00:00:01Z"), "status": "200"}])
    assert sum(_counter(state).values()) == 1
    assert state["c"]["llm_env_usage_skipped_rows_total"] == {'[["source", "omniroute"]]': 4}
    ingest_call_logs(state, [_row("bad-time", "not-a-time")])
    assert state["c"]["llm_env_usage_skipped_rows_total"] == {'[["source", "omniroute"]]': 4}


def test_migration_preserves_legacy_ts_ids_boundary():
    state = {"omni_cursor": {"ts": "2026-01-01T00:00:10Z", "ids": ["a"]}, "allow": ["m"]}
    rows = [_row("old", "2026-01-01T00:00:05Z"), _row("a", "2026-01-01T00:00:10Z"),
            _row("b", "2026-01-01T00:00:10Z"), _row("new", "2026-01-01T00:00:11Z")]
    ingest_call_logs(state, rows)
    assert sum(_counter(state).values()) == 2


def test_naive_timestamps_are_utc(monkeypatch):
    import os
    import time
    original_tz = os.environ.get("TZ")
    monkeypatch.setenv("TZ", "America/Santiago")
    time.tzset()
    from pylib.observe_usage import _dt
    assert _dt("2026-01-01T00:00:00").isoformat() == "2026-01-01T00:00:00+00:00"
    if original_tz is None:
        monkeypatch.delenv("TZ")
    else:
        monkeypatch.setenv("TZ", original_tz)
    time.tzset()


def test_empty_and_null_allowlists_are_repaired():
    for value in ([], None):
        state = {"allow": value}
        rows = [_row("a", "2026-01-01T00:00:00Z", model="m")]
        ingest_call_logs(state, rows)
        assert sum(_counter(state).values()) == 0
        state["allow"] = ["m"]
        ingest_call_logs(state, rows)
        assert sum(_counter(state).values()) == 1


def test_stored_short_allowlist_wins_over_history():
    history = {"byModel": {"m": {"provider": "p", "rawModel": "m", "requests": 2},
                           "n": {"provider": "p", "rawModel": "n", "requests": 10}}}
    assert 'model="other"' in "\n".join(history_lines(history, allow=["m"]))
    assert not any('model="n"' in line for line in history_lines(history, allow=["m"]))


def test_route_cap_and_missing_combo_is_direct():
    state = {"allow": ["m"], "routes": [f"r{i}" for i in range(20)]}
    ingest_call_logs(state, [_row("x", "2026-01-01T00:00:01Z", combo="r20"),
                             _row("y", "2026-01-01T00:00:02Z", combo=None)])
    text = "\n".join(render_state(state))
    assert 'route="other"' in text and 'route="direct"' in text
    assert len(state["routes"]) == 20


def test_future_call_log_is_skipped_and_cursor_does_not_advance():
    from datetime import UTC, datetime
    state: dict = {"allow": ["m"]}
    now = datetime(2026, 1, 1, tzinfo=UTC)
    ingest_call_logs(state, [_row("future", "2099-01-01T00:00:00Z"), _row("now", "2026-01-01T00:00:00Z")], now=now)
    assert sum(_counter(state).values()) == 1
    assert state["omni_cursor"]["ts"] == "2026-01-01T00:00:00Z"


def test_empty_history_emits_nothing():
    assert history_lines({"byModel": {}}) == []


def test_state_file_mode_is_private(tmp_path):
    from pylib.observe_usage import _save
    path = tmp_path / "state.json"
    _save(path, {"x": 1})
    assert path.stat().st_mode & 0o777 == 0o600


def test_iso_timestamp_variants_order_for_lookback():
    state = {"omni_cursor": {"ts": "2026-01-01T00:10:00.123Z", "seen": {}} , "allow": ["m"]}
    ingest_call_logs(state, [_row("within", "2026-01-01T00:00:00.124+00:00"),
                             _row("too-old", "2026-01-01T00:00:00.122Z")])
    assert sum(_counter(state).values()) == 1


def test_corrupt_state_is_preserved_and_skips_collection(monkeypatch, tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{")
    monkeypatch.setattr("pylib.observe_usage.bounded_login", lambda *args: (_ for _ in ()).throw(AssertionError("should not fetch")))
    text = collect_usage(1, "secret", path=path)
    assert text == 'llm_env_source_up{source="usage_state"} 0\n'
    assert path.read_text() == "{"


def test_overflow_increments_gap_counter(monkeypatch, tmp_path):
    import pylib.observe_usage as usage

    rows = [_row(str(i), f"2026-01-01T00:09:{i % 60:02d}Z") for i in range(1500)]
    monkeypatch.setattr(usage, "bounded_login", lambda *args: "token")
    monkeypatch.setattr(usage, "bounded_get", lambda base, endpoint, token, deadline: json.dumps({"byModel": {"m": {"rawModel": "m", "requests": 1}}} if "history" in endpoint else rows))
    path = tmp_path / "state.json"
    usage._save(path, {"omni_cursor": {"ts": "2026-01-01T00:00:00Z", "seen": {}}})
    text = collect_usage(1, "secret", path=path)
    assert 'llm_env_usage_gap_total{source="omniroute"} 1' in text

def test_failed_fetch_preserves_state_bytes_and_sets_source_down(monkeypatch, tmp_path):
    import pylib.observe_usage as usage

    monkeypatch.setattr(usage, "bounded_login", lambda *args: "token")
    monkeypatch.setattr(usage, "bounded_get", lambda *args: (_ for _ in ()).throw(OSError("offline")))
    path = tmp_path / "state.json"
    usage._save(path, {"sentinel": [1, 2, 3]})
    before = path.read_bytes()
    text = collect_usage(1, "secret", path=path)
    assert path.read_bytes() == before
    assert 'llm_env_source_up{source="omniroute_usage"} 0' in text

def test_empty_then_valid_history_sets_allowlist_before_counting(monkeypatch, tmp_path):
    import pylib.observe_usage as usage

    histories = [{"byModel": {}}, {"byModel": {"m": {"provider": "p", "rawModel": "m", "requests": 3}}}]
    calls = [_row("first", "2026-01-01T00:00:01Z", model="m"), _row("second", "2026-01-01T00:00:02Z", model="m")]
    def get(base, endpoint, token, deadline):
        if "history" in endpoint:
            return json.dumps(histories.pop(0))
        return json.dumps([calls.pop(0)])
    monkeypatch.setattr(usage, "bounded_login", lambda *args: "token")
    monkeypatch.setattr(usage, "bounded_get", get)
    path = tmp_path / "state.json"
    first = collect_usage(1, "secret", path=path)
    assert "model=\"other\"" not in first
    saved = usage._load(path)
    assert saved is not None and "allow" not in saved
    second = collect_usage(1, "secret", path=path)
    assert 'model="m",route="my-coding",status="2xx"} 1' in second
    assert 'model="other",route="my-coding"' not in second
    saved = usage._load(path)
    assert saved is not None and saved["allow"] == ["m"]

def test_gap_requires_full_batch_and_oldest_finished_within_lookback(monkeypatch, tmp_path):
    import pylib.observe_usage as usage
    monkeypatch.setattr(usage, "bounded_login", lambda *args: "token")
    monkeypatch.setattr(usage, "bounded_get", lambda base, endpoint, token, deadline: json.dumps({"byModel": {"m": {"rawModel": "m", "requests": 1}}} if "history" in endpoint else rows))
    path = tmp_path / "state.json"
    for full, expected in ((False, False), (True, True)):
        rows = [_row(str(i), "2026-01-01T00:09:00Z", model="m") for i in range(1501 if full else 1499)]
        usage._save(path, {"omni_cursor": {"ts": "2026-01-01T00:00:00Z", "seen": {}}, "allow": ["m"]})
        text = collect_usage(1, "secret", path=path)
        assert ('llm_env_usage_gap_total' in text) is expected

def test_save_failure_still_renders_and_sets_state_down(monkeypatch, tmp_path):
    import pylib.observe_usage as usage
    monkeypatch.setattr(usage, "bounded_login", lambda *args: "token")
    monkeypatch.setattr(usage, "bounded_get", lambda base, endpoint, token, deadline: json.dumps({"byModel": {"m": {"rawModel": "m", "requests": 1}}} if "history" in endpoint else [_row("o", "2026-01-01T00:00:01Z")]))
    monkeypatch.setattr(usage, "_save", lambda *a: (_ for _ in ()).throw(OSError()))
    text = usage.collect_usage(1, "secret", path=tmp_path / "state.json")
    assert 'model="m"' in text and 'source="usage_state"} 0' in text

def test_collect_preserves_short_allowlist_and_routes_third_model_to_other(monkeypatch, tmp_path):
    import pylib.observe_usage as usage
    history = {"byModel": {f"m{i}": {"rawModel": f"m{i}", "requests": 40-i} for i in range(40)}}
    monkeypatch.setattr(usage, "bounded_login", lambda *args: "token")
    monkeypatch.setattr(usage, "bounded_get", lambda base, endpoint, token, deadline: json.dumps(history if "history" in endpoint else [_row("x", "2026-01-01T00:00:01Z", model="m2")]))
    path = tmp_path / "state.json"
    usage._save(path, {"allow": ["m1", "m0"], "omni_cursor": {"ts": "", "seen": {}}})
    text = usage.collect_usage(1, "secret", path=path)
    saved = usage._load(path)
    assert saved is not None
    assert saved["allow"] == ["m1", "m0"]
    assert 'model="other",route="my-coding",status="2xx"' in text

def test_laya_source_failure_does_not_rollback_omniroute(monkeypatch, tmp_path):
    import pylib.observe_usage as usage
    state = {"c": {"llm_env_omniroute_calls_total": {'[]': 2}}}
    original = json.loads(json.dumps(state))
    monkeypatch.setattr(usage, "collect_laya_file", lambda *a, **k: (_ for _ in ()).throw(OSError("corrupt sink")))
    restored = usage._restore_source(state, original, "laya")
    assert restored["c"]["llm_env_omniroute_calls_total"] == {'[]': 2}


def test_failed_logs_after_history_keeps_multikey_state_bytes(monkeypatch, tmp_path):
    import pylib.observe_usage as usage
    from pylib.observe_usage import _save
    monkeypatch.setattr(usage, "bounded_login", lambda *args: "token")
    def get(base, endpoint, token, deadline):
        if "history" in endpoint:
            return json.dumps({"byModel": {"m": {"provider": "p", "rawModel": "m", "requests": 3}}})
        raise OSError("offline")
    monkeypatch.setattr(usage, "bounded_get", get)
    path = tmp_path / "state.json"
    _save(path, {"omni_cursor": {"ts": "2026-01-01T00:00:00Z", "seen": {}}, "allow": ["m"], "routes": ["r"],
                  "c": {"llm_env_omniroute_calls_total": {'[]': 1}},
                  "h": {"llm_env_omniroute_duration_ms": {'[]': {"b": [0] * 10, "s": 2}}}})
    before = path.read_bytes()
    collect_usage(1, "secret", path=path)
    assert path.read_bytes() == before

def _file_record(span_id="0123456789abcdef", **overrides):
    return {"v": 1, "ts": "2026-01-01T00:00:00Z", "span_id": span_id,
            "endpoint": "/v1/systemone", "batch": False, "http_status": 200, "duration_ms": 30,
            "answers": {"choice": 1, "noul": 0, "score": 0}, "confidences": [0.97], **overrides}


def test_laya_file_contract_counts_metrics_and_preserves_legacy_totals(tmp_path):
    from pylib.observe_usage import collect_laya_file
    source = tmp_path / "laya-spans.jsonl"
    source.write_text(json.dumps(_file_record()) + "\n")
    state = {"c": {"llm_env_laya_requests_total": {'[["batch", "false"], ["endpoint", "/v1/systemone"], ["status", "2xx"]]': 1000}}}
    up = collect_laya_file(state, source, deadline_s=1)
    text = "\n".join(render_state(state))
    assert up == 1
    assert 'llm_env_laya_requests_total{batch="false",endpoint="/v1/systemone",status="2xx"} 1001' in text
    assert 'llm_env_laya_answers_total{type="choice"} 1' in text
    assert 'llm_env_laya_duration_ms_count{endpoint="/v1/systemone"} 1' in text


def test_laya_file_defers_torn_line_then_counts_completed_once(tmp_path):
    from pylib.observe_usage import collect_laya_file
    source = tmp_path / "spans.jsonl"
    state = {}
    raw = json.dumps(_file_record()).encode()
    source.write_bytes(raw)
    collect_laya_file(state, source, deadline_s=1)
    assert not state.get("c")
    with source.open("ab") as stream:
        stream.write(b"\n")
    collect_laya_file(state, source, deadline_s=1)
    collect_laya_file(state, source, deadline_s=1)
    assert sum(state["c"]["llm_env_laya_requests_total"].values()) == 1


def test_laya_file_rotation_reads_old_rotated_tail_once(tmp_path, monkeypatch):
    import pylib.observe_usage as usage
    source = tmp_path / "spans.jsonl"
    rotated = source.with_name(source.name + ".1")
    records = [_file_record(f"{i:016x}", ts=f"2026-01-01T00:00:0{i}Z") for i in (1, 2, 3)]
    source.write_text("".join(json.dumps(r) + "\n" for r in records))
    state = {}
    monkeypatch.setattr(usage, "MAX_LAYA_RECORDS", 1)
    usage.collect_laya_file(state, source, deadline_s=1)
    assert sum(state["c"]["llm_env_laya_requests_total"].values()) == 1
    source.replace(rotated)
    source.write_text(json.dumps(_file_record("aaaaaaaaaaaaaaaa", ts="2026-01-01T00:00:04Z")) + "\n")
    for _ in range(4): usage.collect_laya_file(state, source, deadline_s=1)
    assert sum(state["c"]["llm_env_laya_requests_total"].values()) == 4


def test_laya_file_rotation_does_not_carry_oversize_discard_state(tmp_path):
    import pylib.observe_usage as usage
    source = tmp_path / "spans.jsonl"
    rotated = source.with_name(source.name + ".1")
    source.write_bytes(b"x" * (usage.MAX_LAYA_LINE + 1))
    state = {}
    usage.collect_laya_file(state, source, deadline_s=1)
    assert state["laya_file_cursor"]["discarding"] is True
    source.replace(rotated)
    source.write_text("".join(json.dumps(_file_record(f"{i:016x}", ts=f"2026-01-01T00:00:{i:02d}Z")) + "\n" for i in range(4)))
    for _ in range(4):
        usage.collect_laya_file(state, source, deadline_s=1)
    assert sum(state["c"]["llm_env_laya_requests_total"].values()) == 4
    assert state["c"]["llm_env_laya_skipped_records_total"] == {'[["reason", "too_long"]]': 1}


def test_laya_file_replacement_and_shrink_clear_discard_state(tmp_path):
    from pylib.observe_usage import collect_laya_file
    source = tmp_path / "spans.jsonl"
    state = {"laya_file_cursor": {"inode": 123, "offset": 1000, "discarding": True}}
    source.write_text("".join(json.dumps(_file_record(f"{i:016x}", ts=f"2026-01-01T00:00:{i:02d}Z")) + "\n" for i in range(4)))
    collect_laya_file(state, source, deadline_s=1)
    assert sum(state["c"]["llm_env_laya_requests_total"].values()) == 4

    state["laya_file_cursor"].update(inode=source.stat().st_ino, offset=source.stat().st_size, discarding=True)
    source.write_text(json.dumps(_file_record("aaaaaaaaaaaaaaaa")) + "\n")
    collect_laya_file(state, source, deadline_s=1)
    assert state["laya_file_cursor"]["discarding"] is False


def test_laya_file_dedupe_set_handles_10000_seen_ids_and_3000_records(tmp_path):
    import time

    from pylib.observe_usage import collect_laya_file
    source = tmp_path / "spans.jsonl"
    source.write_text("".join(json.dumps(_file_record(f"{i + 10000:016x}", ts=f"2026-01-01T00:{i // 60:02d}:{i % 60:02d}Z")) + "\n" for i in range(3000)))
    state: dict = {"laya_file_seen": [f"{i:016x}" for i in range(10000)]}
    started = time.monotonic()
    while state.get("laya_file_cursor", {}).get("offset", 0) < source.stat().st_size:
        collect_laya_file(state, source, deadline_s=10)
    assert time.monotonic() - started < 5
    assert sum(state["c"]["llm_env_laya_requests_total"].values()) == 3000
    assert len(state["laya_file_seen"]) == 10000


def test_laya_file_invalid_record_is_skipped_and_private(tmp_path):
    from pylib.observe_usage import collect_laya_file
    source = tmp_path / "spans.jsonl"
    source.write_text(json.dumps(_file_record(question="PRIVATE SENTINEL", model="PRIVATE MODEL", error_type="PRIVATE ERROR")) + "\n")
    state = {}
    assert collect_laya_file(state, source, deadline_s=1) == 1
    assert sum(state["c"]["llm_env_laya_requests_total"].values()) == 1
    assert all(sentinel not in json.dumps(state) for sentinel in ("PRIVATE SENTINEL", "PRIVATE MODEL", "PRIVATE ERROR"))
    assert "PRIVATE SENTINEL" not in "\n".join(render_state(state))


def test_laya_file_missing_after_cursor_stays_up(tmp_path):
    from pylib.observe_usage import collect_laya_file
    source = tmp_path / "spans.jsonl"
    source.touch()
    state = {}
    assert collect_laya_file(state, source) == 1
    source.unlink()
    assert collect_laya_file(state, source) == 1


def test_laya_file_rejects_version_two(tmp_path):
    from pylib.observe_usage import collect_laya_file
    source = tmp_path / "spans.jsonl"
    source.write_text(json.dumps(_file_record(v=2)) + "\n")
    state = {}
    collect_laya_file(state, source)
    assert state["c"]["llm_env_laya_skipped_records_total"] == {'[["reason", "version"]]': 1}
    assert not state.get("h")


def test_laya_file_rejects_text_span_id(tmp_path):
    from pylib.observe_usage import collect_laya_file
    source = tmp_path / "spans.jsonl"
    source.write_text(json.dumps(_file_record("PRIVATE SPAN SENTINEL")) + "\n")
    state = {}
    collect_laya_file(state, source)
    assert state["c"]["llm_env_laya_skipped_records_total"] == {'[["reason", "span_id"]]': 1}
    assert "PRIVATE SPAN SENTINEL" not in json.dumps(state)


def test_skipped_laya_record_emits_one_valid_counter_type(tmp_path):
    from pylib.observe_metrics import valid_exposition_fragment
    source = tmp_path / "spans.jsonl"
    source.write_text(json.dumps(_file_record("not-a-hex-span")) + "\n")
    state = {}
    from pylib.observe_usage import collect_laya_file
    collect_laya_file(state, source)
    text = "\n".join(render_state(state)) + "\n"
    assert text.count("# TYPE llm_env_laya_skipped_records_total counter") == 1
    assert valid_exposition_fragment(text)
    assert text.count("# TYPE llm_env_laya_skipped_records_total ") == 1


def test_laya_file_absent_is_down_and_empty_is_up(tmp_path):
    from pylib.observe_usage import collect_laya_file
    source = tmp_path / "missing" / "spans.jsonl"
    assert collect_laya_file({}, source, deadline_s=1) == 0
    source.parent.mkdir()
    source.touch()
    assert collect_laya_file({}, source, deadline_s=1) == 1


def test_laya_file_cursor_migrates_real_schema_without_rebasing_counters(tmp_path):
    from pylib.observe_usage import collect_laya_file
    legacy = json.loads((Path(__file__).parent / "fixtures/usage-state-phb.json").read_text())
    before_requests = sum(legacy["c"]["llm_env_laya_requests_total"].values())
    before_answers = {k: v for k, v in legacy["c"]["llm_env_laya_answers_total"].items()}
    source = tmp_path / "laya-spans.jsonl"
    source.write_text(json.dumps(_file_record()) + "\n")
    collect_laya_file(legacy, source, deadline_s=1)
    assert sum(legacy["c"]["llm_env_laya_requests_total"].values()) == before_requests + 1
    assert legacy["c"]["llm_env_laya_answers_total"]['[["type", "choice"]]'] == before_answers['[["type", "choice"]]'] + 1
    assert legacy["laya_file_cursor"]["offset"] == source.stat().st_size
    assert "laya_cursor" not in legacy
    assert legacy["h"]["llm_env_laya_duration_ms"][next(iter(legacy["h"]["llm_env_laya_duration_ms"]))]["s"] >= 56491


def test_laya_file_large_input_advances_in_bounded_ticks(tmp_path, monkeypatch):
    import pylib.observe_usage as usage
    source = tmp_path / "spans.jsonl"
    source.write_text("".join(json.dumps(_file_record(f"{i:016x}")) + "\n" for i in range(30)))
    monkeypatch.setattr(usage, "MAX_LAYA_RECORDS", 7)
    state = {}
    for _ in range(5):
        usage.collect_laya_file(state, source, deadline_s=1)
    assert sum(state["c"]["llm_env_laya_requests_total"].values()) == 30
    assert state["laya_file_cursor"]["offset"] == source.stat().st_size


def test_laya_file_large_files_and_one_oversized_record(tmp_path):
    import pylib.observe_usage as usage
    source = tmp_path / "large.jsonl"
    normal = [_file_record(f"{i:016x}") for i in range(300)]
    source.write_text("".join(json.dumps(record) + "\n" for record in normal))
    state = {}
    usage.collect_laya_file(state, source)
    assert sum(state["c"]["llm_env_laya_requests_total"].values()) == 300
    source.write_text(json.dumps(_file_record("aaaaaaaaaaaaaaaa")) + "\n" + "x" * 300_000 + "\n" + json.dumps(_file_record("bbbbbbbbbbbbbbbb")) + "\n")
    state = {}
    while usage.collect_laya_file(state, source):
        if state["laya_file_cursor"]["offset"] == source.stat().st_size:
            break
    assert sum(state["c"]["llm_env_laya_requests_total"].values()) == 2
    assert state["c"]["llm_env_laya_skipped_records_total"] == {'[["reason", "too_long"]]': 1}
    assert state["laya_file_cursor"]["offset"] == source.stat().st_size


def test_laya_empty_file_decrease_preserves_prior_counters(tmp_path):
    from pylib.observe_usage import collect_laya_file
    source = tmp_path / "spans.jsonl"
    source.write_text(json.dumps(_file_record()) + "\n")
    state = {}
    collect_laya_file(state, source)
    expected = sum(state["c"]["llm_env_laya_requests_total"].values())
    source.write_text("")
    collect_laya_file(state, source)
    assert sum(state["c"]["llm_env_laya_requests_total"].values()) == expected == 1


def test_laya_file_dedupe_survives_inode_rotation(tmp_path):
    from pylib.observe_usage import collect_laya_file
    source = tmp_path / "spans.jsonl"
    rotated = source.with_name(source.name + ".1")
    record = _file_record()
    source.write_text(json.dumps(record) + "\n")
    state = {}
    collect_laya_file(state, source, deadline_s=1)
    source.replace(rotated)
    source.write_text(json.dumps(record) + "\n")
    collect_laya_file(state, source, deadline_s=1)
    assert sum(state["c"]["llm_env_laya_requests_total"].values()) == 1
