# Onboard a named model

Loaded for any alias except the enabled one on a host with llm-server enabled, a Hugging Face repo or file, a scout pick, and
the Steps of a local GGUF (`SKILL.md` Route). Report each step as it ends.

Sections: Candidate by kind (Alias for a new entry, Alias, Hugging Face H1-H5, Scout pick); Steps (progress checklist, No-device order, steps 1-9 with gates G1-G5); Entry template; Where things live.

Ask yourself first:
- What must the candidate beat: the enabled model, measured today on the same image, or nothing (no baseline)?
- Will the number I decide on come from the reference depth, one llama.cpp build and a resident System One?
- Which step is the first to change the host, and what must its gate state?
- If I stop halfway, what stays behind: a downloaded file, a stopped llm-server, an edited models.yml?

## Candidate by kind

`S=tmp/model-scout/<run>/scratch.yml` is the scratch config (API key and dashboard password redacted). The live file stays
untouched until G3. `<models_dir>` is `~/llm-workspace/models` (`LLM_ENV_MODELS_DIR`), except where `references/local-gguf.md`
names the file's own directory.

### Alias for a new entry

Decided at step 3 (a local GGUF: L5, run order 7) and used unchanged in `$S`, the lanes and 7a:
`bash $SK/scripts/new-alias.sh --file <entry file> --size <bytes> --quant <quant>` reads the live models.yml and prints
`{alias, rule}`. `<bytes>` is `pick.bytes` (a local GGUF: `size_bytes` of `resolve-target.sh`); `<quant>` is `pick.quant` or
the `quant` of `resolve-target.sh` (leave `--quant` out when it is null).
- A1. A live entry has that `file` and `size_bytes`: its alias. One GGUF never gets a second entry. Exit 1, the same `file`
  with another size (a different file under that name; the models directory holds one file per name): report both sizes it
  prints and stop before G1.
- A2. Else a name made from the file name (its quant token dropped; the script header states the rule), or
  `<that alias>-<quant lowercase>` when a live entry's file gives the same name: the name `quant-headroom.sh add-entry` gives
  (`gemma4-q5_k_m`).
- A3. A2's name is already a live alias: `-2`, `-3`... appended. A name is never reused for a different file.

### Alias

The enabled alias on a host with llm-server enabled is a re-measure (`SKILL.md` Route), not this section. Every other alias
(an entry left enabled on a gateway-only host included):
- `bash $SK/scripts/resolve-target.sh <alias> --scratch-config $S` writes `$S` with that alias alone enabled.
- `validate-gguf` and `budget` take `--config $S`.
- `present: false`: G1 downloads the entry's `url` first.
- Its entry keeps its own settings: step 4 writes none.
- G3 adds no entry: it runs `make llama-enable MODEL=<alias>`.

### Hugging Face

H1. **Scout the one repo:** `bash $SK/scripts/hf-scout.sh --repo <repo> --json --budget-mib <rule 1 budget> --ram-mib <rule 1 RAM>`.
    - An `hf_file` URL adds `--revision <revision> --file <file>` from its JSON.
    - An `hf_repo` with a `quant` (`org/repo:Q4_K_M`, the llama.cpp `-hf` and Ollama form) adds `--quant <quant>`.
    - A scout pick adds `--file <pick file>`.
    - The row is read at one commit, `sha` (the revision resolved, `main` by default).
    - Exit 2 (the file is not in that commit, or the repo or revision is unreadable): report and stop.
H2. **Read the row.** `pick` is the named file or quant whatever the scout would choose, else the scout's choice. `sha`, `pick.bytes`
    and `pick.sha256` fill the entry (template below); never re-read `tree/main` for them.
    - The base name of `pick.file` is the `file` of the baseline (step 2): with `pick.bytes` equal to its `size_bytes` it is
      the served model, not a candidate, so follow `SKILL.md`'s enabled-alias row (the re-measure) and stop here, before H3
      and H4. With another size it is a different file under the served file's name: report both sizes and stop (the
      models directory holds one file per name).
    - `fit: "unknown"` (the tree was unreadable): retry once, else report and stop, because G1 needs the size.
    - A null `sha`: stop before G1.
    - `pick.split` true or a null `pick.sha256` (an entry takes one checksummed file): stop before G1 and report the fallback.
      Go on with it only when the owner asks after that report.
    - Fallback: H1's call without `--file` or `--quant`, with `--single-file` (`--revision` kept). When its `fit` is `vram`, its `pick` is
      the repo's largest fitting single-file quant. Any other `fit` means no single-file quant of 3.5 bpw or more fits in
      VRAM: say so and offer none.
