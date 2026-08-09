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

## 3. Explicit RAM ceiling (percentage of total host RAM)

Today `pylib/resources.py::compute_resource_limits()` hands `llm-server`
*all* remaining host RAM after fixed floors
(`host_memory_total_mib - HOST_MEMORY_FLOOR_MIB - OMNIROUTE_MEMORY_FIXED_MIB`)
— there is no cap. `setup.sh` Step 8/8 calls this live on every `make
setup` run (via `llmenv resources`) and writes whatever it returns into
`resources.llm_server.memory_mib`. Changing only the example file's default
would get silently overwritten the next time setup runs, so the cap has to
live in `compute_resource_limits()` itself.

New config field `resources.llm_server.memory_ceiling_pct` (plain number,
percent of `host_memory_total_mib`, not of what's free — RAM isn't
contended the way VRAM is here). Default `46` (≈14.4 GiB on your 32 GiB
host, closest whole percent to the 14 GiB you asked for; scales
proportionally on other hosts instead of hard-coding a byte count that
means something different on different machines).

The computed ceiling is itself floored, at two tiers: a real, user-facing
config default of `resources.llm_server.memory_ceiling_floor_mib` (10 GiB /
10240 MiB, backfilled by `migrate_config`) and a lower, code-level fallback
of 6 GiB / 6144 MiB used only when a config genuinely bypassed migration
(a hand-crafted config, or a stubbed test fixture). Without this, a
valid-but-tiny `memory_ceiling_pct` could round the ceiling down to
something `pylib/compose.py` treats as "no limit" (it omits `mem_limit`
entirely when `memory_mib` is falsy/`0`) — the opposite of the intended
guarantee.

`compute_resource_limits()` gains `memory_ceiling_pct` and
`memory_ceiling_floor_mib` parameters:

```python
def compute_resource_limits(
    host_cpu_count: int,
    host_memory_total_mib: int,
    memory_ceiling_pct: float = 100,
    memory_ceiling_floor_mib: int = 6144,
) -> dict[str, Any]:
    ...
    memory_ceiling_mib = max(
        memory_ceiling_floor_mib,
        round(host_memory_total_mib * memory_ceiling_pct / 100),
    )
    llm_server_memory_mib = min(
        host_memory_total_mib - memory_floor_mib, memory_ceiling_mib
    )
```

`llm_server.memory_mib` in the returned dict uses `llm_server_memory_mib`.
The `ResourceError` floor check is unchanged (it's about whether the fixed
floors fit at all, independent of the ceiling). Whatever caller currently
invokes `compute_resource_limits()` (the `llmenv resources` CLI command)
resolves both `resources.llm_server.memory_ceiling_pct` and
`resources.llm_server.memory_ceiling_floor_mib` from config and passes them
through — same shape as how `budget` already reads config before calling
`compute_budget()`.

`pylib/config.py`:
- `migrate_config`: `llm_server_resources.setdefault("memory_ceiling_pct", 46)`
  and `llm_server_resources.setdefault("memory_ceiling_floor_mib", 10240)`
  alongside the existing `cpus`/`memory_mib` defaults.
- `validate_config`: `memory_ceiling_pct` must be a finite number in `(0, 100]`;
  `memory_ceiling_floor_mib` must be a positive integer.

## 4. Explicit VRAM ceiling (percentage of VRAM free at setup time)

Unlike RAM, VRAM contention is real and time-varying (desktop compositor +
other GPU clients), so the ceiling is a percentage of what's *actually free
right now*, computed once during `make setup` (Step 2, "Detecting GPUs" —
where `vram_total`/`vram_used` for the chosen GPU are already known) and
stored as a resolved absolute value, the same way `gpu.pci_address` and
`gpu.vram_total_mib` themselves are captured at setup time rather than
re-detected on every start.

Three new config fields:
- `gpu.vram_budget_ceiling_pct` — plain number, the configurable knob.
  Default `95`.
