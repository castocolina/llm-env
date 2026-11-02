# Fitting a candidate

Loaded at onboarding step 4 (`SKILL.md` Route). `llmenv budget` gives the verdict; this is the arithmetic behind it.

## 1. Fit math

- Budget: `SKILL.md` rule 1.
- `weights + KV cache + ~1 GiB compute buffers <= budget`. KV bytes per token for full-attention layers:
  `2 * attn_layers * n_kv_heads * head_dim * bytes_per_elem`, with q5_1 = 0.75, q8_0 = 1.0625, f16 = 2 bytes per element.
  Read the numbers from the base model's `config.json` (`resolve/main/config.json`).
- **Hybrid-attention models** (Gated DeltaNet / linear layers, `layer_types` or `full_attention_interval` in config)
  only keep KV for the full-attention layers (about 1 in 4), so 256K context is cheap. A dense full-attention 27B at
  256K with q5_1 KV costs several GiB more than its file size suggests. Always compute it for the candidate.
- After the GGUF is on disk, `uv run llmenv.py --config <cfg> budget --models-dir <models_dir>` (`$LLM_ENV_MODELS_DIR`, default `~/llm-workspace/models`) is the authoritative VRAM and
  RAM verdict (it errors until every enabled model file exists). `hf-scout.sh` fit classes are screening only.
- RAM is the usual binding constraint: the desktop session here often uses 15+ GiB, so check `ram.available_mib` and
  `budget.ram_offload_budget_mib` before promising a MoE offload. That budget is the llm-server memory cap from `llmenv budget`
  (set by `resources.llm_server.memory_ceiling_pct`), clamped to MemAvailable, and 0 while llm-server is disabled.
- The host runs `parallel=1`, `models_max=1`, `context-shift=off`: the full `ctx_size` is allocated at load and a prompt
  longer than it fails instead of sliding. Size `ctx_size` to what the agent will really use (128K floor, 256K target).

## 2. MoE offload

- Active parameters do not reduce memory: every expert must sit in VRAM or RAM. A "35B-A3B" needs the full 35B file.
- `n_cpu_moe N` moves the routed-expert tensors of blocks 0..N-1 (ascending) to RAM. Per-block expert size is in the
  GGUF (`llmenv` parses it; see `pylib/gguf.py::moe_expert_offload_mib`). Solve for N:
  `VRAM = non_expert + (blocks - N) * per_block_expert + KV + 1 GiB <= budget`, `RAM = N * per_block_expert`.
- Measured on this host (Ornith 1.0 35B Q4_K_M, RX 9070 XT, Vulkan, q5_1 KV, 256K ctx): N=40 → tg 34 t/s,
  N=28 → 13.2 GB VRAM and tg 39, N=24 → 15.0 GB VRAM and tg 42. Throughput gains per block are small; the
  decision is dominated by whether VRAM and RAM both fit. Prefer the N that leaves >= 8% VRAM free for compositor spikes.
- Before download, `hf-scout.sh`'s `offload` class is the only estimate; once the GGUF is on disk, the solve above replaces it.
- Dense models offloaded layer by layer fall to CPU-bound speeds. Treat a dense model that does not fit in VRAM as "no".

## 3. Quant floor

- Tool calling and long agent loops degrade first under heavy quantisation. Floor: about 3.5 bits per weight
  (IQ3_S / Q3_K_M); default to Q4_K_M or UD-Q4_K_XL / IQ4_XS; use Q5/Q6 when VRAM allows.
- A repository with only sub-3-bit quants is "vram-lowbit" in `hf-scout.sh`: report it, do not recommend it without an
  own benchmark showing the verifier pass rate holds against the Q4 of the same model.
