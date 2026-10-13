# Spike: custom OmniRoute routing strategy without a hard fork

Date: 2026-10-13. Image: `docker.io/diegosouzapw/omniroute:next` (package 3.8.52, created 2026-10-07 08:55 UTC, no git SHA label, Next 16.3.6, Node 26.10.0, 1.52 GB).
Upstream checked: `release/v3.8.52` branch HEAD `b0d53251` (shallow clone in `prd-research/spike-src/`).
Spike artifacts: `prd-research/spike/patch-strategies.mjs`, `prd-research/spike/strategies/spike-reverse.mjs`, `prd-research/spike/probe*.mjs`.

## TL;DR

- Runtime is compiled Next.js standalone output (webpack, minified). `src/` and `open-sse/` in the image are partial leftovers (124 `.ts` files, no `combo.ts`) and are not on the request path.
- Patching the compiled JS at start **works end to end** (tested in a throwaway container): a combo with `strategy: "spike-reverse"` was accepted by the API (201; a bogus name still gets 400) and the request path ran the external module, which reordered the targets.
- It needs **2 anchors** (a regex on the strategy-list literal, 48 matches in 47 files, and a regex on the head of `applyStrategyOrdering`, 2 matches). Both are string/shape anchors, not minified names.
- **Upstream is moving under this right now.** Commit `4898a669` (2026-10-10, #16206, "rail 3.8.52 R0.4a") replaced the if/else dispatch with a typed `strategyRegistry.ts` + `STRATEGY_ORDERERS` map. The current `:next` image (built 2026-10-07) predates it, so the dispatch anchor **will miss on the next image pull**. The patch script fails loudly (exit 2) when that happens, which is the breakage detector we want. The registry file says: *"Internal on purpose: the public plugin extension point is planned for v4."* That is the hook for the upstream PR.
- Upstream also added (2026-10-10, `92827c65`, #13051) the `nadir` auto-router strategy with an operator-configurable `baseUrl`, an **HTTP decision hook** that needs no patch at all (details in 2c). It is not in the current image either.

## 1. What executes at runtime

Evidence (all read-only on the live `omniroute` container):

- Entrypoint `/app/check-permissions.sh` → `exec node dev/run-standalone.mjs` → spawns `server-ws.mjs` (WS wrapper around Next's standalone `server.js`). `/proc/1/cmdline` = `node dev/run-standalone.mjs`.
- `server.js` embeds `nextConfig` with `"distDir":"./.build/next"`. Compiled output is in `/app/.build/next` (413 MB): `server/app/**/route.js`, `server/chunks/*.js`, `server/webpack-runtime.js`, `static/chunks/*`. Module IDs are numeric (`592573:(a,b,c)=>{...}`), so this is **webpack**, not Turbopack. The Dockerfile pins `OMNIROUTE_USE_TURBOPACK=0` for images, although the repo default is Turbopack.
- No `tsx`/`ts-node` (`node_modules/.bin` is absent). `src/` (1.3 MB) and `open-sse/` (5.1 MB) hold 124 `.ts` files: executors, workers, mitm handlers, SQL migrations (outputFileTracing leftovers / worker entrypoints). `open-sse/services/combo/` contains only `roundRobinCombo.ts`, with no `combo.ts`, `applyStrategyOrdering.ts`, or `routingStrategies.ts`. Editing `src/` in the image has **no effect** on routing.

Where the strategy pieces land in the compiled output:

| Source | Compiled form | Copies |
|---|---|---|
| `ROUTING_STRATEGY_VALUES` (`src/shared/constants/routingStrategies.ts`) | webpack module **592573** `let d=["priority","weighted","round-robin",…,"pipeline"]`, exported as `gG` | Inlined textually into **44 server files** (39 `app/**/route.js` + chunks) with the same module ID, plus module **529032** (1 file, other layer), plus 1 client static chunk. 47 files total contained the literal. One webpack runtime → evaluated once per process, but every copy must be patched because whichever route loads first wins. |
| zod `z.enum(ROUTING_STRATEGY_VALUES)` (combo/settings/MCP schemas) | references `592573.gG` → picks up the patched array automatically | — |
| `ROUTING_STRATEGIES` UI option objects (`labelKey:"quotaWeighted"`) | `static/chunks/28948-*.js`, `combos/page-*.js`, server `combos/page.js`, `chunks/57596.js` | not patched in the spike |
| Combo strategy dispatch (`applyStrategyOrdering`, old if/else chain in this image) | `async function o(a,b,d){let{combo:n,config:o,body:p,log:q,apiKeyAllowedConnections:r,sessionKey:s}=d,t=b,u=null;if("lkgp"===a)…else if("quota-weighted"===a){…}…return{orderedTargets:t,quotaShareRelease:u}}` | **2 modules**: 514186 in `chunks/32948.js`, 434847 in `chunks/45121.js` (two webpack layers) |
| `"reset-aware"` string anywhere | 188 files (i18n, docs, schemas, UI), so not a useful count | — |

Conclusion: the constant is duplicated into many files but is one tiny, highly regular literal (one regex). The dispatch is a single function in 2 copies (one regex). The patch is feasible, with a small surface.

## 2. Options ranked

### Rank 1 (short term): a', a thin derived image that patches the compiled JS at **build** time
`FROM docker.io/diegosouzapw/omniroute:next` + `COPY strategies/ /opt/omni-strategies/` + `COPY patch-strategies.mjs` + `RUN node /opt/patch-strategies.mjs` (the same script as option a, run during `podman build`).
- Effort: about 1 day, including the canary and a Makefile target. The build takes seconds (the patch runs in under 1 s over the 413 MB tree) and adds about 0 MB.
- Fragility: high per upstream release on the dispatch anchor (see the registry refactor), low on the list anchor. **Every break surfaces at `podman build` as exit 2**, never as a silently mis-routing prod container.
- Health check: (1) build fails on anchor miss; (2) at runtime, a canary combo with the custom strategy plus the log line `[<name>] custom ordering` (or `usage_history.combo_strategy = '<name>'`); (3) `POST /api/combos` with the custom strategy must return 201 (a 400 "Invalid option: expected one of…" means the list patch is gone).

### Rank 2: a, the same patch at **container start** (wrapper entrypoint)
Proven in the spike (section 3). It is equivalent to a', but a break is found at boot and the container must decide between refusing to start and starting unpatched. It also needs a writable `/app` (true today: uid `node` owns `/app/.build`; it breaks under `--read-only`). Prefer a'. A variant that needs no disk writes: a `--import` preload using `module.registerHooks` to transform chunk sources in memory (untested).

### Rank 3 (the upstream end state): b, rebuild from source with a loader patch
- After #16206, the source patch is small and well-shaped: (1) `routingStrategies.ts`: merge `OMNIROUTE_STRATEGIES_DIR` names into the list (or widen `comboStrategySchema` to `z.enum(...).or(z.string().regex(/^x-[a-z0-9-]+$/))`); (2) `strategyRegistry.ts`: `getStrategyTraits()` falls back to `DEFAULT_STRATEGY_TRAITS` or plugin-declared traits; (3) `applyStrategyOrdering.ts`: look up a `registerComboStrategy(name, {traits?, order(targets, deps)})` map before `STRATEGY_ORDERERS`; (4) the loader: dynamic `import()` of `*.mjs` at boot; (5) UI: a `ROUTING_STRATEGIES` entry or a generic "custom" option, plus i18n keys; (6) the known-symbols gate test `tests/unit/combo/strategy-registry.test.ts` / `check-known-symbols.ts`, which enforces canonical == registered.
- Cost: the Dockerfile notes **~17 min webpack / ~9–12 min Turbopack on a 32-core box** with a large heap (`OMNIROUTE_BUILD_MEMORY_MB`, OOM history), and the image is about 1.5 GB. `:next` has no git SHA label, so the rebuild is from a moving `release/v3.8.52` branch and is not byte-identical to what runs. As a local carry-patch this is a soft fork; as an **upstream PR** it is the right end state, and the upstream comment says it is "planned for v4".

### Rank 4: c, existing extension points (no patch)
- **`auto` + `config.routerStrategy: "nadir"` + `config.nadir.baseUrl`** (upstream `open-sse/services/autoCombo/nadirStrategy.ts`). OmniRoute POSTs `{prompt (last user msg, ≤16k chars), menu:[model ids ≤100], source}` to `<baseUrl>/v1/bucket` and expects `{selected_model, bucket?, confidence?}`. A self-hosted decision service is therefore a custom *model chooser*. Limits: model only (the account is still chosen by `rules`); `auto` combos only; fail-open with a 2 s default timeout and a 30 s cooldown after a failure; private URLs are allowed by default (`getProviderOutboundGuard` → `OMNIROUTE_ALLOW_LOCAL_PROVIDER_URLS` default on). **Not in the current image** (bundle has 0 hits for `NadirStrategy` / `/v1/bucket`); it arrives with the next pull. `routerStrategy` is `z.string()`, and an in-process `registerStrategy()` exists (documented in `docs/routing/AUTO-COMBO.md`), but nothing loads external code into it.
- **Plugins** (`OMNIROUTE_PLUGINS_DIR`, present in the current image): child-process IPC hooks `onRequest/onResponse/onError`. `onRequest` runs in `chatCore` **after** the provider/target is chosen (`open-sse/handlers/chatCore.ts:538`), so it can block or rewrite the body but **cannot select targets**. Not a strategy extension point.
- **External job rewriting combo order/weights** via `PUT/PATCH /api/combos/[id]` or `/api/combos/reorder` on a `priority`/`weighted` combo. It works today with no patch, but the decision is per period, not per request. It also writes the DB on every tick and races with dashboard edits. Health check: GET the combo and compare its order to what was last written.

### Rank 5: d, an external OpenAI-compatible sidecar in front of OmniRoute
Full per-request control with zero coupling to OmniRoute internals, and no breakage on update other than the public API. Costs: a second hop (latency, streaming passthrough), a duplicated API-key/auth surface, and OmniRoute's dashboard/analytics no longer showing the strategy. The sidecar must call combos or models by name and handle fallback itself, or delegate fallback by calling a `priority` combo. It is a good fallback if the patch route is rejected, and overkill for an ordering tweak.

## 3. Throwaway container experiment (≈1 min wall time, 11:17:06–11:17:52 UTC)

The CMD was overridden with `sleep infinity` so the patch could run before the server started. Otherwise it used the exact image, name, and port, with no volumes and no secrets.

```
$ podman run --rm -d --name omniroute-spike -p 127.0.0.1:29128:20128 docker.io/diegosouzapw/omniroute:next sleep infinity
$ podman exec -u 0 omniroute-spike mkdir -p /opt/omni-strategies
$ podman cp spike/strategies/spike-reverse.mjs omniroute-spike:/opt/omni-strategies/
$ podman cp spike/patch-strategies.mjs omniroute-spike:/opt/patch-strategies.mjs
$ podman exec omniroute-spike node /opt/patch-strategies.mjs
{"names":["spike-reverse"],"lists":48,"dispatch":2,"files":47,"already":0}
$ podman exec omniroute-spike node /opt/patch-strategies.mjs          # idempotent
{"names":["spike-reverse"],"lists":0,"dispatch":0,"files":0,"already":47}   exit 0
$ podman exec -d omniroute-spike sh -c 'cd /app && node dev/run-standalone.mjs > /tmp/server.log 2>&1'
  /healthz -> 200 within ~4 s
# inside the container, loopback (fresh install = no login on loopback):
POST /api/combos {strategy:"spike-reverse", models:[openai/gpt-4o-mini, anthropic/claude-haiku-4-5, groq/llama-3.1-8b-instant]} -> 201
POST /api/combos {strategy:"not-a-strategy"} -> 400 "Invalid option: expected one of "priority"|…"
POST /api/combos {strategy:"priority"} -> 201
POST /api/keys {name:"spike"} -> 201
POST /v1/chat/completions model=spike-custom -> 503 ALL_TARGETS_SKIPPED (expected: no provider creds)
server.log:
  CHAT  Combo "spike-custom" [spike-reverse] with 3 models
  COMBO spike-reverse with nested resolution: 3 total targets
  COMBO [spike-reverse] custom ordering: groq/llama-3.1-8b-instant > anthropic/claude-haiku-4-5 > openai/gpt-4o-mini
# anchor-miss simulation (tree without the dispatch function):
$ node /opt/patch-strategies.mjs /app/.build/next/static
{"names":["spike-reverse"],"lists":1,"dispatch":0,"files":1,"already":0}   exit=2
$ podman rm -f omniroute-spike     -> removed; `podman ps -a --filter name=omniroute-spike` empty
```

What the patch does:
- List anchor `/\["priority","weighted","round-robin"(?:,"[a-z0-9-]+")*\]/` → append the custom names (read from `OMNIROUTE_STRATEGIES_DIR/*.mjs` at patch time, because zod enums are built at module eval).
- Dispatch anchor `/async function (\w+)\((\w+),(\w+),(\w+)\)\{let\{combo:(\w+),config:/` → prepend an early return. For a custom name, `await import(file://DIR/<name>.mjs)` (cached on `globalThis`) and `order([...targets], deps)` → `{orderedTargets, quotaShareRelease:null}`. On error it logs a warning and falls back to the declared order.
- The marker comment `/*omni-custom-strategies*/` makes the patch idempotent.

Not verified: the dashboard combo editor with a custom strategy (the UI option list is not patched, and one client chunk also carries the list literal; editing/saving in the UI may reset or reject the strategy); a successful upstream call (no creds); interaction with `auto`/`weighted`/round-robin-specific traits (custom names take the default branches).

## 4. Recommended Phase-1 go/no-go spike for the PRD

**Experiment:** build a thin derived image (`FROM omniroute:next`, run `patch-strategies.mjs` at build) with one real custom strategy module. Run it **beside** prod (not in place) on a test port with a copy of a non-secret config. Then repeat the build against the **first `:next` image that contains #16206 (strategy registry)** and #13051 (nadir).

Pass criteria (all required):
1. `podman build` succeeds on the current `:next`, and the patch reports `dispatch == 2` (count of applyStrategyOrdering copies) and `lists >= 44`.
2. `POST /api/combos` with the custom strategy → 201, and with a bogus name → 400 (the list patch is live and validation is intact).
3. A real request through a canary combo (a free or cheap model, 2+ targets) succeeds, and the logs or `usage_history.combo_strategy` show the custom strategy ordered the targets. With the first target forced to fail, fallback proceeds to target 2 (OmniRoute's attempt loop is still intact).
4. On the registry-era `:next`: **either** the build fails with exit 2 (the detector works) **and** an updated anchor (the `applyStrategyOrdering` head / `getStrategyTraits`) is written in ≤2 h, **or** it passes unchanged.
5. p95 overhead of the hook < 1 ms (one `Array.includes` per request on built-in strategies).
6. Built-in strategies (`priority`, `quota-weighted`) behave identically on the patched image: same first target over N=20 requests.

Fail / no-go triggers: the anchor needs more than 2 sites, or rewriting needs minified identifiers; a registry-era rewrite takes more than half a day; or the dashboard editor corrupts custom-strategy combos on save, with no workaround of API-only management. On no-go: use `nadir` + a self-hosted `/v1/bucket` service (option c) if model-level choice in `auto` combos is enough, else the sidecar (option d). Either way, open the upstream "pluggable strategies" PR against `strategyRegistry.ts` (option b), since upstream already plans it for v4.
