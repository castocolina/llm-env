import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY_LAYOUT = re.compile(r"(?ms)^## Directory Layout\n.*?(?=^## |\Z)")
RELOCATED_SHELL_PATHS = {
    "tools/lib.sh",
    "setup/disable-boot.sh",
    "setup/enable-boot.sh",
    "setup/network.sh",
    "setup/prerequisites.sh",
    "setup/render-unit.sh",
    "setup/setup.sh",
    "scripts/benchmark.sh",
    "scripts/check-server.sh",
    "scripts/check-setup.sh",
    "scripts/check-with-agents.sh",
    "scripts/clean.sh",
    "scripts/key-reset.sh",
    "scripts/start.sh",
    "scripts/stop.sh",
}
RELOCATED_SCRIPT_REFERENCE = re.compile(
    r"(?<![\w-])(?P<path>(?:\./)?(?:[\w.-]+/)*(?:"
    r"benchmark|check-server|check-setup|check-with-agents|clean|disable-boot|"
    r"enable-boot|key-reset|lib|network|prerequisites|render-unit|setup|start|stop"
    r")\.sh)\b"
)


def _has_invalid_relocated_script_reference(markdown: str) -> bool:
    for match in RELOCATED_SCRIPT_REFERENCE.finditer(DIRECTORY_LAYOUT.sub("", markdown)):
        path = match["path"].removeprefix("./")
        if path not in RELOCATED_SHELL_PATHS:
            return True
    return False


def test_current_docs_describe_vulkan_only_diagnostics() -> None:
    readme = (ROOT / "README.md").read_text().lower()
    quick_start = (ROOT / "QUICK_START.md").read_text().lower()
    agent_instructions = (ROOT / "AGENTS.md").read_text().lower()
    architecture = (ROOT / ".agents/architecture.md").read_text().lower()
    historical_docs = [
        ROOT / ".claude/handoffs/2026-07-28-064440-transparent-checks.md",
        ROOT / "docs/superpowers/plans/2026-07-25-llm-env-rearchitecture.md",
        ROOT / "docs/superpowers/plans/2026-07-27-transparent-checks-and-vulkan-only.md",
        ROOT / "docs/superpowers/specs/2026-07-25-llm-env-rearchitecture-design.md",
        ROOT / "docs/superpowers/specs/2026-07-26-setup-runtime-lifecycle-design.md",
        ROOT / "docs/superpowers/specs/2026-07-27-transparent-check-diagnostics-design.md",
    ]

    assert "vulkan-only benchmark" in readme
    assert "cpu fallback and exits nonzero" in readme
    assert (
        "every check prints its redacted command, input, stdout, stderr, parsed value,\n"
        "expectation, and verdict."
    ) in quick_start
    assert (
        "`llm_env_keep_check_artifacts=1` retains only the\n"
        "redacted private diagnostic artifacts; without it, the checks remove them\n"
        "after printing their contents."
    ) in quick_start
    assert "check-with-agents" in quick_start
    assert "fixed local prompt `reply with exactly: ready`" in quick_start
    assert "opt-in live check" in quick_start
    assert "independently fetch public weather and usd-to-clp data" in quick_start
    assert "vulkan vs rocm" not in readme
    assert "vulkan vs rocm" not in agent_instructions
    assert "rocm -> vulkan" not in architecture
    assert all("rocm" not in path.read_text().lower() for path in historical_docs)


@pytest.mark.parametrize(
    "path",
    [
        ROOT / path
        for path in subprocess.check_output(
            ["git", "ls-files", "--", "*.md"], text=True
        ).splitlines()
    ],
)
def test_tracked_markdown_uses_relocated_script_paths(path: Path) -> None:
    assert not _has_invalid_relocated_script_reference(path.read_text()), path


def test_relocation_matcher_rejects_moved_scripts_outside_approved_directories() -> None:
    assert _has_invalid_relocated_script_reference("`start.sh`")
    assert _has_invalid_relocated_script_reference("`./start.sh`")
    assert _has_invalid_relocated_script_reference("`archive/start.sh`")


def test_relocation_matcher_permits_approved_and_unrelated_shell_paths() -> None:
    assert not _has_invalid_relocated_script_reference("`scripts/start.sh`")
    assert not _has_invalid_relocated_script_reference("`other-tool.sh`")
