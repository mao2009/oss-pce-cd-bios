# Issues #14 and #5: CD load-and-execute experiment (development-only)

An **original, isolated HuC6280 experiment** that reads one 2048-byte sector of a synthetic CUE/BIN disc with the
existing READ(6) path, validates a small header, copies the code it contains to work RAM at `$3000`, jumps there,
and checks the code's return marker. It is **not** a production BIOS, **not** `CD_READ` (API slot `$03`), **not** a
System Card compatibility claim, and **not** a successful System Card or commercial game boot.

## Pipeline and what each stage proves

| Stage | Mechanism | Observed by (pinned Geargrafx `b49ae82a`, real CPU) |
| --- | --- | --- |
| 1. payload | `src/cd_load_exec_payload.s`, assembled separately, linked at `$3000`, 28 bytes | `payload.bin` digest in the manifest |
| 2. data sector | `tools/build_cdload_exec.py` writes `PCEX`, entry offset, length, checksum, reserved, then the code into LBA 5 | sector bytes compared with the independent generator |
| 3. READ(6) | Stage 0 polls `$1800`-`$1804` with the Issue #5 protocol (copied, not linked) | `cd_data_done` reached, transferred bytes = 2048 |
| 4. RAM transfer | sector staged at `$2800`, code copied to `$3000` | RAM dump `$2800`-`$3FFF` equals sector + payload |
| 5. MPR / stack | MPR set as in the Issue #5 diagnostic; payload reads MPR1 with `TMA #$02` and SP with `TSX` | MPR at halt `[ff f8 01 02 1f 03 04 00]`; payload-observed MPR1 `$F8`, SP `$FF` |
| 6. control transfer | `jmp (EXECVEC)` into `$3000` | `payload_entry` PC `$3000` |
| 7. marker | payload writes `$3F00`=`A5`, `$3F01`=`5A`, `$3F04`=MPR1, `$3F05`=SP, `$3F06`=`3C`, then `jmp (RETVEC)` | marker bytes in the RAM dump |
| 8. return | stage 0 `cd_payload_return` checks the marker, then halts at `cd_halt_ok` | `cd_halt_ok` confirmed for 64 instructions, cold AND warm |

PC order must be exactly `cd_buffer_ready`, `cd_data_done`, `payload_entry`, `payload_done`, `cd_payload_return`,
`cd_halt_ok`. The judge rejects any other order.

## Negative cases (same judge; each must be rejected)

| Case | Expected guest result |
| --- | --- |
| No header on the disc (plain generator sector) | stage 11, `BAD_MAGIC` (20), 2048 bytes transferred, payload never reached |
| Magic byte flipped | `BAD_MAGIC` (20) |
| Code byte flipped (checksum mismatch) | `BAD_CHECKSUM` (23) |
| Entry offset = code length (invalid execution address) | `BAD_ENTRY_OFFSET` (21) |
| Length byte = 0 | `BAD_LENGTH` (22) |
| READ(6) for LBA 16 on a 16-sector disc | SCSI `PREMATURE_STATUS_PHASE` (5), 0 bytes |
| READ(6) for LBA `0x1FFFFF` | `PREMATURE_STATUS_PHASE` (5), 0 bytes |
| Drive abort at DATA-IN (`eject-in-data`) | `DATA_TIMEOUT` (4), 0 bytes (incomplete read) |
| Step budget 1000 | `budget`, header never reached |
| Mutated observations (RAM code byte, marker MPR, MPR at halt, payload entry PC, missing return, unconfirmed halt, sector byte, result block) | rejected by the success judge |

## Commands

```sh
make setup
make dev-cdload-exec                  # build/<mode>/cdload-exec/cdload-exec-probe-not-bios.pce
python3 -m unittest tests.host.test_cdload_exec -v
make dev-cdload-exec-integration      # real Geargrafx; writes cdload-exec-evidence.json
make dev-cdload-exec MODE=release
cmp build/debug/cdload-exec/cdload-exec-probe-not-bios.pce build/release/cdload-exec/cdload-exec-probe-not-bios.pce
```

## Not proven

- `CD_READ` ABI, System Card layout, interrupt-driven transfer, retries, TOC or track-type handling.
- Real PC Engine hardware or a second emulator (NOT_TESTED).
- A partial sector read with bytes transferred strictly between 0 and 2048 (only the abort at DATA-IN was injected).
- A RAM copy fault (no RAM fault injection), an exec address other than `$3000`, and a payload larger than one sector.
- Any compatibility, boot, or game-loading result. The default DEV ROM, the #4 startup ROM and the 81-entry API table
  are untouched and remain release-blocked.
