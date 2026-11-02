"""latency-profile.sh against a fake llama-server (no GPU, no containers): request shape, recorded fields, failures."""

import json
import os
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar

import pytest
from skill_helpers import ROOT
from skill_helpers import out as _out

SCRIPT = ROOT / ".claude" / "skills" / "onboard-local-model" / "scripts" / "latency-profile.sh"
KEY = "sk-test-secret-key-0123456789"
CTX, CAP = 4096, 256


class _Fake(BaseHTTPRequestHandler):
    requests: ClassVar[list[dict]] = []
    fail: ClassVar[bool] = False

    def log_message(self, *_args) -> None:
        pass

    def _send(self, code: int, body: bytes, kind: str = "application/json") -> None:
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        self._send(200, json.dumps({"default_generation_settings": {"n_ctx": CTX}}).encode())

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path == "/tokenize":
            self._send(200, json.dumps({"tokens": [0] * (len(body["content"]) // 4)}).encode())
            return
        content = body["messages"][0]["content"]
        prompt_n = len(content) // 4 + 8
        type(self).requests.append({"auth": self.headers.get("Authorization"), "head": content[:60], "body": body, "prompt_n": prompt_n})
        if type(self).fail or prompt_n + body["max_tokens"] > CTX:
            self._send(400, json.dumps({"error": {"message": "request exceeds the context size"}}).encode())
            return
        chunks = [{"choices": [{"index": 0, "delta": {"role": "assistant", "content": None}}]},
                  {"choices": [{"index": 0, "delta": {"reasoning_content": "Think"}}]},
                  {"choices": [{"index": 0, "delta": {"content": "Answer"}}]},
                  {"choices": [], "usage": {"completion_tokens": body["max_tokens"], "prompt_tokens": prompt_n},
                   "timings": {"cache_n": 0, "prompt_n": prompt_n, "prompt_ms": 100.0, "prompt_per_second": 1000.0,
                               "predicted_n": body["max_tokens"], "predicted_ms": 200.0, "predicted_per_second": 50.0}}]
        text = "".join(f"data: {json.dumps(c, separators=(',', ':'))}\n\n" for c in chunks)  # compact, as llama-server sends it + "data: [DONE]\n\n"
        self._send(200, text.encode(), "text/event-stream")


@pytest.fixture
def server():
    _Fake.requests, _Fake.fail = [], False
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Fake)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd.server_address[1]
    httpd.shutdown()
    thread.join()


@pytest.fixture
def env(tmp_path: Path, server: int):
    config = tmp_path / "models.yml"
    config.write_text(
        f"server: {{port: {server}, api_key: {KEY}}}\n"
        "gpu: {pci_address: '0000:99:00.0'}\n"
        f"models:\n  - {{alias: a, file: a.gguf, ctx_size: {CTX}, client_max_output_tokens: {CAP}}}\n"
    )
    cg = tmp_path / "cgroup"
    cg.mkdir()
    (cg / "memory.current").write_text("1048576000\n")
    (cg / "memory.stat").write_text("anon 104857600\nfile 0\n")
    (cg / "cpu.stat").write_text("usage_usec 1000000\n")
    (cg / "cgroup.procs").write_text("")
    out = _out(tmp_path, "latency")
    yield {**os.environ, "LLM_ENV_CONFIG": str(config), "LATENCY_PROFILE_CGROUP": str(cg)}, out


def _run(env, *args: str):
    environ, out = env
    return subprocess.run(["bash", str(SCRIPT), "--alias", "a", "--out", str(out), "--idle-seconds", "1", *args],
                          capture_output=True, text=True, check=False, env=environ, stdin=subprocess.DEVNULL, timeout=120)


def test_profile_records_timings_unique_prompts_and_never_prints_the_key(env) -> None:
    result = _run(env, "--sizes", "256,max", "--tokens", "16")
    assert result.returncode == 0, result.stderr
    data = json.loads((env[1] / "latency-a.json").read_text())
    assert data["kind"] == "latency-profile" and data["ctx_size"] == CTX and data["n_ctx"] == CTX and data["file"] == "a.gguf"
    assert [r["label"] for r in data["runs"]] == ["p256", f"p{CTX - 16 - 64}", f"cap{CAP}"]
    assert [r["kind"] for r in data["runs"]] == ["size", "size", "cap"]
    for run in data["runs"]:
        assert run["error"] is None and run["input_tps"] == 1000.0 and run["output_tps"] == 50.0 and run["cache_n"] == 0
        assert run["ttft_s"] is not None and run["ttft_s"] > 0 and run["wall_s"] >= run["ttft_s"]
        assert run["prompt_n"] == run["prompt_tokens"] and run["predicted_n"] == run["completion_tokens"]
        assert run["host"]["cg_mem_peak_mib"] == 1000 and run["host"]["cg_anon_peak_mib"] == 100
        assert run["gpu"]["peak_vram_mib"] is None and run["gpu"]["peak_busy_pct"] is None  # no such GPU: not measured, not 0
        assert run["gpu"]["peak_cpu_pct"] is not None
    assert data["runs"][0]["prompt_n"] <= 256 and data["runs"][1]["prompt_n"] + 16 <= CTX
    assert data["runs"][2]["prompt_n"] + CAP <= CTX and data["runs"][2]["predicted_n"] == CAP
    sent = [r for r in _Fake.requests if r["head"] != "Reply with the single word ok."]
    assert len(sent) == 3 and len({r["head"] for r in sent}) == 3  # a unique prefix per request
    assert all(r["body"]["cache_prompt"] is False and r["body"]["ignore_eos"] is True and r["body"]["stream"] for r in sent)
    assert all(r["auth"] == f"Bearer {KEY}" for r in _Fake.requests)
    assert KEY not in result.stdout + result.stderr + (env[1] / "latency-a.json").read_text()
    assert "in_tok/s" in result.stdout and "p256" in result.stdout


def test_a_failed_request_is_recorded_and_exits_nonzero(env) -> None:
    _Fake.fail = True
    result = _run(env, "--sizes", "256", "--tokens", "16", "--cap-run", "off")
    assert result.returncode == 1
    runs = json.loads((env[1] / "latency-a.json").read_text())["runs"]
    assert len(runs) == 1 and "exceeds the context size" in runs[0]["error"] and runs[0]["input_tps"] is None
    assert "FAILED p256" in result.stderr


def test_a_size_that_cannot_fit_with_its_output_is_skipped_not_sent(env) -> None:
    result = _run(env, "--sizes", f"{CTX}", "--tokens", "16", "--cap-run", "off")
    assert result.returncode == 0, result.stderr
    data = json.loads((env[1] / "latency-a.json").read_text())
    assert data["runs"] == [] and data["skipped"][0]["label"] == f"p{CTX}"
    assert [r["head"] for r in _Fake.requests] == ["Reply with the single word ok."]


def test_out_must_be_under_the_repo_tmp(env, tmp_path: Path) -> None:
    result = subprocess.run(["bash", str(SCRIPT), "--alias", "a", "--out", str(tmp_path / "elsewhere")],
                            capture_output=True, text=True, check=False, env=env[0], stdin=subprocess.DEVNULL)
    assert result.returncode == 2 and "under" in result.stderr


def test_bad_flags_exit_2() -> None:
    for args in (["--sizes", "abc"], ["--tokens", "0"], ["--cap-run", "maybe"], ["--bogus"], []):
        assert subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True, check=False).returncode == 2
