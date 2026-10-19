"""Host tests for the Issue #239 CD_EXEC (slot $05, candidate $E00F) contract ledger.

Bookkeeping only: the tables in docs/cd-exec-contract-ledger.md, the slot 5 record in
spec/api_contracts.json, and the absence of CD_EXEC / $E00F / 57359 from src/ and the Makefile.
Nothing is executed and ./rom/ is never read. A callee row may never be SOURCE_FACT, OBSERVED,
VERIFIED or PASS.
"""
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LEDGER = ROOT / 'docs' / 'cd-exec-contract-ledger.md'
CONTRACTS = ROOT / 'spec' / 'api_contracts.json'

LEDGER_HEADER = ('ID', '区分', '項目', '確度', '出典', '決着条件')
# Other tables in the ledger: header -> column count. Any other table line is a violation.
KNOWN_TABLES = {
    ('ID', '出典（固定版・パス・行）', 'ライセンス表示', '状態'): 4,
    ('コマンド', '結果'): 2,
}
LEDGER_ROW_ID = re.compile(r'^[A-Z]\d+$')
ALLOWED = {'SOURCE_FACT', 'HYPOTHESIS', 'UNKNOWN', 'BLOCKED'}
FORBIDDEN = {'OBSERVED', 'VERIFIED', 'PASS'}
FORBIDDEN_TOKEN = re.compile(r'\b(?:OBSERVED|VERIFIED|PASS)\b')
# Section 2 is the only prose allowed to name the forbidden words, and it says they are not used.
RULE_LINE_MARK = '使わない'
SCOPES = {'caller', 'callee', 'repo'}
CALLEE_ALLOWED = {'HYPOTHESIS', 'UNKNOWN', 'BLOCKED'}
REQUIRED_IDS = {'A1', 'A2', 'A3', 'B1', 'B2', 'C1', 'C2', 'C3', 'C4', 'D1', 'D2', 'E1', 'F1'}
# CD_EXEC aliases: symbol, $E00F / 0xE00F, and decimal 57359 (= $E00F).
UNWIRED = re.compile(r'E00F|CD_EXEC|\b57359\b', re.IGNORECASE)


def split_cells(line):
    s = line.strip()
    if s.endswith('|'):
        s = s[:-1]
    return tuple(c.strip() for c in s[1:].split('|'))


def table_blocks(text):
    """Contiguous runs of lines starting with '|', as cell tuples, separator rows removed."""
    blocks, cur = [], []
    for line in text.splitlines() + ['']:
        if line.lstrip().startswith('|'):
            cur.append(split_cells(line))
        elif cur:
            blocks.append([c for c in cur if not all(re.fullmatch(r':?-+:?', x) for x in c)])
            cur = []
    return blocks


def check_rows(rows):
    """Pure rule check, so negative cases can be fed synthetic rows. Returns violations."""
    problems = [] if rows else ['no ledger rows']
    ids = [row['id'] for row in rows]
    problems += [f'duplicate id {rid}' for rid in {i for i in ids if ids.count(i) > 1}]
    for row in rows:
        status = row['status']
        if not LEDGER_ROW_ID.match(row['id']):
            problems.append(f"bad id {row['id']!r}")
        if row['scope'] not in SCOPES:
            problems.append(f"{row['id']}: bad scope {row['scope']!r}")
        if status in FORBIDDEN:
            problems.append(f"{row['id']}: forbidden status {status}")
        elif status not in ALLOWED:
            problems.append(f"{row['id']}: unknown status {status}")
        if row['scope'] == 'callee' and status not in CALLEE_ALLOWED:
            problems.append(f"{row['id']}: callee row cannot be {status}")
    return problems


def ledger_rows(text):
    return [{'id': c[0], 'scope': c[1], 'status': c[3], 'next': c[5]}
            for block in table_blocks(text) if block and block[0] == LEDGER_HEADER for c in block[1:]
            if len(c) == 6]


