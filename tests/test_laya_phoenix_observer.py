from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path
from typing import Any

MODULE = Path(__file__).parents[1] / "docker/system-one/phoenix_observer.py"
spec = importlib.util.spec_from_file_location("phoenix_observer", MODULE)
assert spec and spec.loader
observer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(observer)


def test_asgi_observer_records_real_request_and_typed_response_without_auth() -> None:
    records: list[dict[str, Any]] = []
    body = json.dumps({"state": {"case": "owned-marker", "api_key": "never-export"}, "questions": {
        "route": {"type": "choice", "instructions": "choose", "criteria": {"a": "A"}}
    }}).encode()

    async def app(scope, receive, send):
        assert (await receive())["body"].find(b"owned-marker") >= 0
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": json.dumps({
            "model": "english", "answers": {"route": {"type": "choice", "choice": "a",
            "probabilities": {"a": 0.91}, "confidence": 0.87}},
            "usage": {"input_tokens": 9, "output_tokens": 1, "truncated": False}
        }).encode()})

    messages = iter([
        {"type": "http.request", "body": body, "more_body": False},
    ])

    async def receive():
        return next(messages)

    async def send(message):
        pass

    wrapped = observer.RequestObserver(app, records.append)
    asyncio.run(wrapped({"type": "http", "method": "POST", "path": "/v1/systemone",
                         "headers": [(b"authorization", b"Bearer must-not-be-recorded")]}, receive, send))

    assert len(records) == 1
    record = records[0]
    assert record["input"]["state"]["api_key"] == "[REDACTED]"
    assert record["output"]["answers"]["route"]["probabilities"] == {"a": 0.91}
    assert record["http_status"] == 200
    assert "authorization" not in json.dumps(record).lower()


def test_asgi_observer_records_http_validation_error_and_batch_cardinality() -> None:
    records: list[dict[str, Any]] = []
    body = json.dumps({"states": [{"case": "one"}, {"case": "two"}], "questions": {}}).encode()

    async def app(scope, receive, send):
        await receive()
        await send({"type": "http.response.start", "status": 422, "headers": []})
        await send({"type": "http.response.body", "body": b'{"detail":"invalid question"}'})

    messages = iter([{"type": "http.request", "body": body, "more_body": False}])
    async def receive(): return next(messages)
    async def send(message): pass
    wrapped = observer.RequestObserver(app, records.append)
    asyncio.run(wrapped({"type": "http", "method": "POST", "path": "/v1/systemone/batch", "headers": []}, receive, send))
    assert records[0]["http_status"] == 422
    assert records[0]["batch_items"] == 2
    assert records[0]["output"]["detail"] == "invalid question"
    assert records[0]["span"]["attributes"]["laya.batch"] is True
    assert records[0]["span"]["attributes"]["laya.http_status"] == 422


def test_exporter_rejects_nonlocal_or_unexpected_endpoints() -> None:
    for endpoint in ("https://phoenix:6006/v1/projects/laya/spans",
                     "http://example.invalid/v1/projects/laya/spans",
                     "http://phoenix:6007/v1/projects/laya/spans",
                     "http://user@phoenix:6006/v1/projects/laya/spans"):
        try:
            observer.BoundedPhoenixExporter(endpoint, post=lambda body: None)
        except ValueError:
            continue
        raise AssertionError(f"unsafe endpoint accepted: {endpoint}")


def test_request_body_is_forwarded_incrementally_and_capture_is_bounded() -> None:
    records: list[dict[str, Any]] = []
    original = b'{"state":{"text":"' + b'x' * (observer.MAX_CAPTURE_BYTES + 500) + b'"},"questions":{}}'
    calls = 0
    async def receive():
        nonlocal calls
        calls += 1
        return {"type": "http.request", "body": original if calls == 1 else b"", "more_body": calls == 1}
    async def app(scope, incoming, send):
        first = await incoming()
        second = await incoming()
        assert first["body"] == original
        assert second["type"] == "http.request"
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})
    async def send(message): pass
    asyncio.run(observer.RequestObserver(app, records.append)(
        {"type": "http", "method": "POST", "path": "/v1/systemone"}, receive, send))
    assert records[0]["input"]["_capture_truncated"] is True
    assert records[0]["input"]["_total_bytes"] == len(original)
    assert records[0]["span"]["attributes"]["laya.request_truncated"] is True


def test_native_single_contract_counts_answers_and_maps_usage_tokens() -> None:
    request = json.dumps({"state": {"tag": "owned-single"}, "questions": {"a": {}, "b": {}}}).encode()
    response = json.dumps({"model": "typed-decisions", "answers": {
        "a": {"type": "choice", "choice": "x", "probabilities": {"x": 0.9}, "confidence": 0.8},
        "b": {"type": "score", "score": 3, "probabilities": {"3": 0.7}, "confidence": 0.6, "legend": {}}},
        "usage": {"input_tokens": 11, "output_tokens": 2, "truncated": True, "input_truncated": False},
        "routing": {"model": "typed-decisions"}}).encode()
    record = observer._record("/v1/systemone", request, response, 200, 1000.0, 3)
    attrs = record["span"]["attributes"]
    assert attrs["laya.completed_answers"] == 2
    assert attrs["laya.requested_questions"] == 2
    assert attrs["llm.token_count.prompt"] == 11
    assert attrs["llm.token_count.completion"] == 2
    assert attrs["input.value"] and attrs["output.value"]


