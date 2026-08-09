# Bounded Agent Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bound every existing live Pi and OpenCode harness invocation by a systemd user scope, a monotonic Python deadline, and fixed transcript and stderr limits while preserving the current prompts and evidence validators.

**Architecture:** `pylib/agent_runner.py` owns process-boundary mechanics behind a dependency-injectable `ScopeBackend`; `llmenv.py` exposes one JSON-only command; Bash continues to own client configuration, source snapshots, evidence parsing, matrix flow, and redacted diagnostics. The runner captures the transient scope's cgroup path before trusting a client result and returns a precise timeout or stream-limit outcome only after the cgroup is absent or reports `populated 0`; every manager, setup, signal, or cleanup uncertainty becomes `boundary-failure`.

**Tech Stack:** Python 3.11+, `subprocess`, threads, monotonic time, Linux cgroup v2, systemd user scopes, Bash, jq, curl, pytest, Ruff, and ShellCheck.

## Global Constraints

- This plan covers one independently testable subproject: bounding the existing live-agent harness. Sampler experiment recovery belongs to a separate future plan.
- Production defaults are exactly 300 seconds of runtime, 10 seconds from TERM request to KILL request, and 33,554,432 retained bytes for each of transcript and client stderr.
- Display at most 262,144 redacted bytes from each agent diagnostic stream.
- Fetch at most 1,048,576 bytes for model discovery and each public source, and reject final assistant text larger than 1,048,576 bytes before shell evidence parsing.
- `BoundedRunResult` JSON contains exactly `schema`, `outcome`, `exit_status`, `transcript_bytes`, `stderr_bytes`, and `cleanup_proved`; `schema` is `1`.
- `outcome` is exactly one of `completed`, `timeout`, `transcript-limit`, `stderr-limit`, or `boundary-failure`.
- A reported nonzero client exit remains `outcome=completed` with that integer in `exit_status`; Bash retains responsibility for treating the client exit as a failed matrix cell.
- Production launches the unmodified client argument vector with `systemd-run --user --scope --quiet`; Python passes neither `cwd` nor `env` to `Popen`, so the scope inherits the Bash caller's working directory and environment, including `OPENCODE_API_KEY` without adding that secret to argv.
- Every production scope sets `RuntimeMaxSec=300s`, `RuntimeRandomizedExtraSec=0`, `KillMode=control-group`, and `OOMPolicy=kill`; test-only CLI flags may lower the runtime, grace interval, and stream cap.
- Python reads stdout and stderr concurrently in fixed-size chunks, stores no more than each cap, detects the first byte beyond a cap, keeps draining both pipes, and signals the exact random scope unit rather than a PID or process group.
- Python requests TERM at a timeout or stream limit and requests KILL only if the same scope is not quiescent 10 seconds later.
- A precise timeout or stream-limit result requires proved cleanup. An absent saved cgroup path or `cgroup.events` containing `populated 0` proves cleanup; an uncaptured path, malformed events file, manager command failure, signal failure, or unproved cleanup produces `boundary-failure`.
- The CLI prints only the small result JSON on stdout and exits zero whenever it successfully emits that result, regardless of the result's outcome or client exit status.
- Preserve Pi and OpenCode prompts byte-for-byte, including the exact client-side curl command. Add `--max-filesize` only to harness-owned model-discovery and source-snapshot requests. Preserve client options, isolated configuration, environment, working directory, final-response parsers, source comparisons, alias grammar, and evidence validators.
- Continue the matrix after `completed`, `timeout`, `transcript-limit`, or `stderr-limit` only when `cleanup_proved` is true. Abort all later cells on `boundary-failure`, malformed runner JSON, or any result whose cleanup is not proved.
- Retained diagnostic files stay private, size-bounded by their owning producer, and redacted by the existing `finish_diagnostic_dir` finalizer.
- Do not add a request broker, server cancellation, model-behavior classification, result manifest, ledger, storage reservation, experiment recovery, memory calibration, task calibration, container, Bubblewrap, alias grammar change, or security claim.
- After every Python or shell edit in this plan, use the stronger combined gate `make validate && make test` after the focused red-green cycle.
- Stage only files named by the current task. Do not alter the existing untracked resource-bounds design document.

---

## File Responsibility Map

| File | Responsibility |
|---|---|
| `pylib/agent_runner.py` | Own result types, limits, the backend protocol, production systemd scope operations, concurrent bounded stream capture, TERM-to-KILL escalation, and cgroup cleanup proof. |
| `llmenv.py` | Parse `run-agent-bounded`, construct `RunLimits`, call the runner, and emit only its exact JSON object. |
| `pylib/__init__.py` | Remain an empty package marker; do not re-export the runner API. |
| `.agents/architecture.md` | Record the new Python boundary owner and the Bash-to-Python JSON contract. |
| `tools/lib.sh` | Provide streaming, redact-before-truncate diagnostic logging without file contents in shell variables or argv. |
| `scripts/check-with-agents.sh` | Keep orchestration and validation while replacing both `tee` pipelines with the bounded CLI, mapping outcomes, enforcing source/final size limits, and aborting on boundary uncertainty. |
| `tests/test_agent_runner.py` | Exercise all runner outcomes through a local fake backend and unit-test exact systemd argv and cgroup event parsing without a live user manager. |
| `tests/test_cli.py` | Prove the new CLI passes override limits and emits exactly the six-field schema. |
| `tests/test_shell.py` | Replace the `tee` fixture with a fake bounded `uv` command and cover matrix flow, size failures, excerpts, preserved client setup, prompts, aliases, and validators. |
| `AGENTS.md` | Remain unchanged; its combined Python gate and JSON layer rules govern execution. |

## Verified Platform Baseline

- `systemd-run(1)` documents that scope execution is synchronous, runs the command as a child of `systemd-run`, inherits the caller's execution environment, and propagates the waited command's exit status.
- `systemd.scope(5)` documents `RuntimeMaxSec`, `RuntimeRandomizedExtraSec`, and `OOMPolicy` for scopes; `systemd.kill(5)` documents `KillMode=control-group`.
- The cgroup v2 kernel documentation defines `cgroup.events` as flat keyed text and `populated 0` as no live process in the cgroup or any descendant.
- Local systemd 259 accepts `--user`, `--scope`, `--quiet`, `--unit`, and repeated `--property`; local curl 8.18.0 supports `--max-filesize`.

### Task 1: Build the Dependency-Injectable Bounded Runner

**Files:**
- Create: `pylib/agent_runner.py`
- Create: `tests/test_agent_runner.py`

**Interfaces:**
- Consumes: an existing mode-`0600` transcript path, an existing mode-`0600` stderr path, a nonempty `Sequence[str]` client command, and an optional `ScopeBackend`.
- Produces: `RunLimits(runtime_seconds: float = 300.0, grace_seconds: float = 10.0, stream_limit_bytes: int = 33_554_432)`.
- Produces: `BoundedRunResult(outcome, exit_status, transcript_bytes, stderr_bytes, cleanup_proved)` with `to_dict() -> dict[str, object]`.
- Produces: `ScopeBackend.start_scope(unit_name, command, limits)`, `ScopeBackend.cgroup_path(unit_name, timeout_seconds)`, `ScopeBackend.signal_scope(unit_name, requested_signal, timeout_seconds)`, and `ScopeBackend.cgroup_empty(cgroup_path)`.
- Contract: backend setup, manager, signal, path, and cleanup uncertainty raises `BoundaryError`; `cgroup_path()` returns `None` only while the named scope is not observable yet, and `cgroup_empty()` returns `False` only for a parsed `populated 1`.
- Produces: `SystemdScopeBackend` and `run_bounded_agent(command, transcript_path, stderr_path, *, limits=RunLimits(), backend=None) -> BoundedRunResult`.

- [ ] **Step 1: Create the runner-test scaffold and local fake backend**

Create `tests/test_agent_runner.py` with this concrete scaffold:

```python
from __future__ import annotations

import signal
import subprocess
import sys
from pathlib import Path
from typing import Sequence

import pytest

import pylib.agent_runner as agent_runner
from pylib.agent_runner import (
    BoundaryError,
    BoundedRunResult,
    RunLimits,
    SystemdScopeBackend,
    run_bounded_agent,
)


class FakeBackend:
    def __init__(self, *, cleanup_error: bool = False) -> None:
        self.cleanup_error = cleanup_error
        self.process: subprocess.Popen[bytes] | None = None
        self.unit_name: str | None = None
        self.signals: list[signal.Signals] = []

    def start_scope(
        self,
        unit_name: str,
        command: Sequence[str],
        limits: RunLimits,
    ) -> subprocess.Popen[bytes]:
        self.unit_name = unit_name
        self.process = subprocess.Popen(
            list(command),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        return self.process

    def cgroup_path(self, unit_name: str, timeout_seconds: float) -> Path | None:
        assert timeout_seconds > 0
        assert unit_name == self.unit_name
        return Path("/fake-cgroup") / unit_name

    def signal_scope(
        self,
        unit_name: str,
        requested_signal: signal.Signals,
        timeout_seconds: float,
    ) -> None:
        assert timeout_seconds > 0
        assert unit_name == self.unit_name
        assert self.process is not None
        self.signals.append(requested_signal)
        if self.process.poll() is None:
            self.process.send_signal(requested_signal)

    def cgroup_empty(self, cgroup_path: Path) -> bool:
        assert cgroup_path == Path("/fake-cgroup") / self.unit_name
        if self.cleanup_error:
            raise BoundaryError("fixture cleanup state unavailable")
        assert self.process is not None
        return self.process.poll() is not None


def private_output(path: Path) -> None:
    path.touch()
    path.chmod(0o600)


def run_fixture(
    tmp_path: Path,
    code: str,
    *,
    runtime_seconds: float = 1.0,
    grace_seconds: float = 0.1,
    stream_limit_bytes: int = 1024,
    backend: FakeBackend | None = None,
) -> tuple[BoundedRunResult, Path, Path, FakeBackend]:
    transcript = tmp_path / "transcript"
    client_stderr = tmp_path / "client-stderr"
    private_output(transcript)
    private_output(client_stderr)
    selected_backend = backend or FakeBackend()
    result = run_bounded_agent(
        [sys.executable, "-c", code],
        transcript,
        client_stderr,
        limits=RunLimits(
            runtime_seconds=runtime_seconds,
            grace_seconds=grace_seconds,
            stream_limit_bytes=stream_limit_bytes,
        ),
        backend=selected_backend,
    )
    return result, transcript, client_stderr, selected_backend
```

- [ ] **Step 2: Add completed, nonzero-exit, and timeout tests**

Append these tests to `tests/test_agent_runner.py`:

```python


def test_bounded_run_completes_and_captures_both_streams(tmp_path: Path) -> None:
    result, transcript, client_stderr, backend = run_fixture(
        tmp_path,
        "import os; os.write(1, b'ok\\n'); os.write(2, b'warn')",
    )

    assert result.to_dict() == {
        "schema": 1,
        "outcome": "completed",
        "exit_status": 0,
        "transcript_bytes": 3,
        "stderr_bytes": 4,
        "cleanup_proved": True,
    }
    assert transcript.read_bytes() == b"ok\n"
    assert client_stderr.read_bytes() == b"warn"
    assert backend.signals == []


def test_bounded_run_reports_nonzero_client_exit_as_completed(tmp_path: Path) -> None:
    result, transcript, client_stderr, _ = run_fixture(
        tmp_path,
        "raise SystemExit(17)",
    )

    assert result.outcome == "completed"
    assert result.exit_status == 17
    assert result.cleanup_proved is True
    assert transcript.read_bytes() == b""
    assert client_stderr.read_bytes() == b""


def test_bounded_run_times_out_and_proves_cleanup(tmp_path: Path) -> None:
    result, _, _, backend = run_fixture(
        tmp_path,
        "import time; time.sleep(60)",
        runtime_seconds=0.05,
        grace_seconds=0.05,
    )

    assert result.outcome == "timeout"
    assert result.cleanup_proved is True
    assert backend.signals == [signal.SIGTERM]
```

- [ ] **Step 3: Add stream-limit, escalation, and cleanup-failure tests**

Append these tests to `tests/test_agent_runner.py`:

```python


def test_bounded_run_caps_transcript_and_detects_the_next_byte(tmp_path: Path) -> None:
    result, transcript, _, backend = run_fixture(
        tmp_path,
        "import os, time; os.write(1, b'123456789'); time.sleep(60)",
        stream_limit_bytes=8,
    )

    assert result.outcome == "transcript-limit"
    assert result.transcript_bytes == 8
    assert result.cleanup_proved is True
    assert transcript.read_bytes() == b"12345678"
    assert backend.signals == [signal.SIGTERM]


def test_bounded_run_caps_stderr_and_detects_the_next_byte(tmp_path: Path) -> None:
    result, _, client_stderr, backend = run_fixture(
        tmp_path,
        "import os, time; os.write(2, b'abcdefghi'); time.sleep(60)",
        stream_limit_bytes=8,
    )

    assert result.outcome == "stderr-limit"
    assert result.stderr_bytes == 8
    assert result.cleanup_proved is True
    assert client_stderr.read_bytes() == b"abcdefgh"
    assert backend.signals == [signal.SIGTERM]


def test_bounded_run_escalates_from_term_to_kill(tmp_path: Path) -> None:
    code = (
        "import signal, sys, time\n"
        "signal.signal(signal.SIGTERM, lambda *_: None)\n"
        "sys.stdout.write('ready\\n')\n"
        "sys.stdout.flush()\n"
        "time.sleep(60)\n"
    )
    result, _, _, backend = run_fixture(
        tmp_path,
        code,
        runtime_seconds=0.2,
        grace_seconds=0.05,
    )

    assert result.outcome == "timeout"
    assert result.cleanup_proved is True
    assert backend.signals == [signal.SIGTERM, signal.SIGKILL]


def test_bounded_run_turns_cleanup_uncertainty_into_boundary_failure(
    tmp_path: Path,
) -> None:
    backend = FakeBackend(cleanup_error=True)
    result, _, _, _ = run_fixture(tmp_path, "", backend=backend, grace_seconds=0.02)

    assert result.outcome == "boundary-failure"
    assert result.exit_status == 0
    assert result.cleanup_proved is False
```

- [ ] **Step 4: Add exact systemd argv and cgroup-events tests**

Append these tests to `tests/test_agent_runner.py`:

```python


def test_systemd_backend_constructs_exact_production_command(monkeypatch) -> None:
    captured: dict[str, object] = {}
    marker = object()

    def fake_popen(command: list[str], **kwargs: object) -> object:
        captured["command"] = command
        captured["kwargs"] = kwargs
        return marker

    monkeypatch.setattr(agent_runner.subprocess, "Popen", fake_popen)
    backend = SystemdScopeBackend()

    process = backend.start_scope(
        "llm-env-agent-fixed.scope",
        ["client", "argument"],
        RunLimits(),
    )

    assert process is marker
    assert captured["command"] == [
        "systemd-run",
        "--user",
        "--scope",
        "--quiet",
        "--unit=llm-env-agent-fixed.scope",
        "--property=RuntimeMaxSec=300s",
        "--property=RuntimeRandomizedExtraSec=0",
        "--property=KillMode=control-group",
        "--property=OOMPolicy=kill",
        "--",
        "client",
        "argument",
    ]
    assert captured["kwargs"] == {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "bufsize": 0,
    }


def test_systemd_backend_parses_cgroup_events_and_absence(tmp_path: Path) -> None:
    backend = SystemdScopeBackend(cgroup_root=tmp_path)
    scope = tmp_path / "scope"
    scope.mkdir()
    events = scope / "cgroup.events"

    events.write_text("populated 1\nfrozen 0\n", encoding="ascii")
    assert backend.cgroup_empty(scope) is False

    events.write_text("populated 0\nfrozen 0\n", encoding="ascii")
    assert backend.cgroup_empty(scope) is True

    events.unlink()
    with pytest.raises(BoundaryError):
        backend.cgroup_empty(scope)

    scope.rmdir()
    assert backend.cgroup_empty(scope) is True


@pytest.mark.parametrize(
    "events_text",
    [
        "frozen 0\n",
        "populated 2\n",
        "populated 0\npopulated 1\n",
        "populated\n",
    ],
)
def test_systemd_backend_rejects_uncertain_cgroup_events(
    tmp_path: Path,
    events_text: str,
) -> None:
    backend = SystemdScopeBackend(cgroup_root=tmp_path)
    scope = tmp_path / "scope"
    scope.mkdir()
    (scope / "cgroup.events").write_text(events_text, encoding="ascii")

    with pytest.raises(BoundaryError):
        backend.cgroup_empty(scope)
```

- [ ] **Step 5: Run the new module and verify the missing implementation fails collection**

Run:

```bash
uv run --with pytest pytest tests/test_agent_runner.py -v
```

Expected: pytest exits nonzero during collection with `ModuleNotFoundError: No module named 'pylib.agent_runner'`.

- [ ] **Step 6: Add limits, result types, the protocol, and the systemd backend**

Create `pylib/agent_runner.py` with this concrete first section:

```python
"""Run one agent client inside a bounded transient systemd scope."""

from __future__ import annotations

import math
import os
import queue
import secrets
import signal
import stat
import subprocess
import threading
import time
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Literal, Protocol, Sequence

DEFAULT_RUNTIME_SECONDS = 300.0
DEFAULT_GRACE_SECONDS = 10.0
DEFAULT_STREAM_LIMIT_BYTES = 33_554_432

_STREAM_CHUNK_BYTES = 64 * 1024
_POLL_SECONDS = 0.01

Outcome = Literal[
    "completed",
    "timeout",
    "transcript-limit",
    "stderr-limit",
    "boundary-failure",
]


class BoundaryError(RuntimeError):
    """The process boundary cannot be established or proved clean."""


@dataclass(frozen=True)
class RunLimits:
    runtime_seconds: float = DEFAULT_RUNTIME_SECONDS
    grace_seconds: float = DEFAULT_GRACE_SECONDS
    stream_limit_bytes: int = DEFAULT_STREAM_LIMIT_BYTES

    def __post_init__(self) -> None:
        for name, value in (
            ("runtime_seconds", self.runtime_seconds),
            ("grace_seconds", self.grace_seconds),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value <= 0
            ):
                raise ValueError(f"{name} must be a finite positive number")
        if (
            isinstance(self.stream_limit_bytes, bool)
            or not isinstance(self.stream_limit_bytes, int)
            or self.stream_limit_bytes <= 0
        ):
            raise ValueError("stream_limit_bytes must be a positive integer")


@dataclass(frozen=True)
class BoundedRunResult:
    schema: Literal[1] = field(init=False, default=1)
    outcome: Outcome
    exit_status: int | None
    transcript_bytes: int
    stderr_bytes: int
    cleanup_proved: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.schema,
            "outcome": self.outcome,
            "exit_status": self.exit_status,
            "transcript_bytes": self.transcript_bytes,
            "stderr_bytes": self.stderr_bytes,
            "cleanup_proved": self.cleanup_proved,
        }


class ScopeBackend(Protocol):
    def start_scope(
        self,
        unit_name: str,
        command: Sequence[str],
        limits: RunLimits,
    ) -> subprocess.Popen[bytes]:
        raise NotImplementedError

    def cgroup_path(self, unit_name: str, timeout_seconds: float) -> Path | None:
        raise NotImplementedError

    def signal_scope(
        self,
        unit_name: str,
        requested_signal: signal.Signals,
        timeout_seconds: float,
    ) -> None:
        raise NotImplementedError

    def cgroup_empty(self, cgroup_path: Path) -> bool:
        raise NotImplementedError


class SystemdScopeBackend:
    def __init__(self, *, cgroup_root: Path = Path("/sys/fs/cgroup")) -> None:
        self._cgroup_root = cgroup_root

    def start_scope(
        self,
        unit_name: str,
        command: Sequence[str],
        limits: RunLimits,
    ) -> subprocess.Popen[bytes]:
        runtime = f"{limits.runtime_seconds:g}s"
        return subprocess.Popen(
            [
                "systemd-run",
                "--user",
                "--scope",
                "--quiet",
                f"--unit={unit_name}",
                f"--property=RuntimeMaxSec={runtime}",
                "--property=RuntimeRandomizedExtraSec=0",
                "--property=KillMode=control-group",
                "--property=OOMPolicy=kill",
                "--",
                *command,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )

    def cgroup_path(self, unit_name: str, timeout_seconds: float) -> Path | None:
        try:
            result = subprocess.run(
                [
                    "systemctl",
                    "--user",
                    "show",
                    unit_name,
                    "--property=ControlGroup",
                    "--value",
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout_seconds,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise BoundaryError("could not query transient scope") from exc
        if result.returncode != 0:
            return None
        relative_text = result.stdout.strip()
        if not relative_text or "\n" in relative_text:
            return None
        relative = PurePosixPath(relative_text)
        if not relative.is_absolute() or ".." in relative.parts:
            raise BoundaryError("systemd returned an invalid cgroup path")
        return self._cgroup_root.joinpath(*relative.parts[1:])

    def signal_scope(
        self,
        unit_name: str,
        requested_signal: signal.Signals,
        timeout_seconds: float,
    ) -> None:
        signal_name = requested_signal.name.removeprefix("SIG")
        try:
            result = subprocess.run(
                [
                    "systemctl",
                    "--user",
                    "kill",
                    f"--signal={signal_name}",
                    "--kill-whom=all",
                    unit_name,
                ],
                capture_output=True,
                check=False,
                timeout=timeout_seconds,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise BoundaryError(f"could not request {signal_name}") from exc
        if result.returncode != 0:
            raise BoundaryError(f"systemd rejected {signal_name}")

    def cgroup_empty(self, cgroup_path: Path) -> bool:
        events_path = cgroup_path / "cgroup.events"
        try:
            text = events_path.read_text(encoding="ascii")
        except FileNotFoundError:
            try:
                cgroup_path.stat()
            except FileNotFoundError:
                return True
            except OSError as exc:
                raise BoundaryError("could not inspect saved cgroup path") from exc
            raise BoundaryError("cgroup.events is absent from the saved cgroup path")
        except (OSError, UnicodeError) as exc:
            raise BoundaryError("could not read cgroup.events") from exc

        fields: dict[str, str] = {}
        for line in text.splitlines():
            parts = line.split()
            if len(parts) != 2 or parts[0] in fields:
                raise BoundaryError("malformed cgroup.events")
            fields[parts[0]] = parts[1]
        populated = fields.get("populated")
        if populated not in {"0", "1"}:
            raise BoundaryError("cgroup.events has no valid populated field")
        return populated == "0"
```

