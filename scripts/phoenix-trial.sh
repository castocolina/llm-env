#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck disable=SC1091
source "$REPO_DIR/tools/lib.sh"

readonly SERVER_IMAGE='docker.io/arizephoenix/phoenix:20.23.0@sha256:c542e7de1dc1fdd0f938de337d44ebefa7266e91391eed6e7978523d7f6e1f2f'
readonly FIXTURE="$REPO_DIR/docker/phoenix-trial/fixtures/laya-synthetic.json"
readonly COMPOSE_FILE="$REPO_DIR/docker/phoenix-trial/compose.yml"
readonly DOCKERFILE="$REPO_DIR/docker/phoenix-trial/Dockerfile.sender"
readonly ACTION="${1:-status}"

project="${LLM_ENV_PHOENIX_TRIAL_PROJECT:-llm-env-phoenix-trial}"
port="${LLM_ENV_PHOENIX_PORT:-6006}"
retention="${LLM_ENV_PHOENIX_RETENTION_DAYS:-7}"
if [[ ! "$project" =~ ^llm-env-phoenix-trial(-it-[a-z0-9]{8,32})?$ ]]; then
    die "invalid Phoenix trial project name"
fi
if [[ ! "$port" =~ ^[1-9][0-9]{0,4}$ ]] || ((port > 65535)); then
    die "LLM_ENV_PHOENIX_PORT must be an integer from 1 to 65535"
fi
if [[ ! "$retention" =~ ^[1-9][0-9]*$ ]]; then
    die "LLM_ENV_PHOENIX_RETENTION_DAYS must be a positive integer"
fi
case "$ACTION" in
    up|seed|status|verify-persistence|stop) ;;
    *) die "usage: phoenix-trial.sh [up|seed|status|verify-persistence|stop]" ;;
esac

