# OmniRoute Smart Routing P3: `smart` Strategy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the `smart` combo strategy module. For every request on a `smart` combo it:
- classifies the request with System One (Laya first, then consented remote Jev backends, then an LLM arbiter, then conservative defaults);
- gates the members and ranks them on capability plus quota scarcity, forfeiture, failures, declared order and price;
- picks the effort;
- hands the ordered targets to OmniRoute's normal combo loop;
- fails closed with `503 smart_routing_unavailable` and a reason;
- writes one `decisions.jsonl` line per client request, plus daily `task-stats.json` aggregates.

Fusion (single vs panel) is P4. In P3 every request runs single, and the decision only records `fusion_eligible`.

**Architecture:**
- **Delivery.** `omniroute-smart/strategies/smart.mjs` is the module P1's patch loads. Its helpers live in `omniroute-smart/strategies/smart/*.mjs`. They sit in a subdirectory, because every top-level `.mjs` file in `strategies/` becomes a strategy name.
- **Pure math.** Quota terms, gates, classes, utility, effort, failure EWMA and System One answer handling are pure functions with unit tests, including the PRD's worked examples. I/O is confined to:
  - System One HTTP;
  - reading `settings.json` and `scorecard.json` (mtime-cached);
  - the decision log;
  - the injected OmniRoute context.
