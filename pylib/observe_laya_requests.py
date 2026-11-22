"""Bounded notable-request metrics from Laya's privacy-filtered JSONL spans."""

from __future__ import annotations

import json
import math
import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

MAX_BYTES = 1024 * 1024
MAX_LINE = 8192
MAX_REQUESTS = 100


def _labels(values: dict[str, str]) -> str:
    return "{" + ",".join(f'{key}="{value.replace(chr(92), "").replace(chr(34), "")}"' for key, value in values.items()) + "}"


def collect_laya_requests(path: Path, state: dict[str, Any], *, now: datetime | None = None) -> str:
    """Emit only the newest 100 notable, complete requests from the prior 24 hours."""
    path = Path(path)
    malformed = 0  # a gauge over the scanned window: the same bad line is not re-counted as new on every run
    up = 0
    try:
        with path.open("rb") as stream:
            stat = os.fstat(stream.fileno())
            if stat.st_size > MAX_BYTES:
                stream.seek(stat.st_size - MAX_BYTES)
                stream.readline()
            raw_lines = stream.readlines(MAX_BYTES)
        up = 1
        if raw_lines and not raw_lines[-1].endswith(b"\n"):
            raw_lines.pop()
        requests = []
        now = now or datetime.now(UTC)
        threshold = now - timedelta(hours=24)
        for raw in raw_lines:
            if not raw.endswith(b"\n") or len(raw) > MAX_LINE:
                malformed += 1
                continue
            try:
                row = json.loads(raw)
                if not isinstance(row, dict):
                    raise TypeError
                stamp = datetime.fromisoformat(row["ts"])
                if stamp.tzinfo is None:
                    stamp = stamp.replace(tzinfo=UTC)
                status, duration = row["http_status"], row["duration_ms"]
                error_type = row.get("error_type")
                if error_type is not None and (not isinstance(error_type, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,40}", error_type) is None):
                    raise ValueError
                if (not isinstance(status, int) or isinstance(status, bool) or
                        not isinstance(duration, (int, float)) or isinstance(duration, bool) or
                        not math.isfinite(duration) or not isinstance(row.get("span_id"), str) or
                        re.fullmatch(r"[0-9a-f]{16}", row["span_id"]) is None or
                        row.get("endpoint") not in ("/v1/systemone", "/v1/systemone/batch") or
                        not isinstance(row.get("batch"), bool)):
                    raise ValueError
                answers, confidences = row.get("answers", {}), row.get("confidences", [])
                questions = row.get("requested_questions", 0)
                if (not isinstance(answers, dict) or any(k not in ("noul", "choice", "score") or
                        not isinstance(v, int) or isinstance(v, bool) or v < 0 for k, v in answers.items()) or
                        not isinstance(confidences, list) or any(not isinstance(v, (int, float)) or
                        isinstance(v, bool) or not math.isfinite(v) for v in confidences) or
                        # shortcut: Laya writes null for requests it rejected before parsing (a real 400 does); unknown is not zero
                        (questions is not None and (not isinstance(questions, int) or isinstance(questions, bool) or questions < 0))):
                    raise ValueError
                if stamp < threshold or stamp > now + timedelta(minutes=5):
                    continue
                notable = status >= 400 or row.get("error_type") is not None or row.get("truncated") is True or row["batch"] or duration > 1000
                if notable:
                    requests.append((stamp, row, duration, questions, answers, confidences))
            except (ValueError, TypeError, KeyError, OverflowError, AttributeError):
                malformed += 1
        requests.sort(key=lambda item: item[0], reverse=True)
        requests = requests[:MAX_REQUESTS]
    except OSError:
        requests = []

    output = ['# HELP llm_env_laya_requests_malformed_lines Malformed Laya span lines in the scanned window (last 1 MiB of the file).',
              '# TYPE llm_env_laya_requests_malformed_lines gauge',
              f'llm_env_laya_requests_malformed_lines {malformed}',
              '# HELP llm_env_laya_request_duration_ms Notable Laya request duration in milliseconds.',
              '# TYPE llm_env_laya_request_duration_ms gauge',
              '# HELP llm_env_laya_request_questions Requested questions in a notable Laya request.',
              '# TYPE llm_env_laya_request_questions gauge',
              '# HELP llm_env_laya_request_confidence_mean Mean self-reported confidence for a notable request.',
              '# TYPE llm_env_laya_request_confidence_mean gauge',
              '# HELP llm_env_laya_request_answers Answer count by type in a notable Laya request.',
              '# TYPE llm_env_laya_request_answers gauge']
    for stamp, row, duration, questions, answers, confidences in requests:
        labels = {"span_id": row["span_id"], "endpoint": row["endpoint"], "status": str(row["http_status"]),
                  "error_type": row.get("error_type") or "", "batch": str(row["batch"]).lower(),
                  "ts": stamp.astimezone(UTC).isoformat().replace("+00:00", "Z")}
        label_text = _labels(labels)
        output.append(f"llm_env_laya_request_duration_ms{label_text} {duration}")
        if questions is not None:
            output.append(f"llm_env_laya_request_questions{label_text} {questions}")
        if confidences:
            output.append(f"llm_env_laya_request_confidence_mean{label_text} {sum(confidences) / len(confidences)}")
        for kind in ("noul", "choice", "score"):
            output.append(f'llm_env_laya_request_answers{_labels({**labels, "type": kind})} {answers.get(kind, 0)}')
    output.append(f'llm_env_source_up{{source="laya_requests"}} {up}')
    return "\n".join(output) + "\n"
