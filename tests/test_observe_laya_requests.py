from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pylib.observe_laya_requests import collect_laya_requests
from pylib.observe_metrics import valid_exposition_fragment


def record(span_id: int, **changes: object) -> dict:
    value = {
        "v": 1, "ts": (datetime.now(UTC) - timedelta(seconds=span_id)).isoformat().replace("+00:00", "Z"),
        "span_id": f"{span_id:016x}", "endpoint": "/v1/systemone", "batch": False,
        "http_status": 200, "duration_ms": 100, "requested_questions": 1,
        "answers": {"noul": 1, "choice": 0, "score": 0}, "confidences": [0.8],
        "error_type": None, "truncated": False,
    }
    value.update(changes)
    return value


def collect(path: Path, state: dict | None = None) -> str:
    return collect_laya_requests(path, state if state is not None else {}, now=datetime.now(UTC))


def test_real_rejected_request_with_null_counts_is_listed_not_malformed(tmp_path: Path) -> None:
    # shape of the span Laya wrote for a live 400 (rejected before parsing): null counts, empty confidences
    path = tmp_path / "spans.jsonl"
    path.write_text(json.dumps(record(1, http_status=400, error_type="http_4xx", requested_questions=None,
                                      completed_answers=None, model=None, confidences=[])) + "\n")
    text = collect(path)
    assert 'status="400"' in text and "llm_env_laya_requests_malformed_lines 0" in text
    assert "llm_env_laya_request_questions{" not in text  # unknown is not zero
    assert "llm_env_laya_request_confidence_mean{" not in text


def test_selects_each_notable_reason_and_omits_plain_success(tmp_path: Path) -> None:
    rows = [
        record(1, http_status=500), record(2, error_type="timeout"),
        record(3, truncated=True), record(4, endpoint="/v1/systemone/batch", batch=True),
        record(5, duration_ms=1001), record(6),
    ]
    path = tmp_path / "spans.jsonl"
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    text = collect(path)
    assert sum(line.startswith("llm_env_laya_request_duration_ms{") for line in text.splitlines()) == 5
    assert "0000000000000006" not in text
    assert valid_exposition_fragment(text)


def test_keeps_newest_100(tmp_path: Path) -> None:
    path = tmp_path / "spans.jsonl"
    start = datetime.now(UTC) - timedelta(seconds=105)
    rows = [record(i + 1, http_status=500,
                   ts=(start + timedelta(seconds=i)).isoformat().replace("+00:00", "Z"))
            for i in range(105)]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    text = collect(path)
    assert text.count("llm_env_laya_request_duration_ms{") == 100
    assert 'span_id="0000000000000069"' in text
    assert 'span_id="0000000000000001"' not in text


def test_defers_partial_line_and_processes_after_completion(tmp_path: Path) -> None:
    path = tmp_path / "spans.jsonl"
    raw = json.dumps(record(1, http_status=500))
    path.write_text(raw[:20])
    state: dict = {}
    assert "llm_env_laya_request_duration_ms{" not in collect(path, state)
    path.write_text(raw + "\n")
    assert "llm_env_laya_request_duration_ms{" in collect(path, state)


def test_rescans_after_truncation_and_counts_malformed(tmp_path: Path) -> None:
    path = tmp_path / "spans.jsonl"
    state: dict = {}
    path.write_text(json.dumps(record(1, http_status=500)) + "\n")
    collect(path, state)
    path.write_text("bad json\n" + json.dumps(record(2, http_status=500)) + "\n")
    text = collect(path, state)
    assert 'span_id="0000000000000002"' in text
    assert "llm_env_laya_requests_malformed_lines 1" in text
    assert "llm_env_laya_requests_malformed_lines 1" in collect(path, state)  # stable on rescan, not climbing
    path.write_text("x" * 9000 + "\n" + "bad json\n")
    for _ in range(3):  # an oversized line is one malformed line per scan, never a growing total
        assert "llm_env_laya_requests_malformed_lines 2" in collect(path, state)


def test_missing_file_is_source_down(tmp_path: Path) -> None:
    text = collect(tmp_path / "missing.jsonl")
    assert 'llm_env_source_up{source="laya_requests"} 0' in text


def test_privacy_and_allowed_labels(tmp_path: Path) -> None:
    path = tmp_path / "spans.jsonl"
    row = record(1, http_status=500, instructions="secret", request_sha256="secret",
                 choice="answer text")
    path.write_text(json.dumps(row) + "\n")
    text = collect(path)
    assert not any(secret in text for secret in ("instructions", "answer text", "request_sha256"))
    labels = {part.split("=", 1)[0] for line in text.splitlines() if "{" in line
              for part in line.split("{", 1)[1].split("}", 1)[0].split(",")}
    assert labels <= {"span_id", "endpoint", "status", "error_type", "batch", "ts", "type", "source"}


def test_empty_or_unreadable_source_isolated_by_metrics_worker(tmp_path: Path) -> None:
    from pylib import observe_metrics

    assert 'llm_env_source_up{source="laya_requests"} 0' in observe_metrics._laya_requests_worker(
        lambda: (_ for _ in ()).throw(OSError("no")))
