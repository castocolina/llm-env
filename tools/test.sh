#!/usr/bin/env bash
# test.sh — Python test suite on LLM_ENV_TEST_CPU_PCT % of the CPUs (default 40), niced, quiet.
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

nice -n 10 uv run pytest -n "$workers" -q "$@"
