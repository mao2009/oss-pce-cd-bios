"""Host tests for Issue #271 (CD_SUBRQ / CD_SUBRD, $0D) research ledger.

Reads docs/research/cd-subrq-subrd-conflicts.md as text only. No emulator, ROM or BIOS
execution is involved. The pinned statuses guard against silent promotion of an UNKNOWN
or silent resolution of a name/return conflict; changing one requires changing this file
in review.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / 'docs' / 'research' / 'cd-subrq-subrd-conflicts.md'

ALLOWED = {'SOURCE_FACT', 'HYPOTHESIS', 'UNKNOWN'}
EXPECTED = {
    'C1': 'SOURCE_FACT', 'C2': 'SOURCE_FACT', 'C3': 'SOURCE_FACT', 'C4': 'UNKNOWN',
    'C5': 'UNKNOWN', 'C6': 'SOURCE_FACT', 'C7': 'SOURCE_FACT', 'C8': 'UNKNOWN',
    'C9': 'SOURCE_FACT', 'C10': 'UNKNOWN', 'C11': 'UNKNOWN', 'C12': 'UNKNOWN',
    'C13': 'UNKNOWN', 'C14': 'UNKNOWN', 'C15': 'SOURCE_FACT', 'C16': 'UNKNOWN',
    'C17': 'HYPOTHESIS',
}


def _claim_rows(text):
    rows = {}
    for line in text.splitlines():
        if not line.startswith('| C'):
            continue
        cells = [c.strip() for c in line.strip().strip('|').split('|')]
        if re.fullmatch(r'C\d+', cells[0]):
            rows[cells[0]] = cells
    return rows


class CdSubrqSubrdConflictLedgerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = DOC.read_text(encoding='utf-8')
        cls.rows = _claim_rows(cls.text)

    def test_claim_statuses_are_pinned_and_within_allowed_set(self):
        statuses = {cid: cells[3] for cid, cells in self.rows.items()}
        self.assertEqual(statuses, EXPECTED)
        self.assertTrue(set(statuses.values()) <= ALLOWED)

    def test_no_verified_or_observed_claim_in_table(self):
        for cid, cells in self.rows.items():
            self.assertNotIn(cells[3], {'VERIFIED', 'OBSERVED', 'PASS'}, cid)

    def test_name_conflict_is_kept_unresolved(self):
        self.assertIn('conflict unresolved', self.rows['C1'][4])
        self.assertIn('同一と断定しない', self.rows['C1'][4])

    def test_return_and_q_conflicts_are_kept_unresolved(self):
        self.assertIn('conflict unresolved', self.rows['C4'][4])
        self.assertIn('conflict unresolved', self.rows['C8'][4])

    def test_same_mechanism_hypothesis_is_not_adopted(self):
        self.assertIn('採用しない', self.rows['C17'][4])

    def test_blocked_and_release_gate_are_stated(self):
        self.assertIn('BLOCKED_ABI', self.text)
        self.assertIn('release_eligible=false', self.text)
        self.assertIn('DEV_CALLABLE', self.text)

    def test_no_reference_bios_execution_claimed(self):
        self.assertIn('一切していない', self.text)


if __name__ == '__main__':
    unittest.main()
