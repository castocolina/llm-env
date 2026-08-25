"""Minimal .env-style file reader.

Deliberately not python-dotenv -- this repo's one Python dependency is
pyyaml (see pylib/omniroute_client.py's own module docstring for the same
stdlib-only constraint), and the format needed here is a single flat
KEY=VALUE mapping, no interpolation/export/multiline support required.
"""

from __future__ import annotations

from pathlib import Path


def read_env_file(path: Path) -> dict[str, str]:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    result: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        result[key.strip()] = value.strip()
    return result
