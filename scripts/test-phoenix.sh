#!/usr/bin/env bash
# Real, local-only acceptance check: normal Laya endpoint -> Phoenix API -> native UI.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck disable=SC1091
source "$REPO_DIR/tools/lib.sh"
require_cmd curl jq yq podman node mktemp
[ -f "$CONFIG_PATH" ] || die "no config at ${CONFIG_PATH}; run 'make setup' first"
[[ "$(yq -r '.system_one.enabled' "$CONFIG_PATH")" == true ]] || die "System One is disabled; enable it before make test-phoenix"
port="$(yq -r '.system_one.port' "$CONFIG_PATH")"
phoenix_url="$(bash "$REPO_DIR/scripts/phoenix.sh" endpoint)"
curl --noproxy '*' --connect-timeout 2 --max-time 5 -fsS "http://127.0.0.1:${port}/health" >/dev/null \
    || die "System One is not ready at 127.0.0.1:${port}"
curl --noproxy '*' --connect-timeout 2 --max-time 5 -fsS "${phoenix_url}/readyz" >/dev/null \
    || die "Phoenix is not ready at ${phoenix_url}"

run_dir="$(mktemp -d "$REPO_DIR/tmp/phoenix-live-e2e.XXXXXX")"
chmod 700 "$run_dir"
marker="PHOENIX_E2E_$(date +%s)_$(od -An -N8 -tx1 /dev/urandom | tr -d ' \n')"
request="$run_dir/request.json"
response="$run_dir/response.json"
spans="$run_dir/spans.json"
result="$run_dir/result.json"
jq -cn --arg marker "$marker" '{state:{integration_probe:$marker,description:"A harmless local Phoenix history check. The test object is blue and needs no action."},model:"jev-1",questions:{color:{type:"choice",instructions:"What color is the test object?",criteria:{blue:"The object is blue",green:"The object is green"}},severity:{type:"score",instructions:"How urgent is this harmless check?",criteria:["No action is needed","Review soon"]},action_needed:{type:"noul",instructions:"Is any action needed?"}}}' > "$request"
curl --noproxy '*' --connect-timeout 3 --max-time 90 -sS \
    -o "$response" -w '%{http_code}\n' -H 'Content-Type: application/json' \
    --data-binary "@$request" "http://127.0.0.1:${port}/v1/systemone" > "$run_dir/laya-status"
[[ "$(< "$run_dir/laya-status")" == 200 ]] || die "real Laya request failed; evidence retained at $run_dir"
jq -e '.model and ([.answers[]?.type] | sort == ["choice","noul","score"])' "$response" >/dev/null \
    || die "real Laya response did not include Choice/Score/Noul answers; evidence retained at $run_dir"
for attempt in $(seq 1 40); do
    curl --noproxy '*' --connect-timeout 3 --max-time 15 -fsS \
        "${phoenix_url}/v1/projects/laya/spans?limit=1000&sort=id&order=desc" -o "$spans" \
        || die "Phoenix spans API failed; evidence retained at $run_dir"
    if jq -e --arg marker "$marker" '[.data[] | select((.attributes["input.value"] // "") | contains($marker))] | length == 1' \
        "$spans" >/dev/null 2>&1; then break; fi
    sleep 0.25
done
jq -e --arg marker "$marker" '
  [.data[] | select((.attributes["input.value"] // "") | contains($marker))] as $matches |
  ($matches | length) == 1 and
  ($matches[0].attributes["laya.http_status"] == 200) and
  ($matches[0].attributes["laya.completed_answers"] == 3) and
  ($matches[0].attributes["output.value"] | contains("probabilities"))
' "$spans" >/dev/null || die "real Laya trace not found in Phoenix API; evidence retained at $run_dir"
jq -c --arg marker "$marker" '.data[] | select((.attributes["input.value"] // "") | contains($marker)) | {trace_id:.context.trace_id,span_id:.context.span_id}' "$spans" > "$result"
trace_id="$(jq -er '.trace_id' "$result")"
short_tmp="$(mktemp -d "$REPO_DIR/tmp/pb.XXXXXX")"
TMPDIR="$short_tmp" node "$REPO_DIR/tests/phoenix_browser_smoke.cjs" \
    "$phoenix_url" "$trace_id" "$marker" > "$run_dir/browser-status"
cat "$run_dir/browser-status"
printf 'Real Laya -> Phoenix E2E: PASS (project laya; trace %s; evidence %s)\n' "$trace_id" "$run_dir"
