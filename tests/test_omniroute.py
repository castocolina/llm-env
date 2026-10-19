import base64
import io
import json
import sys
import urllib.error
from email.message import Message
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pylib.omniroute as omniroute_module
from pylib.omniroute import (
    CONNECTION_NAME,
    OmniRouteError,
    build_payload,
    compute_combo_context,
    find_connection,
    fix_codex_context_overrides,
    import_codex_auth,
    provision,
)

LOGIN_URL = "http://127.0.0.1:20128/api/auth/login"


class FakeResponse:
    def __init__(self, payload, *, set_cookie=None):
        self._body = json.dumps(payload).encode("utf-8")
        self.headers = Message()
        if set_cookie is not None:
            self.headers["Set-Cookie"] = set_cookie

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


def _fake_login_ok(set_cookie="auth_token=session-token; Path=/; HttpOnly"):
    def fake(request, timeout):
        assert request.full_url == LOGIN_URL
        assert request.get_method() == "POST"
        assert json.loads(request.data) == {"password": "dashboard-pw"}
        return FakeResponse({"success": True}, set_cookie=set_cookie)

    return fake


def test_build_payload_uses_the_real_router_api_key():
    payload = build_payload(port=8000, api_key="secret-key")
    assert payload == {
        "provider": "llama-cpp",
        "name": CONNECTION_NAME,
        "apiKey": "secret-key",
        "priority": 1,
        "testStatus": "active",
        "providerSpecificData": {"baseUrl": "http://llm-server:8000/v1"},
    }


def test_find_connection_matches_by_name():
    connections = [{"id": "1", "name": "other"}, {"id": "2", "name": CONNECTION_NAME}]
    found = find_connection(connections)
    assert found is not None
    assert found["id"] == "2"


def test_find_connection_returns_none_when_absent():
    assert find_connection([{"id": "1", "name": "other"}]) is None


def test_provision_creates_when_no_existing_connection(monkeypatch):
    calls = []
    login = _fake_login_ok()

    def fake_urlopen(request, timeout):
        if request.full_url == LOGIN_URL:
            return login(request, timeout)
        calls.append((request.get_method(), request.full_url, request.data))
        if request.get_method() == "GET":
            return FakeResponse({"connections": []})
        return FakeResponse({"connection": {"id": "new-id"}})

    monkeypatch.setattr(omniroute_module.urllib.request, "urlopen", fake_urlopen)
    result = provision("http://127.0.0.1:20128", "dashboard-pw", 8000, "api-key")
    assert result == {"action": "created", "id": "new-id"}
    assert calls[0][0] == "GET"
    assert calls[0][1] == "http://127.0.0.1:20128/api/providers"
    assert calls[1][0] == "POST"
    assert calls[1][1] == "http://127.0.0.1:20128/api/providers"
    posted = json.loads(calls[1][2])
    assert posted["providerSpecificData"]["baseUrl"] == "http://llm-server:8000/v1"
    assert posted["apiKey"] == "api-key"


def test_provision_updates_when_existing_connection(monkeypatch):
    login = _fake_login_ok()

    def fake_urlopen(request, timeout):
        if request.full_url == LOGIN_URL:
            return login(request, timeout)
        if request.get_method() == "GET":
            return FakeResponse({"connections": [{"id": "existing-id", "name": CONNECTION_NAME}]})
        assert request.get_method() == "PUT"
        assert request.full_url == "http://127.0.0.1:20128/api/providers/existing-id"
        return FakeResponse({"connection": {"id": "existing-id"}})

    monkeypatch.setattr(omniroute_module.urllib.request, "urlopen", fake_urlopen)
    result = provision("http://127.0.0.1:20128", "dashboard-pw", 8000, "api-key")
    assert result == {"action": "updated", "id": "existing-id"}


def test_provision_accepts_a_providers_wrapped_listing(monkeypatch):
    login = _fake_login_ok()

    def fake_urlopen(request, timeout):
        if request.full_url == LOGIN_URL:
            return login(request, timeout)
        if request.get_method() == "GET":
            return FakeResponse({"providers": [{"id": "existing-id", "name": CONNECTION_NAME}]})
        return FakeResponse({"connection": {"id": "existing-id"}})

    monkeypatch.setattr(omniroute_module.urllib.request, "urlopen", fake_urlopen)
    result = provision("http://127.0.0.1:20128", "dashboard-pw", 8000, "api-key")
    assert result == {"action": "updated", "id": "existing-id"}


