#!/usr/bin/env bash
# check-setup.sh — offline validation. No server required.
set -uo pipefail
# shellcheck disable=SC1091 # Resolved from this script at runtime.
# shellcheck source=../tools/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/../tools/lib.sh"
set +e

PASS=0; FAIL=0
diagnostic_dir="$(prepare_diagnostic_dir check-setup)"
trap 'status=$?; finish_diagnostic_dir "$diagnostic_dir"; exit "$status"' EXIT

log_identity() {
    printf 'Identity: '
    redact_text "$1"
    printf '\n'
}

record_command() {
    local identity="$1" command_text="$2" input="$3" expectation="$4"
    local expected_result="$5" show_parsed="$6" stdout_label="$7" stderr_label="$8"
    local stdout_file stderr_file status parsed normalized
    shift 8

    stdout_file="$(mktemp "${diagnostic_dir}/stdout.XXXXXX")" || die "could not create diagnostic stdout"
    stderr_file="$(mktemp "${diagnostic_dir}/stderr.XXXXXX")" || die "could not create diagnostic stderr"
    record_stdout_file="$stdout_file"

    open_diagnostic_capture "$diagnostic_dir"
    log_identity "$identity"
    log_command "$command_text"
    log_block "Input" "$input"
    if "$@" >"$stdout_file" 2>"$stderr_file"; then status=0; else status=$?; fi
    log_block "$stdout_label" "$(<"$stdout_file")"
    log_nonempty_block "$stderr_label" "$(<"$stderr_file")"
    log_block "Exit status" "$status"
    parsed="$(<"$stdout_file")"
    if [ -n "$expected_result" ]; then
        parsed=""
        while IFS= read -r line || [ -n "$line" ]; do
            if [ -n "${line//[[:space:]]/}" ] && [ "$line" != "Exiting..." ]; then
                parsed="$line"
            fi
        done < "$stdout_file"
        normalized="$(printf '%s' "$parsed" | tr '[:upper:]' '[:lower:]' | \
            sed -E 's/^[[:space:][:punct:]]+//; s/[[:space:][:punct:]]+$//')"
    fi
    if [ "$show_parsed" = 1 ]; then
        log_block "Parsed result" "$parsed"
    fi
    log_block "Expectation" "$expectation"

    if [ "$status" -ne 0 ]; then
        close_diagnostic_capture 1
        log_error "Verdict: FAIL stage=command exit reason=${identity%% *} exited ${status}"
        FAIL=$((FAIL + 1))
        return 1
    fi
    if [ -n "$expected_result" ] && [ "$normalized" != "$expected_result" ]; then
        close_diagnostic_capture 1
        log_error "Verdict: FAIL stage=parsed result reason=normalized assistant content mismatch expected=${expected_result}"
        FAIL=$((FAIL + 1))
        return 1
    fi

    close_diagnostic_capture 0
    log_info "Verdict: PASS identity=${identity}"
    PASS=$((PASS + 1))
}

record_inference_skip() {
    local alias="$1" command_text="$2" reason="$3"

    log_identity "inference ${alias}"
    log_command "$command_text"
    log_block "Input" "Reply with exactly: ready"
    log_block "Inference stdout" ""
    log_block "Exit status" "SKIP"
    log_block "Expectation" "normalized assistant content: ready"
    log_warn "Verdict: SKIP reason=${reason}"
}

inference_command() {
    local file="$1" device="$2" layers="$3" n_cpu_moe="$4"
    local ctx_size="$5" max_output_tokens="$6" timeout_seconds="$7" moe_part=""
    [ -n "$n_cpu_moe" ] && moe_part="--n-cpu-moe ${n_cpu_moe} "
    printf 'timeout %q podman run --rm --device /dev/dri -v %q:/models:ro,z --entrypoint /app/llama %q cli -m %q --device %q --n-gpu-layers %q %s--ctx-size %q --single-turn --no-show-timings -p %q -n %q' \
        "$timeout_seconds" "$MODELS_DIR" "$image" "/models/${file}" "$device" \
        "$layers" "$moe_part" "$ctx_size" "Reply with exactly: ready" "$max_output_tokens"
}

