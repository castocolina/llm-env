from __future__ import annotations

import asyncio
import importlib.util
import json
import multiprocessing
import re
import stat
import threading
import time
from pathlib import Path

MODULE = Path(__file__).parents[1] / "docker/system-one/laya_span_observer.py"
spec = importlib.util.spec_from_file_location("laya_span_observer", MODULE)
assert spec and spec.loader
observer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(observer)


def asgi(path, req, response, status=200):
    records = []
    async def app(scope, receive, send):
        await receive()
        await send({"type": "http.response.start", "status": status, "headers": []})
        await send({"type": "http.response.body", "body": response})
    messages = iter([{"type": "http.request", "body": req, "more_body": False}])
    async def receive(): return next(messages)
    async def send(message): pass
    asyncio.run(observer.RequestObserver(app, records.append)(
        {"type": "http", "method": "POST", "path": path}, receive, send))
    return records


def test_record_contract_counts_and_privacy_for_single_and_batch():
    secret = "SENTINEL instruction question answer request-id"
    response = json.dumps({"model": "model:tag", "answers": {"q": {"type": "choice", "confidence": .75}}}).encode()
    request = json.dumps({"id": secret, "questions": {"q": {"instructions": secret}}}).encode()
    record = asgi("/v1/systemone", request, response)[0]
    assert set(record) == {"v", "ts", "span_id", "name", "endpoint", "batch", "batch_items", "http_status", "duration_ms", "requested_questions", "completed_answers", "answers", "confidences", "model", "error_type", "truncated"}
    assert re.fullmatch(r"[0-9a-f]{16}", record["span_id"])
    assert record["name"] == "laya.systemone" and record["answers"] == {"noul": 0, "choice": 1, "score": 0}
    assert record["confidences"] == [.75] and record["model"] == "model:tag"
    assert secret not in json.dumps(record)
    batch = asgi("/v1/systemone/batch", b'{"states":[{},{}],"questions":{"q":{}}}',
                 b'{"results":[{"model":"english","answers":{"a":{"type":"noul","confidence":0.2}}},{"model":"english","answers":{"b":{"type":"score","confidence":0.8}}}]}')[0]
    assert batch["batch_items"] == 2 and batch["answers"] == {"noul": 1, "choice": 0, "score": 1}
    assert batch["confidences"] == [.2, .8] and batch["completed_answers"] == 2
    partial = asgi("/v1/systemone/batch", b'{"states":[{},{},{}]}',
                   b'{"results":[{"answers":{"a":{}}},{"error":"bad"},{"answers":{"b":{},"c":{}}}]}')[0]
    assert partial["completed_answers"] == 3


def test_confidences_round_to_four_decimals_and_worst_case_record_stays_under_limit(tmp_path):
    response = json.dumps({"model": "x" * 40, "results": [{"model": "x" * 40,
        "answers": {str(i): {"type": "choice", "confidence": 0.123456789} for i in range(256)}}]}).encode()
    record = observer._record("/v1/systemone/batch", b'{"states":[]}', response,
                              500, "2026-11-21T00:00:00.000Z", 999999.9, error_type="exception")
    assert record["confidences"] == [0.1235] * 256
    sink = observer.BoundedFileSink(str(tmp_path / "worst.jsonl"))
    sink.submit(record)
    sink.queue.join()
    try:
        assert sink.dropped == 0
        assert (tmp_path / "worst.jsonl").stat().st_size < observer.MAX_RECORD_BYTES
    finally:
        sink.close()


def test_privacy_model_allowlist_and_confidence_filtering_cap():
    answers = {str(i): {"type": "choice", "confidence": v} for i, v in enumerate([.4, float("nan"), 1.1, True] + [.5] * 260)}
    response = json.dumps({"model": " weird model ", "answers": answers}, allow_nan=True).encode()
    r = asgi("/v1/systemone", b'{"questions":{}}', response)[0]
    assert r["model"] is None and len(r["confidences"]) == 256 and all(0 <= x <= 1 for x in r["confidences"])
    for path, req, body in (
        ("/v1/systemone", b'{"questions":{}}', b'{"model":[],"answers":{}}'),
        ("/v1/systemone/batch", b'{"states":[]}', b'{"results":[{"model":[],"answers":{}},{"model":{},"answers":{}}]}'),
    ):
        assert asgi(path, req, body)[0]["model"] is None


