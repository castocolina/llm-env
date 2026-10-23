#!/usr/bin/env bash
# Idle-aware, OmniRoute-only updater. Exit 0 = unchanged/updated, 75 = safe deferral, 1 = error.
# Not zero-downtime, no rollback, observation is not admission control (see the design spec).
set -euo pipefail
# shellcheck disable=SC1091 # Resolved from this script at runtime.
# shellcheck source=../tools/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/../tools/lib.sh"
require_cmd podman skopeo jq yq uv journalctl flock setsid timeout

# tools/lib.sh exports COMPOSE_FILE and COMPOSE_INSPECT_DIR; the CLI rejects every COMPOSE_* variable,
# so keep the paths locally and drop the exports before any CLI runs.
# shellcheck disable=SC2153,SC2034 # CONFIG_PATH/COMPOSE_FILE are exported by tools/lib.sh; the locals are consumed below.
config_path="$CONFIG_PATH"
compose_path="$COMPOSE_FILE"
unset COMPOSE_FILE COMPOSE_INSPECT_DIR
# An array (not a shell function) so that `setsid "${llm[@]}" observe ...` runs a real executable.
llm=(uv run --project "$REPO_DIR" "$REPO_DIR/llmenv.py" omniroute-update)
backend_env_init
cli() { "${llm[@]}" "$@" 9>&-; }                    # CLI steps never inherit the update lock
# shellcheck disable=SC2154 # backend_env is filled by backend_env_init above.
pm() { "${backend_env[@]}" podman "$@" 9>&-; }      # Podman steps use the validated backend environment, no lock FD
defer() { echo "deferred: $1"; exit 75; }

# 0. Overrides: capture first, check status, parse without eval (eval would hide a failed substitution).
ov_args=()
if [[ -v LLM_ENV_UPDATE_IDLE_SECONDS ]]; then ov_args+=(--idle "$LLM_ENV_UPDATE_IDLE_SECONDS"); fi
if [[ -v LLM_ENV_UPDATE_MAX_WAIT_SECONDS ]]; then ov_args+=(--max-wait "$LLM_ENV_UPDATE_MAX_WAIT_SECONDS"); fi
overrides_out="$("${llm[@]}" overrides "${ov_args[@]}")" || exit 1
min_wait="" max_deferred_wait=""
while IFS="=" read -r k v; do
    case "$k" in
        min_wait) min_wait="$v" ;;
        max_deferred_wait) max_deferred_wait="$v" ;;
        *) exit 1 ;;
    esac
done <<<"$overrides_out"
[[ "$min_wait" =~ ^[0-9]+$ && "$max_deferred_wait" =~ ^[0-9]+$ ]] || exit 1
health_timeout="${LLM_ENV_HEALTH_TIMEOUT_SECONDS:-60}"
[[ "$health_timeout" =~ ^[1-9][0-9]*$ ]] || { echo "error: invalid LLM_ENV_HEALTH_TIMEOUT_SECONDS" >&2; exit 1; }

work="$(mktemp -d)"                      # 0700; private artifacts, projection, deadline, password file
observer_pid=""
stop_observer() {
    if [[ -n "$observer_pid" ]]; then
        kill -TERM -- "-$observer_pid" 2>/dev/null || true
        wait "$observer_pid" 2>/dev/null || true
        observer_pid=""
    fi
}
trap 'stop_observer; rm -rf "$work"' EXIT

# 1. Lock before any staging. The path comes from the inspected backend; the file is never unlinked.
acquire_update_lock

# 2. Preflight (inspects identity after the lock): artifacts in $work, key=value on stdout.
rc=0
cli preflight --config "$config_path" --compose "$compose_path" --out-dir "$work" >"$work/deploy.env" || rc=$?
if (( rc != 0 )); then cat "$work/deploy.env"; exit "$rc"; fi
container_id="" image_id="" image_ref="" stop_seconds="" os_name="" architecture="" variant=""
while IFS="=" read -r k v; do
    case "$k" in
        container_id) container_id="$v" ;;
        image_id) image_id="$v" ;;
        image_ref) image_ref="$v" ;;
        stop_seconds) stop_seconds="$v" ;;
        os) os_name="$v" ;;
        architecture) architecture="$v" ;;
        variant) variant="$v" ;;
        *) ;;
    esac
done <"$work/deploy.env"
[[ "$container_id" =~ ^[0-9a-f]{64}$ && "$image_id" =~ ^sha256:[0-9a-f]{64}$ && "$stop_seconds" =~ ^[0-9]+$ ]] \
    || { echo "error: malformed preflight output" >&2; exit 1; }

