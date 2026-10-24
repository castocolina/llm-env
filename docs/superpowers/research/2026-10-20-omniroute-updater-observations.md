# OmniRoute-only updater: observations for later planning

Date: 2026-10-20. Status: research only. The production updater was not invoked;
no registry image operation, migration, restart or implementation occurred.

Related: [Unified observability research](2026-10-20-laya-jev-grafana-observability.md)
and [resource-sizing research](2026-10-20-container-resource-sizing.md).

## 1. Current implementation is containment, not an updater

`scripts/update-omniroute.sh` is a deliberate 13-line safety disablement. It
returns exit 75 unconditionally, before attempting an update. It does not
compare images, inspect current eligibility, observe traffic or implement its
advertised idle policy. Its warning describes missing engineering; passing its
safety tests does not establish a functioning update path.

The old whole-stack stop/start implementation was removed in commit
`40aacb35c752fab571afe498be51443c7c77225b`. That prevented an unsafe lifecycle
operation, but did not finish the user request. Current repository HEAD for
production source is `65d10bf710ff3c87334e4b6946791dd7cdb18242`.

The existing approved design and implementation plan remain authoritative
background, preserved without modification:

- `docs/superpowers/specs/2026-10-18-omniroute-idle-aware-update-design.md`.
- `docs/superpowers/plans/2026-10-18-omniroute-idle-aware-update.md`.

This document records feasibility and missing evidence rather than replacing
those plans or starting another implementation/review cycle.

## 2. The real native dependency and its direction

A selected-field inspection on October 20 confirms this current graph:

| Container | ID prefix | Native dependencies |
| --- | --- | --- |
| OmniRoute | `44b8702af6cd` | None |
| Laya/System One | `8b0ef2bb3920` | None |
| remote-setup | `fce05b8857d5` | Current OmniRoute container ID |
| Phoenix | `19e65850edfb` | None |

All four were healthy. Their IDs and start times were unchanged by the research.
This narrow confirmation is not a fresh complete inventory of every possible
stopped/cross-project dependent; a functioning updater must perform that larger
check itself.

`pylib/compose.py` declares remote-setup's healthy OmniRoute dependency. The
installed podman-compose 1.6.0 creates a native Podman `--requires` relationship.
It belongs to the **already-created remote-setup container**. Removing a future
YAML `depends_on`, or passing `--no-deps` to a future operation, does not erase
that old native edge.

Podman 5.8.7's installed `update` interface has no operation to remove this
requirement in place. Ordinary target removal can be refused while a dependent
exists; recursive removal would violate the no-sibling-replacement requirement.
A shared pod name is not proof of a shared network namespace: previous measured
namespace evidence established independent networking here. Do not revive that
retracted shared-netns explanation.

## 3. Smallest credible migration

A future engineering effort can remove only the generated remote-setup
`depends_on` relationship, retain its backend URL and all other service
properties, and prove a **one-time remote-setup-only recreation**. That replaces
the container carrying the old native requirement, while leaving OmniRoute,
Laya and Phoenix untouched.

This is source-plausible with the installed provider, not yet a proven
production procedure. It requires a disposable test of the exact provider,
resolved Compose definition, service selection and recreation behavior. Flags
alone are not proof; podman-compose can recreate dependents in some paths.
The migration must retain remote-setup's volume/data, key cache, ports, network
attachments/aliases, mounts, environment and healthcheck. It must not use broad
`down`, recursive dependency removal, orphan cleanup or volume deletion.

The visible interruption is the remote installer service during its one
recreation. There is no identified need to stop or replace Laya, Phoenix or
OmniRoute for that migration. It is a separately scheduled operation, not
something an OmniRoute update should silently do each time.

Afterward, any other native incoming dependency still makes replacement
ineligible. A backend-wide inventory and drift check remain necessary. This
migration by itself does not qualify traffic observation or finish the updater.

### Remote-setup's application dependency remains

Removing startup ordering does not remove `/config`'s need for OmniRoute:

- `/setup.sh` is served locally; its existing healthcheck can succeed while
  OmniRoute is unavailable.
- `/config` calls OmniRoute to obtain/reuse a scoped key and calculate client
  configuration. Current handled backend failures become HTTP 502.
- The shared HTTP helpers use per-request socket timeouts, but do not provide
  one aggregate handler deadline or bounded response bodies. Common failures
  terminate, yet a fully bounded outage contract is not established.

