"""Host checks for the Issue #234 CD_DINFO ($0B, $E021) caller-contract ledger.

These tests check the ledger's rules and pin every row's side, status and value to a snapshot, so any
change to those fields fails regardless of its citation. They run no emulator, read no ROM and quote no
BIOS bytes. Passing them is not evidence of System Card behaviour.
"""
import re
import unittest
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / 'docs' / 'research' / 'cd-dinfo-contract.md'

FIELDS = ['id', 'side', 'item', 'status', 'value', 'source', 'reason']
SIDES = {'caller', 'callee', 'reference', 'name'}
STATUSES = {'SOURCE_FACT', 'HYPOTHESIS', 'UNKNOWN', 'BLOCKED_ABI'}
FORBIDDEN_WORDS = ('VERIFIED', 'OBSERVED', 'PASS', 'DEV_CALLABLE')
# SOURCE_FACT must cite at least one primary source. S4 (no licence shown), S5 (name only) and S6 (repository
# inventory) are not a basis for any fact.
PRIMARY_SOURCES = {'S1', 'S2', 'S3'}

# Pinned snapshot of (side, status, value) for every ledger row. Changing any of them fails the ledger
# test until this snapshot is updated in the same change.
EXPECTED = {
    'F01': ('name', 'SOURCE_FACT', '`$E021`'),
    'F02': ('caller', 'UNKNOWN', 'UNKNOWN'),
    'F03': ('caller', 'SOURCE_FACT', '`0`'),
    'F04': ('caller', 'SOURCE_FACT', '`1`'),
    'F05': ('caller', 'SOURCE_FACT', '`2`'),
    'F06': ('caller', 'SOURCE_FACT', '`$20F8`（_al）、`$20F9`（_ah）'),
    'F07': ('callee', 'HYPOTHESIS', '下位 2 ビットのみ使用'),
    'F08': ('caller', 'SOURCE_FACT', 'BCD（種別 0 の結果 `tnomin` を渡す）'),
    'F09': ('callee', 'HYPOTHESIS', 'BCD'),
    'F10': ('callee', 'UNKNOWN', 'UNKNOWN'),
    'F11': ('caller', 'UNKNOWN', 'UNKNOWN'),
    'F12': ('callee', 'UNKNOWN', 'UNKNOWN'),
    'F13': ('callee', 'HYPOTHESIS', '+0 は最小トラック（BCD）、+1 は最大トラック（BCD）、+2・+3 は 0'),
    'F14': ('callee', 'HYPOTHESIS', '+0 分、+1 秒、+2 フレーム（いずれも BCD）、+3 は 0'),
    'F15': ('callee', 'HYPOTHESIS', '+0〜+2 はトラック開始の MSF（BCD）、+3 は SubQ フラグ'),
    'F16': ('caller', 'SOURCE_FACT', 'ビット `$04` が立つとデータとみなす'),
    'F17': ('caller', 'SOURCE_FACT', 'Z フラグ（`bne` で分岐）'),
    'F18': ('callee', 'HYPOTHESIS', '成功は $00、失敗は非 0'),
    'F19': ('callee', 'UNKNOWN', 'UNKNOWN'),
    'F20': ('callee', 'UNKNOWN', 'UNKNOWN'),
    'F21': ('caller', 'SOURCE_FACT', '種別 0・1 は非 0 で中断、種別 2 は存在しないトラックを次へ進める'),
    'F22': ('callee', 'UNKNOWN', 'UNKNOWN'),
    'F23': ('callee', 'UNKNOWN', 'UNKNOWN'),
    'F24': ('callee', 'UNKNOWN', 'UNKNOWN'),
    'F25': ('caller', 'UNKNOWN', 'UNKNOWN'),
    'F26': ('callee', 'UNKNOWN', 'UNKNOWN'),
    'F27': ('callee', 'UNKNOWN', 'UNKNOWN'),
    'F28': ('callee', 'UNKNOWN', 'UNKNOWN'),
    'F29': ('name', 'SOURCE_FACT', '`cd_dinfo`（HuC）、`CD_DINFO`（当リポジトリ）、`cdr_dinfo`（elmer の内部名）'),
    'F30': ('reference', 'UNKNOWN', 'UNKNOWN'),
}

# Status counts as the rows stand. Enforced exactly by test_status_counts.
EXPECTED_COUNTS = {'SOURCE_FACT': 10, 'HYPOTHESIS': 6, 'UNKNOWN': 14}

# Field coverage required by the Issue #234 acceptance list. Each keyword must appear in the item cell of
# the named row (token match, not a substring of the whole table).
REQUIRED_FIELD_ROWS = {
    'A': 'F19', 'X': 'F22', 'Y': 'F18', 'P': 'F23', '$20F8': 'F10', 'フラグ': 'F23', 'SP': 'F24',
    'MPR': 'F25', '副作用': 'F26', 'IRQ': 'F27', '失敗': 'F21', 'エラー': 'F20', '戻り': 'F18',
}


def token_in(kw, text):
    """True if kw occurs as a whole token: not adjacent to an ASCII letter (so 'A' does not match 'Abc')."""
    return re.search(rf'(?<![A-Za-z]){re.escape(kw)}(?![A-Za-z])', text) is not None


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
        if status == 'SOURCE_FACT' and not (set(re.findall(r'S\d+', source)) & PRIMARY_SOURCES):
            out.append(f'{rid}: SOURCE_FACT cites no primary source {source!r}')
        if status == 'UNKNOWN' and value.strip('`') != 'UNKNOWN':
            out.append(f'{rid}: UNKNOWN row with a filled value {value!r}')
        if side == 'callee' and status not in {'HYPOTHESIS', 'UNKNOWN', 'BLOCKED_ABI'}:
            out.append(f'{rid}: callee row with status {status!r}')
    for cell in cells_text:
        for word in FORBIDDEN_WORDS:
            if re.search(rf'\b{word}\b', cell):
                out.append(f'forbidden word {word!r} in table cell {cell[:40]!r}')
    return out