- **Per-request flow.** `prelude(ctx)` classifies and ranks the members, and answers a 503 itself when nothing fits. `order(targets)` re-orders the targets OmniRoute resolved into that ranking. `observe(outcome)` feeds failures and the decision line.

  The three share a per-request store, `globalThis.__omniSmart.als.getStore()` (an AsyncLocalStorage that P1's patch enters at the prelude site). Concurrent requests never see each other's decisions.
- **No database query per request.**
  - Quota comes from OmniRoute's in-memory quota cache through the context.
  - Cursor/Claude usage is cached by the strategy with stale-while-revalidate.
  - Plugin config is cached for `pluginConfigTtlSeconds`.
  - `F` is in memory.

**Tech Stack:** Node 26 in the image and Node 24 on the host. Plain ES modules with JSDoc, built-ins only (`node:test`, `node:async_hooks`, `fetch`, `AbortSignal.timeout`). Python 3 for the settings limits. Bash for `smart-probe` and `check-server`.

**Spec:** `docs/prds/omniroute-smart-routing-v2.1-prd.md`. The sections that matter here are "System One classification", "Request-time ranking (`smart`)", "Quota data per provider", "Data requirements", "Edge cases", the "Error response" and the "Acceptance Criteria".

Also read `docs/prds/omniroute-smart-routing-research/spike-report-p1.md` for:
- the context keys that are reachable;
- whether the per-request store is visible in `order` and `observe`;
- the argument shapes recorded for `handleSingleModel` and the usage fetchers;
- the Laya batching decision (batched vs parallel single questions);
- the "Impact on P2–P4" edits for this plan.

The spike report wins over this plan where they disagree.

## Preconditions

- P1 ends with `VERDICT: GO`, and its "Impact on P2–P4" edits for P3 have been applied here.
- The P1 spike report confirms that the per-request store (`als`) is visible in `order` and `observe`. If it is not, stop and report: the fail-closed design depends on it.
- P2 is merged. This plan reads `settings.json` (`defaults`, `plans`), `scorecard.json` (`entries[id].Q`, `entries[id].catalog`) and `task-stats.json`, and writes the latter.

## Global Constraints

- **No production changes.** Never stop, restart, exec writes into or reconfigure the production `omniroute` container, and never edit `~/.config/llm-env/*`.
- **Throwaway containers.** Integration uses `omniroute-smart-it3*` on `127.0.0.1:29131` with the temporary volume `omniroute-smart-it3-data`. Both are removed at the end, pass or fail.
- **Fail closed.** No silent fallback to declared order. Every failure returns `503` with the PRD error body:

  ```json
  {"error":{"type":"smart_routing_unavailable","reason":"…","detail":"…","request_id":"…"}}
  ```

  The reasons are `system_one_unavailable`, `no_candidate_fits`, `strategy_unavailable`, `scorecard_missing`, `config_invalid` and `fusion_failed` (the last one is P4).
- **Remote System One consent.** A remote backend receives the request excerpt only when its consent flag is `true` in the plugin config **and** its key is present. The Zen key is read from the highest-priority active `opencode-zen` connection through the context, and `TYPESAFE_API_KEY` comes from the process env.
- **Honest User-Agent.** Outgoing System One calls send `User-Agent: llm-env-omniroute-smart/<PATCH_VERSION>`. Never spoof a client User-Agent.
- **Secrets.** Never logged and never written to `decisions.jsonl` or `task-stats.json`. Error details carry status codes and reason words, never response bodies or keys.
- **Files.** Every state file is mode 0600 and written atomically. `decisions.jsonl` rotates at `decisionLogMaxMB` × `decisionLogFiles`.
- **Performance.** Strategy overhead, excluding classification, is p95 < 5 ms. No synchronous network or database call on the request path, except System One and the arbiter.
- **Commits and style.** Commit after each task and never push. Follow ponytail rules: reuse `shared/classifier-questions.mjs` and the P2 plugin's `lib/files.mjs` (imported by relative path `../../plugins/smart-catalog/lib/files.mjs`, which resolves the same way in the repo and in the image).

## Review Focus

1. **A combo `config` holding OmniRoute-native keys** (for example `maxComboDepth`) must not trip `config_invalid`. Only known smart keys are validated. Pinned in Task 2.
2. **Laya answering with low confidence while every remote backend is unconsented** must go straight to the arbiter and then to conservative defaults, and must never fail the request. Laya being *unreachable* with no consented remote must fail with `system_one_unavailable`. Pinned in Task 3.
3. **A declared plan with `manualRemainingPct`** must override the live fetcher's windows for that connection. A plan with `renews: false` past `expiresAt` must gate the member out (`plan_expired`). Pinned in Tasks 4 and 6.
4. **A 429 on a target** must mark that connection exhausted until the reset the error names, else the reset of its shortest window with data, else `failureBackoffMaxMinutes`, without raising `F`. A 400 must change nothing. Pinned in Task 5.
5. **A request whose prelude ran, but whose combo loop stopped early** (request-scoped 4xx) must still produce exactly one `decisions.jsonl` line, via the 5-minute sweep. Pinned in Task 7.

---

## File Structure

| Path | Responsibility |
|---|---|
| `pylib/smart_settings.py`, `tests/test_smart_settings.py` | Add `limits` to `settings.json`, so the strategy validates combo config against the same ranges |
| `omniroute-smart/strategies/smart.mjs` | Module entry: `prelude`, `order`, `observe`, and the probe marker |
| `omniroute-smart/strategies/smart/errors.mjs` | `SmartError` and the 503 response builder |
| `omniroute-smart/strategies/smart/config.mjs` | mtime-cached `settings.json` and `scorecard.json`, plugin config TTL, combo-config merge and validation |
| `omniroute-smart/strategies/smart/systemone.mjs` | Backend chain, error table, low-confidence escalation, arbiter, defaults |
| `omniroute-smart/strategies/smart/quota.mjs` | Windows from the quota cache, usage fetchers and declarations; burn EWMA; S and W |
| `omniroute-smart/strategies/smart/failures.mjs` | F EWMA, cooldown, exhausted windows |
| `omniroute-smart/strategies/smart/rank.mjs` | Gates, classes, utility, effort |
| `omniroute-smart/strategies/smart/log.mjs` | `decisions.jsonl` and `task-stats.json` |
| `omniroute-smart/strategies/smart/test/*.test.mjs` | Unit and module tests (fake context, fake fetch) |
| `omniroute-smart/strategies/smart/bench.mjs` | Overhead benchmark |
| `omniroute-smart/spike/spike-order.mjs` | P1 canary, moved out of `strategies/` so production images do not register it |
| `scripts/smart-probe.sh`, `scripts/check-server.sh`, `tests/test_shell.py` | Remote System One probes; System One and decision-count health rows |

**Interfaces this plan produces for P4 and P5:**
- `globalThis.__omniSmart.als.getStore().smart`, set by `prelude`:

  ```js
  { requestId, combo, cfg, answers, stages, ranked: [{id, connId, cls, U, terms, shortestResetAt}], dropped: [{id, reason}],
    lane: "single", fusion_eligible: boolean, effort, startedAt }
  ```

  P4 reads `fusion_eligible` and `ranked`, and sets `lane`.
- `createSmart(deps)` (factory) and the `deps` shape:

  ```js
  { dataDir, now, fetchImpl, ctx, env, flushMs, batched, patchVersion, classify (bench only) }
  ```

  `ctx` is `globalThis.__omniSmart.ctx` at run time and a fake in tests.
- `decide(answers, ranked, cfg, body)` in `smart.mjs`. P3 returns `{lane: "single", fusion_eligible}`, and P4 replaces the lane logic.
- `errorResponse(reason, detail, requestId)`.

---

### Task 1: Settings limits; move the spike canary out of production strategies

**Files:**
- Modify: `pylib/smart_settings.py`, `tests/test_smart_settings.py`
- Move: `omniroute-smart/strategies/spike-order.mjs` → `omniroute-smart/spike/spike-order.mjs`

**Interfaces:**
- Produces: a `settings.json` key `limits`:

  ```json
  {"defaults": {"<key>": [min, max, integer]}, "minHeadroomPct": [0, 100, false], "fusionTuning": {"<key>": [min, max, integer]}}
  ```

- [ ] **Step 1: Write the failing test**

```python
def test_render_includes_the_numeric_limits_the_strategy_validates_with():
    settings = render_smart_settings({"smart_routing": {}})
    assert settings["limits"]["defaults"]["systemOneMinConfidence"] == [0, 1, False]
    assert settings["limits"]["fusionTuning"]["minPanel"] == [1, 2, True]
    assert settings["limits"]["minHeadroomPct"] == [0, 100, False]
```

- [ ] **Step 2: Implement**

In `render_smart_settings`:

```python
    limits = {
        "defaults": {k: [lo, hi, integer] for k, (lo, hi, integer) in _DEFAULT_NUMBERS.items()},
        "minHeadroomPct": [0, 100, False],
        "fusionTuning": {k: [lo, hi, integer] for k, (lo, hi, integer) in _FUSION_NUMBERS.items()},
    }
    return {"version": 1, **settings, "limits": limits}
```

Move the canary and keep its test path working:

```bash
git mv omniroute-smart/strategies/spike-order.mjs omniroute-smart/spike/spike-order.mjs
rg -n "strategies/spike-order" omniroute-smart docs/superpowers/plans/2026-10-13-omniroute-smart-p1-spikes-derived-image.md
```

Update every hit in code (`omniroute-smart/**`) to the new path. Leave the P1 plan text alone; it is history.

P1's `patch-omniroute.mjs` exits 2 when `strategies/` has no `.mjs`. That is correct, because Task 8 adds `smart.mjs` before the image is rebuilt. Do not rebuild the image in this task.

- [ ] **Step 3: Run the tests, then commit**

Run: `uv run pytest tests/test_smart_settings.py -v && bash tools/validate.sh && node --test 'omniroute-smart/**/*.test.mjs'`
Expected: PASS.

```bash
git add pylib/smart_settings.py tests/test_smart_settings.py omniroute-smart
git commit -m "feat(smart-routing): numeric limits in settings.json; move spike canary out of strategies/"
```

---

### Task 2: Errors and config (settings, scorecard, plugin config, combo config)

**Files:**
- Create: `omniroute-smart/strategies/smart/errors.mjs`, `omniroute-smart/strategies/smart/config.mjs`
- Test: `omniroute-smart/strategies/smart/test/config.test.mjs`

**Interfaces:**
- Produces:
  - `class SmartError(reason, detail)`;
  - `errorResponse(reason, detail, requestId) → Response(503)`;
  - `readJsonCached(path) → value|null|undefined` (missing → `null`, unparseable → `undefined`);
  - `loadSettings(dataDir)`, which throws `config_invalid`;
  - `loadScorecard(dataDir, intervalHours, now)`, which throws `scorecard_missing`;
  - `effectiveConfig(settings, comboConfig) → cfg`, which throws `config_invalid` naming the key;
  - `makePluginConfig(ctx, ttlSeconds, manifestSchema, now) → () => Promise<config>`.

- [ ] **Step 1: Write the failing tests**

```js
// omniroute-smart/strategies/smart/test/config.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync, utimesSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { SmartError, errorResponse } from "../errors.mjs";
import { effectiveConfig, loadScorecard, loadSettings, makePluginConfig } from "../config.mjs";

const settings = {
  defaults: { systemOneMinConfidence: 0.7, minHeadroomPct: { small: 1, medium: 3, large: 6 },
    weights: { plan: { wQ: .5, wS: .25, wW: .1, wO: .05, wH: .1 } }, fusionTuning: { panelHardTimeoutMs: 90000, minPanel: 2, stragglerGraceMs: 8000 },
    classifierArbiterModel: "codex/gpt-6-luna-high", judgeModel: null },
  limits: { defaults: { systemOneMinConfidence: [0, 1, false] }, minHeadroomPct: [0, 100, false],
    fusionTuning: { panelHardTimeoutMs: [1000, 600000, true], minPanel: [1, 2, true], stragglerGraceMs: [0, 120000, true] } },
};
const dir = () => mkdtempSync(join(tmpdir(), "smart-cfg-"));

test("503 body has the PRD shape and no stack", async () => {
  const r = errorResponse("no_candidate_fits", "a: quota", "req-1");
  assert.equal(r.status, 503);
  assert.deepEqual(await r.json(), { error: { type: "smart_routing_unavailable", reason: "no_candidate_fits", detail: "a: quota", request_id: "req-1" } });
});

test("missing or invalid settings.json is config_invalid", () => {
  const d = dir();
  assert.throws(() => loadSettings(d), (e) => e instanceof SmartError && e.reason === "config_invalid");
  writeFileSync(join(d, "settings.json"), "{not json");
  assert.throws(() => loadSettings(d), (e) => e.reason === "config_invalid");
  for (const bad of [{ defaults: {}, limits: {} }, { defaults: { weights: [] }, limits: { defaults: {} } }, { ...settings, limits: { ...settings.limits, minHeadroomPct: 3 } }]) {
    const b = dir();
    writeFileSync(join(b, "settings.json"), JSON.stringify(bad));
    assert.throws(() => loadSettings(b), (e) => e.reason === "config_invalid"); // parseable but malformed
  }
});

test("scorecard older than 2 × interval is scorecard_missing", () => {
  const d = dir();
  writeFileSync(join(d, "scorecard.json"), JSON.stringify({ generated_at: "2026-10-13T00:00:00Z", entries: {} }));
  assert.ok(loadScorecard(d, 6, Date.parse("2026-10-13T11:00:00Z")));
  assert.throws(() => loadScorecard(d, 6, Date.parse("2026-10-13T12:00:01Z")), (e) => e.reason === "scorecard_missing");
  assert.throws(() => loadScorecard(dir(), 6, Date.now()), (e) => e.reason === "scorecard_missing");
  const g = dir();
  writeFileSync(join(g, "scorecard.json"), JSON.stringify({ generated_at: "yesterday-ish", entries: {} }));
  assert.throws(() => loadScorecard(g, 6, Date.now()), (e) => e.reason === "scorecard_missing"); // NaN age must not pass
});

test("combo config overrides defaults; weights rows replace; native keys are ignored", () => {
  const cfg = effectiveConfig(settings, { systemOneMinConfidence: 0.8, maxComboDepth: 3, weights: { plan: { wQ: 1, wS: 0, wW: 0, wO: 0, wH: 0 } } });
  assert.equal(cfg.systemOneMinConfidence, 0.8);
  assert.deepEqual(cfg.weights.plan, { wQ: 1, wS: 0, wW: 0, wO: 0, wH: 0 });
});

test("an out-of-range or malformed smart key is config_invalid naming the key", () => {
  assert.throws(() => effectiveConfig(settings, { systemOneMinConfidence: 2 }), (e) => e.reason === "config_invalid" && /systemOneMinConfidence/.test(e.detail));
  assert.throws(() => effectiveConfig(settings, { weights: { plan: { wQ: 0.9, wS: 0, wW: 0, wO: 0, wH: 0 } } }), (e) => /weights\.plan/.test(e.detail));
  assert.throws(() => effectiveConfig(settings, { classifierArbiterModel: "smart-planning" }), (e) => /classifierArbiterModel/.test(e.detail));
  assert.throws(() => effectiveConfig(settings, { fusionTuning: { minPanel: 3 } }), (e) => /fusionTuning\.minPanel/.test(e.detail));
});

test("an unknown or malformed nested smart key is config_invalid naming it", () => {
  assert.throws(() => effectiveConfig(settings, { minHeadroomPct: { huge: 5 } }), (e) => e.reason === "config_invalid" && /minHeadroomPct\.huge/.test(e.detail));
  assert.throws(() => effectiveConfig(settings, { fusionTuning: { minPanell: 2 } }), (e) => /fusionTuning\.minPanell/.test(e.detail));
  assert.throws(() => effectiveConfig(settings, { weights: { plann: { wQ: 1, wS: 0, wW: 0, wO: 0, wH: 0 } } }), (e) => /weights\.plann/.test(e.detail));
  assert.throws(() => effectiveConfig(settings, { fusionTuning: 3 }), (e) => /fusionTuning/.test(e.detail));
  assert.throws(() => effectiveConfig(settings, { weights: { plan: { wQ: 0.5, wS: 0.25, wW: 0.1, wO: 0.05, wH: 0.1, wX: 0 } } }), (e) => /weights\.plan/.test(e.detail));
});

test("plugin config is cached for the TTL and merged over schema defaults", async () => {
  let calls = 0;
  const ctx = { getPluginConfig: async () => { calls++; return { consentZenFree: true }; } };
  let t = 0;
  const get = makePluginConfig(ctx, 60, { consentZenFree: { default: false }, syncIntervalHours: { default: 6 } }, () => t);
  assert.deepEqual(await get(), { consentZenFree: true, syncIntervalHours: 6 });
  t = 59e3; await get(); assert.equal(calls, 1);
  t = 61e3; await get(); assert.equal(calls, 2);
});
```

- [ ] **Step 2: Run and see them fail**

Run: `node --test 'omniroute-smart/strategies/smart/test/*.test.mjs'`
Expected: FAIL with `Cannot find module`.

- [ ] **Step 3: Implement**

```js
// omniroute-smart/strategies/smart/errors.mjs
export class SmartError extends Error {
  constructor(reason, detail) { super(`${reason}: ${detail}`); this.reason = reason; this.detail = String(detail); }
}

export const errorResponse = (reason, detail, requestId) =>
  new Response(JSON.stringify({ error: { type: "smart_routing_unavailable", reason, detail: String(detail).slice(0, 300), request_id: requestId } }),
    { status: 503, headers: { "content-type": "application/json" } });
```

```js
// omniroute-smart/strategies/smart/config.mjs
// settings.json / scorecard.json are re-read only when their mtime changes (no I/O per request otherwise).
import { readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { SmartError } from "./errors.mjs";

const cache = new Map();
const MODEL_ID = /^[^/\s]+\/\S+$/;
const TERMS = ["wQ", "wS", "wW", "wO", "wH"];

export function readJsonCached(path) {
  const st = statSync(path, { throwIfNoEntry: false });
  if (!st) return null;
  const hit = cache.get(path);
  if (hit && hit.mtimeMs === st.mtimeMs) return hit.value;
  let value;
  try { value = JSON.parse(readFileSync(path, "utf8")); } catch { value = undefined; }
  cache.set(path, { mtimeMs: st.mtimeMs, value });
  return value;
}

export function loadSettings(dataDir) {
  const s = readJsonCached(join(dataDir, "settings.json"));
  const obj = (v) => v != null && typeof v === "object" && !Array.isArray(v);
  // the shape effectiveConfig walks; a malformed file must be config_invalid, never a TypeError (strategy_unavailable)
  const ok = obj(s) && obj(s.defaults) && obj(s.limits) && obj(s.limits.defaults) && obj(s.limits.fusionTuning)
    && Array.isArray(s.limits.minHeadroomPct) && ["minHeadroomPct", "fusionTuning", "weights"].every((k) => obj(s.defaults[k]))
    && Object.values(s.limits.defaults).every(Array.isArray) && Object.values(s.limits.fusionTuning).every(Array.isArray);
  if (!ok) throw new SmartError("config_invalid", "settings.json missing or invalid; run make smart-sync");
  return s;
}

export function loadScorecard(dataDir, intervalHours, now = Date.now()) {
  const card = readJsonCached(join(dataDir, "scorecard.json"));
  const generated = Date.parse(card?.generated_at);
  if (!card?.entries || !Number.isFinite(generated)) throw new SmartError("scorecard_missing", "scorecard.json missing, unreadable or without a valid generated_at");
  if (now - generated > 2 * intervalHours * 3600e3) {
    throw new SmartError("scorecard_missing", `scorecard.json older than ${2 * intervalHours} h`);
  }
  return card;
}

const inRange = (v, [lo, hi, integer]) => typeof v === "number" && Number.isFinite(v) && v >= lo && v <= hi && (!integer || Number.isInteger(v));

/** Merge a combo's config over settings.defaults; validate only smart keys (OmniRoute-native keys pass through untouched). */
export function effectiveConfig(settings, comboConfig = {}) {
  const d = settings.defaults;
  const own = Object.fromEntries(Object.entries(comboConfig ?? {}).filter(([k]) => k in d));
  const bad = (key) => { throw new SmartError("config_invalid", `combo config key ${key} is invalid`); };
  for (const k of ["minHeadroomPct", "fusionTuning", "weights"]) { // a typo in a nested key must not be silently ignored
    if (!(k in own)) continue;
    if (!own[k] || typeof own[k] !== "object" || Array.isArray(own[k])) bad(k);
    for (const n of Object.keys(own[k])) if (!(n in d[k])) bad(`${k}.${n}`);
  }
  const cfg = { ...structuredClone(d), ...structuredClone(own) };
  cfg.minHeadroomPct = { ...d.minHeadroomPct, ...(own.minHeadroomPct ?? {}) };
  cfg.fusionTuning = { ...d.fusionTuning, ...(own.fusionTuning ?? {}) };
  cfg.weights = { ...d.weights, ...(own.weights ?? {}) }; // a row replaces the default row
  for (const [k, limit] of Object.entries(settings.limits.defaults)) if (!inRange(cfg[k], limit)) bad(k);
  for (const [k, v] of Object.entries(cfg.minHeadroomPct)) if (!inRange(v, settings.limits.minHeadroomPct)) bad(`minHeadroomPct.${k}`);
  for (const [k, limit] of Object.entries(settings.limits.fusionTuning)) if (!inRange(cfg.fusionTuning[k], limit)) bad(`fusionTuning.${k}`);
  for (const [kind, row] of Object.entries(cfg.weights)) {
    if (!row || typeof row !== "object" || Object.keys(row).length !== TERMS.length || TERMS.some((t) => !inRange(row[t], [0, 1, false]))
      || Math.abs(TERMS.reduce((s, t) => s + row[t], 0) - 1) > 1e-6) bad(`weights.${kind}`); // exactly the five terms
  }
  if (typeof cfg.classifierArbiterModel !== "string" || !MODEL_ID.test(cfg.classifierArbiterModel)) bad("classifierArbiterModel");
  if (cfg.judgeModel != null && (typeof cfg.judgeModel !== "string" || !MODEL_ID.test(cfg.judgeModel))) bad("judgeModel");
  return cfg;
}

export function makePluginConfig(ctx, ttlSeconds, schema, now = Date.now) {
  let value = null, at = -Infinity;
  const defaults = Object.fromEntries(Object.entries(schema).map(([k, f]) => [k, f.default]));
  return async () => {
    if (now() - at < ttlSeconds * 1e3 && value) return value;
    const raw = (await ctx.getPluginConfig?.("smart-catalog")) ?? {};
    value = { ...defaults, ...(raw.config ?? raw) };
    at = now();
    return value;
  };
}
```

`raw.config ?? raw` covers both shapes the context may return: the API shape `{config}` and the bare object. Record in a comment which shape the P1 spike report found.

- [ ] **Step 4: Run the tests, then commit**

Run: `node --test 'omniroute-smart/strategies/smart/test/*.test.mjs' && bash tools/validate.sh`
Expected: PASS.

```bash
git add omniroute-smart/strategies/smart
git commit -m "feat(smart): error responses, mtime-cached settings/scorecard, combo-config validation"
```

---

### Task 3: System One client (chain, error table, escalation, arbiter, defaults)

**Files:**
- Create: `omniroute-smart/strategies/smart/systemone.mjs`
- Test: `omniroute-smart/strategies/smart/test/systemone.test.mjs`

**Interfaces:**
- Consumes: `QUESTIONS`, `buildState`, `deriveConfidence` (`omniroute-smart/shared/classifier-questions.mjs`).
- Produces:
  - `CONSERVATIVE`;
  - `backendsFor(pluginConfig, keys) → Backend[]` (Laya first, then consented remotes that have a key);
  - `classify({body, cfg, backends, arbiter, fetchImpl, now, batched}) → {answers: {q: {value, confidence, stage}}, backend}`;
  - `parseArbiter(text, questions) → {q: {value, confidence}}`.

  `Backend = {id, url, model, headers}`. `arbiter(questionsSubset, stateText) → Promise<{q: {value, confidence}}>` is injected and wraps `handleSingleModel` (Task 8).

- [ ] **Step 1: Write the failing tests**

```js
// omniroute-smart/strategies/smart/test/systemone.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { CONSERVATIVE, backendsFor, classify, parseArbiter } from "../systemone.mjs";

const cfg = { systemOneMinConfidence: 0.7, systemOneLayaTimeoutMs: 1500, systemOneRemoteTimeoutMs: 3000, systemOneChainBudgetMs: 8000,
  systemOneRetries: 2, systemOneRetryAfterMaxMs: 2000, systemOneStateMaxChars: 4000, classifierArbiterTimeoutMs: 5000 };
const body = { messages: [{ role: "user", content: "Plan the OAuth2 migration" }] };
const sure = { task_kind: { type: "choice", choice: "plan", answer_confidence: 0.95 },
  domain: { type: "choice", choice: "backend", answer_confidence: 0.9 },
  scope: { type: "score", probabilities: { 0: 0.05, 1: 0.1, 2: 0.85 } },
  complexity: { type: "score", probabilities: { 0: 0.05, 1: 0.15, 2: 0.8 } },
  second_opinion: { type: "noul", noul: 0.95 } };
const ok = (answers) => new Response(JSON.stringify({ answers }));
const laya = { id: "laya", url: "http://system-one:8000/v1/systemone", model: "jev-1", headers: {} };
const zen = { id: "zen-free", url: "https://opencode.ai/zen/v1/systemone", model: "jev-1.13-free", headers: { authorization: "Bearer z" } };

test("consent and keys decide the chain", () => {
  const ids = (c, k) => backendsFor(c, k).map((b) => b.id);
  assert.deepEqual(ids({ systemOneLayaUrl: "http://system-one:8000" }, { zen: "z" }), ["laya"]);
  assert.deepEqual(ids({ systemOneLayaUrl: "http://s:8000", consentZenFree: true, consentTypeSafe: true }, { zen: "z" }), ["laya", "zen-free"]);
  assert.deepEqual(ids({ systemOneLayaUrl: "http://s:8000", consentZenFree: true, consentZenPaid: true, consentTypeSafe: true }, { zen: "z", typesafe: "t" }),
    ["laya", "zen-free", "typesafe", "zen-paid"]);
});

test("confident Laya answers settle everything in one batched request", async () => {
  const seen = [];
  const r = await classify({ body, cfg, backends: [laya], fetchImpl: async (u, i) => { seen.push(JSON.parse(i.body)); return ok(sure); } });
  assert.equal(seen.length, 1);
  assert.equal(Object.keys(seen[0].questions).length, 5);
  assert.deepEqual(Object.fromEntries(Object.entries(r.answers).map(([k, a]) => [k, [a.value, a.stage]])),
    { task_kind: ["plan", "laya"], domain: ["backend", "laya"], scope: ["large", "laya"], complexity: ["high", "laya"], second_opinion: [0.95, "laya"] });
});

test("unbatched mode sends five parallel single-question requests", async () => {
  const seen = [];
  await classify({ body, cfg, backends: [laya], batched: false,
    fetchImpl: async (u, i) => { const q = JSON.parse(i.body).questions; seen.push(Object.keys(q)); const k = Object.keys(q)[0]; return ok({ [k]: sure[k] }); } });
  assert.equal(seen.length, 5);
  assert.ok(seen.every((qs) => qs.length === 1));
});

test("Laya down falls through to a consented remote", async () => {
  const r = await classify({ body, cfg, backends: [laya, zen],
    fetchImpl: async (u) => { if (u.startsWith("http://system-one")) throw new TypeError("fetch failed"); return ok(sure); } });
  assert.equal(r.backend, "zen-free");
  assert.equal(r.answers.task_kind.stage, "jev:zen-free");
});

test("no reachable backend fails closed", async () => {
  await assert.rejects(classify({ body, cfg, backends: [laya], fetchImpl: async () => { throw new TypeError("fetch failed"); } }),
    (e) => e.reason === "system_one_unavailable");
});

test("503 with a short Retry-After retries; a long one goes to the next backend", async () => {
  let n = 0;
  const r = await classify({ body, cfg, backends: [laya], fetchImpl: async () => (++n === 1 ? new Response("", { status: 503, headers: { "retry-after": "0" } }) : ok(sure)) });
  assert.equal(n, 2); assert.equal(r.backend, "laya");
  const hits = [];
  await classify({ body, cfg, backends: [laya, zen], fetchImpl: async (u) => { hits.push(u); return u.startsWith("http://system-one") ? new Response("", { status: 503, headers: { "retry-after": "60" } }) : ok(sure); } });
  assert.equal(hits.filter((u) => u.startsWith("http://system-one")).length, 1);
});

test("a Retry-After that does not fit the remaining chain budget is not waited for", async () => {
  let n = 0;
  await assert.rejects(classify({ body, cfg: { ...cfg, systemOneChainBudgetMs: 500, systemOneRetryAfterMaxMs: 5000, systemOneRetries: 3 }, backends: [laya],
    fetchImpl: async () => { n++; return new Response("", { status: 503, headers: { "retry-after": "1" } }); } }),
  (e) => e.reason === "system_one_unavailable");
  assert.equal(n, 1); // 1 s > 500 ms budget: no wait, no second attempt
});

test("the request state sent to System One carries has_tools", async () => {
  let sent;
  await classify({ body: { ...body, tools: [{ type: "function", function: { name: "x" } }] }, cfg, backends: [laya],
    fetchImpl: async (_u, i) => { sent = JSON.parse(i.body); return ok(sure); } });
  assert.equal(sent.state.has_tools, true);
});

test("400/413/422 is a caller bug: strategy_unavailable, no next backend", async () => {
  const hits = [];
  await assert.rejects(classify({ body, cfg, backends: [laya, zen], fetchImpl: async (u) => { hits.push(u); return new Response("", { status: 422 }); } }),
    (e) => e.reason === "strategy_unavailable");
  assert.equal(hits.length, 1);
});

test("low confidence escalates only the unsure questions: remote, then arbiter, then default", async () => {
  const unsure = { ...sure, domain: { type: "choice", choice: "frontend", answer_confidence: 0.4 }, second_opinion: { type: "noul", noul: 0.55 } };
  const remoteAsked = [];
  const r = await classify({ body, cfg, backends: [laya, zen],
    fetchImpl: async (u, i) => {
      if (u.startsWith("http://system-one")) return ok(unsure);
      remoteAsked.push(Object.keys(JSON.parse(i.body).questions));
      return ok({ domain: { type: "choice", choice: "backend", confidence: 0.9 }, second_opinion: { type: "noul", noul: 0.6 } });
    },
    arbiter: async (qs) => { assert.deepEqual(Object.keys(qs), ["second_opinion"]); return { second_opinion: { value: 0.5, confidence: 0.3 } }; } });
  assert.deepEqual(remoteAsked, [["domain", "second_opinion"]]);
  assert.deepEqual([r.answers.domain.value, r.answers.domain.stage], ["backend", "jev:zen-free"]);
  assert.deepEqual([r.answers.second_opinion.value, r.answers.second_opinion.stage], [CONSERVATIVE.second_opinion, "default"]);
});

test("unconsented remotes plus an arbiter failure still answer with conservative defaults", async () => {
  const unsure = { ...sure, task_kind: { type: "choice", choice: "plan", answer_confidence: 0.2 } };
  const r = await classify({ body, cfg, backends: [laya], fetchImpl: async () => ok(unsure), arbiter: async () => { throw new Error("arbiter 500"); } });
  assert.deepEqual([r.answers.task_kind.value, r.answers.task_kind.stage], ["review_audit", "default"]);
});

test("arbiter JSON is parsed from fenced output and validated against allowed values", () => {
  const qs = { task_kind: { type: "choice", criteria: { plan: "", review_audit: "", execute_edit: "", mechanical: "" } }, second_opinion: { type: "noul" } };
  assert.deepEqual(parseArbiter('```json\n{"task_kind":{"value":"plan","confidence":0.9},"second_opinion":{"value":0.8,"confidence":0.75}}\n```', qs),
    { task_kind: { value: "plan", confidence: 0.9 }, second_opinion: { value: 0.8, confidence: 0.75 } });
  assert.deepEqual(parseArbiter('{"task_kind":{"value":"cook","confidence":0.9}}', qs), {});
});
```

- [ ] **Step 2: Run and see them fail**

Run: `node --test 'omniroute-smart/strategies/smart/test/systemone.test.mjs'`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement**

```js
// omniroute-smart/strategies/smart/systemone.mjs
// PRD "System One classification": type-1 classifier chain, then a type-2 arbiter, then conservative values.
import { QUESTIONS, buildState, deriveConfidence } from "../../shared/classifier-questions.mjs";
import { SmartError } from "./errors.mjs";

export const CONSERVATIVE = { task_kind: "review_audit", domain: "fullstack", scope: "medium", complexity: "medium", second_opinion: 0 };
const REMOTES = [
  { id: "zen-free", consent: "consentZenFree", url: "https://opencode.ai/zen/v1/systemone", model: "jev-1.13-free", key: "zen" },
  { id: "typesafe", consent: "consentTypeSafe", url: "https://api.typesafe.ai/v1/systemone", model: "jev-latest", key: "typesafe" },
  { id: "zen-paid", consent: "consentZenPaid", url: "https://opencode.ai/zen/v1/systemone", model: "jev-1.13", key: "zen" },
];

/** Remotes left out of backendsFor, with why: the probe reports them as SKIP and never sends them a request. */
export function skippedRemotes(pluginConfig, keys) {
  return REMOTES.filter((r) => !(pluginConfig[r.consent] === true && keys[r.key]))
    .map((r) => ({ name: r.id, skip: pluginConfig[r.consent] === true ? "no key" : "not consented" }));
}

export function backendsFor(pluginConfig, keys, userAgent = "llm-env-omniroute-smart") {
  const laya = { id: "laya", url: `${pluginConfig.systemOneLayaUrl.replace(/\/$/, "")}/v1/systemone`, model: "jev-1", headers: { "user-agent": userAgent } };
  const remotes = REMOTES.filter((r) => pluginConfig[r.consent] === true && keys[r.key])
    .map((r) => ({ id: r.id, url: r.url, model: r.model, headers: { "user-agent": userAgent, authorization: `Bearer ${keys[r.key]}` } }));
  return [laya, ...remotes];
}

/** Map one raw answer to its value: choice → label, score → criteria[argmax], noul → P(yes). */
function valueOf(name, raw) {
  const q = QUESTIONS[name];
  if (q.type === "choice") return Object.hasOwn(q.criteria, raw.choice) ? raw.choice : undefined; // unknown label → low, escalates
  if (q.type === "noul") return raw.noul;
  const best = Object.entries(raw.probabilities ?? {}).sort((a, b) => b[1] - a[1])[0]?.[0];
  return best == null ? undefined : q.criteria[Number(best)];
}

const confidenceOf = (name, raw) => deriveConfidence({ type: QUESTIONS[name].type, ...raw });

class Unreachable extends Error {}

async function call(backend, questions, stateText, hasTools, timeoutMs, cfg, fetchImpl, deadline, now) {
  for (let attempt = 0; ; attempt++) {
    const left = deadline - now(); // every attempt and every Retry-After wait fits inside the chain budget
    if (left <= 0) throw new Unreachable(`${backend.id}: timeout`);
    let r;
    try {
      r = await fetchImpl(backend.url, { method: "POST", signal: AbortSignal.timeout(Math.min(timeoutMs, left)),
        headers: { "content-type": "application/json", ...backend.headers },
        body: JSON.stringify({ model: backend.model, state: { request: stateText, has_tools: hasTools }, questions }) });
    } catch (e) { throw new Unreachable(`${backend.id}: ${e.name === "TimeoutError" ? "timeout" : "unreachable"}`); }
    if (r.ok) return (await r.json()).answers ?? {};
    if ([400, 413, 422].includes(r.status)) throw new SmartError("strategy_unavailable", `${backend.id} rejected the classification request (HTTP ${r.status})`);
    const raHeader = r.headers.get("retry-after");
    const ra = raHeader == null ? NaN : Number(raHeader); // a bare 503 (no Retry-After) goes to the next backend, no retry
    if (r.status === 503 && Number.isFinite(ra) && ra * 1000 <= cfg.systemOneRetryAfterMaxMs && ra * 1000 < deadline - now() && attempt < cfg.systemOneRetries) {
      await new Promise((res) => setTimeout(res, ra * 1000));
      continue;
    }
    throw new Unreachable(`${backend.id}: HTTP ${r.status}`);
  }
}

async function ask(backend, names, state, cfg, fetchImpl, batched, timeoutMs, deadline, now) {
  const pick = (ns) => Object.fromEntries(ns.map((n) => [n, QUESTIONS[n]]));
  if (batched) return call(backend, pick(names), state.text, state.has_tools, timeoutMs, cfg, fetchImpl, deadline, now);
  const parts = await Promise.all(names.map((n) => call(backend, pick([n]), state.text, state.has_tools, timeoutMs, cfg, fetchImpl, deadline, now)));
  return Object.assign({}, ...parts);
}

export function parseArbiter(text, questions) {
  let obj;
  try { obj = JSON.parse(String(text).replace(/^\s*```(?:json)?\s*|\s*```\s*$/g, "")); } catch { return {}; }
  const out = {};
  for (const [name, q] of Object.entries(questions)) {
    const a = obj?.[name];
    if (!a || typeof a.confidence !== "number") continue;
    const allowed = q.type === "choice" ? Object.keys(q.criteria) : q.type === "score" ? q.criteria : null;
    const ok = allowed ? allowed.includes(a.value) : typeof a.value === "number" && a.value >= 0 && a.value <= 1;
    if (ok) out[name] = { value: a.value, confidence: a.confidence };
  }
  return out;
}

export async function classify({ body, cfg, backends, arbiter, fetchImpl = fetch, now = Date.now, batched = true }) {
  const state = buildState(body, cfg.systemOneStateMaxChars);
  const deadline = now() + cfg.systemOneChainBudgetMs;
  const names = Object.keys(QUESTIONS);
  const timeoutFor = (b) => Math.max(1, Math.min(b.id === "laya" ? cfg.systemOneLayaTimeoutMs : cfg.systemOneRemoteTimeoutMs, deadline - now()));
  const answers = {};
  let first = -1, lastError = null; // lastError: e.g. "zen-free: HTTP 401" (status words only, never bodies)
  for (let i = 0; i < backends.length && now() < deadline; i++) {
    try {
      const raw = await ask(backends[i], names, state, cfg, fetchImpl, batched, timeoutFor(backends[i]), deadline, now);
      for (const n of names) if (raw[n]) answers[n] = { value: valueOf(n, raw[n]), confidence: confidenceOf(n, raw[n]), stage: i === 0 ? "laya" : `jev:${backends[i].id}` };
      first = i;
      break;
    } catch (e) { if (e instanceof SmartError) throw e; lastError = e.message; }
  }
  if (first < 0) throw new SmartError("system_one_unavailable", `no System One backend answered within the chain budget${lastError ? ` (last: ${lastError})` : ""}`);
  const low = () => names.filter((n) => !answers[n] || answers[n].value === undefined || answers[n].confidence < cfg.systemOneMinConfidence);
  for (let i = first + 1; i < backends.length && low().length && now() < deadline; i++) {
    try {
      const raw = await ask(backends[i], low(), state, cfg, fetchImpl, batched, timeoutFor(backends[i]), deadline, now);
      for (const n of low()) {
        if (!raw[n]) continue;
        const c = confidenceOf(n, raw[n]);
        if (c >= cfg.systemOneMinConfidence) answers[n] = { value: valueOf(n, raw[n]), confidence: c, stage: `jev:${backends[i].id}` };
      }
    } catch { /* escalation errors skip to the next backend: an answer already exists */ }
  }
  if (low().length && arbiter) {
    const subset = Object.fromEntries(low().map((n) => [n, QUESTIONS[n]]));
    try {
      const got = await Promise.race([arbiter(subset, state.text),
        new Promise((_, rej) => setTimeout(() => rej(new Error("arbiter timeout")), cfg.classifierArbiterTimeoutMs).unref?.())]);
      for (const [n, a] of Object.entries(got ?? {})) if (a.confidence >= cfg.systemOneMinConfidence) answers[n] = { ...a, stage: "arbiter" };
    } catch { /* fall through to conservative values */ }
  }
  for (const n of low()) answers[n] = { value: CONSERVATIVE[n], confidence: answers[n]?.confidence ?? 0, stage: "default" };
  return { answers, backend: backends[first].id };
}
```

`.unref?.()` is called on the timer *inside* the promise executor. If the arbiter wins the race, the stale timer must not hold the event loop. If a test is flaky because of the timer, store the handle and `clearTimeout` it in a `finally` instead.

- [ ] **Step 4: Run the tests, then commit**

Run: `node --test 'omniroute-smart/strategies/smart/test/*.test.mjs' && bash tools/validate.sh`
Expected: PASS.

```bash
git add omniroute-smart/strategies/smart
git commit -m "feat(smart): System One chain with error table, low-confidence escalation, arbiter and defaults"
```

---

### Task 4: Quota windows and terms (S, W)

**Files:**
- Create: `omniroute-smart/strategies/smart/quota.mjs`
- Test: `omniroute-smart/strategies/smart/test/quota.test.mjs`

**Interfaces:**
- Produces:
  - `windowsFromCacheEntry(entry) → Window[]` (the in-process quota cache);
  - `windowsFromQuotaInfo(info) → Window[]` (the quota fetcher);
  - `windowsFromUsage(usage) → Window[]`;
  - `declaredWindow(plan, now, horizonH) → Window`;
  - `planFor(plans, provider, connectionId) → plan|null`;
  - `makeBurn(alpha) → {observe(key, window, now), rate(key, window)}`;
  - `terms(windows, opts) → {S, W, minH, perWindow, unlimited}`.

  `Window = {name, remainingPct: number|null, resetAt: string|null, lengthH}`.

  Upstream sources:
  - quota cache `QuotaCacheEntry {quotas: {<name>: {remainingPercentage, resetAt, windowSeconds, fractionReported}}, exhausted, nextResetAt}` (upstream `src/domain/quotaCacheState.ts` L19-43, research `strategies-fusion.md` "quota-weighted"). Before coding, read `fractionReported` in that file: the parser below assumes it means `remainingPercentage` is a 0..1 fraction. If it means something else, fix `windowsFromCacheEntry` and its test to match;
  - fetcher `QuotaInfo.window5h | window7d | windowWeekly | windowMonthly | windows{}`, where `percentUsed` is a fraction 0..1 and `unlimited` is a boolean (upstream `open-sse/services/quotaPreflight.ts`);
  - usage `{quotas: {<name>: {remainingPercentage, resetAt}}}` (upstream `open-sse/services/usage/cursor.ts`, `claudeQuota.ts`).

- [ ] **Step 1: Write the failing tests**

These are the PRD worked examples, with the numbers from `data-sources-quota.md` §5.

```js
// omniroute-smart/strategies/smart/test/quota.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { declaredWindow, planFor, terms, windowsFromCacheEntry, windowsFromQuotaInfo, windowsFromUsage, makeBurn } from "../quota.mjs";

const now = Date.parse("2026-10-13T00:00:00Z");
const at = (h) => new Date(now + h * 3600e3).toISOString();
const base = { now, reserve: 3, minResetH: 0.25, burnPriorUtil: 0.8, horizonH: 720, neutralScarcity: 0.5 };
const near = (a, b, eps = 0.01) => assert.ok(Math.abs(a - b) < eps, `${a} ≉ ${b}`);
const w = (name, remainingPct, h, lengthH) => ({ name, remainingPct, resetAt: at(h), lengthH });

test("owner's case: A (renews) S≈0.54, A (expires in 24 h) S≈0.34, B S=1", () => {
  const A = [w("5h", 100, 5, 5), w("weekly", 70, 72, 168), w("monthly", 60, 24, 730)];
  const burnA = { "5h": 6, weekly: 0.5, monthly: 0.8 };
  near(terms(A, { ...base, burnFor: (x) => burnA[x.name] }).S, 0.54);
  near(terms(A, { ...base, burnFor: (x) => burnA[x.name], renews: false, expiresAt: at(24) }).S, 0.34);
  const B = [w("5h", 80, 0.08, 5), w("weekly", 40, 96, 168)];
  assert.equal(terms(B, { ...base, burnFor: (x) => ({ "5h": 6, weekly: 0.6 })[x.name] }).S, 1);
});

test("longest-cycle counter-case: C S≈0.04, D S=1", () => {
  near(terms([w("weekly", 60, 2, 168)], { ...base, burnFor: () => 1.0 }).S, 0.035);
  assert.equal(terms([w("monthly", 50, 360, 730)], { ...base, burnFor: () => 0.2 }).S, 1);
});

test("W uses only the longest window with data", () => {
  const A = [w("5h", 100, 5, 5), w("monthly", 60, 24, 730)];
  const t = terms(A, { ...base, burnFor: (x) => ({ "5h": 6, monthly: 0.8 })[x.name] });
  near(t.W, (1 - t.S) * 37.8 / 24);
});

test("no burn history uses the prior burnPriorUtil·(100−reserve)/length", () => {
  const t = terms([w("weekly", 50, 84, 168)], base);
  near(t.perWindow[0].b, 0.8 * 97 / 168, 1e-9);
});

test("unknown remaining is neutral and not a gate input", () => {
  const t = terms([{ name: "declared", remainingPct: null, resetAt: at(100), lengthH: 730 }], base);
  assert.deepEqual([t.S, t.W, t.minH], [0.5, 0, Infinity]);
});

test("cycle none: forfeit 0 and T = horizon", () => {
  const t = terms([{ name: "declared", remainingPct: 40, resetAt: null, lengthH: 720 }], { ...base, burnFor: () => 0.01 });
  assert.equal(t.perWindow[0].forfeit, 0);
  assert.equal(t.perWindow[0].T, 720);
});

test("QuotaInfo canonical windows map to 5h/168h/730h and percentUsed is a fraction", () => {
  const ws = windowsFromQuotaInfo({ percentUsed: 0.5, window5h: { percentUsed: 0.2, resetAt: at(3) }, windowWeekly: { percentUsed: 0.5, resetAt: at(50) } });
  assert.deepEqual(ws.map((x) => [x.lengthH, x.remainingPct]), [[5, 80], [168, 50]]);
  assert.deepEqual(windowsFromQuotaInfo({ unlimited: true, percentUsed: 0 }), []);
});

test("quota cache entry: remainingPercentage per named window, windowSeconds sets the length, fractions scale to %", () => {
  const ws = windowsFromCacheEntry({ fetchedAt: at(0), exhausted: false, quotas: {
    session: { remainingPercentage: 60, resetAt: at(2), windowSeconds: 5 * 3600 },
    weekly: { remainingPercentage: 0.25, resetAt: at(90), windowSeconds: 7 * 86400, fractionReported: true },
    monthly: { remainingPercentage: 80, resetAt: at(300) } } }); // no windowSeconds: length from the name
  assert.deepEqual(ws.map((x) => [x.lengthH, x.remainingPct]), [[5, 60], [168, 25], [730, 80]]);
  assert.deepEqual(windowsFromCacheEntry({ quotas: {} }), []);
  assert.deepEqual(windowsFromCacheEntry(null), []);
});

test("usage quotas: session/weekly/billing names, duplicates keep the lowest remaining", () => {
  const ws = windowsFromUsage({ quotas: { Total: { remainingPercentage: 70, resetAt: at(200) }, API: { remainingPercentage: 40, resetAt: at(200) },
    "session (5h)": { remainingPercentage: 90, resetAt: at(2) } } });
  assert.deepEqual(ws.map((x) => [x.lengthH, x.remainingPct]).sort((a, b) => a[0] - b[0]), [[5, 90], [730, 40]]);
});

test("declared monthly@1 resets at the next 1st 00:00 UTC; weekly@mon next Monday", () => {
  assert.equal(declaredWindow({ cycle: "monthly@1", manualRemainingPct: 30 }, now, 720).resetAt, "2026-11-01T00:00:00.000Z");
  assert.equal(declaredWindow({ cycle: "weekly@mon" }, now, 720).resetAt, "2026-10-19T00:00:00.000Z");
  assert.equal(declaredWindow({ cycle: "none" }, now, 720).resetAt, null);
});

test("planFor prefers an exact connection over *", () => {
  const plans = [{ provider: "cursor", connection: "*", cycle: "none" }, { provider: "cursor", connection: "c2", cycle: "monthly@5" }];
  assert.equal(planFor(plans, "cursor", "c2").cycle, "monthly@5");
  assert.equal(planFor(plans, "cursor", "c9").cycle, "none");
  assert.equal(planFor(plans, "codex", "x"), null);
});

test("burn EWMA from successive snapshots ignores resets", () => {
  const burn = makeBurn(0.5);
  burn.observe("k", { name: "weekly", remainingPct: 60 }, now);
  burn.observe("k", { name: "weekly", remainingPct: 58 }, now + 2 * 3600e3);   // 1 %/h
  burn.observe("k", { name: "weekly", remainingPct: 95 }, now + 3 * 3600e3);   // reset: skipped
  burn.observe("k", { name: "weekly", remainingPct: 91 }, now + 5 * 3600e3);   // 2 %/h
  near(burn.rate("k", { name: "weekly" }), 1.5, 1e-9);
});
```

The 2026-10-13 test date is a Tuesday; the next Monday is 2026-10-19.

- [ ] **Step 2: Run and see them fail**

Run: `node --test 'omniroute-smart/strategies/smart/test/quota.test.mjs'`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement**

```js
// omniroute-smart/strategies/smart/quota.mjs
// PRD "Request-time ranking": h, T, b, scarcity, forfeit per window; S = max scarcity; W from the longest window.
const H = 3600e3;
const DOW = { sun: 0, mon: 1, tue: 2, wed: 3, thu: 4, fri: 5, sat: 6 };

const lengthOf = (name) => (/session|5h|five/i.test(name) ? 5 : /week|7d|seven/i.test(name) ? 168 : 730);

function dedupe(windows) {
  const byLen = new Map();
  for (const w of windows) {
    const cur = byLen.get(w.lengthH);
    if (!cur || w.remainingPct < cur.remainingPct) byLen.set(w.lengthH, w);
  }
  return [...byLen.values()].sort((a, b) => a.lengthH - b.lengthH);
}

export function windowsFromQuotaInfo(info) {
  if (!info || info.unlimited) return [];
  const out = [];
  const add = (name, lengthH, wi) => wi && Number.isFinite(wi.percentUsed) &&
    out.push({ name, remainingPct: Math.max(0, (1 - wi.percentUsed) * 100), resetAt: wi.resetAt ?? null, lengthH });
  add("5h", 5, info.window5h);
  add("weekly", 168, info.windowWeekly ?? info.window7d);
  add("monthly", 730, info.windowMonthly);
  for (const [name, wi] of Object.entries(info.windows ?? {})) add(name, lengthOf(name), wi);
  if (!out.length && Number.isFinite(info.percentUsed)) add("single", 730, info);
  return dedupe(out);
}

export function windowsFromCacheEntry(entry) {
  return dedupe(Object.entries(entry?.quotas ?? {})
    .filter(([, q]) => Number.isFinite(q?.remainingPercentage))
    .map(([name, q]) => ({ name, remainingPct: q.fractionReported ? q.remainingPercentage * 100 : q.remainingPercentage, resetAt: q.resetAt ?? null,
      lengthH: Number.isFinite(q.windowSeconds) && q.windowSeconds > 0 ? q.windowSeconds / 3600 : lengthOf(name) })));
}

export function windowsFromUsage(usage) {
  return dedupe(Object.entries(usage?.quotas ?? {})
    .filter(([, q]) => Number.isFinite(q?.remainingPercentage))
    .map(([name, q]) => ({ name, remainingPct: q.remainingPercentage, resetAt: q.resetAt ?? null, lengthH: lengthOf(name) })));
}

export function declaredWindow(plan, now, horizonH) {
  const [kind, arg] = plan.cycle.split("@");
  const d = new Date(now);
  let reset = null, lengthH = horizonH;
  if (kind === "monthly") {
    const day = Number(arg);
    const mk = (y, m) => new Date(Date.UTC(y, m, Math.min(day, new Date(Date.UTC(y, m + 1, 0)).getUTCDate())));
    reset = mk(d.getUTCFullYear(), d.getUTCMonth());
    if (reset.getTime() <= now) reset = mk(d.getUTCFullYear(), d.getUTCMonth() + 1);
    lengthH = 730;
  } else if (kind === "weekly") {
    reset = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate() + ((DOW[arg] - d.getUTCDay() + 7) % 7 || 7)));
    lengthH = 168;
  }
  return { name: "declared", remainingPct: plan.manualRemainingPct ?? null, resetAt: reset ? reset.toISOString() : null, lengthH };
}

export function planFor(plans, provider, connectionId) {
  const mine = (plans ?? []).filter((p) => p.provider === provider);
  return mine.find((p) => p.connection === connectionId) ?? mine.find((p) => (p.connection ?? "*") === "*") ?? null;
}

export function makeBurn(alpha) {
  const last = new Map(), rate = new Map();
  return {
    observe(key, w, t) {
      const k = `${key}|${w.name}`;
      const prev = last.get(k);
      last.set(k, { pct: w.remainingPct, t });
      if (!prev || t <= prev.t || w.remainingPct > prev.pct) return; // first sample, same snapshot, or a reset
      const r = (prev.pct - w.remainingPct) / ((t - prev.t) / H);
      rate.set(k, rate.has(k) ? alpha * r + (1 - alpha) * rate.get(k) : r);
    },
    rate: (key, w) => rate.get(`${key}|${w.name}`),
  };
}

export function terms(windows, { now, reserve, minResetH, burnPriorUtil, horizonH, neutralScarcity, renews = true, expiresAt = null, burnFor = () => undefined }) {
  const perWindow = [];
  for (const w of windows) {
    if (w.remainingPct == null) { perWindow.push({ name: w.name, neutral: true, scarcity: neutralScarcity, forfeit: 0 }); continue; }
    const h = w.remainingPct - reserve;
    let T = w.resetAt ? Math.max(minResetH, (Date.parse(w.resetAt) - now) / H) : horizonH;
    if (!renews && expiresAt) T = Math.min(T, Math.max(minResetH, (Date.parse(expiresAt) - now) / H));
    const b = burnFor(w) ?? (burnPriorUtil * (100 - reserve)) / w.lengthH;
    const scarcity = h <= 0 ? 1 : Math.min(1, (b * T) / h);
    const forfeit = !w.resetAt && !expiresAt ? 0 : Math.max(0, h - b * T); // a never-resetting pool forfeits nothing
    perWindow.push({ name: w.name, lengthH: w.lengthH, resetAt: w.resetAt ?? null, h, T, b, scarcity, forfeit });
  }
  const known = perWindow.filter((p) => !p.neutral);
  const S = perWindow.length ? Math.max(...perWindow.map((p) => p.scarcity)) : 0;
  const L = [...known].sort((a, b) => b.lengthH - a.lengthH)[0];
  return {
    S, W: L ? ((1 - S) * L.forfeit) / L.T : 0,
    minH: known.length ? Math.min(...known.map((p) => p.h)) : Infinity,
    perWindow, unlimited: windows.length === 0,
  };
}
```

`terms([])` returns `S = 0`; the caller (Task 8, `candidates`) passes `[]` only for an unlimited connection, and gives a single null window for "no data" so it gets `neutralScarcity`. The PRD says no data means neutral; unlimited means full headroom.

- [ ] **Step 4: Run the tests, then commit**

Run: `node --test 'omniroute-smart/strategies/smart/test/*.test.mjs' && bash tools/validate.sh`
Expected: PASS. If a worked-example number disagrees, recompute it by hand from the PRD formulas before touching the code or the test.

```bash
git add omniroute-smart/strategies/smart
git commit -m "feat(smart): quota windows, burn EWMA, binding-window scarcity and forfeiture"
```

---

### Task 5: Failures (F EWMA, cooldown, exhausted windows)

**Files:**
- Create: `omniroute-smart/strategies/smart/failures.mjs`
- Test: `omniroute-smart/strategies/smart/test/failures.test.mjs`

**Interfaces:**
- Produces: `makeFailures({alpha, baseSeconds, maxMinutes}) → {record(key, outcome, now), F(key), cooldownUntil(key), exhaustedUntil(connKey)}`.
  - `outcome = {status, retryAfterMs?, quotaResetAt?}`, where `status` is 0 for a timeout or network error.
  - `key` is `"provider|model|connection"` and `connKey` is `"provider|connection"`.

- [ ] **Step 1: Write the failing tests**

```js
// omniroute-smart/strategies/smart/test/failures.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { makeFailures } from "../failures.mjs";

const mk = () => makeFailures({ alpha: 0.3, baseSeconds: 30, maxMinutes: 30 });
const k = "opencode-go|glm-5.3|c1";

test("5xx and timeouts raise F and start an exponential cooldown", () => {
  const f = mk();
  f.record(k, { status: 502 }, 0);
  assert.ok(Math.abs(f.F(k) - 0.3) < 1e-9);
  assert.equal(f.cooldownUntil(k), 30e3);
  f.record(k, { status: 0 }, 1000);
  assert.equal(f.cooldownUntil(k), 1000 + 60e3);
});

test("cooldown is capped and honours a longer Retry-After", () => {
  const f = mk();
  for (let i = 0; i < 12; i++) f.record(k, { status: 503 }, 0);
  assert.equal(f.cooldownUntil(k), 30 * 60e3);
  f.record(k, { status: 503, retryAfterMs: 45 * 60e3 }, 0);
  assert.equal(f.cooldownUntil(k), 45 * 60e3);
});

test("success decays F and resets the streak", () => {
  const f = mk();
  f.record(k, { status: 500 }, 0);
  f.record(k, { status: 200 }, 1);
  assert.ok(Math.abs(f.F(k) - 0.21) < 1e-9);
  f.record(k, { status: 500 }, 2);
  assert.equal(f.cooldownUntil(k), 2 + 30e3);
});

test("429 marks the connection exhausted until reset or the max backoff, without touching F", () => {
  const f = mk();
  f.record(k, { status: 429, quotaResetAt: "1970-01-01T01:00:00Z" }, 0);
  assert.equal(f.F(k), 0);
  assert.equal(f.exhaustedUntil("opencode-go|c1"), 3600e3);
  f.record("a|m|c2", { status: 429 }, 0);
  assert.equal(f.exhaustedUntil("a|c2"), 30 * 60e3);
  f.record("a|m|c3", { status: 429, fallbackResetAt: 7200e3 }, 0); // no named window → shortest window with data
  assert.equal(f.exhaustedUntil("a|c3"), 7200e3);
  f.record("a|m|c4", { status: 429, fallbackResetAt: null }, 0); // only a no-reset pool (cycle: "none") → max backoff, not ~30 days
  assert.equal(f.exhaustedUntil("a|c4"), 30 * 60e3);
});

test("other 4xx change nothing", () => {
  const f = mk();
  f.record(k, { status: 400 }, 0);
  assert.deepEqual([f.F(k), f.cooldownUntil(k), f.exhaustedUntil("opencode-go|c1")], [0, 0, 0]);
});
```

- [ ] **Step 2: Implement**

```js
// omniroute-smart/strategies/smart/failures.mjs
// PRD "Failures (F)": in-memory EWMA of 5xx/timeouts; exponential cooldown; 429/quota marks the connection exhausted.
export function makeFailures({ alpha, baseSeconds, maxMinutes }) {
  const F = new Map(), streak = new Map(), cool = new Map(), exhausted = new Map();
  const connKey = (key) => { const [p, , c] = key.split("|"); return `${p}|${c}`; };
  return {
    record(key, { status, retryAfterMs = 0, quotaResetAt = null, fallbackResetAt = null }, now) {
      if (status === 429) {
        // PRD: the window the error names, else the shortest window with data, else failureBackoffMaxMinutes
        const named = quotaResetAt ? Date.parse(quotaResetAt) : NaN;
        const until = Number.isFinite(named) && named > now ? named : fallbackResetAt > now ? fallbackResetAt : now + maxMinutes * 60e3;
        exhausted.set(connKey(key), until);
        return;
      }
      const failed = status === 0 || status === 408 || status >= 500;
      if (!failed && status >= 400) return; // caller/content errors are not provider health
      F.set(key, (failed ? alpha : 0) + (1 - alpha) * (F.get(key) ?? 0));
      if (!failed) { streak.set(key, 0); return; }
      const n = (streak.get(key) ?? 0) + 1;
      streak.set(key, n);
      const backoff = Math.min(baseSeconds * 1e3 * 2 ** (n - 1), maxMinutes * 60e3);
      cool.set(key, now + Math.max(backoff, retryAfterMs));
    },
    F: (key) => F.get(key) ?? 0,
    cooldownUntil: (key) => cool.get(key) ?? 0,
    exhaustedUntil: (ck) => exhausted.get(ck) ?? 0,
  };
}
```

- [ ] **Step 3: Run the tests, then commit**

Run: `node --test 'omniroute-smart/strategies/smart/test/*.test.mjs' && bash tools/validate.sh`
Expected: PASS.

```bash
git add omniroute-smart/strategies/smart
git commit -m "feat(smart): failure EWMA, exponential cooldown and quota exhaustion marks"
```

---

### Task 6: Ranking (gates, classes, utility, effort)

**Files:**
- Create: `omniroute-smart/strategies/smart/rank.mjs`
- Modify: `omniroute-smart/plugins/smart-catalog/lib/score.mjs` (catalog gains `tool_call`, `input_modalities`)
- Test: `omniroute-smart/strategies/smart/test/rank.test.mjs`, `omniroute-smart/plugins/smart-catalog/test/score.test.mjs`

**Interfaces:**
- Consumes: the `terms` output (Task 4).
- Produces:
  - `gateReasons(entry, {body}) → string[]` (capability gates only; breaker, lock, expiry, cooldown, headroom and quality are applied by `candidates()` and `rank()`);
  - `rank(cands, {taskKind, cfg}) → {ranked: Cand[], dropped: {id, reason}[]}`;
  - `effortFor(answers, options) → string|null`.

  ```js
  Cand = { id, idx, n, Q, S, W, minH, F, metered, pricePct, preGates: string[], cooldownUntil, exhaustedUntil }
  ```

  Each ranked entry gains `{cls, U, terms: {Q, S, Wn, order, F}}`.

- [ ] **Step 1: Write the failing tests**

```js
// omniroute-smart/strategies/smart/test/rank.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { effortFor, gateReasons, rank } from "../rank.mjs";

const cfg = { qualityFloorDelta: 0.10, scarcityClassCut: 0.9, meteredPriceFactor: 0.2, minHeadroomPct: { small: 1, medium: 3, large: 6 },
  weights: { plan: { wQ: .5, wS: .25, wW: .1, wO: .05, wH: .1 }, execute_edit: { wQ: .3, wS: .3, wW: .2, wO: .1, wH: .1 },
    review_audit: { wQ: .5, wS: .25, wW: .1, wO: .05, wH: .1 }, mechanical: { wQ: .2, wS: .3, wW: .2, wO: .1, wH: .2 } } };
const c = (id, o) => ({ id, idx: 0, n: 2, Q: 0.8, S: 0.3, W: 0, minH: 50, F: 0, metered: false, pricePct: 0.5, preGates: [], cooldownUntil: 0, exhaustedUntil: 0, ...o });
const ids = (r) => r.ranked.map((x) => x.id);

test("owner's case: A (S .54) ranks above B (S 1.0) even with B declared first", () => {
  assert.deepEqual(ids(rank([c("B", { idx: 0, S: 1.0 }), c("A", { idx: 1, S: 0.54, W: 0.72 })], { taskKind: "plan", cfg, scope: "medium", now: 0 })), ["A", "B"]);
});

test("longest-cycle counter-case: C (S .04) above D (S 1.0)", () => {
  assert.deepEqual(ids(rank([c("D", { idx: 0, S: 1 }), c("C", { idx: 1, S: 0.04, W: 26 })], { taskKind: "execute_edit", cfg, scope: "small", now: 0 })), ["C", "D"]);
});

test("metered members always come last", () => {
  assert.deepEqual(ids(rank([c("M", { idx: 0, metered: true, Q: 0.95 }), c("S", { idx: 1, S: 0.95, Q: 0.9 })], { taskKind: "plan", cfg, scope: "small", now: 0 })), ["S", "M"]);
});

test("headroom gate depends on scope", () => {
  const r = rank([c("X", { minH: 4 }), c("Y", { idx: 1 })], { taskKind: "plan", cfg, scope: "large", now: 0 });
  assert.deepEqual([ids(r), r.dropped], [["Y"], [{ id: "X", reason: "headroom 4.0% < 6% (large)" }]]);
  assert.deepEqual(ids(rank([c("X", { minH: 4 })], { taskKind: "plan", cfg, scope: "medium", now: 0 })), ["X"]);
});

test("quality floor is relative to the best surviving Q", () => {
  const r = rank([c("hi", { Q: 0.9 }), c("lo", { idx: 1, Q: 0.79 })], { taskKind: "plan", cfg, scope: "small", now: 0 });
  assert.deepEqual(r.dropped.map((d) => d.id), ["lo"]);
});

test("cooldown, exhaustion and pre-gates drop with reasons", () => {
  const r = rank([c("a", { cooldownUntil: 10 }), c("b", { exhaustedUntil: 10 }), c("x", { preGates: ["breaker_open"] }), c("ok", {})],
    { taskKind: "plan", cfg, scope: "small", now: 5 });
  assert.deepEqual(r.dropped.map((d) => [d.id, d.reason]), [["a", "cooldown"], ["b", "quota_exhausted"], ["x", "breaker_open"]]);
});

test("n = 1 gives order 1; max W = 0 gives Wn 0", () => {
  const r = rank([c("only", { n: 1, W: 0 })], { taskKind: "plan", cfg, scope: "small", now: 0 });
  assert.equal(r.ranked[0].terms.order, 1);
  assert.equal(r.ranked[0].terms.Wn, 0);
});

test("metered utility replaces S and W with the price term", () => {
  const r = rank([c("M", { metered: true, Q: 1, pricePct: 0, F: 0, n: 1 })], { taskKind: "plan", cfg, scope: "small", now: 0 });
  assert.ok(Math.abs(r.ranked[0].U - (0.5 * 1 + (0.25 + 0.1) * 0.2 * 1 + 0.05 * 1 + 0.1 * 1)) < 1e-9);
});

test("everything gated returns an empty ranking with every reason", () => {
  const r = rank([c("a", { minH: 0 }), c("b", { idx: 1, preGates: ["plan_expired"] })], { taskKind: "plan", cfg, scope: "small", now: 0 });
  assert.equal(r.ranked.length, 0);
  assert.equal(r.dropped.length, 2);
});

test("context, tools and modality gates use the scorecard catalog", () => {
  const big = { messages: [{ role: "user", content: "x".repeat(40000) }], tools: [{ type: "function" }] };
  assert.deepEqual(gateReasons({ catalog: { limits: { context: 8000 }, tool_call: false } }, { body: big }), ["context", "tools"]);
  assert.deepEqual(gateReasons({ catalog: { input_modalities: ["text"] } },
    { body: { messages: [{ role: "user", content: [{ type: "image_url", image_url: { url: "x" } }] }] } }), ["modality"]);
  assert.deepEqual(gateReasons({ catalog: null }, { body: big }), []);
});

test("effort follows complexity and scope; mechanical is lowest", () => {
  const o = ["high", "low", "medium"];
  assert.equal(effortFor({ task_kind: "plan", complexity: "high", scope: "small" }, o), "high");
  assert.equal(effortFor({ task_kind: "plan", complexity: "low", scope: "small" }, o), "low");
  assert.equal(effortFor({ task_kind: "plan", complexity: "medium", scope: "medium" }, o), "medium");
  assert.equal(effortFor({ task_kind: "mechanical", complexity: "high", scope: "large" }, o), "low");
  assert.equal(effortFor({ task_kind: "plan", complexity: "high", scope: "large" }, []), null);
});
```

- [ ] **Step 2: Implement**

```js
// omniroute-smart/strategies/smart/rank.mjs
// PRD gates → classes → utility. All U terms are in [0,1].
const EFFORTS = ["minimal", "low", "medium", "high", "xhigh", "max"];

export function gateReasons(entry, { body }) {
  const out = [];
  const cat = entry?.catalog;
  if (!cat) return out; // unknown model: no capability data to gate on
  const estTokens = JSON.stringify(body.messages ?? []).length / 4;
  if (cat.limits?.context && estTokens > cat.limits.context) out.push("context");
  if (Array.isArray(body.tools) && body.tools.length && body.tool_choice !== "none" && cat.tool_call === false) out.push("tools");
  const wantsImage = (body.messages ?? []).some((m) => Array.isArray(m.content) && m.content.some((p) => p?.type === "image_url" || p?.type === "input_image"));
  if (wantsImage && Array.isArray(cat.input_modalities) && !cat.input_modalities.includes("image")) out.push("modality");
  return out;
}

export function rank(cands, { taskKind, cfg, scope, now }) {
  const dropped = [];
  const drop = (c, reason) => { dropped.push({ id: c.id, ...(c.connId != null && { connId: c.connId }), reason }); return false; };
  let alive = cands.filter((c) =>
    c.preGates.length ? drop(c, c.preGates[0])
      : now < c.cooldownUntil ? drop(c, "cooldown")
        : now < c.exhaustedUntil ? drop(c, "quota_exhausted")
          : c.minH < cfg.minHeadroomPct[scope] ? drop(c, `headroom ${c.minH.toFixed(1)}% < ${cfg.minHeadroomPct[scope]}% (${scope})`)
            : true);
  const qBest = Math.max(...alive.map((c) => c.Q));
  alive = alive.filter((c) => c.Q >= qBest - cfg.qualityFloorDelta || drop(c, `quality ${c.Q.toFixed(2)} < ${(qBest - cfg.qualityFloorDelta).toFixed(2)}`));
  const w = cfg.weights[taskKind];
  const maxW = Math.max(0, ...alive.filter((c) => !c.metered).map((c) => c.W));
  const ranked = alive.map((c) => {
    const order = c.n <= 1 ? 1 : 1 - c.idx / (c.n - 1);
    const Wn = maxW > 0 && !c.metered ? Math.log1p(c.W) / Math.log1p(maxW) : 0;
    const quota = c.metered ? (w.wS + w.wW) * cfg.meteredPriceFactor * (1 - (c.pricePct ?? 0.5)) : w.wS * (1 - c.S) + w.wW * Wn;
    const U = w.wQ * c.Q + quota + w.wO * order + w.wH * (1 - c.F);
    const cls = c.metered ? 3 : c.S < cfg.scarcityClassCut ? 1 : 2;
    return { ...c, cls, U, terms: { Q: c.Q, S: c.S, Wn, order, F: c.F } };
  }).sort((a, b) => a.cls - b.cls || b.U - a.U);
  return { ranked, dropped };
}

export function effortFor(answers, options) {
  const opts = [...new Set(options ?? [])].filter((o) => EFFORTS.includes(o)).sort((a, b) => EFFORTS.indexOf(a) - EFFORTS.indexOf(b));
  if (!opts.length) return null;
  if (answers.task_kind === "mechanical") return opts[0];
  if (answers.complexity === "high" || answers.scope === "large") return opts.at(-1);
  if (answers.complexity === "low" && answers.scope === "small") return opts[0];
  return opts.includes("medium") ? "medium" : opts[Math.floor((opts.length - 1) / 2)];
}
```

`gateReasons` reads `catalog.tool_call` and `catalog.input_modalities`. P2's scorecard does not record them yet. Add them in this task, in `omniroute-smart/plugins/smart-catalog/lib/score.mjs`:

```js
        tool_call: r.md?.tool_call ?? null,
        input_modalities: r.md?.modalities?.input ?? null,
```

Put them inside the `catalog: {…}` block, and add one assertion in P2's `score.test.mjs` that `tool_call` is carried through. Both are models.dev model fields (`data-sources-quota.md` §3). Add `tool_call: true` to the `glm-5.3` entry of the `modelsDev` fixture in `score.test.mjs` so the assertion checks a real value, and assert `tool_call === null` for `glm-5.3-flash`. `null` means unknown, and unknown never gates.

- [ ] **Step 3: Run the tests, then commit**

Run: `node --test 'omniroute-smart/**/*.test.mjs' && bash tools/validate.sh`
Expected: PASS.

```bash
git add omniroute-smart/strategies/smart omniroute-smart/plugins/smart-catalog
git commit -m "feat(smart): gates, classes, utility and effort; scorecard carries tool_call and input modalities"
```

---

### Task 7: Decision log and task stats

**Files:**
- Create: `omniroute-smart/strategies/smart/log.mjs`
- Test: `omniroute-smart/strategies/smart/test/log.test.mjs`

**Interfaces:**
- Consumes: `appendJsonl`, `writeJsonAtomic`, `readJson` (`../../plugins/smart-catalog/lib/files.mjs`).
- Produces: `makeLog({dataDir, maxBytes, files, now, flushMs}) → {begin, attempt, finalize, sweep, flush}`.
  - `begin(s)` counts the request and its task kind in today's stats, and holds the decision `s` in pending.
  - `finalize` also counts `decisions` in the bucket of the day the request began, so a finished day has `decisions == requests` exactly (the check-server row relies on it).
  - `attempt(s, a)` appends `{target, status, latency_ms}` and counts attempts and quota attempts.
  - `finalize(s, extra)` writes exactly one `decisions.jsonl` line, idempotently.
  - `sweep(maxAgeMs)` finalizes pending decisions older than `maxAgeMs` with `outcome.status = "unfinished"`.

  Stats days older than 90 are pruned on flush.

- [ ] **Step 1: Write the failing tests**

```js
// omniroute-smart/strategies/smart/test/log.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, statSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { makeLog } from "../log.mjs";

const setup = (t0 = Date.parse("2026-10-13T10:00:00Z")) => {
  const dataDir = mkdtempSync(join(tmpdir(), "smart-log-"));
  let t = t0;
  const log = makeLog({ dataDir, maxBytes: 1e6, files: 3, now: () => t, flushMs: 0 });
  return { dataDir, log, advance: (ms) => (t += ms) };
};
const lines = (d) => readFileSync(join(d, "decisions.jsonl"), "utf8").trim().split("\n").map((l) => JSON.parse(l));
const s = (id) => ({ requestId: id, combo: "smart-execution", answers: { task_kind: { value: "execute_edit" } }, ranked: [], dropped: [], lane: "single", effort: null, startedAt: 0 });

test("one line per request with attempts, written once, mode 0600", () => {
  const { dataDir, log } = setup();
  const d = s("r1");
  log.begin(d);
  log.attempt(d, { target: "a/x", status: 503, latency_ms: 10 });
  log.attempt(d, { target: "a/y", status: 200, latency_ms: 20 });
  log.finalize(d, { status: "ok" });
  log.finalize(d, { status: "ok" });
  const [line] = lines(dataDir);
  assert.equal(lines(dataDir).length, 1);
  assert.deepEqual(line.attempts.map((a) => a.status), [503, 200]);
  assert.equal(line.outcome.winner, "a/y"); // the target that answered, not the top-ranked one
  assert.equal(statSync(join(dataDir, "decisions.jsonl")).mode & 0o777, 0o600);
});

test("task-stats counts requests, kinds, attempts and quota attempts per day", () => {
  const { dataDir, log } = setup();
  const d = s("r1");
  log.begin(d);
  log.attempt(d, { target: "a/x", status: 429, latency_ms: 1 });
  log.finalize(d, { status: "ok" });
  log.flush();
  const day = JSON.parse(readFileSync(join(dataDir, "task-stats.json"), "utf8")).days["2026-10-13"]["smart-execution"];
  assert.deepEqual([day.requests, day.task_kind.execute_edit, day.attempts, day.quota_attempts, day.decisions], [1, 1, 1, 1, 1]);
});

test("a request finalized after midnight counts its decision on the day it began", () => {
  const { dataDir, log, advance } = setup(Date.parse("2026-10-13T23:59:59Z"));
  const d = s("late");
  log.begin(d);
  advance(5e3);
  log.finalize(d, { status: "ok" });
  log.flush();
  const days = JSON.parse(readFileSync(join(dataDir, "task-stats.json"), "utf8")).days;
  assert.deepEqual([days["2026-10-13"]["smart-execution"].requests, days["2026-10-13"]["smart-execution"].decisions], [1, 1]);
  assert.equal(days["2026-10-14"], undefined);
});

test("the sweep finalizes requests whose loop never reported success", () => {
  const { dataDir, log, advance } = setup();
  log.begin(s("stuck"));
  advance(6 * 60e3);
  log.sweep(5 * 60e3);
  assert.equal(lines(dataDir)[0].outcome.status, "unfinished");
});

test("days older than 90 are pruned", () => {
  const { dataDir, log, advance } = setup(Date.parse("2026-01-01T00:00:00Z"));
  log.begin(s("old")); log.flush();
  advance(100 * 86400e3);
  log.begin(s("new")); log.flush();
  assert.deepEqual(Object.keys(JSON.parse(readFileSync(join(dataDir, "task-stats.json"), "utf8")).days), ["2026-04-11"]);
});
```

- [ ] **Step 2: Implement**

```js
// omniroute-smart/strategies/smart/log.mjs
// decisions.jsonl: exactly one line per client request. task-stats.json: daily aggregates kept 90 days (survive rotation/restarts).
import { join } from "node:path";
import { appendJsonl, readJson, writeJsonAtomic } from "../../plugins/smart-catalog/lib/files.mjs";

export function makeLog({ dataDir, maxBytes, files, now = Date.now, flushMs = 10e3 }) {
  const statsPath = join(dataDir, "task-stats.json");
  const stats = readJson(statsPath) ?? { version: 1, days: {} };
  const pending = new Map();
  let timer = null;
  const bucket = (combo, t = now()) => {
    const d = (stats.days[new Date(t).toISOString().slice(0, 10)] ??= {});
    return (d[combo] ??= { task_kind: {}, requests: 0, decisions: 0, attempts: 0, quota_attempts: 0, fusion_degraded: 0, fusion_failed: 0 });
  };
  const flush = () => {
    clearTimeout(timer); timer = null;
    const cutoff = new Date(now() - 90 * 86400e3).toISOString().slice(0, 10);
    for (const d of Object.keys(stats.days)) if (d < cutoff) delete stats.days[d];
    writeJsonAtomic(statsPath, stats);
  };
  const schedule = () => { if (flushMs === 0) return; timer ??= setTimeout(flush, flushMs); timer.unref?.(); };
  return {
    begin(s) {
      const b = bucket(s.combo);
      b.requests++;
      const kind = s.answers?.task_kind?.value;
      if (kind) b.task_kind[kind] = (b.task_kind[kind] ?? 0) + 1;
      pending.set(s.requestId, { s, attempts: [], at: now() });
      schedule();
    },
    attempt(s, a) {
      const p = pending.get(s.requestId);
      if (!p) return;
      p.attempts.push(a);
      const b = bucket(s.combo, p.at); // same day as the request and its decision
      b.attempts++;
      if (a.status === 429) b.quota_attempts++;
      schedule();
    },
    finalize(s, outcome) {
      const p = pending.get(s.requestId);
      if (!p) return;
      pending.delete(s.requestId);
      const b = bucket(s.combo, p.at); // the day the request began: requests and decisions pair up per day
      b.decisions++;
      if (s.lane === "fusion_degraded" || s.lane === "fusion_failed") b[s.lane]++;
      const { requestId, combo, answers, stages, ranked, dropped, lane, fusion_eligible, effort, backend } = s;
      appendJsonl(join(dataDir, "decisions.jsonl"), {
        request_id: requestId, combo, at: new Date(p.at).toISOString(),
        classification: { answers, stages, backend },
        candidates: { ranked: (ranked ?? []).map(({ id, connId, cls, U, terms }) => ({ id, connId, cls, U, terms })), dropped },
        outcome: { ...outcome, lane, fusion_eligible: fusion_eligible ?? false, effort, winner: [...p.attempts].reverse().find((a) => a.status >= 200 && a.status < 300)?.target ?? null, elapsed_ms: now() - p.at },
        attempts: p.attempts,
      }, { maxBytes, files });
      schedule();
    },
    sweep(maxAgeMs) {
      for (const p of [...pending.values()]) if (now() - p.at > maxAgeMs) this.finalize(p.s, { status: "unfinished" });
    },
    flush,
  };
}
```

`flushMs: 0` means "flush only when called", which the tests use. In production, Task 8 uses 10 s plus a 60 s sweep timer, both `unref`'d.

- [ ] **Step 3: Run the tests, then commit**

Run: `node --test 'omniroute-smart/strategies/smart/test/*.test.mjs' && bash tools/validate.sh`
Expected: PASS.

```bash
git add omniroute-smart/strategies/smart
git commit -m "feat(smart): one-line-per-request decision log and 90-day task stats"
```

---

### Task 8: The `smart` module (prelude, order, observe, probe)

**Files:**
- Create: `omniroute-smart/strategies/smart.mjs`
- Modify: `docker/omniroute-smart/Dockerfile`
- Test: `omniroute-smart/strategies/smart/test/module.test.mjs`

**Interfaces:**
- Consumes: Tasks 2–7, plus the context keys from the P1 report:
  - `getQuotaCache(connectionId)`, `getQuotaFetcher(provider)`, `getUsageFetcher(provider)` (or `getUsageForProvider(connection)`);
  - `getCircuitBreaker(provider)`, `isModelLocked(provider, connectionId, model)`;
  - `getPluginConfig(name)`, `listProviderConnections()`, `handleSingleModel(body, modelStr)`.
- Produces:
  - `createSmart(deps)`, whose default instance uses `globalThis.__omniSmart.ctx`;
  - `decide(answers, ranked, cfg, body)`.

  The default export is `{ name: "smart", prelude, order, observe }`.

- [ ] **Step 1: Write the failing tests**

They run against a fake context, a fake System One and temp data files.

```js
// omniroute-smart/strategies/smart/test/module.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { AsyncLocalStorage } from "node:async_hooks";
import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createSmart } from "../../smart.mjs";

const NOW = Date.parse("2026-10-13T10:00:00Z");
const Q = (q) => Object.fromEntries(["plan", "review_audit", "execute_edit", "mechanical"].map((k) =>
  [k, Object.fromEntries(["frontend", "backend", "fullstack", "infra_docs"].map((d) => [d, { q, c: 1, source: "measured" }]))]));
function env({ layaDown = false, scorecardAge = 0, comboConfig = {}, quota = {}, conns = [{ id: "c1", provider: "p", priority: 1, isActive: true }], quotaFetcher } = {}) {
  const dataDir = mkdtempSync(join(tmpdir(), "smart-mod-"));
  const settings = JSON.parse(readFileSync(new URL("./fixtures/settings.json", import.meta.url), "utf8"));
  writeFileSync(join(dataDir, "settings.json"), JSON.stringify(settings));
  writeFileSync(join(dataDir, "scorecard.json"), JSON.stringify({ generated_at: new Date(NOW - scorecardAge).toISOString(), entries: {
    "p/a": { Q: Q(0.8), catalog: { effort_options: ["low", "medium", "high"] } }, "p/b": { Q: Q(0.78), catalog: {} } } }));
  const als = new AsyncLocalStorage();
  globalThis.__omniSmart = { als, ctx: {} };
  const ctx = {
    getPluginConfig: async () => ({ config: { systemOneLayaUrl: "http://system-one:8000" } }),
    getQuotaCache: (conn) => quota[conn] ?? null,
    getQuotaFetcher: quotaFetcher ? () => quotaFetcher : undefined,
    getCircuitBreaker: () => ({ getStatus: () => ({ state: "CLOSED" }) }),
    isModelLocked: () => false,
    listProviderConnections: async () => conns,
  };
  const fetchImpl = async () => {
    if (layaDown) throw new TypeError("fetch failed");
    return new Response(JSON.stringify({ answers: {
      task_kind: { type: "choice", choice: "execute_edit", answer_confidence: 0.9 }, domain: { type: "choice", choice: "backend", answer_confidence: 0.9 },
      scope: { type: "score", probabilities: { 0: 0.9, 1: 0.05, 2: 0.05 } }, complexity: { type: "score", probabilities: { 0: 0.1, 1: 0.8, 2: 0.1 } },
      second_opinion: { type: "noul", noul: 0.05 } } }));
  };
  const smart = createSmart({ dataDir, ctx, env: {}, fetchImpl, now: () => NOW, flushMs: 0 });
  const combo = { name: "smart-execution", strategy: "smart", models: ["p/a", "p/b"], config: comboConfig };
  const run = async (body, targets = [{ modelStr: "p/a", provider: "p" }, { modelStr: "p/b", provider: "p" }]) => als.run({ body }, async () => {
    const pre = await smart.prelude({ strategy: "smart", body, combo, config: comboConfig, store: als.getStore() });
    if (pre?.response) return { response: pre.response };
    const ordered = await smart.order(targets, { combo, config: comboConfig });
    smart.observe({ store: als.getStore(), target: ordered[0], status: 200, latencyMs: 12 });
    return { ordered: ordered.map((t) => t.modelStr), store: als.getStore() };
  });
  return { dataDir, run, smart, ctx };
}
const body = { messages: [{ role: "user", content: "Add pagination to /orders" }] };

test("happy path: ranks members, sets effort, writes one decision line", async () => {
  const e = env();
  const r = await e.run(structuredClone(body));
  assert.deepEqual(r.ordered, ["p/a", "p/b"]);
  assert.equal(r.store.smart.effort, "medium");
  const line = JSON.parse(readFileSync(join(e.dataDir, "decisions.jsonl"), "utf8").trim());
  assert.equal(line.classification.answers.task_kind.value, "execute_edit");
  assert.equal(line.classification.stages.task_kind, "laya"); // PRD stage vocabulary: laya | jev:<backend> | arbiter | default
  assert.equal(line.attempts.length, 1);
  assert.equal(line.outcome.fusion_eligible, false); // execute_edit is not an analysis kind
});

test("Laya down and no consented remote → 503 system_one_unavailable", async () => {
  const { response } = await env({ layaDown: true }).run(structuredClone(body));
  assert.equal(response.status, 503);
  assert.equal((await response.json()).error.reason, "system_one_unavailable");
});

test("stale scorecard → 503 scorecard_missing", async () => {
  const { response } = await env({ scorecardAge: 13 * 3600e3 }).run(structuredClone(body));
  assert.equal((await response.json()).error.reason, "scorecard_missing");
});

test("invalid combo config → 503 config_invalid naming the key", async () => {
  const { response } = await env({ comboConfig: { qualityFloorDelta: 5 } }).run(structuredClone(body));
  const err = (await response.json()).error;
  assert.deepEqual([err.reason, /qualityFloorDelta/.test(err.detail)], ["config_invalid", true]);
});

test("every member gated → 503 no_candidate_fits with per-member reasons, no upstream call", async () => {
  const e = env({ quota: { c1: { quotas: { weekly: { remainingPercentage: 1, resetAt: "2026-10-14T00:00:00Z", windowSeconds: 604800 } } } } });
  const { response } = await e.run(structuredClone(body));
  const err = (await response.json()).error;
  assert.equal(err.reason, "no_candidate_fits");
  assert.match(err.detail, /p\/a@c1: headroom/);
});

test("a connection-pinned object member is keyed provider/model and uses its own connection's plan", async () => {
  const e = env();
  const combo = { name: "smart-execution", strategy: "smart", config: {},
    models: [{ kind: "model", providerId: "p", model: "b", connectionId: "c9" }, "p/a"] };
  const out = await globalThis.__omniSmart.als.run({}, async () => {
    await e.smart.prelude({ strategy: "smart", body: structuredClone(body), combo, config: {}, store: globalThis.__omniSmart.als.getStore() });
    return globalThis.__omniSmart.als.getStore().smart;
  });
  assert.deepEqual(out.ranked.map((c) => c.id).sort(), ["p/a", "p/b"]);
  assert.equal(out.ranked.find((c) => c.id === "p/b").connId, "c9");
});

test("the probe answers per consented backend and SKIPs the rest without calling them", async () => {
  const { response } = await env().run({ messages: [{ role: "user", content: "__smart_probe__" }] });
  const { probe } = await response.json();
  assert.deepEqual(probe.map((p) => [p.name, p.ok ?? null, p.skip ?? null]),
    [["laya", true, null], ["zen-free", null, "not consented"], ["typesafe", null, "not consented"], ["zen-paid", null, "not consented"]]);
});

test("a request lists provider connections once, however many members the combo has", async () => {
  const e = env();
  let calls = 0;
  const list = e.ctx.listProviderConnections;
  e.ctx.listProviderConnections = async () => { calls++; return list(); };
  await e.run(structuredClone(body));
  assert.equal(calls, 1);
});

test("an unpinned member on two connections is two candidates; order() pins each target to its connection", async () => {
  const low = { quotas: { weekly: { remainingPercentage: 1, resetAt: "2026-10-14T00:00:00Z", windowSeconds: 604800 } } };
  const e = env({ quota: { c1: low }, conns: [{ id: "c1", provider: "p", priority: 1, isActive: true }, { id: "c2", provider: "p", priority: 2, isActive: true }] });
  const out = await globalThis.__omniSmart.als.run({}, async () => {
    const s = globalThis.__omniSmart.als.getStore();
    await e.smart.prelude({ strategy: "smart", body: structuredClone(body), combo: { name: "x", strategy: "smart", models: ["p/a"], config: {} }, config: {}, store: s });
    return { smart: s.smart, ordered: await e.smart.order([{ modelStr: "p/a", provider: "p" }], {}) };
  });
  assert.deepEqual(out.smart.dropped.map((d) => [d.id, d.connId]), [["p/a", "c1"]]); // c1 is nearly exhausted
  assert.deepEqual(out.ordered.map((t) => [t.modelStr, t.connectionId]), [["p/a", "c2"]]);
});

test("a quota-cache entry marked exhausted gates the connection until its reset", async () => {
  const e = env({ quota: { c1: { exhausted: true, nextResetAt: "2026-10-14T00:00:00Z", quotas: {} } } });
  const err = (await (await e.run(structuredClone(body))).response.json()).error;
  assert.equal(err.reason, "no_candidate_fits");
  assert.match(err.detail, /p\/a@c1: quota_exhausted/);
});

test("a quota fetcher that throws synchronously never fails the request", async () => {
  const r = await env({ quotaFetcher: () => { throw new Error("boom"); } }).run(structuredClone(body));
  assert.deepEqual(r.ordered, ["p/a", "p/b"]); // no strategy_unavailable; the members are scored as unknown quota
});

test("order() without a prelude decision fails closed", async () => {
  const e = env();
  await assert.rejects(e.smart.order([{ modelStr: "p/a" }], {}), /SmartStrategyUnavailable/);
});
```

The module reads the context lazily through `deps.ctx ?? globalThis.__omniSmart.ctx` on each call, so the runtime can register context keys after the module loaded. Tests pass their fake through `deps.ctx` (here via `env()`'s `quota` parameter). Create `test/fixtures/settings.json` by running `uv run llmenv.py smart-settings` on a temp config, then set `"plans": []`.

- [ ] **Step 2: Implement**

```js
// omniroute-smart/strategies/smart.mjs
// OmniRoute `smart` combo strategy (loaded by the P1 patch). prelude → classify + rank (or 503); order → apply ranking;
// observe → failures + one decision line per request. Fusion lanes are added in P4 via decide().
import { randomUUID } from "node:crypto";
import { readFileSync } from "node:fs";
import { effectiveConfig, loadScorecard, loadSettings, makePluginConfig } from "./smart/config.mjs";
import { SmartError, errorResponse } from "./smart/errors.mjs";
import { makeFailures } from "./smart/failures.mjs";
import { makeLog } from "./smart/log.mjs";
import { declaredWindow, makeBurn, planFor, terms, windowsFromCacheEntry, windowsFromQuotaInfo, windowsFromUsage } from "./smart/quota.mjs";
import { effortFor, gateReasons, rank } from "./smart/rank.mjs";
import { backendsFor, classify, parseArbiter, skippedRemotes } from "./smart/systemone.mjs";

const manifest = JSON.parse(readFileSync(new URL("../plugins/smart-catalog/plugin.json", import.meta.url), "utf8"));
const USAGE_TTL_MS = 5 * 60e3;
// 5 min, plus the longest fusion run the settings limits allow (P4: 2 × panelHardTimeoutMs + fusionJudgeTimeoutMs + 5 s
// margin, both capped at 600 s by P2), so an in-flight fusion request is never swept as "unfinished". check-server and
// P5's metrics treat a day as settled 37 min after UTC midnight (this age + the 60 s sweep tick + the 10 s flush).
export const SWEEP_AGE_MS = 5 * 60e3 + 2 * 600e3 + 600e3 + 5e3;
export const PROBE_MARKER = "__smart_probe__";

/** P3: always single. P4 replaces the lane decision; fusion_eligible is recorded for the explain log. */
export function decide(answers, ranked, cfg, body) {
  const tools = Array.isArray(body.tools) && body.tools.length > 0 && body.tool_choice !== "none";
  const confident = Object.values(answers).every((a) => a.confidence >= cfg.systemOneMinConfidence);
  const analysis = ["plan", "review_audit"].includes(answers.task_kind.value);
  const wants = answers.complexity.value === "high" || answers.second_opinion.value > cfg.fusionSecondOpinionMin;
  return { lane: "single", fusion_eligible: analysis && wants && confident && !tools && ranked.length >= 2 };
}

export function createSmart(deps) {
  const now = deps.now ?? Date.now;
  const ctx = () => deps.ctx ?? globalThis.__omniSmart?.ctx ?? {};
  const store = () => globalThis.__omniSmart?.als?.getStore?.();
  let state = null; // lazily built from settings (failures/log need settings values)
  const usage = new Map(); // provider|conn → {at, windows, inflight}

  // shortcut: failure and log tuning come from the first request's effective config; later per-combo values for
  // failureAlpha / failureBackoff* / burnAlpha / decisionLog* are ignored. Upgrade to a per-combo state map if operators tune them per combo.
  const init = (settings, cfg) => {
    if (state) return state;
    state = {
      failures: makeFailures({ alpha: cfg.failureAlpha, baseSeconds: cfg.failureBackoffBaseSeconds, maxMinutes: cfg.failureBackoffMaxMinutes }),
      burn: makeBurn(cfg.burnAlpha),
      log: makeLog({ dataDir: deps.dataDir, maxBytes: settings.decisionLogMaxMB * 1048576, files: settings.decisionLogFiles, now, flushMs: deps.flushMs ?? 10e3 }),
      pluginConfig: makePluginConfig(ctx(), settings.pluginConfigTtlSeconds, manifest.configSchema, now),
    };
    if (deps.flushMs !== 0) setInterval(() => state.log.sweep(SWEEP_AGE_MS), 60e3).unref?.();
    return state;
  };

  // PRD edge case "same model on several connections": an unpinned member becomes one candidate per active connection,
  // so each account's quota is scored on its own. [null] when the provider lists no connection (quota then neutral).
  // getProviderConnections maps the is_active column to isActive (1/0 or true/false, as OmniRoute's own filter accepts)
  const active = (c) => c.isActive !== false && c.isActive !== 0;
  // one connection listing per request, shared by keys() and every connectionsFor() call (the listing may hit SQLite)
  const listConns = async () => (await ctx().listProviderConnections?.()) ?? [];
  function connectionsFor(provider, pinned, conns) {
    if (pinned) return [pinned];
    const ids = conns.filter((c) => c.provider === provider && active(c)).sort((a, b) => (a.priority ?? 99) - (b.priority ?? 99)).map((c) => c.id);
    return ids.length ? ids : [null];
  }

  function liveWindows(provider, connId) {
    // 1. the in-process quota cache (QuotaCacheEntry), filled by OmniRoute's own quota polling
    const entry = connId ? ctx().getQuotaCache?.(connId) : null;
    // OmniRoute marks a drained connection exhausted until nextResetAt, sometimes with no usable quotas left
    const exhaustedUntil = entry?.exhausted === true ? (Date.parse(entry.nextResetAt) || now() + USAGE_TTL_MS) : null;
    if (exhaustedUntil && exhaustedUntil > now()) return { windows: [], unlimited: false, exhaustedUntil };
    const cached = windowsFromCacheEntry(entry);
    if (cached.length) return { windows: cached, unlimited: false };
    // 2. else the provider's quota fetcher (QuotaInfo, may say unlimited), then its usage fetcher; both
    // stale-while-revalidate: never block the request on a fetch
    const key = `${provider}|${connId}`;
    const hit = usage.get(key);
    const quotaFetcher = ctx().getQuotaFetcher?.(provider);
    const usageFetcher = quotaFetcher ? null : ctx().getUsageFetcher?.(provider);
    if ((quotaFetcher || usageFetcher) && (!hit || now() - hit.at > USAGE_TTL_MS) && !hit?.inflight) {
      usage.set(key, { ...(hit ?? { windows: [], unlimited: false }), inflight: true });
      Promise.resolve().then(() => (quotaFetcher ? quotaFetcher(connId) : usageFetcher(connId))) // a synchronous throw lands in .catch
        .then((u) => usage.set(key, { at: now(), windows: quotaFetcher ? windowsFromQuotaInfo(u) : windowsFromUsage(u), unlimited: quotaFetcher ? u?.unlimited === true : false }))
        .catch(() => usage.set(key, { ...(usage.get(key) ?? { windows: [], unlimited: false }), at: now(), inflight: false }));
    }
    return { windows: hit?.windows ?? [], unlimited: hit?.unlimited === true };
  }

  async function candidates(combo, body, settings, card, cfg, answers, conns) {
    conns ??= await listConns(); // callers outside the prelude (P4's judge lookup) list on their own
    // Members are "provider/model" strings or {kind:"model", providerId, model, connectionId?}; key both as provider/model.
    const members = (combo.models ?? []).map((m) => (typeof m === "string" ? { model: m }
      : { ...m, model: m.model?.includes("/") || !m.providerId ? m.model : `${m.providerId}/${m.model}` })).filter((m) => m.model);
    const n = members.length;
    const domain = answers.domain.value, kind = answers.task_kind.value;
    const out = [];
    for (const [idx, m] of members.entries()) for (const connId of connectionsFor(m.model.slice(0, m.model.indexOf("/")), m.connectionId, conns)) {
      const provider = m.model.slice(0, m.model.indexOf("/"));
      const plan = planFor(settings.plans, provider, connId);
      const live = plan?.manualRemainingPct != null ? null : liveWindows(provider, connId);
      let windows = live ? live.windows : [declaredWindow(plan, now(), cfg.noResetHorizonHours)];
      // unlimited → terms([]) → S 0 (full headroom); no data → one neutral window (PRD: scarcity neutralScarcity)
      if (!windows.length && !live?.unlimited) windows = [plan ? declaredWindow(plan, now(), cfg.noResetHorizonHours) : { name: "unknown", remainingPct: null, resetAt: null, lengthH: 730 }];
      const key = `${provider}|${connId}`;
      for (const w of windows) if (w.remainingPct != null) state.burn.observe(key, w, now());
      const t = terms(windows, { now: now(), reserve: cfg.quotaReservePercent, minResetH: cfg.minResetHours, burnPriorUtil: cfg.burnPriorUtil,
        horizonH: cfg.noResetHorizonHours, neutralScarcity: cfg.neutralScarcity, renews: plan?.renews ?? true, expiresAt: plan?.expiresAt ?? null,
        burnFor: (w) => state.burn.rate(key, w) });
      const entry = card.entries[m.model];
      const preGates = [...gateReasons(entry, { body })];
      if (ctx().getCircuitBreaker?.(provider)?.getStatus?.()?.state === "OPEN") preGates.push("breaker_open");
      if (ctx().isModelLocked?.(provider, connId, m.model.slice(provider.length + 1))) preGates.push("model_locked");
      if (plan?.expiresAt && Date.parse(plan.expiresAt) < now()) preGates.push("plan_expired");
      if (live?.exhaustedUntil) preGates.push("quota_exhausted");
      const fk = `${provider}|${m.model}|${connId}`;
      out.push({ id: m.model, connId, idx, n, Q: entry?.Q?.[kind]?.[domain]?.q ?? 0.5, S: t.S, W: t.W, minH: t.minH, F: state.failures.F(fk),
        metered: plan?.metered === true, pricePct: entry?.catalog?.price_percentile ?? 0.5, preGates,
        cooldownUntil: state.failures.cooldownUntil(fk), exhaustedUntil: state.failures.exhaustedUntil(key), quota: t.perWindow,
        // only windows with a real reset; a no-reset pool falls back to failureBackoffMaxMinutes on a 429 (PRD)
        shortestResetAt: (() => { const w = t.perWindow.filter((p) => !p.neutral && p.resetAt).sort((a, b) => a.lengthH - b.lengthH)[0]; return w ? Date.parse(w.resetAt) : null; })() });
    }
    return out;
  }

  function arbiterFor(cfg) {
    const call = ctx().handleSingleModel;
    if (!call) return undefined;
    return async (questions, text) => {
      const sys = `Answer each question about the user's request. Reply with JSON only, shaped {"<question>":{"value":<allowed value>,"confidence":<0..1>}}.\n${JSON.stringify(questions)}`;
      const res = await call({ model: cfg.classifierArbiterModel, stream: false, temperature: 0,
        messages: [{ role: "system", content: sys }, { role: "user", content: text }] }, cfg.classifierArbiterModel);
      if (!res.ok) throw new Error(`arbiter HTTP ${res.status}`);
      return parseArbiter((await res.json())?.choices?.[0]?.message?.content ?? "", questions);
    };
  }

  async function keys(conns) {
    conns ??= await listConns();
    const zen = conns.filter((c) => c.provider === "opencode-zen" && active(c)).sort((a, b) => (a.priority ?? 99) - (b.priority ?? 99))[0];
    return { zen: zen?.apiKey ?? null, typesafe: (deps.env ?? process.env).TYPESAFE_API_KEY || null };
  }

  return {
    name: "smart",
    async prelude(pctx) {
      const s = pctx.store ?? store();
      const requestId = randomUUID();
      let begun = null; // the decision already held in the log, if a later step throws
      try {
        const settings = loadSettings(deps.dataDir);
        const cfg = effectiveConfig(settings, pctx.config ?? pctx.combo?.config); // P1 passes the combo config as its own key
        init(settings, cfg);
        const plugin = await state.pluginConfig();
        const conns = await listConns();
        const k = await keys(conns);
        const backends = backendsFor(plugin, k, `llm-env-omniroute-smart/${deps.patchVersion ?? "dev"}`);
        if (lastUser(pctx.body) === PROBE_MARKER) return { response: await probe(backends, cfg, deps.fetchImpl ?? fetch, skippedRemotes(plugin, k)) };
        const card = loadScorecard(deps.dataDir, plugin.syncIntervalHours, now());
        const { answers, backend } = await (deps.classify ?? classify)({ body: pctx.body, cfg, backends, arbiter: arbiterFor(cfg),
          fetchImpl: deps.fetchImpl ?? fetch, now, batched: deps.batched ?? true });
        const flat = Object.fromEntries(Object.entries(answers).map(([k, a]) => [k, a.value]));
        const cands = await candidates(pctx.combo, pctx.body, settings, card, cfg, answers, conns);
        const { ranked, dropped } = rank(cands, { taskKind: flat.task_kind, cfg, scope: flat.scope, now: now() });
        const smart = { requestId, combo: pctx.combo?.name, cfg, answers, backend,
          stages: Object.fromEntries(Object.entries(answers).map(([k, a]) => [k, a.stage])), ranked, dropped, startedAt: now() };
        if (!ranked.length) {
          state.log.begin({ ...smart, lane: "none" });
          state.log.finalize({ ...smart, lane: "none" }, { status: 503, reason: "no_candidate_fits" });
          return { response: errorResponse("no_candidate_fits", dropped.map((d) => `${d.id}${d.connId ? `@${d.connId}` : ""}: ${d.reason}`).join("; "), requestId) };
        }
        Object.assign(smart, decide(answers, ranked, cfg, pctx.body));
        smart.effort = effortFor(flat, card.entries[ranked[0].id]?.catalog?.effort_options);
        if (smart.effort) pctx.body.reasoning_effort = smart.effort;
        if (s) s.smart = smart;
        state.log.begin(begun = smart);
        return null;
      } catch (e) {
        if (!(e instanceof SmartError)) console.error(`[smart] ${requestId} internal error: ${e?.stack ?? e}`); // no request text, no keys
        const reason = e instanceof SmartError ? e.reason : "strategy_unavailable";
        // PRD: every request entering the prelude is counted and gets one decision line. Only a request that fails
        // before the first successful init() (no log yet, e.g. config_invalid on a fresh process) is not logged.
        if (state) {
          // a throw after begin() (P4's fusion steps) finalizes that entry: begin twice would count the request twice
          const failed = begun ?? { requestId, combo: pctx.combo?.name, lane: "none", startedAt: now() };
          if (!begun) state.log.begin(failed);
          state.log.finalize(failed, { status: 503, reason });
        }
        return { response: errorResponse(reason, e instanceof SmartError ? e.detail : "internal error", requestId) };
      }
    },
    async order(targets) {
      const smart = store()?.smart;
      if (!smart) throw new Error("SmartStrategyUnavailable: no prelude decision for this request");
      // one target per ranked candidate: a pinned target must match the candidate's connection; an unpinned one is
      // cloned and pinned to it (P1 S1 confirmed the loop honours target.connectionId; see the P1 report)
      const ordered = [];
      for (const c of smart.ranked) {
        const t = targets.find((x) => x.modelStr === c.id && (x.connectionId == null || x.connectionId === c.connId));
        if (!t) continue;
        if (c.connId == null || t.connectionId === c.connId) ordered.push(t);   // no connection to pin, or already pinned to it
        else ordered.push({ ...t, connectionId: c.connId });                     // unpinned target: pin a copy to the scored connection
      }
      smart.orderedCount = ordered.length;
      return ordered;
    },
    observe(outcome) {
      const smart = outcome.store?.smart ?? store()?.smart;
      if (!smart || !state) return;
      const t = outcome.target ?? {};
      const provider = t.provider ?? String(t.modelStr).split("/")[0];
      const cand = smart.ranked.find((c) => c.id === t.modelStr && (t.connectionId == null || c.connId === t.connectionId));
      state.failures.record(`${provider}|${t.modelStr}|${t.connectionId ?? cand?.connId ?? null}`,
        { status: outcome.status ?? 0, retryAfterMs: outcome.retryAfterMs, quotaResetAt: outcome.quotaResetAt, fallbackResetAt: cand?.shortestResetAt ?? null }, now());
      state.log.attempt(smart, { target: t.modelStr, connection: t.connectionId ?? cand?.connId ?? null, status: outcome.status ?? 0, latency_ms: outcome.latencyMs });
      const attempts = (smart.attemptCount = (smart.attemptCount ?? 0) + 1);
      if ((outcome.status >= 200 && outcome.status < 300) || attempts >= (smart.orderedCount ?? Infinity)) {
        state.log.finalize(smart, { status: outcome.status });
      }
    },
  };
}

