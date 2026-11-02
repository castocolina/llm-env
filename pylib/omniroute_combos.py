"""Backup and restore of OmniRoute combos (not the full-DB export).

OmniRoute's dashboard "backup" button (Settings -> System/Storage) calls
GET /api/db-backups/export, which snapshots the *entire* SQLite database --
every connection, every API key, everything -- via a raw db.backup(). For a
lighter, combo-scoped backup/restore this module uses the purpose-built
GET/POST/PUT /api/combos endpoints instead, matching pylib/omniroute.py's
_login()/_request() session-cookie pattern but kept in its own module since
combo backup/restore is a distinct concern from connection provisioning,
Codex session import, or context-window correction.

Also backs up provider-connection *metadata* (backup_connections) -- not a
secret-preserving backup (OmniRoute's API never exposes raw credentials),
but enough (provider, name, id) to remap a restored combo's stale
connectionId references onto whatever live connection currently matches, so
restore_combos() stays correct after e.g. `make clean` regenerates
connections with new ids.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pylib.omniroute import (
    OmniRouteError,
    _extract_providers,
    _login,
    _request,
    find_connection,
)

# Fields accepted by POST /api/combos and PUT /api/combos/{id} (see
# createComboSchema in OmniRoute's own source). Backing up and restoring
# only this subset -- and dropping DB-internal fields like id, isHidden,
# sortOrder, createdAt, updatedAt, version, computed_context_length --
# keeps a restored combo a clean re-creation rather than a raw row replay.
_RESTORABLE_COMBO_FIELDS = (
    "name",
    "description",
    "models",
    "strategy",
    "config",
    "allowedProviders",
    "allowedModelFamilies",
    "system_message",
    "tool_filter_regex",
    "context_cache_protection",
    "context_length",
    "dimensions",
)


def _combo_payload(combo: dict[str, Any]) -> dict[str, Any]:
    return {field: combo[field] for field in _RESTORABLE_COMBO_FIELDS if field in combo}


def backup_combos(base_url: str, dashboard_password: str, output_path: Path) -> dict[str, Any]:
    """Write every combo's restorable fields to output_path as JSON.

    Idempotent and read-only against OmniRoute -- safe to run repeatedly.
    """
    session_token = _login(base_url, dashboard_password)
    payload = _request("GET", f"{base_url}/api/combos", session_token)
    combos = payload.get("combos") if isinstance(payload, dict) else None
    if not isinstance(combos, list):
        raise OmniRouteError("unexpected /api/combos response shape")

    backup = {
        "backed_up_at": datetime.now(UTC).isoformat(),
        "combos": [_combo_payload(combo) for combo in combos if isinstance(combo, dict)],
    }
    output_path.write_text(json.dumps(backup, indent=2) + "\n")
    return {"path": str(output_path), "count": len(backup["combos"])}


def backup_connections(base_url: str, dashboard_password: str, output_path: Path) -> dict[str, Any]:
    """Write provider-connection metadata (provider, name, id only) to output_path.

    This is NOT a secret-preserving backup -- GET /api/providers never returns
    raw credentials (OAuth connections, e.g. codex/grok-cli, carry no token
    field at all; API-key connections have their key masked, e.g.
    "sk-q4X8A****0sM8"), so there is no way to restore a connection's actual
    auth from this API. What this metadata DOES enable: restore_combos()
    remapping a backed-up combo's stale connectionId references onto
    whatever live connection currently has the same (provider, name) --
    without it, restoring combos after e.g. `make clean` would leave every
    member pointing at a connectionId that no longer exists.
    """
    session_token = _login(base_url, dashboard_password)
    listing = _request("GET", f"{base_url}/api/providers", session_token)
    connections = _extract_providers(listing)

    backup = {
        "backed_up_at": datetime.now(UTC).isoformat(),
        "connections": [
            {"provider": c["provider"], "name": c["name"], "id": c["id"]}
            for c in connections
            if isinstance(c, dict)
            and isinstance(c.get("provider"), str)
            and isinstance(c.get("name"), str)
            and isinstance(c.get("id"), str)
        ],
    }
    output_path.write_text(json.dumps(backup, indent=2) + "\n")
    return {"path": str(output_path), "count": len(backup["connections"])}


def _remap_combo_connection_ids(
    combo: dict[str, Any], connection_index: dict[tuple[str, str], str]
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Point each member's connectionId at the current live connection.

    A backed-up combo's connectionId is only ever valid for the OmniRoute
    instance it was captured from -- after e.g. `make clean` regenerates
    connections, those ids no longer exist. Each member is instead resolved
    by (providerId, label): OmniRoute stamps a member's "label" from the
    connection's own "name" at combo-creation time, so it doubles as a
    stable cross-instance key. A member with no match has its connectionId
    cleared (None) rather than left stale, letting OmniRoute fall back to
    its own default connection for that provider; it is also reported back
    as unresolved so the caller can flag it.
    """
    if "models" not in combo:
        return combo, []

    unresolved: list[dict[str, str]] = []
    remapped_models = []
    for member in combo.get("models") or []:
        if not isinstance(member, dict) or not member.get("connectionId"):
            remapped_models.append(member)
            continue
        member = dict(member)
        provider_id = member.get("providerId")
        label = member.get("label")
        live_id = None
        if isinstance(provider_id, str) and isinstance(label, str):
            live_id = connection_index.get((provider_id, label))
        if live_id is not None:
            member["connectionId"] = live_id
        else:
            member["connectionId"] = None
            unresolved.append(
                {"provider": str(provider_id), "label": str(label), "model": str(member.get("model"))}
            )
        remapped_models.append(member)
    combo = dict(combo)
    combo["models"] = remapped_models
    return combo, unresolved


