# Quick Start

## First-Time Setup

```bash
make all
```

This will:
1. Prompt you to select models (Gemma4, Ornith, or Both)
2. Create a distrobox container
3. Download selected models
4. Compile llama.cpp with Vulkan support
5. Validate models are accessible
6. Test inference on downloaded models
7. Start the server
8. Run live agent test

## Step by Step

```bash
make setup        # Interactive setup with model selection
make setup-test   # Test inference (non-blocking, 20s timeout)
make start        # Start server, print connection info
make server-test  # Live test forcing internet access
make stop         # Stop server
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

## Troubleshooting

- **Server won't start**: Check logs at `~/llm-workspace/.config/server.log`
- **Connection refused**: Ensure server is running with `make start`
- **Inference timeout**: Models may need more time to load, increase `SETUP_TEST_TIMEOUT` in models.sh
- **Build failed**: Run `make clean-cache && make setup` to rebuild from scratch
