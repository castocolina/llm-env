#!/usr/bin/env bash
# model-report.sh -- one page of plain text about a local model, from its capacity card.
#
# Usage: scripts/model-report.sh <alias>
# Prints the page and saves it as <config dir>/capacity/<alias>.report.md (mode 0600). Unknown values read "not measured".
set -euo pipefail
# shellcheck disable=SC1091 # Resolved from this script at runtime.
# shellcheck source=../tools/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/../tools/lib.sh"

alias_name="${1-}"
[ -n "$alias_name" ] || die "usage: make model-report MODEL=<alias>"
require_cmd uv
[ -f "$CONFIG_PATH" ] || die "no config at ${CONFIG_PATH}; run 'make setup' first"

llmenv --config "$CONFIG_PATH" capacity report --alias "$alias_name" --write
