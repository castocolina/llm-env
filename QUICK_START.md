# Quick Start Guide - LLM Environment

## Setup (First time)
```bash
cd ~/git/llm-env
make setup-dev
```

The script will prompt you to select a model:
- **Gemma 4 26B-A4B** (~17 GB) - Best quality, MoE architecture. Recommended for 16GB+ VRAM.
- **Ornith 1.0 9B** (~6 GB) - Coding specialist (69.4% SWE-bench). Recommended for 8GB+ VRAM.

Skip the prompt with environment variables:
```bash
MODEL_URL="https://..." MODEL_NAME="model.gguf" make setup-dev
```

---

## Remote Access (macOS to Linux)

If your macOS has only 16GB RAM and you cannot run models locally, access the Linux machine remotely via SSH tunnel.

### Setup (one time)

On your **Linux machine**, ensure the llama-server is running:
```bash
make shell
cd ~/llm-workspace/llama.cpp
./build/bin/llama-server -m ../models/<model>.gguf -ngl 99 --jinja --host 0.0.0.0 --port 8000
```

On your **macOS**, create an SSH tunnel:
```bash
ssh -L 8000:localhost:8000 user@linux-machine-ip
```

Then access the API from macOS at: `http://localhost:8000/docs`

### Alternative: Run inference remotely

```bash
ssh user@linux-machine-ip "cd ~/llm-workspace/llama.cpp && ./build/bin/llama-cli -m ../models/<model>.gguf -ngl 99 --jinja -p 'Your prompt' -n 256 --simple-io"
```

---

## Local Usage (Linux)

### Enter the container
```bash
make shell
# Then inside the container:
cd ~/llm-workspace/llama.cpp
```

### Run inference
```bash
./build/bin/llama-cli \
  -m ../models/<model>.gguf \
  -ngl 99 \
  --jinja \
  -t $(nproc) \
  -p "Your prompt here" \
  -n 256
```

### Start API server (OpenAI-compatible)
```bash
./build/bin/llama-server \
  -m ../models/<model>.gguf \
  -ngl 99 \
  --jinja \
  --host 0.0.0.0 \
  --port 8000
```

Then access at: `http://localhost:8000/docs`

Test call:
```bash
curl http://localhost:8000/v1/completions \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "Write a short greeting",
    "max_tokens": 100
  }'
```

---

## Important Flags

| Flag | Usage |
|------|-------|
| `-m <path>` | Path to model |
| `-ngl 99` | Offload 99 layers to GPU (practically all) |
| `--jinja` | Enable Jinja chat template (required for Gemma 4 and Ornith) |
| `-t $(nproc)` | Use all CPU cores (Linux) |
| `-n 256` | Generate 256 tokens |
| `-p "prompt"` | Initial prompt |
| `--interactive` | Continuous conversation mode |
| `--simple-io` | Simple interface without special characters |

---

## Optimizations

### Ryzen 9900X
- Compiled with `-march=native` (detects native ISA: Zen 5)
- Flags: `-O3 -mtune=native -flto` (Link-Time Optimization)
- Uses all cores automatically

### GPU
| Platform | API | Flag |
|----------|-----|------|
| Linux | Vulkan | `-DGGML_VULKAN=ON` |
| macOS | Metal | `-DGGML_METAL=ON` |

`-ngl 99` offloads all layers to GPU on both platforms.

### Model sizing
| Model | Q4_K Size | Min VRAM | Recommended |
|-------|-----------|----------|-------------|
| Gemma 4 26B-A4B | ~17 GB | 16 GB | 16GB+ VRAM (Linux) |
| Ornith 1.0 9B | ~6 GB | 8 GB | 8GB+ VRAM or 16GB RAM |

---

## Changing the Model

Edit `setup-dev.sh` or pass environment variables:

```bash
# Edit the script: change MODEL_URL and MODEL_NAME variables
# Or use environment variables:
MODEL_URL="https://your-model-url" MODEL_NAME="model-name.gguf" make setup-dev
```

---

## Troubleshooting

### Vulkan permission issue (Linux)
```bash
distrobox enter llm-env
sudo usermod -a -G render $USER
# Exit and re-enter the container
```

### Metal not found (macOS)
```bash
xcode-select --install
# Then re-run make setup-dev
```

### View compilation logs
```bash
# Linux
distrobox enter llm-env
cd ~/llm-workspace/llama.cpp/build
make VERBOSE=1

# macOS
cd ~/llm-workspace/llama.cpp/build
make VERBOSE=1
```

### Clean and rebuild
```bash
make clean-cache
make setup-dev
```

---

## Resources

- [llama.cpp Docs](https://github.com/ggml-org/llama.cpp)
- [Gemma 4 26B](https://huggingface.co/ggml-org/gemma-4-26B-A4B-it-GGUF)
- [Ornith 1.0 9B](https://huggingface.co/deepreinforce-ai/Ornith-1.0-9B-GGUF)
- [GGUF Format](https://github.com/ggerganov/ggml/blob/master/docs/gguf.md)

---

**Last Updated**: 2026-07-21
**Hardware**: Ryzen 9900X | AMD 9070 XT 16GB | 32GB DDR5
