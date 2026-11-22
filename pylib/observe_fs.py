"""Read-only filesystem headroom and cached, bounded store-size metrics."""
from __future__ import annotations

import json
import math
import os
import re
import time
from pathlib import Path
from typing import Any

STORES = ("repo_tmp", "victoriametrics", "podman_storage", "pytest_roots")
SAFE = re.compile(r"[A-Za-z0-9._-]{1,64}\Z")


def _walk(root: Path, deadline: float, clock: Any, listing: Any) -> tuple[int, int, dict[str, int], int, bool]:
    size = errors = 0
    top: dict[str, int] = {}
    truncated = False
    try:
        root = Path(os.path.realpath(root))
        device = root.stat(follow_symlinks=False).st_dev
    except OSError:
        return 0, 1, top, 1, False
    stack = [(root, "")]
    while stack:
        if clock() >= deadline:
            truncated = True
            break
        path, first = stack.pop()
        try:
            with listing(path) as entries:
                for entry in entries:
                    if clock() >= deadline:
                        truncated = True
                        break
                    try:
                        if entry.is_symlink():
                            continue
                        st = entry.stat(follow_symlinks=False)
                        name = first or entry.name
                        if st.st_dev != device:
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            stack.append((Path(entry.path), name))
                        elif entry.is_file(follow_symlinks=False):
                            size += st.st_size
                            top[name] = top.get(name, 0) + st.st_size
                    except OSError:
                        errors += 1
        except OSError:
            errors += 1
        if truncated:
            break
    return size, errors, top, errors, truncated


def collect_fs(*, state_path: Path | None = None, paths: dict[str, Path] | None = None,
               store_paths: dict[str, Path] | None = None, statvfs: Any = os.statvfs,
               clock: Any = time.time, listing: Any = os.scandir,
               scan_budget_s: float = 20) -> str:
    paths = paths if paths is not None else {"tmp": Path("/tmp"), "var_home": Path(os.environ.get("HOME", "/var/home")).resolve().parent}
    store_paths = store_paths if store_paths is not None else {
        "repo_tmp": Path(__file__).resolve().parents[4] / "tmp",
        "victoriametrics": Path(os.environ.get("LLM_ENV_VM_DATA_PATH", Path.home() / ".local/share/containers/storage/volumes/llm-env-observe_metrics/_data")),
        "podman_storage": Path(os.environ.get("HOME", "/var/home")) / ".local/share/containers/storage",
        "pytest_roots": Path("/tmp") / f"pytest-of-{os.environ.get('USER', '')}",
    }
    state_path = state_path or Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "llm-env/observe/fs-state.json"
    now = clock()
    lines: list[str] = []
    try:
        for mount in ("tmp", "var_home"):
            if mount not in paths:
                continue
            s = statvfs(paths[mount])
            total, free = s.f_frsize * s.f_blocks, s.f_frsize * s.f_bavail
            used = total - free
            lines += [f'llm_env_fs_bytes{{mount="{mount}",kind="total"}} {total}',
                      f'llm_env_fs_bytes{{mount="{mount}",kind="used"}} {used}',
                      f'llm_env_fs_bytes{{mount="{mount}",kind="free"}} {free}',
                      f'llm_env_fs_used_ratio{{mount="{mount}"}} {used / total if total else 0}']
            if s.f_files:
                lines.append(f'llm_env_fs_inodes_free_ratio{{mount="{mount}"}} {s.f_favail / s.f_files}')
        try:
            state = json.loads(state_path.read_text())
            stamp = state["scanned"]
            if isinstance(stamp, bool) or not isinstance(stamp, (int, float)):
                raise TypeError("invalid cache stamp")
            scanned = float(stamp)
            results = state["results"]
            if (not math.isfinite(scanned) or scanned < 0 or scanned > now
                    or not isinstance(results, dict) or not all(store in results for store in STORES)):
                raise ValueError("invalid cache")
        except (OSError, ValueError, KeyError, TypeError):
            scanned, results = -1e30, {}
        errors = 0
        unreadable: dict[str, int] = {}
        truncated = 0
        if now - scanned >= 600 or any(store not in results for store in STORES):
            results = {}
            top = {}
            per_store_budget = max(0, scan_budget_s) / len(STORES)
            for store in STORES:
                root = store_paths.get(store)
                if root is None:
                    results[store] = 0
                    unreadable[store] = 0
                    continue
                started = clock()
                value, count, leaders, missed, cut = _walk(Path(root), started + per_store_budget, clock, listing)
                results[store] = value
                errors += count
                unreadable[store] = missed
                truncated |= int(cut)
                if store == "repo_tmp":
                    top = leaders
            scanned = now
            state_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            state_path.write_text(json.dumps({"scanned": scanned, "results": results, "top": top, "errors": errors, "unreadable": unreadable, "truncated": truncated}))
        else:
            try:
                cached = json.loads(state_path.read_text())
                top, errors, truncated = cached.get("top", {}), cached.get("errors", 0), cached.get("truncated", 0)
                unreadable = cached.get("unreadable", {})
            except (OSError, ValueError):
                top, errors, unreadable, truncated = {}, 0, {}, 0
        for store in STORES:
            lines.append(f'llm_env_fs_store_bytes{{store="{store}"}} {results.get(store, 0)}')
            lines.append(f'llm_env_fs_store_unreadable_entries{{store="{store}"}} {int(unreadable.get(store, 0))}')
        ordered = sorted(top.items(), key=lambda item: (-item[1], item[0]))[:5]
        for rank in range(1, 6):
            name, value = ordered[rank - 1] if len(ordered) >= rank else ("other", 0)
            safe_name = name if SAFE.fullmatch(name) else "other"
            lines.append(f'llm_env_fs_repo_tmp_top_bytes{{rank="{rank}",dir="{safe_name}"}} {value}')
        lines += [f"llm_env_fs_scan_age_seconds {min(86400, max(0, now - scanned))}",
                  f"llm_env_fs_scan_truncated {int(truncated)}", f"llm_env_fs_scan_errors {int(errors)}",
                  'llm_env_source_up{source="fs"} 1']
    except (OSError, ValueError, TypeError, KeyError, AttributeError, ZeroDivisionError):
        return 'llm_env_source_up{source="fs"} 0\n'
    families = {line.split("{")[0].split()[0] for line in lines} - {"llm_env_source_up"}
    return "".join(f"# HELP {name} Filesystem metric.\n# TYPE {name} gauge\n" for name in sorted(families)) + "\n".join(lines) + "\n"
