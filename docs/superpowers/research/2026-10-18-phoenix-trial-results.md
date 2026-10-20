# Phoenix synthetic dashboard trial results

> This report separates observed evidence from untested requirements. A passing API/persistence test does not establish native UI fit, historical usefulness, or retention execution.

## Execution metadata

- Status: **runtime acceptance passed; UI fit and retention execution remain untested**.
- Rootless engine: Podman; the opt-in test confirmed `podman info --format '{{.Host.Security.Rootless}}'` returned `true`. Earlier environment inspection recorded Podman 5.8.7 and podman-compose 1.6.0.
- Passing serialized test project: `llm-env-phoenix-trial-it-dacd942f072e`.
- Run ID: `bf1693fd6e5146d3be9c17666bd4de59`.
- Phoenix API project: `laya-phoenix-trial`.
- Server image ID: `048a41a2de914fabe9ef7cf0e2032ae36d2c3aabfa7d3c881d81dac44b3dc101` (configured digest: Phoenix 20.23.0, `sha256:c542e7de1dc1fdd0f938de337d44ebefa7266e91391eed6e7978523d7f6e1f2f`).
- Server container IDs across recreation: `c1528e66ed5b` → `59e38aca76d0`; the image ID and external volume `llm-env-phoenix-trial-it-dacd942f072e-data` were unchanged.
- Host port was a test-selected free loopback port; observed binding was only `127.0.0.1:58827` (container port 6006).
- Full sanitized runtime evidence: `tmp/phoenix-trial-implementation/2026-10-19-main/runtime-tmp/integration-evidence/llm-env-phoenix-trial-it-dacd942f072e.json` (mode 0600). Sample records and all command logs are under `tmp/phoenix-trial-implementation/2026-10-19-main/`.
- No Laya inference, production credentials, production database, or production lifecycle operation was part of the trial.

## Evidence checklist and capability matrix

The exact verified run reports 13 spans, 12 root requests, 9 OK and 3 ERROR roots, 25% root error rate, 65 ms median, 900 input and 18 output tokens recorded, 3 requests with unknown usage, 2 batch requests, 15 requested items, 18 questions, and 15 completed answers. These values were verified from the API readback against the immutable expected artifact before and after server recreation. They are fixture facts, not historical performance, savings, or real confidence measurements.

| Requirement | Demonstrated native UI | API/export capability | Gap / evidence |
|---|---|---|---|
| 12 root requests vs 13 total spans; child excluded from request denominator | Untested; no browser evaluation | Exact API readback verified 13 span identities and a root-only summary of 12 requests | No UI denominator observed. Exact run ID and receipt are above. |
| Latency distribution and 65 ms median | Untested | Expected summary and persisted API records verified | No native percentile/distribution display observed. |
| 9 OK / 3 ERROR / 25% root request error rate | Untested | Exact API readback and root summary verified | No native grouping/filter display observed. |
| 900 input / 18 output recorded tokens; 3 unknown-usage requests | Untested | Exact API readback preserved known values and missing-usage semantics | No UI display observed; unknown values were not treated as zero by the contract. |
| Safe metadata, request/result types and synthetic trace details | Untested | Synthetic record attributes and trace identities verified by API comparison | Attribute visibility is not evidence of a chart. |
| Choice and gate confidence distributions | Untested | Values/absence retained in exact API data | Native distribution/histogram unsupported or supported: not evaluated. No absent confidence was replaced with zero. |
| 2 batch requests / 15 items / 18 questions / 15 completed answers | Untested | Exact API readback and summary verified | No native batch grouping/metric display observed; cardinalities are not extra requests. |
| CPU/RAM for Phoenix and sender | Not applicable | Phoenix snapshots captured using `podman stats --no-stream --format json` | Phoenix samples were one-time snapshots, not sustained benchmarks: before seed 99.60% CPU / 457.6 MB; after seed 92.32% / 458.7 MB; after recreation 105.63% / 451.9 MB. CPU percentages are engine-reported instantaneous values and can exceed 100%. Sender was too short-lived for a meaningful stats snapshot; no sender CPU/RAM claim. |
| Phoenix volume logical bytes before seed, after seed and after recreation | Not applicable | Read-only inspector samples on the owned volume | Before seed 1,312,456 bytes; before recreation 1,415,456 bytes; after recreation 1,196,032 bytes. The seed command returned 2 after the POST; therefore the lifecycle did not write an `after-seed` sample. These are summed regular-file sizes, not allocated blocks, free space, or quota. Exact run was read-back verified after the uncertain outcome and after recreation. |
| Seven-day retention policy, project override and cleanup schedule | Untested | Runtime container environment included `PHOENIX_DEFAULT_RETENTION_POLICY_DAYS=7` | No project-specific override or actual scheduled cleanup was observed. No seven-day wait was attempted; this setting is not a disk quota. |

