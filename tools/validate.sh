#!/usr/bin/env bash
# validate.sh — shellcheck + ruff + pyright.
set -euo pipefail

shopt -s nullglob
# The skill tree is untracked, so it is absent on a clean checkout.
skill_scripts=(./.claude/skills/*/scripts/*.sh)
shellcheck -s bash ./tools/*.sh ./setup/*.sh ./setup/lib/*.sh ./scripts/*.sh "${skill_scripts[@]}"
uv run ruff check llmenv.py pylib tests
uv run pyright
echo "All checks passed."
