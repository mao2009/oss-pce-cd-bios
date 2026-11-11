# Issue #718 (P03 of #473): synthetic HuC6280 A+D+T fixtures, gap-only synthesis

Status: **host-model synthetic expectation only**. Not a production mechanism,
not a gate change, not a real-core run. The project is not a compatible
System Card BIOS. #173 / #367 / #459, BLOCKED_ABI, `release_eligible=false`,
DEV_CALLABLE 0, ABI VERIFIED 0/81 are unchanged.

## What this adds (and what it reuses)

#647 (S5 decode-time TAM refusal / IRQ deferral), #648 (S6 pre-start /
mid-transfer), #653 (S7 newly-asserted IRQ timing), and #654 (S8
`m_mpr_buffer` / TMA #$00) already own the drivers, the verdict engines, and
the real-core runs. This issue adds **no new driver and no new core run**.
It adds one unified fixture table and one host check:

- `tests/fixtures/huc6280_adt_synthesis.json` — 17 gap-only rows that pair
  each forbidden case with its WRAM control, name the expected refusal
  position, forbid resuming, discriminate the owning mechanism, guard the
  clock-point-less TAM assert as NOT_MEASURED, and keep the synthetic
  expectation and the real-core observation in separate fields.
- `tests/host/test_718_huc6280_adt_fixtures.py` — host-only checks (no cc65,
  no Geargrafx, no BIOS/ROM/`./rom/` read).

Reuse rule: every row carries a `reuses` pointer into S5/S6/S7/S8 case ids.
The host test asserts each pointer family exists in the corresponding
driver's `cases()` list. Driver logic (`check_*`, `expected`, `gg_verify`,
transfer state) is not copied into the fixture.

## Gap analysis (why these 17 rows)

