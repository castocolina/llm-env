import hashlib
import http.server
import json
import math
import sys
import threading
import time
import urllib.parse
from collections.abc import Callable
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from omniroute_fakes import CONTAINER, mk_sample, profile_entry, write_profiles

from pylib.omniroute_activity import (
    OVERFLOW,
    ROW_PAGES,
    Collector,
    JournalBatch,
    JournalctlReader,
    JournalRecord,
    Sample,
    bounded_get,
    bounded_login,
    check_freshness,
    classify,
    failed_sample,
    has_profile,
    load_profile,
    measure_clock_tolerance_ms,
    parse_journal_line,
)
from pylib.omniroute_update import Deferral, Reason, canon_image_id


class FakeReader:
    def __init__(self, *batches):
        self.batches = list(batches)
        self.terminated = False

    def poll(self, deadline, force_barrier=False):
        return self.batches.pop(0)

    def terminate(self):
        self.terminated = True


def batch(*records, ready=True, barrier=True, continuous=True, overflow=False, boot="b"):
    return JournalBatch(list(records), boot, ready, barrier, continuous, overflow)


def make_fetch(*, ts=1000, active=0, by_model=None, waiting=0, rows=(), health=None, history=None, logs=None):
    """Raw-body fake of the three admin GETs; `health`/`history`/`logs` replace a whole body (for missing-source cases).

    Call-log paging follows the source: every page holds all in-memory rows, then persisted rows by limit/offset."""
    def fetch(path, deadline):
        if path.startswith("/api/monitoring/health"):
            return json.dumps(health if health is not None else {"timestamp": ts, "chatAdmission": {
                "activeHeavy": 0, "activeHealthyHeadroom": 0, "waiting": waiting, "queuedBytes": 0}}).encode()
        if path.startswith("/api/usage/history"):
            return json.dumps(history if history is not None else {"active": active, "pending": {
                "byModel": by_model or {}, "byAccount": {"a": 1} if by_model else {}}}).encode()
        if path.startswith("/api/usage/call-logs"):
            fetch.pages.append(path)
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)
            limit, offset = int(q["limit"][0]), int(q["offset"][0])
            persisted = [r for r in rows if not r.get("memory")]
            page = [r for r in rows if r.get("memory")] + persisted[offset:offset + limit]
            return json.dumps(logs if logs is not None else {"logs": page}).encode()
        raise AssertionError(path)
    fetch.pages = []
    return fetch


def collector(tmp_path, reader, fetch, *, wall=lambda: 1.0, recheck=lambda d: None, tol=250):
    prof = load_profile("sha256:" + "c" * 64, CONTAINER, write_profiles(tmp_path, profile_entry()))
    dep = type("D", (), {"container_id": "a" * 64, "started_at": "t"})()
    return Collector(deployment=dep, profile=prof, reader=reader, fetch=fetch, recheck=recheck,
                     clock=lambda: 1.0, wall=wall, sleep=lambda _: None, tolerance_ms=tol, min_wait=300)


# ---- classify and counts ----

def test_active_counts_are_busy(tmp_path):
    s = collector(tmp_path, FakeReader(batch()), make_fetch(active=1, ts=1000), wall=lambda: 1.0).sample(deadline=time.monotonic() + 5)
    assert s.active == 1 and classify(s) == "busy"


def test_pending_counts_by_model_once(tmp_path):
    s = collector(tmp_path, FakeReader(batch()), make_fetch(by_model={"synthetic": 1})).sample(time.monotonic() + 5)
    assert s.pending == 1  # byAccount duplicate is not summed


def test_admission_waiting_is_busy(tmp_path):
    s = collector(tmp_path, FakeReader(batch()), make_fetch(waiting=1)).sample(time.monotonic() + 5)
    assert s.admission == 1 and classify(s) == "busy"


def test_uncaught_barrier_is_unknown_even_with_zero_counts(tmp_path):
    s = collector(tmp_path, FakeReader(batch(barrier=False)), make_fetch()).sample(time.monotonic() + 5)
    assert classify(s) == "unknown" and s.reason is Reason.feed


def test_unprofiled_embeddings_arrival_is_coverage_unknown(tmp_path):
    s = collector(tmp_path, FakeReader(batch(JournalRecord("POST", "/v1/embeddings"))), make_fetch()).sample(time.monotonic() + 5)
    assert classify(s) == "unknown" and s.reason is Reason.coverage
    assert s.detail == "unprofiled route: POST /v1/embeddings"  # the operator sees which route to characterize


# ---- a stale health clock under load: independent activity evidence wins (addendum c) ----

STALE = lambda: 5.0  # health timestamp 1000 ms vs wall 5000 ms: 4 s old, over 1000 + 500 + 250
CHAT = JournalRecord("POST", "/v1/chat/completions")


@pytest.mark.parametrize("reader,fetch", [
    (lambda: FakeReader(batch()), lambda: make_fetch(active=1)),
    (lambda: FakeReader(batch()), lambda: make_fetch(waiting=1)),
    (lambda: FakeReader(batch(CHAT)), lambda: make_fetch()),
    (lambda: FakeReader(batch()), lambda: make_fetch(rows=[{"path": "/v1/chat/completions", "ts": 1000, "memory": True}])),
], ids=["active-request", "admission", "journal-arrival", "in-memory-row"])
def test_stale_health_with_independent_activity_is_a_busy_sample(tmp_path, reader, fetch):
    s = collector(tmp_path, reader(), fetch(), wall=STALE).sample(time.monotonic() + 5)
    assert not s.unknown and s.reason is Reason.busy and classify(s) == "busy"


def test_stale_health_without_activity_is_transient_age_unknown(tmp_path):
    s = collector(tmp_path, FakeReader(batch()), make_fetch(), wall=STALE).sample(time.monotonic() + 5)
    assert s.unknown and s.reason is Reason.age and classify(s) == "unknown"
    assert s.detail == "health timestamp age 4000 ms outside 0..1750 ms"  # the age seen and the limit, for diagnosis
    assert Reason.age in TRANSIENT_REASONS