- [ ] **Step 7: Add fixed-chunk drains, termination escalation, and result helpers**

Append this code to `pylib/agent_runner.py`:

```python


@dataclass
class _DrainState:
    stored_bytes: int = 0
    exceeded: bool = False
    failed: bool = False


def _open_private_output(path: Path) -> BinaryIO:
    flags = os.O_WRONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise BoundaryError(f"could not open private output {path}") from exc
    try:
        file_stat = os.fstat(descriptor)
    except OSError as exc:
        os.close(descriptor)
        raise BoundaryError(f"could not inspect private output {path}") from exc
    if not stat.S_ISREG(file_stat.st_mode) or stat.S_IMODE(file_stat.st_mode) != 0o600:
        os.close(descriptor)
        raise BoundaryError(f"output must be an existing mode-0600 file: {path}")
    try:
        return os.fdopen(descriptor, "wb", buffering=0)
    except OSError as exc:
        os.close(descriptor)
        raise BoundaryError(f"could not wrap private output {path}") from exc


def _drain_stream(
    source: BinaryIO,
    destination: BinaryIO,
    limit_bytes: int,
    limit_outcome: Outcome,
    events: queue.Queue[Outcome],
    state: _DrainState,
) -> None:
    write_enabled = True
    try:
        while True:
            try:
                chunk = source.read(_STREAM_CHUNK_BYTES)
            except OSError:
                state.failed = True
                events.put("boundary-failure")
                return
            if not chunk:
                return

            remaining = max(0, limit_bytes - state.stored_bytes)
            retained = chunk[:remaining]
            if retained and write_enabled:
                try:
                    written = destination.write(retained)
                except OSError:
                    written = 0
                state.stored_bytes += written or 0
                if written != len(retained):
                    write_enabled = False
                    state.failed = True
                    events.put("boundary-failure")

            if len(chunk) > remaining and not state.exceeded:
                state.exceeded = True
                events.put(limit_outcome)
    finally:
        try:
            source.close()
        except OSError:
            state.failed = True
            events.put("boundary-failure")


def _execution_finished(
    process: subprocess.Popen[bytes],
    threads: Sequence[threading.Thread],
) -> bool:
    return process.poll() is not None and not any(thread.is_alive() for thread in threads)


def _next_event(events: queue.Queue[Outcome]) -> Outcome | None:
    selected: Outcome | None = None
    while True:
        try:
            event = events.get_nowait()
        except queue.Empty:
            return selected
        if event == "boundary-failure":
            return event
        if selected is None:
            selected = event


def _cleanup_now(
    backend: ScopeBackend,
    cgroup_path: Path | None,
    process: subprocess.Popen[bytes],
    threads: Sequence[threading.Thread],
) -> tuple[bool, bool]:
    if not _execution_finished(process, threads):
        return False, False
    if cgroup_path is None:
        return False, True
    try:
        return backend.cgroup_empty(cgroup_path), False
    except BoundaryError:
        return False, True


def _reap_local_launcher(
    process: subprocess.Popen[bytes],
    threads: Sequence[threading.Thread],
    deadline: float,
) -> bool:
    while time.monotonic() < deadline:
        if _execution_finished(process, threads):
            return True
        time.sleep(_POLL_SECONDS)

    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=max(_POLL_SECONDS, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            process.kill()
            try:
                process.wait(timeout=_POLL_SECONDS * 10)
            except subprocess.TimeoutExpired:
                return False
    for thread in threads:
        thread.join(timeout=_POLL_SECONDS * 10)
    return _execution_finished(process, threads)


def _wait_for_cleanup(
    backend: ScopeBackend,
    cgroup_path: Path | None,
    process: subprocess.Popen[bytes],
    threads: Sequence[threading.Thread],
    deadline: float,
) -> tuple[bool, bool]:
    if cgroup_path is None:
        return False, True
    while True:
        try:
            scope_empty = backend.cgroup_empty(cgroup_path)
        except BoundaryError:
            return False, True
        if scope_empty:
            reaped = _reap_local_launcher(process, threads, deadline)
            return reaped, not reaped
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False, False
        time.sleep(min(_POLL_SECONDS, remaining))


def _stop_and_prove(
    backend: ScopeBackend,
    unit_name: str,
    cgroup_path: Path | None,
    process: subprocess.Popen[bytes],
    threads: Sequence[threading.Thread],
    limits: RunLimits,
) -> tuple[bool, bool]:
    cleanup_proved, boundary_failed = _cleanup_now(
        backend, cgroup_path, process, threads
    )
    if cleanup_proved:
        return True, boundary_failed
    if cgroup_path is None:
        try:
            backend.signal_scope(unit_name, signal.SIGTERM, limits.grace_seconds)
        except BoundaryError:
            pass
        term_deadline = time.monotonic() + limits.grace_seconds
        while process.poll() is None and time.monotonic() < term_deadline:
            time.sleep(_POLL_SECONDS)
        if process.poll() is None:
            try:
                backend.signal_scope(unit_name, signal.SIGKILL, limits.grace_seconds)
            except BoundaryError:
                pass
        _reap_local_launcher(
            process,
            threads,
            time.monotonic() + limits.grace_seconds,
        )
        return False, True

    term_requested_at = time.monotonic()
    try:
        backend.signal_scope(unit_name, signal.SIGTERM, limits.grace_seconds)
    except BoundaryError:
        pass
    cleanup_proved, proof_failed = _wait_for_cleanup(
        backend,
        cgroup_path,
        process,
        threads,
        term_requested_at + limits.grace_seconds,
    )
    boundary_failed = boundary_failed or proof_failed
    if cleanup_proved:
        return True, boundary_failed

    try:
        backend.signal_scope(unit_name, signal.SIGKILL, limits.grace_seconds)
    except BoundaryError:
        pass
    cleanup_proved, proof_failed = _wait_for_cleanup(
        backend,
        cgroup_path,
        process,
        threads,
        time.monotonic() + limits.grace_seconds,
    )
    return cleanup_proved, boundary_failed or proof_failed or not cleanup_proved


def _make_result(
    outcome: Outcome,
    process: subprocess.Popen[bytes] | None,
    transcript_state: _DrainState,
    stderr_state: _DrainState,
    cleanup_proved: bool,
) -> BoundedRunResult:
    return BoundedRunResult(
        outcome=outcome,
        exit_status=None if process is None else process.poll(),
        transcript_bytes=transcript_state.stored_bytes,
        stderr_bytes=stderr_state.stored_bytes,
        cleanup_proved=cleanup_proved,
    )
```

- [ ] **Step 8: Add the bounded run coordinator**

Append this function to `pylib/agent_runner.py`:

```python


def run_bounded_agent(
    command: Sequence[str],
    transcript_path: Path,
    stderr_path: Path,
    *,
    limits: RunLimits = RunLimits(),
    backend: ScopeBackend | None = None,
) -> BoundedRunResult:
    """Run one command and return only bounded process facts."""
    transcript_state = _DrainState()
    stderr_state = _DrainState()
    if not command:
        return _make_result(
            "boundary-failure", None, transcript_state, stderr_state, False
        )

    selected_backend = backend or SystemdScopeBackend()
    unit_name = f"llm-env-agent-{secrets.token_hex(16)}.scope"
    started_at = time.monotonic()
    runtime_deadline = started_at + limits.runtime_seconds
    process: subprocess.Popen[bytes] | None = None

    with ExitStack() as stack:
        try:
            transcript_output = stack.enter_context(
                _open_private_output(transcript_path)
            )
            stderr_output = stack.enter_context(_open_private_output(stderr_path))
            os.ftruncate(transcript_output.fileno(), 0)
            os.ftruncate(stderr_output.fileno(), 0)
            process = selected_backend.start_scope(unit_name, command, limits)
        except (BoundaryError, OSError):
            return _make_result(
                "boundary-failure",
                process,
                transcript_state,
                stderr_state,
                False,
            )

        if process.stdout is None or process.stderr is None:
            return _make_result(
                "boundary-failure",
                process,
                transcript_state,
                stderr_state,
                False,
            )

        events: queue.Queue[Outcome] = queue.Queue()
        transcript_thread = threading.Thread(
            target=_drain_stream,
            args=(
                process.stdout,
                transcript_output,
                limits.stream_limit_bytes,
                "transcript-limit",
                events,
                transcript_state,
            ),
            daemon=True,
        )
        stderr_thread = threading.Thread(
            target=_drain_stream,
            args=(
                process.stderr,
                stderr_output,
                limits.stream_limit_bytes,
                "stderr-limit",
                events,
                stderr_state,
            ),
            daemon=True,
        )
        threads = (transcript_thread, stderr_thread)

        cgroup_path: Path | None = None
        pending_outcome: Outcome | None = None
        boundary_failed = False
        while cgroup_path is None:
            remaining = runtime_deadline - time.monotonic()
            if remaining <= 0:
                pending_outcome = "timeout"
                break
            try:
                cgroup_path = selected_backend.cgroup_path(
                    unit_name,
                    min(limits.grace_seconds, remaining),
                )
            except BoundaryError:
                boundary_failed = True
                break
            if process.poll() is not None:
                boundary_failed = True
                break
            time.sleep(min(_POLL_SECONDS, remaining))

        # Do not drain client output until the cgroup path is saved. Kernel pipe
        # capacity back-pressures a fast writer without allowing a stream limit
        # to race ahead of the cleanup identity needed to stop it safely.
        for thread in threads:
            thread.start()

        if cgroup_path is None:
            cleanup_proved, stop_failed = _stop_and_prove(
                selected_backend,
                unit_name,
                None,
                process,
                threads,
                limits,
            )
            return _make_result(
                "boundary-failure",
                process,
                transcript_state,
                stderr_state,
                cleanup_proved and not (boundary_failed or stop_failed),
            )

        if pending_outcome is None and not boundary_failed:
            while True:
                pending_outcome = _next_event(events)
                if pending_outcome is not None:
                    boundary_failed = pending_outcome == "boundary-failure"
                    break
                if time.monotonic() >= runtime_deadline:
                    pending_outcome = "timeout"
                    break
                if _execution_finished(process, threads):
                    if transcript_state.failed or stderr_state.failed:
                        boundary_failed = True
                        pending_outcome = "boundary-failure"
                        break
                    if transcript_state.exceeded:
                        pending_outcome = "transcript-limit"
                        break
                    if stderr_state.exceeded:
                        pending_outcome = "stderr-limit"
                        break
                    cleanup_proved, proof_failed = _cleanup_now(
                        selected_backend, cgroup_path, process, threads
                    )
                    if proof_failed:
                        boundary_failed = True
                        pending_outcome = "boundary-failure"
                        break
                    if cleanup_proved:
                        return _make_result(
                            "completed",
                            process,
                            transcript_state,
                            stderr_state,
                            True,
                        )
                remaining = runtime_deadline - time.monotonic()
                if remaining > 0:
                    time.sleep(min(_POLL_SECONDS, remaining))

        cleanup_proved, stop_failed = _stop_and_prove(
            selected_backend,
            unit_name,
            cgroup_path,
            process,
            threads,
            limits,
        )
        if (
            boundary_failed
            or stop_failed
            or not cleanup_proved
            or pending_outcome == "boundary-failure"
            or transcript_state.failed
            or stderr_state.failed
        ):
            final_outcome: Outcome = "boundary-failure"
        else:
            assert pending_outcome is not None
            final_outcome = pending_outcome
        return _make_result(
            final_outcome,
            process,
            transcript_state,
            stderr_state,
            cleanup_proved,
        )
```

