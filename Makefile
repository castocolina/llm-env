.PHONY: help all setup start stop test shell clean cache-status validate

CONTAINER_NAME = llm-env
WORKSPACE = $(HOME)/llm-workspace
CHECKPOINTS = $(WORKSPACE)/.cache/checkpoints

help:
	@echo "╔════════════════════════════════════════════════════════════╗"
	@echo "║           LLM Environment - Available Commands             ║"
	@echo "╠════════════════════════════════════════════════════════════╣"
	@echo "║ make all          Full setup + start + test                ║"
	@echo "║ make setup        Download/compile LLM environment         ║"
	@echo "║ make start        Start LLM server                        ║"
	@echo "║ make stop         Stop LLM server                         ║"
	@echo "║ make test         Run server and agent tests              ║"
	@echo "║ make shell        Enter distrobox container               ║"
	@echo "║ make cache-status Show build cache/checkpoints status     ║"
	@echo "║ make clean-cache  Clear all checkpoints (rebuild next)    ║"
	@echo "║ make clean        Remove container & workspace            ║"
	@echo "║ make validate     Run shellcheck on all .sh files         ║"
	@echo "╚════════════════════════════════════════════════════════════╝"

all: setup start test
	@echo "Full setup complete!"

setup:
	@echo "Starting LLM environment setup..."
	@bash setup.sh

start:
	@bash start.sh

stop:
	@bash stop.sh

test:
	@bash test.sh

shell:
	@if distrobox list | grep -q "$(CONTAINER_NAME)"; then \
		distrobox enter $(CONTAINER_NAME); \
	else \
		echo "Container $(CONTAINER_NAME) not found. Run 'make setup' first."; \
		exit 1; \
	fi

cache-status:
	@echo "Build Cache Status:"
	@if [ -d "$(CHECKPOINTS)" ]; then \
		echo "  Checkpoint directory exists"; \
		ls -1 "$(CHECKPOINTS)" 2>/dev/null | sed 's/^/    /'; \
	else \
		echo "  No checkpoints yet"; \
	fi
	@echo ""
	@echo "Workspace: $(WORKSPACE)"
	@if [ -d "$(WORKSPACE)/models" ]; then \
		echo "  Models: $$(ls -lh $(WORKSPACE)/models 2>/dev/null | tail -n +2 | awk '{print $$9, "(" $$5 ")"}')"; \
	fi

clean-cache:
	@echo "Clearing all build checkpoints..."
	@rm -rf "$(CHECKPOINTS)"
	@echo "Checkpoints cleared. Next run will rebuild from scratch."

clean:
	@echo "This will remove the entire LLM environment!"
	@echo "  Container: $(CONTAINER_NAME)"
	@echo "  Workspace: $(WORKSPACE)"
	@read -p "Are you sure? (yes/no) " confirm && [ "$$confirm" = "yes" ] || exit 1
	@distrobox rm -f $(CONTAINER_NAME) 2>/dev/null || true
	@rm -rf $(WORKSPACE)
	@echo "Cleanup complete."

validate:
	@command -v shellcheck >/dev/null 2>&1 || { echo "shellcheck not found. Install with: brew install shellcheck (macOS) or sudo dnf install ShellCheck (Fedora)"; exit 1; }
	@echo "Running shellcheck on all .sh files..."
	@shellcheck -s bash *.sh
	@echo "All shell scripts pass shellcheck."
