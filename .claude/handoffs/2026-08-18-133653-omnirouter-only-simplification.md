# Handoff: OmniRoute-only simplification (drop local llm-server/GPU dependency)

## Session Metadata
- Created: 2026-08-18 13:36:53
- Project: /var/home/bazzite/git/llm-env
- Branch: main
- Session duration: long, multi-day session (spans the Remote Agent Setup feature build/merge, post-merge hardening, and this strategic-direction discussion)

### Recent Commits (for context)
  - 147bbec fix(remote-setup): serialize key issuance across processes; parse the Host header
  - 767d1e2 fix(remote-setup): keep the scoped key out of argv and validate Pi targets
  - 8aa88dc fix(compose): publish the secret-bearing compose file atomically
  - d0433cc fix(omniroute): require an API key on the LAN-published client API
  - 41ed6dc fix(remote-setup): address final review findings (.env gitignore, /api/keys shape tolerance, logging+timeout)

## Handoff Chain

- **Continues from**: None (the auto-linked `2026-07-28-064440-transparent-checks.md` is an unrelated, older piece of work — a different feature area entirely. Do not treat it as context for this thread.)
- **Supersedes**: None.

## Current State Summary

The "Remote Agent Setup" feature (an `omniroute` compose service acting as an
LLM router, plus a `remote-setup` service that serves a one-command LAN
installer) was fully implemented, reviewed (multiple rounds, including two
independent external reviews), and **merged to `main`** at commit `147bbec`.
That work is done and committed.

After the merge, in direct follow-up conversation (not a formal plan), three
more features were implemented and tested but **never committed** (see
"Files Modified" below) — `make status` now prints the same endpoint banner
as `make start` (including the OmniRoute dashboard password, per an explicit
user decision to expose it since the master-key gate already protects it), a
`SIGTERM`/PID-1 shutdown-hang bug in `remote-setup` was fixed, and the
remote installer now updates OpenCode's favorites file when one already
exists on the remote machine (mirroring `make setup-local-llm-agents`).

Then the conversation pivoted into a **strategic/architecture discussion**,
still in progress, that has NOT resulted in any code changes yet:

1. User asked about running this stack on a machine with **no GPU at all**,
   using it purely as an OmniRoute router + remote-setup installer — no
   local llama.cpp inference ever. I ran two Explore agents + one Plan agent
   and wrote a full implementation plan to
   `/home/bazzite/.claude/plans/cryptic-bouncing-wren.md` (see below — this
   plan file still exists and was fully designed with the user's answers to
   3 clarifying questions baked in, but **the user interrupted `ExitPlanMode`
   and did not approve it** — they pivoted to a different question instead).
2. The pivot: user asked whether there's a real advantage to running
   OmniRoute in front of OpenRouter vs. just pointing clients at OpenRouter
   directly, specifically in a "frontier-cloud-models-only" scenario (named
   models: Kimi K3, Qwen3.8-27B). I researched this (WebSearch) and gave a
   balanced answer: OpenRouter's own Management API (scoped per-key access,
   spend caps) and its **Presets** feature (named, dashboard-managed model
   configs referenced via `@preset/slug`) close most of the gap that used to
   be OmniRoute's unique value — but OmniRoute still wins if the user wants
   provider-agnostic flexibility (mixing OpenRouter with other backends
   later, or reviving local inference) and reuses the `remote-setup`
   distribution flow already built and hardened this session.
3. **This handoff was requested specifically to preserve the conclusion the
   user reached**, stated in their own words when invoking `/session-handoff`:
   > "Lo importante es que guardemos todo lo que sabes de mis intenciones de
   > solo mantener omnirouter para simplificar la config y la carga"
   (Save my intent to keep ONLY OmniRoute, to simplify config and load.)

## Codebase Understanding

### Architecture Overview

- Compose stack has 3 services today: `llm-server` (llama.cpp, GPU-backed),
  `omniroute` (LLM router — local proxy in front of any backend, local or
  remote), `remote-setup` (LAN installer server, master-key-gated, issues
  scoped OmniRoute API keys to remote machines). `remote-setup` depends only
  on `omniroute`; `omniroute` currently depends on `llm-server` being
  healthy (see the unapproved plan for why/how that would change).
- `pylib/compose.py::render_compose()` is the single source of truth for the
  rendered `docker-compose.yml` — re-rendered fresh on every `make start`
  via `setup/render-unit.sh`. Per-model `enabled` toggles already drive
  conditional inclusion this way; the unapproved plan proposes the same
  pattern for a new `llm_server.enabled` config key.
