# Agentic Gemma Replacement Design

**Date:** 2026-07-31
**Status:** Approved

## Goal

Replace the base Gemma 4 GGUF with
`yuxinlu1/gemma-4-12B-agentic-fable5-composer2.5-v2-3.5x-tau2-GGUF`
for clean setup and the active deployment. Preserve the `gemma4` alias and the
existing one-resident 131,072-token runtime contract.

## Selected Artifact

Use `gemma4-v2-Q4_K_M.gguf` from repository revision
`190a31365a6b80a692349be34ccdac730cad4fe4`:

- URL: `https://huggingface.co/yuxinlu1/gemma-4-12B-agentic-fable5-composer2.5-v2-3.5x-tau2-GGUF/resolve/190a31365a6b80a692349be34ccdac730cad4fe4/gemma4-v2-Q4_K_M.gguf`
- Size: `7,381,381,664` bytes
- LFS SHA-256: `0b9506cab36f7f818e34f9c0f5a3d6568d0b37100f3a3e1092e2eec3c4c96791`
- Architecture: Gemma 4
- Published context length: 262,144 tokens
- License: Apache-2.0

Q4_K_M is the publisher's recommended balance of size and quality. It is
smaller than the current Gemma Q4 artifact, which lowers the weight-size input
to the VRAM budget. Deployment must confirm the complete 131,072-token budget
from the downloaded GGUF metadata before restarting the service.

## Configuration

Replace the current Gemma record in `models.yml.example` and the private active
configuration. Keep these values unchanged:

- `alias: gemma4`
- `parameters: 12B`
- `quantization: Q4_K_M`
- `ctx_size: 131072`
- `client_max_output_tokens: 8192`
- `n_gpu_layers: 99`
- `runtime.models_max: 1`
- Q5_1 K/V caches, one request slot, flash attention, disabled fitting, and
  disabled context shifting

Update the label, file name, URL, and exact byte size. Preserve the user's
enabled-model selection and all unrelated private configuration.

## Compatibility

The model embeds a Gemma 4 Jinja template for reasoning and native tool calls.
The deployed llama.cpp build is newer than the model publisher's minimum
guidance and enables Jinja by default. The replacement adds no explicit Jinja
or sampler setting unless a live tool-use test proves the current defaults
incompatible.

Do not enable the optional MTP draft. The publisher reports version-sensitive
MTP loader regressions, and a second model allocation would reduce the VRAM
margin. Do not add CPU offload or silently reduce context.

## Deployment

1. Update the checked-in model record and its regression tests.
2. Save a mode-0600 rollback copy of the active config in a private temporary
   directory. Never print its API key. Keep the old GGUF in place.
3. Apply the new record to the active config, then run unattended `make setup`
   to download and validate the new GGUF while the current service remains
   available.
4. Verify the exact byte size and LFS SHA-256.
5. Stop the old service so its allocation does not affect offline VRAM
   detection, then run `make check-setup` immediately after `make setup`.
6. Confirm that GGUF validation, derived KV geometry, the 131,072-token Q5_1
   budget, and disposable Vulkan inference all pass.
7. Run `make start`. The start path loads the replacement from an unloaded VRAM
   baseline.
8. Refresh Pi and OpenCode with `make setup-local-llm-agents`.
9. Delete the rollback config and old base Gemma GGUF only after every
   acceptance check passes.

If download, validation, budgeting, startup, or inference fails, restore the
old active config and restart the base model. Keep the old GGUF until the final
acceptance check completes.

## Acceptance Criteria

- `make validate` and `make test` pass.
- Unattended `make setup` succeeds, followed by a passing `make check-setup`
  while the router service is stopped.
- The downloaded file matches the published byte size and SHA-256.
- Offline GGUF validation and the VRAM budget pass without CPU offload,
  automatic fitting, cache changes, or context reduction.
- `/v1/models` lists exactly the enabled alias `gemma4` and reports a
  131,072-token Q5_1 preset.
- `make check-server` passes.
- A prompt above the former 8,192-token limit succeeds.
- `make check-with-agents` passes for installed Pi and OpenCode clients,
  exercising the embedded tool-call template.
- Pi and OpenCode advertise context 131,072 and output 8,192 for `gemma4`.
- The service remains active with the agentic model selected.
- The old base Gemma GGUF is removed after successful validation.

## Out of Scope

- MTP speculative decoding
- Q3, Q6, or Q8 variants
- A second Gemma alias
- A 262,144-token deployment
- New sampler defaults
- Changes to Ornith
