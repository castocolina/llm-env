---
name: onboard-local-model
description: Find, fit, benchmark and onboard a local agentic-coding LLM (GGUF) for this repo's llama.cpp router beside System One (Laya). Use when the user names a models.yml alias, a Hugging Face repo or URL, a .gguf file or a model name to download, add, try, swap or re-measure; says the local model got slower; asks which model fits the VRAM or beats the current one; compares quants (Q4_K_M/Q5_K_M/Q6_K); wants tok/s at depth, ctx_size, KV or n_cpu_moe tuned, the capacity card or my-coding-lite; scores it against Haiku, Luna or Composer; asks if the llama.cpp image or Laya is outdated; or wants one ladder challenge generated or verified, even as "a faster model for pi", "what LLM can I run locally" or "modelo agéntico que quepa en mi GPU". Not for OmniRoute combos except my-coding-lite, container memory limits, gpu-status, asking Laya for a judgment, cloud providers, temperature/top_p, LoRA fine-tuning or converting safetensors to GGUF.
---

# Onboard a local model

`SK=.claude/skills/onboard-local-model`; artifacts go under `tmp/`;
`<run>` is `<YYYYMMDD>-<alias>` (`-2`, `-3`... when that folder exists), `<stamp>` is `date +%Y%m%dT%H%M%S`.
Goal: a coding model for pi (>= 128K context) that fits beside a resident System One, proven by measurement, not model-card
claims. The ladder needs `git node pi`.

## Route

A model argument goes first through `bash $SK/scripts/resolve-target.sh <model>`: its `kind` or exit code picks the row.
Follow the row's flow file; load the others only at the step that names them. Rows marked onboard load
`references/scoring.md` at step 1 and `references/fit-math.md` at step 4.

| The user gave or asked | Flow file, section | Also load | Do NOT load |
|---|---|---|---|
| the enabled alias (`alias`, `enabled: true`) on a host with llm-server enabled (preflight `services.llm_server_enabled`): re-measure | `references/served-alias.md` | `references/scoring.md` at R1 | `onboard-flow.md`, `tuning.md` |
| any other models.yml alias (`alias`) | `references/onboard-flow.md`, Alias | onboard | `tuning.md`, `challenges.md` |
| a Hugging Face repo or URL (`hf_repo`, `hf_file`) | `references/onboard-flow.md`, Hugging Face (a ranked pick: Scout pick) | onboard | `tuning.md`, `challenges.md` |
| a `.gguf` path (`gguf_file`) | `references/local-gguf.md`, then `references/onboard-flow.md`, Steps | onboard | `tuning.md`, `challenges.md` |
| a bare model name (`resolve-target.sh` exits 3) | `references/selection-criteria.md`, Find the repo | then the Hugging Face row | |
| nothing: what fits, anything newer or better | `references/selection-criteria.md`, Scout and rank | a pick to onboard: the Hugging Face row (Scout pick) | `anchors.md`, `challenges.md` |
| quants of the current model (Q4_K_M vs Q5_K_M, room for Q6) | `references/tuning.md`, Standalone quant comparison | `references/scoring.md` after P0 | `onboard-flow.md`, `selection-criteria.md` |
| tune it: context, KV, tok/s at depth, capacity card, `my-coding-lite` | `references/tuning.md` | `references/scoring.md` at T1 | `onboard-flow.md`, `selection-criteria.md` |
| score a local model against Haiku, Luna or Composer | `references/anchors.md` | `references/scoring.md` | `onboard-flow.md` unless a candidate is named |
| llama.cpp image, Vulkan or ROCm, server flags | `references/runtimes.md` | | every other file |
| System One (Laya) outdated, or VRAM it could give back | `references/system-one.md` | | every other file |
| one ladder challenge (generate, verify, debug), the challenge library, a `leak_suspect` row | `references/challenges.md` | `references/scoring.md` to calibrate | every other file |

- `resolve-target.sh` exit 2 is an error (a bad path, a near-miss alias, a non-model URL, usage): report its message and stop;
  a "no, search it" after a near-miss alias takes the bare-name row.
- Every route that runs `effort-run.sh` reads `references/scoring.md` before its first lane.
- When two rows match, the model argument wins (a tune or quant request on the enabled alias takes that row) and an anchor
  named with it joins that flow's quality step.
  With no model argument, follow each matching row in table order and load every file they name.
- A read-only question never starts a gated step. Before the first gate name the number that will decide, the baseline it is
  compared with (or none) and the depth (throughput decisions only); without them, stay read-only.

## Rules for every measurement

