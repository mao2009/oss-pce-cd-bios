# CD_READ ($03 / candidate `$E009`) calling contract (Issue #12)

Status: **callee ABI not verified, CD_READ NOT_IMPLEMENTED.** Nothing here was taken from a System Card
image or disassembly. A third-party name, caller wrapper or HLE model is never promoted to VERIFIED.

Status tags:

* **VERIFIED**: executed by original guest code on the pinned real Geargrafx core
  (`b49ae82a012eade566d72e9d61c9d95a5caa4da3`, `make dev-cdread-integration`), with the named scenario
  in `tests/integration/geargrafx_cdread.py`. This is emulator evidence only; real hardware stays UNKNOWN.
* **HYPOTHESIS**: a pinned caller/HLE source shows an expectation; the System Card 3.0 callee was never executed.
* **UNKNOWN**: no admissible evidence.

Machine check: `python3 tools/cd_read_contract.py` (`tests/host/test_cd_read_contract.py`). It fails if a
transport claim is VERIFIED without real-core scenarios in the suite, or if any slot-3 field in
[api_contracts.json](../spec/api_contracts.json) becomes VERIFIED, executed or implemented before this
document is updated.

## 1. Callee ABI (System Card 3.0 service). No VERIFIED item

Sources (provenance `PENDING`, see [abi-contracts.md](abi-contracts.md#cd_read--slot-03-candidate-e009)):
HuCC `pce-devel/huc@54f5c73606c1e7142e8095c727cab2767e2b3cb3` (`hucc-systemcard.asm` L283-459,
`core-kernel.asm` L486-552, `pcengine.inc` L330-343, L468-552) and Hu-Go! HLE
`mckayemu/hugo@64ed226da2288738781743eeef228c069e8973a4` (`bios.c` L378-573).

| Item | Claim | Status | Source |
| --- | --- | --- | --- |
| Entry | slot `$03`, CPU `$E009` (`$E000 + 3*slot`); MPR7 = bank 0 at call | HYPOTHESIS | HuCC `pcengine.inc`, `core-kernel.asm` |
| Block number | 24-bit `_cl:_ch:_dl` (high:mid:low) at `$20FC/$20FD/$20FE` | HYPOTHESIS | HuCC callers, `get_file_lba` |
| Block base | relative to a base set by CD_BASE (#11) vs absolute LBA | UNKNOWN | - |
| Destination mode | `_dh`: 0 local bytes `_ax`; 1 local sectors (Hu-Go only); 2..6 RAM bank `_bl`, `_al` sectors; `$FE` VRAM `_bx` bytes `_ax`; `$FF` VRAM `_bx` sectors `_al` | HYPOTHESIS | HuCC + Hu-Go HLE |
| Mode list complete / invalid mode result | - | UNKNOWN | - |
| Count 0, bank wrap, odd VRAM byte count, upper bounds | Hu-Go reports A=`$22` for `$FF` count 0 (HLE only) | HYPOTHESIS / UNKNOWN | Hu-Go `bios.c` |
| Return | A=0 success, nonzero error | HYPOTHESIS | HuCC TAX/BNE, Hu-Go |
| Error codes (no disc, bad block, read error, busy/timeout) | - | UNKNOWN | - |
| Flags (N/Z/C/T/I/D) on return | Hu-Go sets N/Z from A, clears T (HLE) | HYPOTHESIS | Hu-Go |
| X/Y and work-RAM `$20F8-$20FF` clobber/preserve | HuCC saves `_bx.._dl` around calls (caller protection, not a clobber list) | HYPOTHESIS | HuCC |
| Preserved registers / RAM | - | UNKNOWN | - |
| MPR | caller restores MPR3 itself; HuCC comments that CD_READ corrupts it on error | HYPOTHESIS | HuCC |
| Stack | JSR in / RTS out via HuCC `call_bios`; callee depth and balance | HYPOTHESIS / UNKNOWN | HuCC |
| Interrupts | HuCC overlay loader runs CLI before CD_READ; BIOS IRQ requirement | HYPOTHESIS / UNKNOWN | HuCC |
| Synchronous completion, timing | Hu-Go models a synchronous loop | HYPOTHESIS | Hu-Go |

## 2. Transport layer (what any CD_READ must do on the bus). Emulator-verified part

Executed by `src/cd_read_probe.s` (polling, IRQs off) through `$1800-$1804`; details in
[cd-read-diagnostic.md](cd-read-diagnostic.md). Model: `tools/cd_read_contract.transport_outcome`.

| Id | Claim | Status | Real-core scenarios |
| --- | --- | --- | --- |
| T1 | READ(6) LBA *n* on the fixture (MODE1/2352, `INDEX 01 00:00:00`) delivers BIN sector *n*, 2048 bytes; LBA field is 21 bits | VERIFIED | `single_sector_lba5`, `first_sector_lba0`, `last_sector_lba15`, `wrong_sector_requested_lba6` |
| T2 | count 1..3 delivers consecutive sectors, count*2048 bytes | VERIFIED | `three_sectors_lba5`, `disc_end_boundary_lba13_x3` |
| T3 | start LBA >= disc end: STATUS phase instead of DATA_IN, 0 bytes, destination untouched | VERIFIED | `invalid_sector_lba16`, `invalid_sector_lba_max` |
| T4 | request running off the end: sectors up to the end delivered intact, then STATUS | VERIFIED | `read_past_disc_end_lba14_x3` |
| T5 | READ(6) on a CD-DA LBA returns GOOD + raw PCM (no error); firmware must check track type itself | VERIFIED (Geargrafx behaviour only) | `read6_on_audio_track_lba` |
| T6 | status byte GOOD (T3) / CHECK CONDITION (T4) | HYPOTHESIS (Geargrafx source reading) | - |
| T7 | no disc / zero-sector image | UNKNOWN (Geargrafx does not run without media) | - |
| T8 | count 0 (SCSI: 256), count > 3, drive busy/spun down, retries, real hardware | UNKNOWN | - |

`transport_outcome` raises instead of guessing for T7/T8 inputs, and the host test checks it against every
positive and end-of-disc request in the real-core suite.

## 3. What is implemented, what is blocked

* Implemented: this ledger, the transport outcome model and their host tests. No ROM routine was added:
  every input/output/register/MPR/error item of the callee ABI is HYPOTHESIS or UNKNOWN, so wiring `$E009`
  would fix an unverified ABI.
* Blocked on source evidence (needs an admissible, independently corroborated System Card 3.0 behaviour
  record under #8, not a name or HLE): block-number base (#11 CD_BASE), destination-mode list and their
  count/address units, error codes and no-disc result, A/X/Y/P and `$20F8-$20FF` preservation, MPR state on
  success and error, stack depth, IRQ prerequisites, timing.
* Blocked on the emulator: no-disc, drive busy, interrupt-driven transfer, `$1808` auto-ACK; a second
  emulator and real hardware are NOT_TESTED.
