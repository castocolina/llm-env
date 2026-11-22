"""Bounded Prometheus metrics from remote-setup request counters."""

from __future__ import annotations

import json
import math
import os
from datetime import datetime
from pathlib import Path
from typing import Any

MAX_BYTES = 64 * 1024
ROUTES = {"/setup.sh", "/config", "other", "healthcheck"}
CLASSES = {"2xx", "3xx", "4xx", "5xx", "error"}
DOWN = 'llm_env_source_up{source="remote_setup"} 0\n'


def _number(value: Any) -> bool:
    return (not isinstance(value, bool) and isinstance(value, (int, float))
            and math.isfinite(value) and value >= 0)


def collect_remote_setup(path: Path) -> str:
    """Emit counters only for a complete, bounded, valid counter file."""
    try:
        with Path(path).open("rb") as stream:
            if os.fstat(stream.fileno()).st_size > MAX_BYTES:
                return DOWN
            raw = stream.read(MAX_BYTES)
        doc = json.loads(raw)
        if not isinstance(doc, dict) or doc.get("v") != 1 or isinstance(doc.get("v"), bool):
            return DOWN
        started = datetime.fromisoformat(doc["started"])
        if started.tzinfo is None:
            return DOWN
        requests, latency = doc["requests"], doc["latency"]
        if not isinstance(requests, dict) or not isinstance(latency, dict):
            return DOWN
        request_rows = []
        for key, value in requests.items():
            if not isinstance(key, str) or "|" not in key:
                return DOWN
            route, request_class = key.rsplit("|", 1)
            if route not in ROUTES or request_class not in CLASSES or not _number(value):
                return DOWN
            request_rows.append((route, request_class, value))
        latency_rows = []
        for route, values in latency.items():
            if route not in ROUTES or not isinstance(values, dict):
                return DOWN
            count, total = values["count"], values["sum_ms"]
            if not _number(count) or not _number(total):
                return DOWN
            latency_rows.append((route, total, count))
        write_errors = doc["write_errors"]
        if not _number(write_errors):
            return DOWN
        lines = [
            "# HELP llm_env_remote_setup_requests_total Remote-setup requests by route and response class since process start.",
            "# TYPE llm_env_remote_setup_requests_total counter",
        ]
        lines.extend(f'llm_env_remote_setup_requests_total{{route="{route}",class="{kind}"}} {value}'
                     for route, kind, value in request_rows)
        lines += [
            "# HELP llm_env_remote_setup_request_duration_ms_sum Sum of remote-setup request durations in milliseconds.",
            "# TYPE llm_env_remote_setup_request_duration_ms_sum counter",
            "# HELP llm_env_remote_setup_request_duration_ms_count Count of remote-setup request durations.",
            "# TYPE llm_env_remote_setup_request_duration_ms_count counter",
        ]
        for route, total, count in latency_rows:
            lines += [f'llm_env_remote_setup_request_duration_ms_sum{{route="{route}"}} {total}',
                      f'llm_env_remote_setup_request_duration_ms_count{{route="{route}"}} {count}']
        lines += [
            "# HELP llm_env_remote_setup_counter_write_errors Counter-file write errors since process start.",
            "# TYPE llm_env_remote_setup_counter_write_errors gauge",
            f"llm_env_remote_setup_counter_write_errors {write_errors}",
            "# HELP llm_env_remote_setup_started_timestamp_seconds Process start time as Unix timestamp.",
            "# TYPE llm_env_remote_setup_started_timestamp_seconds gauge",
            f"llm_env_remote_setup_started_timestamp_seconds {started.timestamp()}",
            'llm_env_source_up{source="remote_setup"} 1',
        ]
        return "\n".join(lines) + "\n"
    except (OSError, ValueError, TypeError, KeyError, OverflowError, AttributeError):
        return DOWN