def test_native_batch_contract_records_each_result_and_total_usage() -> None:
    request = json.dumps({"states": [{"id": 1}, {"id": 2}], "questions": {"q": {}}}).encode()
    response = json.dumps({"results": [
        {"model": "english", "answers": {"q": {"type": "noul", "noul": True}},
         "usage": {"input_tokens": 4, "output_tokens": 1}},
        {"model": "english", "answers": {"q": {"type": "choice", "choice": "a"}},
         "usage": {"input_tokens": 5, "output_tokens": 2}}],
        "total_usage": {"input_tokens": 9, "output_tokens": 3}}).encode()
    attrs = observer._record("/v1/systemone/batch", request, response, 200, 1000.0, 4)["span"]["attributes"]
    assert attrs["laya.batch_items"] == 2
    assert attrs["laya.requested_questions"] == 1
    assert attrs["laya.completed_answers"] == 2
    assert attrs["llm.token_count.prompt"] == 9
    assert attrs["llm.token_count.completion"] == 3


def test_native_unknown_usage_does_not_coerce_boolean_or_report_zero() -> None:
    request = b'{"state":{},"questions":{}}'
    response = b'{"model":"english","answers":{},"usage":{"input_tokens":true}}'
    attrs = observer._record("/v1/systemone", request, response, 200, 1.0, 0)["span"]["attributes"]
    assert attrs["laya.usage_known"] is False
    assert "llm.token_count.prompt" not in attrs


def test_truncated_batch_request_omits_unknown_cardinality_counts() -> None:
    request = json.dumps({
        "states": [{"id": i, "payload": "x" * 400} for i in range(80)],
        "questions": {"q": {"instructions": "x" * 400}},
    }).encode()
    record = observer._record(
        "/v1/systemone/batch", request[:observer.MAX_CAPTURE_BYTES], b'{"results":[]}',
        200, 1.0, 0, request_truncated=True, request_bytes=len(request),
    )
    attrs = record["span"]["attributes"]
    assert "laya.batch_items" not in attrs
    assert "laya.requested_questions" not in attrs
    assert record["batch_items"] is None


def test_empty_parsed_batch_has_known_zero_cardinality() -> None:
    record = observer._record(
        "/v1/systemone/batch", b'{"states":[],"questions":{}}', b'{"results":[]}', 200, 1.0, 0
    )
    attrs = record["span"]["attributes"]
    assert attrs["laya.batch_items"] == 0
    assert attrs["laya.requested_questions"] == 0
    assert attrs["laya.completed_answers"] == 0


def test_unparseable_request_and_response_do_not_fabricate_zero_counts() -> None:
    record = observer._record(
        "/v1/systemone/batch", b'{"states":[', b'{"results":[', 200, 1.0, 0
    )
    attrs = record["span"]["attributes"]
    assert "laya.batch_items" not in attrs
    assert "laya.requested_questions" not in attrs
    assert "laya.completed_answers" not in attrs


def test_truncated_response_omits_unknown_completed_answer_count() -> None:
    response = json.dumps({"results": [{"answers": {"q": {"type": "choice"}}}, {"answers": {}}]}).encode()
    record = observer._record(
        "/v1/systemone/batch", b'{"states":[{},{}],"questions":{"q":{}}}',
        response[:observer.MAX_CAPTURE_BYTES], 200, 1.0, 0,
        response_truncated=True, response_bytes=len(response) + 1,
    )
    assert "laya.completed_answers" not in record["span"]["attributes"]


def test_span_identity_uses_two_uuid_values_with_valid_hex_lengths(monkeypatch) -> None:
    values = iter(["a" * 32, "b" * 32])
    calls = 0

    class UUID:
        def __init__(self, value: str): self.hex = value

    def uuid4():
        nonlocal calls
        calls += 1
        return UUID(next(values))

    monkeypatch.setattr(observer.uuid, "uuid4", uuid4)
    record = observer._record("/v1/systemone", b"{}", b"{}", 200, 1.0, 0)
    context = record["span"]["context"]
    assert context == {"trace_id": "a" * 32, "span_id": "b" * 16}
    assert calls == 2


def test_error_detail_structures_are_serialized_safely() -> None:
    record = observer._record("/v1/systemone", b'{}', b'{"detail":{"errors":["bad"]}}', 422, 1.0, 0)
    assert record["span"]["attributes"]["error.message"] == '{"errors":["bad"]}'


def test_oversized_input_is_marked_truncated_and_distribution_remains_inspectable() -> None:
    request = json.dumps({"state": {"text": "x" * 100000}, "questions": {"q": {"type": "choice"}}}).encode()
    response = json.dumps({"model": "english", "answers": {"q": {
        "type": "choice", "choice": "a", "probabilities": {"a": 0.75, "b": 0.25}, "confidence": 0.8
    }}, "usage": {"input_tokens": 15, "output_tokens": 1, "truncated": True}}).encode()
    record = observer._record("/v1/systemone", request, response, 200, 1000.0, 12)
    attrs = record["span"]["attributes"]
    assert attrs["laya.record_truncated"] is True
    assert attrs["laya.usage_known"] is True
    assert attrs["llm.token_count.prompt"] == 15
    assert "probabilities" in attrs["output.value"]
    assert "[record truncated]" in attrs["input.value"]