function lastUser(body) {
  const m = [...(body?.messages ?? [])].reverse().find((x) => x.role === "user");
  return typeof m?.content === "string" ? m.content.trim() : null;
}

async function probe(backends, cfg, fetchImpl, skipped) {
  const results = [];
  for (const b of backends) {
    const t0 = performance.now();
    try {
      await classify({ body: { messages: [{ role: "user", content: "Plan the migration of the auth service to OAuth2." }] },
        cfg: { ...cfg, systemOneChainBudgetMs: cfg.systemOneRemoteTimeoutMs, systemOneMinConfidence: 0 }, backends: [b], fetchImpl });
      results.push({ name: b.id, ok: true, ms: Math.round(performance.now() - t0) });
    } catch (e) {
      results.push({ name: b.id, ok: false, detail: e.detail ?? e.reason ?? e.message, ms: Math.round(performance.now() - t0) });
    }
  }
  return new Response(JSON.stringify({ probe: [...results, ...skipped] }), { status: 200, headers: { "content-type": "application/json" } });
}

export default createSmart({
  dataDir: process.env.OMNIROUTE_SMART_DATA_DIR || "/app/data/smart-routing",
  patchVersion: process.env.OMNIROUTE_SMART_PATCH_VERSION,
  batched: process.env.OMNIROUTE_SMART_SYSTEM_ONE_BATCHED !== "false",
});
```

`OMNIROUTE_SMART_SYSTEM_ONE_BATCHED` follows the P1 S1 batching decision. If S1 found that answers flip when batched, set it to `false` in the Dockerfile (`ENV`) in this task, and record the measured p95 in the commit message.

`OMNIROUTE_SMART_PATCH_VERSION` is set by the Dockerfile from the `PATCH_VERSION` build arg. Add `ENV OMNIROUTE_SMART_PATCH_VERSION=${PATCH_VERSION}` after `ARG PATCH_VERSION` in `docker/omniroute-smart/Dockerfile`.

Adapt the context calls to the names and argument shapes in the P1 report. If usage is exposed as `getUsageForProvider(connection)` rather than `getUsageFetcher(provider)`, change only `liveWindows`. Keep the stale-while-revalidate shape.

`order()` relies on the combo loop honouring `target.connectionId` (P1 S1 records whether targets carry and honour it). If P1 found they do not, keep one candidate per member (`connectionsFor` returns only the first active connection), drop the two-connection test, and record "same model on several connections: unsupported, scored on the first connection" as a deviation in Task 12.

- [ ] **Step 3: Run the tests, then commit**

Run: `node --test 'omniroute-smart/**/*.test.mjs' && bash tools/validate.sh`
Expected: PASS.

```bash
git add omniroute-smart/strategies docker/omniroute-smart/Dockerfile
git commit -m "feat(smart): strategy module wiring classification, ranking, effort, failures and decision log"
```

---

### Task 9: Overhead benchmark

**Files:**
- Create: `omniroute-smart/strategies/smart/bench.mjs`
- Modify: `docs/prds/omniroute-smart-routing-research/p3-results.md` (new)

**Interfaces:**
- Produces: `node omniroute-smart/strategies/smart/bench.mjs`, which prints `{p50_ms, p95_ms, n}` for prelude + order + observe with an instant fake System One. The PRD target is p95 < 5 ms.

- [ ] **Step 1: Implement**

Task 8's prelude already calls `(deps.classify ?? classify)(…)` (production passes nothing); check it is there. Reuse the module-test harness. Copy its `env()` into `bench.mjs`, pass `classify: async () => ({ backend: "laya", answers: Object.fromEntries(Object.entries({ task_kind: "execute_edit", domain: "backend", scope: "medium", complexity: "medium", second_opinion: 0.05 }).map(([k, v]) => [k, { value: v, confidence: 0.9, stage: "laya" }])) })` in its `createSmart` deps (the bench must run standalone, without `node:test`; change the fixture path to `new URL("./test/fixtures/settings.json", import.meta.url)` since `bench.mjs` sits one level up), with 10 members whose scorecard entries and quota-cache windows all exist. Then:

```js
// The PRD's 5 ms target excludes classification: the bench passes deps.classify, a fixed instant answer,
// so the timer covers config, candidates, ranking, effort, order and observe only.
const N = 2000, times = [];
for (let i = 0; i < 200; i++) await run(structuredClone(body)); // warm-up
for (let i = 0; i < N; i++) { const t0 = performance.now(); await run(structuredClone(body)); times.push(performance.now() - t0); }
times.sort((a, b) => a - b);
console.log(JSON.stringify({ n: N, p50_ms: times[N / 2], p95_ms: times[Math.floor(N * 0.95)] }));
process.exit(times[Math.floor(N * 0.95)] < 5 ? 0 : 1);
```

- [ ] **Step 2: Run, record and commit**

Run: `node omniroute-smart/strategies/smart/bench.mjs`
Expected: exit 0 and p95 < 5 ms. Record the JSON plus the P1 S1 Laya p95 in `p3-results.md`, section "Performance", and the Laya cold-start figure (first call after idle unload, ≈ +0.4 s per the PRD; take it from the System One benchmark results, `make system-one-benchmark`, or note it as not measured). `systemOneLayaTimeoutMs` (1500 ms) absorbs that reload.

If p95 ≥ 5 ms, profile with `node --cpu-prof` and fix the hot path, the likely site being `JSON.stringify` in `gateReasons`. Cache per request, not globally. Do not raise the target. The bench's fake `listProviderConnections` is in memory, so the real listing's cost is not in this number: the prelude lists once per request (module test). Record that exclusion in `p3-results.md` "Performance" with the result. shortcut: the 5 ms gate covers strategy work over an in-memory listing, so if the live chats in Task 11 feel slow, time `listProviderConnections` on it3 first.

```bash
git add omniroute-smart/strategies/smart/bench.mjs docs/prds/omniroute-smart-routing-research/p3-results.md
git commit -m "test(smart): strategy overhead benchmark"
```

---

### Task 10: Remote System One probes and health rows

**Files:**
- Modify: `scripts/smart-probe.sh`, `scripts/check-server.sh`, `tests/test_shell.py`

**Interfaces:**
- Consumes: `PROBE_MARKER` (Task 8), P2's `smart-status.mjs`, the local agents' **cached** API key `$(dirname "$CONFIG_PATH")/omniroute-api-key.json` (`{id, key}`, written by `llmenv omniroute issue-key` during `make check-server` / `make setup-local-llm-agents`). The probe only reads it; it never mints a key.
- Produces:
  - `make smart-probe` also prints PASS/FAIL per System One backend that is consented (plus SKIP lines for unconsented ones), through a probe request to the first combo whose strategy is `smart`;
  - check-server row `smart-routing System One reachable` (Laya PASS in the probe);
  - check-server row `smart-routing decisions match requests`, from `task-stats.json` only: for yesterday (UTC) `decisions == requests` exactly once yesterday has settled (37 min after UTC midnight, so its sweep (`SWEEP_AGE_MS` ≈ 35 min, long enough for the longest legal fusion run) and 10 s flush are done; before that `decisions ≤ requests`); for today `decisions ≤ requests` (the gap is in flight). A missing `task-stats.json` counts as zeros; an unreadable one FAILs.

- [ ] **Step 1: Write the failing shell tests**

```python
def test_smart_probe_asks_the_strategy_for_remote_backends(tmp_path: pathlib.Path) -> None:
    probe = '{"probe":[{"name":"laya","ok":true,"ms":12},{"name":"zen-free","ok":false,"detail":"HTTP 401","ms":40}]}'
    result, _, _ = run_lifecycle_script(tmp_path, "scripts/smart-probe.sh", smart_routing_enabled=True,
                                        env_overrides={"STUB_SMART_PROBE_CHAT": probe})
    assert "PASS system-one laya (12 ms)" in result.stdout
    assert "FAIL system-one zen-free: HTTP 401" in result.stdout
    assert "SKIP system-one typesafe: not consented" in result.stdout
    assert "fixture-cached-key" not in result.stdout + result.stderr
    assert result.returncode == 1


