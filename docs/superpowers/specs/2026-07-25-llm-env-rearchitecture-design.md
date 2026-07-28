# LLM Environment Re-architecture — Design Spec

**Date:** 2026-07-25
**Status:** Approved for planning
**Supersedes:** `2026-07-22-script-restructuring-design.md`

## Goal

Replace the distrobox + source-build + bash-only environment with a prebuilt-image,
podman-quadlet, YAML-configured LLM server that is measured rather than assumed:
GPU device, VRAM budget, and inference backend are all detected at setup and stored
in config, never hardcoded.

---

## 1. Measured Context

All values below were measured on the target machine on 2026-07-25, not assumed.

### Hardware

| Property | Value |
|---|---|
| OS | Bazzite 44 (Kinoite), immutable, kernel 7.1.3 |
| CPU | AMD Ryzen 9 9900X, 12c / 24t |
| RAM | 30 GiB total, ~21 GiB available |
| dGPU | RX 9070 XT, Navi 48, **gfx1201** (RDNA4), 16304 MiB VRAM, PCI `0000:03:00.0` = `card1` = `renderD128` |
| iGPU | Raphael, 512 MiB VRAM, PCI `0000:0e:00.0` = `card0` = `renderD129` |
| Displays | `card0-HDMI-A-2` connected (iGPU), `card1-DP-2` connected (dGPU) |
| Compositor | KDE / Wayland; `plasmashell` renders on `renderD128` (**the dGPU**) |
| dGPU VRAM in use | 2026 MiB (with both displays attached) |

### Available tooling

`podman 5.8.4` (no docker), `distrobox 1.8.2.5`, quadlet at `/usr/libexec/podman/quadlet`
with `podman-user-generator` wired in (verified by dry-run), `python 3.14.6`,
`uv 0.11.28`, `PyYAML 6.0.3`, `yq`, `jq 1.8.1`, `curl`, `systemd 259`,
`firewall-cmd 2.4.4`, `avahi-browse 0.8`. `Linger=no`. No JVM, no groovy.

### Upstream facts verified against the llama.cpp source tree

- Router mode is real and documented: `--models-preset`, `--models-max`,
  `--models-autoload`, `--models-dir` (`common/arg.cpp:3427`, `tools/server/README.md:220`).
- Preset INI format requires `version = 1` and supports a `[*]` global section
  (`tools/server/README.md:1654`).
- `--list-models` **does not exist**. The current `setup.sh:88` call is invalid.
- Device selection: `-dev, --device <dev1,dev2,...>` by device *name*, plus
  `--list-devices`, env `LLAMA_ARG_DEVICE` (`common/arg.cpp:2535`).
- Vulkan device names are **index-based** (`Vulkan0`, `Vulkan1`) — `ggml-vulkan.cpp:6670`.
  Indices may reorder; they are therefore unsafe to persist.
- `GGML_VK_VISIBLE_DEVICES` is honoured (`ggml-vulkan.cpp:7170`).
- `--sleep-idle-seconds` unloads idle models (`tools/server/README.md:236`).
- KV quantization `-ctk/-ctv` and flash attention `-fa` are available.
- The Vulkan image builds for `gfx1201`.
- The `server-vulkan` image is **0.31 GB** compressed with 6 layers.
- `KWIN_DRM_DEVICES` exists in `libkwin.so` (currently unset).

---

## 2. Decisions

| # | Decision | Rationale |
|---|---|---|
| D1 | Manual start/stop by default, opt-in boot | User choice (C). `loginctl enable-linger` gates boot start. |
| D2 | Prebuilt images only; no source build | Deletes the entire build pipeline, where every discovered bug lives. |
| D3 | Bash orchestrates; one Python helper for parsing/config | `jq` + `yq` cover shell-side JSON/YAML; Python owns schema, arithmetic, INI generation. |
| D4 | Benchmark Vulkan at setup, with CPU fallback | Measure Vulkan throughput and record failure details rather than assuming GPU inference works. |
| D5 | VRAM budget measured at every start | Compositor usage is dynamic (observed 1403 → 2026 MiB during design). |
| D6 | `models_max` = count of enabled models | Hard user requirement; feasibility is validated and reported, not silently overridden. |
| D7 | Store PCI address, resolve device index at runtime | Vulkan indices are unstable; PCI addresses are not. |
| D8 | API key + mDNS for LAN access | Prevents unauthenticated GPU consumption; `.local` survives DHCP changes. |

---

## 3. Architecture

### Entry points (Makefile = thin dispatcher, ≤3 lines per target)

```
make setup          → setup.sh
make start          → start.sh
make stop           → stop.sh
make check-setup    → check-setup.sh
make check-server   → check-server.sh
make benchmark      → benchmark.sh
make enable-boot    → inline (2 lines)
make disable-boot   → inline (2 lines)
make status         → inline (1 line)
make logs           → inline (1 line)
make validate       → shellcheck + `uvx ruff`
```

`shellcheck` is installed; `ruff` is not, so it runs via `uvx ruff` (no global install).

Any target body exceeding 3 lines must delegate to a `.sh` file.