# ---- stale-while-revalidate health: prime, then read (addendum h) ----

class StaleWhileRevalidate:
    """`/api/monitoring/health` as measured live (2026-10-24): every response is the snapshot the previous request
    computed (its timestamp and its counters); a request finding it older than the cache window recomputes it behind
    its own answer. History and call-logs answer the current state. Time is fake and advanced by `sleep`."""

    def __init__(self, cache_ms=1000):
        self.now, self.cache_ms, self.waiting, self.active, self.health_reads = 100.0, cache_ms, 0, 0, 0
        self.on_sleep: Callable[[], None] | None = None
        self.snap = (int(self.now * 1000), 0)

    def fetch(self, path, deadline):
        if not path.startswith("/api/monitoring/health"):
            return make_fetch(active=self.active)(path, deadline)
        self.health_reads += 1
        (ts, waiting), now_ms = self.snap, int(self.now * 1000)
        if now_ms - ts >= self.cache_ms:
            self.snap = (now_ms, self.waiting)
        return json.dumps({"timestamp": ts, "chatAdmission": {
            "activeHeavy": 0, "activeHealthyHeadroom": 0, "waiting": waiting, "queuedBytes": 0}}).encode()

    def sleep(self, seconds):
        self.now += seconds
        if self.on_sleep:
            self.on_sleep()

    def collector(self, tmp_path):
        c = collector(tmp_path, FakeReader(*[batch() for _ in range(10)]), self.fetch)
        c.clock = c.wall = lambda: self.now
        c.sleep = self.sleep
        return c


def test_single_health_read_is_stale_at_idle_but_prime_then_read_is_fresh(tmp_path):
    lone = StaleWhileRevalidate()
    lone.now += 5  # the observer's 5 s poll, no other client
    ts = json.loads(lone.fetch("/api/monitoring/health", 0))["timestamp"]
    assert check_freshness(ts, int(lone.now * 1000), 1000, 500, 250) is Reason.age  # 5000 ms: our own poll interval
    swr = StaleWhileRevalidate()
    c, stamps = swr.collector(tmp_path), []
    for _ in range(6):  # 30 s of idle 5 s polling
        swr.now += 5
        s = c.sample(1e18)
        assert classify(s) == "idle" and s.health_age_ms == 200, s
        stamps.append(s.health_timestamp_ms)
    assert stamps == sorted(set(stamps)) and swr.health_reads == 12  # advancing; one extra GET per sample


def test_busy_counters_come_from_the_primed_snapshot(tmp_path):
    swr = StaleWhileRevalidate()
    swr.waiting = 1  # queued after the last snapshot: a lone read still answers waiting=0
    swr.now += 5
    s = swr.collector(tmp_path).sample(1e18)
    assert s.admission == 1 and classify(s) == "busy"


def test_a_request_starting_between_prime_and_read_is_visible(tmp_path):
    swr = StaleWhileRevalidate()
    swr.on_sleep = lambda: setattr(swr, "active", 1)
    swr.now += 5
    s = swr.collector(tmp_path).sample(1e18)
    assert s.admission == 0 and s.active == 1 and classify(s) == "busy"  # history is read after the health read


def test_a_failed_health_prime_is_a_transient_io_unknown(tmp_path):
    good = make_fetch()

    def fetch(path, deadline):
        if path.startswith("/api/monitoring/health") and not fetch.primed:
            fetch.primed = True
            raise Deferral(Reason.auth, "session rejected")
        return good(path, deadline)
    fetch.primed = False
    s = collector(tmp_path, FakeReader(batch()), fetch).sample(time.monotonic() + 5)
    assert s.unknown and s.reason is Reason.io and "prime" in s.detail


def test_structural_failure_stays_unknown_even_with_activity(tmp_path):
    s = collector(tmp_path, FakeReader(batch(CHAT, barrier=False)), make_fetch(active=1), wall=STALE).sample(
        time.monotonic() + 5)
    assert s.unknown and s.reason is Reason.feed


def test_io_failure_after_a_journal_arrival_is_busy(tmp_path):
    good = make_fetch()

    def fetch(path, deadline):
        if path.startswith("/api/usage/history"):
            raise Deferral(Reason.io, "timeout")
        return good(path, deadline)
    s = collector(tmp_path, FakeReader(batch(CHAT)), fetch).sample(time.monotonic() + 5)
    assert not s.unknown and s.reason is Reason.busy
    s = collector(tmp_path, FakeReader(batch()), fetch).sample(time.monotonic() + 5)
    assert s.unknown and s.reason is Reason.io  # no evidence: still a transient unknown


def test_busy_samples_with_a_stale_clock_never_exhaust_the_unknown_budget(tmp_path):
    g = IdleGate(300, 10800)
    c = collector(tmp_path, FakeReader(*[batch(CHAT) for _ in range(40)]), make_fetch(), wall=STALE)
    for t in range(0, 200, 5):  # 200 s of continuously stale health while requests keep arriving
        s = c.sample(time.monotonic() + 5)
        assert g.step(t, classify(s), arrival_generation=s.arrival_generation, row_generation=s.row_generation,
                      health_timestamp_ms=s.health_timestamp_ms, reason=s.reason) == "wait"
    assert g.quiet_since is None


def test_probe_arrivals_do_not_reset_quiet(tmp_path):
    c = collector(tmp_path, FakeReader(batch(), batch(JournalRecord("GET", "/api/monitoring/health"),
                                                      JournalRecord("POST", "/api/auth/login"))), make_fetch())
    a, b = c.sample(time.monotonic() + 5), c.sample(time.monotonic() + 5)
    assert b.arrival_generation == a.arrival_generation and classify(b) == "idle"


