# #635: DEV format and PUBLIC IPL format build split (S1)

Status: synthetic media only. NOT a System Card BIOS, NOT a BIOS ABI, NOT CD_BOOT / CD_READ verified,
`release_eligible=false`. No commercial bytes, sums, addresses or sector contents are recorded here.

## 1. Build policy (S1)

The loader source `tests/integration/ipl_loader_rom.s` has two formats, selected at build time only:

| build | define | MPR bytes | header bytes 14..15 | body verification |
| --- | --- | --- | --- | --- |
| DEV (default) | none | must already be `$80..$87` (raw) | 16-bit additive body sum, must match | sum of disc bytes == header sum, readback == sum |
| PUBLIC | `IPL_PUBLIC` | bank index `0..7`, mapped to `$80 + index` | ignored | readback == sum of disc bytes read (no header sum) |

- The default build is byte-identical to the previous loader (checked by SHA-256 of the 256 KiB image).
- A single ROM never auto-detects the format.
- The DEV checksum is never disabled unconditionally: the DEV build keeps every DEV check, and the boundary
  cases `0000` / `FFFF` are tested as mismatches.

## 2. MPR bank rule: observation status

Working hypothesis I1 (Owner: kept as a working hypothesis, not a specification).

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

- Normal: `pub_n5_mpr_raw_0_4_zero_checksum_fields`, `pub_n5_header_bytes_14_15_nonzero_ignored`,
  `pub_n1_minimum_count`, `pub_n10_maximum_count_fills_3000_7fff`, `pub_mpr_raw_7_maps_to_bank_87`.
- Abnormal: `pub_neg_mpr_raw_8_refused`, `pub_neg_mpr_raw_ff_refused`, `pub_neg_mpr_raw_80_refused_dev_value`,
  `pub_neg_iplsta_top_overrun_8000`, `pub_fault_readback_mismatch_error31` (test-only mutant).
- Documented limit: `pub_post_sum_preserving_change_accepted_documented_limit` (see section 4).
- DEV boundary: `dev_neg_checksum_0000_mismatch`, `dev_neg_checksum_ffff_mismatch`.
- Existing DEV cases remain unchanged, including `neg_mpr2_not_ram_bank` (raw `$00` refused in the DEV build).

Specification change, stated explicitly: in the **PUBLIC build**, raw `$00` for MPR2..6 is accepted (mapped to `$80`).
The DEV build still refuses it. The old expectation (`neg_mpr2_not_ram_bank`) is a DEV-build rule, not a public rule.

## 4. Residual risk (must be read with the change)

- PUBLIC build: the body is verified only by readback against the sum of the bytes read from the disc. The disc
  content itself is not checked. A change to the disc that preserves the 16-bit additive sum is NOT detected.
  The test `pub_post_sum_preserving_change_accepted_documented_limit` pins this behaviour so it cannot change silently.
- PUBLIC build: bytes 14..15 are ignored, because their public meaning is not verified. Treating them as a checksum
  would be wrong for public media.
- The SCSI transfer and the readback are the only integrity checks in the PUBLIC build.
- The DEV-only sum remains weak (single-byte and most copy errors are detected; sum-preserving corruption is not).
- The MPR rule is an observation for one BIOS and one core (section 2), not a specification.

## 5. Not claimed

System Card compatibility, a BIOS ABI, CD_BOOT / CD_READ verification, commercial game compatibility,
hardware behaviour, `release_eligible`.
