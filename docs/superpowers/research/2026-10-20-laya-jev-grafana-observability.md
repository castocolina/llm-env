# Laya/Jev and container observability (one interface, Grafana optional)

Date: 2026-10-20, reviewed and corrected 2026-10-24. Research only: not an
implementation plan, and nothing here was installed, started or reconfigured.

## Status

- **Written** 2026-10-20 by `openai/gpt-6-luna:high` and `kimi-coding/k3:high`
  (§8). **Reviewed** 2026-10-24 by an Anthropic model (Claude Opus 5.5) with
  read-only access to the repo, `tmp/` and the live stack (§8). The review's
  findings are merged into this file; there is no separate review document.
- **Verdict:** the semantics (confidence is not accuracy, unknown is not zero,
  separate denominators) and the recovered-data inventory stand. The original
  collector/store proposal was replaced by footprint-labelled options and a
  phased recommendation (§5).
- **Decided as the research recommendation** (not an approved plan):
  phase 0 is a user timer running a new `uv run llmenv.py observe` every 60 s
  that writes JSONL, with no resident service; phase 1 adds VictoriaMetrics
  single node; phase 2 adds VictoriaLogs; Grafana comes only if a single UI is
  demanded; Phoenix stays until the retirement criteria in §5 hold. Collection
  is host-side, not a sidecar per container. The proposed privacy default is
  hash by default, content opt-in (§7), pending the owner's answer.
- **Open:** the seven owner questions in §12.

### Superseded statements

Each line names the 2026-10-20 statement and where its correction lives.

1. "These files intentionally use the `./.docs/` path": committed under `docs/` in `f7fadd0`; an untracked `.docs/` copy remains (§12 Q5).
2. "The helper sends one request, defaults to OpenCode Zen and never makes a second call": since 2026-10-23 the client escalates per item laya, typesafe, opencode Zen with consent (§3).
3. "A fallback flag is not a fallback request; no automatic Laya-to-Jev cascade": the client now cascades automatically, but on this host only Laya is usable (§3).
4. "No Pi/OpenCode environment variable or hook API was qualified": Pi, OpenCode, Claude Code and Cursor session sources are now qualified (§2).
5. "Grafana can be the only interface" with Prometheus + Loki as the base: Grafana is optional and comes last (§5).
6. "Prometheus supplies numeric metrics" for every owned container: no packaged exporter supplies the cgroup limit fields; the repo's own sampler does (§5).
7. Alloy `loki.source.journal` works "without privileged recipes": Alloy documents that it needs the `adm` and `systemd-journal` groups (§6).
8. OmniRoute has only JSON usage routes: `main` also has an OTLP GenAI span exporter, unverified on the deployed version (§5, §6).
9. "Assess the Loki-first option": VictoriaLogs is preferred on this host for its disk cap; Loki stays acceptable (§5).
10. "Preserve bounded full content; metadata-only would not meet the inspection need": proposed default is hash plus shape, content opt-in (§7, §12 Q1).
11. "All 49 current spans": Phoenix held 5,542 spans on 2026-10-24, still with zero `session.id` (§1).
12. The "shared collector" wording: clarified, see §5.

## Scope and related research

The user wants one place to inspect container resources and real AI decisions:
request counts, questions/state, typed answers, confidence, actual fallbacks,
and sessions. The comparison of interest is **local Laya versus hosted Jev on
the same task**, including cases where both remain uncertain. The user stated
a preference for one interface rather than separate Grafana and Phoenix UIs;
whether that is a hard requirement is open (§12 Q2).

This research also preserves the other outstanding operational observations:

- [Container resources and a representative workload](2026-10-20-container-resource-sizing.md).
- [OmniRoute updater: current limitations and feasible isolation](2026-10-20-omniroute-updater-observations.md).
- [Benchmark-derived sizing](2026-10-24-benchmark-derived-sizing.md), source of the thresholds in §9.

Earlier background: [Laya local observability](2026-10-18-laya-local-observability.md),
[Phoenix trial results](2026-10-18-phoenix-trial-results.md),
[operations resource snapshot](2026-10-19-operations-resource-snapshot.md),
and the plans [Laya Phoenix live history](../plans/2026-10-19-laya-phoenix-live-history.md)
and [Phoenix start integration](../plans/2026-10-19-phoenix-start-integration.md).

These files were first written under `./.docs/superpowers/research/` and are
now committed under `docs/superpowers/research/` (commit `f7fadd0`). An
identical untracked copy still exists in `.docs/`; two copies will drift (§12
Q5). They are evidence and background, not approval to deploy the proposals
below.

## Conclusions

1. **Activity since October 14 is recoverable:** 574 native HTTP access events
   were recovered from journald. They do not contain historic confidence or
   prove that hosted Jev was called.
2. **Zero Phoenix sessions is an instrumentation gap:** the observer creates
   independent request traces but sets no `session.id`, and the Jev client
   sends no session or run header. Still true on 2026-10-24.
3. **The Jev client now escalates, but nothing remote is usable here.** Since
   2026-10-23 a low-confidence or failed Laya answer goes to the next consented
   rung (TypeSafe, then OpenCode Zen). On this host TypeSafe has consent but no
   key and Zen has neither, so all 785 logged items ended on Laya and 80.4 %
   ended with `needs_fallback: true`, which means the caller reasoned alone.
4. **The existing confidence fields are different statistics.** Laya's
   `x_jev_confidence` is a local transformation, not evidence of a Jev response.
5. **Footprint decides the stack on this host** (30.5 GiB RAM, 13.8 GiB in swap
   on 2026-10-24). Start with no new resident service; add VictoriaMetrics,
   then VictoriaLogs; add Grafana only if one UI is demanded. Tempo is not
   needed while Laya calls are single spans.
6. **Collect on the host, not in sidecars.** No packaged exporter reports the
   cgroup fields the resource-limit work gates on (`memory.peak`,
   `memory.swap.peak`, `memory.events`, PSI) for rootless Podman; the repo
   already reads those files. No backend supplies missing session/fallback
   semantics; those must come from the clients.

## 1. What can actually be recovered

### Historical access events

The completed recovery scanned 50,565 journal records for the current host user
and `CONTAINER_NAME=system-one`, across five container lifetimes. The query
started at October 14 local midnight and ended at
`2026-10-20T12:18:45.475Z`. It finished successfully without reaching its scan
limit or deadline. It excluded 49,864 health access records and three other
HTTP records (the 49,864 figure was not re-verified on 2026-10-24: the private
summary was not reopened).

| Chile calendar day | Native HTTP requests |
| --- | ---: |
| October 14 | 274 |
| October 15 | 45 |
| October 16 | 23 |
| October 17 | 19 |
| October 18 | 107 |
| October 19 | 96 |
| October 20, through the fixed cutoff | 10 |
| **Total** | **574** |

There are 573 HTTP 200 responses and one HTTP 400; 573 calls use
`/v1/systemone` and one uses `/v1/systemone/batch`. Of these access events,
525 precede the earliest stored Phoenix trace.

These are **logged HTTP calls**, not individual questions or proof of gap-free
coverage. Their timestamps describe access-log emission/receipt, not inference
start. The scanned messages contain no structured decision records or JSON
bodies from which to reconstruct questions, answers, durations, confidence,
usage, or batch cardinality. HTTP 200 does not establish answer correctness.

Authoritative private evidence, relative to the repository root:

- `tmp/research-2026-10-20/history/journal-access-events.recovered.ndjson`:
  574 records; SHA-256
  `aec62d42093ec35aa03205ccc90b287c8a28d914171177fbdf1fdb60d24711e8`
  (line count and hash re-verified 2026-10-24 with `wc -l` and `sha256sum`).
- `tmp/research-2026-10-20/history/journal-access-recovery-summary.json`.
- `tmp/research-2026-10-20/history/README.md`.

The earlier worker file `local-events.ndjson` is an invalid extraction and is
not a substitute for the explicitly named recovered dataset.

### Archived content

A separate, bounded search verified **82 unique saved native request/response
pairs**: 36 have original call timestamps and 46 do not. The dated calls occurred
on October 19, `13:32:12.016Z`–`13:32:13.996Z`. The receipts are primarily review
and verification traffic; they are not a representative user-traffic sample.
No inspected receipt establishes October 14–18 question/answer recovery.
**Unverified on 2026-10-24:** the review did not open the manifest because it
contains content.

The pairs overlap access events: **do not add 82 to 574**. Keep undated pairs in
an undated archive; folder names and modification times are not event times.
The manifest is
`tmp/research-2026-10-20/history/combined-receipt-manifest.json`.

### Recent Phoenix records, independently checked again

