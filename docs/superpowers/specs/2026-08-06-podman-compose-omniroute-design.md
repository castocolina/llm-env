# Declarative Container Definitions and OmniRoute Integration Design

## Goal

Replace the bash-heredoc-generated Quadlet unit with a versioned, templated
`podman compose` definition covering both the llama.cpp router and a new
OmniRoute gateway service, pre-configured against the local router on every
start. Systemd stays the boot/lifecycle supervisor; only how the containers
themselves are declared changes.

## Scope

This change covers:

- A `podman-compose` host prerequisite (the provider `podman compose`
  currently has none of on the reference machine)
- A checked-in compose template rendered into
  `~/.config/llm-env/docker-compose.yml`
- A thin systemd wrapper `.service` unit that replaces today's Quadlet
  `.container` unit as the thing `make start`/`stop`/`status`/`logs`/
  `enable-boot` operate on
- A new `omniroute` compose service, network-joined to `llm-server`, gated
  on the router's health before it starts
- A `pylib/compose.py` renderer and a `pylib/omniroute.py` +
  `llmenv.py omniroute provision` subcommand that idempotently configures
  OmniRoute's connection to the local router via its admin API
- A new `omniroute:` section in the `models.yml` schema (schema version
  bump, handled by the existing `migrate-config` mechanism)
- CPU/memory limits on both services, sized from detected host resources
- A `podman compose config` syntax check added to `check-setup`; an
  OmniRoute functional check added to `check-server`
- Documentation stating exactly which files get generated, where, and what
  to run to troubleshoot each layer

This change does not cover:

- Docker support (podman only, matching the rest of this repo)
- Making OmniRoute optional — it is always part of the stack
- Any change to `setup/network.sh` (firewall/mDNS) or GPU/model selection
- Multi-host or remote deployment

## Current State

`setup/render-unit.sh` builds `~/.config/containers/systemd/llm-server.container`
as a bash heredoc at render time (`render-unit.sh:87-116`). The container
definition therefore only ever exists transiently on disk after a render;
nothing describing "what does this container look like" is checked into
git. Quadlet's systemd-generator integration (`systemctl --user
daemon-reload` turning that file into `llm-server.service`) is what gives
today's boot integration, `journalctl --user -u llm-server`, and the
healthcheck-gated `BindsTo=`/`After=` relationship with the mDNS sidecar
unit — all systemd primitives, not something built on top.

## Compose Service Definitions

A new template, `setup/templates/docker-compose.yml.tmpl`, declares two
services with `${VAR}`-style placeholders for values resolved at render
time (image, resolved GPU device, port, API key, resource limits, OmniRoute
token). `pylib/compose.py` renders it into
`~/.config/llm-env/docker-compose.yml`, the same way `pylib/presets.py`
already renders `presets.ini` — a `HEADER_COMMENT` marks it generated,
not for hand editing.

`llm-server`:

- Same image, device mapping, volumes, port publish, environment, and
  `healthcheck:` (translated from today's `HealthCmd=curl -fsS
  http://127.0.0.1:${port}/health`) as the current Quadlet unit.
- `restart: on-failure`, matching today's `Restart=on-failure` in
  `render-unit.sh:113`.

`omniroute`:

- `image: docker.io/diegosouzapw/omniroute:latest` (pinned via
  `omniroute.image` in config, same override pattern as `gpu.image`).
- `ports: ["${OMNIROUTE_PORT}:${OMNIROUTE_PORT}"]`, default 20128, matching
  the user's original manual `podman run`.
- `volumes: ["omniroute-data:/app/data"]` — a named volume, so the SQLite
  connection database survives restarts. This is why provisioning must be
  idempotent (see below): the connection persists, so re-running `make
  start` must not create a duplicate.
