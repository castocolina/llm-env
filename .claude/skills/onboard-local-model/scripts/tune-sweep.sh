#!/usr/bin/env bash
# tune-sweep.sh — staged performance sweep of one models.yml entry: fit (llmenv budget), throughput, verify.
# usage: tune-sweep.sh --alias A --out DIR [--ctx 131072,196608,262144] [--kv q5_1,q8_0] [--ubatch 512,1024,2048] [--fa on]
#                      [--ngl LIST] [--moe LIST] [--threads LIST] [--timeout 3600]
# Stages: fit (each kv x ctx x ngl x moe through `llmenv budget` on a redacted scratch config; only vram_feasible and
# ram_feasible cells continue), throughput (each kv x ngl x moe at its first feasible ctx, x ubatch x fa x threads, depths
# 0,32768), verify (per feasible fit, the best throughput row of the fit's kv, ngl and moe by tg then pp at 32768, at depths 0,
# 32768, min(131072, ctx-512), ctx-512).
# --fa takes on|off values; --threads is swept only for cells that set --moe. Prints its upper time bound (runs x timeout) first
# and ends by printing `tune-pick.sh results.jsonl`.
# DIR must be absent or empty. It becomes a git repository: plan.json and the redacted base.yml in the baseline commit, then one
# commit per run (runs/<id>/ holds that run's models.yml and bench output); fit.jsonl and results.jsonl sit at its top.
# TUNE_BENCH_CMD (default bench-fit.sh next to this script) is the benchmark command seam. A failed run is recorded with
# status "failed" and the last stderr line; the sweep goes on. The live models.yml is only read.
# --out must resolve to a directory under <repo>/tmp/ (exit 2 otherwise): benchmark challenges run only in isolated temp directories there.
# Exit 3 while llm-server runs (it holds the GPU), 2 on usage or a non-empty DIR, 1 when llmenv budget cannot answer.
set -euo pipefail

alias=""; out=""; ctx_arg="131072,196608,262144"; kv_arg="q5_1,q8_0"; ub_arg="512,1024,2048"; fa_arg="on"
ngl_arg=""; moe_arg=""; thr_arg=""; bound=3600
while [ $# -gt 0 ]; do
    case "$1" in
    --alias) alias="${2:-}"; shift 2 ;;
    --out) out="${2:-}"; shift 2 ;;
    --ctx) ctx_arg="${2:-}"; shift 2 ;;
    --kv) kv_arg="${2:-}"; shift 2 ;;
    --ubatch) ub_arg="${2:-}"; shift 2 ;;
    --fa) fa_arg="${2:-}"; shift 2 ;;
    --ngl) ngl_arg="${2:-}"; shift 2 ;;
    --moe) moe_arg="${2:-}"; shift 2 ;;
    --threads) thr_arg="${2:-}"; shift 2 ;;
    --timeout) bound="${2:-}"; shift 2 ;;
    -h|--help) sed -n '2,/^set -/{/^set -/!p}' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown flag: $1" >&2; exit 2 ;;
    esac
done
[ -n "$alias" ] && [ -n "$out" ] || { echo "--alias and --out are required" >&2; exit 2; }

# comma lists; an unset axis (default ",") is one empty element, meaning "axis not set"
IFS=, read -ra ctxs <<<"${ctx_arg:-,}"; IFS=, read -ra kvs <<<"${kv_arg:-,}"; IFS=, read -ra ubs <<<"${ub_arg:-,}"
IFS=, read -ra fas <<<"${fa_arg:-,}"; IFS=, read -ra ngls <<<"${ngl_arg:-,}"; IFS=, read -ra moes <<<"${moe_arg:-,}"
IFS=, read -ra thrs <<<"${thr_arg:-,}"
for v in "${ctxs[@]}" "${ubs[@]}" "$bound"; do
    [[ "$v" =~ ^[1-9][0-9]*$ ]] || { echo "ctx, ubatch and timeout values must be positive integers, got '$v'" >&2; exit 2; }
done
for v in "${ngls[@]}" "${moes[@]}" "${thrs[@]}"; do
    [[ "$v" =~ ^([0-9]+)?$ ]] || { echo "ngl, moe and threads values must be non-negative integers, got '$v'" >&2; exit 2; }
done
for v in "${fas[@]}"; do [[ "$v" =~ ^(on|off)$ ]] || { echo "--fa values must be on or off, got '$v'" >&2; exit 2; }; done
for v in "${kvs[@]}"; do [[ "$v" =~ ^[a-z0-9_]+$ ]] || { echo "--kv values must be cache type names, got '$v'" >&2; exit 2; }; done

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source-path=SCRIPTDIR
# shellcheck source=../../../../tools/lib.sh
source "$here/../../../../tools/lib.sh"
# shellcheck source-path=SCRIPTDIR
# shellcheck source=require-tmp-out.sh
source "$here/require-tmp-out.sh"
require_tmp_out "$out"
if [ -d "$out" ] && [ -n "$(ls -A "$out")" ]; then echo "$out is not empty; use a new directory per sweep" >&2; exit 2; fi
bench_cmd="${TUNE_BENCH_CMD:-$here/bench-fit.sh}"
if container_running llm-server; then
    echo "llm-server is running and holds the GPU; stop it first with make stop-llama (the skill asks the user)" >&2; exit 3
