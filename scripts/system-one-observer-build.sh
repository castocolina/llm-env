#!/usr/bin/env bash
# Build a small observer-only derivative without reinstalling the cached ROCm stack.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck disable=SC1091
source "$REPO_DIR/tools/lib.sh"
require_cmd podman yq sha256sum
[ -f "$CONFIG_PATH" ] || die "no config at ${CONFIG_PATH}; run 'make setup' first"
base_image="$(yq -r '.system_one.image' "$CONFIG_PATH")"
[ -n "$base_image" ] && [ "$base_image" != null ] || die "System One image is not configured"
podman image exists "$base_image" || die "System One base image missing; run 'make setup'"
base_id="$(podman image inspect "$base_image" --format '{{.Id}}')"
source_hash="$(cat "$REPO_DIR/docker/system-one/observer.Dockerfile" "$REPO_DIR/docker/system-one/phoenix_observer.py" | sha256sum | awk '{print $1}')"
key="$(printf '%s\n%s\n' "$base_id" "$source_hash" | sha256sum | awk '{print substr($1,1,12)}')"
image="localhost/llm-env-system-one-phoenix:${key}"
if podman image exists "$image"; then
    label="$(podman image inspect "$image" | jq -r '.[0].Labels["llm-env.phoenix-observer-sha256"] // ""')"
    [[ "$label" == "$key" ]] || die "observer image label does not match current source/base hash"
else
    log="$REPO_DIR/tmp/system-one-observer-build-${key}.log"
    mkdir -p "$REPO_DIR/tmp"
    podman build --pull=never --build-arg "BASE_IMAGE=$base_image" \
        --label "llm-env.phoenix-observer-sha256=$key" \
        -f "$REPO_DIR/docker/system-one/observer.Dockerfile" \
        -t "$image" "$REPO_DIR/docker/system-one" > "$log" 2>&1 \
        || { tail -80 "$log" >&2; die "System One observer derivative build failed; base image and weights are preserved"; }
fi
printf '%s\n' "$image"
