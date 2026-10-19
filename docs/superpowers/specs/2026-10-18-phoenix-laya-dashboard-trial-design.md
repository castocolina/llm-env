# Phoenix trial for local Laya observability

Date: 2026-10-18
Status: Document revision only in this step; no implementation now. Round 1 GPT 6.1 Sol review found PHX-R1-001/002/003/005 (HIGH) and PHX-R1-004 (MEDIUM); retain those original labels and the frozen raw report. The authorized workflow is GPT 6.1 Sol fix/review rounds until zero unresolved Critical/High, then a fresh Claude Opus 5.5 HIGH-effort evaluation, then implementation of the approved plans only if approved. No renewed approval prompt is needed for that already-authorized sequence; a fix is not itself review approval.

## Intent and scope

Evaluate whether a containerized Phoenix dashboard meets the user's primary needs: historical request counts, latency, successes/errors, token usage, decision/request/result types, confidence, metadata and distributions over time. CPU/RAM/GPU/VRAM history is useful but secondary. Prefer containers for the dashboard and its test-data sender; no new host SDK/server installation.

This deliverable is **Phase A: isolated dashboard + synthetic traces**. It does not instrument Laya, collect production history, generate inference, modify the generated main compose file, or restart existing services. Production OmniRoute replacement is excluded. The review/approval gates below govern later execution; this revision neither implements nor starts the trial.

## Grounding and verified compatibility

Checked official sources on 2026-10-18:

- Latest GitHub release: `arize-phoenix-v20.23.0`, published 2026-10-16T03:16:52Z: https://github.com/Arize-ai/phoenix/releases/tag/arize-phoenix-v20.23.0 . Docker Hub confirms tag `20.23.0` and Linux AMD64/ARM64 images.
- Pin `docker.io/arizephoenix/phoenix:20.23.0@sha256:c542e7de1dc1fdd0f938de337d44ebefa7266e91391eed6e7978523d7f6e1f2f` (multi-platform index). Resolve the host platform at execution; this index is not an individual platform image ID.
- Official deployment docs support SQLite with `PHOENIX_WORKING_DIR=/mnt/data` mounted on a persistent volume: https://arize.com/docs/phoenix/self-hosting/deployment-options/docker . Configuration: https://arize.com/docs/phoenix/self-hosting/configuration . Check pinned source when current docs differ.
- Pinned source `src/phoenix/server/app.py` exposes `/healthz` and database-aware `/readyz`. The image is **distroless and nonroot**: do not assume it contains a shell, curl or wget. https://github.com/Arize-ai/phoenix/blob/arize-phoenix-v20.23.0/Dockerfile
- Pinned REST API supports `POST /v1/projects/{project_identifier}/spans` with `{"data":[span,...]}`, returning **202** and `total_received`/`total_queued`. A name can create a project through ingestion; insertion is asynchronous. Verify persistence by querying spans, not just accepting HTTP 202. Source: https://github.com/Arize-ai/phoenix/blob/arize-phoenix-v20.23.0/src/phoenix/server/api/routers/v1/spans.py
- Pinned REST span records include name, context trace/span IDs, kind, parent ID, timezone-aware start/end, status, attributes and events. Use this supported JSON API for the synthetic trial; it is **not an OTLP exporter**. Future real Laya instrumentation can use OpenTelemetry/OpenInference separately.
- Official upstream uses sender-suitable base `ghcr.io/astral-sh/uv:0.12.19-python3.13-trixie-slim`; registry inspection verified digest `sha256:aba5f865793af9275ceaa06d30935dfc6db8b81969c9fb099b0ebca6396b189e`. Pin tag + digest for the sender build. Project CLI requires only its existing PyYAML dependency; use stdlib HTTP/JSON, no new host dependencies.
- Retention is indefinite by default; a positive `PHOENIX_DEFAULT_RETENTION_POLICY_DAYS` changes the default policy. Cleanup is scheduled and project-specific overrides can differ; it is not a hard disk quota. https://github.com/Arize-ai/phoenix/blob/arize-phoenix-v20.23.0/docs/phoenix/settings/data-retention.mdx
- `PHOENIX_ALLOWED_PROVIDERS=NONE` hides provider options but does **not** enforce API/network denial. Disable external resources/analytics and unneeded MCP/sandbox features; validate network isolation rather than treating UI settings as a firewall. https://github.com/Arize-ai/phoenix/blob/arize-phoenix-v20.23.0/docs/phoenix/self-hosting/security/network-security.mdx