# 3. Resolve the immutable target; nothing to do when it is already running.
rc=0
cli resolve --reference "$image_ref" --os "$os_name" --arch "$architecture" --variant "$variant" \
    --json "$work/resolved.json" >"$work/resolve.out" || rc=$?
if (( rc != 0 )); then cat "$work/resolve.out"; exit "$rc"; fi
config_id="$(jq -r .config_id "$work/resolved.json")"
immutable_ref="$(jq -r .immutable_ref "$work/resolved.json")"
[[ "$config_id" =~ ^sha256:[0-9a-f]{64}$ ]] || { echo "error: malformed resolved identity" >&2; exit 1; }
if [[ "$config_id" == "$image_id" ]]; then echo "unchanged"; exit 0; fi
staged_id="${config_id#sha256:}"

# 4. Stage and verify. The configured tag is NOT moved here.
pm pull "$immutable_ref" >/dev/null
pm image inspect --format json "$immutable_ref" >"$work/staged.json"
cli verify-staged --resolved "$work/resolved.json" --inspect "$work/staged.json"

# 5. Projection + provider round trip (75/1 before any observation).
cli projection --dir "$work" --service omniroute --image "$immutable_ref" --out "$work/projection.yml"

# 6. Observe. The wait budget starts now (after staging). The supervisor gets the password through a 0600 file
#    inside $work, never argv/env, and publishes the gate verdict in $work/latest.json.
( umask 077; yq -r '.omniroute.initial_password // ""' "$config_path" >"$work/pw" )
cli deadline --file "$work/deadline" --init "$max_deferred_wait"
stat_line="$(<"/proc/$$/stat")"; stat_rest="${stat_line##*) }"; read -r -a stat_fields <<<"$stat_rest"
lease_tick="${stat_fields[19]}"                                   # field 22 of /proc/$$/stat
setsid "${llm[@]}" observe --dir "$work" --config "$config_path" --compose "$compose_path" \
    --password-file "$work/pw" --lease-pid "$$" --lease-start-tick "$lease_tick" \
    --lifetime "$((max_deferred_wait + 10))" --interval 5 \
    --min-wait "$min_wait" --deadline-file "$work/deadline" 9>&- >/dev/null &
observer_pid=$!

stale_limit=15                                                    # three sample intervals without a new latest.json
last_seq=-1
stale_since=$SECONDS
while true; do
    sleep 1 9>&-
    rc=0
    dl="$(cli deadline --file "$work/deadline")" || rc=$?         # exclusive deadline, authoritative in the shell too
    if (( rc != 0 )); then printf '%s\n' "$dl"; exit "$rc"; fi
    line="$(jq -r '[.sequence, .verdict, .reason] | join(" ")' "$work/latest.json" 2>/dev/null || true)"
    read -r seq verdict reason <<<"${line:--1 none none}"
    if [[ "$seq" =~ ^[0-9]+$ ]] && (( seq > last_seq )); then
        last_seq="$seq"
        stale_since=$SECONDS
        case "$verdict" in
            wait) ;;
            ready) break ;;
            defer-deadline) defer deadline ;;
            defer-unknown) [[ "$reason" =~ ^[a-z]+$ ]] || reason=feed; defer "$reason" ;;
            *) echo "error: unexpected observer verdict" >&2; exit 1 ;;
        esac
    elif (( SECONDS - stale_since > stale_limit )); then
        defer feed
    fi
done

# 7. Hand off to the detached commit worker. It inherits FD 9 (the lock) and keeps it until it exits,
#    even if this shell is SIGKILLed; every Podman/provider child inside it closes FD 9.
stop_observer
printf 'config_id=%s\nimmutable_ref=%s\nstaged_id=%s\nhealth_timeout=%s\n' \
    "$config_id" "$immutable_ref" "$staged_id" "$health_timeout" >"$work/commit.env"
worker_budget=$(( stop_seconds + 3 * health_timeout + 120 ))
rc=0
setsid --wait timeout --kill-after=10 "$worker_budget" \
    bash "$REPO_DIR/scripts/omniroute-commit-worker.sh" "$work" "$config_path" "$compose_path" || rc=$?
if (( rc == 124 || rc == 137 )); then
    echo "error: the commit worker exceeded its time budget; inspect the deployment manually (no rollback was attempted)" >&2
    exit 1
fi
exit "$rc"
