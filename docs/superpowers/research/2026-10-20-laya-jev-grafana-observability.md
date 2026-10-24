# Laya/Jev observability through one Grafana interface

Date: 2026-10-20. Status: research, not an implementation plan or deployment.

## Scope and related research

The user wants one place to inspect container resources and real AI decisions:
request counts, questions/state, typed answers, confidence, actual fallbacks,
and sessions. The comparison of interest is **local Laya versus hosted Jev on
the same task**, including cases where both remain uncertain. The user prefers
Grafana over maintaining separate Grafana and Phoenix interfaces.

This research also preserves the other outstanding operational observations:

- [Container resources and a representative workload](2026-10-20-container-resource-sizing.md).
- [OmniRoute updater: current limitations and feasible isolation](2026-10-20-omniroute-updater-observations.md).

These files intentionally use the requested `./.docs/superpowers/research/`
path. Earlier plans and research under `docs/superpowers/` remain unchanged.
They are evidence and background, not approval to deploy the proposals below.

## Conclusions

1. **Activity since October 14 is recoverable:** 574 native HTTP access events
   were recovered from journald. They do not contain historic confidence or
   prove that hosted Jev was called.
2. **Zero Phoenix sessions is an instrumentation gap:** the observer creates
   independent request traces but sets no `session.id`.
3. **A fallback flag is not a fallback request.** The inspected helper returns
   `needs_fallback`; it does not invoke a second engine. Its documented caller
   action is full-model reasoning, not an automatic Laya-to-Jev cascade.
4. **The existing confidence fields are different statistics.** Laya's
   `x_jev_confidence` is a local transformation, not evidence of a Jev response.
5. **Grafana can be the only interface.** Prometheus supplies numeric metrics;
   Loki can supply container logs and structured decision history. Tempo is an
   optional trace backend, not a prerequisite for viewing questions and answers.
   None of these backends supplies missing session/fallback semantics.
6. **Prefer a shared collector over a sidecar for every container.** Owned
   cgroups, AMD sysfs and the current journald driver provide useful source
   data without privileged recipes. Packaging and exporter overhead remain
   untested.

## 1. What can actually be recovered

### Historical access events

The completed recovery scanned 50,565 journal records for the current host user
and `CONTAINER_NAME=system-one`, across five container lifetimes. The query
started at October 14 local midnight and ended at
`2026-10-20T12:18:45.475Z`. It finished successfully without reaching its scan
limit or deadline. It excluded 49,864 health access records and three other
HTTP records.

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
  `aec62d42093ec35aa03205ccc90b287c8a28d914171177fbdf1fdb60d24711e8`.
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

The pairs overlap access events: **do not add 82 to 574**. Keep undated pairs in
an undated archive; folder names and modification times are not event times.
The manifest is
`tmp/research-2026-10-20/history/combined-receipt-manifest.json`.

### Recent Phoenix records, independently checked again

A bounded GET-only listing on October 20 returned all 49 current spans, without
a continuation cursor. No application inference was run. Node and an independent
jq extraction agree on these aggregates:

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
semantics, deduplicate sources, and qualify Loki's age/out-of-order limits.
This research performed no import, trace fabrication, or request replay.

## 2. Why requests exist but sessions do not

Phoenix groups traces into sessions using the OpenInference **`session.id`**
attribute. One session can contain many traces. A trace ID and a session ID have
different meanings.

`docker/system-one/phoenix_observer.py` currently generates a fresh random trace
ID per native HTTP request, emits a root span, and does not set `session.id` or
propagate an inbound parent trace. Zero sessions is therefore expected; it does
not mean Phoenix lost the request records.

The actual harness must supply a stable session identifier through a verified
runtime integration. Client instrumentation should associate it with all engine
attempts and carry it to server telemetry where supported. The server cannot
reliably infer a conversation from a shared IP, process name, project directory,
time window, or randomly generated trace ID. Missing session identity must stay
unknown. No particular Pi/OpenCode environment variable or hook API was
qualified in this research.

## 3. What “fallback” and “confidence” mean here

### Current helper behavior

The inspected helper is
`${HOME}/.claude/skills/jev-fast-decision/scripts/jev_review.py`.
It sends one inference request to its configured endpoint. Its default endpoint
is OpenCode Zen; `JEV_API_URL` can select local Laya. It validates the response
and returns `needs_fallback` on each answer. It does **not** make a second call.
The accompanying skill describes full-model reasoning as a caller action.

Different events must remain separate:

- Laya returns an uncertain answer or an abstention flag.
- The helper recommends further reasoning.
- A caller actually requests hosted Jev.
- A caller performs a full reasoning-LLM review instead.
- OmniRoute switches providers because of quota, errors, or availability.