def test_smart_probe_without_a_cached_key_fails_and_never_mints_one(tmp_path: pathlib.Path) -> None:
    result, _, calls = run_lifecycle_script(tmp_path, "scripts/smart-probe.sh", smart_routing_enabled=True, api_key_cache=None,
                                            env_overrides={"STUB_SMART_PROBE_CHAT": "{}"})
    assert "FAIL system-one: no cached OmniRoute API key" in result.stdout
    calls_text = calls.read_text() if calls.exists() else ""  # the helper returns the calls log as a Path
    assert "issue-key" not in calls_text
    assert "chat/completions" not in calls_text
    assert result.returncode == 1
```

In the first test, add `{"name":"typesafe","skip":"not consented"}` to the `probe` JSON. Add an `api_key_cache: str | None = '{"id": "k1", "key": "fixture-cached-key"}'` parameter to `run_lifecycle_script`; when it is not `None`, write it to `config.parent / "omniroute-api-key.json"` (mode 0600) right after the config is written (the helper's own `config.parent.mkdir(parents=True)` runs first, so it cannot be pre-created by the test).

The probe chat is a `curl` to `http://127.0.0.1:<omniroute.port>/v1/chat/completions`, carrying the cached API key and the model set to a `smart` combo name. Stub `curl` to print `$STUB_SMART_PROBE_CHAT`, following how the harness stubs other HTTP calls (`rg -n "curl" tests/test_shell.py | head`). Also make the `podman` stub print `smart-execution` when `$*` contains `combos` (the combo lookup); otherwise the empty name sends the script down the skip branch.

