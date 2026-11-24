"""pylib/selection.py: which test files a change selects (spec D1, D3, G1)."""
import gzip
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from git_helpers import git, git_out, git_repo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pylib.selection import (
    ALWAYS,
    cli_edges,
    executables,
    is_test_file,
    lint,
    load_tree,
    read_exempt,
    select,
)

NP = ALWAYS[0]
TREE = {"scripts/a.sh": 'bash "$R/scripts/b.sh"\n', "scripts/b.sh": "", "scripts/c.sh": "bash scripts/a.sh\n",
        "scripts/orphan.sh": "", "pylib/m.py": "X = 1\n", "pylib/n.py": "from pylib.m import X\n",
        "docs/guide.md": "see scripts/b.sh\n", "docs/notes/old.md": "", "tools/lib.sh": "", "tests/conftest.py": "",
        "tests/test_a.py": "S = ROOT / 'scripts' / 'a.sh'\n", "tests/test_b.py": "'b.sh'\n", "tests/test_c.py": "'scripts/c.sh'\n",
        "tests/test_n.py": "from pylib import other, n\n", "tests/test_guide.py": "'docs/guide.md'\n",
        "tests/test_lib.py": "'tools/lib.sh'\n", "tests/test_env.py": "'tests/conftest.py'\n",
        "tests/sub/conftest.py": "", "tests/sub/test_s.py": "",
        NP: "", "tests/test_omniroute_update_proof.py": "'scripts/orphan.sh'\n"}


def S(ch, st, ex=None, de=()):
    return select(TREE, ch, de, st, ex or {})


def test_depth_one_at_commit_and_two_at_push():
    assert S(["scripts/b.sh"], "commit").tests == ("tests/test_a.py", "tests/test_b.py", NP)   # markdown is never a referrer
    assert S(["scripts/b.sh"], "push").tests == ("tests/test_a.py", "tests/test_b.py", "tests/test_c.py", NP)


def test_an_owned_file_never_falls_back_so_reach_beyond_push_depth_is_missed():    # spec D1 fixture
    chain = {"x/a.sh": "", "x/b.sh": "a.sh\n", "x/c.sh": "b.sh\n", "x/e.sh": "c.sh\n",
             "tests/test_a.py": "'a.sh'\n", "tests/test_d.py": "'c.sh'\n", "tests/test_e.py": "'e.sh'\n", NP: ""}
    sel = select(chain, ["x/a.sh"], [], "push", {})
    assert not sel.full and sel.tests == ("tests/test_a.py", "tests/test_d.py", NP)


def test_ownership_is_measured_at_commit_depth_at_both_stages():                    # spec G1: the push is never weaker
    chain = {"x/a.sh": "", "x/b.sh": "a.sh\n", "x/c.sh": "b.sh\n", "tests/test_d.py": "'c.sh'\n", NP: ""}
    assert select(chain, ["x/a.sh"], [], "push", {}).full


def test_an_opt_in_test_is_no_referrer_either():
    tree = TREE | {"tests/test_x.py": "'tests/test_omniroute_update_proof.py'\n"}
    assert select(tree, ["scripts/orphan.sh"], [], "push", {}).full


def test_imports_markdown_and_changed_tests():
    assert "tests/test_n.py" in S(["pylib/m.py"], "commit").tests
    assert S(["docs/guide.md"], "push").tests == ("tests/test_guide.py", NP)
    assert S(["tests/test_c.py"], "commit").tests == ("tests/test_c.py", NP)


def test_a_test_helper_change_selects_the_tests_that_import_it():                   # tests/test_capacity.py: from test_cli import ...
    assert "tests/test_capacity.py" in select(load_tree(ROOT), ["tests/test_cli.py"], [], "commit", {}).tests


def test_precedence_of_full_hubs_and_conftests():                                   # spec D3 rules 1-3
    assert S(["scripts/b.sh"], "commit", {"scripts/b.sh": ("no runner", True)}).full                 # owned full: file
    assert S(["tests/conftest.py"], "push").full
    assert S(["tests/conftest.py"], "commit").tests == ("tests/test_env.py", NP)                     # not every test
    assert S(["tools/lib.sh"], "commit").tests == ("tests/test_lib.py", NP)
    assert S(["tests/sub/conftest.py"], "commit").tests == ("tests/sub/test_s.py", NP)


def test_unowned_exempt_and_opt_in():
    assert S(["scripts/orphan.sh"], "commit").full                                   # only the opt-in proof names it
    assert S(["docs/notes/old.md"], "push", {"docs/notes/*": ("history", False)}).tests == (NP,)
    assert S(["docs/guide.md"], "push", {"docs/*": ("history", False)}).tests == ("tests/test_guide.py", NP)


