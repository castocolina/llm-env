# Idle-aware OmniRoute Updates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Correct immutable image comparison and require a continuous observed idle window, deferring at the maximum deadline without forcing a busy restart.

**Architecture:** Resolve both index and single manifests to one immutable candidate; validate config/effective-compose agreement and a backend-wide native-dependent preflight under a deployment-resource lock. Any incoming native Podman dependency, or uncertainty while inventorying all backend containers and their inspected dependency/configuration, defers before tag or service mutation. Existing APIs plus a capability-qualified cursor-based log observer supply the conservative gate. Only a separately proven dependency-free disposable deployment can exercise ID-scoped stop/remove and a private literal service-only compose projection; never use the provider's forced-recreation path.

**Tech Stack:** Existing Bash/flock/setsid/Podman/podman-compose/journalctl/jq/uv/Python/pytest, plus skopeo as an explicitly checked host prerequisite. No new runtime Python packages or services.

**Spec:** `docs/superpowers/specs/2026-10-18-omniroute-idle-aware-update-design.md` (revision 3; read completely).

## Global Constraints

- Define `min_wait="${LLM_ENV_UPDATE_IDLE_SECONDS-300}"` and `max_deferred_wait="${LLM_ENV_UPDATE_MAX_WAIT_SECONDS-10800}"` once, in seconds. Defaults are **300 continuous idle seconds** and **10800 maximum seconds**. Empty/invalid/overflowing values or maximum < minimum fail before staging. No force bypass.
- Deadline starts after staging; monotonic and exclusive (`now >= deadline` defers); traffic never extends it. All observation I/O uses the remaining budget.
- No ingress/gateway/preload/sidecar/request registry or changes to `pylib/compose.py` for tracking. No paid inference probes, new services, automatic retry, notifications or zero-drop promises.
- Unknown capability, freshness, readiness, ownership or continuity means defer. No supported production profile is asserted by this revision.
- No incoming native Podman dependency on the captured OmniRoute ID is supported: defer before tag/service mutation if any complete-backend inspection finds one or cannot prove absence. No main-compose rewrite, config migration/pinning, whole-stack stop/start, automatic rollback, or removal of sibling containers/networks/volumes.
- Bash owns deployment lifecycle and observer scope creation; a bounded Python observer owns/reaps only its journal-reader descendants. Python application entrypoints use `uv run llmenv.py <subcommand>`; tests use the established `uv run pytest`/`make test` paths.
- After shell edits run `make validate`; after Python edits run `make validate && make test`.
- Preserve unrelated dirty work. This document revision stages/commits nothing. At later approved implementation checkpoints stage only reviewed task paths/hunks, never `git add -A`; no commit of another task's work.
- GPT 6.1 Sol review/fix until zero unresolved Critical/High → fresh Opus 5.5 HIGH evaluation → explicit user approval before implementation. Independent per-finding Laya evidence/adjudication is mandatory; round 1 uses its frozen snapshots and original labels. Production OmniRoute replacement remains excluded even after implementation approval.

## Review Focus

1. Alternate configs/symlink/project aliases still address one container; exactly one updater can enter commit (Task 2).
2. Stored incoming native Podman dependencies can block captured-ID removal; inventory all backend containers and their inspected dependency IDs/configuration, defer uncertain/dependency-bearing topologies before mutation, and recheck at commit (Tasks 2, 4). The default dependency-bearing topology is a safe-deferral case, never a replacement-success case.
3. Silent/disabled/lagging logs, uncovered media/upgrade routes and stale health cannot become zero; exact-artifact profiles and cursor barriers must qualify every signal (Task 3).
4. Single-manifest tag movement, stale rendered images and late config/provider/resource drift cannot select an unexamined candidate (Tasks 1, 2, 4).
5. SIGKILL of the updater must not leak observers or their lock descriptors; death after mutation starts is explicitly nontransactional, and native drain has a residual arrival race (Tasks 3–5).

## File map and task boundaries

| File | Responsibility |
| --- | --- |
| `pylib/omniroute_update.py` (new) | Pure immutable image resolution and idle transition types/functions |
| `pylib/omniroute_update_deployment.py` (new) | Pure agreement, fingerprints and literal service projection validation |
| `pylib/omniroute_activity.py` (new) | Capability schemas, bounded API parsing, cursor barriers and scoped journal observer |
| `tools/omniroute-update-lib.sh` (new) | Lock, staging, controlled provider calls, bounded orchestration/commit |
| `scripts/update-omniroute.sh` | Public entrypoint and exit messages, no config migration |
| `llmenv.py` | CLI adapters, imports and dispatch only |
| `pylib/omniroute.py` | Optional bounded timeout/body limit for existing auth/request helpers; preserve callers |
| `setup/prerequisites.sh` | Explicit skopeo/journalctl/setsid checks, no updater-time installation |
| `tests/test_omniroute_update.py`, `tests/test_omniroute_update_deployment.py`, `tests/test_omniroute_activity.py` (new) | Pure fixture tests and subprocess ownership tests |
| `tests/test_shell.py`, `tests/test_cli.py`, `tests/test_omniroute.py` | Existing stub/CLI/helper regression suites |
| `tests/test_omniroute_update_integration.py`, `tests/fixtures/omniroute_update/` (new) | Opt-in isolated provider topology, fake inference upstream and sanitized fixture evidence |
| `pylib/omniroute_update_profiles/` (new data only) | Reviewed exact-image capability evidence; no wildcard/default-safe profile |
| `README.md`, `scripts/help.sh`, `AGENTS.md`, `.agents/architecture.md` | Operator policy, limitations and exits after approved implementation |

