# Pre-commit Automation, Pyright, and Parallel Tests Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Catch the kind of drift that just slipped through (a script's log wording changed, its test's assertion didn't) automatically, before it reaches `origin`, and cut the suite's ~4-6 minute serial runtime.

**Architecture:** Add `pyright` as a third `tools/validate.sh` check alongside the existing `shellcheck`+`ruff`; wrap both `tools/validate.sh` and `tools/test.sh` in a `pre-commit` (pre-commit.com) config as `repo: local` hooks, with lint on `pre-commit` and the (now parallelized) test suite on `pre-push`; parallelize `tools/test.sh` with `pytest-xdist`.

**Tech Stack:** `uv` (dependency groups), `pyright`, `pre-commit` (pre-commit.com), `pytest-xdist`. No new runtime dependency — all dev-only.

**Spec:** This plan has no separate spec document; the Context section below carries the equivalent background (what broke, why, and what was asked for).

## Global Constraints

- Python floor: `requires-python = ">=3.11"` (`pyproject.toml`) — `pyright`'s `pythonVersion` must match.
- No new runtime dependency — everything added here goes in `[dependency-groups] dev`.
- Reuse existing scripts (`tools/validate.sh`, `tools/test.sh`) from the pre-commit hooks; do not duplicate their command lines into the YAML.
- `make validate`/`make test` must keep working unchanged (they already shell out to these scripts) — do not touch the `Makefile`.
- Match the existing code style: no new abstractions, no suppressions where a real one-line fix exists (see Task 2's `presets.py` exception, which is a genuine stub limitation, not a bug).

## Review Focus

- **A machine with no Volta default Node set** (this one, today): `tests/test_remote_setup.py`'s 8 OpenCode-install tests fail with `Volta error: Node is not available`, unrelated to any code change. Task 1 documents this as a one-time prerequisite rather than letting it look like a broken hook on a fresh clone.
- **Two full test runs hitting this repo's real Volta cache/network/CPU at once** (confirmed cause of the `173 failed` run vs. a solo `9 failed`): Task 4's determinism check (run `-n auto` twice back-to-back, solo) is the test that would catch a regression here before trusting the suite's new default parallel mode.
- **A `pre-commit` hook that silently no-ops on a fresh clone** because `pre-commit install` was never run: Task 3 documents the one-time setup command; there is no code-level guard against skipping it (same trust boundary as `make setup` already has).
- **`pyright` flagging the `ConfigParser.optionxform` assignment as broken** when it is a long-standing, correct stdlib pattern (`parser.optionxform = str`) just outside pyright's stub coverage: Task 2 pins this as a scoped `# type: ignore`, not a rewrite, and the task's own verification step confirms it's the *only* suppression in the baseline (6 of 7 are real fixes).
- **A pre-push `pytest` hook that takes 4-6 minutes blocking every `git push`** even after parallelization: Task 4 measures the actual `-n auto` wall time so this is a known, visible number rather than a surprise the first time someone pushes.

---

### Task 1: Fix the hermeticity bug and the one stale test, document the Volta gap

**Files:**
- Create: `tests/conftest.py` (already written this session, not yet committed — verify content matches below, then commit)
- Modify: `tests/test_shell.py` (the `test_set_desktop_gpu_reset_is_a_noop_when_nothing_active` assertion, already edited — verify, then commit)
- Modify: `setup/prerequisites.sh` (add a one-line Volta/Node note to `DEVELOPMENT` group's `node` purpose string, or a standalone printed note — see Step 3 below)

**Interfaces:**
- Consumes: nothing from other tasks (this is the foundation task; Tasks 2-4 assume a green baseline).
- Produces: a suite where `uv run pytest tests/` run **solo** on this machine is 0 failures (Volta caveat aside, which Step 4 confirms separately and isn't a repo defect to "fix").

- [ ] **Step 1: Verify `tests/conftest.py` exists with this content**

```python
"""Shared pytest fixtures for the lifecycle-script tests."""

import os

import pytest


@pytest.fixture(autouse=True, scope="session")
def _no_stale_pwd() -> None:
    """Drop PWD from the environment before any subprocess test runs.

    On Fedora Atomic/Bazzite hosts /home is a symlink to /var/home, so the
    test runner's own shell can have $PWD set to the symlinked form even
    though this file's canonical path (ROOT = Path(__file__).resolve()) is
    the real /var/home/... one. Tests spawn child scripts with
    subprocess.run(..., cwd=ROOT, env=os.environ | {...}): the OS chdir()s
    the child into the real directory, but bash trusts an inherited PWD
    that still refers to the same directory and keeps its stale string
    instead of recomputing it -- so a script's own REPO_DIR (computed via
    `cd ... && pwd`) can land on the symlinked path while the test's
    expected string uses the canonical one, failing a path-equality
    assertion for a reason that has nothing to do with the script under
    test. Unsetting PWD once, before any test builds its environment,
    makes every child bash recompute it fresh from the real post-chdir cwd.
    """
    os.environ.pop("PWD", None)
```

If the file is missing or differs, write it exactly as above.

- [ ] **Step 2: Run the two originally-failing tests to confirm they pass**

Run: `cd /var/home/bazzite/git/llm-env && uv run pytest tests/test_shell.py -k "test_enable_boot_prepares_a_secure_key_without_starting or test_update_omniroute_pulls_and_restarts_when_a_newer_version_is_published" -q`
Expected: `2 passed`

- [ ] **Step 3: Verify the stale-test fix in `tests/test_shell.py`**

Find `test_set_desktop_gpu_reset_is_a_noop_when_nothing_active` (around line 1495-1502) and confirm it reads:

```python
def test_set_desktop_gpu_reset_is_a_noop_when_nothing_active(
    tmp_path: pathlib.Path,
) -> None:
    result, _, _ = run_set_desktop_gpu_with_stubs(tmp_path, "reset")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "no environment.d override was active" in result.stdout
    assert "no stale per-app Flatpak DRI_PRIME override was active" in result.stdout
```

(This matches `scripts/set-desktop-gpu.sh`'s current, more specific log lines at `log_info "no environment.d override was active"` and `log_info "no stale per-app Flatpak DRI_PRIME override was active"`.) If it still has the old single `"no override was active"` assertion, apply the edit above.

- [ ] **Step 4: Run it to confirm the fix**

Run: `uv run pytest tests/test_shell.py -k test_set_desktop_gpu_reset_is_a_noop_when_nothing_active -q`
Expected: `1 passed`

- [ ] **Step 5: Document the Volta/Node prerequisite**

In `setup/prerequisites.sh`, the `command_purpose` function's `node)` case currently reads:

```bash
        node) printf '%s\n' "JSONC editor for OpenCode client configuration" ;;
```

Change it to also flag the test dependency:

```bash
        node) printf '%s\n' "JSONC editor for OpenCode client configuration; also needs 'volta install node' once for tests/test_remote_setup.py to pass" ;;
```

- [ ] **Step 6: Run the full suite solo to get the true baseline**

Run: `uv run pytest tests/ -q`
Expected: `8 failed` (the `test_remote_setup.py` Volta-dependent tests only — confirm the failure reason in the output is `Volta error: Node is not available` for each, not something else), `0` from `test_shell.py`/`test_set_desktop_gpu_*`.

If `volta install node` has been run on this machine by the time this step executes, expected becomes `0 failed`.

- [ ] **Step 7: Commit**

```bash
git add tests/conftest.py tests/test_shell.py setup/prerequisites.sh
git commit -m "fix(tests): drop stale \$PWD before subprocess tests, fix stale desktop-gpu assertion, document Volta/Node prereq"
```

---

### Task 2: Add pyright, fix the 7 real baseline errors

**Files:**
- Modify: `pyproject.toml` (add `pyright` dev dep + `[tool.pyright]` section)
- Modify: `pylib/agent_runner.py` (`_close_streams` definition at line 301; its import line 20; called from lines 691 and 747)
- Modify: `pylib/config.py:58,132` (`_positive_int` definition and its narrowed call site)
- Modify: `pylib/config.py:416` (`seen.add(alias)` — the alias-narrowing fix; unrelated to `_positive_int`)
- Modify: `pylib/presets.py:36`
- Modify: `pylib/remote_setup.py:611,623`
- Modify: `tools/validate.sh` (add the `pyright` invocation)
- Modify: `setup/dev-setup.sh` (header comment + log string — it already `uv sync`s the `dev` group this task adds `pyright` to)
- Modify: `AGENTS.md:34` (the `make validate` Commands-list entry — see Step 12a)

**Interfaces:**
- Consumes: a green Task 1 baseline (so any new failure after this task is attributable to this task alone).
- Produces: `tools/validate.sh` now exits non-zero on a type error, consumed by Task 3's pre-commit hook.

- [ ] **Step 1: Get the current baseline (already run once; reconfirm)**

Run: `cd /var/home/bazzite/git/llm-env && uvx pyright --pythonversion 3.11 llmenv.py pylib`
Expected: `7 errors, 2 warnings` — the 2 warnings are `reportMissingModuleSource` for `yaml` (pyright can't see PyYAML's bundled stubs under `uv`'s venv layout; harmless, left as-is, not part of this task's fix list) in `pylib/compose.py:17` and `pylib/config.py:12`.

- [ ] **Step 2: Fix `pylib/agent_runner.py:691,747`**

Read the surrounding function first:

Run: `rg -n "_close_streams" pylib/agent_runner.py` — the definition is at line 301 (`def _close_streams(streams: Sequence[BinaryIO | None]) -> bool:`), called at line 691 with `(process.stdout, process.stderr)` and at line 747 with `sources[len(started_threads):]`.

The error is `Argument of type "tuple[IO[Any] | None, IO[Any] | None]" cannot be assigned to parameter "streams" of type "Sequence[BinaryIO | None]"`. `_close_streams`'s own signature is too narrow for what it's actually called with (`subprocess.Popen`'s `.stdout`/`.stderr`/`.stdin` are typed `IO[bytes] | None` generically, not `BinaryIO | None`, on this `Popen[bytes]` usage). Widen the parameter type to match what every call site actually passes:

1. Change line 301's signature to `def _close_streams(streams: Sequence[IO[bytes] | None]) -> bool:`.
2. Add `IO` to the existing `typing` import at line 20 — it currently reads `from typing import BinaryIO, Literal, Protocol`; change it to `from typing import BinaryIO, IO, Literal, Protocol`. (`BinaryIO` stays — nothing else in this step removes its other uses.)

- [ ] **Step 3: Verify Step 2**

Run: `uvx pyright --pythonversion 3.11 pylib/agent_runner.py`
Expected: `0 errors` for this file (warnings, if any, unrelated to `_close_streams`).

- [ ] **Step 4: Fix `pylib/config.py:132`**

Run: `bat --plain --line-range 118,133 pylib/config.py` to see the call site:

```python
ctx_size = model.get("ctx_size")
if _positive_int(ctx_size):
    model["client_max_output_tokens"] = min(ctx_size, 8192)
```

The error is `Argument of type "Unknown | None" cannot be assigned to parameter "arg1" of type "SupportsRichComparisonT@min" in function "min"`. The code already guards this correctly at runtime — `if _positive_int(ctx_size):` is true only when `ctx_size` is a positive `int` — but pyright can't see that, because `_positive_int` (defined at line 58) is typed `(value: Any) -> bool`: a plain `bool` return carries no information back to the caller about `value`'s narrowed type, so pyright still treats `ctx_size` as `Unknown | None` inside the `if` body. Make pyright understand the same thing the runtime check already guarantees, by turning `_positive_int` into a type guard:

```python
from typing import Any, TypeGuard  # was: from typing import Any


def _positive_int(value: Any) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0
```

This is the same one-line-bodied function with a narrower return type — every other call site (lines 88, 93, 272, 286, 308, 367, 380, 466, 469, 470, 485, 492, 531, 561) still works unchanged, since a `TypeGuard[int]` is used exactly like a `bool` at runtime and in every existing `if _positive_int(...)`/`if not _positive_int(...)` call.

- [ ] **Step 5: Fix `pylib/config.py:416`**

Run: `bat --plain --line-range 400,417 pylib/config.py` to see the surrounding loop:

```python
seen: set[str] = set()
for index, model in enumerate(models):
    ...
    alias = model.get("alias")
    valid_alias = isinstance(alias, str) and bool(alias.strip())
    model_name = alias if valid_alias else f"at index {index}"
    for key in REQUIRED_MODEL_KEYS:
        if key not in model:
            errors.append(f"model {model_name} missing key: {key}")
    if not valid_alias:
        errors.append(f"model at index {index} alias must be a non-empty string")
    elif alias in seen:
        errors.append(f"duplicate model alias: {alias}")
    else:
        seen.add(alias)
```

The error is `Argument of type "Unknown | str | None" cannot be assigned to parameter "object" of type "str" in function "add"` on `seen.add(alias)`. At runtime this branch is only reached when `valid_alias` is `True`, which already proves `alias` is a non-empty `str` — but pyright can't follow that proof through a separately-stored `bool` variable (`valid_alias`) back to `alias`'s type several lines later. Add a one-line assertion at the point of use so pyright narrows `alias` the same way the runtime logic already has:

```python
    else:
        assert isinstance(alias, str)
        seen.add(alias)
```

- [ ] **Step 6: Verify Steps 4-5**

Run: `uvx pyright --pythonversion 3.11 pylib/config.py`
Expected: `0 errors` (the 1 `reportMissingModuleSource` warning for `yaml` at line 12 is expected and untouched).

- [ ] **Step 7: Fix `pylib/presets.py:36`**

Run: `bat --plain --line-range 30,40 pylib/presets.py`. This is `parser.optionxform = str` (`parser` is the local `configparser.ConfigParser()` instance created the line above — not `cfg`, the function's `dict[str, Any]` parameter) — a standard, correct `ConfigParser` pattern that pyright's bundled stub doesn't model as assignable. Add a scoped ignore on that exact line:

```python
parser.optionxform = str  # type: ignore[method-assign]  # stdlib stub limitation, not a bug
```

- [ ] **Step 8: Fix `pylib/remote_setup.py:611,623`**

Run: `bat --plain --line-range 605:630 pylib/remote_setup.py`. Both overrides rename `BaseHTTPRequestHandler`'s second parameter from `format` to `format_string`. Rename it back to `format` in both `log_message` and `log_error` definitions (the parameter is positional in every real call site in `http.server`, so the rename has no behavioral effect — it only breaks the override's type compatibility).

- [ ] **Step 9: Verify Steps 7-8 and the full baseline**

Run: `uvx pyright --pythonversion 3.11 llmenv.py pylib`
Expected: `0 errors, 2 warnings` (the 2 `reportMissingModuleSource` `yaml` warnings only).

- [ ] **Step 10: Add pyright to `pyproject.toml`**

```toml
[dependency-groups]
dev = ["pytest>=8.0", "ruff>=0.8", "pyright>=1.1"]

[tool.pyright]
include = ["llmenv.py", "pylib"]
pythonVersion = "3.11"
typeCheckingMode = "basic"
```

`tests/` is deliberately left out of `include`. Running `uvx pyright` straight against it reports ~70 pre-existing errors (missing-stub `BaseHTTPRequestHandler.log_message`/`log_error` overrides inherited from the same stdlib stub gap as `pylib/remote_setup.py`, plus many loosely-typed test-helper call sites passing `object`/`str` where a helper's signature wants a narrower type) — none introduced by this task, and two orders of magnitude past its "fix the 7 real baseline errors" scope. Widening `include` to cover `tests/` is future work for a dedicated task, not this one.

- [ ] **Step 11: Install it through `uv` and re-verify via the project's own pinned version**

Run: `uv sync && uv run pyright`
Expected: `0 errors, 2 warnings` — the same 2 `reportMissingModuleSource` `yaml` warnings as Step 9 (`include` stays scoped to `llmenv.py` and `pylib`; `tests/` is out of scope per the note above).

- [ ] **Step 12: Wire it into `tools/validate.sh`**

Current content:

```bash
#!/usr/bin/env bash
# validate.sh — shellcheck + ruff.
set -euo pipefail

shellcheck -s bash ./tools/*.sh ./setup/*.sh ./setup/lib/*.sh ./scripts/*.sh
uv run ruff check llmenv.py pylib tests
echo "All checks passed."
```

Change to:

```bash
#!/usr/bin/env bash
# validate.sh — shellcheck + ruff + pyright.
set -euo pipefail

shellcheck -s bash ./tools/*.sh ./setup/*.sh ./setup/lib/*.sh ./scripts/*.sh
uv run ruff check llmenv.py pylib tests
uv run pyright
echo "All checks passed."
```

(Update the header comment too, as shown.)

- [ ] **Step 12a: Update `AGENTS.md`'s `make validate` Commands-list entry**

`AGENTS.md:34` currently reads:

```
make validate                  # shellcheck + ruff
```

Change it to match `tools/validate.sh`'s new header comment from Step 12:

```
make validate                  # shellcheck + ruff + pyright
```

- [ ] **Step 13: Run it end to end**

Run: `bash tools/validate.sh`
Expected: `All checks passed.` printed, exit 0.

- [ ] **Step 14: Update `setup/dev-setup.sh` so its own text doesn't drift from what it now installs**

`make dev-setup` (AGENTS.md's "Install dev-only tooling" entry point) runs this script, which `uv sync`s the `dev` dependency group Step 10 just added `pyright` to. Its header comment and log line still only say "pytest, ruff" — update both:

```bash
#!/usr/bin/env bash
# dev-setup.sh — sync the Python dev environment (pytest, ruff, pyright).
set -euo pipefail
# shellcheck disable=SC1091 # Resolved from this script at runtime.
# shellcheck source=../tools/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/../tools/lib.sh"

require_cmd uv

log_step "Syncing the Python dev environment"
uv sync
log_info "dev environment ready (.venv, pytest, ruff, pyright)"
```

- [ ] **Step 15: Commit**

```bash
git add pyproject.toml pylib/agent_runner.py pylib/config.py pylib/presets.py pylib/remote_setup.py tools/validate.sh setup/dev-setup.sh AGENTS.md uv.lock
git commit -m "feat(validate): add pyright, fix its 7 real baseline errors"
```

---

### Task 3: Add the pre-commit framework

**Files:**
- Create: `.pre-commit-config.yaml`
- Modify: `AGENTS.md` (add the `pre-commit install` step next to the existing `make dev-setup` line; see Step 1)

**Interfaces:**
- Consumes: `tools/validate.sh` (Task 2's updated version) and `tools/test.sh`, called as opaque commands — this task does not change either script's internals.
- Produces: a `pre-commit` hook set installable via one documented command; later tasks don't depend on this one.

- [ ] **Step 1: Find where one-time machine setup is already documented**

Run: `rg -n "make dev-setup|make setup|make prerequisites" AGENTS.md`

`AGENTS.md:33` already has the dedicated one-time dev-tooling entry point: `make dev-setup                 # Install dev-only tooling`. That's the command listing's own line for exactly this kind of one-time setup (it already wraps `setup/dev-setup.sh`'s `uv sync` of the `dev` group `pyright` now lives in, per Task 2; `pytest-xdist` is not added until Task 4, which runs after this task) — add the `pre-commit install` step immediately after that line, not next to the unrelated `make setup`/`make prerequisites` bullets above it.

- [ ] **Step 2: Write `.pre-commit-config.yaml`**

```yaml
repos:
  - repo: local
    hooks:
      - id: validate
        name: shellcheck + ruff + pyright
        entry: bash tools/validate.sh
        language: system
        pass_filenames: false
        always_run: true
      - id: test
        name: pytest (full suite)
        entry: bash tools/test.sh
        language: system
        pass_filenames: false
        always_run: true
        stages: [pre-push]
```

- [ ] **Step 3: Document the one-time install command in `AGENTS.md`, right after the `make dev-setup` line found in Step 1**

```
make dev-setup                 # Install dev-only tooling
uv tool install pre-commit && pre-commit install --hook-type pre-commit --hook-type pre-push  # one-time: enable the git hooks below
```

- [ ] **Step 4: Verify the config is well-formed and the validate hook runs**

Run: `uv tool install pre-commit && pre-commit run validate --all-files`
Expected: the `validate` hook runs `tools/validate.sh` and passes (same result as Task 2 Step 13).

- [ ] **Step 5: Verify the test hook is pre-push-only (does not fire on commit)**

Run: `pre-commit install --hook-type pre-commit --hook-type pre-push` then make a trivial no-op change (e.g. touch a comment) and `git commit -m "probe: confirm validate-only on commit"`
Expected: only the `validate` hook runs on `git commit`; `test` does not run (it's gated to the `pre-push` stage).

Then immediately discard the probe commit so it doesn't linger in history: `git reset --soft HEAD~1` followed by `git checkout -- <the touched file>` (or `git restore <the touched file>` on a modern git) to drop the no-op change from the working tree too.

- [ ] **Step 6: Commit**

```bash
git add .pre-commit-config.yaml AGENTS.md
git commit -m "feat: add pre-commit config (validate on commit, tests on push)"
```

---

### Task 4: Parallelize the test suite, verify determinism

**Files:**
- Modify: `pyproject.toml` (add `pytest-xdist` dev dep)
- Modify: `tools/test.sh`
- Modify: `setup/dev-setup.sh` (header comment + log string, same reason as Task 2 Step 14)

**Interfaces:**
- Consumes: Task 2's `pyproject.toml` — specifically its `dev = ["pytest>=8.0", "ruff>=0.8", "pyright>=1.1"]` list. This task **must run after Task 2, not just after Task 1**: Step 1 below appends `pytest-xdist` onto that exact list; running this task from Task 1's original 2-entry list instead would silently drop `pyright` the next time Task 2 ran, and running Task 2 after this task would silently drop `pytest-xdist`. Do not parallelize Task 2 and Task 4.
- Produces: `tools/test.sh`'s new runtime number, which Task 3's `pre-push` hook (already wired to call this script) inherits automatically — no further change needed there.

- [ ] **Step 1: Add `pytest-xdist` onto Task 2's dev list**

```toml
[dependency-groups]
dev = ["pytest>=8.0", "ruff>=0.8", "pyright>=1.1", "pytest-xdist>=3.5"]
```

Confirm the line you're editing already reads `dev = ["pytest>=8.0", "ruff>=0.8", "pyright>=1.1"]` (Task 2's Step 10 result) before appending `"pytest-xdist>=3.5"` — if it doesn't, Task 2 hasn't landed yet; stop and resolve that first rather than overwriting its entry.

Run: `uv sync`

- [ ] **Step 2: Update `tools/test.sh`**

Current content:

```bash
#!/usr/bin/env bash
# test.sh — Python test suite.
set -euo pipefail

uv run pytest tests/ -v
```

Change to:

```bash
#!/usr/bin/env bash
# test.sh — Python test suite (parallelized across available CPU cores).
set -euo pipefail

uv run pytest tests/ -n auto -v
```

- [ ] **Step 3: Run it twice back to back, solo (nothing else hitting this machine's network/Volta/CPU at the same time)**

Run: `time bash tools/test.sh` (first run)
Run: `time bash tools/test.sh` (second run, immediately after)
Expected: identical pass/fail counts both times (Volta-caveat failures aside, per Task 1 Step 6) — if the two runs disagree, STOP and report the exact divergent test names before proceeding; that would mean a real hermeticity bug, not the already-diagnosed concurrent-process cause.

- [ ] **Step 4: Record the wall-time improvement**

Compare both runs' `real` time against the ~4-6 minute serial baseline measured earlier this session. Note the number in the commit message (Step 6) so it's visible in `git log` later.

- [ ] **Step 5: Update `setup/dev-setup.sh` again for `pytest-xdist`**

Same reason as Task 2 Step 14 — this task just added `pytest-xdist` to the `dev` group `setup/dev-setup.sh` `uv sync`s, so its header comment and log line (last updated in Task 2 Step 14 to say "pytest, ruff, pyright") need the new package too:

```bash
#!/usr/bin/env bash
# dev-setup.sh — sync the Python dev environment (pytest, ruff, pyright, pytest-xdist).
set -euo pipefail
# shellcheck disable=SC1091 # Resolved from this script at runtime.
# shellcheck source=../tools/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/../tools/lib.sh"

require_cmd uv

log_step "Syncing the Python dev environment"
uv sync
log_info "dev environment ready (.venv, pytest, ruff, pyright, pytest-xdist)"
```

- [ ] **Step 6: Commit**

Use the wall time measured in Step 4 (e.g. `real`'s `4m12s` → `~1m30s`) as `<serial> -> <parallel>` below:

```bash
git add pyproject.toml tools/test.sh setup/dev-setup.sh uv.lock
git commit -m "perf(tests): parallelize via pytest-xdist (-n auto, <serial> -> <parallel>)"
```

## Verification (end to end, after all 4 tasks)

1. `bash tools/validate.sh` — clean (shellcheck + ruff + pyright).
2. `bash tools/test.sh` run twice solo — same result both times, faster than the ~4-6 min serial baseline.
3. `pre-commit run --all-files` — the `validate` hook passes; the `test` hook is skipped (pre-push stage, not run by `--all-files`'s default `pre-commit` stage) unless invoked with `pre-commit run --all-files --hook-stage pre-push`.
4. A trivial dummy commit confirms `validate` fires on `git commit`; a trivial `git push` (or `pre-commit run --hook-stage pre-push --all-files`) confirms `test` fires there and not earlier.