def test_chat_suffix_and_query_are_stripped_before_matching(tmp_path):
    c = collector(tmp_path, FakeReader(batch(), batch(JournalRecord("POST", "/v1/chat/completions?x=1 | model-a"))), make_fetch())
    a, b = c.sample(time.monotonic() + 5), c.sample(time.monotonic() + 5)
    assert b.arrival_generation == a.arrival_generation + 1 and classify(b) == "idle"


def test_between_poll_arrival_and_new_client_trace_bump_generations_though_both_samples_idle(tmp_path):
    c = collector(tmp_path, FakeReader(batch(), batch(JournalRecord("POST", "/v1/chat/completions"))), make_fetch(),
                  wall=NOW)
    c.fetch = make_fetch(ts=NOW_MS, rows=[client(OLD)])
    a = c.sample(time.monotonic() + 5)
    c.fetch = make_fetch(ts=NOW_MS, rows=[client(OLD - 1), client(OLD - 2)])
    b = c.sample(time.monotonic() + 5)
    assert classify(a) == classify(b) == "idle"
    assert b.arrival_generation == a.arrival_generation + 1 and b.row_generation == a.row_generation + 1


@pytest.mark.parametrize("wall_s,expected", [(1.0 + 1.750, "idle"), (1.0 + 1.751, "unknown")])
def test_freshness_boundary_1750(tmp_path, wall_s, expected):
    c = collector(tmp_path, FakeReader(batch()), make_fetch(ts=1000), wall=lambda: wall_s - 0.0)
    # timestamp_ms=1000 vs wall 1.0 s + age: age = wall_ms - 1000
    assert classify(c.sample(time.monotonic() + 5)) == expected


def test_check_freshness_formula():
    assert check_freshness(1000, 2750, 1000, 500, 250) is None
    assert check_freshness(1000, 2751, 1000, 500, 250) is Reason.age
    assert check_freshness(1000, 999, 1000, 500, 250) is Reason.age  # future timestamp


def test_clock_bracket_mismatch_is_clock_unknown(tmp_path):
    walls = iter([1.0, 9.0])  # wall jumps 8 s inside one monotonic-instant bracket
    c = collector(tmp_path, FakeReader(batch()), make_fetch(), wall=lambda: next(walls))
    assert c.sample(time.monotonic() + 5).reason is Reason.clock


# ---- call-log traces: the newest client trace against the idle threshold (min_wait 300 s, page limit 3) ----

NOW = lambda: 1000.0
NOW_MS = 1_000_000
OLD, RECENT = NOW_MS - 300_000, NOW_MS - 299_999  # exactly the threshold is old enough; 1 ms less is still recent


def client(ts, **extra):
    return {"path": "/v1/chat/completions", "ts": ts, **extra}


def probe(ts, **extra):
    return {"path": "/api/providers/test", "ts": ts, **extra}


def trace_sample(tmp_path, rows):
    fetch = make_fetch(ts=NOW_MS, rows=rows)
    return collector(tmp_path, FakeReader(batch()), fetch, wall=NOW).sample(time.monotonic() + 5), fetch.pages


@pytest.mark.parametrize("rows,state,pages", [
    ([client(OLD - i) for i in range(5000)], "idle", 1),  # a full store is nothing: one small page, oldest is old
    ([], "idle", 1),  # no traces at all: idle (the journal and health still had to agree)
    ([client(OLD)], "idle", 1),
    ([client(RECENT), client(OLD)], "busy", 1),
    ([client(OLD - 60_000, memory=True)], "busy", 1),  # in flight, however long ago it started
    ([probe(NOW_MS - 1), client(OLD)], "idle", 1),  # a probe newer than an old client trace never counts
    ([probe(NOW_MS - i) for i in range(1, 4)] + [client(OLD)], "idle", 2),  # probes only, then page to an old client
    ([probe(NOW_MS - 1, memory=True), client(OLD)], "idle", 1),  # an in-flight probe is not activity either
    ([probe(NOW_MS - i) for i in range(1, 4)] + [probe(OLD - 1)] * 3 + [client(RECENT - 5)], "idle", 2),
], ids=["full-store", "empty", "old-trace", "recent-trace", "in-flight", "probe-newer-than-old-client",
        "probes-then-old-client", "in-flight-probe", "oldest-probe-past-threshold"])
def test_newest_client_trace_decides_busy_or_idle(tmp_path, rows, state, pages):
    s, fetched = trace_sample(tmp_path, rows)
    assert classify(s) == state and not s.unknown and len(fetched) == pages
    assert fetched[0] == "/api/usage/call-logs?limit=3&offset=0"


def test_probe_only_pages_up_to_the_bound_while_still_young_are_transient_unknown(tmp_path):
    s, fetched = trace_sample(tmp_path, [probe(NOW_MS - i) for i in range(1, 100)])
    assert s.unknown and s.reason is Reason.age and s.reason in TRANSIENT_REASONS
    assert fetched == [f"/api/usage/call-logs?limit=3&offset={3 * i}" for i in range(ROW_PAGES)]


@pytest.mark.parametrize("ts,reason", [("yesterday", Reason.age), ("2026-13-40T00:00:00Z", Reason.age),
                                        (None, Reason.age), (NOW_MS + 251, Reason.clock)])
def test_unreadable_or_future_trace_time_is_transient_unknown(tmp_path, ts, reason):
    s, _ = trace_sample(tmp_path, [client(ts)])
    assert s.unknown and s.reason is reason and reason in TRANSIENT_REASONS


def test_unknown_row_path_is_coverage_unknown(tmp_path):
    s, _ = trace_sample(tmp_path, [{"path": "/v1/embeddings?x=1", "ts": OLD}])
    assert s.unknown and s.reason is Reason.coverage and s.detail == "unprofiled route: /v1/embeddings"
    s, _ = trace_sample(tmp_path, [{"path": "", "ts": OLD}])  # an in-memory row without a client endpoint
    assert s.unknown and s.reason is Reason.coverage and s.detail == "unprofiled route: (empty path)"


