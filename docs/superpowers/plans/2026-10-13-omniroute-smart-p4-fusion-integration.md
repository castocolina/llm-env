# OmniRoute Smart Routing P4: Fusion Lane, Live Canary Tests and Operator Guide Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn P3's recorded `fusion_eligible` into a real fusion lane, prove the whole stack end to end on throwaway canary containers, and write the operator guide. Concretely:
- a fusion-eligible request runs a two-member panel plus a judge through OmniRoute's injected `handleFusionChat`;
- the judge is gated like any member and is bounded by `fusionJudgeTimeoutMs`;
- a degraded panel is logged as `fusion_degraded`, and a panel with no survivor gets one single retry before `503 fusion_failed`;
- an outer deadline makes a hung fusion call impossible;
- the PRD's Phase 3 live tests run on canary containers and land in a test report;
- `docs/omniroute-smart-routing.md` tells the operator how to run, read, upgrade and roll back the feature.

**Architecture:**
- **Fusion runs inside the prelude.** When `decide()` picks the fusion lane, the prelude calls `ctx.handleFusionChat` itself and returns its `Response`. OmniRoute's combo loop never runs for that request, so `order()` and `observe()` are not involved.
- **One wrapper sees every upstream call.** The strategy passes `handleFusionChat` a wrapped `handleSingleModel`. The wrapper records each attempt (decision log and failure EWMA), tells panel calls from the judge call by call order, applies the judge timeout, and keeps a clone of the first good panel answer for the degraded paths.
- **Pure helpers, thin wiring.** `smart/fusion.mjs` holds panel choice and the fusion runner, with unit tests against a fake `handleFusionChat`. `smart.mjs` only wires them into the prelude and gates the judge.
- **Live tests use test-only overrides.** Two env switches let a canary container point remote System One backends and the models.dev fetch at local mocks. Both are honoured only with an explicit test-mode flag, and `make check-server` fails if either flag is set in production.

**Tech Stack:** Node 26 in the image, Node 24 on the host. Plain ES modules with JSDoc, built-ins only (`node:test`, `fetch`, `Response`). Bash and pytest for the check-server rows. Podman for the canary containers.

**Spec:** `docs/prds/omniroute-smart-routing-v2.1-prd.md` ("Single vs fusion", "Data requirements" → `decisions.jsonl` and `task-stats.json`, "Acceptance Criteria", "Execution Phases → Phase 3"). Also read:
- `docs/prds/omniroute-smart-routing-research/spike-report-p1.md`, section S3: the argument shape of `handleFusionChat`, which path the one-survivor case takes, whether `stream: true` is supported, whether panel calls also pass through patch site 5, and the S1 step 11 result (missing strategy module);
- `docs/prds/omniroute-smart-routing-research/p3-results.md`;
- `docs/prds/omniroute-smart-routing-research/strategies-fusion.md` §4.

The spike report wins over this plan where they disagree.

## Preconditions

- P1 ended `VERDICT: GO`, and its "Impact on P2–P4" edits for P4 have been applied here.
- P2 and P3 are merged; `node --test 'omniroute-smart/**/*.test.mjs'` and `bash tools/test.sh` pass on `main`.
- P1 S3 recorded a working `handleFusionChat` call from the strategy. If S3 failed, stop and report: the fusion lane has no delivery route.

## Global Constraints

- **No production changes.** Never stop, restart, exec writes into or reconfigure the production `omniroute` container, and never edit `~/.config/llm-env/*`. Reading `~/.config/llm-env/models.yml` into a temporary copy is allowed; reading `omniroute-smart.env` is not needed in P4.
- **Throwaway containers.** Canary runs use `omniroute-smart-it4*` on `127.0.0.1:29132`, with volume `omniroute-smart-it4-data`. Mocks listen on 18080 (upstream), 18081 (Laya-shaped System One) and 18082 (remote-shaped System One). Every container, volume and mock process is removed at the end of its task, pass or fail.
- **Fail closed.** Every failure answers `503` with `{"error":{"type":"smart_routing_unavailable","reason":"…","detail":"…","request_id":"…"}}`. A degraded fusion is allowed only when it is logged (`lane: "fusion_degraded"`) and counted in `task-stats.json`.
- **Test overrides stay test-only.** `OMNIROUTE_SMART_TEST_MODE` and `OMNIROUTE_PLUGIN_SMART_CATALOG_TEST_MODE` are never written by `pylib/compose.py`, never set in `docker/omniroute-smart/Dockerfile`, and never documented as an operator setting.
- **Secrets.** No key appears in logs, `decisions.jsonl`, test reports or mock logs. Mocks log whether an `authorization` header was present, never its value.
- **Honest User-Agent.** Unchanged from P3: `llm-env-omniroute-smart/<PATCH_VERSION>`.
- **Lanes are not hardcoded.** Panel, judge and retry choices come from the ranking and combo config. No model id is written into code outside tests.
- **Commits and style.** Commit after each task and never push. Ponytail rules: reuse P3's `rank`, `candidates`, `errorResponse`, log and failure helpers; no new dependency.

## Review Focus

1. **A fusion call that never settles** (an upstream panel member or judge that hangs, or an upstream fusion bug) must still answer the client within `panelHardTimeoutMs + fusionJudgeTimeoutMs + 5 s`, plus at most `panelHardTimeoutMs` for the zero-survivor retry. Pinned in Task 2 (fake hang) and Task 6 (live `m-hang`).
2. **A configured `judgeModel` whose provider breaker is OPEN, or whose quota window is gated,** must not judge. The panel's first member judges instead, and the log says why. Pinned in Task 3.
3. **A tool-bearing analysis request** (`tools` non-empty, `tool_choice` not `"none"`) must run single even when every other fusion condition holds. Pinned in Task 3 and Task 6.
4. **A judge that fails after two good panel answers** must not turn into a client error. The first good panel answer goes back, logged as `fusion_degraded` with `judge_failed: true`. Pinned in Task 2.
5. **The test-override env vars leaking into production** must turn `make check-server` red. Pinned in Task 4.

---

## File Structure

| Path | Responsibility |
|---|---|
| `omniroute-smart/strategies/smart/fusion.mjs` | `pickPanel`, `runFusion` (wrapper, judge timeout, outer deadline, lanes, zero-survivor retry) |
| `omniroute-smart/strategies/smart/test/fusion.test.mjs` | Unit tests against a fake `handleFusionChat` |
| `omniroute-smart/strategies/smart.mjs` | `decide()` picks the lane; judge gating; `recordAttempt` shared by `observe` and fusion |
| `omniroute-smart/strategies/smart/test/fusion-module.test.mjs` | Module tests of the fusion lane through `prelude` |
| `omniroute-smart/strategies/smart/systemone.mjs` | `backendsFor(…, testRemoteUrl)` |
| `omniroute-smart/plugins/smart-catalog/lib/sources.mjs`, `…/sync.mjs` | `fetchModelsDev(fetchImpl, url)` test override |
| `omniroute-smart/spike/mock-upstream.mjs` (+ test) | `*hang*` models, System One answer profiles, remote mode, models.dev route |
| `scripts/check-server.sh`, `tests/test_shell.py` | Rows: fusion degraded in the last 24 h; test overrides are off |
| `docs/omniroute-smart-routing.md` | Operator guide |
| `docs/prds/omniroute-smart-routing-research/p4-test-report.md` | Live canary results |

**Interfaces this plan produces for P5:**
- `outcome.lane ∈ {"single", "fusion", "fusion_degraded", "fusion_failed", "none"}` in every `decisions.jsonl` line (`"none"` exists only in the log, for prelude 503s), and `outcome.fusion` by lane: `fusion` / `fusion_degraded` / `fusion_failed` lines carry `{panel, judge, judge_source, survivors, judge_failed, retry, timed_out, threw}`; a `single` line carries `{skip}` only (`skip` ∈ `not_eligible`, `no_fusion_handler`, `rules_unavailable`, `stream`); `none` lines carry none.
- check-server rows `smart-routing fusion degraded (24 h)` and `smart-routing test overrides are off`.
- The operator guide at `docs/omniroute-smart-routing.md`, which P5 links from `README.md`.

---

### Task 1: Test-only overrides for remote System One and models.dev

**Files:**
- Modify: `omniroute-smart/strategies/smart/systemone.mjs`, `omniroute-smart/strategies/smart.mjs`
- Modify: `omniroute-smart/plugins/smart-catalog/lib/sources.mjs`, `omniroute-smart/plugins/smart-catalog/lib/sync.mjs`
- Test: `omniroute-smart/strategies/smart/test/systemone.test.mjs`, `omniroute-smart/plugins/smart-catalog/test/sources.test.mjs`

**Interfaces:**
- Consumes: P3 `backendsFor(pluginConfig, keys, userAgent)`; P2 `fetchModelsDev(fetchImpl)` and `runSync({…, env})`.
- Produces:
  - `backendsFor(pluginConfig, keys, userAgent, testRemoteUrl?)`: when `testRemoteUrl` is a string, every consented remote keeps its id, model, consent and key rules but uses that URL;
  - `fetchModelsDev(fetchImpl, url = MODELS_DEV_URL)`;
  - the strategy passes `testRemoteUrl` only when `env.OMNIROUTE_SMART_TEST_MODE === "1"` (value from `env.OMNIROUTE_SMART_TEST_REMOTE_URL`);
  - `runSync` passes the models.dev URL only when `env.OMNIROUTE_PLUGIN_SMART_CATALOG_TEST_MODE === "1"` (value from `env.OMNIROUTE_PLUGIN_SMART_CATALOG_TEST_MODELSDEV_URL`). The plugin child only sees `OMNIROUTE_PLUGIN_SMART_CATALOG_*`, so both names carry that prefix.

- [ ] **Step 1: Write the failing tests**

Append to `omniroute-smart/strategies/smart/test/systemone.test.mjs`:

```js
test("testRemoteUrl redirects consented remotes only, keeping consent and key rules", () => {
  const plugin = { systemOneLayaUrl: "http://laya:8000", consentTypeSafe: true, consentZenFree: false };
  const b = backendsFor(plugin, { typesafe: "k", zen: "z" }, "ua", "http://mock:18082/v1/systemone");
  assert.deepEqual(b.map((x) => [x.id, x.url]), [["laya", "http://laya:8000/v1/systemone"], ["typesafe", "http://mock:18082/v1/systemone"]]);
  assert.equal(backendsFor(plugin, { typesafe: "k" }, "ua").find((x) => x.id === "typesafe").url, "https://api.typesafe.ai/v1/systemone");
});
```

Append to `omniroute-smart/plugins/smart-catalog/test/sources.test.mjs`, and add `fetchModelsDev` to its import from `../lib/sources.mjs`:

```js
test("fetchModelsDev uses the URL it is given", async () => {
  const seen = [];
  await fetchModelsDev(async (url) => { seen.push(url); return new Response("{}"); }, "http://mock:18080/api.json");
  await fetchModelsDev(async (url) => { seen.push(url); return new Response("{}"); });
  assert.deepEqual(seen, ["http://mock:18080/api.json", "https://models.dev/api.json"]);
});
```

Append to `omniroute-smart/strategies/smart/test/module.test.mjs` (it already imports `AsyncLocalStorage`, `createSmart` and the fs/path helpers, and defines `NOW` and `body`):

```js
for (const [mode, expectHit] of [[undefined, false], ["1", true]]) {
  test(`remote URL override ${expectHit ? "applies" : "is ignored"} when OMNIROUTE_SMART_TEST_MODE=${mode}`, async () => {
    const dataDir = mkdtempSync(join(tmpdir(), "smart-override-"));
    writeFileSync(join(dataDir, "settings.json"), readFileSync(new URL("./fixtures/settings.json", import.meta.url)));
    writeFileSync(join(dataDir, "scorecard.json"), JSON.stringify({ generated_at: new Date(NOW).toISOString(), entries: {} }));
    const als = new AsyncLocalStorage();
    globalThis.__omniSmart = { als, ctx: {} };
    const fetched = [];
    const ctx = { getPluginConfig: async () => ({ config: { systemOneLayaUrl: "http://system-one:8000", consentTypeSafe: true } }),
      listProviderConnections: async () => [] };
    const env = { TYPESAFE_API_KEY: "k", OMNIROUTE_SMART_TEST_REMOTE_URL: "http://x/v1/systemone", ...(mode ? { OMNIROUTE_SMART_TEST_MODE: mode } : {}) };
    const smart = createSmart({ dataDir, ctx, env, now: () => NOW, flushMs: 0,
      fetchImpl: async (url) => { fetched.push(url); throw new TypeError("fetch failed"); } });
    const combo = { name: "c", strategy: "smart", models: ["p/a"], config: {} };
    const pre = await als.run({}, () => smart.prelude({ strategy: "smart", body: structuredClone(body), combo, config: {}, store: als.getStore() }));
    assert.equal((await pre.response.json()).error.reason, "system_one_unavailable");
    assert.equal(fetched.includes("http://x/v1/systemone"), expectHit);
  });
}
```

