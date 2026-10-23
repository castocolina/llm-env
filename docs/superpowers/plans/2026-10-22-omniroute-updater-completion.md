# OmniRoute-only Idle-aware Updater Completion Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (or subagent-driven-development) to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the 13-line safety stub in `scripts/update-omniroute.sh` with a working updater that compares immutable image identities, waits for a continuous observed-idle window (300 s, max wait 10 800 s), and replaces **only** the OmniRoute container — deferring (exit 75) whenever that cannot be proven safe.

**Architecture:** Focused Python modules under `pylib/` (image resolution + deployment preflight, activity sampling, observer supervisor that owns the idle gate + lock path) exposed through one `llmenv.py omniroute-update` subcommand group whose handlers all live in `pylib/omniroute_cli.py` (`resolve verify-staged preflight projection overrides deadline lock-path observe verify-commit`; every flag is defined, produced, stored and consumed inside the owning task); `scripts/update-omniroute.sh` owns the lock, polls the observer's verdict file and commits through a detached worker. A separate one-time, remote-setup-only migration removes the generated `depends_on` edge so the default topology becomes eligible. Everything is unit-testable with fakes; real Podman proofs are opt-in against disposable resources.

**Tech Stack:** Bash, Python 3 via `uv run llmenv.py`, pytest, Podman 5.8.7 / podman-compose 1.6.0, skopeo, journalctl, jq/yq. No new runtime Python packages.

**Spec:** `docs/superpowers/specs/2026-10-18-omniroute-idle-aware-update-design.md` (revision 3 — the safety contract; read it fully before Task 2). Evidence: `.docs/superpowers/research/2026-10-20-omniroute-updater-observations.md` (primary) and `.docs/superpowers/research/2026-10-20-container-resource-sizing.md` (resource limits; informs Task 8). The older plan `docs/superpowers/plans/2026-10-18-omniroute-idle-aware-update.md` stays preserved and unmodified; this plan supersedes it for execution because it targets the actual repository state at `65d10bf` (stub updater, no `pylib/omniroute_update*.py`).

## Global Constraints

- Defaults: `min_wait="${LLM_ENV_UPDATE_IDLE_SECONDS-300}"`, `max_deferred_wait="${LLM_ENV_UPDATE_MAX_WAIT_SECONDS-10800}"`. Positive integers ≤ 2147483647; empty or non-numeric is invalid; `max_deferred_wait >= min_wait`. Invalid → exit 1 before any registry or container operation. No force bypass flag.
- The wait deadline is monotonic and **exclusive** (`now >= deadline` defers), starts after staging, and activity resets quiet time, never the deadline.
- Exit codes: **0** unchanged or updated, **75** safe deferral (busy, unknown, lock contention, ineligible topology), **1** invalid input or operational error.
- Never: `podman rm --depend`, `compose down`, orphan cleanup, volume deletion, whole-stack stop/start, sibling recreation, `/api/shutdown` as an idle probe, automatic rollback, config/compose rewrite by the updater, paid inference probes, new runtime services (no sidecar/proxy/ingress guard).
- Do not promise zero-downtime or zero-drop. Observation is not atomic admission control; mutation after commit is non-transactional and must be reported honestly.
- Python entrypoints are `uv run llmenv.py <subcommand>`. After editing `.sh`: `make validate`. After editing `.py`: `make validate && make test`. Makefile bodies > 3 lines delegate to `.sh`. All output in English. Secrets (dashboard password, master key) never printed or hashed into output.
- Production OmniRoute is **not** replaced by any task or test in this plan. Real Podman proofs run only in a **separate rootless Podman backend** (own `CONTAINERS_STORAGE_CONF` graphroot/runroot under a temp dir, ownership asserted before every invocation and cleanup), because the container name `omniroute` is fixed and a compose project name cannot isolate it.
- No test-only injection seam reaches production CLIs: handlers take an injected `run` argument in-process; shell tests use stub executables on `PATH`; proofs supply profiles via a temp copy of the repo tree.
- Deferral reasons are the closed `Reason` enum (spec ORU-R1-006) everywhere, including stdout `deferred: <reason>`.
- **One backend environment for every invocation.** Every Podman, `podman-compose` (inventory, provider config, mutation), `skopeo` and `journalctl` child — from Python (`scrubbed_runner`, Task 1) and from the shell scripts (`backend_env_init` + `pm`, Task 6) — runs under the same allowlist: `env -i` plus `PATH HOME` and, when set, `XDG_RUNTIME_DIR CONTAINERS_STORAGE_CONF CONTAINERS_CONF CONTAINERS_REGISTRIES_CONF`. `preflight` records the backend (graphRoot/runRoot) in the snapshot and the worker re-verifies it with `assert_unchanged` (in `preflight --recheck`) under that same environment before the first mutation. `COMPOSE_FILE`/`COMPOSE_INSPECT_DIR` are exported by `tools/lib.sh`; the script copies them into local variables and `unset`s them before any CLI runs; any other `COMPOSE_*`/`PODMAN_COMPOSE_*` variable still defers (`Reason.input`). Test stubs therefore cannot receive control variables: they find their world file and call journal beside their own symlink (Task 6).
- Every external command in a sample, recheck or readiness wait shares the remaining budget through `with_deadline(run, deadline)` (Task 1), which also discards a command that succeeds at or after the deadline; no inventory/fingerprint command can outlive the budget.
- Test helper modules under `tests/` are imported by bare name (`from omniroute_fakes import ...`): `tests/` has no `__init__.py`, pytest's default import mode puts it on `sys.path`, and each module inserts the repository root itself (`sys.path.insert(0, str(Path(__file__).resolve().parents[1]))`) exactly like `tests/test_remote_setup.py`.
- Commits only when the user asks; this plan lists commit steps for the executor to run only after that approval.

## Review Focus

Inputs/conditions most likely to bite that no task's happy-path tests cover; each has a pinning test in the owning task.

1. Registry returns a **single manifest** (not an index) → must resolve the same fields as the index path (Task 1).
2. Index lists only attestation or non-matching platforms → error, never alternate-platform guess (Task 1).
3. Stopped or cross-project container with a native `--requires` edge to OmniRoute → defer before any mutation (Task 2).
4. Journal cursor vanished / boot id changed / empty seed → `unknown`, never idle (Task 3).
5. Activity arrives at second 299 of the quiet window and at the deadline second → quiet resets; deadline still exclusive (Task 4).
6. `LLM_ENV_UPDATE_IDLE_SECONDS=""` or `0` or `10800 < idle` → exit 1 with no external command run, including when the validator prints nothing (Tasks 4, 6).
7. Updater invoked twice concurrently → second exits 75 on lock, first unaffected (Task 6).
8. Migration run when remote-setup already has no dependency → no-op, remote-setup not recreated; deployed compose still carrying the edge → abort before mutation (Task 7).
9. Request arrives and completes between two zero-count samples, or health timestamp never advances → quiet resets / never starts (Task 4).
10. Dependent or sibling config appears after quiet is reached, before commit → exit 75, no mutation (Task 6).
11. A required activity source (pending map, call-log rows) is missing, `null` or mistyped → `schema` unknown, never a zero count (Task 3).
12. A persisted call-log row or an in-flight (memory) row disappears between samples without being explained → `continuity` unknown, never merely a quiet reset (Task 3).
13. Wall clock jumps between samples or the health timestamp goes backward → `clock` unknown even when each single bracket looks fine (Task 3).
14. Effective compose disagrees with config (image, digest pin, port, password, `REQUIRE_API_KEY`) while the running container matches → `ownership` before any registry access (Task 2).
15. The provider reads the written projection differently from the running container's evidence, or a bind source is relative → deferral, nothing executed (Task 6).
16. The profile lookup receives Podman's unprefixed image id → still selects the `sha256:`-keyed profile through one canonical form (Tasks 2, 3).

## File Map

| File | Responsibility |
| --- | --- |
| `pylib/omniroute_update.py` (create) | Immutable image resolution, scrubbed runner, deployment preflight, native-dependency inventory, projection + provider round trip, commit verification (pure functions over injected command runner) |
| `pylib/omniroute_activity.py` (create) | Sanitized observer state, collector, in-memory idle gate, env override validation |
| `pylib/omniroute_observer.py` (create) | Persistent observer supervisor (parent lease, reaping, gate verdict in `latest.json`) and canonical lock path |
| `pylib/omniroute_cli.py` (create, Task 1) | Every `omniroute-update` handler (`cmd_*`, the `guarded` exit-code mapper) and `register(sub)` |
| `llmenv.py` (modify, Task 1 only) | Calls `pylib.omniroute_cli.register(sub)`; later tasks add subcommands inside `register` |
| `scripts/update-omniroute.sh` (modify) | Lock, staging, verdict polling loop, hand-off to the commit worker, exit codes |
| `tools/lib.sh` (modify, Task 6) | `backend_env_init`, `acquire_update_lock` shared by the updater, worker and migration scripts |
| `scripts/migrate-remote-setup.sh` (create) | One-time remote-setup-only recreation |
| `pylib/compose.py` (modify) | Drop remote-setup `depends_on`; keep everything else |
| `pylib/remote_setup.py` (modify) | Bounded `/config` unavailable behavior (HTTP 503 + `Retry-After`) |
| `tests/test_omniroute_update.py`, `tests/test_omniroute_activity.py`, `tests/test_omniroute_observer.py` (create) | Unit tests with fakes |
| `tests/proof_backend.py`, `tests/fixtures/proof/Containerfile`, `tests/fixtures/fake_inference.py`, `tests/test_omniroute_update_proof.py`, `tests/test_omniroute_characterize.py` (create, Task 8) | Opt-in isolated-backend proofs and characterization |
| `tests/test_update_omniroute_script.py` (create, Task 6) | Real-script tests (updater and migration) against the scripted stub world |
| `tests/test_shell.py` (modify, Task 6: delete only), `tests/test_compose.py`, `tests/test_remote_setup.py`, `tests/test_cli.py` (modify) | Retire the stub-updater tests; compose/remote-setup/CLI tests |
| `pylib/omniroute_activity_profiles.json` (create, Task 3) | Reviewed activity profiles; empty until a human reviews a characterization |
| `tests/fixtures/fake_omniroute.py` (create, Task 3) | Stdlib fake OmniRoute HTTP API (`--host` defaults to loopback; the proof image binds `0.0.0.0`) used by Tasks 3, 6 and 8 |
| `tests/omniroute_fakes.py`, `tests/omniroute_world.py`, `tests/fixtures/stub_tool.py` (create, Tasks 2/3/6) | Shared inspect/backend/profile fixtures, scripted-stub shell-test world, generic Podman/skopeo/provider/journalctl stub (world file beside its symlink, no environment) |
| `scripts/omniroute-commit-worker.sh` (create, Task 6) | Detached bounded commit worker that keeps the update lock through the mutation sequence |
| `Makefile`, `scripts/help.sh`, `README.md`, `QUICK_START.md`, `AGENTS.md` (modify) | Replace "safety deferral" wording; document limits |

---

### Task 1: Immutable image resolution

**Files:**
- Create: `pylib/omniroute_update.py`, `pylib/omniroute_cli.py`
- Create: `tests/test_omniroute_update.py`
- Modify: `llmenv.py` (call `omniroute_cli.register(sub)` once), `tests/test_cli.py`

**Interfaces:**
- Produces: `ImageIdentity` dataclass: `repository, source_digest, manifest_digest, immutable_ref, config_id, os, architecture, variant, kind` (`"single"|"index"`).
- Produces: `resolve_image(reference: str, platform: Platform, run: Runner) -> ImageIdentity` where `Runner = Callable[..., bytes]` (called `run(argv, timeout=None)`) raises `UpdateError` on non-zero exit or timeout; `Platform = tuple[str, str, str]` (os, arch, variant).
- Produces: `class UpdateError(Exception)`, `class Reason(str, Enum)` (closed spec set), `class Deferral(Exception)` with `.reason: Reason`, and `verify_staged(identity, image_inspect: dict) -> None`.
- Produces: `parse_reference(ref: str) -> tuple[str, str, str]` = (repository, tag, digest) with only the documented Docker Hub aliases normalized (`name` -> `docker.io/library/name`, `user/name` -> `docker.io/user/name`) and the default tag `latest` applied **only when no digest is present**; a digest pin is preserved, never discarded. This is the single reference parser (Task 2's comparisons reuse it).
- Produces: `canon_image_id(value: str) -> str` — the one canonical image-config identity, `sha256:<64 lowercase hex>`; accepts Podman's bare hex or the prefixed form and raises `UpdateError` on anything else. Producers (`Deployment.image_id`, `verify_staged`, `verify_commit`) and consumers (`load_profile`, the script's regex) all use this form.
- Produces runner helpers: `default_runner(argv, timeout=None, env=None) -> bytes`, `backend_env(env) -> dict[str, str]` (the allowlist `PATH HOME XDG_RUNTIME_DIR CONTAINERS_STORAGE_CONF CONTAINERS_CONF CONTAINERS_REGISTRIES_CONF`, only non-empty values; `UpdateError` when `PATH`/`HOME` are missing), `scrubbed_runner(env) -> Runner` (every command runs with exactly `backend_env(env)`), `with_deadline(run, deadline, clock=time.monotonic) -> Runner`, `write_private(path, text)`.
- Produces in `pylib/omniroute_cli.py`: `guarded(fn)` — decorator mapping `Deferral` to stdout `deferred: <reason>` + return 75 and `UpdateError` to a sanitized `error: ...` on stderr + return 1 — and `register(sub)`, which adds the `omniroute-update` subparser group (later tasks add their subcommands inside `register`; every subcommand `--config` uses `default=argparse.SUPPRESS`, the pattern of the existing `budget`/`omniroute` subparsers, so the top-level `--config` is not shadowed).
- Consumes (later tasks): the running container's image config id from `podman inspect`.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_omniroute_update.py
import hashlib, json, sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))      # same first lines as tests/test_remote_setup.py

from pylib.omniroute_update import (resolve_image, verify_staged, parse_reference, canon_image_id,
                                    UpdateError, Deferral, Reason)

def sha(b: bytes) -> str:
    return "sha256:" + hashlib.sha256(b).hexdigest()

CONFIG = "sha256:" + "c" * 64
PLAT = ("linux", "amd64", "")

def make_runner(raw_by_ref: dict[str, bytes]):
    def run(argv, timeout=None):
        # argv: ["skopeo", "inspect", "--raw", "docker://REF"]
        assert argv[:3] == ["skopeo", "inspect", "--raw"]
        return raw_by_ref[argv[3].removeprefix("docker://")]
    return run

def single_manifest() -> bytes:
    return json.dumps({
        "schemaVersion": 2,
        "mediaType": "application/vnd.oci.image.manifest.v1+json",
        "config": {"digest": CONFIG, "mediaType": "application/vnd.oci.image.config.v1+json", "size": 1},
        "layers": [],
    }).encode()

def test_single_manifest_resolves_to_same_fields_as_index():
    m = single_manifest()
    ident = resolve_image("docker.io/x/y:latest", PLAT, make_runner({"docker.io/x/y:latest": m}))
    assert ident.kind == "single"
    assert ident.manifest_digest == sha(m) == ident.source_digest
    assert ident.immutable_ref == f"docker.io/x/y@{sha(m)}"
    assert ident.config_id == CONFIG

def test_index_selects_matching_platform_and_ignores_attestations():
    m = single_manifest()
    index = json.dumps({
        "schemaVersion": 2,
        "mediaType": "application/vnd.oci.image.index.v1+json",
        "manifests": [
            {"digest": sha(b"att"), "mediaType": "application/vnd.oci.image.manifest.v1+json",
             "platform": {"os": "unknown", "architecture": "unknown"}},
            {"digest": sha(m), "mediaType": "application/vnd.oci.image.manifest.v1+json",
             "platform": {"os": "linux", "architecture": "amd64"}},
        ],
    }).encode()
    ident = resolve_image("docker.io/x/y:latest", PLAT, make_runner({
        "docker.io/x/y:latest": index, f"docker.io/x/y@{sha(m)}": m}))
    assert ident.kind == "index"
    assert ident.source_digest == sha(index) and ident.manifest_digest == sha(m)
    assert ident.config_id == CONFIG

def test_index_without_matching_platform_is_an_error():
    index = json.dumps({"schemaVersion": 2,
        "mediaType": "application/vnd.oci.image.index.v1+json",
        "manifests": [{"digest": sha(b"a"), "mediaType": "application/vnd.oci.image.manifest.v1+json",
                       "platform": {"os": "linux", "architecture": "arm64"}}]}).encode()
    with pytest.raises(UpdateError, match="no runnable manifest"):
        resolve_image("docker.io/x/y:latest", PLAT, make_runner({"docker.io/x/y:latest": index}))

def test_malformed_digests_are_rejected():
    for bad in ("sha256:abc", "sha256:" + "G" * 64, "md5:" + "c" * 32):
        doc = json.loads(single_manifest())
        doc["config"]["digest"] = bad
        with pytest.raises(UpdateError):
            resolve_image("docker.io/x/y:latest", PLAT,
                          make_runner({"docker.io/x/y:latest": json.dumps(doc).encode()}))
    m = single_manifest()
    index = json.dumps({"schemaVersion": 2,
        "mediaType": "application/vnd.oci.image.index.v1+json",
        "manifests": [{"digest": "sha256:short", "mediaType": "application/vnd.oci.image.manifest.v1+json",
                       "platform": {"os": "linux", "architecture": "amd64"}}]}).encode()
    with pytest.raises(UpdateError, match="descriptor digest"):
        resolve_image("docker.io/x/y:latest", PLAT, make_runner({"docker.io/x/y:latest": index}))

def test_verify_staged_checks_config_id_and_platform():
    ident = resolve_image("docker.io/x/y:latest", PLAT,
                          make_runner({"docker.io/x/y:latest": single_manifest()}))
    good = {"Id": CONFIG.removeprefix("sha256:"), "Os": "linux", "Architecture": "amd64", "Variant": ""}
    verify_staged(ident, good)                                     # no raise
    with pytest.raises(UpdateError, match="config id"):
        verify_staged(ident, {**good, "Id": "d" * 64})
    with pytest.raises(UpdateError, match="platform"):
        verify_staged(ident, {**good, "Architecture": "arm64"})

def test_schema1_and_missing_config_are_rejected():
    bad = json.dumps({"schemaVersion": 1}).encode()
    with pytest.raises(UpdateError):
        resolve_image("docker.io/x/y:latest", PLAT, make_runner({"docker.io/x/y:latest": bad}))
    nocfg = json.dumps({"schemaVersion": 2,
        "mediaType": "application/vnd.oci.image.manifest.v1+json"}).encode()
    with pytest.raises(UpdateError):
        resolve_image("docker.io/x/y:latest", PLAT, make_runner({"docker.io/x/y:latest": nocfg}))

def test_parse_reference_normalizes_aliases_and_preserves_digest_pins():
    d = "sha256:" + "a" * 64
    assert parse_reference("diegosouzapw/omniroute") == ("docker.io/diegosouzapw/omniroute", "latest", "")
    assert parse_reference("redis") == ("docker.io/library/redis", "latest", "")
    assert parse_reference("127.0.0.1:5000/a/b:t") == ("127.0.0.1:5000/a/b", "t", "")
    assert parse_reference("localhost/x") == ("localhost/x", "latest", "")
    assert parse_reference(f"docker.io/x/y@{d}") == ("docker.io/x/y", "", d)           # no default tag next to a digest
    assert parse_reference(f"x/y:t@{d}") == ("docker.io/x/y", "t", d)
    assert parse_reference(f"x/y@{d}") != parse_reference("x/y@sha256:" + "b" * 64)    # different pins never compare equal

def test_canon_image_id_accepts_bare_and_prefixed_hex_only():
    assert canon_image_id("c" * 64) == canon_image_id("sha256:" + "c" * 64) == CONFIG
    for bad in ("", "c" * 63, "sha256:" + "G" * 64, "md5:" + "c" * 32, "C" * 64):
        with pytest.raises(UpdateError):
            canon_image_id(bad)

def test_pinned_index_reference_defers():
    m = single_manifest()
    index = json.dumps({"schemaVersion": 2, "mediaType": "application/vnd.oci.image.index.v1+json",
        "manifests": [{"digest": sha(m), "mediaType": "application/vnd.oci.image.manifest.v1+json",
                       "platform": {"os": "linux", "architecture": "amd64"}}]}).encode()
    ref = f"docker.io/x/y@{sha(index)}"
    with pytest.raises(Deferral) as exc:
        resolve_image(ref, PLAT, make_runner({ref: index, f"docker.io/x/y@{sha(m)}": m}))
    assert exc.value.reason is Reason.input

def test_pinned_single_manifest_resolves():
    m = single_manifest()
    ref = f"docker.io/x/y@{sha(m)}"
    ident = resolve_image(ref, PLAT, make_runner({ref: m}))
    assert ident.manifest_digest == sha(m) and ident.kind == "single"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_omniroute_update.py -v`
Expected: FAIL with `ModuleNotFoundError: pylib.omniroute_update`.

- [ ] **Step 3: Implement** (everything the Step 1 tests import is defined here; the runner helpers are used from Task 2 onward and tested in Step 5)

```python
# pylib/omniroute_update.py
"""OmniRoute-only updater: immutable image resolution and deployment preflight."""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum

Runner = Callable[..., bytes]  # run(argv, timeout=None) -> stdout bytes
Platform = tuple[str, str, str]

_MANIFEST_TYPES = {
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json",
}
_INDEX_TYPES = {
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
}


class UpdateError(Exception):
    """Operational error (exit 1); deferrals use Deferral instead."""


class Reason(str, Enum):
    """Closed deferral/sample reason set (spec ORU-R1-006); stdout prints only these values."""
    ready = "ready"
    busy = "busy"
    capability = "capability"
    coverage = "coverage"
    logging = "logging"
    feed = "feed"
    overflow = "overflow"
    auth = "auth"
    schema = "schema"
    age = "age"
    clock = "clock"
    continuity = "continuity"
    deadline = "deadline"
    ownership = "ownership"
    lock = "lock"
    input = "input"
    io = "io"


class Deferral(Exception):
    """Safe, expected refusal to proceed (exit 75). `detail` is sanitized, never printed to stdout."""

    def __init__(self, reason: Reason, detail: str = ""):
        super().__init__(f"{reason.value}: {detail}" if detail else reason.value)
        self.reason, self.detail = reason, detail


@dataclass(frozen=True)
class ImageIdentity:
    repository: str
    source_digest: str
    manifest_digest: str
    immutable_ref: str
    config_id: str
    os: str
    architecture: str
    variant: str
    kind: str


_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")
_IMAGE_ID_RE = re.compile(r"(?:sha256:)?([0-9a-f]{64})")


def _valid_digest(value: object) -> bool:
    return isinstance(value, str) and _DIGEST_RE.fullmatch(value) is not None


def canon_image_id(value: str) -> str:
    """The one canonical image-config identity: `sha256:<64 lowercase hex>` (Podman reports the bare hex)."""
    match = _IMAGE_ID_RE.fullmatch(value) if isinstance(value, str) else None
    if match is None:
        raise UpdateError("malformed image id")
    return "sha256:" + match.group(1)


def _digest(raw: bytes) -> str:
    # Deliberate in-Python equivalent of `skopeo manifest-digest FILE` (spec ORU-R1-003 step 1):
    # sha256 over the exact raw bytes skopeo returned; never re-serialize JSON, never strip bytes.
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def parse_reference(ref: str) -> tuple[str, str, str]:
    """(repository, tag, digest). Only the documented Docker Hub aliases are normalized; a digest pin is kept."""
    name, _, digest = ref.partition("@")
    tag = ""
    if ":" in name.rsplit("/", 1)[-1]:
        name, tag = name.rsplit(":", 1)
    elif not digest:
        tag = "latest"
    head = name.split("/")[0]
    if "/" not in name:
        name = "docker.io/library/" + name
    elif "." not in head and ":" not in head and head != "localhost":
        name = "docker.io/" + name
    return name, tag, digest


def _fetch(ref: str, run: Runner) -> tuple[bytes, dict]:
    raw = run(["skopeo", "inspect", "--raw", f"docker://{ref}"])
    try:
        return raw, json.loads(raw)
    except json.JSONDecodeError as exc:
        raise UpdateError(f"manifest for {ref} is not JSON") from exc


def _config_id(doc: dict, ref: str) -> str:
    if doc.get("schemaVersion") != 2 or doc.get("mediaType") not in _MANIFEST_TYPES:
        raise UpdateError(f"unsupported manifest type for {ref}")
    config = doc.get("config")
    digest = config.get("digest") if isinstance(config, dict) else None
    if not _valid_digest(digest):
        raise UpdateError(f"manifest for {ref} has no config digest")
    return digest


def resolve_image(reference: str, platform: Platform, run: Runner) -> ImageIdentity:
    os_, arch, variant = platform
    repo = parse_reference(reference)[0]
    raw, doc = _fetch(reference, run)
    source = _digest(raw)
    if "@sha256:" in reference and doc.get("mediaType") in _INDEX_TYPES:
        raise Deferral(Reason.input, "pinned reference selects an index, not a single manifest")
    if doc.get("mediaType") in _INDEX_TYPES:
        matches = [
            m for m in doc.get("manifests", [])
            if m.get("mediaType") in _MANIFEST_TYPES
            and (m.get("platform") or {}).get("os") == os_
            and (m.get("platform") or {}).get("architecture") == arch
            and (m.get("platform") or {}).get("variant", "") == variant
        ]
        if len(matches) != 1:
            raise UpdateError(
                f"no runnable manifest (or ambiguous: {len(matches)}) for {os_}/{arch}/{variant}"
            )
        manifest_digest = matches[0].get("digest")
        if not _valid_digest(manifest_digest):
            raise UpdateError("selected descriptor digest is malformed")
        sel_raw, sel_doc = _fetch(f"{repo}@{manifest_digest}", run)
        if _digest(sel_raw) != manifest_digest:
            raise UpdateError("selected manifest bytes do not match the index descriptor")
        return ImageIdentity(repo, source, manifest_digest, f"{repo}@{manifest_digest}",
                             _config_id(sel_doc, manifest_digest), os_, arch, variant, "index")
    return ImageIdentity(repo, source, source, f"{repo}@{source}",
                         _config_id(doc, reference), os_, arch, variant, "single")


def verify_staged(identity: ImageIdentity, image_inspect: dict) -> None:
    """Check the locally staged image (`podman image inspect` JSON) against the resolved identity."""
    if canon_image_id(image_inspect.get("Id", "")) != identity.config_id:
        raise UpdateError("staged image config id does not match the resolved identity")
    got = (image_inspect.get("Os"), image_inspect.get("Architecture"), image_inspect.get("Variant", ""))
    if got != (identity.os, identity.architecture, identity.variant):
        raise UpdateError("staged image platform does not match the resolved identity")


_BACKEND_VARS = ("PATH", "HOME", "XDG_RUNTIME_DIR",
                 "CONTAINERS_STORAGE_CONF", "CONTAINERS_CONF", "CONTAINERS_REGISTRIES_CONF")


def backend_env(env: Mapping[str, str]) -> dict[str, str]:
    """The one environment every Podman/provider/skopeo/journalctl child runs under (matches tools/lib.sh)."""
    out = {name: env[name] for name in _BACKEND_VARS if env.get(name)}
    if "PATH" not in out or "HOME" not in out:
        raise UpdateError("PATH and HOME must be set")
    return out


def default_runner(argv: list[str], timeout: float | None = None, env: Mapping[str, str] | None = None) -> bytes:
    """Production Runner: one bounded subprocess; stderr is never echoed (it may carry secrets)."""
    try:
        return subprocess.run(argv, timeout=60 if timeout is None else timeout,
                              check=True, capture_output=True, env=env).stdout
    except (subprocess.SubprocessError, OSError) as exc:
        raise UpdateError(f"{argv[0]} failed: {type(exc).__name__}") from exc


def scrubbed_runner(env: Mapping[str, str]) -> Runner:
    clean = backend_env(env)

    def run(argv: list[str], timeout: float | None = None) -> bytes:
        return default_runner(argv, timeout, env=clean)
    return run


def with_deadline(run: Runner, deadline: float, clock: Callable[[], float] = time.monotonic) -> Runner:
    """Wrap `run` so every command shares one budget; a result completed at/after the deadline is discarded."""
    def bounded(argv: list[str], timeout: float | None = None) -> bytes:
        remaining = deadline - clock()
        if remaining <= 0:
            raise UpdateError("deadline exceeded before command start")
        out = run(argv, timeout=min(remaining, 60 if timeout is None else timeout))
        if clock() >= deadline:
            raise UpdateError("deadline exceeded; result discarded")
        return out
    return bounded


def write_private(path: str, text: str) -> None:
    """Atomically write a 0600 file (callers place it inside a 0700 directory)."""
    tmp = f"{path}.tmp{os.getpid()}"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(text)
    os.replace(tmp, path)
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_omniroute_update.py -v` → PASS.

- [ ] **Step 5: `pylib/omniroute_cli.py`, `llmenv.py` registration, and the runner-helper and CLI tests.** Create the handler module (all later tasks add their handlers here and their subparsers in `register`):

```python
# pylib/omniroute_cli.py
"""Handlers for `llmenv.py omniroute-update <subcommand>`. Handlers take the injected `run` in-process (tests pass a
fake); there is no environment variable, pytest-detection or module-attribute seam that changes production behavior."""
from __future__ import annotations

import argparse
import functools
import json
import os
import sys
from dataclasses import asdict

from pylib.omniroute_update import Deferral, UpdateError, resolve_image, scrubbed_runner, write_private


def guarded(fn):
    """Exit-code contract: Deferral -> `deferred: <reason>` on stdout + 75; UpdateError -> sanitized stderr + 1."""
    @functools.wraps(fn)
    def wrapper(args, *a, **kw):
        try:
            return fn(args, *a, **kw)
        except Deferral as exc:
            print(f"deferred: {exc.reason.value}")
            return 75
        except UpdateError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
    return wrapper


@guarded
def cmd_resolve(args, run=None, env=None) -> int:
    run = run or scrubbed_runner(os.environ if env is None else env)
    identity = resolve_image(args.reference, (args.os, args.arch, args.variant), run)
    write_private(args.json, json.dumps(asdict(identity)))
    for key, value in asdict(identity).items():
        print(f"{key}={value}")
    return 0


def register(sub) -> None:
    group = sub.add_parser("omniroute-update")
    usub = group.add_subparsers(dest="update_command", required=True)
    resolve = usub.add_parser("resolve")
    resolve.add_argument("--reference", required=True)
    resolve.add_argument("--os", required=True)
    resolve.add_argument("--arch", required=True)
    resolve.add_argument("--variant", default="")
    resolve.add_argument("--json", required=True)
    resolve.set_defaults(func=cmd_resolve)
```

  In `llmenv.py`, import `register as register_omniroute_update` from `pylib.omniroute_cli` and call it in `build_parser()` right after the existing subparsers are created (`register_omniroute_update(sub)`), before `return parser`. Producer: `cmd_resolve` runs `resolve_image`, writes `asdict(identity)` as JSON to `--json FILE` through `write_private` and prints the same fields as `key=value` lines. Consumers: `verify-staged --resolved FILE` (Task 6) and the script (`jq -r .config_id`, `.immutable_ref`). Exit 0 on success; **75 and `deferred: <reason>`** on `Deferral` (the pinned-index test above); 1 with a sanitized message on `UpdateError`.

  **Injection rule for every `omniroute-update` subcommand (Tasks 1-7):** handlers are plain functions `cmd_<name>(args, run=None, env=None, ...)` decorated with `guarded`; unit tests call them in-process with a fake `run`; shell tests put stub `podman`/`skopeo`/`journalctl` executables first on `PATH` and run the real CLI. `run=None` means `scrubbed_runner(os.environ)`.

  Add these tests to `tests/test_omniroute_update.py` (it owns the manifest helpers) and a registration test to `tests/test_cli.py`:

```python
import argparse, time
from pylib.omniroute_cli import cmd_resolve
from pylib.omniroute_update import backend_env, default_runner, scrubbed_runner, with_deadline

def test_with_deadline_refuses_expired_budget():
    run = with_deadline(lambda argv, timeout=None: b"x", time.monotonic() - 1)
    with pytest.raises(UpdateError, match="deadline"):
        run(["true"])

def test_with_deadline_rejects_results_completed_after_the_deadline():
    now = {"t": 0.0}
    def slow(argv, timeout=None):
        now["t"] = 10.0                                     # the command "ran" past the deadline and then succeeded
        return b"ok"
    run = with_deadline(slow, 5.0, clock=lambda: now["t"])
    with pytest.raises(UpdateError, match="deadline"):
        run(["x"])

def test_default_runner_converts_failure_and_timeout():
    with pytest.raises(UpdateError):
        default_runner(["false"])
    with pytest.raises(UpdateError):
        default_runner(["sleep", "5"], timeout=0.1)

def test_backend_env_keeps_only_the_allowlist():
    env = {"PATH": "/usr/bin:/bin", "HOME": "/h", "XDG_RUNTIME_DIR": "/run/u", "CONTAINERS_CONF": "/c", "CONTAINERS_STORAGE_CONF": "",
           "HTTP_PROXY": "p", "CONTAINER_HOST": "ssh://x", "COMPOSE_FILE": "f"}
    assert backend_env(env) == {"PATH": "/usr/bin:/bin", "HOME": "/h", "XDG_RUNTIME_DIR": "/run/u", "CONTAINERS_CONF": "/c"}

def test_backend_env_requires_path_and_home():
    with pytest.raises(UpdateError):
        backend_env({"PATH": "/bin"})

def test_scrubbed_runner_children_see_only_the_allowlist():
    run = scrubbed_runner({"PATH": "/usr/bin:/bin", "HOME": "/h", "SECRET": "x"})
    out = run(["env"]).decode()
    assert "HOME=/h" in out and "SECRET" not in out

def test_omniroute_update_resolve_failure_is_sanitized(tmp_path, capsys):
    def failing(argv, timeout=None):
        raise UpdateError("skopeo failed: CalledProcessError")
    args = argparse.Namespace(reference="docker.io/x/y:latest", os="linux", arch="amd64",
                              variant="", json=str(tmp_path / "r.json"))
    assert cmd_resolve(args, run=failing) == 1
    assert "skopeo failed" in capsys.readouterr().err
    assert not (tmp_path / "r.json").exists()

def test_omniroute_update_resolve_writes_private_json(tmp_path, capsys):
    m = single_manifest()
    args = argparse.Namespace(reference="docker.io/x/y:latest", os="linux", arch="amd64",
                              variant="", json=str(tmp_path / "r.json"))
    assert cmd_resolve(args, run=make_runner({"docker.io/x/y:latest": m})) == 0
    assert json.loads((tmp_path / "r.json").read_text())["config_id"] == CONFIG
    assert (tmp_path / "r.json").stat().st_mode & 0o777 == 0o600
    assert f"config_id={CONFIG}" in capsys.readouterr().out

