import sys
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import json

import pylib.omniroute_client as client_module
from pylib.omniroute_client import OmniRouteError
from pylib.omniroute_provision import (
    CONNECTION_NAME,
    build_payload,
    find_connection,
    provision,
)
from tests._omniroute_test_support import LOGIN_URL, FakeResponse, fake_login_ok


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
    assert find_connection(connections)["id"] == "2"


def test_find_connection_returns_none_when_absent():
    assert find_connection([{"id": "1", "name": "other"}]) is None


def test_provision_creates_when_no_existing_connection(monkeypatch):
    calls = []
    login = fake_login_ok()

    def fake_urlopen(request, timeout):
        if request.full_url == LOGIN_URL:
            return login(request, timeout)
        calls.append((request.get_method(), request.full_url, request.data))
        if request.get_method() == "GET":
            return FakeResponse({"connections": []})
        return FakeResponse({"connection": {"id": "new-id"}})

    monkeypatch.setattr(client_module.urllib.request, "urlopen", fake_urlopen)
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
    login = fake_login_ok()

    def fake_urlopen(request, timeout):
        if request.full_url == LOGIN_URL:
            return login(request, timeout)
        if request.get_method() == "GET":
            return FakeResponse({"connections": [{"id": "existing-id", "name": CONNECTION_NAME}]})
        assert request.get_method() == "PUT"
        assert request.full_url == "http://127.0.0.1:20128/api/providers/existing-id"
        return FakeResponse({"connection": {"id": "existing-id"}})

    monkeypatch.setattr(client_module.urllib.request, "urlopen", fake_urlopen)
    result = provision("http://127.0.0.1:20128", "dashboard-pw", 8000, "api-key")
    assert result == {"action": "updated", "id": "existing-id"}


def test_provision_accepts_a_providers_wrapped_listing(monkeypatch):
    login = fake_login_ok()

    def fake_urlopen(request, timeout):
        if request.full_url == LOGIN_URL:
            return login(request, timeout)
        if request.get_method() == "GET":
            return FakeResponse({"providers": [{"id": "existing-id", "name": CONNECTION_NAME}]})
        return FakeResponse({"connection": {"id": "existing-id"}})

    monkeypatch.setattr(client_module.urllib.request, "urlopen", fake_urlopen)
    result = provision("http://127.0.0.1:20128", "dashboard-pw", 8000, "api-key")
    assert result == {"action": "updated", "id": "existing-id"}


def test_provision_sends_the_session_cookie(monkeypatch):
    seen_cookies = []
    login = fake_login_ok(set_cookie="auth_token=secret-session; Path=/; HttpOnly")

    def fake_urlopen(request, timeout):
        if request.full_url == LOGIN_URL:
            return login(request, timeout)
        seen_cookies.append(request.get_header("Cookie"))
        if request.get_method() == "GET":
            return FakeResponse({"connections": []})
        return FakeResponse({"connection": {"id": "x"}})

    monkeypatch.setattr(client_module.urllib.request, "urlopen", fake_urlopen)
    provision("http://127.0.0.1:20128", "dashboard-pw", 8000, "api-key")
    assert all(cookie == "auth_token=secret-session" for cookie in seen_cookies)


def test_provision_raises_when_login_returns_no_session_cookie(monkeypatch):
    def fake_urlopen(request, timeout):
        assert request.full_url == LOGIN_URL
        return FakeResponse({"success": True})  # no Set-Cookie

    monkeypatch.setattr(client_module.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(OmniRouteError, match="no session cookie"):
        provision("http://127.0.0.1:20128", "dashboard-pw", 8000, "api-key")


def test_provision_raises_omniroute_error_on_login_http_failure(monkeypatch):
    def fake_urlopen(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 401, "boom", None, None)

    monkeypatch.setattr(client_module.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(OmniRouteError):
        provision("http://127.0.0.1:20128", "wrong-pw", 8000, "api-key")


def test_provision_raises_omniroute_error_on_http_failure(monkeypatch):
    login = fake_login_ok()

    def fake_urlopen(request, timeout):
        if request.full_url == LOGIN_URL:
            return login(request, timeout)
        raise urllib.error.HTTPError(request.full_url, 500, "boom", None, None)

    monkeypatch.setattr(client_module.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(OmniRouteError):
        provision("http://127.0.0.1:20128", "dashboard-pw", 8000, "api-key")


def test_provision_raises_on_unexpected_listing_shape(monkeypatch):
    login = fake_login_ok()

    def fake_urlopen(request, timeout):
        if request.full_url == LOGIN_URL:
            return login(request, timeout)
        return FakeResponse({"unexpected": "shape"})

    monkeypatch.setattr(client_module.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(OmniRouteError):
        provision("http://127.0.0.1:20128", "dashboard-pw", 8000, "api-key")


def test_unexpected_listing_shape_error_does_not_leak_the_payload(monkeypatch):
    """The error text may reach stderr/the journal via scripts/start.sh -- it must
    never embed response contents (e.g. a leaked provider apiKey)."""
    login = fake_login_ok()

    def fake_urlopen(request, timeout):
        if request.full_url == LOGIN_URL:
            return login(request, timeout)
        return FakeResponse({"providerApiKey": "super-secret-value", "other": "junk"})

    monkeypatch.setattr(client_module.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(OmniRouteError) as excinfo:
        provision("http://127.0.0.1:20128", "dashboard-pw", 8000, "api-key")
    assert "super-secret-value" not in str(excinfo.value)


def test_provision_raises_when_existing_connection_has_no_id(monkeypatch):
    login = fake_login_ok()

    def fake_urlopen(request, timeout):
        if request.full_url == LOGIN_URL:
            return login(request, timeout)
        assert request.get_method() == "GET"
        return FakeResponse({"connections": [{"name": CONNECTION_NAME}]})

    monkeypatch.setattr(client_module.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(OmniRouteError):
        provision("http://127.0.0.1:20128", "dashboard-pw", 8000, "api-key")
