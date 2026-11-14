PYTHON ?= python3
MODE ?= debug

.PHONY: setup build test test-real-core host-test check clean release setup-geargrafx integration integration-test irq-postaccept-boundary block-transfer-matrix

setup:
	$(PYTHON) tools/setup.py all

setup-geargrafx:
	bash scripts/setup-geargrafx.sh

build:
	$(PYTHON) tools/build.py --mode "$(MODE)"

# Required host gate (no Geargrafx needed): OSS_PCE_REQUIRE_CC65=1 turns any missing cc65 into a FAILURE, never a SKIP.
# Real-core checks are NOT run here; they are the required separate gate `test-real-core` (CI runs both).
test: build
	OSS_PCE_REQUIRE_CC65=1 $(PYTHON) -m unittest discover -s tests/host -v
	@echo 'INFO: real Geargrafx checks are not part of make test; run make test-real-core (informational only)'

# Required real-core gate (Issue #398): needs the pinned Geargrafx core. Fail-closed: a missing core is FAIL, never PASS.
test-real-core: build
	$(MAKE) integration MODE="$(MODE)"
	$(MAKE) integration-test

# Real-Geargrafx tests (fetch/build, real-core runs) live only in tests/integration (Issue #333).
integration-test:
	OSS_PCE_REQUIRE_CC65=1 $(PYTHON) -m unittest discover -s tests/integration -p 'test_*.py' -v

# Issue #366: manual debugger-only synthetic IRQ acceptance -> handler boundary proof.
# Not part of the ordinary libretro real-core gate: it needs the debugger step API.
irq-postaccept-boundary:
	$(PYTHON) tests/integration/geargrafx_irq_postaccept_boundary.py

# Local host-only run: no build, no integration. cc65-dependent tests SKIP with a reason when cc65 is absent;
# the required gate is `make test`.
host-test:
	$(PYTHON) -m unittest discover -s tests/host -v

integration: build
	$(PYTHON) tests/integration/geargrafx_smoke.py --rom "build/$(MODE)/smoke-test-not-bios.pce" --output "build/$(MODE)/geargrafx-evidence.json"
	$(PYTHON) tests/integration/geargrafx_reference_guard.py
	$(PYTHON) tests/integration/geargrafx_reference_guard_issue173.py
	$(PYTHON) tests/integration/geargrafx_mmio1804_trap.py
	$(PYTHON) tests/integration/geargrafx_mmio1804_preblock.py
	$(PYTHON) tests/integration/geargrafx_irq_register.py
	$(PYTHON) tests/integration/geargrafx_st_bypass.py
	$(PYTHON) tests/integration/geargrafx_bcdbin.py
	$(MAKE) block-write-guard
	$(MAKE) block-transfer-matrix
	$(MAKE) block-mid-refusal
	$(MAKE) stack-access-guard
	$(MAKE) stack-methods
	$(MAKE) stack-methods-compare
	$(MAKE) stack-s5
	$(MAKE) stack-s6
	$(MAKE) stack-irq-timing
	$(MAKE) stack-mpr-buffer
	$(MAKE) caller-stack-contract
	$(MAKE) st-boundary
	$(MAKE) block-preblock

# Issue #742 (#737 E4): block-transfer whole-instruction preblock (TII/TDD/TIN/TAI/TIA
# refused before the first step, synthetic media, debugger build, verify-only probe).
# Not a production mechanism. Runs in `integration`.
.PHONY: block-preblock
block-preblock:
	$(PYTHON) tests/integration/geargrafx_block_preblock.py

# Issue #365: ST0/ST1/ST2 single-step instruction-boundary monitor on the pinned core (v2 probe,
# synthetic media). Seventeen armed families (entry, unknown-PC, data-dependent branch, JSR/RTS return,
# page/bank boundaries, ROM page 0, guest-remapped MPR, fixed-JMP and BBR3 data-dependent
# hardware-page fail-closed entries, IRQ surveys), nine mutation kinds (22 runs), and a positive
# control. Fail-closed: any classifier rejection exits non-zero. Runs in `integration` (therefore `test-real-core` / required CI).
.PHONY: st-boundary
st-boundary:
	$(PYTHON) tests/integration/geargrafx_st_boundary.py

