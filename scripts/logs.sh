#!/usr/bin/env bash
# logs.sh — follow the service unit's logs.
set -euo pipefail
# shellcheck disable=SC1091 # Resolved from this script at runtime.
# shellcheck source=../tools/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/../tools/lib.sh"

journalctl --user -u "${UNIT_NAME}.service" -f
