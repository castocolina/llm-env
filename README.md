# llm-env

Local llama.cpp router server with GPU acceleration, running as a rootless
podman quadlet on Bazzite.

## Quick start

```bash
make setup       # choose GPU and models, download, generate config
make benchmark   # measure Vulkan vs ROCm, record the winner
make start       # start the server
make check-server
```

## What it does

- Serves multiple models from one endpoint using llama.cpp router mode.
  Clients pick a model with the `model` field.
- Detects your GPU, VRAM, and compositor usage, then computes a VRAM budget
  and refuses to start a configuration that cannot fit.
- Benchmarks Vulkan against ROCm and records tokens/sec in `models.yml`.
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

Bazzite or Fedora, podman, uv, jq, yq, curl. No compiler needed — the
llama.cpp server comes from a prebuilt image.

## Commands

Run `make help`.
