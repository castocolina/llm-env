"""Deterministic unit coverage for the characterization candidate rules.

The derivation rules live in `pylib/omniroute_characterize.py`; the harness that feeds them needs a real image
(opt-in), so these cases drive them in `make test`. These cases drive synthetic
evidence shaped exactly like the sampler's output (JSON-pointer keys, `<pointer>.length` for arrays).
"""
from __future__ import annotations

import datetime
import hashlib
import json
import pathlib
import shutil
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from pylib.omniroute_activity import PROFILE_PATH, load_profile
from pylib.omniroute_characterize import derive_candidate, promote_candidate
from pylib.omniroute_update import Deferral, Reason

REPO = pathlib.Path(__file__).resolve().parents[1]

HEALTH = "/api/monitoring/health"
HISTORY = "/api/usage/history"
LOGS = "/api/usage/call-logs?limit=10&offset=0"
TICKS = 40
START = 1_792_790_600.0
CLIENT_ROW = {"path": "/v1/chat/completions", "method": "POST", "active": False}
PROBE_ROW = {"path": "/api/providers/test", "method": "POST", "active": False}


def _access(method: str, path: str) -> str:
    message = json.dumps({"level": 30, "method": method, "path": path, "msg": "incoming"}, sort_keys=True)
    return json.dumps({"MESSAGE": message}, sort_keys=True)


STAMP = "/adaptiveAdmission/resourceObservedAtMs"


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
    # call-log pages, newest first: one old client row, and from tick 35 an internal connection-test row
    client_row = dict(CLIENT_ROW, timestamp=_iso(int(START * 1000)))
    probe_row = dict(PROBE_ROW, timestamp=_iso(int(tick(35) * 1000)))
    logs = [{"t": tick(i), "body": _page("", ([probe_row] if i >= 35 else []) + [client_row] if rows_length else [])}
            for i in range(TICKS)]

    scenarios = [
        {"scenario": "long_stream", "requests": 1, "ok": 1,
         "window_start": tick(8), "window_end": tick(20)},
        {"scenario": "queued_request", "requests": 8, "ok": 8,
         "window_start": tick(22), "window_end": tick(33)},
        {"scenario": "provider_test", "requests": 1, "ok": 1, "window_start": tick(34), "window_end": tick(38)},
        {"scenario": "burst", "requests": 200, "ok": 200, "client_log_lines": burst_lines},
        {"scenario": "native_shutdown_drain", "stop_rc": 0, "stop_duration_s": 0.3,
         "stream_completed": True},
        {"scenario": "boundary_arrival", "requests": 1, "ok": 1, "arrival_offset_ms": -12.5},
        {"scenario": "idle_poll", "interval_s": 5.0, "prime_gap_s": 0.2, "start": tick(TICKS), "end": tick(TICKS) + 31,
         "polls": [{"t_lone": t, "lone": {STAMP: int(t * 1000) - 5000}, "t_read": t + 0.2,
                    "read": {STAMP: int(t * 1000)}} for t in (tick(TICKS) + 5.0 * k for k in range(1, 7))]},
    ]
    return {"config_id": "sha256:" + "0" * 64,
            "series": {HEALTH: health, HISTORY: history, LOGS: logs},
            "row_table": ROW_TABLE,
            "journal": [_access("POST", "/v1/chat/completions | llama-cpp/fake-model | 1 msgs"),
                        _access("GET", "/v1/models"), ""],
            "scenarios": scenarios}


ROW_TABLE: dict = {}


def _page(prefix: str, rows: list[dict]) -> dict:
    """A call-log page as the sampler records it: its length, its first row, and every row as a row-table digest."""
    body: dict = {f"{prefix}.length": len(rows), **{f"{prefix}/0/{k}": v for k, v in (rows[:1] or [{}])[0].items()},
                  "#rows": []}
    for row in rows:
        digest = hashlib.sha256(json.dumps(row, sort_keys=True).encode()).hexdigest()[:16]
        ROW_TABLE[digest] = {f"/{k}": v for k, v in row.items()}
        body["#rows"].append(digest)
    return body


