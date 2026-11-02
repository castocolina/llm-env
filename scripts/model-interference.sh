#!/usr/bin/env bash
# model-interference.sh -- measure whether the local llama model slows System One (Laya) or pressures
# container memory while it serves load, and gate on the result.
#
# Usage: scripts/model-interference.sh [--seconds 120] [--baseline-seconds 30]
#                                      [--max-regression-pct 50] [--min-regression-ms 10]
# Output root: $LLM_ENV_SCOUT_OUT (default <repo>/tmp); the run dir is
# <root>/model-interference/<YYYYmmddTHHMMSS>-<pid>. Exits with `llmenv interference evaluate`'s status.
# OmniRoute is probed at the unauthenticated GET /api/health (the monitoring endpoint needs a login whose
# secret must not reach argv); its latency is recorded only and does not gate.
# Probe loops and the sampler stop at the iteration count or at the window end, whichever comes first.
set -euo pipefail
# shellcheck disable=SC1091 # Resolved from this script at runtime.
# shellcheck source=../tools/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/../tools/lib.sh"

seconds=120
baseline_seconds=30
max_regression_pct=50
min_regression_ms=10
while [ $# -gt 0 ]; do
    case "$1" in
        --seconds) seconds="${2-}"; shift 2 ;;
        --baseline-seconds) baseline_seconds="${2-}"; shift 2 ;;
        --max-regression-pct) max_regression_pct="${2-}"; shift 2 ;;
        --min-regression-ms) min_regression_ms="${2-}"; shift 2 ;;
        *) die "unknown argument: $1" ;;
    esac
done
[[ "$seconds" =~ ^[1-9][0-9]*$ ]] || die "--seconds must be a positive integer"
[[ "$baseline_seconds" =~ ^[1-9][0-9]*$ ]] || die "--baseline-seconds must be a positive integer"

require_cmd curl jq yq uv podman
[ -f "$CONFIG_PATH" ] || die "no config at ${CONFIG_PATH}; run 'make setup' first"
migrate_config_file || die "configuration migration failed"

# Preconditions: refuse before writing anything.
[ "$(yq -r '.llm_server.enabled' "$CONFIG_PATH")" = "true" ] \
    || die "local llama inference is disabled; run 'make llama-enable MODEL=<alias>'"
server_port="$(yq -r '.server.port' "$CONFIG_PATH")"
if ! container_running llm-server || ! curl -fsS --max-time 5 "http://127.0.0.1:${server_port}/health" >/dev/null 2>&1; then
    die "llm-server is not running or not healthy; run 'make start-llama'"
fi
system_one=false
if [ "$(yq -r '.system_one.enabled' "$CONFIG_PATH")" = "true" ]; then
    system_one=true
    container_running system-one || die "system-one is not running; run 'make start'"
    laya_url="http://127.0.0.1:$(yq -r '.system_one.port' "$CONFIG_PATH")/v1/systemone"
fi
omniroute_url="http://127.0.0.1:$(yq -r '.omniroute.port' "$CONFIG_PATH")/api/health"
chat_url="http://127.0.0.1:${server_port}/v1/chat/completions"

alias_name="$(yq -r '[.models[] | select(.enabled)][0].alias // ""' "$CONFIG_PATH")"
[ -n "$alias_name" ] || die "no enabled model in ${CONFIG_PATH}"
ctx_size="$(yq -o=json '.models' "$CONFIG_PATH" | jq -r --arg a "$alias_name" '[.[] | select(.alias == $a)][0].ctx_size // 4096')"
[[ "$ctx_size" =~ ^[1-9][0-9]*$ ]] || die "ctx_size of ${alias_name} is not a positive integer: ${ctx_size}"
api_key="$(yq -r '.server.api_key' "$CONFIG_PATH")"

root="${LLM_ENV_SCOUT_OUT:-${REPO_DIR}/tmp}"
run="${root}/model-interference/$(date +%Y%m%dT%H%M%S)-$$"
work="${run}/work"
mkdir -p "$work"
: > "${run}/load.jsonl"
: > "${run}/samples.jsonl"

jobs_pids=()
# shellcheck disable=SC2329 # Invoked by the EXIT trap.
cleanup() {
    local status=$? pid
    for pid in "${jobs_pids[@]}"; do kill -TERM -- "-${pid}" 2>/dev/null || true; done
    for pid in "${jobs_pids[@]}"; do wait "$pid" 2>/dev/null || true; done
    rm -rf "$work"
    exit "$status"
}
trap cleanup EXIT
trap 'exit 143' INT TERM

# min(98304, ctx_size) bytes of a repeated English sentence.
prompt_bytes=$(( ctx_size < 98304 ? ctx_size : 98304 ))
yes 'The quick brown fox jumps over the lazy dog while the model reads this sentence again.' \
    | head -c "$prompt_bytes" > "${work}/prompt.txt" || true

chat_body() { # <prefix> <max_tokens> <out file>
    jq -n --arg model "$alias_name" --arg prefix "$1" --rawfile text "${work}/prompt.txt" --argjson max "$2" \
        '{model: $model, max_tokens: $max, stream: false, messages: [{role: "user", content: ($prefix + $text)}]}' > "$3"
}

# The key travels only through curl's stdin config, never argv.
chat() { # <body file> <max-time> <out file>; prints "<http_code> <seconds>"
    printf 'header = "Authorization: Bearer %s"\n' "$api_key" \
        | curl -sS -K - -H 'Content-Type: application/json' --max-time "$2" --data-binary "@$1" \
            -o "$3" -w '%{http_code} %{time_total}\n' "$chat_url" 2>/dev/null || true
}

