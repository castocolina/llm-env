#!/usr/bin/env bash
# context-ladder.sh — print or run the context-fill ladder.
# usage: context-ladder.sh --out DIR [--dry-run] [options]
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec node "$here/context-ladder.mjs" "$@"
