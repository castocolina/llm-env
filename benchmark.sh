#!/usr/bin/env bash
# benchmark.sh — measure Vulkan vs ROCm, record the winner, fall back safely.
set -euo pipefail
# shellcheck source=lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

require_cmd uv jq yq podman awk

VULKAN_IMAGE="ghcr.io/ggml-org/llama.cpp:server-vulkan"
ROCM_IMAGE="ghcr.io/ggml-org/llama.cpp:server-rocm"

bench_model="$(yq -r '[.models[] | select(.enabled)] | sort_by(.size_bytes) | .[0].file' "$CONFIG_PATH")"
[ -n "$bench_model" ] && [ "$bench_model" != "null" ] || die "no enabled models to benchmark"
log_info "benchmarking with the smallest enabled model: ${bench_model}"

# Runs llama-bench in a container. Echoes "pp_tps tg_tps" or returns non-zero.
run_bench() {
    local image="$1"; shift
    local devices=("$@")
    local args=()
    for dev in "${devices[@]}"; do args+=(--device "$dev"); done

    podman run --rm "${args[@]}" \
        -v "${MODELS_DIR}:/models:ro,z" \
        --entrypoint /app/llama-bench \
        "$image" -m "/models/${bench_model}" -p 512 -n 128 -r 2 -o json 2>/dev/null \
      | jq -r '
          [ (.[] | select(.n_prompt > 0) | .avg_ts),
            (.[] | select(.n_gen    > 0) | .avg_ts) ] | @tsv'
}

record() {
    local backend="$1" pp="$2" tg="$3"
    yq -i ".gpu.benchmark.${backend}.pp_tps = ${pp}" "$CONFIG_PATH"
    yq -i ".gpu.benchmark.${backend}.tg_tps = ${tg}" "$CONFIG_PATH"
    yq -i ".gpu.benchmark.${backend}.measured_at = \"$(date -Iseconds)\"" "$CONFIG_PATH"
}

winner_backend=""; winner_image=""; winner_tg="0"

log_step "Trying ROCm"
if [ ! -e /dev/kfd ]; then
    log_warn "skipping ROCm: /dev/kfd is absent"
elif [ ! -r /dev/kfd ]; then
    log_warn "skipping ROCm: /dev/kfd is not readable by this user"
else
    log_info "pulling ${ROCM_IMAGE} (7.69 GB, this takes a while)"
    if podman pull "$ROCM_IMAGE" >/dev/null 2>&1 \
       && result="$(run_bench "$ROCM_IMAGE" /dev/dri /dev/kfd)" \
       && [ -n "$result" ]; then
        pp="$(cut -f1 <<<"$result")"; tg="$(cut -f2 <<<"$result")"
        record rocm "$pp" "$tg"
        winner_backend=rocm; winner_image="$ROCM_IMAGE"; winner_tg="$tg"
        log_info "ROCm: ${pp} tok/s prompt, ${tg} tok/s generation"
    else
        log_warn "ROCm benchmark failed; falling back"
    fi
fi

log_step "Trying Vulkan"
log_info "pulling ${VULKAN_IMAGE} (0.31 GB)"
if podman pull "$VULKAN_IMAGE" >/dev/null 2>&1 \
   && result="$(run_bench "$VULKAN_IMAGE" /dev/dri)" \
   && [ -n "$result" ]; then
    pp="$(cut -f1 <<<"$result")"; tg="$(cut -f2 <<<"$result")"
    record vulkan "$pp" "$tg"
    log_info "Vulkan: ${pp} tok/s prompt, ${tg} tok/s generation"
    if [ -z "$winner_backend" ] || awk "BEGIN{exit !($tg > $winner_tg)}"; then
        winner_backend=vulkan; winner_image="$VULKAN_IMAGE"; winner_tg="$tg"
    fi
else
    log_warn "Vulkan benchmark failed"
fi

if [ -z "$winner_backend" ]; then
    log_warn "no GPU backend worked; falling back to CPU. Expect very slow inference."
    winner_backend=cpu; winner_image="ghcr.io/ggml-org/llama.cpp:server"
    podman pull "$winner_image" >/dev/null || die "cannot pull the CPU image either"
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
    log_info "device name recorded: ${device_name}"
else
    log_warn "could not match a device with ${vram} MiB; start.sh will offload to all devices"
fi

yq -i ".gpu.backend = \"${winner_backend}\"" "$CONFIG_PATH"
yq -i ".gpu.image = \"${winner_image}\"" "$CONFIG_PATH"
log_info "backend set to ${winner_backend} (${winner_image})"
