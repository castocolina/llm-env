# Context-fill ladder

The ladder measures synthetic needle recall, TTFT, prefill and decode speed as context fills. It measures fact recall, not reasoning or coding skill. A single run per fill is anecdote; use `--runs 5` for a conclusion.

Run from the repository with a new output directory under `tmp/`:

```sh
bash $SK/scripts/context-ladder.sh --via omniroute --model my-local-worker --out tmp/cl-omni-<stamp>
bash $SK/scripts/context-ladder.sh --via direct --out tmp/cl-direct-<stamp>
```

- `--via omniroute|direct` selects `router-env` in `~/.pi/agent/models.json` or the local enabled alias in `~/.config/llm-env/models.yml`.
- `--model ID` selects the OmniRoute id; `--alias A` selects a direct alias. Neither target is pinned. `--chars-per-token R` accepts 1.5–8 (default 3.4) and is the sole filler-sizing input; it does not calibrate from either target.
- `--fills`, `--needles`, `--runs R` (1–9), `--seed S` (integer, default 1), `--max-tokens`, and `--think` configure cells. Each run uses seed `S + run index`; `--chars-per-token R` (1.5–8, default 3.4) fixes filler length at `round(target tokens × R)`. For the same seed, fill, flags, R and context size, the user prompts and needle IDs are identical across targets. Token counts are target-specific measurements, not controlled or guaranteed identical.
- A cold TTFT needs a prompt the server has not seen: choose a `--seed` different from earlier runs. Prompts are identical across targets, so the second target may hit the cache if its server ignores `cache_prompt: false`.
- `--ctx N` supplies context size when configuration or direct `/props` does not. `--timeout` is per-request; `--max-seconds` is the run budget; `--min-tg` sets the decode floor. Estimates use `--pp-tps` and `--tg-tps`.
- `--dry-run` prints planned cells and estimates without credentials or requests. `--idle-check` reports two host-read cgroup CPU interval percentages (not Podman lifetime averages), without affecting exit status. `--out DIR` is required, new, and under `tmp/`.
- `results.jsonl` records measured rows, including `prompt_sha256_prefix` (first 12 hex characters of SHA-256 of the user prompt) for comparing same-seed cells without saving prompts. `answer_kind` is `exact`, `format_drift`, `partial`, `wrong_values`, `no_ids`, or `empty`; `answer_values_in_filler` counts answer numbers also present in the synthetic filler. Answers are not stored unless `--keep-answers` is set.
- Summary reports per-fill medians and strict/core recall as `k/R`; the table shows fill, prompt tokens, context %, TTFT seconds, decode tokens/s, and strict/core `k/R`. `ceiling_fill` is the last fill meeting `--min-tg` and strict recall at every run. Direct disables prompt caching; OmniRoute TTFT includes gateway overhead.
- Targets above `n_ctx - max_tokens - 256` are clamped deterministically; cells that cannot fit are skipped and never sent. Each completed row records measured prompt tokens and percent size error; `size_off` flags errors over 15%. Summary reports the median observed filler-characters per prompt-token. `--calibrate` works only with direct and measures one 100 KB sample; it does not run the ladder. Two consecutive timeouts or budget expiry stop cleanly.
- Large cold prefills are expensive: 90% of a 128K context takes roughly 3–4 minutes at 581 tokens/s. Synthetic needles measure recall, not reasoning.
