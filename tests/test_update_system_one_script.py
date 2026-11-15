"""scripts/update-system-one.sh against a stub world: podman/podman-compose/curl/uv on PATH, a copied repo tree."""
import os
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
BASE = "localhost/llm-env-system-one:rocm"

PODMAN = r"""#!/usr/bin/env bash
W="$(cd "$(dirname "$0")/../w" && pwd)"  # podman runs under env -i
echo "podman $*" >>"$W/calls"
key() { printf '%s' "$1" | tr '/:' '__'; }
resolve() { if [[ -f "$W/tags/$(key "$1")" ]]; then cat "$W/tags/$(key "$1")"; else printf '%s' "$1"; fi; }
case "$1" in
  image) [[ -f "$W/tags/$(key "${@: -1}")" ]] || exit 1; cat "$W/tags/$(key "${@: -1}")"; echo ;;
  tag) resolve "$2" >"$W/tags/$(key "$3")" ;;
  build) [[ -z "${FAIL_BUILD:-}" ]] || exit 1
         while [[ $# -gt 0 ]]; do [[ "$1" == -t ]] && { echo cand-id >"$W/tags/$(key "$2")"; }; shift; done ;;
  port) echo "127.0.0.1:18001" ;;
  ps) if [[ "$*" == *label=* ]]; then echo new-cid; fi ;;
  inspect) echo "obs-cand-id true" ;;
  *) ;;
esac
exit 0
"""

CURL = r"""#!/usr/bin/env bash
W="$(cd "$(dirname "$0")/../w" && pwd)"  # podman runs under env -i
url="${@: -1}"; for a in "$@"; do [[ "$a" == http* ]] && url="$a"; done
port="${url#http://127.0.0.1:}"; port="${port%%/*}"
case "$url" in
  */health) [[ "$port" == 18010 ]] && echo "{\"idle_seconds\": ${IDLE:-900}}"; exit 0 ;;
  */v1/systemone)
    cat >/dev/null
    [[ "$port" == "${FAIL_PROBE_PORT:-none}" ]] && exit 22
    echo '{"model":"laya-rl-agent","answers":{"team":{"choice":"technical","probabilities":{"technical":0.8}},"urgent":{"noul":1.0}}}' ;;
esac
"""

UV = r"""#!/usr/bin/env bash
W="$(cd "$(dirname "$0")/../w" && pwd)"  # podman runs under env -i
echo "uv $*" >>"$W/calls"
args=("$@"); sub=""
for i in "${!args[@]}"; do
  if [[ "${args[$i]}" == omniroute-update ]]; then sub="${args[$((i + 1))]}"; break; fi
  if [[ "${args[$i]}" == *llmenv.py ]]; then sub="${args[$((i + 1))]}"; [[ "$sub" == --config ]] && sub="${args[$((i + 3))]}"; fi
done
case "$sub" in
  overrides) printf 'min_wait=300\nmax_deferred_wait=%s\n' "${MAX_WAIT:-0}" ;;
  lock-path) echo "$W/lock" ;;
  preflight) printf 'container_id=old-cid\nstop_seconds=10\nproject=llm-env\nprovider=podman-compose\ndrift=%s\n' "${DRIFT:-}" ;;
  projection) touch "$W/projection" ;;
  verify-commit) exit "${VERIFY_RC:-0}" ;;
  detect) echo '{"gpus":[{"pci_address":"0000:03:00.0","render_node":"renderD128"}]}' ;;
  gfx-arch) echo gfx1201 ;;
  migrate-config) echo '{}' ;;
esac
"""

RENDER = r"""#!/usr/bin/env bash
echo "render" >>"$W/calls"
base="$(podman image inspect --format '{{.Id}}' localhost/llm-env-system-one:rocm)"
echo "obs-$base" >"$W/tags/localhost_obs_$base"
printf 'services:\n  system-one:\n    image: localhost/obs:%s\n' "$base" >"$HOME/.config/llm-env/docker-compose.yml"
"""


@pytest.fixture
def world(tmp_path):
    repo, home, w = tmp_path / "repo", tmp_path / "home", tmp_path / "w"
    for rel in ("tools/lib.sh", "scripts/update-system-one.sh", "scripts/service-replace.sh",
                "scripts/system-one-build.sh", "scripts/system-one-cache-check.py", "docker/system-one/Dockerfile"):
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(REPO / rel, repo / rel)
    # The tests own the pin: the real Dockerfile moves with every update.
    dockerfile = repo / "docker/system-one/Dockerfile"
    dockerfile.write_text(re.sub(r"(?m)^ARG LAYA_VERSION=.*$", "ARG LAYA_VERSION=0.4.2", dockerfile.read_text()))
    (repo / "setup").mkdir()
    (repo / "setup/render-unit.sh").write_text(RENDER)
    (w / "tags").mkdir(parents=True)
    (w / "tags" / BASE.replace("/", "_").replace(":", "_")).write_text("old-id")
    (w / "calls").touch()
    cfg = home / ".config/llm-env"
    cfg.mkdir(parents=True)
    (cfg / "models.yml").write_text(textwrap.dedent("""\
        gpu:
          pci_address: "0000:03:00.0"
        system_one:
          enabled: true
          port: 18010
          image: localhost/llm-env-system-one:rocm
          models: english,multilingual
        """))
    (cfg / "docker-compose.yml").write_text("services: {}\n")
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    for name, body in (("podman", PODMAN), ("curl", CURL), ("uv", UV), ("podman-compose", "#!/bin/sh\necho \"compose $*\" >>\"$(dirname \"$0\")/../w/calls\"\n")):
        (bin_ / name).write_text(body)
        (bin_ / name).chmod(0o755)

    class World:
        dockerfile = repo / "docker/system-one/Dockerfile"

        def run(self, **env):
            e = {"PATH": f"{bin_}:{os.environ['PATH']}", "HOME": str(home), "W": str(w), "TMPDIR": str(tmp_path),
                 "LLM_ENV_SYSTEM_ONE_HEALTH_TIMEOUT_SECONDS": "2", "LAYA_VERSION": "0.4.27", **env}
            return subprocess.run(["bash", str(repo / "scripts/update-system-one.sh")], env=e, text=True,
                                  capture_output=True, timeout=120, check=False)

        def rendered(self, base):   # the compose make start last rendered, observer on top of `base`
            (w / "tags" / f"localhost_obs_{base}").write_text(f"obs-{base}")
            (cfg / "docker-compose.yml").write_text(f"services:\n  system-one:\n    image: localhost/obs:{base}\n")

        def calls(self):
            return (w / "calls").read_text().splitlines()

        def tag(self, ref):
            return (w / "tags" / ref.replace("/", "_").replace(":", "_")).read_text().strip()

        lock = w / "lock"

    return World()


