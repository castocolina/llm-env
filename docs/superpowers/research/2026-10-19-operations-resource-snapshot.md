# Operations resource snapshot and history-source notes

Timestamp: **2026-10-19 23:05 America/Santiago** (host clock; `date -Is` returned `2026-10-19T23:05:16-03:00`). Read-only sample; no container lifecycle changes, provider calls, or payload logging were used for this snapshot. Sample was taken shortly after the harmless real Laya-to-Phoenix browser acceptance request.

## Runtime identity and cgroup snapshot

Container IDs and started times were captured before measurement. All four existing normal containers remained running; OmniRoute, System One and remote-setup reported healthy. Phoenix had no container health status because its Compose service had no healthcheck at sample time. Podman `stats --no-stream` and cgroup v2 files were read. Values below are observations, not capacity claims. `memory.peak` is lifetime since this container's current start, and is reset on recreation.

| Service | ID prefix | Started | Podman memory current / hard | cgroup memory.current | cgroup memory.peak | memory.events (high/max/oom/oom_kill) | configured soft memory / cgroup memory.min | configured CPU shares / cgroup cpu.weight | CPU quota |
|---|---|---|---:|---:|---:|---|---:|---:|---:|
| OmniRoute | `44b8702af6cd` | 2026-10-19 18:45:19 -03 | 309.9 MB / 3.0 GiB | 332,435,456 B | 622,034,944 B | 0 / 0 / 0 / 0 | 1.0 GiB / 0 B | 1024 / 100 | 4 CPUs (`400000/100000`) |
| System One (Laya) | `8b0ef2bb3920` | 2026-10-19 18:45:19 -03 | 3.282 GiB / 4.5 GiB | 3,976,028,064 B | 4,735,868,928 B | 0 / 0 / 0 / 0 | 2.5 GiB / 0 B | 1024 / 100 | 2 CPUs (`200000/100000`) |
| remote-setup | `fce05b8857d5` | 2026-10-19 18:45:25 -03 | 12.03 MB / 64 MiB | 16,744,448 B | 40,628,224 B | 0 / 0 / 0 / 0 | 32 MiB / 0 B | 1024 / 100 | 1 CPU (`100000/100000`) |
| Phoenix | `a67c315465e7` | 2026-10-19 18:45:25 -03 | 465.4 MB / 1.0 GiB | 496,029,696 B | 722,214,912 B | 0 / 0 / 0 / 0 | 256 MiB / 0 B | 256 / 35 | 2 CPUs (`200000/100000`) |

Podman inspect's configured `MemoryReservation` matched 1 GiB / 2.5 GiB / 32 MiB / 256 MiB respectively. However, cgroup `memory.min` was **0** for all four. This snapshot does not establish that soft Compose reservation becomes an enforced cgroup floor on this host. `cpu_shares` were configured, but cgroup `cpu.weight` is the effective relative contention weight; neither is reserved CPU capacity. Hard quotas and memory maxima are ceilings, not allocations.

Prior evidence retained in the corrections brief remains relevant: OmniRoute's prior 1 GiB cap produced resource-pressure 503s around 700 MB during concurrent tool-heavy activity; Laya's older peak was 3,155 MiB against 2,560 MiB reservation and 4,608 MiB cap; remote-setup observed peak was about 42 MiB under a 64 MiB cap; Phoenix earlier peak was about 526 MiB under a 1 GiB cap. The fresh Laya lifetime peak above is higher because this container is different/restarted and must not be conflated with prior container counters.

Three additional Podman-stat/cgroup samples, two seconds apart at 23:16 local time after the harmless real Laya checks, are preserved in ignored `tmp/operations-corrections/repeated-resource-samples.tsv`. System One remained around 4.30 GiB current / 4.41 GiB peak against a 4.50 GiB hard limit; its cgroup `memory.events` had no high/max/OOM events in the preceding snapshot. This is close enough to the cap to rule out a guessed reduction, but does not establish safe peak capacity. OmniRoute stayed around 313 MiB, remote-setup around 16 MiB, Phoenix around 749 MiB for these three short samples.

No cap was reduced. These short post-request samples are not a concurrent-load benchmark or host-wide headroom guarantee. Follow-up should preserve raw command evidence locally and distinguish each new container lifetime.

After local `check-server` and the post-recreation browser check, final read-only `make status` reported project `laya`: 28 spans in the bounded page, 27 `OK`, 1 `ERROR`, time range `2026-10-19T18:59:45.645562+00:00` through `2026-10-20T02:16:19.065146+00:00`, no next cursor. These are Phoenix spans, not a hardware time series. Status output is preserved privately in ignored `tmp/operations-corrections/final-status.raw`; request/response bodies are not included in this durable report.

The same final status sample found System One `memory.current=4,308,070,400 B`, `memory.peak=4,831,838,208 B` exactly equal to its hard cgroup maximum, and `memory.events max=166` with `oom=0` and `oom_kill=0`. This is direct evidence of prior hard-limit boundary events during the current container lifetime, not proof of an OOM kill. It materially reinforces retaining the existing cap and treating additional memory demand as a risk; no cap reduction or workload stress test was attempted. OmniRoute, remote-setup and Phoenix still had zero `max`/OOM events in that sample.

## History source and semantics

The installed System One observer is configured in the generated Compose service with `LAYA_PHOENIX_URL=http://phoenix:6006/v1/projects/laya/spans`; real Laya requests are pushed as Phoenix spans. The current endpoint is an ASGI observer/export path, not a Phoenix scraper and not a hardware metrics exporter. Phoenix `/readyz` reports current application/database readiness; it is not request history. Request/response spans and cgroup usage are separate data sources.

Phoenix REST traces are queryable from the `laya` project. Existing historical records begin at `2026-10-19T18:59:45Z` per preserved receipt. These are records captured since instrumentation/deployment, not a complete reconstruction of all requests made before observation existed. Older local review receipts may only be a provenance-verified subset and were not imported. No private logs, old request payloads, credentials, or SQLite contents were scanned for this report.

A current project span query can expose actual request/response attributes, but standard `make status`, `make check-setup`, `make check-server`, and `make logs` still require complete operational integration to show bounded completeness/range metadata, readiness, health, resource measurements, and redacted diagnostics. Do not infer absent values as zero, claim all historical traffic, or equate Phoenix UI charts with resource time-series collection.
