"""Make signal-sending tests independent of how pytest itself was launched.

A process started with `nohup` or as a background job inherits SIGHUP and SIGINT as "ignored", and a shell
script cannot trap a signal that was ignored when it started. The tests that send HUP or INT to a script
under test therefore failed whenever the suite ran from such a launcher. `default_signals(argv)` runs the
command through a tiny Python wrapper that restores the default disposition first.
"""
from __future__ import annotations

import sys

_WRAPPER = (
    "import os, signal, sys\n"
    "for s in (signal.SIGINT, signal.SIGHUP, signal.SIGTERM):\n"
    "    signal.signal(s, signal.SIG_DFL)\n"
    "os.execvp(sys.argv[1], sys.argv[1:])\n"
)


def default_signals(argv: list[str]) -> list[str]:
    """Return argv wrapped so the command starts with default SIGINT, SIGHUP and SIGTERM handling."""
    return [sys.executable, "-c", _WRAPPER, *argv]
