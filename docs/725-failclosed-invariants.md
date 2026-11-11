# Issue #725: release gate fail-closed invariants (mechanical verification)

Status: **verification only**. No production implementation, no production
guard enabled, no safety gate lifted. `release_eligible=false`,
ABI VERIFIED 0/81, DEV_CALLABLE 0, reference JSR permitted 0/81.
#173 / #367 / #459 stay BLOCKED. `make release` stays BLOCKED.
This project is not a compatible System Card BIOS.

Parent: #473. Design input: PR #714 (design only). Dependencies: #173, #367,
#459. Pinned Geargrafx `b49ae82` is recorded for context; this verification
uses no emulator, no BIOS, no ROM, and no private `./rom/` input.

## 1. Scope

#473 validation progress must never silently lift the safety boundary of
#173 / #367 / #459 or the `make release` boundary. This document and its
mechanical gate verify that boundary statically (issue #725, plan 10):

* `tools/failclosed_gate.py --check` (host-only, stdlib only).
* `tests/host/test_725_failclosed.py` (host-only `unittest`).
* This document records what ran, what did not run, and how a future release
  could ever be unlocked.

What this is not:

* Not a System Card BIOS, not a BIOS ABI, never a release artifact.
* Not a reference-BIOS measurement: every reference callee verification is
  NOT_RUN, every reference JSR execution is BLOCKED, `jsr_count` is 0.
* Not a real-core result: no Geargrafx run, synthetic or otherwise, is claimed
  here. Geargrafx observations would not prove physical-hardware behaviour
  anyway.
* No private `./rom/` media, no commercial BIOS/ROM bytes, no reference JSR
  was used, read, or published.

## 2. Invariants (all mechanically checked)

| # | Invariant | Checker | Today |
|---|---|---|---|
| I1 | `make release` stays BLOCKED (static Makefile contract; the recipe is never executed by the gate) | `check_makefile_release` + `test_make_release_refuses` | BLOCKED, `exit 1` |
| I2 | `release_eligible=false` in every ledger that carries it | `check_spec_release_eligible` | false in `dev_implementations.json`, `abi1_reference_review.json`, `hardware_observations.json`, and every `reference_jsr_gate` slot |
| I3 | Owner-approved DEV_CALLABLE stays 0 | `check_dev_callable_zero` | 0 DEV_CALLABLE, 0 owner-approved, 81 NOT_READY |
| I4 | Reference ABI VERIFIED stays 0/81 | `check_abi_verified_zero` | 81/81 verification NOT_RUN, 0 OBSERVED/PARTIAL/VERIFIED, `reference_abi_verified` 0 |
| I5 | Reference JSR permitted stays 0/81, `jsr_count` 0 | `check_reference_jsr_closed` | 81/81 closed, `execution_permitted` false, `jsr_count` 0, `cdseek_safety_gate` BLOCKED |
| I6 | `tools/build_rom.py --release-gate` always refuses | `check_release_gate_refuses` | current and hypothetical-full (81/81) override sets both raise `ReleaseBlocked("BLOCKED: ...")` |
| I7 | Synthetic PASS, doc-only merge, host PASS never promote | `check_promotion_negatives` | well-formed CONTROLLED approval yields at most MATERIALS_COMPLETE, never ALLOWED |
| I8 | Undecidable release paths fail-closed; the gate itself cannot be disabled | `check_undecidable_fail_closed` | ALLOWED ledger refused, `require_allowed` always raises `GateRefused`, no ALLOWED return path |

The fail-closed rule is literal: any crash, unreadable ledger, or unexpected
value is a FAIL (exit 1), never a pass. The gate has no unlock input and no
write path to any ledger.

## 3. What ran (and what did not)

Ran (host-only, synthetic fixtures only, no cc65 required for this gate):

* `python3 tools/failclosed_gate.py --check` → exit 0, 8/8 PASS, summary
  `{"ok": true, "failed": [], "release_eligible": false,
  "reference_jsr_permitted": 0, "jsr_count": 0}`.
* `python3 -m unittest discover -s tests/host -p 'test_725*'` → all PASS.
* `python3 tools/reference_jsr_gate.py --check` → exit 3 (BLOCKED convention),
  `BLOCKED: reference JSR execution gate closed for 81/81 slots; jsr_count=0`.
* `python3 tools/build_rom.py --release-gate` → exit 3, `BLOCKED: 65
  unimplemented API slots: ...` on stderr (16 DEV overrides present, 65 stubs).
* `make release` (test only, after the static contract already proved the
  recipe is the harmless `echo BLOCKED + exit 1` stub) → non-zero exit with
  `BLOCKED` on stderr, as expected. The gate itself never executes this recipe:
  if the static contract ever fails, the recipe is not run.

Did not run (explicitly NOT_RUN / BLOCKED, not evidence):

* No Geargrafx core, no guest execution, no reference JSR call.
* No commercial disc, no System Card boot, no callable System Card BIOS API.
* No original homebrew-disc boot, no retail-game compatibility run.
* No private `./rom/` input (absent or not applicable; public tests must pass
  without it).

Evidence boundaries per `docs/testing.md` and `AGENTS.md` are kept: host-model
checks, synthetic-disc real-core diagnostics, local commercial-disc structural
observations, callable BIOS API execution, homebrew boot, and game
compatibility are distinct categories. This issue claims only the first
(host-model static verification); every other category is NOT_RUN here.

## 4. Negative tests (promotion is refused)

The gate asserts that none of these promotes anything:

* A fully CONTROLLED hazard table plus a well-formed approval
  (`reviewer`, numeric `pr`, `abi_status: REVIEWED`, non-empty
  `callee_run_evidence`) still yields at most MATERIALS_COMPLETE with
  `execution_permitted: false` and `jsr_count: 0` — never ALLOWED.
* `is_execution_permitted` accepts only the exact string `"ALLOWED"`.
  `None`, `""`, `"allowed"`, `" ALLOWED "`, `"MATERIALS_COMPLETE"`,
  `"BLOCKED"`, `"PASS"`, and non-strings are all refused, so an unexpected
  ledger value can never be read as an allow.
* `release_gate` with all 81 slots overridden still raises `ReleaseBlocked`
  (`override presence does not prove implementation`), so implementation
  count alone never unlocks a release.
* `dev_implementations.validate` refuses `VERIFIED` even with evidence filled
  in (the DEV ledger has no reference-BIOS evidence schema).
* `abi1_reference_review.validate` refuses `OBSERVED` / `PARTIAL` /
  `VERIFIED` from a static review (no independent certification method
  exists).

## 5. Fail-closed on undecidable paths

* A ledger that claims `execution: ALLOWED` is rejected by
  `validate_ledger` (fail closed with `ValueError`).
* Duplicate JSON keys are rejected by the ledger loader (last-one-wins is
  refused).
* `require_allowed("slot-1e")` and malformed targets (`"slot-zz"`) always
  raise `GateRefused` on the committed ledger — there is no allow path.
* The `reference_jsr_gate --check` CLI has no exit-0 path: it counts closed
  slots by the explicit permission test and unconditionally returns 3.
* This gate (`tools/failclosed_gate.py`) contains no `return "ALLOWED"`,
  no `release_eligible: True`, and no ledger write; it only reads the
  committed ledgers and reports PASS/FAIL.

The safety gate itself cannot be disabled by this verification: the gate has
no flag, environment variable, or approval input that changes its verdict.

## 6. Future unlock procedure (independent review + Owner re-approval required)

No unlock is approved by this issue. If a future release is ever proposed,
all of the following are required, in a separate Issue and separate Draft PR
(1 Issue = 1 Draft PR), before any gate text may change:

1. An independent review by a reviewer who did not author the change,
   recorded with reviewer identity, PR reference, hazard-control evidence,
   and callee-run evidence — and even then the current
   `tools/reference_jsr_gate.py` can only reach MATERIALS_COMPLETE, never
   ALLOWED, because no reference-JSR runner exists.
2. Explicit Owner re-approval of the release, recorded in the ledger approval
   fields (`approved_by`, `review_ref`) and in the PR.
3. A reproducible real-core observation plus an implemented independent
   certification method for `OBSERVED` / `PARTIAL`, and a defined promotion
   path for `VERIFIED` — none of which exists today.
4. Closure or explicit re-scoping of #173 / #367 / #459 with the same
   independent review and Owner approval; a passing synthetic suite, a
   doc-only merge, or a passing host suite is never sufficient.
5. The mechanical gate in this issue must be updated in the same PR to expect
   the newly approved state, and must keep failing closed on everything else.

Until every step above is completed and merged, `release_eligible` stays
false, `make release` stays BLOCKED, and any PR that lifts the gate without
them must be refused.

## 7. Provenance

Original files added by this issue: `tools/failclosed_gate.py`,
`tests/host/test_725_failclosed.py`, `docs/725-failclosed-invariants.md`.
No OSS reuse, no upstream pin, no proprietary bytes. Synthetic fixtures only.
No `./rom/` input used or required.