def test_deleted_paths_select_like_changed_ones():
    tree = {k: v for k, v in TREE.items() if k != "scripts/b.sh"}
    assert select(tree, [], ["scripts/b.sh"], "commit", {}).tests == ("tests/test_a.py", "tests/test_b.py", NP)


CLI = {"llmenv.py": "from pylib.m import compute\nfrom pylib.q import other\nfrom pylib.r import register as reg_r\n\n"
                    "def _helper():\n    return compute()\n\ndef cmd_budget(args):\n    return _helper()\n\n"
                    "def cmd_render_compose(args):\n    return 0\n\n"
                    "def cmd_capacity(args):\n    from pylib import cap, cap_report as cr\n    return cap.save(cr.page())\n\n"
                    "def cmd_interference(args):\n    from pylib.inter import run\n    return run()\n",
       "pylib/m.py": "def compute(): ...\n", "pylib/q.py": "def other(): ...\n", "pylib/cap.py": "", "pylib/cap_report.py": "",
       "pylib/inter.py": "", "pylib/r.py": 'def register(sub):\n    sub.add_parser("omniroute-update")\n',
       "scripts/start.sh": 'out="$(llmenv --config "$c" budget --models-dir "$d")"\n',
       "scripts/pre.sh": 'llmenv_budget_json "$cfg" "$dir"\n', "scripts/other.sh": "llmenv render-compose\n",
       "tests/test_start.py": "'start.sh'\n", "tests/test_pre.py": "'pre.sh'\n", "tests/test_other.py": "'other.sh'\n",
       NP: ""}


def test_cli_edges_trace_every_llmenv_import_form():    # top-level, function-local, `from pylib import a, b as c`, aliases
    assert cli_edges(CLI) == {"pylib/m.py": frozenset({"budget"}), "pylib/q.py": frozenset({"*"}),
                              "pylib/r.py": frozenset({"omniroute-update"}), "pylib/cap.py": frozenset({"capacity"}),
                              "pylib/cap_report.py": frozenset({"capacity"}), "pylib/inter.py": frozenset({"interference"})}


def test_cli_edge_selects_script_tests_through_the_subcommand():
    assert select(CLI, ["pylib/m.py"], [], "commit", {}).tests == tuple(sorted((NP, "tests/test_pre.py", "tests/test_start.py")))
    assert select(CLI, ["pylib/q.py"], [], "push", {}).full


def test_cli_edge_follows_a_backslash_continued_call():                             # scripts/observe-publish.sh:13-14
    tree = CLI | {"scripts/cont.sh": 'uv run "$R/llmenv.py" \\\n  budget --models-dir "$d"\n', "tests/test_cont.py": "'cont.sh'\n"}
    assert "tests/test_cont.py" in select(tree, ["pylib/m.py"], [], "commit", {}).tests


def test_real_llmenv_has_no_hub_module_and_parser_names_match_handlers():
    tree = load_tree(ROOT)
    hubs = sorted(p for p, words in cli_edges(tree).items() if words == {"*"})
    assert not hubs, f"{hubs}: implement spec D2's '*' hub rule in select() before a module traces to no subcommand"
    parsers = set(re.findall(r'add_parser\(\s*"([^"]+)"', tree["llmenv.py"]))
    handlers = {h.replace("_", "-") for h in re.findall(r"^def cmd_(\w+)", tree["llmenv.py"], re.MULTILINE)}
    assert parsers == handlers   # cli_edges derives each word from its cmd_ handler name


def test_load_tree_reads_binary_files_as_empty(tmp_path):
    git_repo(tmp_path, {"f.gz": gzip.compress(b"x"), "bad.txt": b"\xff\xfe", "ok.sh": b"echo\n"})
    assert load_tree(tmp_path) == {"f.gz": "", "bad.txt": "", "ok.sh": "echo\n"}


def test_executables_and_test_files_come_from_the_index(tmp_path):
    (tmp_path / "run.sh").write_text("")
    (tmp_path / "run.sh").chmod(0o755)
    git_repo(tmp_path, {"lib.sh": b"", "tests/sub/test_x.py": b"", "tests/x_test.py": b""})
    assert executables(tmp_path) == {"run.sh"}
    assert is_test_file("tests/sub/test_x.py") and not is_test_file("tests/x_test.py") and not is_test_file("test_x.py")


