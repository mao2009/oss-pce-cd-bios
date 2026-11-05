# Issue #648 (S6 of #473): block transfers before the start and during the transfer

Status: **verification-only measurement**. Not a production mechanism, not a gate change, no production allowlist.
The project is not a compatible System Card BIOS. #173 / #367 / #459, BLOCKED_ABI, release_eligible, DEV_CALLABLE
and ABI VERIFIED are unchanged.

## Test policy T (probe method `AT` = method A + T)

- **Pre-start.** At the instruction start of TII / TIN / TDD / TIA / TAI with transfer state 0, the probe peeks the
  six operands without a bus read. It computes every source and destination address of the transfer (pinned
  source: TDD decrements both, TIN keeps the destination, TIA alternates the destination, TAI alternates the
  source, length 0 = 65536). If any byte lies in a page not mapped to WRAM $F8, the whole instruction is refused
  before the opcode fetch. The TransferStart stack precondition (MPR1 = $F8) is method A's check.
- **Mid-transfer.** At every later step of the same transfer, no MPR may differ from its value at TransferStart.
  If one does, the step is refused before it runs.
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

## Mid-transfer MPR change paths on the pinned core

| Path | Status |
|---|---|
| TAM | Not possible inside a transfer: each byte step re-runs the block opcode. |
| IRQ handler | Not possible: the IRQ is not taken while the transfer state ≠ 0. Measured for IRQ1, IRQ2 and TIQ on all five opcodes: the handler starts after TransferEnd. |
| Harness direct SetMpr | Measured. S1 #497 makes any mid-run direct setting a FAIL. Covered by `m` (destination page before byte step 3), `m1` (MPR1 before byte step 3) and, at the TransferEnd step, the #498 `tend*` rows. |

## PR #613 re-evaluated

#613's abort (`make block-write-guard`) is re-evaluated from the source; it is not re-run here.

1. It refuses after the opcode fetch.
2. It skips the six operands (PC + 7).
3. The guest continues.

So on the eight criteria the PC is not preserved and the guest runs after the refusal: it is not SAFE_IN_SCOPE.
T refuses before the fetch and stops.

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