# Issue #654 (#473 S8): the TAM/TMA buffer (m_mpr_buffer) and TMA #$00 for single / multi-MPR TAM and TMA, TAM #$00,
# refused TAMs (A / A+D / A+E) and IRQ-in-TAM (synthetic media, debugger build, verification-only patch with a
# read-only accessor). Not a production mechanism. Runs in `integration`.
.PHONY: stack-mpr-buffer
stack-mpr-buffer:
	$(PYTHON) tests/integration/geargrafx_stack_mpr_buffer.py

# Issue #653 (#473 S7): IRQs newly asserted inside an instruction (timer / harness request bits at a chosen
# instruction or clock point) and where they are taken: TAM, STA to the VDC, the five block transfers (synthetic
# media, debugger build, verification-only patch). Not a production mechanism. Runs in `integration`.
.PHONY: stack-irq-timing
stack-irq-timing:
	$(PYTHON) tests/integration/geargrafx_stack_irq_timing.py

# Issue #648 (#473 S6): block transfers (TII/TIN/TDD/TIA/TAI) refused before TransferStart (pre-start range check)
# and during the transfer (MPR change), judged on the eight criteria; IRQ during a transfer (synthetic media,
# debugger build, verification-only patch). Not a production mechanism. Runs in `integration`.
.PHONY: stack-s6
stack-s6:
	$(PYTHON) tests/integration/geargrafx_stack_s6.py

# Issue #647 (#473 S5): decode-time TAM refusal (D) and IRQ deferral (E) on top of method A, all TAM bitmasks,
# IRQ1/IRQ2/TIQ with I = 0/1, TMA (synthetic media, debugger build, verification-only patch). Not a production
# mechanism. Runs in `integration`.
.PHONY: stack-s5
stack-s5:
	$(PYTHON) tests/integration/geargrafx_stack_s5.py

# PR #481 (Refs #473): caller-stack contract experiment on the unmodified pinned core (synthetic test-only HuCARD,
# needs cc65 from `make setup`). Runs in `integration` as the #481 regression check of #499/#500.
.PHONY: caller-stack-contract
caller-stack-contract:
	$(PYTHON) tests/integration/geargrafx_caller_stack_contract.py

# Issue #500 (#473 S4): methods A, C and A+C compared under the same conditions on the eight criteria
# (synthetic media, debugger build, verification-only patch). Not a production mechanism. Runs in `integration`.
.PHONY: stack-methods-compare
stack-methods-compare:
	$(PYTHON) tests/integration/geargrafx_stack_methods_compare.py

# Issue #498 (#473 S2, method A): instruction refusal before execution, judged on eight criteria, on the same
# verification-only core patch (synthetic media, debugger build). Not a production mechanism. Runs in `integration`.
.PHONY: stack-methods
stack-methods:
	$(PYTHON) tests/integration/geargrafx_stack_methods.py

# Issue #499 (#473 S3, method C): stack push/pop refused before the side effect via a verification-only core
# patch (synthetic media, debugger build). Not a production mechanism. Runs in `integration`.
.PHONY: stack-access-guard
stack-access-guard:
	$(PYTHON) tests/integration/geargrafx_stack_access_guard.py

# Issue #367: free-running pre-write refusal via a verification-only core patch (synthetic media). Runs in `integration`.
.PHONY: block-write-guard
block-write-guard:
	$(PYTHON) tests/integration/geargrafx_block_write_guard.py

# Issue #367: pinned multi-byte block transfer preflight matrix on Geargrafx b49ae82. Runs in `integration`.
.PHONY: block-transfer-matrix
block-transfer-matrix:
	$(PYTHON) tests/integration/geargrafx_block_transfer_matrix.py

