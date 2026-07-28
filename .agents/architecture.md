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
| `pylib/config.py` | Schema, enable/disable, `models_max` sync |
| `pylib/gguf.py` | GGUF header parsing, KV geometry |
| `pylib/detect.py` | GPU and compositor detection from sysfs |
| `pylib/budget.py` | VRAM arithmetic and remedies |
| `pylib/presets.py` | `presets.ini` via configparser |

## Invariants

- `runtime.models_max` always equals the count of enabled models.
- Vulkan device indices are never persisted; the PCI address is.
- `spike_headroom` is 1024 MiB, defined only in `pylib/budget.py`.
- An infeasible VRAM budget is reported, never silently corrected.
- `presets.ini` must contain no `[DEFAULT]` section and no `version` key;
  `llama-server` treats every INI section as a model preset and would
  register a phantom model.
- Host-side probes use `127.0.0.1`, never `localhost`.
- A failed Vulkan benchmark configures CPU fallback and exits nonzero.

## Platform

Linux only. Bazzite/Fedora with podman and rootless quadlets. There is no
macOS support and none is planned.
