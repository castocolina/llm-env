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

echo "This removes:"
echo "  compose stack   ${COMPOSE_FILE}"
echo "  compose volumes (including omniroute-data; OmniRoute's stored connections/password)"
echo "  unit            ${WRAPPER_UNIT_PATH}"
echo "  config          ${CONFIG_PATH}"
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
rm -f "$CONFIG_PATH" "$COMPOSE_FILE" "${HOME}/.config/llm-env/presets.ini"
if [ -n "$configured_image" ]; then
    podman rmi -f "$configured_image" 2>/dev/null || true
else
    podman rmi -f "$VULKAN_IMAGE" "$CPU_IMAGE" 2>/dev/null || true
fi
if [ -n "$configured_omniroute_image" ]; then
    podman rmi -f "$configured_omniroute_image" 2>/dev/null || true
fi
log_info "cleanup complete"
