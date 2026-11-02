#!/usr/bin/env bash
# fetch-gguf.sh — download one model file into FILE.part, verify it, then rename it to FILE; a re-run resumes an interrupted download.
# usage: fetch-gguf.sh (--url URL | --from PATH) --dest FILE --size BYTES [--sha256 HEX]
# `--from PATH` copies a local file the same way, through curl's `file://` (the path percent-encoded); the original is only read.
# FILE already there: an equal size (and sha256, when given) prints `already present: FILE` and exits 0 without a download; any
# other file there exits 1 untouched: it is never resumed onto, overwritten or deleted. Otherwise FILE.part (this script's own
# scratch) is resumed with `curl -fL --continue-at - -o FILE.part URL`: a .part of exactly BYTES skips curl, a larger one is
# removed first, and the free disk under FILE's directory must hold BYTES minus the .part plus 1024 MiB. A failed download keeps
# the .part for a re-run (exit 1). The size, then the sha256 when given, must match: a mismatch removes only the .part (exit 1). Then
# FILE.part is renamed to FILE and `fetched FILE (size verified[, sha256 verified])` is printed. Exit 2 on usage errors.
# Each attempt aborts below 1024 B/s for 120 s. At most 4 attempts, 5 s apart: a stall, a dropped connection, HTTP 408, 429 or
# 5xx retries from the .part. HTTP 401/403 stops at once with a gated-repo message; any other HTTP error, or a server that cannot
# resume the .part (curl exit 33), stops at once too. HF_TOKEN (optional) is sent only to https://huggingface.co/ urls (an hf.co
# url redirects to huggingface.co, another host, and curl drops the header there), as an `Authorization: Bearer` header through
# curl's stdin config (`-K -`), never in argv, output or a file. It must match [A-Za-z0-9_-]+ (else exit 2, value not shown).
# Needs curl, sha256sum, df, and jq (for --from). Writes only FILE.part and FILE.
set -euo pipefail

url=""; from=""; dest=""; size=""; sha=""
while [ $# -gt 0 ]; do
    case "$1" in
    --url|--from|--dest|--size|--sha256)
        [ $# -ge 2 ] || { echo "$1 needs a value" >&2; exit 2; }
        case "$1" in --url) url="$2" ;; --from) from="$2" ;; --dest) dest="$2" ;; --size) size="$2" ;; *) sha="$2" ;; esac
        shift 2 ;;
    -h|--help) sed -n '2,/^set -/{/^set -/!p}' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown flag: $1" >&2; exit 2 ;;
    esac
done
[ -z "$url" ] || [ -z "$from" ] || { echo "--url and --from exclude each other" >&2; exit 2; }
[ -z "$from" ] || [ -f "$from" ] || { echo "--from $from is not a regular file" >&2; exit 2; }
[ -n "$url$from" ] && [ -n "$dest" ] && [ -n "$size" ] || { echo "--url (or --from), --dest and --size are required" >&2; exit 2; }
[[ "$size" =~ ^[0-9]{1,19}$ ]] || { echo "--size must be a non-negative integer, got '$size'" >&2; exit 2; }
size=$((10#$size))  # a leading zero would read as octal
if [ -n "$from" ]; then
    command -v jq >/dev/null || { echo "--from needs jq" >&2; exit 2; }
    abs="$(CDPATH='' cd -- "$(dirname -- "$from")" && pwd)/$(basename -- "$from")"
    url="file://$(jq -nr --arg p "$abs" '$p | split("/") | map(@uri) | join("/")')"
fi
sha="${sha,,}"
[ -z "$sha" ] || [[ "$sha" =~ ^[0-9a-f]{64}$ ]] || { echo "--sha256 must be 64 hex characters, got '$sha'" >&2; exit 2; }
token=""
[[ ! "$url" =~ ^https://huggingface\.co/ ]] || token="${HF_TOKEN:-}"
[ -z "$token" ] || [[ "$token" =~ ^[A-Za-z0-9_-]+$ ]] \
    || { echo "HF_TOKEN must hold only [A-Za-z0-9_-] (a Hugging Face access token); its value is not shown" >&2; exit 2; }

die() { echo "fetch-gguf.sh: $*" >&2; exit 1; }
filesize() { stat -c %s "$1"; }
verified="size verified${sha:+, sha256 verified}"

if [ -e "$dest" ]; then
    if [ -f "$dest" ] && [ "$(filesize "$dest")" -eq "$size" ] \
        && { [ -z "$sha" ] || [ "$(sha256sum "$dest" | cut -d' ' -f1)" = "$sha" ]; }; then
        echo "already present: $dest"; exit 0
    fi
    die "$dest is already there with another size or sha256; it is never resumed onto, overwritten or deleted: move or rename it first"
fi
dir="$(dirname "$dest")"
mkdir -p "$dir"
part="$dest.part"
have=0
[ ! -f "$part" ] || have="$(filesize "$part")"
if [ "$have" -gt "$size" ]; then rm -f "$part"; have=0; fi
free="$(df --output=avail -B1 "$dir" | tail -n1 | tr -d ' ')"
need=$((size - have + 1024 * 1048576))
[ "$free" -ge "$need" ] \
    || die "not enough free disk under $dir: $((free / 1048576)) MiB free, need $((need / 1048576)) MiB (size - .part + 1024)"
if [ "$have" -lt "$size" ]; then
    opts=(-fL --speed-limit 1024 --speed-time 120 -w '%{http_code}')
    tstate="was not set"
    [ -z "${HF_TOKEN:-}" ] || tstate="was not sent: only https://huggingface.co/ urls get it"
    [ -z "$token" ] || tstate="was sent and refused"
    for attempt in 1 2 3 4; do
        [ "$attempt" -eq 1 ] || sleep 5
        rc=0
        if [ -n "$token" ]; then
            code="$(printf 'header = "Authorization: Bearer %s"\n' "$token" | curl "${opts[@]}" -K - --continue-at - -o "$part" "$url")" || rc=$?
        else
            code="$(curl "${opts[@]}" --continue-at - -o "$part" "$url")" || rc=$?
        fi
        case "$rc:$code" in
        0:*) break ;;
        22:401|22:403) die "HTTP $code: a gated or private repo; accept its licence on its Hugging Face page, set HF_TOKEN to a read token of that account (HF_TOKEN $tstate), then re-run the same command" ;;
        22:408|22:429|22:5??) ;;
        22:*) die "download failed: HTTP $code; $part is kept" ;;
        33:*) die "download failed: the server cannot resume from $part (curl exit 33); remove it and re-run" ;;
        esac
        [ "$attempt" -lt 4 ] || die "download failed after 4 attempts (curl exit $rc); $part is kept: re-run the same command to resume"
    done
fi
got="$(filesize "$part")"
[ "$got" -eq "$size" ] || { rm -f "$part"; die "size mismatch for $dest: got $got bytes, expected $size; $part was removed"; }
if [ -n "$sha" ]; then
    got="$(sha256sum "$part" | cut -d' ' -f1)"
    [ "$got" = "$sha" ] || { rm -f "$part"; die "sha256 mismatch for $dest: got $got, expected $sha; $part was removed"; }
fi
mv "$part" "$dest"
echo "fetched $dest ($verified)"
