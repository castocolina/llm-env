# llm-env

Local llama.cpp router server with GPU acceleration, fronted by an OmniRoute
gateway and a LAN installer for other machines, running as a rootless podman
compose stack on Bazzite.

## Quick start

```bash
make prerequisites  # confirm and install Bazzite/Fedora host tools
make setup       # choose GPU and models (or opt out of local GPU inference entirely), download, generate config
make benchmark   # measure Vulkan throughput (only after make stop-llama; see below); CPU fallback still reports a failed Vulkan benchmark
make start       # start whatever is not running (never stops a running container)
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
- Serves a LAN installer other machines can run with one `curl | bash` to
  configure Pi/OpenCode against this host's OmniRoute gateway. See
  [Remote setup](#remote-setup).
- Can run with no local GPU at all — OmniRoute + the remote installer only,
  routing to models elsewhere. See [GPU-optional mode](#gpu-optional-mode).
- Optional System One (Laya) decisions on an AMD ROCm GPU. `english` and
  `multilingual` are loaded by default. Steady VRAM is about 4.26 GB, so
  the default `vram_reserve_mib` is 5120. `english` alone saves only about
  200 MiB, and non-English or short English inputs then fail with HTTP 500.
  Changing `system_one.models` needs `make setup` again. Beyond 64 requests
  in flight, Laya returns HTTP 503 and `Retry-After: 1` (no queue). After
  10 idle minutes (`system_one.idle_unload_seconds`, default 600; `0` never
  unloads) Laya drops its checkpoints and frees about 3.7 GB of VRAM. The
  next request reloads them in about 0.4 s.

### Laya request history in Phoenix

When System One is enabled, normal `make start` also starts Phoenix and the
server-side observer records real Laya requests in the `laya` project. Open
`http://127.0.0.1:6006/projects` (or the configured
`LLM_ENV_PHOENIX_PORT`) and select **laya**. History begins when the observer
is deployed; earlier requests are not backfilled. Use normal `make stop` to
stop the dashboard. Its owned data volume survives normal stops and starts.

The observer records request state and questions, typed answers and their
probabilities/confidence fields, model/routing metadata, known usage, timing,
HTTP status/errors, and batch structure. It does not record HTTP headers and
redacts credential-like JSON keys. Each request and response capture is
limited to 24,000 bytes; truncation is marked. Export uses a 128-record
in-memory queue, a 64 KiB record cap, and a 1.5-second background HTTP
timeout. If Phoenix is unavailable or the queue fills, exports can be lost;
those failures and drops are logged. This is best-effort history, not a
complete audit log. Trace details show the captured input/output; do not
assume every attribute is available as a native chart or that confidence
means correctness. Comparative observer latency overhead has not been measured.

Phoenix binds to loopback and disables authentication. This blocks LAN
publication, but **does not protect traces from other users or processes on
the same machine**. Treat prompts, state, and model output in this dashboard
as locally accessible private data. No Phoenix telemetry or off-host export
is enabled. The configured seven-day retention policy
(`LLM_ENV_PHOENIX_RETENTION_DAYS`, default `7`) is a scheduled cleanup policy,
not a storage quota or a guarantee that every record has been deleted on time.
`make status` reports only the bounded first page of up to 1,000 spans; its
range and counts describe that page, and the `completeness` field indicates
whether Phoenix reports another page. Cgroup `memory.low` (best-effort
protection) and `memory.min` (hard protection) are distinct; neither should be
read as the other. CPU quota is reported from `NanoCpus` when available, with
`CpuQuota/CpuPeriod` as the compatible fallback.

The current resource policy gives Phoenix a 192 MiB soft memory hint and caps
it at 1 GiB memory and 1 CPU. CPU shares are 256 relative shares under
contention, not guaranteed cores. The cap is benchmark-derived (804 MiB peak
+ 20 %); re-derive it with `make resources-benchmark`. Browser E2E discovery uses an installed Playwright package or a
local npm cache; set `LLM_ENV_PLAYWRIGHT_ROOT` to a Playwright package
location or `LLM_ENV_CHROMIUM_EXECUTABLE` to an existing Chromium binary when
automatic discovery is unavailable. The test does not install packages.

## Configuration

Everything lives in `~/.config/llm-env/models.yml`. Models are enabled and
disabled with a flag, so nothing is lost when you stop using one:

```bash
uv run llmenv.py models list
uv run llmenv.py models disable ornith
uv run llmenv.py models enable openhermes
```

Reclaim disk space from downloaded models (they are NOT removed by
`make clean`) with:

```bash
make prune
```

Enabled models are all routable. `runtime.models_max` is a separate residency
limit; the target configuration keeps one model resident and unloads it before
loading another alias. This adds model-switch latency but avoids holding both
models in VRAM.

Clean setup maps `gemma4` to yuxinlu1's Agentic Gemma 4 12B v2 Q4_K_M
build with a 131,072-token context. Ornith 1.0 9B uses its full native
262,144-token context (confirmed via its `config.json`'s
`max_position_embeddings`). Both get one request slot with Q5_1 K/V caches.
Pi and OpenCode advertise up to 8,192 output tokens, so reserving the full
output allowance leaves the rest of each model's context for prompt and
history. All tokens still share the same slot. Setup reports an explicit
VRAM-budget failure instead of shrinking context or offloading layers.

An optional `ornith-35b` entry adds Ornith 1.0 35B, a mixture-of-experts
model (35B total / ~3B active parameters per token) too large to fit fully
in most consumer GPUs' VRAM. It uses llama.cpp's `--n-cpu-moe` flag (set via
the model's `n_cpu_moe` field) to keep routed-expert weights for its first N
transformer blocks in host RAM while the rest of the model stays on GPU —
`pylib/gguf.py` computes exactly how many bytes that is from the GGUF's own
tensor layout, and `make setup` (and `make start`/`make start-llama` whenever
llm-server is not already running) refuse to start if
`resources.llm_server.memory_ceiling_pct` isn't high enough to hold it, the
same way they already refuse an infeasible VRAM budget.

The measured llama.cpp build applies a strict admission rule: post-template
prompt tokens must be less than `n_ctx`. With Gemma's 131,072-token slot,
131,071 is admitted with `max_tokens: 1`; 131,072 and above are rejected.
Ornith's 262,144-token slot follows the same rule at its own boundary. This
practical boundary does not change either model's configured client context.

## GPU-optional mode

`make setup` asks "Enable local GPU inference (llm-server)? [Y/n]" — answer
`n` (or set `LLM_ENV_NO_GPU=1` for unattended runs) to skip GPU detection,
model selection/download, and VRAM budgeting entirely, and run this host as
an OmniRoute gateway + remote installer only, with no local llama.cpp
instance and the GPU untouched. This is persisted as `llm_server.enabled` in
`models.yml` — switch with `make llama-disable` then `make restart`, or back to local with
`make llama-enable MODEL=<alias>`, then `make setup` (GPU detection, model
download and validation were skipped in gateway-only mode), then `make restart`. In this mode, configure an upstream provider (OpenRouter, another
frontier API, etc.) through the OmniRoute dashboard yourself; `make start`
does not auto-provision a connection to a local model that doesn't exist.

## Local inference toggle and model swap

`make llama-enable MODEL=<alias>` and `make llama-disable` change only
`models.yml`. Each one first copies `models.yml` to
`models.yml.<YYYYmmddTHHMMSS>.bak` (mode 0600) and prints its path. It then
edits a temporary copy and renames it over `models.yml` only when every step
succeeds, so a failure leaves `models.yml` byte-identical. Neither command
starts or stops anything; each prints the next command.

Swap the model without touching OmniRoute:

```bash
make stop-llama && make llama-enable MODEL=<alias> && make start-llama
```

- `make stop-llama` stops the llm-server container only and never removes it.
  `make start-llama` runs the budget gate and render, then starts llm-server
  alone. It waits for `/health`. If that fails, it exits 1 and prints this
  line on stdout, and leaves the container as it is:

  ```
    Recover: make stop-llama, restore the backup printed by make llama-enable if the model changed, then make start-llama
  ```

- A model swap changes `presets.ini`. llama.cpp reads it when the process
  starts (assumed; not checkable offline), so the new model loads on the next
  llm-server start. `LLAMA_ARG_MODELS_MAX` is baked into the container and
  keeps its old value until `make restart`. For a one-model swap that value
  is harmless.
- `make start-llama` on an llm-server that is already running changes nothing.
  It only checks `/health` and provisions `llm-env-local`. Stop it first to
  load a changed model.
- Budget: when System One runs and the compositor is on the dGPU, System One's
  VRAM is counted twice. This can refuse a start that would fit. It never
  over-commits. `make restart` measures with everything stopped, which clears
  a false refusal.
- Healer and `--requires`: the unit's ExecStart (inactive unit, boot) creates
  OmniRoute with `--requires=llm-server`; an active `make start` runs `up --no-deps`,
  so a container it creates has no dependency. While llm-server is stopped and
  OmniRoute carries the dependency, the healer's `podman restart omniroute` is
  refused ("dependencies not started"). The refusal is logged, and the healer
  never reloads llm-server. `make update-omniroute` recreates OmniRoute without that
  dependency. Either way,
  `make stop-llama` is a temporary stop.
- `make stop-llama`, then `make llama-disable`, then `make start`: with
  `--requires` on OmniRoute, `make start` leaves llm-server stopped. Gateway-only
  mode is applied by `make restart`, which `make llama-disable` prints.
- llm-server missing from the compose file while still running (after
  `make llama-disable` and before `make restart`): `make start` leaves it
  running. `make stop` or `make restart` removes it through `--remove-orphans`.
- System One disabled, then `make start` while Phoenix still runs: `make start`
  no longer stops Phoenix, so it keeps running. `make restart` stops it, even
  when System One is disabled.
- While llm-server is stopped, the mDNS unit's `ExecStartPre` polls its
  `/health`, fails after the timeout, and clears on `make start-llama`.
- An inactive server unit with containers still running (the unit failed,
  for example when its start timed out): `make start` starts the unit without
  recreating anything, so running containers keep their old definition.
  `make restart` removes them directly and recreates everything.
- An active-unit `make start` runs its direct `up` with `--pull=never`, so it
  makes no registry call and adds no latency or DNS noise. It does not avoid
  failure on an unreachable registry. Running containers are untouched and
  the exit status is 0. A container that must be created pulls through
  `podman create` and fails only if its image is unavailable. (An inactive
  unit still pre-pulls through its ExecStart.)

### Benchmarking while llm-server holds the GPU

`make benchmark` rewrites `gpu.backend` and `gpu.image` in the live `models.yml`
when the Vulkan benchmark fails. It also gives contended or failing results while
llm-server holds the GPU. Run it only after `make stop-llama`. Then take a fresh timestamped backup right before the benchmark: `cp -p ~/.config/llm-env/models.yml ~/.config/llm-env/models.yml.$(date +%Y%m%dT%H%M%S).bak`. Do not reuse the backup `make llama-enable` took earlier: it may be stale, and restoring it would undo the enable edit.
Afterwards verify that `yq '.gpu.backend, .gpu.image' ~/.config/llm-env/models.yml`
is unchanged. If it is not, restore from that backup and report it. Prefer the
skill's `bench-fit.sh` for screening.

## Local model capacity and my-coding-lite

A local model's measured limits live in one card per alias,
`~/.config/llm-env/capacity/<alias>.json` (directory 0700, file 0600, written
atomically). Schema: `pylib/capacity_card.schema.json` (schema 1). Unknown
values are `null`; the card never holds guesses. `make clean` keeps cards.
`make model-capacity MODEL=<alias> [DEPTH=<bench.json>] [SCORE=<score.json>]`
writes the sections it is given (others are kept) and then shows the card.
The alias must exist in `models.yml` and match `[a-z0-9][a-z0-9._-]{0,63}` as a
whole; anything else is refused.

Each section records a fingerprint of the model's config. A section written
under another config is listed in `stale_sections` and never feeds `usable`.

**Usable context.** `usable.context_tokens = min(ctx_size, d + 512)`, where `d`
is the deepest depth-sweep point with a measured `tg_tps`. It is `null` when
there is no current depth section or when the current interference section has
`passed: false`. `usable.max_output_tokens = min(client_max_output_tokens,
context_tokens)`, or `null`.

**Interference gate (`make model-interference`).** It loads the local model for
120 s after a 30 s baseline and fails when any of these holds:

- a System One (Laya) request returns non-200 during load;
- Laya p95 during load exceeds 500 ms (`--max-p95-ms`) or Laya p50 exceeds
  150 ms (`--max-p50-ms`). Laya serves advisory typed judgments (`jev`) whose
  clients time out after about 20 s, so these are service-level limits; a
  relative p95 rise (more than 50 % and more than 10 ms over the baseline) is
  only reported as `regression_warning`, never a failure;
- a `memory.events` `high`, `max`, `oom` or `oom_kill` counter grows for
  omniroute, remote-setup, system-one, phoenix or llm-server;
- a container's counters reset (its uptime went down) between snapshots;
- no load request completes inside the window;
- a service running before the load is not running after it.

OmniRoute health latency (`GET /api/health`, unauthenticated) is recorded but
does not gate, nor do VRAM headroom, host MemAvailable and the swap delta.
Each load request starts with a unique prefix so llama-server cannot reuse the
KV cache; every request pays a full prefill.

With System One disabled, the gate starts no Laya probe: the report has
`system_one: null` and the note `system_one latency not measured (System One
disabled)`, and the card keeps that note. With System One enabled but not
running, the gate refuses (`run 'make start'`). Run directories land in
`<root>/model-interference/<stamp>-<pid>`, where `<root>` is `tmp/` in the repo
unless `LLM_ENV_SCOUT_OUT` names another root.

**my-coding-lite (`make local-combo`).** An OmniRoute combo with one member,
`llama-cpp/<alias>` on the `llm-env-local` connection, strategy `priority`, and
`context_length` set to the usable context. It has no cloud fallbacks, so the
local floor is never lowered. The tool owns the combo: drift is overwritten, as
for `llm-env-local`. The combo is created or updated only when `llm_server.enabled`
is true, llm-server is running, the `llm-env-local` connection exists, and the
card has a current depth section and a current interference section with
`passed: true` (a card whose interference section is missing or stale has
`usable` null with basis `interference not measured`). Otherwise the target reports `skipped` with the reason and the
make target to run, and exits 0. `make start` and `make start-llama` run it only
when `llm_server.enabled` is true and OmniRoute provisioning succeeded. `make backup` includes the combo; restore remaps its member to
`llm-env-local`.

Observed on the pinned OmniRoute image with a throwaway instance, `/v1/models` did not list
`llama-cpp/<model>`, so the card is the only source of a `llama-cpp` member's
limits. A combo whose member carries `id` and `context_length` (no `config`) was
accepted and persisted, and the combo itself is listed in `/v1/models` with its
`context_length`. A chat to the combo routed to the local upstream, and a PUT of
a new `context_length` persisted. With llm-server down, a chat to the combo
returns 502 and the combo stays in OmniRoute.

**Clients.** `make setup-local-llm-agents` lists `router-env/my-coding-lite` in
pi and opencode only while llm-server is enabled and the card is current; a
combo with no known minimum is dropped. Remote installers never list it: their
`llama-cpp` members have no local limits, so the combo has no known minimum. The router
key is written to `~/.config/router-env/api-key` (0600) and referenced from the
client configs (pi `!cat`, opencode `{file:...}`), never written into them.

**Quant headroom.** The onboard-local-model skill's
`scripts/quant-headroom.sh check` asks whether the next quants of the same
Hugging Face repo (default `Q5_K_M,Q6_K`) fit beside the current one, using the
sizes the repo reports and the measured VRAM and RAM. Its sections go into the
card's `quants` section, with the verdict per candidate. When every candidate is
known not to fit, the verdict is written as `no headroom for Q5_K_M/Q6_K at ctx
N, free MiB X`. A fitting candidate is trialled in ascending size with the same
depths and ladder; the first one that fails the interference gate stops the
trials. Each trial ends by restoring the original quant, so the default is never
switched silently. `my-coding-lite` is unavailable while a candidate is served.
Run outputs go to `<root>/model-tune/quant-<stamp>-<pid>`, using the same
`LLM_ENV_SCOUT_OUT` root.
The ladder challenges are the skill's bundled tasks, copied into isolated
directories under `<repo>/tmp/`; the scripts refuse an `--out` outside it, and
nothing runs in or against the owner's project.

## OmniRoute updates

`make update-omniroute` is an idle-aware, dependency-aware updater for the
OmniRoute container. It only acts when **all** of these hold:

- The configured image tag resolves to a different immutable digest than the
  running container's image, **or** the rendered omniroute service definition
  differs from the running container (config drift, below). Otherwise it
  prints `unchanged` and exits 0.
- The running image has an OmniRoute activity profile: a reviewed one
  shipped in `pylib/omniroute_activity_profiles.json` (today `:latest`
  `038b2aba95a6` and `:next` `2a7a68e89fe5`), or one the updater derived
  itself. **A running image without one is characterized first**, under the
  update lock: the updater runs it unmodified in a disposable, isolated
  rootless Podman store under `tmp/` (never the live stack) against a fake
  provider, derives the profile from that evidence, and keeps it, with the
  evidence, in the user-local store
  `${XDG_STATE_HOME:-~/.local/state}/llm-env/omniroute-profiles/`
  (`<config-id>.profile.json`, `.evidence.json`, `.candidate.json`; never
  the git tree). Budget: 1200 s (a run takes about 8 minutes); stdout shows
  `phase: characterizing ...`, `phase: deriving`, `phase: storing ...`,
  then `phase: observing ...`. An incomplete derivation defers with
  `deferred: capability` and `detail:` lines naming each reason and the
  evidence file; a harness failure exits 1 and stores nothing. The loader
  reads shipped profiles first and the local store second; a local profile is
  used only while its evidence still hashes to the recorded sha256 and the
  store is a private 0700 directory (otherwise the next run re-characterizes).
- The dashboard's activity feed and health endpoint show the router idle for
  `LLM_ENV_UPDATE_IDLE_SECONDS` (default 300) and stay idle while it
  re-samples; a single deployment-wide deadline bounds the whole wait
  (`LLM_ENV_UPDATE_MAX_WAIT_SECONDS`, default 10800).
- The relevant backend inventory, compose configuration, effective compose
  output and running container all agree (fingerprints and digests are
  rechecked during the wait and at commit time by a detached worker that holds
  the update lock).

**What counts as a relevant inventory change.** Only containers that matter to
replacing OmniRoute are compared: the target itself, every container of its
compose project (siblings, including restarts), any container that depends on
it, and any container attached to one of its networks. Throwaway containers of
other projects on the same rootless Podman (experiments, e2e runs) may come and
go during an hours-long wait; at commit time the updater prints
`ignored unrelated inventory change: N added, M removed` (counts only). A
relevant change while waiting (a sibling restarted, a dependent appeared, the
target restarted by the healer) prints `phase: re-baselining (<names and what>)`
and re-runs preflight once, in the same lock and wait budget; a second relevant
change in the same run defers `ownership` with a `detail:` line naming the
containers. The target replaced or removed by someone else defers at once
(`detail: target replaced or removed: omniroute`); a new dependent is refused by
the re-baseline preflight (`detail: a container depends on the target: <name>`).
A change in the few seconds between the end of the wait and the recreate
defers without a re-baseline.

When OmniRoute is **stopped or absent** (for example after `make stop`),
nothing can be interrupted: the idle gate, the activity profile and the
observer are skipped, the image is still resolved and pulled immutably, and
only `omniroute` is recreated (`--no-deps`) and verified. Note that this
**starts** it: a stopped container is replaced by a running one on the new
image, and an absent one is created and started from the deployed compose file. The ownership checks
(compose project/service labels, the compose file agreeing with `models.yml`)
still apply; a running/stopped container whose port, password or image
reference differs is config drift. A running container that Podman reports `unhealthy` or `starting` is
accepted only if the authenticated `/api/monitoring/health` answers;
otherwise the update defers with `health`.

**Configuration changes (deferred restart).** `make update-omniroute`
applies a changed omniroute service definition too, once idle. It compares
the rendered service (`~/.config/llm-env/docker-compose.yml`, from
`bash setup/render-unit.sh` or `make setup`) with the running container: the
environment keys the service defines (variables the image sets itself or that
are not rendered are ignored; list or map form and order do not matter;
values are compared as hashes and never printed), memory limit and
reservation, CPUs and CPU shares, published ports, healthcheck, restart
policy and image reference. Any difference prints one line naming the fields,
e.g. `drift: environment CALL_LOG_MAX_ENTRIES, memory`, and recreates
OmniRoute with the **same** image through the same path as an image update:
the idle gate (profile, observer, last look), the target-only recreate and
the health verification (a failing verification exits 1, no rollback). A new
image and drift in one run are applied together. While the password or port
drifted, the running container is observed with its own values and the
replacement with the configured ones. After changing OmniRoute configuration,
re-render and run `make update-omniroute`; never recreate the container by
hand. Both paths share `apply_when_idle` (`scripts/omniroute-apply-when-idle.sh`).
Not detected: a variable removed from the template (unrendered variables are
ignored), volume or network changes (still an `ownership` deferral).

Exit codes of `bash scripts/update-omniroute.sh`: `0` updated or unchanged,
`1` sanitized failure (never rolls back; a failed `podman pull` is one),
`75` `deferred: <reason>`. The last stdout line is the machine-readable
result (`unchanged`, `updated <old> -> <new>`, `deferred: <reason>`); errors
print `error: ...` on stderr. `make update-omniroute` cannot pass `75` through:
GNU make exits `2` for every failing recipe (its own message still reads
`Error 75` or `Error 1`, and the banner says `deferred, exit 75`), so
automation runs the script directly. The deferral reasons are a closed set:
`busy` (client activity), `deadline` (the wait budget ran out), `lock`
(another update holds the lock), `age`, `clock`, `io` (transient: health
timestamp too old, clock mismatch, an unreachable API), `auth`, `schema`,
`feed`, `continuity`, `overflow`, `coverage` (an unprofiled route; `detail:`
on stderr names it), `capability` (no reviewed profile), `logging`,
`ownership`, `health`, `input` and `state` (the observer cannot write its own
verdict file, e.g. disk quota).

Activity routes (both profiles, measured): `POST /v1/chat/completions`,
`/v1/messages` and `/v1/responses` are client inference (each writes its own
access-log line and call-log rows); `POST /api/providers/test` rows are
OmniRoute's own connection tests and never count; `GET /v1/models` leaves no
activity signal at all and is not activity; `POST /v1/embeddings` writes
call-log rows but no access-log line, so it is unprofiled and defers
`coverage` while one of its rows is on the call-log page read. The call log is read as a small newest-first page (the profile's
`rows_page_limit`): an in-flight client call, or a newest client call younger
than the idle threshold (`LLM_ENV_UPDATE_IDLE_SECONDS`, default 300 s), is
busy. A full call log is not an error. Right before `podman stop` the commit
worker takes one more API-only sample (health, history, call log; same rule)
and defers `busy` (or the unknown reason) with nothing changed. `GET
/api/monitoring/health` is stale-while-revalidate: a response carries the
snapshot the previous health request computed, so a lone read at idle is as
old as the poll interval. Every sample (observer, last look, healer) primes it
with one discarded GET and reads it again 0.2 s later; an `age` deferral's
`detail:` gives the age seen and the limit. When the health
clock lags (it can lag by seconds under load) but the journal, counters or the
call log show activity, the sample counts as busy. A sample that is unknown for a transient reason (`age`, `io`, `clock`)
with no such activity does not end the wait. It restarts the idle window, and the run defers only if
such samples continue for more than 60 s. Any other unknown reason defers at
once.
Deferral is the normal safe outcome; nothing mutates before every check
passes, and the replacement itself is not transactional and is never rolled
back (the previous image stays in the local store; recovery is manual).

This is **not zero-downtime** and observation is **not admission control**:
in-flight requests are drained with the compose `stop_grace_period`, and the
idle policy reduces, but cannot eliminate, the chance of cutting a request.

`make migrate-remote-setup` is a one-time recreation of the remote-setup
container without its native dependency on OmniRoute. It requires the
**deployed** `docker-compose.yml` to already have no
`services.remote-setup.depends_on` — re-render it first (see "Updates"
above; `make setup` re-renders, and its side effects include a device-probe
`podman run --rm`, Phoenix prepare and observer-image build when System One
is enabled, a legacy `llm-mdns.service` stop, and a `systemctl --user
daemon-reload`). The migration never rewrites the deployed compose file.
While remote-setup is down or `/config` cannot reach OmniRoute, `/config`
answers `503` + `Retry-After: 5` (bounded, single-flight, never a hang);
OmniRoute itself is untouched.

Cgroup resource caps in the rendered compose are soft/hard policy, not
reservations. The OmniRoute 2 GiB cap is benchmark-derived (1,651 MiB peak
+ 20 %) -- the prior 1 GiB limit tripped OmniRoute's 503 memory guard under
load. See `make resources-benchmark` and the sizing rule in AGENTS.md.

How the updater itself is verified: normal `make test` runs stubbed worlds plus
unit coverage of the derivation rules. Two opt-in suites exercise real
containers inside a **disposable** rootless Podman backend under `tmp/` (never
the live stack; they need `podman-compose`, `skopeo`, a readable journal and
the base images on the host):

```bash
LLM_ENV_UPDATE_PROOF=1 uv run pytest tests/test_omniroute_update_proof.py -q      # replacement mechanics
LLM_ENV_UPDATE_CHARACTERIZE=1 uv run pytest tests/test_omniroute_characterize.py -q  # activity measurement
```

Measurements and their review outcome live in
`docs/superpowers/research/2026-10-23-updater-characterization.md`. The second
command runs the same harness as the updater's self-characterization
(`pylib/omniroute_characterize.py`) and writes the gzipped evidence, the
candidate and, when complete, `<date>-omniroute-profile-<config12>.json` under
`tmp/omniroute-characterization/` (gitignored; never `docs/`); set
`LLM_ENV_UPDATE_CHARACTERIZE_IMAGE=<image>` for another image. To ship a
profile, copy its `.json.gz` into `tests/fixtures/omniroute-evidence/` (the
entry already names that path) and append the entry to
`pylib/omniroute_activity_profiles.json`; the test suite re-derives every
shipped entry byte for byte from that fixture, and replays the recorded real
samples through the runtime.

Work files: each updater, migration and healer run keeps its artifacts (the
dashboard password file, the projection with environment values) in a fresh
0700 directory under `$XDG_RUNTIME_DIR` (per-user tmpfs), else under `$TMPDIR`
or `/tmp`. TERM, HUP and INT exit with 128+signal after removing it; after the
hand-off the commit worker owns and removes it on every exit path; a SIGKILLed
updater shell is cleaned up by its orphaned observer. The password file exists
only while a reader needs it (until the observer's first verdict, then again
from the hand-off). A SIGKILL before observation starts can still leave a
directory behind (no password file yet, but the projection can hold environment values); it lives on the tmpfs until logout.

### z.ai weekly-quota reset

`make start` also installs a 5-minute `llm-env-zai-reset` systemd user timer that runs
`scripts/zai-reset-weekly.sh`. When an active z.ai connection's weekly quota (OmniRoute `GET /api/usage/<id>`)
reaches 99 %, it redeems one `WEEK` reset card through OmniRoute (`POST /api/usage/glm-reset-card`), at most once
per weekly window (the window's `resetAt` is recorded in `~/.cache/llm-env/zai-reset-state.json`; the idempotency key
is derived from it). Without a card it does nothing. `bash scripts/zai-reset-weekly.sh --dry-run` reports what it
would do without redeeming; `journalctl --user -u llm-env-zai-reset` has each run.

### OmniRoute healer

`make start` installs a 5-minute `llm-env-omniroute-heal` systemd user timer
that runs `scripts/heal-omniroute-admission.sh` on the host. It restarts
OmniRoute (`podman restart omniroute`) in two cases only:

- a stuck chat-admission lease (upstream #14456): `activeHeavy` pinned while
  `shedTotal` keeps climbing for `OMNIROUTE_HEAL_STRIKE_THRESHOLD` runs;
- a memory recycle: the router is idle by the updater's own rule (`uv run
  llmenv.py omniroute-update activity`: the running image's reviewed profile,
  probe rows ignored, no admitted, queued or in-flight request, no client call
  in the last 120 s; an unprofiled image or route counts as unknown) and no
  OmniRoute update holds the update lock (the healer holds it through the
  restart), and its cgroup memory, read from
  `/sys/fs/cgroup` without exec and excluding file cache, stays above the
  threshold -- 60 % of the container's `memory.max` (1228 MiB at the 2048 MiB
  default, below the guard's 0.85/0.92 shedding marks), or an absolute
  `OMNIROUTE_HEAL_MEMORY_MIB` -- for
  `OMNIROUTE_HEAL_MEMORY_SAMPLES` runs (default 3). Memory restarts are at
  least `OMNIROUTE_HEAL_MEMORY_COOLDOWN_HOURS` apart (default 6) and at most 2
  per rolling 24 h. A skip is logged. `OMNIROUTE_HEAL_MEMORY_MIB=0` disables
  it. If activity or memory cannot be read, it does not restart. A missing,
  truncated or garbage state file counts as zeros (it is rewritten atomically),
  so the stuck-lease check keeps working.

Set these in a drop-in (`systemctl --user edit llm-env-omniroute-heal.service`,
`[Service]` `Environment=` lines); `make start` rewrites the unit itself.

## Remote setup

Any other machine on the LAN can configure its own Pi/OpenCode sessions
against this host's OmniRoute gateway with one command:

```bash
curl -sS http://<this-host>.local:<remote-setup-port>/setup.sh | bash
```

It prompts for `OMNI_ROUTER_MASTER_KEY` (set in this repo's `.env`) and, once
authenticated, writes a scoped OmniRoute API key to the remote machine's
client configs — it never hands out the master key itself. `make status`
(after `make start`) prints the exact one-liners (mDNS and LAN-IP forms) plus
every other credential you need — OmniRoute's dashboard password and, when
GPU inference is enabled, this host's own API key — under one banner. Run
`make show-secrets` for the same credentials without starting anything.

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

## Ornith 35B acceptance check

`ornith-35b` (opt-in, disabled by default) is a mixture-of-experts model too
large to fit fully in most consumer GPUs' VRAM; it uses `--n-cpu-moe` to keep
some expert weights in host RAM. Unit tests cover the RAM/VRAM split
arithmetic but cannot verify the actual container image's flag handling or
real hybrid CPU/GPU loading, so verify it manually after any change that
touches MoE offload, the resource ceilings, or the Ornith 35B model entry:

1. Select ONLY `ornith-35b` (`select`, not `enable` — `enable` leaves
   whatever was previously selected also enabled, and `make setup` defaults
   to the first enabled model, which can silently re-pick it instead):
   ```bash
   uv run llmenv.py --config ~/.config/llm-env/models.yml models select ornith-35b
   uv run llmenv.py --config ~/.config/llm-env/models.yml models list \
     | jq -r '.models[] | select(.enabled) | .alias'
   # must print exactly: ornith-35b
   ```
2. `make setup` — must complete without the VRAM/RAM budget gate firing, at
   the recommended `n_cpu_moe: 28` / `memory_ceiling_pct: 60`.
3. `make restart` (on a running stack `make start` skips the budget gate and keeps the old llm-server) — must reach a healthy state (`make check-server` passes).
4. `make benchmark` — measures every enabled model (here, just `ornith-35b`)
   pinned to the configured GPU device, using its own presets-sourced
   `--n-gpu-layers`/`--n-cpu-moe` and its `check_ctx_size`/
   `client_max_output_tokens` for `-p`/`-n` (there is no standalone
   `llama-bench` binary — this runs `llama bench`, a subcommand of `/app/llama`
   in the image, and does not run against the already-started `llm-server`
   container). Results land under
   `.models[] | select(.alias == "ornith-35b") | .benchmark.vulkan` in
   `models.yml` (`pp_tps`, `tg_tps`, `measured_at`) — read it with:
   ```bash
   yq '.models[] | select(.alias == "ornith-35b") | .benchmark.vulkan' \
     ~/.config/llm-env/models.yml
   ```
   Compare `pp_tps`/`tg_tps` against the baseline recorded in
   `docs/superpowers/specs/2026-08-10-ornith-35b-moe-incorporation-design.md`
   (`n_cpu_moe 28`: pp @ depth 0 ≈ 441 tok/s, tg @ depth 0 ≈ 39 tok/s) — note
   that baseline used a short `-p 512 -n 128 -d 0,32768` probe, while
   `make benchmark` now measures at the model's full configured context size,
   so a lower `pp_tps` at a much larger `-p` is expected, not a regression by
   itself. A large regression indicates the offload split, quantization, or
   `n_gpu_layers` changed, not just the budgeting arithmetic.
5. During a generation request against the running server, confirm
   sustained CPU utilization (`podman stats llm-server`) is in the same
   ballpark as the design doc's measurement (~43-44% average across host
   threads) — much higher suggests `cpu_ceiling_pct` isn't actually being
   applied (compare `podman inspect llm-server`'s `cpus`/`NanoCpus` against
   `resources.llm_server.cpus` in `models.yml`).
