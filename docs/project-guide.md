# Project guide

[← English README](../README.md) · [日本語版](project-guide.jp.md) · [Documentation hub](README.md)

## Goal

The project aims to reproduce **System Card 3.0-compatible software behavior** for PC Engine / TurboGrafx-CD using original code and appropriately licensed open-source components. It replaces BIOS software **only**: an emulator or compatible hardware implementation must provide the HuC6280 CPU, CD controller, expansion RAM, ADPCM, and other devices.

The target is **behavioral compatibility**, not bit-for-bit identity with proprietary System Card firmware. Planned validation starts with Japanese CD-ROM² and SUPER CD-ROM² software, then expands through independently verified API contracts, cross-emulator comparisons, actual gameplay, and saving/loading.

## Three distinct artifacts

| | Ordinary DEV ROM | G5 experimental BIOS | Future compatible BIOS |
| --- | --- | --- | --- |
| Primary purpose | Diagnostic routines and hardware experiments | PUBLIC IPL loader and experimental service calls | Retail System Card behavior |
| API surface | 18 project-defined DEV routines; 63 stubs | Selected experimental service wiring per builder | Services supported by verified reference ABI |
| Evidence | Host and real-core tests | Synthetic media and authorized local observation | Independent ABI and end-to-end game tests |
| End-user ready? | No | No | **Not yet implemented** |

A DEV routine is **not** automatically callable by commercial titles. Experimental BIOS wiring does not promote anything into the ordinary DEV or release builds.

## What works (snapshot: 2026-11-18, main fa694281)

- Produce deterministic HuCARD fixtures and a 256 KiB diagnostic DEV ROM with pinned cc65/Geargrafx tooling; check ROM identity and hardware/CPU behavior.
- Test the **18/81 project-defined development routines**, without asserting a single reference-BIOS-verified ABI (0/81).
- Load PUBLIC IPL INFO/BODY blocks, transfer code to RAM, and verify experimental `$02`, `$03`, and `$33` service integration on **synthetic** media.
- Reproduce a common unimplemented `$4A` halt on two locally authorized, read-only commercial CD observations.

### What does not work yet

- Boot a commercial game as a System Card replacement; no retail title screen has been observed (**G5_OBSERVED=false**).
- Claim reference ABI verification (0/81), owner-approved DEV_CALLABLE (0), or reference JSR permission (0/81).
- Ship a compatible BIOS (`release_eligible=false`; `make release` intentionally refuses).
- Analyze/execute reference BIOS code without separately approved provenance and safety permissions.

## Evidence terminology

| Term | Meaning |
| --- | --- |
| SYNTHETIC / DEV | Project-specific behavior on original fixtures |
| HISTORICALLY_OBSERVED / CURRENT_OBSERVED | Version-scoped, anonymized observations on authorized local media, **not compatibility** |
| SIMULATED_NEXT_API | Conditional progress with a fabricated return; **not** genuine service behavior |
| ABI VERIFIED | Independently substantiated interface, flags, RAM/MPR/stack, and side effects — **currently 0/81** |
| G5_OBSERVED | Directly observed retail title screen — **currently false** |
| BLOCKED / NOT_RUN | Unmet prerequisites or absent measurements; never PASS |

## Developer quick start

```sh
make setup
make dev-rom
make test
make test-real-core
make check
```

Development assumes Linux/WSL and the pinned Geargrafx and cc65 toolchains. Private media are not needed for setup or public CI, and any local run requires explicit opt-in and evidence sanitization. See [Building](building.md), [Testing](testing.md), and [Provenance](provenance.md).

## Longer-term evidence sequence

1. Review source originality, licenses, and provenance.
2. Validate behavior and failure cases on synthetic fixtures, including mutation testing.
3. Confirm cold/warm, RAM/CPU/MPR and IRQ effects on pinned Geargrafx real core.
4. Independently establish genuine reference ABI contracts.
5. Demonstrate retail title screens, inputs, gameplay, and save/load per title.
6. Satisfy reproducibility, safety, provenance, and release gates.

The number **18** counts DEV routines; it is not a count of completed steps 4–6.

[Status](status.md) · [Roadmap](roadmap.md) · [Architecture](architecture.md)
