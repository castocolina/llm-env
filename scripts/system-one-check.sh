#!/usr/bin/env bash
# system-one-check.sh — read-only: compare the System One (Laya) image pins in docker/system-one/Dockerfile with what is
# newest upstream. Prints one JSON object: pinned versions, latest Laya on PyPI and how many releases behind, release/fix/
# feature counts since the pin and the state of every PR/issue the Dockerfile waits on (via gh), and the newest torch
# wheel per ROCm index, and whether the reviewed weight revisions (laya/revisions.py PINNED_REVISIONS, which
# LAYA_REVISION=reviewed loads) differ between the pinned and the latest release, and which installed dependencies
# (base + the `serve` extra the image installs) the latest release adds or drops: a new hard dependency can switch on
# new behavior by default. Changes nothing but --save's file.
# usage: system-one-check.sh [--save]   --save also writes the report, stamped, to
#   ${XDG_STATE_HOME:-~/.local/state}/llm-env/system-one-check.json (read by make status; the weekly timer runs this).
#   SO_FIXTURE_DIR=<dir> reads pypi.json, torch-index.html and, when present, pypi-<version>.json,
#   revisions-pinned.py and revisions-latest.py instead of the network, and skips gh.
set -euo pipefail
case "${1:-}" in -h|--help) sed -n '2,/^set -/s/^# \{0,1\}//p' "${BASH_SOURCE[0]}"; exit 0 ;; esac
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
dockerfile="${SO_DOCKERFILE:-$here/../docker/system-one/Dockerfile}"
[ -f "$dockerfile" ] || { echo "no Dockerfile at $dockerfile" >&2; exit 2; }
arg() { sed -n "s/^ARG $1=\(.*\)$/\1/p" "$dockerfile" | head -1; }
laya="$(arg LAYA_VERSION)"; torch="$(arg TORCH_VERSION)"; index="$(arg TORCH_INDEX)"
fetch() { if [ -n "${SO_FIXTURE_DIR:-}" ]; then cat "$SO_FIXTURE_DIR/$1"; else curl -fsS --compressed --max-time 30 "$2"; fi; }

versions="$(fetch pypi.json https://pypi.org/pypi/laya/json \
    | jq -r '.releases | to_entries[] | select((.value | length) > 0 and (.value[0].yanked | not)) | .key' | sort -V)"
latest="$(tail -1 <<<"$versions")"
after="$(awk -v p="$laya" 'f {print} $0 == p {f = 1}' <<<"$versions" | jq -Rn '[inputs]')"

# Installed dependency names of one release: no marker, or only the `serve` extra. null when unreadable.
deps() {
    { if [ -n "${SO_FIXTURE_DIR:-}" ]; then cat "$SO_FIXTURE_DIR/pypi-$1.json" 2>/dev/null
      else curl -fsS --compressed --max-time 30 "https://pypi.org/pypi/laya/$1/json" 2>/dev/null; fi; } \
        | jq -c '[.info.requires_dist[]? | select((test(";") | not) or test("^[^;]*; *extra == \"serve\" *$"))
                  | capture("^(?<n>[A-Za-z0-9_.-]+)").n | ascii_downcase | gsub("[-_.]+"; "-")] | unique' 2>/dev/null || true
}
dep_pinned="$(deps "$laya")"; dep_latest="$(deps "$latest")"
dependencies=null
if [ -n "$dep_pinned" ] && [ -n "$dep_latest" ]; then
    dependencies="$(jq -nc --argjson a "$dep_pinned" --argjson b "$dep_latest" '{added: ($b - $a), removed: ($a - $b)}')"
fi

# newest cp311 wheel per local tag (rocm7.2, rocm7.14, rocm10.0, ...): the image is python 3.11; sorted ascending, last wins
torch_latest="$(fetch torch-index.html https://download.pytorch.org/whl/torch/ \
    | grep -o 'torch-[0-9][0-9.]*%2B[a-z0-9.]*-cp311-cp311-manylinux[^"]*x86_64\.whl' \
    | sed -E 's/^torch-([0-9.]+)%2B([a-z0-9.]+)-.*/\2 \1/' | grep -E '^rocm' | sort -k1,1 -k2,2V \
    | jq -Rn '[inputs | split(" ") | {(.[0]): .[1]}] | add')"

