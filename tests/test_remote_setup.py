import http.client
import http.server
import json
import os
import pty
import select
import shutil
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pylib.remote_setup as remote_setup_module
from pylib.omniroute import OmniRouteError
from pylib.remote_setup import (
    RemoteSetupHandler,
    _build_unified_models,
    build_config_response,
    host_without_port,
    master_key_matches,
    parse_bearer_token,
    read_cached_key,
    render_setup_script,
    validate_host_header,
    write_cached_key,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def _use_real_opencode_updater(monkeypatch):
    """render_setup_script() reads UPDATE_OPENCODE_CONFIG_PATH from disk to
    embed it into the generated script. In the running container that path
    is /app/setup/update-opencode-config.mjs (see pylib/compose.py's
    remote-setup volume mount); on the test host it's this repo's real
    setup/update-opencode-config.mjs -- point the module at it so tests
    exercise the real embed rather than a container path that doesn't
    exist outside the container.
    """
    monkeypatch.setattr(
        remote_setup_module,
        "UPDATE_OPENCODE_CONFIG_PATH",
        REPO_ROOT / "setup" / "update-opencode-config.mjs",
    )


def test_parse_bearer_token_extracts_the_token():
    assert parse_bearer_token("Bearer abc123") == "abc123"


def test_parse_bearer_token_returns_none_for_missing_header():
    assert parse_bearer_token(None) is None


def test_parse_bearer_token_returns_none_for_non_bearer_scheme():
    assert parse_bearer_token("Basic abc123") is None


def test_parse_bearer_token_returns_none_for_empty_token():
    assert parse_bearer_token("Bearer ") is None


def test_master_key_matches_true_for_equal_strings():
    assert master_key_matches("secret", "secret") is True


def test_master_key_matches_false_for_different_strings():
    assert master_key_matches("wrong", "secret") is False


def test_read_cached_key_returns_none_when_file_missing(tmp_path):
    assert read_cached_key(tmp_path / "missing.json") is None


def test_read_cached_key_returns_none_for_malformed_json(tmp_path):
    cache = tmp_path / "cache.json"
    cache.write_text("not json")
    assert read_cached_key(cache) is None


def test_read_cached_key_returns_none_when_fields_missing(tmp_path):
    cache = tmp_path / "cache.json"
    cache.write_text(json.dumps({"id": "abc"}))
    assert read_cached_key(cache) is None


def test_write_then_read_cached_key_round_trips(tmp_path):
    cache = tmp_path / "nested" / "cache.json"
    write_cached_key(cache, "key-id", "sk-value")
    assert read_cached_key(cache) == {"id": "key-id", "key": "sk-value"}


def test_host_without_port_strips_a_trailing_port():
    assert host_without_port("192.0.2.1:20130") == "192.0.2.1"


def test_host_without_port_leaves_a_bare_host_unchanged():
    assert host_without_port("192.0.2.1") == "192.0.2.1"


def test_host_without_port_handles_a_bracketed_ipv6_literal_with_port():
    assert host_without_port("[::1]:20130") == "[::1]"


def test_host_without_port_handles_a_bracketed_ipv6_literal_without_port():
    assert host_without_port("[::1]") == "[::1]"


def test_validate_host_header_accepts_a_hostname_with_port():
    assert validate_host_header("192.0.2.1:20130") is True


def test_validate_host_header_accepts_a_bracketed_ipv6_literal():
    assert validate_host_header("[::1]:20130") is True


def test_validate_host_header_accepts_an_mdns_name():
    assert validate_host_header("my-host.local:20130") is True


def test_validate_host_header_rejects_an_empty_header():
    assert validate_host_header("") is False


def test_validate_host_header_rejects_shell_metacharacters():
    assert validate_host_header("evil`touch pwned`:20130") is False
    assert validate_host_header("evil$(touch pwned):20130") is False
    assert validate_host_header("evil; rm -rf /:20130") is False


def test_validate_host_header_rejects_a_bare_unbracketed_ipv6_authority():
    """"::1:20130" passes a character-class filter but is not an authority:
    host_without_port() used to reduce it to "" and /config would then hand
    back "http://:20128"."""
    assert validate_host_header("::1:20130") is False
    assert validate_host_header("fe80::1") is False
    assert host_without_port("::1:20130") == ""


def test_validate_host_header_rejects_trailing_garbage_after_the_bracket():
    assert validate_host_header("[::1]junk:20130") is False
    assert validate_host_header("[::1]:20130junk") is False
    assert validate_host_header("[::1") is False


def test_validate_host_header_rejects_a_non_numeric_or_out_of_range_port():
    assert validate_host_header("example.com:abc") is False
    assert validate_host_header("example.com:0") is False
    assert validate_host_header("example.com:65536") is False
    assert validate_host_header("example.com:") is False
    assert validate_host_header("example.com:65535") is True


def test_validate_host_header_rejects_an_empty_host_with_a_port():
    assert validate_host_header(":20130") is False


def test_host_without_port_round_trips_a_validated_bracketed_ipv6_literal():
    for header in ("[::1]:20130", "[::1]", "[2001:db8::1]:20128"):
        assert validate_host_header(header) is True
    assert host_without_port("[::1]:20130") == "[::1]"
    assert host_without_port("[::1]") == "[::1]"
    assert host_without_port("[2001:db8::1]:20128") == "[2001:db8::1]"


def test_build_unified_models_prefixes_llm_server_models():
    result = _build_unified_models(
        [{"alias": "a", "ctx_size": 8192, "client_max_output_tokens": 4096}], []
    )
    assert result == [
        {"id": "llama-cpp/a", "label": "a", "ctx_size": 8192, "client_max_output_tokens": 4096}
    ]


def test_build_unified_models_includes_combos_with_known_context():
    result = _build_unified_models(
        [],
        [{"combo": "my-coding", "min_context_window": 200000, "min_max_output_tokens": 128000}],
    )
    assert result == [
        {
            "id": "my-coding",
            "label": "my-coding (combo)",
            "ctx_size": 200000,
            "client_max_output_tokens": 128000,
        }
    ]


def test_build_unified_models_falls_back_to_capped_output_when_unknown():
    result = _build_unified_models(
        [],
        [{"combo": "my-planning", "min_context_window": 500000, "min_max_output_tokens": None}],
    )
    assert result[0]["client_max_output_tokens"] == 128000


def test_build_unified_models_clamps_output_above_context():
    result = _build_unified_models(
        [],
        [{"combo": "weird", "min_context_window": 50000, "min_max_output_tokens": 999999}],
    )
    assert result[0]["client_max_output_tokens"] == 50000


def test_build_unified_models_skips_combos_with_unknown_context():
    result = _build_unified_models(
        [], [{"combo": "mystery", "min_context_window": None, "min_max_output_tokens": None}]
    )
    assert result == []


def test_build_config_response_shape():
    response = build_config_response(
        host="192.0.2.1",
        omniroute_port="20128",
        api_key="sk-test",
        omniroute_dashboard_password="dashboard-pw",
        models=[{"alias": "a", "ctx_size": 8192, "client_max_output_tokens": 4096}],
    )
    assert response == {
        "omniroute_base_url": "http://192.0.2.1:20128",
        "api_key": "sk-test",
        "omniroute_dashboard_password": "dashboard-pw",
        "models": [{"alias": "a", "ctx_size": 8192, "client_max_output_tokens": 4096}],
    }


def test_render_setup_script_embeds_the_host(monkeypatch):
    _use_real_opencode_updater(monkeypatch)
    script = render_setup_script("192.0.2.1:20130")
    assert 'LLM_ENV_HOST="192.0.2.1:20130"' in script
    assert script.startswith("#!/usr/bin/env bash")


def test_render_setup_script_prompts_for_the_master_key_from_the_tty(monkeypatch):
    _use_real_opencode_updater(monkeypatch)
    script = render_setup_script("192.0.2.1:20130")
    assert "OMNI_ROUTER_MASTER_KEY:" in script
    assert "read -r -s -p" in script
    # Under `curl ... | bash`, stdin is the script pipe, not the terminal --
    # reading from /dev/tty explicitly is required for the prompt to
    # actually reach the user instead of silently reading EOF.
    assert "/dev/tty" in script


def test_render_setup_script_does_not_use_curl_dash_f(monkeypatch):
    # -f (--fail) swallows the response body on a non-2xx status, which is
    # exactly the body the script needs to read `.error` from. Match only
    # curl invocations -- the template's own `rm -f --` / `mv -f --` lines
    # (unrelated uses of -f) legitimately contain " -f " as a substring,
    # so a blanket substring check would false-positive on those.
    _use_real_opencode_updater(monkeypatch)
    script = render_setup_script("192.0.2.1:20130")
    for line in script.splitlines():
        if line.strip().startswith("curl"):
            assert " -f" not in line, line
    assert "curl -fsS" not in script


def test_render_setup_script_captures_the_http_status_and_checks_it(monkeypatch):
    _use_real_opencode_updater(monkeypatch)
    script = render_setup_script("192.0.2.1:20130")
    assert "%{http_code}" in script
    assert 'if [ "$http_status" != "200" ]' in script


def test_render_setup_script_never_passes_the_master_key_as_a_curl_argument(monkeypatch):
    # A bearer token passed via `curl -H "Authorization: Bearer $x"` is
    # visible to every other local user on the remote machine through
    # `ps`/`/proc/<pid>/cmdline` for as long as curl runs -- the script
    # must instead use a private, mode-0600 curl config file passed via
    # -K, mirroring scripts/check-server.sh's own auth_conf pattern.
    _use_real_opencode_updater(monkeypatch)
    script = render_setup_script("192.0.2.1:20130")
    assert '-H "Authorization: Bearer' not in script
    assert 'header = "Authorization: Bearer %s"' in script
    assert "-K \"$auth_conf\"" in script
    assert 'chmod 600 "$auth_conf"' in script


def test_render_setup_script_sends_omniroute_routed_model_ids(monkeypatch):
    # $models_file already carries the final client-visible "id" per entry
    # ("llama-cpp/<alias>" for llm-server models, the bare combo name for
    # OmniRoute combos) -- built server-side by pylib.remote_setup before
    # /config ever returns it, so the bash template must consume .id
    # verbatim rather than re-prefixing "llama-cpp/" itself.
    _use_real_opencode_updater(monkeypatch)
    script = render_setup_script("192.0.2.1:20130")
    assert "id: .id" in script
    assert ".[$model.id]" in script


def test_render_setup_script_embeds_the_opencode_updater_via_heredoc(monkeypatch):
    _use_real_opencode_updater(monkeypatch)
    script = render_setup_script("192.0.2.1:20130")
    assert "<<'OPENCODE_UPDATER_EOF'" in script
    assert "OPENCODE_UPDATER_EOF" in script
    # A real function from setup/update-opencode-config.mjs, proving the
    # file's actual content was embedded rather than a second HTTP fetch.
    assert "function replaceProvider(text, provider)" in script
    assert "http://${LLM_ENV_HOST}/update-opencode-config.mjs" not in script


def test_render_setup_script_uses_staged_files_with_restrictive_permissions(monkeypatch):
    _use_real_opencode_updater(monkeypatch)
    script = render_setup_script("192.0.2.1:20130")
    assert "chmod 700 \"$workdir\"" in script
    assert "chmod 600" in script
    assert "trap cleanup EXIT" in script


def test_render_setup_script_detects_an_existing_opencode_candidate_file(monkeypatch):
    # Mirrors setup-local-llm-agents.sh's own opencode_candidates detection
    # (setup/setup-local-llm-agents.sh:149-231): every candidate already
    # containing the "local-llm-env" provider is targeted; only when none
    # do does it fall back to the highest-priority *existing* file
    # (reverse order: opencode.jsonc, then opencode.json, then
    # config.json), and only when none exist at all does it default to
    # creating opencode.jsonc fresh.
    _use_real_opencode_updater(monkeypatch)
    script = render_setup_script("192.0.2.1:20130")
    assert (
        'opencode_candidates=(\n'
        '    "${opencode_dir}/config.json"\n'
        '    "${opencode_dir}/opencode.json"\n'
        '    "${opencode_dir}/opencode.jsonc"\n'
        ')'
    ) in script
    assert 'node "$updater" --contains-provider "$candidate"' in script
    assert 'for candidate in "${opencode_candidates[2]}" "${opencode_candidates[1]}" "${opencode_candidates[0]}"; do' in script
    assert 'opencode_targets+=("${opencode_candidates[2]}")' in script


def test_render_setup_script_validates_opencode_candidates_are_regular_files_and_checks_status(
    monkeypatch,
):
    # Full fidelity to setup/setup-local-llm-agents.sh:193-211: a
    # candidate that exists but isn't a regular file must die loud (not
    # be silently skipped), and the updater's exit status must be
    # discriminated 0 (contains)/1 (does not contain)/anything else
    # (real validation error) rather than treating every non-zero exit as
    # "does not contain".
    _use_real_opencode_updater(monkeypatch)
    script = render_setup_script("192.0.2.1:20130")
    assert '[ -f "$candidate" ] ||' in script
    assert 'contains_status=0' in script
    assert 'contains_status=$?' in script
    assert 'case "$contains_status" in' in script


def test_render_setup_script_stages_pi_and_opencode_before_moving_either_into_place(monkeypatch):
    # A failure partway through building the targets (Pi's models.json,
    # Pi's settings.json, every detected/created OpenCode config) must
    # never leave some of them updated and others untouched -- so every
    # staging step (the jq/node calls that write into the *_staged temp
    # files) must appear in the script BEFORE the first `mv -f --` that
    # moves any of them into place.
    _use_real_opencode_updater(monkeypatch)
    script = render_setup_script("192.0.2.1:20130")
    last_staging_step = script.index(
        'node "$updater" --replace-provider "$opencode_source" "$provider_file" "$staged"'
    )
    first_move = script.index('mv -f -- "$pi_staged" "$pi_path"')
    assert last_staging_step < first_move
    assert script.index('mv -f -- "$pi_settings_staged" "$pi_settings_path"') > first_move
    assert script.index(
        'mv -f -- "${opencode_staged[$index]}" "${opencode_targets[$index]}"'
    ) > first_move


class _FakeOmniRouteHandler(http.server.BaseHTTPRequestHandler):
    """Stands in for OmniRoute's admin API during the integration test."""

    keys_created = 0
    last_key_name = None

    def _reply(self, payload, status=200, set_cookie=None):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        if set_cookie:
            self.send_header("Set-Cookie", set_cookie)
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length else {}
        if self.path == "/api/auth/login":
            self._reply({"success": True}, set_cookie="auth_token=tok; Path=/; HttpOnly")
        elif self.path == "/api/keys":
            _FakeOmniRouteHandler.keys_created += 1
            _FakeOmniRouteHandler.last_key_name = body.get("name")
            self._reply({"id": "key-id", "key": "sk-live-value", "name": body.get("name")}, 201)

    def do_GET(self):
        if self.path == "/api/keys":
            self._reply({"keys": [{"id": "key-id", "keyPreview": "abcd"}]})
        elif self.path == "/v1/models":
            self._reply({"object": "list", "data": []})
        elif self.path == "/api/combos":
            self._reply({"combos": [], "total": 0})

    def log_message(self, format_string, *args):
        pass


def _start_server(handler_class):
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler_class)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def test_setup_sh_is_served_without_auth_and_embeds_the_request_host(tmp_path, monkeypatch):
    _use_real_opencode_updater(monkeypatch)
    monkeypatch.setenv("OMNI_ROUTER_MASTER_KEY", "test-key")
    monkeypatch.setenv("MODELS_JSON", "[]")
    server, thread = _start_server(RemoteSetupHandler)
    try:
        port = server.server_address[1]
        conn = http.client.HTTPConnection("127.0.0.1", port)
        conn.request("GET", "/setup.sh")
        response = conn.getresponse()
        body = response.read().decode("utf-8")
        assert response.status == 200
        assert f'LLM_ENV_HOST="127.0.0.1:{port}"' in body
    finally:
        server.shutdown()
        thread.join()


