# Laya upgrade: check, update, roll back

## What must not break

- Contract: Jev-compatible `POST /v1/systemone` and `/batch` on loopback `system_one.port` (8010). Its clients are
  `make check-server`, `make model-interference`, the Laya span observer that writes request records to
  `~/.local/state/llm-env/laya-spans/laya-spans.jsonl` for the observe collector and Grafana, and the jev-fast-decision
  ladder. A change to the contract is a client change, not an image bump.
- Footprint (measured, `models.yml.example`): about 4.26 GB steady VRAM with `english,multilingual`. `english` alone saves
  about 200 MiB but returns HTTP 500 on non-English and short English input. Idle unload after 600 s frees about 3.7 GB,
  and a reload takes about 0.4 s. `vram_reserve_mib` is subtracted from the local coding model's budget. Judge an upgrade
  against these numbers.

## Check (read-only)

The weekly timer (`llm-env-system-one-check.timer`) saves `~/.local/state/llm-env/system-one-check.json` with
`checked_at`. `make status` prints one line from it. For a fresh report, run `bash scripts/system-one-check.sh`.

| Field | Meaning | How to read it |
|---|---|---|
| `pinned`, `latest` | Dockerfile pins against PyPI; yanked releases are not counted | the gap |
| `github.since_pin` | releases/fixes/features/perf since the pin | `null` means the notes are unread. Say so and do not describe the gap |
| `weights` | did the reviewed HF revisions (`LAYA_REVISION=reviewed`) move? | `false` = code-only update. `null` = unread, not unchanged |
| `dependencies.added` | new installed dependencies in the latest release (base + `serve`) | each one can switch something on by default. Find it in the target's `laya/serve.py` / `router.py` before you recommend the bump |
| `torch_latest_per_rocm_index`, `pin_watch` | ROCm wheels and the upstream issues the Dockerfile waits on | see the ROCm pin below |

Release notes:
`gh api "repos/<github.repo>/releases?per_page=60" --jq '.[] | select(.tag_name | test("<range>")) | .body'`. Look for the
Python server: `serve`, `router`, `revisions`, `agent`, plus env-var changes and fixes to what this repo hit (VRAM after
reload, 503 admission, multilingual routing). Most entries are for other SDKs. Count them and set them aside.

Report it as a table: pin, latest, gap, what the gap holds for this server, risk. Say plainly when the gap holds nothing relevant.

Known for 0.4.27: `vegaml` became a hard dependency, and `Router(vega="auto")` registers a lazy "vega" lane. States longer
than the encoders' 8,192 tokens, and media, route there and try to download `nandakishorm/vega-U`. With `HF_HUB_OFFLINE=1`
that request fails where 0.4.2 truncated. Short typed requests are unaffected.

## The ROCm pin is a deliberate workaround

The Dockerfile pins the `rocm7.2` index because ROCR 1.21's AsyncEventsLoop busy-spins one CPU core after the first GPU
kernel (ROCm/rocm-systems#12674, TheRock#7051). A merged PR is not a released fix. The pin moves only when a released wheel
carries rocm-systems PR #7898 and a 10-minute idle check passes: the candidate sits idle and `make resources-snapshot`
shows average CPU well under one core. `make update-system-one` never touches this pin.

## Update

`make update-system-one [LAYA_VERSION=x.y.z]` is the only way to apply a bump, and the only way to apply a `system_one`
config change without `make restart`. It builds `:rocm-cand`, prefetches the
weights, runs a CPU smoke test, waits until `/health` `idle_seconds` reaches the idle threshold, keeps the old base as
`:rocm-prev`, swaps only `system-one`, checks the answers, and rolls back by itself on failure. Run it detached with its
log under `./tmp/` and poll it. A client using Laya during the swap gets connection errors for up to about 2 minutes;
jev-fast-decision answers `needs_fallback`. To shorten the wait at the user's request, set `LLM_ENV_UPDATE_IDLE_SECONDS`.

| Result (last line, exit) | Meaning | Next |
|---|---|---|
| `unchanged`, 0 | the pin equals the target and the rendered service matches the running one | nothing |
| `drift: <fields>` … `updated system-one configuration`, 0 | a `system_one` change (for example `models: english,multilingual,typed-decisions`) re-rendered with `bash setup/render-unit.sh` was applied once idle | back up `models.yml` before editing it; a failed config swap is not rolled back, so revert, re-render and rerun |
| `updated laya A -> B`, 0 | swapped and verified; the Dockerfile pin was rewritten | `make check-server`, `make resources-snapshot`, then ask before committing the Dockerfile |
| `deferred: deadline`, 75 (make: 2) | never idle long enough | rerun later; nothing changed |
| `deferred: health` / `lock`, 75 | `/health` unreadable / another update is running | check `make status` |
| `error: ... production untouched`, 1 | build, prefetch or CPU smoke failed | read the build log tail; `:rocm-cand` is the evidence |
| `error: rolled back to laya A`, 1 | the swap failed and production is back on the old image | the failing stage is in stderr |
| `ROLLBACK FAILED`, 1 | manual recovery | `podman tag localhost/llm-env-system-one:rocm-prev localhost/llm-env-system-one:rocm`, `bash setup/render-unit.sh`, then `make restart` once no agent uses OmniRoute |
