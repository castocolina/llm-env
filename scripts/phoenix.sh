#!/usr/bin/env bash
# phoenix.sh — normal Phoenix dashboard lifecycle.
#
# The dashboard is a System One companion: it starts with `make start`
# (and at boot) exactly when system_one.enabled is true, and is omitted
# entirely otherwise. It owns a separate rootless compose project
# (docker/phoenix/compose.yml) on its own internal network, published on
# loopback only. Basic startup never builds or requires the synthetic
# sender; seed-synthetic/verify-synthetic are explicit internal test
# tooling and never run during normal startup.
#
# The installed System One observer exports real single, batch, and error
# requests to this local-only Phoenix project; synthetic tooling remains
# explicit test infrastructure and is never used during normal startup.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck disable=SC1091
source "$REPO_DIR/tools/lib.sh"

readonly SERVER_IMAGE='docker.io/arizephoenix/phoenix:20.23.0@sha256:c542e7de1dc1fdd0f938de337d44ebefa7266e91391eed6e7978523d7f6e1f2f'
readonly COMPOSE_FILE="$REPO_DIR/docker/phoenix/compose.yml"
readonly FIXTURE="$REPO_DIR/docker/phoenix-trial/fixtures/laya-synthetic.json"
# The synthetic sender image is shared internal tooling built by
# scripts/phoenix-trial.sh (seed builds it on demand).
readonly SENDER_IMAGE='localhost/llm-env-phoenix-trial-sender:trial'
readonly ACTION="${1:-}"

project="llm-env-phoenix"
port="${LLM_ENV_PHOENIX_PORT:-6006}"
retention="${LLM_ENV_PHOENIX_RETENTION_DAYS:-7}"
health_timeout="${LLM_ENV_PHOENIX_HEALTH_TIMEOUT_SECONDS:-60}"
if [[ ! "$port" =~ ^[1-9][0-9]{0,4}$ ]] || ((port > 65535)); then
    die "LLM_ENV_PHOENIX_PORT must be an integer from 1 to 65535"
fi
if [[ ! "$retention" =~ ^[1-9][0-9]*$ ]]; then
    die "LLM_ENV_PHOENIX_RETENTION_DAYS must be a positive integer"
fi
if [[ ! "$health_timeout" =~ ^[1-9][0-9]*$ ]]; then
    die "LLM_ENV_PHOENIX_HEALTH_TIMEOUT_SECONDS must be a positive integer"
fi
case "$ACTION" in
    start|stop|status|endpoint|prepare|seed-synthetic|verify-synthetic) ;;
    *) die "usage: phoenix.sh [start|stop|status|endpoint|prepare|seed-synthetic|verify-synthetic]" ;;
esac