- [ ] **Step 2: Run them and see them fail**

Run: `node --test 'omniroute-smart/**/*.test.mjs'`
Expected: FAIL on three of the four new tests (one in `systemone.test.mjs`, one in `sources.test.mjs`, and the `module.test.mjs` loop's `=1` case). The loop's `=undefined` case already passes: it pins the ignored case, which P3's code already satisfies.

- [ ] **Step 3: Implement**

In `systemone.mjs`:

```js
export function backendsFor(pluginConfig, keys, userAgent = "llm-env-omniroute-smart", testRemoteUrl) {
  const laya = { id: "laya", url: `${pluginConfig.systemOneLayaUrl.replace(/\/$/, "")}/v1/systemone`, model: "jev-1", headers: { "user-agent": userAgent } };
  const remotes = REMOTES.filter((r) => pluginConfig[r.consent] === true && keys[r.key])
    .map((r) => ({ id: r.id, url: testRemoteUrl ?? r.url, model: r.model, headers: { "user-agent": userAgent, authorization: `Bearer ${keys[r.key]}` } }));
  return [laya, ...remotes];
}
```

In `smart.mjs`, inside `prelude`, replace the `backendsFor(...)` call:

```js
        const env = deps.env ?? process.env; // the default export passes no env, so a container reads process.env (tests pass deps.env)
        // Test-only: canary containers point remote backends at a local mock (P4 Task 6). check-server fails if set in production.
        const testRemoteUrl = env.OMNIROUTE_SMART_TEST_MODE === "1" ? env.OMNIROUTE_SMART_TEST_REMOTE_URL : undefined;
        const backends = backendsFor(plugin, await keys(conns), `llm-env-omniroute-smart/${deps.patchVersion ?? "dev"}`, testRemoteUrl);
```

In `sources.mjs`: `export async function fetchModelsDev(fetchImpl = fetch, url = MODELS_DEV_URL)`, using `url` in the fetch.

In `sync.mjs`, before the models.dev fetch:

```js
    const mdUrl = env.OMNIROUTE_PLUGIN_SMART_CATALOG_TEST_MODE === "1" ? env.OMNIROUTE_PLUGIN_SMART_CATALOG_TEST_MODELSDEV_URL : undefined;
    const md = await cached("modelsdev", cacheDir, () => fetchModelsDev(fetchImpl, mdUrl), now);
```

`fetchModelsDev(fetchImpl, undefined)` falls back to the default parameter, so production is unchanged.

- [ ] **Step 4: Run the tests and commit**

Run: `node --test 'omniroute-smart/**/*.test.mjs'`
Expected: PASS.

```bash
git add omniroute-smart/strategies omniroute-smart/plugins
git commit -m "feat(smart): test-mode-only overrides for remote System One and models.dev URLs"
```

---

### Task 2: The fusion runner (`smart/fusion.mjs`)

**Files:**
- Create: `omniroute-smart/strategies/smart/fusion.mjs`
- Test: `omniroute-smart/strategies/smart/test/fusion.test.mjs`

**Interfaces:**
- Consumes: `errorResponse(reason, detail, requestId)` from P3 `smart/errors.mjs`; the ranked candidate shape `{id, connId, cls, U, terms}` from P3 `rank`.
- Produces:
  - `pickPanel(ranked) → [a, b]` (candidates, each keeping its `connId`): the top candidate, then the best-ranked one from a different provider (the text before the first `/`), else the best-ranked different model, else the second-ranked (the same model on another connection);
  - `runFusion(opts) → Promise<{response, lane, fusion}>` with
    ```js
    opts = { handleFusionChat, handleSingleModel, body, ranked, panel: [{id, connId}], judge, judgeConn, cfg, requestId, comboName, log,
             record, now = Date.now, guardMarginMs = 5000, fusionExtras = {}, checkResponse = null }
    // checkResponse(res) → {fallback, reason?}: OmniRoute's own combo-rule check (P1 ctx.checkComboResponse bound to the combo),
    // or null when handleSingleModel already applies the rules. A 2xx answer it flags counts as a failed call (502).
    // record({target, connectionId, status, latency_ms, role}) is called once per upstream attempt (role: panel | judge | retry),
    // always before runFusion resolves: every panel call is bounded by panelHardTimeoutMs, the judge by fusionJudgeTimeoutMs.
    // Every call carries its candidate's connection as handleSingleModel's target {connectionId} (P3 scored that connection).
    // handleFusionChat itself gets plain model strings (`models: panel.map(c => c.id)`), the shape the research names;
    // the connection is added by the wrapper, which maps the i-th panel call back to its seat.
    fusion = { panel: [ids], judge, survivors, judge_failed, retry: id|null, timed_out, threw }
    ```
  - `lane` is `"fusion"` (two survivors and a good judged answer), `"fusion_degraded"` (one survivor, a failed judge, an outer timeout with a survivor, or a successful zero-survivor retry), or `"fusion_failed"` (503).

How the wrapper tells calls apart: `handleFusionChat` starts every panel call before it calls the judge, so the first `panel.length` calls are the panel and any later call is the judge. If the P1 S3 report recorded an explicit judge marker in the call arguments (for example a `target.role`), use that instead and keep this test suite unchanged.

- [ ] **Step 1: Write the failing tests**

```js
// omniroute-smart/strategies/smart/test/fusion.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { pickPanel, runFusion } from "../fusion.mjs";

const json = (status, body) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
const answer = (m) => json(200, { choices: [{ message: { role: "assistant", content: `from ${m}` } }] });
const never = () => new Promise(() => {});

/** Upstream-like fusion: panel in parallel; 0 ok → 503; 1 ok → that answer; 2 ok → judge (502 if the judge fails). */
function fakeFusion({ hang = false } = {}) {
  return async ({ body, models, handleSingleModel, judgeModel }) => {
    if (hang) return never();
    const res = await Promise.all(models.map((m) => handleSingleModel(body, m)));
    const ok = res.filter((r) => r.ok);
    if (!ok.length) return json(503, { error: { message: "all panel members failed" } });
    if (ok.length === 1) return ok[0];
    const j = await handleSingleModel({ messages: [{ role: "user", content: "judge these" }] }, judgeModel);
    return j.ok ? j : json(502, { error: { message: "judge failed" } });
  };
}
const single = ({ fail = [], hang = [] } = {}) => async (_body, m) => (hang.includes(m) ? never() : fail.includes(m) ? json(503, { error: {} }) : answer(m));

const cfg = { fusionTuning: { panelHardTimeoutMs: 30, minPanel: 2, stragglerGraceMs: 10 }, fusionJudgeTimeoutMs: 30 };
const ranked = [{ id: "a/x" }, { id: "a/y" }, { id: "b/z" }];
function run(over = {}) {
  const attempts = [];
  const { panel = ["a/x", "b/z"], ...rest } = over; // tests name panel seats by id, or pass {id, connId}
  return runFusion({ handleFusionChat: fakeFusion(), handleSingleModel: single(), body: { messages: [{ role: "user", content: "q" }] },
    ranked, judge: "a/x", cfg, requestId: "r1", comboName: "smart-planning", log: () => {}, record: (a) => attempts.push(a),
    guardMarginMs: 30, ...rest, panel: panel.map((p) => (typeof p === "string" ? { id: p } : p)) }).then((r) => ({ ...r, attempts }));
}

test("pickPanel prefers a different provider for the second seat", () => {
  assert.deepEqual(pickPanel(ranked).map((c) => c.id), ["a/x", "b/z"]);
  assert.deepEqual(pickPanel([{ id: "a/x" }, { id: "a/y" }]).map((c) => c.id), ["a/x", "a/y"]);
  // the same model on another connection is the last resort for the second seat
  assert.deepEqual(pickPanel([{ id: "a/x", connId: "c1" }, { id: "a/x", connId: "c2" }, { id: "a/y" }]).map((c) => c.id), ["a/x", "a/y"]);
  assert.deepEqual(pickPanel([{ id: "a/x", connId: "c1" }, { id: "a/x", connId: "c2" }]).map((c) => c.connId), ["c1", "c2"]);
});

test("two survivors and a good judge → lane fusion, three recorded attempts", async () => {
  const r = await run();
  assert.equal(r.lane, "fusion");
  assert.equal(r.response.status, 200);
  assert.deepEqual(r.attempts.map((a) => a.role), ["panel", "panel", "judge"]);
  assert.equal(r.fusion.survivors, 2);
});

test("one survivor → fusion_degraded with the survivor's answer", async () => {
  const r = await run({ handleSingleModel: single({ fail: ["b/z"] }) });
  assert.equal(r.lane, "fusion_degraded");
  assert.equal((await r.response.json()).choices[0].message.content, "from a/x");
});

test("judge hangs → judge timeout, first good panel answer, judge_failed", async () => {
  const t0 = Date.now();
  const r = await run({ judge: "j/hang", handleSingleModel: single({ hang: ["j/hang"] }) });
  assert.ok(Date.now() - t0 < 1000);
  assert.equal(r.lane, "fusion_degraded");
  assert.equal(r.fusion.judge_failed, true);
  assert.equal(r.attempts.at(-1).status, 504);
  assert.equal((await r.response.json()).choices[0].message.content, "from a/x");
});

test("zero survivors → one single retry on the best non-panel candidate", async () => {
  const r = await run({ handleSingleModel: single({ fail: ["a/x", "b/z"] }) });
  assert.equal(r.lane, "fusion_degraded");
  assert.equal(r.fusion.retry, "a/y");
  assert.deepEqual(r.attempts.map((a) => [a.role, a.status]), [["panel", 503], ["panel", 503], ["retry", 200]]);
});

test("zero survivors and a failed retry → 503 fusion_failed", async () => {
  const r = await run({ handleSingleModel: single({ fail: ["a/x", "a/y", "b/z"] }) });
  assert.equal(r.lane, "fusion_failed");
  assert.equal(r.response.status, 503);
  assert.equal((await r.response.json()).error.reason, "fusion_failed");
});

test("zero survivors and nobody outside the panel → 503 fusion_failed without a retry", async () => {
  const r = await run({ ranked: ranked.slice(0, 2), panel: ["a/x", "a/y"], handleSingleModel: single({ fail: ["a/x", "a/y"] }) });
  assert.equal(r.lane, "fusion_failed");
  assert.equal(r.fusion.retry, null);
});

test("a hung panel member is cut at panelHardTimeoutMs and recorded as 504 before runFusion resolves", async () => {
  const r = await run({ handleSingleModel: single({ hang: ["b/z"] }) });
  assert.equal(r.lane, "fusion_degraded");
  assert.deepEqual(r.attempts.map((a) => [a.target, a.status, a.role]), [["a/x", 200, "panel"], ["b/z", 504, "panel"]]);
});

test("a panel answer that trips OmniRoute's combo rule counts as a failed call", async () => {
  const checkResponse = async (r) => ({ fallback: (await r.text()).includes("from b/z"), reason: "forbidden substring" });
  const r = await run({ checkResponse });
  assert.equal(r.lane, "fusion_degraded");
  assert.deepEqual(r.attempts.filter((a) => a.role === "panel").map((a) => [a.target, a.status]), [["a/x", 200], ["b/z", 502]]);
});

test("a failed judge returns the first good panel answer even when upstream still answers 200", async () => {
  const lenient = async ({ body, models, handleSingleModel, judgeModel }) => {
    await Promise.all(models.map((m) => handleSingleModel(body, m)));
    await handleSingleModel(body, judgeModel);
    return answer("upstream-merge"); // upstream ignores the judge failure
  };
  const r = await run({ handleFusionChat: lenient, handleSingleModel: single({ fail: ["j/j"] }), judge: "j/j" });
  assert.deepEqual([r.lane, r.fusion.judge_failed], ["fusion_degraded", true]);
  assert.match(await r.response.text(), /from a\/x/);
});

test("a judge answer that trips the rule falls back to the first good panel answer", async () => {
  const checkResponse = async (r) => ({ fallback: (await r.text()).includes("from j/j") });
  const r = await run({ judge: "j/j", checkResponse });
  assert.deepEqual([r.lane, r.fusion.judge_failed], ["fusion_degraded", true]);
  assert.match(await r.response.text(), /from a\/x/);
});

test("panel, judge and retry calls carry their candidate's connection", async () => {
  const seen = [];
  const h = (fail = []) => async (_b, m, target) => { seen.push([m, target?.connectionId ?? null]); return fail.includes(m) ? json(503, { error: {} }) : answer(m); };
  const r = await run({ handleSingleModel: h(), panel: [{ id: "a/x", connId: "c9" }, { id: "b/z", connId: "c2" }], judgeConn: "c9" });
  assert.deepEqual(seen, [["a/x", "c9"], ["b/z", "c2"], ["a/x", "c9"]]);
  assert.deepEqual(r.attempts.map((a) => a.connectionId), ["c9", "c2", "c9"]);
  seen.length = 0;
  await run({ handleSingleModel: h(["a/x", "b/z"]), ranked: [{ id: "a/x" }, { id: "a/y", connId: "c5" }, { id: "b/z" }] });
  assert.deepEqual(seen.at(-1), ["a/y", "c5"]); // the zero-survivor retry keeps its connection too
});

test("an upstream that judges with a panel model sends that model's connection, not the configured judge's", async () => {
  const seen = [];
  const h = async (_b, m, target) => { seen.push([m, target?.connectionId ?? null]); return answer(m); };
  const panelJudge = async ({ body, models, handleSingleModel }) => { await Promise.all(models.map((m) => handleSingleModel(body, m))); return handleSingleModel(body, models[0]); };
  const r = await run({ handleFusionChat: panelJudge, handleSingleModel: h, judge: "j/j", judgeConn: "cj",
    panel: [{ id: "a/x", connId: "c9" }, { id: "b/z", connId: "c2" }] });
  assert.deepEqual(seen.at(-1), ["a/x", "c9"]);
  assert.deepEqual([r.attempts.at(-1).role, r.attempts.at(-1).connectionId], ["judge", "c9"]);
});

test("a synchronous rule check works, and a synchronous throw fails closed", async () => {
  const sync = await run({ checkResponse: () => ({ fallback: false }) });
  assert.equal(sync.lane, "fusion");
  const thrown = await run({ checkResponse: () => { throw new Error("check broke"); } });
  assert.equal(thrown.lane, "fusion_failed");
  assert.ok(thrown.attempts.every((a) => a.status === 502));
});

test("an upstream-supplied connection never replaces the scored seat's connection", async () => {
  const seen = [];
  const h = async (_b, m, target) => { seen.push([m, target?.connectionId]); return answer(m); };
  const upstream = async ({ body, models, handleSingleModel }) => { await Promise.all(models.map((m) => handleSingleModel(body, m, { connectionId: "upstream-pick" }))); return answer("x"); };
  const r = await run({ handleFusionChat: upstream, handleSingleModel: h, panel: [{ id: "a/x", connId: "c9" }, { id: "b/z", connId: "c2" }] });
  assert.deepEqual(seen, [["a/x", "c9"], ["b/z", "c2"]]);
  assert.deepEqual(r.attempts.map((a) => a.connectionId), ["c9", "c2"]);
});

test("a rule check that never settles fails closed inside the call's bound", async () => {
  const t0 = Date.now();
  const r = await run({ checkResponse: never });
  assert.ok(Date.now() - t0 < 1000, "a stalled check must not stretch the deadline");
  assert.equal(r.lane, "fusion_failed");
  assert.ok(r.attempts.every((a) => a.status === 502));
});

test("a throwing fusion call logs only the error name, never its message", async () => {
  const logged = [];
  const r = await run({ handleFusionChat: async () => { throw new Error("Bearer sk-live-secret in request text"); }, log: { error: (m) => logged.push(m) } });
  assert.equal(r.fusion.threw, true);
  assert.deepEqual(logged, ["[smart] handleFusionChat threw (Error)"]);
});

test("with one model on three connections, the retry uses the connection outside the panel", async () => {
  const seen = [];
  const h = async (_b, m, target) => { seen.push([m, target?.connectionId]); return ["c1", "c2"].includes(target?.connectionId) ? json(503, { error: {} }) : answer(m); };
  const three = ["c1", "c2", "c3"].map((connId) => ({ id: "a/x", connId }));
  const r = await run({ handleSingleModel: h, ranked: three, panel: three.slice(0, 2) });
  assert.deepEqual(seen.at(-1), ["a/x", "c3"]);
  assert.deepEqual([r.fusion.retry, r.lane], ["a/x", "fusion_degraded"]);
});

test("a hung zero-survivor retry ends in 503 fusion_failed within the panel timeout", async () => {
  const t0 = Date.now();
  const r = await run({ handleSingleModel: single({ fail: ["a/x", "b/z"], hang: ["a/y"] }) });
  assert.ok(Date.now() - t0 < 1000);
  assert.equal(r.lane, "fusion_failed");
  assert.deepEqual([r.attempts.at(-1).role, r.attempts.at(-1).status], ["retry", 504]);
});

test("regression: a fusion call that never settles is cut at panel + judge + margin", async () => {
  const t0 = Date.now();
  const r = await run({ handleFusionChat: fakeFusion({ hang: true }) });
  assert.ok(Date.now() - t0 < 1000, "runFusion must not hang");
  assert.equal(r.fusion.timed_out, true);
  // nothing survived inside the hung call, so the zero-survivor retry ran
  assert.equal(r.fusion.retry, "a/y");
  assert.equal(r.lane, "fusion_degraded");
});
```

- [ ] **Step 2: Run them and see them fail**

Run: `node --test 'omniroute-smart/strategies/smart/test/fusion.test.mjs'`
Expected: FAIL (`fusion.mjs` is missing).

- [ ] **Step 3: Implement**

```js
// omniroute-smart/strategies/smart/fusion.mjs
// Fusion lane: panel of two + judge through OmniRoute's injected handleFusionChat, bounded and fully logged.
import { errorResponse } from "./errors.mjs";

const TIMEOUT = Symbol("timeout");
const providerOf = (id) => String(id).slice(0, String(id).indexOf("/"));
const within = (promise, ms) => {
  let t;
  return Promise.race([promise, new Promise((r) => { t = setTimeout(() => r(TIMEOUT), ms); })]).finally(() => clearTimeout(t));
};
const failed = (status, message) =>
  new Response(JSON.stringify({ error: { message } }), { status, headers: { "content-type": "application/json" } });

/** Top candidate, then the best-ranked one from another provider, else another model, else the runner-up. */
export function pickPanel(ranked) {
  const [first, ...rest] = ranked;
  return [first, rest.find((c) => providerOf(c.id) !== providerOf(first.id)) ?? rest.find((c) => c.id !== first.id) ?? rest[0]];
}

/** handleSingleModel's target pinned to the scored candidate's connection: its quota and gates were checked, an upstream pick was not. */
const withConn = (target, connId) => (connId ? { ...(target ?? {}), connectionId: connId } : target);

export async function runFusion({ handleFusionChat, handleSingleModel, body, ranked, panel, judge, judgeConn = null, cfg, requestId, comboName, log,
  record, now = Date.now, guardMarginMs = 5000, fusionExtras = {}, checkResponse = null }) {
  // OmniRoute's own combo rules (forbidden substrings, HTTP-200 error text) on a 2xx answer, inside the time left of the bound
  // that covers the call (`until`), so a stalled check cannot stretch a deadline; a check that throws or stalls fails closed
  const tripped = async (res, until) => {
    if (!res?.ok || !checkResponse) return null;
    const r = await within(Promise.resolve().then(() => checkResponse(res.clone())).catch(() => null), Math.max(1, until - now())); // sync or async check
    return r && r !== TIMEOUT ? r : { fallback: true, reason: r === TIMEOUT ? "rule check timed out" : "rule check failed" };
  };
  const fusion = { panel: panel.map((c) => c.id), judge, survivors: 0, judge_failed: false, retry: null, timed_out: false, threw: false };
  let calls = 0, firstOk = null, closed = false;

  const wrapped = async (callBody, modelStr, target) => {
    const i = calls++;
    const role = i < panel.length ? "panel" : "judge";
    // panel calls start in `models` order, so seat i is the candidate; fall back to the id for a reordering upstream
    const seat = role === "panel" ? (panel[i].id === modelStr ? panel[i] : panel.find((c) => c.id === modelStr)) : null;
    // upstream may judge with a surviving panel model instead of judgeModel: the connection follows the model actually called
    const connId = role === "panel" ? seat?.connId ?? null
      : modelStr === judge ? judgeConn : panel.find((c) => c.id === modelStr)?.connId ?? null;
    const limit = role === "judge" ? cfg.fusionJudgeTimeoutMs : cfg.fusionTuning.panelHardTimeoutMs;
    const t0 = now();
    let res = null;
    try {
      // both roles are bounded, so every attempt is recorded before the outer deadline (and before finalize)
      res = await within(handleSingleModel(callBody, modelStr, withConn(target, connId)), limit);
    } catch { res = null; }
    if (res === TIMEOUT) res = failed(504, `${role} timed out`);
    res ??= failed(502, `${role} call failed`);
    const rule = await tripped(res, t0 + limit);
    if (rule?.fallback) res = failed(502, `${role} answer tripped the combo rule${rule.reason ? ` (${rule.reason})` : ""}`); // as the combo loop would
    if (closed) return res; // a call the hung upstream fusion started after the outer deadline: the decision is already final
    record({ target: modelStr, connectionId: connId ?? target?.connectionId ?? null, status: res.status, latency_ms: now() - t0, role });
    if (role === "panel" && res.ok) { fusion.survivors++; firstOk ??= res.clone(); }
    if (role === "judge" && !res.ok) fusion.judge_failed = true;
    return res;
  };

  const deadline = cfg.fusionTuning.panelHardTimeoutMs + cfg.fusionJudgeTimeoutMs + guardMarginMs, start = now();
  let res = await within(Promise.resolve().then(() => handleFusionChat({ body, models: panel.map((c) => c.id), handleSingleModel: wrapped, log, comboName,
    judgeModel: judge, ...fusionExtras, tuning: cfg.fusionTuning })).catch((e) => { log?.error?.(`[smart] handleFusionChat threw (${e?.name ?? "error"})`); fusion.threw = true; return null; }), deadline); // the name only: a message can carry request text or keys
  closed = true;
  if (res === TIMEOUT) { fusion.timed_out = true; res = null; }
  if ((await tripped(res, start + deadline))?.fallback) { fusion.judge_failed = true; res = null; } // an answer upstream assembled itself is checked too

  if (fusion.survivors === 0) {
    const seat = (c) => panel.some((p) => p.id === c.id && (p.connId ?? null) === (c.connId ?? null));
    // prefer a model outside the panel; else the same model on a connection the panel did not use
    const next = ranked.find((c) => !panel.some((p) => p.id === c.id)) ?? ranked.find((c) => !seat(c));
    if (next) {
      fusion.retry = next.id;
      const t0 = now();
      let r = null;
      try { r = await within(handleSingleModel(body, next.id, withConn(undefined, next.connId)), cfg.fusionTuning.panelHardTimeoutMs); } catch { r = null; }
      if (r === TIMEOUT) r = failed(504, "retry timed out");
      if ((await tripped(r, t0 + cfg.fusionTuning.panelHardTimeoutMs))?.fallback) r = failed(502, "retry answer tripped the combo rule");
      record({ target: next.id, connectionId: next.connId ?? null, status: r?.status ?? 502, latency_ms: now() - t0, role: "retry" });
      if (r?.ok) return { response: r, lane: "fusion_degraded", fusion };
    }
    return { response: errorResponse("fusion_failed", `no panel survivor${next ? `; retry on ${next.id} failed` : "; no candidate outside the panel"}`, requestId),
      lane: "fusion_failed", fusion };
  }
  if (res?.ok && fusion.survivors >= 2 && !fusion.judge_failed && !fusion.timed_out) return { response: res, lane: "fusion", fusion };
  // a failed judge never reaches the client: the first good panel answer goes back, even if upstream still answered 2xx
  if (res?.ok && !fusion.judge_failed) return { response: res, lane: "fusion_degraded", fusion };
  return { response: firstOk, lane: "fusion_degraded", fusion };
}
```

The argument list must be the one P1 S3 recorded for the pinned digest. The research (`strategies-fusion.md` §3) and P1's context registry name `body, models, handleSingleModel, log, comboName, judgeModel, judgeTarget, tuning, perTargetAdmission`; the code above passes the always-present ones and spreads `fusionExtras` for the rest. Task 3 builds `fusionExtras` from the S3 record (for example `judgeTarget` from the judge candidate, and `perTargetAdmission` if S3 showed it is required). A throw from `handleFusionChat` is logged and recorded as `fusion.threw`, so a wrong argument list shows up in the decision log and in Task 6 row 2 instead of hiding behind the zero-survivor retry.

`handleSingleModel(body, modelStr, target)` takes the connection from `target.connectionId`, the same field P3's `order()` sets on combo targets (P1 S1/S3 record that both honour it). If P1 recorded a different target shape, change only `withConn`.

- [ ] **Step 4: Run the tests and commit**

Run: `node --test 'omniroute-smart/strategies/smart/test/fusion.test.mjs'`
Expected: PASS, 20 tests.

```bash
git add omniroute-smart/strategies/smart/fusion.mjs omniroute-smart/strategies/smart/test/fusion.test.mjs
git commit -m "feat(smart): fusion runner with judge timeout, outer deadline and zero-survivor retry"
```

---

### Task 3: Wire the fusion lane into `smart.mjs`

**Files:**
- Modify: `omniroute-smart/strategies/smart.mjs`
- Test: `omniroute-smart/strategies/smart/test/fusion-module.test.mjs`

**Interfaces:**
- Consumes: Task 2; P3 `createSmart` internals (`candidates`, `rank`, `state.log`, `state.failures`, `ctx()`).
- Produces:
  - `decide(answers, ranked, cfg, body, { fusionAvailable, rulesCovered }) → { lane: "fusion" | "single", fusion_eligible, skip? }`. Both default to `false`, so the P3 four-argument call keeps returning `single`. `rulesCovered` is true when the fusion lane inherits OmniRoute's forbidden-substring / HTTP-200 error-text rules (P1: `ctx.checkComboResponse` exists, or `handleSingleModel` applies them).
  - `skip` is one of `"not_eligible"`, `"no_fusion_handler"`, `"rules_unavailable"`, `"stream"` (the last only if P1 S3 recorded that `handleFusionChat` cannot stream).
  - The decision object gains `fusion` (Task 2 shape plus `judge_source: "config" | "panel_default" | "panel_fallback"` and `skip`).

- [ ] **Step 1: Write the failing tests**

```js
// omniroute-smart/strategies/smart/test/fusion-module.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { AsyncLocalStorage } from "node:async_hooks";
import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createSmart, decide } from "../../smart.mjs";

const NOW = Date.parse("2026-10-13T10:00:00Z");
const Q = (q) => Object.fromEntries(["plan", "review_audit", "execute_edit", "mechanical"].map((k) =>
  [k, Object.fromEntries(["frontend", "backend", "fullstack", "infra_docs"].map((d) => [d, { q, c: 1, source: "measured" }]))]));
const json = (status, body) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
const ANALYSIS = { task_kind: { type: "choice", choice: "plan", answer_confidence: 0.9 }, domain: { type: "choice", choice: "backend", answer_confidence: 0.9 },
  scope: { type: "score", probabilities: { 0: 0.05, 1: 0.9, 2: 0.05 } }, complexity: { type: "score", probabilities: { 0: 0.05, 1: 0.05, 2: 0.9 } },
  second_opinion: { type: "noul", noul: 0.05 } };

function env({ comboConfig = {}, breakerOpen = [], withFusion = true, quota = {} } = {}) {
  const dataDir = mkdtempSync(join(tmpdir(), "smart-fusion-"));
  writeFileSync(join(dataDir, "settings.json"), readFileSync(new URL("./fixtures/settings.json", import.meta.url)));
  writeFileSync(join(dataDir, "scorecard.json"), JSON.stringify({ generated_at: new Date(NOW).toISOString(), entries: {
    "p/a": { Q: Q(0.8), catalog: {} }, "q/b": { Q: Q(0.79), catalog: {} }, "p/c": { Q: Q(0.78), catalog: {} }, "j/judge": { Q: Q(0.9), catalog: {} }, "j/low": { Q: Q(0.5), catalog: {} } } }));
  const als = new AsyncLocalStorage();
  globalThis.__omniSmart = { als, ctx: {} };
  const calls = [];
  const ctx = {
    getPluginConfig: async () => ({ config: { systemOneLayaUrl: "http://system-one:8000" } }),
    getQuotaCache: (conn) => quota[conn] ?? null,
    getCircuitBreaker: (provider) => ({ getStatus: () => ({ state: breakerOpen.includes(provider) ? "OPEN" : "CLOSED" }) }),
    isModelLocked: () => false,
    listProviderConnections: async () => ["p", "q", "j"].map((provider) => ({ id: `c-${provider}`, provider, priority: 1, isActive: true })),
    handleSingleModel: async (_body, m) => { calls.push(m); return json(200, { choices: [{ message: { content: `from ${m}` } }] }); },
  };
  if (withFusion) ctx.checkComboResponse = async () => ({ fallback: false }); // P1's inherited combo-rule check (passes here)
  if (withFusion) ctx.handleFusionChat = async ({ body, models, handleSingleModel, judgeModel }) => {
    await Promise.all(models.map((m) => handleSingleModel(body, m)));
    return handleSingleModel({ messages: [] }, judgeModel);
  };
  const fetchImpl = async () => json(200, { answers: ANALYSIS });
  const smart = createSmart({ dataDir, ctx, env: {}, fetchImpl, now: () => NOW, flushMs: 0 });
  const combo = { name: "smart-planning", strategy: "smart", models: ["p/a", "q/b", "p/c"], config: comboConfig };
  const run = (body) => als.run({ body }, async () => ({ pre: await smart.prelude({ strategy: "smart", body, combo, config: comboConfig, store: als.getStore() }),
    store: als.getStore() }));
  const lines = () => readFileSync(join(dataDir, "decisions.jsonl"), "utf8").trim().split("\n").map((l) => JSON.parse(l));
  return { run, calls, lines, smart };
}
const ask = { messages: [{ role: "user", content: "Plan the migration of the billing service to event sourcing." }] };

test("a fusion-eligible request runs panel + judge in the prelude and logs lane fusion", async () => {
  const e = env();
  const { pre } = await e.run(structuredClone(ask));
  assert.equal(pre.response.status, 200);
  assert.deepEqual(e.calls, ["p/a", "q/b", "p/a"]); // panel prefers another provider; default judge = panel[0]
  const line = e.lines()[0];
  assert.equal(line.outcome.lane, "fusion");
  assert.equal(line.outcome.fusion.judge_source, "panel_default");
  assert.equal(line.attempts.length, 3);
});

test("a configured judge with an OPEN breaker falls back to the panel's first member", async () => {
  const e = env({ comboConfig: { judgeModel: "j/judge" }, breakerOpen: ["j"] });
  await e.run(structuredClone(ask));
  assert.equal(e.calls.at(-1), "p/a");
  assert.equal(e.lines()[0].outcome.fusion.judge_source, "panel_fallback");
});

test("a configured judge whose connection is drained falls back to the panel's first member", async () => {
  const e = env({ comboConfig: { judgeModel: "j/judge" }, quota: { "c-j": { exhausted: true, nextResetAt: "2026-10-14T00:00:00Z", quotas: {} } } });
  await e.run(structuredClone(ask));
  assert.equal(e.calls.at(-1), "p/a");
  assert.equal(e.lines()[0].outcome.fusion.judge_source, "panel_fallback");
});

test("a configured judge below the combo's quality floor falls back to the panel's first member", async () => {
  const e = env({ comboConfig: { judgeModel: "j/low" } }); // Q 0.5 < best member 0.8 − qualityFloorDelta
  await e.run(structuredClone(ask));
  assert.equal(e.calls.at(-1), "p/a");
  assert.equal(e.lines()[0].outcome.fusion.judge_source, "panel_fallback");
});

test("a configured judge that passes the gates judges", async () => {
  const e = env({ comboConfig: { judgeModel: "j/judge" } });
  await e.run(structuredClone(ask));
  assert.equal(e.calls.at(-1), "j/judge");
  assert.equal(e.lines()[0].outcome.fusion.judge_source, "config");
});

test("a tool-bearing analysis request stays single", async () => {
  const e = env();
  const { pre, store } = await e.run({ ...structuredClone(ask), tools: [{ type: "function", function: { name: "f", parameters: {} } }] });
  assert.equal(pre, null);
  assert.deepEqual(e.calls, []);
  assert.deepEqual([store.smart.lane, store.smart.fusion.skip], ["single", "not_eligible"]);
});

test("tool_choice none does not count as tool-bearing", () => {
  const answers = Object.fromEntries(Object.entries({ task_kind: "plan", domain: "backend", scope: "medium", complexity: "high", second_opinion: 0 })
    .map(([k, value]) => [k, { value, confidence: 0.9 }]));
  const body = { tools: [{ type: "function" }], tool_choice: "none" };
  assert.equal(decide(answers, [{ id: "p/a" }, { id: "q/b" }], { systemOneMinConfidence: 0.7, fusionSecondOpinionMin: 0.5 }, body, { fusionAvailable: true, rulesCovered: true }).lane, "fusion");
});

test("without OmniRoute's combo-rule check, fusion stays off (rules_unavailable)", () => {
  const answers = Object.fromEntries(Object.entries({ task_kind: "plan", domain: "backend", scope: "medium", complexity: "high", second_opinion: 0 })
    .map(([k, value]) => [k, { value, confidence: 0.9 }]));
  assert.deepEqual(decide(answers, [{ id: "p/a" }, { id: "q/b" }], { systemOneMinConfidence: 0.7, fusionSecondOpinionMin: 0.5 }, {}, { fusionAvailable: true }),
    { lane: "single", fusion_eligible: true, skip: "rules_unavailable" });
});

test("no handleFusionChat in the context → single, with skip recorded", async () => {
  const e = env({ withFusion: false });
  const { pre, store } = await e.run(structuredClone(ask));
  assert.equal(pre, null);
  assert.deepEqual([store.smart.lane, store.smart.fusion_eligible, store.smart.fusion.skip], ["single", true, "no_fusion_handler"]);
  e.smart.observe({ store, target: { modelStr: "p/a", connectionId: "c-p" }, status: 200, latencyMs: 5 });
  assert.deepEqual(e.lines()[0].outcome.fusion, { skip: "no_fusion_handler" }); // the logged single-lane skip shape
});

test("the decision-log sweep waits out the longest legal fusion run", async () => {
  const { SWEEP_AGE_MS } = await import("../../smart.mjs");
  // P2's limits: panelHardTimeoutMs and fusionJudgeTimeoutMs ≤ 600 s; worst case = panel + judge + 5 s margin + a retry bounded by the panel timeout
  assert.ok(SWEEP_AGE_MS >= 5 * 60e3 + (600e3 + 600e3 + 5e3) + 600e3);
});

test("the P3 four-argument decide() still returns single", () => {
  const answers = Object.fromEntries(["task_kind", "domain", "scope", "complexity", "second_opinion"].map((k) => [k, { value: k === "task_kind" ? "plan" : "high", confidence: 0.9 }]));
  assert.equal(decide(answers, [{ id: "p/a" }, { id: "q/b" }], { systemOneMinConfidence: 0.7, fusionSecondOpinionMin: 0.5 }, {}).lane, "single");
});
```

The fixture `test/fixtures/settings.json` is the one P3 Task 8 created. If P3 changed its location, follow that.

- [ ] **Step 2: Run them and see them fail**

Run: `node --test 'omniroute-smart/strategies/smart/test/fusion-module.test.mjs'`
Expected: FAIL (`decide` ignores the fifth argument; the prelude never runs fusion).

- [ ] **Step 3: Implement**

Replace P3's `decide` in `smart.mjs`:

```js
/** Lane choice (PRD "Single vs fusion"). fusionAvailable is false unless the context injects handleFusionChat. */
export function decide(answers, ranked, cfg, body, { fusionAvailable = false, rulesCovered = false } = {}) {
  const tools = Array.isArray(body.tools) && body.tools.length > 0 && body.tool_choice !== "none";
  const confident = Object.values(answers).every((a) => a.confidence >= cfg.systemOneMinConfidence);
  const analysis = ["plan", "review_audit"].includes(answers.task_kind.value);
  const wants = answers.complexity.value === "high" || answers.second_opinion.value > cfg.fusionSecondOpinionMin;
  const fusion_eligible = analysis && wants && confident && !tools && ranked.length >= 2;
  if (!fusion_eligible) return { lane: "single", fusion_eligible, skip: "not_eligible" };
  if (!fusionAvailable) return { lane: "single", fusion_eligible, skip: "no_fusion_handler" };
  // the user's rule: fusion must inherit OmniRoute's own forbidden-substring / HTTP-200 error-text behaviour, or not run
  if (!rulesCovered) return { lane: "single", fusion_eligible, skip: "rules_unavailable" };
  return { lane: "fusion", fusion_eligible };
}
```

If P1 S3 recorded that `handleFusionChat` cannot stream, add before the last line: `if (body.stream === true) return { lane: "single", fusion_eligible, skip: "stream" };`, plus a test for it.

Inside `createSmart`, add a shared attempt recorder and use it from `observe`:

```js
  function recordAttempt(smart, { target, status, latency_ms, role, connectionId, retryAfterMs, quotaResetAt }) {
    const provider = String(target).split("/")[0];
    const cand = smart.ranked.find((c) => c.id === target && (connectionId == null || c.connId === connectionId));
    const conn = connectionId ?? cand?.connId ?? null;
    state.failures.record(`${provider}|${target}|${conn}`,
      { status: status ?? 0, retryAfterMs, quotaResetAt, fallbackResetAt: cand?.shortestResetAt ?? null }, now()); // keep P3's 429 window rule
    state.log.attempt(smart, { target, connection: conn, status: status ?? 0, latency_ms, ...(role ? { role } : {}) });
  }
```

In `observe`, make the finalize call carry the lane details, so every `decisions.jsonl` line has `outcome.fusion` when a skip was recorded: `state.log.finalize(smart, { status: outcome.status, ...(smart.fusion ? { fusion: smart.fusion } : {}) });`. Then replace the `failures.record` and `log.attempt` lines with `recordAttempt(smart, { target: t.modelStr, status: outcome.status, latency_ms: outcome.latencyMs, connectionId: t.connectionId, retryAfterMs: outcome.retryAfterMs, quotaResetAt: outcome.quotaResetAt });`, and add at its top, after the `!smart` check:

```js
      if (smart.lane !== "single") return; // fusion attempts are recorded by the fusion wrapper, never twice
```

Add judge gating inside `createSmart`:

```js
  /** PRD: judge = combo judgeModel if it passes the gates, else the panel's first member (always gated already). */
  async function judgeFor(cfg, smart, panel, body, settings, card) {
    const want = cfg.judgeModel;
    if (!want) return { judge: panel[0].id, judgeConn: panel[0].connId ?? null, judge_source: "panel_default" };
    const inRanking = smart.ranked.find((c) => c.id === want);
    if (inRanking) return { judge: want, judgeConn: inRanking.connId ?? null, judge_source: "config" };
    if (!smart.dropped.some((d) => d.id === want)) {
      const cands = await candidates({ models: [want] }, body, settings, card, cfg, smart.answers);
      const flat = Object.fromEntries(Object.entries(smart.answers).map(([k, a]) => [k, a.value]));
      const best = rank(cands, { taskKind: flat.task_kind, cfg, scope: flat.scope, now: now() }).ranked[0];
      // ranked alone it is its own Q-best, so apply the quality floor against the combo's eligible candidates here
      const qFloor = Math.max(...smart.ranked.map((c) => c.Q)) - cfg.qualityFloorDelta;
      if (best && best.Q >= qFloor) return { judge: want, judgeConn: best.connId ?? null, judge_source: "config" };
    }
    return { judge: panel[0].id, judgeConn: panel[0].connId ?? null, judge_source: "panel_fallback" };
  }
```

A single-candidate ranking cannot trip the quality floor (it is its own best), so `rank` applies the hard gates (capability, breaker, lock, cooldown, headroom, expiry) and `judgeFor` then checks the judge's `Q` against the combo's best eligible `Q` minus `qualityFloorDelta`, the same floor the members passed.

In `prelude`, replace `Object.assign(smart, decide(answers, ranked, cfg, pctx.body));` and the lines after it up to `return null;` with:

```js
        const checkResponse = ctx().checkComboResponse ? (r) => ctx().checkComboResponse(pctx.combo, r) : null;
        const lane = decide(answers, ranked, cfg, pctx.body, { fusionAvailable: typeof ctx().handleFusionChat === "function",
          rulesCovered: checkResponse != null || deps.singleModelAppliesRules === true });
        Object.assign(smart, { lane: lane.lane, fusion_eligible: lane.fusion_eligible, fusion: lane.skip ? { skip: lane.skip } : undefined });
        smart.effort = effortFor(flat, card.entries[ranked[0].id]?.catalog?.effort_options);
        if (smart.effort) pctx.body.reasoning_effort = smart.effort;
        if (s) s.smart = smart;
        state.log.begin(begun = smart); // P3's catch finalizes this entry if a fusion step throws
        if (smart.lane !== "fusion") return null;
        const panel = pickPanel(ranked);
        const { judge, judgeConn, judge_source } = await judgeFor(cfg, smart, panel, pctx.body, settings, card);
        const out = await runFusion({ handleFusionChat: ctx().handleFusionChat, handleSingleModel: ctx().handleSingleModel, body: pctx.body,
          ranked, panel, judge, judgeConn, cfg, requestId, comboName: pctx.combo?.name, log: pctx.log,
          record: (a) => recordAttempt(smart, a), now, guardMarginMs: deps.fusionGuardMarginMs ?? 5000,
          fusionExtras: deps.fusionExtras?.({ judge, panel, ranked, body: pctx.body }) ?? {},
          checkResponse: deps.singleModelAppliesRules === true ? null : checkResponse });
        smart.lane = out.lane;
        smart.fusion = { ...out.fusion, judge_source };
        state.log.finalize(smart, { status: out.response.status, ...(out.lane === "fusion_failed" ? { reason: "fusion_failed" } : {}), fusion: smart.fusion });
        return { response: out.response };
```

Add `import { pickPanel, runFusion } from "./smart/fusion.mjs";` to the imports. In the default export, set `singleModelAppliesRules: true` only if P1 S3 recorded that `handleSingleModel` itself applies the combo's forbidden-substring / HTTP-200 error-text rules; adapt the `checkComboResponse(combo, response)` call to the shape P1 recorded. In the default export at the bottom of `smart.mjs`, add a `fusionExtras` function that returns the extra `handleFusionChat` arguments P1 S3 recorded as required (for example `({ judge }) => ({ judgeTarget: <S3's target shape for judge> })`); if S3 recorded none, omit it. P3's `log.finalize` already copies `lane` from the decision and counts `fusion_degraded` / `fusion_failed` in `task-stats.json`; the extra `fusion` field lands in `outcome` because `finalize` spreads its second argument into `outcome`.

- [ ] **Step 4: Run all strategy tests and the benchmark, then commit**

Run: `node --test 'omniroute-smart/**/*.test.mjs' && node omniroute-smart/strategies/smart/bench.mjs`
Expected: PASS; the benchmark still exits 0 (its requests are execute_edit, so the fusion branch costs one `decide` call).

```bash
git add omniroute-smart/strategies
git commit -m "feat(smart): fusion lane with gated judge, shared attempt recording and logged skips"
```

---

### Task 4: check-server rows (fusion degraded, test overrides)

**Files:**
- Create: `omniroute-smart/probe/fusion-24h.mjs` (baked into the image with P2's `probe/` directory)
- Modify: `scripts/check-server.sh` (the smart-routing section's enabled branch)
- Test: `omniroute-smart/probe/test/fusion-24h.test.mjs`, `tests/test_shell.py`

**Interfaces:**
- Consumes: `decisions.jsonl` and its rotated files (`decisions.jsonl.1` …, P3's `appendJsonl` rotation) lines (`at`, `outcome.lane`); the production container's env.
- Produces:
  - `Verdict: PASS identity=smart-routing fusion degraded (24 h): <n> of <m> fusion requests`: always PASS (informational, as the PRD asks for the count), unless the count cannot be read, which is a FAIL;
  - `Verdict: PASS identity=smart-routing test overrides are off` or FAIL when either `OMNIROUTE_SMART_TEST_MODE` or `OMNIROUTE_PLUGIN_SMART_CATALOG_TEST_MODE` is set in the `omniroute` container.

- [ ] **Step 1: Write the failing tests**

```js
// omniroute-smart/probe/test/fusion-24h.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fusionCounts } from "../fusion-24h.mjs";

