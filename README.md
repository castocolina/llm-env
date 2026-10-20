# llm-env

Local llama.cpp router server with GPU acceleration, fronted by an OmniRoute
gateway and a LAN installer for other machines, running as a rootless podman
compose stack on Bazzite.

## Quick start

```bash
make prerequisites  # confirm and install Bazzite/Fedora host tools
make setup       # choose GPU and models (or opt out of local GPU inference entirely), download, generate config
make benchmark   # measure Vulkan throughput; CPU fallback still reports a failed Vulkan benchmark
make start       # start the server
make check-server
```

## What it does

- Serves multiple models from one endpoint using llama.cpp router mode.
  Clients pick a model with the `model` field.
- Detects your GPU, VRAM, and compositor usage, then computes a VRAM budget
  and refuses to start a configuration that cannot fit.
- Uses a Vulkan-only benchmark and records tokens/sec in `models.yml`. If the
  Vulkan benchmark fails, it configures CPU fallback and exits nonzero.
- Runs as a systemd user service. Manual by default; `make enable-boot`
  makes it start with the machine.
- Exposes an OpenAI-compatible API on the LAN with an API key and an
  mDNS `.local` name.
- Runs an OmniRoute gateway alongside the router and auto-configures its
  connection to the local model on every start — no manual dashboard setup.
