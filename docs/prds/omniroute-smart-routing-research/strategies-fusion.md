# OmniRoute spike: combo strategies, fusion, plugins, background syncs

Sources: an upstream shallow clone at `omniroute-src/` (HEAD `b0d53251`, 2026-10-13, package.json `3.8.52`), plus read-only greps against the running container `omniroute` (`/app/.build/next/server/chunks/*.js`, compiled only, no TS source) and `podman logs`.
All paths below are relative to the repo root unless they are marked "container".

## 0. Version discrepancy (important)

- Upstream has no `v3.8.52` tag. The newest tags are `v3.8.50` and `v3.8.51`. Main is already labelled 3.8.52 (the "rail 3.8.52" work).
- The running image `diegosouzapw/omniroute:next` was built 2026-10-07. Container `/app/package.json` says `"version": "3.8.52"`.
- The **strategy registry refactor landed after the image was built**: `strategyRegistry.ts` was added in commit `4898a669` on 2026-10-10 ("route strategy-specific branching through a typed strategy registry (rail 3.8.52 R0.4a) (#16206)").
  - The running build still uses literal comparisons. Container chunk `32948.js` contains `if("fusion"!==e&&(g||s)&&f.warn(...)...,"fusion"!==e)return null;`.
  - `grep usesRoundRobinLoop|getStrategyTraits` finds nothing in the container chunks.
  - So the upstream-main answers to Q1 describe the *next* image, not the one running now.
- Behaviours confirmed present in the running build: the fusion tool-bypass string (`bypassing panel synthesis`), `plugin_block`, `hooks.boot_failed` (lazy plugin boot), `Quota-weighted ordering`, and the arena/models.dev syncs (both appear in the logs).

## 1. Strategy implementation and dispatch

### Constants
`src/shared/constants/routingStrategies.ts`:
- L1-22: `ROUTING_STRATEGY_VALUES` holds 20 public values.
- L32: `INTERNAL_ROUTING_STRATEGY_VALUES = ["quota-share"]`.
- L71-82: `normalizeRoutingStrategy`. An unknown value becomes `"priority"`.
- L94-232: `ROUTING_STRATEGIES`, the UI option list (`labelKey`, `combosDescKey`, `settingsDescKey`, `icon`).

### Dispatch flow (upstream main)
`open-sse/services/combo.ts` `handleComboChatInner` (L686+) dispatches in this order (comment at L742):
1. Pinned context-cache model: `tryPinnedModelDispatch`, L747.
2. Fusion: `tryFusionDispatch`, L763.
3. Chaos: `dispatchChaosFromCombo`, L792.
4. Pipeline: `tryPipelineDispatch`, L808.
5. Nested combo-ref runtime units: `tryRuntimeUnitDispatch`, L822.
6. Round-robin handler, chosen when `getStrategyTraits(strategy).usesRoundRobinLoop` is true, L856.
7. Common pipeline: `resolveComboTargetPipeline` (L887) produces an ordered target list, then the attempt loop runs over it.

### Registry / extension point
There is an **internal, typed, compile-time registry**. There is **no runtime plugin registry**.
- `open-sse/services/combo/strategyRegistry.ts`:
  - L13: "Internal on purpose: the public plugin extension point is planned for v4."
  - L117: `STRATEGY_TRAIT_OVERRIDES: Record<AnyRoutingStrategyValue, StrategyTraitOverrides>`. A missing key is a compile error.
  - L40-82: the traits are `ordering`, `dispatchPrelude: "none"|"fusion"|"pipeline"`, `usesRoundRobinLoop`, `weightedSteps`, `stickyPin`, `runtimeUnitOrder`, `inflightReservation`, `smartPipeline`, `contextRelay`, and others.
  - L203: `getStrategyTraits()`. An unknown string gets `DEFAULT_STRATEGY_TRAITS`.
- `open-sse/services/combo/applyStrategyOrdering.ts`:
  - L265-337: `STRATEGY_ORDERERS: Record<Exclude<StrategyOrderingKind,"declared"|"auto">, StrategyOrderer>`. This is the name→function map.
  - L348-358: `applyStrategyOrdering` looks up `getStrategyTraits(strategy).ordering`.
  - Orderer signature (L51): `(orderedTargets: ResolvedComboTarget[], deps: {combo, config, body, log, apiKeyAllowedConnections, sessionKey}) => Promise<{orderedTargets, quotaShareRelease}>`.
