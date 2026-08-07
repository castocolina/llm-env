#!/usr/bin/env bash
# status.sh — show the service unit's status.
set -euo pipefail
# shellcheck disable=SC1091 # Resolved from this script at runtime.
# shellcheck source=../tools/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/../tools/lib.sh"

systemctl --user status "${UNIT_NAME}.service" --no-pager || true
podman compose -f "$COMPOSE_FILE" ps || true
