#!/usr/bin/env bash
# resources.sh — show the effective per-service CPU/RAM policy, active overrides and live cgroup counters.
set -euo pipefail
# shellcheck disable=SC1091 # Resolved from this script at runtime.
# shellcheck source=../tools/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/../tools/lib.sh"

require_cmd uv jq column

[ -f "$CONFIG_PATH" ] || die "no config at ${CONFIG_PATH}; run 'make setup' first"

resources_json="$(mktemp)"
trap 'rm -f "$resources_json"' EXIT
if ! llmenv --config "$CONFIG_PATH" resources --live > "$resources_json"; then
    die "$(jq -r '.error // "llmenv resources failed"' "$resources_json")"
fi

# Disabled services (all-zero policy) are hidden; llm_server is the computed remainder.
jq -r '
  . as $r
  | ["service","mem_min_mib","mem_max_mib","cpus_min","cpus_max","source"],
    (["omniroute","remote_setup","system_one","phoenix"][]
      | . as $n | $r[$n] | select(.memory_max_mib > 0)
      | [$n, .memory_min_mib, .memory_max_mib, .cpus_min, .cpus_max,
         (if $r.overrides_applied[$n] then "override: " + ($r.overrides_applied[$n] | join(",")) else "default" end)]),
    ["llm_server", 0, $r.llm_server.memory_mib, 0, $r.llm_server.cpus, "computed remainder"]
  | @tsv' "$resources_json" | column -t -s $'\t'
jq -r '.summary[]?' "$resources_json"
echo
jq -r '.live_table[]?' "$resources_json" | column -t -s $'\t'
jq -r '.live_footer[]?' "$resources_json"
