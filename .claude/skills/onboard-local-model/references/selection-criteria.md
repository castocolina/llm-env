# Selecting and fitting a candidate

Loaded for a bare model name and the no-argument route. A research subagent reads it for its brief (section 4, Research brief).

## Find the repo (a bare model name)

`resolve-target.sh` exits 3 on a bare model name. Its exit 2 is an error: report it and stop. When the message names a near-miss alias (`did you mean alias <x>?`), the owner's answer decides: that alias goes to `SKILL.md`'s alias rows, and a "no, search it" comes here. Run
`bash $SK/scripts/hf-scout.sh --query "<name>" --json --budget-mib <rule 1 budget> --ram-mib <rule 1 RAM>` and take the first-party repo, else the quantizer the owner named. Several plausible
repos: list them and ask. None (`[]`: no GGUF repo, or every match is under 131072 context or a modded variant): report that, ask
for a repo id or URL, and stop. Then `SKILL.md`'s Hugging Face row.

## Scout and rank (no argument)

Here `<run>` is `<YYYYMMDD>-scout`; a pick onboarded later runs under its own `<run>` (`SKILL.md`), and its evidence card stays here.

Ask yourself first:
- Is a newer model better for agent loops on this card, or only newer? A leaderboard without version and effort proves neither.
- Does it fit in VRAM beside a resident System One, or does it need an offload this host measured as slow?
- Which claims on its card are unverified, and what evidence would change the ranking?

1. Preflight (`bash $SK/scripts/preflight.sh`, budget per `SKILL.md` rule 1) and the baseline: the enabled entry of `~/.config/llm-env/models.yml`
   (none when `llm_server.enabled` is false, even with an entry kept enabled, or no entry is enabled). With none, skip the
   family term and rank on fit alone. Preflight exits 2 (`gpu.pci_address is empty`: a gateway-only `make setup` skipped GPU detection): no budget can be sized; report its message, which names the switch, and stop.
2. When the session lists a `deep-research` skill, run it once for the landscape question (evidence, not measurement); else
   skip.
3. `bash $SK/scripts/hf-scout.sh --query <term> --json --budget-mib <rule 1 budget> --ram-mib <rule 1 RAM> > tmp/model-scout/<run>/scout-<term>.json`
   for `coder`, `agent`, the baseline's family and every family the research named;
   add another term only when no row has `fit: vram`.