Shared conventions: frozen dataclasses for the Python types below; JSON serialization uses the documented field names. Invalid metadata raises `ValueError`; activity transport/schema errors use `OmniRouteError` internally and become sanitized unknown/75 at the updater. Human logs go to stderr, scalar JSON to stdout. Do not add undocumented production test-clock/profile-bypass switches. Test helpers below live in their owning test file and are not product interfaces.

## Task 1: Complete immutable image resolution

**Files:** Create `pylib/omniroute_update.py`, `tests/test_omniroute_update.py`; modify `llmenv.py`, `setup/prerequisites.sh`, `tests/test_cli.py`, `tests/test_shell.py` (prerequisite cases).

**Interfaces produced:**
- `Platform(os: str, architecture: str, variant: str | None)`.
- `Resolution(kind: str, repository: str, source_digest: str, manifest_digest: str, immutable_ref: str, config_id: str, platform: Platform)`.
- `normalize_image_id(value: str) -> str`.
- `select_platform_manifest(document: dict[str, Any], *, platform: Platform) -> str | None` (`None` means supported single manifest, not missing digest).
- `resolve_image(repository: str, source_raw: bytes, selected_raw: bytes | None, *, platform: Platform) -> Resolution`.
- `uv run llmenv.py omniroute-update-select --source-file FILE --platform-file FILE` emits `kind`, `source_digest`, `manifest_digest` for the initial immutable selection (single uses source digest; index uses selected descriptor). Bash fetches that digest for an index. Then `uv run llmenv.py omniroute-update-image --repository REPO --source-file FILE --platform-file FILE [--selected-file FILE]` emits all Resolution fields, with platform flattened as `os/architecture/variant`; index requires the selected file. Neither CLI accesses the registry; no partial selection result can reach staging.

- [ ] **Step 1: Write failing resolution and CLI fixtures.** Use OCI and Docker schema-2 media types; include index descriptors for measured platforms and an attestation descriptor with unknown platform. Put raw test bytes in fixture files unchanged. Representative test (local `single_manifest` emits a minimal schema-2 object with config and layers):

```python
def test_single_manifest_carries_initial_immutable_ref():
    raw = single_manifest(config_id="sha256:" + "a" * 64)
    first = resolve_image("docker.io/example/router", raw, None,
                          platform=Platform("linux", "amd64", None))
    assert first.kind == "single"
    assert first.manifest_digest == "sha256:" + hashlib.sha256(raw).hexdigest()
    assert first.immutable_ref == first.repository + "@" + first.manifest_digest
    assert first.config_id == "sha256:" + "a" * 64
```

Also assert the recorded index/platform/config descriptor chain is unchanged against running ID (synthetic raw fixtures need not hash to the recorded real source digest; never claim they do); ARM selection; absent/ambiguous variant rejection; attestation exclusion; selected raw digest mismatch; nested index/schema-1/malformed SHA/absent file rejection. CLI test asserts both branches emit identical required keys, not the old kind-dependent incomplete schema.

- [ ] **Step 2: Run the failing tests.** `uv run pytest tests/test_omniroute_update.py tests/test_cli.py -k 'update_image or manifest or image_id' -v` → new tests fail for absent interfaces (not fixture syntax errors).

- [ ] **Step 3: Implement the declared interfaces and prerequisite checks.** Hash exact raw bytes for supported media types (cross-check with `skopeo manifest-digest` in fixture tests); index branch verifies selected digest before config extraction, single branch uses initial raw digest. Bash in Task 4 fetches raw files directly using redirection, never JSON reserialization/command substitution. Normalize Docker Hub references with default tag only where omitted. Reject unsupported registries with explanatory defer; release-list failure is only an informational warning if immutable identity resolves.

- [ ] **Step 4: Verify and checkpoint.** `uv run pytest tests/test_omniroute_update.py tests/test_cli.py -k 'update_image or manifest or image_id' -v && make validate && make test` → all selected/new tests and existing suite pass. Review/stage only this task's paths/hunks; checkpoint message `feat: resolve immutable OmniRoute image identities` after implementation is authorized.

## Task 2: Bind candidate and lock to the actual deployment; construct safe projection

**Files:** Create `pylib/omniroute_update_deployment.py`, `tests/test_omniroute_update_deployment.py`, `tools/omniroute-update-lib.sh`; extend CLI and shell/CLI tests.

**Consumes:** `Resolution` from Task 1.

