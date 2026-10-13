# Specification inventory and validation ledger

**Status:** references and questions, **not** a complete or independently verified System Card 3.0 specification. Documentation reading is not hardware proof.


## API Issue inventory

[System Card 3.0 candidate API ledger](https://github.com/mao2009/oss-pce-cd-bios/blob/main/docs/api-inventory.md): **81 primary jump-table slots** (`$00-$50`) + **9 PSG_BIOS subfunction candidates**. Each is cross-linked to a separate open Issue. All behaviors still require ABI/provenance verification; several published sources disagree ($0D, $4A, $4C, $50) and $4D/$4E names are unofficial. This is an **issue inventory**, not a tested spec or implementation.

## Sources to review

| Source | Supported starting point | Limits |
| --- | --- | --- |
| [Zeograd System Card notes](https://www.zeograd.com/download/pce_bios.html) | Lists System Card 3.0 services and `$E000` jump table formula: offset is function-number × 3 | Author acknowledges incomplete coverage and use of reverse engineering. Provenance review per claim. |
| [Geargrafx](https://github.com/drhelius/Geargrafx) | Emulator support and source for HuC6280/CD subsystem observability | External implementation, not the normative hardware specification. Verify license before any code reuse. |
| [Geargrafx MCP](https://github.com/drhelius/Geargrafx/blob/main/MCP_README.md) | `load_bios` for `syscard` 256 KiB; execution control, CPU registers, memory, CD-ROM, trace tools | Loader/API availability alone does not show a custom BIOS boots or API semantics are right. |
| [oss-mcd-bios research](https://github.com/mao2009/oss-mcd-bios/tree/main/docs) | Evidence and clean implementation workflow | Mega-CD hardware/API constants are **not transferable** to PCE. |

Reference URLs may change; pin tested revisions and record accessed dates with individual claims.

## Items requiring concrete verification

| Topic | Working assumption | Verification required |
| --- | --- | --- |
| Image format | Target Geargrafx `syscard` loader expects 256 KiB | Determine exact ROM layout, bank order, reset vector, data/header conventions and behavior across emulators |
| Initial API surface | System Card 3.0 function jump table at CPU-visible `$E000` | Validate table mapping with MPR state; enumerate selectors, calls, preserved registers, scratch memory, errors |
| HuC6280 initialization | Reset, MPR and vector setup needed | Find minimal original fixture and startup checkpoint |
| CD-ROM | BIOS controls disc recognition, TOC/sector reads and boot | Separate hardware model from BIOS interface; observe timeouts and state transitions |
| Work RAM | May contain publicly observable BIOS work variables | Enumerate exact caller-visible regions with sources and tests |
| Audio/ADPCM | Some games may rely on BIOS service behavior | Verify API and hardware ownership in scope |
| Region | JP target first | Distinguish disc release, console mode, video clock and BIOS presentation |
| Backward compatibility | System Card 3.0 intended to support much CD-ROM² software | Measure old System Card assumptions; do not claim complete backward compatibility |
| Arcade Card | Later target only | Separate dedicated RAM/bank access and BIOS requirements |

## API-contract record template

For every confirmed function, record:

- `api_id` / human name / function-number candidate / CPU-visible entry and bank mapping
- Evidence URLs with **exact revision**, claim origin, reviewer and permission status
- Inputs: registers, zero page, memory pointers, widths, valid ranges, preconditions
- Return: registers, flags, stack/PC, preserved registers
- Effects: RAM, VDC/PSG, CD-ROM/SCSI, ADPCM, callbacks, IRQ and work variables
- Timing: synchronous/asynchronous behavior, status polling, event order, reset/retry
- Failure: invalid argument, missing disc, unavailable hardware, read error, exhausted RAM
- Test fixture(s), game scenarios, emulator/BIOS ROM revision and evidence hash
- Separate `spec_status`, `provenance_status`, `implementation_status` and `verification_status`

Suggested status values: UNKNOWN / HYPOTHESIS / OBSERVED / CORROBORATED / VERIFIED-IN-SCOPE. A single public reverse-engineering article does not automatically mean APPROVED or VERIFIED.

## Compatibility definition

Do not equate a correct ROM size or jump table with working gameplay. The project needs independently demonstrated startup, reading, presentation, input, audio, saving and continued play on defined game editions.

A pure host-side HLE may inform the behavior contract but should not be the distributable firmware image.

## Scoped ROM and per-API records (#1/#3)

[ROM geometry/loader ledger](system-card-rom.md) and the
[81-slot ABI ledger](abi-contracts.md) distinguish manufacturer facts, pinned
core behavior, source-supported caller expectations and UNKNOWN callee effects.
[Expanded Geargrafx experiments](geargrafx-contract-evidence.md) observe original
CPU/RAM/IRQ procedures; they do not verify any BIOS API service.
