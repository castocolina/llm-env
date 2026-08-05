"""Run one agent client inside a bounded transient systemd scope."""

from __future__ import annotations

import errno
import math
import os
import queue
import secrets
import signal
import stat
import subprocess
import threading
import time
from collections.abc import Sequence
from contextlib import ExitStack
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Literal, Protocol

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


class _CgroupRemovalInProgress(BoundaryError):
    """The saved cgroup path is being deactivated or removed."""


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
        if (
            limits.runtime_seconds > DEFAULT_RUNTIME_SECONDS
            or limits.grace_seconds > DEFAULT_GRACE_SECONDS
            or limits.stream_limit_bytes > DEFAULT_STREAM_LIMIT_BYTES
        ):
            raise BoundaryError("run limits exceed production maxima")
        runtime = f"{Decimal(str(limits.runtime_seconds)).normalize():f}s"
        try:
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
        except OSError as exc:
            raise BoundaryError("could not launch transient scope") from exc

    def cgroup_path(self, unit_name: str, timeout_seconds: float) -> Path | None:
        try:
            result = subprocess.run(
                [
                    "systemctl",
                    "--user",
                    "show",
                    unit_name,
                    "--property=LoadState",
                    "--property=ControlGroup",
                    "--all",
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout_seconds,
            )
        except (OSError, subprocess.SubprocessError, UnicodeError) as exc:
            raise BoundaryError("could not query transient scope") from exc
        if result.returncode != 0:
            raise BoundaryError("systemd rejected transient scope query")

        fields: dict[str, str] = {}
        expected_fields = {"LoadState", "ControlGroup"}
        for line in result.stdout.splitlines():
            name, separator, value = line.partition("=")
            if not separator or name not in expected_fields or name in fields:
                raise BoundaryError("malformed transient scope query")
            fields[name] = value
        if fields.keys() != expected_fields:
            raise BoundaryError("incomplete transient scope query")

        load_state = fields["LoadState"]
        relative_text = fields["ControlGroup"]
        if load_state == "not-found":
            if relative_text:
                raise BoundaryError("missing scope returned a cgroup path")
            return None
        if load_state != "loaded" or not relative_text:
            raise BoundaryError("transient scope has no usable cgroup path")

        relative = PurePosixPath(relative_text)
        if (
            not relative.is_absolute()
            or relative.root != "/"
            or relative == PurePosixPath("/")
            or relative.as_posix() != relative_text
            or ".." in relative.parts
        ):
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
                if exc.errno == errno.ENODEV:
                    raise _CgroupRemovalInProgress(
                        "saved cgroup path removal is in progress"
                    ) from exc
                raise BoundaryError("could not inspect saved cgroup path") from exc
            raise BoundaryError("cgroup.events is absent from the saved cgroup path")
        except OSError as exc:
            if exc.errno == errno.ENODEV:
                raise _CgroupRemovalInProgress(
                    "saved cgroup path removal is in progress"
                ) from exc
            raise BoundaryError("could not read cgroup.events") from exc
        except UnicodeError as exc:
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


def _close_streams(streams: Sequence[BinaryIO | None]) -> bool:
    close_succeeded = True
    for stream in streams:
        if stream is None:
            continue
        try:
            stream.close()
        except OSError:
            close_succeeded = False
    return close_succeeded


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


def _poll_process(
    process: subprocess.Popen[bytes],
) -> tuple[int | None, bool]:
    try:
        return process.poll(), False
    except (OSError, subprocess.SubprocessError):
        return None, True


def _execution_finished(
    process: subprocess.Popen[bytes],
    threads: Sequence[threading.Thread],
) -> tuple[bool, bool]:
    exit_status, poll_failed = _poll_process(process)
    return (
        exit_status is not None and not any(thread.is_alive() for thread in threads),
        poll_failed,
    )


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
    execution_finished, poll_failed = _execution_finished(process, threads)
    if poll_failed:
        return False, True
    if not execution_finished:
        return False, False
    if cgroup_path is None:
        return False, True
    try:
        return backend.cgroup_empty(cgroup_path), False
    except _CgroupRemovalInProgress:
        return False, False
    except BoundaryError:
        return False, True


def _reap_local_launcher(
    process: subprocess.Popen[bytes],
    threads: Sequence[threading.Thread],
    deadline: float,
    grace_seconds: float,
) -> bool:
    try:
        while time.monotonic() < deadline:
            execution_finished, poll_failed = _execution_finished(process, threads)
            if poll_failed:
                return False
            if execution_finished:
                return True
            time.sleep(_POLL_SECONDS)

        exit_status, poll_failed = _poll_process(process)
        if poll_failed:
            return False
        if exit_status is None:
            process.terminate()
            try:
                process.wait(timeout=grace_seconds)
            except subprocess.TimeoutExpired:
                process.kill()
                try:
                    process.wait(timeout=grace_seconds)
                except subprocess.TimeoutExpired:
                    return False
        for thread in threads:
            thread.join(timeout=_POLL_SECONDS * 10)
        execution_finished, poll_failed = _execution_finished(process, threads)
        return execution_finished and not poll_failed
    except (OSError, subprocess.SubprocessError):
        return False


def _wait_for_cleanup(
    backend: ScopeBackend,
    cgroup_path: Path | None,
    process: subprocess.Popen[bytes],
    threads: Sequence[threading.Thread],
    deadline: float,
    grace_seconds: float,
) -> tuple[bool, bool, bool]:
    if cgroup_path is None:
        return False, True, False
    boundary_failed = False
    while time.monotonic() < deadline:
        try:
            scope_empty = backend.cgroup_empty(cgroup_path)
        except _CgroupRemovalInProgress:
            pass
        except BoundaryError:
            boundary_failed = True
        else:
            if scope_empty:
                reaped = _reap_local_launcher(
                    process,
                    threads,
                    deadline,
                    grace_seconds,
                )
                return reaped, boundary_failed or not reaped, False
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(_POLL_SECONDS, remaining))

    try:
        scope_empty = backend.cgroup_empty(cgroup_path)
    except _CgroupRemovalInProgress:
        return False, True, False
    except BoundaryError:
        return False, True, False
    if scope_empty:
        reaped = _reap_local_launcher(
            process,
            threads,
            deadline,
            grace_seconds,
        )
        return reaped, boundary_failed or not reaped, False
    return False, boundary_failed, True


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
            boundary_failed = True
        term_deadline = time.monotonic() + limits.grace_seconds
        while time.monotonic() < term_deadline:
            exit_status, poll_failed = _poll_process(process)
            if poll_failed:
                boundary_failed = True
                break
            if exit_status is not None:
                break
            time.sleep(_POLL_SECONDS)
        _reap_local_launcher(
            process,
            threads,
            time.monotonic() + limits.grace_seconds,
            limits.grace_seconds,
        )
        return False, True

    try:
        backend.signal_scope(unit_name, signal.SIGTERM, limits.grace_seconds)
    except BoundaryError:
        boundary_failed = True
    term_deadline = time.monotonic() + limits.grace_seconds
    cleanup_proved, proof_failed, scope_populated = _wait_for_cleanup(
        backend,
        cgroup_path,
        process,
        threads,
        term_deadline,
        limits.grace_seconds,
    )
    boundary_failed = boundary_failed or proof_failed
    if cleanup_proved or not scope_populated:
        return cleanup_proved, boundary_failed

    try:
        backend.signal_scope(unit_name, signal.SIGKILL, limits.grace_seconds)
    except BoundaryError:
        boundary_failed = True
    cleanup_proved, proof_failed, _ = _wait_for_cleanup(
        backend,
        cgroup_path,
        process,
        threads,
        time.monotonic() + limits.grace_seconds,
        limits.grace_seconds,
    )
    return cleanup_proved, boundary_failed or proof_failed or not cleanup_proved