# Issue #367: per-byte mid-transfer refusal (method B) and transfer-source read side effects on Geargrafx
# b49ae82, reusing the block-write-guard verification-only patch. Runs in `integration`.
.PHONY: block-mid-refusal
block-mid-refusal:
	$(PYTHON) tests/integration/geargrafx_block_midrefusal.py

check:
	$(PYTHON) tools/contracts.py
	$(PYTHON) tools/check.py
	$(PYTHON) tools/c3_static_check.py
	$(PYTHON) tools/verify_build_diff.py

# Issue #724: debug/release diff audit and verification-only/GPL non-contamination gate (static only).
# Runs inside `make check`; unmeasured free-run/libretro/release equivalence stays UNKNOWN.
.PHONY: verify-build-diff
verify-build-diff:
	$(PYTHON) tools/verify_build_diff.py
	$(PYTHON) tools/verify_build_diff.py --report

# Issue #335: same-input rebuild must be byte-identical and its manifest must describe the ROM (fail-closed).
rom-provenance:
	$(PYTHON) tools/rom_provenance.py --reproduce smoke --mode "$(MODE)"

clean:
	$(PYTHON) -c 'from pathlib import Path; import shutil; p = Path("build"); shutil.rmtree(p) if p.exists() else None'

release:
	@echo 'BLOCKED: BIOS APIs and System Card boot are unimplemented; no BIOS release is available.' >&2
	@exit 1

.PHONY: dev-rom dev-test dev-integration dev-sweep dev-bcdbin-sweep dev-mul16s-sweep dev-release-gate dev-release-gate-blocked
dev-rom:
	$(PYTHON) tools/build_rom.py --mode "$(MODE)"
dev-test: dev-rom
	OSS_PCE_REQUIRE_CC65=1 $(PYTHON) -m unittest discover -s tests/host -p 'test_api*.py' -v
	OSS_PCE_REQUIRE_CC65=1 $(PYTHON) -m unittest discover -s tests/host -p 'test_ma*.py' -v
	$(MAKE) dev-integration MODE="$(MODE)"
dev-integration: dev-rom
	$(PYTHON) tests/integration/geargrafx_api.py --rom "build/$(MODE)/bios-dev/dev-only-not-compatible-syscard3.pce"
	$(PYTHON) tests/integration/dev_getver_multi_input.py --rom "build/$(MODE)/bios-dev/dev-only-not-compatible-syscard3.pce"
	$(PYTHON) tests/integration/geargrafx_dev_bcd.py --rom "build/$(MODE)/bios-dev/dev-only-not-compatible-syscard3.pce"
	$(PYTHON) tests/integration/geargrafx_dev_ex_setwrt.py --rom "build/$(MODE)/bios-dev/dev-only-not-compatible-syscard3.pce"
	$(PYTHON) tests/integration/geargrafx_dev_joysns.py --rom "build/$(MODE)/bios-dev/dev-only-not-compatible-syscard3.pce"
	$(PYTHON) tests/integration/geargrafx_dev_joyrep.py --rom "build/$(MODE)/bios-dev/dev-only-not-compatible-syscard3.pce"
	$(PYTHON) tests/integration/geargrafx_dev_bgon.py --rom "build/$(MODE)/bios-dev/dev-only-not-compatible-syscard3.pce"
	$(MAKE) dev-bcdbin-sweep MODE="$(MODE)"
	$(PYTHON) tests/integration/geargrafx_dev_mul8u.py --rom "build/$(MODE)/bios-dev/dev-only-not-compatible-syscard3.pce"
	$(PYTHON) tests/integration/geargrafx_dev_mul16u.py --rom "build/$(MODE)/bios-dev/dev-only-not-compatible-syscard3.pce"
	$(PYTHON) tests/integration/geargrafx_dev_div16s.py --rom "build/$(MODE)/bios-dev/dev-only-not-compatible-syscard3.pce"
	$(PYTHON) tests/integration/geargrafx_dev_mul8s.py --rom "build/$(MODE)/bios-dev/dev-only-not-compatible-syscard3.pce"
	$(PYTHON) tests/integration/geargrafx_dev_div16u.py --rom "build/$(MODE)/bios-dev/dev-only-not-compatible-syscard3.pce"
	$(PYTHON) tests/integration/geargrafx_dev_mul16s.py --rom "build/$(MODE)/bios-dev/dev-only-not-compatible-syscard3.pce"
	$(PYTHON) tests/integration/geargrafx_dev_sqrt.py --rom "build/$(MODE)/bios-dev/dev-only-not-compatible-syscard3.pce"
	$(PYTHON) tests/integration/geargrafx_dev_sin.py --rom "build/$(MODE)/bios-dev/dev-only-not-compatible-syscard3.pce"
	$(PYTHON) tests/integration/geargrafx_dev_ex_rnd.py --rom "build/$(MODE)/bios-dev/dev-only-not-compatible-syscard3.pce"
	$(PYTHON) tests/integration/geargrafx_dev_cos.py --full --rom "build/$(MODE)/bios-dev/dev-only-not-compatible-syscard3.pce"
	$(PYTHON) tests/integration/geargrafx_dev_sin_cos.py --rom "build/$(MODE)/bios-dev/dev-only-not-compatible-syscard3.pce"
	$(MAKE) dev-sweep MODE="$(MODE)"
