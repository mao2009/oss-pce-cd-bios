# Roadmap — aspirational milestones, no date guarantees

## M0 — Foundation
- [x] README, MIT license and initial research documentation
- [ ] Verify System Card ROM layout and entry/interrupt/banking requirements
- [ ] Select a reproducible, pinned HuC6280 assembly toolchain
- [ ] Document permitted sources, known API contracts and unresolved behaviors
- [ ] Establish original fixture and evidence schema

## M1 — Minimal software BIOS
- [ ] Build repeatable System Card-compatible image
- [ ] Verify Geargrafx loads the independently built image
- [ ] Reach reset/boot code checkpoint in the **real emulator core**
- [ ] Verify repeated runs and fail-closed negative cases

## M2 — CD-ROM² homebrew bring-up
- [ ] Implement necessary hardware/service initialization
- [ ] Detect self-authored CD image and read original data sectors
- [ ] Load/transfer to a self-authored CD program and execute it
- [ ] Compare important behavior in at least one other independent emulator

## M3 — Retail-compatible BIOS API services
- [ ] Enumerate System Card 3.0 callable interfaces with evidence
- [ ] Implement documented ABI and caller-visible side effects
- [ ] Exercise CD, IRQ, memory, storage, graphics/input/audio helpers as required by target software
- [ ] Validate named local commercial titles when legally available
- [ ] Publish limitations and regressions; preserve previous passes

## M4 — Broader compatibility
- [ ] Cover old CD-ROM² edge cases and regional variants
- [ ] Assess Arcade Card-related interface requirements separately
- [ ] Verify FPGA-compatible ROM loading in systems which accept third-party firmware
- [ ] Export behavior specs and tests reusable by future RetroRecompStudio

## Explicit boundaries

The goal is compatibility with commercial CD games, ideally broad/full coverage, but no such compatibility is yet established. A synthetic test, successful build, emulator load and first menu frame are different results.

The first release must require independently recorded evidence and verified source provenance.
