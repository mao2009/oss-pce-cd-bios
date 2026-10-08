# Architecture — proposal, not an implemented system

## Target

A self-contained **HuC6280 System Card 3.0-compatible BIOS ROM image** for software emulators and, if validated later, FPGA implementations capable of loading a third-party System Card image.

No emulator-resident BIOS interception should be required to use the resulting ROM. The emulator/FPGA owns the CPU, RAM, CD-ROM peripheral implementation, ADPCM, video and input. The ROM owns startup and guest-visible BIOS services. Physical HuCARD design and hardware expansion are out of scope.

## Responsibilities and boundaries

| Module (proposed) | Responsibility |
| --- | --- |
| `src/boot/` | Startup vectors, reset sequencing, validated image/banking metadata |
| `src/abi/` | BIOS jump/entry tables and calling conventions |
| `src/cd/` | CD-ROM status, commands, reads, boot protocol |
| `src/memory/` | System Card RAM usage, HuC6280 MPR/bank setup and persistent data where applicable |
| `src/services/` | Console-visible BIOS helper functions; graphics/input/audio routines only as needed |
| `src/regions/` | Explicit JP/US/EU behavior differences (initially JP); avoid duplicating common logic |
| `tests/fixtures/` | Original standalone ROM/CD programs and disc-image generators |
| `tools/` | Pinned assembler, binary validators, Geargrafx runner and sanitized evidence |
| `docs/` | Behavioral contracts, source provenance, release verification |

Directories are aspirational until code exists; no empty directory is required.

## Initial firmware boundaries

- HuC6280-specific CPU semantics (MPR mapping, vectors, stack, interrupts) are not the same as the Mega Drive's Motorola 68000.
- A System Card 3.0 image does not itself supply the additional physical RAM of SUPER CD-ROM². The **target emulator/FPGA must already model that expansion**.
- CD service API and CD controller/SCSI/ADPCM interaction must be validated separately; a mocked sector fetch on a PC cannot substitute for a guest CPU ROM reading the emulated CD subsystem.
- User program handoff and persistent BIOS work RAM may be externally observable by retail games; internal behavior is flexible only where observationally equivalent.
- The CPU-visible `$E000` function table described in third-party literature must be mapped/qualified with MPR/bank state. No addresses, opcode values or service signatures become normative merely by appearing in external documentation.
- Arcade Card is an extension milestone, not automatically solved by System Card 3.0.

## Design principles

1. Favor behaviorally compatible **original or properly licensed reused/adapted OSS** over reproducing proprietary ROM layout byte-for-byte. Prefer direct reuse to unnecessary reinvention; review each file's origin, license and technical fit before integration.
2. Expose and test calling conventions, register effects, memory side effects, timing/event ordering, error paths and reset/retry.
3. Avoid game-title checks as the first response to a bug; fix the general contract, retaining precise evidence of any necessary exception.
4. Build deterministic ROM images and require true emulator execution for functional claims.
5. Keep console-specific implementation out of generalized validation tooling so oss-mcd-bios and future RetroRecompStudio can reuse **methodology and schemas**, not unsafe binary dependencies.
6. Track unknowns explicitly; fail closed when tests or reference inputs are unavailable.

## Architecture milestone sequence

Format/ABI inventory → pinned toolchain + valid ROM → Geargrafx reset checkpoint → synthetic CD read → homebrew disc startup → named retail scenarios → independent emulator validation → optional FPGA.

No stage is currently complete.
