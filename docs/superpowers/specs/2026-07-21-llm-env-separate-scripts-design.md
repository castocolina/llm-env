# LLM Environment - Separate Scripts Design

## Overview

Refactor the LLM environment setup into separate scripts for setup, server lifecycle, and testing. Linux-only server (macOS connects as client via direct IP). Model selection via short aliases stored in config.

## Model Presets

| Alias | Model | File | Size | Notes |
|-------|-------|------|------|-------|
| `gemma4` | Gemma 4 12B Q4_K_M | `gemma-4-12B-it-Q4_K_M.gguf` | ~7.6 GB | Dense, multimodal, best for 16GB VRAM |
| `ornith` | Ornith 1.0 9B Q4_K_M | `ornith-1.0-9b-Q4_K_M.gguf` | ~5.6 GB | Coding specialist (69.4% SWE-bench) |

Both require `--jinja` flag for chat templates. Both fit comfortably in 16GB VRAM.

## Config File

Location: `~/llm-workspace/.config`

```
MODEL_ALIAS=gemma4
MODEL_NAME=gemma-4-12B-it-Q4_K_M.gguf
MODEL_URL=https://huggingface.co/bartowski/gemma-4-12B-it-GGUF/resolve/main/gemma-4-12B-it-Q4_K_M.gguf
SERVER_PORT=8000
SERVER_HOST=0.0.0.0
```

Server PID stored at: `~/llm-workspace/.config/server.pid`

## Scripts

### setup/setup.sh

Download/compile only. Does NOT start server.

Steps:
1. Prompt model selection (gemma4 / ornith) unless MODEL_ALIAS env var is set
2. Save selection to config file
3. Create distrobox container (idempotent, checkpointed)
4. Download model file if not present (idempotent, checkpointed)
5. Compile llama.cpp with Vulkan in distrobox (idempotent, checkpointed)
6. Run basic inference test inside container (idempotent, checkpointed)
7. Print summary: what was set up, how to start server

Checkpoints stored at: `~/llm-workspace/.cache/checkpoints/`

### scripts/start.sh

Launch server and wait for it to be ready.

Steps:
1. Read config file
2. Check if server already running (PID file exists and process alive)
3. Start `llama-server` inside distrobox:
   - `-m ../models/$MODEL_NAME`
   - `-ngl 99` (full GPU offload)
   - `--jinja` (required for both models)
   - `--host $SERVER_HOST`
   - `--port $SERVER_PORT`
   - `--ctx-size 8192`
4. Save PID to server.pid
5. Wait loop: curl `http://localhost:$SERVER_PORT/health` with 30s timeout
6. On success: print connection info
   - Local: `http://localhost:$SERVER_PORT/docs`
   - Network: `http://$(hostname -I | awk '{print $1}'):$SERVER_PORT/docs`
   - OpenCode config: model endpoint URL
7. On failure: print logs, exit 1

### scripts/stop.sh

Kill running server.

Steps:
1. Read PID from server.pid
2. Kill process
3. Clean up PID file
4. Print confirmation

### test.sh

Verify server and agent integration.

Steps:
1. Check server is running (health endpoint)
2. **curl test**: Send a simple prompt, verify JSON response with content
3. **opencode test**:
   - Check if opencode is installed
   - Configure opencode to use local server (model endpoint)
   - Send prompt that requires internet: "What is the current temperature in Santiago, Chile?"
   - Verify response contains temperature data (proves internet access works)
4. Print results

## Makefile Targets

```makefile
setup:       # Run setup/setup.sh
start:       # Run scripts/start.sh
stop:        # Run scripts/stop.sh
test:        # Run test.sh
validate:    # Run shellcheck on all .sh files
shell:       # Enter distrobox container
cache-status:# Show checkpoint status
clean-cache: # Remove checkpoints
clean:       # Remove everything
```

## Files to Remove

None. Old `setup-dev.sh` gets replaced by `setup/setup.sh` (same location, root directory).

## Remote Access (macOS to Linux)

No SSH tunnel needed if on same network. macOS connects directly:
```
http://<linux-ip>:8000/docs
```

The scripts/start.sh script prints the network URL after server starts.

## Error Handling

- All scripts use `set -e`
- setup/setup.sh uses checkpoint system for idempotency
- scripts/start.sh checks for existing server before starting
- scripts/stop.sh handles missing PID file gracefully
- test.sh reports pass/fail for each test

## Language

All script output and documentation in English regardless of input language.
