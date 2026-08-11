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
`http://llm-server:<port>/v1` (`llm-server` is the compose service's
`container_name`, which is also its DNS name on the shared podman network)
with the router's real API key — see `pylib/omniroute.py`. OmniRoute's
management API (`/api/providers`) accepts only a dashboard session or a
Bearer key with `manage` scope; there is no CLI-token header, so
provisioning logs in with the dashboard password (`POST /api/auth/login`)
and reuses the resulting `auth_token` session cookie for subsequent
requests. To inspect what is actually configured in OmniRoute:

```bash
pw="$(yq -r '.omniroute.initial_password' ~/.config/llm-env/models.yml)"
curl -c /tmp/omni-cookies -X POST -H "Content-Type: application/json" \
  -d "{\"password\":\"${pw}\"}" http://127.0.0.1:20128/api/auth/login
curl -b /tmp/omni-cookies http://127.0.0.1:20128/api/providers
```

### Topology and what survives a restart

```
                 podman-compose default network (compose project network)
                 ┌──────────────────────────────────────────────────┐
                 │                                                  │
 host:8000 ─────▶│  llm-server  ◀───DNS "llm-server"────  omniroute │◀──── host:127.0.0.1:20128
 (LLAMA_ARG_PORT) │  (llama.cpp)                          (router)  │      (dashboard + /v1/*)
                 │      ▲                                    ▲     │
                 └──────┼────────────────────────────────────┼─────┘
                        │                                    │
              bind mount (ro)                        named volume
       ~/llm-workspace/models  :/models              omniroute-data:/app/data
       ~/.config/llm-env/presets.ini                 (dashboard password, provider
       (host directories -- not podman-managed)        connections, everything you
                                                        configure in the dashboard)
```

See `docker-compose.yml.example` for the full annotated compose shape this
diagram summarizes (checked-in, static, NOT read by any script -- purely a
reference; the real file is generated, see below).

**What survives what:**

| Event | `llm-server`/`omniroute` containers | `omniroute-data` volume | bind-mounted models/presets |
| --- | --- | --- | --- |
| `podman kill`/crash, then `restart:` policy relaunches | recreated | untouched | untouched (host files) |
| `make stop` / `systemctl --user stop llm-server.service` (`podman compose down`, no `-v`) | removed, volume detached | **kept** | untouched |
| `make start` (`podman compose up -d` again) | recreated | reattached, same data | untouched |
| host reboot (`start_at_boot` unit, or manual `make start` after) | recreated | **kept** | untouched |
| `make clean` (`podman compose down -v`) | removed | **destroyed** — explicitly warned before confirming | untouched (models are never deleted) |

In short: anything you configure by hand in the OmniRoute dashboard (extra
provider connections, settings) lives in the `omniroute-data` named volume
and survives every normal lifecycle operation — stopping, starting,
rebooting the host, or the container crashing and being restarted. It is
only lost if you run `make clean`, or manually `podman volume rm
omniroute-data` / `podman compose down -v`.

To provision more than the one `llm-env-local` connection this repo sets up
automatically, either add it by hand in the dashboard (persists in the
volume the same way), or extend `llmenv.py omniroute provision` /
`pylib/omniroute.py` — today it only idempotently creates/updates that one
connection; generalizing it to read a list of desired connections from
`models.yml` would make additional provisioning reproducible instead of
manual.

**Inspecting the generated compose file:** `setup/render-unit.sh` writes the
real, live compose file to `~/.config/llm-env/docker-compose.yml` on every
`make start`, and also copies that exact render to `./tmp/docker-compose.yml`
(gitignored) so it can be inspected or diffed from inside the repo without
touching `~/.config`. `docker-compose.yml.example` above is the static,
annotated counterpart for reading without running anything.

Three OmniRoute API shapes are undocumented and were only confirmed by
capturing the dashboard UI's own network traffic while adding a connection
by hand (`Add Connection` on a provider's page, e.g.
`/dashboard/providers/llama-cpp`):

- A connection's outbound URL lives at `providerSpecificData.baseUrl`, not
  a top-level `url`/`baseUrl` key. A top-level key is silently accepted by
  `POST /api/providers` (`201 Created`) and even passes the separate
  `POST /api/providers/validate` syntax check, but is never persisted —
  `GET /api/providers` afterward shows no URL at all.
- `/v1/*` inference routes (`/v1/chat/completions`, `/v1/models`) accept
  the same dashboard password as a plain `Authorization: Bearer <password>`
  header — no separate API key needs to be minted via `POST /api/keys`.
- Routing keys on the **provider slug** (e.g. `llama-cpp`), not the
  connection's own `name`. A request for model `llm-env-local/<alias>`
  404s with `model_not_found`; the correct model ID is
  `llama-cpp/<alias>`, confirmed via `GET /v1/models`.

