"""Aggregate the native Grafana and VictoriaMetrics HTTP counters."""

from __future__ import annotations

import math
import re
import urllib.request
from collections import defaultdict
from collections.abc import Callable

_LIMIT = 2 * 1024 * 1024
_SAMPLE = re.compile(r'([a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{(.*)\})?\s+([^\s]+)(?:\s+[0-9]+)?\Z')
_LABEL = re.compile(r'\s*([a-zA-Z_][a-zA-Z0-9_]*)="((?:[^"\\]|\\[\\"n])*)"\s*(?:,|\Z)')
_USED = {
    "grafana_http_request_duration_seconds_count", "grafana_http_request_duration_seconds_sum",
    "vm_http_requests_total", "vm_http_request_errors_total",
}
_URLS = {
    "grafana": ("grafana_http", "http://127.0.0.1:3000/metrics"),
    "victoriametrics": ("victoriametrics_http", "http://127.0.0.1:8428/metrics"),
}


def _fetch(url: str, timeout: float, max_bytes: int) -> str:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        data = response.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError("response too large")
    return data.decode("utf-8")


def _parse(text: str) -> list[tuple[str, dict[str, str], float]]:
    samples = []
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        match = _SAMPLE.fullmatch(line)
        if not match:
            raise ValueError("invalid exposition")
        name, raw_labels, raw_value = match.groups()
        labels: dict[str, str] = {}
        if raw_labels is not None:
            pos = 0
            while pos < len(raw_labels):
                label = _LABEL.match(raw_labels, pos)
                if not label or label.group(1) in labels:
                    raise ValueError("invalid labels")
                labels[label.group(1)] = label.group(2)
                pos = label.end()
        try:
            value = float(raw_value)
        except ValueError as exc:
            raise ValueError("invalid number") from exc
        if not math.isfinite(value):
            # real Grafana output carries NaN quantiles (summaries) and +Inf buckets in families we never read: skip those,
            # but a non-finite value in a family we do read makes the whole payload untrustworthy.
            if name in _USED:
                raise ValueError("nonfinite number")
            continue
        samples.append((name, labels, value))
    return samples


def _class(value: str) -> str:
    if len(value) == 3 and value.isdigit():
        code = int(value)
        if 200 <= code < 300:
            return "2xx"
        if 300 <= code < 400:
            return "3xx"
        if 400 <= code < 500:
            return "4xx"
        if 500 <= code < 600:
            return "5xx"
    return "other"


def collect_native_http(fetch: Callable = _fetch) -> str:
    """Fetch each loopback metrics endpoint independently and emit only fixed labels."""
    out: list[str] = [
        "# HELP llm_env_native_http_requests_total Grafana requests by status class since its last restart.",
        "# TYPE llm_env_native_http_requests_total counter",
        "# HELP llm_env_native_http_requests_all_total VictoriaMetrics requests (all paths) since its last restart.",
        "# TYPE llm_env_native_http_requests_all_total counter",
        "# HELP llm_env_native_http_request_errors_total VictoriaMetrics request errors since its last restart.",
        "# TYPE llm_env_native_http_request_errors_total counter",
        "# HELP llm_env_native_http_request_duration_seconds_sum Grafana summed request handling time.",
        "# TYPE llm_env_native_http_request_duration_seconds_sum counter",
        "# HELP llm_env_native_http_request_duration_seconds_count Grafana timed requests.",
        "# TYPE llm_env_native_http_request_duration_seconds_count counter",
    ]
    for service, (source, url) in _URLS.items():
        try:
            text = fetch(url, timeout=3, max_bytes=_LIMIT)
            if not isinstance(text, str) or len(text.encode("utf-8")) > _LIMIT:
                raise ValueError("response too large")
            samples = _parse(text)
            grouped: dict[str, float] = defaultdict(float)
            durations: dict[str, float] = defaultdict(float)
            for name, labels, value in samples:
                if service == "grafana":
                    if name == "grafana_http_request_duration_seconds_count":
                        grouped[_class(labels.get("status_code", ""))] += value
                else:
                    if name == "vm_http_requests_total":
                        grouped["requests"] += value
                    elif name == "vm_http_request_errors_total":
                        grouped["errors"] += value
                if service == "grafana" and name == "grafana_http_request_duration_seconds_sum":
                    durations["sum"] += value
                if service == "grafana" and name == "grafana_http_request_duration_seconds_count":
                    durations["count"] += value
            # Grafana: classes come from the status_code label of its request-duration histogram count (every handled
            # request), whose sum/count give the latency. VictoriaMetrics has no status label, and its duration family
            # only times its /metrics endpoint (measured: count 7 against ~10k requests), so no latency is emitted for it.
            if service == "grafana":
                for cls, value in sorted(grouped.items()):
                    out.append(f'llm_env_native_http_requests_total{{service="grafana",class="{cls}"}} {value!r}')
                out.extend([
                    f'llm_env_native_http_request_duration_seconds_sum{{service="grafana"}} {durations["sum"]!r}',
                    f'llm_env_native_http_request_duration_seconds_count{{service="grafana"}} {durations["count"]!r}',
                ])
            else:
                out.extend([
                    f'llm_env_native_http_requests_all_total{{service="victoriametrics"}} {grouped["requests"]!r}',
                    f'llm_env_native_http_request_errors_total{{service="victoriametrics"}} {grouped["errors"]!r}',
                ])
            out.append(f'llm_env_source_up{{source="{source}"}} 1')
        except Exception:  # noqa: BLE001 -- independent sources; never expose endpoint response/errors
            out.append(f'llm_env_source_up{{source="{source}"}} 0')
    return "\n".join(out) + "\n"
