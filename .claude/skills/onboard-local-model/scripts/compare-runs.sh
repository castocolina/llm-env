#!/usr/bin/env bash
# compare-runs.sh — compare several effort-run result files as a head-to-head on one generated challenge set.
# usage: compare-runs.sh [--correctness-only] RESULTS.jsonl RESULTS.jsonl [...]
# Runs score.sh on each file (--correctness-only is passed through; the skill passes it on every head-to-head) and prints
# {challenge_set, runs: [{file, models, ladder, ladder_at, overall, pass_rate, invalid_runs, leak_suspect_runs}],
#  delta_vs_first: [{file, ladder, overall, pass_rate}], warnings: [...]}; runs and delta_vs_first hold one object per
# file in input order (index 0 is the first file, its deltas are 0; a delta is null when either side is null).
# A file with invalid_runs or leak_suspect_runs above 0 gets a warning: re-run that lane on the same seeds before comparing.
# Exit 0 on a head-to-head; exit 3 with {"error": "not a head-to-head: ...", "challenge_sets": [...]} when the files'
# challenge_set (generators, tasks, efforts, timeouts) differ; exit 2 on fewer than two files, an unknown flag or an
# unreadable file.
set -euo pipefail

usage() { sed -n '2,/^set -/s/^# \{0,1\}//p' "${BASH_SOURCE[0]}"; }
case "${1:-}" in -h|--help) usage; exit 0 ;; "") usage >&2; exit 2 ;; esac
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
flags=(); files=()
for a in "$@"; do
    case "$a" in
    --correctness-only) flags+=("$a") ;;
    -*) echo "unknown flag: $a" >&2; exit 2 ;;
    *) [ -r "$a" ] || { echo "unreadable file: $a" >&2; exit 2; }; files+=("$a") ;;
    esac
done
[ "${#files[@]}" -ge 2 ] || { echo "need at least two result files" >&2; exit 2; }

scored=""
for f in "${files[@]}"; do
    one="$(bash "$here/score.sh" "$f" ${flags[@]+"${flags[@]}"})" || { echo "score.sh failed on $f" >&2; exit 1; }
    models="$(jq -s -c 'map(.model) | unique' "$f")" || { echo "unreadable rows in $f" >&2; exit 1; }
    scored+="$(jq -c --arg file "$f" --argjson models "$models" '. + {file: $file, models: $models}' <<<"$one")"$'\n'
done

out="$(jq -s '
    . as $r
    | if ($r | map(.challenge_set) | unique | length) > 1
      then {error: "not a head-to-head: the challenge sets differ", challenge_sets: ($r | map({file, challenge_set}))}
      else {
        challenge_set: $r[0].challenge_set,
        runs: ($r | map({file, models, ladder, ladder_at, overall, pass_rate, invalid_runs, leak_suspect_runs})),
        delta_vs_first: ($r | map(. as $x | {file, ladder: ($x.ladder - $r[0].ladder),
            overall: (if $x.overall == null or $r[0].overall == null then null else ($x.overall - $r[0].overall | . * 10 | round / 10) end),
            pass_rate: (if $x.pass_rate == null or $r[0].pass_rate == null then null else ($x.pass_rate - $r[0].pass_rate | . * 1000 | round / 1000) end)})),
        warnings: ($r | map(select(.invalid_runs > 0 or .leak_suspect_runs > 0)
            | "\(.file): \(.invalid_runs) invalid and \(.leak_suspect_runs) leak-suspect runs; re-run that lane on the same seeds before comparing"))
      } end' <<<"$scored")"
printf '%s\n' "$out"
if [ "$(jq 'has("error")' <<<"$out")" = true ]; then exit 3; fi