### Bash layer

| File | Responsibility |
|---|---|
| `lib.sh` | Logging, colours, YAML reads via `yq`, error trap |
| `setup.sh` | Interactive configurator; orchestrates detection, selection, pull, benchmark, config write |
| `start.sh` | Resolve device, compute budget, render quadlet, start unit, gate on health |
| `stop.sh` | Stop the systemd unit |
| `check-setup.sh` | Offline validation |
| `check-server.sh` | Online API contract validation |
| `benchmark.sh` | Re-run backend benchmark on demand |

### Python layer — single `llmenv.py`, invoked as `uv run llmenv.py <cmd>`

PEP 723 inline header declares PyYAML. Subcommands:

| Subcommand | Output |
|---|---|
| `detect` | JSON: GPUs, PCI↔card↔renderD map, VRAM total/used, connector status, compositor render node |
| `budget` | JSON: reserve, available, per-model cost, feasibility verdict, remedies |
| `presets` | Writes `presets.ini` via `configparser` (guarantees `version = 1` and `[*]`) |
| `resolve-device` | Maps stored PCI address → current `VulkanN` name using `--list-devices` |
| `models` | `list` / `enable <alias>` / `disable <alias>` against `models.yml` |
| `validate-gguf` | Checks GGUF magic bytes and size for enabled models |

---

## 4. Config Schema — `models.yml`

Single user-editable file. Disabling never deletes.

```yaml
version: 1

server:
  host: 0.0.0.0
  port: 8000
  api_key: "<generated at setup>"
  mdns_name: llm                # → http://llm.local:8000
  sleep_idle_seconds: 300

gpu:
  pci_address: "0000:03:00.0"
  device_name: "AMD Radeon RX 9070 XT (RADV GFX1201)"
  backend: vulkan               # vulkan | cpu — set by benchmark
  image: ghcr.io/ggml-org/llama.cpp:server-vulkan
  vram_total_mib: 16304
  reserve_mode: auto            # auto = re-measure compositor each start
  reserve_floor_mib: 1024
  benchmark:                    # recorded evidence, written by benchmark.sh
    vulkan: { pp_tps: null, tg_tps: null, measured_at: null }

runtime:
  models_max: 2                 # derived: count(enabled models) — 2 enabled below
  flash_attn: true
  cache_type_k: q8_0
  cache_type_v: q8_0

models:
  - alias: gemma4
    enabled: true
    file: gemma-4-12B-it-Q4_K_M.gguf
    url: https://huggingface.co/...
    size_bytes: 7660000000
    vram_budget: 55%            # accepts "55%" or "7.5GB"
    ctx_size: 8192
    n_gpu_layers: 99

  - alias: ornith
    enabled: true
    file: ornith-1.0-9b-Q4_K_M.gguf
    url: https://huggingface.co/...
    size_bytes: 5600000000
    vram_budget: 40%
    ctx_size: 8192
    n_gpu_layers: 99

  - alias: openhermes                 # example of a retained-but-disabled entry
    enabled: false
    file: openhermes-2.5-mistral-7b.Q4_K_M.gguf
    url: https://huggingface.co/...
    size_bytes: 4368450304
    vram_budget: 30%
    ctx_size: 8192
    n_gpu_layers: 99
```

**Derived rules.** `runtime.models_max` is always recomputed as the count of enabled
models. `reserve_mode: auto` re-measures compositor VRAM at every start.

---

## 5. VRAM Budget Model

```
spike_headroom = 1024 MiB       # fixed allowance for compositor/browser/game spikes
available   = vram_total - max(measured_compositor_usage, reserve_floor) - spike_headroom
cost(model) = weights + kv_cache(ctx_size, cache_type_k/v, flash_attn)
feasible    = sum(cost(m) for m in enabled) <= available
```

`spike_headroom` is a constant, not a tunable, and is defined here as the single source
of truth for its value.

Current measured state (compositor on dGPU, 2026 MiB):

| Scenario | Reserve | Available | 2 models + KV | Verdict |
|---|---|---|---|---|
| As-is, f16 KV | ~3000 | ~13300 | 12673 + ~2000 | over by ~1.4 GB |
| As-is, `-fa` + q8_0 KV | ~3000 | ~13300 | 12673 + ~1000 | borderline |
| `KWIN_DRM_DEVICES=/dev/dri/card0` | ~300 | ~16000 | 12673 + ~2000 | fits |

When `models_max` exceeds the budget, setup reports the shortfall and offers concrete
remedies (enable flash attention, quantize KV, lower `ctx_size`, disable a model, or
pin the compositor to the iGPU). It does not silently reduce `models_max`.

---

## 6. Backend Selection

`benchmark.sh` runs `llama-bench` on the smallest enabled model and records
Vulkan prompt-processing and generation throughput.

Fallback behavior — failures are logged with a reason and return nonzero:

```
Vulkan requires /dev/dri/renderD* + container start + bench success
  ↓
CPU    is configured as fallback; the benchmark still exits nonzero
```

Results are written to `gpu.benchmark` so the choice is auditable and re-runnable.

