#!/usr/bin/env bash
# refresh-llm-env-pi-profile.sh -- keep the legacy Pi profile ~/.config/llm-env/pi (provider `llm-env`, used by
# check-with-agents and effort-run.sh) in step with the enabled local models of models.yml.
# It rewrites only .providers["llm-env"].models in an existing models.json (every other key and the file mode stay), and
# does nothing when the file is absent, llm_server.enabled is false, or the file is not the expected JSON.
# Env: LLM_ENV_PI_PROFILE_DIR overrides the profile directory.
set -euo pipefail
# shellcheck disable=SC1091 # Resolved from this script at runtime.
# shellcheck source=../tools/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/../tools/lib.sh"

profile="${LLM_ENV_PI_PROFILE_DIR:-$HOME/.config/llm-env/pi}/models.json"
[ -f "$profile" ] || exit 0
[ "$(yq -r '.llm_server.enabled' "$CONFIG_PATH")" = "true" ] || exit 0
jq -e '.providers["llm-env"] | type == "object"' "$profile" >/dev/null 2>&1 \
    || { log_warn "${profile} has no llm-env provider; left unchanged"; exit 0; }

models="$(yq -o=json '[.models[] | select(.enabled) | {"id": .alias}]' "$CONFIG_PATH")"
tmp="$(mktemp "${profile}.XXXXXX")"
trap 'rm -f "$tmp"' EXIT
jq --argjson models "$models" '.providers["llm-env"].models = $models' "$profile" >"$tmp"
chmod --reference="$profile" "$tmp"
mv -f "$tmp" "$profile"
log_info "refreshed ${profile}: $(jq -r '[.[].id] | join(", ")' <<<"$models")"
