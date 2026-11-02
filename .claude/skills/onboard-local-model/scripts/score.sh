#!/usr/bin/env bash
# score.sh — turn effort-run results into a 0-100 score per effort level and overall.
# usage: score.sh RESULTS.jsonl [--time-budget-s 600] [--temp-limit-c 85] [--correctness-only]
# score = 70 * correctness + 15 * speed + 10 * thermal headroom + 5 * host-CPU headroom, per run, averaged.
#   correctness = pass/total of the task's tests
#   speed       = 1 - seconds/time_budget, floored at 0
#   thermal     = 1 - peak_temp/temp_limit, rescaled so 60 C scores 1 (floored at 0); a 0 reading means no sensor,
#                 so the term is dropped and the other weights are rescaled to 100
#   cpu         = 1 - avg_cpu_pct/100
# --correctness-only scores 100 * correctness. Use it for every head-to-head (compare-runs.sh): thermal and CPU follow
# the room and the host, not the model, and a cloud anchor never loads them.
# Runs with bounded_outcome boundary-failure or launch-failure (the bounded runner never ran the agent) are excluded and
# counted as invalid_runs too: they measure the harness, not the model.
# Runs with api_error=true (401, 429, endpoint down) are excluded and counted as invalid_runs: they measure the
# endpoint, not the model, and scoring them 0 would hide a credential problem behind a bad score.
# by_layer groups the runs by task layer (the layer of the generated challenge; rows without a `layer` field derive it from the task
# name, others are left out): mean score and solved/total runs per layer.
# Runs with leak_suspect=true (the agent may have read or changed what it should not) are excluded too and counted as
# leak_suspect_runs; a row that is also invalid counts as invalid_runs only.
# ladder_at maps each layer present to the lowest effort tier (off < minimal < low < medium < high < xhigh, unknown
# tiers last) at which a strict majority of the layer's distinct challenges (tasks, over all valid rows) were solved
# (pass == total) at that tier, or null; with one challenge per layer that is "solved at that tier". A layer present in
# any row but in no valid row (all its rows invalid or leak-suspect) is in ladder_at as null.
# ladder = the highest layer N such that ladder_at is not null for every layer from the lowest layer run (any row, valid
# or not: ladder_at's keys) through N; 0 when the lowest layer run has none or no layer ran (a layer not run ends the climb).
# challenge_set = {generators, tasks, efforts, timeouts}: the unique values over ALL rows (efforts in tier order, missing
# keys null); two result files are a head-to-head only when their challenge_sets are equal (compare-runs.sh).
# by_operator = [{op, solved: "x/y"}] over valid rows that carry operators, sorted by op.
# pass_rate = solved valid runs / valid runs (3 decimals; null with no valid run).
# tool_use = {layers: [3,4,5], calls, errors, error_rate} over valid runs of layer >= 3: calls/errors are the sums of the
# rows' tool_calls/tool_errors (0 with no such run, both null when any such run lacks tool_calls); error_rate =
# errors / calls to 3 decimals, null when calls is null or 0.
# Correctness dominates on purpose: a fast wrong answer must never outrank a slow right one.
set -euo pipefail

usage() { sed -n '2,/^set -/s/^# \{0,1\}//p' "${BASH_SOURCE[0]}"; }
case "${1:-}" in -h|--help) usage; exit 0 ;; "") usage >&2; exit 2 ;; esac
file="$1"; shift
budget=600; limit=85; only=false
while [ $# -gt 0 ]; do
    case "$1" in
    --time-budget-s) budget="$2"; shift 2 ;;
    --temp-limit-c) limit="$2"; shift 2 ;;
    --correctness-only) only=true; shift ;;
    *) echo "unknown flag: $1" >&2; exit 2 ;;
    esac
done

