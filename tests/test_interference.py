import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pylib.interference import append_sample, evaluate, percentile, snapshot

ZERO = {"high": 0, "max": 0, "oom": 0, "oom_kill": 0}
LIMITS = {
    "omniroute": {"memory_max_mib": 2048},
    "remote_setup": {"memory_max_mib": 256},
    "system_one": {"memory_max_mib": 5120},
    "phoenix": {"memory_max_mib": 1024},
    "llm_server": {"memory_mib": 12000},
}


def running(uptime=5000, **events):
    return {"state": "running", "uptime_s": uptime, "events": {**ZERO, **events}}


def snap(host=None, gpu=None, **services):
    base = {"omniroute": running(), "system_one": running(), "llm_server": running()}
    return {
        "services": {**base, **services},
        "host": host or {"mem_available_mib": 20000, "swap_used_mib": 100},
        "gpu": gpu or {"vram_used_mib": 9000, "vram_total_mib": 16304},
    }


def make_run(tmp_path, *, before=None, after=None, samples=None, laya=(0.012, 0.014), load=None, omni=True):
    d = tmp_path / "run"
    d.mkdir()
    if laya:
        (d / "laya-before.txt").write_text(f"200 {laya[0]}\n" * 5)
        (d / "laya-during.txt").write_text(f"200 {laya[1]}\n" * 5)
    if omni:
        (d / "omniroute-before.txt").write_text("200 0.003\n")
        (d / "omniroute-during.txt").write_text("200 0.004\n")
    (d / "live-before.json").write_text(json.dumps(before or snap(), indent=2))
    (d / "live-after.json").write_text(json.dumps(after or snap(), indent=2))
    for s in [snap()] if samples is None else samples:
        append_sample(d / "samples.jsonl", s)
    rows = load if load is not None else [
        {"status": 200, "seconds": 3.0, "prompt_tokens": 900, "completion_tokens": 50},
        {"status": 200, "seconds": 4.0, "prompt_tokens": 1000, "completion_tokens": 60},
    ]
    (d / "load.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    return d


def fake_cgroup(tmp_path):
    cg = tmp_path / "cg"
    cg.mkdir()
    mib = 1024 * 1024
    (cg / "memory.current").write_text(str(8000 * mib))
    (cg / "memory.peak").write_text(str(9000 * mib))
    (cg / "memory.max").write_text("12582912000")
    (cg / "memory.events").write_text("high 0\nmax 0\noom 0\noom_kill 0\n")
    (cg / "cpu.stat").write_text("usage_usec 1000000\n")
    return cg


def llm_only_locate(cg, now):
    return lambda c: (cg, 4242, now - 600.0) if c == "llm-server" else None


def test_percentile_uses_the_system_one_benchmark_rank():
    assert percentile(range(1, 21), 0.5) == 11
    assert percentile(range(1, 21), 0.95) == 20
    assert percentile([], 0.5) is None


def test_evaluate_passes_when_latency_and_events_hold(tmp_path):
    report = evaluate(make_run(tmp_path))
    assert report["passed"] is True
    assert report["failures"] == []
    assert report["notes"] == []
    assert report["load"] == {"requests": 2, "ok": 2, "prompt_tokens_max": 1000, "completion_tokens_total": 110}


def test_evaluate_fails_on_system_one_p95_regression_beyond_both_thresholds(tmp_path):
    report = evaluate(make_run(tmp_path, laya=(0.012, 0.040)))
    assert report["passed"] is False
    assert any("system_one p95" in f for f in report["failures"])
    (tmp_path / "ok").mkdir()
    assert evaluate(make_run(tmp_path / "ok", laya=(0.012, 0.020)))["passed"] is True


def test_evaluate_fails_on_any_memory_events_increase(tmp_path):
    after = snap(system_one=running(max=1), llm_server=running(oom_kill=1))
    report = evaluate(make_run(tmp_path, after=after))
    assert report["passed"] is False
    assert report["events_changed"] == [
        {"service": "system_one", "counter": "max", "before": 0, "after": 1},
        {"service": "llm_server", "counter": "oom_kill", "before": 0, "after": 1},
    ]


def test_evaluate_fails_when_a_container_restarted_between_snapshots(tmp_path):
    report = evaluate(make_run(tmp_path, before=snap(omniroute=running(5000)), after=snap(omniroute=running(30))))
    assert report["counters_reset"] == ["omniroute"]
    assert report["passed"] is False


def test_evaluate_fails_when_a_service_stops_during_the_window(tmp_path):
    before = snap(phoenix={"state": "not running"})
    after = snap(system_one={"state": "not running"}, phoenix={"state": "not running"})
    del after["services"]["llm_server"]
    report = evaluate(make_run(tmp_path, before=before, after=after))
    assert report["services_stopped"] == ["system_one", "llm_server"]
    assert any("system_one stopped during the window" in f for f in report["failures"])
    assert report["passed"] is False


def test_append_sample_writes_one_compact_line_that_evaluate_reads(tmp_path):
    cg, now = fake_cgroup(tmp_path), 1_000_000.0
    empty_drm = tmp_path / "drm"
    empty_drm.mkdir()
    run_dir = make_run(tmp_path, samples=[])
    snaps = []
    for mib in (20000, 12000):
        meminfo = tmp_path / "meminfo"
        meminfo.write_text(f"MemAvailable:    {mib * 1024} kB\n")
        snaps.append(snapshot(LIMITS, None, locate=llm_only_locate(cg, now), now=now, meminfo=meminfo, drm_root=empty_drm))
        append_sample(run_dir / "samples.jsonl", snaps[-1])
    lines = (run_dir / "samples.jsonl").read_text().splitlines()
    assert len(lines) == 2
    assert [json.loads(line) for line in lines] == snaps
    assert evaluate(run_dir)["host"]["mem_available_min_mib"] == 12000


def test_evaluate_fails_on_system_one_errors_during_load(tmp_path):
    run_dir = make_run(tmp_path)
    (run_dir / "laya-during.txt").write_text("503 0.001\n")
    assert evaluate(run_dir)["passed"] is False


def test_evaluate_fails_without_a_completed_load_request(tmp_path):
    report = evaluate(make_run(tmp_path, load=[{"status": "000", "seconds": 1.0}]))
    assert report["passed"] is False
    assert any("no load request completed within the window" in f for f in report["failures"])


def test_evaluate_without_system_one_gates_on_events_only(tmp_path):
    report = evaluate(make_run(tmp_path, laya=None))
    assert report["system_one"] is None
    assert report["passed"] is True
    assert report["notes"] == ["system_one latency not measured (System One disabled)"]


def test_evaluate_reports_vram_headroom_and_host_memory(tmp_path):
    samples = [
        snap(host={"mem_available_mib": 20000, "swap_used_mib": 100}, gpu={"vram_used_mib": 9000, "vram_total_mib": 16304}),
        snap(host={"mem_available_mib": 12000, "swap_used_mib": 600}, gpu={"vram_used_mib": 14000, "vram_total_mib": 16304}),
    ]
    report = evaluate(make_run(tmp_path, samples=samples))
    assert report["vram"] == {"total_mib": 16304, "peak_used_mib": 14000, "headroom_min_mib": 2304}
    assert report["host"]["mem_available_min_mib"] == 12000
    assert report["host"]["swap_delta_mib"] == 500


def test_snapshot_adds_llm_server_beside_the_services(tmp_path):
    cg, now = fake_cgroup(tmp_path), 1_000_000.0
    meminfo, drm = tmp_path / "meminfo", tmp_path / "drm"
    meminfo.write_text("")
    drm.mkdir()
    out = snapshot(LIMITS, None, locate=llm_only_locate(cg, now), now=now, meminfo=meminfo, drm_root=drm)
    assert out["services"]["llm_server"]["events"]["oom_kill"] == 0
    assert out["services"]["llm_server"]["cap_mib"] == 12000
    assert out["services"]["omniroute"] == {"state": "not running"}
    assert out["gpu"] == {"vram_used_mib": None, "vram_total_mib": None}
    gone = snapshot(LIMITS, None, locate=lambda c: None, now=now, meminfo=meminfo, drm_root=drm)
    assert gone["services"]["llm_server"] == {"state": "not running"}
