# llm-env

Local llama.cpp router server with GPU acceleration, running as a rootless
podman quadlet on Bazzite.

## Quick start

```bash
make prerequisites  # confirm and install Bazzite/Fedora host tools
make setup       # choose GPU and models, download, generate config
make benchmark   # measure Vulkan throughput; CPU fallback still reports a failed Vulkan benchmark
make start       # start the server
make check-server
```

## What it does

- Serves multiple models from one endpoint using llama.cpp router mode.
  Clients pick a model with the `model` field.
- Detects your GPU, VRAM, and compositor usage, then computes a VRAM budget
  and refuses to start a configuration that cannot fit.
- Uses a Vulkan-only benchmark and records tokens/sec in `models.yml`. If the
  Vulkan benchmark fails, it configures CPU fallback and exits nonzero.
- Runs as a systemd user service. Manual by default; `make enable-boot`
  makes it start with the machine.
- Exposes an OpenAI-compatible API on the LAN with an API key and an
  mDNS `.local` name.

## Configuration

Everything lives in `~/.config/llm-env/models.yml`. Models are enabled and
disabled with a flag, so nothing is lost when you stop using one:

```bash
uv run llmenv.py models list
uv run llmenv.py models disable ornith
uv run llmenv.py models enable openhermes
```

`models_max` always follows the number of enabled models.

## Requirements

Bazzite or Fedora. Run `make prerequisites` to detect and, after explicit
confirmation, install uv, jq, Mike Farah yq v4, podman, curl, iproute, git,
and ShellCheck. No compiler needed — the llama.cpp server comes from a
prebuilt image.

## Commands

Run `make help`.
