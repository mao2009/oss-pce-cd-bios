# Issue #5: independent CD-ROM² sector-read diagnostic (development-only)

An **original, isolated HuC6280 diagnostic ROM** whose guest code drives the CD-ROM² controller registers
and copies a 2048-byte data sector from a synthetic disc into guest RAM. It is **not** a production BIOS,
**not** `CD_READ` (API slot `$03`), and **not** an approved System Card ABI. `make release` and
`make dev-release-gate` keep rejecting the firmware; a successful sector read here does not make the System
Card replacement complete.

What it proves, and what it does not:

| Proven (pinned Geargrafx `b49ae82a`, real CPU execution) | Not proven |
| --- | --- |
| Guest HuC6280 code, using no System Card ROM, no HLE and no host-side data injection, issues READ(6) and receives the sector via `$1800`-`$1804` | Real PC Engine CD hardware behaviour, a second emulator, Super CD-ROM² RAM, CD_READ's ABI |
| All 2048 (or 6144) bytes in guest RAM equal an independently generated reference, on cold **and** warm reset | IRQ-driven transfer, `$1808` auto-ACK, ADPCM/VRAM destinations, TOC/track-type handling, retries |
| Negative runs (tampered disc, wrong sector, bad LBA, bad counts, timeout, drive abort) are rejected by the same judge | "No disc" inside the emulator (Geargrafx does not run the card without media: `NOT_TESTED`) |

## Commands

```sh
make setup
make dev-cdread                      # build build/<mode>/cdread-probe/cdread-probe-not-bios.pce
python3 -m unittest tests.host.test_cdimage tests.host.test_cdread -v
make dev-cdread-integration          # real Geargrafx; writes cdread-evidence.json
python3 tools/make_cd_image.py out/ --sectors 16 [--audio-sectors 8]   # inspect the fixture
make dev-cdread MODE=release
cmp build/debug/cdread-probe/cdread-probe-not-bios.pce build/release/cdread-probe/cdread-probe-not-bios.pce
```

The default DEV ROM, the #4 startup ROM and the 81-entry API table are untouched (the first 4 KiB of the
image is byte-identical to the base ROM and verified on every build).

## Fixture disc (`tools/make_cd_image.py`)

* CUE/BIN, one **MODE1/2352** track, `INDEX 01 00:00:00` so SCSI LBA *n* is BIN sector *n*. 16 sectors by default.
* Sector *n* user data = 32-byte header (`OSSPCE-CD-TEST-V1\0`, LBA, ~LBA) + SHA-256 counter stream. Every sector
  is different in every 64-byte window; nothing third-party.
* Raw frame: sync, BCD MSF header (LBA + 150 frames), mode 1. **EDC/ECC are zero** (not computed) - fine for
  emulators returning user data, not a valid burnable image.
* Output includes `disc-manifest.json` with the SHA-256 of the BIN, the CUE and every sector's user data
  (`bin_sha256` for the default disc is recorded in `cdread-evidence.json`).
* Optional `--audio-sectors N` appends an original CD-DA track 02.

## Guest program (`src/cd_read_probe.s`)

Polling only (`SEI`, IRQ2 masked via `$1802` low bits = 0), every wait bounded by a 24-bit counter
(`TMO_HI * 65536` polls, about 2.5 s of CPU time). Sequence, with the confidence of each step in
[the survey](cd-oss-survey.md):

