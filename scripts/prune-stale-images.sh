#!/usr/bin/env bash
# prune-stale-images.sh — reclaim the images this stack's builds and updates left in the rootless Podman store.
# Dry run unless --apply. An image is removed only when it is ours (a localhost/llm-env-* name, the omniroute or
# llama.cpp repository, or untagged with an llm-env.* label), no container (running, stopped or build) uses it, no
# configuration names it, and it is not a rollback copy. Every other image in the store is never touched.
# Removal is one plain `podman rmi` per tag or ID; a refusal (children, in use) is reported as kept, never forced.
set -euo pipefail
# shellcheck disable=SC1091 # Resolved from this script at runtime.
# shellcheck source=../tools/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/../tools/lib.sh"

apply=0
case "${1:-}" in
    --apply) apply=1 ;;
    "") ;;
    *) die "usage: prune-stale-images.sh [--apply]" ;;
esac
require_cmd podman jq yq numfmt
[ -f "$CONFIG_PATH" ] || die "no config at ${CONFIG_PATH}; run 'make setup' first (the configured images must be known)"
[ -f "$COMPOSE_FILE" ] || die "no rendered compose file at ${COMPOSE_FILE}; run 'bash setup/render-unit.sh' first"

# Every read fails closed: an unreadable source must never shrink the protected set.
images_json="$(podman images --format json)" || die "podman images failed"
containers_json="$(podman ps -a --external --format json)" || die "podman ps failed"
config_refs="$(yq -r '.. | select(key == "image") | select(tag == "!!str")' "$CONFIG_PATH")" || die "cannot read image fields of ${CONFIG_PATH}"
compose_refs="$(yq -r '.services[].image // ""' "$COMPOSE_FILE")" || die "cannot read ${COMPOSE_FILE}"
observe_refs="$(yq -r '.services[].image // ""' "${REPO_DIR}/docker/observe/compose.yml")" || die "cannot read docker/observe/compose.yml"
system_one_image="$(yq -r '.system_one.image // ""' "$CONFIG_PATH")" || die "cannot read system_one.image"

protected=""   # lines: <full image id>\t<reason>
protect_ref() {   # a reference absent from the store protects nothing and is fine
    local id
    [ -n "$1" ] || return 0
    id="$(podman image inspect --format '{{.Id}}' "$1" 2>/dev/null)" || return 0
    protected+="${id}"$'\t'"$2"$'\n'
}
container_images="$(jq -r '.[].ImageID' <<<"$containers_json")" || die "cannot parse podman ps output"
while IFS= read -r id; do
    [ -z "$id" ] || protected+="${id}"$'\t'"in use by a container"$'\n'
done <<<"$container_images"
while IFS= read -r ref; do protect_ref "$ref" "configured in models.yml"; done <<<"$config_refs"
while IFS= read -r ref; do protect_ref "$ref" "in the rendered docker-compose.yml"; done <<<"$compose_refs"
while IFS= read -r ref; do protect_ref "$ref" "in docker/observe/compose.yml"; done <<<"$observe_refs"
protect_ref "$VULKAN_IMAGE" "benchmark image (tools/lib.sh)"
protect_ref "$CPU_IMAGE" "benchmark CPU fallback image (tools/lib.sh)"
if [ -n "$system_one_image" ] && [ "$system_one_image" != null ]; then
    protect_ref "${system_one_image%:*}:rocm-prev" "rollback copy kept by update-system-one"
    protect_ref "${system_one_image%:*}:rocm-cand" "update-system-one candidate (kept for inspection)"
fi
protected_json="$(jq -Rn '[inputs | select(length > 0) | split("\t")] | group_by(.[0])
    | map({(.[0][0]): (map(.[1]) | unique | join("; "))}) | add // {}' <<<"$protected")" || die "cannot build the protected set"