jq -s --argjson budget "$budget" --argjson limit "$limit" --argjson only "$only" '
    def clamp: if . < 0 then 0 elif . > 1 then 1 else . end;
    def solved: .pass == .total and .total > 0;
    def tier: . as $e | (["off", "minimal", "low", "medium", "high", "xhigh"] | index($e)) // 99;
    def invalid: (.api_error // false) or ((.bounded_outcome // "") | IN("boundary-failure", "launch-failure"));
    def leak: .leak_suspect // false;
    def lay: .layer // ([(.task // "") | capture("^l(?<n>[0-9])-").n | tonumber] | first);
    . as $all
    | (map(select(invalid)) | length) as $invalid
    | (map(select(leak and (invalid | not))) | length) as $leaks
    | map(select(invalid or leak | not))
    | map(. + {
        layer: lay,
        correct: (if .total > 0 then (.pass / .total) else 0 end),
        speed: (1 - .seconds / $budget | clamp),
        thermal: (if .gpu.peak_temp_c > 0 then ((($limit - .gpu.peak_temp_c) / ($limit - 60)) | clamp) else null end),
        cpu: (1 - .gpu.avg_cpu_pct / 100 | clamp)
    })
    | map(. + {score: (if $only then 100 * .correct
                       elif .thermal == null then (70 * .correct + 15 * .speed + 5 * .cpu) * 100 / 90
                       else 70 * .correct + 15 * .speed + 10 * .thermal + 5 * .cpu end)})
    | ([.[] | select(.layer != null)] | group_by(.layer)) as $layers
    | ($layers | map({key: (.[0].layer | tostring),
        value: (. as $rows | ($rows | map(.task) | unique | length) as $n
            | ([$rows[] | select(solved) | .effort] | unique | sort_by(tier)
                | map(. as $e | select(([$rows[] | select(solved and .effort == $e) | .task] | unique | length) * 2 > $n)) | .[0])
        )}) | from_entries | ([$all[] | lay | select(. != null)] | unique | map({key: tostring, value: null}) | from_entries) + .) as $at
    | ($at | keys | map(tonumber) | min) as $lo
    | {
        by_effort: (group_by(.effort) | map({effort: .[0].effort,
            score: (map(.score) | add / length | . * 10 | round / 10),
            solved: "\(map(select(.pass == .total and .total > 0)) | length)/\(length)",
            avg_seconds: (map(.seconds) | add / length | round),
            peak_temp_c: (map(.gpu.peak_temp_c) | max), peak_vram_mib: (map(.gpu.peak_vram_mib) | max)})),
        overall: (if length > 0 then (map(.score) | add / length | . * 10 | round / 10) else null end),
        by_layer: ($layers | map({layer: .[0].layer,
            score: (map(.score) | add / length | . * 10 | round / 10),
            solved: "\(map(select(solved)) | length)/\(length)"})),
        ladder: (if $lo == null then 0 else (last(range($lo; 10) | select(all(range($lo; . + 1); $at[tostring] != null))) // 0) end),
        ladder_at: $at,
        pass_rate: (if length > 0 then (map(select(solved)) | length) / length * 1000 | round / 1000 else null end),
        tool_use: ([.[] | select((.layer // 0) >= 3)] as $deep
            | (if any($deep[]; .tool_calls == null) then null else ($deep | map(.tool_calls) | add // 0) end) as $calls
            | (if $calls == null then null else ($deep | map(.tool_errors // 0) | add // 0) end) as $errors
            | {layers: [3, 4, 5], calls: $calls, errors: $errors,
               error_rate: (if $calls == null or $calls == 0 then null else $errors / $calls * 1000 | round / 1000 end)}),
        invalid_runs: $invalid,
        leak_suspect_runs: $leaks,
        challenge_set: {generators: ($all | map(.generator) | unique), tasks: ($all | map(.task) | unique),
            efforts: ($all | map(.effort) | unique | sort_by(tier)), timeouts: ($all | map(.timeout_s) | unique)},
        by_operator: ([.[] | select(.operators != null) | . as $r | $r.operators[] | {op: ., ok: ($r | solved)}]
            | group_by(.op) | map({op: .[0].op, solved: "\(map(select(.ok)) | length)/\(length)"})),
        runs: map({task, layer, effort, pass, total, seconds, score: (.score * 10 | round / 10)})
      }' "$file"
