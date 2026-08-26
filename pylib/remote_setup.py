"""HTTP server for the remote-setup service.

Gates OmniRoute credentials behind OMNI_ROUTER_MASTER_KEY and serves a
self-contained installer script that configures Pi/OpenCode on a remote
machine to talk to OmniRoute. Stdlib-only, run as
`python3 -m pylib.remote_setup` inside the `remote-setup` compose service
-- see docs/superpowers/specs/2026-08-12-remote-agent-setup-design.md.

Reuses pylib.omniroute's session-login/request helpers rather than
reimplementing OmniRoute's dashboard-session auth a second time -- both
modules ship together in the same read-only pylib/ bind mount.
"""

from __future__ import annotations

import contextlib
import fcntl
import hmac
import http.server
import json
import os
import re
import signal
import tempfile
import threading
from pathlib import Path
from typing import Any

from pylib.omniroute import OmniRouteError, _login, _request, compute_combo_context

KEY_NAME = "llm-env-remote-agents"
CACHE_PATH = Path("/app/data/api-key.json")

# Mounted read-only by pylib/compose.py's remote-setup service (see Task
# 2) alongside pylib/ itself. NOT served over HTTP -- the design defines
# exactly two public routes (/setup.sh, /config); render_setup_script()
# below reads this file's content and embeds it into the /setup.sh
# response via a bash heredoc instead of serving it as its own route.
UPDATE_OPENCODE_CONFIG_PATH = Path("/app/setup/update-opencode-config.mjs")

# Mounted read-only by pylib/compose.py's remote-setup service (Task 3 of
# docs/superpowers/plans/2026-08-25-unify-agent-client-setup-scripts.md)
# alongside pylib/ and update-opencode-config.mjs. Not served over HTTP --
# embedded into /setup.sh's response via a bash heredoc, exactly like the
# JS updater.
INSTALL_AGENT_CLIENTS_LIB_PATH = Path("/app/setup/lib/install-agent-clients.sh")

# An HTTP Host header is an *authority*, and we interpolate it into a
# generated shell script and a JSON response -- so it is parsed, not just
# character-filtered. A character-class-only check ("are all these bytes
# harmless?") accepts strings that are not authorities at all, e.g. the
# unbracketed "::1:20130", which host_without_port() below would then
# reduce to the empty string and yield a nonsense "http://:20128".
#
# Exactly two forms are accepted, each with an optional ":<port>":
#   - a bracketed IPv6 literal: "[::1]", "[::1]:20130"
#   - a bare hostname / IPv4:   "llm.local", "192.168.1.5:20128"
# Everything else is rejected: bare (unbracketed) IPv6, trailing garbage
# after "]", an empty host, a non-numeric or out-of-range port, and any
# shell/JSON metacharacter (backticks, `$`, `;`, spaces, ...).
_BRACKETED_IPV6_AUTHORITY_RE = re.compile(
    # Hex groups, colons and the dots of an IPv4-mapped tail, plus RFC 6874's
    # percent-encoded zone id ("%25eth0").
    r"^(\[[0-9A-Fa-f:.]+(?:%25[A-Za-z0-9._~-]+)?\])(?::(\d{1,5}))?$"
)
_HOST_AUTHORITY_RE = re.compile(r"^([A-Za-z0-9.-]+)(?::(\d{1,5}))?$")

# render_setup_script() builds the final script via plain string
# replacement (not str.format()) specifically so the embedded
# update-opencode-config.mjs source -- which is full of literal `{`/`}`
# JS syntax -- never has to be escaped for a format mini-language it was
# never written for.
_HOST_PLACEHOLDER = "@@LLM_ENV_HOST@@"
_UPDATER_JS_PLACEHOLDER = "@@OPENCODE_UPDATER_JS@@"
_INSTALL_LIB_PLACEHOLDER = "@@INSTALL_LIB_SH@@"

