# System One updater, weekly check and `system-one-scout` skill

Status: approved direction (owner, 2026-11-07): an updater like update-omniroute, swapped while idle with automatic rollback;
a weekly report-only check; a dedicated skill written test-first with superpowers:writing-skills. Reuse and refactor
before adding code (ponytail).

## Context

- Laya is built locally from a PyPI pin (`ARG LAYA_VERSION=0.4.2` in `docker/system-one/Dockerfile`). Upstream is at
  0.4.27 (25 releases, about 155 fixes). The HF weights did not change: the `model.safetensors` hash at the pinned SHAs
  equals `main`, so an update changes code only.
- There is no registry image, so OmniRoute's skopeo digest resolve does not apply. The running container is the observer
  derivative `localhost/llm-env-system-one-phoenix:<sha12(base id + observer source)>`, rebuilt by `setup/render-unit.sh`
  from `system_one.image`.
- Laya's `/health` gives the idle signal directly (`loaded`, `idle_seconds`). OmniRoute's observer/profile machinery is not
  needed.
- The old base image stays on disk and the weights stay in the cache volume, so rollback is a retag plus a recreate.

## Architecture

The shared core already exists in `pylib/omniroute_update.py` (`preflight --service`, `projection --service`,
`verify-commit --service`, `config_drift`). Its bash half, stop/rm/name-check/up, is duplicated in
`scripts/omniroute-commit-worker.sh` and `scripts/migrate-remote-setup.sh`.

1. Extract `replace_service` (stop by ID, rm by ID, refuse a same-name leftover, `podman-compose up --no-deps
   --no-recreate --no-build --pull never <service>`) into a sourced `scripts/service-replace.sh`. The three callers use it:
   the OmniRoute commit worker, the remote-setup migration and the new System One updater. Each caller keeps its own
   `fail` and verification.
2. `preflight` computes `drift` for every service, not only `omniroute`.
3. `scripts/update-system-one.sh` is the System One-specific part only:
   - the target version (argument, or the newest version from the check script);
   - building the candidate under `:rocm-cand`;
   - prefetching the weights with the candidate;
   - a CPU smoke test in a throwaway container;
   - the `/health` idle gate;
   - promoting the tags and re-rendering;
   - the shared preflight/projection, then `replace_service`, then verify-commit;
   - a `/v1/systemone` smoke test;
   - rollback;
   - rewriting the Dockerfile pin.
   Exit codes and output lines follow update-omniroute: 0 for `unchanged` or `updated`, 75 for `deferred: <reason>`, 1 for an error.
4. `scripts/system-one-check.sh` moves from the onboard-local-model skill to `scripts/`, because product code depends on it.
   It gains a weights-hash signal and `--save` (writes `~/.local/state/llm-env/system-one-check.json`). A weekly
   systemd user timer (`setup/enable-system-one-check.sh`, same shape as the heal timer) runs it. `make status` prints one line from it.
5. The skill `.claude/skills/system-one-scout` holds the judgment parts: reading the release-note gap, deciding between
   bump and switch, and the alternatives eval. The System One section of onboard-local-model moves there.

## Steps

### A. Shared core refactor (TDD; behavior unchanged)
- [x] `scripts/service-replace.sh` with `replace_service`; the OmniRoute worker and the remote-setup migration call it.
  Existing tests (`tests/test_update_omniroute_script.py`, the migration tests) stay green.
- [x] `preflight`: drift for every service (a unit test for a non-omniroute service).

### B. `make update-system-one [LAYA_VERSION=x.y.z]`
- [x] Tests first (`tests/test_update_system_one_script.py`, stub podman/curl/uv on PATH):
  - same version → `unchanged`, no mutations;
  - busy until the deadline → 75, candidate built, production untouched;
  - lock contention → 75;
  - a CPU smoke failure → error, production untouched;
  - post-swap verify failure → rollback (`rocm-prev` retagged, recreated);
  - success → Dockerfile pin rewritten.
- [x] `scripts/system-one-build.sh`: optional `LAYA_VERSION` and `SYSTEM_ONE_BUILD_TAG` env overrides.
- [x] `scripts/update-system-one.sh` and the Makefile target (through `tools/run-target.sh`).
- [x] AGENTS.md / README command list and env vars.

### C. Weekly check
- [x] Move `system-one-check.sh` and its tests. Add the weights hash and `--save`.
- [x] `setup/enable-system-one-check.sh`, called from `scripts/start.sh` only when System One is enabled; the `make status` line.

### D. `system-one-scout` skill (superpowers:writing-skills, RED → GREEN → REFACTOR)
- [x] RED: baseline scenarios without the skill, run in subagents:
  1. "Is Laya outdated? Should I update it?"
  2. "Is there something better than Laya under 5 GB?"
  3. "Update Laya now" while an agent is using it.
  Record the failures verbatim.
- [x] GREEN: a minimal SKILL.md and references (`upgrade.md` moved from onboard-local-model, `alternatives.md` from the
  2026-11-07 research), addressing only the recorded failures. Re-run the scenarios.
- [x] REFACTOR: close loopholes and run skill-judge.
- [ ] Eval harness (`uv run llmenv.py system-one-eval`) and the llama.cpp logprob adapter: a separate follow-up plan once
  the skill exists (skill-judge D8 finding: runs re-derive the measurement loop).

## Outcome (2026-11-07)

- Shared core: `scripts/service-replace.sh` (`replace_service`), used by the OmniRoute commit worker, the remote-setup
  migration and the System One updater. `install_user_timer` and `system_one_cache_check` in `tools/lib.sh` replaced
  two copied timer installers and two copied weight-check `podman run`s. `preflight` reports drift for every service.
- `make update-system-one` also applies `system_one` config drift through the same idle gate. This came out of GREEN
  scenario 2, which otherwise fell back to `make restart` for a `typed-decisions` trial.
- `system-one-check.sh` gained `weights` (reviewed revisions moved?) and `dependencies` (installed deps added/removed). The live run flags
  `vegaml` (0.4.24+), whose default vega lane fails offline on states over 8,192 tokens; 0.4.22 is the last release
  without it.
- Skill RED baselines: all three behaved well without a skill. The failures were no dependency diff, verdicts on
  published claims, and repeating the web search on every run. GREEN: both re-runs used the skill and closed those gaps.
  skill-judge: 80.8 % B before the REFACTOR fixes (trigger queries, NEVER reasons, target-version choice).
- First live run (2026-11-07) failed at the CPU smoke: the smoke's dummy `LAYA_PHOENIX_URL` failed the observer's
  allowlist, which the stubbed tests could not see. Fixed to the allowlisted `127.0.0.1:6006` spans URL, with a test
  that runs every URL in the script through the real exporter check.
- Live (2026-11-15): the owner chose the newest releases. `updated laya 0.4.30 -> 0.4.31` after one retry of a torn
  torch wheel download; `:rocm-prev` holds 0.4.30. The vega lane caveat applies (long states fail offline).

## Verification
- `make validate && make test`.
- Live, while no agent uses Laya: `make update-system-one LAYA_VERSION=0.4.27`. Then check that the container is healthy on
  the new image, `make check-server` passes, `make resources-snapshot` shows OK, and one `jev_review.py ladder` call
  answers through laya.
