# Re-measure the served alias

Loaded for the enabled alias (`resolve-target.sh`: `kind alias`, `enabled: true`) on a host with llm-server enabled, when
the owner wants it measured again, for example because it "got slower". No scratch config, no candidate and no verdict: a
model cannot beat itself.

Ask yourself first:
- Is the slowdown real? Only a number at the reference depth, on the image that serves now, can show it (rule 3).
- What changed since its capacity card: the llama.cpp image, `ctx_size`, the KV type, or System One's load?
- How long is the local lane offline while I measure, and is a session using it now?
- In which state do I hand llm-server back: serving, or stopped as I found it?

Progress checklist (copy it into your task list): R1 preflight (G-R1 if the stack is down); R2 lane and build; R3 G2;
R4 screen; R5 G-R2, interference, card; R6 report, leftover check.

R1. **Preflight.** `bash $SK/scripts/preflight.sh`, then the rule 3 profile check.
    - The fit is preflight's `llmenv_budget`. A false `feasible` leads the report.
    - `services.llm_server_enabled` false (a gateway-only host): not a re-measure. The alias is a candidate with no baseline:
      `SKILL.md`'s other-alias row.
    - `services.llm_server_running` false: serve it per `references/scoring.md`, Serving the lane's model (Gate G-R1 when
      the stack is down), then R1 again; R6 hands it back. A no to G-R1 also reports the capacity card as it stands
      (`uv run llmenv.py capacity show --alias <alias>`).
R2. **Lane, while it is served** (a benchmark run, rule 2; its upper bound per `references/scoring.md`, Running a lane):
    `PI_CODING_AGENT_DIR=$HOME/.pi/agent bash $SK/scripts/effort-run.sh --model router-env/llama-cpp/<alias> --out tmp/model-scout/<run>/effort-served --efforts low,high --per-layer 3`.
    Then record the build: `podman inspect --format '{{.Image}}' llm-server`.
R3. **Gate G2:** free the GPU (`SKILL.md` Freeing the GPU) on the last R1 preflight read.
    - A no to G2: score R2's lane (R5's `score.sh` and `SCORE` card steps), report it, then R6.
R4. **Screen** on the live config (a benchmark run, rule 2; `<ref>` is the reference depth of rule 3):
    `bash $SK/scripts/bench-fit.sh --alias <alias> --out tmp/model-scout/<run>/bench-served --depths 0,32768,<ref>`.
    Show loading, benchmarking, done, then pp/tg per depth, `gpu.model_vram_mib`, peak temperature and average clock.
    - `model_vram_mib` over the budget (the raw peak includes System One, which the budget excludes), or `tg` at `<ref>` under
      the floor: report it; `make start-llama` only when llm-server served before R1; then R6.
R5. **Gate G-R2** (serve again; nothing is edited). Its facts: `make start-llama` serves `<alias>` again, so the local
    OmniRoute lane comes back; the interference gate and the card follow. Commands: `make start-llama`, then
    `make model-interference`, then `make model-capacity MODEL=<alias> DEPTH=<R4 bench json>`, `bash $SK/scripts/score.sh <R2 results.jsonl>` and
    `make model-capacity MODEL=<alias> SCORE=<score.json>`. A failed interference gate is reported, not reverted.
    - A no to G-R2: llm-server stays stopped. Score R2's lane (the `score.sh` and `SCORE` card steps above), report that the
      local lane is offline until `make start-llama`, then R6.
R6. **Report** the numbers and the capacity card, with no verdict; G5 has nothing to revert.
    - llm-server was stopped before R1 and runs now: `make stop-llama` hands it back stopped (only llm-server:
      `references/scoring.md`, Serving the lane's model).
    - Last: `podman ps -a` and a `ps` by PID for everything this run started: nothing may remain but the stack containers of
      a yes to G-R1.
