# Project Agent Instructions

## LLM Environment

Local llama.cpp router server on Bazzite, running as a rootless podman
compose stack with GPU acceleration, fronted by an OmniRoute gateway that
`make start` auto-configures against the local router. Configuration lives
in `models.yml`.

## Commands

```bash
make help                    # List all targets
make prerequisites           # Confirm and install Bazzite/Fedora host tools
make setup                   # Interactive configuration (GPU inference is opt-in)
make start                   # Start the server (or omniroute + remote-setup only)
make stop                    # Stop the server
make restart                 # Stop then start
make check-setup             # Offline validation
make check-server            # Online API contract validation
make check-with-agents       # Live Pi/OpenCode weather and USD-to-CLP checks
make benchmark                # Measure Vulkan throughput; CPU fallback exits nonzero
make gpu-status                # Show which process (if any) holds the GPU
make provider-provision        # Import local CLI provider sessions (Codex) into OmniRoute
make enable-boot              # Start at boot (opt-in)
make disable-boot             # Disable start at boot
make status                   # Show service status and print endpoints/credentials
make show-secrets              # Print endpoints/credentials without starting anything
make logs                      # Tail service logs
make setup-local-llm-agents    # Configure normal Pi/OpenCode client profiles
make key-reset                 # Rotate the server API key
make prune                     # Delete downloaded model files (not removed by clean)
make dev-setup                 # Install dev-only tooling
make validate                  # shellcheck + ruff
make test                      # Python test suite
make clean                     # Remove everything
```

### Environment variables

- `LLM_ENV_ASSUME_YES=1` — run `make setup` / `make clean` unattended,
  accepting defaults instead of prompting. `make gpu-status` is the
  exception: for its two remediation prompts specifically, this means
  "always decline", not "auto-accept" — those are the only prompts in the
  codebase that mutate the host outside the repo's own config (desktop
  launcher overrides, environment.d files).
- `LLM_ENV_ROTATE_KEY=1` — force `make setup` to rotate the API key instead
  of keeping the existing one.
- `LLM_ENV_NO_GPU=1` — seed `make setup`'s GPU-inference prompt default to
  "no" for unattended runs, configuring this host as an OmniRoute gateway +
  remote installer only (no local llama.cpp instance). Persisted as
  `llm_server.enabled` in `models.yml`; flip it and re-run `make setup`/
  `make start` to change modes later.

## Rules

After editing any `.sh` file, run `make validate`.
After editing any `.py` file, run `make validate && make test`.

Makefile target bodies longer than 3 lines must delegate to a `.sh` file.

Python is invoked only as `uv run llmenv.py <subcommand>`.

### Language

All output must be in English regardless of input language.

### Research Before Implementation

Do not assume. Before implementing anything that is not 100% clear:

1. Research the platform, API, or tool behavior first
2. Verify assumptions with documentation or experiments
3. If something is unclear or you are guessing, ask the user

Silent failures from unresearched assumptions waste more time than asking.

Never hardcode a value that can be measured. GPU device, VRAM totals, and
compositor usage are all detected at runtime. Benchmarking uses Vulkan only;
an unsuccessful Vulkan benchmark configures CPU fallback and exits nonzero.

## Detailed Instructions

- [Architecture](.agents/architecture.md)