- [ ] **Step 2: Implement**

Replace the last line of P2's `scripts/smart-probe.sh` (the bare `podman exec omniroute node /opt/omniroute-smart/probe/smart-probe.mjs`, which under `set -e` would exit before the System One rows) with the block below, which runs it once and keeps its exit status in `rc`; do not `exec`. Change its `require_cmd podman yq` to `require_cmd podman yq jq curl`.

```bash
rc=0
podman exec omniroute node /opt/omniroute-smart/probe/smart-probe.mjs || rc=1
port="$(yq -r '.omniroute.port' "$CONFIG_PATH")"
key_cache="$(dirname "$CONFIG_PATH")/omniroute-api-key.json"
combo="$(podman exec omniroute node -e "const {DatabaseSync}=require('node:sqlite');const d=new DatabaseSync('/app/data/storage.sqlite',{readOnly:true});const r=d.prepare(\"SELECT name FROM combos WHERE json_extract(data,'$.strategy')='smart' ORDER BY name LIMIT 1\").get();console.log(r?r.name:'')")"
if [ -z "$combo" ]; then
    echo "SKIP system-one: no combo with strategy smart, so the strategy probe did not run"   # stdout: check-server shows it
elif ! jq -e '.key | strings | length > 0' "$key_cache" >/dev/null 2>&1; then
    # read-only: minting a key is check-server's / agent setup's job, not a probe's
    echo "FAIL system-one: no cached OmniRoute API key (run make check-server or make setup-local-llm-agents first)"; rc=1
else
    body="$(jq -nc --arg m "$combo" '{model:$m,stream:false,messages:[{role:"user",content:"__smart_probe__"}]}')"
    auth_conf="$(mktemp)"; chmod 600 "$auth_conf"
    # the key goes from the cache file straight into the mode-600 curl config: never argv, a shell variable or output
    jq -r '"header = \"Authorization: Bearer \(.key)\""' "$key_cache" > "$auth_conf"
    out="$(curl -sf -K "$auth_conf" -H 'Content-Type: application/json' -d "$body" "http://127.0.0.1:${port}/v1/chat/completions" || echo '{}')"
    rm -f "$auth_conf"
    # jq formats the lines itself: an empty detail must not shift fields (read with IFS=tab collapses empty columns)
    jq -r '.probe[]? | if .skip then "SKIP system-one \(.name): \(.skip)" elif .ok then "PASS system-one \(.name) (\(.ms) ms)"
        else "FAIL system-one \(.name): \(.detail // "error") (\(.ms) ms)" end' <<<"$out"
    if ! jq -e '.probe' <<<"$out" >/dev/null; then echo "FAIL system-one: strategy probe did not answer"; rc=1
    elif jq -e 'any(.probe[]; (.skip | not) and (.ok != true))' <<<"$out" >/dev/null; then rc=1; fi
fi
exit "$rc"
```

