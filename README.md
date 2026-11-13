# oss-pce-cd-bios

An open-source project to create a **PC Engine / TurboGrafx-CD Super System Card 3.0-compatible BIOS replacement**, using original and appropriately licensed reusable OSS code.

> **Status: historical main `5a8dc5a` (2026-11-06) plus this revision — development diagnostics only.** The repository does **not** yet contain a releasable or commercial-game-compatible System Card BIOS. Historical main `5a8dc5a` has **14/81** DEV implementations and **67 stubs**. This revision (EX_JOYSNS $21, Issue #43) has **17/81** DEV implementations and **64 stubs**. Owner-approved DEV_CALLABLE: **0**; reference ABI VERIFIED: **0/81**; reference JSR permitted: **0/81**; `release_eligible: false`. See [status](docs/status.md).

Current DEV implementation count: 17/81; current stubs: 64/81; owner-approved DEV_CALLABLE: 0; reference ABI VERIFIED: 0/81; reference JSR permitted: 0/81.

## Current development-only capabilities

The 81 candidate slots in `spec/dev_implementations.json` contain 17 project-defined implementations: `EX_GETVER` ($1E), `EX_JOYSNS` ($21), `EX_JOYREP` ($22), `EX_BGON` ($2E), `EX_SETWRT` ($3A), `EX_BINBCD` ($3C), `EX_BCDBIN` ($3D), `EX_RND` ($3E), `MA_MUL8U` ($3F), `MA_MUL8S` ($40), `MA_MUL16U` ($41), `MA_DIV16S` ($42), `MA_DIV16U` ($43), `MA_SQRT` ($44), `MA_SIN` ($45), `MA_COS` ($46) and `MA_MUL16S` ($4F). The other **64** entries are nonreturning stubs. An installed DEV routine is **not** a reference-BIOS-verified ABI.

The pinned Geargrafx core executes original HuCARD and development firmware diagnostics: reset/stack/MPR/IRQ probes, synthetic CD READ(6), RAM payload execution, and experimental IPL loading. These do **not** implement callable, independently verified `CD_BOOT`, `CD_RESET`, `CD_BASE` or `CD_READ` services.

- [PR #681](https://github.com/mao2009/oss-pce-cd-bios/pull/681) (merged 2026-11-06): MPR2/MPR3 measurement and a **multi-sector synthetic IPL** loader on the 8 KiB HuCARD+CUE path. Its PR evidence reports 22 scenarios, cold/warm.
- [PR #683](https://github.com/mao2009/oss-pce-cd-bios/pull/683) (merged 2026-11-06): the same 22 synthetic scenarios through a **separate 256 KiB DEV image** using Geargrafx `LoadBiosFromBuffer` and loader-identity checks. This demonstrates the selected DEV loader path, **not System Card compatibility**.
- The **single-sector PCEX** experiment (Issue #668) is a different DEV contract and remains available. No lawful commercial CD game boot or verified reference ABI is demonstrated.

Historical reference only: main `f90c16b` (2026-10-22) had 9 DEV implementations and 72 stubs; main `2954e90` (2026-10-28) had 13 DEV implementations and 68 stubs. These are not current counts.

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

## Optional local ROM and disc images

For local, **optional** diagnostics, contributors can put their own lawfully obtained game-disc images or separate System Card reference ROMs under `./rom/`. The directory is Git-ignored and is not required by setup, public tests, or CI. Tool-specific format support and test commands still apply; placing files there does **not** automatically run tests or establish BIOS/game compatibility. See [CONTRIBUTING.md](CONTRIBUTING.md) and [AGENTS.md](AGENTS.md).

## Documentation

- [Actual implementation status (main vs open Draft PR, if any)](docs/status.md)
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

## Build and verification

Linux x86_64 (or WSL) requires Python 3.10+, GNU Make, GCC/G++, binutils and curl. `make setup` downloads/builds checksum-pinned tools into an **external** cache.

```sh
make setup
make build                        # original 8 KiB HuCARD test fixture, not BIOS
make test                         # host tests (cc65 required)
make test-real-core               # pinned real Geargrafx core
make check                        # source and workflow checks
make dev-rom                      # 256 KiB DEV ROM; 14 project-defined routines
make dev-test                     # DEV diagnostics; no reference ABI guarantee
make dev-release-gate-blocked     # expected refusal
```

Opt-in development diagnostics (original **synthetic** media only):

```sh
make dev-cdread-integration
make dev-cdload-exec-integration
make dev-boot-internal-integration
make dev-ipl-boot-integration         # one-sector PCEX (Issue #668)
make mpr-bank-probe                   # MPR2/3 observation
make ipl-loader-integration           # multi-sector, 8 KiB HuCARD path (#681)
make dev-bios-ipl-loader-integration  # multi-sector, 256 KiB DEV BIOS-buffer path (#683)
```

`make release` intentionally refuses to publish a BIOS. Each test result only supports the exact commit, toolchain and scenario it was run on; this documentation change itself is not an emulator test run. See [building](docs/building.md), [testing](docs/testing.md), [DEV IPL boot](docs/dev-ipl-boot.md) and [compatibility](docs/compatibility.md).

## License

Original source code and documentation authored for this repository are MIT-licensed; see [LICENSE](LICENSE). Properly licensed third-party components may be reused under **their own** terms, with file-level approval, attribution and required notices. The presence of MIT project code does not relicense external code; see [the reuse policy](docs/provenance.md). Not affiliated with NEC, Hudson Soft, Konami or other trademark holders.
