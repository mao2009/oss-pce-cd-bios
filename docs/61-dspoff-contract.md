# EX_DSPOFF ($33 / $E099) DEV-only contract research (Issue #61)

**NOT a System Card BIOS. NOT a reference ABI. NOT game compatibility. NOT G5 success.**

Reference mapping: **HYPOTHESIS**. Provenance: **PENDING**. Reference callee
verification: **NOT_RUN / BLOCKED**. DEV implementation in ROM: **none on this
branch (BLOCKED by the ROM-layout ceiling)**. Host synthetic model of the
project-own fragment `src/ex_dspoff.s`: **PASS** (see below). Real-core via DEV
ROM: **BLOCKED** (no $33 override wired). Commercial media / second emulator /
physical hardware: **SKIP / NOT_RUN**. `release_eligible` stays `false`.

## 1. Target

System Card 3.0 main API **EX_DSPOFF**, number `$33`, candidate jump-table entry
`$E099` (`$E000 + 3*$33`). Issue #61 asks for the BG+sprite display-OFF
combination and its propagation. This document surveys the public caller-side
sources, records why the reference call contract stays UNKNOWN, and defines a
separate DEV-only project-own contract that reuses the existing DEV display
control discipline (`src/overrides/slot_2E.s`, EX_BGON). The DEV contract is not
evidence for the reference behavior.

## 2. Public-source survey (REFERENCE_ONLY, no copy, no BIOS bytes)

All items below are fact citations only; no source text is reproduced and no
third-party code is reused. Disposition for every external item is
`REFERENCE_ONLY / PENDING` (no human APPROVED reuse; `docs/provenance.md`).

| ID | Source (pinned where possible) | What it says about $33 | Status |
|----|--------------------------------|-------------------------|--------|
| S1 | Zeograd tentative System Card table, `https://www.zeograd.com/download/pce_bios.html` (fetched 2026-11-11 for this survey; reverse-engineering note on the page, System Card 3.0 scope) | `EX_DSPOFF, Function #$33`: IN (N/A), OUT (N/A). One-line behavior: "This function clears bit 6+7 in $20F3. It does not update the VDC reg #5!" Siblings on the same page: BGON sets bit 7, BGOFF clears bit 7, SPRON sets bit 6, SPROFF clears bit 6, DSPON sets bits 6+7 — each "does not update the VDC reg #5" (BGOFF line spells "VFC", an obvious typo) | HYPOTHESIS. No register/flag/RAM/MPR/stack/IRQ/timing contract beyond the one line. The `$20F3` address disagrees with the same page's register table (`$20F3` = current VDC register selected; `$20F7` = value of VDC reg #5), so the shadow address is unresolved |
| S2 | Stum generation table, `https://www.stum.de/2025/pcenginebiosoffsets/` (2025-07-28; CC BY-NC-SA 4.0 display, fact citation only) | Row `ex_dspoff`: JSR `$E099`, bodies `$E48A` (1.0) / `$E3F3` (2.0, 2.1, 3.0) / `$E40C` (TG2.0, TG3.0). Header columns are Method / JSR / 1.0 / 2.0 / 2.1 / 3.0 / TG2.0 / TG3.0. The JSR column is the stable jump-table entry; body addresses move by version | HYPOTHESIS for the name/entry mapping. No arguments, returns, or side effects described |
| S3 | pce-devel/huc `include/hucc/pcengine.inc` @ `54f5c73`, equates L468-552 (`ex_dspoff = $00:E099, FUNC` per `spec/api_contracts.json` slot 51). Pinned hash `b6a25d33bb787ea1fe550c6e04aff9f8ccaaa91b649a05a963be24b440b728a9` recorded in `docs/reuse/external-verification-log-p7.md:55` (BSL-1.0). Caller-side equate only | Name-to-entry equate for the caller SDK | SOURCE_SUPPORTED as a caller fact (the equate exists); HYPOTHESIS as a BIOS callee claim. No call contract |
| S4 | mckayemu/hugo `bios.c` @ `64ed226`, defines L30-133 (HLE selector, not an executable entry proof). Pinned hash `db3f227e09a7457bc9b0f2c54a392ab9d472f0c204b50209722` recorded in the same verification log | `ex_dspoff` selector define for the HLE dispatcher | HYPOTHESIS. An HLE selector is not proof of the jump-table entry or behavior |
| S5 | NEC HuC6270 manual (public hardware fact) + pinned Geargrafx notes (`huc6270.h` `k_register_mask[5] = 0x1FFF`, `RenderLine` `latched_cr & 0x80/0x40`; HuDK `vdc_crl`/`vdc_crh` RAM-copy practice; vdcdox names VRR the only valid data-port read) | CR R05 bit 7 = BB (background enable), bit 6 = SB (sprite enable); CR high bits 13-15 unwritable | SOURCE_SUPPORTED hardware facts. They justify the DEV mask `$3F` (= clear bits 7+6) but say nothing about the BIOS routine |
| S6 | In-tree DEV precedent `src/overrides/slot_2E.s` (EX_BGON) + shared harness `tests/integration/geargrafx_dev_cr.py` + host model `tests/host/test_api_cr_dev.py` | Shadow-RMW discipline: CR-low image at `$2502`, magic `$A5` at `$2503`, reserved `$2504`; MPR0=`$FF` window with SEI; AR left at 5; C=0 ok / C=1 uninitialized-shadow reject; SP >= `$05`; no zero-page/VRAM/CD/BURAM access | Project-own DEV contract, `PROJECT_SPEC_ONLY` for slot `$2E`. Reused as the template for the DEV DSPOFF fragment, not as reference evidence |

