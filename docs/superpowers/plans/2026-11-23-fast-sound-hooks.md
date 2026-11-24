# Fast, Sound Hooks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Commit and push hooks run only the checks and tests the change can affect; `make test` is the only command
whose purpose is the full suite (capped, offline, leak-checked); guards prove the selection hides no failure within
its stated depth.

**Architecture:** `tools/test.sh` is the one runner for the default suite (CPU cap, `nice`, opt-ins and inherited
pytest settings dropped, probed network and PID namespaces, offline uv check, per-run `TMPDIR`, leak diff, signal
forwarding). `pylib/selection.py` maps `git diff --name-status -z --no-renames` to test files through names, the
`llmenv` subcommand edge, hubs and exemptions, and lints ownership. Commit stage: four pre-commit hooks. Push stage:
the tracked `tools/pre-push.sh`, linked by `make dev-setup`.

**Tech Stack:** bash, pytest + xdist, pre-commit 4.6.2, util-linux 2.42, stdlib `ast`/`re`/`fnmatch`, `jq`, pyyaml.

**Spec:** `docs/superpowers/specs/2026-11-23-fast-sound-hooks-design.md` (hubs, precedence and statuses as there).

## Global Constraints

- `workers = max(1, floor(nproc * LLM_ENV_TEST_CPU_PCT / 100))`, default 40; the value must match `^[1-9][0-9]{0,2}$`
  and be <= 100 (`010` is rejected), else exit 2. pytest runs under `nice -n 10`, with `-q`, never `-v`.
- `tools/test.sh` unsets `LLM_ENV_UPDATE_PROOF`, `LLM_ENV_UPDATE_CHARACTERIZE`, `LLM_ENV_LIVE_VM` (from T13 also
  `LLM_ENV_MUTATION_CHECK`) and, from T4, `PYTEST_ADDOPTS`; it sets `PYTEST_DEBUG_TEMPROOT=<run dir>/pytest`.
