# EX_DSPOFF ($33 / $E099) real-CPU pathfinder — G5 Agent B

**NOT a compatible System Card BIOS. NOT the reference EX_DSPOFF ABI. NOT game
compatibility. NOT G5 success.** This document records the first real-CPU
execution of project-own EX_DSPOFF processing (replacing every prior fake-RTS
stub for slot $33) inside the experimental DEV image only. The normal DEV ROM is
byte-identical and keeps its nonreturning $33 stub.

Related: Issue #61 (target), docs/61-dspoff-contract.md (contract + layout
ceiling), #758 / #759 / PR #772 (SIMULATED return experiments, superseded for
$33 by this real processing).

## 1. Result

On the pinned Geargrafx core (`b49ae82a`, instruction-debugger probe objects)
with the pinned cc65 (`71746c8`), the experimental image's $E099 table entry
points at a BOOT-region body ($F055 in the debug build) that executes the
`src/ex_dspoff.s` discipline on real hardware state:

* seeded shadow → `VDC CR low = shadow & $3F` (BB+SB cleared, every other CR bit
  kept), CR high untouched, shadow published, `C=0`;
* unseeded shadow → no VDC write, no RAM write outside the call's own transient
  stack slots, `C=1`;
* `A/X/Y` preserved, `N/Z/V/D/I/T` preserved (`C` is the result bit), `MPR0`
  restored after the `SEI` + `MPR0=$FF` window, `MPR1`/`MPR7` untouched, `SP`
  balanced, VDC `AR` left at 5, RAM sentinel untouched;
* the call returns through the real `$E099` table entry (cold and warm phases
  both reach the guest halt loop with identical VDC state).

First-stop cause before this work: the $33 table entry aimed at the FALLBACK
stub (`LDX #$33; JMP api_unimplemented` → `BRA -2`); any guest calling $E099
never returned. After this work: the call returns, so a synthetic boot-flow
probe can proceed past $33.

## 2. Wiring (experimental image only)

1. `src/experimental_bios_boot.s` appends `experimental_slot_33`, which
   `.include`s `src/ex_dspoff.s` (single source of truth — no logic is
   duplicated; the host test refuses an `and #$3F` copy in the experimental file).
2. `tools/build_experimental_bios_boot.py` redirects the linked $E099 entry from
   the fallback stub to the BOOT body, fail-closed: the pre-patch table must
   still hold the linked stub, the body must sit in `$F000-$FFF6` and begin with
   the fragment's `PHP/PHA` prologue, and the first-4KiB identity vs the normal
   ROM allows exactly the two differing bytes (`$E09A/$E09B`). The manifest
   records the wiring under `experimental_slot_33` (`release_eligible: false`,
   `system_card_abi: NO_CLAIM`, `dev_callable: false`, `private_media_used: false`).
3. `src/ex_dspoff.s` changed by comment only (no instruction touched): the header
   now states the experimental-BOOT inclusion. All `test_ex_dspoff_dev.py`
   static checks still pass unmodified.

Why not the normal ROM: `OVERRIDES` ends at `$E7FB`, 5 bytes below the
`$E800-$EFFF` probe-overlay window (host gate
`test_linked_dev_rom_keeps_overrides_out_of_probe_overlay` refuses any override
there and requires the window to stay `$FF` padding). A ~44-byte
`src/overrides/slot_33.s` would break that layout with no approved relocation.
No `src/overrides/slot_33.s` was added; `overrides()` still lacks `$33`.

## 3. Evidence (boundaries kept distinct)