const NOW = Date.parse("2026-10-13T12:00:00Z");
const l = (h, lane) => JSON.stringify({ at: new Date(NOW - h * 3600e3).toISOString(), outcome: { lane } }) + "\n";

test("counts the last 24 h of fusion lines across the live and rotated logs", () => {
  const dir = mkdtempSync(join(tmpdir(), "fusion24-"));
  writeFileSync(join(dir, "decisions.jsonl.2"), l(30, "fusion_degraded"));                  // older than 24 h
  writeFileSync(join(dir, "decisions.jsonl.1"), l(20, "fusion_degraded") + l(19, "fusion"));  // rotated inside the window
  writeFileSync(join(dir, "decisions.jsonl"), l(1, "fusion_failed") + l(1, "single") + "not json\n");
  assert.deepEqual(fusionCounts(dir, NOW), { degraded: 1, fusion: 3 });
});

test("a missing log directory counts as no decisions yet", () => {
  assert.deepEqual(fusionCounts(join(tmpdir(), "no-such-smart-routing-dir"), NOW), { degraded: 0, fusion: 0 });
});
```

```python
def test_check_server_reports_fusion_degraded_count(tmp_path: pathlib.Path) -> None:
    result, _ = run_check_server(tmp_path, {}, llm_server_enabled=False, smart_routing_enabled=True, smart_fusion_counts="2 7")
    assert "PASS identity=smart-routing fusion degraded (24 h): 2 of 7 fusion requests" in result.stdout


