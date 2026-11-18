# Independent review: PR #816 (CD_SUBRQ / CD_SUBRD synthetic fixture)

- Date: 2026-11-18 (JST)
- PR: https://github.com/mao2009/oss-pce-cd-bios/pull/816
- Implementation Issue: https://github.com/mao2009/oss-pce-cd-bios/issues/272 (parent #22)
- Review-record Issue: https://github.com/mao2009/oss-pce-cd-bios/issues/817
- Inspected PR HEAD: `4e7d8053fc205ecd909afc0fdf708a168d0d00c5`
- Inspected PR base: `fa694281eb4eec4b04beca14278635cb5ca0902a`
- Review disposition: **CHANGES REQUESTED / MERGE HOLD**
- Scope: two new files, 288 added lines; no implementation, ABI or ROM changes.

## Verification and interpretation

GitHub reports the PR as OPEN, mergeable and **non-draft** (`draft=false`).
Four GitHub Actions workflows associated with the inspected HEAD completed successfully:
CI (original fixture, not BIOS compatibility); BIOS DEV diagnostics; HuC6280 core harness;
and Real Geargrafx ex_bcdbin. These are workflow statuses, not evidence of real CD_SUBRQ/SUBRD ABI verification.
The PR body reports 5,719 host tests and 9 new tests; this review did **not** independently run the full test suite.

The synthetic fixture declares `BLOCKED_ABI`, `NOT_RUN`, `release_eligible=false` and
`reference_jsr_count=0`. The review found no evidence in the two-file patch of using a reference BIOS,
commercial disc data or an emulator for this fixture. CodeRabbit was rate limited and did not provide
a substantive review at the observed time.

## Findings

### P1 — PR state conflicts with Issue #272's explicit Draft gate

**Evidence:** The Issue #272 acceptance/safety section explicitly forbids Draft removal and merging.
PR #816 is already `draft=false`, despite its description stating `1 Issue = 1 Draft PR`.

**Action:** Return PR #816 to Draft unless the Owner explicitly changes the Issue gate. Passing CI
or GitHub's `mergeable=true` does not waive the Issue-specific condition. Do not merge as-is.

### P2 — `_well_formed` accepts inconsistent synthetic-medium boundaries

**Location:** `tests/host/test_cd_subrq_subrd_fixture.py`, `_well_formed`.
The loop enforces increasing track numbers and non-overlap (`start > prev_end`), but
not the **contiguous** track boundary represented by the pinned fixture. Nor does it check
`leadout_lba == tracks[-1].end + 1`, despite the author-defined lead-out model.

**Minimal logic reproductions using the PR's judge rules:**

```python
gap = {
    "synthetic": True, "leadout_lba": 200,
    "tracks": [
        {"no": 1, "type": "data", "start": 0, "end": 99},
        {"no": 2, "type": "audio", "start": 101, "end": 199},
    ],
}
q = {"request": "current_q", "track": 2, "lba": 101}
assert _well_formed(gap) is True
assert judge(gap, q)[0] == "POSITIVE"

bad_leadout = {
    "synthetic": True, "leadout_lba": 205,
    "tracks": [
        {"no": 1, "type": "data", "start": 0, "end": 99},
        {"no": 2, "type": "audio", "start": 100, "end": 199},
    ],
}
assert _well_formed(bad_leadout) is True
assert judge(bad_leadout, {"request": "current_q", "track": 2, "lba": 150})[0] == "POSITIVE"
```

**Why it matters:** The Issue requires invalid boundaries not to be accidentally judged positive.
The fixed fixture's `test_medium_boundaries_are_contiguous` does not exercise the judge against
a malformed alternate medium.

**Action:** For this author-defined contiguous model, validate the first start, adjacent
`start == previous.end + 1`, and final lead-out relationship in `_well_formed`;
add negative judge tests expecting `UNKNOWN` for malformed media. If pregaps/gaps are
an intentional supported model, explicitly define those invariants instead of silently accepting them.

### P2 — Boolean control flags can produce false POSITIVE results

**Location:** `judge(medium, query, present=True, needs_callee=False)`.
Tracks and LBAs use `type(value) is int`, correctly rejecting bool-as-int. In contrast,
`present` and `needs_callee` are evaluated by truthiness without verifying they are booleans.

**Minimal logic reproduction:**

```python
valid = {
    "synthetic": True, "leadout_lba": 200,
    "tracks": [
        {"no": 1, "type": "data", "start": 0, "end": 99},
        {"no": 2, "type": "audio", "start": 100, "end": 199},
    ],
}
q = {"request": "current_q", "track": 2, "lba": 150}
assert judge(valid, q, present="false")[0] == "POSITIVE"
```

**Why it matters:** A malformed serialized presence flag can be interpreted as a present medium.
The stated pure-host input validation should not silently promote malformed control fields.

**Action:** Require `type(present) is bool` and `type(needs_callee) is bool` and choose an
explicit non-positive verdict for malformed flags (such as `UNKNOWN`). Preserve the existing
callee-dependence precedence for *valid* boolean flags. Test strings, integers and null values
on each flag, plus the valid `present=False, needs_callee=True` precedence case.

### Minor — Mutation-caught count in PR description is inaccurate

**Evidence:** The PR description says nine deliberately lenient cases are caught, but the
`LENIENT_CAUGHT_IDS` set in the implementation contains **ten** distinct IDs.
The test correctly pins the set; update the text or clarify the intended count.

## Good aspects and limitations

- The four verdicts (POSITIVE / NEGATIVE / UNKNOWN / BLOCKED) are separated and are not
  labeled real hardware compatibility or `VERIFIED`.
- The pinned SHA-256 and explicit expected results guard silent fixture changes.
- The slot ledger stays hypothesis/NOT_RUN and the patch does not wire a callable BIOS entry.
- The filename-only `git grep` wiring test prevents literal references from tracked files,
  but **does not prove** that a generic fixture-directory scanner or wildcard build step
  could never consume the new file. Treat this as a test coverage boundary, not a proven wiring defect.

## Acceptance checklist for PR #816

- [ ] Reconcile Draft state with Issue #272 (Owner approval required for changing the gate).
- [ ] Add malformed-medium gap and lead-out regression tests; close false POSITIVE paths.
- [ ] Add strict bool control-flag validation and regression tests.
- [ ] Correct 9-versus-10 description mismatch.
- [ ] Run the new host tests and check required CI against the new PR HEAD.
- [ ] Keep `BLOCKED_ABI`, `NOT_RUN`, `reference_jsr_count=0`, `release_eligible=false`.
- [ ] Review changed code independently; retain unverified ABI status.

## Scope and safety

This is a static code review with small, independent reproductions of the published Python
control flow, **not** an invocation of the full repository tests, real Geargrafx, commercial
media, or any reference BIOS. No reference BIOS JSR was issued. The review-document PR
does not amend PR #816's implementation, change #272, or authorize a merge.
