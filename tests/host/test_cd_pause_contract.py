"""Host checks for the CD_PAUSE caller-ABI evidence ledger (Issue #254).

The ledger docs/research/cd-pause-contract.md must classify every field as SOURCE_FACT,
HYPOTHESIS or UNKNOWN. These tests fail when a status word that implies execution evidence
(VERIFIED, OBSERVED, PASS, DEV_CALLABLE) appears in a status cell, when a SOURCE_FACT row has
no source ID, when the release-blocking state is weakened, or when any row's status or source
cell differs from the pinned table PINNED below. They do not run any callee.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LEDGER = ROOT / 'docs' / 'research' / 'cd-pause-contract.md'

ALLOWED = {'SOURCE_FACT', 'HYPOTHESIS', 'UNKNOWN'}
FORBIDDEN = {'VERIFIED', 'OBSERVED', 'PASS', 'DEV_CALLABLE', 'BLOCKED'}
REQUIRED_TOPICS = ['入力 A', '入力 X', '入力 Y', '$20F8', '戻り A', '戻り N/Z', 'SP', 'MPR7', 'IRQ', '副作用', '失敗']

# Pinned (status, source) cells per field row. Any change to a row, including relabelling
# UNKNOWN or HYPOTHESIS as SOURCE_FACT with a citation, must update this table in review.
PINNED = {
    'F01': ('SOURCE_FACT', 'S2 109–112、S3 90'),
    'F02': ('SOURCE_FACT', 'S4 502–514'),
    'F03': ('UNKNOWN', '-'),
    'F04': ('SOURCE_FACT', 'S4 500–516'),
    'F05': ('UNKNOWN', '-'),
    'F06': ('SOURCE_FACT', 'S11 41–44、S2 110、S4 500'),
    'F07': ('UNKNOWN', '-'),
    'F08': ('SOURCE_FACT', 'S4 509–514'),
    'F09': ('SOURCE_FACT', 'S1 330–343、S2 109–112'),
    'F10': ('UNKNOWN', '-'),
    'F11': ('SOURCE_FACT', 'S3 90、S4 511–514'),
    'F12': ('UNKNOWN', 'S2'),
    'F13': ('SOURCE_FACT（経路）／UNKNOWN（意味）', 'S4'),
    'F14': ('SOURCE_FACT', 'S2 111'),
    'F15': ('SOURCE_FACT', 'S2 111'),
    'F16': ('SOURCE_FACT（経路）／UNKNOWN（BIOS の値）', 'S4、S2'),
    'F17': ('SOURCE_FACT（経路）／UNKNOWN（BIOS 内部）', 'S4'),
    'F18': ('SOURCE_FACT', 'S4 502–514'),
    'F19': ('HYPOTHESIS', 'S8（本文）'),
    'F20': ('UNKNOWN', '-'),
    'F21': ('SOURCE_FACT（経路）／UNKNOWN（BIOS）', 'S2、S4'),
    'F22': ('HYPOTHESIS', 'S6、S7'),
    'F23': ('SOURCE_FACT（経路）／UNKNOWN（BIOS）', 'S5、S12'),
    'F24': ('UNKNOWN', 'S9'),
    'F25': ('HYPOTHESIS', 'S1 476、S8'),
    'F26': ('HYPOTHESIS', 'S8'),
    'F27': ('UNKNOWN', '-'),
    'F28': ('HYPOTHESIS', 'S10'),
}


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


def pin_violations(rows):
    """Compare every row's (status, source) with PINNED; any difference is a problem."""
    problems = []
    seen = {}
    for rid, _topic, status, source in rows:
        seen[rid] = (status, source)
        if rid not in PINNED:
            problems.append(f'{rid}: not in pinned table')
        elif (status, source) != PINNED[rid]:
            problems.append(f'{rid}: changed from {PINNED[rid]} to {(status, source)}')
    for rid in PINNED:
        if rid not in seen:
            problems.append(f'{rid}: missing from ledger')
    return problems


def mutate(rows, rid, status, source):
    """Return a copy of rows with one row's status and source replaced."""
    return [(r, t, status, source) if r == rid else (r, t, s, src) for r, t, s, src in rows]


class CdPauseLedgerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = LEDGER.read_text(encoding='utf-8')
        cls.rows = field_rows(cls.text)

    def test_ledger_has_fields(self):
        self.assertGreaterEqual(len(self.rows), 20)

    def test_no_forbidden_status_or_unsourced_fact(self):
        self.assertEqual(violations(self.rows), [])

    def test_status_and_source_match_pinned_table(self):
        self.assertEqual(pin_violations(self.rows), [])

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


class PinnedPromotionMutationTests(unittest.TestCase):
    """Relabelling a real row as SOURCE_FACT with a citation must fail the pinned check."""

    def setUp(self):
        self.rows = field_rows(LEDGER.read_text(encoding='utf-8'))

    def test_unknown_row_relabelled_source_fact_with_source_is_rejected(self):
        mutated = mutate(self.rows, 'F03', 'SOURCE_FACT', 'S4 502–514')
        self.assertTrue(pin_violations(mutated))

    def test_hypothesis_row_relabelled_source_fact_with_source_is_rejected(self):
        mutated = mutate(self.rows, 'F19', 'SOURCE_FACT', 'S8')
        self.assertTrue(pin_violations(mutated))


if __name__ == '__main__':
    unittest.main()
