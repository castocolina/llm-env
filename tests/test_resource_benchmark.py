import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pylib import resource_benchmark as rb

MIB = rb.MIB


def _samples(rows):
    """rows: (phase, current_mib, swap_mib, peak_mib, cpu_seconds_total), one per second."""
    return [
        {"label": "svc", "phase": p, "t": float(i), "current": c * MIB, "swap": s * MIB, "peak": pk * MIB,
         "cpu_usec": int(cpu * 1e6)}
        for i, (p, c, s, pk, cpu) in enumerate(rows)
    ]


def test_summarize_sizes_floor_from_modal_resident_and_ceiling_from_peak():
    rows = [("idle", 100, 0, 100, 0)] * 3 + [("load", 400, 50, 700, 0)] * 6 + [("cooldown", 500, 0, 700, 0)] * 3
    out = rb.summarize(_samples(rows), "svc")
    assert out["resident_mode_mib"] == 416  # 400 MiB rounded up to the 32 MiB step
    assert out["retained_after_load_mib"] == 500
    assert out["peak_mib"] == 700
    assert out["recommend"]["memory_min_mib"] == 608  # 500 retained * 1.2 = 600 -> 608
    assert out["recommend"]["memory_max_mib"] == 864  # 700 * 1.2 = 840 -> 864
    assert out["swap_median_mib"] == 25


def test_summarize_cpu_uses_windowed_peak_and_quarter_core_steps():
    rows = [("run", 100, 0, 100, i * 0.5) for i in range(12)]
    out = rb.summarize(_samples(rows), "svc")
    assert out["cpu_peak_cores"] == 0.5
    assert out["recommend"]["cpus_min"] == 0.75 and out["recommend"]["cpus_max"] == 0.75


def test_summarize_ceiling_never_below_floor_and_needs_samples():
    rows = [("cooldown", 300, 0, 100, 0)] * 4
    out = rb.summarize(_samples(rows), "svc")
    assert out["recommend"]["memory_max_mib"] >= out["recommend"]["memory_min_mib"]
    try:
        rb.summarize(_samples(rows[:1]), "svc")
    except ValueError as exc:
        assert "at least 2 samples" in str(exc)
    else:
        raise AssertionError("one sample must be rejected")


def test_report_recomputes_from_saved_samples(tmp_path, capsys):
    path = tmp_path / "r.json"
    path.write_text(json.dumps({"samples": _samples([("run", 100, 0, 100, 0)] * 4)}))
    out = rb.report(path, 0.5)
    assert out["svc"]["memory_min_mib"] == 192  # mode bucket 128 MiB * 1.5


def test_saturated_phases_count_for_the_peak_but_not_the_reservation():
    rows = ([("first", 300, 0, 300, 0)] * 6 + [("ramp-40rps", 900, 0, 950, 0)] * 8
            + [("cooldown", 310, 0, 950, 0)] * 3)
    requests = {"first": {"200": 30}, "ramp-40rps": {"200": 50, "0": 1100}, "cooldown": {}}
    out = rb.summarize(_samples(rows), "svc", requests=requests)
    assert {k: out["phases"]["first"][k] for k in ("ok", "errors", "saturated")} == {"ok": 30, "errors": 0, "saturated": False}
    assert out["phases"]["ramp-40rps"]["ok"] == 50 and out["phases"]["ramp-40rps"]["errors"] == 1100
    assert out["phases"]["ramp-40rps"]["saturated"] is True
    assert out["resident_mode_mib"] == 320  # without the saturated rows; with them it would be 928
    assert out["peak_mib"] == 950  # the saturated phase still sets the ceiling
    assert out["recommend"]["memory_max_mib"] == 1152  # 950 * 1.2 = 1140 -> 1152
    assert "SATURATED" in rb.render_summary(out)


def test_ninety_percent_ok_is_not_saturated():
    assert rb.phase_requests({"p": {"200": 9, "503": 1}})["p"]["saturated"] is False
    assert rb.phase_requests({"p": {"200": 8, "503": 2}})["p"]["saturated"] is True


def test_attach_ignores_a_lifetime_peak_that_did_not_rise_during_the_run():
    rows = [("run", 400, 0, 4608, 0)] * 4  # memory.peak from before the run (clipped at a cap)
    out = rb.summarize(_samples(rows), "svc", lifetime_peak=False)
    assert out["peak_mib"] == 400 and out["peak_source"] == "memory.current"
    rising = [("run", 400, 0, 500, 0), ("run", 400, 0, 700, 0), ("run", 400, 0, 700, 0)]
    out = rb.summarize(_samples(rising), "svc", lifetime_peak=False)
    assert out["peak_mib"] == 700 and out["peak_source"] == "memory.peak" and out["sampled_peak_mib"] == 400
    # a container the run created: its whole lifetime is the run
    assert rb.summarize(_samples(rows), "svc")["peak_mib"] == 4608


