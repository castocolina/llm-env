#!/usr/bin/env bash
# pyright-staged.sh — commit-stage pyright hook (spec R2, D7): pyright on the whole project when the index adds,
# modifies, deletes or renames a .py file, pyproject.toml or uv.lock; otherwise nothing. It reads the index itself
# because pre-commit never passes a staged deletion to a hook.
# shortcut: a staged pyproject.toml reruns pyright but not ruff at commit; the push-stage validate.sh reruns ruff over everything
set -euo pipefail
inputs='(\.py|^pyproject\.toml|^uv\.lock)$'
while IFS= read -r -d '' p; do
  if [[ $p =~ $inputs ]]; then
    exec uv run pyright
  fi
done < <(git diff --staged --name-only -z --no-renames)
exit 0
