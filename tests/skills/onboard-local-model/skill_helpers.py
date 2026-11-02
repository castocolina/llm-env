"""Shared helpers for the onboard-local-model skill tests (imported by bare name, like tests/omniroute_fakes.py)."""

import os
import shutil
import signal
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SKILL = ROOT / ".claude" / "skills" / "onboard-local-model"
SCRIPTS = SKILL / "scripts"


def _real_node() -> str | None:
    """The real node binary: `command -v node` may be a Volta shim that fails under a changed HOME, so ask it for process.execPath."""
    shim = shutil.which("node")
    if shim is None:
        return None
    probe = subprocess.run([shim, "-p", "process.execPath"], capture_output=True, text=True, check=False)
    path = probe.stdout.strip()
    return path if probe.returncode == 0 and path and Path(path).is_file() else None


NODE = _real_node()
NODE_DIR = str(Path(NODE).parent) if NODE else None
NODE_SKIP_REASON = "no runnable node: command -v node found nothing, or node -p process.execPath failed"


def node_env(**extra: str) -> dict[str, str]:
    env = {**os.environ, **extra}
    env["PATH"] = f"{NODE_DIR}{os.pathsep}{env.get('PATH', '')}"
    return env


def run(*args: str | Path, timeout: float | None = None, **kwargs: object) -> subprocess.CompletedProcess[str]:
    """subprocess.run in its own session: on a timeout the whole process group dies, not only the direct child."""
    proc = subprocess.Popen(  # pyright: ignore[reportCallIssue]
        [str(a) for a in args], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True, **kwargs  # pyright: ignore[reportArgumentType]
    )
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.communicate()
        raise
    return subprocess.CompletedProcess(proc.args, proc.returncode, out, err)


def scratch(tmp_path: Path) -> Path:
    """The test's scratch root under <repo>/tmp/; the pid keeps it unique across xdist workers (pytest truncates tmp_path.name)."""
    return ROOT / "tmp" / f"skill-test-{os.getpid()}-{tmp_path.name}"


def out(tmp_path: Path, name: str) -> Path:
    """An --out directory under <repo>/tmp/ (the scripts refuse anything else); removed again by the conftest cleanup fixture."""
    path = scratch(tmp_path) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    return path
