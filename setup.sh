#!/usr/bin/env bash
# setup.sh — interactive configurator.
set -euo pipefail
# shellcheck source=lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

ASSUME_YES="${LLM_ENV_ASSUME_YES:-0}"

bash "${REPO_DIR}/prerequisites.sh" --check || die "missing prerequisites; run 'make prerequisites'"

# ask PROMPT DEFAULT -> echoes the answer, or DEFAULT when running unattended
ask() {
    local prompt="$1" default="$2" reply
    if [ "$ASSUME_YES" = "1" ]; then
        printf '%s%s\n' "$prompt" "$default" >&2
        printf '%s' "$default"
        return 0
    fi
    read -rp "$prompt" reply
    printf '%s' "${reply:-$default}"
}

require_cmd uv jq yq podman curl

log_step "Step 1/7  Creating configuration"
if [ -f "$CONFIG_PATH" ]; then
    log_info "using existing config at ${CONFIG_PATH}"
else
    llmenv --config "$CONFIG_PATH" init --template "${REPO_DIR}/models.yml.example" >/dev/null
    log_info "created ${CONFIG_PATH} from template"
fi

log_step "Step 2/7  Detecting GPUs"
facts="$(llmenv detect)"
gpu_count="$(echo "$facts" | jq '.gpus | length')"
[ "$gpu_count" -gt 0 ] || die "no VRAM-backed GPUs detected"
echo "$facts" | jq -r --arg green "$GREEN" --arg nc "$NC" '
  .gpus | to_entries[] |
  "  \(.key + 1)) \($green)\(.value.card)\($nc)  \(.value.pci_address)  \(.value.vram_total_mib) MiB total  \(.value.vram_used_mib) MiB used  \(.value.vram_total_mib - .value.vram_used_mib) MiB free  \(.value.render_node)  " +
  (if (.value.connected_outputs | length) > 0 then "displays: \(.value.connected_outputs | join(","))" else "headless" end)'
default_gpu="$(echo "$facts" | jq -r '.gpus | to_entries | max_by(.value.vram_total_mib) | .key + 1')"
gpu_choice="$(ask "  GPU number [${default_gpu}]: " "$default_gpu")"
[[ "$gpu_choice" =~ ^[1-9][0-9]*$ ]] || die "GPU selection must be a positive integer"
[ "$gpu_choice" -le "$gpu_count" ] || die "GPU selection is out of range"
gpu="$(echo "$facts" | jq --argjson index "$gpu_choice" '.gpus[$index - 1]')"
pci="$(echo "$gpu" | jq -r '.pci_address')"
vram_total="$(echo "$gpu" | jq -r '.vram_total_mib')"
vram_used="$(echo "$gpu" | jq -r '.vram_used_mib')"
vram_free="$((vram_total - vram_used))"
log_info "selected ${pci} with ${vram_total} MiB total, ${vram_used} MiB used, ${vram_free} MiB free"

log_step "Step 3/7  Selecting models"
if [ -f "$CONFIG_PATH" ]; then
    yq -i 'del(.models[] | select(.alias == "openhermes"))' "$CONFIG_PATH"
fi
models="$(llmenv --config "$CONFIG_PATH" models list)"
model_count="$(echo "$models" | jq '.models | length')"
[ "$model_count" -gt 0 ] || die "no models are configured"
yq -r '.models | to_entries[] | "  \(.key + 1)) \(.value.label) — \(.value.parameters), \(.value.quantization), \(.value.size_bytes / 1000000000) GB"' "$CONFIG_PATH"
default_models="$(echo "$models" | jq -r '[.models | to_entries[] | select(.value.enabled) | (.key + 1 | tostring)] | join(",")')"
model_choice="$(ask "  Model numbers [${default_models}]: " "$default_models")"
[[ "$model_choice" =~ ^[1-9][0-9]*(,[1-9][0-9]*)*$ ]] || die "model selection must be comma-separated positive integers"
IFS=',' read -r -a model_indexes <<< "$model_choice"
declare -A selected_indexes=()
aliases=()
for index in "${model_indexes[@]}"; do
    [ "$index" -le "$model_count" ] || die "model selection is out of range"
    [ -z "${selected_indexes[$index]:-}" ] || die "model selection contains duplicate index ${index}"
    selected_indexes[$index]=1
    aliases+=("$(echo "$models" | jq -r --argjson index "$index" '.models[$index - 1].alias')")
