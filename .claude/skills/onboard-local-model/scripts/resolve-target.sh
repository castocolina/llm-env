#!/usr/bin/env bash
# resolve-target.sh — classify the one model the owner named and print one JSON object.
# usage: resolve-target.sh MODEL [--scratch-config FILE]
# MODEL, first match wins (a leading ~ or ~/ is first expanded to $HOME: a quoted path arrives unexpanded, and a leading file:// is dropped): (1) an existing .gguf file (must start with the bytes GGUF) -> gguf_file; (2) an http(s) Hugging Face URL (a scheme-less huggingface.co/... or hf.co/... argument that is not an existing path counts as https://)
# (huggingface.co or hf.co) -> hf_file for resolve|blob/<rev>/<path>.gguf (query string dropped; a refs/pr/N revision becomes refs%2Fpr%2FN), a resolve|blob URL of any other
# file exits 2, else hf_repo; (3) an alias in models.yml -> alias; (4) any other .gguf argument -> exit 2 "not found" (never a
# repo id), with the closest .gguf of its directory (a common prefix of 3+ characters, else within 2 edits), or of the models dir for a bare name, as a hint; (4b) a path-like argument
# (starts with /, ./, ../ or ~, holds two or more /, exists, has an existing directory as its first segment, or ends in a .gg*
# extension) -> exit 2 "not a .gguf file" with the same hint;
# (5) ORG/NAME -> hf_repo; (5b) a bare name that matches an alias case- and separator-insensitively, starts one (3+ characters) or is within 2 edits
# of one with the same digits (Levenshtein on the lowercased letters and digits), exits 2 naming up to 3 aliases; a bare name whose <models dir>/<name>.gguf exists exits 2 naming it; (6) exit 3: a bare model name,
# to search on Hugging Face. A trailing `:<quant>` (hf-scout.sh's quant regex, any case) or `:latest` after an ORG/NAME or a Hugging Face URL
# that is not an existing path is split off first. The result must be hf_repo, which carries it as `quant` (`latest`: null); any other kind exits 2.
# Every other failure (a missing .gguf, a path-like argument, an unsupported or non-model URL,
# --scratch-config misuse, usage) exits 2.
# Output: {kind: alias, alias, file, enabled, present} | {kind: hf_repo, repo, quant (the suffix, else null)} | {kind: hf_file, repo, revision, file, quant}
#         | {kind: gguf_file, path, size_bytes, in_models_dir (true only for a file directly in the models dir), quant}. quant is hf-scout.sh's quant regex on the file name, else null.
# --scratch-config FILE (alias and gguf_file only; FILE must resolve under <repo>/tmp/, exit 2 otherwise): writes tune-sweep.sh's
# redacted copy of models.yml, for gguf_file adds one entry `candidate` copied from the first enabled entry (else the first),
# without its n_cpu_moe, sampling and benchmark, with file, size_bytes, quantization (the file's token, else unknown), parameters
# unknown and url file://<path> set, then runs `llmenv models select <alias|candidate>` on it (exit 1 with
# llmenv's error, FILE removed), only then sets llm_server.enabled true (so a gateway-only file with every model disabled works),
# and adds "scratch_config" (absolute FILE) to the JSON. An existing alias `candidate` exits 2.
# Env: LLM_ENV_CONFIG, LLM_ENV_MODELS_DIR. The live models.yml is only read; FILE is the only file written. Needs jq and yq.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source-path=SCRIPTDIR
# shellcheck source=../../../../tools/lib.sh
source "$here/../../../../tools/lib.sh"
# shellcheck source-path=SCRIPTDIR
# shellcheck source=require-tmp-out.sh
source "$here/require-tmp-out.sh"

model=""; scratch=""
while [ $# -gt 0 ]; do
    case "$1" in
    --scratch-config) scratch="${2:-}"; [ -n "$scratch" ] || { echo "--scratch-config needs a file" >&2; exit 2; }; shift 2 ;;
    -h|--help) sed -n '2,/^set -/{/^set -/!p}' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*) echo "unknown flag: $1" >&2; exit 2 ;;
    *) [ -z "$model" ] || { echo "one MODEL only" >&2; exit 2; }; model="$1"; shift ;;
    esac
