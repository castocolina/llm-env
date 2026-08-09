# Resource Limits and Single-Model Setup Design

**Status:** Approved

**Date:** 2026-08-09

## Purpose

Four small, independent operational fixes so the running stack stays within
explicit, predictable resource bounds instead of implicitly using whatever
the host happens to have free:

1. `presets.ini` gets the same repo-inspectable copy as the compose file.
2. `make setup` only ever enables one model at a time, matching the existing
   `runtime.models_max: 1` residency policy and removing the possibility of
   enabling two models whose combined VRAM never fits.
3. `llm-server`'s RAM is explicitly capped, so CPU-offloaded model layers, KV
   cache, and llama.cpp overhead can never crowd out other host processes.
4. `llm-server`'s VRAM planning respects an explicit ceiling below the
   detected hardware total, leaving deliberate headroom for the desktop
   compositor (already moved to the iGPU) and other GPU clients (e.g.
   Firefox) that still touch the dGPU.

## Non-Goals

- Detecting or moving other processes off the dGPU — that's the companion
  `gpu-contention-diagnostic-tool` design.
- Multi-model concurrent residency — out of scope; `models_max` stays at 1.

## 1. `presets.ini` inspection copy

`setup/render-unit.sh` already copies the rendered compose file to
`${COMPOSE_INSPECT_DIR}/docker-compose.yml` (see
`2026-08-06-podman-compose-omniroute-design.md`). Add the same treatment for
`presets.ini`, right after it's rendered (before the compose file, since
compose references it):

```bash
if [ -f "$presets_path" ]; then
    mkdir -p "$COMPOSE_INSPECT_DIR"
    cp "$presets_path" "${COMPOSE_INSPECT_DIR}/presets.ini"
    log_info "wrote ${COMPOSE_INSPECT_DIR}/presets.ini (inspection copy)"
fi
```

Guarded on existence for the same reason as the compose copy: harmless no-op
under test stubs that skip the real renderer.

## 2. Single-model setup

`setup/setup.sh`'s model-selection prompt currently accepts comma-separated
indices (`model_choice` regex `^[1-9][0-9]*(,[1-9][0-9]*)*$`) and calls
`llmenv models select` with however many aliases were chosen. Change the
prompt and validation to accept exactly one index:

- Regex narrows to `^[1-9][0-9]*$`.
- Prompt copy changes from "comma-separated" to a single selection.
- `default_models` (used to pre-fill the prompt) changes from joining all
  currently-enabled indices to just the first one, since after this change
  there should only ever be one.

`pylib/config.py`'s `set_enabled_models`/`enabled_models` stay as-is — they
already support N enabled models and are exercised directly by tests and by
`llmenv models select` for advanced/manual use outside the guided setup
flow. Only the interactive prompt's contract narrows.

## 3. Explicit RAM ceiling

`resources.llm_server.memory_mib` already exists in the schema and is
already translated to podman's `mem_limit` by `pylib/compose.py` — no code
changes needed there. Change the default from `0` (unlimited) to `14336`
(14 GiB) in `models.yml.example`, and set the same value in the live config
via `make setup` or a manual `yq` edit. `resources.omniroute.memory_mib`
(1024 MiB) is unchanged.

## 4. Explicit VRAM ceiling

New config field `gpu.vram_budget_ceiling`, same syntax as a model's
existing `vram_budget` (`"95%"`, `"15GB"`, or `"15488MiB"` — reuses
`budget.py`'s existing `BUDGET_RE`/`parse_vram_budget`). Default `"95%"`.

`pylib/budget.py`'s `compute_budget()` gains a `vram_budget_ceiling_mib`
parameter (resolved by the caller via `parse_vram_budget(ceiling,
vram_total_mib)` before calling in). `available_mib` becomes:

```python
ceiling = min(vram_total_mib, vram_budget_ceiling_mib)
available = ceiling - reserve - SPIKE_HEADROOM_MIB
```

i.e. the ceiling acts as an artificial cap on `vram_total_mib` before the
existing reserve/spike-headroom arithmetic runs — everything downstream
(feasibility check, remedies, resident model selection) is unchanged. If
`vram_budget_ceiling_mib` is not configured (config migration backfills a
default of `"95%"`, mirroring how `omniroute.port` gets backfilled), the
ceiling equals the full total and behavior is identical to today's.

`pylib/config.py`:
- `migrate_config`: `gpu.setdefault("vram_budget_ceiling", "95%")`.
- `validate_config`: validate with the same `VRAM_BUDGET_RE` used for model
  `vram_budget`.

Whatever script currently calls `compute_budget()` (device/budget resolution
in `llmenv.py`) resolves `gpu.vram_budget_ceiling` via `parse_vram_budget`
the same way it presumably already resolves each model's `vram_budget`, and
passes the result through.

## Testing

- `tests/test_shell.py`: assert `render-unit.sh` writes
  `${COMPOSE_INSPECT_DIR}/presets.ini`; assert `setup.sh` rejects
  comma-separated model selection and accepts a single index.
- `tests/test_config.py`: migration backfills `gpu.vram_budget_ceiling`;
  validation accepts/rejects the same shapes as `vram_budget`.
- `tests/test_compose.py` (or wherever `budget.py` is unit-tested): ceiling
  below total reduces `available_mib` accordingly; ceiling above total is a
  no-op; default `95%` behavior against the example config's 16304 MiB total
  (≈15488 MiB ceiling).
- Update `models.yml.example` and re-run the full suite + ruff.
