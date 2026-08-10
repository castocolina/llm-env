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

igpu_pci=""
igpu_candidate="$(echo "$facts" | jq --arg pci "$pci" '[.gpus[] | select(.pci_address != $pci)] | sort_by(.vram_total_mib) | first')"
if [ -n "$igpu_candidate" ] && [ "$igpu_candidate" != null ]; then
    igpu_pci="$(echo "$igpu_candidate" | jq -r '.pci_address')"
fi
dri_prime=""
[ -n "$igpu_pci" ] && dri_prime="pci-$(echo "$igpu_pci" | tr ':.' '__')"

confirm() {
    local prompt="$1" reply
    if [ "${LLM_ENV_ASSUME_YES:-0}" = "1" ]; then
        return 1
    fi
    read -rp "$prompt" reply || reply=""
    case "$reply" in
        y|Y|yes|YES) return 0 ;;
        *) return 1 ;;
    esac
}

find_desktop_file() {
    local exe_basename="$1" dir file exec_line exec_basename
    for dir in "${HOME}/.local/share/applications" "${LLM_ENV_SYSTEM_APPLICATIONS_DIR:-/usr/share/applications}"; do
        [ -d "$dir" ] || continue
        while IFS= read -r -d '' file; do
            exec_line="$(grep -m1 '^Exec=' "$file" 2>/dev/null | cut -d= -f2-)"
            [ -n "$exec_line" ] || continue
            # Strip a leading "env DRI_PRIME=<value> " prefix -- the exact
            # prefix apply_igpu_override() itself writes -- before taking
            # the match token, so this function can recognize its own
            # previously-written override (whose Exec= line starts with
            # "env", not the app's binary name) and not just an untouched
            # system file. `#` removes the shortest match from the front;
            # non-matching lines (a plain "Exec=firefox %u") pass through
            # unchanged.
            exec_line="${exec_line#env DRI_PRIME=* }"
            exec_basename="$(basename "${exec_line%% *}")"
            if [ "$exec_basename" = "$exe_basename" ]; then
                printf '%s\n' "$file"
                return 0
            fi
        done < <(find "$dir" -maxdepth 1 -name '*.desktop' -print0 2>/dev/null)
    done
    return 1
}

apply_igpu_override() {
    local desktop_file="$1" prime="$2" target_dir target_file tmp_file
    target_dir="${HOME}/.local/share/applications"
    mkdir -p "$target_dir" || return 1
    target_file="${target_dir}/$(basename "$desktop_file")"
    # Write to a temp file in the same directory, then rename over
    # target_file, rather than redirecting awk's output directly into
    # target_file. desktop_file and target_file are frequently the SAME
    # path (find_desktop_file() prefers an existing HOME override over the
    # system file), and `awk '...' "$desktop_file" > "$target_file"` would
    # have the shell truncate target_file to empty via `>` before awk ever
    # opens it to read -- awk would then read nothing and silently write an
    # empty file. Writing to an independent temp file first means awk
    # always reads the real, untouched input regardless of whether the
    # source and destination paths coincide; only a subsequent `mv`
    # replaces target_file, and only once awk has fully succeeded.
    tmp_file="$(mktemp "${target_dir}/.gpu-status-override.XXXXXX")" || return 1
    if ! awk -v prefix="env DRI_PRIME=${prime} " '
        /^Exec=env DRI_PRIME=[^ ]* / { sub(/^Exec=env DRI_PRIME=[^ ]* /, "Exec=" prefix); print; next }
        /^Exec=/ { sub(/^Exec=/, "Exec=" prefix); print; next }
        { print }
    ' "$desktop_file" > "$tmp_file"; then
        rm -f "$tmp_file"
        return 1
    fi
    chmod 644 "$tmp_file" || { rm -f "$tmp_file"; return 1; }
    mv -f "$tmp_file" "$target_file" || { rm -f "$tmp_file"; return 1; }
}

if [ -z "$igpu_pci" ]; then
    log_warn "no alternate GPU detected; skipping migration"
elif confirm "Move these ${count} processes to the iGPU? [y/N] "; then
    moved=0
    skipped=0
    while IFS=$'\t' read -r p_pid p_comm p_exe; do
        if ! desktop_file="$(find_desktop_file "$p_exe")"; then
            echo "  ${p_comm} (pid ${p_pid}): no launcher found, skipped"
            skipped=$((skipped + 1))
        elif apply_igpu_override "$desktop_file" "$dri_prime"; then
            echo "  ${p_comm} (pid ${p_pid}): overridden -> $(basename "$desktop_file")"
            moved=$((moved + 1))
        else
            echo "  ${p_comm} (pid ${p_pid}): override failed, skipped"
            skipped=$((skipped + 1))
        fi
    done < <(echo "$top3" | jq -r '.[] | "\(.pid)\t\(.comm)\t\(.exe)"')
    log_info "migration summary: ${moved} overridden, ${skipped} skipped"
fi