def test_omniroute_update_resolve_defers_on_a_pinned_index(tmp_path, capsys):
    m = single_manifest()
    index = json.dumps({"schemaVersion": 2, "mediaType": "application/vnd.oci.image.index.v1+json",
        "manifests": [{"digest": sha(m), "mediaType": "application/vnd.oci.image.manifest.v1+json",
                       "platform": {"os": "linux", "architecture": "amd64"}}]}).encode()
    ref = f"docker.io/x/y@{sha(index)}"
    args = argparse.Namespace(reference=ref, os="linux", arch="amd64", variant="", json=str(tmp_path / "r.json"))
    assert cmd_resolve(args, run=make_runner({ref: index})) == 75
    assert capsys.readouterr().out == "deferred: input\n"
```

```python
# tests/test_cli.py
def test_omniroute_update_resolve_is_registered():
    import llmenv
    args = llmenv.build_parser().parse_args(["omniroute-update", "resolve", "--reference", "x/y:latest",
                                              "--os", "linux", "--arch", "amd64", "--json", "r.json"])
    assert args.func.__name__ == "cmd_resolve" and args.variant == ""
```
- [ ] **Step 6: `make validate && make test`; commit (`feat(update): resolve immutable OmniRoute image identity`) only if the user approved commits.**

---

### Task 2: Backend inventory, deployment fingerprint and preflight

**Files:**
- Create: `tests/omniroute_fakes.py` (shared fixtures; Tasks 3, 5 and 6 append to or import it)
- Modify: `pylib/omniroute_update.py`, `tests/test_omniroute_update.py`
- Modify: `pylib/omniroute_cli.py` (add `omniroute-update preflight`)
- Modify: `tests/test_cli.py`

**Interfaces:**
- Consumes: `Runner`, `UpdateError`, `Deferral`, `Reason`, `ImageIdentity`, `parse_reference`, `canon_image_id`, `with_deadline`, `scrubbed_runner`, `write_private`, `guarded` (Task 1).
- Produces: `Snapshot(backend: dict, containers: dict[str, str], deps: frozenset[tuple[str, str]], docs: dict[str, dict] = field(default_factory=dict, compare=False, repr=False))` plus `to_json() -> str` / `Snapshot.from_json(text) -> Snapshot` (storage format: `{"backend": {...}, "containers": {id: hash}, "deps": [[dependent, target], ...]}`; `docs` — the raw inspect documents, which may contain secrets in `Config.Env` — is never serialized).
- Produces: `take_snapshot(run, env=os.environ) -> Snapshot` (the remote-connection check reads the **injected** `env`, never a hidden global), `incoming(snap, target_id) -> list[str]`, `assert_unchanged(before, after) -> None` (`Deferral(Reason.ownership)` on any difference in backend, ID set, per-container hash or dependency set). The per-container hash covers the identity, image, dependencies, mounts, the whole `HostConfig`, the whole `Config` object (labels, environment, entrypoint, command, working directory, user, health configuration, ...), network attachments and `StartedAt`; it is stored only in the private `snapshot.json`.
- Produces: `fingerprint_parts(config_path, compose_path, provider, effective, env, snap, run) -> dict[str, bytes]` and `fingerprint(parts) -> str` (`sha256:` over `name\0sha256(value)\n` lines sorted by name; secrets stay in memory and never appear in output or errors).
- Produces: `Deployment(container_id, container_name, project, image_id, image_ref, started_at, port, stop_seconds, pinned, os, architecture, variant, provider, network_ids, volume_names, sibling_ids)` (frozen dataclass; `network_ids`, `volume_names`, `sibling_ids` are sorted tuples; **`image_id` is `canon_image_id(Config id)` = `sha256:<64 hex>`**, the single form used by `load_profile`, `verify_commit` and the script) and `preflight(cfg, config_path, compose_path, env, run, provider=None, service="omniroute") -> tuple[Deployment, Snapshot, str, dict]` returning (deployment, baseline snapshot, baseline fingerprint, effective compose object). For `omniroute`, agreement is established **before any registry access** between three views: the configuration (`omniroute.image/port/initial_password`), the **effective compose service** (`image` must equal the configured reference under `parse_reference` — repository, tag *and digest pin* all compared; the published port set must be `{port}`; `INITIAL_PASSWORD` equal to the configured password in plain or `$$`-doubled form; `REQUIRE_API_KEY == "true"`) and the running container (`same_image`, port binding, `INITIAL_PASSWORD`, `REQUIRE_API_KEY`). Any disagreement is `Deferral(Reason.ownership)` (stale rendered configuration; nothing is regenerated).
- Produces: `same_image(configured: str, running: str) -> bool` — repository must match; a configured digest pin must equal the running digest exactly; a tag-form configuration matches a running tag-form reference with the same tag **or** a digest-form reference of the same repository (a container this updater created from the immutable reference legitimately reports `repo@sha256:...` while the configured tag moves).
- Produces: `recheck(out_dir, config_path, compose_path, env, run) -> None` (loads the files below, retakes the snapshot, requires `assert_unchanged`, no incoming dependency on the target, and an identical recomputed fingerprint; any difference is `Deferral(Reason.ownership)`; **any `UpdateError` from a command, including a deadline-discarded result, becomes `Deferral(Reason.io)`**, because a recheck that cannot complete is not proof of safety).
- **Preflight artifact directory** (producer `preflight`, `--out-dir DIR`, which the script created with `mktemp -d`; the CLI refuses a directory that is a symlink, not owned by the user or not mode 0700 with `Deferral(Reason.lock)`). Files, all 0600 via `write_private`:
  - `snapshot.json` — `Snapshot.to_json()`; consumers: `preflight --recheck`, `verify-commit` (Task 6).
  - `fingerprint.txt` — one `sha256:...` line; consumer: `preflight --recheck`.
  - `effective.json` — the parsed provider `config` object (contains secrets, never printed); consumer: `projection` (Task 6).
  - `target.json` — the target's raw `podman container inspect` JSON list (contains secrets, never printed); consumers: `projection`, `verify-commit`.
  - `deployment.json` — `asdict(Deployment)`; consumers: `preflight --recheck`.
  - stdout: `key=value` lines of the Deployment's non-secret scalar fields (`container_id image_id image_ref project stop_seconds pinned os architecture variant provider started_at port`; `pinned` is `1` or `0`); consumer: the script's allowlist `read` loop.
- Produces CLI `omniroute-update preflight --config FILE --compose FILE --out-dir DIR [--recheck [--budget SECONDS]] [--service omniroute]` (`--service` is exercised by Task 7). `cmd_preflight(args, run=None, env=None, which=shutil.which)` lives in `pylib/omniroute_cli.py`: `run` defaults to `scrubbed_runner(env)`, `env` to `os.environ`, and the provider is `which("podman-compose")` (an injected argument, so tests never depend on the host). Normal mode writes the files above; `--recheck` reads them, writes nothing and, with `--budget S` (default 60), runs every command through `with_deadline(run, time.monotonic() + S)`. Exit 0 / 75 `deferred: <reason>` on `Deferral` / 1 sanitized on `UpdateError`.
- Reason mapping (closed enum, Task 1): incoming dependency, incomplete/changed inventory, fingerprint drift, ownership/backend/config-compose disagreement, unhealthy or missing target, any agreement-check mismatch -> `Reason.ownership`; unknown inspect schema -> `Reason.schema`; unsupported compose input, **any** `COMPOSE_*`/`PODMAN_COMPOSE_*` variable still set, a remote Podman connection variable (`CONTAINER_HOST`/`CONTAINER_CONNECTION`) -> `Reason.input`; command failure/timeout -> `Reason.io`. One reason per input class; the tests below use exactly this table.

- [ ] **Step 1: Verify the Podman shapes this task relies on (research before code).** Against the production backend, read-only:

```bash
podman info --format json | jq '{rootless: .host.security.rootless, remote: .host.serviceIsRemote, graph: .store.graphRoot, run: .store.runRoot}'
podman container inspect omniroute --format json | jq '.[0] | {Id, Name, Dependencies, labels: .Config.Labels, nets: .NetworkSettings.Networks | map_values(.NetworkID), state: .State | {Status, StartedAt, Health}}'
```

Expected: `rootless: true`, `remote: false`, `Dependencies` a top-level array of 64-hex strings, and the compose project/service label keys. If the label keys differ from the constants `LABEL_PROJECT = "io.podman.compose.project"` and `LABEL_SERVICE = "io.podman.compose.service"` used below, change only those two constants. If `serviceIsRemote` is absent, stop and ask the user (do not guess a remote-detection field).

- [ ] **Step 2: Failing tests.** First create `tests/omniroute_fakes.py` (shared by Tasks 2-8; `tests/` has no `__init__.py`, so tests import it by bare name, and it inserts the repository root into `sys.path` itself like every existing test module). Tasks 3 and 5 append to it (`profile_entry` and friends, `mk_sample`).

```python
# tests/omniroute_fakes.py
import json, socket, sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pylib.omniroute_update import DEFAULT_IMAGE

OMNI, RS, SIB, NEW = "a" * 64, "b" * 64, "c" * 64, "9" * 64
NET, IMG = "e" * 64, "d" * 64
PROJECT, PASSWORD = "llm-env", "s3cret-pass"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def inspect_doc(cid, *, name=None, deps=(), state="running", project=PROJECT, port=20128,
                health="healthy", net=NET, image=IMG, image_name="docker.io/diegosouzapw/omniroute:latest",
                env=()):
    name = name or f"svc-{cid[:4]}"
    return {"Id": cid, "Name": name, "Image": image, "ImageName": image_name,
            "Dependencies": list(deps), "Mounts": [{"Type": "volume", "Name": "omniroute-data"}],
            "HostConfig": {"PortBindings": {f"{port}/tcp": [{"HostPort": str(port)}]},
                           "LogConfig": {"Type": "journald"}},
            "Config": {"Labels": {"io.podman.compose.project": project, "io.podman.compose.service": name},
                       "Env": list(env), "Entrypoint": ["node", "server.js"], "Cmd": ["--serve"], "WorkingDir": "/app",
                       "Healthcheck": {"Test": ["CMD", "true"], "Interval": 5000000000}},
            "NetworkSettings": {"Networks": {f"{project}_default": {"NetworkID": net}}},
            "State": {"Status": state, "Running": state == "running", "StartedAt": "2026-10-22T00:00:00Z",
                      "Health": {"Status": health}}}


def omni(**kw):
    kw.setdefault("env", [f"INITIAL_PASSWORD={PASSWORD}", "REQUIRE_API_KEY=true", "PORT=20128", "NODE_ENV=production"])
    return inspect_doc(OMNI, name="omniroute", **kw)


def fake_backend(containers, *, rootless=True, remote=False, extra=None):
    """Scripted Runner. `containers`: id -> inspect doc, or an Exception instance to raise on inspect.
    `extra`: list of (argv-prefix tuple, bytes) answered after the podman basics."""
    def run(argv, timeout=None):
        if argv[:2] == ["podman", "info"]:
            return json.dumps({"store": {"graphRoot": "/g", "runRoot": "/r"},
                               "host": {"security": {"rootless": rootless}, "serviceIsRemote": remote}}).encode()
        if argv[:2] == ["podman", "ps"]:
            return json.dumps([{"Id": cid} for cid in containers]).encode()
        if argv[:3] == ["podman", "container", "inspect"]:
            doc = containers[argv[-1]]
            if isinstance(doc, Exception):
                raise doc
            return json.dumps([doc]).encode()
        for prefix, out in (extra or []):
            if tuple(argv[:len(prefix)]) == prefix:
                return out
        raise AssertionError(f"unscripted command {argv}")
    return run


def with_dependent():       # RS now depends on OMNI
    return fake_backend({OMNI: omni(), RS: inspect_doc(RS, name="remote-setup", deps=[OMNI])})


def without_sibling():
    return fake_backend({OMNI: omni()})


def with_port_changed(cid):
    return fake_backend({OMNI: omni(), cid: inspect_doc(cid, name="sib", port=9999)})


def default_effective(port=20128, image=DEFAULT_IMAGE, password=PASSWORD, **svc_patch):
    svc = {"image": image, "container_name": "omniroute", "ports": [f"0.0.0.0:{port}:{port}"], "stop_grace_period": "40s",
           "environment": {"INITIAL_PASSWORD": password.replace("$", "$$"), "REQUIRE_API_KEY": "true"}}
    return {"services": {"omniroute": {**svc, **svc_patch}}, "networks": {"default": {}}, "volumes": {"omniroute-data": {}}}


def preflight_world(tmp_path, *, port=20128, password=PASSWORD, image=DEFAULT_IMAGE, omni_kw=None,
                    extra_containers=None, effective=None, env=None, svc_patch=None):
    cfg = {"omniroute": {"port": port, "image": image, "initial_password": password}}
    config_path = tmp_path / "models.yml"; config_path.write_text(yaml.safe_dump(cfg))
    compose_path = tmp_path / "docker-compose.yml"; compose_path.write_text("services:\n  omniroute: {}\n")
    prov = tmp_path / "podman-compose"; prov.write_bytes(b"#!/bin/sh\n"); prov.chmod(0o755)
    eff = effective or default_effective(port, image, password, **(svc_patch or {}))
    containers = {OMNI: omni(**(omni_kw or {})), **(extra_containers or {})}
    run = fake_backend(containers, extra=[
        ((str(prov), "--version"), b"podman-compose 1.6.0\n"),
        ((str(prov), "--in-pod"), yaml.safe_dump(eff).encode()),
        (("podman", "image", "inspect"), json.dumps([{"Id": IMG, "Os": "linux", "Architecture": "amd64", "Variant": ""}]).encode()),
    ])
    return cfg, str(config_path), str(compose_path), str(prov), (env if env is not None else {}), run
```

Tests (top of `tests/test_omniroute_update.py`'s Task 2 section; the repo-root `sys.path` line from Task 1 is already at the top of the module):

```python
import yaml
from pylib.omniroute_update import (Snapshot, take_snapshot, incoming, assert_unchanged, fingerprint, fingerprint_parts,
                                    preflight, recheck, write_preflight_artifacts)
from omniroute_fakes import (OMNI, RS, SIB, NET, IMG, PROJECT, PASSWORD, inspect_doc, omni, fake_backend, with_dependent,
                             without_sibling, with_port_changed, preflight_world, default_effective)

@pytest.fixture(autouse=True)
def _no_remote_podman_in_the_ambient_environment(monkeypatch):
    for name in ("CONTAINER_HOST", "CONTAINER_CONNECTION"):
        monkeypatch.delenv(name, raising=False)
```

Tests:

```python
def test_top_level_dependencies_detected_even_for_stopped_cross_project_container():
    run = fake_backend({OMNI: omni(), RS: inspect_doc(RS, deps=[OMNI], state="exited", project="other")})
    assert incoming(take_snapshot(run), OMNI) == [RS]

def test_missing_dependencies_key_is_schema_deferral():
    doc = inspect_doc(RS, deps=[OMNI]); del doc["Dependencies"]
    with pytest.raises(Deferral) as e:
        take_snapshot(fake_backend({OMNI: omni(), RS: doc}))
    assert e.value.reason is Reason.schema

@pytest.mark.parametrize("bad", ["short", None, 7, "G" * 64])
def test_malformed_dependency_id_defers(bad):
    doc = inspect_doc(RS); doc["Dependencies"] = [bad]
    with pytest.raises(Deferral) as e:
        take_snapshot(fake_backend({OMNI: omni(), RS: doc}))
    assert e.value.reason is Reason.schema

def test_uninspectable_container_defers_inventory():
    with pytest.raises(Deferral) as e:
        take_snapshot(fake_backend({OMNI: omni(), RS: UpdateError("inspect failed")}))
    assert e.value.reason is Reason.io

def test_non_rootless_or_remote_backend_defers():
    with pytest.raises(Deferral) as e:
        take_snapshot(fake_backend({OMNI: omni()}, rootless=False))
    assert e.value.reason is Reason.ownership
    with pytest.raises(Deferral) as e:
        take_snapshot(fake_backend({OMNI: omni()}, remote=True))
    assert e.value.reason is Reason.ownership
    for var in ("CONTAINER_HOST", "CONTAINER_CONNECTION"):          # the injected env is what is checked, not os.environ
        with pytest.raises(Deferral) as e:
            take_snapshot(fake_backend({OMNI: omni()}), {var: "ssh://x"})
        assert e.value.reason is Reason.input

def test_assert_unchanged_detects_new_dependent_added_container_removed_and_config_change():
    base = take_snapshot(fake_backend({OMNI: omni(), SIB: inspect_doc(SIB, name="sib")}))
    assert_unchanged(base, take_snapshot(fake_backend({OMNI: omni(), SIB: inspect_doc(SIB, name="sib")})))
    for mutated in (with_dependent(), without_sibling(), with_port_changed(SIB)):
        with pytest.raises(Deferral) as e:
            assert_unchanged(base, take_snapshot(mutated))
        assert e.value.reason is Reason.ownership

def _mutate_config(**changes):
    doc = omni()
    doc["Config"].update(changes)
    return doc

@pytest.mark.parametrize("changed", [
    _mutate_config(Env=["INITIAL_PASSWORD=other", "REQUIRE_API_KEY=true"]),
    _mutate_config(Entrypoint=["node", "other.js"]),
    _mutate_config(Cmd=["--other"]),
    _mutate_config(WorkingDir="/elsewhere"),
    _mutate_config(Healthcheck={"Test": ["CMD", "false"], "Interval": 1}),
])
def test_every_create_configuration_field_is_part_of_the_container_hash(changed):
    base = take_snapshot(fake_backend({OMNI: omni(), SIB: inspect_doc(SIB, name="sib")}))
    after = take_snapshot(fake_backend({OMNI: changed, SIB: inspect_doc(SIB, name="sib")}))
    with pytest.raises(Deferral) as e:
        assert_unchanged(base, after)
    assert e.value.reason is Reason.ownership

def test_snapshot_json_roundtrip_drops_raw_docs():
    snap = take_snapshot(fake_backend({OMNI: omni()}))
    text = snap.to_json()
    assert PASSWORD not in text
    assert Snapshot.from_json(text) == snap
```

Fingerprint tests use real temp files (helper below) and mutate exactly one input class per case:

```python
COMPOSE = ('services:\n  omniroute:\n    image: docker.io/diegosouzapw/omniroute:latest\n'
           '    ports: ["0.0.0.0:${PORT}:20128"]\n')
EFFECTIVE = {"services": {"omniroute": {"image": "docker.io/diegosouzapw/omniroute:latest",
                                        "container_name": "omniroute", "ports": ["0.0.0.0:20128:20128"],
                                        "deploy": {"resources": {"limits": {"memory": "2g"}}}}},
             "networks": {"default": {}}, "volumes": {"omniroute-data": {}}}

def fp_world(tmp_path, *, config="a: 1\n", compose=COMPOSE, provider=b"#!/bin/sh\n", effective=None,
             env=None, containers=None):
    cfg = tmp_path / "models.yml"; cfg.write_text(config)
    comp = tmp_path / "docker-compose.yml"; comp.write_text(compose)
    prov = tmp_path / "podman-compose"; prov.write_bytes(provider); prov.chmod(0o755)
    run = fake_backend(containers or {OMNI: omni()}, extra=[((str(prov), "--version"), b"podman-compose 1.6.0\n")])
    snap = take_snapshot(run)
    parts = fingerprint_parts(str(cfg), str(comp), str(prov), effective or EFFECTIVE,
                              env or {"PORT": "20128"}, snap, run)
    return fingerprint(parts), parts

def test_fingerprint_is_stable(tmp_path):
    assert fp_world(tmp_path)[0] == fp_world(tmp_path)[0]

def _mut(**kw):
    return kw

@pytest.mark.parametrize("mutate", [
    _mut(config="a: 2\n"),
    _mut(compose=COMPOSE + "# changed\n"),
    _mut(env={"PORT": "20129"}),                       # referenced interpolation value
    _mut(provider=b"#!/bin/sh\n# other\n"),
    _mut(containers={OMNI: omni(net="f" * 64)}),       # network id
    _mut(containers={OMNI: omni(), SIB: inspect_doc(SIB, name="sib")}),   # sibling id
    _mut(effective={**EFFECTIVE, "volumes": {"other-data": {}}}),         # volume name
    _mut(effective={**EFFECTIVE, "services": {"omniroute": {**EFFECTIVE["services"]["omniroute"],
                                                             "ports": ["0.0.0.0:20129:20128"]}}}),
    _mut(effective={**EFFECTIVE, "services": {"omniroute": {**EFFECTIVE["services"]["omniroute"],
                                                             "deploy": {"resources": {"limits": {"memory": "1g"}}}}}}),
])
def test_fingerprint_changes_for_every_input_class(tmp_path, mutate):
    assert fp_world(tmp_path)[0] != fp_world(tmp_path, **mutate)[0]

def test_fingerprint_output_never_contains_secret_bytes(tmp_path):
    digest, parts = fp_world(tmp_path, effective={**EFFECTIVE, "x-secret": PASSWORD})
    assert PASSWORD not in digest and all(PASSWORD not in k for k in parts)
    assert digest.startswith("sha256:") and len(digest) == 71

@pytest.mark.parametrize("key", ["include", "extends", "env_file", "secrets", "configs", "build",
                                 "network_mode", "pod", "volumes_from", "links"])
def test_unsupported_compose_inputs_defer_input(tmp_path, key):
    svc = {**EFFECTIVE["services"]["omniroute"], key: "x"}
    with pytest.raises(Deferral) as e:
        fp_world(tmp_path, effective={**EFFECTIVE, "services": {"omniroute": svc}})
    assert e.value.reason is Reason.input

def test_compose_env_variables_defer_input(tmp_path):
    with pytest.raises(Deferral) as e:
        fp_world(tmp_path, env={"PORT": "1", "COMPOSE_PROFILES": "x"})
    assert e.value.reason is Reason.input
```

(In the parametrized test, each case runs `fp_world` into the same `tmp_path`, which rewrites the same files; that is intended.) Preflight agreement checks use `preflight_world` from `tests/omniroute_fakes.py` (it builds the config, compose file, provider stub, scripted backend, and an effective compose object that agrees with the config; `svc_patch` changes only the effective `omniroute` service, `omni_kw` only the running container):

```python
def test_preflight_happy_path_returns_deployment_without_secrets(tmp_path):
    cfg, cp, comp, prov, env, run = preflight_world(tmp_path)
    dep, snap, fp, eff = preflight(cfg, cp, comp, env, run, provider=prov)
    assert dep.container_id == OMNI and dep.project == PROJECT and dep.port == 20128
    assert dep.stop_seconds == 40 and dep.pinned is False and (dep.os, dep.architecture) == ("linux", "amd64")
    assert dep.image_id == "sha256:" + IMG                           # Podman's bare hex, canonicalized once here
    assert PASSWORD not in repr(dep) and fp.startswith("sha256:")

PIN_A, PIN_B = "docker.io/diegosouzapw/omniroute@sha256:" + "a" * 64, "docker.io/diegosouzapw/omniroute@sha256:" + "b" * 64

@pytest.mark.parametrize("name,kw,reason", [
    ("image-mismatch", dict(omni_kw=dict(image_name="docker.io/other/img:latest")), Reason.ownership),
    ("docker-hub-alias-ok", dict(image="diegosouzapw/omniroute:latest"), None),
    ("digest-form-running-reference-ok", dict(omni_kw=dict(image_name="docker.io/diegosouzapw/omniroute@sha256:" + "9" * 64)), None),
    ("pinned-config-running-other-digest", dict(image=PIN_A, svc_patch=dict(image=PIN_A), omni_kw=dict(image_name=PIN_B)), Reason.ownership),
    ("pinned-config-effective-other-digest", dict(image=PIN_A, svc_patch=dict(image=PIN_B), omni_kw=dict(image_name=PIN_A)), Reason.ownership),
    ("effective-image-differs-from-config", dict(svc_patch=dict(image="docker.io/other/img:latest")), Reason.ownership),
    ("effective-port-differs-from-config", dict(svc_patch=dict(ports=["0.0.0.0:9999:9999"])), Reason.ownership),
    ("effective-password-differs-from-config", dict(svc_patch=dict(environment={"INITIAL_PASSWORD": "wrong", "REQUIRE_API_KEY": "true"})), Reason.ownership),
    ("effective-require-key-off", dict(svc_patch=dict(environment={"INITIAL_PASSWORD": PASSWORD, "REQUIRE_API_KEY": "false"})), Reason.ownership),
    ("port-mismatch", dict(omni_kw=dict(port=9999)), Reason.ownership),
    ("password-mismatch", dict(omni_kw=dict(env=["INITIAL_PASSWORD=wrong", "REQUIRE_API_KEY=true"])), Reason.ownership),
    ("dollar-password-ok", dict(password="pa$s", omni_kw=dict(env=["INITIAL_PASSWORD=pa$s", "REQUIRE_API_KEY=true"])), None),
    ("require-key-off", dict(omni_kw=dict(env=[f"INITIAL_PASSWORD={PASSWORD}", "REQUIRE_API_KEY=false"])), Reason.ownership),
    ("unhealthy", dict(omni_kw=dict(health="unhealthy")), Reason.ownership),
    ("stopped", dict(omni_kw=dict(state="exited")), Reason.ownership),
    ("no-project-label", dict(omni_kw=dict(project="")), Reason.ownership),
    ("incoming-dependent", dict(extra_containers={RS: inspect_doc(RS, name="remote-setup", deps=[OMNI])}), Reason.ownership),
    ("scale-2", dict(svc_patch=dict(scale=2)), Reason.input),
    ("compose-env", dict(env={"COMPOSE_PROFILES": "x"}), Reason.input),
])
def test_agreement_checks(tmp_path, name, kw, reason):
    cfg, cp, comp, prov, env, run = preflight_world(tmp_path, **kw)
    if reason is None:
        preflight(cfg, cp, comp, env, run, provider=prov)
        return
    with pytest.raises(Deferral) as e:
        preflight(cfg, cp, comp, env, run, provider=prov)
    assert e.value.reason is reason
    assert PASSWORD not in str(e.value)

def test_recheck_defers_on_new_dependent_and_succeeds_when_unchanged(tmp_path):
    cfg, cp, comp, prov, env, run = preflight_world(tmp_path)
    out = tmp_path / "out"; out.mkdir(mode=0o700)
    dep, snap, fp, eff = preflight(cfg, cp, comp, env, run, provider=prov)
    write_preflight_artifacts(str(out), dep, snap, fp, eff, snap.docs[OMNI])
    recheck(str(out), cp, comp, env, run)                         # unchanged -> no raise
    cfg2, cp2, comp2, prov2, env2, run2 = preflight_world(
        tmp_path, extra_containers={RS: inspect_doc(RS, name="remote-setup", deps=[OMNI])})
    with pytest.raises(Deferral) as e:
        recheck(str(out), cp, comp, env, run2)
    assert e.value.reason is Reason.ownership

def test_recheck_turns_a_command_failure_into_a_deferral_never_an_error(tmp_path):
    cfg, cp, comp, prov, env, run = preflight_world(tmp_path)
    out = tmp_path / "out"; out.mkdir(mode=0o700)
    dep, snap, fp, eff = preflight(cfg, cp, comp, env, run, provider=prov)
    write_preflight_artifacts(str(out), dep, snap, fp, eff, snap.docs[OMNI])
    def failing(argv, timeout=None):
        if argv[0] == prov and "config" in argv:
            raise UpdateError("provider failed")                  # raised outside the inventory helper
        return run(argv, timeout=timeout)
    with pytest.raises(Deferral) as e:
        recheck(str(out), cp, comp, env, failing)
    assert e.value.reason is Reason.io
```

In `tests/test_cli.py`, add `test_preflight_cli_exit_codes_and_files` (import the world from `omniroute_fakes`; `cmd_preflight` is in `pylib.omniroute_cli`). The handler's provider lookup is an injected argument, not an environment seam, so the test never depends on a host `podman-compose`: call `cmd_preflight(Namespace(config=cp, compose=comp, out_dir=out, recheck=False, budget=60, service="omniroute"), run=run, env={"PATH": "/usr/bin", "HOME": "/h"}, which=lambda name: prov)`; assert return 0, stdout lines contain `container_id=` and `image_id=sha256:` and not the password, files `snapshot.json fingerprint.txt effective.json target.json deployment.json` exist with mode 0600; a world with an incoming dependent returns 75 and stdout is exactly `deferred: ownership`; a runner that raises `UpdateError` returns 1 with an empty stdout; `out_dir` with mode 0755 returns 75 `deferred: lock`; `env={"PATH": "/usr/bin", "HOME": "/h", "CONTAINER_HOST": "ssh://x"}` returns 75 `deferred: input` (the injected env, not `os.environ`, is what is validated); `recheck=True` returns 0 for the unchanged world and 75 for the drifted one; and `test_preflight_recheck_budget_discards_a_slow_success`: with `budget=1` and a `run` that sleeps 1.2 s before answering `podman info`, the recheck returns 75 (`deferred: io`) because `with_deadline` discards the late result.

- [ ] **Step 3: Run, verify FAIL** (`uv run pytest tests/test_omniroute_update.py tests/test_cli.py -v`).
- [ ] **Step 4: Implement** in `pylib/omniroute_update.py`:

```python
import hmac
import shutil
import stat
from dataclasses import asdict, field
from pathlib import Path

import yaml

LABEL_PROJECT = "io.podman.compose.project"
LABEL_SERVICE = "io.podman.compose.service"
DEFAULT_IMAGE = "docker.io/diegosouzapw/omniroute:latest"
_ID_RE = re.compile(r"[0-9a-f]{64}")
_UNSUPPORTED = ("include", "extends", "env_file", "secrets", "configs", "build",
                "network_mode", "pod", "volumes_from", "links")


def _json(raw_or_call, what: str):
    """Parse JSON; inventory failures are unknown, therefore a deferral (Reason.io)."""
    try:
        return json.loads(raw_or_call)
    except (json.JSONDecodeError, TypeError) as exc:
        raise Deferral(Reason.io, f"{what} output is not JSON") from exc


def _inventory(run: Runner, argv: list[str], what: str):
    try:
        return _json(run(argv), what)
    except UpdateError as exc:
        raise Deferral(Reason.io, f"{what} command failed") from exc


def _canon_hash(obj) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class Snapshot:
    backend: dict
    containers: dict[str, str]
    deps: frozenset[tuple[str, str]]
    docs: dict[str, dict] = field(default_factory=dict, compare=False, repr=False)

    def to_json(self) -> str:
        return json.dumps({"backend": self.backend, "containers": self.containers,
                           "deps": sorted([list(d) for d in self.deps])}, sort_keys=True)

    @staticmethod
    def from_json(text: str) -> "Snapshot":
        raw = json.loads(text)
        return Snapshot(raw["backend"], raw["containers"], frozenset(tuple(d) for d in raw["deps"]))


def take_snapshot(run: Runner, env: Mapping[str, str] = os.environ) -> Snapshot:
    if env.get("CONTAINER_HOST") or env.get("CONTAINER_CONNECTION"):
        raise Deferral(Reason.input, "remote Podman connection configured")
    info = _inventory(run, ["podman", "info", "--format", "json"], "info")
    store, host = info.get("store") or {}, info.get("host") or {}
    if (host.get("security") or {}).get("rootless") is not True or host.get("serviceIsRemote") is not False:
        raise Deferral(Reason.ownership, "backend is not a local rootless Podman")
    backend = {"graphRoot": os.path.realpath(store["graphRoot"]), "runRoot": os.path.realpath(store["runRoot"]),
               "rootless": True, "remote": False}
    ids = [c["Id"] for c in _inventory(run, ["podman", "ps", "--all", "--no-trunc", "--format", "json"], "ps")]
    containers, deps, docs = {}, set(), {}
    for cid in ids:
        if not isinstance(cid, str) or not _ID_RE.fullmatch(cid):
            raise Deferral(Reason.schema, "malformed container id")
        got = _inventory(run, ["podman", "container", "inspect", "--format", "json", cid], "inspect")
        if not (isinstance(got, list) and len(got) == 1 and got[0].get("Id") == cid):
            raise Deferral(Reason.schema, "unexpected inspect shape")
        d = got[0]
        dependencies = d.get("Dependencies")                 # supported schema: top-level array of strings
        if not isinstance(dependencies, list) or not all(isinstance(x, str) and _ID_RE.fullmatch(x) for x in dependencies):
            raise Deferral(Reason.schema, "missing or malformed Dependencies")
        deps.update((cid, t) for t in dependencies)
        docs[cid] = d
        containers[cid] = _canon_hash(
            {k: d.get(k) for k in ("Id", "Name", "Image", "ImageName", "Dependencies", "Mounts", "HostConfig", "Config")}
            | {"networks": {n: v.get("NetworkID") for n, v in ((d.get("NetworkSettings") or {}).get("Networks") or {}).items()},
               "started": (d.get("State") or {}).get("StartedAt")})
    return Snapshot(backend, containers, frozenset(deps), docs)


def incoming(snap: Snapshot, target_id: str) -> list[str]:
    return sorted(dep for dep, tgt in snap.deps if tgt == target_id)


def assert_unchanged(before: Snapshot, after: Snapshot) -> None:
    if before.backend != after.backend or before.containers != after.containers or before.deps != after.deps:
        raise Deferral(Reason.ownership, "backend inventory changed")


def reject_unsupported(effective: dict) -> None:
    for name, svc in (effective.get("services") or {}).items():
        if any(key in svc for key in _UNSUPPORTED):
            raise Deferral(Reason.input, f"unsupported compose key on service {name}")
        if svc.get("scale", 1) != 1 or (svc.get("deploy") or {}).get("replicas", 1) != 1:
            raise Deferral(Reason.input, f"service {name} is scaled")
        if any(isinstance(v, str) and ("${" in v) for v in _strings(svc)):
            raise Deferral(Reason.input, "unresolved interpolation")
        for volume in svc.get("volumes") or []:
            if isinstance(volume, str) and ":" not in volume:
                raise Deferral(Reason.input, "anonymous volume")


def _env_dict(env) -> dict[str, str]:
    if isinstance(env, dict):
        return {k: "" if v is None else str(v) for k, v in env.items()}
    return dict(item.partition("=")[::2] for item in env or [])


def _strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _strings(v)


