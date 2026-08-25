"""Backup and restore of OmniRoute combos (not the full-DB export).

OmniRoute's dashboard "backup" button (Settings -> System/Storage) calls
GET /api/db-backups/export, which snapshots the *entire* SQLite database --
every connection, every API key, everything -- via a raw db.backup(). For a
lighter, combo-scoped backup/restore this module uses the purpose-built
GET/POST/PUT /api/combos endpoints instead, matching pylib/omniroute.py's
_login()/_request() session-cookie pattern but kept in its own module since
combo backup/restore is a distinct concern from connection provisioning,
Codex session import, or context-window correction.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pylib.omniroute import OmniRouteError, _login, _request

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


def restore_combos(
    base_url: str, dashboard_password: str, input_path: Path, *, overwrite: bool = False
) -> list[dict[str, Any]]:
    """Restore combos from a backup_combos() JSON file.

    A combo whose name already exists is left untouched unless overwrite=True,
    in which case it is updated in place via PUT (preserving its id and any
    routing state other than the restored fields). New combos are always
    created via POST. Idempotent either way: re-running without overwrite
    only ever fills in what's missing.
    """
    backup = json.loads(input_path.read_text())
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

    results: list[dict[str, Any]] = []
    for combo in combos:
        if not isinstance(combo, dict) or "name" not in combo:
            continue
        name = combo["name"]
        payload = _combo_payload(combo)
        current = existing_by_name.get(name)
        if current is None:
            _request("POST", f"{base_url}/api/combos", session_token, payload)
            results.append({"combo": name, "action": "created"})
        elif overwrite:
            _request("PUT", f"{base_url}/api/combos/{current['id']}", session_token, payload)
            results.append({"combo": name, "action": "updated"})
        else:
            results.append({"combo": name, "action": "skipped"})
    return results
