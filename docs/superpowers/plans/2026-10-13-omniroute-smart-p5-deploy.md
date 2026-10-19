# OmniRoute Smart Routing P5: Deploy, Observe and Hand Off Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable smart routing on this host with a full backup and an explicit operator confirmation before every live change, then observe it:
- capture the 7-day baseline for the success metric before anything changes;
- enable `smart_routing`, build and start the derived image, create the management key, sync, probe and health-check;
- create `smart-execution` and `smart-planning` with the operator's member lists, keeping OmniRoute's fallback and forbidden-substring rules from an existing coding combo;
- point the local clients at the new combos;
- verify the plugin timer over 24 h, and give the operator a `make smart-metrics` report for the 30-day review;
- draft (not open) the upstream PRs, record the backlog, and document a tested rollback.

**Architecture:**
- **Two kinds of tasks.** Tasks 1–3 are repo changes (code, docs, drafts) with tests, run like any other plan. Tasks 4–10 are live operations on the production stack. Every live step marked **🔒 CONFIRM** stops, shows the operator exactly what will change, and runs only after an explicit "yes" in chat. A subagent executing a live task cannot ask the operator itself: it stops at each 🔒 step and returns the prepared change to the orchestrator, which asks.
- **Reuse, don't add** (one exception: the read-only `llmenv omniroute key-status`, Task 1 Step 3b, so no step can mint a key unannounced). Backups use `make backup`, `make combo-backup` and `make connections-backup`. Combos are created by `make combo-restore` from a seed file in the combo-backup format (`pylib/omniroute_combos.py`), so no new combo code is written. Clients are refreshed by `make setup-local-llm-agents`.
- **Metrics come from files the stack already writes:** `task-stats.json` (P3), `decisions.jsonl` (P3/P4), plus one new `sync-history.jsonl` line per plugin sync (Task 1), read by a small in-container script.

**Tech Stack:** Bash (`make` targets, `tools/lib.sh`), Python (`llmenv`, `pylib`), Node 26 in the image for the metrics script, podman, `jq`, `yq`.

**Spec:** `docs/prds/omniroute-smart-routing-v2.1-prd.md` (P1 Task 13 renames v2.0 to v2.1; before that, read v2.0): "Execution Phases → Phase 4: Deployment", "Acceptance Criteria → User Acceptance" and "Success metrics". Also read `docs/omniroute-smart-routing.md` (P4 Task 8), `docs/prds/omniroute-smart-routing-research/p4-test-report.md`, and the P1 spike report (S1 step 9 for the `usage_history` columns, S4 for whether the dashboard combo editor is safe, S5 for the timer and loopback findings).

## Preconditions

- P1–P4 are merged on `main`; `p4-test-report.md` has a PASS on every row.
- `bash tools/validate.sh && bash tools/test.sh && node --test 'omniroute-smart/**/*.test.mjs'` pass on `main`.
- The operator is present for Tasks 5–7 (🔒 steps). Task 4 needs no confirmation (backups and read-only queries). Task 8 needs the operator only if its conditional Step 2 runs. Task 9 needs the operator for Steps 2–3.

## Global Constraints

- **Confirmation before every live change.** A 🔒 step shows: the command(s), the files or API objects it changes, the backup that covers it, and how to undo it. It runs only after the operator answers yes for that step. A yes covers that step only.
- **Backups first.** Task 4 runs before any other live task. A live task whose backup is missing stops.
- **`make check-server` can mint a key.** It calls `llmenv omniroute issue-key`, which creates a new OmniRoute key (and rewrites `omniroute-api-key.json`) when the cached key is missing *or no longer valid in OmniRoute*. So no step runs `make check-server` directly: it runs `cs_safe` (defined with `key_ok` in Task 4 Step 1; define both again in any new shell), which first asks `llmenv omniroute key-status` (`key_ok`) whether `issue-key` would reuse the cached key (its id is listed in `GET /api/keys`, the test `ensure_api_key` applies) and skips check-server, saying so, otherwise. `make setup-local-llm-agents` calls the same `issue-key`, so Task 7 runs it only after `key_ok` passes. Minting a key is only ever the separate 🔒 step in Task 6 Step 1; when a verification needs check-server and `cs_safe` skipped, run that key check and 🔒 step first, then repeat the verification.
- **Secrets.** Never print, echo, `cat` or commit a key, the dashboard password, or the content of `~/.config/llm-env/omniroute-smart.env` or `omniroute-api-key.json`. Show `models.yml` changes only as a diff of the `smart_routing` section (`yq '.smart_routing'`), never the whole file (it holds `server.api_key` and `omniroute.initial_password`). The operator types keys into `omniroute-smart.env` themselves.
- **Remote System One consent stays off** unless the operator turns it on in the plugin page. P5 never sets a consent flag.
- **Never set** `OMNIROUTE_SMART_TEST_MODE` or `OMNIROUTE_PLUGIN_SMART_CATALOG_TEST_MODE` on the production container.
- **Upstream.** PR drafts are written as files. Nothing is pushed, forked or opened on GitHub without the operator's explicit approval for that PR.
- **Commits.** Commit repo changes after each task and never push.
- **Lanes are the operator's.** Combo members, judge model and arbiter model come from the operator at the 🔒 step, never from this plan.

## Review Focus

1. **A step that fails halfway through enablement** (for example `make setup` exits 2 because the anchors moved, or the first sync never writes a scorecard) must leave the operator on a working stack: stop, and run Task 10's rollback for the steps already done. Pinned in Task 5.
2. **A seed combo whose name already exists** must not overwrite the operator's combo: `make combo-restore` without `OVERWRITE` skips it, and the task reports the skip. Pinned in Task 6.
3. **The baseline window** must be the 7 days *before* enablement. Capturing it after enabling would mix smart traffic into it. Pinned in Task 4.
4. **A `usage_history` without `status` or `combo_strategy`** (S1 step 9 result) must switch the baseline to OmniRoute logs, not silently compute a zero. Pinned in Task 4.
5. **The plugin timer dying silently** must show up within a day, not at the 30-day review. Pinned in Task 8 (24 h check) and by the existing scorecard-age row.

---

## File Structure

| Path | Responsibility |
|---|---|
| `omniroute-smart/plugins/smart-catalog/lib/sync.mjs` (+ test) | Append one `sync-history.jsonl` line per sync attempt |
| `omniroute-smart/probe/smart-metrics.mjs` (+ test) | Success metrics from `task-stats.json`, `decisions.jsonl`, `sync-history.jsonl` |
| `scripts/smart-metrics.sh`, `Makefile`, `scripts/help.sh`, `tests/test_shell.py` | `make smart-metrics` |
| `README.md` | Link to the operator guide |
| `docs/upstream/omniroute-pluggable-strategies.md`, `docs/upstream/omniroute-plugin-scoped-env.md`, `docs/upstream/omniroute-decision-provider.md` | Upstream PR / proposal drafts |
| `docs/prds/omniroute-smart-routing-research/backlog.md` | Backlog items |
| `docs/prds/omniroute-smart-routing-research/p5-deploy-log.md` | What was done live, when, with which backup |
| `docs/prds/omniroute-smart-routing-research/p5-metrics.md` | Baseline, 24 h check, 30-day report |

---

### Task 1: Sync history and `make smart-metrics`