SETUP_SCRIPT_TEMPLATE = r'''#!/usr/bin/env bash
set -euo pipefail
umask 077

LLM_ENV_HOST="@@LLM_ENV_HOST@@"

for cmd in curl jq node mktemp cmp; do
    command -v "$cmd" >/dev/null 2>&1 || {
        echo "remote-setup: missing required command: $cmd" >&2
        exit 1
    }
done

echo "close Pi and OpenCode before continuing" >&2

# install-agent-clients.sh's own source is embedded here (not fetched over
# HTTP -- the design defines only /setup.sh and /config as public routes),
# exactly like update-opencode-config.mjs below. It must be materialized
# and sourced BEFORE create_agent_client_workdir can be called (that
# function is defined by sourcing it) -- so it is staged to its own
# mktemp'd file rather than into $workdir, which does not exist yet.
lib="$(mktemp)" || {
    echo "remote-setup: could not stage install-agent-clients.sh" >&2
    exit 1
}
chmod 600 "$lib" || {
    rm -f -- "$lib"
    echo "remote-setup: could not secure staged install-agent-clients.sh" >&2
    exit 1
}
cat >"$lib" <<'INSTALL_LIB_EOF'
@@INSTALL_LIB_SH@@
INSTALL_LIB_EOF
# shellcheck disable=SC1090 # heredoc-written above, not a static path
source "$lib"

create_agent_client_workdir
# create_agent_client_workdir's `trap _iac_cleanup EXIT` (just installed)
# owns cleanup for $workdir and everything staged into $staged_files by
# install_agent_clients() below -- and that function's own mv/shift
# bookkeeping (see setup/lib/install-agent-clients.sh's trailing
# mv/`staged_files=("${staged_files[@]:1})` block) assumes staged_files[0]
# is always the file *just* moved. Pushing $lib onto that array here would
# violate that assumption: $lib (at index 0) gets shifted off after the
# FIRST unrelated mv, not after $lib is actually removed anywhere -- so
# $lib leaks on every successful run. Since setup/lib/install-agent-clients.sh
# must not be modified for this, $lib is kept out of $staged_files entirely
# and instead cleaned up by replacing the trap wholesale (bash traps do not
# chain): remove $lib first, restore the original exit status, then
# delegate to _iac_cleanup -- a plain global function once
# create_agent_client_workdir has run -- for everything else. This covers
# every exit path: success, an _iac_die failure inside install_agent_clients,
# and any earlier failure once this trap is installed.
#
# The whole script runs under `set -e`, which also applies inside an
# already-firing EXIT trap: a bare `(exit "$lib_exit_status")` used to
# restore $? for _iac_cleanup would itself be a "failing" command whenever
# the original status was non-zero, aborting the trap right there and
# skipping _iac_cleanup entirely -- leaking $workdir (which can hold the
# staged API key and provider files) on every failure path instead of just
# leaking $lib on the success path. `set +e` around that one restore-$?
# step is what keeps that failing exit status from also killing the trap.
# shellcheck disable=SC2154 # _iac_cleanup is defined by create_agent_client_workdir above
trap 'lib_exit_status=$?; rm -f -- "$lib"; set +e; (exit "$lib_exit_status"); _iac_cleanup' EXIT

# The updater's own source is embedded here too, same reasoning as above. A
# quoted heredoc delimiter means the shell does no expansion inside the JS
# body, so its own `$`/backtick usage is inert.
updater="${workdir}/update-opencode-config.mjs"
cat >"$updater" <<'OPENCODE_UPDATER_EOF'
@@OPENCODE_UPDATER_JS@@
OPENCODE_UPDATER_EOF

rm_key=0
for arg in "${@-}"; do
    [ -z "$arg" ] && continue
    case "$arg" in
        --rm-key) rm_key=1 ;;
        *)
            echo "remote-setup: unknown argument: $arg" >&2
            exit 1
            ;;
    esac
done

master_key_cache_dir="${XDG_CACHE_HOME:-$HOME/.cache}/llm-env"
master_key_cache="${master_key_cache_dir}/master-key"

# `curl ... | bash` leaves stdin attached to the script pipe, not the
# terminal -- `read` must go to /dev/tty explicitly or the prompt below
# silently reads EOF instead of actually asking the user.
if [ "$rm_key" -eq 1 ]; then
    rm -f -- "$master_key_cache"
    read -r -s -p "OMNI_ROUTER_MASTER_KEY: " master_key < /dev/tty
    echo
elif [ -f "$master_key_cache" ] && \
     [ "$(find "$master_key_cache" -mtime -7 2>/dev/null)" = "$master_key_cache" ]; then
    master_key="$(cat "$master_key_cache")"
else
    read -r -s -p "OMNI_ROUTER_MASTER_KEY: " master_key < /dev/tty
    echo
    mkdir -p "$master_key_cache_dir"
    chmod 700 "$master_key_cache_dir"
    printf '%s' "$master_key" >"$master_key_cache"
    chmod 600 "$master_key_cache"
fi

# A bearer token on the curl command line would be visible to every other
# local user via `ps`/`/proc/<pid>/cmdline` for as long as curl runs --
# mirrors scripts/check-server.sh's own auth_conf pattern (a private,
# mode-0600 curl config file passed via -K, never -H on the command line).
auth_conf="${workdir}/auth.conf"
printf 'header = "Authorization: Bearer %s"\n' "$master_key" >"$auth_conf"
chmod 600 "$auth_conf"

response_file="${workdir}/config-response.json"
http_status="$(curl -sS -K "$auth_conf" -o "$response_file" -w '%{http_code}' \
    "http://${LLM_ENV_HOST}/config")" || {
    echo "remote-setup: could not reach http://${LLM_ENV_HOST}/config" >&2
    exit 1
}
if [ "$http_status" != "200" ]; then
    error="$(jq -r '.error // "request failed"' "$response_file" 2>/dev/null || echo "request failed")"
    echo "remote-setup: ${error} (HTTP ${http_status})" >&2
    exit 1
fi

config_json="$(cat "$response_file")"
omniroute_dashboard_url="$(printf '%s' "$config_json" | jq -r '.omniroute_base_url')"
base_url="${omniroute_dashboard_url}/v1"
api_key="$(printf '%s' "$config_json" | jq -r '.api_key')"
omniroute_dashboard_password="$(printf '%s' "$config_json" | jq -r '.omniroute_dashboard_password')"
models_json="$(printf '%s' "$config_json" | jq -c '.models')"

api_key_file="${workdir}/api-key"
printf '%s' "$api_key" >"$api_key_file"
chmod 600 "$api_key_file"

# Every jq invocation below reads its JSON inputs from files (--rawfile /
# --slurpfile), never from `--argjson "$..."`: a command-line argument is
# readable by any other local user through /proc/<pid>/cmdline for as long
# as jq runs, and the provider documents built here embed the scoped API
# key. `umask 077` at the top of the script means each of these files is
# created private; the explicit chmod is belt-and-braces.
models_file="${workdir}/models.json"
printf '%s' "$models_json" >"$models_file"
chmod 600 "$models_file"

install_agent_clients "$base_url" "$api_key_file" "$models_file" "$updater" "$lib" "false"

echo
echo "OmniRoute dashboard: ${omniroute_dashboard_url}"
echo "  Password: ${omniroute_dashboard_password}"
echo "  (this is the OmniRoute ADMIN password -- full access to add/remove"
echo "  provider connections and revoke API keys, not just chat access."
echo "  Keep it as private as the master key.)"
'''