### check-server.sh's failure isolation

`check-server.sh` runs its checks in a fixed, dependency-aware order so a
FAIL points at one layer, not several:

1. **Health** — if `/health` doesn't respond, the whole script exits
   immediately (nothing downstream can possibly pass either).
2. **Authentication**, **Model listing**, **Completions** — probe
   `llm-server` directly (bypassing OmniRoute), per enabled model alias.
3. **OmniRoute login**, **OmniRoute providers** — if login fails,
   "OmniRoute providers" is reported as a skip, not re-attempted.
4. **OmniRoute completions** — per alias, but only for aliases whose
   direct **Completions** check (step 2) already passed. If a model's
   direct completion already failed, the OmniRoute completion for that
   same alias is a `SKIP`, not a second, redundant `FAIL` — OmniRoute
   proxies to the same `llm-server`, so re-testing a model already known
   to be broken would just print a second symptom of the same root cause.

Net effect: a `FAIL` under "Completions" (not "OmniRoute completions")
means the model or `llm-server` itself is broken, independent of
OmniRoute. A `FAIL` under "OmniRoute completions" (with the matching
"Completions" row showing PASS) means the model and `llm-server` are fine
and the problem is specifically in OmniRoute's routing/provider config.

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
- `resources.llm_server.memory_ceiling_pct` (default 46) caps `llm-server`'s
  RAM at that percent of total host RAM, floored at
  `memory_ceiling_floor_pct` (default 30%, also of total host RAM) so a
  tiny configured percentage can never round the cap down to something
  `pylib/compose.py` treats as "no limit" — computed live by
  `compute_resource_limits()` on every `llmenv resources` call.
- `resources.llm_server.cpu_ceiling_pct` (default 60%) caps how many host
  CPU cores `llm-server` can use, the same way `memory_ceiling_pct` caps
  RAM, floored at `cpu_ceiling_floor_pct` (default 20%) — added because MoE
  CPU-offload inference genuinely uses multiple cores (measured ~44% of all
  threads sustained), unlike GPU-resident dense models which barely touch
  the CPU. Reaches `models.yml` the same way `memory_ceiling_pct` does:
  `llmenv resources` computes it live, and `make setup` persists the result
  into `resources.llm_server.cpus`, which `pylib/compose.py` writes into the
  container's `cpus:` limit.
- MoE models (`n_cpu_moe` set on a model entry) split their weight cost
  between VRAM and host RAM: `pylib/gguf.py::moe_expert_offload_mib()`
  reads the GGUF's own tensor byte offsets to compute exactly how many MiB
  of routed-expert tensors `--n-cpu-moe` sends to CPU for that model's first
  `n_cpu_moe` transformer blocks (confirmed empirically that `--n-cpu-moe N`
  offloads ascending block indices, not descending). `llmenv budget`
  cross-checks the sum of the top `runtime.models_max` such requirements,
  ranked by RAM cost across every enabled model (not just the ones
  compute_budget() would rank highest by VRAM cost, and not just the single
  largest — this plan's shipped default keeps `models_max: 1`, where the sum
  reduces to exactly the largest, but a config with `models_max > 1` sums
  every concurrently-resident model's requirement)
  against `resources.llm_server.memory_mib`, and reports the same kind of
  explicit, remedied infeasibility as the existing VRAM check — never
  silently corrected. `n_cpu_moe: 0` on a model with no routed experts at
  all is still rejected explicitly, not silently ignored.
- `gpu.vram_budget_ceiling_mib` caps VRAM planning at
  `gpu.vram_budget_ceiling_pct` (default 95%) of the dGPU's total VRAM,
  floored at `vram_budget_ceiling_floor_pct` (default 30%, also of total)
  — a resolved snapshot computed once by `make setup`, not re-measured on
  every `make start`. This is a stable hardware safety margin, independent
  of what else is using the GPU at any given moment; live VRAM contention
  (the desktop compositor, other GPU clients) is accounted for separately,
  at budget-computation time, by `compute_budget()`'s `reserve`.
- `make gpu-status` is informational only — it never runs automatically and
  never gates `make start`. Any remediation it applies is opt-in via an
  explicit `[y/N]` confirmation, and is limited to XDG per-user overrides
  (`~/.local/share/applications/*.desktop`, `~/.config/environment.d/`) —
  it never modifies a system file, and never touches an already-running
  process (only future launches of the same app are affected). Flatpak-
  installed applications are a known, out-of-scope limitation: their
  `.desktop` files live under `/var/lib/flatpak/exports/share/applications`,
  outside the directories this tool searches, and even if found, its
  `DRI_PRIME` override prefix does not propagate into the Flatpak sandbox
  (`Exec=` there invokes `flatpak run ...`).

## Platform

Linux only. Bazzite/Fedora with podman, running as a rootless compose
stack. There is no macOS support and none is planned.
