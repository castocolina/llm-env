#!/usr/bin/env bash
# dev-setup.sh — sync the Python dev environment (pytest, ruff, pyright, pytest-xdist) and install the git hooks.
set -euo pipefail
# shellcheck disable=SC1091 # Resolved from this script at runtime.
# shellcheck source=../tools/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/../tools/lib.sh"

if [ -n "$(git config core.hooksPath)" ]; then
  log_error "core.hooksPath is set: unsupported; unset it, then rerun make dev-setup"
  exit 1
fi
require_cmd uv

log_step "Syncing the Python dev environment"
uv sync
# Warm the llmenv.py script environment (pyyaml) in the uv cache: tools/test.sh runs offline.
uv run llmenv.py --help >/dev/null
log_info "dev environment ready (.venv, pytest, ruff, pyright, pytest-xdist)"

log_step "Linking the pre-push hook"
ln -sfn "$(readlink -f "$(git rev-parse --path-format=absolute --git-common-dir)/../tools/pre-push.sh")" \
  "$(git rev-parse --git-path hooks/pre-push)"
if command -v pre-commit >/dev/null; then
  pre-commit install --hook-type pre-commit
else
  log_warn "install pre-commit: uv tool install pre-commit"
fi
