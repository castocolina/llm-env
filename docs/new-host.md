# New host and migration runbook

This runbook brings the stack up on a new machine from the tracked profile
`llm-env.profile.yml`, and moves an existing deployment's state to it. The design is in
`docs/superpowers/plans/2026-11-23-tracked-host-profile.md`.

## What the repository reproduces and what it does not

| Reproduced from the checkout | Generated per host (never copied, never in git) | Not reproduced: copy it privately or redo it |
|---|---|---|
| Owner choices in `llm-env.profile.yml`: ports, the enabled models and their tuned definitions, KV cache types, System One on/off, `listen`, VRAM reserve, resource overrides and ceilings, start at boot, the observe stack, firewall policy | `server.api_key`, the OmniRoute dashboard password (`models.yml`), the remote-setup master key (`<repo>/.env`), the Grafana admin password, GPU facts (PCI address, Vulkan device, VRAM), the computed llm-server CPU/RAM limits | OmniRoute provider logins and OAuth sessions, the API keys OmniRoute issued to clients, combos, usage history (volume `llm-env_omniroute-data`); Grafana state and VictoriaMetrics history; Laya spans; downloaded model files (re-downloaded) |

Image tags float (`omniroute.image`, the llama.cpp image, `python:3.13-alpine`, the Laya base):
two hosts built on different days can run different images. Pinning digests is a documented
follow-up.

## Fresh deployment

Run these in a terminal on the new host, as the user that will own the stack:

```bash
git clone <this repository> ~/git/llm-env && cd ~/git/llm-env
make prerequisites                    # rpm-ostree on Bazzite/Silverblue, dnf on package-based Fedora
LLM_ENV_ASSUME_YES=1 make setup       # applies llm-env.profile.yml; no prompt for any key it names
make start                            # renders, starts, installs timers, boot units and the observe stack
make firewall                         # sudo: opens the LAN ports per network.firewall (skip if manage: false)
make check-setup                      # offline checks, including profile drift
make check-server                     # online API checks
```

What is automatic:

- `make setup` creates `~/.config/llm-env/models.yml` from `models.yml.example`, merges the
  profile, writes `<repo>/.env` with a random `OMNI_ROUTER_MASTER_KEY` (mode 0600, never
  overwritten), detects the GPU, downloads the enabled models, and builds the Laya image and
  prefetches its weights when System One is enabled.
- `make start` generates the API key and dashboard password, renders the compose file and units,
  starts the containers, installs the OmniRoute healer (and the weekly System One check), enables
  `llm-server.service` and linger when `boot.enable` is true, and, when `observe.enabled` is true,
  starts Grafana + VictoriaMetrics and enables `llm-env-observe-stack.service`.

What needs the owner:

1. `make firewall` with sudo (it prints exactly the commands it runs; `DRY_RUN=1` only prints).
   Without a terminal it prints the commands and exits non-zero; `make start` only warns.
2. OmniRoute provider authentication: open `http://<host>:20128`, log in with the password from
   `make show-secrets`, and add the providers (OAuth logins cannot be scripted). `make start`
   imports a Codex CLI session automatically when `~/.codex/auth.json` exists.
3. Combos: after the providers exist, `make combo-restore INPUT=<file>` with a file from the old
   host's `make backup` (restoring before the providers exist drops the connection IDs).

Nothing secret is copied for a fresh deployment. Changing a profile value later: edit
`llm-env.profile.yml`, then `make profile-apply` and `make restart` (or the targeted update
commands in the README).

## Reboot model

Linger keeps the user's systemd manager running from boot, so enabled user units start without a
login: `llm-server.service` (`default.target`) runs `scripts/start.sh` in boot mode and starts the
last rendered compose file without recreating containers; `llm-env-observe-stack.service`
(`default.target`) runs `scripts/observe-stack.sh start` the same way; the timers come back with
`timers.target`. `podman-restart.service` is not used, so a container's `restart:` policy only
covers crashes while the host is up. Check after a reboot with
`systemctl --user is-enabled llm-server.service llm-env-observe-stack.service llm-env-observe.timer llm-env-omniroute-heal.timer`
and `make status`.

## Migrating an existing deployment

`make backup` writes combo definitions and provider connection names (not credentials) to
`~/.config/llm-env/combo-backups/`. It does not include provider credentials, OAuth sessions,
the API keys issued to clients, usage history, Grafana dashboards state, VictoriaMetrics history
or Laya spans. To keep those, move the volumes.

On the old host (stop the stacks so the copies are consistent):

```bash
make backup
make stop
bash scripts/observe-stack.sh down
podman volume export llm-env_omniroute-data   > omniroute-data.tar
podman volume export llm-env-observe_grafana  > observe-grafana.tar
podman volume export llm-env-observe_metrics  > observe-metrics.tar
```

Copy privately (scp to the new host's home, mode 0600; these hold credentials):
the three `.tar` files, `~/.config/llm-env/combo-backups/`, `~/.config/llm-env/models.yml`
(only if the clients must keep the same llm-server API key and dashboard password), `<repo>/.env`
(only if remote machines must keep the same master key),
`~/.local/state/llm-env/observe/grafana-admin-password` (required with the Grafana volume: the
admin bootstrap only applies to a new volume) and, optionally,
`~/.local/state/llm-env/laya-spans/`.

On the new host, before the first `make start`:

```bash
make prerequisites
LLM_ENV_ASSUME_YES=1 make setup       # keeps a copied models.yml and .env, applies the profile
podman volume create llm-env_omniroute-data   && podman volume import llm-env_omniroute-data   omniroute-data.tar
podman volume create llm-env-observe_grafana  && podman volume import llm-env-observe_grafana  observe-grafana.tar
podman volume create llm-env-observe_metrics  && podman volume import llm-env-observe_metrics  observe-metrics.tar
make start                            # observe-stack.sh creates the Grafana secret from the copied file
make firewall
make check-setup && make check-server
```

Never run `make clean` as a migration step: it removes the volumes, the config and the Laya
weight cache. Laya weights (`llm-env-system-one-cache`) and model files are downloaded again by
`make setup`. Re-render units on the new host (`make start` does it); never copy generated units.
