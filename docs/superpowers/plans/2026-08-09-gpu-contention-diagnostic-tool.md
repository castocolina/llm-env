# GPU Contention Diagnostic Tool Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `make gpu-status`, a one-shot, read-only-by-default diagnostic that shows what else is using the configured dGPU right now, and offers a single, explicitly-confirmed way to push the worst offenders (and, separately, new apps generally) onto the iGPU.

**Architecture:** A new `pylib/detect.py` function walks `/proc/<pid>/fd` + `/proc/<pid>/fdinfo` to find every process with an open handle on the dGPU's render node and its VRAM use (the kernel DRM fdinfo standard — the same mechanism `nvtop`/`amdgpu_top` use), exposed as a new `llmenv processes-on-render-node` CLI subcommand. A new `scripts/gpu-status.sh` (following the existing `scripts/*.sh` + `tools/lib.sh` pattern used by `status.sh`/`clean.sh`) calls it plus the existing `llmenv detect`/`budget` plumbing, prints a diagnostic table, and — only on explicit confirmation — writes XDG per-user `.desktop` overrides (`~/.local/share/applications`) and/or a `~/.config/environment.d/` default, both reversible, neither touching a system file.

**Tech Stack:** Python 3.11 (`pylib/detect.py`, `llmenv.py`, existing `pathlib`-based procfs/sysfs reading style), Bash (`scripts/gpu-status.sh`, existing `tools/lib.sh` helpers), `jq`/`yq` for JSON/YAML plumbing (existing convention throughout `setup/setup.sh`).

## Global Constraints

