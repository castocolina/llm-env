# Local Laya historical-usage dashboard research

Date: 2026-10-18
Status: Research and planned trial only. No dashboard installed, package upgraded, telemetry enabled, or inference generated. User prioritizes request/decision metrics and prefers containerized components wherever possible; hardware charts are useful but secondary. Saved Phase A [design](../specs/2026-10-18-phoenix-laya-dashboard-trial-design.md) and [implementation plan](../plans/2026-10-18-phoenix-laya-dashboard-trial.md). A possible Claude Opus high-effort review is optional and deferred; no review/retry is scheduled.

## Bottom line

Given the user's comments, trial **Phoenix in its own rootless Podman container with persistent SQLite** first. Request counts, latency, success/errors, typed decision metadata/results, confidence and time distributions are the primary requirements; CPU/RAM/GPU/VRAM are secondary. Validate which aggregates and custom-attribute views Phoenix actually provides before committing to it. For custom aggregate/resource dashboards, **Prometheus + Grafana** remains an alternative or later complement, not an automatic second installation. Neither automatically observes this Laya deployment just by installing a dashboard.

Prefer containers for the dashboard, any later collectors/storage, and the temporary test-data sender. Do not install dashboard runtimes or tracing SDKs on the host. Containers still require explicit persistent volumes for history.

No official Laya usage-history dashboard or request-history database was found in the checked repository/docs or installed server. Do not confuse unrelated GitHub projects called Laya, a Hugging Face demo, or TypeSafe's hosted service with this local server.

## Verified deployment and upstream

- Local image: `localhost/llm-env-system-one:rocm`, container `system-one`.
- Project Dockerfile pins `laya[serve]==0.4.2` with ROCm PyTorch. The project's cache volume is for Hugging Face weights, not inference history.
- Installed sources inspected read-only: `/usr/local/lib/python3.11/site-packages/laya/serve.py` and `hooks.py`.
- Actual project: https://github.com/NandhaKishorM/laya (Apache-2.0), official docs https://nandhakishorm.github.io/laya/ . The current main README discusses 0.4.11; do not assume every current example is identical to pinned 0.4.2.
- Laya is an independent Jev-compatible typed-decision engine. Its HTTP protocol is `state` + typed `questions` → `answers` + `usage`; compatibility with `/v1/systemone` is not OpenAI chat-completions compatibility.
- Installed `serve.py` registers `/health`, `POST /v1/systemone`, and `POST /v1/systemone/batch`. No `/metrics`, dashboard, or historical-usage query route is registered there.
- Single inference responses expose `usage` and timing headers `Server-Timing` / `X-Inference-Time-Ms`; batch responses expose aggregate usage. These values are returned, not automatically retained in a DB.
- `/health` reports residency/revisions/device, CPU fallback counts and idle-unload information. These are current process state, not durable historical request/token totals.
- Installed `hooks.py` exposes `PredictContext.run_id`, `model`, `usage`, `elapsed_ms`, `error`, routing information and start/end/error lifecycle methods. The documented run ID is not automatically persisted.
- A limited read-only search under `/home/laya` and `/app` found no `.sqlite`, `.db`, or `.jsonl` files; combined with the server's code this supports no built-in request-history store in this deployment. It does not prove no unrelated file exists anywhere in the container.
- Existing uvicorn access logs record paths/status; they do not constitute a complete token/latency/decision history. Historical token counts and decision outputs cannot be reconstructed if they were never collected.

## Official instrumentation support, not an official dashboard

Sources:
- https://github.com/NandhaKishorM/laya/blob/main/docs/hooks/tracing.md
- https://github.com/NandhaKishorM/laya/blob/main/examples/hooks/otel.py
- https://github.com/NandhaKishorM/laya/blob/main/examples/hooks/audit.py

The official project includes an OpenTelemetry counter/histogram example and a tracing guide, plus an audit example that prints a record per decision. These are integration examples, not a preconfigured collector, database, or UI. The OTel example's default counter/list are in-memory and dependency-free, not a persistent monitoring service.

The tracing guidance recommends `hooks_raise=False` and warns that blocking exporters stall prediction. Production instrumentation should enqueue bounded metadata and export outside the inference critical path, count dropped telemetry, and keep inference working if the collector is unavailable. Batch cardinality matters: count HTTP calls, batch items, questions and token totals separately; avoid counting router lifecycle contexts as additional predictions.

