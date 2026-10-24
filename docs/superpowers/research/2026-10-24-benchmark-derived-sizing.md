# Benchmark-derived container sizing (2026-10-24)

Rule agreed with the owner: **reservation (`memory_min_mib`) = resident memory the container keeps
under use + 20 %; ceiling (`memory_max_mib`) = highest observed resident memory or `memory.peak` + 20 %**,
with the most common (modal) level preferred over the mean. The OmniRoute pressure guard sheds at
`memory.current / memory.max >= 0.85`, so a ceiling of peak + 20 % (peak / ceiling = 0.83) is also the
smallest ceiling that does not shed at its own peak. Swap is reported but not added: `memory.max` does not count it.

Tool: `uv run llmenv.py resource-benchmark omniroute|attach|report` (`pylib/resource_benchmark.py`).
Raw runs stay in `tmp/resource-benchmark/` (ephemeral).

## OmniRoute (throwaway container, same image and environment, fake upstream, 8 KiB prompts)

Phases: cold start from container creation, first requests (1 rps), ramp 2/5/10/20/40 rps (30 s each),
60 s cooldown. Resident memory: boot 311-377 MiB, first requests ~467, 5 rps ~475, 10 rps ~518,
20 rps ~558, 40 rps ~609-635, cooldown 624 (kept after load). CPU peak 0.72 cores (5 s window).
Benchmark-only derivation: min 768 MiB, max 768 MiB. Limits of the run: the gateway served ~3 successful
requests/s against a 1 s upstream and timed out the rest (client 30 s), so 5+ rps phases are saturation,
and a 64 KiB prompt serialised at ~0.35 s per request. A synthetic fake upstream understates real
tool-heavy traffic (earlier 1 GiB cap tripped the guard near 700 MB), so the ceiling takes the live
lifetime peak instead.

Live container, 5 days uptime: `memory.current` 907 MiB + 480 MiB swap, lifetime `memory.peak` 1,651 MiB,
`swap.peak` 602 MiB, against ~624 MiB for a fresh, loaded process: ~0.3-0.75 GiB of retention/leak that
only a restart clears. (An earlier draft said the healer restarts "never for memory"; that was wrong:
`heal-omniroute-admission.sh` also recycles an idle OmniRoute whose cgroup memory stays above a threshold,
see "Healer threshold" below.)

## System One (Laya) and Phoenix, live, `make system-one-benchmark` (cold 20 s idle, 10-2000 requests, 2x admission)

Laya resident: 2,573 MiB idle (model unloaded) -> 3,863-3,869 MiB from the first request on (kept
after load); VRAM 712 -> 4,478 MiB (Laya process 4,236 MiB); CPU 1.07-1.55 cores; 54-131 req/s;
`memory.peak` 4,608 MiB = its cap (166 `memory.events max` since the container started), median swap 1,075 MiB.
The peak is clipped by the cap, so it is a lower bound and nothing can be derived from it. Phoenix:
73 MiB idle, ~144 MiB during/after the run, lifetime peak 765 MiB, median swap 451 MiB, CPU peak 0.3 cores.
Phoenix again 2 h after a restart with real Laya traffic: 483 MiB resident, `memory.peak` 804 MiB (804 / 960
= 0.84 of the then cap).
remote-setup: 17 MiB, lifetime peak 39 MiB.

## Recommended policy

| Service | memory_min_mib | memory_max_mib | cpus_min / max | Basis |
|---|---:|---:|---|---|
| omniroute | 768 (was 1024) | 2048 (was 3072) | 1 / 2 (was 4) | fresh loaded 640 MiB * 1.2; live peak 1,651 * 1.2 = 1,981 |
| remote_setup | 32 | 64 | 0.25 / 0.5 | peak 39 MiB * 1.2 = 47 -> keep 64 |
| phoenix | 192 (was 256) | 1024 (draft 960) | 0.25 / 1 | resident ~150 MiB * 1.2 (short run); live peak 804 * 1.2 = 965 -> 1024 |
| system_one | 2560 | 4608 | 1 / 2 | explicit exception, see below |

Phoenix's reservation stays at 192 MiB by owner decision although its resident level after hours of real
traffic (483 MiB) is higher; re-derive it from a run of hours, not minutes.

## System One: explicit exception to the rule

`system_one` keeps 2560 / 4608 MiB without applying "peak + 20 %":

- its `memory.peak` equals its own cap (166 `memory.events max`), so the true peak is clipped and unknown;
- raising the cap only moves that pressure onto the host (RAM and swap), with no measured benefit;
- the hot set after the first request is ~3.87 GiB, under the cap.

Revisit it with `make resources-benchmark SERVICE=attach CONTAINERS=system-one RUN=...` under a
temporarily higher cap, so the unclipped peak can be read.

## `memory_min_mib` is a soft hint with no effect on this host

`memory_min_mib` renders as `mem_reservation`, which is cgroup `memory.low`. Protection only reaches a cgroup
when every ancestor also has `memory.low` set, and on this host the ancestors (user slice, session) have
`memory.low` 0. So the reservation never protects anything here; it only feeds the budget arithmetic
(the sum of reservations against host RAM, and the llm_server remainder).

## Healer threshold

`scripts/heal-omniroute-admission.sh` recycles an idle OmniRoute when its anonymous memory (`memory.current`
minus file cache) stays above the threshold for 3 runs (6 h cooldown, max 2 per 24 h). The default
threshold is **60 % of the running container's `memory.max`**: 1228 MiB at the 2048 MiB cap. The order

    threshold (0.60 cap) < guard high (0.85 cap) < guard critical (0.92 cap) < cap

holds for any cap and is tested, so a bloated idle process is recycled before a normal burst (~+0.75 GiB
above a fresh process) can push it into the guard's 503 shedding. `OMNIROUTE_HEAL_MEMORY_MIB` is an
absolute override (a warning is logged at or above 0.85 cap); `0` disables it. An uncapped container
(`memory.max` = max) is never recycled without an explicit threshold.

## Benchmark limits

- The fake upstream answers each request after ~1 s and the gateway served ~3 ok req/s, so phases above
  that rate are **saturation**, not load. A phase with under 90 % ok requests is marked `saturated`: it
  still counts toward the peak (the ceiling), but not toward the modal/retained level (the reservation).
  Per-phase ok/error counts are in the summary.
- A fake upstream understates real tool-heavy traffic; the ceiling therefore also takes the live lifetime
  peak where one exists.
- In attach mode the peak is window-only: a `memory.peak` that did not rise during the run predates it and
  is ignored.
- Swap is reported (median, max, swap-in volume and rate from `memory.stat` `pswpin`), never added to a limit.
- VRAM is sampled per process (DRM fdinfo) and per device (`mem_info_vram_used`) when `--pci`/`--render` is
  given, or auto-detected for `system-one`.

## Status (2026-10-24)

The evidence gate (`resource-evidence`, `make resources-measure`, `memory_max_evidence`) was removed: it
could not accept any real lowered cap. Lowering a cap is now allowed with a warning that points at
`make resources-benchmark`. See the plan addendum in
`docs/superpowers/plans/2026-10-23-container-resource-sizing.md`.
