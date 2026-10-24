"""Shared pytest fixtures for the lifecycle-script tests."""

import os
from pathlib import Path

import pytest


@pytest.fixture(autouse=True, scope="session")
def _no_stale_pwd() -> None:
    """Drop PWD from the environment before any subprocess test runs.

    On Fedora Atomic/Bazzite hosts /home is a symlink to /var/home, so the
    test runner's own shell can have $PWD set to the symlinked form even
    though this file's canonical path (ROOT = Path(__file__).resolve()) is
    the real /var/home/... one. Tests spawn child scripts with
    subprocess.run(..., cwd=ROOT, env=os.environ | {...}): the OS chdir()s
    the child into the real directory, but bash trusts an inherited PWD
    that still refers to the same directory and keeps its stale string
    instead of recomputing it -- so a script's own REPO_DIR (computed via
    `cd ... && pwd`) can land on the symlinked path while the test's
    expected string uses the canonical one, failing a path-equality
    assertion for a reason that has nothing to do with the script under
    test. Unsetting PWD once, before any test builds its environment,
    makes every child bash recompute it fresh from the real post-chdir cwd.
    """
    os.environ.pop("PWD", None)



@pytest.fixture(autouse=True, scope="session")
def _shared_uv_cache() -> None:
    """Point every child `uv run` at one shared cache.

    Shell tests set HOME to a per-test tmp dir; without this, uv would fill a
    fresh cache (downloading pyyaml) under each test's /tmp basetemp, which
    exhausted the 16 GiB /tmp tmpfs. Children inherit os.environ, so one
    setdefault covers them; the user's own cache is the one the other
    subprocess tests already use.
    """
    os.environ.setdefault("UV_CACHE_DIR", str(Path.home() / ".cache" / "uv"))
