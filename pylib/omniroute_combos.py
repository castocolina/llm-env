"""Backup and restore OmniRoute Combos (model routing groups).

Combos here are whatever the user has actually configured in the OmniRoute
dashboard (e.g. "my-planning", "my-plan-review", "my-coding") -- this module
round-trips *existing* combos by name; it does not create or assume any
fixed combo scheme.

Backup captures each combo's name, model list, and strategy -- deliberately
NOT its id, since ids are only meaningful on the instance that issued them.
Restore re-resolves each combo by name (creating it if it no longer exists,
the same idempotent create-or-update pattern as
pylib.omniroute_provision.provision) and PUTs the saved model list back.
This makes a backup/restore round-trip safe on the SAME instance -- the
supported use case is "undo my own bad combo edit" -- but a backup is not
portable to a different instance whose connection ids differ, since each
combo model entry pins a connectionId.
"""

from __future__ import annotations

from typing import Any

from pylib.omniroute_client import (
    OmniRouteError,
    describe_unexpected_shape,
    login,
    request_json,
)


def _extract_combos(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        value = payload.get("combos")
        if isinstance(value, list):
            return value
    if isinstance(payload, list):
        return payload
    raise OmniRouteError(f"unexpected combo listing shape: {describe_unexpected_shape(payload)}")


def find_combo(combos: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    return next((c for c in combos if c.get("name") == name), None)


def list_combos(base_url: str, dashboard_password: str) -> list[dict[str, Any]]:
    session_token = login(base_url, dashboard_password)
    listing = request_json("GET", f"{base_url}/api/combos", session_token)
    return _extract_combos(listing)


def backup_combos(base_url: str, dashboard_password: str) -> list[dict[str, Any]]:
    """Return a restorable snapshot of every combo on the live instance:
    name + models (+ strategy, when present), stripped of the instance's own
    ids so restore_combos() can re-resolve them by name."""
    snapshot: list[dict[str, Any]] = []
    for combo in list_combos(base_url, dashboard_password):
        name = combo.get("name")
        if not isinstance(name, str):
            continue
        entry: dict[str, Any] = {"name": name, "models": combo.get("models") or []}
        if "strategy" in combo:
            entry["strategy"] = combo["strategy"]
        snapshot.append(entry)
    return snapshot


def restore_combos(
    base_url: str, dashboard_password: str, snapshot: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Restore a snapshot produced by backup_combos(). For each entry: find
    the combo by name (create it if it's gone), then PUT its saved model
    list -- idempotent, so re-running a restore is always safe."""
    session_token = login(base_url, dashboard_password)
    combos = _extract_combos(request_json("GET", f"{base_url}/api/combos", session_token))

    results: list[dict[str, Any]] = []
    for entry in snapshot:
        name = entry.get("name")
        if not isinstance(name, str):
            raise OmniRouteError("backup entry is missing a combo name")
        models = entry.get("models") or []

        existing = find_combo(combos, name)
        if existing is None:
            create_payload = {"name": name, "strategy": entry.get("strategy", "priority")}
            created = request_json("POST", f"{base_url}/api/combos", session_token, create_payload)
            combo_id = created.get("id") if isinstance(created, dict) else None
            if not isinstance(combo_id, (str, int)):
                raise OmniRouteError(f"combo {name!r} creation returned no usable id")
            action = "created"
        else:
            combo_id = existing.get("id")
            if not isinstance(combo_id, (str, int)):
                raise OmniRouteError(f"existing combo {name!r} has no usable id")
            action = "updated"

        request_json("PUT", f"{base_url}/api/combos/{combo_id}", session_token, {"models": models})
        results.append({"combo": name, "action": action, "id": combo_id, "models": len(models)})
    return results
