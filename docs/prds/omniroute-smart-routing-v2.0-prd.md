# OmniRoute Smart Routing - Product Requirements Document (PRD)

> **About this version.** v2.0 replaces v1.2. v1.2 was written before any code was read. Its
> premises were checked against the running OmniRoute container and the upstream source, and a
> throwaway-container spike was run. Evidence (file:line, commands, outputs) lives in
> [`omniroute-smart-routing-research/`](omniroute-smart-routing-research/):
>
> | File | Content |
> |---|---|
> | `strategies-fusion.md` | strategy dispatch and inputs, fusion, plugins, background syncs, dashboard paths |
> | `custom-strategy-spike.md` | adding a strategy without a fork; experiment log |
> | `data-sources-quota.md` | Artificial Analysis, Arena and models.dev; scoring; quota-window math |
> | `spike/` | patch script and sample strategy |
>
> Plugin facts not in those files come from the image's own `docs/frameworks/PLUGIN_MARKETPLACE.md` (OmniRoute 3.8.52).

## Requirements Description

### Background

**Business problem.** Each built-in OmniRoute combo strategy optimises one thing:

| Strategy | Optimises |
|---|---|
| `priority` | declared order |
| `reset-aware` | session and weekly reset |
| `headroom` | 5h and 7d utilisation |
| `cost-optimized` | input price |
| `auto` | a fixed blend tuned for `auto/*` combos |

`fusion` is a separate case: it runs a panel on every request to that combo that is not tool-bearing (see Single vs fusion).

None of them, for the members the user put in a combo:
- tells analysis work (plan, review, audit) from execution work;
- ranks members on the right capability for that work, together with:
  - quota health across every subscription window, including expiry;
  - recent failures;
  - price at that provider;
  - declared order;
  - subscription vs metered;
- decides per request whether analysis needs one model or a panel.

As catalogs drift (GLM 5.3 → 5.4, Sol 6 → 5.6), the operator also hand-edits combos.

**Target users.** A single operator on this llm-env host. OmniRoute runs in compose, and Laya System One runs on the compose network. Clients such as Claude Code, Codex, OpenCode and Cursor use OmniRoute combos. The same subscriptions are also used outside OmniRoute.

**Value proposition.** The operator keeps two tier pools and stops hand-tuning them:
- `smart-execution`: agentic, instruction-following, medium reasoning.
- `smart-planning`: frontier analysis.

Every request then goes to the best-fitting member that won't waste or exhaust quota. Analysis can escalate to a two-model panel when it is complex. Combos stay current through automatic, audited bumps.

### Feature Overview

**Core features:**

1. **`smart-catalog` plugin.** Scores models by capability and maintains combos. It runs on a periodic sync and shows flat settings and active/error status in the OmniRoute UI. Probes run through `make smart-probe`.
2. **`smart` routing strategy.** Picks a member at request time and decides single vs. fusion. It is delivered through a derived OmniRoute image with a build-time patch, and the same feature is proposed upstream.
3. **System One task classification.** Laya first, then remote backends the operator has consented to. It answers five questions per request; ranking stays deterministic code.
4. **Plan declarations.** In `models.yml` → `smart_routing.plans`, the operator declares cycle, renewal, expiry and an optional manual %. A declaration fills in for subscriptions with no quota reader, and overrides a reader when it is set.
5. **Upgrade-safe operations.** The base image is `:next`, pinned by digest. Patch counts are asserted at build, and `make check-server` covers the patch, the plugin, scorecard freshness and System One. `make smart-probe` tests each data source and System One backend.

**In scope (v2):**
- everything above;
- automatic bumps with an audit trail;
- an explain log for every decision;
- two seed combos.

**Out of scope (v2):**

| Item | Status |
|---|---|
| Metered-spend cap | future feature |
| Learning %-per-$ (`k_w`) from quota snapshots | rejected: plans are shared with other clients |
| Historic quality ranker | post-MVP; outcomes are logged from day 1 |
| Web-search + cheap-LLM enrichment for unscored models | post-MVP |
| Fusion panels larger than 2 members + judge | — |
| Mid-session account rotation / context relay | — |
| Domain taxonomy UI | — |
| OpenGSD prompt packs | — |

**User scenarios:**
1. **Setup.**
   - In `~/.config/llm-env/omniroute-smart.env` (0600, created empty by `make setup`), the operator sets the AA key and the TypeSafe key.
   - In the plugin page, the operator turns on consent for Zen free. The Zen key comes from OmniRoute's highest-priority `opencode-zen` connection.
   - In `models.yml`, the operator declares the plans.
   - The operator runs `make setup`, then `make smart-probe`. All checks are green.
2. **Planning a feature.** A large backend plan arrives on `smart-planning`.
   - System One classifies: `plan`, `backend`, `large`, `high`, second opinion 0.7.
   - The two best members by U from different providers form a panel, and a judge synthesises the answer.
   - A subscription whose weekly window is binding is ranked below one whose monthly cycle ends tomorrow with unused quota.
3. **Executing a plan.** A plan-following edit arrives on `smart-execution` with tools. It is classified `execute_edit`, `frontend`, `medium`, and goes to a single model, ranked on coding/agentic scores with Arena webdev as the domain signal. A mechanical rename on the same combo is ranked with the `mechanical` weights (`Q` = intelligence + speed; quota terms weigh more than for planning), and runs at the lowest effort.
4. **Catalog drift.** A sync finds `glm-5.4-flash` at the same price with a higher score. The plugin replaces `glm-5.3-flash` in the combo, keeps it as the fallback member, and writes an audit entry.
5. **Upgrade.** A new `:next` digest moves a patch anchor. `make setup` fails with exit 2 and names the site. Production keeps running on the pinned image.
6. **Laya down.** Classification goes to Zen free, the only consented remote. If that also fails, the request gets `503 smart_routing_unavailable` (reason `system_one_unavailable`).

### Detailed Requirements

#### Input/Output

**Inputs:**
- Requests on combos with `strategy: "smart"`: chat completions or responses, including `messages` and `tools`.
- The combo config (§Interface Design).
- Plugin settings and plan declarations.
- From OmniRoute, through the context object the patch injects:
  - quota fetchers and quota cache;
  - generic usage fetchers;
  - circuit breaker and model lockout;
  - pricing;
  - the `smart-catalog` plugin config;
  - provider connections (to read the Zen key).
