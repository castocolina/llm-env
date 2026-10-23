# Updater isolated-backend proof record

Command: `LLM_ENV_UPDATE_PROOF=1 uv run pytest tests/test_omniroute_update_proof.py -q -s`
(date 2026-10-23, repository `llm-env`, all work under the repository's ignored `tmp/`).

Result: **3 passed** (`test_update_replaces_only_omniroute_and_is_idempotent`,
`test_dependent_container_defers_and_nothing_changes`,
`test_migration_clears_only_the_remote_setup_dependency`) in 81.69s after a warm
first run of 1 pass / 2 fixture-staleness failures that motivated the fixes below.

These runs prove **replacement mechanics only** (pull/verify/stage/projection/
tag-move/stop-rm-up/verify-commit and the ownership deferral) inside a disposable
rootless Podman backend. They are **not** exact-OmniRoute activity qualification,
which comes only from the characterization step (never executed as part of this
run; the shipped activity-profile list remains empty, so the production updater
defers with `capability`).

## Host versions

- `podman version 5.8.7`
- `podman-compose version 1.6.0`
- `skopeo version 1.22.3 commit: 92397d5ba399784c6e2f9f229a4244b724743dba`
- `systemd 259 (259.9-1.fc44)` (journalctl)

## Fixture config ids (built twice with different `VARIANT` in the proof backend)

- old: `sha256:418e8cd4233e` (full `sha256:418e8cd4233e943e1b78cc2882866d51e47b7c22e1c535b8dc3a71f7ab255e6f`)
- new: `sha256:9fece43a192c` (full `sha256:9fece43a192c240938bee2b2df965f112121081f20485e93d2a2a5dd33dd5049`)

The updater's own reported transition in the passing run used the same fixture pair
(the `updated <old12> -> <new12>` line matched the last stdout line).

## Environment findings that changed the proof harness (deviations from the plan text)

1. **Healthchecks must be driven explicitly.** conmon sanitizes its environment, so
   with an isolated `CONTAINERS_STORAGE_CONF` the automatic healthcheck timer execs
   `podman` against the *default* store and can never find the proof containers
   (user journal: `no container with name or ID ... found`). `ProofBackend` now runs
   a `podman healthcheck run` loop (same CMD, 2s cadence, transient failures
   swallowed) while a deployment exists. Without it, `depends_on: service_healthy`
   deploys hung for the full 300s `up` timeout and no replacement ever became
   healthy.
2. **The mutable tag's local copy must be dropped between tests.** After a
   successful update the proof store's `127.0.0.1:PORT/proof/omniroute:latest`
   points at the new digest; `podman-compose up` reuses that stale local image, so
   `reset_deployment()` also removes the reference (proof store only).
3. **skopeo does not read `CONTAINERS_REGISTRIES_CONF`.** skopeo 1.22.3 on this host
   ignores that variable (and `REGISTRIES_CONF_PATH`) for `docker://` transports but
   does read `$HOME/.config/containers/registries.conf`; the proof home carries the
   insecure-registry entry while podman keeps the env var.
4. **Rootless networking under the repo `tmp/` needs slirp4netns.** pasta cannot open
   its rootless-netns PID file there (`Permission denied`), so the proof
   `containers.conf` pins `default_rootless_network_cmd = "slirp4netns"`.
5. The real `podman-compose` writes pull/create progress to the inherited stdout, so
   the updater's verdict is asserted as the **last** stdout line in the proof (the
   stub-world tests keep the exact-output assertion).

## Reproduce

```bash
podman pull docker.io/library/registry:2 docker.io/library/python:3.12-slim   # host prerequisites, once
LLM_ENV_UPDATE_PROOF=1 uv run pytest tests/test_omniroute_update_proof.py -q -s
```

## Follow-up corrections in the same execution pass (2026-10-23, later run)

A re-run after the first record (`3 passed in 83.50s`, warm store) plus the characterization
attempt produced these additional harness findings. Each was fixed in
`tests/proof_backend.py` / `tests/test_omniroute_update_proof.py` and re-verified:

1. **The healthcheck pump has to be running *during* `up -d`.** A `depends_on: service_healthy`
   edge makes `podman-compose up` wait for the parent to report healthy; with the pump starting
   only after `up`, the two-service proofs hung for the full 300 s timeout. `deploy()` now starts
   the pump before `up` and leaves it running.
2. **`close()` cannot always delete its own storage.** Image content extracted by the engine can
   belong to the subuid range (`ls -l` shows `525287`), which plain `shutil.rmtree` cannot unlink;
   an interrupted run left ~1 GB undeletable under `tmp/proof-*`. `close()` now falls back to
   `podman unshare rm -rf` (without the already-deleted proof storage conf) and retries.
3. **The updater's verdict is the last stdout line** in the real-provider proofs (see note 5 above);
   the stub-world tests still pin the exact output.
4. **Proof home carries the registry configuration** for skopeo (note 3 above), written to
   `home/.config/containers/registries.conf` by `write_home()`.

## Characterization

The characterization harness ran on the same backend: see
`2026-10-23-updater-characterization.md` for the command, duration, measurements, and why no
activity profile can be checked in from it.
