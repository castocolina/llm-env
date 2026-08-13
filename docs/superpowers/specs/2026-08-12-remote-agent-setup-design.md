# Remote Agent Setup (master-key-gated curl installer) — Design

**Status:** Approved by user 2026-08-12. Ready for implementation planning.

## Goal

`setup/setup-local-llm-agents.sh` already configures Pi and OpenCode
*locally* (on the machine running `llm-env` itself) to talk to the local
llama.cpp router directly. The owner wants the same outcome on a **remote**
machine on the same LAN (e.g. a Raspberry Pi) — reachable with one command:

```
curl http://<llm-env-host>:<port>/setup.sh | bash
```

The script prompts interactively for a master key
(`OMNI_ROUTER_MASTER_KEY`, already present in this repo's gitignored
`.env`) and, once entered, configures Pi/OpenCode on the *remote* machine
to talk to **OmniRoute** (not the local router directly) — matching the
existing decision that OmniRoute is meant to be the shared/routed entry
point for anything beyond this one host.

`setup/setup-local-llm-agents.sh` itself is also repointed at OmniRoute
(not just the new remote script) — the *local* machine's Pi/OpenCode
should have the same revocable, scoped credential and the same
single-point-of-observation-in-OmniRoute property as every remote
machine, instead of being the one remaining client holding the raw
`server.api_key`. It gets its own OmniRoute-issued key
(`llm-env-local-agents`, distinct from the remote installer's shared
`llm-env-remote-agents` key), obtained via a new `llmenv.py omniroute
issue-key` CLI action rather than over HTTP — the local script already
runs on this machine with direct read access to `models.yml`'s
`omniroute.initial_password`, so there is no need for it to go through
the master-key/`/config` HTTP round trip the *remote* script requires.

## Two prerequisite decisions (confirmed with the owner, 2026-08-12)