def test_provision_sends_the_session_cookie(monkeypatch):
    seen_cookies = []
    login = _fake_login_ok(set_cookie="auth_token=secret-session; Path=/; HttpOnly")

    def fake_urlopen(request, timeout):
        if request.full_url == LOGIN_URL:
            return login(request, timeout)
        seen_cookies.append(request.get_header("Cookie"))
        if request.get_method() == "GET":
            return FakeResponse({"connections": []})
        return FakeResponse({"connection": {"id": "x"}})

    monkeypatch.setattr(omniroute_module.urllib.request, "urlopen", fake_urlopen)
    provision("http://127.0.0.1:20128", "dashboard-pw", 8000, "api-key")
    assert all(cookie == "auth_token=secret-session" for cookie in seen_cookies)


def test_provision_raises_when_login_returns_no_session_cookie(monkeypatch):
    def fake_urlopen(request, timeout):
        assert request.full_url == LOGIN_URL
        return FakeResponse({"success": True})  # no Set-Cookie

    monkeypatch.setattr(omniroute_module.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(OmniRouteError, match="no session cookie"):
        provision("http://127.0.0.1:20128", "dashboard-pw", 8000, "api-key")


def test_provision_raises_omniroute_error_on_login_http_failure(monkeypatch):
    def fake_urlopen(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 401, "boom", Message(), None)

    monkeypatch.setattr(omniroute_module.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(OmniRouteError):
        provision("http://127.0.0.1:20128", "wrong-pw", 8000, "api-key")


def test_provision_raises_omniroute_error_on_http_failure(monkeypatch):
    login = _fake_login_ok()

    def fake_urlopen(request, timeout):
        if request.full_url == LOGIN_URL:
            return login(request, timeout)
        raise urllib.error.HTTPError(request.full_url, 500, "boom", Message(), None)

    monkeypatch.setattr(omniroute_module.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(OmniRouteError):
        provision("http://127.0.0.1:20128", "dashboard-pw", 8000, "api-key")


def test_provision_raises_on_unexpected_listing_shape(monkeypatch):
    login = _fake_login_ok()

    def fake_urlopen(request, timeout):
        if request.full_url == LOGIN_URL:
            return login(request, timeout)
        return FakeResponse({"unexpected": "shape"})

    monkeypatch.setattr(omniroute_module.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(OmniRouteError):
        provision("http://127.0.0.1:20128", "dashboard-pw", 8000, "api-key")


def test_unexpected_listing_shape_error_does_not_leak_the_payload(monkeypatch):
    """The error text may reach stderr/the journal via scripts/start.sh -- it must
    never embed response contents (e.g. a leaked provider apiKey)."""
    login = _fake_login_ok()

    def fake_urlopen(request, timeout):
        if request.full_url == LOGIN_URL:
            return login(request, timeout)
        return FakeResponse({"providerApiKey": "super-secret-value", "other": "junk"})

    monkeypatch.setattr(omniroute_module.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(OmniRouteError) as excinfo:
        provision("http://127.0.0.1:20128", "dashboard-pw", 8000, "api-key")
    assert "super-secret-value" not in str(excinfo.value)


def test_provision_raises_when_existing_connection_has_no_id(monkeypatch):
    login = _fake_login_ok()

    def fake_urlopen(request, timeout):
        if request.full_url == LOGIN_URL:
            return login(request, timeout)
        assert request.get_method() == "GET"
        return FakeResponse({"connections": [{"name": CONNECTION_NAME}]})

    monkeypatch.setattr(omniroute_module.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(OmniRouteError):
        provision("http://127.0.0.1:20128", "dashboard-pw", 8000, "api-key")


def _id_token_with_email(email: str) -> str:
    """Minimal unsigned JWT whose payload carries email (header.payload.sig)."""
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').decode().rstrip("=")
    payload = base64.urlsafe_b64encode(
        json.dumps({"email": email}).encode()
    ).decode().rstrip("=")
    return f"{header}.{payload}.sig"


def test_codex_auth_email_reads_the_id_token_claim():
    assert (
        omniroute_module.codex_auth_email(
            {"tokens": {"id_token": _id_token_with_email("Casto@Example.com")}}
        )
        == "casto@example.com"
    )
    assert omniroute_module.codex_auth_email({"tokens": {}}) is None


def test_resolve_codex_import_name_matches_existing_connection_by_email():
    connections = [
        {"provider": "codex", "name": "cco-cl", "email": "castocolina.cl@gmail.com"},
        {"provider": "codex", "name": "cco-dev", "email": "castocolina.dev@gmail.com"},
    ]
    assert (
        omniroute_module.resolve_codex_import_name(
            connections, "castocolina.dev@gmail.com"
        )
        == "cco-dev"
    )
    assert (
        omniroute_module.resolve_codex_import_name(
            connections, "castocolina.cl@gmail.com"
        )
        == "cco-cl"
    )


def test_resolve_codex_import_name_refuses_shared_display_name():
    connections = [
        {"provider": "codex", "name": "cco-cl", "email": "castocolina.cl@gmail.com"},
        {"provider": "codex", "name": "cco-cl", "email": "castocolina.dev@gmail.com"},
    ]
    with pytest.raises(OmniRouteError, match="also belongs to"):
        omniroute_module.resolve_codex_import_name(
            connections, "castocolina.dev@gmail.com"
        )


def test_resolve_codex_import_name_refuses_to_reuse_name_owned_by_other_email():
    connections = [
        {"provider": "codex", "name": "cco-cl", "email": "castocolina.cl@gmail.com"},
    ]
    # Known alias for .dev is cco-dev (free); override cco-cl must be rejected.
    assert (
        omniroute_module.resolve_codex_import_name(
            connections, "castocolina.dev@gmail.com"
        )
        == "cco-dev"
    )
    with pytest.raises(OmniRouteError, match="already belongs to"):
        omniroute_module.resolve_codex_import_name(
            connections,
            "castocolina.dev@gmail.com",
            name_override="cco-cl",
        )


def test_import_codex_auth_updates_the_email_matched_connection(monkeypatch, tmp_path):
    login = _fake_login_ok()
    calls = []
    auth_path = tmp_path / "auth.json"
    auth_path.write_text(
        json.dumps(
            {
                "auth_mode": "chatgpt",
                "tokens": {"id_token": _id_token_with_email("castocolina.dev@gmail.com")},
            }
        )
    )

    def fake_urlopen(request, timeout):
        if request.full_url == LOGIN_URL:
            return login(request, timeout)
        calls.append((request.get_method(), request.full_url, request.data))
        if request.full_url.endswith("/api/providers") and request.get_method() == "GET":
            return FakeResponse(
                {
                    "connections": [
                        {
                            "id": "cl-id",
                            "provider": "codex",
                            "name": "cco-cl",
                            "email": "castocolina.cl@gmail.com",
                        },
                        {
                            "id": "dev-id",
                            "provider": "codex",
                            "name": "cco-dev",
                            "email": "castocolina.dev@gmail.com",
                        },
                    ]
                }
            )
        return FakeResponse(
            {"created": False, "connection": {"id": "dev-id", "name": "cco-dev"}}
        )

    monkeypatch.setattr(omniroute_module.urllib.request, "urlopen", fake_urlopen)
    result = import_codex_auth("http://127.0.0.1:20128", "dashboard-pw", auth_path)
    assert result == {
        "action": "updated",
        "id": "dev-id",
        "name": "cco-dev",
        "email": "castocolina.dev@gmail.com",
    }
    assert calls[0][0] == "GET"
    assert calls[0][1] == "http://127.0.0.1:20128/api/providers"
    assert calls[1][0] == "POST"
    assert calls[1][1] == "http://127.0.0.1:20128/api/providers/codex-auth/import"
    posted = json.loads(calls[1][2])
    assert posted["name"] == "cco-dev"
    assert posted["overwriteExisting"] is True


def test_import_codex_auth_creates_with_preferred_alias_when_no_email_match(
    monkeypatch, tmp_path
):
    login = _fake_login_ok()
    calls = []
    auth_path = tmp_path / "auth.json"
    auth_path.write_text(
        json.dumps(
            {"tokens": {"id_token": _id_token_with_email("castocolina.cl@gmail.com")}}
        )
    )

    def fake_urlopen(request, timeout):
        if request.full_url == LOGIN_URL:
            return login(request, timeout)
        calls.append((request.get_method(), request.full_url, request.data))
        if request.full_url.endswith("/api/providers") and request.get_method() == "GET":
            return FakeResponse({"connections": []})
        return FakeResponse(
            {"created": True, "connection": {"id": "new-id", "name": "cco-cl"}}
        )

    monkeypatch.setattr(omniroute_module.urllib.request, "urlopen", fake_urlopen)
    result = import_codex_auth("http://127.0.0.1:20128", "dashboard-pw", auth_path)
    assert result["action"] == "created"
    assert result["name"] == "cco-cl"
    assert result["email"] == "castocolina.cl@gmail.com"
    assert json.loads(calls[1][2])["name"] == "cco-cl"


def test_import_codex_auth_raises_when_auth_file_is_missing(tmp_path):
    with pytest.raises(OmniRouteError, match="no Codex session"):
        import_codex_auth(
            "http://127.0.0.1:20128", "dashboard-pw", tmp_path / "missing.json"
        )


def test_import_codex_auth_raises_when_auth_file_is_not_json(tmp_path):
    auth_path = tmp_path / "auth.json"
    auth_path.write_text("not json")
    with pytest.raises(OmniRouteError, match="not valid JSON"):
        import_codex_auth("http://127.0.0.1:20128", "dashboard-pw", auth_path)


def test_import_codex_auth_raises_when_id_token_has_no_email(tmp_path):
    auth_path = tmp_path / "auth.json"
    auth_path.write_text(json.dumps({"tokens": {"id_token": "not.a.jwt"}}))
    with pytest.raises(OmniRouteError, match="no ChatGPT email"):
        import_codex_auth("http://127.0.0.1:20128", "dashboard-pw", auth_path)


def test_import_codex_auth_surfaces_the_servers_error_detail(monkeypatch, tmp_path):
    """A session-expired failure (409 {"error": "..."}) should reach the caller
    as an actionable message, not just a bare status code."""
    login = _fake_login_ok()
    auth_path = tmp_path / "auth.json"
    auth_path.write_text(
        json.dumps(
            {"tokens": {"id_token": _id_token_with_email("castocolina.cl@gmail.com")}}
        )
    )
    body = json.dumps({"error": "Re-authenticate this account before exporting."}).encode()

    def fake_urlopen(request, timeout):
        if request.full_url == LOGIN_URL:
            return login(request, timeout)
        if request.full_url.endswith("/api/providers") and request.get_method() == "GET":
            return FakeResponse({"connections": []})
        raise urllib.error.HTTPError(
            request.full_url, 409, "Conflict", Message(), io.BytesIO(body)
        )

    monkeypatch.setattr(omniroute_module.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(OmniRouteError, match="Re-authenticate this account"):
        import_codex_auth("http://127.0.0.1:20128", "dashboard-pw", auth_path)


def _catalog_entry(model_id, root, owned_by="codex"):
    return {"id": model_id, "root": root, "owned_by": owned_by, "context_length": 272000}


def test_fix_codex_context_overrides_corrects_every_gpt_5_6_variant(monkeypatch):
    login = _fake_login_ok()
    catalog = {
        "object": "list",
        "data": [
            _catalog_entry("cx/gpt-5.6-sol-high", "gpt-5.6-sol-high"),
            # Duplicate root under the "codex/" alias prefix -- must be deduped,
            # not PUT twice.
            _catalog_entry("codex/gpt-5.6-sol-high", "gpt-5.6-sol-high"),
            _catalog_entry("cx/gpt-5.6-terra-low", "gpt-5.6-terra-low"),
            _catalog_entry("cx/gpt-5.6-luna", "gpt-5.6-luna"),
            # A real codex model outside the GPT-5.6 family -- must be left alone.
            _catalog_entry("cx/gpt-5.4", "gpt-5.4"),
            # A non-codex model -- must be ignored entirely.
            _catalog_entry("grok-cli/grok-4.6", "grok-4.6", owned_by="grok-cli"),
        ],
    }
    puts = []

    def fake_urlopen(request, timeout):
        if request.full_url == LOGIN_URL:
            return login(request, timeout)
        if request.full_url == "http://127.0.0.1:20128/v1/models":
            assert request.get_method() == "GET"
            return FakeResponse(catalog)
        assert request.get_method() == "PUT"
        assert request.full_url == "http://127.0.0.1:20128/api/provider-models"
        puts.append(json.loads(request.data))
        return FakeResponse({"ok": True})

    monkeypatch.setattr(omniroute_module.urllib.request, "urlopen", fake_urlopen)
    result = fix_codex_context_overrides("http://127.0.0.1:20128", "dashboard-pw")

    assert result == [
        {"model": "gpt-5.6-sol-high", "family": "gpt-5.6-sol", "context_window": 1_050_000},
        {"model": "gpt-5.6-terra-low", "family": "gpt-5.6-terra", "context_window": 1_050_000},
        {"model": "gpt-5.6-luna", "family": "gpt-5.6-luna", "context_window": 1_050_000},
    ]
    assert len(puts) == 3
    assert puts[0] == {
        "provider": "codex",
        "modelId": "gpt-5.6-sol-high",
        "contextWindowOverride": 1_050_000,
    }
    assert {p["modelId"] for p in puts} == {"gpt-5.6-sol-high", "gpt-5.6-terra-low", "gpt-5.6-luna"}


def test_fix_codex_context_overrides_is_a_noop_without_gpt_5_6_models(monkeypatch):
    login = _fake_login_ok()
    catalog = {"object": "list", "data": [_catalog_entry("cx/gpt-5.4", "gpt-5.4")]}

    def fake_urlopen(request, timeout):
        if request.full_url == LOGIN_URL:
            return login(request, timeout)
        assert request.full_url == "http://127.0.0.1:20128/v1/models"
        return FakeResponse(catalog)

    monkeypatch.setattr(omniroute_module.urllib.request, "urlopen", fake_urlopen)
    result = fix_codex_context_overrides("http://127.0.0.1:20128", "dashboard-pw")
    assert result == []


def test_fix_codex_context_overrides_raises_on_unexpected_catalog_shape(monkeypatch):
    login = _fake_login_ok()

    def fake_urlopen(request, timeout):
        if request.full_url == LOGIN_URL:
            return login(request, timeout)
        return FakeResponse({"unexpected": "shape"})

    monkeypatch.setattr(omniroute_module.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(OmniRouteError, match="unexpected /v1/models response shape"):
        fix_codex_context_overrides("http://127.0.0.1:20128", "dashboard-pw")


def _combo_member(provider_id, model):
    return {"kind": "model", "providerId": provider_id, "model": f"{provider_id}/{model}"}


def _combo_context_urlopen(login, catalog, combos_payload):
    def fake_urlopen(request, timeout):
        if request.full_url == LOGIN_URL:
            return login(request, timeout)
        if request.full_url == "http://127.0.0.1:20128/v1/models":
            assert request.get_method() == "GET"
            return FakeResponse(catalog)
        assert request.full_url == "http://127.0.0.1:20128/api/combos"
        assert request.get_method() == "GET"
        return FakeResponse(combos_payload)

    return fake_urlopen


def test_compute_combo_context_corrects_stale_and_hidden_models(monkeypatch):
    login = _fake_login_ok()
    catalog = {
        "object": "list",
        "data": [
            # Still stale in the catalog even though an override has been
            # applied -- OmniRoute's own /v1/models never reflects it.
            _catalog_entry("cx/gpt-5.6-sol-high", "gpt-5.6-sol-high"),
            {
                "id": "opencode-go/kimi-k2.7-code",
                "root": "kimi-k2.7-code",
                "owned_by": "opencode-go",
                "context_length": 262144,
                "max_output_tokens": 262144,
            },
            # grok-composer-2.5-fast is deliberately absent from the catalog
            # entirely -- must still be corrected via the known constant.
        ],
    }
    combos_payload = {
        "combos": [
            {
                "name": "my-coding",
                "models": [
                    _combo_member("codex", "gpt-5.6-sol-high"),
                    _combo_member("grok-cli", "grok-composer-2.5-fast"),
                    _combo_member("opencode-go", "kimi-k2.7-code"),
                ],
            }
        ]
    }

    monkeypatch.setattr(
        omniroute_module.urllib.request,
        "urlopen",
        _combo_context_urlopen(login, catalog, combos_payload),
    )
    result = compute_combo_context("http://127.0.0.1:20128", "dashboard-pw")

    assert result == [
        {
            "combo": "my-coding",
            "members": [
                {
                    "provider": "codex",
                    "model": "gpt-5.6-sol-high",
                    "context_window": 1_050_000,
                    "max_output_tokens": None,
                },
                {
                    "provider": "grok-cli",
                    "model": "grok-composer-2.5-fast",
                    "context_window": 200_000,
                    "max_output_tokens": None,
                },
                {
                    "provider": "opencode-go",
                    "model": "kimi-k2.7-code",
                    "context_window": 262144,
                    "max_output_tokens": 262144,
                },
            ],
            "min_context_window": 200_000,
            "min_max_output_tokens": 262144,
            "limiting_member": "grok-cli/grok-composer-2.5-fast",
        }
    ]


def test_compute_combo_context_leaves_unknown_models_out_of_the_minimum(monkeypatch):
    login = _fake_login_ok()
    catalog = {"object": "list", "data": []}
    combos_payload = {
        "combos": [
            {
                "name": "solo-mystery",
                "models": [_combo_member("mystery-provider", "mystery-model")],
            }
        ]
    }

    monkeypatch.setattr(
        omniroute_module.urllib.request,
        "urlopen",
        _combo_context_urlopen(login, catalog, combos_payload),
    )
    result = compute_combo_context("http://127.0.0.1:20128", "dashboard-pw")

    assert result == [
        {
            "combo": "solo-mystery",
            "members": [
                {
                    "provider": "mystery-provider",
                    "model": "mystery-model",
                    "context_window": None,
                    "max_output_tokens": None,
                }
            ],
            "min_context_window": None,
            "min_max_output_tokens": None,
            "limiting_member": None,
        }
    ]


def test_compute_combo_context_limiting_member_resolves_ties_to_first_occurrence(
    monkeypatch,
):
    login = _fake_login_ok()
    catalog = {
        "object": "list",
        "data": [
            _catalog_entry("grok-cli/grok-4.6", "grok-4.6"),
            _catalog_entry("opencode-go/kimi-k2.7-code", "kimi-k2.7-code"),
        ],
    }
    combos_payload = {
        "combos": [
            {
                "name": "tied-floor",
                "models": [
                    _combo_member("opencode-go", "kimi-k2.7-code"),
                    _combo_member("grok-cli", "grok-4.6"),
                ],
            }
        ]
    }
    catalog["data"][0]["context_length"] = 131072
    catalog["data"][1]["context_length"] = 131072

    monkeypatch.setattr(
        omniroute_module.urllib.request,
        "urlopen",
        _combo_context_urlopen(login, catalog, combos_payload),
    )
    result = compute_combo_context("http://127.0.0.1:20128", "dashboard-pw")

    assert result[0]["limiting_member"] == "opencode-go/kimi-k2.7-code"


def test_compute_combo_context_filters_by_combo_name(monkeypatch):
    login = _fake_login_ok()
    catalog = {"object": "list", "data": []}
    combos_payload = {
        "combos": [
            {"name": "combo-a", "models": [_combo_member("grok-cli", "grok-4.6")]},
            {"name": "combo-b", "models": [_combo_member("grok-cli", "grok-4.6")]},
        ]
    }

    monkeypatch.setattr(
        omniroute_module.urllib.request,
        "urlopen",
        _combo_context_urlopen(login, catalog, combos_payload),
    )
    result = compute_combo_context("http://127.0.0.1:20128", "dashboard-pw", combo_name="combo-b")

    assert [combo["combo"] for combo in result] == ["combo-b"]


def test_compute_combo_context_raises_when_combo_name_not_found(monkeypatch):
    login = _fake_login_ok()
    catalog = {"object": "list", "data": []}
    combos_payload = {"combos": [{"name": "combo-a", "models": []}]}

    monkeypatch.setattr(
        omniroute_module.urllib.request,
        "urlopen",
        _combo_context_urlopen(login, catalog, combos_payload),
    )
    with pytest.raises(OmniRouteError, match="no combo named 'nope' found"):
        compute_combo_context("http://127.0.0.1:20128", "dashboard-pw", combo_name="nope")


def test_compute_combo_context_raises_on_unexpected_combos_shape(monkeypatch):
    login = _fake_login_ok()
    catalog = {"object": "list", "data": []}

    monkeypatch.setattr(
        omniroute_module.urllib.request,
        "urlopen",
        _combo_context_urlopen(login, catalog, {"unexpected": "shape"}),
    )
    with pytest.raises(OmniRouteError, match="unexpected /api/combos response shape"):
        compute_combo_context("http://127.0.0.1:20128", "dashboard-pw")
