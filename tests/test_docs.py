from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


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
    assert "redacted command" in quick_start
    assert "input" in quick_start
    assert "stdout" in quick_start
    assert "stderr" in quick_start
    assert "parsed value" in quick_start
    assert "expectation" in quick_start
    assert "verdict" in quick_start
    assert "llm_env_keep_check_artifacts=1" in quick_start
    assert "check-with-agents" in quick_start
    assert "fixed local prompt `reply with exactly: ready`" in quick_start
    assert "opt-in live check" in quick_start
    assert "independently fetch public weather and usd-to-clp data" in quick_start
    assert "vulkan vs rocm" not in readme
    assert "vulkan vs rocm" not in agent_instructions
    assert "rocm -> vulkan" not in architecture
    assert all("rocm" not in path.read_text().lower() for path in historical_docs)
