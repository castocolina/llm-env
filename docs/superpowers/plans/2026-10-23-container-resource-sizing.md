# Container Resource Sizing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every container a persistent, validated, reported per-service CPU/RAM policy override that every render path (start, boot render, Phoenix) derives from one effective policy, and gate any hard-memory-cap reduction (starting with the OmniRoute 2 GiB candidate) on measured, container-bound cgroup evidence that `validate_config` re-checks on every write path.

**Architecture:** `pylib/resources.py` is the single source of per-service policy: a `DEFAULT_POLICY` table plus a pure, validated `effective_policy(service, overrides)` merge. User intent lives in `resources.overrides.<service>` in `models.yml`. Every consumer that turns policy into container limits (`llmenv render-compose`, which both `scripts/start.sh` and `setup/render-unit.sh`/`enable-boot` reach, `scripts/phoenix.sh`, `scripts/check-setup.sh`) recomputes it from that effective policy on each run; the generated `resources.<service>` copies in `models.yml` become an informational mirror that no render path reads. A new `pylib/resource_evidence.py` collects read-only cgroup evidence (memory, swap, events, PSI, the application's own pressure-guard thresholds) from the one container the repo declares for each service into a per-container-lifetime, hash-chained sample ledger (the loader recomputes the chain, so an edited, removed or reordered sample is detected) and evaluates a candidate cap against every sample of it. A lowered `memory_max_mib` must carry a persisted `memory_max_evidence` reference (absolute resolved path, sha256, container id, start time, collection time, peak) that `validate_config` re-verifies against the stored ledger, so a hand edit cannot bypass the gate and the reference does not depend on the caller's working directory. The default constants do not change in this plan.

**Tech Stack:** Python 3 (`uv run llmenv.py`), PyYAML, pytest, pyright, ruff, bash + shellcheck, jq, yq, podman (rootless, cgroup v2).

**Spec:** `/var/home/bazzite/git/llm-env/docs/superpowers/research/2026-10-20-container-resource-sizing.md` (copied there from the untracked `.docs/` tree by Task 0; section 7 "Candidates, durability and unresolved work" is the requirement).

## Verified facts this plan is built on

Checked against the code and the live host on 2026-10-23. Re-check them if the code has moved.

- Hard caps: OmniRoute 3072 + System One (Laya) 4608 + remote-setup 64 + Phoenix 1024 = **8768 MiB** (`pylib/resources.py`). `llm_server` has its own host-derived cap and is 0 when disabled.
- Reservations are soft: Compose `mem_reservation` becomes cgroup `memory.low`. Live: OmniRoute `memory.low` = 1073741824, `memory.min` = 0 (`tmp/research-2026-10-20/resources/measurement.json`). Lowering a reservation or a cap releases no RAM; a lower cap only bounds worst-case growth.
- No persistent per-service override exists. `compute_resource_limits` ignores whatever `models.yml` holds for `resources.omniroute|remote_setup|phoenix` and returns constants; `scripts/start.sh` (lines ~76-150) then writes those constants back with `yq -i`, and `setup/setup.sh` does the same once. System One policy is not in config at all; `pylib/compose.py` reads `SYSTEM_ONE_*` constants directly.
- **Boot does not run `scripts/start.sh`.** `setup/enable-boot.sh` sets `server.start_at_boot`, then runs `setup/render-unit.sh`, which calls `llmenv render-compose` and writes a unit whose `ExecStart` is `podman compose -f <rendered file> up -d` (plus `ExecStartPost=scripts/phoenix.sh start` when System One is on). Today `render-compose` reads the persisted `resources.omniroute|remote_setup|llm_server` mirror (`pylib/compose.py:74-77`) and `phoenix.sh load_resource_env` reads `resources.phoenix` (`scripts/phoenix.sh:101-111`); `scripts/check-setup.sh:250` reads it too. A mirror that is stale relative to an override, or relative to the host (the `llm_server` remainder shrinks when a minimum is raised), would reach containers at boot and on `render-unit.sh`. Boot starts from the last rendered file, so a changed override is rendered by the next `make start`/`make restart`/`render-unit.sh` and reaches boot only after such a render. `render-unit.sh` writes files and runs `systemctl --user daemon-reload`; it does **not** restart or recreate running containers, so a change reaches the running stack only through `make restart` (or `make start`, which stops an active unit first), which interrupts in-flight requests.
- `llmenv budget` calls `compute_resource_limits` twice and `llmenv resources` once (`llmenv.py` ~330-430); all three must see overrides or the RAM-feasibility check disagrees with what gets rendered.
- OmniRoute container `omniroute` (id `44b8702af6cd...`, `StartedAt` epoch 1792446319, 2026-10-19 18:45 -03). **Live `memory.peak` on 2026-10-23: 1,731,936,256 B = 1,651.7 MiB** (it was 593 MiB on 2026-10-20 and 1,222.8 MiB earlier on 2026-10-23, so the lifetime peak is still rising). Against the 2 GiB candidate that is a peak/cap ratio of **0.806**, only ~89 MiB below the 0.85 guard ratio. `memory.swap.peak` = 631,885,824 B = **602.6 MiB** (swap current 394 MiB), so peak + swap peak = 2,254 MiB = **1.10** of 2048. `memory.events` max/oom/oom_kill = 0; `memory.max` = 3221225472; cgroup `memory.pressure` all zero at collection; host `MemAvailable` ~4.5 GiB, host memory PSI `some avg10` 0.00 at that instant.
- Consequences for the 2 GiB candidate: `memory.peak` is a monotone lifetime counter, so the gate refuses to promote while it is still rising (it requires 24 h of samples with no new peak). On the 2026-10-23 numbers the demand-bound rule (`memory.peak + memory.swap.peak` must fit the candidate) is expected to reject 2048 MiB (1.10) while accepting 2560 MiB (0.645 resident, 0.88 with swap). **A rejection is a valid outcome of this plan**; Task 12 records it and stops rather than lowering the gate.
- `memory.max` limits resident memory only; swap is accounted separately (`memory.swap.max`). A lower cap therefore does **not** force pages that already sit in swap back through reclaim; it bounds resident growth and pushes the excess demand into swap, which is where thrashing starts. `memory.peak` and `memory.swap.peak` are two independent lifetime high-water marks that need not have occurred at the same moment (research section 2: do not sum them as a measured simultaneous peak). Their sum is only a **conservative upper bound on the anonymous demand**; the gate uses it as a fail-closed bound and every message, comment and record in this plan words it that way.
- OmniRoute's resource-pressure guard sheds load with `503 resource_pressure` when `(memory.current - file cache) / memory.max >= criticalRatio` **or** memory PSI crosses its thresholds. Constants compiled into the image (read from the running container with `podman exec -u 0 omniroute grep -hoE 'highRatio:[0-9.]+,criticalRatio:[0-9.]+,recoveryRatio:[0-9.]+,highPsiAvg10:[0-9.]+,criticalPsiAvg10:[0-9.]+' /app/.build/next/server/chunks/*.js`): `highRatio:.85, criticalRatio:.92, recoveryRatio:.75, highPsiAvg10:30, criticalPsiAvg10:60, recoveryPsiAvg10:15, sustainedSamplesHigh:2, sustainedSamplesCritical:2`. PSI is read from the unit's cgroup `memory.pressure` (falling back to `/proc/pressure/memory`) and can be switched off by `OMNIROUTE_PRESSURE_PSI_DISABLED` (`1|true|yes|on`). There is no env override for the ratios or PSI values. They belong to this image version and are re-read by every evidence sample, never assumed.
- The guard's log signal is `[resourcePressure] critical pressure guard tripped (reason=...); returning 503` (and `[chatCore] heap pressure guard tripped`). Both strings exist in the image chunks, console output reaches `podman logs` (the `[STREAM]`, `[HealthCheck]` prefixes are present), and the current container's log has **0** matches. `resource_pressure` alone is the JSON error code in the 503 body and is **not** a log string (0 hits), so it must not be used as the soak's log pattern.
- The main stack's compose project is **implicit**. podman compose names it after the directory of the rendered file: `tools/lib.sh` sets `COMPOSE_FILE=${HOME}/.config/llm-env/docker-compose.yml`, the generated unit runs `podman compose -f docker-compose.yml up -d` from that directory with no `-p`, and `render_compose` emits no top-level `name:`. Live on 2026-10-23, `omniroute`, `remote-setup` and `system-one` all carry `com.docker.compose.project=llm-env` and `com.docker.compose.project.working_dir=/var/home/bazzite/.config/llm-env`; Phoenix carries project `llm-env-phoenix` and working dir `<repo>/docker/phoenix`. The bare name `llm-env` is not an ownership proof (a stack started from the repo checkout, whose directory is also called `llm-env`, would get the same project name), so evidence pins the project label AND the working directory, compared by resolved path (`$HOME` is `/home/bazzite`, a symlink to `/var/home/bazzite`, which is what podman recorded).

## Global Constraints

Copied from `/var/home/bazzite/git/llm-env/AGENTS.md`; every task includes these.

- After editing any `.sh` file, run `make validate`.
- After editing any `.py` file, run `make validate && make test`. Ruff enforces import sorting (I001) and string-concatenation style (ISC004): after adding imports run `uv run ruff check --select I --fix <file>`, and wrap implicitly concatenated strings inside list/tuple/call arguments in parentheses.
- Makefile target bodies longer than 3 lines must delegate to a `.sh` file.
- Python is invoked only as `uv run llmenv.py <subcommand>`.
- All output must be in English regardless of input language.
- Do not assume. Research platform behavior first; never hardcode a value that can be measured.
- Ephemeral artifacts (evidence ledgers, logs, snapshots) go under the repository's `./tmp/` (already ignored). Durable research lives only under `docs/superpowers/`. Evidence that backs a persisted override is copied to a durable store next to the config (`~/.config/llm-env/evidence/`), never into `tmp/`, so cleaning `tmp/` cannot invalidate `models.yml`.
- Retain all plans; do not delete or rename existing plans.
- Work directly in the current checkout.
- Zero in a policy field means "no limit" to `pylib/compose.py`; an override must never be able to produce zero.
- Gate floors are policy constants, not flags: the observation window cannot go below 24 h and the peak ratio must satisfy 0 < r <= 1 and cannot exceed the guard's own `highRatio`. Flags may only tighten.
- Never copy the live `models.yml` (it holds secrets) into `tmp/` or anywhere else for experiments.
- Use `rg`, `sd`, `fd`, `bat` in shell commands. Commit steps append the attribution trailer that the executing session's own instructions require.

## Review Focus

Failure modes the spec implies but a quick read would miss. Each has a test in the task named in brackets.

1. An override of `0`, a negative, a bool, a string like `"2048"`, or a float for a MiB field must be rejected, never rendered as "no limit" [Task 1, Task 3].
2. A hand-edited typo (`memory_max` instead of `memory_max_mib`) or unknown service key must be a validation error, not silently ignored [Task 1, Task 3].
3. An override for a disabled service (System One / Phoenix while System One is off) must be inert: not charged to the host floor, not in totals, no crash [Task 2].
4. Raising a minimum (soft reservation) must shrink the llm-server remainder and, on a host too small, raise `ResourceError` instead of silently clamping [Task 2, Task 4].
5. Every render path (`start.sh`, `render-unit.sh`/`enable-boot`, Phoenix start, `check-setup`) must use the same effective policy: `resource-override set` then `render-unit.sh` renders the override, `clear` then `start` renders the default even when `models.yml` still holds a stale generated mirror [Task 5, Task 6].
6. A lowered `memory_max_mib` with no evidence reference, a reference whose file is missing, edited (sha256 mismatch) or no longer passes the gate, or whose candidate differs from the cap, must fail `validate_config`; `resource-override clear` must still work in that state [Task 1, Task 8, Task 9].
7. Evidence from another container lifetime, an old sample, a short window, a still-rising lifetime peak, missing `memory.events` counters, wrong JSON types or missing swap/PSI files must be rejected (fail closed) [Task 7, Task 9].
8. No flag can lower the 24 h floor, push the peak ratio outside 0 < r <= 1, or raise it above the guard's `highRatio`; a missing guard threshold fails closed for OmniRoute [Task 7, Task 9].
9. Swap and memory PSI are gated, not just `memory.peak` and `memory.events` [Task 7].
10. The soak must capture host `MemAvailable` and memory PSI with a criterion written down before the change, and its log signal must be verified before it is relied on. A soak window with missing, `NA` or unparseable samples (container PSI above all), too few rows, a gap, or a ledger without post-change samples must be reported as insufficient and must never pass on the zero defaults of an empty summary [Task 12].
11. Evidence for a service must come from the container the repo declares for that service (compose `container_name` plus the compose service label, project label and project working directory, for the main stack as well as Phoenix), never from a container name carried by the ledger: a ledger of another running workload, or a container with the declared name and service label in another compose project or directory, must not authorise a cap [Task 7, Task 9].
12. A high memory-pressure reading or a missing or different guard threshold in ANY sample of the observation window must refuse the cap, even when the newest sample is calm [Task 7].
13. `resource-override set` saves configuration only; applying it to running containers is a separate, disruptive step (`make restart`) whose impact is stated wherever the command is documented, and Task 12 judges guard logs and counters over the whole post-change window including any extension [Task 9, Task 11, Task 12].
14. The evidence reference must hold an absolute, resolved path even when `--config` is relative, and a later command run from any other directory must still verify it; a relative stored path is rejected [Task 1, Task 7, Task 8, Task 9].
15. Deleting, editing or reordering a recorded sample of a ledger must be detected by the loader (hash chain), not silently accepted as a shorter history [Task 7].

---

## File Structure

| File | Responsibility |
| --- | --- |
| `docs/superpowers/research/2026-10-20-container-resource-sizing.md` (+ two linked siblings) (create, copy) | Durable requirement source (Task 0) |
| `pylib/resources.py` (modify) | `DEFAULT_POLICY`, gate floors, `validate_overrides` (incl. evidence-reference shape), `effective_policy`, override-aware `compute_resource_limits`, `summarize_policy` |
| `pylib/config.py` (modify) | Validate `resources.overrides` and re-verify evidence references; leave overrides untouched in `migrate_config` |
| `pylib/compose.py` (modify) | `render_compose(..., limits=...)`: all service limits come from the caller, never from the persisted mirror |
| `pylib/resource_evidence.py` (create) | Read-only cgroup evidence collection, sample ledger, strict loading, cap gate, reference build/verify |
| `llmenv.py` (modify) | `limits_for` helper; `render-compose`, `budget`, `resources` use it; new `effective-policy`, `resource-evidence`, `resource-override show|set|check|clear` |
| `scripts/start.sh` (modify) | Print the policy summary |
| `scripts/phoenix.sh`, `scripts/check-setup.sh` (modify) | Read Phoenix policy from `llmenv effective-policy`; new read-only `phoenix.sh resource-env` action |
| `scripts/resources.sh` (create) | `make resources`: effective policy table |
| `scripts/resources-measure.sh` (create) | `make resources-measure SERVICE=...`: append a sample to the per-container ledger under `tmp/resource-evidence/` |
| `Makefile`, `scripts/help.sh` (modify) | Two new targets |
| `AGENTS.md`, `.agents/architecture.md`, `models.yml.example` (modify) | Command list, architecture rows, the override section, stale example fix |
| Tests: `tests/test_resources.py`, `tests/test_config.py`, `tests/test_cli.py`, `tests/test_compose.py`, `tests/test_omniroute_characterize.py`, `tests/test_shell.py`, `tests/test_phoenix_operations.py` (modify); `tests/test_resource_evidence.py`, `tests/resource_fixtures.py` (create) | |

Design choices recorded here: (1) the persisted generated copies `resources.<service>` stay as an informational mirror (existing setup/start tests assert they are written) but no render path reads them any more; removing the mirror is a follow-up. (2) System One, OmniRoute, remote-setup and Phoenix policy is host-independent; the `llm_server` remainder is host-dependent, so `llmenv render-compose` recomputes all of it with `limits_for(cfg, host_resources())` on every render. (3) Evidence is stored as a hash-chained sample ledger per container lifetime (`<service>-<container id[:12]>.json`) so "peak still rising" is decidable from data, not from a single reading, and so dropping or editing a recorded sample is detected by the loader. The chain has no secret: it is tamper-evident against loss and edits, not a security boundary (see Known limits).

---

### Task 0: Durable requirement source

**Files:**
- Create: `docs/superpowers/research/2026-10-20-container-resource-sizing.md`, `docs/superpowers/research/2026-10-20-laya-jev-grafana-observability.md`, `docs/superpowers/research/2026-10-20-omniroute-updater-observations.md` (copies of the `.docs/superpowers/research/` files of the same names; the first links to the other two)

**Interfaces:**
- Produces: the spec path named in this plan's header exists in a tracked tree.

- [ ] **Step 1: Copy the research and check its links**

```bash
cp -n .docs/superpowers/research/2026-10-20-container-resource-sizing.md \
      .docs/superpowers/research/2026-10-20-laya-jev-grafana-observability.md \
      .docs/superpowers/research/2026-10-20-omniroute-updater-observations.md \
      docs/superpowers/research/
rg -o '\]\(([^)#]+\.md)' -r '$1' docs/superpowers/research/2026-10-20-container-resource-sizing.md | sort -u | while read -r link; do test -f "docs/superpowers/research/$link" && echo "ok $link" || echo "MISSING $link"; done
```
Expected: no `MISSING` line. If one appears, copy that file as well from `.docs/` (never edit the copies' content). `cp -n` refuses to overwrite, so a re-run cannot clobber later edits. Leave `.docs/` untouched.

- [ ] **Step 2: Run the docs tests and commit**

Run: `uv run pytest tests/test_docs.py -q`
Expected: PASS.

```bash
git add docs/superpowers/research/2026-10-20-container-resource-sizing.md docs/superpowers/research/2026-10-20-laya-jev-grafana-observability.md docs/superpowers/research/2026-10-20-omniroute-updater-observations.md
git commit -m "docs(research): preserve the container resource sizing requirement source"
```

---

### Task 1: Policy table, override validation, evidence-reference shape, effective policy

**Files:**
- Modify: `pylib/resources.py` (imports at top; new block after `OMNIROUTE_CHAT_ADMISSION_QUEUE_MS`)
- Create: `tests/resource_fixtures.py`
- Test: `tests/test_resources.py`

**Interfaces:**
- Produces:
  - `DEFAULT_POLICY: dict[str, dict[str, Any]]` keyed `omniroute`, `remote_setup`, `system_one`, `phoenix`, each with exactly `cpus_min`, `cpus_max`, `memory_min_mib`, `memory_max_mib`
  - `OVERRIDABLE_SERVICES: tuple[str, ...]`, `POLICY_KEYS: tuple[str, ...]`, `EVIDENCE_KEY = "memory_max_evidence"`
  - `MIN_OBSERVATION_HOURS_FLOOR = 24.0`, `MAX_PEAK_RATIO_CEILING = 1.0` (gate floors; the evidence module imports them from here)
  - `validate_overrides(overrides: object) -> list[str]` (structural only: no file access)
  - `effective_policy(service: str, overrides: Mapping[str, Any] | None) -> dict[str, Any]` (only `POLICY_KEYS`; the evidence reference is never part of a policy)
- Evidence reference shape (all keys required): `path` (absolute path string; a relative path would depend on the caller's working directory, so it is refused), `sha256` (64 hex), `container_id` (64 hex), `started_at_epoch` (int > 0), `collected_at_epoch` (int > 0), `peak_bytes` (int > 0), `swap_peak_bytes` (int >= 0), `candidate_mib` (int > 0, must equal `memory_max_mib`), `max_peak_ratio` (0 < r <= 1), `min_observation_hours` (>= 24).
- Rule: effective `memory_max_mib` below `DEFAULT_POLICY[service]["memory_max_mib"]` requires `memory_max_evidence`; `memory_max_evidence` without a lowered cap is an error.

- [ ] **Step 1: Write the failing tests**

Create `tests/resource_fixtures.py` (shared by later tasks; Task 7 appends the ledger builders):

```python
"""Shared constants and builders for resource override and evidence tests."""

MIB = 1024 * 1024
STARTED = 1_792_446_319  # omniroute StartedAt (epoch) of the live container, 2026-10-19
T0 = STARTED + 73 * 3600  # baseline sample
T1 = T0 + 25 * 3600  # newest sample, 98 h into the container's life
PEAK = 1_731_936_256  # 1,651.7 MiB: live memory.peak on 2026-10-23
SWAP_PEAK = 631_885_824  # 602.6 MiB: live memory.swap.peak on 2026-10-23
CONTAINER_ID = "44b8702af6cdd6eae0d81d660279b5cce953a90b2d39bbe51f1c719e37282702"


def evidence_ref(candidate_mib, **changes):
    """A structurally valid memory_max_evidence reference (no file behind it)."""
    ref = {
        "path": "/evidence/omniroute-0123456789abcdef.json",
        "sha256": "a" * 64,
        "container_id": CONTAINER_ID,
        "started_at_epoch": STARTED,
        "collected_at_epoch": T1,
        "peak_bytes": PEAK,
        "swap_peak_bytes": SWAP_PEAK,
        "candidate_mib": candidate_mib,
        "max_peak_ratio": 0.85,
        "min_observation_hours": 24.0,
    }
    ref.update(changes)
    return ref
```

In `tests/test_resources.py` add `import` lines below the existing `import pytest`: `from resource_fixtures import evidence_ref`. Add to the existing `from pylib.resources import (...)` list: `DEFAULT_POLICY, EVIDENCE_KEY, OVERRIDABLE_SERVICES, POLICY_KEYS, PHOENIX_CPUS_MAX, PHOENIX_MEMORY_MAX_MIB, effective_policy, validate_overrides`. Append at the end of the file:

```python
def test_default_policy_matches_the_module_constants():
    assert DEFAULT_POLICY["omniroute"] == _omniroute_expected()
    assert DEFAULT_POLICY["remote_setup"] == _remote_setup_expected()
    assert DEFAULT_POLICY["system_one"] == {
        "cpus_min": SYSTEM_ONE_CPUS_MIN,
        "cpus_max": SYSTEM_ONE_CPUS,
        "memory_min_mib": SYSTEM_ONE_MEMORY_MIN_MIB,
        "memory_max_mib": SYSTEM_ONE_MEMORY_MIB,
    }
    assert DEFAULT_POLICY["phoenix"]["cpus_max"] == PHOENIX_CPUS_MAX
    assert DEFAULT_POLICY["phoenix"]["memory_max_mib"] == PHOENIX_MEMORY_MAX_MIB
    assert set(OVERRIDABLE_SERVICES) == set(DEFAULT_POLICY)
    assert all(set(policy) == set(POLICY_KEYS) for policy in DEFAULT_POLICY.values())


def test_baseline_hard_memory_caps_sum_to_8768_mib():
    # Research 2026-10-20 section 2. Update deliberately if a default cap changes.
    assert sum(p["memory_max_mib"] for p in DEFAULT_POLICY.values()) == 8768


def test_effective_policy_without_overrides_is_the_default_and_a_copy():
    policy = effective_policy("omniroute", None)
    assert policy == DEFAULT_POLICY["omniroute"]
    policy["memory_max_mib"] = 1
    assert DEFAULT_POLICY["omniroute"]["memory_max_mib"] == OMNIROUTE_MEMORY_MAX_MIB


def test_effective_policy_merges_only_the_overridden_keys():
    policy = effective_policy("omniroute", {"omniroute": {"memory_max_mib": 4096}})
    assert policy == {**DEFAULT_POLICY["omniroute"], "memory_max_mib": 4096}
    other = effective_policy("remote_setup", {"omniroute": {"memory_max_mib": 4096}})
    assert other == DEFAULT_POLICY["remote_setup"]


def test_effective_policy_never_carries_the_evidence_reference():
    overrides = {"omniroute": {"memory_max_mib": 2560, EVIDENCE_KEY: evidence_ref(2560)}}
    policy = effective_policy("omniroute", overrides)
    assert policy == {**DEFAULT_POLICY["omniroute"], "memory_max_mib": 2560}
    assert EVIDENCE_KEY not in policy


def test_valid_overrides_produce_no_errors():
    assert validate_overrides(None) == []
    assert validate_overrides({}) == []
    assert validate_overrides({"omniroute": {"memory_max_mib": 4096, "cpus_max": 2.5}}) == []
    lowered = {"omniroute": {"memory_max_mib": 2560, EVIDENCE_KEY: evidence_ref(2560)}}
    assert validate_overrides(lowered) == []


def _without(ref, key):
    return {k: v for k, v in ref.items() if k != key}


@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        ([], "resources.overrides must be a mapping"),
        ({"llm_server": {"memory_max_mib": 1}}, "resources.overrides.llm_server is not an overridable service"),
        ({"omniroute": []}, "resources.overrides.omniroute must be a mapping"),
        ({"omniroute": {"memory_max": 2048}}, "resources.overrides.omniroute.memory_max is not an overridable field"),
        ({"omniroute": {"memory_max_mib": 0}}, "resources.overrides.omniroute.memory_max_mib must be a positive integer"),
        ({"omniroute": {"memory_max_mib": -1}}, "resources.overrides.omniroute.memory_max_mib must be a positive integer"),
        ({"omniroute": {"memory_max_mib": True}}, "resources.overrides.omniroute.memory_max_mib must be a positive integer"),
        ({"omniroute": {"memory_max_mib": "2048"}}, "resources.overrides.omniroute.memory_max_mib must be a positive integer"),
        ({"omniroute": {"memory_max_mib": 2048.5}}, "resources.overrides.omniroute.memory_max_mib must be a positive integer"),
        ({"omniroute": {"cpus_max": 0}}, "resources.overrides.omniroute.cpus_max must be a positive number"),
        ({"omniroute": {"cpus_max": float("inf")}}, "resources.overrides.omniroute.cpus_max must be a positive number"),
        ({"omniroute": {"memory_max_mib": 512}}, "effective memory_min_mib (1024) must not exceed effective memory_max_mib (512)"),
        ({"omniroute": {"cpus_min": 8}}, "effective cpus_min (8) must not exceed effective cpus_max (4)"),
        # A lowered cap needs evidence; evidence needs a lowered cap.
        ({"omniroute": {"memory_max_mib": 2048}}, "needs a memory_max_evidence reference"),
        ({"omniroute": {EVIDENCE_KEY: evidence_ref(2048)}}, "only valid together with a memory_max_mib below"),
        ({"omniroute": {"memory_max_mib": 4096, EVIDENCE_KEY: evidence_ref(4096)}}, "only valid together with a memory_max_mib below"),
        ({"omniroute": {"memory_max_mib": 2048, EVIDENCE_KEY: evidence_ref(2560)}}, "candidate_mib (2560) must equal memory_max_mib (2048)"),
        ({"omniroute": {"memory_max_mib": 2048, EVIDENCE_KEY: []}}, "memory_max_evidence must be a mapping"),
        ({"omniroute": {"memory_max_mib": 2048, EVIDENCE_KEY: _without(evidence_ref(2048), "path")}}, "memory_max_evidence.path is missing"),
        ({"omniroute": {"memory_max_mib": 2048, EVIDENCE_KEY: evidence_ref(2048, path="evidence/omniroute-0123456789abcdef.json")}}, "memory_max_evidence.path is invalid"),
        ({"omniroute": {"memory_max_mib": 2048, EVIDENCE_KEY: evidence_ref(2048, path="")}}, "memory_max_evidence.path is invalid"),
        ({"omniroute": {"memory_max_mib": 2048, EVIDENCE_KEY: evidence_ref(2048, sha256="xyz")}}, "memory_max_evidence.sha256 is invalid"),
        ({"omniroute": {"memory_max_mib": 2048, EVIDENCE_KEY: evidence_ref(2048, peak_bytes="1")}}, "memory_max_evidence.peak_bytes is invalid"),
        ({"omniroute": {"memory_max_mib": 2048, EVIDENCE_KEY: evidence_ref(2048, max_peak_ratio=1.5)}}, "memory_max_evidence.max_peak_ratio is invalid"),
        ({"omniroute": {"memory_max_mib": 2048, EVIDENCE_KEY: evidence_ref(2048, max_peak_ratio=0)}}, "memory_max_evidence.max_peak_ratio is invalid"),
        ({"omniroute": {"memory_max_mib": 2048, EVIDENCE_KEY: evidence_ref(2048, min_observation_hours=1.0)}}, "memory_max_evidence.min_observation_hours is invalid"),
        ({"omniroute": {"memory_max_mib": 2048, EVIDENCE_KEY: {**evidence_ref(2048), "extra": 1}}}, "memory_max_evidence.extra is not an evidence field"),
    ],
)
def test_invalid_overrides_are_reported(overrides, fragment):
    errors = validate_overrides(overrides)
    assert any(fragment in error for error in errors), errors
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_resources.py -q -x`
Expected: FAIL at collection with `ImportError: cannot import name 'DEFAULT_POLICY' from 'pylib.resources'`.

- [ ] **Step 3: Implement**

In `pylib/resources.py` change the imports:

```python
from __future__ import annotations

import math
import os
import re
from collections.abc import Callable, Mapping
from typing import Any, TypeGuard
```

Insert directly after the line `OMNIROUTE_CHAT_ADMISSION_QUEUE_MS = 8000`:

```python
# Built-in per-service policy. These are the defaults that persistent
# `resources.overrides.<service>` entries in models.yml are merged over; they
# are policy choices, not measurements -- see docs/superpowers/plans/
# 2026-10-23-container-resource-sizing.md for how a lower cap is validated.
DEFAULT_POLICY: dict[str, dict[str, Any]] = {
    "omniroute": {
        "cpus_min": OMNIROUTE_CPU_MIN,
        "cpus_max": OMNIROUTE_CPU_MAX,
        "memory_min_mib": OMNIROUTE_MEMORY_MIN_MIB,
        "memory_max_mib": OMNIROUTE_MEMORY_MAX_MIB,
    },
    "remote_setup": {
        "cpus_min": REMOTE_SETUP_CPU_MIN,
        "cpus_max": REMOTE_SETUP_CPU_MAX,
        "memory_min_mib": REMOTE_SETUP_MEMORY_MIN_MIB,
        "memory_max_mib": REMOTE_SETUP_MEMORY_MAX_MIB,
    },
    "system_one": {
        "cpus_min": SYSTEM_ONE_CPUS_MIN,
        "cpus_max": SYSTEM_ONE_CPUS,
        "memory_min_mib": SYSTEM_ONE_MEMORY_MIN_MIB,
        "memory_max_mib": SYSTEM_ONE_MEMORY_MIB,
    },
    "phoenix": {
        "cpus_min": PHOENIX_CPUS_MIN,
        "cpus_max": PHOENIX_CPUS_MAX,
        "memory_min_mib": PHOENIX_MEMORY_MIN_MIB,
        "memory_max_mib": PHOENIX_MEMORY_MAX_MIB,
    },
}
OVERRIDABLE_SERVICES = tuple(DEFAULT_POLICY)
POLICY_KEYS = ("cpus_min", "cpus_max", "memory_min_mib", "memory_max_mib")
EVIDENCE_KEY = "memory_max_evidence"

# Hard floors of the cap-reduction gate (pylib/resource_evidence.py). They live
# here because validate_overrides re-checks them on every persisted reference;
# no CLI flag or hand edit may go below / outside them.
MIN_OBSERVATION_HOURS_FLOOR = 24.0
MAX_PEAK_RATIO_CEILING = 1.0

_HEX64 = re.compile(r"[0-9a-f]{64}")


def _is_finite_number(value: object) -> TypeGuard[int | float]:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _is_int_at_least(value: object, minimum: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= minimum


def _is_hex64(value: object) -> bool:
    return isinstance(value, str) and _HEX64.fullmatch(value) is not None


# Shape of a persisted evidence reference; the file behind `path` is verified
# by pylib/resource_evidence.py:verify_evidence_reference (called from
# validate_config), not here.
_EVIDENCE_FIELDS: dict[str, Callable[[object], bool]] = {
    "path": lambda v: isinstance(v, str) and os.path.isabs(v),
    "sha256": _is_hex64,
    "container_id": _is_hex64,
    "started_at_epoch": lambda v: _is_int_at_least(v, 1),
    "collected_at_epoch": lambda v: _is_int_at_least(v, 1),
    "peak_bytes": lambda v: _is_int_at_least(v, 1),
    "swap_peak_bytes": lambda v: _is_int_at_least(v, 0),
    "candidate_mib": lambda v: _is_int_at_least(v, 1),
    "max_peak_ratio": lambda v: _is_finite_number(v) and 0 < v <= MAX_PEAK_RATIO_CEILING,
    "min_observation_hours": lambda v: _is_finite_number(v) and v >= MIN_OBSERVATION_HOURS_FLOOR,
}


def _validate_evidence_reference(prefix: str, ref: object) -> list[str]:
    if not isinstance(ref, Mapping):
        return [f"{prefix} must be a mapping"]
    errors = [
        f"{prefix}.{key} is not an evidence field"
        for key in ref
        if key not in _EVIDENCE_FIELDS
    ]
    for key, is_valid in _EVIDENCE_FIELDS.items():
        if key not in ref:
            errors.append(f"{prefix}.{key} is missing")
        elif not is_valid(ref[key]):
            errors.append(f"{prefix}.{key} is invalid ({ref[key]!r})")
    return errors


def validate_overrides(overrides: object) -> list[str]:
    """Return human-readable errors for a `resources.overrides` value.

    Every policy value must be strictly positive: 0 means "no limit" to
    pylib/compose.py, so an override of 0 would silently uncap a container.
    A memory_max_mib below the built-in default must carry a
    `memory_max_evidence` reference; this function checks its shape, and
    validate_config verifies the file behind it.
    """
    if overrides is None:
        return []
    if not isinstance(overrides, Mapping):
        return ["resources.overrides must be a mapping"]
    errors: list[str] = []
    for service, override in overrides.items():
        prefix = f"resources.overrides.{service}"
        if service not in DEFAULT_POLICY:
            errors.append(
                f"{prefix} is not an overridable service "
                f"(expected one of {', '.join(OVERRIDABLE_SERVICES)})"
            )
            continue
        if not isinstance(override, Mapping):
            errors.append(f"{prefix} must be a mapping")
            continue
        service_errors: list[str] = []
        for key, value in override.items():
            if key == EVIDENCE_KEY:
                continue
            if key not in POLICY_KEYS:
                service_errors.append(
                    f"{prefix}.{key} is not an overridable field "
                    f"(expected one of {', '.join((*POLICY_KEYS, EVIDENCE_KEY))})"
                )
            elif key.startswith("cpus"):
                if not (_is_finite_number(value) and value > 0):
                    service_errors.append(
                        f"{prefix}.{key} must be a positive number (0 would remove the limit)"
                    )
            elif not _is_int_at_least(value, 1):
                service_errors.append(
                    f"{prefix}.{key} must be a positive integer (0 would remove the limit)"
                )
        errors.extend(service_errors)
        if service_errors:
            continue
        merged = effective_policy(service, {service: override})
        for low, high in (("cpus_min", "cpus_max"), ("memory_min_mib", "memory_max_mib")):
            if merged[low] > merged[high]:
                errors.append(
                    f"{prefix}: effective {low} ({merged[low]}) must not exceed "
                    f"effective {high} ({merged[high]})"
                )
        default_cap = DEFAULT_POLICY[service]["memory_max_mib"]
        lowered = merged["memory_max_mib"] < default_cap
        has_reference = EVIDENCE_KEY in override
        if lowered and not has_reference:
            errors.append(
                f"{prefix}.memory_max_mib ({merged['memory_max_mib']}) is below the "
                f"built-in default ({default_cap}) and needs a {EVIDENCE_KEY} "
                "reference written by `uv run llmenv.py resource-override set --evidence` "
                "(hand edits are rejected)"
            )
        elif has_reference and not lowered:
            errors.append(
                f"{prefix}.{EVIDENCE_KEY} is only valid together with a "
                f"memory_max_mib below the built-in default ({default_cap})"
            )
        if has_reference:
            reference_errors = _validate_evidence_reference(
                f"{prefix}.{EVIDENCE_KEY}", override[EVIDENCE_KEY]
            )
            errors.extend(reference_errors)
            if (
                not reference_errors
                and override[EVIDENCE_KEY]["candidate_mib"] != merged["memory_max_mib"]
            ):
                errors.append(
                    f"{prefix}.{EVIDENCE_KEY}.candidate_mib "
                    f"({override[EVIDENCE_KEY]['candidate_mib']}) must equal "
                    f"memory_max_mib ({merged['memory_max_mib']})"
                )
    return errors


def effective_policy(
    service: str, overrides: Mapping[str, Any] | None
) -> dict[str, Any]:
    """Built-in policy for `service` with validated overrides merged over it.

    Only POLICY_KEYS merge; the evidence reference is metadata, not policy.
    """
    override = (overrides or {}).get(service) or {}
    return {
        **DEFAULT_POLICY[service],
        **{key: value for key, value in override.items() if key in POLICY_KEYS},
    }
```

Pyright: `_is_finite_number` is a `TypeGuard`, so the comparisons type-check without ignores; if pyright flags a line, fix that line rather than adding an ignore.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_resources.py -q`
Expected: PASS (all old and new tests).

- [ ] **Step 5: Validate and commit**

Run: `make validate && make test`
Expected: both succeed.

```bash
git add pylib/resources.py tests/test_resources.py tests/resource_fixtures.py
git commit -m "feat(resources): add a validated per-service policy table and evidence-gated override merge"
```

---

### Task 2: Override-aware budget, new output shape, summary

**Files:**
- Modify: `pylib/resources.py` (`_floor_words`, `compute_resource_limits` head and return block, new `summarize_policy`)
- Modify (existing tests): `tests/test_resources.py` lines containing `with_one["system_one"] ==` and `["system_one"] == {"cpus": 0`
- Test: `tests/test_resources.py`

**Interfaces:**
- Consumes: `DEFAULT_POLICY`, `POLICY_KEYS`, `effective_policy`, `validate_overrides` from Task 1.
- Note: tests here use raised caps (4096 / 5120) because a cap below the built-in default needs an evidence reference (Task 1); the reference is metadata and never appears in `overrides_applied`.
- Produces:
  - `compute_resource_limits(..., system_one_enabled: bool = False, overrides: Mapping[str, Any] | None = None) -> dict[str, Any]`
  - Result keys `omniroute`, `remote_setup`, `system_one`, `phoenix`: each `{cpus_min, cpus_max, memory_min_mib, memory_max_mib}` (zeros for `system_one` and `phoenix` when System One is disabled). **`system_one` changes from `{cpus, memory_mib}` to this shape.**
  - Result key `overrides_applied: dict[str, list[str]]` (active services only, sorted field names)
  - Result key `totals: {"hard_memory_cap_mib": int, "soft_memory_reservation_mib": int}`
  - `summarize_policy(limits: Mapping[str, Any]) -> list[str]`
  - Invalid overrides raise `ResourceError`.

- [ ] **Step 1: Find every consumer of the old `system_one` shape**

Run: `rg -n '(limits|result|with_one|payload|budget|resources|compute_resource_limits\([^)]*\))\[.system_one.\]' llmenv.py pylib scripts setup tests tools` and `rg -n 'jq.*\.system_one' scripts setup tools`
Expected: exactly two hits, the assertions at `tests/test_resources.py:256` (`with_one["system_one"]`) and `:274` (`compute_resource_limits(16, 32768)["system_one"]`); the second command prints nothing. (The pattern `system_one"\]` alone also matches `cfg["system_one"]` config reads in `pylib/config.py` and several tests, which are unrelated to the limits shape; the `compute_resource_limits\([^)]*\)` alternative is what catches line 274.) If anything else appears, update it in this task the same way.

- [ ] **Step 2: Update the two existing assertions and add new failing tests**

In `tests/test_resources.py`, add `summarize_policy` to the `pylib.resources` import list, then:

- Replace the line `assert with_one["system_one"] == {"cpus": SYSTEM_ONE_CPUS, "memory_mib": SYSTEM_ONE_MEMORY_MIB}` with `assert with_one["system_one"] == DEFAULT_POLICY["system_one"]`.
- Replace `assert compute_resource_limits(16, 32768)["system_one"] == {"cpus": 0, "memory_mib": 0}` with `assert compute_resource_limits(16, 32768)["system_one"] == dict.fromkeys(POLICY_KEYS, 0)`.

Append:

```python
def test_overrides_apply_and_are_reported():
    result = compute_resource_limits(
        16, 32768, llm_server_enabled=False,
        overrides={"omniroute": {"memory_max_mib": 4096}},
    )
    assert result["omniroute"]["memory_max_mib"] == 4096
    assert result["omniroute"]["memory_min_mib"] == OMNIROUTE_MEMORY_MIN_MIB
    assert result["remote_setup"] == _remote_setup_expected()
    assert result["overrides_applied"] == {"omniroute": ["memory_max_mib"]}


def test_override_of_a_disabled_service_is_inert():
    base = compute_resource_limits(16, 32768)
    result = compute_resource_limits(
        16, 32768, overrides={"system_one": {"memory_max_mib": 5120, "memory_min_mib": 3000}}
    )
    assert result["system_one"] == dict.fromkeys(POLICY_KEYS, 0)
    assert result["phoenix"] == dict.fromkeys(POLICY_KEYS, 0)
    assert result["overrides_applied"] == {}
    assert result["totals"] == base["totals"]
    assert result["llm_server"] == base["llm_server"]


def test_raised_minimum_shrinks_the_llm_server_remainder():
    base = compute_resource_limits(16, 32768, memory_ceiling_pct=100)
    raised = compute_resource_limits(
        16, 32768, memory_ceiling_pct=100,
        overrides={"omniroute": {"memory_min_mib": 2048}},
    )
    assert raised["llm_server"]["memory_mib"] == (
        base["llm_server"]["memory_mib"] - (2048 - OMNIROUTE_MEMORY_MIN_MIB)
    )


def test_raised_minimum_on_a_too_small_host_raises_resource_error():
    floor = HOST_MEMORY_FLOOR_MIB + 3072 + REMOTE_SETUP_MEMORY_MIN_MIB
    with pytest.raises(ResourceError, match=f"at least {floor} MiB"):
        compute_resource_limits(
            16, floor - 1, llm_server_enabled=False,
            overrides={"omniroute": {"memory_min_mib": 3072}},
        )


def test_invalid_overrides_raise_resource_error():
    with pytest.raises(ResourceError, match=r"resources\.overrides\.omniroute\.memory_max_mib"):
        compute_resource_limits(16, 32768, overrides={"omniroute": {"memory_max_mib": 0}})


def test_totals_report_hard_caps_and_soft_reservations():
    result = compute_resource_limits(
        16, 65536, llm_server_enabled=False, system_one_enabled=True
    )
    assert result["totals"]["hard_memory_cap_mib"] == 8768
    assert result["totals"]["soft_memory_reservation_mib"] == (
        OMNIROUTE_MEMORY_MIN_MIB + REMOTE_SETUP_MEMORY_MIN_MIB
        + SYSTEM_ONE_MEMORY_MIN_MIB + PHOENIX_MEMORY_MIN_MIB
    )


def test_totals_include_the_llm_server_cap_when_enabled():
    result = compute_resource_limits(16, 32768)
    assert result["totals"]["hard_memory_cap_mib"] == (
        result["llm_server"]["memory_mib"]
        + OMNIROUTE_MEMORY_MAX_MIB + REMOTE_SETUP_MEMORY_MAX_MIB
    )


def test_summarize_policy_names_totals_and_overrides():
    plain = summarize_policy(compute_resource_limits(16, 32768, llm_server_enabled=False))
    assert len(plain) == 1
    assert "hard memory caps total" in plain[0] and "not allocated RAM" in plain[0]
    overridden = summarize_policy(
        compute_resource_limits(
            16, 32768, llm_server_enabled=False,
            overrides={"omniroute": {"memory_max_mib": 4096}},
        )
    )
    assert overridden[1] == "resource overrides applied: omniroute=memory_max_mib"
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_resources.py -q`
Expected: FAIL (`TypeError: ... unexpected keyword argument 'overrides'` and the changed `system_one` shape).

- [ ] **Step 4: Implement**

In `pylib/resources.py`:

1. Replace the whole `_floor_words` function with:

```python
def _floor_words(
    system_one_enabled: bool,
    system_one: Mapping[str, Any],
    phoenix: Mapping[str, Any],
) -> str:
    words = "the conservative host budget and OmniRoute/remote-setup policy minima"
    if system_one_enabled:
        words += (
            f", System One's budgeted minimum ({system_one['cpus_min']} CPU-share unit, "
            f"{system_one['memory_min_mib']} MiB) and Phoenix's budgeted minimum "
            f"({phoenix['cpus_min']} CPU share units, {phoenix['memory_min_mib']} MiB)"
        )
    return words
```

2. Run: `sd -F '_floor_words(system_one_enabled)' '_floor_words(system_one_enabled, system_one, phoenix)' pylib/resources.py`
   then `rg -c '_floor_words\(system_one_enabled, system_one, phoenix\)' pylib/resources.py` (expect `4`).

3. In the `compute_resource_limits` signature add `overrides: Mapping[str, Any] | None = None,` after `system_one_enabled: bool = False,`. Replace the block from `cpu_floor = HOST_CPU_FLOOR + OMNIROUTE_CPU_MIN + REMOTE_SETUP_CPU_MIN` through `memory_floor_mib += PHOENIX_MEMORY_MIN_MIB` with:

```python
    override_errors = validate_overrides(overrides)
    if override_errors:
        raise ResourceError("; ".join(override_errors))
    omniroute = effective_policy("omniroute", overrides)
    remote_setup = effective_policy("remote_setup", overrides)
    system_one = effective_policy("system_one", overrides)
    phoenix = effective_policy("phoenix", overrides)
    cpu_floor = HOST_CPU_FLOOR + omniroute["cpus_min"] + remote_setup["cpus_min"]
    memory_floor_mib = (
        HOST_MEMORY_FLOOR_MIB
        + omniroute["memory_min_mib"]
        + remote_setup["memory_min_mib"]
    )
    if system_one_enabled:
        cpu_floor += system_one["cpus_min"]
        memory_floor_mib += system_one["memory_min_mib"]
        # Phoenix shares the System One enable flag: it is charged whenever
        # System One is on, including the llm-server-disabled gateway mode.
        cpu_floor += phoenix["cpus_min"]
        memory_floor_mib += phoenix["memory_min_mib"]
```

4. Replace the final `return {...}` block of `compute_resource_limits` (from `    return {` with `"host_cpu_floor": HOST_CPU_FLOOR,` to the end of the function) with:

```python
    system_one_policy = (
        system_one if system_one_enabled else dict.fromkeys(POLICY_KEYS, 0)
    )
    phoenix_policy = phoenix if system_one_enabled else dict.fromkeys(POLICY_KEYS, 0)
    active = ["omniroute", "remote_setup"] + (
        ["system_one", "phoenix"] if system_one_enabled else []
    )
    overrides_applied = {
        service: sorted(key for key in overrides[service] if key in POLICY_KEYS)
        for service in active
        if overrides and overrides.get(service)
    }
    policies = [omniroute, remote_setup, system_one_policy, phoenix_policy]
    return {
        "host_cpu_floor": HOST_CPU_FLOOR,
        "host_memory_floor_mib": HOST_MEMORY_FLOOR_MIB,
        "omniroute": omniroute,
        "remote_setup": remote_setup,
        "llm_server": {
            "cpus": llm_server_cpus,
            "memory_mib": llm_server_memory_mib,
        },
        "system_one": system_one_policy,
        "phoenix": phoenix_policy,
        "overrides_applied": overrides_applied,
        "totals": {
            "hard_memory_cap_mib": llm_server_memory_mib
            + sum(policy["memory_max_mib"] for policy in policies),
            "soft_memory_reservation_mib": sum(
                policy["memory_min_mib"] for policy in policies
            ),
        },
    }


def summarize_policy(limits: Mapping[str, Any]) -> list[str]:
    totals = limits["totals"]
    lines = [
        (
            f"hard memory caps total {totals['hard_memory_cap_mib']} MiB; "
            f"soft reservations (memory.low, not allocated RAM) total "
            f"{totals['soft_memory_reservation_mib']} MiB"
        )
    ]
    applied = limits["overrides_applied"]
    if applied:
        lines.append(
            "resource overrides applied: "
            + "; ".join(f"{service}={','.join(keys)}" for service, keys in applied.items())
        )
    return lines
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_resources.py tests/test_cli.py -q -k "resource or phoenix or budget"`
Expected: PASS (the existing Phoenix and System One tests still hold because the values are unchanged).

- [ ] **Step 6: Validate and commit**

Run: `make validate && make test`
Expected: both succeed.

```bash
git add pylib/resources.py tests/test_resources.py
git commit -m "feat(resources): apply per-service overrides to the host budget and report totals"
```

---

### Task 3: Config validation and migration

**Files:**
- Modify: `pylib/config.py` (import block ~line 14-27; `validate_config` after the `phoenix` validation ~line 400)
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: `validate_overrides` from Task 1.
- Produces: `validate_config` returns structural `resources.overrides...` errors (including "a lowered cap needs a memory_max_evidence reference"); `migrate_config` neither creates nor alters `resources.overrides`. Task 8 adds the file-level verification of evidence references.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config.py`:

```python
def test_migrate_config_never_creates_resource_overrides():
    assert "overrides" not in migrate_config(make_cfg())["resources"]


def test_migrate_config_preserves_resource_overrides_untouched():
    overrides = {"omniroute": {"memory_max_mib": 4096}}
    cfg = make_cfg(resources={"overrides": overrides})
    assert migrate_config(cfg)["resources"]["overrides"] == overrides


def test_config_accepts_valid_resource_overrides():
    cfg = make_cfg()
    cfg["resources"]["overrides"] = {"omniroute": {"memory_max_mib": 4096}}
    assert validate_config(cfg) == []


@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        ({"omniroute": {"memory_max_mib": 0}}, "resources.overrides.omniroute.memory_max_mib"),
        ({"omniroute": {"memory_max": 2048}}, "resources.overrides.omniroute.memory_max is not"),
        ({"nginx": {"memory_max_mib": 64}}, "resources.overrides.nginx is not an overridable service"),
        ({"omniroute": {"memory_max_mib": 512}}, "effective memory_min_mib"),
        ({"omniroute": {"memory_max_mib": 2048}}, "needs a memory_max_evidence reference"),
        ([], "resources.overrides must be a mapping"),
    ],
)
def test_config_rejects_invalid_resource_overrides(overrides, fragment):
    cfg = make_cfg()
    cfg["resources"]["overrides"] = overrides
    assert any(fragment in error for error in validate_config(cfg))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_config.py -q -k resource_overrides`
Expected: `test_config_rejects_invalid_resource_overrides` cases FAIL (no errors produced); the accept/migrate tests pass already. `pytest` is already imported in this file; if `rg -n '^import pytest' tests/test_config.py` finds nothing, add `import pytest`.

- [ ] **Step 3: Implement**

In `pylib/config.py` add `validate_overrides` as the last name of the `from pylib.resources import (...)` list (after `REMOTE_SETUP_MEMORY_MIN_MIB,`). In `validate_config`, directly after `errors.extend(_validate_min_max_resources(resources, "phoenix"))` add:

```python
            errors.extend(validate_overrides(resources.get("overrides")))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_config.py -q`
Expected: PASS.

- [ ] **Step 5: Validate and commit**

Run: `make validate && make test`
Expected: both succeed.

```bash
git add pylib/config.py tests/test_config.py
git commit -m "feat(config): validate persistent resource overrides"
```

---

### Task 4: One limits helper for budget and resources, summary output

**Files:**
- Modify: `llmenv.py` (`cmd_budget` ~lines 330-345 and ~375-385; `cmd_resources` ~419-433; import of `pylib.resources` at line 87)
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `compute_resource_limits(..., overrides=...)`, `summarize_policy` from Task 2.
- Produces: `limits_for(cfg: dict[str, Any], host: dict[str, Any], *, memory_ceiling_pct: float | None = None) -> dict[str, Any]` in `llmenv.py`; `llmenv resources` JSON gains `summary: list[str]` (plus the Task 2 keys).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli.py`:

```python
def _resources_cfg(tmp_path, **overrides):
    from pylib.config import migrate_config

    cfg = migrate_config(yaml.safe_load(write_test_config(tmp_path).read_text()))
    cfg["resources"]["llm_server"]["memory_ceiling_pct"] = 100
    if overrides:
        cfg["resources"]["overrides"] = overrides
    return cfg


def test_cmd_resources_applies_overrides_and_prints_a_summary(tmp_path, monkeypatch, capsys):
    import llmenv

    cfg = _resources_cfg(tmp_path, omniroute={"memory_max_mib": 4096})
    monkeypatch.setattr(llmenv, "load_config", lambda path: cfg)
    monkeypatch.setattr(
        llmenv, "host_resources", lambda: {"cpu_count": 16, "memory_total_mib": 32768}
    )

    assert llmenv.cmd_resources(cast(Namespace, SimpleNamespace(config="cfg"))) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["omniroute"]["memory_max_mib"] == 4096
    assert payload["overrides_applied"] == {"omniroute": ["memory_max_mib"]}
    assert any("resource overrides applied: omniroute=memory_max_mib" in line for line in payload["summary"])


def test_cmd_budget_ram_check_sees_a_raised_minimum(tmp_path, monkeypatch, capsys):
    import llmenv

    facts = {
        "compositor_render_node": "/dev/dri/renderD128",
        "gpus": [{
            "pci_address": "0000:03:00.0", "render_node": "/dev/dri/renderD129",
            "vram_total_mib": 16304, "vram_used_mib": 200,
        }],
    }
    available = []
    for extra in ({}, {"omniroute": {"memory_min_mib": 2048}}):
        cfg = _resources_cfg(tmp_path, **extra)
        monkeypatch.setattr(llmenv, "load_config", lambda path, cfg=cfg: cfg)
        monkeypatch.setattr(llmenv, "detect", lambda: facts)
        monkeypatch.setattr(
            llmenv, "host_resources", lambda: {"cpu_count": 24, "memory_total_mib": 32768}
        )
        monkeypatch.setattr(
            llmenv, "_model_costs",
            lambda config, path: [{"alias": "a", "weights_mib": 100, "ram_weights_mib": 0, "kv_mib": 10}],
        )
        llmenv.cmd_budget(cast(Namespace, SimpleNamespace(config="cfg", models_dir="models")))
        available.append(json.loads(capsys.readouterr().out)["ram_available_mib"])

    assert available[0] - available[1] == 2048 - 1024
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -q -k "applies_overrides or raised_minimum"`
Expected: FAIL (`KeyError: 'summary'` / equal availability).

- [ ] **Step 3: Implement**

In `llmenv.py` change the import to `from pylib.resources import ResourceError, compute_resource_limits, summarize_policy`. Add above `cmd_budget` (near the other helpers, after `fail`):

```python
def limits_for(
    cfg: dict[str, Any],
    host: dict[str, Any],
    *,
    memory_ceiling_pct: float | None = None,
) -> dict[str, Any]:
    """The one place llmenv turns config + host facts into resource limits."""
    resources = cfg.get("resources", {})
    llm_server = resources.get("llm_server", {})
    return compute_resource_limits(
        host["cpu_count"],
        host["memory_total_mib"],
        llm_server.get("memory_ceiling_pct", 46)
        if memory_ceiling_pct is None
        else memory_ceiling_pct,
        llm_server.get("memory_ceiling_floor_pct", 30),
        llm_server.get("cpu_ceiling_pct", 60),
        llm_server.get("cpu_ceiling_floor_pct", 20),
        llm_server_enabled=cfg.get("llm_server", {}).get("enabled", True),
        system_one_enabled=cfg.get("system_one", {}).get("enabled", False),
        overrides=resources.get("overrides"),
    )
```

Replace the first call in `cmd_budget` (the `resource_limits = compute_resource_limits(...)` statement inside the `try:`) with `resource_limits = limits_for(cfg, host)`. Replace the second call (`absolute_max_limits = compute_resource_limits(...)`) with `absolute_max_limits = limits_for(cfg, host, memory_ceiling_pct=100)`. Replace the body of `cmd_resources` after `host = host_resources()` with:

```python
    limits = limits_for(cfg, host)
    return emit({"host": host, **limits, "summary": summarize_policy(limits)})
```

(delete the now-unused `llm_server_resources` / `llm_server_enabled` locals in `cmd_resources`; keep them in `cmd_budget` only where still referenced. `llm_server_resources` in `cmd_budget` becomes unused after both calls change; remove it too.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -q`
Expected: PASS, including the pre-existing `test_resources_*` and budget tests.

- [ ] **Step 5: Validate and commit**

Run: `make validate && make test`
Expected: both succeed (ruff flags any leftover unused local).

```bash
git add llmenv.py tests/test_cli.py
git commit -m "feat(llmenv): route budget and resources through one override-aware helper"
```

---

### Task 5: Every render path derives limits from the effective policy

`scripts/start.sh` reaches Compose through `setup/render-unit.sh`, and so does `setup/enable-boot.sh`; the boot unit then only runs `podman compose ... up -d` on that rendered file. So the single place that must be correct is `llmenv render-compose` (plus Phoenix, which has its own compose project). After this task nothing that renders a container limit reads the persisted `resources.<service>` mirror.

**Files:**
- Modify: `pylib/compose.py` (import block lines 19-25; `render_compose` signature and limit lookups ~lines 61-77; the `_apply_resource_limits(system_one_service, {...})` call ~line 296; `write_compose` signature ~line 326 and its `render_compose(...)` call)
- Modify: `llmenv.py` (`cmd_render_compose` ~line 494; new `cmd_effective_policy`; parser entries near `render_compose` ~line 729; import of `pylib.resources`)
- Modify: `scripts/phoenix.sh` (`load_resource_env` ~lines 98-115; usage list line 44-45; new `resource-env` action next to `start)`), `scripts/check-setup.sh` (`phoenix_offline_check`, the `yq -o=json '.resources.phoenix ...'` line ~250)
- Modify: `setup/render-unit.sh` (the `llmenv render-compose` call ~line 81: show the real error instead of discarding it, see Step 3)
- Modify (tests): `tests/test_shell.py` (`run_lifecycle_script`'s `uv` stub), `tests/test_compose.py` (every direct `render_compose(`/`write_compose(` call: **9 calls** at lines ~55 `compose_dict`, ~269, ~278, ~303, ~323, ~333 (the `write_compose` tests), ~389, ~549 `system_one_document`, ~619), `tests/test_omniroute_characterize.py:305` (`pylib_render`, **1 call**; 10 calls in all, confirm with `rg -n 'render_compose\(|write_compose\(' tests`), `tests/test_phoenix_operations.py` (the harness around lines 21-62 that extracts `phoenix_offline_check`), `tests/test_shell.py` (`run_render_unit_with_legacy_rocm_config` ~line 3951)
- Test: `tests/test_compose.py`, `tests/test_cli.py`, `tests/test_shell.py`, `tests/test_phoenix_operations.py`, `tests/resource_fixtures.py`

**Interfaces:**
- Consumes: `effective_policy`, `OVERRIDABLE_SERVICES` (Task 1); `limits_for` (Task 4).
- Produces:
  - `render_compose(cfg, *, models_dir, presets_path, repo_root, limits: Mapping[str, Mapping[str, Any]], omni_router_master_key="", system_one_rocr_index="") -> str` and the same `limits` keyword on `write_compose`. `limits` is required and has the keys `llm_server` (`{cpus, memory_mib}`), `omniroute`, `remote_setup`, `system_one` (each `{cpus_min, cpus_max, memory_min_mib, memory_max_mib}`), i.e. the output shape of `compute_resource_limits`.
  - `uv run llmenv.py --config PATH effective-policy --service <omniroute|remote_setup|system_one|phoenix>` prints `effective_policy(...)` as JSON (host-independent; does not depend on whether the service is enabled).
  - `bash scripts/phoenix.sh resource-env` prints `PHOENIX_CPU_SHARES=...`, `PHOENIX_CPUS_MAX=...`, `PHOENIX_MEMORY_MIN_MIB=...`, `PHOENIX_MEMORY_MAX_MIB=...` exactly as `start` would hand them to Compose.
  - `tests/resource_fixtures.py: limits_of(cfg) -> dict` (test helper: builds `limits` from a test cfg's persisted copies plus `effective_policy("system_one", ...)`, so existing compose tests keep exercising "zero means no limit").

- [ ] **Step 1: Write the failing tests**

Append to `tests/resource_fixtures.py`:

```python
def limits_of(cfg):
    """`limits` for render_compose built from a test cfg (persisted copies as test input)."""
    from pylib.resources import effective_policy

    resources = cfg.get("resources", {})
    return {
        "llm_server": resources.get("llm_server", {}),
        "omniroute": resources.get("omniroute", {}),
        "remote_setup": resources.get("remote_setup", {}),
        "system_one": effective_policy("system_one", resources.get("overrides")),
    }
```

In `tests/test_compose.py` add `from resource_fixtures import limits_of` and `from pylib.resources import effective_policy`, then add `limits=limits_of(cfg)` (or `limits_of(CFG)` where the call passes `CFG` or a dict derived from it) to every direct `render_compose(...)` / `write_compose(...)` call found by the `rg` above; do the same with `limits=limits_of(cfg)` in `tests/test_omniroute_characterize.py:pylib_render`, importing `from resource_fixtures import limits_of` inside that function next to its existing local import. Rename nothing else. Then append:

```python
def test_system_one_limits_come_from_the_limits_argument():
    cfg = {**CFG, "system_one": SYSTEM_ONE}
    limits = {
        **limits_of(cfg),
        "system_one": effective_policy(
            "system_one", {"system_one": {"memory_max_mib": 5120, "cpus_max": 1.5}}
        ),
    }
    text = render_compose(
        cfg, models_dir="/m", presets_path="/p", repo_root="/r",
        system_one_rocr_index="0", limits=limits,
    )
    service = yaml.safe_load(text)["services"]["system-one"]
    assert service["mem_limit"] == "5120m"
    assert service["cpus"] == 1.5
    assert service["mem_reservation"] == "2560m"
    assert service["cpu_shares"] == 1024


def test_render_compose_ignores_the_persisted_resource_mirror():
    cfg = {
        **CFG,
        "resources": {
            **CFG["resources"],
            "omniroute": {"cpus_min": 1, "cpus_max": 4, "memory_min_mib": 512, "memory_max_mib": 999},
        },
    }
    limits = {**limits_of(CFG), "omniroute": effective_policy("omniroute", None)}
    text = render_compose(
        cfg, models_dir="/m", presets_path="/p", repo_root="/r", limits=limits
    )
    assert yaml.safe_load(text)["services"]["omniroute"]["mem_limit"] == "3072m"
```

In `tests/test_cli.py` append (it reuses `_resources_cfg` from Task 4; add `from resource_fixtures import ...` only if you use the fixtures module here, which these tests do not):

```python
def _render_args(tmp_path):
    return cast(Namespace, SimpleNamespace(
        config="cfg", models_dir="/m", presets_path="/p", repo_root="/r",
        env_file=None, output=str(tmp_path / "compose.yml"), system_one_rocr_index="",
    ))


def _render_with(tmp_path, monkeypatch, **overrides):
    import llmenv

    cfg = _resources_cfg(tmp_path, **overrides)
    # Stale generated mirror: must not reach the rendered file.
    cfg["resources"]["omniroute"] = {"cpus_min": 1, "cpus_max": 4, "memory_min_mib": 512, "memory_max_mib": 999}
    cfg["resources"]["remote_setup"] = {"cpus_min": 1, "cpus_max": 1, "memory_min_mib": 16, "memory_max_mib": 17}
    cfg["resources"]["llm_server"].update({"cpus": 1, "memory_mib": 1})
    host = {"cpu_count": 16, "memory_total_mib": 32768}
    monkeypatch.setattr(llmenv, "load_config", lambda path: cfg)
    monkeypatch.setattr(llmenv, "host_resources", lambda: host)
    assert llmenv.cmd_render_compose(_render_args(tmp_path)) == 0
    services = yaml.safe_load((tmp_path / "compose.yml").read_text())["services"]
    return services, llmenv.limits_for(cfg, host)


def test_render_compose_follows_overrides_not_the_stale_mirror(tmp_path, monkeypatch):
    services, limits = _render_with(tmp_path, monkeypatch, omniroute={"memory_max_mib": 4096})
    assert services["omniroute"]["mem_limit"] == "4096m"
    assert services["remote-setup"]["mem_limit"] == "64m"
    assert services["llm-server"]["mem_limit"] == f"{limits['llm_server']['memory_mib']}m"


def test_render_compose_after_clear_restores_defaults_despite_the_stale_mirror(tmp_path, monkeypatch):
    services, limits = _render_with(tmp_path, monkeypatch)
    assert services["omniroute"]["mem_limit"] == "3072m"
    assert services["remote-setup"]["mem_limit"] == "64m"
    assert limits["llm_server"]["memory_mib"] > 1
    assert services["llm-server"]["mem_limit"] == f"{limits['llm_server']['memory_mib']}m"


def test_effective_policy_command_returns_the_merged_policy(tmp_path, monkeypatch, capsys):
    import llmenv

    cfg = _resources_cfg(tmp_path, phoenix={"cpus_max": 1.5, "memory_max_mib": 1536})
    monkeypatch.setattr(llmenv, "load_config", lambda path: cfg)
    code = llmenv.cmd_effective_policy(cast(Namespace, SimpleNamespace(config="cfg", service="phoenix")))
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload == {"cpus_min": 0.25, "cpus_max": 1.5, "memory_min_mib": 256, "memory_max_mib": 1536}


def test_effective_policy_command_is_wired(tmp_path):
    config = write_test_config(tmp_path)
    shown = run("effective-policy", "--config", str(config), "--service", "omniroute")
    assert shown.returncode == 0, shown.stderr
    assert json.loads(shown.stdout)["memory_max_mib"] == 3072
    assert run("effective-policy", "--config", str(config), "--service", "nginx").returncode == 2


def test_render_compose_on_a_host_below_the_resource_floor_fails_with_a_clear_error(
    tmp_path, monkeypatch, capsys
):
    """render-compose now reads the host (limits_for); ResourceError/DetectError must surface, not crash."""
    import llmenv

    cfg = _resources_cfg(tmp_path)
    monkeypatch.setattr(llmenv, "load_config", lambda path: cfg)
    monkeypatch.setattr(llmenv, "host_resources", lambda: {"cpu_count": 1, "memory_total_mib": 512})
    output = tmp_path / "compose.yml"
    code = llmenv.main([
        "--config", "cfg", "render-compose", "--models-dir", "/m",
        "--presets-path", "/p", "--repo-root", "/r", "--output", str(output),
    ])
    assert code == 1  # `main` converts ResourceError into {"error": ...}, exit 1
    assert "host has" in json.loads(capsys.readouterr().out)["error"]
    assert not output.exists()
```

Failure surface: `cmd_render_compose` itself only catches `ValueError`; `ResourceError` (and `DetectError` when `/proc` is unreadable) propagate to `main`, whose exception tuple already catches both and prints `{"error": ...}` with exit 1. The new dependency on `host_resources()` therefore fails `render-compose` on a host below the resource floor, which means `bash setup/render-unit.sh`, `make enable-boot` and `make start` fail at render time, never at boot itself (boot only runs `podman compose ... up -d` on the last rendered file). `setup/render-unit.sh` currently discards the renderer's stdout (`>/dev/null`) and so would exit silently under `set -e`; Step 3 fixes that. The three existing subprocess tests `test_render_compose_writes_a_compose_file`, `..._reads_the_master_key_from_env_file` and `..._defaults_to_empty_master_key_when_env_file_missing` (`tests/test_cli.py` ~2211-2265) call the real CLI and so now also read the real host; like the existing `test_resources_*` subprocess tests they need a host above the budget floor (any development machine) and need no edit. The real-render shell tests (`run_render_unit_with_legacy_rocm_config` and every `test_render_unit_*` that uses it) run `render-compose` through the real `uv` and `llmenv.py` the same way and are likewise unchanged; their config has no `resources:` key and `limits_for` defaults every service.

In `tests/test_shell.py` make the render-unit proof reusable. Run `rg -n 'def run_render_unit_with_legacy_rocm_config' -A60 tests/test_shell.py` and change it as follows: add a parameter `extra_config: str = ""` after `llm_server_enabled`; append `+ extra_config` to the end of the inline config text (the string ends right before the closing `)` of `config.write_text(`; the helper's config has no `resources:` key, so appended YAML is a new top-level key). Then add, directly after the function:

```python
def rerun_render_unit(tmp_path: pathlib.Path) -> subprocess.CompletedProcess[str]:
    """Run setup/render-unit.sh again on the config/home the helper above prepared."""
    return subprocess.run(
        ["/usr/bin/bash", "setup/render-unit.sh"],
        cwd=ROOT,
        env=os.environ | {
            "HOME": str(tmp_path / "home"),
            "LLM_ENV_CONFIG": str(tmp_path / "models.yml"),
            "LLM_ENV_COMPOSE_INSPECT_DIR": str(tmp_path / "compose-inspect"),
            "PATH": f"{tmp_path / 'bin'}:/usr/bin:/bin",
            "REAL_UV": shutil.which("uv") or "",
            "REAL_YQ": shutil.which("yq") or "",
        },
        text=True,
        capture_output=True,
        check=False,
    )


def _rendered_omniroute_limit(tmp_path: pathlib.Path) -> str:
    import yaml

    document = yaml.safe_load((tmp_path / "compose-inspect/docker-compose.yml").read_text())
    return document["services"]["omniroute"]["mem_limit"]
```

(`os` is already imported in `tests/test_shell.py`.) Append the boot-path test that needs no new CLI:

```python
def test_render_unit_ignores_a_stale_resource_mirror(tmp_path: pathlib.Path) -> None:
    """enable-boot runs render-unit.sh; the unit then starts the file it renders."""
    stale = "resources:\n  omniroute: {cpus_min: 1, cpus_max: 4, memory_min_mib: 512, memory_max_mib: 999}\n"
    result, _unit = run_render_unit_with_legacy_rocm_config(tmp_path, extra_config=stale)
    assert result.returncode == 0, result.stderr
    assert _rendered_omniroute_limit(tmp_path) == "3072m"  # the mirror (999) is ignored

    # The first run re-serialized models.yml (migrate), so edit it with yq.
    subprocess.run(
        ["yq", "-i", ".resources.overrides.omniroute.memory_max_mib = 4096", str(tmp_path / "models.yml")],
        check=True,
    )
    assert rerun_render_unit(tmp_path).returncode == 0
    assert _rendered_omniroute_limit(tmp_path) == "4096m"


def test_render_unit_shows_the_renderers_resource_error(tmp_path: pathlib.Path) -> None:
    """An override that cannot fit this host must fail loudly, not as a silent exit 1."""
    too_big = "resources:\n  overrides:\n    omniroute: {memory_min_mib: 8000000, memory_max_mib: 8000000}\n"
    result, _unit = run_render_unit_with_legacy_rocm_config(tmp_path, extra_config=too_big)
    assert result.returncode != 0
    assert "render-compose failed" in result.stdout + result.stderr
    assert "MiB is required to reserve" in result.stdout + result.stderr
    assert not (tmp_path / "compose-inspect/docker-compose.yml").exists()
```

Task 9 Step 1 adds the same lifecycle proof driven by the real `resource-override set` and `clear` commands.

Phoenix tests. Append to `tests/test_shell.py`:

```python
def _phoenix_resource_env(tmp_path: pathlib.Path, extra_config: str) -> subprocess.CompletedProcess[str]:
    config = tmp_path / "models.yml"
    config.write_text(
        "version: 1\n"
        "server: {host: 0.0.0.0, port: 8000, api_key: k, mdns_name: llm, sleep_idle_seconds: 300}\n"
        "gpu: {pci_address: '0000:03:00.0', device_name: d, backend: vulkan, image: i,"
        " vram_total_mib: 16304, reserve_mode: auto, reserve_floor_mib: 1024}\n"
        "runtime: {models_max: 1, parallel_slots: 1, ubatch_size: 512, flash_attn: true,"
        " cache_type_k: q8_0, cache_type_v: q8_0}\n"
        "models:\n"
        "  - {alias: a, label: A, parameters: 1B, quantization: Q4_K_M, enabled: true, file: a.gguf,"
        " url: u, size_bytes: 1, vram_budget: 10%, ctx_size: 4096, client_max_output_tokens: 4096,"
        " n_gpu_layers: 99}\n"
        + extra_config
    )
    return subprocess.run(
        ["/usr/bin/bash", "scripts/phoenix.sh", "resource-env"], cwd=ROOT,
        env=os.environ | {"LLM_ENV_CONFIG": str(config), "HOME": str(tmp_path / "home"),
                          "XDG_STATE_HOME": str(tmp_path / "state")},
        text=True, capture_output=True, check=False,
    )


def test_phoenix_resource_env_uses_the_effective_policy(tmp_path: pathlib.Path) -> None:
    result = _phoenix_resource_env(
        tmp_path, "resources:\n  overrides:\n    phoenix: {cpus_max: 1.5, memory_max_mib: 1536}\n"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    lines = dict(line.split("=", 1) for line in result.stdout.split() if "=" in line)
    assert lines["PHOENIX_CPUS_MAX"] == "1.5"
    assert lines["PHOENIX_MEMORY_MAX_MIB"] == "1536"
    assert lines["PHOENIX_MEMORY_MIN_MIB"] == "256"
    assert lines["PHOENIX_CPU_SHARES"] == "256"


def test_phoenix_resource_env_ignores_a_stale_mirror(tmp_path: pathlib.Path) -> None:
    result = _phoenix_resource_env(
        tmp_path, "resources:\n  phoenix: {cpus_min: 0.5, cpus_max: 3, memory_min_mib: 512, memory_max_mib: 4096}\n"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PHOENIX_MEMORY_MAX_MIB=1024" in result.stdout
    assert "PHOENIX_CPUS_MAX=2" in result.stdout


def test_phoenix_start_and_resource_env_share_one_loader() -> None:
    script = (ROOT / "scripts/phoenix.sh").read_text()
    assert script.count("load_resource_env") >= 3  # definition, start, resource-env
    assert "yq -r '.resources.phoenix" not in script
```

Adapt `_phoenix_resource_env` to the real config fixtures if `validate_config` rejects the inline config (`uv run pytest ... -x` shows the missing key; reuse `write_test_config` from `tests/test_cli.py` instead by copying its text). In `tests/test_phoenix_operations.py`, `offline_check()` extracts `phoenix_offline_check()` from `scripts/check-setup.sh` and runs it in bare bash, where `llmenv` does not exist and the minimal config would not pass `validate_config`. Replace the `resources.phoenix` fixture by a stand-in for the new command's JSON:

```python
    config.write_text(yaml.safe_dump({"system_one": {"image": "localhost/fixture-base:rocm"}}))
    # The offline check asks `llmenv effective-policy`; the fixture stands in for its JSON.
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps({"cpus_min": 0.25,
                                  "cpus_max": 0 if mode == "invalid-resource-policy" else 2,
                                  "memory_min_mib": 256, "memory_max_mib": 1024}))
```

and define the function in the bash prelude and pass the file through the environment: `["bash", "-c", 'llmenv() { cat "$POLICY_JSON"; }\n' + definition + "\nphoenix_offline_check\n"]` with `"POLICY_JSON": str(policy)` added to the `env=os.environ | {...}` dict. The `invalid-resource-policy` case keeps failing for the same reason as before (the check's own `jq` bounds validation).

`scripts/phoenix.sh start` now calls `llmenv effective-policy`, and the System One `start.sh` tests reach it through the `uv` stub in `run_lifecycle_script` (without this line `test_start_waits_for_system_one_health_when_image_present` fails with `jq: invalid JSON text passed to --argjson`). In that stub, directly after the line `f"case \"$*\" in *' resources') {resources_case} ;; esac\n"`, add:

```python
        "case \"$*\" in *' effective-policy'*) printf '%s\\n' '{\"cpus_min\": 0.25, \"cpus_max\": 2, \"memory_min_mib\": 256, \"memory_max_mib\": 1024}' ;; esac\n"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_compose.py tests/test_cli.py tests/test_shell.py tests/test_phoenix_operations.py -q -x -k "limits_argument or persisted_resource_mirror or stale or effective_policy or phoenix_resource_env or shared_one_loader or one_loader"`
Expected: FAIL (`TypeError: render_compose() got an unexpected keyword argument 'limits'` / `AttributeError: ... cmd_effective_policy` / `phoenix.sh: usage`).

- [ ] **Step 3: Implement**

`pylib/compose.py`:

1. Replace the `from pylib.resources import (...)` block with `from pylib.resources import OMNIROUTE_CHAT_ADMISSION_QUEUE_MS` (the `SYSTEM_ONE_*` imports become unused) and add `Mapping` to the typing/collections imports (`from collections.abc import Mapping`).
2. Add `limits: Mapping[str, Mapping[str, Any]],` to the keyword-only parameters of `render_compose` (after `repo_root: str,`) and of `write_compose`; pass `limits=limits,` in `write_compose`'s `render_compose(...)` call.
3. Replace

```python
    llm_server_resources = cfg.get("resources", {}).get("llm_server", {})
    omniroute_cfg = cfg.get("omniroute", {})
    omniroute_resources = cfg.get("resources", {}).get("omniroute", {})
    remote_setup_resources = cfg.get("resources", {}).get("remote_setup", {})
```
with
```python
    # Limits come from the caller (`llmenv render-compose` derives them from
    # the effective policy for THIS host on every render). resources.<service>
    # in models.yml is a generated mirror and is deliberately never read here.
    llm_server_resources = limits["llm_server"]
    omniroute_cfg = cfg.get("omniroute", {})
    omniroute_resources = limits["omniroute"]
    remote_setup_resources = limits["remote_setup"]
```
4. Replace the whole `_apply_resource_limits(system_one_service, {...},)` call with `_apply_resource_limits(system_one_service, limits["system_one"])`. `_apply_resource_limits` takes `dict[str, Any]`; widen its annotation to `Mapping[str, Any]` (it only reads).

`llmenv.py`: change the resources import to include `OVERRIDABLE_SERVICES, effective_policy` (`from pylib.resources import OVERRIDABLE_SERVICES, ResourceError, compute_resource_limits, effective_policy, summarize_policy`; Task 9 extends it again). Replace `cmd_render_compose` with:

```python
def cmd_render_compose(args: argparse.Namespace) -> int:
    cfg = require_valid_config(load_config(Path(args.config)))
    env_vars = read_env_file(Path(args.env_file)) if args.env_file else {}
    # Recomputed on every render from the effective policy and THIS host's
    # facts: start.sh, render-unit.sh and enable-boot all land here, so none of
    # them can ship a stale persisted resources.<service> mirror.
    limits = limits_for(cfg, host_resources())
    try:
        write_compose(
            cfg,
            models_dir=args.models_dir,
            presets_path=args.presets_path,
            repo_root=args.repo_root,
            limits=limits,
            omni_router_master_key=env_vars.get("OMNI_ROUTER_MASTER_KEY", ""),
            path=Path(args.output),
            system_one_rocr_index=args.system_one_rocr_index,
        )
    except ValueError as error:
        return fail(str(error))
    return emit({"written": str(args.output)})


def cmd_effective_policy(args: argparse.Namespace) -> int:
    cfg = require_valid_config(load_config(Path(args.config)))
    return emit(effective_policy(args.service, cfg["resources"].get("overrides")))
```

and, after the `render_compose.set_defaults(func=cmd_render_compose)` line, add:

```python
    effective_parser = sub.add_parser("effective-policy")
    effective_parser.add_argument("--config", default=argparse.SUPPRESS)
    effective_parser.add_argument("--service", required=True, choices=OVERRIDABLE_SERVICES)
    effective_parser.set_defaults(func=cmd_effective_policy)
```

`scripts/phoenix.sh`: replace the `load_resource_env` function (and its comment) with:

```bash
# Export the resource environment for the compose render. The policy is the
# effective one (pylib/resources.py defaults + resources.overrides.phoenix)
# resolved by llmenv on every call, so `start` -- including the boot-time
# ExecStartPost -- never uses a stale persisted resources.phoenix mirror.
load_resource_env() {
    local policy cpus_min
    [[ -f "$CONFIG_PATH" ]] || die "no config at ${CONFIG_PATH}; run 'make setup' first"
    policy="$(llmenv --config "$CONFIG_PATH" effective-policy --service phoenix)" \
        || die "cannot resolve the Phoenix resource policy: $(jq -r '.error // "unknown error"' <<<"$policy")"
    cpus_min="$(jq -r '.cpus_min' <<<"$policy")"
    PHOENIX_CPUS_MAX="$(jq -r '.cpus_max' <<<"$policy")"
    PHOENIX_MEMORY_MIN_MIB="$(jq -r '.memory_min_mib' <<<"$policy")"
    PHOENIX_MEMORY_MAX_MIB="$(jq -r '.memory_max_mib' <<<"$policy")"
    # cpu_shares is a RELATIVE WEIGHT (baseline 1024 per share unit), not
    # guaranteed cores; the hard caps are PHOENIX_CPUS_MAX/PHOENIX_MEMORY_MAX_MIB.
    PHOENIX_CPU_SHARES="$(jq -n --argjson c "$cpus_min" '($c * 1024 | round) as $shares | if $shares < 2 then 2 else $shares end')"
    export PHOENIX_CPU_SHARES PHOENIX_CPUS_MAX PHOENIX_MEMORY_MIN_MIB PHOENIX_MEMORY_MAX_MIB
}
```

Add `resource-env` to the accepted-action `case` (line ~44: `start|stop|status|endpoint|prepare|seed-synthetic|verify-synthetic|resource-env) ;;`) and to the usage message, add `uv` to the `require_cmd` line of the `start)` branch (`require_cmd podman podman-compose ss curl jq yq flock realpath uv`), and add this branch directly before `stop)`:

```bash
    resource-env)
        # Read-only: print the resource environment `start` hands to Compose.
        require_cmd uv jq
        load_resource_env
        printf 'PHOENIX_CPU_SHARES=%s\nPHOENIX_CPUS_MAX=%s\nPHOENIX_MEMORY_MIN_MIB=%s\nPHOENIX_MEMORY_MAX_MIB=%s\n' \
            "$PHOENIX_CPU_SHARES" "$PHOENIX_CPUS_MAX" "$PHOENIX_MEMORY_MIN_MIB" "$PHOENIX_MEMORY_MAX_MIB"
        ;;
```

`setup/render-unit.sh`: replace the `llmenv --config "$CONFIG_PATH" render-compose ... --output "$COMPOSE_FILE" >/dev/null` statement (keep its other arguments exactly as they are) with

```bash
render_output="$(llmenv --config "$CONFIG_PATH" render-compose \
    --models-dir "$MODELS_DIR" --presets-path "$presets_path" \
    --repo-root "$REPO_DIR" --env-file "${REPO_DIR}/.env" \
    --system-one-rocr-index "$system_one_rocr_index" \
    --output "$COMPOSE_FILE")" \
    || die "render-compose failed: $(jq -r '.error // "unknown error"' <<<"$render_output" 2>/dev/null || printf '%s' "$render_output")"
```

so the host-dependent renderer's `{"error": ...}` (for example "host has N MiB RAM; more than M MiB is required to reserve ...") reaches the operator instead of being thrown away by `>/dev/null`. The stdout of a successful render is still unused. `die` and `jq` are already available in this script (`tools/lib.sh`, `require_cmd ... jq`).

`scripts/check-setup.sh` `phoenix_offline_check`: replace the line `yq -o=json '.resources.phoenix // {"cpus_min": 0.25, ...}' "$CONFIG_PATH" |` with `llmenv --config "$CONFIG_PATH" effective-policy --service phoenix |` (keep the following `jq -e ...` validation and error message unchanged).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_compose.py tests/test_omniroute_characterize.py tests/test_cli.py tests/test_phoenix_operations.py -q` then `uv run pytest tests/test_shell.py -q -k "render_unit or phoenix"`
Expected: PASS . Existing zero-limit compose tests still pass because `limits_of` feeds them the same persisted zeros.

- [ ] **Step 5: Validate and commit**

Run: `make validate && make test`
Expected: both succeed.

```bash
git add pylib/compose.py llmenv.py scripts/phoenix.sh scripts/check-setup.sh setup/render-unit.sh tests/test_compose.py tests/test_omniroute_characterize.py tests/test_cli.py tests/test_shell.py tests/test_phoenix_operations.py tests/resource_fixtures.py
git commit -m "feat(resources): derive every rendered container limit from the effective policy"
```

---

### Task 6: `make start` prints the policy summary, preserves overrides and renders the effective policy

**Files:**
- Modify: `scripts/start.sh` (after the Phoenix `if [ "$system_one_enabled" = "true" ]; then ... fi` block, before `bash "${REPO_DIR}/setup/render-unit.sh"`)
- Modify: `tests/test_shell.py` (`run_lifecycle_script`: new `extra_config` and `real_render_compose` parameters; the canned `resources` JSON gets a `summary`)
- Test: `tests/test_shell.py`

**Interfaces:**
- Consumes: `summary` array in `llmenv resources` output (Task 4); the real `llmenv render-compose` from Task 5.
- Produces: `run_lifecycle_script(..., extra_config: str = "", real_render_compose: bool = False)`: `extra_config` appends raw YAML to the generated test config; `real_render_compose=True` makes the `uv` stub forward `render-compose` to the real binary (everything else stays stubbed) so a test can read the compose file `render-unit.sh` produces under `tmp_path/compose-inspect/docker-compose.yml`.

- [ ] **Step 1: Make the harness changes and write the failing tests**

1. In `tests/test_shell.py`, in `run_lifecycle_script`'s signature add `extra_config: str = "", real_render_compose: bool = False,` after `system_one_port: int = 8010,`. The helper's config text ends with `"  vram_reserve_mib: 4096\n"` followed by `)` and `config.chmod(config_mode)`; use the Edit tool on the `config.chmod(config_mode)` line that follows `vram_reserve_mib: 4096` inside `run_lifecycle_script` (the config has no top-level `resources:` key, so the appended YAML is a new key) and change the tail from

```python
        "  vram_reserve_mib: 4096\n"
    )
    config.chmod(config_mode)
```
to
```python
        "  vram_reserve_mib: 4096\n"
        + extra_config
    )
    config.chmod(config_mode)
```

2. In the `uv` stub of `run_lifecycle_script`, add this line directly after the `*' migrate-config'*` forwarding line, and add `"REAL_RENDER_COMPOSE": "1" if real_render_compose else "0",` to the `environment` dict:

```python
        "case \"$*\" in *' render-compose'*) [ \"$REAL_RENDER_COMPOSE\" = 1 ] && exec \"$REAL_UV\" \"$@\" ;; esac\n"
```

3. Extend the canned stub output: run
`sd -F '\"memory_max_mib\": 1024}}'"'"'' '\"memory_max_mib\": 1024}, \"summary\": [\"stub resource summary\"]}'"'"'' tests/test_shell.py`
then `rg -n 'stub resource summary' tests/test_shell.py` (expect exactly one hit, in the `else` branch of `resources_case`). If the hit count is not 1, make the same edit by hand with the Edit tool on the line containing `\"phoenix\": {\"cpus_min\": 0.25`.

4. Append the tests:

```python
def test_start_keeps_resource_overrides_and_prints_the_policy_summary(
    tmp_path: pathlib.Path,
) -> None:
    result, config, _calls = run_lifecycle_script(
        tmp_path,
        "scripts/start.sh",
        extra_config="resources:\n  overrides:\n    omniroute:\n      memory_max_mib: 4096\n",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert yq_value(config, ".resources.overrides.omniroute.memory_max_mib") == "4096"
    assert "stub resource summary" in result.stdout


def _start_and_read_omniroute_limit(
    tmp_path: pathlib.Path, extra_config: str
) -> str:
    import yaml

    tmp_path.mkdir()
    result, _config, _calls = run_lifecycle_script(
        tmp_path,
        "scripts/start.sh",
        llm_server_enabled=False,  # keeps the real render host-independent
        real_render_compose=True,
        extra_config=extra_config,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    document = yaml.safe_load((tmp_path / "compose-inspect/docker-compose.yml").read_text())
    return document["services"]["omniroute"]["mem_limit"]


STALE_MIRROR = (
    "resources:\n"
    "  omniroute: {cpus_min: 1, cpus_max: 4, memory_min_mib: 512, memory_max_mib: 999}\n"
)


def test_start_renders_a_resource_override(tmp_path: pathlib.Path) -> None:
    limit = _start_and_read_omniroute_limit(
        tmp_path / "set", STALE_MIRROR + "  overrides:\n    omniroute: {memory_max_mib: 4096}\n"
    )
    assert limit == "4096m"


def test_start_after_an_override_is_cleared_renders_the_default(tmp_path: pathlib.Path) -> None:
    """The cleared config still carries a stale generated mirror (999 MiB) and the
    canned stub rewrites it to 4096 MiB; neither may reach the container."""
    limit = _start_and_read_omniroute_limit(tmp_path / "clear", STALE_MIRROR)
    assert limit == "3072m"
```

   If `render-unit.sh` fails under the harness for a reason unrelated to resources (for example a missing `.env`), read the failure in `result.stderr`, stub only that dependency in the harness, and keep `render-compose` real.

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_shell.py -q -k "keeps_resource_overrides or start_renders_a_resource_override or override_is_cleared"`
Expected: FAIL on `"stub resource summary" in result.stdout` (the override-preserved assertion already passes); the two render tests pass only if Task 5 is done, otherwise FAIL on `999m`/`4096m`.

- [ ] **Step 3: Implement**

In `scripts/start.sh`, directly before the line `bash "${REPO_DIR}/setup/render-unit.sh"`, add:

```bash
# Effective policy (totals and any active overrides), computed by llmenv so the
# wording is unit-tested; resources.overrides itself is never rewritten here.
jq -r '.summary[]?' "$resources_json" | while IFS= read -r summary_line; do
    log_info "$summary_line"
done
```

Also change the comment above the persisted-mirror `yq -i` blocks in `scripts/start.sh` (the "Computing resource limits" step) to say the written `resources.<service>` values are an informational mirror that `llmenv render-compose` and `phoenix.sh` recompute independently; do not remove the writes (existing tests assert them).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_shell.py -q -k "start"`
Expected: PASS.

- [ ] **Step 5: Validate and commit**

Run: `make validate && make test`
Expected: both succeed.

```bash
git add scripts/start.sh tests/test_shell.py
git commit -m "feat(start): print the effective resource policy and prove start renders it"
```

---

### Task 7: Cgroup evidence ledger and the cap gate

**Files:**
- Create: `pylib/resource_evidence.py`
- Modify: `tests/resource_fixtures.py` (ledger builders)
- Modify (test): `tests/test_compose.py` (one drift test pinning `SERVICE_CONTAINERS` to the rendered compose)
- Test: `tests/test_resource_evidence.py` (create)

**Interfaces:**
- Consumes: `MIN_OBSERVATION_HOURS_FLOOR`, `MAX_PEAK_RATIO_CEILING` (Task 1).
- Produces (module `pylib/resource_evidence.py`):
  - `EvidenceError(Exception)`; `Declared(NamedTuple)` with `container`, `compose_service`, `compose_project` (all `str`); constants `SCHEMA = 2`, `MAIN_STACK_PROJECT = "llm-env"`, `MAX_EVIDENCE_AGE_HOURS = 1.0`, `MAX_FOOTPRINT_RATIO = 1.0`, `NO_GUARD_LIMITS`, `SERVICE_CONTAINERS: dict[str, Declared]` (the one container the repo declares per service), `REQUIRED_EVENTS = ("max", "oom", "oom_kill")`; `seal_samples(service: str, container_id: str, started_at_epoch: int, samples) -> list[dict[str, Any]]` (builds the hash chain; used by collection, by `record_evidence` and by the test builders)
  - `live_identity(service: str, *, runner: Runner = ...) -> dict[str, Any]` returning `{"container_id", "started_at_epoch", "cgroup_path"}` of the **running** container declared for `service`. The container is resolved from `SERVICE_CONTAINERS`, never from a name a ledger supplies, and its `com.docker.compose.service`, `com.docker.compose.project` and `com.docker.compose.project.working_dir` labels must all match the declaration: the project is `MAIN_STACK_PROJECT` for the three main-stack services and `llm-env-phoenix` for Phoenix, and the working directory (compared by resolved path, because `$HOME` is a symlink on this host) is `~/.config/llm-env` for the main stack, where `tools/lib.sh` renders it, and `<repo>/docker/phoenix` for Phoenix.
  - `collect_evidence(service: str, *, cgroup_root: Path = CGROUP_ROOT, runner: Runner = ..., now: float | None = None) -> dict[str, Any]` returning a one-sample ledger ("observation") of the declared container (there is no container-name parameter)
  - `record_evidence(directory: Path, observation: Mapping[str, Any]) -> tuple[Path, dict[str, Any]]` appending the observation to `<directory>/<service>-<container_id[:12]>.json`, sealing the new samples onto the stored chain head and refusing a stored ledger whose chain does not verify
  - `parse_evidence(raw: bytes, *, label: str) -> dict[str, Any]` and `load_evidence(path: Path) -> dict[str, Any]`: strict, fail-closed validation (types, required counters, monotone time and peak, and the hash chain)
  - `parse_guard(output: str) -> dict[str, Any] | None`
  - `evaluate_recorded_evidence(ledger, candidate_mib, *, max_peak_ratio: float | None, min_observation_hours: float) -> dict[str, Any]` (pure; no live container or clock) and `evaluate_memory_cap(ledger, candidate_mib, *, max_peak_ratio, min_observation_hours, live: Mapping[str, Any], now: float) -> dict[str, Any]` (adds the running-container binding and the age limit). Both return `{"accepted": bool, "reasons": list[str], "metrics": {...}}` and raise `EvidenceError` for a flag outside the hard floors.
  - `build_reference(path: Path, sha256: str, ledger, candidate_mib: int, result) -> dict[str, Any]` (the `memory_max_evidence` value; `path` must be absolute or it raises `EvidenceError`), `verify_evidence_reference(service: str, cap_mib: int, ref: Mapping[str, Any]) -> list[str]`, `store_bytes(path: Path, data: bytes) -> None`.
- Ledger schema (`schema` 2): `service`, `container` (name), `container_id` (64 hex), `started_at_epoch`, and `samples`: a non-empty list, strictly increasing by time and non-decreasing by `memory_peak_bytes`, each sample holding `collected_at_epoch`, `memory_max_bytes` (int or null), `memory_current_bytes`, `memory_peak_bytes`, `memory_swap_current_bytes`, `memory_swap_peak_bytes`, `memory_events` (must include `max`, `oom`, `oom_kill`), `memory_pressure` (`some` and `full`, each `avg10`, `avg60`, `avg300`, `total_us`), `guard` (the application's thresholds read from the container, or null) and `chain` (64 hex: sha256 over the previous sample's `chain`, or for the first sample a genesis value derived from service, container id and start time, followed by the canonical JSON of this sample without `chain`). `container` must equal `SERVICE_CONTAINERS[service].container` (the ledger cannot name a different workload). Cumulative counters (`memory.peak`, `memory.swap.peak`, each required `memory.events` counter) must be non-decreasing across samples; a decrease means a different lifetime or a forged file.
- Observation window: **every sample in the ledger**, i.e. the whole recorded container lifetime. `record_evidence` only ever appends, and it refuses to extend a ledger whose chain does not verify. Because each sample's `chain` depends on every earlier sample, deleting, editing or reordering a recorded sample makes the loader fail with `chain does not match`, so an unfavourable reading cannot be dropped silently; it can only be left behind by a new container lifetime and a fresh 24 h observation. The chain holds no secret: it is tamper-evident against loss and edits, not tamper-proof (someone who recomputes the whole chain on purpose is out of scope, see Known limits), and the reference's sha256 additionally pins the exact file, including a truncated tail, from the moment the cap is set. Every pressure and guard rule below is evaluated over that whole window (the maximum or strictest value over all samples), never from the newest sample alone.
- Gate rules (all must hold; each failure adds one reason): container lifetime >= `min_observation_hours`; **no new `memory.peak` across >= `min_observation_hours` of samples** (a still-rising lifetime peak is refused); `memory.peak / candidate <= ratio limit`; `(memory.peak + memory.swap.peak) / candidate <= 1.0` (`memory.max` does not count swap, so this is not a reclaim rule: the sum is a conservative upper bound on the anonymous demand, because the two lifetime peaks need not have been simultaneous, and a cap below it pushes more of that demand into swap); `memory.events` `max`, `oom`, `oom_kill` all 0; the highest cgroup memory PSI `some avg10/avg60/avg300` over **all samples** below the guard's `highPsiAvg10`; for OmniRoute the guard thresholds must have been read from the container in **every** sample, must be identical across samples (a difference is a reason), and the strictest values over the window (lowest `highRatio`, lowest `highPsiAvg10`) are the ones applied. Ratio limit = that strictest `highRatio` (OmniRoute) or `NO_GUARD_LIMITS["high_ratio"]` (0.75, a policy value for services that have no guard); a flag can only lower it. Live binding: the ledger's `container_id` and `started_at_epoch` must equal those of the running container declared for the service (`live_identity(service)`), and the newest sample must be at most 1 h old.

- [ ] **Step 1: Write the failing tests**

Append to `tests/resource_fixtures.py` (and add `import hashlib` and `import json` to the import lines at the top of that file, not mid-file, or ruff reports E402):

```python
def make_sample(collected, **changes):
    sample = {
        "collected_at_epoch": collected,
        "memory_max_bytes": 3072 * MIB,
        "memory_current_bytes": 1_160_777_728,
        "memory_peak_bytes": PEAK,
        "memory_swap_current_bytes": 413_323_264,
        "memory_swap_peak_bytes": SWAP_PEAK,
        "memory_events": {"low": 0, "high": 0, "max": 0, "oom": 0, "oom_kill": 0},
        "memory_pressure": {
            "some": {"avg10": 0.0, "avg60": 0.0, "avg300": 0.0, "total_us": 3_152_676},
            "full": {"avg10": 0.0, "avg60": 0.0, "avg300": 0.0, "total_us": 3_119_359},
        },
        "guard": {
            "high_ratio": 0.85, "critical_ratio": 0.92,
            "high_psi_avg10": 30.0, "critical_psi_avg10": 60.0, "psi_disabled": False,
        },
    }
    sample.update(changes)
    return sample


def seal(ledger):
    """`ledger` with its hash chain recomputed from its current samples (always a valid chain)."""
    # Lazy import: Task 1's tests import this module before pylib/resource_evidence.py exists.
    from pylib.resource_evidence import seal_samples

    sealed = seal_samples(
        ledger["service"], ledger["container_id"], ledger["started_at_epoch"], ledger["samples"]
    )
    return {**ledger, "samples": sealed}


def make_ledger(samples=None, **changes):
    """Two sealed samples 25 h apart with an unchanged peak, from the live container."""
    ledger = {
        "schema": 2,
        "service": "omniroute",
        "container": "omniroute",
        "container_id": CONTAINER_ID,
        "started_at_epoch": STARTED,
        "samples": samples if samples is not None else [make_sample(T0), make_sample(T1)],
    }
    ledger.update(changes)
    return seal(ledger)


def ledger_reference(path, ledger, candidate_mib, **changes):
    """Write `ledger` to `path` and return the matching memory_max_evidence reference."""
    raw = json.dumps(ledger).encode()
    path.write_bytes(raw)
    last = ledger["samples"][-1]
    ref = evidence_ref(
        candidate_mib,
        path=str(path),
        sha256=hashlib.sha256(raw).hexdigest(),
        container_id=ledger["container_id"],
        started_at_epoch=ledger["started_at_epoch"],
        collected_at_epoch=last["collected_at_epoch"],
        peak_bytes=last["memory_peak_bytes"],
        swap_peak_bytes=last["memory_swap_peak_bytes"],
    )
    ref.update(changes)
    return ref
```

Create `tests/test_resource_evidence.py`:

```python
import copy
import json
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from resource_fixtures import (
    CONTAINER_ID,
    MIB,
    PEAK,
    STARTED,
    T0,
    T1,
    ledger_reference,
    make_ledger,
    make_sample,
    seal,
)

from pylib.resource_evidence import (
    MAIN_STACK_PROJECT,
    SERVICE_CONTAINERS,
    EvidenceError,
    build_reference,
    collect_evidence,
    evaluate_memory_cap,
    evaluate_recorded_evidence,
    live_identity,
    load_evidence,
    parse_evidence,
    parse_guard,
    record_evidence,
    verify_evidence_reference,
)

LIVE = {"container_id": CONTAINER_ID, "started_at_epoch": STARTED}
NOW = T1 + 600
# Where podman records the compose working directory (compared by resolved path).
MAIN_WORKDIR = str(Path.home() / ".config" / "llm-env")
PHOENIX_WORKDIR = str(Path(__file__).resolve().parents[1] / "docker" / "phoenix")


def gate(ledger, candidate, **changes):
    kwargs = {"max_peak_ratio": None, "min_observation_hours": 24.0, "live": LIVE, "now": NOW}
    kwargs.update(changes)
    return evaluate_memory_cap(ledger, candidate, **kwargs)


# --- the gate, on the live numbers of 2026-10-23 (peak 1651.7 MiB, swap peak 602.6 MiB) ---


def test_gate_accepts_a_candidate_that_stays_inside_the_demand_bound():
    result = gate(make_ledger(), 2560)
    assert result["accepted"] is True, result["reasons"]
    metrics = result["metrics"]
    assert metrics["peak_mib"] == pytest.approx(1651.7, abs=0.1)
    assert metrics["swap_peak_mib"] == pytest.approx(602.6, abs=0.1)
    assert metrics["peak_ratio"] == pytest.approx(0.645, abs=0.001)
    assert metrics["demand_bound_ratio"] == pytest.approx(0.881, abs=0.001)
    assert metrics["observed_hours"] == pytest.approx(98.0, abs=0.1)
    assert metrics["stable_hours"] == pytest.approx(25.0, abs=0.1)
    assert metrics["max_peak_ratio"] == 0.85


def test_gate_rejects_the_two_gib_candidate_on_swap_alone():
    # 1651.7 / 2048 = 0.806 passes the 0.85 guard ratio, but peak + swap peak is 1.10.
    result = gate(make_ledger(), 2048)
    assert result["accepted"] is False
    assert len(result["reasons"]) == 1
    assert "memory.swap.peak" in result["reasons"][0]
    # memory.max does not count swap: the message must say "upper bound", never claim a reclaim effect.
    assert "upper bound" in result["reasons"][0] and "reclaim" not in result["reasons"][0]
    assert result["metrics"]["peak_ratio"] == pytest.approx(0.806, abs=0.001)


def test_gate_rejects_the_one_gib_candidate_on_ratio_and_swap():
    result = gate(make_ledger(), 1024)
    assert result["accepted"] is False
    assert any("lifetime memory.peak" in reason for reason in result["reasons"])
    assert any("memory.swap.peak" in reason for reason in result["reasons"])


def test_gate_refuses_a_still_rising_lifetime_peak():
    rising = make_ledger([make_sample(T0, memory_peak_bytes=PEAK - 100 * MIB), make_sample(T1)])
    result = gate(rising, 2560)
    assert result["accepted"] is False
    assert len(result["reasons"]) == 1
    assert "without a new peak" in result["reasons"][0]
    assert result["metrics"]["stable_hours"] == 0.0


def test_gate_needs_two_samples_a_full_window_apart():
    result = gate(make_ledger([make_sample(T1)]), 2560)
    assert result["accepted"] is False
    assert any("without a new peak" in reason for reason in result["reasons"])


def test_gate_rejects_a_freshly_started_container():
    fresh = make_ledger(
        [make_sample(T1 - 1800), make_sample(T1)], started_at_epoch=T1 - 3600
    )
    result = gate(fresh, 2560, live={"container_id": CONTAINER_ID, "started_at_epoch": T1 - 3600})
    assert result["accepted"] is False
    assert any("has run for 1.0 h" in reason for reason in result["reasons"])


@pytest.mark.parametrize("event", ["max", "oom", "oom_kill"])
def test_gate_rejects_limit_or_oom_events(event):
    events = {"low": 0, "high": 0, "max": 0, "oom": 0, "oom_kill": 0, event: 166}
    ledger = make_ledger([make_sample(T0), make_sample(T1, memory_events=events)])
    result = gate(ledger, 2560)
    assert result["accepted"] is False
    assert any(f"memory.events {event}=166" in reason for reason in result["reasons"])


@pytest.mark.parametrize("window", ["avg10", "avg60", "avg300"])
def test_gate_rejects_memory_pressure_at_the_guards_psi_threshold(window):
    some = {"avg10": 0.0, "avg60": 0.0, "avg300": 0.0, "total_us": 1}
    some[window] = 30.0
    pressure = {"some": some, "full": dict(some, **{window: 0.0})}
    ledger = make_ledger([make_sample(T0), make_sample(T1, memory_pressure=pressure)])
    result = gate(ledger, 2560)
    assert result["accepted"] is False
    assert any(f"memory.pressure some {window}" in reason for reason in result["reasons"])


def _spiked_pressure(window, value=30.0):
    some = {"avg10": 0.0, "avg60": 0.0, "avg300": 0.0, "total_us": 1}
    some[window] = value
    return {"some": some, "full": {"avg10": 0.0, "avg60": 0.0, "avg300": 0.0, "total_us": 1}}


@pytest.mark.parametrize("window", ["avg10", "avg60", "avg300"])
@pytest.mark.parametrize("position", [0, 1])
def test_gate_judges_pressure_from_every_sample_not_only_the_newest(window, position):
    # A spike in the first or a middle sample is followed by calm samples; the newest one is clean.
    samples = [make_sample(T0), make_sample(T0 + 12 * 3600), make_sample(T1)]
    samples[position] = make_sample(samples[position]["collected_at_epoch"], memory_pressure=_spiked_pressure(window))
    result = gate(make_ledger(samples), 2560)
    assert result["accepted"] is False
    assert any(f"memory.pressure some {window}=30" in reason and "highest of 3 samples" in reason
               for reason in result["reasons"])
    assert result["metrics"]["psi_some"][window] == 30.0


def test_gate_fails_closed_for_omniroute_without_guard_thresholds():
    ledger = make_ledger([make_sample(T0, guard=None), make_sample(T1, guard=None)])
    result = gate(ledger, 2560)
    assert result["accepted"] is False
    assert any("could not be read from the container in 2 of 2 samples" in r for r in result["reasons"])


def test_gate_fails_closed_when_only_one_sample_lacks_the_guard_thresholds():
    result = gate(make_ledger([make_sample(T0, guard=None), make_sample(T1)]), 2560)
    assert result["accepted"] is False
    assert any("could not be read from the container in 1 of 2 samples" in r for r in result["reasons"])


def test_gate_applies_the_strictest_guard_ratio_seen_in_the_window():
    strict = dict(make_sample(T0)["guard"], high_ratio=0.60)
    result = gate(make_ledger([make_sample(T0, guard=strict), make_sample(T1)]), 2560)
    assert result["accepted"] is False  # 0.645 > 0.60 although the newest sample says 0.85
    assert result["metrics"]["max_peak_ratio"] == 0.6
    assert any("changed between samples" in reason for reason in result["reasons"])
    assert any("must stay at or below 0.6" in reason for reason in result["reasons"])


def test_gate_applies_the_lowest_psi_threshold_seen_in_the_window():
    strict = dict(make_sample(T0)["guard"], high_psi_avg10=10.0)
    ledger = make_ledger(
        [make_sample(T0, guard=strict), make_sample(T1, memory_pressure=_spiked_pressure("avg10", 12.0))]
    )
    result = gate(ledger, 2560)
    assert result["accepted"] is False  # 12 < 30 (newest guard) but >= 10 (strictest guard)
    assert result["metrics"]["psi_limit"] == 10.0
    assert any("memory.pressure some avg10=12" in reason for reason in result["reasons"])


def test_services_without_a_guard_use_the_fixed_policy_ratio():
    def remote(peak_mib):
        samples = [
            make_sample(t, memory_max_bytes=64 * MIB, memory_peak_bytes=peak_mib * MIB,
                        memory_swap_peak_bytes=0, guard=None)
            for t in (T0, T1)
        ]
        return make_ledger(samples, service="remote_setup", container="remote-setup")

    assert gate(remote(40), 64)["accepted"] is True       # 0.625 <= 0.75
    assert gate(remote(40), 48)["accepted"] is False      # 0.833 > 0.75


def test_a_flag_can_tighten_but_never_loosen_the_ratio():
    assert gate(make_ledger(), 2560, max_peak_ratio=0.7)["accepted"] is True
    tight = gate(make_ledger(), 2560, max_peak_ratio=0.5)
    assert tight["accepted"] is False and tight["metrics"]["max_peak_ratio"] == 0.5
    loose = gate(make_ledger(), 2560, max_peak_ratio=0.95)
    assert loose["accepted"] is False
    assert any("exceeds the guard's high ratio" in reason for reason in loose["reasons"])


@pytest.mark.parametrize(
    ("changes", "fragment"),
    [
        ({"min_observation_hours": 1.0}, "at least 24"),
        ({"min_observation_hours": float("nan")}, "at least 24"),
        ({"max_peak_ratio": 0.0}, "0 < r <= 1"),
        ({"max_peak_ratio": 1.5}, "0 < r <= 1"),
        ({"max_peak_ratio": float("nan")}, "0 < r <= 1"),
    ],
)
def test_flags_outside_the_hard_floors_raise(changes, fragment):
    with pytest.raises(EvidenceError, match=fragment):
        gate(make_ledger(), 2560, **changes)


@pytest.mark.parametrize("candidate", [0, -1, True, 2560.5])
def test_the_candidate_must_be_a_positive_integer(candidate):
    with pytest.raises(EvidenceError, match="positive integer"):
        gate(make_ledger(), candidate)


def test_gate_binds_the_evidence_to_the_running_container():
    other_id = {"container_id": "f" * 64, "started_at_epoch": STARTED}
    other_start = {"container_id": CONTAINER_ID, "started_at_epoch": STARTED + 5}
    for live in (other_id, other_start):
        result = gate(make_ledger(), 2560, live=live)
        assert result["accepted"] is False
        assert any("different container lifetime" in reason for reason in result["reasons"])


@pytest.mark.parametrize("now", [T1 + 2 * 3600, T1 - 10])
def test_gate_rejects_old_or_future_samples(now):
    result = gate(make_ledger(), 2560, now=now)
    assert result["accepted"] is False
    assert any("newest sample is" in reason for reason in result["reasons"])


def test_recorded_evaluation_needs_neither_the_container_nor_the_clock():
    result = evaluate_recorded_evidence(
        make_ledger(), 2560, max_peak_ratio=0.85, min_observation_hours=24.0
    )
    assert result["accepted"] is True


# --- strict loading ---

BAD_LEDGERS = {
    "schema": lambda d: d.update(schema=1),
    "no samples": lambda d: d.update(samples=[]),
    "unknown service": lambda d: d.update(service="nginx"),
    "container id": lambda d: d.update(container_id="abc"),
    "missing oom_kill counter": lambda d: d["samples"][1]["memory_events"].pop("oom_kill"),
    "string counter": lambda d: d["samples"][1]["memory_events"].update(max="0"),
    "bool as integer": lambda d: d["samples"][1].update(memory_peak_bytes=True),
    "negative swap peak": lambda d: d["samples"][1].update(memory_swap_peak_bytes=-1),
    "peak decreases": lambda d: d["samples"][1].update(memory_peak_bytes=1),
    "swap peak decreases": lambda d: d["samples"][1].update(memory_swap_peak_bytes=1),
    "event counter decreases": lambda d: (
        d["samples"][0]["memory_events"].update(oom=5),
        d["samples"][1]["memory_events"].update(oom=0),
    ),
    "container is not the declared one": lambda d: d.update(container="some-other-workload"),
    "time goes backwards": lambda d: d["samples"][1].update(collected_at_epoch=T0 - 1),
    "sample before start": lambda d: d["samples"][0].update(collected_at_epoch=STARTED - 1),
    "pressure missing": lambda d: d["samples"][1].pop("memory_pressure"),
    "pressure string": lambda d: d["samples"][1]["memory_pressure"]["some"].update(avg10="0"),
    "guard incomplete": lambda d: d["samples"][1].update(guard={"high_ratio": 0.85}),
}


@pytest.mark.parametrize("mutate", BAD_LEDGERS.values(), ids=BAD_LEDGERS.keys())
def test_parse_evidence_fails_closed_on_bad_shapes(mutate):
    data = copy.deepcopy(make_ledger())
    mutate(data)
    # Re-seal so that each case fails for its stated reason, not because the chain broke.
    with pytest.raises(EvidenceError, match="unusable"):
        parse_evidence(json.dumps(seal(data)).encode(), label="x")


def test_parse_evidence_rejects_non_json_and_non_objects(tmp_path):
    for raw in (b"{nope", b"[]", b"\xff\xfe"):
        with pytest.raises(EvidenceError, match="cannot read evidence"):
            parse_evidence(raw, label="x")
    with pytest.raises(EvidenceError, match="cannot read evidence"):
        load_evidence(tmp_path / "absent.json")


def test_load_evidence_round_trips(tmp_path):
    path = tmp_path / "ok.json"
    path.write_text(json.dumps(make_ledger()))
    assert load_evidence(path) == make_ledger()


# --- the hash chain ---


def _three():
    return make_ledger([make_sample(T0), make_sample(T0 + 12 * 3600), make_sample(T1)])


TAMPERED = {
    "edited sample": lambda d: d["samples"][1]["memory_pressure"]["some"].update(avg10=1.0),
    "removed middle sample": lambda d: d["samples"].pop(1),
    "removed first sample": lambda d: d["samples"].pop(0),
    "swapped samples": lambda d: d["samples"].insert(1, d["samples"].pop(2)),
    "appended without a chain update": lambda d: d["samples"].append(
        dict(d["samples"][-1], collected_at_epoch=T1 + 60)
    ),
    "chain deleted": lambda d: d["samples"][0].pop("chain"),
    "chain of another sample": lambda d: d["samples"][2].update(chain=d["samples"][0]["chain"]),
}


@pytest.mark.parametrize("mutate", TAMPERED.values(), ids=TAMPERED.keys())
def test_parse_evidence_detects_a_changed_history(mutate):
    data = copy.deepcopy(_three())
    mutate(data)
    with pytest.raises(EvidenceError, match="chain"):
        parse_evidence(json.dumps(data).encode(), label="x")


def test_an_untouched_chain_loads():
    assert len(parse_evidence(json.dumps(_three()).encode(), label="x")["samples"]) == 3


def test_a_deliberately_resealed_history_still_loads():
    # Pins the stated assurance: the chain has no secret, so it is tamper-evident, not tamper-proof.
    # What still pins the exact history after a cap is set is the reference's sha256 (Task 8).
    data = copy.deepcopy(_three())
    data["samples"].pop(1)
    assert len(parse_evidence(json.dumps(seal(data)).encode(), label="x")["samples"]) == 2


# --- collection ---

GUARD_OUTPUT = (
    "highRatio:.85,criticalRatio:.92,recoveryRatio:.75,highPsiAvg10:30,criticalPsiAvg10:60\n"
    "psi_disabled=\n"
)
EVENTS_TEXT = "low 0\nhigh 0\nmax 0\noom 0\noom_kill 0\noom_group_kill 0\nsock_throttled 99\n"
PRESSURE_TEXT = (
    "some avg10=0.00 avg60=0.01 avg300=0.02 total=3152676\n"
    "full avg10=0.00 avg60=0.00 avg300=0.00 total=3119359\n"
)
COLLECTED = T1


def write_cgroup(root, *, swap_peak=True, pressure=True, events=EVENTS_TEXT, memory_max="3221225472\n"):
    cgroup = root / "user.slice" / "x.scope"
    cgroup.mkdir(parents=True)
    (cgroup / "memory.current").write_text("293601280\n")
    (cgroup / "memory.peak").write_text("1282199552\n")
    (cgroup / "memory.max").write_text(memory_max)
    (cgroup / "memory.events").write_text(events)
    (cgroup / "memory.swap.current").write_text("1048576\n")
    if swap_peak:
        (cgroup / "memory.swap.peak").write_text("2097152\n")
    if pressure:
        (cgroup / "memory.pressure").write_text(PRESSURE_TEXT)


def fake_runner(calls, *, exec_output: str | None = GUARD_OUTPUT, ids=(), compose_service="omniroute"):
    pending = iter(ids)

    def run(command):
        calls.append(command)
        if command[:2] == ["podman", "inspect"]:
            return (
                f"true|{next(pending, CONTAINER_ID)}|{STARTED}|/user.slice/x.scope|"
                f"{compose_service}|{MAIN_STACK_PROJECT}|{MAIN_WORKDIR}\n"
            )
        if command[:2] == ["podman", "exec"]:
            if exec_output is None:
                raise EvidenceError("exec failed")
            return exec_output
        raise AssertionError(command)

    return run


def test_collect_evidence_reads_cgroup_files_identity_and_guard(tmp_path):
    write_cgroup(tmp_path)
    calls = []
    observation = collect_evidence(
        "omniroute", cgroup_root=tmp_path, runner=fake_runner(calls), now=COLLECTED
    )
    assert observation["schema"] == 2 and observation["service"] == "omniroute"
    assert observation["container"] == SERVICE_CONTAINERS["omniroute"].container
    assert observation["container_id"] == CONTAINER_ID
    assert observation["started_at_epoch"] == STARTED
    (sample,) = observation["samples"]
    assert sample["collected_at_epoch"] == COLLECTED
    assert sample["memory_peak_bytes"] == 1282199552
    assert sample["memory_swap_peak_bytes"] == 2097152
    assert sample["memory_swap_current_bytes"] == 1048576
    assert sample["memory_max_bytes"] == 3221225472
    assert sample["memory_events"]["sock_throttled"] == 99
    assert sample["memory_pressure"]["some"] == {
        "avg10": 0.0, "avg60": 0.01, "avg300": 0.02, "total_us": 3152676,
    }
    assert sample["guard"] == {
        "high_ratio": 0.85, "critical_ratio": 0.92,
        "high_psi_avg10": 30.0, "critical_psi_avg10": 60.0, "psi_disabled": False,
    }
    assert calls[0][:2] == ["podman", "inspect"] and calls[0][-1] == "omniroute"
    assert calls[1][:5] == ["podman", "exec", "-u", "0", "omniroute"]
    # the collected observation is itself a valid ledger
    parse_evidence(json.dumps(observation).encode(), label="collected")


def test_collect_evidence_reports_a_disabled_psi_guard(tmp_path):
    write_cgroup(tmp_path)
    runner = fake_runner([], exec_output=GUARD_OUTPUT.replace("psi_disabled=", "psi_disabled=true"))
    sample = collect_evidence("omniroute", cgroup_root=tmp_path, runner=runner, now=COLLECTED)["samples"][0]
    assert sample["guard"]["psi_disabled"] is True


def test_collect_evidence_unlimited_memory_is_null(tmp_path):
    write_cgroup(tmp_path, memory_max="max\n")
    sample = collect_evidence("omniroute", cgroup_root=tmp_path, runner=fake_runner([]), now=COLLECTED)["samples"][0]
    assert sample["memory_max_bytes"] is None


def test_collect_evidence_only_probes_the_guard_for_omniroute(tmp_path):
    write_cgroup(tmp_path)
    calls = []
    observation = collect_evidence(
        "remote_setup", cgroup_root=tmp_path,
        runner=fake_runner(calls, compose_service="remote-setup"), now=COLLECTED,
    )
    assert observation["container"] == "remote-setup"
    assert observation["samples"][0]["guard"] is None
    assert {call[1] for call in calls} == {"inspect"}


def test_collect_evidence_degrades_to_no_guard_when_the_probe_fails(tmp_path):
    write_cgroup(tmp_path)
    runner = fake_runner([], exec_output=None)
    sample = collect_evidence("omniroute", cgroup_root=tmp_path, runner=runner, now=COLLECTED)["samples"][0]
    assert sample["guard"] is None


@pytest.mark.parametrize(
    ("kwargs", "fragment"),
    [
        ({"swap_peak": False}, "memory.swap.peak"),
        ({"pressure": False}, "memory.pressure"),
        ({"events": "low 0\nhigh 0\n"}, "memory.events is missing"),
    ],
)
def test_collect_evidence_fails_closed_on_missing_signals(tmp_path, kwargs, fragment):
    write_cgroup(tmp_path, **kwargs)
    with pytest.raises(EvidenceError, match=fragment):
        collect_evidence("omniroute", cgroup_root=tmp_path, runner=fake_runner([]), now=COLLECTED)


def test_collect_evidence_rejects_a_container_that_restarted_mid_collection(tmp_path):
    write_cgroup(tmp_path)
    runner = fake_runner([], ids=(CONTAINER_ID, "e" * 64))
    with pytest.raises(EvidenceError, match="restarted while evidence was being collected"):
        collect_evidence("omniroute", cgroup_root=tmp_path, runner=runner, now=COLLECTED)


def labelled_runner(
    compose_service="omniroute", project=MAIN_STACK_PROJECT, running="true", calls=None, workdir=MAIN_WORKDIR
):
    def run(command):
        if calls is not None:
            calls.append(command)
        return (
            f"{running}|{CONTAINER_ID}|{STARTED}|/user.slice/x.scope|"
            f"{compose_service}|{project}|{workdir}\n"
        )

    return run


def test_live_identity_rejects_a_stopped_container():
    with pytest.raises(EvidenceError, match="unexpected podman inspect output"):
        live_identity("omniroute", runner=labelled_runner(running="false"))


def test_live_identity_inspects_the_declared_container_and_reads_its_labels():
    calls = []
    identity = live_identity("omniroute", runner=labelled_runner(calls=calls))
    assert identity == {"container_id": CONTAINER_ID, "started_at_epoch": STARTED, "cgroup_path": "/user.slice/x.scope"}
    assert calls[0][-1] == SERVICE_CONTAINERS["omniroute"].container == "omniroute"


def test_live_identity_rejects_a_container_of_another_compose_service():
    # Same container name, but its labels say it is not the declared compose service.
    with pytest.raises(EvidenceError, match="declared omniroute compose project: service label 'postgres'"):
        live_identity("omniroute", runner=labelled_runner(compose_service="postgres"))


@pytest.mark.parametrize("service", ["omniroute", "remote_setup", "system_one"])
def test_live_identity_pins_the_main_stack_project(service):
    declared = SERVICE_CONTAINERS[service]
    assert declared.compose_project == MAIN_STACK_PROJECT == "llm-env"
    assert live_identity(service, runner=labelled_runner(declared.compose_service))["container_id"] == CONTAINER_ID
    # The declared name and service label with another project is not the repo's stack.
    other = labelled_runner(declared.compose_service, project="someone-elses-project")
    with pytest.raises(EvidenceError, match="project label 'someone-elses-project'"):
        live_identity(service, runner=other)


def test_live_identity_pins_the_main_stack_working_directory():
    # Same container name, service label and project name `llm-env`, but started from the repo
    # checkout (also a directory called llm-env) instead of the directory the stack is rendered into.
    checkout = str(Path(__file__).resolve().parents[1])
    with pytest.raises(EvidenceError, match="working directory"):
        live_identity("omniroute", runner=labelled_runner(workdir=checkout))
    with pytest.raises(EvidenceError, match="unexpected podman inspect output"):
        live_identity("omniroute", runner=labelled_runner(workdir=""))


def test_live_identity_compares_resolved_working_directories(tmp_path, monkeypatch):
    # $HOME may be a symlink (/home/bazzite -> /var/home/bazzite); podman records the real path.
    (tmp_path / "real" / ".config" / "llm-env").mkdir(parents=True)
    (tmp_path / "link").symlink_to(tmp_path / "real")
    monkeypatch.setenv("HOME", str(tmp_path / "link"))
    recorded = str(tmp_path / "real" / ".config" / "llm-env")
    assert live_identity("omniroute", runner=labelled_runner(workdir=recorded))["container_id"] == CONTAINER_ID


def test_live_identity_pins_the_phoenix_compose_project():
    ok = labelled_runner("phoenix", "llm-env-phoenix", workdir=PHOENIX_WORKDIR)
    assert live_identity("phoenix", runner=ok)["container_id"] == CONTAINER_ID
    other = labelled_runner("phoenix", "someone-elses-project", workdir=PHOENIX_WORKDIR)
    with pytest.raises(EvidenceError, match="project label 'someone-elses-project'"):
        live_identity("phoenix", runner=other)
    with pytest.raises(EvidenceError, match="working directory"):
        live_identity("phoenix", runner=labelled_runner("phoenix", "llm-env-phoenix"))  # the main-stack directory


def test_live_identity_rejects_an_unknown_service():
    with pytest.raises(EvidenceError, match="unknown service"):
        live_identity("nginx", runner=labelled_runner())


def test_collect_evidence_rejects_an_unknown_service(tmp_path):
    with pytest.raises(EvidenceError, match="unknown service"):
        collect_evidence("nginx", cgroup_root=tmp_path, runner=fake_runner([]))


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        (GUARD_OUTPUT, {"high_ratio": 0.85, "critical_ratio": 0.92,
                        "high_psi_avg10": 30.0, "critical_psi_avg10": 60.0, "psi_disabled": False}),
        ("psi_disabled=\n", None),
        (GUARD_OUTPUT.replace("critical", "xcritical"), None),
        (GUARD_OUTPUT + GUARD_OUTPUT.replace(".85", ".80"), None),
    ],
)
def test_parse_guard_requires_exactly_one_distinct_threshold_set(output, expected):
    assert parse_guard(output) == expected


@pytest.mark.parametrize("value", ["1", "true", "YES", "on"])
def test_parse_guard_reads_the_psi_disabled_switch_like_the_application(value):
    parsed = parse_guard(GUARD_OUTPUT.replace("psi_disabled=", f"psi_disabled={value}"))
    assert parsed is not None and parsed["psi_disabled"] is True


# --- ledger on disk ---


def test_record_evidence_appends_samples_per_container_lifetime(tmp_path):
    first, ledger = record_evidence(tmp_path, make_ledger([make_sample(T0)]))
    assert first.name == "omniroute-44b8702af6cd.json"
    assert len(ledger["samples"]) == 1
    second, ledger = record_evidence(tmp_path, make_ledger([make_sample(T1)]))
    assert second == first and len(ledger["samples"]) == 2
    assert load_evidence(first) == ledger
    assert stat.S_IMODE(first.stat().st_mode) == 0o600
    # The appended sample is chained onto the stored head, not onto the genesis of its own observation.
    alone = make_ledger([make_sample(T1)])["samples"][0]["chain"]
    assert ledger["samples"][1]["chain"] != alone


def test_record_evidence_refuses_a_different_lifetime_or_a_repeat(tmp_path):
    record_evidence(tmp_path, make_ledger([make_sample(T0)]))
    with pytest.raises(EvidenceError, match="same container lifetime"):
        record_evidence(tmp_path, make_ledger([make_sample(T1)], started_at_epoch=STARTED + 1))
    with pytest.raises(EvidenceError, match="strictly increasing"):
        record_evidence(tmp_path, make_ledger([make_sample(T0)]))


def test_record_evidence_refuses_to_extend_a_ledger_whose_history_was_cut(tmp_path):
    path, _ = record_evidence(tmp_path, make_ledger([make_sample(T0)]))
    record_evidence(tmp_path, make_ledger([make_sample(T0 + 12 * 3600)]))
    cut = json.loads(path.read_text())
    cut["samples"].pop(0)  # an adverse early reading removed from the stored file
    path.write_text(json.dumps(cut))
    with pytest.raises(EvidenceError, match="chain"):
        record_evidence(tmp_path, make_ledger([make_sample(T1)]))


# --- references ---


def test_build_reference_records_the_gate_inputs(tmp_path):
    ledger = make_ledger()
    result = gate(ledger, 2560)
    ref = build_reference(tmp_path / "e.json", "b" * 64, ledger, 2560, result)
    assert ref == {
        "path": str(tmp_path / "e.json"), "sha256": "b" * 64, "container_id": CONTAINER_ID,
        "started_at_epoch": STARTED, "collected_at_epoch": T1, "peak_bytes": PEAK,
        "swap_peak_bytes": ledger["samples"][-1]["memory_swap_peak_bytes"],
        "candidate_mib": 2560, "max_peak_ratio": 0.85, "min_observation_hours": 24.0,
    }


def test_build_reference_requires_an_absolute_path():
    ledger = make_ledger()
    with pytest.raises(EvidenceError, match="must be absolute"):
        build_reference(Path("evidence/e.json"), "b" * 64, ledger, 2560, gate(ledger, 2560))


def test_verify_reference_rejects_a_relative_path(tmp_path, monkeypatch):
    ref = ledger_reference(tmp_path / "e.json", make_ledger(), 2560)
    monkeypatch.chdir(tmp_path)  # the file would even be found from here; it must still be refused
    (error,) = verify_evidence_reference("omniroute", 2560, {**ref, "path": "e.json"})
    assert "not absolute" in error and "working directory" in error


def test_verify_reference_accepts_a_matching_untouched_file(tmp_path):
    ref = ledger_reference(tmp_path / "e.json", make_ledger(), 2560)
    assert verify_evidence_reference("omniroute", 2560, ref) == []


def test_verify_reference_reports_a_missing_file_with_the_way_out(tmp_path):
    ref = ledger_reference(tmp_path / "e.json", make_ledger(), 2560)
    (tmp_path / "e.json").unlink()
    (error,) = verify_evidence_reference("omniroute", 2560, ref)
    assert "cannot be read" in error and "resource-override clear --service omniroute" in error


def test_verify_reference_detects_an_edited_file(tmp_path):
    ref = ledger_reference(tmp_path / "e.json", make_ledger(), 2560)
    (tmp_path / "e.json").write_text(json.dumps(make_ledger([make_sample(T1)])))
    (error,) = verify_evidence_reference("omniroute", 2560, ref)
    assert "sha256" in error


@pytest.mark.parametrize(
    ("changes", "fragment"),
    [
        ({"peak_bytes": PEAK + 1}, "peak_bytes does not match"),
        ({"collected_at_epoch": T1 - 1}, "collected_at_epoch does not match"),
        ({"container_id": "e" * 64}, "container_id does not match"),
        ({"max_peak_ratio": 0.99}, "exceeds the guard's high ratio"),
    ],
)
def test_verify_reference_detects_a_reference_that_disagrees_with_the_file(tmp_path, changes, fragment):
    ref = ledger_reference(tmp_path / "e.json", make_ledger(), 2560, **changes)
    assert any(fragment in error for error in verify_evidence_reference("omniroute", 2560, ref))


def test_verify_reference_detects_a_cap_that_was_hand_edited(tmp_path):
    ref = ledger_reference(tmp_path / "e.json", make_ledger(), 2560)
    errors = verify_evidence_reference("omniroute", 2304, ref)
    assert any("candidate_mib does not match" in error for error in errors)


def test_verify_reference_reruns_the_gate(tmp_path):
    rising = make_ledger([make_sample(T0, memory_peak_bytes=PEAK - 100 * MIB), make_sample(T1)])
    ref = ledger_reference(tmp_path / "e.json", rising, 2560)
    assert any("without a new peak" in error for error in verify_evidence_reference("omniroute", 2560, ref))


def test_verify_reference_rejects_a_ledger_that_names_another_container(tmp_path):
    other = make_ledger(container="some-other-workload")
    ref = ledger_reference(tmp_path / "e.json", other, 2560)
    errors = verify_evidence_reference("omniroute", 2560, ref)
    assert errors and any("unusable" in error and "declared" in error for error in errors)
```

Append to `tests/test_compose.py` so the declared containers and the main-stack project cannot drift from what the repo renders and runs (the binding in `live_identity` is only as good as this table):

```python
def test_declared_evidence_containers_match_the_rendered_compose():
    from pylib.resource_evidence import SERVICE_CONTAINERS

    document = system_one_document({**CFG, "system_one": SYSTEM_ONE})
    for service in ("omniroute", "remote_setup", "system_one"):
        declared = SERVICE_CONTAINERS[service]
        assert document["services"][declared.compose_service]["container_name"] == declared.container

    root = Path(__file__).resolve().parents[1]
    phoenix = yaml.safe_load((root / "docker/phoenix/compose.yml").read_text())
    declared = SERVICE_CONTAINERS["phoenix"]
    assert declared.compose_service in phoenix["services"]
    assert f'project="{declared.compose_project}"' in (root / "scripts/phoenix.sh").read_text()
    # podman-compose names an unnamed container <project>_<service>_1.
    assert declared.container == f"{declared.compose_project}_{declared.compose_service}_1"


def test_main_stack_project_is_the_one_podman_compose_derives_from_the_rendered_file():
    """Read the project name the rendered stack really gets instead of trusting a constant."""
    import re

    from pylib.resource_evidence import MAIN_STACK_PROJECT, SERVICE_CONTAINERS

    root = Path(__file__).resolve().parents[1]
    match = re.search(r'^COMPOSE_FILE="\$\{HOME\}/(.+)"$', (root / "tools/lib.sh").read_text(), re.MULTILINE)
    assert match, "tools/lib.sh no longer defines COMPOSE_FILE under $HOME"
    rendered = Path(match.group(1))
    # With no -p and no top-level `name:`, podman compose uses the directory of the compose file.
    assert MAIN_STACK_PROJECT == rendered.parent.name
    assert "name" not in system_one_document({**CFG, "system_one": SYSTEM_ONE})
    for service in ("omniroute", "remote_setup", "system_one"):
        assert SERVICE_CONTAINERS[service].compose_project == MAIN_STACK_PROJECT
    # Nothing that starts or manages the main stack may rename the project.
    for script in [*(root / "scripts").glob("*.sh"), *(root / "setup").glob("*.sh")]:
        if script.name.startswith("phoenix"):
            continue  # separate stacks that pass -p on purpose
        for line in script.read_text().splitlines():
            if "podman compose" in line and "COMPOSE_FILE" in line:
                assert " -p " not in line and "--project-name" not in line, f"{script.name}: {line.strip()}"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_resource_evidence.py -q`
Expected: FAIL at collection (`ModuleNotFoundError: No module named 'pylib.resource_evidence'`).

- [ ] **Step 3: Implement**

Create `pylib/resource_evidence.py`:

```python
"""Read-only cgroup evidence and the gate for lowering a hard memory cap.

A hard cap is a policy choice, so lowering one below the built-in default has
to be backed by evidence read from the running container. Nothing here writes
to a cgroup, resets a counter or changes a container; the only files written
are evidence ledgers, which are hash-chained samples of one container lifetime.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import subprocess
import tempfile
import time
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any, NamedTuple, TypeGuard

from pylib.resources import MAX_PEAK_RATIO_CEILING, MIN_OBSERVATION_HOURS_FLOOR

MIB = 1024 * 1024
SCHEMA = 2
CGROUP_ROOT = Path("/sys/fs/cgroup")

# Policy choices, not measurements.
# - A sample older than this cannot authorise a cap: memory.peak may have moved.
MAX_EVIDENCE_AGE_HOURS = 1.0
# - memory.max does NOT count swap, so a lower cap does not force pages that
#   already sit in swap back through reclaim; it pushes more of the demand into
#   swap, where thrashing starts. memory.peak and memory.swap.peak are two
#   independent lifetime peaks that need not have been reached at the same
#   moment, so their sum is a conservative UPPER BOUND on the anonymous demand,
#   never a measured simultaneous peak. A candidate below the bound is refused.
MAX_FOOTPRINT_RATIO = 1.0
# - Services without a pressure guard of their own have nothing to measure;
#   these stand in for the guard's highRatio / highPsiAvg10.
NO_GUARD_LIMITS = {"high_ratio": 0.75, "high_psi_avg10": 30.0}


class Declared(NamedTuple):
    """The one container the repo declares for a service."""

    container: str
    compose_service: str
    compose_project: str


# podman compose names the main stack after the directory of the rendered file:
# tools/lib.sh renders ~/.config/llm-env/docker-compose.yml, and neither the
# generated unit nor render_compose passes -p or a top-level `name:`. The
# project label alone is not an ownership proof (a stack started from the repo
# checkout is also called llm-env), so live_identity also pins the working
# directory. tests/test_compose.py derives this value from those files.
MAIN_STACK_PROJECT = "llm-env"
REPO_ROOT = Path(__file__).resolve().parents[1]

# Evidence is bound to these, never to a container name carried by a ledger.
# tests/test_compose.py pins them to the rendered compose file, tools/lib.sh and scripts/phoenix.sh.
SERVICE_CONTAINERS = {
    "omniroute": Declared("omniroute", "omniroute", MAIN_STACK_PROJECT),
    "remote_setup": Declared("remote-setup", "remote-setup", MAIN_STACK_PROJECT),
    "system_one": Declared("system-one", "system-one", MAIN_STACK_PROJECT),
    "phoenix": Declared("llm-env-phoenix_phoenix_1", "phoenix", "llm-env-phoenix"),
}
REQUIRED_EVENTS = ("max", "oom", "oom_kill")
GUARD_KEYS = ("high_ratio", "critical_ratio", "high_psi_avg10", "critical_psi_avg10")
PSI_WINDOWS = ("avg10", "avg60", "avg300")

# OmniRoute's resource-pressure guard sheds load (503 resource_pressure) once
# (memory.current - file cache) / memory.max reaches criticalRatio or memory
# PSI crosses its thresholds. The constants are compiled into the image, so
# they are read from the running container on every sample, never assumed.
_GUARD_PATTERN = (
    "highRatio:[0-9.]+,criticalRatio:[0-9.]+,recoveryRatio:[0-9.]+,"
    "highPsiAvg10:[0-9.]+,criticalPsiAvg10:[0-9.]+"
)
_GUARD_PROBE = (
    f"grep -hoE '{_GUARD_PATTERN}' /app/.build/next/server/chunks/*.js | sort -u; "
    "printf 'psi_disabled=%s\\n' \"${OMNIROUTE_PRESSURE_PSI_DISABLED:-}\""
)
_GUARD_RE = re.compile(
    r"highRatio:([0-9.]+),criticalRatio:([0-9.]+),recoveryRatio:[0-9.]+,"
    r"highPsiAvg10:([0-9.]+),criticalPsiAvg10:([0-9.]+)"
)
_PSI_DISABLED_RE = re.compile(r"^psi_disabled=(.*)$", re.MULTILINE)
_PSI_LINE_RE = re.compile(
    r"^(some|full) avg10=([0-9.]+) avg60=([0-9.]+) avg300=([0-9.]+) total=([0-9]+)$"
)
_TRUTHY = {"1", "true", "yes", "on"}  # mirrors the application's own parsing
_HEX64 = re.compile(r"[0-9a-f]{64}")

Runner = Callable[[list[str]], str]


class EvidenceError(Exception):
    """Evidence could not be collected, read, or is unusable."""


def _run(command: list[str]) -> str:
    try:
        done = subprocess.run(
            command, capture_output=True, text=True, check=False, timeout=30
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise EvidenceError(f"cannot run {command[0]}: {exc}") from exc
    if done.returncode != 0:
        raise EvidenceError(f"{' '.join(command)} failed: {done.stderr.strip()}")
    return done.stdout


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise EvidenceError(f"cannot read {path}: {exc}") from exc


def _read_int(path: Path, *, allow_max: bool = False) -> int | None:
    text = _read_text(path)
    if allow_max and text == "max":
        return None
    try:
        return int(text)
    except ValueError as exc:
        raise EvidenceError(f"{path} is not an integer: {text!r}") from exc


def _is_count(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _is_number(value: object) -> TypeGuard[int | float]:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _genesis(service: str, container_id: str, started_at_epoch: int) -> str:
    """The chain value before the first sample: it binds a chain to one container lifetime."""
    return hashlib.sha256(
        f"llm-env-evidence|{service}|{container_id}|{started_at_epoch}".encode()
    ).hexdigest()


def _digest(previous: str, sample: Mapping[str, Any]) -> str:
    body = {key: value for key, value in sample.items() if key != "chain"}
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256((previous + canonical).encode()).hexdigest()


def _seal(previous: str, samples: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Copies of `samples` chained onto `previous`, each carrying its own `chain` value."""
    sealed: list[dict[str, Any]] = []
    for sample in samples:
        previous = _digest(previous, sample)
        sealed.append({**{k: v for k, v in sample.items() if k != "chain"}, "chain": previous})
    return sealed


def seal_samples(
    service: str,
    container_id: str,
    started_at_epoch: int,
    samples: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """`samples` as the hash chain of one container lifetime (no secret: tamper-evident only)."""
    return _seal(_genesis(service, container_id, started_at_epoch), samples)


def parse_guard(output: str) -> dict[str, Any] | None:
    """The guard thresholds from the probe output; None unless exactly one set."""
    sets = set(_GUARD_RE.findall(output))
    if len(sets) != 1:
        return None
    try:
        values = [float(value) for value in next(iter(sets))]
    except ValueError:
        return None
    disabled = _PSI_DISABLED_RE.search(output)
    return {
        **dict(zip(GUARD_KEYS, values, strict=True)),
        "psi_disabled": disabled is not None and disabled.group(1).strip().lower() in _TRUTHY,
    }


def _parse_events(text: str) -> dict[str, int]:
    events: dict[str, int] = {}
    for line in text.splitlines():
        key, _, value = line.partition(" ")
        if value.strip().isdigit():
            events[key] = int(value)
    missing = [key for key in REQUIRED_EVENTS if key not in events]
    if missing:
        raise EvidenceError(f"memory.events is missing {', '.join(missing)}")
    return events


def _parse_pressure(text: str) -> dict[str, dict[str, Any]]:
    pressure: dict[str, dict[str, Any]] = {}
    for line in text.splitlines():
        match = _PSI_LINE_RE.match(line.strip())
        if match:
            kind, avg10, avg60, avg300, total = match.groups()
            pressure[kind] = {
                "avg10": float(avg10), "avg60": float(avg60),
                "avg300": float(avg300), "total_us": int(total),
            }
    if set(pressure) != {"some", "full"}:
        raise EvidenceError("memory.pressure has no parseable some/full lines")
    return pressure


def _declared(service: str) -> Declared:
    if service not in SERVICE_CONTAINERS:
        raise EvidenceError(
            f"unknown service {service!r}; expected one of {', '.join(SERVICE_CONTAINERS)}"
        )
    return SERVICE_CONTAINERS[service]


_INSPECT_FORMAT = (
    "{{.State.Running}}|{{.Id}}|{{.State.StartedAt.Unix}}|{{.State.CgroupPath}}|"
    '{{index .Config.Labels "com.docker.compose.service"}}|'
    '{{index .Config.Labels "com.docker.compose.project"}}|'
    '{{index .Config.Labels "com.docker.compose.project.working_dir"}}'
)


def _expected_workdir(service: str) -> Path:
    """The directory podman records as the compose project's working directory."""
    if service == "phoenix":
        return REPO_ROOT / "docker" / "phoenix"
    return Path.home() / ".config" / "llm-env"  # where tools/lib.sh renders the main stack


def live_identity(service: str, *, runner: Runner = _run) -> dict[str, Any]:
    """Id, start time and cgroup path of the RUNNING container declared for `service`.

    The container name comes from SERVICE_CONTAINERS, not from any ledger. Its
    compose service label, project label and project working directory must all
    agree with the declaration (fail closed otherwise); the directory is compared
    by resolved path because $HOME is a symlink on this host.
    """
    declared = _declared(service)
    fields = runner(
        ["podman", "inspect", "--format", _INSPECT_FORMAT, declared.container]
    ).strip().split("|", 6)
    if (
        len(fields) != 7
        or fields[0] != "true"
        or not _HEX64.fullmatch(fields[1])
        or not fields[2].isdigit()
        or not fields[3].startswith("/")
        or not fields[6].startswith("/")
    ):
        raise EvidenceError(
            f"unexpected podman inspect output for {declared.container} (is it running?): {fields!r}"
        )
    expected_dir = _expected_workdir(service)
    problems = []
    if fields[4] != declared.compose_service:
        problems.append(f"service label {fields[4]!r} (expected {declared.compose_service!r})")
    if fields[5] != declared.compose_project:
        problems.append(f"project label {fields[5]!r} (expected {declared.compose_project!r})")
    if Path(fields[6]).resolve() != expected_dir.resolve():
        problems.append(f"working directory {fields[6]!r} (expected {str(expected_dir)!r})")
    if problems:
        raise EvidenceError(
            f"container {declared.container} does not belong to the declared {service} "
            f"compose project: {'; '.join(problems)}"
        )
    return {
        "container_id": fields[1],
        "started_at_epoch": int(fields[2]),
        "cgroup_path": fields[3],
    }


def collect_evidence(
    service: str,
    *,
    cgroup_root: Path = CGROUP_ROOT,
    runner: Runner = _run,
    now: float | None = None,
) -> dict[str, Any]:
    """One observation of the container declared for `service`, as a one-sample ledger."""
    name = _declared(service).container
    identity = live_identity(service, runner=runner)
    cgroup = cgroup_root / identity["cgroup_path"].lstrip("/")

    guard = None
    if service == "omniroute":
        try:
            guard = parse_guard(
                runner(["podman", "exec", "-u", "0", name, "sh", "-c", _GUARD_PROBE])
            )
        except EvidenceError:
            guard = None

    sample = {
        "collected_at_epoch": int(time.time() if now is None else now),
        "memory_max_bytes": _read_int(cgroup / "memory.max", allow_max=True),
        "memory_current_bytes": _read_int(cgroup / "memory.current"),
        "memory_peak_bytes": _read_int(cgroup / "memory.peak"),
        "memory_swap_current_bytes": _read_int(cgroup / "memory.swap.current"),
        "memory_swap_peak_bytes": _read_int(cgroup / "memory.swap.peak"),
        "memory_events": _parse_events(_read_text(cgroup / "memory.events")),
        "memory_pressure": _parse_pressure(_read_text(cgroup / "memory.pressure")),
        "guard": guard,
    }
    # Counters read from a cgroup that was replaced mid-way are worthless.
    after = live_identity(service, runner=runner)
    if (after["container_id"], after["started_at_epoch"]) != (
        identity["container_id"], identity["started_at_epoch"],
    ):
        raise EvidenceError(f"{name} restarted while evidence was being collected")
    return {
        "schema": SCHEMA,
        "service": service,
        "container": name,
        "container_id": identity["container_id"],
        "started_at_epoch": identity["started_at_epoch"],
        "samples": seal_samples(
            service, identity["container_id"], identity["started_at_epoch"], [sample]
        ),
    }


def _sample_errors(index: int, sample: object) -> list[str]:
    where = f"samples[{index}]"
    if not isinstance(sample, dict):
        return [f"{where} is not an object"]
    errors = [
        f"{where}.{key} must be a non-negative integer"
        for key in (
            "collected_at_epoch", "memory_current_bytes", "memory_peak_bytes",
            "memory_swap_current_bytes", "memory_swap_peak_bytes",
        )
        if not _is_count(sample.get(key))
    ]
    cap = sample.get("memory_max_bytes")
    if cap is not None and not (_is_count(cap) and cap > 0):
        errors.append(f"{where}.memory_max_bytes must be a positive integer or null")
    events = sample.get("memory_events")
    if not isinstance(events, dict):
        errors.append(f"{where}.memory_events must be an object")
    else:
        errors.extend(
            f"{where}.memory_events.{key} must be a non-negative integer (a missing counter fails closed)"
            for key in REQUIRED_EVENTS
            if not _is_count(events.get(key))
        )
    pressure = sample.get("memory_pressure")
    if not isinstance(pressure, dict):
        errors.append(f"{where}.memory_pressure must be an object")
    else:
        for kind in ("some", "full"):
            entry = pressure.get(kind)
            if not isinstance(entry, dict):
                errors.append(f"{where}.memory_pressure.{kind} must be an object")
                continue
            for window in PSI_WINDOWS:
                value = entry.get(window)
                if not (_is_number(value) and value >= 0):
                    errors.append(f"{where}.memory_pressure.{kind}.{window} must be a non-negative number")
            if not _is_count(entry.get("total_us")):
                errors.append(f"{where}.memory_pressure.{kind}.total_us must be a non-negative integer")
    guard = sample.get("guard")
    if guard is not None and not (
        isinstance(guard, dict)
        and all(_is_number(guard.get(key)) and guard[key] > 0 for key in GUARD_KEYS)
        and isinstance(guard.get("psi_disabled"), bool)
    ):
        errors.append(f"{where}.guard must be null or hold {', '.join(GUARD_KEYS)} and psi_disabled")
    if not (isinstance(sample.get("chain"), str) and _HEX64.fullmatch(sample["chain"])):
        errors.append(f"{where}.chain must be 64 hex characters")
    return errors


def parse_evidence(raw: bytes, *, label: str) -> dict[str, Any]:
    """Strictly validate a ledger; any wrong type or missing counter is an error."""
    try:
        data = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvidenceError(f"cannot read evidence {label}: {exc}") from exc
    if not isinstance(data, dict):
        raise EvidenceError(f"cannot read evidence {label}: not a JSON object")
    errors: list[str] = []
    if data.get("schema") != SCHEMA:
        errors.append(f"schema must be {SCHEMA}")
    if not (isinstance(data.get("service"), str) and data["service"] in SERVICE_CONTAINERS):
        errors.append(f"service must be one of {', '.join(SERVICE_CONTAINERS)}")
    if data.get("service") in SERVICE_CONTAINERS and (
        data.get("container") != SERVICE_CONTAINERS[data["service"]].container
    ):
        errors.append(
            f"container must be the declared {SERVICE_CONTAINERS[data['service']].container!r} "
            f"for {data['service']}, not {data.get('container')!r}"
        )
    if not (isinstance(data.get("container_id"), str) and _HEX64.fullmatch(data["container_id"])):
        errors.append("container_id must be 64 hex characters")
    if not (_is_count(data.get("started_at_epoch")) and data["started_at_epoch"] > 0):
        errors.append("started_at_epoch must be a positive integer")
    samples = data.get("samples")
    if not isinstance(samples, list) or not samples:
        errors.append("samples must be a non-empty list")
    else:
        for index, sample in enumerate(samples):
            errors.extend(_sample_errors(index, sample))
        if not errors:
            link = _genesis(data["service"], data["container_id"], data["started_at_epoch"])
            chain_broken = False
            previous = None
            for index, sample in enumerate(samples):
                if not chain_broken and sample["chain"] != _digest(link, sample):
                    chain_broken = True
                    errors.append(
                        f"samples[{index}].chain does not match: a sample was edited, removed "
                        "or reordered since it was recorded"
                    )
                link = sample["chain"]
                if sample["collected_at_epoch"] < data["started_at_epoch"]:
                    errors.append(f"samples[{index}] predates the container start")
                if previous is not None:
                    if sample["collected_at_epoch"] <= previous["collected_at_epoch"]:
                        errors.append(f"samples[{index}] is not strictly increasing in time")
                    if sample["memory_peak_bytes"] < previous["memory_peak_bytes"]:
                        errors.append(
                            f"samples[{index}] memory.peak decreased (a different container lifetime?)"
                        )
                    if sample["memory_swap_peak_bytes"] < previous["memory_swap_peak_bytes"]:
                        errors.append(f"samples[{index}] memory.swap.peak decreased")
                    errors.extend(
                        f"samples[{index}] memory.events {key} decreased"
                        for key in REQUIRED_EVENTS
                        if sample["memory_events"][key] < previous["memory_events"][key]
                    )
                previous = sample
    if errors:
        raise EvidenceError(f"evidence {label} is unusable: " + "; ".join(errors))
    return data


def load_evidence(path: Path) -> dict[str, Any]:
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        raise EvidenceError(f"cannot read evidence {path}: {exc}") from exc
    return parse_evidence(raw, label=str(path))


def store_bytes(path: Path, data: bytes) -> None:
    """Atomically write `data` to `path` with mode 0600."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def record_evidence(
    directory: Path, observation: Mapping[str, Any]
) -> tuple[Path, dict[str, Any]]:
    """Append `observation` to its container lifetime's ledger file."""
    path = Path(directory) / f"{observation['service']}-{observation['container_id'][:12]}.json"
    stored: list[dict[str, Any]] = []
    if path.exists():
        existing = load_evidence(path)  # a ledger whose chain does not verify is never extended
        if (existing["container_id"], existing["started_at_epoch"]) != (
            observation["container_id"], observation["started_at_epoch"],
        ):
            raise EvidenceError(f"{path} belongs to a different container; not the same container lifetime")
        stored = existing["samples"]
    head = stored[-1]["chain"] if stored else _genesis(
        observation["service"], observation["container_id"], observation["started_at_epoch"]
    )
    ledger = {**observation, "samples": [*stored, *_seal(head, observation["samples"])]}
    checked = parse_evidence(json.dumps(ledger).encode(), label=str(path))
    store_bytes(path, (json.dumps(checked, indent=2) + "\n").encode())
    return path, checked


def _stable_hours(samples: list[dict[str, Any]]) -> float:
    """Hours between the first sample that already showed the final peak and the last."""
    last = samples[-1]
    anchor = next(s for s in samples if s["memory_peak_bytes"] == last["memory_peak_bytes"])
    return (last["collected_at_epoch"] - anchor["collected_at_epoch"]) / 3600


def evaluate_recorded_evidence(
    ledger: Mapping[str, Any],
    candidate_mib: int,
    *,
    max_peak_ratio: float | None,
    min_observation_hours: float,
) -> dict[str, Any]:
    """Decide from the recorded samples alone (no running container, no clock)."""
    if not (isinstance(candidate_mib, int) and not isinstance(candidate_mib, bool) and candidate_mib > 0):
        raise EvidenceError("candidate must be a positive integer MiB value")
    if not (_is_number(min_observation_hours) and min_observation_hours >= MIN_OBSERVATION_HOURS_FLOOR):
        raise EvidenceError(
            f"min observation hours must be at least {MIN_OBSERVATION_HOURS_FLOOR:g} (a hard floor, not adjustable)"
        )
    if max_peak_ratio is not None and not (
        _is_number(max_peak_ratio) and 0 < max_peak_ratio <= MAX_PEAK_RATIO_CEILING
    ):
        raise EvidenceError("max peak ratio must satisfy 0 < r <= 1")

    samples = ledger["samples"]
    last = samples[-1]
    reasons: list[str] = []
    base_ratio: float | None
    psi_limit: float | None
    if ledger["service"] == "omniroute":
        # Every sample of the window must carry the guard thresholds; the strictest
        # ones seen anywhere in the window are applied, not just the newest sample's.
        guards = [sample["guard"] for sample in samples]
        missing = sum(1 for guard in guards if guard is None)
        known = [guard for guard in guards if guard is not None]
        if missing:
            base_ratio = psi_limit = None
            reasons.append(
                "OmniRoute's pressure-guard thresholds could not be read from the container "
                f"in {missing} of {len(samples)} samples, so the safe ratio is unknown (fail closed)"
            )
        else:
            base_ratio = min(guard["high_ratio"] for guard in known)
            psi_limit = min(guard["high_psi_avg10"] for guard in known)
            if len({tuple(guard[key] for key in GUARD_KEYS) for guard in known}) > 1:
                reasons.append(
                    "the guard thresholds changed between samples of the same container lifetime; "
                    f"the strictest values are applied (high ratio {base_ratio:g}, high PSI {psi_limit:g})"
                )
    else:
        base_ratio, psi_limit = NO_GUARD_LIMITS["high_ratio"], NO_GUARD_LIMITS["high_psi_avg10"]
    ratio_limit = base_ratio if max_peak_ratio is None else max_peak_ratio
    if base_ratio is not None and ratio_limit is not None and ratio_limit > base_ratio:
        reasons.append(
            f"peak ratio limit {ratio_limit:g} exceeds the guard's high ratio {base_ratio:g}; "
            "it can only be tightened"
        )

    peak_mib = last["memory_peak_bytes"] / MIB
    swap_peak_mib = last["memory_swap_peak_bytes"] / MIB
    hours = (last["collected_at_epoch"] - ledger["started_at_epoch"]) / 3600
    stable = _stable_hours(samples)
    ratio = peak_mib / candidate_mib
    footprint = (peak_mib + swap_peak_mib) / candidate_mib
    # The highest reading of each window over ALL samples: an earlier spike is not
    # forgiven by a calm newest sample.
    some = {
        window: max(sample["memory_pressure"]["some"][window] for sample in samples)
        for window in PSI_WINDOWS
    }
    some_last = last["memory_pressure"]["some"]

    if hours < min_observation_hours:
        reasons.append(
            f"container has run for {hours:.1f} h; at least {min_observation_hours:g} h of normal use is required"
        )
    if stable < min_observation_hours:
        reasons.append(
            f"memory.peak ({peak_mib:.0f} MiB) has been unchanged for only {stable:.1f} h of samples; "
            f"at least {min_observation_hours:g} h without a new peak is required, so a still-rising "
            "lifetime peak is refused"
        )
    if ratio_limit is not None and ratio > ratio_limit:
        reasons.append(
            f"lifetime memory.peak {peak_mib:.0f} MiB is {ratio:.2f} of the {candidate_mib} MiB "
            f"candidate; it must stay at or below {ratio_limit:g}"
        )
    if footprint > MAX_FOOTPRINT_RATIO:
        reasons.append(
            f"memory.peak + memory.swap.peak = {peak_mib + swap_peak_mib:.0f} MiB is {footprint:.2f} of the "
            f"{candidate_mib} MiB candidate; the sum is a conservative upper bound on the memory demand "
            "(the two lifetime peaks need not have been simultaneous), and a cap below it pushes more of "
            f"that demand into swap (limit {MAX_FOOTPRINT_RATIO:g})"
        )
    # Event counters are cumulative and parse_evidence enforces that they never decrease,
    # so the newest sample holds the maximum over the window.
    reasons.extend(
        f"memory.events {key}={last['memory_events'][key]} since the container started"
        for key in REQUIRED_EVENTS
        if last["memory_events"][key] > 0
    )
    if psi_limit is not None:
        reasons.extend(
            f"memory.pressure some {window}={some[window]:g} is at or above the guard's high PSI "
            f"threshold {psi_limit:g} (highest of {len(samples)} samples)"
            for window in PSI_WINDOWS
            if some[window] >= psi_limit
        )
    lifetime_stall_pct = some_last["total_us"] / (hours * 3600 * 1_000_000) * 100 if hours > 0 else 0.0
    return {
        "accepted": not reasons,
        "reasons": reasons,
        "metrics": {
            "peak_mib": round(peak_mib, 1),
            "swap_peak_mib": round(swap_peak_mib, 1),
            "candidate_mib": candidate_mib,
            "peak_ratio": round(ratio, 3),
            "demand_bound_ratio": round(footprint, 3),
            "max_peak_ratio": ratio_limit,
            "observed_hours": round(hours, 1),
            "stable_hours": round(stable, 1),
            "min_observation_hours": min_observation_hours,
            "psi_some": some,
            "psi_limit": psi_limit,
            "psi_some_lifetime_pct": round(lifetime_stall_pct, 4),
            "guard": last["guard"],
            "samples": len(samples),
        },
    }


def evaluate_memory_cap(
    ledger: Mapping[str, Any],
    candidate_mib: int,
    *,
    max_peak_ratio: float | None,
    min_observation_hours: float,
    live: Mapping[str, Any],
    now: float,
) -> dict[str, Any]:
    """The recorded gate plus the binding to the running container and the age limit."""
    result = evaluate_recorded_evidence(
        ledger, candidate_mib,
        max_peak_ratio=max_peak_ratio, min_observation_hours=min_observation_hours,
    )
    reasons = list(result["reasons"])
    if (live["container_id"], live["started_at_epoch"]) != (
        ledger["container_id"], ledger["started_at_epoch"],
    ):
        reasons.append(
            "the evidence was collected from a different container lifetime than the one running now "
            f"(evidence {ledger['container_id'][:12]}@{ledger['started_at_epoch']}, "
            f"running {str(live['container_id'])[:12]}@{live['started_at_epoch']})"
        )
    age_hours = (now - ledger["samples"][-1]["collected_at_epoch"]) / 3600
    if not 0 <= age_hours <= MAX_EVIDENCE_AGE_HOURS:
        reasons.append(
            f"newest sample is {age_hours:.1f} h old; it must be between 0 and "
            f"{MAX_EVIDENCE_AGE_HOURS:g} h old, so measure again right before setting the cap"
        )
    return {**result, "accepted": not reasons, "reasons": reasons}


def build_reference(
    path: Path,
    sha256: str,
    ledger: Mapping[str, Any],
    candidate_mib: int,
    result: Mapping[str, Any],
) -> dict[str, Any]:
    """The `memory_max_evidence` value that records an accepted gate decision."""
    if not Path(path).is_absolute():
        raise EvidenceError(f"evidence path {path} must be absolute; resolve it before building a reference")
    last = ledger["samples"][-1]
    return {
        "path": str(path),
        "sha256": sha256,
        "container_id": ledger["container_id"],
        "started_at_epoch": ledger["started_at_epoch"],
        "collected_at_epoch": last["collected_at_epoch"],
        "peak_bytes": last["memory_peak_bytes"],
        "swap_peak_bytes": last["memory_swap_peak_bytes"],
        "candidate_mib": candidate_mib,
        "max_peak_ratio": result["metrics"]["max_peak_ratio"],
        "min_observation_hours": result["metrics"]["min_observation_hours"],
    }


def verify_evidence_reference(
    service: str, cap_mib: int, ref: Mapping[str, Any]
) -> list[str]:
    """Re-check a persisted reference against its stored ledger (no clock, no podman).

    Freshness and the running-container binding were enforced when the cap was
    set; here the file must still exist unchanged, agree with every recorded
    field, and still pass the gate for exactly this cap.
    """
    prefix = f"resources.overrides.{service}.memory_max_evidence"
    path = Path(ref["path"])
    if not path.is_absolute():
        return [
            (
                f"{prefix}.path {path} is not absolute, so it would depend on the working directory; "
                f"set the cap again with --evidence, or run `uv run llmenv.py resource-override clear --service {service}`"
            )
        ]
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return [
            (
                f"{prefix}.path {path} cannot be read ({exc}); measure again and set the cap with "
                f"--evidence, or run `uv run llmenv.py resource-override clear --service {service}`"
            )
        ]
    if hashlib.sha256(raw).hexdigest() != ref["sha256"]:
        return [f"{prefix}.sha256 does not match {path}; the evidence file was changed"]
    try:
        ledger = parse_evidence(raw, label=str(path))
        result = evaluate_recorded_evidence(
            ledger, cap_mib,
            max_peak_ratio=ref["max_peak_ratio"],
            min_observation_hours=ref["min_observation_hours"],
        )
    except EvidenceError as exc:
        return [f"{prefix}: {exc}"]
    last = ledger["samples"][-1]
    recorded = {
        "service": (service, ledger["service"]),
        "container_id": (ref["container_id"], ledger["container_id"]),
        "started_at_epoch": (ref["started_at_epoch"], ledger["started_at_epoch"]),
        "collected_at_epoch": (ref["collected_at_epoch"], last["collected_at_epoch"]),
        "peak_bytes": (ref["peak_bytes"], last["memory_peak_bytes"]),
        "swap_peak_bytes": (ref["swap_peak_bytes"], last["memory_swap_peak_bytes"]),
        "candidate_mib": (ref["candidate_mib"], cap_mib),
    }
    errors = [
        f"{prefix}.{name} does not match the evidence file"
        for name, (claimed, actual) in recorded.items()
        if claimed != actual
    ]
    errors.extend(f"{prefix}: {reason}" for reason in result["reasons"])
    return errors
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_resource_evidence.py -q`
Expected: PASS.

- [ ] **Step 5: Validate and commit**

Run: `make validate && make test`
Expected: both succeed (pyright checks the `Runner` alias, the `TypeGuard` helpers and the `Any` returns).

```bash
git add pylib/resource_evidence.py tests/resource_fixtures.py tests/test_resource_evidence.py tests/test_compose.py
git commit -m "feat(resources): collect container-bound cgroup evidence and gate hard-cap reductions on it"
```

---

### Task 8: Enforce the evidence gate on every write path

Every path that writes or reads a resource policy (`resource-override`, a hand edit of `models.yml`, `make setup`, `make start`, `render-unit.sh`, `llmenv budget|resources|render-compose|effective-policy`) goes through `require_valid_config` -> `validate_config`. Putting the file-level check there is what makes "a lowered cap without evidence" unrepresentable, not just unreachable from one CLI.

**Files:**
- Modify: `pylib/config.py` (import block ~lines 14-27; `validate_config` right after the `validate_overrides` call added in Task 3)
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: `verify_evidence_reference` (Task 7); `EVIDENCE_KEY`, `validate_overrides` (Task 1).
- Produces: `validate_config` also returns the errors of `verify_evidence_reference` for every override that carries `memory_max_evidence`, but only when `validate_overrides` found the section structurally valid (so the verifier can rely on field types).

- [ ] **Step 1: Write the failing tests**

In `tests/test_config.py` add `from resource_fixtures import MIB, PEAK, T0, T1, ledger_reference, make_ledger, make_sample` below the existing imports, and append:

```python
def _lowered_cfg(tmp_path, cap=2560, ledger=None, **ref_changes):
    cfg = make_cfg()
    ref = ledger_reference(tmp_path / "evidence.json", ledger or make_ledger(), cap, **ref_changes)
    cfg["resources"]["overrides"] = {
        "omniroute": {"memory_max_mib": cap, "memory_max_evidence": ref}
    }
    return cfg


def test_config_accepts_a_lowered_cap_backed_by_verified_evidence(tmp_path):
    assert validate_config(_lowered_cfg(tmp_path)) == []


def test_config_rejects_a_hand_edited_cap_without_a_reference():
    cfg = make_cfg()
    cfg["resources"]["overrides"] = {"omniroute": {"memory_max_mib": 2560}}
    assert any("needs a memory_max_evidence reference" in e for e in validate_config(cfg))


def test_config_rejects_a_hand_edited_cap_that_no_longer_matches_its_reference(tmp_path):
    cfg = _lowered_cfg(tmp_path)
    cfg["resources"]["overrides"]["omniroute"]["memory_max_mib"] = 2304
    assert any("candidate_mib" in e for e in validate_config(cfg))


def test_config_rejects_a_missing_evidence_file(tmp_path):
    cfg = _lowered_cfg(tmp_path)
    (tmp_path / "evidence.json").unlink()
    errors = validate_config(cfg)
    assert any("cannot be read" in e and "resource-override clear" in e for e in errors)


def test_config_rejects_an_edited_evidence_file(tmp_path):
    cfg = _lowered_cfg(tmp_path)
    (tmp_path / "evidence.json").write_text("{}")
    assert any("sha256" in e for e in validate_config(cfg))


def test_config_rejects_evidence_that_does_not_pass_the_gate(tmp_path):
    rising = make_ledger([make_sample(T0, memory_peak_bytes=PEAK - 100 * MIB), make_sample(T1)])
    errors = validate_config(_lowered_cfg(tmp_path, ledger=rising))
    assert any("without a new peak" in e for e in errors)


def test_config_verifies_the_evidence_from_any_working_directory(tmp_path, monkeypatch):
    cfg = _lowered_cfg(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert validate_config(cfg) == []


def test_config_rejects_a_relative_evidence_path(tmp_path):
    cfg = _lowered_cfg(tmp_path)
    cfg["resources"]["overrides"]["omniroute"]["memory_max_evidence"]["path"] = "evidence.json"
    errors = validate_config(cfg)
    assert any("memory_max_evidence.path is invalid" in e for e in errors)


def test_config_rejects_evidence_with_a_broken_hash_chain(tmp_path):
    cut = make_ledger([make_sample(T0), make_sample(T0 + 12 * 3600), make_sample(T1)])
    cut["samples"].pop(1)  # the file matches its reference sha256, but its history was cut
    errors = validate_config(_lowered_cfg(tmp_path, ledger=cut))
    assert any("chain does not match" in e for e in errors)


def test_config_does_not_touch_evidence_files_when_the_section_is_structurally_invalid(tmp_path):
    cfg = _lowered_cfg(tmp_path, sha256="not-hex")
    errors = validate_config(cfg)
    assert any("sha256 is invalid" in e for e in errors)
    assert not any("cannot be read" in e for e in errors)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_config.py -q -k "lowered or evidence or hand_edited"`
Expected: `test_config_rejects_a_missing_evidence_file`, `..._an_edited_evidence_file`, `..._does_not_pass_the_gate`, `..._a_broken_hash_chain` FAIL (no file-level errors yet); the structural ones already pass.

- [ ] **Step 3: Implement**

In `pylib/config.py` extend the existing `from pylib.resources import (...)` list with `EVIDENCE_KEY` and add `from pylib.resource_evidence import verify_evidence_reference` (isort order: `pylib.resource_evidence` before `pylib.resources`). Replace the line `errors.extend(validate_overrides(resources.get("overrides")))` added in Task 3 with:

```python
            override_errors = validate_overrides(resources.get("overrides"))
            errors.extend(override_errors)
            if not override_errors:
                # The structure is valid, so the verifier may trust field types.
                for service, override in (resources.get("overrides") or {}).items():
                    if EVIDENCE_KEY in override:
                        errors.extend(
                            verify_evidence_reference(
                                service, override["memory_max_mib"], override[EVIDENCE_KEY]
                            )
                        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_config.py tests/test_resource_evidence.py -q`
Expected: PASS.

- [ ] **Step 5: Validate and commit**

Run: `make validate && make test`
Expected: both succeed.

```bash
git add pylib/config.py tests/test_config.py
git commit -m "feat(config): re-verify evidence references behind every lowered memory cap"
```

---

### Task 9: `resource-evidence` and `resource-override` subcommands

**Files:**
- Modify: `llmenv.py` (stdlib imports `hashlib`, `time`; resource imports ~line 87; new command functions near `cmd_resources`; parser entries after the `effective-policy` parser from Task 5; `main` exception tuple ~line 823)
- Test: `tests/test_cli.py`, `tests/test_shell.py`

**Interfaces:**
- Consumes: `validate_overrides`, `DEFAULT_POLICY`, `OVERRIDABLE_SERVICES`, `EVIDENCE_KEY`, `MIN_OBSERVATION_HOURS_FLOOR`, `MAX_PEAK_RATIO_CEILING` (Task 1); `collect_evidence`, `record_evidence`, `parse_evidence`, `evaluate_memory_cap`, `build_reference`, `live_identity`, `store_bytes`, `EvidenceError` (Task 7); `finite_positive_float`, `positive_int`, `save_config`, `require_valid_config`, `load_config` (existing).
- Produces:
  - `uv run llmenv.py resource-evidence --service <svc> [--ledger-dir DIR]` prints one observation of the container the repo declares for that service (there is deliberately no `--container` flag); with `--ledger-dir` it appends it to the container-lifetime ledger and prints `{"ledger": <absolute path>, "samples": n, "sample": {...}}`.
  - `uv run llmenv.py resource-override [--config PATH] show|set|check|clear [--service S] [--cpus-min F] [--cpus-max F] [--memory-min-mib N] [--memory-max-mib N] [--evidence LEDGER] [--max-peak-ratio F] [--min-observation-hours F]`.
  - Rule: `set --memory-max-mib N` with N below `DEFAULT_POLICY[svc]["memory_max_mib"]` requires `--evidence` (a ledger from `make resources-measure`) that passes `evaluate_memory_cap` against the **running container declared for that service** (`live_identity(service)`, never a name read from the ledger) right now; on success the ledger bytes are copied to `<resolved config dir>/evidence/<service>-<sha256[:16]>.json` and `memory_max_evidence` is written next to the cap. The config path is resolved first, so the stored `path` is absolute even for a relative `--config` and verifies from any later working directory. Raising a cap to the default or above removes the reference; other fields never need evidence. `check` runs the same gate and writes nothing. `clear` loads the config without requiring it to be valid, so a config broken by lost evidence can always be repaired.
  - `--max-peak-ratio` must satisfy 0 < r <= 1 (argparse) and is rejected by the gate when above the guard's `highRatio`; `--min-observation-hours` must be >= 24 (argparse), and the gate re-checks both.
  - `set` and `clear` change `models.yml` only. Every `set`/`clear` result includes `"apply"` (`_APPLY_HINT`), which separates two different things: `bash setup/render-unit.sh` re-renders the compose file and unit for the **next** start or boot and does not touch running containers; applying the change to the **running** containers needs `make restart` (`make stop` then `make start`: OmniRoute, remote-setup, System One and, when System One is enabled, Phoenix are stopped and started again, in-flight requests are interrupted, System One reloads its weights before it is healthy, and every container begins a new cgroup lifetime, so `memory.peak`, the counters and the evidence ledgers start over).

- [ ] **Step 1: Write the failing tests**

Add near the top of `tests/test_cli.py`: `from resource_fixtures import CONTAINER_ID, STARTED, T0, T1, make_ledger, make_sample`. (Most tests below call the command functions in-process; the few that go through the pre-existing `run()` helper of that file only exercise argparse wiring. That helper predates the `uv run llmenv.py` rule and is left unchanged; every NEW subprocess helper in this plan uses `uv run llmenv.py`.) Append:

```python
@pytest.fixture
def live_omniroute(monkeypatch):
    import llmenv

    monkeypatch.setattr(
        llmenv, "live_identity",
        lambda service: {"container_id": CONTAINER_ID, "started_at_epoch": STARTED, "cgroup_path": "/x"},
    )
    monkeypatch.setattr(llmenv, "_now", lambda: T1 + 600)


def _override_call(config, action, **fields):
    import llmenv

    args = {
        "config": str(config), "action": action, "service": None,
        "cpus_min": None, "cpus_max": None, "memory_min_mib": None,
        "memory_max_mib": None, "evidence": None, "max_peak_ratio": None,
        "min_observation_hours": 24.0,
    }
    args.update(fields)
    return llmenv.cmd_resource_override(cast(Namespace, SimpleNamespace(**args)))


def _saved_overrides(config):
    return yaml.safe_load(config.read_text()).get("resources", {}).get("overrides")


def _evidence_file(tmp_path, ledger=None):
    path = tmp_path / "ledger.json"
    path.write_text(json.dumps(ledger or make_ledger()))
    return str(path)


def _out(capsys):
    return json.loads(capsys.readouterr().out)


def test_override_lowering_a_cap_without_evidence_is_refused(tmp_path, capsys):
    config = write_test_config(tmp_path)
    code = _override_call(config, "set", service="omniroute", memory_max_mib=2560)
    assert code == 1
    assert "requires --evidence" in _out(capsys)["error"]
    assert _saved_overrides(config) is None


def test_override_raising_a_cap_needs_no_evidence(tmp_path, capsys):
    config = write_test_config(tmp_path)
    assert _override_call(config, "set", service="omniroute", memory_max_mib=4096) == 0
    assert _saved_overrides(config) == {"omniroute": {"memory_max_mib": 4096}}
    apply = _out(capsys)["apply"]
    assert "make restart" in apply
    # render-unit.sh is "next start", not "apply now"; the disruption must be spelled out.
    assert "does not touch running containers" in apply
    assert "interrupted" in apply and "new cgroup lifetime" in apply


def test_override_lowering_a_cap_with_passing_evidence_stores_a_verified_reference(
    tmp_path, capsys, live_omniroute
):
    import hashlib

    import llmenv

    config = write_test_config(tmp_path)
    evidence = _evidence_file(tmp_path)
    code = _override_call(
        config, "set", service="omniroute", memory_max_mib=2560, evidence=evidence
    )
    out = _out(capsys)
    assert code == 0 and out["validation"]["accepted"] is True

    override = _saved_overrides(config)["omniroute"]
    reference = override["memory_max_evidence"]
    digest = hashlib.sha256(Path(evidence).read_bytes()).hexdigest()
    stored = tmp_path / "evidence" / f"omniroute-{digest[:16]}.json"
    assert override["memory_max_mib"] == 2560
    assert reference["sha256"] == digest and reference["path"] == str(stored)
    assert reference["candidate_mib"] == 2560 and reference["container_id"] == CONTAINER_ID
    assert stored.read_bytes() == Path(evidence).read_bytes()
    # the config validates (and keeps validating if the source ledger is deleted)
    Path(evidence).unlink()
    llmenv.require_valid_config(llmenv.load_config(config))


def test_override_stores_an_absolute_evidence_path_for_a_relative_config(
    tmp_path, capsys, monkeypatch, live_omniroute
):
    import llmenv

    config = write_test_config(tmp_path)
    evidence = _evidence_file(tmp_path)
    monkeypatch.chdir(tmp_path)
    code = _override_call(
        Path(config.name), "set", service="omniroute", memory_max_mib=2560, evidence=evidence
    )
    assert code == 0, capsys.readouterr().out
    stored = Path(_saved_overrides(config)["omniroute"]["memory_max_evidence"]["path"])
    assert stored.is_absolute() and stored.parent == (tmp_path / "evidence").resolve()
    # a later command from another directory still verifies the reference
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    llmenv.require_valid_config(llmenv.load_config(config))
    assert _override_call(config, "set", service="omniroute", cpus_max=3) == 0


def test_override_two_gib_is_rejected_on_the_demand_bound(tmp_path, capsys, live_omniroute):
    config = write_test_config(tmp_path)
    code = _override_call(
        config, "set", service="omniroute", memory_max_mib=2048, evidence=_evidence_file(tmp_path)
    )
    out = _out(capsys)
    assert code == 1 and out["accepted"] is False
    assert any("memory.swap.peak" in reason for reason in out["reasons"])
    assert _saved_overrides(config) is None
    assert not (tmp_path / "evidence").exists()


@pytest.mark.parametrize(
    ("ledger_changes", "live_changes", "now", "flags", "fragment"),
    [
        ({"samples": [make_sample(T1)]}, {}, None, {}, "without a new peak"),
        ({}, {"container_id": "f" * 64}, None, {}, "different container lifetime"),
        ({}, {"started_at_epoch": STARTED + 9}, None, {}, "different container lifetime"),
        ({}, {}, T1 + 7200, {}, "newest sample is"),
        ({}, {}, None, {"max_peak_ratio": 0.95}, "exceeds the guard's high ratio"),
        ({}, {}, None, {"max_peak_ratio": 0.5}, "must stay at or below 0.5"),
    ],
)
def test_override_gate_failures_change_nothing(
    tmp_path, capsys, monkeypatch, live_omniroute, ledger_changes, live_changes, now, flags, fragment
):
    import llmenv

    live = {"container_id": CONTAINER_ID, "started_at_epoch": STARTED, "cgroup_path": "/x", **live_changes}
    monkeypatch.setattr(llmenv, "live_identity", lambda service: live)
    if now is not None:
        monkeypatch.setattr(llmenv, "_now", lambda: now)
    config = write_test_config(tmp_path)
    code = _override_call(
        config, "set", service="omniroute", memory_max_mib=2560,
        evidence=_evidence_file(tmp_path, make_ledger(**ledger_changes)), **flags,
    )
    out = _out(capsys)
    assert code == 1 and out["accepted"] is False
    assert any(fragment in reason for reason in out["reasons"]), out["reasons"]
    assert _saved_overrides(config) is None


def test_override_fails_closed_without_guard_thresholds(tmp_path, capsys, live_omniroute):
    config = write_test_config(tmp_path)
    ledger = make_ledger([make_sample(T0, guard=None), make_sample(T1, guard=None)])
    code = _override_call(
        config, "set", service="omniroute", memory_max_mib=2560, evidence=_evidence_file(tmp_path, ledger)
    )
    assert code == 1
    assert any("could not be read" in r for r in _out(capsys)["reasons"])


def test_override_flags_below_the_hard_floor_are_refused(tmp_path, capsys, live_omniroute):
    config = write_test_config(tmp_path)
    code = _override_call(
        config, "set", service="omniroute", memory_max_mib=2560,
        evidence=_evidence_file(tmp_path), min_observation_hours=1.0,
    )
    assert code == 1 and "at least 24" in _out(capsys)["error"]
    assert _saved_overrides(config) is None


def test_override_evidence_for_another_service_is_refused(tmp_path, capsys, live_omniroute):
    config = write_test_config(tmp_path)
    code = _override_call(
        config, "set", service="remote_setup", memory_max_mib=48, evidence=_evidence_file(tmp_path)
    )
    assert code == 1 and "evidence is for omniroute" in _out(capsys)["error"]


def test_override_binds_the_live_check_to_the_declared_container(tmp_path, capsys, monkeypatch):
    """The container inspected comes from the service, not from the (editable) ledger."""
    import llmenv

    inspected = []

    def live(service):
        inspected.append(service)
        return {"container_id": CONTAINER_ID, "started_at_epoch": STARTED, "cgroup_path": "/x"}

    monkeypatch.setattr(llmenv, "live_identity", live)
    monkeypatch.setattr(llmenv, "_now", lambda: T1 + 600)
    config = write_test_config(tmp_path)

    # A ledger naming some other running workload is unusable before any podman call.
    foreign = _evidence_file(tmp_path, make_ledger(container="some-other-workload"))
    code = _override_call(config, "set", service="omniroute", memory_max_mib=2560, evidence=foreign)
    error = _out(capsys)["error"]
    assert code == 1 and "unusable" in error and "declared" in error
    assert inspected == [] and _saved_overrides(config) is None

    # A valid ledger is checked against the container declared for the service.
    ok = _evidence_file(tmp_path)
    assert _override_call(config, "set", service="omniroute", memory_max_mib=2560, evidence=ok) == 0
    assert inspected == ["omniroute"]


def test_override_check_runs_the_gate_and_writes_nothing(tmp_path, capsys, live_omniroute):
    config = write_test_config(tmp_path)
    evidence = _evidence_file(tmp_path)
    assert _override_call(config, "check", service="omniroute", memory_max_mib=2560, evidence=evidence) == 0
    assert _out(capsys)["accepted"] is True
    assert _override_call(config, "check", service="omniroute", memory_max_mib=2048, evidence=evidence) == 1
    assert _out(capsys)["accepted"] is False
    assert _saved_overrides(config) is None and not (tmp_path / "evidence").exists()


def test_override_set_rejects_an_invalid_merge(tmp_path, capsys):
    config = write_test_config(tmp_path)
    assert _override_call(config, "set", service="omniroute", cpus_min=9) == 1
    assert "effective cpus_min" in _out(capsys)["error"]
    assert _saved_overrides(config) is None


def test_override_set_requires_a_service_and_a_field(tmp_path, capsys):
    config = write_test_config(tmp_path)
    assert _override_call(config, "set", memory_max_mib=4096) == 1
    assert _override_call(config, "set", service="omniroute") == 1


def test_override_raising_the_cap_again_drops_the_reference_and_other_edits_keep_it(
    tmp_path, capsys, live_omniroute
):
    config = write_test_config(tmp_path)
    _override_call(config, "set", service="omniroute", memory_max_mib=2560, evidence=_evidence_file(tmp_path))
    assert _override_call(config, "set", service="omniroute", cpus_max=3) == 0
    assert "memory_max_evidence" in _saved_overrides(config)["omniroute"]
    assert _override_call(config, "set", service="omniroute", memory_max_mib=4096) == 0
    assert _saved_overrides(config) == {"omniroute": {"memory_max_mib": 4096, "cpus_max": 3}}


def test_override_clear_repairs_a_config_whose_evidence_was_lost(tmp_path, capsys, live_omniroute):
    import llmenv

    config = write_test_config(tmp_path)
    _override_call(config, "set", service="omniroute", memory_max_mib=2560, evidence=_evidence_file(tmp_path))
    next((tmp_path / "evidence").iterdir()).unlink()
    with pytest.raises(llmenv.ConfigError, match="resource-override clear"):
        llmenv.require_valid_config(llmenv.load_config(config))
    assert _override_call(config, "clear", service="omniroute") == 0
    assert _saved_overrides(config) is None


def test_override_clear_drops_one_service_or_everything(tmp_path, capsys):
    config = write_test_config(tmp_path)
    _override_call(config, "set", service="omniroute", memory_max_mib=4096)
    _override_call(config, "set", service="remote_setup", memory_max_mib=128)
    assert _override_call(config, "clear", service="omniroute") == 0
    assert _saved_overrides(config) == {"remote_setup": {"memory_max_mib": 128}}
    assert _override_call(config, "clear") == 0
    assert "overrides" not in yaml.safe_load(config.read_text())["resources"]


def test_override_show_lists_overrides_and_defaults(tmp_path, capsys):
    config = write_test_config(tmp_path)
    _override_call(config, "set", service="omniroute", memory_max_mib=4096)
    capsys.readouterr()
    assert _override_call(config, "show") == 0
    out = _out(capsys)
    assert out["overrides"] == {"omniroute": {"memory_max_mib": 4096}}
    assert out["defaults"]["omniroute"]["memory_max_mib"] == 3072


def test_resource_evidence_appends_to_the_ledger(tmp_path, monkeypatch, capsys):
    import llmenv

    samples = iter([make_ledger([make_sample(T0)]), make_ledger([make_sample(T1)])])
    monkeypatch.setattr(llmenv, "collect_evidence", lambda service: next(samples))
    args = cast(Namespace, SimpleNamespace(service="omniroute", ledger_dir=str(tmp_path)))
    assert llmenv.cmd_resource_evidence(args) == 0
    assert _out(capsys)["samples"] == 1
    assert llmenv.cmd_resource_evidence(args) == 0
    out = _out(capsys)
    assert out["samples"] == 2 and Path(out["ledger"]).name == "omniroute-44b8702af6cd.json"


def test_resource_evidence_prints_an_absolute_ledger_path(tmp_path, monkeypatch, capsys):
    import llmenv

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(llmenv, "collect_evidence", lambda service: make_ledger([make_sample(T0)]))
    args = cast(Namespace, SimpleNamespace(service="omniroute", ledger_dir="ledgers"))
    assert llmenv.cmd_resource_evidence(args) == 0
    ledger = Path(_out(capsys)["ledger"])
    assert ledger.is_absolute()
    assert ledger == (tmp_path / "ledgers" / "omniroute-44b8702af6cd.json").resolve()


def test_resource_override_and_evidence_subcommands_are_wired(tmp_path):
    config = write_test_config(tmp_path)
    shown = run("resource-override", "show", "--config", str(config))
    assert shown.returncode == 0, shown.stderr
    assert "defaults" in json.loads(shown.stdout)
    base = ["resource-override", "set", "--config", str(config), "--service", "omniroute"]
    for bad in (
        ["--memory-max-mib", "0"],
        ["--memory-max-mib", "2560", "--min-observation-hours", "1"],
        ["--memory-max-mib", "2560", "--max-peak-ratio", "1.5"],
        ["--memory-max-mib", "2560", "--max-peak-ratio", "0"],
    ):
        assert run(*base, *bad).returncode == 2, bad
    assert run("resource-evidence", "--service", "nginx").returncode == 2
    # evidence is only ever collected from the declared container: no free container name
    assert run("resource-evidence", "--service", "omniroute", "--container", "x").returncode == 2
```

Append to `tests/test_shell.py` (helpers `run_render_unit_with_legacy_rocm_config`, `rerun_render_unit`, `_rendered_omniroute_limit` come from Task 5):

```python
def _override_cli(tmp_path: pathlib.Path, *args: str) -> subprocess.CompletedProcess[str]:
    # AGENTS.md: Python is invoked only as `uv run llmenv.py <subcommand>`.
    return subprocess.run(
        ["uv", "run", "llmenv.py", "resource-override", *args,
         "--config", str(tmp_path / "models.yml")],
        cwd=ROOT, text=True, capture_output=True, check=False,
    )


def test_render_unit_follows_resource_override_set_and_clear(tmp_path: pathlib.Path) -> None:
    """set -> render-unit.sh (what enable-boot runs) -> clear -> render-unit.sh, with a stale mirror present."""
    stale = "resources:\n  omniroute: {cpus_min: 1, cpus_max: 4, memory_min_mib: 512, memory_max_mib: 999}\n"
    first, _unit = run_render_unit_with_legacy_rocm_config(tmp_path, extra_config=stale)
    assert first.returncode == 0, first.stderr
    assert _rendered_omniroute_limit(tmp_path) == "3072m"

    setting = _override_cli(tmp_path, "set", "--service", "omniroute", "--memory-max-mib", "4096")
    assert setting.returncode == 0, setting.stdout + setting.stderr
    assert rerun_render_unit(tmp_path).returncode == 0
    assert _rendered_omniroute_limit(tmp_path) == "4096m"

    clearing = _override_cli(tmp_path, "clear", "--service", "omniroute")
    assert clearing.returncode == 0, clearing.stdout + clearing.stderr
    assert rerun_render_unit(tmp_path).returncode == 0
    assert _rendered_omniroute_limit(tmp_path) == "3072m"


def test_render_unit_refuses_a_hand_edited_lowered_cap(tmp_path: pathlib.Path) -> None:
    hand_edit = "resources:\n  overrides:\n    omniroute: {memory_max_mib: 2560}\n"
    result, _unit = run_render_unit_with_legacy_rocm_config(tmp_path, extra_config=hand_edit)
    assert result.returncode != 0
    assert "memory_max_evidence" in result.stdout + result.stderr
    assert not (tmp_path / "compose-inspect/docker-compose.yml").exists()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -q -k "override or resource_evidence"`
Expected: FAIL (`AttributeError: module 'llmenv' has no attribute 'cmd_resource_override'`).

- [ ] **Step 3: Implement**

In `llmenv.py`:

1. Add `import hashlib` (after `import copy`) and `import time` (after `import sys`) to the stdlib imports.

2. Extend the resource imports (the `from pylib.resources import ...` line from Task 5 becomes the block below; keep isort order, `pylib.resource_evidence` before `pylib.resources`):

```python
from pylib.resource_evidence import (
    EvidenceError,
    build_reference,
    collect_evidence,
    evaluate_memory_cap,
    live_identity,
    parse_evidence,
    record_evidence,
    store_bytes,
)
from pylib.resources import (
    DEFAULT_POLICY,
    EVIDENCE_KEY,
    MAX_PEAK_RATIO_CEILING,
    MIN_OBSERVATION_HOURS_FLOOR,
    OVERRIDABLE_SERVICES,
    ResourceError,
    compute_resource_limits,
    effective_policy,
    summarize_policy,
    validate_overrides,
)
```

3. Add after `cmd_resources`:

```python
def _now() -> float:
    return time.time()


def cmd_resource_evidence(args: argparse.Namespace) -> int:
    observation = collect_evidence(args.service)
    if args.ledger_dir is None:
        return emit(observation)
    path, ledger = record_evidence(Path(args.ledger_dir).resolve(), observation)
    return emit(
        {"ledger": str(path), "samples": len(ledger["samples"]), "sample": ledger["samples"][-1]}
    )


def _gate_lowered_cap(
    args: argparse.Namespace, service: str, cap_mib: int, store_dir: Path
) -> tuple[dict[str, Any], dict[str, Any] | None, bytes]:
    """Run the evidence gate for one lowered cap: (result, reference or None, ledger bytes)."""
    default_cap = DEFAULT_POLICY[service]["memory_max_mib"]
    if args.evidence is None:
        raise EvidenceError(
            f"lowering {service} memory_max_mib below its built-in default ({default_cap} MiB) "
            f"requires --evidence from `make resources-measure SERVICE={service}`"
        )
    try:
        raw = Path(args.evidence).read_bytes()
    except OSError as exc:
        raise EvidenceError(f"cannot read evidence {args.evidence}: {exc}") from exc
    ledger = parse_evidence(raw, label=str(args.evidence))
    if ledger["service"] != service:
        raise EvidenceError(f"evidence is for {ledger['service']}, not {service}")
    result = evaluate_memory_cap(
        ledger,
        cap_mib,
        max_peak_ratio=args.max_peak_ratio,
        min_observation_hours=args.min_observation_hours,
        live=live_identity(service),  # the declared container, never ledger["container"]
        now=_now(),
    )
    if not result["accepted"]:
        return result, None, raw
    digest = hashlib.sha256(raw).hexdigest()
    stored = store_dir / f"{service}-{digest[:16]}.json"
    return result, build_reference(stored, digest, ledger, cap_mib, result), raw


# set/clear only edit models.yml. Rendering for the next start and applying to
# running containers are different steps with different impact; say so.
_APPLY_HINT = (
    "saved to models.yml only; running containers keep their current limits. "
    "`bash setup/render-unit.sh` re-renders the compose file and unit for the next start or boot "
    "and does not touch running containers. To apply this to the running stack now, run "
    "`make restart` (stop, then start): OmniRoute, remote-setup, System One and, when System One "
    "is enabled, Phoenix are stopped and started again, so in-flight requests are interrupted, "
    "System One reloads its weights before it is healthy, and every container begins a "
    "new cgroup lifetime (memory.peak, counters and evidence samples start over)"
)


def cmd_resource_override(args: argparse.Namespace) -> int:
    try:
        return _resource_override(args)
    except EvidenceError as exc:
        return fail(str(exc))


def _resource_override(args: argparse.Namespace) -> int:
    path = Path(args.config)
    cfg = load_config(path)
    if args.action != "clear":
        require_valid_config(cfg)  # clear must work even when lost evidence broke the config
    resources = cfg["resources"]
    current = resources.get("overrides")
    overrides: dict[str, Any] = (
        {name: dict(value) for name, value in current.items() if isinstance(value, dict)}
        if isinstance(current, dict)
        else {}
    )

    if args.action == "show":
        return emit(
            {
                "overrides": overrides,
                "defaults": {name: DEFAULT_POLICY[name] for name in OVERRIDABLE_SERVICES},
            }
        )

    if args.action == "clear":
        if args.service is None:
            overrides = {}
        else:
            overrides.pop(args.service, None)
        if overrides:
            resources["overrides"] = overrides
        else:
            resources.pop("overrides", None)
        require_valid_config(cfg)
        save_config(cfg, path)
        return emit({"overrides": overrides, "apply": _APPLY_HINT})

    service = args.service
    if service is None:
        return fail(f"--service is required for {args.action}")
    if args.action == "check":
        if args.memory_max_mib is None:
            return fail("check needs --memory-max-mib")
        requested: dict[str, Any] = {"memory_max_mib": args.memory_max_mib}
    else:
        requested = {
            key: value
            for key, value in (
                ("cpus_min", args.cpus_min),
                ("cpus_max", args.cpus_max),
                ("memory_min_mib", args.memory_min_mib),
                ("memory_max_mib", args.memory_max_mib),
            )
            if value is not None
        }
        if not requested:
            return fail(
                "set needs at least one of --cpus-min, --cpus-max, --memory-min-mib, --memory-max-mib"
            )

    merged = {**overrides.get(service, {}), **requested}
    new_cap = requested.get("memory_max_mib")
    validation: dict[str, Any] | None = None
    reference: dict[str, Any] | None = None
    raw = b""
    # Resolved first: the stored reference must not depend on the caller's working directory.
    store_dir = path.resolve().parent / "evidence"
    if new_cap is not None and new_cap < DEFAULT_POLICY[service]["memory_max_mib"]:
        validation, reference, raw = _gate_lowered_cap(args, service, new_cap, store_dir)
        if not validation["accepted"]:
            return emit(validation, 1)
        merged[EVIDENCE_KEY] = reference
    elif new_cap is not None:
        merged.pop(EVIDENCE_KEY, None)  # at or above the default no evidence is needed
        validation = {"accepted": True, "reasons": [], "note": "no evidence needed at or above the default"}
    if args.action == "check":
        return emit(validation or {})

    candidate = {**overrides, service: merged}
    errors = validate_overrides(candidate)
    if errors:
        return fail("; ".join(errors))
    stored: Path | None = None
    existed = False
    if reference is not None:
        stored = Path(reference["path"])
        existed = stored.exists()
        store_bytes(stored, raw)
    resources["overrides"] = candidate
    try:
        require_valid_config(cfg)  # re-verifies the stored evidence like every other writer
    except ConfigError:
        if stored is not None and not existed:
            stored.unlink(missing_ok=True)
        raise
    save_config(cfg, path)
    return emit(
        {"service": service, "override": merged, "validation": validation, "apply": _APPLY_HINT}
    )
```

4. Parser: after the `effective_parser.set_defaults(...)` lines from Task 5 add:

```python
    def peak_ratio_arg(value: str) -> float:
        parsed = finite_positive_float(value)
        if parsed > MAX_PEAK_RATIO_CEILING:
            raise argparse.ArgumentTypeError(f"must satisfy 0 < r <= {MAX_PEAK_RATIO_CEILING:g}")
        return parsed

    def observation_hours_arg(value: str) -> float:
        parsed = finite_positive_float(value)
        if parsed < MIN_OBSERVATION_HOURS_FLOOR:
            raise argparse.ArgumentTypeError(
                f"must be at least {MIN_OBSERVATION_HOURS_FLOOR:g} (hard floor)"
            )
        return parsed

    evidence_parser = sub.add_parser("resource-evidence")
    evidence_parser.add_argument("--service", required=True, choices=OVERRIDABLE_SERVICES)
    evidence_parser.add_argument("--ledger-dir", default=None)
    evidence_parser.set_defaults(func=cmd_resource_evidence)

    override_parser = sub.add_parser("resource-override")
    override_parser.add_argument("--config", default=argparse.SUPPRESS)
    override_parser.add_argument("action", choices=["show", "set", "check", "clear"])
    override_parser.add_argument("--service", choices=OVERRIDABLE_SERVICES, default=None)
    override_parser.add_argument("--cpus-min", type=finite_positive_float, default=None)
    override_parser.add_argument("--cpus-max", type=finite_positive_float, default=None)
    override_parser.add_argument("--memory-min-mib", type=positive_int, default=None)
    override_parser.add_argument("--memory-max-mib", type=positive_int, default=None)
    override_parser.add_argument("--evidence", default=None)
    override_parser.add_argument("--max-peak-ratio", type=peak_ratio_arg, default=None)
    override_parser.add_argument(
        "--min-observation-hours", type=observation_hours_arg, default=MIN_OBSERVATION_HOURS_FLOOR
    )
    override_parser.set_defaults(func=cmd_resource_override)
```

5. Add `EvidenceError,` to the exception tuple in `main` (after `ResourceError,`). `cmd_resource_override` already converts it, but `cmd_resource_evidence` relies on `main`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -q` then `uv run pytest tests/test_shell.py -q -k "render_unit"`
Expected: PASS. The hand-edit test asserts that the failure names the missing `memory_max_evidence` reference, so it cannot pass for an unrelated render failure.

- [ ] **Step 5: Validate and commit**

Run: `make validate && make test`
Expected: both succeed.

```bash
git add llmenv.py tests/test_cli.py tests/test_shell.py
git commit -m "feat(llmenv): add evidence-gated resource-override and ledger-backed resource-evidence commands"
```

---

### Task 10: `make resources` and `make resources-measure`

**Files:**
- Create: `scripts/resources.sh`, `scripts/resources-measure.sh`
- Modify: `Makefile` (`.PHONY` line 1; two targets before `key-reset:`), `scripts/help.sh` (before the `key-reset` line)
- Test: `tests/test_shell.py`

**Interfaces:**
- Consumes: `llmenv resources` JSON (`summary`, `overrides_applied`, policy keys) and `llmenv resource-evidence --ledger-dir` (Task 9).
- Produces: `make resources`; `make resources-measure SERVICE=<omniroute|remote_setup|system_one|phoenix>` appends one sample to the container-lifetime ledger `${LLM_ENV_EVIDENCE_DIR:-<repo>/tmp/resource-evidence}/<service>-<container id[:12]>.json` and prints `saved <path> (<n> sample(s))`. Run it again later against the same container to extend the ledger; the gate needs samples a full 24 h apart.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_shell.py`:

```python
def _stub_uv(bin_dir: pathlib.Path, body: str) -> None:
    bin_dir.mkdir(exist_ok=True)
    uv = bin_dir / "uv"
    uv.write_text("#!/usr/bin/bash\n" + body)
    uv.chmod(uv.stat().st_mode | stat.S_IXUSR)


def _script_env(tmp_path: pathlib.Path, **extra: str) -> dict[str, str]:
    import os

    return {
        **os.environ,
        "PATH": f"{tmp_path / 'bin'}:{os.environ['PATH']}",
        "LLM_ENV_CONFIG": str(tmp_path / "models.yml"),
        **extra,
    }


def test_resources_script_prints_the_policy_table_and_summary(tmp_path: pathlib.Path) -> None:
    payload = {
        "llm_server": {"cpus": 0, "memory_mib": 0},
        "omniroute": {"cpus_min": 1, "cpus_max": 4, "memory_min_mib": 1024, "memory_max_mib": 2048},
        "remote_setup": {"cpus_min": 1, "cpus_max": 1, "memory_min_mib": 32, "memory_max_mib": 64},
        "system_one": {"cpus_min": 0, "cpus_max": 0, "memory_min_mib": 0, "memory_max_mib": 0},
        "phoenix": {"cpus_min": 0, "cpus_max": 0, "memory_min_mib": 0, "memory_max_mib": 0},
        "overrides_applied": {"omniroute": ["memory_max_mib"]},
        "summary": ["hard memory caps total 2112 MiB; soft reservations total 1056 MiB"],
    }
    (tmp_path / "models.yml").write_text("version: 1\n")
    _stub_uv(tmp_path / "bin", f"cat <<'EOF'\n{json.dumps(payload)}\nEOF\n")
    result = subprocess.run(
        ["/usr/bin/bash", "scripts/resources.sh"], cwd=ROOT, env=_script_env(tmp_path),
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "override: memory_max_mib" in result.stdout
    assert "remote_setup" in result.stdout and "default" in result.stdout
    assert "system_one" not in result.stdout.replace("service", "")  # disabled rows hidden
    assert "hard memory caps total 2112 MiB" in result.stdout


def test_resources_measure_script_appends_a_sample_to_the_ledger(tmp_path: pathlib.Path) -> None:
    ledger = tmp_path / "evidence" / "omniroute-44b8702af6cd.json"
    _stub_uv(
        tmp_path / "bin",
        "printf '%s\\n' \"$*\" >> \"$STUB_CALLS\"\n"
        f"printf '%s\\n' '{{\"ledger\": \"{ledger}\", \"samples\": 2}}'\n",
    )
    calls = tmp_path / "calls"
    result = subprocess.run(
        ["/usr/bin/bash", "scripts/resources-measure.sh"], cwd=ROOT,
        env=_script_env(tmp_path, SERVICE="omniroute", STUB_CALLS=str(calls),
                        LLM_ENV_EVIDENCE_DIR=str(tmp_path / "evidence")),
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"saved {ledger} (2 sample(s))" in result.stdout
    recorded = calls.read_text()
    assert "resource-evidence --service omniroute --ledger-dir" in recorded
    assert str(tmp_path / "evidence") in recorded


def test_resources_measure_script_reports_the_collector_error(tmp_path: pathlib.Path) -> None:
    _stub_uv(tmp_path / "bin", "printf '%s\\n' '{\"error\": \"boom\"}'\nexit 1\n")
    result = subprocess.run(
        ["/usr/bin/bash", "scripts/resources-measure.sh"], cwd=ROOT,
        env=_script_env(tmp_path, SERVICE="omniroute", LLM_ENV_EVIDENCE_DIR=str(tmp_path / "evidence")),
        capture_output=True, text=True, check=False,
    )
    assert result.returncode != 0
    assert "boom" in result.stdout + result.stderr
    assert not (tmp_path / "evidence").exists()


def test_resources_measure_script_requires_a_service(tmp_path: pathlib.Path) -> None:
    _stub_uv(tmp_path / "bin", "exit 0\n")
    result = subprocess.run(
        ["/usr/bin/bash", "scripts/resources-measure.sh"], cwd=ROOT,
        env=_script_env(tmp_path), capture_output=True, text=True, check=False,
    )
    assert result.returncode != 0
    assert "SERVICE" in result.stdout + result.stderr


def test_makefile_exposes_resources_targets() -> None:
    makefile = (ROOT / "Makefile").read_text()
    help_text = (ROOT / "scripts/help.sh").read_text()
    assert "bash scripts/resources.sh" in makefile
    assert "bash scripts/resources-measure.sh" in makefile
    assert '_help_line "resources"' in help_text
    assert '_help_line "resources-measure"' in help_text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_shell.py -q -k "resources_script or resources_measure or resources_targets"`
Expected: FAIL (scripts and targets do not exist).

- [ ] **Step 3: Implement**

Create `scripts/resources.sh`:

```bash
#!/usr/bin/env bash
# resources.sh — show the effective per-service CPU/RAM policy and active overrides.
set -euo pipefail
# shellcheck disable=SC1091 # Resolved from this script at runtime.
# shellcheck source=../tools/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/../tools/lib.sh"

require_cmd uv jq column

[ -f "$CONFIG_PATH" ] || die "no config at ${CONFIG_PATH}; run 'make setup' first"

resources_json="$(mktemp)"
trap 'rm -f "$resources_json"' EXIT
if ! llmenv --config "$CONFIG_PATH" resources > "$resources_json"; then
    die "$(jq -r '.error // "llmenv resources failed"' "$resources_json")"
fi

# Disabled services (all-zero policy) are hidden; llm_server is the computed remainder.
jq -r '
  . as $r
  | ["service","mem_min_mib","mem_max_mib","cpus_min","cpus_max","source"],
    (["omniroute","remote_setup","system_one","phoenix"][]
      | . as $n | $r[$n] | select(.memory_max_mib > 0)
      | [$n, .memory_min_mib, .memory_max_mib, .cpus_min, .cpus_max,
         (if $r.overrides_applied[$n] then "override: " + ($r.overrides_applied[$n] | join(",")) else "default" end)]),
    ["llm_server", 0, $r.llm_server.memory_mib, 0, $r.llm_server.cpus, "computed remainder"]
  | @tsv' "$resources_json" | column -t -s $'\t'
jq -r '.summary[]?' "$resources_json"
```

Create `scripts/resources-measure.sh`:

```bash
#!/usr/bin/env bash
# resources-measure.sh — append a read-only cgroup sample for SERVICE to its container ledger.
set -euo pipefail
# shellcheck disable=SC1091 # Resolved from this script at runtime.
# shellcheck source=../tools/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/../tools/lib.sh"

require_cmd uv jq

service="${SERVICE:-}"
[ -n "$service" ] || die "set SERVICE=omniroute|remote_setup|system_one|phoenix (make resources-measure SERVICE=omniroute)"

evidence_dir="${LLM_ENV_EVIDENCE_DIR:-${REPO_DIR}/tmp/resource-evidence}"
result="$(llmenv resource-evidence --service "$service" --ledger-dir "$evidence_dir")" \
    || die "evidence collection failed for ${service}: $(jq -r '.error // "unknown error"' <<<"$result")"
log_info "saved $(jq -r '.ledger' <<<"$result") ($(jq -r '.samples' <<<"$result") sample(s))"
```

In `Makefile`: run `sd -F 'system-one-benchmark \' 'system-one-benchmark resources resources-measure \' Makefile` (line 1 only matches), then insert before the `key-reset:` target (recipe lines start with a TAB):

```make
resources:
	@bash tools/run-target.sh resources -- bash scripts/resources.sh

resources-measure:
	@bash tools/run-target.sh resources-measure -- bash scripts/resources-measure.sh

```

In `scripts/help.sh`, insert before `_help_line "key-reset" ...`:

```bash
_help_line "resources" "Show the effective per-service CPU/RAM policy and active overrides"
_help_line "resources-measure" "Append a read-only cgroup evidence sample for SERVICE=<name> (tmp/resource-evidence/)"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_shell.py -q -k "resources_script or resources_measure or resources_targets"`
Expected: PASS. Then `make -n resources resources-measure` shows the two recipes.

- [ ] **Step 5: Validate and commit**

Run: `make validate && make test`
Expected: both succeed (shellcheck covers both new scripts).

```bash
git add scripts/resources.sh scripts/resources-measure.sh Makefile scripts/help.sh tests/test_shell.py
git commit -m "feat(make): report effective resource policy and record cgroup evidence samples"
```

---

### Task 11: Documentation

**Files:**
- Modify: `AGENTS.md` (command list after `make system-one-benchmark`; new subsection under `## Rules`)
- Modify: `.agents/architecture.md` (Files table; a short "Resource policy" paragraph)
- Modify: `models.yml.example` (stale `resources.omniroute` block; override example)
- Modify: any README/QUICK_START command list that names `system-one-benchmark` (found by `rg`)
- Test: `tests/test_docs.py`

- [ ] **Step 1: Find the places to update and any pinned doc strings**

Run: `rg -n "system-one-benchmark" README.md QUICK_START.md AGENTS.md .agents/architecture.md`
Run: `rg -n "models.yml.example" tests scripts setup tools Makefile`
Expected: the first lists every command listing to extend; the second tells you whether a test parses the example (if it does, run that test after the edit).

- [ ] **Step 2: Edit**

1. `AGENTS.md`: after the line `make system-one-benchmark    # Benchmark ...` insert

```
make resources               # Show the effective per-service CPU/RAM policy and active overrides
make resources-measure SERVICE=omniroute  # Append a read-only cgroup evidence sample (tmp/resource-evidence/)
```

   and add this subsection after `### Environment variables`' list (before `## Rules`):

```markdown
### Resource overrides

Per-service CPU/RAM policy defaults live in `pylib/resources.py`
(`DEFAULT_POLICY`). Persistent changes go in `models.yml` under
`resources.overrides.<service>` (`omniroute`, `remote_setup`, `system_one`,
`phoenix`). `resources.<service>` is a generated, informational mirror that
`make start` rewrites; no render path reads it, so never hand-edit it.
Manage overrides with `uv run llmenv.py resource-override show|set|check|clear`.
Values must be positive (0 would mean "no limit"). `set` and `clear` only edit
`models.yml`; running containers keep their current limits until you apply the
change. `bash setup/render-unit.sh` re-renders the compose file and unit for the
next start or boot and does not touch running containers (boot starts the last
rendered file). To apply to the running stack, run `make restart`: it stops and
starts OmniRoute, remote-setup, System One and (when System One is enabled)
Phoenix, so in-flight requests are interrupted, System One reloads its weights
before it is healthy, and every container begins a new cgroup lifetime
(`memory.peak`, counters and evidence samples start over). Run it when no agent
session is using OmniRoute.

Lowering a `memory_max_mib` below its default requires a `memory_max_evidence`
reference, written only by `resource-override set --evidence <ledger>`. The
ledger comes from `make resources-measure` (one sample per run; samples of one
container lifetime accumulate) and the gate requires: the evidence was read
from the container the repo declares for that service (compose container name,
service label, project label and project working directory, never a name taken
from the ledger), it is the running container
(id and start time) and its newest sample is under an hour old; at least 24 h
of container life and 24 h of samples with no new `memory.peak` (a
still-rising peak is refused); peak at most the application's own `highRatio`
of the cap; `memory.peak + memory.swap.peak` within the cap (`memory.max` does
not count swap, so the sum is a conservative upper bound on the memory demand,
not a measured simultaneous peak, and a cap below it pushes demand into swap);
`memory.events` max/oom/oom_kill all zero; and, judged over every sample of the
container lifetime rather than the newest one, the highest cgroup memory PSI
below the guard's `highPsiAvg10` and the guard thresholds present and
identical in every sample (the strictest are applied). The 24 h floor and the 0 < ratio <= 1 range cannot be changed
by flags. `validate_config` re-verifies the stored ledger (sha256 and the same
gate) on every command, so a hand edit of the cap or a changed evidence file is
rejected; `resource-override clear` always repairs it. The stored reference
holds the absolute path of the evidence copy under `~/.config/llm-env/evidence/`,
so it verifies from any working directory. Ledger samples are hash-chained: an
edited, removed or reordered sample is detected, but the chain holds no secret,
so the gate is a safety rail against mistakes and hand edits, not a security
boundary. A memory reservation is
a soft `memory.low` hint, and a lower cap releases no RAM -- it only bounds
growth.
```

2. `.agents/architecture.md`: add to the Files table
   `| pylib/resource_evidence.py | Read-only cgroup evidence ledger, the hard-memory-cap gate and evidence-reference verification |`,
   `| scripts/resources.sh | Effective per-service CPU/RAM policy table and active overrides (make resources) |`,
   `| scripts/resources-measure.sh | Appends a cgroup evidence sample for one service to its container ledger under tmp/resource-evidence/ (make resources-measure) |`,
   change the `pylib/resources.py` row to `Host CPU/RAM budgeting, per-service default policy, validated overrides`, and add a paragraph after the "Container Lifecycle" first paragraph:

```markdown
Resource policy is layered: `pylib/resources.py` holds the defaults and the
gate floors, `resources.overrides.<service>` in `models.yml` holds user intent
(a lowered memory cap also carries a `memory_max_evidence` reference to a
ledger under `~/.config/llm-env/evidence/`), and `llmenv render-compose`,
`scripts/phoenix.sh` and `scripts/check-setup.sh` recompute the effective
policy from those on every run. `resources.<service>` (written by
`scripts/start.sh` and `setup/setup.sh`) is only an informational mirror. Boot
(`setup/enable-boot.sh` -> `setup/render-unit.sh`) renders through the same
path and then starts the rendered compose file.
```

3. `models.yml.example`: delete the stale three lines

```yaml
  omniroute:
    cpus: 1
    memory_mib: 1024
```

   and after the `llm_server:` block under `resources:` add

```yaml
  # Optional persistent per-service overrides of the built-in policy
  # (pylib/resources.py DEFAULT_POLICY). Manage with `uv run llmenv.py
  # resource-override`. Values must be positive; 0 would mean "no limit".
  # A lowered memory_max_mib is written only by `resource-override set --evidence`.
  # overrides:
  #   omniroute:
  #     memory_max_mib: 4096   # raising a cap is free; lowering needs --evidence (make resources-measure)
```

4. Add the same two `make` lines to each README/QUICK_START listing found in Step 1.

- [ ] **Step 3: Verify**

Run: `uv run pytest tests/test_docs.py tests/test_config.py -q`
Expected: PASS.

- [ ] **Step 4: Validate and commit**

Run: `make validate && make test`
Expected: both succeed.

```bash
git add AGENTS.md .agents/architecture.md models.yml.example README.md QUICK_START.md
git commit -m "docs: document resource overrides, evidence and the new make targets"
```
(Stage only the files actually changed.)

---

### Task 12: Validate the OmniRoute cap candidate on the real host (operational)

No code changes; this produces evidence and a durable decision record. Do not change `OMNIROUTE_MEMORY_MAX_MIB`. Run the restart steps only when no agent session is using OmniRoute, because `make restart` interrupts in-flight requests. **A gate rejection is a valid outcome:** on the 2026-10-23 numbers (peak 1651.7 MiB, swap peak 602.6 MiB) the demand-bound rule (`memory.peak + memory.swap.peak`, a conservative upper bound on the anonymous demand, not a measured simultaneous peak) is expected to reject 2048 MiB, and a still-rising lifetime peak makes the gate refuse every candidate until a full 24 h passes without a new peak. In either case record the result and stop; never loosen the gate and never edit `models.yml` by hand. Adopting a candidate other than 2048 MiB (for example 2560 MiB) is a different decision that needs the user's explicit approval.

**Files:**
- Create (ephemeral): `tmp/resource-evidence/omniroute-<container id[:12]>.json` (via `make resources-measure`), `tmp/soak/sample.sh`, `tmp/soak/summarize.sh`, `tmp/soak/judge.sh`, `tmp/soak/host-pre.tsv`, `tmp/soak/host-post.tsv`, `tmp/soak/change.epoch`
- Create (durable): `docs/superpowers/research/2026-10-23-omniroute-memory-cap-validation.md`
- Do not copy `models.yml` anywhere (it holds secrets); `resource-override check` is the dry run and writes nothing.

- [ ] **Step 1: Verify the signals before relying on them, then write the criteria down**

```bash
G='highRatio:[0-9.]+,criticalRatio:[0-9.]+,recoveryRatio:[0-9.]+,highPsiAvg10:[0-9.]+,criticalPsiAvg10:[0-9.]+'
podman exec -u 0 omniroute sh -c "grep -hoE '$G' /app/.build/next/server/chunks/*.js | sort -u"
podman exec -u 0 omniroute sh -c "grep -hoE 'critical pressure guard tripped|heap pressure guard tripped' /app/.build/next/server/chunks/*.js | sort -u"
podman logs omniroute 2>&1 | rg -c '\[(STREAM|HealthCheck)\]'
podman logs omniroute 2>&1 | rg -c 'pressure guard tripped' || echo 0
cat "/sys/fs/cgroup$(podman inspect omniroute --format '{{.State.CgroupPath}}')/memory.pressure"
```
Expected: exactly one threshold line (`highRatio:.85,...,criticalPsiAvg10:60`); both log strings listed; a count greater than 0 for the `[STREAM]`/`[HealthCheck]` prefixes (console output reaches `podman logs`, so the guard's `console.warn` would too); `0` for the baseline guard count; `some`/`full` lines in `memory.pressure`. If the threshold line differs from the facts section, the image changed: use the new values in the criteria below. Do **not** use `resource_pressure` as the log pattern: it is the JSON error code of the 503 body and has 0 hits in the logs.

Create `docs/superpowers/research/2026-10-23-omniroute-memory-cap-validation.md` with a `## Criteria (written before measuring)` section containing exactly this, and commit it so the criteria predate the data:

```text
Pre window: the >= 24 h stability observation (Step 3). Post window: from the change timestamp (tmp/soak/change.epoch, written immediately before `make restart` in Step 5) through the END of the soak (Step 6), including any extension. A pass requires every criterion to hold over that whole range, never over only its last 24 h.
C0 coverage    : checked first and fails closed (tmp/soak/judge.sh). When it fails, C1-C5 are NOT evaluated and the result is INSUFFICIENT, never a pass. Each sampler file must hold only valid rows over its whole window: every field numeric (an NA, empty or unparseable value invalidates the row), no gap above 120 s between rows, a span of at least 24 h, at least 90% of the rows a 30 s sampler yields in 24 h (2592), a first row within 120 s of the window start, and (post window) a newest row under 120 s old while the soak runs. The container-PSI column of the post file may be NA or ABSENT only during the restart grace, the first 600 s after the change, while the container is recreated; at least 2592 rows must carry a numeric container reading. The post ledger must hold >= 3 samples taken after the change, the first within 2 h of it and the newest at least 24 h after it, and its container must have started after the change.
C1 host PSI    : post window host `some avg10` max < 30 (the guard's highPsiAvg10) AND post p95 <= pre p95 + 5.
C2 MemAvailable: post window host MemAvailable minimum >= 0.95 x the pre window minimum.
C3 container   : over the post window omniroute memory.events max/oom/oom_kill stay 0 in EVERY ledger sample taken since the change AND its cgroup `some avg10` max < 30 (the numeric container column of host-post.tsv, all rows since the change). A container PSI that was never read is not a pass: C0 requires the readings to exist.
C4 guard log   : `pressure guard tripped` count in `podman logs --since <change.epoch>` is 0 (verified baseline: 0) AND the `[STREAM]`/`[HealthCheck]` line count over the same range is > 0 (a log that holds no traffic proves nothing).
C5 comparability: record the harnesses/combos used in each window; a post window with visibly less load than the pre window does not count as a pass, extend it. Judged by hand.
A failed C1-C4 criterion means rollback (Step 7). INSUFFICIENT coverage is not a failed criterion and not a pass: a short window is extended and judged again; a damaged window (invalid rows, a gap, a stalled sampler) cannot be repaired by waiting and is recorded as inconclusive. Extending the soak widens the post window; it never resets it, and the criteria are re-judged over the whole widened range.
```

```bash
git add docs/superpowers/research/2026-10-23-omniroute-memory-cap-validation.md
git commit -m "docs(research): fix the OmniRoute memory cap validation criteria before measuring"
```

- [ ] **Step 2: Start the pre-change host sampler and take the baseline sample**

```bash
mkdir -p tmp/soak
cat > tmp/soak/sample.sh <<'EOF'
#!/usr/bin/env bash
# Scratch sampler (tmp/, not shipped): every 30 s append one row
# epoch, host MemAvailable kB, host SwapFree kB, host memory PSI some/full avg10, omniroute cgroup PSI some avg10.
# A failed read is written as NA. The container column is ABSENT while omniroute is not running (e.g. during
# `make restart`); it is never read from the root cgroup, which would silently report host-wide pressure.
set -u
out="$1"
while :; do
  state="$(podman inspect omniroute --format '{{.State.Running}} {{.State.CgroupPath}}' 2>/dev/null || true)"
  case "$state" in
    "true /"*) csome="$(awk '/^some/{sub("avg10=","",$2); print $2}' "/sys/fs/cgroup${state#true }/memory.pressure" 2>/dev/null || true)"
               csome="${csome:-NA}" ;;
    true*) csome=NA ;;
    *) csome=ABSENT ;;
  esac
  some="$(awk '/^some/{sub("avg10=","",$2); print $2}' /proc/pressure/memory 2>/dev/null || true)"
  full="$(awk '/^full/{sub("avg10=","",$2); print $2}' /proc/pressure/memory 2>/dev/null || true)"
  avail="$(awk '/^MemAvailable:/{print $2}' /proc/meminfo 2>/dev/null || true)"
  swap="$(awk '/^SwapFree:/{print $2}' /proc/meminfo 2>/dev/null || true)"
  printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$(date +%s)" "${avail:-NA}" "${swap:-NA}" "${some:-NA}" "${full:-NA}" "$csome" >> "$out"
  sleep 30
done
EOF
cat > tmp/soak/summarize.sh <<'EOF'
#!/usr/bin/env bash
# usage: summarize.sh FILE FROM_EPOCH REQUIRED_SECONDS LIVE GRACE_SECONDS
#   FROM_EPOCH  window start (0 = the first row; the window-start check is skipped)
#   LIVE        1 = the sampler must still be running (newest row under 120 s old)
#   GRACE       seconds after FROM_EPOCH in which the container column may be NA/ABSENT (restart)
# Prints key=value pairs. Exits 0 ONLY for a complete, valid window; an empty, short, gapped or damaged
# window prints window=INSUFFICIENT with the reasons and exits 1 (it never prints passing-looking zeros).
set -u
file="${1:?file}"; from="${2:-0}"; need="${3:-86400}"; live="${4:-0}"; grace="${5:-0}"
[ -r "$file" ] || { echo "window=INSUFFICIENT reason=unreadable"; exit 1; }
awk -F'\t' -v from="$from" -v need="$need" -v live="$live" -v grace="$grace" -v now="$(date +%s)" '
function isnum(v) { return v ~ /^[0-9]+([.][0-9]+)?$/ }
BEGIN { interval = 30; maxgap = 120 }
{
  if (isnum($1) && $1 + 0 < from) next
  if (NF != 6 || !isnum($1) || !isnum($2) || !isnum($3) || !isnum($4) || !isnum($5)) { bad++; next }
  t = $1 + 0
  if (isnum($6)) { c++; if ($6 + 0 > cmax) cmax = $6 + 0 }
  else if (t > from + grace) { bad++; next }
  n++
  if (n == 1) { first = t; min = $2 + 0 }
  else { if (t - prev > maxg) maxg = t - prev; if ($2 + 0 < min) min = $2 + 0 }
  prev = t; h[n] = $4 + 0; if ($4 + 0 > hmax) hmax = $4 + 0
}
END {
  want = int(need / interval * 0.9); why = ""
  if (bad > 0) why = why " invalid_rows(" bad ")"
  if (n == 0) why = why " no_valid_rows"
  else {
    if (from > 0 && first - from > maxgap) why = why " starts_late(" first - from "s)"
    if (prev - first < need) why = why " span_short(" prev - first "s<" need "s)"
    if (n < want) why = why " too_few_rows(" n "<" want ")"
    if (c < want) why = why " too_few_container_readings(" c + 0 "<" want ")"
    if (maxg > maxgap) why = why " gap(" maxg "s)"
    if (live == 1 && now - prev > maxgap) why = why " sampler_stalled(" now - prev "s)"
  }
  asort(h); i = int(n * 0.95); if (i < 1) i = 1
  printf "window=%s samples=%d invalid=%d container_readings=%d span_s=%d max_gap_s=%d min_MemAvailable_MiB=%d host_some_avg10_max=%s host_some_avg10_p95=%s container_some_avg10_max=%s reason=%s\n",
    (why == "" ? "OK" : "INSUFFICIENT"), n, bad, c, (n ? prev - first : 0), maxg, min / 1024, hmax + 0, h[i] + 0, cmax + 0, (why == "" ? "-" : substr(why, 2))
  exit (why == "" ? 0 : 1)
}' "$file"
EOF
cat > tmp/soak/judge.sh <<'EOF'
#!/usr/bin/env bash
# usage: bash tmp/soak/judge.sh LEDGER   (from the repo root)
# Fails closed. C0 (coverage) is checked first; C1-C4 are evaluated only on a complete window.
# Exit 0 = C1-C4 pass (C5 stays a manual judgment), 1 = a criterion failed, 2 = INSUFFICIENT coverage.
set -u
ledger="${1:?ledger path}"; need=86400; grace=600
change="$(cat tmp/soak/change.epoch 2>/dev/null || true)"
case "$change" in ''|*[!0-9]*) echo "C0 INSUFFICIENT: tmp/soak/change.epoch is missing or not an epoch"; exit 2 ;; esac
field() { tr ' ' '\n' <<<"$1" | sed -n "s/^$2=//p"; }

short=0
pre="$(bash tmp/soak/summarize.sh tmp/soak/host-pre.tsv 0 "$need" 0 0)" || { echo "C0 INSUFFICIENT pre window: $pre"; short=1; }
post="$(bash tmp/soak/summarize.sh tmp/soak/host-post.tsv "$change" "$need" 1 "$grace")" || { echo "C0 INSUFFICIENT post window: $post"; short=1; }
cov="$(jq -c --argjson c "$change" --argjson need "$need" '
  [.samples[] | select(.collected_at_epoch >= $c)] as $s
  | {started_after_change: (.started_at_epoch >= $c), samples_since_change: ($s | length),
     first_after_s: ($s[0].collected_at_epoch - $c), last_after_s: ($s[-1].collected_at_epoch - $c)}
  | . + {ok: (.started_after_change and .samples_since_change >= 3 and .first_after_s <= 7200 and .last_after_s >= $need)}' "$ledger" 2>/dev/null)" || cov="ledger unreadable or holds no samples taken after the change"
jq -e '.ok == true' <<<"$cov" >/dev/null 2>&1 || { echo "C0 INSUFFICIENT ledger: $cov"; short=1; }
if [ "$short" -ne 0 ]; then echo "INSUFFICIENT: C1-C5 were not evaluated; this is not a pass"; exit 2; fi
echo "C0 OK pre:  $pre"; echo "C0 OK post: $post"; echo "C0 OK ledger: $cov"

fails=0
verdict() { local name="$1"; shift; if "$@"; then echo "$name PASS"; else echo "$name FAIL"; fails=1; fi; }
# shellcheck disable=SC2329 # invoked through verdict "$@"
num() { awk -v a="$1" -v b="$2" "BEGIN{exit !(a $3 b)}"; }

verdict "C1 host PSI max < 30" num "$(field "$post" host_some_avg10_max)" 30 "<"
verdict "C1 host PSI p95 <= pre p95 + 5" num "$(field "$post" host_some_avg10_p95)" "$(awk -v p="$(field "$pre" host_some_avg10_p95)" 'BEGIN{print p + 5}')" "<="
verdict "C2 MemAvailable min >= 0.95 x pre min" num "$(field "$post" min_MemAvailable_MiB)" "$(awk -v p="$(field "$pre" min_MemAvailable_MiB)" 'BEGIN{print 0.95 * p}')" ">="
events="$(jq --argjson c "$change" '[.samples[] | select(.collected_at_epoch >= $c) | .memory_events | .max + .oom + .oom_kill] | add' "$ledger")"
echo "C3 ledger memory.events max+oom+oom_kill over every sample since the change: $events"
verdict "C3 events are 0" num "$events" 0 "=="
verdict "C3 container PSI max < 30" num "$(field "$post" container_some_avg10_max)" 30 "<"
guard="$(podman logs --since "$change" omniroute 2>&1 | rg -c 'pressure guard tripped')"; guard="${guard:-0}"
traffic="$(podman logs --since "$change" omniroute 2>&1 | rg -c '\[(STREAM|HealthCheck)\]')"; traffic="${traffic:-0}"
echo "C4 guard trips since the change: $guard; traffic lines in the same range: $traffic"
verdict "C4 guard trips are 0" num "$guard" 0 "=="
verdict "C4 the log holds traffic" num "$traffic" 0 ">"
echo "C5 comparability: judge by hand (harnesses and combos used in each window)"
exit "$fails"
EOF
chmod +x tmp/soak/sample.sh tmp/soak/summarize.sh tmp/soak/judge.sh
systemd-run --user --unit=llm-env-soak-sampler --collect --working-directory="$PWD" bash tmp/soak/sample.sh tmp/soak/host-pre.tsv
make resources-measure SERVICE=omniroute
```
Expected: the sampler unit is active (`systemctl --user is-active llm-env-soak-sampler`), `tmp/soak/host-pre.tsv` grows every 30 s with six numeric fields per row (a failed read is written as `NA` and later invalidates the window; the container column is `ABSENT`, not the root cgroup's pressure, whenever OmniRoute is not running), and `make resources-measure` prints `saved .../omniroute-44b8702af6cd.json (1 sample(s))` (the container id prefix differs if the container was recreated; the ledger belongs to that one container lifetime).

- [ ] **Step 3: Observe for at least 24 h without restarting OmniRoute, then take the second sample**

Use the harnesses normally, including the tool-heavy combos that tripped the 1 GiB limit (`my-coding`, `my-plan-review`), and note which ones in the research document. If OmniRoute restarts, the ledger belongs to the old container and this step starts over. After >= 24 h:

```bash
make resources-measure SERVICE=omniroute
ledger="$(ls -t tmp/resource-evidence/omniroute-*.json | head -n 1)"
jq '{samples: (.samples | length), hours_of_life: (((.samples[-1].collected_at_epoch - .started_at_epoch) / 3600) | floor), first_peak_mib: (.samples[0].memory_peak_bytes / 1048576 | floor), last: (.samples[-1] | {peak_mib: (.memory_peak_bytes / 1048576 | floor), swap_peak_mib: (.memory_swap_peak_bytes / 1048576 | floor), events: .memory_events, psi_some: .memory_pressure.some, guard})}' "$ledger"
bash tmp/soak/summarize.sh tmp/soak/host-pre.tsv 0 86400 0 0
```
Expected: the `summarize.sh` line starts with `window=OK` (a continuous, valid baseline of at least 24 h from the Step 2 sampler; `window=INSUFFICIENT` with its reasons means the baseline cannot be used, so wait for a full 24 h or, if rows are invalid or gapped, restart the observation with a new sampler file); `guard` is `{"high_ratio": 0.85, "critical_ratio": 0.92, "high_psi_avg10": 30, "critical_psi_avg10": 60, "psi_disabled": false}`; `events.max/oom/oom_kill` are 0; `first_peak_mib` equals `last.peak_mib` only if the lifetime peak stopped rising (if it differs, the gate will refuse: let the container run another full window and repeat this step; the ledger accumulates). If `guard` is `null`, the image changed or the probe failed: stop and investigate before any gate run.

- [ ] **Step 4: Dry-run the gate (writes nothing)**

The newest sample must be under an hour old, so measure immediately before checking:

```bash
make resources-measure SERVICE=omniroute
ledger="$(ls -t tmp/resource-evidence/omniroute-*.json | head -n 1)"
for cap in 1024 2048 2560; do
  echo "== candidate ${cap} MiB"
  uv run llmenv.py resource-override check --service omniroute --memory-max-mib "$cap" --evidence "$ledger"; echo "exit=$?"
done
```
Expected on the 2026-10-23 numbers with a stable peak: 1024 rejected (ratio 1.61 and the demand bound); **2048 rejected** with exactly one reason about `memory.peak + memory.swap.peak` (peak ratio 0.806 passes the 0.85 guard ratio; the sum, a conservative upper bound on the demand rather than a measured simultaneous peak, is 1.10 of the cap); 2560 accepted (0.645 resident, 0.881 with swap). If the peak rose between samples every candidate shows `without a new peak`: wait and repeat Step 3. Record all three JSON results. If 2048 is rejected the plan's candidate is **not justified on current evidence**: skip to Step 8 and record "not adopted", plus the 2560 result as information. Do not proceed to Step 5 with any other cap unless the user explicitly approves that cap.

- [ ] **Step 5: Adopt an accepted, user-approved cap, restart, verify**

Replace `CAP` with the approved value (2048 only if the gate accepted it). `resource-override set` only writes `models.yml`; the cap reaches the running container only through `make restart`, which stops and starts the whole stack (OmniRoute, remote-setup, System One and, when enabled, Phoenix), interrupts in-flight requests and gives OmniRoute a new cgroup lifetime. The post-change window starts at the timestamp written just before that restart, and the post sampler is started before it so there is no gap.

```bash
systemctl --user stop llm-env-soak-sampler
make resources-measure SERVICE=omniroute
ledger="$(ls -t tmp/resource-evidence/omniroute-*.json | head -n 1)"
uv run llmenv.py resource-override set --service omniroute --memory-max-mib CAP --evidence "$ledger"
make resources
date +%s > tmp/soak/change.epoch   # the change timestamp: the post window runs from here to the END of the soak
systemd-run --user --unit=llm-env-soak-sampler --collect --working-directory="$PWD" bash tmp/soak/sample.sh tmp/soak/host-post.tsv
make restart
podman inspect omniroute --format '{{.HostConfig.Memory}}'
```
Expected: `set` prints `"accepted": true`, stores the ledger copy under `~/.config/llm-env/evidence/` and an `apply` hint that says running containers are unchanged until `make restart`; `make resources` shows `omniroute ... CAP ... override: memory_max_mib` and a hard-cap total `3072 - CAP` MiB lower than before; `podman inspect` prints `CAP * 1048576` (2147483648 for 2048) only AFTER the restart. `tmp/soak/change.epoch` holds one epoch value. Leave the post sampler running until the final judgment in Step 6.

- [ ] **Step 6: Soak for at least 24 h under normal and tool-heavy use, then judge coverage first and C1-C4 second**

Use the same harnesses as in Step 3 and note them. Run the measure command below once right after the restart and about every 12 h, so the ledger holds at least three samples taken after the change (the first within 2 h of it). After >= 24 h (extend until the peak has been stable for 24 h of samples, because this is a new container lifetime), run the judgment block. **If the soak is extended, keep the sampler running and run the judgment block again at the new end: every number is judged from the change timestamp through the final end of the soak, never from a trailing slice (`podman logs --since 24h` would silently drop the earlier part of a longer soak).**

```bash
make resources-measure SERVICE=omniroute
ledger="$(ls -t tmp/resource-evidence/omniroute-*.json | head -n 1)"
uv run llmenv.py resource-override check --service omniroute --memory-max-mib CAP --evidence "$ledger"; echo "check exit=$?"
bash tmp/soak/judge.sh "$ledger"; echo "judge exit=$?"
```
When the judgment is final (and only then, because `judge.sh` also checks that the sampler is still running): `systemctl --user stop llm-env-soak-sampler`.

`judge.sh` fails closed. It first checks C0 coverage and prints one line per problem: the pre and post sampler files must hold only valid rows (no `NA`, empty or unparseable field, except the container column during the 600 s restart grace), no gap above 120 s, a span of at least 24 h from the change, at least 2592 rows with a numeric container-PSI reading, a first row within 120 s of the change and a newest row under 120 s old; the ledger must hold at least three samples taken after the change from a container that started after it, the newest at least 24 h after the change. An empty, short, gapped or damaged window exits **2** with `INSUFFICIENT: C1-C5 were not evaluated; this is not a pass`; the zero defaults of an empty summary can never read as success. A merely short window (`span_short`, `too_few_rows`, `too_few_container_readings`, or a ledger that still lacks its third or 24 h post-change sample) is extended and judged again. A damaged window (`invalid_rows`, `gap`, `starts_late`, `sampler_stalled`, or a ledger whose container started before the change) cannot be repaired by waiting: record it as inconclusive and go to Step 7 (or, only with the user's approval, repeat Step 5 and this soak). Only a complete window reaches C1-C4, each printed as `PASS` or `FAIL`; exit **1** means a criterion failed (Step 7), exit **0** means C1-C4 passed over the whole window and C5 is still judged by hand.

Expected on a healthy complete window: `check` still reports `"accepted": true` against the new container's own `memory.peak` (it judges every sample of this lifetime, so a pressure spike anywhere after the restart fails it); `judge.sh` prints `C0 OK` for the pre window, the post window and the ledger (`started_after_change` true, `samples_since_change` >= 3, `last_after_s` >= 86400), the guard count since the change is `0` while the traffic count over the same range is above 0 (a zero guard count on a log that holds no traffic proves nothing), the events sum is 0 across every sample since the change, and C1-C3 pass exactly as written in the criteria, computed from the change timestamp. A guard trip that happened and then stopped still fails C4: the criterion is the count over the whole post window, not the current state.

- [ ] **Step 7: Rollback path (if a criterion failed or the host misbehaves)**

```bash
uv run llmenv.py resource-override clear --service omniroute
make restart
podman inspect omniroute --format '{{.HostConfig.Memory}}'
```
Expected: `3221225472` (the 3072 MiB default is back). The stored evidence copy under `~/.config/llm-env/evidence/` is kept as an audit trail.

- [ ] **Step 8: Record the decision**

Complete `docs/superpowers/research/2026-10-23-omniroute-memory-cap-validation.md` with real values pasted in: for every ledger the file name and `sha256sum`; the Step 4 `check` JSON for every candidate (including the 2048 demand-bound rejection or its acceptance); the change timestamp, the observation and soak durations (including every extension, with the final end time); the harnesses and combos used in each window; the full `judge.sh` output (the C0 coverage lines with row counts, spans and the ledger summary, the pre and post `summarize.sh` lines computed from the change timestamp, the guard-log count since the change, the events over all samples since the change and the `[STREAM]`/`[HealthCheck]` line count over the same range, which is the proof that the log covers the window); the verdict per criterion C0-C5 (a C0 `INSUFFICIENT` is recorded as inconclusive, never as a pass); and the outcome (not adopted, adopted as a persistent override with the cap and reason, or rolled back). Promoting an accepted cap into `OMNIROUTE_MEMORY_MAX_MIB` is a separate change that needs explicit user approval; if approved it is a one-constant edit plus updating `test_baseline_hard_memory_caps_sum_to_8768_mib` to `8768 - (3072 - CAP)` (7744 for 2048).

```bash
git add docs/superpowers/research/2026-10-23-omniroute-memory-cap-validation.md
git commit -m "docs(research): record the OmniRoute memory cap validation result"
```

---

## Self-Review

**Spec coverage (research section 7 and section 2):**
- "Persistent per-service override support is missing": Tasks 1-5 (table, validation, budget, config, one render path).
- "Validated persistent overrides": Tasks 1, 3 and 8 (structure, then the evidence file behind a lowered cap).
- "Clear effective-policy reporting": Task 2 (`overrides_applied`, `totals`, `summarize_policy`), Task 4 (`summary`), Task 6 (start output), Task 10 (`make resources`).
- "Proof that ordinary start/boot preserve those choices": Task 5 (`render-unit.sh` ignores a stale mirror, `phoenix.sh resource-env`), Task 6 (`start` set and cleared, with the stale mirror), Task 9 (`set` -> `render-unit.sh` -> `clear` -> `render-unit.sh` through the real CLI). Boot is `enable-boot.sh` -> `render-unit.sh`, not `start.sh`; both reach `llmenv render-compose`.
- "OmniRoute 2 GiB unvalidated; do not hardcode": the default stays 3072; Tasks 7-9 enforce container-bound, age-limited evidence (declared-container binding, 24 h floor, no-new-peak window, strictest guard ratio, demand bound, PSI over every sample, events) for any reduction on every write path; Task 12 runs the gate, treats a rejection as a valid outcome, records criteria before measuring and judges logs and counters from the change timestamp through the end of any extended soak.
- "Reservations are soft / hard cap sum is not RAM": `summarize_policy` wording, AGENTS.md text and the totals test (8768).
- "Monitoring stack / Laya / Phoenix sizing": out of scope; Laya and Phoenix defaults are untouched, and both can now be overridden and gated the same way when evidence exists. Representative-load capture (50 requests, zram) remains research, not implemented here.
- "Durable requirement source": Task 0.

**Placeholder scan:** no TBD/TODO; every code step has full code; Task 11's doc edits give exact text; the only values left to the executor are measured numbers pasted into the Task 12 record, and `CAP`, which the user approves.

**Type and name consistency:** `DEFAULT_POLICY`, `POLICY_KEYS`, `OVERRIDABLE_SERVICES`, `EVIDENCE_KEY`, `MIN_OBSERVATION_HOURS_FLOOR`, `MAX_PEAK_RATIO_CEILING`, `validate_overrides`, `effective_policy`, `summarize_policy`, `limits_for`, `collect_evidence`, `record_evidence`, `parse_evidence`, `load_evidence`, `evaluate_recorded_evidence`, `evaluate_memory_cap`, `build_reference`, `verify_evidence_reference`, `live_identity(service)`, `Declared`, `SERVICE_CONTAINERS`, `MAIN_STACK_PROJECT`, `seal_samples`, `store_bytes`, `parse_guard`, `EvidenceError` are spelled identically in every task. Evidence-reference keys (`path`, `sha256`, `container_id`, `started_at_epoch`, `collected_at_epoch`, `peak_bytes`, `swap_peak_bytes`, `candidate_mib`, `max_peak_ratio`, `min_observation_hours`) match between Task 1 (shape), Task 7 (`build_reference`/`verify_evidence_reference`), Task 8 (config tests) and Task 9 (CLI). The `system_one` result shape change is introduced in Task 2 and every consumer (two old assertions, no scripts) is covered by its Step 1 `rg`. `render_compose`/`write_compose` gain a required `limits` keyword in Task 5 and every direct caller is listed there.

**Review Focus coverage:** 1-2 Tasks 1/3 parametrized tests; 3 Task 2 `test_override_of_a_disabled_service_is_inert`; 4 Task 2 and Task 4 tests; 5 Tasks 5, 6, 9 (`render-unit`, `start`, `phoenix.sh resource-env`); 6 Tasks 1, 8, 9 (hand edit, missing/edited file, `clear` repair); 7 Tasks 7 and 9 (container binding, age, window, rising peak, counters, types, missing swap/PSI); 8 Tasks 7 and 9 (flag floors, ratio ceiling, guard missing); 9 Task 7 demand-bound and PSI tests; 10 Task 12 Steps 1-6 (`judge.sh` C0 coverage exits 2 on NA, missing, short, gapped or stalled windows and on a ledger without post-change samples, before C1-C4 are evaluated); 11 Tasks 7 and 9 (declared-container table pinned to the rendered compose, main-stack project derived from `tools/lib.sh` and the unit, project and working-directory labels pinned for every service, ledger naming another workload, label mismatch); 12 Task 7 (spike in a non-newest sample, a guard missing or different in one sample); 13 Tasks 9, 11 and 12 (`_APPLY_HINT`, AGENTS.md text, change timestamp and whole-window judgment); 14 Tasks 1, 7, 8 and 9 (absolute path shape, relative `--config`, verification from any working directory); 15 Task 7 (hash-chain tamper cases, refusal to extend a cut ledger) and Task 8 (config rejects a broken chain).

**Known limits, stated rather than hidden:** the evidence gate is a safety rail against mistakes and hand edits, not a security boundary. The ledger's hash chain makes silent loss visible (an edited, removed or reordered sample fails the loader and `record_evidence` refuses to extend such a ledger; truncating the tail or replacing the file fails the reference's sha256), but the chain holds no secret, so someone who recomputes the whole chain and the sha256 on purpose can still forge history; the ledger is replaceable by design and the gate's assurance is exactly "detects accidents and casual edits"; PSI averages are judged as the maximum over every recorded sample of the container lifetime plus the soak's continuous host sampling, so a transient spike between samples can still go unseen by the gate (a sample is a 10/60/300 s average at one instant) and only the soak criteria cover it; the generated `resources.<service>` mirror still exists and is still rewritten by start/setup (removing it is a follow-up); a changed override reaches boot only after the next render.

---

## Addendum 2026-10-24: evidence gate SUPERSEDED/REMOVED

Owner decision, 2026-10-24. The evidence gate could not accept any real lowered cap: its 24 h stable-peak
window, the running-container identity check and the `highRatio` bound together refused every cap below
the default that live data supported, so it only ever blocked. The system is now: benchmark -> numbers ->
`DEFAULT_POLICY` or per-user overrides. The sections below are kept for history; these parts are
**SUPERSEDED/REMOVED**:

- **Task 7** (the evidence ledger, `pylib/resource_evidence.py`): REMOVED.
- **Task 8** (enforce the evidence gate on every write path, `validate_config` re-verification of the
  stored ledger): REMOVED. Lowering `memory_max_mib` below the default is allowed and prints a warning
  pointing at `make resources-benchmark`.
- **Task 9**, evidence parts only (`resource-evidence` subcommand, `resource-override set --evidence`,
  `--max-peak-ratio`, `--min-observation-hours`): REMOVED. `resource-override show|set|check|clear` stays.
- **Task 10**, `make resources-measure` and `scripts/resources-measure.sh`: REMOVED, replaced by
  `make resources-benchmark` (`scripts/resources-benchmark.sh`). `make resources` stays.
- **Task 12** (validating the OmniRoute cap through the gate/soak judge): SUPERSEDED by the benchmark
  derivation in `docs/superpowers/research/2026-10-24-benchmark-derived-sizing.md`.
- The `memory_max_evidence` override field: REMOVED; a stale one is dropped silently on load.
- The test pin of a 3072 MiB OmniRoute default (`conftest` gate scenarios): REMOVED; tests assert the
  real defaults.
- The generated `resources.<service>` mirror written by `scripts/start.sh` and `setup/setup.sh`: REMOVED;
  it is dropped on load (`resources.llm_server` stays).

Also changed on 2026-10-24: Phoenix `memory_max_mib` 960 -> 1024 (live peak 804 MiB + 20 %); `system_one`
is documented as an explicit exception to the sizing rule; the OmniRoute healer threshold defaults to 60 %
of the container's `memory.max` (1228 MiB at 2048).
