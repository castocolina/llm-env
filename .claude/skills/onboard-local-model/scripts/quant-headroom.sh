#!/usr/bin/env bash
# quant-headroom.sh — can the next quants of the current model fit this GPU, and how do they compare once measured?
# usage: quant-headroom.sh check --alias A [--quants Q5_K_M,Q6_K] [--vram-available-mib N]
#        quant-headroom.sh fetch --alias A --quant Q
#        quant-headroom.sh add-entry --alias A --quant Q
#        quant-headroom.sh record --alias A --candidate B
# check: needs a current capacity card (`llmenv capacity show`). Reads the entry's Hugging Face tree at its commit (an unpinned
# resolve/main/ url is resolved only when the entry's size_bytes equals that file's tree size, else: pin the entry url first),
# writes candidates.json under <out>/model-tune/quant-<YYYYmmddTHHMMSS>-<pid>/ and runs `llmenv capacity headroom`. The fit
# VERDICT of each candidate is the repo's `llmenv budget` on a scratch copy of the config for that candidate (same ctx and KV;
# a file that is not downloaded is stood in for by a sparse file with the current file's GGUF header and the tree's size)
# (removed once its budget is read, and by an EXIT trap when the check is interrupted; budget-<q>/models.yml stays), never
# the measured-VRAM-plus-weight-delta formula; the formula prediction and the measured model VRAM (the card of <A>-<q> when it
# exists) are printed beside it. Candidate path =
# the entry's url path (a sub-folder kept) with its quantization token replaced, case-insensitively, then looked up in the tree in
# any case (the tree's spelling is kept; a quant token is letters, digits, `_` and `-`, as in UD-Q4_K_XL, starting with a letter or digit); its base name is the
# candidate's file, downloaded to the models dir under that name (a base name equal to the entry's file is `quant token not in file
# name`); candidates.json carries `path` only when it differs from `file`. An entry url whose path does not end in its file exits 1.
# A missing candidate is `not in repo`, a -0000N-of-0000M one is `sharded, not supported`. Without --vram-available-mib it reads
# available_mib from `llmenv budget` and refuses while llm-server runs (pass the applied tune-pick row's vram_available_mib, or make stop-llama). Exits 0 also when nothing fits.
# fetch: downloads one candidate file into the models dir through fetch-gguf.sh (verified against the tree's size and lfs.oid; an
# interrupted fetch resumes in its .part; a different file already at the name exits 1 untouched). add-entry: appends a disabled copy of A as alias <A>-<q lowercase> after a backup
# (<config>.pre-quant-<stamp>.bak), validated on a private copy before the rename; refuses when that alias exists.
# record: `llmenv capacity compare`, then one line of deltas against A.
# Env: LLM_ENV_CONFIG, LLM_ENV_MODELS_DIR, LLM_ENV_SCOUT_OUT (output root, default <repo>/tmp), HF_FIXTURE_DIR (tests:
# model-<org>__<repo>.json and tree-<org>__<repo>@<sha>.json instead of the network).
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source-path=SCRIPTDIR
# shellcheck source=../../../../tools/lib.sh
source "$here/../../../../tools/lib.sh"
require_cmd uv jq yq

usage() { sed -n '2,/^set -/{/^set -/!p}' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }
fail_usage() { echo "$1" >&2; exit 2; }

# die_error JSON FALLBACK: dies with llmenv's .error, else the fallback
die_error() { local msg; msg="$(jq -r '.error // empty' <<<"$1" 2>/dev/null || true)"; die "${msg:-$2}"; }

# llmenv_ok ARGS...: runs llmenv on the live config; on failure dies with its JSON .error. Output in $out_json.
llmenv_ok() { out_json=""; out_json="$(llmenv --config "$CONFIG_PATH" "$@")" || die_error "$out_json" "llmenv $1 $2 failed"; }

