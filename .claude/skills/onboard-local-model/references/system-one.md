# System One (Laya) upgrade review

Loaded when the user asks whether Laya, its torch/ROCm stack or an alternative would use less VRAM, run faster or fix
something, or when VRAM is the limit for a candidate. Everything System One saves
goes straight into the model budget, so this is part of fitting a model.

## What must not break

- Contract: Jev-compatible `POST /v1/systemone` (and `/batch`) on loopback `system_one.port` (8010): System One's own API, which its clients depend on (among them `make check-server`, `make model-interference` and the Phoenix observer). A replacement that changes the contract is a client change,
  not an image bump: say so and stop unless the user asks for it.
- Footprint facts (`models.yml.example`, measured by the repo): steady about 4.26 GB VRAM with `english,multilingual`;
  `english` alone saves about 200 MiB but returns HTTP 500 on non-English and short English input; idle unload after
  600 s frees about 3.7 GB and a reload takes about 0.4 s; `vram_reserve_mib` 5120 is subtracted from the model budget.
  The value of any upgrade is measured against those numbers, never against a feeling of "newer".

## Check (read-only)

`bash $SK/scripts/system-one-check.sh` prints the pins in `docker/system-one/Dockerfile` (Laya, torch, ROCm index) against
PyPI's newest Laya (with how many releases behind), the newest torch wheel per ROCm index, and the model repo's last
change. `github.since_pin` counts the releases, fixes, features and perf changes since the pin (release notes live on GitHub; the model
card and PyPI lag them, so do not stop there). Read the bodies for the interesting ones:
`gh api "repos/<github.repo>/releases?per_page=60" --jq ".[] | select(.tag_name | test(\"<range>\")) | .body"`. Look for: fixes to anything the repo hit (VRAM growth after reload, HTTP 503 admission, multilingual
routing), new typed primitives in the `/v1/systemone` contract, checkpoint size changes, and API or env-var changes.

Report as a table: pin, latest, gap, what the gap contains, risk. State plainly when the gap holds nothing relevant.

## The ROCm pin is a deliberate workaround

The Dockerfile pins the `rocm7.2` index because ROCR 1.21's AsyncEventsLoop busy-spins one CPU core after the first GPU
kernel (ROCm/rocm-systems#12674, TheRock#7051). Its comment says to move back to the newest index once a release
carries rocm-systems PR #7898 and to re-run the idle check. `pin_watch` reports the live state of every PR and issue the
Dockerfile comment names: a merged PR is not a released fix, so an open issue naming the released wheel means the pin stays. So a newer `torch_latest_per_rocm_index` entry is only an
upgrade if it carries that fix: read the PR and the wheel's ROCm version, then prove it with the idle check
(candidate container idle for 10 minutes, `make resources-snapshot` average CPU well under one core). Never move the pin
on version numbers alone.

## Trial without touching production

1. Disk first: a ROCm torch image is large (the repo's own note says several minutes to build).
2. Build under a different tag, never the configured one: `podman build --build-arg GPU_ARCH=$(uv run llmenv.py gfx-arch
   --render-node <node>) --build-arg LAYA_VERSION=<new> [--build-arg TORCH_VERSION=... --build-arg TORCH_INDEX=...]
   -t localhost/llm-env-system-one:rocm-cand docker/system-one`. `scripts/system-one-build.sh` always writes the configured tag.
3. Free the GPU first (G2, `SKILL.md`): the model budget leaves no room for a second Laya (about 4.26 GB steady). Then run
   it on port 8011 with the same environment as the running container (`podman inspect system-one` for the `LAYA_*`
   variables), same device passthrough, name it `llm-env-scout-so`.
4. Compare with the running one: steady and peak VRAM (whole-card `mem_info_vram_used` delta), first-request load time,
   latency and answers for the same typed requests on both ports, the idle CPU check above. Then remove the trial container
   and run `make start-llama` if llm-server served before step 3.
5. **Gate G6: replace.** Update the pins in the Dockerfile, `make system-one-build`, recreate only `system-one` while no agent
   uses it (it has no dependants; OmniRoute does not proxy it). Rollback: retag the previous image id (record it first with
   `podman image inspect --format '{{.Id}}' <image>`) and recreate.

## Alternatives

Only when the check shows Laya cannot meet a stated need. Search Hugging Face and PyPI for non-autoregressive or typed
decision engines, and for each state: does it speak the `/v1/systemone` contract, steady VRAM, ROCm gfx1201 support,
license, maintainer track record, and a measured comparison on the repo's own typed requests. "No better option found"
is a valid result; say what was searched.