def check_text(text):
    """Whole-document check: table layout, row rules, and forbidden words in any prose line."""
    problems, rows = [], []
    for block in table_blocks(text):
        header, body = block[0], block[1:]
        if header == LEDGER_HEADER:
            for cells in body:
                if len(cells) != 6:
                    problems.append(f'malformed ledger line ({len(cells)} cells): {cells[:2]}')
                else:
                    rows.append({'id': cells[0], 'scope': cells[1], 'status': cells[3], 'next': cells[5]})
        elif header in KNOWN_TABLES:
            problems += [f'malformed table line {cells[:2]}' for cells in body
                         if len(cells) != KNOWN_TABLES[header]]
        else:
            problems.append(f'unknown table header {header[:2]}')
    problems += prose_violations(text)
    return problems + check_rows(rows)


def prose_violations(text):
    return [f'forbidden word in prose: {line[:40]}' for line in text.splitlines()
            if FORBIDDEN_TOKEN.search(line) and RULE_LINE_MARK not in line]


def unwired_hits():
    paths = [p for p in (ROOT / 'src').rglob('*') if p.is_file()] + [ROOT / 'Makefile']
    return [str(p.relative_to(ROOT)) for p in paths
            if p.exists() and UNWIRED.search(p.read_text(encoding='utf-8', errors='ignore'))]


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.text = LEDGER.read_text(encoding='utf-8')
        self.rows = ledger_rows(self.text)

    def test_document_is_well_formed_and_promotes_nothing(self):
        self.assertEqual(check_text(self.text), [])
        self.assertTrue(self.rows)

    def test_required_criteria_rows_exist_with_expected_status(self):
        by_id = {row['id']: row for row in self.rows}
        self.assertTrue(REQUIRED_IDS <= set(by_id))
        self.assertEqual(by_id['B1']['status'], 'HYPOTHESIS')
        self.assertEqual(by_id['B2']['status'], 'UNKNOWN')
        self.assertEqual(by_id['E1']['status'], 'BLOCKED')

    def test_no_forbidden_word_in_status_or_next_cells_or_prose(self):
        for row in self.rows:
            for word in FORBIDDEN:
                self.assertNotIn(word, row['status'] + row['next'], row['id'])
        self.assertEqual(prose_violations(self.text), [])

    def test_no_bios_execution_or_rom_wiring_is_claimed(self):
        self.assertIn('ROM 配線なし', self.text)
        self.assertIn('危険な純正 JSR は 0 回', self.text)


class RegressionTests(unittest.TestCase):
    """Synthetic rows injected into the real ledger table must be rejected by check_text."""

    def inject_after_f1(self, line):
        text = LEDGER.read_text(encoding='utf-8')
        injected, n = re.subn(r'^(\| F1 \|.*)$', lambda m: m.group(1) + '\n' + line, text, flags=re.M)
        self.assertEqual(n, 1)
        return injected

    def test_g1_observed_row_is_rejected(self):
        self.assertTrue(check_text(self.inject_after_f1('| G1 | callee | synthetic | OBSERVED | s | n |')))

    def test_compact_b2_pass_row_is_rejected(self):
        self.assertTrue(check_text(self.inject_after_f1('|B2|callee|synthetic|PASS|s|n|')))

    def test_forbidden_word_in_prose_is_rejected(self):
        self.assertTrue(check_text(self.inject_after_f1('') + '\nCD_EXEC は OBSERVED 済み。\n'))

    def test_malformed_ledger_line_is_rejected(self):
        self.assertTrue(check_text(self.inject_after_f1('| A9 | caller | extra |')))

    def test_unwired_aliases_are_detected(self):
        for sample in ('E00F', '0xE00F', '$e00f', 'CD_EXEC', 'cd_exec', '57359'):
            with self.subTest(sample=sample):
                self.assertIsNotNone(UNWIRED.search(sample))


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
    def test_no_cd_exec_e00f_or_57359_reference_in_src_or_makefile(self):
        self.assertEqual(unwired_hits(), [])


if __name__ == '__main__':
    unittest.main()