- `pylib/remote_setup.py`'s `SETUP_SCRIPT_TEMPLATE` (a big embedded bash
  heredoc) is the actual code served at `GET /setup.sh` to remote machines —
  most "remote installer" changes this session were edits to this string,
  not a separate script file.

### Critical Files

| File | Purpose | Relevance |
|------|---------|-----------|
| `pylib/compose.py` | Renders `docker-compose.yml` from `models.yml` | Where "llm-server optional" would be implemented (see unapproved plan) |
| `pylib/remote_setup.py` | The `remote-setup` HTTP server + embedded installer script | Heavily modified this session (dashboard password exposure, SIGTERM fix, OpenCode favorites) — all uncommitted |
| `scripts/status.sh`, `scripts/print-endpoints.sh` (new, uncommitted) | `make status` output | `print-endpoints.sh` is a NEW file, currently untracked in git |
| `setup/setup.sh` | Interactive `make setup` flow, 8 steps | Hard-requires a GPU today (dies with 0 GPUs); this is what the unapproved plan targets |
| `scripts/start.sh` | `make start` orchestration | Currently nests omniroute's health-wait/provisioning INSIDE llm-server's health-wait `if` block — the key structural blocker to an omniroute-only start |
| `pylib/omniroute.py` | OmniRoute provisioning (`build_payload`/`provision`) | Hardcodes `http://llm-server:{port}/v1` as the provider target — must be skipped, not just failed-soft, if llm-server is ever disabled |
| `/home/bazzite/.claude/plans/cryptic-bouncing-wren.md` | Full unapproved implementation plan for GPU-optional setup + omniroute-only start | **Read this file in full before doing any related implementation** — it has the complete file-by-file design already worked out and user-confirmed answers to 3 open questions |
| `.agents/architecture.md` | Living architecture doc (not a dated plan — keep it current) | Has uncommitted edits this session; also the place to document the eventual `llm_server.enabled` mode |

### Key Patterns Discovered

- **Living vs. dated docs**: `.agents/architecture.md` is treated as always-current truth and gets corrected whenever reality changes. `docs/superpowers/plans/*.md` and `docs/superpowers/specs/*.md` are dated historical records of a design decision at the time — NOT updated later even if a subsequent conversation revisits/reverses that decision (established convention this session: only `.agents/architecture.md` and `.gitignore` get corrected post-hoc, not the plan/spec docs).
- **Config-driven, re-rendered-per-run is the house style**: nothing in this repo uses a separate "compiled/cached mode" file — every toggle (per-model `enabled`, `omniroute`/`remote_setup` sections) lives in `models.yml` and is read fresh by `render_compose()` on every `make start`. The user explicitly confirmed (answering an open question in the unapproved plan) that `llm_server.enabled` should follow the same pattern: config is the ONLY source of truth, no separate runtime override flag.
- **`LLM_ENV_ASSUME_YES`-style env vars** are the established convention for non-interactive/scripted opt-in to setup behavior (see `setup/setup.sh:8`). The unapproved plan proposes `LLM_ENV_NO_GPU` following the same pattern, paired with an interactive prompt.
- **Security posture**: OmniRoute's own dashboard password IS now deliberately exposed to remote machines through `/config` (see decision below) — the design principle is "gate the endpoint with a strong secret (`OMNI_ROUTER_MASTER_KEY`), not the payload it returns."

## Work Completed

### Tasks Finished (this session, chronologically)

- [x] Remote Agent Setup feature: full implementation, multi-round review (including two independent external reviewers), fix waves for Critical/Important findings, scoped re-reviews — **merged to `main` at `147bbec`**.
- [x] `make status` extended to print the same endpoint banner as `make start` (new shared script `scripts/print-endpoints.sh`), plus Local/Network/mDNS forms for the remote-setup URL and both one-liner forms (mDNS domain + LAN IP) — implemented, tested (1085 tests passing at the time), **not committed**.
- [x] OmniRoute dashboard password now deliberately exposed by `/config` and printed by the remote installer (explicit user decision — see below) — implemented, tested, **not committed**.
- [x] `SIGTERM`/PID-1 signal-handling fix in `pylib/remote_setup.py` (`install_termination_handlers()`) — root-caused and fixed a real production incident where the `remote-setup` container got stuck in "Stopping" and had to be force-killed, because it runs as PID 1 with no init wrapper and Python doesn't install a SIGTERM handler by default. Verified against the real running container (stop went from 5-10s+SIGKILL to 0.4s clean). **Not committed.**
- [x] Remote installer now updates OpenCode's `model.json` favorites file when one already exists on the remote machine (never creates one from scratch, to avoid a hard `opencode --version` pin on unknown remote machines) — implemented, tested, **not committed**.
- [x] Investigated GPU-optional setup / omniroute-only start; wrote a complete plan to `cryptic-bouncing-wren.md` — **plan not approved, no code written**.
- [x] Researched OmniRoute-vs-OpenRouter-direct tradeoff and OpenRouter's Presets feature — pure discussion, no code changes.

