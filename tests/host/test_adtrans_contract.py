"""Host tests for the Issue #297 AD_TRANS ($11, $E033) caller-ABI evidence ledger.

These tests check the ledger's rules only: the exact row set, the status vocabulary, no
VERIFIED/OBSERVED/PASS/DEV_CALLABLE wording in the table, callee rows never promoted from caller
source, UNKNOWN/BLOCKED values kept as UNKNOWN, and cited source ids. No emulator, ROM or System
Card code is involved. Passing these tests is NOT evidence of BIOS behaviour.
"""
import re
import unittest
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / 'docs' / 'research' / 'ad-trans-contract.md'

EXPECTED_SIDES = {
    **{f'K{i}': 'K' for i in range(1, 15)},
    **{f'C{i}': 'C' for i in range(1, 17)},
    **{f'R{i}': 'R' for i in range(1, 6)},
}
FIELDS = ['id', 'side', 'item', 'status', 'value', 'source', 'reason', 'test']
STATUSES = {'SOURCE_FACT', 'HYPOTHESIS', 'UNKNOWN', 'BLOCKED'}
CALLEE_STATUSES = {'HYPOTHESIS', 'UNKNOWN', 'BLOCKED'}
NEEDS_SOURCE = {'SOURCE_FACT', 'HYPOTHESIS'}
FORBIDDEN = ('VERIFIED', 'OBSERVED', 'PASS', 'DEV_CALLABLE')
SOURCE_ID_RE = re.compile(r'\bS\d+\b')
SECTION3_RE = re.compile(r'^## 3\..*?$(.*?)(?=^## |\Z)', re.M | re.S)


def parse_ledger(text):
    """Return (item rows, defined source ids, malformed §3 lines)."""
    defined = set()
    for line in text.splitlines():
        s = line.strip()
        if s.startswith('|'):
            first = s.strip('|').split('|')[0].strip()
            if re.fullmatch(r'S\d+', first):
                defined.add(first)
    m = SECTION3_RE.search(text)
    items, malformed = [], []
    for line in (m.group(1) if m else '').splitlines():
        s = line.strip()
        if not s:
            continue
        if not s.startswith('|') or not s.endswith('|'):
            malformed.append(s)
            continue
        cells = [c.strip() for c in s.strip('|').split('|')]
        if cells[0] == 'ID' or set(s) <= set('|-: '):
            continue
        if len(cells) == len(FIELDS):
            items.append(dict(zip(FIELDS, cells)))
        else:
            malformed.append(s)
    return items, defined, malformed


def ledger_problems(items, defined, malformed=()):
    """Return rule violations; an empty list means the ledger passes every rule here."""
    problems = [f'malformed row: {m[:40]}' for m in malformed]
    counts = Counter(r['id'] for r in items)
    problems += [f'duplicate {i} x{n}' for i, n in counts.items() if n > 1]
    problems += [f'missing {i}' for i in EXPECTED_SIDES if i not in counts]
    problems += [f'unexpected {i}' for i in counts if i not in EXPECTED_SIDES]
    for r in items:
        rid, side, status, value = r['id'], r['side'], r['status'], r['value']
        if rid in EXPECTED_SIDES and side != EXPECTED_SIDES[rid]:
            problems.append(f'side {rid}={side}')
        if status not in STATUSES:
            problems.append(f'status {rid}={status}')
            continue
        if side in ('C', 'R') and status not in CALLEE_STATUSES:
            problems.append(f'{side} row {rid} promoted to {status}')
        if status in ('UNKNOWN', 'BLOCKED') and value != 'UNKNOWN':
            problems.append(f'{rid} value {value!r} must be UNKNOWN')
        if status in NEEDS_SOURCE:
            cited = set(SOURCE_ID_RE.findall(r['source']))
            if not cited:
                problems.append(f'{rid} cites no source')
            problems += [f'{rid} cites undefined {s}' for s in cited - defined]
        for word in FORBIDDEN:
            if any(word in cell for cell in (r['status'], r['value'], r['source'], r['reason'], r['test'])):
                problems.append(f'{rid} contains forbidden word {word}')
    return problems


TEXT = DOC.read_text(encoding='utf-8')


class LedgerRuleTests(unittest.TestCase):
    def test_document_keeps_release_flag_false(self):
        self.assertIn('release_eligible=false', TEXT)

    def test_rows_are_exactly_the_expected_ids_and_no_rule_is_broken(self):
        items, defined, malformed = parse_ledger(TEXT)
        self.assertEqual(len(EXPECTED_SIDES), 35)
        self.assertEqual(ledger_problems(items, defined, malformed), [])
        self.assertEqual(len(items), 35)

    def test_c1_entry_is_hypothesis_and_names_e033(self):
        items, _, _ = parse_ledger(TEXT)
        c1 = [r for r in items if r['id'] == 'C1']
        self.assertEqual(c1[0]['status'], 'HYPOTHESIS')
        self.assertIn('E033', c1[0]['value'])

    def test_no_callee_row_is_source_fact(self):
        items, _, _ = parse_ledger(TEXT)
        self.assertFalse([r['id'] for r in items if r['side'] != 'K' and r['status'] == 'SOURCE_FACT'])


class LedgerMutationTests(unittest.TestCase):
    """Each mutated copy of the ledger must be rejected by the rules above."""

    def _problems_for(self, old, new):
        self.assertIn(old, TEXT, 'mutation target not found in ledger')
        return ledger_problems(*parse_ledger(TEXT.replace(old, new, 1)))

    def _row(self, rid):
        return next(line for line in TEXT.splitlines() if line.startswith(f'| {rid} |'))

    def test_promoting_callee_unknown_to_source_fact_fails(self):
        row = self._row('C4')
        new = row.replace('| UNKNOWN | UNKNOWN |', '| SOURCE_FACT | UNKNOWN |', 1)
        self.assertNotEqual(row, new)
        self.assertTrue(self._problems_for(row, new))

    def test_filling_an_unknown_value_fails(self):
        row = self._row('C6')
        new = row.replace('| UNKNOWN | UNKNOWN |', '| UNKNOWN | 2 |', 1)
        self.assertNotEqual(row, new)
        self.assertTrue(self._problems_for(row, new))

    def test_verified_wording_in_table_fails(self):
        row = self._row('C14')
        new = row.replace('戻りの時点で転送が完了している', 'VERIFIED: 戻りの時点で転送が完了している')
        self.assertNotEqual(row, new)
        self.assertTrue(self._problems_for(row, new))

    def test_removed_row_fails(self):
        row = self._row('R5')
        self.assertTrue(self._problems_for(row + '\n', ''))

    def test_hypothesis_without_source_fails(self):
        row = self._row('R2')
        new = row.replace('| S1、S3、S7 |', '| なし |', 1)
        self.assertNotEqual(row, new)
        self.assertTrue(self._problems_for(row, new))


if __name__ == '__main__':
    unittest.main()
