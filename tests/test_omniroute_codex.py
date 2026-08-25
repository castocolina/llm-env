import io
import json
import sys
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pylib.omniroute_client as client_module
from pylib.omniroute_client import OmniRouteError
from pylib.omniroute_codex import import_codex_auth
from tests._omniroute_test_support import LOGIN_URL, FakeResponse, fake_login_ok


def test_import_codex_auth_creates_a_named_connection(monkeypatch, tmp_path):
    login = fake_login_ok()
    calls = []
    auth_path = tmp_path / "auth.json"
    auth_path.write_text(json.dumps({"auth_mode": "chatgpt", "tokens": {"id_token": "x"}}))

    def fake_urlopen(request, timeout):
        if request.full_url == LOGIN_URL:
            return login(request, timeout)
        calls.append((request.get_method(), request.full_url, request.data))
        return FakeResponse({"created": True, "connection": {"id": "new-id", "name": "cco-cl"}})

    monkeypatch.setattr(client_module.urllib.request, "urlopen", fake_urlopen)
    result = import_codex_auth(
        "http://127.0.0.1:20128", "dashboard-pw", auth_path, name="cco-cl"
    )
    assert result == {"action": "created", "id": "new-id", "name": "cco-cl"}
    assert calls[0][0] == "POST"
    assert calls[0][1] == "http://127.0.0.1:20128/api/providers/codex-auth/import"
    posted = json.loads(calls[0][2])
    assert posted["name"] == "cco-cl"
    assert posted["overwriteExisting"] is True
    assert posted["source"] == {
        "kind": "json",
        "json": {"auth_mode": "chatgpt", "tokens": {"id_token": "x"}},
    }


def test_import_codex_auth_reports_update_on_rerun(monkeypatch, tmp_path):
    login = fake_login_ok()
    auth_path = tmp_path / "auth.json"
    auth_path.write_text(json.dumps({"tokens": {}}))

    def fake_urlopen(request, timeout):
        if request.full_url == LOGIN_URL:
            return login(request, timeout)
        return FakeResponse({"created": False, "connection": {"id": "same-id", "name": "cco-cl"}})

    monkeypatch.setattr(client_module.urllib.request, "urlopen", fake_urlopen)
    result = import_codex_auth(
        "http://127.0.0.1:20128", "dashboard-pw", auth_path, name="cco-cl"
    )
    assert result == {"action": "updated", "id": "same-id", "name": "cco-cl"}


def test_import_codex_auth_raises_when_auth_file_is_missing(tmp_path):
    with pytest.raises(OmniRouteError, match="no Codex session"):
        import_codex_auth(
            "http://127.0.0.1:20128", "dashboard-pw", tmp_path / "missing.json", name="cco-cl"
        )


def test_import_codex_auth_raises_when_auth_file_is_not_json(tmp_path):
    auth_path = tmp_path / "auth.json"
    auth_path.write_text("not json")
    with pytest.raises(OmniRouteError, match="not valid JSON"):
        import_codex_auth("http://127.0.0.1:20128", "dashboard-pw", auth_path, name="cco-cl")


def test_import_codex_auth_surfaces_the_servers_error_detail(monkeypatch, tmp_path):
    """A session-expired failure (409 {"error": "..."}) should reach the caller
    as an actionable message, not just a bare status code."""
    login = fake_login_ok()
    auth_path = tmp_path / "auth.json"
    auth_path.write_text(json.dumps({"tokens": {}}))
    body = json.dumps({"error": "Re-authenticate this account before exporting."}).encode()

    def fake_urlopen(request, timeout):
        if request.full_url == LOGIN_URL:
            return login(request, timeout)
        raise urllib.error.HTTPError(
            request.full_url, 409, "Conflict", None, io.BytesIO(body)
        )

    monkeypatch.setattr(client_module.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(OmniRouteError, match="Re-authenticate this account"):
        import_codex_auth("http://127.0.0.1:20128", "dashboard-pw", auth_path, name="cco-cl")
