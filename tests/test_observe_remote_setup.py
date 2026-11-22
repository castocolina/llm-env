import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pylib.observe_remote_setup import collect_remote_setup


def test_collector_fallback_lines_end_in_a_real_newline() -> None:
    # a literal backslash-n in a fallback line is only rescued by the validator: keep it out of the source
    source = (Path(__file__).resolve().parents[1] / "pylib" / "observe_metrics.py").read_text()
    assert "\\\\n" not in source


SAMPLE = {
    "v": 1,
    "started": "2026-11-22T03:35:32.927711+00:00",
    "requests": {"/setup.sh|2xx": 1, "other|4xx": 1},
    "latency": {"/setup.sh": {"count": 1, "sum_ms": 0.5076510133221745},
                "other": {"count": 1, "sum_ms": 0.24244398809969425}},
    "write_errors": 0,
}


def _write(tmp_path, content=SAMPLE):
    path = tmp_path / "request-counters.json"
    path.write_text(json.dumps(content))
    return path


def test_healthcheck_route_is_accepted_and_labelled(tmp_path):
    content = {**SAMPLE, "requests": {"healthcheck|2xx": 40, "/setup.sh|2xx": 1},
               "latency": {"healthcheck": {"count": 40, "sum_ms": 12.5}, "/setup.sh": {"count": 1, "sum_ms": 0.5}}}
    text = collect_remote_setup(_write(tmp_path, content))
    assert 'llm_env_remote_setup_requests_total{route="healthcheck",class="2xx"} 40' in text
    assert 'llm_env_source_up{source="remote_setup"} 1' in text


def test_valid_file_emits_counter_and_start_timestamp(tmp_path):
    text = collect_remote_setup(_write(tmp_path))
    assert 'llm_env_remote_setup_requests_total{route="/setup.sh",class="2xx"} 1' in text
    assert 'llm_env_remote_setup_request_duration_ms_sum{route="other"} 0.24244398809969425' in text
    assert 'llm_env_remote_setup_request_duration_ms_count{route="other"} 1' in text
    assert 'llm_env_remote_setup_counter_write_errors 0' in text
    assert 'llm_env_remote_setup_started_timestamp_seconds ' in text
    assert 'llm_env_source_up{source="remote_setup"} 1' in text


@pytest.mark.parametrize("content", ["{", {**SAMPLE, "v": 2},
    {**SAMPLE, "requests": {"/secret?key=x|2xx": 1}},
    {**SAMPLE, "requests": {"/setup.sh|9xx": 1}},
    {**SAMPLE, "requests": {"/setup.sh|2xx": -1}},
    {**SAMPLE, "requests": {"/setup.sh|2xx": float("nan")}},
])
def test_invalid_file_only_emits_source_down(tmp_path, content):
    path = tmp_path / "request-counters.json"
    path.write_text(content if isinstance(content, str) else json.dumps(content))
    assert collect_remote_setup(path) == 'llm_env_source_up{source="remote_setup"} 0\n'


def test_missing_file_is_unknown_not_zero(tmp_path):
    assert collect_remote_setup(tmp_path / "missing") == 'llm_env_source_up{source="remote_setup"} 0\n'


def test_oversize_file_is_rejected_without_reading_unbounded_data(tmp_path):
    path = tmp_path / "large"
    payload = json.dumps(SAMPLE).encode()
    path.write_bytes(payload + b" " * (64 * 1024 + 1 - len(payload)))
    assert collect_remote_setup(path) == 'llm_env_source_up{source="remote_setup"} 0\n'


def test_output_labels_are_only_allowlisted(tmp_path):
    text = collect_remote_setup(_write(tmp_path))
    assert 'route="/config"' not in text
    assert all(label in text for label in ('route="/setup.sh"', 'route="other"', 'class="2xx"', 'class="4xx"'))
    assert "2026-11-22T" not in text


def test_invalid_latency_values_reject_whole_file(tmp_path):
    content = json.loads(json.dumps(SAMPLE))
    content["latency"]["/setup.sh"]["sum_ms"] = -1
    assert collect_remote_setup(_write(tmp_path, content)) == 'llm_env_source_up{source="remote_setup"} 0\n'


def test_collect_metrics_isolates_raising_remote_collector(monkeypatch, tmp_path):
    import pylib.observe_metrics as om
    from tests.test_observe_metrics import _install_collector
    _install_collector(monkeypatch, states={})
    monkeypatch.setattr(om, "collect_remote_setup", lambda path: (_ for _ in ()).throw(RuntimeError()))
    text = om.collect_metrics({}, None, port=1, password="pw", remote_setup_path=tmp_path / "never")
    assert 'llm_env_source_up{source="remote_setup"} 0' in text
    assert 'llm_env_source_up{source="containers"}' in text


def test_collect_metrics_remote_timeout_falls_back(monkeypatch, tmp_path):
    import time

    import pylib.observe_metrics as om
    from tests.test_observe_metrics import _install_collector
    _install_collector(monkeypatch, states={})
    def slow(path):
        time.sleep(0.2)
        return 'llm_env_source_up{source="remote_setup"} 1\n'
    monkeypatch.setattr(om, "collect_remote_setup", slow)
    text = om.collect_metrics({}, None, port=1, password="pw", deadline_s=0.01,
                              remote_setup_path=tmp_path / "never")
    assert 'llm_env_source_up{source="remote_setup"} 0' in text
    assert 'llm_env_source_up{source="containers"}' in text
