#!/usr/bin/env bash
# test.sh — Python test suite (parallelized across available CPU cores).
set -euo pipefail

uv run pytest tests/ -n auto -v
