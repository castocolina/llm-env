#!/usr/bin/env bash
# One-time, remote-setup-only recreation that drops its native dependency on OmniRoute.
# Everything before the detached worker is read-only. Not transactional: no rollback is attempted.
set -euo pipefail
# shellcheck disable=SC1091 # Resolved from this script at runtime.
# shellcheck source=../tools/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/../tools/lib.sh"
require_cmd podman jq yq uv flock setsid timeout

# tools/lib.sh exports COMPOSE_FILE and COMPOSE_INSPECT_DIR; the CLI rejects every COMPOSE_* variable,
# so keep the paths locally and drop the exports before any CLI runs.
# shellcheck disable=SC2153,SC2034 # CONFIG_PATH/COMPOSE_FILE are exported by tools/lib.sh; the locals are consumed below.
config_path="$CONFIG_PATH"
compose_path="$COMPOSE_FILE"
unset COMPOSE_FILE COMPOSE_INSPECT_DIR
llm=(uv run --project "$REPO_DIR" "$REPO_DIR/llmenv.py" omniroute-update)
backend_env_init
cli() { "${llm[@]}" "$@" 9>&-; }
# shellcheck disable=SC2154 # backend_env is filled by backend_env_init above.
pm() { "${backend_env[@]}" podman "$@" 9>&-; }
health_timeout="${LLM_ENV_HEALTH_TIMEOUT_SECONDS:-60}"
[[ "$health_timeout" =~ ^[1-9][0-9]*$ ]] || { echo "error: invalid LLM_ENV_HEALTH_TIMEOUT_SECONDS" >&2; exit 1; }

read_deploy() {   # sets container_id image_id project stop_seconds provider from $work/deploy.env
    container_id="" image_id="" project="" stop_seconds="" provider=""
    while IFS="=" read -r k v; do
        case "$k" in
            container_id) container_id="$v" ;;
            image_id) image_id="$v" ;;
            project) project="$v" ;;
            stop_seconds) stop_seconds="$v" ;;
            provider) provider="$v" ;;
            *) ;;
        esac
    done <"$work/deploy.env"
}

if [[ "${1:-}" == "--worker" ]]; then
    work="$2" config_path="$3" compose_path="$4"
    trap 'rm -rf "$work"' EXIT
    read_deploy
    fail() {
        printf 'error: migration failed at stage: %s\n' "$1" >&2
        printf 'old container: %s\n' "${container_id:0:12}" >&2
        printf 'No rollback was attempted. If remote-setup is gone, recreate it plainly with:\n  podman-compose -p %s -f "%s" up -d --no-deps remote-setup\n' "$project" "$compose_path" >&2
        exit 1
    }
    rc=0
    cli preflight --recheck --budget 60 --config "$config_path" --compose "$compose_path" --out-dir "$work" >"$work/recheck.out" || rc=$?
    if (( rc != 0 )); then cat "$work/recheck.out"; exit "$rc"; fi
    pm stop --time "$stop_seconds" "$container_id" || fail "stop"
    pm rm "$container_id" || fail "rm"                              # by ID only
    if [[ -n "$(pm ps --all --filter name='^remote-setup$' --format '{{.ID}}')" ]]; then fail "name still in use after removal"; fi
    "${backend_env[@]}" "$provider" --in-pod false --env-file /dev/null -p "$project" -f "$work/projection.yml" \
        up -d --no-deps --no-recreate --no-build --pull never remote-setup 9>&- || fail "up"
    cli verify-commit --dir "$work" --service remote-setup --old-id "$container_id" --expect-no-deps \
        --timeout "$health_timeout" >"$work/verify.out" || fail "verify-commit"
    echo "remote-setup recreated without its native dependency"
    exit 0
fi

# 1. The deployed compose must already be dependency-free; this script never rewrites it.
yq -e '.services["remote-setup"] | has("depends_on") | not' "$compose_path" >/dev/null 2>&1 || {
    echo "error: re-render the deployed compose first (README 'Operations'); the migration never rewrites it" >&2
    exit 1
}
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
# 2. Lock + read-only preflight of the remote-setup service.
acquire_update_lock
rc=0
cli preflight --service remote-setup --config "$config_path" --compose "$compose_path" --out-dir "$work" >"$work/deploy.env" || rc=$?
if (( rc != 0 )); then cat "$work/deploy.env"; exit "$rc"; fi
read_deploy
[[ "$container_id" =~ ^[0-9a-f]{64}$ && "$image_id" =~ ^sha256:[0-9a-f]{64}$ && "$stop_seconds" =~ ^[0-9]+$ ]] || { echo "error: malformed preflight output" >&2; exit 1; }
# 3. No-op when the running container already has no native dependency.
if jq -e '.[0].Dependencies | length == 0' "$work/target.json" >/dev/null; then
    echo "remote-setup already has no native dependency; nothing changed"
    exit 0
fi
# 4. Prove the data volume and build the projection (same image id, so no pull).
jq -e --arg v "${project}_remote-setup-data" '.volume_names | index($v)' "$work/deployment.json" >/dev/null || {
    echo "error: ${project}_remote-setup-data is not mounted on the running remote-setup; refusing" >&2
    exit 1
}
cli projection --dir "$work" --service remote-setup --image "${image_id#sha256:}" --out "$work/projection.yml"
# 5. Detached bounded worker; it inherits FD 9 (the lock) and keeps it until it exits.
budget=$(( stop_seconds + 3 * health_timeout + 120 ))
rc=0
setsid --wait timeout --kill-after=10 "$budget" bash "${BASH_SOURCE[0]}" --worker "$work" "$config_path" "$compose_path" || rc=$?
if (( rc == 124 || rc == 137 )); then
    echo "error: the migration worker exceeded its time budget; inspect remote-setup manually (no rollback was attempted)" >&2
    exit 1
fi
exit "$rc"
