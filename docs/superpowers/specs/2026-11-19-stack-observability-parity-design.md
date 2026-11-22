# Stack observability and Laya parity

The [Grafana-first prototype](../plans/2026-11-19-observe-prometheus.md) proved
metrics delivery. It did not deliver stack-wide traffic coverage, Laya request
history, or a Phoenix replacement. The follow-up keeps the useful dashboard
while closing those gaps. It draws on the
[October research](../research/2026-10-20-laya-jev-grafana-observability.md)
and [original Observe plan](../plans/2026-10-24-observe-mvp.md).

## One lifecycle and one dashboard

- Make the collector a service of the generated llm-env Compose stack, with
  Grafana, the metrics store and the selected request-detail store. Stack
  membership, including optional services, comes from the enabled Compose
  services, not from an independent list of currently running container names.
  Include monitoring services themselves; exclude trial and unrelated containers.
- Qualify rootless access to owned cgroups, Laya GPU counters and required
  service endpoints in staging. Do not add privileged mode, a Podman socket
  mount, or anonymous/LAN access to Grafana. Do not call coverage complete if a
  required source is inaccessible. The rendered Compose service list defines
  membership, but it does not prove a container is running. Test runtime
  identity across stop, restart, and recreation without counting stale
  cgroups or unrelated containers.
- Reuse VictoriaMetrics and its existing volume unless a measured comparison
  shows that Prometheus would improve this deployment. Neither makes application
  request counts or traces by itself. Prefer structured request events in a
  separate Grafana-queryable store (qualify VictoriaLogs first). Use a trace
  backend instead if the parity check needs cross-service spans, not just
  Laya's current single-span requests.
- Preserve OmniRoute, Phoenix, Grafana, and VictoriaMetrics data. Map existing
  volumes explicitly before moving services between projects. Compare staged
  and current sample streams before turning off the host timer to prevent
  either a gap or duplicate collection. Stage with separate ports and
  disposable volumes. Do not restart the existing inference stack to validate
  the design. If production instrumentation or consolidation requires replacing
  a running service, apply it only after staging passes and in an approved
  window, using the existing idle-aware update path where applicable.
- `make status` shows Grafana's URL and status. `make show-secrets` may show
  the initial Grafana credential from its existing private file; ordinary
  status and start output need not repeat it.

## Signals and coverage

- Separate **enabled / running / stopped / unknown** containers from **collector
  and API availability**. The existing `llm_env_source_up{source="containers"}`
  is a binary source signal. It is not a count. At the initial live check,
  Podman showed seven owned running containers, but only four had
  `llm_env_container_running` samples. The dashboard must account for the
  missing `llm-server`, Grafana and VictoriaMetrics, as well as the eventual
  collector and detail store.
- Define ingress per HTTP service. Show arrivals, results by status class, and
  latency where the source can measure it. Separate readiness probes, scrapes,
  internal calls and provider tests from client traffic. Show per-service
  counts, not a sum that counts one user request again after each hop. Record
  what is measured and render missing coverage as unknown.
- Verify each source instead of assuming logs suffice. OmniRoute's health API
  reports operational counters; completed client traffic needs a separately
  verified request source. Laya has access logs and an existing request
  observer. `remote-setup` deliberately omits successful requests from its
  logs, so a journal parser alone cannot count them; add a counter to the
  service or qualify an ingress counter that cannot miss direct traffic.
  Qualify the exact deployed llama.cpp build's metrics, and Phoenix and the
  monitoring services' own HTTP counters, before promising their coverage.
  Never read the remote installer's `/config` body for telemetry.
- Carry the original plan's OmniRoute call views into Grafana: status,
  configured combo/model/provider, latency, and known input/output,
  cache-read, and reasoning token counts. Distinguish completed calls from
  arrivals and provider attempts. If a source cannot measure one of them,
  label the gap. Reconcile across collector and service restarts without
  counting the same event twice.
- Retain current/peak/cap memory and add interval CPU utilization, throttling,
  memory pressure, swap, restarts, saturation/queue and collection failures or
  drops. Show Laya process VRAM separately from whole-device GPU usage; only
  include attributable stack telemetry in the default views.
- Bring over the original resource trends: p95 resident memory, headroom,
  cap hits, OOMs, admission shedding, and healer/updater events for explaining
  restarts. Use the research's headroom and pressure thresholds. Include the
  monitoring services' own resource and storage cost, and verify the budgets
  and `/var` free-space guard from research §12.

## Laya detail and Phoenix parity

- Audit Phoenix using its schema and selected metadata, without printing
  captured request/response bodies. The existing Laya observer and recent
  spans show single/batch endpoint, outcome, duration, batch size, requested
  questions, completed answers, model, routing and token use when known, plus
  capped, redacted input/output. Build a Grafana request table and detail view
  covering those fields, plus capture/truncation flags. Default to hash and
  shape. Keep payload capture explicitly opt-in, redacted, bounded, local,
  and retained for no more than seven days. No request IDs or payloads in
  Prometheus labels.
- Deliver the research extras: real session/run/caller correlation when the
  caller supplies it, a session timeline, decision type and the source's
  confidence/top statistic, `needs_fallback` versus a fallback actually taken,
  and telemetry drop counts. Distinguish a batch HTTP request from its items
  and decisions. Keep unknown fields unknown. A fallback taken outside Laya
  requires the client's evidence; never derive it from low confidence or
  invent a session from timestamps. The caller-side changes belong in
  ai-kit; a synthetic sender can verify the integration in staging but does
  not prove that real callers send those fields.
- Keep Phoenix running and preserve its history during the comparison. Compare
  request IDs, totals, failures, and visible details on controlled traffic;
  account for drops and truncation explicitly. Phoenix can be retired only
  after equivalence of the views and 30 days of parallel count agreement, as
  specified by the research. Use metadata counts for the comparison; it
  does not require 30 days of payload retention. Confirm the replacement
  views with the owner. Export or retain existing history before a
  retirement that would remove access to it.

## Laya build visibility

Stream real Podman build output and named stages (image build, weight prefetch,
smoke test, idle wait and swap) while keeping bounded failure logs. The updater
currently redirects `system-one-build.sh` output into `build.log`; the observer
image build captures stdout because its caller consumes the image tag. Keep
that tag alone on stdout and stream derivative build progress on stderr. Test
that build failures still propagate and leave the running image untouched.
Do not invent completion percentages.

## Definition of done

1. A fresh staged stack starts and stops the collector and monitoring services
   through the same Compose lifecycle. An unrelated container never appears.
   Running, stopped and unknown service fixtures appear correctly.
2. Controlled successful and failed requests reconcile with the dashboard for
   each enabled HTTP service. Health and scrape traffic are reported
   separately. Missing sources are visible, not shown as zero. Verify
   the dashboard against known call counts, resource pressure, restarts,
   and missing samples as well as healthy traffic.
3. Grafana can inspect representative Laya single, batch, error, and truncated
   records and show a genuinely correlated caller session and fallback.
   Phoenix remains available until the separate retirement gate passes.
4. A cold and a cached Laya build show real progress in the terminal. The
   failure case remains safe. The rollout preserves existing data and has a
   verified rollback that does not remove volumes.