- Not a continuous daemon — `make gpu-status` is a one-shot run, invoked on demand only.
- Never integrated into `make start`'s health gate — informational only, never blocks or auto-runs.
- Doesn't touch already-running processes' actual GPU context — remediation only affects *future* launches of the same app, via its `.desktop` launcher.
- The per-process migration prompt is a **single** combined confirmation covering all (up to 3) flagged processes — never one prompt per process.
- The default-iGPU-for-new-apps prompt is a **second, independent** confirmation, offered only when the diagnostic actually found other processes on the dGPU (never offered when there was nothing to report — see Task 2's zero-process exit).
- `.desktop` overrides are written **only** to `~/.local/share/applications` (the standard XDG per-user override directory) — the system copy under `/usr/share/applications` (or wherever `LLM_ENV_SYSTEM_APPLICATIONS_DIR` points, for test injection) is read-only input, never modified. Overrides are reversible by deleting the override file.
- `~/.config/environment.d/60-llm-env-igpu-default.conf` is idempotent — each run overwrites the same file, never accumulates duplicates.
- One PID/process failing its `.desktop` lookup or override write never aborts the whole run — it's reported as "no launcher found, skipped" (lookup failure) or "override failed, skipped" (write failure) and the loop continues.
- `gpu.vram_budget_ceiling_mib: 0` is the existing "uncapped" sentinel (see `docs/superpowers/specs/2026-08-09-resource-limits-and-single-model-setup-design.md`) — `gpu-status.sh` must display it as "uncapped", never literally "0 MiB".
- **`LLM_ENV_ASSUME_YES=1` means "auto-decline" for `gpu-status.sh`'s two migration prompts — a deliberate, documented exception to the flag's meaning everywhere else in this codebase.** Elsewhere (`setup/setup.sh`'s `ask()`, `scripts/clean.sh`'s confirmation), `LLM_ENV_ASSUME_YES=1` means "auto-accept the default, skip the interactive read." `gpu-status.sh` inverts this specifically because these are the only two prompts in the whole codebase that mutate the host *outside* the repo's own config — writing `.desktop` launcher overrides to `~/.local/share/applications` and a file under `~/.config/environment.d/` — so an unattended/CI run must never silently rewrite a real user's desktop launchers just because it opted into non-interactive mode for everything else.
- The tool assumes exactly one dGPU + one iGPU: Task 3's iGPU-selection heuristic (`sort_by(.vram_total_mib) | first` among GPUs other than the configured one) picks whichever non-configured card has the least total VRAM, so on a 3+ GPU host (e.g. two dGPUs plus an iGPU) it may not select the true iGPU — this is an accepted scope limit, not a bug to fix in this plan.
- Full test suite, `ruff check .`, and `shellcheck` must stay clean after every task.

---

### Task 1: `processes_on_render_node()` primitive + `llmenv processes-on-render-node` CLI subcommand

**Files:**
- Modify: `pylib/detect.py`
- Modify: `llmenv.py:48` (import), `llmenv.py` (new `cmd_processes_on_render_node` + parser registration near `cmd_detect`/`sub.add_parser("detect")` at line ~349)
- Test: `tests/test_detect.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Produces: `pylib.detect.processes_on_render_node(render_node: str, proc_root: Path = Path("/proc")) -> list[dict]`, each dict shaped `{"pid": int, "comm": str, "exe": str, "vram_mib": int}`. `exe` is the basename of the resolved `/proc/<pid>/exe` symlink (falls back to `comm` if the symlink can't be read).
- Produces: `llmenv processes-on-render-node --render-node <node>` → `{"processes": [...]}` on stdout, exit 0. (Consumed by Task 2's `scripts/gpu-status.sh`.)

- [ ] **Step 1: Write the failing tests for the fdinfo/proc-walking primitive**

Add to `tests/test_detect.py` (after the existing `build_proc`/`test_compositor_render_node_*` tests):

```python
def build_proc_with_fd(
    root: Path,
    pid: str,
    comm: str,
    render_node: str,
    *,
    exe_target: str = "/usr/bin/firefox",
    fd_name: str = "7",
    vram_kib: int | None = 524288,
    client_id: str | None = "1",
) -> Path:
    """A /proc/<pid> with one fd pointed at render_node, mirroring the real
    DRM fdinfo layout. vram_kib=None omits the fdinfo file entirely (the
    "kernel didn't expose accounting for this fd" case). client_id=None
    omits the drm-client-id line entirely (the "kernel/driver doesn't
    expose a dedup id for this fd" case, e.g. an older kernel) -- callers
    testing dedup behavior must pass an explicit, non-None client_id."""
    proc = root / "proc"
    pid_dir = proc / pid
    (pid_dir / "fd").mkdir(parents=True, exist_ok=True)
    (pid_dir / "fdinfo").mkdir(parents=True, exist_ok=True)
    (pid_dir / "comm").write_text(comm + "\n")
    target = root / "dev" / "dri" / render_node
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.write_text("")
    (pid_dir / "fd" / fd_name).symlink_to(target)
    if vram_kib is not None:
        client_line = f"drm-client-id:\t{client_id}\n" if client_id is not None else ""
        (pid_dir / "fdinfo" / fd_name).write_text(
            "pos:\t0\nflags:\t02100002\nmnt_id:\t24\n"
            f"drm-driver:\tamdgpu\ndrm-pdev:\t0000:03:00.0\n{client_line}"
            f"drm-memory-vram:\t{vram_kib} KiB\ndrm-memory-gtt:\t0 KiB\n"
        )
    (pid_dir / "exe").symlink_to(exe_target)
    return proc


def test_processes_on_render_node_finds_a_matching_pid(tmp_path):
    proc = build_proc_with_fd(tmp_path, "3021", "firefox", "renderD128", vram_kib=524288)
    result = processes_on_render_node("renderD128", proc)
    assert result == [{"pid": 3021, "comm": "firefox", "exe": "firefox", "vram_mib": 512}]


def test_processes_on_render_node_ignores_other_render_nodes(tmp_path):
    proc = build_proc_with_fd(tmp_path, "3021", "firefox", "renderD129")
    assert processes_on_render_node("renderD128", proc) == []


def test_processes_on_render_node_sums_multiple_fds_for_one_pid(tmp_path):
    """Two fds for one PID with genuinely *distinct* drm-client-id values
    (unlike a dup()'d fd, which shares one) must still sum in full -- this
    is the "not a dedup false positive" counterpart to the dedup test
    below."""
    proc = build_proc_with_fd(
        tmp_path, "3021", "firefox", "renderD128", fd_name="7", vram_kib=524288, client_id="1"
    )
    (proc / "3021" / "fdinfo" / "8").write_text(
        "drm-driver:\tamdgpu\ndrm-client-id:\t2\ndrm-memory-vram:\t262144 KiB\n"
    )
    (proc / "3021" / "fd" / "8").symlink_to(proc.parent / "dev" / "dri" / "renderD128")
    result = processes_on_render_node("renderD128", proc)
    assert result == [{"pid": 3021, "comm": "firefox", "exe": "firefox", "vram_mib": 768}]


def test_processes_on_render_node_dedupes_same_drm_client_id_across_fds(tmp_path):
    """Two fds for one PID sharing the same drm-client-id (e.g. via dup(),
    or certain driver/threading patterns) refer to the same logical DRM
    client. The kernel's fdinfo docs
    (https://origin.kernel.org/doc/html/latest/gpu/drm-usage-stats.html)
    define drm-client-id specifically so consumers can recognize this and
    count each client once, not once per fd -- summing both fds' VRAM here
    would double the real total."""
    proc = build_proc_with_fd(
        tmp_path, "3021", "firefox", "renderD128", fd_name="7", vram_kib=524288, client_id="9"
    )
    (proc / "3021" / "fdinfo" / "8").write_text(
        "drm-driver:\tamdgpu\ndrm-client-id:\t9\ndrm-memory-vram:\t524288 KiB\n"
    )
    (proc / "3021" / "fd" / "8").symlink_to(proc.parent / "dev" / "dri" / "renderD128")
    result = processes_on_render_node("renderD128", proc)
    assert result == [{"pid": 3021, "comm": "firefox", "exe": "firefox", "vram_mib": 512}]


def test_processes_on_render_node_sums_sub_mib_fds_without_losing_precision(tmp_path):
    """Two fds each holding 512 KiB (0 MiB if floored individually) must sum
    to 1 MiB — the per-fd amount must accumulate at sub-MiB precision, not
    be floored to whole MiB before being summed."""
    proc = build_proc_with_fd(tmp_path, "3021", "firefox", "renderD128", fd_name="7", vram_kib=512)
    (proc / "3021" / "fdinfo" / "8").write_text(
        "drm-driver:\tamdgpu\ndrm-memory-vram:\t512 KiB\n"
    )
    (proc / "3021" / "fd" / "8").symlink_to(proc.parent / "dev" / "dri" / "renderD128")
    result = processes_on_render_node("renderD128", proc)
    assert result == [{"pid": 3021, "comm": "firefox", "exe": "firefox", "vram_mib": 1}]


def test_processes_on_render_node_treats_single_token_vram_as_raw_bytes(tmp_path):
    """The DRM fdinfo spec's documented default when drm-memory-vram has no
    unit suffix at all (just a bare number) is raw bytes -- a valid form
    distinct from the two-token `<amount> <unit>` form covered by the other
    tests in this file. 1048576 bytes must parse as 1 MiB, not 0."""
    proc = build_proc_with_fd(tmp_path, "3021", "firefox", "renderD128", vram_kib=524288)
    (proc / "3021" / "fdinfo" / "7").write_text(
        "drm-driver:\tamdgpu\ndrm-memory-vram:\t1048576\n"
    )
    result = processes_on_render_node("renderD128", proc)
    assert result == [{"pid": 3021, "comm": "firefox", "exe": "firefox", "vram_mib": 1}]


def test_processes_on_render_node_treats_missing_fdinfo_as_zero(tmp_path):
    proc = build_proc_with_fd(tmp_path, "3021", "firefox", "renderD128", vram_kib=None)
    result = processes_on_render_node("renderD128", proc)
    assert result == [{"pid": 3021, "comm": "firefox", "exe": "firefox", "vram_mib": 0}]


def test_processes_on_render_node_treats_malformed_fdinfo_as_zero(tmp_path):
    proc = build_proc_with_fd(tmp_path, "3021", "firefox", "renderD128", vram_kib=524288)
    (proc / "3021" / "fdinfo" / "7").write_text("drm-driver:\tamdgpu\nno-vram-line-here:\t1\n")
    result = processes_on_render_node("renderD128", proc)
    assert result == [{"pid": 3021, "comm": "firefox", "exe": "firefox", "vram_mib": 0}]


def test_processes_on_render_node_falls_back_to_comm_when_exe_unreadable(tmp_path):
    proc = build_proc_with_fd(tmp_path, "3021", "firefox", "renderD128")
    (proc / "3021" / "exe").unlink()
    result = processes_on_render_node("renderD128", proc)
    assert result == [{"pid": 3021, "comm": "firefox", "exe": "firefox", "vram_mib": 512}]


def test_processes_on_render_node_skips_pid_with_unreadable_comm(tmp_path):
    proc = build_proc_with_fd(tmp_path, "3021", "firefox", "renderD128")
    (proc / "3021" / "comm").unlink()
    assert processes_on_render_node("renderD128", proc) == []


def test_processes_on_render_node_empty_proc_root_returns_empty_list(tmp_path):
    assert processes_on_render_node("renderD128", tmp_path / "proc") == []
```

Add `processes_on_render_node` to the existing `from pylib.detect import (...)` block at the top of `tests/test_detect.py`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_detect.py -k processes_on_render_node -v`
Expected: FAIL with `ImportError: cannot import name 'processes_on_render_node'`

- [ ] **Step 3: Implement `processes_on_render_node()` in `pylib/detect.py`**

Add after the existing `compositor_render_node()` function:

```python
def _parse_fdinfo(text: str) -> tuple[int, str | None]:
    """Return (vram_bytes, drm_client_id) parsed from fdinfo content.

    vram_bytes (not MiB) so multiple fds for the same PID can be summed at
    full precision before any MiB rounding happens -- summing
    already-floored per-fd MiB values would lose small fds entirely (e.g.
    two 512 KiB fds would each floor to 0 MiB and sum to 0, when the
    correct combined total is 1024 KiB = 1 MiB). 0 for missing/unparseable
    content -- best-effort, matching the kernel's own "a process can hold
    the fd without VRAM accounting being exposed for it" behavior.

    `drm-memory-vram:` accepts two forms per the kernel's fdinfo spec: the
    usual `<amount> <unit>` pair (KiB/MiB/GiB/B, case-insensitive), and a
    single bare number with no unit suffix at all -- the spec's documented
    default of raw bytes when no unit is given. Both must parse correctly;
    only a line that is neither of these (missing entirely, or with a
    non-numeric/unrecognized token) falls through to the 0 default.

    drm_client_id is the raw `drm-client-id:` value (a string; the kernel
    defines no particular format for it, so it's never parsed as an int),
    or None when the fdinfo record has no such line (older kernels/drivers
    that don't expose it). The kernel's DRM fdinfo interface
    (https://origin.kernel.org/doc/html/latest/gpu/drm-usage-stats.html)
    defines drm-client-id specifically to identify duplicated/shared fds
    (e.g. via dup()) that refer to the same logical client, and documents
    that consumers should count each client once, not once per fd --
    callers use this to de-duplicate VRAM across fds for the same PID.
    """
    vram_bytes = 0
    client_id: str | None = None
    for line in text.splitlines():
        if line.startswith("drm-memory-vram:"):
            parts = line.split(":", 1)[1].split()
            if len(parts) == 2:
                amount_text, unit = parts
                try:
                    amount = int(amount_text)
                except ValueError:
                    amount = None
                if amount is not None:
                    unit = unit.lower()
                    if unit == "kib":
                        vram_bytes = amount * 1024
                    elif unit == "mib":
                        vram_bytes = amount * MIB
                    elif unit == "gib":
                        vram_bytes = amount * 1024 * MIB
                    elif unit == "b":
                        vram_bytes = amount
            elif len(parts) == 1:
                # No unit suffix at all -- the fdinfo spec's documented
                # default is raw bytes (e.g. "drm-memory-vram:\t1048576").
                try:
                    vram_bytes = int(parts[0])
                except ValueError:
                    pass
        elif line.startswith("drm-client-id:"):
            value = line.split(":", 1)[1].strip()
            if value:
                client_id = value
    return vram_bytes, client_id


def processes_on_render_node(render_node: str, proc_root: Path = Path("/proc")) -> list[dict]:
    """Return every process with an open fd on render_node, with its VRAM use.

    Fds sharing the same drm-client-id (duplicated fds referring to one
    logical DRM client, e.g. via dup()) are counted once, not once per fd.
    An fd whose fdinfo record has no drm-client-id line at all (older
    kernel/driver) is always counted on its own, preserving today's
    behavior for kernels that don't expose the id.
    """
    proc_root = Path(proc_root)
    if not proc_root.is_dir():
        return []

    results: list[dict] = []
    for pid_dir in proc_root.iterdir():
        if not pid_dir.name.isdigit():
            continue
        try:
            comm = (pid_dir / "comm").read_text().strip()
        except OSError:
            continue

        try:
            fds = list((pid_dir / "fd").iterdir())
        except OSError:
            continue

        matched = False
        vram_bytes = 0
        seen_client_ids: set[str] = set()
        for fd in fds:
            try:
                target = fd.resolve().name
            except OSError:
                continue
            if target != render_node:
                continue
            matched = True
            try:
                fdinfo_text = (pid_dir / "fdinfo" / fd.name).read_text()
            except OSError:
                continue
            fd_vram_bytes, client_id = _parse_fdinfo(fdinfo_text)
            if client_id is not None:
                if client_id in seen_client_ids:
                    continue
                seen_client_ids.add(client_id)
            vram_bytes += fd_vram_bytes

        if not matched:
            continue

        try:
            exe = (pid_dir / "exe").readlink().name
        except OSError:
            exe = comm

        results.append(
            {"pid": int(pid_dir.name), "comm": comm, "exe": exe, "vram_mib": vram_bytes // MIB}
        )

    return results
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_detect.py -k processes_on_render_node -v`
Expected: PASS (11 tests)

- [ ] **Step 5: Write the failing CLI test**

Add to `tests/test_cli.py`:

```python
def test_cmd_processes_on_render_node_returns_a_processes_list() -> None:
    result = run("processes-on-render-node", "--render-node", "renderD128")
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert isinstance(payload["processes"], list)


def test_cmd_processes_on_render_node_requires_render_node() -> None:
    result = run("processes-on-render-node")
    assert result.returncode == 2
```

- [ ] **Step 6: Run the CLI tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -k processes_on_render_node -v`
Expected: FAIL with `argument --render-node: error: unrecognized arguments` / `error: argument command: invalid choice: 'processes-on-render-node'`

- [ ] **Step 7: Wire the CLI subcommand**

In `llmenv.py`, add `processes_on_render_node` to the existing detect import at line 48. The current line is:

```python
from pylib.detect import DetectError, detect, host_resources
```

Replace it with:

```python
from pylib.detect import DetectError, detect, host_resources, processes_on_render_node
```

(Only add the new name — `DetectError` and `host_resources` must stay, since `cmd_resources()` calls `host_resources()` and the top-level exception handling catches `DetectError`.)

Add near `cmd_detect` (after its definition, around line 93):

```python
def cmd_processes_on_render_node(args: argparse.Namespace) -> int:
    return emit({"processes": processes_on_render_node(args.render_node)})
```

Register the subparser near `sub.add_parser("detect")` (around line 349):

```python
processes_parser = sub.add_parser("processes-on-render-node")
processes_parser.add_argument("--render-node", required=True)
processes_parser.set_defaults(func=cmd_processes_on_render_node)
```

- [ ] **Step 8: Run the CLI tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -k processes_on_render_node -v`
Expected: PASS (2 tests)

- [ ] **Step 9: Full suite + lint**

Run: `uv run pytest tests/ -q && uv run ruff check .`
Expected: all passing, ruff clean

- [ ] **Step 10: Commit**

```bash
git add pylib/detect.py llmenv.py tests/test_detect.py tests/test_cli.py
git commit -m "feat(detect): add processes_on_render_node() and its CLI subcommand"
```

---

### Task 2: `scripts/gpu-status.sh` diagnostic display (read-only)

**Files:**
- Create: `scripts/gpu-status.sh`
- Modify: `Makefile:1-2` (`.PHONY` list), `Makefile` (new `gpu-status` target, alongside `status:`)
- Modify: `scripts/help.sh` (new help line)
- Modify: `tests/test_shell.py` (extend `test_shell_scripts_use_the_approved_directories` and `test_makefile_dispatches_relocated_entrypoints`; new `run_gpu_status_with_stubs()` helper + tests)

**Interfaces:**
- Consumes: `llmenv detect` (existing), `llmenv --config "$CONFIG_PATH" budget --models-dir "$MODELS_DIR"` (existing, `available_mib` field), `llmenv processes-on-render-node --render-node <node>` (Task 1, `{"processes": [...]}`).
- Consumes: `tools/lib.sh`'s `CONFIG_PATH`, `MODELS_DIR`, `log_step`, `log_info`, `log_warn`, `die`, `require_cmd`, `migrate_config_file` (existing, `tools/lib.sh:229` — called before any raw `yq` read of `$CONFIG_PATH`, matching every other user-facing entrypoint in this repo).
- Produces (for Task 3/4 to extend): the script defines `$pci`, `$facts`, `$render_node`, `$top3` (a jq array, up to 3 entries `{pid, comm, exe, vram_mib}`), `$count` (`$top3`'s length) as shell variables still in scope after this task's final line — Task 3 appends code that reads them.

- [ ] **Step 1: Write the failing shell-script location/dispatch tests**

In `tests/test_shell.py`, extend the existing tests:

```python
def test_shell_scripts_use_the_approved_directories() -> None:
    assert not list(ROOT.glob("*.sh"))
    assert (TOOLS_DIR / "lib.sh").is_file()
    assert (SETUP_DIR / "setup.sh").is_file()
    assert (SETUP_DIR / "setup-local-llm-agents.sh").is_file()
    assert (SCRIPT_DIR / "check-server.sh").is_file()
    assert (SCRIPT_DIR / "gpu-status.sh").is_file()


def test_makefile_dispatches_relocated_entrypoints() -> None:
    makefile = (ROOT / "Makefile").read_text()

    assert "bash scripts/help.sh" in makefile
    assert "bash setup/setup.sh" in makefile
    assert "bash setup/setup-local-llm-agents.sh" in makefile
    assert "bash scripts/check-server.sh" in makefile
    assert "bash scripts/gpu-status.sh" in makefile
```

(Replace the two existing function bodies with these — same function names, one new assertion line each.)

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_shell.py -k "approved_directories or dispatches_relocated" -v`
Expected: FAIL — `scripts/gpu-status.sh` does not exist yet, and the Makefile doesn't reference it.

- [ ] **Step 3: Write the failing diagnostic-display tests**

This plan follows TDD: the functional tests for `scripts/gpu-status.sh` are written now, against a script that doesn't exist yet, *before* Step 5 writes the implementation.

Add to `tests/test_shell.py` (after the `run_setup_with_numbered_selection`/setup tests, before the benchmark tests):

```python
def run_gpu_status_with_stubs(
    tmp_path: pathlib.Path,
    *,
    processes_json: str = '{"processes":[]}',
    config_text: str | None = None,
    input_text: str = "",
    system_applications_dir: pathlib.Path | None = None,
    extra_env: dict[str, str] | None = None,
) -> tuple[subprocess.CompletedProcess[str], pathlib.Path, pathlib.Path]:
    """Run gpu-status.sh against a stubbed llmenv/detect/budget/processes pipeline."""
    commands = tmp_path / "bin"
    commands.mkdir()

    uv = commands / "uv"
    uv.write_text(
        "#!/usr/bin/bash\n"
        "case \"$*\" in\n"
        "  *' detect')\n"
        "    printf '%s\\n' '{\"gpus\":["
        "{\"card\":\"card1\",\"pci_address\":\"0000:03:00.0\",\"vram_total_mib\":16384,"
        "\"vram_used_mib\":2048,\"render_node\":\"renderD128\",\"connected_outputs\":[]},"
        "{\"card\":\"card0\",\"pci_address\":\"0000:0e:00.0\",\"vram_total_mib\":512,"
        "\"vram_used_mib\":51,\"render_node\":\"renderD129\",\"connected_outputs\":[]}]}' ;;\n"
        "  *' budget '*) printf '%s\\n' '{\"available_mib\":9000,\"required_mib\":6000,\"feasible\":true}' ;;\n"
        f"  *' processes-on-render-node '*) printf '%s\\n' '{processes_json}' ;;\n"
        "esac\n"
    )
    uv.chmod(uv.stat().st_mode | stat.S_IXUSR)

    real_yq = shutil.which("yq")
    assert real_yq is not None
    yq = commands / "yq"
    yq.write_text(f"#!/usr/bin/bash\nexec {real_yq} \"$@\"\n")
    yq.chmod(yq.stat().st_mode | stat.S_IXUSR)

    home = tmp_path / "home"
    config = tmp_path / "models.yml"
    config.write_text(
        config_text
        or 'gpu:\n  pci_address: "0000:03:00.0"\n  vram_budget_ceiling_mib: 15565\n'
    )

    environment = os.environ | {
        "HOME": str(home),
        "LLM_ENV_CONFIG": str(config),
        "LLM_ENV_MODELS_DIR": str(tmp_path / "models"),
        "PATH": f"{commands}:/usr/bin:/bin",
    }
    if system_applications_dir is not None:
        environment["LLM_ENV_SYSTEM_APPLICATIONS_DIR"] = str(system_applications_dir)
    if extra_env:
        environment |= extra_env

    result = subprocess.run(
        ["/usr/bin/bash", "scripts/gpu-status.sh"],
        cwd=ROOT,
        env=environment,
        input=input_text,
        text=True,
        capture_output=True,
        check=False,
    )
    return result, config, home


def test_gpu_status_reports_total_used_ceiling_and_headroom(tmp_path: pathlib.Path) -> None:
    result, _, _ = run_gpu_status_with_stubs(tmp_path)
    assert result.returncode == 0
    assert "total VRAM:         16384 MiB" in result.stdout
    assert "used (system-wide): 2048 MiB" in result.stdout
    assert "llm-env ceiling:    15565 MiB" in result.stdout
    assert "budget headroom:    9000 MiB" in result.stdout


def test_gpu_status_reports_uncapped_ceiling_as_uncapped_not_zero(tmp_path: pathlib.Path) -> None:
    result, _, _ = run_gpu_status_with_stubs(
        tmp_path,
        config_text='gpu:\n  pci_address: "0000:03:00.0"\n  vram_budget_ceiling_mib: 0\n',
    )
    assert "llm-env ceiling:    uncapped" in result.stdout


def test_gpu_status_migrates_the_config_before_reading_the_ceiling(tmp_path: pathlib.Path) -> None:
    """A config that predates gpu.vram_budget_ceiling_mib (a real,
    anticipated upgrade path -- pylib/config.py's migrate_config()
    persists gpu.setdefault("vram_budget_ceiling_mib", ...) to disk) must
    not make the script read a raw, unmigrated field via `yq`'s `// 0`
    default and print "uncapped" while the very next line (budget
    headroom, via `llmenv budget`, which migrates in-memory by default
    through pylib/config.py's load_config()) reflects the real, non-zero
    ceiling -- two adjacent lines of the same diagnostic must never
    disagree about whether a cap exists. This uv stub's migrate-config
    case actually rewrites $LLM_ENV_CONFIG on disk with `yq -i`, mirroring
    cmd_migrate_config's real persistence behavior, so the test proves
    gpu-status.sh calls migrate_config_file (which shells out to
    `llmenv migrate-config`) before reading the ceiling with `yq` -- not
    merely that it tolerates the call being made."""
    commands = tmp_path / "bin"
    commands.mkdir()
    uv = commands / "uv"
    uv.write_text(
        "#!/usr/bin/bash\n"
        "case \"$*\" in\n"
        "  *' migrate-config')\n"
        "    yq -i '.gpu.vram_budget_ceiling_mib = 15565' \"$LLM_ENV_CONFIG\"\n"
        "    printf '{\"written\":true,\"path\":\"%s\"}\\n' \"$LLM_ENV_CONFIG\" ;;\n"
        "  *' detect')\n"
        "    printf '%s\\n' '{\"gpus\":["
        "{\"card\":\"card1\",\"pci_address\":\"0000:03:00.0\",\"vram_total_mib\":16384,"
        "\"vram_used_mib\":2048,\"render_node\":\"renderD128\",\"connected_outputs\":[]},"
        "{\"card\":\"card0\",\"pci_address\":\"0000:0e:00.0\",\"vram_total_mib\":512,"
        "\"vram_used_mib\":51,\"render_node\":\"renderD129\",\"connected_outputs\":[]}]}' ;;\n"
        "  *' budget '*) printf '%s\\n' '{\"available_mib\":9000,\"required_mib\":6000,\"feasible\":true}' ;;\n"
        "  *' processes-on-render-node '*) printf '%s\\n' '{\"processes\":[]}' ;;\n"
        "esac\n"
    )
    uv.chmod(uv.stat().st_mode | stat.S_IXUSR)
    real_yq = shutil.which("yq")
    assert real_yq is not None
    yq = commands / "yq"
    yq.write_text(f"#!/usr/bin/bash\nexec {real_yq} \"$@\"\n")
    yq.chmod(yq.stat().st_mode | stat.S_IXUSR)

    config = tmp_path / "models.yml"
    config.write_text('gpu:\n  pci_address: "0000:03:00.0"\n')  # no vram_budget_ceiling_mib at all

    environment = os.environ | {
        "HOME": str(tmp_path / "home"),
        "LLM_ENV_CONFIG": str(config),
        "LLM_ENV_MODELS_DIR": str(tmp_path / "models"),
        "PATH": f"{commands}:/usr/bin:/bin",
    }
    result = subprocess.run(
        ["/usr/bin/bash", "scripts/gpu-status.sh"],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0
    assert "llm-env ceiling:    15565 MiB" in result.stdout
    assert "budget headroom:    9000 MiB" in result.stdout


def test_gpu_status_prints_only_the_top_three_processes_by_vram(tmp_path: pathlib.Path) -> None:
    processes = json.dumps(
        {
            "processes": [
                {"pid": 1, "comm": "a", "exe": "a", "vram_mib": 100},
                {"pid": 2, "comm": "b", "exe": "b", "vram_mib": 400},
                {"pid": 3, "comm": "c", "exe": "c", "vram_mib": 200},
                {"pid": 4, "comm": "d", "exe": "d", "vram_mib": 300},
            ]
        }
    )
    result, _, _ = run_gpu_status_with_stubs(tmp_path, processes_json=processes)
    assert "400 MiB" in result.stdout
    assert "300 MiB" in result.stdout
    assert "200 MiB" in result.stdout
    assert "100 MiB" not in result.stdout
    b_index = result.stdout.index("400 MiB")
    d_index = result.stdout.index("300 MiB")
    c_index = result.stdout.index("200 MiB")
    assert b_index < d_index < c_index


def test_gpu_status_excludes_the_llm_env_stack_from_the_table(tmp_path: pathlib.Path) -> None:
    """Excludes by `comm` name, which is inherently approximate, by-name
    best-effort matching -- a legitimately-named user process called e.g.
    `podman` would be wrongly excluded too, a documented, accepted
    limitation, not fixed here. This is deliberately *not* a self-exclusion
    mechanism: a running bash script's own `comm` (as read from
    /proc/<pid>/comm by Task 1's processes_on_render_node()) is always
    `bash`, never the script's filename, so no name-based entry could ever
    match gpu-status.sh's own process -- and in practice this essentially
    never matters, since a plain bash script doing procfs reads does not
    hold an open fd on a DRM render node."""
    processes = json.dumps(
        {
            "processes": [
                {"pid": 1, "comm": "llama-server", "exe": "llama-server", "vram_mib": 9000},
                {"pid": 2, "comm": "conmon", "exe": "conmon", "vram_mib": 10},
                {"pid": 3, "comm": "podman", "exe": "podman", "vram_mib": 5},
                {"pid": 4, "comm": "firefox", "exe": "firefox", "vram_mib": 500},
            ]
        }
    )
    result, _, _ = run_gpu_status_with_stubs(tmp_path, processes_json=processes)
    assert "firefox" in result.stdout
    assert "llama-server" not in result.stdout
    assert "conmon" not in result.stdout
    assert "podman" not in result.stdout


def test_gpu_status_exits_cleanly_with_no_other_processes(tmp_path: pathlib.Path) -> None:
    result, _, _ = run_gpu_status_with_stubs(tmp_path, processes_json='{"processes":[]}')
    assert result.returncode == 0
    assert "no other processes using the dGPU" in result.stdout


def test_gpu_status_requires_an_existing_config(tmp_path: pathlib.Path) -> None:
    result, config, _ = run_gpu_status_with_stubs(tmp_path)
    config.unlink()
    commands = tmp_path / "bin"
    environment = os.environ | {
        "HOME": str(tmp_path / "home"),
        "LLM_ENV_CONFIG": str(config),
        "LLM_ENV_MODELS_DIR": str(tmp_path / "models"),
        "PATH": f"{commands}:/usr/bin:/bin",
    }
    result = subprocess.run(
        ["/usr/bin/bash", "scripts/gpu-status.sh"],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0
    assert "no config found" in result.stderr


def test_gpu_status_requires_a_configured_gpu(tmp_path: pathlib.Path) -> None:
    result, _, _ = run_gpu_status_with_stubs(tmp_path, config_text="gpu: {}\n")
    assert result.returncode != 0
    assert "gpu.pci_address is not set" in result.stderr


def test_gpu_status_dies_clearly_when_detect_fails(tmp_path: pathlib.Path) -> None:
    commands = tmp_path / "bin"
    commands.mkdir()
    uv = commands / "uv"
    uv.write_text("#!/usr/bin/bash\ncase \"$*\" in\n  *' detect') exit 1 ;;\nesac\n")
    uv.chmod(uv.stat().st_mode | stat.S_IXUSR)
    real_yq = shutil.which("yq")
    assert real_yq is not None
    yq = commands / "yq"
    yq.write_text(f"#!/usr/bin/bash\nexec {real_yq} \"$@\"\n")
    yq.chmod(yq.stat().st_mode | stat.S_IXUSR)
    config = tmp_path / "models.yml"
    config.write_text('gpu:\n  pci_address: "0000:03:00.0"\n  vram_budget_ceiling_mib: 15565\n')
    environment = os.environ | {
        "HOME": str(tmp_path / "home"),
        "LLM_ENV_CONFIG": str(config),
        "LLM_ENV_MODELS_DIR": str(tmp_path / "models"),
        "PATH": f"{commands}:/usr/bin:/bin",
    }
    result = subprocess.run(
        ["/usr/bin/bash", "scripts/gpu-status.sh"],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0
    assert "could not detect GPUs" in result.stderr


def test_gpu_status_shows_headroom_even_when_budget_is_infeasible(tmp_path: pathlib.Path) -> None:
    """cmd_budget emits a full, valid JSON payload (including available_mib)
    even when the budget doesn't fit, but exits nonzero in that case
    (existing, correct, diagnostic-only behavior). gpu-status.sh must still
    show the headroom number in exactly this scenario -- an infeasible
    budget is when an operator most needs to see it. Builds its own uv
    stub directly (rather than via run_gpu_status_with_stubs) so the budget
    case can both print a payload and exit 1."""
    commands = tmp_path / "bin"
    commands.mkdir()
    uv = commands / "uv"
    uv.write_text(
        "#!/usr/bin/bash\n"
        "case \"$*\" in\n"
        "  *' detect')\n"
        "    printf '%s\\n' '{\"gpus\":["
        "{\"card\":\"card1\",\"pci_address\":\"0000:03:00.0\",\"vram_total_mib\":16384,"
        "\"vram_used_mib\":2048,\"render_node\":\"renderD128\",\"connected_outputs\":[]},"
        "{\"card\":\"card0\",\"pci_address\":\"0000:0e:00.0\",\"vram_total_mib\":512,"
        "\"vram_used_mib\":51,\"render_node\":\"renderD129\",\"connected_outputs\":[]}]}' ;;\n"
        "  *' budget '*)\n"
        "    printf '%s\\n' '{\"available_mib\":9000,\"required_mib\":12000,\"feasible\":false}'\n"
        "    exit 1 ;;\n"
        "  *' processes-on-render-node '*) printf '%s\\n' '{\"processes\":[]}' ;;\n"
        "esac\n"
    )
    uv.chmod(uv.stat().st_mode | stat.S_IXUSR)
    real_yq = shutil.which("yq")
    assert real_yq is not None
    yq = commands / "yq"
    yq.write_text(f"#!/usr/bin/bash\nexec {real_yq} \"$@\"\n")
    yq.chmod(yq.stat().st_mode | stat.S_IXUSR)

    config = tmp_path / "models.yml"
    config.write_text('gpu:\n  pci_address: "0000:03:00.0"\n  vram_budget_ceiling_mib: 15565\n')
    environment = os.environ | {
        "HOME": str(tmp_path / "home"),
        "LLM_ENV_CONFIG": str(config),
        "LLM_ENV_MODELS_DIR": str(tmp_path / "models"),
        "PATH": f"{commands}:/usr/bin:/bin",
    }
    result = subprocess.run(
        ["/usr/bin/bash", "scripts/gpu-status.sh"],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert "budget headroom:    9000 MiB" in result.stdout
```

- [ ] **Step 4: Run to verify they fail**

Run: `uv run pytest tests/test_shell.py -k gpu_status -v`
Expected: FAIL — `/usr/bin/bash` itself exists, so `subprocess.run` executes it successfully; it's the *script argument* that's missing. Bash prints its own `scripts/gpu-status.sh: No such file or directory` to stderr and exits with status 127 (not a Python `FileNotFoundError` — that would only occur if `/usr/bin/bash` itself didn't exist). Every test in this run fails on `result.returncode == 127` instead of the assertions it's actually checking. Verify the exact status empirically if in doubt — don't assume the literal exception name will appear anywhere.

- [ ] **Step 5: Create `scripts/gpu-status.sh`**

```bash
#!/usr/bin/env bash
# gpu-status.sh — live diagnostic of the configured dGPU's VRAM contention,
# with an optional, explicitly-confirmed migration of the worst offenders
# to the iGPU. Read-only unless the operator confirms a migration prompt.
set -euo pipefail
# shellcheck disable=SC1091 # Resolved from this script at runtime.
# shellcheck source=../tools/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/../tools/lib.sh"

require_cmd yq jq

[ -f "$CONFIG_PATH" ] || die "no config found at ${CONFIG_PATH}; run 'make setup' first"

# Matches the pattern every other user-facing entrypoint in this repo
# follows (scripts/check-setup.sh, scripts/key-reset.sh, scripts/start.sh,
# setup/enable-boot.sh, setup/render-unit.sh,
# setup/setup-local-llm-agents.sh, setup/setup.sh): migrate the config file
# on disk before reading any field from it with a raw `yq`. Without this, a
# config that predates a field (e.g. gpu.vram_budget_ceiling_mib) would
# read via `yq`'s own `// 0` default below as "uncapped", while the budget
# headroom line further down (computed by shelling out to `llmenv budget`,
# whose `load_config()` migrates in-memory by default) would reflect the
# real, migrated, non-zero ceiling -- two adjacent lines of the same
# diagnostic disagreeing about whether a cap exists.
migrate_config_file || die "configuration migration failed"

pci="$(yq -r '.gpu.pci_address // ""' "$CONFIG_PATH")"
[ -n "$pci" ] && [ "$pci" != null ] || die "gpu.pci_address is not set; run 'make setup' first"

facts="$(llmenv detect)" || die "could not detect GPUs"

gpu="$(echo "$facts" | jq --arg pci "$pci" '[.gpus[] | select(.pci_address == $pci)] | first')"
[ -n "$gpu" ] && [ "$gpu" != null ] || die "configured GPU ${pci} not detected"

render_node="$(echo "$gpu" | jq -r '.render_node')"
vram_total="$(echo "$gpu" | jq -r '.vram_total_mib')"
vram_used="$(echo "$gpu" | jq -r '.vram_used_mib')"

ceiling_mib="$(yq -r '.gpu.vram_budget_ceiling_mib // 0' "$CONFIG_PATH")"
if [ "$ceiling_mib" = "0" ]; then
    ceiling_display="uncapped"
else
    ceiling_display="${ceiling_mib} MiB"
fi

headroom_display="unavailable"
budget_json="$(llmenv --config "$CONFIG_PATH" budget --models-dir "$MODELS_DIR" 2>/dev/null)" || true
if [ -n "$budget_json" ]; then
    headroom_display="$(echo "$budget_json" | jq -r '"\(.available_mib) MiB"')"
fi

log_step "GPU ${pci} (${render_node})"
echo "  total VRAM:         ${vram_total} MiB"
echo "  used (system-wide): ${vram_used} MiB"
echo "  llm-env ceiling:    ${ceiling_display}"
echo "  budget headroom:    ${headroom_display}"

# Approximate, by-name best-effort exclusion of this codebase's own
# dGPU-consuming stack -- comm names are matched literally, so a
# legitimately-named user process (e.g. one also called `podman`) would be
# wrongly excluded too; this is an accepted limitation, not fixed here.
# Deliberately does NOT list "gpu-status.sh": a running bash script's own
# `comm` (as read from /proc/<pid>/comm) is always `bash`, never the
# script's filename, so no name-based entry could ever match this script's
# own process -- there is no self-exclusion entry to add here, because none
# would ever match.
exclude_names='["llama-server","conmon","podman"]'
top3="$(llmenv processes-on-render-node --render-node "$render_node" | jq --argjson exclude "$exclude_names" '
    [.processes[] | select(([.comm] | inside($exclude)) | not)]
    | sort_by(-.vram_mib)
    | .[:3]
')"
count="$(echo "$top3" | jq 'length')"

if [ "$count" -eq 0 ]; then
    log_info "no other processes using the dGPU"
    exit 0
fi

log_step "Top VRAM users on this GPU"
echo "$top3" | jq -r '.[] | "  \(.pid)\t\(.comm)\t\(.vram_mib) MiB"'
```

```bash
chmod +x scripts/gpu-status.sh
```

`budget_json="$(...)" || true` captures stdout regardless of `llmenv budget`'s exit status (nonzero on an infeasible budget is existing, correct, diagnostic-only behavior — not an error to `die` on), and the subsequent `[ -n "$budget_json" ]` check — not an exit-code check — decides whether to parse it. This means the one scenario where headroom matters most (an infeasible budget) still displays the number instead of "unavailable".

- [ ] **Step 6: Wire the Makefile and help text**

In `Makefile`, add `gpu-status` to the `.PHONY` line (after `status`):

```makefile
.PHONY: help prerequisites dev-setup setup setup-local-llm-agents start stop restart check-setup check-server check-with-agents benchmark \
        key-reset show-secrets enable-boot disable-boot status gpu-status logs validate test clean
```

Add the target itself, after the existing `status:` target:

```makefile
gpu-status:
	@bash tools/run-target.sh gpu-status -- bash scripts/gpu-status.sh
```

In `scripts/help.sh`, add after the `make status` line:

```bash
echo "make gpu-status    Show live dGPU contention; optionally migrate offenders to the iGPU"
```

- [ ] **Step 7: Run to verify everything passes**

Run: `uv run pytest tests/test_shell.py -k "approved_directories or dispatches_relocated or gpu_status" -v`
Expected: PASS (2 location/dispatch tests + 10 diagnostic-display tests = 12 tests)

- [ ] **Step 8: Full suite + lint + shellcheck**

Run: `uv run pytest tests/ -q && uv run ruff check . && shellcheck scripts/gpu-status.sh`
Expected: all clean

- [ ] **Step 9: Commit**

```bash
git add scripts/gpu-status.sh Makefile scripts/help.sh tests/test_shell.py
git commit -m "feat(gpu-status): add read-only dGPU contention diagnostic"
```

---

### Task 3: Per-process iGPU migration (single confirmation, `.desktop` overrides)

**Files:**
- Modify: `scripts/gpu-status.sh` (append after the "Top VRAM users on this GPU" block from Task 2)
- Modify: `tests/test_shell.py` (extend `run_gpu_status_with_stubs()`'s `input_text`/`system_applications_dir` usage; new tests)

**Interfaces:**
- Consumes: Task 2's `$facts`, `$pci`, `$top3`, `$count` (still in scope at the point this task's code is appended).
- Produces: `$igpu_pci`, `$dri_prime` shell variables, defined once here — Task 4 reuses both without recomputing them.
- Produces: `confirm()` shell function (`confirm PROMPT` → 0 on `y`/`Y`/`yes`/`YES`, 1 otherwise, and always 1 when `LLM_ENV_ASSUME_YES=1` — no prompt is ever auto-accepted) — Task 4 reuses it for its own prompt.

- [ ] **Step 1: Write the failing migration tests**

Add to `tests/test_shell.py`, after the Task 2 gpu-status tests:

```python
def _desktop_file(directory: pathlib.Path, filename: str, exec_line: str) -> pathlib.Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
    path.write_text(f"[Desktop Entry]\nType=Application\nName=Test App\nExec={exec_line}\nIcon=test\n")
    return path


def test_gpu_status_prompts_once_for_all_flagged_processes(tmp_path: pathlib.Path) -> None:
    """A single combined confirmation covers every flagged process, never
    one prompt per process. This can't be proven by asserting on the
    prompt's literal text: under piped, non-terminal stdin (exactly how
    every test in this file invokes the script), bash's `read -rp` prompt
    is written to neither stdout nor stderr at all -- per the bash manual,
    "the prompt is displayed only if input is coming from a terminal."
    (This codebase's own `test_cleanup_confirmation_prompt_...` in this
    file follows the same rule: it never asserts on `clean.sh`'s `read -rp`
    prompt text.) Prove it by *effect* instead: two flagged processes, and
    exactly one "y" line for the (first) migration prompt. If the script
    prompted once per process, that single "y" would only accept the first
    process, leaving the second line ("n") to be consumed by a second
    per-process prompt -- and the later, independent default-preference
    prompt (Task 4) would then have no input left. Since both processes end
    up migrated from that one "y" line, and the run still exits cleanly
    after consuming the second line for the separate default-preference
    prompt, the confirmation must be single and combined.
    """
    processes = json.dumps(
        {
            "processes": [
                {"pid": 1, "comm": "firefox", "exe": "firefox", "vram_mib": 500},
                {"pid": 2, "comm": "thunderbird", "exe": "thunderbird", "vram_mib": 400},
            ]
        }
    )
    system_apps = tmp_path / "system-apps"
    _desktop_file(system_apps, "firefox.desktop", "firefox %u")
    _desktop_file(system_apps, "thunderbird.desktop", "thunderbird %u")
    result, _, home = run_gpu_status_with_stubs(
        tmp_path,
        processes_json=processes,
        input_text="y\nn\n",
        system_applications_dir=system_apps,
    )
    assert result.returncode == 0
    assert (home / ".local/share/applications/firefox.desktop").is_file()
    assert (home / ".local/share/applications/thunderbird.desktop").is_file()
    assert "migration summary: 2 overridden, 0 skipped" in result.stdout


def test_gpu_status_writes_a_user_override_on_confirmed_migration(tmp_path: pathlib.Path) -> None:
    processes = json.dumps(
        {"processes": [{"pid": 1, "comm": "firefox", "exe": "firefox", "vram_mib": 500}]}
    )
    system_apps = tmp_path / "system-apps"
    _desktop_file(system_apps, "firefox.desktop", "firefox %u")
    result, _, home = run_gpu_status_with_stubs(
        tmp_path,
        processes_json=processes,
        input_text="y\nn\n",
        system_applications_dir=system_apps,
    )
    override = home / ".local/share/applications/firefox.desktop"
    assert override.is_file()
    content = override.read_text()
    assert "Exec=env DRI_PRIME=pci-0000_0e_00_0 firefox %u" in content
    assert "overridden -> firefox.desktop" in result.stdout
    assert "migration summary: 1 overridden, 0 skipped" in result.stdout


def test_gpu_status_never_touches_the_system_desktop_file(tmp_path: pathlib.Path) -> None:
    processes = json.dumps(
        {"processes": [{"pid": 1, "comm": "firefox", "exe": "firefox", "vram_mib": 500}]}
    )
    system_apps = tmp_path / "system-apps"
    system_file = _desktop_file(system_apps, "firefox.desktop", "firefox %u")
    original = system_file.read_text()
    run_gpu_status_with_stubs(
        tmp_path,
        processes_json=processes,
        input_text="y\nn\n",
        system_applications_dir=system_apps,
    )
    assert system_file.read_text() == original


def test_gpu_status_skips_a_process_with_no_matching_launcher(tmp_path: pathlib.Path) -> None:
    processes = json.dumps(
        {"processes": [{"pid": 1, "comm": "unmatched-app", "exe": "unmatched-app", "vram_mib": 500}]}
    )
    system_apps = tmp_path / "system-apps"
    system_apps.mkdir()
    result, _, home = run_gpu_status_with_stubs(
        tmp_path,
        processes_json=processes,
        input_text="y\nn\n",
        system_applications_dir=system_apps,
    )
    assert "no launcher found, skipped" in result.stdout
    assert "migration summary: 0 overridden, 1 skipped" in result.stdout
    assert not (home / ".local/share/applications").exists() or not list(
        (home / ".local/share/applications").glob("*.desktop")
    )


def test_gpu_status_declines_migration_writes_nothing(tmp_path: pathlib.Path) -> None:
    processes = json.dumps(
        {"processes": [{"pid": 1, "comm": "firefox", "exe": "firefox", "vram_mib": 500}]}
    )
    system_apps = tmp_path / "system-apps"
    _desktop_file(system_apps, "firefox.desktop", "firefox %u")
    result, _, home = run_gpu_status_with_stubs(
        tmp_path,
        processes_json=processes,
        input_text="n\nn\n",
        system_applications_dir=system_apps,
    )
    assert not (home / ".local/share/applications").exists() or not list(
        (home / ".local/share/applications").glob("*.desktop")
    )
    assert "overridden" not in result.stdout


def test_gpu_status_migration_is_idempotent_on_repeat_runs(tmp_path: pathlib.Path) -> None:
    """A second confirmed migration for the same app must not stack a
    second `env DRI_PRIME=...` prefix onto the first run's already-written
    override. find_desktop_file() searches the user override directory
    before the system one, so the second run finds its own prior output as
    input -- apply_igpu_override()'s awk substitution must replace an
    existing `env DRI_PRIME=...` prefix rather than prepending another."""
    processes = json.dumps(
        {"processes": [{"pid": 1, "comm": "firefox", "exe": "firefox", "vram_mib": 500}]}
    )
    system_apps = tmp_path / "system-apps"
    _desktop_file(system_apps, "firefox.desktop", "firefox %u")
    run_gpu_status_with_stubs(
        tmp_path,
        processes_json=processes,
        input_text="y\nn\n",
        system_applications_dir=system_apps,
    )
    result, _, home = run_gpu_status_with_stubs(
        tmp_path,
        processes_json=processes,
        input_text="y\nn\n",
        system_applications_dir=system_apps,
    )
    override = home / ".local/share/applications/firefox.desktop"
    content = override.read_text()
    exec_line = next(line for line in content.splitlines() if line.startswith("Exec="))
    assert exec_line.count("DRI_PRIME") == 1
    assert exec_line == "Exec=env DRI_PRIME=pci-0000_0e_00_0 firefox %u"


def test_gpu_status_finds_and_reprefixes_an_existing_override_when_system_file_is_gone(
    tmp_path: pathlib.Path,
) -> None:
    """find_desktop_file() must genuinely recognize its own previously-written
    override, not merely appear idempotent because it fell through to a
    still-present, untouched system file. Pre-seed the HOME override
    directory directly (bypassing the script) with an already-applied
    `Exec=env DRI_PRIME=pci-old-value firefox %u` line, then point
    LLM_ENV_SYSTEM_APPLICATIONS_DIR at a directory that doesn't exist at
    all -- so the system file is genuinely unreachable and the only way
    this run can find a launcher for firefox is by matching the existing
    HOME override. Confirms two things at once: find_desktop_file() strips
    the "env DRI_PRIME=..." prefix before extracting its match token (the
    fix for HIGH #2), and apply_igpu_override() safely rewrites a target
    file that is the SAME path as its input via a temp-file-then-rename
    (the fix for CRITICAL #1) -- producing exactly one DRI_PRIME
    assignment with the new value, not zero, not two, and not an empty
    file."""
    processes = json.dumps(
        {"processes": [{"pid": 1, "comm": "firefox", "exe": "firefox", "vram_mib": 500}]}
    )
    home = tmp_path / "home"
    apps_dir = home / ".local" / "share" / "applications"
    apps_dir.mkdir(parents=True)
    (apps_dir / "firefox.desktop").write_text(
        "[Desktop Entry]\nType=Application\nName=Test App\n"
        "Exec=env DRI_PRIME=pci-old-value firefox %u\nIcon=test\n"
    )
    system_apps = tmp_path / "system-apps-does-not-exist"
    result, _, home = run_gpu_status_with_stubs(
        tmp_path,
        processes_json=processes,
        input_text="y\nn\n",
        system_applications_dir=system_apps,
    )
    assert result.returncode == 0
    override = home / ".local/share/applications/firefox.desktop"
    content = override.read_text()
    exec_line = next(line for line in content.splitlines() if line.startswith("Exec="))
    assert exec_line.count("DRI_PRIME") == 1
    assert exec_line == "Exec=env DRI_PRIME=pci-0000_0e_00_0 firefox %u"
    assert "overridden -> firefox.desktop" in result.stdout
    assert "migration summary: 1 overridden, 0 skipped" in result.stdout


def test_gpu_status_skips_a_process_when_override_write_fails(tmp_path: pathlib.Path) -> None:
    """One process's .desktop override write failing (e.g. an unwritable
    target directory) must be reported as skipped and must not abort the
    run under `set -euo pipefail` -- the run must still exit 0 overall."""
    processes = json.dumps(
        {"processes": [{"pid": 1, "comm": "firefox", "exe": "firefox", "vram_mib": 500}]}
    )
    system_apps = tmp_path / "system-apps"
    _desktop_file(system_apps, "firefox.desktop", "firefox %u")
    home = tmp_path / "home"
    share_dir = home / ".local" / "share"
    share_dir.mkdir(parents=True)
    share_dir.chmod(0o555)  # read-only: mkdir of "applications" inside it fails
    try:
        result, _, _ = run_gpu_status_with_stubs(
            tmp_path,
            processes_json=processes,
            input_text="y\nn\n",
            system_applications_dir=system_apps,
        )
    finally:
        share_dir.chmod(0o755)
    assert result.returncode == 0
    assert "override failed, skipped" in result.stdout
    assert "migration summary: 0 overridden, 1 skipped" in result.stdout


def test_gpu_status_warns_and_skips_migration_with_no_alternate_gpu(tmp_path: pathlib.Path) -> None:
    """Asserted against result.stderr, not result.stdout: this message is
    emitted via tools/lib.sh's log_warn(), whose `printf ... >&2` is an
    unconditional redirect to stderr regardless of tty state -- unlike the
    `read -rp` prompt text elsewhere in this file (which bash only ever
    writes to a terminal, never to a pipe), log_warn's output always lands
    on stderr in a piped/non-terminal test run."""
    processes = json.dumps(
        {"processes": [{"pid": 1, "comm": "firefox", "exe": "firefox", "vram_mib": 500}]}
    )
    commands = tmp_path / "bin"
    commands.mkdir()
    uv = commands / "uv"
    uv.write_text(
        "#!/usr/bin/bash\n"
        "case \"$*\" in\n"
        "  *' detect') printf '%s\\n' '{\"gpus\":["
        "{\"card\":\"card1\",\"pci_address\":\"0000:03:00.0\",\"vram_total_mib\":16384,"
        "\"vram_used_mib\":2048,\"render_node\":\"renderD128\",\"connected_outputs\":[]}]}' ;;\n"
        "  *' budget '*) printf '%s\\n' '{\"available_mib\":9000,\"required_mib\":6000,\"feasible\":true}' ;;\n"
        f"  *' processes-on-render-node '*) printf '%s\\n' '{processes}' ;;\n"
        "esac\n"
    )
    uv.chmod(uv.stat().st_mode | stat.S_IXUSR)
    real_yq = shutil.which("yq")
    assert real_yq is not None
    yq = commands / "yq"
    yq.write_text(f"#!/usr/bin/bash\nexec {real_yq} \"$@\"\n")
    yq.chmod(yq.stat().st_mode | stat.S_IXUSR)

    config = tmp_path / "models.yml"
    config.write_text('gpu:\n  pci_address: "0000:03:00.0"\n  vram_budget_ceiling_mib: 15565\n')
    environment = os.environ | {
        "HOME": str(tmp_path / "home"),
        "LLM_ENV_CONFIG": str(config),
        "LLM_ENV_MODELS_DIR": str(tmp_path / "models"),
        "PATH": f"{commands}:/usr/bin:/bin",
    }
    result = subprocess.run(
        ["/usr/bin/bash", "scripts/gpu-status.sh"],
        cwd=ROOT,
        env=environment,
        input="n\n",
        text=True,
        capture_output=True,
        check=False,
    )
    assert "no alternate GPU detected; skipping migration" in result.stderr
```

(This test builds its own single-GPU `uv`/`yq` stubs directly, rather than calling `run_gpu_status_with_stubs`, since that helper's default `detect` stub always returns both a dGPU and an iGPU — exactly the two-GPU case the other tests need.)

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_shell.py -k "prompts_once or writes_a_user_override or never_touches_the_system or skips_a_process_with_no or migration_is_idempotent or finds_and_reprefixes or override_write_fails or declines_migration or warns_and_skips" -v`
Expected: FAIL — the script doesn't prompt or write overrides yet.

- [ ] **Step 3: Append the migration logic to `scripts/gpu-status.sh`**

After the Task 2 block's final line (`echo "$top3" | jq -r '.[] | "  \(.pid)\t\(.comm)\t\(.vram_mib) MiB"'`), append:

```bash

igpu_pci=""
igpu_candidate="$(echo "$facts" | jq --arg pci "$pci" '[.gpus[] | select(.pci_address != $pci)] | sort_by(.vram_total_mib) | first')"
if [ -n "$igpu_candidate" ] && [ "$igpu_candidate" != null ]; then
    igpu_pci="$(echo "$igpu_candidate" | jq -r '.pci_address')"
fi
dri_prime=""
[ -n "$igpu_pci" ] && dri_prime="pci-$(echo "$igpu_pci" | tr ':.' '__')"

confirm() {
    local prompt="$1" reply
    if [ "${LLM_ENV_ASSUME_YES:-0}" = "1" ]; then
        return 1
    fi
    read -rp "$prompt" reply || reply=""
    case "$reply" in
        y|Y|yes|YES) return 0 ;;
        *) return 1 ;;
    esac
}

find_desktop_file() {
    local exe_basename="$1" dir file exec_line exec_basename
    for dir in "${HOME}/.local/share/applications" "${LLM_ENV_SYSTEM_APPLICATIONS_DIR:-/usr/share/applications}"; do
        [ -d "$dir" ] || continue
        while IFS= read -r -d '' file; do
            exec_line="$(grep -m1 '^Exec=' "$file" 2>/dev/null | cut -d= -f2-)"
            [ -n "$exec_line" ] || continue
            # Strip a leading "env DRI_PRIME=<value> " prefix -- the exact
            # prefix apply_igpu_override() itself writes -- before taking
            # the match token, so this function can recognize its own
            # previously-written override (whose Exec= line starts with
            # "env", not the app's binary name) and not just an untouched
            # system file. `#` removes the shortest match from the front;
            # non-matching lines (a plain "Exec=firefox %u") pass through
            # unchanged.
            exec_line="${exec_line#env DRI_PRIME=* }"
            exec_basename="$(basename "${exec_line%% *}")"
            if [ "$exec_basename" = "$exe_basename" ]; then
                printf '%s\n' "$file"
                return 0
            fi
        done < <(find "$dir" -maxdepth 1 -name '*.desktop' -print0 2>/dev/null)
    done
    return 1
}

apply_igpu_override() {
    local desktop_file="$1" prime="$2" target_dir target_file tmp_file
    target_dir="${HOME}/.local/share/applications"
    mkdir -p "$target_dir" || return 1
    target_file="${target_dir}/$(basename "$desktop_file")"
    # Write to a temp file in the same directory, then rename over
    # target_file, rather than redirecting awk's output directly into
    # target_file. desktop_file and target_file are frequently the SAME
    # path (find_desktop_file() prefers an existing HOME override over the
    # system file), and `awk '...' "$desktop_file" > "$target_file"` would
    # have the shell truncate target_file to empty via `>` before awk ever
    # opens it to read -- awk would then read nothing and silently write an
    # empty file. Writing to an independent temp file first means awk
    # always reads the real, untouched input regardless of whether the
    # source and destination paths coincide; only a subsequent `mv`
    # replaces target_file, and only once awk has fully succeeded.
    tmp_file="$(mktemp "${target_dir}/.gpu-status-override.XXXXXX")" || return 1
    if ! awk -v prefix="env DRI_PRIME=${prime} " '
        /^Exec=env DRI_PRIME=[^ ]* / { sub(/^Exec=env DRI_PRIME=[^ ]* /, "Exec=" prefix); print; next }
        /^Exec=/ { sub(/^Exec=/, "Exec=" prefix); print; next }
        { print }
    ' "$desktop_file" > "$tmp_file"; then
        rm -f "$tmp_file"
        return 1
    fi
    chmod 644 "$tmp_file" || { rm -f "$tmp_file"; return 1; }
    mv -f "$tmp_file" "$target_file" || { rm -f "$tmp_file"; return 1; }
}

if [ -z "$igpu_pci" ]; then
    log_warn "no alternate GPU detected; skipping migration"
elif confirm "Move these ${count} processes to the iGPU? [y/N] "; then
    moved=0
    skipped=0
    while IFS=$'\t' read -r p_pid p_comm p_exe; do
        if ! desktop_file="$(find_desktop_file "$p_exe")"; then
            echo "  ${p_comm} (pid ${p_pid}): no launcher found, skipped"
            skipped=$((skipped + 1))
        elif apply_igpu_override "$desktop_file" "$dri_prime"; then
            echo "  ${p_comm} (pid ${p_pid}): overridden -> $(basename "$desktop_file")"
            moved=$((moved + 1))
        else
            echo "  ${p_comm} (pid ${p_pid}): override failed, skipped"
            skipped=$((skipped + 1))
        fi
    done < <(echo "$top3" | jq -r '.[] | "\(.pid)\t\(.comm)\t\(.exe)"')
    log_info "migration summary: ${moved} overridden, ${skipped} skipped"
fi
```

`find_desktop_file()` searches `${HOME}/.local/share/applications` (the user override directory) before falling back to the system directory, so a second confirmed migration for the same app finds its own previously-written override — which already starts with `Exec=env DRI_PRIME=... firefox %u`. Without the prefix-stripping line added above, this would never actually happen: the function's match token is the first whitespace-delimited word of the `Exec=` value, and for an already-overridden line that word is `env`, not `firefox` — the comparison against `exe_basename="firefox"` would always fail, so the function would silently fall through past its own override to the system file (today, while the system file is untouched and happens to produce byte-identical output — masking the bug) or find no match at all (once the system file becomes unavailable, e.g. the app is later uninstalled while a working override still exists). Stripping the `env DRI_PRIME=<value> ` prefix before taking the match token is what lets the function recognize its own prior output at all, in either case.

The awk substitution has two ordered rules to keep the *content* idempotent once `find_desktop_file()` has correctly located the existing override: the first rule matches an `Exec=` line that *already* starts with `env DRI_PRIME=<anything> ` and replaces that whole prefix with the current one (so re-running never stacks a second `env DRI_PRIME=...`, and correctly updates the value if the iGPU's PCI address ever changes); only a plain `Exec=` line with no existing prefix falls through to the second rule, which prepends. Without this, `sub()`'s unconditional prepend would compound a new `env DRI_PRIME=...` in front of the existing one on every repeat run.

`apply_igpu_override()` writes to a `mktemp`-generated temp file in the same target directory and only `mv`s it over `target_file` after `awk` has fully succeeded, rather than redirecting `awk`'s output directly into `target_file` (`awk '...' "$desktop_file" > "$target_file"`). This matters precisely in the case `find_desktop_file()` above now correctly reaches: when the located override is the existing HOME file, `desktop_file` and `target_file` are the *same path*. A direct `>` redirection is opened by the shell — truncating that path to empty — before `awk` ever gets to read it, so `awk` would read nothing and silently write an empty `.desktop` file; `chmod` would then succeed on the now-empty file and the run would report the process as "overridden" even though the launcher is now broken. Reading from `desktop_file` while writing to an independent temp path sidesteps this regardless of whether the two paths coincide.

Every step inside `apply_igpu_override()` — `mkdir`, `mktemp`, the `awk` write, `chmod`, and the final `mv` — is checked explicitly and returns 1 on the first failure, rather than relying on the function's own `set -e` to enforce this. That reliance would not work: `apply_igpu_override` is invoked as the condition of an `elif` (see below), and bash's `set -e` is documented to not apply to *any* command in a pipeline/function invoked as the condition of an `if`/`elif`/`while`/`until` — so without the explicit checks, an early command's failure inside the function would be silently ignored, later commands would still run against incomplete state, and the function could return 0 (success) without `target_file` actually having been replaced with the intended content.

Placing both `find_desktop_file` and `apply_igpu_override` calls as the condition of an `if`/`elif` is deliberate, not cosmetic: bash's `set -e` is documented to not apply to a compound command's exit status when that command is the condition of an `if`, `elif`, `while`, or `until`, or part of an `&&`/`||` list. Calling `apply_igpu_override` directly (its old call site ran it as a bare statement, outside any condition) meant an internal failure would trigger `set -e` and kill the whole script instead of being reported as "skipped" for that one process — violating the Global Constraint that one process's override failure must never abort the run. Calling it as an `elif` condition keeps that constraint intact, and is exactly why the function's internal steps need their own explicit failure checks instead of leaning on `set -e` a second time.

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_shell.py -k "prompts_once or writes_a_user_override or never_touches_the_system or skips_a_process_with_no or migration_is_idempotent or finds_and_reprefixes or override_write_fails or declines_migration or warns_and_skips" -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Full suite + lint + shellcheck**

Run: `uv run pytest tests/ -q && uv run ruff check . && shellcheck scripts/gpu-status.sh`
Expected: all clean

- [ ] **Step 6: Commit**

```bash
git add scripts/gpu-status.sh tests/test_shell.py
git commit -m "feat(gpu-status): add single-confirmation per-process iGPU migration"
```

---

### Task 4: Default iGPU preference for new apps (`environment.d`)

**Files:**
- Modify: `scripts/gpu-status.sh` (append after Task 3's migration `if`/`elif` block)
- Modify: `tests/test_shell.py`

**Interfaces:**
- Consumes: Task 3's `$igpu_pci`, `$dri_prime`, `confirm()`.
- Produces: nothing further consumed by later tasks — this is the last piece of script logic.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_shell.py`:

```python
def test_gpu_status_writes_environment_d_default_on_confirmation(tmp_path: pathlib.Path) -> None:
    processes = json.dumps(
        {"processes": [{"pid": 1, "comm": "firefox", "exe": "firefox", "vram_mib": 500}]}
    )
    system_apps = tmp_path / "system-apps"
    _desktop_file(system_apps, "firefox.desktop", "firefox %u")
    result, _, home = run_gpu_status_with_stubs(
        tmp_path,
        processes_json=processes,
        input_text="n\ny\n",
        system_applications_dir=system_apps,
    )
    conf = home / ".config/environment.d/60-llm-env-igpu-default.conf"
    assert conf.is_file()
    assert conf.read_text() == "DRI_PRIME=pci-0000_0e_00_0\n"
    assert "wrote" in result.stdout
    assert "best-effort" in result.stdout
    assert "DRI_PRIME convention" in result.stdout
    assert "next login" in result.stdout
    assert "llm-server itself" in result.stdout


def test_gpu_status_default_preference_declined_writes_nothing(tmp_path: pathlib.Path) -> None:
    processes = json.dumps(
        {"processes": [{"pid": 1, "comm": "firefox", "exe": "firefox", "vram_mib": 500}]}
    )
    system_apps = tmp_path / "system-apps"
    _desktop_file(system_apps, "firefox.desktop", "firefox %u")
    result, _, home = run_gpu_status_with_stubs(
        tmp_path,
        processes_json=processes,
        input_text="n\nn\n",
        system_applications_dir=system_apps,
    )
    assert not (home / ".config/environment.d/60-llm-env-igpu-default.conf").exists()


def test_gpu_status_default_preference_is_idempotent_on_repeat_runs(tmp_path: pathlib.Path) -> None:
    processes = json.dumps(
        {"processes": [{"pid": 1, "comm": "firefox", "exe": "firefox", "vram_mib": 500}]}
    )
    system_apps = tmp_path / "system-apps"
    _desktop_file(system_apps, "firefox.desktop", "firefox %u")
    run_gpu_status_with_stubs(
        tmp_path,
        processes_json=processes,
        input_text="n\ny\n",
        system_applications_dir=system_apps,
    )
    result, _, home = run_gpu_status_with_stubs(
        tmp_path,
        processes_json=processes,
        input_text="n\ny\n",
        system_applications_dir=system_apps,
    )
    conf_dir = home / ".config/environment.d"
    assert len(list(conf_dir.glob("*llm-env*"))) == 1


def test_gpu_status_assume_yes_never_writes_any_override(tmp_path: pathlib.Path) -> None:
    """LLM_ENV_ASSUME_YES=1 means "auto-decline" for gpu-status.sh's two
    prompts (see the plan's Global Constraints) -- confirm this holds even
    when stdin would answer "y" to both prompts if they were ever read,
    proving ASSUME_YES short-circuits confirm() before either prompt is
    reached at all, not merely that "y" happens not to arrive."""
    processes = json.dumps(
        {"processes": [{"pid": 1, "comm": "firefox", "exe": "firefox", "vram_mib": 500}]}
    )
    system_apps = tmp_path / "system-apps"
    _desktop_file(system_apps, "firefox.desktop", "firefox %u")
    result, _, home = run_gpu_status_with_stubs(
        tmp_path,
        processes_json=processes,
        input_text="y\ny\n",
        system_applications_dir=system_apps,
        extra_env={"LLM_ENV_ASSUME_YES": "1"},
    )
    assert result.returncode == 0
    assert not (home / ".local/share/applications").exists() or not list(
        (home / ".local/share/applications").glob("*.desktop")
    )
    assert not (home / ".config/environment.d/60-llm-env-igpu-default.conf").exists()
```

Note: the idempotency test calls `run_gpu_status_with_stubs` twice with the *same* `tmp_path`, so the second run's `home` directory is identical to the first's (the helper always derives `home = tmp_path / "home"`) — this is intentional, it's what makes the idempotency check meaningful. The `ASSUME_YES` test above uses `run_gpu_status_with_stubs`'s `extra_env` parameter (part of the helper's signature, defined in Task 2's Step 3) to inject `LLM_ENV_ASSUME_YES=1` into a single run.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_shell.py -k "writes_environment_d or default_preference or assume_yes_never_writes" -v`
Expected: FAIL — no second prompt exists yet.

- [ ] **Step 3: Append the default-preference logic to `scripts/gpu-status.sh`**

After Task 3's `if [ -z "$igpu_pci" ]; then ... elif confirm ...; then ... fi` block, append:

```bash

if [ -n "$igpu_pci" ]; then
    if confirm "Set the iGPU as the default GPU for new apps? [y/N] "; then
        env_dir="${HOME}/.config/environment.d"
        mkdir -p "$env_dir"
        printf 'DRI_PRIME=%s\n' "$dri_prime" > "${env_dir}/60-llm-env-igpu-default.conf"
        log_info "wrote ${env_dir}/60-llm-env-igpu-default.conf"
        log_info "best-effort: only affects apps that respect Mesa's DRI_PRIME convention, only takes effect on your next login/session (not already-running processes), and never affects llm-server itself"
    fi
fi
```

The second `log_info` line is the design doc's required caveat
(`docs/superpowers/specs/2026-08-09-gpu-contention-diagnostic-tool-design.md`,
"Default GPU preference for new/unmapped apps" section), printed verbatim
in close paraphrase so an operator who confirms this prompt sees, in the
same run, exactly what the write will and won't do: it's best-effort
(only apps that honor Mesa's `DRI_PRIME` convention respect it at all),
it only takes effect on the next login/session (never an already-running
process), and it never affects `llm-server` itself (which selects its GPU
explicitly by PCI address inside the container, independent of this
variable).

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_shell.py -k "writes_environment_d or default_preference or assume_yes_never_writes" -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Full suite + lint + shellcheck**

Run: `uv run pytest tests/ -q && uv run ruff check . && shellcheck scripts/gpu-status.sh`
Expected: all clean

- [ ] **Step 6: Commit**

```bash
git add scripts/gpu-status.sh tests/test_shell.py
git commit -m "feat(gpu-status): add idempotent default-iGPU-for-new-apps preference"
```

---

### Task 5: Docs and final verification

**Files:**
- Modify: `.agents/architecture.md` (Invariants section, after the two RAM/VRAM ceiling bullets added by the prior resource-limits plan)

**Interfaces:** None — this task adds no new code paths. Every error-handling case from the design spec (`no dGPU configured / llmenv detect fails`, `zero non-excluded processes`, `one process's desktop-file lookup failing`) is already covered by Tasks 2 and 3's tests; this task only verifies the whole feature end-to-end and documents it.

- [ ] **Step 1: Add the Invariants bullet**

In `.agents/architecture.md`, after the two existing RAM/VRAM ceiling bullets (added by the resource-limits plan), add:

```markdown
- `make gpu-status` is informational only — it never runs automatically and
  never gates `make start`. Any remediation it applies is opt-in via an
  explicit `[y/N]` confirmation, and is limited to XDG per-user overrides
  (`~/.local/share/applications/*.desktop`, `~/.config/environment.d/`) —
  it never modifies a system file, and never touches an already-running
  process (only future launches of the same app are affected).
```

- [ ] **Step 2: Run the full suite, ruff, and shellcheck**

Run: `uv run pytest tests/ -q && uv run ruff check . && shellcheck scripts/*.sh setup/*.sh tools/*.sh`
Expected: all passing/clean

- [ ] **Step 3: Commit**

```bash
git add .agents/architecture.md
git commit -m "docs: document the gpu-status diagnostic tool invariants"
```

- [ ] **Step 4: Manual live verification**

Per this project's established convention (see `docs/superpowers/specs/2026-08-09-gpu-contention-diagnostic-tool-design.md`'s Testing section), after the automated suite is green:

1. Run `make gpu-status` for real against the live host.
2. Confirm the reported total/used VRAM against `radeontop` or `amdgpu_top` if available.
3. If any process is flagged, confirm accepting the migration prompt writes a working override — either re-run the app via `gio launch <file>.desktop` or relaunch it normally and confirm (via `radeontop`/`amdgpu_top`, or the same `processes-on-render-node` output) that it now runs on the iGPU's render node instead of the dGPU's.
4. Confirm declining leaves the app's `.desktop` behavior unchanged, and that deleting a written override file restores the original launch behavior.

This step has no automated pass/fail — report what you observed to your human partner.

---

## Self-Review Notes

**Spec coverage:** Every component in the design doc has a task — `pylib/detect.py`'s `processes_on_render_node()` (Task 1), the diagnostic display (Task 2), per-process migration (Task 3), default-GPU preference (Task 4), and all three Error Handling cases (covered across Tasks 2/3's tests, verified again in Task 5). The design's `Testing` section's three bullets map to: unit tests with injectable `proc_root` (Task 1), shell tests with stubbed `llmenv detect`/`processes-on-render-node` output and fixture `.desktop` files (Tasks 2-4), and manual live verification (Task 5, Step 4).

**Deviation from the design doc:** the design specifies `tools/gpu-status.sh`; this plan places it at `scripts/gpu-status.sh` instead. `tools/` in this codebase holds only shared plumbing sourced by other scripts (`lib.sh`, `run-target.sh`, `validate.sh`, `test.sh`) — every user-invoked `make`-target script (`status.sh`, `clean.sh`, `check-server.sh`, etc.) lives in `scripts/` or `setup/`, enforced by `tests/test_shell.py::test_shell_scripts_use_the_approved_directories`. `gpu-status.sh` is a `make`-target script like `status.sh`, so it follows that convention rather than the design doc's literal path.

**Placeholder scan:** no TBD/TODO markers; every step has real code, not a description of code.

**Type/name consistency:** `processes_on_render_node()`'s return shape (`{pid, comm, exe, vram_mib}`) is used identically by its Task 1 tests, its CLI wrapper, and every Task 2-4 shell-test JSON fixture. `igpu_pci`/`dri_prime`/`confirm()` are defined once in Task 3 and reused, not redefined, in Task 4.

**Revision (iteration 1 review):** Fixed Task 1's import citation (`llmenv.py:48`, not `:26-33`; the corrected import keeps `DetectError`/`host_resources` alongside the new `processes_on_render_node`), fixed `_parse_fdinfo_vram_bytes()` to sum at byte precision before a single final MiB conversion (was losing sub-MiB fds), replaced prompt-text assertions with effect-based assertions in the shell tests (piped stdin never surfaces a `read -rp` prompt in captured output, matching this codebase's own `test_cleanup_confirmation_prompt_...` precedent), isolated `apply_igpu_override()`'s internal failures from `set -e` by calling it as an `if`/`elif` condition, made the `.desktop` override write idempotent against repeat migrations, changed the budget-headroom capture to not gate on `llmenv budget`'s exit code, reordered Task 2 into TDD order (failing tests before the implementation they test), and added tests for `llmenv detect` failing and for `LLM_ENV_ASSUME_YES=1` never writing any override. The Global Constraints section now states explicitly that `LLM_ENV_ASSUME_YES=1` is a deliberate exception to this codebase's usual "auto-accept" meaning for `gpu-status.sh` specifically, with its rationale.

**Revision (iteration 2 review):** Two independent reviewers converged on one defect cluster in the iteration-1 idempotency fix, from complementary angles, and both are now fixed together in Task 3 Step 3: (1) `find_desktop_file()` extracted the first whitespace-delimited token of an `Exec=` line as its match token without ever stripping a prior `env DRI_PRIME=<value> ` prefix — so for an already-overridden file the extracted token was `env`, never the app's binary name, and the function could never actually recognize its own previously-written override (it either fell through to a still-present, unmodified system file that happened to produce byte-identical output — masking the bug — or, once the next fix landed, would find no match at all once the system file became unavailable). Fixed by stripping that exact prefix from the `Exec=` value before extracting the match token. (2) `apply_igpu_override()` wrote `awk '...' "$desktop_file" > "$target_file"` — a direct redirection that truncates `$target_file` to empty *before* `awk` reads `$desktop_file`, which is silently destructive exactly when the two paths coincide (i.e. exactly the case fix (1) now correctly reaches: matching an existing HOME override). Fixed by writing to a `mktemp`-generated temp file in the same directory and `mv`-ing it over `target_file` only after `awk` succeeds — safe regardless of whether the input and output paths are the same. While rewriting the function, also gave every internal step (`mkdir`, `mktemp`, the `awk` write, `chmod`, `mv`) its own explicit failure check rather than leaning on `set -e`, since `apply_igpu_override` is invoked as an `elif` condition and bash's `set -e` is documented to not apply inside a command invoked as an `if`/`elif`/`while`/`until` condition — without the explicit checks, an internal failure could be silently swallowed and the function could return success without `target_file` actually being replaced. Added `test_gpu_status_finds_and_reprefixes_an_existing_override_when_system_file_is_gone` (Task 3 Step 1), which pre-seeds a HOME override with an already-applied `DRI_PRIME` prefix, points `LLM_ENV_SYSTEM_APPLICATIONS_DIR` at a directory that doesn't exist at all, and confirms the run still finds and correctly re-prefixes the existing override with exactly one `DRI_PRIME` assignment — the scenario the previous test suite never actually exercised. Also corrected Task 2 Step 4's "expected failure" description (`/usr/bin/bash` exists and executing it against a missing script path is bash's own exit-127 "No such file or directory," not a Python `FileNotFoundError`).

**Revision (iteration 3 review):** Two independent reviewers each found one new, independent issue, plus a third, non-functional detail. (1) [IMPORTANT] `processes_on_render_node()` (Task 1) summed every fdinfo record matching the render node for a PID with no de-duplication, but the kernel's DRM fdinfo interface defines `drm-client-id` specifically so consumers can recognize duplicated fds (e.g. via `dup()`) referring to one logical client and count that client once, not once per fd. Fixed by replacing `_parse_fdinfo_vram_bytes()` with `_parse_fdinfo()`, which also returns the fd's `drm-client-id` (or `None` if the fdinfo record doesn't expose one); `processes_on_render_node()` now tracks `seen_client_ids` per PID and skips an fd's VRAM if its client-id was already counted for that PID, while still counting every fd on its own when no client-id is exposed at all (preserving today's behavior for kernels/drivers that don't expose it). Added `test_processes_on_render_node_dedupes_same_drm_client_id_across_fds` (two fds, same client-id, VRAM counted once) and gave the existing `test_processes_on_render_node_sums_multiple_fds_for_one_pid` distinct client-ids per fd so it continues to prove genuinely distinct clients still sum in full. (2) [HIGH] `gpu-status.sh` read `gpu.vram_budget_ceiling_mib` via a raw `yq` on `$CONFIG_PATH` without running this codebase's own `migrate_config_file` first — unlike every other user-facing entrypoint in this repo — so an unmigrated config (missing the field entirely, a real upgrade path) would print "uncapped" on one line while the very next line (budget headroom, via `llmenv budget`, which migrates in-memory) reflected the real, non-zero ceiling: two adjacent lines of one diagnostic disagreeing about whether a cap exists. Fixed by calling `migrate_config_file || die "configuration migration failed"` immediately after the config-existence check and before any `yq` read, matching `scripts/check-setup.sh`/`scripts/start.sh`/etc.'s established pattern. Added `test_gpu_status_migrates_the_config_before_reading_the_ceiling`, whose `uv` stub's `migrate-config` case actually rewrites the config on disk with `yq -i`, proving the script calls migration before reading the ceiling rather than merely tolerating the call. (3) [MEDIUM] The `"gpu-status.sh"` entry in `exclude_names` could never match, because a running bash script's own `/proc/<pid>/comm` is `bash`, never its filename — verified empirically. Evaluated genuine PID-based self-exclusion (comparing against `$$` in the jq filter) as the reviewer's preferred fix: the production-code change itself is cheap (one added `select(.pid != $self)` clause), but reliably black-box-testing it would require either injecting the real, not-known-in-advance subprocess PID into a stub file under an inherent start/write race, or having a test-only stub walk `/proc` parent-PID chains to a hardcoded depth that silently breaks if the script's subshell/pipeline structure ever changes — both meaningfully complicating this plan's established, fully-deterministic, static-fixture black-box testing pattern (used by every other test in `tests/test_shell.py`) in a way the underlying fix doesn't warrant. Took the documented fallback instead: dropped the non-functional `"gpu-status.sh"` entry from `exclude_names`, added a code comment at the exclusion site explaining why no self-exclusion entry exists, and rewrote `test_gpu_status_excludes_the_llm_env_stack_from_the_table`'s docstring and fixture to state plainly that this is approximate, by-name best-effort exclusion of the known dGPU-consuming stack (the same accepted limitation already documented for `podman`/`conmon`), not a self-exclusion guarantee.

**Revision (iteration 5 review):** Two independent findings. (1) [CRITICAL] `test_gpu_status_warns_and_skips_migration_with_no_alternate_gpu` (Task 3 Step 1) asserted the "no alternate GPU detected; skipping migration" message against `result.stdout`, but the script emits it via `tools/lib.sh`'s `log_warn()`, whose `printf ... >&2` is an unconditional, explicit redirect to stderr — unlike the `read -rp` prompts elsewhere in this file, which bash only ever writes to a terminal, never to a pipe. Fixed by changing the assertion to `result.stderr` and adding a docstring to the test explaining why (it's the only `log_warn` call in the whole script, and the fix is unrelated to the `read -rp`/prompt-text precedent the other tests in this file follow). Task 3 Step 4's "Expected: PASS (9 tests)" count was already correct and needed no change — only the assertion's stream was wrong. (2) [HIGH] The design doc's "Default GPU preference for new/unmapped apps" section requires the `environment.d` write be "documented in the script's own output as best-effort: only affects apps that respect Mesa's DRI_PRIME convention, only takes effect on the next login/session (not already-running processes), and never affects `llm-server` itself" — Task 4's implementation emitted only `log_info "wrote ${env_dir}/60-llm-env-igpu-default.conf"`, with no caveat text at all, and no test asserted one existed. Fixed by adding a second `log_info` line with the caveat (close paraphrase of the design doc's wording) immediately after the existing "wrote" line in Task 4 Step 3, and adding four assertions to `test_gpu_status_writes_environment_d_default_on_confirmation` (Task 4 Step 1) confirming the key caveat phrases ("best-effort", "DRI_PRIME convention", "next login", "llm-server itself") appear in the script's stdout when the write happens.
