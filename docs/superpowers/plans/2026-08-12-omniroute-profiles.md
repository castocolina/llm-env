# OmniRoute Profiles (Combo-based model routing) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Provision two new external OmniRoute provider connections (OpenAI, Moonshot) and three named OmniRoute Combos ("profiles" — `llm-env-execution`, `llm-env-validation`, `llm-env-planning`) so any OpenAI-compatible client can call a stable model id and get automatic in-combo fallback, idempotently, on every `make start`.

**Architecture:** Extend `pylib/omniroute.py`'s single-connection `provision()` into a multi-step idempotent provisioner: create-or-update up to 3 provider connections (skipping any external one whose API key is blank), then create-or-update 3 Combos referencing those connections' ids. `models.yml` gains an `omniroute.providers.{openai,moonshot}.api_key` section for the two new secrets. `llmenv.py omniroute provision` (already invoked by `scripts/start.sh` on every `make start`) passes the new keys through.

**Tech Stack:** Python 3.11 stdlib only (`urllib.request`, `json` — no new dependency, matching the existing module's own constraint), pytest, existing bash test harness in `tests/test_shell.py` / `tests/test_cli.py`.

## Global Constraints

- No new Python dependency — `pylib/omniroute.py` uses only the standard library today and must keep doing so (see its module docstring).
- OmniRoute API behavior below is **verified live** against a running instance (127.0.0.1:20128) by creating and deleting real test objects — not inferred from the (partially stale) bundled `/openapi.yaml`. Trust this plan's shapes over that spec.
- `PUT /api/combos/{id}` array order is the priority order — there is no explicit per-node priority field.
- A combo's `name` field is the model id OpenAI-compatible clients call — not its `id`.
- Reasoning effort / thinking budget is **not configurable per combo or per connection** in OmniRoute (confirmed: no such field in any relevant schema; the only related control, `PUT /api/settings/thinking-budget`, is instance-wide). Nothing in this plan attempts to set it.
- Every new secret (`omniroute.providers.openai.api_key`, `omniroute.providers.moonshot.api_key`) defaults to `""` and, when blank, the connection (and any combo that needs it as primary) is skipped — never fail provisioning because an optional external key is unset.
- Per `docs/superpowers/specs/2026-08-12-omniroute-profiles-design.md`: model ids are fixed literals in code (`ornith-35b`, `gpt-5.6-terra`, `gpt-5.6-sol`, `kimi-k3`), not user-configurable — only API keys are secrets in `models.yml`.

---

## Task 1: External provider connections (OpenAI, Moonshot)

**Files:**
- Modify: `pylib/omniroute.py`
- Modify: `tests/test_omniroute.py`
- Modify: `tests/test_cli.py:1441-1470` (`test_omniroute_provision_creates_a_connection_via_the_admin_api`)

**Interfaces:**
- Produces: `OPENAI_CONNECTION_NAME = "llm-env-openai"`, `MOONSHOT_CONNECTION_NAME = "llm-env-moonshot"`, `MOONSHOT_BASE_URL = "https://api.moonshot.ai/v1"` module constants.
- Produces: `build_openai_payload(api_key: str) -> dict[str, Any]`.
- Produces: `build_moonshot_payload(api_key: str) -> dict[str, Any]`.
- Produces: `_create_or_update_connection(base_url: str, session_token: str, connections: list[dict[str, Any]], payload: dict[str, Any]) -> dict[str, Any]` (returns `{"action": "created"|"updated", "id": ...}`) — shared by the local, OpenAI, and Moonshot connections.
- Changes: `provision(base_url, dashboard_password, port, api_key, openai_api_key="", moonshot_api_key="")` — new two trailing optional params; return shape becomes `{"connections": {"llm-env-local": {...}, "llm-env-openai": {...}, "llm-env-moonshot": {...}}}` (an entry is `{"action": "skipped", "reason": "no api key configured"}` when its key is blank). Combos are added by Task 2 — this task's `provision()` does not touch `/api/combos` yet.
- Consumes: existing `find_connection`, `_request`, `_login`, `_extract_providers`, `OmniRouteError`, `CONNECTION_NAME`, `build_payload` (all unchanged).

- [ ] **Step 1: Write the failing tests for the two new payload builders**

Add to `tests/test_omniroute.py` (near the existing `test_build_payload_uses_the_real_router_api_key`):

```python
from pylib.omniroute import (
    CONNECTION_NAME,
    MOONSHOT_BASE_URL,
    MOONSHOT_CONNECTION_NAME,
    OPENAI_CONNECTION_NAME,
    OmniRouteError,
    build_moonshot_payload,
    build_openai_payload,
    build_payload,
    find_connection,
    provision,
)


def test_build_openai_payload_has_no_base_url_override():
    payload = build_openai_payload(api_key="sk-test")
    assert payload == {
        "provider": "openai",
        "name": OPENAI_CONNECTION_NAME,
        "apiKey": "sk-test",
        "priority": 1,
        "testStatus": "active",
    }


def test_build_moonshot_payload_overrides_base_url():
    payload = build_moonshot_payload(api_key="mk-test")
    assert payload == {
        "provider": "openai",
        "name": MOONSHOT_CONNECTION_NAME,
        "apiKey": "mk-test",
        "priority": 1,
        "testStatus": "active",
        "providerSpecificData": {"baseUrl": MOONSHOT_BASE_URL},
    }
```

Replace the existing `from pylib.omniroute import (...)` import block at the
top of `tests/test_omniroute.py` with the block above (it's the same
imports plus the three new names).

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `uv run pytest tests/test_omniroute.py -k "openai_payload or moonshot_payload" -v`
Expected: FAIL with `ImportError: cannot import name 'build_openai_payload'`

- [ ] **Step 3: Add the two payload builders and connection-name/base-url constants**

In `pylib/omniroute.py`, right after the existing `CONNECTION_NAME = "llm-env-local"` line, add:

```python
OPENAI_CONNECTION_NAME = "llm-env-openai"
MOONSHOT_CONNECTION_NAME = "llm-env-moonshot"
# Moonshot's own OpenAI-compatible Chat Completions endpoint (confirmed via
# Moonshot's docs, https://platform.kimi.ai) -- OmniRoute has no native
# "moonshot" provider type (its catalog only lists first-party types), so
# this is provisioned as an "openai" connection with a custom baseUrl, the
# same providerSpecificData override mechanism the "llm-env-local" llama-cpp
# connection already uses. Verified live: POST/PUT /api/providers with
# provider="openai" + providerSpecificData.baseUrl persists and round-trips
# correctly against a running OmniRoute instance.
MOONSHOT_BASE_URL = "https://api.moonshot.ai/v1"
```

Then, right after the existing `build_payload()` function, add:

```python
def build_openai_payload(api_key: str) -> dict[str, Any]:
    return {
        "provider": "openai",
        "name": OPENAI_CONNECTION_NAME,
        "apiKey": api_key,
        "priority": 1,
        "testStatus": "active",
    }


def build_moonshot_payload(api_key: str) -> dict[str, Any]:
    return {
        "provider": "openai",
        "name": MOONSHOT_CONNECTION_NAME,
        "apiKey": api_key,
        "priority": 1,
        "testStatus": "active",
        "providerSpecificData": {"baseUrl": MOONSHOT_BASE_URL},
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_omniroute.py -k "openai_payload or moonshot_payload" -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Write the failing tests for multi-connection `provision()`**

Replace the existing `test_provision_creates_when_no_existing_connection` and
`test_provision_updates_when_existing_connection` tests in
`tests/test_omniroute.py` with these (same names — they now assert the new
nested return shape):

```python
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
    assert result["connections"][CONNECTION_NAME] == {"action": "created", "id": "new-id"}
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
    assert result["connections"][CONNECTION_NAME] == {"action": "updated", "id": "existing-id"}
```

The remaining existing test that asserts the flat return shape,
`test_provision_accepts_a_providers_wrapped_listing`, needs the same
treatment. Replace its final line:

```python
    result = provision("http://127.0.0.1:20128", "dashboard-pw", 8000, "api-key")
    assert result == {"action": "updated", "id": "existing-id"}
```

with:

```python
    result = provision("http://127.0.0.1:20128", "dashboard-pw", 8000, "api-key")
    assert result["connections"][CONNECTION_NAME] == {"action": "updated", "id": "existing-id"}
```

`test_provision_sends_the_session_cookie`,
`test_provision_raises_when_login_returns_no_session_cookie`,
`test_provision_raises_omniroute_error_on_login_http_failure`,
`test_provision_raises_omniroute_error_on_http_failure`,
`test_provision_raises_on_unexpected_listing_shape`,
`test_unexpected_listing_shape_error_does_not_leak_the_payload`, and
`test_provision_raises_when_existing_connection_has_no_id` need no changes
— they don't inspect the success return shape.

Add three new tests, after `test_provision_updates_when_existing_connection`:

```python
def test_provision_creates_openai_and_moonshot_connections_when_keys_set(monkeypatch):
    login = _fake_login_ok()
    posted = []

    def fake_urlopen(request, timeout):
        if request.full_url == LOGIN_URL:
            return login(request, timeout)
        if request.get_method() == "GET":
            return FakeResponse({"connections": []})
        body = json.loads(request.data)
        posted.append(body)
        return FakeResponse({"connection": {"id": f"{body['name']}-id"}})

    monkeypatch.setattr(omniroute_module.urllib.request, "urlopen", fake_urlopen)
    result = provision(
        "http://127.0.0.1:20128", "dashboard-pw", 8000, "api-key",
        openai_api_key="sk-oa", moonshot_api_key="mk-ks",
    )
    assert result["connections"][OPENAI_CONNECTION_NAME] == {
        "action": "created", "id": f"{OPENAI_CONNECTION_NAME}-id",
    }
    assert result["connections"][MOONSHOT_CONNECTION_NAME] == {
        "action": "created", "id": f"{MOONSHOT_CONNECTION_NAME}-id",
    }
    openai_posted = next(p for p in posted if p["name"] == OPENAI_CONNECTION_NAME)
    assert openai_posted["apiKey"] == "sk-oa"
    assert "providerSpecificData" not in openai_posted
    moonshot_posted = next(p for p in posted if p["name"] == MOONSHOT_CONNECTION_NAME)
    assert moonshot_posted["apiKey"] == "mk-ks"
    assert moonshot_posted["providerSpecificData"]["baseUrl"] == MOONSHOT_BASE_URL


def test_provision_skips_openai_and_moonshot_when_keys_blank(monkeypatch):
    login = _fake_login_ok()

    def fake_urlopen(request, timeout):
        if request.full_url == LOGIN_URL:
            return login(request, timeout)
        if request.get_method() == "GET":
            return FakeResponse({"connections": []})
        return FakeResponse({"connection": {"id": "new-id"}})

    monkeypatch.setattr(omniroute_module.urllib.request, "urlopen", fake_urlopen)
    result = provision("http://127.0.0.1:20128", "dashboard-pw", 8000, "api-key")
    assert result["connections"][OPENAI_CONNECTION_NAME] == {
        "action": "skipped", "reason": "no api key configured",
    }
    assert result["connections"][MOONSHOT_CONNECTION_NAME] == {
        "action": "skipped", "reason": "no api key configured",
    }


def test_provision_updates_existing_openai_connection(monkeypatch):
    login = _fake_login_ok()

    def fake_urlopen(request, timeout):
        if request.full_url == LOGIN_URL:
            return login(request, timeout)
        if request.get_method() == "GET":
            return FakeResponse({
                "connections": [{"id": "openai-id", "name": OPENAI_CONNECTION_NAME}],
            })
        assert request.get_method() == "PUT"
        assert request.full_url == "http://127.0.0.1:20128/api/providers/openai-id"
        return FakeResponse({"connection": {"id": "openai-id"}})

    monkeypatch.setattr(omniroute_module.urllib.request, "urlopen", fake_urlopen)
    result = provision(
        "http://127.0.0.1:20128", "dashboard-pw", 8000, "api-key",
        openai_api_key="sk-oa",
    )
    assert result["connections"][OPENAI_CONNECTION_NAME] == {
        "action": "updated", "id": "openai-id",
    }
```

- [ ] **Step 6: Run the new/changed tests to verify they fail**

Run: `uv run pytest tests/test_omniroute.py -v`
Expected: FAIL — `provision()` doesn't accept `openai_api_key`/`moonshot_api_key` yet, and its return shape is still flat.

- [ ] **Step 7: Refactor `provision()` to the multi-connection shape**

Replace the entire `provision()` function body in `pylib/omniroute.py` with:

```python
def _create_or_update_connection(
    base_url: str,
    session_token: str,
    connections: list[dict[str, Any]],
    payload: dict[str, Any],
) -> dict[str, Any]:
    name = payload["name"]
    existing = find_connection(connections, name)
    if existing is None:
        created = _request("POST", f"{base_url}/api/providers", session_token, payload)
        # A live instance nests the new record under "connection" (verified
        # against a running container); fall back to a bare id for safety.
        created_id = None
        if isinstance(created, dict):
            connection = created.get("connection")
            created_id = (
                connection.get("id")
                if isinstance(connection, dict)
                else created.get("id")
            )
        return {"action": "created", "id": created_id}

    connection_id = existing.get("id")
    if not isinstance(connection_id, (str, int)):
        raise OmniRouteError(f"existing {name!r} connection has no usable id")
    _request("PUT", f"{base_url}/api/providers/{connection_id}", session_token, payload)
    return {"action": "updated", "id": connection_id}


def provision(
    base_url: str,
    dashboard_password: str,
    port: int,
    api_key: str,
    openai_api_key: str = "",
    moonshot_api_key: str = "",
) -> dict[str, Any]:
    session_token = _login(base_url, dashboard_password)
    listing = _request("GET", f"{base_url}/api/providers", session_token)
    connections = _extract_providers(listing)

    connection_results: dict[str, dict[str, Any]] = {
        CONNECTION_NAME: _create_or_update_connection(
            base_url, session_token, connections, build_payload(port, api_key)
        ),
    }

    if openai_api_key:
        connection_results[OPENAI_CONNECTION_NAME] = _create_or_update_connection(
            base_url, session_token, connections, build_openai_payload(openai_api_key)
        )
    else:
        connection_results[OPENAI_CONNECTION_NAME] = {
            "action": "skipped", "reason": "no api key configured",
        }

    if moonshot_api_key:
        connection_results[MOONSHOT_CONNECTION_NAME] = _create_or_update_connection(
            base_url, session_token, connections, build_moonshot_payload(moonshot_api_key)
        )
    else:
        connection_results[MOONSHOT_CONNECTION_NAME] = {
            "action": "skipped", "reason": "no api key configured",
        }

    return {"connections": connection_results}
```

- [ ] **Step 8: Run the tests to verify they pass**

Run: `uv run pytest tests/test_omniroute.py -v`
Expected: PASS (all tests in the file)

- [ ] **Step 9: Update the `llmenv.py` CLI integration test for the new return shape**

In `tests/test_cli.py`, `test_omniroute_provision_creates_a_connection_via_the_admin_api`
(around line 1452) currently asserts:

```python
assert json.loads(result.stdout) == {"action": "created", "id": "created-id"}
```

Change it to:

```python
assert json.loads(result.stdout)["connections"]["llm-env-local"] == {
    "action": "created", "id": "created-id",
}
```

No other change is needed in this test — the test config
(`_write_omniroute_test_config`) sets no `omniroute.providers` section, so
`omniroute_cfg.get("providers")` is absent and `cmd_omniroute` (unchanged
until Task 4) still calls `provision()` with no `openai_api_key`/
`moonshot_api_key` args, which default to `""` and are skipped.

- [ ] **Step 10: Run the full test suite to verify nothing else broke**

Run: `uv run pytest tests/test_omniroute.py tests/test_cli.py -v`
Expected: PASS (all tests)

- [ ] **Step 11: Commit**

```bash
git add pylib/omniroute.py tests/test_omniroute.py tests/test_cli.py
git commit -m "feat(omniroute): provision OpenAI and Moonshot provider connections"
```

---

## Task 2: Combo provisioning (the 3 profiles)

**Files:**
- Modify: `pylib/omniroute.py`
- Modify: `tests/test_omniroute.py`
- Modify: `tests/test_cli.py:1380-1470` (`_RecordingProviderHandler`, `test_omniroute_provision_creates_a_connection_via_the_admin_api`)

**Interfaces:**
- Consumes (from Task 1): `provision()`'s `connection_results` dict keyed by `CONNECTION_NAME`/`OPENAI_CONNECTION_NAME`/`MOONSHOT_CONNECTION_NAME`, each `{"action": ..., "id": ...}` or `{"action": "skipped", ...}`.
- Produces: `EXECUTION_COMBO_NAME = "llm-env-execution"`, `VALIDATION_COMBO_NAME = "llm-env-validation"`, `PLANNING_COMBO_NAME = "llm-env-planning"` module constants.
- Produces: `find_combo(combos: list[dict[str, Any]], name: str) -> dict[str, Any] | None`.
- Produces: `_create_or_update_combo(base_url: str, session_token: str, combos: list[dict[str, Any]], name: str, nodes: list[dict[str, str]]) -> dict[str, Any]` (returns `{"action": "created"|"updated", "id": ...}`).
- Changes: `provision()`'s return value gains a `"combos"` key: `{"connections": {...}, "combos": {"llm-env-execution": {...}, "llm-env-validation": {...}, "llm-env-planning": {...}}}`. A combo whose primary connection was skipped becomes `{"action": "skipped", "reason": "<connection-name> connection not provisioned"}`.

- [ ] **Step 1: Write the failing tests for the combo helpers**

Add to `tests/test_omniroute.py` (near `test_find_connection_matches_by_name`):

```python
def test_find_combo_matches_by_name():
    combos = [{"id": "1", "name": "other"}, {"id": "2", "name": "llm-env-execution"}]
    assert find_combo(combos, "llm-env-execution")["id"] == "2"


def test_find_combo_returns_none_when_absent():
    assert find_combo([{"id": "1", "name": "other"}], "llm-env-execution") is None
```

Update the `from pylib.omniroute import (...)` block at the top of the file
to add `find_combo`.

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `uv run pytest tests/test_omniroute.py -k find_combo -v`
Expected: FAIL with `ImportError: cannot import name 'find_combo'`

- [ ] **Step 3: Implement `find_combo`**

In `pylib/omniroute.py`, right after the existing `find_connection()`
function, add:

```python
def find_combo(combos: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    return next((c for c in combos if c.get("name") == name), None)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_omniroute.py -k find_combo -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Write the failing tests for `provision()`'s combo behavior**

Add to `tests/test_omniroute.py`, after the connection tests from Task 1:

```python
def _fake_urlopen_full_provision(monkeypatch, *, combo_ids=None):
    """Wire a fake urlopen that answers login, connection listing/creation,
    and combo listing/creation/update, recording every non-login call."""
    login = _fake_login_ok()
    calls = []
    combo_ids = combo_ids or {}

    def fake_urlopen(request, timeout):
        if request.full_url == LOGIN_URL:
            return login(request, timeout)
        calls.append((request.get_method(), request.full_url, request.data))
        if request.full_url == "http://127.0.0.1:20128/api/providers" and request.get_method() == "GET":
            return FakeResponse({"connections": []})
        if request.full_url == "http://127.0.0.1:20128/api/providers" and request.get_method() == "POST":
            body = json.loads(request.data)
            return FakeResponse({"connection": {"id": f"{body['name']}-id"}})
        if request.full_url == "http://127.0.0.1:20128/api/combos" and request.get_method() == "GET":
            return FakeResponse({"combos": []})
        if request.full_url == "http://127.0.0.1:20128/api/combos" and request.get_method() == "POST":
            body = json.loads(request.data)
            combo_id = combo_ids.get(body["name"], f"{body['name']}-combo-id")
            return FakeResponse({"id": combo_id, "name": body["name"], "models": []})
        assert request.get_method() == "PUT"
        return FakeResponse({"id": request.full_url.rsplit("/", 1)[-1], "models": []})

    monkeypatch.setattr(omniroute_module.urllib.request, "urlopen", fake_urlopen)
    return calls


def test_provision_creates_the_execution_combo_pinned_to_ornith_35b(monkeypatch):
    calls = _fake_urlopen_full_provision(monkeypatch)
    result = provision("http://127.0.0.1:20128", "dashboard-pw", 8000, "api-key")
    assert result["combos"]["llm-env-execution"] == {
        "action": "created", "id": "llm-env-execution-combo-id",
    }
    put_call = next(
        c for c in calls
        if c[0] == "PUT" and c[1] == "http://127.0.0.1:20128/api/combos/llm-env-execution-combo-id"
    )
    assert json.loads(put_call[2]) == {
        "models": [{"connectionId": "llm-env-local-id", "model": "ornith-35b"}],
    }


def test_provision_skips_validation_and_planning_combos_without_keys(monkeypatch):
    _fake_urlopen_full_provision(monkeypatch)
    result = provision("http://127.0.0.1:20128", "dashboard-pw", 8000, "api-key")
    assert result["combos"]["llm-env-validation"] == {
        "action": "skipped", "reason": "llm-env-openai connection not provisioned",
    }
    assert result["combos"]["llm-env-planning"] == {
        "action": "skipped", "reason": "llm-env-moonshot connection not provisioned",
    }


def test_provision_creates_validation_combo_with_terra_primary_sol_fallback(monkeypatch):
    calls = _fake_urlopen_full_provision(monkeypatch)
    result = provision(
        "http://127.0.0.1:20128", "dashboard-pw", 8000, "api-key",
        openai_api_key="sk-oa",
    )
    assert result["combos"]["llm-env-validation"] == {
        "action": "created", "id": "llm-env-validation-combo-id",
    }
    put_call = next(
        c for c in calls
        if c[0] == "PUT"
        and c[1] == "http://127.0.0.1:20128/api/combos/llm-env-validation-combo-id"
    )
    assert json.loads(put_call[2]) == {
        "models": [
            {"connectionId": "llm-env-openai-id", "model": "gpt-5.6-terra"},
            {"connectionId": "llm-env-openai-id", "model": "gpt-5.6-sol"},
        ],
    }


def test_provision_creates_planning_combo_pinned_to_kimi_k3(monkeypatch):
    calls = _fake_urlopen_full_provision(monkeypatch)
    result = provision(
        "http://127.0.0.1:20128", "dashboard-pw", 8000, "api-key",
        moonshot_api_key="mk-ks",
    )
    assert result["combos"]["llm-env-planning"] == {
        "action": "created", "id": "llm-env-planning-combo-id",
    }
    put_call = next(
        c for c in calls
        if c[0] == "PUT"
        and c[1] == "http://127.0.0.1:20128/api/combos/llm-env-planning-combo-id"
    )
    assert json.loads(put_call[2]) == {
        "models": [{"connectionId": "llm-env-moonshot-id", "model": "kimi-k3"}],
    }


def test_provision_updates_existing_combo(monkeypatch):
    login = _fake_login_ok()
    calls = []

    def fake_urlopen(request, timeout):
        if request.full_url == LOGIN_URL:
            return login(request, timeout)
        calls.append((request.get_method(), request.full_url, request.data))
        if request.full_url == "http://127.0.0.1:20128/api/providers" and request.get_method() == "GET":
            return FakeResponse({"connections": [{"id": "local-id", "name": CONNECTION_NAME}]})
        if request.full_url == "http://127.0.0.1:20128/api/combos" and request.get_method() == "GET":
            return FakeResponse({"combos": [{"id": "existing-combo-id", "name": "llm-env-execution"}]})
        assert request.get_method() == "PUT"
        return FakeResponse({"id": "existing-combo-id", "models": []})

    monkeypatch.setattr(omniroute_module.urllib.request, "urlopen", fake_urlopen)
    result = provision("http://127.0.0.1:20128", "dashboard-pw", 8000, "api-key")
    assert result["combos"]["llm-env-execution"] == {"action": "updated", "id": "existing-combo-id"}
```

- [ ] **Step 6: Run the new tests to verify they fail**

Run: `uv run pytest tests/test_omniroute.py -k "combo" -v`
Expected: FAIL — `provision()`'s return dict has no `"combos"` key yet.

- [ ] **Step 7: Add combo constants, `_create_or_update_combo`, and wire combos into `provision()`**

In `pylib/omniroute.py`, after the `MOONSHOT_BASE_URL` constant, add:

```python
EXECUTION_COMBO_NAME = "llm-env-execution"
VALIDATION_COMBO_NAME = "llm-env-validation"
PLANNING_COMBO_NAME = "llm-env-planning"

LOCAL_EXECUTION_MODEL = "ornith-35b"
# Primary + in-combo fallback, both real/current OpenAI models as of
# 2026-08-12 (see docs/superpowers/specs/2026-08-12-omniroute-profiles-design.md).
OPENAI_VALIDATION_MODELS = ["gpt-5.6-terra", "gpt-5.6-sol"]
MOONSHOT_PLANNING_MODEL = "kimi-k3"
```

After `_extract_providers()`, add its combo-listing counterpart:

```python
def _extract_combos(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        value = payload.get("combos")
        if isinstance(value, list):
            return value
    if isinstance(payload, list):
        return payload
    shape = (
        f"dict with keys {sorted(payload.keys())!r}"
        if isinstance(payload, dict)
        else type(payload).__name__
    )
    raise OmniRouteError(f"unexpected combo listing shape: {shape}")
```

After `find_combo()`, add:

```python
def _create_or_update_combo(
    base_url: str,
    session_token: str,
    combos: list[dict[str, Any]],
    name: str,
    nodes: list[dict[str, str]],
) -> dict[str, Any]:
    existing = find_combo(combos, name)
    if existing is None:
        created = _request(
            "POST", f"{base_url}/api/combos", session_token,
            {"name": name, "strategy": "priority"},
        )
        combo_id = created.get("id") if isinstance(created, dict) else None
        if not isinstance(combo_id, (str, int)):
            raise OmniRouteError(f"combo {name!r} creation returned no usable id")
        action = "created"
    else:
        combo_id = existing.get("id")
        if not isinstance(combo_id, (str, int)):
            raise OmniRouteError(f"existing combo {name!r} has no usable id")
        action = "updated"
    # The array order in "models" IS the fallback priority order (verified
    # live -- there is no separate priority field on a combo node).
    _request("PUT", f"{base_url}/api/combos/{combo_id}", session_token, {"models": nodes})
    return {"action": action, "id": combo_id}
```

Finally, replace `provision()`'s `return {"connections": connection_results}`
line with:

```python
    combo_listing = _request("GET", f"{base_url}/api/combos", session_token)
    combos = _extract_combos(combo_listing)

    combo_results: dict[str, dict[str, Any]] = {}

    local_connection_id = connection_results[CONNECTION_NAME]["id"]
    combo_results[EXECUTION_COMBO_NAME] = _create_or_update_combo(
        base_url, session_token, combos, EXECUTION_COMBO_NAME,
        [{"connectionId": local_connection_id, "model": LOCAL_EXECUTION_MODEL}],
    )

    if connection_results[OPENAI_CONNECTION_NAME]["action"] != "skipped":
        openai_connection_id = connection_results[OPENAI_CONNECTION_NAME]["id"]
        combo_results[VALIDATION_COMBO_NAME] = _create_or_update_combo(
            base_url, session_token, combos, VALIDATION_COMBO_NAME,
            [
                {"connectionId": openai_connection_id, "model": model}
                for model in OPENAI_VALIDATION_MODELS
            ],
        )
    else:
        combo_results[VALIDATION_COMBO_NAME] = {
            "action": "skipped",
            "reason": f"{OPENAI_CONNECTION_NAME} connection not provisioned",
        }

    if connection_results[MOONSHOT_CONNECTION_NAME]["action"] != "skipped":
        moonshot_connection_id = connection_results[MOONSHOT_CONNECTION_NAME]["id"]
        combo_results[PLANNING_COMBO_NAME] = _create_or_update_combo(
            base_url, session_token, combos, PLANNING_COMBO_NAME,
            [{"connectionId": moonshot_connection_id, "model": MOONSHOT_PLANNING_MODEL}],
        )
    else:
        combo_results[PLANNING_COMBO_NAME] = {
            "action": "skipped",
            "reason": f"{MOONSHOT_CONNECTION_NAME} connection not provisioned",
        }

    return {"connections": connection_results, "combos": combo_results}
```

- [ ] **Step 8: Document the combo idempotency approach in the module docstring**

Per the design doc's "Documentation" section, the `GET /api/combos`
idempotency approach belongs next to the existing comments in
`pylib/omniroute.py` about why the session-cookie auth mechanism was
chosen — i.e. the module docstring. In `pylib/omniroute.py`, replace the
module docstring:

```python
"""Idempotent provisioning of OmniRoute's connection to the local router.

OmniRoute's management API (/api/providers) accepts either a dashboard
session or a Bearer API key with "manage" scope -- it does NOT recognize
any machine-auth header keyed on a standalone CLI token (verified live
against a running instance; no such mechanism exists in OmniRoute's own
authorization docs). This module logs in with the dashboard password
(POST /api/auth/login) and reuses the resulting session cookie, since
that is the credential this deployment actually has. Uses only the
standard library so this repo's one Python dependency (pyyaml) does not
grow.
"""
```

with:

```python
"""Idempotent provisioning of OmniRoute's connections and Combos.

OmniRoute's management API (/api/providers) accepts either a dashboard
session or a Bearer API key with "manage" scope -- it does NOT recognize
any machine-auth header keyed on a standalone CLI token (verified live
against a running instance; no such mechanism exists in OmniRoute's own
authorization docs). This module logs in with the dashboard password
(POST /api/auth/login) and reuses the resulting session cookie, since
that is the credential this deployment actually has. Uses only the
standard library so this repo's one Python dependency (pyyaml) does not
grow.

Combos (the "profiles" a client calls by a stable model id) are made
idempotent the same way connections are: GET /api/combos, find-by-name
in the listing (find_combo(), mirroring find_connection()'s approach
above), then POST a new combo or PUT an update to the existing one --
there is no separate "does this combo already exist" endpoint.
"""
```

- [ ] **Step 9: Run the tests to verify they pass**

Run: `uv run pytest tests/test_omniroute.py -v`
Expected: PASS (all tests in the file)

- [ ] **Step 10: Update the `llmenv.py` CLI integration test's fake server for combos**

`provision()` now unconditionally calls `GET/POST/PUT /api/combos` for the
execution combo, even with no OpenAI/Moonshot keys configured. The fake
server in `tests/test_cli.py` (`_RecordingProviderHandler`, around line
1390) must answer those too. Replace its `do_GET` and `do_POST` methods
with:

```python
    def do_GET(self):
        self.received.append(("GET", self.path, self.headers.get("Cookie")))
        if self.path == "/api/combos":
            self._reply({"combos": []})
        else:
            self._reply({"connections": []})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length else {}
        if self.path == "/api/auth/login":
            self.received.append(("POST", self.path, body))
            self._reply({"success": True}, set_cookie="auth_token=session-token; Path=/; HttpOnly")
            return
        self.received.append(("POST", self.path, body))
        if self.path == "/api/combos":
            self._reply({"id": "combo-created-id", "name": body["name"], "models": []})
        else:
            self._reply({"connection": {"id": "created-id"}})

    def do_PUT(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length else {}
        self.received.append(("PUT", self.path, body))
        self._reply({"id": self.path.rsplit("/", 1)[-1], "models": []})
```

Add `import http.server`'s handler needs no new import — `do_PUT` uses the
same `self.rfile`/`self._reply` already used by `do_POST`, both already
present on this handler class.

- [ ] **Step 11: Run the CLI integration test to verify it still passes**

Run: `uv run pytest tests/test_cli.py -k omniroute -v`
Expected: PASS (2 passed)

- [ ] **Step 12: Run the full test suite**

Run: `uv run pytest tests/test_omniroute.py tests/test_cli.py -v`
Expected: PASS (all tests)

- [ ] **Step 13: Commit**

```bash
git add pylib/omniroute.py tests/test_omniroute.py tests/test_cli.py
git commit -m "feat(omniroute): provision the execution/validation/planning combos"
```

---

## Task 3: `models.yml` schema for the new provider API keys

**Files:**
- Modify: `pylib/config.py`
- Modify: `tests/test_config.py:844-852` (`test_migrate_config_adds_default_omniroute_section`) and other additions throughout the file
- Modify: `models.yml.example`
- Modify: `scripts/show-secrets.sh`
- Modify: `tests/test_shell.py:5287-5297` (`test_show_secrets_prints_the_api_key_and_dashboard_password`)

**Interfaces:**
- Changes: `migrate_config()` now also defaults `cfg["omniroute"]["providers"]["openai"]["api_key"]` and `cfg["omniroute"]["providers"]["moonshot"]["api_key"]` to `""` when absent.
- Changes: `validate_config()` now also validates `omniroute.providers` (optional; when present, must be a mapping whose `openai`/`moonshot` entries, if present, are mappings with a string `api_key`).
- Consumes: existing `_positive_int`, `_finite_number` helpers (unchanged); existing `make_cfg()` test helper in `tests/test_config.py` (unchanged — the new fields are optional, so tests that don't set them keep working).

- [ ] **Step 1: Write the failing migration tests**

Add to `tests/test_config.py`, after `test_migrate_config_preserves_existing_omniroute_values`:

```python
def test_migrate_config_adds_default_omniroute_providers_section():
    cfg = make_cfg()
    cfg["omniroute"].pop("providers", None)
    migrated = migrate_config(cfg)
    assert migrated["omniroute"]["providers"] == {
        "openai": {"api_key": ""},
        "moonshot": {"api_key": ""},
    }


def test_migrate_config_preserves_existing_omniroute_providers_values():
    cfg = make_cfg(
        omniroute={
            "image": "docker.io/diegosouzapw/omniroute:latest",
            "port": 21000,
            "initial_password": "existing-password",
            "providers": {
                "openai": {"api_key": "sk-existing"},
                "moonshot": {"api_key": "mk-existing"},
            },
        }
    )
    migrated = migrate_config(cfg)
    assert migrated["omniroute"]["providers"]["openai"]["api_key"] == "sk-existing"
    assert migrated["omniroute"]["providers"]["moonshot"]["api_key"] == "mk-existing"
```

- [ ] **Step 2: Update the existing default-migration test for the new `providers` key**

`tests/test_config.py:844-852`, `test_migrate_config_adds_default_omniroute_section`,
asserts `migrated["omniroute"]` with exact dict equality. Once Step 3 below
adds the `providers` default, that assertion will fail unless updated here.
Replace the test:

```python
def test_migrate_config_adds_default_omniroute_section():
    cfg = make_cfg()
    del cfg["omniroute"]
    migrated = migrate_config(cfg)
    assert migrated["omniroute"] == {
        "image": "docker.io/diegosouzapw/omniroute:latest",
        "port": 20128,
        "initial_password": "",
    }
```

with:

```python
def test_migrate_config_adds_default_omniroute_section():
    cfg = make_cfg()
    del cfg["omniroute"]
    migrated = migrate_config(cfg)
    assert migrated["omniroute"] == {
        "image": "docker.io/diegosouzapw/omniroute:latest",
        "port": 20128,
        "initial_password": "",
        "providers": {
            "openai": {"api_key": ""},
            "moonshot": {"api_key": ""},
        },
    }
```

- [ ] **Step 3: Run the new/changed tests to verify they fail**

Run: `uv run pytest tests/test_config.py -k "omniroute_providers or test_migrate_config_adds_default_omniroute_section" -v`
Expected: FAIL — `migrated["omniroute"]` has no `"providers"` key yet, so
both the new tests and the just-updated existing test fail the same way.

- [ ] **Step 4: Add the migration defaults**

In `pylib/config.py`, inside `migrate_config()`, replace:

```python
    omniroute = cfg.setdefault("omniroute", {})
    if isinstance(omniroute, dict):
        omniroute.setdefault("image", DEFAULT_OMNIROUTE_IMAGE)
        omniroute.setdefault("port", DEFAULT_OMNIROUTE_PORT)
        omniroute.setdefault("initial_password", "")
```

with:

```python
    omniroute = cfg.setdefault("omniroute", {})
    if isinstance(omniroute, dict):
        omniroute.setdefault("image", DEFAULT_OMNIROUTE_IMAGE)
        omniroute.setdefault("port", DEFAULT_OMNIROUTE_PORT)
        omniroute.setdefault("initial_password", "")
        providers = omniroute.setdefault("providers", {})
        if isinstance(providers, dict):
            openai_provider = providers.setdefault("openai", {})
            if isinstance(openai_provider, dict):
                openai_provider.setdefault("api_key", "")
            moonshot_provider = providers.setdefault("moonshot", {})
            if isinstance(moonshot_provider, dict):
                moonshot_provider.setdefault("api_key", "")
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_config.py -k "omniroute_providers or test_migrate_config_adds_default_omniroute_section" -v`
Expected: PASS (3 passed)

- [ ] **Step 6: Write the failing validation tests**

Add to `tests/test_config.py`, after `test_config_rejects_invalid_omniroute_values`:

```python
def test_config_accepts_valid_omniroute_providers_section():
    cfg = make_cfg(
        omniroute={
            "image": "docker.io/diegosouzapw/omniroute:latest",
            "port": 20128,
            "initial_password": "pw",
            "providers": {
                "openai": {"api_key": "sk-test"},
                "moonshot": {"api_key": ""},
            },
        }
    )
    assert validate_config(cfg) == []


def test_config_rejects_non_mapping_omniroute_providers_section():
    cfg = make_cfg(
        omniroute={
            "image": "docker.io/diegosouzapw/omniroute:latest",
            "port": 20128,
            "initial_password": "pw",
            "providers": [],
        }
    )
    assert "omniroute.providers must be a mapping" in validate_config(cfg)


@pytest.mark.parametrize("provider_name", ["openai", "moonshot"])
def test_config_rejects_non_mapping_omniroute_provider_entry(provider_name):
    cfg = make_cfg(
        omniroute={
            "image": "docker.io/diegosouzapw/omniroute:latest",
            "port": 20128,
            "initial_password": "pw",
            "providers": {provider_name: []},
        }
    )
    assert f"omniroute.providers.{provider_name} must be a mapping" in validate_config(cfg)


@pytest.mark.parametrize("provider_name", ["openai", "moonshot"])
def test_config_rejects_non_string_omniroute_provider_api_key(provider_name):
    cfg = make_cfg(
        omniroute={
            "image": "docker.io/diegosouzapw/omniroute:latest",
            "port": 20128,
            "initial_password": "pw",
            "providers": {provider_name: {"api_key": 5}},
        }
    )
    assert (
        f"omniroute.providers.{provider_name}.api_key must be a string"
        in validate_config(cfg)
    )
```

Confirm `import pytest` is already present at the top of
`tests/test_config.py` (it is, used by other `@pytest.mark.parametrize`
tests in the file already) — no new import needed.

- [ ] **Step 7: Run the new tests to verify they fail**

Run: `uv run pytest tests/test_config.py -k "omniroute_provider" -v`
Expected: FAIL — no validation for `omniroute.providers` exists yet, so the
"rejects" tests get an empty error list and the "accepts" test may already
pass (harmless either way — the important signal is the "rejects" tests
failing).

- [ ] **Step 8: Add the validation logic**

In `pylib/config.py`, inside `validate_config()`, inside the
`if "omniroute" in cfg:` block, after the existing
`if not isinstance(omniroute.get("initial_password", ""), str):` check, add:

```python
            if "providers" in omniroute:
                providers = omniroute["providers"]
                if not isinstance(providers, dict):
                    errors.append("omniroute.providers must be a mapping")
                else:
                    for provider_name in ("openai", "moonshot"):
                        if provider_name not in providers:
                            continue
                        provider_cfg = providers[provider_name]
                        if not isinstance(provider_cfg, dict):
                            errors.append(
                                f"omniroute.providers.{provider_name} must be a mapping"
                            )
                            continue
                        if not isinstance(provider_cfg.get("api_key", ""), str):
                            errors.append(
                                f"omniroute.providers.{provider_name}.api_key must be a string"
                            )
```

- [ ] **Step 9: Run the tests to verify they pass**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS (all tests in the file)

- [ ] **Step 10: Update `models.yml.example`**

In `models.yml.example`, replace:

```yaml
omniroute:
  image: docker.io/diegosouzapw/omniroute:latest
  port: 20128
  initial_password: ""
```

with:

```yaml
omniroute:
  image: docker.io/diegosouzapw/omniroute:latest
  port: 20128
  initial_password: ""
  providers:
    openai:
      api_key: ""
    moonshot:
      api_key: ""
```

- [ ] **Step 11: Write the failing test for `show-secrets.sh`**

Replace `test_show_secrets_prints_the_api_key_and_dashboard_password` in
`tests/test_shell.py` with:

```python
def test_show_secrets_prints_the_api_key_and_dashboard_password(
    tmp_path: pathlib.Path,
) -> None:
    result, _config, _calls = run_lifecycle_script(
        tmp_path, "scripts/show-secrets.sh", api_key="existing-key"
    )

    assert result.returncode == 0, result.stderr
    assert "existing-key" in result.stdout
    assert "(not set)" in result.stdout


def test_show_secrets_prints_omniroute_provider_keys_when_configured(
    tmp_path: pathlib.Path,
) -> None:
    result, config, _calls = run_lifecycle_script(
        tmp_path, "scripts/show-secrets.sh", api_key="existing-key"
    )
    assert result.returncode == 0, result.stderr
    # No omniroute.providers section in the fixture config -- both keys
    # print as unset rather than erroring.
    assert result.stdout.count("(not set)") >= 2
```

(`run_lifecycle_script`'s fixture config never sets
`omniroute.providers.*`, so this only proves the new `yq` lookups don't
error on a missing key — it can't assert a real key value without changing
the shared fixture, which is out of scope here.)

- [ ] **Step 12: Run the test to verify it fails**

Run: `uv run pytest tests/test_shell.py -k show_secrets -v`
Expected: FAIL — `test_show_secrets_prints_omniroute_provider_keys_when_configured`
counts fewer than 2 occurrences of `"(not set)"` (today only the dashboard
password line produces one).

- [ ] **Step 13: Add the new lines to `show-secrets.sh`**

In `scripts/show-secrets.sh`, after the existing
`printf 'OmniRoute dashboard password: %s\n' ...` line, add:

```bash
printf 'OmniRoute OpenAI provider key:   %s\n' "$(yq -r '.omniroute.providers.openai.api_key // "(not set)"' "$CONFIG_PATH")"
printf 'OmniRoute Moonshot provider key: %s\n' "$(yq -r '.omniroute.providers.moonshot.api_key // "(not set)"' "$CONFIG_PATH")"
```

- [ ] **Step 14: Run the tests to verify they pass**

Run: `uv run pytest tests/test_shell.py -k show_secrets -v`
Expected: PASS (3 passed)

- [ ] **Step 15: Commit**

```bash
git add pylib/config.py tests/test_config.py models.yml.example scripts/show-secrets.sh tests/test_shell.py
git commit -m "feat(config): add omniroute.providers.{openai,moonshot}.api_key"
```

---

## Task 4: Wire the CLI, document, and run the full gate

**Files:**
- Modify: `llmenv.py:368-380` (`cmd_omniroute`)
- Modify: `tests/test_cli.py` (extend the omniroute CLI tests)
- Modify: `.agents/architecture.md`

**Interfaces:**
- Consumes (from Task 1+2): `provision(base_url, dashboard_password, port, api_key, openai_api_key="", moonshot_api_key="")`.
- Consumes (from Task 3): `cfg["omniroute"]["providers"]["openai"]["api_key"]`, `cfg["omniroute"]["providers"]["moonshot"]["api_key"]` (both present after `migrate_config`, both default `""`).

- [ ] **Step 1: Write the failing CLI test for key passthrough**

Add to `tests/test_cli.py`, after `test_omniroute_provision_creates_a_connection_via_the_admin_api`:

```python
def test_omniroute_provision_passes_through_provider_keys(tmp_path):
    _RecordingProviderHandler.received = []
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _RecordingProviderHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        config = tmp_path / "models.yml"
        config.write_text(
            "version: 1\n"
            "server: {host: 0.0.0.0, port: 8000, api_key: routerkey, mdns_name: llm,"
            " sleep_idle_seconds: 300}\n"
            "gpu: {pci_address: '0000:03:00.0', device_name: d, backend: vulkan,"
            " image: i, vram_total_mib: 16304, reserve_mode: auto, reserve_floor_mib: 1024}\n"
            "runtime: {models_max: 1, parallel_slots: 1, ubatch_size: 512,"
            " flash_attn: true, cache_type_k: q8_0, cache_type_v: q8_0}\n"
            f"omniroute: {{image: i, port: {server.server_address[1]},"
            " initial_password: dashboard-pw, providers: {openai: {api_key: sk-oa},"
            " moonshot: {api_key: mk-ks}}}}\n"
            "models:\n"
            "  - {alias: a, label: A, parameters: 1B, quantization: Q4_K_M, enabled: true,"
            " file: a.gguf, url: u, size_bytes: 1, vram_budget: 10%, ctx_size: 4096,"
            " client_max_output_tokens: 4096, n_gpu_layers: 99}\n"
        )
        result = run("--config", str(config), "omniroute", "provision")
        assert result.returncode == 0, result.stderr
        posted_names = {
            body.get("name")
            for method, path, body in _RecordingProviderHandler.received
            if method == "POST" and path == "/api/providers"
        }
        assert posted_names == {"llm-env-local", "llm-env-openai", "llm-env-moonshot"}
    finally:
        server.shutdown()
        thread.join()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_cli.py -k passes_through_provider_keys -v`
Expected: FAIL — only `llm-env-local` gets posted; `cmd_omniroute` doesn't
read/pass the provider keys yet.

- [ ] **Step 3: Wire the provider keys through `cmd_omniroute`**

In `llmenv.py`, replace the body of `cmd_omniroute`:

```python
def cmd_omniroute(args: argparse.Namespace) -> int:
    cfg = require_valid_config(load_config(Path(args.config)))
    omniroute_cfg = cfg.get("omniroute") or {}
    initial_password = omniroute_cfg.get("initial_password")
    port = omniroute_cfg.get("port")
    if not initial_password or not port:
        return fail(
            "omniroute.initial_password and omniroute.port must be set; run "
            "'make start' after 'make setup' to generate them"
        )
    providers_cfg = omniroute_cfg.get("providers") or {}
    openai_api_key = (providers_cfg.get("openai") or {}).get("api_key", "")
    moonshot_api_key = (providers_cfg.get("moonshot") or {}).get("api_key", "")
    base_url = f"http://127.0.0.1:{port}"
    result = provision(
        base_url,
        initial_password,
        cfg["server"]["port"],
        cfg["server"]["api_key"],
        openai_api_key,
        moonshot_api_key,
    )
    return emit(result)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_cli.py -k passes_through_provider_keys -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Run the full CLI and omniroute test files**

Run: `uv run pytest tests/test_cli.py tests/test_omniroute.py tests/test_config.py tests/test_shell.py -v`
Expected: PASS (all tests)

- [ ] **Step 6: Correct the stale connection-provisioning paragraph and document the profiles in `.agents/architecture.md`**

`.agents/architecture.md`'s `### Topology and what survives a restart`
section (under `## Container Lifecycle`) has a paragraph that says this
repo's provisioning only ever creates the one `llm-env-local` connection —
that becomes false once this plan lands. Replace that paragraph:

```markdown
To provision more than the one `llm-env-local` connection this repo sets up
automatically, either add it by hand in the dashboard (persists in the
volume the same way), or extend `llmenv.py omniroute provision` /
`pylib/omniroute.py` — today it only idempotently creates/updates that one
connection; generalizing it to read a list of desired connections from
`models.yml` would make additional provisioning reproducible instead of
manual.
```

with:

```markdown
This repo's `llmenv.py omniroute provision` automatically provisions three
connections (`llm-env-local`, `llm-env-openai`, `llm-env-moonshot` — the
latter two only when their `models.yml` API keys are set) and three Combos
built on top of them — see "OmniRoute profiles (Combos)" below. To
provision anything beyond those, either add it by hand in the dashboard
(persists in the volume the same way) or extend `pylib/omniroute.py`
further.
```

Immediately after that replaced paragraph — still inside
`### Topology and what survives a restart`, before the
`**Inspecting the generated compose file:**` paragraph — insert a new
subsection:

```markdown
### OmniRoute profiles (Combos)

`llmenv.py omniroute provision` also provisions three OmniRoute Combos —
named, callable model ids with in-combo fallback:

| Combo | Primary → fallback | Backing connection(s) |
|---|---|---|
| `llm-env-execution` | `ornith-35b` (no fallback) | `llm-env-local` (this repo's own llama.cpp router) |
| `llm-env-validation` | `gpt-5.6-terra` → `gpt-5.6-sol` | `llm-env-openai` |
| `llm-env-planning` | `kimi-k3` (no fallback) | `llm-env-moonshot` (OpenAI-compatible, `https://api.moonshot.ai/v1`) |

`llm-env-validation` and `llm-env-planning` are provisioned only when
`omniroute.providers.openai.api_key` / `omniroute.providers.moonshot.api_key`
are set in `models.yml` — both default to empty and are silently skipped
otherwise (`provision()`'s return JSON reports
`{"action": "skipped", "reason": "..."}` for whichever piece was skipped).

Reasoning effort/thinking budget is **not** configurable per combo or
connection in OmniRoute — the only related control,
`PUT /api/settings/thinking-budget`, is instance-wide. A caller (e.g.
GSD-Pi's `PREFERENCES.md` phase config) that wants a specific effort level
from `llm-env-validation` must set it in its own request, the same way it
already sets `max_tokens`/`temperature` today.

See `docs/superpowers/specs/2026-08-12-omniroute-profiles-design.md` for
the full design rationale and the live-verified Combo API shape (the
bundled `/openapi.yaml` on the instance is stale for `/api/combos`).
```

- [ ] **Step 7: Run the full project gate**

Run: `make validate && make test`
Expected: all checks and tests pass (this repo's full suite, currently
979 tests before this plan's additions).

- [ ] **Step 8: Commit**

```bash
git add llmenv.py tests/test_cli.py .agents/architecture.md
git commit -m "feat(omniroute): wire provider keys through the CLI and document the profiles"
```