- `auto` is handled separately in `resolveAutoStrategy.ts` (`resolveAutoStrategyOrder`).
- Fusion and pipeline are handled by `dispatchPrelude.ts` (`tryFusionDispatch` L387, `tryPipelineDispatch` L566).
- `open-sse/services/combo/strategyDispatch.ts` L55: `HANDLED_COMBO_STRATEGIES` is derived from the registry. It is used by the `scripts/check/check-known-symbols.ts` gate, so the gate needs no edit.
- No dynamic import per strategy, except that round-robin lazy-imports `roundRobinCombo.ts` (combo.ts L857).

### Places to change to add one new strategy (upstream main)

Required:
1. `src/shared/constants/routingStrategies.ts`: add the value to `ROUTING_STRATEGY_VALUES` and add an entry to `ROUTING_STRATEGIES` (this drives the UI select).
2. `open-sse/services/combo/strategyRegistry.ts`: add an entry to `STRATEGY_TRAIT_OVERRIDES`. This is compile-enforced. If the strategy needs a new ordering kind, also extend the `StrategyOrderingKind` union (L22).
3. `open-sse/services/combo/applyStrategyOrdering.ts`: add an orderer to `STRATEGY_ORDERERS`. This is compile-enforced once the new ordering kind exists.
   - Alternative, for a non-ordering strategy such as a per-request fusion/single decision: extend `dispatchPrelude` in the registry, add a `tryXDispatch` in `combo/dispatchPrelude.ts`, and call it in `combo.ts` `handleComboChatInner` near L763.
4. `src/i18n/messages/en.json`: add the label and desc keys. They exist in two namespaces: the combos block (around L3461) and the settings block (around L7369). Other locales are optional. Assumption, UNVERIFIED: missing locales fall back to English or to the key.

Automatic, no edit needed:
- Zod validation derives from the constant:
  - `src/shared/validation/schemas/combo.ts` L77 `comboStrategySchema = z.enum(ROUTING_STRATEGY_VALUES)`
  - `src/shared/schemas/validation.ts` L42
  - `open-sse/mcp-server/schemas/tools.ts` L140 and L672
- New strategy config keys are accepted because `comboRuntimeConfigSchema` is `.passthrough()` (combo.ts schema L170/L309). Adding typed keys is optional.
- DB: there is no CHECK constraint. `combos.data` is a JSON TEXT blob (`src/lib/db/migrations/001_initial_schema.sql` L66-72), and `usage_history.combo_strategy` is free TEXT (`062_*.sql`).

Optional or cosmetic:
- `src/app/(dashboard)/dashboard/combos/page.tsx`: `STRATEGY_GUIDANCE_FALLBACK` (L123) and `STRATEGY_RECOMMENDATIONS_FALLBACK` (L254) are untyped literals. The fusion config fields are also gated by `strategy === "fusion"` (around L4563).
- `src/app/(dashboard)/dashboard/combos/live/nodes/StrategyNode.tsx` `STRATEGY_COLORS`. It falls back to grey.
- Group-B connection-aware expansion membership. It is duplicated in two places:
  - `open-sse/services/combo/connectionAwareExpansion.ts` L30-46
  - `src/app/(dashboard)/dashboard/combos/comboRrLegibility.ts`
- `src/app/(dashboard)/dashboard/settings/components/ComboDefaultsTab.tsx` L123 maps `ROUTING_STRATEGIES`, so it picks up the new value automatically.

**Count: 4 required files** (constants, registry, orderer or prelude plus `combo.ts`, and en.json), plus about 4 optional UI/list files.

On the running pre-registry build the same change instead means adding literal `strategy === "x"` branches in combo.ts and its slices. That count is UNVERIFIED for the old layout.

## 2. Inputs available at selection time

Every orderer receives `ApplyStrategyOrderingDeps` (`applyStrategyOrdering.ts` L42-49): `combo`, `config` (combo config JSON), `body` (the full request, including `messages` and `tools`), `log`, `apiKeyAllowedConnections`, and `sessionKey`.

The target shape is `ResolvedComboTarget` (`open-sse/services/combo/types.ts` L221-244): `modelStr`, `provider`, `providerId`, `connectionId`, `weight`, `label`, `prompt`, `fallbackOnlyOnQuotaExhaustion`, `trafficType`, and `pinnedFingerprint`.

### Quota telemetry