def test_check_server_fails_fusion_row_when_the_log_is_unreadable(tmp_path: pathlib.Path) -> None:
    result, _ = run_check_server(tmp_path, {}, llm_server_enabled=False, smart_routing_enabled=True, smart_fusion_counts=None)
    assert "FAIL identity=smart-routing fusion degraded (24 h) could not be read" in result.stderr


def test_check_server_fails_when_test_overrides_are_set(tmp_path: pathlib.Path) -> None:
    result, _ = run_check_server(tmp_path, {}, llm_server_enabled=False, smart_routing_enabled=True,
                                 smart_container_env="OMNIROUTE_SMART_TEST_MODE=1\nPORT=20128\n")
    assert "FAIL identity=smart-routing test overrides are off" in result.stderr


def test_check_server_passes_when_test_overrides_are_absent(tmp_path: pathlib.Path) -> None:
    result, _ = run_check_server(tmp_path, {}, llm_server_enabled=False, smart_routing_enabled=True,
                                 smart_container_env="PORT=20128\n")
    assert "PASS identity=smart-routing test overrides are off" in result.stdout
```

Extend `run_check_server` with two keywords whose defaults leave existing tests unchanged:
- `smart_fusion_counts: str | None = "0 0"`: the stubbed `podman exec omniroute node /opt/omniroute-smart/probe/fusion-24h.mjs` prints it (match on `fusion-24h` in `$*`); `None` makes it exit 1 with no output (an unreadable log);
- `smart_container_env: str | None = "PORT=20128\n"`: the stubbed `podman inspect --format '{{range .Config.Env}}…' omniroute` prints it (match on `.Config.Env` in `$*`); `None` makes that inspect exit 125.

Add a third test: `smart_container_env=None` → `"FAIL identity=smart-routing test overrides are off (podman inspect omniroute failed"` in stderr.

Run: `node --test 'omniroute-smart/probe/test/*.test.mjs' && uv run pytest tests/test_shell.py -k "smart or fusion or override" -v`
Expected: FAIL (no `fusion-24h.mjs`, no rows yet). The `-k` expression selects the new check-server tests: their names contain `fusion` or `override`, not `smart`.

- [ ] **Step 2: Implement**

```js
// omniroute-smart/probe/fusion-24h.mjs
// check-server's fusion row: fusion requests in the last 24 h and how many degraded, over the live and rotated decision logs.
import { existsSync, readdirSync, readFileSync, realpathSync } from "node:fs";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

