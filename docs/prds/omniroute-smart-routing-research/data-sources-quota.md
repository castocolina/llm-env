# Smart routing research: data sources, scoring, quota windows

Date: 2026-10-13. Scope: read-only research (web + local OmniRoute container `diegosouzapw/omniroute:next` bundle + live fetches). No system changes.
Legend: **[V]** verified from a primary source or live fetch today; **[U]** unverified, inferred, or from secondary sources.

---

## 1. Artificial Analysis (AA) API

**Source of truth:** the OpenAPI spec at https://artificialanalysis.ai/api/v2/openapi (generated 2026; downloaded today) [V]. The older page https://artificialanalysis.ai/documentation still describes a legacy `GET /api/v2/data/llms/models` at 1,000 req/day. That path still answers 401 without a key, but the spec does not document it. Treat it as legacy [V for the 401; U for whether it still serves data].

| Item | Finding |
|---|---|
| Free API exists? | Yes [V]. Create a key at artificialanalysis.ai and send it in the `x-api-key` header. |
| Free endpoint | `GET https://artificialanalysis.ai/api/v2/language/models/free`, paginated (`page`; `page_size` 200; example shows `total_pages: 2`) [V]. The non-`/free` routes are Pro+ and return 403 to Free keys [V]. |
| Rate limit | Free: 100 req per fixed 24h window. Pro: 500. The quota is shared across the user/org scope, not per key. Headers: `X-RateLimit-Limit/Remaining/Reset`; 429 includes `Retry-After` [V]. At a 6h sync with about 2 pages, that is about 8 req/day, which fits. |
| Terms / attribution | Attribution is required on every tier: "credit Artificial Analysis as the source. A visible byline or footer link is sufficient." Use is also subject to the Terms of Use PDF and the Data Platform Terms PDF. Redistribution needs a contract [V]. Practical rule: show "Scores: Artificial Analysis (link)" wherever the scorecard appears in the UI, and do not republish the raw dataset. |
| Free-shape fields | `id` (UUID, stable), `name` (can change, and includes the effort variant, e.g. "gpt-oss-20B (high)"), `slug`, `release_date`, `model_creator{id,name}`; `evaluations.artificial_analysis_intelligence_index`, `..._coding_index`, `..._agentic_index`, plus six capability indexes (finance, strategy_ops, legal, healthcare, engineering, economics); `artificial_analysis_intelligence_index_cost{total_cost, cost_per_task}`; `pricing.price_1m_input_tokens/output/cache_hit/cache_write`; `performance.median_output_tokens_per_second`, `median_time_to_first_token_seconds`, `median_time_to_first_answer_token_seconds`, `median_end_to_end_response_time_seconds`. The response also carries `intelligence_index_version` [V]. |
| Pro-only fields | The per-benchmark eval set (GPQA, HLE, Terminal-Bench, SciCode, ...), token counts, blended price, percentiles, context window, parameters, modalities, licence, omniscience/hallucination breakdown [V]. |
| Granularity | The Free and Pro tiers are **per model only**. Per-provider-endpoint data (`/api/v2/language/providers`, a `providers[]` array, 7/30/90-day performance) is **Commercial only** [V]. Speed and latency are therefore model-median figures, not a measure of your subscription endpoint. Measure that locally from OmniRoute logs. |
| Index composition | Intelligence Index v4.3.2 has 10 evals: AA-Briefcase, GDPval-AA, AutomationBench-AA, Terminal-Bench 4.0, SciCode, AA-LCR, AA-Omniscience, HLE, GDP.pdf, CritPt. The Coding and Agentic indexes are subsets and are not versioned separately [V] ([methodology](https://artificialanalysis.ai/methodology/intelligence-benchmarking)). A major version bump re-bases scores, so normalise per version (see section 4). |
| Update cadence | No schedule is published [V that it is absent]. New models are usually added within days of release, and performance is re-measured continuously [U]. |

**ID matching (hard part).** AA keys on its own slug/UUID per *model + reasoning variant*. It has no provider or plan concept. Recommended pipeline [design, U]:
1. Strip the OmniRoute provider prefix (`opencode-go/`, `cursor/`, `grok-cli/`).
2. Pivot through the models.dev `canonical_model_id` when the provider exists there. Example: models.dev `opencode-go/glm-5.3` has `canonical_model_id = zhipuai/glm-5.3` [V]. 5,913 of 8,777 models.dev entries carry one [V].
3. Normalise: lowercase; `.`, `-` and space treated as equivalent; drop `(…)` effort tags. Match on AA `slug` plus `model_creator.name`.
4. **Do not strip semantic suffixes blindly.** `-fast`, `-flash`, `-mini`, `-highspeed` and `-code` are often *different models*. `grok-cli/grok-4.7-build-fast` must not inherit `grok-4.7` at full confidence. Map such a model to its sibling with a confidence penalty, or use a manual alias.
5. Keep a plugin-settings alias table (`omniroute_id -> aa_slug | null`) and surface the unmatched models in the UI.
- `cursor/*` does not exist in models.dev [V], so Cursor models go through name stems only. For example, `cursor/grok-4.7` maps to `xai/grok-4.7`, which exists in models.dev [V].

## 2. Arena (LMArena, now arena.ai) ELO in OmniRoute

From the running container bundle (`src/lib/arenaEloSync.ts`) [V]:
- **Source:** `https://api.wulong.dev/arena-ai-leaderboards/v1/leaderboard?name={text|code}`. This is a **third-party scraper/mirror**, not an official Arena API. Its index (`/v1/leaderboards`) also lists `agent, document, search, vision, image-edit, text-to-image, text-to-video, image-to-video, video-edit` [V live].
- **Format:** `{meta:{leaderboard, source_url, fetched_at, last_updated, model_count}, models:[{rank, model, vendor, license, score, ci, votes}]}`. The `agent` board has multi-dimension metrics and no single `score` [V live].
- **Coverage:** `model_count: 20` for text and for code. **Only the top 20.** Today's code board has Claude/GPT/Gemini/Qwen/Kimi/Grok-4.7-xhigh/MiMo. **There is no GLM**, and niche or "-fast" variants are absent [V live]. The mirror's `fetched_at` was 2026-10-07, 6 days stale on 2026-10-13 [V].
- **Mapping:** `text` maps to the task categories default/review/documentation/debugging; `code` maps to coding [V].
- **Normalisation:** `score = 0.4 + 0.58·(elo − min)/(max − min)` per board, **min–max over the top 20 only** [V]. The 20th frontier model therefore gets 0.40, and the result is not comparable to AA. Confidence by votes: high ≥ 5,000, medium ≥ 1,000, low < 1,000 [V]. Entries expire after 7 days [V]. `ARENA_ELO_SYNC_INTERVAL` defaults to 86,400s, and sync is on by default [V].
- **Name matching:** lowercase, strip a trailing `(…)`, strip vendor prefixes (`anthropic/`, `openai/`, …), collapse effort and `-free` suffixes onto the base model, keeping the max [V]. On the ranking side: exact match, then version-suffix strip, then prefix match (`findMatchingIntelligence`) [V docs].
- **Biases:**
  - Length and markdown style inflate votes. Arena's *style control* regresses out answer length, header, bold and list counts ([arena.ai/blog/style-control](https://arena.ai/blog/style-control), 2024-08, updated 2025-06) [V]. Whether style control is the default view today is [U].
  - Selective private-variant testing, and more battles for proprietary labs ([Leaderboard Illusion, arXiv 2504.20879](https://arxiv.org/abs/2504.20879)) [V].
  - Prompts are single-turn and come from the general public, not agentic multi-file coding.
  - New models have wide CIs (±16 at about 2k votes in today's code board) [V].
  - The variant explosion (e.g. `-max`, `-xhigh`, `-high`) means scores are per effort level.
- **Verdict:** use Arena only as a secondary "taste" signal with weight ≤ 0.25 for planning/review, and re-normalise it yourself (see section 4).

## 3. models.dev

- **API:** `https://models.dev/api.json`, about 5.6 MB, 228 providers, 8,777 provider-models [V live]. Repo: [github.com/sst/models.dev](https://github.com/sst/models.dev) (TOML files, community PRs, CI schema validation) [V].
- **Provider fields:** `id, name, npm, api, env, doc` [V].
- **Model fields:** `id, name, description, family, canonical_model_id, attachment, reasoning, reasoning_options` (e.g. `[{type:'effort', values:[low,medium,high,xhigh]}]` or `budget_tokens{max}`), `tool_call, structured_output, temperature, interleaved, open_weights, knowledge` (YYYY-MM), `release_date, last_updated, status` (alpha/beta/deprecated), `modalities{input,output}`, `limit{context,input,output}`, `cost{input, output, cache_read, cache_write, reasoning, input_audio, output_audio, context_over_200k, tiers[{…, tier:{type:'context', size}}]}` in USD per 1M tokens [V live].
- **Subscriptions:** **not encoded as plans.** Some coding plans appear as separate providers (`zai-coding-plan`, `kimi-code-plan-global`, `minimax-coding-plan`, `alibaba-coding-plan`, …), usually with `cost = 0` [V]. There is no plan price and no 5h/7d/30d window data. `opencode-go` lists per-token *list* prices, not plan economics [V]. Cursor, Codex and Claude subscriptions are absent [V]. Quota windows must come from OmniRoute telemetry.
- **Cadence:** the repo does not state one [V absent]. It changes many times a week via PRs [U]. OmniRoute's `modelsDevSync.ts` uses `MODELS_DEV_SYNC_INTERVAL`, default 86,400s, and runs only if enabled [V bundle]. The PRD asks for 6h, which is fine because the file is static and cheap.
- **Role in the design:** the canonical join key (`canonical_model_id`, `family`), price, context, effort options, `release_date` (for "new model" handling) and `status`.

## 4. Scoring methodology (recommended)

Practitioner consensus [U, synthesis]: rank-based normalisation beats raw-score mixing; weight by signal confidence; shrink sparse models toward a prior; never let a single saturated benchmark dominate.

**Recommended scheme (simple and robust):**
1. **Normalise per source and metric to a percentile in [0,1]** within a reference cohort. The cohort is all AA models with `release_date` in the last 12 months and the same `intelligence_index_version` major version. Use the full AA list, not just your combo members, so scores are stable. For Arena, take a percentile of the raw `eloRaw` within each board. Do **not** reuse OmniRoute's 0.4–0.98 min-max.
2. **Role signals** (weights within a role; renormalise over the signals present):
   - *Planning (frontier):* AA Intelligence 0.55, AA Agentic 0.20, Arena text 0.25.
   - *Audit/review:* AA Intelligence 0.45, AA Coding 0.30, Arena text 0.25. (AA hallucination/omniscience would be ideal but is Pro-only.)
   - *Execution/coding:* AA Coding 0.45, AA Agentic 0.35, Arena code 0.20.
   - *Cheap-fast:* AA Intelligence 0.35, inverse price percentile 0.35, speed percentile 0.30. For speed, prefer locally measured tok/s and TTFT from OmniRoute over AA's model median.
3. **Confidence:** `c = Σ w_i·conf_i` over the signals present. `conf` = 1 for AA, and 1/0.7/0.4 for Arena by vote tier. Multiply by staleness `exp(−age_days/14)` on the last *successful* sync.
4. **Missing-data shrinkage:** `score = c·raw + (1 − c)·prior`.
   - The prior is the **sibling/family score** when one exists: the same models.dev `family` or AA creator plus name stem, e.g. `glm-5.3` takes `glm-5.2`, and `-fast` takes its base model. Apply a −0.05 penalty for variant/size mismatch.
   - Otherwise the prior is the **role cohort median**. With percentiles that is ≈ 0.5, so the PRD's "medium default 0.5" is equivalent, but sibling imputation should come first.
   - Store `score_source ∈ {aa, aa+arena, sibling:<id>, websearch, medium_default}` and `confidence`.
5. **Effort variants:** match the requested effort to the AA/Arena variant. If the effort is unknown, use the default variant for execution and the highest variant for planning, minus 0.03.
6. **Hysteresis:** only re-order combos when a score moves by more than 0.05. This avoids flapping between syncs.

**Caveats:**
- Public static benchmarks saturate and leak. OpenAI stopped reporting SWE-bench Verified in 2026, citing contamination and defective tests. Frontier models reproduce gold patches ([Epoch review](https://epoch.ai/benchmarks/swe-bench-verified/review)) [V secondary].
- Prefer time-segmented or held-out sets ([LiveCodeBench](https://arxiv.org/abs/2403.07974)), and AA's proprietary evals (Briefcase, GDPval-AA, Terminal-Bench 4.0).
- Index versions change scores. Re-normalise on version change.
- Your own observed outcomes (success rate and retry rate per role) are the least contaminated signal. Log them now and blend them in later.

## 5. Quota-window prioritisation (owner's question)

**Key facts:**
1. A request on subscription *c* consumes **every** window of *c* at once (5h, 7d, 30d).
2. Headroom left in a window at its reset is forfeited.
3. A short window that resets soon is only *free* to use if the longer windows of the same subscription are not scarce.

So "resets in 5 minutes" alone says nothing about whether a request there is cheap.

**Recommended rule: "binding-window shadow cost, then forfeiture rate"** (FEFO from perishable-inventory practice, applied only to headroom that is genuinely forecast to expire).

Per candidate *c* and each window *w*:
- `h_w = r_w − reserve` (% points; reserve default 3). **Feasible** iff `h_w ≥ est_cost_w` for **all** *w*, where `est_cost_w = k_w × est_$` (section 6). The tightest window decides feasibility.
- `T_w` = hours to reset, floored at 0.25h. If the subscription **expires (does not renew)**, cap every `T_w` at the time to expiry.
- `b_w` = EWMA burn rate (%/h) on that window. Prior if there is no history: `(100 − reserve)·u / L_w` with u = 0.8.
- `scarcity_w = min(1, b_w·T_w / h_w)`. This is ≈ the probability the window binds before reset (its shadow price).
- `forfeit_w = max(0, h_w − b_w·T_w)`.
- **S_c = max_w scarcity_w** (the binding window).
- **W_c = (1 − S_c) · max_w (forfeit_w / k_w) / T_w**, in $-equivalent per hour forfeited. If `k_w` has not been learned yet, use only the longest window.

**Ranking:**
1. Hard gates: combo membership, context/tools/modality fit, circuit-breaker cooldown, all-window feasibility, role quality floor (`Q ≥ Q_best − 0.10`).
2. Lexicographic class: subscription candidates with `S < 0.9` first, then subscriptions with `S ≥ 0.9` (reserve protection), then metered.
3. Weighted utility within the class, with all terms in [0,1]:
   - Subscription class: `U = wQ·Q_role + wS·(1 − S_c) + wW·Wn + wO·order + wH·(1 − F)`.
   - Defaults for plan/review: Q .50, S .25, W .10, O .05, H .10. Execution: Q .30, S .30, W .20, O .10, H .10. Cheap-fast: Q .20, S .30, W .20, O .10, H .20.
   - `Wn = log1p(W)/log1p(max W)` across candidates. `order = 1 − rank/(n − 1)`.
   - Metered class: replace the S and W terms with `0.5·(1 − price_pct)` and set wS + wW onto that term.
- **Why the hybrid:** pure lexicographic order (the PRD's current "30d → 7d → 5h, then %…") ignores magnitudes. A pure weighted sum lets quota math trade into a much worse model. Gates plus classes plus a small weighted score is explainable in the UI and robust.
- **Failures:** EWMA error rate `F` (α = 0.3 per request, or a 30-min half-life). Exponential cooldown after consecutive failures: `min(30s·2^(n−1), 30 min)`, honouring `Retry-After`. A 429 or quota error marks that window exhausted until its reset (no penalty to F). 5xx and timeouts feed F. Content and 4xx errors do not.

**Worked example** (reserve 3; numbers computed):

*Case 1 (owner's).* A is a plan whose cycle ends in 24h. B's 5h window resets in 5 min.

| Candidate | Window | r | T | b | scarcity | forfeit |
|---|---|---|---|---|---|---|
| A | 5h | 100 | 5h | 6 | 0.31 | 67 |
| A | 7d | 70 | 72h | 0.5 | 0.54 | 31 |
| A | 30d | 60 | 24h | 0.8 | 0.34 | 37.8 |
| B | 5h | 80 | 0.08h | 6 | 0.02 | 75.5 |
| B | 7d | 40 | 96h | 0.6 | **1.00** | 0 |

- A: S = 0.54 if it renews, and S = 0.34 if it expires (7d capped to 24h, so scarcity 0.18).
- B: S = 1.0. Its 7d window will run out before reset, so every request on B burns scarce weekly quota. The 5-minute 5h reset is irrelevant.
- **Shadow rule:** A. **Soonest-reset-first:** B, which is wrong: it exhausts B's week early while A forfeits about 38–55 points tomorrow. **Longest-cycle-first:** A (correct here).
- *Case 1b:* B's 7d is healthy (r = 90, so scarcity 0.66). The rule still picks A (0.34/0.54 vs 0.66). B's huge 5h forfeiture only matters when B's longer windows are also slack.

*Case 2 (longest-cycle-first fails).*

| Candidate | Window | r | T | b | scarcity | forfeit |
|---|---|---|---|---|---|---|
| C | 7d | 60 | resets in 2h | 1.0 | 0.04 | 55 points in 2h |
| D | 30d | 50 | 15 days | 0.2 | 1.0 | 0 |

- Longest-cycle-first picks D, which wastes C's 55 points and drives D to exhaustion.
- Soonest-reset-first picks C (correct here).
- The shadow rule picks C (S 0.31 vs 1.0).

The rule matches whichever heuristic is right in each case, because it prices the *binding* window and only rewards *forecast* forfeiture.

## 6. Token/cost estimation when plans expose only %

- **Input:** use `chars/4` for English and code (about ±25%), and `chars/1.5` for CJK [U heuristic]. Better: learn a per-model `ratio_m = EWMA(usage.prompt_tokens / chars)` from responses, which are free and exact after the first few calls. A local tokenizer (tiktoken `o200k`) is optional for OpenAI-family models only.
- **Output:** use the EWMA p75 of `completion_tokens` (including reasoning) per (model, task class, effort) bucket. Cold-start defaults: tiny/classify 300; edit/exec 2–4k; review 3k; plan with high effort 8–16k [U, tune].
- **`est_$`** = in·price_in + cached·price_cache + out·price_out, at models.dev list prices.
- **Learning % per unit: feasible, with care** [design, U].
  - Unit = list-price dollars. This auto-weights Opus vs Sonnet and output vs input, the way plans do.
  - Per window, `k_w = Σ Δ% / Σ $routed`, a ratio-of-sums over snapshot intervals.
  - Keep only intervals with Δ ≥ 2 points (percent values are rounded to 1%). Drop intervals spanning a reset or showing negative Δ (rolling windows release old usage).
  - Flag external use: Δ% ≫ k·$ means other clients (Claude Code, Codex used outside OmniRoute) share the plan. Exclude those intervals.
  - Use an EWMA of k with a floor/cap. It needs roughly 10–20 qualifying intervals per window and polling at least every few minutes while active.
  - It is also biased by plan-side weighting changes. Re-learn after a drift detector fires, e.g. residual > 2σ.
  - Until k is learned, gate only on `r_w ≥ reserve + tinyTaskMin` (PRD thresholds).

## Sources
- AA OpenAPI: https://artificialanalysis.ai/api/v2/openapi ; AA docs (legacy): https://artificialanalysis.ai/documentation ; AA methodology: https://artificialanalysis.ai/methodology/intelligence-benchmarking
- Arena mirror used by OmniRoute: https://api.wulong.dev/arena-ai-leaderboards/v1/leaderboards ; OmniRoute bundle `src/lib/arenaEloSync.ts` (container `omniroute`, `/app/.build/next/server/chunks/48446.js`)
- Arena style control: https://arena.ai/blog/style-control ; Leaderboard Illusion: https://arxiv.org/abs/2504.20879
- models.dev: https://models.dev/api.json , https://github.com/sst/models.dev
- Contamination: https://epoch.ai/benchmarks/swe-bench-verified/review , https://arxiv.org/abs/2403.07974
- Raw downloads: `prd-research/dl/` (aa-openapi.json, arena-*.json, modelsdev.json)
