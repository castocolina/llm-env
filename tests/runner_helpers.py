"""Harness for tests of tools/test.sh: a scratch repo with stub nproc, uv and podman on PATH."""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OPT_IN_SWITCHES = ["LLM_ENV_UPDATE_PROOF", "LLM_ENV_UPDATE_CHARACTERIZE", "LLM_ENV_LIVE_VM"]
SYSTEM_PATH = ("/usr/bin", "/bin", "/usr/sbin")

STUB_NPROC = "#!/bin/sh\necho 24\n"
STUB_UV = f"""#!/usr/bin/env bash
if [ "$1 $2" = "run pytest" ]; then
  echo "niceness=$(nice)" >>"$CALLS"
  set_=(); for v in {" ".join(OPT_IN_SWITCHES)}; do [[ -v $v ]] && set_+=("$v"); done
  echo "optin=${{set_[*]}}" >>"$CALLS"
  echo "$*" >>"$CALLS"
  exit "${{STUB_RC:-0}}"
fi
echo "$*" >>"$CALLS"
"""
STUB_PODMAN = """#!/usr/bin/env bash
case "$*" in
  "ps -a -q --no-trunc") class=container ;;
  "volume ls -q") class=volume ;;
  "images -q --no-trunc") class=image ;;
  *) exit 0 ;;
esac
while read -r c id; do [ "$c" = "$class" ] && echo "$id"; done <"${PODMAN_STATE:-$PWD/podman-state}"
exit 0
"""


def runner_repo(tmp_path: Path, extra=()) -> Path:
    repo = tmp_path / "repo"
    for rel in ("tools/test.sh", *extra):
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / rel, repo / rel)
    (repo / "tmp/keep").mkdir(parents=True)
    (repo / "tmp/keep/old.log").write_text("old\n")
    (repo / "podman-state").write_text("")
    (stub := repo / "stub").mkdir(mode=0o755)
    for name, body in (("nproc", STUB_NPROC), ("uv", STUB_UV), ("podman", STUB_PODMAN)):
        (stub / name).write_text(body)
        (stub / name).chmod(0o755)
    return repo


def _path_without_podman(repo: Path) -> str:
    """One dir of symlinks to the stubs and system executables, minus podman."""
    links = repo.parent / "path-without-podman"
    if not links.is_dir():
        links.mkdir()
        for d in (repo / "stub", *map(Path, SYSTEM_PATH)):
            for exe in d.iterdir():
                if exe.name != "podman" and not (links / exe.name).exists() and os.access(exe, os.X_OK):
                    (links / exe.name).symlink_to(exe)
    return str(links)


def _env(repo: Path, env, podman: bool) -> dict:
    base = {k: v for k, v in os.environ.items() if k != "LLM_ENV_TEST_CPU_PCT"}  # the default case means unset
    path = f"{repo / 'stub'}:{':'.join(SYSTEM_PATH)}" if podman else _path_without_podman(repo)
    return base | (env or {}) | {"PATH": path, "CALLS": str(repo / "calls"), "REAL_UV": shutil.which("uv") or "",
                                 "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}


def run_runner(repo: Path, *args, env=None, prefix=(), podman=True, script="tools/test.sh", input=None):
    return subprocess.run([*prefix, "bash", script, *args], cwd=repo, env=_env(repo, env, podman),
                          capture_output=True, text=True, input=input, timeout=120, check=False)


def start_runner(repo: Path, env=None) -> subprocess.Popen:
    return subprocess.Popen(["bash", "tools/test.sh"], cwd=repo, env=_env(repo, env, True),
                            start_new_session=True, stderr=subprocess.PIPE)


def wait_until(pred, timeout=10) -> None:
    deadline = time.monotonic() + timeout
    while not pred():
        if time.monotonic() > deadline:
            raise AssertionError(f"condition not met within {timeout} s")
        time.sleep(0.05)


def calls(repo: Path) -> list[str]:
    path = repo / "calls"
    return path.read_text().splitlines() if path.exists() else []


def descendants(pid: int) -> dict[int, bytes]:
    children: dict[int, list[int]] = {}
    for stat in Path("/proc").glob("[0-9]*/stat"):
        try:
            ppid = int(stat.read_text().rsplit(")", 1)[1].split()[1])
        except (OSError, IndexError, ValueError):
            continue  # vanished
        children.setdefault(ppid, []).append(int(stat.parent.name))
    found: dict[int, bytes] = {}
    todo = list(children.get(pid, []))
    while todo:
        child = todo.pop()
        try:
            found[child] = Path(f"/proc/{child}/cmdline").read_bytes()
        except OSError:
            continue
        todo += children.get(child, [])
    return found
