# Documentation hub

[← English README](../README.md) · [日本語README](../readme.jp.md) · [Project guide](project-guide.md) · [Implementation status](status.md)

> **Research / diagnostics only.** A PASS applies only to its stated evidence scope. DEV routine count, genuine System Card ABI parity, retail boot, and release eligibility are separate axes.

## Start here

| Document | Purpose |
| --- | --- |
| [Project guide](project-guide.md) · [日本語版](project-guide.jp.md) | Goals, capabilities, and distinctions between DEV, experimental, and compatible firmware |
| [Status](status.md) | DEV 18/81, ABI VERIFIED 0/81, release gate |
| [Roadmap](roadmap.md) | Evidence-based milestones and remaining blockers |
| [Project review (2026-11-18)](project-review-2026-11-18.md) | Strengths, gaps, and recommended next actions |
| [Compatibility](compatibility.md) | Requirements for demonstrating actual game compatibility |

## Build, run, and architecture

| Topic | Documents |
| --- | --- |
| Reproducible builds | [Building](building.md) · [Toolchain](toolchain.md) |
| Firmware architecture | [Architecture](architecture.md) · [System Card ROM](system-card-rom.md) |
| Emulator and test harness | [Geargrafx](emulator-geargrafx.md) · [Testing](testing.md) |
| IPL experiments | [PUBLIC IPL](g5-commercial-ipl-path-800.md) · [DEV IPL](dev-ipl-boot.md) |
| Visual design and localization | [README visual maintenance](readme-visuals.md) |

## APIs and ABI

| Topic | Documents |
| --- | --- |
| Candidate services | [81-slot API inventory](api-inventory.md) |
| Verification policy | [ABI contracts](abi-contracts.md) · [Verification tiers](abi-verification-tiers.md) |
| CD experiments | [CD_BASE](cd-base-contract.md) · [CD_READ](cd-read-contract.md) · [Commercial FIRST](cd-read-commercial-first-mode.md) |
| Development implementation examples | [EX_DSPOFF](ex-dspoff-real-cpu-g5.md) · [EX_GETVER](abi2-getver-observation.md) |

## Observations and evidence

| Topic | Documents |
| --- | --- |
| **2026-11-18 local commercial observations** | [G5 FIRST-mode re-observation](g5-commercial-reobservation-2026-11-18.md) |
| Earlier experiments | [G5 addendum](g5-evidence-2026-11-18.md) · [2026-11-13 snapshot](g5-progress-2026-11-13.md) |
| Evidence conventions | [Verdict schema](verdict-schema.md) · [Compatibility evidence](compat-evidence-schema.md) |
| Core, IRQ, and stack | [Geargrafx contract evidence](geargrafx-contract-evidence.md) · [MPR/stack](497-mpr1-stack-contract.md) |

## Safety and provenance

| Topic | Documents |
| --- | --- |
| Licensing and independent implementation | [Provenance](provenance.md) · [OSS audit](oss-audit-systemcard.md) |
| Release controls | [Fail-closed invariants](725-failclosed-invariants.md) · [P10 verification](725-p10-release-gate-verification.md) |
| Agent command safety | [Destructive command guard](claude-destructive-guard.md) |
| Advanced / archival material | `docs/contracts/` · `docs/design/` · `docs/evidence/` · `docs/research/` |

### Documentation and translation policy

- **English `README.md` is canonical.** `readme.jp.md` is its Japanese translation. Keep both updated in the same PR when status, badges, images, limitations, or links change.
- The image assets under `assets/readme/` are shared between languages; do not generate a second set of conflicting factual infographics.
- Dated 2026-11-06 and 2026-11-13 reports are **historical snapshots**, not evidence from the latest build.
- Do not publish commercial media identifiers, contents, hashes, raw traces, or proprietary disassembly.
- When implementation/evidence changes, update [Status](status.md), both READMEs, and [visuals](readme-visuals.md) together.