## Runtime acceptance and isolation

The serialized opt-in suite passed all four tests with `-n 0`: exact persistence/noninterference, busy-port refusal, unowned-volume refusal, and the opt-in guard. It observed:

- Rootless Podman and the pinned server image, owned labels, one external named data volume, `internal: true` network, loopback-only port publication, and expected Phoenix environment allowlist. Phoenix had no GPU/device or host bind/socket mounts.
- The sender and Phoenix were attached only to the trial network. Sender IPv4/IPv6 route-table inspection found no external default route (a kernel loopback-only IPv6 route was excluded correctly). No external connection was attempted.
- Synthetic ingestion and read-only exact API verification succeeded. The first sender command returned exit code 2 after its POST; the documented GET-only recovery returned `status: verified` with the exact run ID, summary, and all 13 expected span IDs. `make phoenix-trial-verify-persistence` then verified before recreation, observed a changed container ID with unchanged image and volume, and verified the same run from the sender after recreation. No POST retry/reseed was performed for that uncertain run.
- `make phoenix-trial-stop` in the successful acceptance test stopped its scoped services while retaining the named volume. Production `.env` and generated `tmp/docker-compose.yml` hashes were unchanged; the private integration Podman storage had zero non-trial containers before and after.
- Busy-port and intentionally unowned-volume tests failed before adopting/creating trial resources. The intentionally unowned test volumes were retained.

A separate shell-level lock leak was found during earlier live attempts: rootlessport inherited the lifecycle lock descriptor and kept it locked after the lifecycle command exited. A failing stub test proved descriptor inheritance; `scripts/phoenix-trial.sh` now closes descriptor 9 inside the Compose subprocess, and focused shell tests plus `make validate` and the offline full suite passed after the fix. Existing failed-attempt processes are not removed by this report. Inspection found four Phoenix servers still running in the private integration Podman storage (`d0037fed8ac2`, `7145e07e4778`, `f9e40730f1b0`, `57cb6860912c`) and four lifecycle lock holders (`d0037fed8ac2`, `7145e07e4778`, `df2fa7649f32`, `d65e5cbc7b05`) from earlier runs; two additional deliberately unowned volumes (`bc8858c3d07d`, `a2d4ea91db55`) were retained by safety tests. These are isolated trial/test resources under repository `tmp/`, not production services. They were inspected and intentionally left intact for diagnosis; they may consume resources or hold their test ports. Do not assume they were stopped by later passing tests.

## UI assessment and remaining limitations

A cached sandboxed Chromium 154.0.8037.0 and Playwright package were available; the coordinator's short-TMPDIR probe launched Chromium successfully (`tmp/phoenix-trial-implementation/2026-10-19-main/browser-probe/probe-short-tmp.log`). The earlier launch failure was a Unix socket path-length issue under a long TMPDIR, not missing browser tools. However, no Phoenix page was actually evaluated in this run, so every native-UI row above remains **untested**; API persistence must not be presented as native dashboard fit. The integration test confirmed the host loopback endpoint and sender/API route, not browser rendering.

The CPU/RAM figures are brief snapshots around a short synthetic test, not a sustained load or resource budget. The after-seed logical-volume sample is absent because the sender command exited nonzero; recovery and persistence were proven, but this sample gap is preserved rather than filled with a measurement from a different phase. The volume's logical byte total decreased after recreation; the exact run remained readable, and no disk-allocation or compaction conclusion is drawn.

## Assessment and decision

- **Isolation/persistence/noninterference:** runtime acceptance passed for the recorded isolated project and exact synthetic run.
- **Native UI fit:** not evaluated; do not make a product-fit decision from the API result.
- **Resource footprint:** limited point-in-time Phoenix samples only; sender footprint and sustained behavior are unknown.
- **Retention execution:** untested; only the configured seven-day default was observed.
- **Operational caution:** earlier failed attempts left scoped test servers and lock holders in private test storage. Preserve them until their exact ownership and safe stop procedure are explicitly resolved; do not delete any volume.

Read-only uncertain-run recovery:

```bash
UV_OFFLINE=1 uv run llmenv.py phoenix-trial verify \
  --base-url "http://127.0.0.1:<trial-port>" \
  --run-id '<run-id>' --expected '<private-state>/runs/<run-id>/expected.json'
```

Do not reseed an uncertain run. `make phoenix-trial-stop` only stops the scoped Compose service and retains the external volume; no boot enablement or permanent integration is authorized by this evaluation.
