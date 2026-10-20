#!/usr/bin/env bash
# Safe failure mode: no update path is qualified for the current deployment.
# In particular, remote-setup stores a native Podman dependency on OmniRoute;
# replacing OmniRoute can require touching that dependent container. Until a
# complete, tested target-only procedure and activity profile are available,
# do not migrate config, inspect/pull registry content, or alter any container.
set -euo pipefail
# shellcheck disable=SC1091 # Resolved from this script at runtime.
# shellcheck source=../tools/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/../tools/lib.sh"

log_warn "OmniRoute update deferred (exit 75): the current native dependency-bearing deployment is unsupported; no update was attempted. The updater has no qualified idle/activity profile or safe isolated replacement path. Existing 300-second continuous-idle and 10800-second maximum-wait policy is not evaluated or bypassed."
exit 75
