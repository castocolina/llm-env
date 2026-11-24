"""The suite never inherits the GIT_* variables git exports to its hooks (the hooks run selected tests; `make test` runs the whole suite)."""

import os

import pytest

LOCAL_GIT_VARIABLES = (
    "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_COMMON_DIR", "GIT_PREFIX", "GIT_IMPLICIT_WORK_TREE", "GIT_NO_REPLACE_OBJECTS", "GIT_CONFIG",
)


@pytest.mark.parametrize("name", LOCAL_GIT_VARIABLES)
def test_repository_local_git_variables_are_scrubbed(name: str) -> None:
    # With them set, a scratch `git init` + `git commit` in a test's tmp dir writes into THIS repository: a run started
    # with GIT_DIR exported once added dozens of "baseline" commits to main. Canary for tests/conftest.py.
    assert name not in os.environ


@pytest.mark.parametrize("name", ("PUSH_FROM", "PUSH_TO", "SELECT_ALL"))
def test_selector_switches_are_scrubbed(name: str) -> None:
    # The pre-push hook and an owner bypass run the suite with these set; select-tests tests pass them explicitly.
    assert name not in os.environ
