# CD_BASE real-action prototype V1 (Agent-A G5 P0)

**Status: EXPERIMENTAL DEV-ONLY. NOT a compatible System Card BIOS. NOT the genuine
CD_BASE ABI. NOT game compatibility. NOT G5 success. `release_eligible=false`.**

Slot `$02`, candidate entry `$E006`. Prior merged PRs #757 ($02 fake RTS), #759
($02->$33->$4A fake RTS chain), #765 (DEV/PUBLIC loader diff), #772 (EX_DSPOFF
unconnected) are **fake RTS, NOT real API success**. This prototype implements REAL
guest processing (LBA compare/subtract, INDEX01 handling, state update, MPR/RAM
discipline) and wires it into the **experimental BIOS image only**. The normal DEV
ROM is byte-identical (no `src/overrides/slot_02.s` is created; the proto lives in
`src/cd_base_proto.s` + `tools/build_experimental_bios_boot.py` 5-byte patch).

## 1. Measured caller conditions (SOURCE_CONFIRMED, ledger S1/S2)

- HuCC caller writes `_al` (track, `$20F8`), `_bh=$80` (`$20FB`), `_cl=2`
  (`$20FC`) then `jsr cd_base` (ledger §3(1), S1). Which bytes the genuine callee
  reads is UNKNOWN.
- `_al.._dh` at `$F8:20F8`, `tnomax` at `$F8:226B` (S2). Block hypothesis for
  CD_READ is 24-bit `_cl:_ch:_dl` at `$20FC/$20FD/$20FE` (HYPOTHESIS, not genuine).
- 2ISO default off (S3). CD_READ callers (S4/S5) never call CD_BASE in the same
  function. All of (2)-(6) genuine behavior UNKNOWN/BLOCKED.

## 2. Undetermined ABI (UNKNOWN/BLOCKED, never guessed as success)

| # | Question | Prototype choice (EXPERIMENTAL) | Genuine status |
|---|----------|----------------------------------|----------------|
| (1) | Input bytes read | Reads `$20F8/$20FB/$20FC-$20FE`, requires `$20F9/$20FA/$20FF=0`. Input-sensitivity provable by 1-byte differential on the pinned core. | UNKNOWN (genuine callee reads unknown) |
| (2) | Block absolute vs relative | Absolute LBA candidate vs INDEX01 (ledger ABS_LBA class). `LBA=base`, `offset=base-INDEX01`. BASE_PLUS_BLOCK with stored base is a later step, not claimed here. | UNKNOWN (HYPOTHESIS only) |
| (3) | Base setting/retention | Stored base at `$2400-$2402`, generation `$2403`, last `$2404`, offset `$2405-$2407`. Retained across calls, preserved on error, cleared only by RAM init (test harness zeroes). Reset persistence NOT claimed (cold/warm both start from zeroed harness state). | UNKNOWN |
| (4) | CD_READ relation | No CD_READ is issued by the proto. Relation to CD_READ UNKNOWN; a later step may feed `stored_base+block` to READ(6) but this V1 does not. | UNKNOWN |
| (5) | MPR/RAM side effects | No TAM (MPRs preserved by construction), inputs read-only, only `$2400-$240A` written. I/D/V preserved, Z/N/C reflect A. | UNKNOWN (genuine) |
| (6) | Return/failure | A=`0` DATA / `$10` track / `$11` flags / `$12` reserved / `$21` over-21-bit / `$30` pregap / `$31` before_track. C=`0`/`1`. Genuine codes UNKNOWN. | UNKNOWN |

No VERIFIED, no DEV_CALLABLE, no compatibility. Reference JSR 0/81, ABI VERIFIED
0/81. Geargrafx observations are not hardware proof.

## 3. What actually advances vs fake RTS

- Fake RTS: 1-byte `RTS`, no arithmetic, no state, no input sensitivity, no
  error taxonomy. Post-return behavior CONDITIONAL on fabrication.
- This V1: ~120-byte routine with 24-bit compare/subtract, virtual-PREGAP
  (`INDEX01-150`) handling, 6 error codes, generation counter, offset store,
  strict input checks, balanced stack, preserved MPR/X/Y. Synthetic tests assert
  LBA/INDEX01/state/MPR/RAM on the pinned core (cold+warm). The first blocker
  moves from “no executable callee” (fallback non-return, all-BLOCKED) to
  “executable experimental callee with defined taxonomy” (OBSERVED for the
  experimental contract, still BLOCKED for the genuine ABI).

## 4. Evidence kinds

- Host: `tests/host/test_cd_base_proto_v1.py` (13), `test_cd_base_proto_build.py`
  (3). No Geargrafx needed. Never VERIFIED for genuine ABI.
- Real-core (pinned `b49ae82a`, cc65 `71746c8`): focused integration
  `tests/integration/test_cd_base_proto_v1.py` runs the experimental image with
  synthetic INDEX01=150 + INDEX01=0 fixtures, JSR `$E006` payloads for
  normal/abnormal/boundary, cold+warm, asserting A/C/MPR/RAM/state. Evidence JSON
  is sanitized (no media bytes/paths/hashes).
- Commercial (opt-in `./rom/*.cue`, read-only, sanitized, never staged): checks
  whether the first blocker advances beyond the fake-RTS point (`SIMULATED_NEXT_API`
  with X=2). If `./rom` absent/unsupported, reports SKIP/BLOCKED, never PASS.

## 5. Remaining blocker (single next function)

V1 stores an absolute base but does not feed `stored_base+block` into a READ(6).
The single next implementation is the **CD_READ link**: a `BASE_PLUS_BLOCK`
READ path (`LBA=stored_base+block`, 21-bit + disc-window checks, one-sector
RAM transfer to `$2800`, result block) so the proto’s offset can be observed as
a SCSI LBA and sector payload on synthetic media. Until then, commercial-game
progress stays at “experimental return observed, no sector read”.