4. `bash $SK/scripts/rank-candidates.sh --scout <f1> --scout <f2> ...` gates and orders the rows (top 6). Full-VRAM rows first:
   an offload MoE enters only as challenger to the best full-VRAM row, and only with `ram.available_mib` >= its offload RAM estimate
   + 4 GiB and no swap in use; say so when you leave one out. The offload RAM estimate of a scout row is
   `pick.mib + 2048 - <rule 1 budget>` MiB: what does not fit in VRAM once about 2 GiB is left for KV cache and buffers (the
   arithmetic of `hf-scout.sh`'s `offload` class).
5. One research subagent per shortlisted row, in parallel (the Research brief, section 4), then section 4's optional jev items.
6. `bash $SK/scripts/rank-candidates.sh --scout <f1> --scout <f2> ... --evidence tmp/model-scout/<run>/evidence` ranks the cards. A shortlisted row
   without a card is absent from both `ranked` and `excluded`: re-dispatch it or report it unresearched, never eliminated.
   Re-check every `disagreements` entry from the sources and record the decision in `notes`.
7. Report per section 3. A read-only request ends here. None fits or beats the baseline: a null result (without a baseline, say there was none to beat). At most 3 go on, and
   only when the owner asked to onboard, one at a time in rank order, each on `SKILL.md`'s Hugging Face row as a scout pick and
   all on one seed set. The first pick's step 5 baseline lane writes `seeds.json` and its step 6 benches the baseline. Later picks
   skip that lane and pass `--seeds` of it to their candidate lane. They reuse that bench only while
   `podman image inspect --format '{{.Id}}' <gpu.image>` is unchanged from its value at the first pick's step 6; otherwise their step 6 benches the baseline again. Each
   pick's step 7c runs one `compare-runs.sh --correctness-only` with the baseline first over every lane so far. With no baseline, the first pick's 7c candidate lane writes `seeds.json` and no compare has a baseline lane. A kept pick
   (G5 yes) ends the list.

## 1. Is it really agentic? Evidence ladder

Section 4's `agentic_evidence` levels rank the evidence; the generated challenge ladder measured here outranks all of it. Host
specifics: a leaderboard counts only with its harness and version (Terminal-Bench 2.x, SWE-bench Verified / Pro / Multilingual,
tau2-bench, BFCL v4, LiveCodeBench, Aider polyglot; the same model moves 2x across versions and effort, `SKILL.md` NEVER); HF
exposes `gguf.chat_template`, so grep it for `tools` to check tool handling; an unknown fine-tuner must show a gain over its own
base model's official instruct (a first-party release deserves a head start).

## 2. Red flags in repositories

- Names stacking teachers and benchmarks ("Opus5.5-GPT6Astra-Grok4.7-...-SFT-RLOO"): distillation marketing; require
  an own run before trusting. Compare against the base instruct, not against the previous fine-tune.
- `uncensored`, `abliterated`, `heretic`, `lora`: weight edits that cost tool-calling reliability. `hf-scout.sh` hides
  them unless `--include-modded`.
- A GGUF under 1.5 bits per weight at full parameter count is a draft head or adapter, not the model.
- MTP / DFlash / speculative variants need llama.cpp support for that exact arch and feature. Check the pinned image
  (`SKILL.md` Route, llama.cpp image row) before wiring one; fall back to the plain GGUF of the same weights.
- Trending lists reward downloads of image, TTS and censored-variant repos. Filter by `--query` on the task, not rank alone.
- Model-card dates lag: `lastModified` on HF is for any file change. Read the commit history when "newer" decides the pick.

## 3. Reporting format

One table per decision, lead with the verdict: candidate, base model and lab, params and active params, quant and
bits per weight, weights MiB, VRAM at ctx, RAM, measured pp/tg, effort scores, delta versus current model and versus
anchors, and the single reason it wins or loses. Name what was not verified.

## 4. Ranking without a named model

Used by Scout and rank. The script does the arithmetic; you supply the evidence.

**Gates** (`scripts/rank-candidates.sh`, from the scout rows of `hf-scout.sh --json`, checked in this order): `fit` is `vram` or
`offload`; `pick.bpw` >= 3.5; `ctx` >= 131072. A research card adds `tool_template`, `arch_supported` and `no_red_flags`: `false`
excludes the candidate and `null` lists it under `unverified`. Gates are not scores: a candidate that fails one never ranks.

**Levels** (0-3 per criterion; the card holds the integer):

| Criterion | 0 | 1 | 2 | 3 |
|-----------|---|---|---|---|
| `agentic_evidence` | none | lab card states agentic or tool-call training | community reports with reproduction detail | independent leaderboard with harness and version |
| `speed` | dense needing layer offload, or MoE offload with under 4 GiB RAM spare beyond its offload RAM estimate (Scout and rank, step 4) | MoE offload fitting RAM | full VRAM with more than 14B active | full VRAM with at most 14B active |
| `context` | under 131072 | 131072 | at least 196608 | 262144 with KV fitting |
| `lab` | unknown fine-tuner | fine-tuner beating its base instruct | known quantizer of a first-party base | first-party release |
| `recency` | older than the baseline's base | same generation | newer and under 6 months | newer and under 2 months |

**Score**: `agentic_evidence` x3 + `speed` x2 + `context` x2 + `lab` x1 + `recency` x1 (maximum 27). Ties go to the smaller `pick.mib`.

Each candidate writes one evidence card (`tmp/model-scout/<run>/evidence/<rank>.json`; a model the owner named: `1.json`): `{id, gates: {tool_template, arch_supported,
no_red_flags}, levels: {agentic_evidence, speed, context, lab, recency}, jev: {<level>: {level, needs_fallback}}, sources, notes}`.
`rank-candidates.sh --evidence DIR` rejects a malformed card (exit 2) and prints `ranked`, `excluded` and `disagreements`.

**jev items** (optional, advisory): when a card's `notes` mark a level uncertain, you may ask the `jev-fast-decision` skill
(follow its own SKILL.md for how to call it) which of the four level descriptions above fits that criterion, given the
candidate's facts (never secrets), and record the answer in the card's `jev` block. Its answers never enter the score; a
`needs_fallback` answer or no answer means you decide that level yourself. A jev level that differs from the card level by 2 or
more, or a `needs_fallback` answer, is a disagreement: re-read the sources yourself and record the decision in `notes`. Every
`disagreements` entry is re-checked before the shortlist goes on.

**Research brief** (one research subagent per candidate, dispatched in parallel): input is the candidate id and its scout row;
read this file; cite every claim with a URL and retrieval date; benchmark numbers only with version, scaffold and effort per
`SKILL.md` NEVER; no weight downloads; write only the card at `tmp/model-scout/<run>/evidence/<rank>.json` (`1.json` for a model the owner named).
