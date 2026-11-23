# Quick Start

## First run

```bash
make setup       # 1. pick GPU + models (or opt out of local GPU inference), download, write config
make benchmark   # 2. measure Vulkan throughput (see the note below); CPU fallback exits nonzero; skipped if GPU inference is disabled
make start       # 3. start whatever is not running
make check-server
```

`make setup` asks "Enable local GPU inference (llm-server)? [Y/n]" — answer
`n` (or set `LLM_ENV_NO_GPU=1` for unattended runs) to skip GPU/model steps
entirely and run this host as an OmniRoute gateway + remote installer only.
See the main [README](README.md#gpu-optional-mode) for details.

`make benchmark` rewrites `gpu.backend` and `gpu.image` in the live `models.yml`
when the Vulkan benchmark fails, and gives contended results while llm-server holds
the GPU. Run it only after `make stop-llama`. Then take a fresh timestamped backup right before the benchmark: `cp -p ~/.config/llm-env/models.yml ~/.config/llm-env/models.yml.$(date +%Y%m%dT%H%M%S).bak`. Do not reuse the backup `make llama-enable` took earlier: it may be stale, and restoring it would undo the enable edit,
and check that
`yq '.gpu.backend, .gpu.image' ~/.config/llm-env/models.yml` is unchanged. If it is
not, restore the backup and report it. See the
[README](README.md#local-inference-toggle-and-model-swap) for details.

## Daily use

```bash
make start        # start whatever is not running; never stops a running container
make stop
make restart      # stop everything, re-render and start (applies config changes)
make start-llama  # start llm-server only, after make llama-enable
make stop-llama   # stop llm-server only; OmniRoute stays up
make status
make logs
```

## Laya from other machines

By default Laya (System One) answers on `127.0.0.1:8010` only. To reach it from the LAN
(`http://llm.local:8010`), set `system_one.listen: lan` in `models.yml`, run
`bash setup/render-unit.sh`, then `make update-system-one` (it recreates only
`system-one`, once Laya is idle). **Laya has no authentication: with `lan`,
anyone who can reach port 8010 can use it.** `listen: loopback` reverts it.

## Laya request telemetry

Laya writes privacy-filtered request records (no question or answer text) to
`~/.local/state/llm-env/laya-spans/laya-spans.jsonl`, bounded to the current
file and one rotation. The host observe collector reads the file and publishes
request counts, durations, answer types, and confidence histograms to Grafana.
Earlier history is not backfilled.

## Start at boot

```bash
make enable-boot     # enable lingering and render the wrapper unit's [Install] section
make disable-boot
```

## Updates

`make update-omniroute` replaces the OmniRoute container only when the
configured image tag resolves to a different digest than the running image,
the dashboard has been idle for `LLM_ENV_UPDATE_IDLE_SECONDS` (default 300)
within `LLM_ENV_UPDATE_MAX_WAIT_SECONDS` (default 10800), a reviewed activity
profile exists for the running image (otherwise it defers with `capability`),
and every pre-commit inventory/fingerprint recheck passes. When OmniRoute is
stopped or absent it skips the idle wait and only pulls, recreates and
verifies `omniroute` (so a stopped OmniRoute ends up **running** on the new
image); a running container that Podman marks `unhealthy` is accepted only if
its API answers (otherwise `deferred: health`). Reviewed profiles exist for
`:latest` and `:next`. It prints `updated <old> -> <new>`, `unchanged`, or
`deferred: <reason>` (exit 75; `make` reports it as `Error 75` and exits 2, so
scripts call `bash scripts/update-omniroute.sh`); a failure exits 1 with the
stage that failed and **no rollback is attempted**. Reasons and the client
routes that count as activity are listed in the README ("OmniRoute updates").
It is not zero-downtime and observation is not admission control.

`make migrate-remote-setup` recreates the remote-setup container without its
native OmniRoute dependency. It refuses to run until the deployed compose is
re-rendered without the remote-setup `depends_on` (re-run `make setup`; it
device-probes with `podman run --rm` and builds the
observer image when System One is enabled, stops a legacy `llm-mdns.service`
and runs `systemctl --user daemon-reload`). While remote-setup is down,
`/config` answers 503 + `Retry-After: 5` instead of hanging.

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

The command reads the private API key from `models.yml` and the current combos from
OmniRoute's API, checks that OmniRoute is healthy, and creates or fully refreshes the
`router-env` provider in both clients' normal profiles. `router-env` points at
OmniRoute (`http://127.0.0.1:<omniroute.port>/v1`), not llm-server directly --
OmniRoute is the router: it can dispatch a request to this machine's own
llm-server or to any other provider/combo you've configured in it, using
priority and quota-aware strategies, not "local models only". It preserves
all other providers and settings and writes each updated file with mode
`0600`. It never prints the key or writes it into the Pi or OpenCode
configs: both reference the private key file `~/.config/router-env/api-key`
(`0600`). Run it again after rotating the key or
changing the port; combo changes are picked up on every run.

Clean setup maps `gemma4` to yuxinlu1's Agentic Gemma 4 12B v2 Q4_K_M
build. Combo records carry the context window OmniRoute computes for them. Pi's global `enabledModels` starts with the
combos in OmniRoute's order, which defines its model cycle; stale router-env entries are
removed, while other enabled models keep their order.
OpenCode favorites start with the same combos in the same order;
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
make stop-llama && make llama-enable MODEL=gemma4 && make start-llama
```

To keep several models enabled at once, use `uv run llmenv.py models select <a> <b>`
and then `make restart`. Model swaps and the toggle are explained in the
[README](README.md#local-inference-toggle-and-model-swap).

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
