#!/usr/bin/env bash
# system-one-benchmark.sh — load profile for the local Laya server.
# Beyond LAYA_MAX_CONCURRENT requests in flight, Laya returns HTTP 503
# and Retry-After: 1 (no queue). The 2x phase reports that non-200 count.
set -euo pipefail
# shellcheck disable=SC1091 # Resolved from this script at runtime.
# shellcheck source=../tools/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/../tools/lib.sh"

require_cmd awk curl date jq podman seq tee xargs yq
[ -f "$CONFIG_PATH" ] || die "no config at ${CONFIG_PATH}; run 'make setup' first"
migrate_config_file || die "configuration migration failed"
[ "$(yq -r '.system_one.enabled' "$CONFIG_PATH")" = "true" ] || die "System One is disabled"
running="$(podman inspect --format '{{.State.Running}}' system-one 2>/dev/null || true)"
[ "$running" = "true" ] || die "System One is not running; run make start"

port="$(yq -r '.system_one.port' "$CONFIG_PATH")"
pci="$(yq -r '.gpu.pci_address' "$CONFIG_PATH")"
[ -n "$port" ] && [ "$port" != "null" ] || die "system_one.port is empty"
[ -n "$pci" ] && [ "$pci" != "null" ] || die "gpu.pci_address is empty"
render="$(llmenv detect | jq -r --arg p "$pci" '.gpus[] | select(.pci_address == $p) | .render_node')"
[ -n "$render" ] && [ "$render" != "null" ] || die "no render node for ${pci}"

# Same private diagnostic directory benchmark.sh uses. The timestamped table
# copy is retained with the rest of that directory (LLM_ENV_KEEP_CHECK_ARTIFACTS=1).
diagnostic_dir="$(prepare_diagnostic_dir benchmark)"
B=""
cleanup() {
    status=$?
    [ -n "$B" ] && rm -rf "$B"
    finish_diagnostic_dir "$diagnostic_dir"
    exit "$status"
}
trap cleanup EXIT
B="$(mktemp -d)"

cap="$(podman exec system-one printenv LAYA_MAX_CONCURRENT)"
[[ "$cap" =~ ^[1-9][0-9]*$ ]] || die "LAYA_MAX_CONCURRENT is not a positive integer: ${cap}"

URL="http://127.0.0.1:${port}/v1/systemone"
V="${LLM_ENV_DRM_ROOT:-/sys/class/drm}/${render}/device/mem_info_vram_used"
export URL B
cat > "$B/post.sh" << 'EOF'
#!/usr/bin/env bash
set -euo pipefail
n="$1"
curl -s -o /dev/null -w '%{http_code} %{time_total}\n' -X POST "$URL" \
    -H 'content-type: application/json' --data-binary "@${B}/b$((n % 5)).json"
EOF

i=0
for s in \
    "I was charged twice this month, please refund me" \
    "The app crashes every time I open the settings page" \
    "Me cobraron dos veces este mes, por favor reembolso" \
    "Our whole site is down and no customer can pay right now" \
    "refund pls"
do
    printf '{"model":"jev-1","state":{"ticket":"%s"},"questions":{"team":{"type":"choice","instructions":"Which team should handle this ticket?","criteria":{"billing":"charges, invoices, refunds","technical":"bugs, crashes, outages","other":"general questions"}},"urgent":{"type":"noul","instructions":"Is this blocking customers right now?"}}}' \
        "$s" > "$B/b${i}.json"
    i=$((i + 1))
done

cg() { podman exec system-one cat "/sys/fs/cgroup/$1"; }
usage() { cg cpu.stat | awk '/usage_usec/{print $2}'; }
mem() { echo $(( $(cg memory.current) / 1048576 )); }
vram() { echo $(( $(cat "$V") / 1048576 )); }

phase() { # name n concurrency count_non200
    local name="$1" n="$2" c="$3" count_non200="${4:-0}"
    local u0 t0 u1 t1 rows lat wall_ms p50 p95 reqs cpu non200
    u0="$(usage)"
    t0="$(date +%s%N)"
    rows="$(seq 1 "$n" | xargs -P "$c" -I{} bash "$B/post.sh" {})"
    t1="$(date +%s%N)"
    u1="$(usage)"
    wall_ms=$(( (t1 - t0) / 1000000 ))
    [ "$wall_ms" -ge 1 ] || wall_ms=1
    lat="$(awk '{print $2}' <<<"$rows" | sort -n)"
    p50="$(awk -v n="$n" 'NR==int(n*0.5)+1{print $1*1000}' <<<"$lat")"
    p95="$(awk -v n="$n" 'NR==int(n*0.95)+1{print $1*1000}' <<<"$lat")"
    [ -n "$p50" ] || p50=0
    [ -n "$p95" ] || p95=0
    reqs="$(awk -v n="$n" -v w="$wall_ms" 'BEGIN{printf "%.0f", n*1000/w}')"
    cpu=$(( (u1 - u0) / (wall_ms * 10) ))
    if [ "$count_non200" = 1 ]; then
        non200="$(awk '$1 != 200 {c++} END {print c+0}' <<<"$rows")"
        printf '%-22s n=%-5s c=%-3s wall=%6sms  req/s=%6s  cpu=%4s%% of 1 core  p50=%5.1fms p95=%5.1fms  ram=%sMiB vram=%sMiB  non200=%s\n' \
            "$name" "$n" "$c" "$wall_ms" "$reqs" "$cpu" "$p50" "$p95" "$(mem)" "$(vram)" "$non200"
    else
        printf '%-22s n=%-5s c=%-3s wall=%6sms  req/s=%6s  cpu=%4s%% of 1 core  p50=%5.1fms p95=%5.1fms  ram=%sMiB vram=%sMiB\n' \
            "$name" "$n" "$c" "$wall_ms" "$reqs" "$cpu" "$p50" "$p95" "$(mem)" "$(vram)"
    fi
}

idle() { # name seconds
    local label="$1" seconds="$2" u0 u1
    u0="$(usage)"
    sleep "$seconds"
    u1="$(usage)"
    printf '%-22s %ss window          cpu=%4s%% of 1 core  ram=%sMiB vram=%sMiB\n' \
        "$label" "$seconds" "$(( (u1 - u0) / (seconds * 10000) ))" "$(mem)" "$(vram)"
}

stamp="$(date -Iseconds)"
copy="${diagnostic_dir}/system-one-benchmark-${stamp}.txt"
{
    printf 'limits: cpu.max=%s memory.max=%sMiB\n' "$(cg cpu.max)" "$(( $(cg memory.max) / 1048576 ))"
    idle "idle (before)" 20
    phase "10 sequential" 10 1
    phase "100 sequential" 100 1
    phase "1000 sequential" 1000 1
    phase "1000 concurrent x8" 1000 8
    phase "2000 concurrent x16" 2000 16
    phase "$((cap * 2)) concurrent 2x" "$((cap * 2))" "$((cap * 2))" 1
    idle "cooldown +0-20s" 20
    idle "cooldown +20-80s" 60
    printf 'memory.peak=%sMiB\n' "$(( $(cg memory.peak) / 1048576 ))"
} | tee -a "$copy"
