#!/usr/bin/env bash
# Build a small span-observer derivative without reinstalling the cached ROCm stack.
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
source_hash="$(cat "$REPO_DIR/docker/system-one/observer.Dockerfile" "$REPO_DIR/docker/system-one/laya_span_observer.py" | sha256sum | awk '{print $1}')"
key="$(printf '%s\n%s\n' "$base_id" "$source_hash" | sha256sum | awk '{print substr($1,1,12)}')"
image="localhost/llm-env-system-one-span-sink:${key}"
if podman image exists "$image"; then
    label="$(podman image inspect "$image" | jq -r '.[0].Labels["llm-env.span-observer-sha256"] // ""')"
    [[ "$label" == "$key" ]] || die "span observer image label does not match current source/base hash"
else
    log="$REPO_DIR/tmp/system-one-observer-build-${key}.log"
    mkdir -p "$REPO_DIR/tmp"
    podman build --pull=never --build-arg "BASE_IMAGE=$base_image" \
        --label "llm-env.span-observer-sha256=$key" \
        -f "$REPO_DIR/docker/system-one/observer.Dockerfile" \
        -t "$image" "$REPO_DIR/docker/system-one" > "$log" 2>&1 \
        || { tail -80 "$log" >&2; die "System One span observer derivative build failed; base image and weights are preserved"; }
fi
# Derivatives of earlier base images or sources are keyed by a hash and never reused: drop them (an image a running
# container still uses refuses removal and stays until the next build). A rollback rebuilds its derivative in seconds.
while IFS= read -r stale; do
    [[ -n "$stale" && "$stale" != "$image" ]] || continue
    podman rmi "$stale" >/dev/null 2>&1 || true
done < <(podman images --format '{{.Repository}}:{{.Tag}}' --filter 'reference=localhost/llm-env-system-one-span-sink' 2>/dev/null || true)
prune_superseded_build_images
printf '%s\n' "$image"