## Proposed topology and operating contract

Author choices, not additional user requirements:

- A separate compose project, default **`llm-env-phoenix-trial`**, containing one persistent `phoenix` service. A `sender` service is invoked explicitly as a one-shot container; ordinary startup never seeds data.
- A dedicated trial network, never the production network or host networking. Prefer compose `internal: true`; verify rootless Podman loopback publishing/egress behavior before calling it isolated. If unsupported, stop for a scoped design decision rather than changing host firewall rules or weakening isolation silently.
- Publish only **`127.0.0.1:${LLM_ENV_PHOENIX_PORT:-6006}:6006`**. Validate 1–65535 and fail clearly on a busy port; do not kill its owner. Do not publish gRPC 4317 or metrics 9090. Sender talks to `http://phoenix:6006` on the trial network.
- One external named volume **`${project}-data`**, created with trial ownership labels and mounted at `/mnt/data`. Refuse to adopt a conflicting/unowned volume. External declaration prevents ordinary compose teardown from owning deletion. No volume deletion command is included in this deliverable.
- **`LLM_ENV_PHOENIX_RETENTION_DAYS=7`** is the proposed positive, configurable trial default; set `PHOENIX_DEFAULT_RETENTION_POLICY_DAYS` accordingly and verify the effective policy. Seven days is a policy choice, not a measured storage bound. Do not set zero/infinite as an implicit fallback.
- Set `PHOENIX_WORKING_DIR=/mnt/data`, `PHOENIX_ALLOW_EXTERNAL_RESOURCES=false`, `PHOENIX_TELEMETRY_ENABLED=false`, `PHOENIX_ALLOWED_PROVIDERS=NONE`, `PHOENIX_ALLOWED_SANDBOX_PROVIDERS=NONE`, `PHOENIX_ENABLE_MCP_SERVER=false`, and `PHOENIX_ENABLE_PROMETHEUS=false`. Keep auth disabled only for this loopback-only synthetic trial; LAN/multiuser exposure requires a separate authenticated design.
- No GPU devices, privileged containers, container-engine socket, host filesystem mounts, production volume attachments or credentials. Copy only curated project CLI/modules and synthetic fixture files into the sender image; never copy the whole repository/build context blindly.
- Use existing host Podman/compose/uv tooling. Python commands remain `uv run llmenv.py <subcommand>` on host and in the sender. Warm the sender's CLI environment at build time, then use `UV_OFFLINE=1` at runtime and prove it works without downloads. No tracing dependencies are added to root `pyproject.toml`.
- Lifecycle actions: **up, seed, status, verify-persistence, stop**. `up` starts the dashboard; `seed` explicitly starts a one-shot sender. Status and CLI `verify` are read-only. `verify-persistence` explicitly recreates only the trial server and runs a one-shot verifier; it must announce the recreation and never reseed. Stop removes only trial containers/network, preserving its external volume and built images. Nothing is wired into main `make start`, `stop`, `clean`, boot or systemd lifecycle.
- Inspect existing production container IDs and `StartedAt` before/after the trial as read-only non-interference evidence. Do not read their logs, databases, credentials or invoke inference endpoints. Unrelated changes during the experiment must be reported, not attributed automatically to the trial.

## Synthetic dataset and metric semantics

Use a checked-in manifest with **12 root request spans: 9 OK and 3 ERROR**, plus **one OK child span** under the first root to expose span/request double counting. Therefore 13 spans do not mean 13 requests; the request error rate is **25%**, not 3/13. Each invocation gets a new run ID and nonzero valid hex IDs; do not delete previous runs or automatically retry ambiguous ingestion.

