#!/usr/bin/env bash
# gpu-sample.sh — sample GPU VRAM/temperature/clock/busy and host CPU from sysfs; amdgpu only.
#   gpu-sample.sh start PCI CSV [INTERVAL_S]   append one CSV row per tick until SIGTERM (the caller backgrounds it)
#   gpu-sample.sh once PCI                     print one CSV row (epoch,vram_mib,temp_c,sclk_mhz,busy_pct,cpu_pct)
#   gpu-sample.sh summary CSV                  print one JSON object
# Missing counters are recorded as empty and ignored in the summary.
set -euo pipefail

usage() { sed -n '2,/^set -/s/^# \{0,1\}//p' "${BASH_SOURCE[0]}"; }
cpu_now() { local _ u n s i w q sq st; read -r _ u n s i w q sq st _ </proc/stat; echo "$((u + n + s + i + w + q + sq + st)) $((i + w))"; }

# row PCI PREV_TOTAL PREV_IDLE → prints the CSV row; sets CPU_TOTAL/CPU_IDLE for the next tick. Shell builtins only, so the
# sampler adds almost no load of its own to the numbers it reports.
row() {
    local dev="/sys/bus/pci/devices/$1" hw d v="" t="" busy="" sclk="" l ct ci cpu now
    for d in "$dev"/hwmon/hwmon*; do [ -d "$d" ] && hw="$d" && break; done
    [ -r "$dev/mem_info_vram_used" ] && { read -r v <"$dev/mem_info_vram_used"; v=$((v / 1048576)); }
    [ -r "${hw:-/none}/temp1_input" ] && { read -r t <"$hw/temp1_input"; t=$((t / 1000)); }
    [ -r "$dev/gpu_busy_percent" ] && read -r busy <"$dev/gpu_busy_percent"
    if [ -r "$dev/pp_dpm_sclk" ]; then
        while read -r l; do [[ $l == *'*' ]] && { sclk="${l#*: }"; sclk="${sclk%%[!0-9]*}"; break; }; done <"$dev/pp_dpm_sclk"
    fi
    read -r ct ci <<<"$(cpu_now)"
    cpu=$((100 * ((ct - $2) - (ci - $3)) / ((ct - $2) > 0 ? (ct - $2) : 1)))
    CPU_TOTAL=$ct; CPU_IDLE=$ci
    printf -v now '%(%s)T' -1
    echo "$now,$v,$t,$sclk,$busy,$cpu"
}

case "${1:-}" in
start)
    pci="${2:?pci}"; csv="${3:?csv}"; every="${4:-2}"
    echo "epoch,vram_mib,temp_c,sclk_mhz,busy_pct,cpu_pct" >"$csv"
    read -r CPU_TOTAL CPU_IDLE <<<"$(cpu_now)"
    # bash runs a trap only after its foreground child exits, so the sleep runs in the background and wait is interrupted
    sleep_pid=""
    trap 'kill "$sleep_pid" 2>/dev/null || true; exit 0' TERM INT
    while :; do
        row "$pci" "$CPU_TOTAL" "$CPU_IDLE" >>"$csv"
        sleep "$every" & sleep_pid=$!
        wait "$sleep_pid"
    done
    ;;
once)
    read -r t i <<<"$(cpu_now)"; sleep 0.2
    row "${2:?pci}" "$t" "$i"
    ;;
summary)
    awk -F, 'NR > 1 {
        n++; if (n == 1) t0 = $1; t1 = $1
        if ($2 != "") { if (!seen) { first = $2; seen = 1 } if ($2 > vmax) vmax = $2; vs += $2; vn++ }
        if ($3 != "" && $3 > tmax) tmax = $3
        if ($4 != "") { ss += $4; sn++; if ($4 > smax) smax = $4 }
        if ($5 != "") { bs += $5; bn++; if ($5 > bmax) bmax = $5 }
        cs += $6; if ($6 > cmax) cmax = $6
    } END {
        printf "{\"samples\":%d,\"seconds\":%d,\"first_vram_mib\":%d,\"peak_vram_mib\":%d,\"peak_temp_c\":%d,\"avg_sclk_mhz\":%d,\"avg_gpu_busy_pct\":%d,\"avg_cpu_pct\":%d,\"peak_sclk_mhz\":%d,\"peak_gpu_busy_pct\":%d,\"peak_cpu_pct\":%d,\"avg_vram_mib\":%d}\n",
            n, t1 - t0, first, vmax, tmax, (sn ? ss / sn : 0), (bn ? bs / bn : 0), (n ? cs / n : 0), smax, bmax, cmax, (vn ? vs / vn : 0)
    }' "${2:?csv}"
    ;;
-h|--help) usage ;;
*) usage >&2; exit 2 ;;
esac