# One row per image ID: <action>\t<id>\t<bytes>\t<display name>\t<reason>\t<space-separated tags>
# shortcut: OmniRoute records no rollback target (the commit worker only prints the prior ID on failure), so the
# newest untagged omniroute image is kept as the previous release; upgrade if the updater ever persists it.
plan="$(jq -r --argjson prot "$protected_json" --arg omni docker.io/diegosouzapw/omniroute --arg llama ghcr.io/ggml-org/llama.cpp '
    def tags: [(.Names // [])[] | select(test("@") | not)];
    def repos: [(.RepoDigests // [])[] | sub("@.*$"; "")];
    def our_repo: startswith("localhost/llm-env-") or . == $omni or . == $llama;
    def ours: (tags | length > 0 and all(.[]; sub(":[^:/]*$"; "") | our_repo))   # one foreign tag makes it not ours
        or (tags | length == 0) and ((repos | any(.[]; our_repo)) or ((.Labels // {}) | keys | any(.[]; startswith("llm-env."))));
    (unique_by(.Id)) as $all
    | ([$all[] | select((tags | length == 0) and any(repos[]; . == $omni))] | max_by(.Created) | .Id // "") as $omni_prev
    | $all[] | select(ours)
    | (if $prot[.Id] then $prot[.Id]
       elif .Containers > 0 then "in use by a container"
       elif .Id == $omni_prev then "rollback copy: newest untagged omniroute image (previous release)"
       else null end) as $keep
    | [if $keep then "keep" else "remove" end, .Id, (.Size | tostring),
       (if (tags | length) > 0 then tags[0] else "<untagged> \(repos[0] // "build") \(.Id[0:12])" end),
       ($keep // (if (tags | length) > 0 then "stale: tag not configured, no container"
                  elif (repos | length) > 0 then "stale: untagged pull, superseded"
                  else "stale: untagged llm-env build, superseded" end)),
       (tags | join(" "))] | @tsv' <<<"$images_json")" || die "cannot classify images"
foreign="$(jq -r --argjson n "$(grep -c . <<<"$plan" || true)" 'unique_by(.Id) | length - $n' <<<"$images_json")"

images_bytes() { podman system df --format json | jq -r '.[] | select(.Type == "Images") | .RawSize'; }
human() { numfmt --to=si --suffix=B --format='%.2f' "$1"; }

log_step "Stale image plan ($([ "$apply" = 1 ] && echo apply || echo 'dry run; APPLY=1 to remove'))"
printf '%-7s %-62s %10s  %s\n' ACTION IMAGE SIZE REASON
candidate_bytes=0
while IFS=$'\t' read -r action _ bytes shown reason _; do
    [ -n "$action" ] || continue
    printf '%-7s %-62s %10s  %s\n' "$action" "$shown" "$(human "$bytes")" "$reason"
    [ "$action" = remove ] && candidate_bytes=$((candidate_bytes + bytes))
done <<<"$plan"
printf '%s other image(s) in the store are outside this stack and are never touched\n' "$foreign"
printf 'candidates total %s (upper bound: sizes include layers shared with kept images)\n' "$(human "$candidate_bytes")"
[ "$apply" = 1 ] || { log_info "dry run: nothing removed"; exit 0; }

before="$(images_bytes)" || die "podman system df failed"
removed=0 refused=0
while IFS=$'\t' read -r action id _ shown _ tag_list; do
    [ "$action" = remove ] || continue
    read -r -a targets <<<"${tag_list:-$id}"
    err=""
    for target in "${targets[@]}"; do
        err="$(podman rmi "$target" 2>&1 >/dev/null)" || break
        err=""
    done
    if [ -z "$err" ]; then
        removed=$((removed + 1)); printf 'removed  %s\n' "$shown"
    else
        refused=$((refused + 1)); printf 'kept     %s  podman refused: %s\n' "$shown" "$(head -1 <<<"$err")"
    fi
done <<<"$plan"
after="$(images_bytes)" || die "podman system df failed"
log_info "removed ${removed} image(s), ${refused} refused; image store $(human "$before") -> $(human "$after"), freed $(human $((before - after)))"