**Files:**
- Modify: `omniroute-smart/plugins/smart-catalog/lib/sync.mjs`
- Create: `omniroute-smart/probe/smart-metrics.mjs`, `omniroute-smart/probe/test/smart-metrics.test.mjs`, `scripts/smart-metrics.sh`
- Modify: `omniroute-smart/plugins/smart-catalog/test/e2e.test.mjs` (P2's `runSync` e2e tests), `Makefile`, `scripts/help.sh`, `tests/test_shell.py`

**Interfaces:**
- Consumes: P2 `appendJsonl(path, record, {maxBytes, files})` and `runSync`; P3 `task-stats.json` (`{version, days: {YYYY-MM-DD: {<combo>: {task_kind, requests, decisions, attempts, quota_attempts, fusion_degraded, fusion_failed}}}}`; `decisions` is counted on the day the request began, so a finished day has `decisions == requests`); P3/P4 `decisions.jsonl` lines.
- Produces:
  - `/app/data/smart-routing/sync-history.jsonl`: one `{at, ok, error?}` line per sync attempt, rotated at 1 MB × 2;
  - `since` is a day (`YYYY-MM-DD`, from midnight UTC) or an ISO timestamp;
  - `metrics({stats, decisions, history, intervalHours, since, now}) → {window, unfit: {quota_attempts, attempts, rate, source}, freshness: {pct, worst_gap_h}, requests: {client, decisions, checked_days, mismatched_days}, split: {subscription, metered}, fusion: {analysis, fusion, degraded, failed, rate}, log_coverage: {from, complete}}`. `split` and `fusion` come from the retained (rotated) decision lines only: `log_coverage.from` is the oldest retained line in the window and `complete` says whether it reaches back to `since`; `fusion.rate` = (fusion + degraded + failed) ÷ analysis requests;
  - `node /opt/omniroute-smart/probe/smart-metrics.mjs [--since YYYY-MM-DD]` prints that JSON;
  - `make smart-metrics [SINCE=YYYY-MM-DD]` runs it in the `omniroute` container and prints it.

- [ ] **Step 1: Write the failing tests**

Append to `omniroute-smart/plugins/smart-catalog/test/e2e.test.mjs` (it already has `runSync`, `fakeOmniRoute`, `aaFetch`, `settings`, `makeApi` and `writeJsonAtomic` in scope):

```js
test("every sync attempt appends one sync-history line, ok or not", async () => {
  const dataDir = mkdtempSync(join(tmpdir(), "e2e-"));
  await runSync({ api: makeApi({ base: "http://127.0.0.1:9", key: "k" }), env: {}, dataDir, dbPath: "/x", now: Date.parse("2026-10-13T00:00:00Z"), fetchImpl: aaFetch });
  writeJsonAtomic(join(dataDir, "settings.json"), settings);
  const srv = await fakeOmniRoute({ bumpMode: "off", puts: [], catalog: [], combos: [] });
  await runSync({ api: makeApi({ base: `http://127.0.0.1:${srv.address().port}`, key: "k" }), env: {}, dataDir,
    dbPath: join(dataDir, "absent.sqlite"), now: Date.parse("2026-10-13T06:00:00Z"), fetchImpl: aaFetch });
  srv.close();
  const lines = readFileSync(join(dataDir, "sync-history.jsonl"), "utf8").trim().split("\n").map((l) => JSON.parse(l));
  assert.deepEqual(lines.map((l) => [l.at, l.ok]), [["2026-10-13T00:00:00.000Z", false], ["2026-10-13T06:00:00.000Z", true]]);
  assert.match(lines[0].error, /settings\.json missing/);
});
```

```js
// omniroute-smart/probe/test/smart-metrics.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { metrics } from "../smart-metrics.mjs";

const H = 3600e3;
const T0 = Date.parse("2026-10-01T00:00:00Z");
const stats = { version: 1, days: {
  "2026-09-30": { "smart-execution": { task_kind: {}, requests: 99, attempts: 99, quota_attempts: 99, fusion_degraded: 0, fusion_failed: 0 } },
  "2026-10-01": { "smart-execution": { task_kind: { execute_edit: 8 }, requests: 8, attempts: 10, quota_attempts: 1, fusion_degraded: 0, fusion_failed: 0 },
                  "smart-planning": { task_kind: { plan: 2 }, requests: 2, attempts: 4, quota_attempts: 0, fusion_degraded: 1, fusion_failed: 0 } },
} };
const line = (lane, cls, kind) => ({ at: "2026-10-01T05:00:00Z", outcome: { lane }, candidates: { ranked: [{ id: "p/x", cls }] },
  attempts: [{ target: "p/x", status: 200 }], classification: { answers: { task_kind: { value: kind } } } });

test("log coverage is complete when the retained lines hold every decision task-stats counted", () => {
  const cov = (counted) => metrics({ stats: { days: { "2026-10-01": { "smart-execution": { requests: counted, decisions: counted } } } },
    decisions: [line("single", 1, "plan"), line("single", 1, "plan")], history: [], intervalHours: 6, since: "2026-10-01", now: T0 + 12 * H }).log_coverage;
  assert.deepEqual(cov(2), { from: "2026-10-01T05:00:00.000Z", complete: true });  // enablement day: nothing rotated away
  assert.equal(cov(3).complete, false);                                             // one line rotated out
});

test("unfit rate, split, fusion counts and the --since window", () => {
  const m = metrics({ stats, decisions: [line("single", 1, "execute_edit"), line("single", 3, "execute_edit"), line("fusion", 1, "plan"), line("fusion_degraded", 1, "plan")],
    history: [], intervalHours: 6, since: "2026-10-01", now: T0 + 12 * H });
  assert.deepEqual(m.unfit, { quota_attempts: 1, attempts: 14, rate: 1 / 14, source: "task-stats" });
  assert.deepEqual(m.split, { subscription: 3, metered: 1 });
  assert.deepEqual(m.fusion, { analysis: 2, fusion: 1, degraded: 1, failed: 0, rate: 1 });
  assert.deepEqual(m.log_coverage, { from: "2026-10-01T05:00:00.000Z", complete: false }); // the fixture's stats count no decisions
  assert.deepEqual(m.requests, { client: 10, decisions: 0, checked_days: 0, mismatched_days: [] }); // 10-01 is not finished yet
});

test("a timestamp window counts unfit starts from the decision lines inside it, not whole days", () => {
  const at = (t, statuses) => ({ at: t, outcome: { lane: "single" }, attempts: statuses.map((status) => ({ target: "p/x", status })) });
  const m = metrics({ stats, decisions: [at("2026-10-01T05:00:00Z", [429, 429, 200]), at("2026-10-01T07:00:00Z", [429, 200])],
    history: [], intervalHours: 6, since: "2026-10-01T06:00:00Z", now: T0 + 12 * H });
  assert.deepEqual(m.unfit, { quota_attempts: 1, attempts: 2, rate: 0.5, source: "decisions" });
});

test("split follows the target that answered, not the top-ranked one", () => {
  const d = { at: "2026-10-01T05:00:00Z", outcome: { lane: "single" }, classification: { answers: { task_kind: { value: "execute_edit" } } },
    candidates: { ranked: [{ id: "sub/a", cls: 1 }, { id: "met/b", cls: 3 }] },
    attempts: [{ target: "sub/a", status: 429 }, { target: "met/b", status: 200 }] };
  const m = metrics({ stats: { days: {} }, decisions: [d], history: [], intervalHours: 6, since: "2026-10-01", now: T0 + 12 * H });
  assert.deepEqual(m.split, { subscription: 0, metered: 1 });
});

test("every finished day must have decisions == requests; today is not judged", () => {
  const s = (requests, decisions) => ({ "smart-execution": { requests, decisions } });
  const r = (d2) => metrics({ stats: { days: { "2026-10-01": s(50, 50), "2026-10-02": s(3, d2), "2026-10-03": s(2, 1) } }, decisions: [], history: [],
    intervalHours: 6, since: "2026-10-01", now: Date.parse("2026-10-03T12:00:00Z") }).requests;
  // last finished day = the UTC day of now − 24 h 37 min = 10-02; 10-03 still has a request in flight
  assert.deepEqual(r(3), { client: 55, decisions: 54, checked_days: 2, mismatched_days: [] });
  assert.deepEqual(r(2).mismatched_days, [{ day: "2026-10-02", requests: 3, decisions: 2 }]);
});

test("hours before the first sync in the window do not count as stale", () => {
  // enabled at 17:00 UTC: first ok at 17 h, then every 6 h; window opens at midnight
  const history = [17, 23, 29, 35].map((h) => ({ at: new Date(T0 + h * H).toISOString(), ok: true }));
  const m = metrics({ stats: { days: {} }, decisions: [], history, intervalHours: 6, since: "2026-10-01", now: T0 + 36 * H });
  assert.equal(m.freshness.worst_gap_h, 6);
  assert.equal(m.freshness.pct, 1);
});

test("a gap that started before the window counts from the last sync before it", () => {
  // last ok 20 h before the window, next at 10 h, window 24 h: stale from −13.5 h (clipped to 0) to 10 h, then 16.5 h → 24 h
  const history = [-20, 10].map((h) => ({ at: new Date(T0 + h * H).toISOString(), ok: true }));
  const m = metrics({ stats: { days: {} }, decisions: [], history, intervalHours: 6, since: "2026-10-01", now: T0 + 24 * H });
  assert.equal(m.freshness.worst_gap_h, 30);
  assert.ok(Math.abs(m.freshness.pct - (1 - 17.5 / 24)) < 1e-9);
});