**reset-aware and reset-window** (`combo/quotaStrategies.ts` L513, L551) use per-connection live fetchers.
- The fetcher is `getQuotaFetcher(provider)` (`quotaPreflight.ts`), with an optional TTL/stale cache (`fetchResetAwareQuotaWithCache`, L362).
- The quota object has these windows: `window5h`, `window7d`, `windowWeekly`, `windowMonthly`, each `{percentUsed, resetAt}` (`combo/quotaScoring.ts` L172-179). There is also a `windows` map, plus `limitReached` and `resetAt`.
- `RESET_WINDOW_NAMES = ["weekly","session","monthly"]` (`types.ts` L13).
- reset-aware score (`scoreResetAwareQuota`, quotaScoring.ts L345):
  - It is a weighted session (5h) and weekly remaining plus reset urgency, with monthly blended in if present.
  - `limitReached` scores −∞. No data scores 0.5.
  - Config keys: `resetAwareSessionWeight`, `resetAwareWeeklyWeight`, `resetAwareTieBandPercent`, `resetAwareExhaustionGuardPercent`, `resetAwareQuotaCacheTtlMs`, `resetAwareQuotaCacheMaxStaleMs` (L67-93).
- reset-window sorts by time-to-reset of the configured windows (`resetWindowWindows`, `resetWindowTieBandMs`; L95-118, L569-600).

**headroom** (quotaStrategies.ts L633-700): headroom = 1 − max(util_5h, util_7d). It reads `src/lib/quota/saturationSignals.ts`, which takes Codex/Bailian fetchers, the Claude `/api/oauth/usage` plan windows, and rate-limit token headers.

**quota-weighted** (quotaStrategies.ts L772+): reads the in-process quota cache.
- `getQuotaWeightedRemainingPercent(connectionId, model)` and `getQuotaSnapshotFetchedAt` (`src/domain/quotaCache.ts` L998, L1029).
- Cache entry type `QuotaCacheEntry {quotas: Record<name, QuotaInfo{remainingPercentage, resetAt, windowSeconds, fractionReported}>, modelQuotas, fetchedAt, exhausted, nextResetAt}` (`src/domain/quotaCacheState.ts` L19-43).
- Snapshot max age is 10 minutes (`QUOTA_WEIGHTED_MAX_SNAPSHOT_AGE_MS`, L67).
- It also reads in-flight counts (`quotaShareInflight.ts`).

### Circuit breaker and lockout
- `getCircuitBreaker(provider).getStatus().state`, with states CLOSED/DEGRADED/OPEN/HALF_OPEN (`src/shared/utils/circuitBreaker.ts`). It is read by:
  - `targetSorters.ts` L130 (p2c)
  - `quotaStrategies.ts` L489 and L791
  - `targetResolution.ts` L172 (pre-filter)
- `isModelLocked` and `getModelLockoutInfo` from `accountFallback.ts` are used in `targetResolution.ts` L188 and `executeTargetGates.ts` L260.

### Pricing
cost-optimized calls `sortTargetsByCost` (`targetSorters.ts` L62-80), which uses `getPricingForModel(provider, model).input`. That function lives in `src/lib/db/settings/pricing.ts` L135 and is cached via `getCachedPricing`. Layers merge as defaults → LiteLLM (`pricing_synced`) → **models.dev (`models_dev_pricing`)** → user (L6-22).
- Optional `config.manifestRouting` adds `generateRoutingHints` over `messages` and `tools` (applyStrategyOrdering.ts L146-188).

### Other strategies
- **lkgp**: `getLKGP(combo.name, combo.id)` from `@/lib/db/settings` (applyStrategyOrdering.ts L64-117).
- **least-used and p2c**: `getComboMetrics` (in-process).
- **auto**: `buildAutoCandidates` (combo.ts L320+). It combines:
  - pricing
  - `getModelLatencyStats` (p95, stddev, errorRate)
  - breaker state
  - quota fetcher (remaining % and reset-window affinity)
  - `getTaskFitness` (Arena ELO / models.dev tier)
  - intent from `classifyWithConfig(prompt)` (`resolveAutoStrategy.ts` L245)

  The factors are listed in `autoCombo/scoring.ts` L11-39: quota, health, costInv, latencyInv, taskFit, stability, tier, context/cache affinity, resetWindowAffinity, quality, and reliability.

## 3. Fusion end to end

