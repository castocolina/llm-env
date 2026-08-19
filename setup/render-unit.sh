#!/usr/bin/env bash
# render-unit.sh — generate presets and the Quadlet unit without starting it.
set -euo pipefail
# shellcheck disable=SC1091 # Resolved from this script at runtime.
# shellcheck source=../tools/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/../tools/lib.sh"

require_cmd uv jq yq podman systemctl

[ -f "$CONFIG_PATH" ] || die "no config at ${CONFIG_PATH}; run 'make setup' first"
migrate_config_file || die "configuration migration failed"

backend="$(yq -r '.gpu.backend' "$CONFIG_PATH")"
image="$(yq -r '.gpu.image' "$CONFIG_PATH")"
device_name="$(yq -r '.gpu.device_name' "$CONFIG_PATH")"
load_server_config
# shellcheck disable=SC2153 # PORT is set by load_server_config() in ../tools/lib.sh.
port="$PORT"

# migrate_config_file (above) always normalizes llm_server.enabled to a
# concrete boolean, so this reads it directly -- `// true` would be wrong
# here: jq/yq's `//` treats `false` itself as falsy and would silently
# coerce a disabled config back to enabled.
llm_server_enabled="$(yq -r '.llm_server.enabled' "$CONFIG_PATH")"

presets_path=""
if [ "$llm_server_enabled" = "true" ]; then
    models_max="$(yq -r '.runtime.models_max' "$CONFIG_PATH")"

    [ "$models_max" -gt 0 ] || die "no models enabled; run 'make setup'"

    log_step "Resolving the GPU device"
    device="all"
    if [ "$backend" != "cpu" ] && [ -n "$device_name" ] && [ "$device_name" != "null" ]; then
        listing_file="$(mktemp)"
        podman run --rm --device /dev/dri --entrypoint /app/llama-server \
            "$image" --list-devices >"$listing_file" 2>/dev/null || true
        if resolved="$(llmenv resolve-device --device-name "$device_name" \
                       --listing-file "$listing_file")"; then
            device="$(echo "$resolved" | jq -r '.device')"
            log_info "pinned to ${device} (${device_name})"
        else
            log_warn "could not resolve ${device_name}; offloading to all devices"
        fi
        rm -f "$listing_file"
    fi

    log_step "Generating presets.ini"
    presets_path="${HOME}/.config/llm-env/presets.ini"
    llmenv --config "$CONFIG_PATH" presets \
        --models-dir /models --device "$device" --output "$presets_path" >/dev/null
    log_info "wrote ${presets_path}"

    if [ -f "$presets_path" ]; then
        mkdir -p "$COMPOSE_INSPECT_DIR"
        cp "$presets_path" "${COMPOSE_INSPECT_DIR}/presets.ini"
        log_info "wrote ${COMPOSE_INSPECT_DIR}/presets.ini (inspection copy)"
    fi
else
    log_step "Resolving the GPU device"
    log_info "skipped (llm-server disabled)"
    log_step "Generating presets.ini"
    log_info "skipped (llm-server disabled)"
fi

log_step "Rendering the compose file"
llmenv --config "$CONFIG_PATH" render-compose \
    --models-dir "$MODELS_DIR" --presets-path "$presets_path" \
    --repo-root "$REPO_DIR" --env-file "${REPO_DIR}/.env" \
    --output "$COMPOSE_FILE" >/dev/null
log_info "wrote ${COMPOSE_FILE}"

# Kept alongside the repo (gitignored) so the rendered compose file can be
# inspected or diffed without going to ~/.config/llm-env -- see
# docker-compose.yml.example for the static, annotated shape it follows.
# Guarded on existence: harmless no-op under test stubs that skip the real
# renderer, but always present after a genuine `llmenv render-compose`.
if [ -f "$COMPOSE_FILE" ]; then
    mkdir -p "$COMPOSE_INSPECT_DIR"
    cp "$COMPOSE_FILE" "${COMPOSE_INSPECT_DIR}/docker-compose.yml"
    log_info "wrote ${COMPOSE_INSPECT_DIR}/docker-compose.yml (inspection copy)"
