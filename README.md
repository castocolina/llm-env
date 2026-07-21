# LLM Environment - Bazzite + AMD GPU

Automated setup to compile and run **llama.cpp** on Bazzite with Vulkan GPU support and hardware optimizations.

## Quick Start

```bash
make setup-dev
```

Prompts for model selection (Gemma 4 26B or Ornith 9B), downloads the model, compiles llama.cpp with Vulkan, and runs a test inference. Each step is cached - if interrupted, re-running picks up where it left off.

## Commands

```bash
make setup-dev        # Set up / complete the environment
make shell            # Enter the Distrobox container
make cache-status     # View checkpoint status
make clean-cache      # Remove checkpoints (forces rebuild)
make clean            # Remove the entire environment
make validate         # Run shellcheck on all .sh files
```

## Remote Access (macOS to Linux)

Run models on your Linux machine, access from macOS via SSH tunnel:

```bash
# On Linux: start the server
make shell
cd ~/llm-workspace/llama.cpp
./build/bin/llama-server -m ../models/<model>.gguf -ngl 99 --jinja --host 0.0.0.0 --port 8000

# On macOS: tunnel to it
ssh -L 8000:localhost:8000 user@linux-machine-ip

# Then open http://localhost:8000/docs on macOS
```

## Using Locally (Linux)

```bash
make shell
cd ~/llm-workspace/llama.cpp
./build/bin/llama-cli -m ../models/<model>.gguf -ngl 99 --jinja -t $(nproc) -p "Your prompt" -n 256
```

Key flags: `-ngl 99` (all layers to GPU), `--jinja` (chat template, required), `--simple-io` (clean output).

## Customization

```bash
# Skip model selection prompt
MODEL_URL="https://huggingface.co/..." MODEL_NAME="model.gguf" make setup-dev

# Change Fedora version
FEDORA_VERSION="45" make setup-dev
```

## Model Sizing

| Model | Q4_K Size | Min VRAM | Best For |
|-------|-----------|----------|----------|
| Gemma 4 26B-A4B | ~17 GB | 16 GB | Best quality, reasoning |
| Ornith 1.0 9B | ~6 GB | 8 GB | Coding (69.4% SWE-bench) |

## Optimizations

- **CPU**: `-march=native -O3 -flto` for Zen 5, all cores for compilation
- **GPU**: `-DGGML_VULKAN=ON` (Linux) / `-DGGML_METAL=ON` (macOS), full layer offload via `-ngl 99`

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Container won't create | `distrobox create -n llm-env --image fedora:44 --no-entry` |
| Model download error | Delete the file in `~/llm-workspace/models/` and re-run |
| Vulkan permission issue | `sudo usermod -a -G render $USER` then re-enter container |
| Metal not found (macOS) | `xcode-select --install` |
| View build logs | `cd ~/llm-workspace/llama.cpp/build && make VERBOSE=1` |

## Resources

- [llama.cpp](https://github.com/ggml-org/llama.cpp)
- [Gemma 4 26B](https://huggingface.co/ggml-org/gemma-4-26B-A4B-it-GGUF)
- [Ornith 1.0 9B](https://huggingface.co/deepreinforce-ai/Ornith-1.0-9B-GGUF)
- [GGUF Format](https://github.com/ggerganov/ggml/blob/master/docs/gguf.md)

---

**Last reviewed**: 2026-07-21