# hf FIXTURE URL: the fixture directory in tests, the network otherwise
hf() {
    if [ -n "${HF_FIXTURE_DIR:-}" ]; then cat "$HF_FIXTURE_DIR/$1"; else curl -fsS --max-time 60 "$2"; fi
}

quant_re='^[A-Za-z0-9][A-Za-z0-9_-]*$'  # UD-Q4_K_XL; no regex metacharacter, so gsub below matches the token literally

entry() { A="$alias" F="$1" yq -r '.models[] | select(.alias == strenv(A)) | .[strenv(F)] // ""' "$CONFIG_PATH"; }

# load_entry: validates the alias (current card), then sets repo, rev, path, quantization, file from the entry
load_entry() {
    llmenv_ok capacity show --alias "$alias"
    [ -f "$CONFIG_PATH" ] || die "no config at $CONFIG_PATH"
    [ "$(A="$alias" yq -r '[.models[] | select(.alias == strenv(A))] | length' "$CONFIG_PATH")" -gt 0 ] || die "no models.yml entry '$alias'"
    url="$(entry url)"; file="$(entry file)"; quantization="$(entry quantization)"; entry_size="$(entry size_bytes)"
    [[ "$url" =~ ^https://huggingface\.co/([^/]+/[^/]+)/resolve/([^/]+)/(.+)$ ]] || die "entry url is not a Hugging Face resolve url: $url"
    repo="${BASH_REMATCH[1]}"; rev="${BASH_REMATCH[2]}"; path="${BASH_REMATCH[3]}"
    [ "${path##*/}" = "$file" ] || die "entry url path '$path' does not end in its file '$file'; fix the entry's url or file"
    [[ "$quantization" =~ $quant_re ]] || die "entry quantization '$quantization' is not a plain quant token"
}

# resolve_tree: sets sha, params_total and writes $1 (a file) with the repo tree at sha
resolve_tree() {
    local slug="${repo//\//__}" model
    model="$(hf "model-$slug.json" "https://huggingface.co/api/models/$repo")"
    if [[ "$rev" =~ ^[0-9a-f]{40}$ ]]; then
        sha="$rev"
    else
        sha="$(jq -r '.sha // empty' <<<"$model")"
        [[ "$sha" =~ ^[0-9a-f]{40}$ ]] || die "cannot resolve $rev of $repo to a commit"
    fi
    params_total="$(jq -c '.gguf.total // null' <<<"$model")"
    hf "tree-$slug@$sha.json" "https://huggingface.co/api/models/$repo/tree/$sha?recursive=true" >"$1" \
        || die "cannot read the tree of $repo at $sha"
    jq -e 'type == "array"' "$1" >/dev/null 2>&1 || die "the tree of $repo at $sha is not a list"
    if [ "$rev" != "$sha" ]; then
        [ "$(jq -r --arg p "$path" '[.[] | select(.type == "file" and .path == $p)][0].size // empty' "$1")" = "$entry_size" ] \
            || die "$rev of $repo no longer matches size_bytes $entry_size of $file; pin the entry url first"
        echo "resolved $rev to $sha"
    fi
}

# candidate_row TREE QUANT: one candidates.json row for QUANT (or its reason)
candidate_row() {
    jq -nc --slurpfile tree "$1" --arg f "$file" --arg p "$path" --arg tok "$quantization" --arg q "$2" --argjson params "$params_total" '
        ($p | gsub($tok; $q; "i")) as $cp
        | ($tree[0] | map(select(.type == "file")) | (map(select(.path == $cp)) + map(select((.path | ascii_downcase) == ($cp | ascii_downcase)))) | .[0]) as $hit
        | ($hit.path // $cp) as $rp
        | ($rp | sub(".*/"; "")) as $cf
        | if ($cf | ascii_downcase) == ($f | ascii_downcase) then {quant: $q, reason: "quant token not in file name"}
          elif $hit != null then {quant: $q, file: $cf, size_bytes: $hit.size, sha256: ($hit.lfs.oid // null), params_total: $params}
                + (if $rp != $cf then {path: $rp} else {} end)
          elif ($tree[0] | any(.type == "file" and (.path | ascii_downcase | contains($q | ascii_downcase))
                and (.path | test("-[0-9]{5}-of-[0-9]{5}\\.gguf$")))) then {quant: $q, reason: "sharded, not supported"}
          else {quant: $q, reason: "not in repo"} end'
}

# budget_for ROW: prints {required_mib, available_mib, vram_feasible, ram_feasible} from `llmenv budget` on a scratch config
# under $run for the candidate ROW (the live config is never touched); prints nothing when the budget cannot answer.
budget_for() {
    local q cfile csize sd out
    q="$(jq -r .quant <<<"$1")"; cfile="$(jq -r .file <<<"$1")"; csize="$(jq -r .size_bytes <<<"$1")"
    sd="$run/budget-$q"; mkdir -p "$sd/models/$(dirname "$cfile")"
    if [ "$(stat -c %s "$MODELS_DIR/$cfile" 2>/dev/null || echo 0)" = "$csize" ]; then
        ln -sf "$MODELS_DIR/$cfile" "$sd/models/$cfile"
    else
        head -c 67108864 "$MODELS_DIR/$file" >"$sd/models/$cfile" 2>/dev/null && truncate -s "$csize" "$sd/models/$cfile" || return 0
    fi
    A="$alias" F="$cfile" Q="$q" S="$csize" yq '.server.api_key = "redacted" | .omniroute.initial_password = "" | .llm_server.enabled = true
        | (.models[] | select(.alias == strenv(A))) |= (.file = strenv(F) | .quantization = strenv(Q) | .size_bytes = env(S))' \
        "$CONFIG_PATH" >"$sd/models.yml"
    llmenv --config "$sd/models.yml" models select "$alias" >/dev/null 2>&1 || return 0
    out="$(llmenv_budget_json "$sd/models.yml" "$sd/models" 2>/dev/null)" || return 0
    jq -c 'select(type == "object" and (.required_mib? != null) and (.available_mib? != null))
        | {required_mib, available_mib, vram_feasible: (.vram_feasible // false), ram_feasible: (.ram_feasible // false)}' <<<"$out" 2>/dev/null || true
}

check_quant_arg() { [[ "$1" =~ $quant_re ]] || fail_usage "quant '$1' is not a plain quant token"; }

cmd_check() {
    local quants="Q5_K_M,Q6_K" vram="" q row row_q rows budget budget_json tree stamp measured v
    while [ $# -gt 0 ]; do
        case "$1" in
        --alias) alias="${2:-}"; shift 2 ;;
        --quants) quants="${2:-}"; shift 2 ;;
        --vram-available-mib) vram="${2:-}"; shift 2 ;;
        *) fail_usage "unknown flag: $1" ;;
        esac
    done
    [ -n "$alias" ] || fail_usage "--alias is required"
    [ -z "$vram" ] || [[ "$vram" =~ ^[0-9]+$ ]] || fail_usage "--vram-available-mib must be a non-negative integer, got '$vram'"
    IFS=, read -ra qlist <<<"$quants"
    for q in "${qlist[@]}"; do check_quant_arg "$q"; done
    load_entry
    if [ -z "$vram" ]; then
        if container_running llm-server; then
            die "llm-server holds VRAM; pass --vram-available-mib from the applied tune-pick row or run make stop-llama"
        fi
        budget_json="$(llmenv_budget_json "$CONFIG_PATH" "$MODELS_DIR")" || exit 1
        vram="$(jq -r '.available_mib // empty' <<<"$budget_json" 2>/dev/null || true)"
        [[ "$vram" =~ ^[0-9]+$ ]] || die "llmenv budget has no available_mib: $budget_json"
    fi
    stamp="$(date +%Y%m%dT%H%M%S)-$$"
    run="${LLM_ENV_SCOUT_OUT:-$REPO_DIR/tmp}/model-tune/quant-$stamp"
    mkdir -p "$run"
    trap 'rm -rf "$run"/budget-*/models' EXIT # the stand-ins, also on abort; rm drops a symlink budget_for made, never its target
    tree="$run/tree.json"
    resolve_tree "$tree"
    row="$(jq -nc --slurpfile tree "$tree" --arg p "$path" '$tree[0] | map(select(.type == "file" and .path == $p)) | .[0] // empty')"
    [ -n "$row" ] || die "$file is not in the tree of $repo at $sha"
    rows="$(for q in "${qlist[@]}"; do
        row_q="$(candidate_row "$tree" "$q")"
        if jq -e 'has("size_bytes")' <<<"$row_q" >/dev/null; then
            budget="$(budget_for "$row_q")"
            [ -z "$budget" ] || row_q="$(jq -c --argjson b "$budget" '. + {budget: $b}' <<<"$row_q")"
        fi
        rm -rf "$run/budget-$q/models" # per quant: a budget subshell that outlives an aborted check leaves nothing behind
        printf '%s\n' "$row_q"
    done | jq -sc .)"
    jq -n --arg repo "$repo" --arg sha "$sha" --argjson params "$params_total" --argjson row "$row" --arg q "$quantization" \
        --arg f "$file" --argjson rows "$rows" '
        {repo: $repo, sha: $sha, params_total: $params,
         current: {quant: $q, file: $f, size_bytes: $row.size, sha256: ($row.lfs.oid // null), params_total: $params},
         candidates: $rows}' >"$run/candidates.json"
    llmenv_ok capacity headroom --alias "$alias" --candidates "$run/candidates.json" --vram-available-mib "$vram"
    measured="$(for q in "${qlist[@]}"; do
        v="$(llmenv --config "$CONFIG_PATH" capacity show --alias "$alias-$(tr '[:upper:]' '[:lower:]' <<<"$q")" 2>/dev/null \
            | jq -r '.depth.model_vram_mib // empty' 2>/dev/null || true)"
        [ -z "$v" ] || jq -nc --arg q "$q" --argjson v "$v" '{($q): $v}'
    done | jq -sc 'add // {}')"
    echo "candidates: $run/candidates.json"
    jq -r --argjson m "$measured" '(.quants.candidates // [])[]
        | "\(.quant): " + (if .fits == true then "fits" elif .fits == false then "does not fit" else "unknown" end)
          + " (\(.reason // "no reason"))"
          + (if .delta_mib != null then ", formula predicted \(.predicted_vram_mib) MiB (delta \(.delta_mib) MiB)" else "" end)
          + (if $m[.quant] != null then ", measured \($m[.quant]) MiB" else "" end)' <<<"$out_json"
    jq -r '"verdict: " + (.quants.verdict // "none")' <<<"$out_json"
}

# fetch_row: sets $row for --quant, dying on a reason-only candidate
fetch_row() {
    tree="$(mktemp)"; trap 'rm -f "$tree"' EXIT
    resolve_tree "$tree"
    row="$(candidate_row "$tree" "$quant")"
    ! jq -e 'has("reason")' <<<"$row" >/dev/null || die "$quant: $(jq -r .reason <<<"$row")"
}

parse_quant_args() {
    quant=""
    while [ $# -gt 0 ]; do
        case "$1" in
        --alias) alias="${2:-}"; shift 2 ;;
        --quant) quant="${2:-}"; shift 2 ;;
        *) fail_usage "unknown flag: $1" ;;
        esac
    done
    [ -n "$alias" ] && [ -n "$quant" ] || fail_usage "--alias and --quant are required"
    check_quant_arg "$quant"
}

cmd_fetch() {
    local cfile cpath size oid
    parse_quant_args "$@"
    load_entry
    fetch_row
    cfile="$(jq -r .file <<<"$row")"; cpath="$(jq -r '.path // .file' <<<"$row")"; size="$(jq -r .size_bytes <<<"$row")"; oid="$(jq -r '.sha256 // empty' <<<"$row")"
    [ -n "$oid" ] || die "$cfile has no lfs.oid in the tree; cannot verify a download"
    bash "$here/fetch-gguf.sh" --url "https://huggingface.co/$repo/resolve/$sha/$cpath" --dest "$MODELS_DIR/$cfile" --size "$size" --sha256 "$oid"
}

# add_copy WORK: appends the disabled copy of entry $A as $B (env set by cmd_add_entry) to the private config copy WORK
add_copy() {
    yq -i '.models += [(.models[] | select(.alias == strenv(A)) | .alias = strenv(B) | .label = strenv(L)
        | .quantization = strenv(Q) | .file = strenv(F) | .url = strenv(U) | .size_bytes = env(S) | .enabled = false | del(.benchmark))]' "$1"
}

