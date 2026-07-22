# LLM Environment

Automated setup for llama.cpp on Bazzite (Linux) with GPU acceleration.

## Quick Start

```bash
make setup      # Download/compile (idempotent, uses checkpoints)
make start      # Start server, print connection info
make test       # Verify server and agent integration
make stop       # Stop server
```

## Model Selection

| Model | Size | Best For |
|-------|------|----------|
| Gemma 4 12B Q4_K_M | ~7.6 GB | Best for 16GB VRAM, multimodal |
| Ornith 1.0 9B Q4_K_M | ~5.6 GB | Coding specialist (69.4% SWE-bench) |

Select model during `make setup`. Config saved to `~/llm-workspace/.config`.

## Remote Access (macOS Client)

Connect directly to Linux IP (no SSH tunnel needed):

```bash
http://<linux-ip>:8000/docs
```

Configure OpenCode to use local server:
```json
{
    "provider": {
        "local": {
            "api_key": "none",
            "models": {
                "local-model": {
                    "endpoint": "http://<linux-ip>:8000/v1/chat/completions"
                }
            }
        }
    }
}
```

## Available Commands

| Command | Description |
|---------|-------------|
| `make setup` | Download/compile environment (idempotent) |
| `make start` | Start LLM server |
| `make stop` | Stop LLM server |
| `make test` | Run server and agent tests |
| `make shell` | Enter distrobox container |
| `make cache-status` | Show checkpoint status |
| `make clean-cache` | Clear all checkpoints |
| `make clean` | Remove container & workspace |
| `make validate` | Run shellcheck on all scripts |

## Hardware Requirements

- **GPU**: AMD 9070 XT 16GB VRAM (or equivalent)
- **RAM**: 32GB DDR5
- **Storage**: 2TB NVMe SSD
- **OS**: Bazzite (Fedora-based) with distrobox

## Architecture

- **Linux only** for server (macOS connects as client)
- **Distrobox** container for isolated build environment
- **Vulkan** GPU acceleration for llama.cpp
- **Checkpoint system** for idempotent setup
