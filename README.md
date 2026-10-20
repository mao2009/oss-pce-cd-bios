# oss-pce-cd-bios

A redistributable **PC Engine / TurboGrafx-CD System Card BIOS replacement** built from original and properly licensed reusable OSS code.

> **Status: development-only firmware, not a compatible BIOS.** Main builds an original HuCARD fixture and a separate 256 KiB System Card DEV ROM with 81 candidate jump-table entries: **one callable OSS `EX_GETVER` service** (slot `$1E`, entry `$E05A`) and **80 nonreturning stubs**. Pinned Geargrafx real-core tests observe `EX_GETVER` caller return on cold/warm runs, alongside separate startup and synthetic-CD READ(6) → RAM diagnostics. **Full reference-BIOS ABI compatibility, callable CD services, BIOS-mediated disc boot, retail-game compatibility and release eligibility are not established.** See the [dated implementation status](docs/status.md).

## Latest merged progress (2026-10-20, main `963f3a36`)

**Callable development-only System Card BIOS services: 1 / 81 (`EX_GETVER`, slot `$1E`).** The other **80 entries are nonreturning stubs**. `CD_RESET`, `CD_BASE`, `CD_READ`, BIOS-mediated homebrew boot, retail-game compatibility and releasable firmware remain unimplemented or unverified.

What improved since the October 17 status report:

- **Standalone HuC6280 routines**, including `EX_BINBCD`, `EX_BCDBIN`, `MA_MUL8S` and `MA_MUL8U`, are tested independently ([#328](https://github.com/mao2009/oss-pce-cd-bios/pull/328), [#331](https://github.com/mao2009/oss-pce-cd-bios/pull/331)); they are **not wired into the BIOS jump table**.
- **Reference BIOS execution safeguards:** write-path inventory ([#360](https://github.com/mao2009/oss-pce-cd-bios/pull/360)) and pre-side-effect probe evaluation ([#362](https://github.com/mao2009/oss-pce-cd-bios/pull/362)). Unresolved paths keep reference-JSR execution **BLOCKED**.
- **Evidence gates:** measurement-point/log binding ([#371](https://github.com/mao2009/oss-pce-cd-bios/pull/371)) and observation-kind/claim-field validation ([#359](https://github.com/mao2009/oss-pce-cd-bios/pull/359)). These checks do **not** independently authenticate a producer or certify reference ABI compatibility.
- **Publication safeguards:** Pages validation/build/deploy privilege separation ([#374](https://github.com/mao2009/oss-pce-cd-bios/pull/374)) and fail-closed artifact-action checks ([#349](https://github.com/mao2009/oss-pce-cd-bios/pull/349)), each within its documented coverage.

**Next milestone:** caller-safe minimal CD firmware services and a self-authored disc boot **through the BIOS**. See the [dated status](docs/status.md) for evidence limits, not a game compatibility percentage.

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

- [Actual implementation status (main vs Draft PR)](docs/status.md)
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
to publish a BIOS because most APIs and System Card boot remain unimplemented.

On **main** the additional, development-only targets are:

```sh
make dev-rom                    # 256 KiB; 1 EX_GETVER override + 80 nonreturning stubs
make dev-test                   # host API tests plus DEV ROM real-core integration
make dev-integration            # EX_GETVER cold/warm caller/return observations
make dev-startup-integration    # separate reset/MPR/stack checkpoint
make dev-cdread-integration     # guest READ(6), full-byte RAM/phase checks
make dev-trace-integration      # real-core isolated trace and negative checks
make dev-release-gate-blocked   # expected refusal; NOT a firmware release
```

**`EX_GETVER` is the first callable, development-only BIOS API** ([#174](https://github.com/mao2009/oss-pce-cd-bios/pull/174)): the OSS implementation returns X=3/Y=0 and preserves A in the tested caller contract. The full reference ABI and behavior on other emulators or physical hardware are **not verified**; Issue [#40](https://github.com/mao2009/oss-pce-cd-bios/issues/40) remains open. The DEV ROM manifest keeps `abi: UNVERIFIED` and `release_eligible: false`.

The low-level **CD sector read diagnostic works**, but callable `CD_RESET`, `CD_BASE` and **`CD_READ` are not implemented**. The self-authored CD→RAM execution experiment ([#122](https://github.com/mao2009/oss-pce-cd-bios/pull/122)) is a diagnostic, **not** BIOS-mediated CD boot. ROM byte-identity checks protect the DEV image against unintended modifications; they do not prove BIOS compatibility.
CI tests original HuCARD and separate DEV diagnostics; Nightly is HuCARD-only.
See [building](docs/building.md), [testing](docs/testing.md) and the
[81-slot scaffold limits](docs/api-stub-scaffold.md).

## License

Original source code and documentation authored for this repository are MIT-licensed; see [LICENSE](LICENSE). Properly licensed third-party components may be reused under **their own** terms, with file-level approval, attribution and required notices. The presence of MIT project code does not relicense external code; see [the reuse policy](docs/provenance.md). Not affiliated with NEC, Hudson Soft, Konami or other trademark holders.