A bounded GET-only listing on October 20 returned all 49 then-current spans,
without a continuation cursor. No application inference was run. Node and an
independent jq extraction agree on these aggregates (a 2026-10-20 snapshot):

| Observation | Value |
| --- | ---: |
| HTTP request spans | 49 |
| HTTP 200 / HTTP 400 | 48 / 1 |
| Batch HTTP requests | 1 |
| Returned typed answers | 81 |
| Choice / Score / Noul answers | 37 / 20 / 24 |
| Spans carrying `session.id` | 0 |
| Numeric returned `confidence` values below 0.7 | 28 of 81 |
| Winning-side probabilities below 0.7 | 13 of 81 |
| HTTP spans containing any such below-0.7 winning probability | 13 |
| Choice/Score answers carrying `x_jev_confidence` | 57 |
| Actual hosted-Jev fallback count | **Unknown** |

The earliest trace starts at `2026-10-19T18:59:45.645562Z`; the latest starts at
`2026-10-20T04:45:34.840971Z`. No truncation markers occur in this readback.
This is recent instrumented traffic, including checks/reviews, not reconstructed
week-long content history.

**Update 2026-10-24** (paged GET on `/v1/projects/laya/spans`): Phoenix holds
5,542 spans in project `laya`, and **0** carry `session.id`. Per day: 10-19 21,
10-20 28, 10-23 678, 10-24 4,815; 5,476 OK and 66 ERROR. The 4,815 spans on
10-24 compare with 566 Jev items that day (§3): most Laya traffic that day was
`make system-one-benchmark` and checks, not the owner's tools, which is why
Laya traffic must be attributed by caller.

The 0.7 calculations are descriptive and reflect the inspected helper's default,
not proof of the threshold configured by every historical caller. A threshold
on returned `confidence` produces a different count from the helper's actual
winning-probability gate. Neither count establishes an executed fallback.

Private reproducible evidence:
`tmp/grafana-comparison-research/coordinator/phoenix-summary.final.json`,
`phoenix-summary-independent.json`, and `summarize-phoenix.mjs`. Earlier derived
summaries in that directory are preliminary; the final and independent summaries
are authoritative. Raw span content remains private and is not included here.

A future history view can display recovered access events and the dated content
without inventing missing fields. Importing old events must preserve timestamp
semantics, deduplicate sources, and respect the log store's age and
out-of-order limits. This research performed no import, trace fabrication, or
request replay.

## 2. Why requests exist but sessions do not

Phoenix groups traces into sessions using the OpenInference **`session.id`**
attribute. One session can contain many traces. A trace ID and a session ID have
different meanings.

`docker/system-one/phoenix_observer.py` currently generates a fresh random trace
ID per native HTTP request, emits a root span, and does not set `session.id` or
propagate an inbound parent trace (re-checked 2026-10-24, lines 80-118). Zero
sessions is therefore expected; it does not mean Phoenix lost the request
records.

The actual harness must supply a stable session identifier through a verified
runtime integration. Client instrumentation should associate it with all engine
attempts and carry it to server telemetry where supported. The server cannot
reliably infer a conversation from a shared IP, process name, project directory,
time window, or randomly generated trace ID. Missing session identity must stay
unknown.

### Where session identity can come from (qualified 2026-10-24 from vendor docs)

| Tool | Native mechanism | Session identity | Content default |
| --- | --- | --- | --- |
| Claude Code | `CLAUDE_CODE_ENABLE_TELEMETRY=1`, `OTEL_METRICS_EXPORTER=otlp\|prometheus`, `OTEL_LOGS_EXPORTER=otlp`, `OTEL_EXPORTER_OTLP_PROTOCOL`, `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_RESOURCE_ATTRIBUTES`. Traces are beta (`CLAUDE_CODE_ENHANCED_TELEMETRY_BETA=1` + `OTEL_TRACES_EXPORTER=otlp`). Metrics include `claude_code.token.usage`, `claude_code.cost.usage`, `claude_code.session.count`; events include `claude_code.api_request`, `claude_code.api_error`, `claude_code.tool_result`. Metric temporality defaults to `delta`. | `session.id` attribute (`OTEL_METRICS_INCLUDE_SESSION_ID`, default true); hooks also receive `session_id` | Prompts redacted unless `OTEL_LOG_USER_PROMPTS=1`; tool details off unless `OTEL_LOG_TOOL_DETAILS=1` |
| OpenCode | No built-in OTel key in the config docs. Community plugins `@devtheops/opencode-plugin-otel` (`OPENCODE_ENABLE_TELEMETRY`, `OPENCODE_OTLP_ENDPOINT`) and `@gcornut/opencode-otel`. The local `opencode.db` already stores per-session tokens and cost (`session_v2.tokens_*`, `cost`, `model`). | `session_v2.id`; OmniRoute recognizes `x-opencode-session` for its OpenCode providers (OmniRoute doc, v3.8.51) | The devtheops plugin puts the prompt in span `input.value` regardless of its prompt flag (per its README): do not adopt without review |
| Pi | Extension events including `before_provider_headers` (can modify outgoing headers), `after_provider_response`, `turn_end`, `session_start`, `session_shutdown`. The local `session_before_compact` event used by `ai-kit-precompact.ts` is not listed in that doc. | `sessionManager.getSessionId()` | Under the extension's control |
| Cursor | Hooks `sessionStart`, `sessionEnd`, `beforeSubmitPrompt`, `afterAgentResponse`, `stop`, `preCompact` and others; every payload carries `conversation_id`, `generation_id`, `model`. No OTel. Only a `sessionStart` hook is configured locally. | `conversation_id` | Hook-defined |

OpenTelemetry GenAI conventions now live in their own repository at status
**Development**. Use `gen_ai.operation.name`, `gen_ai.provider.name`,
`gen_ai.request.model`, `gen_ai.response.model`, `gen_ai.usage.input_tokens`,
`gen_ai.usage.output_tokens`, `error.type` and **`gen_ai.conversation.id`** for
the conversation. `gen_ai.input.messages` and `gen_ai.output.messages` are
Opt-In and flagged sensitive. Phoenix sessions keep using OpenInference
`session.id`, so emit both.

## 3. What “fallback” and “confidence” mean here

### Current client behavior (since 2026-10-23)

The client is `~/.claude/skills/jev-fast-decision/scripts/jev_review.py`
(1,987 lines, dated 2026-10-23, `CLIENT_PROTOCOL = 2`; line numbers below are
as of 2026-10-24). It replaced the single-call helper inspected on 2026-10-20.

- **Order of attempts in auto mode:** `laya` (local, one HTTP call per item, no
  consent needed), then `typesafe` (one batch, needs consent and a key), then
  `opencode` Zen (one call per item, consent and key, `jev-1.13-free` falling
  back to `jev-1.13` on 402/429) (`_ladder`/`_fold_rung`, lines 1775-1828;
  `backends/laya.md`).
- **When the next rung is tried:** a low local answer (`needs_fallback`,
  `top` < 0.7) stays tentative and moves on; remote answers are final. Laya 503
  is retried at most twice, then escalates; 401/403, other 5xx and network
  errors escalate; 400/413/422 are final caller errors.
- **Switches:** `JEV_BACKEND=<name>` pins one rung and never escalates.
  `GSD_PATH_JEV=1` is the kill switch: without it nothing is sent.
  `JEV_API_URL` is honoured only when it is loopback (`_laya_url`, line 562).
- **On the wire:** `User-Agent: jev-fast-decision/2.0` and the bearer key.
  **There is no session, run or trace header.**
- **Recorded:** one JSON line per item in `~/.local/state/ai-kit/jev-answers.jsonl`
  (mode 0600, `_log_line`, lines 1645-1669) with `ts`, `caller` (from
  `JEV_CALLER`), `id`, `type`, `instructions` (**the question text**),
  `backend`, `model`, the answer, `top`, `needs_fallback` and `request_sha256`.
  Only the **final** rung is logged; the per-item `trail` and `attempts` are
  returned to the caller and discarded. The file is never rotated.

What happened on this host (aggregate parse of `jev-answers.jsonl` plus
`jev_review.py --probe`, which only issued a GET on Laya `/health`):

| Day | Items | Backend / model | Caller |
| --- | ---: | --- | --- |
| 2026-10-23 | 219 | laya / `laya-rl-agent` | `ai-kit-precompact` 197, `ai-kit-gsd-spec-discuss` 22 |
| 2026-10-24 (to 16:xx UTC) | 566 | laya / `laya-rl-agent` | `ai-kit-precompact` 559, `ai-kit-gsd-spec-discuss` 7 |

- **631 of 785 items (80.4 %) ended with `needs_fallback: true`**, and every
  item ended on Laya.
