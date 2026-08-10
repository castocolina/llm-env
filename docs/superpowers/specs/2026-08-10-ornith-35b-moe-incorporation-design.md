# Ornith 35B MoE Incorporation — Design

**Goal:** Add Ornith 1.0 35B (a MoE model) as a selectable model alongside the existing Ornith 1.0 9B, with a generic, reusable CPU-offload mechanism for MoE expert tensors so the model fits this host's 16GB VRAM / 30GB RAM, and correct the 9B's `ctx_size` to match its real native context window.

**Architecture:** Extend the existing VRAM-budget system with a parallel RAM-budget check for MoE models that offload routed-expert tensors to CPU via llama.cpp's `--n-cpu-moe` flag. GGUF tensor parsing lives in `pylib/gguf.py`; the weight-cost split and the RAM feasibility cross-check live in `llmenv.py`, reported through `compute_budget()`'s existing `feasible`/`remedies` shape rather than a raised exception (see Component 2 for why). The offload amount is a per-model config value (`n_cpu_moe`), tuned empirically per model/quant rather than computed, because the right value depends on measured VRAM headroom under load, not just static tensor sizes.

**Tech stack:** No new dependencies. Uses the GGUF metadata already read by `pylib/budget.py`, and llama.cpp's built-in `--n-cpu-moe` server flag (confirmed present in `ghcr.io/ggml-org/llama.cpp:server-vulkan`, build 10326).

## Global Constraints

- `runtime.models_max` stays `1` — only one model resident at a time. This makes host-wide settings (like `resources.llm_server.memory_ceiling_pct`) safe to raise for a large model, since it never competes with another model for RAM/VRAM simultaneously.
- Never silently correct an infeasible config — every new failure mode (RAM ceiling too low, `n_cpu_moe` on a non-MoE model, VRAM budget exceeded) must fail explicitly with a specific, actionable remedy, matching the existing philosophy in `.agents/architecture.md` and `pylib/budget.py`'s `remedies` list.
- `make clean` must keep downloaded models (already true — `scripts/clean.sh` explicitly preserves `$MODELS_DIR`). Do not regress this.
- All exact values below (quant, `n_cpu_moe`, `vram_budget`, `ctx_size`, `memory_ceiling_pct`) come from real measurements taken on this host (AMD RX 9070 XT, 16304 MiB VRAM; 30GB host RAM; 24 CPU threads) during this design's research — not estimates.
- `resources.llm_server` must cap CPU cores the same way it already caps RAM: never hand `llm-server` every host thread. A `cpu_ceiling_pct` (default 60%) applies to every model, not just MoE ones — it's a general resource-hygiene gap this research surfaced, not something scoped to Ornith 35B specifically.

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

### 2. `pylib/gguf.py`: tensor-level parsing + `pylib/budget.py`-equivalent RAM-side cost for MoE-offloaded models

**Confirmed by implementation, revising this component from the original design:** the split does not live in `pylib/budget.py`, and RAM infeasibility is not raised from `pylib/resources.py`. Both landed in `pylib/gguf.py` (parsing) and `llmenv.py` (the split + the cross-check), reported through `feasible`/`remedies` — the same shape `compute_budget()` already uses for VRAM, not a raised exception. Rationale for the deviation: `compute_budget()` never raises for an infeasible VRAM budget — it returns `feasible: false` plus an actionable `remedies` list, and `cmd_budget` is the single place that already turns that into a CLI exit code and a JSON payload `make setup`/`make start` parse. Introducing a *second*, exception-based failure mode for the RAM side of the exact same command would mean two different error-handling shapes for what is, from the caller's perspective, one feasibility check — worse ergonomics for no real benefit, since both `make setup` and `make start` already have to parse `cmd_budget`'s JSON output either way.