**Interfaces produced:**
- `Deployment(resource_key: str, project: str, container_name: str, container_id: str, image_id: str, started_at: str, fingerprint: str, stop_seconds: int)`; private resource maps/inputs remain in the owning invocation directory, not this public JSON.
- `deployment_key(*, uid: int, graph_root: str, run_root: str, container_name: str) -> str` (canonical roots).
- `validate_deployment(config: dict[str, Any], effective: dict[str, Any], inspect: dict[str, Any]) -> None`.
- `fingerprint_inputs(config_raw: bytes, compose_raw: bytes, effective: dict[str, Any], evidence: dict[str, Any]) -> str`; `evidence` contains the exact paths/env/provider/backend/container/sibling/resource scope in the spec.
- `DependencySnapshot(backend_id: str, containers: tuple[dict[str, Any], ...], fingerprint: str)`; each entry is an inspected backend container ID plus its validated native dependency IDs and relevant create configuration. Inventory includes stopped containers and containers outside the target compose project.
- `capture_dependency_snapshot(backend: dict[str, Any], container_ids: list[str], inspections: list[dict[str, Any]]) -> DependencySnapshot`; reject incomplete inventory, inspection/schema uncertainty, duplicate/missing IDs, or unparseable dependency/configuration evidence. `assert_no_incoming_dependencies(snapshot: DependencySnapshot, target_id: str) -> None` raises a deferral if any inspected container other than target records target ID as a native dependency.
- `make_projection(effective: dict[str, Any], deployment: dict[str, Any], candidate: Resolution) -> dict[str, Any]`; input `deployment` includes inspected network/volume identities and a dependency-free snapshot. Returns literal Compose data with `$` escaped for serialization, not an executable shell command.
- `uv run llmenv.py omniroute-update-deployment --config PATH --compose-json PATH --evidence-json PATH --output-dir DIR` validates/fingerprints private evidence and emits Deployment JSON; `omniroute-update-projection --compose-json PATH --evidence-json PATH --resolution-json PATH --output PATH` writes 0600 YAML.
- Bash `update_capture_deployment DIR`, `update_lock_deployment RESOURCE_KEY`, `update_capture_dependency_snapshot DIR`, `update_recheck_dependency_snapshot DIR`, `update_check_continuity DIR`, `update_make_projection DIR`, `update_projection_up DIR` return 0/75/1. Inventory uses the selected local backend's complete `podman ps --all` ID set and `podman container inspect` evidence, not project/running filters. Paths/keys are explicit arguments; original provider/compose/environment, target evidence and dependency snapshot are frozen private files in DIR. The only shell global resource is `update_lock_fd`.

- [ ] **Step 1: Add agreement/projection and backend-wide dependency preflight tests before implementation.** Minimal supported effective fixture:

```yaml
services:
  llm-server:
    image: docker.io/library/python:3.13-alpine
    healthcheck: {test: ['CMD', 'true']}
  omniroute:
    image: docker.io/example/router:next
    container_name: omniroute
    volumes: [omniroute-data:/app/data]
    environment: {PORT: '20128', INITIAL_PASSWORD: 'synthetic$literal', REQUIRE_API_KEY: 'true'}
    ports: ['127.0.0.1:20128:20128']
    stop_grace_period: 40s
    depends_on: {llm-server: {condition: service_healthy}}
  remote-setup:
    image: docker.io/library/python:3.13-alpine
    depends_on: {omniroute: {condition: service_healthy}}
volumes: {omniroute-data: {}}
```

Supply matching synthetic config/inspect fixtures, including project labels, network IDs and named-volume names. `test_initial_image_disagreement_defers_before_pull` changes config to image B, leaves effective image A and asserts exit 75, no pull/tag/stop/rm. `test_dependency_preflight_scans_all_backend_containers` includes stopped and cross-project containers; an incoming dependency on the target yields 75 before staging/tag/stop/rm. `test_dependency_inspection_uncertainty_defers_before_mutation` covers a missing container, failed inspect, malformed dependency IDs/configuration and inventory drift. `test_projection_is_literal_and_resource_identical` asserts service keys == `{'omniroute'}`, immutable candidate image, no depends_on, unchanged ports/health/grace, external networks/volumes with original names, exact dollar roundtrip through installed provider config. Projection construction requires a dependency-free preflight snapshot. Reject pods/indirect compose inputs/anonymous mounts/scale/name/ownership ambiguity. Assert every fingerprint input change (including consumed env, provider hash, sibling ID, mount/resource/health fields) changes the fingerprint; a baseline hash cannot excuse initial disagreement.

- [ ] **Step 2: Add two-invocation lock tests and run red.** `test_alternate_config_paths_and_symlinks_share_lock` uses two configs and aliases, one HOME/backend/name, concurrent processes and a barrier before commit; assert only one lock winner, loser 75 before staging, lock inode unchanged after release and observer process cannot hold it. Assert replacement ID change does not change resource key. Run `uv run pytest tests/test_omniroute_update_deployment.py tests/test_shell.py -k 'projection or agreement or fingerprint or deployment_lock or alternate_config' -v` → fail on missing behavior.

- [ ] **Step 3: Implement pure validation/projection and Bash capture/lock.** Capture `podman info --format json`, the complete local-backend `podman ps --all --no-trunc` ID set, and `podman container inspect` for every listed container, plus target image/network/volume inspections, read-only into private files. Validate inspected `Dependencies` arrays and relevant create configuration for every container; missing IDs, failed/truncated inspections, unknown schema, mismatched backend or inability to establish inventory completeness returns 75 before image staging/tagging/service mutation. Identify any container whose native dependency IDs include the captured OmniRoute ID, regardless of state, project or compose ownership; any match is unsupported and defers. Use installed provider `config` with controlled environment and explicit original project; normalize to JSON via the new CLI's existing PyYAML dependency. Reject unsupported forms from the spec rather than silently omitting fingerprints. Project is taken from inspected labels and checked against original compose resolution. Flock key excludes config/project alias/current container ID; lock directory checks ownership/mode/symlinks, lock FD stays with owner, never unlink lock inode. No calls to migration/render/start scripts.

- [ ] **Step 4: Pin and implement the projection launch helper with stub assertions.** Add `test_projection_up_never_uses_provider_recreate_or_down`: invoke `update_projection_up DIR` against a stub provider and assert exact argv `--in-pod false --env-file /dev/null -p PROJECT -f PROJECTION up -d --no-deps --no-recreate --no-build --pull never omniroute`. Assert no `--force-recreate`, `--remove-orphans`, `down`, network/volume create/rm or sibling operation. Implement this bounded launch helper now; Task 4 owns old-ID stop/rm and its preconditions. Do not mistake this stub for Task 5 integration proof.