def test_errors_truncation_and_non_observed_endpoint():
    req = b'{"questions":{"q":{}}}'
    r = asgi("/v1/systemone", req, b'{"detail":"private"}', 422)[0]
    assert r["error_type"] == "http_4xx" and r["http_status"] == 422
    records = []
    async def app(scope, receive, send): await send({"type": "http.response.start", "status": 200, "headers": []})
    async def receive(): return {"type": "http.request", "body": b"", "more_body": False}
    async def send(message): pass
    asyncio.run(observer.RequestObserver(app, records.append)({"type":"http","method":"GET","path":"/v1/systemone"}, receive, send))
    assert records == []
    assert observer._record("/v1/systemone", b"x" * (observer.MAX_CAPTURE_BYTES+1), b"{}", 200, "ts", 1, truncated=True)["truncated"]


def test_file_sink_permissions_append_rotation_and_env(tmp_path, monkeypatch):
    monkeypatch.setenv("LAYA_SPAN_SINK", str(tmp_path / "nested" / "sink.jsonl"))
    sink = observer.BoundedFileSink(capacity=4, max_bytes=150)
    try:
        sink.submit({"a": "x" * 100}); sink.queue.join()
        sink.submit({"a": "y" * 100}); sink.queue.join()
        path = tmp_path / "nested" / "sink.jsonl"
        assert path.exists() and path.with_name("sink.jsonl.1").exists()
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o755
        assert stat.S_IMODE(path.stat().st_mode) == 0o644
        assert all(json.loads(line) for line in path.read_text().splitlines())
    finally:
        sink.close()


def test_full_queue_drops_while_writer_is_blocked_without_delaying_requests(tmp_path):
    entered, release = threading.Event(), threading.Event()
    original = observer.BoundedFileSink._run
    class BlockedSink(observer.BoundedFileSink):
        def _run(self):
            entered.set()
            release.wait()
            original(self)
    sink = BlockedSink(str(tmp_path / "queue.jsonl"), capacity=1)
    try:
        assert entered.wait(1)
        timings = []
        def requests():
            for n in range(20):
                start = time.monotonic()
                sink.submit({"n": n})
                timings.append(time.monotonic() - start)
        caller = threading.Thread(target=requests, daemon=True)
        caller.start(); caller.join(.1)
        assert not caller.is_alive() and max(timings) < .1
        assert sink.queue.qsize() == 1 and sink.dropped == 19
    finally:
        release.set()
        caller.join(.5)
        while True:
            try: sink.queue.get_nowait()
            except observer.queue.Empty: break
            else: sink.queue.task_done()


def _process_writer(path, n):
    sink = observer.BoundedFileSink(path, capacity=64)
    for i in range(16): sink.submit({"writer": n, "i": i, "payload": "x" * 3000})
    sink.queue.join()
    sink.close()


def test_concurrent_process_sinks_append_only_whole_lines(tmp_path):
    path = tmp_path / "concurrent.jsonl"
    context = multiprocessing.get_context("spawn")
    processes = [context.Process(target=_process_writer, args=(str(path), n)) for n in range(8)]
    for process in processes: process.start()
    for process in processes:
        process.join(5)
        assert process.exitcode == 0
    lines = path.read_bytes().splitlines()
    assert len(lines) == 128
    parsed = [json.loads(line) for line in lines]
    assert {(row["writer"], row["i"]) for row in parsed} == {(n, i) for n in range(8) for i in range(16)}
    assert all(len(row["payload"]) == 3000 for row in parsed)
    assert all(line.endswith(b"}") for line in lines)


