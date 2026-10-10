#!/usr/bin/env bash
# validate.sh — shellcheck + ruff + pyright.
set -euo pipefail

shellcheck -s bash ./tools/*.sh ./setup/*.sh ./setup/lib/*.sh ./scripts/*.sh
uv run ruff check llmenv.py pylib tests
uv run pyright
echo "All checks passed."