The strategy lives in the `combos.data` JSON blob, not in a column (`strategies-fusion.md` §1; confirmed by a read-only schema read: `combos(id, name, data, sort_order, …)`, `data` keys `name, models, strategy, config, …`), so the query uses `json_extract(data,'$.strategy')`; `node:sqlite` ships JSON1. Task 11 checks it against it3 (row 1b). The API key goes only into the mode-600 curl config file (`-K`), never into argv or output.

`scripts/check-server.sh`, in the enabled branch of the smart section:

```bash
    probe_out="$(bash "${REPO_DIR}/scripts/smart-probe.sh" 2>/dev/null || true)"
    if grep -q '^PASS system-one laya (' <<<"$probe_out"; then ok "Verdict: PASS identity=smart-routing System One reachable"
    else bad "Verdict: FAIL identity=smart-routing System One reachable ($(grep -m1 -E 'system-one( laya|:)' <<<"$probe_out" || echo 'no probe answer'))"; fi
    # task-stats counts a decision on the day its request began; yesterday must match exactly once settled (00:37 UTC, see SWEEP_AGE_MS)
    counts="$(podman exec omniroute node -e '
const f="/app/data/smart-routing/task-stats.json", fs=require("fs");
const s=fs.existsSync(f)?JSON.parse(fs.readFileSync(f,"utf8")):{days:{}};
const now=Date.now(), day=(ago)=>new Date(now-ago).toISOString().slice(0,10);
const sum=(d,k)=>Object.values(s.days[d]||{}).reduce((a,c)=>a+(c[k]||0),0);
const y=day(86400e3), t=day(0), settled=now%86400e3>=2220e3?1:0;
console.log([sum(y,"requests"),sum(y,"decisions"),sum(t,"requests"),sum(t,"decisions"),settled].join(" "));' 2>/dev/null || echo x)"
    read -r yreq ydec treq tdec settled <<<"$counts"
    if [ "$counts" = x ]; then bad "Verdict: FAIL identity=smart-routing decisions match requests (task-stats.json unreadable)"
    elif { [ "$ydec" -eq "$yreq" ] || { [ "$settled" = 0 ] && [ "$ydec" -le "$yreq" ]; }; } && [ "$tdec" -le "$treq" ]; then
        ok "Verdict: PASS identity=smart-routing decisions match requests (yesterday ${ydec}/${yreq}$([ "$settled" = 0 ] && echo ' settling'), today ${tdec}/${treq})"
    else bad "Verdict: FAIL identity=smart-routing decisions match requests (yesterday ${ydec}/${yreq}, today ${tdec}/${treq} decisions/requests)"; fi
```

