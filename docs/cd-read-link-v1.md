# CD_READ link experimental contract V1 (Agent-B G5 P0)

**Status: EXPERIMENTAL DEV-ONLY. NOT a compatible System Card BIOS. NOT the genuine
CD_READ ABI. NOT game compatibility. NOT G5 success. `release_eligible=false`.**

Direct G5 improvement path: turn the CD_BASE proto stored base (`$2400-$2402`) plus a
block operand into a REAL SCSI READ(6) issuing a 1-sector RAM transfer to `$2800` on the
real CPU. Not LBA math alone — actual sector bytes in RAM.

## 1. Survey (read-only) and reuse decision

- CD_READ callee ABI (`docs/cd-read-contract.md` §1): block number hypothesis
  24-bit `_cl:_ch:_dl` at `$20FC/$20FD/$20FE`, entry slot `$03`/`$E009` HYPOTHESIS,
  return/error/MPR/stack all HYPOTHESIS or UNKNOWN. This link **never registers** a
  guessed ABI as System Card compatible and never touches `spec/api_contracts.json`.
- Transport layer (`docs/cd-read-contract.md` §2, `src/cd_read_probe.s`,
  `tools/cd_read_contract.py::transport_outcome`): READ(6) 21-bit LBA, 1..3 sectors,
  STATUS vs DATA_IN, sentinel/off-target discipline. Reused as a **pattern** (polling
  `wait_phase`/`ack_byte`/`delay`, `TMO_HI=8`, `PH_*`, result byte counting).
- `cd_set_read_lba` / `cd_entry_selected` (`src/cd_load_exec_probe.s`): the CDB idiom
  (A=LO/X=MID/Y=HI, low five bits) is reused as a pattern. The `cd_entry_selected`
  **flow is NOT reused**: it validates a `PCEX` header, copies code to `$3000`, and
  jumps to `EXEC` — unsuitable for a raw 2048-byte transfer to `$2800`. The link
  builds its own CDB at `LINK_CDB $27E8` and returns via `RTS` with a link result
  block instead of halting or executing.
- CD_BASE ledger (`docs/cd-base-abi-ledger.md`): (1)-(6) UNKNOWN/BLOCKED for the genuine
  callee. Agent-A proto V1 (`src/cd_base_proto.s` on `origin/agent-a/cd-base-proto-g5`,
  HEAD `5fe93c4b`) defines the state this link reads: `$2400/$2401/$2402` base
  HI/MID/LO, `$2403` gen, `$2404` last, `$2405-$2407` off. The V1 fix (base/gen
  preserved on error, `last` updated) is mirrored: this link **never writes**
  `$2400-$2407`.

## 2. Experimental contract CD_READ_LINK_V1 (synthetic-only, pinned core only)

Entry `cd_read_link_entry` (ROM-resident BOOT helper in a custom experimental image,
direct `JSR` by address; NOT a BIOS slot entry). Preconditions: `MPR1=$F8`, stack in
work RAM, IRQs masked, MPRs already `EXPECTED_MPR` (no `TAM` in this routine).

Inputs (preserved): base `$2400-$2402` HI/MID/LO, `gen $2403`, `last $2404` (must be
`$00`/DATA else `INVALID_STATE`); block `$20F2/$20F3/$20F4` LO/MID/HI (little-endian,
mirrors proto `$20FC-$20FE` order); window start `$20F5/$20F6/$20F7` LO/MID/HI
(synthetic INDEX 01, 21-bit); `WINDOW_LEN=16` (synthetic-only).

Computation: `LBA = base + block` 24-bit with carry (carry out of bit 23 is `OVER_CAP`,
never wraps). Refuse **before any `$1800` access** (no SCSI on refusal): `last != 0`
→ `$60 INVALID_STATE`; `WIN_HI >= $20` → `$61 INVALID_WINDOW`; carry or `LBA_HI >=
$20` → `$62 OVER_CAP`; `LBA < win` → `$63 BEFORE`; `LBA >= win+16` → `$64 AFTER`.
Stage stays `1` on refusal.

Valid request: `READ(6)` (`$08`, LBA 21 bits, count `1`) from `LINK_CDB`, exactly 2048
bytes to `$2800`. Success `$00` (`C=0/Z=1`); `READ fail $70` via two disjoint paths
(review 5872704142): `@premature` = early STATUS during the DATA_IN wait -> stage 5 +
partial bytes, STATUS byte never read (SCSI stays 0; pinned on the real core for an
off-disc READ as `LINK_RES 70 05 00 00 00 00 D8 ...` cold+warm); `@tail_bad` = bad
SCSI/message status AFTER the full 2048-byte transfer -> stage 8 + 2048 bytes
(host-only: synthetic drive always returns GOOD, so BLOCKED on the real core).
`timeout $71` (bus/select/command/data-ACK/status/message/bus-free wait expiry;
stage/bytes = the wait site: stages 2/3/4 carry 0 bytes, stage 5 partial, stages
6/7/8 full 2048; host-only per-site model, BLOCKED on the real core). `X/Y/MPR`
preserved, `I/D/V/T` preserved, stack balanced (4/4), `A` = code.

Side effects: `LINK_RES $2320-$2329` (code/stage/bytes LO-HI/SCSI/msg/last/LBA HI-MID-LO)
updated on every call; `LINK_CDB $27E8-$27ED` on valid path only; ZP `$00-$11` scratch.
Success: `$2800-$2FFF` = sector bytes. Refusal: `$2800` untouched. Fail/timeout:
`$2800` may be partial (`bytes` reports count), never outside `$2800-$2FFF`. Untouched:
bootloader `RES $2300`, proto state `$2400-$2407`, inputs `$20F2-$20F7`, `$3000+`
payload/code, all MPRs.

## 3. Evidence kinds

- Host: `tests/host/test_cd_read_link_v1.py` (38: adder/carry/max-LBA, valid/refusal,
  no-SCSI, preservation, re-run, premature/tail_bad/timeout fault-model incl. unreachable-combo rejection, sector match) +
  `tests/host/test_cd_read_link_build.py` (6: source discipline, no TAM/TXS/slot,
  normal ROM untouched, mirror-code agreement). No Geargrafx needed. Never VERIFIED.
- Real-core (pinned `b49ae82a`, cc65 `71746c8`): focused integration
  `tests/integration/test_cd_read_link_v1.py` builds a custom experimental image
  (experimental boot + probe objects + `src/cd_read_link.s` in BOOT, payload `<256`
  bytes calling the link by address) for synthetic `INDEX01=150` (16 sectors
  `150..165`) and `INDEX01=0` (`0..15`) fixtures, asserting cold+warm `A/C/MPR/SP`,
  `LINK_RES`/LBA/state/RAM, `$2800` vs `sector_payload` (or `encode_sector` for the
  patched INDEX01 sector), off-target invariants, and `PHASE load_sector` showing
  bootloader-origin vs link-origin `READ(6)`. Genuine ABI stays UNKNOWN/BLOCKED.
- Commercial (opt-in `./rom/*.cue`, read-only, sanitized, never staged): checks only
  whether the link advances beyond the proto point (`BASE_PLUS_BLOCK` READ observed).
  If `./rom` absent/unsupported, reports `NOT_RUN`/`BLOCKED`, never `PASS`.

## 4. Remaining blocker (single next item)

Wire the link as a named experimental slot (or document why payload-direct `JSR` is
sufficient) and extend the union experimental image so the proto base flows into the
link without test-seeded state, then observe a commercial PCE CD title screen (G5).
Until then, commercial-game progress stays at “synthetic sector transferred, no title”.
