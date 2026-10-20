"""Best-effort bounded ASGI request/response export to the local Phoenix service."""
from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
import urllib.error
import urllib.request
import urllib.parse
import uuid
from datetime import UTC, datetime
from typing import Any, Callable

MAX_RECORD_BYTES = 65536
MAX_CAPTURE_BYTES = 24000
MAX_QUEUE = 128
_LOG = logging.getLogger("laya.phoenix")
SECRET_KEYS = {"authorization", "api_key", "apikey", "token", "password", "secret"}


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: ("[REDACTED]" if k.lower().replace("-", "_") in SECRET_KEYS else _redact(v))
                for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(v) for v in value]
    return value


def _decode(body: bytes, *, truncated: bool = False, total_bytes: int | None = None) -> Any:
    try:
        decoded = json.loads(body) if body else None
    except (ValueError, UnicodeDecodeError):
        decoded = {"_captured_prefix": body.decode("utf-8", errors="replace")}
    if truncated:
        if isinstance(decoded, dict):
            decoded["_capture_truncated"] = True
            decoded["_captured_bytes"] = len(body)
            decoded["_total_bytes"] = total_bytes
        else:
            decoded = {"_captured_value": decoded, "_capture_truncated": True,
                       "_captured_bytes": len(body), "_total_bytes": total_bytes}
    return decoded


def _record(path: str, request: bytes, response: bytes, status: int, started: float, duration_ms: int,
            *, request_truncated: bool = False, request_bytes: int | None = None,
            response_truncated: bool = False, response_bytes: int | None = None) -> dict[str, Any]:
    source = _decode(request, truncated=request_truncated, total_bytes=request_bytes)
    result = _decode(response, truncated=response_truncated, total_bytes=response_bytes)
    batch = path.endswith("/batch")
    request_parsed = isinstance(source, dict) and "_captured_prefix" not in source and not request_truncated
    response_parsed = isinstance(result, dict) and "_captured_prefix" not in result and not response_truncated
    states = source.get("states") if batch and request_parsed else None
    batch_items = len(states) if isinstance(states, list) else (1 if not batch else None)
    questions = source.get("questions") if request_parsed else None
    requested_questions = len(questions) if isinstance(questions, dict) else None
    outputs = result.get("results") if batch and response_parsed else None
    single_answers = result.get("answers") if not batch and response_parsed else None
    model = result.get("model") if isinstance(result, dict) else None
    if not model and isinstance(outputs, list):
        selected = [item.get("model") for item in outputs if isinstance(item, dict) and item.get("model")]
        model = selected[0] if selected and len(set(selected)) == 1 else ("mixed" if selected else None)
    usage = (result.get("total_usage") if batch else result.get("usage")) if isinstance(result, dict) else None
    usage_known = isinstance(usage, dict) and all(
        isinstance(usage.get(k), int) and not isinstance(usage.get(k), bool)
        for k in ("input_tokens", "output_tokens"))
    answer_sets = outputs if batch and isinstance(outputs, list) else None
    completed_answers = None
    if batch and isinstance(answer_sets, list) and all(
        isinstance(item, dict) and isinstance(item.get("answers"), dict) for item in answer_sets
    ):
        completed_answers = sum(len(item["answers"]) for item in answer_sets)
    elif not batch and isinstance(single_answers, dict):
        completed_answers = len(single_answers)
    attributes: dict[str, Any] = {
        "openinference.span.kind": "LLM",
        "llm.model_name": model or "unknown",
        "laya.http_status": status,
        "laya.batch": path.endswith("/batch"),
        "laya.endpoint": path,
        "input.value": json.dumps(_redact(source), ensure_ascii=False, separators=(",", ":")),
        "input.mime_type": "application/json",
        "output.value": json.dumps(_redact(result), ensure_ascii=False, separators=(",", ":")),
        "output.mime_type": "application/json",
        "laya.duration_ms": duration_ms,
        "laya.usage_known": usage_known,
    }
    if batch_items is not None:
        attributes["laya.batch_items"] = batch_items
    if requested_questions is not None:
        attributes["laya.requested_questions"] = requested_questions
    if completed_answers is not None:
        attributes["laya.completed_answers"] = completed_answers
    if request_truncated:
        attributes["laya.request_truncated"] = True
    if response_truncated:
        attributes["laya.response_truncated"] = True
    if isinstance(result, dict) and isinstance(result.get("routing"), (dict, list)):
        attributes["laya.routing"] = json.dumps(result["routing"], separators=(",", ":"))
    if isinstance(usage, dict):
        for name, phoenix_name in (("input_tokens", "prompt"), ("output_tokens", "completion")):
            value = usage.get(name)
            if isinstance(value, int) and not isinstance(value, bool):
                attributes[f"llm.token_count.{phoenix_name}"] = value
        attributes["laya.usage"] = json.dumps(usage, separators=(",", ":"))
    if status >= 400:
        attributes["error.type"] = "http_error"
        detail = result.get("detail", "HTTP error") if isinstance(result, dict) else "HTTP error"
        attributes["error.message"] = detail if isinstance(detail, str) else json.dumps(
            _redact(detail), ensure_ascii=False, separators=(",", ":"))
    span = {
        "name": "laya." + path.rsplit("/", 1)[-1],
        "context": {"trace_id": uuid.uuid4().hex, "span_id": uuid.uuid4().hex[:16]},
        "span_kind": "LLM", "parent_id": None,
        "start_time": datetime.fromtimestamp(started, UTC).isoformat(),
        "end_time": datetime.now(UTC).isoformat(),
        "status_code": "ERROR" if status >= 400 else "OK",
        "status_message": str(status) if status >= 400 else "",
        "attributes": attributes, "events": [],
    }
    encoded = json.dumps(span, ensure_ascii=False, separators=(",", ":")).encode()
    if len(encoded) > MAX_RECORD_BYTES:
        for key in ("input.value", "output.value", "laya.usage", "laya.routing"):
            if key in attributes and isinstance(attributes[key], str):
                attributes[key] = attributes[key][:4096] + "… [record truncated]"
        attributes["laya.record_truncated"] = True
    return {"input": _redact(source), "output": _redact(result), "http_status": status,
            "batch_items": batch_items, "duration_ms": duration_ms, "span": span}