record_inferences() {
    local device="$1" skip_reason="${2:-}" presets_file="${3:-}"
    local alias file model_b64 model_json check_ctx_override max_output_tokens
    local timeout_seconds n_gpu_layers n_cpu_moe preset_ctx_size missing_key
    local ctx_size command_text model_records
    local -a moe_args

    model_records="$(mktemp "${diagnostic_dir}/inference-model-records.XXXXXX")" \
        || die "could not create model records file"
    if ! yq -o=json -I=0 '[.models[] | select(.enabled)]' "$CONFIG_PATH" |
            jq -r '.[] | @base64' > "$model_records"; then
        log_error "failed to enumerate enabled models for offline inference"
        FAIL=$((FAIL + 1))
        return 1
    fi
    if [ ! -s "$model_records" ]; then
        log_error "no enabled models to check"
        FAIL=$((FAIL + 1))
        return 1
    fi

    log_step "Offline inference"
    while IFS= read -r model_b64; do
        [ -n "$model_b64" ] || continue
        model_json="$(printf '%s' "$model_b64" | base64 --decode)"
        alias="$(jq -r '.alias' <<<"$model_json")"
        file="$(jq -r '.file' <<<"$model_json")"
        check_ctx_override="$(jq -r '.check_ctx_size // empty' <<<"$model_json")"
        max_output_tokens="$(jq -r '.client_max_output_tokens' <<<"$model_json")"
        timeout_seconds="$(jq -r '.check_timeout_seconds // 140' <<<"$model_json")"
        if [ -n "$skip_reason" ]; then
            record_inference_skip "$alias" "not run: inference prerequisite unavailable" "$skip_reason"
            continue
        fi
        n_gpu_layers="$(presets_value "$presets_file" "$alias" "n-gpu-layers")"
        n_cpu_moe="$(presets_value "$presets_file" "$alias" "n-cpu-moe")"
        preset_ctx_size="$(presets_value "$presets_file" "$alias" "ctx-size")"
        if [ -z "$n_gpu_layers" ] || [ -z "$preset_ctx_size" ]; then
            missing_key="n-gpu-layers"
            [ -n "$n_gpu_layers" ] && missing_key="ctx-size"
            command_text="not run: missing ${missing_key} preset for ${alias}"
            log_error "missing ${missing_key} preset for ${alias}"
            FAIL=$((FAIL + 1))
            record_inference_skip "$alias" "$command_text" "missing required production preset"
            continue
        fi
        ctx_size="${check_ctx_override:-$preset_ctx_size}"
        moe_args=()
        [ -n "$n_cpu_moe" ] && moe_args=(--n-cpu-moe "$n_cpu_moe")
        command_text="$(inference_command "$file" "$device" "$n_gpu_layers" "$n_cpu_moe" \
            "$ctx_size" "$max_output_tokens" "$timeout_seconds")"
        record_command "inference ${alias}" "$command_text" "Reply with exactly: ready" \
            "normalized assistant content: ready" "ready" 1 "Inference stdout" "Inference stderr" \
            timeout "$timeout_seconds" podman run --rm --device /dev/dri \
            -v "${MODELS_DIR}:/models:ro,z" --entrypoint /app/llama "$image" cli \
            -m "/models/${file}" --device "$device" --n-gpu-layers "$n_gpu_layers" \
            "${moe_args[@]}" --ctx-size "$ctx_size" --single-turn --no-show-timings \
            -p "Reply with exactly: ready" -n "$max_output_tokens" || true
    done < "$model_records"
}

log_step "Tooling"
for cmd in uv jq yq base64 podman systemctl curl; do
    record_command "tooling command ${cmd}" "command -v ${cmd}" "" \
        "exit status: 0" "" 0 "Command stdout" "Command stderr" command -v "$cmd" || true
done

log_step "Configuration"
record_command "configuration file" "test -f ${CONFIG_PATH}" "" \
    "exit status: 0" "" 0 "Command stdout" "Command stderr" test -f "$CONFIG_PATH" || true
migrate_config_file || die "configuration migration failed"
record_command "configuration validation" \
    "uv run ${REPO_DIR}/llmenv.py --config ${CONFIG_PATH} models list" "" \
    "exit status: 0" "" 0 "Command stdout" "Command stderr" \
    uv run "${REPO_DIR}/llmenv.py" --config "$CONFIG_PATH" models list || true

pci="$(yq -r '.gpu.pci_address' "$CONFIG_PATH" 2>/dev/null || true)"
image="$(yq -r '.gpu.image' "$CONFIG_PATH" 2>/dev/null || true)"

log_step "GPU access"
record_command "GPU directory" "test -d /dev/dri" "" \
    "exit status: 0" "" 0 "Command stdout" "Command stderr" test -d /dev/dri || true
record_command "GPU detection" \
    "uv run ${REPO_DIR}/llmenv.py detect" "configured PCI address: ${pci}" \
    "GPU record for ${pci}" "" 0 "Command stdout" "Command stderr" \
    uv run "${REPO_DIR}/llmenv.py" detect
