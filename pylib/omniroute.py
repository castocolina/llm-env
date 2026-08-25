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

from __future__ import annotations

import http.cookies
import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

CONNECTION_NAME = "llm-env-local"

# OpenAI's own GPT-5.6 spec (raw context window: 1,050,000 tokens) is shared
# identically across the "sol"/"terra"/"luna" branded variants and the bare
# "gpt-5.6" id -- confirmed both in OmniRoute's own bundled model spec and
# independently via live web research (OpenAI/Codex community reporting,
# August 2026). Codex CLI's *product* layer defaults to a much more
# conservative 272,000-token window for these models (a client-side choice,
# not an upstream API limit -- see openai/codex#32806); OmniRoute's synced
# catalog inherits that same conservative number for any effort-suffixed
# variant id (e.g. "gpt-5.6-sol-high"), because its alias-matching only
# recognizes the bare family name. Verified live against a real OmniRoute
# instance: a 591,211-token request to "cx/gpt-5.6-sol-high" completed
# successfully once the override below was applied, confirming the true
# ceiling is far above 272,000 for this account.
CODEX_GPT_5_6_CONTEXT_WINDOW = 1_050_000
CODEX_GPT_5_6_FAMILIES = ("gpt-5.6", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna")
CODEX_EFFORT_SUFFIXES = ("low", "medium", "high", "xhigh", "max", "ultra")

# grok-composer-2.5-fast is genuinely served through xAI's "Grok Build" CLI/API
# integration at a 200,000-token window (confirmed via live web research,
# August 2026 sources) -- this is correct, not a bug, and matches OmniRoute's
# own hardcoded registry entry for it (open-sse/config/providers/registry/
# grok-cli/index.ts). It never appears in /v1/models at all, though (neither
# via API key nor the dashboard session cookie): OmniRoute's Grok Build
# normalizer deliberately hides supported_in_api=false models from the
# catalog while still routing combo requests to them. Without this constant,
# a combo containing it would silently drop it from the min() calculation --
# understating risk for the exact combo ("my-coding") this feature exists to
# get right.
GROK_COMPOSER_2_5_FAST_CONTEXT_WINDOW = 200_000


class OmniRouteError(Exception):
    """Raised when OmniRoute's admin API cannot be reached or misbehaves."""


def _login(base_url: str, password: str) -> str:
    """Authenticate with the dashboard password; return the auth_token cookie value."""
    data = json.dumps({"password": password}).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url}/api/auth/login",
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            response.read()
            cookie_headers = response.headers.get_all("Set-Cookie") or []
    except urllib.error.HTTPError as exc:
        raise OmniRouteError(f"login failed: HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise OmniRouteError(f"login failed: {exc.reason}") from exc
    cookies: http.cookies.SimpleCookie = http.cookies.SimpleCookie()
    for header in cookie_headers:
        cookies.load(header)
    if "auth_token" not in cookies:
        raise OmniRouteError("login succeeded but no session cookie was issued")
    return cookies["auth_token"].value


def _request(
    method: str, url: str, session_token: str, payload: dict[str, Any] | None = None
) -> Any:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Cookie", f"auth_token={session_token}")
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            body = response.read()
    except urllib.error.HTTPError as exc:
        # OmniRoute's API routes always shape a failure body as {"error": "..."},
        # a short, purpose-written message (e.g. "session expired, re-authenticate")
        # -- never raw connection data -- so surfacing just that field is safe and
        # far more actionable than a bare status code. Any other body shape (or an
        # unparseable one) falls back to the status code alone, never the raw body.
        detail = ""
        try:
            error_body = json.loads(exc.read())
            if isinstance(error_body, dict) and isinstance(error_body.get("error"), str):
                detail = f": {error_body['error']}"
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
            pass
        raise OmniRouteError(f"{method} {url} failed: HTTP {exc.code}{detail}") from exc
    except urllib.error.URLError as exc:
        raise OmniRouteError(f"{method} {url} failed: {exc.reason}") from exc
    return json.loads(body) if body else None


def _extract_providers(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        # "connections" is what a live instance actually returns (verified
        # against a running container); "providers"/"data" are kept as
        # fallbacks in case this shape varies across OmniRoute versions.
        for key in ("connections", "providers", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
    # Describe only the type/shape, never the payload itself — it may carry
    # provider secrets (e.g. an apiKey), and this message can end up in
    # stderr/the systemd journal via scripts/start.sh.
    shape = (
        f"dict with keys {sorted(payload.keys())!r}"
        if isinstance(payload, dict)
        else type(payload).__name__
    )
    raise OmniRouteError(f"unexpected provider listing shape: {shape}")


def build_payload(port: int, api_key: str) -> dict[str, Any]:
    return {
        "provider": "llama-cpp",
        "name": CONNECTION_NAME,
        # "llm-server" is the compose service's container_name, which is
        # also its DNS name on the shared podman network -- confirmed live
        # (`podman ps` shows both containers on network "llm-env_default").
        "apiKey": api_key,
        "priority": 1,
        "testStatus": "active",
        # The outbound URL lives under this nested key, not top-level --
        # confirmed by capturing the dashboard's own POST /api/providers
        # request while adding a llama.cpp connection through the UI. A
        # top-level "baseUrl"/"url" is silently dropped by the real
        # create/update endpoints despite passing the separate
        # POST /api/providers/validate syntax check.
        "providerSpecificData": {"baseUrl": f"http://llm-server:{port}/v1"},
    }


def find_connection(
    connections: list[dict[str, Any]], name: str = CONNECTION_NAME
) -> dict[str, Any] | None:
    return next((c for c in connections if c.get("name") == name), None)


def provision(base_url: str, dashboard_password: str, port: int, api_key: str) -> dict[str, Any]:
    session_token = _login(base_url, dashboard_password)
    listing = _request("GET", f"{base_url}/api/providers", session_token)
    providers = _extract_providers(listing)
    payload = build_payload(port, api_key)

    existing = find_connection(providers)
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

    provider_id = existing.get("id")
    if not isinstance(provider_id, (str, int)):
        raise OmniRouteError(
            f"existing {CONNECTION_NAME!r} connection has no usable id"
        )
    _request("PUT", f"{base_url}/api/providers/{provider_id}", session_token, payload)
    return {"action": "updated", "id": provider_id}


def import_codex_auth(
    base_url: str, dashboard_password: str, auth_json_path: Path, name: str
) -> dict[str, Any]:
    """Import a local Codex CLI session (~/.codex/auth.json) into OmniRoute
    as a named provider connection, creating it on first run and updating
    the same connection's tokens on every later run (overwriteExisting).

    Bypasses Codex's normal OAuth loopback flow entirely -- there is no
    browser callback involved, so this works whether OmniRoute is reached
    over its published dashboard port from the same host or a remote one;
    no SSH tunnel or extra port publishing is needed. Uses
    POST /api/providers/codex-auth/import (distinct from the bulk
    /api/oauth/codex/import route, which always creates a new connection
    and does not accept a custom name -- verified against a live instance).
    """
    try:
        auth_json = json.loads(auth_json_path.read_text())
    except FileNotFoundError as exc:
        raise OmniRouteError(f"no Codex session at {auth_json_path}") from exc
    except json.JSONDecodeError as exc:
        raise OmniRouteError(f"{auth_json_path} is not valid JSON") from exc

    session_token = _login(base_url, dashboard_password)
    payload = {
        "source": {"kind": "json", "json": auth_json},
        "name": name,
        "overwriteExisting": True,
    }
    result = _request(
        "POST", f"{base_url}/api/providers/codex-auth/import", session_token, payload
    )
    if not isinstance(result, dict) or "connection" not in result:
        raise OmniRouteError("unexpected response from codex-auth import")
    connection = result.get("connection") or {}
    return {
        "action": "created" if result.get("created") else "updated",
        "id": connection.get("id"),
        "name": connection.get("name"),
    }


def _codex_gpt_5_6_family(root_model_id: str) -> str | None:
    """Strip a known effort-tier suffix (e.g. "-high") off a catalog model id
    and return the base family name if it's a known GPT-5.6 variant, else
    None. "gpt-5.6-sol-high" -> "gpt-5.6-sol"; "gpt-5.6-sol" -> itself
    (models can be listed at their default effort with no suffix)."""
    for suffix in CODEX_EFFORT_SUFFIXES:
        marker = f"-{suffix}"
        if root_model_id.endswith(marker):
            base = root_model_id[: -len(marker)]
            if base in CODEX_GPT_5_6_FAMILIES:
                return base
    if root_model_id in CODEX_GPT_5_6_FAMILIES:
        return root_model_id
    return None


def fix_codex_context_overrides(base_url: str, dashboard_password: str) -> list[dict[str, Any]]:
    """Correct OmniRoute's understanding of the codex GPT-5.6 family's real
    context window (see CODEX_GPT_5_6_CONTEXT_WINDOW's comment for why this
    is needed and how it was verified). Scans the live catalog for every
    codex-owned model id -- current and future effort-tier variants alike --
    and sets a manual context-window override for each GPT-5.6 family member
    found, so this never needs to be repeated by hand per combo/model.

    Idempotent: OmniRoute upserts the underlying override row, so re-running
    this is always safe and just re-affirms the same value.
    """
    session_token = _login(base_url, dashboard_password)
    catalog = _request("GET", f"{base_url}/v1/models", session_token)
    entries = catalog.get("data") if isinstance(catalog, dict) else None
    if not isinstance(entries, list):
        raise OmniRouteError("unexpected /v1/models response shape")

    seen_roots: set[str] = set()
    results: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("owned_by") != "codex":
            continue
        root = entry.get("root")
        if not isinstance(root, str) or root in seen_roots:
            continue
        seen_roots.add(root)
        family = _codex_gpt_5_6_family(root)
        if family is None:
            continue
        payload = {
            "provider": "codex",
            "modelId": root,
            "contextWindowOverride": CODEX_GPT_5_6_CONTEXT_WINDOW,
        }
        _request("PUT", f"{base_url}/api/provider-models", session_token, payload)
        results.append(
            {"model": root, "family": family, "context_window": CODEX_GPT_5_6_CONTEXT_WINDOW}
        )
    return results


def _resolve_member_context_window(
    catalog_context_by_root: dict[str, int], provider_id: str, model_id: str
) -> int | None:
    """Resolve a combo member's real context window.

    OmniRoute's /v1/models catalog never reflects manual context-window
    overrides (confirmed by reading OmniRoute's own source: catalog.ts
    resolves "synced -> registry -> spec", the same chain as the buggy
    /api/combos "computed_context_length" field, and never consults the
    override table) -- so a codex GPT-5.6 family member is corrected here
    using the exact same value fix_codex_context_overrides() persists,
    instead of trusting the catalog's stale number for it.
    """
    if provider_id == "codex" and _codex_gpt_5_6_family(model_id) is not None:
        return CODEX_GPT_5_6_CONTEXT_WINDOW
    if provider_id == "grok-cli" and model_id == "grok-composer-2.5-fast":
        return GROK_COMPOSER_2_5_FAST_CONTEXT_WINDOW
    return catalog_context_by_root.get(model_id)


def compute_combo_context(
    base_url: str, dashboard_password: str, combo_name: str | None = None
) -> list[dict[str, Any]]:
    """Compute each combo's real minimum context window across its members.

    OmniRoute's own /api/combos "computed_context_length" field is not
    trustworthy: it short-circuits on the same stale catalog value described
    in _resolve_member_context_window's docstring, so it never reflects a
    manual override (see fix_codex_context_overrides). This walks the live
    catalog and combo membership directly and applies the known corrections
    itself, so the result is the actual minimum an orchestrator can rely on
    to decide when to split a request before the provider ever rejects it.

    Raises OmniRouteError if combo_name is given but no combo has that name.
    """
    session_token = _login(base_url, dashboard_password)
    catalog = _request("GET", f"{base_url}/v1/models", session_token)
    entries = catalog.get("data") if isinstance(catalog, dict) else None
    if not isinstance(entries, list):
        raise OmniRouteError("unexpected /v1/models response shape")

    catalog_context_by_root: dict[str, int] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        root = entry.get("root")
        context_length = entry.get("context_length")
        if (
            isinstance(root, str)
            and isinstance(context_length, int)
            and root not in catalog_context_by_root
        ):
            catalog_context_by_root[root] = context_length

    combos_payload = _request("GET", f"{base_url}/api/combos", session_token)
    combos = combos_payload.get("combos") if isinstance(combos_payload, dict) else None
    if not isinstance(combos, list):
        raise OmniRouteError("unexpected /api/combos response shape")

    results: list[dict[str, Any]] = []
    for combo in combos:
        if not isinstance(combo, dict):
            continue
        name = combo.get("name")
        if not isinstance(name, str):
            continue
        if combo_name is not None and name != combo_name:
            continue

        members: list[dict[str, Any]] = []
        for member in combo.get("models") or []:
            if not isinstance(member, dict) or member.get("kind") != "model":
                continue
            provider_id = member.get("providerId")
            model_field = member.get("model")
            if not isinstance(provider_id, str) or not isinstance(model_field, str):
                continue
            model_id = model_field.split("/", 1)[-1]
            context_window = _resolve_member_context_window(
                catalog_context_by_root, provider_id, model_id
            )
            members.append(
                {"provider": provider_id, "model": model_id, "context_window": context_window}
            )

        known_windows = [
            member["context_window"]
            for member in members
            if isinstance(member["context_window"], int)
        ]
        results.append(
            {
                "combo": name,
                "members": members,
                "min_context_window": min(known_windows) if known_windows else None,
            }
        )

    if combo_name is not None and not results:
        raise OmniRouteError(f"no combo named {combo_name!r} found")
    return results