The last two are not automatically Laya-to-Jev fallbacks. A server observer
covering only local Laya cannot see later client actions or off-host calls.
An external caller might implement a cascade, but the inspected helper does not
prove one. Unknown executed-fallback counts must not become zero.

### Preserve the statistic, engine and policy

| Field/statistic | Meaning |
| --- | --- |
| Laya Choice/Score `confidence` | Normalized distribution concentration, `1 - H(p)/log(n)`; not a correctness probability |
| Laya Noul `confidence` in the current non-strict responses | Winning-side probability, `max(p_yes, 1-p_yes)` |
| Laya `answer_confidence` | Probability mass on the winning answer, when reported; no automatic calibration guarantee |
| Laya `x_jev_confidence` | Jev's formula applied locally to **Laya's** distribution; no Jev API call |
| Hosted TypeSafe Choice `confidence` | `(p_max - 1/n)/(1 - 1/n)`, according to current public docs |
| Hosted TypeSafe Score `confidence` | Distribution spread around the modal ordered level, normalized against uniform spread |
| Hosted TypeSafe Noul | Probability of yes; public docs specify no separate returned confidence |
| Current helper gate | Choice/Score `p_max < 0.7`, or Noul `max(p_yes,1-p_yes) < 0.7`, unless overridden |

The running Laya `common.py` and `serve.py` were copied read-only and matched
byte-for-byte against the public 0.4.2 wheel. That wheel's SHA-256 is
`126284550840e8fc2445f9cd4c808e505d25d457242e7de5658b2cb9f9155b26`.
Its source explicitly distinguishes these statistics and computes
`x_jev_confidence` locally. The current readback contains `answer_confidence`
on all 81 answers and `x_jev_confidence` on all 57 Choice/Score answers.

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
| Versioned submitted-task fingerprint | Binds the state, instructions, criteria/levels, type and ordering used for both attempts |
| Per-engine effective-input information, where exposed | Records truncation, dropped tokens, routing and transformations; unknown remains unknown |
| Engine/provider, requested/returned model, typed answer, distribution, statistic | Supports honest side-by-side inspection |
| Gate statistic/threshold/policy and reason | Explains why an escalation was requested |
| Suggested/started/completed/failed/timed-out events | Establishes actual execution rather than inferring it from a flag |
| Timestamp semantics, latency, available usage, truncation/export loss | Makes missing, late and incomplete records visible |

Preserve the already-authorized **bounded full request/state and typed-answer
content**, with credential exclusion/redaction, in protected log/trace records.
A metadata-only design would not meet the requested question/answer inspection.
Keep small query fields separate from the larger JSON content.

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
  / all decisions; suggestions without a start remain distinct.
- Hosted-Jev completions, failures/timeouts, and still-unobserved outcomes.
- Paired outcomes: Laya-low/Jev-high, both-low, both-high, disagreement,
  incomplete pair, error and timeout. “High” means a named policy threshold,
  not proven correctness.
- Session timelines with the actual engine selected and final decision outcome
  when the caller records it.

Emit a small terminal comparison record containing both sides when possible;
this makes a Loki comparison table practical without assuming LogQL performs
arbitrary joins between two independent attempt records. Preserve pending or
orphan attempts when a caller crashes before that record exists.

Fallback-only comparisons are selected for difficult Laya cases. They describe
the escalation policy, not overall comparative quality. Unbiased shadow sampling
would be a separately enabled, costed workflow; do not silently duplicate all
requests to hosted Jev. Agreement, confidence uplift and correctness are distinct.
Accuracy/calibration require labeled outcomes and a stated evaluation method.

## 4. Can a Jev API key recover past data?

Public TypeSafe documentation describes bearer-authenticated inference,
`POST /v1/systemone`, per-response token usage, and `GET /v1/models`.
The inspected public API/OpenAPI and documentation index expose no documented
historical request/response or account-usage export endpoint.

That finding does not prove that no console export or support-assisted export
exists. It means **an inference key is not verified history-export access**.
Provider retention, export fields/windows and account permissions remain unknown.
No key, account console or authenticated API was accessed for this research.

The existing helper's OpenCode Zen key handling is also not evidence of direct
TypeSafe account-history access. Distinguish `typesafe_direct`, `opencode_zen`
and local Laya in provenance. A future client can record its own hosted-Jev
responses/usage going forward; it cannot recover unrecorded history merely by
obtaining an inference key.

## 5. One Grafana interface: credible options

Grafana is the interface, not the storage or an automatic AI semantic collector.
Several data sources can coexist behind **one** Grafana URL.

