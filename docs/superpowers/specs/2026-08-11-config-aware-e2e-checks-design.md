# Config-Aware E2E Check Scripts — Design

**Goal:** Make `check-setup.sh`, `check-server.sh`, and `benchmark.sh` read every model-specific value (offload split, context size, output-token budget, GPU device, probe sizing) from `models.yml` instead of hardcoding or re-deriving it — so these scripts always validate exactly the configuration that's actually deployed, not a silently different one.

**Architecture:** Reuse `llmenv presets`'s existing output (the same `n-gpu-layers`/`n-cpu-moe`/`ctx-size` flags `start.sh` already generates for the real server) as the single source of truth for check-setup.sh and benchmark.sh, instead of each script re-deriving flags from raw model config. `check-server.sh` reads `client_max_output_tokens` directly (a client-side value, not a server flag, so it isn't part of `presets.ini`). Two new optional per-model config fields (`check_ctx_size`, `check_timeout_seconds`) give checks room to diverge from full production parity only when explicitly and visibly configured to — never silently in script code. Benchmark results move from one shared config slot to per-model storage.

**Tech stack:** No new dependencies. Reuses `llmenv presets`, `llmenv detect`, `llmenv resolve-device` (all already exist), `yq`, `jq`.

## Global Constraints

- **No hardcoded values in `.sh`/`.py` scripts for anything that varies per model.** `n_cpu_moe`, `ctx_size`, `client_max_output_tokens`, probe context size, probe timeout — all must come from `models.yml`, either directly or via an optional override field that defaults to the model's real configured value. Where a script currently can't get a value from config, the fix is to add that field to the schema, never to hardcode a script-level default that diverges from production.
- **Checks validate exactly the configured production values by default** — the same `ctx_size`/`n_cpu_moe` the real server uses, matching what was empirically validated in the Ornith 35B MoE research spike. A check is never silently scaled down to make it faster or more likely to pass. If a specific model genuinely needs a different probe size, that's an explicit, documented, per-model config override (`check_ctx_size`), never an implicit script shortcut.
- **Scripts must be agnostic to how many models are enabled** — loop over every enabled model, never assume exactly one (even though `runtime.models_max` stays 1 by convention elsewhere in this repo).
- Lazy-load (llama.cpp router mode) + idle-unload stays exactly as-is. No eager-load option is being added — it already works correctly (confirmed via live acceptance testing: `status`/`gpu-status` showed the model unloading after `sleep_idle_seconds`).
- `make prune` is explicitly out of scope (destructive, unrelated to check/benchmark config-awareness).
- `check-with-agents.sh`'s Pi-client failures are out of scope — confirmed to be a broken local `pi` CLI npm/pnpm install (`Cannot find module '.../dist/cli.js'`), unrelated to model config or this repo's code.

## Background / Research Findings

This design is triggered by a real acceptance-testing run against `ornith-35b` on live hardware (AMD RX 9070 XT), executed after the Ornith 35B MoE incorporation plan shipped. Findings:

- **`check-server.sh` hardcodes `max_tokens: 256`** for its completion probe, regardless of the model's own `client_max_output_tokens` (8192 for Ornith). Ornith emits `<think>` reasoning content before any final answer; against the real `/v1/chat/completions` endpoint, the probe reliably exhausted its 256-token budget mid-reasoning and returned `finish_reason: length` with **empty** final content — a false failure, not a real problem with the deployment.

- **`check-setup.sh`'s offline inference check builds its `llama cli` invocation from only `.n_gpu_layers`**, never `.n_cpu_moe` or `.ctx_size`. Directly reproduced: running the exact command `check-setup.sh` builds for `ornith-35b` (no `--n-cpu-moe`, no `--ctx-size`) loaded successfully in ~15s and correctly answered "ready" — despite the model being 21GB against 16GB of VRAM. This strongly indicates llama.cpp fell back to a different (likely much-closer-to-full-CPU) expert-offload profile than the tuned `n_cpu_moe: 28`, since `--n-gpu-layers` alone doesn't govern MoE expert placement. The check "passes," but it never validates the configuration that's actually deployed. Notably, this check *also* succeeded where `check-server.sh`'s same-sized-looking probe failed — because `llama cli`'s raw single-turn mode applies the chat template differently than the server's actual `/v1/chat/completions` path, producing a shorter reasoning trace. Two checks that look equivalent gave different, both-somewhat-wrong signals.

- **`benchmark.sh` unconditionally benchmarks the single smallest enabled model**, passes no `--n-cpu-moe`, and doesn't restrict which GPU device llama.cpp uses (`podman run --device /dev/dri` grants access to every render node; the benchmark's own `podman run ... bench` call has no `--device` flag). A real run against `ornith-35b` auto-distributed across **both** the discrete GPU and the integrated GPU (UMA/system-RAM-backed), producing pp≈833.6/tg≈34.95 tok/s — numbers that don't correspond to the actual single-dGPU, `n_cpu_moe: 28` deployment the design doc's baseline was measured against.

