import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import json

import pylib.omniroute_client as client_module
from pylib.omniroute_client import OmniRouteError
from pylib.omniroute_combos import backup_combos, find_combo, restore_combos
from tests._omniroute_test_support import LOGIN_URL, FakeResponse, fake_login_ok


def test_find_combo_matches_by_name():
    combos = [{"id": "1", "name": "other"}, {"id": "2", "name": "my-coding"}]
    assert find_combo(combos, "my-coding")["id"] == "2"


def test_find_combo_returns_none_when_absent():
    assert find_combo([{"id": "1", "name": "other"}], "my-coding") is None


LIVE_COMBOS = {
    "combos": [
        {
            "id": "combo-1",
            "name": "my-planning",
            "strategy": "priority",
            "models": [
                {"connectionId": "conn-a", "model": "kimi-k3"},
                {"connectionId": "conn-b", "model": "grok-4.6"},
            ],
        },
        {
            "id": "combo-2",
            "name": "my-coding",
            "strategy": "priority",
            "models": [{"connectionId": "conn-c", "model": "gpt-5.6-terra-high"}],
        },
    ]
}


def test_backup_combos_captures_name_models_and_strategy_without_ids(monkeypatch):
    login = fake_login_ok()

    def fake_urlopen(request, timeout):
        if request.full_url == LOGIN_URL:
            return login(request, timeout)
        assert request.full_url == "http://127.0.0.1:20128/api/combos"
        assert request.get_method() == "GET"
        return FakeResponse(LIVE_COMBOS)

    monkeypatch.setattr(client_module.urllib.request, "urlopen", fake_urlopen)
    snapshot = backup_combos("http://127.0.0.1:20128", "dashboard-pw")

    assert snapshot == [
        {
            "name": "my-planning",
            "models": [
                {"connectionId": "conn-a", "model": "kimi-k3"},
                {"connectionId": "conn-b", "model": "grok-4.6"},
            ],
            "strategy": "priority",
        },
        {
            "name": "my-coding",
            "models": [{"connectionId": "conn-c", "model": "gpt-5.6-terra-high"}],
            "strategy": "priority",
        },
    ]
    assert "id" not in snapshot[0]


def test_backup_combos_raises_on_unexpected_listing_shape(monkeypatch):
    login = fake_login_ok()

    def fake_urlopen(request, timeout):
        if request.full_url == LOGIN_URL:
            return login(request, timeout)
        return FakeResponse({"unexpected": "shape"})

    monkeypatch.setattr(client_module.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(OmniRouteError, match="unexpected combo listing shape"):
        backup_combos("http://127.0.0.1:20128", "dashboard-pw")


SNAPSHOT = [
    {"name": "my-planning", "models": [{"connectionId": "conn-a", "model": "kimi-k3"}]},
    {"name": "my-new-combo", "models": [{"connectionId": "conn-z", "model": "grok-4.6"}]},
]


def test_restore_combos_updates_an_existing_combo_by_name(monkeypatch):
    login = fake_login_ok()
    puts = []

    def fake_urlopen(request, timeout):
        if request.full_url == LOGIN_URL:
            return login(request, timeout)
        if request.full_url == "http://127.0.0.1:20128/api/combos" and request.get_method() == "GET":
            return FakeResponse({"combos": [{"id": "combo-1", "name": "my-planning"}]})
        if request.full_url == "http://127.0.0.1:20128/api/combos" and request.get_method() == "POST":
            body = json.loads(request.data)
            return FakeResponse({"id": "new-combo-id", "name": body["name"]})
        assert request.get_method() == "PUT"
        puts.append((request.full_url, json.loads(request.data)))
        return FakeResponse({"ok": True})

    monkeypatch.setattr(client_module.urllib.request, "urlopen", fake_urlopen)
    results = restore_combos("http://127.0.0.1:20128", "dashboard-pw", SNAPSHOT)

    assert results == [
        {"combo": "my-planning", "action": "updated", "id": "combo-1", "models": 1},
        {"combo": "my-new-combo", "action": "created", "id": "new-combo-id", "models": 1},
    ]
    assert (
        "http://127.0.0.1:20128/api/combos/combo-1",
        {"models": [{"connectionId": "conn-a", "model": "kimi-k3"}]},
    ) in puts
    assert (
        "http://127.0.0.1:20128/api/combos/new-combo-id",
        {"models": [{"connectionId": "conn-z", "model": "grok-4.6"}]},
    ) in puts


def test_restore_combos_raises_on_entry_missing_a_name(monkeypatch):
    login = fake_login_ok()

    def fake_urlopen(request, timeout):
        if request.full_url == LOGIN_URL:
            return login(request, timeout)
        return FakeResponse({"combos": []})

    monkeypatch.setattr(client_module.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(OmniRouteError, match="missing a combo name"):
        restore_combos(
            "http://127.0.0.1:20128", "dashboard-pw", [{"models": []}]
        )


def test_restore_combos_raises_when_creation_returns_no_usable_id(monkeypatch):
    login = fake_login_ok()

    def fake_urlopen(request, timeout):
        if request.full_url == LOGIN_URL:
            return login(request, timeout)
        if request.get_method() == "GET":
            return FakeResponse({"combos": []})
        assert request.get_method() == "POST"
        return FakeResponse({"name": "my-new-combo"})  # no "id"

    monkeypatch.setattr(client_module.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(OmniRouteError, match="no usable id"):
        restore_combos("http://127.0.0.1:20128", "dashboard-pw", SNAPSHOT[1:])
