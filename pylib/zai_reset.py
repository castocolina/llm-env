"""Redeem a z.ai weekly-quota reset card when the weekly quota is (nearly) spent.

Runs from a 5-minute systemd --user timer (scripts/zai-reset-weekly.sh). For every active z.ai/GLM connection it reads
the weekly quota OmniRoute reports (GET /api/usage/<id>) and, once used/total reaches THRESHOLD_PERCENT, redeems one
WEEK reset card through OmniRoute (POST /api/usage/glm-reset-card). A card is a finite resource, so it is spent at most
once per weekly window: the window's `resetAt` is recorded after a successful redeem and the idempotency key is derived
from it, so a retry after a timeout cannot spend a second card for the same window.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pylib.omniroute import OmniRouteError, _extract_providers, _login, _request

ZAI_PROVIDERS = ("zai", "glm", "glm-cn", "glmt")
THRESHOLD_PERCENT = 99
CARD_TYPE = "WEEK"


def _read_state(path: Path) -> dict[str, Any]:
    try:
        state = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return state if isinstance(state, dict) else {}


def _write_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    with os.fdopen(fd, "w") as handle:  # mkstemp creates the file 0600
        json.dump(state, handle)
    os.replace(tmp, path)


def _weekly(usage: Any) -> tuple[float, float, str] | None:
    quotas = usage.get("quotas") if isinstance(usage, dict) else None
    weekly = quotas.get("weekly") if isinstance(quotas, dict) else None
    if not isinstance(weekly, dict):
        return None
    used, total, window = weekly.get("used"), weekly.get("total"), weekly.get("resetAt")
    if not (isinstance(used, (int, float)) and isinstance(total, (int, float)) and total > 0):
        return None
    return float(used), float(total), window if isinstance(window, str) else ""


def _week_card(cards: Any) -> dict[str, Any] | None:
    credits = cards.get("credits") if isinstance(cards, dict) else None
    usable = [
        card for card in credits or []
        if isinstance(card, dict) and card.get("resetType") == CARD_TYPE and card.get("selectionToken")
    ]
    return min(usable, key=lambda card: str(card.get("expiresAt")), default=None)  # soonest-expiring first


def _check_connection(base_url: str, token: str, connection: dict[str, Any], state: dict[str, Any],
                      state_path: Path, dry_run: bool) -> dict[str, Any]:
    cid = connection["id"]
    out: dict[str, Any] = {"connection": connection.get("name") or cid}
    weekly = _weekly(_request("GET", f"{base_url}/api/usage/{cid}", token))
    if weekly is None:
        return {**out, "action": "none", "reason": "no_weekly_quota"}
    used, total, window = weekly
    percent = round(100 * used / total, 1)
    out.update(used_percent=percent, window=window)
    if percent < THRESHOLD_PERCENT:
        return {**out, "action": "none", "reason": "below_threshold"}
    if window and state.get(cid, {}).get("window") == window:
        return {**out, "action": "none", "reason": "already_redeemed_this_window"}
    card = _week_card(_request("GET", f"{base_url}/api/usage/glm-reset-card?connectionId={cid}", token))
    if card is None:
        return {**out, "action": "none", "reason": "no_week_card"}
    if dry_run:
        return {**out, "action": "would_redeem", "expires_at": card.get("expiresAt")}
    try:
        _request("POST", f"{base_url}/api/usage/glm-reset-card", token, {
            "connectionId": cid,
            "idempotencyKey": f"llm-env-zai-reset:{cid}:{window}",
            "creditId": card["selectionToken"],
        })
    except OmniRouteError as exc:  # not recorded: the next pass retries with the same idempotency key
        return {**out, "action": "error", "error": str(exc)}
    state[cid] = {"window": window, "redeemed_at": datetime.now(UTC).isoformat(timespec="seconds")}
    _write_state(state_path, state)
    return {**out, "action": "redeemed"}


def check(base_url: str, password: str, state_path: Path, *, dry_run: bool = False) -> list[dict[str, Any]]:
    """One pass over every active z.ai connection. A failed redeem is reported and retried on the next pass."""
    token = _login(base_url, password)
    connections = [
        c for c in _extract_providers(_request("GET", f"{base_url}/api/providers", token))
        if c.get("provider") in ZAI_PROVIDERS and c.get("isActive") and c.get("id")
    ]
    state = _read_state(state_path)
    results = []
    for connection in connections:
        try:
            results.append(_check_connection(base_url, token, connection, state, state_path, dry_run))
        except OmniRouteError as exc:
            results.append({"connection": connection.get("name") or connection["id"], "action": "error", "error": str(exc)})
    return results