| Candidate | What it supplies | Limits and costs |
| --- | --- | --- |
| Grafana + Prometheus + Loki, with shared collection | Container/host numeric metrics, logs, structured decision records, JSON details and session/comparison dashboards | No native span tree; comparison/session UX and terminal records require instrumentation/dashboard work |
| Add Tempo to the above | Distributed trace trees, duration/parent relationships, TraceQL search and trace/log/metric links | Another backend, volume, retention policy and resource budget; generic trace attributes are not Phoenix's LLM UI |
| Grafana + PostgreSQL for decision records | Built-in SQL data source, explicit joins and JSON storage for paired outcomes | Requires a maintained event ingester/schema/database; does not automatically collect infrastructure metrics or provide log/trace storage |
| Keep Phoenix and add Grafana | Preserves the current specialized trace UI | Two interfaces; conflicts with the stated preference unless a deliberate transition only |
| Alloy or an OTel Collector alone | Receives/processes/forwards telemetry | Not a history database or dashboard |
| A sidecar per container | Can forward local signals | More processes/lifecycle coupling; does not solve host permissions or recover missing AI semantics |

**Research recommendation:** assess the Loki-first option against the actual
low-volume workload before adopting the full Grafana/Prometheus/Loki/Tempo
stack. It directly covers the requested history tables and resource dashboard
without requiring two interfaces or a trace backend. Add Tempo if distributed
trace navigation is a demonstrated need. This is a reasoned starting point,
not a measured RAM/latency advantage or an approved architecture.

Loki's warning about expensive large-JSON parsing is real, but is not evidence
that this small local workload cannot use structured decision logs. Extract
frequently queried fields or use unindexed structured metadata; keep the full
bounded JSON for details. Native Grafana log views, table transformations and
data links can expose content and navigate a session. A polished chat viewer,
annotation/evaluation workflow or Phoenix-equivalent AI dashboard remains work.
No backend combination supplies those automatically.

Tempo's generic span metrics derive calls/errors/durations; they do not
automatically turn a `confidence` attribute into a numeric confidence histogram.
That requires explicit instrumentation or a qualified transformation. Putting
a floating confidence value into a metric dimension creates unwanted series.

Migrating the existing observer is also **not an endpoint-only configuration
change**: it uses Phoenix's REST payload and an explicit local endpoint
allowlist, not OTLP. Its bounded full-payload parser/redaction is reusable;
transport, context propagation, lifecycle, freshness checks and retention need
qualification. Preserve the Phoenix volume/history until any chosen migration
and historical readback have been verified. Nothing was stopped here.

## 6. Collection coverage for every owned container

An October 20 selected-field inspection confirms all four current services are
healthy and use **journald**: OmniRoute, System One/Laya, remote-setup and Phoenix.
The normal engine identities remain `44b8702af6cd`, `8b0ef2bb3920`,
`fce05b8857d5` and `19e65850edfb`, respectively. The existing historical journal
recovery already proves user-scoped access to Laya logs; it is not just a
hypothetical log-driver choice.

| Scope | Resource/log signals | Application-level signals or gap |
| --- | --- | --- |
| Every current owned container | CPU time/quota/throttling; charged RAM, anon/cache, peak, swap, limit events; health; stdout/stderr journal | Identify by owned service and verified current cgroup, not a stale ID or delegation root |
| Laya | The above plus device-level GPU context | Request/answer/usage collection already exists; passive metrics and caller/session/fallback linkage do not |
| OmniRoute | The above plus request/provider logs | Clone source has JSON provider/usage metrics routes; these are not a verified deployed Prometheus endpoint or Laya-fallback history |
| remote-setup | The above plus endpoint availability/errors | `/setup.sh` readiness and `/config` backend failures have different meanings; redact credentials and never collect config response bodies |
| Phoenix while retained | The above and its readiness | Optional own metrics do not monitor other containers or AMD hardware |
| llama.cpp if enabled later | The same container coverage | Qualify that exact server's metrics flags/endpoint; currently disabled, not “zero traffic” from a missing scrape |
| New monitoring services/collectors | Their own CPU/RAM/swap, health, logs, queue/loss and disk growth | Include self-monitoring so collection cost is visible |
| Host and AMD devices | MemAvailable, CPU/IO/memory PSI, swap activity; discovered GPU busy/VRAM | Whole-device GPU measurements include other clients; not automatic per-container attribution |

The intentionally retained synthetic fixture on port 42877 uses a separate
private store. It is not normal production traffic or a disposable orphan.
Keep it outside normal collection/history counts. Do not enumerate unrelated
containers or collect other user applications' payloads.

### Least-privilege direction

- Use one user-owned host collection layer for the owned stack. Direct reads of
  the owned cgroup files and AMD sysfs are proven; a production exporter and its
  container/namespace packaging are not.
- Alloy's `loki.source.journal` can forward journal records with field filters.
  Prove its actual reader access, cursor persistence, restart/dedup behavior and
  scope; do not assume journalctl access alone qualifies a packaged collector.
