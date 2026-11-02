#!/usr/bin/env bash
# latency-profile.sh — real requests to the RUNNING llm-server at increasing prompt sizes, with host peaks sampled every second.
# usage: latency-profile.sh --alias ALIAS [--out DIR] [--sizes 1024,8192,32768,131072,max] [--tokens 512]
#                           [--cap-run auto|off] [--max-seconds 3600] [--idle-seconds 3]
# Base URL and API key come from models.yml (server.port, server.api_key) as scripts/model-interference.sh does; the key
# travels only through curl's stdin config, never argv or output. Needs llm-server running; it only sends requests to it.
# Each run sends one unique-prefix prompt with cache_prompt off (cache_n must stay 0) and ignore_eos on, so exactly
# --tokens tokens are generated. "max" is ctx_size - tokens - 64 (slack for the chat template). --cap-run adds one run that
# generates client_max_output_tokens (the reply cap) after a prompt of ctx_size - cap - 64: the full window, as an agent would
# fill it. Per request it records prompt_n, input tok/s, time to first token (client clock, first content or reasoning
# chunk), predicted_n, output tok/s and wall time from the server's own timings/usage, plus 1 s peaks of: GPU VRAM, GPU busy,
# temperature, clock, host CPU, llm-server cgroup CPU cores and memory, llama-server process VRAM, host RAM and swap.
# A run is skipped when the estimate from earlier runs would pass --max-seconds. Exit 1 when any run failed.
# --out must resolve under <repo>/tmp/ (default tmp/latency-profile/<stamp>-<alias>); it gets latency-<alias>.json.
# Feed that JSON to `make model-capacity MODEL=<alias> PROFILE=<json>`, then `make model-report MODEL=<alias>`.
# Test seam: LATENCY_PROFILE_CGROUP replaces the llm-server container cgroup directory.
set -euo pipefail

alias_name=""; out=""; sizes="1024,8192,32768,131072,max"; tokens=512; cap_run=auto; budget=3600; idle=3
while [ $# -gt 0 ]; do
    case "$1" in
    --alias|--out|--sizes|--tokens|--cap-run|--max-seconds|--idle-seconds)
        [ $# -ge 2 ] || { echo "$1 needs a value" >&2; exit 2; }
        case "$1" in --alias) alias_name="$2" ;; --out) out="$2" ;; --sizes) sizes="$2" ;; --tokens) tokens="$2" ;;
            --cap-run) cap_run="$2" ;; --max-seconds) budget="$2" ;; *) idle="$2" ;; esac
        shift 2 ;;
    -h|--help) sed -n '2,/^set -/{/^set -/!p}' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown flag: $1" >&2; exit 2 ;;
    esac
done
[ -n "$alias_name" ] || { echo "--alias is required" >&2; exit 2; }
[[ "$tokens" =~ ^[1-9][0-9]*$ && "$budget" =~ ^[1-9][0-9]*$ && "$idle" =~ ^[1-9][0-9]*$ ]] \
    || { echo "--tokens, --max-seconds and --idle-seconds must be positive integers" >&2; exit 2; }
[[ "$sizes" =~ ^([0-9]+|max)(,([0-9]+|max))*$ ]] || { echo "--sizes must be comma-separated token counts or max" >&2; exit 2; }
case "$cap_run" in auto|off) ;; *) echo "--cap-run must be auto or off" >&2; exit 2 ;; esac

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source-path=SCRIPTDIR
# shellcheck source=../../../../tools/lib.sh
source "$here/../../../../tools/lib.sh"
# shellcheck source-path=SCRIPTDIR
# shellcheck source=require-tmp-out.sh
source "$here/require-tmp-out.sh"
[ -n "$out" ] || out="$REPO_DIR/tmp/latency-profile/$(date +%Y%m%dT%H%M%S)-$alias_name"
require_tmp_out "$out"
require_cmd curl jq yq awk
[ -f "$CONFIG_PATH" ] || die "no config at ${CONFIG_PATH}; run 'make setup' first"

entry="$(A="$alias_name" yq -o=json '.models[] | select(.alias == strenv(A))' "$CONFIG_PATH")"
[ -n "$entry" ] || die "no models.yml entry '$alias_name'"
ctx="$(jq -r '.ctx_size // empty' <<<"$entry")"
[[ "$ctx" =~ ^[1-9][0-9]*$ ]] || die "ctx_size of $alias_name is not a positive integer"
cap="$(jq -r '.client_max_output_tokens // empty' <<<"$entry")"
file="$(jq -r '.file' <<<"$entry")"
base="http://127.0.0.1:$(yq -r '.server.port' "$CONFIG_PATH")"
key="$(yq -r '.server.api_key' "$CONFIG_PATH")"
pci="$(yq -r '.gpu.pci_address' "$CONFIG_PATH")"
slack=64

