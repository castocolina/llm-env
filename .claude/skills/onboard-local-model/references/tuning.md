# Capacity and tuning procedure

Loaded for tuning, the capacity card, `my-coding-lite` and quant comparisons.

Ask yourself first:
- Which limit binds first for this model: VRAM at the target context, RAM for offload, or decode speed at depth?
- Is the gain larger than run noise (about 3 %)? A smaller one is no reason to touch the live config.
- Would a faster row that keeps less context make pi worse over a long session?

## When

The owner asks for the best context and speed for the current model, a capacity card, or `my-coding-lite`. Needs
`llm_server.enabled: true` (already set before step T1, or enabled in T2) and the model served (the sweep itself runs with llm-server stopped).

## Procedure

Every step is one command; the gates and the bypass rule are those of `SKILL.md` Gates.

- T1. `bash $SK/scripts/preflight.sh` (budget, RAM, swap; flag a non-zero `swap_used_mib` or a false `llmenv_budget.vram_feasible`), then the rule 3 profile check (`SKILL.md`).
  Enabled but stopped: serve it per `references/scoring.md`, Serving the lane's model; the Hand-back bullet of Quant headroom
  stops it again.
  Preflight exits 2 with `gpu.pci_address is empty`, or `gpu.device_name` is empty in the live config (a gateway-only
  `make setup` skipped them): with `llm_server.enabled` true, run `LLM_ENV_ASSUME_YES=1 make setup` (gate G-T1), else T2
  first; then T1 again.
