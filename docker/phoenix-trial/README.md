# Phoenix trial (Phase A, synthetic)

Isolated evaluation of the Phoenix request/decision dashboard against a
checked-in synthetic Laya-shaped dataset. This project is standalone: it is
never part of `make start`/`make stop`/`make clean`, never touches the
production compose file, the production network, the Laya container, GPU
devices, the container-engine socket, or any credential.

## Pinned images

- Server: `docker.io/arizephoenix/phoenix:20.23.0@sha256:c542e7de1dc1fdd0f938de337d44ebefa7266e91391eed6e7978523d7f6e1f2f`
  (multi-platform index; resolve the host platform at pull/build time, no
  floating tags). Distroless and nonroot: do not assume a shell, curl or
  wget inside the server container.
- Sender: `ghcr.io/astral-sh/uv:0.12.19-python3.13-trixie-slim@sha256:aba5f865793af9275ceaa06d30935dfc6db8b81969c9fb099b0ebca6396b189e`
  via `Dockerfile.sender` (curated file list, warmed `uv` cache, offline
  runtime).

Sources verified 2026-10-18: release `arize-phoenix-v20.23.0`
(2026-10-16), official self-hosting/configuration/retention docs, and the
pinned `spans.py`/`app.py`/Dockerfile sources (see
`docs/superpowers/specs/2026-10-18-phoenix-laya-dashboard-trial-design.md`).

## Topology and isolation boundaries

- Compose project default `llm-env-phoenix-trial`; every owned resource
  (server container, trial network, external volume, sender image) carries
  labels `llm-env.scope=phoenix-trial` and
  `llm-env.project=<project>`. Conflicting or unowned resources are
  refused, never adopted.
- One persistent `phoenix` service on a dedicated `trial` network declared
  `internal: true` (no external egress from the network). The only
  publication is `127.0.0.1:${LLM_ENV_PHOENIX_PORT:-6006}:6006` on the
  host loopback; gRPC 4317 and metrics 9090 are never published.
- One external named volume `<project>-data` mounted at `/mnt/data`
  (SQLite working dir). External declaration means compose teardown never
  deletes it, and this deliverable contains no volume-deletion command at
  all.
- No GPU, `privileged`, host bind mounts, engine socket, or credential
  passthrough. `PHOENIX_ALLOWED_PROVIDERS=NONE` hides provider options in
  the UI but is not a firewall; isolation relies on the `internal: true`
  network plus loopback-only publication. The serialized rootless integration
  observed these settings, no sender external default route, successful
  internal sender/API access, and exact persistence; see the evidence and
  remaining limits in
  `docs/superpowers/research/2026-10-18-phoenix-trial-results.md`.
- Operator environment to compose environment mapping (GLM-PHX-001):
  `LLM_ENV_PHOENIX_TRIAL_PROJECT` → `PHOENIX_TRIAL_PROJECT`,
  `LLM_ENV_PHOENIX_PORT` → `PHOENIX_TRIAL_PORT` (validated 1–65535, must be
  free on 127.0.0.1; the script fails rather than killing the owner),
  `LLM_ENV_PHOENIX_RETENTION_DAYS` → `PHOENIX_TRIAL_RETENTION_DAYS`
  (positive integer; 7-day default is a policy choice, not a measured
  bound). The lifecycle script validates these, exports only them (plus the
  validated volume/sender-image names), points `--env-file` at `/dev/null`
  and clears inherited compose selectors so no repo `.env` or shell
  environment leaks into the trial compose run.
- Sender isolation evidence (GLM-PHX-002): verify the sender base's shell
  before use, then inspect `/proc/net/route` and `/proc/net/ipv6_route`
  (no iproute2 assumption); Phoenix itself is distroless and is probed only
  through the pinned REST API or host curl.

## Data and lifecycle

- All data is synthetic: fixture `laya-synthetic-v1` in
  `fixtures/laya-synthetic.json`, Phoenix API project `laya-phoenix-trial`.
  12 root request spans (9 OK / 3 ERROR) plus one child span; the root-only
  summary is 13 spans / 12 requests / 25% error rate / 65 ms median /
  900+18 recorded tokens with 3 unknown-usage requests. Fixture values are
  not historical savings, real confidence measurements, or production
  timing.
- Lifecycle actions (explicit opt-in, serial, locked):
  `make phoenix-trial-up`, `make phoenix-trial-seed`,
  `make phoenix-trial-status`, `make phoenix-trial-verify-persistence`,
  `make phoenix-trial-stop`.
- `seed` is the only action that writes to Phoenix: the host durably
  prepares an immutable `expected.json` and an exclusive `attempt.json`
  marker before exactly one POST; read-only `verify` (GET only) recovers
  accepted-but-uncertain runs.
- Recovery of an uncertain run (accepted response lost, or readback
  timeout) is read-only GET-only and never reseeds. The host recovery
  command is printed on seed failure diagnostics:

  ```bash
  UV_OFFLINE=1 uv run llmenv.py phoenix-trial verify \
    --base-url "http://127.0.0.1:${LLM_ENV_PHOENIX_PORT:-6006}" \
    --run-id "$run_id" --expected "$expected_path"
  ```

- `verify-persistence` first runs the read-only host verify, then
  recreates only the `phoenix` container against the same inspected image
  and volume, then re-verifies the same run exactly (identity + content,
  not totals alone).
- `stop` runs scoped `down` without `-v`: the external volume, built
  sender image, and host state (expected/prepared/attempt receipts under
  the per-project state directory) are retained for diagnosis and later
  inspection.

- Mutating actions use one per-project `flock`, reject state-path symlinks,
  verify Phoenix trial labels and Compose ownership before acting, and refuse
  busy ports or conflicting image/volume/network/container identities. They
  invoke only this absolute compose file with `/dev/null` as the env file and
  clear inherited `COMPOSE_*` selectors. `up` starts only `phoenix`; it does
  not call the production lifecycle.
- Logical volume-byte samples are recorded as private JSON under
  `${XDG_STATE_HOME:-$HOME/.local/state}/llm-env/phoenix-trial/<project>/resource-samples/`.
  The inspector is a one-shot pinned sender container with `--network none`
  and only `<project>-data:/inspect:ro`; it reports the sum of regular-file
  sizes, not allocated blocks, free capacity or a quota. Phases are timestamped
  `before-seed`, `after-seed`, `before-recreation`, and `after-recreation` where
  those lifecycle milestones are reached. A failed inspect blocks the action
  rather than weakening volume permissions or SELinux.

## Retention and limits

`PHOENIX_DEFAULT_RETENTION_POLICY_DAYS` is scheduled cleanup, not a disk
quota. Retention is indefinite unless a positive value is set; the trial
default is 7 days. Project-specific overrides can differ from the default.
Expired data is a verification mismatch, never a reason to reseed. Runtime
acceptance observed `PHOENIX_DEFAULT_RETENTION_POLICY_DAYS=7` in the isolated
server environment, but did not observe a project-specific override or an
actual scheduled cleanup; the configured value is not a quota or proof of
retention execution.
