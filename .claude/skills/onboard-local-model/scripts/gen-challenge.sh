#!/usr/bin/env bash
# gen-challenge.sh — generate one seeded benchmark challenge from the skill's challenge library, or check or verify one.
# usage: gen-challenge.sh --layer N --seed S --out DIR [--with-reference] | --check-library --out DIR [--layer N] | --verify DIR | --score DIR | --generator-id | --help
# Layers 1-5, seed an unsigned 32-bit integer; DIR must resolve under <repo>/tmp/ and be empty or absent.
# --with-reference also keeps DIR/reference/ (tests and diagnostics only; benchmark runs never use it).
# A successful generation makes DIR/challenge a git repository with one "baseline" commit and prints one JSON line:
# {id, challenge, manifest, layer, seed, project, operators, expected_failing_files}.
# --score DIR runs every DIR/test/**/*.test.js and prints {pass, total, timed_out}; each file gets ONBOARD_GEN_TEST_TIMEOUT_MS
#   (default 20000) and a process-group kill, a hung or cancelled test counts as failed, and it writes nothing.
# --generator-id prints g-<12 hex>: a hash of the generator library, so any library edit changes it.
# Takes no input file: challenges come only from the library plus a seed, never from a transcript or a repository.
# Exit codes: 0 ok, 1 generation or library failure, 2 usage (unknown flag, missing or invalid argument, --out outside ./tmp/).
set -euo pipefail

mode="generate"; layer=""; seed=""; out=""; verify_dir=""; with_ref=0; have_layer=0; have_seed=0; have_out=0; have_verify=0; score_dir=""; have_score=0
while [ $# -gt 0 ]; do
    case "$1" in
    --layer) [ $# -ge 2 ] || { echo "--layer needs a value" >&2; exit 2; }; layer="$2"; have_layer=1; shift 2 ;;
    --seed) [ $# -ge 2 ] || { echo "--seed needs a value" >&2; exit 2; }; seed="$2"; have_seed=1; shift 2 ;;
    --out) [ $# -ge 2 ] || { echo "--out needs a value" >&2; exit 2; }; out="$2"; have_out=1; shift 2 ;;
    --with-reference) with_ref=1; shift ;;
    --check-library) mode="check"; shift ;;
    --verify) [ $# -ge 2 ] || { echo "--verify needs a directory" >&2; exit 2; }; mode="verify"; verify_dir="$2"; have_verify=1; shift 2 ;;
    --score) [ $# -ge 2 ] || { echo "--score needs a directory" >&2; exit 2; }; mode="score"; score_dir="$2"; have_score=1; shift 2 ;;
    --generator-id) mode="id"; shift ;;
    -h|--help) sed -n '2,/^set -/s/^# \{0,1\}//p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "unknown flag: $1 (usage: gen-challenge.sh --layer N --seed S --out DIR | --check-library --out DIR | --verify DIR | --score DIR | --generator-id)" >&2; exit 2 ;;
    esac
done

case "$mode" in
generate) [ "$have_layer" = 1 ] && [ "$have_seed" = 1 ] && [ "$have_out" = 1 ] || { echo "--layer, --seed and --out are required" >&2; exit 2; } ;;
check) [ "$have_out" = 1 ] || { echo "--check-library requires --out" >&2; exit 2; } ;;
verify) [ "$have_verify" = 1 ] && [ -n "$verify_dir" ] || { echo "--verify requires a directory" >&2; exit 2; } ;;
score) [ "$have_score" = 1 ] && [ -n "$score_dir" ] || { echo "--score requires a directory" >&2; exit 2; } ;;
esac

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source-path=SCRIPTDIR
# shellcheck source=../../../../tools/lib.sh
source "$here/../../../../tools/lib.sh"
# shellcheck source-path=SCRIPTDIR
# shellcheck source=require-tmp-out.sh
source "$here/require-tmp-out.sh"

# require_tmp_out only after the required-argument checks: it exits 1, not 2, on an empty path.
if [ "$have_out" = 1 ]; then
    [ -n "$out" ] || { echo "--out must not be empty" >&2; exit 2; }
    require_tmp_out "$out"
fi
if [ "$have_layer" = 1 ]; then
    [[ "$layer" =~ ^[1-5]$ ]] || { echo "--layer must be 1-5; got '$layer'" >&2; exit 2; }
fi
if [ "$have_seed" = 1 ]; then
    [[ "$seed" =~ ^[0-9]+$ ]] && [ "${#seed}" -le 10 ] && [ "$seed" -le 4294967295 ] \
        || { echo "--seed must be an integer from 0 to 4294967295; got '$seed'" >&2; exit 2; }
fi
if [ "$have_out" = 1 ] && [ -n "$(ls -A "$out" 2>/dev/null || true)" ]; then
    echo "--out must be empty or absent: $out" >&2; exit 2
fi
require_cmd node git
[ "$mode" != generate ] || require_cmd jq

args=()
case "$mode" in
generate) args=(--layer "$layer" --seed "$seed" --out "$out"); [ "$with_ref" = 0 ] || args+=(--with-reference) ;;
check) args=(--check-library --out "$out"); [ "$have_layer" = 0 ] || args+=(--layer "$layer") ;;
verify) args=(--verify "$verify_dir") ;;
score) args=(--score "$score_dir") ;;
id) args=(--generator-id) ;;
esac
if [ "$mode" != "generate" ]; then exec node "$here/../challenges/gen.mjs" "${args[@]}"; fi

node "$here/../challenges/gen.mjs" "${args[@]}"
git -C "$out/challenge" init -q
git -C "$out/challenge" add -A
git -C "$out/challenge" -c user.name=gen-challenge -c user.email=gen-challenge@localhost -c commit.gpgsign=false -c core.hooksPath=/dev/null commit -q -m baseline
jq -c --arg challenge "$(readlink -m "$out/challenge")" --arg manifest "$(readlink -m "$out/manifest.json")" \
    '{id, challenge: $challenge, manifest: $manifest, layer, seed, project, operators: [.operators[].op], expected_failing_files: [.expected_failing[].file]}' \
    "$out/manifest.json"