- [ ] **Step 9: Run the runner tests and verify every local-backend case passes**

Run:

```bash
uv run --with pytest pytest tests/test_agent_runner.py -v
```

Expected: pytest exits zero; all success, nonzero exit, timeout, transcript cap, stderr cap, escalation, cleanup failure, production argv, and cgroup parsing cases report `PASSED`.

- [ ] **Step 10: Run the required combined Python gate**

Run:

```bash
make validate && make test
```

Expected: exit status `0`; ShellCheck and Ruff emit no diagnostics, `All checks passed.` is printed, and pytest reports no failures.

- [ ] **Step 11: Commit the bounded runner**

```bash
git add pylib/agent_runner.py tests/test_agent_runner.py
git commit -m "feat(agent-runner): bound live agent processes"
```

### Task 2: Expose the Exact JSON CLI Contract

**Files:**
- Modify: `llmenv.py:15-43,243-293`
- Modify: `tests/test_cli.py:211-226`
- Modify: `.agents/architecture.md:10-25,27-43`

**Interfaces:**
- Consumes: `RunLimits`, `BoundedRunResult`, and `run_bounded_agent` from Task 1.
- Produces: `uv run llmenv.py run-agent-bounded --transcript PATH --stderr PATH [--runtime-seconds FLOAT] [--grace-seconds FLOAT] [--stream-limit-bytes INT] -- COMMAND ARGUMENTS`.
- Produces: stdout containing only one JSON object with the exact `BoundedRunResult` schema; every emitted runner result uses CLI exit status `0`.

- [ ] **Step 1: Add a failing CLI contract test**

Add this test after `test_unknown_subcommand_is_usage_error()` in `tests/test_cli.py`:

```python
def test_run_agent_bounded_cli_passes_limits_and_emits_exact_json(
    tmp_path: Path,
    monkeypatch,
    capsys,
):
    import llmenv
    from pylib.agent_runner import BoundedRunResult, RunLimits

    captured = {}

    def fake_run_bounded_agent(command, transcript_path, stderr_path, *, limits):
        captured.update(
            command=list(command),
            transcript_path=transcript_path,
            stderr_path=stderr_path,
            limits=limits,
        )
        return BoundedRunResult(
            outcome="completed",
            exit_status=17,
            transcript_bytes=12,
            stderr_bytes=4,
            cleanup_proved=True,
        )

    monkeypatch.setattr(llmenv, "run_bounded_agent", fake_run_bounded_agent)
    transcript = tmp_path / "transcript"
    client_stderr = tmp_path / "client-stderr"

    status = llmenv.main(
        [
            "run-agent-bounded",
            "--transcript",
            str(transcript),
            "--stderr",
            str(client_stderr),
            "--runtime-seconds",
            "1.5",
            "--grace-seconds",
            "0.25",
            "--stream-limit-bytes",
            "7",
            "--",
            "client",
            "argument",
        ]
    )

    output = capsys.readouterr()
    assert status == 0
    assert output.err == ""
    assert json.loads(output.out) == {
        "schema": 1,
        "outcome": "completed",
        "exit_status": 17,
        "transcript_bytes": 12,
        "stderr_bytes": 4,
        "cleanup_proved": True,
    }
    assert captured == {
        "command": ["client", "argument"],
        "transcript_path": transcript,
        "stderr_path": client_stderr,
        "limits": RunLimits(
            runtime_seconds=1.5,
            grace_seconds=0.25,
            stream_limit_bytes=7,
        ),
    }


def test_run_agent_bounded_cli_requires_a_remainder_command(tmp_path: Path) -> None:
    result = run(
        "run-agent-bounded",
        "--transcript",
        str(tmp_path / "transcript"),
        "--stderr",
        str(tmp_path / "client-stderr"),
    )

    assert result.returncode == 2
    assert result.stdout == ""
    assert "a remainder command is required" in result.stderr
```

- [ ] **Step 2: Run the new CLI test and verify the subcommand is absent**

Run:

```bash
uv run --with pytest pytest \
  tests/test_cli.py::test_run_agent_bounded_cli_passes_limits_and_emits_exact_json \
  tests/test_cli.py::test_run_agent_bounded_cli_requires_a_remainder_command \
  -v
```

Expected: pytest exits nonzero with argparse's `invalid choice: 'run-agent-bounded'` diagnostic and a `SystemExit: 2` failure.

- [ ] **Step 3: Import the runner API and add strict positive argument parsers**

Add this import after the existing `pylib` imports in `llmenv.py`:

```python
from pylib.agent_runner import (
    DEFAULT_GRACE_SECONDS,
    DEFAULT_RUNTIME_SECONDS,
    DEFAULT_STREAM_LIMIT_BYTES,
    RunLimits,
    run_bounded_agent,
)
```

Add these functions immediately before `build_parser()`:

```python
def _positive_float(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number") from exc
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("must be a finite positive number")
    return parsed


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def cmd_run_agent_bounded(args: argparse.Namespace) -> int:
    command = list(args.client_command)
    if command[:1] == ["--"]:
        command = command[1:]
    result = run_bounded_agent(
        command,
        Path(args.transcript),
        Path(args.stderr),
        limits=RunLimits(
            runtime_seconds=args.runtime_seconds,
            grace_seconds=args.grace_seconds,
            stream_limit_bytes=args.stream_limit_bytes,
        ),
    )
    return emit(result.to_dict())
```

- [ ] **Step 4: Register the bounded remainder-command parser**

Insert this parser block in `build_parser()` immediately before `return parser`:

```python
    bounded = sub.add_parser("run-agent-bounded")
    bounded.add_argument("--transcript", required=True)
    bounded.add_argument("--stderr", required=True)
    bounded.add_argument(
        "--runtime-seconds",
        type=_positive_float,
        default=DEFAULT_RUNTIME_SECONDS,
    )
    bounded.add_argument(
        "--grace-seconds",
        type=_positive_float,
        default=DEFAULT_GRACE_SECONDS,
    )
    bounded.add_argument(
        "--stream-limit-bytes",
        type=_positive_int,
        default=DEFAULT_STREAM_LIMIT_BYTES,
    )
    bounded.add_argument("client_command", nargs=argparse.REMAINDER, metavar="COMMAND")
    bounded.set_defaults(func=cmd_run_agent_bounded)
```

- [ ] **Step 5: Require a nonempty remainder command in main dispatch**

Replace `main()` with this complete parser, command gate, and existing dispatch logic:

```python
def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "run-agent-bounded":
        command = list(args.client_command)
        if command[:1] == ["--"]:
            command = command[1:]
        if not command:
            parser.error("a remainder command is required")
    if args.command == "models" and args.action != "list" and not args.aliases:
        print(json.dumps({"error": "at least one alias is required"}, indent=2))
        return 2
    if (
        args.command == "models"
        and args.action in ("enable", "disable")
        and len(args.aliases) != 1
    ):
        print(
            json.dumps(
                {"error": "exactly one alias is required for enable/disable"},
                indent=2,
            )
        )
        return 2
    try:
        return args.func(args)
    except (ConfigError, BudgetError, GgufError, DetectError) as exc:
        return fail(str(exc))
    except OSError as exc:
        return fail(f"filesystem error: {exc}")
```

- [ ] **Step 6: Record the new file owner in the architecture guide**

Add this row immediately after the `llmenv.py` row in `.agents/architecture.md`:

```markdown
| `pylib/agent_runner.py` | Bounded live-agent scopes, stream capture, and cleanup proof |
```

Add this invariant after the host-side `127.0.0.1` invariant:

```markdown
- Live Pi and OpenCode checks enter a random systemd user scope through
  `run-agent-bounded`; Bash consumes only its six-field result JSON and never
  treats an unproved scope cleanup as a model result.
```

- [ ] **Step 7: Run the focused CLI tests and verify the exact schema passes**

Run:

```bash
uv run --with pytest pytest \
  tests/test_cli.py::test_run_agent_bounded_cli_passes_limits_and_emits_exact_json \
  tests/test_cli.py::test_run_agent_bounded_cli_requires_a_remainder_command \
  -v
```

Expected: pytest exits zero and both named tests report `PASSED`.

- [ ] **Step 8: Run the required combined Python gate**

Run:

```bash
make validate && make test
```

Expected: exit status `0`; `All checks passed.` is printed and pytest reports no failures.

- [ ] **Step 9: Commit the CLI boundary**

```bash
git add llmenv.py tests/test_cli.py .agents/architecture.md
git commit -m "feat(cli): expose bounded agent runner"
```

### Task 3: Add Streaming Redacted File Excerpts

**Files:**
- Modify: `tools/lib.sh:24-65`
- Modify: `tests/test_shell.py:100-114`

**Interfaces:**
- Consumes: `log_file_excerpt LABEL FILE MAX_BYTES`, where `FILE` is readable and `MAX_BYTES` is a non-negative decimal integer.
- Produces: a labeled block whose complete input passes through `_redact_stream` before a draining consumer emits at most `MAX_BYTES`; an empty file prints the existing `(empty)` marker.
- Preserves: no file body enters a shell variable, function argument, command argument, or environment variable.

- [ ] **Step 1: Add a failing helper-level shell test**

Add this test after `_mock_dirname()` in `tests/test_shell.py`:

```python
def test_log_file_excerpt_redacts_before_capping_and_drains_input(
    tmp_path: pathlib.Path,
) -> None:
    secret = "fixture-excerpt-secret"
    config = tmp_path / "models.yml"
    config.write_text(f"server:\n  api_key: {secret}\n")
    source = tmp_path / "diagnostic"
    source.write_bytes(
        secret.encode() + b"abcdefghijklmnopqrstuvwxyz" + (b"z" * 1_048_576)
    )

    result = subprocess.run(
        [
            "/usr/bin/bash",
            "-c",
            'source tools/lib.sh; log_file_excerpt "Client stderr" "$SOURCE" 12',
        ],
        cwd=ROOT,
        env=os.environ
        | {
            "LLM_ENV_CONFIG": str(config),
            "SOURCE": str(source),
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "Client stderr:\n  <redacted>ab\n"
    assert secret not in result.stdout + result.stderr
```

- [ ] **Step 2: Run the helper test and verify the function is missing**

Run:

```bash
uv run --with pytest pytest \
  tests/test_shell.py::test_log_file_excerpt_redacts_before_capping_and_drains_input \
  -v
```

Expected: pytest exits nonzero because Bash reports `log_file_excerpt: command not found` and returns status `127`.

- [ ] **Step 3: Implement the streaming helper without a content variable**

Add this function immediately after `log_nonempty_block()` in `tools/lib.sh`:

