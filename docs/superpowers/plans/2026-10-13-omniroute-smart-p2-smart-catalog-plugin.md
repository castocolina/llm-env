# OmniRoute Smart Routing P2: `smart-catalog` Plugin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the `smart-catalog` OmniRoute plugin, which scores combo members by capability, records discovery data, keeps model ids usable and bumps combo members with an audit trail. Also ship the llm-env side: the `smart_routing` settings schema rendered to `settings.json`, the post-start sync that installs and activates the plugin, `make smart-probe` for the data sources and Laya, and `check-server` rows.

**Architecture:**
- **Plugin.** `smart-catalog` is a self-contained plugin directory (`omniroute-smart/plugins/smart-catalog/`) baked into the derived image from P1. OmniRoute installs it by copying the directory, so it must not import anything outside itself.
  - It runs in OmniRoute's plugin child process. A 5-minute tick decides whether a sync is due.
  - A sync reads three sources: Artificial Analysis (free API), models.dev (`api.json`), and OmniRoute's own Arena ELO rows. The Arena rows are read straight from `storage.sqlite` with `node:sqlite`, read-only.
  - A sync then reads combos and the catalog through OmniRoute's management API, writes `scorecard.json`, runs hygiene and bumps, and appends to `bump-audit.jsonl`.
- **Pure logic.** Scoring, matching, discovery and bump planning are pure modules with unit tests. I/O sits at the edges (`api.mjs`, `sources.mjs`, `files.mjs`).
- **llm-env side.**
  - `pylib/smart_settings.py` holds every `smart_routing` default and its validation, and renders `settings.json`. `scripts/omniroute-smart-sync.sh` pipes that file into the running container and then installs and activates the plugin through the dashboard API.
  - `make smart-probe` runs a probe inside the container, where the AA key, the database and the compose network are all available.
- **Out of P2:**
  - remote System One backends (Zen, TypeSafe), which P3 adds to `smart-probe` because the Zen key is only reachable from the strategy's injected context;
  - the `smart` strategy (P3);
  - fusion (P4).

**Tech Stack:** Node 26 in the image and Node 24 on the host. Plain ES modules with JSDoc, built-ins only: `node:sqlite`, `node:test`, `fetch`. Python 3 (pylib, pytest), Bash (`tools/lib.sh`), podman.

**Spec:** `docs/prds/omniroute-smart-routing-v2.1-prd.md`, produced by P1. Use v2.0 if P1 has not renamed it yet. The sections that matter here are "Scoring (`smart-catalog`)", "Plugin maintenance", "Data requirements", "Interface design" and "Data storage".

Also read `docs/prds/omniroute-smart-routing-research/spike-report-p1.md`, in particular S5 and "Impact on P2–P4". Its measured facts override this plan where they disagree:
- whether loopback trust removes the need for the management key;
- whether `activate` on an already-active plugin restarts the child;
- the plugin export shape.

## Preconditions

- The P1 spike report ends with `VERDICT: GO`.
- The P2 edits listed in its "Impact on P2–P4" section have been applied to this file before Task 1 starts.
- The derived image `smart_routing.image` builds (`make omniroute-smart-build`).

## Global Constraints

- **No production changes.** Never stop, restart, exec writes into or reconfigure the production `omniroute` container, and never edit `~/.config/llm-env/*`. Live runs of the new scripts belong to P5.
- **Throwaway containers.** Integration uses containers named `omniroute-smart-it*` on `127.0.0.1:29130`, plus one temporary named volume `omniroute-smart-it-data`. Both are removed at the end of the task, pass or fail.
- **Secrets.** They are never printed, logged, or written to the repo, `scorecard.json`, `sync-state.json`, `bump-audit.jsonl`, or the plugin config. Error messages carry the HTTP status and path only, never response bodies or headers.
- **Files.** Every file the plugin writes under `/app/data/smart-routing/` is created with mode 0600, and its directory with 0700. Writes are atomic (temp file plus `rename`).
- **Plugin code.** It uses Node built-ins only and imports nothing outside `omniroute-smart/plugins/smart-catalog/`. The plugin's lifecycle hooks return within 1 s: `onActivate` never awaits a sync, because OmniRoute's hook timeout is 10 s and a timeout kills the child with no respawn.
- **Price.** It never enters `Q`. It is stored in the scorecard's catalog block only.
- **Arena attribution.** Every scorecard carries `attribution` naming Artificial Analysis. Raw AA payloads are cached only inside the data volume, never in the repo.
- **Commits.** Commit after each task. Never push.
- **Code style.** Ponytail rules: reuse `pylib/omniroute.py` `_login`/`_request`, the `tools/lib.sh` helpers and the `scripts/system-one-build.sh` script shape. Write the smallest complete change.

## Review Focus

1. **Missing AA key or AA outage** must not fail the sync. Scores fall back to the cached AA payload when one exists, otherwise to Arena and priors. `sync-state.json` records `aa: "missing_key" | "unavailable" | "stale" | "ok"` (`unavailable` = AA down and nothing cached yet). Pinned in Task 5 and Task 9.
2. **`bumpMode: off`** must make zero `PUT /api/combos` calls and record suggestions only. A bump must never remove a member that no earlier bump created. Pinned in Task 8.
3. **A slow first sync** must not hold `onActivate` past the hook timeout. Pinned in Task 9.
4. **An unknown or mistyped `smart_routing` key** in `models.yml` must fail validation and name the key, not be silently ignored. Pinned in Task 1.
5. **`-fast` / `-flash` / `-build-fast` ids** must never be scored as their base model. They take the base only as a prior, with `priorFamilyPenalty` subtracted, and `score_source: "prior"`. Pinned in Task 6.

---

## File Structure

| Path | Responsibility |
|---|---|
| `pylib/smart_settings.py`, `tests/test_smart_settings.py` | `smart_routing` defaults, validation and `settings.json` rendering |
| `pylib/config.py` | Calls the smart-settings validation from `validate_config` |
| `llmenv.py`, `tests/test_cli.py` | `smart-settings` subcommand; `omniroute smart-plugin` and `omniroute smart-key` actions |
| `pylib/omniroute_smart.py`, `tests/test_omniroute_smart.py` | Install and activate the plugin; create the manage-scoped key into the env file |
| `scripts/omniroute-smart-sync.sh`, `scripts/smart-probe.sh` | Post-start sync; probe runner |
| `scripts/start.sh`, `Makefile`, `scripts/help.sh` | Call the sync when enabled; `smart-sync`, `smart-probe`, `smart-key` targets |
| `pylib/compose.py`, `docker/omniroute-smart/Dockerfile` | `OMNIROUTE_PLUGINS_DIR`; copy the plugin and probe into the image |
| `scripts/check-server.sh` | Plugin-active and scorecard-age rows |
| `omniroute-smart/plugins/smart-catalog/plugin.json` | Manifest and flat `configSchema` |
| `omniroute-smart/plugins/smart-catalog/index.mjs` | Lifecycle hooks and the tick scheduler |
| `omniroute-smart/plugins/smart-catalog/lib/files.mjs` | Atomic 0600 JSON writes, JSONL append, JSON read |
| `omniroute-smart/plugins/smart-catalog/lib/api.mjs` | Loopback management-API client and plugin config with defaults |
| `omniroute-smart/plugins/smart-catalog/lib/sources.mjs` | AA, models.dev, and Arena from SQLite, with cache fallback |
| `omniroute-smart/plugins/smart-catalog/lib/match.mjs` | Catalog id → models.dev / AA / sibling resolution |
| `omniroute-smart/plugins/smart-catalog/lib/score.mjs` | Percentiles, confidence, prior, `Q[task_kind][domain]`, scorecard |
| `omniroute-smart/plugins/smart-catalog/lib/catalog.mjs` | Discovery (effort options, fast tiers) and hygiene |
| `omniroute-smart/plugins/smart-catalog/lib/bumps.mjs` | Bump planning and application, audit records |
| `omniroute-smart/plugins/smart-catalog/lib/sync.mjs` | One sync run (orchestration) |
| `omniroute-smart/plugins/smart-catalog/test/*.test.mjs` + `fixtures/` | Unit tests and an end-to-end test against a fake OmniRoute HTTP server |
| `omniroute-smart/probe/smart-probe.mjs`, `omniroute-smart/probe/smart-status.mjs` | In-container probe and status JSON |

**Interfaces this plan produces for P3–P5:**

- **`settings.json`** at `/app/data/smart-routing/settings.json` (P3 reads it):

  ```json
  { "version": 1,
    "plans": [{"provider":"…","connection":"*","cycle":"monthly@1","renews":true,"expiresAt":null,"manualRemainingPct":null,"metered":false}],
    "bumps": [{"match":"opencode-go/glm-*-flash","maxPriceRatio":1.0}],
    "aliases": {"<provider>/<model>": "<models.dev provider>/<model>"},
    "providerMap": {"<omniroute provider>": "<models.dev provider>"},
    "capabilityWeights": {"plan": {"intelligence":0.55,"agentic":0.20,"arena":0.25}, "…": {}},
    "pluginConfigTtlSeconds": 60, "scoreDecayDays": 14, "cohortMonths": 12, "priorFamilyPenalty": 0.05,
    "bumpPriceMaxRatio": 1.0, "bumpMaxFallbacks": 1, "decisionLogMaxMB": 10, "decisionLogFiles": 3,
    "defaults": { "<every combo-config key from the PRD Interface design>": "<default>" } }
  ```

- **`scorecard.json`** at `/app/data/smart-routing/scorecard.json` (P3 reads it):

  ```json
  { "version": 1, "generated_at": "ISO", "attribution": "…Artificial Analysis…",
    "intelligence_index_version": 4.3,
    "sources": {"aa": {"status":"ok|stale|missing_key|unavailable","fetched_at":"ISO|null"},
                "modelsdev": {"status":"ok|stale","fetched_at":"ISO"},
                "arena": {"synced_at":"ISO|null","rows":0}},
    "entries": { "<provider>/<model>": {
        "id": "…", "canonical_id": "zhipuai/glm-5.3|null",
        "Q": {"<task_kind>": {"<domain>": {"q":0.0,"c":0.0,"source":"measured|prior|median_default"}}},
        "aa": {"slug":"…","intelligence":0,"coding":0,"agentic":0,"speed":0} ,
        "arena": {"code": {"elo":0,"confidence":"high"}, "text": null},
        "catalog": {"price": {"input":0,"output":0,"cache_read":0}, "blended": 0, "price_percentile": 0,
                    "effort_options": ["low","high"], "fast_tiers": ["…"], "limits": {"context":0,"output":0},
                    "release_date": "YYYY-MM-DD|null"} } },
    "hygiene": {"unhidden": [], "added": [], "missing": []},
    "bump_suggestions": [] }
  ```

  Domains are `frontend`, `backend`, `fullstack` and `infra_docs`. Task kinds are `plan`, `review_audit`, `execute_edit` and `mechanical`.

- **`task-stats.json`.** P3's strategy writes it and this plan reads it for bumps:

  ```json
  {"version":1,"days":{"YYYY-MM-DD":{"<combo name>":{"task_kind":{"plan":0,"review_audit":0,"execute_edit":0,"mechanical":0},"requests":0,"decisions":0,"attempts":0,"quota_attempts":0,"fusion_degraded":0,"fusion_failed":0}}}}
  ```

- **`sync-state.json`:** `{last_attempt_at, last_success_at, last_error, aa, modelsdev}`.
- **`smart-status.mjs` output**, consumed by check-server and P5:

  ```json
  {"plugin_status":"active|…|missing","sync_interval_hours":6,"scorecard_age_minutes":12.5,"last_error":null}
  ```

## Spec deviations (recorded in the PRD in Task 13)

1. **No Arena webdev board.** The mirror OmniRoute syncs from has no webdev board; its index lists `text`, `code`, `agent`, `document`, `search` and media boards. The domain → board mapping is therefore:

   | Domain | Board |
   |---|---|
   | `frontend` | `code` |
   | `backend` | `code` |
   | `fullstack` | `code` |
   | `infra_docs` | `text` |

2. **Price comes from the models.dev `api.json` the plugin already fetches.** This is the same upstream as OmniRoute's synced price table, and it avoids coupling to OmniRoute's pricing schema.
3. **`settings.json` lives in the data volume** and is written by `scripts/omniroute-smart-sync.sh` through `podman exec -i`. The image runs as user `node`, so a 0600 host bind mount owned by the host user would be unreadable under rootless podman.
4. **`make smart-probe` in P2** covers AA, models.dev, Arena freshness and Laya. P3 adds the remote System One backends.
5. **Bump score guard uses the domain mean.** The PRD's `Q_new ≥ Q_old − scoreHysteresis` names the dominant task kind but no domain; bumps compare Q averaged over the four domains, because a rule covers every request the combo serves.

---

### Task 1: `smart_routing` settings schema and `settings.json` rendering

**Files:**
- Create: `pylib/smart_settings.py`, `tests/test_smart_settings.py`
- Modify: `pylib/config.py` (call it from `validate_config`, after the P1 `smart_routing` block)
- Modify: `llmenv.py` (`smart-settings` subcommand), `tests/test_cli.py`

**Interfaces:**
- Consumes: `cfg["smart_routing"]` (P1 keys `enabled`, `base_image`, `image`).
- Produces:
  - `DEFAULTS: dict`;
  - `TASK_KINDS`;
  - `resolve_smart_settings(section: dict) -> tuple[dict, list[str], list[str]]`, returning (settings, errors, warnings);
  - `render_smart_settings(cfg: dict) -> dict`, which raises `ValueError` listing every error;
  - `llmenv --config X smart-settings`, which prints the settings JSON and writes warnings to stderr.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_smart_settings.py
import pytest

from pylib.smart_settings import DEFAULTS, render_smart_settings, resolve_smart_settings


def test_defaults_are_valid_and_complete():
    settings, errors, warnings = resolve_smart_settings({})
    assert errors == [] and warnings == []
    assert settings["defaults"]["classifierArbiterModel"] == "codex/gpt-6-luna-high"
    assert settings["defaults"]["fusionTuning"] == {"panelHardTimeoutMs": 90000, "minPanel": 2, "stragglerGraceMs": 8000}
    assert settings["defaults"]["weights"]["execute_edit"] == {"wQ": 0.30, "wS": 0.30, "wW": 0.20, "wO": 0.10, "wH": 0.10}
    assert settings["capabilityWeights"]["mechanical"] == {"intelligence": 0.50, "speed": 0.50}


def test_user_values_merge_over_defaults_and_p1_keys_are_ignored():
    settings, errors, _ = resolve_smart_settings({"enabled": True, "image": "x", "defaults": {"quotaReservePercent": 5}})
    assert errors == []
    assert settings["defaults"]["quotaReservePercent"] == 5
    assert settings["defaults"]["burnAlpha"] == 0.3
    assert "enabled" not in settings and "image" not in settings


@pytest.mark.parametrize(
    ("section", "message"),
    [
        ({"bumpz": []}, "smart_routing.bumpz is not a known setting"),
        ({"defaults": {"quotaReserve": 3}}, "smart_routing.defaults.quotaReserve is not a known setting"),
        ({"defaults": {"weights": {"plan": {"wQ": 0.9}}}}, "smart_routing.defaults.weights.plan must sum to 1"),
        ({"capabilityWeights": {"plan": {"intelligence": 0.5, "vibes": 0.5}}}, "smart_routing.capabilityWeights.plan.vibes is not a known metric"),
        ({"defaults": {"systemOneMinConfidence": 1.5}}, "smart_routing.defaults.systemOneMinConfidence must be a number in [0, 1]"),
        ({"defaults": {"classifierArbiterModel": "smart-planning"}}, "smart_routing.defaults.classifierArbiterModel must be a provider/model id"),
        ({"plans": [{"provider": "codex", "cycle": "monthly@40"}]}, r"smart_routing.plans\[0\].cycle"),
        ({"plans": [{"provider": "codex", "cycle": "none", "manualRemainingPct": 120}]}, r"smart_routing.plans\[0\].manualRemainingPct"),
        ({"bumps": [{"match": "opencode-go/glm-5.3-flash"}]}, r"smart_routing.bumps\[0\].match must be <provider>/<glob with \*>"),
        ({"defaults": "fast"}, "smart_routing.defaults must be a mapping"),
        ({"defaults": {"minHeadroomPct": 5}}, "smart_routing.defaults.minHeadroomPct must be a mapping"),
        ({"defaults": {"fusionTuning": 2}}, "smart_routing.defaults.fusionTuning must be a mapping"),
    ],
)
def test_invalid_settings_name_the_key(section, message):
    _, errors, _ = resolve_smart_settings(section)
    assert any(__import__("re").search(message, e) for e in errors), errors


def test_a_user_weights_row_replaces_the_default_row():
    settings, errors, _ = resolve_smart_settings({"capabilityWeights": {"plan": {"intelligence": 1.0}}})
    assert errors == []
    assert settings["capabilityWeights"]["plan"] == {"intelligence": 1.0}


def test_arena_weight_above_quarter_warns_only():
    _, errors, warnings = resolve_smart_settings({"capabilityWeights": {"plan": {"intelligence": 0.6, "arena": 0.4}}})
    assert errors == []
    assert warnings == ["smart_routing.capabilityWeights.plan.arena is 0.4; keep Arena at 0.25 or below (third-party mirror, top 20 only)"]


def test_render_adds_version_and_raises_on_errors():
    assert render_smart_settings({"smart_routing": {}})["version"] == 1
    with pytest.raises(ValueError, match="bumpz"):
        render_smart_settings({"smart_routing": {"bumpz": []}})


def test_defaults_constant_is_not_mutated_by_resolution():
    before = repr(DEFAULTS)
    resolve_smart_settings({"defaults": {"minHeadroomPct": {"small": 2}}})
    assert repr(DEFAULTS) == before
```

In `tests/test_config.py`, add a test that a bad `smart_routing` setting fails `validate_config` through the same message:

```python
def test_validate_config_reports_smart_settings_errors(valid_cfg):
    cfg = copy.deepcopy(valid_cfg)
    cfg["smart_routing"] = {**migrate_config({})["smart_routing"], "bumpz": []}
    errors = validate_config(cfg)
    assert any("smart_routing.bumpz is not a known setting" in e for e in errors), errors
```

In `tests/test_cli.py`, following its existing pattern for invoking subcommands on a temporary config:

```python
def test_smart_settings_prints_rendered_json(tmp_path):
    # Write a valid config via the helpers this file already uses, then:
    result = run_cli(tmp_path, "smart-settings")
    assert result.returncode == 0
    assert json.loads(result.stdout)["version"] == 1
