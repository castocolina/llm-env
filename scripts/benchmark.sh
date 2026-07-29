#!/usr/bin/env bash
# benchmark.sh — measure Vulkan throughput, record it, and fall back safely.
set -euo pipefail
# shellcheck disable=SC1091 # Resolved from this script at runtime.
# shellcheck source=../tools/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/../tools/lib.sh"

require_cmd uv jq yq podman awk

VULKAN_IMAGE="ghcr.io/ggml-org/llama.cpp:server-vulkan"
CPU_IMAGE="ghcr.io/ggml-org/llama.cpp:server"

bench_model="$(yq -r '[.models[] | select(.enabled)] | sort_by(.size_bytes) | .[0].file' "$CONFIG_PATH")"
[ -n "$bench_model" ] && [ "$bench_model" != "null" ] || die "no enabled models to benchmark"

record_vulkan() {
    local pp="$1" tg="$2"
    yq -i 'del(.gpu.benchmark.rocm)' "$CONFIG_PATH"
    yq -i ".gpu.benchmark.vulkan.pp_tps = ${pp}" "$CONFIG_PATH"
    yq -i ".gpu.benchmark.vulkan.tg_tps = ${tg}" "$CONFIG_PATH"
    yq -i ".gpu.benchmark.vulkan.measured_at = \"$(date -Iseconds)\"" "$CONFIG_PATH"
}

run_vulkan_bench() {
    local stdout_file="$1" stderr_file="$2" parser_stderr_file="$3" status result

    log_command "podman run --rm --device /dev/dri -v ${MODELS_DIR}:/models:ro,z --entrypoint /app/llama ${VULKAN_IMAGE} bench -m /models/${bench_model} -p 512 -n 128 -r 2 -o json"
    if podman run --rm --device /dev/dri \
        -v "${MODELS_DIR}:/models:ro,z" \
        --entrypoint /app/llama \
        "$VULKAN_IMAGE" bench -m "/models/${bench_model}" -p 512 -n 128 -r 2 -o json \
        >"$stdout_file" 2>"$stderr_file"; then
        status=0
    else
        status=$?
    fi
    log_block "Benchmark stdout" "$(<"$stdout_file")"
    log_nonempty_block "Benchmark stderr" "$(<"$stderr_file")"
    log_block "Exit status" "$status"
    if [ "$status" -ne 0 ]; then
        log_nonempty_block "Benchmark parser stderr" "$(<"$parser_stderr_file")"
        log_error "Vulkan benchmark failure: command exit ${status}"
        return 1
    fi
    if ! result="$(jq -ce '
        [(.[] | select(.n_prompt > 0) | .avg_ts),
         (.[] | select(.n_gen > 0) | .avg_ts)]
        | select(length == 2 and all(.[]; type == "number"))
        | {pp_tps: .[0], tg_tps: .[1]}
    ' "$stdout_file" 2>"$parser_stderr_file")"; then
        log_nonempty_block "Benchmark parser stderr" "$(<"$parser_stderr_file")"
        log_error "Vulkan benchmark failure: response parsing"
        return 1
    fi
    log_nonempty_block "Benchmark parser stderr" "$(<"$parser_stderr_file")"
    log_block "Parsed metrics" "$result"
    BENCH_RESULT="$result"
}

log_step "Vulkan benchmark"
log_info "model: ${bench_model}"
diagnostic_dir="$(prepare_diagnostic_dir benchmark)"
trap 'status=$?; finish_diagnostic_dir "$diagnostic_dir"; exit "$status"' EXIT
vulkan_stdout="$(mktemp "${diagnostic_dir}/vulkan-benchmark-stdout.XXXXXX")" \
    || die "could not create Vulkan benchmark stdout diagnostic"
vulkan_stderr="$(mktemp "${diagnostic_dir}/vulkan-benchmark-stderr.XXXXXX")" \
    || die "could not create Vulkan benchmark stderr diagnostic"
vulkan_parser_stderr="$(mktemp "${diagnostic_dir}/vulkan-benchmark-parser-stderr.XXXXXX")" \
    || die "could not create Vulkan benchmark parser diagnostic"

winner_backend="cpu"
winner_image="$CPU_IMAGE"
BENCH_RESULT=""
benchmark_status=1
if run_vulkan_bench "$vulkan_stdout" "$vulkan_stderr" "$vulkan_parser_stderr" && [ -n "$BENCH_RESULT" ]; then
    pp="$(jq -er '.pp_tps' <<<"$BENCH_RESULT")"
    tg="$(jq -er '.tg_tps' <<<"$BENCH_RESULT")"
    record_vulkan "$pp" "$tg"
    winner_backend="vulkan"
    winner_image="$VULKAN_IMAGE"
    benchmark_status=0
    log_info "Vulkan: ${pp} tok/s prompt, ${tg} tok/s generation"
else
    log_warn "Vulkan benchmark failed; falling back to CPU. Expect very slow inference."
    podman pull "$winner_image" >/dev/null || die "cannot pull the CPU image"
fi

log_step "Resolving the GPU device name"
listing_file="$(mktemp)"
podman run --rm --device /dev/dri --entrypoint /app/llama-server \
    "$winner_image" --list-devices >"$listing_file" 2>/dev/null || true
log_info "device listing:"
sed 's/^/    /' "$listing_file" || true

vram="$(yq -r '.gpu.vram_total_mib' "$CONFIG_PATH")"
device_name="$(awk -v want="$vram" '
    match($0, /^[[:space:]]*[^:]+:[[:space:]]+/) {
        rest = substr($0, RLENGTH + 1)
        if (match(rest, /[[:space:]]+\([0-9]+[[:space:]]*MiB/)) {
            name = substr(rest, 1, RSTART - 1)
            mib  = rest
            sub(/^.*\(/, "", mib); sub(/[[:space:]]*MiB.*$/, "", mib)
            if (mib + 0 == want + 0) { print name; exit }
        }
    }' "$listing_file")"
rm -f "$listing_file"

if [ -n "$device_name" ]; then
    yq -i ".gpu.device_name = \"${device_name}\"" "$CONFIG_PATH"
    log_info "device name recorded: ${device_name} (pci $(yq -r '.gpu.pci_address' "$CONFIG_PATH"))"
else
    log_warn "could not match a device with ${vram} MiB; start.sh will offload to all devices"
fi

yq -i ".gpu.backend = \"${winner_backend}\"" "$CONFIG_PATH"
yq -i ".gpu.image = \"${winner_image}\"" "$CONFIG_PATH"
log_info "backend set to ${winner_backend} (${winner_image})"
exit "$benchmark_status"
