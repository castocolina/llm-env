#!/usr/bin/env bash
# status.sh — show the service unit's status.
set -euo pipefail
# shellcheck disable=SC1091 # Resolved from this script at runtime.
# shellcheck source=../tools/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/../tools/lib.sh"

systemctl --user status "${UNIT_NAME}.service" --no-pager || true
podman compose -f "$COMPOSE_FILE" ps || true
bash "$(dirname "${BASH_SOURCE[0]}")/phoenix.sh" status || true

# Print bounded health/resource metadata and aggregate Phoenix history only.
# Span bodies are stored in a private temporary file and never printed.
status_dir="$(mktemp -d "${TMPDIR:-/tmp}/llm-env-status.XXXXXX")"
chmod 700 "$status_dir"
trap 'rm -rf -- "$status_dir"' EXIT
status_containers=(llm-server omniroute remote-setup system-one llm-env-phoenix_phoenix_1)
for container in "${status_containers[@]}"; do
    if ! podman container exists "$container" >/dev/null 2>&1; then
        continue
    fi
    inspect_json="$status_dir/${container}.json"
    if ! podman inspect "$container" > "$inspect_json" 2>/dev/null; then
        printf 'Container %s: metadata UNKNOWN (inspect failed)\n' "$container"
        continue
    fi
    printf 'Container %s: ' "$container"
    jq -r '.[0] as $c |
      "health=" + ($c.State.Health.Status // "UNKNOWN") +
      " health_last_failure_exit=" + (([$c.State.Health.Log[]? | select(.ExitCode != 0) | .ExitCode] | last // "none")|tostring) +
      " health_last_failure_end=" + (([$c.State.Health.Log[]? | select(.ExitCode != 0) | .End] | last // "none")|tostring) +
      " id=" + ($c.Id[0:12] // "UNKNOWN") +
      " started=" + ($c.State.StartedAt // "UNKNOWN") +
      " memory_limit=" + (if ($c.HostConfig.Memory // 0) > 0 then (($c.HostConfig.Memory / 1048576|floor|tostring) + "MiB") else "UNKNOWN" end) +
      " soft_memory=" + (if ($c.HostConfig.MemoryReservation // 0) > 0 then (($c.HostConfig.MemoryReservation / 1048576|floor|tostring) + "MiB") else "UNKNOWN" end) +
      " cpu_weight_config=" + (($c.HostConfig.CpuShares // "UNKNOWN")|tostring) +
      " cpu_quota=" + (if ($c.HostConfig.NanoCpus // 0) > 0 then (($c.HostConfig.NanoCpus / 1000000000|tostring) + " CPUs (NanoCpus)") elif ($c.HostConfig.CpuQuota // 0) > 0 and ($c.HostConfig.CpuPeriod // 0) > 0 then ((($c.HostConfig.CpuQuota / $c.HostConfig.CpuPeriod)|tostring) + " CPUs (CpuQuota/CpuPeriod)") else "UNKNOWN" end)' "$inspect_json"
    cgroup_path="$(jq -r '.[0].State.CgroupPath // empty' "$inspect_json")"
    cgroup="/sys/fs/cgroup${cgroup_path}"
    for metric in memory.current memory.peak memory.events memory.low memory.min memory.high memory.max cpu.weight cpu.max; do
        if [[ -z "$cgroup_path" || "$cgroup_path" == / || ! -r "$cgroup/$metric" ]]; then
            printf '  cgroup %s=UNKNOWN\n' "$metric"
        else
            printf '  cgroup %s=%s\n' "$metric" "$(tr '\n' ',' < "$cgroup/$metric")"
        fi
    done
done
if command -v curl >/dev/null 2>&1; then
    if phi_url="$(bash "$(dirname "${BASH_SOURCE[0]}")/phoenix.sh" endpoint 2>/dev/null)"; then
        :
    else
        phi_url=""
    fi
    if [[ -z "$phi_url" ]]; then
        printf 'Phoenix readiness: NOT READY (invalid endpoint configuration)\n'
    elif curl --noproxy '*' --connect-timeout 2 --max-time 5 -fsS "$phi_url/readyz" >/dev/null 2>&1; then
        printf 'Phoenix readiness: READY (%s/readyz)\n' "$phi_url"
        spans_file="$status_dir/laya-spans.json"
        if curl --noproxy '*' --connect-timeout 2 --max-time 10 -fsS \
            "$phi_url/v1/projects/laya/spans?limit=1000&sort=id&order=asc" -o "$spans_file" 2>/dev/null \
            && jq -e '.data | type == "array"' "$spans_file" >/dev/null 2>&1; then
            jq -r '[.data[]] as $spans |
              "Phoenix project=laya records_in_page=" + ($spans|length|tostring) +
              " range=" + (if ($spans|length)==0 then "UNKNOWN" else (($spans|map(.start_time)|min) + ".." + ($spans|map(.end_time)|max)) end) +
              " ok=" + ([$spans[]|select(.status_code=="OK")]|length|tostring) +
              " error=" + ([$spans[]|select(.status_code=="ERROR")]|length|tostring) +
              " completeness=" + (if .next_cursor == null then "complete" else "incomplete (bounded first page)" end)' "$spans_file"
        else
            printf 'Phoenix project=laya history: UNKNOWN (API query failed or malformed)\n'
        fi
    else
        printf 'Phoenix readiness: NOT READY or unreachable\n'
    fi
fi

if [ -f "$CONFIG_PATH" ]; then
    bash "$(dirname "${BASH_SOURCE[0]}")/print-endpoints.sh"
else
    log_info "no config at ${CONFIG_PATH} yet; run 'make setup' first"
fi
