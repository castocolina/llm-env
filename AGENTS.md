# Project Agent Instructions

## LLM Environment

Local llama.cpp router server on Bazzite, running as a rootless podman
quadlet with GPU acceleration. Configuration lives in `models.yml`.

## Commands

```bash
make help          # List all targets
make setup         # Interactive configuration
make start         # Start the server
make stop          # Stop the server
make restart       # Stop then start
make check-setup   # Offline validation
make check-server  # Online API contract validation
make benchmark     # Measure Vulkan vs ROCm, record the winner
make enable-boot   # Start at boot (opt-in)
make disable-boot  # Disable start at boot
make status        # Show service status
make logs          # Tail service logs
make validate      # shellcheck + ruff
make test          # Python test suite
make clean         # Remove everything
```

### Environment variables

- `LLM_ENV_ASSUME_YES=1` — run `make setup` / `make clean` unattended,
  accepting defaults instead of prompting.
- `LLM_ENV_ROTATE_KEY=1` — force `make setup` to rotate the API key instead
  of keeping the existing one.

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

Never hardcode a value that can be measured. GPU device, VRAM totals,
compositor usage, and backend choice are all detected at runtime.

## Detailed Instructions

- [Architecture](.agents/architecture.md)