1. `SEI/CLD/CSH`, explicit MPR0-7 (`$FF,$F8,1,2,$1F,3,4,0`, same diagnostic mapping as #4), `SP=$FF`.
2. Clear the 32-byte result block at `$2300` (`[0]=$FF` "in progress"), fill `$2800-$3FFF` with sentinel `$5A`
   so stale RAM can never pass.
3. Reject `count` outside 1..3 (buffer holds three sectors) without touching the hardware (error 9).
4. Pulse `$1804` bit 1 (SCSI bus reset) and require bus free (`$1800 & $F8 == 0`).
5. Write `$81` to `$1801`, then `$1800` (selection); wait for COMMAND phase (`$D0`).
6. Send the six READ(6) bytes (`cd_cdb`, default `08 00 00 05 01 00` = LBA 5, 1 sector), each as: wait
   COMMAND+REQ, write `$1801`, ACK handshake.
7. For each data byte: wait DATA_IN+REQ (`$C8`) - an early STATUS phase aborts the wait (error 5) - read `$1801`,
   store, ACK handshake.
8. Read STATUS byte and MESSAGE byte (both must be 0), wait for bus free, store the transferred byte count,
   write `[0]=0`, set `X=$C0` and stop at `cd_halt_ok`. Any failure stops at `cd_halt_fail` with `X=error`.

Result block (`$2300`): `+0` error code (`$FF` in progress, 0 OK), `+1` last stage, `+2/+3` bytes transferred,
`+4` SCSI status, `+5` message byte, `+6` last `$1800` value seen.

| Code | Meaning | Code | Meaning |
| --- | --- | --- | --- |
| 1 | bus not free after reset | 6 | SCSI status/message not GOOD/COMPLETE |
| 2 | selection never reached COMMAND | 7 | status/message phase timeout |
| 3 | command byte REQ timeout | 8 | bus did not return to free |
| 4 | data timeout | 9 | invalid request (sector count) |
| 5 | STATUS phase before all data (no data / short read) | 10 | ACK handshake timeout |

`wait_phase`, `ack_byte` and the loop registers are internal to the diagnostic; none of this is a System Card
calling convention. The register values it writes (`$81` for ID/select, reset pulse length) are from public
sources and **only Geargrafx has accepted them** - real-hardware validity is UNKNOWN.

## How success is judged (`tools/cdread_verify.py`)

A run passes only if, for both cold and warm reset: the guest stopped at `cd_halt_ok` (not merely "a PC was
reached" - the probe keeps stepping 64 more instructions to show the halt is stable); `X=$C0`, `SP=$FF`, the
expected MPRs, `I` set / `D` clear; the result block is `0 / stage 9 / bytes = count*2048 / status 0 / message 0`;
**every transferred byte equals the independently generated sector data**; bytes beyond the request are still
`$5A`; the RAM was the sentinel at `cd_buffer_ready` and the data at `cd_data_done`; and the emulator itself saw
SCSI phases COMMAND, DATA_IN (with `load_sector/load_count` equal to the request), STATUS, MESSAGE_IN, BUS_FREE.
The C++ probe (`tests/integration/geargrafx_cdread_probe.cpp`) only reads state; it never writes guest memory or
registers.

## Test matrix (measured: `build/debug/cdread-probe/cdread-evidence.json`)

| Scenario | Expected | Status |
| --- | --- | --- |
| LBA 5 ×1 (default), LBA 0, LBA 15 (last), LBA 5 ×3, LBA 13 ×3 (ends at disc end) | full-byte match, cold+warm | PASS |
| Disc user data changed at byte 3 / byte 2047 of sector 5 | guest reads fine; judge rejects at that exact byte | PASS_EXPECTED_FAILURE |
| ROM requests LBA 6, judged as 5 | judge rejects at byte 24 (the LBA field) | PASS_EXPECTED_FAILURE |
| LBA 16 / `0x1FFFFF` (invalid) | error 5, 0 bytes, no RAM written | PASS_EXPECTED_FAILURE |
| LBA 14 ×3 (runs off the end) | 4096 bytes of sectors 14/15 correct, then error 5 | PASS_EXPECTED_FAILURE |
| count 0 / 4 | error 9 before touching the bus | PASS_EXPECTED_FAILURE |
| Selection redirected to `$1805` (nobody answers) | error 2, bounded timeout | PASS_EXPECTED_FAILURE |
| Drive-side abort during DATA_IN (emulator media-eject notification resets the SCSI bus, injected by the probe and logged as `INJECT`) | error 4 | PASS_EXPECTED_FAILURE |
| 18 corrupted copies of the good observations (RAM bit flips, overrun, budget, MPR, missing/altered SCSI phases) | judge rejects each | PASS_EXPECTED_FAILURE |
| Data sector with an audio track present | still reads correctly | PASS |
| READ(6) on an audio-track LBA | observed Geargrafx behaviour recorded (see below) | OBSERVED_EMULATOR_BEHAVIOUR |
| No media loaded | Geargrafx leaves CD hardware disabled and does not execute the card | NOT_TESTED (not a firmware test) |
| Zero-sector image | Geargrafx refuses to load it | NOT_TESTED |
| Interrupt-driven transfer, `$1808` auto-ACK, second emulator, real hardware | - | NOT_TESTED |

## Findings and open questions

* Geargrafx answers READ(6) with GOOD status and **no data** for a start LBA at or beyond the disc end
  (indistinguishable from a bad command until the guest sees STATUS instead of DATA_IN), and with CHECK
  CONDITION if a later sector of a multi-sector request runs off the end. Hardware: UNKNOWN.
* READ(6) addressed to a CD-DA LBA is **not an error** in Geargrafx: it returns GOOD and 2048 raw PCM bytes
  starting 16 bytes into the frame. Real drives/firmware must therefore decide data-vs-audio from the TOC
  (`$DE` mode 2 → type byte `$04` data / `$00` audio in Geargrafx); this diagnostic does not do that yet.
* In Geargrafx the 2048-byte transfer completes about 0.78M guest instructions after the command is sent
  (modelled seek + random initial phase + per-byte REQ latency); the loop is bounded by per-byte waits, not by a fixed delay.
* UNKNOWN: required reset-pulse width, meaning of the select byte on real hardware, behaviour when the drive is
  busy/spun down, retry policy, and how the real System Card splits work between BIOS and IRQ handlers.

## Toward the BIOS APIs (not done here)

* `CD_READ` ($03/$E009, Issue #12): the contract inputs (`al/ah/bl/bh/cl/ch/dl/dh` at `$20F8-$20FF`, destination
  modes, return codes, register/MPR preservation) are still `HYPOTHESIS`; HuCC's caller wrappers and Hu-Go!'s HLE
  were **not** promoted to the System Card 3.0 ABI. The reusable part of this work is the proven
  select/command/data/status sequence, which a future `CD_READ` can wrap once the ABI is reviewed.
* Suggested order: `CD_RESET` (#10, reuse the bus-reset + bus-free check) → `CD_BASE` (#11) → TOC/track type →
  `CD_READ` (#12) → `CD_EXEC` (#14) → `CD_BOOT`.
