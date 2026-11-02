# Frontier anchors: published numbers and how to use them

Loaded when the user names a cloud anchor, with `references/scoring.md`.

Ask yourself first:
- Is the comparison fair: one seed set, the same efforts and timeout, correctness only?
- Does a working route exist for each anchor before any quota is spent?
- Will a published number be quoted with its benchmark version and effort, or not at all?

## Published figures (retrieved 2026-10-30 from secondary coverage; re-check before quoting)

| Anchor | Benchmark | Score | Source note |
|---|---|---|---|
| Claude Haiku 4.5 | SWE-bench Verified | 73.3 % | Anthropic; mean of 50 trials, 128K thinking budget, 500 problems |
| Claude Haiku 4.5 | Terminal-Bench (Terminus 2) | about 41 % (40.2 no thinking, 41.8 with 32K) | Anthropic; one aggregator lists 35.5, a different version |
| GPT-5.6 Luna | Terminal-Bench 2.1 | 80.9 max / 53.2 medium / 43.4 low effort | Artificial Analysis via OpenRouter; provider claims 84.7, Vals 79.0 |
| GPT-5.6 Luna | SWE-bench | 93.0 (Vals) vs 38.8 (aggregator, "Verified", max effort) | Conflicting: do not quote either unaudited |
| GPT-5.6 Luna | SWE-bench Pro | 62.7 % | BenchLM |
| Composer 2.5 | SWE-bench Multilingual | 79.8 % (Cursor) vs 71.6 % (BenchmarkList) | Cursor's own harness |
| Composer 2.5 | Terminal-Bench 2.0 / 2.1 | 69.3 % (Cursor, 2.0) / 76.0 % (BenchmarkList, 2.1) | Different benchmark versions |
| Composer 2.5 | CursorBench v3.1 | 63.2 % (Cursor) / 56.1 % (BenchmarkList) | Private benchmark, not reproducible |

Sources: openrouter.ai/openai/gpt-5.6-luna, benchlm.ai, datacamp.com/blog/composer-2-5, benchmarklist.com/providers/cursor,
anthropic.com Haiku 4.5 announcement coverage. Newer siblings exist in the pi catalog (`claude-haiku-5-5`,
`gpt-6-luna`); mention them when the user's named anchors are no longer the current tier.

## How to use them

- Treat the table as order of magnitude. Effort level alone moves Luna's Terminal-Bench by 2x, and the same benchmark
  name hides version changes. Never put a local model and an anchor on one line unless both rows state version and effort.
- Target for a model of about 10 GiB: the anchor's verifier pass rate on small, well-specified edits at lower effort, not its
  Terminal-Bench or SWE-bench score.

## Anchor lanes

1. **Route check** on the router profile, after the rule 3 profile check (`SKILL.md`). When the comparison runs a local lane
   itself, `bash $SK/scripts/preflight.sh` comes first, before that profile check too: a gateway-only host stops there (`references/scoring.md`, Serving the
   lane's model), before any route check. The check:
   `PI_CODING_AGENT_DIR=$HOME/.pi/agent pi auth check --model <provider/id>`. Composer 2.5 has no pi route. It may be reachable
   through `cursor-agent` (the user has used it as an execution lane): run `cursor-agent models` to see whether it lists a
   Composer 2.5 id and whether auth works (on 2026-10-30 `status` said "Logged in" but `models` failed with "Authentication
   required"). `grok-composer-2.5-fast` inside the OmniRoute `my-coding` combo is a Grok Build variant, not Cursor's model:
   never count it as the anchor. If no verified route exists, compare by published numbers only and say so.
2. **Gate G4** before the first anchor run and before any switch of anchor model or provider: anchor runs spend real quota.
3. The first local lane of the comparison (the served model; when an onboarding run already has lanes, its baseline lane)
   writes `seeds.json` with `--per-layer 3`; reuse a recorded lane when its `challenge_set` matches. Each anchor lane (and an
   onboarding candidate) takes `--seeds` that file, with the same `--efforts` and `--timeout`, one lane at a time.
   A local lane the comparison runs itself needs llm-server serving it: `references/scoring.md`, Serving the lane's model, on
   item 1's preflight read, and `make stop-llama` after the last local lane when that rule started it.
   Their `--out` (the `<out>` of `references/scoring.md`, Running a lane): `--out tmp/model-scout/<run>/effort-<alias>` only for a local lane the comparison runs itself, and `--out tmp/model-scout/<run>/effort-<anchor>` per anchor (`<anchor>`: its model id with `/` as `-`). `<run>` is `<YYYYMMDD>-anchors`, or the onboarding run's: inside one the comparison reuses that run's local lanes (their own folders) and adds only the `effort-<anchor>` lanes, never a second local lane.
4. `bash $SK/scripts/compare-runs.sh --correctness-only <local results.jsonl> <anchor results.jsonl>`, local lane first. Report
   local overall / anchor overall and both ladders. Correctness-only, because the thermal and CPU terms come from the local
   machine and would hand a cloud anchor full marks.
5. A 401 or a "usage limit" arrives as a completed run with `stopReason: error`; `effort-run.sh` marks it `api_error` and
   `score.sh` excludes it (on 2026-10-30 `opencode/claude-haiku-4-5` returned 401 and `openai/gpt-5.6-luna` hit a subscription
   usage limit). Follow the AGENTS.md retry ladder; never switch anchor or provider; published numbers only as a labelled,
   version-caveated fallback.
6. Retire the seed set after any cloud lane; it is never reused locally.
