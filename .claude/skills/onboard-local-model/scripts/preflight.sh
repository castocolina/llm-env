#!/usr/bin/env bash
# preflight.sh — read-only host snapshot (one JSON object): VRAM, RAM, who holds the GPU, thermals, and the budgets a
# candidate model must fit. While llm-server serves on the GPU the compositor renders on, `llmenv budget` counts the
# llama-server holders' VRAM as compositor reserve. `llmenv_budget` adds it back (at most `reserve_mib` minus
# `gpu.reserve_floor_mib`), recomputes the VRAM verdict and records `llama_server_added_back_mib`, so it gives what a
# fresh load would get. Changes nothing. `make gpu-status` is the human-readable view of the same GPU facts.
# usage: preflight.sh   (env: LLM_ENV_CONFIG, LLM_ENV_MODELS_DIR); exit 2 without a config or a needed tool
set -euo pipefail
case "${1:-}" in -h|--help) sed -n '2,/^set -/s/^# \{0,1\}//p' "${BASH_SOURCE[0]}"; exit 0 ;; esac

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source-path=SCRIPTDIR
# shellcheck source=../../../../tools/lib.sh
source "$here/../../../../tools/lib.sh"
for t in yq jq podman uv; do command -v "$t" >/dev/null || { echo "missing tool: $t" >&2; exit 2; }; done
cfg="$CONFIG_PATH"
[ -f "$cfg" ] || { echo "no config at $cfg; run make setup" >&2; exit 2; }

