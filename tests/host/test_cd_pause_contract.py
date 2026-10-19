"""Host checks for the CD_PAUSE caller-ABI evidence ledger (Issue #254).

The ledger docs/research/cd-pause-contract.md must classify every field as SOURCE_FACT,
HYPOTHESIS or UNKNOWN. These tests fail when a status word that implies execution evidence
(VERIFIED, OBSERVED, PASS, DEV_CALLABLE) appears in a status cell, when a SOURCE_FACT row has
no source ID, or when the release-blocking state is weakened. They do not run any callee.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LEDGER = ROOT / 'docs' / 'research' / 'cd-pause-contract.md'

ALLOWED = {'SOURCE_FACT', 'HYPOTHESIS', 'UNKNOWN'}
FORBIDDEN = {'VERIFIED', 'OBSERVED', 'PASS', 'DEV_CALLABLE', 'BLOCKED'}
REQUIRED_TOPICS = ['入力 A', '入力 X', '入力 Y', '$20F8', '戻り A', '戻り N/Z', 'SP', 'MPR7', 'IRQ', '副作用', '失敗']


def field_rows(text):
    """Return (id, topic, status, source) for each table row whose first cell is F01..Fnn."""
    rows = []
    for line in text.splitlines():
        if not line.startswith('| F'):
            continue
        cells = [c.strip() for c in line.strip().strip('|').split('|')]
        if len(cells) != 6 or not re.fullmatch(r'F\d{2}', cells[0]):
            continue
        rows.append((cells[0], cells[1], cells[2], cells[4]))
    return rows


def violations(rows):
    """Promotion check on synthetic or real rows; returns a list of human-readable problems."""
    problems = []
    for rid, topic, status, source in rows:
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


class CdPauseLedgerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = LEDGER.read_text(encoding='utf-8')
        cls.rows = field_rows(cls.text)

    def test_ledger_has_fields(self):
        self.assertGreaterEqual(len(self.rows), 20)

    def test_no_forbidden_status_or_unsourced_fact(self):
        self.assertEqual(violations(self.rows), [])

    def test_required_topics_are_covered(self):
        topics = ' '.join(topic for _, topic, _, _ in self.rows)
        for needle in REQUIRED_TOPICS:
            self.assertIn(needle, topics, needle)

    def test_release_stays_blocked(self):
        self.assertIn('BLOCKED_ABI', self.text)
        self.assertIn('release_eligible=false', self.text)
        self.assertNotIn('release_eligible=true', self.text)

    def test_reference_callee_never_executed_claim(self):
        self.assertIn('System Card の CD_PAUSE は一度も実行していない', self.text)


class PromotionCheckNegativeTests(unittest.TestCase):
    """Synthetic rows prove that the check rejects promotion of UNKNOWN or HYPOTHESIS rows."""

    def test_verified_status_is_rejected(self):
        self.assertTrue(violations([('F90', 'x', 'VERIFIED', 'S1 476')]))

    def test_observed_or_pass_is_rejected(self):
        self.assertTrue(violations([('F91', 'x', 'OBSERVED', 'S1 476')]))
        self.assertTrue(violations([('F92', 'x', 'PASS', 'S1 476')]))

    def test_dev_callable_is_rejected(self):
        self.assertTrue(violations([('F93', 'x', 'DEV_CALLABLE', 'S1 476')]))

    def test_source_fact_without_source_is_rejected(self):
        self.assertTrue(violations([('F94', 'x', 'SOURCE_FACT', '-')]))

    def test_unknown_and_hypothesis_are_accepted(self):
        self.assertEqual(violations([('F95', 'x', 'UNKNOWN', '-'),
                                     ('F96', 'x', 'HYPOTHESIS', 'S8')]), [])


if __name__ == '__main__':
    unittest.main()
