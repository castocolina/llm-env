"""Regression tests for shell-script lifecycle behavior."""

from __future__ import annotations

import os
import pathlib
import stat
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _mock_command(directory: pathlib.Path, name: str) -> None:
    command = directory / name
    command.write_text("#!/usr/bin/env bash\nexit 0\n")
    command.chmod(command.stat().st_mode | stat.S_IXUSR)


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