- Root durations in milliseconds: **10, 20, 30, 40, 50, 60, 70, 80, 90, 100, 200, 500**. Assign the final three roots ERROR with synthetic 422/500/503 statuses. Assert median **65 ms**; record the UI's percentile convention rather than imposing an undocumented p95 formula.
- End times are spaced three minutes apart backward from the invocation's UTC base time; starts subtract duration. All timestamps are timezone-aware, past/current, within retention, and synthetic—not evidence of actual server/client timing.
- Cycle requested types `choice`, `score`, `noul`. Roots 2 and 6 represent batches of 3 and 2 items respectively; use 2 questions per item on root 2 and 1 per item elsewhere. Distinguish HTTP calls (12), requested items (15), requested questions (18), completed answer count (15) and batch calls (2). Failed roots contribute no completed answers.
- Each successful root carries 100 synthetic input tokens and 2 output tokens: **900 input / 18 output** total. Failed roots omit unknown usage rather than assert zero. Child span carries no token usage, preventing double counting.
- Root `span_kind=LLM` is a synthetic typed-inference operation, not a claim that Laya accepts chat completions; child kind `CHAIN` represents synthetic validation. Include standard `llm.model_name`/token-count attributes and separately named `laya.*` metadata for counts, request type, safe fixture result and available confidence fields.
- Every span includes `metadata` JSON identifying `data_source: synthetic`, run ID and fixture version; project **`laya-phoenix-trial`** remains distinct from any future real data. No state, prompts, secrets, real questions or production results.
- Preserve the distinction between Laya `confidence`, `x_jev_confidence`, `answer_confidence`, and a noul probability. Do not replace absent confidence with zero or portray confidence as correctness.
- No cost price mapping or savings is fabricated. Tokens/timing here are fixture values. Future savings require comparable workload baselines and stated prices, and must be labeled estimates.

## Versioned fixture and artifact contract

These are author-selected local interchange contracts, not claims about upstream Laya responses. Use UTF-8 JSON objects, reject duplicate keys, unknown keys in local contracts, unsupported versions, booleans as integers, NaN/Infinity and malformed values. Serialized objects use sorted keys and compact separators; timestamps are UTC with millisecond precision (`2026-10-18T16:00:00.000Z`). No new schema package is needed. Validators return explicit typed structures, not unchecked dictionaries.

### Manifest v1

Required keys: `schema_version: 1`, `fixture_version: "laya-synthetic-v1"`, `project: "laya-phoenix-trial"`, `spacing_seconds: 180`, `roots: RootSpec[12]`, `child: ChildSpec`, `expected_summary: Summary`. Each RootSpec has exactly `key: str` (`r01`–`r12`), `duration_ms: int > 0`, `request_type: "choice"|"score"|"noul"`, `items: int > 0`, `questions_per_item: int > 0`, `status: "OK"|"ERROR"`, `http_status: int`. The complete rows are:

| key | duration_ms | request_type | items | questions_per_item | status | http_status |
| --- | ---: | --- | ---: | ---: | --- | ---: |
| r01 | 10 | choice | 1 | 1 | OK | 200 |
| r02 | 20 | score | 3 | 2 | OK | 200 |
| r03 | 30 | noul | 1 | 1 | OK | 200 |
| r04 | 40 | choice | 1 | 1 | OK | 200 |
| r05 | 50 | score | 1 | 1 | OK | 200 |
| r06 | 60 | noul | 2 | 1 | OK | 200 |
| r07 | 70 | choice | 1 | 1 | OK | 200 |
| r08 | 80 | score | 1 | 1 | OK | 200 |
| r09 | 90 | noul | 1 | 1 | OK | 200 |
| r10 | 100 | choice | 1 | 1 | ERROR | 422 |
| r11 | 200 | score | 1 | 1 | ERROR | 500 |
| r12 | 500 | noul | 1 | 1 | ERROR | 503 |

`child` is exactly `{"key":"c01","parent_key":"r01","duration_ms":5,"end_offset_ms":1}`. It ends 1 ms before its root ends and carries no request counts, results, confidence or usage. Root index starts at zero: end = base_time − index × 180 seconds, start = end − duration. Names are `synthetic.r01` through `synthetic.r12`, and `synthetic.c01`; successful status_message is empty, failures use `synthetic HTTP <http_status>`; events are `[]`.

Summary has exactly these required fields and values for v1:

```json
{"spans":13,"requests":12,"ok_requests":9,"error_requests":3,"error_rate":0.25,"median_ms":65,"requested_items":15,"requested_questions":18,"completed_answers":15,"batch_requests":2,"recorded_input_tokens":900,"recorded_output_tokens":18,"usage_unknown_requests":3}
```

Counts/tokens are nonnegative integers; rate/median are finite numbers. Token totals mean **recorded** usage, not inferred total usage; `usage_unknown_requests` makes missing usage visible. Root-only summary must match this object; child status/timing/usage never contributes.

### Span identity, attributes and safe results