test("freshness counts only the time a scorecard was older than interval + 30 min", () => {
  // successes at 0 h, 6 h and 20 h; interval 6 h → limit 6.5 h; the 6 h → 20 h gap is stale for 7.5 h; window 24 h
  const history = [0, 6, 20].map((h) => ({ at: new Date(T0 + h * H).toISOString(), ok: true }));
  const m = metrics({ stats: { days: {} }, decisions: [], history, intervalHours: 6, since: "2026-10-01", now: T0 + 24 * H });
  assert.equal(m.freshness.worst_gap_h, 14);
  assert.ok(Math.abs(m.freshness.pct - (1 - 7.5 / 24)) < 1e-9);
});
```

In `tests/test_shell.py`, assert the Makefile exposes `smart-metrics:` with the `tools/run-target.sh` recipe, and that `scripts/smart-metrics.sh` refuses when `smart_routing.enabled` is false (exit ≠ 0, message `smart_routing.enabled is false`), using the same harness P2 Task 10 used for `smart-probe.sh`.

- [ ] **Step 2: Run them and see them fail**

Run: `node --test 'omniroute-smart/**/*.test.mjs' && uv run pytest tests/test_shell.py -k smart_metrics -v`
Expected: FAIL (no `sync-history.jsonl`, no `smart-metrics.mjs`, no target).

- [ ] **Step 3: Implement**

In `sync.mjs`, add `appendJsonl` to the `files.mjs` import and write one line on each path (success after the `sync-state.json` write; failure in the `catch`):

```js
const HISTORY = { maxBytes: 1_000_000, files: 2 };
// success path
    appendJsonl(join(dataDir, "sync-history.jsonl"), { at: attempt, ok: true }, HISTORY);
// catch path
    appendJsonl(join(dataDir, "sync-history.jsonl"), { at: attempt, ok: false, error: String(error.message).slice(0, 200) }, HISTORY);
```

```js
// omniroute-smart/probe/smart-metrics.mjs
// PRD success metrics from the files the stack already writes. Runs in the omniroute container; prints JSON only.
import { readFileSync, realpathSync } from "node:fs";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

const H = 3600e3;
const jsonl = (path) => { try { return readFileSync(path, "utf8").split("\n").filter(Boolean).flatMap((l) => { try { return [JSON.parse(l)]; } catch { return []; } }); } catch { return []; } };

export function metrics({ stats, decisions, history, intervalHours, since, now }) {
  // since: a day (YYYY-MM-DD, from midnight UTC) or an ISO timestamp (for the 24 h check)
  const start = Date.parse(since.length === 10 ? `${since}T00:00:00Z` : since);
  const sinceDay = new Date(start).toISOString().slice(0, 10);
  const days = Object.entries(stats.days ?? {}).filter(([d]) => d >= sinceDay).flatMap(([, combos]) => Object.values(combos));
  const sum = (k) => days.reduce((a, c) => a + (c[k] ?? 0), 0);
  const lines = decisions.filter((d) => Date.parse(d.at) >= start);
  // a day window uses task-stats (kept 90 days, survives log rotation); a timestamp window starts mid-day, so it counts the
  // attempts in its own decision lines (recent enough that the rotated log still holds them)
  const fromLines = since.length !== 10;
  const att = lines.flatMap((d) => d.attempts ?? []);
  const unfit = fromLines
    ? { quota_attempts: att.filter((a) => a.status === 429).length, attempts: att.length, source: "decisions" }
    : { quota_attempts: sum("quota_attempts"), attempts: sum("attempts"), source: "task-stats" };
  unfit.rate = unfit.attempts ? unfit.quota_attempts / unfit.attempts : null;

  // class of the target that actually answered (the last 2xx attempt), not the top-ranked one: a fallback may be metered
  const cls = (d) => {
    const served = [...(d.attempts ?? [])].reverse().find((a) => a.status >= 200 && a.status < 300)?.target;
    return (d.candidates?.ranked ?? []).find((c) => c.id === served)?.cls;
  };
  const analysis = lines.filter((d) => ["plan", "review_audit"].includes(d.classification?.answers?.task_kind?.value));
  const lane = (l) => lines.filter((d) => d.outcome?.lane === l).length;

  const oks = history.filter((h) => h.ok).map((h) => Date.parse(h.at)).sort((a, b) => a - b);
  const prev = oks.filter((t) => t < start).at(-1); // the scorecard in use when the window opened
  const inWin = oks.filter((t) => t >= start && t <= now);
  const limit = intervalHours * H + 30 * 60e3;
  // Before the very first sync there is no scorecard to age (enablement window), so only then start at the first ok.
  const edges = prev != null ? [prev, ...inWin, now] : inWin.length ? [...inWin, now] : [];
  const from = prev != null ? start : edges[0];
  let stale = 0, worst = 0;
  for (let i = 1; i < edges.length; i++) {
    worst = Math.max(worst, edges[i] - edges[i - 1]);
    stale += Math.max(0, edges[i] - Math.max(edges[i - 1] + limit, from)); // stale time, clipped to the window
  }
  return {
    window: { since: new Date(start).toISOString(), until: new Date(now).toISOString() },
    unfit,
    freshness: edges.length ? { pct: now > from ? 1 - stale / (now - from) : null, worst_gap_h: worst / H } : { pct: null, worst_gap_h: null }, // null: no sync recorded
    requests: requestCoverage(stats, sinceDay, now),
    split: { subscription: lines.filter((d) => cls(d) === 1 || cls(d) === 2).length, metered: lines.filter((d) => cls(d) === 3).length },
    fusion: { analysis: analysis.length, fusion: lane("fusion"), degraded: lane("fusion_degraded"), failed: lane("fusion_failed"),
      rate: analysis.length ? (lane("fusion") + lane("fusion_degraded") + lane("fusion_failed")) / analysis.length : null },
    // split and fusion are read from the retained decision lines; rotation can drop the oldest ones
    // complete: the retained lines hold every decision task-stats counted since sinceDay (a mid-day ISO window counts the
    // whole first day, so it can only say false too often, never true wrongly); a reduce, not a spread, for 30-day logs
    log_coverage: (() => {
      const first = lines.reduce((m, d) => Math.min(m, Date.parse(d.at)), Infinity);
      const counted = days.reduce((a, c) => a + (c.decisions ?? 0), 0);
      return Number.isFinite(first) ? { from: new Date(first).toISOString(), complete: counted > 0 && lines.length >= counted } : { from: null, complete: false };
    })(),
  };
}

/** Client requests vs written decisions, both from task-stats (kept 90 days, so no log-rotation gaps). P3 counts a decision
 *  on the day its request began, so every finished day must match exactly. The last finished day is the UTC day of
 *  now − 24 h 37 min (P3's SWEEP_AGE_MS sweep and the 10 s flush are done), the same rule as P3's check-server row. */
export function requestCoverage(stats, sinceDay, now) {
  const lastFinished = new Date(now - 86400e3 - 2220e3).toISOString().slice(0, 10);
  const inWin = Object.keys(stats.days ?? {}).filter((d) => d >= sinceDay).sort();
  const total = (d, k) => Object.values(stats.days[d]).reduce((a, c) => a + (c[k] ?? 0), 0);
  const checked = inWin.filter((d) => d <= lastFinished);
  return {
    client: inWin.reduce((a, d) => a + total(d, "requests"), 0),
    decisions: inWin.reduce((a, d) => a + total(d, "decisions"), 0),
    checked_days: checked.length,
    mismatched_days: checked.filter((d) => total(d, "decisions") !== total(d, "requests"))
      .map((d) => ({ day: d, requests: total(d, "requests"), decisions: total(d, "decisions") })),
  };
}

