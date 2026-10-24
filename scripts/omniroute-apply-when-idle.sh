# shellcheck shell=bash
# Shared deferred recreate of the omniroute container, sourced by scripts/update-omniroute.sh (its two callers: a newer
# staged image, and config drift with the running image).
#
#   apply_when_idle CONFIG_ID IMMUTABLE_REF
#     CONFIG_ID      image config id the replacement must run (sha256:<hex>); the running image id = "same image".
#     IMMUTABLE_REF  locally present repo@digest reference for that image (already pulled and verified).
#   The desired service definition is the preflight artifacts in $work (effective.json = the rendered compose service).
#   Steps: projection + provider round trip; idle gate (running target only: activity profile, observer, quiet for
#   min_wait s, newest client call older than that, waiting up to max_deferred_wait s); hand-off to the commit worker
#   (recheck, last look, target-only stop/rm/up, health verification; no rollback).
#   Result: exit 0 with `updated <old> -> <new>`, 75 with `deferred: <reason>`, 1 with `error: ...` on stderr. It
#   exits the calling shell, except once per run (while $rebaselined is empty): a relevant inventory change during the
#   wait prints `phase: re-baselining (...)` and returns, so the caller re-runs preflight and calls it again.
#
# Run context (set by the caller, as for acquire_update_lock): llm, cli, work (0700, preflight artifacts), the update
# lock on FD 9, defer, rebaselined, config_path, compose_path, state, stop_seconds, min_wait, max_deferred_wait, health_timeout.
# shellcheck disable=SC2154 # the run context above is provided by the sourcing script.

observer_pid=""
stop_observer() {
    if [[ -n "$observer_pid" ]]; then
        kill -TERM -- "-$observer_pid" 2>/dev/null || true
        wait "$observer_pid" 2>/dev/null || true
        observer_pid=""
    fi
}

# Password files live only while a reader needs them: the observer reads one once at start, the worker from the
# hand-off on. Never argv/env; 0600, no trailing newline. `pw` is the configured password (the replacement starts with
# it); `live-pw` is the running container's own (they differ only while INITIAL_PASSWORD itself drifted).
write_pw() {
    ( umask 077
      pw="$(yq -r '.omniroute.initial_password // ""' "$config_path")" && printf '%s' "$pw" >"$work/pw" &&
      pw="$(jq -r '(.[0].Config.Env // [])[] | select(startswith("INITIAL_PASSWORD=")) | ltrimstr("INITIAL_PASSWORD=")' \
          "$work/target.json")" && printf '%s' "$pw" >"$work/live-pw" ) \
        || { echo "error: cannot read omniroute.initial_password" >&2; exit 1; }
}

apply_when_idle() {
    local config_id="$1" immutable_ref="$2" rc stat_line stat_rest lease_tick stale_limit last_seq stale_since
    local line seq verdict reason detail dl worker_budget
    local -a stat_fields
    [[ "$config_id" =~ ^sha256:[0-9a-f]{64}$ && -n "$immutable_ref" ]] \
        || { echo "error: apply_when_idle needs a config id and a reference" >&2; exit 1; }

    # Projection + provider round trip (75/1 before any observation). An absent target has no container to project
    # from; the worker then creates it from the deployed compose file (the configured tag then names the image).
    if [[ "$state" != "absent" ]]; then
        cli projection --dir "$work" --service omniroute --image "$immutable_ref" --out "$work/projection.yml"
    fi

    # Observe (running targets only). The wait budget starts now (after staging). The supervisor reads the password
    # file once at start and publishes the gate verdict in $work/latest.json; the file is removed at its first verdict.
    [[ -f "$work/deadline" ]] || cli deadline --file "$work/deadline" --init "$max_deferred_wait"   # kept on re-baseline
    if [[ "$state" == "running" ]]; then
        echo "phase: observing (idle gate: ${min_wait} s quiet, wait budget ${max_deferred_wait} s)"
        write_pw
        stat_line="$(<"/proc/$$/stat")"; stat_rest="${stat_line##*) }"; read -r -a stat_fields <<<"$stat_rest"
        lease_tick="${stat_fields[19]}"                                   # field 22 of /proc/$$/stat
        setsid "${llm[@]}" observe --dir "$work" --config "$config_path" --compose "$compose_path" \
            --password-file "$work/live-pw" --lease-pid "$$" --lease-start-tick "$lease_tick" \
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
                rm -f "$work/pw" "$work/live-pw"                          # the observer has read it (it samples)
                stale_since=$SECONDS
                case "$verdict" in
                    wait) ;;
                    ready) break ;;
                    defer-deadline) defer deadline ;;
                    defer-unknown)
                        [[ "$reason" =~ ^[a-z]+$ ]] || reason=feed
                        detail="$(jq -r '.sample.detail // ""' "$work/latest.json" 2>/dev/null || true)"
                        # A relevant container (sibling, dependent; never the target itself) changed while waiting: the caller
                        # re-runs preflight once in the same lock and budget; a second change in this run defers `ownership`.
                        if [[ "$reason" == ownership && "$detail" == "relevant inventory changed: "* && -z "${rebaselined:-}" ]]; then
                            stop_observer
                            rm -f "$work/latest.json" "$work/pw" "$work/live-pw"
                            echo "phase: re-baselining (${detail#relevant inventory changed: })"
                            return 0
                        fi
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

    # Hand off to the detached commit worker. It inherits FD 9 (the lock) and keeps it until it exits,
    # even if this shell is SIGKILLed; every Podman/provider child inside it closes FD 9.
    stop_observer
    write_pw
    printf 'config_id=%s\nimmutable_ref=%s\nstaged_id=%s\nhealth_timeout=%s\nmin_wait=%s\n' \
        "$config_id" "$immutable_ref" "${config_id#sha256:}" "$health_timeout" "$min_wait" >"$work/commit.env"
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
}
