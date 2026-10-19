# Phoenix Laya Dashboard Trial Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Evaluate Phoenix's request/decision dashboard with isolated synthetic data, proving exact persistence and UI fit without touching running Laya.

**Architecture:** A standalone rootless Podman compose project runs pinned Phoenix with SQLite on an external named volume. The host durably prepares an exact expected-run artifact before a one-shot sender submits it through the pinned REST API; read-only verification compares identities and semantic content, not just totals. Bash handles lifecycle/JSON handoff; the existing Python CLI validates data and performs scoped HTTP.

**Tech Stack:** Existing Bash/Podman-compose/uv/jq, Phoenix 20.23.0, SQLite, Python stdlib and existing PyYAML, pytest/shellcheck/ruff/pyright. No tracing SDK or collector.

**Spec:** `docs/superpowers/specs/2026-10-18-phoenix-laya-dashboard-trial-design.md` (read completely, including its versioned contracts).

## Global Constraints

- This step revises documents only: **no implementation now**. Authorized sequence: GPT 6.1 Sol fix/review rounds until zero unresolved Critical/High, then fresh Claude Opus 5.5 HIGH-effort evaluation, then implementation of the approved plans only if approved. No renewed approval prompt for that sequence; edits alone are not approval.
- Scope is **Phase A, synthetic traces**. No Laya inference/instrumentation/restart, production credentials, main compose/config edits, production-network attachment or production OmniRoute replacement. Other plans remain outside this revision.
- Container-first: one persistent Phoenix plus explicitly invoked one-shot sender/inspector. No new host server/SDK dependencies, privileged mode, GPU devices, engine socket or broad host mounts.
- Phoenix pin: `docker.io/arizephoenix/phoenix:20.23.0@sha256:c542e7de1dc1fdd0f938de337d44ebefa7266e91391eed6e7978523d7f6e1f2f`. Sender base: `ghcr.io/astral-sh/uv:0.12.19-python3.13-trixie-slim@sha256:aba5f865793af9275ceaa06d30935dfc6db8b81969c9fb099b0ebca6396b189e`. Resolve platform dynamically, no floating fallback.
- Default compose project `llm-env-phoenix-trial`; tests use `llm-env-phoenix-trial-it-<random>`. Volume `${project}-data`; ownership labels `llm-env.scope=phoenix-trial` and `llm-env.project=${project}` on owned resources. Refuse conflicting/unowned resources. No production compose operations.
- `LLM_ENV_PHOENIX_PORT=6006` default, integer 1–65535, publish **127.0.0.1 only**, fail on conflict. `LLM_ENV_PHOENIX_RETENTION_DAYS=7` default, positive integer. These are trial policy defaults, not measured requirements.
- External named volume at `/mnt/data`; stop/recreate preserve it. **No volume-deletion action**. Retention is scheduled cleanup, not a disk quota; verify effective project/default policy.
- Set the spec's external-resource/analytics/provider/sandbox/MCP/Prometheus disable settings. Verify installed rootless compose `internal: true` and loopback behavior; UI provider settings are not a firewall. Unsupported isolation is a blocker, not permission to weaken it.
- Phoenix is distroless/nonroot: no assumed shell/curl/wget. Use host curl or sender probes and a scoped read-only inspector. Measure UID/GID/permissions before any trial-volume initialization; never disable SELinux.
- Python entrypoints remain `uv run llmenv.py <subcommand>` on host and in sender; existing test invocation is `uv run pytest ...`. Warm inline-script dependencies during build and prove `UV_OFFLINE=1` runtime. Do not change root dependencies.
- Manifest/expected/receipt **schema_version 1**, fixture **laya-synthetic-v1**, Phoenix API project **laya-phoenix-trial**, and all required fields/values/absence rules come from the spec's versioned contract. `--expected` is the immutable pre-POST artifact, never an aggregate or successful-seed stdout.
- Preserve dirty work, no staging/commits in this assignment. Later task checkpoints mean review intended paths/hunks, not `git add -A`; commit only if separately permitted. Shell edits require `make validate`; Python edits require `make validate && make test` with live integration unset.
- Default tests must be service-free. `tools/test.sh` uses xdist `-n auto`; **never use `LLM_ENV_TEST_PHOENIX_TRIAL=1 make test`**. Live integration is explicitly selected and serialized with `-n 0`, unique resources and an interprocess suite lock.

## Review Focus

1. Same totals can hide substituted spans or changed confidence/results/parents: exact identity/content comparison before and after recreation (Tasks 2–3, 5).
2. Asynchronous acceptance plus lost response/readback timeout must leave durable run/expected state, one POST and a read-only recovery path (Tasks 2–4).
3. Child spans, batches and missing usage/confidence must not become extra requests, fabricated zeros or savings (Tasks 2, 6).
4. Busy ports, unowned resources, concurrent tests and rootless/SELinux failures must stop locally without killing owners, deleting volumes or restarting production (Tasks 1, 4–5).
5. Pinned REST normalization, distroless probes, sender offline resolution and network controls must be demonstrated rather than inferred from a working UI (Tasks 1–3, 5).

## Grounding and file map