def test_setup_sh_rejects_an_invalid_host_header(monkeypatch):
    monkeypatch.setenv("OMNI_ROUTER_MASTER_KEY", "test-key")
    monkeypatch.setenv("MODELS_JSON", "[]")
    server, thread = _start_server(RemoteSetupHandler)
    try:
        port = server.server_address[1]
        conn = http.client.HTTPConnection("127.0.0.1", port)
        conn.putrequest("GET", "/setup.sh", skip_host=True)
        conn.putheader("Host", "evil`touch pwned`")
        conn.endheaders()
        response = conn.getresponse()
        payload = json.loads(response.read())
        assert response.status == 400
        assert "error" in payload
    finally:
        server.shutdown()
        thread.join()


@pytest.mark.parametrize(
    "path",
    ["/update-opencode-config.mjs", "/nonexistent", "/setup", "/config.json"],
)
def test_unknown_paths_404(path, monkeypatch):
    # Exactly two public routes exist -- /setup.sh and /config. In
    # particular, /update-opencode-config.mjs is deliberately NOT a route
    # (its content is embedded into /setup.sh's response via heredoc
    # instead, see render_setup_script()) -- a client trying to fetch it
    # directly must get a plain 404, not a 200 or a 500.
    monkeypatch.setenv("OMNI_ROUTER_MASTER_KEY", "test-key")
    monkeypatch.setenv("MODELS_JSON", "[]")
    server, thread = _start_server(RemoteSetupHandler)
    try:
        port = server.server_address[1]
        conn = http.client.HTTPConnection("127.0.0.1", port)
        conn.request("GET", path)
        response = conn.getresponse()
        payload = json.loads(response.read())
        assert response.status == 404
        assert "error" in payload
    finally:
        server.shutdown()
        thread.join()


