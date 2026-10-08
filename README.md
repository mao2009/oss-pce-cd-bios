# oss-pce-cd-bios

A redistributable **PC Engine / TurboGrafx-CD System Card BIOS replacement** built from original and properly licensed reusable OSS code.

> **Status: development environment / diagnostic ROMs only.** Pinned tools build an original HuCARD fixture and execute it in the real Geargrafx core. There is no bootable System Card BIOS, implemented BIOS API, or CD-game compatibility result.

## Objectives

- Prioritize a software **Super System Card 3.0-compatible ROM** as the first target, serving CD-ROM² and SUPER CD-ROM² software where the emulated hardware configuration permits.
- Long-term aim: broad commercial CD title compatibility, starting with Japanese releases, and expansion to other regional releases and later Arcade Card-related requirements where technically applicable.
- Replace the **BIOS software**, not the PCE CD hardware: emulators or FPGA implementations supply the HuC6280 CPU, CD-ROM interface, additional RAM, ADPCM and other peripherals.
- Target **Geargrafx** first as the instrumented emulator/debugger; use another independent emulator for cross-checks. FPGA BIOS loading may be validated later.
- Reuse appropriately licensed existing OSS code where technically suitable; adapt it to the HuC6280/System Card ABI or implement original code as needed. Produce reproducible ROM builds and redistributable synthetic test fixtures.
- Share documentation, evidence formats, tests, and reusable components with [oss-mcd-bios](https://github.com/mao2009/oss-mcd-bios) when technically and legally appropriate; isolate console-specific behavior. The API/behavior specification may also inform a future RetroRecompStudio PCE runtime.

The goal is **behavioral compatibility**, not binary identity with any proprietary System Card. A homebrew disc boot is an intermediate milestone, **not** proof of retail compatibility.

## Non-goals (initially)

- Building a physical HuCARD or replacing the original console's RAM hardware.
- Shipping NEC/Hudson BIOS images, commercial game data, copyrighted artwork/audio, or disassembled/translated proprietary code.
- Pretending the project already passes tests or supports games.
- Guaranteeing every game or FPGA implementation works before empirical verification.

## Development sequence

1. Verify the System Card 3.0 ROM layout, banking, vectors, HuC6280 startup and documented API calling conventions.
2. Maintain the implemented pinned HuC6280 assembler/linker and reproducible fixture build/ROM checks.
3. Instrument **real Geargrafx core execution** with synthetic ROM/CD fixtures and headless traces; mocks remain separate.
4. Implement reset, essential System Card entry points, CD commands and disc boot.
5. Expand services and prove each compatible behavior with negative tests, independent emulator comparisons and named gameplay scenarios.

**Important:** references describe the System Card function jump table around CPU address `$E000`, but precise bank mapping, ABI and service behavior must be validated per revision. Geargrafx documentation lists a `syscard` BIOS type of 256 KiB; this is a *loader target*, not confirmation that any arbitrary 256 KiB image boots.

## Documentation

- [Architecture](docs/architecture.md)
- [Building and setup](docs/building.md)
- [Tests and evidence scope](docs/testing.md)
- [Toolchain selection](docs/toolchain.md)
- [Geargrafx setup and debugging](docs/emulator-geargrafx.md)
- [ROM and API research status](docs/specification.md)
- [System Card ROM source contract](docs/system-card-rom.md)
- [81-slot ABI contract ledger](docs/abi-contracts.md)
- [Expanded Geargrafx CPU/RAM/IRQ evidence](docs/geargrafx-contract-evidence.md)
- [Pinned OSS research audit](docs/oss-audit-systemcard.md)
- [System Card 3.0 API-by-API Issue inventory](docs/api-inventory.md)
- [Geargrafx harness plan](docs/geargrafx.md)
- [Compatibility and evidence](docs/compatibility.md)
- [OSS reuse, licensing and provenance policy](docs/provenance.md)
- [Initial OSS reuse candidate inventory](docs/reuse-inventory.md)
- [Roadmap](docs/roadmap.md)
- [Change-aware nightly developer ROM builds](docs/nightly.md)
- [Contributing](CONTRIBUTING.md)

## Current build

On Linux x86_64 with Python 3.10+, GNU Make, GCC/G++, binutils and curl:

```sh
make setup     # checksum-verified, pinned source builds; no sudo
make build     # build/debug/smoke-test-not-bios.pce (8192 bytes)
make test      # host tests AND real Geargrafx cold/warm/negative checks
make check     # source, assembler and GitHub Actions static checks
make clean
make build MODE=release
```

`make setup` keeps external sources outside this repository and is safe to repeat.
The default `MODE=debug` includes a listing and debug information; release mode
produces the same test ROM with fewer debugging artifacts. `make release` refuses
to publish a BIOS while APIs and System Card boot remain unimplemented.

Implemented: deterministic fixture builds, format/layout/negative host checks,
headless Geargrafx HuCARD execution, CI and main-SHA-aware Nightly workflows.
Unimplemented: all System Card API services, CD boot/read and production BIOS.
Planned: System Card mapping verification (#1), startup (#4), MCP trace/CD harness
(#6), and API implementations following the [inventory](docs/api-inventory.md).
Draft PR #101 adds a separate diagnostic-only 81-slot scaffold. `make dev-rom`
builds a 256 KiB BIOS DEV image in `build/<mode>/bios-dev/`; `make dev-test`
checks its table and real Geargrafx PC/X/MPR checkpoints with synthetic inputs.
These stubs never return success and are not implemented API services.
See [diagnostic scope and unresolved ABI](docs/api-stub-scaffold.md).
See [building](docs/building.md) and [testing](docs/testing.md) for exact scope.

## License

Original source code and documentation authored for this repository are MIT-licensed; see [LICENSE](LICENSE). Properly licensed third-party components may be reused under **their own** terms, with file-level approval, attribution and required notices. The presence of MIT project code does not relicense external code; see [the reuse policy](docs/provenance.md). Not affiliated with NEC, Hudson Soft, Konami or other trademark holders.