- Serves a LAN installer other machines can run with one `curl | bash` to
  configure Pi/OpenCode against this host's OmniRoute gateway. See
  [Remote setup](#remote-setup).
- Can run with no local GPU at all — OmniRoute + the remote installer only,
  routing to models elsewhere. See [GPU-optional mode](#gpu-optional-mode).
- Optional System One (Laya) decisions on an AMD ROCm GPU. `english` and
  `multilingual` are loaded by default. Steady VRAM is about 4.26 GB, so
  the default `vram_reserve_mib` is 5120. `english` alone saves only about
  200 MiB, and non-English or short English inputs then fail with HTTP 500.
  Changing `system_one.models` needs `make setup` again. Beyond 64 requests
  in flight, Laya returns HTTP 503 and `Retry-After: 1` (no queue). After
  10 idle minutes (`system_one.idle_unload_seconds`, default 600; `0` never
  unloads) Laya drops its checkpoints and frees about 3.7 GB of VRAM. The
  next request reloads them in about 0.4 s.

### Laya request history in Phoenix

When System One is enabled, normal `make start` also starts Phoenix and the
server-side observer records real Laya requests in the `laya` project. Open
`http://127.0.0.1:6006/projects` (or the configured
`LLM_ENV_PHOENIX_PORT`) and select **laya**. History begins when the observer
is deployed; earlier requests are not backfilled. Use normal `make stop` to
stop the dashboard. Its owned data volume survives normal stops and starts.

The observer records request state and questions, typed answers and their
probabilities/confidence fields, model/routing metadata, known usage, timing,
HTTP status/errors, and batch structure. It does not record HTTP headers and
redacts credential-like JSON keys. Each request and response capture is
limited to 24,000 bytes; truncation is marked. Export uses a 128-record
in-memory queue, a 64 KiB record cap, and a 1.5-second background HTTP
timeout. If Phoenix is unavailable or the queue fills, exports can be lost;
those failures and drops are logged. This is best-effort history, not a
complete audit log. Trace details show the captured input/output; do not
assume every attribute is available as a native chart or that confidence
means correctness. Comparative observer latency overhead has not been measured.

Phoenix binds to loopback and disables authentication. This blocks LAN
publication, but **does not protect traces from other users or processes on
the same machine**. `make update-omniroute` is intentionally deferred: there
is currently no qualified automatic updater or safe isolated replacement path
for this dependency-bearing native deployment. It performs no update and does
not claim that idle-time or image qualification has completed; an architecture
decision is still required before updates can be enabled. Treat prompts, state, and model output in this dashboard
as locally accessible private data. No Phoenix telemetry or off-host export
is enabled. The configured seven-day retention policy
(`LLM_ENV_PHOENIX_RETENTION_DAYS`, default `7`) is a scheduled cleanup policy,
not a storage quota or a guarantee that every record has been deleted on time.
`make status` reports only the bounded first page of up to 1,000 spans; its
range and counts describe that page, and the `completeness` field indicates
whether Phoenix reports another page. Cgroup `memory.low` (best-effort
protection) and `memory.min` (hard protection) are distinct; neither should be
read as the other. CPU quota is reported from `NanoCpus` when available, with
`CpuQuota/CpuPeriod` as the compatible fallback.

The current resource policy reserves 256 MiB soft memory and caps Phoenix at
1 GiB memory and 2 CPUs. CPU shares are 256 relative shares under contention,
not guaranteed cores. These are policy limits, not a load or capacity
benchmark. Browser E2E discovery uses an installed Playwright package or a
local npm cache; set `LLM_ENV_PLAYWRIGHT_ROOT` to a Playwright package
location or `LLM_ENV_CHROMIUM_EXECUTABLE` to an existing Chromium binary when
automatic discovery is unavailable. The test does not install packages.

## Configuration

Everything lives in `~/.config/llm-env/models.yml`. Models are enabled and
disabled with a flag, so nothing is lost when you stop using one:

```bash
uv run llmenv.py models list
uv run llmenv.py models disable ornith
uv run llmenv.py models enable openhermes
```

Reclaim disk space from downloaded models (they are NOT removed by
`make clean`) with:

```bash
make prune
```

Enabled models are all routable. `runtime.models_max` is a separate residency
limit; the target configuration keeps one model resident and unloads it before
loading another alias. This adds model-switch latency but avoids holding both
models in VRAM.

Clean setup maps `gemma4` to yuxinlu1's Agentic Gemma 4 12B v2 Q4_K_M
build with a 131,072-token context. Ornith 1.0 9B uses its full native
262,144-token context (confirmed via its `config.json`'s
`max_position_embeddings`). Both get one request slot with Q5_1 K/V caches.
Pi and OpenCode advertise up to 8,192 output tokens, so reserving the full
output allowance leaves the rest of each model's context for prompt and
history. All tokens still share the same slot. Setup reports an explicit
VRAM-budget failure instead of shrinking context or offloading layers.

An optional `ornith-35b` entry adds Ornith 1.0 35B, a mixture-of-experts
model (35B total / ~3B active parameters per token) too large to fit fully
in most consumer GPUs' VRAM. It uses llama.cpp's `--n-cpu-moe` flag (set via
the model's `n_cpu_moe` field) to keep routed-expert weights for its first N
transformer blocks in host RAM while the rest of the model stays on GPU —
`pylib/gguf.py` computes exactly how many bytes that is from the GGUF's own
tensor layout, and `make setup`/`make start` both refuse to start if
`resources.llm_server.memory_ceiling_pct` isn't high enough to hold it, the
same way they already refuse an infeasible VRAM budget.

The measured llama.cpp build applies a strict admission rule: post-template
prompt tokens must be less than `n_ctx`. With Gemma's 131,072-token slot,
131,071 is admitted with `max_tokens: 1`; 131,072 and above are rejected.
Ornith's 262,144-token slot follows the same rule at its own boundary. This
practical boundary does not change either model's configured client context.

## GPU-optional mode

`make setup` asks "Enable local GPU inference (llm-server)? [Y/n]" — answer
`n` (or set `LLM_ENV_NO_GPU=1` for unattended runs) to skip GPU detection,
model selection/download, and VRAM budgeting entirely, and run this host as
an OmniRoute gateway + remote installer only, with no local llama.cpp
instance and the GPU untouched. This is persisted as `llm_server.enabled` in
`models.yml` — flip it and re-run `make setup`/`make start` to change modes
later. In this mode, configure an upstream provider (OpenRouter, another
frontier API, etc.) through the OmniRoute dashboard yourself; `make start`
does not auto-provision a connection to a local model that doesn't exist.

## Remote setup

Any other machine on the LAN can configure its own Pi/OpenCode sessions
against this host's OmniRoute gateway with one command:

```bash
curl -sS http://<this-host>.local:<remote-setup-port>/setup.sh | bash
```

It prompts for `OMNI_ROUTER_MASTER_KEY` (set in this repo's `.env`) and, once
authenticated, writes a scoped OmniRoute API key to the remote machine's
client configs — it never hands out the master key itself. `make status`
(after `make start`) prints the exact one-liners (mDNS and LAN-IP forms) plus
every other credential you need — OmniRoute's dashboard password and, when
GPU inference is enabled, this host's own API key — under one banner. Run
`make show-secrets` for the same credentials without starting anything.

## Clients

After `make start`, configure normal Pi and OpenCode sessions once:

```bash
make setup-local-llm-agents
```

The command reads the private API key and enabled models from `models.yml`,
checks that the local server is healthy, and writes private client
configuration. It never prints the key. Run it again after rotating the key,
changing the port, or enabling or disabling a model.

`QUICK_START.md` includes copy-paste `curl`, Pi, and OpenCode client examples.
The `make check-with-agents` output masks the key and its temporary isolated
configuration paths, but shows the command flags, selected model, prompt, final
response, validation facts, and result for each check.

## Requirements

Bazzite or Fedora. Run `make prerequisites` to detect and, after explicit
confirmation, install uv, jq, Mike Farah yq v4, podman, curl, iproute, git,
Node.js, and ShellCheck. No compiler needed — the llama.cpp server comes from a
prebuilt image.

## Commands

Run `make help`.

## Ornith 35B acceptance check

`ornith-35b` (opt-in, disabled by default) is a mixture-of-experts model too
large to fit fully in most consumer GPUs' VRAM; it uses `--n-cpu-moe` to keep
some expert weights in host RAM. Unit tests cover the RAM/VRAM split
arithmetic but cannot verify the actual container image's flag handling or
real hybrid CPU/GPU loading, so verify it manually after any change that
touches MoE offload, the resource ceilings, or the Ornith 35B model entry:

1. Select ONLY `ornith-35b` (`select`, not `enable` — `enable` leaves
   whatever was previously selected also enabled, and `make setup` defaults
   to the first enabled model, which can silently re-pick it instead):
   ```bash
   uv run llmenv.py --config ~/.config/llm-env/models.yml models select ornith-35b
   uv run llmenv.py --config ~/.config/llm-env/models.yml models list \
     | jq -r '.models[] | select(.enabled) | .alias'
   # must print exactly: ornith-35b
   ```
2. `make setup` — must complete without the VRAM/RAM budget gate firing, at
   the recommended `n_cpu_moe: 28` / `memory_ceiling_pct: 60`.
3. `make start` — must reach a healthy state (`make check-server` passes).
4. `make benchmark` — measures every enabled model (here, just `ornith-35b`)
   pinned to the configured GPU device, using its own presets-sourced
   `--n-gpu-layers`/`--n-cpu-moe` and its `check_ctx_size`/
   `client_max_output_tokens` for `-p`/`-n` (there is no standalone
   `llama-bench` binary — this runs `llama bench`, a subcommand of `/app/llama`
   in the image, and does not run against the already-started `llm-server`
   container). Results land under
   `.models[] | select(.alias == "ornith-35b") | .benchmark.vulkan` in
   `models.yml` (`pp_tps`, `tg_tps`, `measured_at`) — read it with:
   ```bash
   yq '.models[] | select(.alias == "ornith-35b") | .benchmark.vulkan' \
     ~/.config/llm-env/models.yml
   ```
   Compare `pp_tps`/`tg_tps` against the baseline recorded in
   `docs/superpowers/specs/2026-08-10-ornith-35b-moe-incorporation-design.md`
   (`n_cpu_moe 28`: pp @ depth 0 ≈ 441 tok/s, tg @ depth 0 ≈ 39 tok/s) — note
   that baseline used a short `-p 512 -n 128 -d 0,32768` probe, while
   `make benchmark` now measures at the model's full configured context size,
   so a lower `pp_tps` at a much larger `-p` is expected, not a regression by
   itself. A large regression indicates the offload split, quantization, or
   `n_gpu_layers` changed, not just the budgeting arithmetic.
5. During a generation request against the running server, confirm
   sustained CPU utilization (`podman stats llm-server`) is in the same
   ballpark as the design doc's measurement (~43-44% average across host
   threads) — much higher suggests `cpu_ceiling_pct` isn't actually being
   applied (compare `podman inspect llm-server`'s `cpus`/`NanoCpus` against
   `resources.llm_server.cpus` in `models.yml`).