- Feeds: models.dev prices (OmniRoute table) and capabilities (the models.dev API, fetched by the plugin); Arena ELO (OmniRoute's `model_intelligence`); Artificial Analysis (free API).

**Outputs:**
- One upstream call through OmniRoute's normal combo loop, including its fallbacks and the forbidden-substring and HTTP-200 error-text rules. For fusion: a panel plus a judge via `handleFusionChat`, and only the judge streams.
- An explain-log entry per decision.
- `scorecard.json` updated every sync.
- Combo edits from bumps, with audit entries.
- Health status.

**Error response** (HTTP 503, JSON):

```json
{"error": {"type": "smart_routing_unavailable",
           "reason": "system_one_unavailable | no_candidate_fits | strategy_unavailable | scorecard_missing | config_invalid | fusion_failed",
           "detail": "<one line, no secrets>", "request_id": "<id>"}}
```

`no_candidate_fits` lists the gate that dropped each member.

#### Quota data per provider (verified upstream; Cursor also verified in the running image)

| Provider | Source | Windows |
|---|---|---|
| `opencode-go`, `opencode`, `opencode-zen` | combo quota fetcher `opencodeQuotaFetcher.ts` | 5h, weekly, monthly (`percentUsed`, `resetAt`) |
| `grok-cli` | combo quota fetcher `grokCliQuotaFetcher.ts` | shared weekly credit pool (one window) |
| `codex` | combo quota fetcher `codexQuotaFetcher.ts` | session and weekly |
| `cursor` | generic usage fetcher (`USAGE_FETCHER_PROVIDERS`, `usage/cursor.ts`; `get-current-period-usage` is present in the running image's chunks) | billing cycle: remaining % and reset |
| `claude`, `anthropic` | generic usage fetcher (same list); S1 confirms it is present in the pinned image | as the fetcher reports |
| anything else | **plan declaration** | as declared; no live % |

A **plan declaration** goes in `models.yml` → `smart_routing.plans[]`, one per subscription. It is validated by llm-env's config validation and rendered by `make` into `settings.json`.

| Field | Values |
|---|---|
| `provider` | provider id |
| `connection` | `connectionId` or `*` |
| `cycle` | `monthly@<day>`, `weekly@<weekday>` or `none` |
| `renews` | `true` / `false` |
| `expiresAt` | ISO date, optional |
| `manualRemainingPct` | 0–100, optional |
| `metered` | `true` / `false` |

Rules:
- When both are present, a declaration's `manualRemainingPct` overrides the fetcher.
- With no live or manual %, a window's scarcity is neutral (`neutralScarcity`, 0.5).
- A 429 or quota error marks a window exhausted until its reset:
  - the window the provider's error or headers name;
  - if none is named, the shortest window with data;
  - with no reset time, for `failureBackoffMaxMinutes`.
- `renews: false` caps every window's time-to-reset at `expiresAt`.
- `cycle: none` (a credit pool that never resets) with no `expiresAt`:
  - `T = noResetHorizonHours` [720];
  - `forfeit = 0`, since nothing is lost unused;
  - scarcity is computed against that horizon.

#### System One classification

The instructions and criteria are sent in English, whatever the language of the chat. The request `state` is built within `systemOneStateMaxChars` [4000] characters, in priority order:
1. the last user turn, up to 3/4 of the budget (its head and tail kept, middle elided);
2. the system prompt, filling what remains (its head kept). Long client system prompts, such as Claude Code's, never push out the user turn;
3. `has_tools`.

All five questions go in **one** System One request. The provisioning plan measured 10 questions per request at ≈19 ms, against ≈12 ms for one. Its "never batch questions" note concerns check-server exemplars, where each case should read the whole state on its own. S1 runs the same 20 inputs batched and as 5 separate requests. If any answer flips, the strategy sends the 5 questions as parallel single-question requests instead. Laya serialises GPU work (8 concurrent ≈ 84 req/s, so ≈60–95 ms wall time), so S1 measures that fallback against the p95 < 50 ms target. If it misses, the target is relaxed to the measured value, recorded in the spike report.

| Question | Type | Values | Effect |
|---|---|---|---|
| `task_kind` | choice | `plan`, `review_audit`, `execute_edit`, `mechanical` | Selects the capability weights (§Scoring) and the utility weights (§Ranking). `mechanical` (move, rename, format) uses the `mechanical` rows: `Q` is intelligence + speed, and the quota terms (`S`, `W`, `F`) weigh more. Price only matters among metered members, and class order still puts them last. It runs at the lowest effort. |
| `domain` | choice | `frontend`, `backend`, `fullstack`, `infra_docs` | Selects the Arena signal: webdev, code, webdev+code average, or text. |
| `scope` | score | `small`, `medium`, `large` | Token estimate and the minimum-headroom gate. |
| `complexity` | score | `low`, `medium`, `high` | Effort and quality floor. |
| `second_opinion` | noul (Laya's binary type: answers P(yes) in [0,1]) | probability | Fusion trigger (§Single vs fusion). |

**Confidence per answer**, derived by question type, since Laya reports `answer_confidence` only for `choice`:

| Type | Confidence |
|---|---|
| `choice` | `answer_confidence` (Jev: `confidence`; for Jev `score`/`noul`, the same derivation as Laya's below, applied to the returned probabilities) |
| `score` | probability of the argmax option |
| `noul` | `|p − 0.5| · 2` (p = 0.9 → 0.8; p = 0.6 → 0.2) |

**Low-confidence escalation.** A fast type-1 classifier is tried first, then a type-2 LLM arbiter. Only the questions below `systemOneMinConfidence` (0.7) are re-asked:
1. Each consented remote Jev backend in turn, in chain order (Zen free → TypeSafe → Zen paid), until the answer clears the threshold or `systemOneChainBudgetMs` runs out. A Jev backend that errors or times out during escalation is skipped; it does not fail the request, because an answer already exists.
2. If still below threshold, the **classifier arbiter**: `classifierArbiterModel`, a direct OmniRoute model id in fast/priority mode such as Luna or Sonnet, never a `smart` combo, which validation rejects to prevent recursion.
   - It is called through the injected `handleSingleModel` with the same English questions and criteria, and must answer as JSON with a self-reported confidence per answer.
   - It is bounded by `classifierArbiterTimeoutMs` [5000].
   - Its quota use counts like any other request.
3. If the arbiter is unset, fails, or answers below threshold, the answer takes its conservative value: `review_audit`, `fullstack`, `medium`, `medium`, `second_opinion=0`. The decision log records which stage settled each answer: `laya`, `jev:<backend>`, `arbiter` or `default`.

This escalation applies to **low confidence** only. If no System One backend is **reachable**, the request still fails closed (`system_one_unavailable`), as decided in round 6. The arbiter's self-reported confidence uses the same `systemOneMinConfidence` threshold. Worst-case classification time is `systemOneChainBudgetMs + classifierArbiterTimeoutMs` (8 s + 5 s). It happens only when Laya is unsure and the remotes don't help.

**Backend order:**
1. Laya at `http://system-one:8000` (compose DNS). The host-loopback port 8010 is for host clients only.
2. OpenCode Zen free: `https://opencode.ai/zen/v1/systemone`, model `jev-1.13-free`, honest User-Agent.
3. TypeSafe: `POST https://api.typesafe.ai/v1/systemone` with `Authorization: Bearer $TYPESAFE_API_KEY` and `Content-Type: application/json`, model `jev-latest`. It is **deferred for the MVP**: consent is off by default, and no key is provisioned yet. The chain skips it until both are set.
4. Zen paid: same Zen endpoint, model `jev-1.13`.

These endpoints and models are the ones in the existing `jev-fast-decision` skill plan. `make smart-probe` verifies each one before it is used.

A remote backend is used only if its consent flag is on.

**Backend keys** (never in compose, the plugin DB or logs):

| Backend | Key source |
|---|---|
| Zen | the highest-priority active `opencode-zen` connection, read through the injected context |
| TypeSafe | `TYPESAFE_API_KEY` in `~/.config/llm-env/omniroute-smart.env` (see Data storage), until OmniRoute has a TypeSafe/System One provider |

**Timeouts:**

| Setting | Default |
|---|---|
| `systemOneLayaTimeoutMs` | 1500 |
| `systemOneRemoteTimeoutMs` (each remote) | 3000 |
| `systemOneChainBudgetMs` (whole chain) | 8000 |

When the budget runs out:
- **before any backend has answered** (reachability), the request fails closed with `system_one_unavailable`;
- **during low-confidence escalation** (an answer exists), the chain stops and goes on to the arbiter. `make smart-probe` reports each backend's latency, so the defaults can be tuned.

**Errors:**

| Error | Action |
|---|---|
| `503` + `Retry-After` | retry at most `systemOneRetries` [2] times, waiting `Retry-After` capped at `systemOneRetryAfterMaxMs` [2000] (a longer value goes straight to the next backend), then the next backend |
| `500` | next backend |
| `400`/`413`/`422` | caller bug: surface as `strategy_unavailable`, no next backend |
| Refused connection or timeout (per-backend, above) | next backend, within `systemOneChainBudgetMs` |
| Chain exhausted | `503 system_one_unavailable` |

#### Scoring (`smart-catalog`)

1. **Normalise** each metric to a percentile within its own cohort: AA indices among AA models released in the last 12 months, each Arena board within that board.
2. **Capability weights by task kind** (editable):

   | task_kind | Weights |
   |---|---|
   | `plan` | AA Intelligence .55, Agentic .20, Arena[domain] .25 |
   | `review_audit` | Intelligence .45, Coding .30, Arena[domain] .25 |
   | `execute_edit` | Coding .45, Agentic .35, Arena[domain] .20 |
   | `mechanical` | Intelligence .50, measured speed .50 |

   `Q` is pure capability. Price never enters `Q`, so it doesn't distort the quality gate or bumps, and it isn't counted twice. Price acts only in the utility's metered-class term; on subscriptions, cost is already the quota terms `S` and `W`.

   - **Speed:** AA median output speed (free API).
   - **Price:** models.dev blended price `(3·input + output)/4` per 1M tokens, as a percentile among all scored models.
   - **Weights:** each row must sum to 1. Validation rejects anything else, which keeps `c` ≤ 1.

   `Arena[domain]` is webdev, code, the average of the two, or text, as set by `domain`. The defaults keep Arena at 0.25 or below, and validation warns above that. The mirror is third-party, covers only the top 20 per board, and was 6 days stale when checked.
3. **Score with confidence** (from the research):
   - `raw = Σ w_i·x_i / Σ w_i` over the available metrics;
   - `c = Σ w_i·conf_i × exp(−age_days/scoreDecayDays [14])`, where `conf_i` is 1.0 for AA, the vote-count tier for Arena (high 1.0, medium 0.7, low 0.4), and 0 when missing;
   - final `Q = c·raw + (1−c)·prior`.
   - The AA cohort is models released within `cohortMonths` [12].
4. **Prior:** the family or sibling score minus `priorFamilyPenalty` [0.05], otherwise the cohort median (≈0.5). `score_source` is `measured`, `prior` or `median_default`.
5. **Hysteresis:** suggestions and bumps react only to changes greater than `scoreHysteresis` (0.05).

#### Request-time ranking (`smart`)

All constants below are combo-config keys, with defaults in brackets. Windows are the ones OmniRoute exposes for that connection (5h / weekly / monthly), or the declared ones.

**Per window `w` of candidate `c`:**

| Term | Definition |
|---|---|
| `h_w` | `remaining_w − quotaReservePercent` [3], in % points |
| `T_w` | hours to reset, floored at `minResetHours` [0.25], capped at expiry when the plan does not renew |
| `b_w` | EWMA burn in %/h from successive OmniRoute snapshots, α `burnAlpha` [0.3]; with no history, `burnPriorUtil` [0.8] × (100 − reserve) / window length in hours. For `cycle: none` the window length is `noResetHorizonHours` |
| `scarcity_w` | `min(1, b_w·T_w / h_w)` (1 when `h_w ≤ 0`) |
| `forfeit_w` | `max(0, h_w − b_w·T_w)`, in % points |

**Per candidate:**
- `S_c = max_w scarcity_w`: the binding window.
- `W_c = (1 − S_c) · forfeit_L / T_L`, in %/h of quota lost unused, where `L` is the candidate's **longest** window with data. This follows the evidence. Short windows are excluded on purpose: a 5h window floored at 0.25 h would otherwise dominate `Wn` and bring back the soonest-reset bias (evidence Case 1b). Short windows still act through `S` and the gates.

**Gates.** A candidate is dropped, with the reason logged, when any of these holds:
- context, tools or modality mismatch;
- circuit breaker OPEN or the model locked;
- in failure cooldown;
- any known window has `h_w < minHeadroomPct[scope]` [small 1, medium 3, large 6];
- `Q < Q_best − qualityFloorDelta` [0.10], where `Q_best` is the best Q among candidates passing the other gates;
- the plan is declared expired.

If nothing passes: `503 no_candidate_fits`. Plans are shared and only expose %, so no token-to-% conversion is attempted. The gate is a %-of-window heuristic per scope.

**Classes, in order:**
1. subscriptions with `S < scarcityClassCut` [0.9];
2. subscriptions with `S ≥ 0.9`;
3. metered.

**Utility inside a class** (all terms in [0,1]):
- `U = wQ·Q + wS·(1−S) + wW·Wn + wO·order + wH·(1−F)`
- `Wn = log1p(W)/log1p(max W)`, or 0 when max W = 0.
- `order = 1 − rank/(n−1)`, where `rank` is the 0-based position in the combo's declared member list (before gates) and `n` is the list length. `order = 1` when n = 1.
- Metered class: `wS·(1−S) + wW·Wn` becomes `(wS+wW)·meteredPriceFactor·(1 − price_percentile)`, with `meteredPriceFactor` [0.2].
  - At most, the price term is worth 0.07 of U for plan/review and 0.10 for execution.
  - Compare a quality gap at the floor (`wQ × qualityFloorDelta`): 0.05 for plan/review, 0.03 for execution, 0.02 for mechanical. For plan/review the two are about equal. For execution and mechanical, price can outweigh a floor-sized quality gap among metered members, on purpose: those tasks are cheaper to get slightly wrong. Raise `qualityFloorDelta` or lower `meteredPriceFactor` per combo to change that.
  - The research's 0.5 was dropped because it had no justification.

Default weights:

| task_kind | wQ | wS | wW | wO | wH |
|---|---|---|---|---|---|
| `plan`, `review_audit` | .50 | .25 | .10 | .05 | .10 |
| `execute_edit` | .30 | .30 | .20 | .10 | .10 |
| `mechanical` | .20 | .30 | .20 | .10 | .20 |

**Failures (`F`)** come from the strategy's own counter, not `getModelLatencyStats`. That function counts every failed row, 429s and 4xx included, and queries the database on every call.
- `F` is an in-memory EWMA per (provider, model, connection), α `failureAlpha` [0.3]. It is updated from the outcome of each upstream attempt, which the strategy sees through the injected `handleSingleModel` wrapper.
- `F` resets on restart. That is acceptable because the cooldown is at most 30 min.
- Cooldown is `min(failureBackoffBaseSeconds [30]·2^(n−1), failureBackoffMaxMinutes [30])` after n consecutive 5xx or timeouts, honouring `Retry-After`.
- A 429 or quota error marks the window exhausted, without counting toward F.
- 4xx and content errors do not count.
- A newer model that fails more than its sibling loses through F alone.

**Effort.** The effort comes from the options recorded for the chosen model:

| Condition | Effort |
|---|---|
| `complexity=high` or `scope=large` | highest |
| `low` and `small` | lowest |
| otherwise | medium (or nearest) |
| `mechanical` | always lowest |

**Worked example (owner's case).** A's cycle ends in 24 h with 60% unused. B's 5h window resets in 5 minutes, but B's weekly window has 40% left and is burning.
- `S_A = 0.54` (0.34 if A expires rather than renews).
- `S_B = 1.0`, because B's week binds first.
- **A wins.**
- Soonest-reset-first would pick B and burn its scarce week while A forfeits about 38–55 points.
- Longest-cycle-first (v1.2) fails the opposite case: weekly C has 55 points expiring in 2 h (`S_C = 0.04`), monthly D is binding (`S_D = 1.0`), and it picks D.
- Tables: `data-sources-quota.md` §5. That file's "S 0.31" for C is a typo for 0.04.

#### Single vs fusion

Fusion runs on any `smart` combo when **all** of the following hold:
- `task_kind ∈ {plan, review_audit}`;
- `complexity = high` or `second_opinion > fusionSecondOpinionMin` [0.5];
- all five answers have confidence ≥ `systemOneMinConfidence`;
- the request is not tool-bearing. OmniRoute's definition (`isToolBearingRequest`) is: `tools` non-empty and `tool_choice` ≠ `"none"`;
- two candidates pass the gates (preferring different providers).

**Execution.** The panel is the top 2 by U. The judge is `judgeModel` from the combo config if set, otherwise the panel's first member (OmniRoute's default). The judge must pass the same gates: breaker, quota and cooldown. If it fails them, the panel's first member judges. If neither can, the request runs single on the top 1. It runs through the injected `handleFusionChat` with `fusionTuning` defaults `panelHardTimeoutMs` [90000], `minPanel` [2] (the quorum) and `stragglerGraceMs` [8000], which match OmniRoute's own defaults.

**Judge timeout.** `handleFusionChat` calls the judge through the `handleSingleModel` the strategy injects. The strategy wraps that call with `fusionJudgeTimeoutMs` [120000], so the worst case for fusion is `panelHardTimeoutMs + fusionJudgeTimeoutMs`.

**Degraded panel.**
- **One survivor:** OmniRoute's behaviour is kept as-is (`fusion.ts`, one-survivor path). The surviving answer reaches the client, either directly or through the judge. S3 records which path the pinned image takes and its latency. The decision log records `lane: fusion_degraded`, and `make check-server` reports the 24 h count.
- **Zero survivors:** one single-model attempt on the best gated candidate outside the panel. If there is none, or it fails, `503 smart_routing_unavailable` with reason `fusion_failed`.

**Fallback.** When the conditions don't hold, the request goes single to the top-1. The explain log records why.

#### Plugin maintenance

- **Discovery:** for each connected subscription, record the effort options (models.dev `reasoning_options` plus the provider catalog) and the priority/fast tiers.
- **Custom-model hygiene:** make sure the discovered variant ids exist, are unhidden, and are usable in combos.
- **Matching:** an explicit alias table plus models.dev `canonical_model_id`. Never strip `-fast`, `-flash` or `-build-fast` blindly.
- **Bumps (automatic, audited):** each rule is declared in `models.yml` → `smart_routing.bumps[]` as `{match: "<provider>/<glob>", maxPriceRatio?}`, for example `{match: "opencode-go/glm-*-flash"}`. A member matching a rule is replaced by a newer id that matches the same glob when all of these hold:
  - its models.dev price ≤ `maxPriceRatio` (default `bumpPriceMaxRatio`, 1.0) × the old price;
  - `Q_new ≥ Q_old − scoreHysteresis` for the combo's dominant task kind. That is the most frequent `task_kind` in `task-stats.json` for that combo over the last 7 days, or `execute_edit` when there is no history. `task-stats.json` holds rolling daily counts per combo, written by the strategy, so it does not depend on log rotation;
  - the new id is newer by release date.

  The old id is appended as the fallback member, and the change is written to `bump-audit.jsonl`. At most `bumpMaxFallbacks` [1] previous ids are kept per rule; older ones are removed in the same edit. Combo edits use `PUT /api/combos/{id}` (upstream `src/app/api/combos/[id]/route.ts`) with the management key; S4 confirms the strategy config survives the edit. `bumpMode` is `auto` (default) or `off`.

#### Data requirements

Files live in `<OmniRoute data volume>/smart-routing/`, mode 0600, and are never world-readable. They hold no secrets.

- **`models.yml` is llm-env's own config file, not OmniRoute's.** It lives at `~/.config/llm-env/models.yml` (`pylib/config.py` `DEFAULT_CONFIG_PATH`). It already holds the `llm_server` (llama.cpp), `system_one` and `omniroute` sections, is validated by `pylib/config.py`, and is rendered by `make` into the compose file. This feature adds one more section, `smart_routing:`, handled the same way.
- **`settings.json`**, rendered by `make` from `models.yml` → `smart_routing` and validated by llm-env's config validation. It holds the plan declarations, bump rules, capability weights, and default combo-config values.
- **Plugin config** (OmniRoute DB, flat `configSchema` fields, **no secrets**), read by the strategy through the injected context and by the plugin over its loopback config API:
  - `syncIntervalHours`, `bumpMode`, `scoreHysteresis`;
  - Laya URL, and a consent flag for each remote backend.
- **`scorecard.json`**, one entry per `(provider, model)`:

  | Field | Content |
  |---|---|
  | ids | `canonical_id` |
  | scores | AA indices (with attribution), Arena per board, `Q` per task kind × domain, `confidence`, `score_source` |
  | catalog | price (in/out/cache), effort options, fast tiers, context/output limits |
  | timestamps | `synced_at`, `source_ages` |

  Top level: `version`, `generated_at`, `attribution`.
- **`decisions.jsonl`:** one line per request:

  | Field | Content |
  |---|---|
  | request | `request_id`, combo |
  | classification | answers, confidences, backend used |
  | candidates | U terms, or the gate that dropped each one |
  | outcome | winner, effort, lane (single, fusion or fusion_degraded), elapsed ms |
  | attempts | `[{target, status, latency_ms}]` for every upstream attempt, from `observe()` |

  The line is written once, after the combo loop finishes or the stream ends.

  It rotates at `decisionLogMaxMB` [10] × `decisionLogFiles` [3].
- **`bump-audit.jsonl`:** a log of bump changes.
- **`task-stats.json`:** daily aggregates per combo, kept for 90 days and written by the strategy. They survive restarts and log rotation:
  - `task_kind` counts;
  - client requests;
  - upstream attempts;
  - quota/429 attempts;
  - `fusion_degraded` and `fusion_failed` counts.

  Bumps read their 7-day dominant task kind from here. The 30-day success metrics are computed from here, not from the rotated `decisions.jsonl`.
- **Validation:**
  - `smart_routing` in `models.yml` is validated with the rest of llm-env's config (types, ranges, enums, globs). An invalid value fails `make`.
  - Plugin fields are validated against `configSchema`: min/max on numbers, enums on selects.
  - The strategy validates the combo config merged with defaults. An invalid value returns `503 config_invalid`, naming the key.
  - An unreadable or invalid `settings.json` → `503 config_invalid`.
  - A scorecard that is unreadable, or older than 2 × the interval, is treated as missing: `503 scorecard_missing`.
  - A stalled or dead plugin child shows up as scorecard age: health turns red at interval + 30 min, and requests fail at 2 × the interval.

#### Edge cases

| Case | Handling |
|---|---|
| Combo with one member | `order = 1`; gates still apply |
| All candidates gated out | `503 no_candidate_fits` with reasons; never start a call likely to be cut mid-plan |
| Provider with no quota data and no declaration | scarcity 0.5; only F, Q, order and price differentiate it |
| Metered-only combo | class 3 only; price term active |
| Tool-bearing analysis request | always single |
| Laya idle-unloaded (first request after 600 s) | about +0.4 s reload; excluded from the warm p95 target and reported separately by the benchmark |
| Model missing from AA and Arena | prior or median score, flagged `score_source` |
| Same model reachable through several connections | each connection is its own candidate with its own windows |

## Design Decisions

### Technical Approach

#### Architecture

Each concern goes where OmniRoute lets it act. The verified constraints:
- **Plugins can't choose the member.** `onRequest` runs inside `handleChatCore`, after the target is chosen. Hooks are fail-open.
- **Only a strategy sees the target list before dispatch,** and the strategy list is closed. Upstream added an internal `strategyRegistry.ts` on 2026-10-10; its public extension point is "planned for v4".
- **The image runs compiled Next.js output,** so editing `.ts` files has no effect.

| Component | Responsibility |
|---|---|
| `smart-catalog` plugin (OmniRoute plugin, child process) | Flat settings in the per-plugin config page, and active/error status in `/dashboard/plugins`. Periodic sync on a `setInterval` (default 6 h): models.dev capabilities, AA, scoring, discovery, hygiene, bumps. Writes `scorecard.json` and `bump-audit.jsonl`. Reuses OmniRoute's native models.dev price sync (`/dashboard/settings/ai`) and Arena ELO sync (`/dashboard/settings/feature-flags`, card "Arena ELO Sync", on by default) instead of duplicating them. It reads its own config from `GET /api/plugins/smart-catalog/config` over loopback, using the management key. S5 checks whether loopback trust makes that key unnecessary. It receives `OMNIROUTE_PLUGIN_SMART_CATALOG_AA_API_KEY` and `OMNIROUTE_PLUGIN_SMART_CATALOG_MANAGEMENT_KEY` through `env_file` and patch item 6. |
| `smart` strategy (module in `OMNIROUTE_STRATEGIES_DIR`) | Classify, gate, class, utility, effort, single or fusion, explain log. Reads `settings.json` and `scorecard.json` (mtime-cached), plus the injected context. No database query per request: plugin config is cached with a `pluginConfigTtlSeconds` [60] TTL, quota comes from OmniRoute's cache, and `F` is in memory. |
| `make smart-probe` (llm-env) | Tests AA, models.dev, Arena freshness, and each System One backend with a fixed English question. Prints pass/fail per source. |
| Derived image (`docker/omniroute-smart/`, built by `make setup`) | `FROM diegosouzapw/omniroute:next@<pinned digest>`. Copies the strategy modules in and runs `patch-strategies.mjs`. The live config already runs `:next`; the repo template still defaults to `:latest`. |
| Upstream PRs | (1) A pluggable strategy loader in `strategyRegistry.ts`; when it merges, the patch is deleted. (2) Owner's idea: a System One / decision provider type plus "decision combos". OmniRoute would then handle the Laya → Zen → TypeSafe fallback natively, and the strategy's backend chain would go away. |

**Module contract.** A strategy module exports:
- `prelude(ctx)`, returning one of:
  - `{response}`, for a 503 or a fusion result;
  - `{targets, lane}`;
  - `null`, to fall through to built-in behaviour.
- `order(ctx)`, returning the targets ordered for the normal combo loop.

**What the patch changes:**
1. Adds `smart` to the compiled strategy list: ≈48 occurrences of one literal across 47 files, a mechanical edit that also updates validation.
2. Hooks the ordering step (2 sites, proven by the spike).
3. Hooks the combo dispatch prelude, where fusion and pipeline already branch. This is what lets the module return a response.
4. Injects the context object:
   - quota fetchers and cache, and the generic usage fetchers;
   - `getCircuitBreaker`, `isModelLocked`, `getPricingForModel`;
   - plugin config and provider connections;
   - `handleFusionChat`, `handleSingleModel`.
5. Wraps the per-target call that the combo loop receives (`handleSingleModelWithTimeout` in `combo.ts`). The wrapper reports each attempt's status and latency to the module's `observe(outcome)`, which feeds `F`. Without this site, `F` cannot see failures.
6. Extends the plugin env allowlist (`getFilteredEnv`, upstream `src/lib/plugins/loader.ts:441-457`) so that variables named `OMNIROUTE_PLUGIN_<PLUGIN_NAME>_*` reach only that plugin's child process. Today the child gets only PATH/HOME/USER/LANG/LC_ALL/NODE_ENV, plus PORT/HOSTNAME/TZ/TMPDIR with the `env` permission. That is why `env_file` alone cannot get keys to the plugin, even though the container itself receives them.

S1 measures the exact count for sites 3–6. Plugin-scoped env is also proposed upstream, next to the pluggable-strategy PR. The build asserts **exact** counts for every site and exits 2 on any mismatch, including partial matches. At runtime, a missing or failed module returns `503 strategy_unavailable`. It never falls back to declared order.

#### Interface design

**Combo config for `strategy: "smart"`:** any key not set takes the default from `settings.json`.
- quota: `quotaReservePercent`, `minHeadroomPct{small,medium,large}`, `minResetHours`, `burnAlpha`, `burnPriorUtil`, `scarcityClassCut`, `neutralScarcity`, `noResetHorizonHours`;
- ranking: `qualityFloorDelta`, `weights{task_kind:{wQ,wS,wW,wO,wH}}`, `meteredPriceFactor`, `failureAlpha`, `failureBackoffBaseSeconds`, `failureBackoffMaxMinutes`;
- System One: `systemOneMinConfidence`, `classifierArbiterModel` (seed: `codex/gpt-6-luna-high`), `classifierArbiterTimeoutMs`, `systemOneLayaTimeoutMs`, `systemOneRemoteTimeoutMs`, `systemOneChainBudgetMs`, `systemOneRetries`, `systemOneRetryAfterMaxMs`, `systemOneStateMaxChars`;
- fusion: `fusionSecondOpinionMin`, `judgeModel`, `fusionJudgeTimeoutMs`, `fusionTuning{panelHardTimeoutMs, minPanel, stragglerGraceMs}`.

Keys that are not per combo live in `models.yml` → `smart_routing` and are validated there (types, ranges, globs):
- **plugin and strategy:** `pluginConfigTtlSeconds`, `scoreDecayDays`, `cohortMonths`, `priorFamilyPenalty`;
- **bumps:** `bumpPriceMaxRatio`, `bumpMaxFallbacks`;
- **decision log:** `decisionLogMaxMB`, `decisionLogFiles`.

**Plugin settings** (`configSchema`, flat fields only, no secrets):
- `syncIntervalHours` [6];
- `systemOneLayaUrl` [`http://system-one:8000`];
- consent flags `consentZenFree`, `consentTypeSafe`, `consentZenPaid` (all off by default);
- `bumpMode` [auto], `scoreHysteresis` [0.05].

**llm-env settings** (`models.yml` → `smart_routing`):
- `plans[]`, `bumps[]`, `capabilityWeights`;
- `defaults` for every combo-config key above;
- the non-per-combo keys listed under Interface design.

**Probes:** `make smart-probe`, as described above.

**Seed combos.** These are initial membership only. The operator edits members at any time in OmniRoute, and the implementation must not depend on which members a combo has. Ids were checked against this host's OmniRoute catalog on 2026-10-13; the order is the declared order (`order` term).
- **`smart-execution`**: agentic, plan-following, medium reasoning, tool use; fast variants, and Kimi `-code`.
  1. `cursor/composer-2.5-fast`
  2. `grok-cli/grok-4.7-build-fast`
  3. `opencode-go/glm-5.3-flash`
  4. `cursor/glm-5p3-flash-high`
  5. `opencode-go/deepseek-v4.1-flash`
  6. `opencode-go/kimi-k2.7-code`
  7. Zen last resort: `opencode-zen/glm-5.3-flash`, `opencode-zen/deepseek-v4.1-flash`, `opencode-zen/kimi-k2.7-code`
- **`smart-planning`**: frontier analysis (plan, review, audit).
  1. `codex/gpt-6-sol-high`
  2. `cursor/gpt-5.6-sol-high-1m`
  3. `opencode-go/glm-5.3`
  4. `cursor/glm-5p3-high`
  5. `opencode-go/kimi-k3`
  6. Zen last resort: `opencode-zen/glm-5.3`, `opencode-zen/kimi-k3`
- **Arbiter seed** (`smart_routing.defaults.classifierArbiterModel`): `codex/gpt-6-luna-high`.
  - The catalog has no id literally named "luna-high-fast". Discovery records a priority tier for it when one exists.
  - `dva/gpt-5-6-luna-high-priority` is the priority-tier alternative.
- Both combos copy the forbidden-substring fallbacks from the existing coding combos.

#### Data storage

- Non-secret files go in `<OmniRoute data volume>/smart-routing/` (see Data requirements).
- **Secrets** live in two places:
  - **`~/.config/llm-env/omniroute-smart.env`**, mode 0600, alongside llm-env's other config (`models.yml`, `omniroute-api-key.json`). It holds:
    - `OMNIROUTE_PLUGIN_SMART_CATALOG_AA_API_KEY` and `OMNIROUTE_PLUGIN_SMART_CATALOG_MANAGEMENT_KEY`, which reach the plugin child via patch item 6;
    - `TYPESAFE_API_KEY`, read by the strategy in OmniRoute's main process, which sees the full container env.

    Compose/podman load the file into the container env; nothing parses it by hand. `make setup` creates it empty if missing and never overwrites it. The rendered compose references it with `env_file:` on the `omniroute` service only, so values never appear in the compose file. It is never copied elsewhere, and `make check-setup` verifies the mode.
  - **The `opencode-zen` provider connection**: the Zen key, protected by OmniRoute like any other connection.
- Secrets are never written to the plugin DB, the `smart-routing/` files, or logs.

### Constraints

**Performance:**

| Measure | Target |
|---|---|
| Strategy overhead, excluding classification (no database query per request) | p95 < 5 ms |
| Warm Laya classification, five questions in one request | p95 < 50 ms. The provisioning plan measured ≈12 ms for one question and ≈19 ms for ten; the five-question figure is measured in S1. |
| First request after Laya's idle unload | ≈0.4 s |
| Sync | never blocks request admission |
| Fusion | `panelHardTimeoutMs + fusionJudgeTimeoutMs` (90 s + 120 s) |
| Classification worst case (low-confidence escalation to the arbiter) | `systemOneChainBudgetMs + classifierArbiterTimeoutMs` (8 s + 5 s) |

**Compatibility:** the OmniRoute base image is `:next`, pinned by digest. A new digest is adopted only after `make setup` passes the patch assertions and S1's checks.

**Security:**
- Secrets are never logged and are sent only to their owning backend.
- Remote System One backends receive the `state` excerpt only with consent.
- Artificial Analysis attribution is shown wherever AA-derived scores appear, and raw AA data is never redistributed.
- The plugin and its API are loopback-only (OmniRoute Tier 1).

**Scalability:** single tenant. The scorecard is O(models). The decision log is bounded by rotation.

### Risk Assessment

**Technical risks:**

| Risk | Mitigation |
|---|---|
| The next `:next` image moves the patch anchors (certain for #16206) | Digest pin; exit 2 at build; S2; upstream PR |
| The prelude hook or context injection needs anchors on minified identifiers, or can't return a `Response` | S1 no-go → external sidecar router that reads quota and health over the REST API. The site count itself is not a no-go criterion, as long as every count is exact and asserted |
| Dashboard editor resets a custom strategy on save | S4 → edit `smart` combos through the API only |
| Plugin timer doesn't start after a restart (plugins load lazily) | S5 → post-start activate call; fallback: a systemd user timer runs the same sync |

**Dependency risks:**

| Dependency | Risk | Mitigation |
|---|---|---|
| AA free tier | 100 requests/day | a 6 h sync uses about 8; keep the last scorecard on outage and mark it stale |
| Arena data | third-party mirror, top 20 only | low weight, priors |
| System One remote backends | outage or no consent | the chain fails closed with a clear reason |

**Schedule risks:** Phase 1 is a go/no-go gate. If the patch route fails, the sidecar replaces only the strategy delivery; the plugin and the algorithms carry over unchanged.

## Acceptance Criteria

### Functional Acceptance
- [ ] **Spikes recorded.** S1–S5 have pass/fail results in `omniroute-smart-routing-research/`. On no-go, the documented fallback is used.
- [ ] **Build safety.** A derived image builds in `make setup`. The patch asserts exact counts, and a mismatch exits 2. A combo with `strategy:"smart"` is created (201); a bogus strategy is rejected (400).
- [ ] **Plugin and scorecard.**
  - `smart-catalog` is listed `active` in `/dashboard/plugins` and its config page saves.
  - `make smart-probe` reports pass or fail per source and per System One backend.
  - The Zen key is read from the top-priority `opencode-zen` connection.
  - `scorecard.json` is rewritten every interval with attribution, confidence and `score_source`.
- [ ] **Discovery, hygiene and bumps.**
  - Discovery records effort options and fast tiers.
  - Hygiene makes the variant ids usable in combos.
  - A bump fires only when price, score and release-date conditions hold. It keeps the old id as the fallback member and writes `bump-audit.jsonl`.
- [ ] **Classification.**
  - Each request is classified on the five questions, with confidence derived by type.
  - Tests show that a low-confidence answer escalates Laya → consented Jev backend → `classifierArbiterModel` → conservative value, and that the decision log names the stage that settled it.
  - A `smart` combo as arbiter is rejected.
  - Every backend error class follows the System One error table.
- [ ] **Ranking unit tests** cover:
  - the owner's case (A over B);
  - the longest-cycle counter-case (C over D);
  - expiry capping;
  - `minHeadroomPct` per scope;
  - the n=1 and max W = 0 cases;
  - cooldown;
  - class order (metered last).
- [ ] **Gates.** With every member gated out, the response is `503 no_candidate_fits` with per-member reasons, and no upstream call is made.
- [ ] **Single vs fusion.** Fusion runs only when every fusion condition holds. Tool-bearing requests are always single. `panelHardTimeoutMs` is respected.
- [ ] **Failure modes** return `503` with reasons `system_one_unavailable`, `strategy_unavailable`, `scorecard_missing`, `config_invalid` and `fusion_failed` (zero panel survivors and a failed single retry) respectively. None of them falls back silently, and each has a test that triggers it.
- [ ] **OmniRoute behaviour kept.** Its fallbacks and forbidden-substring / HTTP-200 error-text rules still apply inside `smart` combos.
- [ ] **Explain log.** Every decision writes a `decisions.jsonl` line with the fields listed in Data requirements.

### Quality Standards
- [ ] **Code quality:** follows llm-env conventions (shell/Python). The plugin and strategy are plain JavaScript ES modules (`.mjs`, as in the spike) with JSDoc types, no build step, and tests run with `node --test`. `make validate` passes, and no secrets are committed.
- [ ] **Test coverage:** unit tests for:
  - quota math (S, W, gates, classes, U);
  - scoring (percentiles, confidence, prior);
  - bump conditions;
  - classification fallbacks;
  - patch-count assertions against a fixture tree.

  Integration test: a canary combo on the side-by-side derived image, including the fallback when target 1 fails.
- [ ] **Performance:** a local benchmark shows strategy overhead p95 < 5 ms and warm Laya classification p95 < 50 ms.
- [ ] **Security review:**
  - secrets never appear in logs or plugin files;
  - consent flags gate remote System One;
  - AA attribution is present;
  - plugin files are mode 0600.

### User Acceptance
- [ ] **User experience:**
  - The operator sets keys in `omniroute-smart.env` and consent in the plugin page, declares plans and bumps in `models.yml`, and runs `make smart-probe`. No hand edits to SQLite.
  - `make check-server` covers the patch marker, the strategy being accepted, the plugin being active, scorecard age < interval + 30 min, and System One reachability. It reports red on any failure.
- [ ] **Documentation:** an operator guide covering:
  - the plugin setup and the two dashboard toggle paths;
  - plan declarations;
  - how to read `decisions.jsonl`;
  - the upgrade procedure (new digest → `make setup` → checks);
  - how to roll back to the pinned image.
- [ ] **Training materials:** not required (single operator).

**Success metrics** (30 days after enablement, measured from `decisions.jsonl` and OmniRoute logs):
- **Unfit starts:** ≤ 2% of upstream attempts on `smart` combos end in a quota/429 error.
  - **Smart rate source:** the strategy's own `observe()` outcomes, aggregated daily in `task-stats.json` (quota/429 attempts ÷ attempts).
  - **Baseline:** for 7 days before enabling, the same per-attempt rate on the existing `priority` combos. It comes from OmniRoute's usage history (`src/lib/usage/usageHistory.ts`), which writes one row per target attempt with `status` and `combo_strategy`: quota/429 rows ÷ all rows where `combo_strategy = priority`. S1 confirms the pinned image records both columns; otherwise the baseline comes from OmniRoute logs.
  - Both sides count attempts, not client requests, because usage history has no request id.
  - The target is also met if the rate is at least 50% below the baseline.
- **Scorecard freshness:** age < interval + 30 min, 99% of the time.
- **Silent degradations:** every fallback or failure path is covered by a test. In production, every client request to a `smart` combo has exactly one `decisions.jsonl` line, even when fusion or fallbacks make several upstream calls. The strategy counts client requests entering its prelude in `task-stats.json`, which is persisted, so restarts don't reset it. `make check-server` checks that today's count equals today's `decisions.jsonl` line count.
- **Spend split** (informational): subscription vs metered share of requests, and the fusion rate on analysis.

## Execution Phases

### Phase 1: Preparation
**Goal:** prove the delivery route and wire it into llm-env.

- [ ] **S1:** build the derived image (`:next@digest`) beside production on a test port, from a non-secret config copy, with the prelude hook and context injection.
  - **Pass:**
    - every site count is exact and asserted;
    - create returns 201 and a bogus strategy returns 400;
    - the prelude can return a 503 `Response`;
    - a canary request is ordered by `smart`;
    - when target 1 fails, the request falls back to target 2;
    - built-in strategies make the same first pick as unpatched;
    - the Cursor and Claude usage fetchers are reachable through the context;
    - hook p95 < 1 ms;
    - five-question Laya classification p95 is measured, and batched vs separate answers are compared on 20 inputs;
    - the pinned image's usage history records `status` and `combo_strategy` (needed for the metric baseline).
  - **No-go:** anchors on minified identifiers, or no way to return a response → the sidecar.
- [ ] **S2:** rebuild against the first `:next` that contains #16206. It builds unchanged, or exits 2 and the anchor is rewritten within 2 h.
- [ ] **S3:** a fusion call through the injected `handleFusionChat` from the strategy. The same text yields single or panel + judge as the conditions dictate, and tools force single.
- [ ] **S4:** save a `smart` combo through the dashboard editor. It must keep its strategy and config; otherwise use API-only editing.
- [ ] **S5:** with podman compose, check that `env_file` puts the keys in the container env, the strategy reads `TYPESAFE_API_KEY`, and patch item 6 hands the `OMNIROUTE_PLUGIN_SMART_CATALOG_*` keys to the plugin child and to no other plugin. After a restart, `make start` calls `POST /api/plugins/smart-catalog/activate` over loopback. That must start the plugin timer, and the sync must run on schedule for 24 h. Also check whether loopback trust lets the plugin read its config without the management key. Otherwise use a systemd timer.
- [ ] `docker/omniroute-smart/` with the digest pin and label, built in `make setup`.
- [ ] Health checks added to `make check-server`.

**Deliverables:** spike report; derived image build; health checks.
**Time:** 2–3 days.

### Phase 2: Core Development
**Goal:** the plugin and the strategy.

- [ ] `smart-catalog` plugin:
  - config schema;
  - the AA client and id matching;
  - models.dev capability fetch;
  - scoring and `scorecard.json`;
  - discovery and hygiene;
  - automatic bumps with audit;
  - probes.
- [ ] `smart` strategy:
  - System One client and backend chain;
  - classification fallbacks;
  - quota terms, gates, classes, utility and effort;
  - the error responses;
  - `decisions.jsonl`.
- [ ] Unit tests listed under Quality Standards.

**Deliverables:** working plugin and strategy modules, with tests.
**Time:** 8–12 days. The plugin and the strategy can proceed in parallel.

### Phase 3: Integration & Testing
**Goal:** fusion, end-to-end behaviour and upgrade safety.

- [ ] Single vs fusion decision through `handleFusionChat`, with a regression test for the fusion hang (two-member panel, timeout).
- [ ] Live tests on canary combos:
  - quota gate (owner's case, using a declared plan);
  - each 503 reason;
  - Laya down falling through to a consented remote;
  - a bump in a dry environment;
  - an upgrade rehearsal with a new digest.
- [ ] Operator guide.

**Deliverables:** test report, operator guide.
**Time:** 3–5 days.

### Phase 4: Deployment
**Goal:** enable on this host and observe.

- [ ] Create `smart-execution` and `smart-planning` with the operator's ids.
- [ ] Point the clients at them.
- [ ] Watch the success metrics for 30 days.
- [ ] Open the upstream PR for pluggable strategies.
- [ ] Open the upstream PR for plugin-scoped env (`OMNIROUTE_PLUGIN_<NAME>_*`). When both PRs merge, patch items 1–6 are deleted.
- [ ] Propose upstream a System One / decision provider type with "decision combos" (owner's idea).
- [ ] Add to the backlog:
  - a metered-spend cap;
  - the historic quality ranker;
  - enrichment for unscored models.

**Deliverables:** enabled stack, upstream PR, backlog items.
**Time:** 1–2 days, plus the PR.

---

## Clarification Summary

| Round | Topics resolved |
|---|---|
| Prior chat (v1.x) | Fusion is not a merge; the judge is `panel[0]` or `config.judgeModel`; no context relay; models.dev prices; AA scores; System One as the decision brain; smart combos for plan, review and execution; custom Grok models |
| 1–4 (v1.x) | Plugin volume, UI and a 6 h sync; reserve plus multi-factor ranking; fail closed; all thresholds configurable; AA miss → prior; System One order Laya → Zen free → TypeSafe → Zen paid; effort and fast-tier discovery |
| 5 (v2 research, 2026-10-13) | Plugins can't pick members, so the strategy goes into a derived image (spiked); "longest cycle first" replaced by binding-window scarcity plus forfeiture; System One classifies only; Arena is a secondary signal; native models.dev and Arena syncs reused |
| 6 | Providers without a quota reader use a plan declaration (later found that Cursor does have a usage reader; see round 9). Delivery: a context-injecting patch with exact asserted counts, digest pin, plus an upstream PR. System One: Laya, then consented remotes, then fail closed |
| 7 | Combos are tier pools (`smart-execution`, `smart-planning`). System One, not the combo name, decides analysis vs execution, and fusion is allowed on any combo when the work is analysis and complex. Plans are shared, so no learning of %-per-$; %/h math with per-scope headroom gates |
| 8 | The five-question taxonomy is accepted. No metered cap in the MVP (future feature). Bumps are automatic with audit |
| 9 | Config is split: flat fields in the plugin page; plans, bumps and weights in `models.yml`; probes via `make smart-probe`. The Zen key comes from the top-priority `opencode-zen` connection. Upstream idea: System One decision combos. Image `:next` pinned by digest. Cursor has a usage reader (verified in the running image) |
| 10 | The plugin DB stores config as plaintext, so AA, TypeSafe and management keys go in `~/.config/llm-env/omniroute-smart.env` (0600), passed with `env_file:`, never written into compose. System One timeouts: Laya 1500 ms, each remote 3000 ms ("these backends are expected to be fast"), chain budget 8000 ms |
| 11 | OmniRoute filters plugin env (verified in `loader.ts`), so the keys keep using `env_file` with plugin-specific names (`OMNIROUTE_PLUGIN_SMART_CATALOG_*`), and patch item 6 passes them to that plugin only (also proposed upstream). Degraded fusion (one survivor) answers with the survivor and logs `fusion_degraded`. Price leaves `Q` and acts only in the metered utility term |
| 12 | Confidence is derived per type: choice `answer_confidence`, score argmax probability, noul `|p−0.5|·2`. Low confidence escalates from type 1 to type 2: Laya → consented Jev backends → an LLM arbiter (fast model such as Luna or Sonnet) → conservative value. `meteredPriceFactor` 0.2 makes price a tie-breaker |
| 13 | AA key stored in `omniroute-smart.env`. TypeSafe: `https://api.typesafe.ai/v1/systemone` with Bearer auth, deferred for the MVP. Seed members are initial config only, and the implementation must not depend on them: execution uses fast variants (composer, grok, glm, deepseek) plus kimi `-code`; planning uses sol, glm, kimi; Zen goes last. Arbiter seed `codex/gpt-6-luna-high` |

---

**Document Version:** 2.0
**Created:** 2026-10-12T09:08:35-03:00
**Updated:** 2026-10-13
**Clarification Rounds:** 13
**Quality Score:** 92/100 (independent review, round 7; the author does not grade its own document). Earlier drafts 68, 75, 85, 88, 87, 89. The material findings of that review (parallel-fallback latency, per-attempt log fields and daily aggregates, chain-budget scope, remote endpoints) were fixed afterwards and not re-scored.
