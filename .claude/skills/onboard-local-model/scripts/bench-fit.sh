#!/usr/bin/env bash
# bench-fit.sh — llama-bench one models.yml entry on the configured GPU in a throwaway container while sampling VRAM,
# temperature, clocks and CPU; writes <out>/bench-<alias>.json and prints it.
# usage: LLM_ENV_CONFIG=<models.yml> bench-fit.sh --alias ALIAS --out DIR [--depths 0,32768,131072] [--threads N] [--timeout SECONDS]
# For a candidate that is not enabled yet, point LLM_ENV_CONFIG at the scratch copy that holds its entry (`references/onboard-flow.md` step 4).
# Flags (gpu layers, n-cpu-moe, KV types, ubatch, flash attention) come from the rendered production presets, so numbers
# match production (flash attention follows the preset; --threads adds -t N). `make benchmark` renders the same presets
# but passes only gpu layers and n-cpu-moe, measures only enabled entries and has no depth sweep.
# The throwaway container runs under the production llm-server CPU/RAM limits (`llmenv resources`, llm_server; 0 = none).
# When System One is enabled and running, the script keeps it resident (one request before the sampler starts, then one
# every 60 s), since production has it warm and the GPU shares VRAM with it; `system_one_warm` in the output records it.
# Output keys: alias, file, n_cpu_moe, flags, limits, system_one_warm, gpu, tps.
# --out must resolve to a directory under <repo>/tmp/ (exit 2 otherwise): benchmark challenges run only in isolated temp directories there.
# Exit 2 when gpu.device_name is empty (a gateway-only make setup skips it). Exit 3 while the llm-server container holds
# the GPU (contended numbers are worthless). Only the throwaway container and the sampler it starts are touched, and both
# are removed on exit.
set -euo pipefail

alias=""; out=""; depths="0,32768,131072"; bound=3600; threads=""
while [ $# -gt 0 ]; do
    case "$1" in
    --alias) alias="$2"; shift 2 ;;
    --out) out="$2"; shift 2 ;;
    --depths) depths="$2"; shift 2 ;;
    --threads) threads="$2"; shift 2 ;;
    --timeout) bound="$2"; shift 2 ;;
    -h|--help) sed -n '2,/^set -/{/^set -/!p}' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown flag: $1" >&2; exit 2 ;;
    esac
done
[ -n "$alias" ] && [ -n "$out" ] || { echo "--alias and --out are required" >&2; exit 2; }

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source-path=SCRIPTDIR
# shellcheck source=../../../../tools/lib.sh
source "$here/../../../../tools/lib.sh"
# shellcheck source-path=SCRIPTDIR
# shellcheck source=require-tmp-out.sh
source "$here/require-tmp-out.sh"
require_tmp_out "$out"
if container_running llm-server; then
    echo "llm-server is running and holds the GPU; stop it first with make stop-llama (the skill asks the user)" >&2; exit 3
fi
file="$(A="$alias" yq -r '.models[] | select(.alias == strenv(A)) | .file' "$CONFIG_PATH")"
[ -n "$file" ] && [ -f "$MODELS_DIR/$file" ] || { echo "no models.yml entry '$alias' or file missing in $MODELS_DIR" >&2; exit 2; }
dev_name="$(yq -r '.gpu.device_name // ""' "$CONFIG_PATH")"
[ -n "$dev_name" ] || { echo "gpu.device_name is empty: make setup has not picked a Vulkan device (a gateway-only setup skips it; make llama-enable MODEL=<alias>, then make setup, then make restart)" >&2; exit 2; }
out="$(mkdir -p "$out" && cd "$out" && pwd)"
name="llm-env-scout-$$"
sampler_pid=""; warm_pid=""
trap '[ -z "$sampler_pid" ] || kill "$sampler_pid" 2>/dev/null || true; [ -z "$warm_pid" ] || kill "$warm_pid" 2>/dev/null || true; podman rm -f "$name" >/dev/null 2>&1 || true' EXIT
trap 'exit 130' INT TERM

# same device resolution as scripts/benchmark.sh
podman run --rm --device /dev/dri --entrypoint /app/llama-server "$VULKAN_IMAGE" --list-devices >"$out/devices.txt" 2>/dev/null
device="$(llmenv resolve-device --device-name "$dev_name" --listing-file "$out/devices.txt" | jq -r .device)"
render_presets_file "$device" "$out/presets.ini"
preset() { presets_value "$out/presets.ini" "$1" "$2"; }
layers="$(preset "$alias" n-gpu-layers)"; ctk="$(preset '*' cache-type-k)"; ctv="$(preset '*' cache-type-v)"
ubatch="$(preset '*' ubatch-size)"; fa=0; [ "$(preset '*' flash-attn)" != on ] || fa=1
args=(bench -m "/models/$file" --device "$device" --n-gpu-layers "$layers" -fa "$fa"
      -ctk "$ctk" -ctv "$ctv" -ub "$ubatch" -p 512 -n 128 -d "$depths" -r 2 -o json)
