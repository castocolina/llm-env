#!/usr/bin/env bash
# Idle-aware System One (Laya) updater. Exit 0 = unchanged/updated, 75 = safe deferral, 1 = error.
# The last stdout line is the result: `unchanged`, `updated laya <old> -> <new>` or `deferred: <reason>`;
# an error prints `error: ...` on stderr. Automation runs this script directly (make turns 75 into 2).
# Laya is built locally from the PyPI pin `ARG LAYA_VERSION` in docker/system-one/Dockerfile; there is no registry image.
# Order: build the candidate beside production (:rocm-cand), prefetch its weights, smoke-test it on CPU in a throwaway
# container, wait until Laya's /health reports `idle_seconds` >= LLM_ENV_UPDATE_IDLE_SECONDS (default 300; it can
# defer for LLM_ENV_UPDATE_MAX_WAIT_SECONDS, default 10800), then a detached worker promotes the tags, re-renders and
# replaces only system-one through the shared preflight/projection/replace/verify path. A failed swap is rolled back
# (the previous base is kept as :rocm-prev). Success rewrites the Dockerfile pin: commit it.
# Config drift: with the target equal to the pin, a rendered system-one service that differs from the running container
# (after editing system_one in models.yml and `bash setup/render-unit.sh`) prints `drift: <fields>`, prefetches the
# configured weights and recreates it through the same idle gate; a failed config swap is reported, not rolled back.
# Target: LAYA_VERSION=<x.y.z>, else the newest PyPI release (scripts/system-one-check.sh).
set -euo pipefail
# shellcheck disable=SC1091 # Resolved from this script at runtime.
# shellcheck source=../tools/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/../tools/lib.sh"
# shellcheck disable=SC1091 # Resolved from this script at runtime.
# shellcheck source=service-replace.sh
source "$REPO_DIR/scripts/service-replace.sh"
require_cmd podman jq yq uv curl flock setsid timeout

# shellcheck disable=SC2153,SC2034 # CONFIG_PATH/COMPOSE_FILE are exported by tools/lib.sh.
config_path="$CONFIG_PATH"
compose_path="$COMPOSE_FILE"
unset COMPOSE_FILE COMPOSE_INSPECT_DIR
llm=(uv run --project "$REPO_DIR" "$REPO_DIR/llmenv.py" omniroute-update)
backend_env_init
cli() { "${llm[@]}" "$@" 9>&-; }
# shellcheck disable=SC2154 # backend_env is filled by backend_env_init above.
pm() { "${backend_env[@]}" podman "$@" 9>&-; }
defer() { echo "deferred: $1"; exit 75; }

dockerfile="$REPO_DIR/docker/system-one/Dockerfile"
image="$(yq -r '.system_one.image' "$config_path")"
port="$(yq -r '.system_one.port' "$config_path")"
cand="${image%:*}:rocm-cand" prev="${image%:*}:rocm-prev"
health_timeout="$LLM_ENV_SYSTEM_ONE_HEALTH_TIMEOUT_SECONDS"

# One typed request, the benchmark's own shape; prints the response JSON or fails unless it is a Laya answer.
probe() {
    local p="$1" text="$2"
    curl -fsS -m 60 -X POST "http://127.0.0.1:${p}/v1/systemone" -H 'content-type: application/json' \
        --data-binary @- <<EOF | jq -e 'select((.model | test("^laya-")) and (.answers.team.choice | type == "string") and (.answers.urgent.noul | type == "number"))'
{"model":"jev-1","state":{"ticket":"${text}"},"questions":{"team":{"type":"choice","instructions":"Which team should handle this ticket?","criteria":{"billing":"charges, invoices, refunds","technical":"bugs, crashes, outages","other":"general questions"}},"urgent":{"type":"noul","instructions":"Is this blocking customers right now?"}}}
EOF
}
probes() {   # english + multilingual, one JSON array
    { probe "$1" "Our whole site is down and no customer can pay right now" \
        && probe "$1" "Me cobraron dos veces este mes, por favor reembolso"; } | jq -s .
}

