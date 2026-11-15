# Alternatives to Laya under 5 GB VRAM

Last scan: 2026-11-07. Search only what is newer than this date, then update this file: the date, the table, and any
measured result.

## A switch is a client change

Any replacement must serve `/v1/systemone` and `/batch` with `noul`, `choice` and score answers, plus probabilities.
It must handle multilingual input (Spanish is routine here), run on ROCm or Vulkan beside the llama.cpp coding model,
and carry the Phoenix observer, or that observer must be re-homed. A candidate that misses one of these needs an adapter
and client changes. Name each one in the verdict.

## Candidates (claims are published numbers; nothing here is measured on this host yet)

| Candidate | Size / VRAM | Contract | Notes |
|---|---|---|---|
| Laya `typed-decisions` checkpoint (ModernBERT-large, 421M) | ~0.5 GB more as a third loaded model | same server, `system_one.models` value, applied by `make update-system-one` (drift) | card: 0.766 on LocalLLaMA/typed-decisions vs 0.727 published for Jev 1.13 (third party). Cheapest trial: it is not a switch |
| Laya 0.4.22 vs 0.4.27 | same | same | 0.4.24+ requires `vegaml` and from 0.4.25 a default vega lane handles states over 8,192 tokens (fails offline here). 0.4.22 holds nearly all the server fixes without it |
| llama.cpp `/v1/systemone` (PR #29818, merged 2026-10-02) + `ggml-org/Laya-GGUF` (Q8 428 MiB) on Vulkan | likely well under 4.26 GB (unmeasured) | no `/batch`, no multilingual GGUF, no observer | the only structural alternative. It would drop the ROCm torch stack and its busy-spin pin |
| Qwen3-4B-Instruct-2507 / Qwen3.5-4B, Phi-4-mini (llama.cpp, logprobs) | ~2.5–3.5 GB Q4 | adapter needed; p(yes)/(p(yes)+p(no)), option-letter softmax | general, but uncalibrated (fit temperature scaling). Competes with llm-server for VRAM |
| Qwen3-Reranker-0.6B/4B | ~0.6–2.5 GB | adapter; P(yes) by design | yes/no and screening only; one call per option for choice |
| ModernBERT-large-zeroshot-v2.0, GLiClass modern-large v3.0 | < 1 GB | adapter; NLI or label scores | same backbone family as Laya; score answers are crude |
| Kev-4B, lev, pplx-decider-flash-v1.2 (GGUF) | ~2.9 GB Q4 | single endpoint, no `/batch` | no measured edge near Jev in public tables |
| Ollaya (server for 29 decision families) | — | `/batch` undocumented, CUDA/CPU images only | not a model; no ROCm |
| Skywork-Reward-V2, Granite Guardian, Qwen3Guard | 0.6–4B | adapter | narrow: reward score or safety yes/no |

Searched on 2026-11-07: PyPI, GitHub (awesome-jev-live, NanoJev, open-alternative-jev, SelfJev, OpenJev, mini-jev,
openJev-verdict-2.0: experiments, unclear licenses or unverified claims), HF model search, llama.cpp PRs.

## Measure before any verdict

Use one item set for every candidate: the 32 labeled cases in `~/.claude/skills/jev-fast-decision/evals/cases.jsonl`,
plus the English and Spanish tickets from `scripts/system-one-benchmark.sh`. One item per request.

1. Baseline: run the set against live Laya on 8010. Record accuracy on non-ambiguous cases, the Brier score of the
   chosen-answer probability, p50/p95 latency and steady VRAM (`system_one_process_vram` in `tools/lib.sh`).
2. A candidate that needs the GPU while llm-server serves: free it with `make stop-llama` first; there is no room for both.
   Run the candidate in a throwaway container on port 8011 named `llm-env-scout-so`, wrapped in `timeout`, removed by a
   trap, and verified gone with `podman ps -a`. Afterwards run `make start-llama` if llm-server was serving.
3. Same metrics, same items. An LLM candidate gets a temperature fit on half the items and is scored on the other half.

Verdict format: a table of candidate × (accuracy, Brier, p95, VRAM, contract gaps), then one line that says keep and
update Laya, trial X, or switch to X with the client changes it needs. "No better option found" is a valid verdict.