def test_swap_max_and_swap_in_rate_are_reported():
    samples = _samples([("run", 100, s, 100, 0) for s in (10, 50, 30, 20, 20)])
    for i, sample in enumerate(samples):
        sample["pswpin"] = 1000 + i * 256  # 256 pages/s = 1 MiB/s at 4 KiB pages
    out = rb.summarize(samples, "svc")
    assert out["swap_median_mib"] == 20 and out["swap_max_mib"] == 50
    assert out["swap_in_mib"] == round(4 * 256 * rb.PAGE / MIB)
    assert out["swap_in_mib_per_s"] == round(256 * rb.PAGE / MIB, 2)
    assert rb.summarize(_samples([("run", 100, 0, 100, 0)] * 3), "svc")["swap_in_mib"] is None  # old reports


def test_summary_prints_the_headroom_that_was_used():
    rows = _samples([("run", 100, 0, 100, 0)] * 4)
    assert "(+50% headroom)" in rb.render_summary(rb.summarize(rows, "svc", headroom=0.5))
    assert "(+20% headroom)" in rb.render_summary(rb.summarize(rows, "svc"))


def test_vram_samples_are_summarized_with_a_reservation():
    samples = _samples([("run", 100, 0, 100, 0)] * 4)
    for sample, (proc, dev) in zip(samples, [(712, 900), (4236, 4478), (4236, 4478), (None, 4400)]):
        sample.update(vram_mib=proc, device_vram_mib=dev)
    out = rb.summarize(samples, "svc")
    assert out["vram"] == {"process_p50_mib": 4236, "process_peak_mib": 4236, "device_peak_mib": 4478}
    assert out["recommend"]["vram_reserve_mib"] == 5088  # 4236 * 1.2 = 5083 -> 5088
    assert "vram process" in rb.render_summary(out)


def test_read_point_samples_vram_and_swap_in(tmp_path, monkeypatch):
    from pylib import detect

    (tmp_path / "cpu.stat").write_text("usage_usec 5\n")
    for name, value in (("memory.current", MIB), ("memory.swap.current", 0), ("memory.peak", 2 * MIB)):
        (tmp_path / name).write_text(f"{value}\n")
    (tmp_path / "memory.stat").write_text("anon 1\npswpin 42\n")
    used = tmp_path / "mem_info_vram_used"
    used.write_text(f"{300 * MIB}\n")
    monkeypatch.setattr(detect, "process_vram_mib", lambda pid, pci: 77 if (pid, pci) == (9, "0000:03:00.0") else None)
    point = rb.read_point(tmp_path, (9, "0000:03:00.0", used))
    assert point["pswpin"] == 42 and point["vram_mib"] == 77 and point["device_vram_mib"] == 300
    assert "vram_mib" not in rb.read_point(tmp_path)


def test_gpu_device_maps_pci_or_render_node_to_sysfs(tmp_path, monkeypatch):
    from pylib import detect

    gpus = [{"card": "card1", "pci_address": "0000:03:00.0", "render_node": "renderD128"}]
    monkeypatch.setattr(detect, "list_gpus", lambda root: gpus)
    expected = ("0000:03:00.0", tmp_path / "card1/device/mem_info_vram_used")
    assert rb.gpu_device(pci="0000:03:00.0", drm_root=tmp_path) == expected
    assert rb.gpu_device(render="renderD128", drm_root=tmp_path) == expected
    try:
        rb.gpu_device(pci="0000:09:00.0", drm_root=tmp_path)
    except ValueError as exc:
        assert "no VRAM-backed GPU" in str(exc)
    else:
        raise AssertionError("an unknown GPU must be rejected")


def test_cleanup_removes_the_container_and_reaps_the_fake_upstream_when_start_fails(monkeypatch):
    calls = []

    class FakeUpstream:
        def __init__(self, *a, **k):
            self.waited = self.killed = False

        def terminate(self):
            calls.append("terminate")

        def wait(self, timeout=None):
            if timeout is not None and not self.killed:
                raise rb.subprocess.TimeoutExpired("fake", timeout)  # ignores SIGTERM: must be killed
            calls.append("wait")

        def kill(self):
            self.killed = True
            calls.append("kill")

    def boom(*a, **k):
        raise rb.subprocess.CalledProcessError(125, "podman run")  # e.g. address already in use

    monkeypatch.setattr(rb.subprocess, "Popen", FakeUpstream)
    monkeypatch.setattr(rb, "start_omniroute", boom)
    monkeypatch.setattr(rb.subprocess, "run", lambda cmd, **k: calls.append(cmd))
    try:
        rb.run_omniroute("img", 1.0, 8, None)
    except rb.subprocess.CalledProcessError:
        pass
    else:
        raise AssertionError("the start failure must propagate")
    rm = [c for c in calls if isinstance(c, list)]
    assert len(rm) == 1 and rm[0][:3] == ["podman", "rm", "-f"] and rm[0][-1].startswith("omniroute-bench-")
    assert calls[-3:] == ["terminate", "kill", "wait"]


def test_benchmark_honours_the_top_level_config(tmp_path, monkeypatch, capsys):
    import llmenv

    config = tmp_path / "models.yml"
    config.write_text("omniroute:\n  image: example.invalid/omniroute:pinned\n")
    seen = {}
    monkeypatch.setattr(rb, "run_omniroute", lambda image, *a: seen.setdefault("image", image) and {})
    assert llmenv.main(["--config", str(config), "resource-benchmark", "omniroute"]) == 0
    assert seen["image"] == "example.invalid/omniroute:pinned"