H3. **Fit gates.**
    - `fit: "no"`: stop before G1, under bypass too. Report the fit math and the fallback (H2). Download the fallback only when
      the owner asks after that report.
    - A false card gate (H4), a `pick.bpw` under 3.5 or a `ctx` under 131072: a G1 fact. Under bypass it proceeds, because
      the owner named the model.
H4. **Evidence.** One research subagent writes the evidence card (`tmp/model-scout/<run>/evidence/1.json`). Hand it the repo
    id, its H1 row and its brief: `references/selection-criteria.md`, section 4, Research brief. Read back the card's `gates`
    for H3.
H5. **Scratch config.** Name the entry per Alias for a new entry.
    - Under A1 (a live entry other than the baseline has this `file` and size) add nothing: that alias is the candidate.
    - Build `$S` with `bash $SK/scripts/resolve-target.sh <live alias> --scratch-config $S`. `<live alias>` is the baseline;
      with no baseline (Steps 2), any live alias.
    - Add the entry (template below) to `$S` with `yq -i`, `enabled: false`. Step 4 writes its settings, and its
      `models select` switches `$S` to the new entry.
    - No alias in the live file: stop and report. Never `cp` the live file into `tmp/`.

### Scout pick

As Hugging Face, with the pick's repo and `--file <pick file>`; its evidence card from the ranking replaces the research
subagent of H4.

## Steps

Names: `<alias>` is the candidate's alias in `$S` (`candidate` for a local GGUF until 7a.2), `<entry file>` its `file` (a base
name), `<baseline>` the enabled alias of step 2, `<ref>` the reference depth of rule 3.
A local GGUF runs these Steps in the run order of `references/local-gguf.md`.

Progress checklist (copy it into your task list; (b) = only with a baseline, step 2):
- 1 preflight (llm-server enabled but stopped: serve it first)
- 2 baseline
- 3 candidate
- 4 settings, G1, select, validate, budget
- 5 (b) baseline lane and build, then G2
- 6 (b) baseline screen, then candidate screen (no `gpu.device_name`: the No-device order below)
- 7 G3 apply and serve; interference and card; ladder, (b) compare
- 8 verdict
- 9 G5, hand-back, downloaded-file report, leftover check

**No-device order** (step 1 found `gpu.device_name` empty; such a host has no baseline, so step 5 runs no lane and G2 stops
nothing): steps 1-4, then 7a.1-7a.4 (Gate G3), then `L=~/.config/llm-env/models.yml yq -i '.gpu = load(strenv(L)).gpu' $S`
(it gives `$S` the device `make setup` just found), then 6.2-6.5, then 7a.5-7a.7, then 7b, 7c, 8, 9.

1. **Preflight (read-only).** `bash $SK/scripts/preflight.sh` prints VRAM, budgets, RAM, swap, GPU holders, temperature and
   clocks.
   - Size per rule 1; it also says which RAM figure `hf-scout.sh` takes.
   - Flag any non-zero `gpu.holders`.
   - `services.llm_server_enabled` true and `services.llm_server_running` false: serve it per `references/scoring.md`,
     Serving the lane's model (it may ask Gate G-R1), then step 1 again. Remember that it was stopped: step 9 hands it
     back, and any later step that says stop goes to step 9's Hand-back first. A no to G-R1 ends the run here.
   - GPU not configured (a gateway-only `make setup` skips GPU detection and the Vulkan device):
     - `preflight.sh` exits 2 with `gpu.pci_address is empty`: no budget can be sized. Report that local inference must be set
       up first (`make llama-enable MODEL=<alias>`, then `LLM_ENV_ASSUME_YES=1 make setup`, which downloads that alias's file
       when it is missing, then `make restart`) and stop; nothing changed.
     - `yq '.gpu.device_name' ~/.config/llm-env/models.yml` prints an empty string (System One on: setup found the GPU but no
       Vulkan device): `bench-fit.sh` cannot screen until G3's `make setup`. Follow the No-device order above. Step 2 still
       finds no baseline. A no to its G3 ends the run as a declined G3 does (step 9).
   - `budget.ram_offload_budget_mib` is the llm-server memory cap from `llmenv budget` (`budget.ram_budget_source` names its
     origin).
   - Run the rule 3 profile check now, for the lane of step 5.
2. **Baseline.** The enabled entry of `~/.config/llm-env/models.yml`: a candidate must beat it, not an abstract bar.
   - Its recorded `benchmark.vulkan` numbers are depth 0 only.
   - Its capacity card's depth numbers (`uv run llmenv.py capacity show --alias <baseline>`) are reported, never compared:
     step 6 benches it beside the candidate on one image, because neither the card nor the bench JSON records the llama.cpp
     build.
   - A gateway-only host (`llm_server.enabled: false`, preflight `services.llm_server_enabled` false, even when
     `make llama-disable` left an entry enabled), or no enabled entry: no baseline. Then there is no baseline lane or bench,
     and step 8 decides without one.