Agreement: S1–S4 agree on the name `EX_DSPOFF`, number `$33`, and jump-table
entry `$E099`. Disagreement / gap: S1's shadow address (`$20F3`) conflicts with
its own register table (`$20F7` for CR value); S1 says the reference does **not**
update VDC reg #5 while the DEV family (S6) writes CR immediately; S2–S4 give no
call contract at all. **Until the conflict is resolved the reference contract
stays UNKNOWN (spec=HYPOTHESIS, provenance=PENDING, implementation=NOT_STARTED,
real-function test NOT_RUN).** No independent source was found that defines
A/X/Y/P, work-RAM, MPR, stack, IRQ, VDC/VRAM/PSG, return/preserved values,
error paths, or timing for the genuine `$E099` callee.

## 3. Call contract (reference vs DEV — kept separate)

### 3.1 Reference `$E099` callee (UNKNOWN, do not implement)

| Field (`spec/api_contracts.json` slot 51) | Value |
|-------------------------------------------|-------|
| mapping (`$33` / `$E099`) | HYPOTHESIS (S1–S4 agree on the label only) |
| arguments / returns / clobbered / preserved / stack / mpr / ram / flags / irq / errors / synchronization | all UNKNOWN, no sources, confidence UNKNOWN |
| provenance / implementation / verification | PENDING / NOT_STARTED / NOT_RUN |

The genuine callee is never executed by this branch. `tests/integration/geargrafx_slot02_return.py`
patches only the 5 fallback bytes of `api_slot_33` with an immediate-RTS stub for
SIMULATED return experiments; that stub is a fabrication, not the callee.

### 3.2 DEV project-own fragment `src/ex_dspoff.s` (the only contract verified here)

Reuses S6 verbatim except the mask. `SET_MASK = $00`, `CLEAR_MASK = $C0`
(`AND #$3F`): initialized shadow → `CR low = shadow & $3F`, shadow published
first then `ST1`, CR high untouched, `C=0`; uninitialized shadow → no VDC/RAM
write outside the call's own transient stack slots, `C=1`.

| Item | DEV contract |
|------|--------------|
| inputs | none read (A/X/Y are not inputs; X/Y never written) |
| ok | `VDC CR low = shadow & $3F`; shadow `$2502` updated; CR high untouched; `C=0`; A/X/Y/MPRs restored; N/Z/V/D/I/T preserved (PLP restore, then `CLC`) |
| reject | no VDC write, no RAM write outside transient stack slots; `C=1`; A/X/Y/MPRs/flags preserved (`SEC` only) |
| VDC | `ST0` selects R05 (AR left at 5); CR low rewritten on ok; effective from the next horizontal display period |
| window | `SEI`, MPR0=`$FF` at `$0000`/`$0002` with `a:` absolute form (never zero page), MPR0 restored; MPR1 never remapped; validity check before the window |
| stack | max 3 pushes below JSR (flags, A, MPR0); caller SP ≥ `$05`; `$04` or lower out of contract; pushes balanced before RTS |
| RAM | owned: STORE record (harness), shadow `$2502`/`$2503`, sentinel, `$21(SP-4)..$21(SP)` transients. No zero-page, VRAM, joypad, CD, ADPCM, BURAM. Reserved `$2504` never written |
| differs from S1 | DEV writes VDC CR immediately; S1 says the reference does not update VDC reg #5. The difference is intentional and documented — DEV is not the reference |

## 4. ROM-layout ceiling (why no `$33` override on this branch)

