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


def detect(
    drm_root: Path = Path("/sys/class/drm"),
    proc_root: Path = Path("/proc"),
) -> dict[str, Any]:
    return {
        "gpus": list_gpus(drm_root),
        "compositor_render_node": compositor_render_node(proc_root),
    }
