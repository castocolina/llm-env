#!/usr/bin/env bash
# pre-push.sh — git's pre-push hook, linked by make dev-setup (spec R3, D8). Usage: pre-push.sh <remote> <url>,
# git's ref lines on stdin. One push shape is checked: a fast-forward (or new branch) of the checked-out branch (sent
# by name or as HEAD) to itself with a clean index and worktree. Every other shape fails closed.
set -euo pipefail
root=$(git rev-parse --show-toplevel)
refuse() { echo "push shape not supported by the hook: run make test, then git push --no-verify" >&2; exit 1; }
# make test checks the worktree, not the pushed commit, so a dirty tree gets no --no-verify advice
dirty() { echo "push refused: the index or worktree is dirty: commit or stash your changes, then push again" >&2; exit 1; }

mapfile -t lines
[ "${#lines[@]}" -eq 0 ] && exit 0
read -r local_ref local_sha remote_ref remote_sha <<<"${lines[0]}"
[ "${#lines[@]}" -eq 1 ] && [[ $local_sha =~ ^0+$ ]] && exit 0
git diff --cached --quiet || dirty
git diff --quiet || dirty
[ "${#lines[@]}" -eq 1 ] || refuse
b=$(git symbolic-ref -q HEAD) || refuse
head=$(git rev-parse HEAD)
{ [ "$local_ref" = "$b" ] || [ "$local_ref" = HEAD ]; } && [ "$remote_ref" = "$b" ] && [ "$local_sha" = "$head" ] || refuse
[[ $remote_sha =~ ^0+$ ]] || git merge-base --is-ancestor "$remote_sha" HEAD 2>/dev/null || refuse

bash "$root/tools/validate.sh"
# exec: a signal to the hook's PID reaches the test run instead of orphaning it
if [[ $remote_sha =~ ^0+$ ]]; then
  exec bash "$root/tools/test.sh"
else
  PUSH_FROM=$remote_sha PUSH_TO=$head exec bash "$root/tools/test-changed.sh" --stage push
fi