Installed `serve.create_app(router=...)` supports operator-supplied routers. `build_router()` preserves environment-based model/device/preload settings. `laya-serve` does not have a verified environment setting that enables arbitrary tracing hooks automatically; a tested local ASGI/bootstrap wrapper or application instrumentation is required. Do not silently rebuild the router with different residency/device defaults to add telemetry. An HTTP wrapper can capture rejects/HTTP timing, whereas prediction hooks capture decisions; complete observability may need both.

## Candidate comparison

| Candidate | Best fit | Persistence/deployment evidence | Laya integration needed |
| --- | --- | --- | --- |
| **Phoenix** | Browse individual requests/traces, latency/errors, input/output token metadata, optional typed answers and confidence | Official self-host docs support one Phoenix container with SQLite on a persistent volume; PostgreSQL is optional. Configurable trace retention is documented. | Manual OpenTelemetry/OpenInference spans from hooks/HTTP wrapper. No native Laya auto-instrumentor was verified. Typed questions/answers are custom structured metadata, not chat generations. GPU metrics require a separate collector. |
| **Prometheus + Grafana** | Historical request rate, token/decision throughput, p50/p95 latency, errors, concurrency, CPU/RAM/AMD GPU and VRAM | Prometheus stores time series with configured retention; Grafana queries and visualizes them. Requires persistent volumes. | Laya metrics exporter/hook is needed; there is no installed `/metrics`. AMD GPU data must use an AMD-compatible exporter or measured sysfs collector, not NVIDIA DCGM. No individual decision browser by default. |
| **Langfuse** | Broader prompt/trace/evaluation workflows shared across multiple apps | Current official compose lists web, worker, PostgreSQL, ClickHouse, Redis and object storage (MinIO). More services than the SQLite Phoenix option. | Manual SDK/OTel integration required; not a transparent Laya-compatible inference proxy. Attractive if standardizing observability across the whole stack, not the simplest single-server starting point. |

Sources:
- Phoenix: https://github.com/Arize-ai/phoenix
- Phoenix Docker/SQLite: https://arize.com/docs/phoenix/self-hosting/deployment-options/docker
- Phoenix configuration/retention: https://arize.com/docs/phoenix/self-hosting/configuration
- Source verification: `docs/phoenix/self-hosting/deployment-options/docker.mdx` and `configuration.mdx` in the Phoenix repository. `PHOENIX_WORKING_DIR` on a volume preserves SQLite; `PHOENIX_DEFAULT_RETENTION_POLICY_DAYS` is documented with default 0/infinite.
- Prometheus: https://github.com/prometheus/prometheus
- Grafana: https://github.com/grafana/grafana
- Langfuse: https://github.com/langfuse/langfuse and its current `docker-compose.yml`

All three project families showed recent repository activity during research; activity alone is not proof of suitability or performance. No runtime overhead/resource figures were measured and none should be invented.

## Recommendation and boundaries

1. If 'historical usage' means **what ran, how often, how long, tokens, success/failure and which checkpoint**, start with a metadata-only Phoenix proof of concept backed by a persistent local SQLite volume. Confirm UI aggregation and custom attribute visibility with Laya-shaped traces before committing to deployment.
2. If it means **operational charts, including GPU utilization/VRAM**, start with Prometheus/Grafana instead; Phoenix alone does not meet that GPU-history requirement.
3. Do not install both stacks before clarifying the primary need. Do not choose Langfuse solely for a single small local server without a broader workflow requirement.
4. Default to local-only binding and metadata-only capture. Raw state, questions and answers may be sensitive; enable content retention only through explicit consent, with bounded retention/disk use.
5. Telemetry must not keep sleeping models resident, trigger inference during scrapes, or alter the existing Laya idle-unload behavior.
6. Upgrading Laya, replacing its inference runtime, adding OmniRoute proxying, and deploying a dashboard are separate changes, not consequences of this research.

## Proposed container-first trial based on user feedback

This is a proposal, not a deployed dashboard or an approved permanent integration.

