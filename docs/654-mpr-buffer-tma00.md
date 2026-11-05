# Issue #654 (S8 of #473): m_mpr_buffer and TMA #$00

Status: **verification-only measurement** on the pinned Geargrafx b49ae82, using a synthetic BIOS/CD and a
verification-only patch. The patch adds a read-only accessor, `Memory::GG_VerifyMprBuffer`.

- This is not a production mechanism and not a gate change.
- Equivalence on real hardware is not assumed (see #655).
- #173 / #367 / #459, BLOCKED_ABI, release_eligible, DEV_CALLABLE and ABI VERIFIED are unchanged.

## Pinned source

- `SetMprTAM(bits, value)`:
  - `bits == 0`: returns with no change.
  - Otherwise: sets `m_mpr_buffer = value` and sets each selected MPR to `value`.
- `GetMprTMA(bits)`:
  - `bits == 0`: returns `m_mpr_buffer`.
  - Otherwise: returns the OR of the selected MPRs and stores it in `m_mpr_buffer`.
- `Memory::Reset` sets the buffer to 0.

## Results

The expectations below were fixed before CI and checked against a local exploratory run. The fixture sets MPR3 = $01
and MPR4 = $02, so a multi-bit TMA produces a distinct OR value.

| Scenario | Observed (A and A+D) |
|---|---|
| TAM #$04 (A = $F8), then TMA #$00 | buffer $F8; TMA #$00 returns $F8 |
| TAM #$0C (two MPRs) | buffer $F8 |
| TMA #$18 (multi-bit) | returns and stores $01 \| $02 = $03; TMA #$00 then returns $03 |
| TAM #$00 | buffer unchanged ($02 from the preceding TMA #$10) |
| IRQ1 pending, TAM #$04, handler runs TMA #$00 | the handler reads $F8, the value written by the TAM it interrupted |

| Forbidden TAM #$02 (A = $FF) | Buffer before the TAM → at the stop | Eight criteria |
|---|---|---|
| A+D | $02 → $02 (refused before the TAM) | SAFE_IN_SCOPE |
| A | $02 → $FF (TAM ran; next instruction refused) | SAFE_IN_SCOPE at that stop, with the forbidden value left visible |
| A+E | $02 → $FF | as for A |
| IRQ-in-TAM, A | $00 → $FF | UNSAFE (entry pushes reach the hardware page) |
| IRQ-in-TAM, A+D | $00 → $00 | SAFE_IN_SCOPE |
| IRQ-in-TAM, A+E | $00 → $FF | SAFE_IN_SCOPE |

## Checks

The refused rows add one check to the unchanged eight criteria: the buffer at the stop must equal the buffer at the
refused instruction's start, and must equal the expected value. A changed buffer at the stop is rejected
(falsification).

**Mutants**

- D off and D checking the wrong bit: the TAM runs and the buffer becomes $FF. Both are rejected against the D
  expectation.
- Accessor replaced by a constant: rejected, because the TMA #$00 result in A no longer matches.

## Not covered

- Real hardware (#655).
- The libretro build (#103).
