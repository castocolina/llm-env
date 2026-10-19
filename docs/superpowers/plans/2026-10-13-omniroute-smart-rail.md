# OmniRoute Smart Routing: Orchestrator Rail (one-shot)

Paste the block below into a fresh Claude Code session at the repo root. It is guidance for the **orchestrator**: the orchestrator does not write product code itself. It dispatches executors, checks their work, runs the gates, and stops when a gate says stop.

````text
/goal Deliver OmniRoute smart routing by executing plans P1→P5 in order, each one merged on main with its gates green, stopping and reporting on any no-go or failed gate.

You are the ORCHESTRATOR. You lead a herd of executors; you do not implement tasks yourself. Read this whole rail before the first dispatch.

## Inputs (read, in this order, before P1)
- PRD: docs/prds/omniroute-smart-routing-v2.0-prd.md (becomes v2.1 at the end of P1) and docs/prds/omniroute-smart-routing-research/.
- Plans, executed strictly in this order:
  P1 docs/superpowers/plans/2026-10-13-omniroute-smart-p1-spikes-derived-image.md
  P2 docs/superpowers/plans/2026-10-13-omniroute-smart-p2-smart-catalog-plugin.md
  P3 docs/superpowers/plans/2026-10-13-omniroute-smart-p3-smart-strategy.md
  P4 docs/superpowers/plans/2026-10-13-omniroute-smart-p4-fusion-integration.md
  P5 docs/superpowers/plans/2026-10-13-omniroute-smart-p5-deploy.md
- AGENTS.md and the user's CLAUDE.md. Each plan's "Global Constraints" apply to every task of that plan.

## What each plan must hand to the next (check before starting the next plan)
- P1 → P2–P4: VERDICT GO in docs/prds/omniroute-smart-routing-research/spike-report-p1.md; the derived image builds in make setup (exact patch counts, exit 2 on mismatch); patch sites 1–6 incl. the per-request AsyncLocalStorage store (S1 orderSawStore and observeSawStore both true) and strategy_unavailable on a failed module load; the context keys list incl. checkComboResponse (or the recorded fact that handleSingleModel applies the combo rules); S3 handleFusionChat shape and the forbidden-substring rule proof; S4 editor result; S5 env scoping + loopback; smart_routing config keys; check-server image/env rows; PRD v2.1 written; the "Impact on P2–P4" list.
- P2 → P3: smart-catalog plugin installed/activated by make start; settings.json (llmenv smart-settings) and scorecard.json (Q per task kind × domain, catalog incl. flattened effort_options, attribution); task-stats contract; make smart-sync / smart-key / smart-probe; check-server plugin + scorecard rows; p2-smoke.md all PASS; PRD v2.1 updated with P2's deviations (P2 Task 13).
- P3 → P4: smart.mjs (prelude/order/observe), createSmart/decide/errorResponse exports, decisions.jsonl + task-stats.json, the six 503 reasons, connection-pinned members, SWEEP_AGE_MS (decision-log sweep covers the longest fusion run; days settle at 00:37 UTC), bench p95 < 5 ms, p3-results.md all PASS; PRD v2.1 updated (P3 Task 12).
- P4 → P5: fusion lane (fusion / fusion_degraded / fusion_failed), OmniRoute's combo rules inherited in the fusion lane (fusion stays off with skip rules_unavailable when no check exists), judge gating and timeout, outer deadline, test-mode overrides + check-server guard, fusion 24 h row (probe/fusion-24h.mjs, live + rotated logs), docs/omniroute-smart-routing.md, p4-test-report.md every row PASS incl. upgrade + rollback rehearsal; PRD v2.1 updated (P4 Task 9).
- P5: llmenv omniroute key-status (no step mints a key without its own 🔒), deploy log, baseline (none → stop before Task 5), enabled stack, two smart combos, clients pointed, 24 h check, make smart-metrics; upstream drafts NOT opened.

## Before P1: plan adjustments come from P1, not from you
- After P1's Task 13: apply every entry of its impact list to the plan it names, P5 included (one executor, docs-only commit), and confirm PRD v2.1 exists. Any plan edit is then re-reviewed (see "Plan re-review") before that plan starts.
- If P1 ends VERDICT NO-GO (S1 anchors only on minified identifiers, no way to return a Response, store not visible in order/observe, etc.): STOP. Do not start P2. Report to the user: the failing spike, the evidence, the PRD's fallback (sidecar), and what would have to change. Wait for the user.

## Executors
- Implementation executor: pi with the user's coding model, one task per dispatch, fresh context each time:
  pi -p --no-session --model router-env/my-coding "<task prompt>"
  The task prompt contains: the plan path and the task number (the executor reads the plan itself; never paste the plan), the plan's Global Constraints by reference, "follow ponytail: smallest complete change, reuse what exists, no speculative code, comment only the why, mark limits with 'shortcut:'", "TDD: write the failing test, see it fail, implement, see it pass", "commit when the task's tests pass; never push", and the forbidden actions from "Live-change guards" below that apply to that plan, inlined verbatim with their P1–P4 / P5 scoping (the executor never sees this rail).
