#!/usr/bin/env bash
# hf-scout.sh — list GGUF candidates from the Hugging Face API with size, context, base model and fit class.
# usage: hf-scout.sh [--query Q | --repo ORG/NAME [--revision REV] [--file PATH | --quant Q] [--single-file]] [--sort trendingScore|downloads|lastModified|likes] [--limit N]
#                    [--min-ctx TOKENS] [--budget-mib N] [--ram-mib N] [--include-modded] [--json]
# Budget flags default to preflight.sh values (the VRAM budget is the smaller of the repo verdict and ceiling minus reserve, SKILL.md rule 1);
# both must come out as non-negative integers, else exit 2 naming the flag. HF_FIXTURE_DIR=<dir> reads <dir>/models.json (or, with --repo,
# <dir>/model-<owner>__<name>.json, one object) and <dir>/tree-<owner>__<name>.json instead of the network (tests); with --revision REV
# the two names carry a @REV suffix (model-<owner>__<name>@REV.json). Needs curl and jq.
# --repo ORG/NAME screens exactly that repo (api/models/<repo>/revision/<REV|main> with the same expand[] list) and keeps it whatever --min-ctx and the modded
# filter say; it excludes --query (exit 2), and a value that is not an ORG/NAME repo id exits 2. --revision REV (a branch, tag or commit; only
# [A-Za-z0-9._%-], no "..", else exit 2) reads the model and its tree at that one commit; an unreadable repo or revision exits 2. --file PATH picks
# that GGUF (the first shard of a split one; a bare file name with no exact match takes the first file of that name in a sub-folder) whatever the fit; a file absent from a tree that was read exits 2.
# --quant Q picks the quant whose token equals Q case-insensitively (UD-Q4_K_XL is not Q4_K_XL), whatever the fit (the first shard of a split one);
# a tree that was read without it exits 2 "no quant <Q> in <repo> at revision <rev>"; it excludes --file. --revision, --file and --quant need --repo (exit 2).
# --single-file drops split (sharded) quants before the fit and the pick, so fit, pick and quants answer for single-file quants only; it excludes --file and --quant (exit 2).
# Every row carries sha (the commit the answer resolved to, null when absent, never "main"); every quant object, and so pick, carries bytes
# (exact sum of the shard sizes), sha256 (lfs.oid when the quant is one file, else null) and split (true when the quant has more than one shard). The trees are read at that sha (main without one).
# HF_TOKEN (optional; network mode only) is sent as an Authorization: Bearer header through curl's stdin config (-K -), never in argv or output; it must match [A-Za-z0-9_-]+ (exit 2 otherwise, value not shown).
# Observed 2026-10-31 on ornith-ai/Ornith-1.5-9B-GGUF: expand[] returns gguf, downloads, likes, lastModified and tags; baseModels is
# absent for a repo whose card declares no base model (it is present for e.g. Qwen/Qwen3-8B-GGUF), which the row maps to base "".
set -euo pipefail

query=""; repo=""; revision=""; file=""; qsel=""; sort=trendingScore; limit=40; min_ctx=131072; budget=""; ram=""; modded=0; as_json=0; single=0
while [ $# -gt 0 ]; do
    case "$1" in
    --query) query="$2"; shift 2 ;;
    --repo) repo="$2"; shift 2 ;;
    --revision) revision="$2"; shift 2 ;;
    --file) file="$2"; shift 2 ;;
    --quant) qsel="$2"; shift 2 ;;
    --single-file) single=1; shift ;;
    --sort) sort="$2"; shift 2 ;;
    --limit) limit="$2"; shift 2 ;;
    --min-ctx) min_ctx="$2"; shift 2 ;;
    --budget-mib) budget="$2"; shift 2 ;;
    --ram-mib) ram="$2"; shift 2 ;;
    --include-modded) modded=1; shift ;;
    --json) as_json=1; shift ;;
    -h|--help) sed -n '2,/^set -/s/^# \{0,1\}//p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "unknown flag: $1" >&2; exit 2 ;;
    esac