volume="${project}-data"
sender_image="localhost/${project}-sender:trial"
state_base="${XDG_STATE_HOME:-$HOME/.local/state}"
if [[ "$state_base" != /* ]]; then
    die "XDG_STATE_HOME must be an absolute path"
fi
state_base="$(realpath -m -- "$state_base")"
state_dir="$state_base/llm-env/phoenix-trial/$project"

# Reject symlinks in trial-local state paths. Trusted canonical HOME aliases
# are handled by the caller's resolved XDG_STATE_HOME/default path; the path
# below the chosen state root must be ordinary directories. (Shared
# implementation lives in tools/lib.sh.)

trial_compose() (
    # The rootless port-forwarder can outlive `podman compose up`; do not leak
    # the lifecycle lock descriptor into the provider or its descendants.
    exec 9>&-
    local selector
    local -a unset_selectors=()
    while IFS= read -r selector; do
        unset_selectors+=(-u "$selector")
    done < <(compgen -e | grep '^COMPOSE_')
    env "${unset_selectors[@]}" \
        PHOENIX_TRIAL_PROJECT="$project" \
        PHOENIX_TRIAL_VOLUME="$volume" \
        PHOENIX_TRIAL_PORT="$port" \
        PHOENIX_TRIAL_RETENTION_DAYS="$retention" \
        PHOENIX_TRIAL_SENDER_IMAGE="$sender_image" \
        podman compose --env-file /dev/null -f "$COMPOSE_FILE" -p "$project" "$@"
)

ensure_state_dir() {
    reject_symlink_components "$state_base"
    mkdir -p -- "$state_dir"
    chmod 700 -- "$state_dir"
    reject_symlink_components "$state_dir"
}

with_lock() {
    ensure_state_dir
    exec 9>"$state_dir/lifecycle.lock"
    chmod 600 -- "$state_dir/lifecycle.lock"
    flock -n 9 || die "Phoenix trial lifecycle is already running for $project"
}

labels_match() {
    local json="$1"
    jq -e --arg project "$project" \
        '."llm-env.scope" == "phoenix-trial" and ."llm-env.project" == $project' \
        <<< "$json" >/dev/null
}

require_owned_resources() {
    local labels id
    if podman volume inspect --format '{{json .Labels}}' "$volume" >/dev/null 2>&1; then
        labels="$(podman volume inspect --format '{{json .Labels}}' "$volume")" \
            || die "could not inspect Phoenix trial volume labels"
        labels_match "$labels" || die "refusing unowned Phoenix trial volume $volume"
    elif [[ "$ACTION" != up ]]; then
        die "Phoenix trial volume $volume does not exist"
    fi
    if podman network inspect --format '{{json .Labels}}' "${project}_trial" >/dev/null 2>&1; then
        labels="$(podman network inspect --format '{{json .Labels}}' "${project}_trial")" \
            || die "could not inspect Phoenix trial network labels"
        labels_match "$labels" || die "refusing unowned Phoenix trial network"
    fi
    if podman image exists "$sender_image"; then
        labels="$(podman image inspect --format '{{json .Labels}}' "$sender_image")" \
            || die "could not inspect Phoenix trial sender image labels"
        labels_match "$labels" || die "refusing unowned Phoenix trial sender image"
    fi
    while IFS= read -r id; do
        [[ -n "$id" ]] || continue
        labels="$(podman inspect --format '{{json .Config.Labels}}' "$id")" \
            || die "could not inspect Phoenix trial container labels"
        labels_match "$labels" || die "refusing unowned Phoenix trial container $id"
        jq -e --arg project "$project" \
            '."com.docker.compose.project" == $project and
             (."com.docker.compose.service" == "phoenix" or ."com.docker.compose.service" == "sender")' \
            <<< "$labels" >/dev/null || die "Phoenix trial container has unexpected compose ownership"
    done < <(podman ps -aq --filter "label=llm-env.project=$project")
}

require_owned_server() {
    local id labels
    id="$(podman ps -q --filter "label=llm-env.project=$project" \
        --filter label=com.docker.compose.service=phoenix)"
    [[ -n "$id" ]] || die "owned Phoenix trial server is not running"
    labels="$(podman inspect --format '{{json .Config.Labels}}' "$id")" \
        || die "could not inspect Phoenix trial server labels"
    labels_match "$labels" || die "refusing unowned Phoenix trial container $id"
    jq -e --arg project "$project" \
        '."com.docker.compose.project" == $project and ."com.docker.compose.service" == "phoenix"' \
        <<< "$labels" >/dev/null || die "Phoenix trial container has unexpected compose ownership"
}

trial_vars() {
    export PHOENIX_TRIAL_PROJECT="$project"
    export PHOENIX_TRIAL_VOLUME="$volume"
    export PHOENIX_TRIAL_PORT="$port"
    export PHOENIX_TRIAL_RETENTION_DAYS="$retention"
    export PHOENIX_TRIAL_SENDER_IMAGE="$sender_image"
}

# Select the scratch directory for Phoenix build contexts. Every Phoenix
# build scratch directory must live inside the repository tmp/ tree: a
# caller-inherited TMPDIR (commonly /tmp) is never trusted to be private,
# short enough for podman's unix-socket path, or free of symlink tricks.
# An absolute TMPDIR whose literal path has no symlink components and that
# canonicalizes inside repo tmp is honored; any other clean path (including
# the inherited /tmp default) falls back to repo tmp; a symlinked path is
# refused, never escaped around. Validation happens here, before any
# mktemp/cp/podman-build work.
resolve_build_tmpdir() {
    local repo_tmp candidate
    repo_tmp="$(realpath -m -- "$REPO_DIR/tmp")"
    candidate="${TMPDIR:-}"
    if [[ -n "$candidate" && "$candidate" != /* ]]; then
        candidate=""
    fi
    if [[ -n "$candidate" ]]; then
        reject_symlink_components "$candidate"
        candidate="$(realpath -m -- "$candidate")"
        if [[ "$candidate" != "$repo_tmp" && "$candidate" != "$repo_tmp"/* ]]; then
            candidate="$repo_tmp"
        fi
    else
        candidate="$repo_tmp"
    fi
    reject_symlink_components "$candidate"
    printf '%s\n' "$candidate"
}

build_sender() {
    local context tmpdir
    tmpdir="$(resolve_build_tmpdir)"
    mkdir -p -- "$tmpdir"
    reject_symlink_components "$tmpdir"
    context="$(mktemp -d "$tmpdir/phoenix-trial-build.XXXXXX")" \
        || die "could not create private sender build context"
    chmod 700 -- "$context"
    local -a sender_files=(
        llmenv.py pyproject.toml uv.lock
        pylib/__init__.py pylib/agent_runner.py pylib/budget.py pylib/compose.py
        pylib/config.py pylib/detect.py pylib/dotenv.py pylib/gguf.py
        pylib/omniroute.py pylib/omniroute_combos.py pylib/presets.py
        pylib/remote_setup.py pylib/resources.py pylib/transcript.py pylib/phoenix_trial.py
        docker/phoenix-trial/fixtures/laya-synthetic.json
    )
    cp --parents -- "${sender_files[@]}" "$context"
    podman build --label llm-env.scope=phoenix-trial \
        --label "llm-env.project=$project" -f "$DOCKERFILE" \
        -t "$sender_image" "$context"
    rm -rf -- "$context"
}

check_port_free() {
    local listeners
    listeners="$(ss -H -ltn "sport = :$port")" \
        || die "could not inspect loopback listeners with ss"
    [[ -z "$listeners" ]] || die "Phoenix trial port 127.0.0.1:$port is already in use"
}

sample_volume_bytes() {
    local phase="$1" output bytes timestamp sample_dir sample_path temporary
    output="$(podman run --rm --network none \
        --label llm-env.scope=phoenix-trial --label "llm-env.project=$project" \
        --volume "$volume:/inspect:ro" "$sender_image" volume-bytes --path /inspect)" \
        || die "read-only Phoenix volume-byte inspection failed for $phase"
    bytes="$(jq -er '.bytes | select(type == "number" and . >= 0 and floor == .)' <<< "$output")" \
        || die "Phoenix volume-byte inspector returned an invalid result"
    sample_dir="$state_dir/resource-samples"
    mkdir -p -- "$sample_dir"
    chmod 700 -- "$sample_dir"
    timestamp="$(date -u +%Y%m%dT%H%M%S.%NZ)"
    sample_path="$sample_dir/$phase-$timestamp-$$.json"
    temporary="$(mktemp "$sample_dir/.sample.XXXXXX")"
    chmod 600 -- "$temporary"
    jq -n --arg phase "$phase" --arg sampled_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
        --arg volume "$volume" --argjson bytes "$bytes" \
        '{phase:$phase,sampled_at:$sampled_at,volume:$volume,logical_bytes:$bytes}' > "$temporary"
    mv -- "$temporary" "$sample_path"
    printf 'Phoenix trial volume sample %s: %s logical bytes (%s)\n' \
        "$phase" "$bytes" "$sample_path" >&2
}

wait_ready() {
    local deadline=$((SECONDS + 60))
    while ((SECONDS < deadline)); do
        if curl --noproxy '*' --connect-timeout 2 --max-time 5 -fsS \
            "http://127.0.0.1:$port/readyz" >/dev/null 2>&1; then
            return 0
        fi
        sleep 1
    done
    die "Phoenix trial readiness timed out; preserve the scoped resources for diagnosis"
}

prepare_run() {
    local receipt="$1"
    llmenv phoenix-trial prepare --manifest "$FIXTURE" --state-dir "$state_dir" > "$receipt"
}

recovery_hint() {
    local run_id="$1" expected_path="$2"
    printf 'Phoenix seed outcome is uncertain. Recovery is read-only:\n' >&2
    printf 'UV_OFFLINE=1 uv run llmenv.py phoenix-trial verify --base-url %q --run-id %q --expected %q\n' \
        "http://127.0.0.1:$port" "$run_id" "$expected_path" >&2
}

sender_failure_diagnostic() {
    local result_file="$1" byte_count message
    byte_count="$(wc -c < "$result_file")"
    if ((byte_count > 4096)); then
        printf 'Phoenix sender diagnostic omitted (output exceeded 4096 bytes).\n' >&2
        return
    fi
    message="$(jq -er 'select(type == "object" and keys == ["error"] and (.error | type == "string")) | .error' \
        "$result_file" 2>/dev/null || true)"
    case "$message" in
        'Phoenix POST outcome is uncertain (URLError); use read-only verify'|\
        'Phoenix POST outcome is uncertain (TimeoutError); use read-only verify'|\
        'Phoenix POST outcome is uncertain (OSError); use read-only verify'|\
        'Phoenix POST outcome is uncertain (ConnectionError); use read-only verify'|\
        'Phoenix POST outcome is uncertain (HTTPError); use read-only verify'|\
        'Phoenix POST outcome is uncertain (PhoenixTrialError); use read-only verify'|\
        'Phoenix POST outcome is uncertain (_TransientTransportError); use read-only verify'|\
        'Phoenix POST acceptance counts do not match the expected span count'|\
        'Phoenix POST exhausted the verification deadline; use read-only verify'|\
        'Phoenix POST returned HTTP '[1-5][0-9][0-9]'; no retry attempted')
            printf 'Phoenix sender diagnostic: %s\n' "$message" >&2
            ;;
        *)
            printf 'Phoenix sender diagnostic omitted (no approved safe error format).\n' >&2
            ;;
    esac
}

seed_trial() {
    local receipt run_id expected_path result_tmp rc expected_summary expected_ids
    require_owned_resources
    require_owned_server
    wait_ready
    podman image exists "$sender_image" || build_sender
    while IFS= read -r -d '' attempt; do
        run_dir="${attempt%/attempt.json}"
        if [[ ! -f "$run_dir/seed-result.json" ]]; then
            printf 'WARNING: prior Phoenix trial run %s has an attempt marker but no valid seed receipt; persisted data may or may not exist. No retry or reseed will be attempted.\n' "${run_dir##*/}" >&2
        fi
    done < <(find "$state_dir/runs" -mindepth 2 -maxdepth 2 -type f -name attempt.json -print0 2>/dev/null)
    receipt="$(mktemp "$state_dir/prepare.XXXXXX")"
    chmod 600 -- "$receipt"
    if ! prepare_run "$receipt"; then
        rm -f -- "$receipt"
        die "Phoenix trial preparation failed; no sender POST was attempted"
    fi
    run_id="$(jq -er '.run_id | select(test("^[0-9a-f]{12}4[0-9a-f]{3}[89ab][0-9a-f]{15}$"))' "$receipt")" \
        || die "Phoenix trial prepare returned an invalid run ID"
    expected_path="$(jq -er --arg root "$state_dir/runs/$run_id/expected.json" \
        'select(.expected_path == $root) | .expected_path' "$receipt")" \
        || die "Phoenix trial prepare returned an unexpected artifact path"
    rm -f -- "$receipt"
    llmenv phoenix-trial mark-attempt --expected "$expected_path" >/dev/null \
        || die "Phoenix trial attempt marker failed; no sender POST was attempted"
    result_tmp="$(mktemp "$state_dir/runs/$run_id/seed-result.XXXXXX")"
    chmod 600 -- "$result_tmp"
    set +e
    trial_compose --profile tools run --rm -T sender seed \
        --base-url http://phoenix:6006 --expected - < "$expected_path" > "$result_tmp"
    rc=$?
    set -e
    if ((rc != 0)); then
        sender_failure_diagnostic "$result_tmp"
        rm -f -- "$result_tmp"
        recovery_hint "$run_id" "$expected_path"
        return "$rc"
    fi
    expected_summary="$(jq -cS '.summary' "$expected_path")"
    expected_ids="$(jq -cS '[.spans[].context.span_id] | sort' "$expected_path")"
    jq -e --arg run_id "$run_id" --argjson summary "$expected_summary" \
        --argjson ids "$expected_ids" '
        .schema_version == 1 and .status == "verified" and
        .project == "laya-phoenix-trial" and .run_id == $run_id and
        .summary == $summary and .verified_span_ids == $ids
    ' "$result_tmp" >/dev/null || {
        rm -f -- "$result_tmp"
        die "Phoenix sender returned a receipt that does not match the prepared artifact"
    }
    mv -- "$result_tmp" "$state_dir/runs/$run_id/seed-result.json"
    chmod 600 -- "$state_dir/runs/$run_id/seed-result.json"
    sample_volume_bytes after-seed
    log_info "Phoenix trial run $run_id seeded and exactly verified"
}

