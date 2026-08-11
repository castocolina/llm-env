#!/usr/bin/env bash
# prune.sh — remove everything clean.sh removes, plus all downloaded models.
set -euo pipefail
# shellcheck disable=SC1091 # Resolved from this script at runtime.
# shellcheck source=../tools/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/../tools/lib.sh"

require_cmd numfmt

resolved_models_dir=""
if [ -d "$MODELS_DIR" ]; then
    resolved_models_dir="$(cd "$MODELS_DIR" && pwd -P)"
    case "$resolved_models_dir" in
        "/" | "$HOME" | "$REPO_DIR")
            die "refusing to prune ${resolved_models_dir}: this looks like the filesystem root, the home directory, or the repository, not a models directory"
            ;;
    esac
    # Reject anything too shallow to plausibly be a dedicated models
    # directory (e.g. "/" has depth 0, "/home" has depth 1); the default,
    # ${HOME}/llm-workspace/models, has depth 3 or more.
    depth="$(printf '%s' "$resolved_models_dir" | tr -cd '/' | wc -c)"
    [ "$depth" -ge 2 ] \
        || die "refusing to prune ${resolved_models_dir}: path is too shallow to be a models directory"
    # Path shape alone (not /, $HOME, or $REPO_DIR, and deep enough) is
    # NOT proof this directory is actually the one llm-env manages -- any
    # pre-existing, unrelated directory at a plausible depth (e.g.
    # /etc/ssh) would otherwise pass every check above and get recursively
    # deleted. setup/setup.sh's Step 4 touches this marker the first time
    # it creates or uses $MODELS_DIR; require it here so prune only ever
    # deletes a directory llm-env itself created for this purpose.
    [ -f "${resolved_models_dir}/.llm-env-managed" ] \
        || die "refusing to prune ${resolved_models_dir}: missing .llm-env-managed marker (only a models directory created by 'make setup' is eligible; if this genuinely is your models directory, run: touch ${resolved_models_dir}/.llm-env-managed)"
fi

model_count=0
model_bytes=0
if [ -n "$resolved_models_dir" ]; then
    # -mindepth 1 with no -maxdepth: counts nested files and dotfiles too,
    # matching what the deletion step below actually removes. The
    # .llm-env-managed marker itself is bookkeeping, not a downloaded
    # model, so it's excluded from the count and size shown to the user.
    while IFS= read -r -d '' file; do
        model_count=$((model_count + 1))
        size="$(stat -c %s "$file" 2>/dev/null || echo 0)"
        model_bytes=$((model_bytes + size))
    done < <(find "$resolved_models_dir" -mindepth 1 -type f -not -name '.llm-env-managed' -print0)
fi
model_human="$(numfmt --to=iec --suffix=B "$model_bytes")"

echo "This removes everything 'make clean' removes, PLUS:"
echo "  ${model_count} downloaded model file(s) in ${MODELS_DIR} (${model_human})"
echo "This cannot be undone — models must be re-downloaded to use again."
if [ "${LLM_ENV_ASSUME_YES:-0}" = "1" ]; then
    confirm=yes
else
    read -rp "Proceed? (yes/no) " confirm
fi
[ "$confirm" = "yes" ] || { echo "Aborted."; exit 1; }

LLM_ENV_ASSUME_YES=1 bash "$(dirname "${BASH_SOURCE[0]}")/clean.sh"
if [ -n "$resolved_models_dir" ]; then
    # -delete (not `rm -rf .../*`): a glob misses dotfiles and requires a
    # separate `rm -rf` per top-level entry to also remove nested
    # directories cleanly; find -mindepth 1 -delete removes everything
    # under the directory, depth-first, in one pass, and leaves the
    # directory itself in place.
    find "$resolved_models_dir" -mindepth 1 -delete
fi
log_info "pruned ${model_count} model file(s) (${model_human})"