def test_derives_every_field_when_the_signals_exist():
    candidate = derive_candidate(_evidence(active_counter="/activeRequestCount"))
    fields = candidate["api_fields"]
    assert fields["active"] == "/activeRequestCount"
    assert fields["pending_by_model"] == "/pending/byModel"
    assert fields["admission"] == ["/adaptiveAdmission/queuedCount", "/chatAdmission/activeHeavy"]
    assert (fields["rows"], fields["row_memory_flag"], fields["row_path"], fields["row_timestamp"],
            fields["rows_page_limit"]) == ("", "active", "path", "timestamp", 10)
    assert candidate["row_probe_routes"] == [{"method": "POST", "path_pattern": "/api/providers/test"}]
    assert candidate["review_notes"]["row_order"] == {"pages": TICKS, "persisted_not_newest_first": 0}
    assert candidate["health_timestamp"] == "/adaptiveAdmission/resourceObservedAtMs"
    # the profile stores the journal *field names*, because pylib reads inner[fields["method"]]
    assert candidate["logger"]["fields"] == {"method": "method", "path": "path"}
    assert candidate["logger"]["loss_detection"] == {"requests": 200, "lines": 200, "loss_detected": False}
    assert {"method": "POST", "path_pattern": "/v1/chat/completions"} in candidate["reachable_routes"]
    assert candidate["unobserved_routes_unreviewed"] is True
    assert candidate["health_cache_ms"] > 0 and candidate["health_refresh_bound_ms"] > 0
    assert candidate["health_refresh_bound_ms"] % 100 == 0
    idle = candidate["review_notes"]["idle_poll"]
    assert idle["lone_age_ms"] == [5000] * 6 and idle["primed_age_ms"] == [200] * 6  # the 5 s regime, primed
    assert candidate["incomplete"] == []


@pytest.mark.parametrize("change", ["missing", "stale", "gap"])
def test_the_idle_regime_must_validate_the_derived_health_bounds(change):
    evidence = _evidence(active_counter="/activeRequestCount")
    idle = evidence["scenarios"][-1]
    if change == "missing":
        evidence["scenarios"].remove(idle)
    elif change == "stale":  # the primed read still answers a snapshot as old as the poll interval
        for poll in idle["polls"]:
            poll["read"] = poll["lone"]
    else:
        idle["prime_gap_s"] = 5.0  # not below the cache window: the read is no longer the primed snapshot
    assert any(e.startswith("idle_poll") for e in derive_candidate(evidence)["incomplete"])


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
    assert any(entry.startswith("api_fields.rows") for entry in candidate["incomplete"])


@pytest.mark.parametrize("pointer,root", [("", True), ("/rows", False)])
def test_row_pointer_follows_the_measured_response_shape(pointer, root):
    evidence = _evidence(active_counter="/activeRequestCount")
    prefix = "" if root else "/rows"
    for record in evidence["series"][LOGS]:
        record["body"] = _page(prefix, [dict(CLIENT_ROW, timestamp=_iso(int(START * 1000)))])
    assert derive_candidate(evidence)["api_fields"]["rows"] == pointer


def test_a_row_path_seen_only_outside_the_provider_test_window_is_never_a_probe():
    evidence = _evidence(active_counter="/activeRequestCount")
    for record in evidence["series"][LOGS]:  # the internal row was written before the window opened
        record["body"] = _page("", [dict(PROBE_ROW, timestamp=_iso(int(START * 1000))),
                                    dict(CLIENT_ROW, timestamp=_iso(int(START * 1000)))])
    candidate = derive_candidate(evidence)
    assert candidate["row_probe_routes"] == []
    assert any(entry.startswith("row probe routes") for entry in candidate["incomplete"])


def test_a_trace_time_ahead_of_the_sampler_or_unreadable_is_not_the_trace_field():
    evidence = _evidence(active_counter="/activeRequestCount")
    for record in evidence["series"][LOGS]:
        record["body"] = _page("", [dict(CLIENT_ROW, timestamp=_iso(int((record["t"] + 5) * 1000)))])
    assert any(e.startswith("api_fields.rows") for e in derive_candidate(evidence)["incomplete"])


