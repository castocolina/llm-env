"""The git hooks: commit-stage checks and selected tests (spec R2, D7), the pre-push hook (tools/pre-push.sh) and its
link by make dev-setup (spec R3, D8); scratch repos only."""
import ast
import fnmatch
import json
import os
import re
import signal
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
import yaml
from git_helpers import git, git_out, git_repo
from runner_helpers import (
    calls,
    descendants,
    run_runner,
    runner_repo,
    start_runner,
    stop_runner,
    wait_until,
)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pylib.selection import ALWAYS, executables, load_tree

NP = ALWAYS[0]
Z = "0" * 40
REFUSED = "push shape not supported by the hook: run make test, then git push --no-verify"
DIRTY = "push refused: the index or worktree is dirty: commit or stash your changes, then push again"
STUBS = {"tools/validate.sh": 'echo validate >>"$CALLS"; exit "${STUB_VALIDATE_RC:-0}"\n',
         "tools/test-changed.sh": 'echo "changed $* from=$PUSH_FROM to=$PUSH_TO" >>"$CALLS"\n',
         "tools/test.sh": 'echo full >>"$CALLS"\n',
         "stub/pre-commit": '#!/usr/bin/env bash\necho "$*" >>"$CALLS"\n'}
REVS = {"B": "main~1", "H": "main", "O": "other", "D": "ahead"}


def _run_tool(repo, script, env=None):
    return run_runner(repo, env=env, script=script)


def sha(repo, name):
    return git_out(repo, "rev-parse", REVS[name])


def _push_repo(tmp_path, shape=None):
    repo = runner_repo(tmp_path, extra=("tools/pre-push.sh", "setup/dev-setup.sh", "tools/lib.sh"))
    (repo / ".git/info").mkdir(parents=True)
    (repo / ".git/info/exclude").write_text("stub/\npodman-state\ncalls\ntmp/\n")
    for rel, body in STUBS.items():
        (repo / rel).write_text(body)
    (repo / "stub/pre-commit").chmod(0o755)
    git_repo(repo, {"README.md": b"base\n"})
    git(repo, "commit", "-q", "-m", "B")
    (repo / "README.md").write_text("head\n")
    git(repo, "commit", "-qam", "H")
    git(repo, "init", "-q", "--bare", str(tmp_path / "remote.git"))
    git(repo, "remote", "add", "origin", str(tmp_path / "remote.git"))
    for branch, base, letter in (("other", "main~1", "O"), ("ahead", "main", "D")):
        git(repo, "checkout", "-q", "-b", branch, base)
        git(repo, "commit", "-q", "--allow-empty", "-m", letter)
    git(repo, "checkout", "-q", "main")
    git(repo, "tag", "-a", "-m", "v1", "v1", "main")
    if shape and shape.endswith(" edit"):
        (repo / "README.md").write_text("edit\n")
        if shape != "unstaged edit":
            git(repo, "add", "README.md")
        if shape == "cancelled edit":
            (repo / "README.md").write_text("head\n")
    return repo


def _expand(repo, token):
    if token == "Z":
        return Z
    if token in ("main", "other"):
        return f"refs/heads/{token}"
    if token.startswith("tags/"):
        return f"refs/{token}"
    return sha(repo, token) if token in REVS else token


def _push(repo, *lines, env=None):
    stdin = "".join(" ".join(_expand(repo, t) for t in line.split()) + "\n" for line in lines)
    return run_runner(repo, "origin", str(repo.parent / "remote.git"), env=env, script="tools/pre-push.sh", input=stdin)


def _git_push(repo, *args):
    return run_runner(repo, 'git push -q origin "$@"', "git-push", *args, script="-c")


def test_dev_setup_runs_uv_sync_in_a_scratch_repo(tmp_path):
    git_repo(repo := runner_repo(tmp_path, extra=("setup/dev-setup.sh", "tools/lib.sh")), {})
    assert _run_tool(repo, "setup/dev-setup.sh").returncode == 0 and "sync" in calls(repo)