Measured on base `864d979c` (`make dev-rom`, `build/debug/bios-dev/dev.lbl` /
`dev.map`): `OVERRIDES` spans `$E292–$E7FB` (`0x56A` bytes), ending 5 bytes below
the `$E800–$EFFF` diagnostic probe overlay (`src/bank0.cfg`). `slot_2E.s`
occupies `0x2C` (44) bytes; the DSPOFF fragment is the same size class. Wiring a
`src/overrides/slot_33.s` would push OVERRIDES past `$E800` and break the layout
contract with no approved relocation on this branch. Therefore:

* `src/ex_dspoff.s` is **not** linked (only `src/overrides/slot_*.s` are linked
  by `tools/build_rom.py:overrides()`); the `$E099` table entry keeps its
  nonreturning stub (`LDX #$33; JMP api_unimplemented`).
* `spec/dev_implementations.json` slot 51 stays `implementation: null`,
  `abi_contract.status: UNKNOWN`, `state: NOT_READY`, approval null. Counts stay
  16 DEV / 65 stubs (no ledger, docs, or gate churn on this branch).
* Real-core execution through the DEV ROM is **BLOCKED** (no executable `$33`
  callee exists). Host synthetic verification below is the only PASS claimed.

## 5. Evidence (boundaries kept distinct)

* Host-model synthetic (PASS): `tests/host/test_ex_dspoff_dev.py` — Python model
  of §3.2 (seeded clear keeps other bits, unseeded reject, idempotence, desync
  out-of-contract) plus static checks on `src/ex_dspoff.s` (window discipline,
  absolute VDC form, `AND #$3F` exactly once, no port reads, no `$0003`, single
  `beq`, 3-push depth, no zero-page/stack writes, `rts` tail, ceiling guard that
  `slot_33` is absent from `overrides()`). The assembled fragment is not
  executed; the check is MODEL-ONLY, same scope as `test_api_cr_dev.py`.
* Real-core DEV ROM (BLOCKED): no `$33` override linked (§4), so
  `geargrafx_dev_cr`-style runs have no entry to call. The shared harness is
  reused only as the host-side RAM-diff oracle design, not as a run claim.
* Local commercial-disc / System Card reference / second emulator / hardware
  (SKIP / NOT_RUN): `./rom/` was checked per `AGENTS.md`; no applicable local
  input or firmware path is used by this branch, and none is required. No ROM,
  sector, audio, firmware, or save bytes are published.
* System Card ABI / release (FORBIDDEN, untouched): `spec/api_contracts.json`
  slot 51, `spec/reference_jsr_gate.json`, and every release gate are unchanged.
  Reference ABI VERIFIED stays 0/81; DEV_CALLABLE approvals stay 0;
  `release_eligible` stays `false`.
* G5 class: **NOT G5 success** — no commercial-game boot, compatibility, or
  performance claim is made. The fragment is a synthetic display-control model
  for the DEV work-RAM shadow discipline only.

## 6. Reuse / provenance

* Strategy per `docs/provenance.md` + `CONTRIBUTING.md` (reuse first): S5
  hardware facts + S6 in-tree DEV discipline are reused as behavior reference;
  S1–S4 are `REFERENCE_ONLY` (fact citation, no copy/translation); the fragment
  is ORIGINAL project-own code, MIT (repository `LICENSE`), no reference BIOS
  bytes, no third-party license text, no `vendor/` or binary payload.
* `tools/provenance_guard.py` scope: only `src/ex_dspoff.s`,
  `docs/61-dspoff-contract.md`, `tests/host/test_ex_dspoff_dev.py` are added;
  no banned extensions (`.pce/.bin/.cue/.iso/.img/.chd/.wav`), no staged
  `./rom/` content, no BIOS-derived bytes.
* Residual risks: (1) S1's `$20F3` vs `$20F7` conflict is unresolved —
  reference shadow handling stays UNKNOWN; (2) the DEV immediate-write choice
  contradicts S1's deferred-write line by design — callers must not mix the two
  disciplines without re-seeding; (3) OVERRIDES ceiling still blocks any future
  `$2F–$33` ROM wiring until a layout change is approved separately.

## 7. Recommended next step (out of scope here)

A future branch (owner-approved layout change first) may add
`src/overrides/slot_33.s` including `../ex_dspoff.s`, ledger + count updates,
and `geargrafx_dev_dspoff.py` / host integration mirroring `geargrafx_dev_bgon.py`.
Do not do that here: it would exceed the `$E800` ceiling and widen this
research scope into ABI/release wiring.
