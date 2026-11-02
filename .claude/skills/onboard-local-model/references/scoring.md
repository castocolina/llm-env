# Effort ladder: run, score, compare

Loaded by every route that runs `effort-run.sh`, before its first lane, and at the first preflight of a route that finds
llm-server stopped. Profile, seeds and reference depth: `SKILL.md` rule 3; delegation: rule 2; `$SK` and paths as defined there.

## Layers and effort tiers

`effort-run.sh` runs one generated challenge per layer by default (`--per-layer K` draws K seeds per layer, `--seeds FILE` reuses
a previous set, `--layers 1,2` keeps some layers) at each `pi --thinking` tier: low, medium, high (the script default, `--efforts low,medium,high`, is the full run; every lane of this skill passes `--efforts low,high` unless the owner asks for the full run, and all lanes of one head-to-head pass the same value).
Each run is isolated in its own git repository; `agent.diff` holds what the agent changed.

| Layer | Generated challenge |
|-------|---------------------|
| L1 | single-function mutation |
| L2 | stub from a spec |
| L3 | cause away from the symptom |
| L4 | cross-module feature removal |
| L5 | repo scale with decoys |

On this host Ornith 9B spent 1,117 output tokens thinking through a trivial bug (budget 8,000), so
`client_max_output_tokens` below about 8K truncates high effort.

## Serving the lane's model

A local lane (`router-env/llama-cpp/<alias>`) needs llm-server serving that alias. A route applies this at its first
preflight and names the step that hands llm-server back.
- Preflight exits 2 with `gpu.pci_address is empty`, or says `services.llm_server_enabled` false (a gateway-only host):
  there is no served model, so the anchor comparison and the calibration have no local lane. Such a route reports that
  nothing local was measured, names AGENTS.md's switch to local inference as the owner's call (`make llama-enable
  MODEL=<alias>`, then `LLM_ENV_ASSUME_YES=1 make setup`, then `make restart`), and stops before any lane or G4.
- `services.llm_server_enabled` true and `services.llm_server_running` false: say so, run `make start-llama`, then
  `preflight.sh` again (`SKILL.md` Freeing the GPU reads `services.llm_server_running` from that read). Remember that
  it was stopped: the route's hand-back step runs `make stop-llama` last, so llm-server ends as it was found, a kept
  change included (the report says that `make start-llama` serves it).
- `make start-llama` says the stack is not running: **Gate G-R1** before `make start`, then `preflight.sh` again. Its
  facts: `make start` renders the units, migrates the config, (re)installs the OmniRoute healer and z.ai weekly-reset
  timers, and brings up every stack container that is down (OmniRoute, remote-setup, System One, Phoenix with System One,
  llm-server); System One loads its weights before it is healthy; the hand-back stops only llm-server, so the rest stays
  up. A no to G-R1: no local lane runs; the route reports what it has, says that nothing was measured, and stops.
- `make start-llama` or `make start` fails its budget check: its output is the result. Report it and stop; never loosen
  `vram_budget`.

## Running a lane

State the upper bound first: layers x per-layer x tiers x T, where T is the lane's `--timeout` (default and maximum: the runner
cap, 300 s today; a larger value exits 2). Screen with `--efforts low,high` (`--per-layer 1`, the default): 5 x 1 x 2 x T.
Head-to-head with `--per-layer 3`: 5 x 3 x 2 x T (9000 s, 2.5 h, at T = 300 s). Once the lane starts, its first stderr line
(`effort-run: <challenges> challenges x <efforts> efforts x <timeout> s upper bound`) is the exact bound: relay it. The full run (all three tiers) only on request. `effort-run.sh` proves each generated challenge before the
first agent run (it fails as shipped and passes with its reference fix) and writes `seeds.json`.