SHAPES = {"two refs": ["main H main B", "other O other Z"], "tag destination": ["main H tags/v1 Z"],
          "other destination": ["main H other Z"], "rewind": ["main H main D"], "other branch": ["other O other Z"],
          "tag": ["tags/v1 H tags/v1 Z"], "staged edit": ["main H main B"], "unstaged edit": ["main H main B"],
          "cancelled edit": ["main H main B"], "HEAD to other": ["HEAD H other Z"],
          "tag destination edit": ["main H tags/v1 Z"]}                # dirty wins over any shape


@pytest.mark.parametrize("shape", SHAPES)
def test_pre_push_refuses_every_other_shape_before_any_check(tmp_path, shape):
    result = _push(repo := _push_repo(tmp_path, shape=shape), *SHAPES[shape])
    message = DIRTY if shape.endswith(" edit") else REFUSED              # make test checks the worktree, not HEAD
    assert result.returncode == 1 and result.stderr.strip() == message and calls(repo) == []


@pytest.mark.parametrize("local", ["main", "HEAD"])                    # `git push origin HEAD` sends the word HEAD
def test_pre_push_selects_a_fast_forward_and_runs_everything_for_a_new_branch(tmp_path, local):
    repo = _push_repo(tmp_path)
    assert _push(repo, f"{local} H main B").returncode == 0 and calls(repo) == ["validate", f"changed --stage push from={sha(repo, 'B')} to={sha(repo, 'H')}"]
    (repo / "calls").write_text("")
    assert _push(repo, "main H main Z").returncode == 0 and calls(repo) == ["validate", "full"]


def test_pre_push_passes_a_lone_deletion_and_stops_at_a_failed_validate(tmp_path):
    repo = _push_repo(tmp_path)
    assert _push(repo, f"(delete) {Z} refs/heads/gone {sha(repo, 'B')}").returncode == 0 and calls(repo) == []
    assert _push(repo, "main H main B", env={"STUB_VALIDATE_RC": "1"}).returncode == 1 and calls(repo) == ["validate"]


def test_pre_push_passes_a_lone_deletion_even_with_a_dirty_tree(tmp_path):
    repo = _push_repo(tmp_path, shape="staged edit")             # intentional (plan T8 item 1): a deletion tests nothing
    assert _push(repo, f"(delete) {Z} refs/heads/gone {sha(repo, 'B')}").returncode == 0 and calls(repo) == []


@pytest.mark.parametrize("ref", ["main", "HEAD"])
def test_git_runs_the_linked_hook_only_while_it_is_executable(tmp_path, ref):        # a real push
    assert "tools/pre-push.sh" in executables(ROOT)                                  # tracked as 100755
    repo = _push_repo(tmp_path); (repo / ".git/hooks/pre-push").symlink_to(repo / "tools/pre-push.sh")
    assert _git_push(repo, ref).returncode == 0 and calls(repo)[0] == "validate"
    (repo / "calls").write_text(""); (repo / "tools/pre-push.sh").chmod(0o644)
    git(repo, "commit", "-q", "--allow-empty", "-m", "N")
    assert _git_push(repo, ref).returncode == 0 and calls(repo) == []                 # git skips a non-executable hook


@pytest.mark.parametrize("hooks_path", [None, "elsewhere"])
def test_dev_setup_links_pre_push_unless_core_hooks_path_is_set(tmp_path, hooks_path):
    repo = _push_repo(tmp_path); hook = repo / ".git/hooks/pre-push"; hook.write_text("old\n")
    if hooks_path:
        git(repo, "config", "core.hooksPath", hooks_path)
    result = _run_tool(repo, "setup/dev-setup.sh")
    if hooks_path:
        assert result.returncode == 1 and "core.hooksPath" in result.stderr and hook.read_text() == "old\n"
    else:
        assert result.returncode == 0 and hook.resolve() == (repo / "tools/pre-push.sh").resolve()
        assert "install --hook-type pre-commit" in calls(repo)


