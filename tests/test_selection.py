"""pylib/selection.py: which test files a change selects (spec D1, D3, G1)."""
import gzip
import sys
from pathlib import Path

import pytest
from git_helpers import git_repo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pylib.selection import (
    ALWAYS,
    executables,
    is_test_file,
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
