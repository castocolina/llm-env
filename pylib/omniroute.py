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
from typing import Any

CONNECTION_NAME = "llm-env-local"


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
        raise OmniRouteError(f"{method} {url} failed: HTTP {exc.code}") from exc
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
