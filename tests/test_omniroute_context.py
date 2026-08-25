import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import json

import pylib.omniroute_client as client_module
from pylib.omniroute_client import OmniRouteError
from pylib.omniroute_context import fix_codex_context_overrides
from tests._omniroute_test_support import LOGIN_URL, FakeResponse, fake_login_ok


def _catalog_entry(model_id, root, owned_by="codex"):
    return {"id": model_id, "root": root, "owned_by": owned_by, "context_length": 272000}


def test_fix_codex_context_overrides_corrects_every_gpt_5_6_variant(monkeypatch):
    login = fake_login_ok()
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

    monkeypatch.setattr(client_module.urllib.request, "urlopen", fake_urlopen)
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
    login = fake_login_ok()
    catalog = {"object": "list", "data": [_catalog_entry("cx/gpt-5.4", "gpt-5.4")]}

    def fake_urlopen(request, timeout):
        if request.full_url == LOGIN_URL:
            return login(request, timeout)
        assert request.full_url == "http://127.0.0.1:20128/v1/models"
        return FakeResponse(catalog)

    monkeypatch.setattr(client_module.urllib.request, "urlopen", fake_urlopen)
    result = fix_codex_context_overrides("http://127.0.0.1:20128", "dashboard-pw")
    assert result == []


def test_fix_codex_context_overrides_raises_on_unexpected_catalog_shape(monkeypatch):
    login = fake_login_ok()

    def fake_urlopen(request, timeout):
        if request.full_url == LOGIN_URL:
            return login(request, timeout)
        return FakeResponse({"unexpected": "shape"})

    monkeypatch.setattr(client_module.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(OmniRouteError, match="unexpected /v1/models response shape"):
        fix_codex_context_overrides("http://127.0.0.1:20128", "dashboard-pw")
