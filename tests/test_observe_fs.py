import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_collect_fs_statvfs_and_source_failure_are_isolated(tmp_path):
    from pylib.observe_fs import collect_fs

    class Stat:
        f_frsize = 10
        f_blocks = 100
        f_bavail = 25
        f_files = 20
        f_favail = 5

    text = collect_fs(state_path=tmp_path / "state.json", paths={"tmp": tmp_path, "var_home": tmp_path},
                      store_paths={}, statvfs=lambda _: Stat(), clock=lambda: 100)
    assert 'llm_env_fs_bytes{mount="tmp",kind="total"} 1000' in text
    assert 'llm_env_fs_bytes{mount="tmp",kind="used"} 750' in text
    assert 'llm_env_fs_used_ratio{mount="tmp"} 0.75' in text
    assert 'llm_env_fs_inodes_free_ratio{mount="tmp"} 0.25' in text
    assert 'llm_env_source_up{source="fs"} 1' in text
    failed = collect_fs(state_path=tmp_path / "bad.json", paths={"tmp": tmp_path, "var_home": tmp_path},
                        store_paths={}, statvfs=lambda _: (_ for _ in ()).throw(OSError()), clock=lambda: 100)
    assert 'llm_env_source_up{source="fs"} 0' in failed


def test_bounded_scan_cache_symlinks_errors_and_sanitized_top5(tmp_path):
    from pylib.observe_fs import collect_fs

    root = tmp_path / "repo-tmp"
    root.mkdir()
    for name, size in (("a", 1), ("b", 2), ("c", 3), ("d", 4), ("e", 5), ("bad name", 6)):
        item = root / name
        item.mkdir()
        (item / "file").write_bytes(b"x" * size)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "huge").write_bytes(b"x" * 1000)
    (root / "link").symlink_to(outside, target_is_directory=True)
    (root / "loop").symlink_to(root, target_is_directory=True)
    now = [0.0]
    calls = []

    def clock():
        now[0] += 0.01
        return now[0]

    def listing(path):
        calls.append(path)
        return os.scandir(path)

    state = tmp_path / "state.json"
    kwargs = {"state_path": state, "paths": {}, "store_paths": {"repo_tmp": root}, "clock": clock,
              "listing": listing, "scan_budget_s": 0.15}
    first = collect_fs(**kwargs)
    assert 'llm_env_fs_scan_truncated 1' in first
    assert calls
    second = collect_fs(**kwargs)
    assert 'llm_env_fs_scan_truncated 1' in second
    assert 'dir="link"' not in second and 'dir="loop"' not in second
    now[0] += 601
    collect_fs(**kwargs)
    assert len(calls) > 1


def test_zero_inode_count_omits_ratio_but_normal_filesystem_keeps_it(tmp_path):
    from pylib.observe_fs import collect_fs

    class Stat:
        f_frsize, f_blocks, f_bavail, f_files, f_favail = 1, 10, 2, 0, 0

    zero = collect_fs(state_path=tmp_path / "zero", paths={"tmp": tmp_path, "var_home": tmp_path},
                      store_paths={}, statvfs=lambda _: Stat(), clock=lambda: 100)
    assert 'llm_env_fs_inodes_free_ratio{mount="tmp"}' not in zero
    assert 'llm_env_fs_inodes_free_ratio{mount="var_home"}' not in zero
    assert zero.count('llm_env_fs_inodes_free_ratio{') == 0
    Stat.f_files, Stat.f_favail = 10, 2
    normal = collect_fs(state_path=tmp_path / "normal", paths={"tmp": tmp_path, "var_home": tmp_path},
                        store_paths={}, statvfs=lambda _: Stat(), clock=lambda: 100)
    assert 'llm_env_fs_inodes_free_ratio{mount="var_home"} 0.2' in normal


def test_wall_clock_cache_invalid_or_future_stamp_is_stale_and_age_is_bounded(tmp_path):
    import json

    from pylib.observe_fs import collect_fs

    root = tmp_path / "store"
    root.mkdir()
    (root / "file").write_bytes(b"123")
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"scanned": 5000.0, "results": {"repo_tmp": 999}, "top": {}}))
    walked = []
    text = collect_fs(state_path=state, paths={}, store_paths={"repo_tmp": root},
                      clock=lambda: 10.0, listing=lambda p: (walked.append(p), os.scandir(p))[1])
    assert walked and 'llm_env_fs_store_bytes{store="repo_tmp"} 3' in text
    assert "llm_env_fs_scan_age_seconds 0" in text
    state.write_text('{bad')
    text = collect_fs(state_path=state, paths={}, store_paths={"repo_tmp": root}, clock=lambda: 1e20)
    assert "llm_env_fs_scan_age_seconds 0" in text


