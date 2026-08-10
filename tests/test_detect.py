import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pylib.detect import (
    DetectError,
    compositor_render_node,
    detect,
    host_resources,
    list_gpus,
    processes_on_render_node,
)

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


def build_proc_with_fd(
    root: Path,
    pid: str,
    comm: str,
    render_node: str,
    *,
    exe_target: str = "/usr/bin/firefox",
    fd_name: str = "7",
    vram_kib: int | None = 524288,
    client_id: str | None = "1",
) -> Path:
    """A /proc/<pid> with one fd pointed at render_node, mirroring the real
    DRM fdinfo layout. vram_kib=None omits the fdinfo file entirely (the
    "kernel didn't expose accounting for this fd" case). client_id=None
    omits the drm-client-id line entirely (the "kernel/driver doesn't
    expose a dedup id for this fd" case, e.g. an older kernel) -- callers
    testing dedup behavior must pass an explicit, non-None client_id."""
    proc = root / "proc"
    pid_dir = proc / pid
    (pid_dir / "fd").mkdir(parents=True, exist_ok=True)
    (pid_dir / "fdinfo").mkdir(parents=True, exist_ok=True)
    (pid_dir / "comm").write_text(comm + "\n")
    target = root / "dev" / "dri" / render_node
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.write_text("")
    (pid_dir / "fd" / fd_name).symlink_to(target)
    if vram_kib is not None:
        client_line = f"drm-client-id:\t{client_id}\n" if client_id is not None else ""
        (pid_dir / "fdinfo" / fd_name).write_text(
            "pos:\t0\nflags:\t02100002\nmnt_id:\t24\n"
            f"drm-driver:\tamdgpu\ndrm-pdev:\t0000:03:00.0\n{client_line}"
            f"drm-memory-vram:\t{vram_kib} KiB\ndrm-memory-gtt:\t0 KiB\n"
        )
    (pid_dir / "exe").symlink_to(exe_target)
    return proc


def test_processes_on_render_node_finds_a_matching_pid(tmp_path):
    proc = build_proc_with_fd(tmp_path, "3021", "firefox", "renderD128", vram_kib=524288)
    result = processes_on_render_node("renderD128", proc)
    assert result == [{"pid": 3021, "comm": "firefox", "exe": "firefox", "vram_mib": 512}]


def test_processes_on_render_node_ignores_other_render_nodes(tmp_path):
    proc = build_proc_with_fd(tmp_path, "3021", "firefox", "renderD129")
    assert processes_on_render_node("renderD128", proc) == []


def test_processes_on_render_node_sums_multiple_fds_for_one_pid(tmp_path):
    """Two fds for one PID with genuinely *distinct* drm-client-id values
    (unlike a dup()'d fd, which shares one) must still sum in full -- this
    is the "not a dedup false positive" counterpart to the dedup test
    below."""
    proc = build_proc_with_fd(
        tmp_path, "3021", "firefox", "renderD128", fd_name="7", vram_kib=524288, client_id="1"
    )
    (proc / "3021" / "fdinfo" / "8").write_text(
        "drm-driver:\tamdgpu\ndrm-client-id:\t2\ndrm-memory-vram:\t262144 KiB\n"
    )
    (proc / "3021" / "fd" / "8").symlink_to(proc.parent / "dev" / "dri" / "renderD128")
    result = processes_on_render_node("renderD128", proc)
    assert result == [{"pid": 3021, "comm": "firefox", "exe": "firefox", "vram_mib": 768}]


