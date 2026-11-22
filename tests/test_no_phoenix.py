from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOKENS = ("pho" + "enix", "zai", "z." + "ai")
ALLOWLIST_FILES = {
    "docs/prds/omniroute-smart-routing-research/data-sources-quota.md",
}
ALLOWLIST = {"docs/superpowers", ".claude/handoffs"}


def test_retired_telemetry_is_absent_outside_history():
    offenders = []
    names = (
        path.relative_to(ROOT).as_posix()
        for path in ROOT.rglob("*")
        if path.is_file()
        and not any(part in {".git", ".venv", "__pycache__", "node_modules", ".pytest_cache", ".ruff_cache"} for part in path.relative_to(ROOT).parts)
        and not any(part in {".pi", ".superpowers", ".codegraph", "tmp"} for part in path.relative_to(ROOT).parts)
        and not path.relative_to(ROOT).as_posix().startswith("docs/superpowers/")
        and path.suffix != ".gz"  # compressed binary fixtures hold arbitrary bytes: a text scan gives false hits
    )
    for name in names:
        if not name:
            continue
        relative = Path(name).as_posix()
        if relative in {"tests/test_no_phoenix.py", "tests/test_no_zai.py"} or relative.startswith(".claude/worktrees/"):
            continue
        if relative in ALLOWLIST_FILES or any(relative == allowed or relative.startswith(allowed + "/") for allowed in ALLOWLIST):
            continue
        path = ROOT / relative
        if not path.is_file():
            continue
        content = path.read_text(errors="ignore").lower()
        if any(token in path.name.lower() or token in content for token in TOKENS):
            offenders.append(relative)
    assert not offenders, "Retired telemetry references remain: " + ", ".join(sorted(set(offenders)))
