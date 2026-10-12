#!/usr/bin/env bash
# system-one-build.sh — build the local ROCm Laya image (no upstream image exists).
set -euo pipefail
# shellcheck disable=SC1091 # Resolved from this script at runtime.
# shellcheck source=../tools/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/../tools/lib.sh"

require_cmd podman yq uv jq
[ -f "$CONFIG_PATH" ] || die "no config at ${CONFIG_PATH}; run 'make setup' first"
migrate_config_file || die "configuration migration failed"
image="$(yq -r '.system_one.image' "$CONFIG_PATH")"; [ -n "$image" ] && [ "$image" != null ] || die "system_one.image is empty; run 'make setup'"
pci="$(yq -r '.gpu.pci_address' "$CONFIG_PATH")"; [ -n "$pci" ] && [ "$pci" != null ] || die "system-one build: gpu.pci_address is empty; run 'make setup' (no GPU arch default)"
render="$(llmenv detect | jq -r --arg p "$pci" '.gpus[] | select(.pci_address == $p) | .render_node')"; [ -n "$render" ] && [ "$render" != null ] || die "system-one build: no render node for ${pci}; cannot determine GPU arch"
arch="$(llmenv gfx-arch --render-node "$render")" && [ -n "$arch" ] || die "system-one build: cannot read gfx arch for ${render} (KFD gfx_target_version); refusing to guess"
log_step "Building ${image} for ${arch} (large ROCm image; first build takes several minutes)"
podman build --label "llm-env.gpu-arch=${arch}" --build-arg "GPU_ARCH=${arch}" -t "$image" "${REPO_DIR}/docker/system-one"
log_info "built ${image}"