class BoundedPhoenixExporter:
    """Nonblocking producer; one daemon worker with bounded queue and finite HTTP timeout."""
    def __init__(self, endpoint: str | None = None, *, capacity: int = MAX_QUEUE,
                 post: Callable[[bytes], None] | None = None) -> None:
        self.endpoint = endpoint or os.environ.get("LAYA_PHOENIX_URL", "http://phoenix:6006/v1/projects/laya/spans")
        parsed = urllib.parse.urlsplit(self.endpoint)
        if (parsed.scheme != "http" or parsed.hostname not in {"phoenix", "localhost", "127.0.0.1"}
                or parsed.port != 6006 or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path != "/v1/projects/laya/spans"):
            raise ValueError("Phoenix exporter URL must be an allowlisted local spans endpoint on port 6006")
        self.queue: queue.Queue[bytes] = queue.Queue(maxsize=capacity)
        self.dropped = 0
        self.errors = 0
        self._post = post or self._http_post
        threading.Thread(target=self._run, name="laya-phoenix-export", daemon=True).start()

    def submit(self, span: dict[str, Any]) -> None:
        body = json.dumps({"data": [span.get("span", span)]}, ensure_ascii=False,
                          separators=(",", ":")).encode()
        if len(body) > MAX_RECORD_BYTES:
            self.dropped += 1
            _LOG.warning("Phoenix export record exceeded byte cap; dropped_records=%d", self.dropped)
            return
        try:
            self.queue.put_nowait(body)
        except queue.Full:
            self.dropped += 1
            _LOG.warning("Phoenix export queue full; dropped_records=%d", self.dropped)

    def _http_post(self, body: bytes) -> None:
        request = urllib.request.Request(self.endpoint, data=body,
                                         headers={"Content-Type": "application/json"}, method="POST")
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        with opener.open(request, timeout=1.5) as response:
            if not 200 <= response.status < 300:
                raise OSError(f"Phoenix returned HTTP {response.status}")
            response.read(1024)

    def _run(self) -> None:
        while True:
            body = self.queue.get()
            try:
                self._post(body)
            except Exception as exc:
                self.errors += 1
                _LOG.warning("Phoenix export failed (%s); errors=%d", type(exc).__name__, self.errors)
            finally:
                self.queue.task_done()


class RequestObserver:
    def __init__(self, app: Any, emit: Callable[[dict[str, Any]], None]) -> None:
        self.app = app
        self.emit = emit

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        path = scope.get("path", "")
        if scope.get("type") != "http" or scope.get("method") != "POST" or path not in (
                "/v1/systemone", "/v1/systemone/batch"):
            await self.app(scope, receive, send)
            return
        started = time.time()
        monotonic_started = time.monotonic()
        request_prefix = bytearray()
        response_prefix = bytearray()
        request_bytes = 0
        response_bytes = 0
        status = 500
        async def tapped_receive():
            nonlocal request_bytes
            message = await receive()
            body = message.get("body", b"")
            request_bytes += len(body)
            remaining = MAX_CAPTURE_BYTES - len(request_prefix)
            if remaining > 0:
                request_prefix.extend(body[:remaining])
            return message
        async def capture(message: dict[str, Any]):
            nonlocal status, response_bytes
            if message["type"] == "http.response.start":
                status = message["status"]
            elif message["type"] == "http.response.body":
                body = message.get("body", b"")
                response_bytes += len(body)
                remaining = MAX_CAPTURE_BYTES - len(response_prefix)
                if remaining > 0:
                    response_prefix.extend(body[:remaining])
            await send(message)
        try:
            await self.app(scope, tapped_receive, capture)
        finally:
            try:
                self.emit(_record(path, bytes(request_prefix), bytes(response_prefix), status, started,
                                  round((time.monotonic() - monotonic_started) * 1000),
                                  request_truncated=request_bytes > len(request_prefix), request_bytes=request_bytes,
                                  response_truncated=response_bytes > len(response_prefix), response_bytes=response_bytes))
            except Exception:
                pass


def install() -> None:
    """Wrap the exact installed laya-serve app factory; inference implementation is untouched."""
    import laya.serve as serve
    original = serve.create_app
    exporter = BoundedPhoenixExporter()
    serve.create_app = lambda *a, **kw: RequestObserver(original(*a, **kw), exporter.submit)
    serve.main()


if __name__ == "__main__":
    install()
