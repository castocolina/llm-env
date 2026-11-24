#!/usr/bin/env bash
# selection-mutation-check.sh — spec G2: ten one-line faults, each committed in a fresh scratch copy of the index, must
# be caught by the push-selected tests. Prints "<verdict> <n> <kind>" per fault (caught, HOLE, INVALID or HARNESS).
# Exit 2 on any HARNESS (or a failed control or setup), else 1 on any HOLE or INVALID, else 0.
# Usage: selection-mutation-check.sh [--only N] [--break-cli-edge]   (the selector without its CLI edge: expect a HOLE)
#        selection-mutation-check.sh --list                          (file<TAB>search<TAB>replace<TAB>kind per fault)
#        selection-mutation-check.sh --classify <rc> <stdout-file> <stderr-file>
set -euo pipefail

# shellcheck disable=SC2016 # literal text: the backticks and $(( )) belong to the target lines
faults=(  # file|search|replace|kind; each search matches exactly one line (pinned by the integrity test)
  'pylib/omniroute_cli.py|print("idle")|print("idle.")|pylib-cli'
  'scripts/heal-omniroute-admission.sh|OMNIROUTE_HEAL_MEMORY_SAMPLES:-3|OMNIROUTE_HEAL_MEMORY_SAMPLES:-4|sh'
  'docker/observe/dashboards/laya.json|"title": "Laya: requests, question types, confidence"|"title": "Laya: requests, question kinds, confidence"|dashboard'
  'docker/observe/compose.yml|GF_USERS_ALLOW_SIGN_UP: "false"|GF_USERS_ALLOW_SIGNUP: "false"|compose-key'
  'README.md|is the executor combo and is never touched by `make local-combo`|is the executor combo and is never changed by `make local-combo`|doc'
  'Makefile|@bash tools/run-target.sh model-capacity -- bash scripts/model-capacity.sh|@bash tools/run-target.sh model-capacity -- bash scripts/model-capacity-x.sh|makefile'
  'tools/lib.sh|2>/dev/null)" = true ]; }|2>/dev/null)" = false ]; }|lib-sh'
  'tests/conftest.py|os.environ["HF_HUB_OFFLINE"] = "1"|os.environ["HF_HUB_OFFLINE"] = "0"|conftest'
  '.claude/skills/onboard-local-model/scripts/fetch-gguf.sh|need=$((size - have + 1024 * 1048576))|need=$((size - have + 2048 * 1048576))|skill-script'
  'pylib/compose.py|"stop_grace_period": "40s"|"stop_grace_period": "30s"|pylib-render'
)
integrity=tests/test_hooks.py::test_every_mutation_fault_matches_exactly_one_line  # fails by design in a fault copy

classify() {  # $1 rc, $2 stdout file, $3 stderr file. xdist reports a crashed worker's test as failed: a harness error
  if grep -Eq 'node down:|crashed while running|replacing crashed worker|maximum crashed workers' "$2" ||
    grep -Eq 'leak:|inventory:|network namespace unavailable|uv cannot resolve' "$3"; then
    echo HARNESS
  elif [ "$1" = 0 ]; then
    echo passed
  elif [ "$1" = 1 ] && tail -n 1 "$2" | grep -Eq '[0-9]+ failed' && ! tail -n 1 "$2" | grep -Eq '[0-9]+ error'; then
    echo caught
  else
    echo HARNESS
  fi
}

case ${1:-} in
  --list)
    for row in "${faults[@]}"; do IFS='|' read -r f s r k <<<"$row"; printf '%s\t%s\t%s\t%s\n' "$f" "$s" "$r" "$k"; done
    exit 0 ;;
  --classify) classify "$2" "$3" "$4"; exit 0 ;;
esac
only='' break_edge=''
while [ $# -gt 0 ]; do
  case $1 in
    --only) only=${2-none}; shift $(($# >= 2 ? 2 : 1)) ;;
    --break-cli-edge) break_edge=1; shift ;;
    *) echo "usage: $0 [--list] [--only N] [--break-cli-edge] | --classify <rc> <out> <err>" >&2; exit 2 ;;
  esac
done
[ -z "$only" ] || [[ $only =~ ^([1-9]|10)$ ]] || { echo "--only takes 1 to ${#faults[@]}" >&2; exit 2; }
unset SELECT_ALL PUSH_FROM PUSH_TO
# an exported GIT_DIR or GIT_INDEX_FILE would aim the scratch copies' git commands at this repo
while read -r v; do unset "$v"; done < <(git rev-parse --local-env-vars)

cd "$(dirname "$0")/.."
for d in . /tmp; do
  pct=$(df --output=pcent "$d" | tail -n 1 | tr -dc 0-9)
  ((pct <= 70)) || { echo "$d is $pct% full (limit 70%)" >&2; exit 2; }
done
git diff --quiet -- tools pylib tests llmenv.py || { echo "unstaged changes under tools pylib tests llmenv.py: stage them" >&2; exit 2; }

finished=''
scratch=$(mktemp -d /tmp/selection-mutation.XXXXXX)
trap 'st=$?; rm -rf -- "$scratch"; [ -n "$finished" ] || ((st > 128)) || exit 2' EXIT  # only the verdict exits 0 or 1
trap 'exit 130' INT; trap 'exit 143' TERM

