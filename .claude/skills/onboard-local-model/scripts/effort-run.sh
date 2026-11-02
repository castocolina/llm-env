#!/usr/bin/env bash
# effort-run.sh — run coding challenges through pi at several thinking levels against one served model,
# verifying each result and sampling system load. One JSON line per (challenge, effort) in <out>/results.jsonl.
# usage: effort-run.sh --model PROVIDER/ID --out DIR [--efforts off|minimal|low|medium|high|xhigh (comma list, no repeats; default low,medium,high)] [--layers 1,2]
#                      [--per-layer K | --seeds FILE] [--timeout SECONDS]
# One challenge per (layer, seed) from gen-challenge.sh, all generated before the first agent run into
#   <out>/challenges/l<N>-s<S>/ and listed in <out>/seeds.json. Seeds are drawn fresh (--per-layer K, 1..10, default 1) or taken from a
#   seeds.json of a previous run (--seeds FILE: schema 1, this library's generator id, every selected layer present, unique seeds;
#   else exit 2; --per-layer and --seeds are mutually exclusive). Task order: layer, then seed order. --layers keeps only the listed
#   layer numbers (1-5, default all).
# The first stderr line, printed before anything is generated, is `effort-run: <challenges> challenges x <efforts> efforts x <timeout> s upper bound`.
# --timeout is the per-run limit and defaults to the bounded runner's cap (`llmenv agent-runtime-cap`,
# 300 s); a larger value exits 2, because the runner refuses it and every run would fail at launch.
# Each results row records `bounded_outcome` (the runner's outcome, or `launch-failure` when it printed none) so score.sh can
# exclude boundary and launch failures, which measure the harness and not the model. It also records seed, generator, project and
# operators, timeout_s, leak_suspect, outside_writes and other_writes.
# Each (challenge, effort) runs in its own <out>/<id>-<effort>/work, a copy of the challenge that keeps its git baseline commit
# so the agent's changes are saved as <run>/agent.diff (git diff HEAD).
# The verdict comes from a fresh copy of the pristine challenge with only the agent's src/ overlaid, scored by `gen-challenge.sh --score`
# (per test file ONBOARD_GEN_TEST_TIMEOUT_MS, default 20 s, process group killed; a hung file counts as failed).
# The row's `total` is never below the manifest's `tests_total`, so a solution that exits before its tests run (`process.exit` at
# load) never counts as solved.
# Leak tripwire (not a sandbox), per run: the pristine challenge is checked with `gen-challenge.sh --verify` before the copy
# (a mismatch exits 1 at once, `pristine challenge <id> was modified`) and after the agent (the row is written with leak_suspect true,
# then exit 1). A row is also leak_suspect when its transcript names the skill directory, <out>/challenges or manifest.json, or when a
# guarded file changed during the run: outside_writes lists (sorted, at most 20, relative to the guard root) guarded paths that are
# new or newer than a stamp touched just before the agent started, or that vanished. The guard root is $ONBOARD_GUARD_ROOT (default:
# the repo; it must be a git work tree, else exit 2). Guarded files are the git-visible files (tracked plus untracked not ignored) under
# .claude/skills/onboard-local-model/scripts/ and challenges/ and tools/lib.sh: what generates and scores a challenge.
# Not detected: reads or writes of sibling run directories under <out>, reads that never name the paths above, writes elsewhere in the
# guard root that git does not list (ignored files), and anything outside the guard root. Git-visible writes elsewhere in the guard root
# never flag a row; other_writes records them the same way (minus .codegraph/, .pi/, .superpowers/, .playwright-mcp/ and everything
# under .claude/ except .claude/skills/). An edit of a guarded path by another session also flags a row, so run with
# no other writer and re-run a flagged lane on the same seeds. score.sh excludes flagged rows.
# Uses the router profile written by `make setup-local-llm-agents` (PI_CODING_AGENT_DIR defaults to
# ~/.pi/agent) and the repo's bounded runner, so a hung agent is killed and cleaned up.
# --out must resolve to a directory under <repo>/tmp/ (exit 2 otherwise): benchmark challenges run only in isolated temp directories there.
# The model must already be served: this script never starts or stops containers.
# Exit codes: 0 ok, 1 generation failure or a modified pristine challenge, 2 usage (flags, seeds file, --out, --timeout, guard root).
set -euo pipefail

