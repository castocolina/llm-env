#!/usr/bin/env bash
# Lifecycle of the isolated Grafana + VictoriaMetrics observe stack.
# Never touches the inference stack, its volumes or the podman-restart service.
set -euo pipefail

# REPO_DIR and CONFIG_PATH come from lib.sh; the generated systemd unit does not inherit them.
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/tools/lib.sh"

PROJECT=llm-env-observe
SECRET=llm-env-observe-grafana-admin
PW_FILE="${XDG_STATE_HOME:-$HOME/.local/state}/llm-env/observe/grafana-admin-password"
INVALID_FILE_MSG="observe-stack: credential file is present but invalid (must be 48 hex chars); restore the original credential or reset the Grafana admin password per README (never delete the Grafana data volume); aborting before generation or start"
MISSING_FILE_MSG="observe-stack: Grafana admin secret exists but the credential file is missing; restore the original credential or reset the Grafana admin password per README (never delete the Grafana data volume); aborting before generation or start"

compose() {
    podman compose --project-name "$PROJECT" --file "$REPO_DIR/docker/observe/compose.yml" "$@"
}

valid_pw() {
    local pw
    [[ -f "$PW_FILE" ]] || return 1
    pw="$(<"$PW_FILE")"
    [[ "$pw" =~ ^[0-9a-f]{48}$ ]]
}

secret_exists() {
    podman secret inspect "$SECRET" >/dev/null 2>&1
}

abort() { printf '%s\n' "$1" >&2; exit 1; }

# Generate the credential only when both the secret and the file are absent.
# Every hard failure happens here, before generation, secret creation or start.
ensure_credential() {
    local have_file=0 have_secret=0 dir tmp pw
    [[ -f "$PW_FILE" ]] && have_file=1
    if secret_exists; then have_secret=1; fi

    if [[ $have_file -eq 1 ]] && ! valid_pw; then
        abort "$INVALID_FILE_MSG"
    fi
    if [[ $have_secret -eq 1 && $have_file -eq 0 ]]; then
        abort "$MISSING_FILE_MSG"
    fi

    if [[ $have_secret -eq 0 ]]; then
        if [[ $have_file -eq 0 ]]; then
            (
                dir="$(dirname "$PW_FILE")"
                mkdir -p "$dir"
                chmod 0700 "$dir"
                tmp="$(mktemp "${dir}/.grafana-admin-password.XXXXXX")"
                trap 'rm -f -- "$tmp"' EXIT  # clean the temp on any failure, including set -e aborts
                pw="$(openssl rand -hex 24)"
                [[ "$pw" =~ ^[0-9a-f]{48}$ ]] || abort "observe-stack: generated an invalid credential; aborting"
                printf '%s\n' "$pw" >"$tmp"
                chmod 0600 "$tmp"
                mv -n -- "$tmp" "$PW_FILE"  # no-overwrite: a concurrent up may have created it
            )
        fi
        podman secret create --ignore "$SECRET" "$PW_FILE"
    fi
}

up() {
    ensure_credential
    compose up -d
    install_user_timer llm-env-observe \
        "Collect llm-env resource and admission metrics" \
        "Run the llm-env metrics collector every 60s" \
        "scripts/observe-publish.sh" \
        "OnActiveSec=5s" \
        "OnUnitActiveSec=60s" \
        "AccuracySec=5s"
    log_info "observe stack is up; Grafana at http://127.0.0.1:3000 (admin credential at $PW_FILE)"
}

down() {
    # Best effort: a missing/inactive unit must not prevent the Compose stop.
    systemctl --user disable --now llm-env-observe.timer 2>/dev/null || true
    systemctl --user stop llm-env-observe.service 2>/dev/null || true
    compose stop
    log_info "observe stack stopped; volumes and secret retained"
}

status() {
    compose ps
    systemctl --user status llm-env-observe.timer --no-pager 2>/dev/null || true
    systemctl --user status llm-env-observe.service --no-pager 2>/dev/null || true
}

case "${1:-}" in
    up) up ;;
    down) down ;;
    status) status ;;
    *) abort "usage: observe-stack.sh up|down|status" ;;
esac