- **Config**: fusion is configured per combo in `combo.config`. It uses `judgeModel` (a string of at most 200 chars) and `fusionTuning {minPanel 1-50, stragglerGraceMs 0-120000, panelHardTimeoutMs 1000-600000, maxPanel 1-200}`. These fields are in `src/shared/validation/schemas/combo.ts` L262-277.
- **UI**: the combo editor shows these fields only when `strategy === "fusion"` (`combos/page.tsx` around L4563-4660). Global defaults are in Settings > Combo Defaults (`settings/components/FusionDefaultsFields.tsx`, used by `ComboDefaultsTab.tsx` L690).
- **Prelude** (`combo/dispatchPrelude.ts` L387-559):
  1. It reads `cfg.judgeModel` and `cfg.fusionTuning`.
  2. It drops the judge if the judge is hidden or lacks confirmed vision on an image request (L413-434). It warns if those fields are set on a non-fusion strategy (L440).
  3. It resolves targets with `resolveComboTargets` and `expandTargetsForAllStrategies`, and applies the vision filter.
  4. It dedupes members by stepId/modelStr. Combo-ref members become one panel "voice" via `fusionPanel.ts`.
  5. It calls `handleFusionChat` (L549).
- **`open-sse/services/fusion.ts` `handleFusionChat`** (L269):
  - Defaults are `minPanel 2`, `stragglerGraceMs 8000`, `panelHardTimeoutMs 90000`, and `maxPanel 40` (L27-38).
  - An empty panel returns 400. A one-member panel is answered directly.
  - A panel larger than maxPanel returns 400.
  - Panel body: `tools` and `tool_choice` are stripped and `stream:false` is forced (L312-315).
  - Per-target admission drops members first (L316-345).
  - The judge is the explicit `judgeModel`, or `panel[0]` otherwise (L350-356).
  - **Tool-bearing requests bypass the panel.** If `tools` is non-empty and `tool_choice !== "none"` (`isToolBearingRequest`, L150), it calls `handleSingleModel(body, judge)` with the body unmodified (L362-374).
  - Fan-out uses `withTimeout(panelHardTimeoutMs)` and `collectPanel` quorum-grace: once `minPanel` members succeed, it waits `stragglerGraceMs` (L182-219, L379-381).
  - 0 answers returns 503 with per-member reasons. 1 answer with no explicit judge is answered directly (L440-463).
  - The effective judge is a surviving member, preferring `panel[0]` (L464-476).
  - The judge receives `appendUserTurn(body, buildJudgePrompt(answers))` with anonymised "Source N" labels. It keeps the client's `stream` flag and tools, so **streaming happens only on the judge call** (L485-490).
- **Reusability**: `handleFusionChat` is exported and loosely coupled. It needs only `{body, models: (ResolvedComboTarget|string)[], handleSingleModel, log, comboName, judgeModel, tuning, perTargetAdmission}`. Another strategy or prelude could call it per request; `tryFusionDispatch` is itself only a thin adapter.
  - The catch: per-request single-vs-fusion is not a trait today. `dispatchPrelude` is fixed per strategy, so the decision would live in a new prelude (or orderer-plus-prelude) that calls `handleFusionChat` or falls through by returning `null`.
  - `pipeline.ts` and `agenticPipeline.ts` already reuse `extractPanelText` and `isToolBearingRequest` from fusion.ts.

### Existing per-request "smart" decisions to build on
- **`pipeline` strategy** (`open-sse/services/pipeline.ts` `handlePipelineChat` L156): a sequential chain, each step with an optional `prompt`. `agenticOrchestration` adds planner/executor mode (`agenticPipeline.ts`).
- **`context-relay`**: priority ordering plus a context-handoff summary when the account rotates (registry trait `contextRelay`; migrations 019 and 064).
- **Smart pipeline** (`targetResolution.ts` L394-425, `dispatchSmartPipeline`): only for strategies with `smartPipeline` (= auto), and only when the combo is `auto/smart` or `config.pipeline_enabled`. It calls `autoCombo/pipelineRouter.ts` `handlePipelineCombo`, which classifies intent and picks a multi-stage template. This is the closest existing per-request "choose an execution mode" hook.
- **`intentClassifier.ts`**: `classifyPromptIntent` / `classifyWithConfig` returns code|math|reasoning|creative|simple|medium. It is synchronous, under 1 ms, and keyword-based in 9 languages. It is used by `resolveAutoStrategy.ts` L245, `autoCombo/engine.ts`, and `pipelineRouter.ts`.
- **`taskAwareRouter.ts`**: `detectTaskType(body)` (L331) and `applyTaskAwareRouting` (L396). It runs in `src/sse/handlers/chat.ts` L1022-1040, *before* combo dispatch, and rewrites `body.model` to an `auto/<intent>` intent. It is configured at `PUT /api/settings/task-routing`.
- **`autoCombo/virtualFactory.ts`**: builds virtual `auto/*` combos (candidate pool preparation and snapshot weights). It is a pool builder, not a per-request mode switch.