def parse_bearer_token(header_value: str | None) -> str | None:
    if not header_value or not header_value.startswith("Bearer "):
        return None
    token = header_value[len("Bearer "):]
    return token or None


def master_key_matches(provided: str, expected: str) -> bool:
    return hmac.compare_digest(provided, expected)


def read_cached_key(cache_path: Path) -> dict[str, str] | None:
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    if (
        isinstance(data, dict)
        and isinstance(data.get("id"), str)
        and isinstance(data.get("key"), str)
    ):
        return {"id": data["id"], "key": data["key"]}
    return None


def write_cached_key(cache_path: Path, key_id: str, key_value: str) -> None:
    """Atomically replace the cache file so a crash mid-write can never
    leave a truncated/corrupt cache: write to a private temp file in the
    same directory, then os.replace() it into place (same-filesystem
    rename is atomic on POSIX)."""
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(cache_path.parent), prefix=".api-key-", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps({"id": key_id, "key": key_value}))
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, cache_path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


# Guards the read-check-create-write sequence in ensure_api_key() below
# against concurrent THREADS. ThreadingHTTPServer dispatches each request
# on its own thread, so two /config requests arriving close together (e.g.
# two remote machines curling /setup.sh | bash at nearly the same time on
# a cold cache) could otherwise both observe "no cached key" and each mint
# a new OmniRoute API key.
#
# This is not sufficient on its own: ensure_api_key() is also called from
# entirely separate PROCESSES (`llmenv omniroute issue-key`), which share
# no Python objects. _cache_file_lock() below adds the cross-process half.
# Both are kept, because neither subsumes the other -- fcntl.flock() locks
# are held per open file description, so two threads in one process each
# opening the lock file get independent descriptions and do NOT block each
# other (and if they shared one descriptor, the second flock() would
# succeed immediately by upgrading the lock the process already holds).
_key_issuance_lock = threading.Lock()


