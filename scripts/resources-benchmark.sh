#!/usr/bin/env bash
# resources-benchmark.sh — measure a container to re-derive its CPU/RAM policy (pylib/resource_benchmark.py).
#   SERVICE=omniroute (default): throwaway OmniRoute (same image/env, fake upstream) through cold start,
#       first requests, a load ramp and a cooldown; removed afterwards. Never touches the live container.
#   SERVICE=attach CONTAINERS="system-one llm-env-phoenix_phoenix_1" [RUN="make system-one-benchmark"]
#       [DURATION=60] [COOLDOWN=60] [PCI=0000:03:00.0 | RENDER=renderD128]: read-only sampling of running
#       containers while RUN (or just DURATION seconds) runs; VRAM too for system-one or a given GPU.
#   SERVICE=report FILE=tmp/resource-benchmark/<run>.json [HEADROOM=0.2]: recompute a saved run.
# Raw runs land in tmp/resource-benchmark/; apply the numbers per AGENTS.md "Re-deriving limits".
set -euo pipefail
# shellcheck disable=SC1091 # Resolved from this script at runtime.
# shellcheck source=../tools/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/../tools/lib.sh"

require_cmd uv
service="${SERVICE:-omniroute}"
args=()
case "$service" in
    omniroute)
        require_cmd podman
        [ -f "$CONFIG_PATH" ] || die "no config at ${CONFIG_PATH}; run 'make setup' first"
        ;;
    attach)
        require_cmd podman
        [ -n "${CONTAINERS:-}" ] || die "SERVICE=attach needs CONTAINERS=\"<name> [<name>...]\""
        read -r -a names <<<"$CONTAINERS"
        for name in "${names[@]}"; do args+=(--container "$name"); done
        [ -z "${RUN:-}" ] || args+=(--run "$RUN")
        args+=(--seconds "${DURATION:-60}" --cooldown "${COOLDOWN:-60}")
        [ -z "${PCI:-}" ] || args+=(--pci "$PCI")
        [ -z "${RENDER:-}" ] || args+=(--render "$RENDER")
        ;;
    report)
        [ -n "${FILE:-}" ] || die "SERVICE=report needs FILE=<saved run .json>"
        args+=(--file "$FILE" --headroom "${HEADROOM:-0.2}")
        ;;
    *) die "SERVICE must be omniroute, attach or report, got '${service}'" ;;
esac
llmenv --config "$CONFIG_PATH" resource-benchmark "$service" "${args[@]}"
