# Quick Start

## First-Time Setup

```bash
make setup
```

This will:
1. Prompt you to select a model (Gemma 4 12B or Ornith 1.0 9B)
2. Create a distrobox container
3. Download the selected model
4. Compile llama.cpp with Vulkan support
5. Run a basic inference test

## Start Server

```bash
make start
```

The script will print:
- Local URL: `http://localhost:8000/docs`
- Network URL: `http://<linux-ip>:8000/docs`
- OpenCode configuration

## Connect from macOS

1. Ensure both machines are on the same network
2. Open browser on macOS: `http://<linux-ip>:8000/docs`
3. Or configure OpenCode:
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

## Test Everything

```bash
make test
```

Tests:
1. Server health check
2. curl inference test
3. OpenCode integration test (requires internet)

## Stop Server

```bash
make stop
```

## Switch Models

Edit `~/llm-workspace/.config`:
```
MODEL_ALIAS=gemma4
MODEL_NAME=gemma-4-12B-it-Q4_K_M.gguf
MODEL_URL=https://huggingface.co/bartowski/gemma-4-12B-it-GGUF/resolve/main/gemma-4-12B-it-Q4_K_M.gguf
SERVER_PORT=8000
SERVER_HOST=0.0.0.0
```

Then restart: `make stop && make start`

## Troubleshooting

- **Server won't start**: Check logs at `~/llm-workspace/.config/server.log`
- **Connection refused**: Ensure server is running with `make start`
- **Out of memory**: Only one model fits in 16GB VRAM at a time
- **Build failed**: Run `make clean-cache && make setup` to rebuild from scratch
