# HTTP request inventory (T10) and per-request Laya table

Status: PLAN, owner approved building both on 2026-11-21 (answers to the final question set). Parent plan:
`2026-11-20-recovery-and-usage-observability.md` (T10 text, M5/M6). Live observe worktree: `.claude/worktrees/observe-prometheus`
(1-minute collector timer: edit only in a staged copy under `tmp/stage/<task>`, install by verified copy with backups).

## Part A. T10, request accounting for every HTTP service

### A1. Inventory (read-only, no code)
Output `tmp/forensics-20261120/T10/t10-http-inventory.md`. One row per enabled listener: OmniRoute 20128, System One 8010,
remote-setup 20130, Grafana 3000, VictoriaMetrics 8428, llm-server 8000. Phoenix is retired (list as removed, not a gap).
Columns: verified native signal for arrivals, completions, status class and latency (name the metric or log field and where it is read);
what is missing; the smallest instrumentation. Constraints: no monitoring proxy container (C46) unless it provably misses no direct
ingress; remote-setup successes are absent from its access logs, so it needs a counter; never read its `/config` body; never print keys.
Evidence by controlled requests only against endpoints that are safe (health, metrics, a 404), counters read before and after.
Acceptance: every row has a measured signal or an explicit "unknown" with the reason; the file lists named follow-ups A2a, A2b...

### A2. Implementation (named after A1, one task per service)
Each task: collector change in `pylib/observe_metrics.py` (a new source with its own `llm_env_source_up{source=...}`), tests, one dashboard
panel group in `llm-env.json` or its own dashboard, a controlled success and failure request with expected counter deltas, and an exact
acceptance command. Probe traffic (the collector's own health checks) is labelled and excluded from the counts. Unknown stays "unknown",
never zero. Already covered, so out of scope: OmniRoute usage rows (source `omniroute_usage`), Laya spans, llama.cpp `/metrics`.

## Part B. Per-request Laya table and timeline

Source: the span file `~/.local/state/llm-env/laya-spans/laya-spans.jsonl` (one JSON line per HTTP request; 4,867 lines, 1.7 MB at the
time of writing). Fields that may be shown: `ts`, `span_id`, `endpoint`, `batch`, `batch_items`, `http_status`, `duration_ms`,
`requested_questions`, `completed_answers`, `answers` (noul, choice, score counts), `confidences`, `error_type`, `truncated`. Question text,
answers and ids are never in the file and must not be added.

### B1. Decide the feed (measured, one short task)
Grafana reads only VictoriaMetrics. Option 1 (preferred): emit one series per recent request, labelled `span_id`, `endpoint`,
`http_status`, `error_type`, with the request timestamp as the sample timestamp and the duration as the value, plus companion gauges for
confidence (max, mean), answers by type and batch size. Bound it to the newest 200 requests per collector run so a run stays small.
Measured 2026-11-22: 4,187 spans on the busiest day (benchmarks) and 680 so far today; VictoriaMetrics holds 1,201 series now. One series per
request at the busy-day rate is about 1.5 million series a year (a thousand times today's count), so emit a series only for requests that
are errors, truncated, batched, or slower than 1 s, and cover plain successes with the aggregated per-minute rows of Option 2.
Cost gate: measure series growth over 24 h of real traffic (`/api/v1/status/tsdb` and the data directory size); if the projected
yearly index plus data exceeds 150 MB, fall back to Option 2 (aggregated per-minute rows by endpoint and status only). Do not add a
Grafana plugin (it needs network at start) without the owner.

### B2. Collector (`collect_laya_requests`)
New function next to `collect_usage`; reads only complete new lines with the same offset-state pattern as the Jev collector
(`~/.local/state/llm-env/observe/` state file, never the repo), tolerates a partial last line and rotation, and returns a valid
exposition fragment or `llm_env_source_up{source="laya_requests"} 0`. Tests: partial line, rotation, malformed line skipped and counted,
bound of 200, no forbidden field in the output, and the exposition-validity check used by the other sources.

### B3. Dashboard
In `laya.json`, under a new row `REQUESTS (per request)`: a table of the last 100 requests (time, endpoint, status, duration, questions,
answers by type, mean confidence, error type) sorted by time, and a timeline of duration by status class. Lints already in place (legends,
layout without gaps, descriptions that claim only what the query does, thresholds style) apply; add one for the new table columns.

### B4. Acceptance
Send 1 batch of 3 noul questions and 1 deliberate invalid request to Laya (value-free text, loopback), run the collector, and see both in
the table with the right counts and status within two collector cycles; `make validate` and a single `-n 4` run pass; the source stays
`up` when the span file is missing.

## Order and rules
A1 and B1 first (independent, read-only plus a measurement), then B2 and B3, then A2 per the inventory. Delegate to `pi
router-env/my-coding-lite` with a fresh Haiku verifier and named-test mutants per task, as in the earlier rounds. No commits, no
restarts of OmniRoute, one pytest at a time, scratch only under `./tmp/`.
