# Quick Start

## First run

```bash
make setup       # 1. pick GPU + models (or opt out of local GPU inference), download, write config
make benchmark   # 2. measure Vulkan throughput; CPU fallback exits nonzero; skipped if GPU inference is disabled
make start       # 3. start
make check-server
```

`make setup` asks "Enable local GPU inference (llm-server)? [Y/n]" — answer
`n` (or set `LLM_ENV_NO_GPU=1` for unattended runs) to skip GPU/model steps
entirely and run this host as an OmniRoute gateway + remote installer only.
See the main [README](README.md#gpu-optional-mode) for details.

## Daily use

```bash
make start
make stop
make status
make logs
```

## Laya request history

With System One enabled, `make start` starts Phoenix alongside Laya. Open
`http://127.0.0.1:6006/projects` and choose the `laya` project (or use the
configured `LLM_ENV_PHOENIX_PORT`). The observer captures real server-side
request and response JSON, including state, questions, typed answers,
probabilities/confidence, known usage, routing, timing, errors, and batch
structure. It does not capture headers and redacts credential-like JSON
fields. History starts at deployment; older calls are not backfilled.

Phoenix is loopback-only and has no authentication. Other users/processes on
the same machine can access the traces. The dashboard may contain private
prompts, state, and outputs. Each direction is capped at 24,000 captured
bytes; each exported record is capped at 64 KiB. A bounded queue holds up to
128 records and export runs in the background with a 1.5-second request
timeout. Unavailable Phoenix, oversized records, or a full queue can cause
loss; drops are logged. The default seven-day retention policy is configured
with `LLM_ENV_PHOENIX_RETENTION_DAYS`; it is scheduled cleanup, not a quota or
deletion-time guarantee. Normal `make stop` retains the data
volume. Phoenix has 256 MiB soft memory, 1 GiB hard memory, 256 relative CPU
shares, and a 2 CPU hard cap; these are policy limits, not a capacity test.
Confidence is not correctness, native charts are not guaranteed for every
field, and observer latency overhead has not been measured. Phoenix telemetry
and off-host export are disabled. See [README](README.md#laya-request-history-in-phoenix)
for the privacy boundary and browser-test discovery settings.

## Start at boot

```bash
make enable-boot     # enable lingering and render the wrapper unit's [Install] section
make disable-boot
```

## Updates

`make update-omniroute` is a safety deferral, not an updater: no automatic
update is currently qualified for this native dependency-bearing deployment.
It does not evaluate or claim completion of the idle policy or image
qualification, and performs no lifecycle or registry mutation. Enabling updates
requires an explicit architecture decision for a safe isolated replacement
path.

## OmniRoute dashboard

`make start` auto-provisions OmniRoute's connection to the local router
(skipped when GPU inference is disabled — configure an upstream provider
through the dashboard yourself in that mode). The dashboard itself is at
`http://127.0.0.1:20128` (or your configured `omniroute.port`); the login
password is `omniroute.initial_password` in `~/.config/llm-env/models.yml`.

## Remote setup (other machines on the LAN)

```bash
make show-secrets   # or `make status` after `make start`
```

Prints the exact one-liner for another machine to configure its own
Pi/OpenCode sessions against this host's OmniRoute gateway:

```bash
curl -sS http://<this-host>.local:<remote-setup-port>/setup.sh | bash
```

It prompts for `OMNI_ROUTER_MASTER_KEY` (from this repo's `.env`) and never
hands out the master key itself.

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

## Coding clients

After `make start`, configure normal Pi and OpenCode sessions once:

```bash
make setup-local-llm-agents
```

The command reads the private API key and enabled models from `models.yml`,
checks that OmniRoute is healthy, and creates or fully refreshes the
`router-env` provider in both clients' normal profiles. `router-env` points at
OmniRoute (`http://127.0.0.1:<omniroute.port>/v1`), not llm-server directly --
OmniRoute is the router: it can dispatch a request to this machine's own
llm-server or to any other provider/combo you've configured in it, using
priority and quota-aware strategies, not "local models only". It preserves
all other providers and settings and writes each updated file with mode
`0600`. It never prints the key. Run it again after rotating the key,
changing the port, or enabling or disabling a model.

Clean setup maps `gemma4` to yuxinlu1's Agentic Gemma 4 12B v2 Q4_K_M
build. The model and client records use an exact 131,072-token context and
8,192-token output limit. Pi's global `enabledModels` becomes exactly the setup-selected
local aliases in setup order, which defines its model cycle.
OpenCode favorites start with the setup-selected local aliases in setup order;
stale local favorites are removed, while unrelated favorites retain their
order. The command leaves explicit client defaults unchanged.

Close Pi and OpenCode before running the command. Restart both clients after the
command succeeds. If a replacement fails partway, keep Pi and OpenCode closed,
rerun `make setup-local-llm-agents`, and restart both clients only after it
succeeds. The deployment uses one request slot with Q5_1 K/V caches,
`fit = off`, and `context-shift = off`. Reserving all 8,192 output tokens leaves
a nominal 122,880 tokens for prompt and history. It never silently reduces
context or offloads model layers to the CPU.

The measured llama.cpp build applies a strict admission rule: post-template
prompt tokens must be less than `n_ctx`. With one 131,072-token slot, 131,071
is admitted with `max_tokens: 1`; 131,072 and above are rejected. The clients
still use a configured 131,072-token context and the nominal 122,880-token
prompt/history allowance described above.

OpenCode `1.18.10` stores model state at
`${XDG_STATE_HOME:-$HOME/.local/state}/opencode/model.json` with the shape
`{recent: ModelRef[], favorite: ModelRef[], variant: object}`. The command
updates `favorite` and preserves `recent` and `variant`.

Separately, OpenCode's global provider configuration merges
`${XDG_CONFIG_HOME:-$HOME/.config}/opencode/config.json`, `opencode.json`, and
`opencode.jsonc` in that order. The command validates every existing file
before writing. It replaces `router-env` in every file that defines the
provider. If none defines it, the command adds it to the preferred existing
file: `opencode.jsonc`, then `opencode.json`, then `config.json`. It creates
`opencode.jsonc` only when none of those files exists.

List the enabled aliases before choosing models:

```bash
yq -r '.models[] | select(.enabled) | .alias' ~/.config/llm-env/models.yml
```

Replace the placeholders below with enabled aliases:

```bash
pi --model router-env/<alias>
pi --models 'router-env/<alias>,router-env/<another-enabled-alias>'
opencode --model router-env/<alias>
```

`make check-with-agents` continues to use its own temporary isolated client
configurations. It does not modify normal Pi or OpenCode profiles.

## Changing models

```bash
uv run llmenv.py models list
uv run llmenv.py models enable gemma4
make restart
```

## When something breaks

```bash
make check-setup                         # offline config, GPU, image, model, and inference records
make check-server                        # local deterministic OpenAI-compatible API records
LLM_ENV_KEEP_CHECK_ARTIFACTS=1 make check-with-agents  # Pi/OpenCode live weather and USD-to-CLP checks
make logs
```

Checks print their command, validation facts, and verdict. They omit empty
diagnostic blocks; non-empty stderr and parser errors remain visible and
redacted. Displayed diagnostics are bounded, redacted excerpts. Explicitly
retained private artifacts are redacted before retention.
`LLM_ENV_KEEP_CHECK_ARTIFACTS=1` retains only the redacted private
diagnostic artifacts; without it, the checks remove raw artifacts after
displaying bounded, redacted excerpts.

`make check-server` uses the fixed local prompt `Reply with exactly: ready`.
`make check-with-agents` is an opt-in live check: Pi and OpenCode independently
fetch public weather and USD-to-CLP data. Its successful rows show the selected
client/model, isolated configuration summary, redacted command, final response,
validation facts, and a final passed/failed count. It compares their evidence
with a fresh source snapshot.
