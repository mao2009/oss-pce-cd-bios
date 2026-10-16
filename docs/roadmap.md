# Roadmap — evidence-graded milestones (no date promises)

Use [status](status.md) to distinguish actual **main** code from Draft PRs.
A completed **diagnostic** is not an implemented **compatible BIOS API**.

## M0 — Foundation and diagnostics

- [x] Reproducible original HuCARD fixture, pinned tools, host tests and real Geargrafx CI
- [x] Original 256 KiB System Card **development diagnostic** with 81 candidate entries and fail-closed stubs
- [x] Real-core System Card loader/reset, slot, RAM/MPR/stack/IRQ observations and isolated startup checkpoint
- [x] Real-core guest READ(6) on synthetic discs, 1–3 2048-byte sectors, full RAM/phase/negative checks
- [x] Research, provenance/OSS audit and candidate API ledger
- [ ] Verify historical/production ROM mapping/vectors and essential **caller ABI** against independent evidence (#1/#3)

## M1 — First genuine callable firmware services

- [ ] Implement actual System Card initialization instead of development reset checkpoint (#4)
- [ ] Implement essential caller-visible API (`CD_RESET`, `CD_BASE`, `CD_READ` as contracts permit) with input/output, flags, RAM/MPR/stack/IRQ, errors and negative tests
- [ ] Verify real caller → callee → return execution; preserve strict release gates

## M2 — Self-authored CD program boot

- [x] Diagnostic guest sector read (not a BIOS service)
- [ ] Review/merge [Draft #122](https://github.com/mao2009/oss-pce-cd-bios/pull/122): CD→RAM payload execution/return (**not** disc boot)
- [ ] Implement BIOS-mediated disc recognition, code load and execution handoff
- [ ] Boot one original CD program **through the firmware startup/service path**
- [ ] Cross-check key behavior in another independent emulator

## M3 — Retail compatibility

- [ ] Implement verified CD/IRQ/memory/graphics/input/audio caller-visible services as needed
- [ ] Test named lawful local retail editions without distributing commercial inputs
- [ ] Verify separate audio/input/save/play scenarios and regressions
- [ ] Release only after source provenance, ABI and independent evidence gates are met

## M4 — Wider support

- [ ] Older CD-ROM² and regional behaviors; Arcade Card separately
- [ ] Optional FPGA-loading validation; share approved evidence methods with other projects

## Boundaries

ROM size, loader acceptance, diagnostic PC checkpoint, synthetic sector read,
RAM payload execution, BIOS service call, homebrew disc boot, retail boot and
playability are **different evidence milestones**. Geargrafx is not physical
hardware proof. `make release` stays blocked until compatibility gates pass.
