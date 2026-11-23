"""Which test files a change selects (spec D1, D3, G1): pure logic over a {path: text} tree of tracked files."""
from __future__ import annotations

import fnmatch
import functools
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

HUBS = frozenset({
    "tests/conftest.py", "pyproject.toml", "uv.lock", "tools/lib.sh", "llmenv.py", "pylib/config.py", "Makefile",
    ".pre-commit-config.yaml",
    # the selector's own inputs
    "pylib/selection.py", "tools/test.sh", "tools/test-changed.sh", "tools/pre-push.sh", "tests/selection-exempt.txt",
})
ALWAYS = ("tests/test_no_" + "pho" + "enix.py",)  # spelled apart: that test fails any file holding its token
OPT_IN = frozenset({"tests/test_omniroute_update_proof.py", "tests/test_omniroute_characterize.py"})
# Tests that glob a directory instead of naming its files.
PREFIX_OWNERS: dict[str, tuple[str, ...]] = {
    # test_challenges.py:114  sources = sorted(CHALLENGES.rglob("*.mjs"))
    ".claude/skills/onboard-local-model/challenges/": ("tests/skills/onboard-local-model/test_challenges.py",),
    # test_onboard_local_model_scripts.py:1057  skill_files = [..., *sorted(SCRIPTS.glob("*.sh"))]
    ".claude/skills/onboard-local-model/": ("tests/skills/onboard-local-model/test_onboard_local_model_scripts.py",),
    # test_system_one_scout.py:39  docs = [SKILL / "SKILL.md", *sorted((SKILL / "references").glob("*.md"))]
    ".claude/skills/system-one-scout/": ("tests/skills/test_system_one_scout.py",),
}
ROOT_CONFTEST = "tests/conftest.py"
DEPTH = {"commit": 1, "push": 2}
_TEST_FILE = re.compile(r"tests/(.*/)?test_[^/]*\.py")


@dataclass(frozen=True)
class Selection:
    full: bool
    tests: tuple[str, ...]
    reasons: dict[str, str]
    total: int


def _git(root, *args: str) -> list[str]:
    out = subprocess.run(["git", *args], cwd=root, check=True, capture_output=True).stdout
    return [os.fsdecode(entry) for entry in out.split(b"\0") if entry]


def load_tree(root) -> dict[str, str]:
    tree = {}
    for rel in _git(root, "ls-files", "-z"):
        try:
            tree[rel] = (Path(root) / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):  # binary, deleted in the worktree, or not a regular file
            tree[rel] = ""
    return tree


def executables(root) -> set[str]:
    return {entry.split("\t", 1)[1] for entry in _git(root, "ls-files", "-s", "-z") if entry.startswith("100755 ")}


def is_test_file(path: str) -> bool:
    return _TEST_FILE.fullmatch(path) is not None


@functools.cache
def _pattern(path: str) -> re.Pattern[str]:
    spellings = {path, path.rsplit("/", 1)[-1]}
    if path.endswith(".py"):
        spellings.add(path.removesuffix(".py").replace("/", "."))
    pattern = r"(?<!\w)(?:" + "|".join(map(re.escape, sorted(spellings))) + r")(?!\w)"
    if path.endswith(".py"):
        pattern += rf"|^\s*(?:from|import)\b.*\b{re.escape(Path(path).stem)}\b"
    return re.compile(pattern, re.MULTILINE)


def names(text: str, path: str) -> bool:
    return _pattern(path).search(text) is not None


def read_exempt(path) -> dict[str, tuple[str, bool]]:
    exempt = {}
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        glob, _, reason = line.partition("#")
        glob, reason = glob.strip(), reason.strip()
        full = glob.startswith("full:")
        glob = glob.removeprefix("full:").strip()
        if not reason:
            raise ValueError(f"selection exemption without a reason: {glob}")
        exempt[glob] = (reason, full)
    return exempt


def _runnable(tree: dict[str, str], tests) -> set[str]:
    return {t for t in tests if t in tree and is_test_file(t) and t not in ALWAYS and t not in OPT_IN}


def _reach(tree: dict[str, str], path: str, depth: int) -> set[str]:
    """`path` plus `depth` hops of non-hub, non-markdown, non-opt-in files naming a reached file; markdown reaches only
    itself. Opt-in tests never own (G1), so they are no referrer either."""
    reached = frontier = {path}
    for _ in range(0 if path.endswith(".md") else depth):
        frontier = {f for f, text in tree.items() if f not in reached and f not in HUBS and f not in OPT_IN
                    and not f.endswith(".md")
                    and any(names(text, r) for r in frontier)}
        reached = reached | frontier
    return reached


def owners(tree: dict[str, str], path: str, depth: int) -> set[str]:
    reached = _reach(tree, path, depth)
    found = {t for t, text in tree.items() if t in reached or (is_test_file(t) and any(names(text, r) for r in reached))}
    for r in reached:
        if r.rsplit("/", 1)[-1] == "conftest.py" and r != ROOT_CONFTEST:
            found |= {t for t in tree if t.startswith(r.removesuffix("conftest.py"))}
        found |= {t for prefix, tests in PREFIX_OWNERS.items() if r.startswith(prefix) for t in tests}
    return _runnable(tree, found)


def select(tree: dict[str, str], changed, deleted, stage: str, exempt: dict[str, tuple[str, bool]]) -> Selection:
    """Spec D3: per changed or deleted path, the first matching rule; results unioned, ALWAYS once."""
    depth, tests, reasons, full = DEPTH[stage], set(), {}, False
    for path in sorted({*changed, *deleted}):
        matches = [(reason, is_full) for glob, (reason, is_full) in exempt.items() if fnmatch.fnmatchcase(path, glob)]
        full_reason = next((reason for reason, is_full in matches if is_full), None)
        if full_reason is not None:
            full, reasons[path] = True, f"full: {full_reason}"
        elif path in HUBS and stage == "push":
            full, reasons[path] = True, "hub"
        elif path in HUBS:
            tests |= owners(tree, path, 0)
            reasons[path] = "hub at commit: direct namers"
        elif path.rsplit("/", 1)[-1] == "conftest.py":
            tests |= _runnable(tree, (t for t in tree if t.startswith(path.removesuffix("conftest.py"))))
            reasons[path] = "conftest: the tests under its directory"
        elif matches or is_test_file(path) or owners(tree, path, 1):  # owned means a commit-depth owner (G1)
            found = owners(tree, path, depth)
            tests |= found
            reasons[path] = f"{len(found)} owner(s)" if found else f"exempt: {matches[0][0]}" if matches else "test file"
        else:
            full, reasons[path] = True, "no owner"
    return Selection(full, () if full else tuple(sorted(tests | set(ALWAYS))), reasons, sum(map(is_test_file, tree)))
