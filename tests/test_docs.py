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
CONTEXT_BOUNDARY_DOCS = (
    ROOT / "README.md",
    ROOT / "QUICK_START.md",
    ROOT / "docs/superpowers/specs/2026-07-30-agent-context-favorites-design.md",
    ROOT / "docs/superpowers/plans/2026-07-30-agent-context-favorites.md",
)


def _has_invalid_relocated_script_reference(markdown: str) -> bool:
    for match in RELOCATED_SCRIPT_REFERENCE.finditer(DIRECTORY_LAYOUT.sub("", markdown)):
        path = match["path"].removeprefix("./")
        if path not in RELOCATED_SHELL_PATHS:
            return True
    return False


def _normalized_markdown(path: Path) -> str:
    return " ".join(path.read_text().split())


def test_current_docs_describe_vulkan_only_diagnostics() -> None:
    readme = (ROOT / "README.md").read_text().lower()
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
    assert "vulkan vs rocm" not in readme
    assert "vulkan vs rocm" not in agent_instructions
    assert "rocm -> vulkan" not in architecture
    assert all("rocm" not in path.read_text().lower() for path in historical_docs)


def test_quick_start_describes_check_diagnostics_without_layout_dependencies() -> None:
    quick_start = _normalized_markdown(ROOT / "QUICK_START.md").lower()

    assert "checks print their command, validation facts, and verdict." in quick_start
    assert "they omit empty diagnostic blocks" in quick_start
    assert (
        "`llm_env_keep_check_artifacts=1` retains only the redacted private "
        "diagnostic artifacts; without it, the checks remove them after printing their "
        "contents."
    ) in quick_start
    assert "fixed local prompt `reply with exactly: ready`" in quick_start
    assert "opt-in live check" in quick_start
    assert "pi and opencode independently fetch public weather and usd-to-clp data" in quick_start


def test_docs_describe_local_client_setup_without_exposing_credentials() -> None:
    readme = (ROOT / "README.md").read_text()
    quick_start = (ROOT / "QUICK_START.md").read_text()

    assert "make setup-local-llm-agents" in readme
    assert "make setup-local-llm-agents" in quick_start
    assert "http://127.0.0.1:<port>/v1" in quick_start
    assert all(name in quick_start for name in ("config.json", "opencode.json", "opencode.jsonc"))
    assert '"apiKey": "<API_KEY>"' not in quick_start
    assert "Paste that value" not in quick_start
    assert "pi --model local-llm-env/<alias>" in quick_start
    assert "opencode --model local-llm-env/<alias>" in quick_start


def test_readme_distinguishes_routable_models_from_resident_models() -> None:
    readme = _normalized_markdown(ROOT / "README.md").lower()

    assert "enabled models are all routable" in readme
    assert "keeps one model resident" in readme
    assert "model-switch latency" in readme
    assert "models_max always follows" not in readme


def test_docs_state_exact_runtime_token_limits() -> None:
    readme = _normalized_markdown(ROOT / "README.md")
    quick_start = _normalized_markdown(ROOT / "QUICK_START.md")

    assert "131,072-token" in readme
    assert "8,192 output tokens" in readme
    assert "nominal 122,880 tokens" in quick_start


def test_docs_state_clean_setup_uses_128k_q5_1_defaults() -> None:
    readme = _normalized_markdown(ROOT / "README.md").lower()
    quick_start = _normalized_markdown(ROOT / "QUICK_START.md").lower()

    for document in (readme, quick_start):
        assert "clean setup" in document
        assert "131,072-token context" in document
        assert "q5_1" in document


@pytest.mark.parametrize("path", CONTEXT_BOUNDARY_DOCS)
def test_context_docs_state_the_measured_strict_prompt_boundary(path: Path) -> None:
    document = _normalized_markdown(path)

    assert "post-template prompt tokens must be less than `n_ctx`" in document
    assert "131,071" in document
    assert "`max_tokens: 1`" in document
    assert "131,072 and above are rejected" in document


def test_quick_start_ties_pi_model_cycle_to_setup_selection_order() -> None:
    quick_start = _normalized_markdown(ROOT / "QUICK_START.md").lower()

    assert (
        "pi's global `enabledmodels` becomes exactly the setup-selected local aliases "
        "in setup order, which defines its model cycle."
    ) in quick_start


def test_quick_start_ties_opencode_favorites_to_setup_selection() -> None:
    quick_start = _normalized_markdown(ROOT / "QUICK_START.md").lower()

    assert (
        "opencode favorites start with the setup-selected local aliases in setup order; "
        "stale local favorites are removed, while unrelated favorites retain their order."
    ) in quick_start


def test_quick_start_states_the_one_slot_runtime_contract() -> None:
    quick_start = _normalized_markdown(ROOT / "QUICK_START.md").lower()

    assert (
        "the deployment uses one request slot with q5_1 k/v caches, `fit = off`, and "
        "`context-shift = off`."
    ) in quick_start


def test_quick_start_explains_client_restart_and_partial_failure_recovery() -> None:
    quick_start = _normalized_markdown(ROOT / "QUICK_START.md").lower()

    assert "close pi and opencode before running the command" in quick_start
    assert "restart both clients after the command succeeds" in quick_start
    assert (
        "if a replacement fails partway, keep pi and opencode closed, rerun "
        "`make setup-local-llm-agents`, and restart both clients only after it succeeds."
    ) in quick_start


def test_architecture_describes_models_max_as_a_residency_limit() -> None:
    architecture = _normalized_markdown(ROOT / ".agents/architecture.md").lower()

    assert "enabled-model count" not in architecture
    assert "validated residency limit" in architecture


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
