#!/usr/bin/env bash
# model-capacity.sh -- write the capacity card of a local model from measured data, then show it.
#
# Usage: scripts/model-capacity.sh <alias> [--depth <bench-fit.sh json>] [--score <score.sh json>]
# The card lives in <config dir>/capacity/<alias>.json (see pylib/capacity.py). Sections not given are kept.
set -euo pipefail
# shellcheck disable=SC1091 # Resolved from this script at runtime.
# shellcheck source=../tools/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/../tools/lib.sh"

alias_name="${1-}"
[ -n "$alias_name" ] || die "usage: make model-capacity MODEL=<alias> [DEPTH=<bench json>] [SCORE=<score json>]"
shift
write_args=()
while [ $# -gt 0 ]; do
    case "$1" in
        --depth|--score) [ -n "${2-}" ] || die "$1 needs a file"; write_args+=("$1" "$2"); shift 2 ;;
        *) die "unknown argument: $1" ;;
    esac
done

require_cmd uv jq
[ -f "$CONFIG_PATH" ] || die "no config at ${CONFIG_PATH}; run 'make setup' first"

llmenv --config "$CONFIG_PATH" capacity write --alias "$alias_name" "${write_args[@]}" > /dev/null
card="$(llmenv --config "$CONFIG_PATH" capacity show --alias "$alias_name")"
jq . <<< "$card"
jq -r '.usable | "usable: \(.context_tokens) ctx / \(.max_output_tokens) out (\(.basis))"' <<< "$card"
