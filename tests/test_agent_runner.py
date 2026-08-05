from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pylib import agent_runner
from pylib.agent_runner import (
    BoundaryError,
    BoundedRunResult,
    RunLimits,
    SystemdScopeBackend,
    run_bounded_agent,
)


class FakeBackend:
    def __init__(
        self,
        *,
        cleanup_error: bool = False,
        cleanup_errors: int = 0,
        cgroup_empty_override: bool | None = None,
        cgroup_observable: bool = True,
        synchronize_start: bool = False,
        signal_errors: set[signal.Signals] | None = None,
        wait_for_exit_before_cgroup: bool = False,
    ) -> None:
        self.cleanup_error = cleanup_error
        self.cleanup_errors = cleanup_errors
        self.cgroup_empty_override = cgroup_empty_override
        self.cgroup_observable = cgroup_observable
        self.synchronize_start = synchronize_start
        self.signal_errors = signal_errors or set()
        self.wait_for_exit_before_cgroup = wait_for_exit_before_cgroup
        self.start_calls = 0
        self.process: subprocess.Popen[bytes] | None = None
        self.unit_name: str | None = None
        self.signals: list[signal.Signals] = []
        self.signal_times: list[float] = []

    def start_scope(
        self,
        unit_name: str,
        command: Sequence[str],
        limits: RunLimits,
    ) -> subprocess.Popen[bytes]:
        self.start_calls += 1
        self.unit_name = unit_name
        self.process = subprocess.Popen(
            list(command),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        if self.synchronize_start:
            _, wait_status = os.waitpid(self.process.pid, os.WUNTRACED)
            if not os.WIFSTOPPED(wait_status):
                raise BoundaryError("fixture exited before signaling readiness")
            self.process.send_signal(signal.SIGCONT)
        return self.process

    def cgroup_path(self, unit_name: str, timeout_seconds: float) -> Path | None:
        assert timeout_seconds > 0
        assert unit_name == self.unit_name
        assert self.process is not None
        if not self.cgroup_observable:
            return None
        if self.wait_for_exit_before_cgroup:
            self.process.wait(timeout=timeout_seconds)
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
        self.signal_times.append(time.monotonic())
        if self.process.poll() is None:
            self.process.send_signal(requested_signal)
        if requested_signal in self.signal_errors:
            raise BoundaryError("fixture signal state unavailable")

    def cgroup_empty(self, cgroup_path: Path) -> bool:
        assert cgroup_path == Path("/fake-cgroup") / self.unit_name
        if self.cleanup_error or self.cleanup_errors > 0:
            self.cleanup_errors = max(0, self.cleanup_errors - 1)
            raise BoundaryError("fixture cleanup state unavailable")
        if self.cgroup_empty_override is not None:
            return self.cgroup_empty_override
        assert self.process is not None
        return self.process.poll() is not None


def private_output(path: Path) -> None:
    path.touch()
    path.chmod(0o600)


def sigterm_ignoring_code() -> str:
    return (
        "import os, signal, time\n"
        "signal.signal(signal.SIGTERM, lambda *_: None)\n"
        "os.kill(os.getpid(), signal.SIGSTOP)\n"
        "time.sleep(60)\n"
    )


def systemctl_result(
    stdout: str,
    *,
    returncode: int = 0,
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["systemctl"],
        returncode=returncode,
        stdout=stdout,
        stderr="",
    )


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


@pytest.mark.parametrize("exit_status", [0, 17])
def test_bounded_run_completes_when_saved_cgroup_outlives_fast_launcher(
    tmp_path: Path,
    exit_status: int,
) -> None:
    backend = FakeBackend(wait_for_exit_before_cgroup=True)

    result, _, _, _ = run_fixture(
        tmp_path,
        f"raise SystemExit({exit_status})",
        backend=backend,
    )

    assert result.outcome == "completed"
    assert result.exit_status == exit_status
    assert result.cleanup_proved is True
    assert backend.signals == []


@pytest.mark.parametrize(
    ("runtime_seconds", "grace_seconds", "stream_limit_bytes"),
    [
        (300.000001, 10.0, 33_554_432),
        (300.0, 10.000001, 33_554_432),
        (300.0, 10.0, 33_554_433),
    ],
)
def test_bounded_run_rejects_over_max_limits_before_injected_backend(
    tmp_path: Path,
    runtime_seconds: float,
    grace_seconds: float,
    stream_limit_bytes: int,
) -> None:
    backend = FakeBackend()

    result, _, _, _ = run_fixture(
        tmp_path,
        "",
        runtime_seconds=runtime_seconds,
        grace_seconds=grace_seconds,
        stream_limit_bytes=stream_limit_bytes,
        backend=backend,
    )

    assert result.to_dict() == {
        "schema": 1,
        "outcome": "boundary-failure",
        "exit_status": None,
        "transcript_bytes": 0,
        "stderr_bytes": 0,
        "cleanup_proved": False,
    }
    assert backend.start_calls == 0


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
    backend = FakeBackend(synchronize_start=True)
    result, _, _, backend = run_fixture(
        tmp_path,
        sigterm_ignoring_code(),
        grace_seconds=0.05,
        backend=backend,
    )

    assert result.outcome == "timeout"
    assert result.cleanup_proved is True
    assert backend.signals == [signal.SIGTERM, signal.SIGKILL]


def test_bounded_run_waits_full_grace_after_cleanup_read_error(
    tmp_path: Path,
) -> None:
    grace_seconds = 0.05
    backend = FakeBackend(cleanup_errors=1, synchronize_start=True)

    result, _, _, _ = run_fixture(
        tmp_path,
        sigterm_ignoring_code(),
        grace_seconds=grace_seconds,
        backend=backend,
    )

    assert result.outcome == "boundary-failure"
    assert result.cleanup_proved is True
    assert backend.signals == [signal.SIGTERM, signal.SIGKILL]
    assert backend.signal_times[1] - backend.signal_times[0] >= grace_seconds


def test_local_launcher_kill_waits_full_term_grace(monkeypatch) -> None:
    grace_seconds = 0.25
    clock = [10.0]
    actions: list[tuple[str, float]] = []
    wait_timeouts: list[float] = []

    class HungLauncher:
        def __init__(self) -> None:
            self.returncode: int | None = None
            self.killed = False

        def poll(self) -> int | None:
            return self.returncode

        def terminate(self) -> None:
            actions.append(("term", clock[0]))

        def wait(self, timeout: float) -> int:
            wait_timeouts.append(timeout)
            if not self.killed:
                clock[0] += timeout
                raise subprocess.TimeoutExpired(["systemd-run"], timeout)
            self.returncode = -signal.SIGKILL
            return self.returncode

        def kill(self) -> None:
            actions.append(("kill", clock[0]))
            self.killed = True

    monkeypatch.setattr(agent_runner.time, "monotonic", lambda: clock[0])
    process = HungLauncher()

    reaped = agent_runner._reap_local_launcher(
        process,
        (),
        clock[0],
        grace_seconds,
    )

    assert reaped is True
    assert wait_timeouts[0] == grace_seconds
    assert actions == [
        ("term", 10.0),
        ("kill", 10.0 + grace_seconds),
    ]


@pytest.mark.parametrize(
    ("failure_stage", "launcher_error"),
    [
        ("poll", OSError("fixture poll failure")),
        ("terminate", ProcessLookupError("fixture terminate failure")),
        ("term-wait", subprocess.SubprocessError("fixture TERM wait failure")),
        ("kill", PermissionError("fixture kill failure")),
        ("kill-wait", subprocess.SubprocessError("fixture KILL wait failure")),
    ],
)
def test_local_launcher_control_errors_return_false(
    monkeypatch,
    failure_stage: str,
    launcher_error: BaseException,
) -> None:
    class FailingLauncher:
        def __init__(self) -> None:
            self.returncode: int | None = None
            self.killed = False

        def poll(self) -> int | None:
            if failure_stage == "poll":
                raise launcher_error
            return self.returncode

        def terminate(self) -> None:
            if failure_stage == "terminate":
                raise launcher_error

        def wait(self, timeout: float) -> int:
            if self.killed:
                if failure_stage == "kill-wait":
                    raise launcher_error
                self.returncode = -signal.SIGKILL
                return self.returncode
            if failure_stage == "term-wait":
                raise launcher_error
            raise subprocess.TimeoutExpired(["systemd-run"], timeout)

        def kill(self) -> None:
            if failure_stage == "kill":
                raise launcher_error
            self.killed = True

    monkeypatch.setattr(agent_runner.time, "monotonic", lambda: 10.0)

    assert (
        agent_runner._reap_local_launcher(
            FailingLauncher(),
            (),
            10.0,
            0.25,
        )
        is False
    )


def test_bounded_run_reaps_launcher_without_scope_kill_after_empty_cgroup(
    tmp_path: Path,
) -> None:
    backend = FakeBackend(
        cgroup_empty_override=True,
        synchronize_start=True,
    )

    result, _, _, _ = run_fixture(
        tmp_path,
        sigterm_ignoring_code(),
        grace_seconds=0.02,
        backend=backend,
    )

    assert result.outcome == "timeout"
    assert result.cleanup_proved is True
    assert backend.signals == [signal.SIGTERM]
    assert backend.process is not None
    assert backend.process.poll() is not None


def test_bounded_run_preserves_boundary_failure_without_saved_cgroup(
    tmp_path: Path,
) -> None:
    backend = FakeBackend(
        cgroup_observable=False,
        synchronize_start=True,
    )

    result, _, _, _ = run_fixture(
        tmp_path,
        sigterm_ignoring_code(),
        runtime_seconds=0.05,
        grace_seconds=0.02,
        backend=backend,
    )

    assert result.outcome == "boundary-failure"
    assert result.cleanup_proved is False
    assert backend.signals == [signal.SIGTERM]
    assert backend.process is not None
    assert backend.process.poll() is not None


def test_bounded_run_does_not_signal_empty_scope_when_launcher_reap_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    backend = FakeBackend(cgroup_empty_override=True)
    monkeypatch.setattr(agent_runner, "_reap_local_launcher", lambda *_: False)

    result, _, _, _ = run_fixture(
        tmp_path,
        "import time; time.sleep(60)",
        runtime_seconds=0.05,
        grace_seconds=0.02,
        backend=backend,
    )

    assert result.outcome == "boundary-failure"
    assert result.cleanup_proved is False
    assert backend.signals == [signal.SIGTERM]


def test_bounded_run_preserves_term_signal_error_after_cleanup(tmp_path: Path) -> None:
    backend = FakeBackend(signal_errors={signal.SIGTERM})

    result, _, _, _ = run_fixture(
        tmp_path,
        "import time; time.sleep(60)",
        runtime_seconds=0.05,
        grace_seconds=0.05,
        backend=backend,
    )

    assert result.outcome == "boundary-failure"
    assert result.cleanup_proved is True
    assert backend.signals == [signal.SIGTERM]


def test_bounded_run_preserves_kill_signal_error_after_cleanup(tmp_path: Path) -> None:
    backend = FakeBackend(
        synchronize_start=True,
        signal_errors={signal.SIGKILL},
    )

    result, _, _, _ = run_fixture(
        tmp_path,
        sigterm_ignoring_code(),
        grace_seconds=0.05,
        backend=backend,
    )

    assert result.outcome == "boundary-failure"
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
    assert backend.signals == [signal.SIGTERM]


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


@pytest.mark.parametrize(
    "limits",
    [
        RunLimits(runtime_seconds=300.000001),
        RunLimits(grace_seconds=10.000001),
        RunLimits(stream_limit_bytes=33_554_433),
    ],
)
def test_systemd_backend_rejects_limits_above_production_maxima(
    monkeypatch,
    limits: RunLimits,
) -> None:
    calls: list[list[str]] = []

    def fake_popen(command: list[str], **kwargs: object) -> object:
        calls.append(command)
        return object()

    monkeypatch.setattr(agent_runner.subprocess, "Popen", fake_popen)

    with pytest.raises(BoundaryError):
        SystemdScopeBackend().start_scope("test.scope", ["client"], limits)

    assert calls == []


def test_systemd_backend_allows_limits_below_production_maxima(monkeypatch) -> None:
    captured: list[str] = []
    marker = object()

    def fake_popen(command: list[str], **kwargs: object) -> object:
        captured.extend(command)
        return marker

    monkeypatch.setattr(agent_runner.subprocess, "Popen", fake_popen)

    process = SystemdScopeBackend().start_scope(
        "test.scope",
        ["client"],
        RunLimits(
            runtime_seconds=0.25,
            grace_seconds=0.1,
            stream_limit_bytes=1024,
        ),
    )

    assert process is marker
    assert "--property=RuntimeMaxSec=0.25s" in captured


def test_systemd_backend_wraps_launch_oserror(monkeypatch) -> None:
    launch_error = OSError("fixture launch failure")

    def fake_popen(command: list[str], **kwargs: object) -> object:
        raise launch_error

    monkeypatch.setattr(agent_runner.subprocess, "Popen", fake_popen)

    with pytest.raises(BoundaryError) as raised:
        SystemdScopeBackend().start_scope("test.scope", ["client"], RunLimits())

    assert raised.value.__cause__ is launch_error


def test_systemd_backend_queries_and_parses_loaded_scope_cgroup(
    monkeypatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["command"] = command
        captured["kwargs"] = kwargs
        return systemctl_result(
            "ControlGroup=/user.slice/test.scope\nLoadState=loaded\n"
        )

    monkeypatch.setattr(agent_runner.subprocess, "run", fake_run)

    cgroup_path = SystemdScopeBackend(cgroup_root=tmp_path).cgroup_path(
        "test.scope",
        1.25,
    )

    assert cgroup_path == tmp_path / "user.slice" / "test.scope"
    assert captured["command"] == [
        "systemctl",
        "--user",
        "show",
        "test.scope",
        "--property=LoadState",
        "--property=ControlGroup",
        "--all",
    ]
    assert captured["kwargs"] == {
        "capture_output": True,
        "text": True,
        "check": False,
        "timeout": 1.25,
    }


def test_systemd_backend_returns_none_only_for_confirmed_missing_scope(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        agent_runner.subprocess,
        "run",
        lambda *args, **kwargs: systemctl_result(
            "LoadState=not-found\nControlGroup=\n"
        ),
    )

    assert SystemdScopeBackend().cgroup_path("test.scope", 1.0) is None


def test_systemd_backend_rejects_nonzero_cgroup_query(monkeypatch) -> None:
    monkeypatch.setattr(
        agent_runner.subprocess,
        "run",
        lambda *args, **kwargs: systemctl_result("", returncode=1),
    )

    with pytest.raises(BoundaryError):
        SystemdScopeBackend().cgroup_path("test.scope", 1.0)


@pytest.mark.parametrize(
    "query_error",
    [
        OSError("fixture manager failure"),
        subprocess.TimeoutExpired(["systemctl"], 1.0),
        UnicodeError("fixture decoding failure"),
    ],
)
def test_systemd_backend_wraps_cgroup_query_errors(
    monkeypatch,
    query_error: BaseException,
) -> None:
    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise query_error

    monkeypatch.setattr(agent_runner.subprocess, "run", fake_run)

    with pytest.raises(BoundaryError) as raised:
        SystemdScopeBackend().cgroup_path("test.scope", 1.0)

    assert raised.value.__cause__ is query_error


@pytest.mark.parametrize(
    "manager_output",
    [
        "",
        "LoadState=loaded\n",
        "ControlGroup=/user.slice/test.scope\n",
        "LoadState loaded\nControlGroup=/user.slice/test.scope\n",
        "LoadState=loaded\nLoadState=loaded\nControlGroup=/user.slice/test.scope\n",
        "LoadState=loaded\nControlGroup=/user.slice/test.scope\nUnknown=value\n",
        "LoadState=masked\nControlGroup=/user.slice/test.scope\n",
        "LoadState=loaded\nControlGroup=\n",
        "LoadState=loaded\nControlGroup=relative/path\n",
        "LoadState=loaded\nControlGroup=/user.slice/../escape.scope\n",
        "LoadState=loaded\nControlGroup=/\n",
        "LoadState=not-found\nControlGroup=/unexpected.scope\n",
        "LoadState=not-found\n",
        "LoadState=loaded\nControlGroup=/test.scope\nControlGroup=/test.scope\n",
    ],
)
def test_systemd_backend_rejects_uncertain_cgroup_query_output(
    monkeypatch,
    manager_output: str,
) -> None:
    monkeypatch.setattr(
        agent_runner.subprocess,
        "run",
        lambda *args, **kwargs: systemctl_result(manager_output),
    )

    with pytest.raises(BoundaryError):
        SystemdScopeBackend().cgroup_path("test.scope", 1.0)


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