### Files Modified (uncommitted — `git status` as of this handoff)

| File | Changes | Rationale |
|------|---------|-----------|
| `pylib/remote_setup.py` | Added `omniroute_dashboard_password` to `build_config_response`/`_handle_config`; printed in the installer's final banner; added `install_termination_handlers()` (SIGTERM/SIGINT → `server.shutdown()` on a separate thread) called from `main()`; added OpenCode favorites-state staging (only if the state file already exists on the remote machine) | Password: explicit user decision (see below). SIGTERM: fixed a real incident. Favorites: explicit user request, scoped to "only if already exists" per user's own chosen option |
| `tests/test_remote_setup.py` | New tests for all of the above (`test_build_config_response_shape` updated; `test_install_termination_handlers_shuts_the_server_down_on_sigterm`; `test_setup_sh_updates_an_existing_opencode_favorites_state` + 2 companion tests) | Regression coverage |
| `scripts/status.sh` | Calls new `scripts/print-endpoints.sh` when config exists | Make `make status` as informative as `make start` |
| `scripts/print-endpoints.sh` | **New file, currently untracked** — extracted shared banner logic (Local/Network/mDNS URLs + credentials for all 3 services) | Single source of truth, used by both `setup/network.sh` and `scripts/status.sh` |
| `setup/network.sh` | Now `exec`s `scripts/print-endpoints.sh` instead of duplicating the banner inline | De-duplication |
| `tests/test_shell.py` | New tests: `test_status_prints_the_same_endpoint_banner_as_network_sh`, `test_status_skips_the_endpoint_banner_when_unconfigured` | Regression coverage for the `make status` change |
| `.agents/architecture.md` | Documents `scripts/print-endpoints.sh`; corrects the "dashboard password never leaves the machine" claim to reflect the deliberate exposure decision | Keep the living doc accurate |
| `docs/superpowers/plans/2026-08-12-omniroute-profiles.md` (untracked) | Pre-existing, unrelated in-progress plan file from BEFORE this session's later work — **not something this session created or should touch** | Leave alone; belongs to separate, deferred work |

### Decisions Made

| Decision | Options Considered | Rationale |
|----------|-------------------|-----------|
| Expose OmniRoute dashboard password via `/config` + the installer's printed output | (a) keep it scoped-key-only, never expose password (original design); (b) expose it, gated only by `OMNI_ROUTER_MASTER_KEY` | User explicitly chose (b): "ya está gateado por el master key... es una forma fácil de acceso sin tener que recordar el password random" — deliberate convenience tradeoff, not an oversight |
| OpenCode favorites in remote installer: only touch if state file already exists | (a) never touch (original design, to avoid requiring `opencode` installed + exact version pin on unknown remote machines); (b) only touch if it already exists (no version risk since editing ≠ creating); (c) replicate local script's full create-with-version-pin behavior | User explicitly chose (b) — editing an existing file needs no version check at all; creating one does, and that risk isn't worth it on unknown remote hardware |
| `llm_server.enabled` (proposed, NOT yet implemented) should be config-only, no runtime override flag | (a) config-only single source of truth; (b) config + one-off override flag | User explicitly chose (a), confirming the repo's existing "one source of truth, re-rendered per run" pattern should extend to this new toggle |
| No-GPU `make setup` should skip model selection/download ENTIRELY (not just skip GPU steps while still letting you pick models) | (a) skip models entirely; (b) keep model selection, just don't use it until llm-server is enabled | User explicitly chose (a), and clarified the requirement further: at `make start` time, NO llama.cpp instance should run at all, GPU stays completely unused, only `omniroute` (+ `remote-setup`) start |
| **Strategic direction (this handoff's main point)**: keep OmniRoute as the router, do NOT switch to calling OpenRouter directly from clients | (a) drop OmniRoute, point Pi/OpenCode straight at OpenRouter (using OpenRouter's own Presets for the "combo" abstraction); (b) keep OmniRoute as the single local gateway, likely backed by OpenRouter (or other frontier providers) as its upstream, to simplify config and reduce load | **User's stated intent, verbatim reason for this handoff**: keep ONLY OmniRoute, specifically to simplify config and reduce load — i.e., the simplification target is dropping the LOCAL GPU/llama-server complexity (the GPU-optional-setup plan), NOT dropping OmniRoute itself. Do not misread this as "abandon OmniRoute for direct OpenRouter" — that option was discussed and NOT chosen. |

