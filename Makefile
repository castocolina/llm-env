.PHONY: help prerequisites dev-setup setup setup-local-llm-agents start stop restart check-setup check-server check-with-agents benchmark \
        key-reset show-secrets enable-boot disable-boot status gpu-status provider-provision fix-codex-context combo-context combo-backup combo-restore logs validate test clean prune

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

show-secrets:
	@bash tools/run-target.sh show-secrets -- bash scripts/show-secrets.sh

enable-boot:
	@bash tools/run-target.sh enable-boot -- bash setup/enable-boot.sh

disable-boot:
	@bash tools/run-target.sh disable-boot -- bash setup/disable-boot.sh

status:
	@bash tools/run-target.sh status -- bash scripts/status.sh

gpu-status:
	@bash tools/run-target.sh gpu-status -- bash scripts/gpu-status.sh

provider-provision:
	@bash tools/run-target.sh provider-provision -- bash scripts/provider-provision.sh

fix-codex-context:
	@bash tools/run-target.sh fix-codex-context -- bash scripts/omniroute-fix-context.sh

combo-context:
	@bash tools/run-target.sh combo-context -- bash scripts/omniroute-combo-context.sh $(COMBO)

combo-backup:
	@bash tools/run-target.sh combo-backup -- bash scripts/omniroute-combo-backup.sh $(OUTPUT)

combo-restore:
	@bash tools/run-target.sh combo-restore -- bash scripts/omniroute-combo-restore.sh $(INPUT) $(if $(OVERWRITE),--overwrite)

logs:
	@bash tools/run-target.sh logs -- bash scripts/logs.sh

validate:
	@bash tools/run-target.sh validate -- bash tools/validate.sh

test:
	@bash tools/run-target.sh test -- bash tools/test.sh

clean:
	@bash tools/run-target.sh clean -- bash scripts/clean.sh

prune:
	@bash tools/run-target.sh prune -- bash scripts/prune.sh