These rows run `scripts/smart-probe.sh` as a subprocess, so wire `run_check_server` the same way as `run_lifecycle_script` above: write the cached key to `omniroute-api-key.json` next to its config, make its `podman` stub print `smart-execution` when `$*` contains `combos` and exit 0 for the in-container probe exec, and make its `curl` stub print `$STUB_SMART_PROBE_CHAT` for the chat call. Its stub exits 1 for unmatched calls, which would send the probe down the skip branch.

Add a shell test for each row's PASS and FAIL, using `STUB_SMART_PROBE_CHAT` and a stubbed `podman exec omniroute node -e …` that prints `"5 5 3 2 1"` (PASS), `"9 2 3 2 1"` (FAIL: yesterday short), `"9 8 3 2 0"` (PASS: yesterday still settling), `"5 5 2 3 1"` (FAIL: more decisions than requests today) or fails (FAIL: unreadable).

- [ ] **Step 3: Run the tests, then commit**

Run: `uv run pytest tests/test_shell.py -k smart -v && bash tools/validate.sh`
Expected: PASS.

```bash
git add scripts/smart-probe.sh scripts/check-server.sh tests/test_shell.py
git commit -m "feat(smart-routing): remote System One probes via the strategy; reachability and decision-count health rows"
```

---

### Task 11: Throwaway-container integration