pci="$(yq -r '.gpu.pci_address // ""' "$cfg")"
[ -n "$pci" ] || { echo "gpu.pci_address is empty: make setup has not detected a GPU (a gateway-only setup skips it; make llama-enable MODEL=<alias>, then make setup, then make restart)" >&2; exit 2; }
gpu="$(llmenv detect | jq --arg p "$pci" '[.gpus[] | select(.pci_address == $p)] | first')"
[ "$gpu" != null ] || { echo "configured GPU $pci not detected" >&2; exit 2; }
total="$(jq .vram_total_mib <<<"$gpu")"; used="$(jq .vram_used_mib <<<"$gpu")"
holders="$(llmenv processes-on-render-node --render-node "$(jq -r .render_node <<<"$gpu")" | jq -c '[.processes[] | {pid, comm, vram_mib}]')"
# the repo's own verdict for the enabled models (stricter than the ceiling minus reserve); null when llmenv budget gives no
# verdict (nothing enabled, a missing file, an error). With the compositor on this GPU, the VRAM llama-server holds is counted
# as compositor reserve; add it back (at most reserve - floor) and recompute the VRAM verdict.
llama_mib="$(jq '[.[] | select(.comm == "llama-server") | .vram_mib] | add // 0' <<<"$holders")"
floor="$(yq -r '.gpu.reserve_floor_mib // 0' "$cfg")"
if b="$(llmenv_budget_json "$cfg" "$MODELS_DIR" 2>/dev/null)"; then
    repo_budget="$(jq -c --argjson l "$llama_mib" --argjson floor "$floor" '
        . as $b
        | (if .compositor_on_this_gpu == true and $l > 0 then ([$l, (.reserve_mib // 0) - $floor] | min | [., 0] | max) else 0 end) as $add
        | {feasible, vram_feasible, vram_available_mib: .available_mib, vram_required_mib: .required_mib, vram_shortfall_mib: .shortfall_mib,
           ram_available_mib, ram_feasible, ram_required_mib, remedies, llama_server_added_back_mib: $add}
        | if $add > 0 then
            .vram_available_mib += $add
            | .vram_shortfall_mib = ([$b.required_mib - .vram_available_mib, 0] | max)
            | .vram_feasible = (.vram_shortfall_mib == 0)
            | .feasible = (.vram_feasible and .ram_feasible)
            | if .feasible then .remedies = [] else . end
          else . end' <<<"$b")"
else
    repo_budget=null
fi

IFS=, read -r _ _ temp sclk busy _ <<<"$(bash "$here/gpu-sample.sh" once "$pci")"
containers="$(podman ps --format '{{.Names}}' 2>/dev/null || true)"
up() { grep -qx "$1" <<<"$containers" && echo true || echo false; }

ceiling="$(yq -r '.gpu.vram_budget_ceiling_mib // 0' "$cfg")"
[ "$ceiling" != 0 ] || ceiling="$total"   # 0 = no cap, as compute_budget (pylib/budget.py) and models.yml.example say
s1_enabled="$(yq -r '.system_one.enabled // false' "$cfg")"
s1_reserve=0; [ "$s1_enabled" = true ] && s1_reserve="$(yq -r '.system_one.vram_reserve_mib // 5120' "$cfg")"
mem_total="$(awk '/^MemTotal:/ {print int($2/1024)}' /proc/meminfo)"
mem_avail="$(awk '/^MemAvailable:/ {print int($2/1024)}' /proc/meminfo)"
swap_used="$(awk '/^SwapTotal:/ {t=$2} /^SwapFree:/ {f=$2} END {print int((t-f)/1024)}' /proc/meminfo)"
# the llm-server memory cap (resources.llm_server.memory_ceiling_pct, floors included) from llmenv, never a local formula; it is
# 0 while llm-server is disabled. Clamped to MemAvailable (a live measurement); null when llmenv answers neither way.
ram_budget=""; ram_source=""
ram_budget="$(jq -r '.ram_available_mib // empty' <<<"$repo_budget" 2>/dev/null || true)"
if [[ "$ram_budget" =~ ^[0-9]+$ ]]; then ram_source="llmenv budget"; else
    ram_budget="$( (llmenv --config "$cfg" resources 2>/dev/null || true) | jq -r '.llm_server.memory_mib // empty' 2>/dev/null || true)"
    if [[ "$ram_budget" =~ ^[0-9]+$ ]]; then ram_source="llmenv resources"; else ram_budget=""; fi
fi
[ -z "$ram_budget" ] || [ "$ram_budget" -le "$mem_avail" ] || ram_budget="$mem_avail"
disk_dir="$MODELS_DIR"; [ -d "$disk_dir" ] || disk_dir="$HOME"   # a fresh host has no models directory yet

jq -n --argjson gpu "$gpu" --argjson holders "$holders" --argjson repo_budget "$repo_budget" --arg pci "$pci" \
    --argjson total "$total" --argjson used "$used" --argjson ceiling "$ceiling" --argjson s1 "$s1_reserve" \
    --argjson temp "${temp:-null}" --argjson sclk "${sclk:-null}" --argjson busy "${busy:-null}" \
    --argjson mt "$mem_total" --argjson ma "$mem_avail" --argjson su "$swap_used" --argjson rb "${ram_budget:-null}" --arg rs "$ram_source" \
    --argjson llm_enabled "$(yq -o=json '.llm_server.enabled' "$cfg")" --argjson s1_enabled "$s1_enabled" \
    --argjson llm_up "$(up llm-server)" --argjson s1_up "$(up system-one)" --argjson omni_up "$(up omniroute)" \
    --argjson aliases "$(yq -o=json -I0 '[.models[] | select(.enabled) | .alias]' "$cfg")" \
    --arg backend "$(yq -r '.gpu.backend' "$cfg")" --arg image "$(yq -r '.gpu.image' "$cfg")" \
    --arg models_dir "$MODELS_DIR" --argjson disk "$(df -BM --output=avail "$disk_dir" | tail -1 | tr -dc 0-9)" '
{
  gpu: {pci: $pci, render_node: $gpu.render_node, total_mib: $total, used_mib: $used, free_mib: ($total - $used),
        temp_c: $temp, sclk_mhz: $sclk, busy_pct: $busy, holders: $holders},
  budget: {vram_ceiling_mib: $ceiling, system_one_reserve_mib: $s1, model_vram_budget_mib: ($ceiling - $s1),
           ram_offload_budget_mib: $rb, ram_budget_source: (if $rs == "" then null else $rs end), llmenv_budget: $repo_budget},
  ram: {total_mib: $mt, available_mib: $ma, swap_used_mib: $su},
  services: {llm_server_enabled: $llm_enabled, llm_server_running: $llm_up, system_one_enabled: $s1_enabled,
             system_one_running: $s1_up, omniroute_running: $omni_up},
  runtime: {backend: $backend, image: $image, enabled_models: $aliases},
  disk: {models_dir: $models_dir, free_mib: $disk}
}'
