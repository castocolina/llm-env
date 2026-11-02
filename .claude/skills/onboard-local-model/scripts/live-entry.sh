#!/usr/bin/env bash
# live-entry.sh — the gated live models.yml edits of onboarding (7a.2) and a quant comparison (P0), after a backup.
# usage: live-entry.sh add --from SCRATCH --alias A [--as LIVE]   (env: LLM_ENV_CONFIG)
#        live-entry.sh set-url --alias A --url URL
# add: appends entry A of SCRATCH to the live config as LIVE (default A), with enabled: false and no benchmark.
#      Prints `backup: <path>`, then `added: LIVE`.
# set-url: sets live entry A's url to URL. Prints `backup: <path>`, then `url: A`.
# Both back the live config up to <config>.pre-entry-<stamp>.bak or <config>.pre-url-<stamp>.bak (mode 0600), validate the
# edited copy with `llmenv migrate-config` and rename it atomically (edit_config_atomically).
# Exit 1, live file untouched: no entry A in SCRATCH; LIVE already a live alias; A's file already the file of a live entry
# (use that alias); set-url on an alias that is not live; a failed migrate-config (its backup stays).
# Exit 2: usage; LIVE = candidate; a URL not of the form https://huggingface.co/<owner>/<repo>/resolve/<40-hex sha>/<path>,
# or whose base name is not A's file. set-url checks the URL's form, then that A is live, then the base name.
set -euo pipefail

usage() { sed -n '2,/^set -/{/^set -/!p}' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }
# `action`, not `cmd`: require_cmd in tools/lib.sh loops with a non-local cmd.
action="${1:-}"
case "$action" in
-h|--help) usage; exit 0 ;;
add|set-url) shift ;;
*) echo "usage: live-entry.sh add|set-url ... (see --help)" >&2; exit 2 ;;
esac
scratch=""; alias=""; live=""; url=""
while [ $# -gt 0 ]; do
    case "$1" in
    --from) scratch="${2:-}"; shift 2 ;;
    --alias) alias="${2:-}"; shift 2 ;;
    --as) live="${2:-}"; shift 2 ;;
    --url) url="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown flag: $1" >&2; exit 2 ;;
    esac
done

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source-path=SCRIPTDIR
# shellcheck source=../../../../tools/lib.sh
source "$here/../../../../tools/lib.sh"
require_cmd uv jq yq
export A="$alias"

if [ "$action" = add ]; then
    [ -n "$scratch" ] && [ -n "$alias" ] && [ -z "$url" ] || { echo "add needs --from and --alias (and takes no --url)" >&2; exit 2; }
    live="${live:-$alias}"
    [ "$live" != candidate ] || { echo "the live alias cannot be candidate; pass --as <alias>" >&2; exit 2; }
    [ -f "$scratch" ] || { echo "no such file: $scratch" >&2; exit 1; }
    [ -f "$CONFIG_PATH" ] || { echo "no config at $CONFIG_PATH" >&2; exit 1; }
    [ "$(yq -r '[.models[] | select(.alias == strenv(A))] | length' "$scratch")" -gt 0 ] || { echo "no entry $alias in $scratch" >&2; exit 1; }
    file="$(yq -r '[.models[] | select(.alias == strenv(A))][0].file // ""' "$scratch")"
    [ "$(B="$live" yq -r '[.models[] | select(.alias == strenv(B))] | length' "$CONFIG_PATH")" -eq 0 ] \
        || { echo "alias $live is already in $CONFIG_PATH" >&2; exit 1; }
    owner="$(F="$file" yq -r '[.models[] | select(.file == strenv(F))][0].alias // ""' "$CONFIG_PATH")"
    [ -z "$owner" ] || { echo "$file is already the file of $owner; use that alias" >&2; exit 1; }
    export S="$scratch" B="$live"
    add_entry() { yq -i '.models += [load(strenv(S)).models[] | select(.alias == strenv(A)) | .alias = strenv(B) | .enabled = false | del(.benchmark)]' "$1"; }
    edit_config_atomically pre-entry add_entry   # prints `backup: <path>`, validates the result, renames atomically
    echo "added: $live"
else
    [ -n "$alias" ] && [ -n "$url" ] && [ -z "$scratch$live" ] || { echo "set-url needs --alias and --url (and takes no --from or --as)" >&2; exit 2; }
    form='^https://huggingface\.co/[^/]+/[^/]+/resolve/[0-9a-f]{40}/.+'
    [[ "$url" =~ $form ]] || { echo "$url is not a https://huggingface.co/<owner>/<repo>/resolve/<40-hex sha>/<path> url" >&2; exit 2; }
    [ -f "$CONFIG_PATH" ] || { echo "no config at $CONFIG_PATH" >&2; exit 1; }
    [ "$(yq -r '[.models[] | select(.alias == strenv(A))] | length' "$CONFIG_PATH")" -gt 0 ] || { echo "no live entry $alias" >&2; exit 1; }
    file="$(yq -r '[.models[] | select(.alias == strenv(A))][0].file' "$CONFIG_PATH")"
    [ "${url##*/}" = "$file" ] || { echo "the url's file ${url##*/} is not $file, the file of $alias" >&2; exit 2; }
    export U="$url"
    set_url() { yq -i '(.models[] | select(.alias == strenv(A)) | .url) = strenv(U)' "$1"; }
    edit_config_atomically pre-url set_url
    echo "url: $alias"
fi
