#!/usr/bin/env bash
# setup.sh — interactive configurator.
set -euo pipefail
# shellcheck disable=SC1091 # Resolved from this script at runtime.
# shellcheck source=../tools/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/../tools/lib.sh"

ASSUME_YES="${LLM_ENV_ASSUME_YES:-0}"
NO_GPU="${LLM_ENV_NO_GPU:-0}"
SYSTEM_ONE="${LLM_ENV_SYSTEM_ONE:-}"
KFD_PATH="${LLM_ENV_KFD_PATH:-/dev/kfd}"

bash "${REPO_DIR}/setup/prerequisites.sh" --check || die "missing prerequisites; run 'make prerequisites'"

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
migrate_config_file || die "configuration migration failed"

# Seed the prompt's default from the config's own persisted state (set by a
# prior run of this script), not just LLM_ENV_NO_GPU -- otherwise a routine
# re-run of `make setup` on a host that already disabled llm-server silently
# re-answers "Y", flips llm_server.enabled back to true, and Step 2 then dies
# with "no VRAM-backed GPUs detected" on a GPU-less host, with the config
# already corrupted before that failure. An explicit LLM_ENV_NO_GPU=1 still
# wins over persisted state: the user explicitly asked for no-GPU this run.
gpu_default="Y"
# Direct read + string comparison, not `.llm_server.enabled // true` --
# yq/jq's `//` alternative operator treats a real `false` value as falsy
# too, so `.llm_server.enabled // "unset"` would collapse an explicit
# `false` back down to "unset" and this bug would still be live.
persisted_llm_server_enabled="$(yq -r '.llm_server.enabled' "$CONFIG_PATH" 2>/dev/null)"
[ "$persisted_llm_server_enabled" = "false" ] && gpu_default="n"
[ "$NO_GPU" = "1" ] && gpu_default="n"
gpu_answer="$(ask "Enable local GPU inference (llm-server)? [Y/n]: " "$gpu_default")"
case "$gpu_answer" in
    [nN]*) llm_server_enabled=false ;;
    *) llm_server_enabled=true ;;
esac
LLM_SERVER_ENABLED="$llm_server_enabled" yq -i \
    '.llm_server.enabled = (strenv(LLM_SERVER_ENABLED) == "true")' "$CONFIG_PATH"
log_info "llm-server (local GPU inference): ${llm_server_enabled}"

# Same persisted-default rule as the llm-server prompt above.
system_one_default="n"
[ "$(yq -r '.system_one.enabled' "$CONFIG_PATH" 2>/dev/null)" = "true" ] && system_one_default="y"
[ "$SYSTEM_ONE" = "1" ] && system_one_default="y"
[ "$SYSTEM_ONE" = "0" ] && system_one_default="n"
system_one_answer="$(ask "Enable local System One decisions (Laya on ROCm)? [y/N]: " "$system_one_default")"
case "$system_one_answer" in
    [yY]*) system_one_enabled=true ;;
    *) system_one_enabled=false ;;
esac
DRI_PATH="${LLM_ENV_DRI_PATH:-/dev/dri}"
if [ "$system_one_enabled" != "true" ]; then
    SYSTEM_ONE_ENABLED=false yq -i '.system_one.enabled = (strenv(SYSTEM_ONE_ENABLED) == "true")' "$CONFIG_PATH"
elif [ ! -r "$KFD_PATH" ]; then
    SYSTEM_ONE_ENABLED=false yq -i '.system_one.enabled = (strenv(SYSTEM_ONE_ENABLED) == "true")' "$CONFIG_PATH"
    die "System One requires an AMD GPU with ROCm (/dev/kfd); not found at ${KFD_PATH}"
fi
log_info "system-one (local Laya decisions): ${system_one_enabled}"

if [ "$llm_server_enabled" = "true" ] || [ "$system_one_enabled" = "true" ]; then

