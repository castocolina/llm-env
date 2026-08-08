# llm-env

Local llama.cpp router server with GPU acceleration, running as a rootless
podman compose stack on Bazzite.

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
- Runs an OmniRoute gateway alongside the router and auto-configures its
  connection to the local model on every start — no manual dashboard setup.

## Configuration

Everything lives in `~/.config/llm-env/models.yml`. Models are enabled and
disabled with a flag, so nothing is lost when you stop using one:

```bash
uv run llmenv.py models list
uv run llmenv.py models disable ornith
uv run llmenv.py models enable openhermes
```

Enabled models are all routable. `runtime.models_max` is a separate residency
limit; the target configuration keeps one model resident and unloads it before
loading another alias. This adds model-switch latency but avoids holding both
models in VRAM.

Clean setup maps `gemma4` to yuxinlu1's Agentic Gemma 4 12B v2 Q4_K_M
build. Gemma and Ornith each receive one 131,072-token context and request slot
with Q5_1 K/V caches. Pi and OpenCode advertise up to 8,192 output tokens,
so reserving the full output allowance leaves a nominal 122,880 tokens for the
prompt and history. All tokens still share the same slot. Setup reports an
explicit VRAM-budget failure instead of shrinking context or offloading layers.

The measured llama.cpp build applies a strict admission rule: post-template
prompt tokens must be less than `n_ctx`. With one 131,072-token slot, 131,071
is admitted with `max_tokens: 1`; 131,072 and above are rejected. This
practical boundary does not change the configured 131,072-token client context.

## Clients

After `make start`, configure normal Pi and OpenCode sessions once:

```bash
make setup-local-llm-agents
```

The command reads the private API key and enabled models from `models.yml`,
checks that the local server is healthy, and writes private client
configuration. It never prints the key. Run it again after rotating the key,
changing the port, or enabling or disabling a model.

`QUICK_START.md` includes copy-paste `curl`, Pi, and OpenCode client examples.
The `make check-with-agents` output masks the key and its temporary isolated
configuration paths, but shows the command flags, selected model, prompt, final
response, validation facts, and result for each check.

## Requirements

Bazzite or Fedora. Run `make prerequisites` to detect and, after explicit
confirmation, install uv, jq, Mike Farah yq v4, podman, curl, iproute, git,
Node.js, and ShellCheck. No compiler needed — the llama.cpp server comes from a
prebuilt image.

## Commands

Run `make help`.