- [ ] **Step 5: Verify and checkpoint.** `uv run pytest tests/test_omniroute_update_deployment.py tests/test_shell.py -k 'projection or agreement or fingerprint or deployment_lock or alternate_config' -v && make validate && make test` → all Task 2 helper tests and existing tests pass. Selective checkpoint `feat: bind OmniRoute update to deployment identity`. Do not claim full updater acceptance before Task 4.

## Task 3: Define trustworthy activity and bound the observer's ownership

**Files:** Create `pylib/omniroute_activity.py`, `tests/test_omniroute_activity.py`, profile schema/fixtures under `tests/fixtures/omniroute_update/`; modify CLI, `pylib/omniroute.py` and its helper tests, Bash scope helpers. Do not manufacture a deployed capability profile.

**Consumes:** Deployment fingerprint/captured container identity (Task 2).

**Interfaces produced:**
- `CapabilityProfile(image_id: str, artifact_sha256: dict[str, str], routes: dict[str, str], health_cache_ms: int, health_refresh_bound_ms: int, evidence_sha256: str, logger: dict[str, Any], schemas: dict[str, Any], delivery: dict[str, Any], reachable_routes: frozenset[str])`. `routes` keys are `METHOD /path`, values reference arrival/in-flight/queue fixture evidence; every reachable route must be accounted for.
- `parse_inference_arrival(line: str, *, routes: frozenset[str]) -> str | None` returns normalized `METHOD /path`, no raw record/suffix. Unrecognized relevant inference route is an error, not None.
- `normalize_activity(call_logs: Any, usage: Any, health: Any, *, profile: CapabilityProfile, received_wall_ms: int, elapsed_ms: int, clock_tolerance_ms: int) -> dict[str, Any]` returns counts/hashed row metadata/health age, rejecting required-schema gaps.
- `health_is_fresh(timestamp_ms: int, *, received_wall_ms: int, elapsed_ms: int, wall_elapsed_ms: int, cache_ms: int, refresh_bound_ms: int, clock_tolerance_ms: int) -> bool` implements the spec's exact age/clock predicate.
- `observe_journal(*, container_id: str, parent_pid: int, parent_start_tick: int, lifetime_seconds: float, state_path: Path, profile: CapabilityProfile) -> int` owns its journalctl children, bounded pipes and v1 atomic sanitized state. No API cookies in this state.
- `uv run llmenv.py omniroute-arrivals --container-id ID --parent-pid PID --parent-start-tick TICK --lifetime-seconds N --profile PATH --state-file PATH`; `omniroute-activity --config PATH --arrival-state PATH --profile PATH --timeout-seconds N` emits v1 schema from spec.
- Existing `_login(base_url: str, password: str, *, timeout_seconds: float = 10, max_body_bytes: int = 8388608) -> str` and `_request(method: str, url: str, session_token: str, payload: dict[str, Any] | None = None, *, timeout_seconds: float = 10, max_body_bytes: int = 8388608) -> Any`. Keep existing positional callers and errors; new sampler maps failures to bounded enum reasons, not raw API error text. Whole CLI sampling also has a shell timeout, since socket timeout alone is not a total body deadline.

- [ ] **Step 1: Write failing API/capability fixtures.** Representative synthetic bodies:

```python
call_logs = [{"id": "synthetic-1", "active": True,
              "path": "/v1/chat/completions", "timestamp": "2026-10-18T17:00:00Z"}]
usage = {"pending": {"byModel": {"synthetic": 1}, "byAccount": {"synthetic": 1}}}
health = {"timestamp": "2026-10-18T17:00:00Z", "chatAdmission": {
    "activeHeavy": 0, "activeHealthyHeadroom": 0, "waiting": 1, "queuedBytes": 0}}
# In test_normalize_active_pending_queue: assert active == 1, pending == 1,
# admission == 1; no persisted output contains "synthetic" account/model text.
```

`test_health_age_uses_exact_profile` asserts cache=1000/refresh=500/injected clock tolerance=250 accepts age=1750, rejects 1751; TTL=30000 needs a different verified profile; negative/future timestamp, clock discrepancy 251 ms against that synthetic tolerance, GET elapsed 5001 ms, nonadvancing timestamp beyond bound and absent refresh evidence are unknown. These are synthetic boundaries, not recorded deployment measurements. Cover stale/swept active rows, booleans/negative counts, malformed/auth/public-health fallback schemas, pending without connection ID, full persisted page/active-row truncation, row-ID retention/reset/overflow, 401 reauth once, body bound and wall-time deadline. Existing caller timeout defaults must still pass their tests.

- [ ] **Step 2: Write failing parser/readiness/ownership tests.** Exact representative MESSAGE is `{"level":30,"tag":"HTTP","method":"POST","path":"/v1/chat/completions | private-model | 1 msgs"}`; only `POST /v1/chat/completions` may survive. Wrap it in synthetic journal JSON with `CONTAINER_ID_FULL`, `__CURSOR`, `_BOOT_ID`, `__REALTIME_TIMESTAMP`. Cover ANSI/noise, split lines, malformed structured records, unsupported inference method/family, fallback logs, loopback arrivals and health/OAuth exclusion. Test logger warn/file-only/unknown defaults, missing artifact/route/delivery evidence → unknown; cursor barrier seen/unseen, replay deduplication, delayed backlog, vacuum/drop/EOF/boot change and >64 KiB record/>10000 IDs or cursors → unknown. Silent valid unchanged barrier remains healthy. Raw log/API content must not reach tempfiles/stdout/stderr.

`test_sigkill_updater_reaps_observer_and_releases_lock` launches the actual worker with fake journalctl executables, SIGKILLs parent, asserts every owned descendant is gone/reaped within 5 seconds and a new updater acquires lock even during grace. Also cover PID reuse/start-tick mismatch, no input/hung child, finite lifetime expiry, TERM and normal exit. Sample `/proc/PID/fd` in tests to prove the worker and journalctl never inherited the flock FD.

