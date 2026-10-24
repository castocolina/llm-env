#!/usr/bin/env bash
# Commit worker: detached from the updater shell; keeps FD 9 (the update lock) until it exits.
# Post-commit mutation is NOT transactional: failures are reported, never rolled back.
set -euo pipefail
work="$1" config_path="$2" compose_path="$3"
trap 'rm -rf "$work"' EXIT   # first: the worker owns $work (password file included) on every exit path
# shellcheck disable=SC1091 # Resolved from this script at runtime.
# shellcheck source=../tools/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/../tools/lib.sh"
unset COMPOSE_FILE COMPOSE_INSPECT_DIR
llm=(uv run --project "$REPO_DIR" "$REPO_DIR/llmenv.py" omniroute-update)
trap_signals_to_exit
backend_env_init
cli() { "${llm[@]}" "$@" 9>&-; }
# shellcheck disable=SC2154 # backend_env is filled by backend_env_init above.
pm() { "${backend_env[@]}" podman "$@" 9>&-; }

config_id="" immutable_ref="" staged_id="" health_timeout="" min_wait=""
# shellcheck disable=SC2034 # immutable_ref is kept for the failure report.
while IFS="=" read -r k v; do
    case "$k" in
        config_id) config_id="$v" ;; immutable_ref) immutable_ref="$v" ;;
        staged_id) staged_id="$v" ;; health_timeout) health_timeout="$v" ;; min_wait) min_wait="$v" ;; *) ;;
    esac
done <"$work/commit.env"
container_id="" image_ref="" project="" stop_seconds="" pinned="" provider="" state=""
while IFS="=" read -r k v; do
    case "$k" in
        container_id) container_id="$v" ;; image_ref) image_ref="$v" ;; project) project="$v" ;;
        stop_seconds) stop_seconds="$v" ;; pinned) pinned="$v" ;; provider) provider="$v" ;; state) state="$v" ;; *) ;;
    esac
done <"$work/deploy.env"

new_id="" prior_tag="" remaining=""
fail() {
    printf 'error: update failed at stage: %s\n' "$1" >&2
    printf 'old container: %s\nnew container: %s\nprior image tag target: %s\n' \
        "${container_id:0:12}" "${new_id:0:12}" "${prior_tag:0:12}" >&2
    printf 'No rollback was attempted; recovery is manual (the previous image is still present).\n' >&2
    exit 1
}
# Remaining wait budget; exits 75 `deferred: deadline` when the exclusive deadline has passed.
budget_left() {
    local out rc=0
    out="$(cli deadline --file "$work/deadline")" || rc=$?
    if (( rc != 0 )); then printf '%s\n' "$out"; exit "$rc"; fi
    remaining="${out#remaining=}"
}

# (a) second full inventory + fingerprint recompute, bounded by the remaining budget (a slow success after the
#     deadline is discarded); any drift or incoming dependent defers before mutation. The backend identity is
#     part of the snapshot, so it is re-verified under the same environment the mutations below use.
budget_left
rc=0
cli preflight --recheck --budget "$remaining" --config "$config_path" --compose "$compose_path" \
    --out-dir "$work" >"$work/recheck.out" || rc=$?
if (( rc != 0 )); then cat "$work/recheck.out"; exit "$rc"; fi
# (b) prior tag target, for the failure report only.
prior_tag="$(pm image inspect --format '{{.Id}}' "$image_ref" 2>/dev/null || true)"
# (b2) last look (running target only): the observer stopped seconds ago, so take one more API-only sample (health,
#      history, call-log rows; same profile and client-vs-probe rule) right before the first mutation. Client
#      activity or an unknown sample defers (75) with nothing changed.
if [[ "$state" == "running" ]]; then
    [[ "$min_wait" =~ ^[0-9]+$ ]] || fail "last look: malformed min_wait"
    rc=0
    look="$(cli activity --inspect "$work/target.json" --port "$(jq -r .port "$work/deployment.json")" \
        --password-file "$work/pw" --min-wait "$min_wait")" || rc=$?
    if (( rc != 0 )); then printf '%s\n' "$look"; exit "$rc"; fi
fi
# (c) exclusive deadline, rechecked after (a)+(b) and immediately before the first mutation.
budget_left

# --- first mutation ---
if [[ "$pinned" != "1" ]]; then
    pm tag "$staged_id" "$image_ref" || fail "tag"
fi
up_file="$compose_path"                                           # absent target: the deployed compose file
if [[ -n "$container_id" ]]; then                                 # running or stopped target: replace it
    pm stop --time "$stop_seconds" "$container_id" || fail "stop"
    pm rm "$container_id" || fail "rm"                            # by ID only: no forced or dependency-following removal
    up_file="$work/projection.yml"
fi
if [[ -n "$(pm ps --all --filter name='^omniroute$' --format '{{.ID}}')" ]]; then
    fail "name still in use after removal"                        # never remove an unexpected same-name container
fi
"${backend_env[@]}" "$provider" --in-pod false --env-file /dev/null -p "$project" -f "$up_file" \
    up -d --no-deps --no-recreate --no-build --pull never omniroute 9>&- || fail "up"
rc=0
verify_out="$(cli verify-commit --dir "$work" --service omniroute --old-id "$container_id" \
    --config-id "$config_id" --timeout "$health_timeout" --password-file "$work/pw")" || rc=$?
if (( rc != 0 )); then fail "verify-commit"; fi
while IFS="=" read -r k v; do
    case "$k" in new_id) new_id="$v" ;; *) ;; esac
done <<<"$verify_out"
[[ "$new_id" =~ ^[0-9a-f]{64}$ ]] || fail "verify-commit output"
old_short="${container_id:0:12}"
echo "updated ${old_short:-none} -> ${new_id:0:12}"