| Kind | Result |
|------|--------|
| Host targeted (`tests/host/test_ex_dspoff_wired.py`, 12 tests) | **PASS**: normal-ROM stub + overlay padding intact; experimental redirect bytes, body placement/prologue, manifest gates, ledger/mapping unchanged, synthetic-only medium |
| Focused integration (`tests/integration/test_ex_dspoff_real_core.py`, 9 tests, `make integration-test`) | **PASS** on pinned real core: 9 runs (ok `F2→32`; CR `00`/`FF→3F`/`C0→00`/high-`1F`-kept; `SP=$05` min-in-contract + `SP=$07`; unseeded reject; `$02`-scaffold→`$33` two-call flow), 6/6 mutants rejected, cold/warm repeatable |
| Pinned real-core regressions | **PASS**: `geargrafx_experimental_track_contract.py` (shifted BOOT), `geargrafx_dev_ipl_boot.py` (shared units), `geargrafx_dev_bgon.py` (normal ROM) |
| Commercial CD / System Card reference / second emulator / hardware | **NOT_RUN / SKIP**: `./rom/` is absent and no private bytes, hashes, paths, images, or traces were consumed or published. No title-screen or retail-game claim is made |
| Reference BIOS JSR / ABI VERIFIED / DEV_CALLABLE | 0 / 81 unchanged; `spec/dev_implementations.json` slot EX_DSPOFF stays `implementation: null`, `UNKNOWN`, `NOT_READY`, approval null; counts stay 16 DEV / 65 stubs |

Mutants rejected (each names its contract mismatch, never a crash): `AND #$FF`
keeps display, `AND #$7F` keeps BB, `AND #$00` clears kept bits, CR-high-byte
rewrite, shadow→stack-page smash (`$2100` out-of-contract write), and
unseeded-reject bypass (takes the ok path; caught by CR/shadow invariance).

The `$02`→`$33` two-call run uses an in-variant-only fake RTS for `$02`
(`RTS+NOPs` over the `$02` stub, guarded by the `LDX #$02` marker check) purely
as conditional scaffolding so the probe can observe order past a prior API
call. Per the task framing this is SIMULATED scaffolding and is NEVER a boot
success or G5 progress claim; the only real processing measured is `$33`.

## 4. G5 contribution (what advanced by real processing)

* Slot `$33` is now callable on the real core through the genuine `$E099`
  entry: before, every $33 "return" was a fabricated immediate-RTS patch
  (SIMULATED, #756/#758/#759); now the table leads to 44 bytes of real
  VDC/shadow/stack/MPR processing with RED→GREEN mutant coverage.
* A synthetic multi-API flow can execute `$33` after a prior call and continue
  (two-call probe returns to its halt loop), which the nonreturning stub made
  impossible. This is boot-flow scaffolding evidence, not a boot claim: the
  normal ROM path games use is untouched, and no CD IPL / game code ran.
* The ROM-layout ceiling is now measured against a working alternative: real
  display-control processing lives outside `OVERRIDES` (BOOT region,
  experimental image) without disturbing the probe overlay or the normal ROM.

## 5. Remaining unknowns and blockers (unchanged)

* `$20F3` vs `$20F7` shadow-address doc mismatch: **UNKNOWN** (S1's own table
  disagrees with itself; the DEV `$2502/$2503` shadow is project-own, not evidence).
* Display-reflection timing (which horizontal period the new BB/SB takes
  effect): **UNKNOWN** — the harness asserts post-halt state only.
* `SP=$04` and below are out of contract (untested by design).
* Single next implementation: real-CPU `CD_BASE` slot `$02` processing so the
  `$02`→`$33` order no longer needs fake-RTS scaffolding (Agent A track); then
  a synthetic IPL→game-display-off sequence can be attempted in the
  experimental image.

## 6. Provenance / reproduction

Original project-own code (MIT, repository `LICENSE`); no reference BIOS bytes;
S1–S4 stay `REFERENCE_ONLY`; no `vendor/` payload; `./rom/` never read.
Reproduce: `make setup` (Linux x86_64, pinned tools) then
`python3 -m unittest tests.host.test_ex_dspoff_wired` and
`python3 -m unittest tests.integration.test_ex_dspoff_real_core`
(`OSS_PCE_REQUIRE_CC65=1` in required CI: missing tools fail, never skip).