A run ID is a new lowercase UUID4 hex string (32 characters); explicitly supplied IDs must satisfy the same form. Reject invalid input rather than lossy sanitization. This safe ID is the only variable in run paths and IDs. Base time is chosen once, truncated to milliseconds. For fixture version `v`, run `r`, canonical base time `b`, and root key `k`, trace ID is the first 32 hex characters of SHA256(`v|r|b|trace|k`); span ID is the first 16 of SHA256(`v|r|b|span|k`). Child uses its own span key and the root's trace ID/parent span ID. Reject zero IDs or collisions. New runs are independent; fixed manifest/run/base generates identical records.

All spans require flat attribute `laya.run_id: str` and `metadata: str` containing JSON with exactly `data_source: "synthetic"`, `run_id: str`, `fixture_version: "laya-synthetic-v1"`. Root attributes additionally require `llm.model_name: "synthetic-laya"`, `laya.request_type`, `laya.http_status`, `laya.requested_items`, `laya.requested_questions` (= items × questions_per_item), `laya.completed_answers` (requested_questions on OK, 0 on ERROR), and `laya.batch: bool` (= items > 1).

OK roots require `llm.token_count.prompt: 100`, `llm.token_count.completion: 2`, `laya.result_type` equal to request_type and `laya.result: str` containing a JSON array, ordered by 1-based item then question, of objects with exactly `item: int`, `question: int`, `value: str|number`. Choice value is `"fixture-a"`, score value `0.5`, noul value `0.25`. Successful choice roots carry `laya.confidence: 0.8`, `laya.x_jev_confidence: 0.7`, `laya.answer_confidence: 0.9`; score roots carry only `laya.answer_confidence: 0.6`; noul roots carry only `laya.noul_probability: 0.25`. These are independent synthetic fields, not interchangeable confidence/correctness measures. All probabilities are finite numbers in [0,1]. ERROR roots omit tokens, result_type/result and every confidence/probability field. Missing means unknown/not available; JSON null or zero is not a replacement. Only `parent_id` uses null for roots. No other optional fields exist in v1.

### Expected artifact and receipts v1

Before any POST, host CLI `prepare` writes immutable `expected.json` with exactly `schema_version: 1`, `fixture_version`, `project`, `run_id`, `base_time`, `spans: Span[13]`, `summary: Summary`. Span is the pinned REST shape below, without server-generated `id`; it includes all identities, parents, timestamps, status, attributes and events. Validate the full graph, counts, safe values and summary on loading; an edited summary cannot bless inconsistent spans. `--expected` always means this artifact, **not** stdout from a successful seed. Its complete envelope types are `schema_version: Literal[1]`, `fixture_version: Literal["laya-synthetic-v1"]`, `project: Literal["laya-phoenix-trial"]`, `run_id: str` (UUID4 hex), `base_time: str` (canonical UTC), `spans: list[Span]` (13 records), `summary: Summary`; all keys are required, none nullable. Span has exactly `name: str`, `context: {trace_id: str, span_id: str}`, `span_kind: "LLM"|"CHAIN"`, `parent_id: str|null`, `start_time: str`, `end_time: str`, `status_code: "OK"|"ERROR"`, `status_message: str`, `attributes: dict[str, str|int|float|bool]` restricted to the above canonical paths, `events: []`. Hex IDs are lowercase length 32/16, parents length 16; timestamps/status/graph/attributes must agree with deterministic generation for the v1 manifest. A valid full artifact is this envelope populated from the complete table; the REST example below supplies a complete representative span.

Host state is `${XDG_STATE_HOME:-$HOME/.local/state}/llm-env/phoenix-trial/<compose-project>/runs/<run_id>/expected.json`. Private directories are 0700, files 0600; reject symlink paths, traversal and existing run directories, never overwrite an expectation. Write/flush/fsync the file and fsync new directory entries up through the state root before returning or sending anything. If durable state cannot be established, issue zero POSTs. No production state or secrets belong here.

`prepare` stdout is `{"schema_version":1,"status":"prepared","project":"laya-phoenix-trial","run_id":"<32 lowercase UUID4 hex>","expected_path":"<absolute host path>"}`: literal version/status/project, string run_id/expected_path, no optional/null keys. Host preparation durably saves this receipt as `prepared.json` in the run directory before returning; launching the sender cannot depend on capturing successful stdout. The sender receives the exact expected artifact on stdin (`--expected -`), avoiding any host/state bind mount. Successful seed/verify stdout has exactly `schema_version: Literal[1]`, `status: Literal["verified"]`, `project: Literal["laya-phoenix-trial"]`, `run_id: str` (same UUID4 hex), `summary: Summary` and `verified_span_ids: list[str]` (13 unique sorted lowercase 16-hex IDs); all required, none nullable. Save it as `seed-result.json` only on CLI success and after validation against the prepared artifact. Neither receipt replaces `expected.json`.