- **`gpu.benchmark.vulkan.{pp_tps,tg_tps,measured_at}` is one shared config slot**, regardless of which model produced the numbers — already ambiguous the moment more than one model has ever been benchmarked.

- Everything else in the acceptance run was clean: `make setup` correctly backfilled `ornith-35b` and computed a fitting VRAM budget; `make start` correctly wrote `n-cpu-moe = 28`/`ctx-size = 262144` into `presets.ini` and capped the container at 14 CPUs; `make check-setup`, `make status`, and `make gpu-status` all passed and correctly reflected the deployed config; `make check-with-agents` — OpenCode passed both agentic checks (real tool use, live weather/FX API calls, correct JSON extraction) against `ornith-35b`; Pi failed on an unrelated broken local install.

## Components

### 1. `models.yml` schema: two new optional per-model fields

- `check_ctx_size` (int, optional): overrides `ctx_size` specifically for check/probe requests. **Defaults to the model's real `ctx_size` when unset** — checks run at full production parity out of the box.
- `check_timeout_seconds` (int, optional): overrides the probe command's timeout. Defaults to a script-level baseline (set from real cold-load measurement, see Component 7) when unset.

Both are per-model overrides, never hardcoded fallbacks that diverge silently from configured values.

### 2. Shared flag source: reuse `llmenv presets`

`check-setup.sh` and `benchmark.sh` both call `llmenv presets` (writing to a throwaway tmp file, or reusing the already-fresh copy `start.sh` maintains) to get the exact `n-gpu-layers`/`n-cpu-moe`/`ctx-size` flags per alias — the same file the real server uses — instead of re-deriving flags from raw model config independently. A small shared helper in `tools/lib.sh` extracts one alias's INI section. This directly closes the root cause of the bug found in research: two scripts independently re-deriving "what flags does this model need" drifted from the one already-correct implementation in `pylib/presets.py`.

### 3. `check-server.sh`: per-model `max_tokens`

The completion probe's `max_tokens` comes from the model's own `client_max_output_tokens` via `yq`, not a hardcoded `256`. Applies everywhere the script sends a completion request.

### 4. `check-setup.sh` + `check-server.sh`: config-aware probe sizing and timeout

Both scripts' probe requests use `check_ctx_size` (falling back to `ctx_size`) and `check_timeout_seconds` (falling back to the script's tuned default) per Component 1. `check-setup.sh`'s offline inference check additionally passes `--n-cpu-moe`/`--n-gpu-layers` from Component 2's shared presets source, and iterates every enabled model rather than assuming one.

### 5. `benchmark.sh`: model-aware, multi-model, device-pinned

Iterates every enabled model (not just the smallest). For each: resolves the configured GPU's specific Vulkan device index (reusing `check-setup.sh`'s existing `resolve-device` pattern) and passes it explicitly to the `podman run ... bench` invocation — no more auto-selection across every available device — plus `--n-cpu-moe` from Component 2. Writes results to that model's own `benchmark.vulkan` key (Component 6).

### 6. `models.yml` schema: per-model benchmark storage

Move `gpu.benchmark.vulkan.{pp_tps,tg_tps,measured_at}` to `.models[].benchmark.vulkan.{pp_tps,tg_tps,measured_at}`. `migrate_config()` drops the old top-level `gpu.benchmark` key on migration (the same precedent it already applies for other obsolete generated keys) rather than trying to guess which model it belonged to.

### 7. Empirical timeout tuning

During implementation, measure real cold-load time against the largest configured model (`ornith-35b`, full `262144` ctx) end-to-end through the actual check scripts. Use that measurement (with margin) to set the scripts' default `check_timeout_seconds` baseline, and decide whether `ornith-35b` needs an explicit `check_timeout_seconds` override in `models.yml.example` — the same empirically-driven approach the original MoE design doc used for `n_cpu_moe`/`vram_budget`.

## Testing

- Unit tests for the new schema fields (`check_ctx_size`, `check_timeout_seconds` defaults/validation) in `tests/test_config.py`, and for the per-model `benchmark` key migration.
- Shell tests for `check-setup.sh`, `check-server.sh`, `benchmark.sh`'s new config-aware flag construction, mirroring existing patterns in `tests/test_shell.py`.
- Manual acceptance (documented in README, not automated, same pattern as the MoE plan's acceptance check): re-run the full `make setup → check-setup → start → status → gpu-status → check-server → benchmark → check-with-agents` chain against `ornith-35b` on real hardware — this time comparing `check-server`/`benchmark` results against the design doc's recorded `n_cpu_moe: 28` baseline, since the first acceptance run silently exercised the wrong offload profile.

## Out of scope

- `make prune` — explicitly excluded, unrelated to check/benchmark config-awareness.
- `check-with-agents.sh`'s Pi-client failures — a broken local `pi` npm/pnpm install, a host-repair task unrelated to model config. `check-with-agents.sh` itself isn't touched by this plan; it doesn't hardcode token budgets the way `check-server.sh` does, since it delegates to each client's (OpenCode's/Pi's) own tool-use loop.
- Adding an eager-load mode — lazy-load + idle-unload remains the only mode; it already works correctly.