## 4. Plugin runtime

- **Where onRequest runs**: inside `handleChatCore`.
  - `open-sse/handlers/chatCore.ts` L538 calls `runPluginOnRequestHook`, defined in `open-sse/handlers/chatCore/pluginOnRequest.ts`.
  - Combo targets reach `handleChatCore` through `handleSingleModel` (`src/sse/handlers/chat.ts`), so **onRequest runs once per combo target attempt**, not once per client request. Fusion runs it once per panel member plus the judge.
  - It runs after custom-system-prompt injection (chatCore.ts L525-535). `model` and `provider` are the target's own values.
- **Block result**: `runOnRequest` returns `{blocked:true}`, and chatCore returns `{success:false, status:403, errorType:"plugin_block", errorCode:"plugin_block"}` (chatCore.ts L547-559). The response body is `{error:{type:"plugin_block"}}` or the plugin's own payload.
- **In the single-model handler** (`chat.ts` L2621-2645): `shouldSkipConnDisable` returns true for `plugin_block` (`combo/comboPredicates.ts` L413-418, L467-468), so there is **no account cooldown and no account rotation**. The provider breaker is "ignore" for combos (`src/sse/handlers/chatPredicates.ts` L82). The 403 Response goes back to the combo.
- **In the combo attempt loop** (`combo/executeTargetAttempt.ts`): there is no special case for `plugin_block` (grep finds none). `isComboRequestScopedFailure` does not treat `plugin_block` as request-scoped (comboPredicates.ts L279-300, L317-332, L359-370), so the 403 is classified like an upstream 403:
  - **It falls through to the next target** (L1236-1239: "Model X failed, trying next").
  - **The provider circuit breaker is not tripped**, because 403 is not in `PROVIDER_BREAKER_FAILURE_STATUSES = {408,500,502,503,504}` (comboPredicates.ts L207).
  - **The model lockout is recorded** if Model Lockout is enabled, since its default `errorCodes` include 403 (`src/lib/resilience/modelLockoutSettings.ts` L12-19, where `enabled:false` by default; executeTargetAttempt.ts L1205-1234).
  - **A provider cooldown is recorded** if `resilienceSettings.providerCooldown.enabled` (executeTargetAttempt.ts L1246-1257). Its default is UNVERIFIED.
  - `checkFallbackError` may also apply an apikey-403 AUTH_ERROR classification to the exhaustion sets (`accountFallback.ts` L2101-2112).
  - It counts as a combo failure in `recordComboRequest`, metrics, and LKGP clearing. A plugin that blocks every target therefore makes the combo try every target and then return the aggregated "all failed" error.
- **Isolation**: each plugin runs in a child Node process (`src/lib/plugins/loader.ts`: L248 `spawn(process.execPath, ["--no-warnings", hostScript, entry], {stdio:[...,"ipc"]})`). The host script does `await import(plugin)` at top level (L152-183).
  - Top-level module code and `onActivate` can start `setInterval`. The child stays alive while its IPC channel is open. This is inferred from the code, not runtime-tested.
  - The hook timeout defaults to 10 s (L24). A blocking-hook timeout kills the child with **no respawn** (L26-35 comment).
- **Loading after restart**: lazy. `runOnRequest` calls `ensurePluginsLoaded()`, which runs `pluginManager.loadAll()` (activating every DB `status='active'` plugin) once, on the first request (`hooks.ts` L295-337).
  - Booting from instrumentation is deliberately avoided because of the separate Next.js module graph (comment at L301-308).
  - So timers in a plugin only start after the first chat request following a restart.