done
# a quoted ~ arrives unexpanded; expand it as the shell would for the current user (never ~user)
case "$model" in \~|\~/*) model="$HOME${model:1}" ;; esac
# shortcut: a file:// url's percent-escapes stay encoded; decode them when a pasted url ever carries one
case "$model" in file://*) model="${model#file://}" ;; esac
# a scheme-less huggingface.co/ or hf.co/ argument that is not an existing path is a URL
shopt -s nocasematch
[ -e "$model" ] || case "$model" in huggingface.co/*|hf.co/*) model="https://$model" ;; esac
shopt -u nocasematch
[ -n "$model" ] || { echo "MODEL is required" >&2; exit 2; }
require_cmd jq yq
[ -z "$scratch" ] || require_tmp_out "$scratch"

repo_re='^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$'
forms="MODEL is none of the four accepted forms: a models.yml alias, a Hugging Face repo id (ORG/NAME) or URL (huggingface.co, hf.co), or a path to a .gguf file"
fail() { echo "$1" >&2; exit 2; }
is_gguf_name() { [[ "${1,,}" == *.gguf ]]; }
# the quant token hf-scout.sh uses, taken from the file name; null when absent
quant_of() { jq -cn --arg f "${1##*/}" '$f | capture("(?<q>(UD-)?(I?Q[0-9][A-Za-z0-9_]*|BF16|F16|F32))"; "i").q // null'; }
# Levenshtein distance, shared by the .gguf typo hint and the alias near-miss (both jq programs)
# shellcheck disable=SC2016 # the $-names belong to jq
lev_jq='def lev($a; $b): ($a | explode) as $s | ($b | explode) as $t
  | reduce range(0; $s | length) as $i ([range(0; ($t | length) + 1)];
      . as $p | reduce range(0; $t | length) as $j ([$i + 1];
        . + [[$p[$j + 1] + 1, .[$j] + 1, $p[$j] + (if $s[$i] == $t[$j] then 0 else 1 end)] | min]))
  | .[-1];'