def restore_combos(
    base_url: str, dashboard_password: str, input_path: Path, *, overwrite: bool = False
) -> list[dict[str, Any]]:
    """Restore combos from a backup_combos() JSON file.

    A combo whose name already exists is left untouched unless overwrite=True,
    in which case it is updated in place via PUT (preserving its id and any
    routing state other than the restored fields). New combos are always
    created via POST. Idempotent either way: re-running without overwrite
    only ever fills in what's missing.

    Each member's connectionId is remapped to the current live connection
    matching its (provider, label) -- see _remap_combo_connection_ids -- since
    a raw backed-up connectionId is almost certainly stale by restore time.
    """
    try:
        backup = json.loads(input_path.read_text())
    except json.JSONDecodeError as err:
        raise OmniRouteError(f"{input_path} is not valid JSON") from err
    combos = backup.get("combos") if isinstance(backup, dict) else None
    if not isinstance(combos, list):
        raise OmniRouteError("unexpected backup file shape")

    session_token = _login(base_url, dashboard_password)
    existing_payload = _request("GET", f"{base_url}/api/combos", session_token)
    existing = existing_payload.get("combos") if isinstance(existing_payload, dict) else None
    if not isinstance(existing, list):
        raise OmniRouteError("unexpected /api/combos response shape")
    existing_by_name = {
        combo["name"]: combo for combo in existing if isinstance(combo, dict) and "name" in combo
    }

    connections_listing = _request("GET", f"{base_url}/api/providers", session_token)
    connections = _extract_providers(connections_listing)
    connection_index = {
        (c["provider"], c["name"]): c["id"]
        for c in connections
        if isinstance(c, dict)
        and isinstance(c.get("provider"), str)
        and isinstance(c.get("name"), str)
        and isinstance(c.get("id"), str)
    }

    results: list[dict[str, Any]] = []
    for combo in combos:
        if not isinstance(combo, dict) or "name" not in combo:
            continue
        name = combo["name"]
        combo, unresolved = _remap_combo_connection_ids(combo, connection_index)
        payload = _combo_payload(combo)
        current = existing_by_name.get(name)
        if current is None:
            _request("POST", f"{base_url}/api/combos", session_token, payload)
            action = "created"
        elif overwrite:
            _request("PUT", f"{base_url}/api/combos/{current['id']}", session_token, payload)
            action = "updated"
        else:
            action = "skipped"
        result = {"combo": name, "action": action}
        if unresolved:
            result["unresolved_connections"] = unresolved
        results.append(result)
    return results


LOCAL_COMBO_NAME = "my-coding-lite"
_MEMBER_KEYS = ("model", "providerId", "connectionId", "label")


def local_combo_payload(alias: str, connection_id: str, context_length: int) -> dict[str, Any]:
    # Task 0 spike: OmniRoute accepted and persisted member id + context_length, no `config`.
    return {
        "name": LOCAL_COMBO_NAME,
        "strategy": "priority",
        "models": [
            {
                "id": f"{LOCAL_COMBO_NAME}-model-1-llama-cpp-{alias.replace('.', '-')}-{connection_id}",
                "kind": "model",
                "model": f"llama-cpp/{alias}",
                "providerId": "llama-cpp",
                "connectionId": connection_id,
                "weight": 0,
                "label": "llm-env-local",
            }
        ],
        "context_length": context_length,
    }


def _members(combo: dict[str, Any]) -> list[tuple[Any, ...]]:
    return [
        tuple(m.get(k) for k in _MEMBER_KEYS) for m in combo.get("models") or [] if isinstance(m, dict)
    ]


def _matches(existing: dict[str, Any], desired: dict[str, Any]) -> bool:
    # Keys the payload omits (OmniRoute-added `config`, ...) are ignored; member `id` is ignored.
    scalars = all(existing.get(k) == v for k, v in desired.items() if k not in ("name", "models"))
    return scalars and _members(existing) == _members(desired)


def ensure_local_combo(
    base_url: str, dashboard_password: str, alias: str, context_length: int
) -> dict[str, Any]:
    """Create or update the local-only my-coding-lite combo; the tool owns it, so drift is overwritten."""
    result: dict[str, Any] = {
        "combo": LOCAL_COMBO_NAME,
        "model": f"llama-cpp/{alias}",
        "context_length": context_length,
    }
    session_token = _login(base_url, dashboard_password)
    connection = find_connection(
        _extract_providers(_request("GET", f"{base_url}/api/providers", session_token))
    )
    connection_id = connection.get("id") if connection else None
    if not isinstance(connection_id, str) or not connection_id:
        return {**result, "action": "skipped", "reason": "no llm-env-local connection; run make start-llama"}
    desired = local_combo_payload(alias, connection_id, context_length)
    listing = _request("GET", f"{base_url}/api/combos", session_token)
    combos = listing.get("combos") if isinstance(listing, dict) else None
    if not isinstance(combos, list):
        raise OmniRouteError("unexpected /api/combos response shape")
    existing = next((c for c in combos if isinstance(c, dict) and c.get("name") == LOCAL_COMBO_NAME), None)
    if existing is None:
        _request("POST", f"{base_url}/api/combos", session_token, desired)
        return {**result, "action": "created"}
    if _matches(existing, desired):
        return {**result, "action": "unchanged"}
    _request("PUT", f"{base_url}/api/combos/{existing['id']}", session_token, desired)
    return {**result, "action": "updated"}
