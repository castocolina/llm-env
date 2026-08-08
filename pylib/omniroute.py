"""Idempotent provisioning of OmniRoute's connection to the local router.

Talks to OmniRoute's admin API using its machine-auth `x-omniroute-cli-token`
header. See this plan's "Verify During Implementation" section for what is
confirmed vs. still assumed about this API's exact shape. Uses only the
standard library so this repo's one Python dependency (pyyaml) does not grow.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

CONNECTION_NAME = "llm-env-local"


class OmniRouteError(Exception):
    """Raised when OmniRoute's admin API cannot be reached or misbehaves."""


def _request(
    method: str, url: str, cli_token: str, payload: dict[str, Any] | None = None
) -> Any:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("x-omniroute-cli-token", cli_token)
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
        for key in ("providers", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
    raise OmniRouteError(f"unexpected provider listing shape: {payload!r}")


def build_payload(port: int, api_key: str) -> dict[str, Any]:
    return {
        "provider": "llama-cpp",
        "name": CONNECTION_NAME,
        "url": f"http://llm-server:{port}/v1",
        "apiKey": api_key,
        "isActive": True,
    }


def find_connection(
    connections: list[dict[str, Any]], name: str = CONNECTION_NAME
) -> dict[str, Any] | None:
    return next((c for c in connections if c.get("name") == name), None)


def provision(base_url: str, cli_token: str, port: int, api_key: str) -> dict[str, Any]:
    listing = _request("GET", f"{base_url}/api/providers", cli_token)
    providers = _extract_providers(listing)
    payload = build_payload(port, api_key)

    existing = find_connection(providers)
    if existing is None:
        created = _request("POST", f"{base_url}/api/providers", cli_token, payload)
        created_id = created.get("id") if isinstance(created, dict) else None
        return {"action": "created", "id": created_id}

    provider_id = existing.get("id")
    _request("PUT", f"{base_url}/api/providers/{provider_id}", cli_token, payload)
    return {"action": "updated", "id": provider_id}
