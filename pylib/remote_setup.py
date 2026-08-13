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

import hmac
import http.server
import json
import os
import re
import tempfile
import threading
from pathlib import Path
from typing import Any

from pylib.omniroute import OmniRouteError, _login, _request

KEY_NAME = "llm-env-remote-agents"
CACHE_PATH = Path("/app/data/api-key.json")

# Mounted read-only by pylib/compose.py's remote-setup service (see Task
# 2) alongside pylib/ itself. NOT served over HTTP -- the design defines
# exactly two public routes (/setup.sh, /config); render_setup_script()
# below reads this file's content and embeds it into the /setup.sh
# response via a bash heredoc instead of serving it as its own route.
UPDATE_OPENCODE_CONFIG_PATH = Path("/app/setup/update-opencode-config.mjs")

# Authority characters valid in an HTTP Host header we're willing to
# interpolate into a generated shell script or a JSON response: DNS
# labels, dots, a port-separator colon, and the brackets a literal IPv6
# address uses. Rejects anything else (backticks, `$`, `;`, spaces, ...)
# so a hostile Host header can't inject shell/JSON content.
_HOST_HEADER_RE = re.compile(r"^[A-Za-z0-9.:\[\]-]+$")

# render_setup_script() builds the final script via plain string
# replacement (not str.format()) specifically so the embedded
# update-opencode-config.mjs source -- which is full of literal `{`/`}`
# JS syntax -- never has to be escaped for a format mini-language it was
# never written for.
_HOST_PLACEHOLDER = "@@LLM_ENV_HOST@@"
_UPDATER_JS_PLACEHOLDER = "@@OPENCODE_UPDATER_JS@@"

