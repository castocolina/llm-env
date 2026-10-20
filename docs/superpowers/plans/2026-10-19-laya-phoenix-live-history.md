# Real Laya history in Phoenix — corrective implementation plan

> Execute sequentially with TDD, actual runtime/browser evidence and the invocation-local brief. This is the user's requested working observability feature, not a visual trial.

## Controlling requirements

The user explicitly wants **everything available from real Laya calls** in the dashboard, not an empty UI or synthetic examples. Capture actual state, questions, typed answers, probability/confidence fields, model/routing metadata, usage/truncation, timings, errors and batch structure. Keep authentication headers/credentials out. Do not ask the already-answered metadata-versus-payload question again.

Normal setup/start/stop/status and boot must include Phoenix exactly when System One/Laya is enabled. Remove the five trial lifecycle commands from ordinary UX. Keep rootless execution, loopback publication, persistent owned SQLite volume, bounded resources and no off-host telemetry. Starting Phoenix alone is not completion. Prior standalone-only/no-instrumentation clauses and the October 19 normal-start plan's corresponding exclusion are superseded by this explicit requirement; preserve those historical plans unchanged.

Kimi returned actual HTTP 429 exceeded_current_quota_error. The user has already authorized the fallback: **openai/gpt-6-luna:high**. No more model-selection questions or narration are needed; no Kimi retry, no OpenCode Luna, no additional implementation agents or reviewer seats.

## Architecture and implementation constraints

- Research the installed **Laya 0.4.2** serving/router/hook contracts. Preserve device selection, offline checkpoint loading/preload, admission/concurrency, idle unload, schema/status/headers and batch behavior. Never replace GPU inference with a test double in the actual deployed server.
- Capture server-side so all callers are covered. An ASGI request/response observer around the original app plus supported native hooks is a plausible minimal approach; choose from measured installed contracts, not latest README assumptions. Keep all captured information and failure/truncation distinctions that can actually be observed.
- Make request state/questions and typed outputs inspectable in Phoenix native input/output/detail views, not only in an invisible sidecar file. Attach numeric attributes for usable native metrics/filtering where supported. Distinguish HTTP roots, prediction children, questions, results and batch items; do not double-count tokens or requests. Unknown is not zero; confidence is not correctness.
- Export must be bounded and must not block/fail inference when Phoenix is down. A container-local bounded queue/worker with explicit dropped/error counters and finite transport timeout is acceptable; no host tracing SDK/global installation, proxy/default remote fallback, resident host monitor or request interceptor service. Document bounded payload truncation visibly rather than silently presenting a full record. Container-only pinned dependencies are allowed if needed; prefer stdlib when sufficient.
- Preserve the original stable synthetic verifier/recovery tools for tests, but do not present their records as real user history. Give real traffic its own clearly named Laya project.
- Existing native main pod shares network namespace. Research actual rootless connectivity: do not assume host.containers.internal reaches host loopback or that separate service networks isolate a shared pod. Prefer a proven internal shared trace network between the Laya pod and separately isolated Phoenix, with no external default route on Phoenix; do not expose the dashboard to LAN or silently enable egress. If topology requires broad stack disruption, report that boundary rather than force it.
- Resource-policy initial validation: Phoenix soft memory 256 MiB, hard 1024 MiB, CPU shares 256 and hard 2 CPUs. Account for its reservation in host budgeting while Laya is enabled. These are tested policy choices, not guaranteed minima or production capacity claims. Adjust only from measured capped operation. Preserve existing Laya GPU/CPU/RAM constraints.
- No fabricated historical backfill. Prior requests are recoverable only from an existing suitable record; none has been verified. History starts at instrumentation deployment otherwise. Do not bulk read/send old private prompts/logs to the coding model.

## Task 1 — Preserve partial work and prove the observer

- [ ] Inspect/preserve Kimi's partial config/resources/start/lib/normal lifecycle additions and their tests; no reset. Keep unrelated updater script/test hunks, all plans and raw evidence exact.
- [ ] Fetch relevant current TypeSafe API/Choice/Score/Noul/confidence guidance; verify actual installed serving source and native hooks. Save new research under repo tmp.
- [ ] Write failing request/response/hook tests with synthetic owned inputs: success for all primitive types, full inspectable input/output, usage/truncation, routing/timing, errors before and after prediction, concurrent isolation, batch counts, credentials redaction, payload/queue bounds and collector outage. Observe genuine red.
- [ ] Implement a minimal server-side observer/exporter and serving entrypoint preserving the real Laya app/router. Container integration must be rebuildable and detect stale image code rather than relying on a live bind patch.

## Task 2 — Working lifecycle, network and resource integration

- [ ] Add red tests for unset/inherited /tmp TMPDIR before slow work, enabled/disabled Laya modes, owned idempotence/foreign refusal, normal and boot lifecycle, resource budget and actual finite cap rendering.
- [ ] Fix default-temp bug; normal dashboard startup must not require/build synthetic sender. Reuse cached pinned image, validate early.
- [ ] Complete normal setup/start/stop/status/endpoints and container image/config wiring for real export. Preserve persistent history on ordinary restart/stop; no volume deletion or silent adoption. No new dashboard enable question independent of Laya.
- [ ] Prove isolated real network/engine/cgroup operation and collector outage behavior before any live System One change. Existing OmniRoute/remote-setup identities and user-viewed private trial must remain undisturbed.

## Task 3 — Deliver observable real traffic, not merely code

- [ ] Build and test the actual instrumented image against prepared local checkpoints; no substitute model/CPU fallback disguised as the real service.
- [ ] Apply only scoped System One + Phoenix deployment if safely supported without broad stack restart; inspect exact affected IDs/resources and existing dependencies first. Do not touch OmniRoute connections, keys, configuration or service lifecycle. If safe scoped deployment cannot be established, stop with concrete evidence and the exact remaining operator action.
- [ ] Send a harmless uniquely marked request through the actual normal Laya endpoint, not directly into Phoenix. Verify actual model response and resulting trace input/questions/answers/probabilities/usage/status/timings in Phoenix API AND browser. Cover batch and an error without fabricating extra successful model calls.
- [ ] Recreate/restart only the owned Phoenix instance against the same image/volume; verify the same real trace content survives without resending inference. Confirm later normal-client calls are collected server-side.
- [ ] Measure CPU/RAM/cgroups and logical database size with timestamps, document current dashboard URL/project, latency overhead, retention policy and any unobservable field/native-chart limitation. Screenshots must filter to the worker's harmless marker; keep other users' payloads local and out of model tool output.
- [ ] Run make validate, full normal offline make test with live opt-in unset, one serialized opt-in container/browser E2E entry point and whitespace checks including untracked files. Map remaining prior GLM findings honestly.

## Final gate

Coordinator verifies evidence/preservation and obtains the already-requested independent GLM code review with per-finding local-Laya advisories. No plan-review loop or new approval ritual. Report only what actually works, what is still untested and the instrumentation start time. A running empty Phoenix or synthetic-only dashboard cannot pass this plan.
