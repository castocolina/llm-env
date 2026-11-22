# Grafana-first Observe MVP

Operators should be able to open Grafana on this Bazzite host and tell whether
container memory limits and OmniRoute admission are healthy over time. The first
delivery is a working dashboard with a metrics backend, not a new
`make resources-snapshot` report.

## Design

- Keep `make resources-snapshot` as-is. Retain the
  [October plan](../plans/2026-10-24-observe-mvp.md) as history. This design
  supersedes its implementation scope and moves the
  [research roadmap](../research/2026-10-20-laya-jev-grafana-observability.md)
  forward to a dashboard now.
- A host-side one-shot emits Prometheus exposition text. A systemd user timer
  runs it every 60 seconds and posts to local VictoriaMetrics. No resident custom
  exporter, second JSONL store, per-container agents, or Podman socket mount.
  VictoriaMetrics accepts standard Prometheus-format metrics and can later scrape
  additional exporters. Grafana uses its built-in Prometheus data source.
- Use existing cgroup sampling for `omniroute`, `remote_setup`, `system_one`, and
  `phoenix` as enabled, including Laya VRAM when available. Collect allowlisted
  cumulative counters and gauges from OmniRoute's primed dashboard health
  endpoint. Never collect call-log rows, journals, or raw API payloads. Request
  volume, latency, tokens, and Laya request counts are deferred rather than
  presented as approximate totals. No changes to Phoenix instrumentation.
- A source failure omits unavailable measurements. Real zeros survive. A
  separate source status distinguishes collection failures; do not label a
  missing metric as zero. Only fixed metric/label names and numeric values may
  leave the collector. Never print upstream bodies or credentials.
- Deploy separate rootless Podman Compose project `llm-env-observe` with two
  loopback-only ports: Grafana `3000` and VictoriaMetrics `8428`. Provision the
  dashboard and data source from files; require Grafana admin authentication.
  Deliver the initial admin credential through a rootless Podman external secret
  mounted as a file, never through Compose environment variables or `podman run`
  argv. Keep Grafana and metrics data in their own named volumes. State the
  retention policy explicitly: metrics retention is 365 days with a
  VictoriaMetrics free-space floor of 100 GB; no hard 2 GB storage cap is
  promised (the retired JSONL 2 GB/15% rules do not apply to the TSDB), and the
  admin-password bootstrap affects only a new Grafana volume — an existing
  volume is recovered by a Grafana admin reset, never by destroying the volume.
  Record this storage-policy difference and how to inspect volume/cgroup usage.
- Start/stop only the new monitoring project and timer. Never stop or recreate
  the existing stack for this feature, and never delete existing volumes. If
  deployment fails, leave the inference stack healthy and roll back only the
  new monitoring components, keeping their volumes.

## Acceptance

From a fresh deployment, an authenticated operator can open the provisioned
dashboard and see measured values for current/peak/limit memory, OOM/max events,
CPU, System One VRAM if readable, and OmniRoute pressure/traffic health. A manual
tick and a later scheduled tick both reach VictoriaMetrics; Grafana's data
source can query the results. Unavailable measurements read as gaps, not zero.
The existing OmniRoute container identity, running health, and mounts are
unchanged before and after deployment. Deployment is optional if it cannot be
finished safely before 22:00 local time; inference availability is not.