1. Run a version-pinned official Phoenix image under rootless Podman in an isolated trial project, bound only to a free loopback port. Give it a dedicated named volume for `PHOENIX_WORKING_DIR`/SQLite, explicit bounded retention, and no GPU access or privileged mounts. It is one persistent dashboard service; SQLite does not require a separate database server.
2. Use a short-lived sender container to submit clearly labeled synthetic traces with OpenInference-style fields and Laya-shaped metadata: success/error cases, different durations, token counts, `choice`/`score`/`noul`, batch item and question counts, and separately named confidence fields. The saved Phase A plan uses Phoenix 20.23.0's verified JSON REST span API, not an OTLP exporter; real OpenTelemetry integration remains later work. This phase makes no Laya inference calls and leaves production containers/configuration unchanged.
3. Evaluate request browsing, time filtering, count/error/latency aggregates, token totals, and how much custom charting confidence/type/result distributions actually support. Do not promise a built-in savings panel or arbitrary histogram support without demonstrating it in the pinned UI.
4. Restart/recreate only the dashboard against the same volume and verify records survive. Measure its actual CPU/RAM/disk use and bound retention. Removing the trial container must not implicitly remove its data volume.
5. If the UI fits, approve a small real-request test from an instrumented **client container** to the existing Laya service. That does not require restarting Laya, but records only requests from that client; do not represent it as server-wide collection. Check batch accounting, HTTP versus inference timing, rejects and exporter failure behavior.
6. For comprehensive collection across all callers, separately design/test server-side HTTP instrumentation plus prediction hooks in the Laya image. That later change normally requires replacing/restarting `system-one`; do not make it part of starting the dashboard. Preserve device/residency/offline/admission behavior. Export asynchronously with bounded buffering.
7. If custom aggregate plots or hardware history remain unmet, test a containerized Prometheus/Grafana stack and narrowly scoped AMD/container collectors as a separate phase. A dashboard container alone cannot read GPU/host metrics automatically; verify rootless permissions rather than granting broad access.

### What the metrics mean

- Count HTTP calls, batch items and individual questions separately. Track client-observed latency and server inference timing under distinct names; neither substitutes for the other.
- Record existing response fields rather than inventing a generic confidence number. Installed `serve.py` distinguishes Laya `confidence` (normalized-entropy based), `x_jev_confidence`, and `answer_confidence` where available; strict mode removes some fields. Missing is unknown, not zero. Confidence is not measured correctness.
- Time/token/money savings require an explicit comparison baseline for equivalent work, token-accounting definitions, and stated prices. Mark counterfactual estimates separately from observed usage. Local GPU inference has no provider invoice by itself, but it is not automatically free electricity/hardware. No fabricated savings from request counts alone.
- Default to metadata-only retention; safe synthetic result values are fine for the trial. Retention of real state/questions/result labels needs explicit consent because even a short result can be sensitive.

### SQLite, current history and uptime

Phoenix's SQLite would be a **new dashboard database**, not an existing Laya DB moved into a volume. The inspected Laya deployment mounts only `llm-env-system-one-cache` at `/home/laya/.cache/huggingface` for weights. No built-in durable request-history store was found in installed sources or the limited filesystem search; this is not proof that no unrelated DB file exists anywhere.

Starting Phoenix and its test sender does not require changing/restarting Laya, so it does not reset Laya's current uptime. Existing access logs may retain partial evidence, but cannot reconstruct token/decision details that were never recorded. New durable history starts when collection starts. A later server-side instrumentation restart resets process-local counters/uptime, while already exported history survives in Phoenix's separate volume; record the cutover timestamp and distinguish the old/new process lifetimes.

## Remaining verification before implementation

- Obtain approval for the isolated synthetic-data Phoenix trial; this does not authorize live inference or changing Laya.
- Check current pinned Phoenix image, resource footprint, rootless Podman/SELinux behavior and retention control in a disposable deployment.
- Demonstrate end/error/batch hook semantics against installed 0.4.2, and instrument HTTP rejects separately from prediction failures.
- Measure instrumentation overhead, bounded export/backpressure behavior, and restart persistence with synthetic non-sensitive fixtures.
- For GPU metrics, identify the host's real AMD devices dynamically and measure the chosen collector's access requirements. Never grant broad host access just to make a dashboard work.
