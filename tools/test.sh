#!/usr/bin/env bash
# test.sh — Python test suite.
set -euo pipefail

uv run pytest tests/ -v