fi

log_step "Rendering the systemd wrapper unit"
mkdir -p "$(dirname "$WRAPPER_UNIT_PATH")"

# A pre-rename version of this project generated a "llm-mdns.service" unit
# that ran a one-shot `avahi-publish -a -R llm.local <ip>` with the IP baked
# in at start time. That's the only thing that ever published the plain
# "llm.local" hostname alias -- the current ${UNIT_NAME}-mdns.service below
# only publishes a _http._tcp service record, a different mDNS mechanism
# that resolves via the host's own real avahi hostname, not a custom alias.
# The legacy unit's record goes stale on any DHCP lease change, but was
# never redundant: retiring it without replacing what it did breaks
# "llm.local" resolution outright (confirmed live). tools/publish-mdns-hostname.sh
# below now owns both jobs, refreshing the alias when the IP actually
# changes instead of publishing it once and going stale.
# Not derived from $UNIT_NAME: this is the literal old, retired name.
legacy_mdns_unit="${HOME}/.config/systemd/user/llm-mdns.service"
if [ -f "$legacy_mdns_unit" ]; then
    systemctl --user stop llm-mdns.service 2>/dev/null || true
    systemctl --user disable llm-mdns.service 2>/dev/null || true
    rm -f "$legacy_mdns_unit"
    log_info "removed legacy llm-mdns.service (superseded by ${UNIT_NAME}-mdns.service, which now also publishes the hostname alias and tracks IP changes)"
fi

mdns_unit="${HOME}/.config/systemd/user/${UNIT_NAME}-mdns.service"
mdns_wants=""
if [ "$llm_server_enabled" = "true" ] && command -v avahi-publish >/dev/null 2>&1; then
    mkdir -p "$(dirname "$mdns_unit")"
    mdns_name="$(yq -r '.server.mdns_name' "$CONFIG_PATH")"
    curl_path="$(command -v curl)"
    publish_script="${REPO_DIR}/tools/publish-mdns-hostname.sh"
    cat > "$mdns_unit" <<EOF
# Generated by render-unit.sh. Publishes only after the router is healthy.
[Unit]
Description=llama.cpp router mDNS publication
Requires=${UNIT_NAME}.service
After=${UNIT_NAME}.service
BindsTo=${UNIT_NAME}.service
PartOf=${UNIT_NAME}.service

[Service]
Type=simple
ExecStartPre=/usr/bin/bash -c 'i=0; while [ \$\$i -lt ${LLM_ENV_HEALTH_TIMEOUT_SECONDS} ]; do ${curl_path} -fsS -o /dev/null http://127.0.0.1:${port}/health && exit 0; i=\$\$((i + 1)); sleep 1; done; exit 1'
ExecStart=/usr/bin/bash ${publish_script} ${mdns_name}.local ${mdns_name} ${port}
Restart=on-failure
RestartSec=2
EOF
    chmod 600 "$mdns_unit"
    mdns_wants="Wants=${UNIT_NAME}-mdns.service"
    log_info "wrote ${mdns_unit}"
else
    rm -f "$mdns_unit"
fi

start_at_boot="$(yq -r '.server.start_at_boot // false' "$CONFIG_PATH")"
if [ "$start_at_boot" = "true" ]; then
    install_section=$'\n[Install]\nWantedBy=default.target'
else
    install_section=""
fi

cat > "$WRAPPER_UNIT_PATH" <<EOF
# Generated by render-unit.sh from ${CONFIG_PATH}. Edits will be overwritten.
[Unit]
Description=llm-env compose stack (${UNIT_NAME})
After=network-online.target
${mdns_wants}

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=$(dirname "$COMPOSE_FILE")
ExecStart=podman compose -f $(basename "$COMPOSE_FILE") up -d
ExecStop=podman compose -f $(basename "$COMPOSE_FILE") down
TimeoutStartSec=300
${install_section}
EOF
log_info "wrote ${WRAPPER_UNIT_PATH}"
chmod 600 "$WRAPPER_UNIT_PATH"
systemctl --user daemon-reload
