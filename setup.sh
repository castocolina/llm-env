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

log_step "Step 8/8  Network exposure"
port="$(yq -r '.server.port' "$CONFIG_PATH")"
mdns="$(yq -r '.server.mdns_name' "$CONFIG_PATH")"

if command -v firewall-cmd >/dev/null 2>&1; then
    if firewall-cmd --query-port="${port}/tcp" >/dev/null 2>&1; then
        log_info "firewall port ${port}/tcp already open"
    else
        open_port="$(ask "  Open firewall port ${port}/tcp for LAN access? (yes/no) " "no")"
        if [ "$open_port" = "yes" ]; then
            if sudo firewall-cmd --permanent --add-port="${port}/tcp" >/dev/null \
              && sudo firewall-cmd --reload >/dev/null; then
                log_info "opened ${port}/tcp"
            else
                log_warn "could not open the port; do it manually"
            fi
        else
            log_warn "port not opened; other machines will not be able to connect"
        fi
    fi
else
    log_warn "firewall-cmd not found; skipping firewall configuration"
fi

if command -v avahi-publish >/dev/null 2>&1; then
    mkdir -p "${HOME}/.config/systemd/user"
    cat > "${HOME}/.config/systemd/user/llm-mdns.service" <<EOF
[Unit]
Description=Publish ${mdns}.local for the LLM server
After=network-online.target

[Service]
ExecStart=/bin/sh -c 'exec /usr/bin/avahi-publish -a -R ${mdns}.local "\$(ip -4 -json addr show scope global | jq -r "[.[].addr_info[].local] | first")"'
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF
    systemctl --user daemon-reload
    if systemctl --user enable --now llm-mdns.service 2>/dev/null; then
        log_info "publishing ${mdns}.local"
    else
        log_warn "could not start mDNS publishing; use the IP address instead"
    fi
else
    log_warn "avahi-publish not found; use the IP address instead of ${mdns}.local"
fi

echo
log_step "Usage examples"
api_key="$(yq -r '.server.api_key' "$CONFIG_PATH")"
first_alias="$(yq -r '[.models[] | select(.enabled)] | .[0].alias' "$CONFIG_PATH")"
cat <<EOF
  From this machine:
    curl http://127.0.0.1:${port}/v1/chat/completions \\
      -H "Authorization: Bearer ${api_key}" \\
      -H "Content-Type: application/json" \\
      -d '{"model":"${first_alias}","messages":[{"role":"user","content":"hello"}]}'

  From another machine on the LAN:
    curl http://${mdns}.local:${port}/v1/chat/completions \\
      -H "Authorization: Bearer ${api_key}" \\
      -H "Content-Type: application/json" \\
      -d '{"model":"${first_alias}","messages":[{"role":"user","content":"hello"}]}'

  OpenAI-compatible client settings:
    base_url = http://${mdns}.local:${port}/v1
    api_key  = ${api_key}
    model    = ${first_alias}
EOF

echo
log_info "Setup complete. Next: make benchmark, then make start"
