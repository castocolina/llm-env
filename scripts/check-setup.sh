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

record_section_skip() {
    local identity="$1" reason="$2"

    log_identity "$identity"
    log_command "${3:-not run: llm-server is disabled}"
    log_block "Input" ""
    log_block "Command stdout" ""
    log_block "Exit status" "SKIP"
    log_block "Expectation" "n/a"
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

llm_server_enabled="$(yq -r '.llm_server.enabled' "$CONFIG_PATH" 2>/dev/null)"

if [ "$llm_server_enabled" != "false" ]; then
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
else
    log_step "GPU access"
    record_section_skip "GPU access" "llm-server is disabled"
    log_step "Configured GPU is present"
    record_section_skip "configured GPU" "llm-server is disabled"
    log_step "Container image"
    record_section_skip "container image" "llm-server is disabled"
fi

system_one_amd_gpu() {
    uv run "${REPO_DIR}/llmenv.py" detect | jq -e --arg p "$1" \
        '.gpus[] | select(.pci_address == $p and .vendor == "0x1002" and .render_node != "")'
}

system_one_rocr_matches() {
    local detected expected
    detected="$(uv run "${REPO_DIR}/llmenv.py" detect | jq -r --arg p "$1" '.gpus[] | select(.pci_address == $p) | .render_node')"
    expected="$(uv run "${REPO_DIR}/llmenv.py" rocr-index --render-node "$detected")"
    [ -n "$detected" ] && [ "$expected" = "$(yq -r '.services["system-one"].environment.ROCR_VISIBLE_DEVICES' "$COMPOSE_FILE")" ]
}

system_one_enabled="$(yq -r '.system_one.enabled' "$CONFIG_PATH" 2>/dev/null)"

phoenix_offline_check() {
    local image health cpus memory network volume observer_image source_hash base_id key labels resource local_kind local_name internal base_image
    image="$(yq -r '.services.phoenix.image // ""' "$REPO_DIR/docker/phoenix/compose.yml")" || { printf 'Phoenix offline check: cannot read pinned image declaration\n' >&2; return 1; }
    [[ "$image" =~ ^docker.io/arizephoenix/phoenix:20\.23\.0@sha256:[0-9a-f]{64}$ ]] || { printf 'Phoenix offline check: image must use the pinned Phoenix digest\n' >&2; return 1; }
    health="$(yq -r '.services.phoenix.healthcheck.test[1] // ""' "$REPO_DIR/docker/phoenix/compose.yml")" || { printf 'Phoenix offline check: cannot read healthcheck executable\n' >&2; return 1; }
    [[ "$health" == /usr/bin/python3.13 ]] || { printf 'Phoenix offline check: healthcheck must use /usr/bin/python3.13\n' >&2; return 1; }
    cpus="$(yq -r '.services.phoenix.cpus // ""' "$REPO_DIR/docker/phoenix/compose.yml")"
    memory="$(yq -r '.services.phoenix.mem_limit // ""' "$REPO_DIR/docker/phoenix/compose.yml")"
    [[ "$cpus" == "\${PHOENIX_CPUS_MAX:?}" && "$memory" == "\${PHOENIX_MEMORY_MAX_MIB:?}m" ]] || { printf 'Phoenix offline check: CPU and memory limits must use configured maxima\n' >&2; return 1; }
    llmenv --config "$CONFIG_PATH" effective-policy --service phoenix |
        jq -e '(.cpus_min | type) == "number" and (.cpus_max | type) == "number" and
          .cpus_min > 0 and .cpus_max >= .cpus_min and
          (.memory_min_mib | type) == "number" and (.memory_max_mib | type) == "number" and
          .memory_min_mib > 0 and .memory_max_mib >= .memory_min_mib and
          (.memory_min_mib | floor == .) and (.memory_max_mib | floor == .)' >/dev/null || { printf 'Phoenix offline check: resource policy must have positive ordered CPU and integral memory bounds\n' >&2; return 1; }
    network="$(yq -r '.networks.internal.name // ""' "$REPO_DIR/docker/phoenix/compose.yml")"
    volume="$(yq -r '.volumes.data.name // ""' "$REPO_DIR/docker/phoenix/compose.yml")"
    [[ "$network" == *llm-env-phoenix_internal* && "$volume" == "\${PHOENIX_VOLUME:?}" ]] || { printf 'Phoenix offline check: network and volume declarations do not match owned resources\n' >&2; return 1; }
    volume="llm-env-phoenix-data"
    network="llm-env-phoenix_internal"
    podman image exists "$image" || { printf 'Phoenix offline check: pinned Phoenix image is missing; use the authorized startup workflow\n' >&2; return 1; }
    for resource in "volume:$volume" "network:$network"; do
        local_kind="${resource%%:*}"; local_name="${resource#*:}"
        if ! podman "$local_kind" exists "$local_name" >/dev/null 2>&1; then
            # Not created until Phoenix's first startup is an expected offline skip.
            printf 'SKIP %s %s: not created before first Phoenix startup\n' "$local_kind" "$local_name"
            continue
        fi
        labels="$(podman "$local_kind" inspect --format '{{json .Labels}}' "$local_name" 2>/dev/null)" || { printf 'Phoenix offline check: cannot inspect owned %s metadata\n' "$local_kind" >&2; return 1; }
        jq -e '.["llm-env.scope"] == "phoenix" and .["llm-env.project"] == "llm-env-phoenix"' <<<"$labels" >/dev/null || { printf 'Phoenix offline check: %s has missing or foreign ownership labels\n' "$local_kind" >&2; return 1; }
        if [[ "$local_kind" == network ]]; then
            internal="$(podman network inspect --format '{{.Internal}}' "$local_name" 2>/dev/null)" || { printf 'Phoenix offline check: cannot inspect internal network setting\n' >&2; return 1; }
            [[ "$internal" == true ]] || { printf 'Phoenix offline check: network must be internal\n' >&2; return 1; }
        fi
    done
    observer_image="$(yq -r '.services["system-one"].image // ""' "$COMPOSE_FILE" 2>/dev/null)"
    [[ "$observer_image" == *llm-env-system-one-phoenix:* ]] || { printf 'Phoenix offline check: System One observer image declaration is missing\n' >&2; return 1; }
    [[ "$(yq -r '.services["system-one"].environment.LAYA_PHOENIX_URL // ""' "$COMPOSE_FILE")" == http://phoenix:6006/v1/projects/laya/spans ]] || { printf 'Phoenix offline check: observer endpoint must target Phoenix spans\n' >&2; return 1; }
    yq -e '.services["system-one"].networks | contains(["phoenix-trace"])' "$COMPOSE_FILE" >/dev/null || { printf 'Phoenix offline check: System One must join the trace network\n' >&2; return 1; }
    yq -e '.networks["phoenix-trace"].external == true' "$COMPOSE_FILE" >/dev/null || { printf 'Phoenix offline check: trace network must be external\n' >&2; return 1; }
    source_hash="$(cat "$REPO_DIR/docker/system-one/observer.Dockerfile" "$REPO_DIR/docker/system-one/phoenix_observer.py" | sha256sum | awk '{print $1}')"
    base_image="$(yq -r '.system_one.image' "$CONFIG_PATH")"
    base_id="$(podman image inspect "$base_image" --format '{{.Id}}' 2>/dev/null)" || { printf 'Phoenix offline check: cannot inspect configured System One base image\n' >&2; return 1; }
    key="$(printf '%s\n%s\n' "$base_id" "$source_hash" | sha256sum | awk '{print substr($1,1,12)}')"
    [[ "$observer_image" == "localhost/llm-env-system-one-phoenix:${key}" ]] || { printf 'Phoenix offline check: observer image is stale; rebuild from current sources\n' >&2; return 1; }
    podman image inspect "$observer_image" | jq -e --arg key "$key" '.[0].Labels["llm-env.phoenix-observer-sha256"] == $key' >/dev/null || { printf 'Phoenix offline check: observer freshness label is stale; rebuild the image\n' >&2; return 1; }
}

log_step "System One"
if [ "$system_one_enabled" = "true" ]; then
    system_one_image="$(yq -r '.system_one.image' "$CONFIG_PATH")"
    kfd_path="${LLM_ENV_KFD_PATH:-/dev/kfd}"
    pci="$(yq -r '.gpu.pci_address' "$CONFIG_PATH")"
    record_command "ROCm device" "test -r ${kfd_path}" "" \
        "exit status: 0" "" 0 "Command stdout" "Command stderr" test -r "$kfd_path" || true
    record_command "ROCm GPU" \
        "uv run ${REPO_DIR}/llmenv.py detect | jq -e --arg p ${pci} '.gpus[] | select(.pci_address == \$p and .vendor == \"0x1002\" and .render_node != \"\")'" \
        "" "exit status: 0" "" 0 "Command stdout" "Command stderr" \
        system_one_amd_gpu "$pci" || true
    if [ ! -f "$COMPOSE_FILE" ]; then
        record_section_skip "system-one ROCR index" "compose not rendered yet (run make start)" "not run: compose not rendered yet (run make start)"
    elif [ "$(yq -r '.services["system-one"].environment.ROCR_VISIBLE_DEVICES' "$COMPOSE_FILE")" = "null" ]; then
        record_section_skip "system-one ROCR index" "compose not rendered with system-one yet (run make start)" "not run: compose not rendered with system-one yet (run make start)"
    else
        record_command "system-one ROCR index" \
            "llmenv rocr-index for the detected render node equals ROCR_VISIBLE_DEVICES in ${COMPOSE_FILE}" \
            "" "exit status: 0" "" 0 "Command stdout" "Command stderr" \
            system_one_rocr_matches "$pci" || true
    fi
    record_command "system-one image" "podman image exists ${system_one_image}" "" \
        "exit status: 0" "" 0 "Command stdout" "Command stderr" \
        podman image exists "$system_one_image" || true
    record_command "system-one weights cache" "podman volume exists llm-env-system-one-cache" "" \
        "exit status: 0" "" 0 "Command stdout" "Command stderr" \
        podman volume exists llm-env-system-one-cache || true
    system_one_models="$(yq -r '.system_one.models' "$CONFIG_PATH")"
    record_command "system-one weights present" \
        "podman run --rm -e LAYA_REVISION=reviewed -e LAYA_MODELS=${system_one_models} -v llm-env-system-one-cache:/home/laya/.cache/huggingface -v ${REPO_DIR}/scripts/system-one-cache-check.py:/opt/system-one-cache-check.py:ro,z --entrypoint python3 ${system_one_image} /opt/system-one-cache-check.py verify" \
        "" "exit status: 0" "" 0 "Command stdout" "Command stderr" \
        podman run --rm \
            -e LAYA_REVISION=reviewed -e "LAYA_MODELS=${system_one_models}" \
            -v llm-env-system-one-cache:/home/laya/.cache/huggingface \
            -v "${REPO_DIR}/scripts/system-one-cache-check.py:/opt/system-one-cache-check.py:ro,z" \
            --entrypoint python3 "$system_one_image" /opt/system-one-cache-check.py verify || true
    if [ -f "$COMPOSE_FILE" ] && [ "$(yq -r '.services["system-one"] // "null"' "$COMPOSE_FILE")" != null ]; then
        record_command "Phoenix deployment metadata" "offline Phoenix image, observer freshness, resource, network and history-volume declarations; validate owned resource labels when present" "" "declarations valid; resources absent before first startup are skipped; foreign labels fail" "" 0 "Scoped metadata" "Command stderr" phoenix_offline_check || true
    else
        log_warn "Verdict: SKIP identity=Phoenix deployment metadata reason=system-one compose service not rendered yet"
    fi
else
    log_warn "Verdict: SKIP reason=system-one is disabled"
    log_step "Phoenix deployment metadata"
    log_warn "Verdict: SKIP identity=Phoenix deployment metadata reason=System One is disabled"
fi

log_step "Compose file"
record_command "compose file syntax" \
    "podman compose -f ${COMPOSE_FILE} config" "" \
    "exit status: 0" "" 0 "Command stdout" "Command stderr" \
    podman compose -f "$COMPOSE_FILE" config || true

log_step "Model files"
if [ "$llm_server_enabled" != "false" ]; then
    record_command "GGUF validation" \
        "uv run ${REPO_DIR}/llmenv.py --config ${CONFIG_PATH} validate-gguf --models-dir ${MODELS_DIR}" "" \
        "exit status: 0" "" 0 "Command stdout" "Command stderr" \
        uv run "${REPO_DIR}/llmenv.py" --config "$CONFIG_PATH" validate-gguf --models-dir "$MODELS_DIR" || true
else
    record_section_skip "GGUF validation" "llm-server is disabled"
fi

if [ "$llm_server_enabled" != "false" ]; then
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
else
    log_step "VRAM budget"
    record_section_skip "VRAM budget" "llm-server is disabled"
    log_step "Offline inference"
    record_inference_skip "n/a" "not run: no enabled models to check" "llm-server is disabled"
fi

echo
log_step "Results: ${PASS} passed, ${FAIL} failed"
[ "$FAIL" -eq 0 ]