---

## 7. Service Model

Quadlet unit at `~/.config/containers/systemd/llm-server.container`, rendered from
`models.yml` at `make start`. Uses `--device /dev/dri`, mounts the models directory
read-only, and health-gates on `/health` via `HealthCmd`.

Removes: `PID_FILE`, stale-PID handling, the manual `curl` health loop, and `stop.sh`'s
kill/`kill -9` escalation. Boot start is opt-in via `loginctl enable-linger` +
`systemctl --user enable`.

---

## 8. Network Access

Setup generates an API key, opens the firewall port via `firewall-cmd`, and publishes
`<mdns_name>.local` through avahi. It then prints working `curl` samples for both
localhost and LAN, plus paste-ready OpenAI-compatible client config
(`base_url`, `api_key`, model aliases).

---

## 9. Validation

**`check-setup.sh` (offline):** config parses against schema; image present locally;
GGUF magic bytes valid for enabled models; stored PCI address resolves to a live
device; VRAM budget feasible for `models_max`.

**`check-server.sh` (online):** `/health` responds; `/v1/models` lists exactly the
enabled aliases; one completion per model returns non-empty content; a bad API key is
rejected. All request bodies built with `jq -n --arg`.

Explicitly **not** tested: real-time knowledge. The current `server-test.sh` asserts the
model knows the weather, which a bare `llama-server` cannot do; its regex also matches
the substring `am`, so it passes on hallucinations.

---

## 10. Defects Resolved

| Defect | Location | Resolution |
|---|---|---|
| `${TEST_PASS + TEST_FAIL}` → `bad substitution`, always exit 1 | `setup-test.sh:66,68`, `server-test.sh:80,82` | Scripts rewritten |
| llama.cpp cloned into repo (CWD never set to `$WORK_DIR`) | `setup.sh:155` | Build pipeline deleted |
| 1.5 GB untracked clone, `.gitignore` has only `tmp/` | repo root | Removed; `.gitignore` extended |
| `EXIT_CODE=$?` after `|| true` always 0 | `debug-inference.sh:53` | Script deleted |
| Capability test masquerading as API test | `server-test.sh` | Replaced with contract test |
| `--list-models` is not a real flag | `setup.sh:88` | Replaced by `validate-gguf` |
| `cd llama.cpp && git pull \|\| true && cd ..` precedence bug | `setup.sh:173` | Deleted |
| Hardcoded `case 1/2/3` vs dynamic model list | `setup.sh:16-33` | YAML-driven selection |
| `.config` written but never read | `setup.sh:70`, `start.sh:12` | Single `models.yml` |
| `stat -c%s` despite documented `get_file_size` rule | `models.sh:71` | Python helper |
| `hostname -I` is Linux-only | `start.sh:73` | Python detection |
| Unescaped prompt interpolation into JSON | `server-test.sh:44` | `jq -n --arg` |
| `cd` without subshell leaks CWD | `models.sh:83` | Rewritten |
| `presets.ini` missing `version = 1` / `[*]` | generated file | `configparser` |
| No device pinning; iGPU/llvmpipe may receive layers | all inference calls | `--device` from resolved PCI |
| `models-max 2` exceeds VRAM | `models.sh` | Budget model |
| No auth on `0.0.0.0` | `start.sh` | `--api-key` |

### Documentation drift to correct

`AGENTS.md` references `make setup-dev` (target does not exist) and `.agents/setup-dev.md`
documents `detect_os()`, `get_file_size`, `get_cpu_cores`, `download_file` (none exist)
plus a macOS/Metal path that has no implementation. Both files are rewritten to match
reality; macOS support is dropped from the docs rather than claimed.

---

## 11. Risks and Open Questions

1. **Model provenance unverified.** `bartowski/gemma-4-12B-it-GGUF` and
   `deepreinforce-ai/Ornith-1.0-9B-GGUF` could not be confirmed upstream. Files exist
   locally at plausible sizes. Mitigated by `validate-gguf`; the YAML makes replacement trivial.
2. **Vulkan benchmark failure.** GPU initialization or throughput parsing can fail. The
   benchmark records CPU fallback, emits complete diagnostics, and exits nonzero.
3. **Compositor pinning is disruptive.** `KWIN_DRM_DEVICES=/dev/dri/card0` would blank the
   dGPU DP monitor. Documented as an option, not applied automatically.
4. **`models_max` vs VRAM.** Honouring the user rule can produce an infeasible config;
   resolved by reporting loudly at setup rather than overriding.
5. **Rootless podman GPU access.** Confirmed: the user's groups are `bazzite wheel` only —
   **not** `render` or `video`. This works today solely because Bazzite ships
   `/dev/dri/renderD*` as mode `0666`. That is distro policy and could change
   on an update. `check-setup.sh` verifies device readability explicitly rather than assuming
   group membership.

---

## 12. Out of Scope

macOS support; multi-host inference (RPC); model fine-tuning; TLS certificates
(`--ssl-cert-file` noted but unused on a trusted LAN); web search or tool-calling
infrastructure.