3. **Candidate.** The section above for its kind; a local GGUF: `references/local-gguf.md`.
4. **Fit and configure.**
   1. Fit, per `references/fit-math.md`: weights + KV + buffers against the budget at the live KV
      type (`runtime.cache_type_k/v`, one global key: never changed here). An MoE model: solve `n_cpu_moe` against RAM too.
   2. Settings of a new entry:
      - `ctx_size`: 262144 when its KV fits, else 131072.
      - `client_max_output_tokens`: 8192; 16384 when an earlier lane of this model ended runs with `stopReason: length`.
      - `n_cpu_moe`: from the solve (MoE only).
      - `sampling`: the model card's agentic settings, or none.
      An Alias-row or A1 candidate keeps its own settings: skip this write. Otherwise write them into the candidate entry
      of `$S`:
      `yq -i '(.models[] | select(.alias == "<alias>")) |= (.ctx_size = <ctx> | .client_max_output_tokens = <max_out> | .n_cpu_moe = <n> | .sampling = <sampling>)' $S`,
      with `del(.n_cpu_moe)` for a dense model and `del(.sampling)` when the card gives none. 7a's live entry takes the same
      values.
   3. **Gate G1** fetches exactly one file: the top-ranked fit, or the file the owner named (a second candidate only if the
      user asks).
      - `bash $SK/scripts/fetch-gguf.sh --url <url> --dest <models_dir>/<entry file> --size <size_bytes>`, plus
        `--sha256 <pick.sha256>` for a Hugging Face file (an alias `url`: size only, since models.yml stores no checksum; the
        report says so). `<entry file>` is a base name, never a sub-folder path. It downloads into `<entry file>.part`, so a
        re-run resumes an interrupted download, verifies the file, then renames it.
      - The entry's file is already in `<models_dir>`: no G1; the same command only verifies it. A `<entry file>.part` there
        is a resume: G1's facts give its size.
      - Exit 1: report its message, remove the scratch files and stop, as after a declined G1 (step 9). It never touches a
        file it did not download, and it removes its `.part` only on a mismatch.
   4. Select and check on `$S`, in this order:
      - `uv run llmenv.py --config $S models select <alias>` (on the Hugging Face route it switches `$S` from the baseline to
        the new entry);
      - `... --config $S validate-gguf --models-dir <models_dir>`;
      - `... --config $S budget --models-dir <models_dir>` (it exits 1 when infeasible but still prints JSON).
      - `feasible` false: stop and report its `remedies` (what System One could give back: `SKILL.md` System One row). Never
        loosen `vram_budget` to pass.
      - `budget` errors on a wrong `--models-dir` or a missing enabled file.
      After G3, `make setup` repeats the check on the live file (the authoritative one).
5. **Baseline lane, then free the GPU.**
   1. Decide the lane set first (baseline, candidate, any anchor the owner named), all on the router profile (rule 3).
   2. With a baseline, run its lane while llm-server still serves it (a benchmark run, rule 2; upper bound per
      `references/scoring.md`, Running a lane):
      `PI_CODING_AGENT_DIR=$HOME/.pi/agent bash $SK/scripts/effort-run.sh --model router-env/llama-cpp/<baseline> --out tmp/model-scout/<run>/effort-baseline --efforts low,high --per-layer 3`.
      - It writes the `seeds.json` every later lane takes. Once G2 stops llm-server, this lane would need a swap back under
        its own gate.
      - A recorded baseline lane counts only when its `challenge_set` matches.
   3. When this lane ran, record its build: `podman inspect --format '{{.Image}}' llm-server`.
   4. **Gate G2**: free the GPU per `SKILL.md` Freeing the GPU (`bench-fit.sh` needs `llm_server_running` false).