def test_symlinked_store_root_is_resolved_and_entry_symlinks_are_skipped(tmp_path):
    from pylib.observe_fs import collect_fs

    real = tmp_path / "real"
    real.mkdir()
    (real / "file").write_bytes(b"abc")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "huge").write_bytes(b"x" * 1000)
    (real / "link").symlink_to(outside, target_is_directory=True)
    (real / "loop").symlink_to(real, target_is_directory=True)
    linked = tmp_path / "linked"
    linked.symlink_to(real, target_is_directory=True)
    text = collect_fs(state_path=tmp_path / "s", paths={}, store_paths={"repo_tmp": linked}, clock=lambda: 0)
    assert 'llm_env_fs_store_bytes{store="repo_tmp"} 3' in text


def test_missing_store_root_counts_error_and_each_store_gets_budget(tmp_path):
    from pylib.observe_fs import collect_fs

    root = tmp_path / "large"
    root.mkdir()
    (root / "child").mkdir()
    (root / "child" / "file").write_bytes(b"x")
    now = [0.0]
    calls = []

    def clock():
        now[0] += 0.1
        return now[0]

    def listing(path):
        calls.append(Path(path))
        return os.scandir(path)

    text = collect_fs(state_path=tmp_path / "s", paths={}, store_paths={"repo_tmp": root,
                      "podman_storage": tmp_path / "missing"}, clock=clock, listing=listing, scan_budget_s=1)
    assert 'llm_env_fs_store_unreadable_entries{store="podman_storage"} 1' in text
    assert any(p == root for p in calls)


def test_label_sanitizer_handles_adversarial_names_and_ranks_are_bounded(tmp_path):
    from pylib.observe_fs import collect_fs

    root = tmp_path / "repo"
    root.mkdir()
    names = ("bad name", 'quote"', "line\\nbreak", "雪", "x" * 200, "1digit", "safe-name")
    for name in names:
        folder = root / name
        folder.mkdir()
        (folder / "f").write_bytes(b"x" * 100)
    text = collect_fs(state_path=tmp_path / "state", paths={"tmp": tmp_path, "var_home": tmp_path},
                      store_paths={"repo_tmp": root}, clock=lambda: 100)
    labels = [line.split('dir="', 1)[1].split('"', 1)[0] for line in text.splitlines()
              if line.startswith("llm_env_fs_repo_tmp_top_bytes{")]
    assert len(labels) == 5 and all(len(label) <= 64 and label not in names[:5] for label in labels)
    assert {line.split('rank="', 1)[1].split('"', 1)[0] for line in text.splitlines()
            if line.startswith("llm_env_fs_repo_tmp_top_bytes{")} == {"1", "2", "3", "4", "5"}


def test_walk_skips_entries_on_another_device(tmp_path):
    from pylib.observe_fs import _walk

    root = tmp_path / "root"
    root.mkdir()
    (root / "ignored").write_bytes(b"x" * 100)
    class Entry:
        name, path = "cross-device", str(root / "ignored")
        def is_symlink(self): return False
        def stat(self, follow_symlinks=False): return type("S", (), {"st_dev": root.stat().st_dev + 1, "st_size": 100})()
        def is_dir(self, follow_symlinks=False): return False
        def is_file(self, follow_symlinks=False): return True
    class Listing:
        def __enter__(self): return iter([Entry()])
        def __exit__(self, *exc): return False
    size, _, _, _, _ = _walk(root, 100, lambda: 0, lambda _: Listing())
    assert size == 0


def test_walk_checks_clock_inside_huge_directory(tmp_path):
    from pylib.observe_fs import _walk

    class Entry:
        name, path = "f", "/fake/f"
        def is_symlink(self): return False
        def stat(self, follow_symlinks=False): return type("S", (), {"st_dev": tmp_path.stat().st_dev, "st_size": 1})()
        def is_dir(self, follow_symlinks=False): return False
        def is_file(self, follow_symlinks=False): return True
    class Listing:
        def __enter__(self): return (Entry() for _ in range(100000))
        def __exit__(self, *exc): return False
    now = [0.0]
    def clock():
        now[0] += 0.001
        return now[0]
    _, _, _, _, truncated = _walk(tmp_path, 0.5, clock, lambda _: Listing())
    assert truncated and now[0] < 1


