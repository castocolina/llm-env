# Handoff: Transparent Checks and Vulkan-Only Work

## Session Metadata
- Created: 2026-07-28T06:44:40-04:00
- Project: `/var/home/bazzite/git/llm-env`
- Branch: `main` (the human explicitly authorized working on main)

## Current State Summary

The project shows complete redacted diagnostics for setup and all check targets, uses a Vulkan-only benchmark with CPU fallback, and adds live Pi/OpenCode verification. A real E2E run exposed a benchmark regression: Vulkan produced valid JSON but warnings were combined with JSON before parsing; `jq` rejected it, and `benchmark.sh` configured CPU fallback. The fix captures stdout and stderr separately, parses only stdout, and preserves a nonzero result when Vulkan fails.

## Critical Files

| File | Purpose | Current relevance |
|---|---|---|
| `benchmark.sh` | Vulkan benchmark and fallback | Regression at `run_vulkan_bench`: redirects `>"$output" 2>&1`, then parses mixed warning plus JSON with jq |
| `check-setup.sh` | Offline model smoke check | Still hides command/prompt/raw output; transparency Task 3 has not run |
| `check-server.sh` | Online API contract | Works now; uses prompt `Reply with exactly: ready`, 256 tokens, but hides payload/raw response |
| `check-with-agents.sh` | Pi/OpenCode live-data check | Live run fails due parser/snapshot/prompt defects described below |
| `setup.sh` | Interactive GPU/model setup | Task 2 now displays total/used/free VRAM |
| `lib.sh` | Shared helpers | Task 1 added secure redaction and artifact helpers; reviewed clean after two fixes |
| `docs/superpowers/specs/2026-07-27-transparent-check-diagnostics-design.md` | Approved diagnostic/Vulkan-only design | Includes setup, benchmark, all checks, and live agents |
| `docs/superpowers/plans/2026-07-27-transparent-checks-and-vulkan-only.md` | Active implementation plan | Task 1 complete; Task 2 implementation needs scoped re-review; Tasks 3–5 pending |

## Work Completed

### Completed commits

- `459c706 fix: allow reasoning models to complete server check` — 256 tokens are required because Ornith may spend short budgets in reasoning before emitting `ready`; `make check-server` passed live afterward.
- `bc0a7de`, `dc320b4`, `9812a44`, `8b2a77d` — initial `check-with-agents` scaffold/adapters. Not live-ready.
- `eea4b8b`, `9222999`, `352a774` — redaction/artifact helpers. Task 1 review clean after two fix rounds; 127 tests passed at that point.
- `ae005fd refactor: use Vulkan as the sole GPU backend` — project configuration records Vulkan throughput and uses CPU fallback when Vulkan fails.
- `c8c92f5 test: cover Vulkan-only fallback behavior` — Task 2 fix added direct load migration and CPU fallback ordering tests; 137 tests passed.

### Decisions

- Use Vulkan as the only GPU benchmark path. On a Vulkan failure, configure CPU fallback and report a nonzero result.
- Checks must show full default command, prompt/payload, raw redacted result, parsed result, expectation, and a precise failure reason. `LLM_ENV_KEEP_CHECK_ARTIFACTS=1` keeps only private redacted artifacts.
- Agent checks must use every installed agent × every API-listed model × weather and USD→CLP.
- Live agent checks require agents to independently fetch public data. Never put expected snapshot data in prompts.
- User explicitly requested no more generic `fail` output or hidden commands/results.

## Context for Resuming Agent

### Important Context

The user is frustrated by hidden check commands/results and by claims made before live acceptance. Do not claim a target works from mocked tests alone. Run the real target, show its redacted command, prompt, output, parsed value, expectation, and precise failure diagnostic. Preserve the running service until the benchmark regression is fixed.

## Root Causes Established

### Benchmark regression

The live Vulkan benchmark output was valid JSON preceded by:

```text
WARNING: radv is not a conformant Vulkan implementation, testing use only.
```

