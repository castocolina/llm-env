# Handoff: Transparent Checks and Vulkan-Only Work

## Session Metadata

- Created: 2026-07-28T06:44:40-04:00
- Project: `/var/home/bazzite/git/llm-env`
- Branch: `main` (the human explicitly authorized work on main)

## Completion State

The transparent-check implementation is complete. The project uses a
Vulkan-only benchmark. It captures and prints redacted stdout and stderr
separately, parses throughput from stdout, and configures CPU fallback while
returning nonzero when Vulkan fails.

`check-setup`, `check-server`, and `check-with-agents` print redacted command,
input, stdout, stderr, parsed result, expectation, and verdict records.
`LLM_ENV_KEEP_CHECK_ARTIFACTS=1` retains only redacted private artifacts.

`check-server` runs the fixed local `Reply with exactly: ready` contract.
`check-with-agents` is opt-in and has Pi and OpenCode independently retrieve
public weather and USD-to-CLP data before comparing their evidence with a fresh
source snapshot.

## Implemented Components

| File | Current behavior |
|---|---|
| `scripts/benchmark.sh` | Vulkan-only throughput measurement; CPU fallback and nonzero status on failure |
| `scripts/check-setup.sh` | Offline validation with complete redacted inference records |
| `scripts/check-server.sh` | Deterministic local OpenAI-compatible API records for the `ready` contract |
| `scripts/check-with-agents.sh` | Opt-in Pi/OpenCode live weather and FX evidence checks with per-row source snapshots |
| `setup/setup.sh` | Numbered GPU/model setup with measured total, used, and free VRAM |
| `tools/lib.sh` | Redaction and private diagnostic-artifact helpers |

## Completed Commits

- `ae005fd refactor: use Vulkan as the sole GPU backend`
- `c8c92f5 test: cover Vulkan-only fallback behavior`
- `88baa45 fix: parse Vulkan benchmark stdout separately`
- `4e739ac fix: log Vulkan benchmark parser stderr`
- `bf719c5 feat: show complete offline check diagnostics`
- `89a1fd8 feat: expose deterministic server check records`
- `cd1605d fix: trace and validate live agent evidence`
- `09a0667 docs: describe Vulkan-only diagnostic checks`

## Verification

- The focused documentation test checks the current Vulkan-only, redacted
  diagnostic, deterministic-server, and opt-in live-agent documentation.
- `make validate && make test` validates shell and Python quality gates and
  runs the complete test suite.
- `git diff --check` verifies documentation whitespace before commit.

## Related Resources

- Diagnostic design: `docs/superpowers/specs/2026-07-27-transparent-check-diagnostics-design.md`
- Implementation plan: `docs/superpowers/plans/2026-07-27-transparent-checks-and-vulkan-only.md`
- Task report: `.superpowers/sdd/task-5-report.md`
