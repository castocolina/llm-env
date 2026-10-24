# Plans index

Status of every plan in this folder, audited 2026-10-24 against the repository and `git log`. Superseded plans 2026-07-21-llm-env-separate-scripts, 2026-07-22-script-restructuring-plan and
2026-08-12-omniroute-profiles were deleted on 2026-10-24 (git history keeps them). Plans stay in place
(other documents and code comments link to them by path); this index is the single place that says what is done.
Statuses: **DONE** (shipped), **DONE, partly superseded**, **SUPERSEDED** (replaced, kept for history),
**PARTIAL** (some tasks shipped, rest abandoned), **PENDING** (not started).

| Plan | Status | Note |
|---|---|---|
| 2026-07-25-llm-env-rearchitecture | DONE, partly superseded | quadlet became compose (08-07); ROCm fallback removed (07-27) |
| 2026-07-26-setup-runtime-lifecycle | DONE | |
| 2026-07-27-agent-inference-checks-and-client-docs | DONE | |
| 2026-07-27-transparent-checks-and-vulkan-only | DONE | |
| 2026-07-31-agentic-gemma-replacement | DONE | local llama.cpp server disabled on this host |
| 2026-08-01-model-sampler-configuration | PARTIAL | tasks 1-2 shipped (`sampling` schema, preset rendering); live qualification (3-6) abandoned 2026-08-06; llama.cpp only |
| 2026-08-05-bounded-agent-harness | DONE | |
| 2026-08-07-cli-script-hygiene | DONE | |
| 2026-08-07-compose-container-definitions | DONE | resources schema later reworked by the 10-23 sizing plan |
| 2026-08-07-podman-compose-omniroute | DONE | |
| 2026-08-09-gpu-contention-diagnostic-tool | DONE | |
| 2026-08-09-resource-limits-and-single-model-setup | DONE | llama.cpp only |
| 2026-08-10-ornith-35b-moe-incorporation | DONE | llama.cpp only |
| 2026-08-11-config-aware-e2e-checks | DONE | |
| 2026-08-12-remote-agent-setup | DONE | |
| 2026-08-25-unify-agent-client-setup-scripts | DONE | |
| 2026-10-10-precommit-pyright-parallel-tests | DONE | |
| 2026-10-10-system-one-provisioning | DONE | |
| 2026-10-13-omniroute-smart-p1-spikes-derived-image | PENDING | go/no-go spikes; PRD `docs/prds/omniroute-smart-routing-v2.0-prd.md` |
| 2026-10-13-omniroute-smart-p2-smart-catalog-plugin | PENDING | blocked on P1 GO |
| 2026-10-13-omniroute-smart-p3-smart-strategy | PENDING | blocked on P2 |
| 2026-10-13-omniroute-smart-p4-fusion-integration | PENDING | blocked on P3 |
| 2026-10-13-omniroute-smart-p5-deploy | PENDING | blocked on P4; the only plan that changes production |
| 2026-10-13-omniroute-smart-rail | PENDING | not a code plan: the one-shot orchestrator prompt that runs P1 to P5 in order with gates |
| 2026-10-18-omniroute-idle-aware-update | SUPERSEDED | its spec is still the safety contract; executed by the 10-22 plan |
| 2026-10-18-phoenix-laya-dashboard-trial | DONE, partly superseded | trial lifecycle replaced by phoenix-start-integration |
| 2026-10-19-laya-phoenix-live-history | DONE | gap: no `session.id` on traces |
| 2026-10-19-operations-corrections | DONE | |
| 2026-10-19-phoenix-start-integration | DONE | |
| 2026-10-22-omniroute-updater-completion | DONE | `make update-omniroute`; dated addenda (a)-(j) at the end |
| 2026-10-23-container-resource-sizing | DONE, partly superseded | evidence-gate tasks removed 2026-10-24; limits now derived by benchmark (see its addendum) |
