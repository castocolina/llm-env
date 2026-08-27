#!/usr/bin/env bash
# update-omniroute.sh — check Docker Hub for a newer published OmniRoute
# version than the one pulled locally, and only if one exists, pull it
# (while the service keeps running), then stop and start.
set -euo pipefail
# shellcheck disable=SC1091 # Resolved from this script at runtime.
# shellcheck source=../tools/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/../tools/lib.sh"

require_cmd podman curl jq yq

[ -f "$CONFIG_PATH" ] || die "no config at ${CONFIG_PATH}; run 'make setup' first"
migrate_config_file || die "configuration migration failed"

image="$(yq -r '.omniroute.image // "docker.io/diegosouzapw/omniroute:latest"' "$CONFIG_PATH")"

# Only docker.io (bare "namespace/repo" implies docker.io too, same as
# podman/docker's own resolution) is supported: the version check below
# queries Docker Hub's own tags API, which has no equivalent for other
# registries. A configured image on any other registry can't be checked
# automatically -- fail soft (exit 0) rather than block a manual pull.
image_no_tag="${image%%:*}"
host_and_path="$image_no_tag"
case "$host_and_path" in
    docker.io/*) hub_path="${host_and_path#docker.io/}" ;;
    */*/*)
        log_warn "unsupported registry for automatic version checking: ${image}; pull manually if needed"
        exit 0
        ;;
    *) hub_path="$host_and_path" ;;
esac
case "$hub_path" in
    */*) : ;;
    *)
        log_warn "unsupported image reference for automatic version checking: ${image}; pull manually if needed"
        exit 0
        ;;
esac

log_step "Checking the installed OmniRoute version"
# --pull=never: this is a read-only check, not the update itself -- podman
# run would otherwise silently pull on our behalf the moment no local image
# exists yet, before we've even decided whether an update is warranted.
if podman image exists "$image"; then
    local_version="$(podman run --rm --pull=never --entrypoint cat "$image" /app/package.json 2>/dev/null \
        | jq -r '.version // empty' 2>/dev/null || true)"
    [ -n "$local_version" ] || local_version="0.0.0"
else
    log_info "no local image found"
    local_version="0.0.0"
fi

log_step "Checking the latest published version on Docker Hub"
tags_json="$(curl -fsS "https://hub.docker.com/v2/repositories/${hub_path}/tags?page_size=100&ordering=last_updated")" \
    || die "could not reach Docker Hub to check for updates"
# Docker Hub's tags API orders by last_updated (most recent first) by
# default. The most-recently-updated tag matching a plain "X.Y.Z" name is
# the latest published release -- pure recency, no lexicographic-vs-numeric
# version sorting needed here (that only matters for the local/remote
# comparison below, where the two values being compared aren't already
# ordered by anything).
remote_version="$(printf '%s' "$tags_json" | jq -r '
    [.results[]? | .name | select(test("^[0-9]+\\.[0-9]+\\.[0-9]+$"))][0] // empty
')"
[ -n "$remote_version" ] || die "could not determine the latest published OmniRoute version from Docker Hub"

log_info "installed: ${local_version}  latest published: ${remote_version}"

# Numeric major.minor.patch comparison, not lexicographic -- lexicographic
# would wrongly rank "3.8.9" above "3.8.48".
version_gt() {
    [ "$1" != "$2" ] && [ "$(printf '%s\n%s\n' "$1" "$2" | sort -V | tail -n1)" = "$1" ]
}

if ! version_gt "$remote_version" "$local_version"; then
    log_info "already on the latest published version (${local_version}); nothing to do"
    exit 0
fi

log_step "Newer OmniRoute version available: ${local_version} -> ${remote_version}"

# Pulled BEFORE stopping the service, not after: the pull is the slow part
# (a full image download), and the running stack has no reason to be down
# for it. Downtime is limited to the stop+start themselves, both fast once
# the new image is already sitting locally. A mutable tag like ":latest"
# always resolves against the registry on pull -- nothing here needs a
# separate "ignore cache" flag. The risk this guards against is different:
# `podman compose up -d` (invoked by start.sh below) defaults to pull
# policy "missing", which skips pulling entirely once *any* image already
# exists under this tag name, no matter how stale. Pulling explicitly here
# guarantees the freshly-resolved manifest and layers are already in place
# before that ever runs.
log_step "Pulling ${image}"
podman pull "$image" || die "could not pull ${image}"

log_step "Stopping the service"
bash "${REPO_DIR}/scripts/stop.sh"

log_step "Starting the service"
bash "${REPO_DIR}/scripts/start.sh"

log_info "OmniRoute updated to ${remote_version}"