def mutations(calls):
    return [c for c in calls if c.startswith(("podman tag", "podman stop", "podman rm old", "compose", "render"))]


def test_the_pinned_version_is_unchanged_and_touches_nothing(world):
    r = world.run(LAYA_VERSION="0.4.2")
    assert r.returncode == 0 and r.stdout.strip().splitlines()[-1] == "unchanged"
    assert not [c for c in world.calls() if c.startswith("podman")]


def test_busy_until_the_deadline_defers_after_building_with_production_untouched(world):
    r = world.run(IDLE="12.5")
    assert r.returncode == 75 and r.stdout.strip().splitlines()[-1] == "deferred: deadline"
    assert any("build" in c and "LAYA_VERSION=0.4.27" in c and "rocm-cand" in c for c in world.calls())
    assert mutations(world.calls()) == [] and world.tag(BASE) == "old-id"


def test_lock_contention_defers(world):
    world.lock.touch()
    holder = subprocess.Popen(["flock", str(world.lock), "sleep", "30"])
    try:
        while subprocess.run(["flock", "-n", str(world.lock), "true"], check=False).returncode == 0:
            pass
        r = world.run()
    finally:
        holder.kill()
        holder.wait()
    assert r.returncode == 75 and r.stdout.strip().splitlines()[-1] == "deferred: lock"


def test_a_failed_cpu_smoke_test_is_an_error_with_production_untouched(world):
    r = world.run(FAIL_PROBE_PORT="18001")
    assert r.returncode == 1 and "smoke test failed; production untouched" in r.stderr
    assert mutations(world.calls()) == [] and world.tag(BASE) == "old-id"
    assert any(c.startswith("podman rm -f llm-env-system-one-smoke-") for c in world.calls())


def test_a_failed_verify_rolls_back_to_the_previous_image(world):
    pin = world.dockerfile.read_text()
    r = world.run(VERIFY_RC="1")
    assert r.returncode == 1, r.stderr
    assert "update failed at stage: verify" in r.stderr and "rolled back to laya 0.4.2" in r.stderr
    assert world.tag(BASE) == "old-id" and world.tag(BASE.replace(":rocm", ":rocm-prev")) == "old-id"
    assert [c for c in world.calls() if c.startswith("compose")][-1].endswith("system-one")
    assert world.dockerfile.read_text() == pin


def test_success_promotes_the_candidate_and_rewrites_the_pin(world):
    r = world.run()
    assert r.returncode == 0, r.stderr
    out = r.stdout.strip().splitlines()
    assert out[-1] == "updated laya 0.4.2 -> 0.4.27"
    assert any(line.startswith("answers: largest probability change 0") for line in out)
    assert world.tag(BASE) == "cand-id" and world.tag(BASE.replace(":rocm", ":rocm-prev")) == "old-id"
    assert "ARG LAYA_VERSION=0.4.27" in world.dockerfile.read_text()
    assert any(c.startswith("podman stop --time 10 old-cid") for c in world.calls())


def test_config_drift_at_the_pinned_version_recreates_through_the_idle_gate_without_a_build(world):
    world.rendered("old-id")
    r = world.run(LAYA_VERSION="0.4.2", DRIFT="environment LAYA_MODELS")
    assert r.returncode == 0, r.stderr
    out = r.stdout.strip().splitlines()
    assert "drift: environment LAYA_MODELS" in out and out[-1] == "updated system-one configuration"
    calls = world.calls()
    assert not any("podman build" in c or c == "render" for c in calls) and world.tag(BASE) == "old-id"
    assert any("system-one-cache-check.py prefetch" in c for c in calls)
    assert any(c.startswith("podman stop --time 10 old-cid") for c in calls)
    assert "ARG LAYA_VERSION=0.4.2" in world.dockerfile.read_text()


def test_a_failed_config_swap_is_reported_without_a_rollback(world):
    world.rendered("old-id")
    r = world.run(LAYA_VERSION="0.4.2", DRIFT="memory", VERIFY_RC="1")
    assert r.returncode == 1 and "no rollback for a config change" in r.stderr
    assert not any(c.startswith("podman tag") and "rocm-prev localhost" in c for c in world.calls())
