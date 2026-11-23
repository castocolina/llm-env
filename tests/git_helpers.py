"""Scratch git repositories for tests: the scrubbed environment (tests/conftest.py) plus no user or system config."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path


def _env() -> dict[str, str]:
    return os.environ | {"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}


def git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=repo, env=_env(), check=True, capture_output=True, text=True)


def git_out(repo: Path, *args: str) -> str:
    return git(repo, *args).stdout.removesuffix("\n")


def git_repo(root: Path, files: dict[str, bytes], commit: bool = False) -> Path:
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.name", "Test")
    git(root, "config", "user.email", "test@example.invalid")
    for rel, data in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(data)
    git(root, "add", "-A")
    if commit:
        git(root, "commit", "-q", "-m", "base")
    return root