detect_status=$?
detected_gpus="$(<"$record_stdout_file")"
render_node="$(jq -r --arg p "$pci" '.gpus[] | select(.pci_address==$p) | .render_node' <<<"$detected_gpus")"
if [ "$detect_status" -eq 0 ] && [ -n "$render_node" ] && [ "$render_node" != "null" ]; then
    record_command "GPU render node" "test -r /dev/dri/${render_node}" "" \
        "exit status: 0" "" 0 "Command stdout" "Command stderr" test -r "/dev/dri/${render_node}" || true
else
    log_identity "GPU render node"
    log_command "test -r /dev/dri/<resolved-render-node>"
    log_block "Input" "configured PCI address: ${pci}"
    log_block "Command stdout" ""
    log_block "Exit status" "SKIP"
    log_block "Expectation" "a detected readable render node"
    log_warn "Verdict: SKIP reason=GPU detection did not provide a render node"
fi

log_step "Configured GPU is present"
record_command "configured GPU" \
    "uv run ${REPO_DIR}/llmenv.py detect | jq -e --arg p ${pci} '.gpus[] | select(.pci_address==\$p)'" \
    "configured PCI address: ${pci}" "exit status: 0" "" 0 "Command stdout" "Command stderr" \
    bash -c "uv run '${REPO_DIR}/llmenv.py' detect | jq -e --arg p '${pci}' '.gpus[] | select(.pci_address==\$p)'" || true

log_step "Container image"
record_command "container image" "podman image exists ${image}" "" \
    "exit status: 0" "" 0 "Command stdout" "Command stderr" podman image exists "$image" || true

log_step "Compose file"
record_command "compose file syntax" \
    "podman compose -f ${COMPOSE_FILE} config" "" \
    "exit status: 0" "" 0 "Command stdout" "Command stderr" \
    podman compose -f "$COMPOSE_FILE" config || true

log_step "Model files"
record_command "GGUF validation" \
    "uv run ${REPO_DIR}/llmenv.py --config ${CONFIG_PATH} validate-gguf --models-dir ${MODELS_DIR}" "" \
    "exit status: 0" "" 0 "Command stdout" "Command stderr" \
    uv run "${REPO_DIR}/llmenv.py" --config "$CONFIG_PATH" validate-gguf --models-dir "$MODELS_DIR" || true

log_step "VRAM budget"
record_command "VRAM budget" \
    "uv run ${REPO_DIR}/llmenv.py --config ${CONFIG_PATH} budget --models-dir ${MODELS_DIR}" "" \
    "exit status: 0" "" 0 "Command stdout" "Command stderr" \
    uv run "${REPO_DIR}/llmenv.py" --config "$CONFIG_PATH" budget --models-dir "$MODELS_DIR"
budget_status=$?
if [ "$budget_status" -ne 0 ]; then
    record_inferences "" "VRAM budget check failed"
else
    device_name="$(yq -r '.gpu.device_name' "$CONFIG_PATH" 2>/dev/null || true)"
    record_command "GPU device listing" \
        "podman run --rm --device /dev/dri ${image} --list-devices" "" \
        "exit status: 0" "" 0 "Command stdout" "Command stderr" \
        podman run --rm --device /dev/dri "$image" --list-devices
    listing_file="$record_stdout_file"
    record_command "GPU device resolution" \
        "uv run ${REPO_DIR}/llmenv.py resolve-device --device-name ${device_name} --listing-file ${listing_file}" \
        "device name: ${device_name}" "exit status: 0" "" 0 "Command stdout" "Command stderr" \
        uv run "${REPO_DIR}/llmenv.py" resolve-device --device-name "$device_name" --listing-file "$listing_file"
    resolve_status=$?
    device=""
    if [ "$resolve_status" -eq 0 ]; then
        device="$(jq -r '.device // empty' < "$record_stdout_file" 2>/dev/null || true)"
    fi
    if [ "$resolve_status" -ne 0 ]; then
        record_inferences "" "GPU device could not be resolved" ""
    elif [ -z "$device" ]; then
        log_error "GPU device resolution returned no device"
        FAIL=$((FAIL + 1))
        record_inferences "" "GPU device resolution returned no device" ""
    else
        presets_file="$(mktemp "${diagnostic_dir}/presets.XXXXXX")" \
            || die "could not create presets diagnostic"
        presets_status=0
        render_presets_file "$device" "$presets_file" || presets_status=$?
        if [ "$presets_status" -ne 0 ]; then
            log_error "presets rendering failed for ${device} (exit ${presets_status})"
            FAIL=$((FAIL + 1))
            record_inferences "$device" "presets rendering failed" ""
        else
            record_inferences "$device" "" "$presets_file"
        fi
    fi
fi

echo
log_step "Results: ${PASS} passed, ${FAIL} failed"
[ "$FAIL" -eq 0 ]
