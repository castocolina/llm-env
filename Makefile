.PHONY: help prerequisites setup start stop restart check-setup check-server check-with-agents benchmark \
        key-reset enable-boot disable-boot status logs validate test clean

UNIT = llm-server

help:
	@echo "make prerequisites Confirm and install host prerequisites"
	@echo "make setup         Interactive configuration"
	@echo "make start         Start the LLM server"
	@echo "make stop          Stop the LLM server"
	@echo "make restart       Restart the LLM server"
	@echo "make check-setup   Validate config, image, models, GPU (offline)"
	@echo "make check-server  Validate the running server API (online)"
	@echo "make check-with-agents Opt-in independent agent inference checks"
	@echo "make benchmark     Benchmark Vulkan vs ROCm and record results"
	@echo "make key-reset     Generate a new API key"
	@echo "make enable-boot   Start automatically at boot"
	@echo "make disable-boot  Do not start at boot"
	@echo "make status        Show service status"
	@echo "make logs          Follow service logs"
	@echo "make validate      Run shellcheck and ruff"
	@echo "make test          Run the Python test suite"
	@echo "make clean         Remove config, unit, and images"

prerequisites:
	@bash prerequisites.sh

setup:
	@bash setup.sh

start:
	@bash start.sh

stop:
	@bash stop.sh

restart: stop start

check-setup:
	@bash check-setup.sh

check-server:
	@bash check-server.sh

check-with-agents:
	@bash check-with-agents.sh

benchmark:
	@bash benchmark.sh

key-reset:
	@bash key-reset.sh

enable-boot:
	@bash enable-boot.sh

disable-boot:
	@bash disable-boot.sh

status:
	@systemctl --user status $(UNIT).service --no-pager || true

logs:
	@journalctl --user -u $(UNIT).service -f

validate:
	@shellcheck -s bash ./*.sh
	@uvx ruff check llmenv.py pylib tests
	@echo "All checks passed."

test:
	@uv run --with pytest pytest tests/ -v

clean:
	@bash clean.sh
