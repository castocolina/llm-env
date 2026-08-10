# Ornith 35B MoE Incorporation — Design

**Goal:** Add Ornith 1.0 35B (a MoE model) as a selectable model alongside the existing Ornith 1.0 9B, with a generic, reusable CPU-offload mechanism for MoE expert tensors so the model fits this host's 16GB VRAM / 30GB RAM, and correct the 9B's `ctx_size` to match its real native context window.

**Architecture:** Extend the existing VRAM-budget system (`pylib/budget.py`) with a parallel RAM-budget check for MoE models that offload routed-expert tensors to CPU via llama.cpp's `--n-cpu-moe` flag. The offload amount is a per-model config value (`n_cpu_moe`), tuned empirically per model/quant rather than computed, because the right value depends on measured VRAM headroom under load, not just static tensor sizes.

**Tech stack:** No new dependencies. Uses the GGUF metadata already read by `pylib/budget.py`, and llama.cpp's built-in `--n-cpu-moe` server flag (confirmed present in `ghcr.io/ggml-org/llama.cpp:server-vulkan`, build 10326).

## Global Constraints

- `runtime.models_max` stays `1` — only one model resident at a time. This makes host-wide settings (like `resources.llm_server.memory_ceiling_pct`) safe to raise for a large model, since it never competes with another model for RAM/VRAM simultaneously.
- Never silently correct an infeasible config — every new failure mode (RAM ceiling too low, `n_cpu_moe` on a non-MoE model, VRAM budget exceeded) must fail explicitly with a specific, actionable remedy, matching the existing philosophy in `.agents/architecture.md` and `pylib/budget.py`'s `remedies` list.
- `make clean` must keep downloaded models (already true — `scripts/clean.sh` explicitly preserves `$MODELS_DIR`). Do not regress this.
- All exact values below (quant, `n_cpu_moe`, `vram_budget`, `ctx_size`, `memory_ceiling_pct`) come from real measurements taken on this host (AMD RX 9070 XT, 16304 MiB VRAM; 30GB host RAM; 24 CPU threads) during this design's research — not estimates.

## Background / Research Findings

**Model identity:** `ornith-ai/Ornith-1.0-35B-GGUF` (moved from the `deepreinforce-ai` org referenced in older docs). Qwen3.5-35B-A3B-based MoE: 35B total params, ~3B active/token, 256 routed experts (top-8) + 1 shared expert, 40 transformer blocks, hybrid attention (~75% Gated DeltaNet recurrent layers with fixed-size state, ~25% full-attention layers). Native `max_position_embeddings: 262144`, confirmed via the model's own `config.json`.

**The 9B has the same native context.** `deepreinforce-ai/Ornith-1.0-9B`'s `config.json` also reports `max_position_embeddings: 262144`. The current `models.yml.example` entry (`ctx_size: 131072`) uses only half of what the model supports — this is a plain correction, not a new capability.

**GGUF tensor sizes (measured by parsing the GGUF header directly, no external tooling):**

| Quant | Total size | Routed-expert tensors (`*.ffn_*_exps.*`) | Non-expert (attention/shared/embed) | Per-block expert size |
|---|---|---|---|---|
| Q4_K_M | 21.16 GB | 19.5 GB | 1.65 GB | ~522 MB × 40 blocks |
| Q5_K_M | 24.72 GB | 22.86 GB | 1.86 GB | ~589 MB × 40 blocks |