```

Adapt `run_cli` to the helper name `tests/test_cli.py` actually uses (`rg -n "^def " tests/test_cli.py`).

- [ ] **Step 2: Run and see them fail**

Run: `uv run pytest tests/test_smart_settings.py -v`
Expected: FAIL with `ModuleNotFoundError: pylib.smart_settings`.

- [ ] **Step 3: Implement `pylib/smart_settings.py`**

```python
"""smart_routing settings: defaults, validation, and the settings.json the OmniRoute
`smart` strategy and `smart-catalog` plugin read (PRD "Interface design")."""

from __future__ import annotations

import copy
import math
import re
from datetime import date
from typing import Any

TASK_KINDS = ("plan", "review_audit", "execute_edit", "mechanical")
CAPABILITY_METRICS = ("intelligence", "coding", "agentic", "arena", "speed")
_UTILITY_TERMS = ("wQ", "wS", "wW", "wO", "wH")
_P1_KEYS = {"enabled", "base_image", "image"}  # owned by pylib/config.py

DEFAULTS: dict[str, Any] = {
    "plans": [],
    "bumps": [],
    "aliases": {},
    "providerMap": {"codex": "openai", "grok-cli": "xai", "opencode-zen": "opencode"},
    "capabilityWeights": {
        "plan": {"intelligence": 0.55, "agentic": 0.20, "arena": 0.25},
        "review_audit": {"intelligence": 0.45, "coding": 0.30, "arena": 0.25},
        "execute_edit": {"coding": 0.45, "agentic": 0.35, "arena": 0.20},
        "mechanical": {"intelligence": 0.50, "speed": 0.50},
    },
    "pluginConfigTtlSeconds": 60,
    "scoreDecayDays": 14,
    "cohortMonths": 12,
    "priorFamilyPenalty": 0.05,
    "bumpPriceMaxRatio": 1.0,
    "bumpMaxFallbacks": 1,
    "decisionLogMaxMB": 10,
    "decisionLogFiles": 3,
    "defaults": {
        "quotaReservePercent": 3,
        "minHeadroomPct": {"small": 1, "medium": 3, "large": 6},
        "minResetHours": 0.25,
        "burnAlpha": 0.3,
        "burnPriorUtil": 0.8,
        "scarcityClassCut": 0.9,
        "neutralScarcity": 0.5,
        "noResetHorizonHours": 720,
        "qualityFloorDelta": 0.10,
        "weights": {
            "plan": {"wQ": 0.50, "wS": 0.25, "wW": 0.10, "wO": 0.05, "wH": 0.10},
            "review_audit": {"wQ": 0.50, "wS": 0.25, "wW": 0.10, "wO": 0.05, "wH": 0.10},
            "execute_edit": {"wQ": 0.30, "wS": 0.30, "wW": 0.20, "wO": 0.10, "wH": 0.10},
            "mechanical": {"wQ": 0.20, "wS": 0.30, "wW": 0.20, "wO": 0.10, "wH": 0.20},
        },
        "meteredPriceFactor": 0.2,
        "failureAlpha": 0.3,
        "failureBackoffBaseSeconds": 30,
        "failureBackoffMaxMinutes": 30,
        "systemOneMinConfidence": 0.7,
        "classifierArbiterModel": "codex/gpt-6-luna-high",
        "classifierArbiterTimeoutMs": 5000,
        "systemOneLayaTimeoutMs": 1500,
        "systemOneRemoteTimeoutMs": 3000,
        "systemOneChainBudgetMs": 8000,
        "systemOneRetries": 2,
        "systemOneRetryAfterMaxMs": 2000,
        "systemOneStateMaxChars": 4000,
        "fusionSecondOpinionMin": 0.5,
        "judgeModel": None,
        "fusionJudgeTimeoutMs": 120000,
        "fusionTuning": {"panelHardTimeoutMs": 90000, "minPanel": 2, "stragglerGraceMs": 8000},
    },
}

# (min, max, integer)
_TOP_NUMBERS = {
    "pluginConfigTtlSeconds": (1, 3600, True), "scoreDecayDays": (1, 365, False), "cohortMonths": (1, 60, True),
    "priorFamilyPenalty": (0, 0.5, False), "bumpPriceMaxRatio": (0.1, 10, False), "bumpMaxFallbacks": (0, 5, True),
    "decisionLogMaxMB": (1, 1024, True), "decisionLogFiles": (1, 20, True),
}
_DEFAULT_NUMBERS = {
    "quotaReservePercent": (0, 50, False), "minResetHours": (0.01, 24, False), "burnAlpha": (0.01, 1, False),
    "burnPriorUtil": (0, 1, False), "scarcityClassCut": (0, 1, False), "neutralScarcity": (0, 1, False),
    "noResetHorizonHours": (1, 8760, False), "qualityFloorDelta": (0, 1, False), "meteredPriceFactor": (0, 1, False),
    "failureAlpha": (0.01, 1, False), "failureBackoffBaseSeconds": (1, 3600, False), "failureBackoffMaxMinutes": (1, 1440, False),
    "systemOneMinConfidence": (0, 1, False), "classifierArbiterTimeoutMs": (100, 60000, True),
    "systemOneLayaTimeoutMs": (50, 60000, True), "systemOneRemoteTimeoutMs": (50, 60000, True),
    "systemOneChainBudgetMs": (100, 120000, True), "systemOneRetries": (0, 5, True),
    "systemOneRetryAfterMaxMs": (0, 60000, True), "systemOneStateMaxChars": (500, 32000, True),
    "fusionSecondOpinionMin": (0, 1, False), "fusionJudgeTimeoutMs": (1000, 600000, True),
}
_FUSION_NUMBERS = {"panelHardTimeoutMs": (1000, 600000, True), "minPanel": (1, 2, True), "stragglerGraceMs": (0, 120000, True)}
_CYCLE = re.compile(r"^(monthly@([1-9]|[12][0-9]|3[01])|weekly@(mon|tue|wed|thu|fri|sat|sun)|none)$")
_BUMP_GLOB = re.compile(r"^[a-z0-9-]+/[^/\s]*\*[^/\s]*$")
_MODEL_ID = re.compile(r"^[^/\s]+/\S+$")


def _merge(base: Any, over: Any) -> Any:
    if isinstance(base, dict) and isinstance(over, dict):
        merged = copy.deepcopy(base)
        for key, value in over.items():
            merged[key] = _merge(base.get(key), value) if key in base else copy.deepcopy(value)
        return merged
    return copy.deepcopy(over)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _num(errors: list[str], path: str, value: Any, lo: float, hi: float, integer: bool = False) -> None:
    ok = _is_number(value) and lo <= value <= hi and (not integer or float(value).is_integer())
    if not ok:
        kind = "an integer" if integer else "a number"
        errors.append(f"smart_routing.{path} must be {kind} in [{lo:g}, {hi:g}]")


def _weights(errors: list[str], path: str, row: Any, allowed: tuple[str, ...], require_all: bool) -> None:
    if not isinstance(row, dict):
        errors.append(f"smart_routing.{path} must be a mapping")
        return
    for key, value in row.items():
        if key not in allowed:
            errors.append(f"smart_routing.{path}.{key} is not a known metric")
        else:
            _num(errors, f"{path}.{key}", value, 0, 1)
    if require_all and set(row) != set(allowed):
        errors.append(f"smart_routing.{path} must set exactly {', '.join(allowed)}")
    if all(_is_number(v) for v in row.values()) and abs(sum(row.values()) - 1) > 1e-6:
        errors.append(f"smart_routing.{path} must sum to 1")


def _validate_plans(errors: list[str], plans: Any) -> None:
    if not isinstance(plans, list):
        errors.append("smart_routing.plans must be a list")
        return
    for i, plan in enumerate(plans):
        path = f"plans[{i}]"
        if not isinstance(plan, dict):
            errors.append(f"smart_routing.{path} must be a mapping")
            continue
        for key in plan:
            if key not in {"provider", "connection", "cycle", "renews", "expiresAt", "manualRemainingPct", "metered"}:
                errors.append(f"smart_routing.{path}.{key} is not a known setting")
        if not isinstance(plan.get("provider"), str) or not plan["provider"]:
            errors.append(f"smart_routing.{path}.provider must be a provider id")
        if not isinstance(plan.get("connection", "*"), str):
            errors.append(f"smart_routing.{path}.connection must be a connection id or *")
        if not isinstance(plan.get("cycle"), str) or not _CYCLE.match(plan["cycle"]):
            errors.append(f"smart_routing.{path}.cycle must be monthly@<1-31>, weekly@<mon..sun> or none")
        for flag in ("renews", "metered"):
            if not isinstance(plan.get(flag, flag == "renews"), bool):
                errors.append(f"smart_routing.{path}.{flag} must be a Boolean")
        if plan.get("expiresAt") is not None:
            try:
                date.fromisoformat(str(plan["expiresAt"]))
            except ValueError:
                errors.append(f"smart_routing.{path}.expiresAt must be an ISO date (YYYY-MM-DD)")
        if plan.get("manualRemainingPct") is not None:
            _num(errors, f"{path}.manualRemainingPct", plan["manualRemainingPct"], 0, 100)


def _validate_bumps(errors: list[str], bumps: Any) -> None:
    if not isinstance(bumps, list):
        errors.append("smart_routing.bumps must be a list")
        return
    for i, rule in enumerate(bumps):
        path = f"bumps[{i}]"
        if not isinstance(rule, dict) or not isinstance(rule.get("match"), str) or not _BUMP_GLOB.match(rule["match"]):
            errors.append(f"smart_routing.{path}.match must be <provider>/<glob with *>")
            continue
        if "maxPriceRatio" in rule:
            _num(errors, f"{path}.maxPriceRatio", rule["maxPriceRatio"], 0.1, 10)


def _validate_id_map(errors: list[str], name: str, value: Any, pattern: re.Pattern[str]) -> None:
    if not isinstance(value, dict) or not all(isinstance(k, str) and isinstance(v, str) and pattern.match(v) for k, v in value.items()):
        errors.append(f"smart_routing.{name} must map strings to {'provider/model ids' if pattern is _MODEL_ID else 'provider ids'}")


def resolve_smart_settings(section: dict[str, Any]) -> tuple[dict[str, Any], list[str], list[str]]:
    """Merge the user's smart_routing section over DEFAULTS; return (settings, errors, warnings)."""
    errors: list[str] = []
    warnings: list[str] = []
    user: dict[str, Any] = {}
    for key, value in section.items():
        if key in _P1_KEYS:
            continue
        if key not in DEFAULTS:
            errors.append(f"smart_routing.{key} is not a known setting")
        elif isinstance(DEFAULTS[key], dict) and not isinstance(value, dict):
            errors.append(f"smart_routing.{key} must be a mapping")
        else:
            user[key] = value
    for key in user.get("defaults", {}):
        if key not in DEFAULTS["defaults"]:
            errors.append(f"smart_routing.defaults.{key} is not a known setting")
    settings = _merge(DEFAULTS, user)
    # A weights row the user writes replaces the default row: merging would mix two rows and break the sum.
    for kind, row in (user.get("capabilityWeights") or {}).items():
        settings["capabilityWeights"][kind] = copy.deepcopy(row)
    user_weights = (user.get("defaults") or {}).get("weights")
    if isinstance(user_weights, dict):
        for kind, row in user_weights.items():
            settings["defaults"]["weights"][kind] = copy.deepcopy(row)
    elif user_weights is not None:
        errors.append("smart_routing.defaults.weights must be a mapping")
        settings["defaults"]["weights"] = copy.deepcopy(DEFAULTS["defaults"]["weights"])
    for name, table in (("capabilityWeights", settings["capabilityWeights"]), ("defaults.weights", settings["defaults"]["weights"])):
        for kind in table:
            if kind not in TASK_KINDS:
                errors.append(f"smart_routing.{name}.{kind} is not a task kind ({', '.join(TASK_KINDS)})")
    defaults = settings["defaults"]

    for key, (lo, hi, integer) in _TOP_NUMBERS.items():
        _num(errors, key, settings[key], lo, hi, integer)
    for key, (lo, hi, integer) in _DEFAULT_NUMBERS.items():
        _num(errors, f"defaults.{key}", defaults[key], lo, hi, integer)
    for name in ("minHeadroomPct", "fusionTuning"):
        if not isinstance(defaults[name], dict):
            errors.append(f"smart_routing.defaults.{name} must be a mapping")
            defaults[name] = {}
    for scope in ("small", "medium", "large"):
        _num(errors, f"defaults.minHeadroomPct.{scope}", defaults["minHeadroomPct"].get(scope), 0, 100)
    for key, (lo, hi, integer) in _FUSION_NUMBERS.items():
        _num(errors, f"defaults.fusionTuning.{key}", defaults["fusionTuning"].get(key), lo, hi, integer)
    for kind in TASK_KINDS:
        _weights(errors, f"defaults.weights.{kind}", defaults["weights"].get(kind), _UTILITY_TERMS, require_all=True)
        capability = settings["capabilityWeights"].get(kind)
        _weights(errors, f"capabilityWeights.{kind}", capability, CAPABILITY_METRICS, require_all=False)
        arena = capability.get("arena", 0) if isinstance(capability, dict) else 0
        if _is_number(arena) and arena > 0.25:
            warnings.append(
                f"smart_routing.capabilityWeights.{kind}.arena is {arena:g}; keep Arena at 0.25 or below "
                "(third-party mirror, top 20 only)"
            )
    # A combo name has no provider prefix, so this also rejects a `smart` combo as arbiter (recursion).
    if not isinstance(defaults["classifierArbiterModel"], str) or not _MODEL_ID.match(defaults["classifierArbiterModel"]):
        errors.append("smart_routing.defaults.classifierArbiterModel must be a provider/model id, not a combo")
    if defaults["judgeModel"] is not None and (
        not isinstance(defaults["judgeModel"], str) or not _MODEL_ID.match(defaults["judgeModel"])
    ):
        errors.append("smart_routing.defaults.judgeModel must be null or a provider/model id")
    _validate_plans(errors, settings["plans"])
    _validate_bumps(errors, settings["bumps"])
    _validate_id_map(errors, "aliases", settings["aliases"], _MODEL_ID)
    _validate_id_map(errors, "providerMap", settings["providerMap"], re.compile(r"^[a-z0-9-]+$"))
    return settings, errors, warnings


def render_smart_settings(cfg: dict[str, Any]) -> dict[str, Any]:
    settings, errors, _ = resolve_smart_settings(cfg.get("smart_routing") or {})
    if errors:
        raise ValueError("; ".join(errors))
    return {"version": 1, **settings}
```

Check the `providerMap` defaults against models.dev before committing. Run `curl -s https://models.dev/api.json | jq -r 'keys[]' | rg -x 'openai|xai|opencode|opencode-go'` and confirm each mapped value exists. If one doesn't, fix the value and note it in the commit message.

- [ ] **Step 4: Wire validation and the CLI**

`pylib/config.py`: inside the P1 `smart_routing` block of `validate_config`, after the existing checks:

```python
            from pylib.smart_settings import resolve_smart_settings  # local: avoid import cycle

            errors.extend(resolve_smart_settings(smart_routing)[1])
```

If there is no cycle (`pylib/smart_settings.py` imports nothing from `pylib`), move the import to the top of the file instead.

`llmenv.py`: add a subcommand next to `migrate-config`.

```python
def cmd_smart_settings(args: argparse.Namespace) -> int:
    cfg = require_valid_config(load_config(Path(args.config)))
    _, _, warnings = resolve_smart_settings(cfg.get("smart_routing") or {})
    for warning in warnings:
        print(f"warn: {warning}", file=sys.stderr)
    return emit(render_smart_settings(cfg))
```

```python
    smart_settings = sub.add_parser("smart-settings")
    smart_settings.add_argument("--config", default=argparse.SUPPRESS)
    smart_settings.set_defaults(func=cmd_smart_settings)
```

Check that `emit` prints the dict as JSON on stdout. If it wraps the payload, print `json.dumps(...)` directly, so that the stdout is exactly the settings JSON that Task 3 pipes into the container.

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_smart_settings.py tests/test_config.py tests/test_cli.py -v && bash tools/validate.sh`
Expected: PASS. pyright is clean.

- [ ] **Step 6: Commit**

```bash
git add pylib/smart_settings.py pylib/config.py llmenv.py tests/test_smart_settings.py tests/test_config.py tests/test_cli.py
git commit -m "feat(smart-routing): settings schema, validation and settings.json rendering"
```

---

### Task 2: Plugin I/O helpers (`files.mjs`, `api.mjs`) and manifest

**Files:**
- Create: `omniroute-smart/plugins/smart-catalog/plugin.json`
- Create: `omniroute-smart/plugins/smart-catalog/lib/files.mjs`, `.../lib/api.mjs`
- Test: `omniroute-smart/plugins/smart-catalog/test/files.test.mjs`, `.../test/api.test.mjs`

**Interfaces:**
- Produces:
  - `writeJsonAtomic(path, data)`, `readJson(path) → any|null`, `appendJsonl(path, record, {maxBytes, files})`;
  - `makeApi({base, key, fetchImpl}) → {pluginConfig, combos, putCombo, models, unhide, addModel}`;
  - `configWithDefaults(rawConfig, configSchema) → config`.

- [ ] **Step 1: Write the manifest**

```json
{
  "name": "smart-catalog",
  "version": "1.0.0",
  "description": "Scores combo members (Artificial Analysis, Arena, models.dev), keeps model ids usable and bumps members with an audit trail for the smart strategy.",
  "main": "index.mjs",
  "requires": { "permissions": ["env", "network", "file-read", "file-write"] },
  "hooks": { "onActivate": true, "onDeactivate": true },
  "enabledByDefault": true,
  "configSchema": {
    "syncIntervalHours": { "type": "number", "default": 6, "min": 1, "max": 48, "description": "Hours between catalog syncs" },
    "systemOneLayaUrl": { "type": "string", "default": "http://system-one:8000", "description": "Laya System One base URL (compose DNS)" },
    "consentZenFree": { "type": "boolean", "default": false, "description": "Send request excerpts to OpenCode Zen free System One" },
    "consentTypeSafe": { "type": "boolean", "default": false, "description": "Send request excerpts to TypeSafe System One" },
    "consentZenPaid": { "type": "boolean", "default": false, "description": "Send request excerpts to OpenCode Zen paid System One" },
    "bumpMode": { "type": "select", "enum": ["auto", "off"], "default": "auto", "description": "auto: apply bumps; off: suggest only" },
    "scoreHysteresis": { "type": "number", "default": 0.05, "min": 0, "max": 0.5, "description": "Minimum score change that triggers a bump" }
  }
}
```

The field shape (`type`, `default`, `min`, `max`, `enum`, `description`) is upstream `src/lib/plugins/manifest.ts` `ConfigFieldSchema`. A hook fires only when `hooks.<name>` is `true` (`manager.ts`).

- [ ] **Step 2: Write the failing tests**

```js
// omniroute-smart/plugins/smart-catalog/test/files.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, statSync, readFileSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { writeJsonAtomic, readJson, appendJsonl } from "../lib/files.mjs";