- [ ] **Step 3: Run red.** `uv run pytest tests/test_omniroute_activity.py tests/test_omniroute.py tests/test_cli.py -k 'activity or arrival or observer or timeout or body_limit or health_age' -v` → new interfaces/behavior fail, old helper tests continue running.

- [ ] **Step 4: Implement schema predicates and cursor algorithm.** Read exact profiles only; verify deployed image/artifact/env/logger/delivery/route evidence matches before sampling. Use finite `journalctl --output=json --no-pager -n 1 CONTAINER_ID_FULL=ID` for seed/latest barriers and follow using `--after-cursor=CURSOR --follow --no-tail`; re-seek/check cursor existence for continuity. Filter identity/boot on every record. No seed or unprovable suppression/loss means defer. Cursor hashes retained in memory permit exact barrier membership; atomic v1 state writes contain readiness booleans and generations, no raw journal cursor/payload. Measure invocation-local clock tolerance using the spec's 20 paired readings plus measured resolution; pass it explicitly to freshness checks, including inter-sample clock-jump detection. Do not use synthetic test tolerance 250 as a production host assumption. Require a post-readiness health timestamp advance before first idle credit. API GETs/limits are exactly those in spec; never request `deep=1`, provider validation or call artifacts.

- [ ] **Step 5: Implement the scoped supervisor, not an orphanable pipe.** Bash launches `setsid uv run llmenv.py omniroute-arrivals ...` with lock FD closed, parent PID/start tick and lifetime `remaining + 10`. Python uses `subprocess.Popen(..., close_fds=True)` for journalctl only, `selectors`/nonblocking reads, a 1-second maximum parent-check interval, monotonic lifetime, TERM/2-second grace/KILL/reap of owned descendants. Close lock FD on samplers/sleeps/metadata commands too. Shell traps stop/wait its owned observer scope; do not kill by container name or broad pkill. Catchable exits remove private files; abrupt death may leave only owned private/staged artifacts before commit. Reconcile parent identity before any mutation in Task 4. Do not use shell traps as a SIGKILL guarantee.

- [ ] **Step 6: Verify and checkpoint.** `uv run pytest tests/test_omniroute_activity.py tests/test_omniroute.py tests/test_cli.py -v && make validate && make test` → pass including abrupt-parent-death test. Checkpoint only task paths/hunks as `feat: qualify and bound OmniRoute activity observation`. Record no production profile as supported until Task 5 supplies exact-artifact evidence; missing route or journal delivery proof remains an explicit defer, not an assumption.

## Task 4: Connect staging, continuous-idle transitions and isolated commit

**Files:** Modify `scripts/update-omniroute.sh`, `tools/omniroute-update-lib.sh`, `pylib/omniroute_update.py`, CLI and shell/unit tests.

**Consumes:** Resolution, Deployment, v1 activity state and projection from Tasks 1–3.

**Interfaces produced:**
- `IdleState(wait_started_at: float, quiet_since: float | None, arrival_generation: int, row_generation: int)`; `advance_idle(state: IdleState, sample: dict[str, Any], *, now: float, min_wait: int, max_wait: int) -> tuple[IdleState, str]` returns `wait`, `resample` or `defer` (never mutation permission by itself).
- `uv run llmenv.py omniroute-update-idle --state-file PATH --sample-file PATH --now N --min-wait N --max-wait N` emits state/action; Bash supplies measured monotonic time, not user wall clock.
- Bash `update_monotonic_seconds`, `update_resolve_candidate DIR`, `update_stage_candidate DIR`, `update_start_observer DIR`, `update_sample_activity DIR REMAINING`, `update_wait_for_idle DIR MIN MAX`, `update_stop_observer DIR`, `update_commit_candidate DIR` (0 success, 75 defer, 1 error). `update_monotonic_seconds` reads `/proc/uptime`; tests stub this function/commands, never add production clock overrides.

- [ ] **Step 1: Write failing transition tests and a scripted shell scenario harness.** Extend `run_update_omniroute_with_stubs` in `tests/test_shell.py`: capture backend/container/image/config/effective-compose identities, registry raw bytes, time, snapshots/barriers, process tree and command journal. Define test-local `run_update_scenario(tmp_path, *, times, samples, idle=None, maximum=None, source_kind='index', config_image=None, compose_image=None) -> tuple[CompletedProcess[str], list[list[str]]]`; no new product bypass flags.

```python
def test_deadline_is_exclusive_even_when_quiet_qualifies():
    s = IdleState(0.0, 10500.0, 0, 0)
    _, action = advance_idle(s, quiet_sample(), now=10800.0,
                             min_wait=300, max_wait=10800)
    assert action == "defer"
```

Also assert default quiet t=299 wait/t=300 resample; arrival or completion at 299 clears quiet; active stream at 600 waits; first sample has no history credit; continuously busy through 10800 defers. Overrides 60/600 use the same logic: 59 wait, 60 resample, activity at 59 resets, 600 defers. Empty/zero/negative/noninteger/overflow/max<min fail before staging. Set maximum supported durations to 2147483647 seconds each and guard monotonic addition overflow; no hidden forced-restart cap.

