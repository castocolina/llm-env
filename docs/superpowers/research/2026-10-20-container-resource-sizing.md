# Container resources: realistic usage and a responsive 32 GB host

Date: 2026-10-20. Status: read-only research; no limits were changed and no new
load or inference benchmark was run.

Related: [Unified Laya/Jev observability research](2026-10-20-laya-jev-grafana-observability.md)
and [OmniRoute updater observations](2026-10-20-omniroute-updater-observations.md).

## 1. User requirement and units

The intended workload is ordinary use with roughly one to five LLM/agent
runtimes and a browser. The user regards approximately 50 simultaneous requests
as an upper scenario worth considering, not a reason to provision for an
extreme 200-RPS benchmark. The 32 GB machine sometimes freezes when displayed
memory usage passes roughly 25 GB. That responsiveness problem matters more
than a synthetic maximum-throughput score.

Three quantities must be kept separate:

| Quantity | Meaning |
| --- | --- |
| 50 total requests | A sample size; it says nothing about how closely they arrive |
| 50 requests/second | An arrival rate; sustainable only if service capacity supports it |
| 50 requests in flight | Concurrency, including waiting work |

A 50-request representative sample can estimate typical resource use without
sending 50 requests at once. Conversely, one to five harnesses do not prove
concurrency is at most five: a harness can fan out tools/questions. Record actual
HTTP concurrency, questions per request, batch size, payload size and latency.
Do not infer any of them solely from the number of open LLM terminals.

Little's law relates average in-flight work, throughput and end-to-end latency
in a stable system. It does not justify applying an unloaded 16 ms latency to
an offered 200-RPS load that exceeds measured capacity. Queueing and rejections
change that calculation.

## 2. What existing limits mean

- CPU shares and cgroup `cpu.weight` are relative contention weights, not
  reserved cores. A quota of two CPUs is an upper CPU-time rate, not two busy
  cores while idle. Report CPU use as cores or clearly normalized percentages.
- Compose memory reservation maps here to `memory.low`: best-effort reclaim
  protection, not preallocated RAM. All four measured `memory.min` values are
  zero. Lowering a reservation does not release that number of MiB.
- A hard memory cap bounds charged cgroup memory; it is different from actual
  residency, process RSS, swap, GPU VRAM, image size and download size.
- The sum of hard caps is not actual allocated RAM. Lifetime peaks also occur
  at different times and must not be summed as a measured simultaneous peak.

The current four hard caps sum to 8,768 MiB, about 8.56 GiB. In the measured
snapshot the containers actually used about 2.90 GiB of charged resident memory
and 1.53 GiB of logical swap. Even those two quantities are not simply additive
physical RAM consumption when swap includes compressed zram.

## 3. Measured per-container state

Source: `tmp/research-2026-10-20/resources/measurement.json`, timestamp
`2026-10-20T12:22:07.671Z`. This includes a ten-second idle sample and lifetime
cgroup counters. It is not a capacity test.

| Service | Current RAM MiB | Logical swap MiB | Lifetime RAM peak MiB | Soft / hard RAM MiB | CPU weight / quota |
| --- | ---: | ---: | ---: | --- | --- |
| OmniRoute | 266.94 | 336.97 | 593.22 | 1024 / 3072 | 100 / 4 CPUs |
| Laya | 2593.19 | 828.37 | 4608.00 | 2560 / 4608 | 100 / 2 CPUs |
| remote-setup | 14.71 | 10.66 | 38.75 | 32 / 64 | 100 / 1 CPU |
| Phoenix | 98.93 | 387.38 | 764.95 | 256 / 1024 | 35 / 2 CPUs |

Configured CPU shares are 1024 for the first three and 256 for Phoenix; the
observed cgroup weights above are the actual kernel values. The ten-second CPU
sample used approximately 1.55%, 0.44%, 0.92% and 0.61% of one core respectively.
Those idle values do not establish a safe busy-time quota.

Laya's `memory.peak` equals its 4,608-MiB cap and `memory.events.max` is 166, with
zero OOM/OOM-kill events. These are cumulative charging/limit events, not 166
failed requests or proof of ongoing pressure during the idle sample. They do
show that reducing the cap from a 2.6-GiB idle residency alone would be unsafe
reasoning. Significant swapped-out anonymous memory also makes current RAM
understate the complete working-set demand.

Phoenix's roughly 99-MiB resident value conceals approximately 387 MiB of swap
and a 765-MiB lifetime RAM peak. That is not evidence for a 256-MiB hard cap.
Remote-setup already has only a 64-MiB cap; reducing it offers negligible host
savings. The meaning and time span of each peak must survive any report;
container recreation changes the measurement baseline.

