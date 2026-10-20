PYTHON ?= python3
MODE ?= debug

.PHONY: setup build test host-test check clean release setup-geargrafx integration integration-test

setup:
	$(PYTHON) tools/setup.py all

setup-geargrafx:
	bash scripts/setup-geargrafx.sh

build:
	$(PYTHON) tools/build.py --mode "$(MODE)"

# Required gate: OSS_PCE_REQUIRE_CC65=1 turns any missing cc65 into a FAILURE, never a SKIP.
test: build
	OSS_PCE_REQUIRE_CC65=1 $(PYTHON) -m unittest discover -s tests/host -v
	$(MAKE) integration MODE="$(MODE)"
	$(MAKE) integration-test

# Real-Geargrafx tests (fetch/build, real-core runs) live only in tests/integration (Issue #333).
integration-test:
	OSS_PCE_REQUIRE_CC65=1 $(PYTHON) -m unittest discover -s tests/integration -p 'test_*.py' -v

# Local host-only run: no build, no integration. cc65-dependent tests SKIP with a reason when cc65 is absent;
# the required gate is `make test`.
host-test:
	$(PYTHON) -m unittest discover -s tests/host -v

integration: build
	$(PYTHON) tests/integration/geargrafx_smoke.py --rom "build/$(MODE)/smoke-test-not-bios.pce" --output "build/$(MODE)/geargrafx-evidence.json"
	$(PYTHON) tests/integration/geargrafx_reference_guard.py
	$(PYTHON) tests/integration/geargrafx_mmio1804_trap.py
	$(PYTHON) tests/integration/geargrafx_mmio1804_preblock.py
	$(PYTHON) tests/integration/geargrafx_irq_register.py
	$(PYTHON) tests/integration/geargrafx_st_bypass.py
	$(PYTHON) tests/integration/geargrafx_bcdbin.py

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
	OSS_PCE_REQUIRE_CC65=1 $(PYTHON) -m unittest discover -s tests/host -p 'test_api*.py' -v
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
.PHONY: dev-cdreset dev-cdreset-integration
# Opt-in Issue #10 CD_RESET bus-reset diagnostic (original ROM, not the System Card API); separate from the #5 read ROM.
dev-cdreset: dev-rom
	$(PYTHON) tools/build_cdreset.py --mode "$(MODE)"

dev-cdreset-integration: dev-cdreset
	$(PYTHON) tests/integration/geargrafx_cdreset.py --mode "$(MODE)"
.PHONY: dev-cd-base-integration
# Opt-in Issue #11: does the DEV ROM have an executable CD_BASE ($E006) callee? (INDEX01=150 fixture)
dev-cd-base-integration: dev-rom
	$(PYTHON) tests/integration/geargrafx_cd_base.py --rom "build/$(MODE)/bios-dev/dev-only-not-compatible-syscard3.pce"
.PHONY: dev-trace-integration
# Opt-in Issue #6 isolated trace harness: real-core positive + classified negatives on the CD read ROM.
dev-trace-integration: dev-cdread
	$(PYTHON) tests/integration/geargrafx_trace.py --mode "$(MODE)"
.PHONY: dev-cdda dev-cdda-integration dev-cdda-scenarios-integration
# Opt-in Issue #15 CD-DA observation diagnostic (raw SCSI $D8/$DA/$D9/$DD, not the System Card CD_PLAY API).
dev-cdda: dev-rom
	$(PYTHON) tools/build_cdda.py --mode "$(MODE)"

dev-cdda-integration: dev-cdda
	$(PYTHON) tests/integration/geargrafx_cdda.py --mode "$(MODE)"

# Opt-in Issue #215 additional CD-DA scenarios on the same probe (separate evidence file, cdda-scenarios-evidence.json).
dev-cdda-scenarios-integration: dev-cdda
	$(PYTHON) tests/integration/geargrafx_cdda_scenarios.py --mode "$(MODE)"

.PHONY: dev-reset-entry dev-reset-entry-integration
# Opt-in Issue #143 minimal reset entry; separate from the default DEV ROM and other diagnostics.
dev-reset-entry: dev-rom
	$(PYTHON) tools/build_reset_entry.py --mode "$(MODE)"

dev-reset-entry-integration: dev-reset-entry
	$(PYTHON) tests/integration/geargrafx_reset_entry.py --mode "$(MODE)"

.PHONY: dev-cd-media-state dev-cd-media-state-integration
# Opt-in Issue #144 media-state observation point called from the #143 reset entry.
dev-cd-media-state: dev-rom
	$(PYTHON) tools/build_cd_media_state.py --mode "$(MODE)"

dev-cd-media-state-integration: dev-cd-media-state
	$(PYTHON) tests/integration/geargrafx_cd_media_state.py --mode "$(MODE)"

.PHONY: routine-real-core
# Opt-in (#330): Linux-pinned real-core routine tests; not a dependency of test/integration/check.
routine-real-core:
	@[ "$$(uname -s)" = Linux ] || { echo 'BLOCKED: real-core target requires Linux' >&2; exit 1; }
	@[ -n "$(TOOLS_DIR)" ] || { echo 'ERROR: TOOLS_DIR is required (e.g. TOOLS_DIR=$$HOME/oss-pce-cd-bios-tools)' >&2; exit 1; }
	$(PYTHON) tests/integration/geargrafx_ex_binbcd.py --mode debug
	$(PYTHON) tests/integration/geargrafx_ma_mul8s.py
	$(PYTHON) tests/integration/geargrafx_ma_mul8u.py
	$(PYTHON) tests/integration/geargrafx_bcdbin.py