if [ -n "${LATENCY_PROFILE_CGROUP:-}" ]; then
    cg="$LATENCY_PROFILE_CGROUP"
else
    container_running llm-server || die "llm-server is not running; run 'make start-llama'"
    cg="/sys/fs/cgroup$(cut -d: -f3 "/proc/$(podman inspect --format '{{.State.Pid}}' llm-server)/cgroup")"
fi
[ -r "$cg/memory.current" ] || die "no readable cgroup at $cg"

out="$(mkdir -p "$out" && cd "$out" && pwd)"
work="$out/work"; mkdir -p "$work"
gpid=""; hpid=""
stop_samplers() {
    local p
    for p in "$gpid" "$hpid"; do [ -z "$p" ] || kill "$p" 2>/dev/null || true; done
    for p in "$gpid" "$hpid"; do [ -z "$p" ] || wait "$p" 2>/dev/null || true; done
    gpid=""; hpid=""
}
trap 'stop_samplers; [ ! -f "$work/curl.pid" ] || kill "$(<"$work/curl.pid")" 2>/dev/null; rm -rf "$work"' EXIT
trap 'exit 130' INT TERM

api() { printf 'header = "Authorization: Bearer %s"\n' "$key" | curl -sS -K - -H 'Content-Type: application/json' "$@"; }
# The streaming call records curl's pid, so an interrupted run can stop it (the server then stops generating).
stream_api() { printf 'header = "Authorization: Bearer %s"\n' "$key" | bash -c 'echo $$ >"$1"; shift; exec curl "$@"' _ "$work/curl.pid" -sS -K - -H 'Content-Type: application/json' "$@"; }

# Sum of this container's processes' VRAM on the profiled GPU, MiB; one DRM client counts once (as pylib/detect.py does).
# shellcheck disable=SC2016 # awk program
vram_awk='function flush() { if (pd == pci && cid != "" && !(cid in seen)) { seen[cid] = 1; kib += v } pd = ""; cid = ""; v = 0 }
FNR == 1 { flush() }
/^drm-pdev:/ { pd = $2 }
/^drm-client-id:/ { cid = $2 }
/^drm-memory-vram:/ { v = ($3 == "MiB") ? $2 * 1024 : $2 }
END { flush(); print int(kib / 1024) }'

sample_host() { # CSV; runs in the background until TERM
    local nap="" k v ma="" st="" sf="" cpu cur anon p files pv
    printf 'epoch,mem_avail_kib,swap_used_kib,cg_cpu_usec,cg_mem_bytes,cg_anon_bytes,proc_vram_mib\n' >"$1"
    trap 'kill "$nap" 2>/dev/null; exit 0' TERM INT
    while :; do
        while read -r k v _; do case "$k" in MemAvailable:) ma=$v ;; SwapTotal:) st=$v ;; SwapFree:) sf=$v ;; *) ;; esac; done </proc/meminfo
        cpu=""; while read -r k v; do if [ "$k" = usage_usec ]; then cpu=$v; fi; done <"$cg/cpu.stat"
        read -r cur <"$cg/memory.current" || cur=""
        anon=""; while read -r k v; do if [ "$k" = anon ]; then anon=$v; break; fi; done <"$cg/memory.stat"
        files=(); for p in $(<"$cg/cgroup.procs"); do files+=("/proc/$p/fdinfo/"*); done
        pv=""; [ "${#files[@]}" -eq 0 ] || pv="$(awk -v pci="$pci" "$vram_awk" "${files[@]}" 2>/dev/null || true)" # no file = awk reads stdin
        printf '%s,%s,%s,%s,%s,%s,%s\n' "$EPOCHREALTIME" "$ma" "$((st - sf))" "$cpu" "$cur" "$anon" "$pv" >>"$1"
        sleep 1 & nap=$!; wait "$nap" || true
    done
}

