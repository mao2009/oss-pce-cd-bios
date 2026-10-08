# Geargrafx integration — investigation and test-harness proposal

Official project: https://github.com/drhelius/Geargrafx
MCP documentation: https://github.com/drhelius/Geargrafx/blob/main/MCP_README.md

**Bootstrap update (2026-10-08):** the pinned source-built libretro core now runs
the original 8 KiB HuCARD smoke fixture with cold/warm RAM checkpoints and a
negative execution case. See [setup/debug instructions](emulator-geargrafx.md)
and [implemented test scope](testing.md). The following remains the broader
System Card/CD/MCP plan; those stages are not claimed complete.

## Why Geargrafx first

The project documents PC Engine CD emulation, a debugger, memory/register inspection and breakpoints, trace logging with CD-ROM/hardware events and an MCP server usable from automated development tools. MCP docs describe a `load_bios` operation whose `syscard` type expects **256 KiB**. Geargrafx recommends a known System Card 3.0 BIOS but explicitly describes loading other BIOS images.

**This repository has executed an original HuCARD fixture, but has not tested
System Card boot.** Do not claim Geargrafx confirms compatibility with arbitrarily
generated 256 KiB files.

## Noninteractive runner contract (to implement and verify)

1. Detect the installed Geargrafx binary and report precise version/commit.
2. Set isolated writable profile/configuration for each parallel job; do not run agents against one shared emulator instance.
3. Start the actual core using supported headless MCP transport; verify the process and report capability, not only mocked responses.
4. Call `load_bios` for the generated ROM; treat rejection or unexpected media load as explicit failure.
5. Boot a **self-authored** synthetic CD image and run until bounded PC/symbol checkpoint, exception, timeout, or stop.
6. Capture guest CPU state, RAM/ROM mapping, last events and structured diagnostics; omit game discs and proprietary firmware.
7. Run assertion on *actual observed* target state; persist reproducible source revision, ROM hash, emulator SHA and fixture hash.
8. Exit independently of UI and return distinct PASS / FAIL / SKIP / BLOCKED outcomes; do not silently convert unavailable core into PASS.

## Emulation and automation risks

- Validate the CLI and MCP schema against the exact pinned Geargrafx version before hardcoding tool arguments.
- CLI flags/ports may change; docs include headless mode, STDIO and HTTP. Prefer local STDIO or loopback-only HTTP with authentication where relevant.
- Synchronous MCP operations are not equivalent to atomic multi-command transactions. Use one isolated emulator per worker.
- Disc timing, audio, VDC/ADPCM and memory behavior must be cross-checked against an independent emulator where possible.
- A provided `syscard` blob with a known hash is a proprietary reference, **not** a fixture to commit or ship. The acceptance gate must use the independently built ROM.

## Initial tests

- H0: version/capability doctor; report missing binary as BLOCKED.
- H1: original ROM file accepted as `syscard`; distinguish size acceptance from valid boot.
- H2: reset PC and minimal code checkpoint reached through actual HuC6280 execution.
- H3: two consecutive runs produce reproducible normalized observations.
- H4: invalid size or invalid fixture fails as expected.
- H5: original synthetic CD read and boot handoff.
- H6: independent emulator cross-check with documented differences.

CI should distinguish pure fixture/schema checks (always-on) and actual emulator runs (only where verified install/source is available).
