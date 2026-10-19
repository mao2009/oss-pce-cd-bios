"""Host tests for the Issue #239 CD_EXEC (slot $05, candidate $E00F) contract ledger.

Bookkeeping only: the ledger table in docs/cd-exec-contract-ledger.md, the slot 5 record in
spec/api_contracts.json, and the absence of $E00F from src/ and the Makefile. Nothing is
executed and ./rom/ is never read. A callee row may never be SOURCE_FACT, OBSERVED, VERIFIED or PASS.
"""
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LEDGER = ROOT / 'docs' / 'cd-exec-contract-ledger.md'
CONTRACTS = ROOT / 'spec' / 'api_contracts.json'

ROW = re.compile(r'^\| ([A-F]\d) \| (caller|callee|repo) \| (.+?) \| (\S+) \| (.+?) \| (.+?) \|$', re.M)
ALLOWED = {'SOURCE_FACT', 'HYPOTHESIS', 'UNKNOWN', 'BLOCKED'}
FORBIDDEN = {'OBSERVED', 'VERIFIED', 'PASS'}
CALLEE_ALLOWED = {'HYPOTHESIS', 'UNKNOWN', 'BLOCKED'}
REQUIRED_IDS = {'A1', 'A2', 'A3', 'B1', 'B2', 'C1', 'C2', 'C3', 'C4', 'D1', 'D2', 'E1', 'F1'}


def parse_rows(text):
    return [{'id': m[1], 'scope': m[2], 'status': m[4], 'next': m[6]} for m in ROW.finditer(text)]


def check_rows(rows):
    """Pure rule check, so negative cases can be fed synthetic rows. Returns violations."""
    problems = [] if rows else ['no ledger rows']
    ids = [row['id'] for row in rows]
    problems += [f'duplicate id {rid}' for rid in {i for i in ids if ids.count(i) > 1}]
    for row in rows:
        status = row['status']
        if status in FORBIDDEN:
            problems.append(f"{row['id']}: forbidden status {status}")
        elif status not in ALLOWED:
            problems.append(f"{row['id']}: unknown status {status}")
        if row['scope'] == 'callee' and status not in CALLEE_ALLOWED:
            problems.append(f"{row['id']}: callee row cannot be {status}")
    return problems


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.text = LEDGER.read_text(encoding='utf-8')
        self.rows = parse_rows(self.text)

    def test_rows_are_well_formed_and_promote_nothing(self):
        self.assertEqual(check_rows(self.rows), [])
        # A malformed row must not be silently dropped by the parser.
        self.assertEqual(len(re.findall(r'^\| [A-F]\d \|', self.text, re.M)), len(self.rows))

    def test_required_criteria_rows_exist_with_expected_status(self):
        by_id = {row['id']: row for row in self.rows}
        self.assertTrue(REQUIRED_IDS <= set(by_id))
        self.assertEqual(by_id['B1']['status'], 'HYPOTHESIS')
        self.assertEqual(by_id['B2']['status'], 'UNKNOWN')
        self.assertEqual(by_id['E1']['status'], 'BLOCKED')

    def test_no_forbidden_word_in_status_or_next_cells(self):
        for row in self.rows:
            for word in FORBIDDEN:
                self.assertNotIn(word, row['status'] + row['next'], row['id'])

    def test_no_bios_execution_or_rom_wiring_is_claimed(self):
        self.assertIn('ROM 配線なし', self.text)
        self.assertIn('危険な純正 JSR は 0 回', self.text)


class CheckerNegativeTests(unittest.TestCase):
    """The rule checker must reject promotions."""

    def row(self, **kw):
        base = {'id': 'Z1', 'scope': 'callee', 'status': 'HYPOTHESIS', 'next': 'n'}
        base.update(kw)
        return base

    def test_callee_promotions_are_rejected(self):
        for status in ('OBSERVED', 'VERIFIED', 'PASS', 'SOURCE_FACT'):
            with self.subTest(status=status):
                self.assertTrue(check_rows([self.row(status=status)]))

    def test_non_allowed_status_is_rejected_for_any_scope(self):
        self.assertTrue(check_rows([self.row(scope='caller', status='PASS')]))
        self.assertTrue(check_rows([self.row(scope='repo', status='NOT_RUN')]))

    def test_caller_source_fact_and_hypothesis_are_allowed(self):
        self.assertEqual(check_rows([self.row(scope='caller', status='SOURCE_FACT')]), [])
        self.assertEqual(check_rows([self.row(status='HYPOTHESIS')]), [])

    def test_duplicates_and_empty_ledger_are_rejected(self):
        self.assertTrue(check_rows([self.row(scope='caller', status='UNKNOWN')] * 2))
        self.assertTrue(check_rows([]))


class Slot5Tests(unittest.TestCase):
    def setUp(self):
        data = json.loads(CONTRACTS.read_text(encoding='utf-8'))
        slots = [api for api in data['apis'] if api['slot'] == 5]
        self.assertEqual(len(slots), 1)
        self.slot = slots[0]

    def test_identity_and_no_execution_or_implementation(self):
        self.assertEqual((self.slot['candidate_name'], self.slot['candidate_entry']),
                         ('CD_EXEC', '0xE00F'))
        self.assertEqual(self.slot['verification'],
                         {'status': 'NOT_RUN', 'executed_callee': False, 'evidence': []})
        self.assertEqual(self.slot['implementation_status'], 'NOT_STARTED')

    def test_claims_stay_unknown_or_hypothesis(self):
        for field, claim in self.slot['contract'].items():
            with self.subTest(field=field):
                self.assertIn(claim['status'], {'UNKNOWN', 'HYPOTHESIS'})
        self.assertEqual(self.slot['contract']['returns']['status'], 'UNKNOWN')


class SlotUnwiredTests(unittest.TestCase):
    def test_no_e00f_reference_in_src_or_makefile(self):
        paths = [p for p in (ROOT / 'src').rglob('*') if p.is_file()] + [ROOT / 'Makefile']
        hits = [str(p.relative_to(ROOT)) for p in paths
                if p.exists() and re.search(r'E00F', p.read_text(encoding='utf-8', errors='ignore'),
                                            re.IGNORECASE)]
        self.assertEqual(hits, [])


if __name__ == '__main__':
    unittest.main()