@pytest.mark.parametrize("row", [{"ts": OLD}, {"path": "/v1/chat/completions"}, "row"])
def test_row_without_its_profiled_fields_is_schema_unknown(tmp_path, row):
    s, _ = trace_sample(tmp_path, [row])
    assert s.unknown and s.reason is Reason.schema


def test_boot_change_or_vanished_cursor_is_not_continuous(tmp_path):
    s = collector(tmp_path, FakeReader(batch(continuous=False)), make_fetch()).sample(time.monotonic() + 5)
    assert not s.continuous and classify(s) == "unknown" and s.reason is Reason.continuity


def test_reader_overflow_is_overflow(tmp_path):
    s = collector(tmp_path, FakeReader(batch(overflow=True)), make_fetch()).sample(time.monotonic() + 5)
    assert s.overflow and s.reason is Reason.overflow


def test_deployment_drift_is_ownership_unknown(tmp_path):
    def drift(deadline):
        raise Deferral(Reason.ownership, "changed")
    s = collector(tmp_path, FakeReader(batch()), make_fetch(), recheck=drift).sample(time.monotonic() + 5)
    assert s.unknown and s.reason is Reason.ownership


@pytest.mark.parametrize("history", [
    {"active": 0},  # pending map missing
    {"active": 0, "pending": None},
    {"active": 0, "pending": {"byModel": None}},
    {"active": 0, "pending": {"byModel": []}},  # mistyped: must be an object
    {"pending": {"byModel": {}}},  # active missing
    {"active": None, "pending": {"byModel": {}}},
    {"active": True, "pending": {"byModel": {}}},  # bool is not a count
])
def test_missing_or_mistyped_history_is_schema_unknown_never_zero(tmp_path, history):
    s = collector(tmp_path, FakeReader(batch()), make_fetch(history=history)).sample(time.monotonic() + 5)
    assert classify(s) == "unknown" and s.reason is Reason.schema


@pytest.mark.parametrize("logs", [{}, {"logs": None}, {"logs": {}}, {"logs": "x"}, []])
def test_missing_or_mistyped_call_logs_is_schema_unknown(tmp_path, logs):
    s = collector(tmp_path, FakeReader(batch()), make_fetch(logs=logs)).sample(time.monotonic() + 5)
    assert classify(s) == "unknown" and s.reason is Reason.schema


@pytest.mark.parametrize("health", [{"chatAdmission": {"activeHeavy": 0, "activeHealthyHeadroom": 0, "waiting": 0, "queuedBytes": 0}},
                                    {"timestamp": 1000}])
def test_missing_health_fields_are_schema_unknown(tmp_path, health):
    s = collector(tmp_path, FakeReader(batch()), make_fetch(health=health)).sample(time.monotonic() + 5)
    assert classify(s) == "unknown" and s.reason is Reason.schema


def test_wall_clock_jump_between_samples_is_clock_unknown_even_when_each_bracket_is_fine(tmp_path):
    walls = iter([1.0, 1.0, 9.0, 9.0])  # (w0, w1) per sample: every bracket is 0 s wide, the clock jumps between samples
    c = collector(tmp_path, FakeReader(batch(), batch()), make_fetch(ts=1000), wall=lambda: next(walls))
    a = c.sample(time.monotonic() + 5)
    c.fetch = make_fetch(ts=9000)  # a fresh-looking timestamp for the jumped clock
    b = c.sample(time.monotonic() + 5)
    assert classify(a) == "idle" and classify(b) == "unknown" and b.reason is Reason.clock


def test_backward_health_timestamp_is_clock_unknown(tmp_path):
    c = collector(tmp_path, FakeReader(batch(), batch()), make_fetch(ts=2000), wall=lambda: 2.0)
    a = c.sample(time.monotonic() + 5)
    c.fetch = make_fetch(ts=1000)  # age 1000 ms is still "fresh", but time went backward
    b = c.sample(time.monotonic() + 5)
    assert classify(a) == "idle" and classify(b) == "unknown" and b.reason is Reason.clock


def test_failed_sample_is_unknown_with_the_given_reason():
    s = failed_sample("a" * 64, "t", 3, Reason.capability, 12.5)
    s.validate()
    assert classify(s) == "unknown" and s.reason is Reason.capability and s.sequence == 3 and s.sample_monotonic == 12.5


def test_sample_validate_rejects_bools_and_bad_numbers():
    for bad in ({"active": True}, {"pending": -1}, {"sample_monotonic": math.inf}, {"admission": 1.5}):
        with pytest.raises(ValueError):
            mk_sample(**bad).validate()
    mk_sample().validate()


def test_sample_json_roundtrip():
    s = mk_sample(reason=Reason.busy)
    assert Sample.from_json(s.to_json()) == s


# ---- profile loading ----

def test_missing_profile_defers_capability(tmp_path):
    with pytest.raises(Deferral) as e:
        load_profile("sha256:" + "9" * 64, CONTAINER, write_profiles(tmp_path))
    assert e.value.reason is Reason.capability and "make update-omniroute" in e.value.detail


def _local_store(tmp_path, entry=None, evidence=b"{}"):
    store = tmp_path / "store"
    store.mkdir(mode=0o700)
    hexid = "c" * 64
    (store / f"{hexid}.evidence.json").write_bytes(evidence)
    entry = entry or profile_entry(evidence={"characterization": f"{hexid}.evidence.json",
                                             "characterization_sha256": hashlib.sha256(evidence).hexdigest()})
    (store / f"{hexid}.profile.json").write_text(json.dumps(entry))
    return store, entry


