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

## Disc analysis (`tools/cue_analysis.py`)

For locally owned CUE/BIN images (never committed to Git), `cue_analysis.py` verifies
structure, classifies tracks, maps to absolute disc LBAs, probes MODE1/2352 sync/MSF
consistency, and can extract user sectors via `user_sector(cue, report, lba)`. The
report contains layout and checksums only; no sector payload is written. PC Engine
CD-ROM pre-gap sync patterns are tolerated up to a reasonable window. Run with:

```sh
python3 tools/cue_analysis.py rom/"Shin Megami Tensei (Japan)"/"Shin Megami Tensei (Japan).cue"
python3 -m unittest tests.host.test_cue_analysis -v
```

## Guest program (`src/cd_read_probe.s`)

Polling only (`SEI`, `$1802` written 0 at start so every CD IRQ enable is off), every wait bounded by a 24-bit counter
(`TMO_HI * 65536` polls, about 3 s of CPU time). Sequence, with the confidence of each step in
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

Error codes 1, 3, 6, 7, 8 and 10 have no negative test yet, and after any failure the bus is left as-is (no abort/reset). Warm reset (`ResetMedia`) and the eject notification are host-driven; the positive runs inject nothing.

Result block (`$2300`): `+0` error code (`$FF` in progress, 0 OK), `+1` last stage, `+2/+3` bytes transferred,
`+4` SCSI status, `+5` message byte, `+6` last `$1800` value seen.

| Code | Meaning | Code | Meaning |
| --- | --- | --- | --- |
| 1 | bus not free after reset | 6 | SCSI status/message not GOOD/COMPLETE |
| 2 | selection never reached COMMAND | 7 | status/message phase timeout |
| 3 | command byte REQ timeout | 8 | bus did not return to free |
| 4 | data timeout | 5 | STATUS phase before all data (no data / short read) |
| 5 | STATUS phase before all data (no data / short read) | 9 | invalid request (sector count) |
| 10 | ACK handshake timeout | | |
