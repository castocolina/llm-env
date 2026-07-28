#!/usr/bin/env bash
# check-setup.sh — offline validation. No server required.
set -uo pipefail
# shellcheck source=lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
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
    local expected_result="$5" stdout_label="$6" stderr_label="$7"
    local stdout_file stderr_file status parsed normalized
    shift 7

    stdout_file="$(mktemp "${diagnostic_dir}/stdout.XXXXXX")" || die "could not create diagnostic stdout"
    stderr_file="$(mktemp "${diagnostic_dir}/stderr.XXXXXX")" || die "could not create diagnostic stderr"
    record_stdout_file="$stdout_file"

    log_identity "$identity"
    log_command "$command_text"
    log_block "Input" "$input"
    if "$@" >"$stdout_file" 2>"$stderr_file"; then status=0; else status=$?; fi
    log_block "$stdout_label" "$(<"$stdout_file")"
    log_block "$stderr_label" "$(<"$stderr_file")"
    log_block "Exit status" "$status"
    parsed="$(<"$stdout_file")"
    log_block "Parsed result" "$parsed"
    if [ -n "$expected_result" ]; then
        normalized="$(printf '%s' "$parsed" | tr '[:upper:]' '[:lower:]' | \
            sed -E 's/^[[:space:][:punct:]]+//; s/[[:space:][:punct:]]+$//')"
    fi
    log_block "Expectation" "$expectation"

    if [ "$status" -ne 0 ]; then
        log_error "Verdict: FAIL stage=command exit reason=${identity%% *} exited ${status}"
        FAIL=$((FAIL + 1))
        return 1
    fi
    if [ -n "$expected_result" ] && [ "$normalized" != "$expected_result" ]; then
        log_error "Verdict: FAIL stage=parsed result reason=normalized assistant content mismatch expected=${expected_result}"
        FAIL=$((FAIL + 1))
        return 1
    fi

    log_info "Verdict: PASS"
    PASS=$((PASS + 1))
}

record_inference_skip() {
    local alias="$1" command_text="$2" reason="$3"

    log_identity "inference ${alias}"
    log_command "$command_text"
    log_block "Input" "Reply with exactly: ready"
    log_block "Inference stdout" ""
    log_block "Inference stderr" ""
    log_block "Exit status" "SKIP"
    log_block "Parsed result" ""
    log_block "Expectation" "normalized assistant content: ready"
    log_warn "Verdict: SKIP reason=${reason}"
}

inference_command() {
    local file="$1" device="$2" layers="$3"
    printf 'timeout 180 podman run --rm --device /dev/dri -v %q:/models:ro,z --entrypoint /app/llama %q cli -m %q --device %q --n-gpu-layers %q --single-turn -p %q -n 16' \
        "$MODELS_DIR" "$image" "/models/${file}" "$device" "$layers" "Reply with exactly: ready"
}

record_inferences() {
    local device="$1" skip_reason="${2:-}" alias file layers command_text

    log_step "Offline inference"
    while IFS=$'\t' read -r alias file layers; do
        command_text="$(inference_command "$file" "$device" "$layers")"
        if [ -n "$skip_reason" ]; then
            record_inference_skip "$alias" "$command_text" "$skip_reason"
        else
            record_command "inference ${alias}" "$command_text" "Reply with exactly: ready" \
                "normalized assistant content: ready" "ready" "Inference stdout" "Inference stderr" \
                timeout 180 podman run --rm --device /dev/dri \
                -v "${MODELS_DIR}:/models:ro,z" \
                --entrypoint /app/llama "$image" cli \
                -m "/models/${file}" --device "$device" \
                --n-gpu-layers "$layers" --single-turn -p "Reply with exactly: ready" -n 16 || true
        fi
    done < <(yq -r '.models[] | select(.enabled) | [.alias, .file, .n_gpu_layers] | @tsv' "$CONFIG_PATH")
}

log_step "Tooling"
for cmd in uv jq yq podman systemctl curl; do
    record_command "tooling command ${cmd}" "command -v ${cmd}" "" \
        "exit status: 0" "" "Command stdout" "Command stderr" command -v "$cmd" || true
done

log_step "Configuration"
record_command "configuration file" "test -f ${CONFIG_PATH}" "" \
    "exit status: 0" "" "Command stdout" "Command stderr" test -f "$CONFIG_PATH" || true
record_command "configuration validation" \
    "uv run ${REPO_DIR}/llmenv.py --config ${CONFIG_PATH} models list" "" \
    "exit status: 0" "" "Command stdout" "Command stderr" \
    uv run "${REPO_DIR}/llmenv.py" --config "$CONFIG_PATH" models list || true

