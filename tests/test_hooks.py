"""The pre-push hook (tools/pre-push.sh) and its link by make dev-setup (spec R3, D8); scratch repos only."""
import sys
from pathlib import Path

import pytest
from git_helpers import git, git_out, git_repo
from runner_helpers import calls, run_runner, runner_repo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pylib.selection import executables

Z = "0" * 40
REFUSED = "push shape not supported by the hook: run make test, then git push --no-verify"
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
    if shape in ("staged edit", "unstaged edit", "cancelled edit"):
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
          "cancelled edit": ["main H main B"]}


@pytest.mark.parametrize("shape", SHAPES)
def test_pre_push_refuses_every_other_shape_before_any_check(tmp_path, shape):
    result = _push(repo := _push_repo(tmp_path, shape=shape), *SHAPES[shape])
    assert result.returncode == 1 and result.stderr.strip() == REFUSED and calls(repo) == []


def test_pre_push_selects_a_fast_forward_and_runs_everything_for_a_new_branch(tmp_path):
    repo = _push_repo(tmp_path)
    assert _push(repo, "main H main B").returncode == 0 and calls(repo) == ["validate", f"changed --stage push from={sha(repo, 'B')} to={sha(repo, 'H')}"]
    (repo / "calls").write_text("")
    assert _push(repo, "main H main Z").returncode == 0 and calls(repo) == ["validate", "full"]


def test_pre_push_passes_a_lone_deletion_and_stops_at_a_failed_validate(tmp_path):
    repo = _push_repo(tmp_path)
    assert _push(repo, f"(delete) {Z} refs/heads/gone {sha(repo, 'B')}").returncode == 0 and calls(repo) == []
    assert _push(repo, "main H main B", env={"STUB_VALIDATE_RC": "1"}).returncode == 1 and calls(repo) == ["validate"]


def test_git_runs_the_linked_hook_only_while_it_is_executable(tmp_path):             # a real push
    assert "tools/pre-push.sh" in executables(ROOT)                                  # tracked as 100755
    repo = _push_repo(tmp_path); (repo / ".git/hooks/pre-push").symlink_to(repo / "tools/pre-push.sh")
    assert _git_push(repo, "main").returncode == 0 and calls(repo)[0] == "validate"
    (repo / "calls").write_text(""); (repo / "tools/pre-push.sh").chmod(0o644)
    git(repo, "commit", "-q", "--allow-empty", "-m", "N")
    assert _git_push(repo, "main").returncode == 0 and calls(repo) == []              # git skips a non-executable hook


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
