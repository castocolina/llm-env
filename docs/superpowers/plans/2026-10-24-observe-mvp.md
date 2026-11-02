# Observe MVP (phase 0) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A host-side collector with no resident service: a systemd user timer runs `uv run llmenv.py observe` every 60 s and appends JSON lines to a bounded store. The existing `make resources-snapshot` then shows 1 h / 24 h / 7 d trends from that store.

**Architecture:** One new module, `pylib/observe.py`, holds the store (UTC day files, rotation, disk guard, state, lock) and the sources. Container counters come from the existing `resources.live_resources`, behind a locator cache that runs podman only when a container changed. OmniRoute data comes from the existing bounded dashboard helpers in `omniroute_activity` (health prime-then-read, then new persisted call-log rows behind a cursor). Laya request counts come from one bounded `journalctl --user` call per tick, also behind a cursor. The three sources run concurrently on daemon threads under one 30 s tick deadline (Task 7), so a slow source cannot starve the others. A tick commits its records and its cursors through a write-ahead `pending` entry in `state.json`, so a killed run never counts twice (Task 7). The trend renderer reads the store, anchors every window with a baseline sample and renders unknown as `n/a` (Task 8). The subcommand is wired into `llmenv.py`, the trend tables into `cmd_resources --live` and `scripts/resources-snapshot.sh`, and the timer into the setup script that already installs the healer timer.

**Tech Stack:** Python 3.11 standard library (no new dependency), bash, systemd user units, pytest. Fakes: the existing `tests/fixtures/fake_omniroute.py` server and `tests/omniroute_fakes.py` helpers, a fake cgroup tree and a fake `/proc`.

**Spec:** `docs/superpowers/research/2026-10-20-laya-jev-grafana-observability.md`: Status, §5 options and phased recommendation (phase 0), §6 per-container instrumentation table, §7 privacy and retention, §9 "How to know the resource limits work", §12 "Decisions of 2026-10-24" with the disk analysis. Thresholds come from `docs/superpowers/research/2026-10-24-benchmark-derived-sizing.md`. Conventions are in `docs/superpowers/plans/README.md`. The owner's brief of 2026-10-24 narrows phase 0 as follows: no `make observe`, the trend goes into `make resources-snapshot`, and the record list is the one in "Record schema" below.

## Global Constraints

- **No new make targets** and no variant of an existing one. Trends go into `make resources-snapshot`. The only new entry point is the `observe` subcommand of `llmenv.py`.
- Python is invoked only as `uv run llmenv.py <subcommand>` (AGENTS.md rule). The timer runs `uv run --project <repo> <repo>/llmenv.py observe`, the same form as the healer.
- **Test runs.** The full gate is `make validate && make test`; `make test` runs `tools/test.sh`, which is `uv run pytest tests/ -n auto -v` and accepts no arguments. During TDD a targeted run is `uv run pytest <path> [-k <expr>] -q`, written exactly like the invocation inside `tools/test.sh` (the README already uses it for the opt-in characterization runs). That is the only sanctioned way to run Python tests outside `make test`. No command in this plan types `python`, `python3` or a bare `pytest`, adds a make target, or changes `tools/test.sh`. The fake OmniRoute is spawned with `sys.executable` (the interpreter pytest itself runs in under `uv run`) and the same argv as `tests/omniroute_world.py`, never with an interpreter name.
- The timer is installed by the existing path: `scripts/start.sh` already calls `setup/enable-omniroute-heal.sh`. That script also writes the observe units. `start.sh` gets no new call.
- Store: `${XDG_STATE_HOME:-~/.local/state}/llm-env/observe/`. The directory is mode 0700 and every file in it 0600. There is one `YYYY-MM-DD.jsonl` per UTC day, plus `state.json`, `.state.json.tmp` and `.lock`. The mode argument of `os.open(..., O_CREAT, 0o600)` only applies to a file it creates, so every file the collector opens (day file, temp state, lock) is forced to 0600 with `os.fchmod` on the open descriptor. Tests start from permissive (0644/0666) existing files.
- Retention follows research §12 decision 6: metrics are kept **365 days / 2 GB**. Whichever limit binds first applies, and today's file is never deleted.
- Disk guard follows research §12 decision 6: stop appending, and log once, when free space on the store's filesystem is **under 100 GB or under 15 %**. On this host that filesystem is `/var`, because `/home` is `/var/home`. The guard suspends appending only: age and size retention keep running while it is active (that is when reclaiming expired data matters most).
- Cadence is 60 s (`OnUnitActiveSec=60s`, `AccuracySec=5s`) with `TimeoutStartSec=45s`. There is no resident process.
- **One tick deadline.** A tick has one explicit overall deadline, `TICK_DEADLINE_S = 30.0`, which leaves about 15 s of the unit's 45 s for `uv` start-up, the append and two small state writes. The three sources run concurrently on daemon threads, each over a private copy of its own state keys. Per-source budgets: container discovery shares the whole 30 s tick deadline (its `podman inspect` calls are 10 s each, so a cold discovery of four containers can need up to 80 s and is cut at the deadline); OmniRoute has its own 10 s deadline inside `omniroute_record`; the Laya journal call is 5 s. A source still running at the deadline becomes a `TimeoutError` error record and its cursors stay where they were; the records of the sources that finished are still appended and their cursors saved. Daemon threads never block the process exit.
- **Crash consistency.** Cursors and the records they produced are committed together: the new state is saved with a `pending` entry that holds the tick's records (one atomic write), then the records are appended, then `pending` is cleared. The next run appends any pending record not yet in the day file before it collects anything. Whatever instant a run is killed at, each persisted call and each Laya request is counted exactly once. Pruning runs after the commit and its failure is ignored.
- Privacy follows research §7 and the §12 Q1 decision: shape and counts only. No prompt, answer, call-log `error` text, request or response body, API key name, cookie, session token or password may appear in any record, in `state.json`, or on stdout or stderr. The dashboard password is read from `~/.config/llm-env/models.yml` (`omniroute.initial_password`) and is never logged. The `observe` subcommand's own failure output is one of two fixed categories, `config` or `internal`, and never exception text, a path or a configuration value: `load_config` and `validate_config` messages can carry YAML parser diagnostics and configuration-derived strings.
- `/api/monitoring/health` is stale-while-revalidate, so it is read twice: prime, sleep `PRIME_GAP_S` (0.2 s), read again, and use the second answer. This is the same rule as `omniroute_activity.Collector` and the healer.
- Call-log rows on `/api/providers/test` are internal probes and never count as client calls. A path that no shipped profile classifies is counted as `unclassified`, never as a client call.
- Cost: no exec into containers. At steady state no podman process is spawned: pid and cgroup lookups are cached and revalidated through `/proc`. Every external call is bounded: dashboard API 10 s in total, `journalctl` 5 s, `podman inspect` 10 s.
- `observe` always exits 0. A failing source becomes an `error`/`unknown` record, and a failure outside the sources becomes `{"ok":false,"error":"config"}` or `{"ok":false,"error":"internal"}` on stdout (nothing on stderr).
- A missing or unreadable value is recorded as `null` (unknown), never as 0. This follows research §7, "unknown is not zero". The trend tables keep the rule: a value with no readable sample renders `n/a`, and a value that is only a lower bound (some ticks of the window were unknown, truncated or had unreadable rows) renders with a trailing `+`. A counter needs two known samples before it can show an increase, so one reading renders `n/a`, not `0`.
- AGENTS.md "Test Processes and Containers": every process a test starts (the fake OmniRoute) is killed and reaped in `finally`. Tests write only under `tmp_path`.
- After every task, run `make validate && make test` and expect both to pass. AGENTS.md requires this after any `.sh` or `.py` edit.

## Review Focus

Each line names a condition the spec implies but that is easy to miss, and the behavior a reasonable person would expect. Each one has a test pinned in the task that owns the code.