done
if [ -n "$repo" ]; then
    [ -z "$query" ] || { echo "--repo and --query exclude each other" >&2; exit 2; }
    [[ "$repo" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || { echo "--repo takes ORG/NAME, got '$repo'" >&2; exit 2; }
else
    [ -z "$revision$file$qsel" ] || { echo "--revision, --file and --quant need --repo" >&2; exit 2; }
fi
[ -z "$file" ] || [ -z "$qsel" ] || { echo "--file and --quant exclude each other" >&2; exit 2; }
[ "$single" = 0 ] || [ -z "$file$qsel" ] || { echo "--single-file excludes --file and --quant" >&2; exit 2; }
# --revision comes from a pasted URL and reaches the API path and a fixture name
if [ -n "$revision" ]; then
    [[ "$revision" =~ ^[A-Za-z0-9._%-]+$ && "$revision" != *..* ]] || { echo "--revision takes a branch, tag or commit ([A-Za-z0-9._%-], no '..'), got '$revision'" >&2; exit 2; }
fi
# the token is written into a curl config line, so a quote or newline in it would add config lines
[ -n "${HF_FIXTURE_DIR:-}" ] || [ -z "${HF_TOKEN:-}" ] || [[ "$HF_TOKEN" =~ ^[A-Za-z0-9_-]+$ ]] \
    || { echo "HF_TOKEN must hold only [A-Za-z0-9_-] (a Hugging Face access token); its value is not shown" >&2; exit 2; }
if [ -z "$budget" ] || [ -z "$ram" ]; then
    pf="$(bash "$(dirname "${BASH_SOURCE[0]}")/preflight.sh")"
    # the smaller of the repo verdict and ceiling minus reserve (SKILL.md rule 1)
    [ -n "$budget" ] || budget="$(jq '[.budget.llmenv_budget.vram_available_mib, .budget.model_vram_budget_mib] | map(select(. != null)) | min' <<<"$pf")"
    [ -n "$ram" ] || ram="$(jq .budget.ram_offload_budget_mib <<<"$pf")"
fi
# a null budget would make the jq fit math ignore that resource silently
[[ "$budget" =~ ^[0-9]+$ ]] || { echo "--budget-mib must be a non-negative integer, got '$budget'" >&2; exit 2; }
[[ "$ram" =~ ^[0-9]+$ ]] || { echo "--ram-mib must be a non-negative integer, got '$ram'; pass it explicitly" >&2; exit 2; }

api="https://huggingface.co/api"
hf() { # hf <fixture-file> <url> [curl args]: fixture dir for tests, network otherwise
    local f="$1" u="$2"; shift 2
    if [ -n "${HF_FIXTURE_DIR:-}" ]; then cat "$HF_FIXTURE_DIR/$f"
    elif [ -n "${HF_TOKEN:-}" ]; then printf 'header = "Authorization: Bearer %s"\n' "$HF_TOKEN" | curl -fsS --max-time 30 -K - "$@" "$u"
    else curl -fsS --max-time 30 "$@" "$u"; fi
}
q=(--data-urlencode "filter=gguf" --data-urlencode "sort=$sort" --data-urlencode "direction=-1" --data-urlencode "limit=$limit")
x=(); for e in gguf downloads likes lastModified baseModels tags sha; do x+=(--data-urlencode "expand[]=$e"); done
if [ -n "$repo" ]; then
    sfx=""; [ -z "$revision" ] || sfx="@$revision"
    model="$(hf "model-${repo//\//__}$sfx.json" "$api/models/$repo/revision/${revision:-main}" -G "${x[@]}")" || {
        echo "cannot read $repo at revision ${revision:-main} from the Hugging Face API (no such repo or revision?)" >&2; exit 2; }
    models="[$model]"
else
    sfx=""
    [ -z "$query" ] || q+=(--data-urlencode "search=$query")
    models="$(hf models.json "$api/models" -G "${q[@]}" "${x[@]}")"
fi

# Drop noise before spending one tree request per repo: below min ctx, no context data, and (unless asked) modded variants.
noise='uncensored|abliterat|heretic|erotic|nsfw|roleplay|jailbreak|lora'
if [ -n "$repo" ]; then repos="$models"; else repos="$(jq -c --argjson ctx "$min_ctx" --argjson modded "$modded" --arg noise "$noise" '
    [.[] | select((.gguf.context_length // 0) >= $ctx)
         | select($modded == 1 or (.id | ascii_downcase | test($noise) | not))
         | select((.tags // []) | index("text-to-image") | not)]' <<<"$models")"; fi

# One tree request per repo, 8 at a time. A failed request leaves no file, and that repo is reported as "unknown" rather
# than silently classed as "does not fit".
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
trees="${HF_FIXTURE_DIR:-$tmp}"
# Each tree is read at the row's sha (else the asked revision, else main). xargs -n2 passes "<id> <sha>" as two arguments after the fixed ones.
# shellcheck disable=SC2016 # the $-expressions belong to the inner bash
[ -n "${HF_FIXTURE_DIR:-}" ] || jq -r --arg rev "${revision:-main}" '.[] | "\(.id) \(.sha // $rev)"' <<<"$repos" | xargs -r -P8 -n2 bash -c 'u="$0/models/$3/tree/$4?recursive=true"; o="$1/tree-${3//\//__}$2.json"
    if [ -n "$HF_TOKEN" ]; then printf "header = \"Authorization: Bearer %s\"\n" "$HF_TOKEN" | curl -fsS --max-time 30 -K - "$u" -o "$o"; else curl -fsS --max-time 30 "$u" -o "$o"; fi 2>/dev/null || true' "$api" "$tmp" "$sfx"

out="$(while IFS= read -r row; do
    id="$(jq -r .id <<<"$row")"
    tree="$(cat "$trees/tree-${id//\//__}$sfx.json" 2>/dev/null || true)"
    if [ -z "$tree" ]; then
        jq -c '{id, params_b: ((.gguf.total // 0) / 1e9 | . * 10 | floor / 10), arch: .gguf.architecture, ctx: .gguf.context_length, base: "", downloads, likes, updated: .lastModified[0:10], sha: (.sha // null), moe_hint: false, fit: "unknown", pick: null, quants: []}' <<<"$row"
        continue
    fi
    item="$(jq -c --argjson b "$budget" --argjson r "$ram" --arg f "$file" --arg qs "$qsel" --argjson single "$single" --argjson tree "$tree" '
        # one entry per quant: group split shards (-00001-of-00004) and sum their size
        (.gguf.total // 0) as $p
        | ($tree | map(select(.path | test("\\.gguf$")) | select(.path | test("mmproj") | not))
               | group_by(.path | sub("-[0-9]{5}-of-[0-9]{5}"; ""))
               | map({file: (.[0].path), quant: (.[0].path | sub("-[0-9]{5}-of-[0-9]{5}"; "") | capture("(?<q>(UD-)?(I?Q[0-9][A-Za-z0-9_]*|BF16|F16|F32))"; "i").q // "?"),
                      mib: ((map(.size) | add) / 1048576 | floor),
                      bytes: (map(.size) | add), sha256: (if length == 1 then (.[0].lfs.oid // null) else null end), split: (length > 1)})
               # bits per weight; under 1.5 is a draft head or adapter, not the model
               | map(. + {bpw: (if $p > 0 then (.mib * 1048576 * 8 / $p * 10 | floor / 10) else 0 end)})
               | map(select($f != "" or $qs != "" or $p == 0 or .bpw >= 1.5))
               | map(select($single == 0 or (.split | not)))
               | if $qs != "" then map(select((.quant | ascii_downcase) == ($qs | ascii_downcase))) elif $f == "" then . elif any(.[]; .file == $f) then map(select(.file == $f)) else map(select(.file | endswith("/" + $f))) end) as $q
        | ($q | map(select(.mib + 1536 <= $b)) | max_by(.mib)) as $vram       # leaves ~1.5 GiB for KV cache and buffers
        | ($q | map(select(.quant | test("^(UD-)?(I?Q[4-9]|BF16|F16|Q8)"; "i"))) | map(select(.mib <= $b + $r - 2048)) | max_by(.mib)) as $off   # screening only: llmenv budget decides
        # dense models offloaded layer-wise are too slow, so only MoE counts as "offload"
        | (((.id + " " + (.gguf.architecture // "")) | test("[0-9]+B-A[0-9]+B|moe|exp|next"; "i"))) as $moe_hint
        | {id: .id, params_b: ((.gguf.total // 0) / 1e9 | . * 10 | floor / 10), arch: .gguf.architecture, ctx: .gguf.context_length,
           base: (.baseModels.models // [] | map(.id) | join(",")), downloads: .downloads, likes: .likes,
           updated: .lastModified[0:10], sha: (.sha // null),
           moe_hint: $moe_hint,
           fit: (if $vram then (if $vram.bpw >= 3.5 or $p == 0 then "vram" else "vram-lowbit" end) elif ($off and $moe_hint) then "offload" else "no" end),
           pick: (if $f != "" or $qs != "" then ($q[0] // null) else ($vram // $off // ($q | map(select(.quant | test("^(UD-)?(I?Q[4-9]|BF16|F16|Q8)"; "i"))) | min_by(.mib))) end), quants: ($q | map("\(.quant):\(.mib)") )}' <<<"$row")"
    echo "$item"
done < <(jq -c '.[]' <<<"$repos") | jq -s .)"

if [ -n "$file$qsel" ]; then
    what="$file is not a GGUF"; [ -z "$qsel" ] || what="no quant $qsel"
    jq -e 'all(.[]; .fit == "unknown" or .pick != null)' <<<"$out" >/dev/null || { echo "$what in $repo at revision ${revision:-main}" >&2; exit 2; }
fi

if [ "$as_json" = 1 ]; then jq . <<<"$out"; exit 0; fi
printf 'budget: VRAM %s MiB, RAM-for-offload %s MiB, ctx>=%s\n' "$budget" "$ram" "$min_ctx"
jq -r --argjson b "$budget" 'sort_by(.fit != "vram", .fit != "offload", .fit != "vram-lowbit", -(.downloads // 0)) | .[]
    | [.fit, .id, "\(.params_b)B", (.arch // "-"), .ctx, (.pick.quant // "-"), "\(.pick.mib // 0)MiB/\(.pick.bpw // 0)bpw", "left=\($b - (.pick.mib // 0))MiB",
       (if .moe_hint then "moe?" else "dense?" end), "dl=\(.downloads)", "likes=\(.likes)", .updated, (.base | if . == "" then "-" else . end)]
    | @tsv' <<<"$out" | column -t -s $'\t'