def pin_problems(rows):
    """Return a list of differences from the pinned snapshot. Any side, status or value change fails."""
    out = []
    seen = set()
    for r in rows:
        rid = r['id']
        if rid in seen:
            out.append(f'{rid}: duplicate row')
            continue
        seen.add(rid)
        if rid not in EXPECTED:
            out.append(f'{rid}: row not in the pinned snapshot')
            continue
        side, status, value = EXPECTED[rid]
        if r['side'] != side:
            out.append(f"{rid}: side {r['side']!r} differs from pinned {side!r}")
        if r['status'] != status:
            out.append(f"{rid}: status {r['status']!r} differs from pinned {status!r}")
        if r['value'] != value:
            out.append(f"{rid}: value {r['value']!r} differs from pinned {value!r}")
    for rid in EXPECTED:
        if rid not in seen:
            out.append(f'{rid}: pinned row missing')
    return out


def ledger_problems(rows, cells_text=()):
    """Rule violations plus pinned-snapshot differences. An empty list means the ledger is accepted."""
    return problems(rows, cells_text) + pin_problems(rows)


class CdDinfoLedgerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = DOC.read_text(encoding='utf-8')
        cls.rows = parse_rows(cls.text)

    def test_ledger_obeys_rules_and_pins(self):
        self.assertEqual(ledger_problems(self.rows, table_cells(self.text)), [])

    def test_status_counts(self):
        self.assertEqual(Counter(r['status'] for r in self.rows), Counter(EXPECTED_COUNTS))

    def test_required_fields_are_covered(self):
        by_id = {r['id']: r for r in self.rows}
        for kw, rid in REQUIRED_FIELD_ROWS.items():
            self.assertIn(rid, by_id, f'row {rid} missing')
            self.assertTrue(token_in(kw, by_id[rid]['item']), f'{rid} item must cover {kw!r}')

    def test_blocked_and_release_status_stated(self):
        self.assertIn('BLOCKED_ABI', self.text)
        self.assertIn('release_eligible=false', self.text)
        self.assertIn('DEV_CALLABLE にしない', self.text)

    def test_unknown_values_are_not_filled(self):
        unknown = [r for r in self.rows if r['status'] == 'UNKNOWN']
        self.assertTrue(unknown)
        for r in unknown:
            self.assertEqual(r['value'], 'UNKNOWN', r['id'])


class LedgerMutationTest(unittest.TestCase):
    """Promotions and citation swaps applied to the real ledger rows must be rejected."""

    def _mutate(self, rid, **kw):
        rows = parse_rows(DOC.read_text(encoding='utf-8'))
        for r in rows:
            if r['id'] == rid:
                r.update(kw)
        return rows

    def test_callee_unknown_promoted_to_source_fact_is_rejected(self):
        rows = self._mutate('F26', status='SOURCE_FACT', value='副作用なし', source='S3 1582–1600')
        self.assertTrue(any(p.startswith('F26:') for p in problems(rows)))
        self.assertTrue(ledger_problems(rows))

    def test_callee_hypothesis_promoted_to_source_fact_is_rejected(self):
        rows = self._mutate('F18', status='SOURCE_FACT', value='成功は $00', source='S3 1579')
        self.assertTrue(any(p.startswith('F18:') for p in problems(rows)))

    def test_caller_fact_cited_only_to_unlicensed_source_is_rejected(self):
        # S4 has no licence shown, so it cannot ground a SOURCE_FACT even on the caller side.
        rows = self._mutate('F03', source='S4 218–226')
        self.assertTrue(any(p.startswith('F03:') for p in problems(rows)))

    def test_name_only_source_is_rejected(self):
        rows = self._mutate('F01', source='S5')
        self.assertTrue(any(p.startswith('F01:') for p in problems(rows)))

    def test_pinned_value_change_is_rejected(self):
        rows = self._mutate('F13', value='+0 は最小トラック（2 進）')
        self.assertTrue(any(p.startswith('F13:') for p in pin_problems(rows)))


class RuleCheckerNegativeTest(unittest.TestCase):
    """The checker itself must reject promotion; these synthetic rows are not ledger data."""

    def _row(self, **kw):
        base = dict(id='F90', side='caller', item='x', status='SOURCE_FACT', value='v',
                    source='S2 1', reason='r')
        base.update(kw)
        return base

    def test_rejects_callee_source_fact(self):
        self.assertTrue(problems([self._row(side='callee')]))

    def test_rejects_source_fact_without_source(self):
        self.assertTrue(problems([self._row(source='—')]))

    def test_rejects_filled_unknown(self):
        self.assertTrue(problems([self._row(status='UNKNOWN', value='0')]))

    def test_rejects_source_fact_from_unlicensed_or_name_only(self):
        self.assertTrue(problems([self._row(source='S4 218–226')]))
        self.assertTrue(problems([self._row(source='S5, S6')]))

    def test_accepts_source_fact_with_primary_source(self):
        self.assertEqual(problems([self._row(side='name', source='S1 479、S6')]), [])

    def test_rejects_forbidden_status_word(self):
        self.assertTrue(problems([], ['VERIFIED']))
        self.assertTrue(problems([], ['PASS']))
        self.assertTrue(problems([], ['OBSERVED']))

    def test_accepts_honest_callee_unknown(self):
        self.assertEqual(problems([self._row(side='callee', status='UNKNOWN', value='UNKNOWN',
                                             source='—')]), [])


if __name__ == '__main__':
    unittest.main()
