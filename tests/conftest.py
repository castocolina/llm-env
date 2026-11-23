"""Shared pytest fixtures for the lifecycle-script tests."""

import os
from pathlib import Path

import pytest
from observe_state_guard import REAL_LLM_ENV_STATE

# No test downloads anything: uv resolves from its cache, Hugging Face clients stay offline; children inherit both.
os.environ["UV_OFFLINE"] = "1"
os.environ["HF_HUB_OFFLINE"] = "1"


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
def _no_git_hook_environment() -> None:
    """Drop the repository-local GIT_* variables git exports to its hooks.

    The pre-push hook runs this suite with GIT_DIR (and friends) pointing at
    this repository. Tests that `git init` a scratch directory, or scripts
    that ask git whether a directory is a work tree, then talk to THIS
    repository instead of the scratch one, and fail only when the suite is
    started by `git push`. Every test starts from a clean git environment.
    The selector switches go too: the pre-push hook sets PUSH_FROM/PUSH_TO and
    an owner bypass sets SELECT_ALL; select-tests tests pass them explicitly.
    """
    for name in ("GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_CONFIG", "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT",
                 "GIT_OBJECT_DIRECTORY", "GIT_DIR", "GIT_WORK_TREE", "GIT_IMPLICIT_WORK_TREE", "GIT_GRAFT_FILE",
                 "GIT_INDEX_FILE", "GIT_NO_REPLACE_OBJECTS", "GIT_REPLACE_REF_BASE", "GIT_PREFIX", "GIT_SHALLOW_FILE",
                 "GIT_COMMON_DIR", "PUSH_FROM", "PUSH_TO", "SELECT_ALL"):
        os.environ.pop(name, None)



@pytest.fixture(autouse=True)
def _isolate_observe_state(request, tmp_path, monkeypatch):
    if not request.module.__name__.rsplit(".", 1)[-1].startswith("test_observe_"):
        yield
        return
    state_home = tmp_path / "state-home"
    monkeypatch.setenv("XDG_STATE_HOME", str(state_home))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    original_open = Path.open

    def guarded_open(path, *args, **kwargs):
        candidate = path.expanduser()
        if candidate.resolve().is_relative_to(REAL_LLM_ENV_STATE):
            raise AssertionError(f"test opened real state: {path}")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)
    yield


@pytest.fixture(autouse=True, scope="session")
def _no_tracked_profile_or_repo_env(tmp_path_factory) -> None:
    """Keep script tests off the committed llm-env.profile.yml and the checkout's .env.

    Scripts run from ROOT would otherwise apply the owner's real profile (setup, check-setup,
    network, enable-boot, start) and create or read the real <repo>/.env master key. Tests that
    exercise the profile or .env set LLM_ENV_PROFILE / LLM_ENV_ENV_FILE themselves.
    """
    scratch = tmp_path_factory.mktemp("no-profile")
    os.environ.setdefault("LLM_ENV_PROFILE", str(scratch / "absent.profile.yml"))
    os.environ.setdefault("LLM_ENV_ENV_FILE", str(scratch / "env"))


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