export function fusionCounts(dir, now) {
  const since = now - 864e5;
  let degraded = 0, fusion = 0;
  // a missing dir = no decisions yet; an unreadable file throws, so the CLI prints nothing and check-server FAILs
  const files = existsSync(dir) ? readdirSync(dir).filter((n) => /^decisions\.jsonl(\.\d+)?$/.test(n)) : [];
  for (const f of files) for (const l of readFileSync(join(dir, f), "utf8").split("\n")) {
    if (!l) continue;
    let j; try { j = JSON.parse(l); } catch { continue; }
    if (!(Date.parse(j.at) >= since)) continue;
    const lane = j.outcome?.lane;
    if (lane === "fusion" || lane === "fusion_degraded" || lane === "fusion_failed") fusion++;
    if (lane === "fusion_degraded") degraded++;
  }
  return { degraded, fusion };
}

if (process.argv[1] && import.meta.url === pathToFileURL(realpathSync(process.argv[1])).href) {
  const { degraded, fusion } = fusionCounts(process.env.OMNIROUTE_SMART_DATA_DIR || "/app/data/smart-routing", Date.now());
  console.log(`${degraded} ${fusion}`);
}
```

```bash
    fusion="$(podman exec omniroute node /opt/omniroute-smart/probe/fusion-24h.mjs 2>/dev/null || true)"
    if [[ "$fusion" =~ ^[0-9]+\ [0-9]+$ ]]; then
        ok "Verdict: PASS identity=smart-routing fusion degraded (24 h): ${fusion% *} of ${fusion#* } fusion requests"
    else
        bad "Verdict: FAIL identity=smart-routing fusion degraded (24 h) could not be read"
    fi
    # inspect first: a failed inspect must FAIL the row, not count as zero overrides
    if ! container_env="$(podman inspect --format '{{range .Config.Env}}{{println .}}{{end}}' omniroute 2>/dev/null)"; then
        bad "Verdict: FAIL identity=smart-routing test overrides are off (podman inspect omniroute failed; cannot check)"
    elif grep -qE '^(OMNIROUTE_SMART_TEST_MODE|OMNIROUTE_PLUGIN_SMART_CATALOG_TEST_MODE)=' <<<"$container_env"; then
        bad "Verdict: FAIL identity=smart-routing test overrides are off (test-mode env is set on the omniroute container; remove it and make restart)"
    else ok "Verdict: PASS identity=smart-routing test overrides are off"; fi
    unset container_env
