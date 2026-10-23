"""Deterministic unit coverage for the characterization candidate rules.

`tests/test_omniroute_characterize.py` is skipped unless `LLM_ENV_UPDATE_CHARACTERIZE=1`, so the
derivation rules it carries would otherwise never run in `make test`. These cases drive synthetic
evidence shaped exactly like the sampler's output (JSON-pointer keys, `<pointer>.length` for arrays).
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from test_omniroute_characterize import derive_candidate

HEALTH = "/api/monitoring/health"
HISTORY = "/api/usage/history"
LOGS = "/api/usage/call-logs?limit=1000&offset=0"
TICKS = 40
START = 1000.0


def _access(method: str, path: str) -> str:
    message = json.dumps({"level": 30, "method": method, "path": path, "msg": "incoming"}, sort_keys=True)
    return json.dumps({"MESSAGE": message}, sort_keys=True)


def _evidence(*, active_counter: str | None = None, timestamps: list[int] | None = None,
              burst_lines: int = 200, rows_length: int = 3) -> dict:
    """Synthetic sampler output: a stream pulse at ticks 10-18, a queue pulse at ticks 24-30."""
    def tick(index: int) -> float:
        return START + index * 0.5

    in_stream = lambda i: 10 <= i <= 18
    in_queue = lambda i: 24 <= i <= 30

    history = []
    for i in range(TICKS):
        body: dict = {"/totalRequests": i, "/pending/byModel/fake-model (llama-cpp)": 1 if in_stream(i) else 0}
        if active_counter:
            body[active_counter] = 2 if in_stream(i) else 0
        history.append({"t": tick(i), "body": body})

    # the health timestamp is cached: it advances once per second (every two ticks), which is what the
    # dwell and refresh-bound rules measure
    base = timestamps if timestamps is not None else [1_792_790_609_000 + (i // 2) * 1000 for i in range(TICKS)]
    health = [{"t": tick(i),
               "body": {"/timestamp": "2026-10-23T21:19:20.000Z",
                        "/adaptiveAdmission/queuedCount": 3 if in_queue(i) else 0,
                        "/chatAdmission/activeHeavy": 1 if in_queue(i) else 0,
                        "/adaptiveAdmission/resourceObservedAtMs": base[i % len(base)]}}
              for i in range(TICKS)]
    logs = [{"t": tick(i), "body": {".length": rows_length, "/0/id": "r", "/0/active": False}}
            for i in range(TICKS)]

    scenarios = [
        {"scenario": "long_stream", "requests": 1, "ok": 1,
         "window_start": tick(8), "window_end": tick(20)},
        {"scenario": "queued_request", "requests": 8, "ok": 8,
         "window_start": tick(22), "window_end": tick(33)},
        {"scenario": "burst", "requests": 200, "ok": 200, "client_log_lines": burst_lines},
        {"scenario": "native_shutdown_drain", "stop_rc": 0, "stop_duration_s": 0.3,
         "stream_completed": True},
        {"scenario": "boundary_arrival", "requests": 1, "ok": 1, "arrival_offset_ms": -12.5},
    ]
    return {"config_id": "sha256:" + "0" * 64,
            "series": {HEALTH: health, HISTORY: history, LOGS: logs},
            "journal": [_access("POST", "/v1/chat/completions | llama-cpp/fake-model | 1 msgs"),
                        _access("GET", "/v1/models"), ""],
            "scenarios": scenarios}


def test_derives_every_field_when_the_signals_exist():
    candidate = derive_candidate(_evidence(active_counter="/activeRequestCount"))
    fields = candidate["api_fields"]
    assert fields["active"] == "/activeRequestCount"
    assert fields["pending_by_model"] == "/pending/byModel"
    assert fields["admission"] == ["/adaptiveAdmission/queuedCount", "/chatAdmission/activeHeavy"]
    assert (fields["rows"], fields["row_id"], fields["row_memory_flag"]) == ("", "id", "active")
    assert candidate["health_timestamp"] == "/adaptiveAdmission/resourceObservedAtMs"
    # the profile stores the journal *field names*, because pylib reads inner[fields["method"]]
    assert candidate["logger"]["fields"] == {"method": "method", "path": "path"}
    assert candidate["logger"]["loss_detection"] == {"requests": 200, "lines": 200, "loss_detected": False}
    assert {"method": "POST", "path_pattern": "/v1/chat/completions"} in candidate["reachable_routes"]
    assert candidate["unobserved_routes_unreviewed"] is True
    assert candidate["health_cache_ms"] > 0 and candidate["health_refresh_bound_ms"] > 0
    assert candidate["health_refresh_bound_ms"] % 100 == 0
    assert candidate["incomplete"] == []


def test_cumulative_counters_are_not_an_active_signal():
    candidate = derive_candidate(_evidence(active_counter=None))
    assert "active" not in candidate["api_fields"]
    assert any(entry.startswith("api_fields.active") for entry in candidate["incomplete"])
    assert candidate["review_notes"]["active_list_only"] == []


def test_journald_suppression_drops_the_logger_and_the_routes_measured_through_it():
    candidate = derive_candidate(_evidence(active_counter="/activeRequestCount", burst_lines=150))
    assert "logger" not in candidate and "routes" not in candidate
    assert "reachable_routes" not in candidate
    assert any(entry.startswith("logger (journald suppressed 50") for entry in candidate["incomplete"])


def test_non_monotonic_timestamps_are_not_a_health_timestamp():
    jittered = [1_792_790_609_000 + (i % 2) for i in range(TICKS)]
    candidate = derive_candidate(_evidence(active_counter="/activeRequestCount", timestamps=jittered))
    assert candidate["health_timestamp"] is None
    assert any(entry.startswith("health_timestamp") for entry in candidate["incomplete"])
    assert candidate["review_notes"]["health_timestamp_strings"] == ["/timestamp"]


def test_a_zero_refresh_bound_is_reported_missing_not_emitted():
    # a clock that only ever advances on the sampler cadence leaves no measurable refresh window
    flat = [1_792_790_609_000] * TICKS
    candidate = derive_candidate(_evidence(active_counter="/activeRequestCount", timestamps=flat))
    assert candidate["health_timestamp"] is None


def test_empty_rows_page_is_reported_as_incomplete():
    candidate = derive_candidate(_evidence(active_counter="/activeRequestCount", rows_length=0))
    assert any(entry == "api_fields.rows" for entry in candidate["incomplete"])


@pytest.mark.parametrize("pointer,root", [("", True), ("/rows", False)])
def test_row_pointer_follows_the_measured_response_shape(pointer, root):
    evidence = _evidence(active_counter="/activeRequestCount")
    prefix = "" if root else "/rows"
    body = {f"{prefix}.length": 3, f"{prefix}/0/id": "r", f"{prefix}/0/active": False}
    for record in evidence["series"][LOGS]:
        record["body"] = body
    assert derive_candidate(evidence)["api_fields"]["rows"] == pointer
