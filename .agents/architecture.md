# Architecture

## Layers

Bash orchestrates (`podman`, `systemctl`, `curl`, `yq`). Python parses and
computes (`uv run llmenv.py`). The two communicate over JSON via `jq`.

## Files

| File | Responsibility |
|---|---|
| `Makefile` | Thin dispatcher, no logic beyond 3 lines |
| `tools/lib.sh` | Logging, paths, `require_cmd`, the `llmenv` wrapper |
| `setup/setup.sh` | Interactive configuration, downloads, network exposure |
| `scripts/benchmark.sh` | Vulkan-only measurement with CPU fallback; runs via `llama bench`, a subcommand of `/app/llama` in the image (there is no separate `llama-bench` binary) |
| `scripts/start.sh` | Budget check, device resolution, compose+wrapper-unit render, health gate |
| `pylib/compose.py` | `docker-compose.yml` rendering from `models.yml` |
| `pylib/resources.py` | Host CPU/RAM budgeting for the compose container stack |
| `pylib/omniroute.py` | Idempotent OmniRoute provider-connection provisioning via its admin API |
| `scripts/stop.sh` / `scripts/clean.sh` | Lifecycle |
| `scripts/check-setup.sh` | Offline validation |
| `scripts/check-server.sh` | Online API contract validation |
| `llmenv.py` | CLI dispatcher, JSON out |
| `pylib/agent_runner.py` | Bounded live-agent scopes, stream capture, and cleanup proof |
| `pylib/config.py` | Schema, enable/disable, `models_max` validation and clamping |
| `pylib/gguf.py` | GGUF header parsing, KV geometry |
| `pylib/detect.py` | GPU and compositor detection from sysfs |
| `pylib/budget.py` | VRAM arithmetic and remedies |
| `pylib/presets.py` | `presets.ini` via configparser |

## Container Lifecycle

`setup/render-unit.sh` writes two generated files, both regenerated on
every `make start`:

- `~/.config/llm-env/docker-compose.yml` — the container definition
  (`pylib/compose.py`). Not for hand editing.
- `~/.config/systemd/user/llm-server.service` — a thin systemd wrapper unit
  whose `ExecStart`/`ExecStop` run `podman compose … up -d`/`down`. This is
  the unit `make start`/`stop`/`status`/`logs`/`enable-boot` all operate on
  by name (`llm-server.service`); systemd supervises "is the compose stack
  up," while each compose service's own `restart:` policy handles
  crash-restart.

The wrapper unit is `Type=oneshot`/`RemainAfterExit=yes`, so its own
`journalctl -u llm-server.service` only carries the oneshot invocation's
start/stop lines, not container output, and `systemctl status` only
reflects whether `compose up -d` last succeeded, not live container health.
`scripts/logs.sh` and `scripts/status.sh` use `podman compose logs`/`ps`
against the compose file for real container output and health — use those
directly for the same view: `podman compose -f
~/.config/llm-env/docker-compose.yml logs -f`, `podman compose -f
~/.config/llm-env/docker-compose.yml ps`.

The compose file also runs `omniroute`, network-joined to `llm-server` and
gated on its `service_healthy` condition. `scripts/start.sh` calls `llmenv
omniroute provision` once both containers are reachable, which idempotently
creates or updates a provider connection named `llm-env-local` pointing at
`http://llm-server:<port>/v1` with the router's real API key — see
`pylib/omniroute.py`. To inspect what is actually configured in OmniRoute:

```bash
curl -H "x-omniroute-cli-token: $(yq -r '.omniroute.cli_token' ~/.config/llm-env/models.yml)" \
  http://127.0.0.1:20128/api/providers
```

## Invariants

- `runtime.models_max` is a validated residency limit between 1 and the enabled
  model count; model selection clamps it downward only when necessary.
- `pylib/gguf.py` owns per-layer full/recurrent/SWA geometry; `pylib/budget.py`
  owns Q5_1 byte arithmetic, the 512 MiB per-resident runtime allowance, and the
  independent 1024 MiB spike reserve.
- The target deployment uses one request slot. Automatic fitting, context
  shifting, cache changes, and CPU offload remain disabled.
- Vulkan device indices are never persisted; the PCI address is.
- An infeasible VRAM budget is reported, never silently corrected.
- `presets.ini` must contain no `[DEFAULT]` section and no `version` key;
  `llama-server` treats every INI section as a model preset and would
  register a phantom model.
- Host-side probes use `127.0.0.1`, never `localhost`.
- A failed Vulkan benchmark configures CPU fallback and exits nonzero.
- Live Pi and OpenCode checks enter a random systemd user scope through
  `run-agent-bounded`; Bash consumes only the six-field result JSON and never
  treats unproved cleanup as a model result.
- The `llm-env-local` OmniRoute provider connection is owned by this tool —
  never renamed or deleted by hand, or `make start` will create a duplicate.

## Platform

Linux only. Bazzite/Fedora with podman, running as a rootless compose
stack. There is no macOS support and none is planned.