## 4. What the preserved Laya benchmark actually measured

`scripts/system-one-benchmark.sh` uses `xargs -P` to vary **HTTP concurrency**.
Its five fixed ticket payloads each ask two questions, Choice plus Noul. Phases
include 10/100/1000 sequential calls, 1000 calls at concurrency eight, 2000 at
concurrency sixteen, and twice the configured admission count. Current
`LAYA_MAX_CONCURRENT` is 64, so that final phase offers 128 concurrent calls.
It is not a configured 200-RPS arrival test.

The preserved October 12 table contains:

| Phase | Total / concurrency | Attempt throughput | p50 / p95 | Non-200 reporting |
| --- | --- | ---: | --- | --- |
| 10 sequential | 10 / 1 | 30/s | 16.2 / 177.3 ms | Not printed |
| 100 sequential | 100 / 1 | 60/s | 15.6 / 17.6 ms | Not printed |
| 1000 sequential | 1000 / 1 | 62/s | 15.5 / 17.0 ms | Not printed |
| Concurrent eight | 1000 / 8 | 69/s | 100.4 / 142.7 ms | Not printed |
| Concurrent sixteen | 2000 / 16 | 78/s | 203.0 / 208.9 ms | Not printed |
| Twice admission | 128 / 128 | 111/s | 75.7 / 1044.8 ms | 63 non-200 |

The last throughput includes rejected requests: **111 attempts/s is not 111
successful inferences/s**. The script only prints the non-200 count in that
last phase; do not invent successful-response counts for the other rows. Latency
percentiles in the rejected phase likewise mix successful and rejected calls.
No confirmed 200-successful-RPS result was established by this bounded research;
that does not disprove the user's recollection of another run.

That run reported a 2,760-MiB lifetime RAM peak, roughly 1,995–2,226 MiB current
RAM through its phases, and approximately 4,201 MiB Laya-process VRAM while hot.
The earlier provisioning evidence records a 3,155-MiB load peak and about
2.39 GiB steady RSS. `pylib/resources.py` cites that 3,155-MiB peak for the
4,608-MiB cap and approximately 1.3 cores at sixteen clients for the two-CPU
quota. The cap is roughly 46% above that historical peak; it was not calculated
from an average of 200 simultaneous requests. The 2,560-MiB soft policy is near
the historical steady working set, not a hard allocation.

The raw table was copied byte-for-byte from
`/tmp/system-one-benchmark-2026-10-12T06:52:31-03:00.txt` to
`tmp/grafana-comparison-research/coordinator/benchmark-2026-10-12.txt`.
Both have SHA-256
`ee4b375c9aea5eabbf651b1845e0629d0c5142d10d72ffdca45f0930dc2885a9`.
The original provisioning plan remains at
`docs/superpowers/plans/2026-10-10-system-one-provisioning.md`.

## 5. Host memory, zram and the freeze symptom

A fresh, read-only snapshot at `2026-10-20T14:06:39.662Z` reports:

- Usable `MemTotal`: 32,730,349,568 bytes, approximately 30.48 GiB.
- `MemAvailable`: 10,178,711,552 bytes, approximately 9.48 GiB.
- Zram logical swap used: 15,496,146,944 bytes. This is **not** that much
  additional physical RAM: zram's `mm_stat` reports 4,231,987,200 bytes of
  physical memory use, approximately 3.94 GiB.
- Disk swap used: 4,626,038,784 bytes, approximately 4.31 GiB.
- Memory PSI `some` and `full` averages are zero over 10/60/300-second windows.
  IO PSI is nonzero but small in this snapshot. No freeze was reproduced.
- `Committed_AS` exceeds the nominal `CommitLimit`. Those are virtual-memory
  accounting quantities, not measurements of resident RAM or proof of a stall.

The evidence is
`tmp/grafana-comparison-research/coordinator/host-memory-snapshot.json` and its
read-only collector, `read-host-memory.mjs`. Earlier agent snapshot values are
older observations, not interchangeable with these timestamped measurements.

The user's freeze experience is a meaningful constraint, but this idle snapshot
does not establish its cause or a universal 25-GB failure threshold. Observe
MemAvailable, memory/IO PSI, swap-in/out rates, major faults/refault **rates**,
zram physical use, disk IO and GPU/desktop contention during normal work.
Cumulative counters alone cannot prove current thrashing. A displayed “used
RAM” figure also depends on its cache/accounting definition.

