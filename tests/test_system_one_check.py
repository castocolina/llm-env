"""scripts/system-one-check.sh offline (SO_FIXTURE_DIR): pins vs upstream, reviewed weight revisions, --save."""
import json
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "system-one-check.sh"
REV = '"convaiinnovations/laya": "{a}",\n    "convaiinnovations/laya-multilingual": "{b}",\n'


def fixtures(d: Path, latest_ml: str = "b" * 40) -> dict[str, str]:
    (d / "Dockerfile").write_text("ARG TORCH_VERSION=2.14.1\nARG TORCH_INDEX=rocm7.2\nARG LAYA_VERSION=0.4.2\n")
    releases = {v: [{"upload_time": f"2026-10-{i + 10}T00:00:00", "yanked": v == "0.4.4"}]
                for i, v in enumerate(["0.4.1", "0.4.2", "0.4.3", "0.4.4", "0.4.10"])}
    (d / "pypi.json").write_text(json.dumps({"info": {}, "releases": releases}))
    (d / "torch-index.html").write_text("".join(
        f'<a href="torch-{v}%2B{tag}-cp311-cp311-manylinux_2_28_x86_64.whl">w</a>\n'
        for v, tag in [("2.14.0", "rocm7.2"), ("2.14.1", "rocm7.2"), ("2.15.0", "rocm7.14"), ("2.14.1", "cpu")]))
    req = ["torch>=2.0.0", "fastapi>=0.110.0; extra == \"serve\"", "onnx; extra == \"onnx\""]
    (d / "pypi-0.4.2.json").write_text(json.dumps({"info": {"requires_dist": req}}))
    (d / "pypi-0.4.10.json").write_text(json.dumps({"info": {"requires_dist": [*req[1:], "Torch>=2.1", "vegaml>=0.15.0"]}}))
    (d / "revisions-pinned.py").write_text(REV.format(a="a" * 40, b="b" * 40))
    (d / "revisions-latest.py").write_text(REV.format(a="a" * 40, b=latest_ml))
    return {"SO_FIXTURE_DIR": str(d), "SO_DOCKERFILE": str(d / "Dockerfile"), "PATH": "/usr/bin:/bin", "HOME": str(d)}


def run(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["bash", str(SCRIPT), *args], env=env, text=True, capture_output=True, timeout=60, check=False)


def test_help_exits_zero_and_prints_usage() -> None:
    r = run({"PATH": "/usr/bin:/bin"}, "--help")
    assert r.returncode == 0 and r.stdout.startswith("system-one-check.sh") and "set -euo" not in r.stdout


def test_counts_releases_behind_and_newest_torch_per_index(tmp_path: Path) -> None:
    r = run(fixtures(tmp_path))
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["pinned"] == {"laya": "0.4.2", "torch": "2.14.1", "torch_index": "rocm7.2"}
    assert out["latest"] == {"laya": "0.4.10", "releases_behind": 2}, "yanked 0.4.4 must not count"
    assert out["torch_latest_per_rocm_index"] == {"rocm7.2": "2.14.1", "rocm7.14": "2.15.0"}
    assert out["github"]["since_pin"] is None and out["pin_watch"] == [], "offline fixtures skip gh"
    assert out["weights"] == {"reviewed_revisions_changed": False, "repos": []}
    assert out["dependencies"] == {"added": ["vegaml"], "removed": []}, "extras other than serve are not installed"


def test_a_moved_reviewed_revision_is_reported_by_repo(tmp_path: Path) -> None:
    out = json.loads(run(fixtures(tmp_path, latest_ml="c" * 40)).stdout)
    assert out["weights"] == {"reviewed_revisions_changed": True, "repos": ["convaiinnovations/laya-multilingual"]}


def test_unread_revisions_are_null_not_unchanged(tmp_path: Path) -> None:
    env = fixtures(tmp_path)
    (tmp_path / "revisions-latest.py").unlink()
    assert json.loads(run(env).stdout)["weights"] is None


def test_save_writes_a_stamped_report_to_the_state_dir(tmp_path: Path) -> None:
    env = {**fixtures(tmp_path), "XDG_STATE_HOME": str(tmp_path / "state")}
    r = run(env, "--save")
    saved = json.loads((tmp_path / "state/llm-env/system-one-check.json").read_text())
    assert saved["checked_at"].endswith("Z") and {k: v for k, v in saved.items() if k != "checked_at"} == json.loads(r.stdout)
