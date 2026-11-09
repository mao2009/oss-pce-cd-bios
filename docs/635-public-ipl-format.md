# #635: DEV format and PUBLIC IPL format build split (S1)

Status: synthetic media only. NOT a System Card BIOS, NOT a BIOS ABI, NOT CD_BOOT / CD_READ verified,
`release_eligible=false`. No commercial bytes, sums, addresses or sector contents are recorded here.

## 1. Build policy (S1)

The loader source `tests/integration/ipl_loader_rom.s` has two formats, selected at build time only:

| build | define | MPR bytes | header bytes 14..15 | header bytes 16..31 | IPLBLN | load end | body verification |
| --- | --- | --- | --- | --- | --- | --- | --- |
| DEV (default) | none | must already be `$80..$87` (raw) | 16-bit additive body sum, must match | must be zero | 1..10 | `$8000` | sum of disc bytes == header sum, readback == sum |
| PUBLIC | `IPL_PUBLIC` | bank index `0..7`, mapped to `$80 + index` | ignored | ignored (public meaning not verified) | 1..31 | `$E000` | readback == sum of disc bytes read (no header sum) |

- The default build is byte-identical to the previous loader. This was a ONE-TIME manual comparison of the SHA-256
  of the 256 KiB default image (before and after the change, value recorded in the PR). It is NOT an automated
  test. The 256 KiB harness records the current hash in its evidence JSON; it does not compare that hash with a
  stored value.
- A single ROM never auto-detects the format.
- The DEV checksum is never disabled unconditionally: the DEV build keeps every DEV check, and the boundary
  cases `0000` / `FFFF` are tested as mismatches.

## 2. MPR bank rule: observation status

Two statements are kept separate:

- **Implementation rule of the PUBLIC build (established for this test scope):** a raw bank index `0..7` is stored as
  `raw + $80` (equal to `raw OR $80` in that range). This follows the black-box observation below for one pure
  System Card BIOS on one pinned core.
- **Not established:** full real-hardware compatibility, behaviour of other BIOS versions, and the entry state.
  The rule is NOT a claim of hardware compatibility.

Observation method (black box, not a hardware measurement):

- Pinned Geargrafx core `b49ae82a012eade566d72e9d61c9d95a5caa4da3`, loaded through `LoadBiosFromBuffer` and
  `LoadMedia` with a CUE image. The BIOS was a legally held, pure System Card BIOS (version and hash in the local
  evidence only). No BIOS code was copied or transcribed.
- A joypad RUN press was supplied as input (recorded in the local evidence). Without it the BIOS did not access the drive.
- Every `TAM` instruction executed by the BIOS during the info-block and body reads was logged with its mask and A value.
- Controls: the disc image was varied only in the IPL information bytes 8..12 (MPR2..6).

Observed (per-instruction log, `TAM` mask and A value):

| info bytes 8..12 (raw) | `TAM` values written for MPR2..6 |
| --- | --- |
| `0, 1, 2, 3, 4` | `$80, $81, $82, $83, $84` |
| `5, 6, 7, 8, 9` | `$85, $86, $87, $88, $89` |
| `$80, $81, $82, $83, $84` | `$00, $01, $02, $03, $04` |
| `127, 64, 0, 255, 8` | `255, 192, 128, 127, 136` |

Rule observed for all 20 values tested: **written value = (raw + $80) mod 256**.

Consequences:

- The value is an add (wraps modulo 256), not a bitwise OR: `$80 OR $80` would be `$80`, but the BIOS wrote `$00`.
- For raw `0..7` the add equals `raw OR $80`, so the PUBLIC build uses `ORA #$80` for that range only.
- Literal reading (raw value written as the bank) is refuted for raw `0..4`: the BIOS never wrote the raw value.

What this does NOT establish:

- Real-hardware behaviour, or behaviour of a System Card BIOS other than the one tested.
- The meaning of bytes 14..31 in the public format (see section 4).
- The entry state (call versus jump, registers, MPR0/1) at the moment control is transferred.

## 3. Regression tests

Run with `make ipl-loader-integration` (8 KiB HuCard path) and `make dev-bios-ipl-loader-integration`
(256 KiB BIOS path). Each case runs cold and warm.

MPR verification: the test payload reads MPR2..6 with TMA after the loader has launched it and stores them in the
result area. The judge checks every one of them against `(raw + mpr_add) mod 256` (`mpr_add` = `$80` for PUBLIC
cases, `0` for DEV cases). A wrong mapping in any of MPR2..6 therefore fails a positive case.

- Normal: `pub_n5_mpr_raw_0_4_zero_checksum_fields`, `pub_n5_header_bytes_14_15_nonzero_ignored`,
  `pub_n1_minimum_count`, `pub_n10_maximum_count_fills_3000_7fff`, `pub_mpr_raw_7_maps_to_bank_87`.
- Abnormal: `pub_neg_mpr_raw_8_refused`, `pub_neg_mpr_raw_ff_refused`, `pub_neg_mpr_raw_80_refused_dev_value`,
  `pub_neg_iplsta_top_overrun_8000`, `pub_fault_readback_mismatch_error31` (test-only mutant).
