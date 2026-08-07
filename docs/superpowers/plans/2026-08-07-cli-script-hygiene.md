# CLI and Script Hygiene Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cut duplication across the bash scripts, make chained `make`
targets legible with colored start/end banners, cut `check-with-agents.sh`
failure output down to what's actually diagnostic, and give the Python dev
environment a real, reproducible, version-pinned setup path.

**Architecture:** All new shared bash logic lands in `tools/lib.sh` (colors,
constants, `wait_for_health()`, `load_server_config()`) so every script that
sources it gets the fix for free — no script grows its own copy. The one
new piece of real logic (classifying a JSONL agent transcript into "final
answer + error-shaped events" instead of a raw dump) is Python
(`pylib/transcript.py`, wired through a new `llmenv classify-transcript`
subcommand), extending the "bash orchestrates, Python computes" split this
repo already documents. A new `pyproject.toml` gives `uv sync`/`uv run
pytest`/`uv run ruff check` a real, lockable project environment, entirely
separate from `llmenv.py`'s own self-contained PEP 723 script metadata
(deliberately left untouched — see Task 13's note on why).

**Tech Stack:** bash (`tools/lib.sh` conventions), Python (stdlib `json`,
already-established `pylib/` + `llmenv.py` subcommand pattern), `uv`
(project mode via `pyproject.toml`, in addition to its existing PEP 723
script-mode use for `llmenv.py`).

## Global Constraints

- Makefile target bodies longer than 3 lines must delegate to a `.sh` file
  (`AGENTS.md`).
- Python is invoked only as `uv run llmenv.py <subcommand>` (`AGENTS.md`).
- After editing any `.sh` file, run `make validate`. After editing any `.py`
  file, run `make validate && make test` (`AGENTS.md`).
- Never hardcode a value that can be measured (`AGENTS.md`).
- CI/CD is explicitly out of scope (per user direction) — local `make
  validate`/`make test` on each commit is the only gate.
- **Sequencing note with the sibling compose plan:** Task 9 (`scripts/clean.sh`)
  and, to a lesser extent, Task 1/8 (`tools/lib.sh` constants also touched by
  the compose plan's Task 7) overlap with
  `docs/superpowers/plans/2026-08-07-compose-container-definitions.md`. This
  plan is written against the **current** repo state (Quadlet-based
  `clean.sh`, current `tools/lib.sh`). If the compose plan has already been
  executed by the time this plan runs, `clean.sh`'s task body below is
  stale — reconcile against that plan's Task 10 output instead (it already
  reads `${COMPOSE_FILE}`; this plan's job is then just to make it read
  `gpu.image` from config rather than the hardcoded literals, on top of
  that version). Every other task in this plan is independent of the
  compose plan.
- The Makefile/markdown test in `tests/test_docs.py`
  (`RELOCATED_SCRIPT_REFERENCE`) only recognizes a fixed list of existing
  script basenames (`benchmark`, `check-server`, `check-setup`,
  `check-with-agents`, `clean`, `disable-boot`, `enable-boot`, `key-reset`,
  `lib`, `network`, `prerequisites`, `render-unit`, `setup`, `start`,
  `stop`) — the new scripts this plan adds (`run-target`, `status`, `logs`,
  `dev-setup`) are not in that list, so mentioning them in markdown docs
  does not require touching that test.

---

### Task 1: `tools/lib.sh` — shared constants (colors, images, timeout)

**Files:**
- Modify: `tools/lib.sh`

**Interfaces:**
- Produces: `BOLD` (new color constant, alongside the existing `GREEN`/
  `YELLOW`/`BLUE`/`RED`/`NC`); `VULKAN_IMAGE`/`CPU_IMAGE` (moved here from
  `scripts/benchmark.sh`, the single source of truth every other script
  currently hardcodes independently); `LLM_ENV_HEALTH_TIMEOUT_SECONDS`
  (defaults to `60`, overridable via the environment, matching the
  `LLM_ENV_ASSUME_YES`/`LLM_ENV_ROTATE_KEY` override convention this repo
  already uses).

- [ ] **Step 1: Edit `tools/lib.sh`**

Change the color block:

```bash
GREEN=$'\033[0;32m'; YELLOW=$'\033[1;33m'; BLUE=$'\033[0;34m'
RED=$'\033[0;31m'; NC=$'\033[0m'
```

to:

```bash
GREEN=$'\033[0;32m'; YELLOW=$'\033[1;33m'; BLUE=$'\033[0;34m'
RED=$'\033[0;31m'; BOLD=$'\033[1m'; NC=$'\033[0m'
```

Add, near the top-level path constants (after the `UNIT_NAME`/`QUADLET_DIR`
block — or after whatever that block has become if the compose plan already
ran; see the Global Constraints sequencing note):

```bash
VULKAN_IMAGE="ghcr.io/ggml-org/llama.cpp:server-vulkan"
CPU_IMAGE="ghcr.io/ggml-org/llama.cpp:server"
LLM_ENV_HEALTH_TIMEOUT_SECONDS="${LLM_ENV_HEALTH_TIMEOUT_SECONDS:-60}"
```

Update the export line to include the new names. As of this plan's writing,
`tools/lib.sh`'s export line is:

```bash
export REPO_DIR CONFIG_PATH MODELS_DIR UNIT_NAME QUADLET_DIR
```

Append the new names to the end, keeping every existing name in place:

```bash
export REPO_DIR CONFIG_PATH MODELS_DIR UNIT_NAME QUADLET_DIR VULKAN_IMAGE CPU_IMAGE LLM_ENV_HEALTH_TIMEOUT_SECONDS
```

(If the compose plan already ran first, this line may also carry
`COMPOSE_FILE`/`WRAPPER_UNIT_PATH` instead of — or alongside —
`QUADLET_DIR`; whatever names are already there, keep every one of them and
only append the three new names shown above. This task never removes an
unrelated entry.)

- [ ] **Step 2: Run shellcheck**

Run: `shellcheck -s bash tools/lib.sh`
Expected: no new warnings. This constants-only change has no behavioral
test of its own — Task 2 onward exercises these values through the scripts
that consume them.

- [ ] **Step 3: Commit**

```bash
git add tools/lib.sh
git commit -m "refactor(lib): centralize color, image, and timeout constants"
```

---

### Task 2: `tools/run-target.sh` — colored start/end banners

**Files:**
- Create: `tools/run-target.sh`
- Test: `tests/test_shell.py`

**Interfaces:**
- Produces: `tools/run-target.sh <name> -- <command...>` — prints a bold,
  bright-blue `▶ <name>` banner before running `<command...>`, then a bold
  green `■ <name> end — ok (Ns)` on exit 0 or a bold red
  `■ <name> end — failed, exit <status> (Ns)` on nonzero exit, and exits
  with the wrapped command's own exit status (chained `make` targets keep
  stopping on first failure exactly as today — this is a legibility layer
  only, not an error-handling change).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_shell.py`:

```python
def test_run_target_prints_start_banner_before_running_the_command(
    tmp_path: pathlib.Path,
) -> None:
    result = subprocess.run(
        ["/usr/bin/bash", "tools/run-target.sh", "demo", "--", "true"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0
    # The start-banner printf leads with a blank line (spacing between
    # chained make targets), so stdout's line 0 is empty and the banner
    # itself is line 1.
    assert "demo" in result.stdout.splitlines()[1]


def test_run_target_prints_ok_end_banner_on_success(tmp_path: pathlib.Path) -> None:
    result = subprocess.run(
        ["/usr/bin/bash", "tools/run-target.sh", "demo", "--", "true"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0
    assert "demo" in result.stdout
    assert "ok" in result.stdout


def test_run_target_prints_failed_end_banner_and_propagates_exit_status(
    tmp_path: pathlib.Path,
) -> None:
    result = subprocess.run(
        ["/usr/bin/bash", "tools/run-target.sh", "demo", "--", "bash", "-c", "exit 7"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 7
    assert "failed" in result.stdout
    assert "exit 7" in result.stdout


def test_run_target_runs_the_wrapped_commands_own_output(tmp_path: pathlib.Path) -> None:
    result = subprocess.run(
        ["/usr/bin/bash", "tools/run-target.sh", "demo", "--", "echo", "hello"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0
    assert "hello" in result.stdout
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --with pytest pytest tests/test_shell.py -v -k test_run_target`
Expected: FAIL — `tools/run-target.sh` does not exist yet
(`No such file or directory`).

- [ ] **Step 3: Create `tools/run-target.sh`**

```bash
#!/usr/bin/env bash
# run-target.sh — wrap a make target's command with a colored start/end banner.
set -euo pipefail
# shellcheck disable=SC1091 # Resolved from this script at runtime.
# shellcheck source=./lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

[ "$#" -ge 2 ] || { echo "usage: run-target.sh <name> -- <command...>" >&2; exit 64; }
name="$1"
shift
[ "$1" = -- ] || { echo "usage: run-target.sh <name> -- <command...>" >&2; exit 64; }
shift

printf '\n%s%s▶ %s%s\n' "$BOLD" "$BLUE" "$name" "$NC"
started_at=$(date +%s)
status=0
"$@" || status=$?
elapsed=$(( $(date +%s) - started_at ))

if [ "$status" -eq 0 ]; then
    printf '%s%s■ %s end — ok (%ss)%s\n' "$BOLD" "$GREEN" "$name" "$elapsed" "$NC"
else
    printf '%s%s■ %s end — failed, exit %s (%ss)%s\n' "$BOLD" "$RED" "$name" "$status" "$elapsed" "$NC"
fi
exit "$status"
```

Make it executable: `chmod +x tools/run-target.sh`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --with pytest pytest tests/test_shell.py -v -k test_run_target`
Expected: PASS.

- [ ] **Step 5: Run shellcheck**

Run: `shellcheck -s bash tools/run-target.sh`
Expected: no warnings.

- [ ] **Step 6: Commit**

```bash
git add tools/run-target.sh tests/test_shell.py
git commit -m "feat(run-target): add colored start/end banners for make targets"
```

---

### Task 3: `Makefile` — adopt banners, fix `restart`, deduplicate `UNIT`

**Files:**
- Modify: `Makefile`
- Create: `scripts/status.sh`
- Create: `scripts/logs.sh`
- Test: `tests/test_shell.py`

**Interfaces:**
- Consumes: `tools/run-target.sh` (Task 2), `tools/lib.sh`'s `UNIT_NAME`
  (already exported).
- Produces: every target's recipe becomes
  `@bash tools/run-target.sh <name> -- <command>`; `restart` becomes two
  recursive `$(MAKE)` calls instead of prerequisite-chaining, so it gets two
  distinct banners instead of one undifferentiated run; `status`/`logs`
  move into their own scripts sourcing `tools/lib.sh`'s `UNIT_NAME`, so the
  Makefile's separate `UNIT = llm-server` declaration is deleted rather than
  kept as a second, driftable copy of the same value.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_shell.py`:

```python
def test_makefile_wraps_every_target_with_run_target_banners() -> None:
    makefile = (ROOT / "Makefile").read_text()
    assert "UNIT = llm-server" not in makefile
    assert "@bash tools/run-target.sh start -- bash scripts/start.sh" in makefile
    assert "@bash tools/run-target.sh stop -- bash scripts/stop.sh" in makefile
    assert "@bash tools/run-target.sh status -- bash scripts/status.sh" in makefile
    assert "@bash tools/run-target.sh logs -- bash scripts/logs.sh" in makefile


def test_makefile_restart_chains_two_recursive_make_calls() -> None:
    makefile = (ROOT / "Makefile").read_text()
    assert "restart:\n\t@$(MAKE) --no-print-directory stop\n\t@$(MAKE) --no-print-directory start\n" in makefile


def test_status_and_logs_scripts_reference_unit_name_from_lib(tmp_path: pathlib.Path) -> None:
    status_text = (ROOT / "scripts/status.sh").read_text()
    logs_text = (ROOT / "scripts/logs.sh").read_text()
    assert "source" in status_text and "tools/lib.sh" in status_text
    assert "${UNIT_NAME}" in status_text
    assert "source" in logs_text and "tools/lib.sh" in logs_text
    assert "${UNIT_NAME}" in logs_text


def test_make_restart_runs_stop_then_start_with_distinct_banners() -> None:
    # `-n` is a dry run: make never executes any recipe line, so there is
    # nothing here for a fake `make` stub to intercept — a stub would only
    # matter for a real (non-`-n`) invocation. Instead, call the real `make`
    # binary directly (unmodified PATH) and rely on GNU make's documented
    # `-n` behavior: it still expands `$(MAKE)` to the make program's own
    # name and prints each nested recipe line it would run, which is enough
    # to prove `restart` triggers two independent `make` invocations (stop,
    # then start) instead of chaining through prerequisites.
    result = subprocess.run(
        ["/usr/bin/make", "--no-print-directory", "-n", "restart"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "make --no-print-directory stop" in result.stdout
    assert "make --no-print-directory start" in result.stdout
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --with pytest pytest tests/test_shell.py -v -k "makefile_wraps or makefile_restart or status_and_logs_scripts or make_restart_runs"`
Expected: FAIL — the current `Makefile` still declares `UNIT = llm-server`,
has one-line `@bash scripts/x.sh` recipes with no banner wrapper, and
`restart: stop start` as prerequisite-chaining, not recursive `$(MAKE)`
calls; `scripts/status.sh`/`scripts/logs.sh` do not exist yet.

- [ ] **Step 3: Create `scripts/status.sh` and `scripts/logs.sh`**

`scripts/status.sh`:

```bash
#!/usr/bin/env bash
# status.sh — show the service unit's status.
set -uo pipefail
# shellcheck disable=SC1091 # Resolved from this script at runtime.
# shellcheck source=../tools/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/../tools/lib.sh"

systemctl --user status "${UNIT_NAME}.service" --no-pager || true
```

`scripts/logs.sh`:

```bash
#!/usr/bin/env bash
# logs.sh — follow the service unit's logs.
set -uo pipefail
# shellcheck disable=SC1091 # Resolved from this script at runtime.
# shellcheck source=../tools/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/../tools/lib.sh"

journalctl --user -u "${UNIT_NAME}.service" -f
```

Make both executable: `chmod +x scripts/status.sh scripts/logs.sh`.

- [ ] **Step 4: Rewrite the `Makefile`**

```make
.PHONY: help prerequisites setup setup-local-llm-agents start stop restart check-setup check-server check-with-agents benchmark \
        key-reset enable-boot disable-boot status logs validate test clean

help:
	@bash tools/run-target.sh help -- bash scripts/help.sh

prerequisites:
	@bash tools/run-target.sh prerequisites -- bash setup/prerequisites.sh

setup:
	@bash tools/run-target.sh setup -- bash setup/setup.sh

setup-local-llm-agents:
	@bash tools/run-target.sh setup-local-llm-agents -- bash setup/setup-local-llm-agents.sh

start:
	@bash tools/run-target.sh start -- bash scripts/start.sh

stop:
	@bash tools/run-target.sh stop -- bash scripts/stop.sh

restart:
	@$(MAKE) --no-print-directory stop
	@$(MAKE) --no-print-directory start

check-setup:
	@bash tools/run-target.sh check-setup -- bash scripts/check-setup.sh

check-server:
	@bash tools/run-target.sh check-server -- bash scripts/check-server.sh

check-with-agents:
	@bash tools/run-target.sh check-with-agents -- bash scripts/check-with-agents.sh

benchmark:
	@bash tools/run-target.sh benchmark -- bash scripts/benchmark.sh

key-reset:
	@bash tools/run-target.sh key-reset -- bash scripts/key-reset.sh

enable-boot:
	@bash tools/run-target.sh enable-boot -- bash setup/enable-boot.sh

disable-boot:
	@bash tools/run-target.sh disable-boot -- bash setup/disable-boot.sh

status:
	@bash tools/run-target.sh status -- bash scripts/status.sh

logs:
	@bash tools/run-target.sh logs -- bash scripts/logs.sh

validate:
	@bash tools/run-target.sh validate -- bash tools/validate.sh

test:
	@bash tools/run-target.sh test -- bash tools/test.sh

clean:
	@bash tools/run-target.sh clean -- bash scripts/clean.sh
```

Note: `validate`/`test` here delegate to new `tools/validate.sh`/
`tools/test.sh` one-liners (`@shellcheck ...` / `@uvx ruff check ...` and
`@uv run --with pytest pytest tests/ -v` respectively, wrapped for a banner
the same as every other target) rather than keeping multi-command bodies
directly in the Makefile — this keeps every recipe uniformly one line and
avoids the `-e`-vs-`&&`-chaining question the old two-command `validate`
body had. Task 16 later simplifies what's inside `tools/test.sh`/
`tools/validate.sh` once `pyproject.toml` exists; for this task, their
content is a direct lift of the current Makefile bodies:

`tools/validate.sh`:

```bash
#!/usr/bin/env bash
# validate.sh — shellcheck + ruff.
set -euo pipefail

shellcheck -s bash ./tools/*.sh ./setup/*.sh ./scripts/*.sh
uvx ruff check llmenv.py pylib tests
echo "All checks passed."
```

`tools/test.sh`:

```bash
#!/usr/bin/env bash
# test.sh — Python test suite.
set -euo pipefail

uv run --with pytest pytest tests/ -v
```

Make both executable: `chmod +x tools/validate.sh tools/test.sh`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run --with pytest pytest tests/test_shell.py -v -k "makefile_wraps or makefile_restart or status_and_logs_scripts or make_restart_runs"`
Expected: PASS.

- [ ] **Step 6: Run the full shell test suite, `make help`, and shellcheck**

Run: `uv run --with pytest pytest tests/test_shell.py -v`
Run: `bash tools/validate.sh`
Run: `make help` (confirm it still lists every command with no error)
Expected: PASS; `make help`'s own banner ("▶ help" / "■ help end — ok") is
expected new output — if `tests/test_shell.py::test_make_help_describes_a_vulkan_only_benchmark`
starts failing because it now also matches banner text, adjust its
assertion to search `result.stdout.lower()` for `"rocm"` as it already
does — banner text contains neither "rocm" nor any of the other asserted
substrings in that test, so no change should be required, but confirm by
running it explicitly:
Run: `uv run --with pytest pytest tests/test_shell.py -v -k test_make_help_describes_a_vulkan_only_benchmark`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add Makefile scripts/status.sh scripts/logs.sh tools/validate.sh tools/test.sh tests/test_shell.py
git commit -m "feat(makefile): colored start/end banners, recursive restart, dedupe UNIT"
```

---

### Task 4: `tools/lib.sh` — `wait_for_health()` helper

**Files:**
- Modify: `tools/lib.sh`
- Test: `tests/test_shell.py`

**Interfaces:**
- Consumes: `LLM_ENV_HEALTH_TIMEOUT_SECONDS` (Task 1).
- Produces: `wait_for_health(port) -> 0` if `http://127.0.0.1:<port>/health`
  answers within `LLM_ENV_HEALTH_TIMEOUT_SECONDS` seconds (polling once per
  second, matching the current inline loop's cadence exactly), non-zero
  otherwise. A pure function of its argument and the environment — no
  globals besides the ones already exported by `lib.sh`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_shell.py`:

```python
def test_wait_for_health_succeeds_once_curl_reports_healthy(tmp_path: pathlib.Path) -> None:
    commands = tmp_path / "bin"
    commands.mkdir()
    curl = commands / "curl"
    curl.write_text("#!/usr/bin/bash\nexit 0\n")
    curl.chmod(curl.stat().st_mode | stat.S_IXUSR)

    script = tmp_path / "probe.sh"
    script.write_text(
        "#!/usr/bin/bash\nset -euo pipefail\n"
        f"source {ROOT / 'tools/lib.sh'}\n"
        "wait_for_health 8000\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)

    result = subprocess.run(
        ["/usr/bin/bash", str(script)],
        env=os.environ | {"PATH": f"{commands}:/usr/bin:/bin"},
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_wait_for_health_times_out_when_curl_never_succeeds(tmp_path: pathlib.Path) -> None:
    commands = tmp_path / "bin"
    commands.mkdir()
    curl = commands / "curl"
    curl.write_text("#!/usr/bin/bash\nexit 1\n")
    curl.chmod(curl.stat().st_mode | stat.S_IXUSR)

    script = tmp_path / "probe.sh"
    script.write_text(
        "#!/usr/bin/bash\nset -uo pipefail\n"
        f"source {ROOT / 'tools/lib.sh'}\n"
        "wait_for_health 8000\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)

    result = subprocess.run(
        ["/usr/bin/bash", str(script)],
        env=os.environ | {"PATH": f"{commands}:/usr/bin:/bin", "LLM_ENV_HEALTH_TIMEOUT_SECONDS": "2"},
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --with pytest pytest tests/test_shell.py -v -k test_wait_for_health`
Expected: FAIL — `wait_for_health: command not found`.

- [ ] **Step 3: Add `wait_for_health()` to `tools/lib.sh`**

```bash
wait_for_health() {
    local port="$1" attempt
    for (( attempt = 0; attempt < LLM_ENV_HEALTH_TIMEOUT_SECONDS; attempt++ )); do
        curl -fsS -o /dev/null "http://127.0.0.1:${port}/health" 2>/dev/null && return 0
        sleep 1
    done
    return 1
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --with pytest pytest tests/test_shell.py -v -k test_wait_for_health`
Expected: PASS (the timeout test uses `LLM_ENV_HEALTH_TIMEOUT_SECONDS=2` so
it completes quickly rather than waiting the real 60s default).

- [ ] **Step 5: Run shellcheck**

Run: `shellcheck -s bash tools/lib.sh`
Expected: no new warnings.

- [ ] **Step 6: Commit**

```bash
git add tools/lib.sh tests/test_shell.py
git commit -m "feat(lib): add wait_for_health helper"
```

---

### Task 5: `scripts/start.sh` — adopt `wait_for_health()`

**Files:**
- Modify: `scripts/start.sh`
- Test: `tests/test_shell.py`

**Interfaces:**
- Consumes: `wait_for_health()` (Task 4).
- Produces: identical externally-observable behavior to today (same
  60-second default timeout, same 127.0.0.1 probe, same success/failure
  messages) — this task only removes the duplicated inline loop.

- [ ] **Step 1: Confirm the existing lifecycle tests still pass first**

Run: `uv run --with pytest pytest tests/test_shell.py -v -k "start_generates_key or start_retains or start_stops_active or start_migrates or start_rejects"`
Expected: PASS against the *current* `start.sh` — this establishes the
before-state baseline these tests must still pass against after the
refactor (they exercise `start.sh`'s behavior end-to-end already; this task
does not add a new test, it proves an existing one keeps passing through a
pure refactor, which is itself the test cycle for this task).

- [ ] **Step 2: Replace the inline health-poll loop**

In `scripts/start.sh`, replace:

```bash
log_step "Waiting for health"
# Probe 127.0.0.1 explicitly: "localhost" resolves to ::1 first on this system while
# podman publishes the port on 0.0.0.0 (IPv4), so a localhost probe would never connect.
for _ in $(seq 1 60); do
    if curl -fsS -o /dev/null "http://127.0.0.1:${port}/health" 2>/dev/null; then
        log_info "server is ready"
        bash "${REPO_DIR}/setup/network.sh"
        exit 0
    fi
    sleep 1
done

log_error "server did not become healthy within 60s"
echo "  Logs: journalctl --user -u ${UNIT_NAME}.service -n 50"
exit 1
```

with:

```bash
log_step "Waiting for health"
# Probe 127.0.0.1 explicitly: "localhost" resolves to ::1 first on this system while
# podman publishes the port on 0.0.0.0 (IPv4), so a localhost probe would never connect.
if wait_for_health "$port"; then
    log_info "server is ready"
    bash "${REPO_DIR}/setup/network.sh"
    exit 0
fi

log_error "server did not become healthy within ${LLM_ENV_HEALTH_TIMEOUT_SECONDS}s"
echo "  Logs: journalctl --user -u ${UNIT_NAME}.service -n 50"
exit 1
```

- [ ] **Step 3: Run the lifecycle tests again to confirm no regression**

Run: `uv run --with pytest pytest tests/test_shell.py -v -k "start_generates_key or start_retains or start_stops_active or start_migrates or start_rejects"`
Expected: PASS, unchanged from Step 1.

- [ ] **Step 4: Run shellcheck**

Run: `shellcheck -s bash scripts/start.sh`
Expected: no new warnings.

- [ ] **Step 5: Commit**

```bash
git add scripts/start.sh
git commit -m "refactor(start): use the shared wait_for_health helper"
```

---

### Task 6: `tools/lib.sh` — `load_server_config()` helper

**Files:**
- Modify: `tools/lib.sh`
- Test: `tests/test_shell.py`

**Interfaces:**
- Produces: `load_server_config()` sets three globals — `PORT`, `API_KEY`,
  `HOST` — from a single pass over `$CONFIG_PATH` (three `yq` reads, same
  as calling all three individually once; the win is removing the *repeated*
  reads at each of the five call sites in Task 7, not reducing this
  function's own read count below three).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_shell.py`:

```python
def test_load_server_config_sets_port_api_key_and_host(tmp_path: pathlib.Path) -> None:
    config = tmp_path / "models.yml"
    config.write_text(
        "server:\n  host: 0.0.0.0\n  port: 9001\n  api_key: fixture-key\n"
    )
    script = tmp_path / "probe.sh"
    script.write_text(
        "#!/usr/bin/bash\nset -euo pipefail\n"
        f"source {ROOT / 'tools/lib.sh'}\n"
        "load_server_config\n"
        "printf 'PORT=%s API_KEY=%s HOST=%s\\n' \"$PORT\" \"$API_KEY\" \"$HOST\"\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)

    result = subprocess.run(
        ["/usr/bin/bash", str(script)],
        env=os.environ | {"LLM_ENV_CONFIG": str(config)},
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "PORT=9001 API_KEY=fixture-key HOST=0.0.0.0" in result.stdout
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --with pytest pytest tests/test_shell.py -v -k test_load_server_config`
Expected: FAIL — `load_server_config: command not found`.

- [ ] **Step 3: Add `load_server_config()` to `tools/lib.sh`**

```bash
load_server_config() {
    PORT="$(yq -r '.server.port' "$CONFIG_PATH")"
    API_KEY="$(yq -r '.server.api_key' "$CONFIG_PATH")"
    HOST="$(yq -r '.server.host' "$CONFIG_PATH")"
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --with pytest pytest tests/test_shell.py -v -k test_load_server_config`
Expected: PASS.

- [ ] **Step 5: Run shellcheck**

Run: `shellcheck -s bash tools/lib.sh`
Expected: no new warnings.

- [ ] **Step 6: Commit**

```bash
git add tools/lib.sh tests/test_shell.py
git commit -m "feat(lib): add load_server_config helper"
```

---

### Task 7: Adopt `load_server_config()` at its call sites

**Files:**
- Modify: `scripts/check-server.sh`
- Modify: `scripts/check-with-agents.sh`
- Modify: `setup/network.sh`
- Modify: `setup/setup-local-llm-agents.sh`
- Modify: `setup/render-unit.sh`
- Test: `tests/test_shell.py`

**Interfaces:**
- Consumes: `load_server_config()` (Task 6); `LLM_ENV_HEALTH_TIMEOUT_SECONDS`
  (Task 1, already exported by `tools/lib.sh`, which `render-unit.sh`
  already sources).
- Produces: identical externally-observable behavior at four of the five
  call sites — verified by the existing test suites for those scripts
  continuing to pass, not by a new test. `setup/render-unit.sh` is the
  exception: alongside its `load_server_config()` adoption, this task also
  makes the generated mDNS unit's `ExecStartPre` health-poll loop
  interpolate `${LLM_ENV_HEALTH_TIMEOUT_SECONDS}` instead of an
  independent hardcoded `60` literal, so the design's stated requirement
  that the generated unit "at least reference the same timeout constant"
  as `wait_for_health()` (Task 4) is actually met — this one sub-change
  *is* a new, testable, externally-observable behavior (a custom
  `LLM_ENV_HEALTH_TIMEOUT_SECONDS` value now shows up in the rendered
  unit file), so it gets its own failing test in Step 2 below rather than
  relying solely on the "existing tests still pass" baseline the other
  four files use.

- [ ] **Step 1: Confirm the existing tests for all five scripts pass first**

Run: `uv run --with pytest pytest tests/test_shell.py -v -k "check_server or check_with_agents or setup_local_llm_agents or render_unit or network"`
Expected: PASS against the current scripts — this is the before-state
baseline for this refactor's test cycle.

- [ ] **Step 2: Write the failing test for `render-unit.sh`'s health-timeout interpolation**

`run_lifecycle_script()` (used by the existing `render_unit`/`enable_boot`
tests) does not currently offer a way to override an environment variable
for the script under test — add one, as an additional keyword-only
parameter defaulting to `None` so every existing call site is unaffected.
In `tests/test_shell.py`, change `run_lifecycle_script()`'s signature:

```python
def run_lifecycle_script(
    tmp_path: pathlib.Path,
    script: str,
    *,
    api_key: str = "existing-key",
    active: bool = False,
    config_mode: int = 0o600,
    parallel_slots: int = 1,
    sampling_temperature: str | None = None,
    env_overrides: dict[str, str] | None = None,
) -> tuple[subprocess.CompletedProcess[str], pathlib.Path, pathlib.Path]:
```

and change the `environment = os.environ | {...}` assignment near the end
of the function so the overrides are applied last (so a caller-supplied
value always wins over the function's own defaults):

```python
    environment = os.environ | {
        "ACTIVE": "1" if active else "0",
        "CALLS": str(calls),
        "HOME": str(home),
        "LLM_ENV_CONFIG": str(config),
        "LLM_ENV_MODELS_DIR": str(tmp_path / "models"),
        "MDNS_UNIT": str(home / ".config/systemd/user/llm-server-mdns.service"),
        "PATH": f"{commands}:/usr/bin:/bin",
        "REAL_YQ": real_yq,
        "REAL_UV": real_uv,
    } | (env_overrides or {})
```

Then append the new test:

```python
def test_render_unit_mdns_execstartpre_uses_the_configured_health_timeout(
    tmp_path: pathlib.Path,
) -> None:
    """The generated mDNS unit's health poll must track the shared timeout,
    not an independent hardcoded 60 — same requirement wait_for_health()
    (Task 4) exists to satisfy for start.sh."""
    result, config, _ = run_lifecycle_script(
        tmp_path,
        "setup/enable-boot.sh",
        env_overrides={"LLM_ENV_HEALTH_TIMEOUT_SECONDS": "77"},
    )
    mdns_unit = config.parent.parent / "systemd/user/llm-server-mdns.service"

    assert result.returncode == 0, result.stderr
    unit = mdns_unit.read_text()
    assert "-lt 77" in unit
    assert "-lt 60" not in unit
```

- [ ] **Step 3: Run the new test to verify it fails**

Run: `uv run --with pytest pytest tests/test_shell.py -v -k test_render_unit_mdns_execstartpre_uses_the_configured_health_timeout`
Expected: FAIL — the current `render-unit.sh` heredoc always writes the
literal `-lt 60`, so `"-lt 77" in unit` is false regardless of
`LLM_ENV_HEALTH_TIMEOUT_SECONDS`.

- [ ] **Step 4: `scripts/check-server.sh`**

Replace:

```bash
port="$(yq -r '.server.port' "$CONFIG_PATH")"
api_key="$(yq -r '.server.api_key' "$CONFIG_PATH")"
```

with:

```bash
load_server_config
port="$PORT"
api_key="$API_KEY"
```

(Keeping the lowercase local names `port`/`api_key` used throughout the
rest of the file, rather than renaming every subsequent reference, keeps
this a minimal, reviewable diff.)

- [ ] **Step 5: `scripts/check-with-agents.sh`**

Replace:

```bash
port="$(yq -r '.server.port' "$CONFIG_PATH")"
api_key="$(yq -r '.server.api_key' "$CONFIG_PATH")"
```

with:

```bash
load_server_config
port="$PORT"
api_key="$API_KEY"
```

- [ ] **Step 6: `setup/network.sh`**

Replace:

```bash
port="$(yq -r '.server.port' "$CONFIG_PATH")"
mdns="$(yq -r '.server.mdns_name' "$CONFIG_PATH")"
```

with:

```bash
load_server_config
port="$PORT"
mdns="$(yq -r '.server.mdns_name' "$CONFIG_PATH")"
```

(`mdns_name` is not part of `load_server_config`'s three fields, so that
read stays as-is.)

- [ ] **Step 7: `setup/setup-local-llm-agents.sh`**

Replace:

```bash
port="$(yq -r '.server.port // ""' "$CONFIG_PATH")"
api_key="$(yq -r '.server.api_key // ""' "$CONFIG_PATH")"
```

with:

```bash
load_server_config
port="${PORT:-}"
api_key="${API_KEY:-}"
```

Note the `// ""` fallback in the original `yq` filter guards against a
missing key; `load_server_config`'s plain `yq -r '.server.port'` on a
missing key returns the literal string `null`, not empty — this file's own
subsequent validation (`jq -ne --arg port "$port" '$port | test(...)'`)
already rejects `null` as an invalid port, and the `api_key` emptiness
check (`[ -n "$api_key" ]`) needs `null` treated as non-empty-but-invalid,
not silently passing. Since this script already requires a valid,
non-empty config (it dies immediately after with "server API key is
empty" if unset), preserve the original two-line reads here **instead** of
adopting the helper, rather than changing this script's validation
semantics as a side effect of a supposedly pure refactor:

```bash
port="$(yq -r '.server.port // ""' "$CONFIG_PATH")"
api_key="$(yq -r '.server.api_key // ""' "$CONFIG_PATH")"
```

(i.e., leave `setup/setup-local-llm-agents.sh` unmodified — its `// ""`
fallback is a deliberate difference from the other four call sites, not an
oversight, and folding it into the shared helper would either break this
script's `null`-vs-empty handling or require `load_server_config()` itself
to special-case a fallback every other caller does not need.)

- [ ] **Step 8: `setup/render-unit.sh`**

Replace:

```bash
host="$(yq -r '.server.host' "$CONFIG_PATH")"
port="$(yq -r '.server.port' "$CONFIG_PATH")"
api_key="$(yq -r '.server.api_key' "$CONFIG_PATH")"
```

with:

```bash
load_server_config
host="$HOST"
port="$PORT"
api_key="$API_KEY"
```

(Leave the surrounding `backend`/`image`/`sleep_idle`/`models_max` reads on
that same line block untouched — they are not part of
`load_server_config`'s scope.)

Then, in the same file, replace the mDNS unit's `ExecStartPre` line:

```bash
ExecStartPre=/usr/bin/bash -c 'i=0; while [ \$\$i -lt 60 ]; do ${curl_path} -fsS -o /dev/null http://127.0.0.1:${port}/health && exit 0; i=\$\$((i + 1)); sleep 1; done; exit 1'
```

with:

```bash
ExecStartPre=/usr/bin/bash -c 'i=0; while [ \$\$i -lt ${LLM_ENV_HEALTH_TIMEOUT_SECONDS} ]; do ${curl_path} -fsS -o /dev/null http://127.0.0.1:${port}/health && exit 0; i=\$\$((i + 1)); sleep 1; done; exit 1'
```

(This line sits inside the unquoted `cat > "$mdns_unit" <<EOF` heredoc
that already interpolates `${curl_path}`/`${port}`/`${mdns_name}` the same
way — `${LLM_ENV_HEALTH_TIMEOUT_SECONDS}` expands exactly like those do,
at heredoc-write time in this script's own shell, not at
systemd-unit-run time. `LLM_ENV_HEALTH_TIMEOUT_SECONDS` is already
exported by `tools/lib.sh`, Task 1, which this script sources at the top,
so no new `source`/`require_cmd` line is needed. The single-quoted
`'...'` around the whole `bash -c` argument, and the `\$\$` escaping of
the loop counter `i`, are unchanged — those protect `$i`'s *runtime*
expansion inside the generated unit's own `bash -c` invocation from this
script's heredoc expansion; `${LLM_ENV_HEALTH_TIMEOUT_SECONDS}` is meant
to expand now, at generation time, exactly like `${port}` already does on
the same line, so it takes the same `${...}` form, not the `\$\$`-escaped
form.)

- [ ] **Step 9: Run the five scripts' test suites again to confirm no regression, and confirm the new render-unit test now passes**

Run: `uv run --with pytest pytest tests/test_shell.py -v -k "check_server or check_with_agents or setup_local_llm_agents or render_unit or network"`
Expected: PASS, unchanged from Step 1, plus
`test_render_unit_mdns_execstartpre_uses_the_configured_health_timeout`
(added in Step 2) now PASSES too — it matches the `render_unit` filter
above, so it is already included in this run; no separate command is
needed.

- [ ] **Step 10: Run shellcheck on all five files**

Run: `shellcheck -s bash scripts/check-server.sh scripts/check-with-agents.sh setup/network.sh setup/setup-local-llm-agents.sh setup/render-unit.sh`
Expected: no new warnings.

- [ ] **Step 11: Commit**

```bash
git add scripts/check-server.sh scripts/check-with-agents.sh setup/network.sh setup/render-unit.sh tests/test_shell.py
git commit -m "refactor: adopt load_server_config at its call sites; render-unit mDNS health-poll tracks the shared timeout"
```

(`setup/setup-local-llm-agents.sh` is intentionally excluded from this
commit — see Step 7.)

---

### Task 8: Adopt `VULKAN_IMAGE`/`CPU_IMAGE` in `benchmark.sh` and `setup.sh`

**Files:**
- Modify: `scripts/benchmark.sh`
- Modify: `setup/setup.sh`
- Test: `tests/test_shell.py`

**Interfaces:**
- Consumes: `VULKAN_IMAGE`/`CPU_IMAGE` (Task 1, already exported by
  `tools/lib.sh`, which both scripts already source).
- Produces: identical externally-observable behavior — the two image
  literals now have exactly one declaration (`tools/lib.sh`) instead of
  three (`benchmark.sh` plus the two hardcoded occurrences already fixed by
  removing them here).

- [ ] **Step 1: Confirm the existing benchmark/setup tests pass first**

Run: `uv run --with pytest pytest tests/test_shell.py -v -k "benchmark or setup_gpu or setup_writes"`
Expected: PASS against the current scripts.

- [ ] **Step 2: `scripts/benchmark.sh`**

Remove the now-redundant local declarations:

```bash
VULKAN_IMAGE="ghcr.io/ggml-org/llama.cpp:server-vulkan"
CPU_IMAGE="ghcr.io/ggml-org/llama.cpp:server"
```

(both names are already exported by `tools/lib.sh`, which this script
sources at the top — no other line in the file changes, since every
subsequent reference already just uses `$VULKAN_IMAGE`/`$CPU_IMAGE`).

- [ ] **Step 3: `setup/setup.sh`**

Replace:

```bash
podman pull ghcr.io/ggml-org/llama.cpp:server-vulkan >/dev/null
vulkan_listing="podman run --rm --device /dev/dri ghcr.io/ggml-org/llama.cpp:server-vulkan --list-devices"
```

with:

```bash
podman pull "$VULKAN_IMAGE" >/dev/null
vulkan_listing="podman run --rm --device /dev/dri ${VULKAN_IMAGE} --list-devices"
```

- [ ] **Step 4: Run the tests again to confirm no regression**

Run: `uv run --with pytest pytest tests/test_shell.py -v -k "benchmark or setup_gpu or setup_writes"`
Expected: PASS, unchanged.

- [ ] **Step 5: Run shellcheck**

Run: `shellcheck -s bash scripts/benchmark.sh setup/setup.sh`
Expected: no new warnings.

- [ ] **Step 6: Commit**

```bash
git add scripts/benchmark.sh setup/setup.sh
git commit -m "refactor: adopt shared VULKAN_IMAGE/CPU_IMAGE constants"
```

---

### Task 9: `scripts/clean.sh` — read `gpu.image` instead of hardcoding it

**Files:**
- Modify: `scripts/clean.sh`
- Test: `tests/test_shell.py`

**Interfaces:**
- Produces: `clean.sh` removes the image actually recorded in the config's
  `gpu.image` field, falling back to `$VULKAN_IMAGE`/`$CPU_IMAGE` (Task 1)
  only when no config is present to read from — so a repointed
  `gpu.image` (a custom build, a pinned digest) actually gets cleaned up
  instead of leaving the real image orphaned while a stale hardcoded
  literal gets removed instead. The pre-confirmation "This removes:"
  disclosure the user sees *before* typing "yes" is updated to show that
  same resolved image — not the old hardcoded
  `ghcr.io/ggml-org/llama.cpp:server-vulkan and server` literal — since
  the whole point of this task is that the actually-configured image may
  differ from those defaults, and the prompt shown right before deletion
  must not misstate what is about to be removed. Both the prompt and the
  later `podman rmi` call read `gpu.image` exactly once, near the top of
  the script, into a variable that both places share; the original code
  read `.server.port`-style config fields immediately before use, but
  `clean.sh` itself later runs `rm -f "$CONFIG_PATH"` — so a second,
  later `yq` read of `gpu.image` (as opposed to reusing the one taken
  before the prompt) would always find an already-deleted file and
  silently fall back to the defaults, defeating this task's fix. Reading
  once up front avoids that trap entirely.

**Sequencing note:** if the sibling compose plan's Task 10 has already
landed, `clean.sh`'s current body already reads `${COMPOSE_FILE}` and runs
`podman compose … down` — apply this task's `gpu.image` change on top of
that version instead of the block quoted below (which reflects the
Quadlet-era file, current as of this plan's writing). Preserve the same
principle regardless of which version you're modifying: resolve
`gpu.image` once, before the confirmation prompt, and reuse that value
for both the prompt text and the actual removal.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_shell.py`:

```python
def test_cleanup_removes_the_configured_gpu_image_not_a_hardcoded_one(
    tmp_path: pathlib.Path,
) -> None:
    real_yq = shutil.which("yq")
    assert real_yq is not None

    commands = tmp_path / "bin"
    commands.mkdir()
    _mock_command(commands, "systemctl")
    yq = commands / "yq"
    yq.write_text("#!/usr/bin/bash\nexec \"$REAL_YQ\" \"$@\"\n")
    yq.chmod(yq.stat().st_mode | stat.S_IXUSR)
    calls = tmp_path / "calls"
    podman = commands / "podman"
    podman.write_text("#!/usr/bin/bash\nprintf 'podman %s\\n' \"$*\" >> \"$CALLS\"\n")
    podman.chmod(podman.stat().st_mode | stat.S_IXUSR)

    home = tmp_path / "home"
    config = home / ".config/llm-env/models.yml"
    config.parent.mkdir(parents=True)
    config.write_text(
        "gpu:\n  image: example.invalid/custom-build:pinned\n"
    )

    environment = os.environ | {
        "CALLS": str(calls),
        "HOME": str(home),
        "LLM_ENV_CONFIG": str(config),
        "LLM_ENV_ASSUME_YES": "1",
        "PATH": f"{commands}:/usr/bin:/bin",
        "REAL_YQ": real_yq,
    }
    result = subprocess.run(
        ["/usr/bin/bash", "scripts/clean.sh"],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "example.invalid/custom-build:pinned" in calls.read_text()


def test_cleanup_falls_back_to_default_images_without_a_config(
    tmp_path: pathlib.Path,
) -> None:
    commands = tmp_path / "bin"
    commands.mkdir()
    _mock_command(commands, "systemctl")
    calls = tmp_path / "calls"
    podman = commands / "podman"
    podman.write_text("#!/usr/bin/bash\nprintf 'podman %s\\n' \"$*\" >> \"$CALLS\"\n")
    podman.chmod(podman.stat().st_mode | stat.S_IXUSR)

    environment = os.environ | {
        "CALLS": str(calls),
        "HOME": str(tmp_path / "home"),
        "LLM_ENV_CONFIG": str(tmp_path / "home/.config/llm-env/models.yml"),  # absent
        "LLM_ENV_ASSUME_YES": "1",
        "PATH": f"{commands}:/usr/bin:/bin",
    }
    result = subprocess.run(
        ["/usr/bin/bash", "scripts/clean.sh"],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "ghcr.io/ggml-org/llama.cpp:server-vulkan" in calls.read_text()


def test_cleanup_confirmation_prompt_reflects_the_configured_gpu_image(
    tmp_path: pathlib.Path,
) -> None:
    real_yq = shutil.which("yq")
    assert real_yq is not None

    commands = tmp_path / "bin"
    commands.mkdir()
    _mock_command(commands, "systemctl")
    _mock_command(commands, "podman")
    yq = commands / "yq"
    yq.write_text("#!/usr/bin/bash\nexec \"$REAL_YQ\" \"$@\"\n")
    yq.chmod(yq.stat().st_mode | stat.S_IXUSR)

    home = tmp_path / "home"
    config = home / ".config/llm-env/models.yml"
    config.parent.mkdir(parents=True)
    config.write_text("gpu:\n  image: example.invalid/custom-build:pinned\n")

    environment = os.environ | {
        "HOME": str(home),
        "LLM_ENV_CONFIG": str(config),
        "LLM_ENV_ASSUME_YES": "1",
        "PATH": f"{commands}:/usr/bin:/bin",
        "REAL_YQ": real_yq,
    }
    result = subprocess.run(
        ["/usr/bin/bash", "scripts/clean.sh"],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "example.invalid/custom-build:pinned" in result.stdout
    assert "ghcr.io/ggml-org/llama.cpp:server-vulkan and server" not in result.stdout
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --with pytest pytest tests/test_shell.py -v -k "cleanup_removes_the_configured_gpu_image or cleanup_falls_back_to_default or cleanup_confirmation_prompt_reflects"`
Expected: FAIL — the current `clean.sh` always removes the two hardcoded
image literals unconditionally, so
`"example.invalid/custom-build:pinned" in calls.read_text()` is false in
the first test, and it always prints the hardcoded
`"images  ghcr.io/ggml-org/llama.cpp:server-vulkan and server"` line
regardless of `gpu.image`, so the third test's
`"ghcr.io/ggml-org/llama.cpp:server-vulkan and server" not in result.stdout`
assertion is false too.

- [ ] **Step 3: Update `scripts/clean.sh`**

The current file (see Task 9's header for the exact text this plan was
written against) reads no config at all before printing its "This
removes:" disclosure, and unconditionally removes the two hardcoded image
literals at the end. Both of those need to change together, and the
`gpu.image` read needs to happen **before** the confirmation prompt (and
therefore before `rm -f "$CONFIG_PATH"` runs later in the script) so the
same resolved value can back both the prompt text and the actual
removal — reading it a second time after the config file is already
deleted would silently fall back to the defaults every time.

Replace:

```bash
echo "This removes:"
echo "  unit    ${QUADLET_DIR}/${UNIT_NAME}.container"
echo "  config  ${CONFIG_PATH}"
echo "  images  ghcr.io/ggml-org/llama.cpp:server-vulkan and server"
echo "Downloaded models in ${MODELS_DIR} are KEPT."
```

with:

```bash
configured_image=""
if [ -f "$CONFIG_PATH" ]; then
    configured_image="$(yq -r '.gpu.image // ""' "$CONFIG_PATH" 2>/dev/null || true)"
fi
if [ -n "$configured_image" ] && [ "$configured_image" != null ]; then
    images_to_remove="$configured_image"
else
    configured_image=""
    images_to_remove="${VULKAN_IMAGE} and ${CPU_IMAGE} (default; no configured gpu.image found)"
fi

echo "This removes:"
echo "  unit    ${QUADLET_DIR}/${UNIT_NAME}.container"
echo "  config  ${CONFIG_PATH}"
echo "  images  ${images_to_remove}"
echo "Downloaded models in ${MODELS_DIR} are KEPT."
```

(`configured_image` is deliberately reset to `""` in the fallback branch
even though it may have held the literal string `null` — this keeps the
later removal logic a single, simple `[ -n "$configured_image" ]` check
rather than repeating the `!= null` comparison a second time.)

Then replace the final image-removal line:

```bash
podman rmi -f ghcr.io/ggml-org/llama.cpp:server-vulkan \
                ghcr.io/ggml-org/llama.cpp:server 2>/dev/null || true
```

with:

```bash
if [ -n "$configured_image" ]; then
    podman rmi -f "$configured_image" 2>/dev/null || true
else
    podman rmi -f "$VULKAN_IMAGE" "$CPU_IMAGE" 2>/dev/null || true
fi
```

This reuses the `configured_image`/`images_to_remove` variables computed
in the first replacement above — no second `yq` read of `$CONFIG_PATH`
happens down here, which matters because by this point in the script
`rm -f "$CONFIG_PATH"` has already run.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --with pytest pytest tests/test_shell.py -v -k "cleanup_removes_the_configured_gpu_image or cleanup_falls_back_to_default or cleanup_confirmation_prompt_reflects"`
Expected: PASS.

- [ ] **Step 5: Run the pre-existing cleanup test and shellcheck**

Run: `uv run --with pytest pytest tests/test_shell.py -v -k test_cleanup_preserves_the_host_rocm_image`
Run: `shellcheck -s bash scripts/clean.sh`
Expected: PASS; no new warnings.

- [ ] **Step 6: Commit**

```bash
git add scripts/clean.sh tests/test_shell.py
git commit -m "fix(clean): remove and disclose the configured gpu.image instead of a hardcoded one"
```

---

### Task 10: `pylib/transcript.py` — JSONL transcript classifier

**Files:**
- Create: `pylib/transcript.py`
- Test: `tests/test_transcript.py`

**Interfaces:**
- Produces:
  `classify_transcript(client: Literal["pi", "opencode"], transcript_path: Path) -> str`,
  returning a compact excerpt: the final assistant text (if extractable, via
  the same per-client logic `check-with-agents.sh` already uses for its
  success path) plus any line whose parsed JSON object looks error-shaped
  (a top-level `"error"` key, or a `"type"` field containing the substring
  `"error"`, case-insensitively). If neither is found in a non-empty
  transcript, falls back to the last 5 non-blank lines rather than nothing,
  so an unrecognized failure shape still surfaces something. This
  error-shape heuristic is deliberately format-agnostic rather than tied to
  a specific documented Pi/OpenCode error schema, since neither client
  publishes one — flag during review whether real Pi/OpenCode failure
  transcripts actually produce a `"type"` containing `"error"` or a
  top-level `"error"` key; if not, the fallback tail still shows *something*
  rather than silently hiding an unrecognized failure shape.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_transcript.py`:

```python
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pylib.transcript import classify_transcript


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(record) for record in records) + "\n")


def test_pi_final_text_is_extracted(tmp_path):
    transcript = tmp_path / "t.jsonl"
    write_jsonl(
        transcript,
        [
            {"type": "message_start", "message": {"role": "assistant"}},
            {
                "type": "message_end",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "ready"}],
                },
            },
        ],
    )
    result = classify_transcript("pi", transcript)
    assert "ready" in result
    assert "message_start" not in result


def test_opencode_final_text_is_extracted(tmp_path):
    transcript = tmp_path / "t.jsonl"
    write_jsonl(
        transcript,
        [
            {"type": "text", "part": {"type": "text", "messageID": "m1", "text": "rea"}},
            {"type": "text", "part": {"type": "text", "messageID": "m1", "text": "dy"}},
        ],
    )
    result = classify_transcript("opencode", transcript)
    assert "ready" in result


def test_error_shaped_events_are_kept(tmp_path):
    transcript = tmp_path / "t.jsonl"
    write_jsonl(
        transcript,
        [
            {"type": "tool_call", "tool": "bash"},
            {"type": "tool_error", "message": "command not found"},
        ],
    )
    result = classify_transcript("pi", transcript)
    assert "tool_error" in result
    assert "command not found" in result
    assert "tool_call" not in result


def test_top_level_error_key_is_kept(tmp_path):
    transcript = tmp_path / "t.jsonl"
    write_jsonl(transcript, [{"error": "connection refused"}])
    result = classify_transcript("pi", transcript)
    assert "connection refused" in result


def test_falls_back_to_tail_when_nothing_recognized(tmp_path):
    transcript = tmp_path / "t.jsonl"
    write_jsonl(transcript, [{"type": "ping"}, {"type": "pong"}])
    result = classify_transcript("pi", transcript)
    assert "ping" in result
    assert "pong" in result


def test_empty_transcript_reports_empty(tmp_path):
    transcript = tmp_path / "t.jsonl"
    transcript.write_text("")
    assert classify_transcript("pi", transcript) == "(transcript is empty)"


def test_malformed_json_lines_are_skipped_not_raised(tmp_path):
    transcript = tmp_path / "t.jsonl"
    transcript.write_text("not json\n" + json.dumps({"error": "real"}) + "\n")
    result = classify_transcript("pi", transcript)
    assert "real" in result


def test_unsupported_client_raises():
    with pytest.raises(ValueError):
        classify_transcript("unsupported", Path("/dev/null"))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --with pytest pytest tests/test_transcript.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pylib.transcript'`.

- [ ] **Step 3: Implement `pylib/transcript.py`**

```python
"""Classify a JSONL agent transcript into a compact, relevant excerpt.

Keeps the final assistant text and any event that looks error-shaped, and
drops routine framing (tool calls, reasoning deltas, message-start markers).
If nothing recognizable is found, falls back to the transcript's last few
lines instead of nothing, so an unrecognized failure shape still surfaces.
"""

from __future__ import annotations

import json
from pathlib import Path

FALLBACK_TAIL_LINES = 5
SUPPORTED_CLIENTS = ("pi", "opencode")


def _parse_lines(transcript_path: Path) -> list[tuple[str, dict | None]]:
    text = transcript_path.read_text(encoding="utf-8", errors="replace")
    parsed: list[tuple[str, dict | None]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            record = json.loads(stripped)
        except json.JSONDecodeError:
            parsed.append((stripped, None))
            continue
        parsed.append((stripped, record if isinstance(record, dict) else None))
    return parsed


def _is_error_shaped(record: dict) -> bool:
    if "error" in record:
        return True
    record_type = record.get("type")
    return isinstance(record_type, str) and "error" in record_type.lower()


def _extract_final_text_pi(lines: list[tuple[str, dict | None]]) -> str:
    text = ""
    for _, record in lines:
        if record is None:
            continue
        if record.get("type") != "message_end":
            continue
        message = record.get("message")
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        text = "".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    return text


def _extract_final_text_opencode(lines: list[tuple[str, dict | None]]) -> str:
    message_id: str | None = None
    text = ""
    for _, record in lines:
        if record is None or record.get("type") != "text":
            continue
        part = record.get("part")
        if not isinstance(part, dict) or part.get("type") != "text":
            continue
        if part.get("messageID") != message_id:
            message_id = part.get("messageID")
            text = ""
        text += part.get("text", "")
    return text


_FINAL_TEXT_EXTRACTORS = {
    "pi": _extract_final_text_pi,
    "opencode": _extract_final_text_opencode,
}


def classify_transcript(client: str, transcript_path: Path) -> str:
    if client not in SUPPORTED_CLIENTS:
        raise ValueError(f"unsupported client: {client!r}; expected one of {SUPPORTED_CLIENTS}")

    lines = _parse_lines(Path(transcript_path))
    if not lines:
        return "(transcript is empty)"

    final_text = _FINAL_TEXT_EXTRACTORS[client](lines)
    error_lines = [raw for raw, record in lines if record is not None and _is_error_shaped(record)]

    sections: list[str] = []
    if final_text:
        sections.append(f"Final assistant text:\n{final_text}")
    if error_lines:
        sections.append("Error-shaped events:\n" + "\n".join(error_lines))
    if not sections:
        tail = [raw for raw, _ in lines][-FALLBACK_TAIL_LINES:]
        sections.append(
            "No recognized error event; last lines of transcript:\n" + "\n".join(tail)
        )

    return "\n\n".join(sections)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --with pytest pytest tests/test_transcript.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pylib/transcript.py tests/test_transcript.py
git commit -m "feat(transcript): classify JSONL agent transcripts into relevant excerpts"
```

---

### Task 11: `llmenv.py classify-transcript` subcommand

**Files:**
- Modify: `llmenv.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `classify_transcript()` (Task 10).
- Produces: `llmenv classify-transcript --client <pi|opencode> --transcript <path>`
  emitting `{"excerpt": "<classified text>"}`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cli.py`:

```python
def test_classify_transcript_emits_an_excerpt(tmp_path):
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(json.dumps({"error": "boom"}) + "\n")
    result = run(
        "classify-transcript",
        "--client", "pi",
        "--transcript", str(transcript),
    )
    assert result.returncode == 0, result.stderr
    assert "boom" in json.loads(result.stdout)["excerpt"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --with pytest pytest tests/test_cli.py -v -k test_classify_transcript`
Expected: FAIL — `argparse` rejects the unknown `classify-transcript`
subcommand.

- [ ] **Step 3: Wire the subcommand**

In `llmenv.py`, add to the import block:

```python
from pylib.transcript import classify_transcript
```

Add the command function near `cmd_validate_gguf`:

```python
def cmd_classify_transcript(args: argparse.Namespace) -> int:
    excerpt = classify_transcript(args.client, Path(args.transcript))
    return emit({"excerpt": excerpt})
```

In `build_parser()`:

```python
    classify_transcript_parser = sub.add_parser("classify-transcript")
    classify_transcript_parser.add_argument("--client", required=True, choices=["pi", "opencode"])
    classify_transcript_parser.add_argument("--transcript", required=True)
    classify_transcript_parser.set_defaults(func=cmd_classify_transcript)
```

(No change to `main()`'s exception tuple is needed — `--client`'s
`choices=` already rejects an unsupported client at the argparse layer
before `classify_transcript()`'s own `ValueError` guard could ever fire
from the CLI.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --with pytest pytest tests/test_cli.py -v -k test_classify_transcript`
Expected: PASS.

- [ ] **Step 5: Run the full test suite**

Run: `uv run --with pytest pytest tests/ -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add llmenv.py tests/test_cli.py
git commit -m "feat(cli): add llmenv classify-transcript subcommand"
```

---

### Task 12: `scripts/check-with-agents.sh` — use the classifier on failure

**Files:**
- Modify: `scripts/check-with-agents.sh`
- Test: `tests/test_shell.py`

**Interfaces:**
- Consumes: `llmenv classify-transcript` (Task 11), the existing `llmenv()`
  wrapper in `tools/lib.sh`.
- Produces: on failure, the terminal output shows a "Relevant transcript
  excerpt" block (the classifier's output) instead of the full raw JSONL
  transcript. The complete raw transcript remains on disk exactly as today
  via `LLM_ENV_KEEP_CHECK_ARTIFACTS=1` (`tools/lib.sh:131-190`, unchanged by
  this task) — this is strictly "concise by default, full detail one env
  var away," not a reduction in what's recoverable.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_shell.py`. This exercises the failure path directly
by constructing a transcript file with a recognizable error-shaped event
and confirming the script's stdout contains the classified excerpt rather
than the raw JSONL line shape, using the client-agnostic parts of this
file's existing `check-with-agents.sh` stubbing conventions (an installed
`pi`/`opencode` binary is not required for this specific assertion, since
it targets the logging helper directly):

```python
def test_check_with_agents_shows_classified_excerpt_not_raw_transcript_on_failure(
    tmp_path: pathlib.Path,
) -> None:
    real_yq = shutil.which("yq")
    real_uv = shutil.which("uv")
    assert real_yq is not None
    assert real_uv is not None

    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(
        '{"type": "message_start"}\n'
        '{"type": "tool_error", "message": "boom-diagnostic-marker"}\n'
    )

    script = tmp_path / "probe.sh"
    script.write_text(
        "#!/usr/bin/bash\nset -uo pipefail\n"
        f"source {ROOT / 'tools/lib.sh'}\n"
        f"classified_json=\"$(llmenv classify-transcript --client pi --transcript {transcript})\"\n"
        "excerpt=\"$(echo \"$classified_json\" | jq -r '.excerpt')\"\n"
        "log_block \"Relevant transcript excerpt\" \"$excerpt\"\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)

    result = subprocess.run(
        ["/usr/bin/bash", str(script)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "boom-diagnostic-marker" in result.stdout
    assert "message_start" not in result.stdout
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --with pytest pytest tests/test_shell.py -v -k test_check_with_agents_shows_classified_excerpt`
Expected: This specific test actually exercises the `llmenv
classify-transcript`/`log_block` combination directly rather than
`check-with-agents.sh` itself, so it should already PASS once Tasks 10–11
are in place — it is written this way deliberately, to prove the
*building block* `check-with-agents.sh` is about to adopt behaves exactly
as intended, in isolation, before wiring it into the larger script. Confirm
that first. Then proceed to Step 3, which is the actual `check-with-agents.sh`
change — there is no separate "fails then passes" cycle for the full script
change itself, since the existing failure-path tests for this script (see
Step 4) do not currently assert anything about raw-vs-classified transcript
content one way or the other, so they do not fail first; Step 4 re-runs
them purely as a regression check after Step 3's edit.

- [ ] **Step 3: Update `scripts/check-with-agents.sh`**

There are two occurrences of the raw-transcript dump on the failure paths,
at different indentation depths — the two blocks below are not
interchangeable; match each one to its own location, not to whichever
looks similar.

The first, inside the `if [ "$agent_failed" -ne 0 ]` block (16-space base
indent — this is the deeper-nested of the two, since it sits inside that
`if` in addition to the enclosing loop):

```bash
                if [ -s "$transcript_file" ]; then
                    log_file_excerpt "Client JSONL transcript" "$transcript_file" "$agent_diagnostic_excerpt_bytes"
                fi
```

Replace with:

```bash
                if [ -s "$transcript_file" ]; then
                    classified_json="$(llmenv classify-transcript --client "$client" --transcript "$transcript_file" 2>/dev/null)" || classified_json=""
                    excerpt="$(printf '%s' "$classified_json" | jq -r '.excerpt // empty' 2>/dev/null)"
                    if [ -n "$excerpt" ]; then
                        log_block "Relevant transcript excerpt" "$excerpt"
                    else
                        log_file_excerpt "Client JSONL transcript" "$transcript_file" "$agent_diagnostic_excerpt_bytes"
                    fi
                fi
```

The second, inside the `differences` mismatch branch further down, after
the `if [ -z "$differences" ]; then ... continue; fi` block's early return
(12-space base indent — one level shallower, since it is not nested inside
an extra `if`):

```bash
            if [ -s "$transcript_file" ]; then
                log_file_excerpt "Client JSONL transcript" "$transcript_file" "$agent_diagnostic_excerpt_bytes"
            fi
```

Replace with:

```bash
            if [ -s "$transcript_file" ]; then
                classified_json="$(llmenv classify-transcript --client "$client" --transcript "$transcript_file" 2>/dev/null)" || classified_json=""
                excerpt="$(printf '%s' "$classified_json" | jq -r '.excerpt // empty' 2>/dev/null)"
                if [ -n "$excerpt" ]; then
                    log_block "Relevant transcript excerpt" "$excerpt"
                else
                    log_file_excerpt "Client JSONL transcript" "$transcript_file" "$agent_diagnostic_excerpt_bytes"
                fi
            fi
```

(The `|| classified_json=""` fallback, and falling back to the original
`log_file_excerpt` call when the classifier produces no usable excerpt,
means a classifier failure — e.g. `uv`/`llmenv.py` itself erroring for an
unrelated reason — degrades to today's behavior rather than showing
nothing at all.)

- [ ] **Step 4: Run the existing check-with-agents tests to confirm no regression**

Run: `uv run --with pytest pytest tests/test_shell.py -v -k check_with_agents`
Expected: PASS.

- [ ] **Step 5: Run shellcheck**

Run: `shellcheck -s bash scripts/check-with-agents.sh`
Expected: no new warnings.

- [ ] **Step 6: Commit**

```bash
git add scripts/check-with-agents.sh tests/test_shell.py
git commit -m "feat(check-with-agents): show a classified excerpt instead of the raw transcript on failure"
```

---

### Task 13: `pyproject.toml` — pinned project dependencies

**Files:**
- Create: `pyproject.toml`

**Interfaces:**
- Produces: a real project manifest `uv sync` can act on, with `pyyaml` as
  a main dependency and `pytest`/`ruff` in a `dev` dependency group. This
  is **not** wired into `llmenv.py`'s own execution — `llmenv.py` keeps its
  existing inline PEP 723 `# /// script ... dependencies = ["pyyaml>=6.0"] ... ///`
  header untouched, so it stays a fully self-contained, independently
  portable script (this matters concretely: `scripts/check-with-agents.sh`
  runs it inside an isolated agent workspace via `uv run --offline
  "${REPO_DIR}/llmenv.py" run-agent-bounded ...`, relying on that
  self-containment — folding `llmenv.py` into project mode would risk
  breaking that isolated flow). This `pyproject.toml` exists purely to give
  the *test/lint tooling* (`pytest`, `ruff`) a locked, reproducible
  environment, which is the actual gap the design spec identified
  (`uvx ruff check` and `uv run --with pytest` today resolve completely
  unpinned versions on every invocation).

- [ ] **Step 1: Create `pyproject.toml`**

```toml
[project]
name = "llm-env"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = ["pyyaml>=6.0"]

[dependency-groups]
dev = ["pytest>=8.0", "ruff>=0.8"]
```

- [ ] **Step 2: Sync and verify**

Run: `uv sync`
Expected: creates `.venv/` and `uv.lock` with `pyyaml`, `pytest`, and
`ruff` resolved and installed.

Run: `uv run pytest tests/ -v`
Expected: PASS. If this instead fails with `pytest: command not found` or
similar, the `dev` group is not being installed implicitly by `uv run` on
this `uv` version — in that case, change every subsequent `uv run pytest`/
`uv run ruff` invocation in this plan (Task 16) to
`uv run --group dev pytest`/`uv run --group dev ruff` instead, and note
that explicitly in Task 16's commit message. Confirm which case applies
before proceeding to Task 16.

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "feat: add pyproject.toml for a pinned pytest/ruff dev environment"
```

---

### Task 14: `setup/prerequisites.sh` — bootstrap `uv` via its official installer

**Files:**
- Modify: `setup/prerequisites.sh`
- Test: `tests/test_shell.py`

**Interfaces:**
- Produces: `uv` removed from the rpm-ostree-managed `RUNTIME` array;
  checked and, if missing, installed via
  `curl -LsSf https://astral.sh/uv/install.sh | sh` behind the same
  confirm-before-installing prompt every other prerequisite already uses —
  matching how `uv` is actually installed on the reference machine
  (`~/.local/bin/uv`, not an rpm-ostree layer), and avoiding an unnecessary
  reboot for a tool that does not need one.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_shell.py`:

```python
def test_prerequisites_reports_missing_uv_without_rpm_ostree(tmp_path: pathlib.Path) -> None:
    commands = tmp_path / "bin"
    commands.mkdir()
    _mock_dirname(commands)
    for name in ("jq", "yq", "podman", "curl", "ip", "sudo"):
        _mock_command(commands, name)
    yq = commands / "yq"
    yq.write_text(
        "#!/usr/bin/bash\nprintf '%s\\n' 'yq (https://github.com/mikefarah/yq/) version v4.45.1'\n"
    )
    yq.chmod(yq.stat().st_mode | stat.S_IXUSR)

    environment = os.environ | {"PATH": str(commands)}
    result = subprocess.run(
        ["/usr/bin/bash", "setup/prerequisites.sh", "--check"],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 1
    assert "missing    uv" in result.stdout


def test_prerequisites_installs_uv_via_official_installer_not_rpm_ostree(
    tmp_path: pathlib.Path,
) -> None:
    commands = tmp_path / "bin"
    commands.mkdir()
    _mock_dirname(commands)
    for name in ("jq", "podman", "curl", "ip", "git", "shellcheck"):
        _mock_command(commands, name)
    yq = commands / "yq"
    yq.write_text(
        "#!/usr/bin/bash\nprintf '%s\\n' 'yq (https://github.com/mikefarah/yq/) version v4.45.1'\n"
    )
    yq.chmod(yq.stat().st_mode | stat.S_IXUSR)
    calls = tmp_path / "calls"
    rpm_ostree = commands / "rpm-ostree"
    rpm_ostree.write_text(
        "#!/usr/bin/bash\nprintf '%s\\n' \"$*\" >> \"$CALLS\"\n"
    )
    rpm_ostree.chmod(rpm_ostree.stat().st_mode | stat.S_IXUSR)
    sudo = commands / "sudo"
    sudo.write_text("#!/usr/bin/bash\nexec \"$@\"\n")
    sudo.chmod(sudo.stat().st_mode | stat.S_IXUSR)
    uv_installer_log = tmp_path / "uv-install-invoked"
    curl = commands / "curl"
    curl.write_text(
        "#!/usr/bin/bash\n"
        # Log invocation to $CALLS, not real stdout — real stdout here is
        # what gets piped into `sh` by the script under test, so it must be
        # valid (harmless) shell content, not a log line.
        "printf '%s\\n' \"$*\" >> \"$CALLS\"\n"
        f"touch {uv_installer_log}\n"
        "printf 'true\\n'\n"
        "exit 0\n"
    )
    curl.chmod(curl.stat().st_mode | stat.S_IXUSR)

    environment = os.environ | {
        "CALLS": str(calls),
        # commands must come first so the mocked curl/rpm-ostree/etc. win,
        # but /usr/bin:/bin must also be present — the real `sh` (the
        # pipeline destination of `curl ... | sh`) and `touch` (used inside
        # the curl stub above) are not mocked and must resolve to the real
        # system binaries, the same convention run_lifecycle_script and
        # run_cleanup_with_stubs already use elsewhere in this file.
        "PATH": f"{commands}:/usr/bin:/bin",
    }
    result = subprocess.run(
        ["/usr/bin/bash", "setup/prerequisites.sh"],
        cwd=ROOT,
        env=environment,
        input="yes\nyes\n",
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert uv_installer_log.exists()
    assert "install uv" not in calls.read_text()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --with pytest pytest tests/test_shell.py -v -k "prerequisites_reports_missing_uv or prerequisites_installs_uv_via_official_installer"`
Expected: FAIL — the current script lists `uv` in `RUNTIME` and installs it
via `sudo rpm-ostree install uv` like every other runtime package, so the
official-installer path (and the `curl -LsSf https://astral.sh/uv/install.sh`
invocation) never happens.

- [ ] **Step 3: Update `setup/prerequisites.sh`**

Remove `uv` from `RUNTIME`:

```bash
RUNTIME=("jq:jq" "yq:yq" "podman:podman" "curl:curl" "ip:iproute")
```

Add a dedicated `uv` bootstrap step, run before the `RUNTIME`/
`DEVELOPMENT`/`OPTIONAL_LAN` group checks (`uv` itself has no distinct
"category" among them anymore, since its install mechanism differs from
every other entry):

```bash
uv_missing=0
if ! command -v uv >/dev/null 2>&1; then
    uv_missing=1
    printf '  missing    %-16s %-12s %s\n' uv "(astral.sh)" "Python tool runner and dependency manager"
else
    printf '  installed  %-16s %s\n' uv "Python tool runner and dependency manager"
fi
```

Insert this immediately before the existing:

```bash
printf 'Checking Bazzite/Fedora prerequisites:\n'
check_group runtime "${RUNTIME[@]}"
```

(i.e., print the `uv` line first, then the rest of the runtime group, so
`uv`'s row still appears at the top of the "Checking Bazzite/Fedora
prerequisites" output the way it always has.)

Update the `--check`-mode exit logic — currently:

```bash
if [ "$CHECK_ONLY" -eq 1 ]; then
    if [ "${#missing_runtime[@]}" -eq 0 ]; then
        exit 0
    fi
    exit 1
fi
```

to also account for a missing `uv`:

```bash
if [ "$CHECK_ONLY" -eq 1 ]; then
    if [ "${#missing_runtime[@]}" -eq 0 ] && [ "$uv_missing" -eq 0 ]; then
        exit 0
    fi
    exit 1
fi
```

Add the `uv` install step, before the existing
`if [ "${#missing_packages[@]}" -gt 0 ]; then` block:

```bash
if [ "$uv_missing" -eq 1 ]; then
    printf 'curl -LsSf https://astral.sh/uv/install.sh | sh\n'
    read -rp "Install uv via its official installer? (yes/no) " reply
    if [ "$reply" = "yes" ]; then
        curl -LsSf https://astral.sh/uv/install.sh | sh
        log_info "uv installed; ensure \$HOME/.local/bin is on PATH"
    else
        exit 1
    fi
fi
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --with pytest pytest tests/test_shell.py -v -k "prerequisites_reports_missing_uv or prerequisites_installs_uv_via_official_installer"`
Expected: PASS.

- [ ] **Step 5: Run the full prerequisites test suite and shellcheck**

Run: `uv run --with pytest pytest tests/test_shell.py -v -k prerequisites`
Run: `shellcheck -s bash setup/prerequisites.sh`
Expected: PASS, including the pre-existing `test_prerequisites_reports_missing_yq_v4_without_installing`,
`test_prerequisites_check_reports_missing_non_runtime_rows_on_controlled_path`,
`test_prerequisites_displays_a_distinct_purpose_for_every_command`, and
`test_prerequisites_installs_only_after_yes` — the last of these
specifically already asserts `"install yq" in calls.read_text()` after a
`response="yes"` run; confirm it is unaffected by the new `uv`-specific
prompt (it uses a different fixture, `run_prerequisites_with_stubs`, whose
default stub set already omits `uv` from what it stubs as present/absent in
a way that interacts with this change — read that fixture's current
`names` list, which includes `"uv"`, and confirm the new `uv`-detection
block does not double-count it against the pre-existing
`missing_packages`/`missing_runtime` accounting for the other tools).

- [ ] **Step 6: Commit**

```bash
git add setup/prerequisites.sh tests/test_shell.py
git commit -m "feat(prerequisites): bootstrap uv via its official installer, not rpm-ostree"
```

---

### Task 15: `setup/dev-setup.sh` and `make dev-setup`

**Files:**
- Create: `setup/dev-setup.sh`
- Modify: `Makefile`
- Test: `tests/test_shell.py`

**Interfaces:**
- Consumes: `setup/prerequisites.sh`'s `uv` bootstrap (Task 14),
  `pyproject.toml` (Task 13).
- Produces: `make dev-setup` (chained as `dev-setup: prerequisites`) runs
  `uv sync`, giving contributors a synced `.venv` with `pytest`/`ruff`
  available — separate from `make setup`/`make start`, which never need
  those.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_shell.py`:

```python
def test_dev_setup_runs_uv_sync(tmp_path: pathlib.Path) -> None:
    commands = tmp_path / "bin"
    commands.mkdir()
    _mock_dirname(commands)
    calls = tmp_path / "calls"
    uv = commands / "uv"
    uv.write_text("#!/usr/bin/bash\nprintf 'uv %s\\n' \"$*\" >> \"$CALLS\"\n")
    uv.chmod(uv.stat().st_mode | stat.S_IXUSR)

    environment = os.environ | {"CALLS": str(calls), "PATH": f"{commands}:/usr/bin:/bin"}
    result = subprocess.run(
        ["/usr/bin/bash", "setup/dev-setup.sh"],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "uv sync" in calls.read_text()


def test_makefile_dev_setup_chains_after_prerequisites() -> None:
    makefile = (ROOT / "Makefile").read_text()
    assert "dev-setup: prerequisites\n\t@bash tools/run-target.sh dev-setup -- bash setup/dev-setup.sh" in makefile
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --with pytest pytest tests/test_shell.py -v -k "dev_setup"`
Expected: FAIL — `setup/dev-setup.sh` does not exist; the Makefile has no
`dev-setup` target.

- [ ] **Step 3: Create `setup/dev-setup.sh`**

```bash
#!/usr/bin/env bash
# dev-setup.sh — sync the Python dev environment (pytest, ruff).
set -euo pipefail
# shellcheck disable=SC1091 # Resolved from this script at runtime.
# shellcheck source=../tools/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/../tools/lib.sh"

require_cmd uv

log_step "Syncing the Python dev environment"
uv sync
log_info "dev environment ready (.venv, pytest, ruff)"
```

Make it executable: `chmod +x setup/dev-setup.sh`.

- [ ] **Step 4: Add the `dev-setup` target to the `Makefile`**

Add, after the `.PHONY` line's target list (extend it to include
`dev-setup`) and after the `prerequisites` target's recipe:

```make
dev-setup: prerequisites
	@bash tools/run-target.sh dev-setup -- bash setup/dev-setup.sh
```

Update the `.PHONY` line to include `dev-setup`:

```make
.PHONY: help prerequisites dev-setup setup setup-local-llm-agents start stop restart check-setup check-server check-with-agents benchmark \
        key-reset enable-boot disable-boot status logs validate test clean
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run --with pytest pytest tests/test_shell.py -v -k "dev_setup"`
Expected: PASS.

- [ ] **Step 6: Run shellcheck**

Run: `shellcheck -s bash setup/dev-setup.sh`
Expected: no new warnings.

- [ ] **Step 7: Commit**

```bash
git add setup/dev-setup.sh Makefile tests/test_shell.py
git commit -m "feat(dev-setup): add make dev-setup for a synced pytest/ruff environment"
```

---

### Task 16: Simplify `test`/`validate` to use the synced venv

**Files:**
- Modify: `tools/test.sh`
- Modify: `tools/validate.sh`

**Interfaces:**
- Consumes: `pyproject.toml` (Task 13).
- Produces: `make test`/`make validate` run `uv run pytest`/`uv run ruff
  check` against the synced, version-locked `.venv` instead of the ad hoc
  `uv run --with pytest`/`uvx ruff check` invocations Task 3 carried over
  unchanged from the original Makefile.

- [ ] **Step 1: Confirm Task 13's Step 2 outcome**

This task's exact commands depend on whether Task 13 found that `uv run
pytest`/`uv run ruff` pick up the `dev` dependency group automatically.
Run: `uv run pytest tests/ -v` and `uv run ruff check llmenv.py pylib tests`
Expected: both succeed without a `command not found` error. If either
fails that way, use `uv run --group dev pytest`/`uv run --group dev ruff
check` in Step 2 below instead of the plain `uv run` forms.

- [ ] **Step 2: Update `tools/test.sh`**

Replace:

```bash
uv run --with pytest pytest tests/ -v
```

with:

```bash
uv run pytest tests/ -v
```

(or `uv run --group dev pytest tests/ -v`, per Step 1's finding).

- [ ] **Step 3: Update `tools/validate.sh`**

Replace:

```bash
uvx ruff check llmenv.py pylib tests
```

with:

```bash
uv run ruff check llmenv.py pylib tests
```

(or `uv run --group dev ruff check llmenv.py pylib tests`, per Step 1's
finding).

- [ ] **Step 4: Run both and confirm they pass**

Run: `bash tools/test.sh`
Run: `bash tools/validate.sh`
Expected: PASS, identical output to before this task besides the resolved
tool versions now coming from `uv.lock` instead of an unpinned resolution.

- [ ] **Step 5: Commit**

```bash
git add tools/test.sh tools/validate.sh
git commit -m "refactor: run test/validate against the synced, locked dev venv"
```

---

## End-to-End Verification

After Task 16, confirm the full toolchain still works end to end:

```bash
make dev-setup
make validate
make test
make help
make prerequisites
```

And confirm a chained invocation reads clearly with the new banners:

```bash
make check-setup check-server
```
