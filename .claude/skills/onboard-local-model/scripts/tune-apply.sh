#!/usr/bin/env bash
# tune-apply.sh — write one verified tune-sweep row into the live models.yml, after a backup.
# usage: tune-apply.sh --alias A --results RESULTS --id ID   (env: LLM_ENV_CONFIG)
# Only an ok verify-stage row qualifies. Backs up the live config to <config>.pre-tune-<YYYYmmddTHHMMSS>.bak (mode 0600,
# refuses an existing name), validates the edited copy with `llmenv migrate-config` before the atomic rename, then sets runtime.cache_type_k, runtime.cache_type_v, runtime.ubatch_size, runtime.flash_attn
# and the model's ctx_size, plus n_gpu_layers and n_cpu_moe when the row sets them. Threads are not applied (no key exists).
# Prints the backup path, the row's vram_available_mib (the input of the quant headroom check) and the next commands.
# It does not stop, start or restart anything. Exit 2 on usage, 1 when the row, alias or config is unusable.
set -euo pipefail

alias=""; results=""; id=""
while [ $# -gt 0 ]; do
    case "$1" in
    --alias) alias="${2:-}"; shift 2 ;;
    --results) results="${2:-}"; shift 2 ;;
    --id) id="${2:-}"; shift 2 ;;
    -h|--help) sed -n '2,/^set -/{/^set -/!p}' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown flag: $1" >&2; exit 2 ;;
    esac
done
[ -n "$alias" ] && [ -n "$results" ] && [ -n "$id" ] || { echo "--alias, --results and --id are required" >&2; exit 2; }

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source-path=SCRIPTDIR
# shellcheck source=../../../../tools/lib.sh
source "$here/../../../../tools/lib.sh"
require_cmd uv jq yq
[ -f "$results" ] || { echo "no such file: $results" >&2; exit 1; }
[ -f "$CONFIG_PATH" ] || { echo "no config at $CONFIG_PATH" >&2; exit 1; }
row="$(jq -c --arg id "$id" 'select(.id == $id and .stage == "verify" and .status == "ok")' "$results" | head -n1)"
[ -n "$row" ] || { echo "no ok verify-stage row '$id' in $results (throughput and failed rows cannot be applied)" >&2; exit 1; }
[ "$(A="$alias" yq -r '[.models[] | select(.alias == strenv(A))] | length' "$CONFIG_PATH")" -gt 0 ] || { echo "no models.yml entry '$alias'" >&2; exit 1; }

A="$alias"; KV="$(jq -r .kv <<<"$row")"; UB="$(jq -r .ubatch <<<"$row")"; FA="$(jq -r .fa <<<"$row")"; CTX="$(jq -r .ctx <<<"$row")"
expr='.runtime.cache_type_k = strenv(KV) | .runtime.cache_type_v = strenv(KV) | .runtime.ubatch_size = env(UB) | .runtime.flash_attn = (strenv(FA) == "on")
    | (.models[] | select(.alias == strenv(A)) | .ctx_size) = env(CTX)'
NGL="$(jq -r '.ngl // empty' <<<"$row")"; MOE="$(jq -r '.moe // empty' <<<"$row")"
[ -z "$NGL" ] || expr="$expr | (.models[] | select(.alias == strenv(A)) | .n_gpu_layers) = env(NGL)"
[ -z "$MOE" ] || expr="$expr | (.models[] | select(.alias == strenv(A)) | .n_cpu_moe) = env(MOE)"
export A KV UB FA CTX NGL MOE
apply_row() { yq -i "$expr" "$1"; }
edit_config_atomically pre-tune apply_row   # prints `backup: <path>`, validates the result, renames atomically

echo "vram_available_mib: $(jq -r .vram_available_mib <<<"$row")"
echo "make stop-llama"
echo "make start-llama"
echo "make model-interference"
