# Runtime, image and flag review

Loaded when the user asks whether another runtime, a newer image or a flag change would help, or when a model fails to load on
the current build; not for a plain model swap.

## Last seen on this host (2026-10-30; re-check)

| Image | Build | Notes |
|---|---|---|
| `ghcr.io/ggml-org/llama.cpp:server-vulkan` (production, `gpu.image`) | 10454 | RADV on the RX 9070 XT. Measured, Ornith 9B Q4_K_M, q5_1 KV, ub 512: pp 2396 / tg 88 t/s at depth 0, pp 2109 / tg 83 at 16K |
| `ghcr.io/ggml-org/llama.cpp:server-rocm` (local, unused) | 10143 | See gotchas: not usable as-is |

A decision quotes only the `--version` output read in this run.

Tags float. Find the build with `podman run --rm --entrypoint /app/llama-server <image> --version`; the latest upstream
build is `gh api "repos/ggml-org/llama.cpp/releases?per_page=1" --jq ".[0].tag_name"` (the `/releases/latest` endpoint
returns an unrelated `v0.x` tag, not the `bNNNN` build). On 2026-10-30 upstream was b12017, about 1,560 builds ahead of the
local Vulkan image. A new model architecture (new Qwen / Gemma
generation, MTP, DFlash) often needs a newer build than the one pulled: a load error such as "unknown model architecture"
means update the image first, not that the GGUF is broken.

## Deciding Vulkan versus ROCm

Community reports for RDNA4 favour Vulkan for token generation and are mixed for prompt processing, and results move with
llama.cpp and Mesa versions. Do not decide from reports: A/B both images with the same flags and keep
Vulkan unless ROCm wins tg by a margin larger than run-to-run noise (about 3 %) at the context you will really use.
Prompt processing at depth matters most for agents (every turn re-reads context), so compare at 32K and 128K depth.
`bench-fit.sh` runs only the Vulkan image (`VULKAN_IMAGE`), so the ROCm side is `llama bench` run by hand on the ROCm image
with the `flags` of the Vulkan run's JSON, after freeing the GPU (G2, `SKILL.md`), one run at a time.

## Updating the image

Only when the owner asks; otherwise report findings and never change the image unprompted.

1. `podman pull <image>` is the only step that changes the host. Record the old digest first so you can roll back
   (`podman image inspect --format '{{.Id}}' <image>` before and after; re-tag the old id to roll back). Before pulling, take
   the reference pp/tg from the current model's capacity card (`uv run llmenv.py capacity show --alias <a>`), else from a
   `bench-fit.sh` run on the old image after G2 with the same `--depths`.
2. Re-run `bench-fit.sh` on the current model with the new image, after freeing the GPU (G2, `SKILL.md`); keep it only if pp/tg do not regress and
   the model loads; otherwise re-tag the old id. Then `make start-llama` if llm-server served before.
3. Apply with the project's flow, never by hand: edit `gpu.image` only if the tag changes, `bash setup/render-unit.sh`,
   then `make restart` while no agent session is using OmniRoute (it interrupts requests). Never restart the omniroute
   container directly.

## Flags worth an A/B (one at a time)

`bench-fit.sh` takes every flag from the config's rendered presets, so an A/B runs as `tune-sweep.sh` axes (`--ubatch`, `--kv`,
`--moe`, `--threads`; one scratch config per cell, `tune-sweep.sh --help`) after freeing the GPU (G2, `SKILL.md`). A value worth
keeping is applied only through the tune route of the `SKILL.md` route table, which backs up and edits the live file.

- `-ub` (ubatch) 512 → 1024 / 2048 (the first A/B when the owner names no flag): raises prompt processing, costs compute-buffer VRAM; check peak VRAM.
- KV cache type q5_1 → q8_0: better recall at long context, +40 % KV memory; keep q5_1 when VRAM is the limit; one global key for every model.
- `n_cpu_moe` for MoE models: sweep it with `tune-sweep.sh --moe`.
- Thread count for offloaded experts: physical cores, not threads; offload throughput is memory-bandwidth bound, so more
  threads past the knee only heat the CPU (reported only: models.yml has no key for it).
- `--no-mmap` / `--mlock` keep offloaded experts resident in RAM; only worth it when swap shows up in `preflight.sh`
  (`ram.swap_used_mib` is already several GiB on this host): no models.yml key exists, so report the finding only.

## Other runtimes

Not evaluated unless llama.cpp cannot load the model (vLLM needs full-VRAM safetensors, which removes the MoE-offload option;
CPU+GPU offload projects historically target CUDA first). Say "not evaluated" rather than guess.

## Gotchas

- The local ROCm image enumerates the Ryzen iGPU as device 1. An unpinned `llama bench` aborted with "Memory critical
  error ... Memory in use". `HIP_VISIBLE_DEVICES=0` still produced no output, and a run with an empty
  `ROCR_VISIBLE_DEVICES` hung until the timeout and left a container behind. `uv run llmenv.py rocr-index --render-node <node>`
  prints the right index. Always wrap ROCm trials in `timeout` and remove the container in a trap.
- `llama bench` needs `--entrypoint /app/llama`; the device list comes from `/app/llama-server --list-devices`
  (`bench-fit.sh` does both). `-d` (depth) is how you measure long-context decode: depth 0 flatters every model.
