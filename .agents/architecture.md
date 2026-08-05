# Architecture

## Layers

Bash orchestrates (`podman`, `systemctl`, `curl`, `yq`). Python parses and
computes (`uv run llmenv.py`). The two communicate over JSON via `jq`.

## Files

| File | Responsibility |
|---|---|
| `Makefile` | Thin dispatcher, no logic beyond 3 lines |
| `tools/lib.sh` | Logging, paths, `require_cmd`, the `llmenv` wrapper |
| `setup/setup.sh` | Interactive configuration, downloads, network exposure |
| `scripts/benchmark.sh` | Vulkan-only measurement with CPU fallback; runs via `llama bench`, a subcommand of `/app/llama` in the image (there is no separate `llama-bench` binary) |
| `scripts/start.sh` | Budget check, device resolution, quadlet render, health gate |
| `scripts/stop.sh` / `scripts/clean.sh` | Lifecycle |
| `scripts/check-setup.sh` | Offline validation |
| `scripts/check-server.sh` | Online API contract validation |
| `llmenv.py` | CLI dispatcher, JSON out |
| `pylib/agent_runner.py` | Bounded live-agent scopes, stream capture, and cleanup proof |
| `pylib/config.py` | Schema, enable/disable, `models_max` validation and clamping |
| `pylib/gguf.py` | GGUF header parsing, KV geometry |
| `pylib/detect.py` | GPU and compositor detection from sysfs |
| `pylib/budget.py` | VRAM arithmetic and remedies |
| `pylib/presets.py` | `presets.ini` via configparser |

## Invariants

- `runtime.models_max` is a validated residency limit between 1 and the enabled
  model count; model selection clamps it downward only when necessary.
- `pylib/gguf.py` owns per-layer full/recurrent/SWA geometry; `pylib/budget.py`
  owns Q5_1 byte arithmetic, the 512 MiB per-resident runtime allowance, and the
  independent 1024 MiB spike reserve.
- The target deployment uses one request slot. Automatic fitting, context
  shifting, cache changes, and CPU offload remain disabled.
- Vulkan device indices are never persisted; the PCI address is.
- An infeasible VRAM budget is reported, never silently corrected.
- `presets.ini` must contain no `[DEFAULT]` section and no `version` key;
  `llama-server` treats every INI section as a model preset and would
  register a phantom model.
- Host-side probes use `127.0.0.1`, never `localhost`.
- A failed Vulkan benchmark configures CPU fallback and exits nonzero.
- Live Pi and OpenCode checks enter a random systemd user scope through
  `run-agent-bounded`; Bash consumes only the six-field result JSON and never
  treats unproved cleanup as a model result.

## Platform

Linux only. Bazzite/Fedora with podman and rootless quadlets. There is no
macOS support and none is planned.