- Every P1–P4 task whose steps run podman (at least P1 Tasks 5, 7, 10, 11; P2 Task 12; P3 Tasks 10, 11; P4 Tasks 6, 7) uses the same executor, with the plan's throwaway names/ports and its cleanup. Afterwards YOU verify the production omniroute container's StartedAt is unchanged. P5's live podman steps follow the Live-change guards instead (its confirmed restart is expected to change StartedAt).
- If router-env/my-coding is unavailable or fails twice on the same task, stop and ask the user which model to use. Do not switch models on your own and do not implement the task yourself.
- Plan re-review (only when a plan file was edited after its approval): pi -p --no-session --model router-env/my-plan-review -xt edit,write "<read-only review prompt: plan path, PRD, earlier plans as context; end with VERDICT: APPROVED|CHANGES_REQUIRED>". Loop fix (one executor, docs-only) → re-review until APPROVED.

## Gates (run by you, after every task)
1. Codegraph: .codegraph/ exists in this repo. Before dispatching a task that edits code, run `codegraph sync` from bash (after a large rebase or new top-level directory, `codegraph index`), and check `codegraph status`. Tell executors to use `codegraph explore "<symbols>"` before grep. Use the Explore agent for broad read-only questions instead of reading many files yourself.
2. JS lint and tests: for every changed .mjs, `node --check <file>`; then `node --test 'omniroute-smart/**/*.test.mjs'` (quoted glob; Node 24 on the host rejects a bare directory).
3. Repo checks: `bash tools/validate.sh` and `bash tools/test.sh` (or the task's narrower pytest command, then the full suite at the end of the plan).
4. The task's own "Expected" lines. A mismatch is a failed task: re-dispatch the executor with the failure output (max two retries), then stop and report.
5. Ponytail review of the task diff: run /ponytail-review on the task's commit(s); Must-fix findings go back to the executor before the next task.

## End of each plan
- Code review: superpowers:requesting-code-review, dispatched as a subagent with model opus and effort high, BASE = the commit before the plan's first task, HEAD = the plan's last commit. Fix Critical and Important findings (executor), re-run the gates, and re-review if anything changed.
- Ponytail audit: /ponytail-audit on the folders the plan touched (omniroute-smart/, docker/omniroute-smart/, scripts/ and pylib/ files it changed). Must-fix findings are fixed before the next plan.
- Hand-off check: tick every item of "What each plan must hand to the next" for that plan, with evidence (file paths, test names, report rows). Missing item = the plan is not done.
- Report to the user in five lines: plan, commits, gates, review results, anything deferred. Then start the next plan.

## Live-change guards
- P1–P4 are test-only toward production: never stop, restart, exec writes into or reconfigure the production omniroute container, never edit ~/.config/llm-env/*, never run make start/stop/restart/setup/clean against production. Throwaway containers and volumes use the plans' names/ports and are removed pass or fail. Read-only inspection of production is allowed (podman inspect, podman logs, read-only SQLite).
- Review gates inside a plan are yours, not an executor's: P5 Task 3 Step 1 and P5 Task 8 Step 2's timer-code review run as the same opus-high superpowers:requesting-code-review subagent, before the first live task (or before enabling the timer). Never dispatch them to pi.
- P5 is the only plan that changes production. Every step marked 🔒 CONFIRM: you show the user the exact command(s), what changes, the backup that covers it and how to undo it, and you run it only after an explicit yes for that step. Backups (P5 Task 4) come first. Executors never run 🔒 steps on their own: they stop and return the prepared change to you.
- Secrets: never print, echo, cat or commit keys, the dashboard password, omniroute-smart.env, omniroute-api-key.json or the full models.yml (show only `yq '.smart_routing'` diffs). The user types keys into omniroute-smart.env themselves. Never set a remote System One consent flag. Never set OMNIROUTE_SMART_TEST_MODE / OMNIROUTE_PLUGIN_SMART_CATALOG_TEST_MODE on production. Honest User-Agent only.
- Never push. Never open an upstream PR or issue without the user's explicit approval for that PR. Do not touch ai-kit's .planning/milestone.lock.
- Lanes are the user's: combo members, judge, arbiter and review/fix models are chosen by the user when the plan asks; never hardcode them.

## Done
P5 Task 6 shows both smart combos answering with decision lines, make check-server is green on every smart-routing row, Task 7 clients are pointed, the deploy log and p5-metrics.md (baseline) are committed, the 24 h check (P5 Task 8) is either run and committed or handed to the user as a dated command list, and the 30-day review (P5 Task 9) is handed to the user as a dated command list. Then report and stop.
````
