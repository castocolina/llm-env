#!/usr/bin/env bash
# omniroute-backup.sh — back up combos and provider-connection metadata together.
#
# Use before make clean / make prune. Credentials are NOT included (OmniRoute's
# providers API never returns them); re-auth via make codex-provision / the
# dashboard after restore. Combo restore remaps connectionIds by (provider, name).
#
# Writes both files under ~/.config/llm-env/combo-backups/ with a shared stamp:
#   combos-<stamp>.json
#   connections-<stamp>.json
set -euo pipefail
# shellcheck disable=SC1091 # Resolved from this script at runtime.
# shellcheck source=../tools/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/../tools/lib.sh"

backup_dir="${HOME}/.config/llm-env/combo-backups"
stamp="$(date +%Y%m%dT%H%M%S)"
mkdir -p "$backup_dir"

bash "${REPO_DIR}/scripts/omniroute-combo-backup.sh" \
    "${backup_dir}/combos-${stamp}.json"
bash "${REPO_DIR}/scripts/omniroute-connections-backup.sh" \
    "${backup_dir}/connections-${stamp}.json"