- [ ] **Step 2: Add orchestration safety scenarios and run red.** Names/assertions: `test_single_manifest_tag_moves_but_initial_digest_is_staged` and index equivalent assert only original immutable ref is pulled/recreated after tag changes; `test_same_config_different_index_is_noop` asserts no observer/replacement; `test_local_tag_is_not_running_identity`; `test_initial_image_disagreement_defers_before_pull`; `test_default_dependency_topology_defers_without_mutation` checks exit 75, unchanged OmniRoute ID/running state/local tag and all sibling IDs; stopped/cross-project incoming dependencies and uncertain inspections defer too. `test_new_dependency_before_commit_defers_before_tag` verifies a changed complete backend snapshot returns 75 before tag/stop/rm/create. `test_external_dependency_race_after_recheck_is_non_atomic` records the possible stop-then-rm refusal without force-removal or rollback claims. Also cover `test_change_during_wait_defers` parameterized over all fingerprint/identity/feed fields; `test_final_arrival_resumes_original_budget`; `test_final_unknown_or_deadline_never_mutates`; `test_missing_running_container_defers`; `test_competing_alternate_configs_cannot_commit`; `test_stop_rm_create_and_health_fail_truthfully`. Assert no tag on deferred paths and no migration/start/stop-stack scripts in any case. Failed retag cannot stop; failed stop cannot rm/create; failed rm cannot create; unexpected same-name container is never removed. Failed/wrong-image/unhealthy replacement returns 1 and no automatic rollback. Run `uv run pytest tests/test_omniroute_update.py tests/test_shell.py -k 'update_omniroute or idle or commit or tag_moves' -v` → expected failures against old updater.

- [ ] **Step 3: Implement orchestration and the ambiguous wait algorithm.** Initialize/validate timing once; capture deployment/backend → resource lock → recapture/agreement and complete backend-wide dependency snapshot → defer on incoming dependency or uncertainty → resolve → unchanged exit or immutable stage/verify → start deadline and observer → wait. Retain the dependency snapshot and recheck it immediately before any commit mutation. No config migration or local floating-tag pull. Registry files use `skopeo inspect --raw`/`manifest-digest` as in spec; stage `podman pull "$immutable_ref"` and inspect its config ID/platform. Bound prewait operations operationally (60 seconds each, error if exceeded); these bounds do not consume/extend the later observation deadline.

```text
sample start: if now >= deadline -> defer
collect all APIs + newest cursor barrier + fingerprints within remaining budget
if completion now >= deadline or unknown/continuity loss -> defer
if busy or arrival_generation/row_generation changed -> clear quiet_since
else if quiet_since unset and readiness + timestamp-advance established -> quiet_since = now
if quiet_since set and now - quiet_since >= min_wait -> immediate complete resample
  if new activity -> clear quiet_since and continue, retaining deadline
  if unknown/drift/deadline -> defer
  else -> enter commit worker after one last identity/deadline/parent check
sleep min(5 seconds, remaining); never extend deadline
```

- [ ] **Step 4: Implement `update_commit_candidate DIR` with a final backend-wide dependency recheck.** A bounded synchronous Bash commit worker retains lock FD across the sequence; every spawned Podman/provider command closes that FD, so conmon/observers cannot retain it. If outer Bash dies after the mutation boundary, this worker may finish the already-started bounded sequence; never promise cancellation then. Immediately before the first tag/service mutation, recollect and validate every backend container ID, native `Dependencies` array and relevant create configuration; compare with the eligible dependency-free snapshot and defer on any incoming dependency, changed inventory/configuration, or inspection uncertainty. This is a best-effort guard against arbitrary external Podman actors, not an atomic lock against them: an external container/dependency can still be created after recheck, and Podman may then refuse removal after stop; never claim that race is eliminated or auto-recover it. Use captured ID in stop/rm; convert original grace to positive seconds, reject missing/unparseable grace. Use the private projection and installed provider directly with `--no-recreate`, **not** `--force-recreate`, as specified. Each stop command is bounded by grace+10 seconds, other mutations by 60 seconds; never force rm on timeout. Verify sibling/resource identities after operations and replacement actual image/health within positive validated `LLM_ENV_HEALTH_TIMEOUT_SECONDS` (existing default 60). Do not automatically replay a failed/half-completed commit. Report old image/recovery instructions without passwords/raw config.

- [ ] **Step 5: Verify and checkpoint.** `uv run pytest tests/test_omniroute_update.py tests/test_omniroute_update_deployment.py tests/test_omniroute_activity.py tests/test_shell.py tests/test_cli.py -v && make validate && make test` → pass all updated expectations, including Task 2 projection-launch assertions. Checkpoint `feat: gate isolated OmniRoute updates on observed idle` using selective task hunks. Passing stubs do not authorize procedure use without Task 5's separately proven dependency-free fixture, and never authorize production replacement. The normal native dependency-bearing deployment remains unsupported and deferred until a separately authorized migration or platform capability exists; passing a quiet window is not update support.

## Task 5: Prove safe deferral for the default topology and replacement only for a dependency-free disposable fixture

**Files:** Create `tests/test_omniroute_update_integration.py`, `tests/fixtures/omniroute_update/{compose.yml,fake_provider.py,README.md}` and sanitized exact-artifact evidence/profile files. Test fixtures are not production stack additions.

**Interfaces:** Opt-in switch `LLM_ENV_RUN_OMNIROUTE_UPDATE_INTEGRATION=1`; explicit preloaded immutable image refs in `LLM_ENV_TEST_OMNIROUTE_IMAGE` and `LLM_ENV_TEST_PYTHON_IMAGE`, provider path in `LLM_ENV_TEST_COMPOSE_PROVIDER`. Do not pull images implicitly. The ordinary suite skips this module; the opt-in suite fails, not skips, if prerequisites/evidence are missing. Test-local helper `launch_disposable_stack(tmp_path: Path, *, omniroute_image: str, python_image: str, provider: str) -> Iterator[dict[str, Any]]` allocates unique resource names/loopback ports and returns captured IDs/paths. Retain the real local Podman HOME/storage/runtime environment so preloaded images remain accessible; never point the public updater at production for these tests. A test-local shell harness sources the helpers and supplies private fixture evidence/config/compose globals and prefixed target identity directly, without adding public production bypass/target flags. Cleanup may remove only its recorded resources, never production names or broad prune/down against another project.

