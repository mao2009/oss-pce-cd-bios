# oss-pce-cd-bios

An independently authored, redistributable **PC Engine / TurboGrafx-CD System Card BIOS replacement**.

> **Status: planning / bootstrap only.** There is no bootable BIOS image, confirmed System Card 3.0 ABI implementation, CD-game compatibility result, or working emulator integration yet.

## Objectives

- Prioritize a software **Super System Card 3.0-compatible ROM** as the first target, serving CD-ROM² and SUPER CD-ROM² software where the emulated hardware configuration permits.
- Long-term aim: broad commercial CD title compatibility, starting with Japanese releases, and expansion to other regional releases and later Arcade Card-related requirements where technically applicable.
- Replace the **BIOS software**, not the PCE CD hardware: emulators or FPGA implementations supply the HuC6280 CPU, CD-ROM interface, additional RAM, ADPCM and other peripherals.
- Target **Geargrafx** first as the instrumented emulator/debugger; use another independent emulator for cross-checks. FPGA BIOS loading may be validated later.
- Produce original HuC6280 code, a reproducible ROM build and freely redistributable synthetic test fixtures.
- Share documentation, evidence formats and development methodology with [oss-mcd-bios](https://github.com/mao2009/oss-mcd-bios); keep console-specific code independent. The API/behavior specification may also inform a future RetroRecompStudio PCE runtime.

The goal is **behavioral compatibility**, not binary identity with any proprietary System Card. A homebrew disc boot is an intermediate milestone, **not** proof of retail compatibility.

## Non-goals (initially)

- Building a physical HuCARD or replacing the original console's RAM hardware.
- Shipping NEC/Hudson BIOS images, commercial game data, copyrighted artwork/audio, or disassembled/translated proprietary code.
- Pretending the project already passes tests or supports games.
- Guaranteeing every game or FPGA implementation works before empirical verification.

## Development sequence

1. Verify the System Card 3.0 ROM layout, banking, vectors, HuC6280 startup and documented API calling conventions.
2. Select and pin a HuC6280 assembler/linker and implement reproducible build/ROM checks.
3. Instrument **real Geargrafx core execution** with synthetic ROM/CD fixtures and headless traces; mocks remain separate.
4. Implement reset, essential System Card entry points, CD commands and disc boot.
5. Expand services and prove each compatible behavior with negative tests, independent emulator comparisons and named gameplay scenarios.

**Important:** references describe the System Card function jump table around CPU address `$E000`, but precise bank mapping, ABI and service behavior must be validated per revision. Geargrafx documentation lists a `syscard` BIOS type of 256 KiB; this is a *loader target*, not confirmation that any arbitrary 256 KiB image boots.

## Documentation

- [Architecture](docs/architecture.md)
- [ROM and API research status](docs/specification.md)
- [System Card 3.0 API-by-API Issue inventory](docs/api-inventory.md)
- [Geargrafx harness plan](docs/geargrafx.md)
- [Compatibility and evidence](docs/compatibility.md)
- [Originality and provenance policy](docs/provenance.md)
- [Roadmap](docs/roadmap.md)
- [Change-aware nightly developer ROM builds](docs/nightly.md)
- [Contributing](CONTRIBUTING.md)

## Current build

There is **no working BIOS build command yet**. Adding a source tree, CI badge or a file with the right size does not establish a valid BIOS. Check [the issue tracker](https://github.com/mao2009/oss-pce-cd-bios/issues) for work in progress.

## License

Original source code and original documentation in this repository are MIT-licensed; see [LICENSE](LICENSE). External documents and emulators retain their own licenses and must not be relicensed through this repository. Not affiliated with NEC, Hudson Soft, Konami or other trademark holders.
