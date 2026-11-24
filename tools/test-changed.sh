#!/usr/bin/env bash
# test-changed.sh — run the tests selected from a change (spec R2, R3, R10). Usage: test-changed.sh --stage commit|push
# (push reads PUSH_FROM/PUSH_TO). At commit it first checks that git will run the pre-push hook, and deselects slow
# tests. The ownership lint (spec G1) runs in parallel with the selected tests and its output is printed after them.
# Every tools/test.sh status passes through, 5 included: the always-run test is in every selection, so "nothing
# collected" means something dropped every test; a failed lint turns 0 into 1.
set -euo pipefail
stage=${2:-}
if [ "$stage" = commit ]; then
  h=$(git rev-parse --git-path hooks/pre-push)
  target=$(readlink -f "$(git rev-parse --path-format=absolute --git-common-dir)/../tools/pre-push.sh")
  if ! [ -x "$h" ] || [ "$(readlink -f "$h")" != "$target" ]; then
    echo "pre-push hook is not an executable link to tools/pre-push.sh: run make dev-setup" >&2
    exit 1
  fi
fi
out=$(mktemp -d /tmp/llm-env-lint.XXXXXX)  # outside repo tmp/ and the runner's TMPDIR, which test.sh leak-checks
trap 'wait; cat "$out/out"; cat "$out/err" >&2; rm -rf -- "$out"' EXIT
uv run llmenv.py selection-lint >"$out/out" 2>"$out/err" & lint=$!  # over the whole tree, every invocation
json=$(uv run llmenv.py select-tests --stage "$stage")
marker=()
[ "$stage" = commit ] && marker=(-m "not slow")
if [ "$(jq -r '.full' <<<"$json")" = true ]; then
  tests=(tests/)
else
  mapfile -t tests < <(jq -r '.tests[]' <<<"$json")
fi
rc=0; bash tools/test.sh "${marker[@]}" "${tests[@]}" || rc=$?
wait "$lint" || exit $((rc ? rc : 1))
exit "$rc"
