# Issue #719 (P04 of #473): WRAM positive + deny/allow bidirectional regression

Status: **host-model synthetic expectation only**. Not a production mechanism,
not a gate change, not a real-core run. The project is not a compatible
System Card BIOS. #173 / #367 / #459 stay BLOCKED. `release_eligible=false`,
ABI VERIFIED 0/81, DEV_CALLABLE 0, reference JSR 0/81 are unchanged.

Scope (plan 4): the over-refusal and allow-leak of method A+D+T are detected
symmetrically with a synthetic ledger. Verifying the production guard wiring
is out of scope and not implied.

## What this adds (and what it reuses)

#718 (PR #768) owns the gap-only synthesis fixture
(`tests/fixtures/huc6280_adt_synthesis.json`, 17 rows) and its host check.
This issue adds **no new fixture file and no new driver**: it adds one
focused WRAM ledger inside the host test plus this doc.

- `tests/host/test_719_wram_deny_allow.py` — host-only checks (no cc65, no
  Geargrafx, no BIOS/ROM/`./rom/` read). Six ledger rows with an independent
  per-row judge (`judge_row`), paired ALLOW/REFUSE contrast, zero-device-effect
  assertions, UNDETERMINED/BLOCKED guards, and mutation checks.
- This doc — acceptance mapping, gap analysis, evidence, and limits.

Reuse rule: #718 row ids appear only as `cites718` reference strings; the
718 JSON is not copied. Driver verdict engines (`check_stack_bank`,
`judge_ram`, `check_stackwram_armed`, S5/S6 `cases()`) are called or cited,
never reimplemented. The host test asserts each reuse pointer names a live
driver family and fails if driver logic tokens appear in the test file.

## Ledger (6 rows)

| id | polarity | MPR1 | kind | verdict | device io/vdc | ground |
|---|---|---|---|---|---|---|
| `719-wram-stack-allow` | POSITIVE | `$F8` | stack | ALLOW | 0/0, unchanged | #481 CASE_RAM + #470 stackwram + #497 bank PASS |
| `719-unrelated-allow` | POSITIVE | `$F8` | unrelated | ALLOW | 0/0, unchanged | no over-refusal of non-stack/non-transfer ops |
| `719-io-stack-deny` | NEGATIVE | `$FF` | stack | REFUSE | 0/0, unchanged | #497 bank FAIL; cites `718-tam02ff-neg` |
| `719-io-xfer-deny` | NEGATIVE | `$F8` | transfer, crossing | REFUSE | 0/0, unchanged | A passes `$F8`, T refuses pre-start; cites `718-tii-dstio-neg` + `718-tin-srcio-neg` + `718-tdd-down-neg` + `718-tia-alt-neg` |
| `719-undetermined-blocked` | NEGATIVE | UNDETERMINED (None) | stack | BLOCKED | unobserved | reset never trusted; no harness init; never ALLOW |
| `719-unobserved-blocked` | NEGATIVE | `$F8` | stack, IRQ unknown | BLOCKED | unobserved | `judge_irq_window` UNKNOWN -> BLOCKED; cites `718-irq-pending-neg` |

Refusal-position shorthand: the bank deny is `before_TAM_decode`-equivalent
(TAM never runs under D); the transfer deny is `before_TransferStart`
(0 bytes moved); BLOCKED rows have no resume (kill semantics only). The
positions themselves are owned by #718/S5/S6; this ledger pins only the
ALLOW-vs-REFUSE-vs-BLOCKED direction plus the zero-side-effect coupling.

## Acceptance mapping

