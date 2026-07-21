# Project Agent Instructions

## LLM Environment

Automated setup for llama.cpp on Bazzite (Linux) and macOS with GPU acceleration.

## Commands

```bash
make setup-dev    # Full environment setup
make validate     # Run shellcheck on all .sh files
make shell        # Enter distrobox container
make cache-status # View checkpoint status
make clean-cache  # Clear checkpoints
make clean        # Remove everything
```

## Rules

After editing any `.sh` file, run `make validate` (shellcheck) before committing.

### Language

All output must be in English regardless of input language.

### Research Before Implementation

Do not assume. Before implementing anything that is not 100% clear:

1. Research the platform, API, or tool behavior first
2. Verify assumptions with documentation or experiments
3. If something is unclear or you are guessing, ask the user

Silent failures from unresearched assumptions waste more time than asking.

## Detailed Instructions

- [Setup & Platform Rules](.agents/setup-dev.md)