1. **A restart in the middle of a window.** A container or OmniRoute restart resets `memory.events`, `pressureGuardRejectCount` and `shedTotal` to zero. Trends must count the new lifetime from zero, never negative and never twice. Pinned by `test_counter_increase_survives_restarts_and_unknowns` in Task 8.
2. **UTC midnight between two ticks.** A 1 h window then spans two day files, and both must be read. Pinned by `test_read_records_spans_the_utc_midnight_rollover` in Task 1.
3. **A dashboard password with JSON or shell metacharacters** (`"`, `$`, `\`, backtick, quote). Login must still succeed, and the value must never be printed. Pinned by `test_password_with_json_and_shell_metacharacters_logs_in` in Task 4.
4. **A journald vacuum that invalidates the saved Laya cursor.** `journalctl --after-cursor` then exits 1 on every tick unless the cursor is reseeded. Pinned by `test_laya_invalid_cursor_reseeds_and_a_hung_journal_is_unknown` in Task 6.
5. **A burst larger than the page bound in one minute, or a row persisted later than a newer one.** The tick must be flagged `truncated`, and the following ticks must count the rest exactly once with the same fixed page bound (the cursor remembers a `resume` offset; maximum catch-up per tick is stated in Task 5). Pinned by `test_a_burst_beyond_the_page_bound_is_caught_up_exactly_once_with_the_same_bound`, `test_a_burst_catch_up_survives_new_rows_arriving_meanwhile` and `test_new_rows_are_counted_once_across_ticks` in Task 5.
6. **A counter increase that straddles a window edge, or a restart whose new counter already exceeds the old one.** Each window keeps the newest earlier sample as its baseline, and OmniRoute samples carry the container lifetime (id + StartedAt). Pinned by `test_windows_keep_a_baseline_sample_so_boundary_increases_count` and `test_gateway_counters_use_the_container_lifetime_not_only_a_decrease` in Task 8.
7. **A run killed between the append and the state commit.** Pinned by `test_a_killed_tick_neither_loses_nor_double_counts` in Task 7.
8. **A source that hangs.** Pinned by `test_a_slow_container_discovery_cannot_starve_the_other_sources` in Task 7.
9. **Unknown shown as zero.** Pinned by `test_trend_renders_unknown_as_na_never_as_zero` and `test_counter_increase_survives_restarts_and_unknowns` in Task 8, and by `test_a_failed_call_log_source_is_unknown_not_an_empty_summary` in Task 5.

---

## Measured facts this plan relies on (checked 2026-10-24, read-only)

- **Call-log row lifecycle**, from the recorded 3.8.53 evidence `tests/fixtures/omniroute-evidence/2026-10-24-omniroute-characterization-2a7a68e89fe5.json.gz`, key `row_table`, 1,448 row shapes:
  - A call first appears as **in-memory** rows. Their id has the form `<ms>-<hex>-<n>`. `active` is a boolean: true while the call runs, then false with `completed: true` and `detailState: "in-memory"`.
  - It then appears as **one persisted row**. That row has a new UUID `id`, no `active` field, `detailState: "ready"`, and its `timestamp` equals the in-memory `completedAt`.
  - 279 rows are persisted: 271 `/v1/chat/completions`, 1 `/v1/messages`, 1 `/v1/responses`, 5 `/api/providers/test` and 1 `/v1/embeddings`. Their 279 ids are unique.
  - Conclusion: count persisted rows only (`active` is not a bool), key them by `id`, and use the timestamp as completion time.
  - Fields present on rows: `id, timestamp, path, status, duration, comboName, model, provider, tokens.{in,out,cacheRead,reasoning}`, plus `error`, `apiKeyName` and others that are never read.
- **Evidence encoding** (read from the committed file on 2026-10-24): `tests/fixtures/omniroute-evidence/*.json.gz` is gzip-compressed JSON, a dict with keys `config_id, entrypoint, image, journal, row_table, scenarios, series, versions`. Bodies are **flat maps keyed by JSON pointer**, not the raw nested response. A `series["/api/monitoring/health"]` entry is `{"body": {"/adaptiveAdmission/pressureGuardRejectCount": 0, "/chatAdmission/shedsByReason": {}, ...}}` (511 entries; `body` is a dict or `null`), and each `row_table[<hash>]` is `{"/id": "...", "/tokens/in": 10, "/timestamp": "...", ...}`. The pinning tests (Tasks 4 and 5) state this in their docstrings and read the bodies through helpers that accept both the flat-pointer encoding and a raw nested body (`_pointers` in Task 4, `_evidence_rows` in Task 5), so they hold for either.
- **Health pointers**: every pointer in `HEALTH_FIELDS` (Task 4) is present in the same evidence (`series["/api/monitoring/health"][*].body`). `eventLoopStall`, which research §5 lists, does **not** exist on 3.8.53 and is left out.
- **Laya access log** (`journalctl --user CONTAINER_NAME=system-one -o cat`):
  - Lines look like `"GET /health HTTP/1.1" 200` and `"POST /v1/systemone HTTP/1.1" 200`. Over a 2 h window there were 679 `/health`, 7 `/v1/systemone` and 1 `/metrics` 404.
  - `-n 0 --show-cursor` prints only a `-- cursor: ` line, so the first tick can seed without reading history.
  - A bogus `--after-cursor` exits 1.
  - A cursor-bounded call takes about 5 ms.
- **Healer journal**: `journalctl --user -u llm-env-omniroute-heal.service --since @<epoch> -o short-unix` prints lines that start with `<epoch.us> host ...`. Both restart paths log the phrase `restarting omniroute`. The "not restarting" lines never contain that phrase (`scripts/heal-omniroute-admission.sh:143,236`).
- **Container cgroup**:
  - `/proc/<State.Pid>/cgroup` `0::` resolves to `.../libpod-<64 hex>.scope/container`.
  - That directory holds `memory.{current,peak,max,swap.current,swap.peak,events,pressure}`, `cpu.stat` and `pids.current`.
  - `/proc/<pid>/stat` field 22 is the process start time.
  - Store filesystem: `/var/home` is 806 G with 341 G free (58 % used).
- `uv` lives in `~/.local/bin`, which is already on the healer unit's `Environment=PATH`. systemd is 259.

## Host-specific assumptions (document in README, Task 11)

| Assumption | Where it is used | On another host |
|---|---|---|
| cgroup v2 unified at `/sys/fs/cgroup`; rootless Podman with the systemd cgroup manager (`libpod-<id>.scope/container`) | container records, container id | `id` is `null`; counters still work if `/proc/<pid>/cgroup` resolves |
| `/home` is `/var/home` (Fedora Atomic), so the store sits on `/var` | disk guard | the guard measures whatever filesystem holds the store |
| Container logs on journald; the user journal is readable (ACL) | Laya counts, healer restarts | Laya record `unknown/journal`, healer column `n/a` |
| amdgpu DRM fdinfo `drm-memory-vram` and sysfs `mem_info_vram_used` (existing `detect.process_vram_mib`) | Laya VRAM | VRAM `null` |
| `uv` in `~/.local/bin` | unit `Environment=PATH` | edit with a drop-in (`systemctl --user edit llm-env-observe.service`) |
| systemd user manager; without `loginctl enable-linger` timers stop at logout | timer | same limit the healer timer has today |
| `XDG_STATE_HOME` is the same in the shell and in the systemd user environment (default: unset in both) | store path | the snapshot reads a different directory than the timer writes |
| OmniRoute 3.8.53 field names (shipped profile `api_fields`, `HEALTH_FIELDS`) | OmniRoute record | an absent field is `null`; an unclassified path is counted as `unclassified` |

## Record schema (one JSON object per line, `"v": 1`)

- `{"v":1,"t":<epoch s>,"kind":"container","service":"omniroute|remote_setup|system_one|phoenix","id":"<12 hex>|null", ...}`
  - The rest of the line is the `resources.read_live` row for that service: `state, uptime_s, started_at, current_mib, peak_mib, cap_mib, current_over_cap, peak_over_cap, swap_mib, swap_peak_mib, events{high,max,oom,oom_kill}, cpu_avg_cores, cpu_usec, pids, restart_pending, psi_{some,full}_avg{10,300}, verdict`.
  - For System One it also has `vram_mib` and `device_vram_mib`.
  - Last field: `"cpu_cores": <delta since previous tick>|null`.
  - A stopped container is written as `{"state":"not running"}` with the same envelope.
  - One line per enabled service per tick.
- `{"v":1,"t":…,"kind":"omniroute","state":"ok|unknown","reason"?,"detail"?,"life"?:[<12 hex container id>,<StartedAt epoch s>],"health":{…HEALTH_FIELDS…,"sheds_by_reason"?},"calls":{calls,status{2xx,…},by_combo,by_model,by_provider,duration_ms_sum,duration_ms_max,duration_known,tokens_in,tokens_out,tokens_cache_read,tokens_reasoning,tokens_known,probes,unclassified,unreadable,in_flight,seeded,truncated}|null,"calls_reason"?}`
  - `life` is stamped by `run_observe` from the OmniRoute container record of the same tick, so a counter reset is recognised even when the new counter already exceeds the old one. It is absent when the container was not identified.
  - `calls` is `null` (with `calls_reason`) when the call-log source failed while health was read: unknown, never an empty summary. The call cursor is not advanced then.
  - `duration_known` counts the calls with a numeric duration; `tokens_known` counts the calls with known usage. The mean latency divides by `duration_known`, not by `calls`.
- `{"v":1,"t":…,"kind":"laya","state":"ok|unknown","requests":{"2xx":n,…},"health_checks":n,"other_paths":n,"seeded":bool,"truncated":bool}`. Written only when `system_one.enabled`.
- `{"v":1,"t":…,"kind":"container|omniroute|laya","state":"error","error":"<exception type>"}`. Written when a source raised, or `"TimeoutError"` when it was still running at the tick deadline.
- `state.json` (never records): `containers` (locator cache), `cpu` (baselines), `calls` (`{"floor":ms,"seen":{id:ms},"resume":offset}`), `laya` (`{"cursor":str}`), `guard` (string while the disk guard is active) and `pending` (`{"t":int,"records":[…]}` only between the two commits of a tick).

Size estimate: about 500 B per container line, about 900 B per OmniRoute line and about 150 B per Laya line, which is about 3 KB per tick and **about 4-5 MB per day**. Research §12 estimated 2.3 MB/day for the container lines alone. At that rate 365 days take about 1.6-1.8 GB, under the 2 GB cap, so the 365-day limit binds first.

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `pylib/observe.py` | Create | Store, rotation, disk guard, state and lock; locator cache; container, OmniRoute and Laya sources; `run_observe`; trend report |
| `llmenv.py` | Modify | `observe` subcommand (`cmd_observe`); trend tables in `cmd_resources --live`; `import time` |
| `pylib/resources.py` | Modify `_locate` (≈405-424), `read_live` (≈481-506), `live_resources` (≈509-532) | bounded podman timeout; `cpu_usec`, `pids`, `started_at` in live rows |
| `pylib/resource_benchmark.py` | Modify `container_pid` (≈47-51) | `timeout=10` on `podman inspect` |
| `scripts/resources-snapshot.sh` | Modify | print the trend tables and footer |
| `setup/enable-omniroute-heal.sh` | Modify | render and enable both timers through one `write_timer` function |
| `tools/lib.sh` | Modify | `OBSERVE_UNIT_NAME="llm-env-observe"` |
| `scripts/start.sh` | Modify (log text only, ≈146-148) | name both timers |
| `scripts/help.sh` | Modify (line 22) | mention trends |
| `tests/fixtures/fake_omniroute.py` | Modify | opt-in password check, health override, real call-log paging (`bare_rows`) |
| `tests/resource_fixtures.py` | Modify | shared `fake_cgroup` builder (moved from `tests/test_resources.py`) |
| `tests/test_resources.py` | Modify | import `fake_cgroup` instead of its local `_cgroup` |
| `tests/test_observe.py` | Create | all observe tests |
| `tests/test_shell.py` | Modify | snapshot prints trends; `make start` enables the observe timer |
| `README.md`, `AGENTS.md`, `.agents/architecture.md`, `docs/superpowers/plans/README.md`, research doc Status | Modify (Task 11) | operator docs and index |

---

### Task 1: Bounded JSONL store (day files, rotation, tolerant reader)

**Files:**
- Create: `pylib/observe.py`
- Test: `tests/test_observe.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `SCHEMA: int = 1`, `MAX_DAYS = 365`, `MAX_BYTES = 2 * 10**9`, `DAY_FILE: re.Pattern`.
  - `observe_dir(env: Mapping[str, str] = os.environ) -> Path`.
  - `ensure_store(store: Path) -> Path`, which creates the directory and chmods it 0700.
  - `day_name(t: float) -> str`, the UTC `YYYY-MM-DD`.
  - `append_records(store: Path, records: list[dict[str, Any]], now: float) -> Path`. The day file is forced to 0600 even when it already existed with a wider mode.
  - `prune_store(store: Path, now: float, *, max_days: int = MAX_DAYS, max_bytes: int = MAX_BYTES) -> list[str]`, which returns the removed file names.
  - `read_records(store: Path, since: float) -> tuple[list[dict[str, Any]], int]`, which returns the records oldest first and the count of unreadable lines.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_observe.py`:

```python
"""pylib/observe.py: host-side observe collector and its trends (docs/superpowers/plans/2026-10-24-observe-mvp.md)."""
import datetime
import json
import stat
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from pylib import observe

T = datetime.datetime(2026, 10, 24, 12, tzinfo=datetime.UTC).timestamp()
DAY = 86400


def _store(tmp_path):
    return observe.ensure_store(tmp_path / "observe")


def _day_file(store, day, size=10):
    path = store / f"{day}.jsonl"
    path.write_bytes(b"x" * (size - 1) + b"\n")
    return path


def test_observe_dir_follows_xdg_state_home(tmp_path):
    assert observe.observe_dir({"XDG_STATE_HOME": str(tmp_path)}) == tmp_path / "llm-env/observe"
    assert observe.observe_dir({"HOME": str(tmp_path)}) == tmp_path / ".local/state/llm-env/observe"


def test_append_writes_private_utc_day_files(tmp_path):
    loose = tmp_path / "observe"
    loose.mkdir(mode=0o755)
    store = observe.ensure_store(loose)
    path = observe.append_records(store, [{"t": T, "kind": "x"}, {"t": T, "kind": "y"}], T)
    assert path.name == "2026-10-24.jsonl"
    assert stat.S_IMODE(store.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert [json.loads(line)["kind"] for line in path.read_text().splitlines()] == ["x", "y"]


def test_append_forces_an_existing_permissive_day_file_to_0600(tmp_path):
    store = _store(tmp_path)
    path = store / "2026-10-24.jsonl"
    path.write_text('{"t": 1, "kind": "old"}\n')
    path.chmod(0o644)  # a file created by hand, or by an older run with a different umask
    observe.append_records(store, [{"t": T, "kind": "new"}], T)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert [json.loads(line)["kind"] for line in path.read_text().splitlines()] == ["old", "new"]


def test_torn_last_line_stays_one_unreadable_line(tmp_path):
    store = _store(tmp_path)
    (store / "2026-10-24.jsonl").write_bytes(b'{"t": 1, "kind": "torn"')
    observe.append_records(store, [{"t": T, "kind": "ok"}], T)
    records, bad = observe.read_records(store, T - 60)
    assert [r["kind"] for r in records] == ["ok"] and bad == 1


def test_read_records_spans_the_utc_midnight_rollover(tmp_path):
    store = _store(tmp_path)
    midnight = datetime.datetime(2026, 10, 25, tzinfo=datetime.UTC).timestamp()
    observe.append_records(store, [{"t": midnight - 30, "kind": "before"}], midnight - 30)
    observe.append_records(store, [{"t": midnight + 30, "kind": "after"}], midnight + 30)
    records, _ = observe.read_records(store, midnight - 3600)
    assert [r["kind"] for r in records] == ["before", "after"]
    assert sorted(p.name for p in store.glob("*.jsonl")) == ["2026-10-24.jsonl", "2026-10-25.jsonl"]


def test_read_records_filters_by_time_and_skips_garbage(tmp_path):
    store = _store(tmp_path)
    (store / "2026-10-24.jsonl").write_text('[1, 2]\n\nnot json\n{"kind": "no-t"}\n')
    observe.append_records(store, [{"t": T - 7200, "kind": "old"}, {"t": T, "kind": "new"}], T)
    records, bad = observe.read_records(store, T - 3600)
    assert [r["kind"] for r in records] == ["new"] and bad == 3
    assert observe.read_records(tmp_path / "missing", 0) == ([], 0)


def test_prune_drops_day_files_older_than_365_days(tmp_path):
    store = _store(tmp_path)
    for day in ("2025-10-23", "2025-10-24", "2026-10-24"):
        _day_file(store, day)
    (store / "state.json").write_text("{}")
    assert observe.prune_store(store, T) == ["2025-10-23.jsonl"]
    assert sorted(p.name for p in store.iterdir()) == ["2025-10-24.jsonl", "2026-10-24.jsonl", "state.json"]


def test_prune_caps_bytes_oldest_first_and_keeps_today(tmp_path):
    store = _store(tmp_path)
    for day in ("2026-10-21", "2026-10-22", "2026-10-23", "2026-10-24"):
        _day_file(store, day, size=100)
    assert observe.prune_store(store, T, max_bytes=250) == ["2026-10-21.jsonl", "2026-10-22.jsonl"]
    assert observe.prune_store(store, T, max_bytes=50) == ["2026-10-23.jsonl"]  # today alone may exceed the cap
    assert [p.name for p in store.glob("*.jsonl")] == ["2026-10-24.jsonl"]
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `uv run pytest tests/test_observe.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'pylib.observe'`.

- [ ] **Step 3: Write the minimal implementation**

Create `pylib/observe.py`:

```python
"""Host-side observe collector: phase 0 (MVP) of docs/superpowers/research/2026-10-20-laya-jev-grafana-observability.md.

A systemd user timer runs `uv run llmenv.py observe` every 60 s. Each run appends JSON lines to a bounded store and
exits; there is no resident service. Records hold counters, shapes and counts only, never a prompt, answer, error
text, key, cookie or password (privacy decision of 2026-10-24, research §7 and §12 Q1).
"""
from __future__ import annotations

import datetime
import json
import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

SCHEMA = 1
MAX_DAYS = 365  # research §12 decision 6: metrics 365 days / 2 GB
MAX_BYTES = 2 * 10**9
DAY_FILE = re.compile(r"\d{4}-\d{2}-\d{2}\.jsonl")


def observe_dir(env: Mapping[str, str] = os.environ) -> Path:
    """`${XDG_STATE_HOME:-~/.local/state}/llm-env/observe` (same rule as omniroute_activity.local_profile_dir)."""
    state = env.get("XDG_STATE_HOME") or str(Path(env.get("HOME") or Path.home()) / ".local/state")
    return Path(state) / "llm-env/observe"


def ensure_store(store: Path) -> Path:
    store.mkdir(parents=True, exist_ok=True)
    store.chmod(0o700)  # also repairs a directory created by hand with a wider mode
    return store


def day_name(t: float) -> str:
    """UTC day of `t`: one file per UTC day, so DST never splits or merges a file."""
    return datetime.datetime.fromtimestamp(t, datetime.UTC).strftime("%Y-%m-%d")


def append_records(store: Path, records: list[dict[str, Any]], now: float) -> Path:
    path = store / f"{day_name(now)}.jsonl"
    data = "".join(json.dumps(r, separators=(",", ":")) + "\n" for r in records).encode()
    fd = os.open(path, os.O_RDWR | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.fchmod(fd, 0o600)  # the mode argument above only applies to a file this call creates
        size = os.fstat(fd).st_size
        if size and os.pread(fd, 1, size - 1) != b"\n":
            data = b"\n" + data  # a run killed mid-write left a torn line: end it so it stays one unreadable line
        os.write(fd, data)
    finally:
        os.close(fd)
    return path


def prune_store(store: Path, now: float, *, max_days: int = MAX_DAYS, max_bytes: int = MAX_BYTES) -> list[str]:
    """Remove day files older than `max_days`, then the oldest until the store fits `max_bytes`; today's file stays.
    Only `YYYY-MM-DD.jsonl` names are touched (never state.json or the lock)."""
    cutoff, today = day_name(now - max_days * 86400), day_name(now)
    files = sorted((p for p in store.iterdir() if DAY_FILE.fullmatch(p.name)), key=lambda p: p.name)
    removed: list[str] = []
    for path in files:
        if path.name[:10] < cutoff:
            path.unlink(missing_ok=True)
            removed.append(path.name)
    kept = [p for p in files if p.name not in removed]
    total = sum(p.stat().st_size for p in kept)
    for path in kept:
        if total <= max_bytes or path.name[:10] == today:
            break
        total -= path.stat().st_size
        path.unlink(missing_ok=True)
        removed.append(path.name)
    return removed


def read_records(store: Path, since: float) -> tuple[list[dict[str, Any]], int]:
    """(records with t >= since, oldest first; count of unreadable lines). Only day files that can hold them are read."""
    if not store.is_dir():
        return [], 0
    first = day_name(since)
    records: list[dict[str, Any]] = []
    bad = 0
    for path in sorted(p for p in store.iterdir() if DAY_FILE.fullmatch(p.name) and p.name[:10] >= first):
        with path.open("rb") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    bad += 1
                    continue
                t = record.get("t") if isinstance(record, dict) else None
                if isinstance(t, (int, float)) and not isinstance(t, bool):
                    if t >= since:
                        records.append(record)
                else:
                    bad += 1
    records.sort(key=lambda r: r["t"])
    return records, bad
```

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `uv run pytest tests/test_observe.py -q`
Expected: 8 passed.

- [ ] **Step 5: Run the gate**

Run: `make validate && make test`
Expected: both pass.

- [ ] **Step 6: Commit**

```bash
git add pylib/observe.py tests/test_observe.py
git commit -m "feat(observe): bounded UTC-day JSONL store with 365-day / 2 GB rotation"
```

---

### Task 2: Disk guard, collector state and run lock

**Files:**
- Modify: `pylib/observe.py`
- Test: `tests/test_observe.py`

**Interfaces:**
- Consumes: `ensure_store` (Task 1).
- Produces:
  - `FREE_MIN_BYTES = 100 * 10**9` and `FREE_MIN_RATIO = 0.15`.
  - `disk_guard(store: Path, statvfs: Callable[[Path], Any] = os.statvfs) -> tuple[bool, str]`.
  - `load_state(store: Path) -> dict[str, Any]`, which returns `{}` when the file is missing or corrupt.
  - `save_state(store: Path, state: dict[str, Any]) -> None`, which writes atomically with mode 0600, also when `.state.json.tmp` already exists with a wider mode.
  - `store_lock(store: Path)`, a context manager that yields `bool` (True when held). The `.lock` file is forced to 0600 as well.

- [ ] **Step 1: Write the failing tests**

Add `import pytest` and `from types import SimpleNamespace` to the imports of `tests/test_observe.py`, then append:

```python
def _fs(free_gb, total_gb):
    """A statvfs stub in exact bytes (f_frsize 1)."""
    return lambda _path: SimpleNamespace(f_bavail=int(free_gb * 10**9), f_frsize=1, f_blocks=int(total_gb * 10**9))


@pytest.mark.parametrize(("free", "total", "ok"), [
    (341, 806, True),    # this host on 2026-10-24
    (90, 806, False),    # under 100 GB
    (120, 1000, False),  # 12 % is under 15 %
    (100, 600, True),    # boundary: exactly 100 GB and 16.7 %
])
def test_disk_guard_thresholds(tmp_path, free, total, ok):
    allowed, detail = observe.disk_guard(tmp_path, _fs(free, total))
    assert allowed is ok and "GB free" in detail


def test_state_roundtrip_is_private_and_corrupt_state_is_empty(tmp_path):
    store = _store(tmp_path)
    assert observe.load_state(store) == {}
    observe.save_state(store, {"calls": {"floor": 1}})
    assert observe.load_state(store) == {"calls": {"floor": 1}}
    assert stat.S_IMODE((store / "state.json").stat().st_mode) == 0o600
    (store / "state.json").write_text("{trunc")
    assert observe.load_state(store) == {}
    (store / "state.json").write_text("[]")
    assert observe.load_state(store) == {}


def test_store_lock_is_exclusive(tmp_path):
    store = _store(tmp_path)
    with observe.store_lock(store) as first:
        with observe.store_lock(store) as second:
            assert first is True and second is False
    with observe.store_lock(store) as again:
        assert again is True


def test_state_temp_and_lock_files_are_forced_to_0600_when_they_already_exist(tmp_path):
    store = _store(tmp_path)
    for name in (".state.json.tmp", ".lock", "state.json"):
        (store / name).write_text("{}")
        (store / name).chmod(0o666)  # leftovers of a killed run, or made by hand
    observe.save_state(store, {"calls": {"floor": 1}})
    with observe.store_lock(store) as held:
        assert held is True
    assert {stat.S_IMODE((store / name).stat().st_mode) for name in ("state.json", ".lock")} == {0o600}
    assert not (store / ".state.json.tmp").exists()  # renamed over state.json
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `uv run pytest tests/test_observe.py -q -k "guard or state or lock"`
Expected: FAIL with `AttributeError: module 'pylib.observe' has no attribute 'disk_guard'`.

- [ ] **Step 3: Write the minimal implementation**

In `pylib/observe.py`, add `import contextlib` and `import fcntl` to the imports, and change the `collections.abc` import to `from collections.abc import Callable, Iterator, Mapping`. Then append:

```python
FREE_MIN_BYTES = 100 * 10**9  # research §12 decision 6: guard when free space < 100 GB or 15 %
FREE_MIN_RATIO = 0.15


def disk_guard(store: Path, statvfs: Callable[[Path], Any] = os.statvfs) -> tuple[bool, str]:
    """(appending allowed, description) for the filesystem holding the store (/var on Fedora Atomic: /home is /var/home)."""
    st = statvfs(store)
    free, total = st.f_bavail * st.f_frsize, st.f_blocks * st.f_frsize
    ratio = free / total if total else 0.0
    return (free >= FREE_MIN_BYTES and ratio >= FREE_MIN_RATIO,
            f"{free / 10**9:.0f} GB free ({ratio:.0%}) on the filesystem holding {store}")


def load_state(store: Path) -> dict[str, Any]:
    """The collector's own state (container cache, cursors, CPU baselines); a missing or corrupt file is empty."""
    try:
        state = json.loads((store / "state.json").read_text())
    except (OSError, ValueError):
        return {}
    return state if isinstance(state, dict) else {}


def save_state(store: Path, state: dict[str, Any]) -> None:
    tmp = store / ".state.json.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)  # O_CREAT's mode is ignored for a temp file a killed run left behind
    with os.fdopen(fd, "w") as handle:
        json.dump(state, handle, separators=(",", ":"))
    os.replace(tmp, store / "state.json")  # atomic: a killed run leaves the previous state, never half a file


@contextlib.contextmanager
def store_lock(store: Path) -> Iterator[bool]:
    """Non-blocking exclusive lock; yields True when held. A manual run during a timer run skips instead of interleaving."""
    fd = os.open(store / ".lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        os.fchmod(fd, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        yield True
    finally:
        os.close(fd)
```

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `uv run pytest tests/test_observe.py -q`
Expected: 15 passed.

- [ ] **Step 5: Run the gate**

Run: `make validate && make test`
Expected: both pass.

- [ ] **Step 6: Commit**

```bash
git add pylib/observe.py tests/test_observe.py
git commit -m "feat(observe): disk guard (100 GB / 15 %), atomic state file and run lock"
```

---

### Task 3: Container records over `live_resources` with a podman-free locator cache

**Files:**
- Modify: `pylib/resources.py`
  - `_locate`: add `timeout=10` and catch `subprocess.SubprocessError`.
  - `read_live`: add `cpu_usec` and `pids`.
  - `live_resources`: add `started_at`.
- Modify: `pylib/resource_benchmark.py`: `container_pid` gets `timeout=10`.
- Modify: `tests/resource_fixtures.py`: add `fake_cgroup` (moved from `tests/test_resources.py::_cgroup`, plus `pids.current`).
- Modify: `tests/test_resources.py`: delete the local `_cgroup` definition (≈lines 490-503) and add `from resource_fixtures import fake_cgroup as _cgroup` after the `pylib.resources` import. Keep `MIB`.
- Modify: `pylib/observe.py`
- Test: `tests/test_observe.py`

**Interfaces:**
- Consumes:
  - `resources.live_resources(limits, pci, *, locate, now) -> dict[str, dict]`.
  - `resources._locate(container) -> tuple[Path | None, int, float] | None`.
  - `resources.LIVE_CONTAINERS`.
  - `resource_benchmark.CGROUP_ROOT`.
- Produces:
  - The live row gains `cpu_usec: int | None`, `pids: int | None` and `started_at: int`.
  - `observe.CONTAINER_ID: re.Pattern`.
  - `observe.CachedLocator(cache: dict, *, locate=None, proc=Path("/proc"), root=CGROUP_ROOT)`. It is callable as `(container) -> tuple[Path | None, int, float] | None`. It has the attribute `.podman_calls: int` and the method `.container_id(container) -> str | None`.
  - `observe.container_records(limits, pci, state, now, *, locator=None) -> list[dict]`. It keeps `state["containers"]` (the locator cache) and `state["cpu"]` (`{service: [usec, t, started_at]}`).

- [ ] **Step 1: Move the cgroup builder into the shared fixtures**

Append to `tests/resource_fixtures.py`:

```python
MIB = 1024 * 1024


def fake_cgroup(path, *, current=100, peak=200, cap=1000, events="high 0\nmax 0\noom 0\noom_kill 0",
                psi_some=0.0, psi_full=0.0, cap_text=None, usage_usec=10_000_000, pids=3):
    """A cgroup v2 container directory with the files `resources.read_live` reads (sizes in MiB)."""
    path.mkdir(parents=True, exist_ok=True)
    files = {
        "memory.current": f"{current * MIB}", "memory.peak": f"{peak * MIB}",
        "memory.max": cap_text or f"{cap * MIB}", "memory.swap.current": f"{3 * MIB}",
        "memory.swap.peak": f"{5 * MIB}", "memory.events": events,
        "memory.pressure": f"some avg10=0.00 avg60=0.00 avg300={psi_some:.2f} total=1\n"
                           f"full avg10=0.00 avg60=0.00 avg300={psi_full:.2f} total=1",
        "cpu.stat": f"usage_usec {usage_usec}\nuser_usec 1",
        "pids.current": f"{pids}",
    }
    for name, text in files.items():
        (path / name).write_text(text + "\n")
    return path
```

In `tests/test_resources.py`, remove `def _cgroup(...)` and its body, and add `from resource_fixtures import fake_cgroup as _cgroup` below the `from pylib.resources import (...)` block.

Run: `uv run pytest tests/test_resources.py -q`
Expected: PASS, unchanged.

- [ ] **Step 2: Write the failing tests**

Add `import subprocess` to the imports of `tests/test_observe.py`, plus `from resource_fixtures import fake_cgroup` and `from pylib.resources import DEFAULT_POLICY, read_live` after `from pylib import observe`. Then append:

```python
CID = "f" * 64
REL = f"user.slice/libpod-{CID}.scope/container"


def _proc(tmp_path, pid, rel, start):
    """A fake /proc/<pid> with the cgroup line and a stat whose field 22 (start time) is `start`."""
    d = tmp_path / "proc" / str(pid)
    d.mkdir(parents=True, exist_ok=True)
    (d / "cgroup").write_text(f"0::/{rel}\n")
    (d / "stat").write_text(f"{pid} (laya serve) S " + " ".join(["0"] * 18) + f" {start} 0 0\n")
    return tmp_path / "proc"


class FakeLocate:
    def __init__(self, found):
        self.found, self.calls = found, []

    def __call__(self, name):
        self.calls.append(name)
        return self.found.get(name)


def test_read_live_reports_raw_cpu_usec_and_pids(tmp_path):
    row = read_live(fake_cgroup(tmp_path / "c", pids=7), 0, 1000, now=100)
    assert row["cpu_usec"] == 10_000_000 and row["pids"] == 7


def test_locate_gives_up_on_a_hung_podman(monkeypatch):
    from pylib import resources

    def hung(*args, **kwargs):
        assert kwargs.get("timeout") == 10
        raise subprocess.TimeoutExpired(args[0], 10)

    monkeypatch.setattr(subprocess, "run", hung)
    assert resources._locate("omniroute") is None


def test_locator_spawns_podman_only_when_the_container_changed(tmp_path):
    root = tmp_path / "cg"
    cgroup = fake_cgroup(root / REL)
    proc = _proc(tmp_path, 4242, REL, start=1000)
    locate = FakeLocate({"omniroute": (cgroup, 4242, 1_792_800_000.0)})
    cache: dict = {}
    locator = observe.CachedLocator(cache, locate=locate, proc=proc, root=root)
    assert locator("omniroute") == (cgroup, 4242, 1_792_800_000.0)
    assert locator("omniroute")[0] == cgroup and locate.calls == ["omniroute"]  # second tick: /proc only
    assert locator.container_id("omniroute") == CID[:12]
    _proc(tmp_path, 4242, REL, start=2000)  # same pid, new process: a restart
    locator("omniroute")
    assert locate.calls == ["omniroute", "omniroute"]
    fresh = observe.CachedLocator(json.loads(json.dumps(cache)), locate=locate, proc=proc, root=root)
    fresh("omniroute")  # the next timer run over the persisted cache still needs no podman
    assert len(locate.calls) == 2 and fresh.podman_calls == 0


def test_locator_forgets_a_stopped_container(tmp_path):
    cache = {"omniroute": {"cgroup": str(tmp_path / "gone"), "pid": 1, "started": 0.0, "start": 5}}
    locate = FakeLocate({})
    assert observe.CachedLocator(cache, locate=locate, proc=tmp_path / "proc", root=tmp_path)("omniroute") is None
    assert cache == {} and locate.calls == ["omniroute"]


def test_container_records_cpu_delta_restart_and_stopped(tmp_path):
    root, started = tmp_path / "cg", 1_792_800_000.0
    cgroup = fake_cgroup(root / REL, usage_usec=10_000_000)
    proc = _proc(tmp_path, 4242, REL, start=1000)
    locate = FakeLocate({"omniroute": (cgroup, 4242, started)})
    limits = {s: dict(p) for s, p in DEFAULT_POLICY.items()}
    state: dict = {}
    locator = observe.CachedLocator(state.setdefault("containers", {}), locate=locate, proc=proc, root=root)

    def tick(now):
        return {r["service"]: r for r in observe.container_records(limits, None, state, now, locator=locator)}

    first = tick(started + 100)
    assert first["omniroute"]["cpu_cores"] is None and first["omniroute"]["id"] == CID[:12]
    assert first["omniroute"]["started_at"] == int(started) and first["omniroute"]["pids"] == 3
    assert first["system_one"] == {"v": 1, "t": int(started + 100), "kind": "container", "service": "system_one",
                                   "id": None, "state": "not running", "cpu_cores": None}
    fake_cgroup(root / REL, usage_usec=13_000_000)
    assert tick(started + 160)["omniroute"]["cpu_cores"] == pytest.approx(0.05)  # 3 s of CPU over 60 s
    locate.found["omniroute"] = (cgroup, 4242, started + 150)  # restarted: new StartedAt
    _proc(tmp_path, 4242, REL, start=2000)
    assert tick(started + 220)["omniroute"]["cpu_cores"] is None  # no delta across a restart
    json.dumps(state)  # the state stays JSON-serialisable
```

- [ ] **Step 3: Run the tests and confirm they fail**

Run: `uv run pytest tests/test_observe.py -q -k "read_live or locate or locator or container_records"`
Expected: FAIL with `KeyError: 'cpu_usec'` and `AttributeError: ... 'CachedLocator'`.

- [ ] **Step 4: Implement the resources changes**

`pylib/resource_benchmark.py`, `container_pid`: add `timeout=10` to `subprocess.run([...], check=True, capture_output=True, text=True, timeout=10)`.

`pylib/resources.py`, `_locate`:

```python
    try:
        out = subprocess.run(
            ["podman", "inspect", "-f", "{{.State.Running}} {{.State.Pid}} {{.State.StartedAt.Unix}}", container],
            capture_output=True, text=True, check=True, timeout=10,
        ).stdout.split()
        if out[0] != "true":
            return None
    except (OSError, subprocess.SubprocessError, IndexError):
        return None
    try:
        cgroup = cgroup_dir(container)
    except (OSError, subprocess.SubprocessError, StopIteration, ValueError):
        cgroup = None
```

`pylib/resources.py`, `read_live`: in the `row` dict, add after `"cpu_avg_cores": ...`:

```python
        "cpu_usec": usec,
        "pids": _num(_read(cgroup / "pids.current")),
```

`pylib/resources.py`, `live_resources`: change the two running rows to:

```python
        if cgroup is None:
            out[service] = {"state": "running", "uptime_s": int(max(now - started, 1.0)), "started_at": int(started),
                            "verdict": "n/a"}
            continue
        vram = _vram(pid, pci) if service == "system_one" and pci else None
        out[service] = {"state": "running", "started_at": int(started),
                        **read_live(cgroup, started, policy["memory_max_mib"], now=now, vram=vram)}
```

- [ ] **Step 5: Implement the locator and container records**

In `pylib/observe.py`, add `from pylib.resource_benchmark import CGROUP_ROOT` to the imports. Then append:

```python
CONTAINER_ID = re.compile(r"libpod-([0-9a-f]{64})\.scope")  # rootless Podman, systemd cgroup manager


def _proc_start(pid: int, proc: Path) -> int | None:
    """Field 22 of /proc/<pid>/stat (start time in clock ticks): a reused pid never has the same value."""
    try:
        return int((proc / str(pid) / "stat").read_text().rsplit(")", 1)[1].split()[19])
    except (OSError, IndexError, ValueError):
        return None


def _proc_cgroup(pid: int, proc: Path, root: Path) -> Path | None:
    try:
        line = next(x for x in (proc / str(pid) / "cgroup").read_text().splitlines() if x.startswith("0::"))
    except (OSError, StopIteration):
        return None
    return root / line[3:].lstrip("/")


class CachedLocator:
    """`live_resources(locate=...)` backed by the observe state, so podman runs only when a container changed.

    In steady state each container costs two /proc reads and no process spawn. If the cached pid, /proc start time
    and cgroup path all still match, it is the same container process. A restart or recreate changes the pid and the
    start time; `resources._locate` then re-reads Pid and StartedAt from podman, bounded by its timeout."""

    def __init__(self, cache: dict[str, Any], *, locate: Callable[[str], Any] | None = None,
                 proc: Path = Path("/proc"), root: Path = CGROUP_ROOT) -> None:
        from pylib.resources import _locate

        self.cache, self.locate, self.proc, self.root = cache, locate or _locate, proc, root
        self.podman_calls = 0

    def __call__(self, container: str) -> tuple[Path | None, int, float] | None:
        hit = self.cache.get(container)
        if (isinstance(hit, dict) and hit.get("cgroup") and isinstance(hit.get("pid"), int)
                and _proc_start(hit["pid"], self.proc) == hit.get("start")
                and _proc_cgroup(hit["pid"], self.proc, self.root) == Path(hit["cgroup"])):
            return Path(hit["cgroup"]), hit["pid"], hit["started"]
        self.podman_calls += 1
        found = self.locate(container)
        if found is None:
            self.cache.pop(container, None)
            return None
        cgroup, pid, started = found
        self.cache[container] = {"cgroup": str(cgroup) if cgroup else None, "pid": pid, "started": started,
                                 "start": _proc_start(pid, self.proc)}
        return found

    def container_id(self, container: str) -> str | None:
        match = CONTAINER_ID.search(str((self.cache.get(container) or {}).get("cgroup") or ""))
        return match.group(1)[:12] if match else None


def _compact(row: Mapping[str, Any]) -> dict[str, Any]:
    return {k: round(v, 3) if isinstance(v, float) else v for k, v in row.items()}


def container_records(limits: Mapping[str, Any], pci: str | None, state: dict[str, Any], now: float, *,
                      locator: CachedLocator | None = None) -> list[dict[str, Any]]:
    """One record per enabled service: the `resources.live_resources` row, plus the container id and the CPU cores
    used since the previous tick (None on the first tick of a container lifetime)."""
    from pylib.resources import LIVE_CONTAINERS, live_resources

    locator = locator or CachedLocator(state.setdefault("containers", {}))
    baselines = state.setdefault("cpu", {})
    records = []
    for service, row in live_resources(limits, pci, locate=locator, now=now).items():
        usec, started = row.get("cpu_usec"), row.get("started_at")
        prev = baselines.get(service)
        cores = None
        if (usec is not None and isinstance(prev, list) and len(prev) == 3 and prev[2] == started
                and now > prev[1] and usec >= prev[0]):
            cores = round((usec - prev[0]) / 1e6 / (now - prev[1]), 3)
        if usec is None:
            baselines.pop(service, None)
        else:
            baselines[service] = [usec, now, started]
        records.append({"v": SCHEMA, "t": int(now), "kind": "container", "service": service,
                        "id": locator.container_id(LIVE_CONTAINERS[service]), **_compact(row), "cpu_cores": cores})
    return records
```

- [ ] **Step 6: Run the tests and confirm they pass**

Run: `uv run pytest tests/test_observe.py tests/test_resources.py tests/test_resource_benchmark.py -q`
Expected: all pass.

- [ ] **Step 7: Run the gate**

Run: `make validate && make test`
Expected: both pass.

- [ ] **Step 8: Commit**

```bash
git add pylib/observe.py pylib/resources.py pylib/resource_benchmark.py tests/resource_fixtures.py tests/test_resources.py tests/test_observe.py
git commit -m "feat(observe): container records over live_resources with a podman-free locator cache"
```

---

### Task 4: OmniRoute health counters (prime then read, bounded, password never printed)

**Files:**
- Modify: `tests/fixtures/fake_omniroute.py`. The changes are opt-in only, so existing tests are unaffected: a `password` check, a `health` override and `bare_rows` paging.
- Modify: `pylib/observe.py`
- Test: `tests/test_observe.py`

**Interfaces:**
- Consumes:
  - `omniroute_activity.bounded_login(base, password, deadline) -> str`.
  - `omniroute_activity.bounded_get(base, path, token, deadline) -> bytes`. It raises `Deferral`.
  - `omniroute_activity.PRIME_GAP_S` and `omniroute_activity._ptr`.
  - `omniroute_update.Deferral` (`.reason.value`, `.detail`).
  - `omniroute_fakes.free_port` and `omniroute_fakes.PASSWORD`.
- Produces:
  - `observe.HEALTH_FIELDS: dict[str, str]` (name to JSON pointer).
  - `observe._number(value) -> int | float | None`.
  - `observe.OMNIROUTE_BUDGET_S = 10.0`.
  - `observe.omniroute_record(port: int, password: str, state: dict, now: float, *, budget=OMNIROUTE_BUDGET_S, sleep=time.sleep) -> dict`.
  - The pytest fixture `fake_omni` yields `(port, state_path)`. The helper `_set(state_path, **values)` updates the fake's state. `EVIDENCE: Path` is the recorded evidence file (gzip-compressed JSON; bodies are flat pointer-keyed maps, see "Evidence encoding"), and `_pointers(body) -> set[str]` reads either encoding.

- [ ] **Step 1: Extend the fake OmniRoute (opt-in keys only)**

In `tests/fixtures/fake_omniroute.py`, add `import urllib.parse`. Then:

Replace the start of `health(s)` so that a state `health` object overrides the defaults:

```python
        fresh = {"timestamp": now, "chatAdmission": {"activeHeavy": 0, "activeHealthyHeadroom": 0,
                                                      "waiting": s.get("waiting", 0), "queuedBytes": 0}}
        fresh.update(s.get("health") or {})  # opt-in override (observe tests); absent: unchanged behaviour
```

Replace the login branch of `do_POST`:

```python
            if self.path == "/api/auth/login":
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                print(json.dumps({"method": "POST", "path": self.path}), flush=True)
                expected = state().get("password")  # opt-in: absent means any password is accepted
                try:
                    sent = json.loads(body or b"{}").get("password")
                except ValueError:
                    sent = None
                if expected is not None and sent != expected:
                    return self._send(401, {"error": "invalid password"})
                return self._send(200, {"ok": True}, [("Set-Cookie", "auth_token=fixture; Path=/")])
```

Replace the call-logs branch of `do_GET`:

```python
            if self.path.startswith("/api/usage/call-logs"):
                rows = s.get("rows", [])
                if not s.get("bare_rows"):
                    return self._send(200, {"logs": rows})
                # The real shape (source-verified, omniroute_activity.Collector._call_logs): every in-memory row
                # (boolean `active`) on every page, then the persisted rows newest first, sliced by limit/offset.
                query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                limit, offset = int(query.get("limit", ["10"])[0]), int(query.get("offset", ["0"])[0])
                memory = [r for r in rows if isinstance(r.get("active"), bool)]
                persisted = [r for r in rows if not isinstance(r.get("active"), bool)]
                return self._send(200, memory + persisted[offset:offset + limit])
```

- [ ] **Step 2: Write the failing tests**

Add `import gzip`, `import socket` and `import time` to the imports of `tests/test_observe.py`, plus `from omniroute_fakes import PASSWORD, free_port`. Then append:

```python
EVIDENCE = REPO / "tests/fixtures/omniroute-evidence/2026-10-24-omniroute-characterization-2a7a68e89fe5.json.gz"


@pytest.fixture
def fake_omni(tmp_path):
    """tests/fixtures/fake_omniroute.py on a free port; killed and reaped here, pass or fail (AGENTS.md).

    Spawned exactly like tests/omniroute_world.py does it: `sys.executable` (the interpreter pytest runs in under
    `uv run`) and the same argv. No interpreter is ever named in a command."""
    port, state = free_port(), tmp_path / "omni-state.json"
    state.write_text(json.dumps({"password": PASSWORD, "bare_rows": True, "cache_ms": 100}))
    server = subprocess.Popen([sys.executable, str(REPO / "tests/fixtures/fake_omniroute.py"),
                               "--port", str(port), "--state", str(state)], stdout=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 10
        while True:
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                break
            except OSError:
                assert time.monotonic() < deadline, "fake OmniRoute did not start"
                time.sleep(0.05)
        yield port, state
    finally:
        server.kill()
        server.wait()


def _set(state_path, **values):
    state_path.write_text(json.dumps({**json.loads(state_path.read_text()), **values}))


def test_health_counters_come_from_the_primed_second_read(fake_omni):
    port, path = fake_omni
    _set(path, health={"adaptiveAdmission": {"pressureGuardRejectCount": 1}})
    observe.omniroute_record(port, PASSWORD, {}, time.time())  # leaves a snapshot with 1 behind
    _set(path, health={"adaptiveAdmission": {"pressureGuardRejectCount": 7, "utilization": 0.25},
                       "chatAdmission": {"shedTotal": 3, "shedsByReason": {"memory": 3, "note": "x"}},
                       "uptime": 12.5})
    time.sleep(0.15)  # older than cache_ms: the prime answers the stale 1 and recomputes 7 behind it
    record = observe.omniroute_record(port, PASSWORD, {}, time.time())
    assert record["state"] == "ok"
    health = record["health"]
    assert health["guard_rejects"] == 7 and health["admission_utilization"] == 0.25
    assert health["chat_shed_total"] == 3 and health["sheds_by_reason"] == {"memory": 3}
    assert health["uptime_s"] == 12.5
    assert health["rss_bytes"] is None and health["breakers_open"] is None  # missing: unknown, never 0


def test_login_failure_is_unknown_auth_and_never_echoes_the_password(fake_omni):
    port, _ = fake_omni
    record = observe.omniroute_record(port, "wrong-password", {}, time.time())
    assert record["state"] == "unknown" and record["reason"] == "auth" and "health" not in record
    assert "wrong-password" not in json.dumps(record)


def test_omniroute_down_is_unknown_io_within_the_budget():
    t0 = time.monotonic()
    record = observe.omniroute_record(free_port(), PASSWORD, {}, time.time(), budget=2)
    assert record["state"] == "unknown" and record["reason"] == "io" and time.monotonic() - t0 < 3


def test_password_with_json_and_shell_metacharacters_logs_in(fake_omni):
    port, path = fake_omni
    tricky = 'p"a$s\\w`o\'rd {x}'
    _set(path, password=tricky)
    record = observe.omniroute_record(port, tricky, {}, time.time())
    assert record["state"] == "ok" and tricky not in json.dumps(record)


def _pointers(body):
    """The JSON pointers a recorded body provides. The committed evidence stores a body as a FLAT map keyed by pointer
    ({"/chatAdmission/shedTotal": 0, "/chatAdmission/shedsByReason": {}}); a raw nested body is walked instead, so the
    test holds for either encoding."""
    if all(key.startswith("/") for key in body):
        return set(body)
    found: set[str] = set()

    def walk(node, prefix):
        found.add(prefix)
        if isinstance(node, dict):
            for key, value in node.items():
                walk(value, f"{prefix}/{key}")

    walk(body, "")
    return found


def test_health_pointers_exist_in_the_recorded_live_shape():
    doc = json.loads(gzip.decompress(EVIDENCE.read_bytes()))  # gzip-compressed JSON, see "Evidence encoding"
    bodies = [t["body"] for t in doc["series"]["/api/monitoring/health"] if isinstance(t.get("body"), dict)]
    assert bodies, "the evidence holds no health body"
    seen: set[str] = set()
    for body in bodies:
        seen |= _pointers(body)
    assert {p for p in observe.HEALTH_FIELDS.values() if p not in seen} == set()
```

- [ ] **Step 3: Run the tests and confirm they fail**

Run: `uv run pytest tests/test_observe.py -q -k "health or login or down or password"`
Expected: FAIL with `AttributeError: ... 'omniroute_record'`.

- [ ] **Step 4: Write the minimal implementation**

In `pylib/observe.py`, add `import time` to the imports, plus:

```python
from pylib.omniroute_activity import PRIME_GAP_S, _ptr, bounded_get, bounded_login
from pylib.omniroute_update import Deferral
```

Then append:

```python
OMNIROUTE_BUDGET_S = 10.0
# Research §5 guard and traffic counters, pinned against the recorded 3.8.53 health shape by
# test_health_pointers_exist_in_the_recorded_live_shape (eventLoopStall does not exist there and is left out).
HEALTH_FIELDS = {
    "guard_rejects": "/adaptiveAdmission/pressureGuardRejectCount",
    "admission_rejected": "/adaptiveAdmission/rejectedCount",
    "admission_admitted": "/adaptiveAdmission/admittedCount",
    "admission_utilization": "/adaptiveAdmission/utilization",
    "chat_shed_total": "/chatAdmission/shedTotal",
    "chat_active_heavy": "/chatAdmission/activeHeavy",
    "chat_waiting": "/chatAdmission/waiting",
    "breakers_open": "/circuitBreakers/open",
    "breakers_half_open": "/circuitBreakers/halfOpen",
    "breakers_degraded": "/circuitBreakers/degraded",
    "quota_alerting": "/quotaMonitor/alerting",
    "quota_exhausted": "/quotaMonitor/exhausted",
    "quota_errors": "/quotaMonitor/errors",
    "codex_available": "/codexAccountPools/available",
    "codex_fully_limited": "/codexAccountPools/fullyLimited",
    "rss_bytes": "/memoryUsage/rss",
    "heap_used_bytes": "/memoryUsage/heapUsed",
    "sessions_active": "/sessions/activeCount",
    "credentials_failed": "/credentialHealth/failed",
    "credentials_stale": "/credentialHealth/stale",
    "uptime_s": "/uptime",
}


def _number(value: Any) -> int | float | None:
    """A JSON number, else None: a missing or mistyped counter is unknown, never zero."""
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def omniroute_record(port: int, password: str, state: dict[str, Any], now: float, *,
                     budget: float = OMNIROUTE_BUDGET_S,
                     sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    """OmniRoute dashboard counters for one tick, inside one deadline. The password goes only into the login body.

    `/api/monitoring/health` is stale-while-revalidate: a lone read answers the snapshot that the PREVIOUS request
    computed, which can be a whole timer interval old. So it is primed and read again after PRIME_GAP_S, as the
    healer and omniroute_activity.Collector do."""
    record: dict[str, Any] = {"v": SCHEMA, "t": int(now), "kind": "omniroute"}
    base, deadline = f"http://127.0.0.1:{port}", time.monotonic() + budget
    try:
        token = bounded_login(base, password, deadline)
        bounded_get(base, "/api/monitoring/health", token, deadline)
        sleep(PRIME_GAP_S)
        health = json.loads(bounded_get(base, "/api/monitoring/health", token, deadline))
        record["health"] = {name: _number(_ptr(health, pointer)) for name, pointer in HEALTH_FIELDS.items()}
        sheds = _ptr(health, "/chatAdmission/shedsByReason")
        if isinstance(sheds, dict):
            record["health"]["sheds_by_reason"] = {str(k)[:40]: v for k, v in sheds.items() if _number(v) is not None}
        record["state"] = "ok"
    except Deferral as exc:
        record.update(state="unknown", reason=exc.reason.value, detail=exc.detail[:120])
    except (ValueError, TypeError, AttributeError):
        record.update(state="unknown", reason="schema")
    return record
```

- [ ] **Step 5: Run the tests and confirm they pass**

Run: `uv run pytest tests/test_observe.py tests/test_omniroute_update.py tests/test_omniroute_activity.py -q`
Expected: all pass. The existing fake users are unaffected because the new keys are opt-in.

- [ ] **Step 6: Confirm the fake server left no process behind**

Run: `ps -eo pid,args | grep '[f]ake_omniroute.py' || echo none`
Expected: `none`.

- [ ] **Step 7: Run the gate**

Run: `make validate && make test`
Expected: both pass.

- [ ] **Step 8: Commit**

```bash
git add pylib/observe.py tests/fixtures/fake_omniroute.py tests/test_observe.py
git commit -m "feat(observe): OmniRoute health counters, primed then read, bounded and password-safe"
```

---

### Task 5: New client calls from the call log, behind a persisted cursor

**Files:**
- Modify: `pylib/observe.py`
- Test: `tests/test_observe.py`

**Interfaces:**
- Consumes:
  - `omniroute_activity.Profile(sha256, entry)` and its `classify_path`.
  - `omniroute_activity.PROFILE_PATH` and `omniroute_activity.epoch_ms`.
  - `observe._number` and `observe._ptr` (Task 4).
- Produces:
  - Constants: `CALL_PAGE = 100`, `CALL_PAGES = 10`, `CALL_HEAD_PAGES = 2`, `CALL_OVERLAP = 10`, `CALL_LOOKBACK_MS = 900_000`, `CALL_BACKLOG_MAX_MS = 3_600_000`, `TOP_KEYS = 20`.
  - `observe.shipped_profile(path=PROFILE_PATH) -> Profile`.
  - `observe.new_calls(fetch: Callable[[str], Any], cursor: Mapping, now_ms: int, *, profile=None, page=CALL_PAGE, pages=CALL_PAGES, head_pages=CALL_HEAD_PAGES, overlap=CALL_OVERLAP) -> tuple[dict, dict]`. The cursor has the shape `{"floor": int, "seen": {key: ms}, "resume": int}`; `resume` is the list offset where the unread backlog of a burst begins (0: none).
  - `omniroute_record` now also writes `record["calls"]` (a summary, or `None` plus `record["calls_reason"]` when the call-log source failed) and `state["calls"]`.
- **Paging model (source-verified in `omniroute_activity.Collector._call_logs`):** `/api/usage/call-logs?limit&offset` returns every in-memory row on every page, then the persisted rows `ORDER BY timestamp DESC LIMIT limit OFFSET offset`. Rows never move up: new rows push older ones to a larger offset, a late-persisted row is inserted at its timestamp position, and retention deletes only the oldest rows.
- **Catch-up bound:** a tick reads at most `CALL_PAGES x CALL_PAGE = 1,000` persisted rows. The newest `CALL_HEAD_PAGES x CALL_PAGE = 200` are re-read every tick (late-persisted rows), and the walk continues past them only while it has not met a row seen before. A burst that exhausts the budget stores `resume`; each later tick reads the head, then resumes the backlog at `resume + <new head rows> - CALL_OVERLAP`, so it catches up at least `(CALL_PAGES - CALL_HEAD_PAGES) x CALL_PAGE - CALL_OVERLAP = 790` backlog rows per tick on top of the new head rows (the overlap re-reads rows whose ids are already seen, which makes a late shift harmless). The overlap must stay far below that budget or the backlog would not advance. While a backlog exists the record is `truncated`, and the floor is held for at most `CALL_BACKLOG_MAX_MS` (1 h): a backlog older than that is dropped, still flagged `truncated` until it is gone.

- [ ] **Step 1: Write the failing tests**

Add `import urllib.parse` to the imports of `tests/test_observe.py`, then append:

```python
NOW_MS = 1_792_850_000_000


def _iso(ms):
    return datetime.datetime.fromtimestamp(ms / 1000, datetime.UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _row(row_id, ms, path="/v1/chat/completions", **extra):
    return {"id": row_id, "timestamp": _iso(ms), "path": path, "status": 200, "duration": 100,
            "comboName": "my-coding", "model": "glm-5.3", "provider": "zai", "tokens": {"in": 10, "out": 5}, **extra}


def _pages(rows):
    """fetch() over `rows` the way the real endpoint pages them (in-memory rows on every page)."""
    def fetch(path):
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)
        limit, offset = int(query["limit"][0]), int(query["offset"][0])
        memory = [r for r in rows if isinstance(r.get("active"), bool)]
        persisted = [r for r in rows if not isinstance(r.get("active"), bool)]
        return memory + persisted[offset:offset + limit]
    return fetch


def _evidence_rows():
    """The recorded row table as rows, newest first. Each `row_table[<hash>]` is a flat map keyed by JSON pointer
    ({"/id": "...", "/tokens/in": 10}); it is nested back here, and a raw nested shape passes through unchanged."""
    doc = json.loads(gzip.decompress(EVIDENCE.read_bytes()))
    rows = []
    for shape in doc["row_table"].values():
        row: dict = {}
        for pointer, value in shape.items():
            *parents, leaf = pointer.strip("/").split("/")
            target = row
            for part in parents:
                target = target.setdefault(part, {})
            target[leaf] = value
        rows.append(row)
    return sorted(rows, key=lambda r: r["timestamp"], reverse=True)


def test_first_run_seeds_the_cursor_without_counting_history():
    summary, cursor = observe.new_calls(_pages([_row("a", NOW_MS - 5000)]), {}, NOW_MS)
    assert summary["seeded"] and summary["calls"] == 0 and cursor["floor"] == NOW_MS


def test_new_rows_are_counted_once_across_ticks():
    rows = [_row("b", NOW_MS + 30_000), _row("a", NOW_MS + 10_000), _row("old", NOW_MS - 1)]
    summary, cursor = observe.new_calls(_pages(rows), {"floor": NOW_MS, "seen": {}}, NOW_MS + 60_000)
    assert summary["calls"] == 2 and summary["status"] == {"2xx": 2} and not summary["seeded"]
    again, cursor = observe.new_calls(_pages(rows), cursor, NOW_MS + 120_000)
    assert again["calls"] == 0
    late = sorted([_row("c", NOW_MS + 20_000), *rows], key=lambda r: r["timestamp"], reverse=True)
    later, cursor = observe.new_calls(_pages(late), cursor, NOW_MS + 180_000)  # persisted after a newer row
    assert later["calls"] == 1
    json.dumps(cursor)


def test_in_memory_rows_only_count_as_in_flight_and_the_persisted_twin_counts_once():
    rows = [_row("1792850001000-ab-1", NOW_MS + 1000, active=True, status=0),
            _row("1792850002000-cd-2", NOW_MS + 2000, active=False, completed=True, detailState="in-memory"),
            _row("uuid-of-2", NOW_MS + 3000, detailState="ready")]
    summary, _ = observe.new_calls(_pages(rows), {"floor": NOW_MS, "seen": {}}, NOW_MS + 60_000)
    assert summary["calls"] == 1 and summary["in_flight"] == 1


def test_probes_unclassified_and_unreadable_rows_are_kept_apart():
    rows = [_row("p", NOW_MS + 3000, path="/api/providers/test"), _row("u", NOW_MS + 2000, path="/v1/embeddings"),
            {**_row("x", NOW_MS + 1000), "timestamp": "yesterday"}]
    summary, _ = observe.new_calls(_pages(rows), {"floor": NOW_MS, "seen": {}}, NOW_MS + 60_000)
    assert (summary["calls"], summary["probes"], summary["unclassified"], summary["unreadable"]) == (0, 1, 1, 1)


def test_summary_reads_shapes_only_never_content():
    rows = [_row("a", NOW_MS + 2000, status=503, duration=900, tokens={"in": 0, "out": 0},
                 error="PROMPT-MARKER boom", apiKeyName="KEY-MARKER",
                 request={"messages": [{"content": "PROMPT-MARKER"}]}),
            _row("b", NOW_MS + 1000, comboName=None, tokens={"in": 7, "out": 3, "cacheRead": 4, "reasoning": 2})]
    summary, _ = observe.new_calls(_pages(rows), {"floor": NOW_MS, "seen": {}}, NOW_MS + 60_000)
    assert summary["status"] == {"5xx": 1, "2xx": 1} and summary["by_combo"] == {"my-coding": 1, "none": 1}
    assert (summary["duration_ms_sum"], summary["duration_ms_max"], summary["duration_known"]) == (1000, 900, 2)
    assert (summary["tokens_in"], summary["tokens_out"], summary["tokens_cache_read"],
            summary["tokens_reasoning"]) == (7, 3, 4, 2)
    assert summary["tokens_known"] == 1  # 0/0 is unknown usage, not a free call
    assert "MARKER" not in json.dumps(summary)


BOUND = {"page": 10, "pages": 3, "head_pages": 1, "overlap": 2}  # the SAME bound on every tick: 30 rows per tick


def _burst(count, start=50_000):
    """`count` persisted rows, newest first (timestamps strictly decreasing)."""
    return [_row(f"r{i}", NOW_MS + start - i) for i in range(count)]


def test_a_burst_beyond_the_page_bound_is_caught_up_exactly_once_with_the_same_bound():
    rows = _burst(45)  # 45 rows against a 30-row budget
    first, cursor = observe.new_calls(_pages(rows), {"floor": NOW_MS, "seen": {}}, NOW_MS + 60_000, **BOUND)
    assert first["truncated"] and first["calls"] == 30 and cursor["resume"] == 30
    second, cursor = observe.new_calls(_pages(rows), cursor, NOW_MS + 120_000, **BOUND)
    assert second["calls"] == 15 and not second["truncated"] and cursor["resume"] == 0  # 30 + 15 = 45, none twice
    third, _ = observe.new_calls(_pages(rows), cursor, NOW_MS + 180_000, **BOUND)
    assert third["calls"] == 0 and not third["truncated"]


def test_a_burst_catch_up_survives_new_rows_arriving_meanwhile():
    rows = _burst(45)
    _, cursor = observe.new_calls(_pages(rows), {"floor": NOW_MS, "seen": {}}, NOW_MS + 60_000, **BOUND)
    newer = [_row(f"n{i}", NOW_MS + 58_000 - i) for i in range(4)]  # 4 rows persisted after the first tick
    rows = newer + rows
    second, cursor = observe.new_calls(_pages(rows), cursor, NOW_MS + 120_000, **BOUND)
    assert second["calls"] == 4 + 15 and cursor["resume"] == 0  # the backlog moved 4 deeper; none lost, none repeated
    again, _ = observe.new_calls(_pages(rows), cursor, NOW_MS + 180_000, **BOUND)
    assert again["calls"] == 0


def test_a_long_burst_drains_over_several_ticks_and_every_row_counts_once():
    rows, cursor, total = _burst(95), {"floor": NOW_MS, "seen": {}}, 0
    for tick in range(1, 6):  # 30 rows on the first tick, then 18 backlog rows (2 pages x 10 - overlap 2) per tick
        summary, cursor = observe.new_calls(_pages(rows), cursor, NOW_MS + 60_000 * tick, **BOUND)
        total += summary["calls"]
        if not summary["truncated"]:
            break
    assert total == 95 and cursor["resume"] == 0


def test_rows_without_an_id_are_unreadable_and_counted_once():
    nameless = {**_row("x", NOW_MS + 2000), "id": None}
    rows = [_row("a", NOW_MS + 3000), nameless, {**_row("y", NOW_MS + 1000), "id": ""}]
    first, cursor = observe.new_calls(_pages(rows), {"floor": NOW_MS, "seen": {}}, NOW_MS + 60_000)
    assert (first["calls"], first["unreadable"]) == (1, 2)  # not silently dropped, not counted as calls
    again, _ = observe.new_calls(_pages(rows), cursor, NOW_MS + 120_000)
    assert (again["calls"], again["unreadable"]) == (0, 0)  # an unreadable row is not re-counted every tick


def test_recorded_call_log_rows_count_each_persisted_call_exactly_once():
    rows = _evidence_rows()
    newest = max(observe.epoch_ms(r["timestamp"]) for r in rows)
    summary, _ = observe.new_calls(_pages(rows), {"floor": 0, "seen": {}}, newest + 1000)
    # 279 persisted rows: 273 client calls (/v1/chat/completions 271, /v1/messages 1, /v1/responses 1),
    # 5 /api/providers/test probes, 1 /v1/embeddings (no shipped route class). The 1,169 in-memory rows of the
    # same calls (active true/false, detailState in-memory) count zero times.
    assert (summary["calls"], summary["probes"], summary["unclassified"]) == (273, 5, 1)


def test_omniroute_record_summarises_new_calls_and_keeps_the_cursor(fake_omni):
    port, path = fake_omni
    state: dict = {}
    now = time.time()
    first = observe.omniroute_record(port, PASSWORD, state, now)
    now_ms = int(now * 1000)
    assert first["calls"]["seeded"] and state["calls"]["floor"] == now_ms
    _set(path, rows=[_row("live", now_ms + 5000, active=True, status=0), _row("p2", now_ms + 4000),
                     _row("p1", now_ms + 2000, path="/api/providers/test")])
    second = observe.omniroute_record(port, PASSWORD, state, now + 60)
    assert (second["calls"]["calls"], second["calls"]["probes"], second["calls"]["in_flight"]) == (1, 1, 1)
    assert PASSWORD not in json.dumps([first, second, state])


def test_a_failed_call_log_source_is_unknown_not_an_empty_summary(fake_omni):
    port, path = fake_omni
    state: dict = {}
    now = time.time()
    observe.omniroute_record(port, PASSWORD, state, now)  # seeds the cursor
    cursor = json.loads(json.dumps(state["calls"]))
    _set(path, bare_rows=False, rows=[_row("p1", int(now * 1000) + 2000)])  # not a bare list: the page is unreadable
    record = observe.omniroute_record(port, PASSWORD, state, now + 60)
    assert record["state"] == "ok" and record["health"]["chat_waiting"] == 0  # health was still read
    assert record["calls"] is None and record["calls_reason"] == "schema"  # unknown, not {"calls": 0, ...}
    assert state["calls"] == cursor  # the cursor did not move: the row is counted once the source works again
    _set(path, bare_rows=True)
    assert observe.omniroute_record(port, PASSWORD, state, now + 120)["calls"]["calls"] == 1
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `uv run pytest tests/test_observe.py -q -k "calls or call_log or rows or seeds or burst or probes or shapes"`
Expected: FAIL with `AttributeError: ... 'new_calls'`.

- [ ] **Step 3: Write the minimal implementation**

In `pylib/observe.py`, extend the `omniroute_activity` import to:

```python
from pylib.omniroute_activity import PRIME_GAP_S, PROFILE_PATH, Profile, _ptr, bounded_get, bounded_login, epoch_ms
```

Then append:

```python
CALL_PAGE = 100
CALL_PAGES = 10  # at most 1,000 persisted rows read per tick
CALL_HEAD_PAGES = 2  # the newest 200 persisted rows are re-read every tick, so a late-persisted row is still found
CALL_OVERLAP = 10  # the backlog resumes this many rows early: ids already seen absorb any shift
CALL_LOOKBACK_MS = 15 * 60 * 1000  # how long a counted id is remembered
CALL_BACKLOG_MAX_MS = 60 * 60 * 1000  # a burst backlog older than this is dropped (and stays flagged truncated)
TOP_KEYS = 20


def shipped_profile(path: Path = PROFILE_PATH) -> Profile:
    """Route classes and call-log field names from every shipped activity profile, merged.

    shortcut: not bound to the running image's config id (that takes a podman image inspect per tick); a path no
    shipped profile classifies is counted as `unclassified`, never as a client call. Bind it if an image moves routes."""
    entries = json.loads(Path(path).read_text())["profiles"]
    return Profile("", {"routes": [r for e in entries for r in e["routes"]], "api_fields": entries[-1]["api_fields"]})


def _bump(counts: dict[str, int], key: Any) -> None:
    name = str(key)[:80] if key not in (None, "") else "none"
    if name not in counts and len(counts) >= TOP_KEYS:
        name = "other"
    counts[name] = counts.get(name, 0) + 1


def _count_call(summary: dict[str, Any], row: Mapping[str, Any]) -> None:
    """Fold one client call into the tick summary. Reads only these fields, never `error`, bodies or key names."""
    summary["calls"] += 1
    status = row.get("status")
    _bump(summary["status"], f"{status // 100}xx" if isinstance(status, int) and 100 <= status <= 599 else "other")
    _bump(summary["by_combo"], row.get("comboName"))
    _bump(summary["by_model"], row.get("model"))
    _bump(summary["by_provider"], row.get("provider"))
    duration = _number(row.get("duration"))
    if duration is not None:
        summary["duration_known"] += 1  # the mean latency divides by this, not by `calls`
        summary["duration_ms_sum"] += duration
        summary["duration_ms_max"] = max(summary["duration_ms_max"] or 0, duration)
    tokens = row.get("tokens") if isinstance(row.get("tokens"), dict) else {}
    if _number(tokens.get("in")) or _number(tokens.get("out")):
        summary["tokens_known"] += 1  # 0/0 means unknown usage, not a free call (research §6)
    for name, key in (("tokens_in", "in"), ("tokens_out", "out"), ("tokens_cache_read", "cacheRead"),
                      ("tokens_reasoning", "reasoning")):
        summary[name] += _number(tokens.get(key)) or 0


def new_calls(fetch: Callable[[str], Any], cursor: Mapping[str, Any], now_ms: int, *, profile: Profile | None = None,
              page: int = CALL_PAGE, pages: int = CALL_PAGES, head_pages: int = CALL_HEAD_PAGES,
              overlap: int = CALL_OVERLAP) -> tuple[dict[str, Any], dict[str, Any]]:
    """(summary of client calls persisted since the cursor, next cursor). Counts and shapes only.

    Measured in the recorded 3.8.53 evidence (row_table of
    tests/fixtures/omniroute-evidence/2026-10-24-omniroute-characterization-2a7a68e89fe5.json.gz): a call first shows
    up as in-memory rows (boolean `active`, id `<ms>-<hex>-<n>`), then as ONE persisted row with a new UUID id, no
    `active` field, and its completion time as `timestamp`. So only persisted rows are counted, keyed by id; in-memory
    rows only give the in-flight count. Every page lists the in-memory rows first, then persisted rows newest first
    (`ORDER BY timestamp DESC LIMIT/OFFSET`), so new rows push older ones to a larger offset.

    The cursor is `{"floor", "seen", "resume"}`. `seen` holds the keys counted at or after `floor`, so a row persisted
    late is still counted exactly once. The first run only seeds the floor: calls before the collector started are not
    counted. A row without an id, or with an unreadable timestamp, is counted once as `unreadable` (never dropped,
    never a call); an id-less row is keyed by its timestamp, path, status and duration.

    A tick reads at most `pages` pages. It always reads the newest `head_pages` pages, and goes on only while it has
    not met a key an earlier tick counted. If the budget runs out first (a burst), `resume` stores the offset where
    the unread rows begin and the record is `truncated`. Every later tick re-reads the head, then resumes at
    `resume + <new head rows> - overlap`: rows only move down, so that start is never past the first unread row, and
    the overlap re-reads keys already seen. Each row is therefore counted once, whatever the number of ticks the burst
    needs. While a backlog exists the floor is held for at most CALL_BACKLOG_MAX_MS."""
    profile = profile or shipped_profile()
    f = profile.api_fields
    seeded = not isinstance(cursor.get("floor"), int)
    floor: int = now_ms if seeded else cursor["floor"]
    resume = 0 if seeded or not isinstance(cursor.get("resume"), int) else max(0, cursor["resume"])
    seen: dict[str, int] = {k: v for k, v in (cursor.get("seen") or {}).items() if isinstance(v, int)}
    known = set(seen)  # keys counted by earlier ticks: where the head walk meets one, it has caught up
    summary: dict[str, Any] = {"calls": 0, "status": {}, "by_combo": {}, "by_model": {}, "by_provider": {},
                               "duration_ms_sum": 0, "duration_ms_max": None, "duration_known": 0, "tokens_in": 0,
                               "tokens_out": 0, "tokens_cache_read": 0, "tokens_reasoning": 0, "tokens_known": 0,
                               "probes": 0, "unclassified": 0, "unreadable": 0, "in_flight": 0, "seeded": seeded,
                               "truncated": False}

    def read(offset: int, head: bool) -> tuple[int, int | None, str | None]:
        """Count the unseen rows of the page at `offset`; return (persisted rows on it, ts and key of its oldest row)."""
        rows = _ptr(fetch(f"/api/usage/call-logs?limit={page}&offset={offset}"), f["rows"])
        if not isinstance(rows, list):
            raise ValueError("call-log page is not a list")
        persisted, oldest, oldest_key = 0, None, None
        for row in rows:
            if not isinstance(row, dict):
                continue
            if isinstance(row.get(f["row_memory_flag"]), bool):
                if head and offset == 0 and row[f["row_memory_flag"]] is True:
                    summary["in_flight"] += 1
                continue
            persisted += 1
            try:
                ts: int | None = epoch_ms(row.get(f["row_timestamp"]))
            except (ValueError, TypeError):
                ts = None
            path = row.get(f["row_path"], "")
            key = str(row.get("id") or "") or f"~{ts}|{path}|{row.get('status')}|{row.get('duration')}"
            if ts is not None:
                oldest, oldest_key = ts, key
                if ts < floor:
                    continue
            if key in seen:
                continue
            seen[key] = now_ms if ts is None else ts
            if ts is None or not row.get("id"):
                summary["unreadable"] += 1
                continue
            kind = profile.classify_path(path)
            if kind == "probe":
                summary["probes"] += 1
            elif kind != "inference":
                summary["unclassified"] += 1
            else:
                _count_call(summary, row)
        return persisted, oldest, oldest_key

    offset = used = 0
    ended = exhausted = False
    while True:  # the head: the newest rows, then on only while no key counted by an earlier tick has been met
        persisted, oldest, oldest_key = read(offset, head=True)
        used += 1
        offset += persisted
        if persisted < page:
            ended = True
            break
        if used >= head_pages and oldest is not None and (oldest < floor or oldest_key in known):
            break
        if used >= pages:
            exhausted = True
            break
    head_new = len(seen) - len(known)  # rows counted above the old backlog: they pushed it down by that many
    if exhausted:
        resume = offset  # a burst: the rows below `offset` are unread
    elif ended or (oldest is not None and oldest < floor):
        resume = 0  # the walk reached the last row or the floor: nothing unread is left below it
    elif resume:
        start = resume + head_new
        offset, resume = max(0, start - overlap), start
        while used < pages:
            persisted, oldest, _ = read(offset, head=False)
            used += 1
            offset += persisted
            resume = offset
            if persisted < page or (oldest is not None and oldest < floor):
                resume = 0
                break
    summary["truncated"] = resume > 0
    next_floor = max(floor, now_ms - (CALL_BACKLOG_MAX_MS if resume else CALL_LOOKBACK_MS))
    return summary, {"floor": next_floor, "seen": {k: v for k, v in seen.items() if v >= next_floor}, "resume": resume}
```

In `omniroute_record`, insert before `record["state"] = "ok"`:

```python
        try:
            record["calls"], state["calls"] = new_calls(
                lambda path: json.loads(bounded_get(base, path, token, deadline)), state.get("calls") or {},
                int(now * 1000))
        except (Deferral, ValueError, TypeError, AttributeError) as exc:
            # Health was read, the call log was not: the calls are unknown, never an empty summary, and the cursor
            # stays where it was so the same rows are counted (once) when the source works again.
            record["calls"] = None
            record["calls_reason"] = exc.reason.value if isinstance(exc, Deferral) else "schema"
```

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `uv run pytest tests/test_observe.py -q`
Expected: all pass. Then run `ps -eo pid,args | grep '[f]ake_omniroute.py' || echo none` and expect `none`.

- [ ] **Step 5: Run the gate**

Run: `make validate && make test`
Expected: both pass.

- [ ] **Step 6: Commit**

```bash
git add pylib/observe.py tests/test_observe.py
git commit -m "feat(observe): count new persisted OmniRoute client calls once behind a cursor (shapes only)"
```

---

### Task 6: Laya request counts from its journald access log

**Files:**
- Modify: `pylib/observe.py`
- Test: `tests/test_observe.py`

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `observe.JOURNAL_TIMEOUT_S = 5` and `observe.LAYA_LINES = 5000`.
  - `observe.laya_record(state, now, *, container="system-one", run=subprocess.run) -> dict`. It keeps `state["laya"] = {"cursor": str}`.
  - The test helper `FakeRun(stdout="", rc=0, exc=None)` exposes `.argv` and asserts `timeout == JOURNAL_TIMEOUT_S`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_observe.py`:

```python
class FakeRun:
    """subprocess.run stand-in for journalctl: records argv, returns `stdout`/`rc` or raises `exc`."""

    def __init__(self, stdout="", rc=0, exc=None):
        self.stdout, self.rc, self.exc, self.argv = stdout, rc, exc, None

    def __call__(self, argv, **kwargs):
        assert kwargs["timeout"] == observe.JOURNAL_TIMEOUT_S
        self.argv = argv
        if self.exc:
            raise self.exc
        return SimpleNamespace(returncode=self.rc, stdout=self.stdout)


ACCESS = ('INFO:     10.89.0.4:51234 - "GET /health HTTP/1.1" 200 OK\n'
          'INFO:     10.89.0.4:51236 - "POST /v1/systemone HTTP/1.1" 200 OK\n'
          'INFO:     10.89.0.4:51238 - "POST /v1/systemone/batch HTTP/1.1" 200 OK\n'
          'INFO:     10.89.0.4:51240 - "POST /v1/systemone HTTP/1.1" 400 Bad Request\n'
          'INFO:     10.89.0.4:51242 - "GET /metrics HTTP/1.1" 404 Not Found\n'
          'WARNING  question "PROMPT-MARKER" was long\n')


def test_laya_first_tick_seeds_the_cursor_without_counting():
    run, state = FakeRun("-- cursor: s=1\n"), {}
    record = observe.laya_record(state, T, run=run)
    assert run.argv[-2:] == ["-n", "0"] and record["seeded"] and record["requests"] == {}
    assert state["laya"] == {"cursor": "s=1"}


def test_laya_counts_requests_by_status_class_after_the_cursor():
    run, state = FakeRun(ACCESS + "-- cursor: s=2\n"), {"laya": {"cursor": "s=1"}}
    record = observe.laya_record(state, T, run=run)
    assert run.argv[run.argv.index("--after-cursor") + 1] == "s=1" and "CONTAINER_NAME=system-one" in run.argv
    assert record["requests"] == {"2xx": 2, "4xx": 1}
    assert (record["health_checks"], record["other_paths"], record["seeded"]) == (1, 1, False)
    assert state["laya"] == {"cursor": "s=2"} and "MARKER" not in json.dumps(record)


def test_laya_without_new_lines_keeps_its_cursor():
    state = {"laya": {"cursor": "s=1"}}
    assert observe.laya_record(state, T, run=FakeRun(""))["requests"] == {}
    assert state["laya"] == {"cursor": "s=1"}


def test_laya_invalid_cursor_reseeds_and_a_hung_journal_is_unknown():
    state = {"laya": {"cursor": "s=vacuumed"}}
    assert observe.laya_record(state, T, run=FakeRun(rc=1))["state"] == "unknown" and state["laya"] == {}
    state = {"laya": {"cursor": "s=1"}}
    hung = FakeRun(exc=subprocess.TimeoutExpired(["journalctl"], 5))
    assert observe.laya_record(state, T, run=hung)["reason"] == "journal" and state["laya"] == {"cursor": "s=1"}
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `uv run pytest tests/test_observe.py -q -k laya`
Expected: FAIL with `AttributeError: module 'pylib.observe' has no attribute 'JOURNAL_TIMEOUT_S'`.

- [ ] **Step 3: Write the minimal implementation**

In `pylib/observe.py`, add `import subprocess` to the imports, then append:

```python
JOURNAL_TIMEOUT_S = 5
LAYA_LINES = 5000
LAYA_ACCESS = re.compile(r'"[A-Z]+ (/[^ "?]*)[^"]*" (\d{3})')  # uvicorn access line: "POST /v1/systemone HTTP/1.1" 200


def laya_record(state: dict[str, Any], now: float, *, container: str = "system-one",
                run: Callable[..., Any] = subprocess.run) -> dict[str, Any]:
    """Laya requests since the last tick, by status class, from its journald access log: one bounded
    `journalctl --user` call, no exec into the container (measured 2026-10-24: about 5 ms). Laya's /health has no
    request counter, and Phoenix spans carry content, so the access log is the cheap content-free source. Only the
    path and status of access lines are matched; no line is stored."""
    record: dict[str, Any] = {"v": SCHEMA, "t": int(now), "kind": "laya"}
    cursor = (state.get("laya") or {}).get("cursor")
    argv = ["journalctl", "--user", f"CONTAINER_NAME={container}", "--no-pager", "-o", "cat", "--show-cursor"]
    argv += ["--after-cursor", cursor, "-n", str(LAYA_LINES)] if isinstance(cursor, str) else ["-n", "0"]
    try:
        out = run(argv, capture_output=True, text=True, timeout=JOURNAL_TIMEOUT_S, check=False)
    except (OSError, subprocess.SubprocessError):
        return {**record, "state": "unknown", "reason": "journal"}
    if out.returncode != 0:
        state["laya"] = {}  # e.g. a cursor the journal vacuumed (journalctl exits 1): reseed on the next tick
        return {**record, "state": "unknown", "reason": "journal"}
    lines = out.stdout.splitlines()
    if lines and lines[-1].startswith("-- cursor: "):
        state["laya"] = {"cursor": lines.pop().removeprefix("-- cursor: ")}
    requests: dict[str, int] = {}
    health = other = 0
    for line in lines:
        match = LAYA_ACCESS.search(line)
        if not match:
            continue
        path, code = match.groups()
        if path == "/health":
            health += 1
        elif path.startswith("/v1/systemone"):
            requests[f"{code[0]}xx"] = requests.get(f"{code[0]}xx", 0) + 1
        else:
            other += 1
    return {**record, "state": "ok", "seeded": not isinstance(cursor, str), "requests": requests,
            "health_checks": health, "other_paths": other, "truncated": len(lines) >= LAYA_LINES}
```

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `uv run pytest tests/test_observe.py -q -k laya`
Expected: 4 passed.

- [ ] **Step 5: Verify read-only on this host (no content printed)**

Run:
```bash
journalctl --user CONTAINER_NAME=system-one -n 0 --show-cursor -o cat --no-pager | sed -E 's/(cursor: s=).{8}.*/\1…/'
journalctl --user CONTAINER_NAME=system-one --since -2h -o cat --no-pager | grep -oE '"[A-Z]+ /[^ "?]*' | sort | uniq -c
```
Expected: the first command prints only one `-- cursor: s=…` line. The second prints counts per method and path only, such as `/health` and `/v1/systemone`. If either differs, stop and report it (AGENTS.md "Research Before Implementation").

- [ ] **Step 6: Run the gate**

Run: `make validate && make test`
Expected: both pass.

- [ ] **Step 7: Commit**

```bash
git add pylib/observe.py tests/test_observe.py
git commit -m "feat(observe): Laya request counts from its journald access log behind a cursor"
```

---

### Task 7: `run_observe` tick and the `observe` subcommand

**Files:**
- Modify: `pylib/observe.py`
- Modify: `llmenv.py`: add `cmd_observe`, and register `sub.add_parser("observe")` in `build_parser` after `resource-override`.
- Test: `tests/test_observe.py`

**Interfaces:**
- Consumes: all of Tasks 1-6, plus `llmenv.limits_for`, `load_config`, `require_valid_config`, `host_resources` and `ConfigError` (already imported in `llmenv.py`).
- Produces:
  - `observe.TICK_DEADLINE_S = 30.0`.
  - `observe.run_observe(limits, *, omniroute_port: int, omniroute_password: str, pci: str | None, system_one: bool, store=None, now=None, statvfs=os.statvfs, locator=None, journal=subprocess.run, omniroute=omniroute_record, deadline_s=TICK_DEADLINE_S) -> dict`. It returns one of:
    - `{"ok": True, "records": n, "ms": n, "podman": n}`;
    - `{"ok": True, "skipped": "..."}`;
    - `{"ok": True, "records": 0, "guard": "...", "pruned": n}`.
  - Per-tick order (the crash-consistency protocol): lock, load state, disk guard (suspends appending only; retention still runs), `_recover` a `pending` tick a killed run left, run the sources concurrently under one deadline, stamp `life` on the OmniRoute record, **save state with `pending`** (new cursors and the tick's records, one atomic write), append the records, **save state without `pending`**, prune (a failure is ignored).
  - Source budgets: containers share the 30 s tick deadline, OmniRoute has its own 10 s deadline (Task 4), the Laya journal call 5 s (Task 6). Worst-case wall time is 30 s plus two small state writes, inside `TimeoutStartSec=45s`. A source still running at the deadline becomes `{"state":"error","error":"TimeoutError"}`, its cursors are not merged, and the finished sources are still appended.
  - CLI `uv run llmenv.py observe` prints one compact JSON line and always exits 0. On failure the line is `{"ok":false,"error":"config"}` (a `ConfigError`) or `{"ok":false,"error":"internal"}` (anything else), never a message.
  - Test helpers: `KW`, `_fake_omni`, `_quiet_locator(tmp_path)`, `Crash`, `_calls_source(rows)` and `_tick(...)`.

- [ ] **Step 1: Write the failing tests**

Add `import threading` to the imports of `tests/test_observe.py`, then append:

```python
KW = {"omniroute_port": 1, "omniroute_password": "pw", "pci": None}


def _fake_omni(port, password, state, now):
    return {"v": 1, "t": int(now), "kind": "omniroute", "state": "ok"}


def _quiet_locator(tmp_path):
    return observe.CachedLocator({}, locate=lambda name: None, proc=tmp_path, root=tmp_path)


def _limits():
    return {s: dict(p) for s, p in DEFAULT_POLICY.items()}


def test_run_observe_appends_one_tick_and_saves_state(tmp_path):
    store = tmp_path / "observe"
    result = observe.run_observe(_limits(), **KW, system_one=True, store=store, now=T, statvfs=_fs(341, 806),
                                 locator=_quiet_locator(tmp_path), journal=FakeRun("-- cursor: s=1\n"),
                                 omniroute=_fake_omni)
    assert result["ok"] and result["records"] == 6 and result["podman"] == 4  # 4 containers + omniroute + laya
    records, bad = observe.read_records(store, T - 1)
    assert [r["kind"] for r in records].count("container") == 4 and bad == 0
    state = observe.load_state(store)
    assert state["laya"] == {"cursor": "s=1"} and "pending" not in state


def test_the_omniroute_record_carries_the_container_lifetime(tmp_path):
    root, started = tmp_path / "cg", 1_792_800_000.0
    locate = FakeLocate({"omniroute": (fake_cgroup(root / REL), 4242, started)})
    locator = observe.CachedLocator({}, locate=locate, proc=_proc(tmp_path, 4242, REL, start=1000), root=root)
    store = tmp_path / "observe"
    observe.run_observe(_limits(), **KW, system_one=False, store=store, now=started + 100, statvfs=_fs(341, 806),
                        locator=locator, omniroute=_fake_omni)
    records, _ = observe.read_records(store, started)
    assert next(r for r in records if r["kind"] == "omniroute")["life"] == [CID[:12], int(started)]


def test_disk_guard_stops_appending_logs_once_and_still_prunes(tmp_path, capsys):
    store = observe.ensure_store(tmp_path / "observe")
    expired, recent = _day_file(store, "2025-10-01", size=50), _day_file(store, "2026-10-23", size=50)
    args = {**KW, "system_one": False, "store": store, "locator": _quiet_locator(tmp_path), "omniroute": _fake_omni}
    first = observe.run_observe({}, **args, now=T, statvfs=_fs(90, 806))
    assert first["records"] == 0 and first["pruned"] == 1  # retention keeps running while the guard is active
    assert not expired.exists() and recent.exists()
    assert observe.run_observe({}, **args, now=T + 60, statvfs=_fs(90, 806))["records"] == 0
    assert not (store / "2026-10-24.jsonl").exists()  # nothing was appended
    assert capsys.readouterr().err.count("disk guard") == 1
    observe.run_observe(_limits(), **args, now=T + 120, statvfs=_fs(341, 806))
    assert "disk guard cleared" in capsys.readouterr().err and (store / "2026-10-24.jsonl").exists()


def test_a_failing_source_is_an_error_record_not_a_crash(tmp_path):
    def broken(*args):
        raise RuntimeError("upstream exploded")

    store = tmp_path / "observe"
    result = observe.run_observe({}, **KW, system_one=False, store=store, now=T, statvfs=_fs(341, 806),
                                 locator=_quiet_locator(tmp_path), omniroute=broken)
    records, _ = observe.read_records(store, T - 1)
    assert result["ok"]
    assert {(r["kind"], r.get("error")) for r in records} == {("container", "KeyError"), ("omniroute", "RuntimeError")}
    assert "upstream exploded" not in json.dumps(records)  # the type only, never the message


def test_a_concurrent_run_skips_without_writing(tmp_path):
    store = observe.ensure_store(tmp_path / "observe")
    with observe.store_lock(store):
        result = observe.run_observe({}, **KW, system_one=False, store=store, now=T, statvfs=_fs(341, 806),
                                     omniroute=_fake_omni)
    assert result["skipped"] and not list(store.glob("*.jsonl"))


def test_a_slow_container_discovery_cannot_starve_the_other_sources(tmp_path):
    release = threading.Event()

    def hung_podman(name):
        release.wait(10)  # a wedged `podman inspect`; released in `finally` so no thread outlives the test
        return None

    store = tmp_path / "observe"
    locator = observe.CachedLocator({}, locate=hung_podman, proc=tmp_path, root=tmp_path)
    started = time.monotonic()
    try:
        result = observe.run_observe(_limits(), **KW, system_one=True, store=store, now=T, statvfs=_fs(341, 806),
                                     locator=locator, journal=FakeRun("-- cursor: s=1\n"), omniroute=_fake_omni,
                                     deadline_s=0.5)
    finally:
        release.set()
    assert time.monotonic() - started < 5 and result["ok"] and result["records"] == 3
    records, _ = observe.read_records(store, T - 1)
    assert {(r["kind"], r["state"], r.get("error")) for r in records} == {
        ("container", "error", "TimeoutError"), ("omniroute", "ok", None), ("laya", "ok", None)}
    state = observe.load_state(store)
    assert state["laya"] == {"cursor": "s=1"} and "cpu" not in state  # finished sources saved, the slow one did not move


class Crash(BaseException):
    """A kill, not an error: nothing in run_observe may swallow it."""


def _calls_source(rows):
    """An omniroute stand-in that runs the real call cursor (Task 5) over fixed rows."""
    def source(port, password, state, now):
        summary, state["calls"] = observe.new_calls(_pages(rows), state.get("calls") or {}, int(now * 1000))
        return {"v": 1, "t": int(now), "kind": "omniroute", "state": "ok", "calls": summary}
    return source


def _tick(tmp_path, store, now, rows):
    return observe.run_observe(_limits(), **KW, system_one=False, store=store, now=now, statvfs=_fs(341, 806),
                               locator=_quiet_locator(tmp_path), omniroute=_calls_source(rows))


def _calls_total(store):
    records, _ = observe.read_records(store, 0)
    return sum((r.get("calls") or {}).get("calls", 0) for r in records if r["kind"] == "omniroute")


@pytest.mark.parametrize("killed_at", ["after-the-state-commit", "after-the-append"])
def test_a_killed_tick_neither_loses_nor_double_counts(tmp_path, monkeypatch, killed_at):
    store, ms = tmp_path / "observe", int(T * 1000)
    rows = [_row("b", ms + 40_000), _row("a", ms + 20_000)]  # two calls persisted during the tick at T + 60
    _tick(tmp_path, store, T, [])  # seeds the call cursor at T
    real_save, saves = observe.save_state, []

    def kill_before_append(*args):
        raise Crash

    def kill_at_the_second_save(path, state):
        saves.append(1)
        if len(saves) == 2:  # the save that clears `pending`
            raise Crash
        real_save(path, state)

    if killed_at == "after-the-state-commit":
        monkeypatch.setattr(observe, "append_records", kill_before_append)
    else:
        monkeypatch.setattr(observe, "save_state", kill_at_the_second_save)
    with pytest.raises(Crash):
        _tick(tmp_path, store, T + 60, rows)
    monkeypatch.undo()
    assert observe.load_state(store)["pending"]["t"] == int(T + 60)  # cursors advanced, records still owed
    _tick(tmp_path, store, T + 120, rows)  # the next timer run; the same rows are still listed
    assert _calls_total(store) == 2  # counted once, although the cursor and the records were committed apart
    records, _ = observe.read_records(store, T + 60)
    assert len([r for r in records if r["kind"] == "omniroute" and r["t"] == int(T + 60)]) == 1  # not written twice
    assert "pending" not in observe.load_state(store)


def test_a_failing_prune_does_not_fail_the_tick_or_repeat_counts(tmp_path, monkeypatch):
    store, ms = tmp_path / "observe", int(T * 1000)
    rows = [_row("a", ms + 20_000)]
    _tick(tmp_path, store, T, [])

    def disk_error(*args, **kwargs):
        raise OSError("read-only filesystem")

    monkeypatch.setattr(observe, "prune_store", disk_error)
    assert _tick(tmp_path, store, T + 60, rows)["ok"]
    monkeypatch.undo()
    _tick(tmp_path, store, T + 120, rows)
    assert _calls_total(store) == 1 and "pending" not in observe.load_state(store)


def _config_with(tmp_path, extra):
    from test_cli import write_test_config

    config = write_test_config(tmp_path)
    config.write_text(config.read_text() + extra)
    return config


def _config(tmp_path, port, password):
    return _config_with(tmp_path, f"omniroute: {{port: {port}, initial_password: {json.dumps(password)}}}\n")


def test_cli_observe_exits_zero_and_never_prints_the_password(tmp_path, monkeypatch, capsys):
    import llmenv
    from pylib import resources

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(resources, "_locate", lambda name: None)  # no podman in a unit test
    monkeypatch.setattr(observe, "disk_guard", lambda store, statvfs=None: (True, "test"))  # /tmp is a small tmpfs
    secret = "pw-MARKER-1"
    config = _config(tmp_path, free_port(), secret)  # nothing listens: OmniRoute is down
    assert llmenv.main(["--config", str(config), "observe"]) == 0
    out = capsys.readouterr()
    result = json.loads(out.out)
    assert result["ok"] and result["records"] >= 3
    store = tmp_path / "state/llm-env/observe"
    written = "".join(p.read_text() for p in store.iterdir() if p.is_file())
    assert secret not in written + out.out + out.err
    kinds = [json.loads(line)["kind"] for p in store.glob("*.jsonl") for line in p.read_text().splitlines()]
    assert "omniroute" in kinds and kinds.count("container") >= 2


@pytest.mark.parametrize("extra", [
    None,  # no config file at all
    'omniroute: {port: 8080, initial_password: "SECRET-MARKER-1\n',  # malformed YAML that carries a secret
    'omniroute: {port: "SECRET-MARKER-2", initial_password: "SECRET-MARKER-1"}\n',  # parses, fails validation
])
def test_cli_observe_reports_only_a_fixed_category_for_a_bad_config(tmp_path, capsys, extra):
    import llmenv

    config = tmp_path / "absent.yml" if extra is None else _config_with(tmp_path, extra)
    assert llmenv.main(["--config", str(config), "observe"]) == 0
    out = capsys.readouterr()
    # Not the ConfigError message (it carries the path, the YAML diagnostics or configuration values), not a type name.
    assert out.out == '{"ok":false,"error":"config"}\n' and out.err == ""


def test_cli_observe_reports_internal_for_an_unexpected_failure(tmp_path, monkeypatch, capsys):
    import llmenv

    def boom(*args, **kwargs):
        raise RuntimeError("SECRET-MARKER-3 /home/user/models.yml")

    monkeypatch.setattr(observe, "run_observe", boom)
    assert llmenv.main(["--config", str(_config(tmp_path, 1, "pw")), "observe"]) == 0
    out = capsys.readouterr()
    assert out.out == '{"ok":false,"error":"internal"}\n' and "SECRET-MARKER-3" not in out.err
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `uv run pytest tests/test_observe.py -q -k "run_observe or guard or failing or concurrent or cli or killed or slow_container or lifetime"`
Expected: FAIL with `AttributeError: ... 'run_observe'` and `invalid choice: 'observe'`.

- [ ] **Step 3: Write `run_observe`**

In `pylib/observe.py`, add `import copy`, `import sys` and `import threading` to the imports, then append:

```python
TICK_DEADLINE_S = 30.0  # inside TimeoutStartSec=45s: room for uv start-up, the append and two small state writes


def _error(kind: str, now: float, name: str) -> dict[str, Any]:
    return {"v": SCHEMA, "t": int(now), "kind": kind, "state": "error", "error": name}


class _Source:
    """One collector on a daemon thread, over a private copy of its own state keys.

    A slow source cannot starve the others (the tick joins every thread against ONE deadline), and a slow or failing
    source cannot leave a half-moved cursor behind: its state is merged only if it finished in time without raising.
    The exception type is enough to diagnose; a message could carry a value. Daemon threads never block the exit."""

    def __init__(self, kind: str, tick: float, state: Mapping[str, Any], keys: tuple[str, ...],
                 collect: Callable[[dict[str, Any]], list[dict[str, Any]]]) -> None:
        self.kind, self.tick = kind, tick
        self.sub: dict[str, Any] = copy.deepcopy({k: state[k] for k in keys if k in state})
        self.records: list[dict[str, Any]] | None = None
        self.failure = ""
        self.thread = threading.Thread(target=self._run, args=(collect,), daemon=True)
        self.thread.start()

    def _run(self, collect: Callable[[dict[str, Any]], list[dict[str, Any]]]) -> None:
        try:
            self.records = collect(self.sub)
        except Exception as exc:
            self.failure = type(exc).__name__

    def finish(self, state: dict[str, Any], deadline: float) -> list[dict[str, Any]]:
        self.thread.join(max(0.0, deadline - time.monotonic()))
        if self.thread.is_alive():
            return [_error(self.kind, self.tick, "TimeoutError")]
        if self.records is None:
            return [_error(self.kind, self.tick, self.failure or "RuntimeError")]
        state.update(self.sub)
        return self.records


def _stamp_life(records: list[dict[str, Any]]) -> None:
    """Tag the OmniRoute record with the lifetime (container id, StartedAt) of the container read in the same tick, so
    the trend recognises a counter reset even when the new counter already exceeds the old one."""
    life = next(([r["id"], r["started_at"]] for r in records
                 if r.get("kind") == "container" and r.get("service") == "omniroute" and r.get("state") == "running"
                 and r.get("id") and r.get("started_at") is not None), None)
    for record in records:
        if life and record.get("kind") == "omniroute":
            record["life"] = life


def _prune(store: Path, now: float) -> int:
    """Retention. It runs after the commit (and while the disk guard is active), so a failure must not fail a tick."""
    try:
        return len(prune_store(store, now))
    except OSError:
        return 0


def _recover(store: Path, state: dict[str, Any]) -> None:
    """Finish a tick a killed run committed to state.json but did not finish writing. The cursors in the state already
    include its calls and requests, so collecting them again would count them twice: append the records still missing
    from the day file (matched by kind and service at the tick time) instead."""
    pending = state.pop("pending", None)
    if pending is None:
        return
    if isinstance(pending, dict) and isinstance(pending.get("t"), int) and isinstance(pending.get("records"), list):
        written, _ = read_records(store, pending["t"])
        have = {(r.get("kind"), r.get("service")) for r in written if r.get("t") == pending["t"]}
        missing = [r for r in pending["records"] if isinstance(r, dict) and (r.get("kind"), r.get("service")) not in have]
        if missing:
            append_records(store, missing, pending["t"])
    save_state(store, state)


def run_observe(limits: Mapping[str, Any], *, omniroute_port: int, omniroute_password: str, pci: str | None,
                system_one: bool, store: Path | None = None, now: float | None = None,
                statvfs: Callable[[Path], Any] = os.statvfs, locator: CachedLocator | None = None,
                journal: Callable[..., Any] = subprocess.run,
                omniroute: Callable[..., dict[str, Any]] = omniroute_record,
                deadline_s: float = TICK_DEADLINE_S) -> dict[str, Any]:
    """One timer tick: lock, disk guard, collect every source concurrently under one deadline, commit, append, rotate."""
    started = time.monotonic()
    deadline = started + deadline_s
    tick = time.time() if now is None else now
    store = ensure_store(store or observe_dir())
    with store_lock(store) as held:
        if not held:
            return {"ok": True, "skipped": "another observe run holds the lock"}
        state = load_state(store)
        allowed, detail = disk_guard(store, statvfs)
        if not allowed:
            if not state.get("guard"):
                print(f"observe: disk guard: {detail}; not appending until space is freed", file=sys.stderr)
            state["guard"] = detail
            save_state(store, state)
            return {"ok": True, "records": 0, "guard": detail, "pruned": _prune(store, tick)}
        if state.pop("guard", None):
            print(f"observe: disk guard cleared: {detail}", file=sys.stderr)
        _recover(store, state)
        podman: list[int] = []

        def containers(sub: dict[str, Any]) -> list[dict[str, Any]]:
            loc = locator or CachedLocator(sub.setdefault("containers", {}))
            out = container_records(limits, pci, sub, tick, locator=loc)
            podman.append(loc.podman_calls)
            return out

        sources = [_Source("container", tick, state, ("containers", "cpu"), containers),
                   _Source("omniroute", tick, state, ("calls",),
                           lambda sub: [omniroute(omniroute_port, omniroute_password, sub, tick)])]
        if system_one:
            sources.append(_Source("laya", tick, state, ("laya",),
                                   lambda sub: [laya_record(sub, tick, run=journal)]))
        records = [record for source in sources for record in source.finish(state, deadline)]
        _stamp_life(records)
        state["pending"] = {"t": int(tick), "records": records}
        save_state(store, state)  # commit 1: the new cursors and the records they produced, in one atomic write
        append_records(store, records, int(tick))
        state.pop("pending")
        save_state(store, state)  # commit 2: nothing is owed any more
        _prune(store, tick)
        return {"ok": True, "records": len(records), "ms": round((time.monotonic() - started) * 1000),
                "podman": sum(podman)}
```

- [ ] **Step 4: Wire the subcommand**

In `llmenv.py`, add after `cmd_resource_override`:

```python
def cmd_observe(args: argparse.Namespace) -> int:
    """One collector tick for the llm-env-observe user timer. Always exits 0 so a bad tick never fails the unit;
    the outcome is the single JSON line on stdout (the journal keeps it). The password is passed, never printed."""
    from pylib.observe import run_observe

    try:
        cfg = require_valid_config(load_config(Path(args.config)))
        omniroute = cfg["omniroute"]
        result = run_observe(
            limits_for(cfg, host_resources()),
            omniroute_port=int(omniroute["port"]),
            omniroute_password=str(omniroute.get("initial_password") or ""),
            pci=(cfg.get("gpu") or {}).get("pci_address") or None,
            system_one=bool((cfg.get("system_one") or {}).get("enabled")),
        )
    except ConfigError:  # never its message: it carries the path, YAML parser diagnostics or configuration values
        result = {"ok": False, "error": "config"}
    except Exception:  # the timer must keep ticking; the exception text could carry a value, so it is not printed
        result = {"ok": False, "error": "internal"}
    print(json.dumps(result, separators=(",", ":")))
    return 0
```

In `build_parser`, after `override_parser.set_defaults(func=cmd_resource_override)`, add:

```python
    sub.add_parser("observe").set_defaults(func=cmd_observe)
```

- [ ] **Step 5: Run the tests and confirm they pass**

Run: `uv run pytest tests/test_observe.py tests/test_cli.py -q`
Expected: all pass. Then run `ps -eo pid,args | grep '[f]ake_omniroute.py' || echo none` and expect `none`.

- [ ] **Step 6: Run the gate**

Run: `make validate && make test`
Expected: both pass.

- [ ] **Step 7: Commit**

```bash
git add pylib/observe.py llmenv.py tests/test_observe.py
git commit -m "feat(observe): crash-safe, deadline-bound run_observe and the 'uv run llmenv.py observe' subcommand (always exit 0)"
```

---

### Task 8: Trend report (peak and p95 resident, events, restarts, guard, calls, healer, verdict)

**Files:**
- Modify: `pylib/observe.py`
- Test: `tests/test_observe.py`

**Interfaces:**
- Consumes: `read_records`, `load_state`, `DAY_FILE`, `_number` and `JOURNAL_TIMEOUT_S`.
- Produces:
  - `observe.WINDOWS = (("1h", 3600), ("24h", 86400), ("7d", 604800))`, `observe.VERDICT_HOURS = 24`, `observe.BASELINE_S = 3600`, `observe.HEAL_UNIT` and `observe.GATEWAY_TITLE`.
  - `observe.counter_increase(samples: list[tuple[Any, Any]]) -> int | None`. `samples` are time-ordered `(lifetime, value)` pairs. It returns `None` when fewer than two known values exist.
  - `observe.healer_restarts(now: float, *, run=subprocess.run) -> list[float] | None`.
  - `observe.trend_report(store: Path, now: float, healer: list[float] | None) -> {"tables": [{"title": str, "rows": [tab-separated str]}], "footer": [str]}`. The table titles are:
    - `"Trend: resident memory per container"`
    - `"Trend: memory events, restarts and verdicts"`
    - `"Trend: OmniRoute gateway and Laya"`
- Rules the report keeps (each pinned below):
  - **Baseline.** Every window counts increases and lifetime changes over its samples **plus the newest sample before the window start** (at most `BASELINE_S` older, read across the UTC day files). Without it an increase that straddles the window edge, or a restart between the last pre-window sample and the first in-window one, would be lost.
  - **Lifetime.** OmniRoute counters use the `life` of each record (container id + StartedAt, stamped in Task 7); container counters use `(id, started_at)`. A different lifetime counts the new value from zero even when it is larger than the old one. Where one side has no lifetime, only a decrease counts as a reset.
  - **Unknown.** `n/a` means no usable reading (all counters unreadable, a source failed in every tick, no known duration or token usage). A trailing `+` means a lower bound: some ticks of the window were unknown, truncated or had unreadable rows. A figure is `0` only when it was measured.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_observe.py`:

```python
def _c(t, current, *, started=1000, cid="aaa", max_events=0, oom_kill=0, verdict="OK", peak_over_cap=0.5):
    return {"v": 1, "t": t, "kind": "container", "service": "omniroute", "state": "running", "id": cid,
            "started_at": started, "current_mib": current, "peak_over_cap": peak_over_cap, "verdict": verdict,
            "events": {"high": 0, "max": max_events, "oom": 0, "oom_kill": oom_kill}}


ZERO = {"calls": 0, "status": {}, "tokens_in": 0, "tokens_out": 0, "tokens_known": 0, "duration_ms_sum": 0,
        "duration_known": 0, "probes": 0}
LIFE_A, LIFE_B = ["aaa", 1000], ["bbb", 2000]


def _o(t, guard, shed, calls=ZERO, life=None):
    """An OmniRoute record; `calls=None` is a tick whose call-log source failed (health was still read)."""
    record = {"v": 1, "t": t, "kind": "omniroute", "state": "ok",
              "health": {"guard_rejects": guard, "chat_shed_total": shed}, "calls": calls}
    return {**record, "life": life} if life else record


def _table(report, title):
    return [row.split("\t") for row in next(t for t in report["tables"] if t["title"] == title)["rows"]]


GATEWAY = "Trend: OmniRoute gateway and Laya"  # window, guard rejects, chat sheds, calls, 4xx, 5xx, tokens in/out,
# mean ms, probes, unknown ticks, healer restarts, laya requests


def _write(store, *records):
    for record in records:
        observe.append_records(store, [record], record["t"])


def test_counter_increase_survives_restarts_and_unknowns():
    assert observe.counter_increase([(1, 2), (1, 5), (2, 1), (2, None), (2, 4)]) == 3 + 1 + 3
    assert observe.counter_increase([(None, 10), (None, 13), (None, 2)]) == 3 + 2
    assert observe.counter_increase([("a", 10), ("b", 13)]) == 13  # a new lifetime counts from zero, though 13 > 10
    assert observe.counter_increase([(None, 10), ("b", 13)]) == 3  # lifetime unknown on one side: only a decrease resets
    assert observe.counter_increase([("a", 7), ("a", 7)]) == 0  # two readings and no increase: a measured zero
    # unknown is not zero: no reading, one reading or only unreadable readings carry no increase
    assert observe.counter_increase([]) is None and observe.counter_increase([("a", 4)]) is None
    assert observe.counter_increase([(None, None), (None, "x")]) is None


def test_trend_report_memory_events_restarts_and_verdict(tmp_path):
    store = _store(tmp_path)
    _write(store,
           _c(T - 6 * DAY, 900, max_events=2, peak_over_cap=0.8),
           _c(T - 2 * 3600, 600, max_events=5, verdict="WATCH"),
           _c(T - 1800, 500, started=2000, cid="bbb", max_events=1),  # restarted: a new lifetime
           _c(T - 600, 700, started=2000, cid="bbb", max_events=1),
           {"v": 1, "t": T - 300, "kind": "container", "service": "omniroute", "state": "not running"})
    report = observe.trend_report(store, T, healer=[])
    assert _table(report, "Trend: resident memory per container")[1] == \
        ["omniroute", "700/700", "700/700", "900/900", "0.80", "4"]
    assert _table(report, "Trend: memory events, restarts and verdicts")[1] == \
        ["omniroute", "1/4", "0", "1/1", "WATCH (1/3)"]


def test_windows_keep_a_baseline_sample_so_boundary_increases_count(tmp_path):
    store = _store(tmp_path)
    midnight = datetime.datetime(2026, 10, 25, tzinfo=datetime.UTC).timestamp()
    now = midnight + 600  # the 1 h window starts at 23:10 UTC on the 24th, so it spans two day files
    before_day = now - 86400
    _write(store,
           _o(midnight - 3500, 10, 4), _o(midnight + 30, 13, 4),  # 10 -> 13 straddles the 1 h window start
           _c(before_day - 100, 400, started=1000, cid="aaa", max_events=2),  # the newest sample before the 24 h window
           _c(before_day + 100, 400, started=2000, cid="bbb", max_events=3))  # a restart between the two samples
    report = observe.trend_report(store, now, healer=[])
    assert sorted(p.name for p in store.glob("*.jsonl")) == ["2026-10-24.jsonl", "2026-10-25.jsonl"]
    assert _table(report, GATEWAY)[1][:3] == ["1h", "3", "0"]  # without the baseline sample both would read n/a
    assert _table(report, "Trend: memory events, restarts and verdicts")[1] == \
        ["omniroute", "3/3", "0", "1/1", "OK (1/1)"]  # the restart is counted, and so is the new lifetime's 3 events


def test_gateway_counters_use_the_container_lifetime_not_only_a_decrease(tmp_path):
    store = _store(tmp_path)
    _write(store, _o(T - 1800, 10, 5, life=LIFE_A), _o(T - 1200, 13, 9, life=LIFE_B), _o(T - 600, 14, 9, life=LIFE_B))
    # the restart took the counters from 10/5 to 0, and they rose to 13/9 before the next tick: 13 > 10, no decrease
    assert _table(observe.trend_report(store, T, healer=[]), GATEWAY)[1][:3] == ["1h", "14", "9"]


def test_trend_report_gateway_guard_calls_healer_and_laya(tmp_path):
    store = _store(tmp_path)
    calls = {"calls": 4, "status": {"2xx": 2, "4xx": 1, "5xx": 1}, "tokens_in": 100, "tokens_out": 40,
             "tokens_known": 4, "duration_ms_sum": 2000, "duration_known": 4, "probes": 2}
    _write(store,
           _o(T - 3 * 3600, 10, 0), _o(T - 1200, 13, 1, calls), _o(T - 600, 2, 1),  # guard counter reset by a restart
           {"v": 1, "t": T - 300, "kind": "omniroute", "state": "unknown", "reason": "io"},
           {"v": 1, "t": T - 120, "kind": "laya", "state": "ok", "requests": {"2xx": 5, "4xx": 1}})
    gateway = _table(observe.trend_report(store, T, healer=[T - 100, T - 2 * DAY]), GATEWAY)
    # the unknown tick at T - 300 makes every call figure of the window a lower bound ("+"); the mean is over the 4
    # calls with a known duration
    assert gateway[1] == ["1h", "2", "0", "4+", "1+", "1+", "100/40+", "500", "2+", "1", "1", "6"]
    assert gateway[2][:3] == ["24h", "5", "1"] and gateway[3][10] == "2"


def test_trend_renders_unknown_as_na_never_as_zero(tmp_path):
    store = _store(tmp_path)
    no_usage = {"calls": 3, "status": {"2xx": 3}, "tokens_in": 0, "tokens_out": 0, "tokens_known": 0,
                "duration_ms_sum": 0, "duration_known": 0, "probes": 0}
    _write(store,
           _o(T - 1500, None, None, calls=None),  # health counters unreadable and the call-log source failed
           _o(T - 900, None, None, calls=no_usage),  # no token usage and no duration on any of the 3 calls
           {"v": 1, "t": T - 800, "kind": "laya", "state": "unknown", "reason": "journal"},
           {"v": 1, "t": T - 400, "kind": "laya", "state": "error", "error": "TimeoutError"})
    row = _table(observe.trend_report(store, T, healer=None), GATEWAY)[1]
    assert row == ["1h", "n/a", "n/a", "3+", "0+", "0+", "n/a", "n/a", "0+", "1", "n/a", "n/a"]
    mixed = observe.ensure_store(tmp_path / "second")  # one failed Laya tick next to one good one: a lower bound
    _write(mixed, {"v": 1, "t": T - 800, "kind": "laya", "state": "unknown", "reason": "journal"},
           {"v": 1, "t": T - 400, "kind": "laya", "state": "ok", "requests": {"2xx": 5}},
           _o(T - 300, 1, 1), _o(T - 100, 1, 1))
    row = _table(observe.trend_report(mixed, T, healer=[]), GATEWAY)[1]
    assert row[1] == "0" and row[11] == "5+"  # two readings of an unchanged counter are a measured zero


def test_trend_report_without_data_and_with_an_active_guard(tmp_path):
    store = _store(tmp_path)
    empty = observe.trend_report(store, T, healer=None)
    assert empty["tables"] == [] and "no observe data" in empty["footer"][0]
    _write(store, _c(T - 60, 100))
    with (store / "2026-10-24.jsonl").open("a") as handle:
        handle.write("garbage\n")
    observe.save_state(store, {"guard": "90 GB free (11%)"})
    report = observe.trend_report(store, T, healer=None)
    assert "1 unreadable lines skipped" in report["footer"][0] and "DISK GUARD ACTIVE" in report["footer"][-1]
    assert _table(report, GATEWAY)[1][10] == "n/a"


def test_healer_restarts_parse_only_restart_lines():
    out = ("1792850000.5 host bash[1]: ok chatAdmission: activeHeavy=0 strikes=0/3 -- not restarting\n"
           "1792850100.25 host bash[1]: warn memory recycle: idle at 1300MiB > 1228MiB for 3 consecutive checks"
           " -- restarting omniroute\n"
           "1792850200.0 host bash[1]: warn chatAdmission stuck for 3 consecutive checks"
           " -- restarting omniroute to clear the leaked lease\n"
           "1792850300.0 host bash[1]: ok memory recycle: idle at 1300MiB, strikes=1/3 -- not restarting yet\n")
    run = FakeRun(out)
    assert observe.healer_restarts(1792850400.0, run=run) == [1792850100.25, 1792850200.0]
    assert run.argv[:4] == ["journalctl", "--user", "-u", "llm-env-omniroute-heal.service"]
    assert observe.healer_restarts(1792850400.0, run=FakeRun(rc=1)) is None
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `uv run pytest tests/test_observe.py -q -k "trend or counter_increase or healer or baseline or gateway"`
Expected: FAIL with `AttributeError: ... 'counter_increase'`.

- [ ] **Step 3: Write the minimal implementation**

In `pylib/observe.py`, add `import math`, `from collections import defaultdict` and `from itertools import pairwise` to the imports, then append:

```python
WINDOWS = (("1h", 3600), ("24h", 86400), ("7d", 7 * 86400))
VERDICT_HOURS = 24
BASELINE_S = 3600  # the sample before a window may be this much older than the window start
HEAL_UNIT = "llm-env-omniroute-heal.service"
GATEWAY_TITLE = "Trend: OmniRoute gateway and Laya"
_RANK = {"OK": 0, "WATCH": 1, "ALERT": 2}


def _p95(values: list[Any]) -> Any:
    ordered = sorted(values)
    return ordered[math.ceil(0.95 * len(ordered)) - 1] if ordered else None


def counter_increase(samples: list[tuple[Any, Any]]) -> int | None:
    """Increase of a counter over time-ordered (lifetime, value) samples; the first known sample is the baseline.

    A different lifetime (both known) or a lower value (the process restarted) counts the new value from zero. Unknown
    values are skipped. None when fewer than two known values exist: one reading, or none, says nothing about an
    increase, and unknown is not zero."""
    total, previous, known = 0, None, 0
    for life, value in samples:
        if _number(value) is None:
            continue
        known += 1
        if previous is not None:
            same = previous[0] is None or life is None or previous[0] == life
            total += value - previous[1] if same and value >= previous[1] else value
        previous = (life, value)
    return total if known >= 2 else None


def healer_restarts(now: float, *, run: Callable[..., Any] = subprocess.run) -> list[float] | None:
    """Epoch times of the healer's OmniRoute restarts in the last 7 days, from its own journal; None if unreadable.
    Both restart paths log 'restarting omniroute'; the 'not restarting' lines never contain that phrase."""
    argv = ["journalctl", "--user", "-u", HEAL_UNIT, "--since", f"@{int(now - WINDOWS[-1][1])}",
            "-o", "short-unix", "--no-pager"]
    try:
        out = run(argv, capture_output=True, text=True, timeout=JOURNAL_TIMEOUT_S, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    times = []
    for line in out.stdout.splitlines():
        if "restarting omniroute" in line:
            try:
                times.append(float(line.split(None, 1)[0]))
            except (ValueError, IndexError):
                continue
    return times


def _since(rows: list[dict[str, Any]], now: float, seconds: int) -> list[dict[str, Any]]:
    return [r for r in rows if r["t"] >= now - seconds]


def _window(rows: list[dict[str, Any]], now: float, seconds: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """([the newest sample before the window, at most BASELINE_S older], the samples inside it). The baseline anchors
    the counters that change inside the window; it is never summed itself."""
    start = now - seconds
    return [r for r in rows if start - BASELINE_S <= r["t"] < start][-1:], [r for r in rows if r["t"] >= start]


def _cell(value: Any, partial: bool = False) -> str:
    """n/a for an unknown figure, a trailing + for a lower bound, else the plain number."""
    return "n/a" if value is None else f"{value}+" if partial else str(value)


def _life(record: Mapping[str, Any]) -> tuple[Any, ...] | None:
    life = record.get("life")
    return tuple(life) if isinstance(life, list) else None


def _sum(calls: list[dict[str, Any]], key: str) -> int | None:
    """Sum of a call-summary field over the ticks that have a summary; None when no tick does (unknown, not 0)."""
    return sum(c.get(key, 0) for c in calls) if calls else None


def _status(calls: list[dict[str, Any]], group: str) -> int | None:
    return sum((c.get("status") or {}).get(group, 0) for c in calls) if calls else None


def _events(rows: list[dict[str, Any]], event: str) -> int | None:
    return counter_increase([((r.get("id"), r.get("started_at")), (r.get("events") or {}).get(event)) for r in rows])


def _restarts(rows: list[dict[str, Any]]) -> int | None:
    lives = [(r.get("id"), r.get("started_at")) for r in rows]
    return sum(1 for a, b in pairwise(lives) if a != b) if len(lives) > 1 else None


def trend_report(store: Path, now: float, healer: list[float] | None) -> dict[str, Any]:
    """Trend tables for `make resources-snapshot`: {"tables": [{"title", "rows"}], "footer"}. Rows are tab-separated
    with the header first, like `resources.format_live`."""
    week = WINDOWS[-1][1]
    records, bad = read_records(store, now - week - BASELINE_S)
    guard = load_state(store).get("guard") if store.is_dir() else None
    guard_line = [f"DISK GUARD ACTIVE: {guard}; the collector is not appending."] if guard else []
    if not [r for r in records if r["t"] >= now - week]:
        return {"tables": [], "footer": [f"Trends: no observe data in {store} yet; the llm-env-observe timer "
                                         "(installed by make start) appends every 60 s.", *guard_line]}
    services: dict[str, list[dict[str, Any]]] = defaultdict(list)
    omni: list[dict[str, Any]] = []
    laya: list[dict[str, Any]] = []
    for r in records:
        if r.get("kind") == "container" and r.get("state") == "running":
            services[str(r.get("service"))].append(r)
        elif r.get("kind") == "omniroute":
            omni.append(r)
        elif r.get("kind") == "laya":
            laya.append(r)

    memory = ["\t".join(("service", *(f"{name} peak/p95 MiB" for name, _ in WINDOWS), "7d peak/cap", "samples 7d"))]
    events = ["\t".join(("service", "events max 24h/7d", "oom_kill 7d", "restarts 24h/7d",
                         f"worst verdict {VERDICT_HOURS}h"))]
    for service, rows in services.items():
        in_week = _since(rows, now, week)
        if not in_week:
            continue
        cells = []
        for _, seconds in WINDOWS:
            resident = [r["current_mib"] for r in _since(rows, now, seconds) if _number(r.get("current_mib")) is not None]
            cells.append(f"{max(resident)}/{_p95(resident)}" if resident else "n/a")
        ratios = [r["peak_over_cap"] for r in in_week if _number(r.get("peak_over_cap")) is not None]
        memory.append("\t".join((service, *cells, f"{max(ratios):.2f}" if ratios else "n/a", str(len(in_week)))))
        before_day, day = _window(rows, now, 86400)
        before_week, week_rows = _window(rows, now, week)
        verdicts = [r["verdict"] for r in _since(day, now, VERDICT_HOURS * 3600) if r.get("verdict") in _RANK]
        worst = max(verdicts, key=_RANK.__getitem__) if verdicts else None
        events.append("\t".join((
            service,
            f"{_cell(_events(before_day + day, 'max'))}/{_cell(_events(before_week + week_rows, 'max'))}",
            _cell(_events(before_week + week_rows, "oom_kill")),
            f"{_cell(_restarts(before_day + day))}/{_cell(_restarts(before_week + week_rows))}",
            f"{worst} ({verdicts.count(worst)}/{len(verdicts)})" if worst else "n/a")))

    gateway = ["\t".join(("window", "guard rejects", "chat sheds", "calls", "4xx", "5xx", "tokens in/out", "mean ms",
                          "probes", "unknown ticks", "healer restarts", "laya requests"))]
    for name, seconds in WINDOWS:
        before, ticks = _window(omni, now, seconds)
        samples = [r for r in before + ticks if isinstance(r.get("health"), dict)]
        guard_rejects = counter_increase([(_life(r), r["health"].get("guard_rejects")) for r in samples])
        sheds = counter_increase([(_life(r), r["health"].get("chat_shed_total")) for r in samples])
        calls = [r["calls"] for r in ticks if isinstance(r.get("calls"), dict)]
        # a lower bound: a tick whose call log was unreadable, or a tick that skipped rows, hides some calls
        partial = len(calls) < len(ticks) or any(c.get("truncated") or c.get("unreadable") for c in calls)
        n = _sum(calls, "calls")
        tokens = mean = "n/a"
        if n is not None:
            usage, durations = _sum(calls, "tokens_known") or 0, _sum(calls, "duration_known") or 0
            if usage or not n:  # no call with known usage: the zero sums are not measurements
                tokens = _cell(f"{_sum(calls, 'tokens_in')}/{_sum(calls, 'tokens_out')}", partial or usage < n)
            if durations:
                mean = str(round((_sum(calls, "duration_ms_sum") or 0) / durations))  # over the calls with a duration
            elif not n:
                mean = "-"  # no calls: nothing to average
        lticks = _since(laya, now, seconds)
        ok = [r for r in lticks if r.get("state") == "ok"]
        if not laya:
            requests = "-"  # Laya is not collected at all
        elif not ok:
            requests = "n/a"
        else:
            requests = _cell(sum(sum((r.get("requests") or {}).values()) for r in ok),
                             len(ok) < len(lticks) or any(r.get("truncated") for r in ok))
        gateway.append("\t".join(str(cell) for cell in (
            name, _cell(guard_rejects), _cell(sheds), _cell(n, partial), _cell(_status(calls, "4xx"), partial),
            _cell(_status(calls, "5xx"), partial), tokens, mean, _cell(_sum(calls, "probes"), partial),
            sum(1 for r in ticks if r.get("state") != "ok" or not isinstance(r.get("calls"), dict)),
            "n/a" if healer is None else sum(1 for t in healer if t >= now - seconds),
            requests,
        )))

    files = sorted(p for p in store.iterdir() if DAY_FILE.fullmatch(p.name))
    size = sum(p.stat().st_size for p in files)
    footer = [
        f"Store {store}: {len(files)} day files, {size / 10**6:.1f} MB, oldest {files[0].name[:10]}; retention "
        f"{MAX_DAYS} days / {MAX_BYTES // 10**9} GB; {bad} unreadable lines skipped.",
        "Peak/p95 are resident memory.current per 60 s sample; events and restarts count across container lifetimes; "
        "guard rejects and chat sheds are OmniRoute counter increases (a new container lifetime counts from zero); "
        "calls are persisted client calls (provider-test probes apart). n/a: no usable reading; N+: a lower bound, "
        "some ticks of the window were unknown, truncated or had unreadable rows.",
        *guard_line,
    ]
    return {"tables": [{"title": "Trend: resident memory per container", "rows": memory},
                       {"title": "Trend: memory events, restarts and verdicts", "rows": events},
                       {"title": GATEWAY_TITLE, "rows": gateway}],
            "footer": footer}
```

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `uv run pytest tests/test_observe.py -q`
Expected: all pass.

- [ ] **Step 5: Run the gate**

Run: `make validate && make test`
Expected: both pass.

- [ ] **Step 6: Commit**

```bash
git add pylib/observe.py tests/test_observe.py
git commit -m "feat(observe): 1h/24h/7d trend report (baselines, lifetimes, unknown as n/a: peak, p95, events, restarts, guard, calls, healer, verdict)"
```

---

### Task 9: Show the trends in `make resources-snapshot`

**Files:**
- Modify: `llmenv.py`: add `import time`, and a trend block in `cmd_resources` (≈436-445).
- Modify: `scripts/resources-snapshot.sh`: header comment and trend printing.
- Modify: `scripts/help.sh:22`
- Test: `tests/test_observe.py` and `tests/test_shell.py` (`test_resources_snapshot_script_prints_the_policy_table_and_summary`, ≈12808).

**Interfaces:**
- Consumes: `observe.trend_report`, `observe.healer_restarts` and `observe.observe_dir` (Task 8).
- Produces: the `resources --live` JSON gains `trend_tables: [{"title", "rows"}]` and `trend_footer: [str]`, which the snapshot script prints after the live tables.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_observe.py`:

```python
def test_resources_live_payload_carries_the_trend(tmp_path, monkeypatch, capsys):
    import llmenv
    from test_cli import write_test_config

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(llmenv, "live_resources", lambda limits, pci: {})
    monkeypatch.setattr(observe, "healer_restarts", lambda now: [])
    store = observe.ensure_store(tmp_path / "state/llm-env/observe")
    now = time.time()
    observe.append_records(store, [_c(now - 60, 640)], now - 60)
    assert llmenv.main(["--config", str(write_test_config(tmp_path)), "resources", "--live"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["trend_tables"][0]["title"] == "Trend: resident memory per container"
    assert payload["trend_tables"][0]["rows"][1].startswith("omniroute\t640/640")
    assert payload["trend_footer"][0].startswith("Store ")
```

In `tests/test_shell.py::test_resources_snapshot_script_prints_the_policy_table_and_summary`, add to `payload`:

```python
        "trend_tables": [{"title": "Trend: resident memory per container",
                          "rows": ["service\t1h peak/p95 MiB", "omniroute\t640/600"]}],
        "trend_footer": ["Store /x: 1 day files, 0.1 MB, oldest 2026-10-24"],
```

and append these assertions:

```python
    assert "Trend: resident memory per container" in result.stdout and "640/600" in result.stdout
    assert "Store /x: 1 day files" in result.stdout
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `uv run pytest tests/test_observe.py -k trend_payload -q; uv run pytest tests/test_shell.py -k resources_snapshot_script -q`
Expected: FAIL. The output has no `trend_tables` and does not contain the title.

- [ ] **Step 3: Implement**

`llmenv.py`: add `import time` to the standard imports. In `cmd_resources`, inside `if args.live:` after `payload["live_footer"] = LIVE_FOOTER`, add:

```python
        from pylib.observe import healer_restarts, observe_dir, trend_report

        now = time.time()
        try:
            trend = trend_report(observe_dir(), now, healer_restarts(now))
            payload["trend_tables"], payload["trend_footer"] = trend["tables"], trend["footer"]
        except OSError:  # an unreadable store must not hide the policy and live tables
            payload["trend_tables"], payload["trend_footer"] = [], ["Trends unavailable: the observe store could not be read."]
```

`scripts/resources-snapshot.sh`: change line 2 to:

```bash
# resources-snapshot.sh — read-only snapshot: per-service CPU/RAM policy, active overrides, live cgroup counters and 1h/24h/7d trends from the observe collector.
```

and append at the end:

```bash
echo
trend_count="$(jq -r '(.trend_tables // []) | length' "$resources_json")"
for ((i = 0; i < trend_count; i++)); do
    jq -r --argjson i "$i" '.trend_tables[$i].title' "$resources_json"
    jq -r --argjson i "$i" '.trend_tables[$i].rows[]' "$resources_json" | column -t -s $'\t'
    echo
done
jq -r '.trend_footer[]?' "$resources_json"
```

`scripts/help.sh:22` becomes:

```bash
_help_line "resources-snapshot" "Point-in-time snapshot: per-service CPU/RAM policy, overrides, live cgroup counters with an OK/WATCH/ALERT verdict, and 1h/24h/7d trends from the observe timer (read-only, no load)"
```

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `uv run pytest tests/test_observe.py tests/test_shell.py -k "trend or resources_snapshot or makefile_exposes" -q`
Expected: PASS.

- [ ] **Step 5: Run the gate**

Run: `make validate && make test`
Expected: both pass. shellcheck must be clean on `resources-snapshot.sh`.

- [ ] **Step 6: Commit**

```bash
git add llmenv.py scripts/resources-snapshot.sh scripts/help.sh tests/test_observe.py tests/test_shell.py
git commit -m "feat(resources-snapshot): print 1h/24h/7d observe trends after the live tables"
```

---

### Task 10: Install the 60 s observe timer through the existing healer setup path

**Files:**
- Modify: `tools/lib.sh`: add `OBSERVE_UNIT_NAME="llm-env-observe"` and add it to the `export` line.
- Modify: `setup/enable-omniroute-heal.sh`: one `write_timer` function renders both timers, and the script enables both.
- Modify: `scripts/start.sh:146-148`: the log text names both timers. The call itself is unchanged.
- Test: `tests/test_observe.py` (render test) and `tests/test_shell.py::test_start_installs_the_omniroute_heal_timer` (≈5377).

**Interfaces:**
- Consumes: `REPO_DIR`, `CONFIG_PATH`, `OMNIROUTE_HEAL_UNIT_NAME` and `log_info` from `tools/lib.sh`.
- Produces:
  - `~/.config/systemd/user/llm-env-observe.{service,timer}` (mode 0600), enabled with `--now`.
  - The service runs `ExecStart=/usr/bin/env uv run --project <repo> <repo>/llmenv.py observe` with `TimeoutStartSec=45s`.
  - The timer has `OnBootSec=2min`, `OnUnitActiveSec=60s` and `AccuracySec=5s`.
  - The healer units keep their current settings: 5 min interval and the default 1 min accuracy.

- [ ] **Step 1: Write the failing tests**

Add `import os` to the imports of `tests/test_observe.py`, then append:

```python
def test_setup_renders_and_enables_the_observe_timer_next_to_the_healer(tmp_path):
    home, bin_dir, calls = tmp_path / "home", tmp_path / "bin", tmp_path / "calls"
    bin_dir.mkdir()
    (bin_dir / "systemctl").write_text(f'#!/usr/bin/bash\necho "systemctl $*" >> {calls}\n')
    (bin_dir / "systemctl").chmod(0o755)
    config = tmp_path / "models.yml"
    config.write_text("version: 1\n")
    env = {**os.environ, "HOME": str(home), "PATH": f"{bin_dir}:{os.environ['PATH']}", "LLM_ENV_CONFIG": str(config)}
    result = subprocess.run(["/usr/bin/bash", str(REPO / "setup/enable-omniroute-heal.sh")], env=env,
                            capture_output=True, text=True, check=False, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    units = home / ".config/systemd/user"
    service, timer = (units / "llm-env-observe.service").read_text(), (units / "llm-env-observe.timer").read_text()
    assert "Type=oneshot" in service and "TimeoutStartSec=45s" in service
    assert f"ExecStart=/usr/bin/env uv run --project {REPO} {REPO}/llmenv.py observe" in service
    assert "Environment=PATH=%h/.local/bin:/usr/local/bin:/usr/bin:/bin" in service
    assert "OnUnitActiveSec=60s" in timer and "AccuracySec=5s" in timer and "WantedBy=timers.target" in timer
    assert "OnUnitActiveSec=5min" in (units / "llm-env-omniroute-heal.timer").read_text()
    assert f"ExecStart=/usr/bin/bash {REPO}/scripts/heal-omniroute-admission.sh" in \
        (units / "llm-env-omniroute-heal.service").read_text()
    assert {stat.S_IMODE(p.stat().st_mode) for p in units.iterdir()} == {0o600}
    log = calls.read_text()
    assert "enable --now llm-env-omniroute-heal.timer" in log and "enable --now llm-env-observe.timer" in log
```

In `tests/test_shell.py::test_start_installs_the_omniroute_heal_timer`, append:

```python
    assert "enable --now llm-env-observe.timer" in calls.read_text()
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `uv run pytest tests/test_observe.py -k setup_renders -q; uv run pytest tests/test_shell.py -k installs_the_omniroute_heal_timer -q`
Expected: FAIL with `FileNotFoundError ... llm-env-observe.service`.

- [ ] **Step 3: Implement**

`tools/lib.sh`: below `OMNIROUTE_HEAL_UNIT_NAME=...`, add `OBSERVE_UNIT_NAME="llm-env-observe"`, and append `OBSERVE_UNIT_NAME` to the `export` line.

Replace everything in `setup/enable-omniroute-heal.sh` from the `service_unit=` line to the end with:

```bash
unit_dir="${HOME}/.config/systemd/user"
mkdir -p "$unit_dir"

# write_timer <unit> <service description> <ExecStart> <timer description> <interval> <boot delay> <accuracy> [extra [Service] line]
write_timer() {
    local unit="$1" description="$2" exec_start="$3" timer_description="$4" interval="$5" boot="$6" accuracy="$7"
    local extra="${8:-}"
    cat > "${unit_dir}/${unit}.service" <<EOF
# Generated by enable-omniroute-heal.sh. Edits will be overwritten.
[Unit]
Description=${description}
After=network-online.target

[Service]
Type=oneshot
# systemd --user services do not inherit the interactive shell's PATH (no
# profile/rc files are sourced), so tools installed under the user's own
# bin directories (uv) would otherwise resolve to nothing here.
Environment=PATH=%h/.local/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=${exec_start}
${extra}
EOF
    chmod 600 "${unit_dir}/${unit}.service"
    cat > "${unit_dir}/${unit}.timer" <<EOF
# Generated by enable-omniroute-heal.sh. Edits will be overwritten.
[Unit]
Description=${timer_description}

[Timer]
OnBootSec=${boot}
OnUnitActiveSec=${interval}
AccuracySec=${accuracy}
Persistent=true

[Install]
WantedBy=timers.target
EOF
    chmod 600 "${unit_dir}/${unit}.timer"
    log_info "wrote ${unit_dir}/${unit}.service and .timer"
}

write_timer "$OMNIROUTE_HEAL_UNIT_NAME" \
    "llm-env OmniRoute healer (stuck admission lease, idle memory recycle)" \
    "/usr/bin/bash ${REPO_DIR}/scripts/heal-omniroute-admission.sh" \
    "Check every 5 minutes for a stuck OmniRoute admission lease" 5min 5min 1min
# One sample per minute, no resident process. The tick gives up on slow sources at 30 s (observe.TICK_DEADLINE_S) and
# still writes the finished ones; TimeoutStartSec=45s is only the backstop, well before the next tick is due.
write_timer "$OBSERVE_UNIT_NAME" \
    "llm-env observe collector (one bounded JSONL sample per tick)" \
    "/usr/bin/env uv run --project ${REPO_DIR} ${REPO_DIR}/llmenv.py observe" \
    "Sample llm-env containers, OmniRoute and Laya every 60 s" 60s 2min 5s "TimeoutStartSec=45s"

systemctl --user daemon-reload
systemctl --user enable --now "${OMNIROUTE_HEAL_UNIT_NAME}.timer"
systemctl --user enable --now "${OBSERVE_UNIT_NAME}.timer"
log_info "enabled ${OMNIROUTE_HEAL_UNIT_NAME}.timer (checks every 5min; only restarts omniroute after 3 consecutive stuck checks, ~10min+ of confirmed saturation)"
log_info "check its schedule: systemctl --user list-timers ${OMNIROUTE_HEAL_UNIT_NAME}.timer"
log_info "run it right now:   systemctl --user start ${OMNIROUTE_HEAL_UNIT_NAME}.service"
log_info "tune the strike threshold: OMNIROUTE_HEAL_STRIKE_THRESHOLD=<n> via 'systemctl --user edit ${OMNIROUTE_HEAL_UNIT_NAME}.service' (a drop-in survives this rewrite)"
log_info "memory recycle: OMNIROUTE_HEAL_MEMORY_MIB (default 60% of omniroute memory.max, 0 disables), OMNIROUTE_HEAL_MEMORY_SAMPLES (3), OMNIROUTE_HEAL_MEMORY_COOLDOWN_HOURS (6); max 2 per 24h"
log_info "enabled ${OBSERVE_UNIT_NAME}.timer (one sample per 60 s into \${XDG_STATE_HOME:-~/.local/state}/llm-env/observe; trends in make resources-snapshot)"
log_info "last tick: journalctl --user -u ${OBSERVE_UNIT_NAME}.service -n 1 -o cat; stop it: systemctl --user disable --now ${OBSERVE_UNIT_NAME}.timer"
```

Update the script's header comment (lines 2-7) to say that it installs and enables two systemd `--user` timers. Keep the existing healer description, and add one line: `llm-env-observe: uv run llmenv.py observe every 60 s (pylib/observe.py), a bounded JSONL store read by make resources-snapshot; no resident service`.

`scripts/start.sh:146-148` becomes:

```bash
log_step "Ensuring the OmniRoute healer and observe timers are installed"
bash "${REPO_DIR}/setup/enable-omniroute-heal.sh" \
    || log_warn "could not enable the healer/observe timers; run 'bash setup/enable-omniroute-heal.sh' manually"
```

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `uv run pytest tests/test_observe.py -k setup_renders -q; uv run pytest tests/test_shell.py -k "start_installs or heal" -q`
Expected: PASS.

- [ ] **Step 5: Run the gate**

Run: `make validate && make test`
Expected: both pass. shellcheck must be clean on `setup/enable-omniroute-heal.sh`, `tools/lib.sh` and `scripts/start.sh`.

- [ ] **Step 6: Commit**

```bash
git add tools/lib.sh setup/enable-omniroute-heal.sh scripts/start.sh tests/test_observe.py tests/test_shell.py
git commit -m "feat(observe): install the 60 s llm-env-observe user timer beside the healer timer"
```

---

### Task 11: Documentation and plan index

**Files:**
- Modify: `README.md`: new subsection `### Observe collector` directly after `### OmniRoute healer` (≈line 317).
- Modify: `AGENTS.md`: the `make resources-snapshot` line in Commands (line 18), plus one paragraph after the live-counter paragraph (≈line 93).
- Modify: `.agents/architecture.md`: add rows for `pylib/observe.py` and `setup/enable-omniroute-heal.sh`, and update the `scripts/resources-snapshot.sh` row.
- Modify: `docs/superpowers/plans/README.md`: add a row for this plan.
- Modify: `docs/superpowers/research/2026-10-20-laya-jev-grafana-observability.md`: add one Status bullet.

**Interfaces:**
- Consumes: the behavior of Tasks 1-10.
- Produces: no code.

- [ ] **Step 1: README section**

Insert after the healer section's last paragraph:

```markdown
### Observe collector

`make start` also installs `llm-env-observe`, a 60 s systemd user timer (via
`setup/enable-omniroute-heal.sh`) that runs `uv run llmenv.py observe` once
and exits; no service stays resident. Each tick appends JSON lines to
`${XDG_STATE_HOME:-~/.local/state}/llm-env/observe/YYYY-MM-DD.jsonl` (UTC
days, directory 0700, files 0600):

- per enabled container: the `make resources-snapshot` live counters
  (memory current/peak/cap, swap, `memory.events`, PSI, pids, uptime,
  restart time, CPU cores since the previous tick; Laya VRAM), read from the
  host cgroup without exec; podman runs only when a container changed;
- OmniRoute: `/api/monitoring/health` guard, admission, breaker, quota and
  memory counters (primed, then read), and a summary of client calls
  persisted since the previous tick (status classes, combo/model/provider
  counts, durations, token sums; `/api/providers/test` probes counted apart);
- Laya (when System One is enabled): requests by status class from its
  journald access log.

Shape and counts only: no prompt, answer, error text, key, cookie or
password is ever written. Retention is 365 days or 2 GB, whichever binds
first (about 4-5 MB per day). When the filesystem holding the store (`/var`
on Bazzite) has under 100 GB or 15 % free, the collector stops appending and
logs once (expired files are still deleted); `make resources-snapshot` then
shows `DISK GUARD ACTIVE`. A tick gives up on a slow source after 30 s and
still writes the others, and a run killed mid-tick never counts a call or a
Laya request twice.

`make resources-snapshot` prints 1 h / 24 h / 7 d trends from the store:
peak and p95 resident memory per container, `memory.events` max and
oom_kill increases, restarts, the worst verdict of the last 24 h, guard
rejects and chat sheds, calls, 4xx/5xx, tokens, healer restarts (from the
healer's journal) and Laya requests. `n/a` means unknown (nothing readable
in that window); a trailing `+` is a lower bound because some ticks were
unknown. Last tick:
`journalctl --user -u llm-env-observe.service -n 1 -o cat`. Stop it:
`systemctl --user disable --now llm-env-observe.timer` (`make start`
re-enables it).

Host-specific: cgroup v2 with rootless Podman's `libpod-<id>.scope`
layout, `/home` -> `/var/home`, container logs on journald, amdgpu DRM
fdinfo for VRAM, `uv` in `~/.local/bin`, and the same `XDG_STATE_HOME` in
the shell and the systemd user environment. Without
`loginctl enable-linger` the timer stops at logout, as the healer does.
```

- [ ] **Step 2: AGENTS.md**

Line 18 becomes:

```
make resources-snapshot      # Point-in-time snapshot: per-service CPU/RAM policy, overrides, live cgroup counters (OK/WATCH/ALERT) and 1h/24h/7d trends from the observe timer (read-only, no load)
```

After the paragraph that starts "`make resources-snapshot` also prints", add:

```markdown
The trends come from the `llm-env-observe` user timer (`uv run llmenv.py observe`
every 60 s, `pylib/observe.py`), which `make start` installs with the healer
timer. It appends shape-only JSON lines to
`${XDG_STATE_HOME:-~/.local/state}/llm-env/observe/` (365 days / 2 GB; it stops
appending under 100 GB or 15 % free) and never stores prompt content or
credentials.
```

- [ ] **Step 3: `.agents/architecture.md`**

Add these rows to the Files table:

```markdown
| `pylib/observe.py` | Observe collector (`uv run llmenv.py observe`, 60 s user timer): bounded UTC-day JSONL store (365 days / 2 GB, disk guard 100 GB / 15 %), container counters via `resources.live_resources` behind a podman-free locator cache, OmniRoute health counters and new persisted client calls (cursor), Laya access-log counts, and the trend tables of `make resources-snapshot` |
| `setup/enable-omniroute-heal.sh` | Renders and enables both host timers: `llm-env-omniroute-heal` (5 min) and `llm-env-observe` (60 s); called by `scripts/start.sh` |
```

Change the `scripts/resources-snapshot.sh` row to: `Point-in-time policy tables, active overrides, live cgroup counters and 1h/24h/7d observe trends (make resources-snapshot)`.

- [ ] **Step 4: Plan index and research status**

In `docs/superpowers/plans/README.md`, append this row:

```markdown
| 2026-10-24-observe-mvp | DONE | phase 0 of the observability research; trends in `make resources-snapshot`; operator 24 h check in its Task 12 |
```

Write PENDING instead of DONE if Task 12 has not been run yet. Update the cell once it has.

In the research doc, under `## Status` after the "Decided as the research recommendation" bullet, add:

```markdown
- **Phase 0 implemented** by [the observe MVP plan](../plans/2026-10-24-observe-mvp.md): no `make observe`
  target (owner); the trend is printed by `make resources-snapshot`.
```

- [ ] **Step 5: Run the gate**

Run: `make validate && make test`
Expected: both pass. `tests/test_docs.py` must stay green.

- [ ] **Step 6: Commit**

```bash
git add README.md AGENTS.md .agents/architecture.md docs/superpowers/plans/README.md docs/superpowers/research/2026-10-20-laya-jev-grafana-observability.md
git commit -m "docs(observe): collector, store, privacy, host assumptions and plan index"
```

---

### Task 12: Operator verification checklist (live host, after 24 h)

This task changes no code. It is run on the live host by the operator, or by an agent with the owner's go-ahead. Every command below is read-only.

**Files:**
- Modify: this plan. Append a dated `## Addendum (YYYY-MM-DD): 24 h verification` with the measured values.
- Modify: `docs/superpowers/plans/README.md`: set the row status.

**Interfaces:**
- Consumes: the deployed timer (after `make start`).
- Produces: a recorded verdict. If a check fails, report it to the owner before changing anything.

- [ ] **Step 1: Right after `make start`**

```bash
systemctl --user list-timers llm-env-observe.timer llm-env-omniroute-heal.timer
journalctl --user -u llm-env-observe.service -n 3 -o cat
```
Expected:
- The observe timer's next run is at most 60 s away.
- The last line looks like `{"ok":true,"records":6,"ms":…,"podman":…}`. `records` is the number of enabled containers, plus 1 for OmniRoute, plus 1 for Laya when System One is enabled.
- `podman` is the number of containers on the first tick, then **0**. A non-zero value later means a container restarted.

- [ ] **Step 2: Cost per tick**

```bash
systemctl --user show llm-env-observe.service -p MemoryPeak -p CPUUsageNSec
journalctl --user -u llm-env-observe.service --since -1h -o cat | jq -s 'map(.ms) | {n: length, max: max, avg: (add/length)}'
```
Expected:
- MemoryPeak is about the healer's (about 21 MB; research §6).
- CPUUsageNSec is under 0.5 s per run. That figure is mostly uv and the interpreter starting up.
- `ms` (wall time) is under 1000. It includes the 0.2 s health prime gap and the HTTP round trips. With OmniRoute idle the collector's own work is a few tens of ms.
- There is no `"ok":false` line.

- [ ] **Step 3: Store size after 24 h** (research estimate: about 2.3 MB/day for the container lines; this plan's estimate: about 4-5 MB/day with OmniRoute and Laya)

```bash
store="${XDG_STATE_HOME:-$HOME/.local/state}/llm-env/observe"
ls -l "$store"; du -sh "$store"
wc -l "$store"/*.jsonl
jq -r .kind "$store/$(date -u +%F).jsonl" | sort | uniq -c
stat -c '%a %n' "$store" "$store"/* "$store"/.lock
```
Expected:
- Each full UTC day file is 3-6 MB.
- There are about 1,440 lines per kind per day, times the number of enabled containers for `container`.
- Modes are 700 on the directory and 600 on every file, `.lock` and `state.json` included.
- If a day goes over 5.5 MB, the 2 GB cap will bind before 365 days. Report that to the owner.

- [ ] **Step 4: Trend plausibility**

```bash
make resources-snapshot
podman ps --format '{{.Names}} {{.StartedAt}}'
journalctl --user -u llm-env-omniroute-heal.service --since -24h -o cat | grep -c 'restarting omniroute'
journalctl --user CONTAINER_NAME=system-one --since -24h -o cat | grep -c '"POST /v1/systemone'
```
Expected:
- The three "Trend:" tables appear after the live tables.
- 24 h peak/p95 are at or under each container's live `peak_mib`.
- `restarts 24h` matches the `StartedAt` changes, and `healer restarts` matches the grep count.
- `laya requests` for 24 h is within a few of the grep count. Ticks seeded or lost to the guard explain a small gap.
- `unknown ticks` is about 0 while OmniRoute is up.
- Calls for 24 h are close to the OmniRoute dashboard's request count over the same day, minus provider-test probes.
- Check the §9 criteria in the snapshot: 7 d peak/cap ≤ 0.83 except `system_one`; oom_kill 0; guard rejects 0 or explained; healer restarts ≤ 1 per day.

- [ ] **Step 5: Privacy and guard**

```bash
store="${XDG_STATE_HOME:-$HOME/.local/state}/llm-env/observe"
grep -c -F -f <(yq -r '.omniroute.initial_password' ~/.config/llm-env/models.yml) "$store"/* || true
jq -c 'select(.kind=="omniroute") | (.calls // {} | keys)' "$store"/*.jsonl | sort -u
jq '{guard, pending}' "$store/state.json"; df -h --output=avail,pcent "$store"
```
Expected:
- The password count is 0 in every file. The process substitution keeps the password off the command line.
- Call keys are exactly the summary fields of the record schema.
- `guard` and `pending` are both `null` (`pending` is only set between the two commits of a tick), and free space is at least 100 GB and at least 15 %.

- [ ] **Step 6: Record and close**

Append the addendum with the measured numbers: MB/day, ms, CPU, MemoryPeak, mismatches. Set the plans index row to DONE. Then:

```bash
git add docs/superpowers/plans/2026-10-24-observe-mvp.md docs/superpowers/plans/README.md
git commit -m "docs(observe): 24 h operator verification of the observe MVP"
```

---

## Out of scope

- **VictoriaMetrics (phase 1), VictoriaLogs (phase 2), Grafana (phase 3).** This includes the Prometheus-text output, the push to `/api/v1/import/prometheus`, vmalert rules and the journal-to-VictoriaLogs shipping. Entry gate: phase 0 has run for 7 days and the owner wants charts.
- **Phoenix changes:** `session.id` and header propagation in `docker/system-one/phoenix_observer.py`, the content opt-in switch, retention and the `dropped` counter exposure (research §6 and §7).
- **ai-kit changes:** the whole research §11 list, including rotation of `jev-answers.jsonl`, the ladder log and session headers. Jev log aggregates in the trend are also out: that file stores question text until ai-kit TODO 3 lands.
- **OmniRoute OTLP GenAI exporter:** verified on 3.8.53 and rejected (research §5). `OMNIROUTE_OTEL_ENDPOINT` stays unset.
- **A `make observe` target, or any new or duplicate make target:** excluded by the owner.
- **Host-level records** (MemAvailable, zram, host PSI, GPU busy): not part of the owner's MVP list. They can be added later as one more record kind.
- **Structured JSON lines from the healer and updater** (research §6 table): the trend parses the healer journal instead.
- **Removing the timers and store on `make clean` or `make disable-boot`:** the healer timer has the same gap today. Handle both together if wanted.
- **Alerting or notifications on the §9 checks:** the snapshot shows the data, and a person reads it.
- **OmniRoute's own `call_logs/` disk growth:** already bounded separately, so this plan adds nothing for it. The payload artifacts are capped at about 10 GB (`CALL_LOG_MAX_ENTRIES=20000` entries at up to `CALL_LOG_PIPELINE_MAX_SIZE_KB=512` KB each; about 4.7 GB at the measured average), the call-log rows are kept 180 days (`CALL_LOG_RETENTION_DAYS=180`, table cap 100,000 rows), and the settings are applied by `make update-omniroute` (a deferred recreate once OmniRoute is idle; nothing already on disk is purged). See `pylib/resources.py` (`OMNIROUTE_CALL_LOG_*`) and research §12. The observe store's own 365 days / 2 GB is a different, smaller store.
- **`eventLoopStall` health counters:** absent on the deployed 3.8.53 (pinned by the evidence test).

## Self-review (done while writing, repeated after review round 1)

1. **Spec coverage.**
   - Timer with no resident service: Task 10.
   - JSONL store and 365 d / 2 GB rotation: Task 1. Every file the collector opens is forced to 0600: Tasks 1 and 2.
   - Disk guard (100 GB / 15 %), with retention still enforced while it is active: Tasks 2 and 7.
   - Container lines (memory current/peak/swap/events/PSI, CPU delta, pids, restarts/uptime, Laya VRAM): Task 3, which reuses `live_resources`, `read_live`, `_vram` and `process_vram_mib`.
   - Locator cache revalidated through `/proc`, with StartedAt re-read on change: Task 3.
   - OmniRoute health counters (prime then read): Task 4. Evidence encoding stated and read tolerantly: Measured facts, Task 4.
   - Call summary with cursor, client rows only, probes ignored, no content: Task 5. Burst catch-up across ticks with a stated maximum (1,000 rows in a burst tick, at least 790 backlog rows per later tick), id-less rows as `unreadable`, a failed call-log source as `calls: null`: Task 5. It reuses `bounded_*`, `Profile.classify_path` and `epoch_ms`, and is grounded in the recorded evidence.
   - Laya counts: Task 6.
   - Crash consistency (write-ahead `pending`), one 30 s tick deadline with concurrent sources, container lifetime stamped on OmniRoute records, bounded `config`/`internal` error output: Task 7.
   - Tolerance of stopped containers (Task 3), OmniRoute down or login failure (Task 4), a corrupt last line (Task 1), a corrupt state file (Task 2), a failing or hanging source (Task 7) and exit 0 (Task 7).
   - Snapshot trends: peak/p95 per container for 1 h / 24 h / 7 d, events delta, guard 503 count, healer restarts and the last-24 h verdict (Task 8 renders them with baselines, lifetimes and `n/a`/`+` for unknown, Task 9 prints them).
   - Password read from `models.yml` and never logged: Tasks 4 and 7.
   - Tests: fake cgroup tree (Task 3), fake OmniRoute (Tasks 4 and 5), rotation, cap and modes (Tasks 1 and 2), cursor (Tasks 5 and 6), disk guard (Tasks 2 and 7), crash, deadline and config-error output (Task 7), trend rendering (Tasks 8 and 9), timer render (Task 10).
   - Operator checklist: Task 12. Out-of-scope list: above (OmniRoute's own storage policy stated as ~10 GB / 180 days).
2. **Placeholders.** None (a scan for TBD, TODO, FIXME and "similar to" finds only the unrelated ai-kit TODO 3 reference). Every code step has its code, and every run step names the command and the expected result.
3. **Type and name consistency.** The names used across tasks match:
   - `CachedLocator(cache, *, locate, proc, root)`, with `.podman_calls` and `.container_id`, used in Tasks 3 and 7 (Task 7 builds it over the container source's private state copy);
   - `container_records(..., locator=)`;
   - `omniroute_record(port, password, state, now, *, budget, sleep)` and the injected `omniroute(port, password, state, now)` in Task 7 (it receives the source's private state copy, which holds `calls`);
   - `new_calls(fetch, cursor, now_ms, *, profile, page, pages, head_pages, overlap)` with the cursor `{"floor", "seen", "resume"}`, used in Tasks 5 and 7 (`_calls_source`);
   - `laya_record(state, now, *, container, run)`;
   - `run_observe(..., deadline_s=TICK_DEADLINE_S)`, `_Source`, `_recover`, `_stamp_life`, `_prune` (Task 7) and the record field `life` read by `_life` in Task 8;
   - `counter_increase(samples) -> int | None`, `_window`, `_cell`, `_sum`, `_status`, `GATEWAY_TITLE` (Task 8) and `trend_report(store, now, healer)` and `healer_restarts(now, *, run)`, used in Task 9;
   - the test helpers `_row`, `_pages`, `_day_file`, `_fs`, `FakeRun`, `FakeLocate`, `_proc`, `REL`, `CID` are defined in Tasks 1, 2, 3, 5 and 6 before Tasks 7 and 8 use them;
   - `FakeRun` asserts `timeout == JOURNAL_TIMEOUT_S`, which Tasks 6 and 8 both use.
4. **Review Focus.** The nine lines above are pinned by tests in Tasks 8, 1, 4, 6, 5, 7 and 8.
5. **Finding map.** Every review finding of round 1 is mapped to a task in the Review log below.

## Review log

### Round 1

Reviewer verdicts: reviewer one, Issues Found (3 critical, 6 high, 2 medium); reviewer two, Issues Found (0 critical, 0 high, 2 medium).

Reviewer one, critical:

- **Bounded call-log paging cannot catch up** (Task 5). `new_calls` has a persisted `resume` offset: the head (newest 200 rows) is re-read every tick, a burst stores where the unread rows begin, and each later tick resumes there shifted by the new head rows, with the same page bound on every tick. Maximum catch-up is stated (1,000 rows in a burst tick, at least 790 backlog rows per later tick). Tests: `test_a_burst_beyond_the_page_bound_is_caught_up_exactly_once_with_the_same_bound`, `test_a_burst_catch_up_survives_new_rows_arriving_meanwhile`, `test_a_long_burst_drains_over_several_ticks_and_every_row_counts_once`.
- **Config-error output violates the privacy contract** (Task 7, Global Constraints). `cmd_observe` prints only `{"ok":false,"error":"config"}` or `"internal"`, never exception text. Tests with a missing file, malformed YAML carrying a secret and a parseable but invalid secret-bearing config, plus an unexpected exception.
- **OmniRoute storage policy misstated** (Out of scope). Now: payload artifacts about 10 GB (`CALL_LOG_MAX_ENTRIES=20000`, 512 KB each), 180 days of rows, applied by `make update-omniroute`, with the `pylib/resources.py` and research §12 references.

Reviewer one, high:

- **Execution commands vs the Python entry-point rule** (Global Constraints, Tasks 1-10, Task 4). The constraint states that the full gate is `make validate && make test` and that targeted `uv run pytest <path> [-k]` is the exact invocation inside `tools/test.sh`, the only sanctioned exception; the fake OmniRoute is spawned with `sys.executable` and the argv of `tests/omniroute_world.py`.
- **Record and cursor persistence not crash-consistent** (Task 7, Global Constraints). Write-ahead `pending` entry in `state.json`, `_recover` on the next run, prune after the commit and its failure ignored. Tests: `test_a_killed_tick_neither_loses_nor_double_counts` (two kill points) and `test_a_failing_prune_does_not_fail_the_tick_or_repeat_counts`.
- **Windowed counters omit boundary events** (Task 8). `_window` adds the newest earlier sample as baseline; `trend_report` reads one `BASELINE_S` before the 7 d window. Test: `test_windows_keep_a_baseline_sample_so_boundary_increases_count` (time-window and UTC-day boundary).
- **Counter resets lack lifetime identity** (Tasks 7 and 8). `run_observe` stamps `life` (container id + StartedAt) on the OmniRoute record; `counter_increase` takes it into account. Test: `test_gateway_counters_use_the_container_lifetime_not_only_a_decrease`.
- **Unknown telemetry rendered as zero** (Tasks 5 and 8). `calls: null` for a failed call-log source, `duration_known`, `counter_increase` returns `None` below two known values, `n/a` and `+` cells, lower bounds for unknown ticks and failed Laya ticks. Tests: `test_trend_renders_unknown_as_na_never_as_zero`, `test_counter_increase_survives_restarts_and_unknowns`, `test_a_failed_call_log_source_is_unknown_not_an_empty_summary`.
- **Source budgets can exceed the hard timeout** (Task 7, Task 10 comment, Global Constraints). `TICK_DEADLINE_S = 30` with concurrent daemon-thread sources over private state copies; a timed-out source becomes a `TimeoutError` record and finished sources are still persisted. Test: `test_a_slow_container_discovery_cannot_starve_the_other_sources`.

Reviewer one, medium:

- **Disk guard suspends retention** (Task 7). The guard branch calls `_prune`. Test: `test_disk_guard_stops_appending_logs_once_and_still_prunes` (expired file during an active guard).
- **Existing store files not guaranteed private** (Tasks 1 and 2). `os.fchmod(fd, 0o600)` after opening the day file, the temp state file and the lock. Tests: `test_append_forces_an_existing_permissive_day_file_to_0600`, `test_state_temp_and_lock_files_are_forced_to_0600_when_they_already_exist`.

Reviewer two, medium:

- **Id-less persisted rows vanish** (Task 5). Counted once as `unreadable` (keyed by timestamp, path, status and duration), never as a call. Test: `test_rows_without_an_id_are_unreadable_and_counted_once`.
- **Evidence encoding unstated** (Measured facts, Tasks 4 and 5). The encoding was read from the committed file (gzip JSON, flat pointer-keyed bodies and row shapes) and is stated; `_pointers` and `_evidence_rows` accept either encoding.
