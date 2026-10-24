#!/usr/bin/env bash
# Idle-aware, OmniRoute-only updater. Exit 0 = unchanged/updated, 75 = safe deferral, 1 = error.
# The last stdout line is the machine-readable result: `unchanged`, `updated <old> -> <new>` or `deferred: <reason>`;
# an error prints `error: ...` on stderr. `make update-omniroute` cannot pass 75 through (GNU make exits 2 for any
# failing recipe; its own message still says `Error 75` or `Error 1`): automation runs this script directly.
# Not zero-downtime, no rollback, observation is not admission control (see the design spec).
# A stopped or absent target skips the idle gate (nothing can be in flight) and is replaced/created directly.
# It is also the deferred restart for configuration: when the rendered omniroute service (environment, resource limits,
# ports, healthcheck, restart policy, image reference) differs from the running container it prints
# `drift: <field names>` and recreates it with the same image through the same idle gate. After changing OmniRoute
# configuration, re-render (`bash setup/render-unit.sh`) and run this; never recreate the container by hand. It can
# defer for hours (LLM_ENV_UPDATE_MAX_WAIT_SECONDS, default 10800 s) until OmniRoute has no load.
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

work="$(private_work_dir)"               # 0700; private artifacts, projection, deadline, password files
# shellcheck disable=SC1091 # Resolved from this script at runtime.
# shellcheck source=omniroute-apply-when-idle.sh
source "$REPO_DIR/scripts/omniroute-apply-when-idle.sh"   # apply_when_idle, stop_observer, write_pw
trap 'stop_observer; rm -rf "$work"' EXIT
trap_signals_to_exit

# 1. Lock before any staging. The path comes from the inspected backend; the file is never unlinked.
acquire_update_lock

# 2. Preflight (inspects identity after the lock): artifacts in $work, key=value on stdout.
rc=0
cli preflight --config "$config_path" --compose "$compose_path" --out-dir "$work" >"$work/deploy.env" || rc=$?
if (( rc != 0 )); then cat "$work/deploy.env"; exit "$rc"; fi
container_id="" image_id="" image_ref="" stop_seconds="" os_name="" architecture="" variant="" state="" drift=""
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
        drift) drift="$v" ;;
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
# Config drift: the rendered omniroute service differs from the running container (field names only, never values).
if [[ -n "$drift" ]]; then echo "drift: $drift"; fi

# 2b. A running image without an activity profile (shipped, or user-local under ~/.local/state/llm-env) is
#     characterized first, under this lock, in a disposable isolated Podman backend (bounded; phases on stdout). A
#     complete profile is stored locally and the update continues; an incomplete one defers `capability`.
if [[ "$state" == "running" ]]; then
    rc=0
    cli characterize --config-id "$image_id" || rc=$?
    if (( rc != 0 )); then exit "$rc"; fi
fi

# 3. Resolve the immutable target. Nothing to do only when the image AND the service definition both match.
rc=0
cli resolve --reference "$image_ref" --os "$os_name" --arch "$architecture" --variant "$variant" \
    --json "$work/resolved.json" >"$work/resolve.out" || rc=$?
if (( rc != 0 )); then cat "$work/resolve.out"; exit "$rc"; fi
config_id="$(jq -r .config_id "$work/resolved.json")"
immutable_ref="$(jq -r .immutable_ref "$work/resolved.json")"
[[ "$config_id" =~ ^sha256:[0-9a-f]{64}$ ]] || { echo "error: malformed resolved identity" >&2; exit 1; }
if [[ "$config_id" == "$image_id" && -z "$drift" ]]; then echo "unchanged"; exit 0; fi

# 4. Stage and verify by hash. A config-only change (config_id == the running image) pulls the same image by its
#    digest too: no layer transfer, and the digest reference the projection names is then present locally.
#    The configured tag is NOT moved here.
pm pull "$immutable_ref" >/dev/null || { echo "error: podman pull of the resolved image failed (registry, network or disk)" >&2; exit 1; }
pm image inspect --format json "$immutable_ref" >"$work/staged.json" \
    || { echo "error: the pulled image cannot be inspected" >&2; exit 1; }
cli verify-staged --resolved "$work/resolved.json" --inspect "$work/staged.json"

# 5. Both callers (a newer image, config drift with the same image) share one idle-gated recreate.
apply_when_idle "$config_id" "$immutable_ref"
