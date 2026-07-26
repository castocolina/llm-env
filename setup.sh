#!/usr/bin/env bash
# setup.sh — interactive configurator.
set -euo pipefail
# shellcheck source=lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

ASSUME_YES="${LLM_ENV_ASSUME_YES:-0}"

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

log_step "Step 1/8  Creating configuration"
if [ -f "$CONFIG_PATH" ]; then
    log_info "using existing config at ${CONFIG_PATH}"
else
    llmenv --config "$CONFIG_PATH" init --template "${REPO_DIR}/models.yml.example" >/dev/null
    log_info "created ${CONFIG_PATH} from template"
fi

log_step "Step 2/8  Detecting GPUs"
facts="$(llmenv detect)"
echo "$facts" | jq -r '
  .gpus[] |
  "  \(.card)  \(.pci_address)  \(.vram_total_mib) MiB  " +
  (if (.connected_outputs | length) > 0 then "displays: \(.connected_outputs | join(","))" else "headless" end)'

default_pci="$(echo "$facts" | jq -r '[.gpus[]] | max_by(.vram_total_mib) | .pci_address')"
pci="$(ask "  PCI address to use for inference [${default_pci}]: " "$default_pci")"
pci="${pci:-$default_pci}"

gpu="$(echo "$facts" | jq --arg p "$pci" '.gpus[] | select(.pci_address == $p)')"
[ -n "$gpu" ] || die "no GPU with PCI address ${pci}"
vram_total="$(echo "$gpu" | jq -r '.vram_total_mib')"
log_info "selected ${pci} with ${vram_total} MiB VRAM"

log_step "Step 3/8  Selecting models"
llmenv --config "$CONFIG_PATH" models list \
  | jq -r '.models[] | "  \(if .enabled then "[x]" else "[ ]" end) \(.alias)  \(.file)"'
echo "  Toggle with: make setup, or 'uv run llmenv.py models enable <alias>'"
alias_toggle="$(ask "  Toggle any alias now (blank to keep current): " "")"
if [ -n "$alias_toggle" ]; then
    current="$(llmenv --config "$CONFIG_PATH" models list \
      | jq -r --arg a "$alias_toggle" '.models[] | select(.alias==$a) | .enabled')"
    [ -n "$current" ] || die "unknown alias: ${alias_toggle}"
    if [ "$current" = "true" ]; then action=disable; else action=enable; fi
    llmenv --config "$CONFIG_PATH" models "$action" "$alias_toggle" >/dev/null
    log_info "${action}d ${alias_toggle}"
fi

log_step "Step 4/8  Downloading models"
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

log_step "Step 5/8  Validating model files"
llmenv --config "$CONFIG_PATH" validate-gguf --models-dir "$MODELS_DIR" \
  | jq -r '.results[] | "  \(if .valid then "ok  " else "FAIL" end) \(.alias): \(.message)"' \
  || die "one or more model files are not valid GGUF"

log_step "Step 6/8  Generating API key and storing GPU selection"
existing_key="$(yq -r '.server.api_key // ""' "$CONFIG_PATH")"
if [ -n "$existing_key" ] && [ "$existing_key" != "null" ] \
   && [ "${LLM_ENV_ROTATE_KEY:-0}" != "1" ]; then
    api_key="$existing_key"
    log_info "kept the existing API key (set LLM_ENV_ROTATE_KEY=1 to rotate)"
else
    # 48 random bytes survive the charset filter with room to spare for 32 chars.
    api_key="$(head -c 48 /dev/urandom | base64 | tr -d '/+=' | head -c 32)"
    yq -i ".server.api_key = \"${api_key}\"" "$CONFIG_PATH"
    log_info "generated a new API key"
fi
device_name="$(echo "$gpu" | jq -r '.card')"
yq -i ".gpu.pci_address = \"${pci}\"" "$CONFIG_PATH"
yq -i ".gpu.vram_total_mib = ${vram_total}" "$CONFIG_PATH"
log_info "api key stored in ${CONFIG_PATH}"
log_warn "device_name is set during 'make benchmark'; run it before first start (card: ${device_name})"
chmod 600 "$CONFIG_PATH"

log_step "Step 7/8  Checking the VRAM budget"
if llmenv --config "$CONFIG_PATH" budget --models-dir "$MODELS_DIR" > /tmp/llm-budget.json; then
    jq -r '"  available \(.available_mib) MiB, required \(.required_mib) MiB — fits"' /tmp/llm-budget.json
else
    jq -r '"  available \(.available_mib) MiB, required \(.required_mib) MiB — SHORT BY \(.shortfall_mib) MiB"' /tmp/llm-budget.json
    jq -r '.remedies[] | "    - \(.)"' /tmp/llm-budget.json
    log_warn "models_max=$(jq -r .models_max /tmp/llm-budget.json) exceeds the VRAM budget"
fi

echo
log_info "Setup complete. Next: make benchmark, then make start"