def test_config_rejects_an_invalid_host_header(monkeypatch):
    monkeypatch.setenv("OMNI_ROUTER_MASTER_KEY", "test-key")
    server, thread = _start_server(RemoteSetupHandler)
    try:
        port = server.server_address[1]
        conn = http.client.HTTPConnection("127.0.0.1", port)
        conn.putrequest("GET", "/config", skip_host=True)
        conn.putheader("Host", "evil$(id)")
        conn.putheader("Authorization", "Bearer test-key")
        conn.endheaders()
        response = conn.getresponse()
        payload = json.loads(response.read())
        assert response.status == 400
        assert "error" in payload
    finally:
        server.shutdown()
        thread.join()


def test_config_rejects_missing_bearer_token(monkeypatch):
    monkeypatch.setenv("OMNI_ROUTER_MASTER_KEY", "test-key")
    server, thread = _start_server(RemoteSetupHandler)
    try:
        port = server.server_address[1]
        conn = http.client.HTTPConnection("127.0.0.1", port)
        conn.request("GET", "/config")
        response = conn.getresponse()
        assert response.status == 401
    finally:
        server.shutdown()
        thread.join()


def test_config_rejects_wrong_bearer_token(monkeypatch):
    monkeypatch.setenv("OMNI_ROUTER_MASTER_KEY", "test-key")
    server, thread = _start_server(RemoteSetupHandler)
    try:
        port = server.server_address[1]
        conn = http.client.HTTPConnection("127.0.0.1", port)
        conn.request("GET", "/config", headers={"Authorization": "Bearer wrong"})
        response = conn.getresponse()
        assert response.status == 401
    finally:
        server.shutdown()
        thread.join()


def test_config_returns_503_when_master_key_unset(monkeypatch):
    monkeypatch.setenv("OMNI_ROUTER_MASTER_KEY", "")
    server, thread = _start_server(RemoteSetupHandler)
    try:
        port = server.server_address[1]
        conn = http.client.HTTPConnection("127.0.0.1", port)
        conn.request("GET", "/config", headers={"Authorization": "Bearer anything"})
        response = conn.getresponse()
        assert response.status == 503
    finally:
        server.shutdown()
        thread.join()


