# Issue #762 (Wave1-B, child of #635): commercialCD PUBLIC/DEV gap root cause

**Synthetic media only. NOT a System Card BIOS, NOT a BIOS ABI, NOT game
compatibility, NOT G5 success. No commercial media, no reference BIOS, no
RTS-chain extension. `release_eligible=false`.**

## Root cause (one paragraph)

The commercialCD PUBLIC/DEV gap is a **pre-API loader-validation blocker**,
not a callee/API blocker. The DEV loader refuses PUBLIC-format information
sectors in `validate_info` (stage 3, before any RAM write and before any API
call), so the same media that the PUBLIC loader carries to the first API
call never reaches slot `$02` under DEV: `PRE_API_BLOCKER_OBSERVED` vs the
PUBLIC `API_ENTRY_CONFIRMED` / SIMULATED `$02->$33->$4A` chain (#757/#759).
The SIMULATED chain is therefore **PUBLIC-loader-conditional** — conditioned
both on the fabricated RTS returns AND on the PUBLIC loader choice.

## Differential evidence (synthetic, pinned core, same cue on both ROMs)

`make public-dev-gap-integration` runs each synthetic disc on BOTH loader
ROMs from ONE cue file (cold+warm), so the DEV/PUBLIC difference is explained
by the loader builds only:

| disc | DEV verdict | PUBLIC verdict | gap class |
|---|---|---|---|
| D1 MPR raw `0..4` (only bytes 8..12 differ from DEV baseline) | `cd_halt_fail` code **25**, copied 0 | payload runs, halt | **PASS gap, single-field flip** |
| D2 `IPLBLN=11` (+raw MPR) | `cd_halt_fail` code **22**, copied 0 | payload runs, halt | PASS gap; count check precedes MPR |
| D3 load end `$8800` (`STA $6000`, N=5, +raw MPR) | `cd_halt_fail` code **23**, copied 0 | payload runs, halt | PASS gap; end check precedes MPR |
| D4 reserved byte 20 nonzero (+raw MPR) | `cd_halt_fail` code **28**, copied 0 | payload runs, halt | PASS gap; first field check |
| D5 bad checksum + DEV-valid MPR | `cd_halt_fail` code **30**, copied 5 (stage 6) | `cd_halt_fail` code **25**, copied 0 | **refusal-code divergence** (no PASS gap) |

Reading guide:

- D1 is the only true single-field flip: the header checksum stays correct,
  so DEV code 25 proves the MPR-encoding check is the refusing element.
- D2/D3/D4 necessarily also carry the PUBLIC-required raw MPR, but their DEV
  codes (22/23/28) fire **before** the MPR check in validation order, so each
  code isolates its own check as an independent blocker. Isolation by code
  order, not by flip.
- D5 proves the checksum can never be the first-firing check on a
  PUBLIC-passing disc: reaching the DEV sum compare (code 30) needs
  DEV-valid MPR bytes, but PUBLIC refuses those bytes at the MPR check
  (code 25) before any copy. Same disc, divergent refusal codes.

Validation firing order (`tests/integration/ipl_loader_rom.s`,
`validate_info` then `verify_body`): signature 20 → reserved 28 → IPLBLK 21
→ width 27 → count 22 → STA/end 23 → entry 24 → MPR 25 → OPENMODE 26 →
post-copy checksum 30 / readback 31.

## What this does NOT establish

- No commercial bytes, sums, addresses, or sector contents are recorded
  here; the discs above are synthetic (`pattern()` fill + `ipl_payload.s`).
- Genuine callee behavior for slots `$02`/`$33`/`$4A` remains
  UNKNOWN/BLOCKED (#735 NOT_READY, #186 BLOCKED, #173 gate). No slot was
  patched to RTS in this issue.
- No claim about which of D1..D5 fires first on any specific commercial
  title (that would need private-media field reads, out of scope).
- `release_eligible`, reference ABI VERIFIED, and DEV_CALLABLE are unchanged.

## G5-class

**Pre-API loader-validation blocker** (loader refuses before any JSR into
the BIOS table). NOT a G5 callee/API success: no API was executed, no return
observed, no compatibility inferred. The #757/#759 SIMULATED chain keeps its
SIMULATED prefix and its PUBLIC-loader condition.

## Repro

- `make public-dev-gap-integration` — focused real-core differential
  (5 discs x 2 ROMs, cold+warm; the ONE heavy emu run for this issue).
- Host (no core): `python3 -m unittest discover -s tests/host -p
  'test_public_dev_gap.py'` — 26 tests: table coverage, pure-judge
  pos/neg/mutation, scope guards (no commercial path, no RTS extension, no
  production wiring).
