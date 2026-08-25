import json
import sys
from email.message import Message
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pylib.omniroute as omniroute_module
from pylib.omniroute import OmniRouteError
from pylib.omniroute_combos import backup_combos, restore_combos

LOGIN_URL = "http://127.0.0.1:20128/api/auth/login"
COMBOS_URL = "http://127.0.0.1:20128/api/combos"


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


def _combo(name, **extra):
    return {
        "name": name,
        "id": f"id-{name}",
        "strategy": "priority",
        "models": [{"kind": "model", "providerId": "grok-cli", "model": "grok-cli/grok-4.6"}],
        "isHidden": False,
        "sortOrder": 0,
        "createdAt": "2026-01-01T00:00:00Z",
        "updatedAt": "2026-01-01T00:00:00Z",
        "version": 2,
        "computed_context_length": 500000,
        **extra,
    }


def test_backup_combos_writes_only_restorable_fields(tmp_path, monkeypatch):
    login = _fake_login_ok()
    combos_payload = {"combos": [_combo("my-planning"), _combo("my-coding")], "total": 2}

    def fake_urlopen(request, timeout):
        if request.full_url == LOGIN_URL:
            return login(request, timeout)
        assert request.full_url == COMBOS_URL
        assert request.get_method() == "GET"
        return FakeResponse(combos_payload)

    monkeypatch.setattr(omniroute_module.urllib.request, "urlopen", fake_urlopen)

    output_path = tmp_path / "backup.json"
    result = backup_combos("http://127.0.0.1:20128", "dashboard-pw", output_path)

    assert result == {"path": str(output_path), "count": 2}
    written = json.loads(output_path.read_text())
    assert "backed_up_at" in written
    assert [combo["name"] for combo in written["combos"]] == ["my-planning", "my-coding"]
    for combo in written["combos"]:
        assert "id" not in combo
        assert "isHidden" not in combo
        assert "computed_context_length" not in combo
        assert combo["strategy"] == "priority"
        assert combo["models"] == [
            {"kind": "model", "providerId": "grok-cli", "model": "grok-cli/grok-4.6"}
        ]


def test_backup_combos_raises_on_unexpected_shape(tmp_path, monkeypatch):
    login = _fake_login_ok()

    def fake_urlopen(request, timeout):
        if request.full_url == LOGIN_URL:
            return login(request, timeout)
        return FakeResponse({"unexpected": "shape"})

    monkeypatch.setattr(omniroute_module.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(OmniRouteError, match="unexpected /api/combos response shape"):
        backup_combos("http://127.0.0.1:20128", "dashboard-pw", tmp_path / "out.json")


def _write_backup(tmp_path, combos):
    path = tmp_path / "backup.json"
    path.write_text(json.dumps({"backed_up_at": "2026-01-01T00:00:00Z", "combos": combos}))
    return path


def test_restore_combos_creates_missing_combos(tmp_path, monkeypatch):
    login = _fake_login_ok()
    existing_payload = {"combos": [], "total": 0}
    posts = []

    def fake_urlopen(request, timeout):
        if request.full_url == LOGIN_URL:
            return login(request, timeout)
        if request.get_method() == "GET":
            assert request.full_url == COMBOS_URL
            return FakeResponse(existing_payload)
        assert request.get_method() == "POST"
        assert request.full_url == COMBOS_URL
        posts.append(json.loads(request.data))
        return FakeResponse({"name": "my-planning"}, )

    monkeypatch.setattr(omniroute_module.urllib.request, "urlopen", fake_urlopen)

    input_path = _write_backup(tmp_path, [{"name": "my-planning", "strategy": "priority"}])
    result = restore_combos("http://127.0.0.1:20128", "dashboard-pw", input_path)

    assert result == [{"combo": "my-planning", "action": "created"}]
    assert posts == [{"name": "my-planning", "strategy": "priority"}]


def test_restore_combos_skips_existing_combos_without_overwrite(tmp_path, monkeypatch):
    login = _fake_login_ok()
    existing_payload = {"combos": [_combo("my-planning")], "total": 1}

    def fake_urlopen(request, timeout):
        if request.full_url == LOGIN_URL:
            return login(request, timeout)
        assert request.get_method() == "GET"
        return FakeResponse(existing_payload)

    monkeypatch.setattr(omniroute_module.urllib.request, "urlopen", fake_urlopen)

    input_path = _write_backup(tmp_path, [{"name": "my-planning", "strategy": "priority"}])
    result = restore_combos("http://127.0.0.1:20128", "dashboard-pw", input_path)

    assert result == [{"combo": "my-planning", "action": "skipped"}]


def test_restore_combos_updates_existing_combos_with_overwrite(tmp_path, monkeypatch):
    login = _fake_login_ok()
    existing_payload = {"combos": [_combo("my-planning")], "total": 1}
    puts = []

    def fake_urlopen(request, timeout):
        if request.full_url == LOGIN_URL:
            return login(request, timeout)
        if request.get_method() == "GET":
            return FakeResponse(existing_payload)
        assert request.get_method() == "PUT"
        assert request.full_url == f"{COMBOS_URL}/id-my-planning"
        puts.append(json.loads(request.data))
        return FakeResponse({"name": "my-planning"})

    monkeypatch.setattr(omniroute_module.urllib.request, "urlopen", fake_urlopen)

    input_path = _write_backup(
        tmp_path, [{"name": "my-planning", "strategy": "priority", "description": "updated"}]
    )
    result = restore_combos(
        "http://127.0.0.1:20128", "dashboard-pw", input_path, overwrite=True
    )

    assert result == [{"combo": "my-planning", "action": "updated"}]
    assert puts == [{"name": "my-planning", "strategy": "priority", "description": "updated"}]


def test_restore_combos_raises_on_unexpected_backup_shape(tmp_path, monkeypatch):
    login = _fake_login_ok()
    monkeypatch.setattr(
        omniroute_module.urllib.request,
        "urlopen",
        lambda request, timeout: login(request, timeout),
    )

    input_path = tmp_path / "backup.json"
    input_path.write_text(json.dumps({"unexpected": "shape"}))
    with pytest.raises(OmniRouteError, match="unexpected backup file shape"):
        restore_combos("http://127.0.0.1:20128", "dashboard-pw", input_path)


def test_restore_combos_raises_on_unexpected_existing_combos_shape(tmp_path, monkeypatch):
    login = _fake_login_ok()

    def fake_urlopen(request, timeout):
        if request.full_url == LOGIN_URL:
            return login(request, timeout)
        return FakeResponse({"unexpected": "shape"})

    monkeypatch.setattr(omniroute_module.urllib.request, "urlopen", fake_urlopen)

    input_path = _write_backup(tmp_path, [{"name": "my-planning"}])
    with pytest.raises(OmniRouteError, match="unexpected /api/combos response shape"):
        restore_combos("http://127.0.0.1:20128", "dashboard-pw", input_path)