if (process.argv[1] && import.meta.url === pathToFileURL(realpathSync(process.argv[1])).href) {
  const dir = process.env.OMNIROUTE_SMART_DATA_DIR || "/app/data/smart-routing";
  const read = (f) => { try { return JSON.parse(readFileSync(join(dir, f), "utf8")); } catch { return null; } };
  const stats = read("task-stats.json") ?? { days: {} };
  const i = process.argv.indexOf("--since");
  const since = i > 0 ? process.argv[i + 1] : (Object.keys(stats.days).sort()[0] ?? new Date().toISOString().slice(0, 10));
  // appendJsonl keeps decisionLogFiles files in all: the live one plus .1 … .(n−1), oldest last
  const n = read("settings.json")?.decisionLogFiles ?? 3;
  const decisions = Array.from({ length: n - 1 }, (_, k) => n - 1 - k).flatMap((k) => jsonl(join(dir, `decisions.jsonl.${k}`)))
    .concat(jsonl(join(dir, "decisions.jsonl")));
  const history = [1].flatMap((n) => jsonl(join(dir, `sync-history.jsonl.${n}`))).concat(jsonl(join(dir, "sync-history.jsonl"))); // incl. syncs before --since
  const intervalHours = Number(process.env.SMART_SYNC_INTERVAL_HOURS) || 6;
  console.log(JSON.stringify(metrics({ stats, decisions, history, intervalHours, since, now: Date.now() }), null, 2));
}
```

The rotated-file names (`.1`, `.2`, …) must match P2's `appendJsonl` rotation; check `lib/files.mjs` and adapt the suffixes if it numbers differently.

The class numbers follow P3's `rank` (`cls` 1 and 2 are subscriptions, 3 is metered). If P3 used other values, map them here and in the test.

`scripts/smart-metrics.sh`, in the style of `scripts/smart-probe.sh`:

```bash
#!/usr/bin/env bash
# smart-metrics.sh — print the smart-routing success metrics (PRD "Success metrics") from the running omniroute container.
set -euo pipefail
# shellcheck disable=SC1091 # Resolved from this script at runtime.
# shellcheck source=../tools/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/../tools/lib.sh"
require_cmd podman yq jq
[ "$(yq -r '.smart_routing.enabled' "$CONFIG_PATH")" = "true" ] || die "smart_routing.enabled is false"
interval="$(podman exec omniroute node /opt/omniroute-smart/probe/smart-status.mjs | jq -r '.sync_interval_hours // 6')"
args=(); [ -n "${SINCE:-}" ] && args=(--since "$SINCE")
podman exec -e SMART_SYNC_INTERVAL_HOURS="$interval" omniroute node /opt/omniroute-smart/probe/smart-metrics.mjs "${args[@]}"
```

`Makefile`: `smart-metrics:` with `@bash tools/run-target.sh smart-metrics -- env SINCE=$(SINCE) bash scripts/smart-metrics.sh`, added to `.PHONY`; `scripts/help.sh`: `_help_line "smart-metrics" "Print smart-routing success metrics (SINCE=YYYY-MM-DD)"`.

- [ ] **Step 3b: `llmenv omniroute key-status` (read-only)**

Tasks 4–7 must know, before `make check-server` or `make setup-local-llm-agents`, whether `issue-key` would reuse the cached key or mint one. `ensure_api_key` reuses it only when the cached id is listed in `GET /api/keys`, so the check asks the same question. It never creates a key and never prints one.

Tests first. In `tests/test_remote_setup.py`, next to the `ensure_api_key` listing tests (same fake server):

```python
def test_cached_key_status_reports_reuse_without_creating_a_key(tmp_path):
    _FakeOmniRouteHandler.keys_created = 0
    fake_omniroute, fake_thread = _start_server(_FakeOmniRouteHandler)
    try:
        base_url = f"http://127.0.0.1:{fake_omniroute.server_address[1]}"
        cache_path = tmp_path / "api-key.json"
        write_cached_key(cache_path, "key-id", "sk-cached-value")
        assert remote_setup_module.cached_key_status(base_url, "dashboard-pw", cache_path) == "reuse"
        write_cached_key(cache_path, "revoked-id", "sk-cached-value")
        assert remote_setup_module.cached_key_status(base_url, "dashboard-pw", cache_path) == "would-mint"
        assert _FakeOmniRouteHandler.keys_created == 0
    finally:
        fake_omniroute.shutdown()
        fake_thread.join()


def test_cached_key_status_without_a_cache_would_mint(tmp_path):
    # no request is needed: the base URL is never contacted
    assert remote_setup_module.cached_key_status("http://127.0.0.1:9", "pw", tmp_path / "none.json") == "would-mint"
```

In `tests/test_cli.py`, next to `test_omniroute_issue_key_prints_the_key`:

```python
def test_omniroute_key_status_exits_1_when_issue_key_would_mint(tmp_path, monkeypatch, capsys):
    import llmenv

    config = _write_omniroute_test_config(tmp_path, omniroute_port=20128, initial_password="dash-pw")
    monkeypatch.setattr(llmenv, "cached_key_status", lambda base_url, pw, cache_path: "would-mint")
    assert llmenv.main(["omniroute", "key-status", "--config", str(config)]) == 1
    assert json.loads(capsys.readouterr().out) == {"status": "would-mint"}
```

Run `uv run pytest tests/test_remote_setup.py tests/test_cli.py -k "key_status" -v`: FAIL (no `cached_key_status`).

Then, in `pylib/remote_setup.py`, share the listing test with `ensure_api_key` (replace its inline `any(...)` with `_listed(keys, cached["id"])`):

```python
def _listed(keys: list[Any], key_id: str) -> bool:
    return any(isinstance(k, dict) and k.get("id") == key_id for k in keys)


def cached_key_status(base_url: str, dashboard_password: str, cache_path: Path) -> str:
    """Read-only twin of ensure_api_key's reuse test: "reuse" when the cached key's id is listed in GET /api/keys,
    "would-mint" otherwise. Never creates a key or rewrites the cache."""
    cached = read_cached_key(cache_path)
    if cached is None:
        return "would-mint"
    listing = _request("GET", f"{base_url}/api/keys", _login(base_url, dashboard_password))
    return "reuse" if _listed(_extract_keys(listing), cached["id"]) else "would-mint"
```

In `llmenv.py`: import `cached_key_status` next to `ensure_api_key`, add `"key-status"` to the `omniroute` action choices, and before the `issue-key` fallthrough in `cmd_omniroute`:

```python
    if args.action == "key-status":
        status = cached_key_status(base_url, initial_password, Path(args.config).parent / "omniroute-api-key.json")
        return emit({"status": status}, 0 if status == "reuse" else 1)