model=""; out=""; efforts="low,medium,high"; layers=""; bound=""; per_layer=""; seeds_file=""
while [ $# -gt 0 ]; do
    case "$1" in
    --model) [ $# -ge 2 ] || { echo "--model needs a value" >&2; exit 2; }; model="$2"; shift 2 ;;
    --out) [ $# -ge 2 ] || { echo "--out needs a value" >&2; exit 2; }; out="$2"; shift 2 ;;
    --efforts) [ $# -ge 2 ] || { echo "--efforts needs a value" >&2; exit 2; }; efforts="$2"; shift 2 ;;
    --layers) [ $# -ge 2 ] || { echo "--layers needs a value" >&2; exit 2; }; layers="$2"; shift 2 ;;
    --timeout) [ $# -ge 2 ] || { echo "--timeout needs a value" >&2; exit 2; }; bound="$2"; shift 2 ;;
    --per-layer) [ $# -ge 2 ] || { echo "--per-layer needs a value" >&2; exit 2; }; per_layer="$2"; shift 2 ;;
    --seeds) [ $# -ge 2 ] || { echo "--seeds needs a value" >&2; exit 2; }; seeds_file="$2"; shift 2 ;;
    -h|--help) sed -n '2,/^set -/s/^# \{0,1\}//p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "unknown flag: $1" >&2; exit 2 ;;
    esac
done
[ -n "$model" ] && [ -n "$out" ] || { echo "--model and --out are required" >&2; exit 2; }
usage() { echo "$*" >&2; exit 2; }
# checked before any path is built from the list: an unknown or empty element, or a repeat, would name a directory
[[ "$efforts" =~ ^(off|minimal|low|medium|high|xhigh)(,(off|minimal|low|medium|high|xhigh))*$ ]] \
    || usage "--efforts must be a comma list of off|minimal|low|medium|high|xhigh; got '$efforts'"
IFS=, read -ra effort_list <<<"$efforts"
seen=" "
for effort in "${effort_list[@]}"; do
    case "$seen" in *" $effort "*) usage "--efforts repeats '$effort'; got '$efforts'" ;; esac
    seen="$seen$effort "
done
[ -z "$per_layer" ] || [ -z "$seeds_file" ] || usage "--per-layer and --seeds are mutually exclusive"
[[ "${per_layer:-1}" =~ ^([1-9]|10)$ ]] || usage "--per-layer must be an integer from 1 to 10; got '$per_layer'"
[ -z "$layers" ] || [[ "$layers" =~ ^[1-5](,[1-5])*$ ]] || usage "--layers must be a comma list of 1-5; got '$layers'"
[ -z "$seeds_file" ] || [ -f "$seeds_file" ] || usage "--seeds file not found: $seeds_file"

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source-path=SCRIPTDIR
# shellcheck source=../../../../tools/lib.sh
source "$here/../../../../tools/lib.sh"
# shellcheck source-path=SCRIPTDIR
# shellcheck source=require-tmp-out.sh
source "$here/require-tmp-out.sh"
require_tmp_out "$out"
cap="$(llmenv agent-runtime-cap | jq -r .runtime_seconds)"
[[ "$cap" =~ ^[1-9][0-9]*$ ]] || { echo "cannot read the bounded runner's cap from llmenv agent-runtime-cap" >&2; exit 2; }
bound="${bound:-$cap}"
[[ "$bound" =~ ^[1-9][0-9]*$ ]] && [ "$bound" -le "$cap" ] \
    || { echo "--timeout must be an integer from 1 to ${cap} (the bounded runner's cap); got '${bound}'" >&2; exit 2; }
guard_root="${ONBOARD_GUARD_ROOT:-$REPO_DIR}"
git -C "$guard_root" rev-parse --is-inside-work-tree >/dev/null 2>&1 \
    || usage "the guard root must be a git work tree (set ONBOARD_GUARD_ROOT); got '$guard_root'"
