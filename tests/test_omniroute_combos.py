import json
import re
import sys
from email.message import Message
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pylib.omniroute as omniroute_module
import pylib.omniroute_combos as combos_module
from pylib.omniroute import OmniRouteError
from pylib.omniroute_combos import (
    _combo_payload,
    _remap_combo_connection_ids,
    backup_combos,
    backup_connections,
    ensure_local_combo,
    local_combo_payload,
    restore_combos,
)

LOGIN_URL = "http://127.0.0.1:20128/api/auth/login"
COMBOS_URL = "http://127.0.0.1:20128/api/combos"
PROVIDERS_URL = "http://127.0.0.1:20128/api/providers"


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
        if request.full_url == PROVIDERS_URL:
            return FakeResponse({"connections": []})
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
        if request.full_url == PROVIDERS_URL:
            return FakeResponse({"connections": []})
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
        if request.full_url == PROVIDERS_URL:
            return FakeResponse({"connections": []})
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


def test_restore_combos_raises_on_malformed_json(tmp_path):
    input_path = tmp_path / "corrupt.json"
    input_path.write_text("{broken json")
    with pytest.raises(OmniRouteError, match=re.escape(f"{input_path} is not valid JSON")):
        restore_combos("http://127.0.0.1:20128", "dashboard-pw", input_path)


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


def test_backup_connections_writes_only_metadata_no_secrets(tmp_path, monkeypatch):
    login = _fake_login_ok()
    connections_payload = {
        "connections": [
            {
                "id": "conn-1",
                "provider": "codex",
                "name": "cco-cl",
                "authType": "oauth",
            },
            {
                "id": "conn-2",
                "provider": "opencode-go",
                "name": "og-main",
                "authType": "apikey",
                "apiKey": "sk-q4X8A****0sM8",
            },
        ]
    }

    def fake_urlopen(request, timeout):
        if request.full_url == LOGIN_URL:
            return login(request, timeout)
        assert request.full_url == PROVIDERS_URL
        assert request.get_method() == "GET"
        return FakeResponse(connections_payload)

    monkeypatch.setattr(omniroute_module.urllib.request, "urlopen", fake_urlopen)

    output_path = tmp_path / "connections-backup.json"
    result = backup_connections("http://127.0.0.1:20128", "dashboard-pw", output_path)

    assert result == {"path": str(output_path), "count": 2}
    written = json.loads(output_path.read_text())
    assert "backed_up_at" in written
    assert written["connections"] == [
        {"provider": "codex", "name": "cco-cl", "id": "conn-1"},
        {"provider": "opencode-go", "name": "og-main", "id": "conn-2"},
    ]
    for connection in written["connections"]:
        assert "apiKey" not in connection
        assert "authType" not in connection


