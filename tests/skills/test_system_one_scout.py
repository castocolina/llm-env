"""The system-one-scout skill: its eval fixtures agree with scripts/system-one-check.sh, and its docs stay in bounds."""
import json
import re
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / ".claude/skills/system-one-scout"
FIX = SKILL / "evals/files/system-one"


def _asserts(eval_id: str) -> str:
    evals = json.loads((SKILL / "evals/evals.json").read_text())["evals"]
    return " ".join(next(e for e in evals if e["id"] == eval_id)["assertions"])


def test_check_fixtures_match_their_assertions() -> None:
    env = {"SO_FIXTURE_DIR": str(FIX), "SO_DOCKERFILE": str(FIX / "Dockerfile"), "PATH": "/usr/bin:/bin"}
    r = subprocess.run(["bash", str(ROOT / "scripts/system-one-check.sh")], env=env, text=True, capture_output=True,
                       timeout=60, check=False)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert (out["pinned"]["laya"], out["latest"]["laya"], out["latest"]["releases_behind"]) == ("0.4.2", "0.5.0", 2)
    assert out["torch_latest_per_rocm_index"]["rocm7.14"] == "2.15.0"
    assert out["weights"]["reviewed_revisions_changed"] is False and out["dependencies"]["added"] == ["vegaml"]
    assert all(s in _asserts("system-one-check") for s in ("0.4.2", "0.5.0", "2 releases behind", "rocm7.14", "vegaml"))


def test_eval_files_exist_and_description_fits_the_spec() -> None:
    evals = json.loads((SKILL / "evals/evals.json").read_text())["evals"]
    assert [p for e in evals for p in e["files"] if not (SKILL / p).is_file()] == []
    meta = yaml.safe_load((SKILL / "SKILL.md").read_text().split("---")[1])
    assert meta["name"] == "system-one-scout" and 0 < len(meta["description"]) <= 1024


def test_docs_name_only_existing_scripts_and_references() -> None:
    docs = [SKILL / "SKILL.md", *sorted((SKILL / "references").glob("*.md"))]
    text = "\n".join(p.read_text() for p in docs)
    scripts = set(re.findall(r"\bscripts/[\w-]+\.sh\b", text))
    assert "scripts/system-one-check.sh" in scripts and all((ROOT / s).is_file() for s in scripts)
    refs = set(re.findall(r"references/[\w-]+\.md", (SKILL / "SKILL.md").read_text()))
    assert refs == {f"references/{p.name}" for p in (SKILL / "references").glob("*.md")}


def test_trigger_queries_are_balanced_with_near_miss_negatives() -> None:
    rows = json.loads((SKILL / "evals/trigger-queries.json").read_text())
    pos = [r["query"].lower() for r in rows if r["should_trigger"]]
    neg = [r["query"].lower() for r in rows if not r["should_trigger"]]
    assert 8 <= len(pos) <= 10 and 8 <= len(neg) <= 10 and len(set(pos + neg)) == len(rows)
    for rx in (r"ask laya|use jev", r"onboard", r"memory cap", r"omniroute"):  # the Not-for neighbours
        assert any(re.search(rx, q) for q in neg), rx