Repository conventions: `llmenv.py` has `emit`, `fail`, `finite_positive_float`, `build_parser`, `main` (exit 0/1/2; handled error only `{"error":...}`); `tools/lib.sh:245` provides the uv CLI wrapper. `Makefile` delegates via `tools/run-target.sh`; follow `tests/test_cli.py::run` and the PATH-stub style of `tests/test_shell.py::run_lifecycle_script`, but do not reuse helpers that prepare production services. `pyproject.toml` already depends on PyYAML and `uv.lock` exists; no `llmenv.py.lock` exists. Inline PEP 723 script resolution must therefore be warmed separately from project `uv sync`.

Read existing `docs/superpowers/research/2026-10-18-laya-local-observability.md` for scope, not its superseded deferred-review statement. Pinned evidence is `/tmp/phoenix-trial-spans.py:512–609,989–1187,1412–1568`, `-app.py:786–808`, `-Dockerfile`, `-config.py`, `-retention.mdx`, `-security.mdx`, `-image.json`, `-latest-release.json`. Source URLs/tag are in the spec; do not depend on `/tmp` surviving implementation or claim this source inspection proves runtime isolation/persistence.

| File | Responsibility |
| --- | --- |
| `docker/phoenix-trial/compose.yml` (new) | Standalone server, opt-in sender, internal network/external volume |
| `docker/phoenix-trial/Dockerfile.sender` (new) | Curated CLI image; warmed uv, offline execution |
| `docker/phoenix-trial/fixtures/laya-synthetic.json` (new) | Complete v1 manifest/table/summary from spec |
| `docker/phoenix-trial/README.md` (new) | Reproduction/recovery/stop/pins/data-boundary runbook |
| `pylib/phoenix_trial.py` (new) | Typed contracts, fixture generation, durable artifacts, comparison, scoped REST |
| `llmenv.py` (modify dispatch/parser/error tuple only) | `phoenix-trial` CLI adapter; no production routine calls |
| `scripts/phoenix-trial.sh` (new) | Explicit standalone lifecycle, curated build, state/locks/inspection |
| `Makefile`, `scripts/help.sh` (modify) | Thin opt-in trial targets, no main-lifecycle wiring |
| `tests/test_phoenix_trial.py` (new) | Pure/configuration/API unit tests with fake transport/clock |
| `tests/test_cli.py`, `tests/test_shell.py` (modify) | CLI contracts, stubbed lifecycle/order/safety tests |
| `tests/test_phoenix_trial_integration.py` (new) | Opt-in serialized rootless integration |
| `docs/superpowers/research/2026-10-18-phoenix-trial-results.md` (new at execution) | Actual evidence/capability matrix, never prefilled success |

Tasks are sequential because each consumes the previous task's interface. Six independently testable deliverables; no parallel shared-state lifecycle work.

## Task 1: Validate the isolated server configuration

**Files:** Create `docker/phoenix-trial/compose.yml`, `docker/phoenix-trial/README.md`, `tests/test_phoenix_trial.py`.

**Interfaces:** Compose consumes validated `PHOENIX_TRIAL_PROJECT`, `PHOENIX_TRIAL_VOLUME`, `PHOENIX_TRIAL_PORT`, `PHOENIX_TRIAL_RETENTION_DAYS`. Produces service `phoenix`, network `trial`, external volume `data`; no `container_name`, automatic seed or in-image healthcheck. Task 3 adds sender.

- [ ] **Step 1: Write configuration tests.** Load YAML with `yaml.safe_load`; add `test_compose_has_only_loopback_http_port` and `test_trial_has_no_production_access`. Representative assertions (set `compose` from the new file):

```python
service = compose["services"]["phoenix"]
assert service["ports"] == ["127.0.0.1:${PHOENIX_TRIAL_PORT:?}:6006"]
assert service["volumes"] == ["data:/mnt/data"]
assert compose["networks"]["trial"]["internal"] is True
assert compose["volumes"]["data"]["external"] is True
assert set(compose["services"]) == {"phoenix"}
assert "healthcheck" not in service
```

Also assert exact pin/environment/labels and absence of `privileged`, `devices`, `network_mode`, bind/socket/production mounts, credential passthrough, restart-always or production networks.

- [ ] **Step 2: Run the red tests.** `uv run pytest tests/test_phoenix_trial.py -n 0 -v` → FAIL for missing compose (not an unrelated environment failure).
- [ ] **Step 3: Create the server compose below and README boundary/pin/source notes.** This is the complete initial configuration; interpolate only validated/exported trial variables. `PHOENIX_HOST=0.0.0.0` is inside the container, not host publication.

```yaml
services:
  phoenix:
    image: docker.io/arizephoenix/phoenix:20.23.0@sha256:c542e7de1dc1fdd0f938de337d44ebefa7266e91391eed6e7978523d7f6e1f2f
    ports: ["127.0.0.1:${PHOENIX_TRIAL_PORT:?}:6006"]
    environment:
      PHOENIX_HOST: "0.0.0.0"
      PHOENIX_PORT: "6006"
      PHOENIX_WORKING_DIR: /mnt/data
      PHOENIX_DEFAULT_RETENTION_POLICY_DAYS: "${PHOENIX_TRIAL_RETENTION_DAYS:?}"
      PHOENIX_ALLOW_EXTERNAL_RESOURCES: "false"
      PHOENIX_TELEMETRY_ENABLED: "false"
      PHOENIX_ALLOWED_PROVIDERS: NONE
      PHOENIX_ALLOWED_SANDBOX_PROVIDERS: NONE
      PHOENIX_ENABLE_MCP_SERVER: "false"
      PHOENIX_ENABLE_PROMETHEUS: "false"
      PHOENIX_ENABLE_AUTH: "false"
    volumes: ["data:/mnt/data"]
    networks: [trial]
    labels:
      llm-env.scope: phoenix-trial
      llm-env.project: "${PHOENIX_TRIAL_PROJECT:?}"
networks:
  trial:
    internal: true
    labels:
      llm-env.scope: phoenix-trial
      llm-env.project: "${PHOENIX_TRIAL_PROJECT:?}"
volumes:
  data:
    external: true
    name: "${PHOENIX_TRIAL_VOLUME:?}"
```