This is an execution task run by one subagent. Results go into `docs/prds/omniroute-smart-routing-research/p3-results.md`, section "Integration". A defect gets fixed in the owning task's files, with a new unit test first.

**Interfaces:**
- Consumes: the derived image rebuilt with `smart.mjs`, and P1's mock upstream and probe helpers.
- Produces: PASS/FAIL rows for the acceptance criteria this plan owns.

- [ ] **Step 0: Mock controls (code + unit test, before any container)**

P1's `mock-upstream.mjs` exports `startMock(port)` and has no runtime switches. Extend it to `startMock(port, opts = {})`, with each control read once at start from the environment unless `opts` overrides it (a test passes `opts`; the integration run restarts the mock with a different environment):
- `systemOne` (`MOCK_SYSTEM_ONE`): `"down"` makes `POST /v1/systemone` answer 503 without `Retry-After`; otherwise it answers `{answers}` with confident `task_kind: execute_edit`, `domain: backend`, `complexity: medium` (index 1), `second_opinion: 0.05`;
- `scope` (`MOCK_SYSTEM_ONE_SCOPE`, default `1`): the `scope` answer's argmax index (0 small, 1 medium, 2 large), probability 0.9;
- `okText` (`MOCK_OK_TEXT`): when set, `m-ok` answers 200 with that text as its content instead of `"ok"`.

Append to P1's `omniroute-smart/spike/test/mock-upstream.test.mjs` (only the `test()` block; its imports exist):

```js
test("System One route and the three controls", async (t) => {
  const up = await startMock(0, { scope: 2, okText: "FORBIDDEN-X" });
  const down = await startMock(0, { systemOne: "down" });
  t.after(() => { up.close(); down.close(); });
  const at = (s, path, body) => fetch(`http://127.0.0.1:${s.address().port}${path}`, { method: "POST", body: JSON.stringify(body) });
  const a = await (await at(up, "/v1/systemone", { questions: {} })).json();
  assert.equal(Object.entries(a.answers.scope.probabilities).sort((x, y) => y[1] - x[1])[0][0], "2");
  assert.equal(a.answers.task_kind.choice, "execute_edit");
  assert.equal((await at(down, "/v1/systemone", {})).status, 503);
  const chat = await (await at(up, "/v1/chat/completions", { model: "m-ok", messages: [] })).json();
  assert.equal(chat.choices[0].message.content, "FORBIDDEN-X");
});
```

Run `node --test 'omniroute-smart/spike/test/*.test.mjs'` (PASS) and commit with Step 3's commit. In the integration shell, start both mocks with tracked PIDs and restart them only through these helpers (cleanup kills only `$UP` and `$S1`, never by pattern):

Run all of Task 11 in one shell session so these variables and helpers persist:

```bash
IT3="$(mktemp -d)"; chmod 700 "$IT3"   # also holds the temp config and env file; removed by the cleanup
UP=""; S1=""
cleanup() { podman rm -f omniroute-smart-it3 >/dev/null 2>&1; podman volume rm -f omniroute-smart-it3-data >/dev/null 2>&1
            for p in $UP $S1; do kill "$p" 2>/dev/null; wait "$p" 2>/dev/null; done; rm -rf "$IT3"; }   # only our own PIDs and it3 names
trap cleanup EXIT
node omniroute-smart/spike/mock-upstream.mjs --port 18080 >> "$IT3/mock-up.log" 2>&1 & UP=$!
node omniroute-smart/spike/mock-upstream.mjs --port 18081 >> "$IT3/mock-s1.log" 2>&1 & S1=$!
restart_up() { kill "$UP" 2>/dev/null; wait "$UP" 2>/dev/null; env "$@" node omniroute-smart/spike/mock-upstream.mjs --port 18080 >> "$IT3/mock-up.log" 2>&1 & UP=$!; }
restart_s1() { kill "$S1" 2>/dev/null; wait "$S1" 2>/dev/null; env "$@" node omniroute-smart/spike/mock-upstream.mjs --port 18081 >> "$IT3/mock-s1.log" 2>&1 & S1=$!; }
```

Row 3 runs after `restart_s1 MOCK_SYSTEM_ONE=down` and ends with `restart_s1`; row 6 uses `restart_s1 MOCK_SYSTEM_ONE_SCOPE=2` then `restart_s1`; row 7 uses `restart_up MOCK_OK_TEXT='<the copied rule text>'` then `restart_up`.

- [ ] **Step 1: Rebuild and start (names `-it3`, port 29131), in the Step 0 shell**

Only temporary configuration and generated credentials are used: the config copy drops `server.api_key` and gets a generated dashboard password, `~/.config/llm-env/omniroute-smart.env` is never read, and nothing under `~/.config` is written.

```bash
PROD_STARTED_BEFORE="$(podman inspect --format '{{.State.StartedAt}}' omniroute)"
IT_PW="$(openssl rand -hex 16)"; export IT_PW
TMPCFG="$IT3/models.yml"
yq '.smart_routing.enabled = true | .smart_routing.image = "localhost/llm-env-omniroute-smart:it3"
    | .omniroute.port = 29131 | .omniroute.initial_password = strenv(IT_PW) | del(.server.api_key)' ~/.config/llm-env/models.yml > "$TMPCFG"
chmod 600 "$TMPCFG"
LLM_ENV_CONFIG="$TMPCFG" bash scripts/omniroute-smart-build.sh
printf 'INITIAL_PASSWORD=%s\n' "$IT_PW" > "$IT3/it3.env"; chmod 600 "$IT3/it3.env"
podman run -d --name omniroute-smart-it3 -p 127.0.0.1:29131:20128 -e PORT=20128 -e OMNIROUTE_PLUGINS_DIR=/app/data/plugins \
  -e OMNIROUTE_ALLOW_PRIVATE_PROVIDER_URLS=true --env-file "$IT3/it3.env" -v omniroute-smart-it3-data:/app/data \
  localhost/llm-env-omniroute-smart:it3 >/dev/null
P() { PORT=29131 SPIKE_PASSWORD="$IT_PW" node omniroute-smart/spike/probe.mjs "$@"; }   # P1 Task 9 probe, pointed at it3
for i in $(seq 60); do P list-providers >/dev/null 2>&1 && break; sleep 2; done
[ "$i" -lt 60 ] || P list-providers >/dev/null 2>&1 || { podman logs --tail 100 omniroute-smart-it3; echo "omniroute-smart-it3 not up after 120 s" >&2; exit 1; }
```

The `llmenv` calls below take `--config "$TMPCFG"`, so they talk to it3 (port 29131, the generated password), never to production.

1. (done above) the derived image, rebuilt from the temporary config.
2. (Step 0) the mock upstream on 18080 and the System One mock on 18081.
3. (done above) the volume and container with the generated `INITIAL_PASSWORD` and no real keys.
4. Create two llama-cpp provider connections to `http://host.containers.internal:18080/v1` with `P`, using the P1 Task 10 Step 2 payload.
5. Push a `settings.json` (`uv run llmenv.py --config "$TMPCFG" smart-settings`, edited with `jq`, piped into `podman exec -i omniroute-smart-it3 sh -c 'mkdir -p /app/data/smart-routing && umask 077 && cat > /app/data/smart-routing/settings.json'`) whose `plans` include one declared plan for `llama-cpp` with `manualRemainingPct: 50`, `cycle: "monthly@1"`.
6. Push a hand-written `scorecard.json` with `generated_at` = now and `Q` entries for `llama-cpp/m-ok`, `llama-cpp/m-fail` and `llama-cpp/m-slow`.
7. Point the plugin config `systemOneLayaUrl` at the mock System One on the host, `http://host.containers.internal:18081` (the container reaches host mocks only through that name, as P1's provider connections do), using P1's `probe.mjs put-plugin-config` with the method S5 recorded. Start a second mock on port 18081 for System One (Step 0 adds the route and the controls).
8. Install the plugin (`uv run llmenv.py --config "$TMPCFG" omniroute smart-plugin`, P2's `ensure_smart_plugin`) and set `bumpMode: off` with `P put-plugin-config`. Activation starts a sync that rewrites `scorecard.json`: wait until it exists (at most 3 min), then push the hand-written scorecard from item 6 again, so the scenarios run on the known scores.

- [ ] **Step 2: Run the scenarios**

Record each outcome in a table:

| # | Scenario | Expected |
|---|---|---|
| 1 | Combo `smart-it` with members `[llama-cpp/m-fail, llama-cpp/m-ok]` and `strategy: "smart"` | 201 |
| 1b | `podman exec omniroute-smart-it3 node -e` with the exact `smart-probe.sh` combo query | prints `smart-it` |
| 2 | Chat → mock log | `m-ok` is the target on the second attempt, or first if ranked first; client 200; one `decisions.jsonl` line with `attempts` |
| 2b | The row 2 decision line's `ranked` | two candidates per member with distinct `connId` (the two item-4 connections): the real connection listing has the `id`, `provider`, `priority`, `isActive` fields `connectionsFor` reads. If not, fix the field names in `connectionsFor`/`keys()` and record them |
| 3 | `MOCK_SYSTEM_ONE=down` and no remote consent | 503 `system_one_unavailable`, no mock chat request |
| 4 | Scorecard `generated_at` set 13 h back | 503 `scorecard_missing` |
| 5 | Combo `config.qualityFloorDelta: 5` | 503 `config_invalid` naming the key |
| 6 | Declared plan `manualRemainingPct: 4` (h = 4 − 3 reserve = 1: passes small 1, fails medium 3 and large 6) and a `large`-scope answer (mock System One returns scope 2) | 503 `no_candidate_fits` listing both members with `headroom` |
| 7 | Forbidden-substring / HTTP-200 error-text rule: write one forbidden-substring rule on `smart-it` in the shape P1 S3 recorded (matching the text `mock:m-ok`; never copy from production), and make `m-ok` answer with that text via `MOCK_OK_TEXT` | OmniRoute falls back exactly as on a `priority` combo |
| 8 | Probe marker chat | 200 with `probe` JSON; laya `ok:true` |

- [ ] **Step 3: Clean up, verify production is untouched, commit**

Run `cleanup; trap - EXIT` (the Step 0 trap also runs it on any failure). Expected: `podman ps -a --filter name=omniroute-smart-it3 -q | wc -l` prints 0, no volume `omniroute-smart-it3-data`, `$IT3` is gone, and the production `StartedAt` is unchanged.

```bash
git add omniroute-smart docs/prds/omniroute-smart-routing-research/p3-results.md
git commit -m "test(smart): throwaway-container integration of the smart strategy"
```

---

### Task 12: PRD alignment and final verification

- [ ] **Step 1: Record in the PRD (v2.1)**
  - **Request-time ranking:** members are ranked in the prelude and `order()` applies that ranking. Name the per-request store (AsyncLocalStorage).
  - **Effort:** set as `reasoning_effort` on the request body, from the top-ranked member's options.
  - **Failures:** the 5-minute sweep for loops that never report success.
  - **System One:** `OMNIROUTE_SMART_SYSTEM_ONE_BATCHED` and the S1 decision.
  - **Probes:** remote System One probes go through the strategy's `__smart_probe__` marker.
  - **429 handling:** a 429 gates that candidate's connection (a candidate is one member on one connection) until the named window's reset, else its shortest window's reset, else `failureBackoffMaxMinutes`.
  - **System One retries:** a 503 without `Retry-After` is not retried; the chain moves to the next backend.
  - **Tuning scope:** `failureAlpha`, `failureBackoff*`, `burnAlpha` and `decisionLog*` are taken from the first request's effective config (recorded `shortcut:` in `smart.mjs`).
  - **Same model on several connections:** an unpinned member is scored once per active connection and `order()` pins each target to its connection (or, if P1 showed targets do not honour `connectionId`, the deviation recorded in Task 8).
  - **Quota sources:** the in-process quota cache (`QuotaCacheEntry`), else the provider quota fetcher, else its usage fetcher (stale-while-revalidate), else the plan declaration.
  - **Decision-count health row (deviation):** the PRD's "decisions.jsonl lines match requests" check reads `task-stats.json` `decisions` (counted where the line is written, on the day the request began) instead of counting log lines, since rotation drops old lines. Yesterday exact once settled (00:37 UTC: the sweep waits out the longest legal fusion run); today `decisions ≤ requests`.
  - **Probe key:** `make smart-probe` reads only the cached agent key; it never mints one.
  - **Decision lines for failures:** every request that reaches the prelude after the first successful init gets a `lane: "none"` line with its 503 reason; a failure before the first init (fresh process) is not logged.

  Add a clarification row: `16 | P3 implementation: …`.

- [ ] **Step 2: Full verification and commit**

Run: `bash tools/validate.sh && bash tools/test.sh && node omniroute-smart/strategies/smart/bench.mjs`
Expected: everything passes.

```bash
git add docs/prds
git commit -m "docs(prd): record P3 smart strategy implementation details"
```

---

## Self-Review Notes (author)

**Spec coverage (PRD, P3 scope):**

| PRD item | Task |
|---|---|
| System One client and chain order, consent, keys (Zen from connection, TypeSafe from env), timeouts, chain budget, error table, retries | 3 |
| Confidence by type, low-confidence escalation, arbiter (via `handleSingleModel`, timeout, JSON, a smart combo rejected as arbiter), conservative defaults, stage per answer | 3, 2 |
| State building and batching (and the parallel fallback) | P1 `buildState` + 3 + 8 |
| Quota sources: quota cache, usage fetchers, plan declarations (manual %, `renews`/`expiresAt`, `cycle: none`) | 4, 8 |
| h, T, b, scarcity, forfeit, S, W (longest window) | 4 |
| Gates (context/tools/modality, breaker, lock, cooldown, headroom per scope, quality floor, expiry) | 6, 8 |
| Classes and utility, metered price term | 6 |
| Failures (F, cooldown, 429 exhaustion, 4xx ignored) | 5 |
| Effort | 6, 8 |
| Error reasons and the 503 body | 2, 8, 11 |
| `decisions.jsonl` (one line per request, rotation) and `task-stats.json` | 7 |
| Performance | 9 |
| Probes and health rows | 10 |
| Acceptance tests (owner's case, C/D, expiry, headroom, n=1, max W=0, cooldown, metered last, all-gated 503 with no upstream call) | 4, 6, 8, 11 |

**Left to P4:**
- the fusion lane (`decide`);
- the `fusion_degraded` and `fusion_failed` counters (the fields exist);
- the judge gates;
- the operator guide.

**Type consistency:**
- `store.smart` fields are listed once under Interfaces and used in Tasks 7, 8 and 10.
- Window and `terms` shapes are defined in Task 4 and consumed in Task 8.
- `Cand` is defined in Task 6 and built in Task 8.
