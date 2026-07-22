# LLM Environment

Automated setup for llama.cpp on Bazzite (Linux) with GPU acceleration.

## Quick Start

```bash
make all          # Full setup + test + start + server-test
```

Or step by step:

```bash
make setup        # Download/compile (interactive model selection)
make setup-test   # Test inference on downloaded models
make start        # Start server, print connection info
make server-test  # Live agent test (forces internet access)
make stop         # Stop server
```

## Models (Router Mode)

Both models are served simultaneously from a single server:

| Alias | Model | Size | Best For |
|-------|-------|------|----------|
| `gemma4` | Gemma 4 12B Q4_K_M | ~7.6 GB | Multimodal, general tasks |
| `ornith` | Ornith 1.0 9B Q4_K_M | ~5.6 GB | Coding specialist (69.4% SWE-bench) |

Total VRAM usage: ~13.2 GB (fits in 16GB). Models are loaded on-demand with LRU eviction.

Select model via `model` field in API requests.

## Remote Access (macOS Client)

Connect directly to Linux IP (no SSH tunnel needed):

```bash
http://<linux-ip>:8000/docs
```

### Usage

```bash
# Use Gemma4 (general tasks)
curl http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "gemma4", "messages": [{"role": "user", "content": "Hello"}]}'

# Use Ornith (coding tasks)
curl http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "ornith", "messages": [{"role": "user", "content": "Write a Python function"}]}'
```

### OpenCode Configuration

```json
{
    "provider": {
        "local": {
            "api_key": "none",
            "models": {
                "gemma4": {
                    "endpoint": "http://<linux-ip>:8000/v1/chat/completions"
                },
                "ornith": {
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
| `make all` | Full setup + test + start + server-test |
| `make setup` | Download models, build llama.cpp |
| `make setup-test` | Test inference on downloaded models |
| `make start` | Start LLM server |
| `make stop` | Stop LLM server |
| `make server-test` | Live agent test (forces internet) |
| `make shell` | Enter distrobox container |
| `make cache-status` | Show build checkpoint status |
| `make clean-cache` | Clear all checkpoints |
| `make clean` | Remove container & workspace |
| `make validate` | Run shellcheck on all .sh files |

## Architecture

```
models.sh      ← Single source of truth (model definitions + helpers)
setup.sh       ← Download models, build llama.cpp, validate
setup-test.sh  ← Inference test based on downloaded models
start.sh       ← Launch server based on presets.ini
server-test.sh ← Live test forcing internet access
stop.sh        ← Server shutdown
```

## Workspace

- Models: `~/llm-workspace/models/`
- Config: `~/llm-workspace/.config`
- Presets: `~/llm-workspace/presets.ini`
- Logs: `~/llm-workspace/.config/server.log`
