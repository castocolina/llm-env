#!/usr/bin/env bash
# rank-candidates.sh — gate, order and score the candidates of a no-argument onboarding run (jq only, no network).
# usage: rank-candidates.sh --scout FILE [--scout FILE ...] [--evidence DIR] [--top N]
# Gates, from the scout rows (hf-scout.sh --json): fit is vram or offload, pick.bpw >= 3.5, ctx >= 131072 (checked in that order).
# Research-card gates: tool_template, arch_supported, no_red_flags (false excludes, null is listed under unverified).
# Without --evidence: merge the scout files (a repeated id keeps its first row), gate, sort by fit (vram before offload), ctx desc,
#   updated desc, downloads desc; print {shortlist: [{rank, id, fit, ctx, params_b, pick, updated, downloads}], gates_failed: [{id, gate}]}
#   with at most N shortlist entries (default 6; gates_failed keeps input order).
# With --evidence DIR: read DIR/*.json cards {id, gates: {tool_template, arch_supported, no_red_flags}, levels: {agentic_evidence, speed,
#   context, lab, recency}, jev: {<level>: {level, needs_fallback}}, sources, notes}; print {ranked: [{id, score, levels, unverified}],
#   excluded: [{id, reason}] (a card whose scout row failed a gate: "scout gate: <gate>"; a false card gate: "gate false: <gates>"), disagreements: [{id, criterion, level, jev}]}. score = agentic_evidence x3 + speed x2 + context x2 + lab x1
#   + recency x1 (levels 0-3, max 27); ties go to the smaller pick.mib. jev answers never enter the score: a jev level that differs from
#   the card level by 2 or more, or needs_fallback true, is a disagreement (jev null then) for the orchestrator to re-read.
# Exit 2 on a bad flag, an unreadable scout file or card directory, malformed JSON, a malformed card (cards come from research subagents),
#   a level outside 0-3, a duplicate card id, or a card for an id absent from the scout files. Needs jq.
set -euo pipefail

scouts=(); evidence=""; top=6
while [ $# -gt 0 ]; do
    case "$1" in
    --scout) [ -r "${2:-}" ] || { echo "unreadable scout file: ${2:-}" >&2; exit 2; }; scouts+=("$2"); shift 2 ;;
    --evidence) [ -d "${2:-}" ] || { echo "not a directory: ${2:-}" >&2; exit 2; }; evidence="$2"; shift 2 ;;
    --top) case "${2:-}" in ""|*[!0-9]*|0) echo "--top needs a positive integer" >&2; exit 2 ;; esac; top="$2"; shift 2 ;;
    -h|--help) sed -n '2,/^set -/{/^set -/!p}' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown flag: $1" >&2; exit 2 ;;
    esac
done
[ "${#scouts[@]}" -gt 0 ] || { echo "need at least one --scout FILE" >&2; exit 2; }

gated="$(jq -s '
    def gate:
        if (.fit == "vram" or .fit == "offload") | not then "fit"
        elif ((.pick.bpw // 0) >= 3.5) | not then "bpw"
        elif ((.ctx // 0) >= 131072) | not then "ctx" else null end;
    if all(.[]; type == "array" and all(.[]; type == "object" and (.id | type) == "string")) | not
    then error("a scout file is not an array of rows with a string id") else . end
    | [.[][]] | reduce .[] as $r ([]; if any(.[]; .id == $r.id) then . else . + [$r] end) as $rows
    | [$rows[] | {row: ., gate: gate}] as $g
    | {rows: $rows,
       failed: [$g[] | select(.gate) | {id: .row.id, gate}],
       ok: [$g[] | select(.gate | not) | .row]
           | sort_by([(if .fit == "vram" then 0 else 1 end), -.ctx, (.updated // "" | explode | map(-.)), -(.downloads // 0)])}
' "${scouts[@]}")" || { echo "rank-candidates.sh: cannot merge the scout files" >&2; exit 2; }

if [ -z "$evidence" ]; then
    jq --argjson top "$top" '{
        shortlist: [.ok[:$top] | to_entries[] | .key as $i | .value | {rank: ($i + 1), id, fit, ctx, params_b, pick, updated, downloads}],
        gates_failed: .failed}' <<<"$gated" || { echo "rank-candidates.sh: cannot build the shortlist" >&2; exit 1; }
    exit 0
fi

shopt -s nullglob
cards=("$evidence"/*.json)
for f in "${cards[@]}"; do jq empty "$f" 2>/dev/null || { echo "malformed JSON: $f" >&2; exit 2; }; done

# Cards are untrusted (research subagents): every check below exits 2 through the jq error.
jq -n --argjson scout "$gated" '
    ["agentic_evidence", "speed", "context", "lab", "recency"] as $keys
    | {agentic_evidence: 3, speed: 2, context: 2, lab: 1, recency: 1} as $w
    | ["tool_template", "arch_supported", "no_red_flags"] as $gnames
    | def check:
        . as $c
        | if type != "object" then error("a card is not a JSON object")
          elif (.id | type) != "string" then error("a card has no string id")
          elif ($scout.rows | map(.id) | index($c.id)) == null then error("card id absent from the scout files: \($c.id)")
          elif (.gates | type) != "object" or any($gnames[]; . as $n | ($c.gates | has($n) | not) or ($c.gates[$n] | type | IN("boolean", "null") | not))
          then error("card \($c.id): gates must be true, false or null for \($gnames | join(", "))")
          elif (.levels | type) != "object" or any($keys[]; . as $k | ($c.levels[$k] | type) != "number" or $c.levels[$k] != ($c.levels[$k] | floor) or $c.levels[$k] < 0 or $c.levels[$k] > 3)
          then error("card \($c.id): levels must be integers 0-3 for \($keys | join(", "))")
          elif has("jev") and ((.jev | type) != "object" or any(.jev | to_entries[]; (.key | IN($keys[]) | not) or (.value | type) != "object" or (.value.level | type | IN("number", "null") | not)))
          then error("card \($c.id): malformed jev block")
          else . end;
    [inputs | check] as $cards
    | if ($cards | map(.id) | length) != ($cards | map(.id) | unique | length) then error("duplicate card id") else . end
    | [$cards[] | . as $c | {c: $c, mib: ($scout.rows[] | select(.id == $c.id) | .pick.mib // 1e18), off: [$gnames[] | select($c.gates[.] == false)],
       sg: ([$scout.failed[] | select(.id == $c.id) | .gate] | first)}] as $cs
    | {ranked: [$cs[] | select(.off == [] and .sg == null) | . as $x
            | {id: $x.c.id, mib: $x.mib, score: ([$keys[] | $x.c.levels[.] * $w[.]] | add), levels: $x.c.levels,
               unverified: [$gnames[] | select($x.c.gates[.] == null)]}]
        | sort_by([-.score, .mib]) | map(del(.mib)),
       excluded: [$cs[] | select(.sg != null or .off != [])
            | {id: .c.id, reason: (if .sg != null then "scout gate: \(.sg)" else "gate false: \(.off | join(", "))" end)}],
       disagreements: [$cs[] | select(.off == [] and .sg == null) | .c as $c | $keys[] | . as $k
            | ($c.jev[$k] // null) as $j | select($j != null)
            | select($j.needs_fallback == true or ($j.level != null and (($j.level - $c.levels[$k]) | fabs) >= 2))
            | {id: $c.id, criterion: $k, level: $c.levels[$k], jev: (if $j.needs_fallback == true then null else $j.level end)}]}
' ${cards[@]+"${cards[@]}"} </dev/null || { echo "rank-candidates.sh: a card in $evidence is malformed (see the message above)" >&2; exit 2; }