- [ ] **Step 4: Run the green tests.** `uv run pytest tests/test_phoenix_trial.py -n 0 -v` → PASS. Validate provider parsing only, no container creation: `PHOENIX_TRIAL_PROJECT=llm-env-phoenix-trial PHOENIX_TRIAL_VOLUME=llm-env-phoenix-trial-data PHOENIX_TRIAL_PORT=6006 PHOENIX_TRIAL_RETENTION_DAYS=7 podman compose --env-file /dev/null -f docker/phoenix-trial/compose.yml -p llm-env-phoenix-trial config` → resolved loopback/internal/external configuration. Block on unsupported semantics; do not adopt another network.
- [ ] **Step 5: Review the task diff.** Only Task 1 paths; do not stage/commit. Server configuration is independently testable without the later sender.

## Task 2: Implement fixture contracts and durable pre-POST preparation

**Files:** Create `docker/phoenix-trial/fixtures/laya-synthetic.json`, `pylib/phoenix_trial.py`; modify `llmenv.py`, `tests/test_phoenix_trial.py`, `tests/test_cli.py`.

**Interfaces:** Define `Manifest`, `Span`, `Summary`, `ExpectedRun`, `PreparedReceipt`, `VerifiedReceipt`, `AttemptReceipt` as TypedDicts matching every required/optional field in the spec; use Literal enums/version values and runtime validators, not casts of unchecked JSON. JSON input boundary is `object`. Add `PhoenixTrialError(ValueError)`.

- `load_manifest(path: Path) -> Manifest`
- `build_fixture_spans(manifest: Manifest, *, run_id: str, base_time: datetime) -> list[Span]`
- `summarize_spans(spans: list[Span], *, run_id: str) -> Summary`
- `make_expected(manifest: Manifest, *, run_id: str, base_time: datetime) -> ExpectedRun`
- `validate_expected(value: object) -> ExpectedRun`
- `write_prepared_run(state_dir: Path, expected: ExpectedRun) -> PreparedReceipt` (state_dir is the compose-project state root; creates `runs/<run_id>` exclusively).
- `mark_attempt(expected_path: Path) -> AttemptReceipt` (validates local artifact, durably/exclusively writes sibling attempt marker; existing marker fails).
- CLI `fixture-check --manifest PATH`; `prepare --manifest PATH --state-dir DIR [--run-id ID]`; `mark-attempt --expected PATH`. These are local-only; no HTTP/import-time network effects. Prepare generates UUID4 hex/base UTC once, calls make/write, emits PreparedReceipt; fixture-check emits `{"schema_version":1,"status":"valid","fixture_version":"laya-synthetic-v1","summary":Summary}`.

- [ ] **Step 1: Write fixture/artifact tests.** In `tests/test_phoenix_trial.py`, define pytest `manifest` fixture by loading the checked-in file; `expected` fixture calls `make_expected` with `run_id="12345678123442348123123456789abc"`, UTC base `2026-10-18T16:00:00.000Z`. Add `test_fixture_and_root_summary`:

```python
spans = expected["spans"]
summary = summarize_spans(spans, run_id=expected["run_id"])
assert summary == manifest["expected_summary"] == expected["summary"]
assert (summary["spans"], summary["requests"], summary["error_rate"]) == (13, 12, 0.25)
assert (summary["median_ms"], summary["recorded_input_tokens"], summary["recorded_output_tokens"]) == (65, 900, 18)
assert (summary["requested_items"], summary["requested_questions"], summary["completed_answers"]) == (15, 18, 15)
assert (summary["batch_requests"], summary["usage_unknown_requests"]) == (2, 3)
assert spans == build_fixture_spans(manifest, run_id=expected["run_id"], base_time=datetime(2026, 10, 18, 16, tzinfo=timezone.utc))
```

Pin the full 12-row table, child trace/parent/5 ms containment, 180-second spacing, kind/status/result/confidence paths, missing-vs-zero semantics, UUID4/nonzero stable unique IDs and independence of another run. Summary must reject malformed selected records, not fill missing counts with zero.