- `MPR1=$F8` WRAM positive (#481 CASE_RAM, #470 stackwram) regression:
  `test_case_ram_positive_still_passes` (existing `judge_ram` over synthetic
  `_ram_records` passes), `test_case_ram_injected_negative_still_fails`
  (stray `$0100` write still fails), `test_stackwram_positive_is_still_the_allow_side`
  (`_expected_mpr("stackwram") == ($F8, $F8)`, `tii-stackwram-armed` present,
  `STACK_WRAM_LANDED == [0x34, 0x12, 0x07]`, restored A/X/Y pinned).
- `MPR1=$FF` / I/O deny negative and unrelated-instruction allow positive:
  `test_pairs_contrast_in_both_directions` asserts `719-wram-stack-allow`
  and `719-unrelated-allow` judge ALLOW while `719-io-stack-deny` and
  `719-io-xfer-deny` judge REFUSE, with the bank-vs-transfer ground split
  (`$FF` fails the bank check; `$F8`+crossing passes the bank check and is
  refused by the crossing flag).
- No device read/write side effects on failure; unobserved is UNKNOWN/BLOCKED:
  `test_refusal_rows_show_zero_device_effects`,
  `test_allow_rows_show_zero_device_effects_and_permit_resume`,
  `test_unobserved_is_blocked` (`judge_row` rejects any non-BLOCKED reading
  of an unobserved row; `judge_irq_window` UNKNOWN -> BLOCKED).
- `MPR1` initial value UNDETERMINED never allows:
  `test_undetermined_mpr1_never_allows` (`RESET_MPR1 is None`, `check_stack_bank(None)`
  and `check_initial_state(*, None, *)` are BLOCKED, mutated ALLOW raises).
- Independent assert per case:
  `test_every_row_judges_to_its_declared_verdict` plus per-row `subTest`
  isolation; `test_flipped_verdicts_are_rejected`,
  `test_injected_device_effect_is_rejected`, and `test_resume_flip_is_rejected`
  prove each direction can fail on its own.

## Gap analysis (why these 6 rows and not 17 more)

#718 already pairs TAM contrast, four transfer directions, self-overwrite,
mid-transfer, IRQ, buffer, and NOT_MEASURED rows. Duplicating that table here
would fork the synthesis. The gap closed here is the one #719 owns: the WRAM
trust boundary in both directions — the `$F8` allow side pinned to its two
live positive controls (#481/#470), the `$FF` deny side, the `$F8`+crossing
deny side that A alone would leak (T-owned), the unrelated-op allow side that
an over-eager guard would break, and the two never-safe sides
(UNDETERMINED init, unobserved IRQ). The `cites718` strings are the bridge;
the verdicts are re-derived here from the #497 contract, not copied from the
718 JSON.

## Evidence and limits

- Ran: `python3 -m unittest discover -s tests/host -p 'test_719*' -v`
  (host-only; see PR report for the count and exit code).
- Related: `python3 -m unittest discover -s tests/host -p 'test_mpr1_stack_contract*'`,
  plus the #481 selftest embedded in `test_case_ram_*` (synthetic records only).
- NOT_RUN here: every real-core observation. Real-core evidence, if needed,
  comes from the S5/S6 drivers, the #481 probe, and the #470 write-guard
  probe (`make stack-s5`, `make stack-s6`, `make stack-access-guard`-family in
  `make integration`); this PR does not rerun them and claims nothing about them.
- No `./rom/`, no commercial media, no reference JSR, no System Card bytes.
  Pinned Geargrafx `b49ae82` is named as reference only and is not executed
  by this test.
- Negative/mutation coverage is by construction: each ALLOW has a REFUSE
  counterpart with an independent ground, each refusal forbids resume, each
  unobserved shape judges BLOCKED, and the mutation tests flip verdicts,
  inject device effects, and flip resume (all must raise; a silent accept
  fails the suite).
- Geargrafx observations, where they exist in S5/S6/#481/#470, are not proof
  of hardware behaviour (real hardware: #655 UNKNOWN).

## Provenance

Original synthetic sketches only (MPR/bank/kind/crossing labels as text plus
calls into the repository's own contract and driver modules). No upstream code
is copied, adapted, or referenced beyond issue numbers. No firmware, disc,
audio, save, or `rom/` bytes are committed, logged, or attached.
`provenance_guard` scope is unchanged (host test + this doc; no binary, no
third-party path).