def test_ensure_api_key_uses_the_given_key_name(tmp_path, monkeypatch):
    # Task 5 (setup-local-llm-agents.sh -> OmniRoute) reuses ensure_api_key
    # with a distinct key_name ("llm-env-local-agents") so a locally-issued
    # key never shows up in OmniRoute's dashboard under the shared
    # remote-installer key's name ("llm-env-remote-agents").
    _FakeOmniRouteHandler.keys_created = 0
    _FakeOmniRouteHandler.last_key_name = None
    fake_omniroute, fake_thread = _start_server(_FakeOmniRouteHandler)
    try:
        fake_port = fake_omniroute.server_address[1]
        cache_path = tmp_path / "api-key.json"
        key = remote_setup_module.ensure_api_key(
            f"http://127.0.0.1:{fake_port}",
            "dashboard-pw",
            cache_path,
            key_name="llm-env-local-agents",
        )
        assert key == "sk-live-value"
        assert _FakeOmniRouteHandler.last_key_name == "llm-env-local-agents"
    finally:
        fake_omniroute.shutdown()
        fake_thread.join()


def test_config_returns_the_scoped_key_and_omniroute_address(tmp_path, monkeypatch):
    _FakeOmniRouteHandler.keys_created = 0
    fake_omniroute, fake_thread = _start_server(_FakeOmniRouteHandler)
    try:
        fake_port = fake_omniroute.server_address[1]
        cache_path = tmp_path / "api-key.json"
        monkeypatch.setattr(remote_setup_module, "CACHE_PATH", cache_path)
        monkeypatch.setenv("OMNI_ROUTER_MASTER_KEY", "test-key")
        monkeypatch.setenv("OMNIROUTE_INTERNAL_URL", f"http://127.0.0.1:{fake_port}")
        monkeypatch.setenv("OMNIROUTE_DASHBOARD_PASSWORD", "dashboard-pw")
        monkeypatch.setenv("OMNIROUTE_PORT", "20128")
        monkeypatch.setenv(
            "MODELS_JSON",
            json.dumps([{"alias": "a", "ctx_size": 8192, "client_max_output_tokens": 4096}]),
        )

        server, thread = _start_server(RemoteSetupHandler)
        try:
            port = server.server_address[1]
            conn = http.client.HTTPConnection("127.0.0.1", port)
            conn.request(
                "GET", "/config", headers={"Authorization": "Bearer test-key"}
            )
            response = conn.getresponse()
            payload = json.loads(response.read())
            assert response.status == 200
            assert payload["api_key"] == "sk-live-value"
            assert payload["omniroute_base_url"] == "http://127.0.0.1:20128"
            assert payload["omniroute_dashboard_password"] == "dashboard-pw"
            assert payload["models"] == [
                {"id": "llama-cpp/a", "label": "a", "ctx_size": 8192, "client_max_output_tokens": 4096}
            ]
            assert _FakeOmniRouteHandler.keys_created == 1

            # Second call reuses the cached key -- no new key created.
            conn = http.client.HTTPConnection("127.0.0.1", port)
            conn.request(
                "GET", "/config", headers={"Authorization": "Bearer test-key"}
            )
            response = conn.getresponse()
            payload = json.loads(response.read())
            assert payload["api_key"] == "sk-live-value"
            assert _FakeOmniRouteHandler.keys_created == 1
        finally:
            server.shutdown()
            thread.join()
    finally:
        fake_omniroute.shutdown()
        fake_thread.join()


class _FakeOmniRouteWithComboHandler(_FakeOmniRouteHandler):
    """Adds a real combo to /v1/models and /api/combos so /config's
    server-side compute_combo_context() call has something to resolve."""

    def do_GET(self):
        if self.path == "/v1/models":
            self._reply(
                {
                    "object": "list",
                    "data": [
                        {
                            "id": "grok-cli/grok-4.6",
                            "root": "grok-4.6",
                            "owned_by": "grok-cli",
                            "context_length": 500000,
                            "max_output_tokens": 128000,
                        }
                    ],
                }
            )
            return
        if self.path == "/api/combos":
            self._reply(
                {
                    "combos": [
                        {
                            "name": "my-planning",
                            "models": [
                                {
                                    "kind": "model",
                                    "providerId": "grok-cli",
                                    "model": "grok-cli/grok-4.6",
                                }
                            ],
                        }
                    ],
                    "total": 1,
                }
            )
            return
        super().do_GET()


def test_config_includes_omniroute_combos_alongside_llm_server_models(tmp_path, monkeypatch):
    _FakeOmniRouteWithComboHandler.keys_created = 0
    fake_omniroute, fake_thread = _start_server(_FakeOmniRouteWithComboHandler)
    try:
        fake_port = fake_omniroute.server_address[1]
        cache_path = tmp_path / "api-key.json"
        monkeypatch.setattr(remote_setup_module, "CACHE_PATH", cache_path)
        monkeypatch.setenv("OMNI_ROUTER_MASTER_KEY", "test-key")
        monkeypatch.setenv("OMNIROUTE_INTERNAL_URL", f"http://127.0.0.1:{fake_port}")
        monkeypatch.setenv("OMNIROUTE_DASHBOARD_PASSWORD", "dashboard-pw")
        monkeypatch.setenv("OMNIROUTE_PORT", "20128")
        monkeypatch.setenv(
            "MODELS_JSON",
            json.dumps([{"alias": "a", "ctx_size": 8192, "client_max_output_tokens": 4096}]),
        )

        server, thread = _start_server(RemoteSetupHandler)
        try:
            port = server.server_address[1]
            conn = http.client.HTTPConnection("127.0.0.1", port)
            conn.request("GET", "/config", headers={"Authorization": "Bearer test-key"})
            response = conn.getresponse()
            payload = json.loads(response.read())
            assert response.status == 200
            assert payload["models"] == [
                {
                    "id": "llama-cpp/a",
                    "label": "a",
                    "ctx_size": 8192,
                    "client_max_output_tokens": 4096,
                },
                {
                    "id": "my-planning",
                    "label": "my-planning (combo)",
                    "ctx_size": 500000,
                    "client_max_output_tokens": 128000,
                },
            ]
        finally:
            server.shutdown()
            thread.join()
    finally:
        fake_omniroute.shutdown()
        fake_thread.join()


def test_config_returns_502_when_combo_context_fails(tmp_path, monkeypatch):
    class _BrokenComboHandler(_FakeOmniRouteHandler):
        def do_GET(self):
            if self.path == "/v1/models":
                self._reply({"unexpected": "shape"})
                return
            super().do_GET()

    _BrokenComboHandler.keys_created = 0
    fake_omniroute, fake_thread = _start_server(_BrokenComboHandler)
    try:
        fake_port = fake_omniroute.server_address[1]
        cache_path = tmp_path / "api-key.json"
        monkeypatch.setattr(remote_setup_module, "CACHE_PATH", cache_path)
        monkeypatch.setenv("OMNI_ROUTER_MASTER_KEY", "test-key")
        monkeypatch.setenv("OMNIROUTE_INTERNAL_URL", f"http://127.0.0.1:{fake_port}")
        monkeypatch.setenv("OMNIROUTE_DASHBOARD_PASSWORD", "dashboard-pw")
        monkeypatch.setenv("OMNIROUTE_PORT", "20128")
        monkeypatch.setenv("MODELS_JSON", "[]")

        server, thread = _start_server(RemoteSetupHandler)
        try:
            port = server.server_address[1]
            conn = http.client.HTTPConnection("127.0.0.1", port)
            conn.request("GET", "/config", headers={"Authorization": "Bearer test-key"})
            response = conn.getresponse()
            payload = json.loads(response.read())
            assert response.status == 502
            assert "combo" in payload["error"]
        finally:
            server.shutdown()
            thread.join()
    finally:
        fake_omniroute.shutdown()
        fake_thread.join()