- `environment`: `OMNIROUTE_CLI_TOKEN`, `OMNIROUTE_ALLOW_PRIVATE_PROVIDER_URLS=true`
  (required — OmniRoute's SSRF guard blocks private-IP backends otherwise),
  `INITIAL_PASSWORD` (for the human-facing dashboard login; unrelated to
  scripted provisioning, which never uses it).
- `stop_grace_period: 40s`, matching the `--stop-timeout 40` the user
  already used manually.
- `depends_on: llm-server: condition: service_healthy` — OmniRoute's model
  auto-discovery (it calls the backend's `/v1/models` itself; there is no
  `models` field on provider creation) only succeeds if the router is
  already answering when OmniRoute starts.
- `restart: unless-stopped`, matching the user's original manual run.

## Networking

Both services join compose's default network. OmniRoute's provider `url`
must address `llm-server` by **service name** (`http://llm-server:${port}/v1`),
not `127.0.0.1` — confirmed from OmniRoute's own documentation, which
explicitly warns that container-to-container calls need the service name or
host network, not localhost.

## Systemd Wrapper

A plain systemd user unit, `~/.config/systemd/user/llm-server.service`
(same path convention as today's `llm-server-mdns.service`), replaces the
Quadlet-generated unit:

```
[Unit]
Description=llm-env compose stack (llm-server, omniroute)
After=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=%h/.config/llm-env
ExecStart=podman compose -f docker-compose.yml up -d
ExecStop=podman compose -f docker-compose.yml down
TimeoutStartSec=300

[Install]
WantedBy=default.target
```

`[Install]` is added/removed the same way `render-unit.sh`/`disable-boot.sh`
do today. The mDNS sidecar unit is unaffected — it already targets
`llm-server.service` by name via `BindsTo=`/`After=`/`PartOf=`, and that
unit name does not change.

One real behavior change to document, not hide: today's Quadlet unit gets
crash-restart from systemd itself (`Restart=on-failure`). With a oneshot
wrapper, systemd supervises "is the compose stack up," not "is each
container alive" — crash-restart instead comes from each service's own
`restart:` policy in the compose file. This matches how the user already
ran OmniRoute manually (`--restart unless-stopped`), so it is a change in
mechanism, not in outcome.

## OmniRoute Provisioning

`llmenv.py omniroute provision`, called from `scripts/start.sh` after both
containers report healthy (the same point `network.sh` is called today),
does:

1. `GET http://127.0.0.1:${omniroute_port}/api/providers` with header
   `x-omniroute-cli-token: ${cli_token}`. This header is checked by
   OmniRoute's authz code before any session/API-key check, and is accepted
   without a login flow as long as the request originates from a loopback
   peer — true here, since compose publishes OmniRoute's port to the host
   and the script runs on the host, probing `127.0.0.1` for the same
   IPv4-vs-`::1` reason the rest of this repo already prefers it over
   `localhost`.
2. Look for an existing connection named `llm-env-local` (a stable name
   this tool owns, so it never collides with or clobbers a connection the
   user created by hand).
3. Build the payload from OmniRoute's documented `ProviderConnectionCreate`
   schema:

   ```json
   {
     "provider": "llama-cpp",
     "name": "llm-env-local",
     "url": "http://llm-server:${port}/v1",
     "apiKey": "${server.api_key}",
     "isActive": true
   }
   ```

   `apiKey` must be the router's real API key — unlike OmniRoute's generic
   llama.cpp doc example, which assumes an unauthenticated local server,
   this router enforces one (`check-server.sh` asserts a 401 on a bad key).
4. `PATCH /api/providers/{id}` if found, else `POST /api/providers`. Both
   use the same header and schema.

This is real logic (build a request, decide create vs. update), so it
lives in `pylib/omniroute.py`, not bash — consistent with this repo's
existing split (`.agents/architecture.md`: "Bash orchestrates... Python
computes").

## Config Schema Changes

New `omniroute:` section in `models.yml` (schema version bump):

```yaml
omniroute:
  image: docker.io/diegosouzapw/omniroute:latest
  port: 20128
  cli_token: ""
  initial_password: ""
```

`cli_token` and `initial_password` are generated the same way
`server.api_key` is today — `new_api_key()` in `tools/lib.sh`, written with
`chmod 600` — the first time `start.sh` runs and finds them empty.

New `resources:` section sizing CPU/memory limits for both services:

```yaml
resources:
  llm_server:
    cpus: 0
    memory_mib: 0
  omniroute:
    cpus: 1
    memory_mib: 1024
```

`0` means "no explicit cap" until defaults are computed; `setup.sh` fills
in real numbers during the setup flow (see Resource Limits below), and
they remain user-editable afterward.

## Resource Limits

Sized from detected host resources, the same spirit as the existing VRAM
budget arithmetic in `pylib/budget.py`: reserve a fixed floor for the host
and other applications, then cap the rest. Proposed defaults, tunable
after setup:

- Reserve 2 CPU cores and 4 GiB RAM for the host.
- `omniroute` (a lightweight Node/Next.js process): fixed cap of 1 CPU
  core, 1 GiB RAM — it is not the workload driving resource needs here.
- `llm_server`: remaining cores and remaining RAM after the host floor and
  OmniRoute's fixed allocation.

Host CPU/RAM detection is a small addition to `pylib/detect.py`, which
already owns GPU/compositor detection from sysfs.

## Prerequisites Changes

`uv` is removed from `setup/prerequisites.sh`'s rpm-ostree-managed
`RUNTIME` array — on the reference machine it is actually installed at
`~/.local/bin/uv`, not as an rpm-ostree package, so the existing fallback
(`sudo rpm-ostree install uv`) does not reflect how it is really obtained
and would impose an unnecessary reboot even if it worked. It bootstraps via
its official installer (`curl -LsSf https://astral.sh/uv/install.sh | sh`)
instead, behind the same confirm-before-installing prompt every other
prerequisite uses.

`podman-compose` is added to `RUNTIME` (via rpm-ostree, since it is a real
system package, unlike `uv`), with `podman compose` as the health-check
command demonstrating the provider actually resolves — `podman compose
version` currently fails with "looking up compose provider failed" when
no provider is installed, which is the failure mode this prerequisite
check must catch.

## check-setup / check-server Changes

`check-setup` keeps its existing offline behavior unchanged — including
`record_inferences()`, which already sends a real prompt to a disposable,
self-contained `podman run` of the model and checks the response; that
check needs no running service and stays exactly as it is. It gains one
new check: `podman compose -f ~/.config/llm-env/docker-compose.yml config`
validates the rendered file's syntax and variable interpolation without
starting anything.

`check-server` gains an OmniRoute section, since testing OmniRoute
inherently requires the live stack (it has no self-contained analogue to
the raw-GGUF CLI test): after the existing router checks, verify OmniRoute
answers `GET /api/providers` with the CLI token and that the `llm-env-local`
connection is present and active, then send a chat completion through
OmniRoute's own endpoint and confirm it reaches the router and returns a
correct response — proving the full path, not just that the container is
running.

## Documentation

Add a troubleshooting section (in `AGENTS.md` or `.agents/architecture.md`)
stating explicitly:

- The generated compose file lives at `~/.config/llm-env/docker-compose.yml`.
- The systemd wrapper unit is `~/.config/systemd/user/llm-server.service`.
- To check state: `systemctl --user status llm-server.service`,
  `podman compose -f ~/.config/llm-env/docker-compose.yml ps`,
  `journalctl --user -u llm-server -f`.
- To inspect what is actually configured in OmniRoute:
  `curl -H "x-omniroute-cli-token: $(yq -r '.omniroute.cli_token'
  ~/.config/llm-env/models.yml)" http://127.0.0.1:20128/api/providers`.

## Verify During Implementation

Flagging explicitly rather than asserting as fact, per this repo's
"research before implementation" rule:

- Whether the installed `podman-compose` provider applies
  `deploy.resources.limits.cpus`/`memory` outside of swarm mode. If not,
  fall back to the older, more consistently honored top-level `cpus:`/
  `mem_limit:` service keys.
- OmniRoute's actual container health-check endpoint (needed for compose's
  `healthcheck:` block so `depends_on: condition: service_healthy` has
  something real to poll) — not confirmed by research so far.
- Exact translation of `Memory=`/CPU controls is moot now that Quadlet is
  not the mechanism, but confirm compose's resource keys produce real
  cgroup limits under rootless podman on this host, not just accepted
  YAML.

## Tests

Add or update tests for:

- `pylib/compose.py` rendering: valid YAML, correct service shape, no
  placeholder left unresolved
- `pylib/omniroute.py`: create-vs-update decision logic (mock the GET/POST/
  PATCH calls), payload shape matches the documented schema, real API key
  is used (not a placeholder)
- Config migration adding `omniroute:`/`resources:` sections with sane
  defaults to an existing `models.yml`
- `check-setup`'s new `podman compose config` step
- `check-server`'s new OmniRoute section

Run `make validate` and `make test` after implementation. End-to-end
sequence:

```bash
make setup
make check-setup
make start
make check-server
make stop
```
