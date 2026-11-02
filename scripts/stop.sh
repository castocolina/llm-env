#!/usr/bin/env bash
# stop.sh — stop the server.
set -euo pipefail
# shellcheck disable=SC1091 # Resolved from this script at runtime.
# shellcheck source=../tools/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/../tools/lib.sh"

case "${1:-}" in
    "") ;;
    llm-server)
        require_cmd podman
        if container_running llm-server; then
            podman stop llm-server >/dev/null
            log_info "llm-server stopped"
        else
            log_warn "llm-server is not running"
        fi
        exit 0
        ;;
    *)
        printf 'usage: stop.sh [llm-server]\n' >&2
        exit 2
        ;;
esac

require_cmd systemctl

if ! systemctl --user list-unit-files "${UNIT_NAME}.service" >/dev/null 2>&1; then
    log_warn "unit ${UNIT_NAME}.service is not installed; nothing to stop"
    exit 0
fi

# Stop the companion even if setup has already disabled System One.
bash "${REPO_DIR}/scripts/phoenix.sh" stop

if systemctl --user is-active --quiet "${UNIT_NAME}.service"; then
    systemctl --user stop "${UNIT_NAME}.service"
    log_info "server stopped"
else
    log_warn "server unit is not active; removing any containers it left running"
    # A failed unit can leave running containers that the --no-recreate ExecStart would never recreate.
    if [ -f "$COMPOSE_FILE" ]; then
        require_cmd podman
        podman compose -f "$COMPOSE_FILE" down --remove-orphans
    fi
fi