def test_a_local_profile_bound_to_its_evidence_loads_after_the_shipped_list(tmp_path):
    store, entry = _local_store(tmp_path)
    assert has_profile("sha256:" + "c" * 64, write_profiles(tmp_path), store)
    assert load_profile("sha256:" + "c" * 64, CONTAINER, write_profiles(tmp_path), store).entry == entry
    shipped = profile_entry(health_cache_ms=2000)
    assert load_profile("sha256:" + "c" * 64, CONTAINER, write_profiles(tmp_path, shipped), store).entry == shipped


@pytest.mark.parametrize("damage", ["evidence", "name", "mode"])
def test_a_local_profile_that_no_longer_matches_its_evidence_or_store_defers_capability(tmp_path, damage):
    store, _ = _local_store(tmp_path)
    if damage == "evidence":
        (store / f"{'c' * 64}.evidence.json").write_bytes(b"{} ")
    elif damage == "name":
        entry = json.loads((store / f"{'c' * 64}.profile.json").read_text())
        entry["evidence"]["characterization"] = "other.json"
        (store / f"{'c' * 64}.profile.json").write_text(json.dumps(entry))
    else:
        store.chmod(0o770)
    with pytest.raises(Deferral) as e:
        load_profile("sha256:" + "c" * 64, CONTAINER, write_profiles(tmp_path), store)
    assert e.value.reason is Reason.capability and str(store) in e.value.detail
    assert not has_profile("sha256:" + "c" * 64, write_profiles(tmp_path), store)  # the updater re-characterizes it


def test_shipped_profile_list_defers_capability_for_an_uncharacterized_config():
    with pytest.raises(Deferral) as e:
        load_profile("sha256:" + "c" * 64, CONTAINER)
    assert e.value.reason is Reason.capability


def _collector_with(tmp_path, fetch, wall=lambda: 1.0, **api):
    entry = profile_entry(api_fields={**profile_entry()["api_fields"], **api})
    prof = load_profile("sha256:" + "c" * 64, CONTAINER, write_profiles(tmp_path, entry))
    dep = type("D", (), {"container_id": "a" * 64, "started_at": "t"})()
    return Collector(deployment=dep, profile=prof, reader=FakeReader(batch()), fetch=fetch, recheck=lambda d: None,
                     clock=lambda: 1.0, wall=wall, sleep=lambda _: None, tolerance_ms=250, min_wait=300)


@pytest.mark.parametrize("requests,expected,state", [([], 0, "idle"), ([{"model": "m"}, {"model": "n"}], 2, "busy")])
def test_array_in_flight_source_counts_its_length(tmp_path, requests, expected, state):
    fetch = make_fetch(history={"activeRequests": requests, "pending": {"byModel": {}}})
    s = _collector_with(tmp_path, fetch, active="/activeRequests").sample(time.monotonic() + 5)
    assert s.active == expected and classify(s) == state


def test_rfc3339_health_timestamp_is_read_as_epoch_milliseconds(tmp_path):
    health = {"timestamp": "1970-01-01T00:00:01.000Z", "chatAdmission": {
        "activeHeavy": 0, "activeHealthyHeadroom": 0, "waiting": 0, "queuedBytes": 0}}
    s = _collector_with(tmp_path, make_fetch(health=health)).sample(time.monotonic() + 5)
    assert s.health_timestamp_ms == 1000 and s.reason is Reason.ready
    health["timestamp"] = "1970-01-01T01:00:01.000+01:00"  # only the UTC `Z` form is accepted
    s = _collector_with(tmp_path, make_fetch(health=health)).sample(time.monotonic() + 5)
    assert s.unknown and s.reason is Reason.schema


@pytest.mark.parametrize("drop", ["arrival", "inflight", "queue"])
def test_inference_route_requires_all_three_signals(tmp_path, drop):
    routes = json.loads(json.dumps(__import__("omniroute_fakes").ROUTES))
    del routes[0][drop]
    with pytest.raises(Deferral) as e:
        load_profile("sha256:" + "c" * 64, CONTAINER, write_profiles(tmp_path, profile_entry(routes=routes)))
    assert e.value.reason is Reason.coverage


def test_inference_route_unreachable_needs_evidence(tmp_path):
    routes = json.loads(json.dumps(__import__("omniroute_fakes").ROUTES))
    routes[0] = {"method": "POST", "path_pattern": "/v1/chat/completions", "class": "inference", "unreachable": {}}
    with pytest.raises(Deferral) as e:
        load_profile("sha256:" + "c" * 64, CONTAINER, write_profiles(tmp_path, profile_entry(routes=routes)))
    assert e.value.reason is Reason.coverage


def test_reachable_route_without_entry_defers_coverage(tmp_path):
    entry = profile_entry(reachable_routes=["POST /v1/chat/completions", "POST /v1/embeddings"])
    with pytest.raises(Deferral) as e:
        load_profile("sha256:" + "c" * 64, CONTAINER, write_profiles(tmp_path, entry))
    assert e.value.reason is Reason.coverage


@pytest.mark.parametrize("inspect_patch,reason", [
    ({"Config": {"Env": ["NODE_ENV=production", "LOG_LEVEL=warn"], "Entrypoint": ["node", "server.js"]}}, Reason.logging),
    ({"Config": {"Env": ["NODE_ENV=development"], "Entrypoint": ["node", "server.js"]}}, Reason.logging),
    ({"Config": {"Env": ["NODE_ENV=production"], "Entrypoint": ["sh"]}}, Reason.logging),
    ({"HostConfig": {"LogConfig": {"Type": "k8s-file"}}}, Reason.feed),
])
def test_effective_container_must_match_profile(tmp_path, inspect_patch, reason):
    with pytest.raises(Deferral) as e:
        load_profile("sha256:" + "c" * 64, {**CONTAINER, **inspect_patch}, write_profiles(tmp_path, profile_entry()))
    assert e.value.reason is reason


