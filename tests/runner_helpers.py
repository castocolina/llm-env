"""Harness for tests of tools/test.sh: a scratch repo with stub nproc, uv, unshare and podman on PATH."""
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
  mkdir "$PYTEST_DEBUG_TEMPROOT/pytest-of-$USER"
  echo "addopts=${{PYTEST_ADDOPTS-unset}}" >>"$CALLS"
  echo "temproot=$PYTEST_DEBUG_TEMPROOT" >>"$CALLS"
  [ -z "${{STUB_TOUCH:-}}" ] || {{ mkdir -p "$(dirname "$STUB_TOUCH")" && touch "$STUB_TOUCH"; }}
  [ -z "${{STUB_MKDIR_TMPDIR:-}}" ] || mkdir "$TMPDIR/$STUB_MKDIR_TMPDIR"
  [ -z "${{STUB_NEW_PODMAN:-}}" ] || echo "$STUB_NEW_PODMAN" >>"$PODMAN_STATE"
  if [ -n "${{STUB_SLEEP:-}}" ]; then
    trap 'sleep 1; echo term >>"$CALLS"; exit 143' TERM  # before the sleeper: the test signals once it sees it
    setsid sleep 300 &
    echo "pid=$$" >>"$CALLS"
    sleep 300 & wait
  fi
  echo "niceness=$(nice)" >>"$CALLS"
  set_=(); for v in {" ".join(OPT_IN_SWITCHES)}; do [[ -v $v ]] && set_+=("$v"); done
  echo "optin=${{set_[*]}}" >>"$CALLS"
  echo "net=$(ip -o link show | awk -F': ' '{{printf "%s%s", s, $2; s=","}}')" >>"$CALLS"
  echo "uid=$(id -u)" >>"$CALLS"
  echo "uv_offline=$UV_OFFLINE" >>"$CALLS"
  echo "$*" >>"$CALLS"
  exit "${{STUB_RC:-0}}"
fi
if [ "$1 $2" = "run --offline" ]; then
  [ -n "${{STUB_REAL_OFFLINE:-}}" ] && exec "$REAL_UV" "$@"
  echo "offline-check" >>"$CALLS"
  exit "${{STUB_OFFLINE_RC:-0}}"
fi
echo "$*" >>"$CALLS"
"""
STUB_UNSHARE = """#!/usr/bin/env bash
[ -n "${STUB_UNSHARE_FAIL:-}" ] && exit 1
exec /usr/bin/unshare "$@"
"""
STUB_PODMAN = """#!/usr/bin/env bash
[ -n "${STUB_PODMAN_FAIL:-}" ] && exit 125
case "$*" in
  "ps -a -q --no-trunc") class=container ;;
  "volume ls -q") class=volume ;;
  "images -q --no-trunc") class=image ;;
  *) exit 0 ;;
esac
while read -r c id; do [ "$c" = "$class" ] && echo "$id"; done <"$PODMAN_STATE"
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
    for name, body in (("nproc", STUB_NPROC), ("uv", STUB_UV), ("unshare", STUB_UNSHARE), ("podman", STUB_PODMAN)):
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
    # the default CPU case means unset; UV_OFFLINE must come from the runner, not from conftest
    base = {k: v for k, v in os.environ.items() if k not in ("LLM_ENV_TEST_CPU_PCT", "UV_OFFLINE")}
    path = f"{repo / 'stub'}:{':'.join(SYSTEM_PATH)}" if podman else _path_without_podman(repo)
    return base | (env or {}) | {"PATH": path, "CALLS": str(repo / "calls"),
                                 "PODMAN_STATE": str(repo / "podman-state"), "REAL_UV": shutil.which("uv") or "",
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


def run_dir(stderr: str) -> Path:
    """The run's own TMPDIR, from the runner's `run dir: <path>` line."""
    return Path(next(line for line in stderr.splitlines() if line.startswith("run dir: "))[len("run dir: "):])


def calls(repo: Path) -> list[str]:
    path = repo / "calls"
    return path.read_text().splitlines() if path.exists() else []


def descendants(pid: int) -> dict[int, bytes]:
    children: dict[int, list[int]] = {}
    for stat in Path("/proc").glob("[0-9]*/stat"):
        try:
            ppid = int(stat.read_bytes().rsplit(b")", 1)[1].split()[1])  # bytes: comm need not be UTF-8
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