```

The env listing (it can hold keys) stays in a local variable that is only matched by `grep -q` and then unset; nothing from it is printed.

- [ ] **Step 3: Run the tests, then commit**

Run: `node --test 'omniroute-smart/probe/test/*.test.mjs' && uv run pytest tests/test_shell.py -k "smart or fusion or override" -v && bash tools/validate.sh`
Expected: PASS.

```bash
git add omniroute-smart/probe/fusion-24h.mjs omniroute-smart/probe/test/fusion-24h.test.mjs scripts/check-server.sh tests/test_shell.py
git commit -m "feat(check-server): fusion degraded 24 h count and test-override guard"
```

---

### Task 5: Mock extensions for the canary runs

**Files:**
- Modify: `omniroute-smart/spike/mock-upstream.mjs`
- Create: `omniroute-smart/spike/fixtures/modelsdev-mock.json`
- Test: `omniroute-smart/spike/test/mock-upstream.test.mjs` (P1 creates it; if it exists, append only the `test(...)` block below, since its imports are already there and repeating them breaks the module; create the whole file only if it is missing)

**Interfaces:**
- Consumes: P3 Task 11's mock (`POST /v1/systemone` with fixed confident answers, `MOCK_SYSTEM_ONE=down` → 503, `MOCK_OK_TEXT`).
- Produces:
  - models whose name contains `hang` never answer (the socket stays open until the client gives up);
  - `/models` also lists `m-ok2`, `m-fail2`, `m-hang`, `mock-glm-1-flash`, `mock-glm-2-flash`;
  - `MOCK_HANG_PATTERN=<text>` makes any chat whose raw body contains `<text>` hang, so a test can hang the judge call whichever model upstream picks as judge;
  - `MOCK_SYSTEM_ONE_PROFILE=analysis` makes `/v1/systemone` answer `task_kind=plan`, `complexity=high` (confident), the default profile stays P3's;
  - every `/v1/systemone` request logs `{route:"systemone", ua, auth: <boolean>}` (never the header value);
  - `GET /api.json` serves `fixtures/modelsdev-mock.json` (a models.dev-shaped file with provider `llama-cpp` and models `mock-glm-1-flash` (release 2026-09-01, cost 0.2/0.6) and `mock-glm-2-flash` (release 2026-10-01, cost 0.2/0.6)).

- [ ] **Step 1: Write the failing tests**

```js
// omniroute-smart/spike/test/mock-upstream.test.mjs — if the file exists, append only the test() block; the imports below are for a new file
import { test } from "node:test";
import assert from "node:assert/strict";
import { startMock } from "../mock-upstream.mjs";

test("hang models never answer; analysis profile; auth is logged as a boolean; models.dev route", async (t) => {
  process.env.MOCK_SYSTEM_ONE_PROFILE = "analysis";
  const logs = [];
  const orig = console.log;
  console.log = (l) => logs.push(l);
  const srv = await startMock(0);
  t.after(() => { console.log = orig; delete process.env.MOCK_SYSTEM_ONE_PROFILE; srv.closeAllConnections(); srv.close(); });
  const base = `http://127.0.0.1:${srv.address().port}`;
  await assert.rejects(fetch(`${base}/v1/chat/completions`, { method: "POST", body: JSON.stringify({ model: "m-hang" }), signal: AbortSignal.timeout(200) }));
  const so = await (await fetch(`${base}/v1/systemone`, { method: "POST", headers: { authorization: "Bearer secret-123", "user-agent": "llm-env-omniroute-smart/p1.0" }, body: "{}" })).json();
  assert.equal(so.answers.task_kind.choice, "plan");
  assert.ok(logs.some((l) => l.includes('"auth":true') && l.includes("llm-env-omniroute-smart/p1.0")));
  assert.ok(!logs.some((l) => l.includes("secret-123")));
  const md = await (await fetch(`${base}/api.json`)).json();
  assert.ok(md["llama-cpp"].models["mock-glm-2-flash"]);
});
```

- [ ] **Step 2: Implement**

In the request handler, before the chat branch:

```js
    if (req.url === "/api.json") { res.writeHead(200, { "content-type": "application/json" }); res.end(readFileSync(new URL("./fixtures/modelsdev-mock.json", import.meta.url))); return; }
    if (model.includes("hang")) return; // never answer: the fusion hang regression (P4 Task 6)
    if (process.env.MOCK_HANG_PATTERN && raw.includes(process.env.MOCK_HANG_PATTERN)) return; // hang judge-shaped calls (Task 6 row 6)
```

In the `/v1/systemone` branch (from P3 Task 11), log before answering and pick the profile:

```js
      console.log(JSON.stringify({ route: "systemone", ua: req.headers["user-agent"] ?? null, auth: Boolean(req.headers.authorization) }));
      const answers = process.env.MOCK_SYSTEM_ONE_PROFILE === "analysis" ? ANALYSIS_ANSWERS : DEFAULT_ANSWERS;
```

where `DEFAULT_ANSWERS` is P3's fixed answer object (renamed) and `ANALYSIS_ANSWERS` is:

```js
const ANALYSIS_ANSWERS = {
  task_kind: { type: "choice", choice: "plan", answer_confidence: 0.95 }, domain: { type: "choice", choice: "backend", answer_confidence: 0.95 },
  scope: { type: "score", probabilities: { 0: 0.05, 1: 0.9, 2: 0.05 } }, complexity: { type: "score", probabilities: { 0: 0.02, 1: 0.03, 2: 0.95 } },
  second_opinion: { type: "noul", noul: 0.05 },
};
```

Add `"m-ok2", "m-fail2", "m-hang", "mock-glm-1-flash", "mock-glm-2-flash"` to the `/models` list and `readFileSync` to the `node:fs` import. Write `fixtures/modelsdev-mock.json` in the shape P2's `fetchModelsDev` parses (provider key → `{ id, models: { <id>: { id, name, release_date, cost: { input, output }, limit: { context, output } } } }`), with the two models above. Note the order in the handler: parse the body first (the chat branch already does), then the `hang` check.

- [ ] **Step 3: Run the tests, then commit**

Run: `node --test 'omniroute-smart/**/*.test.mjs'`
Expected: PASS.

```bash
git add omniroute-smart/spike
git commit -m "test(spike): mock hang models, System One analysis profile and models.dev route"
```

---

### Task 6: Live canary tests on a throwaway container

This is an execution task run by one subagent. A defect found here is fixed in the owning task's files (or in P1–P3's files, with a new unit test first), and the scenario is re-run. Results go into `docs/prds/omniroute-smart-routing-research/p4-test-report.md`.

**Files:**
- Create: `docs/prds/omniroute-smart-routing-research/p4-test-report.md`

**Interfaces:**
- Consumes: the derived image rebuilt from this branch; P1's `probe.mjs` (API helper) and mock; P2's `ensure_smart_plugin` and `smart-settings`; P3 Task 11's setup steps.
- Produces: one PASS/FAIL row per scenario below, with the evidence (status, `decisions.jsonl` excerpt with no secrets, mock log lines, elapsed time).

- [ ] **Step 1: Build and start (P3 Task 11 pattern, names `-it4`, port 29132)**

Run every step of this task in **one** shell session so variables, `wait_up` and the trap persist.

```bash
set -u
PROD_STARTED_BEFORE="$(podman inspect --format '{{.State.StartedAt}}' omniroute)"; echo "production StartedAt: $PROD_STARTED_BEFORE"
IT4="$(mktemp -d)"; chmod 700 "$IT4"
IT_PW="$(openssl rand -hex 16)"; export IT_PW
# every llmenv call with --config "$TMPCFG" talks to it4 (port 29132, generated password), never to production
TMPCFG="$IT4/models.yml"; yq '.smart_routing.enabled = true | .smart_routing.image = "localhost/llm-env-omniroute-smart:it4"
  | .omniroute.port = 29132 | .omniroute.initial_password = strenv(IT_PW) | del(.server.api_key)' ~/.config/llm-env/models.yml > "$TMPCFG"; chmod 600 "$TMPCFG"
LLM_ENV_CONFIG="$TMPCFG" bash scripts/omniroute-smart-build.sh
cp -r omniroute-smart/strategies "$IT4/strategies"     # a copy, so scenario 9 can break it without touching the repo
node omniroute-smart/spike/mock-upstream.mjs --port 18080 > "$IT4/mock-up.log" 2>&1 & UP=$!
MOCK_SYSTEM_ONE_PROFILE=analysis node omniroute-smart/spike/mock-upstream.mjs --port 18081 > "$IT4/mock-laya.log" 2>&1 & LAYA=$!
MOCK_SYSTEM_ONE_PROFILE=analysis node omniroute-smart/spike/mock-upstream.mjs --port 18082 > "$IT4/mock-remote.log" 2>&1 & REMOTE=$!
{ echo "INITIAL_PASSWORD=$IT_PW"; echo "TYPESAFE_API_KEY=it4-not-a-real-key"
  echo "OMNIROUTE_SMART_TEST_MODE=1"; echo "OMNIROUTE_SMART_TEST_REMOTE_URL=http://host.containers.internal:18082/v1/systemone"
  echo "OMNIROUTE_PLUGIN_SMART_CATALOG_TEST_MODE=1"; echo "OMNIROUTE_PLUGIN_SMART_CATALOG_TEST_MODELSDEV_URL=http://host.containers.internal:18080/api.json"; } > "$IT4/it4.env"
chmod 600 "$IT4/it4.env"
cleanup() { podman rm -f omniroute-smart-it4 >/dev/null 2>&1; podman volume rm -f omniroute-smart-it4-data >/dev/null 2>&1
            kill "$UP" "$LAYA" "$REMOTE" 2>/dev/null; rm -rf "$IT4"; }   # only our own tracked PIDs, never a pattern kill