g() { git -C "$1" -c user.name=mutation -c user.email=mutation@localhost -c commit.gpgsign=false -c core.hooksPath=/dev/null "${@:2}"; }
replace() {  # $1 file, $2 search, $3 replacement: exactly one matching line, replaced literally
  local text
  [ "$(grep -c -F -- "$2" "$1")" = 1 ] || { echo "$1: '$2' does not match exactly one line" >&2; return 1; }
  text=$(cat -- "$1"; echo x); text=${text%x}
  printf '%s' "${text/"$2"/"$3"}" >"$1"
}
copy() {  # $1: name; a fresh copy of the base commit with its own environment
  cp -a "$scratch/base" "$scratch/$1" &&
    UV_PROJECT_ENVIRONMENT=$scratch/$1/.venv uv sync --project "$scratch/$1" --frozen --offline -q
}
run() {  # $1 copy name, $2 label, rest: test.sh args. Sets $rc and $v (the verdict); the summary goes to stderr
  local dir=$scratch/$1 log=$scratch/$1.$2
  rc=0
  (cd "$dir" && UV_PROJECT_ENVIRONMENT=$dir/.venv bash tools/test.sh -rfE "${@:3}") >"$log.out" 2>"$log.err" || rc=$?
  v=$(classify "$rc" "$log.out" "$log.err")
  echo "  $2: rc=$rc $v: $(tail -n 1 "$log.out")" >&2
  grep -E '^(FAILED|ERROR) ' "$log.out" | cut -c1-240 | sed 's/^/    /' >&2 || true
  [ "$v" != HARNESS ] || { tail -n 20 "$log.out"; tail -n 20 "$log.err"; } >&2
}
select_json() {  # $1 copy name, $2 llmenv.py to run (the copy's own, or the scratch selector's): the push selection
  (cd "$scratch/$1" && PUSH_FROM=$base_sha PUSH_TO=$(git rev-parse HEAD) uv run --offline "$2" select-tests --stage push)
}
targets() { if [ "$(jq -r .full <<<"$1")" = true ]; then echo tests/; else jq -r '.tests[]' <<<"$1"; fi; }

git checkout-index -a --prefix="$scratch/base/"
mkdir "$scratch/base/tmp"
g "$scratch/base" -c init.defaultBranch=main init -q
g "$scratch/base" add -A
g "$scratch/base" commit -q -m base
base_sha=$(git -C "$scratch/base" rev-parse HEAD)
cp -a "$scratch/base" "$scratch/sel"  # runs only llmenv.py (its own uv script environment), so no project sync
replace "$scratch/sel/pylib/selection.py" 'cli_edges(tree)' '{}'

echo "control: full suite on the base copy" >&2
copy control
run control control tests/
[ "$v" = passed ] || { echo "HARNESS 0 control rc=$rc"; exit 2; }
rm -rf -- "$scratch/control"

fault() {  # $1 n, $2 file, $3 search, $4 replace; sets $v to caught, HOLE, INVALID or HARNESS
  local n=$1 name=f$1 json direct=''
  v=HARNESS
  copy "$name" && replace "$scratch/$name/$2" "$3" "$4" && g "$scratch/$name" commit -q -am "fault $n" || return 0
  if [ "$n" = 1 ] || [ -n "$break_edge" ]; then
    echo "  selection without the CLI edge:" >&2
    direct=$(select_json "$name" "$scratch/sel/llmenv.py") || return 0
  fi
  if [ "$n" = 1 ]; then  # a CLI-edge fault: the module's direct namers exist and do not catch it
    v=INVALID
    jq -e --arg p "$2" '.reasons[$p] | test("^[1-9][0-9]* owner")' <<<"$direct" >/dev/null || return 0
    if [ -z "$break_edge" ]; then
      mapfile -t tests < <(targets "$direct")
      run "$name" direct "${tests[@]}" --deselect "$integrity"
      [ "$v" = passed ] || { [ "$v" = HARNESS ] || v=INVALID; return 0; }
    fi
  fi
  if [ -n "$break_edge" ]; then
    json=$direct
  else
    echo "  selection:" >&2
    json=$(select_json "$name" llmenv.py) || { v=HARNESS; return 0; }
  fi
  mapfile -t tests < <(targets "$json")
  run "$name" subset "${tests[@]}" --deselect "$integrity"
  [ "$v" = passed ] || return 0
  run "$name" full tests/ --deselect "$integrity"
  case $v in caught) v=HOLE ;; passed) v=INVALID ;; *) v=HARNESS ;; esac
}

status=0
for n in "${!faults[@]}"; do
  n=$((n + 1))
  [ -z "$only" ] || [ "$n" = "$only" ] || continue
  IFS='|' read -r file search replace kind <<<"${faults[n - 1]}"
  echo "fault $n ($kind): $file" >&2
  fault "$n" "$file" "$search" "$replace"
  rm -rf -- "${scratch:?}/f$n"
  echo "$v $n $kind"
  case $v in HARNESS) status=2 ;; HOLE | INVALID) ((status == 2)) || status=1 ;; esac
done
finished=1
exit "$status"
