#!/usr/bin/env bash
# clean.sh — remove the unit, config, and images. Keeps downloaded models.
set -euo pipefail
# shellcheck source=lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

echo "This removes:"
echo "  unit    ${QUADLET_DIR}/${UNIT_NAME}.container"
echo "  config  ${CONFIG_PATH}"
echo "  images  ghcr.io/ggml-org/llama.cpp:server-*"
echo "Downloaded models in ${MODELS_DIR} are KEPT."
if [ "${LLM_ENV_ASSUME_YES:-0}" = "1" ]; then
    confirm=yes
else
    read -rp "Proceed? (yes/no) " confirm
fi
[ "$confirm" = "yes" ] || { echo "Aborted."; exit 1; }

systemctl --user stop "${UNIT_NAME}.service" 2>/dev/null || true
systemctl --user disable "${UNIT_NAME}.service" 2>/dev/null || true
rm -f "${QUADLET_DIR}/${UNIT_NAME}.container"
systemctl --user daemon-reload
rm -f "$CONFIG_PATH" "${HOME}/.config/llm-env/presets.ini"
podman rmi -f ghcr.io/ggml-org/llama.cpp:server-vulkan \
                ghcr.io/ggml-org/llama.cpp:server-rocm \
                ghcr.io/ggml-org/llama.cpp:server 2>/dev/null || true
log_info "cleanup complete"
