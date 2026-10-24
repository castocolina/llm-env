#!/usr/bin/env python3
"""Scripted stub for podman/skopeo/podman-compose/journalctl (tests only).

Production runs every such child under `env -i` plus a short allowlist, so no STUB_* variable can reach
this process. The world file and the call journal are siblings of the symlink the stub was invoked through."""
import json
import os
import signal
import sys
import time
from pathlib import Path

name, argv = os.path.basename(sys.argv[0]), sys.argv[1:]
home = Path(sys.argv[0]).parent  # NOT resolved: the directory holding the symlinks
world_path, calls = home / "stub-world.json", home / "stub-calls.jsonl"
world = json.loads(world_path.read_text())
phase = world.get("phase", "start")


def set_phase(value):
    w = json.loads(world_path.read_text())
    w["phase"] = value
    world_path.write_text(json.dumps(w))


fds = []
for fd in os.listdir("/proc/self/fd"):
    try:
        fds.append(os.readlink(f"/proc/self/fd/{fd}"))
    except OSError:
        pass
env = {k: os.environ[k] for k in ("CONTAINERS_STORAGE_CONF", "CONTAINERS_CONF", "CONTAINERS_REGISTRIES_CONF",
                                  "XDG_RUNTIME_DIR") if k in os.environ}
with calls.open("a") as handle:
    handle.write(json.dumps({"tool": name, "argv": argv, "phase": phase, "fds": fds, "env": env,
                             "t": time.monotonic()}) + "\n")


def matches(rule):
    if rule["tool"] != name:
        return False
    if "prefix" in rule and argv[:len(rule["prefix"])] != rule["prefix"]:
        return False
    if "last" in rule and (not argv or argv[-1] != rule["last"]):
        return False
    if "has" in rule and not all(token in argv for token in rule["has"]):
        return False
    if "arg_endswith" in rule and not any(a.endswith(rule["arg_endswith"]) for a in argv):
        return False
    return "phase" not in rule or phase in rule["phase"]


rule = next((r for r in world["rules"] if matches(r)), None)
if rule is None:
    if argv and argv[0] in {"pull", "tag", "stop", "rm"}:  # mutating verbs default to success
        sys.exit(0)
    print(f"stub: unscripted {name} {argv} in phase {phase}", file=sys.stderr)
    sys.exit(99)
matched = rule
if "on_term_set_phase" in matched:
    def on_term(*_):
        set_phase(matched["on_term_set_phase"])
        sys.exit(0)
    signal.signal(signal.SIGTERM, on_term)
sys.stdout.write(rule.get("stdout") or ""); sys.stdout.flush()  # output precedes any sleep (a follower streams at once)
time.sleep(rule.get("sleep") or 0)
if "set_phase" in rule:
    set_phase(rule["set_phase"])
if "write_file" in rule:  # [path, text]: change the fake OmniRoute's state at a scripted point
    Path(rule["write_file"][0]).write_text(rule["write_file"][1])
if "echo_file_after" in rule:  # emulate `provider -f FILE config` on a real file
    sys.stdout.write(Path(argv[argv.index(rule["echo_file_after"]) + 1]).read_text())
sys.exit(rule.get("rc", 0))