## Pending Work

## Immediate Next Steps

1. **Ask the user whether to commit the already-implemented, tested, uncommitted work** (make status banner, dashboard password exposure, SIGTERM fix, OpenCode favorites — see "Files Modified"). This has been offered multiple times in-session without a firm yes; don't assume — ask again at the top of the next session.
2. **Resolve the pending `git stash` before touching any more compose/config/lifecycle-script files** — `stash@{0}` (`main-wip-before-remote-agent-setup-merge-2026-08-13`) holds pre-existing WIP work (apparently related to the separate, unrelated `docs/superpowers/plans/2026-08-12-omniroute-profiles.md` effort) that genuinely conflicts with this session's changes to `scripts/clean.sh`, `setup/network.sh`, and `tests/test_shell.py`. It was deliberately left unresolved (conflicts require judgment calls this agent couldn't make unilaterally) — surface this to the user before it's forgotten; conflicts will only get harder to resolve the longer it sits.
3. **Given the user's confirmed direction (keep OmniRoute, drop local GPU/llm-server)**, the next real implementation task is almost certainly the plan already fully designed at `/home/bazzite/.claude/plans/cryptic-bouncing-wren.md` — but it was NOT approved (user interrupted `ExitPlanMode` to ask the OpenRouter question instead, and never returned to approve or reject it explicitly). **Re-confirm with the user that this plan should now proceed** before implementing — their strategic conclusion strongly implies yes, but get an explicit go-ahead (re-enter plan mode, re-present, or just ask directly) rather than assuming.
4. Once (3) is confirmed, the plan itself is complete and ready to execute as written — no further design work needed, just implementation following the plan file's file-by-file breakdown.
5. **Not yet discussed with the user, but implied by their direction and worth raising**: if OmniRoute's backend is going to be OpenRouter (or another frontier-only cloud provider), the actual OmniRoute *connection* configuration (in its dashboard, or via `pylib/omniroute.py`'s provisioning) needs to point at that provider instead of/in addition to `llm-server` — this is downstream of the GPU-optional-setup plan and hasn't been scoped at all yet. `pylib/omniroute.py`'s `provision()` today only knows how to provision a connection to `llm-server`; provisioning an OpenRouter connection would be new, unscoped work.

### Blockers/Open Questions

- [ ] Should the uncommitted work (item 1 above) be committed as one commit or split? (Not asked yet.)
- [ ] Does the user want the `git stash` conflicts resolved now, or deferred further? (Flagged, not resolved.)
- [ ] Is `cryptic-bouncing-wren.md`'s plan approved to implement? (Explicitly NOT yet — this is the most important open item.)
- [ ] How should OmniRoute connect to OpenRouter specifically — via OmniRoute's own dashboard UI (manual, one-time), or should `pylib/omniroute.py`'s provisioning be extended to automate an OpenRouter connection too? Not discussed at all yet.
- [ ] Does the user want to use OpenRouter Presets (the "combo" abstraction) configured manually in the OpenRouter dashboard, or is there an appetite for automating preset creation/management from this repo's tooling too (mirroring how `pylib/omniroute.py` automates OmniRoute connection provisioning)? Purely conceptual discussion so far — no automation scoped.

### Deferred Items

- `docs/superpowers/plans/2026-08-12-omniroute-profiles.md` / `docs/superpowers/plans/2026-08-12-omniroute-profiles.md` (untracked) — a separate, unrelated plan that predates this session's later work. Explicitly deferred per earlier-session notes; not touched, not in scope.
- Task #61 in the task list ("Benchmark comparativo Ornith 35B (Q4/Q5) vs Ornith 9B") — unrelated to this thread, still pending from much earlier work, unaffected by this session's direction change (though if the user commits to the no-GPU/OmniRoute-only direction, this benchmark task may become moot — worth flagging to the user rather than silently carrying it forward).

## Context for Resuming Agent

## Important Context