6. **Screen throughput.** `bench-fit.sh` screens with the production presets (KV types, ubatch and flash attention included,
   which `make benchmark` leaves out), plus the depth sweep and load sampling `make benchmark` lacks; `make benchmark` also
   measures only enabled models. Each run is a benchmark run (rule 2).
   1. With a baseline, first, on the live config:
      `bash $SK/scripts/bench-fit.sh --alias <baseline> --out tmp/model-scout/<run>/bench-baseline --depths 0,32768,<ref>`.
   2. The candidate, on `$S`:
      `LLM_ENV_CONFIG=$S bash $SK/scripts/bench-fit.sh --alias <alias> --out tmp/model-scout/<run>/bench --depths 0,32768,<ref>`.
   3. Show loading, benchmarking, done, then pp/tg per depth, `gpu.model_vram_mib`, peak temperature and average clock.
   4. Reject the candidate when either holds; it then gets no ladder, and no G3 unless the No-device order ran it: report the screen numbers, restore per step 9
      and stop.
      - `model_vram_mib` exceeds the budget (the raw peak includes System One, which the budget already excludes).
      - `tg` at `<ref>` is below the floor (rule 3).
   5. A load error "unknown model architecture" goes to the `SKILL.md` Route row for the llama.cpp image (a newer llama.cpp build), not to a broken GGUF.
7. **Apply and measure with the repo's own targets.**
   - **7a. Apply and serve (Gate G3).**
     1. Rollback copy, before the first live edit:
        `cp -p ~/.config/llm-env/models.yml ~/.config/llm-env/models.yml.pre-<alias>-<stamp>`. Step 9 restores this copy,
        never the backup `make llama-enable` takes for itself.
     2. Entry, first match wins:
        - An Alias-row candidate already has one: go to 3.
        - A live entry with the same file
          (`yq '.models[] | select(.file == "<entry file>") | .alias' ~/.config/llm-env/models.yml`): use its alias (A1 checked its size),
          never a duplicate.
        - Otherwise add the candidate's entry from `$S` (its step 4 settings) under the alias decided at step 3:
          `bash $SK/scripts/live-entry.sh add --from $S --alias <alias>` (a local GGUF: `--alias candidate --as <alias>`).
          It writes `enabled: false` and backs up, validates and renames the live file. `make llama-enable` only selects an
          existing alias.
     3. `make llama-enable MODEL=<alias>` (validates the alias, sets `llm_server.enabled`, writes atomically).
     4. `LLM_ENV_ASSUME_YES=1 make setup` (unattended per AGENTS.md: the GPU prompt takes its default from the live file, so it
        answers yes after `make llama-enable`). It downloads a missing enabled file, validates the GGUFs, pulls the floating
        Vulkan tag and runs the budget check; `make check-setup` validates offline.
        - A Vulkan device selection error (no or several matching devices): hand `make setup` to the owner.
     5. No `make benchmark` (`SKILL.md` NEVER): step 6 measured this model at depth.
     6. `make start-llama`, then `make status`, `make check-server` and `make check-with-agents`. When the last step-1 read
        found the stack down (preflight `services.omniroute_running` false; never after a yes to G-R1, which started it),
        G3's facts also say that this step runs `make start`
        instead, which renders the units, migrates the config, (re)installs the OmniRoute healer and z.ai timers and brings
        up every stack container that is down.
     7. `make setup-local-llm-agents` (the router profile gets `llama-cpp/<alias>`).
   - **7b. Interference and capacity.** `make model-interference`, then `make model-capacity MODEL=<alias> DEPTH=<bench json>`
     (step 6). A failed gate goes to step 9.
   - **7c. Quality ladder.**
     1. Build: `podman inspect --format '{{.Image}}' llm-server` (the image Id, comparable with
        `podman image inspect --format '{{.Id}}' <gpu.image>`). `make start-llama` never recreates the container, so it
        equals step 5's.
        - A different Id (the container was recreated, for example by `make start`): the lanes ran on two llama.cpp builds,
          and step 8 reports (b) as unproven.
        - No baseline lane: only record it.
     2. Candidate lane (a benchmark run, rule 2):
        `PI_CODING_AGENT_DIR=$HOME/.pi/agent bash $SK/scripts/effort-run.sh --model router-env/llama-cpp/<alias> --out tmp/model-scout/<run>/effort --efforts low,high --seeds <baseline seeds.json>`.
        - No baseline lane: it is the first lane, with `--per-layer 3` instead of `--seeds`.
     3. `bash $SK/scripts/score.sh <results.jsonl>`, then `make model-capacity MODEL=<alias> SCORE=<score.json>`.
     4. `bash $SK/scripts/compare-runs.sh --correctness-only` with the baseline first (`references/scoring.md`, Head-to-head).
        - No baseline lane and no anchor: nothing to compare; skip it.
        - An anchor the owner named joins here per its route row, after **Gate G4**.
     5. Classify failures per `references/scoring.md`, Fast classification.