`benchmark.sh` captures both streams into one file and parses that entire file with jq. The correct fix is to capture stdout and stderr separately, display both redacted, and parse only stdout. Add a failing regression test with warning-on-stderr plus valid JSON before changing production code. After verified Vulkan succeeds, restore persisted `.gpu.backend` and `.gpu.image` to Vulkan through the fixed benchmark; do not manually claim the benchmark passed.

### Agent check live failures

`make check-with-agents` failed all four rows with generic `agent invocation failed`. Manual isolated runs proved both clients worked:

- Pi connected through `http://llm.local:8000/v1`, called Bash/curl against Open-Meteo, and returned a JSON object inside a ```json fence.
- OpenCode connected through the same URL, called Bash/curl, and returned the same fenced JSON form.

Current harness defects:
1. It rejects Markdown fenced JSON although real clients produce it.
2. It fetches weather/FX snapshot once before the full matrix; agent fetches later and can produce a different valid timestamp.
3. It includes the expected source snapshot in the agent prompt, allowing copying rather than proving live access.
4. It cleans captured errors before printing them, causing useless diagnostics.
5. It still needs the newly approved full output format.

The active plan Task 4 explicitly addresses these defects.

## Pending Work

### Immediate Next Steps

1. Run the required scoped review for Task 2 fix `c8c92f5` against `ae005fd`; review package creation command:
   ```bash
   /home/bazzite/.pi/agent/git/github.com/obra/superpowers/skills/subagent-driven-development/scripts/review-package docs/superpowers/plans/2026-07-27-transparent-checks-and-vulkan-only.md ae005fdf3cb15597beb932cf5b1c33698f51b82d c8c92f5b33c5d5120d230ecafa522f36a20b7f18
   ```
2. Fix the benchmark mixed-stream parsing regression with TDD. Run `make benchmark`, then confirm config has `backend: vulkan` and Vulkan image before any restart.
3. Continue active plan Task 3 with a fresh OpenAI implementer: full `check-setup` and `check-server` diagnostics.
4. Continue active plan Task 4: correct live-agent snapshots, fenced JSON, source leakage, and full trace output. Run real `make check-with-agents` before claiming success.
5. Complete Task 5 docs and single-model Gemma4/Ornith E2E.

### Open Questions

- The live agent prompts/strict parser should accept only one bare object or one JSON fenced object. This is approved in the diagnostics spec.
- `check-with-agents` may emit arbitrary agent shell output; redact the known local key and bearer values before display/retention. Do not print raw unredacted transcript.

## Environment State

- `llm-server.service` is active.
- Persisted config currently says `backend: cpu`, image `ghcr.io/ggml-org/llama.cpp:server`, model `ornith` because of the benchmark regression. The running server was launched earlier using Vulkan; do not restart before fixing/re-running benchmark.
- Local models directory: `~/llm-workspace/models`.
- Current selected model: Ornith.
- Do not record or print the API key.

## Verification History

- `make check-server` passed live after `459c706`: health, auth 401, model listing, Ornith `ready` completion.
- Earlier `make check-setup` passed live for Ornith but still showed only summaries.
- `make check-with-agents` failed live because of harness/parser diagnostics, not client endpoint reachability.
- Last Task 2 implementation verification: `make validate && make test`, 137 tests passed. Task 2 fix has not received its scoped re-review.

## SDD Ledgers

- Previous agent-check plan ledger: `.superpowers/sdd/2026-07-27-agent-inference-checks-and-client-docs/progress.md`.
- Active plan ledger: `.superpowers/sdd/2026-07-27-transparent-checks-and-vulkan-only/progress.md`.

## Related Resources

- Approved design: `docs/superpowers/specs/2026-07-27-transparent-check-diagnostics-design.md`
- Active plan: `docs/superpowers/plans/2026-07-27-transparent-checks-and-vulkan-only.md`
- Earlier agent plan: `docs/superpowers/plans/2026-07-27-agent-inference-checks-and-client-docs.md`