verify_sender_run() {
    local run_id="$1" expected_path="$2" receipt summary ids
    receipt="$(mktemp "$state_dir/verify-result.XXXXXX")"
    chmod 600 -- "$receipt"
    if ! trial_compose --profile tools run --rm -T sender verify \
        --base-url http://phoenix:6006 --run-id "$run_id" --expected - \
        < "$expected_path" > "$receipt"; then
        rm -f -- "$receipt"
        die "Phoenix sender read-only persistence verification failed"
    fi
    summary="$(jq -cS '.summary' "$expected_path")"
    ids="$(jq -cS '[.spans[].context.span_id] | sort' "$expected_path")"
    jq -e --arg run_id "$run_id" --argjson summary "$summary" --argjson ids "$ids" '
        .schema_version == 1 and .status == "verified" and
        .project == "laya-phoenix-trial" and .run_id == $run_id and
        .summary == $summary and .verified_span_ids == $ids
    ' "$receipt" >/dev/null || {
        rm -f -- "$receipt"
        die "Phoenix sender persistence receipt does not match the prepared artifact"
    }
    rm -f -- "$receipt"
}

inspect_server() {
    local id
    id="$(podman ps -q --filter "label=com.docker.compose.project=$project" \
        --filter label=com.docker.compose.service=phoenix)"
    [[ -n "$id" ]] || die "owned Phoenix trial server is not running"
    podman inspect "$id"
}