1. **OmniRoute's port binding changes from `127.0.0.1:{port}` to
   `0.0.0.0:{port}`** in `pylib/compose.py`'s `render_compose()`. Today it's
   loopback-only (see `setup/network.sh`'s existing warning that OmniRoute's
   Network/mDNS lines aren't actually reachable yet) — this design makes
   them reachable, which is the whole point. The owner has already accepted
   this LAN-exposure trade-off ("no hay problemas con mi red local, soy el
   unico quien la usa").
2. **The remote machine never receives the OmniRoute dashboard password.**
   Verified live against a running instance: `POST /api/keys` (dashboard
   session auth) issues a **scoped** API key — confirmed by testing it
   against `POST /v1/chat/completions` (accepted) and `GET /api/providers`
   (**rejected**, `403 {"error":{"code":"AUTH_001","message":"Invalid
   management token"}}`). The remote machine gets one of these scoped keys,
   never the all-powerful dashboard password.

## Architecture

A third `podman-compose` service, `remote-setup`, alongside `llm-server` and
`omniroute`:

- **Image:** `docker.io/library/python:3.13-alpine` (off-the-shelf, no
  Dockerfile/build step — matches this repo's "no new dependency" posture;
  `pylib/omniroute.py`'s own module docstring already commits to stdlib-only
  Python, and this service follows the same rule).
- **Script:** `pylib/remote_setup.py` — stdlib-only (`http.server`, `json`,
  `hmac`, `secrets`, `urllib.request`), reusing `pylib/omniroute.py`'s
  existing `_login`/`_request`/`OmniRouteError` helpers rather than
  reimplementing OmniRoute's session-cookie auth a second time (DRY — the
  same session-login dance `provision()` already does). Because of that
  import, the whole `pylib/` directory is bind-mounted read-only into the
  container — not a single-file mount — at the **absolute** repo path
  (`render_compose()` is called with `models_dir`/`presets_path` already
  absolute for the same reason — the rendered compose file lives in
  `~/.config/llm-env/`, not the repo, so a relative `./pylib/...` source
  would resolve against the wrong directory; the same absolute-path
  convention applies here: `{repo_root}/pylib:/app/pylib:ro,z`), with the
  service's `working_dir` set to `/app` and run via
  `command: ["python3", "-m", "pylib.remote_setup"]` (module invocation,
  so `from pylib.omniroute import _login, _request, OmniRouteError` resolves
  correctly inside the container). Split into pure, unit-testable functions
  (request parsing, key-cache read/write, script templating) plus a thin
  `main()` that reads env vars and starts `http.server.ThreadingHTTPServer`
  — the same "logic separate from the socket loop" shape `tests/test_cli.py`
  already exercises for OmniRoute's own fake server.
- **Port:** new `remote_setup.port` in `models.yml`, bound `0.0.0.0` (this
  service's entire purpose is being LAN-reachable).
- **Persistent state:** a new named volume `remote-setup-data:/app/data`,
  storing one JSON file `{"id": "...", "key": "sk-..."}` — see "API key
  reuse" below for why this is necessary.

No `enabled` toggle — this service is always part of the compose stack,
matching `omniroute`'s own unconditional inclusion. It degrades safely (see
"Master key not configured" below) rather than needing an opt-out.

## The `.env` file and secret flow

`.env` (repo root, already gitignored via the existing `.gitignore` entry)
holds exactly one line:

```
OMNI_ROUTER_MASTER_KEY=<user-supplied random value>
```

This is a **user-supplied** secret, unlike `server.api_key` /
`omniroute.initial_password` (which `scripts/start.sh` generates
automatically into `models.yml`) — it belongs in `.env`, not `models.yml`,
specifically because nothing in this repo generates or migrates it; it is
the operator's own bootstrap credential, the same convention countless
other tools use `.env` for. A new `.env.example` (repo root, tracked in
git) documents it:

```
# Master key required to fetch OmniRoute credentials through the
# remote-setup service's /config endpoint (see:
# docs/superpowers/specs/2026-08-12-remote-agent-setup-design.md).
# Generate a random value, e.g.:
#   openssl rand -hex 32
OMNI_ROUTER_MASTER_KEY=
```

**How the secret reaches the container:** this repo already writes secrets
directly into the rendered (gitignored, `~/.config/llm-env/`) compose
file's `environment:` block — `LLAMA_API_KEY`, `INITIAL_PASSWORD` in
`pylib/compose.py` are already plaintext there. This design follows the
same, already-established pattern rather than inventing a new one: a small
new stdlib helper, `read_env_file(path: Path) -> dict[str, str]` (simple
`KEY=VALUE` line parser, ignoring blank lines and `#` comments — no
`python-dotenv` dependency), reads `.env` at **compose-render time** and
`render_compose()` injects the value into `remote-setup`'s `environment:`
block as `OMNI_ROUTER_MASTER_KEY`. `llmenv.py render-compose` gains a new
`--env-file` argument; `setup/render-unit.sh` passes
`--env-file "${REPO_DIR}/.env"` (the one place that already knows
`$REPO_DIR`). A missing `.env` file is not an error — `read_env_file`
returns `{}`, and the container starts with an empty
`OMNI_ROUTER_MASTER_KEY` (see "Master key not configured").

## The two endpoints

### `GET /setup.sh` — public, unauthenticated

Returns a generated bash script (`Content-Type: text/x-shellscript`). No
secret is embedded — the script is safe to serve to anyone who can reach
the port; the actual credential gate is `/config`. The server first
validates the incoming request's own `Host` header — rejecting an empty
or shell-metacharacter-laden value (a Host header is attacker-controllable
over plain HTTP, and `400`s rather than proceeding) — then reads it and
interpolates it into the script as `LLM_ENV_HOST="<that Host header,
verbatim>"` — the same trick curl-install scripts commonly use, so a user
never has to separately tell the script which server it came from, and it
keeps working across DHCP/IP changes.

The generated script, once downloaded:

1. Prints a short banner and prompts for the master key with
   `read -r -s -p 'OMNI_ROUTER_MASTER_KEY: ' master_key < /dev/tty`
   (silent, mirroring how a password prompt would look — matches
   `setup-local-llm-agents.sh`'s own `log_warn "close Pi and OpenCode
   before continuing"` UX register: short, direct, no fluff). The explicit
   `< /dev/tty` is required, not cosmetic: under `curl <url> | bash`, the
   script's own stdin is the pipe from `curl`, not the terminal — `read`
   without `/dev/tty` would silently read EOF instead of actually
   prompting the user.
2. Writes the master key to a private, mode-0600 curl config file (`curl
   -K <file>` with a `header = "Authorization: Bearer <key>"` line) instead
   of passing it as a `-H` command-line argument — mirroring
   `scripts/check-server.sh`'s own `auth_conf` pattern — since a bearer
   token on the command line is visible to every other local user via
   `ps`/`/proc/<pid>/cmdline` for as long as `curl` runs. It then issues
   `curl -sS -K <auth-conf> -o <response-file> -w '%{http_code}'
   "http://${LLM_ENV_HOST}/config"`, capturing the HTTP status code
   separately from the response body — deliberately **not** `curl -f`,
   since `-f` suppresses the response body on an HTTP error, which would
   make step 3 (parsing the JSON `error` field) impossible.
3. On a non-200 status, parses the captured response body's JSON `error`
   field (falling back to a generic message if the body isn't valid JSON),
   prints it alongside the status code, and exits nonzero (no retry, no
   silent fallback — same "die loud and specific" convention `tools/lib.sh`'s
   `die()` follows).
4. On success, builds the same two provider JSON shapes
   `setup-local-llm-agents.sh` already builds (Pi's `providers` entry,
   OpenCode's `@ai-sdk/openai-compatible` entry) — **except** `baseUrl`
   points at the returned `omniroute_base_url` instead of
   `http://127.0.0.1:{port}/v1`, and `apiKey` is the returned scoped
   `api_key` instead of `server.api_key`. Model list comes from the
   `models` array in the same response (alias/ctx_size/
   client_max_output_tokens, one entry per currently-enabled model).
   **Model ids sent to OmniRoute (in both the Pi and OpenCode provider
   JSON) MUST use the `llama-cpp/<alias>` prefix, never a bare alias.**
   This repo's own `scripts/check-server.sh` already established this is
   the only format that resolves against a live OmniRoute instance — a
   connection's models are routed by provider slug, not by the
   connection's own name, so a bare alias produces `model_not_found`.
5. Writes those into `~/.pi/agent/models.json` /
   `~/.pi/agent/settings.json` and the remote machine's OpenCode config,
   using a private staging directory and staged-write-then-atomic-move
   (temp file written alongside the target, then `mv -f` into place),
   mirroring `setup-local-llm-agents.sh`'s actual pattern. The jq filters
   are equivalent to (not textually copied from) its Pi provider/settings
   merges — the generated script is standalone bash/jq/node, not a
   fetch-and-run of `setup-local-llm-agents.sh`'s own functions. For
   OpenCode specifically, instead of a third HTTP endpoint, the generated
   `/setup.sh` response **embeds `update-opencode-config.mjs`'s full
   source via a bash heredoc** — the file is still bind-mounted into the
   container (see Architecture) so the server can read and embed it at
   script-render time. There is no route for it; this keeps the design's
   "two endpoints" contract intact while still reusing the updater's
   actual read-modify-write logic rather than reimplementing it in bash.

### `GET /config` — requires `Authorization: Bearer <OMNI_ROUTER_MASTER_KEY>`

1. Compare the provided bearer token against the container's own
   `OMNI_ROUTER_MASTER_KEY` env var using `hmac.compare_digest` (constant-time
   — this is a bearer-token check, not a hashed-password check, so no
   hashing step is needed, just timing-safe comparison). Missing/blank
   header or mismatch → `401 {"error": "invalid or missing master key"}`.
2. If the container's own `OMNI_ROUTER_MASTER_KEY` is itself empty (`.env`
   never set up) → `503 {"error": "remote setup not configured -- set
   OMNI_ROUTER_MASTER_KEY in .env and restart"}`, regardless of what bearer
   token was supplied. This is deliberately a *different* status code from
   401 so an operator debugging "why doesn't this work" can tell "you typed
   the wrong key" apart from "nobody has set a key up yet".
3. On success: idempotent API-key issuance (see next section), then
   `200 {"omniroute_base_url": "http://<Host-header-host>:<omniroute.port>",
   "api_key": "sk-...", "models": [{"alias": ..., "ctx_size": ...,
   "client_max_output_tokens": ...}, ...]}`. Same Host-header trick as
   `/setup.sh` — the address handed back is whatever address the remote
   machine used to reach *this* service, with OmniRoute's port substituted
   in, so it works whether reached by LAN IP, mDNS hostname, or (if
   ever forwarded) something else entirely.

## API key reuse (idempotency)

OmniRoute's `GET /api/keys` listing only ever returns a `keyPreview` (last
4 characters) for existing keys — the full value is returned **once**, at
`POST /api/keys` creation time (confirmed against the schema and live
behavior). This means a naive "look it up by name and reuse" approach is
impossible for the key's actual value, so re-fetching it from OmniRoute is
not an option for real idempotency.

Design: cache the created key in the `remote-setup-data` volume
(`/app/data/api-key.json`, `{"id": "...", "key": "sk-..."}`), written once
on first successful creation. On every subsequent `/config` request:

1. If the cache file exists, `GET /api/keys` (dashboard-session auth,
   obtained the same way `pylib/omniroute.py::_login()` already does — this
   container talks to OmniRoute over the compose network as
   `http://omniroute:{omniroute.port}`, not through the newly-public
   0.0.0.0 binding) and check whether the cached `id` still appears in the
   listing (the key wasn't deleted/rotated out-of-band via the dashboard).
   If present, return the cached `key` value directly — no new key
   created.
2. If the cache is missing, or the cached `id` no longer appears in the
   listing, create a new key (`POST /api/keys {"name":
   "llm-env-remote-agents"}`), overwrite the cache file with the new
   `{id, key}`, and return the new value.

This means the remote machine's config always has a *currently valid*
scoped key after any `/setup.sh | bash` run, self-healing if the key was
ever revoked from the dashboard, while avoiding an ever-growing pile of
orphaned keys under normal operation.

## `models.yml` schema change

```yaml
remote_setup:
  image: docker.io/library/python:3.13-alpine
  port: 20130
```

Same shape/defaulting pattern as `omniroute:` (`migrate_config()` sets
these two defaults when absent; `validate_config()` requires `image`
non-empty string, `port` positive integer). No secret lives here — the
master key is exclusively an `.env` concern (see above).

## `pylib/compose.py` changes

- `render_compose()` gains a `env_vars: dict[str, str]` parameter (or
  reads `.env` itself, given an `env_file` path parameter — implementation
  detail for the plan to settle, either is consistent with the module's
  existing "pure function in, compose dict out" shape).
- New `remote_setup_service` dict, mirroring the existing
  `omniroute_service` construction: `container_name: "remote-setup"`,
  `ports: [f"0.0.0.0:{remote_setup_port}:{remote_setup_port}"]`,
  `working_dir: "/app"`, `volumes:
  [f"{repo_root}/pylib:/app/pylib:ro,z",
  f"{repo_root}/setup/update-opencode-config.mjs:/app/setup/update-opencode-config.mjs:ro,z",
  "remote-setup-data:/app/data"]`
  (`render_compose()` needs a `repo_root` — a new parameter, the same way
  it already receives `models_dir`/`presets_path` as caller-supplied
  absolute paths rather than discovering them itself),
  `command: ["python3", "-m", "pylib.remote_setup"]`, `environment:` carrying
  `OMNI_ROUTER_MASTER_KEY` (from `.env`), `REMOTE_SETUP_PORT`,
  `OMNIROUTE_INTERNAL_URL` (`http://omniroute:{omniroute_port}`),
  `OMNIROUTE_DASHBOARD_PASSWORD` (`omniroute.initial_password`),
  `OMNIROUTE_PORT` (for building the returned `omniroute_base_url`), and a
  `MODELS_JSON` string (same enabled-models array
  `setup-local-llm-agents.sh` already computes via `yq -o=json`, computed
  here in Python from `cfg["models"]` instead).
- `omniroute_service["ports"]` changes from
  `f"127.0.0.1:{omniroute_port}:{omniroute_port}"` to
  `f"0.0.0.0:{omniroute_port}:{omniroute_port}"`.
- New named volume `remote-setup-data` alongside the existing
  `omniroute-data` in the compose document's top-level `volumes:` section.

## Documentation

- `setup/network.sh`'s existing OmniRoute warning ("the OmniRoute container
  only binds 127.0.0.1 -- the Network/mDNS lines above are not actually
  reachable from other machines yet") becomes **false** once this ships and
  must be removed/rewritten to reflect that OmniRoute is now genuinely LAN
  reachable — and the printed summary should mention the new
  `curl .../setup.sh | bash` one-liner for remote machines, the same way it
  already prints llm-server's and OmniRoute's own addresses.
- `.agents/architecture.md` gains a subsection on the `remote-setup`
  service: its two endpoints, the scoped-key-not-dashboard-password
  decision, the Host-header address trick, and the API-key-cache
  volume/idempotency behavior (so a future reader doesn't have to
  re-derive "why is there a cache file" from scratch, mirroring how the
  session-cookie-auth rationale is already documented next to
  `pylib/omniroute.py`).

## Testing

- `pylib/remote_setup.py`'s pure functions (bearer-token parsing/
  comparison, cache read/write, JSON response building, script templating
  with a fake Host header) get direct unit tests — no real HTTP socket
  needed for these.
- One integration-style test (mirroring `tests/test_cli.py`'s
  `_RecordingProviderHandler` pattern) spins up the real
  `http.server.ThreadingHTTPServer` from `pylib/remote_setup.py` against a
  fake upstream OmniRoute (same fake-HTTP-server technique already used for
  OmniRoute itself), exercising a full `/setup.sh` fetch and a full
  `/config` fetch (valid key, invalid key, master-key-unset) end to end.
- `tests/test_compose.py` gains cases for: the new `remote-setup` service
  appearing in rendered compose with the right image/port/volumes/command,
  OmniRoute's port binding changing to `0.0.0.0`, the new named volume, and
  `.env` values flowing into the service's `environment:` block (including
  the "no `.env` file present" case rendering an empty
  `OMNI_ROUTER_MASTER_KEY`).
- `tests/test_config.py` gains schema cases for `remote_setup.{image,port}`
  mirroring the existing `omniroute.{image,port}` cases.
- No live test against a real OmniRoute/dashboard-password round trip in
  CI — same boundary this repo already draws for `pylib/omniroute.py`
  (`tests/test_omniroute.py` mocks `urllib.request.urlopen` entirely).

## Explicitly out of scope for this design

- Per-machine scoped keys (one shared `llm-env-remote-agents` key is
  reused by every remote machine that runs the installer — simplest thing
  that works; revisit only if the owner wants per-machine revocation
  later).
- Any TLS/HTTPS for `remote-setup` or OmniRoute's newly-public port — both
  stay plain HTTP, consistent with this repo's existing "LAN-only, trusted
  network" posture (`llm-server` itself has never used TLS).
- Automatically firewalling/restricting the new `0.0.0.0` bindings —
  the owner has explicitly accepted the LAN-exposure trade-off.
- Building/publishing a custom container image for `remote-setup` — the
  stock `python:3.13-alpine` image plus a bind-mounted script is
  sufficient and avoids an image-build pipeline entirely.
- OpenCode's own `$XDG_STATE_HOME/opencode/model.json` (recent/favorite/
  variant model-cycling state) — this is per-installation runtime UI
  state, not provider/model configuration, and OpenCode regenerates it on
  its own on first use. `setup/setup-local-llm-agents.sh`'s version-pinned
  (`opencode --version` == `1.18.10`) creation path for it is deliberately
  not replicated remotely: doing so would make the installer hard-fail on
  any remote machine running a different OpenCode version, for a file
  OmniRoute connectivity does not depend on. The generated script does
  replicate the rest of `setup-local-llm-agents.sh`'s OpenCode handling
  faithfully (the multi-candidate target-detection algorithm).