cmd_add_entry() {
    local new qlower cfile cpath size label
    parse_quant_args "$@"
    load_entry
    qlower="$(tr '[:upper:]' '[:lower:]' <<<"$quant")"; new="$alias-$qlower"
    if [ "$(B="$new" yq -r '[.models[] | select(.alias == strenv(B))] | length' "$CONFIG_PATH")" -gt 0 ]; then
        die "alias $new already exists in models.yml; use it or restore the backup"
    fi
    fetch_row
    cfile="$(jq -r .file <<<"$row")"; cpath="$(jq -r '.path // .file' <<<"$row")"; size="$(jq -r .size_bytes <<<"$row")"; label="$(entry label) ($quant)"
    trap 'rm -f "$tree"' EXIT
    export A="$alias" B="$new" L="$label" Q="$quant" F="$cfile" U="https://huggingface.co/$repo/resolve/$sha/$cpath" S="$size"
    edit_config_atomically pre-quant add_copy   # prints `backup: <path>`, validates the result, renames atomically
    echo "alias: $new"
}

cmd_record() {
    local cand=""
    while [ $# -gt 0 ]; do
        case "$1" in
        --alias) alias="${2:-}"; shift 2 ;;
        --candidate) cand="${2:-}"; shift 2 ;;
        *) fail_usage "unknown flag: $1" ;;
        esac
    done
    [ -n "$alias" ] && [ -n "$cand" ] || fail_usage "--alias and --candidate are required"
    llmenv_ok capacity compare --alias "$alias" --candidate "$cand"
    jq -r --arg b "$cand" '
        def d: if . == null then "n/a" elif . > 0 then "+\(.)" else "\(.)" end;
        ([(.quants.candidates // [])[] | select(.measured.alias == $b)] | .[0]) as $c
        | if $c == null then "no measured block for \($b) in the compare output"
          else $c.measured as $m | "\($c.quant): tg@\($m.ref_depth) \($m.delta.tg_at_ref | d), pp@\($m.ref_depth) \($m.delta.pp_at_ref | d), VRAM \($m.delta.model_vram_mib | d) MiB, ladder \($m.delta.highest_layer | d), score \($m.delta.overall_score | d)" end' <<<"$out_json"
}

alias=""; tree=""
sub="${1:-}"
case "$sub" in
-h|--help|"") usage; [ -n "$sub" ] || exit 2; exit 0 ;;
check|fetch|add-entry|record) shift ;;
*) fail_usage "unknown subcommand: $sub (check|fetch|add-entry|record)" ;;
esac
for a in "$@"; do [ "$a" != -h ] && [ "$a" != --help ] || { usage; exit 0; }; done
case "$sub" in
check) cmd_check "$@" ;;
fetch) cmd_fetch "$@" ;;
add-entry) cmd_add_entry "$@" ;;
record) cmd_record "$@" ;;
esac