def test_processes_on_render_node_dedupes_same_drm_client_id_across_fds(tmp_path):
    """Two fds for one PID sharing the same drm-client-id (e.g. via dup(),
    or certain driver/threading patterns) refer to the same logical DRM
    client. The kernel's fdinfo docs
    (https://origin.kernel.org/doc/html/latest/gpu/drm-usage-stats.html)
    define drm-client-id specifically so consumers can recognize this and
    count each client once, not once per fd -- summing both fds' VRAM here
    would double the real total."""
    proc = build_proc_with_fd(
        tmp_path, "3021", "firefox", "renderD128", fd_name="7", vram_kib=524288, client_id="9"
    )
    (proc / "3021" / "fdinfo" / "8").write_text(
        "drm-driver:\tamdgpu\ndrm-client-id:\t9\ndrm-memory-vram:\t524288 KiB\n"
    )
    (proc / "3021" / "fd" / "8").symlink_to(proc.parent / "dev" / "dri" / "renderD128")
    result = processes_on_render_node("renderD128", proc)
    assert result == [{"pid": 3021, "comm": "firefox", "exe": "firefox", "vram_mib": 512}]


def test_processes_on_render_node_sums_sub_mib_fds_without_losing_precision(tmp_path):
    """Two fds each holding 512 KiB (0 MiB if floored individually) must sum
    to 1 MiB — the per-fd amount must accumulate at sub-MiB precision, not
    be floored to whole MiB before being summed."""
    proc = build_proc_with_fd(tmp_path, "3021", "firefox", "renderD128", fd_name="7", vram_kib=512)
    (proc / "3021" / "fdinfo" / "8").write_text(
        "drm-driver:\tamdgpu\ndrm-memory-vram:\t512 KiB\n"
    )
    (proc / "3021" / "fd" / "8").symlink_to(proc.parent / "dev" / "dri" / "renderD128")
    result = processes_on_render_node("renderD128", proc)
    assert result == [{"pid": 3021, "comm": "firefox", "exe": "firefox", "vram_mib": 1}]


def test_processes_on_render_node_treats_single_token_vram_as_raw_bytes(tmp_path):
    """The DRM fdinfo spec's documented default when drm-memory-vram has no
    unit suffix at all (just a bare number) is raw bytes -- a valid form
    distinct from the two-token `<amount> <unit>` form covered by the other
    tests in this file. 1048576 bytes must parse as 1 MiB, not 0."""
    proc = build_proc_with_fd(tmp_path, "3021", "firefox", "renderD128", vram_kib=524288)
    (proc / "3021" / "fdinfo" / "7").write_text(
        "drm-driver:\tamdgpu\ndrm-memory-vram:\t1048576\n"
    )
    result = processes_on_render_node("renderD128", proc)
    assert result == [{"pid": 3021, "comm": "firefox", "exe": "firefox", "vram_mib": 1}]


def test_processes_on_render_node_treats_missing_fdinfo_as_zero(tmp_path):
    proc = build_proc_with_fd(tmp_path, "3021", "firefox", "renderD128", vram_kib=None)
    result = processes_on_render_node("renderD128", proc)
    assert result == [{"pid": 3021, "comm": "firefox", "exe": "firefox", "vram_mib": 0}]


def test_processes_on_render_node_treats_malformed_fdinfo_as_zero(tmp_path):
    proc = build_proc_with_fd(tmp_path, "3021", "firefox", "renderD128", vram_kib=524288)
    (proc / "3021" / "fdinfo" / "7").write_text("drm-driver:\tamdgpu\nno-vram-line-here:\t1\n")
    result = processes_on_render_node("renderD128", proc)
    assert result == [{"pid": 3021, "comm": "firefox", "exe": "firefox", "vram_mib": 0}]


def test_processes_on_render_node_falls_back_to_comm_when_exe_unreadable(tmp_path):
    proc = build_proc_with_fd(tmp_path, "3021", "firefox", "renderD128")
    (proc / "3021" / "exe").unlink()
    result = processes_on_render_node("renderD128", proc)
    assert result == [{"pid": 3021, "comm": "firefox", "exe": "firefox", "vram_mib": 512}]


def test_processes_on_render_node_skips_pid_with_unreadable_comm(tmp_path):
    proc = build_proc_with_fd(tmp_path, "3021", "firefox", "renderD128")
    (proc / "3021" / "comm").unlink()
    assert processes_on_render_node("renderD128", proc) == []


def test_processes_on_render_node_empty_proc_root_returns_empty_list(tmp_path):
    assert processes_on_render_node("renderD128", tmp_path / "proc") == []


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