Handled CLI errors retain existing `{"error":"<bounded message>"}`/exit 1; usage errors exit 2. Host diagnostics must print the sanitized run ID, expected path and recovery command even when stdout is only an error. Before launching the sender, durably write `attempt.json` with exactly `schema_version: Literal[1]`, `run_id: str` (same UUID4 hex), `expected_path: str` (same absolute host path), `post_may_have_started: Literal[true]`, all required; refuse a second seed of that run. That conservative marker remains after crashes, transport loss, incomplete 202 counts or readback timeout. A new explicit seed allocates a new run and warns about unresolved attempts; no automatic resend, deletion, ID change or fallback is allowed. A recovered verification does not rewrite an expectation or claim a second POST.

Read-only recovery (no server recreation, writes, pull/build or reseeding):

```bash
UV_OFFLINE=1 uv run llmenv.py phoenix-trial verify --base-url "http://127.0.0.1:${LLM_ENV_PHOENIX_PORT:-6006}" --run-id "$run_id" --expected "$expected_path"
```

The CLI validates the expected artifact/run before network I/O, then only GETs. It works for accepted-but-response-lost and accepted-but-readback-timeout runs as well as successful runs. Persistence verification requires this same exact pre-recreation readback before touching the trial server, then repeats it after recreation. Expired retention data is a mismatch, never a reason to reseed or adjust expected timestamps.

### Pinned REST exchange and semantic verification

POST `/v1/projects/laya-phoenix-trial/spans` uses `{"data":[span,...]}`; a full batch has 13 spans. Representative valid r01 (single-span API example, not a complete v1 artifact):

```json
{"data":[{"name":"synthetic.r01","context":{"trace_id":"11111111111111111111111111111111","span_id":"1111111111111111"},"span_kind":"LLM","parent_id":null,"start_time":"2026-10-18T15:59:59.990Z","end_time":"2026-10-18T16:00:00.000Z","status_code":"OK","status_message":"","attributes":{"llm.model_name":"synthetic-laya","llm.token_count.prompt":100,"llm.token_count.completion":2,"laya.run_id":"12345678123442348123123456789abc","metadata":"{\"data_source\":\"synthetic\",\"fixture_version\":\"laya-synthetic-v1\",\"run_id\":\"12345678123442348123123456789abc\"}","laya.request_type":"choice","laya.http_status":200,"laya.requested_items":1,"laya.requested_questions":1,"laya.completed_answers":1,"laya.batch":false,"laya.result_type":"choice","laya.result":"[{\"item\":1,\"question\":1,\"value\":\"fixture-a\"}]","laya.confidence":0.8,"laya.x_jev_confidence":0.7,"laya.answer_confidence":0.9},"events":[]}]}
```

Require HTTP 202 with `{"total_received":13,"total_queued":13}` for the full fixture (single-span example: both 1). No retry of writes. GET `/v1/projects/laya-phoenix-trial/spans?limit=1000&sort=id&order=asc` returns `{"data":[span,...],"next_cursor":null}`; each readback span adds server-generated `id` (e.g. `"U3Bhbjox"`). For more pages, URL-encode only the returned `next_cursor` as `cursor`; do not treat it as a URL. Query the whole trial project and filter locally by decoded metadata/run; also retain expected identities even if their run metadata is corrupted, so corruption is reported rather than hidden.

Pinned POST unflattens attribute paths; GET flattens them and moves `openinference.span.kind` to top-level `span_kind`. Normalize dotted/nested representations into the same leaves, reject collisions, compare metadata/result JSON by parsed content, and compare timezone-equivalent timestamps at millisecond precision (reject sub-millisecond changes). Ignore only upstream `id` and the redundant kind attribute after asserting it equals top-level kind if present. Compare the exact set of `(trace_id, span_id)`, uniqueness, parent links, name, kind, start/end, status/message, events and **all expected attributes and their absence**. Unexpected `laya.*`/usage/confidence/result or other span attributes fail; do not silently drop them. Type-check first; bool is not numeric. All selected spans must match, then compute the root-only summary. Same counts/latencies/tokens with substituted IDs, altered confidence/results, changed parents or missing metadata must fail before and after recreation.

