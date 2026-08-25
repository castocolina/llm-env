"""Import a local Codex CLI session into OmniRoute as a named provider connection."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pylib.omniroute_client import OmniRouteError, login, request_json


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

    session_token = login(base_url, dashboard_password)
    payload = {
        "source": {"kind": "json", "json": auth_json},
        "name": name,
        "overwriteExisting": True,
    }
    result = request_json(
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