- [ ] **Step 2: Write malformed/durability/CLI tests.** Parameterize unsupported/string/bool version, extra/duplicate/missing keys, array top level, bad UUID/timezone, null/NaN confidence, bool/negative counts, invalid result shape, zero/duplicate IDs, wrong child graph, summary mismatch and unexpected ERROR usage. `validate_expected` must raise PhoenixTrialError. In `test_prepare_is_durable_before_any_send`, spy `os.fsync`/file operations: assert expected file and directory entries were synced before return, 0700/0600, receipt path/run agree, second write/symlink/unsafe path fails and injected fsync failure prevents dispatch. `test_mark_attempt_is_exclusive` asserts durable marker matches artifact and second call fails. CLI subprocess tests use existing `run(...)` helper to assert valid JSON/exit 0, handled malformed input exit 1 with only `error`, missing arguments exit 2. Separately invoke `llmenv.main([...])` in-process with HTTP entrypoints monkeypatched to raise if prepare/fixture-check/mark-attempt calls them; subprocesses do not inherit monkeypatches.
- [ ] **Step 3: Run red tests.** `uv run pytest tests/test_phoenix_trial.py -n 0 -v && uv run pytest tests/test_cli.py -k phoenix -n 0 -v` → missing module/parser/manifest FAIL. Name all added CLI tests with `phoenix` for selection.
- [ ] **Step 4: Implement the interfaces and exact v1 manifest.** Use the spec's complete table/summary, SHA256 ID derivation, millisecond UTC algorithm and flat safe attributes. JSON decoding rejects duplicate keys/nonfinite constants; file validation rejects symlinks and preexisting run dirs, and uses exclusive creation + flush/fsync (files and directory ancestry). Save immutable expected and prepared receipt before return; do not collapse unknown values into zero.
- [ ] **Step 5: Wire CLI to those functions.** Add `cmd_phoenix_trial(args: argparse.Namespace) -> int`, import only the new module/error, add nested argparse actions under `phoenix-trial` in `build_parser`, use `Path` arguments and existing `emit`/`fail`, and add PhoenixTrialError to main's handled tuple. Do not call config loading/provisioning in this branch. `mark-attempt` emits AttemptReceipt; errors retain the existing bounded error-object convention.
- [ ] **Step 6: Run green tests and required regression.** Repeat Step 3 → PASS; run `make validate && env -u LLM_ENV_TEST_PHOENIX_TRIAL make test` → PASS. Review only Task 2 paths/hunks; no staging/commits.

## Task 3: Implement exact REST verification and offline sender

**Files:** Create `docker/phoenix-trial/Dockerfile.sender`; modify `pylib/phoenix_trial.py`, `llmenv.py`, `tests/test_phoenix_trial.py`, `tests/test_cli.py`, `docker/phoenix-trial/compose.yml`, `docker/phoenix-trial/README.md`.

**Interfaces:** Consumes Task 2 typed artifacts. Produces:
- `canonical_span(value: object) -> Span` (normalizes pinned GET shape as the spec requires; omits only server `id`/validated redundant kind).
- `compare_run(spans: list[Span], *, expected: ExpectedRun) -> Summary` (exact selected identity/content equality, then root summary; raises PhoenixTrialError on mismatch).
- `seed_spans(base_url: str, expected: ExpectedRun, *, timeout_seconds: float = 60.0) -> VerifiedReceipt` (exactly one POST then read-only verification).
- `verify_run(base_url: str, *, run_id: str, expected: ExpectedRun, timeout_seconds: float = 60.0) -> VerifiedReceipt` (GET-only; no artifact writes).
- `volume_bytes(path: Path) -> int` (sum regular-file `st_size`, reject symlinks/traversal; documented logical bytes, not allocated disk/quota).
- CLI `seed --base-url URL --expected PATH_OR_DASH [--timeout-seconds N]`; `verify --base-url URL --run-id ID --expected PATH_OR_DASH [--timeout-seconds N]`; `volume-bytes --path PATH` → `{"bytes":nonnegative int}`. `-` means artifact JSON on stdin, never a manifest. Validate before I/O; finite positive timeout via existing parser helper. No direct `seed --manifest` path that bypasses durable host preparation.

- [ ] **Step 1: Write exact-comparison and normalization tests.** `test_compare_rejects_aggregate_preserving_change` uses deepcopy of `expected["spans"]` and parameterizes substituted root trace/span IDs (update child links if replacing r01), a wrong but well-formed child parent, choice confidence 0.8→0.1 and result `fixture-a`→`fixture-b`. For each otherwise unchanged, structurally valid set:

```python
assert summarize_spans(altered, run_id=expected["run_id"]) == expected["summary"]
with pytest.raises(PhoenixTrialError):
    compare_run(altered, expected=expected)
```

Summary checks only record structure/countable values, not equality with v1 safe content or expected IDs; that stricter equality belongs to compare_run. Separately test missing metadata and unexpected ERROR usage as immediate schema errors without calling the aggregate-preserving assertion. Add `test_get_normalization_preserves_semantics`: server global `id`, equivalent UTC offset and nested/flat attributes normalize to the expected span; changed sub-millisecond time, duplicate flattened paths, absent required field, unexpected attribute and bool-as-float fail. Reordered metadata JSON keys are equal; changed safe contents are not. Test duplicate IDs/excess same-run records, unexpected same-run traces, changed run metadata on an expected identity and filtering a genuinely unrelated run.

- [ ] **Step 2: Write transport/failure tests.** Add an in-process fake opener with recorded methods/bodies, scripted 202/GET pages and a fake monotonic clock/sleep (patch module references, no real socket needed). `test_seed_accepted_response_lost_has_one_post` queues the exact expected records then raises a transport timeout on POST response; assert one POST, error, unchanged precreated expected/attempt files, no retry. Then `verify_run` against that stored set succeeds and adds GETs only. `test_seed_readback_timeout_keeps_recovery` returns exact 202 but empty GET pages until deadline; assert one POST, error, byte-identical recovery artifact, then later read-only verify succeeds. `test_failed_prepare_sends_nothing` asserts zero POST if the durable prepare/mark fails in host orchestration (stub order is exercised in Task 4).