# Issue #494: manual large real-core sweep of the DEV ROM MA_MUL16S entry (slot 0x4F): every 65,536 multiplier x 6 multiplicands and every 65,536 multiplicand x 6 multipliers, at two caller SPs. Not run by CI.
dev-mul16s-sweep: dev-rom
	$(PYTHON) tests/integration/geargrafx_dev_mul16s.py --full --rom "build/$(MODE)/bios-dev/dev-only-not-compatible-syscard3.pce"
# Issue #442: full 0..255 x 18-condition real-core sweep of the DEV ROM EX_BCDBIN entry (slot $3D).
dev-bcdbin-sweep: dev-rom
	$(PYTHON) tests/integration/dev_bcdbin_full_sweep.py "build/$(MODE)/bios-dev/dev-only-not-compatible-syscard3.pce"
# Issue #434: full 0..255 real-core sweep of the DEV ROM EX_BINBCD entry (slot $3C).
dev-sweep: dev-rom
	$(PYTHON) tests/integration/dev_bcd_full_sweep.py "build/$(MODE)/bios-dev/dev-only-not-compatible-syscard3.pce"
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

.PHONY: dev-synthetic-track-select-integration
# Opt-in #607 follow-up: host CUE MODE1 selection + guest READ(6), not a BIOS TOC/API command.
dev-synthetic-track-select-integration: dev-cdread
	$(PYTHON) tests/integration/geargrafx_synthetic_track_select.py --mode "$(MODE)"

.PHONY: dev-cdra-compare
# Opt-in A/B of CD read-ahead ON vs OFF on the pinned core (exp/cd-readahead-compare). Diagnostic only, not boot.
dev-cdra-compare: dev-cdread
	$(PYTHON) tests/integration/geargrafx_cdra_compare.py --mode "$(MODE)"

.PHONY: dev-cdload-exec dev-cdload-exec-integration
# Opt-in Issues #14/#5 load-and-execute experiment; separate from the default DEV ROM and the other diagnostics.
dev-cdload-exec: dev-rom
	$(PYTHON) tools/build_cdload_exec.py --mode "$(MODE)"

dev-cdload-exec-integration: dev-cdload-exec
	$(PYTHON) tests/integration/geargrafx_cdload_exec.py --mode "$(MODE)"
.PHONY: dev-boot-internal dev-boot-internal-integration
# Issue #501: DEV-ONLY internal boot path (reset -> synthetic CD media check -> READ(6) -> RAM -> jump). NOT compatible,
# NOT a BIOS, NOT DEV_CALLABLE. Separate from the default DEV ROM and from the Issue #14/#5 cdload-exec experiment.
dev-boot-internal: dev-rom
	$(PYTHON) tools/build_dev_boot_internal.py --mode "$(MODE)"