1. **Budget.** Size against the smaller of `budget.llmenv_budget.vram_available_mib` (the repo's verdict) and
   `budget.model_vram_budget_mib` (ceiling minus the System One reserve) from `preflight.sh`, and say which one; with
   `llmenv_budget` null (no verdict: nothing enabled or its file missing) use the second and say so. A false
   `vram_feasible` there means the current model already does not fit with System One resident: lead with that. Every
   `hf-scout.sh` call takes `--budget-mib` with that budget and `--ram-mib` with the rule 1 RAM:
   `budget.ram_offload_budget_mib`, or `ram.available_mib` (say so) when that is null or 0 (llm-server disabled: a
   precondition, like a false `ram_feasible` then, not a verdict).
2. **Benchmark runs.** Run each `bench-fit.sh` and download (`fetch-gguf.sh`, `quant-headroom.sh fetch`) detached, log
   under `tmp/`, polled: the shell tool kills a foreground run at 600 s; re-run one that exits 143 or is lost, once its PID is
   gone. Every `tune-sweep.sh`, `effort-run.sh` and `latency-profile.sh` run goes to one subagent with a written brief
   (command, out dir, stop, cleanup, what to return), monitored per AGENTS.md. Each benchmark run writes to its own new
   `--out` under `tmp/` and runs alone on the GPU.
   Benchmark challenges run only on generated challenges (`gen-challenge.sh`), copied into isolated directories under `./tmp/`,
   and never in or against the owner's project.
   No other session may edit `$SK/scripts`, `$SK/challenges` or `tools/lib.sh` during a ladder run: it flags rows `leak_suspect`.
3. **Like for like.** Decode counts at the reference depth `min(131072, ctx - 512)`, with a 20 t/s floor (`tune-pick.sh
   --min-tg`); a depth-0 number is never compared with a depth number, because agents re-read their context every turn. Every
   ladder lane uses the router profile (`PI_CODING_AGENT_DIR=$HOME/.pi/agent`, `--model router-env/llama-cpp/<alias>` or the
   anchor's id) on one `seeds.json` with the same `--efforts` and `--timeout`. Profile check, before the first ladder lane:
   `~/.pi/agent/models.json` exists and lists `llama-cpp/<alias>` under `router-env`, else run
   `make setup-local-llm-agents` first.

## Gates

| Gate | Asked before | Facts the question states |
|---|---|---|
| G1 | the one download or copy | file, size, free disk, destination `~/llm-workspace/models`; any failed card, `bpw` or `ctx` gate of a model the owner named (`fit: no` stops before G1) |
| G2 | `make stop-llama` | the served model, that the local OmniRoute lane goes offline, that System One stays up |
| G3 | editing the live models.yml and serving | the diff, the rollback copy, that `make setup` pulls the floating Vulkan tag (the llama.cpp build can change) and llm-server restarts |
| G4 | the first anchor run | provider and model, runs and upper bound, that it spends quota |
| G5 | keeping the change | the comparison table; a no restores the rollback copy |

**Freeing the GPU (G2, G-T2).** When the last `preflight.sh` read has `services.llm_server_running` true (the container
check `bench-fit.sh` and `tune-sweep.sh` exit 3 on, whatever the holders show), run `make stop-llama` (llm-server only),
then `bash $SK/scripts/wait-gpu-release.sh` (it returns once no `llama-server` holder is left); on exit 1 report the
printed holders and stop. False (gateway-only, or llm-server down): skip both.
System One stays resident, as budgeted: measuring without it flatters the result. While llm-server is stopped
the healer's OmniRoute restart fails (logged) and never reloads it.

Route gates follow the same rule: G-T1 to G-T3, G-Q0 to G-Q3, G-R1, G-R2 and G6, each asked in the file that names it.
Each gate is its own question and a yes to one never covers the next; a no to G1-G3 or G5 runs the flow's own rollback step,
a no to any other gate skips that step.

**Permissions bypassed.** With permissions bypassed, a gate becomes announce the facts, then proceed; G5 and G-Q3 count as a no
(keep the baseline: a model switch is the owner's call).
Two things still stop and ask: any step that deletes user data (model files, `make prune`, `make clean`, volumes, the locally
built System One image, or overwriting the live models.yml without a backup), and any change of provider, harness or model after
a failure (AGENTS.md retry ladder).

## NEVER

- NEVER size against live free VRAM. System One idle-unloads about 3.7 GiB after 600 s and reloads on the next request;
  the "fitting" model then OOMs on the first Laya call.
- NEVER free the GPU with `make stop` or `make restart`: they take OmniRoute, System One and remote-setup down with llm-server.
  Only `make stop-llama`, after G2 or G-T2.
- NEVER prefer an MoE offload over a full-VRAM model of comparable score: it spends RAM, swap and CPU for speed that measured
  lower on this host (35B at `n_cpu_moe 28`: tg 39 t/s against 88 for a 9B), and it competes with
  Laya for the same card.
- NEVER trust a leaderboard number without its benchmark version, scaffold and effort level: cite them or omit the row.
- NEVER read `agent_status: 0` as success: a 401 or usage limit completes with `stopReason: error`, which `score.sh` counts
  as `invalid_runs`; re-run the lane per AGENTS.md.
- NEVER recommend a sub-3.5-bit quant (tool calling degrades first and agent loops compound the errors), a dense model
  that needs layer offload (CPU-bound speeds), or an uncensored/abliterated variant (weight edits cost tool reliability)
  without an own run that proves it holds up.
- NEVER flip `llm_server.enabled` or edit the live config except through `make llama-enable MODEL=<alias>` /
  `make llama-disable` or the gated steps of the flow files, each after its gate and a timestamped backup: `make setup` owns
  that file and a bad edit has no other rollback.
- NEVER build a challenge from a transcript, diff or answer of a model under test, point a benchmark at the owner's repositories,
  or reuse a seed set outside one head-to-head: a challenge a model has seen measures recall, not skill.
- NEVER run `make benchmark`: a Vulkan failure rewrites `gpu.backend` to `cpu` in the live models.yml.

## Gotchas

- `make start-llama` never recreates the container: a pulled image is served only after `make restart`.
- KV cache type is one global key (`runtime.cache_type_k/v`); only the tune route changes it.
- `bench-fit.sh` mounts the models directory with `:z` (an SELinux relabel): never point it at
  a directory the owner uses otherwise, such as `~/Downloads`.
