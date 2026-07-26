"""Regression tests for shell-script lifecycle behavior."""

from __future__ import annotations

import os
import pathlib
import stat
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _mock_command(directory: pathlib.Path, name: str) -> None:
    command = directory / name
    command.write_text("#!/usr/bin/bash\nexit 0\n")
    command.chmod(command.stat().st_mode | stat.S_IXUSR)


def _mock_dirname(directory: pathlib.Path) -> None:
    """Provide the only external utility prerequisites.sh needs to source lib.sh."""
    command = directory / "dirname"
    command.write_text(
        "#!/usr/bin/bash\ncase $1 in */*) printf '%s\\n' \"${1%/*}\" ;; *) printf '.\\n' ;; esac\n"
    )
    command.chmod(command.stat().st_mode | stat.S_IXUSR)


def run_prerequisites_with_stubs(
    tmp_path: pathlib.Path,
    *,
    yq_version: str | None = None,
    response: str = "no",
    development_available: bool = True,
    arguments: tuple[str, ...] = (),
) -> tuple[subprocess.CompletedProcess[str], pathlib.Path]:
    """Run prerequisite detection with controlled host command stubs."""
    commands = tmp_path / "bin"
    commands.mkdir()
    calls = tmp_path / "calls"

    _mock_dirname(commands)
    names = ["uv", "jq", "podman", "curl", "ip", "sudo"]
    if development_available:
        names.extend(("git", "shellcheck"))
    for name in names:
        _mock_command(commands, name)

    if yq_version is not None:
        yq = commands / "yq"
        yq.write_text(f"#!/usr/bin/bash\nprintf '%s\\n' '{yq_version}'\n")
        yq.chmod(yq.stat().st_mode | stat.S_IXUSR)

    sudo = commands / "sudo"
    sudo.write_text("#!/usr/bin/bash\nexec \"$@\"\n")
    sudo.chmod(sudo.stat().st_mode | stat.S_IXUSR)
    rpm_ostree = commands / "rpm-ostree"
    rpm_ostree.write_text(
        "#!/usr/bin/bash\nprintf '%s\\n' \"$*\" >> \"$CALLS\"\n"
    )
    rpm_ostree.chmod(rpm_ostree.stat().st_mode | stat.S_IXUSR)

    environment = os.environ | {
        "PATH": str(commands),
        "CALLS": str(calls),
    }
    result = subprocess.run(
        ["/usr/bin/bash", "prerequisites.sh", *arguments],
        cwd=ROOT,
        env=environment,
        input=f"{response}\n",
        text=True,
        capture_output=True,
        check=False,
    )
    return result, calls


def test_prerequisites_reports_missing_yq_v4_without_installing(
    tmp_path: pathlib.Path,
) -> None:
    """An obsolete yq must be reported without modifying the host."""
    result, calls = run_prerequisites_with_stubs(tmp_path, yq_version="yq 3.4.1")

    assert result.returncode == 1
    assert "Mike Farah yq v4" in result.stdout
    assert not calls.exists()


def test_prerequisites_check_reports_missing_non_runtime_rows_on_controlled_path(
    tmp_path: pathlib.Path,
) -> None:
    """Preflight must not discover host development or LAN commands."""
    result, _ = run_prerequisites_with_stubs(
        tmp_path,
        yq_version="yq (https://github.com/mikefarah/yq/) version v4.45.1",
        development_available=False,
        arguments=("--check",),
    )

    assert result.returncode == 0
    assert "missing    git              (git)        source control for updates" in result.stdout
    assert "missing    shellcheck       (ShellCheck) shell script validation" in result.stdout
    assert "missing    firewall-cmd     (firewalld)  firewall configuration for LAN access" in result.stdout
    assert "missing    avahi-publish    (avahi)      LAN service discovery" in result.stdout


def test_prerequisites_displays_a_distinct_purpose_for_every_command(
    tmp_path: pathlib.Path,
) -> None:
    """Each status row must explain the command's specific role."""
    result, _ = run_prerequisites_with_stubs(
        tmp_path,
        yq_version="yq (https://github.com/mikefarah/yq/) version v4.45.1",
        development_available=True,
        arguments=("--check",),
    )

    assert result.returncode == 0
    expected_purposes = (
        "Python tool runner and dependency manager",
        "JSON processor for script-to-Python communication",
        "Mike Farah yq v4 configuration processor",
        "container engine for llama.cpp",
        "HTTP client for downloads and health checks",
        "network address inspection",
        "source control for updates",
        "shell script validation",
        "firewall configuration for LAN access",
        "LAN service discovery",
    )
    for purpose in expected_purposes:
        assert purpose in result.stdout


def test_prerequisites_installs_only_after_yes(tmp_path: pathlib.Path) -> None:
    """Package installation must require the exact affirmative response."""
    result, calls = run_prerequisites_with_stubs(tmp_path, response="yes")

    assert result.returncode == 0
    assert "install yq" in calls.read_text()


def test_setup_stops_for_missing_prerequisites_before_mutating_config(
    tmp_path: pathlib.Path,
) -> None:
    """Setup must direct users to install prerequisites before configuration work."""
    commands = tmp_path / "bin"
    commands.mkdir()
    for name in ("uv", "jq", "podman", "curl", "ip", "git", "shellcheck"):
        _mock_command(commands, name)
    yq = commands / "yq"
    yq.write_text("#!/usr/bin/env bash\nprintf '%s\\n' 'yq 3.4.1'\n")
    yq.chmod(yq.stat().st_mode | stat.S_IXUSR)

    config = tmp_path / "models.yml"
    config.write_text("server: {}\n")
    environment = os.environ | {
        "HOME": str(tmp_path / "home"),
        "LLM_ENV_CONFIG": str(config),
        "PATH": f"{commands}:/usr/bin:/bin",
    }
    result = subprocess.run(
        ["/usr/bin/bash", "setup.sh"],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "missing prerequisites; run 'make prerequisites'" in result.stderr
    assert config.read_text() == "server: {}\n"


def test_enable_boot_fails_when_quadlet_rerender_fails(tmp_path: pathlib.Path) -> None:
    """A failed start.sh rerender must not be reported as boot setup success."""
    commands = tmp_path / "bin"
    commands.mkdir()
    for name in ("yq", "loginctl", "systemctl"):
        _mock_command(commands, name)

    home = tmp_path / "home"
    config = home / ".config/llm-env/models.yml"
    config.parent.mkdir(parents=True)
    config.write_text("server: {}\n")
    unit = home / ".config/containers/systemd/llm-server.container"
    unit.parent.mkdir(parents=True)
    unit.write_text("[Container]\n")

    environment = os.environ | {
        "HOME": str(home),
        "LLM_ENV_CONFIG": str(config),
        "PATH": f"{commands}:/usr/bin:/bin",
    }
    result = subprocess.run(
        ["bash", "enable-boot.sh"],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