- **The user's core intent, in their own words**: "solo mantener omnirouter para simplificar la config y la carga" — keep ONLY OmniRoute, to simplify config and reduce load. Read in context, this means: drop the local llama.cpp/GPU-backed `llm-server` service and its setup complexity (GPU detection, Vulkan probing, VRAM budgeting, model downloads) — but KEEP OmniRoute running as the single gateway, most likely backed by OpenRouter (or another cloud frontier provider) rather than local inference. **This is NOT a decision to abandon OmniRoute in favor of calling OpenRouter directly from Pi/OpenCode** — that alternative was explored in depth (OpenRouter Presets as a "combo" equivalent, OpenRouter's own scoped-key Management API) and the user's handoff request itself confirms OmniRoute stays.
- The unapproved plan at `cryptic-bouncing-wren.md` is the correct next implementation step for the "drop local llm-server" half of this intent. Read it in full — it already has the user's confirmed answers to the 3 key design questions (skip models entirely; config is the only source of truth, no override flag; interactive prompt + `LLM_ENV_NO_GPU` env var).
- The "which upstream does OmniRoute route to" half (OpenRouter, Presets-as-combos) has NOT been scoped into any plan yet — it's still at the conceptual-discussion stage. Don't assume implementation details there; ask before designing.
- A LOT of tested, working code sits uncommitted in the working tree right now (see Files Modified). It is unrelated to the strategic pivot but was in-flight when the conversation turned. Don't lose it — and don't assume it should be discarded just because the direction changed; the SIGTERM fix and dashboard-password/favorites features are still valid regardless of the GPU-optional direction.

### Assumptions Made

- Assumed (not confirmed) that "simplificar la carga" refers to host resource load (not running a GPU-hungry llama.cpp container) rather than "cognitive load" of configuration — the surrounding conversation (GPU-optional setup discussion) strongly supports this reading, but it was not asked explicitly as a follow-up.
- Assumed the user still wants the previously-uncommitted work (status banner, SIGTERM fix, etc.) kept, since nothing in the strategic pivot conversation contradicted it — but this was never explicitly reconfirmed after the pivot.

### Potential Gotchas

- Don't confuse `gpu.backend: "cpu"` (an EXISTING, already-valid config value meaning "llm-server runs, but does CPU inference") with the NEW `llm_server.enabled: false` concept from the unapproved plan (meaning "llm-server doesn't run AT ALL"). They are different axes and the plan is careful to keep them separate — a future agent conflating them would misimplement this.
- The `depends_on: {llm-server: service_healthy}` edge on the `omniroute` service exists ONLY to sequence `omniroute provision` (so it can reach `llm-server`'s health before registering it as a provider) — it is not a hard technical compose requirement. Removing it when llm-server is disabled is safe, per `.agents/architecture.md:58-64`.
- `pylib/omniroute.py`'s `build_payload()`/`provision()` hardcode `http://llm-server:{port}/v1` — if llm-server is ever disabled, this call must be SKIPPED outright, not attempted-and-logged-as-a-failure, since there's nothing valid to provision.
- The git stash from the merge (`stash@{0}`) has REAL conflicts with this session's changes to `scripts/clean.sh`/`setup/network.sh`/`tests/test_shell.py` — don't attempt `git stash pop`/`apply` casually; the conflict markers were seen once already (`<<<<<<< Updated upstream` / `>>>>>>> Stashed changes`) and require actual judgment about which side's logic should win, not a mechanical merge.

## Environment State

### Tools/Services Used
- `podman` / `podman-compose` (v1.6.0) — the actual compose runtime for this stack.
- `uv` — Python environment/runner for `llmenv.py` and pytest.
- Hardware: AMD Radeon RX 9070 XT (16304 MiB VRAM), ~30GB system RAM — this is the machine currently running the full 3-service stack in GPU mode; the GPU-optional work is aimed at a DIFFERENT/future machine profile, not necessarily replacing this one's current setup.

### Active Processes
- As of the last check this session, `llm-server`, `omniroute`, and `remote-setup` containers were all `Up`/`healthy` on this machine (the SIGTERM fix was verified live against the running `remote-setup` container).

### Environment Variables
- `OMNI_ROUTER_MASTER_KEY` (in `.env`, gitignored) — gates `remote-setup`'s `/config` endpoint.
- Proposed (not yet implemented): `LLM_ENV_NO_GPU` — would follow the existing `LLM_ENV_ASSUME_YES` convention for non-interactive opt-in to the no-GPU setup path.

## Related Resources

- `/home/bazzite/.claude/plans/cryptic-bouncing-wren.md` — full unapproved implementation plan, read this first for any GPU-optional/omniroute-only work.
- `.agents/architecture.md` — living architecture doc, has uncommitted edits this session.
- `docs/superpowers/specs/2026-08-12-remote-agent-setup-design.md` / `docs/superpowers/plans/2026-08-12-remote-agent-setup.md` — dated design/plan docs for the ALREADY-MERGED Remote Agent Setup feature (historical record, not to be edited for the new direction).

---

**Security Reminder**: Before finalizing, run `validate_handoff.py` to check for accidental secret exposure.
