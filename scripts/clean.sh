#!/usr/bin/env bash
# clean.sh — remove the compose stack, unit, config, and images. Keeps downloaded models.
set -euo pipefail
# shellcheck disable=SC1091 # Resolved from this script at runtime.
# shellcheck source=../tools/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/../tools/lib.sh"

require_cmd yq podman systemctl

configured_image=""
if [ -f "$CONFIG_PATH" ]; then
    configured_image="$(yq -r '.gpu.image // ""' "$CONFIG_PATH")" \
        || die "could not read gpu.image from ${CONFIG_PATH}; config may be corrupt"
fi
if [ -n "$configured_image" ] && [ "$configured_image" != null ]; then
    images_to_remove="$configured_image"
else
    configured_image=""
    images_to_remove="${VULKAN_IMAGE} and ${CPU_IMAGE} (default; no configured gpu.image found)"
fi

configured_omniroute_image=""
if [ -f "$CONFIG_PATH" ]; then
    configured_omniroute_image="$(yq -r '.omniroute.image // ""' "$CONFIG_PATH")" \
        || die "could not read omniroute.image from ${CONFIG_PATH}; config may be corrupt"
fi
if [ -n "$configured_omniroute_image" ] && [ "$configured_omniroute_image" != null ]; then
    images_to_remove="${images_to_remove}, ${configured_omniroute_image}"
else
    configured_omniroute_image=""
    images_to_remove="${images_to_remove}, and the configured omniroute.image if present"
fi

configured_remote_setup_image=""
if [ -f "$CONFIG_PATH" ]; then
    configured_remote_setup_image="$(yq -r '.remote_setup.image // ""' "$CONFIG_PATH")" \
        || die "could not read remote_setup.image from ${CONFIG_PATH}; config may be corrupt"
fi
if [ -n "$configured_remote_setup_image" ] && [ "$configured_remote_setup_image" != null ]; then
    images_to_remove="${images_to_remove}, ${configured_remote_setup_image}"
else
    configured_remote_setup_image=""
    images_to_remove="${images_to_remove}, and the configured remote_setup.image if present"
fi

configured_system_one_image=""
if [ -f "$CONFIG_PATH" ]; then
    configured_system_one_image="$(yq -r '.system_one.image // ""' "$CONFIG_PATH")" \
        || die "could not read system_one.image from ${CONFIG_PATH}; config may be corrupt"
fi
if [ -n "$configured_system_one_image" ] && [ "$configured_system_one_image" != null ]; then
    images_to_remove="${images_to_remove}, ${configured_system_one_image}"
else
    configured_system_one_image=""
    images_to_remove="${images_to_remove}, and the configured system_one.image if present"
fi

echo "This removes:"
echo "  compose stack   ${COMPOSE_FILE}"
echo "  compose volumes (including omniroute-data and remote-setup-data; OmniRoute's stored connections/password and the cached remote-setup API key)"
echo "  Laya weights    llm-env-system-one-cache (~0.8 GB; removed by make clean and re-downloaded on the next make setup; unlike llama.cpp model files, which make clean keeps and make prune deletes)"
echo "  unit            ${WRAPPER_UNIT_PATH}"
echo "  config          ${CONFIG_PATH}"
echo "  local OmniRoute API key cache  $(dirname "$CONFIG_PATH")/omniroute-api-key.json"
echo "  images          ${images_to_remove}"
echo "Downloaded models in ${MODELS_DIR} are KEPT."
if [ "${LLM_ENV_ASSUME_YES:-0}" = "1" ]; then
    confirm=yes
else
    read -rp "Proceed? (yes/no) " confirm
fi
[ "$confirm" = "yes" ] || { echo "Aborted."; exit 1; }

if [ -f "$COMPOSE_FILE" ]; then
    podman compose -f "$COMPOSE_FILE" down -v 2>/dev/null || true
fi
systemctl --user stop "${UNIT_NAME}.service" 2>/dev/null || true
systemctl --user disable "${UNIT_NAME}.service" 2>/dev/null || true
rm -f "$WRAPPER_UNIT_PATH"
systemctl --user daemon-reload
rm -f "$CONFIG_PATH" "$COMPOSE_FILE" "${HOME}/.config/llm-env/presets.ini" \
    "$(dirname "$CONFIG_PATH")/omniroute-api-key.json" \
    "$(dirname "$CONFIG_PATH")/omniroute-api-key.json.lock"
if [ -n "$configured_image" ]; then
    podman rmi -f "$configured_image" 2>/dev/null || true
else
    podman rmi -f "$VULKAN_IMAGE" "$CPU_IMAGE" 2>/dev/null || true
fi
if [ -n "$configured_omniroute_image" ]; then
    podman rmi -f "$configured_omniroute_image" 2>/dev/null || true
fi
if [ -n "$configured_remote_setup_image" ]; then
    podman rmi -f "$configured_remote_setup_image" 2>/dev/null || true
fi
if [ -n "$configured_system_one_image" ] && [ "$configured_system_one_image" != null ]; then
    podman rmi -f "$configured_system_one_image" 2>/dev/null || true
fi
podman volume rm -f llm-env-system-one-cache 2>/dev/null || true
log_info "cleanup complete"