def test_exit_hook_drains_queued_records_and_is_bounded_when_writer_blocks(tmp_path):
    path = tmp_path / "exit.jsonl"
    import subprocess
    import sys

    code = """import importlib.util, sys
spec = importlib.util.spec_from_file_location('observer', sys.argv[1])
module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
sink = module.BoundedFileSink(sys.argv[2], capacity=128)
for i in range(128): sink.submit({'n': i})
module._register_shutdown(sink)
"""
    result = subprocess.run([sys.executable, "-c", code, str(MODULE), str(path)], check=False)
    assert result.returncode == 0
    assert len(path.read_text().splitlines()) == 128

    entered, release = threading.Event(), threading.Event()
    class BlockedSink(observer.BoundedFileSink):
        def _run(self):
            entered.set()
            release.wait()
    blocked = BlockedSink(str(tmp_path / "blocked.jsonl"), capacity=1)
    assert entered.wait(1)
    started = time.monotonic()
    blocked.close(timeout=0.1)
    assert time.monotonic() - started < 0.3
    release.set()


def test_write_failure_never_escapes_submit(tmp_path):
    path = tmp_path / "not-a-directory"
    path.write_text("x")
    sink = observer.BoundedFileSink(str(path / "sink.jsonl"))
    try:
        sink.submit({"ok": True}); sink.queue.join()
    finally: sink.close()


def test_concurrent_sink_writes_are_whole_records(tmp_path):
    sink = observer.BoundedFileSink(str(tmp_path / "events.jsonl"), capacity=128)
    try:
        threads = [threading.Thread(target=lambda n=n: [sink.submit({"n": n, "i": i}) for i in range(8)]) for n in range(8)]
        [t.start() for t in threads]; [t.join() for t in threads]; sink.queue.join()
        assert len([json.loads(x) for x in (tmp_path / "events.jsonl").read_text().splitlines()]) == 64
    finally: sink.close()


def test_prepare_laya_spans_rejects_links_wrong_types_and_outside_paths(tmp_path):
    import os
    import subprocess

    root = Path(__file__).parents[1]
    fakebin = tmp_path / "bin"
    fakebin.mkdir()
    log = tmp_path / "podman.log"
    log.touch()
    fake = fakebin / "podman"
    fake.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$PODMAN_LOG"\n')
    fake.chmod(0o755)
    state = tmp_path / ".local/state/llm-env"
    state.mkdir(parents=True)
    runtime = tmp_path / "runtime"
    runtime.mkdir(mode=0o700)
    env = os.environ | {"HOME": str(tmp_path), "TMPDIR": str(tmp_path), "XDG_RUNTIME_DIR": str(runtime), "PATH": f"{fakebin}:/usr/bin:/bin", "PODMAN_LOG": str(log)}
    script = root / "scripts/prepare-laya-spans.sh"

    def run(target):
        return subprocess.run(["bash", str(script), str(target)], env=env, capture_output=True, text=True, check=False)

    target = state / "laya-spans"
    outside = tmp_path / "outside-root"
    assert run(outside).returncode != 0
    traversal = tmp_path / "span-sink.abcdef" / ".." / ".." / "evil"
    before = len(log.read_text().splitlines())
    assert run(traversal).returncode != 0
    assert len(log.read_text().splitlines()) == before and not (tmp_path.parent / "evil").exists()
    temp_form = tmp_path / "span-sink.abcdef"
    assert run(temp_form).returncode == 0
    temp_form.rmdir()
    no_tmp = env.copy(); no_tmp.pop("TMPDIR")
    default = subprocess.run(["bash", str(script)], env=no_tmp, capture_output=True, text=True, check=False)
    assert default.returncode == 0, default.stderr
    tmp_form_no_env_path = Path("/tmp/span-sink.pha5abcdef")
    try:
        tmp_form_no_env = subprocess.run(["bash", str(script), str(tmp_form_no_env_path)], env=no_tmp, capture_output=True, text=True, check=False)
        assert tmp_form_no_env.returncode == 0, tmp_form_no_env.stderr
    finally:
        tmp_form_no_env_path.rmdir()
    assert run(target).returncode == 0
    assert run(target).returncode == 0
    assert (target.stat().st_mode & 0o777) == 0o755
    assert len(log.read_text().splitlines()) == 10

    assert len(log.read_text().splitlines()) == 10


