# require-tmp-out.sh — sourced by effort-run.sh, bench-fit.sh, tune-sweep.sh, gen-challenge.sh and resolve-target.sh (--scratch-config FILE) after tools/lib.sh (needs REPO_DIR).
# require_tmp_out DIR: exit 2 unless DIR resolves (.. and symlinks followed, DIR may not exist yet) to a path under <REPO_DIR>/tmp/.
# Benchmark challenges run only on generated challenges, copied into isolated directories there, never in or against the owner's project.
require_tmp_out() {
    local base abs
    base="$(readlink -m "$REPO_DIR/tmp")"; abs="$(readlink -m "$1")"
    case "$abs/" in
    "$base"/?*) ;;
    *) echo "--out must be a directory under $base/ (resolved: $abs): benchmark challenges run only in isolated temp directories under ./tmp/, never in or against the owner's project" >&2; exit 2 ;;
    esac
}