# CSV -> one JSON object of peaks/averages; a metric with no reading is null, never 0.
host_summary() {
    awk -F, 'function j(x, f) { return (x == "" ? "null" : sprintf(f, x)) }
    NR > 1 && $1 != "" {
        n++; if ($2 != "") { a = $2 / 1024; if (am == "" || a < am) am = a }
        if ($3 != "") { if ($3 / 1024 > sw) sw = $3 / 1024; sn++ }
        if ($5 != "") { c = $5 / 1048576; if (c > cm) cm = c; cn++ }
        if ($6 != "") { a = $6 / 1048576; if (a > an) an = a; ann++ }
        if ($7 != "") { if ($7 + 0 > pm) pm = $7 + 0; ps += $7; pn++ }
        if ($4 != "" && pe != "" && $1 > pe) { r = ($4 - pu) / (($1 - pe) * 1e6); if (r > km) km = r; ks += $4 - pu; kt += $1 - pe; kn++ }
        if ($4 != "") { pu = $4; pe = $1 }
    } END {
        printf "{\"samples\":%d,\"mem_avail_min_mib\":%s,\"swap_used_max_mib\":%s,\"cg_mem_peak_mib\":%s,\"cg_anon_peak_mib\":%s,", n,
            j(am, "%d"), (sn ? sprintf("%d", sw) : "null"), (cn ? sprintf("%d", cm) : "null"), (ann ? sprintf("%d", an) : "null")
        printf "\"cg_cpu_peak_cores\":%s,\"cg_cpu_avg_cores\":%s,\"proc_vram_peak_mib\":%s,\"proc_vram_avg_mib\":%s}\n",
            (kn ? sprintf("%.2f", km) : "null"), (kn && kt > 0 ? sprintf("%.2f", ks / kt / 1e6) : "null"),
            (pn ? sprintf("%d", pm) : "null"), (pn ? sprintf("%d", ps / pn) : "null")
    }' "$1"
}

start_samplers() {
    local _
    rm -f "$work/gpu.csv" "$work/host.csv"
    bash "$here/gpu-sample.sh" start "$pci" "$work/gpu.csv" 1 >/dev/null 2>&1 & gpid=$!
    sample_host "$work/host.csv" >/dev/null 2>&1 & hpid=$!
    for _ in $(seq 1 200); do [ -s "$work/gpu.csv" ] && [ -s "$work/host.csv" ] && break; sleep 0.05; done # both headers exist before the request starts
}

# Both JSON halves of one sampled window, as {gpu, host}; GPU counters the card does not expose are null.
window_json() {
    local g busy=true
    [ -r "/sys/bus/pci/devices/$pci/gpu_busy_percent" ] || busy=false
    g="$(bash "$here/gpu-sample.sh" summary "$work/gpu.csv")"
    jq -cn --argjson g "$g" --argjson h "$(host_summary "$work/host.csv")" --argjson busy "$busy" '
        def nz: if . == 0 then null else . end;
        {gpu: {peak_vram_mib: ($g.peak_vram_mib | nz), avg_vram_mib: ($g.avg_vram_mib | nz), peak_busy_pct: (if $busy then $g.peak_gpu_busy_pct else null end),
               avg_busy_pct: (if $busy then $g.avg_gpu_busy_pct else null end), peak_temp_c: ($g.peak_temp_c | nz),
               peak_sclk_mhz: ($g.peak_sclk_mhz | nz), avg_sclk_mhz: ($g.avg_sclk_mhz | nz),
               peak_cpu_pct: $g.peak_cpu_pct, avg_cpu_pct: $g.avg_cpu_pct, samples: $g.samples}, host: $h}'
}

count_tokens() { # FILE -> token count of its text through the server's own tokenizer
    local n
    jq -n --arg m "$alias_name" --rawfile c "$1" '{model: $m, content: $c}' >"$work/tok.json"
    n="$(api --max-time 600 -X POST --data-binary "@$work/tok.json" "$base/tokenize" | jq -r '.tokens | length')"
    [[ "$n" =~ ^[1-9][0-9]*$ ]] || die "the server's /tokenize gave no token count"
    printf '%s\n' "$n"
}

ask='Ignore the records above. Write a long, detailed essay about the history of the printing press.'
bpt_milli=4000 # bytes per token x 1000, refined by every fit
make_prompt() { # TARGET_TOKENS TAG FILE: filler text sized through /tokenize to just under TARGET minus a template allowance
    local want=$(($1 - 32)) bytes n
    [ "$want" -ge 16 ] || want=16
    bytes=$((want * bpt_milli / 1000))
    for _ in 1 2 3 4 5 6; do
        { awk -v n="$bytes" -v tag="$2" 'BEGIN { print tag; len = length(tag) + 1
            while (len < n) { l = sprintf("Entry %d: the quick brown fox jumps over the lazy dog near the old river bank.", ++i)
                if (len + length(l) + 1 > n) l = substr(l, 1, n - len - 1)
                print l; len += length(l) + 1 } }'
          printf '\n%s' "$ask"; } >"$3"
        n="$(count_tokens "$3")"
        bpt_milli=$(($(stat -c %s "$3") * 1000 / n))
        if [ "$n" -le "$want" ] && [ "$n" -ge $((want - want / 200 - 8)) ]; then return 0; fi
        bytes=$((bytes * (want - want / 400) / n))
    done
    die "could not fit a prompt to $1 tokens (last count $n)"
}