- Documented limit: `pub_post_sum_preserving_change_accepted_documented_limit` (see section 4).
- DEV boundary: `dev_neg_checksum_0000_mismatch`, `dev_neg_checksum_ffff_mismatch`.
- Existing DEV cases remain unchanged, including `neg_mpr2_not_ram_bank` (raw `$00` refused in the DEV build).

Specification change, stated explicitly: in the **PUBLIC build**, raw `$00` for MPR2..6 is accepted (mapped to `$80`).
The DEV build still refuses it. The old expectation (`neg_mpr2_not_ram_bank`) is a DEV-build rule, not a public rule.

## 3a. The `$4000` payload check: scope and why it is skipped

The judge compares the bytes the payload reads at `$4000` (before and after its own bank-switch round trip) with the
body byte at the same logical address. That expected value exists only if the body covers `$4000`.

- Skip rule: the `$4000` comparison runs only when `0 <= (0x4000 - load address) < body length`. The same guard
  already applied to the `$3FFF`, `$5FFF`, `$6000` comparisons and the `$4000-$5FFF` window sum.
- Cases affected: only `pub_n1_minimum_count` (one sector, body ends before `$4000`). For that case the `$4000`
  comparisons and the byte comparisons at `$3FFF`, `$4000`, `$5FFF`, `$6000` are NOT checked. The payload-tag,
  end-marker and MPR2..6 checks still run.
- Gap: for that one case the round trip `pay[6] == pay[7]` is also not compared. This is a documented test gap, not
  a loader behaviour change.
- Applies to: PUBLIC cases and DEV cases. Every DEV case that runs the payload has `n >= 3`
  (`pos_n5_3000_payloadA`, `pos_n5_3000_payloadB_same_rom`, `pos_n8_crosses_4000_and_6000`,
  `pos_n3_at_3F00_covers_old_pcex_markers`), so every DEV `$4000` check still runs as before.
- Loader code: none changed for this. The skip is test-harness logic only (`geargrafx_ipl_loader.py`, judge).

## 4. Residual risk (must be read with the change)

- PUBLIC build: the body is verified only by readback against the sum of the bytes read from the disc. The disc
  content itself is not checked. A change to the disc that preserves the 16-bit additive sum is NOT detected.
  The test `pub_post_sum_preserving_change_accepted_documented_limit` pins this behaviour so it cannot change silently.
- PUBLIC build: bytes 14..15 are ignored, because their public meaning is not verified. Treating them as a checksum
  would be wrong for public media.
- The SCSI transfer and the readback are the only integrity checks in the PUBLIC build.
- The DEV-only sum remains weak (single-byte and most copy errors are detected; sum-preserving corruption is not).
- The MPR rule is an observation for one BIOS and one core (section 2), not a specification.

## 4a. PUBLIC expansion beyond the DEV window (synthetic evidence)

The DEV window (`$3000-$7FFF`, 10 sectors) is a DEV safety limit, not a public
specification limit. The PUBLIC build therefore allows IPLBLN 1..31 and a load
end up to `$E000`, copying through the MPR2..6 regions (`$40/$60/$80/$A0/$C0`).
Bytes 16..31 are ignored in PUBLIC for the same reason as bytes 14..15: their
public meaning is not verified, and a DEV-only zero requirement must not refuse
public media.

Synthetic regression cases in `geargrafx_ipl_loader.py` (57 cases, cold+warm,
both the 8 KiB and the 256 KiB loader paths):
- `pub_n11_above_dev_limit`, `pub_n20_at_3000_crosses_c000`: IPLs larger than
  the DEV 10-sector limit are copied and executed in PUBLIC.
- `pub_n10_at_3f00_crosses_8000`: a load crossing the `$8000` region boundary
  uses the MPR4 region and the payload runs.
- `pub_reserved_nonzero_ignored`: bytes 16..31 non-zero are accepted in PUBLIC
  (the DEV case `neg_reserved_byte_nonzero` still refuses them in DEV).
- `pub_neg_iplsta_top_overrun_e000` and `pub_neg_iplbln_32_refused`: the new
  PUBLIC limits are still enforced (load end beyond `$E000`, and 32 sectors,
  are refused before any copy).
- `pub_neg_load_end_overflow_n26`..`n31`: IPLSTA `$3000` with 26..31 sectors
  wraps the 16-bit load-end high byte (actual end `$10000`+). The carry from
  the end calculation is checked and the load is refused before any copy
  (code 23, copied 0, payload never runs). Without the carry check these
  inputs would wrap to a small high byte and be wrongly accepted.

The DEV build is unchanged by these PUBLIC-only branches (`IPL_PUBLIC` ifdefs):
DEV cases, including `neg_iplbln_11`, `neg_iplsta_6000_overruns_7fff` and
`neg_reserved_byte_nonzero`, still refuse with the same codes.

## 5. Not claimed

System Card compatibility, a BIOS ABI, CD_BOOT / CD_READ verification, commercial game compatibility,
hardware behaviour, `release_eligible`.