```

- [ ] **Step 4: Run the tests, then commit**

Run: `node --test 'omniroute-smart/**/*.test.mjs' && uv run pytest tests/test_shell.py -k smart -v && make validate && make test`
Expected: PASS.

```bash
git add omniroute-smart scripts/smart-metrics.sh Makefile scripts/help.sh tests/test_shell.py pylib/remote_setup.py llmenv.py tests/test_remote_setup.py tests/test_cli.py
git commit -m "feat(smart-routing): sync history, make smart-metrics and omniroute key-status"
```

---

### Task 2: README link, upstream drafts and backlog

**Files:**
- Modify: `README.md` (section "Configuration" or "Commands": one paragraph linking `docs/omniroute-smart-routing.md`)
- Create: `docs/upstream/omniroute-pluggable-strategies.md`, `docs/upstream/omniroute-plugin-scoped-env.md`, `docs/upstream/omniroute-decision-provider.md`, `docs/prds/omniroute-smart-routing-research/backlog.md`

**Interfaces:**
- Consumes: P1's `sites.mjs` (what each patch site does), the spike report, the PRD.
- Produces: three drafts the operator can turn into upstream PRs or issues, and the backlog.

- [ ] **Step 1: Write the drafts**

Each draft has: **Title**, **Problem** (what llm-env had to patch, with the site ids), **Proposal** (the smallest upstream API that removes the patch), **Compatibility** (built-in strategies unchanged), **Tests** (what upstream would need), and **Status: draft, not opened**.
- `omniroute-pluggable-strategies.md`: patch sites 1–5. Proposal: a strategy registry that loads modules from `OMNIROUTE_STRATEGIES_DIR`, with `prelude`, `order` and `observe` hooks, a per-request store, and an injected context (the keys from the S1 report). Reference upstream #16206 (the registry refactor) and how the proposal fits on it.
- `omniroute-plugin-scoped-env.md`: patch site 6. Proposal: pass `OMNIROUTE_PLUGIN_<NAME>_*` to that plugin's child only.
- `omniroute-decision-provider.md`: the owner's idea as an issue, not a PR. A System One / decision provider type whose answers can drive "decision combos".

State in each draft: when both PRs merge, patch items 1–6 are deleted from `omniroute-smart/patch/sites.mjs`, and the derived image becomes a plain image plus mounted modules.

- [ ] **Step 2: Write the backlog**

`backlog.md`, one entry each, with the reason it was deferred and the trigger to pick it up:
- a metered-spend cap;
- the historic quality ranker (learn Q from outcomes in `decisions.jsonl`);
- enrichment for models with no AA or Arena score;
- anything left open by `p4-test-report.md` or the S4 result.

- [ ] **Step 3: Validate and commit**

Run: `bash tools/validate.sh`
Expected: PASS.

```bash
git add README.md docs/upstream docs/prds/omniroute-smart-routing-research/backlog.md
git commit -m "docs(smart-routing): README link, upstream PR drafts and backlog"
```

---

### Task 3: Pre-deploy review gate

- [ ] **Step 1: Code review of P5's repo changes**

Use superpowers:requesting-code-review for Tasks 1–2 (BASE = the commit before Task 1). Fix Critical and Important findings before any live task.

- [ ] **Step 2: Record the plan for the live window**

Create `docs/prds/omniroute-smart-routing-research/p5-deploy-log.md` with a header and an empty table: `| time (UTC) | task/step | command | result | backup used |`. Every live step appends a row. Commit it.

```bash
git add docs/prds/omniroute-smart-routing-research/p5-deploy-log.md
git commit -m "docs(smart-routing): P5 deploy log"
```

---

### Task 4: Backups and the 7-day baseline (live, read-only toward the stack)

**Interfaces:**
- Consumes: `make backup`, `make check-server`, the P1 S1 step 9 result (`usage_history` columns).
- Produces: a backup set, the production snapshot, and the baseline numbers in `p5-metrics.md`.

- [ ] **Step 1: Snapshot and backups**

```bash
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
podman inspect --format '{{.ImageName}} {{.State.StartedAt}}' omniroute
make backup
install -m 600 ~/.config/llm-env/models.yml ~/.config/llm-env/models.yml.pre-smart-routing-"$STAMP"
install -m 600 ~/.config/llm-env/docker-compose.yml ~/.config/llm-env/docker-compose.yml.pre-smart-routing-"$STAMP"
if [ -f ~/.config/llm-env/omniroute-smart.env ]; then install -m 600 ~/.config/llm-env/omniroute-smart.env ~/.config/llm-env/omniroute-smart.env.pre-smart-routing-"$STAMP"; fi
ls -l ~/.config/llm-env/*pre-smart-routing-"$STAMP" ~/.config/llm-env/combo-backups | tail -n 6
# key_ok: `llmenv omniroute issue-key` would reuse the cached key, not mint: the same test it applies (the cached id is in
# GET /api/keys), via the read-only key-status action from Task 1. Prints only "reuse" or "would-mint", never the key.
# key-status output is only {"status": …} or {"error": …}; a server or login error is not a "would mint"
key_ok() {
  local out; out="$(uv run llmenv.py omniroute key-status 2>&1)" && return 0
  case "$out" in *would-mint*) echo "issue-key would mint a new key (cached key missing or no longer listed)";; *) echo "key-status failed: $out";; esac
  return 1
}
# make check-server, only when it cannot mint a key
cs_safe() { if key_ok; then make check-server; else echo "check-server skipped (see above)"; fi; }
cs_safe 2>&1 | tail -n 30
```

`install` copies without printing content. Record in the deploy log: `STAMP` (later tasks, possibly in another session, read it from there), the image, `StartedAt`, the backup paths, and whether `cs_safe` ran check-server and it was fully green (or why it skipped). If it was not, list the red rows; smart routing must not be blamed later for rows that were already red.

Also back up the OmniRoute data volume (`make backup` covers only combos and connection metadata). Find the volume the production container really mounts, take a consistent SQLite snapshot through SQLite itself, and export the whole volume:

```bash
VOL="$(podman inspect --format '{{range .Mounts}}{{if eq .Destination "/app/data"}}{{.Name}}{{end}}{{end}}' omniroute)"; echo "data volume: $VOL"
umask 077   # every backup file below is created mode 600 (they hold encrypted provider keys)
BK=~/.config/llm-env/backups/smart-routing-"$STAMP"; install -d -m 700 "$BK"
podman exec omniroute rm -f /tmp/storage-snapshot.sqlite   # VACUUM INTO refuses an existing target (a failed earlier try)
podman exec omniroute node -e 'const {DatabaseSync}=require("node:sqlite");new DatabaseSync("/app/data/storage.sqlite",{readOnly:true}).exec("VACUUM INTO '\''/tmp/storage-snapshot.sqlite'\''")'
podman cp omniroute:/tmp/storage-snapshot.sqlite "$BK/storage.sqlite" && podman exec omniroute rm -f /tmp/storage-snapshot.sqlite
podman volume export "$VOL" > "$BK/omniroute-data.tar"
chmod 600 "$BK"/*; stat -c '%a %n' "$BK"/*   # expect 600 for every file (podman cp keeps the in-container mode)
```

The snapshot is written to the container's `/tmp` (not the volume) and removed right after; both files hold encrypted provider keys, so they stay mode 600 and are never printed or committed. If `VACUUM INTO` is refused on a read-only handle in this Node version, open the database without `readOnly` for that one statement (it only reads the source). Check the tar is complete without printing names: `tar -tf "$BK/omniroute-data.tar" | grep -c 'storage.sqlite'` is nonzero. If `podman volume export` fails or leaves out files it cannot read (the volume's mode-600 files belong to the container's `node` user), take the tar from inside a throwaway container instead: `podman run --rm --user 0:0 -v "$VOL":/data:ro --entrypoint tar "<current image>" -C /data -cf - . > "$BK/omniroute-data.tar"`, and record which method worked in the deploy log. Record `VOL` and `BK` in the deploy log.

- [ ] **Step 2: The 7-day baseline (before enabling)**

Using the columns S1 step 9 recorded, count per-attempt rows for `priority` combos over the last 7 days, read-only:

```bash
podman exec omniroute node -e '
const {DatabaseSync}=require("node:sqlite");const d=new DatabaseSync("/app/data/storage.sqlite",{readOnly:true});
const since=new Date(Date.now()-7*864e5).toISOString();
const q=(w)=>d.prepare(`SELECT COUNT(*) n FROM usage_history WHERE combo_strategy=? AND <time column> >= ? ${w}`).get("priority",since).n;
console.log(JSON.stringify({since, all:q(""), quota:q("AND <quota/429 predicate>")}))'
```

Fill `<time column>` and `<quota/429 predicate>` from the S1 report, and match `since` to the column's storage type shown by the same report or PRAGMA (ISO text → the `toISOString()` value as written; epoch milliseconds → `Date.now()-7*864e5` as a number). SQLite compares an INTEGER with TEXT as never-equal, so a type mismatch silently counts 0. Before recording the baseline, check that `all` is nonzero on this actively used host. If the report does not name the time column, read the schema first, read-only: `podman exec omniroute node -e 'const {DatabaseSync}=require("node:sqlite");console.log(JSON.stringify(new DatabaseSync("/app/data/storage.sqlite",{readOnly:true}).prepare("PRAGMA table_info(usage_history)").all().map(c=>c.name)))'`. Use the columns (for example `status = 429`, or the error-class column if one exists). If S1 found that `status` or `combo_strategy` is missing, try the OmniRoute logs instead, deriving both patterns here (S1 did not record any):
Raw log lines can hold request text or keys, so they are never printed; only what a pattern itself matched is shown.
1. Explore with `grep -oiE` (prints the matched text only) and counts: `podman logs --since 168h omniroute 2>&1 | grep -oiE '<candidate pattern>' | sort | uniq -c | sort -rn | head`. Candidates: for quota rejections, patterns around `\b429\b`, `quota`, `rate.?limit`; for combo attempts, the log's own per-attempt marker (for example a `combo` or target-attempt prefix the matched text reveals).
2. Pick a quota pattern that matches one line per rejected upstream attempt and an attempt pattern that matches one line per upstream attempt of a combo. Validate without raw lines: both counts (`grep -ciE`) are nonzero, quota ≤ attempts, and the `uniq -c` of each pattern's matched text shows only the intended kind of token.
3. If no pair passes that validation, **stop before Task 5**: the PRD requires the 7-day baseline before enabling. Record "baseline: not measurable (usage_history lacks <column>; no log line identifies combo attempts)" and stop the deployment: Task 5 does not start. Report to the operator what is missing and how a baseline could be gathered (for example, a log line that marks combo attempts, then 7 days of it). This plan offers no way to enable without a baseline; changing that is a PRD change the operator makes themselves (recorded in PRD v2.1), after which this plan is re-reviewed before Task 5.

Record the method, the two patterns and the counts in the report.

Write `p5-metrics.md` → "Baseline": the window, the two counts, the rate, and the method. Commit `p5-metrics.md` and the deploy log.

---

### Task 5: Enable smart routing (live)

**Interfaces:**
- Consumes: Task 4's backups; `make setup`, `make smart-key`, `make restart`, `make smart-sync`, `make smart-probe`, `make check-server`.
- Produces: the production `omniroute` container running the derived image with the plugin active and a fresh scorecard.

If any step below fails, stop, append the failure to the deploy log, and run Task 10 for the steps already done. Do not try a second fix on the live stack without the operator.

- [ ] **Step 1: 🔒 CONFIRM — enable in `models.yml`**

Prepare the change on a copy and show only the section diff:

```bash
NEW="$(mktemp)"; chmod 600 "$NEW"
yq '.smart_routing.enabled = true' ~/.config/llm-env/models.yml > "$NEW"
diff <(yq '.smart_routing' ~/.config/llm-env/models.yml) <(yq '.smart_routing' "$NEW")
uv run llmenv.py --config "$NEW" smart-settings >/dev/null && echo "settings render OK"
```

Ask the operator whether to declare plans (`smart_routing.plans`) or bumps (`smart_routing.bumps`) now. If yes, they give the values; add them to `$NEW` with `yq`, show the section diff again, and re-run the render check. On yes: `install -m 600 "$NEW" ~/.config/llm-env/models.yml; rm -f "$NEW"`.

- [ ] **Step 2: 🔒 CONFIRM — `make setup`**

Show: it builds `localhost/llm-env-omniroute-smart` from the pinned digest and creates the empty mode-600 `omniroute-smart.env`. It changes no compose file and restarts nothing (compose is rendered by `make start`, in Step 4). Then run `make setup`. Exit 2 from the build means the anchors moved: stop and run Task 10 Step 3.

- [ ] **Step 3: 🔒 CONFIRM — management key, then the operator's own keys**

`make smart-key` creates a manage-scoped OmniRoute key and writes it into `omniroute-smart.env`. It prints JSON without the key.

Then add the operator's keys. The operator left them in the repo's gitignored `tmp/` directory: `tmp/aa-key.txt` (Artificial Analysis free API key) and `tmp/typesafe.txt` (TypeSafe Jev key). Copy them without ever printing, `cat`-ing or echoing a value (`printf` is a bash builtin, so the value never appears in a process's argv):

```bash
f=~/.config/llm-env/omniroute-smart.env
add_key() {  # add_key <VAR> <file>: append VAR=<first line of file> unless VAR is already set
  [ -s "$2" ] || { echo "missing $2"; return 1; }
  grep -q "^$1=" "$f" && { echo "$1 already set; left as is"; return 0; }
  IFS= read -r v < "$2"; printf '%s=%s\n' "$1" "${v%$'\r'}" >> "$f"; unset v; echo "$1 added"
}
add_key OMNIROUTE_PLUGIN_SMART_CATALOG_AA_API_KEY tmp/aa-key.txt
add_key TYPESAFE_API_KEY tmp/typesafe.txt
```

If a file is missing, ask the operator to add that key themselves instead (`! ${EDITOR:-nano} ~/.config/llm-env/omniroute-smart.env`), never by pasting it in chat. `TYPESAFE_API_KEY` is only used once the operator turns on TypeSafe consent in the plugin page; P5 never sets that flag.

The two `tmp/` files stay where they are (gitignored, mode 600): the operator keeps them for development and testing. Never delete or move them.

Check without printing values: `stat -c '%a' ~/.config/llm-env/omniroute-smart.env` (600) and `grep -c '^OMNIROUTE_PLUGIN_SMART_CATALOG_AA_API_KEY=.' ~/.config/llm-env/omniroute-smart.env` (1), and the same count for `TYPESAFE_API_KEY=.` when `tmp/typesafe.txt` existed.

- [ ] **Step 4: 🔒 CONFIRM — `make restart` (brief OmniRoute downtime)**

Tell the operator: clients get connection errors for the restart (measure the restart duration in this step and record it). `make start` (inside restart) re-renders `~/.config/llm-env/docker-compose.yml`: the image line becomes the derived image and `env_file` gains `omniroute-smart.env`. Backup: Task 4's `docker-compose.yml.pre-smart-routing-$STAMP`; undo: Task 10 Step 3 (restore `models.yml`, then `make setup && make restart`). Run `make restart`, then `make smart-sync`. `make start` (inside restart) activates the plugin over loopback (P2).

- [ ] **Step 5: Verify**

```bash
for i in $(seq 1 36); do podman exec omniroute test -f /app/data/smart-routing/scorecard.json && break; sleep 5; done
make smart-probe
cs_safe 2>&1 | grep -E 'smart-routing|FAIL|skipped'
```

Expected:
- `scorecard.json` exists within 3 minutes;
- `make smart-probe` passes AA, models.dev and Arena; its System One probes are skipped with a warning until Task 6 creates a `smart` combo (they go through a chat on that combo, P3 Task 10);
- every `smart-routing` row of `make check-server` is PASS except "System One reachable", which needs a `smart` combo (Task 6). "decisions match requests" (last day 0/0, today 0/0) and "fusion degraded (24 h)" (0 of 0) pass trivially until Task 6 sends traffic;
- no row that was green in Task 4 has turned red.

Append each result to the deploy log and commit it.

---

### Task 6: Create `smart-execution` and `smart-planning` (live)

**Interfaces:**
- Consumes: the combo-backup format (`pylib/omniroute_combos.py`: `{"combos": [{name, description, models, strategy, config, …}]}`), the backup from Task 4.
- Produces: the two `smart` combos, created by `make combo-restore`.

- [ ] **Step 1: Ask the operator for the members and the rule source**

Ask, in chat:
- the ordered member list for `smart-execution` and for `smart-planning`. Each member is written in the object form the existing combos and backups use (`tests/test_omniroute_combos.py`), which `compute_combo_context()` and the client context windows rely on: `{"kind": "model", "providerId": "<provider>", "model": "<provider>/<model>"}`, plus `"connectionId"` and `"label"` only for a connection-pinned member, copied from an existing combo member that pins that connection (combo-restore remaps pins by `providerId` + `label`);
- which existing combo to copy OmniRoute's own rules from (fallbacks, forbidden-substring / HTTP-200 error-text rules, `tool_filter_regex`, `system_message`, context fields): by default the coding combo they use most today;
- the **arbiter model** (`classifierArbiterModel`), always: show the effective default from `settings.json` (`podman exec omniroute node -e 'console.log(require("/app/data/smart-routing/settings.json").defaults.classifierArbiterModel)'`) and ask the operator to confirm it or name another. It is written into both combos' `config` either way, so the lane is the operator's explicit choice, never a silent default. It must not be a `smart` combo (P3 rejects it);
- optional combo-config values (`judgeModel`, `qualityFloorDelta`, `fusionTuning`).

Check every member id exists in OmniRoute's catalog, without printing the key:

```bash
# The key goes into a mode-600 curl config (scripts/check-server.sh pattern), never into argv or the deploy log.
# Read-only: use the cached agents' key; never mint one here (issue-key would create a key and write the cache).
KC=~/.config/llm-env/omniroute-api-key.json
jq -e '.key | strings | length > 0' "$KC" >/dev/null 2>&1 || { echo "no cached OmniRoute API key: stop (see below)"; false; }
AUTH="$(mktemp)"; chmod 600 "$AUTH"
jq -r '"header = \"Authorization: Bearer \(.key)\""' "$KC" > "$AUTH"   # key goes file → file, never argv/variable/output
PORT="$(yq -r '.omniroute.port' ~/.config/llm-env/models.yml)"
IDS="$(mktemp)"
code="$(curl -s -o "$IDS.raw" -w '%{http_code}' -K "$AUTH" "http://127.0.0.1:${PORT}/v1/models")"
[ "$code" = 200 ] || { echo "GET /v1/models: HTTP $code (cached key revoked or wrong): stop (see below)"; false; }
jq -r '.data[].id' "$IDS.raw" | sort > "$IDS"; rm -f "$IDS.raw"
```

If there is no cached key, `/v1/models` answers 401/403, or `key_ok` fails, stop. Minting a key is its own 🔒 CONFIRM step: show the operator that `uv run llmenv.py omniroute issue-key >/dev/null` creates a scoped client key in OmniRoute and writes `~/.config/llm-env/omniroute-api-key.json` (Task 4 backed nothing up for it because it did not exist, or `install -m 600` the revoked cache to `$BK/omniroute-api-key.json` first if it did); the undo is deleting that key in the dashboard and removing the cache file. Run it only after a yes, then repeat this step.

Keep `$AUTH` and `$IDS` for Step 4 and remove them there. If Step 4 runs in a new shell (the 🔒 confirmations can span sessions), re-create them first: `KC`, `AUTH` and `PORT` with the three lines above, and `IDS` by re-running the `/v1/models` call. In the deploy log, write curl commands with `-K <auth file>`, never an expanded header.

`grep -Fxc '<id>' "$IDS"` must print 1 for each member's `model` value, for the arbiter model and for `judgeModel` if one was given. Ask again for any unknown id; P2's hygiene step can unhide a known variant if the operator wants it.

- [ ] **Step 2: Build the seed file from the latest combo backup**

```bash
SRC="$(ls -t ~/.config/llm-env/combo-backups/combos-*.json | head -n 1)"
SEED="$(mktemp --suffix=.json)"; chmod 600 "$SEED"
jq --arg from "<source combo name>" --argjson exec '<smart-execution members JSON>' --argjson plan '<smart-planning members JSON>' \
   --argjson cfg '<combo-config JSON; always includes "classifierArbiterModel">' '
  (.combos[] | select(.name == $from)) as $s
  | {combos: [
      ($s | .name = "smart-execution" | .strategy = "smart" | .models = $exec | .config = (($s.config // {}) + $cfg) | .description = "smart routing: execution"),
      ($s | .name = "smart-planning"  | .strategy = "smart" | .models = $plan | .config = (($s.config // {}) + $cfg) | .description = "smart routing: planning and review")
    ]}' "$SRC" > "$SEED"
jq '.combos[] | {name, strategy, models, config}' "$SEED"
```

If `jq` finds no combo named `$from`, it prints nothing: stop and ask again. Then validate every member before the confirmation; this must print nothing:

```bash
jq -r '.combos[] | .name as $n | .models[]
  | select((type != "object") or .kind != "model" or (.providerId | type) != "string" or (.model | type) != "string"
      or ((.providerId + "/") as $pre | .model | startswith($pre) | not)
      or (.connectionId != null and (.label | type) != "string"))
  | "\($n): bad member \(tojson)"' "$SEED"
``` The strategy validates only its own known keys in a combo's `config` and ignores OmniRoute-native ones (P3 Task 2, `effectiveConfig`; P3 Review Focus 1), so copying the source combo's `config` is safe.

- [ ] **Step 3: 🔒 CONFIRM — create the combos**

Show the `jq` summary from Step 2. Make clear that `make combo-restore` without `OVERWRITE` creates missing names only: an existing `smart-execution` or `smart-planning` is reported as `skipped` and left untouched. On yes:

```bash
make combo-restore INPUT="$SEED"
rm -f "$SEED"
```

Expected: both `created`, and no `unresolved_connections` in the output. If any is reported, the restore cleared that member's connection pin: stop, show it to the operator, and fix the member (or the connection) before using the combo; do not proceed to Step 4 with a cleared pin. A `skipped` result is reported to the operator, who decides whether to rename or to overwrite their existing combo (a separate 🔒 step with `OVERWRITE=1` and a fresh `make combo-backup` first).

- [ ] **Step 4: 🔒 CONFIRM — verify with one request each**

Show the operator: two real chat requests (one per combo) go to the members' providers, use their quota or credit, and add one decision line each plus OmniRoute usage rows. Nothing to back up (they only append records); undo is not needed. Run them only after the operator's yes.

```bash
for c in smart-execution smart-planning; do
  curl -s -o /dev/null -w "$c %{http_code} %{time_total}s\n" -K "$AUTH" -H 'Content-Type: application/json' \
    -d "{\"model\":\"$c\",\"messages\":[{\"role\":\"user\",\"content\":\"Reply with the single word: ok\"}]}" "http://127.0.0.1:${PORT}/v1/chat/completions"
done
rm -f "$AUTH" "$IDS"
podman exec omniroute sh -c 'tail -n 2 /app/data/smart-routing/decisions.jsonl' | jq -c '{combo, lane: .outcome.lane, winner: .outcome.winner, backend: .classification.backend}'
cs_safe 2>&1 | grep -E 'smart-routing|FAIL|skipped'
make smart-probe
```

Expected: both 200; two decision lines naming the combo, a winner and backend `laya`; every `smart-routing` check-server row PASS; `make smart-probe` reaches Laya through the strategy. Append to the deploy log and commit it.

---

### Task 7: Point the clients (live)

**Interfaces:**
- Consumes: `make setup-local-llm-agents`. Through `setup/lib/install-agent-clients.sh` it writes:
  - pi: `${PI_CODING_AGENT_DIR:-$HOME/.pi/agent}/models.json` and `settings.json`;
  - OpenCode: `${XDG_CONFIG_HOME:-$HOME/.config}/opencode/` (`config.json`, `opencode.json` or `opencode.jsonc`) and `${XDG_STATE_HOME:-$HOME/.local/state}/opencode/model.json`.
- Produces: clients that list the two smart combos, with defaults switched only where the operator says.

- [ ] **Step 1: 🔒 CONFIRM — refresh the client model lists**

Back up the real client targets first, into the Task 4 backup directory:

```bash
PI_DIR="${PI_CODING_AGENT_DIR:-$HOME/.pi/agent}"; OC_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/opencode"; OC_STATE="${XDG_STATE_HOME:-$HOME/.local/state}/opencode"
install -d -m 700 "$BK/clients"; : > "$BK/clients.absent"; chmod 600 "$BK/clients.absent"
for f in "$PI_DIR/models.json" "$PI_DIR/settings.json" "$OC_DIR/config.json" "$OC_DIR/opencode.json" "$OC_DIR/opencode.jsonc" "$OC_STATE/model.json"; do
  if [ -f "$f" ]; then install -D -m 600 "$f" "$BK/clients$f"; else printf '%s\n' "$f" >> "$BK/clients.absent"; fi
done
find "$BK/clients" -type f; cat "$BK/clients.absent"   # paths only
```

`make setup-local-llm-agents` also runs `llmenv omniroute issue-key`, which reuses the cached key or, if it is missing or revoked, mints a new one and rewrites `omniroute-api-key.json`. The disclosure for this 🔒 step says so. Before running it, check `key_ok` (Task 4 Step 1). If it fails, stop and run the Task 6 Step 1 🔒 mint step first, then come back. Back up the key cache with the client files: `if [ -f ~/.config/llm-env/omniroute-api-key.json ]; then install -m 600 ~/.config/llm-env/omniroute-api-key.json "$BK/omniroute-api-key.json"; fi`. Undo for a key minted here anyway: delete it in the dashboard and restore the cache file.

Run `make setup-local-llm-agents`. Show which files changed by comparing each backed-up file with its live path (`cmp -s` per file, printing only the path); never print their content (these files can hold API keys).

Then check that both combos actually reached the clients: `setup-local-llm-agents` leaves out a combo whose computed `min_context_window` is null (no member with a known context window). Print counts only: `for c in smart-execution smart-planning; do pi=$(grep -c "\"$c\"" "$PI_DIR/models.json" 2>/dev/null); echo "$c pi=${pi:-0} opencode=$(cat "$OC_DIR"/config.json "$OC_DIR"/opencode.json "$OC_DIR"/opencode.jsonc 2>/dev/null | grep -c "\"$c\"")"; done`. Each must be ≥ 1 for every client the operator uses. If one is 0, stop before Step 2: the combo's members have no catalog context window, so fix the members (Task 6) rather than switching a default to a model the client does not list.

- [ ] **Step 2: 🔒 CONFIRM — defaults**

Ask the operator which client and which role (default model, planning/review model) should use `smart-execution` or `smart-planning`. The settings live in `$PI_DIR/settings.json` (pi) and in OpenCode's `config.json`/`opencode.json[c]` plus `$OC_STATE/model.json`. Edit only those settings, one 🔒 per client; Step 1's backups cover them. Show the changed keys only. The keys: pi's `defaultProvider` (`router-env`) and `defaultModel` (the combo name) in `settings.json`, plus the combo in `enabledModels` if that list exists; OpenCode's top-level `model` (`"<provider>/<combo>"`) in its config file. For OpenCode's `$OC_STATE/model.json`, list its keys first (`jq -r 'paths|map(tostring)|join(".")'`, names only) and change only the model value the operator points at. Change values in place; add no other keys.
- A file that `jq empty <file>` accepts (plain JSON: pi's `settings.json`, OpenCode's `model.json` and a comment-free `config.json`/`opencode.json`) is edited with `jq` into a mode-600 temp file and moved over the original.
- `opencode.jsonc`, or any file `jq empty` rejects (comments, trailing commas), is never rewritten with `jq`: show the operator the exact key and value, and they make the edit themselves (`! ${EDITOR:-nano} <path>`). Afterwards validate it with the installer's own JSONC parser, `node setup/update-opencode-config.mjs --contains-provider <path>` (exit 0 or 1 is a valid file; a thrown "invalid JSONC" means restore it from `$BK/clients<path>` and retry). Record each change in the deploy log, then commit the log.

---

### Task 8: 24-hour check (live, read-only)

Run 24 h after Task 5 Step 4. The orchestrator schedules it or hands it to the operator as a command list.

- [ ] **Step 1: Check the sync cadence and health**

```bash
podman exec omniroute sh -c 'tail -n 20 /app/data/smart-routing/sync-history.jsonl'
make smart-metrics SINCE="$(date -u -d '24 hours ago' +%FT%TZ)"
cs_safe 2>&1 | grep -E 'smart-routing|FAIL|skipped'
```

**PASS** when:
- `sync-history.jsonl` has at least `floor(24 / syncIntervalHours)` `ok: true` lines in the last 24 h;
- `freshness.worst_gap_h` ≤ interval + 0.5;
- every `smart-routing` row is PASS. The "decisions match requests" row checks the last finished day exactly and today only as `decisions ≤ requests` (requests still in flight); today's exact match is checked by the next day's run;
- `requests.checked_days` ≥ 1 and `requests.mismatched_days` is empty (with `SINCE` 24 h back, the checked day is yesterday; if this runs before 00:37 UTC, `checked_days` can be 0: re-run after 00:37 UTC rather than passing on no evidence; still 0 after 00:37 UTC means no smart traffic yesterday, so check that the clients really default to the smart combos (Task 7) before counting the row as passed);
- `unfit.source` is `decisions` (the rate covers exactly the last 24 h).

Write the result into `p5-metrics.md` → "24 h check", append a deploy-log row, and commit both.

- [ ] **Step 2: Only on a timer failure — 🔒 CONFIRM the systemd fallback**

If the plugin's own timer missed syncs (the PRD's S5 fallback), add `setup/enable-smart-sync-timer.sh` and `setup/disable-smart-sync-timer.sh`, modelled line for line on `setup/enable-token-check.sh` / `setup/disable-token-check.sh`:
- unit name `llm-env-smart-sync`;
- `ExecStart=/usr/bin/bash ${REPO_DIR}/scripts/omniroute-smart-sync.sh`;
- `OnUnitActiveSec=` the plugin's `syncIntervalHours`, read the way `scripts/smart-metrics.sh` does (`podman exec omniroute node /opt/omniroute-smart/probe/smart-status.mjs | jq -r '.sync_interval_hours // 6'`; it is a plugin setting, not a `models.yml` key), plus `OnBootSec=10min`;
- `make enable-smart-sync-timer` / `make disable-smart-sync-timer` targets and `_help_line`s;
- shell tests mirroring the token-check timer tests (`rg -n "enable-token-check" tests/test_shell.py`).

Run `uv run pytest tests/test_shell.py -k smart_sync -v && bash tools/validate.sh` (Expected: PASS) and commit the code with a code review per Task 3 Step 1, then run `make enable-smart-sync-timer` after the operator's yes, and re-run Step 1 after another 24 h.

---

### Task 9: 30-day review (live, read-only)

Run 30 days after Task 5 Step 4.

- [ ] **Step 1: Compute and compare**

```bash
make smart-metrics SINCE="<enablement day from the deploy log>"
```

Fill `p5-metrics.md` → "30-day review" with:
- **Unfit starts:** `unfit.rate` (`source: task-stats`, whole days from enablement) vs 2%, and vs 50% of the Task 4 baseline. PASS if either holds; Task 4 guarantees a baseline exists (no baseline, no Task 5).
- **Scorecard freshness:** `freshness.pct` vs 99%.
- **Silent degradations:** `requests.mismatched_days` must be empty, and `requests.checked_days` must equal the number of finished days since enablement that have a `task-stats` entry (P3 creates a day's entry with its first smart request, so a day without smart traffic has nothing to check; task-stats keeps 90 days). Report both numbers, plus the finished days with no entry, so a day with traffic but no stats would still show.
- **Spend split** and **fusion rate** (`fusion.rate`, informational). Both come from the retained decision lines: report them as "from `log_coverage.from`" and, when `log_coverage.complete` is false, say they cover fewer than the 30 days.

- [ ] **Step 2: Decide next steps with the operator**

Present the table. For any miss, propose one concrete change (for example a lower `scarcityClassCut`, a plan declaration, a different `qualityFloorDelta`) and add it to `backlog.md`. Commit `p5-metrics.md` and `backlog.md`.

- [ ] **Step 3: Upstream PRs — ask, never assume**

Show the three drafts from Task 2. For each one the operator approves, they say where (fork, branch name, or issue only). Opening it is a separate 🔒 step per PR.

---

### Task 10: Rollback (run only on failure or on the operator's request)

Each step is a 🔒 CONFIRM. Run only the steps that undo work already done, in reverse order of the deploy: clients (Task 7), then combos (Task 6), then the image (Task 5).

- [ ] **Step 1: Clients**

Restore each client file from `$BK/clients<path>` to `<path>` (`install -m 600`), for the files Task 7 backed up. For each path in `$BK/clients.absent` that exists now (the installer created it), show the path and remove it only after the operator's yes. Nothing else in those directories is touched. This comes first, so no client keeps defaulting to a `smart` combo the stock image cannot route.

- [ ] **Step 2: Smart combos**

With the stock image, `smart` is no longer a known strategy. Ask the operator whether to keep the two combos (inert, for a later re-enable) or to remove them in the dashboard. Do not delete them from the API without that answer.

- [ ] **Step 3: Back to the stock image**

```bash
install -m 600 ~/.config/llm-env/models.yml.pre-smart-routing-"$STAMP" ~/.config/llm-env/models.yml
make setup
make restart
cs_safe 2>&1 | tail -n 30
```

Expected: `podman inspect --format '{{.ImageName}}' omniroute` matches the Task 4 snapshot, and check-server matches the Task 4 baseline (the rows that were green are green). If P4 Task 7 found the database not backward-compatible (or the stock image fails to start on the volume), restore the Task 4 data, as its own 🔒 step. The tar was taken while OmniRoute ran, so it is used for the non-database files only, and the database comes from the consistent `VACUUM INTO` snapshot:

```bash
make stop
podman volume import "$VOL" "$BK/omniroute-data.tar"        # plugins/ and other files as of Task 4 (files created later stay)
# as container root: the mode-600 backup is owned by the host user (root inside); the DB goes back to the image's node user
podman run --rm --user 0:0 -v "$VOL":/data -v "$BK":/bk:ro,Z --entrypoint sh "<stock image from the Task 4 snapshot>" \
  -c 'u=$(id -u node) g=$(id -g node); install -o "$u" -g "$g" -m 600 /bk/storage.sqlite /data/storage.sqlite && rm -f /data/storage.sqlite-wal /data/storage.sqlite-shm'
make start
```

- [ ] **Step 3b: Keys added for smart routing**

The stock image does not read `omniroute-smart.env`, but the keys Task 5 added stay on disk and the management key stays valid in OmniRoute. Show the operator both, then, each after its own yes:
- the env file: if Task 4 saved `omniroute-smart.env.pre-smart-routing-$STAMP`, `install -m 600` it back over `omniroute-smart.env`; otherwise move the file aside (`mv ~/.config/llm-env/omniroute-smart.env ~/.config/llm-env/omniroute-smart.env.rolled-back-"$STAMP"`; it stays mode 600 and is deleted only on the operator's word). Never print either file;
- the management key: the operator deletes the key named `smart-catalog-management` in the dashboard's API keys page (only that key, which only P2's `make smart-key` creates). This revokes it without restoring the database, so production data written since Task 4 is kept. Skip this if Step 3 restored the Task 4 database snapshot, which predates the key.

- [ ] **Step 4: Record**

Append every rollback step to the deploy log, with the reason, and commit it.

---

## Self-Review Notes (author)

**Spec coverage (PRD Phase 4 and User Acceptance):**

| PRD item | Task |
|---|---|
| Create `smart-execution` and `smart-planning` with the operator's ids | 6 |
| Point the clients | 7 |
| Watch the success metrics for 30 days (baseline before enabling; unfit starts; freshness; silent degradations; spend split) | 1, 4, 8, 9 |
| S5 24 h timer check moved from P1; systemd fallback | 8 |
| Upstream PR (pluggable strategies), PR (plugin-scoped env), decision-provider proposal | 2, 9 |
| Backlog: metered-spend cap, historic quality ranker, unscored-model enrichment | 2 |
| User acceptance: keys in `omniroute-smart.env`, consent in the plugin page, plans and bumps in `models.yml`, `make smart-probe`, no SQLite hand edits | 5 |
| `make check-server` covers marker, strategy, plugin, scorecard age, System One | 5, 6 (rows from P1–P4) |
| Rollback to the pinned/stock image | 10 |

**Safety:** every live change is a 🔒 step with a named backup; keys are typed by the operator; consent and test-mode flags are never touched; production is only changed in Tasks 5–8 and 10.