const dir = () => join(mkdtempSync(join(tmpdir(), "sc-files-")), "smart-routing");

test("atomic JSON write creates 0600 file in a 0700 dir and reads back", () => {
  const d = dir(); const f = join(d, "scorecard.json");
  writeJsonAtomic(f, { a: 1 });
  assert.deepEqual(readJson(f), { a: 1 });
  assert.equal(statSync(f).mode & 0o777, 0o600);
  assert.equal(statSync(d).mode & 0o777, 0o700);
});

test("readJson returns null for a missing or corrupt file", () => {
  assert.equal(readJson(join(dir(), "nope.json")), null);
});

test("appendJsonl rotates at maxBytes keeping `files` files", () => {
  const f = join(dir(), "audit.jsonl");
  for (let i = 0; i < 5; i++) appendJsonl(f, { i, pad: "x".repeat(40) }, { maxBytes: 60, files: 3 });
  assert.ok(existsSync(f) && existsSync(`${f}.1`) && existsSync(`${f}.2`) && !existsSync(`${f}.3`));
  assert.equal(JSON.parse(readFileSync(f, "utf8").trim()).i, 4);
  assert.equal(statSync(f).mode & 0o777, 0o600);
});
```

```js
// omniroute-smart/plugins/smart-catalog/test/api.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { makeApi, configWithDefaults } from "../lib/api.mjs";

const fakeFetch = (routes, seen = []) => async (url, init = {}) => {
  seen.push({ url, method: init.method ?? "GET", headers: init.headers, body: init.body });
  const key = `${init.method ?? "GET"} ${new URL(url).pathname}${new URL(url).search}`;
  const hit = routes[key];
  return hit ? new Response(JSON.stringify(hit.body ?? {}), { status: hit.status ?? 200 }) : new Response("{}", { status: 404 });
};

test("sends the bearer key and parses JSON", async () => {
  const seen = [];
  const api = makeApi({ base: "http://127.0.0.1:20128", key: "k", fetchImpl: fakeFetch({ "GET /api/combos": { body: { combos: [] } } }, seen) });
  assert.deepEqual(await api.combos(), { combos: [] });
  assert.equal(seen[0].headers.authorization, "Bearer k");
});

test("omits the authorization header when no key is set (loopback trust, S5)", async () => {
  const seen = [];
  const api = makeApi({ base: "http://x", key: "", fetchImpl: fakeFetch({ "GET /v1/models": { body: { data: [] } } }, seen) });
  await api.models();
  assert.equal("authorization" in seen[0].headers, false);
});

test("errors carry method, path and status only", async () => {
  const api = makeApi({ base: "http://x", key: "secret-key", fetchImpl: fakeFetch({ "PUT /api/combos/c1": { status: 400, body: { error: "bad secret-key" } } }) });
  await assert.rejects(api.putCombo("c1", {}), (e) => e.message === "PUT /api/combos/c1 -> 400" && !e.message.includes("secret"));
});

test("unhide and addModel use the provider-models routes", async () => {
  const seen = [];
  const api = makeApi({ base: "http://x", key: "k", fetchImpl: fakeFetch({
    "PATCH /api/provider-models?provider=cursor": {}, "POST /api/provider-models": {} }, seen) });
  await api.unhide("cursor", ["glm-5p3-high"]);
  await api.addModel("cursor", "glm-5p3-high");
  assert.deepEqual(JSON.parse(seen[0].body), { isHidden: false, modelIds: ["glm-5p3-high"] });
  assert.deepEqual(JSON.parse(seen[1].body), { provider: "cursor", modelId: "glm-5p3-high", modelName: "glm-5p3-high" });
});

test("config defaults come from the schema; saved values win", () => {
  const schema = { syncIntervalHours: { type: "number", default: 6 }, bumpMode: { type: "select", default: "auto" } };
  assert.deepEqual(configWithDefaults({ bumpMode: "off" }, schema), { syncIntervalHours: 6, bumpMode: "off" });
});
```

- [ ] **Step 3: Run and see them fail**

Run: `node --test 'omniroute-smart/plugins/smart-catalog/test/**/*.test.mjs'`
Expected: FAIL with `Cannot find module '../lib/files.mjs'`.

- [ ] **Step 4: Implement**

```js
// omniroute-smart/plugins/smart-catalog/lib/files.mjs
// Plugin state files: 0700 dir, 0600 files, atomic replace. Self-contained (the plugin is copied on install).
import { appendFileSync, existsSync, mkdirSync, readFileSync, renameSync, statSync, writeFileSync } from "node:fs";
import { dirname } from "node:path";

const ensureDir = (path) => mkdirSync(dirname(path), { recursive: true, mode: 0o700 });

export function writeJsonAtomic(path, data) {
  ensureDir(path);
  const tmp = `${path}.${process.pid}.tmp`;
  writeFileSync(tmp, JSON.stringify(data, null, 2), { mode: 0o600 });
  renameSync(tmp, path);
}

export function readJson(path) {
  try { return JSON.parse(readFileSync(path, "utf8")); } catch { return null; }
}