def fingerprint_parts(config_path, compose_path, provider, effective, env, snap, run) -> dict[str, bytes]:
    if any(name.startswith(("COMPOSE_", "PODMAN_COMPOSE_")) for name in env):
        raise Deferral(Reason.input, "compose environment override set")
    reject_unsupported(effective)
    config_bytes, compose_bytes = Path(config_path).read_bytes(), Path(compose_path).read_bytes()
    referenced = sorted(set(re.findall(r"\$\{([A-Z_][A-Z0-9_]*)", compose_bytes.decode("utf-8", "replace"))))
    version = run([provider, "--version"])
    return {
        "config": config_bytes, "config-path": os.path.realpath(config_path).encode(),
        "compose": compose_bytes, "compose-path": os.path.realpath(compose_path).encode(),
        "effective": json.dumps(effective, sort_keys=True).encode(),
        "interpolation-env": json.dumps({n: env.get(n) for n in referenced}, sort_keys=True).encode(),
        "provider": Path(provider).read_bytes() + b"\0" + version,
        "backend": json.dumps(snap.backend, sort_keys=True).encode(),
        "containers": json.dumps(snap.containers, sort_keys=True).encode(),
        "deps": json.dumps(sorted(list(d) for d in snap.deps)).encode(),
    }


def fingerprint(parts: dict[str, bytes]) -> str:
    lines = b"".join(name.encode() + b"\0" + hashlib.sha256(parts[name]).hexdigest().encode() + b"\n"
                     for name in sorted(parts))
    return "sha256:" + hashlib.sha256(lines).hexdigest()


def same_image(configured: str, running: str) -> bool:
    """Configured reference versus the running container's ImageName; a digest pin is compared, never discarded."""
    c_repo, c_tag, c_digest = parse_reference(configured)
    r_repo, r_tag, r_digest = parse_reference(running)
    if c_repo != r_repo:
        return False
    if c_digest:
        return r_digest == c_digest
    return bool(r_digest) or r_tag == c_tag     # digest form: created from our immutable reference after an earlier update


def _published_ports(svc: dict) -> set[str]:
    out = set()
    for entry in svc.get("ports") or []:
        if isinstance(entry, dict):
            out.add(str(entry.get("published", "")))
        else:
            parts = str(entry).split("/")[0].split(":")
            out.add(parts[-2] if len(parts) >= 2 else "")
    return out


def effective_config(provider: str, project: str, compose_path: str, run: Runner) -> dict:
    out = run([provider, "--in-pod", "false", "--env-file", "/dev/null", "-p", project, "-f", compose_path, "config"])
    try:
        effective = yaml.safe_load(out)
    except yaml.YAMLError as exc:
        raise Deferral(Reason.io, "provider config output is not YAML") from exc
    if not isinstance(effective, dict) or not isinstance(effective.get("services"), dict):
        raise Deferral(Reason.schema, "provider config has no services")
    return effective


@dataclass(frozen=True)
class Deployment:
    container_id: str
    container_name: str
    project: str
    image_id: str
    image_ref: str
    started_at: str
    port: int
    stop_seconds: int
    pinned: bool
    os: str
    architecture: str
    variant: str
    provider: str
    network_ids: tuple[str, ...]
    volume_names: tuple[str, ...]
    sibling_ids: tuple[str, ...]


def _env_value(doc: dict, name: str) -> str | None:
    for item in (doc.get("Config") or {}).get("Env") or []:
        key, _, value = item.partition("=")
        if key == name:
            return value
    return None


def preflight(cfg, config_path, compose_path, env, run, provider: str | None = None, service: str = "omniroute"):
    snap = take_snapshot(run, env)
    omni_cfg = cfg.get("omniroute") or {}
    port, image_ref = int(omni_cfg.get("port", 20128)), omni_cfg.get("image", DEFAULT_IMAGE)
    password = str(omni_cfg.get("initial_password", ""))
    matches = [cid for cid, d in snap.docs.items()
               if ((d.get("Config") or {}).get("Labels") or {}).get(LABEL_SERVICE) == service]
    if len(matches) != 1:
        raise Deferral(Reason.ownership, f"expected exactly one container for service {service}")
    cid = matches[0]
    doc = snap.docs[cid]
    labels = (doc.get("Config") or {}).get("Labels") or {}
    project = labels.get(LABEL_PROJECT)
    if not project:
        raise Deferral(Reason.ownership, "project label missing")
    state = doc.get("State") or {}
    if state.get("Running") is not True or (state.get("Health") or {}).get("Status") != "healthy":
        raise Deferral(Reason.ownership, "target is not running and healthy")
    if service == "omniroute":                       # OmniRoute-specific agreement; other services get the generic checks only
        if not same_image(image_ref, doc.get("ImageName", "")):
            raise Deferral(Reason.ownership, "running image reference differs from configuration")
        bindings = ((doc.get("HostConfig") or {}).get("PortBindings") or {}).get(f"{port}/tcp") or []
        if [b.get("HostPort") for b in bindings] != [str(port)]:
            raise Deferral(Reason.ownership, "published port differs from configuration")
        running_pw = _env_value(doc, "INITIAL_PASSWORD") or ""
        if not hmac.compare_digest(running_pw.encode(), password.encode()):   # private compare; never echoed
            raise Deferral(Reason.ownership, "dashboard password differs from configuration")
        if _env_value(doc, "REQUIRE_API_KEY") != "true":
            raise Deferral(Reason.ownership, "REQUIRE_API_KEY is not true")
    else:
        port, image_ref = 0, doc.get("ImageName", "")
    if incoming(snap, cid):
        raise Deferral(Reason.ownership, "a container depends on the target")
    provider = provider or shutil.which("podman-compose") or ""
    if not provider:
        raise Deferral(Reason.input, "podman-compose not found")
    effective = effective_config(provider, project, compose_path, run)
    svc = (effective["services"] or {}).get(service)
    if not isinstance(svc, dict) or (service == "omniroute" and svc.get("container_name") != "omniroute"):
        raise Deferral(Reason.ownership, f"effective compose has no matching {service} service")
    parts = fingerprint_parts(config_path, compose_path, provider, effective, env, snap, run)   # rejects COMPOSE_* / scale / unsupported keys
    if service == "omniroute":                       # config == effective compose == running container (spec ORU-R1-004), before any registry access
        if parse_reference(str(svc.get("image", ""))) != parse_reference(image_ref):
            raise Deferral(Reason.ownership, "effective compose image differs from configuration")
        if _published_ports(svc) != {str(port)}:
            raise Deferral(Reason.ownership, "effective compose published port differs from configuration")
        effective_env = _env_dict(svc.get("environment"))
        if effective_env.get("INITIAL_PASSWORD", "") not in (password, password.replace("$", "$$")):   # private compare; never echoed
            raise Deferral(Reason.ownership, "effective compose dashboard password differs from configuration")
        if effective_env.get("REQUIRE_API_KEY") != "true":
            raise Deferral(Reason.ownership, "effective compose REQUIRE_API_KEY is not true")
    grace = re.fullmatch(r"(\d+)s", str(svc.get("stop_grace_period", "" if service == "omniroute" else "10s")))
    if not grace:
        raise Deferral(Reason.input, "stop_grace_period must be whole seconds")
    image_info = _inventory(run, ["podman", "image", "inspect", "--format", "json", doc["Image"]], "image inspect")[0]
    siblings = sorted(c for c, d in snap.docs.items()
                      if c != cid and ((d.get("Config") or {}).get("Labels") or {}).get(LABEL_PROJECT) == project)
    dep = Deployment(
        cid, service, project, canon_image_id(doc["Image"]), image_ref, state.get("StartedAt", ""), port, int(grace.group(1)),
        "@sha256:" in image_ref, image_info.get("Os", ""), image_info.get("Architecture", ""),
        image_info.get("Variant", ""), provider,
        tuple(sorted({v.get("NetworkID") for v in ((doc.get("NetworkSettings") or {}).get("Networks") or {}).values()})),
        tuple(sorted(m["Name"] for m in doc.get("Mounts") or [] if m.get("Type") == "volume")),
        tuple(siblings))
    return dep, snap, fingerprint(parts), effective


def write_preflight_artifacts(out_dir: str, dep: Deployment, snap: Snapshot, fp: str, effective: dict, target_doc: dict) -> None:
    write_private(f"{out_dir}/snapshot.json", snap.to_json())
    write_private(f"{out_dir}/fingerprint.txt", fp + "\n")
    write_private(f"{out_dir}/effective.json", json.dumps(effective))
    write_private(f"{out_dir}/target.json", json.dumps([target_doc]))
    write_private(f"{out_dir}/deployment.json", json.dumps(asdict(dep)))


def check_private_dir(path: str) -> None:
    st = os.lstat(path)
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.geteuid() or stat.S_IMODE(st.st_mode) != 0o700:
        raise Deferral(Reason.lock, "artifact directory is not a private 0700 directory owned by this user")


def recheck(out_dir: str, config_path: str, compose_path: str, env, run: Runner) -> None:
    check_private_dir(out_dir)
    before = Snapshot.from_json(Path(out_dir, "snapshot.json").read_text())
    expected = Path(out_dir, "fingerprint.txt").read_text().strip()
    dep = json.loads(Path(out_dir, "deployment.json").read_text())
    try:
        snap = take_snapshot(run, env)
        assert_unchanged(before, snap)
        if incoming(snap, dep["container_id"]):
            raise Deferral(Reason.ownership, "a container now depends on the target")
        effective = effective_config(dep["provider"], dep["project"], compose_path, run)
        if fingerprint(fingerprint_parts(config_path, compose_path, dep["provider"], effective, env, snap, run)) != expected:
            raise Deferral(Reason.ownership, "deployment fingerprint drifted")
    except UpdateError as exc:                       # a recheck that cannot complete (or finished late) proves nothing
        raise Deferral(Reason.io, "recheck command failed") from exc
```

- [ ] **Step 5: Run, verify PASS.**
- [ ] **Step 6: CLI `preflight`** in `pylib/omniroute_cli.py` (register it in `register(sub)` with `--config`, `--compose`, `--out-dir` required, `--recheck` a flag, `--budget` an int default 60, `--service` default `omniroute`):

```python
@guarded
def cmd_preflight(args, run=None, env=None, which=shutil.which) -> int:
    env = os.environ if env is None else env
    run = run or scrubbed_runner(env)
    check_private_dir(args.out_dir)
    if args.recheck:
        recheck(args.out_dir, args.config, args.compose, env,
                with_deadline(run, time.monotonic() + args.budget))      # every recheck command shares one budget
        return 0
    cfg = yaml.safe_load(Path(args.config).read_text()) or {}
    dep, snap, fp, effective = preflight(cfg, args.config, args.compose, env, run,
                                         provider=which("podman-compose") or None, service=args.service)
    write_preflight_artifacts(args.out_dir, dep, snap, fp, effective, snap.docs[dep.container_id])
    for key, value in asdict(dep).items():
        if isinstance(value, (str, int, bool)):
            print(f"{key}={int(value) if isinstance(value, bool) else value}")
    return 0
```

  Prints no secret fields (the Deployment carries none). `Deferral` -> `deferred: <reason>` + 75 and `UpdateError` -> stderr + 1 come from `guarded` (Task 1).
  Add the imports the handler uses at the top of `pylib/omniroute_cli.py`: `shutil`, `time`, `yaml`, `Path`, `asdict`, and from `pylib.omniroute_update` `preflight`, `recheck`, `write_preflight_artifacts`, `check_private_dir`, `with_deadline`.
- [ ] **Step 7: Validate; commit if approved.**

---

### Task 3: Activity sampling and capability profile

**Files:**
- Create: `pylib/omniroute_activity.py`, `pylib/omniroute_activity_profiles.json` (initially `{"schema": 1, "profiles": []}`), `tests/test_omniroute_activity.py`, `tests/fixtures/fake_omniroute.py`
- Modify: `tests/omniroute_fakes.py` (append the profile fixtures and `mk_sample`)

**Interfaces:**
- Consumes (Tasks 1-2): `Runner`, `Deferral`, `Reason`, `UpdateError`, `with_deadline`, `Deployment`.
- Produces: `Sample` frozen dataclass exactly per spec "Sanitized observer state schema v1" (`schema, profile_sha256, container_id, started_at, boot_id, sequence, sample_monotonic, arrival_generation, row_generation, active, pending, admission, health_timestamp_ms, health_age_ms, feed_ready, barrier_seen, continuous, overflow, unknown, reason`) where `reason: Reason` is the same enum as `Deferral.reason`. `Sample.validate()` rejects booleans for counts, negatives, non-finite floats; `Sample.to_json()/from_json()` (reason serialized as its value).
- Produces: `classify(s: Sample) -> Literal["idle","busy","unknown"]`: `busy` if any of `active/pending/admission > 0` (even when unknown); `unknown` if `unknown or overflow or not (feed_ready and barrier_seen and continuous)`; else `idle`.
- Produces: `Profile` (schema below), `load_profile(config_id: str, container_inspect: dict, path=PROFILE_PATH) -> Profile` raising `Deferral(Reason.capability | logging | coverage | feed)`. `config_id` must be the canonical `sha256:<hex>` (`Deployment.image_id`, Task 2); anything else is `Deferral(Reason.capability)`, so a caller that forgets `canon_image_id` fails closed instead of silently never matching.
- Produces: `failed_sample(container_id, started_at, sequence, reason, now) -> Sample` — an all-zero `unknown=True` sample carrying `reason`, used when no `Collector` can be built (Task 5).
- Produces journal types: `JournalRecord(method: str, path: str)`, `JournalBatch(records: list[JournalRecord], boot_id: str, ready: bool, barrier_seen: bool, continuous: bool, overflow: bool)`, and a duck-typed reader with `poll(self, deadline: float, force_barrier: bool = False) -> JournalBatch` and `terminate(self) -> None`. Real implementation `JournalctlReader(container_id: str, popen=subprocess.Popen, argv0: str = "journalctl", env=None)` (`env` is the scrubbed `backend_env(...)` dict passed to every child; children are owned by the supervisor, Task 5); tests inject `FakeReader`. `parse_journal_line(line: bytes, profile) -> JournalRecord | None | OVERFLOW`.
- Produces: `check_freshness(timestamp_ms, wall_ms, cache_ms, refresh_ms, tolerance_ms) -> Reason | None` implementing the spec formula verbatim: `0 <= wall_ms - timestamp_ms <= cache_ms + refresh_ms + tolerance_ms`.
- Produces: `bounded_login(base_url, password, deadline) -> str`, `bounded_get(base_url, path, token, deadline) -> bytes` (aggregate monotonic deadline; see Step 3).
- Produces: `Collector(*, deployment, profile, reader, fetch: Callable[[str, float], bytes], recheck: Callable[[float], None], clock=time.monotonic, wall=time.time, tolerance_ms: int)` and `Collector.sample(deadline: float, force_barrier: bool = False) -> Sample`. `fetch(path, deadline)` returns the raw body (production: `bounded_get` with a token held in memory; tests: a fake). `recheck(deadline)` raises `Deferral` on drift (production: a closure over Task 2 `recheck` using `with_deadline(scrubbed_runner(env), deadline)`). One instance lives for the whole observation (Task 5) and keeps the journal reader, the hashed persisted/in-flight row ids of the previous sample, generation counters and the previous sample's `(monotonic, wall, health timestamp)`. **Missing, `null` or mistyped required sources are `schema` unknown, never zero** (`pending_by_model` must be a JSON object, `rows` a JSON array, every count a non-bool non-negative integer); a persisted row or an in-flight (memory) row that disappears without being explained, a wall-versus-monotonic jump between samples and a backward health timestamp are `continuity` / `clock` unknown (Step 3).
- Produces: `measure_clock_tolerance_ms(wall, clock, samples=20) -> int` (ceiling of the maximum bracket discrepancy plus resolution, in ms).

- [ ] **Step 1: Failing tests** in `tests/test_omniroute_activity.py`. First extend `tests/omniroute_fakes.py` (Tasks 5 and 6 import these too): add `from pylib.omniroute_activity import Sample` and `from pylib.omniroute_update import Reason` to the import block at the top of the file, then append:

```python
# appended to tests/omniroute_fakes.py
API = {"health_timestamp": "/timestamp", "admission": ["/chatAdmission/activeHeavy", "/chatAdmission/activeHealthyHeadroom",
       "/chatAdmission/waiting", "/chatAdmission/queuedBytes"], "active": "/active",
       "pending_by_model": "/pending/byModel", "rows": "/logs", "row_id": "id", "row_memory_flag": "memory"}
ROUTES = [
    {"method": "POST", "path_pattern": "/v1/chat/completions", "class": "inference",
     "arrival": {"signal": "journal-info-http", "evidence": "c1"},
     "inflight": {"signal": "usage-history-active", "evidence": "c1"},
     "queue": {"signal": "health-admission", "evidence": "c1"}},
    {"method": "GET", "path_pattern": "/api/monitoring/health", "class": "probe", "evidence": "c1"},
    {"method": "POST", "path_pattern": "/api/auth/login", "class": "probe", "evidence": "c1"},
]

def profile_entry(**kw):
    entry = {"config_id": "sha256:" + "c" * 64, "artifacts": {"app/server.js": "0" * 64},
        "entrypoint": ["node", "server.js"], "env_allowlist": {"NODE_ENV": "production"},
        "logger": {"destination": "journald-stdout", "min_level": "info", "level_env": "LOG_LEVEL",
                   "default_level": "info", "buffering": "line", "loss_detection": "cursor-continuity+journald-rate-limit-checked",
                   "fields": {"method": "method", "path": "path"}},
        "routes": ROUTES, "reachable_routes": ["POST /v1/chat/completions", "GET /api/monitoring/health", "POST /api/auth/login"],
        "api_fields": API, "health_cache_ms": 1000, "health_refresh_bound_ms": 500,
        "evidence": {"characterization_sha256": "1" * 64, "measured_at": "2026-10-22"}}
    entry.update(kw)
    return entry

def write_profiles(tmp_path, *entries):
    p = tmp_path / "profiles.json"; p.write_text(json.dumps({"schema": 1, "profiles": list(entries)})); return p

CONTAINER = {"Config": {"Env": ["NODE_ENV=production"], "Entrypoint": ["node", "server.js"]},
             "HostConfig": {"LogConfig": {"Type": "journald"}}}

def mk_sample(**kw):
    base = dict(schema=1, profile_sha256="0" * 64, container_id="a" * 64, started_at="t", boot_id="b", sequence=1,
                sample_monotonic=1.0, arrival_generation=0, row_generation=0, active=0, pending=0, admission=0,
                health_timestamp_ms=1000, health_age_ms=10, feed_ready=True, barrier_seen=True, continuous=True,
                overflow=False, unknown=False, reason=Reason.ready)
    return Sample(**{**base, **kw})
```

  Shared fixtures of the test module (the repo-root `sys.path` line comes first, as in `tests/test_remote_setup.py`):

```python
import json, math, sys, threading, time, http.server
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pylib.omniroute_update import Deferral, Reason, canon_image_id
from pylib.omniroute_activity import (Sample, classify, Profile, load_profile, check_freshness, Collector, failed_sample,
    JournalBatch, JournalRecord, bounded_login, bounded_get, parse_journal_line, OVERFLOW, JournalctlReader,
    measure_clock_tolerance_ms)
from omniroute_fakes import API, CONTAINER, profile_entry, write_profiles, mk_sample

class FakeReader:
    def __init__(self, *batches): self.batches = list(batches); self.terminated = False
    def poll(self, deadline, force_barrier=False): return self.batches.pop(0)
    def terminate(self): self.terminated = True
def batch(*records, ready=True, barrier=True, continuous=True, overflow=False, boot="b"):
    return JournalBatch(list(records), boot, ready, barrier, continuous, overflow)

def make_fetch(*, ts=1000, active=0, by_model=None, waiting=0, rows=(), health=None, history=None, logs=None):
    """Raw-body fake of the three admin GETs; `health`/`history`/`logs` replace a whole body (for missing-source cases)."""
    def fetch(path, deadline):
        if path.startswith("/api/monitoring/health"):
            return json.dumps(health if health is not None else {"timestamp": ts, "chatAdmission": {
                "activeHeavy": 0, "activeHealthyHeadroom": 0, "waiting": waiting, "queuedBytes": 0}}).encode()
        if path.startswith("/api/usage/history"):
            return json.dumps(history if history is not None else {"active": active, "pending": {
                "byModel": by_model or {}, "byAccount": {"a": 1} if by_model else {}}}).encode()
        if path.startswith("/api/usage/call-logs"):
            return json.dumps(logs if logs is not None else {"logs": list(rows)}).encode()
        raise AssertionError(path)
    return fetch

def collector(tmp_path, reader, fetch, *, wall=lambda: 1.0, recheck=lambda d: None, tol=250):
    prof = load_profile("sha256:" + "c" * 64, CONTAINER, write_profiles(tmp_path, profile_entry()))
    dep = type("D", (), {"container_id": "a" * 64, "started_at": "t"})()
    return Collector(deployment=dep, profile=prof, reader=reader, fetch=fetch, recheck=recheck,
                     clock=lambda: 1.0, wall=wall, tolerance_ms=tol)
```

Tests (spec fixtures verbatim):

```python
def test_active_counts_are_busy(tmp_path):
    s = collector(tmp_path, FakeReader(batch()), make_fetch(active=1, ts=1000), wall=lambda: 1.0).sample(deadline=time.monotonic() + 5)
    assert s.active == 1 and classify(s) == "busy"

def test_pending_counts_by_model_once(tmp_path):
    s = collector(tmp_path, FakeReader(batch()), make_fetch(by_model={"synthetic": 1})).sample(time.monotonic() + 5)
    assert s.pending == 1                      # byAccount duplicate is not summed

def test_admission_waiting_is_busy(tmp_path):
    s = collector(tmp_path, FakeReader(batch()), make_fetch(waiting=1)).sample(time.monotonic() + 5)
    assert s.admission == 1 and classify(s) == "busy"

def test_uncaught_barrier_is_unknown_even_with_zero_counts(tmp_path):
    s = collector(tmp_path, FakeReader(batch(barrier=False)), make_fetch()).sample(time.monotonic() + 5)
    assert classify(s) == "unknown" and s.reason is Reason.feed

def test_unprofiled_embeddings_arrival_is_coverage_unknown(tmp_path):
    s = collector(tmp_path, FakeReader(batch(JournalRecord("POST", "/v1/embeddings"))), make_fetch()).sample(time.monotonic() + 5)
    assert classify(s) == "unknown" and s.reason is Reason.coverage

def test_probe_arrivals_do_not_reset_quiet(tmp_path):
    c = collector(tmp_path, FakeReader(batch(), batch(JournalRecord("GET", "/api/monitoring/health"),
                                                      JournalRecord("POST", "/api/auth/login"))), make_fetch())
    a, b = c.sample(time.monotonic() + 5), c.sample(time.monotonic() + 5)
    assert b.arrival_generation == a.arrival_generation and classify(b) == "idle"

def test_chat_suffix_and_query_are_stripped_before_matching(tmp_path):
    c = collector(tmp_path, FakeReader(batch(), batch(JournalRecord("POST", "/v1/chat/completions?x=1 | model-a"))), make_fetch())
    a, b = c.sample(time.monotonic() + 5), c.sample(time.monotonic() + 5)
    assert b.arrival_generation == a.arrival_generation + 1 and classify(b) == "idle"

def test_between_poll_arrival_and_row_change_bump_generations_though_both_samples_idle(tmp_path):
    rows1 = [{"id": "r1"}]; rows2 = [{"id": "r1"}, {"id": "r2"}]
    c = collector(tmp_path, FakeReader(batch(), batch(JournalRecord("POST", "/v1/chat/completions"))), make_fetch())
    c.fetch = make_fetch(rows=rows1); a = c.sample(time.monotonic() + 5)
    c.fetch = make_fetch(rows=rows2); b = c.sample(time.monotonic() + 5)
    assert classify(a) == classify(b) == "idle"
    assert b.arrival_generation == a.arrival_generation + 1 and b.row_generation == a.row_generation + 1

@pytest.mark.parametrize("wall_s,expected", [(1.0 + 1.750, "idle"), (1.0 + 1.751, "unknown")])
def test_freshness_boundary_1750(tmp_path, wall_s, expected):
    c = collector(tmp_path, FakeReader(batch()), make_fetch(ts=1000), wall=lambda: wall_s - 0.0)
    # timestamp_ms=1000 vs wall 1.0 s + age: age = wall_ms - 1000
    assert classify(c.sample(time.monotonic() + 5)) == expected

def test_check_freshness_formula():
    assert check_freshness(1000, 2750, 1000, 500, 250) is None
    assert check_freshness(1000, 2751, 1000, 500, 250) is Reason.age
    assert check_freshness(1000, 999, 1000, 500, 250) is Reason.age          # future timestamp

def test_clock_bracket_mismatch_is_clock_unknown(tmp_path):
    walls = iter([1.0, 9.0])                          # wall jumps 8 s inside one monotonic-instant bracket
    c = collector(tmp_path, FakeReader(batch()), make_fetch(), wall=lambda: next(walls))
    assert c.sample(time.monotonic() + 5).reason is Reason.clock

def test_full_call_log_page_overflows(tmp_path):
    rows = [{"id": f"r{i}"} for i in range(1000)]
    s = collector(tmp_path, FakeReader(batch()), make_fetch(rows=rows)).sample(time.monotonic() + 5)
    assert s.overflow and classify(s) == "unknown"

def test_boot_change_or_vanished_cursor_is_not_continuous(tmp_path):
    s = collector(tmp_path, FakeReader(batch(continuous=False)), make_fetch()).sample(time.monotonic() + 5)
    assert not s.continuous and classify(s) == "unknown" and s.reason is Reason.continuity

def test_reader_overflow_is_overflow(tmp_path):
    s = collector(tmp_path, FakeReader(batch(overflow=True)), make_fetch()).sample(time.monotonic() + 5)
    assert s.overflow and s.reason is Reason.overflow

def test_deployment_drift_is_ownership_unknown(tmp_path):
    def drift(deadline): raise Deferral(Reason.ownership, "changed")
    s = collector(tmp_path, FakeReader(batch()), make_fetch(), recheck=drift).sample(time.monotonic() + 5)
    assert s.unknown and s.reason is Reason.ownership

@pytest.mark.parametrize("history", [
    {"active": 0},                                                   # pending map missing
    {"active": 0, "pending": None},
    {"active": 0, "pending": {"byModel": None}},
    {"active": 0, "pending": {"byModel": []}},                       # mistyped: must be an object
    {"pending": {"byModel": {}}},                                    # active missing
    {"active": None, "pending": {"byModel": {}}},
    {"active": True, "pending": {"byModel": {}}},                    # bool is not a count
])
def test_missing_or_mistyped_history_is_schema_unknown_never_zero(tmp_path, history):
    s = collector(tmp_path, FakeReader(batch()), make_fetch(history=history)).sample(time.monotonic() + 5)
    assert classify(s) == "unknown" and s.reason is Reason.schema

@pytest.mark.parametrize("logs", [{}, {"logs": None}, {"logs": {}}, {"logs": "x"}, []])
def test_missing_or_mistyped_call_logs_is_schema_unknown(tmp_path, logs):
    s = collector(tmp_path, FakeReader(batch()), make_fetch(logs=logs)).sample(time.monotonic() + 5)
    assert classify(s) == "unknown" and s.reason is Reason.schema

@pytest.mark.parametrize("health", [{"chatAdmission": {"activeHeavy": 0, "activeHealthyHeadroom": 0, "waiting": 0, "queuedBytes": 0}},
                                    {"timestamp": 1000}])
def test_missing_health_fields_are_schema_unknown(tmp_path, health):
    s = collector(tmp_path, FakeReader(batch()), make_fetch(health=health)).sample(time.monotonic() + 5)
    assert classify(s) == "unknown" and s.reason is Reason.schema

def two_samples(tmp_path, first_rows, second_rows):
    c = collector(tmp_path, FakeReader(batch(), batch()), make_fetch(rows=first_rows))
    a = c.sample(time.monotonic() + 5)
    c.fetch = make_fetch(rows=second_rows)
    return a, c.sample(time.monotonic() + 5)

def test_persisted_row_disappearing_is_continuity_unknown(tmp_path):
    a, b = two_samples(tmp_path, [{"id": "r1"}, {"id": "r2"}], [{"id": "r2"}])
    assert classify(a) == "idle" and classify(b) == "unknown" and b.reason is Reason.continuity

def test_call_log_reset_to_empty_is_continuity_unknown(tmp_path):
    a, b = two_samples(tmp_path, [{"id": "r1"}], [])
    assert classify(b) == "unknown" and b.reason is Reason.continuity

def test_in_flight_row_vanishing_without_persisting_is_continuity_unknown(tmp_path):
    a, b = two_samples(tmp_path, [{"id": "m1", "memory": True}], [])
    assert classify(b) == "unknown" and b.reason is Reason.continuity

def test_in_flight_row_that_completes_as_a_persisted_row_is_only_a_generation_change(tmp_path):
    a, b = two_samples(tmp_path, [{"id": "m1", "memory": True}], [{"id": "m1"}])
    assert classify(b) == "idle" and b.row_generation == a.row_generation + 1

def test_wall_clock_jump_between_samples_is_clock_unknown_even_when_each_bracket_is_fine(tmp_path):
    walls = iter([1.0, 1.0, 9.0, 9.0])               # (w0, w1) per sample: every bracket is 0 s wide, the clock jumps between samples
    c = collector(tmp_path, FakeReader(batch(), batch()), make_fetch(ts=1000), wall=lambda: next(walls))
    a = c.sample(time.monotonic() + 5)
    c.fetch = make_fetch(ts=9000)                     # a fresh-looking timestamp for the jumped clock
    b = c.sample(time.monotonic() + 5)
    assert classify(a) == "idle" and classify(b) == "unknown" and b.reason is Reason.clock

def test_backward_health_timestamp_is_clock_unknown(tmp_path):
    c = collector(tmp_path, FakeReader(batch(), batch()), make_fetch(ts=2000), wall=lambda: 2.0)
    a = c.sample(time.monotonic() + 5)
    c.fetch = make_fetch(ts=1000)                     # age 1000 ms is still "fresh", but time went backward
    b = c.sample(time.monotonic() + 5)
    assert classify(a) == "idle" and classify(b) == "unknown" and b.reason is Reason.clock

def test_failed_sample_is_unknown_with_the_given_reason():
    s = failed_sample("a" * 64, "t", 3, Reason.capability, 12.5)
    s.validate()
    assert classify(s) == "unknown" and s.reason is Reason.capability and s.sequence == 3 and s.sample_monotonic == 12.5

def test_sample_validate_rejects_bools_and_bad_numbers():
    for bad in (dict(active=True), dict(pending=-1), dict(sample_monotonic=math.inf), dict(admission=1.5)):
        with pytest.raises(ValueError):
            mk_sample(**bad).validate()
    mk_sample().validate()

def test_sample_json_roundtrip():
    s = mk_sample(reason=Reason.busy)
    assert Sample.from_json(s.to_json()) == s
```

Profile tests:

```python
def test_missing_profile_defers_capability(tmp_path):
    with pytest.raises(Deferral) as e: load_profile("sha256:" + "9" * 64, CONTAINER, write_profiles(tmp_path))
    assert e.value.reason is Reason.capability

def test_empty_shipped_profile_list_defers_capability():
    with pytest.raises(Deferral) as e: load_profile("sha256:" + "c" * 64, CONTAINER)
    assert e.value.reason is Reason.capability

@pytest.mark.parametrize("drop", ["arrival", "inflight", "queue"])
def test_inference_route_requires_all_three_signals(tmp_path, drop):
    routes = json.loads(json.dumps(ROUTES)); del routes[0][drop]
    with pytest.raises(Deferral) as e:
        load_profile("sha256:" + "c" * 64, CONTAINER, write_profiles(tmp_path, profile_entry(routes=routes)))
    assert e.value.reason is Reason.coverage

def test_inference_route_unreachable_needs_evidence(tmp_path):
    routes = json.loads(json.dumps(ROUTES))
    routes[0] = {"method": "POST", "path_pattern": "/v1/chat/completions", "class": "inference", "unreachable": {}}
    with pytest.raises(Deferral) as e:
        load_profile("sha256:" + "c" * 64, CONTAINER, write_profiles(tmp_path, profile_entry(routes=routes)))
    assert e.value.reason is Reason.coverage

def test_reachable_route_without_entry_defers_coverage(tmp_path):
    entry = profile_entry(reachable_routes=["POST /v1/chat/completions", "POST /v1/embeddings"])
    with pytest.raises(Deferral) as e: load_profile("sha256:" + "c" * 64, CONTAINER, write_profiles(tmp_path, entry))
    assert e.value.reason is Reason.coverage

@pytest.mark.parametrize("inspect_patch,reason", [
    ({"Config": {"Env": ["NODE_ENV=production", "LOG_LEVEL=warn"], "Entrypoint": ["node", "server.js"]}}, Reason.logging),
    ({"Config": {"Env": ["NODE_ENV=development"], "Entrypoint": ["node", "server.js"]}}, Reason.logging),
    ({"Config": {"Env": ["NODE_ENV=production"], "Entrypoint": ["sh"]}}, Reason.logging),
    ({"HostConfig": {"LogConfig": {"Type": "k8s-file"}}}, Reason.feed),
])
def test_effective_container_must_match_profile(tmp_path, inspect_patch, reason):
    with pytest.raises(Deferral) as e:
        load_profile("sha256:" + "c" * 64, {**CONTAINER, **inspect_patch}, write_profiles(tmp_path, profile_entry()))
    assert e.value.reason is reason

@pytest.mark.parametrize("missing", ["health_refresh_bound_ms", "health_cache_ms", "evidence", "api_fields"])
def test_profile_missing_required_field_defers_capability(tmp_path, missing):
    entry = profile_entry(); del entry[missing]
    with pytest.raises(Deferral) as e: load_profile("sha256:" + "c" * 64, CONTAINER, write_profiles(tmp_path, entry))
    assert e.value.reason is Reason.capability

def test_profile_selected_from_podmans_unprefixed_image_id(tmp_path):
    p = write_profiles(tmp_path, profile_entry())
    assert load_profile(canon_image_id("c" * 64), CONTAINER, p).sha256            # Podman reports the bare hex

def test_non_canonical_config_id_defers_capability_instead_of_silently_missing(tmp_path):
    with pytest.raises(Deferral) as e:
        load_profile("c" * 64, CONTAINER, write_profiles(tmp_path, profile_entry()))
    assert e.value.reason is Reason.capability

def test_valid_profile_loads_with_stable_sha(tmp_path):
    p = write_profiles(tmp_path, profile_entry())
    a, b = load_profile("sha256:" + "c" * 64, CONTAINER, p), load_profile("sha256:" + "c" * 64, CONTAINER, p)
    assert a.sha256 == b.sha256 and len(a.sha256) == 64