def _span_prepare_runner(tmp_path):
    import os
    import subprocess

    fakebin = tmp_path / "bin"
    fakebin.mkdir()
    log = tmp_path / "podman.log"
    log.touch()
    fake = fakebin / "podman"
    fake.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$PODMAN_LOG"\n')
    fake.chmod(0o755)
    state = tmp_path / ".local/state/llm-env/laya-spans"
    state.mkdir(parents=True)
    runtime = tmp_path / "runtime"
    runtime.mkdir(mode=0o700)
    env = os.environ | {"HOME": str(tmp_path), "TMPDIR": str(tmp_path), "XDG_RUNTIME_DIR": str(runtime), "PATH": f"{fakebin}:/usr/bin:/bin", "PODMAN_LOG": str(log)}
    script = Path(__file__).parents[1] / "scripts/prepare-laya-spans.sh"

    def run(target):
        return subprocess.run(["bash", str(script), str(target)], env=env, capture_output=True, text=True, check=False)

    return run, log, state


def _assert_span_prepare_rejected(run, log, target):
    before = log.read_text()
    result = run(target)
    assert result.returncode != 0 and "error:" in result.stderr
    assert log.read_text() == before


def test_prepare_laya_spans_rejects_symlink_inside_allowed_state_directory(tmp_path):
    run, log, state = _span_prepare_runner(tmp_path)
    link = state / "record-link"
    link.symlink_to(tmp_path / "victim")
    _assert_span_prepare_rejected(run, log, state)
    assert link.is_symlink() and not (tmp_path / "victim").exists()


def test_prepare_laya_spans_rejects_symlink_in_allowed_temp_directory(tmp_path):
    run, log, _ = _span_prepare_runner(tmp_path)
    target = tmp_path / "span-sink.abcdef"
    target.mkdir()
    link = target / "record-link"
    link.symlink_to(tmp_path / "victim")
    _assert_span_prepare_rejected(run, log, target)
    assert link.is_symlink() and not (tmp_path / "victim").exists()


def test_prepare_laya_spans_rejects_symlinked_state_parent(tmp_path):
    run, log, state = _span_prepare_runner(tmp_path)
    real = tmp_path / "real-state"
    (tmp_path / ".local/state").rename(real)
    (tmp_path / ".local/state").symlink_to(real, target_is_directory=True)
    _assert_span_prepare_rejected(run, log, state)
    assert (tmp_path / ".local/state").is_symlink()


def test_prepare_laya_spans_rejects_symlinked_sink_directory(tmp_path):
    run, log, state = _span_prepare_runner(tmp_path)
    state.rmdir()
    real = tmp_path / "real-sink"
    real.mkdir()
    state.symlink_to(real, target_is_directory=True)
    _assert_span_prepare_rejected(run, log, state)
    assert state.is_symlink() and list(real.iterdir()) == []


def test_prepare_laya_spans_rejects_dangling_sink_link(tmp_path):
    run, log, state = _span_prepare_runner(tmp_path)
    state.rmdir()
    state.symlink_to(tmp_path / "missing")
    _assert_span_prepare_rejected(run, log, state)
    assert state.is_symlink() and not (tmp_path / "missing").exists()


def test_prepare_laya_spans_rejects_regular_file_sink(tmp_path):
    run, log, state = _span_prepare_runner(tmp_path)
    state.rmdir()
    state.write_text("untouched")
    _assert_span_prepare_rejected(run, log, state)
    assert state.read_text() == "untouched"


def test_update_smoke_checks_file_sink_configuration():
    script = (Path(__file__).parents[1] / "scripts/update-system-one.sh").read_text()
    assert "LAYA_SPAN_SINK" in script and "laya-spans.jsonl" in script


def test_collector_path_matches_host_sink_contract():
    import sys
    sys.path.insert(0, str(Path(__file__).parents[1]))
    from test_compose import CFG, SYSTEM_ONE, system_one_document
    service = system_one_document({**CFG, "system_one": SYSTEM_ONE})["services"]["system-one"]
    assert service["environment"]["LAYA_SPAN_SINK"] == "/var/log/laya/laya-spans.jsonl"
    assert any(v.endswith("/.local/state/llm-env/laya-spans:/var/log/laya:Z") for v in service["volumes"])