Hand the subagent this block, with `<m>` (`router-env/llama-cpp/<alias>` or the anchor's id), `<out>` (the `--out` the route step names: one new folder per lane, since `effort-run.sh` exits 2 on a folder that holds a run; a re-run takes `<out>-r2`, `-r3`...) and `<flags>` filled in.

```text
Goal: run one effort ladder against model <m> and report what it measured. The model is already served; the GPU is not yours to free.
Run exactly one command:
PI_CODING_AGENT_DIR=$HOME/.pi/agent nohup bash .claude/skills/onboard-local-model/scripts/effort-run.sh --model <m> --out <out> <flags> > <out>.log 2>&1 &
Poll the log, <out>/results.jsonl and `ps -p <pid>` at least every 5 minutes. A stalled log, an exited process
or a question is a stall: report it at once. Never edit files outside the out dir, never run make targets, never touch containers.
Return: seeds.json, results.jsonl, the score.sh JSON of results.jsonl and the elapsed time.
```

`<flags>` are the `effort-run.sh` flags: `--efforts`, `--layers`, `--per-layer` or `--seeds`, `--timeout`.

## Score (from `scripts/score.sh`)

Formula and weights: the `score.sh` header (correctness dominates, so a fast wrong answer never outranks a slow right one;
clocks and busy % are reported but not scored because they follow the workload, not the model quality).

Reading the output, by order of precedence: (1) any `invalid_runs` > 0 means the lane was down, re-run before comparing;
(2) `leak_suspect_runs` > 0 means rows were excluded because the agent may have touched what it should not: re-run that lane
on the same seeds into `<out>-r2`; (3) `ladder` is the highest layer N where every layer from the lowest one run (any
row, valid or not: the keys of `ladder_at`) through N has a `ladder_at` tier (0: the lowest layer run has none), the first
layer without one is the model's ceiling; (4) a layer counts when a strict majority of its distinct challenges is solved
(pass == total) at one effort tier, and `ladder_at` gives that lowest tier (null: no majority at any tier, or the layer ran with no valid row), so a model that
needs `high` from L2 on costs more per task than one that solves L4 at `low`; (5) `by_layer` scores say how close an unsolved
layer was, `by_operator` (`{op, solved}`) shows which mutation operators or decoys a model misses, and gaps under 10 points are
noise; (6) **the one replace rule** (the only statement of it; the onboarding verdict step names it): put the baseline
first in `compare-runs.sh --correctness-only` and read `delta_vs_first` of the candidate on the same seeds. The quality part passes only when `ladder`
delta >= 0 **and** `overall` delta > 10 (strictly more than 10 points of correctness). A lower `ladder` always fails, whatever `overall` says (it
misses a layer the baseline solves). A higher `ladder` with `overall` delta of 10 or less (for example ladder +1, overall +4) is
within noise and fails too: keep the baseline. An `overall` delta of exactly 10 or less, or `null`, is a tie or no data: keep the
baseline (re-run a `null` lane). The route's verdict step adds budget fit and pp/tg at depth. Correctness only: speed already counts at depth in the verdict
step, and the full score's thermal and CPU terms follow the room and the host on lanes run hours apart (equal correctness, a
cooler and faster candidate gains about 11 points there).

## Head-to-head

1. The first lane runs `effort-run.sh --per-layer 3` and its `seeds.json` is the set. Its upper bound, stated before dispatch: Running a lane.
2. Every other lane (candidates, baseline, quants of one model such as Q4_K_M against Q5_K_M, anchors) runs `--seeds <that file>`
   with the same `--efforts`, `--timeout` and profile, one lane at a time, each served through its own gated swap.
3. `bash $SK/scripts/compare-runs.sh --correctness-only A.jsonl B.jsonl ...` over all result files, anchors included, scores each file and prints the ladders, the deltas against the first file and warnings. Each file
   carries a `challenge_set` = {generators, tasks, efforts, timeouts}; the script exits 3 with `not a head-to-head` when the sets
   differ: no verdict. A lane with `invalid_runs` or `leak_suspect_runs` above 0 is re-run on the same seeds (into `<out>-r2`) per the retry
   ladder in AGENTS.md before comparing.
4. Decide in the route's verdict step.
5. Retire the set (never reuse it for another comparison).

Seeds:
- Each run draws fresh seeds unless it is a lane of a head-to-head, where every lane uses the same `seeds.json`; partly overlapping
  seed sets are never compared.
- Seeds whose challenges the orchestrator inspected to debug or tune a lane are burned for that lane's evaluation; a seed set sent to a
  cloud lane is retired after that comparison. Burned seeds may only reproduce an old result.

## Fast classification with jev-fast-decision

The verifier cannot judge free-text parts (did the reply name the real root cause, what kind of failure was it). For those,
ask the `jev-fast-decision` skill (follow its own SKILL.md for how to call it). Its answers are advisory: the verifier result
stands when they disagree, and a `needs_fallback` answer or no answer means you decide that item yourself from the transcript.
- Failure mode of a failed run: one choice among `no-edit`, `wrong-fix`, `partial-fix`, `tool-call-error`, `loop-or-gave-up`,
  `context-overflow`. Evidence: the last assistant text plus the `llmenv classify-transcript --client pi` excerpt.
- Root cause named: yes or no on "The reply identifies the actual defect in the changed file".
Never send raw source or secrets.