Also test incomplete 202 counts, invalid body/status, eventual subset→full, initial project 404→full, GET 503/timeouts within deadline, page walk/URL-encoded cursor, repeated cursor/20-page cap, duplicates, malformed JSON/schema, timeout budget including pagination, redirect/proxy denial, nontrial URL/run/timeout rejection and filesystem symlinks. Add `test_verify_rejects_aggregate_preserving_persisted_change`, parameterized over before/after-recreation readback phases: fake GET returns each altered set from Step 1, assert the whole aggregate summary still equals expected, `verify_run` raises PhoenixTrialError, the expected artifact bytes are unchanged and only GETs were issued. No test contacts Laya/provider endpoints.

- [ ] **Step 3: Run red tests.** `uv run pytest tests/test_phoenix_trial.py -n 0 -v && uv run pytest tests/test_cli.py -k phoenix -n 0 -v` → missing REST/comparison interfaces FAIL.
- [ ] **Step 4: Implement the interfaces and HTTP algorithm.** Use `urllib.request.build_opener` with `ProxyHandler({})` and an `HTTPRedirectHandler` subclass whose `redirect_request` raises PhoenixTrialError, stdlib JSON, pinned POST/GET payload examples from the spec (do not implement OTLP). Reject URL credentials/path/query/fragment, allow `http://phoenix:6006` or `http://127.0.0.1:<1..65535>` only. Verify explicit run matches artifact. POST once, require 202 and integer received/queued exactly 13, never retry even when acceptance is uncertain. Follow the spec's 60-second monotonic budget (seed includes its POST and passes only remaining time to verify), min(5, remaining) per request, 0.25-second polling, fresh capped page walks and immediate mismatch failures. Match expected IDs even when metadata changed; match other records by run; reject extra/duplicate selected records. Compare full canonical records before summary; ignore only pinned server-generated `id` and validated redundant kind. Errors are bounded, synthetic and carry no raw payload/secret dumps.
- [ ] **Step 5: Add concrete sender configuration and tests.** Update Task 1's service-set assertion to include `sender`, while asserting it is profile-gated/no-port/no-volume and `up phoenix` selects only server. Add this service (same label values as server):

```yaml
  sender:
    image: "${PHOENIX_TRIAL_SENDER_IMAGE:?}"
    profiles: [tools]
    environment:
      UV_OFFLINE: "1"
      UV_PYTHON_DOWNLOADS: never
    networks: [trial]
    labels:
      llm-env.scope: phoenix-trial
      llm-env.project: "${PHOENIX_TRIAL_PROJECT:?}"
```

Create `Dockerfile.sender` with these contents; the explicit module list covers current eager imports from `llmenv.py`, not arbitrary repo data:

```dockerfile
FROM ghcr.io/astral-sh/uv:0.12.19-python3.13-trixie-slim@sha256:aba5f865793af9275ceaa06d30935dfc6db8b81969c9fb099b0ebca6396b189e
WORKDIR /app
ENV UV_CACHE_DIR=/opt/uv-cache UV_PYTHON_DOWNLOADS=never
COPY pyproject.toml uv.lock llmenv.py ./
COPY pylib/__init__.py pylib/agent_runner.py pylib/budget.py pylib/compose.py pylib/config.py pylib/detect.py pylib/dotenv.py pylib/gguf.py pylib/omniroute.py pylib/omniroute_combos.py pylib/presets.py pylib/remote_setup.py pylib/resources.py pylib/transcript.py pylib/phoenix_trial.py ./pylib/
COPY docker/phoenix-trial/fixtures/laya-synthetic.json ./docker/phoenix-trial/fixtures/laya-synthetic.json
RUN uv sync --frozen --no-dev --no-install-project \
 && uv run llmenv.py phoenix-trial fixture-check --manifest docker/phoenix-trial/fixtures/laya-synthetic.json \
 && UV_OFFLINE=1 uv run llmenv.py phoenix-trial fixture-check --manifest docker/phoenix-trial/fixtures/laya-synthetic.json
ENV UV_OFFLINE=1
ENTRYPOINT ["uv", "run", "llmenv.py", "phoenix-trial"]
CMD ["fixture-check", "--manifest", "docker/phoenix-trial/fixtures/laya-synthetic.json"]
```

`uv sync` alone is insufficient for this PEP 723 entrypoint: keep the first inline `uv run` and its cache in the final image. Build may download existing dependencies; runtime must not. No root dependency edits, tracing SDK, `COPY .`, host config or model cache. Configuration tests parse all COPY sources/entrypoint/ENV and deny broad copies/mounts.

- [ ] **Step 6: Run green unit tests.** Repeat Step 3 → PASS, including new sender configuration assertions.
- [ ] **Step 7: Build the curated sender.** Run this exact curated-context recipe from repo root (no directory/glob copies):

```bash
sender_files=(llmenv.py pyproject.toml uv.lock pylib/__init__.py
  pylib/agent_runner.py pylib/budget.py pylib/compose.py pylib/config.py
  pylib/detect.py pylib/dotenv.py pylib/gguf.py pylib/omniroute.py
  pylib/omniroute_combos.py pylib/presets.py pylib/remote_setup.py
  pylib/resources.py pylib/transcript.py pylib/phoenix_trial.py
  docker/phoenix-trial/fixtures/laya-synthetic.json)
context="$(mktemp -d)"
trap 'rm -rf -- "$context"' EXIT
cp --parents -- "${sender_files[@]}" "$context"
podman build -f "$PWD/docker/phoenix-trial/Dockerfile.sender" -t localhost/llm-env-phoenix-trial-sender:test "$context"
```