**Full CPU offload (`n_cpu_moe 40`, i.e. every block's experts on CPU) works but wastes VRAM.** Both quants loaded and served successfully at full native context (262144) with only ~50% VRAM used. Measured via `llama-bench` (`-p 512 -n 128 -d 0,32768`, RX 9070 XT, Vulkan):

| Config | VRAM used | pp @ depth 0 | pp @ depth 32768 | tg @ depth 0 | tg @ depth 32768 |
|---|---|---|---|---|---|
| 9B, full GPU (`ngl 99`, ctx 262144, KV q5_1) | 8602 MiB (52.8%) | 3219.3 | 2294.2 | 88.8 | 76.8 |
| 35B Q4_K_M, `n_cpu_moe 40` (full CPU) | — | 336.8 | 310.8 | 34.3 | 32.9 |
| 35B Q4_K_M, **`n_cpu_moe 28`** | 13268 MiB (81.4%) | 441.3 | 388.2 | 39.4 | 39.5 |
| 35B Q4_K_M, `n_cpu_moe 24` | 15053 MiB (92.3%) | 489.7 | 425.1 | 42.1 | 42.8 |
| 35B Q5_K_M, `n_cpu_moe 40` (full CPU) | — | 273.8–295.6 | 255.2 | 30.2–31.7 | 29.7 |
| 35B Q5_K_M, **`n_cpu_moe 28`** | 14288 MiB (87.6%) | 401.9 | 356.1 | 37.5 | 37.1 |

`n_cpu_moe 24` gives modest extra throughput (+11% pp / +7% tg over `n_cpu_moe 28`) for a much tighter VRAM margin (92% vs 81-88%) — not worth the OOM risk from compositor/desktop VRAM spikes. **`n_cpu_moe 28` is the recommended value for both quants**: keeps 12 of 40 blocks' experts on GPU, ~25-39% throughput improvement over full CPU offload, with safe headroom.

**Utilization during inference** (`n_cpu_moe 28`, full 262144 ctx, sustained generation): CPU ~43-44% average (of 24 threads), GPU busy ~56-58% average, VRAM stable at the levels above. Confirms genuine hybrid CPU/GPU execution, not a GPU-idle CPU bottleneck.

**Reasoning quality:** on a moderate bug-finding task (classic binary-search off-by-one), both 35B quants gave correct, precise diagnoses at full 262144 context; the 9B needed a much larger `max_tokens` budget than expected to complete its reasoning before answering (Ornith emits `<think>` blocks that consume completion-token budget before final content — 1117 tokens used against an 8000 budget). On a deliberately harder/more ambiguous multi-bug task, all three models exhausted a 16000-token budget without reaching a final answer — this was inconclusive due to prompt ambiguity, not a reliable quality signal, and is not used to justify any config choice here.

**`make clean` already keeps models** (`scripts/clean.sh` line: `Downloaded models in ${MODELS_DIR} are KEPT.`) — no fix needed there. What's missing is an opt-in destructive path for when a user actually wants to reclaim the disk space multi-GB model downloads consume.

## Components

### 1. `models.yml` schema: new field `n_cpu_moe`

Optional integer field on a model entry. When present, `pylib/presets.py` emits `--n-cpu-moe <n>` in that model's `presets.ini` section. Absent (the common case for dense models) emits nothing, preserving current behavior exactly.

### 2. `pylib/budget.py`: RAM-side cost for MoE-offloaded models

For a model with `n_cpu_moe` set, compute two weight figures instead of one:
- `ram_weights_mib`: sum of GGUF tensor sizes matching `blk.{0..n_cpu_moe-1}.ffn_*_exps.*` (or whichever `n_cpu_moe` layers llama.cpp actually offloads — verify offload direction, first-N vs last-N, against llama.cpp source/docs before implementing, since this determines which blocks' tensors count toward RAM vs VRAM).
- `vram_weights_mib`: total model size minus `ram_weights_mib` (everything else: non-expert tensors + the GPU-resident experts).

`vram_weights_mib` feeds the existing VRAM budget path unchanged. `ram_weights_mib` is a new figure threaded to the resource check below.

### 3. `pylib/resources.py`: new RAM feasibility check

After computing `llm_server.memory_mib` (existing logic), compare it against the resident model's `ram_weights_mib` (from budget.py). If `ram_weights_mib` exceeds it, raise `ResourceError` with a remedy: the minimum `memory_ceiling_pct` that would fit, computed from the actual numbers (not a guessed constant) — same style as `budget.py`'s existing remedies list.

### 4. `pylib/presets.py`: emit the flag

Add `n-cpu-moe` to the per-model section when `model.get("n_cpu_moe")` is set, alongside the existing `n-gpu-layers`.

### 5. `scripts/prune.sh` + `make prune` (new)

Calls `scripts/clean.sh`'s logic (or invokes it directly), then additionally removes everything under `$MODELS_DIR`. Same confirmation pattern as `clean.sh` (`LLM_ENV_ASSUME_YES` / interactive prompt), with its own explicit listing of what gets deleted (the model files, with total reclaimed size) so it reads as a distinct, more destructive action from `clean`.

### 6. `models.yml.example`: final values

As specified in the Global Constraints section above — `ornith` gets `ctx_size: 262144` (corrected) and `vram_budget: 55%`; new `ornith-35b` entry with `quantization: Q4_K_M`, `n_cpu_moe: 28`, `ctx_size: 262144`, `vram_budget: 85%`, `enabled: false` (opt-in); `resources.llm_server.memory_ceiling_pct` raised to `60%`.

## Testing

- Unit tests for `pylib/budget.py`'s new RAM/VRAM split (given fixed GGUF-metadata-shaped fixtures, not real GGUF files — following existing test patterns in `tests/test_budget.py`).
- Unit tests for `pylib/resources.py`'s new RAM feasibility check and its error remedy.
- Unit test for `pylib/presets.py` emitting `--n-cpu-moe` only when configured.
- `scripts/prune.sh` gets the same test treatment as `scripts/clean.sh` in `tests/test_shell.py` (confirmation gate, `LLM_ENV_ASSUME_YES`, correct deletion scope).
- Manual validation (documented in README, not automated): `make setup` with `ornith-35b` enabled, `make start`, `make check-server`, and a `make benchmark`-style throughput spot-check comparing against the numbers recorded in this design doc — catches regressions in the actual container image/flags, which unit tests can't.

## Out of scope

- Automatic tuning of `n_cpu_moe` (e.g., probing VRAM at setup time and computing the ideal value). The measured sweet spot is host-specific enough (depends on desktop compositor VRAM usage, exact GPU) that a fixed, documented, empirically-good default is more honest than a fragile auto-tuner. Revisit if this becomes a recurring need for other MoE models.
- Concurrent/multi-model residency changes — `models_max` stays 1, unaffected by this work.
- Any change to how `low latency` reasoning-token budgets are chosen; the `<think>`-block token consumption observed during research is a documentation note for future prompt/budget tuning, not a code change here.
