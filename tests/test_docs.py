from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CONTEXT_BOUNDARY_DOCS = (
    ROOT / "README.md",
    ROOT / "QUICK_START.md",
)


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
        "diagnostic artifacts; without it, the checks remove raw artifacts after "
        "displaying bounded, redacted excerpts."
    ) in quick_start
    assert "after printing their contents" not in quick_start
    assert "fixed local prompt `reply with exactly: ready`" in quick_start
    assert "opt-in live check" in quick_start
    assert "pi and opencode independently fetch public weather and usd-to-clp data" in quick_start


def test_docs_describe_local_client_setup_without_exposing_credentials() -> None:
    readme = (ROOT / "README.md").read_text()
    quick_start = (ROOT / "QUICK_START.md").read_text()

    assert "make setup-local-llm-agents" in readme
    assert "make setup-local-llm-agents" in quick_start
    assert "http://127.0.0.1:<omniroute.port>/v1" in quick_start
    assert all(name in quick_start for name in ("config.json", "opencode.json", "opencode.jsonc"))
    assert '"apiKey": "<API_KEY>"' not in quick_start
    assert "Paste that value" not in quick_start
    assert "pi --model router-env/<alias>" in quick_start
    assert "opencode --model router-env/<alias>" in quick_start


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


def test_docs_identify_agentic_gemma_default() -> None:
    readme = _normalized_markdown(ROOT / "README.md").lower()
    quick_start = _normalized_markdown(ROOT / "QUICK_START.md").lower()

    for document in (readme, quick_start):
        assert "agentic gemma 4 12b v2" in document
        assert "`gemma4`" in document


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
        "pi's global `enabledmodels` starts with the setup-selected local aliases "
        "in setup order, which defines its model cycle; stale router-env entries are "
        "removed, while other enabled models keep their order."
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



def test_architecture_documents_the_compose_lifecycle_files() -> None:
    architecture = (ROOT / ".agents/architecture.md").read_text().lower()

    assert "~/.config/llm-env/docker-compose.yml" in architecture
    assert "~/.config/systemd/user/llm-server.service" in architecture
    assert "podman compose" in architecture
    assert "containers/systemd/llm-server.container" not in architecture
    assert "quadlet" not in architecture


def test_no_stale_quadlet_references_outside_architecture() -> None:
    for relative_path in ("AGENTS.md", "README.md", "QUICK_START.md"):
        text = (ROOT / relative_path).read_text().lower()
        assert "quadlet" not in text, f"{relative_path} still mentions quadlet"


def _raw_line_starting_with(path: Path, prefix: str) -> str:
    matches = [line for line in path.read_text().splitlines() if line.startswith(prefix)]
    assert len(matches) == 1, f"expected one line starting with {prefix!r} in {path.name}"
    return matches[0]


def test_agents_commands_list_focused_lifecycle_targets() -> None:
    agents = ROOT / "AGENTS.md"
    normalized = _normalized_markdown(agents)

    for target in ("make start-llama", "make stop-llama", "make llama-enable MODEL=", "make llama-disable"):
        assert target in normalized
    assert "never stops a running container" in _raw_line_starting_with(agents, "make start ")
    assert "re-render" in _raw_line_starting_with(agents, "make restart ")
    assert "for the next `make restart` or boot" in normalized
    assert "for the next start or boot" not in normalized


def test_readme_and_architecture_describe_the_llama_toggle() -> None:
    readme = ROOT / "README.md"
    assert "## Local inference toggle and model swap" in readme.read_text().splitlines()
    assert "make stop-llama && make llama-enable MODEL=<alias> && make start-llama" in _normalized_markdown(readme)

    architecture = _normalized_markdown(ROOT / ".agents/architecture.md")
    for fragment in ("up -d --no-recreate", "down --remove-orphans", "--pull=never", "make stop-llama"):
        assert fragment in architecture

    quick_start = _normalized_markdown(ROOT / "QUICK_START.md")
    assert "make start-llama" in quick_start
    assert "make llama-enable MODEL=" in quick_start


def test_benchmark_docs_require_llama_stopped_and_config_unchanged() -> None:
    for document in ("README.md", "QUICK_START.md", "AGENTS.md"):
        normalized = _normalized_markdown(ROOT / document)
        assert "only after `make stop-llama`" in normalized, document
        assert "`yq '.gpu.backend, .gpu.image' ~/.config/llm-env/models.yml`" in normalized, document


def test_docs_describe_capacity_card_gate_and_local_combo() -> None:
    agents = (ROOT / "AGENTS.md").read_text()
    readme = (ROOT / "README.md").read_text()
    architecture = (ROOT / ".agents/architecture.md").read_text()

    assert "make model-capacity MODEL=<alias>" in agents
    assert "make model-interference" in agents
    assert "make local-combo" in agents

    for fragment in (
        "~/.config/llm-env/capacity/<alias>.json",
        "my-coding-lite",
        "router-env/my-coding-lite",
        "quant-headroom.sh",
        "no headroom for",
        "LLM_ENV_SCOUT_OUT",
        "GET /api/health",
    ):
        assert fragment in readme, fragment
    assert "LLM_ENV_SCOUT_OUT" in agents

    for fragment in (
        "pylib/capacity.py",
        "pylib/interference.py",
        "scripts/model-interference.sh",
        "scripts/omniroute-local-combo.sh",
        "ALIAS_PATTERN",
    ):
        assert fragment in architecture, fragment