def _hook_repo(tmp_path, files):
    repo = runner_repo(tmp_path, extra=("tools/test-changed.sh", "tools/pyright-staged.sh", "tools/pre-push.sh"))
    (repo / ".git/info").mkdir(parents=True)
    (repo / ".git/info/exclude").write_text("stub/\npodman-state\ncalls\ntmp/\n")
    git_repo(repo, {})
    git(repo, "read-tree", "--empty")
    git(repo, "commit", "-q", "--allow-empty", "-m", "E")
    for rel, data in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_bytes(data)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "F")
    (repo / ".git/hooks/pre-push").symlink_to(repo / "tools/pre-push.sh")
    return repo


def _refs(repo):
    return {"PUSH_FROM": git_out(repo, "rev-parse", "HEAD~1"), "PUSH_TO": git_out(repo, "rev-parse", "HEAD")}


def _run_changed(repo, stage, select=None, env=None):
    env = (env or {}) | ({"STUB_SELECT": json.dumps(select)} if select else {})
    return run_runner(repo, "--stage", stage, env=env, script="tools/test-changed.sh")


CFG = yaml.safe_load((ROOT / ".pre-commit-config.yaml").read_text())["repos"][0]["hooks"]


def test_pre_commit_holds_only_commit_stage_hooks_and_the_index_readers_always_run():
    assert [h["id"] for h in CFG] == ["shellcheck", "ruff", "pyright", "tests-changed"]
    assert all(h["stages"] == ["pre-commit"] for h in CFG) and not any("tools/test.sh" in h["entry"] for h in CFG)
    assert all(h["always_run"] and h["pass_filenames"] is False for h in CFG[2:])


def test_shellcheck_and_ruff_cover_exactly_what_validate_covers():
    globs = ("tools/*.sh", "setup/*.sh", "setup/lib/*.sh", "scripts/*.sh", ".claude/skills/*/scripts/*.sh")
    sc, rf = (re.compile(next(h["files"] for h in CFG if h["id"] == i)) for i in ("shellcheck", "ruff"))
    assert rf.search("pylib/x.py") and not rf.search("scripts/system-one-cache-check.py")   # D7
    for p in load_tree(ROOT):
        assert bool(sc.search(p)) == any(fnmatch.fnmatchcase(p, g) and p.count("/") == g.count("/") for g in globs), p
        assert bool(rf.search(p)) == (p == "llmenv.py" or p.startswith(("pylib/", "tests/")) and p.endswith(".py")), p


@pytest.mark.parametrize("path", ["pylib/a ñ b.py", "pyproject.toml", "uv.lock"])        # git quotes the first without -z
@pytest.mark.parametrize("how", ["rm", "mv"])
def test_pyright_runs_on_a_staged_deletion_or_rename_away(tmp_path, path, how):     # D7
    repo = _hook_repo(tmp_path, {path: b"x\n", "README.md": b"r\n"})
    git(repo, *(("rm", "-q", path) if how == "rm" else ("mv", path, path + ".moved")))
    assert _run_tool(repo, "tools/pyright-staged.sh").returncode == 0 and calls(repo)[-1] == "run pyright"


def test_pyright_skips_a_commit_without_python_inputs(tmp_path):
    git(repo := _hook_repo(tmp_path, {"pylib/a.py": b"x\n", "README.md": b"r\n"}), "rm", "-q", "README.md")
    assert _run_tool(repo, "tools/pyright-staged.sh").returncode == 0 and calls(repo) == []