Future planning should require explicit, bounded unavailable behavior and
retry semantics for `/config`, without replaying key-creation side effects.
It should preserve shared `_login`/`_request` caller compatibility. The installer
healthcheck must not be described as proof that backend-dependent `/config`
can succeed during an OmniRoute outage. No handler change was made here.

## 4. Functionality and acceptance evidence still missing

The planned update/activity modules do not exist yet. The work is engineering,
not a missing model, API key or GPU prerequisite.

| Missing capability | Evidence a later implementation must establish |
| --- | --- |
| Image equivalence | Resolve immutable selected-platform artifacts and compare like image/config identities, not a multi-architecture index digest against a local image ID |
| Configuration agreement and locking | Bind the intended service, deployed identity, effective configuration and lock; reject drift instead of silently broadening scope |
| Complete dependency inventory | Include stopped and cross-project backend containers; unknown or incoming native dependencies defer before destructive mutation |
| Qualified activity profile | Cover the exact deployed OmniRoute artifact/routes and signal delivery/freshness; a health endpoint, old log, dashboard total or quiet CPU is not an idle proof |
| Idle/deadline state machine | Require 300 continuous observed-idle seconds and an exclusive 10,800-second maximum-wait deadline under the retained design; activity resets quiet time, not the overall deadline |
| Override validation | Positive integer idle/max-wait overrides; explicitly empty values are invalid, and maximum wait cannot be shorter than required idle time |
| Target-only replacement | Use a verified one-service projection/provider procedure and immutable staged artifact; prove no sibling ID/start-time/config change |
| Bounded readiness and recovery reporting | Verify the new target and report failures honestly, including partial mutation; do not fabricate rollback or atomicity |
| Exit meanings | 0 for unchanged/updated, 75 for safe deferral, 1 for operational error; not an unconditional 75 for every invocation |
| Disposable proofs | Prove dependency-bearing deferral, dependency-free replacement and the separate remote-setup-only migration with retained data and no orphan fixtures |

Ordinary unit/stub tests for these capabilities can be written without touching
production or invoking paid inference. Real provider proofs need opt-in
disposable resources and known preloaded immutable images. Live registry
resolution has normal tooling/network prerequisites, not a new credential or
model requirement. No such image operations occurred in this research.

Existing full-stack lifecycle commands and the generated systemd wrapper remain
inappropriate updater mechanisms: their job is the stack, not one container.
No automatic configuration pin rewrite, sibling migration, whole-stack restart,
force bypass or resident ingress guard is part of the established scope.

## 5. Safety limits that survive a completed implementation

Observational idle detection does not atomically prevent a new request arriving
between the last check and stop. External Podman actors can also create a native
dependency after the last inventory. A target can then be stopped yet removal
refused. A repository lock cannot control arbitrary external actors.

The saved shutdown evidence includes finite native grace/drain behavior, not a
reversible atomic admission barrier. `/api/shutdown` is not a safe reusable idle
probe. An update can be target-only and conservatively gated without becoming
zero-downtime or zero-drop. Do not promise either property.

Once destructive mutation starts, failures are nontransactional. Report the old
and new target identity, exact failed stage, readiness result and observed
sibling preservation. No automatic rollback is established by the approved
scope. A passing disposable fixture is necessary evidence, not universal proof
against every future external actor or artifact.

## 6. Source and evidence map

- Read-only feasibility report:
  `tmp/research-2026-10-20/updater/feasibility.md`.
- Fresh current graph and start times:
  `tmp/grafana-comparison-research/coordinator/runtime-selected-fields.txt`.
- Current source: `scripts/update-omniroute.sh`, `pylib/compose.py`,
  `pylib/remote_setup.py`, `pylib/omniroute.py`, `scripts/start.sh`.
- Installed provider source: `/usr/lib/python3.14/site-packages/podman_compose.py`,
  dependency construction and selected-service recreation/removal paths.
- Earlier disposable topology/provider evidence:
  `tmp/phoenix-start-adjustment/topology-proof/` and `provider-proof/`.
- Approved plan/spec paths in section 1; their original bytes remain preserved.

The actionable conclusion is **finish and test the eligible path, and separately
prove/schedule the small dependency migration**. The graph explains why a safe
production replacement cannot run now; it is not a reason to leave all updater
engineering permanently disabled. This research changed neither graph nor code.