volume="${project}-data"
internal_network="${project}_internal"
state_base="${XDG_STATE_HOME:-$HOME/.local/state}"
if [[ "$state_base" != /* ]]; then
    die "XDG_STATE_HOME must be an absolute path"
fi
state_base="$(realpath -m -- "$state_base")"
state_dir="$state_base/llm-env/phoenix/$project"

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
    flock -n 9 || die "Phoenix lifecycle is already running for $project"
}

phoenix_enabled() {
    [ -f "$CONFIG_PATH" ] \
        && [ "$(yq -r '.system_one.enabled' "$CONFIG_PATH" 2>/dev/null)" = "true" ]
}

phoenix_compose() (
    # The rootless port-forwarder can outlive `podman compose up`; do not
    # leak the lifecycle lock descriptor into long-lived children.
    exec 9>&-
    local selector
    local -a unset_selectors=()
    while IFS= read -r selector; do
        unset_selectors+=(-u "$selector")
    done < <(compgen -e | grep '^COMPOSE_')
    env "${unset_selectors[@]}" \
        PHOENIX_PROJECT="$project" \
        PHOENIX_VOLUME="$volume" \
        PHOENIX_INTERNAL_NETWORK="$internal_network" \
        PHOENIX_PORT="$port" \
        PHOENIX_RETENTION_DAYS="$retention" \
        PHOENIX_CPU_SHARES="${PHOENIX_CPU_SHARES:-256}" \
        PHOENIX_CPUS_MAX="${PHOENIX_CPUS_MAX:-2}" \
        PHOENIX_MEMORY_MIN_MIB="${PHOENIX_MEMORY_MIN_MIB:-256}" \
        PHOENIX_MEMORY_MAX_MIB="${PHOENIX_MEMORY_MAX_MIB:-1024}" \
        podman compose --env-file /dev/null -f "$COMPOSE_FILE" -p "$project" "$@"
)

# Export the resource environment for the compose render. Values come from
# models.yml (written by setup/start from llmenv resources); the defaults
# are the same pylib/resources.py PHOENIX_* policy constants.
load_resource_env() {
    local cpus_min="0.25"
    PHOENIX_CPUS_MAX="2"
    PHOENIX_MEMORY_MIN_MIB="256"
    PHOENIX_MEMORY_MAX_MIB="1024"
    if [[ -f "$CONFIG_PATH" ]]; then
        cpus_min="$(yq -r '.resources.phoenix.cpus_min // 0.25' "$CONFIG_PATH" 2>/dev/null || printf '0.25')"
        PHOENIX_CPUS_MAX="$(yq -r '.resources.phoenix.cpus_max // 2' "$CONFIG_PATH" 2>/dev/null || printf '2')"
        PHOENIX_MEMORY_MIN_MIB="$(yq -r '.resources.phoenix.memory_min_mib // 256' "$CONFIG_PATH" 2>/dev/null || printf '256')"
        PHOENIX_MEMORY_MAX_MIB="$(yq -r '.resources.phoenix.memory_max_mib // 1024' "$CONFIG_PATH" 2>/dev/null || printf '1024')"
    fi
    # cpu_shares is a RELATIVE WEIGHT (baseline 1024 per share unit), not
    # guaranteed cores; the hard caps are PHOENIX_CPUS_MAX/PHOENIX_MEMORY_MAX_MIB.
    PHOENIX_CPU_SHARES="$(jq -n --argjson c "$cpus_min" '($c * 1024 | round) as $shares | if $shares < 2 then 2 else $shares end')"
    export PHOENIX_CPU_SHARES PHOENIX_CPUS_MAX PHOENIX_MEMORY_MIN_MIB PHOENIX_MEMORY_MAX_MIB
}

labels_match() {
    local json="$1"
    jq -e --arg project "$project" \
        '."llm-env.scope" == "phoenix" and ."llm-env.project" == $project' \
        <<< "$json" >/dev/null
}

owned_server_id() {
    podman ps -q --filter "label=llm-env.project=$project" \
        --filter label=com.docker.compose.service=phoenix
}

require_owned_server() {
    local id labels
    id="$(owned_server_id)"
    [[ -n "$id" ]] || die "owned Phoenix server is not running"
    labels="$(podman inspect --format '{{json .Config.Labels}}' "$id")" \
        || die "could not inspect Phoenix server labels"
    labels_match "$labels" || die "refusing unowned Phoenix container $id"
    jq -e --arg project "$project" \
        '."com.docker.compose.project" == $project and ."com.docker.compose.service" == "phoenix"' \
        <<< "$labels" >/dev/null || die "Phoenix container has unexpected compose ownership"
}

ensure_internal_network() {
    local labels internal
    if podman network inspect "$internal_network" >/dev/null 2>&1; then
        labels="$(podman network inspect --format '{{json .Labels}}' "$internal_network")" \
            || die "could not inspect Phoenix network labels"
        labels_match "$labels" || die "refusing unowned Phoenix network $internal_network"
        internal="$(podman network inspect --format '{{.Internal}}' "$internal_network")" \
            || die "could not inspect Phoenix network isolation"
        [[ "$internal" == true ]] || die "Phoenix trace network must be internal"
    else
        podman network create --internal \
            --label llm-env.scope=phoenix --label "llm-env.project=$project" \
            "$internal_network" >/dev/null
    fi
}

require_owned_resources() {
    local labels id
    if podman volume inspect --format '{{json .Labels}}' "$volume" >/dev/null 2>&1; then
        labels="$(podman volume inspect --format '{{json .Labels}}' "$volume")" \
            || die "could not inspect Phoenix volume labels"
        labels_match "$labels" || die "refusing unowned Phoenix volume $volume"
    elif [[ "$ACTION" != start ]]; then
        die "Phoenix volume $volume does not exist"
    fi
    if podman network inspect --format '{{json .Labels}}' "${project}_internal" >/dev/null 2>&1; then
        labels="$(podman network inspect --format '{{json .Labels}}' "${project}_internal")" \
            || die "could not inspect Phoenix network labels"
        labels_match "$labels" || die "refusing unowned Phoenix network"
    fi
    while IFS= read -r id; do
        [[ -n "$id" ]] || continue
        labels="$(podman inspect --format '{{json .Config.Labels}}' "$id")" \
            || die "could not inspect Phoenix container labels"
        labels_match "$labels" || die "refusing unowned Phoenix container $id"
        jq -e --arg project "$project" \
            '."com.docker.compose.project" == $project and
             ."com.docker.compose.service" == "phoenix"' \
            <<< "$labels" >/dev/null || die "Phoenix container has unexpected compose ownership"
    done < <(podman ps -aq --filter "label=llm-env.project=$project")
}

check_port_free() {
    local listeners
    listeners="$(ss -H -ltn "sport = :$port")" \
        || die "could not inspect loopback listeners with ss"
    [[ -z "$listeners" ]] || die "Phoenix port 127.0.0.1:$port is already in use by another process"
}

wait_ready() {
    local deadline=$((SECONDS + health_timeout))
    while ((SECONDS < deadline)); do
        if curl --noproxy '*' --connect-timeout 2 --max-time 5 -fsS \
            "http://127.0.0.1:$port/readyz" >/dev/null 2>&1; then
            return 0
        fi
        sleep 1
    done
    die "Phoenix readiness timed out after ${health_timeout}s; the scoped resources are preserved for diagnosis"
}

newest_prepared_run() {
    local latest
    latest="$(find "$state_dir/runs" -mindepth 2 -maxdepth 2 -type f -name prepared.json -printf '%T@ %h\n' 2>/dev/null | sort -nr | head -n 1)"
    [[ -n "$latest" ]] || die "no prepared Phoenix run is available; run seed-synthetic first"
    printf '%s\n' "${latest#* }"
}

sender_run() {
    # Run the shared synthetic sender against the dashboard over the
    # internal network only. Distroless server: never assume a shell/curl.
    local -a args=("$@")
    podman run --rm -i --network "${project}_internal" \
        --label llm-env.scope=phoenix --label "llm-env.project=$project" \
        -e UV_OFFLINE=1 -e UV_PYTHON_DOWNLOADS=never \
        "$SENDER_IMAGE" "${args[@]}"
}

case "$ACTION" in
    start)
        if ! phoenix_enabled; then
            log_info "System One is disabled; Phoenix dashboard not started"
            exit 0
        fi
        with_lock
        require_cmd podman podman-compose ss curl jq yq flock realpath
        # Idempotence: our own running server is a success, not a conflict.
        if [[ -n "$(owned_server_id)" ]]; then
            require_owned_server
            wait_ready
            log_info "Phoenix dashboard is already running at http://127.0.0.1:$port"
            exit 0
        fi
        require_owned_resources
        # A foreign listener must fail without killing or adopting it.
        check_port_free
        ensure_internal_network
        # Validate the pinned image before pulling anything.
        if ! podman image exists "$SERVER_IMAGE"; then
            podman pull "$SERVER_IMAGE"
        fi
        if ! podman volume inspect "$volume" >/dev/null 2>&1; then
            podman volume create --label llm-env.scope=phoenix \
                --label "llm-env.project=$project" "$volume" >/dev/null
        fi
        load_resource_env
        phoenix_compose up -d phoenix
        wait_ready
        log_info "Phoenix dashboard is ready at http://127.0.0.1:$port (project: laya; System One history enabled)"
        ;;
    stop)
        with_lock
        require_cmd podman flock realpath
        # No-op when the project owns nothing; only require the compose
        # provider when there is something to take down.
        if [[ -z "$(podman ps -aq --filter "label=llm-env.project=$project")" ]] \
            && ! podman volume inspect --format '{{.Name}}' "$volume" >/dev/null 2>&1; then
            exit 0
        fi
        require_cmd podman-compose
        require_owned_resources
        # Compose still interpolates resource fields for `down`; phoenix_compose
        # supplies policy defaults without requiring config or hardware reads.
        phoenix_compose down
        log_info "Phoenix dashboard stopped; data volume retained"
        ;;
    status)
        if ! phoenix_enabled \
            && [[ -z "$(podman ps -aq --filter "label=llm-env.project=$project" 2>/dev/null)" ]]; then
            log_info "Phoenix dashboard: not configured (System One disabled)"
            exit 0
        fi
        require_cmd podman
        podman ps -a --filter "label=llm-env.project=$project"
        ;;
    endpoint)
        printf 'http://127.0.0.1:%s\n' "$port"
        ;;
    prepare)
        # setup.sh hook: pinned server-only preparation. No container, no
        # sender, no volume -- just make the pinned image available.
        if ! phoenix_enabled; then
            exit 0
        fi
        require_cmd podman
        ensure_internal_network
        if ! podman image exists "$SERVER_IMAGE"; then
            podman pull "$SERVER_IMAGE"
        fi
        ;;
    seed-synthetic)
        # Explicit internal test tooling: prepare one synthetic run and
        # POST it exactly once. Never part of normal startup.
        with_lock
        require_cmd podman curl jq uv flock realpath
        phoenix_enabled || die "seed-synthetic requires system_one.enabled"
        require_owned_resources
        require_owned_server
        wait_ready
        podman image exists "$SENDER_IMAGE" \
            || die "synthetic sender image is missing; build it via: bash scripts/phoenix-trial.sh seed"
        receipt="" run_id="" expected_path="" result_tmp="" expected_summary="" expected_ids=""
        receipt="$(mktemp "$state_dir/prepare.XXXXXX")"
        chmod 600 -- "$receipt"
        if ! llmenv phoenix-trial prepare --manifest "$FIXTURE" --state-dir "$state_dir" > "$receipt"; then
            rm -f -- "$receipt"
            die "Phoenix synthetic preparation failed; no sender POST was attempted"
        fi
        run_id="$(jq -er '.run_id | select(test("^[0-9a-f]{12}4[0-9a-f]{3}[89ab][0-9a-f]{15}$"))' "$receipt")" \
            || die "Phoenix synthetic prepare returned an invalid run ID"
        expected_path="$(jq -er --arg root "$state_dir/runs/$run_id/expected.json" \
            'select(.expected_path == $root) | .expected_path' "$receipt")" \
            || die "Phoenix synthetic prepare returned an unexpected artifact path"
        rm -f -- "$receipt"
        llmenv phoenix-trial mark-attempt --expected "$expected_path" >/dev/null \
            || die "Phoenix synthetic attempt marker failed; no sender POST was attempted"
        result_tmp="$(mktemp "$state_dir/runs/$run_id/seed-result.XXXXXX")"
        chmod 600 -- "$result_tmp"
        set +e
        sender_run seed --base-url http://phoenix:6006 --expected - \
            < "$expected_path" > "$result_tmp"
        rc=$?
        set -e
        if ((rc != 0)); then
            rm -f -- "$result_tmp"
            printf 'Phoenix synthetic POST failed; recovery is read-only:\n' >&2
            printf 'bash scripts/phoenix.sh verify-synthetic\n' >&2
            exit "$rc"
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
        log_info "Phoenix synthetic run $run_id seeded and exactly verified"
        ;;
    verify-synthetic)
        # Explicit internal test tooling: GET-only exact persistence check
        # for the newest prepared run. Read-only; never re-POSTs.
        with_lock
        require_cmd podman curl jq flock find sort head realpath
        phoenix_enabled || die "verify-synthetic requires system_one.enabled"
        require_owned_resources
        require_owned_server
        wait_ready
        run_dir="" run_id="" expected_path="" receipt="" summary="" ids=""
        run_dir="$(newest_prepared_run)"
        [[ ! -L "$run_dir" ]] || die "newest prepared Phoenix run has an unsafe path"
        run_id="${run_dir##*/}"
        [[ "$run_id" =~ ^[0-9a-f]{12}4[0-9a-f]{3}[89ab][0-9a-f]{15}$ ]] \
            || die "newest prepared Phoenix run has an invalid identity"
        expected_path="$run_dir/expected.json"
        [[ -f "$expected_path" && ! -L "$expected_path" ]] \
            || die "newest prepared Phoenix run has no safe expected artifact"
        jq -e --arg run_id "$run_id" --arg expected_path "$expected_path" \
            '.schema_version == 1 and .status == "prepared" and .project == "laya-phoenix-trial" and .run_id == $run_id and .expected_path == $expected_path' \
            "$run_dir/prepared.json" >/dev/null \
            || die "newest prepared Phoenix receipt is corrupt or mismatched"
        receipt="$(mktemp "$state_dir/verify-result.XXXXXX")"
        chmod 600 -- "$receipt"
        if ! sender_run verify --base-url http://phoenix:6006 \
                --run-id "$run_id" --expected - < "$expected_path" > "$receipt"; then
            rm -f -- "$receipt"
            die "Phoenix GET-only persistence verification failed"
        fi
        summary="$(jq -cS '.summary' "$expected_path")"
        ids="$(jq -cS '[.spans[].context.span_id] | sort' "$expected_path")"
        jq -e --arg run_id "$run_id" --argjson summary "$summary" --argjson ids "$ids" '
            .schema_version == 1 and .status == "verified" and
            .project == "laya-phoenix-trial" and .run_id == $run_id and
            .summary == $summary and .verified_span_ids == $ids
        ' "$receipt" >/dev/null || {
            rm -f -- "$receipt"
            die "Phoenix persistence receipt does not match the prepared artifact"
        }
        rm -f -- "$receipt"
        log_info "Phoenix synthetic run $run_id persisted exactly (GET-only)"
        ;;
esac
