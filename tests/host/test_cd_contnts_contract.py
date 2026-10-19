"""Host checks for the Issue #264 CD_CONTNTS ($0C, $E024) caller-contract ledger.

These tests check the ledger's rules only: the status vocabulary, no VERIFIED/OBSERVED/PASS/
DEV_CALLABLE wording in table rows, callee rows never promoted to SOURCE_FACT, UNKNOWN values never
filled, and the required field coverage. They run no emulator, read no ROM and quote no BIOS bytes.
Passing them is not evidence of System Card behaviour.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / 'docs' / 'research' / 'cd-contnts-contract.md'

FIELDS = ['id', 'side', 'item', 'status', 'value', 'source', 'reason']
SIDES = {'caller', 'callee', 'reference', 'name'}
STATUSES = {'SOURCE_FACT', 'HYPOTHESIS', 'UNKNOWN', 'BLOCKED_ABI'}
FORBIDDEN_WORDS = ('VERIFIED', 'OBSERVED', 'PASS', 'DEV_CALLABLE')
# Field coverage required by the Issue #264 acceptance list (input, register, flags, stack, MPR, IRQ,
# side effects, failure). Each keyword must appear in some row's item cell.
REQUIRED_ITEM_KEYWORDS = ['A', 'X', 'Y', 'P', '$20F8', 'フラグ', 'SP', 'MPR', 'IRQ', '副作用', '失敗']


def parse_rows(text):
    """Return field rows (ID starting with F) as dicts. Raises ValueError on a malformed F row."""
    rows = []
    for line in text.splitlines():
        s = line.strip()
        if not re.match(r'^\|\s*F\d{2}\s*\|', s):
            continue
        cells = [c.strip() for c in s.strip('|').split('|')]
        if len(cells) != len(FIELDS):
            raise ValueError(f'malformed row: {cells[0]} has {len(cells)} cells')
        rows.append(dict(zip(FIELDS, cells)))
    return rows


def table_cells(text):
    """Every table cell in the file, for the forbidden-word scan."""
    out = []
    for line in text.splitlines():
        if line.strip().startswith('|'):
            out.extend(c.strip() for c in line.strip().strip('|').split('|'))
    return out


def problems(rows, cells_text=()):
    """Return a list of rule violations. An empty list means the ledger obeys the rules."""
    out = []
    for r in rows:
        rid, side, status, value, source = r['id'], r['side'], r['status'], r['value'], r['source']
        if side not in SIDES:
            out.append(f'{rid}: unknown side {side!r}')
        if status not in STATUSES:
            out.append(f'{rid}: unknown status {status!r}')
        if side == 'callee' and status == 'SOURCE_FACT':
            out.append(f'{rid}: callee fact promoted to SOURCE_FACT')
        if status == 'SOURCE_FACT' and (not source or source in {'—', '-'}):
            out.append(f'{rid}: SOURCE_FACT without a source')
        if status == 'UNKNOWN' and value.strip('`') != 'UNKNOWN':
            out.append(f'{rid}: UNKNOWN row with a filled value {value!r}')
        if side == 'callee' and status not in {'HYPOTHESIS', 'UNKNOWN', 'BLOCKED_ABI'}:
            out.append(f'{rid}: callee row with status {status!r}')
    for cell in cells_text:
        for word in FORBIDDEN_WORDS:
            if re.search(rf'\b{word}\b', cell):
                out.append(f'forbidden word {word!r} in table cell {cell[:40]!r}')
    return out


class CdContntsLedgerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = DOC.read_text(encoding='utf-8')
        cls.rows = parse_rows(cls.text)

    def test_ledger_obeys_rules(self):
        self.assertEqual(problems(self.rows, table_cells(self.text)), [])

    def test_required_fields_are_covered(self):
        items = ' '.join(r['item'] for r in self.rows)
        for kw in REQUIRED_ITEM_KEYWORDS:
            self.assertIn(kw, items, f'field keyword {kw!r} not covered')

    def test_blocked_and_release_status_stated(self):
        self.assertIn('BLOCKED_ABI', self.text)
        self.assertIn('release_eligible=false', self.text)
        self.assertIn('DEV_CALLABLE にしない', self.text)

    def test_unknown_values_are_not_filled(self):
        unknown = [r for r in self.rows if r['status'] == 'UNKNOWN']
        self.assertTrue(unknown)
        for r in unknown:
            self.assertEqual(r['value'], 'UNKNOWN', r['id'])


class RuleCheckerNegativeTest(unittest.TestCase):
    """The checker itself must reject promotion; these synthetic rows are not ledger data."""

    def _row(self, **kw):
        base = dict(id='F90', side='caller', item='x', status='SOURCE_FACT', value='v',
                    source='S1 1', reason='r')
        base.update(kw)
        return base

    def test_rejects_callee_source_fact(self):
        self.assertTrue(problems([self._row(side='callee')]))

    def test_rejects_source_fact_without_source(self):
        self.assertTrue(problems([self._row(source='—')]))

    def test_rejects_filled_unknown(self):
        self.assertTrue(problems([self._row(status='UNKNOWN', value='0')]))

    def test_rejects_forbidden_status_word(self):
        self.assertTrue(problems([], ['VERIFIED']))
        self.assertTrue(problems([], ['PASS']))

    def test_accepts_honest_callee_unknown(self):
        self.assertEqual(problems([self._row(side='callee', status='UNKNOWN', value='UNKNOWN',
                                             source='—')]), [])


if __name__ == '__main__':
    unittest.main()