moe="$(preset "$alias" n-cpu-moe)"
[ -z "$moe" ] || args+=(--n-cpu-moe "$moe")
[ -z "$threads" ] || args+=(-t "$threads")
limits="$(llmenv --config "$CONFIG_PATH" resources | jq -c '.llm_server | {cpus: (if .cpus > 0 then .cpus else null end), memory_mib: (if .memory_mib > 0 then .memory_mib else null end)}')"
run_limits=()
cpus="$(jq -r '.cpus // empty' <<<"$limits")"; mem="$(jq -r '.memory_mib // empty' <<<"$limits")"
[ -z "$cpus" ] || run_limits+=(--cpus "$cpus")
[ -z "$mem" ] || run_limits+=(--memory "${mem}m")

# keep System One resident, as in production: one request now, then one a minute until the bench ends
system_one_warm=false
if [ "$(yq -r '.system_one.enabled // false' "$CONFIG_PATH")" = true ] && container_running system-one; then
    system_one_warm=true
    url="http://127.0.0.1:$(yq -r '.system_one.port' "$CONFIG_PATH")/v1/systemone"
    printf '{"model":"jev-1","state":{"ticket":"The app crashes every time I open the settings page"},"questions":{"team":{"type":"choice","instructions":"Which team should handle this ticket?","criteria":{"billing":"charges, invoices, refunds","technical":"bugs, crashes, outages","other":"general questions"}}}}' >"$out/laya-warm.json"
    warm() { curl -s -o /dev/null --max-time 10 -X POST "$url" -H 'content-type: application/json' --data-binary "@$out/laya-warm.json" || true; }
    warm
    (
        trap 'kill "$nap" 2>/dev/null; exit 0' TERM
        while :; do sleep 60 & nap=$!; wait "$nap"; warm; done
    ) &
    warm_pid=$!
fi

csv="$out/gpu-$alias.csv"
bash "$here/gpu-sample.sh" start "$(yq -r '.gpu.pci_address' "$CONFIG_PATH")" "$csv" 2 &
sampler_pid=$!
status=0
timeout --kill-after=15 "$bound" podman run --rm --name "$name" --device /dev/dri "${run_limits[@]}" \
    -v "$MODELS_DIR:/models:ro,z" --entrypoint /app/llama "$VULKAN_IMAGE" "${args[@]}" >"$out/llama-bench-$alias.json" 2>"$out/llama-bench-$alias.err" || status=$?
kill "$sampler_pid" 2>/dev/null || true; wait "$sampler_pid" 2>/dev/null || true; sampler_pid=""
[ -z "$warm_pid" ] || { kill "$warm_pid" 2>/dev/null || true; wait "$warm_pid" 2>/dev/null || true; warm_pid=""; }
[ "$status" -eq 0 ] || { echo "llama-bench failed (exit $status); see $out/llama-bench-$alias.err" >&2; exit "$status"; }

jq -n --arg alias "$alias" --arg file "$file" --arg moe "$moe" --arg layers "$layers" --arg ctk "$ctk" --arg ctv "$ctv" \
    --arg ubatch "$ubatch" --argjson fa "$fa" --arg threads "$threads" --argjson limits "$limits" --argjson warm "$system_one_warm" --argjson gpu "$(bash "$here/gpu-sample.sh" summary "$csv")" \
    --slurpfile bench "$out/llama-bench-$alias.json" '
    {alias: $alias, file: $file, n_cpu_moe: ($moe | if . == "" then null else tonumber end),
     flags: {n_gpu_layers: ($layers | tonumber), n_cpu_moe: ($moe | if . == "" then null else tonumber end), cache_type_k: $ctk,
             cache_type_v: $ctv, ubatch_size: ($ubatch | tonumber), flash_attn: ($fa == 1),
             threads: ($threads | if . == "" then null else tonumber end)},
     limits: $limits, system_one_warm: $warm,
     gpu: ($gpu + {model_vram_mib: ($gpu.peak_vram_mib - $gpu.first_vram_mib)}),
     tps: ($bench[0] | map({depth: .n_depth, pp: (if .n_prompt > 0 then .avg_ts else null end), tg: (if .n_gen > 0 then .avg_ts else null end)})
           | group_by(.depth) | map({depth: .[0].depth, pp_tps: (map(.pp) | map(select(. != null)) | first), tg_tps: (map(.tg) | map(select(. != null)) | first)}))}' \
    | tee "$out/bench-$alias.json"
