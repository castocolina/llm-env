#!/usr/bin/env bash
# dev-setup.sh — sync the Python dev environment (pytest, ruff).
set -euo pipefail
# shellcheck disable=SC1091 # Resolved from this script at runtime.
# shellcheck source=../tools/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/../tools/lib.sh"

require_cmd uv

log_step "Syncing the Python dev environment"
uv sync
log_info "dev environment ready (.venv, pytest, ruff)"
