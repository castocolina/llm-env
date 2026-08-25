"""Correct OmniRoute's catalog understanding of the codex GPT-5.6 family's
real context window.

OpenAI's own GPT-5.6 spec (raw context window: 1,050,000 tokens) is shared
identically across the "sol"/"terra"/"luna" branded variants and the bare
"gpt-5.6" id -- confirmed both in OmniRoute's own bundled model spec and
independently via live web research (OpenAI/Codex community reporting,
August 2026). Codex CLI's *product* layer defaults to a much more
conservative 272,000-token window for these models (a client-side choice,
not an upstream API limit -- see openai/codex#32806); OmniRoute's synced
catalog inherits that same conservative number for any effort-suffixed
variant id (e.g. "gpt-5.6-sol-high"), because its alias-matching only
recognizes the bare family name. Verified live against a real OmniRoute
instance: a 591,211-token request to "cx/gpt-5.6-sol-high" completed
successfully once the override below was applied, confirming the true
ceiling is far above 272,000 for this account.
"""

from __future__ import annotations

from typing import Any

from pylib.omniroute_client import OmniRouteError, login, request_json

CODEX_GPT_5_6_CONTEXT_WINDOW = 1_050_000
CODEX_GPT_5_6_FAMILIES = ("gpt-5.6", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna")
CODEX_EFFORT_SUFFIXES = ("low", "medium", "high", "xhigh", "max", "ultra")


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
    context window (see this module's docstring for why this is needed and
    how it was verified). Scans the live catalog for every codex-owned model
    id -- current and future effort-tier variants alike -- and sets a manual
    context-window override for each GPT-5.6 family member found, so this
    never needs to be repeated by hand per combo/model.

    Idempotent: OmniRoute upserts the underlying override row, so re-running
    this is always safe and just re-affirms the same value.
    """
    session_token = login(base_url, dashboard_password)
    catalog = request_json("GET", f"{base_url}/v1/models", session_token)
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
        request_json("PUT", f"{base_url}/api/provider-models", session_token, payload)
        results.append(
            {"model": root, "family": family, "context_window": CODEX_GPT_5_6_CONTEXT_WINDOW}
        )
    return results
