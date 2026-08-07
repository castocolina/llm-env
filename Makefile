.PHONY: help prerequisites dev-setup setup setup-local-llm-agents start stop restart check-setup check-server check-with-agents benchmark \
        key-reset enable-boot disable-boot status logs validate test clean

help:
	@bash tools/run-target.sh help -- bash scripts/help.sh

prerequisites:
	@bash tools/run-target.sh prerequisites -- bash setup/prerequisites.sh

dev-setup: prerequisites
	@bash tools/run-target.sh dev-setup -- bash setup/dev-setup.sh

setup:
	@bash tools/run-target.sh setup -- bash setup/setup.sh

setup-local-llm-agents:
	@bash tools/run-target.sh setup-local-llm-agents -- bash setup/setup-local-llm-agents.sh

start:
	@bash tools/run-target.sh start -- bash scripts/start.sh

stop:
	@bash tools/run-target.sh stop -- bash scripts/stop.sh

restart:
	@$(MAKE) --no-print-directory stop
	@$(MAKE) --no-print-directory start

check-setup:
	@bash tools/run-target.sh check-setup -- bash scripts/check-setup.sh

check-server:
	@bash tools/run-target.sh check-server -- bash scripts/check-server.sh

check-with-agents:
	@bash tools/run-target.sh check-with-agents -- bash scripts/check-with-agents.sh

benchmark:
	@bash tools/run-target.sh benchmark -- bash scripts/benchmark.sh

key-reset:
	@bash tools/run-target.sh key-reset -- bash scripts/key-reset.sh

enable-boot:
	@bash tools/run-target.sh enable-boot -- bash setup/enable-boot.sh

disable-boot:
	@bash tools/run-target.sh disable-boot -- bash setup/disable-boot.sh

status:
	@bash tools/run-target.sh status -- bash scripts/status.sh

logs:
	@bash tools/run-target.sh logs -- bash scripts/logs.sh

validate:
	@bash tools/run-target.sh validate -- bash tools/validate.sh

test:
	@bash tools/run-target.sh test -- bash tools/test.sh

clean:
	@bash tools/run-target.sh clean -- bash scripts/clean.sh