- [ ] **Step 1: Write the opt-in guard, fixtures and safe launch/cleanup tests.** Requirements: local rootless Podman, installed inspected provider, journalctl access/delivery-policy evidence, skopeo, preloaded unmodified OmniRoute image and Python image; no GPU or real provider credentials. Resolve images through `podman image inspect` and require immutable input/config IDs. Unique project prefix `llm-env-oru-test-<random>`, private config/compose/evidence under tmp_path, `container_name` values all prefixed, dedicated named volumes/network with fixture-local aliases `llm-server` and `omniroute` (the existing provisioning/internal URL contracts), no host/container socket mounts and no host data binds except the read-only synthetic fixture directory and explicitly allowlisted read-only repo source/installer files for real remote-setup. Assert any name/path outside owned namespace aborts before mutation. Use a locally loaded second tag for the same image if necessary to test replacement, not a paid/new download requirement.

- [ ] **Step 2: Test default-topology safe deferral and separately prove a dependency-free success fixture.** First launch the disposable fixture using the repository's actual generated dependency shape: healthy `llm-server` → `omniroute` → `remote-setup`, plus an unrelated sibling. Inspect the captured OmniRoute ID and prove `remote-setup`'s installed Podman `Dependencies` contains it. Run the updater's preflight and assert exit 75 before any tag/service mutation; compare the original OmniRoute ID and running state, local tag target, every sibling ID, network ID and named-volume content marker before/after. This is an explicit safe-deferral acceptance, not a replacement test. Separately create a minimal synthetic service deployment with no Compose `depends_on` or other dependency declaration; source-ground the fixture shape against `pylib/compose.py` and installed provider/Podman semantics, then prove by inspecting the complete backend inventory that no container's native `Dependencies` contains the target ID and all relevant inspected configuration parses successfully. Only this independent, proven dependency-free fixture may exercise old-ID stop/rm + projection `up --no-recreate` success. Assert sibling IDs, original network ID and volume names/content markers unchanged, service discovery via inspect/`ps`, replacement image/health correct, and no network/volume create/rm. Launch and cleanup only recorded fixture resources; never transform the normal dependency-bearing topology into a false success case. Wrap provider/Podman calls for audit; do not edit installed provider or infer integration success from argv.

- [ ] **Step 3: Configure the fake upstream using verified project contracts.** Run fixture server as Python container command (`python3 /fixtures/fake_provider.py`, not a host Python application entrypoint). Implement `/v1/models` and deterministic `/v1/chat/completions` success/error/long SSE; test code controls release/queue gates through loopback-only synthetic endpoints. Use existing `build_payload(port, api_key)` semantics (`providerSpecificData.baseUrl` points at `http://llm-server:<port>/v1`), `_login`/`_request` and `provision` in `pylib/omniroute.py:166–199`; scoped test key uses existing `ensure_api_key`, never dashboard password as inference bearer. Fixture startup/config occurs via the existing CLI or imported functions inside pytest; no guessed setup API. Test remote-setup health by its existing `/setup.sh`, without real remote-client installs.

- [ ] **Step 4: Add exact-artifact observation and shutdown tests.** Prove merged active rows outside persisted page, long SSE busy past a quiet interval, queued work/error completion and short request between polls. Record sanitized actual journal envelope/schema/cursor behavior, effective logger/delivery evidence, artifact hashes and measured health cache/refresh bound; reject profile if any required coverage is unprovable. Inventory every reachable route/method/upgrade from exact artifact/runtime settings: responses/messages/chat, embeddings, media and WebSocket must either have evidence or make whole-deployment eligibility unknown. Unsupported route test must return 75, not disable that route. A fixture image with incomplete coverage may still prove busy/unknown handling; any successful replacement mechanics must use only the separately inspected dependency-free fixture and direct test calls, never the normal dependency-bearing topology. It must not be published as an eligible production profile or add a runtime bypass. Health/OAuth/dashboard polling must not change arrival generation. Boundary-arrival test uses synchronized fake-provider/client gates just after final sample and records native shutdown complete/timeout/refused behavior; do not turn this characterization into a no-drop assertion or add interception/sleeps. Kill updater parent precommit and confirm process/lock isolation with real provider/log children.

- [ ] **Step 5: Run the exact opt-in proof and record results.** After implementation approval and with immutable refs supplied by the operator from already loaded images:

```bash
: "${LLM_ENV_TEST_OMNIROUTE_IMAGE:?set preloaded repository@sha256 reference}"
: "${LLM_ENV_TEST_PYTHON_IMAGE:?set preloaded repository@sha256 reference}"
: "${LLM_ENV_TEST_COMPOSE_PROVIDER:?set inspected absolute provider path}"
LLM_ENV_RUN_OMNIROUTE_UPDATE_INTEGRATION=1 uv run pytest \
  tests/test_omniroute_update_integration.py -n 0 -vv
make validate && make test
```

