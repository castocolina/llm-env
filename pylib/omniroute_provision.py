"""Idempotent provisioning of OmniRoute's connection to the local router."""

from __future__ import annotations

from typing import Any

from pylib.omniroute_client import (
    OmniRouteError,
    describe_unexpected_shape,
    login,
    request_json,
)

CONNECTION_NAME = "llm-env-local"


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
    raise OmniRouteError(f"unexpected provider listing shape: {describe_unexpected_shape(payload)}")


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
    session_token = login(base_url, dashboard_password)
    listing = request_json("GET", f"{base_url}/api/providers", session_token)
    providers = _extract_providers(listing)
    payload = build_payload(port, api_key)

    existing = find_connection(providers)
    if existing is None:
        created = request_json("POST", f"{base_url}/api/providers", session_token, payload)
        # A live instance nests the new record under "connection" (verified
        # against a running container); fall back to a bare id for safety.
        created_id = None
        if isinstance(created, dict):
            connection = created.get("connection")
            created_id = (
                connection.get("id") if isinstance(connection, dict) else created.get("id")
            )
        return {"action": "created", "id": created_id}

    provider_id = existing.get("id")
    if not isinstance(provider_id, (str, int)):
        raise OmniRouteError(f"existing {CONNECTION_NAME!r} connection has no usable id")
    request_json("PUT", f"{base_url}/api/providers/{provider_id}", session_token, payload)
    return {"action": "updated", "id": provider_id}
