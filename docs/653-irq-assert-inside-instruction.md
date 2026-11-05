# Issue #653 (S7 of #473): IRQs newly asserted inside an instruction

Status: **verification-only measurement** on the pinned Geargrafx b49ae82 (synthetic BIOS/CD, verification-only
patch). It is not a production mechanism and not a gate change. Geargrafx observations are not proof of hardware
behaviour (real hardware: #655). #173 / #367 / #459, BLOCKED_ABI, release_eligible, DEV_CALLABLE and ABI VERIFIED
are unchanged.

## What the pinned core does (source)

- `RunInstruction` runs: opcode fetch, then `CheckIRQs()`, then the opcode, then `HandleIRQ()` if an IRQ is pending
  and the transfer state is 0.
- Hardware time advances inside an instruction only at hardware clock points (`ClockHardwareCycles`: VDC/VCE access,
  PSG write, VDC stall), and each such point runs `CheckIRQs()` again. The cycles left after the instruction are
  clocked by the core loop, without `CheckIRQs()`.

The patch adds `gg_verify_clock_observer` at those clock points (after the clock, before `CheckIRQs`). It also adds a
timer-cycles accessor and setter, used only to arm the timer at an instruction start, and two core mutants: 8 (no
`CheckIRQs` at clock points) and 9 (IRQ taken inside a block transfer).

## Fixture

Route `ni_<base>_<src>_<k>[_i]`: an IRQ is asserted at instruction k.

| src | How the IRQ is asserted |
|---|---|
| `t` | Timer (TIQ). Armed at the start of k so that it expires at the first hardware clock after that point. |
| `q1` / `q2` | The IRQ1 / IRQ2 request bit is set at the start of k. |
| `q1m` / `q2m` | The bit is set at the first clock point inside k. If k has none, nothing is injected: **NOT_MEASURED**. |

| base | Instruction under test |
|---|---|
| `tam` | TAM #$02 |
| `sta` | STA to the VDC, which has a clock point |
| `tii`…`tai` | WRAM → WRAM transfers (no clock points) |
| `tiiv`…`taiv` | Transfers to the VDC data port (clock points in the byte steps) |

`_i` starts the run with I = 1. The timer setting is deterministic: it depends only on the master cycles clocked from
the arming point.

## Results (expectations fixed before CI, checked by a local exploratory run)

| Case | Visible at | Taken (first handler instruction) |
|---|---|---|
| TAM, IRQ pending before it (`q1_1`) | inside the TAM | at the end of the TAM (2). The S5 hole: UNSAFE under A (pushes to $2104…), SAFE under A+D |
| TAM, TIQ raised in its cycles (`t_1`) | start of the next instruction (TAM has no clock point) | after the next instruction (3). With A = $FF, A refuses that instruction: SAFE. A+D refuses the TAM: SAFE |
| TAM, assert inside the TAM (`q1m_1`) | — | **NOT_MEASURED**: there is no clock point inside TAM on the pinned core |
| STA to the VDC, assert at its clock point | inside the STA | at the end of the same STA (2) |
| Block transfer, assert in TransferStart or a byte step (`t_1`…`t_5`, `q1_3`, VDC `q1m_3`) | during the transfer | only after TransferEnd (7). IRQ1/IRQ2/TIQ, all 5 opcodes, A and A+T |
| Block transfer, assert in the TransferEnd step (`t_6`, `q1_7`) | the next instruction | after it (8) |
| I = 1 | — | never |

**Mutants**

- No `CheckIRQs` at the clock point: the STA case is taken one instruction later (3 instead of 2).
- No injection: the IRQ is never taken.
- IRQ inside a transfer: taken at instruction 4. Under A+T, the next step runs the handler's opcode, so T refuses it
  (`transfer_opcode_changed`, inside the transfer: BLOCKED).

The SAFE armed rows are falsified on every criterion. Criterion 6 compares the IRR at the refused instruction's start
with the stop, because the asserts happen before the refusal by design.

## D and T under these conditions

- **Method D:** IRQs newly asserted in TAM's cycles do not add a hole on the pinned core. They are taken only after
  the next instruction, which method A already judges. The only TAM hole remains an IRQ that is pending before the
  TAM, which D closes.
- **Method T:** IRQ entry does not happen inside a transfer, so an IRQ handler cannot change a mapping mid-transfer.
  If a core did take it (mutant 9), T refuses the next step.

## Not covered (UNKNOWN / NOT_MEASURED)

- An assert inside TAM itself (no clock point).
- Device-originated IRQ1 (VDC) and IRQ2 (CD) timing: modelled only by harness request bits.
- ST0/ST1/ST2 (#365 / #429).
- DMA (#367).
- The libretro build (#103).
- Real hardware (#655).