# Restart a mock and keep its PID current (cleanup kills only $UP/$LAYA/$REMOTE), e.g.:
#   restart_laya MOCK_SYSTEM_ONE_PROFILE=analysis   restart_laya   restart_up MOCK_HANG_PATTERN='Source 1'   restart_up
restart_laya() { kill "$LAYA" 2>/dev/null; wait "$LAYA" 2>/dev/null; env "$@" node omniroute-smart/spike/mock-upstream.mjs --port 18081 >> "$IT4/mock-laya.log" 2>&1 & LAYA=$!; }
restart_up() { kill "$UP" 2>/dev/null; wait "$UP" 2>/dev/null; env "$@" node omniroute-smart/spike/mock-upstream.mjs --port 18080 >> "$IT4/mock-up.log" 2>&1 & UP=$!; }
restart_remote() { kill "$REMOTE" 2>/dev/null; wait "$REMOTE" 2>/dev/null; env "$@" node omniroute-smart/spike/mock-upstream.mjs --port 18082 >> "$IT4/mock-remote.log" 2>&1 & REMOTE=$!; }
trap cleanup EXIT
start_it4() {
  podman run -d --name omniroute-smart-it4 -p 127.0.0.1:29132:20128 -e PORT=20128 -e OMNIROUTE_PLUGINS_DIR=/app/data/plugins \
    -e OMNIROUTE_ALLOW_PRIVATE_PROVIDER_URLS=true --env-file "$IT4/it4.env" -v omniroute-smart-it4-data:/app/data \
    -v "$IT4/strategies:/opt/omniroute-smart/strategies:ro,Z" -v "$PWD/omniroute-smart/spike:/opt/spike:ro,Z" "${1:-localhost/llm-env-omniroute-smart:it4}" >/dev/null
}
wait_up() {
  # the health path P1's spike report proved, else the root
  for _ in $(seq 60); do { curl -sf http://127.0.0.1:29132/api/health || curl -sf http://127.0.0.1:29132/; } >/dev/null 2>&1 && return 0; sleep 2; done
  podman logs --tail 100 omniroute-smart-it4; echo "omniroute-smart-it4 not up after 120 s" >&2; return 1
}
# Phase A (P2 Task 12 pattern): create a manage-scoped key, then restart with it in env, so the plugin works whether or not
# the pinned image trusts loopback (S5). The one-liner is a throwaway-only exception to AGENTS.md's Python rule.
start_it4 && wait_up || exit 1
MGMT="$(uv run python -c "
from pylib.omniroute import _login, _request
t = _login('http://127.0.0.1:29132', '$IT_PW')
print(_request('POST', 'http://127.0.0.1:29132/api/keys', t, {'name': 'it4', 'scopes': ['manage']})['key'])")"
echo "OMNIROUTE_PLUGIN_SMART_CATALOG_MANAGEMENT_KEY=$MGMT" >> "$IT4/it4.env"; unset MGMT
podman rm -f omniroute-smart-it4 >/dev/null && start_it4 && wait_up || exit 1
P() { podman exec -e SPIKE_PASSWORD="$IT_PW" omniroute-smart-it4 node /opt/spike/probe.mjs "$@"; }
# The strategy caches plugin config for settings.pluginConfigTtlSeconds; wait it out after every put-plugin-config.
TTL="$(podman exec omniroute-smart-it4 node -e 'console.log(require("/app/data/smart-routing/settings.json").pluginConfigTtlSeconds ?? 60)' 2>/dev/null || echo 60)"
set_plugin() { P put-plugin-config "{\"name\":\"smart-catalog\",\"config\":$1}" && sleep $((TTL + 2)); }
```

Never echo `$MGMT` or `$IT_PW`. Re-read `TTL` after item 2 below pushes `settings.json`.

Then, with `P` (the P1 API helper):
1. Create two llama-cpp connections to `http://host.containers.internal:18080/v1`, `mock-a` and `mock-b` (P1 Task 10 Step 2 payload). Record their ids as `A` and `B`.
2. Push `settings.json` (P2 Task 12 Step 3 method). First merge the plans and bumps below into `$TMPCFG` with `yq` (they are not in the live config), then render it with `uv run llmenv.py --config "$TMPCFG" smart-settings`:
   - `plans`: `{provider: "llama-cpp", connection: "<A>", cycle: "none", renews: false, expiresAt: "<tomorrow's UTC date, YYYY-MM-DD>", manualRemainingPct: 60}` and `{provider: "llama-cpp", connection: "<B>", cycle: "weekly@<weekday four days ahead>", manualRemainingPct: 30}` (P2 validates `expiresAt` as a date);
   - `bumps`: `[{match: "llama-cpp/mock-glm-*-flash"}]`.
3. Push a hand-written `scorecard.json` (`generated_at` = now) with `Q` 0.8 for every task kind × domain for `llama-cpp/m-ok`, `m-ok2`, `m-fail`, `m-fail2`, `m-hang`, `m-judge`, `mock-glm-1-flash`.
4. Install the plugin with P2's `ensure_smart_plugin` through `uv run llmenv.py --config "$TMPCFG" omniroute smart-plugin` (the temp config points it at it4), then set its config with P1's probe command (method and body shape as S5 recorded) through the helper: `set_plugin '{"systemOneLayaUrl":"http://host.containers.internal:18081","consentTypeSafe":true,"bumpMode":"off"}'`. Every later "set X" in the scenarios uses `set_plugin`, which waits out the strategy's plugin-config cache. Activation starts a sync that rewrites `scorecard.json`: wait until it exists (at most 3 min), then push item 3's scorecard again.

Use the connection-pinned member form for the owner's-case combo: `{kind: "model", providerId: "llama-cpp", model: "m-ok", connectionId: "<A>"}`. P3's `candidates()` keys a member by `providerId/model`; if the pinned image stores members differently, record the stored shape and adapt.

- [ ] **Step 2: Run the scenarios**

Each row: what to do, and the PASS condition. Read the result from the client response, `podman exec omniroute-smart-it4 tail -n 1 /app/data/smart-routing/decisions.jsonl` (strip nothing; it holds no secrets), and the mock logs.

| # | Scenario | PASS when |
|---|---|---|
| 1 | **Owner's case.** Combo `it4-quota` (`strategy: "smart"`) with members `[B: m-ok2, A: m-ok]` (B declared first). The Laya mock runs the default (execute_edit) profile for this row: `restart_laya` (default profile), then `restart_laya MOCK_SYSTEM_ONE_PROFILE=analysis` after the row. | client 200 from `mock:m-ok`; the decision line ranks A first, with `terms.S` < 0.2 for A and 1 for B, whatever the time of day. Worked: A (`cycle: none`, expires at tomorrow 00:00 UTC → T ≤ 24 h, burn prior 0.8·97/720 ≈ 0.108 %/h, h = 60 − 3 = 57) → S ≤ 0.108·24/57 ≈ 0.045; B (weekly, reset at 00:00 UTC four days ahead → T between 72 and 96 h, burn prior 0.8·97/168 ≈ 0.462 %/h, h = 30 − 3 = 27) → S ≥ 0.462·72/27 ≈ 1.23, capped → S = 1 |
| 2 | **Fusion.** Combo `it4-fusion` with `[m-ok, m-ok2]`, analysis profile. | upstream log shows two panel calls then one judge call; lane `fusion`; 3 attempts |
| 3 | **Tools force single.** Same combo, request with one tool. | one upstream call; lane `single`, `fusion.skip` = `not_eligible` |
| 4 | **One survivor.** Combo `[m-ok, m-fail]`. | client 200; lane `fusion_degraded`; `task-stats.json` `fusion_degraded` +1 |
| 5 | **Fusion hang regression.** Combo `[m-ok, m-hang]` with `config.fusionTuning = {panelHardTimeoutMs: 5000, minPanel: 2, stragglerGraceMs: 1000}` and `config.fusionJudgeTimeoutMs: 5000`. | client answers within 15 s (5 + 5 + 5 margin); lane `fusion_degraded`; elapsed recorded |
| 6 | **Judge timeout.** Upstream may pick a surviving panel member as the effective judge whatever `judgeModel` says (research `strategies-fusion.md` §3; S3 recorded which model judged), so hang the judge **call**, not a judge model: `restart_up MOCK_HANG_PATTERN='<judge-prompt marker S3 recorded, e.g. "Source 1">'`, use combo `[m-ok, m-ok2]` with `config.fusionJudgeTimeoutMs: 3000`, then `restart_up` (no pattern). | client 200 within 3 s + panel time + 5 s; lane `fusion_degraded`; `fusion.judge_failed: true`; judge attempt status 504 |
| 7 | **Zero survivors.** Failure cooldowns from row 4 and from 7(a) live in the strategy's memory, so run `podman restart omniroute-smart-it4 && wait_up` before 7(a) and again before 7(b); otherwise the failed members are gated and the row sees `no_candidate_fits` instead. (a) Combo `[m-fail, m-fail2, m-ok]`: panel = the two failing ones only if they rank first (give them `Q` 0.85 in the scorecard for this row, so `m-ok` at 0.8 stays inside the 0.10 quality floor). (b) Combo `[m-fail, m-fail2]`. | (a) the decision line's `attempts` show both panel calls (`m-fail`, `m-fail2`, role `panel`, non-2xx) and then the retry; client 200 from `m-ok`, `fusion.retry` = `llama-cpp/m-ok`; (b) the decision line's `attempts` show both panel calls; `503 fusion_failed`, `task-stats.json` `fusion_failed` +1 |
| 8 | **Dry bump.** Combo `it4-bump` with `[mock-glm-1-flash]`; set `bumpMode: "auto"`. A sync runs only when `sync-state.json` has no success within `syncIntervalHours` (P2 `tick()` answers `idle` otherwise), so first mark the throwaway volume's last success as old: `podman exec omniroute-smart-it4 node -e 'const fs=require("fs"),f="/app/data/smart-routing/sync-state.json";const s=JSON.parse(fs.readFileSync(f,"utf8"));s.last_success_at="1970-01-01T00:00:00.000Z";fs.writeFileSync(f,JSON.stringify(s))'` (never on production), then re-activate the plugin (P2's activate call); poll `bump-audit.jsonl` for at most 6 min (one 5-minute tick plus the sync). For the second sync, mark it old again the same way, re-activate, and wait for `sync-state.json`'s `last_success_at` to change. | the combo becomes `[mock-glm-2-flash, mock-glm-1-flash]`; one audit line; the second sync makes no edit |
| 9 | **`strategy_unavailable`.** `mv "$IT4/strategies/smart.mjs" "$IT4/strategies/smart.mjs.off"`, `podman restart omniroute-smart-it4 && wait_up`, chat on `it4-fusion`. Then move it back, restart, and re-run row 2. | `503` with reason `strategy_unavailable` (the P1 runtime turns a failed module load into that body); row 2 passes again |
| 10 | **Each other 503 reason.** `system_one_unavailable` (stop the Laya mock and `set_plugin '{"consentTypeSafe":false}'`), `no_candidate_fits` (plans at `manualRemainingPct: 2` with a large-scope answer, as P3 Task 11 row 6), `scorecard_missing` (`generated_at` 13 h back), `config_invalid` (`config.qualityFloorDelta: 5`). Restore after each. | each answers `503` with that reason and no upstream chat request in the mock log |
| 11 | **Laya down → consented remote.** Stop the Laya mock; `set_plugin '{"consentTypeSafe":true}'` (with `TYPESAFE_API_KEY` in env and the test URL override). Chat on `it4-fusion`. | client 200; decision `classification.backend` = `typesafe`; the remote mock logged `auth: true` and `ua` = `llm-env-omniroute-smart/<PATCH_VERSION>` |
| 12 | **Forbidden-substring rule inside a smart combo.** (a) P3 Task 11 row 7 re-run on this image (single lane): `restart_laya` (default profile) first, as row 1 does, so the request is not classified for fusion. (b) Fusion lane: `restart_laya MOCK_SYSTEM_ONE_PROFILE=analysis`, add to `it4-fusion` a forbidden-substring rule (the field shape P1 S3 recorded) matching `m-ok2`'s answer text, and send an analysis request. | (a) same fallback as on a `priority` combo; (b) the decision line's `m-ok2` panel attempt is 502 ("tripped the combo rule"), lane `fusion_degraded`, and the client answer does not contain the substring (unless `skip: rules_unavailable` was recorded because P1 found no check: then the request ran single and (a) covers it) |
| 13 | **One decision line per request that reached the strategy.** Before row 1, record today's `decisions.jsonl` line count and `task-stats.json` `requests` sum. Count the client requests sent in rows 1–12 that reached classification and ranking: exclude only row 9's first request (the module never loaded); its re-run of row 2 counts like any row-2 request. Every other request, including row 10's 503s, gets a decision line (P3 logs prelude failures once the strategy has initialised, which row 1 does). | lines added since the start = that count = `requests` added since the start |

Do not loosen a PASS condition to make a row pass. A row that cannot run (for example, the pinned image stores pinned members in a shape `candidates()` cannot read) is recorded as FAIL with the reason, and fixed before Task 9.

- [ ] **Step 3: Clean up, verify production is untouched, commit**

```bash
cleanup; trap - EXIT
podman ps -a --format '{{.Names}}' | grep -c omniroute-smart-it4 || echo "0 it4 containers"
[ "$(podman inspect --format '{{.State.StartedAt}}' omniroute)" = "$PROD_STARTED_BEFORE" ] && echo "production untouched"
podman rmi -f localhost/llm-env-omniroute-smart:it4 >/dev/null 2>&1 || true
```