8. **Verdict.** Lead with the verdict and one reason. Before recommending, ask: would the owner notice this in a pi session?
   The candidate replaces the baseline only when all three hold:
   - (a) it fits `llmenv budget` with System One resident;
   - (b) it passes the replace rule (`references/scoring.md`, Score, item 6) on one seed set and one build (the step 5
     and 7c Ids are equal). Unproven: no replace; offer to re-run the baseline lane on the 7c build through its own gated swap;
   - (c) pp and tg at `<ref>` from step 6 (one image for both) stay within about 3 % (run noise) of the baseline, or the
     quality gain justifies the slowdown and the trade is stated.
   - No baseline (step 2): the verdict rests on (a), the 20 t/s floor and the ladder reached; (b) and (c) are skipped and said
     so; G5 still applies.
   Then the table: candidate vs baseline vs anchors (score, solved/total, pp/tg at depth, VRAM at ctx, RAM, temperature) and
   what was not verified.
   - `podman image inspect --format '{{.Id}}' <gpu.image>` after 7a differs from the 7c Id: the table says the next container
     recreation (`make restart`) serves a build these numbers did not measure.
9. **Keep or revert (Gate G5).**
   - Keep: `make local-combo`, then `make setup-local-llm-agents`. 7a.6's `make start-llama` skipped `my-coding-lite` (the candidate
     had no card yet), so it still names `llama-cpp/<baseline>`, which the router no longer serves. 7b's card and interference gate let it
     move to `llama-cpp/<alias>` now (a `skipped` line: report its reason). Add the entry to `models.yml.example` with `enabled: false`
     only when the owner wants a repo default. A host that was
     gateway-only before G3: the report names AGENTS.md's last step of the switch, `make restart` (run it when no agent session
     uses OmniRoute).
   - A declined G1 or G2, or a failure while neither G2 nor G3 has run (a `fetch-gguf.sh` exit 1): report it and remove the
     scratch files; nothing is rolled back, and nothing was stopped.
   - A no to G5, a declined G3, a 6.4 reject, or a failed step once G2 or G3 has run (the No-device order runs G3 without G2):
     `make stop-llama`, then restore the rollback copy (`cp -p <rollback copy> ~/.config/llm-env/models.yml`; nothing to restore before G3).
   - After that bullet (a restore or nothing to restore), when the first step-1 read found llm-server serving: `make start-llama`. A host
     that was gateway-only starts nothing; `make restart` later applies gateway-only mode to the whole stack.
   - Profile: not kept and 7a.7 ran, whatever the first step-1 read found: `make setup-local-llm-agents` (since 7a.7 the router
     profile names `llama-cpp/<alias>`; the restored file maps only its own enabled entry, or none on a gateway-only host).
   - Hand-back: the first step-1 read found llm-server enabled but stopped and it runs now (a yes to G5 included): `make stop-llama`.
   - Not kept, and G1 left a file or a `.part` in the models directory: report its path and size and ask whether to delete it (a
     user-data delete: asked under bypass too).
   - Last: `podman ps -a` and a `ps` by PID for everything this run started: nothing of yours may remain.

## Entry template

Required keys from `pylib/config.py`:

```yaml
- alias: <short-lowercase>            # per Alias for a new entry; becomes llama-cpp/<alias> in the router profile
  label: <Human name and variant>
  parameters: 35B (3B active)         # free text
  quantization: Q4_K_M                # pick.quant of the scout row (a local GGUF: L5)
  enabled: false                      # models_max is 1: enable one at a time
  file: <name>.gguf                   # the base name of pick.file
  url: https://huggingface.co/<org>/<repo>/resolve/<sha>/<pick.file>   # the scout row's sha, never "main" or a branch
  size_bytes: 21155768832             # exact: the scout row's pick.bytes
  vram_budget: 85%                    # "55%", "7.5GB" or "512MiB"; used by the budget check
  ctx_size: 262144                    # allocated at load; 131072 is the floor, 262144 the target
  client_max_output_tokens: 8192      # reasoning models need >= 8192 or high effort truncates
  n_gpu_layers: 99
  n_cpu_moe: 28                       # MoE only; omit for dense models
  sampling: {temperature: 0.6, top_p: 0.95, top_k: 20}   # optional; copy the model card's agentic/coding settings
```

Optional per-model check overrides: `check_ctx_size`, `check_timeout_seconds`. `make benchmark` writes
`benchmark.vulkan.{pp_tps,tg_tps}` into each measured entry; never hand-write it. Sha, size and checksum come from the one
`hf-scout.sh --repo` row of the Hugging Face section (`sha`, `pick.bytes`, `pick.sha256`, all read at one commit). A null `sha` or `pick.sha256` stops before G1 (H2).

## Where things live

- Capacity card: `~/.config/llm-env/capacity/<alias>.json`, written only by `make model-capacity MODEL=<alias>`; `make clean`
  keeps it. Its `quants` section is written by the quant headroom check.
