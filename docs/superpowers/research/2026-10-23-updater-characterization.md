# OmniRoute activity characterization — run record and review outcome

Executed 2026-10-23 as the Task 8 Step 3 bounded manual-review step of
`docs/superpowers/plans/2026-10-22-omniroute-updater-completion.md`.

Command (opt-in, disposable rootless Podman backend only, nothing under the live stack touched):

```bash
LLM_ENV_UPDATE_CHARACTERIZE=1 uv run pytest tests/test_omniroute_characterize.py -q -s
```

Result: **1 passed in 286.19s (0:04:46)**.

Artifacts (written by the harness, both uncommitted until reviewed):

- `docs/superpowers/research/2026-10-23-omniroute-characterization-038b2aba95a6.json` (6,666,180 bytes)
- `docs/superpowers/research/2026-10-23-omniroute-characterization-038b2aba95a6.json.sha256`
- `docs/superpowers/research/2026-10-23-omniroute-candidate-038b2aba95a6.json`

Image under test: unmodified `docker.io/diegosouzapw/omniroute:latest`, deployment
config id `sha256:038b2aba95a6...`, driven by `tests/fixtures/fake_inference.py`
(no inference, scripted chunk timing). Sampler cadence 0.5 s over
`/api/monitoring/health`, `/api/usage/history`, `/api/usage/call-logs?limit=1000&offset=0`,
journal records from `journalctl --output=json --follow CONTAINER_ID_FULL=...`.

## What was measured

| Fact | Measurement |
| --- | --- |
| Access-log shape | JSON `MESSAGE` with `level`, `time`, `service`, `module`, `tag`, `method`, `path`, `msg` |
| `logger.fields` | `{"method": "method", "path": "path"}` — field *names*, as the loader expects |
| Logged client path value | `/v1/chat/completions \| llama-cpp/fake-model \| 1 msgs` (the route is polluted by the model/summary suffix; `Profile.classify_route` already splits on `" \| "`) |
| Routes observed | `POST /v1/chat/completions` (admin polling endpoints do not emit access-log lines) |
| journald suppression | none: 200 burst requests produced 200 client-route lines |
| Health timestamp | `/adaptiveAdmission/resourceObservedAtMs` — integer epoch-ms, monotonic across the run |
| Health cache dwell | `health_cache_ms = 542` (same timestamp re-served for ~0.5 s) |
| Health refresh bound | `health_refresh_bound_ms = 39400` (worst gap between distinct values while idle — the admission subsystem only re-observes around activity) |
| Call-logs document | the response **is** the row array (root pointer `""`), row id field `id`, in-flight flag `active` |
| Boundary arrival | request fired 19.9 ms *before* a tick (`arrival_offset_ms: -19.9`) |
| Native shutdown drain | `podman stop --time 40` returned in 0.294 s with an open stream; the stream completed (`stream_completed: true`) |
| Model listing | a freshly registered provider's models are **not** listed in `/v1/models` until a sync cycle; `llama-cpp/<model>` routes still answer 200 |

## Why no activity profile can be checked in from this run

`pylib/omniroute_activity.py` requires, for an inference route, independent
arrival/inflight/queue signals plus these sources, each with a strict runtime type:

- `api_fields.active` — `_count(_ptr(history, ...))`, i.e. a **non-negative integer** in
  `/api/usage/history`. The measured history document exposes in-flight work only as the
  *array* `/activeRequests` (shape records it as `/activeRequests.length`), and the integer
  counters it does expose (`/totalRequests`, `/byProvider/.../requests`,
  `/pending/byModel/<model>`) are cumulative or queue-depth values, not an in-flight count.
  No pointer satisfies the rule, so the candidate reports
  `api_fields.active (integer candidates: [])`.
- `api_fields.pending_by_model` — the same rule applied to the queued-request window found no
  counter that returns to zero, so it is reported incomplete as well.
- `api_fields.admission`, `rows`, `row_id`, `row_memory_flag`, `health_timestamp`,
  `logger`, `routes` and the cache bounds **were** derived and are usable.

Consequence, exactly as the plan anticipated: `pylib/omniroute_activity_profiles.json`
stays `{"schema": 1, "profiles": []}`, `load_profile` defers, and
`make update-omniroute` reports `deferred: capability` for a running image. The updater is
therefore installed but inert on this host until either the profile contract or the measured
signals change.

## Decision needed from the user (not guessed by the executor)

To make an actual update possible, one of these has to happen; all three change reviewed
behaviour, so they belong to the user:

1. **Widen the runtime signal contract** so an array length is a legal in-flight count
   (`api_fields.active` accepts `/activeRequests` as a list and counts it). Cheapest, but it
   makes "active" depend on the same array the queue-depth rule already reads.
2. **Use the health document instead of history** for in-flight work
   (`/adaptiveAdmission/activeCount`, which this run *did* derive as an admission signal),
   and keep `/activeRequests` as a corroboration source.
3. **Accept `capability` deferral** as this host's steady state and drive updates manually
   (`podman pull` + `make start`) when an update is wanted.

Also worth the reviewer's attention: `health_refresh_bound_ms = 39400` is far larger than the
5 s sampling interval. The idle gate's "health must advance" rule would therefore stall until
the deployment-wide deadline whenever the router is genuinely idle, so a checked-in profile
built from this measurement needs a deliberate freshness tolerance rather than this bound.

## Reproduce

```bash
podman pull docker.io/diegosouzapw/omniroute:latest   # host prerequisite, once
LLM_ENV_UPDATE_CHARACTERIZE=1 uv run pytest tests/test_omniroute_characterize.py -q -s
```

The derivation rules themselves are covered by always-run unit tests
(`tests/test_omniroute_derive_candidate.py`, 8 cases) so `make test` exercises them without
Podman.
