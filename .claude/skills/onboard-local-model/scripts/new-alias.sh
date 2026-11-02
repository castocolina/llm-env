#!/usr/bin/env bash
# new-alias.sh — the models.yml alias a candidate file gets (onboarding's Alias for a new entry, A1-A3), read from the live config.
# usage: new-alias.sh --file NAME --size BYTES [--quant TOKEN]
# NAME is the candidate's file (its base name is used), BYTES its exact size, TOKEN its quant token (resolve-target.sh's quant or
# hf-scout.sh's pick.quant; leave it out when null). Prints {"alias": A, "rule": "A1"|"A2"|"A3"}.
# A1: a live entry has this file and size_bytes BYTES: its alias. The same file with another size_bytes exits 1 naming that alias
#     and both sizes (a different file under that name).
# A2: NAME without .gguf and without TOKEN (case-insensitive), lowercased, every run outside [a-z0-9] turned into -, leading and
#     trailing - dropped, cut to 60 characters (a trailing - dropped again). With a TOKEN only: when a live entry's file gives the
#     same name with that entry's own quantization as the token, the alias is <that alias>-<TOKEN lowercase> instead
#     (quant-headroom.sh add-entry's name).
# A3: while a live alias already has the A2 name, -2, -3... is appended.
# Env: LLM_ENV_CONFIG. Exit 2 on usage, a non-integer BYTES, a TOKEN outside [A-Za-z0-9_-], a missing config or an empty name.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source-path=SCRIPTDIR
# shellcheck source=../../../../tools/lib.sh
source "$here/../../../../tools/lib.sh"

file=""; size=""; quant=""
while [ $# -gt 0 ]; do
    case "$1" in
    --file) file="${2:-}"; shift 2 || shift ;;
    --size) size="${2:-}"; shift 2 || shift ;;
    --quant) quant="${2:-}"; [ -n "$quant" ] || { echo "--quant needs a token" >&2; exit 2; }; shift 2 ;;
    -h|--help) sed -n '2,/^set -/{/^set -/!p}' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
done
[ -n "$file" ] && [ -n "$size" ] || { echo "usage: new-alias.sh --file NAME --size BYTES [--quant TOKEN]" >&2; exit 2; }
[[ "$size" =~ ^[0-9]+$ ]] || { echo "--size must be a whole number of bytes: $size" >&2; exit 2; }
[[ "$quant" =~ ^[A-Za-z0-9_-]*$ ]] || { echo "--quant must match [A-Za-z0-9_-]: $quant" >&2; exit 2; }
require_cmd jq yq
[ -f "$CONFIG_PATH" ] || { echo "no models.yml at $CONFIG_PATH" >&2; exit 2; }

# shellcheck disable=SC2016 # the $-names belong to jq
yq -o=json '.models // []' "$CONFIG_PATH" | jq -c --arg f "${file##*/}" --argjson size "$size" --arg t "$quant" '
  def tok: if type == "string" and test("^[A-Za-z0-9_-]+$") then . else "" end;
  def name($q): (split("/") | last // "") | sub("\\.gguf$"; ""; "i") | (if $q == "" then . else gsub($q; ""; "i") end)
    | ascii_downcase | gsub("[^a-z0-9]+"; "-") | gsub("^-|-$"; "") | .[:60] | sub("-$"; "");
  . as $live | [$live[] | select((.file // "" | split("/") | last) == $f)] as $same
  | if any($same[]; .size_bytes == $size) then {alias: ($same | map(select(.size_bytes == $size)) | first.alias), rule: "A1"}
    elif ($same | length) > 0 then
      "\($f): live alias \($same[0].alias) has size_bytes \($same[0].size_bytes), the candidate \($size) bytes (a different file under that name)\n" | halt_error(1)
    else ($f | name($t)) as $n
      | if $n == "" then "\($f): the name is empty once .gguf and the quant token are dropped\n" | halt_error(2) else . end
      # no TOKEN, no sibling-quant name: it would end in a bare -
      | ((if $t == "" then null else [$live[] | select((.quantization | tok) as $q | (.file // "" | name($q)) == $n) | .alias][0] end) as $sib
         | if $sib then "\($sib)-\($t | ascii_downcase)" else $n end) as $a2
      | [$live[].alias] as $taken
      | if ($taken | index([$a2])) == null then {alias: $a2, rule: "A2"}
        else {alias: first(range(2; infinite) | "\($a2)-\(.)" | select(. as $c | $taken | index([$c]) == null)), rule: "A3"} end
    end'