- Do not grant system-journal group membership or host privileges by default.
  Do not mount a Podman/Docker socket and call it read-only security because
  the mount has `:ro`: that does not make the API read-only.
- The reviewed cAdvisor and AMD exporter privileged recipes are unsuitable here.
  A node exporter is host-level, not automatic rootless per-container coverage.
- A sidecar or passive network observer cannot infer a caller's escalation
  decision, session identity or encrypted hosted response content. Client-side
  semantic instrumentation is still required; no TLS interception is proposed.

## 7. Retention, cardinality and operational safety

Prometheus stores bounded numeric series. Keep question/state/answer content,
request/session/decision IDs and arbitrary messages out of labels. Use bounded
service, engine, question-type, outcome and policy dimensions; record unknown
usage separately rather than writing zero. Confidence distributions need named
statistics and appropriate question/option-count grouping.

Loki can keep request IDs as structured metadata or ordinary fields rather than
indexed labels. Structured metadata requires the supported TSDB/schema format.
Tempo can keep span attributes, but payload-size/attribute limits must be
qualified against the existing 24,000-byte direction and 65,536-byte record
bounds. Preserve truncation and exporter-loss indicators.

Configure and verify retention explicitly: Loki does not delete history merely
because it is installed. Retention is not a disk quota, and payload volume,
compression and compaction cost are unmeasured. Keep protected persistent
volumes and private scratch files; separate full content from fast query fields.
The existing Phoenix seven-day scheduled policy is not proof of verified
physical deletion or a storage ceiling.

Keep backend APIs internal/loopback, authenticate Grafana, and qualify allowed
origins/networks. Prometheus supports TLS/basic authentication, but that does
not provide arbitrary per-series authorization. No new public backend ports,
secret-bearing health probes, inference-on-scrape or privileged collectors are
needed for the proposed direction. Local-only binding does not protect content
from all other local users.

## 8. Evidence, research method and remaining uncertainty

Parallel research used `openai/gpt-6-luna:high` and `kimi-coding/k3:high`, matching
the registered model catalog. K3 completed without a quota failure; the approved
`zai-coding-cn/glm-5.3` fallback was therefore not invoked. No Anthropic model was
used. Their reports/downloads are preserved under
`tmp/grafana-comparison-research/{luna,k3}/`; coordinator checks are under
`tmp/grafana-comparison-research/coordinator/`.

The consolidation corrects preliminary research claims: the observer already
captures bounded full content, all four current log drivers are journald,
Phoenix and the private fixture are real, Prometheus supports web
TLS/authentication, and a full Tempo/Loki stack is not required for a single UI.
It also distinguishes same submitted tasks from unknowable hosted internal
inputs and locally computed Jev-style confidence from actual hosted answers.

**Credential-handling incident:** one K3 inventory grep included the live
OmniRoute dashboard password in that agent's tool output. No value is reproduced
in these documents; it may remain in the agent transcript/provider context.
Further config searches stopped. No credential was rotated. The operator should
change that dashboard password. This is not evidence that an API key leaked;
it also prevents an inaccurate blanket claim that no credential was read.
The private incident classification is `k3/incident-note.md` within the research
artifact root. Do not spread or commit the raw tool transcript.

Remaining unverified work includes actual Laya/hosted-Jev paired outcomes,
caller session propagation, a hosted history export, exporter packaging,
Grafana UX acceptance, ingestion loss/retention, monitoring resource cost and
comparative observer overhead. Existing content remains in Phoenix; no Grafana,
Prometheus, Loki or Tempo service was installed. No Laya/Jev inference,
backfill, production update, resource change or service restart occurred.

Private evidence under ignored `tmp/` is not a backup carried by a Git clone.
Preserve/export it through an authorized private backup before cleanup; do not
commit real questions, answers or credentials to make the documentation durable.

### Public sources consulted

- [Phoenix sessions](https://arize.com/docs/phoenix/tracing/how-to-tracing/setup-tracing/setup-sessions)
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
  [TraceQL](https://grafana.com/docs/tempo/latest/traceql/), and
  [PostgreSQL data source](https://grafana.com/docs/grafana/latest/datasources/postgres/).
- [Alloy journal source](https://grafana.com/docs/alloy/latest/reference/components/loki/loki.source.journal/)
  and [rootless Podman installation](https://grafana.com/docs/alloy/latest/set-up/install/podman/).
- [Prometheus security model](https://prometheus.io/docs/operating/security/)
  and [OpenTelemetry GenAI conventions](https://github.com/open-telemetry/semantic-conventions-genai).

Current vendor documentation can differ from pinned deployed versions. These
sources establish available concepts, not a completed local integration.
