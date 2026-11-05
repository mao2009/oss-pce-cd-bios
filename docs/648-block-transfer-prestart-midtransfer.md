# Issue #648 (S6 of #473): block transfers before the start and during the transfer

Status: **verification-only measurement**. Not a production mechanism, not a gate change, no production allowlist.
The project is not a compatible System Card BIOS. #173 / #367 / #459, BLOCKED_ABI, release_eligible, DEV_CALLABLE
and ABI VERIFIED are unchanged.

## Test policy T (probe method `AT` = method A + T)

- **Pre-start.** At the instruction start of TII / TIN / TDD / TIA / TAI with transfer state 0, the probe peeks the
  six operands without a bus read. It computes every source and destination address of the transfer (pinned
  source: TDD decrements both, TIN keeps the destination, TIA alternates the destination, TAI alternates the
  source, length 0 = 65536). If any byte lies in a page not mapped to WRAM $F8, or a destination byte is one of
  the instruction's own seven bytes (each byte step re-runs the opcode), the whole instruction is refused before
  the opcode fetch. An opcode that cannot be peeked is refused (fail closed). The TransferStart stack
  precondition (MPR1 = $F8) is method A's check.
- **Mid-transfer.** At every later step of the same transfer, the opcode must be the one of TransferStart and no
  MPR may differ from its value at TransferStart. If either changed, the step is refused before it runs.
- After a refusal the guest is never resumed.

The patch adds only read-only accessors for the transfer counters (source, destination, remaining length, step count).

## How the results are judged

- **Pre-start and mid-transfer are reported separately.**
- **Pre-start refusal:** nothing has changed yet, so it can be SAFE_IN_SCOPE.
- **Mid-transfer refusal:** the stop is inside the transfer (transfer state 2 or 1), so it is **BLOCKED** (not safe
  to resume), even with no I/O side effect. The bytes already written before the refusal are counted and reported,
  together with the transfer counters at the stop. They are not presented as undone.
- **Mutants:**
  - `mut-tresume` (guest resumed after a T refusal) shows why resuming must be forbidden: the rest of the transfer
    reaches the hardware page.
  - `mut-tnomid` (no mid-transfer check) and `mut-tfirst` (pre-start check of the first byte only) show that each
    part of T is needed.
  - `mut-tseq` (every opcode modelled as TII) is caught by the per-opcode page-boundary fixtures: TDD crossing
    downwards lands on I/O (UNSAFE), and the TIN / TIA / TAI fixtures that stop before the forbidden page are
    wrongly refused (false positive).
  - `mut-tnoself` (no own-opcode check before the start) shows the mid-transfer opcode check: the first byte
    step rewrites the TII opcode and the next step is refused (BLOCKED inside the transfer).

## Mid-transfer MPR change paths on the pinned core

| Path | Status |
|---|---|
| TAM | Not possible inside a transfer, unless the transfer rewrites its own opcode: each byte step re-runs the opcode at PC. T refuses such a transfer before the start, and refuses a changed opcode mid-transfer. |
| IRQ handler | Not possible: the IRQ is not taken while the transfer state ≠ 0. Measured for IRQ1, IRQ2 and TIQ on all five opcodes: the handler starts after TransferEnd. |
| Harness direct SetMpr | Measured. S1 #497 makes any mid-run direct setting a FAIL. Covered by `m` (destination page before byte step 3), `m1` (MPR1 before byte step 3), `e` (destination page before the TransferEnd step) and the #498 `tend*` rows (MPR1 before the TransferEnd step). Without T, `e` has no side effect (TransferEnd only pops from the stack); T's refusal there is conservative. |

## PR #613 re-evaluated

#613 (`make block-write-guard`) is re-evaluated from the source; it is not re-run here.

- Its whole-instruction abort refuses after the opcode fetch, skips the six operands (PC + 7), and lets the guest
  continue.
- Its per-byte write guard skips one byte write and continues the transfer.

On the eight criteria, the PC / transfer state then differ from the refused instruction's start, and the guest
runs after the refusal. Neither is SAFE_IN_SCOPE. T refuses before the fetch and stops.

## push16 / pop

The two-byte access, the order of the S change and latching (#644 mutants late16, popinc, nolatch, ignore,
nostop) are covered by `make stack-access-guard` (#499) on the same probe and patch in the same
`make integration`. They are not repeated here.

## Evidence

The evidence category is a synthetic-disc real-core diagnostic with a verification-only patch, run on the pinned
Geargrafx b49ae82 by `make stack-s6` in `make integration`. Geargrafx observations are not proof of hardware
behaviour.

The expectations were fixed from the pinned source and a local exploratory build (MinGW, supplementary) of an
earlier revision of the probe. The final probe revision could not be run locally: a rebuilt executable was blocked by
the local OS application-control policy, and that policy was not bypassed. The required evidence is the CI `verify`
job of the PR that adds this file; until it passes, the results are expectations.

## Not covered (UNKNOWN, never PASS)

- Device-originated RAM writes (CD/ADPCM DMA, #367 W17).
- ST0/ST1/ST2.
- An IRQ newly asserted inside a transfer step.
- The libretro build.
- Real hardware.