Build must complete; only the owned temporary context is removed. This uses current working-tree files, not an archive that omits untracked implementation. Task 4 automates the same recipe with a labeled per-project tag.
- [ ] **Step 8: Prove offline runtime.** `podman run --rm --network none localhost/llm-env-phoenix-trial-sender:test fixture-check --manifest docker/phoenix-trial/fixtures/laya-synthetic.json` → exit 0, v1 valid summary, no downloads. Save actual build/offline output, not a forecast.
- [ ] **Step 9: Run regression/review checkpoint.** `make validate && env -u LLM_ENV_TEST_PHOENIX_TRIAL make test` → PASS. Review Task 3 hunks only; no staging/commits.

## Task 4: Add explicit standalone lifecycle and recovery handoff

**Files:** Create `scripts/phoenix-trial.sh`; modify `Makefile`, `scripts/help.sh`, `tests/test_shell.py`, `docker/phoenix-trial/README.md`. No production lifecycle/config paths change.

**Interfaces:** Script actions `up|seed|status|verify-persistence|stop` (default status); Make targets `phoenix-trial-up`, `phoenix-trial-seed`, `phoenix-trial-status`, `phoenix-trial-verify-persistence`, `phoenix-trial-stop`. Env overrides `LLM_ENV_PHOENIX_TRIAL_PROJECT`, `LLM_ENV_PHOENIX_PORT`, `LLM_ENV_PHOENIX_RETENTION_DAYS`. Internal helpers in script: `trial_compose()` fixes absolute `-f`, validated `-p`, `--env-file /dev/null`; `require_owned_resources()` checks both labels and expected compose ownership; `build_sender()` uses Task 3's curated context; `wait_ready()` polls host `/readyz` with a 60-second monotonic-equivalent bounded deadline (host curl `--noproxy '*' --connect-timeout 2 --max-time 5`, no redirects). Use existing logging/require_cmd/CLI wrapper only, not production start/stop helpers.

- [ ] **Step 1: Write stubbed safety/order tests.** Add `run_phoenix_trial(tmp_path: Path, action: str, *, failure: str = "") -> tuple[subprocess.CompletedProcess[str], Path]` in `tests/test_shell.py`: private HOME/XDG state/PATH, canned podman/compose/curl/uv/ss, argument log path as second return; no real engine/HTTP fallback. Follow existing PATH-stub patterns but make unknown commands fail. Add `test_phoenix_trial_seed_prepares_before_post`:

```python
result, calls_path = run_phoenix_trial(tmp_path, "seed", failure="response-lost")
calls = calls_path.read_text()
assert result.returncode != 0
assert calls.index("phoenix-trial prepare") < calls.index("phoenix-trial mark-attempt")
assert calls.index("phoenix-trial mark-attempt") < calls.index("run --rm -T sender seed")
assert calls.count("run --rm -T sender seed") == 1
assert "phoenix-trial verify" in result.stderr  # printed recovery command
assert "down -v" not in calls and "volume rm" not in calls
```

Also assert expected/prepared/attempt files survive unchanged with run ID/path in diagnostics for response-lost and readback-timeout; failed fsync/prepare/mark means no sender call; successful receipt is validated and atomically saved only on exit 0. Test bad action/port/retention/project, busy port, unowned volume/container/network/image, failed pull/build/profile support/readiness and lock contention; no production compose, kill/systemctl/volume deletion/host security/config regeneration. Status must issue no builds/pulls/start/recreation.