- T2. Only if `llm_server.enabled` is false (gate G-T1; its facts name the download below): `make llama-enable MODEL=<alias>`,
  then `LLM_ENV_ASSUME_YES=1 make setup` (AGENTS.md: it detects the GPU and the Vulkan device a gateway-only setup skipped,
  and downloads the entry's file when it is missing). T6's `make start-llama` serves the model without a restart; the report names
  AGENTS.md's last step of the switch, `make restart`, for the whole stack (run it when no agent session uses OmniRoute).
- T3. Gate G-T2: free the GPU (`SKILL.md`, Freeing the GPU).
- T4. Dispatch one subagent with the "Subagent brief" below: a benchmark run (`SKILL.md` rule 2).
- T5. Read the `tune-pick` JSON the sweep ended with, then `bash $SK/scripts/tune-apply.sh --alias <a> --results tmp/model-tune/<stamp>/results.jsonl --id <pick>`
  (gate G-T3: live config edit, backup first). Note the printed `vram_available_mib`.
- T6. `make start-llama`, then `make check-server`. No `make benchmark` (`SKILL.md` NEVER): the verify run already measured this configuration with llm-server stopped.
- T7. `make model-interference`. On failure restore the backup, or apply the next ranked id (at most 3 tries in total).
- T8. `make model-capacity MODEL=<a> DEPTH=tmp/model-tune/<stamp>/runs/<pick>/bench-<a>.json`. Then the ladder:
  `PI_CODING_AGENT_DIR=$HOME/.pi/agent bash $SK/scripts/effort-run.sh --model router-env/llama-cpp/<a> --out tmp/model-scout/<stamp>/effort --efforts low,high --per-layer 3`,
  then `bash $SK/scripts/score.sh <results.jsonl>` into `<score.json>`, then `make model-capacity MODEL=<a> SCORE=<score.json>`.
  The run writes `tmp/model-scout/<stamp>/effort/seeds.json`, the seed set every quant lane reuses (Standalone quant comparison,
  `<seeds>`). The ladder is a benchmark run (`SKILL.md` rule 2): state its upper bound first (`references/scoring.md`,
  Running a lane).
- T9. `make local-combo`, then `make setup-local-llm-agents`. Confirm with
  `jq '.providers["router-env"].models[] | select(.id == "my-coding-lite")' ~/.pi/agent/models.json`. Finish with `podman ps -a` and a
  `ps` by PID for everything this run started.

The full program continues with Quant headroom.

## Interference gate

`make model-interference` loads the served model for 120 s and fails on: a non-200 from Laya (System One), Laya p95 under
load above 500 ms (`--max-p95-ms`) or p50 above 150 ms (`--max-p50-ms`), any `memory.events` high/max/oom/oom_kill change, a
container counter reset, a service that stopped, no completed load request, no Laya latency sample under load, or
System One running but never probed. Laya (System One) serves advisory, non-interactive typed judgments, so only a service-level limit is a failure: a measured p95 rise from ~18 ms to ~160 ms
under load is the expected cost of sharing the GPU, not interference. The old relative rule (p95 > +50 % and > +10 ms) is
reported as the `regression_warning` field and never fails the gate. The card's `usable` stays null until a passing gate is
recorded (basis `interference not measured`).

## Latency profile and model report

Run with llm-server serving the tuned alias; it only sends requests (no stop, no restart). One subagent (`SKILL.md` rule 2)
runs it with the Subagent brief below, adapted: its goal and its one command are
`bash $SK/scripts/latency-profile.sh --alias <a> --out tmp/latency-profile/<stamp>-<a>`, llm-server stays serving `<a>`, and
it returns the out dir, the profile JSON, the skipped sizes and the elapsed time (the largest prompt takes minutes; default
bound `--max-seconds 3600`, runs that would pass it are skipped and listed). Sizes default to 1024, 8192, 32768, 131072 and `max` (`ctx_size - 512 - 64`), 512 output tokens each, then one
full-window run (`ctx_size - cap - 64` prompt plus `client_max_output_tokens` generated). Every request has a unique prefix,
`cache_prompt` off and `ignore_eos` on. Peaks are 1 s samples, so a request shorter than a few seconds has few of them
(`gpu.samples`). Host CPU is system-wide: keep other work off the host while it runs. Then:
`make model-capacity MODEL=<a> PROFILE=<json>` and `make model-report MODEL=<a>` (prints one page and saves
`~/.config/llm-env/capacity/<a>.report.md`). The report prints `not measured` for any value the card does not hold.

## Subagent brief

Hand the subagent this block, with `<a>`, `<stamp>` and `<axes>` filled in.

```text
Goal: run one tune-sweep for alias <a> and report what it measured. The GPU is free (llm-server is stopped); System One stays up.
Run exactly one command:
nohup bash .claude/skills/onboard-local-model/scripts/tune-sweep.sh --alias <a> --out tmp/model-tune/<stamp> <axes> > tmp/model-tune/<stamp>.log 2>&1 &
Poll the log, tmp/model-tune/<stamp>/results.jsonl and `ps -p <pid>` at least every 5 minutes. A stalled log, an exited process
or a question is a stall: report it at once. Never edit models.yml, never run make targets, never touch other containers.
If a skill script must be patched, work in `git worktree add .claude/worktrees/<name>`, never in the main checkout.
Return: the out dir, the tune-pick JSON, the failed runs (id and error) and the elapsed time.
```

The sweep prints its upper time bound (runs x timeout) first; relay it before dispatching. `<axes>`: omit them for the default
grid (`tune-sweep.sh --help`) unless the owner names axes; else `--ctx`, `--kv`, `--ubatch`, `--fa`, `--ngl`, `--moe`, `--threads`,
`--timeout`.

## Reading results

- Context comes first: an agent that loses its history is worse than a slower one, so the rank is ctx descending, then decode
  speed at the reference depth, then prefill at that depth.
- The decode number is `tg_at_ref`, at the reference depth `min(131072, ctx - 512)`, never depth 0. A row counts only when it was
  also measured at the full depth `ctx - 512`, its `model_vram_mib` fits `vram_available_mib`, and `tg_at_ref` is at least 20 t/s.
- Differences under about 3 % are run noise; do not prefer a row for them.
- Threads are swept only with `--moe` and are reported, not applied: `models.yml` has no key for them.
- Flash attention off with a quantized KV cache may be refused by llama.cpp; that run is recorded as failed, not assumed.

## Rollback

`make stop-llama`, then `cp -p <backup> ~/.config/llm-env/models.yml`, then `make start-llama`. The backup path is the one
`tune-apply.sh` printed (`models.yml.pre-tune-<stamp>.bak` next to the live file). When T7 applied a second ranked id,
restore the FIRST backup (the earliest `models.yml.pre-tune-*.bak`): `tune-apply.sh` takes a fresh backup of an already
tuned file each time, so the later backups already hold the first applied row.
After restoring a backup, run `make model-interference` again before `make local-combo` acts: the card keeps one interference
section, so the restored config has no current gate result until then.
A declined G-T3 has nothing to restore: only `make start-llama`.

## Quant headroom

Ends the tuning program, after T8 (T9 only builds `my-coding-lite`). A check never starts a download or edits `models.yml`; `check` writes
`<LLM_ENV_SCOUT_OUT>/model-tune/quant-<stamp>-<pid>/candidates.json` (default root `<repo>/tmp`) and the card's `quants` section. Candidates are the next quants of the same
Hugging Face repo at the entry's commit (default `Q5_K_M,Q6_K`; `--quants` overrides); sizes and sha256 come from the repo
tree. A `resolve/main/` entry url is resolved only when its `size_bytes` equals the tree size, else pin the url first (P0). An entry url with a sub-folder (`resolve/<sha>/<folder>/<file>`) works: each candidate is looked up at that path with the quant token replaced (in any case; `UD-` tokens such as `UD-Q5_K_XL` too) and downloads under its base name.

### Standalone quant comparison

For "Q4_K_M vs Q5_K_M" or "is there room for Q5 or Q6" without a tuning run in this session. `<a>` is the enabled, served alias
(enabled but stopped, as a `preflight.sh` read before P0 shows (`services.llm_server_running` false): after P0, serve it per
`references/scoring.md`, Serving the lane's model; the Hand-back bullet stops it again). Names: `<seeds>` is the `seeds.json` of `<a>`'s ladder lane (T8's, or `QR/base/effort/seeds.json` when P3 ran) and `<a-results>`
that lane's `results.jsonl`; `QR` is `tmp/model-tune/quant-<stamp>`, one stamp chosen before P2 or Q1; the verify depths are
`0,32768,min(131072, ctx - 512),ctx - 512` (ctx 131072 gives `0,32768,130560`). Gates: G-Q0 before a url pin, G-T2, G-Q1 before `fetch`, G-Q2 before
`add-entry`, G-Q3 for the verdict.

Ask yourself first:
- Does the higher quant fit with System One resident at the same context? If not, the comparison ends before any download.
- Can one seed set show the quality difference I expect, and is it worth the slower decode?
- Which alias serves at the end of every branch? Q11 always brings `<a>` back.

Progress checklist (copy it into your task list): P0 url; P1 interference; P2 depth; Q1 check; with a fitting candidate: P3
seeds, then per candidate Q2 fetch, Q3 entry, Q4-Q6 serve, Q7 gate, Q8 ladder, Q9-Q10 record, Q11 restore; G-Q3; hand-back; leftover check
(`podman ps -a`, `ps` by PID).

Before Q1, in this order, each only when missing (a tuning run in this session leaves P1 and P2):
- P0. The entry url: read it from the config (`$LLM_ENV_CONFIG`, default `~/.config/llm-env/models.yml`) with
  `yq '.models[] | select(.alias == "<a>") | .url'`. `check` reads a `https://huggingface.co/<org>/<repo>/resolve/<rev>/<path>`
  url whose path ends in the entry's `file`; any other (a `file://` url from a local GGUF) makes it exit 1 with "entry url is not a Hugging Face resolve url". Then ask the
  owner for the repo; under bypass, or with no repo named, report that the comparison needs a Hugging Face entry and stop. With a
  repo: `bash $SK/scripts/hf-scout.sh --repo <repo> --file <entry file> --json --budget-mib <rule 1 budget> --ram-mib <rule 1 RAM>`
  (figures from `bash $SK/scripts/preflight.sh`; the bare name also finds the file in a sub-folder). `fit: "unknown"` (the tree was unreadable, `pick` null): retry once, else report a read failure and stop.
  Exit 2 (no file of that name in the repo, for example a renamed local copy): report it and stop. When `pick.bytes` equals the entry's
  `size_bytes` and `pick.sha256` equals the `sha256sum` of that file in the models directory (`$LLM_ENV_MODELS_DIR`, default `~/llm-workspace/models`):
  Gate G-Q0 (live config edit), then
  `bash $SK/scripts/live-entry.sh set-url --alias <a> --url https://huggingface.co/<repo>/resolve/<sha>/<pick.file>`
  (it backs up, validates and renames; `<sha>` is the row's `sha`; `<pick.file>` keeps its sub-folder). A declined G-Q0 ends the comparison with a
  report of the unpinned url and the checked values (Q1 is not run). Differing values: report them and stop. A `resolve/main/` url
  runs the same `hf-scout.sh --repo <repo> --file <entry file>` call (repo taken from the url): a `pick.bytes` different from
  `size_bytes` means the local file is from an older commit, so report both values and stop; when they match, pin it as above (G-Q0).
- P1. A passed interference gate: `uv run llmenv.py capacity show --alias <a>` has `interference` passed and not in
  `stale_sections`. Else `make model-interference` while `<a>` is served; a failed gate ends the comparison (report it, then the Hand-back bullet).
- P2. A current depth section (`capacity show` says no capacity card, or `depth` is null or in `stale_sections`): Gate G-T2,
  then `bash $SK/scripts/bench-fit.sh --alias <a> --out QR/base --depths <verify depths>` (a benchmark run) and
  `make model-capacity MODEL=<a> DEPTH=<bench json>`. llm-server stays stopped for Q1.
After Q1, only when a candidate has `fits: true`, before the first Q2:
- P3. `<seeds>`: T8's lane of this session, or an earlier lane of `<a>` whose seeds were never in a finished comparison (no
  `compare-runs.sh` and no Q8 lane used them; on seeds of another generator `effort-run.sh` exits 2: then run this step). When in
  doubt, run this step. Else `make start-llama`, then, while `<a>` is served, T8's ladder command with `--out QR/base/effort`, `score.sh` and
  `make model-capacity MODEL=<a> SCORE=<score.json>`. When P3 ran, Q4 frees the GPU again under its own Gate G-T2.
T2 runs only when `llm_server.enabled` is false; T4-T6 (sweep, apply and restart) only when the owner also asks for tuning.

Every lane is a head-to-head lane (`references/scoring.md`, Head-to-head) on `<seeds>` (the same `--efforts`, `--timeout` and layers,
none omitted), one at a time, its upper bound stated first. Order: P0-P2; Q1; P3 when a candidate fits; then Q2-Q11 for each candidate with
`fits: true`, in ascending size (Q11 always restores `<a>`); then G-Q3 once over every measured candidate.

- Q1. `bash $SK/scripts/quant-headroom.sh check --alias <a> --vram-available-mib <N> --quants <list>`,
  with `<list>` the quants the owner named (comma-separated, `UD-Q5_K_XL` too; leave `--quants` out for the default `Q5_K_M,Q6_K`) and N the `vram_available_mib` of the T5 row applied in this session; without one, llm-server must be stopped (P2 did it; else
  Gate G-T2) and the flag omitted, and the script reads `available_mib` from `llmenv budget`
  (while llm-server runs it refuses without the flag). The output has one line
  per candidate and a verdict. The per-candidate fit is the repo budget gate (`llmenv budget` on a scratch config for that candidate; a
  candidate file that is not downloaded is stood in for by a sparse file with the current GGUF header and the tree's size), with the
  formula prediction and the measured model VRAM printed beside it; the formula never overrides the gate. `fits: null` means unknown (RAM not measured, `n_cpu_moe` offload, sharded or absent
  file), never a guess. No candidate with `fits: true` (the verdict `no headroom for <quants> at ctx <N>, free MiB <X>`, or every
  `fits` false or null): the verdict is in the card; `make start-llama` when llm-server was serving at P0 (or T1), then the Hand-back bullet.
- For each candidate with `fits: true`, in ascending size:
  - Q2. Gate G-Q1 (download: announce the file, its size and the free disk), then `bash $SK/scripts/quant-headroom.sh fetch --alias <a> --quant <Q>`.
    It runs `fetch-gguf.sh` (size and the tree's sha256; an interrupted fetch resumes in `<file>.part`); a mismatch removes only that `.part`, and a different file already under that name stops the fetch untouched (exit 1): report it, and ask the owner to move or delete it (a partial file an older fetch left there counts too).
    A declined G-Q1 or a `fetch` exit 1: report it and skip this candidate (no Q3-Q11 for it); the next candidate asks its own G-Q1.
  - Q3. Gate G-Q2 (live config edit: announce the backup path), then `bash $SK/scripts/quant-headroom.sh add-entry --alias <a> --quant <Q>`.
    It adds the disabled alias `<b>` = `<a>-<q lowercase>` after a backup and refuses an existing alias.
    A declined G-Q2: report the file Q2 fetched (its path and size) and ask whether to delete it (a user-data delete: asked under bypass too); then skip this candidate as a declined G-Q1 does.
  - Q4. Free the GPU (`SKILL.md`, Freeing the GPU; already free after P2 or Q1's G-T2, unless P3 served `<a>` again), then `make llama-enable MODEL=<b>`.
  - Q5. The same flags and verify depths as `<a>`'s depth section: `bash $SK/scripts/bench-fit.sh --alias <b> --out QR/<Q> --depths <verify depths>`.
  - Q6. `make start-llama`, then `make check-server`, then `make setup-local-llm-agents`.
  - Q7. `make model-interference`. On a failure run Q9 with `DEPTH` only, then Q10 and Q11, and stop the larger quants; G-Q3 still runs over the measured candidates (the card then
    shows `interference_passed: false` for that quant).
  - Q8. The ladder with the `--efforts` of `<a>`'s lane:
    `PI_CODING_AGENT_DIR=$HOME/.pi/agent bash $SK/scripts/effort-run.sh --model router-env/llama-cpp/<b> --out QR/<Q>/effort --efforts <same> --seeds <seeds>`,
    then `bash $SK/scripts/score.sh <results.jsonl>` into `<score.json>`, then `bash $SK/scripts/compare-runs.sh --correctness-only <a-results> <results.jsonl>`
    (`references/scoring.md`, Head-to-head).
  - Q9. `make model-capacity MODEL=<b> DEPTH=<bench json> SCORE=<score json>`.
  - Q10. `bash $SK/scripts/quant-headroom.sh record --alias <a> --candidate <b>`. The line shows tg and pp at the reference depth,
    VRAM, ladder and score deltas against `<a>`.
  - Q11. Restore: `make stop-llama`, `make llama-enable MODEL=<a>`, `make start-llama`, `make setup-local-llm-agents`.
- A failed Q4-Q6 (no bench JSON, over budget, server not healthy): run Q11 and stop the larger quants; Rollback is only for a T5 apply.
- Every candidate skipped at Q2 or Q3 (none measured): no G-Q3; `make start-llama` when llm-server was serving at P0 (or T1), then the
  Hand-back bullet.
- Q5 and Q8 are benchmark runs (`SKILL.md` rule 2), one at a time.
- `make start-llama` creates or updates `my-coding-lite` for the served alias once that alias has a current card and a passed
  interference gate (`scripts/start.sh`), so Q11 and a G-Q3 yes need no `make local-combo`. While a candidate without a card is served
  (Q6-Q9), `my-coding-lite` still names `<a>` and fails; Q11 brings it back.
- Gate G-Q3 (switch the default quant), once after the loop. A candidate `<b>` qualifies only when its Q7 gate passed, the Q8
  `compare-runs.sh` (`<a>` first) passes the replace rule (`references/scoring.md`, Score, item 6), and its Q10 tg and pp at the
  reference depth stay within about 3 % of `<a>` or the slowdown is stated as the trade. None qualifies: keep `<a>` and name the part
  each one failed. Several: the highest `overall` of its Q8 compare, ties to the smaller file. Ask with the comparison table (quant, bpw, ctx, pp and tg
  at the reference depth, VRAM, ladder and score deltas).
- G-Q3 yes: `make stop-llama`, `make llama-enable MODEL=<b>` (it backs up models.yml first), `make start-llama`, `make check-server`,
  `make setup-local-llm-agents`. `<a>` stays in models.yml, disabled,
  as the rollback: all of Q11 brings it back.
- Hand-back, the last step of the tuning program and of the standalone comparison (after whichever step ends it: G-Q3, a
  P0 stop or a declined G-Q0, a failed P1 gate, a declined G-T2, Q1's no-fit verdict, or every candidate skipped at Q2 or Q3): when T1 or P0 found llm-server
  stopped, `make stop-llama`, so it ends as found; the report says that `make start-llama` serves it.