- Runner status: probe failure 69; offline uv check failure 4 ("uv cannot resolve llmenv.py offline: run make
  dev-setup once with network"); else pytest's own (0-5); a leak turns 0 or 5 into 1; an incomplete inventory prints
  `inventory: <what>` and exits 1; INT/TERM exit 130/143 (TERM to the chain, KILL after a 5 s grace).
- Selection depth: 1 at commit, 2 at push; ownership (G1) is measured at depth 1. Slow = call phase >= 5 s, deselected
  at commit only. Always selected: `tests/test_no_phoenix.py`. Never owners: the two opt-in updater test files. The
  push range travels as `PUSH_FROM`/`PUSH_TO` (two-point diff).
- Python only as `uv run llmenv.py <subcommand>`; selector logic only in `pylib/selection.py`.
- After a `.sh` edit `make validate`; after a `.py` edit `make validate && make test`, except that no full-suite run
  (`make test`, or `tools/test.sh` without file arguments) happens before T3's Green step: until then the free-disk fixtures
  (T2) or the offline boundary (T3) are missing. Until T1 Step 5 `tools/test.sh` ignores its arguments: use
  `uv run pytest <files> -q`, never `tests/test_omniroute_derive_candidate.py`.
- One pytest at a time (`ps -eo pid,args | rg '^ *[0-9]+ \S*(python3?|pytest)\S*( |$).*pytest'` prints nothing).
  Never run pytest or a repo script with `GIT_DIR` exported; keep the `GIT_*` scrub. Never name the big shell test
  file in a bash command: `bash tools/test.sh tests -k <keyword>`, `git add tests`.
- Every task ends Green (`bash tools/test.sh <its test files> && make validate && make test`, unless a step says
  otherwise) and with one commit of everything it changed (`git add tests` for the big shell test file), message
  `<type>(<area>): <what the task delivers>`. No `git push` between T8 and T9.
- Tests never touch the network, registries, Hugging Face, PyPI, the real Podman store, `~/.config`,
  `~/.local/state`, the repo `.env` or profile. Real Podman runs only in the opt-in characterize lane, never in the
  owner's session. Repo `tmp/` ends each task holding only `REVISAR`, `aa-key.txt` and `typesafe.txt`.

---

## Phase 1: one capped, offline, leak-checked runner

### Task 1: No real Podman or live opt-in in the default suite; CPU cap, nice, quiet output

**Files:** Modify `tools/test.sh`, `tests/test_omniroute_derive_candidate.py`, `tests/test_omniroute_characterize.py`;
Create `tests/runner_helpers.py`, `tests/test_test_runner.py`.

**Interfaces:**
- `bash tools/test.sh [pytest args...]` runs `tests/` by default.
- `tests/runner_helpers.py` (written in Step 2, before any test imports it), reused by later tasks:
  `OPT_IN_SWITCHES` (the three switches; T13 appends the fourth); `runner_repo(tmp_path, extra=()) -> Path`
  (`tmp_path/"repo"` with `tools/test.sh` and each `extra` path copied from `ROOT` with modes kept, `tmp/keep/old.log`,
  an empty `podman-state`, `stub/` with the stubs below, mode 755); `run_runner(repo, *args, env=None, prefix=(),
  podman=True) -> CompletedProcess` (runs `[*prefix, "bash", "tools/test.sh", *args]` in `repo` with
  `os.environ | env` plus `PATH=<stub>:/usr/bin:/bin:/usr/sbin`, `CALLS=<repo>/calls`, `REAL_UV`,
  `GIT_CONFIG_GLOBAL=/dev/null`, `GIT_CONFIG_NOSYSTEM=1`; `podman=False` swaps the system PATH entries for a dir of
  symlinks to every executable in them except `podman`); `start_runner(repo, env=None) -> Popen` (same, own session,
  stderr piped); `wait_until(pred, timeout=10)` (polls every 0.05 s); `calls(repo) -> list[str]` (lines of `calls`,
  `[]` when absent); `descendants(pid) -> dict[int, bytes]` (pid → cmdline of every transitive child, from the ppid
  in each `/proc/<n>/stat`, vanished entries skipped).
- Stubs: `nproc` prints `24`. `uv run pytest ...` appends its probe lines (`niceness=$(nice)`, `optin=<set switches>`;
  later tasks add lines), then last its argv, to `$CALLS`, and exits `${STUB_RC:-0}`; any other call appends its argv
  and exits 0. `podman` prints the `<class> <id>` lines of `$PODMAN_STATE` (default `<repo>/podman-state`) for
  `ps -a -q --no-trunc`, `volume ls -q` and `images -q --no-trunc`.

- [ ] **Step 1: Move the real-Podman sweep test into the opt-in lane (before any full-suite run).** Move
  `test_the_sweep_reaps_only_backend_roots_whose_owner_is_gone` unchanged (with its podman `skipif`) from
  `tests/test_omniroute_derive_candidate.py` into `tests/test_omniroute_characterize.py`, gated by
  `LLM_ENV_UPDATE_CHARACTERIZE=1`; fix imports. Verify: `env -u LLM_ENV_UPDATE_CHARACTERIZE uv run pytest
  tests/test_omniroute_characterize.py -q -rs` skips every case. Never run it with the switch set here (O2).
- [ ] **Step 2: Harness, then failing tests** (`tests/test_test_runner.py`):
```python
def test_runner_caps_workers_at_the_cpu_percentage(tmp_path):
    for pct, workers in ((None, "9"), ("10", "2"), ("1", "1"), ("100", "24")):
        run_runner(repo := runner_repo(tmp_path / str(pct)), env={} if pct is None else {"LLM_ENV_TEST_CPU_PCT": pct})
        argv = calls(repo)[-1].split()
        assert argv[argv.index("-n") + 1] == workers and "-q" in argv and "-v" not in argv and argv[-1] == "tests/"

def test_runner_rejects_a_bad_cpu_percentage(tmp_path):
    for bad in ("0", "101", "abc", "40.5", "", "010", "08", "040", "+5", "1000", "9" * 30):
        result = run_runner(repo := runner_repo(tmp_path / (bad[:8] or "empty")), env={"LLM_ENV_TEST_CPU_PCT": bad})
        assert result.returncode == 2 and "LLM_ENV_TEST_CPU_PCT" in result.stderr and calls(repo) == [], bad

def test_runner_runs_pytest_under_nice_capped_at_19_and_passes_targets(tmp_path):
    run_runner(repo := runner_repo(tmp_path / "a"), "-m", "not slow", "tests/test_x.py")
    assert f"niceness={min(19, os.nice(0) + 10)}" in calls(repo) and calls(repo)[-1].endswith("-m not slow tests/test_x.py")
    run_runner(repo := runner_repo(tmp_path / "b"), prefix=("nice", "-n", "10"))
    assert "niceness=19" in calls(repo)

def test_runner_unsets_every_opt_in_switch(tmp_path):
    run_runner(repo := runner_repo(tmp_path), env=dict.fromkeys(OPT_IN_SWITCHES, "1"))
    assert "optin=" in calls(repo)

def test_every_env_gate_in_tests_is_an_opt_in_switch_the_runner_unsets():
    gate = re.compile(r'skipif\(os\.environ\.get\("(\w+)"\)')
    assert {m for p in (ROOT / "tests").rglob("test_*.py") for m in gate.findall(p.read_text())} == set(OPT_IN_SWITCHES)
```
- [ ] **Step 3: Red.** `uv run pytest tests/test_test_runner.py -q`. Expected: 4 failed (today: `-n auto -v`, no
  unset); the gate test passes and guards later switches.
- [ ] **Step 4: Implement in `tools/test.sh`.** `unset` the switches; validate with
  `[[ $pct =~ ^[1-9][0-9]{0,2}$ ]] && ((10#$pct <= 100))`; `workers` floored at 1; `[ $# -gt 0 ] || set -- tests/`;
  `nice -n 10 uv run pytest -n "$workers" -q "$@"`.
- [ ] **Step 5: Green.** `bash tools/test.sh tests/test_test_runner.py && make validate` (no `make test` before T3).

### Task 2: Host-independent free-disk checks in every fetch fixture

**Files:** Modify `tests/skills/onboard-local-model/test_onboard_local_model_scripts.py` (`_qh_env`, `_fetch`,
`test_fetch_gguf_copies_a_local_file_and_resumes_it`, `test_fetch_gguf_needs_free_disk_only_for_what_the_part_lacks`).
**Interfaces:** module constant `DF_STUB = 'printf "Avail\\n%s\\n" "${STUB_DF_FREE:-1099511627776}"\n'`, mimicking
`df --output=avail -B1 <dir>` (`fetch-gguf.sh:64`), 1 TiB free by default.

- [ ] **Step 1: Failing test (R7's boundary)** Red: `bash tools/test.sh tests/skills/onboard-local-model/test_onboard_local_model_scripts.py -k fetch_gguf_needs_exactly`. Fails at `need - 1` (real `df`).
```python
def test_fetch_gguf_needs_exactly_size_minus_part_plus_1024_mib(tmp_path: Path) -> None:
    need = len(KNOWN) + 1024 * MIB
    for free, ok in ((need - 1, False), (need, True)):
        (sub := tmp_path / str(free)).mkdir()
        result, dest = _fetch(sub, env={"STUB_DF_FREE": str(free)})
        assert (result.returncode == 0) is ok and ("free disk" in result.stderr) is not ok, result.stderr
        assert not ok or dest.read_bytes() == KNOWN
```
- [ ] **Step 2: Implement.** Install `DF_STUB` as `df` in the stub dirs of `_qh_env` and `_fetch` and in a stub dir
  prepended to the local-file test's PATH. In the part test replace `shutil.disk_usage(...).free` with
  `free = 4 * 1024 * MIB`, passed as `STUB_DF_FREE` (sizes keep `size = free + 512 * MIB`). `QH_FILES` stays.
- [ ] **Step 3: Green** with `-k fetch_gguf` and no `make test`; the `quant_headroom` cases also need pyyaml and first run in T3.

### Task 3: Offline enforcement and pyyaml from the cache

**Files:** Modify `tools/test.sh`, `tests/conftest.py`, `tests/skills/onboard-local-model/skill_helpers.py`,
`setup/dev-setup.sh`, `AGENTS.md`, `tests/runner_helpers.py` (stubs); Create `tests/test_offline_environment.py`; Test
`tests/test_test_runner.py`.

**Interfaces:**
- Probe: `unshare -rn --pid --fork --kill-child --mount-proc bash -c 'ip link set lo up && exec unshare --user
  --map-user="$1" --map-group="$2" true' _ "$(id -u)" "$(id -g)"`; failure exits 69 ("network namespace
  unavailable"). Then the same chain runs `env UV_OFFLINE=1 bash -c 'uv run --offline llmenv.py --help >/dev/null ||
  { echo "<hint>" >&2; exit 4; }; exec nice -n 10 uv run pytest "$@"' _ <args>`: the check precedes collection.
- `skill_helpers.run(..., env=…)` fills in a missing `UV_CACHE_DIR` and `UV_OFFLINE`; `tests/conftest.py` holds
  exactly `os.environ["HF_HUB_OFFLINE"] = "1"` (T13 fault 8).
- Stub uv also records `net=<link names>`, `uid=$(id -u)`, `uv_offline=$UV_OFFLINE`; `run --offline ...` records
  `offline-check` and exits `${STUB_OFFLINE_RC:-0}`, or with `STUB_REAL_OFFLINE=1` execs `"$REAL_UV" "$@"`. Stub
  `unshare` exits 1 when `STUB_UNSHARE_FAIL` is set, else execs `/usr/bin/unshare`.

- [ ] **Step 1: Failing tests** Red: `bash tools/test.sh tests/test_test_runner.py tests/test_offline_environment.py`. 5 fail.
```python
def test_runner_has_only_loopback_keeps_the_uid_and_is_offline(tmp_path):
    assert run_runner(repo := runner_repo(tmp_path)).returncode == 0
    assert {"net=lo", f"uid={os.getuid()}", "uv_offline=1"} <= set(calls(repo))

def test_runner_exits_69_when_the_namespace_probe_fails(tmp_path):
    result = run_runner(repo := runner_repo(tmp_path), env={"STUB_UNSHARE_FAIL": "1"})
    assert result.returncode == 69 and "network namespace unavailable" in result.stderr and calls(repo) == []

@pytest.mark.parametrize("rc", [3, 5])
def test_runner_passes_the_pytest_status_through_after_a_good_probe(tmp_path, rc):
    result = run_runner(runner_repo(tmp_path), env={"STUB_RC": str(rc)})
    assert result.returncode == rc

def test_runner_exits_4_with_the_hint_when_uv_cannot_resolve_offline(tmp_path):     # real uv, cold cache
    (cold := tmp_path / "cold").mkdir()
    repo = runner_repo(tmp_path, extra=("llmenv.py",))
    result = run_runner(repo, env={"STUB_REAL_OFFLINE": "1", "UV_CACHE_DIR": str(cold)})
    assert result.returncode == 4 and "run make dev-setup once with network" in result.stderr and calls(repo) == []

@pytest.mark.parametrize("name", ["UV_OFFLINE", "HF_HUB_OFFLINE"])                  # tests/test_offline_environment.py
def test_children_inherit_offline_switches(name):
    assert os.environ[name] == "1"
```
- [ ] **Step 2: Implement** the probe, check and wrapper (`shortcut: needs unprivileged user namespaces, which Bazzite
  has; add an opt-out only for a host without them`); both offline variables at conftest import time; the
  `skill_helpers.run` fill-in (the root cause: `_qh_env` and siblings set `HOME=tmp_path`, so `uv` started cold);
  `uv run llmenv.py --help >/dev/null` after `uv sync` in `setup/dev-setup.sh`; in `AGENTS.md`, `make test` is "the
  only command whose purpose is the full suite (capped, offline, leak-checked)" and `LLM_ENV_TEST_CPU_PCT` is listed.
- [ ] **Step 3: Green**, first with `-k "runner or offline or quant_headroom"` over the two runner test files and the
  onboard-local-model scripts test (5 `add-entry` cases failed in the namespace on 2026-11-23). First `make test`.

### Task 4: Leak check, per-run TMPDIR, signal forwarding

**Files:** Modify `tools/test.sh`, `AGENTS.md` (the "pytest's own temp dir" bullet: "`tools/test.sh` gives each run its
own `TMPDIR` under `/tmp/pytest-of-$USER/` (outside the repo). Never pass `--basetemp` under `./tmp/`, and keep
`TMPDIR` outside `./tmp/`."), `tests/runner_helpers.py` (stubs, `run_dir`); Test `tests/test_test_runner.py`.

**Interfaces:**
- Stub uv for `run pytest` now runs `mkdir "$PYTEST_DEBUG_TEMPROOT/pytest-of-$USER"` (no `-p`, like pytest) and records
  `addopts=${PYTEST_ADDOPTS-unset}` and `temproot=$PYTEST_DEBUG_TEMPROOT`. New switches: `STUB_TOUCH=<path>` (uv
  creates it, parents included), `STUB_MKDIR_TMPDIR=<name>` (creates `$TMPDIR/<name>`), `STUB_NEW_PODMAN="<class>
  <id>"` (appends it to `$PODMAN_STATE`), `STUB_PODMAN_FAIL=1` (podman exits 125), `STUB_SLEEP=1` (uv starts
  `setsid sleep 300 &`, a new session like `skill_helpers.run`'s, records `pid=$$`, traps TERM to wait 1 s, record
  `term` and exit 143, then sleeps).
- Output: first stderr line `run dir: <path>`; then `leak: tmp/<relative path>`, `leak: TMPDIR/<name>` or
  `leak: podman <class> <id>` per new entry. An incomplete inventory prints one `inventory: <what>` line and exits 1:
  `inventory: podman unavailable` (a nonzero listing), `inventory: repo tmp/ unreadable` (any `find` error; only a
  missing `tmp/` is empty). No `podman` on PATH prints one "podman not found: Podman leak check skipped" line.
- Run dir `/tmp/pytest-of-$USER/llm-env-run.XXXXXX`, created, printed and given its EXIT trap first, passed into the
  namespace as `TMPDIR` with `PYTEST_DEBUG_TEMPROOT=<run dir>/pytest` (created by the runner; pytest's `tmpdir.py`
  makes only `pytest-of-<user>` inside it); `PYTEST_ADDOPTS` is unset. `run_dir(stderr) -> Path` in
  `tests/runner_helpers.py` reads the `run dir:` line, so each test checks only its own run dir.
- The chain (T3) runs under `setsid` (own group, same PID). Group TERM reaches pytest; namespace PID 1 (`uv`) waits
  for its teardown; the outer `unshare` ignores TERM (verified 2026-11-23), hence the KILL after the grace. When the
  chain ends the kernel kills what is left in its PID namespace. Signal handling, traps set before probe and launch:
```bash
stop() {                         # $1: 130 or 143. A & job in non-interactive bash ignores SIGINT, so TERM is sent
  trap '' INT TERM
  kill -TERM -- "-$child" 2>/dev/null || true
  { sleep 5; kill -KILL -- "-$child" 2>/dev/null; } & guard=$!
  wait "$child" 2>/dev/null || true; kill "$guard" 2>/dev/null || true
  exit "$1"                      # the EXIT trap removes the run dir; with no chain yet the kills are no-ops
}
child=; trap 'stop 130' INT; trap 'stop 143' TERM
# ... probe ...
setsid "${chain[@]}" & child=$!
rc=0; wait "$child" || rc=$?     # || : errexit would exit with the child's status
```

- [ ] **Step 1: Failing tests** Red: the new cases fail, earlier ones pass.
```python
@pytest.mark.parametrize(("env", "rc", "line"), [
    ({}, 0, None),                                                                   # pytest's temp root and tmp/keep/old.log are not leaks
    ({"STUB_TOUCH": "tmp/left.log"}, 1, "leak: tmp/left.log"),
    ({"STUB_TOUCH": "tmp/keep/new.log"}, 1, "leak: tmp/keep/new.log"),               # nested, under an existing dir
    ({"STUB_MKDIR_TMPDIR": "scratch"}, 1, "leak: TMPDIR/scratch"),
    ({"STUB_MKDIR_TMPDIR": "pytest-of-x"}, 1, "leak: TMPDIR/pytest-of-x"),           # no name-prefix exemption
    ({"STUB_NEW_PODMAN": "image abc"}, 1, "leak: podman image abc"),                 # same ID as the container, other class
    ({"STUB_RC": "5", "STUB_TOUCH": "tmp/x"}, 1, "leak: tmp/x"),
    ({"STUB_RC": "3", "STUB_TOUCH": "tmp/x"}, 3, "leak: tmp/x"),                     # other statuses are kept
    ({"STUB_PODMAN_FAIL": "1"}, 1, "inventory: podman unavailable"),
    ({"LOCKED": "1"}, 1, "inventory: repo tmp/ unreadable")])                        # the test makes tmp/locked mode 000
def test_a_leak_turns_0_or_5_into_1_and_keeps_other_statuses(tmp_path, env, rc, line):
    (repo := runner_repo(tmp_path)).joinpath("podman-state").write_text("container abc\n")
    (locked := repo / "tmp/locked").mkdir(mode=0 if env.get("LOCKED") else 0o700)
    try:
        result = run_runner(repo, env=env)
    finally:
        locked.chmod(0o700)                                                         # so pytest can clean tmp_path
    assert result.returncode == rc and "leak: podman container" not in result.stderr
    assert line in result.stderr if line else "leak:" not in result.stderr
    assert not run_dir(result.stderr).exists()                                       # this run's own TMPDIR is gone

def test_runner_drops_inherited_pytest_settings(tmp_path):                           # spec R10
    result = run_runner(repo := runner_repo(tmp_path), env={"PYTEST_ADDOPTS": "-k nothing", "PYTEST_DEBUG_TEMPROOT": str(tmp_path)})
    assert "addopts=unset" in calls(repo) and f"temproot={run_dir(result.stderr)}/pytest" in calls(repo)

def test_runner_skips_the_podman_diff_when_podman_is_absent(tmp_path):
    result = run_runner(runner_repo(tmp_path), podman=False)
    assert result.returncode == 0 and (result.stdout + result.stderr).count("podman not found") == 1

@pytest.mark.parametrize(("sig", "rc"), [(signal.SIGTERM, 143), (signal.SIGINT, 130)])
def test_a_signal_to_the_runner_reaps_every_descendant(tmp_path, sig, rc):
    proc = start_runner(repo := runner_repo(tmp_path), env={"STUB_SLEEP": "1"})
    wait_until(lambda: b"sleep\x00300\x00" in descendants(proc.pid).values())         # the stub's new-session child
    tree = descendants(proc.pid)
    proc.send_signal(sig)                                                           # the runner's PID only
    assert proc.wait(timeout=10) == rc and "term" in calls(repo)                     # forwarded, and waited for
    wait_until(lambda: not any(Path(f"/proc/{p}").exists() for p in tree), timeout=5)
    assert not run_dir(proc.stderr.read().decode()).exists()
```
- [ ] **Step 2: Implement in `tools/test.sh`.** `mkdir -p -m 700 "/tmp/pytest-of-$USER"`, `mktemp -d` the run dir and
  a sibling inventory dir, print the run dir, EXIT trap removing both, then the signal traps. Before and after pytest,
  snapshot `find tmp -mindepth 1` (when `tmp/` exists) and the three Podman ID sets into the inventory dir; new
  entries are `LC_ALL=C comm -13` of the sorted snapshots; every top-level entry of the run dir except exactly
  `pytest` (and an empty file named `uv-<16 hex>.lock`, which uv leaves on every run; a directory of that name still leaks) gives `leak: TMPDIR/<name>`. (`shortcut: a diff cannot tell another agent's or the live stack's new object
  from a leak; the line names it for a person to judge; switch to label-based attribution if false alarms recur`)
- [ ] **Step 3: Green.** `bash tools/test.sh tests/test_test_runner.py && make validate && make test`. Expected: pass,
  no `leak:` line from the real suite. Fix any leaking test at its root.

## Phase 2: selector and hooks

### Task 5: Selection core in `pylib/selection.py`

**Files:** Create `pylib/selection.py`, `tests/git_helpers.py`, `tests/test_selection.py`.
**Interfaces (produced):**
- `HUBS` (spec D3), `OPT_IN`, `ALWAYS`; the always-run test rejects files spelling its retired token, so `ALWAYS`
  is built by concatenation (`"tests/test_no_" + "pho" + "enix.py"`) and tests use `NP = ALWAYS[0]`.
- `PREFIX_OWNERS: dict[str, tuple[str, ...]]`, each citing the glob line in its test: `.claude/skills/onboard-local-model/challenges/`
  → `test_challenges.py`; `.claude/skills/onboard-local-model/` → `test_onboard_local_model_scripts.py`;
  `.claude/skills/system-one-scout/` → `tests/skills/test_system_one_scout.py`.
- `@dataclass(frozen=True) class Selection: full: bool; tests: tuple[str, ...]` (sorted)`; reasons: dict[str, str];
  total: int` (test files in the tree).
- `load_tree(root) -> dict[str, str]`: `git ls-files -z` to text, `""` when undecodable or missing;
  `executables(root) -> set[str]`: mode `100755` in `git ls-files -s -z`; `is_test_file(path)`: `tests/(.*/)?test_[^/]*\.py`.
- `names(text, path) -> bool`: the path, basename or dotted module, bounded by `(?<!\w)`/`(?!\w)`; for `.py` targets
  also an import line `^\s*(from|import)\b.*\b<stem>\b`.
- `read_exempt(path) -> dict[str, tuple[str, bool]]`: glob → (reason, full) from `[full: ]<glob>  # <reason>`; blank
  and `#` lines ignored; a reasonless line raises `ValueError` naming the glob.
- `owners(tree, path, depth) -> set[str]`: tests selecting `path` through names, the CLI edge (T6), conftest scope or
  `PREFIX_OWNERS`, never `ALWAYS` or `OPT_IN`. `select(tree, changed, deleted, stage, exempt) -> Selection`: depth 1
  at `"commit"`, 2 at `"push"`; per path spec D3's precedence, results unioned.
- `tests/git_helpers.py`: `git_repo(root, files: dict[str, bytes], commit=False) -> Path` (`git init -q -b main`,
  local user, files, `git add -A`, optional commit), `git(repo, *args)`, `git_out(repo, *args) -> str` (`check=True`,
  trailing newline stripped), all with the scrubbed environment plus the two git-config isolation variables.

- [ ] **Step 1: Failing tests** (write `tests/git_helpers.py` first, as in Interfaces) Red: `ModuleNotFoundError: pylib.selection`.
```python
NP = ALWAYS[0]
TREE = {"scripts/a.sh": 'bash "$R/scripts/b.sh"\n', "scripts/b.sh": "", "scripts/c.sh": "bash scripts/a.sh\n",
        "scripts/orphan.sh": "", "pylib/m.py": "X = 1\n", "pylib/n.py": "from pylib.m import X\n",
        "docs/guide.md": "see scripts/b.sh\n", "docs/notes/old.md": "", "tools/lib.sh": "", "tests/conftest.py": "",
        "tests/test_a.py": "S = ROOT / 'scripts' / 'a.sh'\n", "tests/test_b.py": "'b.sh'\n", "tests/test_c.py": "'scripts/c.sh'\n",
        "tests/test_n.py": "from pylib import other, n\n", "tests/test_guide.py": "'docs/guide.md'\n",
        "tests/test_lib.py": "'tools/lib.sh'\n", "tests/test_env.py": "'tests/conftest.py'\n",
        "tests/sub/conftest.py": "", "tests/sub/test_s.py": "",
        NP: "", "tests/test_omniroute_update_proof.py": "'scripts/orphan.sh'\n"}
S = lambda ch, st, ex=None, de=(): select(TREE, ch, de, st, ex or {})

def test_depth_one_at_commit_and_two_at_push():
    assert S(["scripts/b.sh"], "commit").tests == ("tests/test_a.py", "tests/test_b.py", NP)   # markdown is never a referrer
    assert S(["scripts/b.sh"], "push").tests == ("tests/test_a.py", "tests/test_b.py", "tests/test_c.py", NP)

def test_an_owned_file_never_falls_back_so_reach_beyond_push_depth_is_missed():    # spec D1 fixture
    chain = {"x/a.sh": "", "x/b.sh": "a.sh\n", "x/c.sh": "b.sh\n", "x/e.sh": "c.sh\n",
             "tests/test_a.py": "'a.sh'\n", "tests/test_d.py": "'c.sh'\n", "tests/test_e.py": "'e.sh'\n", NP: ""}
    sel = select(chain, ["x/a.sh"], [], "push", {})
    assert not sel.full and sel.tests == ("tests/test_a.py", "tests/test_d.py", NP)

def test_imports_markdown_and_changed_tests():
    assert "tests/test_n.py" in S(["pylib/m.py"], "commit").tests
    assert S(["docs/guide.md"], "push").tests == ("tests/test_guide.py", NP)
    assert S(["tests/test_c.py"], "commit").tests == ("tests/test_c.py", NP)

def test_a_test_helper_change_selects_the_tests_that_import_it():                   # tests/test_capacity.py: from test_cli import ...
    assert "tests/test_capacity.py" in select(load_tree(ROOT), ["tests/test_cli.py"], [], "commit", {}).tests

def test_precedence_of_full_hubs_and_conftests():                                   # spec D3 rules 1-3
    assert S(["scripts/b.sh"], "commit", {"scripts/b.sh": ("no runner", True)}).full                 # owned full: file
    assert S(["tests/conftest.py"], "push").full
    assert S(["tests/conftest.py"], "commit").tests == ("tests/test_env.py", NP)                     # not every test
    assert S(["tools/lib.sh"], "commit").tests == ("tests/test_lib.py", NP)
    assert S(["tests/sub/conftest.py"], "commit").tests == ("tests/sub/test_s.py", NP)

def test_unowned_exempt_and_opt_in():
    assert S(["scripts/orphan.sh"], "commit").full                                   # only the opt-in proof names it
    assert S(["docs/notes/old.md"], "push", {"docs/notes/*": ("history", False)}).tests == (NP,)
    assert S(["docs/guide.md"], "push", {"docs/*": ("history", False)}).tests == ("tests/test_guide.py", NP)

def test_deleted_paths_select_like_changed_ones():
    tree = {k: v for k, v in TREE.items() if k != "scripts/b.sh"}
    assert select(tree, [], ["scripts/b.sh"], "commit", {}).tests == ("tests/test_a.py", "tests/test_b.py", NP)

def test_load_tree_reads_binary_files_as_empty(tmp_path):
    git_repo(tmp_path, {"f.gz": gzip.compress(b"x"), "bad.txt": b"\xff\xfe", "ok.sh": b"echo\n"})
    assert load_tree(tmp_path) == {"f.gz": "", "bad.txt": "", "ok.sh": "echo\n"}

def test_read_exempt_parses_full_lines_and_requires_a_reason(tmp_path):
    (tmp_path / "x").write_text("# header\ndocs/a.md  # why\nfull: x/*.mjs  # no runner\n")
    assert read_exempt(tmp_path / "x") == {"docs/a.md": ("why", False), "x/*.mjs": ("no runner", True)}
    (tmp_path / "y").write_text("docs/b.md\n")
    with pytest.raises(ValueError, match="docs/b.md"):
        read_exempt(tmp_path / "y")
```
- [ ] **Step 2: Implement.** `reach()` walks breadth-first up to `depth` hops over non-hub, non-markdown files (test
  helpers included) that name a reached file; a changed markdown file reaches only itself. Each path takes its first
  matching precedence rule; only rule 4 uses naming (tests naming anything reached, changed test files, tests under a
  reached non-root `conftest.py`, `PREFIX_OWNERS`). `ALWAYS` once, results unioned, globs via `fnmatch.fnmatchcase`.

### Task 6: The `llmenv` subcommand edge

**Files:** Modify `pylib/selection.py`; Test `tests/test_selection.py`.
**Interfaces:**
- Produces `cli_edges(tree: dict[str, str]) -> dict[str, frozenset[str]]`: pylib path → subcommand words that use it;
  `frozenset({"*"})` (a hub) when `llmenv.py` imports the module but no subcommand traces to it.
- `reach()` calls `cli_edges(tree)` exactly once, through that literal (T13's selector-only copy replaces it). For a
  reached module it adds every file with a line containing `llmenv` and one of its words, bounded by
  `(?<![A-Za-z0-9-])`/`(?![A-Za-z0-9-])`.

- [ ] **Step 1: Failing tests** Red: 2 failed.
```python
CLI = {"llmenv.py": "from pylib.m import compute\nfrom pylib.q import other\nfrom pylib.r import register as reg_r\n\n"
                    "def _helper():\n    return compute()\n\ndef cmd_budget(args):\n    return _helper()\n\n"
                    "def cmd_render_compose(args):\n    return 0\n\n"
                    "def cmd_capacity(args):\n    from pylib import cap, cap_report as cr\n    return cap.save(cr.page())\n\n"
                    "def cmd_interference(args):\n    from pylib.inter import run\n    return run()\n",
       "pylib/m.py": "def compute(): ...\n", "pylib/q.py": "def other(): ...\n", "pylib/cap.py": "", "pylib/cap_report.py": "",
       "pylib/inter.py": "", "pylib/r.py": 'def register(sub):\n    sub.add_parser("omniroute-update")\n',
       "scripts/start.sh": 'out="$(llmenv --config "$c" budget --models-dir "$d")"\n',
       "scripts/pre.sh": 'llmenv_budget_json "$cfg" "$dir"\n', "scripts/other.sh": "llmenv render-compose\n",
       "tests/test_start.py": "'start.sh'\n", "tests/test_pre.py": "'pre.sh'\n", "tests/test_other.py": "'other.sh'\n",
       NP: ""}

def test_cli_edges_trace_every_llmenv_import_form():    # top-level, function-local, `from pylib import a, b as c`, aliases
    assert cli_edges(CLI) == {"pylib/m.py": frozenset({"budget"}), "pylib/q.py": frozenset({"*"}),
                              "pylib/r.py": frozenset({"omniroute-update"}), "pylib/cap.py": frozenset({"capacity"}),
                              "pylib/cap_report.py": frozenset({"capacity"}), "pylib/inter.py": frozenset({"interference"})}

def test_cli_edge_selects_script_tests_through_the_subcommand():
    assert select(CLI, ["pylib/m.py"], [], "commit", {}).tests == tuple(sorted((NP, "tests/test_pre.py", "tests/test_start.py")))
    assert select(CLI, ["pylib/q.py"], [], "push", {}).full
```
- [ ] **Step 2: Implement `cli_edges`.** Parse `llmenv.py` with `ast`; map each name bound by an `ImportFrom` (top
  level or function-local, honouring `as`) to its module; collect `Name` ids per top-level function, closed over calls
  to other `llmenv.py` functions to a fixpoint; `cmd_<x>` gives `x.replace("_", "-")`; add each module's
  `add_parser("<w>")` literals.

### Task 7: `llmenv select-tests`, reading every change from git

**Files:** Modify `llmenv.py`, `pylib/selection.py`; Test `tests/test_selection.py`.
**Interfaces:**
- `git_changes(root, stage, env) -> tuple[list[str], list[str]]` (changed, deleted) from `git diff --name-status -z
  --no-renames`, `--cached` at commit, `"$PUSH_FROM" "$PUSH_TO"` at push (missing: `ValueError`), split on NUL into
  (status, path) pairs, `D` = deleted. git keeps the caller's environment (the hook's `GIT_INDEX_FILE` during
  `git commit -a`).
- `cmd_select_tests(args) -> int` for `uv run llmenv.py select-tests --stage {commit,push}`: root from `git rev-parse
  --show-toplevel`, exemptions from `tests/selection-exempt.txt` (absent: `{}`), `SELECT_ALL=1` forces full. stdout:
  JSON `{"full", "tests", "total", "reasons"}`; stderr: `selected N of M test files` (or `selected all M test files
  (<reason>)`), then `  <path>: <reason>` per changed path; a push without its range exits 2 with "push stage needs
  PUSH_FROM and PUSH_TO".

- [ ] **Step 1: Failing tests** (`_cli(repo, *args, env=None)` runs `uv run <ROOT>/llmenv.py *args` in `repo`;
  `select_tests(repo, *args, env=None) -> (json, stderr)` wraps it). Red: 7 failed (`invalid choice: 'select-tests'`).
```python
ODD = "scripts/my file ñ.sh"                                                         # git quotes it without -z
BASE = {"scripts/c.sh": b"", ODD: b"", "tests/test_c.py": b"'c.sh'\n", "tests/test_d.py": b"'d.sh'\n",
        "tests/test_odd.py": "'my file ñ.sh'\n".encode(), NP: b""}

@pytest.mark.parametrize("change", ["edit", "rm", "mv", "rm odd"])
def test_select_tests_commit_reads_every_staged_change_from_git(tmp_path, change):
    repo = git_repo(tmp_path, BASE, commit=True)
    if change == "edit":
        (repo / "scripts/c.sh").write_text("x\n"); git(repo, "add", "scripts/c.sh")
    else:
        git(repo, *{"rm": ("rm", "-q", "scripts/c.sh"), "mv": ("mv", "scripts/c.sh", "scripts/d.sh"),
                    "rm odd": ("rm", "-q", ODD)}[change])
    want = {NP, "tests/test_odd.py"} if change == "rm odd" else {"tests/test_c.py", NP} | ({"tests/test_d.py"} if change == "mv" else set())
    out, err = select_tests(repo, "--stage", "commit")
    assert set(out["tests"]) == want and err.startswith(f"selected {len(want)} of 4 test files")

def test_select_tests_push_diffs_the_range(tmp_path):
    repo = git_repo(tmp_path, BASE, commit=True); base = git_out(repo, "rev-parse", "HEAD")
    git(repo, "rm", "-q", "scripts/c.sh"); git(repo, "commit", "-qm", "rm")
    out, _ = select_tests(repo, "--stage", "push", env={"PUSH_FROM": base, "PUSH_TO": git_out(repo, "rev-parse", "HEAD")})
    assert out["tests"] == ["tests/test_c.py", NP] and not out["full"]

def test_select_tests_takes_no_paths_and_needs_the_push_range(tmp_path):
    repo = git_repo(tmp_path, BASE, commit=True)
    assert _cli(repo, "select-tests", "--stage", "commit", "scripts/c.sh").returncode == 2
    result = _cli(repo, "select-tests", "--stage", "push")
    assert result.returncode == 2 and "push stage needs PUSH_FROM and PUSH_TO" in result.stderr

def test_select_all_forces_full(tmp_path):
    assert select_tests(git_repo(tmp_path, BASE, commit=True), "--stage", "commit", env={"SELECT_ALL": "1"})[0]["full"]
```
- [ ] **Step 2: Implement** both as in Interfaces: an argparse subparser (an extra argument exits 2) calling
  `select(load_tree(root), *git_changes(root, stage, os.environ), stage, exempt)`; the range `ValueError` exits 2.

### Task 8: `tools/pre-push.sh`, linked by `make dev-setup`

**Files:** Create `tools/pre-push.sh` (mode 100755), `tests/test_hooks.py`; Modify `setup/dev-setup.sh`,
`.pre-commit-config.yaml` (delete the `test` pre-push entry), `AGENTS.md`, the big shell test file (its
`test_dev_setup_runs_uv_sync` moves out, Step 1).

**Interfaces:**
- Produces `tools/pre-push.sh <remote> <url>`, reading git's ref lines (`<local ref> <local sha> <remote ref> <remote
  sha>`) on stdin. Root is `git rev-parse --show-toplevel` of the cwd. In order:
  1. No line, or one deletion line (local sha all zeros): exit 0.
  2. Refuse unless there is exactly one line, its local ref is `b=$(git symbolic-ref -q HEAD)` or the word `HEAD`
     (final review: `git push origin HEAD` sends it), its remote ref is also `$b`, its local sha is
     `git rev-parse HEAD`, `git diff --cached --quiet` and `git diff --quiet` both succeed, and its remote sha is all
     zeros or `git merge-base --is-ancestor <remote sha> HEAD` succeeds. A refusal is exit 1 before any check, with
     exactly `push refused: the index or worktree is dirty: commit or stash your changes, then push again` when one of
     the two `git diff` checks fails (final review: `make test` tests the worktree, not the pushed commit, so no
     `--no-verify` advice), else `push shape not supported by the hook: run make test, then git push --no-verify`.
  3. `bash "$root/tools/validate.sh"`, then for a fast-forward `PUSH_FROM=<remote sha> PUSH_TO=<HEAD sha> bash
     "$root/tools/test-changed.sh" --stage push`, and for a new branch (remote sha all zeros) the full suite,
     `bash "$root/tools/test.sh"`; the first failure stops it.
- `setup/dev-setup.sh` first exits 1 with "core.hooksPath is set: unsupported; unset it, then rerun make dev-setup"
  when `git config core.hooksPath` prints anything, before touching any hook; then `ln -sfn` links
  `$(git rev-parse --git-path hooks/pre-push)` to `$(readlink -f "$(git rev-parse --path-format=absolute
  --git-common-dir)/../tools/pre-push.sh")` (T9 checks the same two expressions) and runs `pre-commit install
  --hook-type pre-commit` when `pre-commit` is on PATH, else logs "install pre-commit: uv tool install pre-commit".
  `AGENTS.md`'s one-time line becomes `uv tool install pre-commit && make dev-setup`.

- [ ] **Step 1: Move the dev-setup test into a scratch repo, before the script learns to link hooks.**
  `test_dev_setup_runs_uv_sync` runs the script with `cwd=ROOT`, so a linking script would rewrite the real
  `.git/hooks/pre-push` during `make test`. Delete it from the big shell test file and add to `tests/test_hooks.py`
  `_run_tool(repo, script, env=None)` (`bash <script>` in `repo` with `run_runner`'s environment) and:
```python
def test_dev_setup_runs_uv_sync_in_a_scratch_repo(tmp_path):
    git_repo(repo := runner_repo(tmp_path, extra=("setup/dev-setup.sh", "tools/lib.sh")), {})
    assert _run_tool(repo, "setup/dev-setup.sh").returncode == 0 and "sync" in calls(repo)
```
  `bash tools/test.sh tests -k dev_setup`. Expected: pass (the script is unchanged); no test runs it at `ROOT` again.
- [ ] **Step 2: Failing tests.** Fixture `_push_repo(tmp_path, shape=None)`: `runner_repo(extra=("tools/pre-push.sh",
  "setup/dev-setup.sh", "tools/lib.sh"))` (modes kept) made a repo the way `git_repo` does (`stub/`, `podman-state`,
  `calls`, `tmp/` in `.git/info/exclude`), with stub `tools/validate.sh` / `tools/test-changed.sh` / `tools/test.sh`
  that append `validate` / `changed <args> from=$PUSH_FROM to=$PUSH_TO` / `full` to `calls` (validate exits
  `${STUB_VALIDATE_RC:-0}`) and a stub `pre-commit` that records its argv; commits `B` (with `README.md`) then `H` on
  checked-out `main`; an empty bare remote `tmp_path/remote.git` added as `origin`; branch `other` with commit `O`;
  branch `ahead` with commit `D` on top of `H`; annotated tag `v1` at `H`. Shape setup: "staged edit" stages a
  `README.md` edit, "unstaged edit" leaves one unstaged, "cancelled edit" stages one and restores the worktree file.
  `sha(repo, name)` resolves those names. `_push(repo, *lines, env=None)` expands `main`/`other` to `refs/heads/…`,
  `tags/v1` to `refs/tags/v1`, letters to shas and `Z` to `"0" * 40`, and runs `bash tools/pre-push.sh origin <remote
  path>` with the lines on stdin. `_git_push(repo, *args)` runs a real `git push -q origin *args` likewise.
```python
REFUSED = "push shape not supported by the hook: run make test, then git push --no-verify"
DIRTY = "push refused: the index or worktree is dirty: commit or stash your changes, then push again"  # final review
SHAPES = {"two refs": ["main H main B", "other O other Z"], "tag destination": ["main H tags/v1 Z"],
          "other destination": ["main H other Z"], "rewind": ["main H main D"], "other branch": ["other O other Z"],
          "tag": ["tags/v1 H tags/v1 Z"], "staged edit": ["main H main B"], "unstaged edit": ["main H main B"],
          "cancelled edit": ["main H main B"]}

@pytest.mark.parametrize("shape", SHAPES)
def test_pre_push_refuses_every_other_shape_before_any_check(tmp_path, shape):
    result = _push(repo := _push_repo(tmp_path, shape=shape), *SHAPES[shape])
    assert result.returncode == 1 and result.stderr.strip() == REFUSED and calls(repo) == []

def test_pre_push_selects_a_fast_forward_and_runs_everything_for_a_new_branch(tmp_path):
    repo = _push_repo(tmp_path)
    assert _push(repo, "main H main B").returncode == 0 and calls(repo) == ["validate", f"changed --stage push from={sha(repo, 'B')} to={sha(repo, 'H')}"]
    (repo / "calls").write_text("")
    assert _push(repo, "main H main Z").returncode == 0 and calls(repo) == ["validate", "full"]

def test_pre_push_passes_a_lone_deletion_and_stops_at_a_failed_validate(tmp_path):
    repo = _push_repo(tmp_path)
    assert _push(repo, f"(delete) {Z} refs/heads/gone {sha(repo, 'B')}").returncode == 0 and calls(repo) == []
    assert _push(repo, "main H main B", env={"STUB_VALIDATE_RC": "1"}).returncode == 1 and calls(repo) == ["validate"]

def test_git_runs_the_linked_hook_only_while_it_is_executable(tmp_path):             # a real push
    assert "tools/pre-push.sh" in executables(ROOT)                                  # tracked as 100755
    repo = _push_repo(tmp_path); (repo / ".git/hooks/pre-push").symlink_to(repo / "tools/pre-push.sh")
    assert _git_push(repo, "main").returncode == 0 and calls(repo)[0] == "validate"
    (repo / "calls").write_text(""); (repo / "tools/pre-push.sh").chmod(0o644)
    git(repo, "commit", "-q", "--allow-empty", "-m", "N")
    assert _git_push(repo, "main").returncode == 0 and calls(repo) == []              # git skips a non-executable hook

@pytest.mark.parametrize("hooks_path", [None, "elsewhere"])
def test_dev_setup_links_pre_push_unless_core_hooks_path_is_set(tmp_path, hooks_path):
    repo = _push_repo(tmp_path); hook = repo / ".git/hooks/pre-push"; hook.write_text("old\n")
    if hooks_path:
        git(repo, "config", "core.hooksPath", hooks_path)
    result = _run_tool(repo, "setup/dev-setup.sh")
    if hooks_path:
        assert result.returncode == 1 and "core.hooksPath" in result.stderr and hook.read_text() == "old\n"
    else:
        assert result.returncode == 0 and hook.resolve() == (repo / "tools/pre-push.sh").resolve()
        assert "install --hook-type pre-commit" in calls(repo)
```
- [ ] **Step 3: Red.** `bash tools/test.sh tests/test_hooks.py`. Expected: every new test fails; the moved test passes.
- [ ] **Step 4: Implement** `tools/pre-push.sh` as in Interfaces, then `chmod 755 tools/pre-push.sh && git add
  --chmod=+x tools/pre-push.sh`; the `setup/dev-setup.sh` refusal and link, the config deletion and the `AGENTS.md` line.
- [ ] **Step 5: Green.** `bash tools/test.sh tests/test_hooks.py && make validate && make test`, then `make dev-setup`
  and `readlink -f .git/hooks/pre-push`. Expected: the path of `tools/pre-push.sh`.

### Task 9: Commit hooks: staged checks, index-reading pyright, selected tests

**Files:** Create `tools/test-changed.sh`, `tools/pyright-staged.sh`; Modify `.pre-commit-config.yaml`, `AGENTS.md`,
`tests/runner_helpers.py` (the `select-tests` stub case), and the docstrings only of `tests/conftest.py` and
`tests/test_git_environment.py`; Test `tests/test_hooks.py`.

**Interfaces:**
- Produces `bash tools/test-changed.sh --stage commit|push`:
  1. Commit only: unless `h=$(git rev-parse --git-path hooks/pre-push)` passes `[ -x "$h" ]` (git skips a
     non-executable hook) and `readlink -f "$h"` equals the T8 target, exit 1 with "pre-push hook is not an
     executable link to tools/pre-push.sh: run make dev-setup".
  2. `uv run llmenv.py select-tests --stage <stage>` (push range from the environment).
  3. `bash tools/test.sh`, with `-m "not slow"` at commit (a full selection too) and `tests/` for a full selection
     (`mapfile -t tests < <(jq -r '.tests[]' <<<"$json")`). Every status passes through, 5 included: the always-run
     test is in every selection, so "nothing collected" means something dropped every test, and the hook fails.
- Produces `bash tools/pyright-staged.sh`: `uv run pyright` only when a path read from
  `git diff --staged --name-only -z --no-renames` (`while IFS= read -r -d '' p`) matches
  `(\.py|^pyproject\.toml|^uv\.lock)$`, else exit 0.
- Stub uv answers `run llmenv.py select-tests ...` with `$STUB_SELECT` (default
  `{"full": false, "tests": ["tests/test_a.py"], "total": 1, "reasons": {}}`).
- Helpers: `_hook_repo(tmp_path, files)` = `runner_repo(extra=("tools/test-changed.sh", "tools/pyright-staged.sh",
  "tools/pre-push.sh"))` plus `files`, committed on an empty parent commit, `.git/hooks/pre-push` linked, the harness
  files in `.git/info/exclude`; `_refs(repo)` = `{"PUSH_FROM": <HEAD~1>, "PUSH_TO": <HEAD>}`; `_run_changed(repo,
  stage, select=None, env=None)` runs `tools/test-changed.sh --stage <stage>` like `_run_tool`, `STUB_SELECT` = `select`.

- [ ] **Step 1: Failing tests** (first add the stub case and the three helpers above) Red: `bash tools/test.sh tests/test_hooks.py -k "not pre_push and not dev_setup and not linked_hook"`. All fail.
```python
CFG = yaml.safe_load((ROOT / ".pre-commit-config.yaml").read_text())["repos"][0]["hooks"]

def test_pre_commit_holds_only_commit_stage_hooks_and_the_index_readers_always_run():
    assert [h["id"] for h in CFG] == ["shellcheck", "ruff", "pyright", "tests-changed"]
    assert all(h["stages"] == ["pre-commit"] for h in CFG) and not any("tools/test.sh" in h["entry"] for h in CFG)
    assert all(h["always_run"] and h["pass_filenames"] is False for h in CFG[2:])

def test_shellcheck_and_ruff_cover_exactly_what_validate_covers():
    globs = ("tools/*.sh", "setup/*.sh", "setup/lib/*.sh", "scripts/*.sh", ".claude/skills/*/scripts/*.sh")
    sc, rf = (re.compile(next(h["files"] for h in CFG if h["id"] == i)) for i in ("shellcheck", "ruff"))
    assert rf.search("pylib/x.py") and not rf.search("scripts/system-one-cache-check.py")   # D7
    for p in load_tree(ROOT):
        assert bool(sc.search(p)) == any(fnmatch.fnmatchcase(p, g) and p.count("/") == g.count("/") for g in globs), p
        assert bool(rf.search(p)) == (p == "llmenv.py" or p.startswith(("pylib/", "tests/")) and p.endswith(".py")), p

@pytest.mark.parametrize("path", ["pylib/a ñ b.py", "pyproject.toml", "uv.lock"])        # git quotes the first without -z
@pytest.mark.parametrize("how", ["rm", "mv"])
def test_pyright_runs_on_a_staged_deletion_or_rename_away(tmp_path, path, how):     # D7
    repo = _hook_repo(tmp_path, {path: b"x\n", "README.md": b"r\n"})
    git(repo, *(("rm", "-q", path) if how == "rm" else ("mv", path, path + ".moved")))
    assert _run_tool(repo, "tools/pyright-staged.sh").returncode == 0 and calls(repo)[-1] == "run pyright"

def test_pyright_skips_a_commit_without_python_inputs(tmp_path):
    git(repo := _hook_repo(tmp_path, {"pylib/a.py": b"x\n", "README.md": b"r\n"}), "rm", "-q", "README.md")
    assert _run_tool(repo, "tools/pyright-staged.sh").returncode == 0 and calls(repo) == []

def test_test_changed_deselects_slow_only_at_commit(tmp_path):
    repo, full = _hook_repo(tmp_path, {}), {"full": True, "tests": [], "total": 2, "reasons": {}}
    _run_changed(repo, "commit"); _run_changed(repo, "commit", select=full); _run_changed(repo, "push", select=full, env=_refs(repo))
    runs = [c for c in calls(repo) if " pytest " in c]
    assert runs[0].endswith("-m not slow tests/test_a.py") and runs[1].endswith("-m not slow tests/")
    assert runs[2].endswith("-q tests/") and "not slow" not in runs[2]

def test_test_changed_fails_when_nothing_is_collected(tmp_path):                    # spec R10
    assert _run_changed(_hook_repo(tmp_path, {}), "commit", env={"STUB_RC": "5"}).returncode == 5

@pytest.mark.parametrize("broken", ["missing", "not executable"])
def test_commit_hook_requires_the_pre_push_link(tmp_path, broken):
    repo = _hook_repo(tmp_path, {})
    (repo / ".git/hooks/pre-push").unlink() if broken == "missing" else (repo / "tools/pre-push.sh").chmod(0o644)
    result = _run_changed(repo, "commit")
    assert result.returncode == 1 and "run make dev-setup" in result.stderr and calls(repo) == []
```
- [ ] **Step 2: Write the exact config** (`.pre-commit-config.yaml`)
```yaml
repos:
  - repo: local
    hooks:
      - {id: shellcheck, name: "shellcheck (staged)", entry: shellcheck -s bash, language: system, stages: [pre-commit],
         files: '^(tools|setup|setup/lib|scripts|\.claude/skills/[^/]+/scripts)/[^/]+\.sh$'}
      - {id: ruff, name: "ruff (staged)", entry: uv run ruff check, language: system, stages: [pre-commit],
         files: '^(llmenv\.py|pylib/.*\.py|tests/.*\.py)$'}
      - {id: pyright, name: "pyright (project, when the index touches Python inputs)", entry: bash tools/pyright-staged.sh,
         language: system, pass_filenames: false, always_run: true, stages: [pre-commit]}
      - {id: tests-changed, name: "pytest (selected, commit)", entry: bash tools/test-changed.sh --stage commit,
         language: system, pass_filenames: false, always_run: true, require_serial: true, stages: [pre-commit]}
```
  Then the two scripts, straight transcriptions of their Interfaces steps (`set -euo pipefail`). Both docstrings: "the
  hooks run selected tests; `make test` runs the whole suite". `AGENTS.md` Hooks paragraph: what each stage runs, the
  push shapes and the refusal line, the bypasses and `SELECT_ALL=1`. (`shortcut: a staged pyproject.toml reruns
  pyright but not ruff at commit; the push-stage validate.sh reruns ruff over everything`)
- [ ] **Step 3: Green** after `pre-commit validate-config .pre-commit-config.yaml`; the commit runs the new hooks.

### Task 10: `slow` marker

**Files:** Modify `pyproject.toml` (`markers`) and the test files the measurement names; Test `tests/test_hooks.py`.

- [ ] **Step 1: Failing test**
```python
def test_slow_marker_is_registered_and_the_known_32s_test_carries_it():
    assert any(m.startswith("slow:") for m in tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["pytest"]["ini_options"]["markers"])
    fn = next(n for n in ast.walk(ast.parse((ROOT / "tests/test_gguf.py").read_text()))
              if isinstance(n, ast.FunctionDef) and n.name == "test_large_string_array_is_summarized_not_materialized")
    assert "pytest.mark.slow" in {ast.unparse(d) for d in fn.decorator_list}
```
- [ ] **Step 2: Red** (`-k slow`), then `bash tools/test.sh --durations=0 tests/ > tmp/durations.txt`.
- [ ] **Step 3: Implement.** Mark every function with a `call` line of 5.00 s or more (the whole function if any case
  qualifies); register `slow: call phase >= 5 s at the capped worker count; deselected at commit only`. Green, `rm
  tmp/durations.txt`; the commit body lists the marked tests and seconds.

## Phase 3: soundness guards

### Task 11: Ownership lint (G1) in both hooks, `tests/selection-exempt.txt`, and the R9 timings

**Files:** Create `tests/selection-exempt.txt`; Modify `pylib/selection.py` (`lint`, more `PREFIX_OWNERS`),
`llmenv.py` (`selection-lint`), `tools/test-changed.sh`, `tests/runner_helpers.py`, `tests/test_selection.py`,
`tests/test_hooks.py`, this plan (the R9 record).

**Interfaces:**
- `lint(tree, exempt, execs) -> list[str]`, sorted: `unowned: <path>` (not a test file, hub or opt-in, no exemption,
  `owners(tree, p, 1)` empty: commit depth, spec G1), `stale exemption: <glob>` (matches nothing), `plain exemption
  matches code: <glob> (<path>)` (a non-`full:` glob matching a `.sh`/`.py`/`.mjs`/`.js` file or a path in `execs`).
- `uv run llmenv.py selection-lint`: `select-tests`' root, tree and exemptions; problems on stderr and exit 1 (a
  `read_exempt` `ValueError` is one); else `ownership lint: ok (<n> files)`.
- `tools/test-changed.sh` runs it after the link check and before `select-tests`, at both stages; a failure exits 1.
  Stub uv answers it with `exit ${STUB_LINT_RC:-0}`, or `exec "$REAL_UV" run "$STUB_REAL_LINT" selection-lint` when set.

- [ ] **Step 1: Failing tests**
```python
def test_every_tracked_file_has_an_owner_a_hub_or_an_exemption():                  # tests/test_selection.py
    assert lint(load_tree(ROOT), read_exempt(ROOT / "tests/selection-exempt.txt"), executables(ROOT)) == []

def test_lint_flags_a_lost_last_match_a_lost_last_owner_and_code_under_a_plain_line():
    tree = {"docs/a.md": "", "scripts/x.sh": "", "tests/test_x.py": "'x.sh'\n", "x/t.mjs": "", NP: ""}
    ex = {"docs/a.md": ("why", False), "x/*.mjs": ("no runner", True)}
    assert lint(tree, ex, set()) == []
    assert lint({k: v for k, v in tree.items() if k != "docs/a.md"}, ex, set()) == ["stale exemption: docs/a.md"]
    assert lint({k: v for k, v in tree.items() if k != "tests/test_x.py"}, ex, set()) == ["unowned: scripts/x.sh"]
    assert lint(tree, {**ex, "x/*.mjs": ("no runner", False)}, set()) == ["plain exemption matches code: x/*.mjs (x/t.mjs)"]
    chain = {"s/far.sh": "", "s/mid.sh": "far.sh\n", "s/top.sh": "mid.sh\n", "tests/test_top.py": "'top.sh'\n", NP: ""}
    assert lint(chain, {}, set()) == ["unowned: s/far.sh"]                          # an owner only at push depth is not enough

@pytest.mark.parametrize("stage", ["commit", "push"])                                # tests/test_hooks.py
@pytest.mark.parametrize(("removed", "problem"), [("docs/a.md", "stale exemption: docs/a.md"),
                                                  ("tests/test_x.py", "unowned: scripts/x.sh")])
def test_hooks_fail_when_an_exemption_loses_its_last_match_or_a_source_its_last_owner(tmp_path, stage, removed, problem):
    repo = _hook_repo(tmp_path, {"docs/a.md": b"", "scripts/x.sh": b"", "tests/test_x.py": b"'x.sh'\n", NP: b"",
                                 "tests/test_tools.py": b"'pyright-staged.sh'\n",          # owns the copied tool
                                 "tests/selection-exempt.txt": b"docs/a.md  # why\n"})
    real = {"STUB_REAL_LINT": str(ROOT / "llmenv.py")}
    assert _run_changed(repo, "commit", env=real).returncode == 0                  # control: clean fixture ...
    assert any(" pytest " in c for c in calls(repo))                               # ... that reached pytest
    (repo / "calls").write_text("")                                                # measure only the rejected run
    git(repo, "rm", "-q", removed)
    if stage == "push":
        git(repo, "commit", "-qm", "rm")
    result = _run_changed(repo, stage, env={**real, **(_refs(repo) if stage == "push" else {})})
    assert result.returncode == 1 and problem in result.stderr and not any(" pytest " in c for c in calls(repo))
```
- [ ] **Step 2: Red.** `bash tools/test.sh tests/test_selection.py tests/test_hooks.py -k "owner or lint or exemption"`.
  `lint` undefined. Then the real-tree test lists the unowned files (2026-11-23: the challenge and eval trees, test
  helpers, `tests/fixtures/llama-models-*.json`, the `.test.mjs` files, `docs/prds/**`, `docs/superpowers/**`,
  `.claude/handoffs/**`, `rdna*.log`, plus whatever is owned only two hops away).
- [ ] **Step 3: Implement, then give each an owner:** a glob or f-string reader gets a `PREFIX_OWNERS` entry citing
  its line, or a direct test; non-executable content read by no test a plain line (`docs/superpowers/*  # durable
  plans and specs`); code no pytest test runs a `full:` line (the `.test.mjs`-covered `context-ladder.mjs`, O1).
  (`shortcut: ownership is per test file, so a source named only by an opt-in test inside a mixed file counts as owned`)
- [ ] **Step 4: Green**, then `time uv run llmenv.py selection-lint` into the commit body (paid on every hook run).
- [ ] **Step 5: Time R9 through the installed hooks.** One bash script under `tmp/` (deleted afterwards) holds setup,
  the three timed commands and teardown, so the trap fires when it ends:
```bash
set -euo pipefail
root="$PWD"; name="hook-timing-$(date +%s)-$RANDOM"; wt=".claude/worktrees/$name"; made=()
cleanup() { cd "$root"; for ((i = ${#made[@]} - 1; i >= 0; i--)); do case ${made[i]} in   # only what this run made
  worktree) git worktree remove --force "$wt" ;; branch) git branch -D "$name" ;;
  remote) git remote remove "$name" ;; dir) rm -rf "$d" ;; esac; done; }
trap cleanup EXIT
if git remote get-url "$name" >/dev/null 2>&1 || git rev-parse -q --verify "refs/heads/$name" >/dev/null || [ -e "$wt" ]; then
  echo "$name exists: refusing to touch it" >&2; exit 1
fi
[ "$(readlink -f .git/hooks/pre-push)" = "$root/tools/pre-push.sh" ] && [ -x .git/hooks/pre-commit ] || make dev-setup
d="$(mktemp -d "$root/tmp/$name.XXXXXX")"; made+=(dir)
git init -q --bare "$d/remote.git"
git remote add "$name" "$d/remote.git"; made+=(remote)          # errexit: a failed add stops here
git worktree add -q -b "$name" "$wt" HEAD; made+=(branch worktree)
cd "$wt" && uv sync --frozen && uv run llmenv.py --help >/dev/null
git push -q --no-verify "$name" "$name"                         # untimed: the timed push is then a fast-forward
```
  Then time: (1) a one-line `README.md` edit, `time git commit -qam ...` (target < 5 s); (2) a one-line comment in one
  `pylib` module plus its test, `time git commit -qam ...`, choosing a module whose test does not reach the big shell
  test file (target < 15 s); (3) `time git push -q "$name" "$name"` (target < 60 s). Expected: no `$name` remains in
  `git worktree list`, `git remote` or `git branch`, and `ls -A tmp` lists only the three owner files. A missed
  target is recorded as such and goes to O4.
- [ ] **Step 6: Record and commit.** Fill in "R9 measured" under Open decisions, then
  `git add docs/superpowers/plans/2026-11-23-fast-sound-hooks.md && git commit -m "docs(plan): record R9 hook timings"`

### Task 12: Replay check (G3)

**Files:** Test `tests/test_selection.py`.

- [ ] **Step 1: Test, whose oracle uses `git grep` and spec D3's rule 3, not selector code**
```python
TEST_FILE = re.compile(r"tests/(.*/)?test_[^/]*\.py")                               # the oracle's own copy

def _z(repo, *args, ok=(0,)):                                                       # NUL-delimited git output
    run = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)
    assert run.returncode in ok, run.stderr
    return [p for p in run.stdout.split("\0") if p]

def _commit_paths(repo, sha):
    return _z(repo, "diff-tree", "-z", "--no-commit-id", "--name-only", "-r", "--no-renames", sha)

def _required(p, tracked):                    # rule 3: a non-root conftest.py requires the tests under its directory
    if p.endswith("/conftest.py") and p != "tests/conftest.py":
        return {t for t in tracked if TEST_FILE.fullmatch(t) and t.startswith(p.removesuffix("conftest.py"))}
    return set(_z(ROOT, "grep", "-z", "-l", "-w", "-F", Path(p).name, "--", "tests/*test_*.py", ok=(0, 1)))  # 1: none

def test_replayed_commits_select_every_test_the_oracle_requires():
    tree, exempt, beyond = load_tree(ROOT), read_exempt(ROOT / "tests/selection-exempt.txt"), set()
    tracked = _z(ROOT, "ls-files", "-z")
    for sha in git_out(ROOT, "log", "-25", "--no-merges", "--format=%H").split():
        paths = _commit_paths(ROOT, sha)
        sel = select(tree, [p for p in paths if p in tree], [p for p in paths if p not in tree], "push", exempt)
        if sel.full:                                                                # hubs and full: lines (rule 1)
            continue
        oracle = {t for p in paths for t in _required(p, tracked)} - OPT_IN
        assert oracle <= set(sel.tests), (sha, sorted(oracle - set(sel.tests)))
        beyond |= {t for p in paths if p in tree for t in owners(tree, p, 3)} - set(sel.tests)
    print(f"depth 3 would add {len(beyond)} test files: {sorted(beyond)}")         # measurement only (D1 gap)
```
- [ ] **Step 2: Run it** (`-k replayed -rP`): PASS plus the depth-3 line (into the commit body); on a failure fix the selector, never the oracle.
- [ ] **Step 3: Green and commit.**

### Task 13: Mutation spot-check (G2)

**Files:** Create `tools/selection-mutation-check.sh`; Modify `tools/test.sh` (unset `LLM_ENV_MUTATION_CHECK`),
`tests/runner_helpers.py` (append it to `OPT_IN_SWITCHES`); Test `tests/test_hooks.py`.

**Interfaces:**
- `bash tools/selection-mutation-check.sh [--list] [--only N] [--break-cli-edge]`; `--list` prints
  `file<TAB>search<TAB>replace<TAB>kind` per fault in table order; `--only N` runs the control and fault N.
- `--classify <rc> <stdout-file> <stderr-file>` prints one verdict for a subset run: `caught` only when rc is 1, the
  last stdout line has `[0-9]+ failed` and no `[0-9]+ error`, stdout has no xdist crash marker (`node down:`,
  `crashed while running`, `replacing crashed worker`, `maximum crashed workers`: xdist reports a crashed worker's
  test as failed; verified 2026-11-23), and stderr has no runner marker (`leak:`, `inventory:`, `network namespace
  unavailable`, `uv cannot resolve`); `passed` for rc 0 with no marker; otherwise `HARNESS`.
- Setup: exit 2 when the repo or `/tmp` is above 70 % full (`df --output=pcent`) or `git diff --quiet -- tools pylib
  tests llmenv.py` fails. Scratch `tmp/selection-mutation.XXXXXX` (trap-removed); a base copy of the index
  (`git checkout-index -a --prefix=<scratch>/base/`, `mkdir tmp`, `git init`, one commit); each fault runs in a fresh
  `cp -a` of it. A second copy `<scratch>/sel/` has the single `cli_edges(tree)` literal replaced by `{}` (another
  count exits 2) and answers fault 1's direct-namer selection and, under `--break-cli-edge`, every selection.
- Each copy gets its own environment (`UV_PROJECT_ENVIRONMENT=<copy>/.venv uv sync --project <copy> --frozen
  --offline`) and runs `bash tools/test.sh -rfE <tests> --deselect <the integrity test>` with it.
- Flow: control = the full suite on the base copy, integrity test included, exit 0 (else `HARNESS 0 control rc=<rc>`,
  exit 2). Per fault: replace, commit, select with `PUSH_FROM=<base>` `PUSH_TO=<fault commit>`, run, classify; if the
  subset passed, run the full suite (`caught` = `HOLE`, `passed` = `INVALID`, else `HARNESS`). Fault 1 is `INVALID`
  when it has no direct namer or its direct-namer subset fails. Output `<verdict> <n> <kind>` per fault; exit 2 on any
  HARNESS, else 1 on any HOLE or INVALID, else 0.

Fault table (each search counted once with `grep -c -F` on 2026-11-23; fault 8's line is created by T3, and fault 9's
detecting test by T2):

| # | kind | file | search | replace | expected detecting test |
|---|------|------|--------|---------|-------------------------|
| 1 | pylib-cli | `pylib/omniroute_cli.py` | `print("idle")` | `print("idle.")` | `tests/test_heal_omniroute.py::test_idle_container_above_the_threshold_restarts_once_after_n_samples` |
| 2 | sh | `scripts/heal-omniroute-admission.sh` | `OMNIROUTE_HEAL_MEMORY_SAMPLES:-3` | `OMNIROUTE_HEAL_MEMORY_SAMPLES:-4` | same test as fault 1 (line 175: the third run must restart) |
| 3 | dashboard | `docker/observe/dashboards/laya.json` | `"title": "Laya: requests, question types, confidence"` | `"title": "Laya: requests, question kinds, confidence"` | `tests/test_observe_stack.py::test_laya_dashboard_uses_only_emitted_families_with_descriptions` |
| 4 | compose-key | `docker/observe/compose.yml` | `GF_USERS_ALLOW_SIGN_UP: "false"` | `GF_USERS_ALLOW_SIGNUP: "false"` | `tests/test_observe_stack.py::test_grafana_definition` |
| 5 | doc | `README.md` | ``is the executor combo and is never touched by `make local-combo` `` | ``is the executor combo and is never changed by `make local-combo` `` | `tests/test_docs.py::test_docs_describe_capacity_card_gate_and_local_combo` |
| 6 | makefile | `Makefile` | `@bash tools/run-target.sh model-capacity -- bash scripts/model-capacity.sh` | `@bash tools/run-target.sh model-capacity -- bash scripts/model-capacity-x.sh` | big shell test file `::test_makefile_exposes_model_capacity` (read, not run) |
| 7 | lib-sh | `tools/lib.sh` | `2>/dev/null)" = true ]; }` | `2>/dev/null)" = false ]; }` | `tests/skills/onboard-local-model/test_onboard_local_model_scripts.py::test_bench_fit_refuses_while_llm_server_holds_the_gpu` |
| 8 | conftest | `tests/conftest.py` | `os.environ["HF_HUB_OFFLINE"] = "1"` | `os.environ["HF_HUB_OFFLINE"] = "0"` | `tests/test_offline_environment.py::test_children_inherit_offline_switches[HF_HUB_OFFLINE]` |
| 9 | skill-script | `.claude/skills/onboard-local-model/scripts/fetch-gguf.sh` | `need=$((size - have + 1024 * 1048576))` | `need=$((size - have + 2048 * 1048576))` | T2's `test_fetch_gguf_needs_exactly_size_minus_part_plus_1024_mib` (no existing test catches it host-independently) |
| 10 | pylib-render | `pylib/compose.py` | `"stop_grace_period": "40s"` | `"stop_grace_period": "30s"` | `tests/test_compose.py::test_omniroute_service_stop_grace_period_and_restart_policy` |

Evidence, 2026-11-23, in an index copy under `tmp/` with `uv run pytest -q`:
- Faults 2-5, 7 and 10: 6/6 detecting tests passed unmutated and failed (6 failed, 0 errors) with the changes.
- Fault 1 reaches its detector only through the CLI edge (`scripts/heal-omniroute-admission.sh:169` runs `llmenv.py
  omniroute-update activity`; the heal test stubs only `podman`): unmutated 37 passed, mutated 7 failed. Its direct
  namers passed on the mutated copy (456 passed; `test_omniroute_derive_candidate.py`, read, does not touch the line).
- The heal and updater test worlds point `uv` at `<copy>/.venv`, so each copy needs its own environment.

- [ ] **Step 1: Failing tests** (`_classify(tmp_path, rc, out, err)` writes `out`/`err` to two files under `tmp_path`,
  runs `bash tools/selection-mutation-check.sh --classify <rc> <files>` in `ROOT` and returns its stripped stdout)
```python
def test_every_mutation_fault_matches_exactly_one_line():
    out = subprocess.run(["bash", "tools/selection-mutation-check.sh", "--list"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    rows = [l.split("\t") for l in out.splitlines()]
    assert len(rows) == 10 and len({r[3] for r in rows}) == 10 and rows[0][3] == "pylib-cli"
    assert all((ROOT / f).read_text().count(search) == 1 for f, search, _r, _k in rows)

@pytest.mark.parametrize(("rc", "out", "err", "verdict"), [
    (1, "1 failed, 3 passed in 1s", "", "caught"), (0, "4 passed in 1s", "", "passed"),
    (1, "1 failed, 3 passed in 1s", "leak: tmp/x\n", "HARNESS"), (1, "1 failed, 1 error in 1s", "", "HARNESS"),
    (1, "1 failed, 3 passed in 1s", "inventory: podman unavailable\n", "HARNESS"),   # a failed post-run inventory
    (1, "[gw0] node down: Not properly terminated\n2 failed in 1s", "", "HARNESS"),     # real xdist crash output, 2026-11-23
    (3, "", "", "HARNESS"), (69, "", "network namespace unavailable\n", "HARNESS")])
def test_mutation_verdict_credits_only_a_clean_assertion_failure(tmp_path, rc, out, err, verdict):
    assert _classify(tmp_path, rc, out + "\n", err) == verdict

@pytest.mark.skipif(os.environ.get("LLM_ENV_MUTATION_CHECK") != "1", reason="opt-in: LLM_ENV_MUTATION_CHECK=1 (runs the full suite)")
def test_mutation_check_finds_a_hole_without_the_cli_edge():
    result = subprocess.run(["bash", "tools/selection-mutation-check.sh", "--only", "1", "--break-cli-edge"],
                            cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 1 and "HOLE 1 pylib-cli" in result.stdout
```
- [ ] **Step 2: Red** (`-k "mutation or env_gate"` over both test files: no script yet, the opt-in test skipped, the
  env-gate test failing until the fourth switch is unset), then write the script and the two one-line edits.
- [ ] **Step 3: Run against the staged state** (`df -h . /tmp` below 70 %, `git add` the task's files): the two test
  files, then `bash tools/selection-mutation-check.sh` (10 `caught`, exit 0, about 15 min), then
  `LLM_ENV_MUTATION_CHECK=1 uv run pytest tests/test_hooks.py -k cli_edge -q` (1 passed), `make validate`, `ls -A tmp`.
  A `HOLE` is fixed in T5/T6 code with a new test; an `INVALID` or a fault-caused `HARNESS` by another literal; any
  other `HARNESS` in the script. The classifier is never loosened.
- [ ] **Step 4: Commit** with the ten result lines and the regression result in the body (rerun Step 3 after any change).

### Task 14 (deferrable): nightly full run (G4)

`setup/enable-test-nightly.sh` calls `install_user_timer llm-env-test-nightly … "tools/test-nightly.sh"
"OnCalendar=*-*-* 03:30:00"` (exposed by a one-line `test-nightly-enable` Makefile target and a `scripts/help.sh`
line); `tools/test-nightly.sh` runs `make test` into `~/.local/state/llm-env/test-nightly.log` and writes `<ISO time>
<short sha> PASS|FAIL rc=<n>` to `test-nightly.last`, which `scripts/status.sh` prints as `Nightly tests: …`.

- [ ] Tests in `tests/test_hooks.py` (fake `HOME`, stub `systemctl` and `make`, like the
  `setup/enable-system-one-check.sh` tests): `ExecStart`, `OnCalendar`, a `PASS rc=0` line, the status line. Red,
  implement, green (`-k "nightly or help or status"`), commit `feat(tests): opt-in nightly full suite with a make
  status line`.

## Phase 4: de-duplication (no case lost)

Scope (fixed): the 32 groups, 69 tests, found on 2026-11-23 by blanking every constant in each `test_*` body
(`"_"`/`0`) and grouping identical `ast.dump`s over 250 characters (scratch script under `tmp/`, deleted after). Each
group becomes one `@pytest.mark.parametrize` function, ids naming what told the old tests apart, assertions unchanged;
a group that cannot merge without changing an assertion is kept and named, with the reason, in the commit body. Proof
per task, in the commit body: the collected total (`out="$(bash tools/test.sh tests --collect-only)" && tail -1
<<<"$out"`) is equal before and after, and the `def test_` count of the touched files drops by exactly the task's
tests minus groups. Names drop `test_`; `{a, b}` is one test per item; `;` separates groups.

### Task 15: `test_config.py` (7 groups) and `test_compose.py` (3): 24 tests, 14 fewer definitions

`validate_config_rejects_invalid_{memory_ceiling_pct, memory_ceiling_floor_pct, cpu_ceiling_pct,
cpu_ceiling_floor_pct}`; `…_rejects_invalid_{vram_ceiling_pct, vram_ceiling_mib, vram_ceiling_floor_pct}`;
`…_rejects_invalid_{check_ctx_size, check_timeout_seconds}`; `validate_config_accepts_{n_cpu_moe_as_non_negative_int,
check_ctx_size_as_positive_int, check_timeout_seconds_as_positive_int}`; `migrate_config_adds_default_memory_ceiling_{pct,
floor_pct}`; `migrate_config_preserves_existing_memory_ceiling_{pct, floor_pct}`; `config_accepts_{valid_resources_section,
zero_sentinel_resources}`. Compose: `{service_publishes_configured_port, service_passes_the_dri_device}`;
`{sleep_idle_seconds_becomes_a_command_flag, remote_setup_service_command_runs_the_module}`;
`{omniroute_depends_on_llm_server_omitted_when_disabled, remote_setup_has_no_dependency_regardless_of_llm_server_state}`.
- [ ] Merge, prove, `make validate && make test`, commit `test: parametrize shape-duplicate config and compose tests (N -> N cases)`.

### Task 16: the big shell test file (8 groups, 17 tests, 9 fewer definitions; by keyword)

`setup_persists_a_vram_ceiling_using_the_configured_pct_not_the_default` with
`setup_floors_the_vram_ceiling_at_the_{configured_floor, code_default_when_unconfigured}`;
`lib_rejects_a_{non_numeric, zero}_health_timeout`; `setup_local_llm_agents_rejects_{concatenated_pi_json_without_replacement,
non_object_pi_providers_before_creating_opencode_directory}`; `setup_persists_{llm_server_disabled_via_env_var,
system_one_disabled_by_default}`; `setup_rebuilds_system_one_image_when_gpu_arch_label_{differs, missing}`;
`setup_rejects_{invalid_numbered_model_selection_before_download, comma_separated_model_selection}`;
`start_no_gpu_still_{waits_for_omniroute_and_runs_network_sh, starts_the_compose_stack}`;
`{stop_llama, start}_rejects_an_unknown_scope`.
- [ ] As T15 (`bash tools/test.sh tests -k "<group keywords>"`, `git add tests`), commit `test: parametrize shape-duplicate shell tests (N -> N cases)`.

### Task 17: the other 14 groups (28 tests, 14 fewer definitions) and one `_reply`

`test_remote_setup.py`: `combo_client_models_{falls_back_to_capped_output_when_unknown, clamps_output_above_context}`;
`config_{rejects_wrong_bearer_token, returns_503_when_master_key_unset}`;
`render_setup_script_{captures_the_http_status_and_checks_it, sends_omniroute_routed_model_ids}`;
`render_setup_script_{prompts_for_the_master_key_from_the_tty, uses_staged_files_with_restrictive_permissions}`;
`validate_host_header_rejects_{shell_metacharacters, trailing_garbage_after_the_bracket}`. `test_detect.py`:
`process_vram_mib_{sums_kib_on_the_matching_pci_address, counts_one_drm_client_once}`;
`processes_on_render_node_{finds_a_matching_pid, treats_missing_fdinfo_as_zero}`;
`processes_on_render_node_{sums_multiple_fds_for_one_pid, dedupes_same_drm_client_id_across_fds}`. `test_cli.py`:
`cmd_budget_{clamps_system_one_resident_to_reserve, system_one_unreadable_does_not_subtract}`. `test_dotenv.py`:
`read_env_file_{skips_blank_lines_and_comments, strips_surrounding_whitespace}`. `test_gguf.py`:
`tensor_sizes_rejects_{a_tensor_offset_that_violates_alignment, an_offset_beyond_the_tensor_data_span}`.
`test_observe_native_http.py`: `{values_keep_full_precision,
nan_quantiles_in_unused_families_are_skipped_like_real_grafana_output}`. `test_podman_state_check.py`:
`{absent_omniroute, host_getent_failure}_makes_dns_unknown_without_failure`. Onboard-local-model scripts test:
`effort_run_flags_a_transcript_that_reads_the_{manifest, generator_library_by_a_relative_path}`.

`tests/test_cli.py`'s six `_reply` methods (lines 1599, 1722, 1818, 1902, 2025, 2159) become one module-level
`_reply(handler, payload, *, status=200, set_cookie=None, content_length=True) -> None` (`def _reply` count 6 → 1;
`content_length=False` keeps `_MutableCombosHandler` unchanged). `_fake_login_ok` stays:
R8 asks for one copy per file, which each of its two files has; merging across files needs T19's shared module.
- [ ] As T15 (plus `tests/test_capacity.py`, which imports from `test_cli.py`; `git add tests`), commit `test:
  parametrize the remaining shape duplicates; one _reply helper (N -> N cases)`.

## Phase 5 (deferrable, owner's call)

- **T18:** split the big shell test file by script family (shared helpers to `tests/shell_helpers.py`); brings script
  commits under 15 s. Proof: collected total unchanged, T12 and T13 pass.
- **T19:** shared `tests/fakes/`: one fake podman instead of eight, one `_fake_login_ok` instead of two.

## Open decisions (not closable from the repo)

- O1: nothing runs the two `.test.mjs` files (`node --test`?); until decided they are `full:` exemptions.
- O2: the sweep test lives in the opt-in characterize lane (T1); `IsolatedBackend` does not isolate `--tmpdir` or
  `XDG_RUNTIME_DIR`, so the lane meets the AGENTS.md Podman rule only under a separate user (predates this plan).
- O3: hubs at commit select their direct namers; push runs the full suite. O4: script commits take about 30 s until
  T18; missed R9 targets go here. O5: the nightly timer is opt-in, not installed by `make dev-setup`.
- R9 measured (filled by T11 Step 5, 2026-11-23, wall clock through the installed hooks): README commit `14.5 s`
  (target < 5 s: missed, O4), pylib commit `13.5 s` (`pylib/observe_laya_requests.py` + its test, 6 test files; < 15 s
  met), push `32.1 s` (fast-forward of both, 11 test files; < 60 s met). The README miss: `selection-lint` alone takes
  6.5 s (one `owners(tree, p, 1)` per tracked file, each a scan of the whole tree; a one-pass index is the fix), and
  `README.md` selects 3 test files by its basename, among them the 406-test onboard-local-model script tests.

## Self-Review

- **Spec coverage:** R1 → T1, T3, T4, T9. R2 → T5-T7, T9. R3 → T7, T8. R4 → T1. R5 → T1, T3. R6 → T4. R7 → T2.
  R8 → T15-T17. R9 → T10, T11 Step 5, T18. R10 → T4, T8, T9. D1 → T5, T7. D2 → T6. D3 → T5, T11. D4 → T10.
  D5 → T1, T3. D6 → T4. D7 → T9. D8 → T8, T9. G1 → T11. G2 → T13. G3 → T12. G4 → T14. Sweep move → T1 Step 1.
- **Plan-added names:** `PUSH_FROM`/`PUSH_TO`, `LLM_ENV_MUTATION_CHECK`, the offline check's status 4, `run dir:`.