# GitHub holds the release notes (PyPI lists only the Hugging Face page) and the upstream PRs/issues the Dockerfile comment
# waits on. Skipped offline (fixtures) or without gh.
gh_repo="${SO_GH_REPO:-NandhaKishorM/laya}"   # shortcut: found by search on 2026-10-30; override with SO_GH_REPO
releases=null; pin_watch='[]'; rev_pinned=""; rev_latest=""
pins() { grep -oE '"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+": *"[0-9a-f]{40}"' | tr -d ' ' | sort; }
if [ -n "${SO_FIXTURE_DIR:-}" ]; then
    if [ -f "$SO_FIXTURE_DIR/revisions-pinned.py" ] && [ -f "$SO_FIXTURE_DIR/revisions-latest.py" ]; then
        rev_pinned="$(pins <"$SO_FIXTURE_DIR/revisions-pinned.py")"; rev_latest="$(pins <"$SO_FIXTURE_DIR/revisions-latest.py")"
    fi
elif command -v gh >/dev/null; then
    raw() { gh api -H "Accept: application/vnd.github.raw" "repos/$gh_repo/contents/laya/revisions.py?ref=v$1" 2>/dev/null | pins; }
    rev_pinned="$(raw "$laya" || true)"; rev_latest="$(raw "$latest" || true)"
fi
if [ -z "${SO_FIXTURE_DIR:-}" ] && command -v gh >/dev/null; then
    releases="$(gh api "repos/$gh_repo/releases?per_page=60" --jq '[.[] | {tag: (.tag_name | ltrimstr("v")),
        fix: ([.body | scan("(?m)^\\* fix")] | length), feat: ([.body | scan("(?m)^\\* feat")] | length),
        perf: ([.body | scan("(?m)^\\* perf")] | length)}]' 2>/dev/null || echo null)"
    refs="$( { grep -oE 'github\.com/[^/ ]+/[^/ ]+/(issues|pull)/[0-9]+' "$dockerfile" | sed -E 's#github\.com/##; s#/(issues|pull)/#:#'
               grep -oE 'rocm-systems PR #[0-9]+' "$dockerfile" | sed -E 's#rocm-systems PR \##ROCm/rocm-systems:#'; } | sort -u)"
    pin_watch="$(while IFS=: read -r repo_path num; do
        [ -n "$num" ] && gh api "repos/$repo_path/issues/$num" --jq '{ref: "'"$repo_path#$num"'", title, state, merged_at: (.pull_request.merged_at // null), closed_at}' 2>/dev/null
    done <<<"$refs" | jq -s '.' 2>/dev/null || echo '[]')"
fi

# Repos whose reviewed SHA differs (or appears/disappears) between the two releases; null when either side is unread.
weights=null
if [ -n "$rev_pinned" ] && [ -n "$rev_latest" ]; then
    weights="$(comm -3 <(echo "$rev_pinned") <(echo "$rev_latest") | tr -d '\t' | cut -d: -f1 | tr -d '"' | sort -u \
        | jq -Rn '[inputs | select(length > 0)] | {reviewed_revisions_changed: (length > 0), repos: .}')"
fi

report="$(jq -n --arg laya "$laya" --arg torch "$torch" --arg index "$index" --arg latest "$latest" --argjson after "$after" \
    --argjson torch_latest "${torch_latest:-null}" --argjson releases "${releases:-null}" --argjson pin_watch "${pin_watch:-[]}" \
    --arg gh_repo "$gh_repo" --argjson weights "$weights" --argjson dependencies "$dependencies" '
{pinned: {laya: $laya, torch: $torch, torch_index: $index},
 latest: {laya: $latest, releases_behind: ($after | length)},
 github: {repo: $gh_repo,
          since_pin: (if $releases == null then null else
              ([$releases[] | select(.tag as $t | $after | index($t))]
               | {releases: length, fixes: (map(.fix) | add // 0), features: (map(.feat) | add // 0), perf: (map(.perf) | add // 0)}) end)},
 weights: $weights,
 dependencies: $dependencies,
 torch_latest_per_rocm_index: $torch_latest,
 pin_watch: $pin_watch}')"
printf '%s\n' "$report"
if [ "${1:-}" = "--save" ]; then
    state="${XDG_STATE_HOME:-$HOME/.local/state}/llm-env"
    mkdir -p "$state"
    jq --arg at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" '. + {checked_at: $at}' <<<"$report" >"$state/system-one-check.json.tmp"
    mv "$state/system-one-check.json.tmp" "$state/system-one-check.json"
fi
