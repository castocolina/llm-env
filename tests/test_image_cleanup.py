"""Local image builds clean up what they supersede, and only that."""

from __future__ import annotations

import hashlib
import os
import stat
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LABELS = ("llm-env.gpu-arch", "llm-env.span-observer-sha256")
SINK = "localhost/llm-env-system-one-span-sink"

# Records every call; behaviour is driven by files in $FAKE_DIR so each test states exactly what podman answers.
FAKE_PODMAN = r"""#!/usr/bin/env bash
printf '%s\n' "$*" >> "$FAKE_DIR/calls"
case "$1 $2" in
    "image prune") [[ ! -f "$FAKE_DIR/prune-fails" ]] || exit 1; [[ ! -f "$FAKE_DIR/prune-output" ]] || cat "$FAKE_DIR/prune-output"; exit 0 ;;
    "image exists") [[ "$3" == "$BASE_IMAGE" ]] && exit 0; exit 1 ;;
    "image inspect") printf 'sha256:base\n'; exit 0 ;;
esac
case "$1" in
    images) cat "$FAKE_DIR/images" 2>/dev/null; exit 0 ;;
    rmi) grep -qxF "$2" "$FAKE_DIR/in-use" 2>/dev/null && exit 1; exit 0 ;;
    build) exit 0 ;;
esac
exit 0
"""


def _fake_env(tmp_path: Path) -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    podman = bin_dir / "podman"
    podman.write_text(FAKE_PODMAN)
    podman.chmod(podman.stat().st_mode | stat.S_IXUSR)
    fake = tmp_path / "fake"
    fake.mkdir()
    config = tmp_path / "models.yml"
    config.write_text("system_one:\n  image: localhost/llm-env-system-one:rocm\n")
    return {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "FAKE_DIR": str(fake),
            "BASE_IMAGE": "localhost/llm-env-system-one:rocm", "LLM_ENV_CONFIG": str(config), "HOME": str(tmp_path)}


def _calls(env: dict[str, str]) -> list[str]:
    path = Path(env["FAKE_DIR"]) / "calls"
    return path.read_text().splitlines() if path.exists() else []


def _run_helper(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["bash", "-c", f"source {ROOT}/tools/lib.sh; prune_superseded_build_images"],
                          env=env, capture_output=True, text=True, check=False)


def test_helper_prunes_only_images_carrying_our_build_labels(tmp_path: Path) -> None:
    env = _fake_env(tmp_path)
    result = _run_helper(env)
    assert result.returncode == 0, result.stderr
    assert _calls(env) == [f"image prune --force --filter label={label}" for label in LABELS]


def test_helper_never_runs_a_wider_removal(tmp_path: Path) -> None:
    # The untagged previous OmniRoute image is kept for rollback and carries neither label: no blanket prune, no force-remove.
    env = _fake_env(tmp_path)
    _run_helper(env)
    joined = "\n".join(_calls(env))
    for forbidden in ("--all", " -a", "system prune", "rmi", "volume", "--force-rm"):
        assert forbidden not in joined.replace("--force --filter", "")


def test_helper_reports_what_it_removed_and_survives_a_failing_prune(tmp_path: Path) -> None:
    env = _fake_env(tmp_path)
    Path(env["FAKE_DIR"], "prune-output").write_text("494c2c504841\n")
    assert "removed 1 superseded image" in _run_helper(env).stdout
    Path(env["FAKE_DIR"], "prune-fails").write_text("1")
    assert _run_helper(env).returncode == 0  # cleanup must never fail a build or an update


def _observer_key(base_id: str) -> str:
    source = (ROOT / "docker/system-one/observer.Dockerfile").read_bytes() + (ROOT / "docker/system-one/laya_span_observer.py").read_bytes()
    source_hash = hashlib.sha256(source).hexdigest()
    return hashlib.sha256(f"{base_id}\n{source_hash}\n".encode()).hexdigest()[:12]


def test_observer_build_removes_stale_derivatives_but_not_the_current_or_one_in_use(tmp_path: Path) -> None:
    env = _fake_env(tmp_path)
    current = f"{SINK}:{_observer_key('sha256:base')}"
    fake = Path(env["FAKE_DIR"])
    fake.joinpath("images").write_text(f"{SINK}:aaaaaaaaaaaa\n{current}\n{SINK}:bbbbbbbbbbbb\n")
    fake.joinpath("in-use").write_text(f"{SINK}:bbbbbbbbbbbb\n")  # a running container still uses this one
    result = subprocess.run(["bash", str(ROOT / "scripts/system-one-observer-build.sh")], env=env, capture_output=True,
                            text=True, check=False, cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == current
    removals = [line for line in _calls(env) if line.startswith("rmi ")]
    assert f"rmi {SINK}:aaaaaaaaaaaa" in removals and f"rmi {current}" not in removals
    assert f"rmi {SINK}:bbbbbbbbbbbb" in removals  # attempted; podman refuses it, which must not fail the build
    # scoped prune runs after the build
    assert _calls(env)[-len(LABELS):] == [f"image prune --force --filter label={label}" for label in LABELS]


def test_every_image_build_path_cleans_up_after_itself() -> None:
    build = (ROOT / "scripts/system-one-build.sh").read_text()
    assert build.index("podman build") < build.index("prune_superseded_build_images")
    observer = (ROOT / "scripts/system-one-observer-build.sh").read_text()
    assert observer.index("podman build") < observer.index("prune_superseded_build_images")
    updater = (ROOT / "scripts/update-system-one.sh").read_text()
    assert updater.index("if swap; then") < updater.index("prune_superseded_build_images")
    for text in (build, observer, updater, (ROOT / "tools/lib.sh").read_text()):
        assert "system prune" not in text and "image prune -a" not in text and "image prune --all" not in text