def test_cache_stamp_uses_wall_clock_and_rejects_nonfinite(tmp_path):
    import json
    import math

    from pylib.observe_fs import collect_fs

    root = tmp_path / "store"
    root.mkdir()
    (root / "file").write_bytes(b"abc")
    state = tmp_path / "state.json"
    walks = []
    def listing(path):
        walks.append(path)
        return os.scandir(path)

    complete = {store: 99 for store in ("repo_tmp", "victoriametrics", "podman_storage", "pytest_roots")}
    for stamp in (math.nan, math.inf, -math.inf, -1, 101, "100", None):
        state.write_text(json.dumps({"scanned": stamp, "results": complete}))
        text = collect_fs(state_path=state, paths={}, store_paths={"repo_tmp": root},
                          clock=lambda: 100, listing=listing)
        assert 'llm_env_fs_store_bytes{store="repo_tmp"} 3' in text
    assert len(walks) == 7
    state.write_text(json.dumps({"scanned": 99, "results": complete}))
    before = len(walks)
    text = collect_fs(state_path=state, paths={}, store_paths={"repo_tmp": root},
                      clock=lambda: 100, listing=listing)
    assert len(walks) == before
    assert "llm_env_fs_scan_age_seconds 1" in text
    state.write_text(json.dumps({"scanned": 0, "results": complete}))
    collect_fs(state_path=state, paths={}, store_paths={"repo_tmp": root},
               clock=lambda: 601, listing=listing)
    assert len(walks) > before
    collect_fs(state_path=state, paths={}, store_paths={"repo_tmp": root}, listing=listing)
    persisted = json.loads(state.read_text())["scanned"]
    assert time.time() - 5 < persisted <= time.time()


def test_symlinked_store_root_is_resolved_once(tmp_path):
    from pylib.observe_fs import collect_fs

    real = tmp_path / "real"
    real.mkdir()
    (real / "file").write_bytes(b"abc")
    linked = tmp_path / "linked"
    linked.symlink_to(real, target_is_directory=True)
    seen = []
    text = collect_fs(state_path=tmp_path / "state", paths={}, store_paths={"repo_tmp": linked},
                      clock=lambda: 0, listing=lambda path: (seen.append(Path(path)), os.scandir(path))[1])
    assert 'llm_env_fs_store_bytes{store="repo_tmp"} 3' in text
    assert seen == [real.resolve()]


def test_scan_budget_is_split_per_store(tmp_path):
    from pylib.observe_fs import collect_fs

    first, later = tmp_path / "first", tmp_path / "later"
    first.mkdir(); later.mkdir()
    for index in range(100):
        (first / f"file-{index}").write_bytes(b"a")
    (later / "file").write_bytes(b"b")
    ticks = [0.0]
    def clock():
        ticks[0] += 0.05
        return ticks[0]
    seen = []
    def listing(path):
        seen.append(Path(path))
        return os.scandir(path)
    collect_fs(state_path=tmp_path / "state", paths={}, store_paths={"repo_tmp": first,
               "victoriametrics": later}, scan_budget_s=0.8, clock=clock, listing=listing)
    assert first in seen and later in seen


def test_walk_errors_do_not_break_source_and_top5_is_bounded(tmp_path):
    from pylib.observe_fs import collect_fs

    root = tmp_path / "repo-tmp"
    root.mkdir()
    for i in range(7):
        d = root / f"d{i}"
        d.mkdir()
        (d / "x").write_bytes(b"a" * (i + 1))
    class Stat:
        f_frsize = 1
        f_blocks = 10
        f_bavail = 2
        f_files = 10
        f_favail = 2
    text = collect_fs(state_path=tmp_path / "s", paths={"tmp": tmp_path, "var_home": tmp_path},
                      store_paths={"repo_tmp": root}, statvfs=lambda _: Stat(), clock=lambda: 0)
    assert sum('llm_env_fs_repo_tmp_top_bytes{' in line for line in text.splitlines()) == 5
    assert 'llm_env_fs_scan_errors' in text