@pytest.mark.parametrize("missing", ["health_refresh_bound_ms", "health_cache_ms", "evidence", "api_fields"])
def test_profile_missing_required_field_defers_capability(tmp_path, missing):
    entry = profile_entry()
    del entry[missing]
    with pytest.raises(Deferral) as e:
        load_profile("sha256:" + "c" * 64, CONTAINER, write_profiles(tmp_path, entry))
    assert e.value.reason is Reason.capability


def test_profile_selected_from_podmans_unprefixed_image_id(tmp_path):
    p = write_profiles(tmp_path, profile_entry())
    assert load_profile(canon_image_id("c" * 64), CONTAINER, p).sha256  # Podman reports the bare hex


def test_non_canonical_config_id_defers_capability_instead_of_silently_missing(tmp_path):
    with pytest.raises(Deferral) as e:
        load_profile("c" * 64, CONTAINER, write_profiles(tmp_path, profile_entry()))
    assert e.value.reason is Reason.capability


def test_valid_profile_loads_with_stable_sha(tmp_path):
    p = write_profiles(tmp_path, profile_entry())
    a, b = load_profile("sha256:" + "c" * 64, CONTAINER, p), load_profile("sha256:" + "c" * 64, CONTAINER, p)
    assert a.sha256 == b.sha256 and len(a.sha256) == 64


# ---- bounded HTTP ----

