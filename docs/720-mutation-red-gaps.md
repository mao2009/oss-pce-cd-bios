# Issue #720 (P05 of #473): D/T mutation RED gaps — host gap suite

Status: **verification-only measurement plan**. Not a production mechanism,
not a gate change. The project is not a compatible System Card BIOS.
#173 / #367 / #459, BLOCKED_ABI, `release_eligible=false`, DEV_CALLABLE 0 and
ABI VERIFIED 0/81 are unchanged. No production stack allowlist is introduced.

## Question

#647 (S5: D decode-time TAM refusal vs E IRQ deferral) and #648 (S6: T
pre-start / mid-transfer) already define real-core mutants and host expectation
pins. #720 asks for the missing RED only: every disabled guard must fail a
**named** host assertion, D vs E must not be confused, each T mutant kind must
RED through its own judge, the ST no-entry-check technique (#712) must hold
for D/T pre-start, and import-failure / ERROR / timeout must never count as
killed. Existing suites are surveyed first; nothing already covered is copied.

## Survey of existing mutation suites (no duplication)

| Suite | Covers | Not covered (gap for #720) |
|---|---|---|
| `tests/integration/geargrafx_stack_s5.py` `MUTANTS` / `SWEEP_MUTANTS` + `evaluate()` | D-off, D-wrong-bit, E-off real-core rows; wrong-bit sweep both directions; falsified rows | Host RED through named judges is not asserted; D/E confusion cross-check is not asserted |
| `tests/host/test_stack_s5.py` | Forbidden-iff-bit, full-mask sweep coverage, D-keeps-MPR1 vs E-shows-TAM values, mid-transfer BLOCKED, mutant UNSAFE+visible dict pins | Mutant rows are not fed to `check_policy_row` / `check_sweep`; cross-expectation RED is not asserted; core/hook version pin is absent |
| `tests/integration/geargrafx_stack_s6.py` `MUTANTS` / `FALSE_POSITIVE_MUTANTS` + `evaluate()` | `mut-tresume` x6 / `mut-tnomid` x5 / `mut-tfirst` / `mut-tseq` / `mut-tnoself` + 3 false positives on real core | Host RED through `check_refused` / `M.verdict` / `check_completed` per kind is not asserted |
| `tests/host/test_stack_s6.py` | 5 opcodes, mid never SAFE, pre-start changes nothing, mutant presence/counts, one happy-path `check_refused` | Per-kind named RED, false-positive `check_completed` RED, `tnoself` opcode-named RED are absent |
| `tests/integration/geargrafx_st_boundary.py` + `tests/host/test_365_st_boundary_record.py` | ST no-entry-check RED pattern (unguarded row cannot pass as refused) | Same pattern for D/T pre-start is absent |
| `tests/host/test_block_preblock.py` | Armed/unguarded classifier + falsification RED | D/T judge RED (different judges) is out of scope, not repeated |
| `test_stack_s5/s6.py` wiring | `make integration` runs stack-s5/stack-s6; patch flag pin | Pinned core revision / hook kind pin for mutants is absent |

## What this issue adds (gaps only)

Host-only file `tests/host/test_720_mutation_gaps.py` (synthetic rows only,
no emulator, no ROM, no core). Each test feeds a synthetic mutant row to the
real judge and requires a named `ValueError`:

- D-off / D-wrong-bit / E-off RED through `check_policy_row` (`verdict`
  mismatch) and `M.verdict == UNSAFE` with the visible predicate true.
- Wrong-bit sweep RED in both directions through `check_sweep`
  (false positive on mask `0x01`; missed refusal on mask `0x02`).
- D vs E confusion RED in both directions: the AD armed row fails the AE
  expectation and vice versa (`refusal reason` / `MPR1` / `refused
  instruction` / `verdict`); the same forbidden TAM fails under the wrong
  sweep method.
- `mut-tresume` RED (`M.verdict == UNSAFE`, `check_refused` rejects),
  `mut-tnomid` x5 RED, `mut-tfirst` RED, `mut-tseq` landed RED, `tnoself`
  (`AT:xself-mut-tnoself`) BLOCKED with named `transfer_opcode_changed` plus
  landed-without-check UNSAFE RED, and the 3 `mut-tseq` false positives RED
  through `check_completed` (`refused`).
- ST no-entry-check technique for T pre-start: unguarded landed rows never
  pass as refused (`check_refused` / `check_sweep` raise).
- Import failure / ERROR / timeout never killed: `exit != 0`, `TimeoutExpired`
  marker, and missing-evidence rows raise in `_row_ok` / `check_*`, and every
  `MUTANTS` visible predicate is false on them.
- Target version / expectations pin: `b49ae82a012eade566d72e9d61c9d95a5caa4da3`
  shared by the S5/S6 drivers (`S5.G is S6.G`), mutant ID sets equal to the
  #647/#648 specs, mid-transfer `BLOCKED` with `resume_safe` failing.

## Mutation target / expectations / safety inspected

- Target: pinned Geargrafx `b49ae82a012eade566d72e9d61c9d95a5caa4da3`,
  synthetic BIOS/CD, verification-only patch/hook. No reference BIOS.
- D expectations: forbidden TAM refused before fetch (`decode_tam`, 1 insn,
  PC `$1E00`, MPRs unchanged, one decode refusal); allowed TAM applied exactly
  (2 insns, PC `$1E02`, harness budget stop).
- E expectations: TAM runs (`MPR1 = $FF` visible), IRQ stays pending, method A
  refuses the next start (2 insns, PC `$1E02`, reason `mpr1`).
- T pre-start: source/destination full-range + own-opcode check before fetch;
  `SAFE_IN_SCOPE` only when nothing changed. T mid-transfer: opcode + MPRs
  unchanged at every step; any refusal inside is `BLOCKED` (`resume_safe`
  failing), bytes already written are counted, never hidden.
- Safety inspected here: host judges RED on every required mutant kind; ERROR
  rows do not inflate the kill count; mid-transfer is never `SAFE`.
  NOT covered (UNKNOWN, never PASS): real-core execution itself (run in CI
  `verify`, not here), newly asserted IRQ inside TAM/transfer, `m_mpr_buffer`
  (see #654), ST0/ST1/ST2, device-originated writes (#367 W17), libretro
  build, real hardware. NMI not applicable on the pinned core.

## Evidence and provenance

- Category: host-model tests only. Real-core D/T evidence stays in the S5/S6
  drivers and CI `verify`; commercial-disc structural observations, callable
  System Card BIOS API execution, homebrew-disc boot, and game compatibility
  are NOT claimed here.
- Inputs: synthetic rows built in the test file. Private `./rom/`,
  commercial BIOS/ROM bytes, and reference JSR are not used, not read, not
  published. The worktree was verified `rom/`-readonly (no read, no derive).
- Pinned Geargrafx `b49ae82`; synthetic media only. Geargrafx observations
  are not proof of physical hardware behaviour.
- Gates unchanged: `release_eligible=false`, `BLOCKED_ABI`, DEV_CALLABLE 0,
  ABI VERIFIED 0/81, #173/#367/#459 BLOCKED. `make release` stays BLOCKED.

## Review and CI

- `python3 -m unittest discover -s tests/host -p 'test_720_mutation_gaps.py' -v`
  (20 tests, host-only).
- Full host suite: `make test` (required gate, cc65 present in CI).
- Real-core D/T drivers are unchanged; their CI `verify` remains the evidence
  for observed behaviour. Unmeasured items are reported `UNKNOWN`/`BLOCKED`,
  never `PASS`.
