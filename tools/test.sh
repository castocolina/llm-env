#!/usr/bin/env bash
# test.sh — Python test suite on LLM_ENV_TEST_CPU_PCT % of the CPUs (default 40), niced, quiet, and offline:
# pytest runs in network (loopback only) and PID namespaces that keep the caller's uid.
# Exit: 69 no namespace, 4 uv cannot resolve llmenv.py offline, else pytest's own status.
# Usage: bash tools/test.sh [pytest args...]   (default target: tests/)
set -euo pipefail

unset LLM_ENV_UPDATE_PROOF LLM_ENV_UPDATE_CHARACTERIZE LLM_ENV_LIVE_VM  # opt-in lanes run only by hand

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

# This bash stays the namespace's PID 1 (no exec; the trailing exit stops bash from exec'ing the last command):
# it reaps the orphans tests leave behind, which uv as PID 1 would keep as zombies (kill -0 still finds them).
# shellcheck disable=SC2016 # "$@" expands in the inner bash, not here.
"${chain[@]}" env UV_OFFLINE=1 bash -c 'uv run --offline llmenv.py --help >/dev/null || { echo "uv cannot resolve llmenv.py offline: run make dev-setup once with network" >&2; exit 4; }; nice -n 10 uv run pytest "$@"; exit $?' _ -n "$workers" -q "$@"