guard_root="$(cd "$guard_root" && pwd)"

job_task=(); job_layer=(); job_seed=(); seeds_json=""
gid="$(bash "$here/gen-challenge.sh" --generator-id)"
layer_json="$(tr , '\n' <<<"${layers:-1,2,3,4,5}" | sort -un | jq -R -s -c 'split("\n") | map(select(length > 0) | tonumber)')"
if [ -n "$seeds_file" ]; then
    jq -e . "$seeds_file" >/dev/null 2>&1 || usage "seeds file is not valid JSON: $seeds_file"
    problem="$(jq -r --arg gen "$gid" --argjson layers "$layer_json" '
        if type != "object" then "seeds file must be a JSON object"
        elif .schema != 1 then "seeds file: schema must be 1"
        elif .generator != $gen then "seeds file: generator \(.generator) is not this library (\($gen)); draw new seeds"
        else ((.layers | if type == "object" then . else {} end) as $l | [$layers[] | tostring as $k | ($l[$k] // null) as $s
            | if ($s | type) != "array" or ($s | length) == 0 then "seeds file: layer \($k) has no seeds"
              elif ($s | all(type == "number" and . == floor and . >= 0 and . <= 4294967295) | not) then "seeds file: layer \($k) seeds must be integers from 0 to 4294967295"
              elif ($s | unique | length) != ($s | length) then "seeds file: layer \($k) seeds are not unique"
              else empty end] | first // empty) end' "$seeds_file")"
    [ -z "$problem" ] || usage "$problem"
    seeds_json="$(cat "$seeds_file")"
else
    drawn=""
    for layer in $(jq -r '.[]' <<<"$layer_json"); do
        seen=" "
        while [ "$(wc -w <<<"$seen")" -lt "${per_layer:-1}" ]; do
            s="$(od -An -N4 -tu4 /dev/urandom | tr -d ' ')"
            case "$seen" in *" $s "*) continue ;; esac
            seen="$seen$s "; drawn="$drawn$layer $s"$'\n'
        done
    done
    seeds_json="$(jq -R -n --arg gen "$gid" --argjson k "${per_layer:-1}" '[inputs | select(length > 0) | split(" ") | {l: .[0], s: (.[1] | tonumber)}]
        | {schema: 1, generator: $gen, per_layer: $k, layers: (group_by(.l) | map({key: .[0].l, value: map(.s)}) | from_entries)}' <<<"$drawn")"
fi
while read -r layer seed; do
    job_task+=("l${layer}-s${seed}"); job_layer+=("$layer"); job_seed+=("$seed")
done < <(jq -r --argjson layers "$layer_json" '.layers as $l | $layers[] | tostring as $k | $l[$k][] | "\($k) \(.)"' <<<"$seeds_json")
echo "effort-run: ${#job_task[@]} challenges x ${#effort_list[@]} efforts x ${bound} s upper bound" >&2

export PI_CODING_AGENT_DIR="${PI_CODING_AGENT_DIR:-$HOME/.pi/agent}"
pci="$(yq -r '.gpu.pci_address' "$CONFIG_PATH")"
# a re-run into a used --out would empty results.jsonl and overwrite seeds.json before generation fails on the non-empty challenge dir
if [ -e "$out/seeds.json" ] || [ -e "$out/challenges" ]; then
    echo "effort-run: $out already holds a generated run; use a fresh --out (re-run a flagged lane with --seeds $out/seeds.json and a new --out)" >&2; exit 2
fi
out="$(mkdir -p "$out" && cd "$out" && pwd)"   # absolute: the agent runs after a cd
: >"$out/results.jsonl"
sampler_pid=""
trap '[ -z "$sampler_pid" ] || kill "$sampler_pid" 2>/dev/null || true' EXIT

printf '%s\n' "$seeds_json" >"$out/seeds.json"
for i in "${!job_task[@]}"; do
    if ! gen_out="$(bash "$here/gen-challenge.sh" --layer "${job_layer[$i]}" --seed "${job_seed[$i]}" --out "$out/challenges/${job_task[$i]}" 2>&1)"; then
        printf '%s\n' "$gen_out" >&2
        echo "effort-run: generation failed for layer ${job_layer[$i]} seed ${job_seed[$i]}" >&2; exit 1
    fi
done

# the pristine generated challenge must still match its manifest (V3 of gen-challenge.sh --verify); prints the differences otherwise
pristine_intact() { gen_check="$(bash "$here/gen-challenge.sh" --verify "$out/challenges/$1" 2>&1)" || { printf '%s\n' "$gen_check" >&2; return 1; }; }

# what generates and scores a challenge (gen-challenge.sh sources tools/lib.sh); other writers elsewhere never flag a row
guard_paths=(.claude/skills/onboard-local-model/scripts .claude/skills/onboard-local-model/challenges tools/lib.sh)

# guard_list [other]: NUL-separated guarded paths that exist now; with `other`, the other git-visible files instead (see the header)
guard_list() {
    local scope="${1:-}" spec=("${guard_paths[@]}")
    [ "$scope" != other ] || spec=(. "${guard_paths[@]/#/:!}")
    git -C "$guard_root" ls-files -z --cached --others --exclude-standard -- "${spec[@]}" | while IFS= read -r -d '' p; do
        [ "$scope" != other ] || case "$p" in .codegraph/*|.pi/*|.superpowers/*|.playwright-mcp/*) continue ;; .claude/skills/*) ;; .claude/*) continue ;; esac
        # --cached also lists tracked files already deleted in the work tree; the listing means "existed at stamp time"
        [ -e "$guard_root/$p" ] || [ -L "$guard_root/$p" ] || continue
        printf '%s\0' "$p"
    done
}

# outside_writes STAMP BEFORE AFTER: JSON array. A file counts when it is not older than the stamp (a tie counts: the kernel's coarse
# clock can give a write right after the touch the same mtime) or when it was listed before the run and is gone.
outside_writes() {
    {
        while IFS= read -r -d '' p; do
            if { [ -e "$guard_root/$p" ] || [ -L "$guard_root/$p" ]; } && ! [ "$1" -nt "$guard_root/$p" ]; then printf '%s\n' "$p"; fi
        done <"$3"
        while IFS= read -r -d '' p; do
            if ! [ -e "$guard_root/$p" ] && ! [ -L "$guard_root/$p" ]; then printf '%s\n' "$p"; fi
        done <"$2"
    } | LC_ALL=C sort -u | sed -n '1,20p' | jq -R -s -c 'split("\n") | map(select(length > 0))'
}

skill_dir="$(cd "$here/.." && pwd)"
needles=(-e "$skill_dir" -e "$(cd "$here/.." && pwd -P)" -e "$out/challenges" -e "$(cd "$out" && pwd -P)/challenges" -e manifest.json -e skills/onboard-local-model)

for i in "${!job_task[@]}"; do
    task="${job_task[$i]}"; layer="${job_layer[$i]}"; seed="${job_seed[$i]}"
    pristine="$out/challenges/$task/challenge"
    pristine_intact "$task" || { echo "pristine challenge $task was modified" >&2; exit 1; }
    meta="$(jq -c --arg g "$gid" '{generator: $g, project, operators: [.operators[].op], tests_total}' "$out/challenges/$task/manifest.json")"
    for effort in "${effort_list[@]}"; do
        run="$out/${task}-${effort}"
        rm -rf "$run"; mkdir -p "$run"
        cp -r "$pristine/." "$run/work"
        # the bounded runner refuses anything but pre-existing regular files with mode 0600
        install -m 600 /dev/null "$run/transcript.jsonl"; install -m 600 /dev/null "$run/stderr.txt"
        bash "$here/gpu-sample.sh" start "$pci" "$run/gpu.csv" 2 &
        sampler_pid=$!
        touch "$run/stamp"; guard_list >"$run/guard-before.lst"; guard_list other >"$run/other-before.lst"
        start="$(date +%s)"
        status=0
        (cd "$run/work" && llmenv run-agent-bounded \
            --transcript "$run/transcript.jsonl" --stderr "$run/stderr.txt" --runtime-seconds "$bound" \
            -- pi --no-session --no-extensions --no-skills --no-prompt-templates --no-context-files \
               -p --mode json --thinking "$effort" --model "$model" "$(cat prompt.md)" </dev/null >"$run/bounded.json") || status=$?
        seconds=$(( $(date +%s) - start ))
        guard_list >"$run/guard-after.lst"; guard_list other >"$run/other-after.lst"
        kill "$sampler_pid" 2>/dev/null || true; wait "$sampler_pid" 2>/dev/null || true; sampler_pid=""
        writes="$(outside_writes "$run/stamp" "$run/guard-before.lst" "$run/guard-after.lst")"
        other_writes="$(outside_writes "$run/stamp" "$run/other-before.lst" "$run/other-after.lst")"
        leak=false; [ "$writes" = "[]" ] || leak=true
        grep -qF "${needles[@]}" "$run/transcript.jsonl" && leak=true
        pristine_changed=0
        if ! pristine_intact "$task"; then pristine_changed=1; leak=true; fi
        # new files count too: mark them intent-to-add so `git diff HEAD` lists them
        git -C "$run/work" add -A -N . && git -C "$run/work" diff HEAD >"$run/agent.diff" || : >"$run/agent.diff"
        # the agent could edit or plant tests to pass: verify a fresh copy of the pristine challenge with only the agent's src/ overlaid
        # (a deleted src/ then scores 0 instead of aborting the sweep)
        rm -rf "${run:?}/verify"; cp -r "$pristine" "$run/verify"
        if [ -d "$run/work/src" ]; then rm -rf "${run:?}/verify/src"; cp -r "$run/work/src" "$run/verify/src"; fi
        verdict="$(bash "$here/gen-challenge.sh" --score "$run/verify")"
        bounded_outcome="$(jq -r '.outcome // empty' "$run/bounded.json" 2>/dev/null || true)"
        bounded_outcome="${bounded_outcome:-launch-failure}"
        # a harness exit of 0 does not mean the model answered: a 401 or 429 is recorded as stopReason=error
        api_error=false; grep -q '"stopReason":"error"' "$run/transcript.jsonl" && api_error=true
        counts="$(jq -R -s -c '[split("\n")[] | fromjson? | select(.type? == "tool_execution_end")]
            | {tool_calls: length, tool_errors: (map(select(.isError == true)) | length)}' "$run/transcript.jsonl")"
        jq -nc --argjson counts "$counts" --argjson api_error "$api_error" --arg task "$task" --argjson layer "$layer" --arg effort "$effort" --arg model "$model" --arg bounded_outcome "$bounded_outcome" \
            --argjson status "$status" --argjson seconds "$seconds" --argjson verdict "$verdict" --argjson meta "$meta" --argjson seed "${seed:-null}" \
            --argjson timeout "$bound" --argjson leak "$leak" --argjson writes "$writes" --argjson other_writes "$other_writes" \
            --argjson gpu "$(bash "$here/gpu-sample.sh" summary "$run/gpu.csv")" \
            '{task: $task, layer: $layer, seed: $seed, effort: $effort, model: $model, api_error: $api_error, bounded_outcome: $bounded_outcome, agent_status: $status, seconds: $seconds,
              # shortcut: tests_total counts a shipped test file that fails to load as one test, so a file with exactly one test can still be cheated
              # (exit 0 only in the process of that file = 1 pass). Only challenges/projects/log-parser/test/report.test.js ships one test
              # (test_only_known_library_test_files_ship_a_single_test); give it a second at the next library edit, which changes the generator id anyway.
              pass: $verdict.pass, total: ([$verdict.total, $meta.tests_total] | max), gpu: $gpu} + $meta + {timeout_s: $timeout, leak_suspect: $leak, outside_writes: $writes, other_writes: $other_writes} + $counts' | tee -a "$out/results.jsonl"
        [ "$pristine_changed" = 0 ] || { echo "pristine challenge $task was modified" >&2; exit 1; }
    done
done
