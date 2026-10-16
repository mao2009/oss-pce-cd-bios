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

.PHONY: dev-rom dev-test dev-integration dev-release-gate dev-release-gate-blocked
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

# make masks the gate's exit code 3 as 2, so this wraps it: PASS only on exit 3 + 'BLOCKED:'.
dev-release-gate-blocked:
	$(PYTHON) tools/expect_exit.py 3 '^BLOCKED:' -- $(PYTHON) tools/build_rom.py --release-gate

.PHONY: dev-cdread dev-cdread-integration
# Opt-in Issue #5 CD sector-read hardware diagnostic; separate from the default DEV ROM and startup ROM.
dev-cdread: dev-rom
	$(PYTHON) tools/build_cdread.py --mode "$(MODE)"

dev-cdread-integration: dev-cdread
	$(PYTHON) tests/integration/geargrafx_cdread.py --mode "$(MODE)"

.PHONY: dev-cdload-exec dev-cdload-exec-integration
# Opt-in Issues #14/#5 load-and-execute experiment; separate from the default DEV ROM and the other diagnostics.
dev-cdload-exec: dev-rom
	$(PYTHON) tools/build_cdload_exec.py --mode "$(MODE)"

dev-cdload-exec-integration: dev-cdload-exec
	$(PYTHON) tests/integration/geargrafx_cdload_exec.py --mode "$(MODE)"