class Trickle(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Length", "100000")
        self.end_headers()
        try:
            for _ in range(100000):
                self.wfile.write(b"x")
                self.wfile.flush()
                time.sleep(0.2)
        except OSError:
            pass
    do_POST = do_GET

    def log_message(self, *a):
        pass


class Big(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Length", str(9 * 1024 * 1024))
        self.end_headers()
        self.wfile.write(b"x" * (9 * 1024 * 1024))

    def log_message(self, *a):
        pass


def serve(handler):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def test_trickling_get_stops_at_aggregate_deadline():
    srv, url = serve(Trickle)
    start = time.monotonic()
    with pytest.raises(Deferral) as e:
        bounded_get(url, "/x", "tok", start + 1.0)
    assert e.value.reason is Reason.io and time.monotonic() - start < 2.5
    srv.shutdown()


def test_trickling_login_stops_at_aggregate_deadline():
    srv, url = serve(Trickle)
    start = time.monotonic()
    with pytest.raises(Deferral) as e:
        bounded_login(url, "pw", start + 1.0)
    assert e.value.reason is Reason.io and time.monotonic() - start < 2.5
    srv.shutdown()


def test_oversize_body_is_rejected():
    srv, url = serve(Big)
    with pytest.raises(Deferral) as e:
        bounded_get(url, "/x", "tok", time.monotonic() + 10)
    assert e.value.reason is Reason.io
    srv.shutdown()


def test_existing_omniroute_helpers_keep_their_signatures():
    import inspect

    import pylib.omniroute as o
    assert list(inspect.signature(o._login).parameters) == ["base_url", "password"]
    assert list(inspect.signature(o._request).parameters) == ["method", "url", "session_token", "payload"]


# ---- journal parsing and reader ----

def test_parse_journal_line_extracts_method_and_path_and_flags_oversize(tmp_path):
    prof = load_profile("sha256:" + "c" * 64, CONTAINER, write_profiles(tmp_path, profile_entry()))
    line = json.dumps({"MESSAGE": json.dumps({"method": "POST", "path": "/v1/chat/completions"})}).encode()
    assert parse_journal_line(line, prof) == JournalRecord("POST", "/v1/chat/completions")
    assert parse_journal_line(b"x" * 70000, prof) is OVERFLOW
    assert parse_journal_line(json.dumps({"MESSAGE": "not json"}).encode(), prof) is None


def _journal_stub(tmp_path, seed_doc, follow_doc):
    seed = tmp_path / "seed.json"
    follow = tmp_path / "follow.json"
    seed.write_text(seed_doc or "")
    follow.write_text(follow_doc or "")
    scripts = tmp_path / "stubs"
    scripts.mkdir(exist_ok=True)
    body = ("#!/usr/bin/env bash\n"
            f'SEED="{seed}"\nFOLLOW="{follow}"\n'
            'if [[ "$*" == *"--follow"* ]]; then\n'
            "  cat \"$FOLLOW\"\n"
            "  exec sleep 300\n"
            "else\n"
            "  cat \"$SEED\"\n"
            "fi\n")
    p = scripts / "journalctl"
    p.write_text(body)
    p.chmod(0o755)
    return str(scripts)


REC = json.dumps({"MESSAGE": json.dumps({"method": "POST", "path": "/v1/chat/completions"})})


def make_reader(tmp_path, seed_doc, follow_doc):
    path = _journal_stub(tmp_path, seed_doc, follow_doc)
    env = {"PATH": f"{path}:/usr/bin:/bin", "HOME": "/h"}
    prof_path = write_profiles(tmp_path, profile_entry())
    prof = load_profile("sha256:" + "c" * 64, CONTAINER, prof_path)
    reader = JournalctlReader("a" * 64, argv0="journalctl", env=env)
    return reader, prof


def test_journalctl_reader_seed_then_follow_yields_record_and_barrier(tmp_path):
    reader, _prof = make_reader(
        tmp_path,
        json.dumps({"__CURSOR": "s1", "_BOOT_ID": "b1"}) + "\n",
        json.dumps({"__CURSOR": "s1", "_BOOT_ID": "b1", "MESSAGE": json.dumps({"method": "POST", "path": "/v1/chat/completions"})}) + "\n")
    b = reader.poll(time.monotonic() + 5)
    assert b.ready and b.barrier_seen and b.continuous
    assert b.records == [JournalRecord("POST", "/v1/chat/completions")]
    reader.terminate()


def test_journalctl_reader_boot_change_is_not_continuous(tmp_path):
    reader, _prof = make_reader(
        tmp_path,
        json.dumps({"__CURSOR": "s1", "_BOOT_ID": "b1"}) + "\n",
        json.dumps({"__CURSOR": "s1", "_BOOT_ID": "b2", "MESSAGE": json.dumps({"method": "POST", "path": "/v1/chat/completions"})}) + "\n")
    b = reader.poll(time.monotonic() + 5)
    assert b.ready and not b.continuous
    reader.terminate()


def test_journalctl_reader_empty_seed_is_not_ready(tmp_path):
    reader, _prof = make_reader(tmp_path, "", "")
    b = reader.poll(time.monotonic() + 1)
    assert not b.ready
    reader.terminate()


def test_journalctl_reader_terminate_kills_the_child(tmp_path):
    reader, _prof = make_reader(
        tmp_path,
        json.dumps({"__CURSOR": "s1", "_BOOT_ID": "b1"}) + "\n",
        json.dumps({"__CURSOR": "s1", "_BOOT_ID": "b1", "MESSAGE": json.dumps({"method": "POST", "path": "/v1/chat/completions"})}) + "\n")
    reader.poll(time.monotonic() + 5)
    assert reader.follower is not None
    child = reader.follower.pid
    reader.terminate()
    end = time.monotonic() + 2
    while time.monotonic() < end and Path(f"/proc/{child}").exists():
        time.sleep(0.05)
    assert not Path(f"/proc/{child}").exists()


def test_journalctl_reader_unstartable_child_is_ready_false_not_an_exception():
    recorded = {}

    def fake_popen(argv, **kw):  # matches the duck-typed popen the reader accepts
        recorded["env"] = kw.get("env")
        raise OSError("no journalctl")

    reader = JournalctlReader("a" * 64, popen=fake_popen, env={"PATH": "/bin"})
    b = reader.poll(time.monotonic() + 1)
    assert not b.ready and recorded["env"] == {"PATH": "/bin"}


def test_measure_clock_tolerance_ms_is_ceiling_plus_resolution():
    wall = iter([0.0, 0.0013, 0.0, 0.0013])
    clock = iter([0.0, 0.0, 0.0, 0.001])
    assert measure_clock_tolerance_ms(lambda: next(wall), lambda: next(clock), samples=2) == 3


# ---- Task 4: idle gate, overrides, deadline ----

import argparse

from pylib.omniroute_activity import (
    TRANSIENT_REASONS,
    UNKNOWN_BUDGET_S,
    IdleGate,
    validate_overrides,
)
from pylib.omniroute_cli import cmd_deadline, cmd_overrides
from pylib.omniroute_update import UpdateError


def feed(g, now, st, a=0, r=0, h=1):
    return g.step(now, st, arrival_generation=a, row_generation=r, health_timestamp_ms=h)


def test_quiet_starts_only_after_health_timestamp_advance():
    g = IdleGate(300, 10800)  # the wait started at monotonic 0
    assert feed(g, 0, "idle", h=1000) == "wait"  # first sample = baseline, not an advance
    assert feed(g, 400, "idle", h=1000) == "wait"  # reused cache; quiet has NOT started
    assert feed(g, 405, "idle", h=2000) == "wait"  # advance observed; quiet starts here
    assert feed(g, 704, "idle", h=3000) == "wait"
    assert feed(g, 705, "idle", h=4000) == "resample"
    assert feed(g, 706, "idle", h=5000) == "ready"  # confirmation sample, generations unchanged


def test_arrival_between_two_zero_count_samples_resets_quiet():
    g = IdleGate(300, 10800)
    feed(g, 0, "idle", h=1); feed(g, 5, "idle", h=2)  # quiet starts at 5
    assert feed(g, 200, "idle", a=1, h=3) == "wait"  # arrival_generation changed with zero counts: quiet cleared
    assert feed(g, 504, "idle", a=1, h=4) == "wait"  # next nonbusy sample starts the new window at 504
    assert feed(g, 505, "idle", a=1, h=5) == "wait"  # one second of quiet, not 300
    assert feed(g, 803, "idle", a=1, h=6) == "wait"  # 299 s
    assert feed(g, 804, "idle", a=1, h=7) == "resample"  # 300 s: complete resample before commit
    assert feed(g, 805, "idle", a=1, h=8) == "ready"


def test_row_generation_change_resets_quiet():
    g = IdleGate(300, 10800)
    feed(g, 0, "idle", h=1); feed(g, 5, "idle", h=2)
    assert feed(g, 200, "idle", r=1, h=3) == "wait"
    assert feed(g, 504, "idle", r=1, h=4) == "wait"
    assert feed(g, 505, "idle", r=1, h=5) == "wait"
    assert feed(g, 804, "idle", r=1, h=6) == "resample"


def test_activity_at_299_resets_but_deadline_not_extended():
    g = IdleGate(300, 10800)
    feed(g, 0, "idle", h=1); feed(g, 1, "idle", h=2)  # quiet starts at 1
    assert feed(g, 299.5, "busy", h=3) == "wait"
    assert g.quiet_since is None and g.deadline == 10800
    assert feed(g, 300, "idle", h=4) == "wait"  # new window starts at 300
    assert feed(g, 599, "idle", h=5) == "wait"
    assert feed(g, 600, "idle", h=6) == "resample"
    assert feed(g, 10800, "idle", h=7) == "defer-deadline"  # deadline unchanged by the activity


def test_resample_that_finds_activity_resumes_waiting():
    g = IdleGate(300, 10800)
    feed(g, 0, "idle", h=1); feed(g, 1, "idle", h=2)
    assert feed(g, 301, "idle", h=3) == "resample"
    assert feed(g, 302, "busy", h=4) == "wait"
    assert g.quiet_since is None and g.resampling is False
    assert feed(g, 303, "idle", h=5) == "wait"  # new window starts at 303
    assert feed(g, 603, "idle", h=6) == "resample"


def test_deadline_exclusive_and_wins_over_ready():
    g = IdleGate(300, 1000); feed(g, 0, "idle", h=1)
    assert feed(g, 999.9, "idle", h=2) == "wait"
    assert feed(g, 1000, "idle", h=9) == "defer-deadline"


def test_unknown_without_a_reason_defers_immediately():
    g = IdleGate(300, 10800)
    assert feed(g, 5, "unknown") == "defer-unknown"


@pytest.mark.parametrize("reason", sorted(set(Reason) - TRANSIENT_REASONS - {Reason.ready, Reason.busy}))
def test_structural_unknown_defers_on_its_first_sample(reason):
    g = IdleGate(300, 10800)
    feed(g, 0, "idle", h=1); feed(g, 5, "idle", h=2)  # quiet window open
    assert g.step(10, "unknown", arrival_generation=0, row_generation=0, health_timestamp_ms=None,
                  reason=reason) == "defer-unknown"


def unknown(g, now, reason=Reason.age):
    return g.step(now, "unknown", arrival_generation=0, row_generation=0, health_timestamp_ms=None, reason=reason)


@pytest.mark.parametrize("reason", sorted(TRANSIENT_REASONS))
def test_single_transient_unknown_restarts_a_full_window(reason):
    g = IdleGate(300, 10800)
    feed(g, 0, "idle", h=1); feed(g, 5, "idle", h=2)  # quiet starts at 5
    assert unknown(g, 290, reason) == "wait"
    assert g.quiet_since is None
    assert feed(g, 305, "idle", h=3) == "wait"  # would have been 300 s of quiet; the unknown cleared it
    assert feed(g, 306, "idle", h=4) == "wait"  # the new window starts at 305, not on the unknown sample
    assert feed(g, 604, "idle", h=5) == "wait"
    assert feed(g, 605, "idle", h=6) == "resample"
    assert feed(g, 606, "idle", h=7) == "ready"


def test_continuous_transient_unknown_defers_after_the_budget():
    g = IdleGate(300, 10800)
    feed(g, 0, "idle", h=1)
    for t in range(10, 10 + UNKNOWN_BUDGET_S + 1, 5):  # 10 .. 70: exactly the budget, still waiting
        assert unknown(g, t, Reason.io if t % 2 else Reason.age) == "wait"
    assert unknown(g, 10 + UNKNOWN_BUDGET_S + 0.5, Reason.age) == "defer-unknown"


def test_a_complete_sample_resets_the_unknown_budget():
    g = IdleGate(300, 10800)
    feed(g, 0, "idle", h=1)
    assert unknown(g, 10) == "wait"
    assert unknown(g, 65) == "wait"
    assert feed(g, 66, "busy", h=2) == "wait"
    assert unknown(g, 100) == "wait" and unknown(g, 160) == "wait"  # a new 60 s budget from 100
    assert unknown(g, 161) == "defer-unknown"


def test_a_structural_unknown_inside_a_transient_run_still_defers_at_once():
    g = IdleGate(300, 10800)
    assert unknown(g, 1) == "wait"
    assert unknown(g, 2, Reason.capability) == "defer-unknown"


def test_transient_unknown_during_the_resample_resets_the_window():
    g = IdleGate(300, 10800)
    feed(g, 0, "idle", h=1); feed(g, 1, "idle", h=2)
    assert feed(g, 301, "idle", h=3) == "resample"
    assert unknown(g, 302) == "wait"
    assert g.resampling is False and g.quiet_since is None
    assert feed(g, 303, "idle", h=4) == "wait"  # new window starts at 303
    assert feed(g, 602, "idle", h=5) == "wait"
    assert feed(g, 603, "idle", h=6) == "resample"
    assert feed(g, 604, "idle", h=7) == "ready"


def test_deadline_still_wins_during_transient_unknowns():
    g = IdleGate(300, 100)
    assert unknown(g, 90) == "wait"
    assert unknown(g, 100) == "defer-deadline"


@pytest.mark.parametrize("idle,mx", [("", None), (None, ""), ("0", None), ("abc", None),
                                     ("301", "300"), ("2147483648", None), ("-1", None), (" 5", None),
                                     ("5 ", None), ("\u0663", None)])
def test_invalid_overrides_rejected(idle, mx):
    with pytest.raises(UpdateError):
        validate_overrides(idle, mx)


def test_valid_defaults_and_boundaries():
    assert validate_overrides(None, None) == (300, 10800)
    assert validate_overrides("60", "600") == (60, 600)  # the spec acceptance values
    assert validate_overrides("2147483647", "2147483647") == (2147483647, 2147483647)


def test_overrides_cli_prints_nothing_on_stdout_when_invalid(capsys):
    assert cmd_overrides(argparse.Namespace(idle="", max_wait=None)) == 1
    assert capsys.readouterr().out == ""


def test_overrides_cli_prints_exact_lines(capsys):
    assert cmd_overrides(argparse.Namespace(idle="60", max_wait="600")) == 0
    assert capsys.readouterr().out == "min_wait=60\nmax_deferred_wait=600\n"


def test_deadline_file_init_and_exclusive_check(tmp_path, capsys):
    f = str(tmp_path / "deadline")
    assert cmd_deadline(argparse.Namespace(file=f, init=10), clock=lambda: 100.0) == 0
    assert (tmp_path / "deadline").stat().st_mode & 0o777 == 0o600
    capsys.readouterr()
    assert cmd_deadline(argparse.Namespace(file=f, init=None), clock=lambda: 109.5) == 0
    assert capsys.readouterr().out == "remaining=1\n"
    assert cmd_deadline(argparse.Namespace(file=f, init=None), clock=lambda: 110.0) == 75  # now >= deadline
    assert capsys.readouterr().out == "deferred: deadline\n"


def test_deadline_unreadable_file_is_an_error(tmp_path, capsys):
    assert cmd_deadline(argparse.Namespace(file=str(tmp_path / "missing"), init=None)) == 1
    assert capsys.readouterr().out == ""
