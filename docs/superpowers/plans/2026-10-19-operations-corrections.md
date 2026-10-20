# OmniRoute update safety and Phoenix operational integration

> Execute sequentially with TDD using the already authorized sole OpenAI Luna implementer; coordinator reviews and commits coherent changes on main. Preserve earlier plans and evidence. This corrective request is approved by the user's explicit instructions to fix the updater, integrate operational targets, audit resources, provide healthchecks, and commit necessary work.

## Goal and boundaries

Stop `make update-omniroute` from ever restarting the whole stack. Finish the operational integration of real Laya history in Phoenix, report actual used resources separately from reservations/limits, and give every configured container a real healthcheck. Necessary product changes must be committed; demonstrably unnecessary throwaways must not remain as product files. Existing valid plans/research/raw evidence are not throwaways.

The user has not authorized bypassing idle safety, erasing history, making paid provider requests, changing host networking/security, or silently migrating/recreating siblings to make an update possible. Kimi's actual quota error already authorized the OpenAI Luna fallback; no model-selection question or further Kimi attempt. No additional implementation/reviewer seats. One final GLM seat covers exact new deltas and prior fixes; local-Laya advisories remain required for genuine review findings.

## Current measured facts

- Current updater calls scripts/stop.sh and scripts/start.sh at lines135/138. This definitely restarts the whole stack. Its index-versus-platform digest comparison is also wrong. Earlier approved updater design remains an unimplemented design, not a working capability.
- podman5.8.7 / podman-compose1.6.0. Native remote-setup Dependencies contains current OmniRoute ID. No dependency points to System One. Same native pod name does not imply shared netns in this installation.
- Forced provider recreation can affect dependents despite --no-deps. Native incoming dependencies cannot be removed from existing containers merely by projecting different YAML. No forced dependent removal/whole-stack fallback is allowed.
- Phoenix normal project is llm-env-phoenix, API project laya, loopback6006, separate owned internal network and persistent named volume. Root request ASGI observation pushes real records; Phoenix is not scraping an old Laya history database. Earliest verified existing trace is2026-10-19T18:59:45Z. Traces survived subsequent whole-stack recreation.
- Existing status lists Phoenix but does not query readiness/history/resources; logs omits its separate project; check-setup/check-server have no dedicated Phoenix checks. Phoenix lacks a container healthcheck. Other3 current services are healthy after external update/recreation; prior unhealthy root causes are not established by the new passing probes.
- Existing enforced memory soft/hard: OmniRoute1024/3072MiB, Laya2560/4608MiB, remote32/64MiB, Phoenix256/1024MiB. CPU weights1024/1024/1024/256; caps4/2/1/2CPUs. Soft reservation is not allocated RAM, shares are not reserved cores. Prior capped workload/peak evidence must be considered before lowering limits.
- Phoenix image entrypoint actually /usr/bin/python3.13; User0, Workdir/phoenix. Distroless, no shell/curl assumed. Healthcheck can use its verified Python runtime.

## Task1 — eliminate unsafe OmniRoute replacement

- [ ] Freeze existing updater/test bytes, then add failing behavioral tests proving no whole-stack lifecycle, no sibling mutation and no config migration during update.
- [ ] Implement complete-backend native incoming-dependency/ownership preflight before staging/lifecycle mutation, including stopped containers and uncertainty. Normal dependency-bearing topology must return75 with an actionable reason, original IDs/tag/state unchanged. Remove every unconditional whole-stack stop/start path; no --depend, --remove-orphans, force recreation, or hidden fallback.
- [ ] Use equivalent selected-platform config/image identities rather than index-vs-platform comparisons or version fallback. Preserve approved300 continuous observed idle seconds /10800 maximum wait with fail-closed uncertainty; do not substitute fixed sleep or fake an eligible activity profile.
- [ ] Only positively proven dependency-free, observed-idle deployments may perform an immutable target-only replacement following the preserved approved design. If no artifact capability profile exists, safely defer instead of claiming automatic update support. Do not implement a shortcut that updates busy/unknown deployments. Clearly distinguish safety fix/deferred current deployment from a fully qualified updater.
- [ ] Prove scoped success only where eligibility can actually be established with owned disposable fixtures; prove deferral for current normal dependency topology. No actual production update. Cleanup all test containers/pods in finally, retain named history volumes and diagnostic evidence.

