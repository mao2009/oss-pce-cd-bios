# Issue #473 (design only): production safety mechanism for stack push/pop during block transfer after TransferStart

Status: **design only**. No implementation. No production safety guard is enabled. #173 / #367 / #459 remain BLOCKED.
`release_eligible`, ABI VERIFIED, DEV_CALLABLE, referenced JSR permission and production allowlist are unchanged.
The project is not a compatible System Card BIOS.

## 1. Purpose and scope

This document selects (for verification planning only) a production safety mechanism that protects stack
push/pop during block transfer after `TransferStart`. It does not implement any mechanism. Implementation
requires explicit owner approval and a separate Issue/PR.

Sources: `docs/473-stack-safety-mechanism-comparison.md`, `docs/473-caller-stack-experiment.md`,
`docs/367-block-transfer-audit.md`, `docs/497-mpr1-stack-contract.md`, `docs/647-decode-time-tam-refusal-irq-deferral.md`,
`docs/648-block-transfer-prestart-midtransfer.md`. Observations: #470, #459, #367, #481, S0 (#473 comment 2026-10-22).

## 2. Evidence: TransferStart / TransferEnd

Pinned Geargrafx `b49ae82` (`src/huc6280_opcodes_inline.h`):

- `OPCodes_TransferStart` (668-674): sets `m_transfer_state = 2`, then pushes Y, A, X via `StackPush8` x3.
- `OPCodes_TransferEnd` (685-690): pops X, A, Y via `StackPop8` x3, then applies PC +6 and resets state.

All push/pop go through `StackPush8/16` and `StackPop8/16` (`huc6280_inline.h` 371-399), which use
`STACK_ADDR | S` with MPR1 as the bank. When MPR1 = $FF, the stack page lands on the hardware page
(`$2100-$21FF`), so push writes and pop reads hit VDC registers.

Observed (#470): with MPR1 = $FF and no guard, TransferStart's push writes VDC; pop reads uninitialised
slots, changing A/X/Y from `$12/$34/$07` to `$BE/$EF/$05` and clearing VDC status `$05` -> `$00`.
Push-side refusal alone leaves the pop-side read side effect (O3). Transfer body still runs, destination
receives `$5A` (O4).

## 3. Method comparison (A / B / C)

| Method | Definition | Feasibility | Scale | Side effects | Tests |
|---|---|---|---|---|---|
| A | Refuse dangerous MPR1 mapping before it is used (pre-refusal) | High; reuse step-execution harness (O11) | Low-Medium (harness-side) | IRQ-in-TAM hole (UNMEASURED, N-IRQ-TAM) | Synthetic ROM, MPR1=$FF negative |
| B | Protect TransferStart push .. TransferEnd pop as one unit | Low standalone; pop hook needed | Medium | Pop suppression changes instruction meaning (not proposed) | Real core A/X/Y/VDC/S/P/PC/MPR |
| C | Stop immediately before each stack access side effect | Medium; 4 functions as chokepoint | High (verification patch) | Leftover PC/S/P/MPR/VDC/WRAM state (9.2) | Real core, push/pop pre-hook |

Per S0 (owner-approved verification policy, not mechanism approval): A first, C as complement, A+C combined;
B is not a standalone candidate. B's pop-before hook is a subset of C.

## 4. Push/pop and I/O side effects

- Push order: write then S--. Refusal before write leaves S unchanged.
- Pop order: S++ then read. Refusal before read still leaves S incremented (unless stop point is defined
  before S change). This is a required definition before testing (S0 item 4).
- TransferStart push before guard: `m_transfer_state = 2` already set even if push is refused (9.2).
- TransferEnd pop before guard: transfer body already executed; destination already written (O4).
- IRQ entry push during IRQ-in-TAM: same `RunInstruction` as TAM; A alone cannot stop it (8).

## 5. Synthetic ROM verification plan (design only)

Negative cases (keep):
- MPR1=$FF: 5 opcodes (TII/TIN/TIA/TDD/TAI) (#470), JSR+PHA (#481 CASE_IO), PHP, BSR, IRQ entry,
  RTS/RTI pop, other banks (ROM, BRAM, CD RAM), lengths != 1 and 0.
- IRQ-in-TAM (N-IRQ-TAM): TAM #$02 with pending IRQ1, I=0, A=$FF.

Positive cases:
- MPR1=$F8: #481 CASE_RAM and #470 stackwram (30 cases) must still PASS.
- Regression: #470 30 cases PASS; unguarded control reproduces side effects.

Verification order: S1 (allowlist) -> S2 (A + N-IRQ-TAM) -> S3 (C) -> S4 (A+C) -> S5/S6 (decode-time vs
deferral, pre-start/mid-transfer). Each as 1 Issue = 1 Draft PR.

Acceptance after refusal/stop: A/X/Y, VDC AR/R[7]/status, S, P, PC, MPR0-7, destination byte, WRAM stack
image, IRQ pending state match pre-refusal values (with stop-point definition fixed first).

## 6. BLOCKED conditions (maintained)

- #459 / #367: BLOCKED (unchanged). #459 closed as observation; gate not opened.
- #173: BLOCKED.
- release_eligible unchanged; ABI VERIFIED 0/81; DEV_CALLABLE 0; referenced JSR permission 0/81.
- Production allowlist UNDECIDED.
- VERIFIED not granted; no release_eligible change; no production guard enabled.
- Synthetic ROM only; no commercial ROM/BIOS bytes published.
- No implementation without owner approval.

## 7. Open items / unresolved

- Owner approval of any method for production: not granted.
- Stop-point definition for C (S0 item 4): not fixed.
- IRQ newly asserted during TAM/transfer: UNKNOWN.
- MPR buffer / TMA #$00: UNKNOWN.
- Real hardware behaviour: unverified.
- NMI: NOT_APPLICABLE on pinned core; real hardware unknown.

## 8. Recommendation

Design-only: proceed with verification planning (A first, C complement, A+C) under S0. Do not implement or
enable any production guard. Keep all gates BLOCKED.