def test_test_changed_deselects_slow_only_at_commit(tmp_path):
    repo, full = _hook_repo(tmp_path, {}), {"full": True, "tests": [], "total": 2, "reasons": {}}
    _run_changed(repo, "commit"); _run_changed(repo, "commit", select=full); _run_changed(repo, "push", select=full, env=_refs(repo))
    runs = [c for c in calls(repo) if " pytest " in c]
    assert runs[0].endswith("-m not slow tests/test_a.py") and runs[1].endswith("-m not slow tests/")
    assert runs[2].endswith("-q tests/") and "not slow" not in runs[2]


def test_test_changed_fails_when_nothing_is_collected(tmp_path):                    # spec R10
    assert _run_changed(_hook_repo(tmp_path, {}), "commit", env={"STUB_RC": "5"}).returncode == 5


@pytest.mark.parametrize(("tests_rc", "rc"), [("0", 1), ("5", 5)])
def test_a_failed_lint_fails_the_hook_without_gating_the_tests(tmp_path, tests_rc, rc):         # spec G1
    repo = _hook_repo(tmp_path, {})
    assert _run_changed(repo, "commit", env={"STUB_LINT_RC": "1", "STUB_RC": tests_rc}).returncode == rc
    assert any(" pytest " in c for c in calls(repo))                               # the lint runs beside the tests


@pytest.mark.parametrize(("script", "sig", "rc"), [("tools/test-changed.sh", signal.SIGTERM, 143),
                                                   ("tools/test-changed.sh", signal.SIGINT, 130),
                                                   ("tools/pre-push.sh", signal.SIGTERM, 143)])   # git's hook process
def test_a_signal_to_the_hook_stops_the_runner_and_every_descendant(tmp_path, script, sig, rc):
    repo = _hook_repo(tmp_path, {"tools/validate.sh": b"exit 0\n"})
    (refs := tmp_path / "refs").write_text(f"refs/heads/main {git_out(repo, 'rev-parse', 'HEAD')} refs/heads/main "
                                           f"{git_out(repo, 'rev-parse', 'HEAD~1')}\n")
    args = ("--stage", "commit") if script == "tools/test-changed.sh" else ("origin", "url")
    with refs.open() as stdin:
        proc = start_runner(repo, *args, env={"STUB_SLEEP": "1"}, script=script, stdin=stdin)
    tree: dict[int, bytes] = {}
    try:
        wait_until(lambda: b"sleep\x00300\x00" in descendants(proc.pid).values(), timeout=30)
        tree = descendants(proc.pid)
        proc.send_signal(sig)                                                       # the hook's PID only
        assert proc.wait(timeout=15) == rc and "term" in calls(repo)
    finally:
        stop_runner(proc, tree)
    wait_until(lambda: not any(Path(f"/proc/{p}").exists() for p in tree), timeout=10)


@pytest.mark.parametrize("stage", ["commit", "push"])
@pytest.mark.parametrize(("removed", "problem"), [("docs/a.md", "stale exemption: docs/a.md"),
                                                  ("tests/test_x.py", "unowned: scripts/x.sh")])
@pytest.mark.parametrize("tests_rc", ["0", "1"])                                   # the lint is reported either way
def test_hooks_fail_when_an_exemption_loses_its_last_match_or_a_source_its_last_owner(tmp_path, stage, removed, problem, tests_rc):
    repo = _hook_repo(tmp_path, {"docs/a.md": b"", "scripts/x.sh": b"", "tests/test_x.py": b"'x.sh'\n", NP: b"",
                                 "tests/test_tools.py": b"'pyright-staged.sh'\n",          # owns the copied tool
                                 "tests/selection-exempt.txt": b"docs/a.md  # why\n"})
    real = {"STUB_REAL_LINT": str(ROOT / "llmenv.py")}
    assert _run_changed(repo, "commit", env=real).returncode == 0                  # control: clean fixture ...
    assert any(" pytest " in c for c in calls(repo))                               # ... that reached pytest
    (repo / "calls").write_text("")                                                # measure only the rejected run
    git(repo, "rm", "-q", removed)
    if stage == "push":
        git(repo, "commit", "-qm", "rm")
    result = _run_changed(repo, stage, env={**real, "STUB_RC": tests_rc, **(_refs(repo) if stage == "push" else {})})
    assert result.returncode == 1 and problem in result.stderr