def test_config_returns_json_500_for_an_unexpected_error(tmp_path, monkeypatch):
    # A malformed MODELS_JSON env var isn't an OmniRouteError -- it's a
    # plain JSONDecodeError raised deep inside _handle_config, after
    # ensure_api_key() has already succeeded. The handler must still
    # produce a JSON 500, not crash the request thread / hang the socket.
    _FakeOmniRouteHandler.keys_created = 0
    fake_omniroute, fake_thread = _start_server(_FakeOmniRouteHandler)
    try:
        fake_port = fake_omniroute.server_address[1]
        cache_path = tmp_path / "api-key.json"
        monkeypatch.setattr(remote_setup_module, "CACHE_PATH", cache_path)
        monkeypatch.setenv("OMNI_ROUTER_MASTER_KEY", "test-key")
        monkeypatch.setenv("OMNIROUTE_INTERNAL_URL", f"http://127.0.0.1:{fake_port}")
        monkeypatch.setenv("OMNIROUTE_DASHBOARD_PASSWORD", "dashboard-pw")
        monkeypatch.setenv("OMNIROUTE_PORT", "20128")
        monkeypatch.setenv("MODELS_JSON", "not valid json")

        server, thread = _start_server(RemoteSetupHandler)
        try:
            port = server.server_address[1]
            conn = http.client.HTTPConnection("127.0.0.1", port)
            conn.request(
                "GET", "/config", headers={"Authorization": "Bearer test-key"}
            )
            response = conn.getresponse()
            payload = json.loads(response.read())
            assert response.status == 500
            assert "error" in payload
        finally:
            server.shutdown()
            thread.join()
    finally:
        fake_omniroute.shutdown()
        fake_thread.join()