def test_read_exempt_parses_full_lines_and_requires_a_reason(tmp_path):
    (tmp_path / "x").write_text("# header\ndocs/a.md  # why\nfull: x/*.mjs  # no runner\n")
    assert read_exempt(tmp_path / "x") == {"docs/a.md": ("why", False), "x/*.mjs": ("no runner", True)}
    (tmp_path / "y").write_text("docs/b.md\n")
    with pytest.raises(ValueError, match="docs/b.md"):
        read_exempt(tmp_path / "y")


@pytest.mark.slow
def test_every_tracked_file_has_an_owner_a_hub_or_an_exemption():
    assert lint(load_tree(ROOT), read_exempt(ROOT / "tests/selection-exempt.txt"), executables(ROOT)) == []


def test_lint_flags_a_lost_last_match_a_lost_last_owner_and_code_under_a_plain_line():
    tree = {"docs/a.md": "", "scripts/x.sh": "", "tests/test_x.py": "'x.sh'\n", "x/t.mjs": "", NP: ""}
    ex = {"docs/a.md": ("why", False), "x/*.mjs": ("no runner", True)}
    assert lint(tree, ex, set()) == []
    assert lint({k: v for k, v in tree.items() if k != "docs/a.md"}, ex, set()) == ["stale exemption: docs/a.md"]
    assert lint({k: v for k, v in tree.items() if k != "tests/test_x.py"}, ex, set()) == ["unowned: scripts/x.sh"]
    assert lint(tree, {**ex, "x/*.mjs": ("no runner", False)}, set()) == ["plain exemption matches code: x/*.mjs (x/t.mjs)"]
    chain = {"s/far.sh": "", "s/mid.sh": "far.sh\n", "s/top.sh": "mid.sh\n", "tests/test_top.py": "'top.sh'\n", NP: ""}
    assert lint(chain, {}, set()) == ["unowned: s/far.sh"]                          # an owner only at push depth is not enough


def _cli(repo, *args, env=None):
    env = os.environ | {"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"} | (env or {})
    return subprocess.run(["uv", "run", str(ROOT / "llmenv.py"), *args], cwd=repo, env=env, capture_output=True, text=True,
                          check=False)


def select_tests(repo, *args, env=None):
    result = _cli(repo, "select-tests", *args, env=env)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout), result.stderr


ODD = "scripts/my file ñ.sh"                                                         # git quotes it without -z
BASE = {"scripts/c.sh": b"", ODD: b"", "tests/test_c.py": b"'c.sh'\n", "tests/test_d.py": b"'d.sh'\n",
        "tests/test_odd.py": "'my file ñ.sh'\n".encode(), NP: b""}


@pytest.mark.parametrize("change", ["edit", "rm", "mv", "rm odd"])
def test_select_tests_commit_reads_every_staged_change_from_git(tmp_path, change):
    repo = git_repo(tmp_path, BASE, commit=True)
    if change == "edit":
        (repo / "scripts/c.sh").write_text("x\n")
        git(repo, "add", "scripts/c.sh")
    else:
        git(repo, *{"rm": ("rm", "-q", "scripts/c.sh"), "mv": ("mv", "scripts/c.sh", "scripts/d.sh"),
                    "rm odd": ("rm", "-q", ODD)}[change])
    want = {NP, "tests/test_odd.py"} if change == "rm odd" else {"tests/test_c.py", NP} | ({"tests/test_d.py"} if change == "mv" else set())
    out, err = select_tests(repo, "--stage", "commit")
    assert set(out["tests"]) == want and err.startswith(f"selected {len(want)} of 4 test files")


def test_select_tests_push_diffs_the_range(tmp_path):
    repo = git_repo(tmp_path, BASE, commit=True)
    base = git_out(repo, "rev-parse", "HEAD")
    git(repo, "rm", "-q", "scripts/c.sh")
    git(repo, "commit", "-qm", "rm")
    out, _ = select_tests(repo, "--stage", "push", env={"PUSH_FROM": base, "PUSH_TO": git_out(repo, "rev-parse", "HEAD")})
    assert out["tests"] == ["tests/test_c.py", NP] and not out["full"]


def test_select_tests_takes_no_paths_and_needs_the_push_range(tmp_path):
    repo = git_repo(tmp_path, BASE, commit=True)
    assert _cli(repo, "select-tests", "--stage", "commit", "scripts/c.sh").returncode == 2
    result = _cli(repo, "select-tests", "--stage", "push")
    assert result.returncode == 2 and "push stage needs PUSH_FROM and PUSH_TO" in result.stderr


def test_select_all_forces_full(tmp_path):
    assert select_tests(git_repo(tmp_path, BASE, commit=True), "--stage", "commit", env={"SELECT_ALL": "1"})[0]["full"]