def _make_result(
    outcome: Outcome,
    process: subprocess.Popen[bytes] | None,
    transcript_state: _DrainState,
    stderr_state: _DrainState,
    cleanup_proved: bool,
) -> BoundedRunResult:
    exit_status: int | None = None
    poll_failed = False
    if process is not None:
        exit_status, poll_failed = _poll_process(process)
    if poll_failed:
        outcome = "boundary-failure"
        cleanup_proved = False
    return BoundedRunResult(
        outcome=outcome,
        exit_status=exit_status,
        transcript_bytes=transcript_state.stored_bytes,
        stderr_bytes=stderr_state.stored_bytes,
        cleanup_proved=cleanup_proved,
    )


def _close_outputs_and_make_result(
    stack: ExitStack,
    outcome: Outcome,
    process: subprocess.Popen[bytes] | None,
    transcript_state: _DrainState,
    stderr_state: _DrainState,
    cleanup_proved: bool,
    outputs: Sequence[BinaryIO | None],
) -> BoundedRunResult:
    stack.pop_all()
    if not _close_streams(outputs):
        outcome = "boundary-failure"
    return _make_result(
        outcome,
        process,
        transcript_state,
        stderr_state,
        cleanup_proved,
    )


def run_bounded_agent(
    command: Sequence[str],
    transcript_path: Path,
    stderr_path: Path,
    *,
    limits: RunLimits = RunLimits(),  # noqa: B008
    backend: ScopeBackend | None = None,
) -> BoundedRunResult:
    """Run one command and return only bounded process facts."""
    transcript_state = _DrainState()
    stderr_state = _DrainState()
    if not command:
        return _make_result(
            "boundary-failure", None, transcript_state, stderr_state, False
        )
    if (
        limits.runtime_seconds > DEFAULT_RUNTIME_SECONDS
        or limits.grace_seconds > DEFAULT_GRACE_SECONDS
        or limits.stream_limit_bytes > DEFAULT_STREAM_LIMIT_BYTES
    ):
        return _make_result(
            "boundary-failure", None, transcript_state, stderr_state, False
        )

    selected_backend = backend or SystemdScopeBackend()
    unit_name = f"llm-env-agent-{secrets.token_hex(16)}.scope"
    started_at = time.monotonic()
    runtime_deadline = started_at + limits.runtime_seconds
    process: subprocess.Popen[bytes] | None = None
    transcript_output: BinaryIO | None = None
    stderr_output: BinaryIO | None = None
    cleanup_proved = False

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
            return _close_outputs_and_make_result(
                stack,
                "boundary-failure",
                process,
                transcript_state,
                stderr_state,
                False,
                (transcript_output, stderr_output),
            )

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
            if cgroup_path is None:
                exit_status, poll_failed = _poll_process(process)
                if poll_failed or exit_status is not None:
                    boundary_failed = True
                    break
            time.sleep(min(_POLL_SECONDS, remaining))

        if process.stdout is None or process.stderr is None:
            cleanup_proved, _ = _stop_and_prove(
                selected_backend,
                unit_name,
                cgroup_path,
                process,
                (),
                limits,
            )
            _close_streams((process.stdout, process.stderr))
            return _close_outputs_and_make_result(
                stack,
                "boundary-failure",
                process,
                transcript_state,
                stderr_state,
                cleanup_proved,
                (transcript_output, stderr_output),
            )

        sources = (process.stdout, process.stderr)
        events: queue.Queue[Outcome] = queue.Queue()
        transcript_thread = threading.Thread(
            target=_drain_stream,
            args=(
                sources[0],
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
                sources[1],
                stderr_output,
                limits.stream_limit_bytes,
                "stderr-limit",
                events,
                stderr_state,
            ),
            daemon=True,
        )
        threads = (transcript_thread, stderr_thread)

        # Do not drain client output until the cgroup path is saved. Kernel pipe
        # capacity back-pressures a fast writer without allowing a stream limit
        # to race ahead of the cleanup identity needed to stop it safely.
        started_threads: list[threading.Thread] = []
        try:
            for thread in threads:
                thread.start()
                started_threads.append(thread)
        except (OSError, RuntimeError):
            cleanup_proved, _ = _stop_and_prove(
                selected_backend,
                unit_name,
                cgroup_path,
                process,
                started_threads,
                limits,
            )
            _close_streams(sources[len(started_threads) :])
            return _close_outputs_and_make_result(
                stack,
                "boundary-failure",
                process,
                transcript_state,
                stderr_state,
                cleanup_proved,
                (transcript_output, stderr_output),
            )

        if cgroup_path is None:
            cleanup_proved, stop_failed = _stop_and_prove(
                selected_backend,
                unit_name,
                None,
                process,
                threads,
                limits,
            )
            return _close_outputs_and_make_result(
                stack,
                "boundary-failure",
                process,
                transcript_state,
                stderr_state,
                cleanup_proved and not (boundary_failed or stop_failed),
                (transcript_output, stderr_output),
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
                execution_finished, poll_failed = _execution_finished(process, threads)
                if poll_failed:
                    boundary_failed = True
                    pending_outcome = "boundary-failure"
                    break
                if execution_finished:
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
                        return _close_outputs_and_make_result(
                            stack,
                            "completed",
                            process,
                            transcript_state,
                            stderr_state,
                            True,
                            (transcript_output, stderr_output),
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
        return _close_outputs_and_make_result(
            stack,
            final_outcome,
            process,
            transcript_state,
            stderr_state,
            cleanup_proved,
            (transcript_output, stderr_output),
        )
