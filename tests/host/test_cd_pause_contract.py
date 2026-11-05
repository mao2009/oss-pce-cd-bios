"""Host checks for the CD_PAUSE caller-ABI evidence ledger (Issue #254).

The rule functions below check rows that a ledger would contain, using synthetic input only (the ledger
document is withdrawn). They fail when a status word that implies execution evidence
(VERIFIED, OBSERVED, PASS, DEV_CALLABLE) appears in a status cell, when a SOURCE_FACT row has
no source ID, or when a synthetic row changes from its baseline without a review. The release-blocking
state of the pause slot is checked on the real gate in test_public_api_ledger.py. They do not run any callee.
"""
import re
import unittest

ALLOWED = {'SOURCE_FACT', 'HYPOTHESIS', 'UNKNOWN'}
FORBIDDEN = {'VERIFIED', 'OBSERVED', 'PASS', 'DEV_CALLABLE', 'BLOCKED'}

# Synthetic baseline for the change check. It is not a research record: it only proves that a row
# whose baseline is UNKNOWN cannot be relabelled as a sourced fact without the baseline being updated.
SYNTHETIC_BASELINE = {
    'R1': ('UNKNOWN', '-'),
    'R2': ('HYPOTHESIS', 'SYNTH-A'),
}


def violations(rows):
    """Promotion check on synthetic or real rows; returns a list of human-readable problems."""
    problems = []
    for rid, _topic, status, source in rows:
        tokens = set(re.findall(r'[A-Z_]{4,}', re.sub(r'（[^）]*）', '', status)))
        if not tokens:
            problems.append(f'{rid}: no status token')
        for bad in tokens & FORBIDDEN:
            problems.append(f'{rid}: forbidden status word {bad}')
        for other in tokens - ALLOWED - FORBIDDEN:
            problems.append(f'{rid}: unknown status token {other}')
        if 'SOURCE_FACT' in tokens and not re.search(r'S\d+', source):
            problems.append(f'{rid}: SOURCE_FACT without a source reference')
    return problems


def baseline_violations(rows, baseline):
    """Compare every row's (status, source) with its baseline; any difference is a problem."""
    problems = []
    seen = {}
    for rid, _topic, status, source in rows:
        seen[rid] = (status, source)
        if rid not in baseline:
            problems.append(f'{rid}: not in baseline')
        elif (status, source) != baseline[rid]:
            problems.append(f'{rid}: changed from {baseline[rid]} to {(status, source)}')
    for rid in baseline:
        if rid not in seen:
            problems.append(f'{rid}: missing from ledger')
    return problems


def mutate(rows, rid, status, source):
    """Return a copy of rows with one row's status and source replaced."""
    return [(r, t, status, source) if r == rid else (r, t, s, src) for r, t, s, src in rows]


class PromotionCheckNegativeTests(unittest.TestCase):
    """Synthetic rows prove that the check rejects promotion of UNKNOWN or HYPOTHESIS rows."""

    def test_verified_status_is_rejected(self):
        self.assertTrue(violations([('F90', 'x', 'VERIFIED', 'SYNTH-A')]))

    def test_observed_or_pass_is_rejected(self):
        self.assertTrue(violations([('F91', 'x', 'OBSERVED', 'SYNTH-A')]))
        self.assertTrue(violations([('F92', 'x', 'PASS', 'SYNTH-A')]))

    def test_dev_callable_is_rejected(self):
        self.assertTrue(violations([('F93', 'x', 'DEV_CALLABLE', 'SYNTH-A')]))

    def test_blocked_as_a_status_is_rejected(self):
        self.assertTrue(violations([('F97', 'x', 'BLOCKED', 'SYNTH-A')]))

    def test_source_fact_without_source_is_rejected(self):
        self.assertTrue(violations([('F94', 'x', 'SOURCE_FACT', '-')]))

    def test_unknown_and_hypothesis_are_accepted(self):
        self.assertEqual(violations([('F95', 'x', 'UNKNOWN', '-'),
                                     ('F96', 'x', 'HYPOTHESIS', 'SYNTH-A')]), [])


class BaselineChangeNegativeTests(unittest.TestCase):
    """Relabelling a baseline row as SOURCE_FACT with a citation must fail the baseline check."""

    def setUp(self):
        self.rows = [('R1', 'synthetic', 'UNKNOWN', '-'), ('R2', 'synthetic', 'HYPOTHESIS', 'SYNTH-A')]

    def test_the_unchanged_baseline_passes(self):
        self.assertEqual(baseline_violations(self.rows, SYNTHETIC_BASELINE), [])

    def test_unknown_row_relabelled_source_fact_with_source_is_rejected(self):
        mutated = mutate(self.rows, 'R1', 'SOURCE_FACT', 'SYNTH-B')
        self.assertTrue(baseline_violations(mutated, SYNTHETIC_BASELINE))

    def test_hypothesis_row_relabelled_source_fact_with_source_is_rejected(self):
        mutated = mutate(self.rows, 'R2', 'SOURCE_FACT', 'SYNTH-A')
        self.assertTrue(baseline_violations(mutated, SYNTHETIC_BASELINE))

    def test_a_row_missing_from_the_ledger_is_rejected(self):
        self.assertTrue(baseline_violations(self.rows[:1], SYNTHETIC_BASELINE))


if __name__ == '__main__':
    unittest.main()