runs="[]"; skipped="[]"; failed=0; min_pp=""; min_tg=""
request() { # LABEL KIND TARGET_PROMPT_TOKENS MAX_TOKENS ; writes the run object to $work/run.json; TARGET 0 = a tiny prompt
    local label="$1" kind="$2" target="$3" max="$4" line first="" final="" err="" t0 t1 left win
    if [ "$target" -eq 0 ]; then printf 'Reply with the single word ok.' >"$work/prompt.txt"
    else make_prompt "$target" "[latency-profile $$-$label $EPOCHREALTIME]" "$work/prompt.txt"; fi
    jq -n --arg m "$alias_name" --rawfile p "$work/prompt.txt" --argjson max "$max" \
        '{model: $m, max_tokens: $max, stream: true, stream_options: {include_usage: true}, cache_prompt: false, ignore_eos: true,
          messages: [{role: "user", content: $p}]}' >"$work/body.json"
    left=$((budget - SECONDS)); [ "$left" -ge 30 ] || left=30
    start_samplers
    t0="$EPOCHREALTIME"
    while IFS= read -r line || [ -n "$line" ]; do # an error body has no trailing newline
        case "$line" in
        *'"timings"'*) final="${line#data: }" ;;
        *'"error"'*) err="$line" ;;
        *) ;;
        esac
        if [ -z "$first" ]; then
            case "$line" in *'"content":"'[!\"]*|*'"reasoning_content":"'[!\"]*) first="$EPOCHREALTIME" ;; *) ;; esac
        fi
    done < <(stream_api -N --max-time "$left" -X POST --data-binary "@$work/body.json" "$base/v1/chat/completions" 2>"$work/curl.err")
    t1="$EPOCHREALTIME"; rm -f "$work/curl.pid"
    stop_samplers
    win="$(window_json)"
    [ -n "$final" ] || [ -n "$err" ] || err="$(tr '\n' ' ' <"$work/curl.err" | cut -c1-300)"
    jq -cn --arg label "$label" --arg kind "$kind" --argjson target "$target" --argjson max "$max" --arg t0 "$t0" --arg t1 "$t1" \
        --arg first "$first" --arg final "$final" --arg err "$err" --argjson win "$win" '
        ($final | if . == "" then null else (try fromjson catch null) end) as $f
        | ($f.timings // {}) as $t
        | {label: $label, kind: $kind, target_prompt_tokens: (if $target == 0 then null else $target end), max_tokens: $max,
           prompt_n: $t.prompt_n, cache_n: $t.cache_n, prompt_tokens: $f.usage.prompt_tokens, prompt_ms: $t.prompt_ms,
           input_tps: $t.prompt_per_second,
           ttft_s: (if $first == "" then null else (($first | tonumber) - ($t0 | tonumber)) end),
           predicted_n: $t.predicted_n, completion_tokens: $f.usage.completion_tokens, output_tps: $t.predicted_per_second,
           generate_s: (if $t.predicted_ms then $t.predicted_ms / 1000 else null end),
           wall_s: (($t1 | tonumber) - ($t0 | tonumber)),
           error: (if $f == null then (if $err == "" then "no timings in the response" else $err end) else null end),
           gpu: $win.gpu, host: $win.host}' >"$work/run.json"
}

record() { # run object JSON: append, track the slowest speeds seen (the basis of the time estimate)
    runs="$(jq -c --argjson r "$1" '. + [$r]' <<<"$runs")"
    jq -e '.error != null' <<<"$1" >/dev/null && failed=1
    local pp tg; pp="$(jq -r '.input_tps // empty' <<<"$1")"; tg="$(jq -r '.output_tps // empty' <<<"$1")"
    if [ -n "$pp" ] && { [ -z "$min_pp" ] || awk -v a="$pp" -v b="$min_pp" 'BEGIN { exit !(a < b) }'; }; then min_pp="$pp"; fi
    if [ -n "$tg" ] && { [ -z "$min_tg" ] || awk -v a="$tg" -v b="$min_tg" 'BEGIN { exit !(a < b) }'; }; then min_tg="$tg"; fi
}
fits_budget() { # PROMPT_TOKENS OUT_TOKENS: true when no estimate exists yet or the estimate (slowest seen speeds x 0.7) fits
    [ -n "$min_pp" ] && [ -n "$min_tg" ] || return 0
    awk -v s="$SECONDS" -v b="$budget" -v p="$1" -v o="$2" -v pp="$min_pp" -v tg="$min_tg" 'BEGIN { exit !(s + p / (pp * 0.7) + o / (tg * 0.7) <= b) }'
}

SECONDS=0
n_ctx="$(api --max-time 10 "$base/props?model=$alias_name" 2>/dev/null | jq '.default_generation_settings.n_ctx // null' 2>/dev/null || echo null)"
mem_total_mib="$(awk '/^MemTotal:/ { print int($2 / 1024) }' /proc/meminfo)"

echo "[1/4 warm-up] loading $alias_name (ctx $ctx), one tiny request" >&2
request warmup warmup 0 8; warm="$(<"$work/run.json")"

echo "[2/4 idle] ${idle}s baseline with the model resident" >&2
start_samplers; sleep "$idle"; stop_samplers
idle_json="$(window_json)"

echo "[3/4 runs] sizes $sizes, $tokens output tokens each, cache off" >&2
IFS=, read -r -a size_list <<<"$sizes"
for s in "${size_list[@]}"; do
    [ "$s" != max ] || s=$((ctx - tokens - slack))
    label="p$s"
    if [ "$s" -le 0 ] || [ $((s + tokens)) -gt "$ctx" ]; then
        skipped="$(jq -c --arg l "$label" '. + [{label: $l, reason: "prompt plus output does not fit ctx_size"}]' <<<"$skipped")"; continue
    fi
    if ! fits_budget "$s" "$tokens"; then
        skipped="$(jq -c --arg l "$label" '. + [{label: $l, reason: "estimated to pass --max-seconds"}]' <<<"$skipped")"; continue
    fi
    echo "  $label: running (elapsed ${SECONDS}s)" >&2
    request "$label" size "$s" "$tokens"; record "$(<"$work/run.json")"
done
if [ "$cap_run" = auto ] && [[ "$cap" =~ ^[1-9][0-9]*$ ]] && [ $((cap + slack + 1024)) -lt "$ctx" ]; then
    s=$((ctx - cap - slack))
    if fits_budget "$s" "$cap"; then
        echo "  cap: running prompt $s + $cap output tokens (elapsed ${SECONDS}s)" >&2
        request "cap$cap" cap "$s" "$cap"; record "$(<"$work/run.json")"
    else
        skipped="$(jq -c --arg l "cap$cap" '. + [{label: $l, reason: "estimated to pass --max-seconds"}]' <<<"$skipped")"
    fi
fi

echo "[4/4 write] $out/latency-$alias_name.json" >&2
result="$out/latency-$alias_name.json"
jq -n --arg alias "$alias_name" --arg file "$file" --arg at "$(date -u +%Y-%m-%dT%H:%M:%S+00:00)" --argjson ctx "$ctx" \
    --argjson n_ctx "${n_ctx:-null}" --argjson cap "${cap:-null}" --argjson tokens "$tokens" --argjson slack "$slack" \
    --argjson mem_total "$mem_total_mib" --argjson warm "$warm" --argjson idle "$idle_json" --argjson runs "$runs" --argjson skipped "$skipped" \
    '{schema: 1, kind: "latency-profile", alias: $alias, file: $file, measured_at: $at, ctx_size: $ctx, n_ctx: $n_ctx,
      client_max_output_tokens: $cap, output_tokens: $tokens, slack_tokens: $slack, cache_prompt: false, ignore_eos: true,
      host_mem_total_mib: $mem_total, warmup: $warm, idle: $idle, runs: $runs, skipped: $skipped}' >"$result"

jq -r '(["run", "prompt_n", "ttft_s", "in_tok/s", "out_n", "out_tok/s", "wall_s", "vram_card", "vram_model", "gpu_busy", "temp_c", "cpu_host", "cpu_llm"] | @tsv),
    (.runs[] | [.label, .prompt_n, .ttft_s, .input_tps, .predicted_n, .output_tps, .wall_s, .gpu.peak_vram_mib, .host.proc_vram_peak_mib,
        .gpu.peak_busy_pct, .gpu.peak_temp_c, .gpu.peak_cpu_pct, .host.cg_cpu_peak_cores]
      | map(if . == null then "-" elif type == "number" then (if . == floor then tostring else ((. * 10 | round) / 10 | tostring) end) else . end) | @tsv)' "$result" \
    | { column -t -s "$(printf '\t')" 2>/dev/null || cat; }
jq -r '.runs[] | select(.error != null) | "FAILED \(.label): \(.error)"' "$result" >&2
jq -r '.skipped[] | "skipped \(.label): \(.reason)"' "$result" >&2
echo "wrote $result"
exit "$failed"
