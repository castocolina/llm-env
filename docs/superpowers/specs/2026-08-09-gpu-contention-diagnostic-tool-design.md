# GPU Contention Diagnostic Tool Design

**Status:** Approved

**Date:** 2026-08-09

## Purpose

`llm-server` plans its VRAM budget against `gpu.vram_total_mib` and the new
explicit ceiling (see `2026-08-09-resource-limits-and-single-model-setup-design.md`),
but that planning has no visibility into what else is actually using the
dGPU right now (e.g. Firefox, a game). The desktop compositor was already
moved to the iGPU (`KWIN_DRM_DEVICES`), but other apps default to whichever
GPU the system picks and can still eat into the same ceiling. `make
gpu-status` gives a live, read-only picture of that, plus a single-shot,
explicitly-confirmed way to push the worst offenders onto the iGPU.

## Non-Goals

- Not a continuous daemon — a one-shot diagnostic run on demand.
- Not integrated into `make start`'s health gate — informational only,
  never blocks or auto-runs.
- Doesn't touch already-running processes' actual GPU context (impossible
  without restarting them) — remediation only affects future launches of the
  same app, via its desktop launcher.

## Components

### `pylib/detect.py`: per-process VRAM usage

New function, generalizing the existing `compositor_render_node()` (which
only checks compositor process names) into an unfiltered scan:

```python
def processes_on_render_node(render_node: str, proc_root: Path = Path("/proc")) -> list[dict]:
    """Return every process with an open fd on render_node, with its VRAM use."""
```

For each PID with a matching fd (same `/proc/<pid>/fd/*` resolution
`compositor_render_node()` already does), read
`/proc/<pid>/fdinfo/<fd>` and parse the `drm-memory-vram:` line (kernel
DRM fdinfo standard, populated by the amdgpu driver since ~5.19 — the same
mechanism `nvtop`/`amdgpu_top` use). Sum VRAM bytes across all matching fds
per PID. Return `{pid, comm, exe, vram_mib}`, missing/unparseable fdinfo
treated as `0` (best-effort, not fatal — a process can hold the fd without
the kernel exposing memory accounting for it).

### `tools/gpu-status.sh` + `make gpu-status`

1. Read live GPU state via `llmenv detect` (existing `pylib/detect.py`
   plumbing) to get the configured dGPU's `pci_address`, `render_node`,
   `vram_total_mib`, `vram_used_mib`.
2. Resolve the configured ceiling (`gpu.vram_budget_ceiling`) the same way
   `budget.py` does, and print: total / currently used (system-wide,
   independent of whether `llm-server` is even running) / llm-env's ceiling
   / headroom remaining before `llm-server` would even start planning.
3. Call `processes_on_render_node()` for the dGPU's render node, excluding
   PIDs whose `comm` matches llm-env's own stack (`llama-server`, `conmon`,
   `podman`, and this script's own process) so the tool never flags itself.
4. Sort remaining processes by `vram_mib` descending, take the top 3.
   Print a table: PID, command, VRAM MiB.
5. If the top-3 list is non-empty, a **single** confirmation covering all of
   them: `Move these N processes to the iGPU? [y/N]` (not one prompt per
   process, per your simplification).
6. On yes, for each of the (up to) 3 processes: locate a matching
   `.desktop` launcher (search `~/.local/share/applications` then
   `/usr/share/applications` for one whose `Exec=` basename matches the
   process's `exe` basename). If found, write/overwrite a user override at
   `~/.local/share/applications/<same-filename>` — the standard XDG
   override mechanism, never touches the system file, reversible by
   deleting the override — with `env DRI_PRIME=pci-<igpu-pci-with-underscores>`
   prepended to the `Exec=` line, preserving any `%f`/`%u`/etc. field codes.
   If no matching `.desktop` is found, print that PID/command as
   "no launcher found, skipped" and move on — never fails the whole run.
7. Print a one-line summary of what changed (which apps got an override,
   which were skipped).

### Default GPU preference for new/unmapped apps

Separate from step 5/6 above, a second, independent confirmation at the end
of the same run (per your approval of point 4 as-is):
`Set the iGPU as the default GPU for new apps? [y/N]`. On yes, writes
`~/.config/environment.d/60-llm-env-igpu-default.conf` containing
`DRI_PRIME=pci-<igpu-pci-with-underscores>`. Idempotent — overwrites the
same file on repeat runs rather than accumulating. Documented in the
script's own output as best-effort: only affects apps that respect Mesa's
DRI_PRIME convention, only takes effect on the next login/session (not
already-running processes), and never affects `llm-server` itself (which
selects its GPU explicitly by PCI address inside the container, independent
of this variable).

## Error Handling

- No dGPU configured / `llmenv detect` fails: print the error and exit
  nonzero, same convention as other scripts via `tools/lib.sh`'s `die`.
- `processes_on_render_node()` finding zero non-excluded processes: print
  "no other processes using the dGPU" and exit 0, no prompts.
- Desktop-file search/parsing errors for one process: skip that process
  (see step 6), never abort the whole run over one bad launcher.

## Testing

- `pylib/detect.py`: unit tests for `processes_on_render_node()` using
  injectable `proc_root` fixtures (same pattern as existing
  `compositor_render_node()` tests) — fake `/proc/<pid>/fd/*` symlinks and
  `/proc/<pid>/fdinfo/*` content, including a malformed/missing fdinfo case.
- `tests/test_shell.py`: `gpu-status.sh` with stubbed `llmenv detect` output
  and a fixture `/proc`, asserting: the summary line's numbers, the top-3
  ordering, the exclude-list filtering out the stack's own processes, the
  single combined y/N prompt (not per-process), the `.desktop` override
  content and location, and the `environment.d` file content.
- Manual live verification (per your established convention) after the
  automated suite passes: run `make gpu-status` for real, confirm the
  reported total/used against `radeontop` or `amdgpu_top` if available,
  and confirm a real override file loads correctly (`gio launch` or
  re-running the app) before/after accepting the y/N prompt.