log_step "Step 2/8  Detecting GPUs"
facts="$(llmenv detect)"
gpu_count="$(echo "$facts" | jq '.gpus | length')"
[ "$gpu_count" -gt 0 ] || die "no VRAM-backed GPUs detected"
while IFS=$'\t' read -r gpu_index gpu_card gpu_pci gpu_total gpu_used gpu_render_node gpu_displays; do
    gpu_total_display="$(format_mib "$gpu_total")"
    gpu_used_display="$(format_mib "$gpu_used")"
    gpu_free_display="$(format_mib "$((gpu_total - gpu_used))")"
    gpu_displays_label="headless"
    [ -n "$gpu_displays" ] && gpu_displays_label="displays: ${gpu_displays}"
    printf '  %s) %s%s%s  %s  %s total  %s used  %s free  %s  %s\n' \
        "$gpu_index" "$GREEN" "$gpu_card" "$NC" "$gpu_pci" \
        "$gpu_total_display" "$gpu_used_display" "$gpu_free_display" \
        "$gpu_render_node" "$gpu_displays_label"
done < <(echo "$facts" | jq -r '
  .gpus | to_entries[] |
  [(.key + 1 | tostring), .value.card, .value.pci_address,
   (.value.vram_total_mib | tostring), (.value.vram_used_mib | tostring),
   .value.render_node, (.value.connected_outputs | join(","))] | @tsv')
default_gpu="$(echo "$facts" | jq -r '.gpus | to_entries | max_by(.value.vram_total_mib) | .key + 1')"
gpu_choice="$(ask "  GPU number [${default_gpu}]: " "$default_gpu")"
[[ "$gpu_choice" =~ ^[1-9][0-9]*$ ]] || die "GPU selection must be a positive integer"
[ "$gpu_choice" -le "$gpu_count" ] || die "GPU selection is out of range"
gpu="$(echo "$facts" | jq --argjson index "$gpu_choice" '.gpus[$index - 1]')"
pci="$(echo "$gpu" | jq -r '.pci_address')"
vram_total="$(echo "$gpu" | jq -r '.vram_total_mib')"
vram_used="$(echo "$gpu" | jq -r '.vram_used_mib')"
vram_free="$((vram_total - vram_used))"
ceiling_pct="$(yq -r '.gpu.vram_budget_ceiling_pct // 95' "$CONFIG_PATH")"
# The 20% fallback here is only a code-level safety net for a config that
# bypassed migrate_config (which backfills the real, 30% default) — see
# Global Constraints. The ceiling is a percentage of vram_total (not
# vram_free) so it is a stable hardware safety margin, independent of
# what else is using the GPU right now; live contention is handled
# separately by compute_budget()'s `reserve`.
ceiling_floor_pct="$(yq -r '.gpu.vram_budget_ceiling_floor_pct // 20' "$CONFIG_PATH")"
vram_budget_ceiling_mib="$(jq -n --argjson total "$vram_total" --argjson pct "$ceiling_pct" \
    --argjson floor_pct "$ceiling_floor_pct" \
    '[(($total * $pct / 100) | round), (($total * $floor_pct / 100) | round)] | max')"
log_info "selected ${pci} with $(format_mib "$vram_total") total, $(format_mib "$vram_used") used, $(format_mib "$vram_free") free"
# Step 2's vram_total and vram_budget_ceiling_mib. Step 6 rewrites the same values when llm-server is on. System-One-only (Step 6 skipped) keeps this write.
PCI_ADDRESS="$pci" VRAM_TOTAL_MIB="$vram_total" VRAM_BUDGET_CEILING_MIB="$vram_budget_ceiling_mib" yq -i '.gpu.pci_address = strenv(PCI_ADDRESS) | .gpu.vram_total_mib = (strenv(VRAM_TOTAL_MIB) | tonumber) | .gpu.vram_budget_ceiling_mib = (strenv(VRAM_BUDGET_CEILING_MIB) | tonumber)' "$CONFIG_PATH"
if [ "$system_one_enabled" = "true" ]; then
    gpu_vendor="$(llmenv detect | jq -r --arg p "$pci" '.gpus[] | select(.pci_address == $p) | .vendor // empty')"
    if [ -z "$gpu_vendor" ]; then
        gpu_vendor="$(cat "/sys/bus/pci/devices/${pci}/vendor" 2>/dev/null || echo missing)"
    fi
    if [ "$gpu_vendor" != "0x1002" ]; then
        SYSTEM_ONE_ENABLED=false yq -i '.system_one.enabled = (strenv(SYSTEM_ONE_ENABLED) == "true")' "$CONFIG_PATH"
        die "System One requires an AMD ROCm GPU at ${pci}; vendor is ${gpu_vendor}"
    fi
    system_one_render="$(llmenv detect | jq -r --arg p "$pci" '.gpus[] | select(.pci_address == $p) | .render_node')"
    if [ -z "$system_one_render" ] || [ ! -r "${DRI_PATH}/${system_one_render}" ]; then
        SYSTEM_ONE_ENABLED=false yq -i '.system_one.enabled = (strenv(SYSTEM_ONE_ENABLED) == "true")' "$CONFIG_PATH"
        die "System One: configured GPU ${pci} has no readable render node (${DRI_PATH}/...); re-run make setup"
    fi
    system_one_off() { SYSTEM_ONE_ENABLED=false yq -i '.system_one.enabled = (strenv(SYSTEM_ONE_ENABLED) == "true")' "$CONFIG_PATH"; die "$1"; }
    log_step "System One image and weights"
    system_one_image="$(yq -r '.system_one.image' "$CONFIG_PATH")"
    system_one_models="$(yq -r '.system_one.models' "$CONFIG_PATH")"
    system_one_arch="$(llmenv gfx-arch --render-node "$system_one_render")" || system_one_off "System One: cannot read gfx arch for ${system_one_render}"
    system_one_image_arch=""
    if podman image exists "$system_one_image"; then
        system_one_image_arch="$(podman image inspect --format '{{index .Labels "llm-env.gpu-arch"}}' "$system_one_image" 2>/dev/null || true)"
    fi
    # Missing or stale arch label: the image was built for a different GPU.
    if [ "$system_one_image_arch" != "$system_one_arch" ]; then
        bash "${REPO_DIR}/scripts/system-one-build.sh" || system_one_off "System One image build failed"
    fi
    log_step "Prefetching Laya weights (${system_one_models}) into llm-env-system-one-cache"
    # :z relabels the script bind for Fedora SELinux.
    podman run --rm \
        -e LAYA_REVISION=reviewed \
        -e "LAYA_MODELS=${system_one_models}" \
        -v llm-env-system-one-cache:/home/laya/.cache/huggingface \
        -v "${REPO_DIR}/scripts/system-one-cache-check.py:/opt/system-one-cache-check.py:ro,z" \
        --entrypoint python3 "$system_one_image" /opt/system-one-cache-check.py prefetch \
        || system_one_off "System One weight prefetch failed"
    SYSTEM_ONE_ENABLED=true yq -i '.system_one.enabled = (strenv(SYSTEM_ONE_ENABLED) == "true")' "$CONFIG_PATH"
fi
if [ "$llm_server_enabled" = "true" ]; then

log_step "Step 3/8  Selecting models"
if [ -f "$CONFIG_PATH" ]; then
    yq -i 'del(.models[] | select(.alias == "openhermes"))' "$CONFIG_PATH"
fi
if [ -f "$CONFIG_PATH" ] && [ -z "$(yq -r '.models[] | select(.alias == "ornith-35b") | .alias' "$CONFIG_PATH")" ]; then
    TEMPLATE_PATH="${REPO_DIR}/models.yml.example" yq -i \
      '.models += [load(strenv(TEMPLATE_PATH)).models[] | select(.alias == "ornith-35b")]' \
      "$CONFIG_PATH"
    log_info "added ornith-35b to the existing config from the shipped template"
fi
models="$(llmenv --config "$CONFIG_PATH" models list)"
model_count="$(echo "$models" | jq '.models | length')"
[ "$model_count" -gt 0 ] || die "no models are configured"
yq -r '.models | to_entries[] | "  \(.key + 1)) \(.value.label) — \(.value.parameters), \(.value.quantization), \(.value.size_bytes / 1000000000) GB"' "$CONFIG_PATH"
default_models="$(echo "$models" | jq -r '[.models | to_entries[] | select(.value.enabled) | (.key + 1 | tostring)] | first // "1"')"
model_choice="$(ask "  Model number [${default_models}]: " "$default_models")"
[[ "$model_choice" =~ ^[1-9][0-9]*$ ]] || die "model selection must be a single positive integer"
[ "$model_choice" -le "$model_count" ] || die "model selection is out of range"
aliases=("$(echo "$models" | jq -r --argjson index "$model_choice" '.models[$index - 1].alias')")
llmenv --config "$CONFIG_PATH" models select "${aliases[@]}" >/dev/null
log_info "selected ${aliases[*]}"

log_step "Step 4/8  Downloading models"
mkdir -p "$MODELS_DIR"
# scripts/prune.sh (make prune) refuses to delete a directory without this
# marker -- proof it's actually the models directory llm-env created, not
# an unrelated existing path an operator's LLM_ENV_MODELS_DIR happens to
# point at.
touch "${MODELS_DIR}/.llm-env-managed"
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

log_step "Step 6/8  Preparing Vulkan"
podman pull "$VULKAN_IMAGE" >/dev/null
vulkan_listing="podman run --rm --device /dev/dri ${VULKAN_IMAGE} --list-devices"
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
  VRAM_BUDGET_CEILING_MIB="$vram_budget_ceiling_mib" \
  yq -i '
    .gpu.pci_address = strenv(PCI_ADDRESS) |
    .gpu.vram_total_mib = (strenv(VRAM_TOTAL_MIB) | tonumber) |
    .gpu.device_name = strenv(DEVICE_NAME) |
    .gpu.vram_budget_ceiling_mib = (strenv(VRAM_BUDGET_CEILING_MIB) | tonumber)
  ' "$CONFIG_PATH"
chmod 600 "$CONFIG_PATH"
log_info "prepared ${device_name} for ${pci}"

log_step "Step 7/8  Checking the VRAM budget"
budget_json="$(mktemp)"
trap 'rm -f "$budget_json"' EXIT
if llmenv --config "$CONFIG_PATH" budget --models-dir "$MODELS_DIR" > "$budget_json"; then
    jq -r '"  available \(.available_mib) MiB, required \(.required_mib) MiB — fits"' "$budget_json"
else
    # cmd_budget can exit nonzero two different ways: an OPERATIONAL error
    # (e.g. gpu.pci_address unset, or the configured GPU not detected --
    # llmenv.py's `fail()` helper) whose payload is only `{"error": "..."}`
    # with no `.remedies`/`.models_max` keys at all, or a genuine budget
    # INFEASIBILITY (`.vram_feasible`/`.ram_feasible`/`.remedies` present).
    # Check for `.error` first: under `set -euo pipefail`, piping a bare
    # `{"error": ...}` payload into `jq -r '.remedies[] | ...'` makes jq
    # itself fail ("Cannot iterate over null"), which would crash this
    # script with jq's own cryptic message instead of the actual, much more
    # actionable error `cmd_budget` already produced.
    if jq -e '.error' "$budget_json" > /dev/null 2>&1; then
        die "$(jq -r '.error' "$budget_json")"
    fi
    if [ "$(jq -r 'if has("vram_feasible") then .vram_feasible else true end' "$budget_json")" = "false" ]; then
        jq -r '"  VRAM short by \(.shortfall_mib) MiB (available \(.available_mib) MiB, required \(.required_mib) MiB)"' "$budget_json"
    fi
    if [ "$(jq -r 'if has("ram_feasible") then .ram_feasible else true end' "$budget_json")" = "false" ]; then
        jq -r '"  RAM short by \(.ram_shortfall_mib) MiB (available \(.ram_available_mib) MiB, required \(.ram_required_mib) MiB)"' "$budget_json"
    fi
    jq -r '.remedies // [] | .[] | "    - \(.)"' "$budget_json"
    die "budget infeasible for models_max=$(jq -r '.models_max // "?"' "$budget_json"); adjust the config and retry"
fi
else
    log_step "Step 3/8  Selecting models"; log_info "(skipped -- llm-server disabled)"
    log_step "Step 4/8  Downloading models"; log_info "(skipped -- llm-server disabled)"
    log_step "Step 5/8  Validating model files"; log_info "(skipped -- llm-server disabled)"
    log_step "Step 6/8  Preparing Vulkan"; log_info "(skipped -- llm-server disabled)"
    log_step "Step 7/8  Checking the VRAM budget"; log_info "(skipped -- llm-server disabled)"
fi

else
    log_step "Step 2/8  Detecting GPUs"; log_info "(skipped -- llm-server disabled)"
    log_step "Step 3/8  Selecting models"; log_info "(skipped -- llm-server disabled)"
    log_step "Step 4/8  Downloading models"; log_info "(skipped -- llm-server disabled)"
    log_step "Step 5/8  Validating model files"; log_info "(skipped -- llm-server disabled)"
    log_step "Step 6/8  Preparing Vulkan"; log_info "(skipped -- llm-server disabled)"
    log_step "Step 7/8  Checking the VRAM budget"; log_info "(skipped -- llm-server disabled)"
fi

log_step "Step 8/8  Computing resource limits"
resources_json="$(mktemp)"
# Replaces the Step 7/7 trap above with one that cleans up both temp
# files — a second `trap … EXIT` overwrites rather than adds to the first.
# budget_json is only set when llm_server_enabled=true (Step 7 ran); default
# it to empty here so this doesn't fail under `set -u` when disabled.
trap 'rm -f "${budget_json:-}" "$resources_json"' EXIT
if llmenv --config "$CONFIG_PATH" resources > "$resources_json"; then
    omniroute_cpus_min="$(jq -r '.omniroute.cpus_min' "$resources_json")"
    omniroute_cpus_max="$(jq -r '.omniroute.cpus_max' "$resources_json")"
    omniroute_memory_min_mib="$(jq -r '.omniroute.memory_min_mib' "$resources_json")"
    omniroute_memory_max_mib="$(jq -r '.omniroute.memory_max_mib' "$resources_json")"
    remote_setup_cpus_min="$(jq -r '.remote_setup.cpus_min' "$resources_json")"
    remote_setup_cpus_max="$(jq -r '.remote_setup.cpus_max' "$resources_json")"
    remote_setup_memory_min_mib="$(jq -r '.remote_setup.memory_min_mib' "$resources_json")"
    remote_setup_memory_max_mib="$(jq -r '.remote_setup.memory_max_mib' "$resources_json")"
    OMNIROUTE_CPUS_MIN="$omniroute_cpus_min" OMNIROUTE_CPUS_MAX="$omniroute_cpus_max" \
      OMNIROUTE_MEMORY_MIN_MIB="$omniroute_memory_min_mib" OMNIROUTE_MEMORY_MAX_MIB="$omniroute_memory_max_mib" \
      REMOTE_SETUP_CPUS_MIN="$remote_setup_cpus_min" REMOTE_SETUP_CPUS_MAX="$remote_setup_cpus_max" \
      REMOTE_SETUP_MEMORY_MIN_MIB="$remote_setup_memory_min_mib" REMOTE_SETUP_MEMORY_MAX_MIB="$remote_setup_memory_max_mib" \
      yq -i '
        .resources.omniroute.cpus_min = (strenv(OMNIROUTE_CPUS_MIN) | tonumber) |
        .resources.omniroute.cpus_max = (strenv(OMNIROUTE_CPUS_MAX) | tonumber) |
        .resources.omniroute.memory_min_mib = (strenv(OMNIROUTE_MEMORY_MIN_MIB) | tonumber) |
        .resources.omniroute.memory_max_mib = (strenv(OMNIROUTE_MEMORY_MAX_MIB) | tonumber) |
        .resources.remote_setup.cpus_min = (strenv(REMOTE_SETUP_CPUS_MIN) | tonumber) |
        .resources.remote_setup.cpus_max = (strenv(REMOTE_SETUP_CPUS_MAX) | tonumber) |
        .resources.remote_setup.memory_min_mib = (strenv(REMOTE_SETUP_MEMORY_MIN_MIB) | tonumber) |
        .resources.remote_setup.memory_max_mib = (strenv(REMOTE_SETUP_MEMORY_MAX_MIB) | tonumber)
      ' "$CONFIG_PATH"
    log_info "reserved ${omniroute_cpus_min}-${omniroute_cpus_max} CPUs, ${omniroute_memory_min_mib}-${omniroute_memory_max_mib} MiB RAM for omniroute"
    if [ "$llm_server_enabled" = "true" ]; then
        cpus="$(jq -r '.llm_server.cpus' "$resources_json")"
        memory_mib="$(jq -r '.llm_server.memory_mib' "$resources_json")"
        CPUS="$cpus" MEMORY_MIB="$memory_mib" yq -i '
            .resources.llm_server.cpus = (strenv(CPUS) | tonumber) |
            .resources.llm_server.memory_mib = (strenv(MEMORY_MIB) | tonumber)
          ' "$CONFIG_PATH"
        log_info "reserved ${cpus} CPUs, ${memory_mib} MiB RAM for llm-server"
    fi
else
    # A host too small to reserve the fixed floors is exactly the host where
    # an uncapped container is most dangerous — render_compose() treats
    # cpus/memory_mib == 0 as "no explicit limit", so falling back to 0/0
    # here would silently disable the safety mechanism precisely when it
    # matters most. Fail loudly instead.
    die "$(jq -r '.error' "$resources_json")"
fi

echo
log_info "Setup complete. Next: make check-setup"
