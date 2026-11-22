"""make prune-stale-images removes only this stack's stale images, unless --dry-run, and never forces anything."""

from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/prune-stale-images.sh"
OMNI = "docker.io/diegosouzapw/omniroute"
LLAMA = "ghcr.io/ggml-org/llama.cpp"

# Records every call; answers come from files in $FAKE_DIR. Each successful rmi shrinks the reported store by 1000 bytes.
FAKE_PODMAN = r"""#!/usr/bin/env bash
printf '%s\n' "$*" >> "$FAKE_DIR/calls"
case "$1 $2" in
    "images --format") cat "$FAKE_DIR/images.json"; exit 0 ;;
    "ps -a") [[ ! -f "$FAKE_DIR/ps-fails" ]] || exit 125; cat "$FAKE_DIR/containers.json"; exit 0 ;;
    "image inspect") awk -F'\t' -v r="$5" '$1 == r { print $2; found = 1 } END { exit !found }' "$FAKE_DIR/refs" ;;
    "system df") n=$(wc -l < "$FAKE_DIR/removed" 2>/dev/null || echo 0)
        printf '[{"Type":"Images","RawSize":%d}]\n' $((10000 - 1000 * n)); exit 0 ;;
esac
if [[ "$1" == rmi ]]; then
    if grep -qxF "$2" "$FAKE_DIR/refuse" 2>/dev/null; then echo "Error: image has dependent children" >&2; exit 2; fi
    echo "$2" >> "$FAKE_DIR/removed"; exit 0
fi
exit 0
"""


def _id(char: str) -> str:
    return char * 64


def _image(char: str, names: list[str] | None = None, digests: list[str] | None = None,
           labels: dict[str, str] | None = None, created: int = 100, containers: int = 0) -> dict[str, object]:
    return {"Id": _id(char), "Names": names, "RepoDigests": digests or [], "Labels": labels or {}, "Size": 1000,
            "Created": created, "Containers": containers}


# What each image is, the id it has, and whether it is a stale stack image (True) or must survive (False).
IMAGES = [
    _image("r", [f"{LLAMA}:server-rocm"]),                                                       # stale tag (refusal test)
    _image("a", ["localhost/llm-env-system-one-span-sink:old"], containers=1),                   # in use (running)
    _image("s", ["localhost/llm-env-old-tool:1"]),                                               # in use (stopped container)
    _image("b", ["localhost/llm-env-system-one:rocm"]),                                          # configured
    _image("p", ["localhost/llm-env-system-one:rocm-prev"]),                                     # System One rollback copy
    _image("n", [f"{OMNI}:next"]),                                                               # configured
    _image("o", [f"{OMNI}@sha256:{'1' * 64}"], [f"{OMNI}@sha256:{'1' * 64}"], created=100),      # older untagged: stale
    _image("q", [f"{OMNI}@sha256:{'2' * 64}"], [f"{OMNI}@sha256:{'2' * 64}"], created=200),      # newest untagged: rollback
    _image("l", [f"{OMNI}:latest"]),                                                             # stale tag
    _image("u", None, labels={"llm-env.gpu-arch": "gfx1201"}),                                   # superseded build: stale
    _image("d", ["localhost/llm-env-system-one-retired:abc"]),                                   # retired derivative: stale
    _image("x", ["docker.io/library/python:3.12-slim"]),                                         # other project, unused
    _image("z", None, labels={"io.buildah.version": "1.45.1"}),                                  # dangling, not ours
    _image("g", None, [f"{LLAMA}@sha256:{'3' * 64}"]),                                           # untagged llama pull: stale
    _image("c", ["localhost/llm-env-old-tool:2", "docker.io/other/project:1"]),                 # co-tagged with a foreign repo
]
STALE = {f"{LLAMA}:server-rocm", f"{OMNI}:latest", _id("o"), _id("u"), "localhost/llm-env-system-one-retired:abc", _id("g")}
KEPT = {
    "in-use-running": _id("a"), "in-use-stopped": _id("s"), "configured": _id("b"), "rollback-system-one": _id("p"),
    "configured-omniroute": _id("n"), "rollback-omniroute": _id("q"), "unrelated-project": _id("x"),
    "foreign-dangling": _id("z"), "co-tagged-foreign": _id("c"),
}