dev-boot-internal-integration: dev-boot-internal
	$(PYTHON) tests/integration/geargrafx_dev_boot_internal.py --mode "$(MODE)"
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
.PHONY: dev-frame-tracer-cd-integration
# Issue #603: opt-in real-core bridge of the metadata-only RAM tracer and original synthetic CD-read diagnostic.
dev-frame-tracer-cd-integration: dev-cdread
	$(PYTHON) tests/integration/geargrafx_frame_tracer_cd.py --mode "$(MODE)"
.PHONY: dev-cdda dev-cdda-integration dev-cdda-scenarios-integration
# Opt-in Issue #15 CD-DA observation diagnostic (raw SCSI $D8/$DA/$D9/$DD, not the System Card CD_PLAY API).
dev-cdda: dev-rom
	$(PYTHON) tools/build_cdda.py --mode "$(MODE)"

dev-cdda-integration: dev-cdda
	$(PYTHON) tests/integration/geargrafx_cdda.py --mode "$(MODE)"

# Opt-in Issue #215 additional CD-DA scenarios on the same probe (separate evidence file, cdda-scenarios-evidence.json).
dev-cdda-scenarios-integration: dev-cdda
	$(PYTHON) tests/integration/geargrafx_cdda_scenarios.py --mode "$(MODE)"

.PHONY: dev-cdsubq-integration
# Opt-in Issue #261 $DD Q-subchannel device observation on the #15 CD-DA probe (not the System Card CD_SUBQ ABI).
dev-cdsubq-integration: dev-cdda
	$(PYTHON) tests/integration/geargrafx_cdsubq.py --mode "$(MODE)"

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

.PHONY: dev-cd-stat-observation-integration
# Opt-in Issue #231 device-layer observation (real Geargrafx, synthetic discs); reuses the #144 media-state ROM.
dev-cd-stat-observation-integration: dev-cd-media-state
	$(PYTHON) tests/integration/geargrafx_cd_stat_observation.py --mode "$(MODE)"

.PHONY: routine-real-core
# Opt-in (#330): Linux-pinned real-core routine tests; not a dependency of test/integration/check.
routine-real-core:
	@[ "$$(uname -s)" = Linux ] || { echo 'BLOCKED: real-core target requires Linux' >&2; exit 1; }
	@[ -n "$(TOOLS_DIR)" ] || { echo 'ERROR: TOOLS_DIR is required (e.g. TOOLS_DIR=$$HOME/oss-pce-cd-bios-tools)' >&2; exit 1; }
	$(PYTHON) tests/integration/geargrafx_ex_binbcd.py --mode debug
	$(PYTHON) tests/integration/geargrafx_ma_mul8s.py
	$(PYTHON) tests/integration/geargrafx_ma_mul8u.py
	$(PYTHON) tests/integration/geargrafx_ma_mul16u.py
	$(PYTHON) tests/integration/geargrafx_ma_div16u.py
	$(PYTHON) tests/integration/geargrafx_ma_div16s.py
	$(PYTHON) tests/integration/geargrafx_bcdbin.py

.PHONY: experimental-bios-boot experimental-bios-boot-integration
# Explicit opt-in only: 256 KiB DEV experiment with original reset/init code and project-owned synthetic CD flow.
# NOT compatible with any System Card, NOT a BIOS ABI, and never a release artifact.
experimental-bios-boot: dev-rom
	@[ -n "$(EXPERIMENTAL_CUE)" ] || { echo 'BLOCKED: EXPERIMENTAL_CUE must name the exact synthetic CUE to mount' >&2; exit 1; }
	$(PYTHON) tools/build_experimental_bios_boot.py --mode "$(MODE)" --cue "$(EXPERIMENTAL_CUE)"