SETUP_SCRIPT_TEMPLATE = r'''#!/usr/bin/env bash
set -euo pipefail
umask 077

LLM_ENV_HOST="@@LLM_ENV_HOST@@"

for cmd in curl jq node mktemp; do
    command -v "$cmd" >/dev/null 2>&1 || {
        echo "remote-setup: missing required command: $cmd" >&2
        exit 1
    }
done

workdir="$(mktemp -d)" || {
    echo "remote-setup: could not create private configuration workspace" >&2
    exit 1
}
chmod 700 "$workdir" || {
    rm -rf -- "$workdir"
    echo "remote-setup: could not secure private configuration workspace" >&2
    exit 1
}
staged_files=()
cleanup() {
    local status=$?
    local path
    for path in "${staged_files[@]}"; do
        [ -n "$path" ] && rm -f -- "$path"
    done
    rm -rf -- "$workdir"
    exit "$status"
}
trap cleanup EXIT

# `curl ... | bash` leaves stdin attached to the script pipe, not the
# terminal -- `read` must go to /dev/tty explicitly or the prompt below
# silently reads EOF instead of actually asking the user.
read -r -s -p "OMNI_ROUTER_MASTER_KEY: " master_key < /dev/tty
echo

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
base_url="$(printf '%s' "$config_json" | jq -r '.omniroute_base_url')/v1"
api_key="$(printf '%s' "$config_json" | jq -r '.api_key')"
models_json="$(printf '%s' "$config_json" | jq -c '.models')"

api_key_file="${workdir}/api-key"
printf '%s' "$api_key" >"$api_key_file"
chmod 600 "$api_key_file"

pi_dir="${PI_CODING_AGENT_DIR:-$HOME/.pi/agent}"
pi_path="${pi_dir}/models.json"
pi_settings_path="${pi_dir}/settings.json"
mkdir -p "$pi_dir"
chmod 700 "$pi_dir"

# Same candidate-file detection setup-local-llm-agents.sh uses
# (setup/setup-local-llm-agents.sh:149-231): every candidate that already
# contains the "local-llm-env" provider is updated (a machine can have more
# than one OpenCode config file); if none contains it yet, fall back to the
# highest-priority *existing* candidate (checked in reverse,
# opencode.jsonc -> opencode.json -> config.json, first hit wins); if none
# exists at all, default to creating opencode.jsonc fresh. Preserves an
# existing remote machine's own OpenCode config filename(s) instead of
# always replacing them with a second, out-of-sync opencode.jsonc.
opencode_dir="${XDG_CONFIG_HOME:-$HOME/.config}/opencode"
mkdir -p "$opencode_dir"
chmod 700 "$opencode_dir"
opencode_candidates=(
    "${opencode_dir}/config.json"
    "${opencode_dir}/opencode.json"
    "${opencode_dir}/opencode.jsonc"
)

# The updater's own source is embedded here (not fetched over HTTP -- the
# design defines only /setup.sh and /config as public routes). A quoted
# heredoc delimiter means the shell does no expansion inside the JS body,
# so its own `$`/backtick usage is inert.
updater="${workdir}/update-opencode-config.mjs"
cat >"$updater" <<'OPENCODE_UPDATER_EOF'
@@OPENCODE_UPDATER_JS@@
OPENCODE_UPDATER_EOF

opencode_targets=()
opencode_sources=()
for candidate in "${opencode_candidates[@]}"; do
    [ -e "$candidate" ] || continue
    [ -f "$candidate" ] || {
        echo "remote-setup: OpenCode configuration is not a regular file: ${candidate}" >&2
        exit 1
    }
    if node "$updater" --contains-provider "$candidate"; then
        contains_status=0
    else
        contains_status=$?
    fi
    case "$contains_status" in
        0)
            opencode_targets+=("$candidate")
            opencode_sources+=("$candidate")
            ;;
        1) ;;
        *)
            echo "remote-setup: could not validate OpenCode configuration: ${candidate}" >&2
            exit 1
            ;;
    esac
done
if [ "${#opencode_targets[@]}" -eq 0 ]; then
    for candidate in "${opencode_candidates[2]}" "${opencode_candidates[1]}" "${opencode_candidates[0]}"; do
        if [ -e "$candidate" ]; then
            opencode_targets+=("$candidate")
            opencode_sources+=("$candidate")
            break
        fi
    done
fi
if [ "${#opencode_targets[@]}" -eq 0 ]; then
    printf '{}\n' >"${workdir}/empty-opencode.jsonc"
    opencode_targets+=("${opencode_candidates[2]}")
    opencode_sources+=("${workdir}/empty-opencode.jsonc")
fi

# OpenCode's own recent/favorite/variant model-cycling state
# ($XDG_STATE_HOME/opencode/model.json) -- out of scope for this installer.
# It is per-installation runtime UI state, not provider/model
# configuration: OpenCode regenerates it on its own on first use, and
# replicating setup-local-llm-agents.sh's version-pinned
# (`opencode --version` == 1.18.10) creation path here would make the
# remote installer hard-fail on any machine running a different OpenCode
# version, for a file that isn't required for OmniRoute connectivity to
# work. See docs/superpowers/specs/2026-08-12-remote-agent-setup-design.md,
# "Explicitly out of scope".

# Model ids sent TO OmniRoute must be "llama-cpp/<alias>" -- OmniRoute
# routes on the provider slug, not the connection's own name (confirmed
# live; see scripts/check-server.sh's own OmniRoute completion check,
# which uses the identical "llama-cpp/${alias}" convention).
pi_provider_json="$(jq -n --arg base_url "$base_url" --rawfile api_key "$api_key_file" \
    --argjson models "$models_json" \
    '{baseUrl: $base_url, api: "openai-completions", apiKey: ($api_key | rtrimstr("\n")),
      compat: {supportsDeveloperRole: false, supportsReasoningEffort: false},
      models: [$models[] | {id: "llama-cpp/\(.alias)", contextWindow: .ctx_size, maxTokens: .client_max_output_tokens}]}')"

pi_source="$pi_path"
if [ ! -e "$pi_path" ]; then
    printf '{}\n' >"${workdir}/empty-pi.json"
    pi_source="${workdir}/empty-pi.json"
fi
pi_staged="$(mktemp "${pi_dir}/.models.json.XXXXXX")"
chmod 600 "$pi_staged"
staged_files+=("$pi_staged")
jq --argjson provider "$pi_provider_json" \
   '.providers = ((.providers // {}) + {"local-llm-env": $provider})' \
   "$pi_source" >"$pi_staged" || {
    echo "remote-setup: could not update Pi configuration" >&2
    exit 1
}

pi_settings_source="$pi_settings_path"
if [ ! -e "$pi_settings_path" ]; then
    printf '{}\n' >"${workdir}/empty-pi-settings.json"
    pi_settings_source="${workdir}/empty-pi-settings.json"
fi
pi_settings_staged="$(mktemp "${pi_dir}/.settings.json.XXXXXX")"
chmod 600 "$pi_settings_staged"
staged_files+=("$pi_settings_staged")
jq --argjson models "$models_json" \
   '.enabledModels = [$models[] | "local-llm-env/llama-cpp/\(.alias)"]' \
   "$pi_settings_source" >"$pi_settings_staged" || {
    echo "remote-setup: could not update Pi settings" >&2
    exit 1
}

opencode_provider_json="$(jq -n --arg base_url "$base_url" --rawfile api_key "$api_key_file" \
    --argjson models "$models_json" \
    '{npm: "@ai-sdk/openai-compatible", name: "local-llm-env",
      options: {baseURL: $base_url, apiKey: ($api_key | rtrimstr("\n"))},
      models: (reduce $models[] as $model ({};
          .["llama-cpp/\($model.alias)"] = {name: $model.alias,
              limit: {context: $model.ctx_size, output: $model.client_max_output_tokens}}))}')"
provider_file="${workdir}/opencode-provider.json"
printf '%s' "$opencode_provider_json" >"$provider_file"
chmod 600 "$provider_file"

opencode_staged=()
for index in "${!opencode_targets[@]}"; do
    opencode_target="${opencode_targets[$index]}"
    opencode_source="${opencode_sources[$index]}"
    staged="$(mktemp "${opencode_dir}/.$(basename "$opencode_target").XXXXXX")"
    chmod 600 "$staged"
    staged_files+=("$staged")
    opencode_staged+=("$staged")
    node "$updater" --replace-provider "$opencode_source" "$provider_file" "$staged" || {
        echo "remote-setup: could not update OpenCode configuration: ${opencode_target}" >&2
        exit 1
    }
done

# All targets -- Pi's models.json, Pi's settings.json, and every detected/
# created OpenCode config -- are now staged in full. Only past this point
# does anything get moved into place, so a failure in any of the jq/node
# steps above (each already an explicit `exit 1` on error) leaves every
# existing file on disk completely untouched, never a partial mix of "Pi
# updated, OpenCode not" or vice versa.
mv -f -- "$pi_staged" "$pi_path"
staged_files=("${staged_files[@]:1}")
mv -f -- "$pi_settings_staged" "$pi_settings_path"
staged_files=("${staged_files[@]:1}")
for index in "${!opencode_targets[@]}"; do
    mv -f -- "${opencode_staged[$index]}" "${opencode_targets[$index]}"
    staged_files=("${staged_files[@]:1}")
done
echo "Pi configured: ${pi_path}"
for opencode_target in "${opencode_targets[@]}"; do
    echo "OpenCode configured: ${opencode_target}"
done
echo "Done. Model(s): $(printf '%s' "$models_json" | jq -r '[.[].alias] | join(", ")')"
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


# Guards the read-check-create-write sequence in ensure_api_key() below.
# ThreadingHTTPServer dispatches each request on its own thread, so two
# /config requests arriving close together (e.g. two remote machines
# curling /setup.sh | bash at nearly the same time on a cold cache) could
# otherwise both observe "no cached key" and each mint a new OmniRoute
# API key.
_key_issuance_lock = threading.Lock()


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
    with _key_issuance_lock:
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


def host_without_port(host_header: str) -> str:
    """Strip a trailing :port. A bracketed IPv6 literal (e.g. "[::1]:20130")
    has colons INSIDE the brackets that must survive -- only a colon after
    the closing bracket (or, for a bare non-bracketed host, the first
    colon) separates the port."""
    if host_header.startswith("["):
        end = host_header.find("]")
        if end != -1:
            return host_header[: end + 1]
        return host_header
    return host_header.split(":")[0]


def validate_host_header(host_header: str) -> bool:
    """Reject a Host header before it's interpolated into either the
    generated shell script or the JSON /config response -- an attacker
    who controls the Host header (trivial over plain HTTP) could
    otherwise inject shell syntax into /setup.sh's output."""
    return bool(host_header) and bool(_HOST_HEADER_RE.match(host_header))


def build_config_response(
    *, host: str, omniroute_port: str, api_key: str, models: list[dict[str, Any]]
) -> dict[str, Any]:
    return {
        "omniroute_base_url": f"http://{host}:{omniroute_port}",
        "api_key": api_key,
        "models": models,
    }


def render_setup_script(host: str) -> str:
    updater_source = UPDATE_OPENCODE_CONFIG_PATH.read_text(encoding="utf-8")
    script = SETUP_SCRIPT_TEMPLATE.replace(_HOST_PLACEHOLDER, host)
    return script.replace(_UPDATER_JS_PLACEHOLDER, updater_source)


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

        host = host_without_port(host_header)
        omniroute_port = os.environ.get("OMNIROUTE_PORT", "")
        models = json.loads(os.environ.get("MODELS_JSON", "[]"))
        response = build_config_response(
            host=host, omniroute_port=omniroute_port, api_key=api_key, models=models
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


def main() -> None:
    port = int(os.environ.get("REMOTE_SETUP_PORT", "20130"))
    server = http.server.ThreadingHTTPServer(("0.0.0.0", port), RemoteSetupHandler)
    server.serve_forever()


if __name__ == "__main__":
    main()
