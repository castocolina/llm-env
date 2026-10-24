#!/usr/bin/env bash
# Idle-aware, OmniRoute-only updater. Exit 0 = unchanged/updated, 75 = safe deferral, 1 = error.
# The last stdout line is the machine-readable result: `unchanged`, `updated <old> -> <new>` or `deferred: <reason>`;
# an error prints `error: ...` on stderr. `make update-omniroute` cannot pass 75 through (GNU make exits 2 for any
# failing recipe; its own message still says `Error 75` or `Error 1`): automation runs this script directly.
# Not zero-downtime, no rollback, observation is not admission control (see the design spec).
# A stopped or absent target skips the idle gate (nothing can be in flight) and is replaced/created directly.
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

work="$(private_work_dir)"               # 0700; private artifacts, projection, deadline, password file
observer_pid=""
stop_observer() {
    if [[ -n "$observer_pid" ]]; then
        kill -TERM -- "-$observer_pid" 2>/dev/null || true
        wait "$observer_pid" 2>/dev/null || true
        observer_pid=""
    fi
}
trap 'stop_observer; rm -rf "$work"' EXIT
trap_signals_to_exit
# The password file lives only while a reader needs it: the observer reads it once at start, the worker from the
# hand-off on. Never argv/env; 0600, no trailing newline.
write_pw() {
    ( umask 077; pw="$(yq -r '.omniroute.initial_password // ""' "$config_path")"; printf '%s' "$pw" >"$work/pw" ) \
        || { echo "error: cannot read omniroute.initial_password from the config" >&2; exit 1; }
}

# 1. Lock before any staging. The path comes from the inspected backend; the file is never unlinked.
acquire_update_lock

# 2. Preflight (inspects identity after the lock): artifacts in $work, key=value on stdout.
rc=0
cli preflight --config "$config_path" --compose "$compose_path" --out-dir "$work" >"$work/deploy.env" || rc=$?
if (( rc != 0 )); then cat "$work/deploy.env"; exit "$rc"; fi
container_id="" image_id="" image_ref="" stop_seconds="" os_name="" architecture="" variant="" state=""
while IFS="=" read -r k v; do
    case "$k" in
        container_id) container_id="$v" ;;
        image_id) image_id="$v" ;;
        image_ref) image_ref="$v" ;;
        stop_seconds) stop_seconds="$v" ;;
        os) os_name="$v" ;;
        architecture) architecture="$v" ;;
        variant) variant="$v" ;;
        state) state="$v" ;;
        *) ;;
    esac
done <"$work/deploy.env"
# state: running (observe, then replace), stopped (replace, no observation: nothing can be in flight) or
# absent (create the missing container from the deployed compose file, no observation).
case "$state" in
    running|stopped) [[ "$container_id" =~ ^[0-9a-f]{64}$ && "$image_id" =~ ^sha256:[0-9a-f]{64}$ ]] ;;
    absent) [[ -z "$container_id" && -z "$image_id" ]] ;;
    *) false ;;
esac && [[ "$stop_seconds" =~ ^[0-9]+$ ]] || { echo "error: malformed preflight output" >&2; exit 1; }

# 2b. A running image without an activity profile (shipped, or user-local under ~/.local/state/llm-env) is
#     characterized first, under this lock, in a disposable isolated Podman backend (bounded; phases on stdout). A
#     complete profile is stored locally and the update continues; an incomplete one defers `capability`.
if [[ "$state" == "running" ]]; then
    rc=0
    cli characterize --config-id "$image_id" || rc=$?
    if (( rc != 0 )); then exit "$rc"; fi
fi

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
pm pull "$immutable_ref" >/dev/null || { echo "error: podman pull of the resolved image failed (registry, network or disk)" >&2; exit 1; }
pm image inspect --format json "$immutable_ref" >"$work/staged.json" \
    || { echo "error: the pulled image cannot be inspected" >&2; exit 1; }
cli verify-staged --resolved "$work/resolved.json" --inspect "$work/staged.json"

# 5. Projection + provider round trip (75/1 before any observation). An absent target has no container to project
#    from; the worker then creates it from the deployed compose file (the configured tag now names the staged image).
if [[ "$state" != "absent" ]]; then
    cli projection --dir "$work" --service omniroute --image "$immutable_ref" --out "$work/projection.yml"
fi

# 6. Observe (running targets only). The wait budget starts now (after staging). The supervisor reads the password
#    file once at start and publishes the gate verdict in $work/latest.json; the file is removed at its first verdict.
cli deadline --file "$work/deadline" --init "$max_deferred_wait"
if [[ "$state" == "running" ]]; then
    echo "phase: observing (idle gate: ${min_wait} s quiet, wait budget ${max_deferred_wait} s)"
    write_pw
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
            rm -f "$work/pw"                                          # the observer has read it (it samples)
            stale_since=$SECONDS
            case "$verdict" in
                wait) ;;
                ready) break ;;
                defer-deadline) defer deadline ;;
                defer-unknown)
                    [[ "$reason" =~ ^[a-z]+$ ]] || reason=feed
                    detail="$(jq -r '.sample.detail // ""' "$work/latest.json" 2>/dev/null || true)"
                    if [[ -n "$detail" ]]; then printf 'detail: %s\n' "$detail" >&2; fi   # e.g. the unprofiled route
                    defer "$reason" ;;
                *) echo "error: unexpected observer verdict" >&2; exit 1 ;;
            esac
        elif ! kill -0 "$observer_pid" 2>/dev/null; then              # the observer exited without a verdict
            rc=0; wait "$observer_pid" || rc=$?
            observer_pid=""
            if (( rc == 74 )); then defer state; fi                     # it could not write latest.json (disk/quota)
            defer feed
        elif (( SECONDS - stale_since > stale_limit )); then
            defer feed
        fi
    done
fi

# 7. Hand off to the detached commit worker. It inherits FD 9 (the lock) and keeps it until it exits,
#    even if this shell is SIGKILLed; every Podman/provider child inside it closes FD 9.
stop_observer
write_pw
printf 'config_id=%s\nimmutable_ref=%s\nstaged_id=%s\nhealth_timeout=%s\nmin_wait=%s\n' \
    "$config_id" "$immutable_ref" "$staged_id" "$health_timeout" "$min_wait" >"$work/commit.env"
worker_budget=$(( stop_seconds + 3 * health_timeout + 120 ))
rc=0
trap - EXIT HUP INT TERM   # from here the worker owns $work (its own trap removes it): a signal here must not delete it
setsid --wait timeout --kill-after=10 "$worker_budget" \
    bash "$REPO_DIR/scripts/omniroute-commit-worker.sh" "$work" "$config_path" "$compose_path" || rc=$?
rm -rf "$work"   # the worker is gone (its own trap normally removed it; a SIGKILL after its time budget cannot)
if (( rc == 124 || rc == 137 )); then
    echo "error: the commit worker exceeded its time budget; inspect the deployment manually (no rollback was attempted)" >&2
    exit 1
fi
exit "$rc"