```bash
log_file_excerpt() {
    if [ "$#" -ne 3 ]; then
        return 64
    fi
    local label="$1" file="$2" max_bytes="$3"

    case "$max_bytes" in
        ''|*[!0-9]*) return 64 ;;
    esac
    [ -f "$file" ] && [ -r "$file" ] || return 66

    printf '%s:\n' "$(redact_text "$label")"
    if [ ! -s "$file" ]; then
        printf '  (empty)\n'
        return
    fi

    if ! _redact_stream < "$file" |
        {
            head -c "$max_bytes"
            cat >/dev/null
        } |
        sed 's/^/  /'; then
        return 1
    fi
    printf '\n'
}
```

The grouped `head` and `cat` share one pipe reader: `head` emits the prefix, then `cat` drains every remaining redacted byte, so `_redact_stream` never receives SIGPIPE from prefix truncation.

- [ ] **Step 4: Run the focused helper test and verify exact output**

Run:

```bash
uv run --with pytest pytest \
  tests/test_shell.py::test_log_file_excerpt_redacts_before_capping_and_drains_input \
  -v
```

Expected: pytest exits zero and the named test reports `PASSED`; stdout is exactly the 12-byte redacted prefix under `Client stderr:`.

- [ ] **Step 5: Run the required combined gate**

Run:

```bash
make validate && make test
```

Expected: exit status `0`; ShellCheck and Ruff pass, `All checks passed.` is printed, and pytest reports no failures.

- [ ] **Step 6: Commit the excerpt helper**

```bash
git add tools/lib.sh tests/test_shell.py
git commit -m "feat(logging): stream bounded diagnostic excerpts"
```

### Task 4: Route Both Agent Clients Through the Bounded CLI

**Files:**
- Modify: `scripts/check-with-agents.sh:148-273,395-428`
- Modify: `tests/test_shell.py:3015-3203,3593-3614`

**Interfaces:**
- Consumes: the Task 2 CLI and exact six-field result JSON.
- Produces: `parse_bounded_result()`, which rejects extra keys, wrong types, unknown outcomes, fractional statuses or counters, and negative counters.
- Produces: `run_agent()` globals `AGENT_FAILURE_STAGE`, `AGENT_FAILURE_REASON`, `AGENT_RESULT_REASON`, `AGENT_ABORT_MATRIX`, `AGENT_EXIT_STATUS`, `AGENT_CLIENT_BASE`, and `DISPLAYED_CLIENT_COMMAND`.
- Produces: precise shell stages `agent timeout`, `transcript limit`, `stderr limit`, and `resource boundary`; structured row reasons `timeout`, `transcript-limit`, `stderr-limit`, and `boundary-failure`.

- [ ] **Step 1: Change the agent-check fixture inputs from tee control to runner-result control**

In `run_agent_check()` in `tests/test_shell.py`, remove `tee_exit` and add `runner_results` in its place:

```python
    runner_results: list[dict[str, object]] | None = None,
```

Remove `real_tee = shutil.which("tee")` and its assertion. After creating `source_counter`, create a runner counter:

```python
    runner_counter = tmp_path / "runner-counter"
    runner_counter.write_text("0\n")
```

Extend the external-command wrapper mapping to this exact mapping:

```python
    for name, executable in {
        "cat": "/usr/bin/cat",
        "chmod": "/usr/bin/chmod",
        "date": real_date,
        "find": "/usr/bin/find",
        "head": "/usr/bin/head",
        "mkdir": "/usr/bin/mkdir",
        "mktemp": "/usr/bin/mktemp",
        "mv": "/usr/bin/mv",
        "rm": "/usr/bin/rm",
        "sed": "/usr/bin/sed",
        "wc": "/usr/bin/wc",
        "yq": real_yq,
    }.items():
        command = commands / name
        command.write_text(f"#!/usr/bin/bash\nexec {executable!s} \"$@\"\n")
        command.chmod(command.stat().st_mode | stat.S_IXUSR)
```

- [ ] **Step 2: Replace the tee fake with a bounded uv fake that executes the remainder command**

Delete the `tee = commands / "tee"` block and replace it with this fake `uv` command:

```python
    uv = commands / "uv"
    uv.write_text(
        "#!/usr/bin/bash\n"
        "printf 'uv %s\\n' \"$*\" >> \"$CALLS\"\n"
        "[ \"$1\" = run ] || exit 64\n"
        "shift\n"
        "[ \"$1\" = \"$AGENT_CHECK_LLMENV\" ] || exit 65\n"
        "shift\n"
        "[ \"$1\" = run-agent-bounded ] || exit 66\n"
        "shift\n"
        "transcript=\n"
        "client_stderr=\n"
        "while [ \"$#\" -gt 0 ]; do\n"
        "  case \"$1\" in\n"
        "    --transcript) transcript=\"$2\"; shift 2 ;;\n"
        "    --stderr) client_stderr=\"$2\"; shift 2 ;;\n"
        "    --runtime-seconds|--grace-seconds|--stream-limit-bytes) shift 2 ;;\n"
        "    --) shift; break ;;\n"
        "    *) exit 67 ;;\n"
        "  esac\n"
        "done\n"
        "[ -n \"$transcript\" ] && [ -n \"$client_stderr\" ] && [ \"$#\" -gt 0 ] || exit 68\n"
        "\"$@\" </dev/null >\"$transcript\" 2>\"$client_stderr\"\n"
        "client_status=$?\n"
        "count=\"$(< \"$AGENT_CHECK_RUNNER_COUNTER\")\"\n"
        "count=$((count + 1))\n"
        "printf '%s\\n' \"$count\" > \"$AGENT_CHECK_RUNNER_COUNTER\"\n"
        "result=\"$(\"$REAL_JQ\" -cer --argjson index \"$((count - 1))\" '.[$index] // empty' <<<\"$AGENT_CHECK_RUNNER_RESULTS\" 2>/dev/null)\"\n"
        "if [ -n \"$result\" ]; then\n"
        "  printf '%s\\n' \"$result\"\n"
        "  exit 0\n"
        "fi\n"
        "transcript_bytes=\"$(wc -c < \"$transcript\")\"\n"
        "stderr_bytes=\"$(wc -c < \"$client_stderr\")\"\n"
        "\"$REAL_JQ\" -cn --argjson exit_status \"$client_status\" --argjson transcript_bytes \"$transcript_bytes\" --argjson stderr_bytes \"$stderr_bytes\" '{schema:1,outcome:\"completed\",exit_status:$exit_status,transcript_bytes:$transcript_bytes,stderr_bytes:$stderr_bytes,cleanup_proved:true}'\n"
    )
    uv.chmod(uv.stat().st_mode | stat.S_IXUSR)
```

Add these exact entries to the fixture environment and remove `AGENT_CHECK_TEE_EXIT` and `REAL_TEE`:

```python
        "AGENT_CHECK_LLMENV": str(ROOT / "llmenv.py"),
        "AGENT_CHECK_RUNNER_COUNTER": str(runner_counter),
        "AGENT_CHECK_RUNNER_RESULTS": json.dumps(runner_results or []),
```

- [ ] **Step 3: Replace the obsolete tee-failure test with bounded-outcome and abort tests**

Delete `test_agent_check_reports_client_exit_when_transcript_capture_fails()` and add this helper and these tests in its location:

```python
def bounded_fixture_result(
    outcome: str,
    *,
    exit_status: int | None = None,
    cleanup_proved: bool = True,
) -> dict[str, object]:
    return {
        "schema": 1,
        "outcome": outcome,
        "exit_status": exit_status,
        "transcript_bytes": 0,
        "stderr_bytes": 0,
        "cleanup_proved": cleanup_proved,
    }


@pytest.mark.parametrize(
    ("outcome", "stage", "row_reason"),
    [
        ("timeout", "agent timeout", "timeout"),
        ("transcript-limit", "transcript limit", "transcript-limit"),
        ("stderr-limit", "stderr limit", "stderr-limit"),
    ],
)
def test_agent_check_reports_precise_bounded_outcome_and_continues(
    tmp_path: pathlib.Path,
    outcome: str,
    stage: str,
    row_reason: str,
) -> None:
    bounded_result = bounded_fixture_result(outcome)
    result, calls, _ = run_agent_check(
        tmp_path,
        clients={"pi": VALID_PI_STUB},
        runner_results=[bounded_result, bounded_result],
    )

    assert result.returncode != 0
    assert count_rows(result.stdout, "FAIL client=pi") == 2
    assert result.stdout.count(f"reason={row_reason}") == 2
    assert result.stderr.count(f"Verdict: FAIL stage={stage}") == 2
    assert sum(
        " run-agent-bounded " in line for line in calls.read_text().splitlines()
    ) == 2


@pytest.mark.parametrize(
    "bounded_result",
    [
        bounded_fixture_result("boundary-failure", cleanup_proved=False),
        bounded_fixture_result("timeout", cleanup_proved=False),
    ],
)
def test_agent_check_aborts_later_cells_without_proved_cleanup(
    tmp_path: pathlib.Path,
    bounded_result: dict[str, object],
) -> None:
    result, calls, _ = run_agent_check(
        tmp_path,
        clients={"pi": VALID_PI_STUB},
        model_aliases=("gemma4", "ornith"),
        runner_results=[bounded_result],
    )

    recorded = calls.read_text()
    assert result.returncode != 0
    assert count_rows(result.stdout, "FAIL client=pi") == 1
    assert "FAIL client=pi model=gemma4 check=weather reason=boundary-failure" in result.stdout
    assert "Verdict: FAIL stage=resource boundary" in result.stderr
    assert sum(
        " run-agent-bounded " in line for line in recorded.splitlines()
    ) == 1
    assert "https://open.er-api.com/" not in recorded
    assert "model=ornith" not in result.stdout
```

- [ ] **Step 4: Run the new shell tests and verify direct client execution cannot satisfy them**

Run:

```bash
uv run --with pytest pytest \
  tests/test_shell.py::test_agent_check_reports_precise_bounded_outcome_and_continues \
  tests/test_shell.py::test_agent_check_aborts_later_cells_without_proved_cleanup \
  -v
```

Expected: pytest exits nonzero because `scripts/check-with-agents.sh` does not invoke `run-agent-bounded`, does not map bounded outcomes, and does not abort the matrix on boundary uncertainty.

- [ ] **Step 5: Add strict bounded-result parsing and one boundary marker**

Change the prerequisite line near the top of `scripts/check-with-agents.sh` to:

```bash
require_cmd curl jq yq date uv head cat sed
```

Add these functions immediately before `run_agent()` in `scripts/check-with-agents.sh`:

```bash
parse_bounded_result() {
    jq -er '
        select(
            type == "object"
            and keys == ["cleanup_proved", "exit_status", "outcome", "schema", "stderr_bytes", "transcript_bytes"]
            and .schema == 1
            and (
                .outcome == "completed"
                or .outcome == "timeout"
                or .outcome == "transcript-limit"
                or .outcome == "stderr-limit"
                or .outcome == "boundary-failure"
            )
            and (
                .exit_status == null
                or (
                    (.exit_status | type) == "number"
                    and .exit_status == (.exit_status | floor)
                )
            )
            and (.transcript_bytes | type) == "number"
            and .transcript_bytes == (.transcript_bytes | floor)
            and .transcript_bytes >= 0
            and (.stderr_bytes | type) == "number"
            and .stderr_bytes == (.stderr_bytes | floor)
            and .stderr_bytes >= 0
            and (.cleanup_proved | type) == "boolean"
        )
        | [
            .outcome,
            (if .exit_status == null then "null" else (.exit_status | tostring) end),
            (.cleanup_proved | tostring)
          ]
        | @tsv
    '
}

mark_agent_boundary_failure() {
    AGENT_FAILURE_STAGE="resource boundary"
    AGENT_FAILURE_REASON="scope setup or cleanup could not be proved"
    AGENT_RESULT_REASON="boundary-failure"
    AGENT_ABORT_MATRIX=1
}
```