- **Permissions** (`manifest.ts` L11): `network | file-read | file-write | env | exec`. **Only `env` is enforced**: `getFilteredEnv` (loader.ts L441-457) passes PATH/HOME/USER/LANG/LC_ALL/NODE_ENV, plus PORT/HOSTNAME/TZ/TMPDIR when `env` is granted. Nothing in `src/lib/plugins` checks `network`, and the child is a plain node process with no `--permission` flags.
  - A plugin can make any HTTP call, including to OmniRoute's own API at localhost, but it gets **no API key or secret** from the environment. It would need its own credential in its config or files.
  - Implication: a plugin can poll OmniRoute APIs on a timer, provided an API key is supplied out of band. This is UNVERIFIED for management-API auth requirements.
- **Container**: `/app/data/plugins` does not exist, so no plugins are installed.

## 5. Background jobs

- **Arena ELO** (`src/lib/arenaEloSync.ts`):
  - It starts from `src/instrumentation-node.ts` L392-406 (`initArenaEloSync`) as an in-process `setInterval` (L629).
  - The interval is `ARENA_ELO_SYNC_INTERVAL`, default 86400 s (L154). Backoff is interval/4.
  - It is gated by the feature flag `ARENA_ELO_SYNC_ENABLED`, default `"true"`, with `requiresRestart:false` (`src/shared/constants/featureFlagDefinitions.ts` L697-706).
  - Storage: the `model_intelligence` table, `source='arena_elo'`, with resolution order user_override → arena_elo → models_dev_tier (`src/lib/db/modelIntelligence.ts` L4-5, L54-58, L218 `applyArenaEloRefresh`).
  - **Used by strategies**: only through `getTaskFitness` (`open-sse/services/autoCombo/taskFitness.ts` L490-492), which feeds the `taskFit` factor in the **auto** strategy (`combo/autoStrategy.ts` L36/L401, `autoCombo/engine.ts` L284). It also feeds the Free Provider Rankings page. It is not used by cost-optimized or the quota strategies.
  - Container log: `[ARENA_ELO_SYNC] synced 133 model intelligence entries`.
- **models.dev** (`src/lib/modelsDevSync.ts`):
  - It starts from `instrumentation-node.ts` L455-460 (`initModelsDevSync`, L869) as an in-process `setInterval` (L807).
  - Interval: `MODELS_DEV_SYNC_INTERVAL` (default 86400 s) or the `settings.modelsDevSyncInterval` setting.
  - Enabled by `settings.modelsDevSyncEnabled === true`. The env `MODELS_DEV_SYNC_ENABLED` is a hard off/on override.
  - Pricing is stored in `key_value` namespace `models_dev_pricing` (`saveModelsDevPricing`, L289-300). Capabilities go to a separate synced-capabilities table.
  - **Used by cost-optimized**, through the merged pricing layer in `getPricingForModel`, and by auto's cost factor.
  - Container log: `Starting periodic sync every 21600s`, `9774 pricing entries`, so it is enabled with a 6 h interval.

## 6. Dashboard paths

- **Arena ELO toggle**: `/dashboard/settings/feature-flags`, the "Arena ELO Sync" card.
  - Page: `src/app/(dashboard)/dashboard/settings/feature-flags/page.tsx`, which renders `settings/components/FeatureFlagsGrid.tsx` and `FeatureFlagCard.tsx`.
  - Sidebar entry: `src/shared/constants/sidebarVisibility/sections.ts` L778.
  - The running build has a `feature-flags` route dir in container `/app/.build/next/server/app/(dashboard)/dashboard/settings/`.
- **models.dev toggle**: `/dashboard/settings/ai`, the last card on Settings > AI.
  - Page: `src/app/(dashboard)/dashboard/settings/ai/page.tsx`, which renders `settings/components/ModelsDevSyncTab.tsx`.
  - The toggle PATCHes `/api/settings {modelsDevSyncEnabled}` (L161-164). Status and manual sync use `/api/settings/models-dev`.
  - Sidebar entry: sections.ts L728.
- **Related**: the default strategy and fusion defaults are on `/dashboard/settings/routing` (`settings/routing/page.tsx`, which renders `ComboDefaultsTab.tsx`).

## UNVERIFIED / not checked
- Whether `resilienceSettings.providerCooldown.enabled` defaults to on, and so whether a plugin 403 cools the provider in a combo.
- That a plugin `setInterval` keeps running long-term; inferred from code, not runtime-tested.
- Auth requirements if a plugin calls OmniRoute's management APIs.
- The exact edit list for the pre-registry layout in the running image. It needs a v3.8.51-tag diff.
- i18n fallback for missing locale keys.
