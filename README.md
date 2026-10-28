# oss-pce-cd-bios

A redistributable **PC Engine / TurboGrafx-CD System Card BIOS replacement** built from original and properly licensed reusable OSS code.

> **Status as of 2026-10-23 (main `f90c16b`, committed 2026-10-22): development-only firmware, not a compatible System Card BIOS.** The 256 KiB DEV ROM has 81 candidate entries: **9 project-owned DEV implementations** on main f90c16b (EX_GETVER ($1E), EX_BINBCD ($3C), EX_BCDBIN ($3D), MA_MUL8U ($3F), MA_MUL8S ($40), MA_MUL16U ($41), MA_DIV16S ($42), MA_DIV16U ($43), MA_MUL16S ($4F)) and **72 nonreturning stubs**. On the Issue #79 PR branch (PR #522), MA_SIN ($45) is added on top of MA_SQRT ($44, Issue #78), giving **12 DEV implementations** and **69 nonreturning stubs**. These counts describe installed DEV routines, **not** owner-approved DEV_CALLABLE (0) or independently verified reference BIOS ABI (0/81). All slot readiness states remain NOT_READY and `release_eligible: false`. A lawful commercial CD game boot, BIOS-mediated disc boot, callable CD_RESET/CD_BASE/CD_READ and releasable firmware are not established. See [dated implementation status](docs/status.md).

## Latest merged progress (main `f90c16b`, committed 2026-10-22)

**DEV implementation count: 9/81 (11.1%), 72 stubs.** On main f90c16b, the 9th routine is MA_MUL16S ($4F, [PR #495](https://github.com/mao2009/oss-pce-cd-bios/pull/495), merged 2026-10-22, Issue #494), a **project-defined signed 16x16 to 32-bit multiplication DEV contract** (stack-passed, tested on pinned Geargrafx), **not** a proven match to the proprietary reference System Card. The 8th routine, [MA_DIV16U ($43), PR #480](https://github.com/mao2009/oss-pce-cd-bios/pull/480), is a **project-defined unsigned-division DEV contract** (quotient, remainder, flags and stack tested on pinned Geargrafx), **not** a proven match to the proprietary reference System Card. On the Issue #79 PR branch (PR #522), MA_SIN ($45) is added on top of MA_SQRT ($44, Issue #78), giving **12/81** DEV implementations and **69 nonreturning stubs**.

- **Safety experiment merged:** [PR #481](https://github.com/mao2009/oss-pce-cd-bios/pull/481) records a synthetic HuCARD caller-stack experiment. RAM-mapped JSR was observed returning normally; when MPR1 mapped the stack to I/O, a guard detected the violation **after** JSR/guard pushes had already changed VDC state. The guard is **not** production-safe; [#473](https://github.com/mao2009/oss-pce-cd-bios/issues/473), [#459](https://github.com/mao2009/oss-pce-cd-bios/issues/459) and [#367](https://github.com/mao2009/oss-pce-cd-bios/issues/367) remain gated or unresolved. IRQ timing observations are pinned-core-only, not hardware guarantees.
- **Merged evidence:** [PR #475](https://github.com/mao2009/oss-pce-cd-bios/pull/475) (EX_GETVER synthetic observation contract) and [PR #477](https://github.com/mao2009/oss-pce-cd-bios/pull/477) (81-entry static jump-table profile) are on main. Both are evidence, not reference ABI VERIFIED. [PR #502](https://github.com/mao2009/oss-pce-cd-bios/pull/502) (DEV-only internal boot path) and its CI wiring [PR #506](https://github.com/mao2009/oss-pce-cd-bios/pull/506) are also merged; this is a DEV path, not the BIOS CD_BOOT service. The reference ROM's original headered SHA-256 identity remains unresolved in [#476](https://github.com/mao2009/oss-pce-cd-bios/issues/476); a matching CRC32 or a local-only static analysis is not reference ABI VERIFIED.
- **Evidence remains divided:** main had 9 implemented entries out of 81 at f90c16b, owner-approved DEV_CALLABLE **0**, reference ABI VERIFIED **0/81**, reference JSR execution permitted **0/81**, BIOS-mediated CD boots **0**. Synthetic guest READ(6) and CD-to-RAM execution are diagnostics, not BIOS CD_READ/CD_BOOT.
- **Next engineering milestone:** after safe evidence and execution prerequisites, instrument Geargrafx boot of a lawfully obtained PC Engine CD copy of *Shin Megami Tensei* to locate **the first blocking API**. This is a **planned experiment, not an executed compatibility claim**. Keep the current reference-JSR gate closed. Historical earlier snapshots are archived in [status](docs/status.md) and Git history.



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

## Current build

On Linux x86_64 with Python 3.10+, GNU Make, GCC/G++, binutils and curl:

```sh
make setup     # checksum-verified, pinned source builds; no sudo
make build     # build/debug/smoke-test-not-bios.pce (8192 bytes)
make test      # host tests (no Geargrafx needed)
make test-real-core  # real Geargrafx cold/warm/negative checks (required in CI; needs make setup)
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
make dev-rom                    # 256 KiB; 9 DEV overrides ($1E, $3C, $3D, $3E, $3F, $40, $41, $42, $43, $4F) + 72 nonreturning stubs on main f90c16b; 13 DEV overrides ($1E, $3C, $3D, $3E, $3F, $40, $41, $42, $43, $44, $45, $46, $4F) + 68 nonreturning stubs on Issue #80 PR branch (PR #527, stacked on PR #522)
make dev-test                   # host API tests plus DEV ROM real-core integration
make dev-integration            # DEV-only API integration (including MA_DIV16U); reference ABI still unverified
make dev-startup-integration    # separate reset/MPR/stack checkpoint
make dev-cdread-integration     # guest READ(6), full-byte RAM/phase checks
make dev-trace-integration      # real-core isolated trace and negative checks
make dev-release-gate-blocked   # expected refusal; NOT a firmware release
```

**`EX_GETVER` was the first tested development-only BIOS API** ([#174](https://github.com/mao2009/oss-pce-cd-bios/pull/174)): the OSS implementation returns X=3/Y=0 and preserves A in the tested caller contract. The full reference ABI and behavior on other emulators or physical hardware are **not verified**; Issue [#40](https://github.com/mao2009/oss-pce-cd-bios/issues/40) remains open. The DEV ROM manifest keeps `abi: UNVERIFIED` and `release_eligible: false`.

The low-level **CD sector read diagnostic works**, but callable `CD_RESET`, `CD_BASE` and **`CD_READ` are not implemented**. The self-authored CD→RAM execution experiment ([#122](https://github.com/mao2009/oss-pce-cd-bios/pull/122)) is a diagnostic, **not** BIOS-mediated CD boot. ROM byte-identity checks protect the DEV image against unintended modifications; they do not prove BIOS compatibility.
CI tests original HuCARD and separate DEV diagnostics; Nightly is HuCARD-only.
See [building](docs/building.md), [testing](docs/testing.md) and the
[81-slot scaffold limits](docs/api-stub-scaffold.md).

## License

Original source code and documentation authored for this repository are MIT-licensed; see [LICENSE](LICENSE). Properly licensed third-party components may be reused under **their own** terms, with file-level approval, attribution and required notices. The presence of MIT project code does not relicense external code; see [the reuse policy](docs/provenance.md). Not affiliated with NEC, Hudson Soft, Konami or other trademark holders.
