# Issue #722 (P07 of #473): TII/TIN/TDD/TIA/TAI five-opcode regression table + boundary lengths

Status: **host-model regression table + one fresh real-core matrix run**.
No production wiring. No production guard. No change to `src/`. `#173` /
`#367` / `#459` remain BLOCKED. `release_eligible=false`, ABI VERIFIED 0/81,
DEV_CALLABLE 0, referenced JSR 0/81 are unchanged. The project is not a
compatible System Card BIOS.

- Issue: #722 (parent #473, plan item P07). Dependencies: #367 (transfer
  audit), #648 (pre-start/mid-transfer design), #717 (peek contract, PR
  #766). P01 wiring positions are inherited from #716, not re-decided here.
- Base: `864d979c`; this branch cherry-picks PR #766 (`b4efb909`) and reuses
  `tools/peek_table.py`, `tests/host/test_717_peek_failclosed.py` and
  `docs/717-peek-failclosed.md` unchanged. Nothing here reimplements peek.
- Pinned Geargrafx: `b49ae82a012eade566d72e9d61c9d95a5caa4da3` (source-built
  debugger core, local GPL-3.0 link; no core source is copied into this
  repository). Synthetic BIOS/CD only. No private `./rom/`, no commercial
  BIOS/ROM bytes, no reference JSR. Geargrafx observations are not proof of
  physical hardware behaviour (real hardware #655 UNKNOWN).
- Evidence classes are separated in every section below: `HOST_MODEL`
  (this document + `tools/xfer_722_matrix.py` + `tests/host/test_722*`),
  `REAL_CORE_EXECUTION` (pinned debugger core, synthetic media,
  verification-only probe), `PINNED_SOURCE_READING` (restated contract of
  the core source, no code copied), `NOT_RUN` / `UNKNOWN` (never PASS).

G5 class: five-xfer/boundary-length regression (verification-planning track).

## 1. What was verified

| #722 acceptance item | Where | Class | Result |
|---|---|---|---|
| 5 ops x lengths 1 / 0=65536 / crossing x bank expectation matrix | §2 + `tools/xfer_722_matrix.py::case_rows` / `run_matrix` + `LengthMatrixTests` | HOST_MODEL (matrix-consistent) | 56 rows: 25 length + 5 len0 + 20 boundary + 6 bank/TAM; exact vs `addr_lists` |
| TDD subtraction, TIN fixed dst, TIA/TAI alternation, self-overwrite at operand level | §3 + §6 + `PerOpWalkTests` + `SelfOverwriteTests` | HOST_MODEL | all five walks exact; forward/backward/wrap self-overwrite REFUSED |
| bank boundary / MPR alias / I/O-page read/write independence | §4 + `BoundaryBankTests` | HOST_MODEL + recorded REAL_CORE | read (peek) and write (preflight) judged per byte; hw page REFUSED, unmapped FAIL_CLOSED, other-bank UNKNOWN |
| unpredictable/unpeekable source/destination is fail-closed | §5 + `FailClosedTests` | HOST_MODEL | fetch, mid-range src, and dst-unmapped rows fail closed with byte index |
| #367-unreconciled rows stay UNKNOWN | §7 + `UnknownLedgerTests` | HOST_MODEL (ledger) | other-bank, cross-page alternation, mapper, device-DMA W17 pinned UNKNOWN |

## 2. Expectation matrix (5 ops x lengths x banks)

`tools/xfer_722_matrix.py::case_rows` builds 56 rows. The length fixture is
`src=$3E80`/`dst=$3E90` (TDD shifted by `n-1`, as in the pinned matrix
driver); boundary fixtures mirror `fixture_operands` in
`tests/integration/geargrafx_block_transfer_matrix.py` at the host level.
`verdict()` judges each row by reusing `peek.transfer_sequences` for the
exact walk and `peek.is_peekable` per byte; the mapping below is the steady
synthetic mapping `MPR_WRAM = [00,F8,F8,F8,00,00,00,00]` unless stated.

| Case family | Rows | Host verdict | Notes |
|---|---|---|---|
| `{tii,tin,tdd,tia,tai}-len{1,2,3,4,8}` | 25 | `WOULD_ALLOW_STATIC` | all bytes WRAM; exact vs `addr_lists` (§3) |
| `{op}-len0` | 5 | `REFUSED (len0_never_executed)` | 65536 computed exactly, never executed (§3) |
| `{op}-boundary-1fff` | 5 | `UNKNOWN (dst_unwritable)` | `$1FFF` is page 0 = ROM `$00` in this mapping: other-bank |
| `{op}-boundary-3fff` | 5 | TII/TIN/TDD `WOULD_ALLOW_STATIC`; TIA/TAI `UNKNOWN` | `$3FFF->$4000` stays in WRAM pages; TIA/TAI cross-page alternation unreconciled |
| `{op}-boundary-7fff` | 5 | TII/TDD/TIA/TAI `UNKNOWN`; TIN `WOULD_ALLOW_STATIC` | walk leaves WRAM page 3 for page 4 (ROM `$00`); TIN dst fixed `$7FFF` stays |
| `{op}-boundary-ffff` | 5 | `UNKNOWN (dst_unwritable)` | `$FFFF->$0000` wrap leaves mapped WRAM |
| `tdd-reverse-4000` | 1 | `WOULD_ALLOW_STATIC` | `$4000->$3FFF` descending stays in WRAM pages |
| `tii-boundary-alias` (page 2 -> `$F9`) | 1 | `WOULD_ALLOW_STATIC` | aliased WRAM bank `$F9` in `WRAM_BANKS` |
| `tii-hwpage` / `tii-tam-deny` (page 2 -> `$FF`) | 2 | `REFUSED (dst_hardware_page)` | I/O page never writable |
| `tii-unmapped` (`$F8` backing removed) | 1 | `FAIL_CLOSED (src_unpeekable)` | unpredictable source |
| `tii-tam-alias` (page 2 -> `$F9`) | 1 | `WOULD_ALLOW_STATIC` | TAM-page probe allowed shape |

`run_matrix()` evaluates all 56 rows with zero mismatches against the
`expect` column (rows with mapping-dependent outcomes carry `expect=None`
and are judged individually in `BoundaryBankTests`). `gate` is `BLOCKED` on
every row. No row is PASS.

## 3. Per-opcode walks and boundary lengths

Exact sequences from `peek.transfer_sequences` (TII/TIN/TIA source `+1`,
TDD source `-1`, TAI source alternates `+1/-1`; TII/TAI destination `+1`,
TDD destination `-1`, TIN fixed, TIA alternates `+1/-1`), checked against
`addr_lists` for all five ops at lengths 1/2/3/4/8 (`LengthMatrixTests`):

| Op | Source walk (len 4 from `$3E80`) | Destination walk (len 4 from `$3E90`) |
|---|---|---|
| TII `$73` | `80 81 82 83` | `90 91 92 93` |
| TIN `$D3` | `80 81 82 83` | `90 90 90 90` (fixed) |
| TDD `$C3` | `83 82 81 80` (from `$3E83`) | `93 92 91 90` (from `$3E93`) |
| TIA `$E3` | `80 81 82 83` | `90 91 90 91` (alternates) |
| TAI `$F3` | `80 81 80 81` (alternates) | `90 91 92 93` |

Boundary lengths: `tii len2 @ $3FFF -> [$3FFF, $4000]`;
`len2 @ $FFFF -> [$FFFF, $0000]` (16-bit wrap); TDD reverse
`src [$3E80, $3E7F]`, `dst [$4000, $3FFF]`. Length 1 touches exactly one
byte on all five ops. Length 0 expands to exactly 65536 entries per op:
TII covers every address once (`dst[-1] = dst+0xFFFF`), TDD walks down
(`dst[-1] = dst-0xFFFF`), TIN repeats the single destination, TIA
(`4080/4081`) and TAI (`src 80/81`) keep phase over the whole walk
(`Len0ExactnessTests`). Length 0 is refused before any step in every verdict
(`len0_never_executed`); no 64 KiB guest is executed in any suite.

## 4. Bank boundary / MPR alias / I/O page (read vs write)

Read (operand fetch) and write (destination landing) are inspected
independently, per byte, through the MPR-resolved bank:

- **WRAM banks `$F8-$FB`, mapped:** peekable and writable. Steady-state
  rows, the `$3FFF->$4000` crossing, the TDD `$4000->$3FFF` reverse walk,
  and the `$F9` alias pages (`tii-boundary-alias`, `tii-tam-alias`) are
  `WOULD_ALLOW_STATIC`. The alias pair resolves adjacent logical pages to
  adjacent WRAM offsets, matching the pinned matrix `boundary-alias`
  observation (`dst_bank [F8, F9]`, offsets `[1FFF, 0]`).
- **Hardware page `$FF`:** never peekable (fetch fails closed, no device
  touched -- #717 §4) and never writable (`REFUSED dst_hardware_page`).
  `tii-hwpage` and `tii-tam-deny` pin both sides.
- **Unmapped bank:** `FAIL_CLOSED` (`tii-unmapped`; mid-range `$FF`-page
  source byte index 1 pinned by `test_unpeekable_src_byte_fails_closed`).
- **Mapped non-WRAM bank (e.g. `$E0`, ROM `$00` pages in this mapping):**
  peekable-but-not-writable is `UNKNOWN` (other-bank), never allowed.
- **TIA/TAI alternation crossing a page boundary:** `UNKNOWN`
  (`alternation_crosses_page`), even when both pages are WRAM: the pair
  phase across the boundary is unreconciled with #367.
- **Mapper banks `< $80`:** conservative fail-closed in the synthetic setup
  (no mapper installed); a real-mapper case needs its own measurement
  (`UNKNOWN_MAPPER_BANK`).

## 5. Fail-closed rules

1. Any of the 7 fetch bytes unpeekable -> the whole fetch fails
   (`fetch_unpeekable`); the 6 readable bytes never count as evidence
   (mutation `test_mut_partial_capture_never_passes`).
2. Any transfer source byte unpeekable -> `FAIL_CLOSED src_unpeekable`
   with the byte index; the destination walk is not consulted.
3. Any destination byte on the hardware page -> `REFUSED`; on an
   unmapped/unwritable bank -> `FAIL_CLOSED`/`UNKNOWN` (§4).
4. `length == 0` -> `REFUSED` before any expansion is executed.
5. `pc is None` verdicts judge ranges only (`fetch` recorded as NOT_RUN).

## 6. Self-overwrite (whole range, operand level)

Each transfer byte step re-runs the opcode, so any destination byte in
`pc .. pc+6` (16-bit wrap) must be refused before the first step
(`peek.overlaps_instruction`, reused from #717). Pinned host cases: TII
forward into `$3E00`; TDD backward into `$3E00` (while a clear TDD range
stays allowed); TII wrap `$FFFF->$0000->$0001` into an instruction at
`$0000` with all pages WRAM-mapped. The clean range with a complete
7-byte fetch (`fetch ok`) stays `WOULD_ALLOW_STATIC`.

## 7. UNKNOWN ledger (#367-unreconciled)

These rows are pinned UNKNOWN with reasons; they are never PASS, never
allowed, and need #367/#648 evidence before any promotion:

| UNKNOWN trigger | Reason constant | Status |
|---|---|---|
| mapped bank outside `$F8-$FB` | `UNKNOWN_OTHER_BANK` | other-bank mapping vs WRAM-only preflight unreconciled |
| TIA/TAI alternation across a page | `UNKNOWN_ALTERNATION_XPAGE` | pair phase across boundary unreconciled |
| bank `< $80` with a real mapper | `UNKNOWN_MAPPER_BANK` | synthetic setup installs no mapper |
| device-originated (DMA) writes | `UNKNOWN_DEVICE_DMA_W17` | no real-core device-write measurement (W17) |

`UnknownLedgerTests` pins every UNKNOWN row's reason and asserts
`gate == BLOCKED` on all 56 matrix rows. Mutation REDs: modelling every
opcode as TII disagrees with the exact TDD/TIN/TIA/TAI walks
(`mut-tseq` analogue); a first-byte-only check wrongly allows a walk whose
second byte lands on the hardware page (`mut-tfirst` analogue); executing
len0 instead of refusing is forbidden; partial fetch capture never passes.

## 8. Fresh real-core evidence: 51-case matrix (this issue)

One heavy real-core invocation was run for this issue (heavy-emu budget 1
of max 2; the second slot is left unused):

```
python3 tests/integration/geargrafx_block_transfer_matrix.py
```

Result: **51/51 `REAL_CORE_PASS`** (25 length + 4 page-boundary + reverse +
alias + overlap + 2 TAM + len0 + 15 deny-hw/rom/backup + nonmono-TIA),
**51/51 `MUTATION_EVIDENCE` clean**, totals
`defined=1020 excluded=3 applied=1017 detected=1017 accepted=0`,
`PASS: 51 synthetic pinned real-core matrix cases; production safety
remains BLOCKED`. Per-case `EVIDENCE` JSON (observation kind
`REAL_CORE_EXECUTION`, core `b49ae82a...`) and the `MUTATION_TOTAL` line are
in the run log attached to the PR. The run re-verifies the exact
`addr_lists` walks this issue's host table mirrors (including the TDD
reverse walk, the `$F8/$F9` alias offsets, the len0 whole-instruction
refusal, and the TIA non-monotonic deny), plus per-row MPR-immutability,
TransferEnd CPU/VDC restoration, and stack-frame checks. It does not show
device-DMA safety, mapper-bank behaviour, real hardware behaviour, or
production-gate readiness (§9).

## 9. Non-goals and production gate

* No `src/` change, no accessor added, no wiring position enabled. The
  destination preflight policy stays verification-only; the per-byte write
  guard and mid-transfer refusal semantics are #367/#648 BLOCKED territory.
* Unmeasured and therefore never PASS: mapper banks `< $80` with a real
  mapper installed, libretro build (#103), real hardware (#655), device DMA
  (#367 W17), ST `WriteFromCPU` bypass (#173), IRQs newly asserted inside
  the fetch window, `m_mpr_buffer` aliasing beyond the inherited record,
  and true free-run without a per-instruction stop.
* Overall production verdict for P07: **BLOCKED (fail closed)**. The
  regression table is exact on the measured WRAM paths
  (HOST_MODEL + one fresh 51-case REAL_CORE run); every gap above stays
  UNKNOWN/BLOCKED until the owner approves wiring and the #367 evidence
  lands. Over-refusal (unpeekable/unreconciled -> refuse/UNKNOWN) is
  accepted as the safe direction.

## 10. Files, tests, review

* Added: `tools/xfer_722_matrix.py` (56-row host verdict matrix, reuses
  `peek_table`, no peek reimplementation), `tests/host/test_722_five_xfer.py`
  (40 host tests: matrix exactness, len0, per-op walks, boundaries, banks,
  fail-closed, self-overwrite, UNKNOWN ledger, no-reimplementation,
  mutations), this document.
* Reused unchanged via cherry-pick of PR #766: `tools/peek_table.py`,
  `tests/host/test_717_peek_failclosed.py`, `docs/717-peek-failclosed.md`.
* Ran: `python3 -m unittest discover -s tests/host -p 'test_722*' -v`
  (40 OK, host-only); `python3 tools/xfer_722_matrix.py`
  (56 rows, 0 mismatches, gate BLOCKED); `python3
  tests/integration/geargrafx_block_transfer_matrix.py` (1 heavy real-core
  run, 51/51 + mutations clean, §8); `git diff --check` clean.
* Independent review: requested on the Draft PR; CRITICAL 0 / MAJOR 0
  required before any follow-up. Review must confirm no GPL text was copied,
  no `./rom` input was used, no production gate string changed, and peek
  was reused rather than reimplemented.

`release_eligible=false`, ABI VERIFIED 0/81, DEV_CALLABLE 0 unchanged.