def test_ensure_api_key_concurrent_calls_create_only_one_key(tmp_path, monkeypatch):
    # Two /config requests arriving close together on a cold cache must
    # not each mint their own OmniRoute API key -- ensure_api_key()'s
    # module-level lock serializes the read-check-create-write sequence.
    _FakeOmniRouteHandler.keys_created = 0
    fake_omniroute, fake_thread = _start_server(_FakeOmniRouteHandler)
    try:
        fake_port = fake_omniroute.server_address[1]
        base_url = f"http://127.0.0.1:{fake_port}"
        cache_path = tmp_path / "api-key.json"

        results = []
        errors = []

        def _call():
            try:
                results.append(
                    remote_setup_module.ensure_api_key(base_url, "dashboard-pw", cache_path)
                )
            except Exception as exc:  # noqa: BLE001 -- pragma: no cover, failure path
                errors.append(exc)

        threads = [threading.Thread(target=_call) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert not errors
        assert len(results) == 8
        assert all(value == "sk-live-value" for value in results)
        assert _FakeOmniRouteHandler.keys_created == 1
    finally:
        fake_omniroute.shutdown()
        fake_thread.join()


class _SlowLoginOmniRouteHandler(_FakeOmniRouteHandler):
    """Widens the cold-cache race window so two overlapping callers would
    reliably both mint a key if issuance were not serialized."""

    def do_POST(self):
        if self.path == "/api/auth/login":
            time.sleep(0.3)
        super().do_POST()


_CONCURRENT_ISSUE_KEY_CHILD = """
import pathlib, sys, time
sys.path.insert(0, sys.argv[1])
import pylib.remote_setup as remote_setup

base_url, cache_path, ready, go = sys.argv[2], pathlib.Path(sys.argv[3]), pathlib.Path(sys.argv[4]), pathlib.Path(sys.argv[5])
ready.write_text("ready", encoding="utf-8")
deadline = time.monotonic() + 30
while not go.exists():
    if time.monotonic() > deadline:
        raise SystemExit("timed out waiting for the start gate")
    time.sleep(0.005)
print(remote_setup.ensure_api_key(base_url, "dashboard-pw", cache_path))
"""


def test_ensure_api_key_serializes_across_separate_processes(tmp_path):
    """`llmenv omniroute issue-key` calls ensure_api_key() from its own
    process, so a threading.Lock cannot serialize it against the HTTP
    server (or against a second CLI invocation). Two genuinely separate
    processes hitting a cold cache must still produce exactly one
    POST /api/keys, and both must return the key that ends up cached."""
    _FakeOmniRouteHandler.keys_created = 0
    fake_omniroute, fake_thread = _start_server(_SlowLoginOmniRouteHandler)
    children = []
    try:
        base_url = f"http://127.0.0.1:{fake_omniroute.server_address[1]}"
        cache_path = tmp_path / "api-key.json"
        gate = tmp_path / "go"
        script = tmp_path / "child.py"
        script.write_text(_CONCURRENT_ISSUE_KEY_CHILD, encoding="utf-8")

        ready_files = [tmp_path / f"ready-{index}" for index in range(2)]
        for ready in ready_files:
            children.append(
                subprocess.Popen(
                    [
                        sys.executable,
                        str(script),
                        str(REPO_ROOT),
                        base_url,
                        str(cache_path),
                        str(ready),
                        str(gate),
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
            )

        deadline = time.monotonic() + 30
        while not all(ready.exists() for ready in ready_files):
            assert time.monotonic() < deadline, "children never became ready"
            time.sleep(0.01)
        gate.write_text("go", encoding="utf-8")

        outputs = []
        for child in children:
            stdout, stderr = child.communicate(timeout=60)
            assert child.returncode == 0, stderr
            outputs.append(stdout.strip())

        assert outputs == ["sk-live-value", "sk-live-value"]
        assert _FakeOmniRouteHandler.keys_created == 1
        assert read_cached_key(cache_path) == {"id": "key-id", "key": "sk-live-value"}
    finally:
        for child in children:
            if child.poll() is None:  # pragma: no cover - failure path only
                child.kill()
        fake_omniroute.shutdown()
        fake_thread.join()


def test_ensure_api_key_does_not_lock_the_replaceable_cache_inode(tmp_path):
    """write_cached_key() publishes the cache with os.replace(), so a lock
    taken on the cache file itself would guard a stale inode. The lock must
    be a stable sibling file."""
    _FakeOmniRouteHandler.keys_created = 0
    fake_omniroute, fake_thread = _start_server(_FakeOmniRouteHandler)
    try:
        base_url = f"http://127.0.0.1:{fake_omniroute.server_address[1]}"
        cache_path = tmp_path / "api-key.json"
        remote_setup_module.ensure_api_key(base_url, "dashboard-pw", cache_path)

        lock_path = cache_path.with_name(cache_path.name + ".lock")
        assert lock_path.exists()
        assert stat.S_IMODE(lock_path.stat().st_mode) == 0o600
    finally:
        fake_omniroute.shutdown()
        fake_thread.join()


def test_ensure_api_key_reissues_after_dashboard_revocation(tmp_path, monkeypatch):
    # _FakeOmniRouteHandler.do_GET always lists exactly one key, "key-id"
    # (see its fixed do_GET body above) -- a cache seeded with a DIFFERENT
    # id therefore can never appear in that listing, which is exactly what
    # a dashboard-side revocation of the previously cached key looks like
    # from ensure_api_key()'s point of view: cache present, but the id it
    # names is gone from GET /api/keys. ensure_api_key() must not blindly
    # trust the cache file -- it must notice the id is missing, mint a
    # replacement, and overwrite the cache with the new value.
    _FakeOmniRouteHandler.keys_created = 0
    fake_omniroute, fake_thread = _start_server(_FakeOmniRouteHandler)
    try:
        fake_port = fake_omniroute.server_address[1]
        base_url = f"http://127.0.0.1:{fake_port}"
        cache_path = tmp_path / "api-key.json"
        write_cached_key(cache_path, "revoked-key-id", "sk-old-revoked-value")

        result = remote_setup_module.ensure_api_key(base_url, "dashboard-pw", cache_path)

        assert result == "sk-live-value"
        assert _FakeOmniRouteHandler.keys_created == 1
        assert read_cached_key(cache_path) == {"id": "key-id", "key": "sk-live-value"}
    finally:
        fake_omniroute.shutdown()
        fake_thread.join()


def _run_generated_setup_sh(tmp_path, monkeypatch, prepare_home=None, timeout=60.0):
    """Fetch the real /setup.sh from a live RemoteSetupHandler (backed by a
    fake OmniRoute) and execute it with bash in an isolated $HOME, typing
    the master key into a pty. Returns (exit_code, output, home_dir)."""
    for cmd in ("bash", "curl", "jq", "node"):
        if shutil.which(cmd) is None:
            pytest.skip(f"{cmd} not available on this host")

    _FakeOmniRouteHandler.keys_created = 0
    fake_omniroute, fake_thread = _start_server(_FakeOmniRouteHandler)
    try:
        fake_port = fake_omniroute.server_address[1]
        monkeypatch.setattr(remote_setup_module, "CACHE_PATH", tmp_path / "api-key.json")
        _use_real_opencode_updater(monkeypatch)
        monkeypatch.setenv("OMNI_ROUTER_MASTER_KEY", "test-master-key")
        monkeypatch.setenv("OMNIROUTE_INTERNAL_URL", f"http://127.0.0.1:{fake_port}")
        monkeypatch.setenv("OMNIROUTE_DASHBOARD_PASSWORD", "dashboard-pw")
        monkeypatch.setenv("OMNIROUTE_PORT", "20128")
        monkeypatch.setenv(
            "MODELS_JSON",
            json.dumps([{"alias": "a", "ctx_size": 8192, "client_max_output_tokens": 4096}]),
        )

        server, thread = _start_server(RemoteSetupHandler)
        try:
            port = server.server_address[1]
            conn = http.client.HTTPConnection("127.0.0.1", port)
            conn.request("GET", "/setup.sh")
            script_body = conn.getresponse().read()
            script_path = tmp_path / "setup.sh"
            script_path.write_bytes(script_body)

            home_dir = tmp_path / "home"
            home_dir.mkdir()
            if prepare_home is not None:
                prepare_home(home_dir)
            child_env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(home_dir)}

            pid, master_fd = pty.fork()
            if pid == 0:
                os.execvpe("bash", ["bash", str(script_path)], child_env)

            os.write(master_fd, b"test-master-key\n")
            # A regression that makes the installer *hang* (jq reading a
            # FIFO) must surface as a test failure, not a stuck suite.
            output = b""
            deadline = time.monotonic() + timeout
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    os.kill(pid, 9)
                    os.waitpid(pid, 0)
                    raise AssertionError(
                        "the generated setup.sh hung; output so far: "
                        + output.decode(errors="replace")
                    )
                readable, _, _ = select.select([master_fd], [], [], remaining)
                if not readable:
                    continue
                try:
                    chunk = os.read(master_fd, 4096)
                except OSError:
                    break
                if not chunk:
                    break
                output += chunk
            _, status = os.waitpid(pid, 0)
            return os.WEXITSTATUS(status), output.decode(errors="replace"), home_dir
        finally:
            server.shutdown()
            thread.join()
    finally:
        fake_omniroute.shutdown()
        fake_thread.join()


def test_setup_sh_executed_end_to_end_configures_pi_and_opencode(tmp_path, monkeypatch):
    """Runs the ACTUAL generated /setup.sh via bash, in an isolated $HOME,
    typing the master key into a pty -- the tests above only inspect the
    script's text or drive the handler in-process; this is the one test
    that proves the script is genuinely executable end to end, including
    its /dev/tty prompt, its jq transforms, and its OpenCode update."""
    exit_code, output, home_dir = _run_generated_setup_sh(tmp_path, monkeypatch)
    assert exit_code == 0, output

    pi_models = json.loads((home_dir / ".pi" / "agent" / "models.json").read_text())
    assert pi_models["providers"]["local-llm-env"]["models"][0]["id"] == "llama-cpp/a"
    pi_settings = json.loads((home_dir / ".pi" / "agent" / "settings.json").read_text())
    assert pi_settings["enabledModels"] == ["local-llm-env/llama-cpp/a"]

    opencode_config = json.loads(
        (home_dir / ".config" / "opencode" / "opencode.jsonc").read_text()
    )
    assert "llama-cpp/a" in opencode_config["provider"]["local-llm-env"]["models"]

    pi_models_path = home_dir / ".pi" / "agent" / "models.json"
    assert (pi_models_path.stat().st_mode & 0o777) == 0o600

    assert "OmniRoute dashboard: http://127.0.0.1:20128" in output
    assert "dashboard-pw" in output
    # Regression: the final summary used to read a `.alias` field that
    # doesn't exist on the id/label model shape, silently printing
    # "Done. Model(s): " with no names at all.
    assert "Done. Model(s): llama-cpp/a" in output


def test_setup_sh_never_passes_the_api_key_through_a_command_argument(
    tmp_path, monkeypatch
):
    """The scoped key ends up inside the Pi/OpenCode provider documents. Any
    `--argjson` carrying one of those documents would put the key in
    /proc/<pid>/cmdline for every other local user to read while jq runs.

    Asserted by static inspection of the rendered script rather than by
    sampling `ps` during execution: the jq invocations complete in
    milliseconds, so a sampler races the very window it is meant to observe
    and would pass by luck on a fast machine. The script's own text is the
    thing under test, and it is fully deterministic -- if no `--argjson`
    survives at all, no argument can carry the key."""
    _use_real_opencode_updater(monkeypatch)
    script = render_setup_script("llm.local:20130")

    code_lines = [
        line for line in script.splitlines() if not line.lstrip().startswith("#")
    ]
    assert not [line for line in code_lines if "--argjson" in line]
    # The key only ever reaches jq through file-backed options.
    assert '--rawfile api_key "$api_key_file"' in script
    assert '--slurpfile models "$models_file"' in script
    assert '"$pi_provider_file"' in script

    # ...and the end-to-end run confirms the key still lands in the configs.
    exit_code, output, home_dir = _run_generated_setup_sh(tmp_path, monkeypatch)
    assert exit_code == 0, output
    pi_models = json.loads((home_dir / ".pi" / "agent" / "models.json").read_text())
    assert pi_models["providers"]["local-llm-env"]["apiKey"] == "sk-live-value"
    assert "sk-live-value" not in output


def test_setup_sh_cleanup_trap_survives_an_empty_staged_files_array(monkeypatch):
    """`"${staged_files[@]}"` alone raises "unbound variable" under `set -u`
    on bash < 4.4 (macOS's stock /bin/bash is 3.2) once every element has
    been consumed by the staged_files=("${staged_files[@]:1}") slices --
    i.e. on every successful run, right as cleanup() fires on exit. Live
    reproduction on macOS: exactly this crash, right after the final
    "Done." output. `"${staged_files[@]-}"` is the portable, bash-3.2-safe
    idiom; assert it's actually what's rendered."""
    _use_real_opencode_updater(monkeypatch)
    script = render_setup_script("llm.local:20130")
    assert 'for path in "${staged_files[@]-}"; do' in script


def test_setup_sh_merges_into_a_well_formed_existing_pi_configuration(
    tmp_path, monkeypatch
):
    """An existing, valid Pi config must be preserved, not replaced."""

    def prepare(home_dir):
        pi_dir = home_dir / ".pi" / "agent"
        pi_dir.mkdir(parents=True)
        (pi_dir / "models.json").write_text(
            json.dumps({"keepMe": 1, "providers": {"other": {"baseUrl": "http://x"}}}),
            encoding="utf-8",
        )
        (pi_dir / "settings.json").write_text(
            json.dumps({"theme": "dark"}), encoding="utf-8"
        )

    exit_code, output, home_dir = _run_generated_setup_sh(
        tmp_path, monkeypatch, prepare_home=prepare
    )

    assert exit_code == 0, output
    pi_models = json.loads((home_dir / ".pi" / "agent" / "models.json").read_text())
    assert pi_models["keepMe"] == 1
    assert "other" in pi_models["providers"]
    assert "local-llm-env" in pi_models["providers"]
    pi_settings = json.loads((home_dir / ".pi" / "agent" / "settings.json").read_text())
    assert pi_settings["theme"] == "dark"
    assert pi_settings["enabledModels"] == ["local-llm-env/llama-cpp/a"]


@pytest.mark.parametrize("target", ["models.json", "settings.json"])
def test_setup_sh_rejects_a_non_regular_pi_target_instead_of_hanging(
    tmp_path, monkeypatch, target
):
    """A FIFO in place of a Pi config would block jq forever. The installer
    must refuse it with a clear message instead."""

    def prepare(home_dir):
        pi_dir = home_dir / ".pi" / "agent"
        pi_dir.mkdir(parents=True)
        os.mkfifo(pi_dir / target)

    exit_code, output, _ = _run_generated_setup_sh(
        tmp_path, monkeypatch, prepare_home=prepare, timeout=30.0
    )

    assert exit_code == 1, output
    assert "not a regular file" in output


@pytest.mark.parametrize("target", ["models.json", "settings.json"])
def test_setup_sh_rejects_a_directory_in_place_of_a_pi_file(
    tmp_path, monkeypatch, target
):
    def prepare(home_dir):
        (home_dir / ".pi" / "agent" / target).mkdir(parents=True)

    exit_code, output, _ = _run_generated_setup_sh(
        tmp_path, monkeypatch, prepare_home=prepare, timeout=30.0
    )

    assert exit_code == 1, output
    assert "not a regular file" in output


@pytest.mark.parametrize("target", ["models.json", "settings.json"])
def test_setup_sh_rejects_a_multi_document_existing_pi_file(
    tmp_path, monkeypatch, target
):
    """Plain jq reads two concatenated JSON documents and writes two
    transformed documents back out -- silently producing an equally
    malformed staged file. Reject the input instead."""

    def prepare(home_dir):
        pi_dir = home_dir / ".pi" / "agent"
        pi_dir.mkdir(parents=True)
        (pi_dir / target).write_text('{"a": 1}\n{"b": 2}\n', encoding="utf-8")

    exit_code, output, home_dir = _run_generated_setup_sh(
        tmp_path, monkeypatch, prepare_home=prepare, timeout=30.0
    )

    assert exit_code == 1, output
    assert "exactly one JSON object" in output
    # The malformed file is left exactly as it was -- nothing was staged in.
    assert (home_dir / ".pi" / "agent" / target).read_text() == '{"a": 1}\n{"b": 2}\n'


def test_setup_sh_rejects_an_existing_pi_config_with_a_non_object_providers(
    tmp_path, monkeypatch
):
    def prepare(home_dir):
        pi_dir = home_dir / ".pi" / "agent"
        pi_dir.mkdir(parents=True)
        (pi_dir / "models.json").write_text('{"providers": []}', encoding="utf-8")

    exit_code, output, _ = _run_generated_setup_sh(
        tmp_path, monkeypatch, prepare_home=prepare, timeout=30.0
    )

    assert exit_code == 1, output
    assert "object providers" in output


def test_setup_sh_updates_an_existing_opencode_favorites_state(tmp_path, monkeypatch):
    """If the remote machine already has OpenCode state
    ($XDG_STATE_HOME/opencode/model.json, here defaulting to
    ~/.local/state/opencode/model.json since XDG_STATE_HOME isn't set),
    the installer adds the configured model to favorites -- same outcome
    setup-local-llm-agents.sh produces locally -- without requiring
    `opencode` to be installed or any particular version, since editing
    an existing file needs no version check."""

    def prepare(home_dir):
        state_dir = home_dir / ".local" / "state" / "opencode"
        state_dir.mkdir(parents=True)
        (state_dir / "model.json").write_text(
            json.dumps(
                {
                    "recent": [],
                    "favorite": [{"providerID": "other", "modelID": "some-model"}],
                    "variant": {},
                }
            ),
            encoding="utf-8",
        )

    exit_code, output, home_dir = _run_generated_setup_sh(
        tmp_path, monkeypatch, prepare_home=prepare
    )

    assert exit_code == 0, output
    assert "OpenCode favorites configured" in output
    state = json.loads(
        (home_dir / ".local" / "state" / "opencode" / "model.json").read_text()
    )
    assert {"providerID": "local-llm-env", "modelID": "llama-cpp/a"} in state["favorite"]
    assert {"providerID": "other", "modelID": "some-model"} in state["favorite"]


def test_setup_sh_leaves_opencode_favorites_untouched_when_absent(tmp_path, monkeypatch):
    """No pre-existing state file -- the installer must not create one
    (that would require `opencode` installed at an exact pinned version
    on the remote machine, per the design trade-off); OpenCode creates
    its own default state on first use instead."""
    exit_code, output, home_dir = _run_generated_setup_sh(tmp_path, monkeypatch)

    assert exit_code == 0, output
    assert "OpenCode favorites configured" not in output
    assert not (home_dir / ".local" / "state" / "opencode" / "model.json").exists()


def test_setup_sh_rejects_a_non_regular_opencode_state_file(tmp_path, monkeypatch):
    def prepare(home_dir):
        state_dir = home_dir / ".local" / "state" / "opencode"
        state_dir.mkdir(parents=True)
        os.mkfifo(state_dir / "model.json")

    exit_code, output, _ = _run_generated_setup_sh(
        tmp_path, monkeypatch, prepare_home=prepare, timeout=30.0
    )

    assert exit_code == 1, output
    assert "not a regular file" in output


class _BareListKeysHandler(_FakeOmniRouteHandler):
    """GET /api/keys answers with a BARE LIST instead of {"keys": [...]}.

    Which of the two shapes the live OmniRoute build actually returns was
    never confirmed against a running instance, and guessing wrong used to
    be silent: the old `listing.get("keys", [])` fallback turned a bare
    list into an empty listing, so the cached key never looked "still
    present" and a fresh OmniRoute API key was minted on every /config.
    """

    def do_GET(self):
        if self.path == "/api/keys":
            self._reply([{"id": "key-id", "keyPreview": "abcd"}])


class _MalformedKeysHandler(_FakeOmniRouteHandler):
    """GET /api/keys answers with a shape nothing can be read out of."""

    def do_GET(self):
        if self.path == "/api/keys":
            self._reply({"unexpected": "shape"})


def test_ensure_api_key_reuses_the_cached_key_from_a_dict_listing(tmp_path):
    _FakeOmniRouteHandler.keys_created = 0
    fake_omniroute, fake_thread = _start_server(_FakeOmniRouteHandler)
    try:
        base_url = f"http://127.0.0.1:{fake_omniroute.server_address[1]}"
        cache_path = tmp_path / "api-key.json"
        write_cached_key(cache_path, "key-id", "sk-cached-value")

        assert (
            remote_setup_module.ensure_api_key(base_url, "dashboard-pw", cache_path)
            == "sk-cached-value"
        )
        assert _FakeOmniRouteHandler.keys_created == 0
    finally:
        fake_omniroute.shutdown()
        fake_thread.join()


def test_ensure_api_key_reuses_the_cached_key_from_a_bare_list_listing(tmp_path):
    _FakeOmniRouteHandler.keys_created = 0
    fake_omniroute, fake_thread = _start_server(_BareListKeysHandler)
    try:
        base_url = f"http://127.0.0.1:{fake_omniroute.server_address[1]}"
        cache_path = tmp_path / "api-key.json"
        write_cached_key(cache_path, "key-id", "sk-cached-value")

        assert (
            remote_setup_module.ensure_api_key(base_url, "dashboard-pw", cache_path)
            == "sk-cached-value"
        )
        assert _FakeOmniRouteHandler.keys_created == 0
    finally:
        fake_omniroute.shutdown()
        fake_thread.join()


def test_ensure_api_key_raises_instead_of_minting_on_an_unrecognized_listing(tmp_path):
    # Failing loud beats silently creating a new key on every request and
    # piling up orphaned keys in OmniRoute's dashboard.
    _FakeOmniRouteHandler.keys_created = 0
    fake_omniroute, fake_thread = _start_server(_MalformedKeysHandler)
    try:
        base_url = f"http://127.0.0.1:{fake_omniroute.server_address[1]}"
        cache_path = tmp_path / "api-key.json"
        write_cached_key(cache_path, "key-id", "sk-cached-value")

        with pytest.raises(OmniRouteError) as excinfo:
            remote_setup_module.ensure_api_key(base_url, "dashboard-pw", cache_path)

        assert "unexpected API key listing shape" in str(excinfo.value)
        assert _FakeOmniRouteHandler.keys_created == 0
    finally:
        fake_omniroute.shutdown()
        fake_thread.join()


def test_handler_sets_a_socket_timeout():
    # Without it, a client that connects and never sends a request line
    # pins a server thread indefinitely on a LAN-exposed service.
    assert RemoteSetupHandler.timeout == 10


def test_handler_logs_a_non_2xx_response_but_stays_quiet_on_200(capsys, monkeypatch):
    _use_real_opencode_updater(monkeypatch)
    server, thread = _start_server(RemoteSetupHandler)
    try:
        port = server.server_address[1]
        conn = http.client.HTTPConnection("127.0.0.1", port)
        conn.request("GET", "/setup.sh")
        assert conn.getresponse().status == 200
        assert capsys.readouterr().err == ""

        conn = http.client.HTTPConnection("127.0.0.1", port)
        conn.request("GET", "/nope")
        assert conn.getresponse().status == 404
    finally:
        server.shutdown()
        thread.join()
    assert '"GET /nope HTTP/1.1" 404' in capsys.readouterr().err


def test_install_termination_handlers_shuts_the_server_down_on_sigterm():
    """main() runs this process directly as the container's `command` --
    no tini/init wrapper. As PID 1 inside its own PID namespace, Linux
    only applies the normal "terminate on SIGTERM" default to signals the
    process has installed a handler for; an *unhandled* SIGTERM is
    silently ignored there. That failure mode can't be reproduced by
    sending a real SIGTERM to a subprocess in a test -- a plain
    subprocess on the host is never PID 1 of its own PID namespace, so
    the kernel's default disposition already terminates it whether or not
    a handler is installed, masking exactly the bug this guards against.
    Instead this exercises install_termination_handlers() directly: it
    must register handlers that receive a real, process-wide SIGTERM (the
    prerequisite for behaving correctly once it *is* PID 1) and shut the
    server down promptly without calling shutdown() from the handler
    itself (which would deadlock against serve_forever()'s own lock)."""
    import signal

    server, thread = _start_server(RemoteSetupHandler)
    previous_term = signal.getsignal(signal.SIGTERM)
    previous_int = signal.getsignal(signal.SIGINT)
    try:
        remote_setup_module.install_termination_handlers(server)
        os.kill(os.getpid(), signal.SIGTERM)
        thread.join(timeout=5)
        assert not thread.is_alive(), "server did not shut down after SIGTERM"
    finally:
        signal.signal(signal.SIGTERM, previous_term)
        signal.signal(signal.SIGINT, previous_int)
        if thread.is_alive():
            server.shutdown()
            thread.join()
