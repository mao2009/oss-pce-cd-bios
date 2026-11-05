# Issue #647 (S5 of #473): decode-time TAM refusal vs IRQ deferral

Status: **verification-only measurement**. Not a production mechanism, not a gate change. The project is not a
compatible System Card BIOS. #173 / #367 / #459, BLOCKED_ABI, release_eligible, DEV_CALLABLE and ABI VERIFIED are
unchanged. No production stack allowlist is introduced.

## Question

#498 / #500 left IRQ-in-TAM open: under method A the TAM's own `RunInstruction` maps MPR1 to $FF and, in the same
call, takes a pending IRQ whose entry pushes reach the hardware page ($2104, $2103, $2102). Two independent test
policies were measured on top of method A, on the same fixtures and with the same observations:

| | D: decode-time refusal | E: IRQ deferral |
|---|---|---|
| where | probe, pre-instruction hook | core, `gg_verify_defer_irq` in the verification patch |
| what | peek opcode/operand without a bus read; refuse a TAM with operand bit 1 set and A != $F8; refuse an opcode that cannot be peeked (fail closed) | when the instruction is a TAM, skip the IRQ check at the end of its `RunInstruction`; method A judges the next instruction start |

## Evidence (pinned Geargrafx b49ae82, synthetic BIOS/CD, `make stack-s5` in `make integration`)

Category: synthetic-disc real-core diagnostic with a verification-only patch. Geargrafx observations are not proof of
hardware behaviour.

- TAM bitmask: all 256 operands with A = $F8 and A = $FF under A+D and under A; A = $80 / $F7 / $00 for multi-MPR
  operands under A+D. A+D refuses exactly the operands with bit 1 set and A != $F8, before any MPR changes; all
  other operands are applied exactly (each selected MPR = A, the rest unchanged); no hardware page access.
- IRQ1 / IRQ2 / TIQ pending at a TAM #$02, I = 0 and I = 1: A alone is UNSAFE for I = 0 (all three sources);
  A+D, A+E, A+D+C, A+E+C are SAFE_IN_SCOPE for all six.
- TMA: positive (TMA #$02 then TAM #$02: not refused) and negative (TMA #$80 reads $00: refused; A+D before the
  TAM, A/A+E after it).
- D fail-closed branch: TAM #$01 with A = $FF maps the code page to the hardware page; A+D refuses the next
  opcode without fetching it. Method A (MPR1 only) lets it run (control; MPR0 is outside the S1 #497 contract).
- Mutants: D off, D checking bit 0, E off: UNSAFE (entry pushes land); the wrong-bit D is rejected by the sweep
  check in both directions (missed refusal, false positive). Every SAFE IRQ row is falsified on every criterion.
- Regression: every #498 route under A+D and A+E, with the allowed controls (no false positive); the #498/#499/#500
  drivers are unchanged; the #481 caller-stack contract now runs in `make integration`.

## D vs E (IRQ-in-TAM, I = 0)

| | D | E |
|---|---|---|
| 1 side effect blocked before it happens | yes | yes |
| 2 CPU state at the stop = run start | yes | yes |
| 3 MPRs unchanged | yes (TAM refused) | **no**: the TAM's MPR1 = $FF is applied and stays visible |
| 4 IRQ still pending, not entered | yes | yes |
| 5 transfer state | 0 | 0 |
| 6 nothing ran after the refusal | yes | yes |
| 7 normal operation (allowed run) | handler entered at instruction 2 (unchanged) | entered at instruction 3 (+1 instruction latency per TAM) |
| 8 implementation | probe-side; needs TAM operand decode and a side-effect-free peek | core change in the IRQ check; no decode |

## Not covered (UNKNOWN, never PASS)

An IRQ newly asserted while the TAM executes (every IRQ here is pending before the TAM starts), ST0/ST1/ST2,
device-originated writes, the libretro build, real hardware. NMI is not applicable on the pinned core. The
mid-transfer MPR1 change stays BLOCKED under both policies (S6, #648).