verify_persistence() {
    local run_dir expected_path run_id before after before_id after_id before_image after_image before_volume after_volume latest_prepared
    require_owned_resources
    require_owned_server
    latest_prepared="$(find "$state_dir/runs" -mindepth 2 -maxdepth 2 -type f -name prepared.json -printf '%T@ %h\n' 2>/dev/null | sort -nr | head -n 1)"
    run_dir="${latest_prepared#* }"
    [[ -n "$latest_prepared" && -d "$run_dir" && ! -L "$run_dir" ]] \
        || die "no prepared Phoenix trial run is available for persistence verification"
    run_id="${run_dir##*/}"
    [[ "$run_id" =~ ^[0-9a-f]{12}4[0-9a-f]{3}[89ab][0-9a-f]{15}$ ]] \
        || die "newest prepared Phoenix trial run has an invalid identity"
    expected_path="$run_dir/expected.json"
    [[ -f "$expected_path" && ! -L "$expected_path" ]] \
        || die "newest prepared Phoenix trial run has no safe expected artifact"
    jq -e --arg run_id "$run_id" --arg expected_path "$expected_path" \
        '.schema_version == 1 and .status == "prepared" and .project == "laya-phoenix-trial" and .run_id == $run_id and .expected_path == $expected_path' \
        "$run_dir/prepared.json" >/dev/null \
        || die "newest prepared Phoenix trial receipt is corrupt or mismatched"
    llmenv phoenix-trial verify --base-url "http://127.0.0.1:$port" \
        --run-id "$run_id" --expected "$expected_path" >/dev/null
    sample_volume_bytes before-recreation
    podman image exists "$SERVER_IMAGE" || die "pinned Phoenix server image is missing; refusing implicit pull during recreation"
    printf 'Recreating only Phoenix trial service %s; synthetic data remains unchanged.\n' "$project" >&2
    before="$(inspect_server)"
    before_id="$(jq -er '.[0].Id' <<< "$before")"
    before_image="$(jq -er '.[0].Image' <<< "$before")"
    before_volume="$(jq -er '[.[0].Mounts[] | select(.Destination == "/mnt/data") | .Name] | if length == 1 then .[0] else error("expected one Phoenix data volume") end' <<< "$before")"
    [[ "$before_volume" == "$volume" ]] || die "Phoenix trial server is not attached to the expected data volume"
    trial_compose up -d --no-deps --force-recreate phoenix
    wait_ready
    after="$(inspect_server)"
    after_id="$(jq -er '.[0].Id' <<< "$after")"
    after_image="$(jq -er '.[0].Image' <<< "$after")"
    after_volume="$(jq -er '[.[0].Mounts[] | select(.Destination == "/mnt/data") | .Name] | if length == 1 then .[0] else error("expected one Phoenix data volume") end' <<< "$after")"
    [[ "$before_id" != "$after_id" && "$before_image" == "$after_image" \
        && "$after_volume" == "$before_volume" && "$after_volume" == "$volume" ]] \
        || die "Phoenix trial recreation changed container, image or data volume identity"
    verify_sender_run "$run_id" "$expected_path"
    sample_volume_bytes after-recreation
}

trial_vars
case "$ACTION" in
    status)
        trial_compose ps
        ;;
    up)
        with_lock
        require_cmd podman podman-compose ss curl jq uv flock realpath
        check_port_free
        require_owned_resources
        podman pull "$SERVER_IMAGE"
        # Basic startup never builds or requires the synthetic sender;
        # `seed` builds it on demand as explicit internal tooling.
        if ! podman volume inspect "$volume" >/dev/null 2>&1; then
            podman volume create --label llm-env.scope=phoenix-trial \
                --label "llm-env.project=$project" "$volume" >/dev/null
        fi
        trial_compose up -d phoenix
        wait_ready
        log_info "Phoenix trial is ready at http://127.0.0.1:$port (synthetic data only)"
        ;;
    seed)
        with_lock
        require_cmd podman podman-compose curl jq uv cp mktemp flock realpath
        seed_trial
        ;;
    verify-persistence)
        with_lock
        require_cmd podman podman-compose curl jq uv flock find sort tail realpath
        verify_persistence
        ;;
    stop)
        with_lock
        require_cmd podman podman-compose flock realpath
        require_owned_resources
        trial_compose down
        log_info "Phoenix trial stopped; external volume and host receipts retained"
        ;;
esac