- `gpu.vram_budget_ceiling_mib` — absolute MiB, computed and written by
  `setup.sh` alongside its existing `pci_address`/`vram_total_mib`/
  `device_name` write in Step 2:
  `max(vram_budget_ceiling_floor_mib, round((vram_total - vram_used) * vram_budget_ceiling_pct / 100))`.
  Recomputed (overwritten) every time `make setup` runs, using whatever is
  free on the GPU at that moment.
- `gpu.vram_budget_ceiling_floor_mib` — the floor applied above, at the
  same two tiers as the RAM ceiling: a real, user-facing config default of
  10 GiB / 10240 MiB (backfilled by `migrate_config`), read by `setup.sh`
  via `yq -r '.gpu.vram_budget_ceiling_floor_mib // 6144' "$CONFIG_PATH"` —
  the `// 6144` is a code-level fallback for a config that bypassed
  migration, not the normal-operation value.

`pylib/budget.py`'s `compute_budget()` gains a `vram_budget_ceiling_mib`
parameter (the already-resolved, already-floored absolute value read
straight from config — no parsing or flooring needed here, unlike a
model's `vram_budget` which stays a `%`/`GB`/`MiB` string because it's
evaluated fresh against `vram_total_mib` every time). `available_mib`
becomes:

```python
ceiling = min(vram_total_mib, vram_budget_ceiling_mib)
available = ceiling - reserve - SPIKE_HEADROOM_MIB
```

i.e. the ceiling acts as an artificial cap on `vram_total_mib` before the
existing reserve/spike-headroom arithmetic runs — everything downstream
(feasibility check, remedies, resident model selection) is unchanged.

`pylib/config.py`:
- `migrate_config`: `gpu.setdefault("vram_budget_ceiling_pct", 95)`,
  `gpu.setdefault("vram_budget_ceiling_mib", gpu.get("vram_total_mib", 0))`
  (backfill to the full total — i.e. no additional cap — for configs
  created before this field existed and not yet re-run through `make
  setup`; the real, tighter value is written the next time setup runs), and
  `gpu.setdefault("vram_budget_ceiling_floor_mib", 10240)`.
- `validate_config`: `vram_budget_ceiling_pct` must be a finite number in
  `(0, 100]`; `vram_budget_ceiling_mib` must be a non-negative integer;
  `vram_budget_ceiling_floor_mib` must be a positive integer.

Whatever caller resolves `gpu.vram_budget_ceiling_mib` from config and
passes it straight into `compute_budget()` — note that a configured `0`
(the `models.yml.example` placeholder before `make setup` has ever run) is
a documented "no cap" sentinel and must be translated to `None` before
reaching `compute_budget()`, not passed through literally (`min(total, 0)`
would otherwise collapse the budget to zero/negative).

## Testing

- `tests/test_shell.py`: assert `render-unit.sh` writes
  `${COMPOSE_INSPECT_DIR}/presets.ini`; assert `setup.sh` rejects
  comma-separated model selection and accepts a single index; assert
  `setup.sh` Step 2 writes `gpu.vram_budget_ceiling_mib` computed from
  `(vram_total - vram_used) * vram_budget_ceiling_pct / 100` against a
  stubbed `llmenv detect`.
- `tests/test_config.py`: migration backfills `resources.llm_server.memory_ceiling_pct`
  (46), `gpu.vram_budget_ceiling_pct` (95), and `gpu.vram_budget_ceiling_mib`
  (falls back to `vram_total_mib`); validation accepts/rejects each new
  field's range.
- `pylib/resources.py` unit tests: `compute_resource_limits()` with a small
  `memory_ceiling_pct` caps `llm_server.memory_mib` below
  `host_memory_total_mib - memory_floor_mib`; a `memory_ceiling_pct` of
  `100` (or high enough) reproduces today's uncapped behavior; the
  `ResourceError` floor check is unaffected by the ceiling.
- `pylib/budget.py` unit tests: `vram_budget_ceiling_mib` below
  `vram_total_mib` reduces `available_mib` accordingly; a ceiling at or
  above `vram_total_mib` is a no-op.
- Update `models.yml.example` (new fields at their defaults, `resources.llm_server.memory_mib`
  left as the computed convention — set by `make setup`, not hand-edited)
  and re-run the full suite + ruff.
