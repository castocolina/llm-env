# Transparent Check Diagnostics Design

## Goal

Make every check show what it ran, what it asked, what it received, how it evaluated the result, and why a failure occurred, without exposing the local API key or bearer header. This applies to every validation operation, not only inference.

## Scope

This design changes `setup/setup.sh`, `scripts/benchmark.sh`, `scripts/check-setup.sh`, `scripts/check-server.sh`, and `scripts/check-with-agents.sh`. It also fixes the live-agent harness defects found in real execution: stale matrix snapshots, expected-value leakage in prompts, fenced JSON rejection, and generic agent failure messages.

## Safety Rules

- Redact the configured API key and every `Authorization: Bearer` value from all displayed commands, output, errors, and retained artifacts.
- Never display curl configuration-file content.
- Commands that need authentication display `Authorization: Bearer <redacted>`.
- Keep temporary workspaces mode 700 and generated config/auth files mode 600.
- Preserve only redacted diagnostics. Do not retain a raw file before redaction.

## Common Check Output

Every validation operation prints these sections when applicable, in this order:

1. **Identity:** check name, client when applicable, model alias, and source check when applicable.
2. **Command:** the exact executable and arguments, with secret values replaced by `<redacted>`.
3. **Input:** the exact prompt or HTTP JSON payload.
4. **Raw result:** complete redacted stdout and stderr, shown as separate streams.
5. **Parsed result:** extracted response content or evidence JSON.
6. **Expectation:** the exact expected normalized value or source snapshot.
7. **Verdict:** PASS or FAIL with a precise stage and reason.

A failure must name one stage: command exit, HTTP response, response parsing, missing assistant content, normalized-value mismatch, source fetch, agent evidence parsing, source-evidence mismatch, or skipped dependency. It must include the relevant redacted diagnostic rather than only `fail` or `agent invocation failed`.

Checks continue after independent failures and exit nonzero after reporting every failure. A dependent operation that cannot run prints `SKIP`, names the failed prerequisite, and does not hide its blocking reason.

`LLM_ENV_KEEP_CHECK_ARTIFACTS=1` preserves a mode-700, redacted diagnostic directory and prints its path. The default removes it after printing the same redacted data.

## Setup GPU Selection

Before the numbered GPU prompt, `setup` shows the measured values from `llmenv detect` for every GPU: total VRAM, used VRAM, free VRAM (`total - used`), render node, PCI address, and connected displays. The default remains the card with the largest measured total VRAM. The selection confirmation repeats the selected card's measured total, used, and free MiB.

## Benchmark Workflow

`benchmark` becomes a Vulkan-only measurement. It captures stdout and stderr in separate private files, prints both complete redacted streams, and parses throughput from stdout only. It prints the exact Podman command, model, device nodes, exit status, and measured prompt/generation tokens per second. It records the Vulkan result and selects the Vulkan image only on successful parsing. If Vulkan fails, it configures CPU fallback but exits nonzero after showing the Vulkan failure and the persisted backend/image.

Generated configuration records only Vulkan benchmark results. Benchmarking does not pull, run, or configure another GPU backend.

## Check Setup

`check-setup` prints the command, inputs, raw result, parsed result, expectation, and verdict for every validation section, not only its final PASS counter:

- Tooling: each command checked and its discovered executable path or missing-command error.
- Configuration: config path, parse/validation result, and validation error JSON when invalid.
- GPU access: configured PCI address, detected render node, and readable-device test.
- Image and models: selected image, each enabled model file, GGUF validator result, and any validator diagnostic.
- Budget: available MiB, required MiB, `models_max`, and remedies when infeasible.
- Device resolution: the full `--list-devices` command and output, persisted device name, resolved transient `VulkanN` ID, and candidate/error details.

For each enabled model, `check-setup` prints the full disposable `podman run` command, including image, device, model mount, model file, GPU layers, prompt, and timeout. The prompt is the deterministic `Reply with exactly: ready` contract. It prints separate complete redacted stdout and stderr. On nonzero exit, it includes exit status and complete redacted output rather than a 1000-character truncation.

## Check Server

For each enabled model, `check-server` runs one local, deterministic `Reply with exactly: ready` request and prints:

- the executed redacted curl command and a copyable OpenAI-compatible curl template with `Authorization: Bearer <redacted>`;
- the request payload, including `Reply with exactly: ready`, `max_tokens: 256`, and `stream: false`;
- the full redacted HTTP response;
- assistant content, reasoning content when present, normalized content, and expected `ready`.

The server check never fetches Internet data. The 256-token completion budget remains required because Ornith can consume a short budget in its reasoning channel before producing final content.

## Check With Agents

For every client/model/check matrix row, print the redacted client command, exact prompt, full redacted client JSONL transcript, extracted final assistant text, parsed evidence, freshly fetched expected source snapshot, field-by-field comparison, and comparison verdict. On a client failure, also print the redacted client stderr and transcript/parser diagnostics.

### Source ordering

Fetch the authoritative weather or FX snapshot immediately before each agent row. Do not put any snapshot values into the agent prompt. The prompt directs the agent to independently retrieve current data from the authoritative source URL with a shell network command and return the required evidence fields. Weather and USD-to-CLP are deliberate live-data checks; they are not deterministic server-contract tests.

### Evidence parsing

The agent final text is valid evidence when it is either:

- one bare JSON object with only surrounding whitespace; or
- one Markdown fenced block labeled `json` whose body is one JSON object with only surrounding whitespace.

All other output fails evidence parsing. Multiple objects, prose outside the one permitted fence, arrays, scalars, and additional fenced blocks fail.

### Live-data comparison

The harness compares the parsed evidence to the snapshot fetched immediately before that row. It reports every differing field by name, expected value, and received value. A matching response proves the agent returned fresh source data rather than copying a harness-provided value.

## Testing

Mocked shell tests must assert the complete redacted diagnostic layout and prove secrets do not appear in output or retained artifacts. Add cases for fenced JSON acceptance, multiple-object rejection, stale source mismatch, agent nonzero exit, malformed transcript, and redaction of API key/bearer header.

Run live acceptance for Pi and OpenCode against an enabled model. Verify each transcript shows an actual shell fetch from the weather/FX source and that every displayed value is redacted where required.

## Acceptance Criteria

- No check operation hides its command, prompt/payload, stdout, stderr, result, parsed value, expectation, or failure reason.
- No displayed or retained diagnostic exposes the local API key or bearer header.
- `check-with-agents` reports the real Pi/OpenCode transcript and accepts one fenced JSON object.
- `check-with-agents` fetches a fresh source snapshot per row and never gives the expected values to the agent.
- Setup shows measured total, used, and free VRAM before GPU selection.
- Benchmark runs and documents Vulkan only, parses stdout separately from stderr, and configures CPU fallback when Vulkan fails while returning nonzero.
- Vulkan is the sole GPU benchmark path in project configuration, scripts, tests, and documentation.
- `check-setup` shows its device resolution, GGUF, budget, and complete offline inference diagnostics for every enabled model.
- `check-server` shows the real authenticated completion response, a copyable redacted curl template, and passes Ornith with its 256-token budget.
- Every target continues after independent failures, reports skipped dependent work with its blocker, and exits nonzero if any required check fails.