Concretely:
- `pylib/gguf.py::tensor_sizes(path) -> dict[str, int]` parses the GGUF tensor-info section directly (byte-offset deltas) to get each tensor's exact size, and `moe_expert_offload_mib(path, metadata, n_cpu_moe)` sums the routed-expert tensors (`blk.{0..n_cpu_moe-1}.ffn_{gate,up,down}_exps.*`) for a model's first `n_cpu_moe` transformer blocks — confirmed via `-v` llama-server logs that `--n-cpu-moe N` offloads ascending block indices (`blk.0..N-1`), not descending.
- `llmenv.py::_model_costs()` calls `moe_expert_offload_mib()` for any model with `n_cpu_moe` set, and reports `ram_weights_mib` (the offloaded portion) separately from `weights_mib` (everything that stays resident in VRAM: non-expert tensors + the GPU-resident experts) for that model.
- `llmenv.py::cmd_budget()` sums `ram_weights_mib` across the worst-case set of concurrently-resident models (ranked by RAM cost, not VRAM cost — a model cheap in VRAM but expensive in offloaded RAM can lose `compute_budget()`'s VRAM ranking and still be selectable) against `resources.llm_server.memory_mib` (from `compute_resource_limits()`, see Component 4), and reports `ram_feasible`/`ram_shortfall_mib`/`ram_available_mib`/`ram_required_mib` alongside the existing `vram_feasible`/`shortfall_mib`/`available_mib`/`required_mib`, plus a `remedies` entry computing the minimum `memory_ceiling_pct` that would fit (or an explicit "no percentage can ever fit" remedy when even the host's absolute maximum can't cover it) — same style as `compute_budget()`'s existing remedies.
- `pylib/resources.py` itself is unchanged in shape (see Component 4 for its one real change, the CPU ceiling) — it still only ever raises `ResourceError` for the pre-existing "host can't reserve the fixed floors at all" case, never for a configurable-ceiling RAM shortfall.

`vram_weights_mib` (via `weights_mib`) feeds the existing VRAM budget path unchanged.

### 3. `pylib/presets.py`: emit the flag

Add `n-cpu-moe` to the per-model section when `model.get("n_cpu_moe")` is set, alongside the existing `n-gpu-layers`.

### 4. `pylib/resources.py`: CPU core ceiling (new — general, not MoE-specific)

Today `compute_resource_limits()` gives `llm-server` every host core minus a fixed floor (`HOST_CPU_FLOOR + OMNIROUTE_CPU_FIXED = 3`) — on this 24-thread host that's 21 cores, effectively "all of it." This was never a problem for dense models fully on GPU (llama.cpp barely touches CPU), but MoE CPU-offload changes that: the research above measured ~43-44% sustained CPU utilization across all 24 threads during `n_cpu_moe 28` inference. Uncapped, a future heavier-offload config could starve the host desktop.

Add a `resources.llm_server.cpu_ceiling_pct` config value (default **60**), mirroring the existing `memory_ceiling_pct`/`memory_ceiling_floor_pct` pattern: `cpus = min(host_cpu_count - cpu_floor, max(1, round(host_cpu_count * cpu_ceiling_floor_pct / 100), round(host_cpu_count * cpu_ceiling_pct / 100)))`. On this 24-core host that caps `llm-server` at 14 cores instead of 21, regardless of model. The `max(1, ...)` floor exists because CPU core counts are small whole numbers, unlike the memory ceiling's MiB values — on a small host (e.g. 4 usable cores) a low `cpu_ceiling_pct`/`cpu_ceiling_floor_pct` can independently round to 0, which `pylib/compose.py` would then treat as "no `cpus:` limit at all" (fully uncapped) rather than "cap at 0 cores," silently defeating the ceiling on exactly the host where over-committing CPU hurts most. This is a general resource-hygiene fix (applies to every model, not just MoE ones) surfaced by this research, not a new per-model field — no `models.yml` schema change, only `resources.llm_server`.

### 5. `scripts/prune.sh` + `make prune` (new)

Calls `scripts/clean.sh`'s logic (or invokes it directly), then additionally removes everything under `$MODELS_DIR`. Same confirmation pattern as `clean.sh` (`LLM_ENV_ASSUME_YES` / interactive prompt), with its own explicit listing of what gets deleted (the model files, with total reclaimed size) so it reads as a distinct, more destructive action from `clean`.

### 6. `models.yml.example`: final values

As specified in the Global Constraints section above — `ornith` gets `ctx_size: 262144` (corrected) and `vram_budget: 55%`; new `ornith-35b` entry with `quantization: Q4_K_M`, `n_cpu_moe: 28`, `ctx_size: 262144`, `vram_budget: 85%`, `enabled: false` (opt-in); `resources.llm_server.memory_ceiling_pct` raised to `60%`; new `resources.llm_server.cpu_ceiling_pct: 60`. A config that predates the `ornith-35b` entry (created by `make setup` before this change) does not get it from `migrate_config()` alone (that function only backfills missing top-level keys, never new model list entries) — `setup/setup.sh`'s model-selection step backfills it explicitly, the same way it already removes the legacy `openhermes` alias from pre-existing configs, without touching any of that config's other, possibly hand-customized, model entries.

## Testing

- Unit tests for `pylib/gguf.py`'s new tensor-size parsing and RAM/VRAM split (given fixed GGUF-metadata-shaped fixtures, not real GGUF files — following existing test patterns in `tests/test_gguf.py`), and for `llmenv.py`'s RAM feasibility cross-check and its remedy (`tests/test_cli.py`, alongside the existing `cmd_budget` tests).
- Unit tests for `pylib/resources.py`'s new `cpu_ceiling_pct` behavior: caps cores below the current floor-based value on a many-core host, never rounds down to 0 cores on a small host, never exceeds `host_cpu_count - cpu_floor`, and still raises `ResourceError` if the host doesn't have enough cores for the fixed floors (existing behavior preserved).
- Unit test for `pylib/presets.py` emitting `--n-cpu-moe` only when configured.
- `scripts/prune.sh` gets the same test treatment as `scripts/clean.sh` in `tests/test_shell.py` (confirmation gate, `LLM_ENV_ASSUME_YES`, correct deletion scope), plus a path-safety test that a target other than the true models directory is rejected before any deletion.
- A `setup/setup.sh` test proving the `ornith-35b` backfill (Component 6) adds the alias to a pre-existing config without touching that config's other model entries.
- Manual validation (documented in README, not automated): `make setup` with `ornith-35b` enabled, `make start`, `make check-server`, and a `make benchmark`-style throughput spot-check comparing against the numbers recorded in this design doc — catches regressions in the actual container image/flags, which unit tests can't.

## Out of scope

- Automatic tuning of `n_cpu_moe` (e.g., probing VRAM at setup time and computing the ideal value). The measured sweet spot is host-specific enough (depends on desktop compositor VRAM usage, exact GPU) that a fixed, documented, empirically-good default is more honest than a fragile auto-tuner. Revisit if this becomes a recurring need for other MoE models.
- Concurrent/multi-model residency changes — `models_max` stays 1, unaffected by this work.
- Any change to how `low latency` reasoning-token budgets are chosen; the `<think>`-block token consumption observed during research is a documentation note for future prompt/budget tuning, not a code change here.
