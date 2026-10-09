PYTHON ?= python3
MODE ?= debug

.PHONY: setup build test check clean release setup-geargrafx integration

setup:
	$(PYTHON) tools/setup.py all

setup-geargrafx:
	bash scripts/setup-geargrafx.sh

build:
	$(PYTHON) tools/build.py --mode "$(MODE)"

test: build
	$(PYTHON) -m unittest discover -s tests/host -v
	$(MAKE) integration MODE="$(MODE)"

integration: build
	$(PYTHON) tests/integration/geargrafx_smoke.py --rom "build/$(MODE)/smoke-test-not-bios.pce" --output "build/$(MODE)/geargrafx-evidence.json"

check:
	$(PYTHON) tools/contracts.py
	$(PYTHON) tools/check.py

clean:
	$(PYTHON) -c 'from pathlib import Path; import shutil; p = Path("build"); shutil.rmtree(p) if p.exists() else None'

release:
	@echo 'BLOCKED: BIOS APIs and System Card boot are unimplemented; no BIOS release is available.' >&2
	@exit 1

.PHONY: dev-rom dev-test dev-integration dev-release-gate
dev-rom:
	$(PYTHON) tools/build_rom.py --mode "$(MODE)"
dev-test: dev-rom
	$(PYTHON) -m unittest discover -s tests/host -p 'test_api*.py' -v
	$(MAKE) dev-integration MODE="$(MODE)"
dev-integration: dev-rom
	$(PYTHON) tests/integration/geargrafx_api.py --rom "build/$(MODE)/bios-dev/dev-only-not-compatible-syscard3.pce"
dev-release-gate:
	$(PYTHON) tools/build_rom.py --release-gate

.PHONY: dev-startup dev-startup-integration
# Opt-in Issue #4 startup checkpoint; deliberately separate from the default DEV ROM.
dev-startup: dev-rom
	$(PYTHON) tools/build_startup.py --mode "$(MODE)"

dev-startup-integration: dev-startup
	$(PYTHON) tests/integration/geargrafx_startup.py --mode "$(MODE)"
