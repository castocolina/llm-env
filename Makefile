.PHONY: help prerequisites setup setup-local-llm-agents start stop restart check-setup check-server check-with-agents benchmark \
        key-reset enable-boot disable-boot status logs validate test clean

UNIT = llm-server

help:
	@bash scripts/help.sh

prerequisites:
	@bash setup/prerequisites.sh

setup:
	@bash setup/setup.sh

setup-local-llm-agents:
	@bash setup/setup-local-llm-agents.sh

start:
	@bash scripts/start.sh

stop:
	@bash scripts/stop.sh

restart: stop start

check-setup:
	@bash scripts/check-setup.sh

check-server:
	@bash scripts/check-server.sh

check-with-agents:
	@bash scripts/check-with-agents.sh

benchmark:
	@bash scripts/benchmark.sh

key-reset:
	@bash scripts/key-reset.sh

enable-boot:
	@bash setup/enable-boot.sh

disable-boot:
	@bash setup/disable-boot.sh

status:
	@systemctl --user status $(UNIT).service --no-pager || true

logs:
	@journalctl --user -u $(UNIT).service -f

validate:
	@shellcheck -s bash ./tools/*.sh ./setup/*.sh ./scripts/*.sh
	@uvx ruff check llmenv.py pylib tests
	@echo "All checks passed."

test:
	@uv run --with pytest pytest tests/ -v

clean:
	@bash scripts/clean.sh
