# CD-ROM² low-level control: existing OSS survey (Issue #5)

**Status: every disposition is `PENDING` human approval. Nothing here approves code for inclusion.**
Technical fit and license usability are judged separately (see [provenance policy](provenance.md)).
Survey date 2026-10-13; sources were read at the pinned SHAs below via the GitHub API. Items marked
UNKNOWN were not verified. The `huc` `cdrom.asm` header and the `ares` LICENSE text were re-read by the
author of this change; the remaining per-file license statements come from the initial survey pass and need
the usual human confirmation before any reuse decision.

## Result in one paragraph

No open-source *replacement System Card BIOS* exists that we could find (GitHub search only). The closest
real HuC6280 CD code is `pce-devel/huc` `cdrom.asm`, but it is derived from Hudson's original code, so only
the contributor's modifications are Boost-licensed and the base provenance is unclear: **reference only, not
copied**. Emulator sources (GPL: Geargrafx, Mednafen, Hu-Go!; permissive: ares ISC, MAME file-level BSD-3)
describe the *register behaviour* we need but are C/C++ models of the controller, not firmware, and cannot be
embedded. Therefore the Issue #5 diagnostic is an **original implementation written from register/phase
facts**, then checked against the pinned Geargrafx at runtime.

## Candidates

| Source @ pinned SHA | Files | What it does for CD-ROM² | Language | License observed | Technical fit | License usability | Disposition |
| --- | --- | --- | --- | --- | --- | --- | --- |
| [pce-devel/huc](https://github.com/pce-devel/huc) `54f5c73606c1e7142e8095c727cab2767e2b3cb3` | `examples/asm/elmer/include/cdrom.asm` | Fast bit-banged SCSI READ(6) with manual REQ/ACK handshake and `$1808` auto-ACK; phases, select, status/message. Header: "derived from Hudson's original fast CD code in Legend of Xanadu 2 … Modifications copyright John Brandwood 2022. Distributed under the Boost Software License, Version 1.0." | HuC6280 asm (PCEAS) | Boost-1.0 on the modifications only; base code provenance unclear | HIGH (exact protocol) | RISKY/UNCLEAR | REFERENCE_ONLY |
| same pin | `include/hucc/pcengine.inc`, `examples/asm/elmer/cd-core-scsitest/scsitest.asm` | `IFU_*` register constants; hardware test program | HuC6280 asm | Boost-1.0 (file headers; copyright holder line absent in `pcengine.inc`) | HIGH for constants | Boost, needs attribution; register facts are not themselves copyrightable | REFERENCE_ONLY (ADAPTED possible, human decision) |
| same pin | `include/hucc/hucc-systemcard.asm` | Caller-side wrappers for System Card CD calls (not firmware) | HuC6280 asm | Boost-1.0 | Contract hints for #12 only | Boost | REFERENCE_ONLY |
| [ares-emulator/ares](https://github.com/ares-emulator/ares) `9408cb43d4948fc3ea6e152a307a34348df3fe04` | `ares/pce/pcd/io.cpp`, `scsi.cpp` | Cleanest permissive model of `$1800` bits, ACK (`$1802` bit 7), reset (`$1804` bit 1), `$1808` auto-ACK; commands `$00 $08 $D8-$DE` | C++ emulator | ISC ("Copyright (c) 2004-2025 ares team, Near et al."); per-file headers not individually checked | HIGH for bit meanings | ISC is MIT-compatible but code is not portable to firmware | REFERENCE_ONLY |
| [mamedev/mame](https://github.com/mamedev/mame) `2fcf48ca55a0caab2aea1442f91955367db756da` | `src/mame/nec/pce_cd.cpp` | Register map, READ(6) decode, command table; admits its SCSI is legacy | C++ emulator | File header `BSD-3-Clause`, Wilbert Pol / Angelo Salese (MAME as a whole is GPL) | MEDIUM | file-level only | REFERENCE_ONLY |
| [mckayemu/hugo](https://github.com/mckayemu/hugo) `64ed226da2288738781743eeef228c069e8973a4` | `pcecd.c` | Register-file emulation | C emulator | Modified BSD per `COPYING` exception, but no copyright holder in the file; license texts absent at this SHA | MEDIUM | UNKNOWN attribution | REFERENCE_ONLY |
| same pin | `cd.c`, `bios.c` | HLE BIOS behaviour | C emulator | GPL-2.0 | MEDIUM (HLE ≠ hardware) | none for MIT | REFERENCE_ONLY (no copy/translation) |
| [libretro/beetle-pce-libretro](https://github.com/libretro/beetle-pce-libretro) `b96c11e095b6a40a412d2da02766ae1c3f4fd539` (Mednafen) | `mednafen/pce/pcecd.cpp`, `mednafen/cdrom/scsicd*.{cpp,inc}` | Full SCSI-CD model; confirms `$D8` is *audio* "set start position", not a data read | C++ emulator | GPL-2.0-or-later | HIGH on behaviour | none for MIT | REFERENCE_ONLY (no copy/translation) |
| [drhelius/Geargrafx](https://github.com/drhelius/Geargrafx) `b49ae82a012eade566d72e9d61c9d95a5caa4da3` (the project's pinned test core) | `src/scsi_controller.cpp`, `cdrom.cpp` | The behaviour our tests execute against | C++ emulator | GPL-3.0, Copyright (C) 2024 Ignacio Sanchez | HIGH (it is the test oracle) | none for MIT firmware | REFERENCE_ONLY; used unmodified as an external test dependency, never linked into the ROM |
| [dshadoff/PCE_ReDevelo](https://github.com/dshadoff/PCE_ReDevelo) `ab164bd8ba258fde234c1cd3866c5e6a77817402` | `Software/python/get_cdsector.py` | Host-side sector fetch over Develo box | Python | MIT, (c) 2022 David Shadoff | LOW | usable but irrelevant | REFERENCE_ONLY |
| [mathieulh/PCE_CD_BIOSES_NO_AUTOBOOT](https://github.com/mathieulh/PCE_CD_BIOSES_NO_AUTOBOOT) `e6a5bdaf3bc0704ae828e1f583b6642050231164` | IPS patches | Patches against Hudson's copyrighted ROMs | binary patches | GPL-2 text + proprietary base | none | none | REJECTED |
| Ootake, higan, `openfpga-pcengine-cd` | — | not inspected | — | UNKNOWN / GPL-2.0 (FPGA core) | — | — | UNKNOWN / not examined |

## Register facts, with provenance of each claim

`S1` = HuC `pcengine.inc` / `cdrom.asm`, `S2` = ares `io.cpp`/`scsi.cpp`, `S3` = MAME `pce_cd.cpp`,
`G` = pinned Geargrafx source read as a behaviour reference (GPL, not copied), `T` = executed by the guest
in this repository's Geargrafx tests.

| Fact | Sources | Verified in Geargrafx by guest (T) | Real hardware |
| --- | --- | --- | --- |
| `$1800` read, mask `$F8`: bit7 BSY, 6 REQ, 5 MSG, 4 C/D, 3 I/O | S1 S2 S3 G | yes (phase compares) | UNKNOWN (not measured) |
| Phase values (`& $F8`): COMMAND `$D0`, DATA_IN `$C8`, STATUS `$D8`, MESSAGE_IN `$F8`, free `$00` | S1 G | yes | UNKNOWN |
| Selection: ID bits on `$1801`, then write to `$1800` | S1 (writes `$81` to both) | selection pulse accepted; the written value is ignored by Geargrafx | the correct value/ID semantics UNKNOWN |
| ACK = `$1802` bit 7; low bits = IRQ enables | S1 S2 G | yes (kept 0 → IRQ2 masked) | UNKNOWN |
| REQ/ACK handshake: set ACK, wait REQ low, clear ACK | S1 | yes | UNKNOWN timing |
| `$1804` bit 1 pulses SCSI bus reset | S1 S2 G | yes (bus returns to free) | UNKNOWN required hold time |
| READ(6) = `$08, LBA[20:16], LBA[15:8], LBA[7:0], count, ctl`; count 0 = 256 in G | S1 S2 G | yes for count 1..3 | UNKNOWN (count 0 not exercised on the controller) |
| Data in: 2048 bytes, one per REQ; then STATUS (byte 0 = GOOD) then MESSAGE_IN (byte 0) then bus free | S1 G | yes | UNKNOWN |
| `$1808` read = data + automatic ACK | S1 S2 G | NOT_TESTED (guest uses `$1801` + manual ACK) | UNKNOWN |
| IRQ-driven transfer (`$1803` flags `$20/$40`) | S1 S2 G | NOT_TESTED (polling only) | UNKNOWN |
| READ(6) at/after end of disc | G only (status values) | guest sees STATUS instead of DATA_IN (error 5); the status byte itself (GOOD / CHECK CONDITION) is from the source and was **not** read by the guest | UNKNOWN |
| READ(6) on a CD-DA LBA | G only | returns GOOD + 2048 bytes 16 bytes into the raw frame (as if MODE1) | UNKNOWN |

Seek/spin-up timing in Geargrafx is modelled (`SeekTime`, a random initial phase up to 1/60 s); real drive
latency is UNKNOWN and the guest's 24-bit poll budget (about 2.5 s of CPU time per wait) is a diagnostic choice.