Standalone readback has a default 60-second monotonic deadline; seed's same total budget includes its single POST and only the remaining budget is passed to readback. Each HTTP request gets at most min(5 seconds, remaining budget), each poll starts a fresh pagination walk, at most 20 pages of 1000, repeated cursors fail, and polls wait at most 0.25 seconds within the remaining budget. Missing/subset records and initial project 404 can settle until deadline; malformed data, duplicates, unexpected/altered records and nontransient HTTP errors fail immediately. Retry GET 503/transport timeouts only within that deadline. Disable redirects and proxy inheritance; accept only plain HTTP, no credentials/path/query/fragment, host `phoenix` port 6006 or `127.0.0.1` with validated port 1–65535. No generic arbitrary endpoint support.

Grounding artifacts: `/tmp/phoenix-trial-spans.py:512–609,989–1187,1412–1568` define upstream shape, pagination, flattening and asynchronous insertion; `/tmp/phoenix-trial-app.py:786–808` defines DB-aware readiness; `/tmp/phoenix-trial-Dockerfile` confirms distroless execution; `/tmp/phoenix-trial-config.py`, `-retention.mdx`, `-security.mdx`, `-image.json`, and `-latest-release.json` ground the configuration/pins. `/tmp` evidence is ephemeral: keep source URLs/tag and exact observations in the execution runbook, not an invented permanent receipt.

## Acceptance and evaluation

Automated checks must prove: pinned image, loopback-only publication, expected environment/security settings, usable SQLite permissions under SELinux/rootless Podman, API readiness, successful queueing **and eventual exact semantic readback**, root-only fixture totals, exactly one POST even on ambiguous failure, durable pre-POST recovery state, and exact identity/content persistence after recreating only the trial server using the same image/volume. Test malformed artifacts and aggregate-preserving substitutions/alterations, accepted-but-response-lost and accepted-but-readback-timeout. Bounded timeouts and clear failures are required; do not probe assumptions by restarting production. Pure/API/stubbed tests run by default with no real service; live integration is opt-in, uniquely scoped and serialized (`-n 0`), not the default xdist `make test` invocation.

Produce a capability matrix: **native UI / API or export only / additional work / unsupported**, with evidence for counts/time windows, latency, errors, tokens, metadata, confidence distributions, request/result types and batch accounting. An exportable attribute is not proof of a native chart. Manual browser checks are allowed; no custom dashboard frontend or new observability stack is authorized to fill gaps.

Record actual dashboard/sender CPU/RAM and volume bytes before seed, after seed, and after recreation using container diagnostics and a short-lived read-only inspector where needed. Report measurements with their method, not claimed forecasts or a hard disk quota. The inspector cannot assume a shell exists in Phoenix. Verify retention settings separately; old-data cleanup timing follows the effective schedule and does not need a seven-day wait in normal tests.

A fit assessment may honestly be 'partial' or 'not suitable'. Preserve the trial volume so the user can inspect the UI; no silent permanent deployment, boot enablement or production integration.

## Authorized review workflow and deferred product work

- **Phase B:** only after approval, instrument a client container to call existing Laya; no server restart, but coverage is limited to that client.
- **Phase C:** separate design for all-caller HTTP/prediction instrumentation, requiring an approved Laya image change/restart; preserve offline/GPU/residency/admission behavior. Existing uptime/history is untouched by Phase A. Previously unrecorded history cannot be reconstructed.
- Hardware collectors, Prometheus/Grafana, auth/LAN, permanent main-stack integration and cost/savings comparison remain out of scope.
- The coordinator continues **GPT 6.1 Sol** fix/review rounds until zero unresolved Critical/High, including addressing this round's Medium finding, then obtains a **fresh Claude Opus 5.5 HIGH-effort evaluation**. Review both current documents through installed `ai-kit-spec-review` with code/research grounding; verify actual model/effort support. Unavailability, quota, disagreement or finite-budget exhaustion is a reported blocker, not permission to substitute or downgrade. No global agent/model-setting edits or fabricated reports.
- Mandatory per-finding Laya evidence is recorded independently against frozen round-1 snapshots under `.claude/handoffs/2026-10-18-160735-review-severity-policy.md`. This fixer does not invoke Laya or disturb that worker. Preserve raw findings, original labels, hashes and receipts; advisory impact labels do not waive checklist HIGH findings. Missing mandatory coverage blocks promotion/execution, not these document corrections.
- Implementation of the authorized plans may begin only after the required approval outcome and evidence coverage; no new approval prompt for the already-authorized sequence. This step performs no implementation. Other plans and production OmniRoute replacement are outside this document revision.
