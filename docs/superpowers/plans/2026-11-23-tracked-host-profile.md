# Tracked host profile (configuration as code) — plan

Date: 2026-11-23. Owner decision: the deployment must come back after a reboot and on another
machine from tracked configuration alone. Mechanism (owner): a committed, non-secret profile file
that `make setup` and an idempotent re-apply merge onto the generated, host-local
`~/.config/llm-env/models.yml`. Secrets stay generated per host.

## Facts verified before coding (2026-11-23, this host)

- (a) `models.yml` is untracked (`.gitignore`), mode 0600, and holds the API key, the OmniRoute
  dashboard password and every owner choice (`system_one.listen: lan`, enabled model
  `qwen3-5-4b`, `omniroute.image: ...:next`, `vram_reserve_mib: 4480`, KV cache `q5_1`, ...).
  `qwen3-5-4b` is not in `models.yml.example`, so an alias list alone cannot reproduce it.
- (b) The observe pair (`llm-env-observe` compose project) is started only by
  `bash scripts/observe-stack.sh up`; `llm-server.service` never starts it and
  `podman-restart.service` is disabled, so after a reboot it stays down. Its collector timer is
  enabled. Linger is on.
- (c) The firewall opening is an interactive `sudo firewall-cmd` prompt in `setup/network.sh`.
  This host's zone (FedoraWorkstation) already opens `1025-65535/tcp`.
- (d) Referee blockers: no migration runbook; `.env` master key never created (remote-setup
  `/config` answers 503 on a fresh clone); `make check-setup` fails before the first render
  (compose syntax) and always fails its span-bind check (literal `${HOME}`, fixtures repeat it);
  `make prerequisites` assumes rpm-ostree; floating image tags.

## Schema (`llm-env.profile.yml`, version 1)

Allowlist; any other key is an error. Every key is optional except `version`.

| Profile key | Lands in | Validation |
|---|---|---|
| `server.{port,mdns_name,sleep_idle_seconds}` | `models.yml` `server.*` | positive int / non-empty str |
| `omniroute.{image,port}`, `remote_setup.{image,port}` | same path | `validate_config` |
| `llm_server.enabled` | same path | `validate_config` |
| `llm_server.models` (ordered aliases) | `models[].enabled` via `set_enabled_models` | `config.py` |
| `models` (entries for those aliases, minus `enabled`) | upsert by alias into `models[]` | allowed model keys + `validate_config` |
| `runtime.{models_max,ubatch_size,flash_attn,cache_type_k,cache_type_v}` | same path | `validate_config` |
| `system_one.{enabled,listen,port,models,vram_reserve_mib,idle_unload_seconds}` | same path | `validate_config` |
| `resources.overrides` | same path, replaced whole | `validate_overrides` |
| `resources.llm_server.{memory,cpu}_ceiling{,_floor}_pct` | same path | `validate_config` |
| `boot.enable` | `server.start_at_boot` | bool |
| `observe.enabled` | profile only | bool |
| `network.firewall.{manage,allowed_cidrs}` | profile only | bool / list of CIDRs |

Never in the profile (generated or host-specific): `server.api_key`, `omniroute.initial_password`,
`.env`, `gpu.*` (detected), `resources.llm_server.{cpus,memory_mib}` (computed), `system_one.image`
(local build tag), model `benchmark` data.

## Merge rules

1. Only keys the profile names are written; everything else in `models.yml` is left as it was.
2. `resources.overrides` and each model entry key are replaced as a unit; model entries the
   profile does not list are untouched; `llm_server.models` sets `enabled` and order through the
   existing `set_enabled_models` (which also clamps `runtime.models_max`).
3. The result goes through `migrate_config` + `require_valid_config`; any error aborts with
   nothing written.
4. Write only when the data differs: timestamped backup `<config>.profile-<stamp>.bak` (0600),
   atomic replace through `save_config` (0600). A second apply is a no-op.
5. `check` runs the same merge in memory and reports differing paths (never secret values);
   exit 1 on drift. `export` writes the allowlisted subset; it keeps `observe` and `network` from
   an existing profile and refuses to write if any secret value from `models.yml` appears.

Precedence in `make setup`: profile > `LLM_ENV_*` seed > persisted value > default, per key. A key
the profile names is never prompted for; keys it does not name keep today's prompts and seeds.

## Boot model

Linger keeps the user manager running from boot. Enabled user units start with it:
`llm-server.service` (`WantedBy=default.target`, runs `scripts/start.sh` in boot mode, which
starts the last rendered compose file with `--no-recreate`), the timers (`timers.target`), and the
new generated `llm-env-observe-stack.service` (`WantedBy=default.target`, runs
`observe-stack.sh start`: credential check + `compose up -d --no-recreate`). Nothing relies on
`podman-restart.service`. `make enable-boot` and `make start` install and enable units from
config (`server.start_at_boot`, profile `observe.enabled`); installing never starts or recreates
a container. `make disable-boot` and `observe-stack.sh down` disable them.

## Firewall model

`scripts/firewall.sh` (`make firewall`): the LAN-published ports of enabled services
(llm-server, OmniRoute, remote-setup, System One with `listen: lan`) from `models.yml`, and the
profile's `network.firewall`. `manage: false` or no profile: nothing. Queries first (unprivileged),
adds only what is missing, runtime and `--permanent`; with `allowed_cidrs`, rich rules per CIDR
and port instead of open ports. Removes nothing. Prints each command it runs; `--dry-run` only
prints. Root runs directly, a terminal may prompt through `sudo`, otherwise `sudo -n`; without
usable sudo it prints the commands and exits non-zero. `network.sh` (run by `make start` and boot)
calls it only with `manage: true` and only warns on failure, so boot never fails on the firewall.

## Clean-host fixes in scope

`.env` with `OMNI_ROUTER_MASTER_KEY` (`openssl rand -hex 32`, 0600, filled only when missing or
empty, never printed) from setup and start; check-setup skips the compose check with a message
when not rendered, compares the expanded span path and runs the drift check; prerequisites use
`dnf` unless `/run/ostree-booted` exists, and check `openssl` and `skopeo`.

## Test plan (fakes only)

New `tests/test_profile.py`: schema errors (unknown key, bad types, bad CIDR), export without
sentinel secrets, apply idempotent with backup, secrets untouched, template + profile yields a
valid config with `qwen3-5-4b`, drift exit codes, setup precedence, `.env` creation, prerequisites
on a fake dnf host, firewall with a fake `firewall-cmd` (dry-run, idempotent, `manage: false`,
no sudo, CIDRs, range not removed), observe unit rendering and parse. Existing fixtures that hid
the span bug are fixed. A session fixture points `LLM_ENV_PROFILE`/`LLM_ENV_ENV_FILE` away from
the repo so older script tests never read the committed profile or the real `.env`. Mutants:
export leaks a secret, apply overwrites a secret, apply not idempotent, unknown key accepted,
firewall removes a rule, `.env` overwritten.

## Out of scope (documented follow-ups)

Image digest pinning (OmniRoute, llama.cpp, python, Laya base); VictoriaMetrics 100 GB free-space
floor; quoting `install_user_timer` paths; firewall zone selection (default zone only); model file
hash verification; migrating OmniRoute data automatically (runbook gives the volume commands).
