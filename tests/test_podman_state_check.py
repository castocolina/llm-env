import os
import pathlib
import re
import shutil
import socket
import subprocess
import threading

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts/podman-state-check.sh"


class Stubs:
    def __init__(self, tmp_path):
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        self.config = tmp_path / "config"
        self.config.mkdir()
        self.set("crun", "printf 'ID\t NAME\t STATE\n'")
        self.set("podman", """
case "$1" in
  ps) if [[ "$*" == *Names* ]]; then printf 'abc demo\n'; else printf 'abc running\n'; fi ;;
  container) exit 0 ;;
  inspect) if [[ "$*" == *State.Status* ]]; then printf 'running\n'; else printf 'docker.io/library/demo:latest\n'; fi ;;
  exec) exit 0 ;;
  *) exit 1 ;;
esac
""")
        self.set("getent", "printf '192.0.2.1 STREAM example\n'")
        self.set("timeout", "printf 'OK\n'")

    def set(self, name, body):
        path = self.bin / name
        path.write_text(f"#!/usr/bin/env bash\n{body}\n")
        path.chmod(0o755)

    def run(self, extra_env=None):
        env = {**os.environ, "PATH": f"{self.bin}:{os.environ['PATH']}"}
        env.update(extra_env or {})
        return subprocess.run(["bash", str(SCRIPT)], env=env,
                              capture_output=True, text=True, timeout=15, check=False)


def test_healthy_state_and_dns_are_ok(tmp_path):
    result = Stubs(tmp_path).run()
    assert result.returncode == 0
    assert "podman-state: SUMMARY OK" in result.stdout


def test_running_crun_id_reported_exited_fails_and_prints_names(tmp_path):
    stubs = Stubs(tmp_path)
    stubs.set("crun", "printf 'ID NAME STATE\nabc demo running\n'")
    stubs.set("podman", """
case "$1" in
  ps) if [[ "$*" == *Names* ]]; then printf 'abc important-container\nxyz unrelated-container\n'; else printf 'abc exited\nxyz running\n'; fi ;;
  container) exit 0 ;;
  inspect) if [[ "$*" == *State.Status* ]]; then printf 'running\n'; else printf 'docker.io/library/demo:latest\n'; fi ;;
  exec) exit 0 ;;
  *) exit 1 ;;
esac
""")
    result = stubs.run()
    assert result.returncode == 1
    assert "podman-state: SUMMARY FAIL" in result.stdout
    assert "important-container" in result.stdout
    assert "unrelated-container" not in result.stdout


def test_dns_no_reply_fails(tmp_path):
    stubs = Stubs(tmp_path)
    stubs.set("timeout", "printf 'NOREPLY\\n'")
    result = stubs.run()
    assert result.returncode == 1
    assert "container resolver does not answer; restart the DNS helper (aardvark-dns) after checking bridges" in result.stdout


def test_absent_omniroute_makes_dns_unknown_without_failure(tmp_path):
    stubs = Stubs(tmp_path)
    stubs.set("podman", "case \"$1 $2\" in 'container exists') exit 1;; *) exit 0;; esac")
    result = stubs.run()
    assert result.returncode == 0
    assert "podman-state: DNS UNKNOWN" in result.stdout
    assert "podman-state: SUMMARY UNKNOWN" in result.stdout


def test_host_getent_failure_makes_dns_unknown_without_failure(tmp_path):
    stubs = Stubs(tmp_path)
    stubs.set("getent", "exit 2")
    result = stubs.run()
    assert result.returncode == 0
    assert "podman-state: DNS UNKNOWN" in result.stdout
    assert "podman-state: SUMMARY UNKNOWN" in result.stdout


def test_script_has_no_mutating_commands():
    lines = [line for line in SCRIPT.read_text().splitlines() if not line.lstrip().startswith("#")]
    commands = "\n".join(lines)
    assert not re.search(r"(?m)^\s*(?:kill|systemctl|rm\s+-)\b", commands)
    assert not re.search(r"(?m)^\s*podman\s+(?:stop|start|rm|restart|kill|system|network)\b", commands)


@pytest.mark.skipif(shutil.which("node") is None, reason="node is unavailable")
@pytest.mark.parametrize(("response", "expected"), [
    ("compressed", "OK"),
    ("nxdomain", "UNKNOWN"),
    ("empty", "UNKNOWN"),
    ("noreply", "FAIL"),
])
def test_dns_header_classification_with_local_responder(tmp_path, response, expected):
    responder = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    responder.bind(("127.0.0.1", 0))
    port = responder.getsockname()[1]
    responder.settimeout(0.2)
    stopping = threading.Event()

    def serve():
        while not stopping.is_set():
            try:
                query, address = responder.recvfrom(512)
            except TimeoutError:
                continue
            except OSError:
                return
            if response == "noreply":
                continue
            flags = 0x8000 | (3 if response == "nxdomain" else 0)
            answers = 0 if response == "empty" else (1 if response == "compressed" else 0)
            reply = bytearray(query[:2] + bytes([0x81, flags & 0xff, 0, 1, 0, answers, 0, 0, 0, 0]))
            if answers:
                reply.extend(b"\xc0\x0c\x00\x01\x00\x01\x00\x00\x00\x3c\x00\x04\xc0\x00\x02\x01")
            responder.sendto(reply, address)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        stubs = Stubs(tmp_path)
        resolv = tmp_path / "resolv.conf"
        resolv.write_text("nameserver 127.0.0.1\n")
        stubs.set("timeout", 'shift; [[ "$1" == podman && "$2" == exec ]] || exit 1; shift 2; [[ "$1" == omniroute && "$2" == node && "$3" == -e ]] || exit 1; code="$4"; host="$5"; exec node -e "$code" "$host"')
        result = stubs.run({"PODMAN_STATE_RESOLV": str(resolv), "PODMAN_STATE_DNS_PORT": str(port)})
        assert result.returncode == (1 if expected == "FAIL" else 0)
        assert f"podman-state: DNS {expected}" in result.stdout
    finally:
        stopping.set()
        responder.close()
        thread.join(timeout=1)


@pytest.mark.parametrize("failure", ["crun", "podman"])
def test_runtime_command_failure_is_unknown_without_failure(tmp_path, failure):
    stubs = Stubs(tmp_path)
    stubs.set(failure, "exit 1")
    result = stubs.run()
    assert result.returncode == 0
    assert "podman-state: SUMMARY UNKNOWN" in result.stdout