- The probe explains why: `typesafe` is consented (since 2026-10-14) but its
  key is missing, and `opencode` is unconsented with no key. **There is no Zen
  or TypeSafe fallback on this host today**; every item that needed one fell
  back to the caller's own reasoning (§12 Q3).
- The dominant caller, `ai-kit-precompact`, is a Pi extension
  (`~/.pi/agent/extensions/ai-kit-precompact.ts`). It already has the Pi
  session id (`manager.getSessionId()`, line 151) but does not pass it on.

Different events must still remain separate:

- Laya returns an uncertain answer or an abstention flag.
- The client escalates the item to a remote rung, or would have but no rung
  was usable.
- A remote rung starts, completes, fails or times out.
- The caller performs a full reasoning-LLM review instead.
- OmniRoute switches providers because of quota, errors, or availability.

The last two are not Laya-to-Jev fallbacks. A server observer covering only
local Laya cannot see later client actions or off-host calls, and the client
currently discards the ladder trail, so executed-fallback counts before that
is fixed (§11) remain unknown, never zero.

### Preserve the statistic, engine and policy

| Field/statistic | Meaning |
| --- | --- |
| Laya Choice/Score `confidence` | Normalized distribution concentration, `1 - H(p)/log(n)`; not a correctness probability |
| Laya Noul `confidence` in the current non-strict responses | Winning-side probability, `max(p_yes, 1-p_yes)` |
| Laya `answer_confidence` | Probability mass on the winning answer, when reported; no automatic calibration guarantee |
| Laya `x_jev_confidence` | Jev's formula applied locally to **Laya's** distribution; no Jev API call |
| Hosted TypeSafe Choice `confidence` | `(p_max - 1/n)/(1 - 1/n)`, according to public docs read 2026-10-20 (not re-fetched 2026-10-24) |
| Hosted TypeSafe Score `confidence` | Distribution spread around the modal ordered level, normalized against uniform spread (same caveat) |
| Hosted TypeSafe Noul | Probability of yes; public docs specify no separate returned confidence (same caveat) |
| Client gate | Choice/Score `p_max < 0.7`, or Noul `max(p_yes,1-p_yes) < 0.7` (`top` < 0.7), unless overridden |

The running Laya `common.py` and `serve.py` were copied read-only and matched
byte-for-byte against the public 0.4.2 wheel. That wheel's SHA-256 is
`126284550840e8fc2445f9cd4c808e505d25d457242e7de5658b2cb9f9155b26`
(re-verified 2026-10-24). Its source explicitly distinguishes these statistics
and computes `x_jev_confidence` locally. The 2026-10-20 readback contains
`answer_confidence` on all 81 answers and `x_jev_confidence` on all 57
Choice/Score answers.

Consequently, comparing two raw fields both named `confidence` is insufficient.
Show the original values, the selected gate statistic, threshold, option-count
bucket, model/checkpoint and policy version. A common recomputed statistic can
make distributions easier to compare; it does not turn Laya into Jev or make
confidence equal accuracy. Fifty representative requests can inform operations,
but cannot establish reliable calibration across all question types.

### Correlation required for a useful comparison

The following is a research-level data contract, not an implemented schema:

| Information | Purpose |
| --- | --- |
| Opaque harness/session ID | Groups a real conversation without identifying it by timing |
| Logical decision ID, batch-item ID, question ID | Separates one intended judgment from HTTP batching and multiple questions |
| Attempt ID/number/kind and causal parent | Separates retries, hosted-Jev calls, and full-LLM reviews |
| Trace/span IDs and propagated context | Technical cross-service linkage; distinct from the session ID |
| Versioned submitted-task fingerprint | Binds the state, instructions, criteria/levels, type and ordering used for both attempts (`request_sha256` already exists) |
| Per-engine effective-input information, where exposed | Records truncation, dropped tokens, routing and transformations; unknown remains unknown |
| Engine/provider, requested/returned model, typed answer, distribution, statistic | Supports honest side-by-side inspection |
| Gate statistic/threshold/policy and reason | Explains why an escalation was requested |
| Suggested/started/completed/failed/timed-out events | Establishes actual execution rather than inferring it from a flag |
| Timestamp semantics, latency, available usage, truncation/export loss | Makes missing, late and incomplete records visible |

Question/state and answer **content** is governed by the privacy default in §7:
hash and shape by default, bounded and redacted content only where the owner
opts in. Keep small query fields separate from any larger JSON content.

For pairing, require the same submitted task and an explicit causal relation.
Do not pair only by question label or nearest timestamp. Internal token streams
need not be byte-identical across engines with different tokenizers, and hosted
Jev's internal input is not exposed by the documented API. Record that limit:
“same submitted task” is verifiable; “identical effective model input” may not
be. Compare known truncation/routing differences separately instead of claiming
controlled input equivalence that cannot be established.

### Dashboard counts and denominators

Count HTTP calls, batch items, question answers, logical decisions and attempts
separately. Useful views include:

- Low-statistic answers / answers where that statistic is known, with missing
  values shown separately and the threshold/policy visible.
- Actual hosted-Jev starts / eligible uncertain Laya decisions, and separately
  / all decisions; escalations with no usable rung remain distinct.
- Hosted-Jev completions, failures/timeouts, and still-unobserved outcomes.
- Paired outcomes: Laya-low/Jev-high, both-low, both-high, disagreement,
  incomplete pair, error and timeout. “High” means a named policy threshold,
  not proven correctness.
- Session timelines with the actual engine selected and final decision outcome
  when the caller records it.

Emit a small terminal comparison record containing both sides when possible;
this makes a comparison table practical without assuming the log query language
performs arbitrary joins between two independent attempt records. Preserve
pending or orphan attempts when a caller crashes before that record exists.

Fallback-only comparisons are selected for difficult Laya cases. They describe
the escalation policy, not overall comparative quality. Unbiased shadow sampling
would be a separately enabled, costed workflow; do not silently duplicate all
requests to hosted Jev. Agreement, confidence uplift and correctness are distinct.
Accuracy/calibration require labeled outcomes and a stated evaluation method.

## 4. Can a Jev API key recover past data?

Public TypeSafe documentation (read 2026-10-20) describes bearer-authenticated
inference, `POST /v1/systemone`, per-response token usage, and `GET /v1/models`.
The inspected public API/OpenAPI and documentation index expose no documented
historical request/response or account-usage export endpoint.

That finding does not prove that no console export or support-assisted export
exists. It means **an inference key is not verified history-export access**.
Provider retention, export fields/windows and account permissions remain unknown.
No key, account console or authenticated API was accessed for this research.

Keep the client's backend names as provenance: `laya`, `typesafe` (direct) and
`opencode` (Zen). An OpenCode Zen key is not evidence of TypeSafe account-history
access. On this host neither remote rung has a key and the local log since
2026-10-23 records only Laya answers (§3), so there is no hosted history of our
own to recover yet. A future client can record its hosted-Jev responses/usage
going forward (§11); it cannot recover unrecorded history merely by obtaining an
inference key.

## 5. One interface: options with footprints and a phased recommendation

Grafana is an interface, not storage and not an automatic AI semantic collector.
The 2026-10-20 proposal's "collectors" wording was clarified: it meant a shared Grafana Alloy or OpenTelemetry Collector, never Neptune; Neptune.ai and AWS Neptune/Gremlin/JanusGraph are not needed or recommended.

### Two facts that shape the choice

**No packaged exporter covers the limit metrics on rootless Podman.**

- prometheus-podman-exporter v2.0.0 exports `podman_container_mem_usage_bytes`,
  `…mem_limit_bytes`, CPU, state, health, block I/O and network, but **no**
  peak, swap, `memory.events`, PSI or restart counter.