done
llmenv --config "$CONFIG_PATH" models select "${aliases[@]}" >/dev/null
log_info "selected ${aliases[*]}"

log_step "Step 4/7  Downloading models"
mkdir -p "$MODELS_DIR"
while IFS=$'\t' read -r file url; do
    [ -n "$file" ] || continue
    if [ -f "${MODELS_DIR}/${file}" ]; then
        log_info "${file} already present"
        continue
    fi
    log_info "downloading ${file}"
    curl -fL --continue-at - --progress-bar "$url" -o "${MODELS_DIR}/${file}" \
      || die "download failed: ${url}"
done < <(yq -r '.models[] | select(.enabled) | [.file, .url] | @tsv' "$CONFIG_PATH")

log_step "Step 5/7  Validating model files"
llmenv --config "$CONFIG_PATH" validate-gguf --models-dir "$MODELS_DIR" \
  | jq -r '.results[] | "  \(if .valid then "ok  " else "FAIL" end) \(.alias): \(.message)"' \
  || die "one or more model files are not valid GGUF"

log_step "Step 6/7  Preparing Vulkan"
podman pull ghcr.io/ggml-org/llama.cpp:server-vulkan >/dev/null
vulkan_listing="podman run --rm --device /dev/dri ghcr.io/ggml-org/llama.cpp:server-vulkan --list-devices"
devices="$(llmenv list-devices --list-command "$vulkan_listing")"
matching_candidates="$(echo "$devices" | jq --argjson total "$vram_total" '[.devices[] | select(.total_mib == $total)]')"
match_count="$(echo "$matching_candidates" | jq 'length')"
if [ "$match_count" -eq 1 ]; then
    device_name="$(echo "$matching_candidates" | jq -r '.[0].name')"
else
    if [ "$match_count" -eq 0 ]; then
        candidates="$(echo "$devices" | jq '.devices')"
    else
        candidates="$matching_candidates"
    fi
    candidate_count="$(echo "$candidates" | jq 'length')"
    echo "$candidates" | jq -r 'to_entries[] | "  \(.key + 1)) \(.value.name)  \(.value.total_mib) MiB"'
    [ "$candidate_count" -gt 0 ] || die "no Vulkan devices detected"
    device_choice="$(ask "  Vulkan device number: " "")"
    [[ "$device_choice" =~ ^[1-9][0-9]*$ ]] || die "Vulkan device selection must be a positive integer"
    [ "$device_choice" -le "$candidate_count" ] || die "Vulkan device selection is out of range"
    device_name="$(echo "$candidates" | jq -r --argjson index "$device_choice" '.[$index - 1].name')"
fi
PCI_ADDRESS="$pci" VRAM_TOTAL_MIB="$vram_total" DEVICE_NAME="$device_name" \
  yq -i '
    .gpu.pci_address = strenv(PCI_ADDRESS) |
    .gpu.vram_total_mib = (strenv(VRAM_TOTAL_MIB) | tonumber) |
    .gpu.device_name = strenv(DEVICE_NAME)
  ' "$CONFIG_PATH"
chmod 600 "$CONFIG_PATH"
log_info "prepared ${device_name} for ${pci}"

log_step "Step 7/7  Checking the VRAM budget"
budget_json="$(mktemp)"
trap 'rm -f "$budget_json"' EXIT
if llmenv --config "$CONFIG_PATH" budget --models-dir "$MODELS_DIR" > "$budget_json"; then
    jq -r '"  available \(.available_mib) MiB, required \(.required_mib) MiB — fits"' "$budget_json"
else
    jq -r '"  available \(.available_mib) MiB, required \(.required_mib) MiB — SHORT BY \(.shortfall_mib) MiB"' "$budget_json"
    jq -r '.remedies[] | "    - \(.)"' "$budget_json"
    log_warn "models_max=$(jq -r .models_max "$budget_json") exceeds the VRAM budget"
fi

echo
log_info "Setup complete. Next: make check-setup"
