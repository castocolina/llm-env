import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pylib.detect import DetectError, compositor_render_node, detect, host_resources, list_gpus

MIB = 1024 * 1024


def build_drm(root: Path) -> Path:
    """Mirror the real topology: card1 = dGPU 16304 MiB, card0 = iGPU 512 MiB."""
    drm = root / "drm"
    for card, pci, render, total, used in (
        ("card0", "0000:0e:00.0", "renderD129", 512, 51),
        ("card1", "0000:03:00.0", "renderD128", 16304, 2026),
    ):
        device = root / "devices" / pci
        device.mkdir(parents=True, exist_ok=True)
        (device / "mem_info_vram_total").write_text(str(total * MIB))
        (device / "mem_info_vram_used").write_text(str(used * MIB))

        card_dir = drm / card
        card_dir.mkdir(parents=True, exist_ok=True)
        (card_dir / "device").symlink_to(device, target_is_directory=True)

        render_dir = drm / render
        render_dir.mkdir(parents=True, exist_ok=True)
        (render_dir / "device").symlink_to(device, target_is_directory=True)

    for connector, status in (
        ("card0-HDMI-A-2", "connected"),
        ("card0-DP-4", "disconnected"),
        ("card1-DP-2", "connected"),
        ("card1-DP-1", "disconnected"),
    ):
        conn = drm / connector
        conn.mkdir(parents=True, exist_ok=True)
        (conn / "status").write_text(status + "\n")
    return drm


def test_list_gpus_maps_card_to_pci_and_render_node(tmp_path):
    gpus = {g["card"]: g for g in list_gpus(build_drm(tmp_path))}
    assert gpus["card1"]["pci_address"] == "0000:03:00.0"
    assert gpus["card1"]["render_node"] == "renderD128"
    assert gpus["card0"]["pci_address"] == "0000:0e:00.0"
    assert gpus["card0"]["render_node"] == "renderD129"


def test_list_gpus_reads_vram(tmp_path):
    gpus = {g["card"]: g for g in list_gpus(build_drm(tmp_path))}
    assert gpus["card1"]["vram_total_mib"] == 16304
    assert gpus["card1"]["vram_used_mib"] == 2026
    assert gpus["card0"]["vram_total_mib"] == 512


def test_list_gpus_lists_only_connected_outputs(tmp_path):
    gpus = {g["card"]: g for g in list_gpus(build_drm(tmp_path))}
    assert gpus["card1"]["connected_outputs"] == ["card1-DP-2"]
    assert gpus["card0"]["connected_outputs"] == ["card0-HDMI-A-2"]


def test_list_gpus_is_sorted_by_card_name(tmp_path):
    assert [g["card"] for g in list_gpus(build_drm(tmp_path))] == ["card0", "card1"]


def build_proc(root: Path, comm: str, render_node: str) -> Path:
    proc = root / "proc"
    pid_dir = proc / "3021"
    (pid_dir / "fd").mkdir(parents=True)
    (pid_dir / "comm").write_text(comm + "\n")
    target = root / "dev" / "dri" / render_node
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("")
    (pid_dir / "fd" / "7").symlink_to(target)
    return proc


def test_compositor_render_node_found(tmp_path):
    proc = build_proc(tmp_path, "plasmashell", "renderD128")
    assert compositor_render_node(proc) == "renderD128"


def test_compositor_render_node_absent_returns_none(tmp_path):
    proc = build_proc(tmp_path, "bash", "renderD128")
    assert compositor_render_node(proc) is None


def test_detect_combines_both_sources(tmp_path):
    drm = build_drm(tmp_path)
    proc = build_proc(tmp_path, "kwin_wayland", "renderD128")
    result = detect(drm, proc)
    assert len(result["gpus"]) == 2
    assert result["compositor_render_node"] == "renderD128"


def test_card_without_vram_total_is_skipped(tmp_path):
    drm = build_drm(tmp_path)
    (tmp_path / "devices" / "0000:0e:00.0" / "mem_info_vram_total").unlink()
    cards = [g["card"] for g in list_gpus(drm)]
    assert cards == ["card1"], "a card with no VRAM attribute must be skipped"


def test_card_with_corrupt_vram_total_raises(tmp_path):
    drm = build_drm(tmp_path)
    (tmp_path / "devices" / "0000:03:00.0" / "mem_info_vram_total").write_text("garbage")
    with pytest.raises(DetectError) as excinfo:
        list_gpus(drm)
    assert "does not contain an integer" in str(excinfo.value)


def test_card_without_device_symlink_is_skipped(tmp_path):
    drm = build_drm(tmp_path)
    (drm / "card0" / "device").unlink()
    cards = [g["card"] for g in list_gpus(drm)]
    assert cards == ["card1"], "a card with no resolvable PCI device must be skipped"


def build_proc_resources(root: Path, cpu_count: int, memory_total_kib: int) -> Path:
    proc = root / "proc"
    proc.mkdir(parents=True, exist_ok=True)
    cpuinfo_lines = []
    for index in range(cpu_count):
        cpuinfo_lines.append(f"processor\t: {index}")
        cpuinfo_lines.append("model name\t: Fixture CPU")
        cpuinfo_lines.append("")
    (proc / "cpuinfo").write_text("\n".join(cpuinfo_lines))
    (proc / "meminfo").write_text(
        f"MemTotal:       {memory_total_kib} kB\n"
        "MemFree:         1000000 kB\n"
    )
    return proc


def test_host_resources_reads_cpu_count_and_memory(tmp_path):
    proc = build_proc_resources(tmp_path, cpu_count=8, memory_total_kib=32 * 1024 * 1024)
    result = host_resources(proc)
    assert result == {"cpu_count": 8, "memory_total_mib": 32 * 1024}


def test_host_resources_missing_cpuinfo_raises(tmp_path):
    proc = tmp_path / "proc"
    proc.mkdir()
    (proc / "meminfo").write_text("MemTotal:       1000 kB\n")
    with pytest.raises(DetectError):
        host_resources(proc)


def test_host_resources_missing_meminfo_raises(tmp_path):
    proc = build_proc_resources(tmp_path, cpu_count=4, memory_total_kib=1)
    (proc / "meminfo").unlink()
    with pytest.raises(DetectError):
        host_resources(proc)


def test_host_resources_corrupt_meminfo_raises(tmp_path):
    proc = build_proc_resources(tmp_path, cpu_count=4, memory_total_kib=1)
    (proc / "meminfo").write_text("MemTotal:       not-a-number kB\n")
    with pytest.raises(DetectError):
        host_resources(proc)


def test_host_resources_no_processor_entries_raises(tmp_path):
    proc = tmp_path / "proc"
    proc.mkdir()
    (proc / "cpuinfo").write_text("")
    (proc / "meminfo").write_text("MemTotal:       1000 kB\n")
    with pytest.raises(DetectError):
        host_resources(proc)