@pytest.mark.parametrize("broken", ["missing", "not executable", "foreign"])
def test_commit_hook_requires_the_pre_push_link(tmp_path, broken):
    repo = _hook_repo(tmp_path, {}); hook = repo / ".git/hooks/pre-push"
    if broken == "not executable":
        (repo / "tools/pre-push.sh").chmod(0o644)
    else:
        hook.unlink()
    if broken == "foreign":                                       # a leftover hook that passes every push (D8)
        hook.write_text("#!/bin/sh\nexit 0\n"); hook.chmod(0o755)
    result = _run_changed(repo, "commit")
    assert result.returncode == 1 and "run make dev-setup" in result.stderr and calls(repo) == []


def test_slow_marker_is_registered_and_the_known_32s_test_carries_it():
    assert any(m.startswith("slow:") for m in tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["pytest"]["ini_options"]["markers"])
    fn = next(n for n in ast.walk(ast.parse((ROOT / "tests/test_gguf.py").read_text()))
              if isinstance(n, ast.FunctionDef) and n.name == "test_large_string_array_is_summarized_not_materialized")
    assert "pytest.mark.slow" in {ast.unparse(d) for d in fn.decorator_list}


def _classify(tmp_path, rc, out, err):
    (tmp_path / "out").write_text(out); (tmp_path / "err").write_text(err)
    return subprocess.run(["bash", "tools/selection-mutation-check.sh", "--classify", str(rc), str(tmp_path / "out"),
                           str(tmp_path / "err")], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()


def test_every_mutation_fault_matches_exactly_one_line():                               # spec G2 integrity test
    out = subprocess.run(["bash", "tools/selection-mutation-check.sh", "--list"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    rows = [line.split("\t") for line in out.splitlines()]
    assert len(rows) == 10 and len({r[3] for r in rows}) == 10 and rows[0][3] == "pylib-cli"
    assert all((ROOT / f).read_text().count(search) == 1 for f, search, _r, _k in rows)


@pytest.mark.parametrize(("rc", "out", "err", "verdict"), [
    (1, "1 failed, 3 passed in 1s", "", "caught"), (0, "4 passed in 1s", "", "passed"),
    (1, "1 failed, 3 passed in 1s", "leak: tmp/x\n", "HARNESS"), (1, "1 failed, 1 error in 1s", "", "HARNESS"),
    (1, "1 failed, 3 passed in 1s", "inventory: podman unavailable\n", "HARNESS"),   # a failed post-run inventory
    (1, "[gw0] node down: Not properly terminated\n2 failed in 1s", "", "HARNESS"),     # real xdist crash output, 2026-11-23
    (1, "1 failed, 3 passed in 1s", "timeout: test run exceeded 1800 s\n", "HARNESS"),  # the runner's overall bound
    (3, "", "", "HARNESS"), (69, "", "network namespace unavailable\n", "HARNESS")])
def test_mutation_verdict_credits_only_a_clean_assertion_failure(tmp_path, rc, out, err, verdict):
    assert _classify(tmp_path, rc, out + "\n", err) == verdict


@pytest.mark.skipif(os.environ.get("LLM_ENV_MUTATION_CHECK") != "1", reason="opt-in: LLM_ENV_MUTATION_CHECK=1 (runs the full suite)")
def test_mutation_check_finds_a_hole_without_the_cli_edge():
    result = subprocess.run(["bash", "tools/selection-mutation-check.sh", "--only", "1", "--break-cli-edge"],
                            cwd=ROOT, capture_output=True, text=True, check=False)
    assert result.returncode == 1 and "HOLE 1 pylib-cli" in result.stdout