## Task2 — meaningful Phoenix information in existing targets

- [ ] Add bounded local-only metadata query/computation through uv run llmenv.py, using documented pinned Phoenix endpoints. No content output, proxy/redirect leakage, credentials in logs, automatic inference in status, or unbounded page walks. Aggregates must name range/completeness/unknown values; never fabricate missing history or zero usage.
- [ ] make status retrieves Phoenix readiness/project/history metadata and actual per-container used/peak memory, soft/hard limits, CPU weights/caps, health/last failure when available. Preserve existing credentials UX without sending secrets to coding-model output. Do not conflate systemd active(exited) with live application health.
- [ ] check-setup validates pinned image/observer freshness/network/volume/cgroup configuration without assuming online services. check-server validates Phoenix readiness/read API and real Laya-to-Phoenix collection under the existing normal enabled/disabled rules. Missing/unhealthy/incomplete must not print a green verdict. Do not make paid provider calls just to test history.
- [ ] logs includes both main and Phoenix projects with bounded owned child cleanup on interruption, sensible service labels and no unrelated journal scraping. Update help/docs/tests; no new five-command Phoenix lifecycle surface.

## Task3 — all services have genuine healthchecks

- [ ] Add Phoenix /readyz check using the actual /usr/bin/python3.13 runtime with finite timeout, sensible start-period/interval/retries, no dependency installation or external address.
- [ ] Test success, readiness failure and startup grace, plus renderer coverage for every configured service in enable/disable combinations. Keep Laya GPU identity separate from mere HTTP health.
- [ ] Inspect available old health event metadata if useful without reading prompts/provider secrets. If prior failure details were destroyed with old containers, state that; do not manufacture a root cause from startup failure or resource reservations.
- [ ] Apply only Phoenix healthcheck recreation after exact record/volume checkpoint, verify real healthy state and same-record persistence. Do not restart OmniRoute/remote-setup/System One for a healthcheck already present.

## Task4 — measured resource audit, not guessed cuts

- [ ] Sample real cgroup current/peak/events/soft/hard and CPU data at idle and around harmless representative native Laya/Phoenix activity. Use retained load evidence, including OmniRoute pressure around700MB under former1GiB cap and Laya prior3155MiB peak. No paid provider load, no synthetic high-concurrency pressure on active services.
- [ ] Produce durable timestamped table distinguishing actual used RAM, lifetime peak, soft protection/reservation, hard cap and CPU weight/quota. Container recreation resets counters; current idle sample is not peak workload or supported capacity.
- [ ] Correct misleading documentation that claims guaranteed allocation/cores. Revise a number only when evidence supports the chosen policy; retain justified values rather than automatically shrinking working limits. State any proposed but undeployed values explicitly. No broad stack restart or risky hard-cap lowering to force a nicer-looking table.

## Task5 — history/telemetry truth, verification and Git hygiene

- [ ] Research exact installed Laya routes/hooks/audit integration and active instrumentation configuration. Check whether an existing durable history source is configured; do not broadly scan private logs or import old payloads without a verified source/provenance. Retained review receipts may represent a recoverable subset, never complete prior traffic. Explain request push versus resource sampling and what is/is not stored historically.
- [ ] Re-run validation/full offline suite with both live opt-ins unset; actual serialized default-env E2E; readonly status/checks/healthy/persistence and unchanged sibling identity evidence. Run no production updater replacement. All temporary experiment containers/pods cleaned up, no broad prune or volume/image/history deletion.
- [ ] Coordinator obtains same GLM exact-delta final review and per-finding local-Laya evidence. Necessary changes become logical Conventional Commits on main with exact staged-tree verification; no indiscriminate git add/stash/reset/clean/push. Keep all plans and unrelated user work. Only demonstrably throwaway product files may be archived byte-for-byte under ignored tmp and removed; do not delete working internal tools solely because they are untracked.

## Acceptance

Updater cannot restart siblings on ANY code path; current native-dependent deployment safely defers instead of pretending replacement succeeded. Normal commands actually query Phoenix and honestly expose health/history/used resources. Every configured live service has a passing check, Phoenix history survives its scoped recreation, resource conclusions are measured, all necessary work is committed, and no new orphan containers or unexplained dirty product files remain. Full old historical traffic, idle-period resource time series and unverified native charts must not be claimed.
