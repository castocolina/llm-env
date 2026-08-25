"""Shared HTTP client for OmniRoute's admin API.

OmniRoute's management API (/api/*) accepts either a dashboard session or a
Bearer API key with "manage" scope -- it does NOT recognize any machine-auth
header keyed on a standalone CLI token (verified live against a running
instance; no such mechanism exists in OmniRoute's own authorization docs).
This module logs in with the dashboard password (POST /api/auth/login) and
reuses the resulting session cookie, since that is the credential this
deployment actually has. Uses only the standard library so this repo's one
Python dependency (pyyaml) does not grow.

Every other pylib/omniroute_*.py module (provisioning, Codex import, context
overrides, combo backup/restore) is a separate feature built on top of this
one -- this is the only module that talks HTTP.
"""

from __future__ import annotations

import http.cookies
import json
import urllib.error
import urllib.request
from typing import Any


class OmniRouteError(Exception):
    """Raised when OmniRoute's admin API cannot be reached or misbehaves."""


def login(base_url: str, password: str) -> str:
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


def request_json(
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


def describe_unexpected_shape(payload: Any) -> str:
    """Describe only the type/shape of an unexpected payload, never its
    contents -- it may carry provider secrets (e.g. an apiKey), and callers'
    error messages can end up in stderr/the systemd journal via scripts/*.sh."""
    if isinstance(payload, dict):
        return f"dict with keys {sorted(payload.keys())!r}"
    return type(payload).__name__
