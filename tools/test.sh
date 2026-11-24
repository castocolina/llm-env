#!/usr/bin/env bash
# test.sh — Python test suite on LLM_ENV_TEST_CPU_PCT % of the CPUs (default 40), niced, quiet, and offline:
# pytest runs in network (loopback only) and PID namespaces that keep the caller's uid.
# Each run gets its own TMPDIR (printed first as "run dir: <path>" on stderr) and fails with one "leak: <what>" line per
# new entry under repo tmp/, in its TMPDIR (except pytest's temp root and uv's empty lock files) or in Podman's container/volume/image IDs.
# Exit: 69 no namespace, 4 uv cannot resolve llmenv.py offline, 1 incomplete inventory ("inventory: <what>"),
# 130/143 on INT/TERM, else pytest's own status, with a leak turning 0 or 5 into 1.
# Usage: bash tools/test.sh [pytest args...]   (default target: tests/)
set -euo pipefail

unset LLM_ENV_UPDATE_PROOF LLM_ENV_UPDATE_CHARACTERIZE LLM_ENV_LIVE_VM LLM_ENV_MUTATION_CHECK PYTEST_ADDOPTS  # opt-in lanes run only by hand

runs=/tmp/pytest-of-${USER:-$(id -un)}  # under pylib/observe_fs.py's pytest_roots
run='' inv=''
trap 'rm -rf -- "$run" "$inv"' EXIT
# shellcheck disable=SC2174 # only $runs is created: /tmp exists
mkdir -p -m 700 "$runs"
run=$(mktemp -d "$runs/llm-env-run.XXXXXX")
inv=$(mktemp -d "$runs/llm-env-inventory.XXXXXX")
echo "run dir: $run" >&2
mkdir "$run/pytest"

# shellcheck disable=SC2329 # invoked by the INT and TERM traps
stop() {                         # $1: 130 or 143. A & job in non-interactive bash ignores SIGINT, so TERM is sent
  trap '' INT TERM
  kill -TERM -- "-$child" 2>/dev/null || true
  { sleep 5 && kill -KILL -- "-$child" 2>/dev/null; } & guard=$!
  wait "$child" 2>/dev/null || true; pkill -KILL -P "$guard" || true  # the guard inherits TERM ignored: KILL its sleep
  exit "$1"                      # the EXIT trap removes the run dir; with no chain yet the kills are no-ops
}
child=; trap 'stop 130' INT; trap 'stop 143' TERM

pct=${LLM_ENV_TEST_CPU_PCT-40}
if ! { [[ $pct =~ ^[1-9][0-9]{0,2}$ ]] && ((10#$pct <= 100)); }; then
  echo "LLM_ENV_TEST_CPU_PCT must be an integer from 1 to 100 without leading zeros, got '$pct'" >&2
  exit 2
fi
workers=$(($(nproc) * pct / 100))
((workers >= 1)) || workers=1
[ $# -gt 0 ] || set -- tests/

# shortcut: needs unprivileged user namespaces, which Bazzite has; add an opt-out only for a host without them.
# The outer namespace maps us to root to bring lo up; the nested one maps root back to the caller's uid and gid.
# shellcheck disable=SC2016 # $1/$2 expand in the inner bash, not here.
chain=(unshare -rn --pid --fork --kill-child --mount-proc bash -c 'ip link set lo up && exec unshare --user --map-user="$1" --map-group="$2" "${@:3}"' _ "$(id -u)" "$(id -g)")
"${chain[@]}" true || { echo "network namespace unavailable" >&2; exit 69; }

podman=$(command -v podman) || echo "podman not found: Podman leak check skipped" >&2
podman_ids() {
  podman ps -a -q --no-trunc | sed 's/^/podman container /' && podman volume ls -q | sed 's/^/podman volume /' &&
    podman images -q --no-trunc | sed 's/^/podman image /'
}
snapshot() {  # $1: file name in $inv; exact relative paths under repo tmp/ and Podman IDs, sorted
  : >"$inv/raw"
  if [ -e tmp ] && ! find tmp -mindepth 1 >>"$inv/raw"; then echo "inventory: repo tmp/ unreadable" >&2; exit 1; fi
  if [ -n "$podman" ] && ! podman_ids >>"$inv/raw"; then echo "inventory: podman unavailable" >&2; exit 1; fi
  LC_ALL=C sort -u "$inv/raw" >"$inv/$1"
}
snapshot before

# This bash stays the namespace's PID 1 (no exec; the trailing exit stops bash from exec'ing the last command):
# it reaps the orphans tests leave behind, which uv as PID 1 would keep as zombies (kill -0 still finds them).
# shellcheck disable=SC2016 # "$@" expands in the inner bash, not here.
setsid "${chain[@]}" env --default-signal=INT UV_OFFLINE=1 TMPDIR="$run" PYTEST_DEBUG_TEMPROOT="$run/pytest" bash -c 'uv run --offline llmenv.py --help >/dev/null || { echo "uv cannot resolve llmenv.py offline: run make dev-setup once with network" >&2; exit 4; }; nice -n 10 uv run pytest "$@"; exit $?' _ -n "$workers" -q "$@" &
child=$!
rc=0; wait "$child" || rc=$?     # || : errexit would exit with the child's status

# shortcut: a diff cannot tell another agent's or the live stack's new object (or a concurrent writer under repo tmp/,
# such as a delegated run) from a leak; the line names it for a person to judge; switch to label-based attribution if
# false alarms recur.
snapshot after
# shortcut: uv leaves an empty uv-<16 hex>.lock in TMPDIR on every `uv run` and has no setting to move it, so exactly
# that file is not a leak; drop the exemption once uv cleans it up or can put it elsewhere.
leaks=$({
  LC_ALL=C comm -13 "$inv/before" "$inv/after"
  find "$run" -mindepth 1 -maxdepth 1 -regextype posix-extended ! -name pytest \
    ! \( -type f -empty -regex '.*/uv-[0-9a-f]{16}\.lock' \) -printf 'TMPDIR/%f\n'
} | sed 's/^/leak: /')
if [ -n "$leaks" ]; then
  echo "$leaks" >&2
  ! grep -q '^leak: tmp/' <<<"$leaks" || echo "leak check: if another process writes under tmp/, rerun" >&2
  ((rc != 0 && rc != 5)) || rc=1
fi
exit "$rc"