Expected: opt-in tests execute (none skipped), fixture resources only, the default dependency-bearing topology safely defers with original OmniRoute ID/running state/local tag and sibling IDs unchanged, and only the separately inspected dependency-free fixture can prove successful replacement while preserving resources. Backend inventory uncertainty and commit-time drift defer; record the non-atomic external-mutation race honestly. Supported observation predicates and conservative deferrals pass, native drain outcome is recorded without secrets, and owned resources are cleaned up. If exact deployed image cannot prove coverage/loss bounds, record unknown and keep it deferred; do not fabricate a passing capability profile. If dependency-free provider procedure fails isolation, stop implementation handoff and revise design before any use. Capture actual versions/image IDs/commands and measured results in fixture README; do not claim this documentation revision ran these tests. Selective checkpoint `test: prove disposable OmniRoute update isolation`.

## Task 6: Document limits, validate and hand off without production replacement

**Files:** Modify `README.md`, `scripts/help.sh`, `AGENTS.md`, `.agents/architecture.md`; add assertions in `tests/test_shell.py` for help/exit wording if applicable.

**Interfaces:** Operator outcomes 0 unchanged/updated, 75 deferred/busy/unknown/lock contention, 1 invalid input/operational failure. No automatic retry or force option.

- [ ] **Step 1: Pin help/operating assertions.** Assert configured durations appear in messages; exit 75 explains busy vs unknown vs contention without raw payloads. Documentation must name immutable single/index resolution, config/compose agreement, deployment lock, supported-profile requirement, continuous 300/default and exclusive 10800/default timing with both env overrides, scoped lifetime/death limits, private projection and exact installed-provider proof requirement. Assert help contains no force-restart suggestion or zero-drop promise.

- [ ] **Step 2: Run red and update docs/help.** `uv run pytest tests/test_shell.py -k 'update_omniroute or update_help' -v` → new copy assertions fail until updated. Document manual recovery from partial commit without executing it: preserve old image; inspect actual service/tag/resource state; no auto rollback/retry/start when missing. Explain projection provenance/config-hash labels differ and later main-stack lifecycle is an independent operation. Explicitly retain production replacement exclusion.

- [ ] **Step 3: Validate and checkpoint.** `git diff --check -- README.md scripts/help.sh AGENTS.md .agents/architecture.md && make validate && make test`, plus the Task 5 exact opt-in command → pass or record separate pre-existing failures. Stage only task paths/hunks, checkpoint `docs: explain idle-aware update safety boundaries`. Do not invoke `make update-omniroute` against production.

- [ ] **Step 4: Hand off evidence for review and wait.** Review the final pair and implementation only through the authorized sequence below. Stop for a material user decision if no-drop is required or measured drain behavior is unacceptable; no unapproved infrastructure workaround. No production replacement is an implementation completion criterion.

## Review and authorization ledger (ORU-R1-002/008)

Read the installed `ai-kit-spec-review` skill and full dispatch references before any coordinator review invocation; do not change global configuration or guess model aliases. Review plan and design separately, grounded in this root and read-only upstream, using `/home/bazzite/.claude/skills/ai-kit-spec-review-checklist/references/frameworks/superpowers.md`. Current writing-plans is authoritative on proportion: signatures, representative assertions/fixtures, exact commands and ambiguous algorithms, not whole-program transcripts.

Original round-1 labels remain unchanged while fixes are evaluated:

| Finding | Checklist severity | Impact severity | Revision-3 change / owning task |
| --- | --- | --- | --- |
| ORU-R1-001 | CRITICAL | Medium | No provider force-recreate/down; backend-wide dependency preflight/recheck, default-topology safe deferral, and separate dependency-free fixture (2, 4, 5) |
| ORU-R1-002 | MEDIUM | Low | Current Sol → fresh Opus HIGH → user approval; Laya and production exclusion (global, 6, ledger) |
| ORU-R1-003 | HIGH | Medium | Complete single/index immutable Resolution and tag-movement tests (1, 4) |
| ORU-R1-004 | HIGH | Medium | Initial config/effective-compose agreement and full continuity fingerprint (2, 4) |
| ORU-R1-005 | HIGH | Medium | Backend/name resource lock, alternate-config concurrency (2, 3, 4) |
| ORU-R1-006 | HIGH | Medium | Exact capability schema, cursor barriers, health-age/coverage predicates, unknown fixtures (3, 5) |
| ORU-R1-007 | HIGH | Medium | Parent PID/start-tick lease, finite lifetime/reaping, lock-FD isolation, commit-death distinction (3, 4, 5) |
| ORU-R1-008 | HIGH | Low | Numbered TDD steps, declared types/signatures, executable assertions and opt-in recipe (all) |

Round-1 independent Laya evidence is being recorded against frozen input hashes, not this revised text. Keep receipts separate from raw verdict fields; preserve declared/advisory/final labels and rationale. Missing/unclear receipts block promotion; never downgrade to make the loop terminate. Subsequent Sol review/fix rounds require their own revision hashes and per-finding evidence until zero unresolved Critical/High, then obtain fresh Opus 5.5 HIGH evaluation and explicit user implementation approval. This revision itself dispatches no reviewers, invokes no Laya, and makes no approval claim.

## Author self-review

Coverage: immutable identities (1); agreement/resource lock/backend-wide dependency inventory and safe projection (2); capability/age/readiness/lifetime (3); continuous idle/final dependency recheck/commit verification (4); default-topology safe deferral, separate dependency-free fixture and drain characterization (5); operating/review boundaries (6). All five Review Focus classes have named tests. Shared interfaces use consistent field names; v1 schema and all thresholds are in the spec. Code examples are representative assertions or the ambiguous transition algorithm, not module transcripts. Source reasoning supports the proposed provider procedure but is explicitly not runtime proof. Deployed capability profiles and disposable test results remain implementation evidence to obtain, not invented facts. Both documents retain the approved no-extra-stack policy and residual race.
