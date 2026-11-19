#!/usr/bin/env bash
# Host one-shot: collect resource/admission metrics and publish to VictoriaMetrics.
set -euo pipefail

# REPO_DIR and CONFIG_PATH come from lib.sh; the generated systemd unit does not inherit them.
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/tools/lib.sh"

# Skip quietly when the backend is down: no failing unit, no force flush, no body printed.
if ! curl --noproxy '*' --silent --fail --max-time 2 http://127.0.0.1:8428/health >/dev/null 2>&1; then
    exit 0
fi

timeout --kill-after=5s 45s uv run --project "$REPO_DIR" "$REPO_DIR/llmenv.py" \
    --config "$CONFIG_PATH" observe-metrics --push
