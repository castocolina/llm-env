"""Detect GPUs and which render node the compositor is using.

Reads sysfs and procfs directly. Roots are injectable so tests can supply
fixtures instead of touching the live system.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

MIB = 1024 * 1024
CARD_RE = re.compile(r"^card\d+$")
RENDER_RE = re.compile(r"^renderD\d+$")
COMPOSITOR_NAMES = ("kwin_wayland", "kwin_x11", "plasmashell", "gnome-shell", "sway")


class DetectError(Exception):
    """Raised when a sysfs value is present but cannot be interpreted."""


def _read_int_required(path: Path) -> int | None:
    """Return the integer at path, or None if the file is absent.

    Raises DetectError if the file exists but cannot be read or parsed, so a
    corrupt sysfs value fails loudly instead of silently reading as zero.
    """
    try:
        text = path.read_text()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise DetectError(f"cannot read {path}: {exc}") from exc
    try:
        return int(text.strip())
    except ValueError as exc:
        raise DetectError(
            f"{path} does not contain an integer: {text.strip()!r}"
        ) from exc


def _read_int_optional(path: Path, default: int = 0) -> int:
    """Return the integer at path, or default if it is missing or unparseable."""
    try:
        return int(path.read_text().strip())
    except (OSError, ValueError):
        return default


def _pci_of(entry: Path) -> str | None:
    device = entry / "device"
    if not device.exists():
        return None
    return device.resolve().name


def list_gpus(drm_root: Path = Path("/sys/class/drm")) -> list[dict[str, Any]]:
    drm_root = Path(drm_root)
    if not drm_root.is_dir():
        return []

    entries = list(drm_root.iterdir())

    render_by_pci: dict[str, str] = {}
    for entry in entries:
        if RENDER_RE.match(entry.name):
            pci = _pci_of(entry)
            if pci:
                render_by_pci[pci] = entry.name

    outputs_by_card: dict[str, list[str]] = {}
    for entry in entries:
        status_file = entry / "status"
        if not status_file.is_file():
            continue
        if status_file.read_text().strip() != "connected":
            continue
        card = entry.name.split("-", 1)[0]
        outputs_by_card.setdefault(card, []).append(entry.name)

    gpus: list[dict[str, Any]] = []
    for entry in entries:
        if not CARD_RE.match(entry.name):
            continue
        pci = _pci_of(entry)
        if not pci:
            continue
        device = entry / "device"
        total = _read_int_required(device / "mem_info_vram_total")
        if total is None:
            # Not a VRAM-backed GPU (virtual driver, USB display adapter).
            continue
        gpus.append(
            {
                "card": entry.name,
                "pci_address": pci,
                "render_node": render_by_pci.get(pci, ""),
                "vram_total_mib": total // MIB,
                "vram_used_mib": _read_int_optional(device / "mem_info_vram_used") // MIB,
                "connected_outputs": sorted(outputs_by_card.get(entry.name, [])),
            }
        )

    return sorted(gpus, key=lambda g: g["card"])


def compositor_render_node(proc_root: Path = Path("/proc")) -> str | None:
    proc_root = Path(proc_root)
    if not proc_root.is_dir():
        return None

    for pid_dir in proc_root.iterdir():
        if not pid_dir.name.isdigit():
            continue
        comm_file = pid_dir / "comm"
        try:
            comm = comm_file.read_text().strip()
        except OSError:
            continue
        if comm not in COMPOSITOR_NAMES:
            continue

        fd_dir = pid_dir / "fd"
        try:
            fds = list(fd_dir.iterdir())
        except OSError:
            continue
        for fd in fds:
            try:
                target = fd.resolve().name
            except OSError:
                continue
            if RENDER_RE.match(target):
                return target
    return None


def _parse_fdinfo(text: str) -> tuple[int, str | None]:
    """Return (vram_bytes, drm_client_id) parsed from fdinfo content.

    vram_bytes (not MiB) so multiple fds for the same PID can be summed at
    full precision before any MiB rounding happens -- summing
    already-floored per-fd MiB values would lose small fds entirely (e.g.
    two 512 KiB fds would each floor to 0 MiB and sum to 0, when the
    correct combined total is 1024 KiB = 1 MiB). 0 for missing/unparseable
    content -- best-effort, matching the kernel's own "a process can hold
    the fd without VRAM accounting being exposed for it" behavior.

    `drm-memory-vram:` accepts two forms per the kernel's fdinfo spec: the
    usual `<amount> <unit>` pair (KiB/MiB/GiB/B, case-insensitive), and a
    single bare number with no unit suffix at all -- the spec's documented
    default of raw bytes when no unit is given. Both must parse correctly;
    only a line that is neither of these (missing entirely, or with a
    non-numeric/unrecognized token) falls through to the 0 default.

    drm_client_id is the raw `drm-client-id:` value (a string; the kernel
    defines no particular format for it, so it's never parsed as an int),
    or None when the fdinfo record has no such line (older kernels/drivers
    that don't expose it). The kernel's DRM fdinfo interface
    (https://origin.kernel.org/doc/html/latest/gpu/drm-usage-stats.html)
    defines drm-client-id specifically to identify duplicated/shared fds
    (e.g. via dup()) that refer to the same logical client, and documents
    that consumers should count each client once, not once per fd --
    callers use this to de-duplicate VRAM across fds for the same PID.
    """
    vram_bytes = 0
    client_id: str | None = None
    for line in text.splitlines():
        if line.startswith("drm-memory-vram:"):
            parts = line.split(":", 1)[1].split()
            if len(parts) == 2:
                amount_text, unit = parts
                try:
                    amount = int(amount_text)
                except ValueError:
                    amount = None
                if amount is not None:
                    unit = unit.lower()
                    if unit == "kib":
                        vram_bytes = amount * 1024
                    elif unit == "mib":
                        vram_bytes = amount * MIB
                    elif unit == "gib":
                        vram_bytes = amount * 1024 * MIB
                    elif unit == "b":
                        vram_bytes = amount
            elif len(parts) == 1:
                # No unit suffix at all -- the fdinfo spec's documented
                # default is raw bytes (e.g. "drm-memory-vram:\t1048576").
                try:
                    vram_bytes = int(parts[0])
                except ValueError:
                    pass
        elif line.startswith("drm-client-id:"):
            value = line.split(":", 1)[1].strip()
            if value:
                client_id = value
    return vram_bytes, client_id


def processes_on_render_node(render_node: str, proc_root: Path = Path("/proc")) -> list[dict]:
    """Return every process with an open fd on render_node, with its VRAM use.

    Fds sharing the same drm-client-id (duplicated fds referring to one
    logical DRM client, e.g. via dup()) are counted once, not once per fd.
    An fd whose fdinfo record has no drm-client-id line at all (older
    kernel/driver) is always counted on its own, preserving today's
    behavior for kernels that don't expose the id.
    """
    proc_root = Path(proc_root)
    if not proc_root.is_dir():
        return []

    results: list[dict] = []
    for pid_dir in proc_root.iterdir():
        if not pid_dir.name.isdigit():
            continue
        try:
            comm = (pid_dir / "comm").read_text().strip()
        except OSError:
            continue

        try:
            fds = list((pid_dir / "fd").iterdir())
        except OSError:
            continue

        matched = False
        vram_bytes = 0
        seen_client_ids: set[str] = set()
        for fd in fds:
            try:
                target = fd.resolve().name
            except OSError:
                continue
            if target != render_node:
                continue
            matched = True
            try:
                fdinfo_text = (pid_dir / "fdinfo" / fd.name).read_text()
            except OSError:
                continue
            fd_vram_bytes, client_id = _parse_fdinfo(fdinfo_text)
            if client_id is not None:
                if client_id in seen_client_ids:
                    continue
                seen_client_ids.add(client_id)
            vram_bytes += fd_vram_bytes

        if not matched:
            continue

        try:
            exe = (pid_dir / "exe").readlink().name
        except OSError:
            exe = comm

        results.append(
            {"pid": int(pid_dir.name), "comm": comm, "exe": exe, "vram_mib": vram_bytes // MIB}
        )

    return results


def host_resources(proc_root: Path = Path("/proc")) -> dict[str, int]:
    """Return the host's total CPU count and total RAM, read from procfs directly."""
    proc_root = Path(proc_root)
    cpuinfo_path = proc_root / "cpuinfo"
    try:
        cpuinfo = cpuinfo_path.read_text()
    except OSError as exc:
        raise DetectError(f"cannot read {cpuinfo_path}: {exc}") from exc
    cpu_count = sum(1 for line in cpuinfo.splitlines() if line.startswith("processor"))
    if cpu_count == 0:
        raise DetectError(f"no processor entries found in {cpuinfo_path}")

    meminfo_path = proc_root / "meminfo"
    try:
        meminfo = meminfo_path.read_text()
    except OSError as exc:
        raise DetectError(f"cannot read {meminfo_path}: {exc}") from exc
    memory_total_kib: int | None = None
    for line in meminfo.splitlines():
        if line.startswith("MemTotal:"):
            parts = line.split()
            if len(parts) < 2 or not parts[1].isdigit():
                raise DetectError(f"MemTotal line is malformed in {meminfo_path}: {line!r}")
            memory_total_kib = int(parts[1])
            break
    if memory_total_kib is None:
        raise DetectError(f"MemTotal not found in {meminfo_path}")

    return {"cpu_count": cpu_count, "memory_total_mib": memory_total_kib // 1024}


def detect(
    drm_root: Path = Path("/sys/class/drm"),
    proc_root: Path = Path("/proc"),
) -> dict[str, Any]:
    return {
        "gpus": list_gpus(drm_root),
        "compositor_render_node": compositor_render_node(proc_root),
    }
