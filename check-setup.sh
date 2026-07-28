#!/usr/bin/env bash
# check-setup.sh — offline validation. No server required.
set -uo pipefail
# shellcheck source=lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
set +e

PASS=0; FAIL=0

check() {
    local label="$1"; shift
    if "$@" >/dev/null 2>&1; then
        log_info "$label"; PASS=$((PASS + 1))
    else
        log_error "$label"; FAIL=$((FAIL + 1))
    fi
}

log_step "Tooling"
for cmd in uv jq yq podman systemctl curl; do
    check "command available: ${cmd}" command -v "$cmd"
done

log_step "Configuration"
check "config exists at ${CONFIG_PATH}" test -f "$CONFIG_PATH"
check "config parses and validates" bash -c \
    "uv run '${REPO_DIR}/llmenv.py' --config '${CONFIG_PATH}' models list"

log_step "GPU access"
check "/dev/dri exists" test -d /dev/dri
pci="$(yq -r '.gpu.pci_address' "$CONFIG_PATH" 2>/dev/null || echo "")"
render_node="$(uv run "${REPO_DIR}/llmenv.py" detect 2>/dev/null \
  | jq -r --arg p "$pci" '.gpus[] | select(.pci_address==$p) | .render_node')"
if [ -n "$render_node" ] && [ "$render_node" != "null" ]; then
    check "render node /dev/dri/${render_node} is readable" test -r "/dev/dri/${render_node}"
else
    log_error "no render node found for ${pci}"
    FAIL=$((FAIL + 1))
fi
log_step "Configured GPU is present"
check "GPU ${pci} detected" bash -c \
    "uv run '${REPO_DIR}/llmenv.py' detect | jq -e --arg p '${pci}' '.gpus[] | select(.pci_address==\$p)'"

log_step "Container image"
image="$(yq -r '.gpu.image' "$CONFIG_PATH" 2>/dev/null || echo "")"
check "image ${image} present locally" podman image exists "$image"

log_step "Model files"
if out="$(uv run "${REPO_DIR}/llmenv.py" --config "$CONFIG_PATH" \
          validate-gguf --models-dir "$MODELS_DIR" 2>/dev/null)"; then
    echo "$out" | jq -r '.results[] | "  ok   \(.alias)"'
    PASS=$((PASS + 1))
else
    echo "$out" | jq -r '.results[]? | "  \(if .valid then "ok  " else "FAIL" end) \(.alias): \(.message)"'
    log_error "one or more model files are invalid"
    FAIL=$((FAIL + 1))
fi

log_step "VRAM budget"
if out="$(uv run "${REPO_DIR}/llmenv.py" --config "$CONFIG_PATH" \
          budget --models-dir "$MODELS_DIR" 2>/dev/null)"; then
    echo "$out" | jq -r '"  available \(.available_mib) MiB, required \(.required_mib) MiB"'
    log_info "budget is feasible for models_max=$(echo "$out" | jq -r .models_max)"
    PASS=$((PASS + 1))

    log_step "Offline inference"
    device_name="$(yq -r '.gpu.device_name' "$CONFIG_PATH" 2>/dev/null || echo "")"
    listing_file="$(mktemp)"
    podman run --rm --device /dev/dri \
        "$image" --list-devices >"$listing_file" 2>/dev/null || true
    if resolved="$(uv run "${REPO_DIR}/llmenv.py" resolve-device --device-name "$device_name" \
                    --listing-file "$listing_file")"; then
        device="$(echo "$resolved" | jq -r '.device')"
        log_info "resolved ${device_name} to ${device}"
        while IFS=$'\t' read -r alias file layers; do
            if output="$(timeout 180 podman run --rm --device /dev/dri \
                -v "${MODELS_DIR}:/models:ro,z" \
                --entrypoint /app/llama "$image" cli \
                -m "/models/${file}" --device "$device" \
                --n-gpu-layers "$layers" --single-turn -p "Reply with exactly: ready" -n 16 2>&1)" \
                && [ -n "$output" ]; then
                log_info "inference ${alias}"
                PASS=$((PASS + 1))
            else
                diagnostic="$(printf '%s' "$output" | head -c 1000)"
                [ -n "$diagnostic" ] || diagnostic="no output"
                log_error "inference ${alias} failed: ${diagnostic}"
                FAIL=$((FAIL + 1))
            fi
        done < <(yq -r '.models[] | select(.enabled) | [.alias, .file, .n_gpu_layers] | @tsv' "$CONFIG_PATH")
    else
        log_error "could not resolve GPU device ${device_name}"
        FAIL=$((FAIL + 1))
    fi
    rm -f "$listing_file"
else
    if err="$(echo "$out" | jq -r '.error // empty' 2>/dev/null)" && [ -n "$err" ]; then
        log_error "budget check failed: ${err}"
    else
        echo "$out" | jq -r '"  short by \(.shortfall_mib) MiB"' 2>/dev/null
        echo "$out" | jq -r '.remedies[]? | "    - \(.)"' 2>/dev/null
        log_error "budget exceeded"
    fi
    FAIL=$((FAIL + 1))
fi

echo
log_step "Results: ${PASS} passed, ${FAIL} failed"
[ "$FAIL" -eq 0 ]