printf '{"model":"jev-1","state":{"ticket":"The app crashes every time I open the settings page"},"questions":{"team":{"type":"choice","instructions":"Which team should handle this ticket?","criteria":{"billing":"charges, invoices, refunds","technical":"bugs, crashes, outages","other":"general questions"}}}}' \
    > "${work}/laya.json"
laya_call() {
    curl -s -o /dev/null -w '%{http_code} %{time_total}\n' --max-time 10 -X POST "$laya_url" \
        -H 'content-type: application/json' --data-binary "@${work}/laya.json" || true
}

# shellcheck disable=SC2329 # Started through start_job.
probe_laya() { # <out file> <window seconds>
    local i n=$(( $2 * 4 )) end=$(( SECONDS + $2 )) # 0.25 s interval
    [ "$n" -ge 1 ] || n=1
    for ((i = 0; i < n && SECONDS < end; i++)); do laya_call >> "$1"; sleep 0.25; done
}
# shellcheck disable=SC2329 # Started through start_job.
probe_omniroute() { # <out file> <window seconds>
    local i n=$2 end=$(( SECONDS + $2 ))
    for ((i = 0; i < n && SECONDS < end; i++)); do
        curl -s -o /dev/null -w '%{http_code} %{time_total}\n' --max-time 10 "$omniroute_url" >> "$1" || true
        sleep 1
    done
}
# shellcheck disable=SC2329 # Started through start_job.
sampler() { # <window seconds>
    local i n=$(( $1 / 5 )) end=$(( SECONDS + $1 ))
    [ "$n" -ge 1 ] || n=1
    for ((i = 0; i < n && SECONDS < end; i++)); do
        llmenv --config "$CONFIG_PATH" interference snapshot --append "${run}/samples.jsonl" > /dev/null || true
        sleep 5
    done
}
# shellcheck disable=SC2329 # Started through start_job.
load_loop() { # <window seconds>
    local end=$(( SECONDS + $1 )) n=0 left line code secs out
    while [ "$SECONDS" -lt "$end" ]; do
        n=$((n + 1)); left=$((end - SECONDS)); [ "$left" -ge 1 ] || left=1
        chat_body "[load ${n} ${EPOCHREALTIME}] " 1024 "${work}/load-${n}.json"
        out="${work}/out-${n}.json"
        line="$(chat "${work}/load-${n}.json" "$left" "$out")"
        read -r code secs <<< "${line:-000 0}"
        jq -cn --arg s "$code" --argjson t "${secs:-0}" --slurpfile u <(cat "$out" 2>/dev/null || true) \
            '{status: $s, time_s: $t, prompt_tokens: ($u[0].usage.prompt_tokens // null), completion_tokens: ($u[0].usage.completion_tokens // null)}' \
            >> "${run}/load.jsonl" 2>/dev/null \
            || jq -cn --arg s "$code" '{status: $s}' >> "${run}/load.jsonl"
        rm -f "${work}/load-${n}.json" "$out"
    done
}

# Each background job gets its own process group (as scripts/benchmark.sh's heartbeat does) so the
# trap can kill the whole job, sleep children included.
start_job() { set -m; "$@" < /dev/null & jobs_pids+=("$!"); set +m; }
wait_jobs() { local pid; for pid in "${jobs_pids[@]}"; do wait "$pid" 2>/dev/null || true; done; jobs_pids=(); }

log_step "Warm-up (loads ${alias_name}, one full prefill)"
chat_body "[warm-up] " 1 "${work}/warmup.json"
chat "${work}/warmup.json" "$seconds" /dev/null > "${run}/warmup.txt"
warm_code="$(cut -d' ' -f1 "${run}/warmup.txt")"
[ "$warm_code" = "200" ] || die "warm-up chat failed (${warm_code:-000}); rerun with a larger --seconds (see ${run}/warmup.txt)"
if [ "$system_one" = true ]; then for _ in 1 2 3; do laya_call > /dev/null; done; fi

llmenv --config "$CONFIG_PATH" interference snapshot > "${run}/live-before.json"

log_step "Baseline (${baseline_seconds}s)"
start_job probe_omniroute "${run}/omniroute-before.txt" "$baseline_seconds"
if [ "$system_one" = true ]; then start_job probe_laya "${run}/laya-before.txt" "$baseline_seconds"; fi
wait_jobs

log_step "Load window (${seconds}s)"
start_job load_loop "$seconds"
start_job probe_omniroute "${run}/omniroute-during.txt" "$seconds"
if [ "$system_one" = true ]; then start_job probe_laya "${run}/laya-during.txt" "$seconds"; fi
start_job sampler "$seconds"
wait_jobs

llmenv --config "$CONFIG_PATH" interference snapshot > "${run}/live-after.json"

status=0
verdict="$(llmenv --config "$CONFIG_PATH" interference evaluate --dir "$run" \
    --max-regression-pct "$max_regression_pct" --min-regression-ms "$min_regression_ms")" || status=$?

llmenv --config "$CONFIG_PATH" capacity write --alias "$alias_name" --interference "$run/report.json" >/dev/null \
    || log_warn "could not update the capacity card"

printf 'alias: %s\nrun dir: %s\n' "$alias_name" "$run"
jq -r 'if .passed then "passed" else "failed: " + (.failures | join("; ")) end' <<< "$verdict" 2>/dev/null \
    || echo "evaluate produced no verdict"
exit "$status"