- [ ] **Step 2: Run red tests.** `uv run pytest tests/test_shell.py -k phoenix_trial -n 0 -v` → missing script/targets FAIL.
- [ ] **Step 3: Implement scoped setup/up/status/stop and Make/help dispatch.** Validate project `^llm-env-phoenix-trial(-it-[a-z0-9]{8,32})?$` (POSIX ERE for Bash `[[ =~ ]]`, not PCRE); use per-project tag `localhost/${project}-sender:trial`, volume `${project}-data`, private project state directory and exclusive `flock` for mutations. Reject state symlinks. Export only validated trial vars; clear inherited compose selectors/profiles and do not read repo `.env` (explicit `/dev/null`). `up` checks actual port listeners with `ss`, validates ownership before mutation, pulls only pinned images, labels the curated sender build, creates labeled external volume if absent, and runs `up -d phoenix`; wait DB-ready. Record measured image user/workdir and prove SQLite writable through actual ingestion later. If permission initialization is required, use only a rootless network-disabled helper on the verified trial volume with measured UID/GID, never host chmod/chown or SELinux changes. `stop` runs only scoped `down` without `-v`; preserve volume/images/state. `status` prints scoped state/resource names without mutation. Each Make recipe uses the existing pattern, e.g. `@bash tools/run-target.sh phoenix-trial-up -- bash scripts/phoenix-trial.sh up`; add all five to `.PHONY`/help, no main-start/clean dependencies.
- [ ] **Step 4: Implement the pre-POST seed handoff.** Under the lock: require existing ready owned server/sender image; host `prepare --state-dir "$state_dir" --manifest "$manifest"` durably allocates expected + prepared receipt; validate stdout with jq and derive the safe path only from validated ID/state root. Host `mark-attempt --expected "$expected_path"` persists the exclusive marker. Then invoke exactly `trial_compose --profile tools run --rm -T sender seed --base-url http://phoenix:6006 --expected - < "$expected_path"`, redirecting only stdout to a private temporary result file and leaving diagnostics on stderr. No pipeline that swallows producer errors. On nonzero status retain prepared/expected/attempt, remove only the temporary result and print run/path plus the spec's host read-only recovery command. No shell retry. On success validate receipt run/project/schema/summary/sorted IDs against expected using jq, then atomically rename to `seed-result.json` (the immutable expected/prepared/attempt files are already fsynced; recovery never depends on this success receipt). Warn about older unresolved attempts; a new explicit seed uses a fresh run, never overwrites/reuses one.
- [ ] **Step 5: Implement exact persistence verification.** Choose the most recent prepared run (including uncertain attempts), validate it and run read-only host `verify` first. Failure leaves server untouched; no requirement for successful seed stdout. Announce recreation; inspect and record trial container ID/StartedAt, exact image ID/digest and volume name. Recreate only `phoenix` with `up -d --no-deps --force-recreate phoenix` against the same locally inspected image (no pull/build/seed); assert new container ID, same image ID/volume, await ready and re-run sender `verify --run-id "$run_id" --expected -` with stdin artifact. Compare exact receipt, not aggregates alone. Reject image drift; retain diagnostic server/data on failure. One-shot inspector uses the owned sender image on `--network none` with only `${volume}:/inspect:ro`, command `volume-bytes --path /inspect`; normal sender has no volume mount.
- [ ] **Step 6: Run green/regression tests and review.** `uv run pytest tests/test_shell.py tests/test_cli.py -k 'phoenix_trial or phoenix' -n 0 -v` → PASS, including no-production assertions and a test that persistence comparison precedes recreation and never invokes seed. `make validate && env -u LLM_ENV_TEST_PHOENIX_TRIAL make test` → PASS. Review Task 4 paths/hunks only; no staging/commits.

## Task 5: Prove serialized rootless persistence and non-interference

**Files:** Create `tests/test_phoenix_trial_integration.py`, `docs/superpowers/research/2026-10-18-phoenix-trial-results.md`; modify `docker/phoenix-trial/README.md`.

**Interfaces:** Default skip before **any** engine/port probes unless `LLM_ENV_TEST_PHOENIX_TRIAL=1`. Opt-in fixture `trial_environment(tmp_path: Path) -> Iterator[dict[str, str]]` holds an exclusive lock on `${XDG_STATE_HOME:-$HOME/.local/state}/llm-env/phoenix-trial/integration.lock` resolved from the operator environment **before** giving subprocesses private HOME/state (one stable lock across test projects), rejects any `PYTEST_XDIST_WORKER` (fail with the exact `-n 0` command), chooses unique allowed `-it-` project and measured free loopback port, private state and no provider/model credentials. Never targets default/production project. The test invokes only Task 4's public actions; helper reads JSON receipts with stdlib json.

- [ ] **Step 1: Write integration assertions and default-skip test.** `test_phoenix_trial_exact_persistence` records read-only existing container IDs/StartedAt and file hashes of production config/generated compose without parsing/logging their contents; up → ready → before-seed resource sample → seed → after-seed sample/verify → same-image server recreation → after-recreation sample/exact same-run verify → stop → external volume still exists. Before/after receipt run/IDs/summary must agree with the immutable expected artifact. Assert only trial server ID changes. Add `test_phoenix_trial_rejects_busy_port_and_unowned_volume` using a disposable occupied loopback socket and uniquely named intentionally mislabelled trial fixture volume; both must fail before adoption/mutation. Default test `test_phoenix_trial_integration_is_opt_in` asserts collection does not invoke podman/curl when flag absent.
- [ ] **Step 2: Run default-skip/red checks.** `env -u LLM_ENV_TEST_PHOENIX_TRIAL uv run pytest tests/test_phoenix_trial_integration.py -n 0 -v` → all live cases SKIP, opt-in guard PASS. Once disposable resources are authorized by the review gate, `LLM_ENV_TEST_PHOENIX_TRIAL=1 uv run pytest tests/test_phoenix_trial_integration.py -n 0 -v` → capture any genuine failing assertions before fixing the harness; do not manufacture a failure or run the whole suite with the opt-in flag.
- [ ] **Step 3: Complete runtime isolation/evidence assertions.** Inspect exact image/labels/ports/mounts/networks/environment (allowlist only; never dump all container env). Assert 127.0.0.1-only 6006 publication, Internal=true, only trial membership, no GPU/socket/host mounts/credentials. Confirm host UI/readiness and sender→Phoenix work on the installed rootless provider. Inspect sender IPv4/IPv6 route tables via its shell (not Phoenix); require no external default route and record network inspection as isolation evidence, without contacting external services or inference endpoints. If actual networking differs, block/report rather than change host routing/firewall. Prove `--network none` offline fixture check separately. Record SQLite permission/SELinux behavior through successful seed + exact recreation readback. Assert failed-test resources remain named/available for diagnosis; successful stop also preserves volumes. There is no test cleanup that deletes any volume, even the mislabelled fixture volume.
- [ ] **Step 4: Run live green checks and record actual output.** `LLM_ENV_TEST_PHOENIX_TRIAL=1 uv run pytest tests/test_phoenix_trial_integration.py -n 0 -v` → PASS with unique names, same image/volume and exact semantic equality. Record actual commands, versions, IDs, sanitized receipts, resource measurements/methods and any concurrent nontrial change separately; do not claim causation for unrelated changes. No production logs/databases/inference.
- [ ] **Step 5: Run normal regression/review.** `make validate && env -u LLM_ENV_TEST_PHOENIX_TRIAL make test` → PASS with live tests skipped. Review Task 5 paths/evidence only, no staging/commits. An isolation/permissions/queue/persistence failure blocks acceptance even if UI loads.

