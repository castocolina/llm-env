---
name: system-one-scout
description: Check, update or replace System One (Laya), the local Jev-compatible /v1/systemone server on port 8010. It reads the weekly Laya upstream report, applies a version bump or system_one config change with make update-system-one, and weighs open-source alternatives under 5 GB of VRAM. Use when asked whether Laya is outdated, what a newer release brings or risks, how to update it, why an update deferred or rolled back, or whether another model could replace Laya or Jev, even as "laya desactualizado", "alternativa a jev" or "cambiar de sistema". Not for asking Laya a judgment (jev-fast-decision), the llama.cpp coding model or its image (onboard-local-model), or container memory limits.
---

# System One scout

Bump Laya in place by default; a switch changes every client of `/v1/systemone`.

| The user asks | Read |
|---|---|
| Is Laya outdated? What would an update bring or break? | `references/upgrade.md` § Check |
| Update it; a run deferred, failed or rolled back | `references/upgrade.md` § Update |
| Is there something better under 5 GB? Should we switch? | `references/alternatives.md` |

## Check

Read the weekly report `~/.local/state/llm-env/system-one-check.json` (`checked_at`), or run
`bash scripts/system-one-check.sh` (read-only). The report table has these rows, each REQUIRED:

- **gap**: releases behind, plus what the gap holds for the Python server (`serve`, `router`, `revisions`).
- **weights**: `false` means code only; `null` means unread, which is not the same as unchanged.
- **dependencies.added**: for each one, what it switches on by default, read in the target release's source.
- **torch/ROCm**: the pin stays unless `pin_watch` shows a released fix.
- **risk**, then a recommendation that names a target version: the newest release before any default-on change found
  under dependencies.added, not the latest by default.

## Update

`make update-system-one LAYA_VERSION=x.y.z` is the only way to apply a bump. At the pinned version it applies a
re-rendered `system_one` config change instead (for example a `typed-decisions` trial). Run it detached and poll:

```bash
log="tmp/update-system-one-$(date +%Y%m%dT%H%M%S).log"
setsid make update-system-one LAYA_VERSION=x.y.z >"$log" 2>&1 & echo $!   # then: ps -p <pid>; tail -5 "$log"
```

Read the result with the table in `references/upgrade.md` § Update. The Dockerfile pin it rewrites is committed only
when the user says so.

## Alternatives

Start from `references/alternatives.md`. Search only what is newer than its scan date, then update the file. A verdict
needs this host's own numbers (accuracy, Brier, p95, VRAM on the shared item set) next to Laya's baseline, plus the list
of contract gaps.

## Never

- Never build over `localhost/llm-env-system-one:rocm` by hand: the rollback source is the previous image tag `:rocm-prev`; use `make update-system-one`
  so the idle-gated update path applies and verifies the candidate image.
- Never use `make restart` for a Laya change: it also restarts OmniRoute and cuts every agent session.
- Never move the torch/ROCm pin on version numbers: `rocm7.2` works around an idle CPU busy-spin that newer wheels still have.
- Never recommend a switch on published benchmark claims alone: the Jev numbers in model cards are third-party runs on other
  task sets, and none of the candidates covers `/batch`, Spanish input and the observer.