pci="$(yq -r '.gpu.pci_address' "$CONFIG_PATH" 2>/dev/null || true)"
image="$(yq -r '.gpu.image' "$CONFIG_PATH" 2>/dev/null || true)"

log_step "GPU access"
record_command "GPU directory" "test -d /dev/dri" "" \
    "exit status: 0" "" "Command stdout" "Command stderr" test -d /dev/dri || true
record_command "GPU detection" \
    "uv run ${REPO_DIR}/llmenv.py detect" "configured PCI address: ${pci}" \
    "GPU record for ${pci}" "" "Command stdout" "Command stderr" \
    uv run "${REPO_DIR}/llmenv.py" detect
detect_status=$?
detected_gpus="$(<"$record_stdout_file")"
render_node="$(jq -r --arg p "$pci" '.gpus[] | select(.pci_address==$p) | .render_node' <<<"$detected_gpus")"
if [ "$detect_status" -eq 0 ] && [ -n "$render_node" ] && [ "$render_node" != "null" ]; then
    record_command "GPU render node" "test -r /dev/dri/${render_node}" "" \
        "exit status: 0" "" "Command stdout" "Command stderr" test -r "/dev/dri/${render_node}" || true
else
    log_identity "GPU render node"
    log_command "test -r /dev/dri/<resolved-render-node>"
    log_block "Input" "configured PCI address: ${pci}"
    log_block "Command stdout" ""
    log_block "Command stderr" ""
    log_block "Exit status" "SKIP"
    log_block "Parsed result" ""
    log_block "Expectation" "a detected readable render node"
    log_warn "Verdict: SKIP reason=GPU detection did not provide a render node"
fi

log_step "Configured GPU is present"
record_command "configured GPU" \
    "uv run ${REPO_DIR}/llmenv.py detect | jq -e --arg p ${pci} '.gpus[] | select(.pci_address==\$p)'" \
    "configured PCI address: ${pci}" "exit status: 0" "" "Command stdout" "Command stderr" \
    bash -c "uv run '${REPO_DIR}/llmenv.py' detect | jq -e --arg p '${pci}' '.gpus[] | select(.pci_address==\$p)'" || true

log_step "Container image"
record_command "container image" "podman image exists ${image}" "" \
    "exit status: 0" "" "Command stdout" "Command stderr" podman image exists "$image" || true

log_step "Model files"
record_command "GGUF validation" \
    "uv run ${REPO_DIR}/llmenv.py --config ${CONFIG_PATH} validate-gguf --models-dir ${MODELS_DIR}" "" \
    "exit status: 0" "" "Command stdout" "Command stderr" \
    uv run "${REPO_DIR}/llmenv.py" --config "$CONFIG_PATH" validate-gguf --models-dir "$MODELS_DIR" || true

log_step "VRAM budget"
record_command "VRAM budget" \
    "uv run ${REPO_DIR}/llmenv.py --config ${CONFIG_PATH} budget --models-dir ${MODELS_DIR}" "" \
    "exit status: 0" "" "Command stdout" "Command stderr" \
    uv run "${REPO_DIR}/llmenv.py" --config "$CONFIG_PATH" budget --models-dir "$MODELS_DIR"
budget_status=$?
if [ "$budget_status" -ne 0 ]; then
    record_inferences "" "VRAM budget check failed"
else
    device_name="$(yq -r '.gpu.device_name' "$CONFIG_PATH" 2>/dev/null || true)"
    record_command "GPU device listing" \
        "podman run --rm --device /dev/dri ${image} --list-devices" "" \
        "exit status: 0" "" "Command stdout" "Command stderr" \
        podman run --rm --device /dev/dri "$image" --list-devices
    listing_status=$?
    listing_file="$record_stdout_file"
    record_command "GPU device resolution" \
        "uv run ${REPO_DIR}/llmenv.py resolve-device --device-name ${device_name} --listing-file ${listing_file}" \
        "device name: ${device_name}" "exit status: 0" "" "Command stdout" "Command stderr" \
        uv run "${REPO_DIR}/llmenv.py" resolve-device --device-name "$device_name" --listing-file "$listing_file"
    resolve_status=$?
    resolved="$(<"$record_stdout_file")"
    device="$(jq -r '.device // empty' <<<"$resolved")"
    if [ "$listing_status" -eq 0 ] && [ "$resolve_status" -eq 0 ] && [ -n "$device" ]; then
        record_inferences "$device"
    else
        record_inferences "" "GPU device could not be resolved"
    fi
fi

echo
log_step "Results: ${PASS} passed, ${FAIL} failed"
[ "$FAIL" -eq 0 ]
