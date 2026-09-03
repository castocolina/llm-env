#!/usr/bin/env bash
# set-desktop-gpu.sh -- controls which GPU the desktop compositor and new
# apps default to, via a Mesa DRI_PRIME override in
# ~/.config/environment.d/. Complements scripts/gpu-status.sh's iGPU-default
# migration (freeing dGPU VRAM for llama.cpp) with the opposite direction
# (forcing the dGPU back as the default) plus a status/reset path -- no GPU
# PCI address is ever hardcoded here, both candidates come from `llmenv
# detect` at runtime, optionally anchored to the configured gpu.pci_address
# when set.
set -euo pipefail
# shellcheck disable=SC1091 # Resolved from this script at runtime.
# shellcheck source=../tools/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/../tools/lib.sh"

require_cmd yq jq

usage() {
    echo "usage: $0 <dgpu|igpu|reset|status>" >&2
    exit 1
}

action="${1:-}"
case "$action" in
    dgpu|igpu|reset|status) ;;
    *) usage ;;
esac

env_dir="${HOME}/.config/environment.d"
dgpu_file="${env_dir}/61-llm-env-dgpu-default.conf"
igpu_file="${env_dir}/60-llm-env-igpu-default.conf"

facts="$(llmenv detect)" || die "could not detect GPUs"
gpu_count="$(echo "$facts" | jq '.gpus | length')"

configured_pci=""
if [ -f "$CONFIG_PATH" ]; then
    migrate_config_file || die "configuration migration failed"
    configured_pci="$(yq -r '.gpu.pci_address // ""' "$CONFIG_PATH")"
    [ "$configured_pci" = null ] && configured_pci=""
fi

if [ "$action" = "status" ]; then
    log_step "Detected GPUs"
    echo "$facts" | jq -r '.gpus[] | "  \(.pci_address)\t\(.render_node)\tVRAM \(.vram_total_mib) MiB"'
    compositor_node="$(echo "$facts" | jq -r '.compositor_render_node // "unknown"')"
    log_info "compositor is currently rendering on: ${compositor_node}"
    if [ -f "$dgpu_file" ]; then
        log_info "override active: dGPU forced default ($(cat "$dgpu_file"))"
    elif [ -f "$igpu_file" ]; then
        log_info "override active: iGPU forced default ($(cat "$igpu_file"))"
    else
        log_info "no override active; using firmware/driver default"
    fi
    exit 0
fi

if [ "$action" = "reset" ]; then
    removed=0
    for file in "$dgpu_file" "$igpu_file"; do
        if [ -f "$file" ]; then
            rm -f "$file"
            log_info "removed $(basename "$file")"
            removed=1
        fi
    done
    [ "$removed" -eq 1 ] || log_info "no override was active"
    log_info "log out and back in (or reboot) for the change to take effect"
    exit 0
fi

[ "$gpu_count" -ge 2 ] || die "only ${gpu_count} GPU detected; nothing to prioritize between"

if [ -n "$configured_pci" ]; then
    dgpu_pci="$configured_pci"
else
    dgpu_pci="$(echo "$facts" | jq -r '.gpus | sort_by(-.vram_total_mib) | first | .pci_address')"
fi
igpu_pci="$(echo "$facts" | jq --arg pci "$dgpu_pci" -r '[.gpus[] | select(.pci_address != $pci)] | sort_by(.vram_total_mib) | first | .pci_address')"
[ -n "$igpu_pci" ] && [ "$igpu_pci" != null ] || die "could not identify a second GPU to distinguish from ${dgpu_pci}"

mkdir -p "$env_dir"
case "$action" in
    dgpu)
        rm -f "$igpu_file"
        dri_prime="pci-$(echo "$dgpu_pci" | tr ':.' '__')"
        printf 'DRI_PRIME=%s\n' "$dri_prime" > "$dgpu_file"
        log_info "wrote $(basename "$dgpu_file") (${dgpu_pci})"
        ;;
    igpu)
        rm -f "$dgpu_file"
        dri_prime="pci-$(echo "$igpu_pci" | tr ':.' '__')"
        printf 'DRI_PRIME=%s\n' "$dri_prime" > "$igpu_file"
        log_info "wrote $(basename "$igpu_file") (${igpu_pci})"
        ;;
esac
log_info "best-effort: only affects apps that respect Mesa's DRI_PRIME convention, and only takes effect on your next login/session (not already-running processes, including the current compositor)"
