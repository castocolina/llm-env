#!/usr/bin/env bash
# tune-pick.sh — rank the verify-stage rows of a tune-sweep results.jsonl and pick the configuration to apply.
# usage: tune-pick.sh RESULTS [--min-tg 20]
# Prints {"ranked": [...], "pick": row|null, "rejected": [{"id", "reason"}]}. Only verify-stage rows are considered.
# R and F are the row's ref_depth and full_depth, written by tune-sweep.sh. Reject reasons, checked in this order: failed,
# no recorded depths (a results.jsonl older than tune-pick), not verified to full depth (no tg_tps at F), model VRAM over budget (model_vram_mib > vram_available_mib),
# tg at reference depth below N (--min-tg, default 20). Ranked rows carry ref_depth, tg_at_ref and pp_at_ref; order is
# ctx descending, then tg_at_ref, then pp_at_ref descending. pick is the first ranked row. Read-only.
set -euo pipefail

results=""; min_tg=20
while [ $# -gt 0 ]; do
    case "$1" in
    --min-tg) min_tg="${2:-}"; shift 2 ;;
    -h|--help) sed -n '2,/^set -/{/^set -/!p}' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*) echo "unknown flag: $1" >&2; exit 2 ;;
    *) results="$1"; shift ;;
    esac
done
[ -n "$results" ] || { echo "RESULTS (a results.jsonl path) is required" >&2; exit 2; }
[ -f "$results" ] || { echo "no such file: $results" >&2; exit 2; }
[[ "$min_tg" =~ ^[0-9]+([.][0-9]+)?$ ]] || { echo "--min-tg must be a number, got '$min_tg'" >&2; exit 2; }

jq -s --argjson min "$min_tg" '
    def at($d; $k): ((.tps // []) | map(select(.depth == $d)) | .[0] | .[$k]);
    def ref: .ref_depth;
    def reason:
        if .status != "ok" then "failed"
        elif .ref_depth == null or .full_depth == null then "no recorded depths (a results.jsonl older than tune-pick: re-run the sweep)"
        elif at(.full_depth; "tg_tps") == null then "not verified to full depth"
        elif .model_vram_mib == null or .vram_available_mib == null or .model_vram_mib > .vram_available_mib then "model VRAM over budget"
        elif (at(ref; "tg_tps") // 0) < $min then "tg at reference depth below \($min)"
        else null end;
    map(select(.stage == "verify")) | map(. + {why: reason}) as $v
    | ($v | map(select(.why == null) | del(.why) | . + {ref_depth: ref, tg_at_ref: at(ref; "tg_tps"), pp_at_ref: at(ref; "pp_tps")})
         | sort_by([-.ctx, -.tg_at_ref, -(.pp_at_ref // 0)])) as $ranked
    | {ranked: $ranked, pick: ($ranked[0] // null),
       rejected: ($v | map(select(.why != null) | {id, reason: .why}))}' "$results"
