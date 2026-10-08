# Compatibility and reproducible evidence

**No retail CD game has been tested with an oss-pce-cd-bios image.** There is no bootable image at project bootstrap.

## Evidence stages

| Stage | Minimum evidence |
| --- | --- |
| SOURCE | Original source and provenance reviewed |
| BUILT | Reproducible ROM hash and structural validation |
| BIOS-LOADED | Geargrafx or independent emulator accepted ROM; not necessarily executed |
| STARTUP | Real guest CPU execution reached a bounded checkpoint |
| CD-DETECTED | Disc detection and metadata observed with synthetic disc |
| HOMEBREW-BOOT | Independently authored CD program runs |
| RETAIL-BOOT | Identified lawful local game edition reached game code |
| PLAYABLE | Explicit gameplay/audio/input scenario completed |
| SAVE-TESTED | Persist/reload scenario passed |
| COMPLETION-TESTED | Defined full-game finish path reached |

Never promote automatically between stages.

## Evidence record

- Commit, branch and ROM SHA-256
- Toolchain/assembler/linker versions and build invocation
- Emulator/core ID + commit/version; operating system/host
- System Card ROM loading mode and memory configuration
- Disc fixture origin, track layout, hash and media format
- Target game edition, region and local-use condition if a private retail run is performed
- Start/reset state, bounded sequence of relevant guest events, register/RAM deltas
- Expected checkpoint and observed checkpoint; PASS/FAIL/SKIP/BLOCKED
- Exit/timeout/error status, sanitized evidence bundle, reviewer and open uncertainties

A mock runner cannot validate the real emulator. If the test fixture is missing, status is BLOCKED/SKIP, never PASS. Don't commit proprietary BIOS ROMs, commercial CD images, game assets or raw disassembly.

## Test types

1. Unit: ROM layout and source checks, original synthetic fixtures.
2. Real-core smoke: boot original ROM in pinned Geargrafx.
3. Contract: BIOS API precondition/return/error/register/side effect invariants.
4. Differential: synthetic identical scenario in an independent PCE emulator.
5. Retail: opt-in locally available commercial CD, reporting metadata only.
6. Future FPGA validation: only for implementations that support a third-party BIOS image.

## Targets

JP System Card 3.0-compatible services first. Other regions, previous System Card behavior, Arcade Card and other firmware expectations are separate verified dimensions.

The existence of a CD-ROM emulator, a homebrew pass or a known BIOS filename does **not** establish game compatibility.
