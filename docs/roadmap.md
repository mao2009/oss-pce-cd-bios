# Roadmap — evidence-graded milestones (no date promises)

Use the [dated implementation status](status.md) (main `f90c16b`, committed 2026-10-22, state verified 2026-10-23: 9/81 DEV implementations, 72 stubs; owner-approved DEV_CALLABLE 0; 0/81 reference ABI VERIFIED; 0/81 reference JSR permitted) to distinguish merged **main** code,
real Geargrafx observations and unmerged work. Baseline: [PR #174](https://github.com/mao2009/oss-pce-cd-bios/pull/174)
merged as `05a0ea54` on 2026-10-17; later [#175](https://github.com/mao2009/oss-pce-cd-bios/pull/175)
updated public status wording and [#176](https://github.com/mao2009/oss-pce-cd-bios/pull/176)
added community Pages. A completed **diagnostic**, even one executing an original
OSS routine, is not proof of a **fully reference-compatible BIOS API** or release.

## M0 — Foundation and diagnostics

- [x] Reproducible original HuCARD fixture, pinned tools, host tests and real Geargrafx CI
- [x] Original 256 KiB System Card **development-only** ROM with 81 candidate entries: one callable `EX_GETVER` override and stub slots (historical [#174](https://github.com/mao2009/oss-pce-cd-bios/pull/174): 80 fail-closed nonreturning stubs as merged; current counts and dated snapshots are in [status](status.md))
- [x] Real-core System Card loader/reset, slot, RAM/MPR/stack/IRQ observations and isolated startup checkpoint with non-zero CD-state marker ([#127](https://github.com/mao2009/oss-pce-cd-bios/pull/127))
- [x] Real-core guest READ(6) on synthetic discs, 1–3 2048-byte sectors, full RAM/phase/negative checks
- [x] Isolated real-core Geargrafx trace harness, CD status classifier and host-only CD_BASE model (not BIOS services)
- [x] Research, provenance/OSS audit and candidate API ledger
- [x] CD_BASE ABI evidence ledger ([#128](https://github.com/mao2009/oss-pce-cd-bios/pull/128)): six Issue #11 items recorded; ABI remains UNKNOWN
- [ ] Verify historical/production ROM mapping/vectors and essential **caller ABI** against independent evidence (#1/#3)

## M1 — Callable development firmware services and ABI validation

- [x] Implement the first **development-only callable OSS** service: `EX_GETVER` at slot `$1E` (candidate entry `$E05A`), returning X=3, Y=0 and preserving A in the tested setup ([#174](https://github.com/mao2009/oss-pce-cd-bios/pull/174))
- [x] Observe DEV `EX_GETVER` caller → callee → RTS return and balanced SP in pinned real Geargrafx cold/warm runs; retain independent-rebuild ROM byte-identity checks and blocked release gate
- [x] Merge the **8th DEV-only API**: MA_DIV16U at $43 with independent model, real-core tests, flag-combination and mutation checks ([#480](https://github.com/mao2009/oss-pce-cd-bios/pull/480)); DEV-only and not approved/ABI VERIFIED
- [x] Merge the **9th DEV-only API**: MA_MUL16S at $4F ([#495](https://github.com/mao2009/oss-pce-cd-bios/pull/495), Issue #494); DEV-only and not approved/ABI VERIFIED
- [x] Merge the **DEV-only internal boot path** ([#502](https://github.com/mao2009/oss-pce-cd-bios/pull/502)) and its CI wiring ([#506](https://github.com/mao2009/oss-pce-cd-bios/pull/506)); synthetic CD → RAM → jump is not BIOS CD_BOOT
- [x] Record **synthetic caller-stack safety experiment** ([#481](https://github.com/mao2009/oss-pce-cd-bios/pull/481)); MPR1=$FF violates the stack contract and causes VDC side effects before the guard can refuse; production gate remains BLOCKED (#473/#459/#367)
- [ ] Independently validate the **full reference-BIOS `EX_GETVER` ABI** (including relevant flags/interrupts/conditions, provenance and cross-emulator evidence); [Issue #40](https://github.com/mao2009/oss-pce-cd-bios/issues/40) remains open; `abi: UNVERIFIED`
- [ ] Implement actual System Card initialization instead of the development reset checkpoint (#4)
- [ ] Implement callable `CD_RESET`, `CD_BASE`, `CD_READ` only as contracts permit, with inputs/outputs, flags, RAM/MPR/stack/IRQ, errors and negative tests. Current bus/host models are **not** callable BIOS services
- [ ] Test those new callers end-to-end, independently cross-check significant behaviors and satisfy all provenance/compatibility gates before any firmware release

## M2 — Self-authored CD program boot

- [x] Diagnostic guest sector read (not a BIOS service)
- [x] Merged [#122](https://github.com/mao2009/oss-pce-cd-bios/pull/122): CD→RAM payload execution/return diagnostic (**not** disc boot)
- [ ] Implement BIOS-mediated disc recognition, code load and execution handoff
- [ ] Boot one original CD program **through the firmware startup/service path**
- [ ] Cross-check key behavior in another independent emulator

## M3 — Retail compatibility

- [ ] Implement verified CD/IRQ/memory/graphics/input/audio caller-visible services as needed
- [ ] Plan and, after satisfying safety gates, instrument the PC Engine CD version of *Shin Megami Tensei* in Geargrafx to identify the first blocking API; **not tested/booted yet**
- [ ] Test named lawful local retail editions without distributing commercial inputs
- [ ] Verify separate audio/input/save/play scenarios and regressions
- [ ] Release only after source provenance, ABI and independent evidence gates are met

## M4 — Wider support

- [ ] Older CD-ROM² and regional behaviors; Arcade Card separately
- [ ] Optional FPGA-loading validation; share approved evidence methods with other projects

## Boundaries

ROM size, loader acceptance, diagnostic PC checkpoint, synthetic sector read,
RAM payload execution, DEV-only caller return, independently verified reference
BIOS ABI, homebrew disc boot, retail boot and playability are **different
milestones**. Geargrafx is not physical hardware proof. The guarded reference-ROM
90-function/270-scenario **plan** from [#170](https://github.com/mao2009/oss-pce-cd-bios/pull/170)
does not mean those tests have been run, and optional private reference firmware
is never a distributed or CI input. `make release` remains BLOCKED;
`release_eligible: false` until compatibility, provenance and independent
verification gates are satisfied. The 81-entry reference static profile ([#477](https://github.com/mao2009/oss-pce-cd-bios/pull/477)) and EX_GETVER observation schema ([#475](https://github.com/mao2009/oss-pce-cd-bios/pull/475)) are merged on main as evidence only, not reference ABI VERIFIED; historical headered ROM SHA identity remains unresolved in [#476](https://github.com/mao2009/oss-pce-cd-bios/issues/476).