Expected: `0 it4 containers`, and the production `StartedAt` equals the value recorded before Step 1.

```bash
git add docs/prds/omniroute-smart-routing-research/p4-test-report.md
git commit -m "test(smart): P4 live canary scenarios (fusion, quota gate, 503 reasons, remote fallthrough, dry bump)"
```

---

### Task 7: Upgrade rehearsal

This is an execution task. Results go into `p4-test-report.md`, section "Upgrade rehearsal".

**Interfaces:**
- Consumes: the Task 6 setup (re-run Step 1 of Task 6 in a new session), P1's build script and S2 method.
- Produces: a PASS/FAIL row "upgrade rehearsal", plus the commands the operator guide (Task 8) documents.

- [ ] **Step 1: Resolve the newest `:next` without touching config**

```bash
podman pull docker.io/diegosouzapw/omniroute:next
NEW="docker.io/diegosouzapw/omniroute@$(podman image inspect --format '{{.Digest}}' docker.io/diegosouzapw/omniroute:next)"
```

If `$NEW` equals the pinned `base_image`, record "no newer digest; rehearsal ran the procedure against the pinned digest" and continue with `NEW` = the pinned digest.

- [ ] **Step 2: Old image → state → new image on the same volume**

1. Run Task 6 Step 1 (pinned base) and scenarios 2 and 8 there, so the volume holds combos, plugin state, a scorecard and an audit line.
2. Build the rehearsal image from a second temporary config: `yq '.smart_routing.enabled = true | .smart_routing.base_image = "'"$NEW"'" | .smart_routing.image = "localhost/llm-env-omniroute-smart:rehearsal"' "$TMPCFG" > "$IT4/rehearsal.yml"; chmod 600 "$IT4/rehearsal.yml"; LLM_ENV_CONFIG="$IT4/rehearsal.yml" bash scripts/omniroute-smart-build.sh` (derived from `$TMPCFG`, so it keeps the it4 port and password). Exit 2 means the anchors moved: record the count report, follow P1 Task 11 Step 3 (anchor rewrite within 2 h), and stop the rehearsal until it lands.
3. `podman rm -f omniroute-smart-it4 && start_it4 localhost/llm-env-omniroute-smart:rehearsal && wait_up`, on the same volume.
4. Re-activate the plugin (as `make start` would), then re-run scenario 2 and, for scenario 13, compare just the scenario-2 requests with the lines and `requests` added since a baseline taken right before that run, and `node /opt/omniroute-smart/probe/smart-status.mjs` in the container.

PASS when the build passes, both scenarios pass, the plugin is `active`, and the scorecard age is below the interval.

- [ ] **Step 3: Rollback rehearsal**

`podman rm -f omniroute-smart-it4 && start_it4 localhost/llm-env-omniroute-smart:it4 && wait_up`, re-activate the plugin, re-run scenario 2. PASS when it passes on the old image with the volume the new image wrote. If the new image migrated the database in a way the old one cannot read, record it: the operator guide must then say "back up the data volume before upgrading" as a required step, not an optional one.

- [ ] **Step 4: Clean up and commit**

Run Task 6 Step 3's cleanup, plus `podman rmi -f localhost/llm-env-omniroute-smart:rehearsal`.

```bash
git add docs/prds/omniroute-smart-routing-research/p4-test-report.md
git commit -m "test(smart): upgrade and rollback rehearsal on a throwaway volume"
```

---

### Task 8: Operator guide

**Files:**
- Create: `docs/omniroute-smart-routing.md`
- Modify: `AGENTS.md` (one line in the docs list, if it has one)

**Interfaces:**
- Consumes: the PRD, P1–P4 results, the Makefile targets (`setup`, `start`, `restart`, `check-server`, `smart-sync`, `smart-key`, `smart-probe`).
- Produces: the guide P5 links from `README.md`.

- [ ] **Step 1: Write the guide**

Sections, in this order. Each is short and concrete, with the exact commands and file paths; no section restates the PRD's math beyond what an operator needs to read a log line.

1. **What it is.** Two sentences: the `smart` combo strategy and the `smart-catalog` plugin, delivered in a derived OmniRoute image built by `make setup`.
2. **Enable.** `smart_routing.enabled: true` in `~/.config/llm-env/models.yml` → `make setup` (builds the image) → `make smart-key` → `make restart` → `make smart-sync` → `make smart-probe` → `make check-server`. Say what each prints on success.
3. **Keys and consent.** Keys go in `~/.config/llm-env/omniroute-smart.env` (mode 600): `OMNIROUTE_PLUGIN_SMART_CATALOG_MANAGEMENT_KEY` (written by `make smart-key`; say whether S5's loopback-trust result made it optional), `OMNIROUTE_PLUGIN_SMART_CATALOG_AA_API_KEY` and `TYPESAFE_API_KEY` (typed by the operator). The Zen key is taken from the top-priority `opencode-zen` connection. Consent flags live on the plugin page. The two dashboard paths that reach it: Dashboard → Plugins → smart-catalog → Config, and the plugin's own settings link (use the names P1 S4 / P2 Task 12 recorded). No remote System One is called without both consent and key.
4. **Plan declarations.** The `smart_routing.plans[]` fields (`provider`, `connection`, `cycle`, `renews`, `expiresAt`, `manualRemainingPct`, `metered`) with one example per cycle kind, and when to declare a plan (no quota API, or to override it).
5. **Bumps.** The `smart_routing.bumps[]` rule shape, `bumpMode`, and how to read `bump-audit.jsonl`.
6. **Combos.** How to create a `smart` combo through the API (and, per S4, whether the dashboard editor is safe), the combo-config keys an operator is likely to set (`qualityFloorDelta`, `judgeModel`, `fusionTuning`, `fusionJudgeTimeoutMs`, `minHeadroomPct`), and that OmniRoute's own fallback and forbidden-substring / HTTP-200 error-text rules apply in both lanes: the single lane runs through the combo loop itself, and in the fusion lane every panel, judge and retry answer goes through OmniRoute's own rule check (P1's `checkComboResponse`, or `handleSingleModel` when it applies them), a flagged answer counting as a failed call. If neither was reachable, fusion stays off (`skip: rules_unavailable`). Use the same wording as the PRD v2.1 note (Task 9).
7. **Reading `decisions.jsonl`.** One annotated real line from `p4-test-report.md` (single lane) and one fusion line, plus a `jq` one-liner per common question: why did this request pick X, what dropped Y, how many fusion requests degraded today.
8. **Errors.** A table of the six 503 reasons: what causes each, and the first thing to check.
9. **Health.** Every `smart-routing` row of `make check-server`, what red means, and the fix.
10. **Upgrade.** From Task 7: `make backup` saves combos and connection metadata only, so also back up the data volume: the volume name from `podman inspect --format '{{range .Mounts}}{{if eq .Destination "/app/data"}}{{.Name}}{{end}}{{end}}' omniroute`, then `podman volume export <volume> > <backup dir>/omniroute-data.tar` (mode 600; it holds encrypted provider keys). Then set the new digest in `smart_routing.base_image`, `make setup` (exit 2 = anchors moved: stay on the pinned digest), `make restart`, `make check-server`.
11. **Roll back.** Set `smart_routing.base_image` back to the previous digest (or `smart_routing.enabled: false` to return to the stock image), `make setup && make restart`, and, if Task 7 found the database not backward-compatible, restore the volume: `make stop`, `podman volume import <volume> <backup dir>/omniroute-data.tar`, `make start`.
12. **Not for production.** One paragraph: the `*_TEST_MODE` variables exist for canary tests only, and `make check-server` fails if they are set.

- [ ] **Step 2: Check it against the code**

For every command and file path in the guide, run `rg -n "<target or path>" Makefile scripts pylib omniroute-smart docker` and confirm it exists. Run `bash tools/validate.sh` (the repo's shell and Python checks; the guide itself is verified by the `rg` sweep above).

- [ ] **Step 3: Commit**

```bash
git add docs/omniroute-smart-routing.md AGENTS.md
git commit -m "docs: OmniRoute smart routing operator guide"
```

---

### Task 9: PRD notes and final verification

- [ ] **Step 1: Record in the PRD (v2.1)**
  - **Single vs fusion:** fusion runs from the prelude; the panel prefers different providers; the judge is told apart by call order (or the S3 marker); a failed judge returns the first good panel answer as `fusion_degraded`; the outer deadline is `panelHardTimeoutMs + fusionJudgeTimeoutMs + 5 s`; a successful zero-survivor retry is logged as `fusion_degraded` with `fusion.retry`; every panel call is bounded by `panelHardTimeoutMs` (a hung member is a recorded 504); panel, judge and retry calls carry their candidate's connection. P3's decision-log sweep age (`SWEEP_AGE_MS`) covers the longest legal fusion run (2 × panel + judge + margin at the 600 s maxima), so a slow fusion request is never logged as `unfinished` and its real lane, retry and attempts are kept.
  - **Decision log:** `outcome.fusion` (`panel`, `judge`, `judge_source`, `survivors`, `judge_failed`, `retry`, `timed_out`, `threw`, `skip`) and attempt `role`.
  - **Judge fallback:** the panel's first member has passed every gate, so the PRD's "if neither can judge, run single on the top 1" branch cannot occur; a missing `handleFusionChat` runs single with `fusion.skip = "no_fusion_handler"` instead.
  - **Effective judge:** record which model judged in S3 and Task 6 row 2 (upstream may pick a surviving panel member regardless of `judgeModel`); if so, `judgeModel` and its gating only steer upstream's choice.
  - **Combo-loop rules in the fusion lane ("OmniRoute behaviour kept"):** the forbidden-substring / HTTP-200 error-text rules are inherited, not reimplemented: every fusion upstream answer goes through OmniRoute's own check (`ctx.checkComboResponse`, or `handleSingleModel` when P1 S3 found it applies them), and a flagged answer is a failed call, as in the combo loop. Without either, fusion is off (`skip: rules_unavailable`). Keep the guide (Task 8 item 6) in the same words. Fusion `attempts` come from the wrapper, not `observe()`.
  - **Testing:** the two test-mode overrides and the check-server guard.
  - **Health:** the fusion-degraded 24 h row.

  Add a clarification row: `17 | P4 implementation: …`.

- [ ] **Step 2: Full verification**

Run: `bash tools/validate.sh && bash tools/test.sh && node --test 'omniroute-smart/**/*.test.mjs' && node omniroute-smart/strategies/smart/bench.mjs`
Expected: everything passes.

Confirm `p4-test-report.md` has a PASS for every row of Task 6 and Task 7. Any FAIL left blocks P5: report it instead of committing.

- [ ] **Step 3: Commit**

```bash
git add docs/prds
git commit -m "docs(prd): record P4 fusion lane and canary-test details"
```

---

## Self-Review Notes (author)

**Spec coverage (PRD, P4 scope):**

| PRD item | Task |
|---|---|
| Fusion conditions (analysis kind, complexity or second opinion, confidence, not tool-bearing, two candidates) | 3 |
| Panel = top 2 by U, preferring different providers | 2 |
| Judge = `judgeModel` if it passes the gates, else panel[0]; single on top-1 otherwise | 3 (panel[0] has already passed every gate, so the "neither can" branch cannot occur; `no_fusion_handler` covers a missing handler) |
| `fusionTuning` defaults passed through | 2 |
| Judge timeout `fusionJudgeTimeoutMs`; worst case panel + judge | 2, 6 (row 6) |
| One survivor kept as upstream does; `fusion_degraded` logged and counted | 2, 4, 6 (row 4) |
| Zero survivors: one single retry, else `503 fusion_failed` | 2, 6 (row 7) |
| Regression test for the fusion hang | 2, 6 (row 5) |
| Live tests: owner's case with declared plans, each 503 reason, Laya → consented remote, dry bump, upgrade rehearsal | 6, 7 |
| Operator guide (plugin setup and two dashboard paths, plan declarations, reading `decisions.jsonl`, upgrade, rollback) | 8 |
| check-server: 24 h `fusion_degraded` count | 4 |

**Type consistency:** `runFusion`'s `fusion` object is defined in Task 2 and extended with `judge_source` / `skip` in Task 3; Task 4 reads only `outcome.lane`; Task 8 documents the same field names.

**Left to P5:** enabling on this host, seeding the real combos, pointing clients, the 24 h timer check, the 30-day metrics, the upstream PR drafts and the backlog.