# ---- 2026-10-24 addendum: array in-flight signal, deleted-at-zero maps, string clocks, promotion ----

def _iso(ms: int) -> str:
    return datetime.datetime.fromtimestamp(ms / 1000, datetime.UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def test_an_array_whose_length_pulses_is_the_active_signal_when_no_counter_exists():
    evidence = _evidence(active_counter=None)
    for i, record in enumerate(evidence["series"][HISTORY]):
        record["body"]["/activeRequests.length"] = 1 if 10 <= i <= 18 else 0
    candidate = derive_candidate(evidence)
    assert candidate["api_fields"]["active"] == "/activeRequests"
    assert candidate["review_notes"]["active_list_only"] == ["/activeRequests.length"]


def test_a_map_child_deleted_at_zero_still_pulses_and_the_next_scenario_tick_is_ignored():
    evidence = _evidence(active_counter="/activeRequestCount")
    history = evidence["series"][HISTORY]
    for record in history:  # the model key disappears whenever its count is zero
        key = "/pending/byModel/fake-model (llama-cpp)"
        if record["body"][key] == 0:
            del record["body"][key]
    history[21]["body"]["/activeRequestCount"] = 1  # the first tick after the window saw the next scenario's request
    fields = derive_candidate(evidence)["api_fields"]
    assert fields["pending_by_model"] == "/pending/byModel" and fields["active"] == "/activeRequestCount"


def test_a_fractional_counter_is_never_an_admission_signal():
    evidence = _evidence(active_counter="/activeRequestCount")
    for i, record in enumerate(evidence["series"][HEALTH]):
        record["body"]["/adaptiveAdmission/utilization"] = 0.875 if 24 <= i <= 30 else 0
    assert "/adaptiveAdmission/utilization" not in derive_candidate(evidence)["api_fields"]["admission"]


def test_the_most_reliably_refreshed_clock_wins_over_a_traffic_only_timestamp():
    evidence = _evidence(active_counter="/activeRequestCount")
    for i, record in enumerate(evidence["series"][HEALTH]):
        # the document clock advances every second; the resource clock only moves twice, around traffic
        record["body"]["/timestamp"] = _iso(1_792_790_609_000 + (i // 2) * 1000)
        record["body"]["/adaptiveAdmission/resourceObservedAtMs"] = 1_792_790_609_000 + (1000 if i >= 12 else 0) \
            + (1000 if i >= 30 else 0)
    candidate = derive_candidate(evidence)
    assert candidate["health_timestamp"] == "/timestamp"
    assert set(candidate["review_notes"]["health_timestamp_worst_step_ms"]) == {
        "/timestamp", "/adaptiveAdmission/resourceObservedAtMs"}
    assert candidate["health_cache_ms"] > 0 and candidate["health_refresh_bound_ms"] > 0


def test_clocks_refreshed_together_go_to_the_one_that_tracks_the_wall_clock():
    """7d2169fb2bd6 caches the whole health payload 30 s: every clock in it changes at the same ticks, so the worst
    refresh step ties. The snapshot's own time is about one tick old when first seen; an activity clock is older."""
    evidence = _evidence(active_counter="/activeRequestCount")
    for i, record in enumerate(evidence["series"][HEALTH]):
        snap = START + (i // 10) * 5.0  # a new snapshot every 10 ticks, computed one tick before it is first seen
        record["body"]["/timestamp"] = int((snap - 0.5) * 1000)
        record["body"]["/adaptiveAdmission/resourceObservedAtMs"] = int((snap - 4.0) * 1000)
    candidate = derive_candidate(evidence)
    notes = candidate["review_notes"]
    assert len(set(notes["health_timestamp_worst_step_ms"].values())) == 1  # a tie on the refresh step
    assert candidate["health_timestamp"] == "/timestamp"


def _complete_candidate():
    evidence = _evidence(active_counter="/activeRequestCount")
    evidence["journal"] = [line for line in evidence["journal"] if "/v1/models" not in line]
    return derive_candidate(evidence)


def test_promotion_refuses_incomplete_candidates_and_unclassified_routes():
    meta = {"config_id": "sha256:" + "0" * 64, "evidence_name": "e.json", "evidence_sha256": "1" * 64,
            "entrypoint": ["node", "server.js"]}
    with pytest.raises(ValueError, match="incomplete"):
        promote_candidate(derive_candidate(_evidence(active_counter=None)), **meta)
    with pytest.raises(ValueError, match="/v1/models"):  # observed, but its class was never measured
        promote_candidate(derive_candidate(_evidence(active_counter="/activeRequestCount")), **meta)
    entry = promote_candidate(_complete_candidate(), **meta)
    assert entry["reachable_routes"] == ["POST /v1/chat/completions", "POST /api/providers/test"]
    assert entry["routes"][1] == {"method": "POST", "path_pattern": "/api/providers/test", "class": "probe",
                                  "evidence": "1" * 64}
    assert entry["logger"]["level_env"] == "APP_LOG_LEVEL"


def test_a_promoted_entry_passes_the_runtime_loader(tmp_path):
    entry = promote_candidate(_complete_candidate(), config_id="sha256:" + "0" * 64, evidence_name="e.json",
                              evidence_sha256="1" * 64, entrypoint=["node", "server.js"])
    path = tmp_path / "profiles.json"
    path.write_text(json.dumps({"schema": 1, "profiles": [entry]}))
    container = {"Config": {"Env": ["APP_LOG_LEVEL=debug"], "Entrypoint": ["node", "server.js"]},
                 "HostConfig": {"LogConfig": {"Type": "journald"}}}
    assert load_profile("sha256:" + "0" * 64, container, path).entry == entry
    container["Config"]["Env"] = ["APP_LOG_LEVEL=warn"]  # access-log lines are info: a quieter logger defers
    with pytest.raises(Deferral) as e:
        load_profile("sha256:" + "0" * 64, container, path)
    assert e.value.reason is Reason.logging


def test_checked_in_profiles_are_reproduced_from_their_committed_evidence():
    """Every shipped entry is machine-derived: re-running the derivation and promotion over the committed,
    hash-checked evidence must give the entry byte for byte (a hand edit fails here)."""
    doc = json.loads(PROFILE_PATH.read_text())
    assert doc["schema"] == 1 and doc["profiles"]
    for entry in doc["profiles"]:
        ev_path = REPO / entry["evidence"]["characterization"]
        raw = ev_path.read_bytes()
        sha = hashlib.sha256(raw).hexdigest()
        assert sha == entry["evidence"]["characterization_sha256"]
        assert sha == ev_path.with_suffix(".json.sha256").read_text().strip()
        evidence = json.loads(raw)
        assert evidence["config_id"] == entry["config_id"]
        # The entrypoint is a deployment fact the evidence of this vintage does not record; load_profile re-checks it
        # against the running container, so a wrong value can only defer (`logging`), never admit.
        expected = promote_candidate(derive_candidate(evidence), config_id=evidence["config_id"],
                                     evidence_name=entry["evidence"]["characterization"], evidence_sha256=sha,
                                     entrypoint=evidence.get("entrypoint", entry["entrypoint"]))
        assert entry == expected, json.dumps(expected, indent=2, sort_keys=True)


def test_the_characterized_latest_image_loads_and_its_bounds_are_the_measured_document_clock():
    container = {"Config": {"Env": ["NODE_ENV=production"], "Entrypoint": ["/app/check-permissions.sh"]},
                 "HostConfig": {"LogConfig": {"Type": "journald"}}}
    profile = load_profile("sha256:038b2aba95a685b5614992b006e382c98a2c5cfe882bf30ee809b23094a1236a", container)
    assert profile.api_fields["active"] == "/activeRequests"
    assert profile.api_fields["health_timestamp"] == "/timestamp"
    assert (profile.health_cache_ms, profile.health_refresh_bound_ms) == (660, 900)


def test_extra_client_routes_are_inference_only_with_their_own_access_line():
    evidence = _evidence(active_counter="/activeRequestCount")
    evidence["journal"] = [line for line in evidence["journal"] if "/v1/models" not in line]
    evidence["journal"].append(_access("POST", "/v1/messages | llama-cpp/fake-model | 1 msgs"))
    embed_row = dict(CLIENT_ROW, path="/v1/embeddings", timestamp=_iso(int(START * 1000)))
    evidence["series"][LOGS][-1] = {"t": START + 39 * 0.5, "body": _page("", [embed_row])}
    evidence["scenarios"].append({"scenario": "client_routes", "window_start": START + 39 * 0.5,
                                  "window_end": START + 39 * 0.5, "routes": [
        {"method": "POST", "path": "/v1/messages", "status": 200, "ok": True, "access_lines": 1},
        {"method": "POST", "path": "/v1/embeddings", "status": 200, "ok": True, "access_lines": 0},
        {"method": "GET", "path": "/v1/models", "status": 200, "ok": True, "access_lines": 0}]})
    candidate = derive_candidate(evidence)
    assert candidate["client_routes"] == [{"method": "POST", "path_pattern": "/v1/messages"}]
    assert candidate["review_notes"]["client_routes"] == {
        "inference": ["POST /v1/messages (HTTP 200)"], "unlogged": ["GET /v1/models (HTTP 200)"],
        "unprofiled": ["POST /v1/embeddings (HTTP 200)"]}
    entry = promote_candidate(candidate, config_id="sha256:" + "0" * 64, evidence_name="e.json",
                              evidence_sha256="1" * 64, entrypoint=["node", "server.js"])
    assert entry["reachable_routes"] == ["POST /v1/chat/completions", "POST /v1/messages", "POST /api/providers/test"]
    assert entry["routes"][1]["class"] == "inference" and entry["routes"][1]["arrival"]["signal"] == "journal-access-log"


# ---- review item 5: the REAL shipped profiles over RECORDED real samples, end to end through Collector.sample ----

def _unshape(shape: dict) -> object:
    """Rebuild a JSON document from the sampler's pointer map: a list becomes `<pointer>.length` copies of its first
    element, which is all the recording keeps (the runtime reads list lengths and scalar leaves only)."""
    tree: dict = {}
    lengths = {k.removesuffix(".length"): v for k, v in shape.items() if k.endswith(".length")}
    for pointer, value in [*shape.items(), *((p, {}) for p in lengths)]:
        if pointer.startswith("#") or pointer.endswith(".length"):
            continue
        parts, cur = pointer.split("/")[1:], tree
        for part in parts[:-1]:
            cur = cur.setdefault(part, {})
        if parts and not (value == {} and parts[-1] in cur):  # an empty-object leaf never hides recorded children
            cur[parts[-1]] = value

    def build(node, prefix):
        if prefix in lengths:
            return [build(node.get("0", {}) if isinstance(node, dict) else node, f"{prefix}/0")] * lengths[prefix]
        return {k: build(v, f"{prefix}/{k}") for k, v in node.items()} if isinstance(node, dict) else node
    return build(tree, "")


def _replay(entry: dict, evidence: dict, tick: int, *, min_wait: float):
    """One Collector.sample over recorded tick `tick` (health, history and the call-log page, rows rebuilt from the
    row table), with the shipped profile loaded from the shipped profile file and the wall clock at the tick."""
    from pylib.omniroute_activity import Collector, JournalBatch, classify
    series = evidence["series"]
    logs_key = next(k for k in series if k.startswith("/api/usage/call-logs?"))
    health, history, logs = (series[k][tick] for k in ("/api/monitoring/health", "/api/usage/history", logs_key))
    rows = [{k[1:]: v for k, v in evidence["row_table"][d].items() if k.count("/") == 1} for d in logs["body"]["#rows"]]

    def fetch(path, deadline):
        if path.startswith("/api/monitoring/health"):
            return json.dumps(_unshape(health["body"])).encode()
        if path.startswith("/api/usage/history"):
            return json.dumps(_unshape(history["body"])).encode()
        return json.dumps(rows if path.endswith("offset=0") else []).encode()  # only page 0 was recorded

    class Reader:
        def poll(self, deadline, force_barrier=False):
            return JournalBatch([], "b", True, True, True, False)

    container = {"Config": {"Env": [], "Entrypoint": entry["entrypoint"]},
                 "HostConfig": {"LogConfig": {"Type": "journald"}}}
    dep = type("D", (), {"container_id": "a" * 64, "started_at": "t"})()
    c = Collector(deployment=dep, profile=load_profile(entry["config_id"], container), reader=Reader(), fetch=fetch,
                  recheck=lambda d: None, clock=lambda: 1.0, wall=lambda: health["t"], sleep=lambda _: None,
                  tolerance_ms=250,
                  min_wait=min_wait)
    s = c.sample(1e18)
    return classify(s), s


def _tick_in(evidence: dict, scenario: str, fraction: float) -> int:
    sc = next(x for x in evidence["scenarios"] if x["scenario"] == scenario)
    at = sc["window_start"] + fraction * (sc["window_end"] - sc["window_start"])
    series = evidence["series"]["/api/monitoring/health"]
    return min(range(len(series)), key=lambda i: abs(series[i]["t"] - at))


@pytest.mark.parametrize("entry", json.loads(PROFILE_PATH.read_text())["profiles"], ids=lambda e: e["config_id"][7:19])
def test_shipped_profiles_classify_recorded_real_samples_end_to_end(entry):
    evidence = json.loads((REPO / entry["evidence"]["characterization"]).read_bytes())
    # mid long_stream: a real in-flight chat request (in-memory row, activeRequests, pending) is busy
    state, s = _replay(entry, evidence, _tick_in(evidence, "long_stream", 0.3), min_wait=2)
    assert state == "busy", s
    # end of provider_test: only internal connection-test rows are recent; the client row is older than min_wait
    state, s = _replay(entry, evidence, _tick_in(evidence, "provider_test", 0.97), min_wait=2)
    assert state == "idle", s
    # the same idle tick with the default 300 s threshold: the chat traces of the run are recent, so busy
    state, s = _replay(entry, evidence, _tick_in(evidence, "provider_test", 0.97), min_wait=300)
    assert state == "busy", s


def _first_tick_with_row(evidence: dict, path: str) -> int:
    logs = next(v for k, v in evidence["series"].items() if k.startswith("/api/usage/call-logs?"))
    return next(i for i, tick in enumerate(logs) if "body" in tick and any(
        evidence["row_table"][d].get("/path") == path for d in tick["body"]["#rows"]))


@pytest.mark.parametrize("entry", json.loads(PROFILE_PATH.read_text())["profiles"], ids=lambda e: e["config_id"][7:19])
def test_recorded_extra_client_routes_are_activity_and_embeddings_defers_naming_the_route(entry):
    evidence = json.loads((REPO / entry["evidence"]["characterization"]).read_bytes())
    for path in ("/v1/messages", "/v1/responses"):  # profiled inference routes: their first row is client activity
        state, s = _replay(entry, evidence, _first_tick_with_row(evidence, path), min_wait=2)
        assert state == "busy", (path, s)
    state, s = _replay(entry, evidence, _first_tick_with_row(evidence, "/v1/embeddings"), min_wait=2)
    assert state == "unknown" and s.reason is Reason.coverage and s.detail == "unprofiled route: /v1/embeddings"


# ---- the updater's self-characterization (omniroute-update characterize), fed recorded evidence ----

RESEARCH_EVIDENCE = REPO / "docs/superpowers/research/2026-10-24-omniroute-characterization-2a7a68e89fe5.json"


# The recorded run belongs to a SHIPPED profile, so the image under test is given a synthetic config id that no
# profile covers: this is how an unprofiled image looks to the updater.
UNPROFILED_ID = "sha256:" + "7" * 64


def _without_evidence_hashes(node):
    """Profiles cite the sha of the evidence they came from; a synthetic config id changes that hash."""
    if isinstance(node, dict):
        return {k: _without_evidence_hashes(v) for k, v in node.items() if k != "evidence"}
    if isinstance(node, list):
        return [_without_evidence_hashes(v) for v in node]
    return node


def _recorded_evidence():
    evidence = json.loads(RESEARCH_EVIDENCE.read_bytes())
    evidence["config_id"] = UNPROFILED_ID
    return evidence


def _characterize(tmp_path, collect, budget=1200):
    import argparse

    from pylib.omniroute_cli import cmd_characterize
    evidence = _recorded_evidence()
    args = argparse.Namespace(config_id=evidence["config_id"], budget=budget)
    return evidence["config_id"], cmd_characterize(args, collect=collect, store_dir=tmp_path / "store")


def test_an_unprofiled_image_is_characterized_into_the_local_store_and_loads(tmp_path, capsys):
    calls = []

    def collect(config_id, log):
        calls.append(config_id)
        log("running the scenarios")
        return _recorded_evidence()
    config_id, rc = _characterize(tmp_path, collect)
    out = capsys.readouterr().out
    assert rc == 0 and calls == [config_id]
    phases = [line.split(":", 2)[1].split()[0] for line in out.splitlines()]
    assert phases == ["characterizing", "characterizing", "deriving", "deriving", "storing", "stored"], out
    hexid = config_id.removeprefix("sha256:")
    store = tmp_path / "store"
    assert oct(store.stat().st_mode & 0o777) == "0o700"
    stored = json.loads((store / f"{hexid}.profile.json").read_text())
    research = json.loads((REPO / "docs/superpowers/research/2026-10-24-omniroute-profile-2a7a68e89fe5.json").read_text())
    assert stored["evidence"]["characterization"] == f"{hexid}.evidence.json"
    assert _without_evidence_hashes({**stored, "config_id": None}) == _without_evidence_hashes({**research, "config_id": None})  # same derivation as the reviewed run
    container = {"Config": {"Env": [], "Entrypoint": stored["entrypoint"]}, "HostConfig": {"LogConfig": {"Type": "journald"}}}
    assert load_profile(config_id, container, local_dir=store).health_cache_ms == stored["health_cache_ms"]
    _, rc = _characterize(tmp_path, collect)  # profiled now: nothing runs
    assert rc == 0 and len(calls) == 1


def test_an_incomplete_characterization_defers_capability_naming_reasons_and_evidence(tmp_path, capsys):
    def collect(config_id, log):
        evidence = _recorded_evidence()
        evidence["scenarios"] = [s for s in evidence["scenarios"] if s["scenario"] != "idle_poll"]
        return evidence
    config_id, rc = _characterize(tmp_path, collect)
    captured = capsys.readouterr()
    hexid = config_id.removeprefix("sha256:")
    assert rc == 75 and captured.out.splitlines()[-1] == "deferred: capability"
    assert f"evidence: {tmp_path / 'store' / (hexid + '.evidence.json')}" in captured.err
    assert "detail: - idle_poll (no idle-regime health evidence)" in captured.err
    assert not (tmp_path / "store" / f"{hexid}.profile.json").exists()


def test_a_failed_or_overlong_characterization_stores_nothing(tmp_path, capsys):
    def broken(config_id, log):
        raise RuntimeError("precondition failed")

    def slow(config_id, log):
        import time
        time.sleep(5)
    for collect, budget, message in ((broken, 1200, "RuntimeError: precondition failed"),
                                     (slow, 1, "TimeoutError: exceeded its 1 s budget")):
        _, rc = _characterize(tmp_path, collect, budget)
        err = capsys.readouterr().err
        assert rc == 1 and message in err and "nothing was stored" in err
    assert not (tmp_path / "store").exists()


@pytest.mark.skipif(not shutil.which("podman"), reason="needs podman (an empty isolated store is created and removed)")
def test_the_sweep_reaps_only_backend_roots_whose_owner_is_gone(tmp_path):
    from pylib.omniroute_characterize import IsolatedBackend
    live = IsolatedBackend(tmp_path)                  # this process holds its lease
    dead = IsolatedBackend(tmp_path)
    (dead.root / "lease").write_text("999999999 1\n")  # a SIGKILLed owner
    unrelated = tmp_path / "characterize-run.log"     # shares the prefix, is not a backend root
    unrelated.write_text("keep")
    half = tmp_path / "characterize-half"             # no storage.conf: never handed to podman
    half.mkdir()
    IsolatedBackend.sweep(tmp_path)
    assert live.root.is_dir() and not dead.root.exists() and unrelated.read_text() == "keep" and half.is_dir()
    live.close()
    assert not live.root.exists()
