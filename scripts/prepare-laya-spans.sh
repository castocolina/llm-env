#!/usr/bin/env bash
# Prepare the host-visible Laya span directory for the rootless container UID.
set -euo pipefail

state_root="${HOME}/.local/state/llm-env"
tmp_root="${TMPDIR:-/tmp}"
span_dir="${1:-${state_root}/laya-spans}"
require_cmd() { command -v "$1" >/dev/null 2>&1 || { echo "error: required command not found: $1" >&2; exit 1; }; }
fail() { echo "error: $*" >&2; exit 1; }
require_cmd podman
if [[ "$span_dir" != "$state_root/laya-spans" ]]; then
    [[ "$span_dir" != *".."* ]] || fail "span directory must not contain parent traversal"
    [[ "${span_dir%/*}" == "$tmp_root" && "${span_dir##*/}" =~ ^span-sink[.][A-Za-z0-9]{6,}$ ]] || fail "span directory must be the state sink or a private span-sink temporary directory"
fi
for path in "$HOME/.local" "$HOME/.local/state" "$state_root" "$span_dir"; do
    [[ ! -L "$path" ]] || fail "refusing symlink in span directory path: $path"
done
if [[ -e "$span_dir" && ! -d "$span_dir" ]]; then fail "span path exists and is not a directory: $span_dir"; fi
if [[ -d "$span_dir" ]]; then
    while IFS= read -r -d '' entry; do
        [[ ! -L "$entry" ]] || fail "refusing symlink in span directory: $entry"
    done < <(find -P "$span_dir" -mindepth 1 -maxdepth 1 -print0)
else
    install -d -m 0755 "$span_dir"
fi
podman unshare chown 10001:10001 "$span_dir"
podman unshare chmod 0755 "$span_dir"