# Pinned Geargrafx synthetic CUE check; host selects INDEX 01, guest validates the record (not TOC/API).
experimental-bios-boot-integration: dev-rom
	$(PYTHON) tests/integration/geargrafx_experimental_track_contract.py --mode "$(MODE)"

.PHONY: dev-ipl-boot-integration
# Issue #668: DEV-only IPL boot from RESET (host selection record, guest PCEX validation, RAM, execute).
# Synthetic media only; one ROM, two synthetic IPLs. NOT compatible, NOT a BIOS ABI, NOT commercial.
dev-ipl-boot-integration: dev-rom
	$(PYTHON) tests/integration/geargrafx_dev_ipl_boot.py --mode "$(MODE)"

.PHONY: mpr-bank-probe ipl-loader-integration
# Issue #670 Phase 1: MPR2/MPR3 bank selection and 8 KiB boundary measurement (synthetic ROM, pinned Geargrafx).
# Not a BIOS, no commercial media, no reference BIOS. Requires `make setup` (cc65 + Geargrafx).
mpr-bank-probe:
	$(PYTHON) tests/integration/geargrafx_mpr_bank_probe.py --mode "$(MODE)"
# Issue #670 Phase 2: DEV-only multi-sector IPL loader (synthetic media, 8 KiB HuCard+CUE path). NOT a BIOS, NOT compatible.
ipl-loader-integration:
	$(PYTHON) tests/integration/geargrafx_ipl_loader.py --mode "$(MODE)"
# Issue #682: the same 22 loader cases inside a 256 KiB DEV image, through Geargrafx LoadBiosFromBuffer (CAP-checked).
# Synthetic media only. NOT a System Card BIOS, NOT a BIOS ABI, release_eligible=false.
.PHONY: dev-bios-ipl-loader-integration
dev-bios-ipl-loader-integration: dev-rom
	$(PYTHON) tests/integration/geargrafx_dev_bios_ipl_loader.py --mode "$(MODE)"
# Issue #692: detect the DEV unimplemented-API stub (api_unimplemented) on the pinned core, distinct from budget.
# Synthetic media only. NOT in CI: workflow run steps are pinned by the trusted guard allowlist (needs a main-only change).
.PHONY: api-unimplemented-integration
api-unimplemented-integration: dev-rom
	$(PYTHON) tests/integration/geargrafx_api_unimplemented.py --mode "$(MODE)"
# Issue #635: direct API-entry observation (WRAM->BIOS JSR/JMP, slot, entry regs). Synthetic controls only.
# Commercial runs are opt-in via --commercial in the same script (./rom/ auto-detection, 0 -> NOT_RUN) and are NOT in this target.
# NOT in CI: workflow run steps are pinned by the trusted guard allowlist (needs a main-only change).
.PHONY: api-entry-integration
api-entry-integration: dev-rom
	$(PYTHON) tests/integration/geargrafx_api_entry.py --mode "$(MODE)"
# Issues #756/#758: DEV slot RETURNING experiments (SIMULATED conditional observation). Synthetic only.
# Single-slot (02) and two-slot (02+33) variants with three-way synthetic differential.
# Commercial runs are opt-in via --commercial in the same script (./rom/ auto-detection) and are NOT in this target.
# NOT in CI: workflow run steps are pinned by the trusted guard allowlist (needs a main-only change).
.PHONY: slot02-return-integration
slot02-return-integration: dev-rom
	$(PYTHON) tests/integration/geargrafx_slot02_return.py --mode "$(MODE)"
# Issue #762 (Wave1-B, child of #635): PUBLIC/DEV loader-validation gap differential. Synthetic only.
# Same synthetic cue on both DEV and PUBLIC loader ROMs (HuCard path, no commercial media, no reference BIOS).
# NOT in CI: workflow run steps are pinned by the trusted guard allowlist (needs a main-only change).
.PHONY: public-dev-gap-integration
public-dev-gap-integration:
	$(PYTHON) tests/integration/geargrafx_public_dev_gap.py --mode "$(MODE)"
