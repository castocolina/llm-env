# Phoenix normal-start integration implementation plan

> **For agentic workers:** Use test-driven development and the supplied invocation brief. Work sequentially in the current main checkout; no worker Git mutations or extra reviewer seats.

**Goal:** Make the existing Phoenix dashboard usable through normal setup/start/stop/status when Laya is enabled, without TMPDIR workarounds or a synthetic-sender build for basic startup.

**Architecture:** Keep Phoenix rootless, loopback-only and on its own internal network; integrate its lifecycle with the existing normal lifecycle and boot wrapper. Existing services already share a native Podman pod, so adding an internal network to a service in that pod is not by itself network isolation. Prefer a separately owned Phoenix compose project managed by existing lifecycle hooks, not another resident controller. Keep deterministic synthetic fixture/recovery tools for explicit tests, not ordinary startup.

**Tech stack:** Existing Bash/Podman/systemd, Python through `uv run llmenv.py`, PyYAML/stdlib; no new dependencies or global installs.

**Requirements source:** User's October 19 default-up failure report and request for normal startup conditional on Laya, fewer `-trial-*` commands, actual resource bounds and Kimi implementation; user explicitly identifies `kimi-coding/kimi-for-coding` as their requested Kimi 2.8-preview alias. `openai/gpt-6-luna:high` is permitted ONLY after actual Kimi failure. This new scope supersedes the standalone-only lifecycle clauses of the preserved October 18 plan/spec, not their data integrity/privacy requirements.

## Global constraints

- Preserve every existing plan/spec and unrelated updater script/test draft; new scratch stays repository `./tmp/`.
- No Git staging/commits/reset/stash/clean/branches/worktrees, production service restart, production config/credentials/log/DB access or Laya inference by the worker.
- Rootless internal network, loopback publication, exact pinned Phoenix 20.23.0 image; no GPU, socket, broad host mount, privileged mode, external providers or telemetry.
- Normal startup does not seed fake history or instrument production Laya. State explicitly that startup alone does not collect Laya requests.
- No deletion/adoption/rename of existing trial volumes or private engine stores. Keep the dashboard the user is viewing available.
- CPU shares are relative weight, not guaranteed cores; memory reservation is soft. Hard caps must be proven on the actual engine. Host capacity remains detected, not hardcoded.
- Resource-policy starting point, to validate under capped startup/ingestion/browser access: 256 MiB soft reservation, 1024 MiB hard cap; 256 CPU shares (0.25 baseline share unit), 2 CPU hard cap. Based on observed ~128–268 MiB current memory, ~526 MiB lifetime peak and short synthetic ingestion—not a production capacity claim. Account for Phoenix in host budgeting whenever System One is enabled, including llm-server-disabled mode. If this policy fails, record the measurement before adjusting.
- After .sh edits run make validate; after .py edits run make validate and normal make test with live opt-in unset. Opt-in live tests serialize with -n 0.

## Review focus

1. Unset/inherited /tmp TMPDIR cannot break basic startup or create new task scratch outside repo; unsafe symlinks rejected before downloads/builds.
2. Laya disabled means no Phoenix pulls/builds/start or new conflicting enable flag; Laya enabled means normal and boot lifecycle cover dashboard readiness/status/stop.
3. Repeated start must not treat its own healthy listener as a conflict or rebuild a sender; foreign port/resource ownership must refuse before mutation.
4. Resource output/rendering/effective cgroups agree; tight-host checks reserve Phoenix, disabled modes do not charge it; no silent uncapped fallback.
5. Default-path E2E must not secretly inject the TMPDIR workaround or claim fake System One checks are real GPU/inference tests; browser UI and exact synthetic persistence are distinct evidence.

## Task 1 — Pin the reported failure and separate basic startup from test tooling

Files: `scripts/phoenix-trial.sh`, new normal Phoenix lifecycle/compose files as needed, `tests/test_shell.py`, Phoenix configuration tests.

- [ ] Write failing default/unset and inherited-/tmp TMPDIR regressions, safe repo-root/subdirectory and unsafe-symlink cases, and validation-before-pull/build assertions.
- [ ] Observe genuine red (not import/harness failure).
- [ ] Implement repository-local temp selection; ensure starting only the dashboard neither builds nor requires the synthetic sender. Prefer shared implementation, not a third competing lifecycle.
- [ ] Prove targeted green and no duplicate POST/retained-artifact regressions.

## Task 2 — Integrate conditional lifecycle and resource budgeting

Files: `setup/setup.sh`, `setup/render-unit.sh`, `scripts/start.sh`, `scripts/stop.sh`, `scripts/status.sh`, `scripts/print-endpoints.sh`, `pylib/resources.py`, relevant CLI/config/compose helpers and their tests only where necessary.

- [ ] Add red tests for System One on/off in both llama.cpp modes, normal and wrapper boot/stop paths, idempotence/owned listener handling, foreign ownership refusal and readiness failure propagation.
- [ ] Add red budgeting/resource tests for positive caps/reservations, disabled no-charge and insufficient-host failure. Document CPU weight vs actual hard cap.
- [ ] Implement minimal hooks using the existing System One flag, pinned server-only preparation, persistent owned volume and readiness. Preserve ordinary stop data. Do not introduce a second user enable prompt or new resident process.
- [ ] Render and inspect isolated lifecycle with capped resources; keep unrelated services/config bytes untouched in actual operator environment.

## Task 3 — Simplify user surface and prove the actual workflow

Files: `Makefile`, `scripts/help.sh`, README/QUICK_START/architecture as necessary, Phoenix runbook/results, opt-in integration and relevant CLI/shell tests.

- [ ] Remove five trial lifecycle targets from normal user documentation/help in favor of existing setup/start/stop/status. Internal synthetic fixture tools may retain historical names; do not churn the exact verifier merely for naming.
- [ ] Provide at most one focused `make test-phoenix` opt-in serialized E2E entry point (thin delegate), not a suite of renamed lifecycle targets.
- [ ] Exercise real pinned Phoenix under rootless owned disposable resources with TMPDIR UNSET and inherited /tmp. No production systemctl unit or real Laya restart/inference. Clearly distinguish stubbed non-Phoenix orchestration from actual server/browser verification.
- [ ] Prove effective cgroup bounds, readiness/UI HTTP, browser navigation on existing cached Chromium (short private repo TMPDIR), one explicit fresh synthetic run and exact same-image/same-volume persistence without reseed. Reuse old strict recovery tests; map older pending GLM 003/008 coverage honestly rather than erase it.
- [ ] Record image sizes, timestamped CPU/memory/volume samples, caps vs measured usage, actual browser screenshot/DOM and retained resources. No claim of real history, full load capacity or observed seven-day cleanup.
- [ ] Run fresh make validate, full offline make test, serialized live checks and whitespace scan including new files. Save actual outputs; report remaining blockers precisely.

## Completion gate

Coordinator verifies preservation/default-path evidence and requests the previously authorized independent GLM final code review (not another plan-review round). Every genuine new code-review finding gets the required local-Laya advisory over exact reviewed hashes; uncertainty cannot waive findings. No completion claim until normal-path tests and actual dashboard evidence pass. No worker commits.
