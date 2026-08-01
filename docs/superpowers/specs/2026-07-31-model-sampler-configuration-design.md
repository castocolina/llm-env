# Model-Specific Sampler Configuration Design

## Context

The Agentic Gemma 4 Q4 replacement passes GGUF validation, VRAM budgeting,
startup, API, long-context, and client metadata checks. It does not reliably
pass the existing live agent matrix. Across two runs, the model changed the
required network command, retried failed commands, failed to converge, changed
timestamp formats, rounded source values, and returned the wrong source URL.

The deployed llama.cpp build enables Jinja by default, and both Pi and OpenCode
receive parsed, structured tool calls. The failure is therefore not a missing
Jinja flag or client parser error. The
[publisher's model card](https://huggingface.co/yuxinlu1/gemma-4-12B-agentic-fable5-composer2.5-v2-3.5x-tau2-GGUF)
recommends `temperature: 1.0`, `top_p: 0.95`, and `top_k: 64`, identifies
`repeat_penalty: 1.1` as the fix for repetition, and also permits greedy
`temperature: 0.0` for coding. llm-env cannot currently express these
model-specific sampler settings.

## Goals

- Configure sampler parameters per model in `models.yml.example` and private
  `models.yml` files.
- Render configured sampler parameters into only that model's llama.cpp preset.
- Preserve llama.cpp defaults for models without sampler configuration.
- Test the publisher's complete recommended profile and its greedy alternative,
  with one effective sampler-field change between candidates.
- Accept a sampler profile only after three consecutive valid qualification
  rounds in which the four required agentic-model cells pass.
- Keep the existing Pi and OpenCode profiles and live checks unchanged.

## Non-Goals

- Global sampler defaults
- Client-specific sampler configuration
- Changes to `scripts/check-with-agents.sh` or its evidence requirements
- MTP speculative decoding
- CPU offload, automatic fitting, cache changes, or context reduction
- Sampler changes for Ornith or base Gemma

## Preflight Evidence

Tests against the deployed llama.cpp build established the sampler precedence
that the experiment relies on:

- An agentic-model chat request with no request-level sampler fields used
  `temperature: 1.0`, `top_p: 0.95`, `top_k: 64`, and
  `repeat_penalty: 1.0`, as observed from the active slot.
- Model-preset sampler values became the active-slot values when request fields
  were absent.
- Explicit request-level sampler fields overrode model-preset values.

Secret-safe request projections also established that Pi `0.82.1` omitted all
four fields from two requests in a tool loop and OpenCode `1.18.10` omitted them
from three requests in a tool loop. The projections retained only allowlisted
request metadata and sampler fields; they did not retain authorization values
or complete prompts. These facts make the model preset effective for the tested
client versions, but qualification must repeat the preflight after either
client version changes.

## Configuration Interface

Each model may contain an optional `sampling` mapping:

```yaml
sampling:
  temperature: 0.0
  top_p: 0.95
  top_k: 64
  repeat_penalty: 1.1
```

All four fields are optional. An absent `sampling` mapping and an empty
`sampling: {}` mapping are both valid, emit no sampler directives, and preserve
llama.cpp defaults.

`models.yml.example` documents every supported field. The agentic Gemma record
receives only the sampler values that pass the live qualification gate. Fresh
setups therefore inherit the measured model-specific profile rather than a
global policy.

## Validation

`pylib/config.py` validates the optional mapping before preset generation:

- `sampling` must be a mapping.
- Unknown keys are rejected.
- `temperature` must be a finite, non-negative number.
- `top_p` must be a finite number in the inclusive range `0` through `1`.
- `top_k` must be a non-negative integer and not a Boolean.
- `repeat_penalty` must be a finite number greater than `0`.

Integers are accepted for numeric fields where YAML may naturally represent a
whole number. Boolean values are rejected even though Python treats them as
integers.

No migration adds sampler values to existing model records. Existing private
configuration remains valid because `sampling` is optional.

## Preset Rendering

`pylib/presets.py` maps configured YAML fields to llama.cpp preset keys:

| YAML field | llama.cpp preset key |
|---|---|
| `temperature` | `temp` |
| `top_p` | `top-p` |
| `top_k` | `top-k` |
| `repeat_penalty` | `repeat-penalty` |

The keys are emitted in the selected model's section, never in the global `[*]`
section. Disabled models receive no section, as they do today. Rendering keeps
the existing no-`[DEFAULT]`, no-`version`, one-slot, no-fit, and no-context-shift
invariants.

## Staged Experiment

The base model remains the rollback target. The valid agentic GGUF and private
rollback material remain in place throughout the experiment.

Screen these candidates in order:

1. Publisher profile: `temperature: 1.0`, `top_p: 0.95`, `top_k: 64`, and
   `repeat_penalty: 1.1`.
2. Greedy alternative: `temperature: 0.0`, `top_p: 0.95`, `top_k: 64`, and
   `repeat_penalty: 1.1`.

The measured no-field baseline already uses the publisher's first three
values. Candidate 1 therefore changes only the effective repeat penalty while
explicitly pinning the complete published profile. Candidate 2 changes only
temperature relative to candidate 1 and evaluates the publisher's greedy
coding alternative. Intermediate candidates that merely add `1.0`, `0.95`, or
`64` to the preset are excluded because active-slot probes show that they would
not change sampling behavior. `temperature: 0.4` is excluded because neither
the publisher guidance nor the observed failures provide a hypothesis for that
value.

Run both screening candidates so each supported publisher profile receives one
valid result even if candidate 1 passes screening.

Before the first screening run, record `pi --version`, `opencode --version`, and
the SHA-256 of `scripts/check-with-agents.sh`. Use the harness's exact isolated
Pi and OpenCode provider definitions, model settings, environment isolation,
CLI options, and one current check prompt for each client, changing only the
provider base URL to a projection proxy. Each client must make at least one tool
call and a continuation request. Assert that every projected chat request omits
`temperature`, `top_p`, `top_k`, and `repeat_penalty`.

Bind and address the proxy only through `127.0.0.1`. Disable raw request and
access logging, process authorization and complete message content only in
memory, and write only an allowlisted assertion summary containing the client
identity, request ordinal, model, message roles, tool-call presence, and the
four sampler fields. Use `prepare_diagnostic_dir sampler-probe`, mode `0600`
files, and an `EXIT` trap that stops the proxy and calls
`finish_diagnostic_dir`; this provides mode `0700` storage and cleanup on every
exit path. If retained evidence is requested, retain only the assertion
summary, never proxy stderr or raw traffic. Complete this cleanup before
screening.

If either client version or the recorded harness SHA-256 differs before a later
run, that run is infrastructure-invalid until the same preflight is repeated
with the new version and harness configuration.

For each screening or qualification run:

1. Stop the active service.
2. Update only the agentic model's private `sampling` mapping.
3. Start through `make start`, which regenerates `presets.ini` from config.
4. Assert that `/v1/models` reports the expected model path, context, caches,
   and sampler arguments.
5. Send a direct chat request that omits all four sampler fields and inspect the
   active slot to assert the candidate's effective `temperature`, `top_p`,
   `top_k`, and `repeat_penalty` values.
6. Assert that the installed Pi and OpenCode versions still match the versions
   whose request projections omitted all four sampler fields.
7. Run the unchanged `make check-with-agents` target with retained, redacted
   diagnostics.

An infrastructure-invalid screening run is repeated with the same candidate
after the infrastructure is restored. A model-behavior failure rejects the
candidate. After every candidate has one valid screening result, qualify the
screening candidates whose four required cells passed, in the order above.
Qualification starts a new streak because the intervening screening candidates
changed the active sampler configuration. Select the first candidate in this
fixed order that completes qualification, and stop; a later candidate cannot
replace an earlier qualifying candidate. If a candidate fails qualification,
continue with the next candidate that passed screening.

## Qualification Matrix

Qualification uses cell identities, not the aggregate pass total printed by
`scripts/check-with-agents.sh`. Each valid round must contain exactly one `PASS` for
each of these identities:

| Client | Model alias | Check |
|---|---|---|
| Pi | `gemma4` | `weather` |
| Pi | `gemma4` | `fx` |
| OpenCode | `gemma4` | `weather` |
| OpenCode | `gemma4` | `fx` |

Both clients must be installed and invoked. A `SKIP` for either client, a
missing required cell, or a duplicate required cell invalidates the round and
does not count as a pass or a candidate failure. Before each round,
`/v1/models` must associate `gemma4` with the expected agentic GGUF path and
sampler arguments, the direct no-field active-slot probe must report the exact
candidate values, and the installed Pi and OpenCode versions must match the
successful request-projection preflight.

The unchanged script also tests every additional alias returned by
`/v1/models`. Those rows remain visible and retain their normal effect on the
script's exit status, but they do not count toward the four-cell sampler
qualification and cannot reject or reset the `gemma4` candidate. A failure for
an additional alias still blocks final acceptance, which requires one complete
passing invocation of the unchanged target across all reported aliases.

A candidate qualifies after three consecutive valid rounds with all four
required cells passing, yielding 12 required passing observations. The sampler
configuration must remain unchanged across those rounds. An invalid
infrastructure run neither advances nor resets the streak; it is repaired and
rerun. A valid round with a model-behavior failure rejects the candidate
immediately rather than retrying it.

## Failure Handling

A required cell is a model-behavior failure only when the model was invoked and
the retained diagnostics attribute the result to its behavior. This includes
changing the required network command, making extra network requests, returning
invalid final JSON, altering source values, failing to converge, reporting an
incorrect source URL or timestamp, or causing the agent run to exit
unsuccessfully through its generated actions. Validation remains unchanged;
the experiment must improve model behavior rather than weaken the check.

Source-fetch or source-parser failures before model invocation, an unavailable
required client, local API connectivity or credential failures, client launch
or crashes before a usable model response, transcript-capture failures, and
harness parser failures are infrastructure failures. They invalidate the run
without rejecting the candidate. If the retained evidence cannot distinguish
model behavior from infrastructure, classify the run as invalid rather than
attribute it to the candidate. Repair the infrastructure and rerun the same
candidate without changing its sampler mapping.

Configuration validation, preset rendering, startup, or API regressions that
are reproducibly caused by a candidate reject that candidate. Unrelated
service or client infrastructure failures pause the experiment and invalidate
the run. After a model-behavior candidate failure, stop the service before
changing to the next sampler mapping. If all candidates fail screening or
qualification, or no candidate completes the final acceptance gates:

1. Stop the service.
2. Restore the private base-model config from the validated rollback copy.
3. Start the base service.
4. Run `make check-server`.
5. Keep both GGUF files and rollback material.

## Repository Tests

Implementation follows test-driven development:

- Config tests reject malformed mappings, unknown keys, non-finite numbers,
  out-of-range values, Booleans, and invalid integers. Every rejection must
  exit nonzero before service startup and identify the affected model alias,
  the `sampling` mapping or field, and the violated constraint.
- Config tests accept each valid optional sampler field, configurations with no
  `sampling` mapping, and an empty `sampling: {}` mapping.
- Preset tests assert exact YAML-to-INI key mapping and model-level placement.
- Preset tests assert that models without `sampling` and models with
  `sampling: {}` emit no sampler keys.
- Existing shell, documentation, and full repository tests remain unchanged
  except where the sample configuration's expected structure must include the
  optional interface.

After code changes, run `make validate && make test` as required for Python
edits. The live experiment begins only after repository validation passes.

## Acceptance Criteria

- `models.yml.example` documents all four optional sampler fields.
- Invalid sampler configuration exits nonzero before service startup and names
  the affected model alias, the `sampling` mapping or field, and the violated
  constraint; repository tests assert this diagnostic contract.
- Presets render sampler keys only for models that configure them.
- Models without sampler settings preserve current behavior.
- `make validate` and `make test` pass.
- A secret-safe request-projection preflight uses the harness's isolated client
  configurations and CLI options, proves that every request in a tool call and
  continuation sequence omits all four sampler fields, and records the client
  versions and harness SHA-256 that remain unchanged throughout screening and
  qualification.
- A direct request with no sampler fields produces the exact candidate values
  in the active slot before every screening and qualification run.
- One agentic sampler candidate passes all four required cell identities in
  three consecutive valid qualification rounds with an unchanged configuration.
- The selected profile is the first candidate in the fixed order that completes
  qualification.
- The chosen values become the agentic Gemma defaults in `models.yml.example`.
- `make check-server`, the prompt-above-8K check, client metadata checks, and the
  final repository gates pass with the selected profile.
- Pi and OpenCode are both installed for qualification, neither is skipped, and
  one final unchanged `make check-with-agents` invocation passes every cell for
  every alias reported by `/v1/models`.
- The old base GGUF and rollback material are deleted only after the selected
  profile passes all preceding acceptance criteria, `git diff --check` passes,
  and the service remains active. In particular, cleanup requires the expected
  model path, context, caches, and sampler arguments from `/v1/models`; a
  passing `make check-server`; a successful prompt above 8,192 tokens; correct
  Pi and OpenCode metadata; three valid four-cell qualification rounds; a final
  complete `make check-with-agents` pass; and passing `make validate` and
  `make test` results.
