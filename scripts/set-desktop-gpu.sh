#!/usr/bin/env bash
# set-desktop-gpu.sh -- diagnostic/cleanup for the desktop compositor's
# default-GPU selection. This used to also *force* a GPU choice via a Mesa
# DRI_PRIME override in ~/.config/environment.d/, but that stacks a second,
# conflicting GPU-selection mechanism on top of Bazzite's own `cardwire`
# daemon (which already owns default/discrete GPU selection and per-app
# launch routing, e.g. for Steam). Bazzite's own cardwire docs explicitly
# warn against combining other GPU management tools (switcherooctl,
# envycontrol, supergfxctl, ...) with it -- so the forcing actions were
# removed. Use `cardwire set <mode>` / `cardwire launch %command%` instead;
# see https://docs.bazzite.gg -> Advanced -> Cardwire.
#
# `reset` is kept so a machine that still has a leftover override file from
# before this change can clean it up.
set -euo pipefail
# shellcheck disable=SC1091 # Resolved from this script at runtime.
# shellcheck source=../tools/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/../tools/lib.sh"

require_cmd jq

usage() {
    echo "usage: $0 <reset|status>" >&2
    echo "  GPU default/discrete selection is now managed by Bazzite's 'cardwire'" >&2
    echo "  (see: cardwire --help, cardwire set <mode>, cardwire launch %command%)." >&2
    exit 1
}

action="${1:-}"
case "$action" in
    reset|status) ;;
    *) usage ;;
esac

env_dir="${HOME}/.config/environment.d"
dgpu_file="${env_dir}/61-llm-env-dgpu-default.conf"
igpu_file="${env_dir}/60-llm-env-igpu-default.conf"

facts="$(llmenv detect)" || die "could not detect GPUs"

if [ "$action" = "status" ]; then
    log_step "Detected GPUs"
    echo "$facts" | jq -r '.gpus[] | "  \(.pci_address)\t\(.render_node)\tVRAM \(.vram_total_mib) MiB"'
    compositor_node="$(echo "$facts" | jq -r '.compositor_render_node // "unknown"')"
    log_info "compositor is currently rendering on: ${compositor_node}"
    if [ -f "$dgpu_file" ] || [ -f "$igpu_file" ]; then
        log_warn "a leftover DRI_PRIME override file is present; run '$0 reset' to remove it -- GPU default/discrete selection is owned by cardwire now"
    else
        log_info "no leftover override present; GPU default/discrete selection is owned by cardwire (see: cardwire get)"
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