| #718 acceptance item | Already in drivers | Gap closed here |
|---|---|---|
| MPR1=$FF negative vs MPR1=$F8 positive | S5 sweep (all 256 masks, A=$F8/$FF) scattered across sweep + IRQ tables | `718-tam02ff-neg` / `718-tam02f8-pos` pair with `before_TAM_decode` vs `none_allowed` in one row pair |
| MPR0 code-page hole (TAM #$01, fail-closed) | S5 `tamcode` armed + A control | `718-tam01ff-codepage-neg` with explicit fail-closed reason (next opcode unpeekable, never fetched) |
| src/dst I/O crossing per direction | S6 `d`/`s`/`c` kinds + CROSS_IO counts per opcode | `718-tii-dstio-neg` (dst_io), `718-tin-srcio-neg` (src_io), `718-tdd-down-neg` (decrement), `718-tia-alt-neg` (alternate) with direction labels and TDD/TIN/TIA semantics |
| self opcode overwrite + len0 edge | S6 `xself` / `xlen0` armed | `718-xself-neg` / `718-xlen0-neg` paired against `718-xnoself-pos` (WRAM-only, no self overlap, no false positive) |
| mid-transfer vs pre-start | S6 `m`/`m1`/`e` BLOCKED rows | `718-mid-transfer-blocked` pins `mid_transfer_BLOCKED` (state 2, counted bytes, resume forbidden via mut-tresume RED) against the `before_TransferStart` rows |
| IRQ-in-TAM | S5 pending-IRQ table + S8 `mb_irqf` buffer row | `718-irq-pending-neg` discriminates D-owned pending hole (AD `before_TAM_decode`, buffer `$02`->`$02`) from A UNSAFE and AE visible `$FF` |
| buffer (TMA #$00) | S8 `tam1`/`tamm`/`tmam`/`tam0`/`irq` | `718-tma00-buffer-pos` / `718-tam00-buffer-pos` carry the buffer-equality + A-cleared-before-TMA falsifiability into the synthesis |
| internally newly asserted IRQ | S7 `ni_*` timing + REPEATABLE + mutants 8/9 | `718-irq-newassert-deferred` (timer at a transfer clock point, taken only after TransferEnd) vs `718-irq-pending-neg`, with mutant-9 T-BLOCKED noted |
| clock-point-less TAM assert | S7 NOT_MEASURED list | `718-irq-tam-internal-notmeasured`: TAM has no clock point, nothing is injected; `synthetic_expect.verdict` is `NOT_MEASURED` and the host test rejects any PASS/SAFE/REFUSE/ALLOW/BLOCKED reading |
| refusal position + no-resume per fixture | per-driver `index`/`instructions_after_refusal` | every row has `refusal_position` (`before_TAM_decode` / `before_TransferStart` / `mid_transfer_BLOCKED` / `none_allowed` / `NOT_MEASURED`) and `resume_prohibited` (true for every REFUSE/BLOCKED/NOT_MEASURED); host test enforces the coupling |
| expectation vs observation split | drivers mix EXPECT with measured rows | every row has `synthetic_expect` (this fixture) and `real_core_observation: {status: NOT_RUN, verdict: null}` (the S5–S8 drivers own the runs) |

## Acceptance mapping

- MPR1=$FF negative with MPR1=$F8 contrast: `718-tam02ff-neg` / `718-tam02f8-pos`.
- src/dst I/O crossing: `718-tii-dstio-neg`, `718-tin-srcio-neg`, `718-tdd-down-neg`, `718-tia-alt-neg`.
- Self opcode overwrite: `718-xself-neg`, `718-xlen0-neg` vs `718-xnoself-pos`.
- IRQ-in-TAM vs buffer vs internal new assert: `discriminant` field on every row; `718-irq-pending-neg` (D), `718-tma00-buffer-pos`/`718-tam00-buffer-pos` (buffer), `718-irq-newassert-deferred` (S7 timing).
- Clock-point-less TAM assert: `718-irq-tam-internal-notmeasured` is NOT_MEASURED; the host test fails if it is ever judged PASS/SAFE/REFUSE/ALLOW/BLOCKED.
- Refusal position + guest no-resume: `synthetic_expect.refusal_position` + `resume_prohibited` on every row; `mid_transfer_BLOCKED` is BLOCKED (never SAFE), pre-start refusals write 0 bytes.
- Separate fields: `synthetic_expect` vs `real_core_observation` (NOT_RUN here; see the S5–S8 drivers for the runs).

## Evidence and limits

- Ran: `python3 -m unittest discover -s tests/host -p 'test_718*' -v` (host-model only, 12 tests, OK).
- NOT_RUN here: every `real_core_observation.status` is `NOT_RUN`. Real-core evidence, if needed, comes from the S5/S6/S7/S8 drivers (`make stack-s5`, `make stack-s6`, `make stack-irq-timing`, `make stack-mpr-buffer` in `make integration`); this PR does not rerun them and claims nothing about them.
- No `./rom/`, no commercial media, no reference JSR, no System Card bytes. Pinned Geargrafx `b49ae82` is named as reference only and is not executed by this fixture.
- Negative/mutation coverage is by construction: each NEGATIVE has a paired POSITIVE control, each refusal forbids resume, and the NOT_MEASURED guard is exercised by the host test (a mislabelled verdict fails the suite). Driver-level mutants (D-off/wrong-bit/E-off, mut-tresume/tnomid/tfirst/tseq/tnoself, clock mutants 8/9, buffer-constant) stay in S5–S8 and are not duplicated.
- Geargrafx observations, where they exist in S5–S8, are not proof of hardware behaviour (real hardware: #655 UNKNOWN).

## Provenance

Original synthetic sketches only (instruction mnemonics, operand masks, and
addresses as text). No upstream code is copied, adapted, or referenced beyond
the issue numbers. No firmware, disc, audio, save, or `rom/` bytes are
committed, logged, or attached. `provenance_guard` scope is unchanged (JSON
fixture + host test + this doc; no binary, no third-party path).
