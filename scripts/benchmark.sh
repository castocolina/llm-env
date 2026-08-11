#!/usr/bin/env bash
# benchmark.sh — measure Vulkan throughput, record it, and fall back safely.
set -euo pipefail
# shellcheck disable=SC1091 # Resolved from this script at runtime.
# shellcheck source=../tools/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/../tools/lib.sh"

require_cmd uv jq yq podman awk base64
migrate_config_file || die "configuration migration failed"
diagnostic_dir="$(prepare_diagnostic_dir benchmark)"
status=0
trap 'status=$?; finish_diagnostic_dir "$diagnostic_dir"; exit "$status"' EXIT

device_name="$(yq -r '.gpu.device_name // ""' "$CONFIG_PATH")"
[ -n "$device_name" ] || die "configured gpu.device_name is empty"

listing_file="$(mktemp "${diagnostic_dir}/device-listing.XXXXXX")"
podman run --rm --device /dev/dri --entrypoint /app/llama-server \
    "$VULKAN_IMAGE" --list-devices >"$listing_file" 2>/dev/null \
    || die "could not list Vulkan devices"

resolve_status=0
resolved_json="$(llmenv resolve-device --device-name "$device_name" --listing-file "$listing_file")" \
    || resolve_status=$?
[ "$resolve_status" -eq 0 ] || die "could not resolve configured GPU ${device_name} (exit ${resolve_status})"
device="$(jq -r '.device // empty' <<<"$resolved_json" 2>/dev/null || true)"
[ -n "$device" ] || die "GPU resolution returned no device for ${device_name}"

presets_file="$(mktemp "${diagnostic_dir}/presets.XXXXXX")"
presets_status=0
render_presets_file "$device" "$presets_file" || presets_status=$?
[ "$presets_status" -eq 0 ] \
    || die "could not render production presets for ${device} (exit ${presets_status})"

parse_bench_json() {
    local stdout_file="$1" parser_stderr_file="$2"
    jq -ce '
        [(.[] | select(.n_prompt > 0) | .avg_ts),
         (.[] | select(.n_gen > 0) | .avg_ts)]
        | select(length == 2 and all(.[]; type == "number"))
        | {pp_tps: .[0], tg_tps: .[1]}
    ' "$stdout_file" 2>"$parser_stderr_file"
}

record_backend() {
    local backend="$1" image="$2"
    WINNER_BACKEND="$backend" WINNER_IMAGE="$image" \
        yq -i '.gpu.backend = strenv(WINNER_BACKEND) | .gpu.image = strenv(WINNER_IMAGE)' "$CONFIG_PATH"
}

fall_back_to_cpu() {
    record_backend cpu "$CPU_IMAGE"
    podman pull "$CPU_IMAGE" >/dev/null || die "cannot pull the CPU image"
}

model_records="$(mktemp "${diagnostic_dir}/benchmark-model-records.XXXXXX")"
if ! yq -o=json -I=0 '[.models[] | select(.enabled)]' "$CONFIG_PATH" |
        jq -r '.[] | @base64' > "$model_records"; then
    die "failed to enumerate enabled models for benchmarking"
fi
[ -s "$model_records" ] || die "no enabled models to benchmark"

