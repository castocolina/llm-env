import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pylib.observe_native_http import collect_native_http

GRAFANA = '''# TYPE grafana_api_response_status_total counter
grafana_api_response_status_total{code="200"} 2
grafana_api_response_status_total{code="302"} 3
grafana_api_response_status_total{code="404"} 5
grafana_api_response_status_total{code="502"} 7
grafana_api_response_status_total{code="unknown"} 11
# TYPE grafana_http_request_duration_seconds histogram
grafana_http_request_duration_seconds_sum{handler="/secret",method="GET",status_code="200"} 1.5
grafana_http_request_duration_seconds_count{handler="/secret",method="GET",status_code="200"} 2
grafana_http_request_duration_seconds_count{handler="/secret",method="GET",status_code="302"} 3
grafana_http_request_duration_seconds_count{handler="/secret",method="GET",status_code="404"} 5
grafana_http_request_duration_seconds_count{handler="/secret",method="GET",status_code="502"} 7
grafana_http_request_duration_seconds_count{handler="/secret",method="GET",status_code="-1"} 11
'''
VM = '''vm_http_requests_total{path="/secret"} 12
vm_http_request_errors_total{path="/secret",reason="bad"} 3
vm_http_request_duration_seconds_sum{path="/secret"} 6
vm_http_request_duration_seconds_count{path="/secret"} 4
'''


def test_aggregates_both_services_without_source_labels():
    text = collect_native_http(lambda url, timeout, max_bytes: GRAFANA if ":3000" in url else VM)
    assert 'llm_env_native_http_requests_total{service="grafana",class="2xx"} 2' in text
    assert 'llm_env_native_http_requests_total{service="grafana",class="3xx"} 3' in text
    assert 'llm_env_native_http_requests_total{service="grafana",class="4xx"} 5' in text
    assert 'llm_env_native_http_requests_total{service="grafana",class="5xx"} 7' in text
    assert 'llm_env_native_http_requests_total{service="grafana",class="other"} 11' in text
    assert 'llm_env_native_http_requests_all_total{service="victoriametrics"} 12' in text
    assert 'llm_env_native_http_request_errors_total{service="victoriametrics"} 3' in text
    assert 'llm_env_native_http_request_duration_seconds_sum{service="grafana"} 1.5' in text
    assert 'llm_env_native_http_request_duration_seconds_count{service="grafana"} 28' in text
    assert 'handler=' not in text and 'path=' not in text and 'code=' not in text
    assert 'duration_seconds_sum{service="victoriametrics"}' not in text  # its duration metric only times /metrics


@pytest.mark.parametrize(
    ("body", "first", "second"),
    [
        pytest.param(
            'grafana_http_request_duration_seconds_sum{handler="/x",status_code="200"} 2015.4344\n'
            'grafana_http_request_duration_seconds_count{handler="/x",status_code="200"} 1234567\n',
            'request_duration_seconds_sum{service="grafana"} 2015.4344',
            'requests_total{service="grafana",class="2xx"} 1234567.0',  # not 1.23457e+06
            id="values_keep_full_precision",
        ),
        # real Grafana exposes `grafana_alerting_execution_time_milliseconds{quantile="0.5"} NaN` and +Inf histogram buckets
        pytest.param(
            'grafana_alerting_execution_time_milliseconds{quantile="0.5"} NaN\n'
            'some_hist_bucket{le="+Inf"} +Inf\n'
            'grafana_http_request_duration_seconds_count{handler="/x",status_code="200"} 4\n',
            'llm_env_source_up{source="grafana_http"} 1',
            'requests_total{service="grafana",class="2xx"} 4.0',
            id="nan_quantiles_in_unused_families_are_skipped_like_real_grafana_output",
        ),
    ],
)
def test_grafana_body_renders(body, first, second):
    text = collect_native_http(lambda url, timeout, max_bytes: body if ":3000" in url else VM)
    assert first in text
    assert second in text


def test_fetch_reads_at_most_the_cap_plus_one_byte(monkeypatch):
    from pylib import observe_native_http as native

    asked = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self, size=-1):
            asked.append(size)
            return b"x" * (size if size > 0 else 10**7)

    monkeypatch.setattr(native.urllib.request, "urlopen", lambda url, timeout: Response())
    with pytest.raises(ValueError):
        native._fetch("http://127.0.0.1:3000/metrics", 3, 1024)
    assert asked == [1025]  # a bounded read, never an unbounded one


def test_exposition_declares_each_family_once():
    text = collect_native_http(lambda url, timeout, max_bytes: GRAFANA if ":3000" in url else VM)
    types = [line.split()[2] for line in text.splitlines() if line.startswith("# TYPE")]
    assert len(types) == len(set(types)) == 5


def test_failure_is_independent_and_does_not_echo_response():
    text = collect_native_http(lambda url, timeout, max_bytes: (_ for _ in ()).throw(OSError("private"))
                               if ":3000" in url else VM)
    assert 'llm_env_source_up{source="grafana_http"} 0' in text
    assert 'llm_env_source_up{source="victoriametrics_http"} 1' in text
    assert "private" not in text


@pytest.mark.parametrize("body", ["garbage\n", "x 1\nextra\n",
                                  'grafana_http_request_duration_seconds_count{status_code="200"} NaN\n',
                                  'vm_http_requests_total{path="/x"} +Inf\n'])
def test_invalid_exposition_marks_only_source_down(body):
    text = collect_native_http(lambda url, timeout, max_bytes: body if ":3000" in url else VM)
    assert 'llm_env_source_up{source="grafana_http"} 0' in text
    assert "garbage" not in text and "NaN" not in text


@pytest.mark.parametrize(("code", "expected"), [("200", "2xx"), ("302", "3xx"), ("404", "4xx"), ("502", "5xx"), ("-1", "other")])
def test_status_class_mapping(code, expected):
    body = f'grafana_http_request_duration_seconds_count{{handler="/x",status_code="{code}"}} 1\n'
    text = collect_native_http(lambda url, timeout, max_bytes: body if ":3000" in url else VM)
    assert f'llm_env_native_http_requests_total{{service="grafana",class="{expected}"}} 1' in text


def test_status_class_mapping_and_oversized_response():
    huge = GRAFANA + "x 1\n" * (2 * 1024 * 1024)
    text = collect_native_http(lambda url, timeout, max_bytes: huge if ":3000" in url else VM)
    assert 'llm_env_source_up{source="grafana_http"} 0' in text
    assert 'llm_env_source_up{source="victoriametrics_http"} 1' in text


def test_native_real_metric_fixtures_keep_valid_services():
    root = Path(__file__).resolve().parent / "fixtures"
    grafana = (root / "native_http" / "grafana.prom").read_text()
    vm = (root / "native_http" / "victoriametrics.prom").read_text()
    text = collect_native_http(lambda url, timeout, max_bytes: grafana if ":3000" in url else vm)
    assert 'llm_env_source_up{source="grafana_http"} 1' in text
    assert 'llm_env_source_up{source="victoriametrics_http"} 1' in text
    assert 'llm_env_native_http_requests_all_total{service="victoriametrics"}' in text


def test_parse_error_in_peer_does_not_remove_valid_service():
    text = collect_native_http(lambda url, timeout, max_bytes: "invalid" if ":8428" in url else GRAFANA)
    assert 'llm_env_source_up{source="grafana_http"} 1' in text
    assert 'llm_env_source_up{source="victoriametrics_http"} 0' in text
