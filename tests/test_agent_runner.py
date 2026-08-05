from __future__ import annotations

import signal
import subprocess
import sys
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