log_step "Vulkan benchmark"
per_model_status=0
measured_models=0
vulkan_probe_complete=0
while IFS= read -r model_b64; do
    [ -n "$model_b64" ] || continue
    model_json="$(printf '%s' "$model_b64" | base64 --decode)"
    alias="$(jq -r '.alias' <<<"$model_json")"
    file="$(jq -r '.file' <<<"$model_json")"
    check_ctx_override="$(jq -r '.check_ctx_size // empty' <<<"$model_json")"
    max_output_tokens="$(jq -r '.client_max_output_tokens' <<<"$model_json")"
    n_gpu_layers="$(presets_value "$presets_file" "$alias" "n-gpu-layers")"
    n_cpu_moe="$(presets_value "$presets_file" "$alias" "n-cpu-moe")"
    preset_ctx_size="$(presets_value "$presets_file" "$alias" "ctx-size")"
    if [ -z "$n_gpu_layers" ] || [ -z "$preset_ctx_size" ]; then
        missing_key="n-gpu-layers"
        [ -n "$n_gpu_layers" ] && missing_key="ctx-size"
        log_error "missing ${missing_key} preset for ${alias}"
        per_model_status=1
        continue
    fi
    check_ctx_size="${check_ctx_override:-$preset_ctx_size}"

    bench_args=(
        bench -m "/models/${file}" --device "$device"
        --n-gpu-layers "$n_gpu_layers"
    )
    [ -n "$n_cpu_moe" ] && bench_args+=(--n-cpu-moe "$n_cpu_moe")
    bench_args+=(-p "$check_ctx_size" -n "$max_output_tokens" -r 2 -o json)

    model_stdout="$(mktemp "${diagnostic_dir}/model-bench-stdout.XXXXXX")"
    model_stderr="$(mktemp "${diagnostic_dir}/model-bench-stderr.XXXXXX")"
    model_parser_stderr="$(mktemp "${diagnostic_dir}/model-bench-parser-stderr.XXXXXX")"

    log_command "podman run --rm --device /dev/dri -v ${MODELS_DIR}:/models:ro,z --entrypoint /app/llama ${VULKAN_IMAGE} ${bench_args[*]}"
    model_status=0
    podman run --rm --device /dev/dri \
        -v "${MODELS_DIR}:/models:ro,z" --entrypoint /app/llama \
        "$VULKAN_IMAGE" "${bench_args[@]}" >"$model_stdout" 2>"$model_stderr" \
        || model_status=$?
    log_block "Benchmark stdout" "$(<"$model_stdout")"
    log_nonempty_block "Benchmark stderr" "$(<"$model_stderr")"
    log_block "Exit status" "$model_status"
    if [ "$model_status" -ne 0 ]; then
        log_error "Vulkan benchmark failure for ${alias}: command exit ${model_status}"
        if [ "$vulkan_probe_complete" -eq 0 ]; then
            fall_back_to_cpu
            exit 1
        fi
        per_model_status=1
        continue
    fi
    model_result=""
    if ! model_result="$(parse_bench_json "$model_stdout" "$model_parser_stderr")"; then
        log_nonempty_block "Benchmark parser stderr" "$(<"$model_parser_stderr")"
        log_error "Vulkan benchmark failure for ${alias}: response parsing"
        if [ "$vulkan_probe_complete" -eq 0 ]; then
            fall_back_to_cpu
            exit 1
        fi
        per_model_status=1
        continue
    fi
    log_nonempty_block "Benchmark parser stderr" "$(<"$model_parser_stderr")"
    log_block "Parsed metrics" "$model_result"
    pp="$(jq -er '.pp_tps' <<<"$model_result")"
    tg="$(jq -er '.tg_tps' <<<"$model_result")"

    if [ "$vulkan_probe_complete" -eq 0 ]; then
        record_backend vulkan "$VULKAN_IMAGE"
        vulkan_probe_complete=1
    fi

    measured_at="$(date -Iseconds)"
    MODEL_ALIAS="$alias" PP_TPS="$pp" TG_TPS="$tg" MEASURED_AT="$measured_at" \
        yq -i '
            (.models[] | select(.alias == strenv(MODEL_ALIAS)) | .benchmark.vulkan) = {
                "pp_tps": env(PP_TPS),
                "tg_tps": env(TG_TPS),
                "measured_at": strenv(MEASURED_AT)
            }
        ' "$CONFIG_PATH"
    measured_models=$((measured_models + 1))
done < "$model_records"

total_models="$(wc -l < "$model_records")"
[ "$measured_models" -gt 0 ] || die "no enabled model benchmark completed successfully"
if [ "$measured_models" -ne "$total_models" ]; then
    log_error "measured ${measured_models} of ${total_models} enabled models"
    per_model_status=1
fi
[ "$per_model_status" -eq 0 ] || exit 1
