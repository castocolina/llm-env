"""Phoenix trial: typed fixture contracts, durable artifacts and scoped REST.

Phase A synthetic trial only. This module owns the local v1 interchange
contracts (manifest, expected artifact, receipts), deterministic fixture
span generation, durable pre-POST host preparation and exact semantic
verification against the pinned Phoenix REST readback shape.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import statistics
import time
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal, TypedDict, cast
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

SCHEMA_VERSION = 1
FIXTURE_VERSION = "laya-synthetic-v1"
PROJECT = "laya-phoenix-trial"
SPACING_SECONDS = 180
RUN_ID_RE = re.compile(r"^[0-9a-f]{12}4[0-9a-f]{3}[89ab][0-9a-f]{15}$")
CANONICAL_TIME_RE = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})\.(\d{3})Z$"
)
HEX32_RE = re.compile(r"^[0-9a-f]{32}$")
HEX16_RE = re.compile(r"^[0-9a-f]{16}$")

# The complete pinned v1 table (key, duration_ms, request_type, items,
# questions_per_item, status, http_status). Fixed by the versioned contract.
V1_ROOTS: tuple[tuple[str, int, str, int, int, str, int], ...] = (
    ("r01", 10, "choice", 1, 1, "OK", 200),
    ("r02", 20, "score", 3, 2, "OK", 200),
    ("r03", 30, "noul", 1, 1, "OK", 200),
    ("r04", 40, "choice", 1, 1, "OK", 200),
    ("r05", 50, "score", 1, 1, "OK", 200),
    ("r06", 60, "noul", 2, 1, "OK", 200),
    ("r07", 70, "choice", 1, 1, "OK", 200),
    ("r08", 80, "score", 1, 1, "OK", 200),
    ("r09", 90, "noul", 1, 1, "OK", 200),
    ("r10", 100, "choice", 1, 1, "ERROR", 422),
    ("r11", 200, "score", 1, 1, "ERROR", 500),
    ("r12", 500, "noul", 1, 1, "ERROR", 503),
)
V1_CHILD = {"key": "c01", "parent_key": "r01", "duration_ms": 5, "end_offset_ms": 1}

V1_SUMMARY: dict[str, int | float] = {
    "spans": 13,
    "requests": 12,
    "ok_requests": 9,
    "error_requests": 3,
    "error_rate": 0.25,
    "median_ms": 65,
    "requested_items": 15,
    "requested_questions": 18,
    "completed_answers": 15,
    "batch_requests": 2,
    "recorded_input_tokens": 900,
    "recorded_output_tokens": 18,
    "usage_unknown_requests": 3,
}
SUMMARY_KEYS = tuple(V1_SUMMARY)

REQUEST_TYPES = ("choice", "score", "noul")
FIXTURE_VALUES = {"choice": "fixture-a", "score": 0.5, "noul": 0.25}

SPAN_KEYS = {
    "name",
    "context",
    "span_kind",
    "parent_id",
    "start_time",
    "end_time",
    "status_code",
    "status_message",
    "attributes",
    "events",
}
ROOT_ATTRIBUTE_BASE = {
    "laya.run_id",
    "metadata",
    "llm.model_name",
    "laya.request_type",
    "laya.http_status",
    "laya.requested_items",
    "laya.requested_questions",
    "laya.completed_answers",
    "laya.batch",
}


class PhoenixTrialError(ValueError):
    """Handled phoenix-trial contract/durability error."""


class Summary(TypedDict):
    spans: int
    requests: int
    ok_requests: int
    error_requests: int
    error_rate: float
    median_ms: float
    requested_items: int
    requested_questions: int
    completed_answers: int
    batch_requests: int
    recorded_input_tokens: int
    recorded_output_tokens: int
    usage_unknown_requests: int


class RootSpec(TypedDict):
    key: str
    duration_ms: int
    request_type: Literal["choice", "score", "noul"]
    items: int
    questions_per_item: int
    status: Literal["OK", "ERROR"]
    http_status: int


class ChildSpec(TypedDict):
    key: str
    parent_key: str
    duration_ms: int
    end_offset_ms: int


class Manifest(TypedDict):
    schema_version: Literal[1]
    fixture_version: Literal["laya-synthetic-v1"]
    project: Literal["laya-phoenix-trial"]
    spacing_seconds: int
    roots: list[RootSpec]
    child: ChildSpec
    expected_summary: Summary


class SpanContext(TypedDict):
    trace_id: str
    span_id: str


AttributeValue = str | int | float | bool


class Span(TypedDict):
    name: str
    context: SpanContext
    span_kind: Literal["LLM", "CHAIN"]
    parent_id: str | None
    start_time: str
    end_time: str
    status_code: Literal["OK", "ERROR"]
    status_message: str
    attributes: dict[str, AttributeValue]
    events: list


class ExpectedRun(TypedDict):
    schema_version: Literal[1]
    fixture_version: Literal["laya-synthetic-v1"]
    project: Literal["laya-phoenix-trial"]
    run_id: str
    base_time: str
    spans: list[Span]
    summary: Summary


class PreparedReceipt(TypedDict):
    schema_version: Literal[1]
    status: Literal["prepared"]
    project: Literal["laya-phoenix-trial"]
    run_id: str
    expected_path: str


class AttemptReceipt(TypedDict):
    schema_version: Literal[1]
    run_id: str
    expected_path: str
    post_may_have_started: Literal[True]


class VerifiedReceipt(TypedDict):
    schema_version: Literal[1]
    status: Literal["verified"]
    project: Literal["laya-phoenix-trial"]
    run_id: str
    summary: Summary
    verified_span_ids: list[str]


# ---------------------------------------------------------------------------
# Strict JSON and primitive validation
# ---------------------------------------------------------------------------


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise PhoenixTrialError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_nonfinite_constant(value: str) -> None:
    raise PhoenixTrialError(f"non-finite JSON constant: {value}")


def _loads_object(text: str) -> dict[str, object]:
    try:
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonfinite_constant,
        )
    except PhoenixTrialError:
        raise
    except ValueError as exc:
        raise PhoenixTrialError(f"invalid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise PhoenixTrialError("top-level JSON value must be an object")
    return value


def loads_strict_object(text: str) -> dict[str, object]:
    """Decode a strict JSON object, rejecting duplicate keys and non-finite values."""
    return _loads_object(text)


def _require_exact_keys(value: object, keys: set[str], what: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise PhoenixTrialError(f"{what} must be an object")
    extra = set(value) - keys
    missing = keys - set(value)
    if extra or missing:
        parts = []
        if extra:
            parts.append(f"unexpected keys: {sorted(extra)}")
        if missing:
            parts.append(f"missing keys: {sorted(missing)}")
        raise PhoenixTrialError(f"{what} has {'; '.join(parts)}")
    return value


def _require_int(value: object, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise PhoenixTrialError(f"{what} must be an integer")
    return value


def _require_count(value: object, what: str) -> int:
    count = _require_int(value, what)
    if count < 0:
        raise PhoenixTrialError(f"{what} must be nonnegative")
    return count


def _require_probability(value: object, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PhoenixTrialError(f"{what} must be a number")
    number = float(value)
    if not math.isfinite(number) or not 0 <= number <= 1:
        raise PhoenixTrialError(f"{what} must be a finite probability in [0, 1]")
    return number


def _require_str(value: object, what: str) -> str:
    if not isinstance(value, str):
        raise PhoenixTrialError(f"{what} must be a string")
    return value


def _validate_run_id(run_id: object) -> str:
    run_id = _require_str(run_id, "run_id")
    if not RUN_ID_RE.fullmatch(run_id):
        raise PhoenixTrialError("run_id must be a lowercase UUID4 hex string")
    return run_id


# ---------------------------------------------------------------------------
# Canonical time
# ---------------------------------------------------------------------------


def canonical_time(value: datetime) -> str:
    """Format an aware datetime as canonical UTC millisecond time."""
    if value.tzinfo is None:
        raise PhoenixTrialError("datetime must be timezone-aware")
    utc = value.astimezone(UTC)
    milliseconds = utc.microsecond // 1000
    return utc.strftime("%Y-%m-%dT%H:%M:%S.") + f"{milliseconds:03d}Z"


def parse_canonical_time(value: object, what: str = "timestamp") -> datetime:
    text = _require_str(value, what)
    match = CANONICAL_TIME_RE.fullmatch(text)
    if not match:
        raise PhoenixTrialError(f"{what} must be canonical UTC milliseconds: {text!r}")
    year, month, day, hour, minute, second, millisecond = (
        int(group) for group in match.groups()
    )
    try:
        return datetime(
            year,
            month,
            day,
            hour,
            minute,
            second,
            millisecond * 1000,
            tzinfo=UTC,
        )
    except ValueError as exc:
        raise PhoenixTrialError(f"{what} is not a valid time: {text!r}") from exc


def _duration_ms(start: str, end: str, what: str) -> int:
    start_dt = parse_canonical_time(start, f"{what} start_time")
    end_dt = parse_canonical_time(end, f"{what} end_time")
    delta = end_dt - start_dt
    if delta.total_seconds() <= 0:
        raise PhoenixTrialError(f"{what} duration must be positive")
    if delta.microseconds % 1000 != 0:
        raise PhoenixTrialError(f"{what} duration has sub-millisecond precision")
    return int(delta.total_seconds() * 1000)


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


def _manifest_fixture() -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "fixture_version": FIXTURE_VERSION,
        "project": PROJECT,
        "spacing_seconds": SPACING_SECONDS,
        "roots": [
            {
                "key": key,
                "duration_ms": duration_ms,
                "request_type": request_type,
                "items": items,
                "questions_per_item": questions_per_item,
                "status": status,
                "http_status": http_status,
            }
            for key, duration_ms, request_type, items, questions_per_item, status, http_status in V1_ROOTS
        ],
        "child": dict(V1_CHILD),
        "expected_summary": dict(V1_SUMMARY),
    }


def load_manifest(path: Path) -> Manifest:
    """Load and strictly validate the checked-in v1 manifest."""
    path = Path(path)
    if path.is_symlink():
        raise PhoenixTrialError(f"manifest path is a symlink: {path}")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PhoenixTrialError(f"could not read manifest: {exc}") from exc
    value = _loads_object(text)
    expected = _manifest_fixture()
    envelope = _require_exact_keys(value, set(expected), "manifest")
    if isinstance(envelope["schema_version"], bool) or envelope["schema_version"] != SCHEMA_VERSION:
        raise PhoenixTrialError("unsupported manifest schema_version")
    if envelope["fixture_version"] != FIXTURE_VERSION:
        raise PhoenixTrialError("unsupported fixture_version")
    if envelope["project"] != PROJECT:
        raise PhoenixTrialError("unsupported manifest project")
    spacing = _require_int(envelope["spacing_seconds"], "spacing_seconds")
    if spacing != SPACING_SECONDS:
        raise PhoenixTrialError("unsupported spacing_seconds")
    rows = envelope["roots"]
    if not isinstance(rows, list):
        raise PhoenixTrialError("roots must be a list")
    fixture_rows = cast(list[dict[str, object]], expected["roots"])
    if rows != fixture_rows:
        raise PhoenixTrialError("roots do not match the pinned laya-synthetic-v1 table")
    if envelope["child"] != expected["child"]:
        raise PhoenixTrialError("child does not match the pinned laya-synthetic-v1 spec")
    summary = envelope["expected_summary"]
    if summary != expected["expected_summary"]:
        raise PhoenixTrialError("expected_summary does not match the pinned laya-synthetic-v1 summary")
    return cast(Manifest, value)


# ---------------------------------------------------------------------------
# Deterministic span generation
# ---------------------------------------------------------------------------


def _identity(fixture_version: str, run_id: str, base_time: str, kind: str, key: str, length: int) -> str:
    digest = hashlib.sha256(
        f"{fixture_version}|{run_id}|{base_time}|{kind}|{key}".encode()
    ).hexdigest()
    value = digest[:length]
    if set(value) == {"0"}:
        raise PhoenixTrialError(f"derived zero {kind} id for {key}")
    return value


def _metadata_json(run_id: str) -> str:
    return json.dumps(
        {
            "data_source": "synthetic",
            "fixture_version": FIXTURE_VERSION,
            "run_id": run_id,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _root_attributes(row: dict[str, object], run_id: str) -> dict[str, AttributeValue]:
    request_type = cast(str, row["request_type"])
    items = cast(int, row["items"])
    questions_per_item = cast(int, row["questions_per_item"])
    questions = items * questions_per_item
    attributes: dict[str, AttributeValue] = {
        "laya.run_id": run_id,
        "metadata": _metadata_json(run_id),
        "llm.model_name": "synthetic-laya",
        "laya.request_type": request_type,
        "laya.http_status": cast(int, row["http_status"]),
        "laya.requested_items": items,
        "laya.requested_questions": questions,
        "laya.completed_answers": questions if row["status"] == "OK" else 0,
        "laya.batch": items > 1,
    }
    if row["status"] == "OK":
        result = [
            {"item": item, "question": question, "value": FIXTURE_VALUES[request_type]}
            for item in range(1, items + 1)
            for question in range(1, questions_per_item + 1)
        ]
        attributes["llm.token_count.prompt"] = 100
        attributes["llm.token_count.completion"] = 2
        attributes["laya.result_type"] = request_type
        attributes["laya.result"] = json.dumps(result, sort_keys=True, separators=(",", ":"))
        if request_type == "choice":
            attributes["laya.confidence"] = 0.8
            attributes["laya.x_jev_confidence"] = 0.7
            attributes["laya.answer_confidence"] = 0.9
        elif request_type == "score":
            attributes["laya.answer_confidence"] = 0.6
        else:
            attributes["laya.noul_probability"] = 0.25
    return attributes


def build_fixture_spans(
    manifest: Manifest, *, run_id: str, base_time: datetime
) -> list[Span]:
    """Generate the 13 deterministic v1 spans for one run."""
    _validate_run_id(run_id)
    base_text = canonical_time(base_time)
    base = parse_canonical_time(base_text, "base_time")
    spacing = manifest["spacing_seconds"]
    spans: list[Span] = []
    by_key: dict[str, Span] = {}
    for index, row in enumerate(manifest["roots"]):
        key = row["key"]
        end = base - timedelta(seconds=index * spacing)
        start = end - timedelta(milliseconds=row["duration_ms"])
        span: Span = {
            "name": f"synthetic.{key}",
            "context": {
                "trace_id": _identity(
                    manifest["fixture_version"], run_id, base_text, "trace", key, 32
                ),
                "span_id": _identity(
                    manifest["fixture_version"], run_id, base_text, "span", key, 16
                ),
            },
            "span_kind": "LLM",
            "parent_id": None,
            "start_time": canonical_time(start),
            "end_time": canonical_time(end),
            "status_code": row["status"],
            "status_message": (
                "" if row["status"] == "OK" else f"synthetic HTTP {row['http_status']}"
            ),
            "attributes": _root_attributes(cast(dict[str, object], row), run_id),
            "events": [],
        }
        spans.append(span)
        by_key[key] = span
    child = manifest["child"]
    parent = by_key[child["parent_key"]]
    parent_end = parse_canonical_time(parent["end_time"], "parent end_time")
    child_end = parent_end - timedelta(milliseconds=child["end_offset_ms"])
    child_start = child_end - timedelta(milliseconds=child["duration_ms"])
    key = child["key"]
    spans.append(
        {
            "name": f"synthetic.{key}",
            "context": {
                "trace_id": parent["context"]["trace_id"],
                "span_id": _identity(
                    manifest["fixture_version"], run_id, base_text, "span", key, 16
                ),
            },
            "span_kind": "CHAIN",
            "parent_id": parent["context"]["span_id"],
            "start_time": canonical_time(child_start),
            "end_time": canonical_time(child_end),
            "status_code": "OK",
            "status_message": "",
            "attributes": {"laya.run_id": run_id, "metadata": _metadata_json(run_id)},
            "events": [],
        }
    )
    return spans


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


def _count_attribute(attributes: Mapping[str, object], key: str, what: str) -> int:
    if key not in attributes:
        raise PhoenixTrialError(f"{what} is missing required attribute {key}")
    return _require_count(attributes[key], f"{what} attribute {key}")


def summarize_spans(spans: list[Span], *, run_id: str) -> Summary:
    """Compute the root-only v1 summary, rejecting malformed records.

    Missing counts are never filled with zero: malformed selected records
    raise PhoenixTrialError.
    """
    _validate_run_id(run_id)
    if not isinstance(spans, list) or not spans:
        raise PhoenixTrialError("spans must be a nonempty list")
    roots: list[Span] = []
    for position, span in enumerate(spans):
        if not isinstance(span, dict) or not SPAN_KEYS <= set(span):
            raise PhoenixTrialError(f"span at index {position} has an invalid shape")
        if span["parent_id"] is None:
            roots.append(cast(Span, span))
    if not roots:
        raise PhoenixTrialError("no root spans selected")
    durations: list[int] = []
    ok_requests = 0
    error_requests = 0
    requested_items = 0
    requested_questions = 0
    completed_answers = 0
    batch_requests = 0
    input_tokens = 0
    output_tokens = 0
    usage_unknown = 0
    for span in roots:
        attributes = span["attributes"]
        if not isinstance(attributes, dict):
            raise PhoenixTrialError(f"{span['name']} attributes must be an object")
        status = span["status_code"]
        durations.append(_duration_ms(span["start_time"], span["end_time"], span["name"]))
        items = _count_attribute(attributes, "laya.requested_items", span["name"])
        questions = _count_attribute(attributes, "laya.requested_questions", span["name"])
        completed = _count_attribute(attributes, "laya.completed_answers", span["name"])
        batch = attributes.get("laya.batch")
        if not isinstance(batch, bool):
            raise PhoenixTrialError(f"{span['name']} attribute laya.batch must be a boolean")
        has_prompt = "llm.token_count.prompt" in attributes
        has_completion = "llm.token_count.completion" in attributes
        if has_prompt != has_completion:
            raise PhoenixTrialError(
                f"{span['name']} has partial token usage attributes"
            )
        if status == "ERROR":
            error_requests += 1
            if has_prompt:
                raise PhoenixTrialError(
                    f"{span['name']} is ERROR but carries token usage"
                )
            usage_unknown += 1
        elif status == "OK":
            ok_requests += 1
            if not has_prompt:
                raise PhoenixTrialError(
                    f"{span['name']} is OK but omits recorded token usage"
                )
            input_tokens += _count_attribute(
                attributes, "llm.token_count.prompt", span["name"]
            )
            output_tokens += _count_attribute(
                attributes, "llm.token_count.completion", span["name"]
            )
        else:
            raise PhoenixTrialError(f"{span['name']} has invalid status {status!r}")
        requested_items += items
        requested_questions += questions
        completed_answers += completed
        batch_requests += 1 if batch else 0
    requests = len(roots)
    median = statistics.median(durations)
    median_ms: int | float = int(median) if float(median).is_integer() else median
    return {
        "spans": len(spans),
        "requests": requests,
        "ok_requests": ok_requests,
        "error_requests": error_requests,
        "error_rate": error_requests / requests,
        "median_ms": median_ms,
        "requested_items": requested_items,
        "requested_questions": requested_questions,
        "completed_answers": completed_answers,
        "batch_requests": batch_requests,
        "recorded_input_tokens": input_tokens,
        "recorded_output_tokens": output_tokens,
        "usage_unknown_requests": usage_unknown,
    }


def make_expected(
    manifest: Manifest, *, run_id: str, base_time: datetime
) -> ExpectedRun:
    """Build the immutable expected-run artifact envelope for one run."""
    run_id = _validate_run_id(run_id)
    spans = build_fixture_spans(manifest, run_id=run_id, base_time=base_time)
    return {
        "schema_version": SCHEMA_VERSION,
        "fixture_version": FIXTURE_VERSION,
        "project": PROJECT,
        "run_id": run_id,
        "base_time": canonical_time(base_time),
        "spans": spans,
        "summary": summarize_spans(spans, run_id=run_id),
    }


# ---------------------------------------------------------------------------
# Expected artifact validation
# ---------------------------------------------------------------------------


def _validate_metadata(value: object, run_id: str, what: str) -> None:
    text = _require_str(value, f"{what} attribute metadata")
    try:
        metadata = _loads_object(text)
    except PhoenixTrialError as exc:
        raise PhoenixTrialError(f"{what} attribute metadata is invalid: {exc}") from exc
    expected = {"data_source": "synthetic", "fixture_version": FIXTURE_VERSION, "run_id": run_id}
    if metadata != expected:
        raise PhoenixTrialError(f"{what} attribute metadata does not match the v1 contract")


def _validate_result(
    value: object, row: dict[str, object], what: str
) -> None:
    text = _require_str(value, f"{what} attribute laya.result")
    try:
        decoded = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonfinite_constant,
        )
    except PhoenixTrialError:
        raise
    except ValueError as exc:
        raise PhoenixTrialError(f"{what} attribute laya.result is invalid JSON") from exc
    request_type = cast(str, row["request_type"])
    items = cast(int, row["items"])
    questions_per_item = cast(int, row["questions_per_item"])
    fixture = FIXTURE_VALUES[request_type]
    expected_records = [
        {"item": item, "question": question, "value": fixture}
        for item in range(1, items + 1)
        for question in range(1, questions_per_item + 1)
    ]
    if not isinstance(decoded, list) or decoded != expected_records:
        raise PhoenixTrialError(f"{what} attribute laya.result does not match v1 generation")
    for record in decoded:
        if not isinstance(record, dict) or set(record) != {"item", "question", "value"}:
            raise PhoenixTrialError(f"{what} attribute laya.result has an invalid record")


def _expected_root_attribute_keys(row: dict[str, object]) -> set[str]:
    keys = set(ROOT_ATTRIBUTE_BASE)
    if row["status"] == "OK":
        keys |= {"llm.token_count.prompt", "llm.token_count.completion", "laya.result_type", "laya.result"}
        request_type = cast(str, row["request_type"])
        if request_type == "choice":
            keys |= {"laya.confidence", "laya.x_jev_confidence", "laya.answer_confidence"}
        elif request_type == "score":
            keys |= {"laya.answer_confidence"}
        else:
            keys |= {"laya.noul_probability"}
    return keys


def _validate_root_attributes(
    attributes: dict[str, object], row: dict[str, object], run_id: str, what: str
) -> None:
    _require_exact_keys(attributes, _expected_root_attribute_keys(row), f"{what} attributes")
    if attributes["laya.run_id"] != run_id:
        raise PhoenixTrialError(f"{what} attribute laya.run_id does not match the run")
    _validate_metadata(attributes["metadata"], run_id, what)
    if attributes["llm.model_name"] != "synthetic-laya":
        raise PhoenixTrialError(f"{what} attribute llm.model_name is not synthetic-laya")
    if attributes["laya.request_type"] != row["request_type"]:
        raise PhoenixTrialError(f"{what} attribute laya.request_type does not match the table")
    http_status = _require_int(attributes["laya.http_status"], f"{what} laya.http_status")
    if http_status != row["http_status"] or not 100 <= http_status <= 599:
        raise PhoenixTrialError(f"{what} attribute laya.http_status does not match the table")
    items = _require_count(attributes["laya.requested_items"], f"{what} laya.requested_items")
    questions = _require_count(
        attributes["laya.requested_questions"], f"{what} laya.requested_questions"
    )
    completed = _require_count(
        attributes["laya.completed_answers"], f"{what} laya.completed_answers"
    )
    expected_questions = cast(int, row["items"]) * cast(int, row["questions_per_item"])
    if items != row["items"] or questions != expected_questions:
        raise PhoenixTrialError(f"{what} item/question counts do not match the table")
    expected_completed = expected_questions if row["status"] == "OK" else 0
    if completed != expected_completed:
        raise PhoenixTrialError(f"{what} completed answers do not match v1 semantics")
    if not isinstance(attributes["laya.batch"], bool) or attributes["laya.batch"] != (
        cast(int, row["items"]) > 1
    ):
        raise PhoenixTrialError(f"{what} attribute laya.batch does not match the table")
    if row["status"] == "OK":
        if _require_int(attributes["llm.token_count.prompt"], f"{what} prompt tokens") != 100:
            raise PhoenixTrialError(f"{what} prompt token count is not the v1 value")
        if (
            _require_int(attributes["llm.token_count.completion"], f"{what} completion tokens")
            != 2
        ):
            raise PhoenixTrialError(f"{what} completion token count is not the v1 value")
        if attributes["laya.result_type"] != row["request_type"]:
            raise PhoenixTrialError(f"{what} laya.result_type does not match the request type")
        _validate_result(attributes["laya.result"], row, what)
        pinned = {
            "laya.confidence": 0.8,
            "laya.x_jev_confidence": 0.7,
            "laya.answer_confidence": {"choice": 0.9, "score": 0.6},
            "laya.noul_probability": 0.25,
        }
        for key, expected_value in pinned.items():
            if key in attributes:
                probability = _require_probability(attributes[key], f"{what} {key}")
                if isinstance(expected_value, dict):
                    wanted = expected_value[cast(str, row["request_type"])]
                else:
                    wanted = expected_value
                if probability != wanted:
                    raise PhoenixTrialError(f"{what} {key} is not the pinned v1 value")


def validate_expected(value: object) -> ExpectedRun:
    """Validate an expected-run artifact against the full v1 contract.

    Envelope, deterministic identities/timestamps, graph, safe attributes
    and root-only summary must all agree; an edited summary cannot bless
    inconsistent spans and vice versa.
    """
    envelope = _require_exact_keys(
        value,
        {"schema_version", "fixture_version", "project", "run_id", "base_time", "spans", "summary"},
        "expected artifact",
    )
    if isinstance(envelope["schema_version"], bool) or envelope["schema_version"] != SCHEMA_VERSION:
        raise PhoenixTrialError("unsupported expected artifact schema_version")
    if envelope["fixture_version"] != FIXTURE_VERSION:
        raise PhoenixTrialError("unsupported expected artifact fixture_version")
    if envelope["project"] != PROJECT:
        raise PhoenixTrialError("unsupported expected artifact project")
    run_id = _validate_run_id(envelope["run_id"])
    base_text = _require_str(envelope["base_time"], "base_time")
    base = parse_canonical_time(base_text, "base_time")
    if canonical_time(base) != base_text:
        raise PhoenixTrialError("base_time is not canonical")

    fixture = cast(Manifest, _manifest_fixture())
    rows = fixture["roots"]
    table_by_key = {row["key"]: cast(dict[str, object], row) for row in rows}
    child_spec = cast(dict[str, object], fixture["child"])

    spans_value = envelope["spans"]
    if not isinstance(spans_value, list) or len(spans_value) != 13:
        raise PhoenixTrialError("expected artifact must contain exactly 13 spans")
    seen_span_ids: set[str] = set()
    roots: list[tuple[int, Span]] = []
    child_span: Span | None = None
    child_key = cast(str, child_spec["key"])
    for position, raw in enumerate(spans_value):
        span_envelope = _require_exact_keys(raw, SPAN_KEYS, f"span {position}")
        name = _require_str(span_envelope["name"], "span name")
        prefix, _, key = name.rpartition(".")
        if prefix != "synthetic" or (key not in table_by_key and key != child_key):
            raise PhoenixTrialError(f"span {position} has an unexpected name: {name!r}")
        context = _require_exact_keys(
            span_envelope["context"], {"trace_id", "span_id"}, f"{name} context"
        )
        trace_id = _require_str(context["trace_id"], f"{name} trace_id")
        span_id = _require_str(context["span_id"], f"{name} span_id")
        if not HEX32_RE.fullmatch(trace_id):
            raise PhoenixTrialError(f"{name} trace_id is not lowercase length-32 hex")
        if not HEX16_RE.fullmatch(span_id):
            raise PhoenixTrialError(f"{name} span_id is not lowercase length-16 hex")
        if set(trace_id) == {"0"} or set(span_id) == {"0"}:
            raise PhoenixTrialError(f"{name} has a zero id")
        if span_id in seen_span_ids:
            raise PhoenixTrialError(f"{name} duplicates span id {span_id}")
        seen_span_ids.add(span_id)
        trace_key = cast(str, child_spec["parent_key"]) if key == child_key else key
        expected_trace = _identity(
            FIXTURE_VERSION, run_id, base_text, "trace", trace_key, 32
        )
        expected_span = _identity(FIXTURE_VERSION, run_id, base_text, "span", key, 16)
        if trace_id != expected_trace or span_id != expected_span:
            raise PhoenixTrialError(f"{name} identities do not match deterministic v1 generation")
        kind = span_envelope["span_kind"]
        if kind not in ("LLM", "CHAIN"):
            raise PhoenixTrialError(f"{name} has invalid span_kind {kind!r}")
        if span_envelope["events"] != []:
            raise PhoenixTrialError(f"{name} events must be empty in v1")
        parent_id = span_envelope["parent_id"]
        status = span_envelope["status_code"]
        status_message = _require_str(span_envelope["status_message"], f"{name} status_message")
        attributes = span_envelope["attributes"]
        if not isinstance(attributes, dict):
            raise PhoenixTrialError(f"{name} attributes must be an object")
        span = cast(
            Span,
            {
                "name": name,
                "context": {"trace_id": trace_id, "span_id": span_id},
                "span_kind": kind,
                "parent_id": parent_id,
                "start_time": span_envelope["start_time"],
                "end_time": span_envelope["end_time"],
                "status_code": status,
                "status_message": status_message,
                "attributes": attributes,
                "events": [],
            },
        )
        parse_canonical_time(span["start_time"], f"{name} start_time")
        parse_canonical_time(span["end_time"], f"{name} end_time")
        span["start_time"] = canonical_time(parse_canonical_time(span["start_time"], f"{name} start_time"))
        span["end_time"] = canonical_time(parse_canonical_time(span["end_time"], f"{name} end_time"))

        if key == child_key:
            if child_span is not None:
                raise PhoenixTrialError("duplicate child span")
            child_span = span
            continue
        row = table_by_key[key]
        if parent_id is not None:
            raise PhoenixTrialError(f"{name} is a table root and must have a null parent")
        if kind != "LLM":
            raise PhoenixTrialError(f"{name} must have span_kind LLM")
        index = next(index for index, root_row in enumerate(rows) if root_row["key"] == key)
        end = parse_canonical_time(span["end_time"], f"{name} end_time")
        start = parse_canonical_time(span["start_time"], f"{name} start_time")
        expected_end = base - timedelta(seconds=index * SPACING_SECONDS)
        expected_start = expected_end - timedelta(milliseconds=cast(int, row["duration_ms"]))
        if end != expected_end or start != expected_start:
            raise PhoenixTrialError(f"{name} timestamps do not match deterministic v1 generation")
        if status != row["status"]:
            raise PhoenixTrialError(f"{name} status does not match the v1 table")
        wanted_message = "" if row["status"] == "OK" else f"synthetic HTTP {row['http_status']}"
        if status_message != wanted_message:
            raise PhoenixTrialError(f"{name} status_message does not match v1 semantics")
        _validate_root_attributes(attributes, row, run_id, name)
        roots.append((index, span))

    if child_span is None:
        raise PhoenixTrialError("expected artifact is missing the child span")
    if len(roots) != 12:
        raise PhoenixTrialError("expected artifact must contain exactly 12 root spans")
    parent_row = table_by_key[cast(str, child_spec["parent_key"])]
    parent_name = f"synthetic.{parent_spec_key(parent_row)}"
    parent = next((s for _, s in roots if s["name"] == parent_name), None)
    if parent is None:
        raise PhoenixTrialError("child parent root is missing")
    if child_span["span_kind"] != "CHAIN":
        raise PhoenixTrialError("child span must have span_kind CHAIN")
    if child_span["parent_id"] != parent["context"]["span_id"]:
        raise PhoenixTrialError("child parent_id does not link to its root")
    if child_span["context"]["trace_id"] != parent["context"]["trace_id"]:
        raise PhoenixTrialError("child trace_id does not match its root")
    if child_span["status_code"] != "OK" or child_span["status_message"] != "":
        raise PhoenixTrialError("child span status does not match v1 semantics")
    _require_exact_keys(
        child_span["attributes"], {"laya.run_id", "metadata"}, "synthetic.c01 attributes"
    )
    if child_span["attributes"]["laya.run_id"] != run_id:
        raise PhoenixTrialError("child attribute laya.run_id does not match the run")
    _validate_metadata(child_span["attributes"]["metadata"], run_id, "synthetic.c01")
    parent_end = parse_canonical_time(parent["end_time"], "parent end_time")
    child_end = parse_canonical_time(child_span["end_time"], "child end_time")
    child_start = parse_canonical_time(child_span["start_time"], "child start_time")
    wanted_end = parent_end - timedelta(milliseconds=cast(int, child_spec["end_offset_ms"]))
    wanted_start = wanted_end - timedelta(milliseconds=cast(int, child_spec["duration_ms"]))
    if child_end != wanted_end or child_start != wanted_start:
        raise PhoenixTrialError("child timestamps do not match v1 containment")
    if not (parse_canonical_time(parent["start_time"], "parent start_time") <= child_start):
        raise PhoenixTrialError("child span is not contained in its root")

    summary = envelope["summary"]
    if not isinstance(summary, dict) or set(summary) != set(SUMMARY_KEYS):
        raise PhoenixTrialError("summary does not have exactly the v1 fields")
    summary = cast(Summary, summary)
    recomputed = summarize_spans([*roots_spans(roots), child_span], run_id=run_id)
    if summary != recomputed or summary != cast(Summary, V1_SUMMARY):
        raise PhoenixTrialError("summary does not match the pinned v1 root-only summary")
    return cast(
        ExpectedRun,
        {
            "schema_version": SCHEMA_VERSION,
            "fixture_version": FIXTURE_VERSION,
            "project": PROJECT,
            "run_id": run_id,
            "base_time": base_text,
            "spans": [*roots_spans(roots), child_span],
            "summary": summary,
        },
    )


def load_expected_file(path: Path) -> ExpectedRun:
    """Load a strict expected artifact from a nonsymlink file."""
    path = Path(path)
    _reject_symlink(path, "expected artifact")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PhoenixTrialError(f"could not read expected artifact: {exc}") from exc
    return validate_expected(_loads_object(text))


def parent_spec_key(row: dict[str, object]) -> str:
    return cast(str, row["key"])


def roots_spans(roots: list[tuple[int, Span]]) -> list[Span]:
    return [span for _, span in sorted(roots, key=lambda item: item[0])]


# ---------------------------------------------------------------------------
# Pinned REST normalization and exact comparison
# ---------------------------------------------------------------------------


def _canonical_rest_time(value: object, what: str) -> str:
    text = _require_str(value, what)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise PhoenixTrialError(f"{what} is not an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise PhoenixTrialError(f"{what} must include a timezone")
    if parsed.microsecond % 1000:
        raise PhoenixTrialError(f"{what} has sub-millisecond precision")
    return canonical_time(parsed)


def _flatten_attributes(value: object, prefix: str = "") -> dict[str, AttributeValue]:
    if not isinstance(value, dict):
        raise PhoenixTrialError("span attributes must be an object")
    flattened: dict[str, AttributeValue] = {}
    for raw_key, raw_value in value.items():
        if not isinstance(raw_key, str) or not raw_key:
            raise PhoenixTrialError("attribute keys must be nonempty strings")
        key = f"{prefix}.{raw_key}" if prefix else raw_key
        if isinstance(raw_value, dict):
            if not raw_value:
                raise PhoenixTrialError(f"empty nested attribute: {key}")
            children = _flatten_attributes(raw_value, key)
            if set(flattened).intersection(children):
                raise PhoenixTrialError("duplicate flattened attribute path")
            flattened.update(children)
            continue
        if not isinstance(raw_value, (str, int, float, bool)):
            raise PhoenixTrialError(f"attribute {key} has an unsupported value")
        if isinstance(raw_value, float) and not math.isfinite(raw_value):
            raise PhoenixTrialError(f"attribute {key} is non-finite")
        if key in flattened:
            raise PhoenixTrialError(f"duplicate flattened attribute path: {key}")
        flattened[key] = raw_value
    return flattened


def _canonical_json_attribute(attributes: dict[str, AttributeValue], key: str) -> None:
    if key not in attributes:
        return
    text = attributes[key]
    if not isinstance(text, str):
        raise PhoenixTrialError(f"attribute {key} must contain JSON text")
    try:
        decoded = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonfinite_constant,
        )
    except PhoenixTrialError:
        raise
    except ValueError as exc:
        raise PhoenixTrialError(f"attribute {key} contains malformed JSON") from exc
    attributes[key] = json.dumps(decoded, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def canonical_span(value: object) -> Span:
    """Normalize one pinned Phoenix GET record to the expected REST span shape."""
    if not isinstance(value, dict):
        raise PhoenixTrialError("Phoenix span must be an object")
    if set(value) - (SPAN_KEYS | {"id"}) or not SPAN_KEYS <= set(value):
        raise PhoenixTrialError("Phoenix span has missing or unexpected fields")
    context = _require_exact_keys(value["context"], {"trace_id", "span_id"}, "span context")
    trace_id = _require_str(context["trace_id"], "span trace_id")
    span_id = _require_str(context["span_id"], "span span_id")
    if not HEX32_RE.fullmatch(trace_id) or not HEX16_RE.fullmatch(span_id):
        raise PhoenixTrialError("Phoenix span identity has an invalid format")
    span_kind = value["span_kind"]
    if span_kind not in ("LLM", "CHAIN"):
        raise PhoenixTrialError("Phoenix span_kind is invalid")
    attributes = _flatten_attributes(value["attributes"])
    redundant_kind = attributes.pop("openinference.span.kind", None)
    if redundant_kind is not None and redundant_kind != span_kind:
        raise PhoenixTrialError("redundant OpenInference kind does not match span_kind")
    for key in ("metadata", "laya.result"):
        _canonical_json_attribute(attributes, key)
    parent_id = value["parent_id"]
    if parent_id is not None and (not isinstance(parent_id, str) or not HEX16_RE.fullmatch(parent_id)):
        raise PhoenixTrialError("Phoenix parent_id has an invalid format")
    status = value["status_code"]
    if status not in ("OK", "ERROR"):
        raise PhoenixTrialError("Phoenix status_code is invalid")
    status_message = _require_str(value["status_message"], "span status_message")
    events = value["events"]
    if not isinstance(events, list):
        raise PhoenixTrialError("Phoenix events must be a list")
    return {
        "name": _require_str(value["name"], "span name"),
        "context": {"trace_id": trace_id, "span_id": span_id},
        "span_kind": span_kind,
        "parent_id": parent_id,
        "start_time": _canonical_rest_time(value["start_time"], "span start_time"),
        "end_time": _canonical_rest_time(value["end_time"], "span end_time"),
        "status_code": status,
        "status_message": status_message,
        "attributes": attributes,
        "events": events,
    }


def _span_run_id(span: Span) -> str | None:
    value = span["attributes"].get("metadata")
    if not isinstance(value, str):
        return None
    try:
        metadata = _loads_object(value)
    except PhoenixTrialError:
        return None
    run_id = metadata.get("run_id")
    return run_id if isinstance(run_id, str) else None


def compare_run(spans: list[object], *, expected: ExpectedRun) -> Summary:
    """Require exact selected identity/content equality before summarizing."""
    expected = validate_expected(expected)
    expected_by_id = {
        (span["context"]["trace_id"], span["context"]["span_id"]): span
        for span in expected["spans"]
    }
    selected: dict[tuple[str, str], Span] = {}
    for raw in spans:
        normalized = canonical_span(raw)
        identity = (normalized["context"]["trace_id"], normalized["context"]["span_id"])
        if identity in expected_by_id or _span_run_id(normalized) == expected["run_id"]:
            if identity in selected:
                raise PhoenixTrialError("duplicate selected Phoenix span identity")
            selected[identity] = normalized
    if set(selected) != set(expected_by_id):
        raise PhoenixTrialError("persisted Phoenix span identities do not match expected run")
    for identity, wanted in expected_by_id.items():
        if selected[identity] != wanted:
            raise PhoenixTrialError("persisted Phoenix span content differs from expected run")
    return summarize_spans(list(selected.values()), run_id=expected["run_id"])


class _TransientTransportError(PhoenixTrialError):
    """A transient read transport failure eligible for bounded GET retry."""


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise PhoenixTrialError("Phoenix API redirects are not allowed")


def _validate_base_url(base_url: str) -> str:
    try:
        parsed = urlsplit(base_url)
        port = parsed.port
    except ValueError as exc:
        raise PhoenixTrialError("Phoenix base URL has an invalid port") from exc
    if (
        parsed.scheme != "http"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path != ""
        or parsed.query
        or parsed.fragment
        or parsed.hostname not in ("phoenix", "127.0.0.1")
    ):
        raise PhoenixTrialError("Phoenix base URL must be plain HTTP to phoenix or loopback")
    if parsed.hostname == "phoenix" and port != 6006:
        raise PhoenixTrialError("Phoenix service URL must use port 6006")
    if parsed.hostname == "127.0.0.1" and (port is None or not 1 <= port <= 65535):
        raise PhoenixTrialError("loopback Phoenix URL requires a port in 1..65535")
    chosen_port = port or 6006
    return f"http://{parsed.hostname}:{chosen_port}"


def _open_json(opener, url: str, *, method: str, body: object | None, timeout: float):
    data = None if body is None else json.dumps(body, separators=(",", ":")).encode()
    request = Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"} if data is not None else {},
        method=method,
    )
    try:
        with opener.open(request, timeout=timeout) as response:
            raw = response.read()
            status = response.status
    except HTTPError as exc:
        raw = exc.read()
        status = exc.code
    except (URLError, TimeoutError, OSError) as exc:
        raise _TransientTransportError(
            f"Phoenix {method} transport failed: {type(exc).__name__}"
        ) from exc
    if not raw:
        payload: object = {}
    else:
        try:
            payload = json.loads(
                raw,
                object_pairs_hook=_reject_duplicate_keys,
                parse_constant=_reject_nonfinite_constant,
            )
        except (ValueError, UnicodeDecodeError) as exc:
            raise PhoenixTrialError("Phoenix returned malformed JSON") from exc
    if not isinstance(payload, dict):
        raise PhoenixTrialError("Phoenix API response must be an object")
    return status, payload


def _api_url(base_url: str) -> str:
    return f"{_validate_base_url(base_url)}/v1/projects/{PROJECT}/spans"


def _page_walk(opener, endpoint: str, deadline: float) -> list[object]:
    records: list[object] = []
    cursor: str | None = None
    seen_cursors: set[str] = set()
    for _ in range(20):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Phoenix verification deadline expired")
        query = {"limit": "1000", "sort": "id", "order": "asc"}
        if cursor is not None:
            query["cursor"] = cursor
        status, payload = _open_json(
            opener,
            f"{endpoint}?{urlencode(query)}",
            method="GET",
            body=None,
            timeout=min(5.0, remaining),
        )
        if status == 404:
            raise LookupError("Phoenix project not found yet")
        if status == 503:
            raise ConnectionError("Phoenix API is temporarily unavailable")
        if status != 200:
            raise PhoenixTrialError(f"Phoenix GET returned HTTP {status}")
        if set(payload) != {"data", "next_cursor"} or not isinstance(payload["data"], list):
            raise PhoenixTrialError("Phoenix GET response has an invalid page shape")
        records.extend(payload["data"])
        cursor = payload["next_cursor"]
        if cursor is None:
            return records
        if not isinstance(cursor, str) or not cursor or cursor in seen_cursors:
            raise PhoenixTrialError("Phoenix pagination cursor is invalid or repeated")
        seen_cursors.add(cursor)
    raise PhoenixTrialError("Phoenix pagination exceeded the 20-page limit")


def _verified_receipt(expected: ExpectedRun, summary: Summary) -> VerifiedReceipt:
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "verified",
        "project": PROJECT,
        "run_id": expected["run_id"],
        "summary": summary,
        "verified_span_ids": sorted(span["context"]["span_id"] for span in expected["spans"]),
    }


def _readback_is_incomplete(spans: list[object], expected: ExpectedRun) -> bool:
    expected_by_id = {
        (span["context"]["trace_id"], span["context"]["span_id"]): span
        for span in expected["spans"]
    }
    selected: dict[tuple[str, str], Span] = {}
    try:
        for raw in spans:
            normalized = canonical_span(raw)
            identity = (normalized["context"]["trace_id"], normalized["context"]["span_id"])
            if identity in expected_by_id or _span_run_id(normalized) == expected["run_id"]:
                if identity not in expected_by_id or identity in selected:
                    return False
                if normalized != expected_by_id[identity]:
                    return False
                selected[identity] = normalized
    except PhoenixTrialError:
        return False
    return len(selected) < len(expected_by_id)


def verify_run(
    base_url: str,
    *,
    run_id: str,
    expected: ExpectedRun,
    timeout_seconds: float = 60.0,
) -> VerifiedReceipt:
    """GET-only readback with exact semantic equality and a bounded deadline."""
    expected = validate_expected(expected)
    if _validate_run_id(run_id) != expected["run_id"]:
        raise PhoenixTrialError("requested run_id does not match the expected artifact")
    if isinstance(timeout_seconds, bool) or not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise PhoenixTrialError("timeout_seconds must be finite and positive")
    endpoint = _api_url(base_url)
    opener = build_opener(ProxyHandler({}), _RejectRedirects())
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            records = _page_walk(opener, endpoint, deadline)
            try:
                summary = compare_run(records, expected=expected)
            except PhoenixTrialError as exc:
                if not _readback_is_incomplete(records, expected):
                    raise
                last_error = exc
                summary = None
            if summary is not None:
                return _verified_receipt(expected, summary)
        except _TransientTransportError as exc:
            last_error = exc
        except (LookupError, ConnectionError, TimeoutError) as exc:
            last_error = exc
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(0.25, remaining))
    detail = f": {type(last_error).__name__}" if last_error else ""
    raise PhoenixTrialError(f"Phoenix readback deadline expired{detail}")


def seed_spans(
    base_url: str,
    expected: ExpectedRun,
    *,
    timeout_seconds: float = 60.0,
) -> VerifiedReceipt:
    """POST one synthetic run once, then verify it with GET-only polling."""
    expected = validate_expected(expected)
    if isinstance(timeout_seconds, bool) or not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise PhoenixTrialError("timeout_seconds must be finite and positive")
    endpoint = _api_url(base_url)
    opener = build_opener(ProxyHandler({}), _RejectRedirects())
    deadline = time.monotonic() + timeout_seconds
    remaining = deadline - time.monotonic()
    try:
        status, payload = _open_json(
            opener,
            endpoint,
            method="POST",
            body={"data": expected["spans"]},
            timeout=min(5.0, remaining),
        )
    except PhoenixTrialError as exc:
        # The write may have reached Phoenix. Never retry; retain host recovery state.
        cause = exc.__cause__ if exc.__cause__ is not None else exc
        raise PhoenixTrialError(
            f"Phoenix POST outcome is uncertain ({type(cause).__name__}); use read-only verify"
        ) from exc
    if status != 202:
        raise PhoenixTrialError(f"Phoenix POST returned HTTP {status}; no retry attempted")
    received = payload.get("total_received")
    queued = payload.get("total_queued")
    if (
        isinstance(received, bool)
        or not isinstance(received, int)
        or isinstance(queued, bool)
        or not isinstance(queued, int)
        or received != len(expected["spans"])
        or queued != len(expected["spans"])
    ):
        raise PhoenixTrialError("Phoenix POST acceptance counts do not match the expected span count")
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise PhoenixTrialError("Phoenix POST exhausted the verification deadline; use read-only verify")
    return verify_run(
        base_url,
        run_id=expected["run_id"],
        expected=expected,
        timeout_seconds=remaining,
    )


def volume_bytes(path: Path) -> int:
    """Return logical bytes in a directory, rejecting symlinks and non-files."""
    path = Path(path)
    _reject_symlink(path, "volume inspection root")
    if not path.is_dir():
        raise PhoenixTrialError("volume inspection path must be a directory")
    total = 0
    try:
        for entry in path.rglob("*"):
            if entry.is_symlink():
                raise PhoenixTrialError(f"volume inspection refuses symlink: {entry}")
            if entry.is_file():
                total += entry.stat(follow_symlinks=False).st_size
            elif not entry.is_dir():
                raise PhoenixTrialError(f"volume inspection found a non-regular entry: {entry}")
    except OSError as exc:
        raise PhoenixTrialError(f"volume inspection failed: {exc}") from exc
    return total


# ---------------------------------------------------------------------------
# Durable host artifacts
# ---------------------------------------------------------------------------


def _reject_symlink(path: Path, what: str) -> None:
    if path.is_symlink():
        raise PhoenixTrialError(f"{what} is a symlink: {path}")


def _fsync_dir(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    except OSError as exc:
        raise PhoenixTrialError(f"could not open directory for fsync: {path}") from exc
    try:
        os.fsync(fd)
    except OSError as exc:
        raise PhoenixTrialError(f"could not fsync directory: {path}") from exc
    finally:
        os.close(fd)


def _write_durable(path: Path, value: object) -> None:
    data = (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode(
        "utf-8"
    )
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError as exc:
        raise PhoenixTrialError(f"refusing to overwrite existing artifact: {path}") from exc
    except OSError as exc:
        raise PhoenixTrialError(f"could not create artifact {path}: {exc}") from exc
    try:
        view = memoryview(data)
        while view:
            written = os.write(fd, view)
            view = view[written:]
        os.fchmod(fd, 0o600)
        os.fsync(fd)
    except OSError as exc:
        raise PhoenixTrialError(f"could not durably write artifact {path}: {exc}") from exc
    finally:
        os.close(fd)


def write_prepared_run(state_dir: Path, expected: ExpectedRun) -> PreparedReceipt:
    """Durably allocate runs/<run_id>/ before any send.

    The immutable expected artifact and the prepared receipt are written
    with exclusive creation, 0700/0600 permissions, flush/fsync of the
    files and fsync of every new directory entry up through the state
    root. If durable state cannot be established, PhoenixTrialError is
    raised and the caller must issue zero POSTs.
    """
    expected = validate_expected(expected)
    state_dir = Path(state_dir)
    _reject_symlink(state_dir, "state directory")
    if not state_dir.is_dir():
        raise PhoenixTrialError(f"state directory does not exist: {state_dir}")
    runs_dir = state_dir / "runs"
    _reject_symlink(runs_dir, "runs directory")
    try:
        os.makedirs(runs_dir, mode=0o700, exist_ok=True)
        os.chmod(runs_dir, 0o700)
    except OSError as exc:
        raise PhoenixTrialError(f"could not create runs directory: {exc}") from exc
    run_dir = runs_dir / expected["run_id"]
    _reject_symlink(run_dir, "run directory")
    if run_dir.exists():
        raise PhoenixTrialError(f"run directory already exists: {run_dir}")
    try:
        os.mkdir(run_dir, 0o700)
        os.chmod(run_dir, 0o700)
    except OSError as exc:
        raise PhoenixTrialError(f"could not create run directory: {exc}") from exc
    expected_path = run_dir / "expected.json"
    _write_durable(expected_path, expected)
    receipt: PreparedReceipt = {
        "schema_version": SCHEMA_VERSION,
        "status": "prepared",
        "project": PROJECT,
        "run_id": expected["run_id"],
        "expected_path": str(expected_path),
    }
    _write_durable(run_dir / "prepared.json", receipt)
    _fsync_dir(run_dir)
    _fsync_dir(runs_dir)
    _fsync_dir(state_dir)
    return receipt


def mark_attempt(expected_path: Path) -> AttemptReceipt:
    """Durably write the exclusive attempt marker next to the artifact."""
    expected_path = Path(expected_path)
    _reject_symlink(expected_path, "expected artifact")
    if not expected_path.is_file():
        raise PhoenixTrialError(f"expected artifact does not exist: {expected_path}")
    try:
        text = expected_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PhoenixTrialError(f"could not read expected artifact: {exc}") from exc
    expected = validate_expected(_loads_object(text))
    if str(expected_path.parent / "expected.json") != str(expected_path):
        raise PhoenixTrialError("expected artifact must be named expected.json")
    attempt_path = expected_path.with_name("attempt.json")
    _reject_symlink(attempt_path, "attempt marker")
    if attempt_path.exists():
        raise PhoenixTrialError(
            f"attempt marker already exists for run {expected['run_id']}: refusing a second seed"
        )
    receipt: AttemptReceipt = {
        "schema_version": SCHEMA_VERSION,
        "run_id": expected["run_id"],
        "expected_path": str(expected_path),
        "post_may_have_started": True,
    }
    _write_durable(attempt_path, receipt)
    _fsync_dir(expected_path.parent)
    return receipt


def prepare_run(manifest: Manifest, state_dir: Path, *, run_id: str | None = None) -> PreparedReceipt:
    """Allocate one new run durably; local-only, no network effects."""
    run_id = _validate_run_id(run_id) if run_id is not None else uuid.uuid4().hex
    expected = make_expected(
        manifest, run_id=run_id, base_time=datetime.now(UTC)
    )
    return write_prepared_run(Path(state_dir), expected)
