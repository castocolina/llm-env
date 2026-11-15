#!/usr/bin/env bash
# Sourced by the updater workers (OmniRoute, remote-setup migration, System One). Uses the caller's pm, fail,
# backend_env, provider and project. Not transactional: a failed stage calls the caller's fail.

# replace_service <service> <container id, empty when absent> <stop seconds> <compose file to bring up>
replace_service() {
    local service="$1" cid="$2" stop_seconds="$3" up_file="$4"
    if [[ -n "$cid" ]]; then
        pm stop --time "$stop_seconds" "$cid" || fail "stop"
        pm rm "$cid" || fail "rm"                                   # by ID only: no forced or dependency-following removal
    fi
    if [[ -n "$(pm ps --all --filter "name=^${service}\$" --format '{{.ID}}')" ]]; then
        fail "name still in use after removal"                      # never remove an unexpected same-name container
    fi
    # shellcheck disable=SC2154 # backend_env, provider and project belong to the caller.
    "${backend_env[@]}" "$provider" --in-pod false --env-file /dev/null -p "$project" -f "$up_file" \
        up -d --no-deps --no-recreate --no-build --pull never "$service" 9>&- || fail "up"
}