`HOST_MEMORY_FLOOR_MIB = 4096` in `pylib/resources.py` is only a conservative
planning allowance. It neither reserves memory for the desktop nor limits
browser/harness growth. A useful budget must include the OS/compositor, browser,
agent runtimes, zram physical cost, services and the proposed monitoring stack.
Do not assign the entire unexplained remainder to one application category or
assume the fixed four-GiB floor covers this user's actual session.

## 6. Representative sizing instead of an extreme benchmark

A research protocol suited to this requirement would observe approximately
**50 total representative requests during normal use**, preserving actual
inter-arrival times and session fan-out. Include short and longer states,
question counts, language/checkpoint routing, batches and retries. Capture a
cold/reload case as well as a warm model; persistent model memory does not
scale linearly with HTTP request count.

Treat a controlled burst up to approximately 50 in-flight requests as a separate
upper scenario only if it reflects a real need and the host is healthy. It is
not necessary to rerun 128-in-flight admission overload or target 200 RPS to
choose an ordinary-use reservation. If a smaller concurrency bracket is tested,
state it explicitly; it is a proposed test choice, not a fact inferred from the
one-to-five harness count. No such run occurred in this research.

Record before/during/after:

- Host MemAvailable, PSI, swap rates, zram physical use, responsiveness and IO.
- Per-container current/peak RAM, anonymous/cache components, swap, limit-event
  deltas, CPU time/throttling and cgroup identity.
- GPU VRAM/busy with scope stated: device-level versus a separately qualified
  process measurement.
- Latency, success/rejection/timeout counts, concurrency and questions per call.
- Collector/exporter loss, its own resource cost and ingestion/disk growth.

Set abort/alert conditions from a measured responsive baseline and the user's
working-session budget. Do not invent a six-GiB available-memory threshold or
reproduce a freeze just to validate one. Observe first; no automated host
process killing, desktop changes, model unloading or swap-policy changes are
proposed as part of this research.

An observed average/typical working set can inform **soft protection**, while
retaining startup and reload evidence. A hard cap needs headroom above relevant
peaks and must account for offloaded anonymous memory; the mean of 50 samples
is not a safe hard cap. Fifty requests also do not establish rare-event capacity
or every future payload shape.

## 7. Candidates, durability and unresolved work

| Service | Research conclusion, not an applied change |
| --- | --- |
| OmniRoute | Strongest candidate for a controlled reduction, for example testing 2 GiB rather than 3 GiB. That value is unvalidated. Prior 1-GiB limits triggered the application's resource-pressure 503 guard during tool-heavy sessions, despite host free RAM. |
| Laya | Keep current policy pending representative load/reload evidence; do not lower from idle RAM while peak/limit-hit/swap evidence exists. |
| remote-setup | Already modest. No evidence that shaving this cap materially improves responsiveness. |
| Phoenix | Do not size from its swapped-down 99-MiB residency. A future replacement must include the cost of its replacement backends, not count retirement as free savings. |
| Monitoring stack | No measured budget yet. A smaller backend set is an architectural candidate, not a proven RAM saving. Include compaction/query/startup peaks and host-user collectors. |

Persistent per-service override support is missing. `scripts/start.sh` recomputes
resource policy and rewrites OmniRoute, remote-setup, Phoenix and relevant
llama.cpp fields; System One policy is also supplied by fixed code defaults.
Editing generated Compose or absolute values that startup replaces is not
sustainable tuning. A later implementation needs validated persistent overrides,
clear effective-policy reporting and proof that ordinary start/boot preserve
those choices. No new configuration surface was implemented here.

Observer latency overhead, monitoring overhead, resource behavior under the
representative workload and any new caps remain unverified. The current healthy
four-container state does not close those gaps.

### Source references

- Local: `pylib/resources.py`, `pylib/compose.py`,
  `scripts/start.sh`, `scripts/system-one-benchmark.sh`.
- Earlier measured report:
  `docs/superpowers/research/2026-10-19-operations-resource-snapshot.md`.
- [Kernel cgroup v2](https://docs.kernel.org/admin-guide/cgroup-v2.html).
- [Kernel pressure stall information](https://docs.kernel.org/accounting/psi.html).
- [Kernel zram accounting](https://docs.kernel.org/admin-guide/blockdev/zram.html).
- [Prometheus instrumentation practices](https://prometheus.io/docs/practices/instrumentation/).

All source/resource observations are scoped to the inspected versions and dates.
There was no limit update, host-policy change, service recreation or capacity
certification.