- [ ] **Step 6: Replace both client pipelines with one inherited-environment bounded invocation**

Replace the complete existing `run_agent()` function in `scripts/check-with-agents.sh` with this function:

```bash
run_agent() {
    local client="$1"
    local alias="$2"
    local prompt="$3"
    local transcript_file="$4"
    local stderr_file="$5"
    local final_file="$6"
    local parser_error_file="$7"
    local client_base config_dir config_file config_key_command
    local runner_json runner_fields runner_status outcome exit_status cleanup_proved
    local quoted_prompt
    local -a client_command

    AGENT_FAILURE_STAGE="command exit"
    AGENT_FAILURE_REASON="agent invocation failed"
    AGENT_RESULT_REASON="agent-failed"
    AGENT_ABORT_MATRIX=0
    AGENT_EXIT_STATUS="NOT RUN"
    AGENT_CLIENT_BASE="http://llm.local:${port}/v1"
    client_base="$AGENT_CLIENT_BASE"
    printf -v quoted_prompt '%q' "$prompt"
    DISPLAYED_CLIENT_COMMAND=""

    case "$client" in
        pi)
            config_dir="$workspace/pi"
            config_file="$config_dir/models.json"
            DISPLAYED_CLIENT_COMMAND="PI_CODING_AGENT_DIR=<private> pi --no-session --no-extensions --no-skills --no-prompt-templates --no-context-files --tools bash -p --mode json --model llm-env/${alias} ${quoted_prompt}"
            mkdir -p "$config_dir" || return 1
            chmod 700 "$config_dir" || return 1
            printf -v config_key_command "!yq -r '.server.api_key' %q" "$CONFIG_PATH"
            jq -n \
                --arg base_url "$client_base" \
                --arg api_key_command "$config_key_command" \
                --arg alias "$alias" \
                '{providers: {"llm-env": {
                    baseUrl: $base_url,
                    api: "openai-completions",
                    apiKey: $api_key_command,
                    compat: {supportsDeveloperRole: false, supportsReasoningEffort: false},
                    models: [{id: $alias}]
                }}}' > "$config_file" || return 1
            chmod 600 "$config_file" || return 1
            client_command=(
                pi
                --no-session
                --no-extensions
                --no-skills
                --no-prompt-templates
                --no-context-files
                --tools bash
                -p
                --mode json
                --model "llm-env/${alias}"
                "$prompt"
            )
            ;;
        opencode)
            config_dir="$workspace/opencode"
            config_file="$config_dir/opencode.jsonc"
            DISPLAYED_CLIENT_COMMAND="HOME=<private> XDG_CONFIG_HOME=<private> XDG_DATA_HOME=<private> XDG_STATE_HOME=<private> OPENCODE_CONFIG=<private> OPENCODE_API_KEY=<redacted> opencode run --format json --model llm-env/${alias} ${quoted_prompt}"
            mkdir -p "$config_dir" "$workspace/opencode-home" "$workspace/opencode-config" "$workspace/opencode-data" "$workspace/opencode-state" || return 1
            chmod 700 "$config_dir" "$workspace/opencode-home" "$workspace/opencode-config" "$workspace/opencode-data" "$workspace/opencode-state" || return 1
            jq -n \
                --arg base_url "$client_base" \
                --arg alias "$alias" \
                '{"$schema": "https://opencode.ai/config.json", tools: {"*": false, bash: true}, provider: {"llm-env": {
                    npm: "@ai-sdk/openai-compatible",
                    name: "llm-env",
                    options: {baseURL: $base_url, apiKey: "{env:OPENCODE_API_KEY}"},
                    models: {($alias): {name: $alias}}
                }}}' > "$config_file" || return 1
            chmod 600 "$config_file" || return 1
            client_command=(
                opencode run
                --format json
                --model "llm-env/${alias}"
                "$prompt"
            )
            ;;
        *)
            printf '%s\n' 'unsupported client' >"$stderr_file"
            return 1
            ;;
    esac

    runner_json="$(
        (
            cd "$workspace" || exit 1
            case "$client" in
                pi)
                    export PI_CODING_AGENT_DIR="$config_dir"
                    ;;
                opencode)
                    export HOME="$workspace/opencode-home"
                    export XDG_CONFIG_HOME="$workspace/opencode-config"
                    export XDG_DATA_HOME="$workspace/opencode-data"
                    export XDG_STATE_HOME="$workspace/opencode-state"
                    export OPENCODE_CONFIG="$config_file"
                    export OPENCODE_API_KEY="$api_key"
                    ;;
            esac
            uv run "${REPO_DIR}/llmenv.py" run-agent-bounded \
                --transcript "$transcript_file" \
                --stderr "$stderr_file" \
                -- "${client_command[@]}" </dev/null
        ) 2>>"$parser_error_file"
    )"
    runner_status=$?
    if [ "$runner_status" -ne 0 ]; then
        mark_agent_boundary_failure
        return 1
    fi
    if ! runner_fields="$(parse_bounded_result <<<"$runner_json" 2>>"$parser_error_file")"; then
        mark_agent_boundary_failure
        return 1
    fi
    IFS=$'\t' read -r outcome exit_status cleanup_proved <<<"$runner_fields"
    if [ "$exit_status" = null ]; then
        AGENT_EXIT_STATUS="NOT REPORTED"
    else
        AGENT_EXIT_STATUS="$exit_status"
    fi

    if [ "$outcome" = boundary-failure ] || [ "$cleanup_proved" != true ]; then
        mark_agent_boundary_failure
        return 1
    fi
    case "$outcome" in
        completed)
            if [ "$exit_status" = null ]; then
                mark_agent_boundary_failure
                return 1
            fi
            if [ "$exit_status" -ne 0 ]; then
                return 1
            fi
            ;;
        timeout)
            AGENT_FAILURE_STAGE="agent timeout"
            AGENT_FAILURE_REASON="bounded client runtime expired"
            AGENT_RESULT_REASON="timeout"
            return 1
            ;;
        transcript-limit)
            AGENT_FAILURE_STAGE="transcript limit"
            AGENT_FAILURE_REASON="client transcript exceeded 33554432 bytes"
            AGENT_RESULT_REASON="transcript-limit"
            return 1
            ;;
        stderr-limit)
            AGENT_FAILURE_STAGE="stderr limit"
            AGENT_FAILURE_REASON="client stderr exceeded 33554432 bytes"
            AGENT_RESULT_REASON="stderr-limit"
            return 1
            ;;
    esac

    case "$client" in
        pi)
            if ! jq -rce '
                [select(.type == "message_end" and .message.role == "assistant")
                 | [.message.content[]? | select(.type == "text") | .text] | join("")]
                | last // empty
            ' "$transcript_file" >"$final_file" 2>"$parser_error_file"; then
                AGENT_FAILURE_STAGE="response parsing"
                return 1
            fi
            ;;
        opencode)
            if ! jq -rce '
                reduce (select(.type == "text" and .part.type == "text") | .part) as $part
                ({message_id: null, text: ""};
                 if .message_id == $part.messageID then .text += $part.text
                 else {message_id: $part.messageID, text: $part.text}
                 end)
                | .text
            ' "$transcript_file" >"$final_file" 2>"$parser_error_file"; then
                AGENT_FAILURE_STAGE="response parsing"
                return 1
            fi
            ;;
    esac
}
```

- [ ] **Step 7: Emit precise reasons and stop all nested loops on boundary failure**

Replace the existing failed-agent logging and `continue` block at the end of each cell with this exact block:

```bash
            if [ "$agent_failed" -ne 0 ]; then
                log_nonempty_block "Client JSONL transcript" "$(<"$transcript_file")"
                log_nonempty_block "Client stderr" "$(<"$client_stderr_file")"
                log_nonempty_block "Agent parser stderr" "$(<"$parser_error_file")"
                log_nonempty_block "Final response" "$(<"$final_file")"
                log_error "Verdict: FAIL stage=${AGENT_FAILURE_STAGE} client=${client} model=${alias} check=${check_name} reason=${AGENT_FAILURE_REASON}"
                printf 'FAIL client=%s model=%s check=%s reason=%s\n' \
                    "$client" "$alias" "$check_name" "$AGENT_RESULT_REASON"
                failures=$((failures + 1))
                if [ "$AGENT_ABORT_MATRIX" -ne 0 ]; then
                    break 3
                fi
                continue
            fi
```

This step intentionally leaves file logging unchanged; Task 5 replaces those four content substitutions with streaming excerpts.

- [ ] **Step 8: Run all agent-check tests and verify existing success and alias behavior**

Run:

```bash
uv run --with pytest pytest tests/test_shell.py -k 'agent_check' -v
```

Expected: pytest exits zero; the new timeout, both stream-limit, and boundary-abort cases pass together with all existing success, prompt, parser, retained-artifact, client-configuration, stdin, timestamp, evidence, and unsafe-alias cases.

- [ ] **Step 9: Run the required combined gate**

Run:

```bash
make validate && make test
```

Expected: exit status `0`; ShellCheck and Ruff pass, `All checks passed.` is printed, and pytest reports no failures.

- [ ] **Step 10: Commit bounded harness dispatch**

```bash
git add scripts/check-with-agents.sh tests/test_shell.py
git commit -m "feat(agent-check): run clients in bounded scopes"
```

### Task 5: Bound Every Displayed Agent Diagnostic

**Files:**
- Modify: `scripts/check-with-agents.sh:40-43,88-121,418-445`
- Modify: `tests/test_shell.py:3015-3180,3239-3253,3826-3846`

**Interfaces:**
- Consumes: `log_file_excerpt LABEL FILE MAX_BYTES` from Task 3.
- Produces: at most 262,144 post-redaction bytes for source stdout, source stderr, source parser stderr, client transcript, client stderr, agent parser stderr, and final-response diagnostic blocks.
- Preserves: empty optional diagnostics remain omitted; required source stdout and successful final-response blocks retain their labels.

- [ ] **Step 1: Extend the fixture with a file-backed large stderr stream**

Add this parameter to `run_agent_check()` immediately after `agent_client_stderr`:

```python
    large_client_stderr: bool = False,
```

After creating `runner_counter`, create the diagnostic input without placing it in the environment:

```python
    large_client_stderr_file = tmp_path / "large-client-stderr"
    if large_client_stderr:
        large_client_stderr_file.write_bytes(
            (b"x" * 262_144) + b"not-displayed-after-excerpt-limit"
        )
    else:
        large_client_stderr_file.write_bytes(b"")
```

Add this environment entry:

```python
        "AGENT_CHECK_LARGE_CLIENT_STDERR_FILE": (
            str(large_client_stderr_file) if large_client_stderr else ""
        ),
```

- [ ] **Step 2: Make the Pi fixture emit the file-backed stderr stream**

Add this block immediately after the existing `AGENT_CHECK_CLIENT_STDERR` block in `VALID_PI_STUB`:

```bash
if [ -n "${AGENT_CHECK_LARGE_CLIENT_STDERR_FILE:-}" ]; then
    /usr/bin/cat "$AGENT_CHECK_LARGE_CLIENT_STDERR_FILE" >&2
fi
```

- [ ] **Step 3: Add a failing 262,144-byte integration test**

Add this test after `test_agent_check_keeps_nonempty_success_diagnostics()`:

```python
def test_agent_check_caps_displayed_agent_diagnostics_after_redaction(
    tmp_path: pathlib.Path,
) -> None:
    result, _, _ = run_agent_check(
        tmp_path,
        clients={"pi": VALID_PI_STUB},
        large_client_stderr=True,
    )

    assert result.returncode == 0, result.stderr
    assert "not-displayed-after-excerpt-limit" not in result.stdout + result.stderr
    rows = agent_rows(result.stdout)
    assert len(rows) == 2
    for row in rows:
        excerpt = row.split("Client stderr:\n  ", 1)[1].splitlines()[0]
        assert len(excerpt) == 262_144
        assert set(excerpt) == {"x"}
```

- [ ] **Step 4: Run the excerpt integration test and verify the sentinel leaks**

Run:

```bash
uv run --with pytest pytest \
  tests/test_shell.py::test_agent_check_caps_displayed_agent_diagnostics_after_redaction \
  -v
```

Expected: pytest exits nonzero because the existing `log_nonempty_block` prints `not-displayed-after-excerpt-limit` after byte 262,144.

- [ ] **Step 5: Define the one display-cap constant**

Add this constant immediately after the source URL assignments in `scripts/check-with-agents.sh`:

```bash
agent_diagnostic_excerpt_bytes=262144
```

- [ ] **Step 6: Replace every source-file content substitution with streaming excerpts**

Replace the logging body of the curl-failure branch in `snapshot_for()` with:

```bash
        log_file_excerpt "Source stdout" "$stdout_file" "$agent_diagnostic_excerpt_bytes"
        if [ -s "$stderr_file" ]; then
            log_file_excerpt "Source stderr" "$stderr_file" "$agent_diagnostic_excerpt_bytes"
        fi
        log_block "Exit status" "$status"
        log_error "Verdict: FAIL stage=source fetch reason=${check_name} source exited ${status}"
        return 1
```

Replace the logging body of the first source-body parse-failure branch with:

```bash
        log_file_excerpt "Source stdout" "$stdout_file" "$agent_diagnostic_excerpt_bytes"
        if [ -s "$stderr_file" ]; then
            log_file_excerpt "Source stderr" "$stderr_file" "$agent_diagnostic_excerpt_bytes"
        fi
        log_block "Exit status" "$status"
        if [ -s "$parser_stderr_file" ]; then
            log_file_excerpt "Source parser stderr" "$parser_stderr_file" "$agent_diagnostic_excerpt_bytes"
        fi
        log_error "Verdict: FAIL stage=source fetch reason=${check_name} source body is invalid"
        return 1
```

Replace the logging body of the FX timestamp-normalization failure branch with:

```bash
            log_file_excerpt "Source stdout" "$stdout_file" "$agent_diagnostic_excerpt_bytes"
            if [ -s "$stderr_file" ]; then
                log_file_excerpt "Source stderr" "$stderr_file" "$agent_diagnostic_excerpt_bytes"
            fi
            log_block "Exit status" "$status"
            if [ -s "$parser_stderr_file" ]; then
                log_file_excerpt "Source parser stderr" "$parser_stderr_file" "$agent_diagnostic_excerpt_bytes"
            fi
            log_error "Verdict: FAIL stage=source fetch reason=${check_name} source body is invalid"
            return 1
```

Replace the successful source diagnostic calls with:

```bash
    if [ -s "$stderr_file" ]; then
        log_file_excerpt "Source stderr" "$stderr_file" "$agent_diagnostic_excerpt_bytes"
    fi
    log_block "Exit status" "$status"
    if [ -s "$parser_stderr_file" ]; then
        log_file_excerpt "Source parser stderr" "$parser_stderr_file" "$agent_diagnostic_excerpt_bytes"
    fi
```

- [ ] **Step 7: Replace every agent-file diagnostic with a streaming excerpt**

Replace the failed-agent diagnostic block from Task 4 with:

```bash
            if [ "$agent_failed" -ne 0 ]; then
                if [ -s "$transcript_file" ]; then
                    log_file_excerpt "Client JSONL transcript" "$transcript_file" "$agent_diagnostic_excerpt_bytes"
                fi
                if [ -s "$client_stderr_file" ]; then
                    log_file_excerpt "Client stderr" "$client_stderr_file" "$agent_diagnostic_excerpt_bytes"
                fi
                if [ -s "$parser_error_file" ]; then
                    log_file_excerpt "Agent parser stderr" "$parser_error_file" "$agent_diagnostic_excerpt_bytes"
                fi
                if [ -s "$final_file" ]; then
                    log_file_excerpt "Final response" "$final_file" "$agent_diagnostic_excerpt_bytes"
                fi
                log_error "Verdict: FAIL stage=${AGENT_FAILURE_STAGE} client=${client} model=${alias} check=${check_name} reason=${AGENT_FAILURE_REASON}"
                printf 'FAIL client=%s model=%s check=%s reason=%s\n' \
                    "$client" "$alias" "$check_name" "$AGENT_RESULT_REASON"
                failures=$((failures + 1))
                if [ "$AGENT_ABORT_MATRIX" -ne 0 ]; then
                    break 3
                fi
                continue
            fi
```

Replace the successful comparison branch with:

```bash
            if [ -z "$differences" ]; then
                if [ -s "$client_stderr_file" ]; then
                    log_file_excerpt "Client stderr" "$client_stderr_file" "$agent_diagnostic_excerpt_bytes"
                fi
                if [ -s "$parser_error_file" ]; then
                    log_file_excerpt "Agent parser stderr" "$parser_error_file" "$agent_diagnostic_excerpt_bytes"
                fi
                log_file_excerpt "Final response" "$final_file" "$agent_diagnostic_excerpt_bytes"
                log_block "Validated" "$(log_validation_facts "$check_name" "$snapshot")"
                log_info "Verdict: PASS"
                printf 'PASS client=%s model=%s check=%s reason=agent-returned-json\n' \
                    "$client" "$alias" "$check_name"
                passes=$((passes + 1))
                continue
            fi
```

Replace the mismatch diagnostic calls immediately after that branch with:

```bash
            if [ -s "$transcript_file" ]; then
                log_file_excerpt "Client JSONL transcript" "$transcript_file" "$agent_diagnostic_excerpt_bytes"
            fi
            if [ -s "$client_stderr_file" ]; then
                log_file_excerpt "Client stderr" "$client_stderr_file" "$agent_diagnostic_excerpt_bytes"
            fi
            if [ -s "$parser_error_file" ]; then
                log_file_excerpt "Agent parser stderr" "$parser_error_file" "$agent_diagnostic_excerpt_bytes"
            fi
            if [ -s "$final_file" ]; then
                log_file_excerpt "Final response" "$final_file" "$agent_diagnostic_excerpt_bytes"
            fi
```

- [ ] **Step 8: Run the focused excerpt test and all agent-check regressions**

Run:

```bash
uv run --with pytest pytest tests/test_shell.py -k 'agent_check' -v
```

Expected: pytest exits zero; each large stderr block contains exactly 262,144 `x` bytes, the sentinel is absent from displayed output, and all agent-check regressions pass.

- [ ] **Step 9: Run the required combined gate**

Run:

```bash
make validate && make test
```

Expected: exit status `0`; ShellCheck and Ruff pass, `All checks passed.` is printed, and pytest reports no failures.

- [ ] **Step 10: Commit bounded diagnostic display**

```bash
git add scripts/check-with-agents.sh tests/test_shell.py
git commit -m "fix(agent-check): cap displayed diagnostics"
```

### Task 6: Reject Oversized Source and Final-Response Inputs

**Files:**
- Modify: `scripts/check-with-agents.sh:15,40-49,80-84,275-290,393,399-409`
- Modify: `tests/test_shell.py:3015-3180,3111-3143,3239-3266,3979-4027`

**Interfaces:**
- Consumes: curl `--max-filesize 1048576`, `wc -c`, and file-based `parse_evidence ASSISTANT_FILE`.
- Produces: model-discovery and public-source infrastructure failures when curl rejects a response over 1,048,576 bytes.
- Produces: `stage=final response limit`, `reason=final assistant text exceeded 1048576 bytes`, and structured `reason=final-response-limit` before evidence validation sees oversized assistant text.
- Preserves: both prompts byte-for-byte, the exact fence and single-object validators, source comparison functions, and source timestamp handling.

- [ ] **Step 1: Add file-backed oversized-source and final-response controls to the fixture**

Add these parameters to `run_agent_check()` immediately after `large_client_stderr`:

```python
    oversized_source: str | None = None,
    oversized_final_response: bool = False,
```

After creating `large_client_stderr_file`, create bounded fixture inputs:

```python
    oversized_source_file = tmp_path / "oversized-source"
    oversized_source_file.write_bytes(b"s" * 1_048_577)
    response_prefix_file = tmp_path / "agent-response-prefix"
    if oversized_final_response:
        response_prefix_file.write_bytes(b"r" * 1_048_577)
    else:
        response_prefix_file.write_text(agent_response_prefix)
```

Replace the `AGENT_CHECK_RESPONSE_PREFIX` environment entry with these entries:

```python
        "AGENT_CHECK_RESPONSE_PREFIX_FILE": str(response_prefix_file),
        "AGENT_CHECK_OVERSIZED_SOURCE": oversized_source or "",
        "AGENT_CHECK_OVERSIZED_SOURCE_FILE": str(oversized_source_file),
```

- [ ] **Step 2: Make the fake curl require the one-MiB option and emulate curl status 63**

Replace the complete fake `curl` block in `run_agent_check()` with:

```python
    curl = commands / "curl"
    curl.write_text(
        "#!/usr/bin/bash\n"
        "printf 'curl %s\\n' \"$*\" >> \"$CALLS\"\n"
        "case \" $* \" in *' --max-filesize 1048576 '*) ;; *) exit 62 ;; esac\n"
        "url=\"${!#}\"\n"
        "case \"$url\" in\n"
        "  */v1/models) printf '%s\\n' \"$AGENT_CHECK_MODELS\" ;;\n"
        "  https://api.open-meteo.com/*)\n"
        "    if [ \"$AGENT_CHECK_OVERSIZED_SOURCE\" = weather ] && [ \"$(/usr/bin/wc -c < \"$AGENT_CHECK_OVERSIZED_SOURCE_FILE\")\" -gt 1048576 ]; then\n"
        "      printf '%s\\n' 'curl: (63) Maximum file size exceeded' >&2\n"
        "      exit 63\n"
        "    fi\n"
        "    if [ -n \"$AGENT_CHECK_WEATHER_BODY\" ]; then\n"
        "      printf '%s\\n' \"$AGENT_CHECK_WEATHER_BODY\"\n"
        "    else\n"
        "      count=$(< \"$AGENT_CHECK_SOURCE_COUNTER\")\n"
        "      count=$((count + 1))\n"
        "      printf '%s\\n' \"$count\" > \"$AGENT_CHECK_SOURCE_COUNTER\"\n"
        "      printf -v seconds '%02d' \"$count\"\n"
        "      jq -cn --arg seconds \"$seconds\" '{current:{time:(\"2026-07-27T13:00:\" + $seconds),temperature_2m:16.3,weather_code:3}}'\n"
        "    fi\n"
        "    ;;\n"
        "  https://open.er-api.com/*)\n"
        "    if [ \"$AGENT_CHECK_OVERSIZED_SOURCE\" = fx ] && [ \"$(/usr/bin/wc -c < \"$AGENT_CHECK_OVERSIZED_SOURCE_FILE\")\" -gt 1048576 ]; then\n"
        "      printf '%s\\n' 'curl: (63) Maximum file size exceeded' >&2\n"
        "      exit 63\n"
        "    fi\n"
        "    if [ -n \"$AGENT_CHECK_FX_BODY\" ]; then\n"
        "      printf '%s\\n' \"$AGENT_CHECK_FX_BODY\"\n"
        "    else\n"
        "      count=$(< \"$AGENT_CHECK_SOURCE_COUNTER\")\n"
        "      count=$((count + 1))\n"
        "      printf '%s\\n' \"$count\" > \"$AGENT_CHECK_SOURCE_COUNTER\"\n"
        "      printf -v seconds '%02d' \"$count\"\n"
        "      jq -cn --arg seconds \"$seconds\" '{time_last_update_utc:(\"2026-07-27T00:00:\" + $seconds),rates:{CLP:946.527902}}'\n"
        "    fi\n"
        "    ;;\n"
        "  *) exit 64 ;;\n"
        "esac\n"
    )
    curl.chmod(curl.stat().st_mode | stat.S_IXUSR)
```

- [ ] **Step 3: Make both valid client fakes read the response prefix from a file**

Replace the final `jq` command in `VALID_PI_STUB` with:

```bash
jq -cn --rawfile response_prefix "$AGENT_CHECK_RESPONSE_PREFIX_FILE" --arg response_suffix "$AGENT_CHECK_RESPONSE_SUFFIX" --argjson evidence "$evidence" '{type:"message_end",message:{role:"assistant",content:[{type:"text",text:($response_prefix + ($evidence | tojson) + $response_suffix)}]}}'
```

Replace the final `jq` command in `VALID_OPENCODE_STUB` with:

```bash
jq -cn --rawfile response_prefix "$AGENT_CHECK_RESPONSE_PREFIX_FILE" --arg response_suffix "$AGENT_CHECK_RESPONSE_SUFFIX" --argjson evidence "$evidence" '{type:"text",part:{type:"text",text:($response_prefix + ($evidence | tojson) + $response_suffix)}}'
```

- [ ] **Step 4: Add the oversized public-source rejection test**

Add this test after `test_agent_check_rejects_unparseable_typed_fx_source_timestamp()`:

```python
def test_agent_check_rejects_public_source_larger_than_one_mib(
    tmp_path: pathlib.Path,
) -> None:
    result, calls, _ = run_agent_check(
        tmp_path,
        clients={"pi": VALID_PI_STUB},
        oversized_source="weather",
    )

    recorded = calls.read_text()
    assert result.returncode != 0
    assert "--max-filesize 1048576" in recorded
    assert "Verdict: FAIL stage=source fetch reason=weather source exited 63" in result.stderr
    assert "curl: (63) Maximum file size exceeded" in result.stdout
```

- [ ] **Step 5: Add the oversized final-response rejection test**

Add this test immediately after the source-size test:

```python


def test_agent_check_rejects_final_response_larger_than_one_mib_before_evidence(
    tmp_path: pathlib.Path,
) -> None:
    result, _, _ = run_agent_check(
        tmp_path,
        clients={"pi": VALID_PI_STUB},
        oversized_final_response=True,
    )

    assert result.returncode != 0
    assert count_rows(result.stdout, "FAIL client=pi") == 2
    assert result.stdout.count("reason=final-response-limit") == 2
    assert result.stderr.count("Verdict: FAIL stage=final response limit") == 2
    assert "stage=agent evidence parsing" not in result.stderr
```

- [ ] **Step 6: Lock the unchanged client prompt commands**

Keep these exact assertions in `test_agent_check_prompt_requires_literal_source_evidence()` so harness-owned fetch caps cannot leak into the model prompt:

```python
    assert any(
        "curl -fsS --max-time 20 -- 'https://api.open-meteo.com/v1/forecast?"
        in prompt
        for prompt in prompts
    )
    assert any(
        "curl -fsS --max-time 20 -- 'https://open.er-api.com/v6/latest/USD'"
        in prompt
        for prompt in prompts
    )
```

- [ ] **Step 7: Run the new tests and verify all three missing bounds fail**

Run:

```bash
uv run --with pytest pytest \
  tests/test_shell.py::test_agent_check_rejects_public_source_larger_than_one_mib \
  tests/test_shell.py::test_agent_check_rejects_final_response_larger_than_one_mib_before_evidence \
  tests/test_shell.py::test_agent_check_prompt_requires_literal_source_evidence \
  -v
```

Expected: pytest exits nonzero because the fake model-discovery curl rejects the missing `--max-filesize 1048576` and the final response has no one-MiB infrastructure gate. The prompt regression passes unchanged.

- [ ] **Step 8: Add source and final-response constants and require the new commands**

Change the command prerequisite line in `scripts/check-with-agents.sh` to:

```bash
require_cmd curl jq yq date uv head cat sed wc
```

Add these constants beside `agent_diagnostic_excerpt_bytes`:

```bash
source_response_limit_bytes=1048576
final_response_limit_bytes=1048576
```

- [ ] **Step 9: Add curl's cap only to harness-owned fetches and displayed commands**

Replace `fetch_models()` with:

```bash
fetch_models() {
    local config_file="$1"
    local server_base="$2"

    curl -fsS --max-time 20 --max-filesize "$source_response_limit_bytes" \
        -K "$config_file" "${server_base}/v1/models" |
        jq -er '.data[]?.id'
}
```

Replace the source display and fetch lines in `snapshot_for()` with:

```bash
    log_command "curl --fail --silent --show-error --max-time 20 --max-filesize ${source_response_limit_bytes} ${url}"
    log_block "Input" "(none)"
    if curl -fsS --max-time 20 --max-filesize "$source_response_limit_bytes" \
        "$url" >"$stdout_file" 2>"$stderr_file"; then
```

Do not edit the prompt assignment. Its client-side command remains `curl -fsS --max-time 20 -- '${source_url}'`.

- [ ] **Step 10: Change evidence parsing to consume a bounded file directly**

Replace `parse_evidence()` with:

```bash
parse_evidence() {
    local assistant_file="$1"

    if jq -Rse 'test("^[[:space:]]*```json")' "$assistant_file" >/dev/null; then
        jq -Rrse \
            'capture("^[[:space:]]*```json\\r?\\n(?<body>[\\s\\S]*?)\\r?\\n```[[:space:]]*$").body' \
            "$assistant_file" |
            jq -sce 'select(length == 1 and (.[0] | type == "object")) | .[0]'
        return
    fi
    jq -sce 'select(length == 1 and (.[0] | type == "object")) | .[0]' \
        "$assistant_file"
}
```

This preserves the existing anchored JSON-fence capture and the exact one-object validation. It removes only the full final-response shell variable and function argument.

- [ ] **Step 11: Gate final assistant bytes before calling the unchanged evidence validators**

Replace the cell's `run_agent` and `parse_evidence` decision block with:

```bash
            if run_agent "$client" "$alias" "$prompt" "$transcript_file" \
                "$client_stderr_file" "$final_file" "$parser_error_file"; then
                final_bytes="$(wc -c < "$final_file")"
                if [ "$final_bytes" -gt "$final_response_limit_bytes" ]; then
                    AGENT_FAILURE_STAGE="final response limit"
                    AGENT_FAILURE_REASON="final assistant text exceeded 1048576 bytes"
                    AGENT_RESULT_REASON="final-response-limit"
                    agent_failed=1
                elif ! evidence="$(parse_evidence "$final_file" 2>>"$parser_error_file")"; then
                    AGENT_FAILURE_STAGE="agent evidence parsing"
                    agent_failed=1
                fi
            else
                agent_failed=1
            fi
```

`final_bytes` contains only a decimal count. The final response remains in its private file and never enters a shell variable or argv before validation.

- [ ] **Step 12: Run every agent-check regression and verify bounds do not weaken validators**

Run:

```bash
uv run --with pytest pytest tests/test_shell.py -k 'agent_check' -v
```

Expected: pytest exits zero; the oversized source and final response fail at infrastructure stages, while all exact source URL, timestamp, numeric evidence, fence, extra-value, stale-evidence, success, and unsafe-alias tests still pass.

- [ ] **Step 13: Run the required combined repository gate**

Run:

```bash
make validate && make test
```

Expected: exit status `0`; ShellCheck and Ruff pass, `All checks passed.` is printed, and the complete pytest suite reports no failures.

- [ ] **Step 14: Run a harmless live one-second scope timeout fixture**

Run from `/var/home/bazzite/git/llm-env`:

```bash
preflight_dir="$(mktemp -d)"
trap 'rm -rf -- "$preflight_dir"' EXIT
chmod 700 "$preflight_dir"
install -m 600 /dev/null "$preflight_dir/transcript"
install -m 600 /dev/null "$preflight_dir/client-stderr"
preflight_result="$(uv run llmenv.py run-agent-bounded \
    --transcript "$preflight_dir/transcript" \
    --stderr "$preflight_dir/client-stderr" \
    --runtime-seconds 1 \
    --grace-seconds 1 \
    --stream-limit-bytes 1048576 \
    -- /usr/bin/sleep 2)"
jq -e '
    select(
        .schema == 1
        and .outcome == "timeout"
        and .transcript_bytes == 0
        and .stderr_bytes == 0
        and .cleanup_proved == true
    )
' <<<"$preflight_result"
```

Expected: jq prints the six-field result object and exits `0`; `outcome` is `timeout`, both stored byte counts are `0`, and `cleanup_proved` is `true`. Any `boundary-failure`, nonzero jq status, active saved cgroup, or unremoved preflight process blocks the next step.

- [ ] **Step 15: Run both installed client executables through the production runner**

Run:

```bash
preflight_dir="$(mktemp -d)"
trap 'rm -rf -- "$preflight_dir"' EXIT
chmod 700 "$preflight_dir"
for client in pi opencode; do
    command -v "$client"
    install -m 600 /dev/null "$preflight_dir/${client}.stdout"
    install -m 600 /dev/null "$preflight_dir/${client}.stderr"
    result="$(uv run llmenv.py run-agent-bounded \
        --transcript "$preflight_dir/${client}.stdout" \
        --stderr "$preflight_dir/${client}.stderr" \
        -- "$client" --version)"
    jq -e '
        select(
            .schema == 1
            and .outcome == "completed"
            and .exit_status == 0
            and .cleanup_proved == true
        )
    ' <<<"$result"
done
```

Expected: both command lookups print installed executable paths; each jq invocation prints one six-field `completed` result with exit status `0` and proved cleanup. This does not contact a model, edit sampling configuration, rotate candidates, run screening, or record a sampler streak.

- [ ] **Step 16: Commit the input bounds after repository and live verification**

```bash
git add scripts/check-with-agents.sh tests/test_shell.py
git commit -m "fix(agent-check): reject oversized agent inputs"
```