def test_backup_connections_raises_on_unexpected_shape(tmp_path, monkeypatch):
    login = _fake_login_ok()

    def fake_urlopen(request, timeout):
        if request.full_url == LOGIN_URL:
            return login(request, timeout)
        return FakeResponse({"unexpected": "shape"})

    monkeypatch.setattr(omniroute_module.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(OmniRouteError, match="unexpected provider listing shape"):
        backup_connections("http://127.0.0.1:20128", "dashboard-pw", tmp_path / "out.json")


def test_restore_combos_remaps_connection_id_by_provider_and_label(tmp_path, monkeypatch):
    login = _fake_login_ok()
    existing_payload = {"combos": [], "total": 0}
    connections_payload = {
        "connections": [
            {"id": "conn-live", "provider": "codex", "name": "cco-cl"},
        ]
    }
    posts = []

    def fake_urlopen(request, timeout):
        if request.full_url == LOGIN_URL:
            return login(request, timeout)
        if request.full_url == PROVIDERS_URL:
            return FakeResponse(connections_payload)
        if request.get_method() == "GET":
            return FakeResponse(existing_payload)
        assert request.get_method() == "POST"
        posts.append(json.loads(request.data))
        return FakeResponse({"name": "my-planning"})

    monkeypatch.setattr(omniroute_module.urllib.request, "urlopen", fake_urlopen)

    backup_combo = {
        "name": "my-planning",
        "models": [
            {
                "kind": "model",
                "providerId": "codex",
                "model": "codex/gpt-5.6-sol-high",
                "label": "cco-cl",
                "connectionId": "conn-stale",
            }
        ],
    }
    input_path = _write_backup(tmp_path, [backup_combo])
    result = restore_combos("http://127.0.0.1:20128", "dashboard-pw", input_path)

    assert result == [{"combo": "my-planning", "action": "created"}]
    assert posts[0]["models"][0]["connectionId"] == "conn-live"


def test_restore_combos_clears_and_reports_unresolved_connection_id(tmp_path, monkeypatch):
    login = _fake_login_ok()
    existing_payload = {"combos": [], "total": 0}
    posts = []

    def fake_urlopen(request, timeout):
        if request.full_url == LOGIN_URL:
            return login(request, timeout)
        if request.full_url == PROVIDERS_URL:
            return FakeResponse({"connections": []})
        if request.get_method() == "GET":
            return FakeResponse(existing_payload)
        assert request.get_method() == "POST"
        posts.append(json.loads(request.data))
        return FakeResponse({"name": "my-planning"})

    monkeypatch.setattr(omniroute_module.urllib.request, "urlopen", fake_urlopen)

    backup_combo = {
        "name": "my-planning",
        "models": [
            {
                "kind": "model",
                "providerId": "codex",
                "model": "codex/gpt-5.6-sol-high",
                "label": "cco-cl",
                "connectionId": "conn-stale",
            }
        ],
    }
    input_path = _write_backup(tmp_path, [backup_combo])
    result = restore_combos("http://127.0.0.1:20128", "dashboard-pw", input_path)

    assert result == [
        {
            "combo": "my-planning",
            "action": "created",
            "unresolved_connections": [
                {"provider": "codex", "label": "cco-cl", "model": "codex/gpt-5.6-sol-high"}
            ],
        }
    ]
    assert posts[0]["models"][0]["connectionId"] is None


LOCAL_PROVIDERS = {"connections": [{"provider": "llama-cpp", "name": "llm-env-local", "id": "conn-1"}]}


def _local_combo_server(monkeypatch, *, providers=LOCAL_PROVIDERS, combos=()):
    """Fake OmniRoute; returns the list of (method, path, body) combo writes and the combo-URL call log."""
    login = _fake_login_ok()
    calls = []

    def fake_urlopen(request, timeout):
        if request.full_url == LOGIN_URL:
            return login(request, timeout)
        path = request.full_url.removeprefix("http://127.0.0.1:20128")
        body = json.loads(request.data) if request.data else None
        calls.append((request.get_method(), path, body))
        if path == "/api/providers":
            return FakeResponse(providers)
        if request.get_method() == "GET":
            return FakeResponse({"combos": list(combos), "total": len(combos)})
        return FakeResponse({"name": "my-coding-lite"})

    monkeypatch.setattr(omniroute_module.urllib.request, "urlopen", fake_urlopen)
    return calls


def _local_combo(payload, **extra):
    return _combo("my-coding-lite", **{k: v for k, v in payload.items() if k != "name"}, **extra)


def _writes(calls):
    return [c for c in calls if c[0] in ("POST", "PUT")]


def _ensure(alias="qwen", context_length=131072):
    return ensure_local_combo("http://127.0.0.1:20128", "dashboard-pw", alias, context_length)


def test_ensure_local_combo_creates_a_local_only_priority_combo(monkeypatch):
    calls = _local_combo_server(monkeypatch)
    result = _ensure()
    assert _writes(calls) == [("POST", "/api/combos", local_combo_payload("qwen", "conn-1", 131072))]
    assert result["action"] == "created"
    assert result["context_length"] == 131072 and result["model"] == "llama-cpp/qwen"


def test_ensure_local_combo_is_unchanged_when_it_already_matches(monkeypatch):
    desired = local_combo_payload("qwen", "conn-1", 131072)
    desired["models"][0]["id"] = "another-member-id"
    calls = _local_combo_server(monkeypatch, combos=[_local_combo(desired)])
    assert _ensure()["action"] == "unchanged"
    assert _writes(calls) == []


def test_ensure_local_combo_unchanged_check_ignores_fields_the_payload_omits(monkeypatch):
    payload = local_combo_payload("qwen", "conn-1", 131072)
    del payload["context_length"]
    monkeypatch.setattr(combos_module, "local_combo_payload", lambda *a: payload)
    existing = _local_combo(payload, config={"added": "by omniroute"})
    calls = _local_combo_server(monkeypatch, combos=[existing])
    assert _ensure()["action"] == "unchanged"
    assert _writes(calls) == []


def test_ensure_local_combo_updates_drifted_context_or_members(monkeypatch):
    drifted = local_combo_payload("qwen", "conn-1", 262144)
    drifted["models"].append({"kind": "model", "providerId": "grok-cli", "model": "grok-cli/grok-4.6"})
    calls = _local_combo_server(monkeypatch, combos=[_local_combo(drifted)])
    assert _ensure()["action"] == "updated"
    assert _writes(calls) == [
        ("PUT", "/api/combos/id-my-coding-lite", local_combo_payload("qwen", "conn-1", 131072))
    ]


def test_ensure_local_combo_skips_without_the_llm_env_local_connection(monkeypatch):
    calls = _local_combo_server(monkeypatch, providers={"connections": []})
    result = _ensure()
    assert result["action"] == "skipped" and "make start-llama" in result["reason"]
    assert not [c for c in calls if c[1].startswith("/api/combos")]


def test_local_combo_payload_survives_backup_and_restore_remap():
    payload = local_combo_payload("qwen", "conn-1", 131072)
    assert _combo_payload(payload) == payload
    remapped, unresolved = _remap_combo_connection_ids(payload, {("llama-cpp", "llm-env-local"): "conn-new"})
    assert remapped["models"][0]["connectionId"] == "conn-new" and unresolved == []
