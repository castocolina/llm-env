import json
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

FIXTURES = Path(__file__).parent / "fixtures"
KEY = "SENTINEL-LLAMA-API-KEY"


class Router:
    def __init__(self, states, *, status=200, trickle: float = 0.0, payload=None):
        self.states, self.status, self.requests = states, status, []
        self.trickle, self.payload = float(trickle), payload
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                owner.requests.append((self.path, self.headers.get("Authorization")))
                if owner.payload is not None and self.path == "/models":
                    body = owner.payload
                elif self.path == "/models":
                    body = json.dumps({"object": "list", "data": [
                        {"id": alias, "status": {"value": state}, "args": KEY * 200, "preset": KEY * 200}
                        for alias, state in owner.states.items()
                    ]}).encode()
                elif self.path.startswith("/metrics"):
                    body = (FIXTURES / "llama-metrics-4.txt").read_bytes()
                elif self.path.startswith("/slots"):
                    body = b'[{"id":0,"n_ctx":4096,"is_processing":true}]'
                else:
                    body = b"garbage"
                self.send_response(owner.status if self.path == "/models" else 200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if owner.trickle:
                    for byte in body:
                        self.wfile.write(bytes([byte]))
                        self.wfile.flush()
                        time.sleep(owner.trickle)
                else:
                    self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


def test_loaded_only_is_scraped_once_with_bearer_and_status():
    from pylib.observe_llama import collect_llama

    router = Router({"qwen3-5-4b": "loaded", "sleepy": "sleeping", "busy": "loading", "off": "unloaded"})
    try:
        text = collect_llama(base_url=router.url, api_key=KEY, deadline_s=2)
    finally:
        router.close()
    assert 'llm_env_source_up{source="llama"} 1' in text
    assert 'llm_env_llama_model_status{model="qwen3-5-4b",status="loaded"} 1' in text
    assert 'llm_env_llama_model_status{model="sleepy",status="sleeping"} 1' in text
    assert 'llm_env_llama_model_status{model="busy",status="loading"} 1' in text
    assert 'llm_env_llama_model_status{model="off",status="unloaded"} 1' in text
    assert 'llm_env_llama_model_state{model="busy"} 1' in text
    assert 'llm_env_llama_model_state{model="sleepy"} 2' in text
    assert 'llm_env_llama_model_state{model="qwen3-5-4b"} 3' in text
    assert 'llm_env_llama_model_state{model="off"} 0' in text
    assert 'llm_env_llama_model_state{model="unknown"} 4' not in text
    assert [p.split("?")[0] for p, _ in router.requests].count("/metrics") == 1
    assert [p.split("?")[0] for p, _ in router.requests].count("/slots") == 1
    assert all(auth == f"Bearer {KEY}" for _, auth in router.requests)
    assert f"{KEY}" not in text
    assert 'args' not in text and 'preset' not in text
    assert 'llm_env_llama_slot_context_tokens{model="qwen3-5-4b"} 4096' in text
    assert 'llm_env_llama_slot_processing{model="qwen3-5-4b"} 1' in text


@pytest.mark.parametrize("state", ["unloaded", "sleeping", "loading", "failed", "unknown"])
def test_non_loaded_states_never_hit_model_endpoints(state):
    from pylib.observe_llama import collect_llama

    router = Router({"model-a": state})
    try:
        text = collect_llama(base_url=router.url, api_key=KEY, deadline_s=2)
    finally:
        router.close()
    assert 'llm_env_source_up{source="llama"} 1' in text
    assert 'llm_env_llama_model_status{model="model-a"' in text
    assert not any(urlparse(path).path in ("/metrics", "/slots", "/props") for path, _ in router.requests)
    assert "llm_env_llama_prompt_tokens_total{" not in text


def test_outage_or_bad_models_response_has_no_value_metrics():
    from pylib.observe_llama import collect_llama

    for states, status in [({"model": "loaded"}, 401), ({"model": "loaded"}, 500), ({}, 200)]:
        router = Router(states, status=status)
        try:
            text = collect_llama(base_url=router.url, api_key=KEY, deadline_s=2)
        finally:
            router.close()
        assert 'llm_env_source_up{source="llama"} 0' in text
        assert "llm_env_llama_prompt_tokens_total" not in text
        assert "llm_env_llama_models_loaded" not in text
        assert KEY not in text


def test_real_families_preserve_counter_types_and_reject_bad_samples():
    from pylib.observe_llama import _parse_metrics

    source = (FIXTURES / "llama-metrics-4.txt").read_text()
    text, unknown, dropped = _parse_metrics("qwen3-5-4b", source +
        "# TYPE llamacpp:future_thing gauge\nllamacpp:future_thing 1\n"
        "llamacpp:requests_processing{evil=\"x\"} 2\n"
        "llamacpp:tokens_predicted_total NaN\nllamacpp:tokens_predicted_total Inf\n")
    assert "# TYPE llm_env_llama_prompt_tokens_total counter" in text
    assert "# TYPE llm_env_llama_n_tokens_max gauge" in text
    assert unknown == 1 and dropped == 5
    clean, _, _ = _parse_metrics("qwen3-5-4b", source)
    for source_family in ("prompt_tokens_total", "prompt_tokens_cached_total", "prompt_seconds_total",
                          "tokens_predicted_total", "tokens_predicted_seconds_total", "n_decode_total",
                          "n_tokens_max", "spec_decode_num_draft_tokens_total",
                          "spec_decode_num_accepted_tokens_total", "spec_decode_num_drafts_total",
                          "prompt_tokens_seconds", "predicted_tokens_seconds", "requests_processing",
                          "requests_deferred", "n_busy_slots_per_decode"):
        exported, _ = __import__("pylib.observe_llama", fromlist=["FAMILIES"]).FAMILIES[source_family]
        kind = "gauge" if source_family == "n_tokens_max" or source_family.endswith("seconds") and source_family in ("prompt_tokens_seconds", "predicted_tokens_seconds") or source_family in ("requests_processing", "requests_deferred", "n_busy_slots_per_decode") else "counter"
        assert f"# TYPE llm_env_llama_{exported} {kind}" in clean
    assert 'model="qwen3-5-4b"' in text
    labelled_only, _, labelled_dropped = _parse_metrics(
        "model-a", '# TYPE llamacpp:requests_processing gauge\n'
        'llamacpp:requests_processing{evil="x"} 2\n')
    assert "llm_env_llama_requests_processing{" not in labelled_only
    assert labelled_dropped == 1


def test_shared_family_keeps_type_when_multiple_loaded_aliases_emit_help():
    from pylib.observe_llama import collect_llama

    router = Router({"model-a": "loaded", "model-b": "loaded"})
    try:
        text = collect_llama(base_url=router.url, api_key=KEY, deadline_s=2)
    finally:
        router.close()
    assert text.count("# TYPE llm_env_llama_prompt_tokens_total counter") == 1
    assert text.count("llm_env_llama_prompt_tokens_total{model=") == 2


def test_real_content_length_models_and_metrics_are_accepted():
    from pylib.observe_llama import collect_llama

    models = (FIXTURES / "llama-models-loaded.json").read_bytes()
    router = Router({"qwen3-5-4b": "loaded"}, payload=models)
    try:
        text = collect_llama(base_url=router.url, api_key=KEY, deadline_s=2)
    finally:
        router.close()
    assert 'llm_env_source_up{source="llama"} 1' in text
    assert "llm_env_llama_prompt_tokens_total{model=\"qwen3-5-4b\"} 26" in text


def test_trickling_content_length_body_obeys_total_deadline():
    from pylib.observe_llama import collect_llama

    router = Router({}, trickle=.03, payload=b" " * 300)
    started = time.monotonic()
    try:
        text = collect_llama(base_url=router.url, api_key=KEY, deadline_s=.2)
    finally:
        router.close()
    assert time.monotonic() - started < .7
    assert text == 'llm_env_source_up{source="llama"} 0\n'


def test_duplicate_model_ids_first_status_wins_and_counted():
    from pylib.observe_llama import collect_llama

    router = Router({"duplicate": "loaded"})
    try:
        text = collect_llama(base_url=router.url, api_key=KEY, deadline_s=2)
    finally:
        router.close()
    assert text.count('model="duplicate",status=') == 1


def test_parser_drops_duplicate_samples_conflicting_types_and_strict_numbers():
    from pylib.observe_llama import _parse_metrics

    source = ("# TYPE llamacpp:requests_processing gauge\n"
             "llamacpp:requests_processing 1\nllamacpp:requests_processing 2\n"
             "# TYPE llamacpp:prompt_tokens_total counter\n"
             "# TYPE llamacpp:prompt_tokens_total gauge\n"
             "llamacpp:prompt_tokens_total 1\n"
             "# TYPE llamacpp:tokens_predicted_total counter\n"
             "llamacpp:tokens_predicted_total 1_000\n")
    text, _, dropped = _parse_metrics("model-a", source)
    assert "requests_processing{" not in text
    assert "prompt_tokens_total{" not in text
    assert "1_000" not in text
    assert dropped >= 4


def test_invalid_aliases_dropped_and_counted():
    from pylib.observe_llama import _valid_alias

    assert _valid_alias("Qwen-3_5.4B")
    assert not _valid_alias("../model")
    assert not _valid_alias("x" * 41)


def test_unreachable_router_is_bounded_and_key_is_not_disclosed():
    import time

    from pylib.observe_llama import collect_llama

    start = time.monotonic()
    text = collect_llama(base_url="http://127.0.0.1:1", api_key=KEY, deadline_s=.2)
    assert time.monotonic() - start < .8
    assert text == 'llm_env_source_up{source="llama"} 0\n'
    assert KEY not in text


def test_llama_dashboard_queries_only_emitted_families():
    dashboard = json.loads((Path(__file__).parents[1] / "docker/observe/dashboards/llama.json").read_text())
    from pylib.observe_llama import EMITTED_FAMILIES

    titles = [p["title"] for p in dashboard["panels"]]
    assert len(titles) == len(set(titles))
    assert all(p.get("description") for p in dashboard["panels"])
    assert all(p.get("datasource", {"uid": "llm-env-metrics"}).get("uid") == "llm-env-metrics"
               for p in dashboard["panels"] if p.get("targets"))
    exprs = [t["expr"] for p in dashboard["panels"] for t in p.get("targets", [])]
    used = set(re.findall(r"\bllm_env_[a-zA-Z0-9_]+", " ".join(exprs)))
    assert used <= EMITTED_FAMILIES
    assert not any("kv_cache" in title.lower() for title in titles)
    assert len([panel["id"] for panel in dashboard["panels"]]) == len({panel["id"] for panel in dashboard["panels"]})
    assert any("KV-cache" in p.get("options", {}).get("content", "") for p in dashboard["panels"])
    assert all('llm_env_source_up{source="llama"}' in e for e in exprs if "llm_env_llama_" in e)
    status_panel = dashboard["panels"][3]
    mappings = status_panel["fieldConfig"]["defaults"]["mappings"]
    colors = {key: value for mapping in mappings if mapping["type"] == "value"
              for key, value in mapping["options"].items()}
    assert {key: (value["text"], value["color"]) for key, value in colors.items()} == {
        "0": ("unloaded", "gray"), "1": ("loading", "blue"),
        "2": ("sleeping", "yellow"), "3": ("loaded", "green"), "4": ("other", "red")}
    assert "llm_env_llama_model_state" in status_panel["targets"][0]["expr"]
    assert "llm_env_llama_model_state" in EMITTED_FAMILIES
