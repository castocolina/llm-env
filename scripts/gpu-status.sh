#!/usr/bin/env bash
# gpu-status.sh — live diagnostic of the configured dGPU's VRAM contention,
# with an optional, explicitly-confirmed migration of the worst offenders
# to the iGPU. Read-only unless the operator confirms a migration prompt.
set -euo pipefail
# shellcheck disable=SC1091 # Resolved from this script at runtime.
# shellcheck source=../tools/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/../tools/lib.sh"

require_cmd yq jq

[ -f "$CONFIG_PATH" ] || die "no config found at ${CONFIG_PATH}; run 'make setup' first"

# Matches the pattern every other user-facing entrypoint in this repo
# follows (scripts/check-setup.sh, scripts/key-reset.sh, scripts/start.sh,
# setup/enable-boot.sh, setup/render-unit.sh,
# setup/setup-local-llm-agents.sh, setup/setup.sh): migrate the config file
# on disk before reading any field from it with a raw `yq`. Without this, a
# config that predates a field (e.g. gpu.vram_budget_ceiling_mib) would
# read via `yq`'s own `// 0` default below as "uncapped", while the budget
# headroom line further down (computed by shelling out to `llmenv budget`,
# whose `load_config()` migrates in-memory by default) would reflect the
# real, migrated, non-zero ceiling -- two adjacent lines of the same
# diagnostic disagreeing about whether a cap exists.
migrate_config_file || die "configuration migration failed"

pci="$(yq -r '.gpu.pci_address // ""' "$CONFIG_PATH")"
[ -n "$pci" ] && [ "$pci" != null ] || die "gpu.pci_address is not set; run 'make setup' first"

facts="$(llmenv detect)" || die "could not detect GPUs"

gpu="$(echo "$facts" | jq --arg pci "$pci" '[.gpus[] | select(.pci_address == $pci)] | first')"
[ -n "$gpu" ] && [ "$gpu" != null ] || die "configured GPU ${pci} not detected"

render_node="$(echo "$gpu" | jq -r '.render_node')"
vram_total="$(echo "$gpu" | jq -r '.vram_total_mib')"
vram_used="$(echo "$gpu" | jq -r '.vram_used_mib')"

ceiling_mib="$(yq -r '.gpu.vram_budget_ceiling_mib // 0' "$CONFIG_PATH")"
if [ "$ceiling_mib" = "0" ]; then
    ceiling_display="uncapped"
else
    ceiling_display="${ceiling_mib} MiB"
fi

headroom_display="unavailable"
budget_json="$(llmenv --config "$CONFIG_PATH" budget --models-dir "$MODELS_DIR" 2>/dev/null)" || true
if [ -n "$budget_json" ]; then
    headroom_display="$(echo "$budget_json" | jq -r '"\(.available_mib) MiB"')"
fi

log_step "GPU ${pci} (${render_node})"
echo "  total VRAM:         ${vram_total} MiB"
echo "  used (system-wide): ${vram_used} MiB"
echo "  llm-env ceiling:    ${ceiling_display}"
echo "  budget headroom:    ${headroom_display}"

# Approximate, by-name best-effort exclusion of this codebase's own
# dGPU-consuming stack -- comm names are matched literally, so a
# legitimately-named user process (e.g. one also called `podman`) would be
# wrongly excluded too; this is an accepted limitation, not fixed here.
# Deliberately does NOT list "gpu-status.sh": a running bash script's own
# `comm` (as read from /proc/<pid>/comm) is always `bash`, never the
# script's filename, so no name-based entry could ever match this script's
# own process -- there is no self-exclusion entry to add here, because none
# would ever match.
exclude_names='["llama-server","conmon","podman"]'
top3="$(llmenv processes-on-render-node --render-node "$render_node" | jq --argjson exclude "$exclude_names" '
    [.processes[] | select(.comm as $c | ($exclude | any(. == $c)) | not)]
    | sort_by(-.vram_mib)
    | .[:3]
')"
count="$(echo "$top3" | jq 'length')"

if [ "$count" -eq 0 ]; then
    log_info "no other processes using the dGPU"
    exit 0
fi

log_step "Top VRAM users on this GPU"
echo "$top3" | jq -r '.[] | "  \(.pid)\t\(.comm)\t\(.vram_mib) MiB"'
