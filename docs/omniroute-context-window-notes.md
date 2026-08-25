# OmniRoute context-window / quota reporting — investigation notes

Working notes from live investigation of `diegosouzapw/OmniRoute` (MIT,
<https://github.com/diegosouzapw/OmniRoute>), kept for a possible upstream
issue/PR. Not upstream documentation — this is our own record of what we
found, when, and how we verified it.

**Deployed version at time of writing:** `v3.8.49` (image built
2026-07-30, per `podman inspect omniroute --format '{{.Created}}'` and
`/app/package.json`'s `version` field inside the container). No newer
tagged release exists as of 2026-08-25 (`v3.8.49` is still the latest
GitHub release), though several relevant fixes have merged to the
`diegosouzapw/OmniRoute` main branch after that release and are not yet in
a tagged version or (apparently) in the `:latest` image we're running.

## 1. Catalog/combo context-window does not reflect manual overrides

**Symptom:** setting a context-window override via
`PUT /api/provider-models` (`{provider, modelId, contextWindowOverride}`)
correctly changes runtime enforcement (`resolveComboContextLimit()` in
`open-sse/services/contextManager.ts`, verified live via OmniRoute's own
routing logs — see below) but `GET /v1/models` and `GET /api/combos`'
`computed_context_length` keep displaying the old, pre-override number
indefinitely.

**Root cause (read from source, `src/lib/combos/comboContext.ts` and
`src/app/api/v1/models/catalog.ts`):** both resolve context via a
`synced -> registry -> spec` chain and never consult the
`model_context_overrides` DB table (the "Feature 5004" mechanism, see
upstream #6822). Only `getModelContextLimit()`
(`src/lib/modelCapabilities.ts`), used by the real-time enforcement path,
reads the override table.

**Upstream status: already fixed, not yet in our deployed image.**
- [#10530](https://github.com/diegosouzapw/OmniRoute/issues/10530) "fix(models): combo catalog ignores explicit Codex context override" — closed 2026-08-18
- [#10533](https://github.com/diegosouzapw/OmniRoute/issues/10533) "fix(models): correct Codex context and combo limit resolution" — closed 2026-08-18
- [#10734](https://github.com/diegosouzapw/OmniRoute/issues/10734) "fix(catalog): combo context_length advertised as 128k when members are 500k" — closed 2026-08-20
- [#10793](https://github.com/diegosouzapw/OmniRoute/issues/10793) "fix(catalog): exclude generic 128k default from combo context min() (#10734)" — closed 2026-08-20

**Live verification (this session, 2026-08-25):**
- Applied `contextWindowOverride: 1050000` to all 20 codex GPT-5.6 catalog
  variants (bare + all 6 effort suffixes × sol/terra/luna) via
  `pylib/omniroute.py::fix_codex_context_overrides()`.
- `/v1/models` and `/api/combos` still showed the old value after the
  override (confirmed the display bug directly).
- OmniRoute's own container logs (`podman logs omniroute`), captured
  during a real request routed through combo `my-coding`, showed the
  *enforcement* engine correctly using the override:
  `Combo context limit: 1050000 (source=target)` for
  `codex/gpt-5.6-terra-high`, and
  `Combo context limit: 200000 (source=target)` for
  `grok-cli/grok-composer-2.5-fast` — proving the override is live
  server-side even though the catalog/combo GET endpoints never reflect
  it.

**Our workaround (this repo):** `pylib/omniroute.py::compute_combo_context()`
recomputes the true minimum per combo directly (catalog + known
corrections), bypassing OmniRoute's buggy read path entirely. Exposed as
`make combo-context` / `llmenv omniroute combo-context`.

## 2. Open question: is 272,000 or 1,050,000 the real Codex-routed limit?

**This is unresolved — do not assume either number is correct without
re-testing after an image upgrade.**

Per #10530's own bug description, the OmniRoute maintainers *deliberately*
set Codex-provider (`codex/gpt-5.6-sol|terra|luna`) context to **272,000**,
not the direct-API 1,050,000, because "the official Codex catalog reports
272K for Codex-connected models"
([openai/codex `models.json`](https://github.com/openai/codex/blob/9ded177ce7c1c0bd2047f902936c177612ab3434/codex-rs/models-manager/models.json)).
Their position: earlier PRs (#9431/#9432, unverified by us) had wrongly
changed this to 1,050,000, and #10530/#10533 revert it back to 272,000 as
the *correct* value for Codex-routed access specifically (vs. the direct
OpenAI API, which does get the full 1.05M).

**This directly contradicts our own empirical result.** This session, a
synthetic prompt tokenizing to 591,211 tokens was sent to
`cx/gpt-5.6-sol-high` via `POST /v1/chat/completions` and returned
**HTTP 200 with a correct response** — a token count that would be
impossible if 272,000 were a hard, enforced ceiling for this route. (Real
cost incurred: ~$2.96 for that request, ~$4.09 total across both test
sizes this session — see conversation history.)

**Possible explanations, none confirmed:**
- OmniRoute's actual upstream call for `codex/*` doesn't reproduce Codex
  CLI's own product-layer 272K cap (i.e. it calls a less-restricted
  endpoint/parameter set than Codex CLI itself would use), even though the
  *catalog metadata* the maintainers chose to display matches Codex CLI's
  advertised number.
- OpenAI may have changed the effective Codex-routed limit after
  `openai/codex`'s `models.json` snapshot the maintainers cited was taken.
- Something about this specific OmniRoute account/connection differs from
  the "typical" Codex-connected account the maintainers modeled.

**Before filing anything upstream about this:** pull the current
`:latest` image (or the next tagged release once one exists past
`v3.8.49`), re-run `make fix-codex-context` (idempotent, safe to re-run),
then re-attempt a large-prompt empirical test against `cx/gpt-5.6-sol-high`
to see whether 272K is now actually enforced. If it still succeeds past
272K, that's strong evidence worth reporting back to #10530's thread
directly rather than as a new issue.

## 3. grok-composer-2.5-fast invisible in `/v1/models`

**Symptom:** `grok-composer-2.5-fast` (routed via the `grok-cli` /
"Grok Build" provider) never appears in `GET /v1/models`, whether queried
with a Bearer API key or the dashboard session cookie — confirmed both
ways, case-insensitive. It is nonetheless a real, working model: it's a
live member of the `my-coding` combo, handles the majority of that combo's
traffic per dashboard stats, and OmniRoute's live routing logs show it
being dispatched and succeeding on real requests.

**Root cause (per prior-session code reading, unverified against latest
source in this pass):** OmniRoute's Grok Build catalog normalizer
deliberately excludes models flagged `supported_in_api=false` from
API-key/catalog-facing listings, while still permitting session-routed
combo dispatch to use them.

**Upstream status: no matching issue found.** Searched for `composer`,
`supported_in_api`, and related terms in the issue tracker — nothing
matches this specific behavior. The closest related issue,
[#11475](https://github.com/diegosouzapw/OmniRoute/issues/11475) (open,
"[bug] Hidden models still listed in GET /v1/models — filter not applied
in unified catalog"), is the **opposite** direction (models that should be
hidden are shown; ours is a model that should probably be shown but is
hidden). Likely needs a new issue if pursued.

**Our workaround:** `pylib/omniroute.py`'s
`GROK_COMPOSER_2_5_FAST_CONTEXT_WINDOW = 200_000` constant, hardcoded
because there is no API path to discover this model's context window —
verified via independent web research against xAI's own materials (see
below), not OmniRoute.

## 4. xAI / Grok Build has no visible weekly quota dashboard

**Symptom:** unlike OpenCode Go and OpenAI/Codex connections, the xAI
(`grok-cli`) connection in the OmniRoute dashboard shows no "time window
used" / weekly-quota meter.

**Upstream status: the feature exists, closed.** Weekly quota tracking
for Grok Build / xAI OAuth connections has multiple closed
feature/fix issues:
- [#6844](https://github.com/diegosouzapw/OmniRoute/issues/6844) "feat(providers): Grok Build (grok-cli) quota tracking"
- [#6444](https://github.com/diegosouzapw/OmniRoute/issues/6444) "feat(providers): add weekly quota tracking for Grok x.com web (grok-web) and SuperGrok / X Premium+ oauth variants"
- [#8471](https://github.com/diegosouzapw/OmniRoute/issues/8471) "feat(providers): weekly quota for xAI OAuth (Grok)"
- [#9205](https://github.com/diegosouzapw/OmniRoute/issues/9205) "feat(usage): show Grok Build billing limits"
- [#7714](https://github.com/diegosouzapw/OmniRoute/issues/7714) "feat: add live gRPC-web quota fetcher for grok-cli (#6844)"

Also relevant: [#10728](https://github.com/diegosouzapw/OmniRoute/issues/10728)
"fix(providers): SuperGrok x_search still 400s on published OmniRoute
(v3.8.49); #9111 unreleased" — explicitly confirms `v3.8.49` (our
deployed version) is missing at least one xAI-related fix that exists
upstream but was unreleased as of that issue.

**Not yet root-caused on our side** — this needs checking directly in the
dashboard UI (is the meter present but empty/zero, or entirely absent from
the connection's detail view?) before concluding whether it's a display
bug, a version gap, or something specific to the `casto-dev` connection's
auth type (`oauth`, scope includes `grok-cli:access`).

## Model context-window ground truth (verified against each lab's own
material, not OmniRoute, this session — see conversation for full source
list)

| Model | Real context window | Source |
|---|---|---|
| GPT-5.6 Sol/Terra/Luna (direct API) | 1,050,000 tokens, 128K max output | openai.com/index/gpt-5-6 (primary) |
| GPT-5.6 via Codex CLI product layer | 272,000 tokens (disputed — see §2) | openai/codex `models.json`, per OmniRoute maintainers |
| Kimi K3 | 1,048,576 tokens (1M) | platform.kimi.ai docs (primary) |
| Kimi K2.7 Code | 262,144 tokens (256K) | platform.kimi.ai docs (primary) |
| Grok 4.6 | 500,000 tokens | xAI launch specs, cross-checked by independent trackers |
| Grok Composer 2.5 (Grok Build) | 200,000 tokens | **No primary xAI page found** — `docs.x.ai/docs/models` and the REST models reference do not list it at all; best available is convergent independent tracker reporting |

## Next steps if pursuing an upstream contribution

1. Pull the current `:latest` OmniRoute image (or wait for a release past
   `v3.8.49`) and re-verify whether §1's catalog-override propagation bug
   is actually fixed for us, and whether §2's 272K-vs-1.05M question
   resolves differently once #10530/#10533/#10734/#10793 are live.
2. If the 591K-token empirical result still contradicts 272K post-upgrade,
   comment on #10530 directly with the reproduction steps rather than
   opening a competing issue — the maintainers are already engaged with
   this exact number.
3. §3 (composer-2.5-fast invisibility) looks like a clean, uncontested new
   issue if the behavior still reproduces post-upgrade.
4. §4 needs a dashboard screenshot / more direct reproduction before it's
   issue-shaped.