# closest_gguf DIR NAME: the *.gguf in DIR (empty = cwd) with the longest common case-insensitive prefix (>= 3) with NAME, else
# within 2 edits of it (stems); ties to the first in C sort order; an empty NAME takes the first .gguf. Prints DIR/file (the bare file for an empty DIR) or nothing.
closest_gguf() {
    local dir="$1" name="${2,,}" best="" top=2 f low n list
    list="$(find -H "${dir:-.}" -maxdepth 1 -xtype f -iname '*.gguf' -printf '%f\n' 2>/dev/null | LC_ALL=C sort)"
    while IFS= read -r f; do
        [ -n "$name" ] || { best="$f"; break; }
        low="${f,,}"; n=0
        while [ "$n" -lt "${#name}" ] && [ "${low:n:1}" = "${name:n:1}" ]; do n=$((n + 1)); done
        [ "$n" -le "$top" ] || { best="$f"; top="$n"; }
    done <<<"$list"
    if [ -z "$best" ] && [ -n "$name" ]; then
        best="$(jq -nRr --arg n "${name%.gguf}" "$lev_jq"'
            [inputs | select(length > 0) | {f: ., d: lev(ascii_downcase | sub("\\.gguf$"; ""); $n)} | select(.d <= 2)]
            | sort_by(.d) | (.[0].f // empty)' <<<"$list")"
    fi
    [ -z "$best" ] || echo "${dir:+${dir%/}/}$best"
}

# llama.cpp -hf and Ollama put a quant after the repo (org/repo:Q4_K_M, hf.co/org/repo:q4_k_m); :latest is Ollama's default tag
qsel=""; tag=""
shopt -s nocasematch
if [ ! -e "$model" ] && [[ "$model" =~ ^(.+):((UD-)?(I?Q[0-9][A-Za-z0-9_]*|BF16|F16|F32)|latest)$ ]]; then
    base="${BASH_REMATCH[1]}" t="${BASH_REMATCH[2]}"
    if [[ "$base" =~ $repo_re || "$base" =~ ^https?://(huggingface\.co|hf\.co)/ ]]; then
        model="$base"; tag="$t"; [[ "$t" == latest ]] || qsel="$t"
    fi
fi
shopt -u nocasematch

kind=""; result=""
if [ -f "$model" ] && is_gguf_name "$model"; then
    [ "$(head -c4 "$model")" = GGUF ] || fail "$model is not a GGUF file (it does not start with the bytes GGUF)"
    abs="$(readlink -f "$model")"; size="$(stat -Lc %s "$model")"
    case "$(dirname "$abs")" in "$(readlink -f "$MODELS_DIR")") inside=true ;; *) inside=false ;; esac
    kind=gguf_file
    result="$(jq -cn --arg p "$abs" --argjson s "$size" --argjson i "$inside" --argjson q "$(quant_of "$abs")" \
        '{kind: "gguf_file", path: $p, size_bytes: $s, in_models_dir: $i, quant: $q}')"
elif [[ "$model" =~ ^https?://([^/?#]+)([^?#]*) ]]; then
    host="${BASH_REMATCH[1],,}"; path="${BASH_REMATCH[2]}"
    case "$host" in huggingface.co|hf.co) ;; *) fail "unsupported host '$host'. $forms" ;; esac
    case "${path#/}" in
    datasets|datasets/*|spaces|spaces/*|docs|docs/*|api|api/*|collections|collections/*|papers|papers/*|blog|blog/*|models|models/*|organizations|organizations/*|settings|settings/*|new|new/*)
        fail "not a Hugging Face model URL: $model. $forms" ;;
    esac
    if [[ "$path" =~ ^/([^/]+/[^/]+)/(resolve|blob)/([^/]+)/(.+)$ ]]; then
        is_gguf_name "${BASH_REMATCH[4]}" || fail "not a .gguf file URL: $model. $forms"
        repo="${BASH_REMATCH[1]}"; rev="${BASH_REMATCH[3]}"; file="${BASH_REMATCH[4]}"
        [[ "$repo" =~ $repo_re ]] || fail "not a Hugging Face repo URL: $model. $forms"
        # a pull-request ref holds slashes; hf-scout.sh --revision takes it URL-encoded, as the Hub's web URLs show it
        if [[ "$rev/$file" =~ ^refs/pr/([0-9]+)/(.+)$ ]]; then rev="refs%2Fpr%2F${BASH_REMATCH[1]}"; file="${BASH_REMATCH[2]}"; fi
        kind=hf_file
        result="$(jq -cn --arg r "$repo" --arg v "$rev" --arg f "$file" --argjson q "$(quant_of "$file")" \
            '{kind: "hf_file", repo: $r, revision: $v, file: $f, quant: $q}')"
    elif [[ "$path" =~ ^/([^/]+/[^/]+)(/.*)?$ ]] && repo="${BASH_REMATCH[1]}" && [[ "$repo" =~ $repo_re ]]; then
        kind=hf_repo; result="$(jq -cn --arg r "$repo" '{kind: "hf_repo", repo: $r}')"
    else
        fail "not a Hugging Face repo URL: $model. $forms"
    fi
fi
if [ -z "$kind" ] && [ -f "$CONFIG_PATH" ]; then
    entry="$(A="$model" yq -o=json -I0 '(.models // [])[] | select(.alias == strenv(A)) | {"file": .file, "enabled": (.enabled // false)}' "$CONFIG_PATH" | head -n1)"
    if [ -n "$entry" ]; then
        file="$(jq -r .file <<<"$entry")"
        kind="alias"
        result="$(jq -c --arg a "$model" --argjson p "$([ -f "$MODELS_DIR/$file" ] && echo true || echo false)" \
            '{kind: "alias", alias: $a, file: .file, enabled: .enabled, present: $p}' <<<"$entry")"
    fi
fi
if [ -z "$kind" ]; then
    p="$model"
    if [ -d "$p" ]; then dir="${p%/}"; dir="${dir:-/}"; name=""
    elif [[ "$p" == */* ]]; then dir="${p%/*}"; dir="${dir:-/}"; name="${p##*/}"
    else dir=""; name="$p"; fi
    hint="$(closest_gguf "$dir" "$name")"
    [ -n "$dir" ] || [ -n "$hint" ] || hint="$(closest_gguf "$MODELS_DIR" "$name")"
    is_gguf_name "$model" && fail "$model: not found (a .gguf argument must be an existing file${hint:+; did you mean $hint?})"
    if [[ "$model" == /* || "$model" == ./* || "$model" == ../* || "$model" == "~"* || "$model" == */*/* || "${model,,}" =~ \.gg[^./]*$ ]] \
        || [ -e "$p" ] || { [[ "$model" =~ $repo_re ]] && [ -d "${model%%/*}" ]; }; then
        say="no similar .gguf in ${dir:-.}"; [ -z "$hint" ] || say="did you mean $hint?"
        fail "$model: not a .gguf file ($say); pass a Hugging Face repo as its https://huggingface.co/ URL"
    fi
    if ! [[ "$model" =~ $repo_re ]]; then
        if [ -f "$CONFIG_PATH" ]; then
            near="$(yq -o=json -I0 '[(.models // [])[].alias]' "$CONFIG_PATH" | jq -r --arg m "$model" "$lev_jq"'
                def norm: ascii_downcase | gsub("[^a-z0-9]"; "");
                ($m | norm) as $n
                | [.[] | strings | select(($n | length) > 0 and (norm == $n or (($n | length) >= 3 and (norm | startswith($n)))
                    or ((norm | gsub("[^0-9]"; "")) == ($n | gsub("[^0-9]"; "")) and lev(norm; $n) <= 2)))] | sort | .[:3]
                | if length > 1 then (.[:-1] | join(", ")) + " or " + .[-1] else join("") end')"
            [ -z "$near" ] || fail "$model: no models.yml alias of that name; did you mean alias $near? (if not, the owner can ask for a Hugging Face search by name)"
        fi
        [ ! -f "$MODELS_DIR/$model.gguf" ] || fail "$model: no models.yml alias of that name; did you mean $MODELS_DIR/$model.gguf?"
        echo "$model: $forms; search it as a model name (exit 3)" >&2; exit 3
    fi
    kind=hf_repo; result="$(jq -cn --arg r "$model" '{kind: "hf_repo", repo: $r}')"
fi
[ -z "$tag" ] || [ "$kind" = hf_repo ] || fail "$model:$tag: a :<quant> suffix only selects a quant of a Hugging Face repo, and $model resolves to kind $kind"
[ "$kind" != hf_repo ] || result="$(jq -c --arg q "$qsel" '. + {quant: (if $q == "" then null else $q end)}' <<<"$result")"

if [ -n "$scratch" ]; then
    case "$kind" in alias|gguf_file) ;; *) fail "--scratch-config needs an alias or a local .gguf file, not a Hugging Face target ($kind)" ;; esac
    [ -f "$CONFIG_PATH" ] || fail "--scratch-config needs the models.yml at $CONFIG_PATH"
    target="$model"
    if [ "$kind" = gguf_file ]; then
        target=candidate
        [ "$(A=candidate yq '[(.models // [])[] | select(.alias == strenv(A))] | length' "$CONFIG_PATH")" = 0 ] \
            || fail "$CONFIG_PATH already has an alias 'candidate'; rename or remove it first"
    fi
    mkdir -p "$(dirname "$scratch")"
    trap 'rm -f "$scratch"' ERR
    # llm_server.enabled flips only after select: llmenv rejects zero enabled models while llm-server is enabled (gateway-only file)
    yq '.server.api_key = "redacted" | .omniroute.initial_password = ""' "$CONFIG_PATH" >"$scratch"
    if [ "$kind" = gguf_file ]; then
        # shellcheck disable=SC2016 # the $-names belong to yq
        F="$(jq -r '.path | split("/") | last' <<<"$result")" S="$(jq -r .size_bytes <<<"$result")" U="file://$(jq -r .path <<<"$result")" \
            Q="$(jq -r '.quant // ""' <<<"$result")" yq -i '
            (([.models[] | select(.enabled == true)] | .[0]) // .models[0]) as $t
            | .models += [$t | del(.n_cpu_moe, .sampling, .benchmark) | .alias = "candidate" | .label = "candidate" | .file = strenv(F)
                | .size_bytes = env(S) | .quantization = ((strenv(Q) | select(length > 0)) // "unknown") | .parameters = "unknown"
                | .url = strenv(U)]' "$scratch"
    fi
    if ! sel="$(llmenv --config "$scratch" models select "$target" 2>&1)"; then
        rm -f "$scratch"; echo "$sel" >&2; exit 1
    fi
    yq -i '.llm_server.enabled = true' "$scratch"
    trap - ERR
    result="$(jq -c --arg s "$(readlink -m "$scratch")" '. + {scratch_config: $s}' <<<"$result")"
fi
echo "$result"