- cAdvisor v0.60.6: the rootless Podman issue #3628 was closed "not planned",
  with container names missing. A rootless example exists (PR #3635); this
  host's setup is unverified.
- node_exporter v1.12.1 has `pressure` (host PSI), `textfile` and a
  disabled-by-default amdgpu-only `drm` collector (`memory_vram_used_bytes`,
  `gpu_busy_percent`). It is host-level only.
- The repo already reads the exact files: `pylib/resource_benchmark.py:read_point`
  reads `memory.current`, `memory.swap.current`, `memory.peak`, `memory.stat`,
  `cpu.stat`, and VRAM through `pylib/detect.py:process_vram_mib` (DRM fdinfo
  `drm-memory-vram`) and sysfs `mem_info_vram_used`. Adding `memory.max`,
  `memory.swap.peak`, `memory.events` and `memory.pressure` is a small change.

**OmniRoute can emit OTLP GenAI spans (source-verified on `main` only).**
`OtlpHttpsEventSink` (since commit `d87b97a786`, 2026-08-20) is enabled by
`OMNIROUTE_OTEL_ENDPOINT` or `OTEL_EXPORTER_OTLP_ENDPOINT`. It sends OTLP/HTTP
**JSON** to `<endpoint>/v1/traces` in batches of 64 every 10 s from a bounded
buffer that drops the oldest events. Spans carry `gen_ai.provider.name`,
`gen_ai.request.model`, `gen_ai.operation.name="chat"`, input/output token
counts, `gen_ai.completion.finish_reason` and `omniroute.connection_id`; its
header says no secrets or prompts are serialized. Caveats:

- Missing tokens are written as `0`, which breaks unknown ≠ zero; treat 0 as
  unknown.
- `gen_ai.system` carries the routing strategy; `gen_ai.request.temperature` is
  hardcoded to 0.
- Presence and behavior in the deployed `omniroute 3.8.53` (read from
  `/app/package.json` in the container) is **unverified** and needs a throwaway
  container first (§12 Q7).
- Phoenix ingests OTLP **protobuf** on `/v1/traces`; whether it accepts the JSON
  variant is unverified. An OTel Collector accepts both.

On the host, OmniRoute `/metrics` and `/api/metrics` return 404 even with a
dashboard cookie. The guard and traffic counters the limit work needs are in
`GET /api/monitoring/health` (behind the dashboard login):
`adaptiveAdmission.{pressureGuardRejectCount,rejectedCount,admittedCount,utilization}`,
`chatAdmission.{shedTotal,shedsByReason}`,
`circuitBreakers.{open,halfOpen,degraded}`,
`quotaMonitor.{alerting,exhausted,errors}`,
`codexAccountPools.{available,fullyLimited}`, `eventLoopStall.{count,maxMs}`,
`memoryUsage.{rss,heapUsed}`, `sessions.activeCount` and
`credentialHealth.{failed,stale}`. `pylib/omniroute_activity.py` already logs in
and reads `/api/monitoring/health`, `/api/usage/history` and
`/api/usage/call-logs`, priming the stale-while-revalidate health first. Only the
`path`, `timestamp` and `active` call-log fields are characterized
(`omniroute_activity_profiles.json`); status, token and latency fields are
unverified.

### Options

- **Image** is the compressed amd64 size from the Docker Hub API (measured
  2026-10-24). **Version** is the latest GitHub release on 2026-10-24.
- **RAM** is labelled *measured* or *estimated*. Measure an estimate with
  `make resources-benchmark SERVICE=attach CONTAINERS=<name>` before relying
  on it.
- Budget context (measured 2026-10-24): 13.6 GiB available and 13.8 GiB already
  in swap.

| Option | Version / license | Image | RAM steady | Covers | Verdict |
| --- | --- | ---: | --- | --- | --- |
| **A. User timer runs `uv run llmenv.py observe` every 60 s → size-capped JSONL (+ `make observe` report)** | repo code, no new dependency | 0 | 0 resident; about 21 MB peak per run (measured on the analogous healer oneshot) | cgroup (incl. peak, swap, events, PSI), VRAM, host PSI/swap, OmniRoute health counters, journald-derived healer/restarts, Jev log aggregates | **MVP, do first** |
| B. VictoriaMetrics single node (+ vmui, optional vmalert) | v1.154.1, Apache-2.0 | 18 MiB | estimated 50-150 MiB | Metrics; Prometheus text push to `/api/v1/import/prometheus`, OTLP to `/opentelemetry/v1/metrics`; `-retentionPeriod` | **Phase 1 metric store** |
| B'. Prometheus | v3.15.1, Apache-2.0 | 109 MiB | estimated 150-400 MiB | Same, plus native alerting rules | Acceptable, heavier than B |
| B''. Mimir (monolithic) | 3.2.3, AGPL-3.0 | n/a | estimated ≥ 1 GiB | Multi-tenant scale-out | **Reject**: built for clusters |
| C. VictoriaLogs | v1.53.0, Apache-2.0 | 13 MiB | estimated 50-150 MiB | Logs; journald, OTLP (`/insert/opentelemetry/v1/logs`), Loki-push and JSON-lines ingestion; disk cap `-retention.maxDiskSpaceUsageBytes`; default retention 7 d. Vendor "up to 30x less RAM than Loki" is self-reported | **Phase 2 log/decision store** |
| C'. Loki (monolithic) | v3.7.9, AGPL-3.0 | 46 MiB | estimated 150-500 MiB | Logs; retention is time-based, not a disk quota | Acceptable; prefer C here |
| D. Grafana | v13.2.4, AGPL-3.0 | 460 MiB | estimated 150-300 MiB | One UI over B/C/Phoenix links | **Phase 3, only if one UI is demanded** |
| E. Grafana Alloy | v1.21.2, Apache-2.0 | 213 MiB | estimated 100-250 MiB | Collector; journal source documented to need `adm` + `systemd-journal` | Not needed if A pushes directly |
| E'. OpenTelemetry Collector (contrib) | v0.163.2, Apache-2.0 | n/a | estimated 60-200 MiB | OTLP fan-in, `deltatocumulative`, redaction | Phase 2 only if OTLP needs routing/redaction |
| E''. Vector | v0.60.0, MPL-2.0 | 60 MiB | estimated 30-100 MiB | Collector; journald source shells out to `journalctl` | Viable, but A covers its job |
| F. Tempo (monolithic) | v3.1.1, AGPL-3.0 | 39 MiB | estimated 200-500 MiB | Trace trees + TraceQL; monolithic runs without Kafka | **Reject for now**: Laya calls are single spans |
| F'. VictoriaTraces | v0.12.1, Apache-2.0 | n/a | not estimated | OTLP traces | Pre-1.0; watch only |
| G. Keep Phoenix | 20.23.0 pinned (latest 20.26.0); Elastic License 2.0 | 1.04 GB local | measured: 343 MiB current, peak 784 MiB, volume 30 MiB for 5,542 spans (sizing doc: 73 MiB idle, 483 MiB with traffic, peak 804 MiB) | LLM span UI, sessions once `session.id` is set, OTLP ingest | **Keep through phase 2** |
| H. Langfuse v4 | v4.70.0; core MIT-style with EE dirs | n/a | vendor: at least 4 cores and 16 GiB (VM path) | web+worker+Postgres+Redis+ClickHouse+S3 | **Reject on this host** |
| I. OpenLIT | Apache-2.0 | n/a | not estimated (OpenLIT + ClickHouse + NATS) | LLM + GPU dashboards | **Reject**: ClickHouse on a swapped host |
| J. OpenLLMetry | 0.62.4, Apache-2.0 | SDK only | in-process | Auto-instruments LLM SDKs | Not applicable to Laya |
| K. prometheus-podman-exporter | v2.0.0, Apache-2.0 | n/a | estimated 20-50 MiB | No peak/swap/events/PSI | Reject as primary |
| L. cAdvisor | v0.60.6 | n/a | estimated 100-300 MiB | Rootless poorly supported | Reject |
| M. node_exporter | v1.12.1, Apache-2.0 | 13 MiB | estimated 15-40 MiB | Host PSI, meminfo, zram/swap, amdgpu `drm`, textfile | Optional; A reads the same files |

Expected data volume: about 150 metric series from A (4 services × about 25,
plus host and GPU), a few MB per month at 60 s resolution (estimated); journald
text about 48 MB/week (measured, §6); Laya spans about 5.5 KB each in Phoenix
(measured, 30 MiB / 5,542). Do not install Promtail (end of life 2026-03-02) or
Grafana Agent (end of life 2025-11-01).

Points from the 2026-10-20 analysis that still hold for any store:

- Large-JSON parsing in a log query engine can be expensive, but no measurement
  shows this small workload cannot use structured decision logs. Extract
  frequently queried fields; keep any bounded JSON for details.
- A polished chat viewer, annotation/evaluation workflow or Phoenix-equivalent
  AI dashboard remains work; no backend combination supplies it.
- Generic span metrics derive calls/errors/durations; they do not turn a
  `confidence` attribute into a histogram. A floating confidence value must
  never be a metric label.
- Migrating the existing observer is **not an endpoint-only change**: it uses
  Phoenix's REST payload and an explicit local endpoint allowlist, not OTLP.
  Its bounded parser/redaction is reusable; transport, context propagation,
  lifecycle, freshness checks and retention need qualification. Preserve the
  Phoenix volume until any migration and historical readback are verified.

### Recommendation in phases

**Phase 0 (MVP): no new services.**

1. A new `uv run llmenv.py observe` subcommand, extending
   `pylib/resource_benchmark.read_point`, run by one systemd **user** oneshot
   timer every 60 s (the OmniRoute healer timer sets the pattern). It writes one
   JSON line per tick to `~/.local/state/llm-env/observe/YYYY-MM-DD.jsonl`
   (mode 0600; files older than 14 days deleted). Each line holds:
   - per service: `memory.{current,peak,max,low,swap.current,swap.peak}`,
     `memory.events` counters, `memory.pressure` (some/full `avg10` and
     `total`), `cpu.stat` (`usage_usec`, `nr_throttled`, `throttled_usec`), and
     the container `Id` and `StartedAt`, so a restart is visible and
     `memory.peak` resets are explained;
   - host: `/proc/pressure/{memory,cpu,io}`, MemAvailable, swap and zram
     (`/sys/block/zram0/mm_stat`);
   - GPU: VRAM per discovered device from sysfs, and per Laya process via
     `process_vram_mib`;
   - OmniRoute: the health counters above, through the existing
     `omniroute_activity` login and prime helpers;
   - optionally the same values as Prometheus text, so phase 1 is a push, not a
     rewrite.
2. `make observe` prints the last 24 h / 7 d instead of a dashboard: max
   peak/cap per service and `memory.events` max/oom_kill deltas; PSI p95, guard
   rejects and sheds; healer actions and container restarts; Jev items per day,
   backend, caller and `needs_fallback` rate from `jev-answers.jsonl` (never the
   `instructions` field).
3. Fix semantics at the source: the Laya observer reads inbound session/run
   headers (§6) so Phoenix sessions stop being zero with no new backend; ai-kit
   sends those headers and logs the full ladder (§11).

Cost: no resident RAM, about 21 MB / 70 ms per minute (healer analog), a few MB
of disk per week (estimated).

**Phase 1: VictoriaMetrics single node.** Rootless container, loopback-only
port, `-retentionPeriod=90d` and a 512 MiB cap to start (both proposals, to be
measured and set by the owner, §12 Q6). The sampler pushes to
`/api/v1/import/prometheus`. Use vmui for ad-hoc charts and vmalert, or the
sampler's own threshold check, for the rules in §9. Optionally point Claude
Code metrics at it: `CLAUDE_CODE_ENABLE_TELEMETRY=1`,
`OTEL_METRICS_EXPORTER=otlp`, `OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf`,
`OTEL_EXPORTER_OTLP_METRICS_ENDPOINT=http://127.0.0.1:8428/opentelemetry/v1/metrics`,
`OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE=cumulative` (VictoriaMetrics
works best with cumulative temporality). Entry gate: phase 0 has run 7 days and
the owner wants charts over time.

**Phase 2: VictoriaLogs.** With a disk cap (for example
`-retention.maxDiskSpaceUsageBytes=2GiB`; the owner chooses, §12 Q6). It
receives the journal lines of the four containers and the healer unit, pushed
as JSON lines by the sampler from
`journalctl --user -o json --after-cursor=<saved cursor>` (no Alloy, no extra
group); one **decision record** per Jev item from ai-kit; optionally Claude
Code OTLP logs with prompts redacted. Turn on `OMNIROUTE_OTEL_ENDPOINT` only
after a throwaway-container check (§12 Q7); add an OTel Collector only if
Phoenix or another store cannot take OTLP/JSON directly.

**Phase 3: Grafana, only if one UI is demanded** (§12 Q2), over the
VictoriaMetrics (Prometheus-type), VictoriaLogs and Phoenix (link-out) sources.
Do not add Tempo unless multi-hop traces appear (client, OmniRoute, provider,
with propagated `traceparent`).

**Phoenix retirement criteria** (all must hold):

1. Laya decisions are stored as structured log records with `session.id`,
   `run.id`, caller, type, `top`, `needs_fallback`, latency, tokens and
   truncation flags. A Laya call is a single span, so such a record is a
   complete representation.
2. A table and a session-timeline panel reproduce what the owner actually uses
   in Phoenix (confirm with the owner).
3. 30 days of parallel running show equal counts.

Until then Phoenix stays, at about 0.35-0.5 GiB resident with peaks near
0.8 GiB (measured, above). A replacement is cheaper only if VictoriaLogs plus
Grafana together stay under that, which is unmeasured.

## 6. Collection coverage for every owned container

An October 20 selected-field inspection confirmed all four services healthy and
using **journald**: OmniRoute, System One/Laya, remote-setup and Phoenix
(re-verified 2026-10-24 with `podman inspect … LogConfig.Type`). The 2026-10-20
engine IDs were `44b8702af6cd`, `8b0ef2bb3920`, `fce05b8857d5` and
`19e65850edfb`; containers have been restarted since (start times below), so
identify them by compose name and labels, never by a stored ID. The historical
journal recovery already proves user-scoped access to Laya logs.

| Scope | Resource/log signals | Application-level signals or gap |
| --- | --- | --- |
| Every current owned container | CPU time/quota/throttling; charged RAM, anon/cache, peak, swap, limit events, PSI; health; stdout/stderr journal | Identify by owned service and verified current cgroup, not a stale ID or delegation root |
| Laya | The above plus device-level GPU context | Request/answer/usage collection exists; caller/session/fallback linkage does not |
| OmniRoute | The above plus request/provider logs and `/api/monitoring/health` counters | `/metrics` is 404; an OTLP GenAI exporter exists on `main`, unverified on 3.8.53 (§5) |
| remote-setup | The above plus endpoint availability/errors | `/setup.sh` readiness and `/config` backend failures have different meanings; redact credentials and never collect config response bodies |
| Phoenix while retained | The above and its readiness | Optional own metrics (`PHOENIX_ENABLE_PROMETHEUS`, currently false) do not monitor other containers or AMD hardware |
| llama.cpp if enabled later | The same container coverage | Qualify that exact server's metrics flags/endpoint; currently disabled, not “zero traffic” from a missing scrape |
| New monitoring services/collectors | Their own CPU/RAM/swap, health, logs, queue/loss and disk growth | Include self-monitoring so collection cost is visible |
| Host and AMD devices | MemAvailable, CPU/IO/memory PSI, swap/zram activity; discovered GPU busy/VRAM | Whole-device GPU measurements include other clients; not automatic per-container attribution |

The intentionally retained synthetic fixture on port 42877 uses a separate
private store. It is not normal production traffic or a disposable orphan.
Keep it outside normal collection/history counts. Do not enumerate unrelated
containers or collect other user applications' payloads.

### Live resource state (measured 2026-10-24, cgroup v2 files, about 13:1x local)

Each container's files live in `…/libpod-<id>.scope/container/`. Some exporters
read the `.scope` level instead, so the path must be verified per tool.

| Service | started | memory.current | memory.peak | memory.max | peak/max | swap.current | swap.peak | events max/oom/oom_kill | PSI some avg10 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: |
| omniroute | 11:18 | 595 MiB | 1,005 MiB | 2,048 MiB | 0.49 | 205 MiB | 268 MiB | 0/0/0 | 0.00 |
| system-one | 08:00 | 3,356 MiB | 4,587 MiB | 4,608 MiB | **0.995** | 993 MiB | 1,196 MiB | 0/0/0 | 0.00 |
| remote-setup | 07:50 | 8.7 MiB | 38 MiB | 64 MiB | 0.60 | 10.6 MiB | 10.6 MiB | 0/0/0 | 0.00 |
| phoenix | 10:41 | 343 MiB | 784 MiB | 1,024 MiB | 0.77 | 115 MiB | 399 MiB | 0/0/0 | 0.00 |

- Host memory: MemTotal 30.5 GiB, MemAvailable 13.6 GiB at 13:17. Swap use
  13.8 GiB: zram0 holds 11.8 of 15.2 GiB (zstd, about 3.3 GiB physical) plus
  2 GiB on nvme swap. The four caps add up to 7,744 MiB.
- GPU (sysfs `mem_info_vram_*`): iGPU (card0) 512 MiB VRAM, dGPU (card1)
  15.9 GiB with 710 MiB used while Laya was unloaded.
- Healer (`llm-env-omniroute-heal.timer`, every 5 min): 2,005 runs in 7 days,
  no restarts; every outcome was "not restarting", including 67 memory-recycle
  evaluations. Each run costs about 21 MB memory peak and 67 ms CPU
  (`systemctl --user show … MemoryPeak CPUUsageNSec`).
- Updater: `make update-omniroute` writes no structured run record, only the
  profile and evidence JSON under `~/.local/state/llm-env/omniroute-profiles/`.
- Journal volume over 7 days (`journalctl --user CONTAINER_NAME=<name> -o cat | wc`):
  OmniRoute 543,802 lines and 39.9 MB (19.9 MB in the last 24 h, which included
  benchmark and characterization runs), about 186k JSON lines with
  `level/module/tag/msg`; text tags `[ProxyEgress]` 19,693, `[RATE-LIMIT]`
  7,063, `[STARTUP]` 647; `lastStatus` failover lines 500 ×20 and 403 ×7, of
  which 19 came from the benchmark's fake `llama-cpp` upstream. System One
  3.4 MB, Phoenix 4.3 MB; about 48 MB/week in total. The journal already
  retains it (3.8 GiB of user and system journals on this host).

### Least-privilege direction

- Use one user-owned host collection layer for the owned stack: the phase 0
  sampler reads the owned cgroup files, `/proc`, AMD sysfs and
  `journalctl --user` directly. No sidecar per container.
- Alloy's `loki.source.journal` is **not** privilege-free: Alloy documents that
  its user must be in the `adm` and `systemd-journal` groups, otherwise it
  "starts without error but collects no journal entries". This host's user
  reads `user-1000.journal` through an ACL (`user:bazzite:r--`) and the system
  journal through `group:wheel` (`getfacl`).
- If a containerized journal reader is ever needed, experiment first with
  `--userns=keep-id` (so the host ACL applies), `--security-opt label=disable`
  rather than `:Z` on `/var/log/journal` (`:Z` would relabel a system
  directory), a read-only `/etc/machine-id` mount, and verified cursor
  persistence across restarts.
- Do not grant system-journal group membership or host privileges by default.
  Do not mount a Podman/Docker socket and call it read-only security because
  the mount has `:ro`: that does not make the API read-only.
- The reviewed cAdvisor and AMD exporter privileged recipes are unsuitable here.
  A node exporter is host-level, not per-container coverage.
- A sidecar or passive network observer cannot infer a caller's escalation
  decision, session identity or encrypted hosted response content. Client-side
  semantic instrumentation is still required; no TLS interception is proposed.

### Instrumentation per container

| Target | What to set or emit | Notes |
| --- | --- | --- |
| **system-one** (our own Laya observer, `docker/system-one/phoenix_observer.py`) | In `RequestObserver.__call__`, read `scope["headers"]` for `x-session-id`, `x-run-id`, `x-jev-caller`, `traceparent`. Set `session.id`, `gen_ai.conversation.id`, `llmenv.run_id`, `llmenv.caller`; use the `traceparent` trace id as trace id/parent. Add `gen_ai.operation.name`, `gen_ai.provider.name="laya"`, `gen_ai.response.model`, `gen_ai.usage.input_tokens`/`output_tokens` next to `llm.token_count.*`. A proposed `LAYA_CAPTURE_CONTENT=0\|1` switches `input.value`/`output.value` between redacted content and hash plus shape (§7). Expose `BoundedPhoenixExporter.dropped` (today only a log warning) as a periodic log line or `/health` field. | Bound header values (≤ 128 chars, `[A-Za-z0-9._:-]`), reject others. A header the caller did not send stays **unknown**; never invent sessions from timing. `/health` already gives `loaded`, `idle_seconds`, `cpu_fallbacks`, `device`. |
| **omniroute** | `OMNIROUTE_OTEL_ENDPOINT=http://<collector>:4318` + `OTEL_SERVICE_NAME=omniroute`, **pending verification on the deployed 3.8.53** (§12 Q7). The sampler reads `/api/monitoring/health`. | Treat token 0 as unknown. Whether OmniRoute uses a generic inbound `x-session-id` for non-OpenCode providers is unverified; its doc names only `x-opencode-session` and "existing affinity/session headers". |
| **phoenix** (while kept) | `PHOENIX_ENABLE_PROMETHEUS=true` would expose metrics on container port 9090 (currently false, verified by inspect). Keep `PHOENIX_DEFAULT_RETENTION_POLICY_DAYS=7`. | `PHOENIX_ENABLE_AUTH=false`, so it stays loopback-only; stored content is unauthenticated. |
| **remote-setup** | cgroup + health only. | Never collect `/config` bodies. |
| **host** | `/proc/pressure/*`, `/proc/meminfo`, `/sys/block/zram0/mm_stat`, `/sys/class/drm/card*/device/{mem_info_vram_used,mem_info_vram_total,gpu_busy_percent}`; per-process VRAM from `/proc/<laya pid>/fdinfo` (`drm-memory-vram`). | Discover cards at runtime (two AMD cards here); never hardcode. |
| **healer** (`scripts/heal-omniroute-admission.sh`) | One JSON line per run: `{"action":"none\|recycle\|restart","reason":…,"memory_mib":…,"threshold_mib":…,"activity":…}`. | Current lines carry ANSI colour codes; strip them before parsing. |
| **updater** (`make update-omniroute`) | Start/decision/defer-reason/end as JSON to the journal (`systemd-cat -t llm-env-updater`) or to the observe JSONL. | No structured record exists today. |
| **Jev client** (ai-kit, §11) | Session id and run id per item, as `x-session-id`/`x-run-id`/`x-jev-caller`/`traceparent` headers and in `jev-answers.jsonl`. | Only the client knows the caller's session; the server must not guess it. |
| **Claude Code** | §2 env vars; `OTEL_RESOURCE_ATTRIBUTES=host.name=bazzite,llmenv.tool=claude-code`. | Keep `OTEL_LOG_USER_PROMPTS` and `OTEL_LOG_TOOL_DETAILS` unset. |

## 7. Retention, cardinality, privacy and operational safety

### Privacy and retention decision

There is a conflict to resolve. The 2026-10-19 plan stores bounded **full
content** (up to 24,000 B of request and response per span, `MAX_CAPTURE_BYTES`)
in a Phoenix with `PHOENIX_ENABLE_AUTH=false`, at the owner's explicit request
then. The owner's current rule is never to store prompt contents by default.
This document's default, pending the owner's confirmation (§12 Q1):

- **Hash by default.** No prompt, state, question or answer text in metrics,
  labels or default logs; use `request_sha256` (already present) and shapes
  (counts, type, `top`).
- **Content opt-in per source**, with ≤ 7 d retention and redaction of key-like
  fields (the existing `SECRET_KEYS` set plus `x-api-key`, `cookie`,
  `set-cookie`).
- `jev-answers.jsonl` stores `instructions` (question text) forever today:
  rotate it (for example 30 days) and make the text field opt-in (§11).
- Retention targets in days/GiB for metrics, logs and decision records are the
  owner's choice (§12 Q6); the phase 1/2 values in §5 are placeholders.

### Cardinality

Keep content, request/session/decision/trace IDs, combo free text, paths with
IDs and error messages out of labels; they belong in log or trace fields.
Allowed labels: `service` (4), `device` (≤ 2), `event` (5), `pressure`
(some/full), `backend` (3), `type` (3), `outcome` (bounded enum), `caller`
(allowlist), and `provider`/`model` only from the configured OmniRoute catalog
(8 configured providers per health). Never use a floating-point confidence as a
label; record it as a histogram or log field, with named statistics and
option-count grouping. Record unknown usage separately rather than writing zero.

### Storage limits

Loki can keep request IDs as structured metadata; that it requires the TSDB
schema format, and Loki's out-of-order and age limits, match current
understanding of Loki 3.x but were not re-fetched on 2026-10-24. Any trace store
must be checked against the existing 24,000-byte capture and 65,536-byte record
bounds. Preserve truncation and exporter-loss indicators.

Configure and verify retention explicitly: Loki does not delete history merely
because it is installed, and time retention is not a disk quota (VictoriaLogs
offers `-retention.maxDiskSpaceUsageBytes`). Payload volume, compression and
compaction cost are unmeasured. The Phoenix seven-day policy is not proof of
physical deletion: on 2026-10-24 the oldest surviving span (2026-10-19) was
still inside the window, so deletion remains unobserved.

### Exposure

Keep backend APIs internal/loopback, authenticate any UI, and qualify allowed
origins/networks. Prometheus supports TLS/basic authentication, but that does
not provide per-series authorization. No new public backend ports,
secret-bearing health probes, inference-on-scrape or privileged collectors are
needed for the proposed direction. Local-only binding does not protect content
from other local users.

## 8. Evidence, research method and remaining uncertainty

### 2026-10-20 research

Parallel research used `openai/gpt-6-luna:high` and `kimi-coding/k3:high`, matching
the registered model catalog. K3 completed without a quota failure; the approved
`zai-coding-cn/glm-5.3` fallback was therefore not invoked. No Anthropic model was
used. Their reports/downloads are preserved under
`tmp/grafana-comparison-research/{luna,k3}/`; coordinator checks are under
`tmp/grafana-comparison-research/coordinator/`.

The consolidation corrected preliminary research claims: the observer already
captures bounded full content, all four current log drivers are journald,
Phoenix and the private fixture are real, Prometheus supports web
TLS/authentication, and a full Tempo/Loki stack is not required for a single UI.
It also distinguishes same submitted tasks from unknowable hosted internal
inputs and locally computed Jev-style confidence from actual hosted answers.

**Credential-handling incident:** one K3 inventory grep included the live
OmniRoute dashboard password in that agent's tool output. No value is reproduced
in these documents; it may remain in the agent transcript/provider context.
Further config searches stopped. No credential was rotated on 2026-10-20. The
operator should change that dashboard password; whether that happened is
unknown as of 2026-10-24 (`models.yml` was modified 2026-10-24 10:41, values
were not compared; §12 Q4). This is not evidence that an API key leaked.
The private incident classification is `k3/incident-note.md` within the research
artifact root. Do not spread or commit the raw tool transcript.

### 2026-10-24 review

An Anthropic model (Claude Opus 5.5) re-checked the document read-only. Every
2026-10-24 number in this file comes from a command it ran that day or from a
cited source; RAM figures not labelled measured are estimates. Live-stack
access was read-only: `podman ps`/`inspect`, cgroup files and
`journalctl --user`; a dashboard login POST plus GETs on OmniRoute
`/api/monitoring/health`, `/metrics` and `/api/metrics`; GETs on Phoenix spans
and Laya `/health` (also via `jev_review.py --probe`); one `podman exec` into
Phoenix that tried `127.0.0.1:9090` and was refused (Prometheus disabled); one
`podman exec omniroute cat /app/package.json`. No passwords, keys or content
were printed.

### Remaining uncertainty

Still unverified: actual Laya/hosted-Jev paired outcomes (no remote rung is
usable), caller session propagation, a hosted history export, the OmniRoute
OTLP exporter on 3.8.53 and Phoenix's acceptance of OTLP/JSON, every
*estimated* RAM figure in §5, UI acceptance, ingestion loss/retention and
comparative observer overhead, the TypeSafe confidence formulas (not re-fetched),
the 82 archived pairs and 49,864 health records (private manifests not
reopened), and Pi's 527 M token outlier (§10). No Grafana, Prometheus,
VictoriaMetrics, VictoriaLogs, Loki or Tempo service was installed. No Laya/Jev
inference, backfill, production update, resource change or service restart
occurred in either pass.

Private evidence under ignored `tmp/` is not a backup carried by a Git clone.
Preserve/export it through an authorized private backup before cleanup; do not
commit real questions, answers or credentials to make the documentation durable.

### Public sources consulted

Vendor documentation can differ from pinned deployed versions; these sources
establish available concepts, not a completed local integration. Sources added
on 2026-10-24 were accessed that day.

- [Phoenix sessions](https://arize.com/docs/phoenix/tracing/how-to-tracing/setup-tracing/setup-sessions),
  [Phoenix configuration](https://arize.com/docs/phoenix/self-hosting/configuration)
  and [OpenInference conventions](https://github.com/Arize-ai/openinference/blob/main/spec/semantic_conventions.md).
- [TypeSafe index](https://docs.typesafe.ai/llms.txt),
  [HTTP API](https://docs.typesafe.ai/api.md),
  [OpenAPI](https://api.typesafe.ai/openapi.json), and
  [confidence definitions](https://docs.typesafe.ai/confidence.md).
- [Exact Laya 0.4.2 distribution metadata](https://pypi.org/pypi/laya/0.4.2/json).
- [Grafana Loki data source](https://grafana.com/docs/grafana/latest/datasources/loki/),
  [LogQL](https://grafana.com/docs/loki/latest/query/log_queries/),
  [structured metadata](https://grafana.com/docs/loki/latest/get-started/labels/structured-metadata/),
  and [Loki retention](https://grafana.com/docs/loki/latest/operations/storage/retention/).
- [Grafana Tempo data source](https://grafana.com/docs/grafana/latest/datasources/tempo/),
  [TraceQL](https://grafana.com/docs/tempo/latest/traceql/),
  [Tempo 3.0 release notes](https://grafana.com/docs/tempo/latest/release-notes/v3-0/), and
  [PostgreSQL data source](https://grafana.com/docs/grafana/latest/datasources/postgres/).
- [Alloy journal source](https://grafana.com/docs/alloy/latest/reference/components/loki/loki.source.journal/),
  [rootless Podman installation](https://grafana.com/docs/alloy/latest/set-up/install/podman/),
  [Promtail migration](https://grafana.com/docs/alloy/latest/set-up/migrate/from-promtail/)
  and [Grafana Agent](https://grafana.com/docs/agent/latest/).
- [VictoriaMetrics OpenTelemetry](https://docs.victoriametrics.com/victoriametrics/integrations/opentelemetry/),
  [VictoriaLogs](https://docs.victoriametrics.com/victorialogs/) and
  [VictoriaLogs OTLP ingestion](https://docs.victoriametrics.com/victorialogs/data-ingestion/opentelemetry/).
- [Prometheus security model](https://prometheus.io/docs/operating/security/),
  [prometheus-podman-exporter](https://github.com/containers/prometheus-podman-exporter),
  [cAdvisor issue 3628](https://github.com/google/cadvisor/issues/3628) and
  [node_exporter](https://github.com/prometheus/node_exporter).
- [OpenTelemetry GenAI conventions](https://github.com/open-telemetry/semantic-conventions-genai)
  and [GenAI spans](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-spans.md).
- OmniRoute (now `cheaper-inference/OmniRoute`, MIT):
  [otel.ts](https://github.com/cheaper-inference/OmniRoute/blob/main/open-sse/services/routing/otel.ts),
  [routing/index.ts](https://github.com/cheaper-inference/OmniRoute/blob/main/open-sse/services/routing/index.ts),
  [OpenCode session identity](https://github.com/cheaper-inference/OmniRoute/blob/main/docs/reference/OPENCODE-SESSION-IDENTITY.md)
  and [quota telemetry](https://github.com/cheaper-inference/OmniRoute/blob/main/docs/OMNIROUTE_QUOTA_TELEMETRY.md).
- Clients: [Claude Code monitoring](https://code.claude.com/docs/en/monitoring-usage),
  [OpenCode config](https://opencode.ai/docs/config/),
  [opencode-plugin-otel](https://github.com/devtheops/opencode-plugin-otel),
  [@gcornut/opencode-otel](https://cdn.jsdelivr.net/npm/@gcornut/opencode-otel@0.4.0/README.md),
  [SigNoz OpenCode guide](https://signoz.io/docs/opencode-observability),
  [Pi extensions](https://github.com/badlogic/pi-mono/blob/main/packages/coding-agent/docs/extensions.md)
  and [Cursor hooks](https://cursor.com/docs/agent/hooks).
- Rejected stacks: [Langfuse self-hosting](https://langfuse.com/self-hosting),
  [Langfuse Docker Compose](https://langfuse.com/self-hosting/deployment/docker-compose)
  and [OpenLIT installation](https://docs.openlit.io/latest/openlit/installation).
- Kernel: [cgroup v2](https://docs.kernel.org/admin-guide/cgroup-v2.html) and
  [DRM usage stats](https://docs.kernel.org/gpu/drm-usage-stats.html).
- Versions and licenses: GitHub REST `repos/<owner>/<repo>/releases/latest` and
  `repos/<owner>/<repo>`; image sizes: Docker Hub `v2/repositories/<repo>/tags/<tag>`.

## 9. How to know the resource limits work

Queries use the proposed sampler names (`llmenv_*`, `omniroute_*`) in
PromQL/MetricsQL; in phase 0 the same checks are `jq` over the JSONL.
Thresholds follow [benchmark-derived sizing](2026-10-24-benchmark-derived-sizing.md):
cap sized at peak + 20 % (peak/cap 0.83), guard high at 0.85 and critical at
0.92, healer acting at 0.60 of the cap.

| Check | Query | Pass criterion over ≥ 7 days of normal use |
| --- | --- | --- |
| Headroom | `max_over_time(llmenv_cgroup_memory_peak_bytes[1d]) / llmenv_cgroup_memory_max_bytes` | ≤ 0.83 for omniroute, phoenix, remote-setup; system-one is a declared exception (0.995 on 2026-10-24) |
| Cap hits | `increase(llmenv_cgroup_memory_events_total{event="max"}[15m]) > 0` | 0 except system-one, whose rate is tracked and whose cap is re-evaluated if it rises |
| OOM | `increase(llmenv_cgroup_memory_events_total{event=~"oom\|oom_kill"}[1h]) > 0` | Always 0 (page immediately) |
| Memory stall | `rate(llmenv_cgroup_memory_pressure_some_usec_total[5m]) / 1e6 > 0.10` for 10 m | Never; also watch `full` > 0.02 |
| Swap creep | `llmenv_cgroup_memory_swap_current_bytes / llmenv_cgroup_memory_max_bytes > 0.5` for 1 h | Warn: a tighter cap pushes demand into swap, which `memory.max` does not count |
| Guard shedding (503) | `increase(omniroute_pressure_guard_rejects_total[15m]) > 0` and `increase(omniroute_chat_admission_shed_total[15m]) > 0` | 0 or rare and explained |
| Pre-guard warning | `llmenv_cgroup_memory_current_bytes{service="omniroute"} / llmenv_cgroup_memory_max_bytes > 0.80` for 5 m | Warn before the 0.85 guard |
| Healer | `increase(llmenv_healer_actions_total{action!="none"}[24h]) >= 2` | ≤ 1/day; 2 is the built-in daily cap |
| Restarts | `changes(llmenv_container_start_time_seconds[1h]) > 0` without a matching updater/healer event | 0 unexplained restarts |
| Host | `llmenv_host_mem_available_bytes < 2 * 2^30` or `rate(llmenv_host_memory_pressure_full_usec_total[5m])/1e6 > 0.05` | Never |
| Laya fallback rate | `sum(rate(jev_items_total{needs_fallback="true"}[1d])) / sum(rate(jev_items_total[1d]))` | Informational; 80.4 % on 2026-10-23/24 (§3) |

`memory.peak` and `memory.swap.peak` are cgroup-lifetime values that reset when
a container is recreated; always read them with the container start time.
`increase()` tolerates counter resets on restart.

## 10. Host evidence of real LLM usage (aggregate only)

Measured 2026-10-24. Counts come from file mtimes (`find -newermt 2026-10-14`),
JSONL timestamps and read-only SQLite (`mode=ro`). Only `timestamp`,
`sessionId`, `model`, `provider`, `usage` and `type` were parsed; no message
content was printed and no key file was opened. A file touched on a later day
counts on that day.

**Claude Code** (`~/.claude/projects/**/*.jsonl`, 4.1 GB directory):

| Day (10-) | 14 | 15 | 16 | 17 | 18 | 19 | 20 | 21 | 22 | 23 | 24 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| distinct sessionIds | 8 | 5 | 3 | 4 | 5 | 2 | 2 | 2 | 0 | 8 | 2 |
| JSONL files modified | 63 | 94 | 67 | 64 | 64 | 90 | 0 | 0 | 8 | 122 | 20 |
| uncached in+out tokens (deduped by message id) | 1.81 M | 1.35 M | 1.03 M | 1.42 M | 0.90 M | 0.33 M | 0 | 0 | – | 0.83 M | 0.54 M |

Assistant records by model (not deduped): `claude-opus-5-5` 46,776,
`claude-sonnet-5-5` 8,368, `claude-fable-5-1` 2,913, `claude-haiku-5-5` 1,071;
34,368 distinct API responses. Many files per session are subagent transcripts.

**Pi** (`~/.pi/agent/sessions`, 304 MB): 434 sessions since 10-14; per day 13,
58, 64, 161, 20, 30, –, 1, –, 51, 36. Assistant messages: `router-env/my-coding`
(OmniRoute combo) 16,602, `openai/gpt-6.1-sol` 1,235, `zai-coding-cn/glm-5.3`
628, `opencode/glm-5.3-flash` 212, `zai-coding-cn/glm-5.3-flash` 127,
`opencode/glm-5.3` 75, `kimi-coding/kimi-for-coding` 50, `openai/gpt-5.6-terra`
48, others ≤ 6 each. Usage-derived tokens show a 527 M outlier on 10-15 against
1-11 M on other days, probably cumulative or cached accounting; unverified, do
not trust it. Pi's only custom provider is `router-env` (OmniRoute at
`127.0.0.1:20128`); the default model is `openai/gpt-5.6-terra`.

**OpenCode** (`~/.local/share/opencode/opencode.db`, table `session_v2`, 8.5 GB
directory): 310 sessions since 10-14, 125 of them subagents; per day 25, 34,
57, 41, 47, 50, –, –, 5, 45, 6. Recorded tokens about 55.7 M input and 1.6 M
output; recorded cost $0.76, the rest $0 (combo routing). Session models:
`router-env/my-plan-review` 259, `openai/gpt-6.1-sol` 26,
`zai-coding-plan/glm-5.3` 9, `router-env/my-coding` 7, others ≤ 4.
`opencode.jsonc` has one custom provider, `router-env`, and plugins
`superpowers` and `@dietrichgebert/ponytail`.

**Cursor** (`~/.cursor`, 996 MB): chat store files modified on 10-14 (106),
10-15 (4), 10-16 (12) and 10-23 (4). Model setting `default`; one
`sessionStart` hook.

**Codex** (`~/.codex/sessions`): 14 session files between 10-14 and 10-18.

**Jev / Laya:** see §3 and the Phoenix update in §1.

**What this says.** OmniRoute (`router-env`) carries the bulk of Pi and
OpenCode traffic, so its call-logs or OTLP spans are the best single traffic
source. Claude Code talks to Anthropic directly and needs its own OTel. Laya's
real caller is ai-kit's precompact path, at hundreds of items a day.

## 11. ai-kit TODO list (for the owner to relay)

This repository does not edit ai-kit. Copy these into the ai-kit maintainer's
backlog.

1. **Jev client, propagate identity.** Send `x-session-id`, `x-run-id`,
   `x-jev-caller` and a W3C `traceparent` on every rung request (Laya,
   TypeSafe, Zen), from new env vars `JEV_SESSION_ID`, `JEV_RUN_ID` (UUIDv4
   when unset) and `JEV_CALLER`. Bound and validate values (≤ 128 chars,
   `[A-Za-z0-9._:-]`). Never derive them from timing.
2. **Jev client, log the ladder, not only the last rung.** Add `session_id`,
   `run_id`, `attempt`, the full `trail` (backend, model, outcome, HTTP status,
   latency_ms per rung), `escalated`, `consent_used`, `threshold` and `usage`
   to `jev-answers.jsonl`.
3. **Jev client, privacy.** Make `instructions` opt-in (`JEV_LOG_TEXT=1`) and
   log a hash by default. Rotate `jev-answers.jsonl` (for example daily files
   kept 30 days).
4. **ai-kit-precompact Pi extension.** Pass `sessionManager.getSessionId()` as
   `JEV_SESSION_ID` and a per-compaction `JEV_RUN_ID` to the Python core.
5. **New small Pi extension.** On `before_provider_headers`, add
   `x-session-id: <pi session id>` (and `traceparent`) only when the provider is
   `router-env` (OmniRoute); never for third-party providers without review.
6. **OpenCode.** Add the same header for the `router-env` provider (config
   `headers` or a plugin); confirm which header OmniRoute keys on (it knows
   `x-opencode-session`). Do not adopt community OTel plugins that put prompts
   in `input.value` unconditionally.
7. **Cross-AI dispatch** (`pi -p`, `claude -p`, `cursor-agent`, the
   `cross-ai.mjs` runner). Mint one `AIKIT_RUN_ID` per dispatch; export
   `OTEL_RESOURCE_ATTRIBUTES=aikit.run_id=<id>,aikit.parent_session=<id>,aikit.role=<reviewer|executor>`
   and `JEV_RUN_ID=<id>` to the child; append start, end, exit code, runtime,
   model and output path as one JSON line to `${XDG_STATE_HOME}/ai-kit/dispatch.jsonl`.
8. **Claude Code profile (optional, owner opt-in).** Ship the documented
   phase 1 env snippet (§5) with prompts and tool details left unset.
9. **ai-kit-usage-metrics.** Read `jev-answers.jsonl` and `dispatch.jsonl` so
   the usage dashboard shows Laya items per caller and the fallback rate.
10. **Contract test.** Assert that no request body or header ever contains a
    key value and that header values pass the validation in item 1.

## 12. Open questions for the owner

1. **Content or metadata by default for Laya traces?** Move the observer to
   hash plus shape by default with content opt-in, replacing the 2026-10-19
   full-content capture?
2. **Is one Grafana UI a hard requirement,** or are vmui, the VictoriaLogs UI
   and Phoenix acceptable through phase 2?
3. **Remote Jev escalation is effectively off:** TypeSafe has consent but no
   key, and Zen has neither. Is that intended?
4. **Has the OmniRoute dashboard password been rotated** after the credential
   incident described in §8?
5. **The untracked `.docs/` duplicate copy:** delete it or keep it?
6. **Retention targets:** how many days or GiB for metrics, logs and decision
   records?
7. **May a throwaway OmniRoute container be started** to verify
   `OMNIROUTE_OTEL_ENDPOINT` on the deployed version?