@contextlib.contextmanager
def _cache_file_lock(cache_path: Path):
    """Serialize key issuance across processes with an exclusive
    fcntl.flock() on a stable sibling lock file.

    The lock lives beside the cache, never ON it: write_cached_key()
    publishes the cache with os.replace(), so the cache path's inode
    changes and a lock taken on the old inode would guard nothing.
    """
    lock_path = cache_path.with_name(cache_path.name + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _extract_keys(payload: Any) -> list[Any]:
    """Tolerate either shape GET /api/keys might return, mirroring
    pylib.omniroute._extract_providers: a bare list, or a dict nesting the
    list under a plausible key. An unrecognized shape MUST raise rather
    than degrade to `[]` -- a silent empty listing would make the cached
    key look permanently revoked and mint a brand new OmniRoute API key on
    every single /config request, forever, with no error anywhere."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("keys", "apiKeys", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
    # Describe only the type/shape, never the payload itself -- it may
    # carry key material, and this message reaches stderr/the container log.
    shape = (
        f"dict with keys {sorted(payload.keys())!r}"
        if isinstance(payload, dict)
        else type(payload).__name__
    )
    raise OmniRouteError(f"unexpected API key listing shape: {shape}")


def ensure_api_key(
    base_url: str,
    dashboard_password: str,
    cache_path: Path,
    key_name: str = KEY_NAME,
) -> str:
    """Reused as-is by Task 5's `llmenv.py omniroute issue-key` (a
    different key_name/cache_path -- "llm-env-local-agents" -- so a local
    key is never confused with the shared remote-installer key in
    OmniRoute's own dashboard listing)."""
    with _key_issuance_lock, _cache_file_lock(cache_path):
        session_token = _login(base_url, dashboard_password)
        cached = read_cached_key(cache_path)
        if cached is not None:
            listing = _request("GET", f"{base_url}/api/keys", session_token)
            keys = _extract_keys(listing)
            if any(isinstance(k, dict) and k.get("id") == cached["id"] for k in keys):
                return cached["key"]
        created = _request(
            "POST", f"{base_url}/api/keys", session_token, {"name": key_name}
        )
        if (
            not isinstance(created, dict)
            or not isinstance(created.get("id"), str)
            or not isinstance(created.get("key"), str)
        ):
            raise OmniRouteError("key creation returned no usable id/key")
        write_cached_key(cache_path, created["id"], created["key"])
        return created["key"]


def parse_host_header(host_header: str) -> tuple[str, str | None] | None:
    """Parse an HTTP Host header into (host, port), or None if it is not a
    well-formed authority. `host` keeps the brackets of an IPv6 literal --
    they are required when it is put back into a URL. See the module-level
    regex comment for the exact grammar accepted."""
    if not host_header:
        return None
    match = _BRACKETED_IPV6_AUTHORITY_RE.match(
        host_header
    ) or _HOST_AUTHORITY_RE.match(host_header)
    if match is None:
        return None
    host, port = match.group(1), match.group(2)
    if port is not None and not 1 <= int(port) <= 65535:
        return None
    return host, port


def host_without_port(host_header: str) -> str:
    """Strip a trailing :port. A bracketed IPv6 literal (e.g. "[::1]:20130")
    has colons INSIDE the brackets that must survive, so this delegates to
    the same parse validate_host_header() uses rather than guessing with
    split(":"). Callers must have validated the header first; an
    unparseable value returns "" instead of a plausible-looking wrong
    answer."""
    parsed = parse_host_header(host_header)
    return parsed[0] if parsed is not None else ""


def validate_host_header(host_header: str) -> bool:
    """Reject a Host header before it's interpolated into either the
    generated shell script or the JSON /config response -- an attacker
    who controls the Host header (trivial over plain HTTP) could
    otherwise inject shell syntax into /setup.sh's output, or a malformed
    authority could produce a broken base URL in /config."""
    return parse_host_header(host_header) is not None


def _build_unified_models(
    llm_server_models: list[dict[str, Any]], combos: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Build the client-visible model list SETUP_SCRIPT_TEMPLATE consumes
    verbatim (no further prefixing/correction happens in bash).

    Mirrors setup/setup-local-llm-agents.sh's own equivalent jq construction
    -- keep both in sync if this shape changes. Each entry:
        {id, label, ctx_size, client_max_output_tokens}
    - llm-server models: id="llama-cpp/<alias>" (only ever non-empty when
      llm_server.enabled is true -- see pylib/compose.py's enabled_models).
    - OmniRoute combos: id=<combo name> (OmniRoute routes combos by their
      bare name, not a provider-prefixed id, confirmed live), always
      included regardless of llm_server.enabled -- combos route to real,
      live provider connections and are the point of remote-setup's
      OmniRoute-only mode. ctx_size uses the corrected minimum
      (compute_combo_context) since OmniRoute's own /v1/models and
      /api/combos do not reflect manual context-window overrides (see
      compute_combo_context's docstring). A combo with no known context
      window across any member is skipped entirely rather than mapped with
      a fabricated limit.
    """
    models = [
        {
            "id": f"llama-cpp/{model['alias']}",
            "label": model["alias"],
            "ctx_size": model["ctx_size"],
            "client_max_output_tokens": model["client_max_output_tokens"],
        }
        for model in llm_server_models
    ]
    for combo in combos:
        ctx_size = combo.get("min_context_window")
        if not isinstance(ctx_size, int):
            continue
        max_output = combo.get("min_max_output_tokens")
        if not isinstance(max_output, int):
            client_max_output_tokens = min(ctx_size, 128000)
        elif max_output > ctx_size:
            client_max_output_tokens = ctx_size
        else:
            client_max_output_tokens = max_output
        models.append(
            {
                "id": combo["combo"],
                "label": f"{combo['combo']} (combo)",
                "ctx_size": ctx_size,
                "client_max_output_tokens": client_max_output_tokens,
            }
        )
    return models


def build_config_response(
    *,
    host: str,
    omniroute_port: str,
    api_key: str,
    omniroute_dashboard_password: str,
    models: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "omniroute_base_url": f"http://{host}:{omniroute_port}",
        "api_key": api_key,
        # Deliberately included (not the scoped-key-only design this
        # endpoint started with): the operator gates this endpoint behind
        # OMNI_ROUTER_MASTER_KEY and wants remote machines able to reach
        # the OmniRoute admin dashboard without copying the full random
        # password by hand. Anyone who can call /config already has admin
        # reach through this value -- treat it as sensitive as the master
        # key itself.
        "omniroute_dashboard_password": omniroute_dashboard_password,
        "models": models,
    }


def render_setup_script(host: str) -> str:
    updater_source = UPDATE_OPENCODE_CONFIG_PATH.read_text(encoding="utf-8")
    lib_source = INSTALL_AGENT_CLIENTS_LIB_PATH.read_text(encoding="utf-8")
    script = SETUP_SCRIPT_TEMPLATE.replace(_HOST_PLACEHOLDER, host)
    script = script.replace(_UPDATER_JS_PLACEHOLDER, updater_source)
    # lib_source already ends in "\n"; the template puts the
    # @@INSTALL_LIB_SH@@ placeholder on its own heredoc line followed by
    # the template's own newline before INSTALL_LIB_EOF. Substituting the
    # file's content verbatim would therefore leave one extra trailing
    # blank line in the materialized $lib file compared to the real repo
    # file, so sha256sum "$lib" on the remote side would never match
    # sha256sum on the local file -- defeating the "Installer version"
    # hash line's whole purpose as a local/remote staleness diagnostic.
    return script.replace(_INSTALL_LIB_PLACEHOLDER, lib_source.rstrip("\n"))


class RemoteSetupHandler(http.server.BaseHTTPRequestHandler):
    # socketserver.BaseRequestHandler honors a `timeout` attribute by
    # calling self.connection.settimeout(self.timeout) before handle():
    # without it a LAN client that opens a connection and never sends a
    # request line pins one server thread forever. The design accepted LAN
    # exposure, not unbounded resource consumption.
    timeout = 10

    # log_message() is not told the status it is reporting, so remember
    # what send_response() last emitted and gate the log on that.
    _last_status: int | None = None

    def send_response(self, code, message=None):
        self._last_status = code
        super().send_response(code, message)

    def _write_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _write_text(self, status: int, content_type: str, body_text: str) -> None:
        body = body_text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        # Any unexpected exception below (a malformed env var, a missing
        # mounted file, ...) must still produce a JSON response -- letting
        # it escape do_GET would abort the request without ever writing a
        # response, leaving the client's connection to hang/reset instead
        # of getting a clear error.
        try:
            self._route()
        except Exception as exc:  # noqa: BLE001 -- deliberate catch-all, see above
            self._write_json(500, {"error": f"internal error: {exc}"})

    def _route(self):
        # Exactly two public routes, per the design: /setup.sh and
        # /config. update-opencode-config.mjs is embedded into /setup.sh's
        # response instead of being served at its own path (see
        # render_setup_script() / UPDATE_OPENCODE_CONFIG_PATH above).
        if self.path == "/setup.sh":
            host = self.headers.get("Host", "")
            if not validate_host_header(host):
                self._write_json(400, {"error": "invalid or missing Host header"})
                return
            self._write_text(200, "text/x-shellscript", render_setup_script(host))
            return
        if self.path == "/config":
            self._handle_config()
            return
        self._write_json(404, {"error": "not found"})

    def _handle_config(self):
        expected = os.environ.get("OMNI_ROUTER_MASTER_KEY", "")
        if not expected:
            self._write_json(
                503,
                {"error": "remote setup not configured -- set OMNI_ROUTER_MASTER_KEY in .env and restart"},
            )
            return
        token = parse_bearer_token(self.headers.get("Authorization"))
        if token is None or not master_key_matches(token, expected):
            self._write_json(401, {"error": "invalid or missing master key"})
            return

        host_header = self.headers.get("Host", "")
        if not validate_host_header(host_header):
            self._write_json(400, {"error": "invalid or missing Host header"})
            return

        base_url = os.environ.get("OMNIROUTE_INTERNAL_URL", "")
        dashboard_password = os.environ.get("OMNIROUTE_DASHBOARD_PASSWORD", "")
        try:
            api_key = ensure_api_key(base_url, dashboard_password, CACHE_PATH)
        except OmniRouteError as exc:
            self._write_json(502, {"error": f"could not reach OmniRoute: {exc}"})
            return
        try:
            combos = compute_combo_context(base_url, dashboard_password)
        except OmniRouteError as exc:
            self._write_json(502, {"error": f"could not fetch OmniRoute combos: {exc}"})
            return

        host = host_without_port(host_header)
        omniroute_port = os.environ.get("OMNIROUTE_PORT", "")
        llm_server_models = json.loads(os.environ.get("MODELS_JSON", "[]"))
        models = _build_unified_models(llm_server_models, combos)
        response = build_config_response(
            host=host,
            omniroute_port=omniroute_port,
            api_key=api_key,
            omniroute_dashboard_password=dashboard_password,
            models=models,
        )
        self._write_json(200, response)

    def log_message(self, format_string, *args):
        """Log only what an operator would need to see in
        `podman logs remote-setup`: failed master-key attempts (401),
        OmniRoute outages (502), misconfiguration (503) and internal
        errors (500). Successful 200s on a low-traffic installer endpoint
        are pure noise, so they stay quiet. Same message shape as the base
        class -- the status gate is the only change."""
        status = self._last_status
        if status is not None and 200 <= status < 300:
            return
        super().log_message(format_string, *args)

    def log_error(self, format_string, *args):
        """Always logged, never gated: log_error() fires for problems the
        base class handles before any send_response() ran (a timed-out or
        malformed request line), so self._last_status would still hold the
        PREVIOUS request's status on a kept-alive connection."""
        http.server.BaseHTTPRequestHandler.log_message(self, format_string, *args)


def install_termination_handlers(server: http.server.HTTPServer) -> None:
    """Make SIGTERM/SIGINT actually stop `server`.

    main() runs this process as the container's command directly (no
    init/tini wrapper) -- as PID 1 inside its own PID namespace, Linux
    does NOT apply the normal "terminate on SIGTERM" default for signals
    with no explicit handler; unhandled SIGTERM/SIGINT are silently
    ignored. Without this, `podman stop`/`podman compose down` never
    terminate the process, block until their own timeout, then have to
    SIGKILL -- which can leave the container wedged in a "Stopping" state
    that refuses the next `up -d` ("container state improper") until it's
    force-killed by hand. shutdown() runs on a separate thread because it
    must not be called from the signal handler itself while
    serve_forever()'s loop may be holding the lock shutdown() waits on.
    """

    def _handle_termination(signum, _frame):
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, _handle_termination)
    signal.signal(signal.SIGINT, _handle_termination)


def main() -> None:
    port = int(os.environ.get("REMOTE_SETUP_PORT", "20130"))
    server = http.server.ThreadingHTTPServer(("0.0.0.0", port), RemoteSetupHandler)
    install_termination_handlers(server)
    server.serve_forever()


if __name__ == "__main__":
    main()