fi

# upper bound before any work: every fit counted as feasible
tcount=0
for m in "${moes[@]}"; do if [ -z "$m" ]; then tcount=$((tcount + 1)); else tcount=$((tcount + ${#thrs[@]})); fi; done
fits_max=$((${#kvs[@]} * ${#ctxs[@]} * ${#ngls[@]}))
runs=$((${#kvs[@]} * ${#ngls[@]} * tcount * ${#ubs[@]} * ${#fas[@]} + fits_max * ${#moes[@]}))
echo "upper bound: $runs runs x ${bound}s = $((runs * bound))s (infeasible fits are skipped)"

mkdir -p "$out"; DIR="$(cd "$out" && pwd)"
scratch="$(mktemp -d)"
trap 'rm -rf "$scratch"' EXIT
git init -q "$DIR"
yq '.server.api_key = "redacted" | .omniroute.initial_password = "" | .llm_server.enabled = true' "$CONFIG_PATH" >"$DIR/base.yml"
llmenv --config "$DIR/base.yml" models select "$alias" >/dev/null || { echo "cannot select '$alias' in $CONFIG_PATH" >&2; exit 1; }
jq -n --arg alias "$alias" --arg ctx "$ctx_arg" --arg kv "$kv_arg" --arg ub "$ub_arg" --arg fa "$fa_arg" --arg ngl "$ngl_arg" \
    --arg moe "$moe_arg" --arg thr "$thr_arg" --argjson timeout "$bound" --argjson runs "$runs" \
    '{alias: $alias, ctx: $ctx, kv: $kv, ubatch: $ub, fa: $fa, ngl: $ngl, moe: $moe, threads: $thr, timeout: $timeout, max_runs: $runs}' >"$DIR/plan.json"
: >"$DIR/fit.jsonl"; : >"$DIR/results.jsonl"
commit() { git -C "$DIR" add -A && git -C "$DIR" -c user.name=tune-sweep -c user.email=tune-sweep@localhost commit -q -m "$1"; }
git -C "$DIR" add plan.json base.yml && git -C "$DIR" -c user.name=tune-sweep -c user.email=tune-sweep@localhost commit -q -m baseline

# make_cfg FILE KV CTX NGL MOE [UBATCH FA]: base.yml plus the axis values (strenv/env only)
make_cfg() {
    cp "$DIR/base.yml" "$1"
    KV="$2" CTX="$3" A="$alias" yq -i '.runtime.cache_type_k = strenv(KV) | .runtime.cache_type_v = strenv(KV)
        | (.models[] | select(.alias == strenv(A)) | .ctx_size) = env(CTX)' "$1"
    [ -z "$4" ] || NGL="$4" A="$alias" yq -i '(.models[] | select(.alias == strenv(A)) | .n_gpu_layers) = env(NGL)' "$1"
    [ -z "$5" ] || MOE="$5" A="$alias" yq -i '(.models[] | select(.alias == strenv(A)) | .n_cpu_moe) = env(MOE)' "$1"
    [ -z "${6:-}" ] || UB="$6" FA="$7" yq -i '.runtime.ubatch_size = env(UB) | .runtime.flash_attn = (strenv(FA) == "on")' "$1"
}

# stage 1: fit. llmenv_budget_json reads the verdict of an infeasible fit too; no JSON object or an .error means the cell
# could not be evaluated at all (it would repeat for every cell).
fits=()
for kv in "${kvs[@]}"; do for ctx in "${ctxs[@]}"; do for ngl in "${ngls[@]}"; do for moe in "${moes[@]}"; do
    make_cfg "$scratch/fit.yml" "$kv" "$ctx" "$ngl" "$moe"
    budget_json="$(llmenv_budget_json "$scratch/fit.yml" "$MODELS_DIR")" || exit 1
    line="$(jq -c --arg kv "$kv" --argjson ctx "$ctx" --arg ngl "$ngl" --arg moe "$moe" '
        def num: if . == "" then null else tonumber end;
        {kv: $kv, ctx: $ctx, ngl: ($ngl | num), moe: ($moe | num), vram_feasible: (.vram_feasible // false),
         ram_feasible: (.ram_feasible // false), available_mib: .available_mib}' <<<"$budget_json")"
    echo "$line" >>"$DIR/fit.jsonl"
    if jq -e '.vram_feasible and .ram_feasible' <<<"$line" >/dev/null; then
        fits+=("$kv|$ctx|$ngl|$moe|$(jq -r .available_mib <<<"$line")")
    else
        echo "[fit] kv=$kv ctx=$ctx ngl=${ngl:--} moe=${moe:--}: infeasible, skipped"
    fi
done; done; done; done

# bench-fit.sh's -p 512 rides on top of each depth (BENCH_PROMPT_TOKENS in pylib/capacity.py)
prompt=512

# bench_run STAGE KV CTX NGL MOE AVAIL UBATCH FA THREADS DEPTHS [REF FULL]: one bench-fit run, one results row, one commit
bench_run() {
    local stage="$1" kv="$2" ctx="$3" ngl="$4" moe="$5" avail="$6" ub="$7" fa="$8" thr="$9" depths="${10}" ref="${11:-}" full="${12:-}"
    local id rundir status=0 b err="" bench_args
    id="${kv}-c${ctx}-ub${ub}-fa${fa}${ngl:+-ngl$ngl}${moe:+-moe$moe}${thr:+-t$thr}-${stage}"
    rundir="$DIR/runs/$id"; mkdir -p "$rundir"
    make_cfg "$rundir/models.yml" "$kv" "$ctx" "$ngl" "$moe" "$ub" "$fa"
    bench_args=(--alias "$alias" --out "$rundir" --depths "$depths" --timeout "$bound")
    [ -z "$thr" ] || bench_args+=(--threads "$thr")
    echo "[$stage] $id"
    LLM_ENV_CONFIG="$rundir/models.yml" bash "$bench_cmd" "${bench_args[@]}" </dev/null >/dev/null 2>"$rundir/stderr.txt" || status=$?
    b=""
    [ "$status" -ne 0 ] || b="$(jq -c . "$rundir/bench-$alias.json" 2>/dev/null || true)"
    if [ -z "$b" ]; then
        b=null; err="$(tail -n1 "$rundir/stderr.txt")"; [ -n "$err" ] || err="bench exited $status without output"
    fi
    jq -nc --arg id "$id" --arg stage "$stage" --argjson ctx "$ctx" --arg kv "$kv" --argjson ub "$ub" --arg fa "$fa" \
        --arg ngl "$ngl" --arg moe "$moe" --arg thr "$thr" --arg ref "$ref" --arg full "$full" --argjson avail "$avail" --argjson b "$b" --arg err "$err" '
        def num: if . == "" then null else tonumber end;
        {id: $id, stage: $stage, ctx: $ctx, kv: $kv, ubatch: $ub, fa: $fa, ngl: ($ngl | num), moe: ($moe | num),
         threads: ($thr | num), ref_depth: ($ref | num), full_depth: ($full | num), status: (if $b == null then "failed" else "ok" end), vram_available_mib: $avail,
         model_vram_mib: ($b.gpu.model_vram_mib // null), tps: ($b.tps // null), error: (if $b == null then $err else null end)}' \
        >>"$DIR/results.jsonl"
    commit "$id"
}

# stage 2: throughput; threads only where n_cpu_moe is set
swept=" "
for fit in "${fits[@]}"; do
    IFS='|' read -r kv ctx ngl moe avail <<<"$fit"
    # llama-bench sizes its context from the depth (bench-fit.sh passes no ctx), so one pass serves every ctx
    case "$swept" in *" $kv|$ngl|$moe "*) continue ;; esac
    swept+="$kv|$ngl|$moe "
    for ub in "${ubs[@]}"; do for fa in "${fas[@]}"; do
        if [ -z "$moe" ]; then
            bench_run throughput "$kv" "$ctx" "$ngl" "$moe" "$avail" "$ub" "$fa" "" 0,32768
        else
            for thr in "${thrs[@]}"; do bench_run throughput "$kv" "$ctx" "$ngl" "$moe" "$avail" "$ub" "$fa" "$thr" 0,32768; done
        fi
    done; done
done

# stage 3: verify the best throughput row of each fit at depths 0, 32768, ref = min(131072, ctx-prompt), full = ctx-prompt (ascending, none above full)
for fit in "${fits[@]}"; do
    IFS='|' read -r kv ctx ngl moe avail <<<"$fit"
    best="$(jq -sc --arg kv "$kv" --arg ngl "$ngl" --arg moe "$moe" '
        def num: if . == "" then null else tonumber end;
        def at($d; $k): ((.tps // []) | map(select(.depth == $d)) | .[0] | .[$k]) // -1;
        map(select(.stage == "throughput" and .status == "ok" and .kv == $kv and .ngl == ($ngl | num) and .moe == ($moe | num)))
        | sort_by([-at(32768; "tg_tps"), -at(32768; "pp_tps")]) | (.[0] // empty)' "$DIR/results.jsonl")"
    if [ -z "$best" ]; then echo "[verify] kv=$kv ctx=$ctx: no successful throughput run, skipped"; continue; fi
    read -r ref full depths < <(jq -nr --argjson c "$ctx" --argjson p "$prompt" '
        ($c - $p) as $f | ([131072, $f] | min) as $r
        | [$r, $f, ([0, 32768, $r, $f] | map(select(. <= $f)) | unique | join(","))] | @tsv')
    bench_run verify "$kv" "$ctx" "$ngl" "$moe" "$avail" "$(jq -r .ubatch <<<"$best")" "$(jq -r .fa <<<"$best")" "$(jq -r '.threads // ""' <<<"$best")" "$depths" "$ref" "$full"
done

bash "$here/tune-pick.sh" "$DIR/results.jsonl"