```

Bounded I/O tests (real `http.server` thread; the trickle server never completes a read chunk of 64 KiB and the socket timeout never fires because bytes keep arriving):

```python
class Trickle(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.send_header("Content-Length", "100000"); self.end_headers()
        try:
            for _ in range(100000):
                self.wfile.write(b"x"); self.wfile.flush(); time.sleep(0.2)
        except OSError:
            pass
    do_POST = do_GET
    def log_message(self, *a): pass

class Big(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.send_header("Content-Length", str(9 * 1024 * 1024)); self.end_headers()
        self.wfile.write(b"x" * (9 * 1024 * 1024))
    def log_message(self, *a): pass

def serve(handler):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"

def test_trickling_get_stops_at_aggregate_deadline():
    srv, url = serve(Trickle)
    start = time.monotonic()
    with pytest.raises(Deferral) as e:
        bounded_get(url, "/x", "tok", start + 1.0)
    assert e.value.reason is Reason.io and time.monotonic() - start < 2.5
    srv.shutdown()

def test_trickling_login_stops_at_aggregate_deadline():
    srv, url = serve(Trickle)
    start = time.monotonic()
    with pytest.raises(Deferral) as e:
        bounded_login(url, "pw", start + 1.0)
    assert e.value.reason is Reason.io and time.monotonic() - start < 2.5
    srv.shutdown()

def test_oversize_body_is_rejected():
    srv, url = serve(Big)
    with pytest.raises(Deferral) as e:
        bounded_get(url, "/x", "tok", time.monotonic() + 10)
    assert e.value.reason is Reason.io
    srv.shutdown()

def test_existing_omniroute_helpers_keep_their_signatures():
    import inspect, pylib.omniroute as o
    assert list(inspect.signature(o._login).parameters) == ["base_url", "password"]
    assert list(inspect.signature(o._request).parameters) == ["method", "url", "session_token", "payload"]
```

Journal parsing and reader tests:

```python
def test_parse_journal_line_extracts_method_and_path_and_flags_oversize(tmp_path):
    prof = load_profile("sha256:" + "c" * 64, CONTAINER, write_profiles(tmp_path, profile_entry()))
    line = json.dumps({"MESSAGE": json.dumps({"method": "POST", "path": "/v1/chat/completions"})}).encode()
    assert parse_journal_line(line, prof) == JournalRecord("POST", "/v1/chat/completions")
    assert parse_journal_line(b"x" * 70000, prof) is OVERFLOW
    assert parse_journal_line(json.dumps({"MESSAGE": "not json"}).encode(), prof) is None
```

`JournalctlReader` tests use a stub `journalctl` script on a temp `PATH` (a shell script printing JSON lines from a file; seed capture returns one record with `__CURSOR=s1`, `_BOOT_ID=b1`): (a) seed then a follow line produces a record and `barrier_seen`; (b) a changed `_BOOT_ID` -> `continuous=False`; (c) a seed capture that prints nothing -> `ready=False`; (d) `terminate()` kills the child (poll `/proc/<pid>` gone within 2 s); (e) every child is started with the reader's `env=` (a fake `popen` records `kw["env"]` and raises `OSError`; `poll` then returns `ready=False` instead of raising).

Clock tolerance: `test_measure_clock_tolerance_ms_is_ceiling_plus_resolution` with fake `wall`/`clock` sequences producing a 0.0013 s maximum bracket discrepancy and 1 ms resolution -> `3`.

- [ ] **Step 2: Run, verify FAIL.**
- [ ] **Step 3: Implement.**

```python
# pylib/omniroute_activity.py (key parts; remaining small helpers follow the tests above)
import http.client, json, math, socket, subprocess, threading, time, urllib.parse
from dataclasses import dataclass, asdict
import hashlib
from pathlib import Path
from pylib.omniroute_update import Deferral, Reason

PROFILE_PATH = Path(__file__).with_name("omniroute_activity_profiles.json")
MAX_BODY = 8 * 1024 * 1024
OVERFLOW = object()


def _bounded_request(base_url, method, path, headers, body, deadline) -> tuple[int, list[str], bytes]:
    """One HTTP exchange that cannot outlive `deadline`, even against a trickling server.

    A watchdog Timer shuts the socket down at the deadline, which unblocks any in-progress recv();
    socket timeouts alone cannot do that when bytes keep arriving."""
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise Deferral(Reason.io, "deadline reached before request")
    parsed = urllib.parse.urlsplit(base_url)
    conn = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=min(remaining, 5))

    def expire():
        try:
            if conn.sock is not None:
                conn.sock.shutdown(socket.SHUT_RDWR)
            conn.close()
        except OSError:
            pass

    timer = threading.Timer(remaining, expire)
    timer.start()
    try:
        conn.request(method, path, body=body, headers=headers)
        resp = conn.getresponse()
        data = resp.read(MAX_BODY + 1)
        if len(data) > MAX_BODY:
            raise Deferral(Reason.io, "response exceeds the size limit")
        if time.monotonic() >= deadline:
            raise Deferral(Reason.io, "deadline reached")
        return resp.status, resp.headers.get_all("Set-Cookie") or [], data
    except (OSError, http.client.HTTPException) as exc:
        raise Deferral(Reason.io, type(exc).__name__) from exc
    finally:
        timer.cancel()
        conn.close()


def bounded_login(base_url: str, password: str, deadline: float) -> str:
    status, cookies, _ = _bounded_request(base_url, "POST", "/api/auth/login",
        {"Content-Type": "application/json"}, json.dumps({"password": password}).encode(), deadline)
    if status != 200:
        raise Deferral(Reason.auth, f"login HTTP {status}")
    for header in cookies:
        for part in header.split(";"):
            if part.strip().startswith("auth_token="):
                return part.strip().removeprefix("auth_token=")
    raise Deferral(Reason.auth, "no session cookie")


def bounded_get(base_url: str, path: str, token: str, deadline: float, _retry: bool = True) -> bytes:
    status, _, data = _bounded_request(base_url, "GET", path, {"Cookie": f"auth_token={token}"}, None, deadline)
    if status == 401:
        raise Deferral(Reason.auth, "session rejected")          # caller re-logins once via Collector (below)
    if status != 200:
        raise Deferral(Reason.io, f"HTTP {status}")
    return data
```

  The production `fetch` closure built by Task 5's `observe` is: login once (token kept in a list cell), call `bounded_get`, and on `Deferral(Reason.auth)` log in again once within the same `deadline` and retry once; a second failure propagates. Cookies and bodies never touch disk.

  `check_freshness`: `age = wall_ms - timestamp_ms; return None if 0 <= age <= cache_ms + refresh_ms + tolerance_ms else Reason.age`.

  **Profile schema** (`pylib/omniroute_activity_profiles.json`, entries as in `profile_entry()` above). `profile_sha256` is the SHA256 of the entry's canonical JSON. `load_profile` verifies, in this order: (0) `config_id` is the canonical `sha256:<64 hex>` form, else `Reason.capability`; (1) an entry exists for the running config id (the config id is a content hash covering all layer diff_ids, so `artifacts` is provenance only), else `Reason.capability`; (2) `health_cache_ms`, `health_refresh_bound_ms`, `evidence`, `api_fields`, `logger` present and the two numbers positive integers, else `Reason.capability`; (3) every `reachable_routes` item has a `routes` entry, and every route with `"class": "inference"` has **independent** `arrival`, `inflight` and `queue` objects each carrying `signal` and `evidence`, or an `unreachable` object carrying `evidence`; every `"class": "probe"` route carries `evidence`; otherwise `Reason.coverage`; (4) the container's effective env value for each `env_allowlist` name (`env_allowlist` maps name to the expected value), `Config.Entrypoint`, and the `LOG_LEVEL`-style variable named by `logger.level_env` (value must not be less verbose than `min_level`, ordered `debug < info < warn < error`; a higher index than `min_level` defers) match the profile, else `Reason.logging`; (5) `HostConfig.LogConfig.Type == "journald"`, else `Reason.feed`.

  **Probe rule (spec ORU-R1-006):** a journal record whose method/path matches a `probe` route (health, auth/login, dashboard, OAuth, provider probes) is consumed but never bumps `arrival_generation`; a record matching an `inference` route bumps it by one; an HTTP record matching nothing sets a coverage gap and the sample is `unknown` with `Reason.coverage`. Before matching, `Collector` strips the query (`?...`) and the ` | ...` suffix.

  `parse_journal_line`: lines longer than 64 KiB return `OVERFLOW`; otherwise `json.loads` the line, take `MESSAGE`, `json.loads` that, read `method`/`path` through the profile's `logger.fields` names (the real field names come from the characterization, Task 8 Step 3); non-JSON or missing keys return `None`.

  `Collector.__init__` stores its arguments and starts `sequence = arrival_gen = row_gen = 0`, `rows = None` (the previous sample's `(persisted, memory)` row-hash frozensets) and `last = None` (the previous sample's `(monotonic, wall, health timestamp)`). `Collector.sample(deadline, force_barrier=False)`:

```python
    def sample(self, deadline, force_barrier=False):
        self.sequence += 1
        s = dict(schema=1, profile_sha256=self.profile.sha256, container_id=self.deployment.container_id,
                 started_at=self.deployment.started_at, boot_id="", sequence=self.sequence,
                 sample_monotonic=self.clock(), arrival_generation=self.arrival_gen, row_generation=self.row_gen,
                 active=0, pending=0, admission=0, health_timestamp_ms=None, health_age_ms=None,
                 feed_ready=False, barrier_seen=False, continuous=False, overflow=False,
                 unknown=True, reason=Reason.io)
        try:
            self.recheck(deadline)                                       # inventory + fingerprint, shared budget
            b = self.reader.poll(deadline, force_barrier)
            s.update(boot_id=b.boot_id, feed_ready=b.ready, barrier_seen=b.barrier_seen,
                     continuous=b.continuous, overflow=b.overflow)
            gap = False
            for rec in b.records:
                kind = self.profile.classify_route(rec.method, rec.path)
                if kind == "inference":
                    self.arrival_gen += 1
                elif kind is None:
                    gap = True
            s["arrival_generation"] = self.arrival_gen
            t0, w0 = self.clock(), self.wall()                           # exactly two wall() reads per sample
            health = json.loads(self.fetch("/api/monitoring/health", deadline))
            t1, w1 = self.clock(), self.wall()
            history = json.loads(self.fetch("/api/usage/history", deadline))
            logs = json.loads(self.fetch("/api/usage/call-logs?limit=1000&offset=0", deadline))
            f = self.profile.api_fields
            s["admission"] = sum(_count(_ptr(health, p)) for p in f["admission"])
            s["active"] = _count(_ptr(history, f["active"]))
            s["pending"] = sum(_count(v) for v in _require(history, f["pending_by_model"], dict).values())
            ts = _count(_ptr(health, f["health_timestamp"]))
            persisted, memory = set(), set()
            for row in _require(logs, f["rows"], list):                  # a missing/null/mistyped source is schema unknown, never zero rows
                digest = hashlib.sha256(str(row[f["row_id"]]).encode()).hexdigest()
                (memory if row.get(f["row_memory_flag"]) else persisted).add(digest)
            current = (frozenset(persisted), frozenset(memory))
            # Explained changes only: a previously seen persisted row must still be there (a page under 1000 rows cannot
            # evict it), and an in-flight row must still be in flight or have become a persisted row.
            lost = self.rows is not None and bool((self.rows[0] - current[0]) or (self.rows[1] - current[0] - current[1]))
            if self.rows is not None and current != self.rows:
                self.row_gen += 1
            self.rows = current
            s.update(row_generation=self.row_gen, health_timestamp_ms=ts)
            wall_ms = int(w1 * 1000)
            s["health_age_ms"] = max(0, wall_ms - ts)
            if len(persisted) >= 1000:
                s.update(overflow=True)
            previous, self.last = self.last, (t1, w1, ts)
            # counts above stay visible so busy still wins; the reasons below are in priority order
            if abs((w1 - w0) - (t1 - t0)) * 1000 > self.tolerance_ms or (t1 - t0) * 1000 > 5000:
                raise Deferral(Reason.clock, "wall/monotonic bracket mismatch")
            if previous is not None:
                slack_ms = self.tolerance_ms + 10 * (t1 - previous[0])  # tolerance plus 1% drift over the interval
                if abs((w1 - previous[1]) - (t1 - previous[0])) * 1000 > slack_ms or ts < previous[2]:
                    raise Deferral(Reason.clock, "clock jumped or health timestamp went backward between samples")
            if (reason := check_freshness(ts, wall_ms, self.profile.health_cache_ms,
                                          self.profile.health_refresh_bound_ms, self.tolerance_ms)):
                raise Deferral(reason, "stale or future health timestamp")
            for cond, reason in ((b.overflow or s["overflow"], Reason.overflow), (not b.continuous or lost, Reason.continuity),
                                 (not (b.ready and b.barrier_seen), Reason.feed), (gap, Reason.coverage)):
                if cond:
                    raise Deferral(reason, "")
            busy = s["active"] > 0 or s["pending"] > 0 or s["admission"] > 0
            s.update(unknown=False, reason=Reason.busy if busy else Reason.ready)
        except Deferral as exc:
            s.update(unknown=True, reason=exc.reason)
        except (ValueError, KeyError, TypeError, AttributeError):
            s.update(unknown=True, reason=Reason.schema)
        return Sample(**s)
```

  with module helpers (a missing source is a sentinel, so it can never be mistaken for a legitimate JSON `null` or zero):

```python
_MISSING = object()


def _ptr(doc, pointer):
    """RFC 6901 walk; `_MISSING` when any step is absent (a JSON null stays None)."""
    cur = doc
    for part in [p.replace("~1", "/").replace("~0", "~") for p in pointer.split("/")[1:]]:
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return _MISSING
    return cur


def _count(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("not a non-negative count")
    return value


def _require(doc, pointer, kind):
    value = _ptr(doc, pointer)
    if not isinstance(value, kind):
        raise ValueError("required source is missing or mistyped")
    return value


def failed_sample(container_id, started_at, sequence, reason, now) -> Sample:
    return Sample(schema=1, profile_sha256="0" * 64, container_id=container_id, started_at=started_at, boot_id="",
                  sequence=sequence, sample_monotonic=now, arrival_generation=0, row_generation=0, active=0, pending=0,
                  admission=0, health_timestamp_ms=None, health_age_ms=None, feed_ready=False, barrier_seen=False,
                  continuous=False, overflow=False, unknown=True, reason=reason)
```

  In `test_freshness_boundary_1750` the fake `wall` returns the same value for both bracket reads so the bracket check passes; in `test_clock_bracket_mismatch...` the monotonic clock is constant while wall jumps 8 s so the bracket check fails first. The inter-sample tests use a constant monotonic clock and a wall clock that is read exactly twice per sample.

  `JournalctlReader` algorithm (all via pipes/memory, no export files; **every child, finite or follower, is started with `popen(..., env=self.env, close_fds=True)`** so it sees the same scrubbed backend environment, and a child that cannot start yields `ready=False`, never an exception): on first `poll` run `journalctl --output=json --no-pager -n 1 CONTAINER_ID_FULL=<id>` to seed `__CURSOR`/`_BOOT_ID` (nothing printed -> `ready=False`); start the follower `journalctl --output=json --no-pager --follow --after-cursor=<seed> CONTAINER_ID_FULL=<id>` with `Popen(..., stdout=PIPE, close_fds=True)` and nonblocking `selectors` reads; each `poll` first captures a fresh finite latest-cursor barrier (same `-n 1` command) and reads until the follower has consumed that cursor (retained cursor set bounded to 10000, more -> `overflow=True`) or `deadline` passes (`barrier_seen=False`); a `_BOOT_ID` different from the seed, or a vanished cursor, sets `continuous=False`; a barrier equal to the seed or any retained cursor counts as consumed; a line over 64 KiB sets `overflow=True`. `terminate()` sends TERM, waits <= 2 s, KILLs and reaps the follower.

  `measure_clock_tolerance_ms(wall, clock, samples=20)`: for each of 20 iterations read `w0, c0, w1, c1 = wall(), clock(), wall(), clock()` and track the maximum of `abs((w1-w0)-(c1-c0))`; resolution is the smallest positive delta seen between consecutive `clock()` reads; return `math.ceil((max_discrepancy + resolution) * 1000)`.

  `tests/fixtures/fake_omniroute.py` (created here; reused by Tasks 6 and 8) — a stdlib server driven by a JSON control file so tests can change state between requests:

```python
#!/usr/bin/env python3
"""Fake OmniRoute admin API (mechanics only; shapes mirror the fixture profile's api_fields)."""
import argparse, json, sys, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")   # the Task 8 proof image passes 0.0.0.0 so the published port is reachable
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--state", required=True)      # JSON: {"active":0,"waiting":0,"by_model":{},"rows":[],"cache_ms":1000}
    args = ap.parse_args()

    def state() -> dict:
        try:
            return json.load(open(args.state))
        except (OSError, ValueError):
            return {}

    class H(BaseHTTPRequestHandler):
        def _send(self, code, body, headers=()):
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            for k, v in headers:
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            if self.path == "/api/auth/login":
                self.rfile.read(int(self.headers.get("Content-Length", 0)))
                print(json.dumps({"method": "POST", "path": self.path}), flush=True)
                return self._send(200, {"ok": True}, [("Set-Cookie", "auth_token=fixture; Path=/")])
            self._send(404, {"error": "not found"})

        def do_GET(self):
            s = state()
            if self.path.startswith("/api/monitoring/health"):
                cache = int(s.get("cache_ms", 1000))
                ts = int(time.time() * 1000) // cache * cache       # advances once per cache window
                return self._send(200, {"timestamp": ts, "chatAdmission": {"activeHeavy": 0,
                    "activeHealthyHeadroom": 0, "waiting": s.get("waiting", 0), "queuedBytes": 0}})
            if self.path.startswith("/api/usage/history"):
                by_model = s.get("by_model", {})
                return self._send(200, {"active": s.get("active", 0),
                    "pending": {"byModel": by_model, "byAccount": {"a": sum(by_model.values())} if by_model else {}}})
            if self.path.startswith("/api/usage/call-logs"):
                return self._send(200, {"logs": s.get("rows", [])})
            self._send(404, {"error": "not found"})

        def log_message(self, *a):            # stdout carries only the JSON access lines above
            pass

    print(json.dumps({"event": "started"}), flush=True)     # a journal seed record for the observer (no method/path: ignored as an arrival)
    ThreadingHTTPServer((args.host, args.port), H).serve_forever()


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run, verify PASS** (`uv run pytest tests/test_omniroute_activity.py -v`).
- [ ] **Step 5: Validate; commit if approved.** `make validate && make test`. The disposable characterization that fills real profile fields (`reachable_routes`, `logger.fields`, `api_fields`, cache/refresh bounds) lives in **Task 8 Step 3**, after the isolated-backend helper exists; nothing in Tasks 3-7 depends on it, and with no reviewed entry the updater honestly defers `capability`.

---

### Task 4: Idle gate, override validation and the `overrides`/`deadline` CLI

**Files:**
- Modify: `pylib/omniroute_activity.py` (gate, `validate_overrides`), `pylib/omniroute_cli.py` (`cmd_overrides`, `cmd_deadline`, registration), `tests/test_omniroute_activity.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: `guarded`, `write_private`, `UpdateError` (Task 1); `Sample`, `classify` (Task 3).
- Produces: `validate_overrides(idle: str | None, max_wait: str | None) -> tuple[int, int]` — `None` = unset (defaults 300/10800); `""` invalid; raises `UpdateError`.
- Produces: `IdleGate(min_wait: int, deadline: float)` — **in memory only**, owned by the observer (Task 5); there is no gate file and no JSON persistence. `step(now: float, state: Literal["idle","busy","unknown"], *, arrival_generation: int, row_generation: int, health_timestamp_ms: int | None) -> Literal["wait","resample","ready","defer-deadline","defer-unknown"]` and `remaining(now) -> float`.
- Produces CLI `omniroute-update overrides [--idle S] [--max-wait S]`: prints exactly `min_wait=<int>\nmax_deferred_wait=<int>\n` and exits 0; on invalid input prints a sanitized message to stderr, **nothing to stdout**, exit 1. Consumer: the script's allowlist `read` loop (Task 6).
- Produces CLI `omniroute-update deadline --file FILE [--init SECONDS]` (the only shared wait-budget state). `--init S` (integer >= 1) writes `repr(time.monotonic() + S)` to `FILE` (0600 via `write_private`; Linux `CLOCK_MONOTONIC` is shared across processes), prints nothing, exit 0. Without `--init`: reads `FILE`; `now >= deadline` prints `deferred: deadline` and exits 75 (exclusive); otherwise prints `remaining=<ceil seconds>` and exits 0; an unreadable or non-numeric `FILE` prints a sanitized message to stderr and exits 1. Consumers: `observe --deadline-file` (Task 5), the script's wait loop and the commit worker (Task 6).

- [ ] **Step 1: Failing tests** (fake clock, no sleeping). Quiet restarts on the next nonbusy sample after activity (spec ORU-R1-007), so after an activity-bearing sample at second 200 the new window starts at the next nonbusy sample (504), `resample` is first returned at 804, and 505 is still `wait`.

```python
import argparse
import pytest
from pylib.omniroute_activity import IdleGate, validate_overrides
from pylib.omniroute_cli import cmd_overrides, cmd_deadline
from pylib.omniroute_update import UpdateError

def feed(g, now, st, a=0, r=0, h=1):
    return g.step(now, st, arrival_generation=a, row_generation=r, health_timestamp_ms=h)

def test_quiet_starts_only_after_health_timestamp_advance():
    g = IdleGate(300, 10800)                         # the wait started at monotonic 0
    assert feed(g, 0, "idle", h=1000) == "wait"      # first sample = baseline, not an advance
    assert feed(g, 400, "idle", h=1000) == "wait"    # reused cache; quiet has NOT started
    assert feed(g, 405, "idle", h=2000) == "wait"    # advance observed; quiet starts here
    assert feed(g, 704, "idle", h=3000) == "wait"
    assert feed(g, 705, "idle", h=4000) == "resample"
    assert feed(g, 706, "idle", h=5000) == "ready"   # confirmation sample, generations unchanged

def test_arrival_between_two_zero_count_samples_resets_quiet():
    g = IdleGate(300, 10800)
    feed(g, 0, "idle", h=1); feed(g, 5, "idle", h=2)           # quiet starts at 5
    assert feed(g, 200, "idle", a=1, h=3) == "wait"            # arrival_generation changed with zero counts: quiet cleared
    assert feed(g, 504, "idle", a=1, h=4) == "wait"            # next nonbusy sample starts the new window at 504
    assert feed(g, 505, "idle", a=1, h=5) == "wait"            # one second of quiet, not 300
    assert feed(g, 803, "idle", a=1, h=6) == "wait"            # 299 s
    assert feed(g, 804, "idle", a=1, h=7) == "resample"        # 300 s: complete resample before commit
    assert feed(g, 805, "idle", a=1, h=8) == "ready"

def test_row_generation_change_resets_quiet():
    g = IdleGate(300, 10800)
    feed(g, 0, "idle", h=1); feed(g, 5, "idle", h=2)
    assert feed(g, 200, "idle", r=1, h=3) == "wait"
    assert feed(g, 504, "idle", r=1, h=4) == "wait"
    assert feed(g, 505, "idle", r=1, h=5) == "wait"
    assert feed(g, 804, "idle", r=1, h=6) == "resample"

def test_activity_at_299_resets_but_deadline_not_extended():
    g = IdleGate(300, 10800)
    feed(g, 0, "idle", h=1); feed(g, 1, "idle", h=2)           # quiet starts at 1
    assert feed(g, 299.5, "busy", h=3) == "wait"
    assert g.quiet_since is None and g.deadline == 10800
    assert feed(g, 300, "idle", h=4) == "wait"                 # new window starts at 300
    assert feed(g, 599, "idle", h=5) == "wait"
    assert feed(g, 600, "idle", h=6) == "resample"
    assert feed(g, 10800, "idle", h=7) == "defer-deadline"     # deadline unchanged by the activity

def test_resample_that_finds_activity_resumes_waiting():
    g = IdleGate(300, 10800)
    feed(g, 0, "idle", h=1); feed(g, 1, "idle", h=2)
    assert feed(g, 301, "idle", h=3) == "resample"
    assert feed(g, 302, "busy", h=4) == "wait"
    assert g.quiet_since is None and g.resampling is False
    assert feed(g, 303, "idle", h=5) == "wait"                 # new window starts at 303
    assert feed(g, 603, "idle", h=6) == "resample"

def test_deadline_exclusive_and_wins_over_ready():
    g = IdleGate(300, 1000); feed(g, 0, "idle", h=1)
    assert feed(g, 999.9, "idle", h=2) == "wait"
    assert feed(g, 1000, "idle", h=9) == "defer-deadline"

def test_unknown_defers_immediately():
    g = IdleGate(300, 10800)
    assert feed(g, 5, "unknown") == "defer-unknown"

@pytest.mark.parametrize("idle,mx", [("", None), (None, ""), ("0", None), ("abc", None),
    ("301", "300"), ("2147483648", None), ("-1", None), (" 5", None), ("5 ", None), ("٣", None)])
def test_invalid_overrides_rejected(idle, mx):
    with pytest.raises(UpdateError): validate_overrides(idle, mx)

def test_valid_defaults_and_boundaries():
    assert validate_overrides(None, None) == (300, 10800)
    assert validate_overrides("60", "600") == (60, 600)                  # the spec acceptance values
    assert validate_overrides("2147483647", "2147483647") == (2147483647, 2147483647)

def test_overrides_cli_prints_nothing_on_stdout_when_invalid(capsys):
    assert cmd_overrides(argparse.Namespace(idle="", max_wait=None)) == 1
    assert capsys.readouterr().out == ""

def test_overrides_cli_prints_exact_lines(capsys):
    assert cmd_overrides(argparse.Namespace(idle="60", max_wait="600")) == 0
    assert capsys.readouterr().out == "min_wait=60\nmax_deferred_wait=600\n"

def test_deadline_file_init_and_exclusive_check(tmp_path, capsys):
    f = str(tmp_path / "deadline")
    assert cmd_deadline(argparse.Namespace(file=f, init=10), clock=lambda: 100.0) == 0
    assert (tmp_path / "deadline").stat().st_mode & 0o777 == 0o600
    capsys.readouterr()
    assert cmd_deadline(argparse.Namespace(file=f, init=None), clock=lambda: 109.5) == 0
    assert capsys.readouterr().out == "remaining=1\n"
    assert cmd_deadline(argparse.Namespace(file=f, init=None), clock=lambda: 110.0) == 75     # now >= deadline
    assert capsys.readouterr().out == "deferred: deadline\n"

def test_deadline_unreadable_file_is_an_error(tmp_path, capsys):
    assert cmd_deadline(argparse.Namespace(file=str(tmp_path / "missing"), init=None)) == 1
    assert capsys.readouterr().out == ""
```

- [ ] **Step 2: Run, verify FAIL** (`uv run pytest tests/test_omniroute_activity.py -v -k "quiet or deadline or overrides"`).
- [ ] **Step 3: Implement.**

```python
class IdleGate:
    def __init__(self, min_wait: int, deadline: float):
        self.min_wait, self.deadline = min_wait, deadline
        self.base_ts = None
        self.advanced = False
        self.quiet_since = None
        self.quiet_gens = None
        self.resampling = False

    def remaining(self, now): return max(0.0, self.deadline - now)

    def step(self, now, state, *, arrival_generation, row_generation, health_timestamp_ms):
        if now >= self.deadline:                       # exclusive; checked first
            return "defer-deadline"
        if state == "unknown":
            return "defer-unknown"
        gens = (arrival_generation, row_generation)
        if health_timestamp_ms is not None:
            if self.base_ts is None:
                self.base_ts = health_timestamp_ms
            elif health_timestamp_ms > self.base_ts:
                self.advanced = True
        if state == "busy" or (self.quiet_gens is not None and gens != self.quiet_gens):
            self.quiet_since = self.quiet_gens = None
            self.resampling = False
            return "wait"                              # the activity-bearing sample never starts a window
        if not self.advanced:                          # spec: quiet needs an observed health advance
            return "wait"
        if self.quiet_since is None:                   # quiet begins on the next complete nonbusy sample
            self.quiet_since, self.quiet_gens = now, gens
            return "wait"
        if now - self.quiet_since < self.min_wait:
            return "wait"
        if not self.resampling:                        # spec: complete resample before commit
            self.resampling = True
            return "resample"
        return "ready"
```

  A backward health timestamp never reaches the gate as an idle sample: the `Collector` (Task 3) turns it into an `unknown`/`clock` sample, which the gate defers on.

  `validate_overrides`: `None` -> default; otherwise require `isinstance(v, str) and v.isascii() and v.isdecimal()`, `len(v) <= 10` **before** `int()`, then `1 <= int(v) <= 2147483647`; finally `max >= min`. Handlers in `pylib/omniroute_cli.py` (all handlers live there; `guarded` from Task 1 maps `Deferral` to `deferred: <reason>` + 75 and `UpdateError` to a sanitized stderr line + 1):

```python
@guarded
def cmd_overrides(args) -> int:
    min_wait, max_wait = validate_overrides(args.idle, args.max_wait)     # output is built only after validation
    print(f"min_wait={min_wait}\nmax_deferred_wait={max_wait}")
    return 0


@guarded
def cmd_deadline(args, clock=time.monotonic) -> int:
    if args.init is not None:
        if args.init < 1:
            raise UpdateError("--init must be a positive integer")
        write_private(args.file, repr(clock() + args.init))
        return 0
    try:
        deadline = float(Path(args.file).read_text())
    except (OSError, ValueError) as exc:
        raise UpdateError("deadline file is unreadable") from exc
    now = clock()
    if now >= deadline:
        print("deferred: deadline")
        return 75
    print(f"remaining={math.ceil(deadline - now)}")
    return 0
```
- [ ] **Step 4: Run, verify PASS.**
- [ ] **Step 5: Register both subcommands in `pylib/omniroute_cli.py::register` (`overrides --idle --max-wait`; `deadline --file FILE [--init N]`, `--init` as `type=int`) and add `tests/test_cli.py` cases: both subcommands parse through `build_parser().parse_args([...])`, exit codes, and empty stdout on invalid overrides.**
- [ ] **Step 6: Validate; commit if approved.**

---

### Task 5: Observer supervisor (owns the idle gate) and canonical lock path

**Files:**
- Create: `pylib/omniroute_observer.py`, `tests/test_omniroute_observer.py`
- Modify: `pylib/omniroute_cli.py` (add `lock-path` and `observe`), `tests/test_cli.py`

**Interfaces:**
- Consumes: `Collector`, `JournalctlReader`, `Sample`, `IdleGate`, `classify`, `failed_sample`, `load_profile`, `bounded_login`/`bounded_get`, `measure_clock_tolerance_ms` (Tasks 3-4); `recheck`, `with_deadline`, `scrubbed_runner`, `backend_env`, `write_private`, `check_private_dir` (Tasks 1-2). The deployment's `image_id` in `deployment.json` is already the canonical `sha256:<64 hex>` (Task 2), so it is passed to `load_profile` unchanged.
- Produces: `lock_path(run: Runner, container_name: str = "omniroute", uid: int | None = None) -> Path` (`uid` only feeds the hash input and defaults to `os.geteuid()`; ownership checks always use the real effective UID) and CLI `omniroute-update lock-path [--container omniroute]`: prints one absolute path. Derivation: `podman info` -> `os.path.realpath` of graphRoot and runRoot; hash = SHA256 of `"\0".join([str(uid), graphRoot, runRoot, container_name])`; directory `<runRoot>/llm-env-update-locks`; file `omniroute-update-<hex>.lock`. **Never unlinked**, not by `lock-path` and not by any script. On `Deferral` the CLI prints `deferred: <reason>` to **stderr** (stdout carries only the path) and exits 75; `acquire_update_lock` in `tools/lib.sh` (Task 6) replays it.
- Produces: `lease_alive(pid: int, start_tick: str) -> bool` (field 22 of `/proc/<pid>/stat`, read after the last `)`; PID reuse with a different tick, or a zombie, is not alive).
- Produces: `observe_loop(sample_fn, gate, latest_path, interval, stop, lifetime_end, clock=time.monotonic)` and `supervise(work_fn, terminate, lease_pid, lease_tick, lifetime, clock=time.monotonic)`.
- Produces CLI `omniroute-update observe --dir DIR --config FILE --compose FILE --password-file FILE --lease-pid P --lease-start-tick T --lifetime SECONDS --interval 5 --min-wait N --deadline-file FILE` — the **persistent supervisor, which owns the `Collector` and the `IdleGate`**. `DIR` is the preflight artifact directory (Task 2). `observe` loads `DIR/deployment.json` (container id, port, project, provider, canonical `image_id`) and `DIR/target.json`, builds `profile = load_profile(deployment["image_id"], target)`, the `JournalctlReader(container_id, env=backend_env(os.environ))`, `fetch` = login-once-then-`bounded_get` against `http://127.0.0.1:<port>` (password read from `--password-file`: 0600, written by the script from the config, never argv or environment, deleted by the script's cleanup; one re-login on `Reason.auth` within the same deadline), `recheck = lambda deadline: recheck(DIR, config, compose, os.environ, with_deadline(scrubbed_runner(os.environ), deadline))`, `tolerance_ms = measure_clock_tolerance_ms(time.time, time.monotonic)`, and `gate = IdleGate(min_wait, float(<deadline file>))`. A profile or construction `Deferral` is **not a crash**: `sample_fn` then returns `failed_sample(...)` with that reason at every interval.
- **Verdict file contract.** Every `interval` seconds `observe_loop` takes one sample, feeds the gate, and on a `resample` verdict immediately takes a second sample with `force_barrier=True` (a fresh finite-cursor barrier: the spec's complete resample) and feeds the gate again. It then writes `DIR/latest.json` atomically (`write_private`, 0600): `{"sequence": <int, strictly increasing>, "verdict": "wait"|"ready"|"defer-deadline"|"defer-unknown", "reason": <closed Reason value, "deadline" for defer-deadline>, "sample": <Sample JSON, Task 3 schema>}`. The script only polls this file (Task 6); no signal and no second process computes the verdict. If the worker thread dies on an unexpected exception, `latest.json` stops advancing and the script's stale rule defers `feed`.
- **Responsiveness contract:** the main thread runs only the 0.2 s control loop (SIGTERM flag, `lease_alive`, lifetime); sampling runs in one daemon worker thread whose every external command goes through `with_deadline(run, sample_deadline)` with `sample_deadline = min(sample_start + 5, lifetime_end)`. The supervisor is its own session and process-group leader (Bash starts it under `setsid`, Task 6). On parent mismatch, lifetime expiry or `SIGTERM` the main thread runs `terminate()` on the reader, ignores SIGTERM in itself, sends TERM to its process group, waits <= 2 s while reaping, KILLs the remaining members and exits, so even a hung metadata command (a `podman` that sleeps) cannot keep anything alive past 5 s. It never receives the update lock FD (Task 6 closes it with `9>&-`).

- [ ] **Step 1: Failing tests**

```python
# tests/test_omniroute_observer.py (helpers first)
import fcntl, json, os, signal, subprocess, sys, threading, time
from pathlib import Path
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
from pylib.omniroute_activity import IdleGate
from pylib.omniroute_observer import lock_path, lease_alive, observe_loop
from pylib.omniroute_update import Deferral, Reason, preflight, write_preflight_artifacts
from omniroute_fakes import preflight_world, PASSWORD, mk_sample, fake_backend, omni

def make_stub_path(tmp, **scripts):
    d = tmp / "stubs"; d.mkdir(exist_ok=True)
    for name, body in scripts.items():
        p = d / name; p.write_text(f"#!/usr/bin/env bash\n{body}\n"); p.chmod(0o755)
    return d

def proc_fields(pid):
    return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()   # [state, ppid, pgrp, ...]

def pid_alive(pid):
    try:
        return proc_fields(pid)[0] != "Z"
    except OSError:
        return False

def all_pids():
    return [int(p.name) for p in Path("/proc").iterdir() if p.name.isdigit()]

def any_process_in_group(pgid):
    for pid in all_pids():
        try:
            f = proc_fields(pid)
        except OSError:
            continue
        if int(f[2]) == pgid and f[0] != "Z":
            return True
    return False

def descendants(root):
    found, frontier = set(), {root}
    while frontier:
        nxt = set()
        for pid in all_pids():
            try:
                if int(proc_fields(pid)[1]) in frontier and pid not in found:
                    nxt.add(pid)
            except OSError:
                pass
        found |= nxt; frontier = nxt
    return found

def start_tick(pid):
    return proc_fields(pid)[19]                                   # field 22 of /proc/<pid>/stat

def prepared_observe_args(tmp, pid, tick, lifetime, interval="0.2"):
    out = tmp / "art"; out.mkdir(mode=0o700)
    cfg, cp, comp, prov, env, run = preflight_world(tmp)
    dep, snap, fp, eff = preflight(cfg, cp, comp, env, run, provider=prov)
    write_preflight_artifacts(str(out), dep, snap, fp, eff, snap.docs[dep.container_id])
    pw = tmp / "pw"; pw.write_text(PASSWORD); pw.chmod(0o600)
    dl = tmp / "deadline"; dl.write_text(repr(time.monotonic() + lifetime + 60))
    return ["uv", "run", str(REPO_ROOT / "llmenv.py"), "omniroute-update", "observe", "--dir", str(out), "--config", cp,
            "--compose", comp, "--password-file", str(pw), "--lease-pid", str(pid), "--lease-start-tick", tick,
            "--lifetime", str(lifetime), "--interval", interval, "--min-wait", "1", "--deadline-file", str(dl)]

def start_supervisor(tmp, parent_pid, lifetime, interval, stubs):
    argv = prepared_observe_args(tmp, parent_pid, start_tick(parent_pid), lifetime, interval)
    env = {**os.environ, "PATH": f"{stubs}:{os.environ['PATH']}"}
    return subprocess.Popen(argv, env=env, cwd=REPO_ROOT, start_new_session=True), tmp / "art" / "latest.json"

def wait_for(pred, timeout):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False

def read_seq(path):
    try:
        return json.loads(path.read_text())["sequence"]
    except (OSError, ValueError, KeyError):
        return None
```

  Lock and lease tests (`podman info` answered by `fake_backend`, whose `graphRoot`/`runRoot` are `/g` and `/r`; the tests point them at `tmp_path` by passing their own scripted `run` that returns `{"store": {"graphRoot": str(tmp_path / "g"), "runRoot": str(tmp_path / "run")}}`):

```python
def info_run(tmp_path, run_name="run"):
    (tmp_path / "g").mkdir(exist_ok=True); (tmp_path / run_name).mkdir(exist_ok=True)
    doc = json.dumps({"store": {"graphRoot": str(tmp_path / "g"), "runRoot": str(tmp_path / run_name)}}).encode()
    return lambda argv, timeout=None: doc

def test_lock_path_converges_for_symlinked_runroot_and_never_unlinks(tmp_path):
    first = lock_path(info_run(tmp_path))
    (tmp_path / "alias").symlink_to(tmp_path / "run")
    alias_doc = json.dumps({"store": {"graphRoot": str(tmp_path / "g"), "runRoot": str(tmp_path / "alias")}}).encode()
    second = lock_path(lambda argv, timeout=None: alias_doc)
    assert first == second and first.stat().st_ino == second.stat().st_ino and first.stat().st_mode & 0o777 == 0o600

def test_lock_path_differs_for_uid_graphroot_and_container_name(tmp_path):
    run = info_run(tmp_path)
    base = lock_path(run)
    assert lock_path(run, uid=base.stat().st_uid + 1) != base          # uid is a hash input only
    assert lock_path(run, container_name="other") != base
    other = tmp_path / "g2"; other.mkdir()
    doc = json.dumps({"store": {"graphRoot": str(other), "runRoot": str(tmp_path / "run")}}).encode()
    assert lock_path(lambda argv, timeout=None: doc) != base

@pytest.mark.parametrize("breakage", ["mode", "symlink-dir", "foreign-owner", "symlink-file"])
def test_unsafe_lock_directory_or_file_defers_lock(tmp_path, monkeypatch, breakage):
    run = info_run(tmp_path)
    good = lock_path(run)
    directory = good.parent
    if breakage == "mode":
        directory.chmod(0o755)
    elif breakage == "symlink-dir":
        good.unlink(); directory.rmdir()
        real = tmp_path / "elsewhere"; real.mkdir(mode=0o700)
        directory.symlink_to(real)
    elif breakage == "foreign-owner":
        monkeypatch.setattr("pylib.omniroute_observer.os.geteuid", lambda: os.stat(directory).st_uid + 1)
    else:
        target = tmp_path / "target"; target.write_text(""); good.unlink(); good.symlink_to(target)
    with pytest.raises(Deferral) as e:
        lock_path(run)
    assert e.value.reason is Reason.lock

def test_flock_is_released_when_the_holder_is_sigkilled(tmp_path):
    path = lock_path(info_run(tmp_path))
    holder = subprocess.Popen(["bash", "-c", f'exec 9>>"{path}"; flock -n 9 && echo held && sleep 300'], stdout=subprocess.PIPE, text=True)
    assert holder.stdout.readline().strip() == "held"
    holder.kill(); holder.wait()
    fd = os.open(path, os.O_RDWR)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)                   # kernel released it on death
    os.close(fd)

def test_lease_alive_matches_pid_and_tick():
    assert lease_alive(os.getpid(), start_tick(os.getpid()))
    assert not lease_alive(os.getpid(), "1")
    p = subprocess.Popen(["true"]); p.wait()
    assert not lease_alive(p.pid, "1")
```

  Verdict loop (pure, no processes):

```python
class _Stop:
    def __init__(self, n): self.n = n
    def is_set(self): return self.n <= 0
    def wait(self, timeout): self.n -= 1

def test_observe_loop_resamples_immediately_and_publishes_only_the_final_verdict(tmp_path):
    clock_values = iter([0.0, 5.0, 10.0])                      # sample_monotonic of the three scheduled samples
    calls, seq = [], iter(range(1, 20))
    def sample_fn(deadline, force_barrier=False):
        n = next(seq); calls.append(force_barrier)
        now = next(clock_values) if not force_barrier else 10.1
        return mk_sample(sequence=n, sample_monotonic=now, health_timestamp_ms=n)       # timestamp advances every sample
    latest = tmp_path / "latest.json"
    observe_loop(sample_fn, IdleGate(1, 1e9), str(latest), 0, _Stop(3), 1e9)
    doc = json.loads(latest.read_text())
    assert calls == [False, False, False, True] and doc["sequence"] == 4
    assert doc["verdict"] == "ready" and doc["sample"]["sequence"] == 4
    assert latest.stat().st_mode & 0o777 == 0o600

def test_observe_loop_reports_defer_unknown_with_the_sample_reason(tmp_path):
    latest = tmp_path / "latest.json"
    observe_loop(lambda d, f=False: mk_sample(unknown=True, reason=Reason.feed, sample_monotonic=1.0),
                 IdleGate(1, 1e9), str(latest), 0, _Stop(1), 1e9)
    doc = json.loads(latest.read_text())
    assert doc["verdict"] == "defer-unknown" and doc["reason"] == "feed"

def test_observe_loop_reports_deadline_reason(tmp_path):
    latest = tmp_path / "latest.json"
    observe_loop(lambda d, f=False: mk_sample(sample_monotonic=50.0), IdleGate(1, 50.0), str(latest), 0, _Stop(1), 1e9)
    doc = json.loads(latest.read_text())
    assert doc["verdict"] == "defer-deadline" and doc["reason"] == "deadline"
```

  Process lifecycle tests (real supervisor, stub `podman`/`journalctl` first on `PATH`):

```python
def test_supervisor_dies_within_5s_when_lease_parent_is_killed_even_with_hung_podman(tmp_path):
    stubs = make_stub_path(tmp_path, podman="sleep 300", journalctl="sleep 300")        # both hang forever
    parent = subprocess.Popen(["sleep", "300"])
    sup, latest = start_supervisor(tmp_path, parent.pid, 120, "0.2", stubs)
    assert wait_for(lambda: pid_alive(sup.pid), 5)
    time.sleep(1.5)
    parent.kill(); parent.wait()
    assert wait_for(lambda: not any_process_in_group(sup.pid), 5), "supervisor group still alive after 5 s"

def test_lifetime_expiry_exits(tmp_path):
    stubs = make_stub_path(tmp_path, podman="exit 1", journalctl="exit 1")
    parent = subprocess.Popen(["sleep", "300"])
    sup, latest = start_supervisor(tmp_path, parent.pid, 2, "0.2", stubs)
    try:
        assert wait_for(lambda: not any_process_in_group(sup.pid), 9)
    finally:
        parent.kill(); parent.wait()

def test_missing_profile_publishes_a_defer_unknown_capability_verdict(tmp_path):
    stubs = make_stub_path(tmp_path, podman="exit 1", journalctl="exit 1")
    parent = subprocess.Popen(["sleep", "300"])
    sup, latest = start_supervisor(tmp_path, parent.pid, 60, "0.2", stubs)
    try:
        assert wait_for(lambda: read_seq(latest) is not None, 20)
        doc = json.loads(latest.read_text())
        assert doc["verdict"] == "defer-unknown" and doc["reason"] == "capability" and doc["sample"]["unknown"] is True
    finally:
        parent.kill(); parent.wait(); os.killpg(sup.pid, signal.SIGKILL)

def test_latest_json_is_never_half_written(tmp_path):
    stubs = make_stub_path(tmp_path, podman="exit 1", journalctl="exit 1")
    parent = subprocess.Popen(["sleep", "300"])
    sup, latest = start_supervisor(tmp_path, parent.pid, 30, "0.05", stubs)
    try:
        assert wait_for(lambda: read_seq(latest) is not None, 20)
        seen = set()
        for _ in range(500):
            seen.add(json.loads(latest.read_text())["sequence"])        # raises JSONDecodeError on a torn write
            time.sleep(0.01)
        assert len(seen) > 1
    finally:
        parent.kill(); parent.wait(); os.killpg(sup.pid, signal.SIGKILL)

def test_observer_does_not_inherit_lock_fd(tmp_path):
    stubs = make_stub_path(tmp_path, podman="exit 1", journalctl="exit 1")
    parent = subprocess.Popen(["sleep", "300"])
    lockfile = tmp_path / "lock"
    argv = prepared_observe_args(tmp_path, parent.pid, start_tick(parent.pid), 30)
    script = 'exec 9>>"$0"; flock -n 9 || exit 3; setsid "$@" 9>&- & wait'          # the exact shape Task 6 uses
    env = {**os.environ, "PATH": f"{stubs}:{os.environ['PATH']}"}
    sh = subprocess.Popen(["bash", "-c", script, str(lockfile), *argv], env=env, cwd=REPO_ROOT)
    try:
        assert wait_for(lambda: len(descendants(sh.pid)) >= 2, 10)
        time.sleep(1.0)
        inode = lockfile.stat().st_ino
        for pid in descendants(sh.pid):
            for fd in Path(f"/proc/{pid}/fd").iterdir():
                try:
                    assert os.stat(fd).st_ino != inode, f"pid {pid} inherited the lock fd"
                except OSError:
                    pass
    finally:
        parent.kill(); parent.wait()
        for pid in descendants(sh.pid):
            os.kill(pid, signal.SIGKILL)
        sh.kill()
```

  The stubbed `podman` fails and the profile list is empty, so every sample is `unknown` (`capability`): a profile or inventory failure is a sample, not a crash, which is what the lifecycle tests above rely on.
- [ ] **Step 2: Run, verify FAIL.**
- [ ] **Step 3: Implement** `pylib/omniroute_observer.py`:

```python
"""Observer supervisor (parent lease, verdict file) and the canonical update-lock path."""
from __future__ import annotations

import hashlib
import json
import os
import signal
import stat
import threading
import time
from pathlib import Path

from pylib.omniroute_activity import classify
from pylib.omniroute_update import Deferral, Reason, Runner, UpdateError, write_private

LOCK_DIR = "llm-env-update-locks"


def _stat_fields(pid: int) -> list[str]:
    return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()


def lease_alive(pid: int, start_tick: str) -> bool:
    try:
        fields = _stat_fields(pid)
        return fields[19] == start_tick and fields[0] != "Z"
    except (OSError, IndexError):
        return False


def lock_path(run: Runner, container_name: str = "omniroute", uid: int | None = None) -> Path:
    uid = os.geteuid() if uid is None else uid
    try:
        store = json.loads(run(["podman", "info", "--format", "json"])).get("store") or {}
        graph, runroot = os.path.realpath(store["graphRoot"]), os.path.realpath(store["runRoot"])
    except (UpdateError, ValueError, KeyError, TypeError) as exc:
        raise Deferral(Reason.io, "podman store roots unavailable") from exc
    key = hashlib.sha256("\0".join([str(uid), graph, runroot, container_name]).encode()).hexdigest()
    directory = Path(runroot, LOCK_DIR)
    try:
        directory.mkdir(mode=0o700)
    except FileExistsError:
        pass
    st = os.lstat(directory)
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.geteuid() or stat.S_IMODE(st.st_mode) != 0o700:
        raise Deferral(Reason.lock, "lock directory is not a private directory owned by this user")
    path = directory / f"omniroute-update-{key}.lock"
    try:
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    except OSError as exc:
        raise Deferral(Reason.lock, "lock file cannot be opened safely") from exc
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid() or stat.S_IMODE(st.st_mode) != 0o600:
            raise Deferral(Reason.lock, "lock file is not a private regular file")
    finally:
        os.close(fd)
    return path


def _verdict(gate, s) -> str:
    return gate.step(s.sample_monotonic, classify(s), arrival_generation=s.arrival_generation,
                     row_generation=s.row_generation, health_timestamp_ms=s.health_timestamp_ms)


def observe_loop(sample_fn, gate, latest_path, interval, stop, lifetime_end, clock=time.monotonic) -> None:
    while not stop.is_set():
        start = clock()
        s = sample_fn(min(start + 5, lifetime_end), False)
        verdict = _verdict(gate, s)
        if verdict == "resample":                     # spec: complete resample (fresh barrier) before commit
            s = sample_fn(min(clock() + 5, lifetime_end), True)
            verdict = _verdict(gate, s)
        write_private(latest_path, json.dumps({
            "sequence": s.sequence, "verdict": verdict,
            "reason": "deadline" if verdict == "defer-deadline" else s.reason.value,
            "sample": json.loads(s.to_json())}))
        stop.wait(max(0.0, interval - (clock() - start)))


def _group_members() -> list[int]:
    me, pgid, found = os.getpid(), os.getpgrp(), []
    for entry in Path("/proc").iterdir():
        if entry.name.isdigit() and int(entry.name) != me:
            try:
                fields = _stat_fields(int(entry.name))
            except (OSError, IndexError):
                continue
            if int(fields[2]) == pgid and fields[0] != "Z":
                found.append(int(entry.name))
    return found


def _teardown(terminate) -> None:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    try:
        terminate()
    except Exception:                                  # best effort: the group kill below is the guarantee
        pass
    os.killpg(os.getpgrp(), signal.SIGTERM)
    end = time.monotonic() + 2
    while time.monotonic() < end and _group_members():
        try:
            os.waitpid(-1, os.WNOHANG)                 # reap children that already exited
        except ChildProcessError:
            pass
        time.sleep(0.05)
    for pid in _group_members():
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    os._exit(0)


def supervise(work_fn, terminate, lease_pid, lease_tick, lifetime, clock=time.monotonic) -> None:
    stop, flags = threading.Event(), {"term": False}
    signal.signal(signal.SIGTERM, lambda *_: flags.update(term=True))
    end = clock() + lifetime
    threading.Thread(target=work_fn, args=(stop, end), daemon=True).start()
    while not flags["term"] and clock() < end and lease_alive(lease_pid, lease_tick):
        time.sleep(0.2)
    stop.set()
    _teardown(terminate)
```

  `cmd_observe` and `cmd_lock_path` live in `pylib/omniroute_cli.py`. `cmd_observe(args, run=None, env=None)` builds the collector as described in **Interfaces** and ends with `supervise(lambda stop, end: observe_loop(sample_fn, gate, latest, args.interval, stop, end), reader.terminate if reader else (lambda: None), args.lease_pid, args.lease_start_tick, args.lifetime)`; `supervise` never returns (it ends in `os._exit(0)`). `cmd_lock_path(args, run=None)` prints `lock_path(run, args.container)`; its `Deferral` handler prints `deferred: <reason>` to stderr and returns 75, an `UpdateError` prints to stderr and returns 1 (this is the one handler that does not use `guarded`). `start_new_session` is **not** used inside the supervisor (Bash starts it under `setsid`). Register `lock-path [--container omniroute]` and `observe` (flags exactly as listed in **Interfaces**, `--lease-pid`/`--lifetime`/`--min-wait` as `int`, `--interval` as `float`) in `register(sub)`.
- [ ] **Step 4: Run, verify PASS.**
- [ ] **Step 5: `make validate && make test`; commit if approved.**

---

### Task 6: Projection, commit verification and the updater script

**Files:**
- Create: `scripts/omniroute-commit-worker.sh`, `tests/fixtures/stub_tool.py`, `tests/omniroute_world.py`, `tests/test_update_omniroute_script.py`
- Modify: `tools/lib.sh` (add `backend_env_init`, `acquire_update_lock`), `pylib/omniroute_update.py`, `pylib/omniroute_cli.py` (add `verify-staged`, `projection`, `verify-commit`), `scripts/update-omniroute.sh`, `tests/test_omniroute_update.py`, `tests/test_cli.py`
- Modify (delete only): `tests/test_shell.py` — remove `run_update_omniroute_with_stubs` and the two tests `test_update_omniroute_defers_without_any_lifecycle_or_registry_mutation` / `test_update_omniroute_all_paths_defer_before_external_commands` (they assert the retired stub); the new script tests live in `tests/test_update_omniroute_script.py` so the 12 000-line `tests/test_shell.py` does not grow.

**Interfaces:**
- Consumes: all `omniroute-update` subcommands from Tasks 1-5 and the preflight artifact directory (Task 2: `snapshot.json`, `fingerprint.txt`, `effective.json`, `target.json`, `deployment.json`; plus Task 5's `latest.json`), `scrubbed_runner`, `with_deadline`, `canon_image_id`, `effective_config`, `check_private_dir`.
- Produces: `build_projection(effective: dict, service: str, inspect_doc: dict, image: str, project: str) -> dict` — a compose document with exactly one service named `service`; `image` replaced; `depends_on` removed **only here**; explicit service `networks` naming the inspected network names; top-level `networks`/`volumes` each `{"external": True, "name": <inspected resource name>}`; every string `$` escaped to `$$` (the provider interpolates the projection once more). Dollar handling is decided from evidence, never assumed: `to_runtime_literal(svc, inspect_doc)` compares each effective `environment` value with the running container's `Config.Env`; a value equal to the runtime value is `plain`, a value equal to the runtime value with `$` doubled is `escaped`, anything else is `Deferral(Reason.ownership)`; both forms present is `Deferral(Reason.input)`; an `escaped` result un-doubles every string; no `$`-bearing environment value at all but a `$` somewhere else in the service is `Deferral(Reason.input)` ("dollar form undetermined"). Mounts: a named volume must resolve to an inspected volume (`Reason.ownership`); a **relative or `~` bind source defers `Reason.input`** (the projection file lives in a private directory, so a relative path would silently resolve somewhere else); an absolute bind source must equal an inspected bind `Source` (`Reason.ownership`).
- Produces: `projection_matches(baseline_rt: dict, projected_rt: dict) -> bool` comparing the runtime-literal service dicts with `image`, `depends_on` and `networks` removed.
- Produces: `verify_projection(effective, service, inspect_doc, image, project, projection_path, provider, run) -> None` — **the provider round trip**. It resolves the *written* projection through the selected provider (`effective_config(provider, project, projection_path, run)`, which runs under the scrubbed backend environment) and requires: exactly the one service; `image == image`; no `depends_on`; no namespace-sharing keys; the provider-resolved service, normalized by `to_runtime_literal` against the running container's `Config.Env`, equals the baseline effective service for every field except image, dependency and networks (`projection_matches`); the resolved network name set equals the inspected network names; every named volume/bind resolves as in `build_projection`. Any difference is `Deferral(Reason.ownership)`. A projection that the provider reads differently from the running container's evidence is never executed.
  - `# shortcut: a literal "$$" in a non-environment string compares equal to "$" after normalization; upgrade if such a field is ever generated by compose.py.`
- Produces: `verify_commit(run, out_dir, service, old_id, config_id, timeout, *, expect_no_deps=False, env=os.environ, sleep=time.sleep, clock=time.monotonic) -> str` (returns the new container ID, raises `UpdateError` with sanitized 12-char old/new IDs and changed sibling IDs). `config_id` is the canonical `sha256:<hex>` or `None` (then the original target image is expected). Every command runs through `with_deadline(run, deadline, clock)`, so a slow command that succeeds after the budget is discarded, and a healthy result observed at or after the deadline is rejected. It waits until a container labelled `LABEL_SERVICE == service` and the original `LABEL_PROJECT` exists with an ID different from `old_id`, is healthy, has `canon_image_id(Image) == want`, has the same network IDs and volume names as `target.json`, has empty `Dependencies` when `expect_no_deps`, and **every other baseline container ID in `snapshot.json` is still present with an unchanged hash** (siblings unchanged, `StartedAt` included).
- Produces CLIs (flags are exhaustive; every producer, storage format and consumer is named; all handlers are in `pylib/omniroute_cli.py` and use `guarded`):
  - `omniroute-update verify-staged --resolved FILE --inspect FILE`: `--resolved` is Task 1's `resolve --json` output; `--inspect` is `podman image inspect --format json` output (a one-element list). Calls `verify_staged`; exit 0 / 1.
  - `omniroute-update projection --dir DIR --service SVC --image REF --out FILE`: reads `DIR/effective.json`, `DIR/target.json`, `DIR/deployment.json` (project, provider); builds the projection, writes it as JSON (valid YAML) to `FILE` via `write_private`, then runs `verify_projection` on that file; exit 0 / 75 `deferred: <reason>` / 1.
  - `omniroute-update verify-commit --dir DIR --service SVC --old-id ID [--config-id CID] --timeout S [--expect-no-deps]`: prints `new_id=<id>` and exits 0; failure prints a sanitized message to stderr and exits 1. Consumers: the commit worker (this task) and `scripts/migrate-remote-setup.sh` (Task 7, with `--expect-no-deps` and no `--config-id`).
- Produces `tools/lib.sh` helpers used by every updater script: `backend_env_init` (fills the global array `backend_env=(env -i PATH=… HOME=… [XDG_RUNTIME_DIR=…] [CONTAINERS_STORAGE_CONF=… CONTAINERS_CONF=… CONTAINERS_REGISTRIES_CONF=…])`, identical to Task 1's `backend_env()` allowlist) and `acquire_update_lock` (needs the `llm` array and `$work`; takes the canonical lock on FD 9, replays a `lock-path` deferral, exits 75 `deferred: lock` on contention).
- Produces: script stdout lines `unchanged`, `updated <old-12> -> <new-12>`, `deferred: <reason>`; exit codes per Global Constraints.
- Script data flow (all inside the single 0700 `work` directory the script creates with `mktemp -d`): `preflight` writes the five artifacts + stdout `deploy.env`; `resolve --json` writes `resolved.json`; staging writes `staged.json`; `projection` writes `projection.yml`; the script writes `pw` (0600, from `yq`), `deadline` (`deadline --init`), `commit.env` (`config_id immutable_ref staged_id health_timeout`); `observe` writes `latest.json`.

- [ ] **Step 1: Failing unit tests** in `tests/test_omniroute_update.py` (projection, provider round trip, bind sources, verify_commit):

```python
from pathlib import Path
import yaml
from pylib.omniroute_update import (build_projection, verify_projection, projection_matches, verify_commit,
                                    take_snapshot, write_private, _unescape)
from omniroute_fakes import OMNI, RS, SIB, NEW, NET, PASSWORD, inspect_doc, omni, fake_backend

SVC = {"image": "docker.io/diegosouzapw/omniroute:latest", "container_name": "omniroute",
       "ports": ["0.0.0.0:20128:20128"], "restart": "unless-stopped", "stop_grace_period": "40s",
       "environment": {"PORT": "20128", "INITIAL_PASSWORD": "pa$$s", "REQUIRE_API_KEY": "true"},
       "volumes": ["omniroute-data:/app/data"], "healthcheck": {"test": ["CMD", "true"]},
       "depends_on": {"llm-server": {"condition": "service_healthy"}}}
EFF = {"services": {"omniroute": SVC}, "networks": {"default": {}}, "volumes": {"omniroute-data": {}}}
IMAGE = "docker.io/diegosouzapw/omniroute@sha256:" + "9" * 64

def target(password="pa$s", networks=None):
    d = omni(env=[f"INITIAL_PASSWORD={password}", "PORT=20128", "REQUIRE_API_KEY=true", "NODE_ENV=production"])
    d["Mounts"] = [{"Type": "volume", "Name": "llm-env_omniroute-data"}]
    d["NetworkSettings"]["Networks"] = networks or {"llm-env_default": {"NetworkID": NET}}
    return d

def with_volumes(entries):
    return {**EFF, "services": {"omniroute": {**SVC, "volumes": entries}}}

def test_projection_drops_dependency_escapes_dollars_and_pins_external_resources():
    out = build_projection(EFF, "omniroute", target(), IMAGE, "llm-env")
    svc = out["services"]["omniroute"]
    assert list(out["services"]) == ["omniroute"] and svc["image"] == IMAGE and "depends_on" not in svc
    assert svc["environment"]["INITIAL_PASSWORD"] == "pa$$s"                 # runtime pa$s, doubled for the provider
    assert svc["networks"] == {"llm-env_default": {}}
    assert out["networks"] == {"llm-env_default": {"external": True, "name": "llm-env_default"}}
    assert out["volumes"] == {"omniroute-data": {"external": True, "name": "llm-env_omniroute-data"}}
    assert svc["container_name"] == "omniroute" and svc["stop_grace_period"] == "40s" and svc["ports"] == SVC["ports"]

def test_plain_provider_output_form_gives_the_same_projection():
    plain = {**EFF, "services": {"omniroute": {**SVC, "environment": {**SVC["environment"], "INITIAL_PASSWORD": "pa$s"}}}}
    assert build_projection(plain, "omniroute", target(), IMAGE, "llm-env") == build_projection(EFF, "omniroute", target(), IMAGE, "llm-env")

def test_literal_double_dollar_password_round_trips():
    escaped = {**EFF, "services": {"omniroute": {**SVC, "environment": {**SVC["environment"], "INITIAL_PASSWORD": "a$$$$b"}}}}
    out = build_projection(escaped, "omniroute", target(password="a$$b"), IMAGE, "llm-env")
    assert out["services"]["omniroute"]["environment"]["INITIAL_PASSWORD"] == "a$$$$b"

def test_mixed_dollar_forms_defer_input():
    mixed = {**EFF, "services": {"omniroute": {**SVC, "environment": {"INITIAL_PASSWORD": "x$$y", "OTHER": "p$q"}}}}
    d = target(); d["Config"]["Env"] += ["OTHER=p$q"]; d["Config"]["Env"][0] = "INITIAL_PASSWORD=x$y"
    with pytest.raises(Deferral) as e: build_projection(mixed, "omniroute", d, IMAGE, "llm-env")
    assert e.value.reason is Reason.input

def test_environment_value_differing_from_runtime_defers_ownership():
    with pytest.raises(Deferral) as e: build_projection(EFF, "omniroute", target(password="other"), IMAGE, "llm-env")
    assert e.value.reason is Reason.ownership and "other" not in str(e.value)

def test_extra_inspected_network_defers_ownership():
    nets = {"llm-env_default": {"NetworkID": NET}, "llm-env_extra": {"NetworkID": "f" * 64}}
    with pytest.raises(Deferral) as e: build_projection(EFF, "omniroute", target(networks=nets), IMAGE, "llm-env")
    assert e.value.reason is Reason.ownership

def test_explicit_network_resolves_through_top_level_name():
    eff = {**EFF, "services": {"omniroute": {**SVC, "networks": ["app"]}}, "networks": {"app": {"name": "llm-env_default"}}}
    assert build_projection(eff, "omniroute", target(), IMAGE, "llm-env")["services"]["omniroute"]["networks"] == {"llm-env_default": {}}

def test_volume_not_present_in_inspect_defers_ownership():
    d = target(); d["Mounts"] = [{"Type": "volume", "Name": "someone-else"}]
    with pytest.raises(Deferral) as e: build_projection(EFF, "omniroute", d, IMAGE, "llm-env")
    assert e.value.reason is Reason.ownership

@pytest.mark.parametrize("source", ["./data", "../data", "~/data"])
def test_relative_bind_source_defers_input(source):
    d = target(); d["Mounts"] = [{"Type": "bind", "Source": "/srv/x", "Destination": "/app/data"}]
    with pytest.raises(Deferral) as e: build_projection(with_volumes([f"{source}:/app/data"]), "omniroute", d, IMAGE, "llm-env")
    assert e.value.reason is Reason.input

def test_absolute_bind_source_must_equal_the_inspected_mount():
    d = target(); d["Mounts"] = [{"Type": "bind", "Source": "/srv/omni", "Destination": "/app/data"}]
    out = build_projection(with_volumes(["/srv/omni:/app/data:z"]), "omniroute", d, IMAGE, "llm-env")
    assert out["services"]["omniroute"]["volumes"] == ["/srv/omni:/app/data:z"] and out["volumes"] == {}
    d["Mounts"] = [{"Type": "bind", "Source": "/srv/other", "Destination": "/app/data"}]
    with pytest.raises(Deferral) as e: build_projection(with_volumes(["/srv/omni:/app/data:z"]), "omniroute", d, IMAGE, "llm-env")
    assert e.value.reason is Reason.ownership

@pytest.mark.parametrize("key", ["pod", "network_mode", "links", "volumes_from"])
def test_projection_rejects_namespace_sharing(key):
    eff = {**EFF, "services": {"omniroute": {**SVC, key: "x"}}}
    with pytest.raises(Deferral) as e: build_projection(eff, "omniroute", target(), IMAGE, "llm-env")
    assert e.value.reason is Reason.input

def test_projection_matches_detects_any_field_drift_except_image_depends_and_networks():
    base = dict(SVC)
    assert projection_matches(base, {**base, "image": "other", "depends_on": {}, "networks": {"x": {}}})
    assert not projection_matches(base, {**base, "ports": ["1:1"]})

def fake_provider(mutate=lambda doc: doc):
    """Answers `<provider> ... -f FILE config` the way podman-compose does: it interpolates the file once ($$ -> $)."""
    def run(argv, timeout=None):
        assert argv[-1] == "config"
        doc = _unescape(json.loads(Path(argv[argv.index("-f") + 1]).read_text()))
        return yaml.safe_dump(mutate(doc)).encode()
    return run

def written_projection(tmp_path):
    path = tmp_path / "projection.yml"
    write_private(str(path), json.dumps(build_projection(EFF, "omniroute", target(), IMAGE, "llm-env")))
    return str(path)

def test_verify_projection_accepts_a_faithful_provider_round_trip(tmp_path):
    verify_projection(EFF, "omniroute", target(), IMAGE, "llm-env", written_projection(tmp_path), "podman-compose", fake_provider())

@pytest.mark.parametrize("mutate", [
    lambda d: d["services"]["omniroute"].update(ports=["0.0.0.0:9999:9999"]),                          # semantic drift
    lambda d: d["services"]["omniroute"]["environment"].update(INITIAL_PASSWORD="pas"),                # provider ate a "$"
    lambda d: d["services"]["omniroute"].update(depends_on={"llm-server": {"condition": "service_healthy"}}),
    lambda d: d["services"]["omniroute"].update(image="docker.io/other/img:latest"),
    lambda d: d["services"].update(extra={"image": "x"}),                                              # extra service
    lambda d: d["services"]["omniroute"]["networks"].update({"llm-env_extra": {}}),                   # extra network
    lambda d: d["services"]["omniroute"].update(volumes=["elsewhere:/app/data"]),                      # different volume
])
def test_verify_projection_defers_when_the_provider_reads_it_differently(tmp_path, mutate):
    def mutated(doc):
        mutate(doc)
        return doc
    with pytest.raises(Deferral) as e:
        verify_projection(EFF, "omniroute", target(), IMAGE, "llm-env", written_projection(tmp_path), "podman-compose", fake_provider(mutated))
    assert e.value.reason is Reason.ownership and "pa$s" not in str(e.value)
```

`verify_commit` tests use a stepping backend (each `podman info` call advances one state, because every `take_snapshot` starts with it) and a fake clock:

```python
def stepping_backend(*states):
    state = {"i": 0}
    def run(argv, timeout=None):
        if argv[:2] == ["podman", "info"]:
            state["i"] += 1
        current = states[min(max(state["i"] - 1, 0), len(states) - 1)]
        return fake_backend(current)(argv, timeout=timeout)
    return run

def art(tmp_path, containers, target_id=OMNI):
    snap = take_snapshot(fake_backend(containers))
    d = tmp_path / "art"; d.mkdir(mode=0o700)
    write_private(str(d / "snapshot.json"), snap.to_json())
    write_private(str(d / "target.json"), json.dumps([containers[target_id]]))
    return str(d)

def new_omni(**kw):
    kw.setdefault("image", "2" * 64)
    doc = inspect_doc(NEW, name="omniroute", **kw)
    doc["Mounts"] = omni()["Mounts"]
    return doc

def ticking():
    t = {"now": 0.0}
    def clock(): return t["now"]
    def sleep(s): t["now"] += s
    return clock, sleep

def test_verify_commit_success_waits_for_health(tmp_path):
    sib = {SIB: inspect_doc(SIB, name="sib")}
    d = art(tmp_path, {OMNI: omni(), **sib})
    clock, sleep = ticking()
    run = stepping_backend({NEW: new_omni(health="starting"), **sib}, {NEW: new_omni(), **sib})
    assert verify_commit(run, d, "omniroute", OMNI, "sha256:" + "2" * 64, 30, clock=clock, sleep=sleep) == NEW

def test_verify_commit_fails_on_same_id_wrong_image_or_changed_sibling(tmp_path):
    sib = {SIB: inspect_doc(SIB, name="sib")}
    d = art(tmp_path, {OMNI: omni(), **sib})
    clock, sleep = ticking()
    for states, text in (
        ([{OMNI: omni(), **sib}], "not replaced"),
        ([{NEW: new_omni(image="3" * 64), **sib}], "image"),
        ([{NEW: new_omni(), SIB: inspect_doc(SIB, name="sib", port=9999)}], "sibling"),
    ):
        with pytest.raises(UpdateError, match=text) as e:
            verify_commit(stepping_backend(*states), d, "omniroute", OMNI, "sha256:" + "2" * 64, 3, clock=clock, sleep=sleep)
        assert OMNI[:12] in str(e.value) and PASSWORD not in str(e.value)

def test_verify_commit_times_out_when_never_healthy(tmp_path):
    d = art(tmp_path, {OMNI: omni()})
    clock, sleep = ticking()
    with pytest.raises(UpdateError, match="not healthy within 5s"):
        verify_commit(stepping_backend({NEW: new_omni(health="starting")}), d, "omniroute", OMNI, "sha256:" + "2" * 64, 5, clock=clock, sleep=sleep)

def test_verify_commit_rejects_a_slow_successful_command(tmp_path):
    sib = {SIB: inspect_doc(SIB, name="sib")}
    d = art(tmp_path, {OMNI: omni(), **sib})
    t = {"now": 0.0}
    inner = stepping_backend({NEW: new_omni(), **sib})
    def slow(argv, timeout=None):
        t["now"] += 10.0                                    # each command "takes" 10 s of fake time and then succeeds
        return inner(argv, timeout=timeout)
    with pytest.raises(UpdateError, match="inventory unavailable"):
        verify_commit(slow, d, "omniroute", OMNI, "sha256:" + "2" * 64, 5, clock=lambda: t["now"], sleep=lambda s: None)

def test_verify_commit_expect_no_deps_and_missing_config_id(tmp_path):
    rs = inspect_doc(RS, name="remote-setup", deps=[OMNI])
    d = art(tmp_path, {OMNI: omni(), RS: rs}, target_id=RS)           # the migration target is remote-setup, not OmniRoute
    clock, sleep = ticking()
    fresh_rs = inspect_doc(NEW, name="remote-setup", deps=[])
    fresh_rs["Mounts"], fresh_rs["Image"] = rs["Mounts"], rs["Image"]
    ok = stepping_backend({OMNI: omni(), NEW: fresh_rs})
    assert verify_commit(ok, d, "remote-setup", RS, None, 5, expect_no_deps=True, clock=clock, sleep=sleep) == NEW
    bad = inspect_doc(NEW, name="remote-setup", deps=[OMNI]); bad["Mounts"], bad["Image"] = rs["Mounts"], rs["Image"]
    with pytest.raises(UpdateError, match="dependenc"):
        verify_commit(stepping_backend({OMNI: omni(), NEW: bad}), d, "remote-setup", RS, None, 5,
                      expect_no_deps=True, clock=clock, sleep=sleep)
```

CLI tests in `tests/test_cli.py`: `verify-staged` happy and mismatched config id (exit 0 / 1, no inspect JSON echoed); `projection` writes a 0600 file from a prepared artifact directory (`write_preflight_artifacts` from the Task 2 world) with a `fake_provider()` run and returns 75 with `deferred: ownership` for the extra-network world and for a `fake_provider` that mutates the ports; `verify-commit` prints `new_id=` on success and returns 1 with a stderr message on failure.

- [ ] **Step 2: Failing shell tests.** Test infrastructure first (created in this step, used by every case). **The production scripts scrub the environment of every Podman/provider child (Global Constraints), so the stub cannot depend on variables**: it finds its world file and call journal as siblings of the symlink it was invoked through.

  `tests/fixtures/stub_tool.py` — one scripted stub installed under four names (`podman`, `skopeo`, `podman-compose`, `journalctl`) as symlinks inside `World.bin`:

```python
#!/usr/bin/env python3
"""Scripted stub for podman/skopeo/podman-compose/journalctl (tests only).

Production runs every such child under `env -i` plus a short allowlist, so no STUB_* variable can reach
this process. The world file and the call journal are siblings of the symlink the stub was invoked through."""
import json, os, signal, sys, time
from pathlib import Path

name, argv = os.path.basename(sys.argv[0]), sys.argv[1:]
home = Path(sys.argv[0]).parent                       # NOT resolved: the directory holding the symlinks
world_path, calls = home / "stub-world.json", home / "stub-calls.jsonl"
world = json.loads(world_path.read_text())
phase = world.get("phase", "start")

def set_phase(value):
    w = json.loads(world_path.read_text()); w["phase"] = value; world_path.write_text(json.dumps(w))

fds = []
for fd in os.listdir("/proc/self/fd"):
    try:
        fds.append(os.readlink(f"/proc/self/fd/{fd}"))
    except OSError:
        pass
env = {k: os.environ[k] for k in ("CONTAINERS_STORAGE_CONF", "CONTAINERS_CONF", "CONTAINERS_REGISTRIES_CONF",
                                  "XDG_RUNTIME_DIR") if k in os.environ}
with calls.open("a") as handle:
    handle.write(json.dumps({"tool": name, "argv": argv, "phase": phase, "fds": fds, "env": env,
                             "t": time.monotonic()}) + "\n")

def matches(rule):
    if rule["tool"] != name:
        return False
    if "prefix" in rule and argv[:len(rule["prefix"])] != rule["prefix"]:
        return False
    if "last" in rule and (not argv or argv[-1] != rule["last"]):
        return False
    if "has" in rule and not all(token in argv for token in rule["has"]):
        return False
    if "arg_endswith" in rule and not any(a.endswith(rule["arg_endswith"]) for a in argv):
        return False
    if "phase" in rule and phase not in rule["phase"]:
        return False
    return True

rule = next((r for r in world["rules"] if matches(r)), None)
if rule is None:
    if argv and argv[0] in {"pull", "tag", "stop", "rm"}:      # mutating verbs default to success
        sys.exit(0)
    print(f"stub: unscripted {name} {argv} in phase {phase}", file=sys.stderr)
    sys.exit(99)
if "on_term_set_phase" in rule:
    def on_term(*_):
        set_phase(rule["on_term_set_phase"]); sys.exit(0)
    signal.signal(signal.SIGTERM, on_term)
time.sleep(rule.get("sleep", 0))
if "set_phase" in rule:
    set_phase(rule["set_phase"])
if "echo_file_after" in rule:                                  # emulate `provider -f FILE config` on a real file
    sys.stdout.write(Path(argv[argv.index(rule["echo_file_after"]) + 1]).read_text())
sys.stdout.write(rule.get("stdout", ""))
sys.exit(rule.get("rc", 0))
```

  `tests/omniroute_world.py` builds the whole disposable world and runs the **real** script:

```python
import hashlib, json, os, pathlib, shutil, subprocess, sys
import yaml
from omniroute_fakes import (OMNI, RS, NEW, IMG, NET, PROJECT, PASSWORD, inspect_doc, omni, profile_entry, free_port)

REPO = pathlib.Path(__file__).resolve().parents[1]
STAGED = "sha256:" + "2" * 64

def manifest(config_digest):
    return json.dumps({"schemaVersion": 2, "mediaType": "application/vnd.oci.image.manifest.v1+json",
                       "config": {"digest": config_digest, "mediaType": "application/vnd.oci.image.config.v1+json", "size": 1},
                       "layers": []})

def make_repo_copy(tmp_path, profile_entries):
    """A temp copy of the repo tree whose profile list is `profile_entries` (shared with the Task 8 proofs; this is the
    only way a profile is supplied -- there is no production-reachable injection seam)."""
    dst = tmp_path / "repo"
    if not dst.exists():                                  # a second call in the same test only rewrites the profile list
        shutil.copytree(REPO, dst, ignore=shutil.ignore_patterns(".git", ".venv", "tmp", ".docs", "__pycache__", ".pytest_cache", "node_modules"))
    (dst / "pylib/omniroute_activity_profiles.json").write_text(json.dumps({"schema": 1, "profiles": profile_entries}))
    return dst

class World:
    def __init__(self, tmp_path, *, staged=STAGED, staged_inspect_id=None, dependent=False, drift_after_observer=False,
                 up_rc=0, stop_sleep=0, prior_tag_sleep=0, env=None, busy=False):
        self.tmp, self.env_extra = tmp_path, env or {}
        self.port = free_port()
        self.bin = tmp_path / "bin"; self.bin.mkdir()
        self.calls, self.worldfile = self.bin / "stub-calls.jsonl", self.bin / "stub-world.json"
        self.calls.touch()
        self.home = tmp_path / "home"; cfgdir = self.home / ".config/llm-env"; cfgdir.mkdir(parents=True)
        (cfgdir / "models.yml").write_text(yaml.safe_dump({"version": 1, "omniroute": {
            "port": self.port, "image": "docker.io/diegosouzapw/omniroute:latest", "initial_password": PASSWORD}}))
        (cfgdir / "models.yml").chmod(0o600)
        (cfgdir / "docker-compose.yml").write_text("services:\n  omniroute: {}\n")
        self.state = tmp_path / "omni-state.json"
        self.state.write_text(json.dumps({"active": 1 if busy else 0, "cache_ms": 1000}))
        stub = REPO / "tests/fixtures/stub_tool.py"
        for tool in ("podman", "skopeo", "podman-compose", "journalctl"):
            (self.bin / tool).symlink_to(stub)
        self.repo = make_repo_copy(tmp_path, [profile_entry(config_id="sha256:" + IMG)])
        self.immutable_ref = "docker.io/diegosouzapw/omniroute@sha256:" + hashlib.sha256(manifest(staged).encode()).hexdigest()
        inspect_id = (staged_inspect_id or staged).removeprefix("sha256:")
        old = omni(port=self.port, env=[f"INITIAL_PASSWORD={PASSWORD}", f"PORT={self.port}", "REQUIRE_API_KEY=true", "NODE_ENV=production"])
        new = inspect_doc(NEW, name="omniroute", port=self.port, image=staged.removeprefix("sha256:"),
                          env=old["Config"]["Env"])
        rs = inspect_doc(RS, name="remote-setup", deps=[OMNI])
        eff = {"services": {"omniroute": {"image": "docker.io/diegosouzapw/omniroute:latest", "container_name": "omniroute",
               "ports": [f"0.0.0.0:{self.port}:{self.port}"], "stop_grace_period": "40s", "volumes": ["omniroute-data:/app/data"],
               "environment": {"PORT": str(self.port), "INITIAL_PASSWORD": PASSWORD, "REQUIRE_API_KEY": "true", "NODE_ENV": "production"}}},
               "networks": {"default": {}}, "volumes": {"omniroute-data": {}}}
        old["Mounts"] = [{"Type": "volume", "Name": f"{PROJECT}_omniroute-data"}]; new["Mounts"] = old["Mounts"]
        start_ids = [OMNI] + ([RS] if dependent else [])
        def ps(ids): return json.dumps([{"Id": i} for i in ids])
        T = "tool"
        rules = [
            {T: "podman", "prefix": ["info"], "stdout": json.dumps({"store": {"graphRoot": str(tmp_path / "g"), "runRoot": str(tmp_path / "r")},
                                                                    "host": {"security": {"rootless": True}, "serviceIsRemote": False}})},
            {T: "podman", "prefix": ["ps", "--all", "--filter"], "stdout": ""},
            {T: "podman", "prefix": ["ps"], "phase": ["start"], "stdout": ps(start_ids)},
            {T: "podman", "prefix": ["ps"], "phase": ["post-observer"], "stdout": ps([OMNI, RS] if drift_after_observer else start_ids)},
            {T: "podman", "prefix": ["ps"], "phase": ["post-rm"], "stdout": ps([])},
            {T: "podman", "prefix": ["ps"], "phase": ["post-commit"], "stdout": ps([NEW])},
            {T: "podman", "prefix": ["container", "inspect"], "last": OMNI, "stdout": json.dumps([old])},
            {T: "podman", "prefix": ["container", "inspect"], "last": RS, "stdout": json.dumps([rs])},
            {T: "podman", "prefix": ["container", "inspect"], "last": NEW, "stdout": json.dumps([new])},
            {T: "podman", "prefix": ["image", "inspect", "--format", "json", IMG], "stdout": json.dumps([{"Id": IMG, "Os": "linux", "Architecture": "amd64", "Variant": ""}])},
            {T: "podman", "prefix": ["image", "inspect", "--format", "json"], "stdout": json.dumps([{"Id": inspect_id, "Os": "linux", "Architecture": "amd64", "Variant": ""}])},
            {T: "podman", "prefix": ["image", "inspect", "--format", "{{.Id}}"], "sleep": prior_tag_sleep, "stdout": IMG + "\n"},
            {T: "podman", "prefix": ["stop"], "sleep": stop_sleep},
            {T: "podman", "prefix": ["rm"], "set_phase": "post-rm"},
            {T: "skopeo", "prefix": ["inspect", "--raw"], "stdout": manifest(staged)},
            {T: "podman-compose", "prefix": ["--version"], "stdout": "podman-compose 1.6.0\n"},
            {T: "podman-compose", "last": "config", "arg_endswith": "projection.yml", "echo_file_after": "-f"},   # provider round trip
            {T: "podman-compose", "last": "config", "stdout": yaml.safe_dump(eff)},
            {T: "podman-compose", "has": ["up"], "rc": up_rc, "set_phase": "post-commit" if up_rc == 0 else "start"},
            {T: "journalctl", "has": ["--follow"], "sleep": 300, "on_term_set_phase": "post-observer"},
            {T: "journalctl", "stdout": json.dumps({"__CURSOR": "s1", "_BOOT_ID": "b1", "MESSAGE": json.dumps({"method": "GET", "path": "/api/monitoring/health"})}) + "\n"},
        ]
        self.worldfile.write_text(json.dumps({"phase": "start", "rules": rules}))
        self.server = subprocess.Popen([sys.executable, str(REPO / "tests/fixtures/fake_omniroute.py"),
                                        "--port", str(self.port), "--state", str(self.state)], stdout=subprocess.DEVNULL)

    def env(self):
        base = {k: v for k, v in os.environ.items() if not k.startswith(("COMPOSE_", "PODMAN_COMPOSE_", "CONTAINER_", "STUB_"))}
        return {**base, "HOME": str(self.home), "PATH": f"{self.bin}:{base['PATH']}",
                "UV_PROJECT_ENVIRONMENT": str(REPO / ".venv"),
                "UV_CACHE_DIR": os.environ.get("UV_CACHE_DIR", str(pathlib.Path.home() / ".cache/uv")), **self.env_extra}

    def run(self, **env):
        return subprocess.run(["bash", str(self.repo / "scripts/update-omniroute.sh")], env={**self.env(), **env},
                              capture_output=True, text=True, timeout=180)

    def popen(self, **env):
        return subprocess.Popen(["bash", str(self.repo / "scripts/update-omniroute.sh")], env={**self.env(), **env},
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)

    def journal(self):
        return [json.loads(line) for line in self.calls.read_text().splitlines()]

    def mutations(self):
        """Mutating invocations in order: (tool, verb, rest...)."""
        out = []
        for c in self.journal():
            a = c["argv"]
            if c["tool"] == "podman" and a[:1] in (["pull"], ["tag"], ["stop"], ["rm"]):
                out.append((c["tool"], *a))
            if c["tool"] == "podman-compose" and "up" in a:
                out.append((c["tool"], *a))
        return out

    def close(self):
        self.server.kill(); self.server.wait()
```

  `free_port`, `NEW`, `profile_entry` and the other fixtures come from `tests/omniroute_fakes.py` (Tasks 2-3). The world's `omni()`/`inspect_doc` documents already carry the `Entrypoint`, `LogConfig` and `NODE_ENV=production` fields the fixture profile requires. Real-time note: these tests run the real 5 s sampling cadence, so busy/deadline cases take 5-20 s; keep each case's overrides small (`LLM_ENV_UPDATE_IDLE_SECONDS=1`).

  Cases in `tests/test_update_omniroute_script.py` (each uses `World(tmp_path, ...)` with `w.close()` in `finally`):
  0. `test_stub_finds_its_world_without_any_environment`: `subprocess.run(["env", "-i", f"PATH={w.bin}", "podman", "info"])` exits 0 and prints the scripted JSON; the journal gains one entry (proves the stub survives the production scrub).
  1. `staged=("sha256:" + IMG)` -> stdout `unchanged`, exit 0, `w.mutations() == []`.
  2. `LLM_ENV_UPDATE_IDLE_SECONDS=""` -> exit 1, `w.journal() == []`. **And** a second case putting a stub `uv` that prints nothing and exits 1 first on `PATH`: the script exits 1 with an empty journal (guards the failed-substitution bug).
  3. `dependent=True` -> exit 75, stdout `deferred: ownership`, `w.mutations() == []` (no `pull` either: preflight precedes staging).
  4. concurrent invocation: the test first runs `uv run llmenv.py omniroute-update lock-path` with the world env, holds `flock` on the printed path, then runs the script -> exit 75, stdout `deferred: lock`, journal has no mutation.
  5. `busy=True`, `LLM_ENV_UPDATE_IDLE_SECONDS=1`, `LLM_ENV_UPDATE_MAX_WAIT_SECONDS=3` -> exit 75, stdout `deferred: deadline`, mutations are exactly `[("podman", "pull", w.immutable_ref)]`.
  6. `World(tmp_path, staged=STAGED, staged_inspect_id="sha256:" + "5" * 64)` (the staged image's local Id differs from the resolved config id) -> exit 1, mutations only `pull`.
  7. `dependent=False, drift_after_observer=True` with `LLM_ENV_UPDATE_IDLE_SECONDS=1`, `LLM_ENV_UPDATE_MAX_WAIT_SECONDS=60` -> the world gains a dependent exactly when the observer is terminated, so the commit worker's recheck finds it: exit 75, `deferred: ownership`, mutations == `[pull]` only (no `tag`/`stop`/`rm`/`up`).
  8. **Commit order, no collateral, lock hygiene:** idle dependency-free world, overrides 1/60 -> exit 0, stdout `updated <12> -> <12>`, `w.mutations()` equals exactly `[("podman","pull",ref), ("podman","tag", staged_id_without_prefix, "docker.io/diegosouzapw/omniroute:latest"), ("podman","stop","--time","40",OMNI), ("podman","rm",OMNI), ("podman-compose","--in-pod","false","--env-file","/dev/null","-p",PROJECT,"-f",<work>/projection.yml,"up","-d","--no-deps","--no-recreate","--no-build","--pull","never","omniroute")]` (compare with the `-f` argument wildcarded); no journal argv contains `--force`, `--depend`, `down`, `--remove-orphans`, `-v`, `--volumes`; **and** no journal entry's `fds` list contains a path ending in `.lock`. This case also proves the exported `COMPOSE_FILE`/`COMPOSE_INSPECT_DIR` from `tools/lib.sh` do not trigger the `input` deferral, and (through the `config` rule that echoes the written projection) that the provider round trip ran on `projection.yml`.
  9. Outer shell SIGKILLed while waiting (`busy=True`, large max-wait): record descendants (`/proc` scan as in Task 5 helpers), `os.killpg` is **not** used; `proc.kill()` the outer; assert within 5 s no process of the observer group remains and `w.mutations() == [("podman","pull",ref)]`.
  10. `up_rc=1` -> exit 1; stderr contains `stage: up`, the 12-char old id, `prior image tag target`, `No rollback was attempted`; mutations end with the failed `up` and contain no second `tag` and no `podman run`.
  11. **Worker owns the lock:** `stop_sleep=6`, overrides 1/60; start with `popen()`, wait until a `stop` entry appears in the journal, `proc.kill()` the outer shell, immediately run a second invocation -> exit 75 `deferred: lock`; then wait up to 15 s for `rm` and `up` to appear in the journal (the detached worker finished the sequence).
  12. **One backend environment for everything:** `env={"CONTAINERS_STORAGE_CONF": str(tmp/"storage.conf")}` -> **every** `podman` and `podman-compose` journal entry (inventory, recheck, tag, stop, rm, `up`) has `env["CONTAINERS_STORAGE_CONF"]` equal to that exact value, and none received any other variable the stub records except the allowlisted ones.
  13. Deadline crossing: `prior_tag_sleep=5`, overrides `LLM_ENV_UPDATE_IDLE_SECONDS=1`, `LLM_ENV_UPDATE_MAX_WAIT_SECONDS=12` -> exit 75 `deferred: deadline`, mutations only `pull` (whether the gate reaches `ready` before 12 s or not, no tag/lifecycle command ever runs after the deadline).
  14. `COMPOSE_PROFILES=x` in the environment -> exit 75 `deferred: input`, journal has no mutation.
  (The spec acceptance values 60/600 are pinned by `test_valid_defaults_and_boundaries` in Task 4; a 60 s real-time shell case adds no coverage.)
- [ ] **Step 3: Run, verify FAIL** (`uv run pytest tests/test_omniroute_update.py tests/test_cli.py tests/test_update_omniroute_script.py -k "projection or verify_commit or update_omniroute or stub" -v`).
- [ ] **Step 4: Implement `to_runtime_literal`, `build_projection`, `verify_projection`, `projection_matches`, `verify_commit`, the three CLIs, the `lib.sh` helpers, the script and the worker.** In `pylib/omniroute_update.py` (add `import copy`; `_env_dict` and `_strings` already exist from Task 2):

```python
def _map_strings(obj, fn):
    if isinstance(obj, str):
        return fn(obj)
    if isinstance(obj, dict):
        return {k: _map_strings(v, fn) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_map_strings(v, fn) for v in obj]
    return obj


def _escape(obj): return _map_strings(obj, lambda s: s.replace("$", "$$"))
def _unescape(obj): return _map_strings(obj, lambda s: s.replace("$$", "$"))

_NAMESPACE_KEYS = ("pod", "network_mode", "links", "volumes_from")


def to_runtime_literal(svc: dict, inspect_doc: dict) -> dict:
    running = _env_dict((inspect_doc.get("Config") or {}).get("Env"))
    forms = set()
    for key, value in _env_dict(svc.get("environment")).items():
        actual = running.get(key)
        if actual is None or (value != actual and value != actual.replace("$", "$$")):
            raise Deferral(Reason.ownership, f"effective environment {key} differs from the running container")
        if "$" in actual:
            forms.add("plain" if value == actual else "escaped")
    if len(forms) > 1:
        raise Deferral(Reason.input, "mixed dollar escaping in provider output")
    if forms == {"escaped"}:
        return _unescape(copy.deepcopy(svc))
    if not forms and any("$" in s for s in _strings(svc)):
        raise Deferral(Reason.input, "cannot determine dollar escaping form")
    return copy.deepcopy(svc)


def _core(svc: dict) -> dict:
    return {k: v for k, v in svc.items() if k not in ("image", "depends_on", "networks")}


def projection_matches(baseline_rt: dict, projected_rt: dict) -> bool:
    return _core(baseline_rt) == _core(projected_rt)


def _resolved_networks(svc, effective, project) -> dict[str, str]:
    """compose network key -> real network name for the networks this service attaches to."""
    declared = effective.get("networks") or {}
    keys = list(svc["networks"]) if svc.get("networks") else ["default"]
    return {k: (declared.get(k) or {}).get("name") or f"{project}_{k}" for k in keys}


def _mounts(svc, effective, project, inspect_doc) -> dict[str, dict]:
    """Validate every mount against the inspected container; return the external named volumes to declare."""
    mounts = inspect_doc.get("Mounts") or []
    vols = {m["Name"] for m in mounts if m.get("Type") == "volume"}
    binds = {m.get("Source") for m in mounts if m.get("Type") == "bind"}
    out: dict[str, dict] = {}
    for entry in svc.get("volumes") or []:
        source, kind = (entry.split(":", 1)[0], None) if isinstance(entry, str) else (entry.get("source", ""), entry.get("type"))
        if kind == "bind" or source.startswith(("/", ".", "~")):
            if not source.startswith("/"):
                raise Deferral(Reason.input, "relative or home-relative bind source cannot be projected")
            if source not in binds:
                raise Deferral(Reason.ownership, "bind source differs from the running container")
            continue
        real = ((effective.get("volumes") or {}).get(source) or {}).get("name") or f"{project}_{source}"
        if real not in vols:
            raise Deferral(Reason.ownership, "named volume differs from the running container")
        out[source] = {"external": True, "name": real}
    return out


def build_projection(effective, service, inspect_doc, image, project):
    if service not in (effective.get("services") or {}):
        raise Deferral(Reason.ownership, f"effective compose has no service {service}")
    base = to_runtime_literal(copy.deepcopy(effective["services"][service]), inspect_doc)
    for key in _NAMESPACE_KEYS:
        if key in base:
            raise Deferral(Reason.input, f"unsupported {key} on {service}")
    inspected_nets = (inspect_doc.get("NetworkSettings") or {}).get("Networks") or {}
    key_to_name = _resolved_networks(base, effective, project)
    if set(key_to_name.values()) != set(inspected_nets):
        raise Deferral(Reason.ownership, "network attachments differ from the running container")
    volumes = _mounts(base, effective, project, inspect_doc)
    svc = copy.deepcopy(base)
    svc["image"] = image
    svc.pop("depends_on", None)
    aliases = {key_to_name[k]: (v or {}) for k, v in (base.get("networks") or {}).items()} if isinstance(base.get("networks"), dict) else {}
    svc["networks"] = {name: aliases.get(name, {}) for name in sorted(inspected_nets)}
    return {"services": {service: _escape(svc)},
            "networks": {name: {"external": True, "name": name} for name in sorted(inspected_nets)},
            "volumes": volumes}


def verify_projection(effective, service, inspect_doc, image, project, projection_path, provider, run) -> None:
    baseline = to_runtime_literal(copy.deepcopy(effective["services"][service]), inspect_doc)
    again = effective_config(provider, project, projection_path, run)         # the provider's own reading of the file
    if list(again["services"]) != [service]:
        raise Deferral(Reason.ownership, "provider resolves a different service set")
    svc = again["services"][service]
    if not isinstance(svc, dict) or svc.get("image") != image or svc.get("depends_on"):
        raise Deferral(Reason.ownership, "provider resolves a different image or still has a dependency")
    if any(key in svc for key in _NAMESPACE_KEYS):
        raise Deferral(Reason.input, "provider output shares a namespace")
    resolved = to_runtime_literal(copy.deepcopy(svc), inspect_doc)
    if not projection_matches(baseline, resolved):
        raise Deferral(Reason.ownership, "provider reads the projection differently from the effective service")
    if set(_resolved_networks(resolved, again, project).values()) != set((inspect_doc.get("NetworkSettings") or {}).get("Networks") or {}):
        raise Deferral(Reason.ownership, "provider attaches different networks")
    _mounts(resolved, again, project, inspect_doc)
```

  `verify_commit`:

```python
def verify_commit(run, out_dir, service, old_id, config_id, timeout, *, expect_no_deps=False,
                  env=os.environ, sleep=time.sleep, clock=time.monotonic) -> str:
    baseline = Snapshot.from_json(Path(out_dir, "snapshot.json").read_text())
    target = json.loads(Path(out_dir, "target.json").read_text())[0]
    t_labels = (target.get("Config") or {}).get("Labels") or {}
    want_image = config_id if config_id else canon_image_id(target["Image"])
    want_nets = sorted(v["NetworkID"] for v in (target["NetworkSettings"]["Networks"]).values())
    want_vols = sorted(m["Name"] for m in target.get("Mounts") or [] if m.get("Type") == "volume")
    deadline, new_id, problem = clock() + timeout, "", "not replaced"
    bounded = with_deadline(run, deadline, clock)                  # slow-but-successful commands are discarded too
    while True:
        try:
            snap = take_snapshot(bounded, env)
        except Deferral as exc:
            raise UpdateError(f"inventory unavailable during verification: {exc.reason.value}; old={old_id[:12]}") from exc
        cands = [c for c, d in snap.docs.items()
                 if ((d.get("Config") or {}).get("Labels") or {}).get(LABEL_SERVICE) == service
                 and ((d.get("Config") or {}).get("Labels") or {}).get(LABEL_PROJECT) == t_labels.get(LABEL_PROJECT)
                 and c != old_id]
        changed = sorted(c[:12] for c, h in baseline.containers.items() if c != old_id and snap.containers.get(c) != h)
        if changed:
            raise UpdateError(f"sibling containers changed during replacement: {', '.join(changed)}; old={old_id[:12]}")
        if len(cands) == 1:
            new_id, doc = cands[0], snap.docs[cands[0]]
            nets = sorted(v["NetworkID"] for v in doc["NetworkSettings"]["Networks"].values())
            vols = sorted(m["Name"] for m in doc.get("Mounts") or [] if m.get("Type") == "volume")
            if canon_image_id(doc["Image"]) != want_image:
                raise UpdateError(f"new {service} runs an unexpected image; old={old_id[:12]} new={new_id[:12]}")
            if nets != want_nets or vols != want_vols:
                raise UpdateError(f"new {service} resource attachments differ; old={old_id[:12]} new={new_id[:12]}")
            if expect_no_deps and doc["Dependencies"]:
                raise UpdateError(f"new {service} still has native dependencies; old={old_id[:12]} new={new_id[:12]}")
            if (doc.get("State") or {}).get("Health", {}).get("Status") == "healthy":
                if clock() >= deadline:
                    raise UpdateError(f"new {service} became healthy only after the {timeout}s budget; old={old_id[:12]} new={new_id[:12]}")
                return new_id
            problem = "not healthy"
        elif len(cands) > 1:
            raise UpdateError(f"more than one replacement {service} container; old={old_id[:12]}")
        if clock() >= deadline:
            raise UpdateError(f"new {service} {problem} within {timeout}s; old={old_id[:12]} new={new_id[:12] or 'none'}")
        sleep(1)
```

  Note the unit-test expectation strings: "not replaced" (same id: `cands` empty and the loop keeps polling until timeout, message `... not replaced within 3s`), "image", "sibling", "not healthy within 5s", "dependenc" (message `still has native dependencies`), "inventory unavailable" (slow command). The first loop iteration for the sibling-change case raises immediately.

  Register `verify-staged`, `projection` and `verify-commit` in `register(sub)` with exactly the flags listed in **Interfaces** (`--timeout` as `int`, `--expect-no-deps` a flag, `--config-id` optional). CLI wrappers in `pylib/omniroute_cli.py` (all `@guarded`; add the imports they use: `ImageIdentity`, `verify_staged`, `build_projection`, `verify_projection`, `verify_commit`, `check_private_dir`, `write_private`, `Path`):

```python
@guarded
def cmd_verify_staged(args) -> int:
    identity = ImageIdentity(**json.loads(Path(args.resolved).read_text()))
    inspected = json.loads(Path(args.inspect).read_text())
    if not (isinstance(inspected, list) and len(inspected) == 1 and isinstance(inspected[0], dict)):
        raise UpdateError("unexpected image inspect shape")
    verify_staged(identity, inspected[0])
    return 0


@guarded
def cmd_projection(args, run=None, env=None) -> int:
    env = os.environ if env is None else env
    run = run or scrubbed_runner(env)
    check_private_dir(args.dir)
    d = Path(args.dir)
    effective = json.loads((d / "effective.json").read_text())
    target = json.loads((d / "target.json").read_text())[0]
    dep = json.loads((d / "deployment.json").read_text())
    write_private(args.out, json.dumps(build_projection(effective, args.service, target, args.image, dep["project"])))
    verify_projection(effective, args.service, target, args.image, dep["project"], args.out, dep["provider"], run)
    return 0


@guarded
def cmd_verify_commit(args, run=None, env=None) -> int:
    env = os.environ if env is None else env
    run = run or scrubbed_runner(env)
    check_private_dir(args.dir)
    print(f"new_id={verify_commit(run, args.dir, args.service, args.old_id, args.config_id, args.timeout, expect_no_deps=args.expect_no_deps, env=env)}")
    return 0
```

  `tools/lib.sh` additions (shellcheck-clean; `backend_env` is consumed by sourcing scripts):

```bash
# One Podman backend environment for EVERY updater invocation (matches pylib backend_env()): env -i plus an allowlist,
# so the backend that was inventoried is the backend that is mutated.
backend_env_init() {
    local v
    # shellcheck disable=SC2034 # Consumed by the scripts that source this file.
    backend_env=(env -i "PATH=$PATH" "HOME=$HOME")
    if [[ -n "${XDG_RUNTIME_DIR:-}" ]]; then backend_env+=("XDG_RUNTIME_DIR=$XDG_RUNTIME_DIR"); fi
    for v in CONTAINERS_STORAGE_CONF CONTAINERS_CONF CONTAINERS_REGISTRIES_CONF; do
        if [[ -n "${!v:-}" ]]; then backend_env+=("$v=${!v}"); fi
    done
}

# Take the canonical update lock on FD 9 (kept until the process, or the detached worker that inherits it, exits).
# Needs the `llm` array and a private $work directory. Exits 75 `deferred: lock` on contention.
acquire_update_lock() {
    local rc=0 lock_file
    lock_file="$("${llm[@]}" lock-path 2>"$work/lock.err")" || rc=$?
    if (( rc == 75 )); then cat "$work/lock.err"; exit 75; fi
    if (( rc != 0 )); then cat "$work/lock.err" >&2; exit "$rc"; fi
    exec 9>>"$lock_file"
    if [[ -L "$lock_file" || "$(stat -Lc %i /proc/self/fd/9)" != "$(stat -c %i "$lock_file")" ]] || ! flock -n 9; then
        echo "deferred: lock"
        exit 75
    fi
}
```

  The script, `scripts/update-omniroute.sh` (write it in full; there is no outline left):

```bash
#!/usr/bin/env bash
# Idle-aware, OmniRoute-only updater. Exit 0 = unchanged/updated, 75 = safe deferral, 1 = error.
# Not zero-downtime, no rollback, observation is not admission control (see the design spec).
set -euo pipefail
# shellcheck disable=SC1091 # Resolved from this script at runtime.
# shellcheck source=../tools/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/../tools/lib.sh"
require_cmd podman skopeo jq yq uv journalctl flock setsid timeout

# tools/lib.sh exports COMPOSE_FILE and COMPOSE_INSPECT_DIR; the CLI rejects every COMPOSE_* variable,
# so keep the paths locally and drop the exports before any CLI runs.
config_path="$CONFIG_PATH"
compose_path="$COMPOSE_FILE"
unset COMPOSE_FILE COMPOSE_INSPECT_DIR
# An array (not a shell function) so that `setsid "${llm[@]}" observe ...` runs a real executable.
llm=(uv run --project "$REPO_DIR" "$REPO_DIR/llmenv.py" omniroute-update)
backend_env_init
cli() { "${llm[@]}" "$@" 9>&-; }                    # CLI steps never inherit the update lock
pm() { "${backend_env[@]}" podman "$@" 9>&-; }      # Podman steps use the validated backend environment, no lock FD
defer() { echo "deferred: $1"; exit 75; }

# 0. Overrides: capture first, check status, parse without eval (eval would hide a failed substitution).
ov_args=()
if [[ -v LLM_ENV_UPDATE_IDLE_SECONDS ]]; then ov_args+=(--idle "$LLM_ENV_UPDATE_IDLE_SECONDS"); fi
if [[ -v LLM_ENV_UPDATE_MAX_WAIT_SECONDS ]]; then ov_args+=(--max-wait "$LLM_ENV_UPDATE_MAX_WAIT_SECONDS"); fi
overrides_out="$("${llm[@]}" overrides "${ov_args[@]}")" || exit 1
min_wait="" max_deferred_wait=""
while IFS== read -r k v; do
    case "$k" in
        min_wait) min_wait="$v" ;;
        max_deferred_wait) max_deferred_wait="$v" ;;
        *) exit 1 ;;
    esac
done <<<"$overrides_out"
[[ "$min_wait" =~ ^[0-9]+$ && "$max_deferred_wait" =~ ^[0-9]+$ ]] || exit 1
health_timeout="${LLM_ENV_HEALTH_TIMEOUT_SECONDS:-60}"
[[ "$health_timeout" =~ ^[1-9][0-9]*$ ]] || { echo "error: invalid LLM_ENV_HEALTH_TIMEOUT_SECONDS" >&2; exit 1; }

work="$(mktemp -d)"                      # 0700; private artifacts, projection, deadline, password file
observer_pid=""
stop_observer() {
    if [[ -n "$observer_pid" ]]; then
        kill -TERM -- "-$observer_pid" 2>/dev/null || true
        wait "$observer_pid" 2>/dev/null || true
        observer_pid=""
    fi
}
cleanup() { stop_observer; rm -rf "$work"; }
trap cleanup EXIT

# 1. Lock before any staging. The path comes from the inspected backend; the file is never unlinked.
acquire_update_lock

# 2. Preflight (inspects identity after the lock): artifacts in $work, key=value on stdout.
rc=0
cli preflight --config "$config_path" --compose "$compose_path" --out-dir "$work" >"$work/deploy.env" || rc=$?
if (( rc != 0 )); then cat "$work/deploy.env"; exit "$rc"; fi
container_id="" image_id="" image_ref="" project="" stop_seconds="" pinned="" os_name="" architecture="" variant="" provider=""
while IFS== read -r k v; do
    case "$k" in
        container_id) container_id="$v" ;;
        image_id) image_id="$v" ;;
        image_ref) image_ref="$v" ;;
        project) project="$v" ;;
        stop_seconds) stop_seconds="$v" ;;
        pinned) pinned="$v" ;;
        os) os_name="$v" ;;
        architecture) architecture="$v" ;;
        variant) variant="$v" ;;
        provider) provider="$v" ;;
        *) ;;
    esac
done <"$work/deploy.env"
[[ "$container_id" =~ ^[0-9a-f]{64}$ && "$image_id" =~ ^sha256:[0-9a-f]{64}$ && "$stop_seconds" =~ ^[0-9]+$ ]] || { echo "error: malformed preflight output" >&2; exit 1; }

# 3. Resolve the immutable target; nothing to do when it is already running.
rc=0
cli resolve --reference "$image_ref" --os "$os_name" --arch "$architecture" --variant "$variant" --json "$work/resolved.json" >"$work/resolve.out" || rc=$?
if (( rc != 0 )); then cat "$work/resolve.out"; exit "$rc"; fi
config_id="$(jq -r .config_id "$work/resolved.json")"
immutable_ref="$(jq -r .immutable_ref "$work/resolved.json")"
[[ "$config_id" =~ ^sha256:[0-9a-f]{64}$ ]] || { echo "error: malformed resolved identity" >&2; exit 1; }
if [[ "$config_id" == "$image_id" ]]; then echo "unchanged"; exit 0; fi
staged_id="${config_id#sha256:}"

# 4. Stage and verify. The configured tag is NOT moved here.
pm pull "$immutable_ref" >/dev/null
pm image inspect --format json "$immutable_ref" >"$work/staged.json"
cli verify-staged --resolved "$work/resolved.json" --inspect "$work/staged.json"

# 5. Projection + provider round trip (75/1 before any observation).
cli projection --dir "$work" --service omniroute --image "$immutable_ref" --out "$work/projection.yml"

# 6. Observe. The wait budget starts now (after staging). The supervisor gets the password through a 0600 file
#    inside $work, never argv/env, and publishes the gate verdict in $work/latest.json.
( umask 077; yq -r '.omniroute.initial_password // ""' "$config_path" >"$work/pw" )
cli deadline --file "$work/deadline" --init "$max_deferred_wait"
stat_line="$(<"/proc/$$/stat")"; stat_rest="${stat_line##*) }"; read -r -a stat_fields <<<"$stat_rest"
lease_tick="${stat_fields[19]}"                                   # field 22 of /proc/$$/stat
setsid "${llm[@]}" observe --dir "$work" --config "$config_path" --compose "$compose_path" \
    --password-file "$work/pw" --lease-pid "$$" --lease-start-tick "$lease_tick" \
    --lifetime "$((max_deferred_wait + 10))" --interval 5 \
    --min-wait "$min_wait" --deadline-file "$work/deadline" 9>&- >/dev/null &
observer_pid=$!

stale_limit=15                                                    # three sample intervals without a new latest.json
last_seq=-1
stale_since=$SECONDS
while true; do
    sleep 1 9>&-
    rc=0
    dl="$(cli deadline --file "$work/deadline")" || rc=$?         # exclusive deadline, authoritative in the shell too
    if (( rc != 0 )); then printf '%s\n' "$dl"; exit "$rc"; fi
    line="$(jq -r '[.sequence, .verdict, .reason] | join(" ")' "$work/latest.json" 2>/dev/null || true)"
    read -r seq verdict reason <<<"${line:--1 none none}"
    if [[ "$seq" =~ ^[0-9]+$ ]] && (( seq > last_seq )); then
        last_seq="$seq"
        stale_since=$SECONDS
        case "$verdict" in
            wait) ;;
            ready) break ;;
            defer-deadline) defer deadline ;;
            defer-unknown) [[ "$reason" =~ ^[a-z]+$ ]] || reason=feed; defer "$reason" ;;
            *) echo "error: unexpected observer verdict" >&2; exit 1 ;;
        esac
    elif (( SECONDS - stale_since > stale_limit )); then
        defer feed
    fi
done

# 7. Hand off to the detached commit worker. It inherits FD 9 (the lock) and keeps it until it exits,
#    even if this shell is SIGKILLed; every Podman/provider child inside it closes FD 9.
stop_observer
printf 'config_id=%s\nimmutable_ref=%s\nstaged_id=%s\nhealth_timeout=%s\n' \
    "$config_id" "$immutable_ref" "$staged_id" "$health_timeout" >"$work/commit.env"
worker_budget=$(( stop_seconds + 3 * health_timeout + 120 ))
rc=0
setsid --wait timeout --kill-after=10 "$worker_budget" \
    bash "$REPO_DIR/scripts/omniroute-commit-worker.sh" "$work" "$config_path" "$compose_path" || rc=$?
if (( rc == 124 || rc == 137 )); then
    echo "error: the commit worker exceeded its time budget; inspect the deployment manually (no rollback was attempted)" >&2
    exit 1
fi
exit "$rc"
```

  The worker, `scripts/omniroute-commit-worker.sh`:

```bash
#!/usr/bin/env bash
# Commit worker: detached from the updater shell; keeps FD 9 (the update lock) until it exits.
# Post-commit mutation is NOT transactional: failures are reported, never rolled back.
set -euo pipefail
work="$1" config_path="$2" compose_path="$3"
# shellcheck disable=SC1091 # Resolved from this script at runtime.
# shellcheck source=../tools/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/../tools/lib.sh"
unset COMPOSE_FILE COMPOSE_INSPECT_DIR
llm=(uv run --project "$REPO_DIR" "$REPO_DIR/llmenv.py" omniroute-update)
backend_env_init
trap 'rm -rf "$work"' EXIT
cli() { "${llm[@]}" "$@" 9>&-; }
pm() { "${backend_env[@]}" podman "$@" 9>&-; }

config_id="" immutable_ref="" staged_id="" health_timeout=""
while IFS== read -r k v; do
    case "$k" in config_id) config_id="$v" ;; immutable_ref) immutable_ref="$v" ;; staged_id) staged_id="$v" ;; health_timeout) health_timeout="$v" ;; *) ;; esac
done <"$work/commit.env"
container_id="" image_ref="" project="" stop_seconds="" pinned="" provider=""
while IFS== read -r k v; do
    case "$k" in container_id) container_id="$v" ;; image_ref) image_ref="$v" ;; project) project="$v" ;; stop_seconds) stop_seconds="$v" ;; pinned) pinned="$v" ;; provider) provider="$v" ;; *) ;; esac
done <"$work/deploy.env"

new_id="" prior_tag="" remaining=""
fail() {
    printf 'error: update failed at stage: %s\n' "$1" >&2
    printf 'old container: %s\nnew container: %s\nprior image tag target: %s\n' "${container_id:0:12}" "${new_id:0:12}" "${prior_tag:0:12}" >&2
    printf 'No rollback was attempted; recovery is manual (the previous image is still present).\n' >&2
    exit 1
}
# Remaining wait budget; exits 75 `deferred: deadline` when the exclusive deadline has passed.
budget_left() {
    local out rc=0
    out="$(cli deadline --file "$work/deadline")" || rc=$?
    if (( rc != 0 )); then printf '%s\n' "$out"; exit "$rc"; fi
    remaining="${out#remaining=}"
}

# (a) second full inventory + fingerprint recompute, bounded by the remaining budget (a slow success after the
#     deadline is discarded); any drift or incoming dependent defers before mutation. The backend identity is
#     part of the snapshot, so it is re-verified under the same environment the mutations below use.
budget_left
rc=0
cli preflight --recheck --budget "$remaining" --config "$config_path" --compose "$compose_path" --out-dir "$work" >"$work/recheck.out" || rc=$?
if (( rc != 0 )); then cat "$work/recheck.out"; exit "$rc"; fi
# (b) prior tag target, for the failure report only.
prior_tag="$(pm image inspect --format '{{.Id}}' "$image_ref" 2>/dev/null || true)"
# (c) exclusive deadline, rechecked after (a)+(b) and immediately before the first mutation.
budget_left

# --- first mutation ---
if [[ "$pinned" != "1" ]]; then
    pm tag "$staged_id" "$image_ref" || fail "tag"
fi
pm stop --time "$stop_seconds" "$container_id" || fail "stop"
pm rm "$container_id" || fail "rm"                                # by ID only: no forced or dependency-following removal
if [[ -n "$(pm ps --all --filter name='^omniroute$' --format '{{.ID}}')" ]]; then
    fail "name still in use after removal"                         # never remove an unexpected same-name container
fi
"${backend_env[@]}" "$provider" --in-pod false --env-file /dev/null -p "$project" -f "$work/projection.yml" \
    up -d --no-deps --no-recreate --no-build --pull never omniroute 9>&- || fail "up"
rc=0
verify_out="$(cli verify-commit --dir "$work" --service omniroute --old-id "$container_id" --config-id "$config_id" --timeout "$health_timeout")" || rc=$?
if (( rc != 0 )); then fail "verify-commit"; fi
while IFS== read -r k v; do
    case "$k" in new_id) new_id="$v" ;; *) ;; esac
done <<<"$verify_out"
[[ "$new_id" =~ ^[0-9a-f]{64}$ ]] || fail "verify-commit output"
echo "updated ${container_id:0:12} -> ${new_id:0:12}"
```

  (`--budget` on `preflight --recheck` is added in Task 2's CLI; the stage name printed by `fail` is `stage: up` etc.; the worker's `fail` text must contain the literal `stage: ` prefix the case-10 test asserts.)
- [ ] **Step 5: Run, verify PASS.** `uv run pytest tests/test_omniroute_update.py tests/test_cli.py tests/test_update_omniroute_script.py -v`, then `make validate && make test`, then confirm the scripts contain none of the forbidden verbs: `rg -n -e '--force' -e '--depend' -e '--remove-orphans' -e ' down( |$)' -e 'compose down' scripts/update-omniroute.sh scripts/omniroute-commit-worker.sh` must print nothing (exit 1).
- [ ] **Step 6: Commit if approved.**

---

### Task 7: Remote-setup dependency migration and bounded `/config`

**Files:**
- Modify: `pylib/compose.py` (remove remote-setup `depends_on`), `tests/test_compose.py:438,479`, `pylib/remote_setup.py`, `tests/test_remote_setup.py`, `pylib/omniroute_update.py` (`preflight(..., service=...)` already specified in Task 2), `pylib/omniroute_cli.py` (`preflight --service`, already registered in Task 2)
- Create: `scripts/migrate-remote-setup.sh`; extend `tests/test_update_omniroute_script.py`
- Modify: `scripts/help.sh`, `Makefile` (`migrate-remote-setup` target delegating to the script)

**Interfaces:**
- Consumes: Task 2 `preflight`/`recheck` and the artifact directory, Task 6 `projection`, `verify-commit` (with `--service remote-setup --expect-no-deps` and no `--config-id`), `tools/lib.sh` `backend_env_init` and `acquire_update_lock`, Task 5 `lock-path`, Task 6 `World` test infrastructure.
- Produces: `omniroute-update preflight --service remote-setup` (default stays `omniroute`). Same artifact directory and exit codes as Task 2; for a non-OmniRoute service only the generic checks apply: exactly one container whose `LABEL_SERVICE` equals the service, original project label, running and healthy, no incoming dependency on it, effective service present with no unsupported keys, fingerprint over the whole deployment; the OmniRoute-specific image/port/password/`REQUIRE_API_KEY` agreement checks (container and effective compose) run only for `omniroute`; `stop_grace_period` defaults to 10 seconds when absent for non-OmniRoute services.
- Produces: `make migrate-remote-setup`. Behavior, in order; everything before the worker is read-only:
  1. **Compose source check:** the *deployed* `COMPOSE_FILE` must already contain no `services.remote-setup.depends_on` (`yq -e '.services["remote-setup"] | has("depends_on") | not'`). If it does, exit 1 with: "re-render the deployed compose first (README 'Operations'); the migration never rewrites it". **Premise correction (verified in `setup/render-unit.sh`):** re-rendering is *not* container-neutral. The script runs a `podman run --rm` device probe (line ~36), and with System One enabled runs `scripts/phoenix.sh prepare` and `scripts/system-one-observer-build.sh` (which can create networks, pull images and build images); it also stops and disables a legacy `llm-mdns.service` if present and runs `systemctl --user daemon-reload`. There is no compose-only render target today; this plan does not run the script and does not add one. The README states these side effects and leaves the re-render to the user (an open decision is recorded in the Self-review).
  2. Lock via `acquire_update_lock`, then `preflight --service remote-setup --out-dir "$work"` (defers on incoming dependents or any ownership disagreement).
  3. **No-op (exit 0)** when `jq -e '.[0].Dependencies | length == 0' "$work/target.json"` succeeds, with `remote-setup already has no native dependency; nothing changed`.
  4. Proof volume exists: `jq -e --arg v "${project}_remote-setup-data" '.volume_names | index($v)' "$work/deployment.json"`; build the projection with `projection --service remote-setup --image "${image_id#sha256:}"` (the running image's own bare id, so no pull; the provider round trip of Task 6 runs on it).
  5. **Mutation in a detached bounded worker** (the same `setsid --wait timeout` + inherited FD 9 + `9>&-` children pattern as Task 6; the script re-executes itself as `bash "${BASH_SOURCE[0]}" --worker "$work" ...`): recheck (`--budget 60`), `podman stop --time <stop_seconds> <id>`, `podman rm <id>` (by ID only), `podman-compose --in-pod false --env-file /dev/null -p <project> -f <projection> up -d --no-deps --no-recreate --no-build --pull never remote-setup` under `backend_env`, then `verify-commit --service remote-setup --expect-no-deps` (new ID, empty `Dependencies`, healthy within `LLM_ENV_HEALTH_TIMEOUT_SECONDS`, same network/volume attachments, every other container ID and hash unchanged). Never a forced recreate, `compose down`, orphan removal or volume deletion.
- Recovery (printed on failure and documented): if the worker fails after `stop`, the old container may already be gone; because the deployed file has no remote-setup dependency, `podman-compose -p <project> -f "$COMPOSE_FILE" up -d --no-deps remote-setup` creates it plainly. While remote-setup is down or `/config` cannot reach OmniRoute, `/config` answers 503 + `Retry-After: 5` (bounded, never a hang) and only remote client installs are affected; OmniRoute itself is untouched.
- Behavior: `GET /config` returns **503 + `Retry-After: 5`** and a JSON `{"error": ...}` (the message still names OmniRoute and, for the existing failures, the unchanged `could not reach OmniRoute: ...` / `could not fetch OmniRoute combos: ...` text) when OmniRoute is unreachable, when another `/config` computation is still running (single flight) or when the aggregate `CONFIG_DEADLINE_SECONDS` (module constant, default 20.0, monkeypatched by tests; no environment seam) passes; an unexpected exception in the worker still produces the existing JSON 500; `/setup.sh` is unaffected.

- [ ] **Step 1: Failing tests**
  - compose (`tests/test_compose.py:438,479`, the two tests that currently assert the dependency; rename them `test_remote_setup_service_has_no_native_dependency_on_omniroute` and `test_remote_setup_has_no_dependency_regardless_of_llm_server_state`): assert `"depends_on" not in document["services"]["remote-setup"]`, and add one test that everything else of the remote-setup service (volume `remote-setup-data`, ports, healthcheck, `OMNIROUTE_INTERNAL_URL`/`OMNIROUTE_PORT` env) is deep-equal to the pre-change render minus only that key (compare against `{**service, "depends_on": ...}` removed via a copy of the old literal in the test).
  - `preflight --service remote-setup` (unit, Task 2 fixtures): `preflight_world(tmp_path, effective={"services": {"remote-setup": {"image": "x", "container_name": "remote-setup", "volumes": ["remote-setup-data:/app/data"]}}, "networks": {"default": {}}, "volumes": {"remote-setup-data": {}}}, extra_containers={RS: inspect_doc(RS, name="remote-setup", deps=[OMNI])})` called as `preflight(..., service="remote-setup")` passes and returns `container_id == RS`; a container depending on `RS` itself defers `Reason.ownership`; OmniRoute-only checks (password mismatch, effective-compose image/port) are not applied to it.
  - remote_setup (`tests/test_remote_setup.py`). These tests use the file's **real** helpers and imports: `_start_server(RemoteSetupHandler)` (line 486), `remote_setup_module` (`import pylib.remote_setup as remote_setup_module`, line 19), `OmniRouteError` (already imported, line 20), `http.client`, `json`, `threading`, `time`, `monkeypatch.setenv` for `OMNI_ROUTER_MASTER_KEY`/`OMNIROUTE_INTERNAL_URL`/`OMNIROUTE_DASHBOARD_PASSWORD`/`OMNIROUTE_PORT`/`MODELS_JSON` and `monkeypatch.setattr(remote_setup_module, "CACHE_PATH", ...)` exactly like `test_config_returns_the_scoped_key_and_omniroute_address`. Add `import contextlib` to the module's imports and these two small helpers (there is no existing `server` fixture and no `get_config` helper; the existing tests inline the request, which the new helpers factor out for the new tests only):

```python
@contextlib.contextmanager
def _config_server(monkeypatch, tmp_path, fake_port=1):
    monkeypatch.setattr(remote_setup_module, "CACHE_PATH", tmp_path / "api-key.json")
    monkeypatch.setenv("OMNI_ROUTER_MASTER_KEY", "test-key")
    monkeypatch.setenv("OMNIROUTE_INTERNAL_URL", f"http://127.0.0.1:{fake_port}")
    monkeypatch.setenv("OMNIROUTE_DASHBOARD_PASSWORD", "dashboard-pw")
    monkeypatch.setenv("OMNIROUTE_PORT", "20128")
    monkeypatch.setenv("MODELS_JSON", "[]")
    server, thread = _start_server(RemoteSetupHandler)
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        thread.join()


def _get_config(port):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("GET", "/config", headers={"Authorization": "Bearer test-key"})
    response = conn.getresponse()
    return response.status, response.getheader("Retry-After"), json.loads(response.read())


def _wait_flight_idle():
    end = time.monotonic() + 3
    while remote_setup_module._config_flight.locked() and time.monotonic() < end:
        time.sleep(0.01)
    assert not remote_setup_module._config_flight.locked()


def test_config_returns_503_with_retry_after_when_omniroute_unreachable(tmp_path, monkeypatch):
    def down(*args, **kwargs):
        raise OmniRouteError("connection refused")
    monkeypatch.setattr(remote_setup_module, "ensure_api_key", down)
    with _config_server(monkeypatch, tmp_path) as port:
        status, retry_after, body = _get_config(port)
    assert status == 503 and retry_after == "5" and "OmniRoute" in body["error"]
    _wait_flight_idle()
```

  Edit the existing `test_config_returns_502_when_combo_context_fails` (line 783) in place; do not duplicate it: rename it `test_config_returns_503_when_combo_context_fails`, change `assert response.status == 502` to `assert response.status == 503`, and add `assert response.getheader("Retry-After") == "5"` (its `assert "combo" in payload["error"]` stays true because the worker keeps the `could not fetch OmniRoute combos: ...` text). Then add:

```python
def test_config_blocked_backend_answers_503_within_deadline(tmp_path, monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(remote_setup_module, "ensure_api_key", lambda *a, **k: release.wait(30) or "key")
    monkeypatch.setattr(remote_setup_module, "compute_combo_context", lambda *a, **k: [])
    monkeypatch.setattr(remote_setup_module, "CONFIG_DEADLINE_SECONDS", 0.5)
    with _config_server(monkeypatch, tmp_path) as port:
        start = time.monotonic()
        status, retry_after, body = _get_config(port)
        assert status == 503 and retry_after == "5" and time.monotonic() - start < 1.5
        release.set()
        _wait_flight_idle()


def test_config_is_single_flight(tmp_path, monkeypatch):
    release, calls = threading.Event(), []
    def slow(*a, **k):
        calls.append(1)
        release.wait(30)
        return "key"
    monkeypatch.setattr(remote_setup_module, "ensure_api_key", slow)
    monkeypatch.setattr(remote_setup_module, "compute_combo_context", lambda *a, **k: [])
    monkeypatch.setattr(remote_setup_module, "CONFIG_DEADLINE_SECONDS", 5.0)
    with _config_server(monkeypatch, tmp_path) as port:
        first = threading.Thread(target=_get_config, args=(port,))
        first.start()
        time.sleep(0.2)                                   # the first worker now holds the flight lock
        start = time.monotonic()
        status, retry_after, _ = _get_config(port)        # must not wait for the 5 s deadline or start a second worker
        assert status == 503 and retry_after == "5" and time.monotonic() - start < 1.0 and len(calls) == 1
        release.set()
        first.join()
        _wait_flight_idle()


def test_config_unexpected_worker_exception_is_still_a_json_500(tmp_path, monkeypatch):
    def boom(*args, **kwargs):
        raise ValueError("unexpected")
    monkeypatch.setattr(remote_setup_module, "ensure_api_key", boom)
    with _config_server(monkeypatch, tmp_path) as port:
        status, retry_after, body = _get_config(port)
    assert status == 500 and "error" in body
    _wait_flight_idle()
```

  (`/setup.sh` staying unaffected is already pinned by `test_setup_sh_is_served_without_auth_and_embeds_the_request_host`; the helper-signature guard lives in Task 3.)
  - shell (migration cases in `tests/test_update_omniroute_script.py`, using `MigrationWorld`, defined below): (a) deployed compose still containing the edge -> exit 1, journal empty; (b) remote-setup already dependency-free -> exit 0, `mutations() == []`; (c) dependency present -> `mutations()` is exactly `("podman","stop","--time","10",RS)`, `("podman","rm",RS)`, one `("podman-compose","--in-pod","false","--env-file","/dev/null","-p",PROJECT,"-f",<work>/projection.yml,"up","-d","--no-deps","--no-recreate","--no-build","--pull","never","remote-setup")`, no argv contains `--force-recreate`, `down`, `--depend`, `--remove-orphans`, and every podman/podman-compose journal entry carries the same `CONTAINERS_STORAGE_CONF` as the test's env; (d) `drift_after_projection=True` (a sibling container appears between the main phase and the worker's recheck) -> exit 75 `deferred: ownership` with no mutation; (e) `up_rc=1` -> exit 1, stderr contains `stage: up` and the recovery command `up -d --no-deps remote-setup`, and no second command is attempted.

```python
class MigrationWorld(World):
    """World whose target is remote-setup (native dependency on OmniRoute) instead of an OmniRoute update."""
    def __init__(self, tmp_path, *, compose_has_edge=False, already_free=False, up_rc=0, drift_after_projection=False, **kw):
        super().__init__(tmp_path, dependent=True, up_rc=up_rc, **kw)
        compose = {"services": {"remote-setup": ({"depends_on": {"omniroute": {"condition": "service_healthy"}}} if compose_has_edge else {"image": "x"})}}
        (self.home / ".config/llm-env/docker-compose.yml").write_text(yaml.safe_dump(compose))
        world = json.loads(self.worldfile.read_text())
        rs = inspect_doc(RS, name="remote-setup", deps=[] if already_free else [OMNI])
        rs["Mounts"] = [{"Type": "volume", "Name": f"{PROJECT}_remote-setup-data"}]
        new_rs = json.loads(json.dumps(rs)); new_rs.update(Id=NEW, Dependencies=[])
        eff = {"services": {"remote-setup": {"image": "x", "container_name": "remote-setup", "volumes": ["remote-setup-data:/app/data"],
               "environment": {}}}, "networks": {"default": {}}, "volumes": {"remote-setup-data": {}}}
        def ps(ids): return json.dumps([{"Id": i} for i in ids])
        echo = {"tool": "podman-compose", "last": "config", "arg_endswith": "projection.yml", "echo_file_after": "-f"}
        if drift_after_projection:
            echo["set_phase"] = "post-observer"            # flips right after the projection round trip, before the worker's recheck
        ps_rules = [
            {"tool": "podman", "prefix": ["ps"], "phase": ["start"], "stdout": ps([OMNI, RS])},
            {"tool": "podman", "prefix": ["ps"], "phase": ["post-observer"], "stdout": ps([OMNI, RS, SIB] if drift_after_projection else [OMNI, RS])},
            {"tool": "podman", "prefix": ["ps"], "phase": ["post-rm"], "stdout": ps([OMNI])},
            {"tool": "podman", "prefix": ["ps"], "phase": ["post-commit"], "stdout": ps([OMNI, NEW])},
            {"tool": "podman", "prefix": ["container", "inspect"], "last": RS, "stdout": json.dumps([rs])},
            {"tool": "podman", "prefix": ["container", "inspect"], "last": NEW, "stdout": json.dumps([new_rs])},
            {"tool": "podman", "prefix": ["container", "inspect"], "last": SIB, "stdout": json.dumps([inspect_doc(SIB, name="sib")])},
            echo,
            {"tool": "podman-compose", "last": "config", "stdout": yaml.safe_dump(eff)},
        ]
        kept = [r for r in world["rules"] if not (r.get("prefix") == ["ps"] or r.get("last") in (RS, NEW) or r.get("last") == "config")]
        world["rules"] = ps_rules + kept
        self.worldfile.write_text(json.dumps(world))

    def run_migration(self, **env):
        return subprocess.run(["bash", str(self.repo / "scripts/migrate-remote-setup.sh")], env={**self.env(), **env},
                              capture_output=True, text=True, timeout=180)
```

  Import `SIB` from `tests/omniroute_fakes.py` in that module. Because `World.__init__` already launches the fake OmniRoute server and writes the repo copy, `MigrationWorld.run_migration()` is the only addition needed to run the migration script. (`kept` drops the base world's `["ps"]`, `RS`/`NEW` inspect and `config` rules, but keeps `["ps","--all","--filter"]`, which returns no container and so satisfies the name-free check.)
- [ ] **Step 2: Run, verify FAIL.**
- [ ] **Step 3: Implement.**
  - `compose.py`: delete the `"depends_on": {"omniroute": {"condition": "service_healthy"}}` entry of the remote-setup service (line ~226) only.
  - `remote_setup.py`: add (`threading` is already imported) `CONFIG_DEADLINE_SECONDS = 20.0`, a module-level `_config_flight = threading.Lock()`, the response helper beside `_write_json` (it must go through `send_response`, which records `_last_status` for `log_message`), and replace the two backend calls in `_handle_config` (lines 631-640) with the single-flight worker:

```python
    def _write_unavailable(self, message: str) -> None:
        body = json.dumps({"error": message}).encode("utf-8")
        self.send_response(503)
        self.send_header("Content-Type", "application/json")
        self.send_header("Retry-After", "5")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
```

```python
def _fetch_config_inputs(base_url: str, password: str, box: dict[str, Any]) -> None:
    try:
        try:
            box["api_key"] = ensure_api_key(base_url, password, CACHE_PATH)
        except OmniRouteError as exc:
            box["error"] = f"could not reach OmniRoute: {exc}"
            return
        try:
            box["combos"] = compute_combo_context(base_url, password)
        except OmniRouteError as exc:
            box["error"] = f"could not fetch OmniRoute combos: {exc}"
    except Exception as exc:                  # surfaced to the request thread, which re-raises it as the existing JSON 500
        box["crash"] = exc
    finally:
        _config_flight.release()              # a late worker never overlaps the next key creation
```

```python
        if not _config_flight.acquire(blocking=False):
            self._write_unavailable("OmniRoute configuration is being prepared; retry shortly")
            return
        box: dict[str, Any] = {}
        worker = threading.Thread(target=_fetch_config_inputs, args=(base_url, dashboard_password, box), daemon=True)
        try:
            worker.start()
        except RuntimeError:
            _config_flight.release()
            self._write_unavailable("OmniRoute configuration worker could not start; retry shortly")
            return
        worker.join(CONFIG_DEADLINE_SECONDS)
        if worker.is_alive():
            self._write_unavailable("OmniRoute did not answer in time; retry shortly")
            return
        if "crash" in box:
            raise box["crash"]
        if "error" in box:
            self._write_unavailable(box["error"])
            return
        api_key, combos = box["api_key"], box["combos"]
```

    Per-socket operations remain bounded by the existing 10 s timeouts; `ensure_api_key`'s own cache means a late completion is reused, not replayed. Update the `log_message` docstring ("OmniRoute outages (502)" -> "(503)"). The only existing test that asserts 502 is the one at line 783 (updated above).
  - `omniroute_update.py` / CLI: `preflight(..., service=...)` and `preflight --service` are already specified and tested in Task 2; nothing further here.
  - `scripts/migrate-remote-setup.sh` (full text; the `--worker` branch runs first when re-executed):

```bash
#!/usr/bin/env bash
# One-time, remote-setup-only recreation that drops its native dependency on OmniRoute.
# Everything before the detached worker is read-only. Not transactional: no rollback is attempted.
set -euo pipefail
# shellcheck disable=SC1091 # Resolved from this script at runtime.
# shellcheck source=../tools/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/../tools/lib.sh"
require_cmd podman jq yq uv flock setsid timeout
config_path="$CONFIG_PATH"
compose_path="$COMPOSE_FILE"
unset COMPOSE_FILE COMPOSE_INSPECT_DIR
llm=(uv run --project "$REPO_DIR" "$REPO_DIR/llmenv.py" omniroute-update)
backend_env_init
cli() { "${llm[@]}" "$@" 9>&-; }
pm() { "${backend_env[@]}" podman "$@" 9>&-; }
health_timeout="${LLM_ENV_HEALTH_TIMEOUT_SECONDS:-60}"
[[ "$health_timeout" =~ ^[1-9][0-9]*$ ]] || { echo "error: invalid LLM_ENV_HEALTH_TIMEOUT_SECONDS" >&2; exit 1; }

read_deploy() {   # sets container_id image_id project stop_seconds provider from $work/deploy.env
    container_id="" image_id="" project="" stop_seconds="" provider=""
    while IFS== read -r k v; do
        case "$k" in container_id) container_id="$v" ;; image_id) image_id="$v" ;; project) project="$v" ;; stop_seconds) stop_seconds="$v" ;; provider) provider="$v" ;; *) ;; esac
    done <"$work/deploy.env"
}

if [[ "${1:-}" == "--worker" ]]; then
    work="$2" config_path="$3" compose_path="$4"
    trap 'rm -rf "$work"' EXIT
    read_deploy
    fail() {
        printf 'error: migration failed at stage: %s\n' "$1" >&2
        printf 'old container: %s\n' "${container_id:0:12}" >&2
        printf 'No rollback was attempted. If remote-setup is gone, recreate it plainly with:\n  podman-compose -p %s -f "%s" up -d --no-deps remote-setup\n' "$project" "$compose_path" >&2
        exit 1
    }
    rc=0
    cli preflight --recheck --budget 60 --config "$config_path" --compose "$compose_path" --out-dir "$work" >"$work/recheck.out" || rc=$?
    if (( rc != 0 )); then cat "$work/recheck.out"; exit "$rc"; fi
    pm stop --time "$stop_seconds" "$container_id" || fail "stop"
    pm rm "$container_id" || fail "rm"                              # by ID only
    if [[ -n "$(pm ps --all --filter name='^remote-setup$' --format '{{.ID}}')" ]]; then fail "name still in use after removal"; fi
    "${backend_env[@]}" "$provider" --in-pod false --env-file /dev/null -p "$project" -f "$work/projection.yml" \
        up -d --no-deps --no-recreate --no-build --pull never remote-setup 9>&- || fail "up"
    cli verify-commit --dir "$work" --service remote-setup --old-id "$container_id" --expect-no-deps \
        --timeout "$health_timeout" >"$work/verify.out" || fail "verify-commit"
    echo "remote-setup recreated without its native dependency"
    exit 0
fi

# 1. The deployed compose must already be dependency-free; this script never rewrites it.
yq -e '.services["remote-setup"] | has("depends_on") | not' "$compose_path" >/dev/null 2>&1 || {
    echo "error: re-render the deployed compose first (README 'Operations'); the migration never rewrites it" >&2
    exit 1
}
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
# 2. Lock + read-only preflight of the remote-setup service.
acquire_update_lock
rc=0
cli preflight --service remote-setup --config "$config_path" --compose "$compose_path" --out-dir "$work" >"$work/deploy.env" || rc=$?
if (( rc != 0 )); then cat "$work/deploy.env"; exit "$rc"; fi
read_deploy
[[ "$container_id" =~ ^[0-9a-f]{64}$ && "$image_id" =~ ^sha256:[0-9a-f]{64}$ && "$stop_seconds" =~ ^[0-9]+$ ]] || { echo "error: malformed preflight output" >&2; exit 1; }
# 3. No-op when the running container already has no native dependency.
if jq -e '.[0].Dependencies | length == 0' "$work/target.json" >/dev/null; then
    echo "remote-setup already has no native dependency; nothing changed"
    exit 0
fi
# 4. Prove the data volume and build the projection (same image id, so no pull).
jq -e --arg v "${project}_remote-setup-data" '.volume_names | index($v)' "$work/deployment.json" >/dev/null || {
    echo "error: ${project}_remote-setup-data is not mounted on the running remote-setup; refusing" >&2
    exit 1
}
cli projection --dir "$work" --service remote-setup --image "${image_id#sha256:}" --out "$work/projection.yml"
# 5. Detached bounded worker; it inherits FD 9 (the lock) and keeps it until it exits.
budget=$(( stop_seconds + 3 * health_timeout + 120 ))
rc=0
setsid --wait timeout --kill-after=10 "$budget" bash "${BASH_SOURCE[0]}" --worker "$work" "$config_path" "$compose_path" || rc=$?
if (( rc == 124 || rc == 137 )); then
    echo "error: the migration worker exceeded its time budget; inspect remote-setup manually (no rollback was attempted)" >&2
    exit 1
fi
exit "$rc"
```

    Add the `Makefile` target (`migrate-remote-setup:` with the one-line body `@bash tools/run-target.sh migrate-remote-setup -- bash scripts/migrate-remote-setup.sh`, matching the `update-omniroute` target at line 96, plus its `.PHONY` entry) and a `scripts/help.sh` line.
- [ ] **Step 4: Run, verify PASS.** `make validate && make test`.
- [ ] **Step 5: Disposable proof** is part of Task 8 (same separate backend). The **production** recreation is a separate user-scheduled step and is **not** executed by this plan.
- [ ] **Step 6: Commit if approved.**

---

### Task 8: Isolated end-to-end proof, characterization, resource note and documentation

**Files:**
- Create: `tests/proof_backend.py`, `tests/fixtures/proof/Containerfile`, `tests/fixtures/fake_inference.py`, `tests/test_omniroute_update_proof.py` (skipped unless `LLM_ENV_UPDATE_PROOF=1`), `tests/test_omniroute_characterize.py` (skipped unless `LLM_ENV_UPDATE_CHARACTERIZE=1`)
- Modify: `README.md:69`, `QUICK_START.md:61`, `scripts/help.sh:38`, `AGENTS.md`, `Makefile` comment

(`tests/fixtures/fake_omniroute.py` already exists from Task 3, with the `--host` option and startup log line added there; it is the HTTP surface of the proof image.)

**Isolation design (enforceable):** the production target name `omniroute` is fixed by `pylib/compose.py`, so a compose project name alone cannot isolate the proof. The proof therefore runs in a **separate rootless Podman backend** addressed *only* through three environment variables that the updater and migration scripts preserve for every Podman/provider call (Global Constraints; Task 6 case 12 proves it with stubs): `CONTAINERS_STORAGE_CONF` (own `graphroot`/`runroot` with the `vfs` driver, which needs neither kernel overlay nor fuse-overlayfs and is acceptable for these small disposable images), `CONTAINERS_CONF` (journald log driver so the observer's journal feed works; `[network] network_config_dir` under the temp dir so no host network definition is touched) and `CONTAINERS_REGISTRIES_CONF` (marks `127.0.0.1:<registry port>` insecure for skopeo and Podman). The temp root is created under the repository's `./tmp/` (already ignored), not host `/tmp`. The updater runs with `HOME` = a temp home that holds `.config/llm-env/{models.yml,docker-compose.yml}`; `UV_CACHE_DIR` stays the real cache.

**Ownership guard (two phases, so fixtures can exist):** `ProofBackend.assert_owned()` runs before **every** proof-owned `podman` invocation and every cleanup command and asserts only that `podman info` (under the proof environment) reports `graphRoot`/`runRoot` whose real paths are under the temp root (never that the backend is empty). `ProofBackend.assert_pristine()` additionally asserts `podman ps --all` and `podman image list` are empty and runs **once**, at construction, before anything is imported. Cleanup is `assert_owned()`, then `podman rm --force --all --time 1`, then `podman unshare rm -rf <temp root>`; it never uses `system reset`, `system prune` or any host-wide command.

- [ ] **Step 1: Fixture build (opt-in, fully specified).**

  Host prerequisites (the test skips with the exact hint when absent): `podman`, `podman-compose`, `skopeo`, a readable user journal, and these two images in the **host** image store (`podman pull docker.io/library/registry:2 docker.io/library/python:3.12-slim`). They are copied into the proof backend with `podman save | podman load`; nothing else is fetched from the network.

  `tests/fixtures/proof/Containerfile` — the one image definition, built twice with a different `VARIANT`, which changes the layer content and the build history and therefore yields **distinct config ids** (the test asserts `old != new`):

```dockerfile
FROM docker.io/library/python:3.12-slim
ARG VARIANT
ENV NODE_ENV=production
RUN mkdir -p /app && echo "$VARIANT" > /app/VARIANT && echo '{"cache_ms": 1000}' > /app/state.json
COPY fake_omniroute.py /app/fake_omniroute.py
ENTRYPOINT ["python3", "/app/fake_omniroute.py"]
```

  The server binds `--host 0.0.0.0` (the compose `command` below), so the published port is reachable from the host (the earlier loopback-only bind is not). `tests/proof_backend.py`:

```python
"""Disposable rootless Podman backend for the opt-in updater proofs. Every call is ownership-checked."""
from __future__ import annotations

import json, os, pathlib, shutil, subprocess, sys, tempfile, time, urllib.request

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from omniroute_fakes import free_port

REGISTRY_IMAGE = "docker.io/library/registry:2"
BASE_IMAGE = "docker.io/library/python:3.12-slim"
PROJECT = "proofproj"


class ProofSkip(Exception):
    """A host prerequisite is missing; the message is the exact command that fixes it."""


class ProofBackend:
    def __init__(self) -> None:
        (REPO / "tmp").mkdir(exist_ok=True)
        self.root = pathlib.Path(tempfile.mkdtemp(prefix="proof-", dir=REPO / "tmp")).resolve()
        self.reg_port = free_port()
        self.ref = f"127.0.0.1:{self.reg_port}/proof/omniroute:latest"          # the configured, mutable reference
        for name in ("graph", "run", "netconf", "shim"):
            (self.root / name).mkdir()
        (self.root / "storage.conf").write_text(
            f'[storage]\ndriver = "vfs"\ngraphroot = "{self.root}/graph"\nrunroot = "{self.root}/run"\n')
        (self.root / "containers.conf").write_text(
            f'[containers]\nlog_driver = "journald"\n[network]\nnetwork_config_dir = "{self.root}/netconf"\n')
        (self.root / "registries.conf").write_text(
            f'[[registry]]\nlocation = "127.0.0.1:{self.reg_port}"\ninsecure = true\n')
        self.argv_log = self.root / "argv.log"

    # --- environment and guards -------------------------------------------------
    def env(self) -> dict[str, str]:
        return {"CONTAINERS_STORAGE_CONF": str(self.root / "storage.conf"),
                "CONTAINERS_CONF": str(self.root / "containers.conf"),
                "CONTAINERS_REGISTRIES_CONF": str(self.root / "registries.conf")}

    def _run(self, argv, **kw) -> subprocess.CompletedProcess:
        return subprocess.run(argv, env={**os.environ, **self.env()}, text=True, capture_output=True, **kw)

    def assert_owned(self) -> None:
        info = json.loads(self._run(["podman", "info", "--format", "json"], check=True).stdout)
        for key in ("graphRoot", "runRoot"):
            assert os.path.realpath(info["store"][key]).startswith(str(self.root) + os.sep), "refusing: not the proof backend"

    def assert_pristine(self) -> None:
        self.assert_owned()
        assert self.podman("ps", "--all", "--quiet").stdout.strip() == ""
        assert self.podman("image", "list", "--quiet").stdout.strip() == ""

    def podman(self, *args: str, check: bool = True, **kw) -> subprocess.CompletedProcess:
        self.assert_owned()
        return self._run(["podman", *args], check=check, **kw)

    # --- fixtures ---------------------------------------------------------------
    def import_host_image(self, ref: str) -> None:
        if subprocess.run(["podman", "image", "exists", ref]).returncode != 0:
            raise ProofSkip(f"host image store lacks {ref}; run: podman pull {ref}")
        self.assert_owned()
        save = subprocess.Popen(["podman", "save", ref], stdout=subprocess.PIPE)       # host store: no proof env
        subprocess.run(["podman", "load"], stdin=save.stdout, env={**os.environ, **self.env()}, check=True, capture_output=True)
        assert save.wait() == 0

    def start_registry(self) -> None:
        self.podman("run", "-d", "--name", "proof-registry", "-p", f"127.0.0.1:{self.reg_port}:5000", REGISTRY_IMAGE)
        end = time.monotonic() + 30
        while time.monotonic() < end:
            try:
                if urllib.request.urlopen(f"http://127.0.0.1:{self.reg_port}/v2/", timeout=2).status == 200:
                    return
            except OSError:
                time.sleep(0.5)
        raise AssertionError("proof registry did not start")

    def build(self, variant: str) -> str:
        """Build the fixture image; returns its config id (`sha256:<hex>`)."""
        ctx = self.root / f"ctx-{variant}"
        ctx.mkdir()
        shutil.copy(REPO / "tests/fixtures/proof/Containerfile", ctx / "Containerfile")
        shutil.copy(REPO / "tests/fixtures/fake_omniroute.py", ctx / "fake_omniroute.py")
        self.podman("build", "--format", "oci", "--build-arg", f"VARIANT={variant}", "-t", f"localhost/proof/omniroute:{variant}", str(ctx))
        return "sha256:" + self.podman("image", "inspect", "--format", "{{.Id}}", f"localhost/proof/omniroute:{variant}").stdout.strip()

    def publish(self, variant: str) -> None:
        """Move the registry tag `latest` to `variant` (the agreed mutable-tag transition). `podman push SRC DEST`
        does not retag locally, so the proof backend's own `self.ref` tag only moves when the updater moves it."""
        self.podman("push", "--format", "oci", "--tls-verify=false", f"localhost/proof/omniroute:{variant}", f"docker://{self.ref}")

    def deploy(self, compose_path: pathlib.Path) -> None:
        self.assert_owned()
        subprocess.run([shutil.which("podman-compose") or "podman-compose", "--in-pod", "false", "--env-file", "/dev/null",
                        "-p", PROJECT, "-f", str(compose_path), "up", "-d"],
                       env={**os.environ, **self.env()}, check=True, capture_output=True, text=True, timeout=300)
        for name in self.project_containers():
            self.wait_healthy(name)

    def project_containers(self) -> list[str]:
        out = self.podman("ps", "--all", "--filter", f"label=io.podman.compose.project={PROJECT}", "--format", "{{.Names}}").stdout
        return sorted(out.split())

    def wait_healthy(self, name: str, timeout: int = 90) -> None:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if self.podman("inspect", name, "--format", "{{.State.Health.Status}}").stdout.strip() == "healthy":
                return
            time.sleep(1)
        raise AssertionError(f"{name} did not become healthy")

    def image_of(self, name: str) -> str:
        return "sha256:" + self.podman("inspect", name, "--format", "{{.Image}}").stdout.strip()

    def snapshot(self) -> dict:
        """Everything the updater must leave alone: ids and StartedAt per container, volume names, network ids."""
        containers = {}
        for item in json.loads(self.podman("ps", "--all", "--format", "json").stdout):
            containers[item["Names"][0]] = (item["Id"], item["StartedAt"])
        return {"containers": containers,
                "volumes": sorted(self.podman("volume", "ls", "--format", "{{.Name}}").stdout.split()),
                "networks": sorted(self.podman("network", "ls", "--format", "{{.ID}}").stdout.split())}

    def reset_deployment(self) -> None:
        """Between tests: remove the project's containers, volumes and network; the registry stays."""
        for name in self.project_containers():
            self.podman("rm", "--force", "--time", "1", name)
        for volume in self.podman("volume", "ls", "--filter", f"label=io.podman.compose.project={PROJECT}", "--format", "{{.Name}}").stdout.split():
            self.podman("volume", "rm", "--force", volume)
        self.podman("network", "rm", "--force", f"{PROJECT}_default", check=False)

    # --- argv recording (PATH shims) -------------------------------------------
    def install_shims(self) -> pathlib.Path:
        shim = self.root / "shim"
        for tool in ("podman", "podman-compose"):
            real = shutil.which(tool)
            (shim / tool).write_text(
                f"#!/usr/bin/env bash\nprintf '%s\\037' \"${{0##*/}}\" \"$@\" >> '{self.argv_log}'\nprintf '\\n' >> '{self.argv_log}'\nexec '{real}' \"$@\"\n")
            (shim / tool).chmod(0o755)
        return shim

    def recorded_argv(self) -> list[list[str]]:
        if not self.argv_log.exists():
            return []
        return [line.split("\x1f")[:-1] for line in self.argv_log.read_text().splitlines() if line]

    def clear_argv_log(self) -> None:
        self.argv_log.write_text("")

    def close(self) -> None:
        try:
            self.assert_owned()
            self._run(["podman", "rm", "--force", "--all", "--time", "1"])
            self._run(["podman", "unshare", "rm", "-rf", str(self.root)])
        finally:
            shutil.rmtree(self.root, ignore_errors=True)
```

- [ ] **Step 2: Proof tests (exact content and commands).** `tests/test_omniroute_update_proof.py` runs the real `scripts/update-omniroute.sh` and `scripts/migrate-remote-setup.sh` from a **temp copy of the repo tree** (`make_repo_copy` from Task 6) whose profile list holds one mechanics-only entry for the `:old` fixture config id (`profile_entry(config_id=<old>, entrypoint=["python3", "/app/fake_omniroute.py"], env_allowlist={"NODE_ENV": "production"})`; the fixture's `logger.fields` already name `method`/`path`, which is what `fake_omniroute.py` prints). It uses the backend's PATH shims (`podman`, `podman-compose` recording every argv, then `exec`ing the real binary).

```python
import os, re, subprocess, sys
from pathlib import Path
import pytest, yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from omniroute_fakes import PASSWORD, free_port, profile_entry
from omniroute_world import make_repo_copy
from proof_backend import BASE_IMAGE, REGISTRY_IMAGE, ProofBackend, ProofSkip

pytestmark = pytest.mark.skipif(os.environ.get("LLM_ENV_UPDATE_PROOF") != "1",
                                reason="opt-in: LLM_ENV_UPDATE_PROOF=1 (needs podman, podman-compose, skopeo, journald)")


@pytest.fixture(scope="module")
def backend():
    b = ProofBackend()
    try:
        b.assert_pristine()
        b.import_host_image(BASE_IMAGE)
        b.import_host_image(REGISTRY_IMAGE)
        b.start_registry()
        b.old_id, b.new_id = b.build("old"), b.build("new")
        assert b.old_id != b.new_id
        b.shim = b.install_shims()
    except ProofSkip as exc:
        b.close()
        pytest.skip(str(exc))
    except BaseException:
        b.close()
        raise
    yield b
    b.close()


def fixture_service(backend, name, port, *, depends_on=False, volume=None):
    svc = {"image": backend.ref, "container_name": name,
           "command": ["--host", "0.0.0.0", "--port", str(port), "--state", "/app/state.json"],
           "ports": [f"0.0.0.0:{port}:{port}"], "stop_grace_period": "10s",
           "environment": {"PORT": str(port), "INITIAL_PASSWORD": PASSWORD, "REQUIRE_API_KEY": "true", "NODE_ENV": "production"},
           "healthcheck": {"test": ["CMD", "python3", "-c", f"import urllib.request; urllib.request.urlopen('http://127.0.0.1:{port}/api/monitoring/health', timeout=2)"],
                           "interval": "2s", "timeout": "3s", "retries": 15}}
    if volume:
        svc["volumes"] = [f"{volume}:/app/data"]
    if depends_on:
        svc["depends_on"] = {"omniroute": {"condition": "service_healthy"}}
    return svc


def write_home(tmp_path, backend, port, services, volumes):
    cfg = tmp_path / "home/.config/llm-env"
    cfg.mkdir(parents=True)
    (cfg / "models.yml").write_text(yaml.safe_dump({"version": 1, "omniroute": {"port": port, "image": backend.ref, "initial_password": PASSWORD}}))
    (cfg / "models.yml").chmod(0o600)
    compose = cfg / "docker-compose.yml"
    compose.write_text(yaml.safe_dump({"services": services, "volumes": {v: {} for v in volumes}}))
    return tmp_path / "home", compose


def script_env(backend, home):
    base = {k: v for k, v in os.environ.items() if not k.startswith(("COMPOSE_", "PODMAN_COMPOSE_", "CONTAINER_"))}
    return {**base, **backend.env(), "HOME": str(home), "PATH": f"{backend.shim}:{base['PATH']}",
            "UV_PROJECT_ENVIRONMENT": str(REPO / ".venv"), "UV_CACHE_DIR": os.environ.get("UV_CACHE_DIR", str(Path.home() / ".cache/uv")),
            "LLM_ENV_UPDATE_IDLE_SECONDS": "10", "LLM_ENV_UPDATE_MAX_WAIT_SECONDS": "180"}


def run_script(backend, tmp_path, home, script):
    repo = make_repo_copy(tmp_path, [profile_entry(config_id=backend.old_id, entrypoint=["python3", "/app/fake_omniroute.py"],
                                                    env_allowlist={"NODE_ENV": "production"})])
    backend.clear_argv_log()
    return subprocess.run(["bash", str(repo / "scripts" / script)], env=script_env(backend, home), capture_output=True, text=True, timeout=600)


FORBIDDEN = {"--force", "--depend", "--remove-orphans", "--volumes", "-v", "down"}


def test_update_replaces_only_omniroute_and_is_idempotent(backend, tmp_path):
    backend.reset_deployment(); backend.publish("old")
    port = free_port()
    home, compose = write_home(tmp_path, backend, port, {"omniroute": fixture_service(backend, "omniroute", port, volume="omniroute-data")}, ["omniroute-data"])
    backend.deploy(compose)
    assert backend.image_of("omniroute") == backend.old_id, "push/pull changed the config id; the fixture must be built and pushed as OCI"
    before = backend.snapshot()
    backend.publish("new")                                              # the mutable tag now resolves to a different config id
    result = run_script(backend, tmp_path, home, "update-omniroute.sh")
    assert result.returncode == 0, result.stdout + result.stderr
    assert re.fullmatch(r"updated [0-9a-f]{12} -> [0-9a-f]{12}\n", result.stdout)
    after = backend.snapshot()
    assert backend.image_of("omniroute") == backend.new_id
    assert after["containers"]["omniroute"] != before["containers"]["omniroute"]
    assert {k: v for k, v in after["containers"].items() if k != "omniroute"} == {k: v for k, v in before["containers"].items() if k != "omniroute"}
    assert after["volumes"] == before["volumes"] and after["networks"] == before["networks"]
    calls = backend.recorded_argv()
    assert not any(FORBIDDEN & set(call) for call in calls)
    again = run_script(backend, tmp_path, home, "update-omniroute.sh")        # a second run finds nothing to do
    assert again.returncode == 0 and again.stdout == "unchanged\n"


def test_dependent_container_defers_and_nothing_changes(backend, tmp_path):
    backend.reset_deployment(); backend.publish("old")
    port, port2 = free_port(), free_port()
    services = {"omniroute": fixture_service(backend, "omniroute", port, volume="omniroute-data"),
                "dependent": fixture_service(backend, "dependent", port2, depends_on=True)}
    home, compose = write_home(tmp_path, backend, port, services, ["omniroute-data"])
    backend.deploy(compose)
    before = backend.snapshot()
    backend.publish("new")
    result = run_script(backend, tmp_path, home, "update-omniroute.sh")
    assert result.returncode == 75 and result.stdout == "deferred: ownership\n", result.stdout + result.stderr
    assert backend.snapshot() == before
    assert backend.image_of("omniroute") == backend.old_id
    assert backend.podman("image", "inspect", backend.ref, "--format", "{{.Id}}").stdout.strip() == backend.old_id.removeprefix("sha256:")
    assert not any(call[1:2] in (["pull"], ["tag"], ["stop"], ["rm"]) for call in backend.recorded_argv())


def test_migration_clears_only_the_remote_setup_dependency(backend, tmp_path):
    backend.reset_deployment(); backend.publish("old")
    port, port2 = free_port(), free_port()
    with_edge = {"omniroute": fixture_service(backend, "omniroute", port, volume="omniroute-data"),
                 "remote-setup": fixture_service(backend, "remote-setup", port2, depends_on=True, volume="remote-setup-data")}
    home, compose = write_home(tmp_path, backend, port, with_edge, ["omniroute-data", "remote-setup-data"])
    backend.deploy(compose)
    assert backend.podman("inspect", "remote-setup", "--format", "{{json .Dependencies}}").stdout.strip() != "[]"
    before = backend.snapshot()
    deployed = {"omniroute": with_edge["omniroute"], "remote-setup": {k: v for k, v in with_edge["remote-setup"].items() if k != "depends_on"}}
    compose.write_text(yaml.safe_dump({"services": deployed, "volumes": {"omniroute-data": {}, "remote-setup-data": {}}}))   # the "re-rendered" file
    result = run_script(backend, tmp_path, home, "migrate-remote-setup.sh")
    assert result.returncode == 0, result.stdout + result.stderr
    after = backend.snapshot()
    assert backend.podman("inspect", "remote-setup", "--format", "{{json .Dependencies}}").stdout.strip() == "[]"
    assert after["containers"]["omniroute"] == before["containers"]["omniroute"]
    assert after["containers"]["remote-setup"] != before["containers"]["remote-setup"]
    assert after["volumes"] == before["volumes"] and after["networks"] == before["networks"]
    assert not any(FORBIDDEN & set(call) for call in backend.recorded_argv())
```

  Exact commands (run once each; the first builds both images and takes a few minutes with the `vfs` driver):

```bash
podman pull docker.io/library/registry:2 docker.io/library/python:3.12-slim   # host prerequisites, once
LLM_ENV_UPDATE_PROOF=1 uv run pytest tests/test_omniroute_update_proof.py -v -s
```

  **These runs prove replacement mechanics only; they are not exact-OmniRoute activity qualification, which comes solely from Step 3.** Record versions (`podman --version`, `podman-compose --version`, `skopeo --version`), the 12-char old/new config ids and pass/fail in `docs/superpowers/research/2026-10-22-updater-proof.md`.
- [ ] **Step 3: Disposable characterization (opt-in `LLM_ENV_UPDATE_CHARACTERIZE=1`, `tests/test_omniroute_characterize.py`) — a bounded manual-review step.** It needs `ProofBackend` (Step 1), the fake inference provider below and the **unmodified** OmniRoute image under test (`docker.io/diegosouzapw/omniroute:latest`, imported from the host store; skip with `podman pull` hint when absent). What can be specified exactly is the harness; what cannot be specified before running it is its **output** — the real access-log field names, admin JSON pointers and cache/refresh bounds are measurements, so they are produced by deterministic derivation rules below rather than written into the plan, and anything the rules cannot derive is omitted (the profile then defers `capability`). The step never edits the profile file; a human reviews the evidence and adds the entry in a separate commit.

  - **Deployment:** `render_compose` with `from test_compose import CFG` (override `llm_server.enabled=False`, `omniroute.{port: P, image: <the image>, initial_password: PASSWORD}`), keep only `services.omniroute` and `volumes.omniroute-data` from the YAML, write it to a temp `docker-compose.yml`, `ProofBackend.deploy` it (the real `healthcheck.mjs` healthcheck applies) and record the running config id.
  - **Fake inference provider:** `tests/fixtures/fake_inference.py --host 0.0.0.0 --port Q --state FILE`, started on the **host**. The container reaches it at `http://host.containers.internal:Q/v1` (rootless Podman maps that name to the host; the harness first runs `podman run --rm <python image> python3 -c "import socket; socket.gethostbyname('host.containers.internal')"` and fails fast with that diagnostic if it does not resolve). `compose.py` already sets `OMNIROUTE_ALLOW_PRIVATE_PROVIDER_URLS=true`, which this needs.

```python
#!/usr/bin/env python3
"""OpenAI-compatible stub for characterization: GET /v1/models, POST /v1/chat/completions (stream or not). No inference."""
import argparse, json, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--state", required=True)          # JSON re-read per request: {"chunks": 3, "delay": 0.05, "mode": "ok"|"fail"}
    args = ap.parse_args()

    def state() -> dict:
        try:
            return json.load(open(args.state))
        except (OSError, ValueError):
            return {}

    class H(BaseHTTPRequestHandler):                   # HTTP/1.0: the stream is delimited by closing the connection
        def _json(self, code, body):
            data = json.dumps(body).encode()
            self.send_response(code); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)

        def do_GET(self):
            if self.path.split("?")[0] == "/v1/models":
                return self._json(200, {"object": "list", "data": [{"id": "fake-model", "object": "model", "owned_by": "fixture", "context_length": 8192}]})
            self._json(404, {"error": "not found"})

        def do_POST(self):
            req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            s = state()
            if s.get("mode") == "fail":
                return self._json(500, {"error": {"message": "fixture failure"}})
            chunks, delay = int(s.get("chunks", 3)), float(s.get("delay", 0.05))
            if not req.get("stream"):
                time.sleep(chunks * delay)
                return self._json(200, {"id": "c", "object": "chat.completion", "choices": [{"index": 0, "message": {"role": "assistant", "content": "x" * chunks}, "finish_reason": "stop"}]})
            self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.end_headers()
            try:
                for _ in range(chunks):
                    self.wfile.write(("data: " + json.dumps({"id": "c", "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": {"content": "x"}}]}) + "\n\n").encode())
                    self.wfile.flush(); time.sleep(delay)
                self.wfile.write(b"data: [DONE]\n\n")
            except OSError:
                pass

        def log_message(self, *a):
            pass

    ThreadingHTTPServer((args.host, args.port), H).serve_forever()


if __name__ == "__main__":
    main()
```

  - **Wiring (existing helpers only):** `token = pylib.omniroute._login(base, PASSWORD)`; `payload = pylib.omniroute.build_payload(Q, "fixture-key")` with `payload["providerSpecificData"]["baseUrl"] = f"http://host.containers.internal:{Q}/v1"`; `pylib.omniroute._request("POST", f"{base}/api/providers", token, payload)`; the client key is `pylib.remote_setup.ensure_api_key(base, PASSWORD, tmp / "key.json")`. **Precondition gate:** `GET /v1/models` with that key lists a `llama-cpp/…` model and one non-streaming chat completion returns 200; otherwise the step fails with that diagnostic and writes nothing.
  - **Sampler:** every 0.5 s, with the dashboard session, record the raw JSON of `/api/monitoring/health`, `/api/usage/history` and `/api/usage/call-logs?limit=1000&offset=0` (counts and structure only; request prompts are never sent beyond the one-character stub payload), and in parallel capture `journalctl --output=json --no-pager --follow CONTAINER_ID_FULL=<full id>` lines.
  - **Scenarios** (each sets the stub's state file, drives the client with `http.client` against `http://127.0.0.1:P/v1/chat/completions`, then idles 5 s): `long_stream` (20 chunks, 0.5 s delay, stream fully consumed); `short_between_polls` (non-streaming, completes in about 0.15 s, fired just after a sampler tick); `upstream_failure` (`mode: fail`); `queued_request` (8 concurrent slow requests so some wait in admission); `boundary_arrival` (a request fired within 50 ms of a sampler tick); `burst` (200 requests in about 2 s, then compare request count with captured access-log line count to detect journald rate-limit suppression); last and destructive, `native_shutdown_drain` (`podman stop --time 40 omniroute` while a `long_stream` is open; record whether the stream completes or aborts and the stop duration).
  - **Evidence file:** `docs/superpowers/research/<date>-omniroute-characterization-<config12>.json` (config id, `podman`/`podman-compose`/`journalctl` versions, per-scenario sampler series and journal lines, containing no keys or cookies) plus its own SHA256 in `...<config12>.sha256`.
  - **`derive_candidate(evidence) -> dict` (deterministic, in the test module).** `api_fields`: for each required field, the JSON pointer whose value is `0` in the idle baseline and `>= 1` during the matching scenario (`active`/`pending_by_model` during `long_stream`, an `admission` pointer during `queued_request`, `rows` from the call-logs body) and that is unique among candidates; `health_timestamp` is the pointer whose value changes between sampler ticks. `logger.fields`: the keys of the parsed access-log `MESSAGE` JSON whose values equal the driven request's method and path. `routes`/`reachable_routes`: the distinct (method, path) pairs seen in access-log lines across all scenarios; the candidate carries `"unobserved_routes_unreviewed": true` because traffic cannot enumerate routes the harness never called, so the reviewer must add any other inference route (embeddings, responses, ...) — at runtime the loader's coverage rule already defers any unprofiled HTTP route. `health_cache_ms` = the longest dwell of an unchanged health timestamp across ticks; `health_refresh_bound_ms` = the largest step between consecutive distinct timestamps minus that dwell, rounded up to 100 ms. `logger.loss_detection` is emitted only if `burst` shows captured lines == requests; otherwise `logger` is omitted. Every pointer or field that fails its rule is listed in `candidate["incomplete"]` and omitted, so `load_profile` would defer `capability`. The candidate is printed and written to `docs/superpowers/research/<date>-omniroute-candidate-<config12>.json`; **it is never written to `pylib/omniroute_activity_profiles.json`**. With no reviewed entry the updater always defers `capability` (acceptable and honest).
  - **Command:** `LLM_ENV_UPDATE_CHARACTERIZE=1 uv run pytest tests/test_omniroute_characterize.py -v -s`.
- [ ] **Step 4: Docs.** Replace "safety deferral, not an updater" wording with accurate behavior: eligibility conditions, that the default topology becomes eligible only after the deployed compose is re-rendered without the remote-setup dependency and `make migrate-remote-setup` has run (state the re-render's real side effects listed in Task 7: device-probe `podman run --rm`, Phoenix prepare and observer-image build when System One is enabled, legacy `llm-mdns.service` stop, `systemctl --user daemon-reload`), the three exit codes, the two env overrides, and the explicit non-promises (not zero-downtime, no rollback, observation is not admission control). Document that a running image without a reviewed checked-in activity profile **always defers** (`capability`). Verify nothing of the old wording survives: `rg -n -i "safety deferral|intentionally deferred|Safely defer" README.md QUICK_START.md scripts/help.sh AGENTS.md Makefile` must print nothing.
- [ ] **Step 5: Resource note.** From the sizing research, append one paragraph to the README operations section: caps are soft/hard policy rather than reservations, OmniRoute 2 GiB is an untested candidate (prior 1 GiB tripped the 503 guard), and no limits were changed by this plan.
- [ ] **Step 6: `make validate && make test`; run the Step 2 command once and record its result.**
- [ ] **Step 7: Commit if approved.**

---

## Self-review

- **Spec coverage:** immutable resolution + staged verification + pinned-index deferral (T1); full backend inventory (whole create configuration hashed), commit-time snapshot compare, SHA256 deployment fingerprint, config/effective-compose/running-container agreement with digest pins preserved, closed deferral enum, artifact directory contract, one scrubbed backend environment (T1-T2); capability profile schema with independent arrival/inflight/queue coverage and probe-route rule, strict required-source typing, row/active-row continuity, inter-sample clock and health-timestamp continuity, bounded I/O, generations, sample budget (T3); idle gate with health-advance rule, generation resets, mandatory resample, overrides, shared monotonic deadline file (T4); observer supervisor owning the gate and publishing the verdict, parent lease, responsive cleanup, canonical lock path (T5); evidence-based projection with a provider round trip and bind-source checks, provider flags, detached lock-holding commit worker, deadline-bounded recheck and readiness, tag move, health wait (T6); migration with re-render precondition, recovery, bounded single-flight `/config` (T7); concrete isolated-backend proofs, characterization harness with derivation rules, docs (T8). The full fingerprint is **not** reduced.
- **Deliberate deviations (recorded):** SHA256 of raw bytes in Python replaces `skopeo manifest-digest FILE` (byte-identical); bounded login/GET live in a new module because `pylib.omniroute._login/_request` must stay unmodified; the observer is a persistent supervisor writing `latest.json` (with the gate verdict) rather than a shell pipe, and the gate lives in it instead of a separate `decide` process; `verify-service` was dropped in favor of `verify-commit --service`; the running-image-versus-configuration check compares repository (and a digest pin exactly) but accepts a digest-form running reference for a tag-form configuration, because a container created by this updater from the immutable reference legitimately reports `repo@sha256:...` while the configured tag moves; the spec's strict configured-versus-effective-compose equality is enforced separately and keeps digest pins.
- **Placeholder scan:** every test body, fixture, CLI flag (producer, storage format, consumer), script section, image definition and compose definition is written out in its owning task; the only prose-described components are `JournalctlReader` (algorithm specified step by step with four concrete tests), the thin CLI wrappers (pattern fixed by Tasks 1-2) and the Task 8 Step 3 scenario drivers (behavior, inputs and derivation rules fixed; their outputs are measurements and cannot exist before the run). No `TODO`/`TBD`.
- **Type consistency:** `Runner` (`run(argv, timeout=None)`), `UpdateError`, `Deferral`, `Reason`, `ImageIdentity`, `Snapshot`, `Deployment` (with canonical `image_id`), `Sample`, `Collector`, `IdleGate(min_wait, deadline)`, `Profile` are used identically across tasks; `Reason` is the single enum for `Deferral.reason`, `Sample.reason` and stdout `deferred: <reason>`; helper modules under `tests/` are imported by bare name (`from omniroute_fakes import ...`) because `tests/` has no `__init__.py`.
- **Open decisions for the user (not guessed):** (1) there is no compose-only re-render path; Task 7 documents `setup/render-unit.sh`'s side effects and leaves the re-render to the user, so whether to add a narrow `render-compose` make target is a separate decision; (2) the Podman label keys and `serviceIsRemote` field are verified read-only in Task 2 Step 1 and the executor stops and asks if they differ; (3) the real OmniRoute access-log field names and API pointers come only from the Task 8 Step 3 characterization, which is a manual-review step.
- **Known risks:** the profile list is empty until a human reviews a characterization, so the real updater defers with `capability`; the production dependency migration needs the user to schedule it; the Task 8 proofs require host-local `registry:2` and `python:3.12-slim` images, `podman-compose`, `skopeo` and a readable journal, and use the `vfs` driver (slow first build); Task 6 shell cases run on the real 5 s sampling cadence (5-20 s each); the `$`-escaping and projection semantics are checked against the running container's evidence and the provider round trip, but remain provider-version dependent (podman-compose 1.6.0 is the only verified version).

