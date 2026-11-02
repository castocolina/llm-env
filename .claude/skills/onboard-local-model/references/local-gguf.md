# Local GGUF candidate

Loaded for a `.gguf` path (`resolve-target.sh`: `kind gguf_file`), before the Steps of `references/onboard-flow.md`
(`SKILL.md` Route). The run order below is the whole run: a Step it does not list (4.3, and 4.4 apart from the checks L3 runs) is not run.

Ask yourself first:
- Is this file what its name claims? Size and quant come from the file and `resolve-target.sh`, not from the name.
- Is it a new model, another quant of one in models.yml, or a file models.yml already serves?
- What may I write? The original stays where the owner put it; only a copy in the models directory is ever served.

Run order (copy it into your task list; top to bottom, one line at a time):
1. L1 name check
2. L2 scratch config
3. Steps 1 to 4.2 (preflight, baseline, candidate, settings into `$S`)
4. L3 fit (Steps 4.4's checks, on the file's own directory)
5. L4 copy (Gate G1)
6. Step 5 (baseline lane, G2)
7. L5 live entry: decide every key now, because Gate G3 shows it as its diff
8. Step 6 (screens `candidate` on `$S`)
9. Steps 7a to 8 (L5's entry is the one 7a.2 writes)
10. Step 9 with L6

No `gpu.device_name` (step 1): items 1-7, then the No-device order of `references/onboard-flow.md` from 7a.1 on (L5's entry is
the one 7a.2 writes; step 6 still screens `candidate` on `$S`; its step 9 runs with L6).

L1. **Name check** (read-only) of the file's base name in `~/llm-workspace/models`:
    - A live entry has this `file` (`yq '.models[] | select(.file == "<base name>") | .alias' ~/.config/llm-env/models.yml`)
      and the file is the same (the models-dir file itself, or equal in size and `sha256sum`): the owner named that alias.
      Follow `SKILL.md`'s row for that alias and stop here.
    - A live entry has this `file` but its file is not in the models directory: `new-alias.sh` (A1, with this file's
      byte size) decides now, before any copy. On A1, L2 is `bash $SK/scripts/resolve-target.sh <A1 alias> --scratch-config $S`
      (that entry alone enabled, with its own `n_cpu_moe`, `ctx_size` and `vram_budget`) and `<alias>` is the A1 alias from
      then on: Steps run as `references/onboard-flow.md`, Alias (4.2 writes nothing, no L5, G3 runs `make llama-enable`),
      L3 fits on the file's own directory and L4's copy is its G1, never the entry's `url`.
    - `in_models_dir` true: it is already there. No copy and no G1 at L4.
    - Another file of that name is there: compare size and `sha256sum`. The same: no copy and no G1. Different: never copy
      over it; report both and stop.
L2. **Scratch config.** `bash $SK/scripts/resolve-target.sh <file> --scratch-config $S` (`S=tmp/model-scout/<run>/scratch.yml`)
    writes `$S` with the entry `candidate` (`url: file://<original path>`) as the only one enabled. In Steps, `<alias>` is
    `candidate` until 7a.2.
L3. **Fit, before any copy**, right after Steps 4.2 wrote the settings into `$S`:
    `uv run llmenv.py --config $S models select <alias>` (`candidate`, or the A1 alias), then `validate-gguf` and `budget` on `$S` with `--models-dir` set
    to the file's own directory. `feasible` false, or an error: report it as Steps 4.4 says and stop; nothing is copied.
L4. **G1 is a copy, not a download**, asked only after L3 found the fit feasible, and skipped when L1 found the file in place.
    - G1 copies it into `~/llm-workspace/models`, the directory 7a serves from, before any benchmark or serve step:
      `bash $SK/scripts/fetch-gguf.sh --from <original path> --dest ~/llm-workspace/models/<base name> --size <size_bytes>`
      (`size_bytes` of `resolve-target.sh`). It copies into `<base name>.part` after a free-disk check, resumes an
      interrupted copy, checks the size and renames. Exit 1: report its message and stop, as after a declined G1.
    - The owner wants that directory untouched while screening: the same command with
      `--dest tmp/model-scout/<run>/models/<base name>`. Set `LLM_ENV_MODELS_DIR` to that directory for step 6.2 only: 6.1
      benches the baseline from the default directory. G3 copies it on with the same command from that copy, and G3's
      facts name that second copy.
    - Never move or delete the original.
L5. **The live entry**, decided before G3 (run order 7) and written at 7a.2. It never keeps the alias `candidate`.
    - Before G3, write the keys below (all but `alias`) into the `candidate` entry of `$S` with `yq -i`. It is a scratch
      edit, and step 6 still screens `candidate`. 7a.2 copies it live with `--as <alias>`.
    - `alias`: per `references/onboard-flow.md`, Alias for a new entry (A1-A3).
    - `label` and `parameters`: the human name and size (the scratch entry says `unknown`).
    - `quantization`: the file's quant token, else `unknown`.
    - `url`: ask the owner which Hugging Face repo the file came from. None named, or permissions bypassed:
      `file://<original path>`. With a repo, run
      `bash $SK/scripts/hf-scout.sh --repo <repo> --file <base name> --json --budget-mib <rule 1 budget> --ram-mib <rule 1 RAM>`
      (the bare name also finds a sub-folder file). When `pick.bytes` equals the file's size and `pick.sha256` equals its
      `sha256sum`, the url is `https://huggingface.co/<repo>/resolve/<sha>/<pick.file>` (`<sha>` is the row's `sha`;
      `<pick.file>` keeps its sub-folder). Exit 2, `fit: "unknown"` or a value that differs: report it and use
      `file://<original path>`.
    - `make setup` fetches only a `file` missing from the models directory, so the url is unused while the copy exists; with
      the copy pruned (`make prune`) and the original gone, setup fails with "download failed". A `file://` url also stops
      a later quant comparison until a Hugging Face url is pinned.
    - Another quant of the enabled model (A2 names it `<alias>-<quant lowercase>`, as `quant-headroom.sh add-entry` does):
      the decision stays step 8 and G5. They apply the same three conditions as Gate G-Q3 (fit, the replace rule on one seed
      set and build, pp/tg within about 3 %), and a yes leaves the old alias in models.yml, disabled, as G-Q3's yes does.
L6. **Step 9.** Never touch the original. The downloaded-file report covers only the copy G1 made (none when L1 found it in place).