def _env(tmp_path: Path) -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    podman = bin_dir / "podman"
    podman.write_text(FAKE_PODMAN)
    podman.chmod(podman.stat().st_mode | stat.S_IXUSR)
    fake = tmp_path / "fake"
    fake.mkdir()
    (fake / "images.json").write_text(json.dumps(IMAGES))
    (fake / "containers.json").write_text(json.dumps([{"ImageID": _id("a"), "State": "running"},
                                                      {"ImageID": _id("s"), "State": "exited"}]))
    refs = {"localhost/llm-env-system-one:rocm": "b", "localhost/llm-env-system-one:rocm-prev": "p", f"{OMNI}:next": "n",
            f"{LLAMA}:server-vulkan": "v"}  # remote_setup's python image is absent from the store: protects nothing
    (fake / "refs").write_text("".join(f"{ref}\t{_id(char)}\n" for ref, char in refs.items()))
    config = tmp_path / "models.yml"
    config.write_text(f"gpu:\n  image: {LLAMA}:server-vulkan\nomniroute:\n  image: {OMNI}:next\n"
                      "remote_setup:\n  image: docker.io/library/python:3.13-alpine\n"
                      "system_one:\n  image: localhost/llm-env-system-one:rocm\n")
    compose = tmp_path / ".config/llm-env/docker-compose.yml"
    compose.parent.mkdir(parents=True)
    compose.write_text(f"services:\n  omniroute:\n    image: {OMNI}:next\n")
    return {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "FAKE_DIR": str(fake), "HOME": str(tmp_path),
            "LLM_ENV_CONFIG": str(config)}


def _run(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["bash", str(SCRIPT), *args], env=env, capture_output=True, text=True, check=False)


def _calls(env: dict[str, str]) -> list[str]:
    path = Path(env["FAKE_DIR"]) / "calls"
    return path.read_text().splitlines() if path.exists() else []


def _removals(env: dict[str, str]) -> set[str]:
    return {call.split(" ", 1)[1] for call in _calls(env) if call.startswith("rmi ")}


def test_dry_run_prints_the_table_and_removes_nothing(tmp_path: Path) -> None:
    env = _env(tmp_path)
    result = _run(env, "--dry-run")
    assert result.returncode == 0, result.stderr
    assert not _removals(env)
    assert "ACTION" in result.stdout and "REASON" in result.stdout and "dry run: nothing removed" in result.stdout
    assert f"remove  {OMNI}:latest" in result.stdout and "rollback copy" in result.stdout
    assert "candidates total 6.00kB" in result.stdout


def test_default_run_removes_exactly_the_stale_stack_images(tmp_path: Path) -> None:
    env = _env(tmp_path)
    result = _run(env)
    assert result.returncode == 0, result.stderr
    assert _removals(env) == STALE
    assert "freed 6.00kB" in result.stdout


@pytest.mark.parametrize("kind", sorted(KEPT))
def test_protected_and_foreign_images_are_never_removed(tmp_path: Path, kind: str) -> None:
    env = _env(tmp_path)
    _run(env)
    image = next(i for i in IMAGES if i["Id"] == KEPT[kind])
    targets = {KEPT[kind], *(image["Names"] or [])}  # type: ignore[misc]
    assert not targets & _removals(env), kind


def test_a_refusal_is_reported_and_the_run_continues(tmp_path: Path) -> None:
    env = _env(tmp_path)
    Path(env["FAKE_DIR"], "refuse").write_text(f"{LLAMA}:server-rocm\n")
    result = _run(env)
    assert result.returncode == 0, result.stderr
    assert f"kept     {LLAMA}:server-rocm  podman refused: Error: image has dependent children" in result.stdout
    assert _removals(env) == STALE  # attempted once, then every later candidate still went
    assert "removed 5 image(s), 1 refused" in result.stdout and "freed 5.00kB" in result.stdout


def test_an_unreadable_container_list_fails_closed(tmp_path: Path) -> None:
    env = _env(tmp_path)
    Path(env["FAKE_DIR"], "ps-fails").write_text("1")
    result = _run(env)
    assert result.returncode != 0 and "podman ps failed" in result.stderr
    assert not _removals(env)


def test_no_banned_command_is_ever_invoked(tmp_path: Path) -> None:
    env = _env(tmp_path)
    Path(env["FAKE_DIR"], "refuse").write_text(f"{OMNI}:latest\n")
    _run(env)
    _run(env)
    calls = _calls(env)
    assert calls
    for call in calls:
        words = call.split()
        assert words[0] in {"images", "ps", "image", "system", "rmi"}, call
        assert not {"-f", "--force", "--all", "prune", "reset", "migrate", "volume", "rm"} & set(words), call
        assert "-a" not in words or words[0] == "ps", call
        if words[0] == "system":
            assert words[1] == "df", call
        if words[0] == "image":
            assert words[1] == "inspect", call
        if words[0] == "rmi":
            assert len(words) == 2, call  # one plain target per call
    text = SCRIPT.read_text()
    for banned in ("system prune", "image prune", "rmi -f", "rmi --force", "podman volume", "podman rm ", "system reset",
                   "system migrate"):
        assert banned not in text


def test_make_removes_by_default_and_only_lists_with_dry_run_1() -> None:
    def recipe(*args: str) -> str:
        return subprocess.run(["/usr/bin/make", "--no-print-directory", "-n", "prune-stale-images", *args], cwd=ROOT,
                              capture_output=True, text=True, check=True).stdout
    assert "scripts/prune-stale-images.sh --dry-run" in recipe("DRY_RUN=1")
    assert "--dry-run" not in recipe() and "--dry-run" not in recipe("DRY_RUN=0")
