# Quick Start

## First-Time Setup

```bash
make setup
```

This will:
1. Create a distrobox container
2. Download both models (Gemma4 + Ornith)
3. Generate presets.ini for router mode
4. Compile llama.cpp with Vulkan support
5. Run inference tests on both models

## Start Server

```bash
make start
```

Router mode serves both models from a single server. The script will print:
- Local URL: `http://localhost:8000/docs`
- Network URL: `http://<linux-ip>:8000/docs`

## Usage

Select model via `model` field in API requests:

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

## Test Everything

```bash
make test
```

Tests:
1. Server health check
2. Model list (verifies both models available)
3. Gemma4 inference test
4. Ornith inference test
5. OpenCode integration test (optional)

## Stop Server

```bash
make stop
```

## Troubleshooting

- **Server won't start**: Check logs at `~/llm-workspace/.config/server.log`
- **Connection refused**: Ensure server is running with `make start`
- **Out of memory**: Models are evicted automatically (LRU), but ensure sufficient VRAM
- **Build failed**: Run `make clean-cache && make setup` to rebuild from scratch
