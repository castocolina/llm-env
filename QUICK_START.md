# Quick Start

## First run

```bash
make setup       # 1. pick GPU + models, download, write config
make benchmark   # 2. measure Vulkan throughput; CPU fallback exits nonzero
make start       # 3. start
make check-server
```

## Daily use

```bash
make start
make stop
make status
make logs
```

## Start at boot

```bash
make enable-boot     # enable lingering and render the quadlet [Install] section
make disable-boot
```

## Using it

The config file (`~/.config/llm-env/models.yml`) is mode 600 because it
holds the API key.

From the same machine, use `127.0.0.1` — `localhost` resolves to `::1`
first on this system, and podman publishes the port on `0.0.0.0` (IPv4
only), so `localhost` never connects:

```bash
curl http://127.0.0.1:8000/v1/chat/completions \
  -H "Authorization: Bearer $(yq -r .server.api_key ~/.config/llm-env/models.yml)" \
  -H "Content-Type: application/json" \
  -d '{"model":"gemma4","messages":[{"role":"user","content":"hello"}]}'
```

From another machine on the LAN, use the mDNS name instead:

```bash
curl http://llm.local:8000/v1/chat/completions \
  -H "Authorization: Bearer <api_key>" \
  -H "Content-Type: application/json" \
  -d '{"model":"gemma4","messages":[{"role":"user","content":"hello"}]}'
```

## Changing models

```bash
uv run llmenv.py models list
uv run llmenv.py models enable openhermes
make restart
```

## When something breaks

```bash
make check-setup                         # offline config, GPU, image, model, and inference records
make check-server                        # local deterministic OpenAI-compatible API records
LLM_ENV_KEEP_CHECK_ARTIFACTS=1 make check-with-agents  # Pi/OpenCode live weather and USD-to-CLP checks
make logs
```

Every check prints its redacted command, input, stdout, stderr, parsed value,
expectation, and verdict. `LLM_ENV_KEEP_CHECK_ARTIFACTS=1` retains only the
redacted private diagnostic artifacts; without it, the checks remove them
after printing their contents.

`make check-server` uses the fixed local prompt `Reply with exactly: ready`.
`make check-with-agents` is an opt-in live check: Pi and OpenCode independently
fetch public weather and USD-to-CLP data, then compare their evidence with a
fresh source snapshot.
