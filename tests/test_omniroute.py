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
    find_connection,
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
    assert find_connection(connections)["id"] == "2"


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
        raise urllib.error.HTTPError(request.full_url, 401, "boom", None, None)

    monkeypatch.setattr(omniroute_module.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(OmniRouteError):
        provision("http://127.0.0.1:20128", "wrong-pw", 8000, "api-key")


def test_provision_raises_omniroute_error_on_http_failure(monkeypatch):
    login = _fake_login_ok()

    def fake_urlopen(request, timeout):
        if request.full_url == LOGIN_URL:
            return login(request, timeout)
        raise urllib.error.HTTPError(request.full_url, 500, "boom", None, None)

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
