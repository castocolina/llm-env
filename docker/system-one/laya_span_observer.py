"""Best-effort, privacy-preserving bounded JSONL request sink for Laya."""
from __future__ import annotations

import atexit
import json
import logging
import math
import os
import queue
import re
import threading
import time
from datetime import UTC, datetime
from typing import Any, Callable

MAX_CAPTURE_BYTES = 24000
MAX_QUEUE = 128
MAX_RECORD_BYTES = 4096
DEFAULT_SINK = "/var/log/laya/laya-spans.jsonl"
DEFAULT_MAX_BYTES = 20 * 1024 * 1024
_LOG = logging.getLogger("laya.span")
_MODEL = re.compile(r"^[A-Za-z0-9._:-]{1,40}$")
_TYPES = ("noul", "choice", "score")


def _json(body: bytes) -> Any:
    try:
        return json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return None


def _record(path: str, request: bytes, response: bytes, status: int, ts: str, duration_ms: float,
            *, truncated: bool = False, error_type: str | None = None) -> dict[str, Any]:
    batch = path.endswith("/batch")
    req, res = _json(request), _json(response)
    states = req.get("states") if isinstance(req, dict) and batch else None
    questions = req.get("questions") if isinstance(req, dict) else None
    items = len(states) if isinstance(states, list) else (None if batch else 1)
    requested = len(questions) if isinstance(questions, dict) else None
    results = res.get("results") if isinstance(res, dict) and batch else None
    answer_sets = [x.get("answers") for x in results if isinstance(x, dict) and isinstance(x.get("answers"), dict)] if isinstance(results, list) else []
    answers = res.get("answers") if isinstance(res, dict) and not batch else None
    if isinstance(answers, dict): answer_sets = [answers]
    counts = {t: 0 for t in _TYPES}
    confidences = []
    for aset in answer_sets:
        for answer in aset.values():
            if not isinstance(answer, dict): continue
            typ = answer.get("type")
            if isinstance(typ, str) and typ in counts: counts[typ] += 1
            confidence = answer.get("confidence")
            if len(confidences) < 256 and isinstance(confidence, (int, float)) and not isinstance(confidence, bool) and math.isfinite(confidence) and 0 <= confidence <= 1:
                confidences.append(round(confidence, 4))
    model = res.get("model") if isinstance(res, dict) else None
    if not model and results:
        models = [x.get("model") for x in results if isinstance(x, dict) and x.get("model")]
        models = [value for value in models if isinstance(value, str)]
        model = models[0] if models and all(value == models[0] for value in models) else None
    if error_type not in {"http_4xx", "http_5xx", "exception", "timeout"}:
        error_type = "http_4xx" if 400 <= status < 500 else "http_5xx" if status >= 500 else None
    completed = sum(map(len, answer_sets)) if answer_sets else (0 if isinstance(answers, dict) or isinstance(results, list) else None)
    return {"v": 1, "ts": ts, "span_id": os.urandom(8).hex(),
            "name": "laya.batch" if batch else "laya.systemone", "endpoint": path,
            "batch": batch, "batch_items": items, "http_status": status,
            "duration_ms": duration_ms, "requested_questions": requested,
            "completed_answers": completed, "answers": counts, "confidences": confidences,
            "model": model if isinstance(model, str) and _MODEL.fullmatch(model) else None,
            "error_type": error_type, "truncated": truncated}


class BoundedFileSink:
    def __init__(self, path: str | None = None, *, capacity: int = MAX_QUEUE,
                 max_bytes: int | None = None) -> None:
        self.path = path or os.environ.get("LAYA_SPAN_SINK", DEFAULT_SINK)
        self.max_bytes = max_bytes if max_bytes is not None else int(os.environ.get("LAYA_SPAN_SINK_MAX_BYTES", DEFAULT_MAX_BYTES))
        self.queue: queue.Queue[bytes | None] = queue.Queue(maxsize=capacity)
        self.dropped = 0
        self._last_error: float | None = None
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._run, name="laya-span-writer", daemon=True)
        self._thread.start()

    def submit(self, record: dict[str, Any]) -> None:
        try:
            line = json.dumps(record, separators=(",", ":"), allow_nan=False).encode() + b"\n"
            if len(line) > MAX_RECORD_BYTES: raise ValueError("record exceeds 4 KiB")
            self.queue.put_nowait(line)
        except (queue.Full, ValueError, TypeError):
            self.dropped += 1
            self._warn_drops()

    def _warn_drops(self) -> None:
        now = time.monotonic()
        with self._lock:
            if self._last_error is None or now - self._last_error >= 60:
                _LOG.warning("Laya span sink dropped %d records", self.dropped)
                self._last_error = now

    def _warn(self, exc: Exception) -> None:
        self.dropped += 1
        self._warn_drops()

    def _run(self) -> None:
        while True:
            line = self.queue.get()
            try:
                if line is None: return
                directory = os.path.dirname(self.path)
                os.makedirs(directory, mode=0o755, exist_ok=True)
                os.chmod(directory, 0o755)
                try: size = os.path.getsize(self.path)
                except FileNotFoundError: size = 0
                if size + len(line) > self.max_bytes and size:
                    try: os.replace(self.path, self.path + ".1")
                    except FileNotFoundError: pass
                fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
                try:
                    os.fchmod(fd, 0o644)
                    if os.write(fd, line) != len(line): raise OSError("short append")
                finally: os.close(fd)
            except Exception as exc:
                self._warn(exc)
            finally: self.queue.task_done()

    def close(self, timeout: float = 1.0) -> None:
        deadline = time.monotonic() + timeout
        try:
            self.queue.put(None, timeout=timeout)
        except queue.Full:
            return
        self._thread.join(max(0, deadline - time.monotonic()))


class RequestObserver:
    def __init__(self, app: Any, emit: Callable[[dict[str, Any]], None]) -> None:
        self.app, self.emit = app, emit

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        path = scope.get("path", "")
        if scope.get("type") != "http" or scope.get("method") != "POST" or path not in ("/v1/systemone", "/v1/systemone/batch"):
            await self.app(scope, receive, send); return
        started_wall = datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        started = time.monotonic()
        req, res = bytearray(), bytearray()
        req_bytes = res_bytes = 0
        status = 500
        async def tapped_receive():
            nonlocal req_bytes
            msg = await receive(); body = msg.get("body", b""); req_bytes += len(body)
            req.extend(body[:max(0, MAX_CAPTURE_BYTES-len(req))]); return msg
        async def tapped_send(msg):
            nonlocal status, res_bytes
            if msg["type"] == "http.response.start": status = msg["status"]
            if msg["type"] == "http.response.body":
                body = msg.get("body", b""); res_bytes += len(body)
                res.extend(body[:max(0, MAX_CAPTURE_BYTES-len(res))])
            await send(msg)
        error = None
        try:
            await self.app(scope, tapped_receive, tapped_send)
        except TimeoutError:
            error = "timeout"; raise
        except Exception:
            error = "exception"; raise
        finally:
            try: self.emit(_record(path, bytes(req), bytes(res), status, started_wall,
                                   round((time.monotonic()-started)*1000, 1),
                                   truncated=req_bytes > len(req) or res_bytes > len(res), error_type=error))
            except Exception: pass


def _register_shutdown(sink: BoundedFileSink) -> None:
    atexit.register(sink.close)


def install() -> None:
    import laya.serve as serve
    original = serve.create_app
    sink = BoundedFileSink()
    _register_shutdown(sink)
    serve.create_app = lambda *a, **kw: RequestObserver(original(*a, **kw), sink.submit)
    serve.main()


if __name__ == "__main__": install()