/** Append one JSON line; when the file reaches maxBytes, shift path → path.1 → … keeping `files` files in total. */
export function appendJsonl(path, record, { maxBytes, files }) {
  ensureDir(path);
  if (existsSync(path) && statSync(path).size >= maxBytes) {
    for (let i = files - 1; i >= 1; i--) {
      const from = i === 1 ? path : `${path}.${i - 1}`;
      if (existsSync(from)) renameSync(from, `${path}.${i}`);
    }
  }
  appendFileSync(path, `${JSON.stringify(record)}\n`, { mode: 0o600 });
}
```

```js
// omniroute-smart/plugins/smart-catalog/lib/api.mjs
// Loopback client for OmniRoute's management API. Errors never include bodies (they can echo credentials).
export function makeApi({ base, key, fetchImpl = fetch }) {
  const call = async (method, path, body) => {
    const headers = { "content-type": "application/json", ...(key ? { authorization: `Bearer ${key}` } : {}) };
    const r = await fetchImpl(`${base}${path}`, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
    if (!r.ok) throw new Error(`${method} ${path} -> ${r.status}`);
    const text = await r.text();
    return text ? JSON.parse(text) : null;
  };
  return {
    pluginConfig: () => call("GET", "/api/plugins/smart-catalog/config"),
    combos: () => call("GET", "/api/combos"),
    putCombo: (id, body) => call("PUT", `/api/combos/${encodeURIComponent(id)}`, body),
    models: () => call("GET", "/v1/models"),
    unhide: (provider, modelIds) =>
      call("PATCH", `/api/provider-models?provider=${encodeURIComponent(provider)}`, { isHidden: false, modelIds }),
    addModel: (provider, modelId) => call("POST", "/api/provider-models", { provider, modelId, modelName: modelId }),
  };
}

export function configWithDefaults(config = {}, schema = {}) {
  const defaults = Object.fromEntries(Object.entries(schema).map(([k, f]) => [k, f.default]));
  return { ...defaults, ...config };
}
```

The routes and shapes come from upstream:
- `GET /api/plugins/[name]/config` returns `{config, configSchema}`;
- `GET /api/combos` returns `{combos, total}`;
- `PUT /api/combos/[id]`;
- `PATCH /api/provider-models?provider=` takes `{isHidden, modelIds}`;
- `POST /api/provider-models` takes `{provider, modelId, modelName}`.

All of them use `requireManagementAuth`. A key with scope `manage` passes as `Authorization: Bearer`.

- [ ] **Step 5: Run the tests, then commit**

Run: `node --test 'omniroute-smart/plugins/smart-catalog/test/**/*.test.mjs' && bash tools/validate.sh`
Expected: PASS.

```bash
git add omniroute-smart/plugins/smart-catalog
git commit -m "feat(smart-catalog): manifest, state-file helpers and management API client"
```

---

### Task 3: llm-env wiring (image, compose, post-start sync, management key)

**Files:**
- Create: `pylib/omniroute_smart.py`, `tests/test_omniroute_smart.py`, `scripts/omniroute-smart-sync.sh`
- Modify:
  - `llmenv.py`: `omniroute` actions `smart-plugin` and `smart-key`, added to the `choices` of the existing `omniroute` parser;
  - `docker/omniroute-smart/Dockerfile`: copy `plugins` and `probe`;
  - `pylib/compose.py`: `OMNIROUTE_PLUGINS_DIR`;
  - `scripts/start.sh`: call the sync when enabled;
  - `Makefile` and `scripts/help.sh`: `smart-sync` and `smart-key`.
- Test: `tests/test_compose.py`, `tests/test_shell.py`

**Interfaces:**
- Consumes: `_login`, `_request`, `OmniRouteError` (`pylib/omniroute.py`); `read_env_file` (`pylib/dotenv.py`); `render_smart_settings` (Task 1).
- Produces:
  - `ensure_smart_plugin(base_url, password, plugin_path, manifest_version) -> {"installed": bool, "status": str}`;
  - `ensure_smart_management_key(base_url, password, env_path) -> {"created": bool, "restart_required": bool}`;
  - `scripts/omniroute-smart-sync.sh`;
  - `make smart-sync` and `make smart-key`.

  `PLUGIN_PATH = "/opt/omniroute-smart/plugins/smart-catalog"` is the path inside the image.

- [ ] **Step 1: Write the failing Python tests**

These use the `FakeResponse` / `fake_urlopen` pattern from `tests/test_omniroute_combos.py`.

```python
# tests/test_omniroute_smart.py
import json
import stat
import sys
from email.message import Message
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pylib.omniroute as omniroute_module
from pylib.omniroute_smart import ensure_smart_management_key, ensure_smart_plugin

BASE = "http://127.0.0.1:20128"


class FakeResponse:
    def __init__(self, payload, *, set_cookie=None):
        self._body = json.dumps(payload).encode()
        self.headers = Message()
        if set_cookie:
            self.headers["Set-Cookie"] = set_cookie

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def fake_server(routes, calls):
    def fake_urlopen(request, timeout):
        key = f"{request.get_method()} {request.full_url.removeprefix(BASE)}"
        calls.append((key, json.loads(request.data) if request.data else None))
        if key == "POST /api/auth/login":
            return FakeResponse({"success": True}, set_cookie="auth_token=t; Path=/")
        return FakeResponse(routes[key])

    return fake_urlopen


def test_installs_then_activates_when_missing(monkeypatch):
    calls = []
    routes = {"GET /api/plugins": {"plugins": []}, "POST /api/plugins": {"name": "smart-catalog"},
              "POST /api/plugins/smart-catalog/activate": {"status": "active"}}
    monkeypatch.setattr(omniroute_module.urllib.request, "urlopen", fake_server(routes, calls))
    assert ensure_smart_plugin(BASE, "pw", "/opt/omniroute-smart/plugins/smart-catalog", "1.0.0") == {"installed": True, "status": "active"}
    assert ("POST /api/plugins", {"path": "/opt/omniroute-smart/plugins/smart-catalog"}) in calls


def test_same_version_only_activates(monkeypatch):
    calls = []
    routes = {"GET /api/plugins": {"plugins": [{"name": "smart-catalog", "version": "1.0.0", "status": "active"}]},
              "POST /api/plugins/smart-catalog/activate": {"status": "active"}}
    monkeypatch.setattr(omniroute_module.urllib.request, "urlopen", fake_server(routes, calls))
    assert ensure_smart_plugin(BASE, "pw", "/p", "1.0.0")["installed"] is False
    assert not any(k == "POST /api/plugins" for k, _ in calls)


def test_management_key_is_created_once_and_never_returned(monkeypatch, tmp_path):
    env_path = tmp_path / "omniroute-smart.env"
    env_path.write_text("OMNIROUTE_PLUGIN_SMART_CATALOG_AA_API_KEY=aa\n")
    env_path.chmod(0o600)
    calls = []
    routes = {"POST /api/keys": {"key": "sk-manage-123"}}
    monkeypatch.setattr(omniroute_module.urllib.request, "urlopen", fake_server(routes, calls))
    result = ensure_smart_management_key(BASE, "pw", env_path)
    assert result == {"created": True, "restart_required": True}
    assert "sk-manage-123" not in json.dumps(result)
    assert ("POST /api/keys", {"name": "smart-catalog-management", "scopes": ["manage"]}) in calls
    assert "OMNIROUTE_PLUGIN_SMART_CATALOG_MANAGEMENT_KEY=sk-manage-123" in env_path.read_text()
    assert "OMNIROUTE_PLUGIN_SMART_CATALOG_AA_API_KEY=aa" in env_path.read_text()
    assert stat.S_IMODE(env_path.stat().st_mode) == 0o600
    assert ensure_smart_management_key(BASE, "pw", env_path) == {"created": False, "restart_required": False}
```

- [ ] **Step 2: Implement `pylib/omniroute_smart.py`**

```python
"""Install/activate the smart-catalog plugin and provision its management key (smart routing P2)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from pylib.dotenv import read_env_file
from pylib.omniroute import OmniRouteError, _login, _request

PLUGIN_NAME = "smart-catalog"
PLUGIN_PATH = "/opt/omniroute-smart/plugins/smart-catalog"
MANAGEMENT_KEY_VAR = "OMNIROUTE_PLUGIN_SMART_CATALOG_MANAGEMENT_KEY"


def _version(value: str) -> tuple[int, ...]:
    return tuple(int(part) for part in value.split("."))


def ensure_smart_plugin(base_url: str, password: str, plugin_path: str, manifest_version: str) -> dict[str, Any]:
    """Install (or upgrade) from the image path when absent or older, then activate (starts the sync timer)."""
    token = _login(base_url, password)
    listing = _request("GET", f"{base_url}/api/plugins", token)
    plugins = listing.get("plugins") if isinstance(listing, dict) else None
    if not isinstance(plugins, list):
        raise OmniRouteError("unexpected /api/plugins response shape")
    current = next((p for p in plugins if isinstance(p, dict) and p.get("name") == PLUGIN_NAME), None)
    installed = current is None or _version(str(current.get("version", "0.0.0"))) < _version(manifest_version)
    if installed:
        _request("POST", f"{base_url}/api/plugins", token, {"path": plugin_path})
    _request("POST", f"{base_url}/api/plugins/{PLUGIN_NAME}/activate", token)
    return {"installed": installed, "status": "active"}


def ensure_smart_management_key(base_url: str, password: str, env_path: Path) -> dict[str, Any]:
    """Create a manage-scoped key into env_path (0600) once; the value is never returned or logged."""
    if read_env_file(env_path).get(MANAGEMENT_KEY_VAR):
        return {"created": False, "restart_required": False}
    token = _login(base_url, password)
    created = _request("POST", f"{base_url}/api/keys", token, {"name": "smart-catalog-management", "scopes": ["manage"]})
    key = created.get("key") if isinstance(created, dict) else None
    if not isinstance(key, str) or not key:
        raise OmniRouteError("POST /api/keys returned no key")
    existing = env_path.read_text() if env_path.exists() else ""
    separator = "" if not existing or existing.endswith("\n") else "\n"
    fd = os.open(env_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    with os.fdopen(fd, "a") as handle:
        handle.write(f"{separator}{MANAGEMENT_KEY_VAR}={key}\n")
    env_path.chmod(0o600)
    return {"created": True, "restart_required": True}
```

Before relying on `read_env_file`, check its behaviour: `sed -n 1,40p pylib/dotenv.py`. It must return `{}` for a missing file, or the code must guard with `env_path.exists()`. If `_version` meets a non-numeric version, fall back to string inequality and comment why.

If the S5 section of the P1 spike report says activating an already-active plugin errors, call `POST /api/plugins/smart-catalog/deactivate` first when `current.status == "active"`, and add a test for that path.

`llmenv.py`: inside `cmd_omniroute`, before the `# args.action == "issue-key"` fallthrough, add:

```python
    if args.action == "smart-plugin":
        manifest = json.loads((Path(__file__).parent / "omniroute-smart/plugins/smart-catalog/plugin.json").read_text())
        return emit(ensure_smart_plugin(base_url, initial_password, PLUGIN_PATH, manifest["version"]))
    if args.action == "smart-key":
        return emit(ensure_smart_management_key(base_url, initial_password, Path(args.config).parent / "omniroute-smart.env"))
```

Add `"smart-plugin"` and `"smart-key"` to the `choices` of the `omniroute` action argument (`sed -n 628,658p llmenv.py`).

- [ ] **Step 3: Image, compose, sync script, start hook and make targets**

`docker/omniroute-smart/Dockerfile`: add the two lines below after the existing `COPY omniroute-smart/strategies …` line, before the patch `RUN`.

```dockerfile
COPY omniroute-smart/plugins /opt/omniroute-smart/plugins
COPY omniroute-smart/probe /opt/omniroute-smart/probe
```

Extend the `RUN` cleanup to drop test files: `&& rm -rf /opt/omniroute-smart/patch/test /opt/omniroute-smart/probe/test /opt/omniroute-smart/plugins/*/test`.

`pylib/compose.py`: inside the P1 `if smart.get("enabled"):` block, add:

```python
        # Plugins installed by `make smart-sync` persist in the data volume.
        omniroute_service["environment"]["OMNIROUTE_PLUGINS_DIR"] = "/app/data/plugins"
```

`scripts/omniroute-smart-sync.sh`:

```bash
#!/usr/bin/env bash
# omniroute-smart-sync.sh — push settings.json into the running OmniRoute and install/activate smart-catalog.
set -euo pipefail
# shellcheck disable=SC1091 # Resolved from this script at runtime.
# shellcheck source=../tools/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/../tools/lib.sh"

require_cmd podman yq uv jq
[ -f "$CONFIG_PATH" ] || die "no config at ${CONFIG_PATH}; run 'make setup' first"
if [ "$(yq -r '.smart_routing.enabled' "$CONFIG_PATH")" != "true" ]; then
    log_info "smart_routing.enabled is false; nothing to sync"
    exit 0
fi
settings="$(llmenv --config "$CONFIG_PATH" smart-settings)" || { printf '%s\n' "$settings" >&2; die "smart_routing settings are invalid (see above)"; }
# The image runs as `node`; writing through the container keeps the file owned by it with 0600.
printf '%s\n' "$settings" | podman exec -i omniroute sh -c \
    'umask 077 && mkdir -p /app/data/smart-routing && cat > /app/data/smart-routing/settings.json.tmp && mv /app/data/smart-routing/settings.json.tmp /app/data/smart-routing/settings.json' \
    || die "could not write settings.json into the omniroute container"
log_info "smart-routing settings.json updated"
if ! response="$(llmenv --config "$CONFIG_PATH" omniroute smart-plugin)"; then
    jq -r '.error // "smart-plugin failed"' <<<"$response" >&2
    die "could not install/activate the smart-catalog plugin"
fi
log_info "smart-catalog plugin active"
```

`scripts/start.sh`: after the `provider-provision.sh` call, inside the `if wait_for_tcp_port …` branch:

```bash
    if [ "$(yq -r '.smart_routing.enabled' "$CONFIG_PATH")" = "true" ]; then
        bash "${REPO_DIR}/scripts/omniroute-smart-sync.sh" \
            || log_warn "smart routing sync failed; run 'make smart-sync' (make check-server will report it)"
    fi
```

`Makefile`, in the `tools/run-target.sh` style of the neighbouring targets (help text lives in `scripts/help.sh`, not in `##` comments). Add both names to `.PHONY`:

```make
smart-sync:
	@bash tools/run-target.sh smart-sync -- bash scripts/omniroute-smart-sync.sh

smart-key:
	@bash tools/run-target.sh smart-key -- bash -c 'set -o pipefail; source tools/lib.sh && llmenv --config "$$CONFIG_PATH" omniroute smart-key | jq .'
```

Add matching `_help_line`s in `scripts/help.sh`:
- `smart-sync`: "Push smart-routing settings into OmniRoute and activate the smart-catalog plugin";
- `smart-key`: "Create the smart-catalog management key in omniroute-smart.env (then make restart)".

- [ ] **Step 4: Shell and compose tests**

```python
# tests/test_compose.py, reusing P1's smart_compose helper
def test_smart_routing_persists_plugins_in_the_data_volume(tmp_path):
    assert smart_compose(tmp_path / "absent.env")["environment"]["OMNIROUTE_PLUGINS_DIR"] == "/app/data/plugins"
```

```python
# tests/test_shell.py, reusing run_lifecycle_script(..., smart_routing_enabled=True)
def test_smart_sync_pipes_settings_into_the_container_and_activates(tmp_path: pathlib.Path) -> None:
    result, _config, calls = run_lifecycle_script(tmp_path, "scripts/omniroute-smart-sync.sh", smart_routing_enabled=True)
    assert result.returncode == 0, result.stdout + result.stderr
    recorded = calls.read_text()
    assert "exec -i omniroute sh -c" in recorded
    assert "/app/data/smart-routing/settings.json" in recorded


def test_smart_sync_is_a_noop_when_disabled(tmp_path: pathlib.Path) -> None:
    result, _config, calls = run_lifecycle_script(tmp_path, "scripts/omniroute-smart-sync.sh")
    assert result.returncode == 0
    assert "exec -i omniroute" not in calls.read_text()


def test_start_wires_smart_sync(tmp_path: pathlib.Path) -> None:
    # the enabled/disabled branch is pinned at the sync-script level (it refuses when smart_routing.enabled is false)
    assert "omniroute-smart-sync.sh" in (ROOT / "scripts/start.sh").read_text()
```

The lifecycle harness stubs `podman`. The `llmenv omniroute smart-plugin` call needs a live OmniRoute, so stub it the same way the harness stubs `llmenv omniroute provision`. Check with `rg -n "provision" tests/test_shell.py | head`. If the harness runs the real `uv`, add an env override such as `LLM_ENV_SKIP_SMART_PLUGIN=1` only if a similar switch already exists. Otherwise make the stubbed `uv` return `{"installed": false, "status": "active"}` for that argv.

- [ ] **Step 5: Run the tests, then commit**

Run: `uv run pytest tests/test_omniroute_smart.py tests/test_compose.py tests/test_shell.py -k "smart" -v && bash tools/validate.sh`
Expected: PASS.

```bash
git add pylib/omniroute_smart.py llmenv.py docker/omniroute-smart/Dockerfile pylib/compose.py scripts/omniroute-smart-sync.sh scripts/start.sh Makefile scripts/help.sh tests/test_omniroute_smart.py tests/test_compose.py tests/test_shell.py
git commit -m "feat(smart-routing): post-start settings sync, plugin install/activate, management key"
```

---

### Task 4: Sources (AA, models.dev, Arena) with cache fallback

**Files:**
- Create: `omniroute-smart/plugins/smart-catalog/lib/sources.mjs`
- Test: `omniroute-smart/plugins/smart-catalog/test/sources.test.mjs`

**Interfaces:**
- Consumes: `writeJsonAtomic`, `readJson` (Task 2).
- Produces:
  - `fetchAA(apiKey, fetchImpl) → {version:number, rows:object[]}`;
  - `fetchModelsDev(fetchImpl) → object` (the `api.json` map);
  - `readArena(dbPath) → {boards: {code: Map<normName,{elo,confidence}>, text: Map}, syncedAt: string|null, rows: number}`;
  - `cached(name, cacheDir, fetcher, now) → {data, status: "ok"|"stale", fetched_at}`. It throws when there is neither fresh data nor a cache.

- [ ] **Step 1: Write the failing tests**

```js
// omniroute-smart/plugins/smart-catalog/test/sources.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { fetchAA, readArena, cached } from "../lib/sources.mjs";

test("fetchAA walks every page with the x-api-key header", async () => {
  const seen = [];
  const fetchImpl = async (url, init) => {
    seen.push([url, init.headers["x-api-key"]]);
    const page = Number(new URL(url).searchParams.get("page"));
    return new Response(JSON.stringify({ tier: "free", intelligence_index_version: 4.3,
      pagination: { page, page_size: 200, total_pages: 2, has_more: page < 2 }, data: [{ id: `m${page}` }] }));
  };
  const r = await fetchAA("aa-key", fetchImpl);
  assert.deepEqual(r, { version: 4.3, rows: [{ id: "m1" }, { id: "m2" }] });
  assert.equal(seen.length, 2);
  assert.ok(seen.every(([u, k]) => u.startsWith("https://artificialanalysis.ai/api/v2/language/models/free?") && k === "aa-key"));
});

test("fetchAA without a key throws a stable reason", async () => {
  await assert.rejects(fetchAA("", fetch), /missing_key/);
});

test("readArena maps OmniRoute categories to boards and keeps the latest sync time", () => {
  const db = join(mkdtempSync(join(tmpdir(), "arena-")), "storage.sqlite");
  const d = new DatabaseSync(db);
  d.exec(`CREATE TABLE model_intelligence (model TEXT, source TEXT, category TEXT, score REAL, elo_raw INTEGER, confidence TEXT, synced_at TEXT, expires_at TEXT)`);
  const ins = d.prepare("INSERT INTO model_intelligence VALUES (?,?,?,?,?,?,?,NULL)");
  ins.run("claude-opus-4.7", "arena_elo", "coding", 0.9, 1500, "high", "2026-10-12 10:00:00");
  ins.run("claude-opus-4.7", "arena_elo", "default", 0.9, 1480, "high", "2026-10-12 10:00:00");
  ins.run("claude-opus-4.7", "arena_elo", "review", 0.9, 1480, "high", "2026-10-12 10:00:00");
  ins.run("gpt-6", "models_dev_tier", "coding", 0.5, null, null, "2026-10-12 11:00:00");
  d.close();
  const a = readArena(db);
  assert.deepEqual(a.boards.code.get("claude-opus-4-7"), { elo: 1500, confidence: "high" });
  assert.deepEqual(a.boards.text.get("claude-opus-4-7"), { elo: 1480, confidence: "high" });
  assert.equal(a.boards.code.has("gpt-6"), false);
  assert.equal(a.syncedAt, "2026-10-12T10:00:00Z");
});

test("readArena: a fresh DB without the table is empty; any other DB error is thrown", () => {
  const dir = mkdtempSync(join(tmpdir(), "arena-"));
  const fresh = join(dir, "fresh.sqlite");
  new DatabaseSync(fresh).close();
  assert.equal(readArena(fresh).rows, 0);
  const odd = join(dir, "odd.sqlite");
  const d = new DatabaseSync(odd);
  d.exec("CREATE TABLE model_intelligence (model TEXT)"); // schema drift: no category/elo_raw columns
  d.close();
  assert.throws(() => readArena(odd), /no such column/);
});

test("readArena on a missing database returns empty boards", () => {
  const a = readArena("/nonexistent/storage.sqlite");
  assert.equal(a.rows, 0);
  assert.equal(a.syncedAt, null);
});

test("cached falls back to the last good payload and marks it stale", async () => {
  const dir = mkdtempSync(join(tmpdir(), "cache-"));
  const ok = await cached("aa", dir, async () => ({ v: 1 }), Date.parse("2026-10-13T00:00:00Z"));
  assert.equal(ok.status, "ok");
  const stale = await cached("aa", dir, async () => { throw new Error("AA HTTP 503"); }, Date.parse("2026-10-13T06:00:00Z"));
  assert.deepEqual([stale.status, stale.data, stale.fetched_at], ["stale", { v: 1 }, "2026-10-13T00:00:00.000Z"]);
  await assert.rejects(cached("md", dir, async () => { throw new Error("down"); }), /down/);
});
```

- [ ] **Step 2: Run and see them fail**

Run: `node --test omniroute-smart/plugins/smart-catalog/test/sources.test.mjs`
Expected: FAIL with `Cannot find module '../lib/sources.mjs'`.

- [ ] **Step 3: Implement**

```js
// omniroute-smart/plugins/smart-catalog/lib/sources.mjs
// Data sources. AA free API (x-api-key, paginated); models.dev api.json; OmniRoute's own Arena rows
// (model_intelligence, source 'arena_elo') read-only from storage.sqlite, so the dashboard toggle stays the switch.
import { existsSync } from "node:fs";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { readJson, writeJsonAtomic } from "./files.mjs";
import { norm } from "./match.mjs";

const AA_URL = "https://artificialanalysis.ai/api/v2/language/models/free";
const MODELS_DEV_URL = "https://models.dev/api.json";
// OmniRoute stores the `code` board under category 'coding' and the `text` board under 'default' (+ review/docs/debugging).
// Research (data-sources-quota.md, Arena): the text board is stored under default/review/documentation/debugging with the same ELO.
const BOARD_OF_CATEGORY = { coding: "code", default: "text", review: "text", documentation: "text", debugging: "text" };

export async function fetchAA(apiKey, fetchImpl = fetch) {
  if (!apiKey) throw new Error("missing_key");
  const rows = [];
  let page = 1, totalPages = 1, version = null;
  do {
    const r = await fetchImpl(`${AA_URL}?page=${page}`, { headers: { "x-api-key": apiKey } });
    if (!r.ok) throw new Error(`AA HTTP ${r.status}`);
    const body = await r.json();
    version = body.intelligence_index_version ?? version;
    totalPages = body.pagination?.total_pages ?? 1;
    rows.push(...(body.data ?? []));
  } while (++page <= totalPages);
  return { version, rows };
}

export async function fetchModelsDev(fetchImpl = fetch) {
  const r = await fetchImpl(MODELS_DEV_URL);
  if (!r.ok) throw new Error(`models.dev HTTP ${r.status}`);
  return r.json();
}

export function readArena(dbPath) {
  const boards = { code: new Map(), text: new Map() };
  if (!existsSync(dbPath)) return { boards, syncedAt: null, rows: 0 };
  const db = new DatabaseSync(dbPath, { readOnly: true });
  try {
    let rows;
    try {
      rows = db.prepare(
        "SELECT model, category, elo_raw, confidence, synced_at FROM model_intelligence WHERE source = 'arena_elo' AND elo_raw IS NOT NULL"
      ).all();
    } catch (e) {
      if (!/no such table: model_intelligence/.test(String(e.message))) throw e; // a schema or DB error must surface
      return { boards, syncedAt: null, rows: 0 }; // fresh DB: OmniRoute has not created the table yet
    }
    let latest = null;
    for (const row of rows) {
      const board = BOARD_OF_CATEGORY[row.category];
      if (board) boards[board].set(norm(row.model), { elo: row.elo_raw, confidence: row.confidence });
      if (!latest || row.synced_at > latest) latest = row.synced_at;
    }
    // SQLite datetime('now') is UTC without a zone marker.
    return { boards, syncedAt: latest ? `${latest.replace(" ", "T")}Z` : null, rows: rows.length };
  } finally {
    db.close();
  }
}

/** Run fetcher; on success cache it, on failure serve the last good payload as stale. */
export async function cached(name, cacheDir, fetcher, now = Date.now()) {
  const path = join(cacheDir, `${name}.json`);
  try {
    const data = await fetcher();
    const fetched_at = new Date(now).toISOString();
    writeJsonAtomic(path, { fetched_at, data });
    return { data, status: "ok", fetched_at };
  } catch (error) {
    const last = readJson(path);
    if (!last) throw error;
    return { data: last.data, status: "stale", fetched_at: last.fetched_at };
  }
}
```

`sources.mjs` imports `norm` from `match.mjs`, which Task 6 creates. To keep this task green on its own, create `lib/match.mjs` now with only `norm`. Task 6 extends it.

```js
// omniroute-smart/plugins/smart-catalog/lib/match.mjs
/** Lower-case, drop a trailing "(…)" label, "5p3" → "5.3", collapse punctuation to "-". */
export const norm = (s) =>
  String(s ?? "").toLowerCase().replace(/\s*\([^)]*\)\s*$/, "").replace(/(\d)p(\d)/g, "$1.$2")
    .replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
```

- [ ] **Step 4: Run the tests, then commit**

Run: `node --test 'omniroute-smart/plugins/smart-catalog/test/**/*.test.mjs' && bash tools/validate.sh`
Expected: PASS. Node 24 may print an `ExperimentalWarning` for `node:sqlite`; that is fine.

```bash
git add omniroute-smart/plugins/smart-catalog
git commit -m "feat(smart-catalog): AA, models.dev and Arena sources with stale-cache fallback"
```

---

### Task 5: Id matching (catalog id → models.dev, AA, sibling)

**Files:**
- Modify: `omniroute-smart/plugins/smart-catalog/lib/match.mjs`
- Test: `omniroute-smart/plugins/smart-catalog/test/match.test.mjs`

**Interfaces:**
- Produces:
  - `buildIndex({modelsDev, aaRows}) → {md: Map<"prov/model", entry>, mdByName: Map<norm, entry[]>, aa: Map<norm(slug|name), row>}`;
  - `resolve(id, index, {aliases, providerMap}) → {id, provider, model, mdKey|null, md|null, canonical|null, aa|null, sibling|null, effort|null}`.

  `sibling` is the base id with one variant suffix removed (`-fast`, `-flash`, `-build-fast`, `-code`, `-priority`). It is used for the prior only, never as an identity.

- [ ] **Step 1: Write the failing tests**

```js
// omniroute-smart/plugins/smart-catalog/test/match.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { buildIndex, norm, resolve } from "../lib/match.mjs";

const modelsDev = {
  "opencode-go": { models: {
    "glm-5.3": { id: "glm-5.3", canonical_model_id: "zhipuai/glm-5.3", release_date: "2026-09-01", cost: { input: 1, output: 3 } },
    "glm-5.3-flash": { id: "glm-5.3-flash", canonical_model_id: "zhipuai/glm-5.3-flash", release_date: "2026-09-10", cost: { input: 0.2, output: 0.6 } } } },
  zhipuai: { models: { "glm-5.3": { id: "glm-5.3", canonical_model_id: "zhipuai/glm-5.3" } } },
  openai: { models: { "gpt-6-luna": { id: "gpt-6-luna", canonical_model_id: "openai/gpt-6-luna" } } },
};
const aaRows = [{ slug: "glm-5-3", name: "GLM 5.3 (high)" }, { slug: "gpt-6-luna", name: "GPT-6 Luna" }];
const idx = buildIndex({ modelsDev, aaRows });

test("norm folds punctuation, labels and the 5p3 spelling", () => {
  assert.equal(norm("GLM 5.3 (high)"), "glm-5-3");
  assert.equal(norm("glm-5p3-flash"), "glm-5-3-flash");
});

test("exact models.dev key resolves canonical and AA", () => {
  const r = resolve("opencode-go/glm-5.3", idx);
  assert.equal(r.canonical, "zhipuai/glm-5.3");
  assert.equal(r.aa.slug, "glm-5-3");
  assert.equal(r.sibling, null);
});

test("a -flash variant is its own model and only points at its base as sibling", () => {
  const r = resolve("opencode-go/glm-5.3-flash", idx);
  assert.equal(r.canonical, "zhipuai/glm-5.3-flash");
  assert.equal(r.aa, null); // never borrows the base model's AA row
  assert.equal(r.sibling, "opencode-go/glm-5.3");
});

test("an effort suffix is an option of the same model", () => {
  const r = resolve("codex/gpt-6-luna-high", idx, { providerMap: { codex: "openai" } });
  assert.equal(r.canonical, "openai/gpt-6-luna");
  assert.equal(r.effort, "high");
});

test("unknown provider falls back to a unique cross-provider name match", () => {
  const r = resolve("cursor/glm-5p3-high", idx);
  assert.equal(r.canonical, "zhipuai/glm-5.3");
  assert.equal(r.effort, "high");
});

test("an explicit alias wins", () => {
  const r = resolve("llama-cpp/m-ok", idx, { aliases: { "llama-cpp/m-ok": "opencode-go/glm-5.3" } });
  assert.equal(r.canonical, "zhipuai/glm-5.3");
});

test("no match yields nulls, not a guess", () => {
  const r = resolve("mystery/thing-9", idx);
  assert.deepEqual([r.md, r.canonical, r.aa], [null, null, null]);
});
```

- [ ] **Step 2: Run and see them fail**

Run: `node --test omniroute-smart/plugins/smart-catalog/test/match.test.mjs`
Expected: FAIL (`buildIndex` is not exported).

- [ ] **Step 3: Implement** (append to `lib/match.mjs`)

```js
const EFFORT = /-(minimal|low|medium|high|xhigh|max)$/;
// Variant suffixes are distinct models: never stripped to claim identity (PRD "Plugin maintenance").
const VARIANT = /-(build-fast|fast|flash|code|priority)$/;

export function buildIndex({ modelsDev, aaRows }) {
  const md = new Map(), mdByName = new Map(), aa = new Map();
  for (const [prov, p] of Object.entries(modelsDev ?? {})) {
    for (const [mid, entry] of Object.entries(p.models ?? {})) {
      md.set(`${prov}/${mid}`, entry);
      const k = norm(mid);
      mdByName.set(k, [...(mdByName.get(k) ?? []), entry]);
    }
  }
  // shortcut: several AA effort rows can share a slug stem; the first wins. Upgrade when effort-specific scores matter.
  for (const row of aaRows ?? []) for (const k of [norm(row.slug), norm(row.name)]) if (k && !aa.has(k)) aa.set(k, row);
  return { md, mdByName, aa };
}

export function resolve(id, index, { aliases = {}, providerMap = {} } = {}) {
  const slash = id.indexOf("/");
  const provider = id.slice(0, slash), model = id.slice(slash + 1);
  const effort = model.match(EFFORT)?.[1] ?? null;
  const base = effort ? model.slice(0, -(effort.length + 1)) : model;
  const mdProv = providerMap[provider] ?? provider;
  let mdKey = aliases[id] ?? null;
  if (!mdKey) for (const k of [`${mdProv}/${model}`, `${mdProv}/${base}`]) if (index.md.has(k)) { mdKey = k; break; }
  let md = mdKey ? index.md.get(mdKey) ?? null : null;
  if (!md) {
    const canon = new Set((index.mdByName.get(norm(base)) ?? []).map((e) => e.canonical_model_id).filter(Boolean));
    if (canon.size === 1) md = (index.mdByName.get(norm(base)) ?? []).find((e) => e.canonical_model_id);
  }
  const canonical = md?.canonical_model_id ?? null;
  const aa = canonical ? index.aa.get(norm(canonical.split("/").pop())) ?? null : null;
  const stem = base.replace(VARIANT, "");
  return { id, provider, model, mdKey, md, canonical, aa, sibling: stem !== base ? `${provider}/${stem}` : null, effort };
}
```

- [ ] **Step 4: Run the tests, then commit**

Run: `node --test 'omniroute-smart/plugins/smart-catalog/test/**/*.test.mjs' && bash tools/validate.sh`
Expected: PASS.

```bash
git add omniroute-smart/plugins/smart-catalog
git commit -m "feat(smart-catalog): catalog id matching with explicit variant handling"
```

---

### Task 6: Scoring and the scorecard

**Files:**
- Create: `omniroute-smart/plugins/smart-catalog/lib/score.mjs`
- Test: `omniroute-smart/plugins/smart-catalog/test/score.test.mjs`

**Interfaces:**
- Consumes: `resolve` output (Task 5), `readArena` output (Task 4), settings (Task 1).
- Produces:
  - `percentile(v, xs)`, `DOMAIN_BOARD`, `TASK_KINDS`, `DOMAINS`;
  - `buildScorecard({ids, index, aa:{version, rows, status, fetched_at}, arena, settings, catalogIds, now, matchOpts}) → scorecard` (shape under Interfaces above, without `hygiene` and `bump_suggestions`, which Task 9 adds).

- [ ] **Step 1: Write the failing tests**

```js
// omniroute-smart/plugins/smart-catalog/test/score.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { buildIndex } from "../lib/match.mjs";
import { buildScorecard, percentile } from "../lib/score.mjs";

const now = Date.parse("2026-10-13T00:00:00Z");
const ev = (i, c, a) => ({ artificial_analysis_intelligence_index: i, artificial_analysis_coding_index: c, artificial_analysis_agentic_index: a });
const aaRows = [
  { slug: "glm-5-3", release_date: "2026-09-01", evaluations: ev(60, 55, 50), performance: { median_output_tokens_per_second: 80 } },
  { slug: "gpt-6-luna", release_date: "2026-08-01", evaluations: ev(70, 65, 66), performance: { median_output_tokens_per_second: 120 } },
  { slug: "old-model", release_date: "2024-01-01", evaluations: ev(99, 99, 99), performance: { median_output_tokens_per_second: 999 } },
];
const modelsDev = { "opencode-go": { models: {
  "glm-5.3": { canonical_model_id: "zhipuai/glm-5.3", release_date: "2026-09-01", cost: { input: 1, output: 3, cache_read: 0.1 }, limit: { context: 200000, output: 32000 }, reasoning_options: [{ type: "effort", values: ["low", "high"] }] },
  "glm-5.3-flash": { canonical_model_id: "zhipuai/glm-5.3-flash", release_date: "2026-09-10", cost: { input: 0.2, output: 0.6 } } } },
  openai: { models: { "gpt-6-luna": { canonical_model_id: "openai/gpt-6-luna", cost: { input: 2, output: 8 } } } } };
const settings = { capabilityWeights: {
    plan: { intelligence: 0.55, agentic: 0.20, arena: 0.25 }, review_audit: { intelligence: 0.45, coding: 0.30, arena: 0.25 },
    execute_edit: { coding: 0.45, agentic: 0.35, arena: 0.20 }, mechanical: { intelligence: 0.5, speed: 0.5 } },
  scoreDecayDays: 14, cohortMonths: 12, priorFamilyPenalty: 0.05, aliases: {}, providerMap: { codex: "openai" } };
const arena = { boards: { code: new Map([["gpt-6-luna", { elo: 1500, confidence: "high" }], ["glm-5-3", { elo: 1400, confidence: "medium" }]]), text: new Map() },
  syncedAt: "2026-10-13T00:00:00Z", rows: 2 };
const card = () => buildScorecard({
  ids: ["opencode-go/glm-5.3", "opencode-go/glm-5.3-flash", "codex/gpt-6-luna-high", "mystery/x"],
  index: buildIndex({ modelsDev, aaRows }), aa: { version: 4.3, rows: aaRows, status: "ok", fetched_at: "2026-10-13T00:00:00Z" },
  arena, settings, catalogIds: ["opencode-go/glm-5.3", "opencode-go/glm-5.3-flash", "opencode-go/glm-5.3-fast"], now });

test("percentile is the mid-rank within the cohort", () => {
  assert.equal(percentile(2, [1, 2, 3]), 0.5);
  assert.equal(percentile(null, [1]), null);
});

test("the cohort excludes models older than cohortMonths", () => {
  const e = card().entries["codex/gpt-6-luna-high"];
  assert.equal(e.Q.plan.backend.source, "measured");
  // Two in-cohort AA rows: luna is the top on every AA metric → percentile 0.75; arena code top of 2 → 0.75.
  assert.ok(Math.abs(e.Q.plan.backend.q - 0.75) < 1e-9);
});

test("fresh full-confidence data gives c = 1 and Q = raw", () => {
  const e = card().entries["codex/gpt-6-luna-high"];
  assert.ok(Math.abs(e.Q.plan.backend.c - 1) < 1e-9);
});

test("medium Arena confidence lowers c and blends toward the prior", () => {
  const q = card().entries["opencode-go/glm-5.3"].Q.plan.frontend;
  assert.ok(Math.abs(q.c - (0.55 + 0.20 + 0.25 * 0.7)) < 1e-9);
});

test("a -flash variant without its own data uses its base as prior with the penalty", () => {
  const c = card();
  const flash = c.entries["opencode-go/glm-5.3-flash"].Q.execute_edit.backend;
  const base = c.entries["opencode-go/glm-5.3"].Q.execute_edit.backend;
  assert.equal(flash.source, "prior");
  assert.ok(Math.abs(flash.q - (base.q - 0.05)) < 1e-9);
});

test("unknown models get the median default", () => {
  assert.deepEqual(card().entries["mystery/x"].Q.mechanical.backend, { q: 0.5, c: 0, source: "median_default" });
});

test("infra_docs uses the text board (empty here) and renormalises over AA metrics", () => {
  const e = card().entries["codex/gpt-6-luna-high"].Q.plan.infra_docs;
  assert.ok(Math.abs(e.c - 0.75) < 1e-9); // arena weight missing from c
});

test("catalog block carries price, blended price, percentile, efforts, fast tiers and attribution", () => {
  const c = card();
  const cat = c.entries["opencode-go/glm-5.3"].catalog;
  assert.equal(cat.blended, (3 * 1 + 3) / 4);
  assert.deepEqual(cat.effort_options, ["low", "high"]);
  assert.deepEqual(cat.fast_tiers, ["opencode-go/glm-5.3-fast"]);
  assert.equal(typeof cat.price_percentile, "number");
  assert.match(c.attribution, /Artificial Analysis/);
});

test("stale sources decay confidence", () => {
  const s = buildScorecard({ ids: ["codex/gpt-6-luna-high"], index: buildIndex({ modelsDev, aaRows }),
    aa: { version: 4.3, rows: aaRows, status: "stale", fetched_at: "2026-09-29T00:00:00Z" }, arena, settings, catalogIds: [], now });
  const c = s.entries["codex/gpt-6-luna-high"].Q.plan.backend.c;
  assert.ok(Math.abs(c - (0.75 * Math.exp(-1) + 0.25)) < 1e-9); // AA 14 days old, Arena fresh
});
```

- [ ] **Step 2: Run and see them fail**

Run: `node --test omniroute-smart/plugins/smart-catalog/test/score.test.mjs`
Expected: FAIL with `Cannot find module '../lib/score.mjs'`.

- [ ] **Step 3: Implement**

```js
// omniroute-smart/plugins/smart-catalog/lib/score.mjs
// PRD "Scoring": percentile per metric within its cohort; Q = c·raw + (1−c)·prior; price never enters Q.
import { norm, resolve } from "./match.mjs";

export const TASK_KINDS = ["plan", "review_audit", "execute_edit", "mechanical"];
// No webdev board exists on the Arena mirror (spec deviation 1).
export const DOMAIN_BOARD = { frontend: "code", backend: "code", fullstack: "code", infra_docs: "text" };
export const DOMAINS = Object.keys(DOMAIN_BOARD);
const ARENA_CONF = { high: 1, medium: 0.7, low: 0.4 };
const DAY = 86400e3;
const AA_METRIC = {
  intelligence: (r) => r.evaluations?.artificial_analysis_intelligence_index,
  coding: (r) => r.evaluations?.artificial_analysis_coding_index,
  agentic: (r) => r.evaluations?.artificial_analysis_agentic_index,
  speed: (r) => r.performance?.median_output_tokens_per_second,
};
export const ATTRIBUTION = "Capability scores use data from Artificial Analysis (https://artificialanalysis.ai) and LMArena via OmniRoute.";

export function percentile(v, xs) {
  if (!Number.isFinite(v) || xs.length === 0) return null;
  let below = 0, equal = 0;
  for (const x of xs) x < v ? below++ : x === v && equal++;
  return (below + 0.5 * equal) / xs.length;
}

const finite = (xs) => xs.filter(Number.isFinite);

/** Measured inputs for one resolved model: AA percentiles (conf 1) and Arena percentile per board (conf by votes tier). */
function measure(r, cohortCols, arena) {
  const m = {};
  for (const [k, get] of Object.entries(AA_METRIC)) m[k] = r.aa ? percentile(get(r.aa), cohortCols[k]) : null;
  m.arena = {};
  const key = r.canonical ? norm(r.canonical.split("/").pop()) : null;
  for (const board of ["code", "text"]) {
    const hit = key ? arena.boards[board].get(key) : null;
    const elos = [...arena.boards[board].values()].map((v) => v.elo);
    m.arena[board] = hit ? { x: percentile(hit.elo, elos), conf: ARENA_CONF[hit.confidence] ?? 0.4 } : null;
  }
  return m;
}

function blend(m, weights, board, decay) {
  let num = 0, den = 0, c = 0;
  for (const [metric, w] of Object.entries(weights)) {
    const v = metric === "arena" ? m.arena[board] : m[metric] == null ? null : { x: m[metric], conf: 1 };
    if (!v) continue;
    num += w * v.x; den += w; c += w * v.conf * (metric === "arena" ? decay.arena : decay.aa);
  }
  return { raw: den ? num / den : null, c };
}

export function buildScorecard({ ids, index, aa, arena, settings, catalogIds = [], now = Date.now(), matchOpts }) {
  const opts = matchOpts ?? { aliases: settings.aliases, providerMap: settings.providerMap };
  const cutoff = now - settings.cohortMonths * 30.44 * DAY;
  const cohort = aa.rows.filter((row) => Date.parse(row.release_date) >= cutoff);
  const cohortCols = Object.fromEntries(Object.entries(AA_METRIC).map(([k, get]) => [k, finite(cohort.map(get))]));
  const ageDays = (iso) => (iso ? Math.max(0, (now - Date.parse(iso)) / DAY) : Infinity);
  const decay = { aa: Math.exp(-ageDays(aa.fetched_at) / settings.scoreDecayDays), arena: Math.exp(-ageDays(arena.syncedAt) / settings.scoreDecayDays) };

  const resolved = new Map();
  const get = (id) => resolved.get(id) ?? resolved.set(id, resolve(id, index, opts)).get(id);
  const measuredQ = (r, kind, domain) => {
    const { raw, c } = blend(measure(r, cohortCols, arena), settings.capabilityWeights[kind], DOMAIN_BOARD[domain], decay);
    return raw == null ? null : { raw, c };
  };

  const entries = {};
  const prices = [];
  for (const id of ids) {
    const r = get(id);
    const Q = {};
    for (const kind of TASK_KINDS) {
      Q[kind] = {};
      for (const domain of DOMAINS) {
        const own = measuredQ(r, kind, domain);
        const sib = r.sibling ? measuredQ(get(r.sibling), kind, domain) : null;
        const siblingQ = sib ? sib.c * sib.raw + (1 - sib.c) * 0.5 : null;
        const prior = siblingQ != null ? siblingQ - settings.priorFamilyPenalty : 0.5;
        Q[kind][domain] = own
          ? { q: own.c * own.raw + (1 - own.c) * prior, c: own.c, source: "measured" }
          : { q: prior, c: 0, source: siblingQ != null ? "prior" : "median_default" };
      }
    }
    const cost = r.md?.cost ?? null;
    const blended = cost && Number.isFinite(cost.input) && Number.isFinite(cost.output) ? (3 * cost.input + cost.output) / 4 : null;
    if (blended != null) prices.push(blended);
    const stem = id.replace(/-(minimal|low|medium|high|xhigh|max)$/, "");
    entries[id] = {
      id, canonical_id: r.canonical,
      aa: r.aa ? { slug: r.aa.slug, ...Object.fromEntries(Object.entries(AA_METRIC).map(([k, g]) => [k, g(r.aa) ?? null])) } : null,
      arena: Object.fromEntries(["code", "text"].map((b) => {
        const hit = r.canonical ? arena.boards[b].get(norm(r.canonical.split("/").pop())) : null;
        return [b, hit ?? null];
      })),
      Q,
      catalog: {
        price: cost ? { input: cost.input ?? null, output: cost.output ?? null, cache_read: cost.cache_read ?? null } : null,
        blended, price_percentile: null,
        effort_options: (r.md?.reasoning_options ?? []).filter((o) => o?.type === "effort").flatMap((o) => o.values ?? []),
        fast_tiers: catalogIds.filter((c) => c !== id && new RegExp(`^${stem.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}-(fast|priority|build-fast)(-|$)`).test(c)),
        limits: r.md?.limit ? { context: r.md.limit.context ?? null, output: r.md.limit.output ?? null } : null,
        release_date: r.md?.release_date ?? null,
      },
    };
  }
  for (const e of Object.values(entries)) if (e.catalog.blended != null) e.catalog.price_percentile = percentile(e.catalog.blended, prices);
  return {
    version: 1, generated_at: new Date(now).toISOString(), attribution: ATTRIBUTION,
    intelligence_index_version: aa.version ?? null,
    sources: { aa: { status: aa.status, fetched_at: aa.fetched_at ?? null }, arena: { synced_at: arena.syncedAt, rows: arena.rows } },
    entries,
  };
}
```

`effort_options` here comes from models.dev only. Task 7 merges in the efforts discovered in the provider catalog. If the arithmetic in a test disagrees with the code, recompute the expected value by hand from the PRD formula and fix whichever side is wrong. Never loosen a tolerance.

- [ ] **Step 4: Run the tests, then commit**

Run: `node --test 'omniroute-smart/plugins/smart-catalog/test/**/*.test.mjs' && bash tools/validate.sh`
Expected: PASS.

```bash
git add omniroute-smart/plugins/smart-catalog
git commit -m "feat(smart-catalog): percentile scoring with confidence decay, priors and catalog block"
```

---

### Task 7: Discovery merge and hygiene

**Files:**
- Create: `omniroute-smart/plugins/smart-catalog/lib/catalog.mjs`
- Test: `omniroute-smart/plugins/smart-catalog/test/catalog.test.mjs`

**Interfaces:**
- Consumes: the `api` methods `unhide`, `addModel` and `models` (Task 2).
- Produces:
  - `catalogEfforts(id, catalogIds) → string[]`: effort suffixes the provider exposes for the same stem;
  - `memberIds(combo) → string[]`;
  - `smartCombos(combos) → combo[]`: combos with `strategy === "smart"`;
  - `runHygiene(api, neededIds, catalogIds) → {unhidden, added, missing}`.

- [ ] **Step 1: Write the failing tests**

```js
// omniroute-smart/plugins/smart-catalog/test/catalog.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { catalogEfforts, memberIds, runHygiene, smartCombos } from "../lib/catalog.mjs";

test("member ids accept strings and model steps, skip combo refs", () => {
  const combo = { strategy: "smart", models: ["a/x", { kind: "model", model: "b/y" }, { kind: "combo", comboName: "z" }] };
  assert.deepEqual(memberIds(combo), ["a/x", "b/y"]);
  assert.deepEqual(smartCombos([combo, { strategy: "priority", models: [] }]).length, 1);
});

test("catalog efforts come from sibling ids with effort suffixes", () => {
  const ids = ["cursor/glm-5p3", "cursor/glm-5p3-high", "cursor/glm-5p3-low", "cursor/glm-5p3-flash-high"];
  assert.deepEqual(catalogEfforts("cursor/glm-5p3-high", ids), ["high", "low"]);
});

test("hygiene unhides first, adds only what is still missing, and reports the rest", async () => {
  const calls = [];
  let visible = ["a/ok"];
  const api = {
    unhide: async (p, ids) => { calls.push(["unhide", p, ids]); if (ids.includes("hidden")) visible.push("a/hidden"); },
    addModel: async (p, m) => { calls.push(["add", p, m]); if (m === "new") visible.push("a/new"); },
    models: async () => ({ data: visible.map((id) => ({ id })) }),
  };
  const r = await runHygiene(api, ["a/ok", "a/hidden", "a/new", "a/ghost"], ["a/ok"]);
  assert.deepEqual(r, { unhidden: ["a/hidden"], added: ["a/new"], missing: ["a/ghost"] });
  assert.deepEqual(calls[0], ["unhide", "a", ["hidden", "new", "ghost"]]);
});
```

- [ ] **Step 2: Run and see them fail**

Run: `node --test omniroute-smart/plugins/smart-catalog/test/catalog.test.mjs`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement**

```js
// omniroute-smart/plugins/smart-catalog/lib/catalog.mjs
// Discovery helpers and hygiene: every id a smart combo or bump needs must be visible in /v1/models.
const EFFORT = /-(minimal|low|medium|high|xhigh|max)$/;

export const memberIds = (combo) =>
  (combo.models ?? []).map((m) => (typeof m === "string" ? m : m?.kind === "model" || m?.model ? m.model : null)).filter(Boolean);

export const smartCombos = (combos) => combos.filter((c) => c.strategy === "smart");

export function catalogEfforts(id, catalogIds) {
  const stem = id.replace(EFFORT, "");
  return [...new Set(catalogIds.filter((c) => c.replace(EFFORT, "") === stem && EFFORT.test(c)).map((c) => c.match(EFFORT)[1]))];
}

const byProvider = (ids) => ids.reduce((acc, id) => {
  const i = id.indexOf("/");
  (acc[id.slice(0, i)] ??= []).push(id.slice(i + 1));
  return acc;
}, {});

export async function runHygiene(api, neededIds, catalogIds) {
  const visible = async () => new Set(((await api.models())?.data ?? []).map((m) => m.id));
  const absent = neededIds.filter((id) => !catalogIds.includes(id));
  if (absent.length === 0) return { unhidden: [], added: [], missing: [] };
  for (const [provider, models] of Object.entries(byProvider(absent))) await api.unhide(provider, models).catch(() => {});
  const afterUnhide = await visible();
  const unhidden = absent.filter((id) => afterUnhide.has(id));
  const still = absent.filter((id) => !afterUnhide.has(id));
  for (const id of still) await api.addModel(id.slice(0, id.indexOf("/")), id.slice(id.indexOf("/") + 1)).catch(() => {});
  const afterAdd = await visible();
  return { unhidden, added: still.filter((id) => afterAdd.has(id)), missing: still.filter((id) => !afterAdd.has(id)) };
}
```

In `memberIds`, `m?.kind === "model" || m?.model` keeps model steps written without `kind`. Combo refs have no `model` field (upstream `comboRefStepInputSchema`).

- [ ] **Step 4: Run the tests, then commit**

Run: `node --test 'omniroute-smart/plugins/smart-catalog/test/**/*.test.mjs' && bash tools/validate.sh`
Expected: PASS.

```bash
git add omniroute-smart/plugins/smart-catalog
git commit -m "feat(smart-catalog): discovery helpers and id hygiene (unhide, add, report)"
```

---

### Task 8: Bumps (plan, apply, audit)

**Files:**
- Create: `omniroute-smart/plugins/smart-catalog/lib/bumps.mjs`
- Test: `omniroute-smart/plugins/smart-catalog/test/bumps.test.mjs`

**Interfaces:**
- Consumes: scorecard entries (Task 6), `memberIds` and `smartCombos` (Task 7), `appendJsonl` (Task 2), `task-stats.json` (written by P3; shape under Interfaces).
- Produces:
  - `globToRegExp(glob)`;
  - `dominantTaskKind(taskStats, comboName, now) → task_kind`;
  - `planBumps({combos, rules, scorecard, taskStats, audit, settings, hysteresis, now}) → Edit[]`, where `Edit = {comboId, comboName, rule, from, to, removed: string[], task_kind, q_old, q_new, price_ratio}`;
  - `applyBumps({api, combos, edits, auditPath, settings, now}) → number`, the count applied.

- [ ] **Step 1: Write the failing tests**

```js
// omniroute-smart/plugins/smart-catalog/test/bumps.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { applyBumps, dominantTaskKind, globToRegExp, planBumps } from "../lib/bumps.mjs";

const now = Date.parse("2026-10-13T12:00:00Z");
const Qall = (q) => Object.fromEntries(["plan", "review_audit", "execute_edit", "mechanical"].map((k) =>
  [k, Object.fromEntries(["frontend", "backend", "fullstack", "infra_docs"].map((d) => [d, { q, c: 1, source: "measured" }]))]));
const entry = (q, blended, release_date) => ({ Q: Qall(q), catalog: { blended, release_date } });
const scorecard = { entries: {
  "opencode-go/glm-5.3-flash": entry(0.60, 0.30, "2026-09-10"),
  "opencode-go/glm-5.4-flash": entry(0.62, 0.30, "2026-10-10"),
  "opencode-go/glm-5.5-flash": entry(0.70, 0.90, "2026-10-12"), // newer and better but 3× the price
  "opencode-go/glm-5.2-flash": entry(0.55, 0.25, "2026-06-01"),
} };
const combo = (models) => ({ id: "c1", name: "smart-execution", strategy: "smart", models, config: { k: 1 } });
const settings = { bumpPriceMaxRatio: 1.0, bumpMaxFallbacks: 1 };
const rules = [{ match: "opencode-go/glm-*-flash" }];

test("glob matches one path segment", () => {
  assert.ok(globToRegExp("opencode-go/glm-*-flash").test("opencode-go/glm-5.4-flash"));
  assert.ok(!globToRegExp("opencode-go/glm-*-flash").test("opencode-go/glm-5.4-flash-high"));
});

test("dominant task kind over 7 days, default execute_edit", () => {
  const stats = { days: { "2026-10-12": { "smart-execution": { task_kind: { plan: 1, execute_edit: 5 } } },
                          "2026-09-01": { "smart-execution": { task_kind: { plan: 99 } } } } };
  assert.equal(dominantTaskKind(stats, "smart-execution", now), "execute_edit");
  assert.equal(dominantTaskKind(null, "smart-execution", now), "execute_edit");
});

test("bumps to the newest eligible id within the price cap and keeps the old id as fallback", () => {
  const edits = planBumps({ combos: [combo(["cursor/x", "opencode-go/glm-5.3-flash"])], rules, scorecard, taskStats: null,
    audit: [], settings, hysteresis: 0.05, now });
  assert.equal(edits.length, 1);
  assert.deepEqual([edits[0].from, edits[0].to, edits[0].removed], ["opencode-go/glm-5.3-flash", "opencode-go/glm-5.4-flash", []]);
});

test("no bump when the newer id scores below Q_old − hysteresis", () => {
  const sc = { entries: { ...scorecard.entries, "opencode-go/glm-5.4-flash": entry(0.50, 0.30, "2026-10-10") } };
  const sc2 = { entries: { ...sc.entries, "opencode-go/glm-5.5-flash": entry(0.50, 0.30, "2026-10-12") } };
  assert.equal(planBumps({ combos: [combo(["opencode-go/glm-5.3-flash"])], rules, scorecard: sc2, taskStats: null, audit: [], settings, hysteresis: 0.05, now }).length, 0);
});

test("fallbacks beyond bumpMaxFallbacks are removed, but only ids an earlier bump created", () => {
  const audit = [{ comboId: "c1", rule: "opencode-go/glm-*-flash", from: "opencode-go/glm-5.2-flash", to: "opencode-go/glm-5.3-flash" }];
  const edits = planBumps({ combos: [combo(["opencode-go/glm-5.3-flash", "opencode-go/glm-5.2-flash", "opencode-go/glm-5.0-flash"])],
    rules, scorecard, taskStats: null, audit, settings, hysteresis: 0.05, now });
  assert.deepEqual(edits[0].removed, ["opencode-go/glm-5.2-flash"]); // 5.0-flash was added by the user, never by a bump
});

test("apply rewrites members in place, appends the fallback, PUTs restorable fields and audits", async () => {
  const dir = mkdtempSync(join(tmpdir(), "bumps-"));
  const puts = [];
  const api = { putCombo: async (id, body) => puts.push([id, body]) };
  const combos = [combo([{ kind: "model", providerId: "opencode-go", model: "opencode-go/glm-5.3-flash", connectionId: "k1" }])];
  const edits = planBumps({ combos, rules, scorecard, taskStats: null, audit: [], settings, hysteresis: 0.05, now });
  const n = await applyBumps({ api, combos, edits, auditPath: join(dir, "bump-audit.jsonl"), settings: { ...settings, decisionLogMaxMB: 10, decisionLogFiles: 3 }, now });
  assert.equal(n, 1);
  assert.deepEqual(puts[0][1].models.map((m) => m.model), ["opencode-go/glm-5.4-flash", "opencode-go/glm-5.3-flash"]);
  assert.equal(puts[0][1].models[0].connectionId, "k1");
  assert.deepEqual(puts[0][1].config, { k: 1 });
  assert.equal("id" in puts[0][1], false);
  assert.equal(JSON.parse(readFileSync(join(dir, "bump-audit.jsonl"), "utf8")).to, "opencode-go/glm-5.4-flash");
});

test("overlapping rules bump a member once", async () => {
  const dir = mkdtempSync(join(tmpdir(), "bumps-"));
  const puts = [];
  const api = { putCombo: async (id, body) => puts.push([id, body]) };
  const combos = [combo(["opencode-go/glm-5.3-flash"])];
  const overlapping = [{ match: "opencode-go/glm-*-flash" }, { match: "opencode-go/glm-5.*-flash" }];
  const edits = planBumps({ combos, rules: overlapping, scorecard, taskStats: null, audit: [], settings, hysteresis: 0.05, now });
  const n = await applyBumps({ api, combos, edits, auditPath: join(dir, "bump-audit.jsonl"), settings: { ...settings, decisionLogMaxMB: 10, decisionLogFiles: 3 }, now });
  assert.equal(n, 1);
  assert.deepEqual(combos[0].models.map((m) => (typeof m === "string" ? m : m.model)), ["opencode-go/glm-5.4-flash", "opencode-go/glm-5.3-flash"]);
});
```

- [ ] **Step 2: Run and see them fail**

Run: `node --test omniroute-smart/plugins/smart-catalog/test/bumps.test.mjs`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement**

```js
// omniroute-smart/plugins/smart-catalog/lib/bumps.mjs
// PRD "Bumps (automatic, audited)". A bump never removes a member that no earlier bump created.
import { appendJsonl } from "./files.mjs";
import { memberIds, smartCombos } from "./catalog.mjs";

const DAY = 86400e3;
// Same subset pylib/omniroute_combos.py restores (upstream createComboSchema).
const RESTORABLE = ["name", "description", "models", "strategy", "config", "allowedProviders", "allowedModelFamilies",
  "system_message", "tool_filter_regex", "context_cache_protection", "context_length", "dimensions"];

export const globToRegExp = (glob) =>
  new RegExp(`^${glob.split("*").map((s) => s.replace(/[.+?^${}()|[\]\\]/g, "\\$&")).join("[^/]*")}$`);

export function dominantTaskKind(taskStats, comboName, now = Date.now()) {
  const totals = {};
  for (const [day, combos] of Object.entries(taskStats?.days ?? {})) {
    if (now - Date.parse(`${day}T00:00:00Z`) > 7 * DAY) continue;
    for (const [kind, n] of Object.entries(combos?.[comboName]?.task_kind ?? {})) totals[kind] = (totals[kind] ?? 0) + n;
  }
  const best = Object.entries(totals).sort((a, b) => b[1] - a[1])[0];
  return best ? best[0] : "execute_edit";
}

// Bumps compare Q averaged over the four domains: a bump rule covers every request the combo serves, whatever its domain.
const qOf = (entry, kind) => {
  const row = Object.values(entry?.Q?.[kind] ?? {});
  return row.length ? row.reduce((s, v) => s + v.q, 0) / row.length : null;
};

export function planBumps({ combos, rules, scorecard, taskStats, audit, settings, hysteresis, now = Date.now() }) {
  const edits = [];
  for (const combo of smartCombos(combos)) {
    const members = memberIds(combo);
    const kind = dominantTaskKind(taskStats, combo.name, now);
    for (const rule of rules) {
      const re = globToRegExp(rule.match);
      const primary = members.find((m) => re.test(m));
      const old = scorecard.entries[primary];
      if (!primary || !old?.catalog?.blended || !old.catalog.release_date) continue;
      const qOld = qOf(old, kind);
      const maxRatio = rule.maxPriceRatio ?? settings.bumpPriceMaxRatio;
      const candidates = Object.entries(scorecard.entries)
        .filter(([id, e]) => id !== primary && re.test(id) && !members.includes(id))
        .filter(([, e]) => e.catalog?.blended != null && e.catalog.blended <= maxRatio * old.catalog.blended)
        .filter(([, e]) => e.catalog?.release_date && e.catalog.release_date > old.catalog.release_date)
        .filter(([, e]) => qOf(e, kind) >= qOld - hysteresis)
        .sort((a, b) => b[1].catalog.release_date.localeCompare(a[1].catalog.release_date));
      if (!candidates.length) continue;
      const [to, next] = candidates[0];
      const bumpedFrom = new Set(audit.filter((a) => a.comboId === combo.id && a.rule === rule.match).map((a) => a.from));
      const fallbacks = members.filter((m) => m !== primary && bumpedFrom.has(m));
      const keep = Math.max(0, settings.bumpMaxFallbacks - 1); // the old primary becomes one fallback
      edits.push({
        comboId: combo.id, comboName: combo.name, rule: rule.match, from: primary, to, task_kind: kind,
        removed: fallbacks.slice(keep), q_old: qOld, q_new: qOf(next, kind),
        price_ratio: next.catalog.blended / old.catalog.blended,
      });
    }
  }
  return edits;
}

const idOf = (m) => (typeof m === "string" ? m : m.model);
const withId = (m, id) => (typeof m === "string" ? id : { ...m, model: id, providerId: id.slice(0, id.indexOf("/")) });

export async function applyBumps({ api, combos, edits, auditPath, settings, now = Date.now() }) {
  let applied = 0;
  const done = new Set();
  for (const edit of edits) {
    // overlapping rules can plan two edits for the same member: apply the first only
    if (done.has(`${edit.comboId}|${edit.from}`)) continue;
    done.add(`${edit.comboId}|${edit.from}`);
    const combo = combos.find((c) => c.id === edit.comboId);
    const old = combo.models.find((m) => idOf(m) === edit.from);
    const models = combo.models
      .filter((m) => !edit.removed.includes(idOf(m)))
      .map((m) => (idOf(m) === edit.from ? withId(m, edit.to) : m));
    if (settings.bumpMaxFallbacks > 0) models.push(old);
    const body = Object.fromEntries(RESTORABLE.filter((k) => k in combo).map((k) => [k, k === "models" ? models : combo[k]]));
    await api.putCombo(combo.id, body);
    combo.models = models; // later edits on the same combo see this one
    appendJsonl(auditPath, { at: new Date(now).toISOString(), ...edit }, { maxBytes: settings.decisionLogMaxMB * 1048576, files: settings.decisionLogFiles });
    applied++;
  }
  return applied;
}
```

- [ ] **Step 4: Run the tests, then commit**

Run: `node --test 'omniroute-smart/plugins/smart-catalog/test/**/*.test.mjs' && bash tools/validate.sh`
Expected: PASS.

```bash
git add omniroute-smart/plugins/smart-catalog
git commit -m "feat(smart-catalog): audited bumps with price, score and release-date guards"
```

---

### Task 9: Sync orchestration, scheduler and lifecycle hooks

**Files:**
- Create: `omniroute-smart/plugins/smart-catalog/lib/sync.mjs`, `omniroute-smart/plugins/smart-catalog/index.mjs`
- Test: `omniroute-smart/plugins/smart-catalog/test/sync.test.mjs`, `.../test/index.test.mjs`, `.../test/e2e.test.mjs`

**Interfaces:**
- Consumes: everything from Tasks 2–8.
- Produces:
  - `runSync({api, env, dataDir, dbPath, now, fetchImpl}) → {ok, entries, bumps, error?}`;
  - `tick({deps, now}) → "synced"|"idle"|"busy"|"failed"`;
  - the default export `{onActivate, onDeactivate}`.

- [ ] **Step 1: Write the failing tests**

```js
// omniroute-smart/plugins/smart-catalog/test/index.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import plugin, { tick } from "../index.mjs";
import { writeJsonAtomic } from "../lib/files.mjs";

const deps = (over = {}) => {
  const dataDir = mkdtempSync(join(tmpdir(), "tick-"));
  let syncs = 0;
  return { dataDir, config: async () => ({ syncIntervalHours: 6 }), sync: async () => { syncs++; }, syncs: () => syncs, ...over };
};

test("first tick syncs; a recent success makes the next tick idle", async () => {
  const d = deps();
  assert.equal(await tick({ deps: d, now: 0 }), "synced");
  writeJsonAtomic(join(d.dataDir, "sync-state.json"), { last_success_at: new Date(0).toISOString() });
  assert.equal(await tick({ deps: d, now: 3600e3 }), "idle");
  assert.equal(await tick({ deps: d, now: 6 * 3600e3 }), "synced");
});

test("a failed attempt backs off 15 minutes", async () => {
  const d = deps();
  writeJsonAtomic(join(d.dataDir, "sync-state.json"), { last_attempt_at: new Date(0).toISOString(), last_error: "x" });
  assert.equal(await tick({ deps: d, now: 5 * 60e3 }), "idle");
  assert.equal(await tick({ deps: d, now: 16 * 60e3 }), "synced");
});

test("onActivate returns immediately even when the sync is slow", async () => {
  const t0 = Date.now();
  await plugin.onActivate({ __testDeps: deps({ sync: () => new Promise((r) => setTimeout(r, 5000)) }) });
  assert.ok(Date.now() - t0 < 1000);
  await plugin.onDeactivate();
});
```

```js
// omniroute-smart/plugins/smart-catalog/test/e2e.test.mjs
// Whole sync against a fake OmniRoute + fake AA/models.dev + a temp SQLite: proves wiring, files, modes and bumps.
import { test } from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { mkdtempSync, readFileSync, statSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { makeApi } from "../lib/api.mjs";
import { runSync } from "../lib/sync.mjs";
import { writeJsonAtomic } from "../lib/files.mjs";

const settings = JSON.parse(readFileSync(new URL("./fixtures/settings.json", import.meta.url), "utf8"));

async function fakeOmniRoute(state) {
  const srv = createServer(async (req, res) => {
    let raw = ""; for await (const c of req) raw += c;
    const send = (b, s = 200) => { res.writeHead(s, { "content-type": "application/json" }); res.end(JSON.stringify(b)); };
    if (req.url === "/api/plugins/smart-catalog/config") return send({ config: { bumpMode: state.bumpMode }, configSchema: {} });
    if (req.url === "/api/combos" && req.method === "GET") return send({ combos: state.combos, total: state.combos.length });
    if (req.url.startsWith("/api/combos/") && req.method === "PUT") { state.puts.push(JSON.parse(raw)); return send({}); }
    if (req.url === "/v1/models") return send({ data: state.catalog.map((id) => ({ id })) });
    return send({}, 404);
  });
  await new Promise((r) => srv.listen(0, "127.0.0.1", r));
  return srv;
}

const aaFetch = async (url) => {
  if (url.startsWith("https://models.dev/")) return new Response(readFileSync(new URL("./fixtures/modelsdev.json", import.meta.url)));
  return new Response(readFileSync(new URL("./fixtures/aa-page1.json", import.meta.url)));
};

for (const bumpMode of ["auto", "off"]) {
  test(`sync writes a 0600 scorecard and ${bumpMode === "auto" ? "applies" : "only suggests"} bumps`, async () => {
    const dataDir = mkdtempSync(join(tmpdir(), "e2e-"));
    writeJsonAtomic(join(dataDir, "settings.json"), settings);
    const db = join(dataDir, "storage.sqlite");
    new DatabaseSync(db).exec("CREATE TABLE model_intelligence (model TEXT, source TEXT, category TEXT, score REAL, elo_raw INTEGER, confidence TEXT, synced_at TEXT, expires_at TEXT)");
    const state = { bumpMode, puts: [], catalog: ["opencode-go/glm-5.3-flash", "opencode-go/glm-5.4-flash"],
      combos: [{ id: "c1", name: "smart-execution", strategy: "smart", models: ["opencode-go/glm-5.3-flash"], config: {} }] };
    const srv = await fakeOmniRoute(state);
    const api = makeApi({ base: `http://127.0.0.1:${srv.address().port}`, key: "k" });
    const r = await runSync({ api, env: { OMNIROUTE_PLUGIN_SMART_CATALOG_AA_API_KEY: "AA-SECRET-7f3" }, dataDir, dbPath: db,
      now: Date.parse("2026-10-13T00:00:00Z"), fetchImpl: aaFetch });
    srv.close();
    assert.equal(r.ok, true);
    const card = JSON.parse(readFileSync(join(dataDir, "scorecard.json"), "utf8"));
    assert.equal(statSync(join(dataDir, "scorecard.json")).mode & 0o777, 0o600);
    assert.ok(card.entries["opencode-go/glm-5.3-flash"] && card.entries["opencode-go/glm-5.4-flash"]);
    assert.equal(state.puts.length, bumpMode === "auto" ? 1 : 0);
    assert.equal(card.bump_suggestions.length, 1);
    assert.equal(JSON.parse(readFileSync(join(dataDir, "sync-state.json"), "utf8")).aa, "ok");
    for (const name of ["scorecard.json", "sync-state.json", "cache/aa.json", "cache/modelsdev.json"]) {
      assert.ok(!readFileSync(join(dataDir, name), "utf8").includes("AA-SECRET-7f3"), `${name} leaks the AA key`);
    }
  });
}

test("a missing AA key still produces a scorecard and records missing_key", async () => {
  const dataDir = mkdtempSync(join(tmpdir(), "e2e-"));
  writeJsonAtomic(join(dataDir, "settings.json"), settings);
  const state = { bumpMode: "off", puts: [], catalog: [], combos: [] };
  const srv = await fakeOmniRoute(state);
  const r = await runSync({ api: makeApi({ base: `http://127.0.0.1:${srv.address().port}`, key: "k" }), env: {}, dataDir,
    dbPath: join(dataDir, "absent.sqlite"), now: Date.now(), fetchImpl: aaFetch });
  srv.close();
  assert.equal(r.ok, true);
  assert.equal(JSON.parse(readFileSync(join(dataDir, "sync-state.json"), "utf8")).aa, "missing_key");
});

test("an AA outage with no cache still produces a scorecard and records unavailable", async () => {
  const dataDir = mkdtempSync(join(tmpdir(), "e2e-"));
  writeJsonAtomic(join(dataDir, "settings.json"), settings);
  const state = { bumpMode: "off", puts: [], catalog: [], combos: [] };
  const srv = await fakeOmniRoute(state);
  const downAA = async (url) => (url.startsWith("https://models.dev/") ? aaFetch(url) : new Response("", { status: 503 }));
  const r = await runSync({ api: makeApi({ base: `http://127.0.0.1:${srv.address().port}`, key: "k" }),
    env: { OMNIROUTE_PLUGIN_SMART_CATALOG_AA_API_KEY: "AA-SECRET-7f3" }, dataDir, dbPath: join(dataDir, "absent.sqlite"),
    now: Date.now(), fetchImpl: downAA });
  srv.close();
  assert.equal(r.ok, true);
  assert.ok(statSync(join(dataDir, "scorecard.json")).isFile());
  assert.equal(JSON.parse(readFileSync(join(dataDir, "sync-state.json"), "utf8")).aa, "unavailable");
});

test("a missing settings.json fails the sync with a clear error and no scorecard", async () => {
  const dataDir = mkdtempSync(join(tmpdir(), "e2e-"));
  const r = await runSync({ api: makeApi({ base: "http://127.0.0.1:9", key: "k" }), env: {}, dataDir, dbPath: "/x", now: Date.now(), fetchImpl: aaFetch });
  assert.equal(r.ok, false);
  assert.match(r.error, /settings\.json missing; run make smart-sync/);
});
```

Create these fixtures under `test/fixtures/`, keeping them minimal:
- `settings.json`: run `uv run llmenv.py smart-settings` against a temporary config whose `smart_routing.bumps` is `[{"match": "opencode-go/glm-*-flash"}]`, and save the output.
- `modelsdev.json`: `opencode-go` with `glm-5.3-flash` (release 2026-09-10, cost 0.2/0.6) and `glm-5.4-flash` (release 2026-10-10, cost 0.2/0.6), with `canonical_model_id`s `zhipuai/glm-5.3-flash` and `zhipuai/glm-5.4-flash`.
- `aa-page1.json`: `{"tier":"free","intelligence_index_version":4.3,"pagination":{"page":1,"page_size":200,"total_pages":1,"has_more":false},"data":[…]}`, with rows whose slugs `glm-5-3-flash` and `glm-5-4-flash` have release dates in 2026-09 and 2026-10, and 5.4 scoring higher on every index.

- [ ] **Step 2: Run and see them fail**

Run: `node --test 'omniroute-smart/plugins/smart-catalog/test/**/*.test.mjs'`
Expected: FAIL (`index.mjs` and `sync.mjs` are missing).

- [ ] **Step 3: Implement**

```js
// omniroute-smart/plugins/smart-catalog/lib/sync.mjs
// One sync run. Writes scorecard.json and sync-state.json; never writes secrets; errors carry no bodies.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { configWithDefaults } from "./api.mjs";
import { applyBumps, globToRegExp, planBumps } from "./bumps.mjs";
import { catalogEfforts, memberIds, runHygiene, smartCombos } from "./catalog.mjs";
import { readJson, writeJsonAtomic } from "./files.mjs";
import { buildIndex } from "./match.mjs";
import { buildScorecard } from "./score.mjs";
import { cached, fetchAA, fetchModelsDev, readArena } from "./sources.mjs";
import manifest from "../plugin.json" with { type: "json" };

// shortcut: reads only the current bump-audit.jsonl; rotated files are ignored. Upgrade if the audit log ever rotates.
const readAudit = (path) => {
  try { return readFileSync(path, "utf8").split("\n").filter(Boolean).map((line) => JSON.parse(line)); } catch { return []; }
};

export async function runSync({ api, env, dataDir, dbPath, now = Date.now(), fetchImpl = fetch }) {
  const statePath = join(dataDir, "sync-state.json");
  const prev = readJson(statePath) ?? {};
  const attempt = new Date(now).toISOString();
  try {
    const settings = readJson(join(dataDir, "settings.json"));
    if (!settings) throw new Error("settings.json missing; run make smart-sync");
    const config = configWithDefaults((await api.pluginConfig())?.config, manifest.configSchema);
    const cacheDir = join(dataDir, "cache");
    let aa = { version: null, rows: [], status: "missing_key", fetched_at: null };
    const aaKey = env.OMNIROUTE_PLUGIN_SMART_CATALOG_AA_API_KEY;
    if (aaKey) {
      try {
        const got = await cached("aa", cacheDir, () => fetchAA(aaKey, fetchImpl), now);
        aa = { ...got.data, status: got.status, fetched_at: got.fetched_at };
      } catch {
        // AA down with no cache yet: score from Arena and priors (Review Focus 1); the next tick retries AA
        aa = { version: null, rows: [], status: "unavailable", fetched_at: null };
      }
    }
    const md = await cached("modelsdev", cacheDir, () => fetchModelsDev(fetchImpl), now);
    const arena = readArena(dbPath);
    const combos = (await api.combos())?.combos ?? [];
    const catalogIds = ((await api.models())?.data ?? []).map((m) => m.id);
    const ruleRes = settings.bumps.map((r) => globToRegExp(r.match));
    const ids = [...new Set([...smartCombos(combos).flatMap(memberIds), ...catalogIds.filter((id) => ruleRes.some((re) => re.test(id)))])];
    const index = buildIndex({ modelsDev: md.data, aaRows: aa.rows });
    const card = buildScorecard({ ids, index, aa, arena, settings, catalogIds, now });
    card.sources.modelsdev = { status: md.status, fetched_at: md.fetched_at };
    for (const [id, e] of Object.entries(card.entries)) {
      e.catalog.effort_options = [...new Set([...e.catalog.effort_options, ...catalogEfforts(id, catalogIds)])];
    }
    card.hygiene = await runHygiene(api, ids, catalogIds);
    const edits = planBumps({ combos, rules: settings.bumps, scorecard: card, taskStats: readJson(join(dataDir, "task-stats.json")),
      audit: readAudit(join(dataDir, "bump-audit.jsonl")), settings, hysteresis: config.scoreHysteresis, now });
    card.bump_suggestions = edits;
    const bumps = config.bumpMode === "auto"
      ? await applyBumps({ api, combos, edits, auditPath: join(dataDir, "bump-audit.jsonl"), settings, now })
      : 0;
    writeJsonAtomic(join(dataDir, "scorecard.json"), card);
    writeJsonAtomic(statePath, { last_attempt_at: attempt, last_success_at: attempt, last_error: null, aa: aa.status, modelsdev: md.status });
    return { ok: true, entries: ids.length, bumps };
  } catch (error) {
    writeJsonAtomic(statePath, { ...prev, last_attempt_at: attempt, last_error: String(error.message).slice(0, 200) });
    return { ok: false, error: error.message };
  }
}
```

`import … with { type: "json" }` needs Node ≥ 22. Both host (24) and image (26) qualify.

```js
// omniroute-smart/plugins/smart-catalog/index.mjs
// OmniRoute plugin entry. Hooks return at once (10 s hook timeout kills the child with no respawn).
import { join } from "node:path";
import { makeApi, configWithDefaults } from "./lib/api.mjs";
import { readJson } from "./lib/files.mjs";
import { runSync } from "./lib/sync.mjs";
import manifest from "./plugin.json" with { type: "json" };

const TICK_MS = 5 * 60e3;
const RETRY_MS = 15 * 60e3;
let timer = null;
let running = false;

export async function tick({ deps, now = Date.now() }) {
  if (running) return "busy";
  const state = readJson(join(deps.dataDir, "sync-state.json")) ?? {};
  const config = await deps.config();
  const since = (iso) => (iso ? now - Date.parse(iso) : Infinity);
  if (since(state.last_success_at) < config.syncIntervalHours * 3600e3) return "idle";
  if (state.last_error && since(state.last_attempt_at) < RETRY_MS) return "idle";
  running = true;
  try { await deps.sync(); return "synced"; } catch { return "failed"; } finally { running = false; }
}

function liveDeps() {
  const env = process.env;
  const api = makeApi({ base: `http://127.0.0.1:${env.PORT || 20128}`, key: env.OMNIROUTE_PLUGIN_SMART_CATALOG_MANAGEMENT_KEY || "" });
  const dataDir = "/app/data/smart-routing";
  return {
    dataDir,
    config: async () => configWithDefaults((await api.pluginConfig())?.config, manifest.configSchema),
    sync: () => runSync({ api, env, dataDir, dbPath: "/app/data/storage.sqlite" }),
  };
}

export default {
  async onActivate(ctx) {
    const deps = ctx?.__testDeps ?? liveDeps();
    const run = () => { tick({ deps }).catch(() => {}); };
    clearInterval(timer);
    timer = setInterval(run, TICK_MS);
    run(); // not awaited
  },
  async onDeactivate() {
    clearInterval(timer);
    timer = null;
  },
};
```

`__testDeps` is the only test seam. `liveDeps` is used whenever OmniRoute calls the hook with its own context.

- [ ] **Step 4: Run the tests, then commit**

Run: `node --test 'omniroute-smart/plugins/smart-catalog/test/**/*.test.mjs' && bash tools/validate.sh`
Expected: PASS. The e2e test proves the scorecard is 0600, that bumps follow `bumpMode`, that a missing key is tolerated, and that a missing `settings.json` fails loudly.

```bash
git add omniroute-smart/plugins/smart-catalog
git commit -m "feat(smart-catalog): sync orchestration, tick scheduler and non-blocking lifecycle"
```

---

### Task 10: `make smart-probe` and the status JSON

**Files:**
- Create: `omniroute-smart/probe/smart-probe.mjs`, `omniroute-smart/probe/smart-status.mjs`, `omniroute-smart/probe/test/probe.test.mjs`
- Create: `scripts/smart-probe.sh`
- Modify: `Makefile`, `scripts/help.sh`, `tests/test_shell.py`

**Interfaces:**
- Consumes: `QUESTIONS` (`omniroute-smart/shared/classifier-questions.mjs`, from P1).
- Produces:
  - `probeAA(key, fetchImpl)`, `probeModelsDev(fetchImpl)`, `probeArena(dbPath, now)` and `probeLaya(url, fetchImpl)`, each returning `{name, ok, detail, ms}`;
  - `status({dbPath, dataDir, now}) → {plugin_status, sync_interval_hours, scorecard_age_minutes, last_error}`;
  - `make smart-probe`, which exits 1 if any probe fails.

- [ ] **Step 1: Write the failing tests**

```js
// omniroute-smart/probe/test/probe.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync, utimesSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { probeAA, probeArena, probeLaya } from "../smart-probe.mjs";
import { status } from "../smart-status.mjs";

test("AA probe reports missing key without calling out", async () => {
  const r = await probeAA("", () => assert.fail("no fetch"));
  assert.deepEqual([r.ok, r.detail], [false, "OMNIROUTE_PLUGIN_SMART_CATALOG_AA_API_KEY is not set"]);
});

test("AA probe never echoes the key", async () => {
  const r = await probeAA("secret", async () => new Response("{}", { status: 401 }));
  assert.equal(r.ok, false);
  assert.ok(!JSON.stringify(r).includes("secret"));
});

test("Arena probe fails when the newest row is older than 48 h", () => {
  const db = join(mkdtempSync(join(tmpdir(), "p-")), "s.sqlite");
  const d = new DatabaseSync(db);
  d.exec("CREATE TABLE model_intelligence (model TEXT, source TEXT, category TEXT, score REAL, elo_raw INTEGER, confidence TEXT, synced_at TEXT, expires_at TEXT)");
  d.prepare("INSERT INTO model_intelligence VALUES ('m','arena_elo','coding',1,1500,'high','2026-10-10 00:00:00',NULL)").run();
  d.close();
  assert.equal(probeArena(db, Date.parse("2026-10-11T00:00:00Z")).ok, true);
  assert.equal(probeArena(db, Date.parse("2026-10-13T00:00:01Z")).ok, false);
});

test("Laya probe posts the five questions in one request", async () => {
  let body;
  const r = await probeLaya("http://system-one:8000", async (url, init) => {
    body = JSON.parse(init.body);
    return new Response(JSON.stringify({ answers: { task_kind: { choice: "plan" } } }));
  });
  assert.equal(r.ok, true);
  assert.deepEqual(Object.keys(body.questions).sort(), ["complexity", "domain", "scope", "second_opinion", "task_kind"]);
});

test("status reads plugin row and scorecard age", () => {
  const dir = mkdtempSync(join(tmpdir(), "st-"));
  const db = join(dir, "storage.sqlite");
  const d = new DatabaseSync(db);
  d.exec("CREATE TABLE plugins (name TEXT, status TEXT, config TEXT)");
  d.prepare("INSERT INTO plugins VALUES ('smart-catalog','active','{\"syncIntervalHours\":3}')").run();
  d.close();
  const card = join(dir, "scorecard.json");
  writeFileSync(card, "{}");
  const t = Date.parse("2026-10-13T00:00:00Z") / 1000;
  utimesSync(card, t, t);
  writeFileSync(join(dir, "sync-state.json"), JSON.stringify({ last_error: null }));
  assert.deepEqual(status({ dbPath: db, dataDir: dir, now: (t + 600) * 1000 }),
    { plugin_status: "active", sync_interval_hours: 3, scorecard_age_minutes: 10, last_error: null });
});
```

- [ ] **Step 2: Implement**

```js
// omniroute-smart/probe/smart-probe.mjs
// Runs INSIDE the omniroute container (AA key in env, storage.sqlite, compose DNS). Prints one line per source.
import { DatabaseSync } from "node:sqlite";
import { existsSync, realpathSync } from "node:fs";
import { pathToFileURL } from "node:url";
import { QUESTIONS } from "../shared/classifier-questions.mjs";

const timed = async (name, fn) => {
  const t0 = performance.now();
  try { const detail = await fn(); return { name, ok: true, detail, ms: Math.round(performance.now() - t0) }; }
  catch (e) { return { name, ok: false, detail: e.message, ms: Math.round(performance.now() - t0) }; }
};

export const probeAA = (key, fetchImpl = fetch) => timed("artificial-analysis", async () => {
  if (!key) throw new Error("OMNIROUTE_PLUGIN_SMART_CATALOG_AA_API_KEY is not set");
  const r = await fetchImpl("https://artificialanalysis.ai/api/v2/language/models/free?page=1", { headers: { "x-api-key": key } });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  const body = await r.json();
  return `${body.data?.length ?? 0} models, index v${body.intelligence_index_version}, remaining ${r.headers.get("x-ratelimit-remaining") ?? "?"}`;
});

export const probeModelsDev = (fetchImpl = fetch) => timed("models.dev", async () => {
  const r = await fetchImpl("https://models.dev/api.json");
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  const n = Object.keys(await r.json()).length;
  if (n < 50) throw new Error(`only ${n} providers`);
  return `${n} providers`;
});

export function probeArena(dbPath, now = Date.now()) {
  const name = "arena (OmniRoute sync)";
  if (!existsSync(dbPath)) return { name, ok: false, detail: `${dbPath} not found`, ms: 0 };
  const db = new DatabaseSync(dbPath, { readOnly: true });
  try {
    let row = null;
    try { row = db.prepare("SELECT max(synced_at) AS at, count(*) AS n FROM model_intelligence WHERE source='arena_elo'").get(); }
    catch (e) { if (!/no such table: model_intelligence/.test(String(e.message))) return { name, ok: false, detail: `arena query failed: ${e.message}`, ms: 0 }; }
    if (!row?.at) return { name, ok: false, detail: "no arena_elo rows (enable Arena ELO Sync in /dashboard/settings/feature-flags)", ms: 0 };
    const ageH = (now - Date.parse(`${row.at.replace(" ", "T")}Z`)) / 3600e3;
    return { name, ok: ageH <= 48, detail: `${row.n} rows, newest ${ageH.toFixed(1)} h old (limit 48 h)`, ms: 0 };
  } finally { db.close(); }
}

export const probeLaya = (base, fetchImpl = fetch) => timed("system-one laya", async () => {
  const r = await fetchImpl(`${base}/v1/systemone`, { method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ model: "jev-1", state: { request: "Plan the migration of the auth service to OAuth2." }, questions: QUESTIONS }),
    signal: AbortSignal.timeout(5000) });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  const body = await r.json();
  if (!(body.answers ?? body).task_kind) throw new Error("no task_kind answer");
  return "5 questions answered";
});

if (process.argv[1] && import.meta.url === pathToFileURL(realpathSync(process.argv[1])).href) {
  const results = [
    await probeAA(process.env.OMNIROUTE_PLUGIN_SMART_CATALOG_AA_API_KEY),
    await probeModelsDev(),
    probeArena("/app/data/storage.sqlite"),
    await probeLaya(process.env.SMART_PROBE_LAYA_URL || "http://system-one:8000"),
  ];
  for (const r of results) console.log(`${r.ok ? "PASS" : "FAIL"} ${r.name}: ${r.detail} (${r.ms} ms)`);
  process.exit(results.every((r) => r.ok) ? 0 : 1);
}
```

```js
// omniroute-smart/probe/smart-status.mjs
// Read-only status for check-server: plugin row, configured interval, scorecard age, last sync error.
import { existsSync, readFileSync, realpathSync, statSync } from "node:fs";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { pathToFileURL } from "node:url";

export function status({ dbPath = "/app/data/storage.sqlite", dataDir = "/app/data/smart-routing", now = Date.now() } = {}) {
  let row = null;
  if (existsSync(dbPath)) {
    const db = new DatabaseSync(dbPath, { readOnly: true });
    try { row = db.prepare("SELECT status, config FROM plugins WHERE name = 'smart-catalog'").get() ?? null; } finally { db.close(); }
  }
  const card = join(dataDir, "scorecard.json");
  const statePath = join(dataDir, "sync-state.json");
  const state = existsSync(statePath) ? JSON.parse(readFileSync(statePath, "utf8")) : {};
  return {
    plugin_status: row?.status ?? "missing",
    sync_interval_hours: JSON.parse(row?.config || "{}").syncIntervalHours ?? 6,
    scorecard_age_minutes: existsSync(card) ? Math.round(((now - statSync(card).mtimeMs) / 60e3) * 10) / 10 : null,
    last_error: state.last_error ?? null,
  };
}

if (process.argv[1] && import.meta.url === pathToFileURL(realpathSync(process.argv[1])).href) console.log(JSON.stringify(status()));
```

The plugin row's saved `config` omits fields left at their defaults, so a missing `syncIntervalHours` means the manifest default, 6.

`scripts/smart-probe.sh`:

```bash
#!/usr/bin/env bash
# smart-probe.sh — test each smart-routing data source and System One backend from inside OmniRoute.
set -euo pipefail
# shellcheck disable=SC1091 # Resolved from this script at runtime.
# shellcheck source=../tools/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/../tools/lib.sh"
require_cmd podman yq
[ "$(yq -r '.smart_routing.enabled' "$CONFIG_PATH")" = "true" ] || die "smart_routing.enabled is false"
podman exec omniroute node /opt/omniroute-smart/probe/smart-probe.mjs
```

`Makefile`: a `smart-probe` target (added to `.PHONY`) with the recipe `@bash tools/run-target.sh smart-probe -- bash scripts/smart-probe.sh`, plus a `_help_line` "Probe smart-routing data sources and System One backends". In `tests/test_shell.py`, assert that the Makefile exposes `smart-probe:` and that the script refuses when disabled (the lifecycle harness with the default config gives exit ≠ 0 and the message `smart_routing.enabled is false`).

- [ ] **Step 3: Run the tests, then commit**

Run: `node --test 'omniroute-smart/**/*.test.mjs' && uv run pytest tests/test_shell.py -k smart -v && bash tools/validate.sh`
Expected: PASS.

```bash
git add omniroute-smart/probe scripts/smart-probe.sh Makefile scripts/help.sh tests/test_shell.py
git commit -m "feat(smart-routing): smart-probe for data sources and Laya; status JSON"
```

---

### Task 11: `check-server` rows for the plugin and scorecard

**Files:**
- Modify: `scripts/check-server.sh` (the P1 "Smart routing" section), `tests/test_shell.py`

**Interfaces:**
- Consumes: `smart-status.mjs` (Task 10).
- Produces, when enabled:
  - `smart-routing plugin is active` (`plugin_status == "active"`);
  - `smart-routing scorecard is fresh` (age < `sync_interval_hours`·60 + 30 minutes). The FAIL row prints `last_error` when it is set.

- [ ] **Step 1: Write the failing tests**

```python
def test_check_server_reports_plugin_and_scorecard_rows(tmp_path: pathlib.Path) -> None:
    status = '{"plugin_status":"active","sync_interval_hours":6,"scorecard_age_minutes":400,"last_error":"AA HTTP 503"}'
    result, _ = run_check_server(tmp_path, {}, llm_server_enabled=False, smart_routing_enabled=True, smart_status=status)
    assert "PASS identity=smart-routing plugin is active" in result.stdout
    # bad() logs through log_error, which writes to stderr
    assert "FAIL identity=smart-routing scorecard is fresh" in result.stderr
    assert "AA HTTP 503" in result.stderr
```

P1 Task 12 gave `run_check_server` the `smart_routing_enabled` keyword and the label stubs. Add a `smart_status: str | None = None` keyword: when set, the stubbed `podman exec omniroute node …smart-status.mjs` prints it (pass it through a `STUB_SMART_STATUS` env var).

- [ ] **Step 2: Implement**

Append to the enabled branch of the P1 section:

```bash
    smart_status="$(podman exec omniroute node /opt/omniroute-smart/probe/smart-status.mjs 2>/dev/null || echo '{}')"
    if [ "$(jq -r '.plugin_status // "missing"' <<<"$smart_status")" = "active" ]; then
        ok "Verdict: PASS identity=smart-routing plugin is active"
    else
        bad "Verdict: FAIL identity=smart-routing plugin is active (status $(jq -r '.plugin_status // "missing"' <<<"$smart_status"); run make smart-sync)"
    fi
    if jq -e '.scorecard_age_minutes != null and .scorecard_age_minutes < (.sync_interval_hours * 60 + 30)' <<<"$smart_status" >/dev/null; then
        ok "Verdict: PASS identity=smart-routing scorecard is fresh"
    else
        bad "Verdict: FAIL identity=smart-routing scorecard is fresh (age $(jq -r '.scorecard_age_minutes // "none"' <<<"$smart_status") min; last error: $(jq -r '.last_error // "none"' <<<"$smart_status"))"
    fi
```

- [ ] **Step 3: Run the tests, then commit**

Run: `uv run pytest tests/test_shell.py -k "smart" -v && bash tools/validate.sh`
Expected: PASS.

```bash
git add scripts/check-server.sh tests/test_shell.py
git commit -m "feat(check-server): smart-catalog plugin and scorecard freshness rows"
```

---

### Task 12: Container smoke test on a throwaway instance

This is an execution task run by one subagent. It changes no repo code unless a defect is found; a fix goes into the owning task's files with its own test. Results go into `docs/prds/omniroute-smart-routing-research/p2-smoke.md`.

**Files:**
- Create: `docs/prds/omniroute-smart-routing-research/p2-smoke.md`

**Interfaces:**
- Consumes: the derived image rebuilt with this plan's Dockerfile, plus every task above.
- Produces: a PASS/FAIL table for plugin install, activation, a real sync (AA and models.dev), scorecard mode, env scoping, the probe and the status JSON.

- [ ] **Step 1: Rebuild the derived image from a temporary config copy (P1 Task 7 method)**

Run: `TMPCFG=$(mktemp) && yq '.smart_routing.enabled = true' ~/.config/llm-env/models.yml > "$TMPCFG" && LLM_ENV_CONFIG="$TMPCFG" bash scripts/omniroute-smart-build.sh; rm -f "$TMPCFG"`
Expected: the build passes. `podman run --rm --entrypoint ls localhost/llm-env-omniroute-smart:latest /opt/omniroute-smart/plugins/smart-catalog /opt/omniroute-smart/probe` lists the files, with no `test/` directory.

- [ ] **Step 2: Phase A, a throwaway container that creates a management key**

Steps 2–3 use `uv run python -c "…"` one-liners that reuse `pylib`'s `_login`, `_request` and `ensure_smart_plugin` against the throwaway container. They are an allowed exception to AGENTS.md's "Python only through `uv run llmenv.py`" rule, like `uv run pytest`: ephemeral, never committed, never pointed at production.

Run Steps 2–5 in **one** shell session (one script file, or one persistent terminal), so `IT_PW`, `MGMT`, `wait_up` and the cleanup trap survive between steps. If the session dies, run the Step 5 cleanup by hand before anything else.

```bash
IT_PW="$(openssl rand -hex 16)"
podman volume create omniroute-smart-it-data >/dev/null
trap 'podman rm -f omniroute-smart-it >/dev/null 2>&1; podman volume rm -f omniroute-smart-it-data >/dev/null 2>&1' EXIT
wait_up() {
    # the health path P1's spike report proved, else the root
    for _ in $(seq 60); do { curl -sf http://127.0.0.1:29130/api/health || curl -sf http://127.0.0.1:29130/; } >/dev/null 2>&1 && return 0; sleep 2; done
    podman logs --tail 100 omniroute-smart-it; echo "omniroute-smart-it not up after 120 s" >&2; return 1
}
podman run -d --name omniroute-smart-it -p 127.0.0.1:29130:20128 -e PORT=20128 -e INITIAL_PASSWORD="$IT_PW" \
  -e OMNIROUTE_PLUGINS_DIR=/app/data/plugins -v omniroute-smart-it-data:/app/data localhost/llm-env-omniroute-smart:latest
wait_up || exit 1
MGMT="$(uv run python -c "
from pylib.omniroute import _login, _request
t = _login('http://127.0.0.1:29130', '$IT_PW')
print(_request('POST', 'http://127.0.0.1:29130/api/keys', t, {'name': 'it', 'scopes': ['manage']})['key'])")"
podman rm -f omniroute-smart-it >/dev/null
```

Never echo `$MGMT` or `$IT_PW`.

- [ ] **Step 3: Phase B, restart with the keys in env, push settings, install and activate**

```bash
ENVF="$(mktemp)"; chmod 600 "$ENVF"
{ grep -E '^OMNIROUTE_PLUGIN_SMART_CATALOG_AA_API_KEY=' ~/.config/llm-env/omniroute-smart.env || true
  echo "OMNIROUTE_PLUGIN_SMART_CATALOG_MANAGEMENT_KEY=$MGMT"; echo "OMNIROUTE_PLUGIN_OTHER_SECRET=present"; } > "$ENVF"
podman run -d --name omniroute-smart-it -p 127.0.0.1:29130:20128 -e PORT=20128 -e INITIAL_PASSWORD="$IT_PW" \
  -e OMNIROUTE_PLUGINS_DIR=/app/data/plugins --env-file "$ENVF" -v omniroute-smart-it-data:/app/data localhost/llm-env-omniroute-smart:latest
rm -f "$ENVF"
wait_up || exit 1
TMPCFG=$(mktemp); yq '.smart_routing.enabled = true | .smart_routing.bumps = [{"match": "opencode-go/glm-*-flash"}]' ~/.config/llm-env/models.yml > "$TMPCFG"
uv run llmenv.py --config "$TMPCFG" smart-settings | podman exec -i omniroute-smart-it sh -c 'umask 077 && mkdir -p /app/data/smart-routing && cat > /app/data/smart-routing/settings.json'
rm -f "$TMPCFG"
uv run python -c "
from pylib.omniroute_smart import ensure_smart_plugin, PLUGIN_PATH
print(ensure_smart_plugin('http://127.0.0.1:29130', '$IT_PW', PLUGIN_PATH, '1.0.0'))"
```

Reading `omniroute-smart.env` with `grep` into a temporary 0600 file is the only use of the live secrets file. Its content is never printed.

Expected: `{'installed': True, 'status': 'active'}`.

- [ ] **Step 4: Verify (poll for at most 3 minutes for the first sync)**

```bash
for i in $(seq 1 36); do podman exec omniroute-smart-it test -f /app/data/smart-routing/scorecard.json && break; sleep 5; done
podman exec omniroute-smart-it node /opt/omniroute-smart/probe/smart-status.mjs
podman exec omniroute-smart-it sh -c 'stat -c "%a %n" /app/data/smart-routing/*.json; jq -c "{aa,modelsdev,last_error}" /app/data/smart-routing/sync-state.json; jq ".sources, (.entries|length), .attribution" /app/data/smart-routing/scorecard.json'
podman exec -e SMART_PROBE_LAYA_URL=http://127.0.0.1:1 omniroute-smart-it node /opt/omniroute-smart/probe/smart-probe.mjs || true
```

PASS criteria, recorded one per row in `p2-smoke.md`:
- the plugin status is `active`;
- every `.json` file is mode `600`;
- `sync-state.aa` is `ok` when a key was present, otherwise `missing_key`;
- `modelsdev` is `ok`;
- `attribution` is present;
- the probe prints PASS for AA and models.dev. Arena may FAIL in a fresh container; record it as expected only if the detail says "no arena_elo rows". Laya FAIL is expected here because the throwaway is not on the compose network.

**Env scoping:** check that the plugin child does not see `OMNIROUTE_PLUGIN_OTHER_SECRET`. A temporary debug line would need a rebuild, so instead confirm via the P1 S5 result. Record "covered by S5" plus the S5 evidence line.

- [ ] **Step 5: Clean up, check production is untouched, commit the report**

```bash
podman rm -f omniroute-smart-it; podman volume rm -f omniroute-smart-it-data
podman ps -a --format '{{.Names}}' | rg -c 'omniroute-smart-it' || echo "0 it containers"
podman inspect --format '{{.State.StartedAt}}' omniroute
git add docs/prds/omniroute-smart-routing-research/p2-smoke.md
git commit -m "docs(smart-routing): P2 container smoke results"
```

Expected: `0 it containers`, and the production `StartedAt` is the same as before this task. Record both.

---

### Task 13: PRD deviations and final verification

**Files:**
- Modify: `docs/prds/omniroute-smart-routing-v2.1-prd.md`

**Interfaces:**
- Produces: PRD text that matches what P2 built, which P3 and P4 read.

- [ ] **Step 1: Record the five spec deviations in the PRD**

Edit these PRD sections, citing this plan:
- **Scoring:** the domain → Arena board table.
- **Input/Output** and **Plugin maintenance:** the price source and the hygiene routes (`PATCH`/`POST /api/provider-models`).
- **Data storage:** `settings.json` delivery via `make smart-sync`, and the management key via `make smart-key`.
- **Architecture:** `make smart-probe` scope in P2 vs P3.
- **Plugin maintenance → Bumps:** the Q guard uses the mean over the four domains.

Add a clarification-summary row: `15 | P2 implementation: Arena webdev unavailable → code board; price from models.dev api.json; settings pushed via podman exec; smart-key automates the manage-scoped key`.

- [ ] **Step 2: Full verification**

Run: `bash tools/validate.sh && bash tools/test.sh`
Expected: `All checks passed.`, pytest passes, and `node --test` reports 0 failures.

- [ ] **Step 3: Commit**

```bash
git add docs/prds
git commit -m "docs(prd): record P2 smart-catalog implementation deviations"
```

---

## Self-Review Notes (author)

**Spec coverage (PRD, P2 scope):**

| PRD item | Task |
|---|---|
| Config schema | 2 |
| AA client and id matching | 4, 5 |
| models.dev capability fetch | 4 |
| Scoring and `scorecard.json` (percentiles, confidence with decay, prior, `score_source`, attribution) | 6 |
| Discovery and hygiene | 6, 7 |
| Automatic bumps with audit (price, score with hysteresis against the 7-day dominant kind, release date, fallbacks) | 8 |
| Probes | 10 (System One remotes in P3) |
| Plugin active and scorecard freshness health | 11 |
| Settings in `models.yml` validated by llm-env | 1 |
| `env_file` keys reach only the plugin | P1 site 6 + S5; used in 9 and 12 |
| Files 0600 | 2, 9, 12 |
| Loopback config read with the management key | 2, 3, 9 |

**Deliberately not here:**
- the strategy and everything at request time (P3);
- remote System One probes (P3);
- fusion (P4);
- the operator guide (P4);
- the live enablement and the 24 h timer observation (P5).

**Type consistency:**
- `scorecard.entries[id].Q[kind][domain] = {q, c, source}` is used the same way in Tasks 6, 8 and 9 and in the Interfaces block that P3 reads.
- `memberIds` and `smartCombos` are defined in Task 7 and used in Tasks 8 and 9.
- `appendJsonl` options `{maxBytes, files}` are the same in Tasks 2 and 8.