# ---------------------------------------------------------------- worker: the swap (owns $work and the lock FD 9)
if [[ "${1:-}" == "--worker" ]]; then
    work="$2" old_version="$3" new_version="$4" mode="$5"
    trap 'rm -rf "$work"' EXIT
    trap_signals_to_exit
    # shellcheck disable=SC2034 # project/provider are read by replace_service.
    stage="" project="$(basename "$(dirname "$compose_path")")" provider="$(command -v podman-compose || true)"
    # shellcheck disable=SC2329 # called by replace_service.
    fail() { echo "error: stage ${stage}: $1" >&2; exit 1; }   # replace_service's fail; it runs in a subshell below
    rendered_observer() { pm image inspect --format '{{.Id}}' "$(yq -r '.services["system-one"].image' "$compose_path")"; }
    old_base="$(pm image inspect --format '{{.Id}}' "$image")"
    pm tag "$old_base" "$prev"

    swap() {   # promote the candidate, re-render, replace system-one through the shared path, check it answers
        local container_id stop_seconds observer
        if [[ "$mode" == "version" ]]; then   # a config drift is already rendered
            stage="tag"; pm tag "$cand" "$image" || return 1
            stage="render"; bash "$REPO_DIR/setup/render-unit.sh" >"$work/render.log" 2>&1 || return 1
        fi
        stage="preflight"
        cli preflight --service system-one --config "$config_path" --compose "$compose_path" --out-dir "$work" \
            >"$work/deploy.env" || return 1
        container_id="$(sed -n 's/^container_id=//p' "$work/deploy.env")"
        stop_seconds="$(sed -n 's/^stop_seconds=//p' "$work/deploy.env")"
        # shellcheck disable=SC2034 # read by replace_service.
        provider="$(sed -n 's/^provider=//p' "$work/deploy.env")"
        stage="projection"; observer="$(rendered_observer)" || return 1
        cli projection --dir "$work" --service system-one --image "$observer" --out "$work/projection.yml" || return 1
        stage="replace"; ( replace_service system-one "$container_id" "$stop_seconds" "$work/projection.yml" ) || return 1
        stage="verify"
        cli verify-commit --dir "$work" --service system-one --old-id "$container_id" --config-id "sha256:${observer}" \
            --timeout "$health_timeout" >"$work/verify.out" || return 1
        stage="answers"; probes "$port" >"$work/after.json" || return 1
    }
    rollback() {   # back to the previous base; recreate only when the running container is not the rendered one
        local cid running want
        stage="rollback tag"; pm tag "$prev" "$image" || return 1
        stage="rollback render"; bash "$REPO_DIR/setup/render-unit.sh" >"$work/render.log" 2>&1 || return 1
        want="$(rendered_observer)" || return 1
        cid="$(pm ps -a --filter label=io.podman.compose.service=system-one --format '{{.ID}}')"
        running=""; [[ -z "$cid" ]] || running="$(pm inspect --format '{{.Image}} {{.State.Running}}' "$cid")"
        if [[ "$running" != "$want true" ]]; then
            stage="rollback replace"; ( replace_service system-one "$cid" 30 "$compose_path" ) || return 1
        fi
        stage="rollback health"; LLM_ENV_HEALTH_TIMEOUT_SECONDS="$health_timeout" wait_for_health "$port"
    }

    if swap; then
        jq -n -r --slurpfile a "$work/before.json" --slurpfile b "$work/after.json" '
            [$a[0][] | .answers] as $x | [$b[0][] | .answers] as $y
            | ([$x | paths(numbers) as $p | (($x | getpath($p)) - (($y | getpath($p)) // 0)) | fabs] | max) as $d
            | ([range(0; $x | length) as $i | select($x[$i].team.choice != $y[$i].team.choice
                or $x[$i].urgent.noul != $y[$i].urgent.noul)] | length) as $f
            | "answers: largest probability change \($d); changed decisions \($f)/\($x | length) (information only)"'
        prune_superseded_build_images   # the promoted swap moved :rocm-prev: its former image is garbage now
        if [[ "$mode" == "drift" ]]; then echo "updated system-one configuration"; exit 0; fi
        sed -i "s/^ARG LAYA_VERSION=.*/ARG LAYA_VERSION=${new_version}/" "$dockerfile"
        echo "previous image kept as ${prev}; commit docker/system-one/Dockerfile"
        echo "updated laya ${old_version} -> ${new_version}"
        exit 0
    fi
    if [[ "$mode" == "drift" ]]; then
        echo "error: configuration update failed at stage: ${stage}; no rollback for a config change: revert system_one in models.yml, run 'bash setup/render-unit.sh' and rerun" >&2
        exit 1
    fi
    echo "error: update failed at stage: ${stage}; rolling back to laya ${old_version}" >&2
    if rollback; then
        echo "error: rolled back to laya ${old_version}; the candidate stays as ${cand} for inspection" >&2
    else
        echo "error: ROLLBACK FAILED at stage: ${stage}; the previous image is ${prev}; see 'make status'" >&2
    fi
    exit 1
fi

# ---------------------------------------------------------------- main: everything here leaves production untouched
[[ "$(yq -r '.system_one.enabled' "$config_path")" == "true" ]] || { echo "error: System One is disabled" >&2; exit 1; }
pinned="$(sed -n 's/^ARG LAYA_VERSION=//p' "$dockerfile")"
target="${LAYA_VERSION:-}"
if [[ -z "$target" ]]; then target="$(bash "$REPO_DIR/scripts/system-one-check.sh" | jq -r '.latest.laya')"; fi
[[ "$target" =~ ^[0-9]+(\.[0-9]+)*$ ]] || { echo "error: invalid Laya version: ${target}" >&2; exit 1; }

ov_args=()
if [[ -v LLM_ENV_UPDATE_IDLE_SECONDS ]]; then ov_args+=(--idle "$LLM_ENV_UPDATE_IDLE_SECONDS"); fi
if [[ -v LLM_ENV_UPDATE_MAX_WAIT_SECONDS ]]; then ov_args+=(--max-wait "$LLM_ENV_UPDATE_MAX_WAIT_SECONDS"); fi
overrides_out="$(cli overrides "${ov_args[@]}")" || exit 1
min_wait="$(sed -n 's/^min_wait=//p' <<<"$overrides_out")"
max_wait="$(sed -n 's/^max_deferred_wait=//p' <<<"$overrides_out")"
[[ "$min_wait" =~ ^[0-9]+$ && "$max_wait" =~ ^[0-9]+$ ]] || exit 1

work="$(private_work_dir)"
smoke="" sink_dir=""   # named only once each resource exists, so the trap removes nothing else
trap '[[ -z "$smoke" ]] || pm rm -f "$smoke" >/dev/null 2>&1 || true; [[ -z "$sink_dir" ]] || pm unshare rm -rf "$sink_dir" >/dev/null 2>&1 || true; rm -rf "$work"' EXIT
trap_signals_to_exit
acquire_update_lock --container system-one

mode="version"
if [[ "$target" == "$pinned" ]]; then
    rc=0
    cli preflight --service system-one --config "$config_path" --compose "$compose_path" --out-dir "$work" \
        >"$work/deploy.env" || rc=$?
    if (( rc != 0 )); then cat "$work/deploy.env"; exit "$rc"; fi
    drift="$(sed -n 's/^drift=//p' "$work/deploy.env")"
    if [[ -z "$drift" ]]; then echo "unchanged"; exit 0; fi
    echo "drift: ${drift}"
    mode="drift"
    echo "phase: prefetching the configured weights"
    system_one_cache_check "$image" prefetch >"$work/prefetch.log" 2>&1 \
        || { tail -20 "$work/prefetch.log" >&2; echo "error: weight prefetch failed; production untouched" >&2; exit 1; }
fi
if [[ "$mode" == "version" ]]; then
    echo "phase: building laya ${target} as ${cand}"
    LAYA_VERSION="$target" SYSTEM_ONE_BUILD_TAG="$cand" bash "$REPO_DIR/scripts/system-one-build.sh" >"$work/build.log" 2>&1 \
        || { tail -40 "$work/build.log" >&2; echo "error: candidate build failed; production untouched" >&2; exit 1; }
    echo "phase: prefetching weights with the candidate"
    system_one_cache_check "$cand" prefetch >"$work/prefetch.log" 2>&1 \
        || { tail -20 "$work/prefetch.log" >&2; echo "error: candidate weight prefetch failed; production untouched" >&2; exit 1; }

    # CPU smoke: the candidate's own server (observer included) answers both probes without touching the GPU.
    echo "phase: smoke-testing the candidate on CPU"
    models="$(yq -r '.system_one.models' "$config_path")"
    smoke="llm-env-system-one-smoke-$$"
    # Keep the sink outside XDG_RUNTIME_DIR: the container uid cannot traverse its 0700 work directory.
    sink_dir="$(mktemp -d "${TMPDIR:-/tmp}/span-sink.XXXXXX")"
    bash "$REPO_DIR/scripts/prepare-laya-spans.sh" "$sink_dir"
    pm run -d --name "$smoke" -p 127.0.0.1::8000 -e LAYA_DEVICE=cpu -e LAYA_REVISION=reviewed -e HF_HUB_OFFLINE=1 \
        -e "LAYA_MODELS=${models}" -e LAYA_SPAN_SINK=/var/log/laya/laya-spans.jsonl \
        -v "$sink_dir:/var/log/laya:Z" -v llm-env-system-one-cache:/home/laya/.cache/huggingface "$cand" >/dev/null
    smoke_port="$(pm port "$smoke" 8000/tcp | sed -n '1s/.*://p')"
    if ! LLM_ENV_HEALTH_TIMEOUT_SECONDS="$health_timeout" wait_for_health "$smoke_port" || ! probes "$smoke_port" >/dev/null; then
        pm logs --tail 30 "$smoke" >&2 || true
        echo "error: candidate smoke test failed; production untouched" >&2
        exit 1
    fi
    for _ in {1..30}; do [[ -s "$sink_dir/laya-spans.jsonl" ]] && break; sleep 1; done
    if [[ ! -s "$sink_dir/laya-spans.jsonl" ]] || ! jq -e 'select(.v == 1 and .endpoint == "/v1/systemone")' "$sink_dir/laya-spans.jsonl" >/dev/null; then
        pm logs --tail 30 "$smoke" >&2 || true
        echo "error: candidate span sink smoke test failed; production untouched" >&2
        exit 1
    fi
    pm rm -f "$smoke" >/dev/null
    pm unshare rm -rf "$sink_dir" >/dev/null 2>&1 || true
    sink_dir=""
fi

# Idle gate: Laya's own clock of the last request. A /health poll does not reset it.
echo "phase: waiting for System One to be idle for ${min_wait}s"
deadline=$(( SECONDS + max_wait ))
while true; do
    idle="$(curl -fsS -m 5 "http://127.0.0.1:${port}/health" | jq -r '.idle_seconds // empty')" || defer health
    [[ "$idle" =~ ^[0-9]+(\.[0-9]+)?$ ]] || defer health
    if (( ${idle%.*} >= min_wait )); then break; fi
    if (( SECONDS >= deadline )); then defer deadline; fi
    sleep 5
done
probes "$port" >"$work/before.json" || { echo "error: the running System One does not answer; not swapping" >&2; exit 1; }

# The worker inherits FD 9 (the lock) and owns $work from here; a signal to this shell must not cut the swap short.
budget=$(( 2 * (3 * health_timeout + 300) ))
trap - EXIT HUP INT TERM
rc=0
setsid --wait timeout --kill-after=10 "$budget" bash "${BASH_SOURCE[0]}" --worker "$work" "$pinned" "$target" "$mode" || rc=$?
rm -rf "$work"
if (( rc == 124 || rc == 137 )); then
    echo "error: the swap exceeded its time budget; check 'podman ps' and 'make status' (previous image: ${prev})" >&2
    exit 1
fi
exit "$rc"