## Task 6: Evaluate UI fit and report measured limitations

**Files:** Modify `docker/phoenix-trial/README.md`, `docs/superpowers/research/2026-10-18-phoenix-trial-results.md`. Do not edit the existing research document/other plan as part of this task; link to that research from the new runbook/results instead.

**Interfaces:** Capability matrix columns: requirement, demonstrated native UI, API/export capability, gap, evidence; classify **native UI / API or export only / additional work / unsupported**. Inputs are the exact v1 artifact/verified receipts and observed browser/runtime evidence, never production history.

- [ ] **Step 1: Write the evidence checklist before browsing.** Rows: request/time counts (12 roots vs 13 spans), latency/65 ms median, 9 OK/3 ERROR/25% request error rate, 900/18 recorded tokens with 3 unknown usage requests, metadata, request/result types, distinct confidence/probability distributions, batch calls/items/questions/completed answers (2/15/18/15), CPU/RAM and volume bytes, retention. Mark each untested initially; attach run ID/filter/window and evidence to every later pass/partial/fail. Assert no child contributes request/tokens, no absent confidence is zero and no attribute visibility alone counts as a native chart.
- [ ] **Step 2: Perform browser checks against the trial only.** Record time/root filters and any UI denominator/percentile differences, safe synthetic trace/result visibility and actual native grouping/chart support. An absent confidence histogram may be API/export-only; report honestly rather than add a custom frontend/Grafana/collector. Keep all evidence synthetic.
- [ ] **Step 3: Record resource/retention evidence.** Measure actual dashboard/sender CPU/RAM (`podman stats --no-stream` scoped to owned container IDs) and logical volume bytes via Task 4 inspector before seed, after seed and after recreation. Reuse Task 5's timestamped samples or record them during a fresh explicit trial sequence; never pretend a post-seed sample was taken before seed. Include method/time/volume identity, not forecasts. Inspect effective seven-day default plus trial-project overrides and actual cleanup schedule; retention is not a disk quota and does not require waiting seven days in tests. Fixture timing/tokens are not historical savings or confidence correctness.
- [ ] **Step 4: Write fit/recovery/stop assessment.** State pass/partial/fail and untested gaps; include exact reproduction and read-only uncertain-run recovery commands, same image/volume persistence evidence and `make phoenix-trial-stop` retaining data. No unrecorded history recovery or Laya uptime reset. Leave trial available only if wanted, never auto-enable boot or permanent integration. Optional Phase B/C remains separately scoped/approved; no real inference, production OmniRoute replacement, auth/LAN or main-stack integration here.
- [ ] **Step 5: Validate documentation and final regressions.** `git diff --check -- docker/phoenix-trial llmenv.py pylib/phoenix_trial.py scripts/phoenix-trial.sh Makefile scripts/help.sh tests docs/superpowers/research/2026-10-18-phoenix-trial-results.md` plus whitespace checks on newly untracked files → no whitespace errors; `make validate && env -u LLM_ENV_TEST_PHOENIX_TRIAL make test` → PASS. Review intended hunks only; no staging/commits without separate permission.

## Review gates and author self-check

PHX-R1-001/002/003/005 retain original **HIGH** labels; PHX-R1-004 retains **MEDIUM**. This revision addresses requirements, not final reviewer disposition. Preserve raw round-1 report and frozen hashes. Mandatory Laya per-finding evidence worker independently records original findings under `.claude/handoffs/2026-10-18-160735-review-severity-policy.md`; this fixer does not invoke Laya or disturb it. Missing coverage blocks promotion, not the document corrections.

Coordinator: fresh GPT 6.1 Sol review/fix iterations until zero unresolved Critical/High; then fresh **Claude Opus 5.5 HIGH-effort** evaluation through installed `ai-kit-spec-review`. Verify actual model/effort, retain original labels and genuine reports/receipts, and report unsupported model/effort, quota, disagreement, no progress or finite-budget exhaustion without substitution/global-settings edits. Only approved plans proceed to implementation; this assignment performs none and dispatches no reviewers itself. No new approval prompt for the already-authorized workflow.

Self-check mapping: topology/pins/privacy (Task 1); complete typed fixture/expected/receipt contracts and durable pre-POST allocation (Task 2); pinned REST payloads, exact identity/content readback and uncertain-run tests/offline sender (Task 3); durable shell handoff/scoped lifecycle/recreation (Task 4); opt-in serialized runtime/non-interference proof (Task 5); honest UI/resource/retention/fit evidence (Task 6). All five Review Focus classes have owning checks. Signatures/types/actions agree with the spec; plan references the shared contract instead of pasting whole implementation bodies. Independent re-review, mandatory advisory receipts and final approval remain coordinator gates, not claims made by this fix.
