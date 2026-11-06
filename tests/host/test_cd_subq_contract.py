"""Host checks for the Issue #259 CD_SUBQ caller ABI ledger (撤去済み文書).

Pure text checks: the ledger table is parsed and each row's status is validated. No ROM, no
emulator, no reference BIOS. A promotion (UNKNOWN/HYPOTHESIS -> SOURCE_FACT without a pinned
value, or any VERIFIED/OBSERVED/PASS/DEV_CALLABLE status) must fail these tests.
"""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))

import reference_jsr_gate as gate  # noqa: E402

ALLOWED_STATUS = {'SOURCE_FACT', 'HYPOTHESIS', 'UNKNOWN', 'BLOCKED'}
FORBIDDEN_STATUS = {'VERIFIED', 'OBSERVED', 'PASS', 'DEV_CALLABLE', 'CALLABLE'}
# \b does not split CJK-adjacent text (e.g. 'VERIFIED扱い'), so the boundary is an explicit
# ASCII alnum/underscore lookaround: any non-ASCII character counts as a boundary.
FORBIDDEN_WORD_RE = re.compile(r'(?<![A-Za-z0-9_])(VERIFIED|OBSERVED|PASS|DEV_CALLABLE|CALLABLE)(?![A-Za-z0-9_])')
# Only sources recorded as BSL-1.0 with an unambiguous origin may back a SOURCE_FACT row.
# S5 is excluded: its header says it is based on Hudson code whose rights are unconfirmed (PENDING).
BSL_SOURCES = {'S1', 'S2', 'S3'}
SOURCE_ID_RE = re.compile(r'[SXZ]\d+')
# The exact value each SOURCE_FACT row may carry. A SOURCE_FACT row with any other id or value
# is rejected, so promoting a HYPOTHESIS/UNKNOWN row cannot pass by merely citing a BSL source.
SOURCE_FACT_PINS = {
    'F1': '`cd_subq`（`$00:E01E`、FUNC）',
    'F3': '`$E01E`（宣言）',
    'F5': '`ldy #<cd_subq` の後 `jsr call_bios`。`call_bios` は MPR7 を System Card 側に切替え、呼出し後に復元する',
    'F12': '`call_bios` が呼出し後に呼出側の MPR7 を復元する',
}
# Section 4 naming conflict: F17 must keep the CD_SUBA form and say the conflict is unresolved.
CD_SUBA_VALUE = '`CD_SUBA` という名称（`docs/abi-contracts.md` の記録）'
ROW_RE = re.compile(r'^\|\s*(F\d+)\s*\|([^|]*)\|([^|]*)\|([^|]*)\|([^|]*)\|([^|]*)\|\s*$')


def parse_rows(text):
    rows = []
    for line in text.splitlines():
        m = ROW_RE.match(line)
        if m:
            rows.append({
                'id': m.group(1), 'item': m.group(2).strip(), 'status': m.group(3).strip(),
                'value': m.group(4).strip(), 'source': m.group(5).strip(),
                'reason': m.group(6).strip(),
            })
    return rows


def _row_line(row):
    return f"| {row['id']} | {row['item']} | {row['status']} | {row['value']} | {row['source']} | {row['reason']} |"


def mutate_row(text, row_id, **changes):
    """Return a copy of the ledger text with one row's cells replaced (in memory only)."""
    out = []
    for line in text.splitlines():
        m = ROW_RE.match(line)
        if m and m.group(1) == row_id:
            row = parse_rows(line)[0]
            row.update(changes)
            line = _row_line(row)
        out.append(line)
    return '\n'.join(out) + '\n'


def check_row(row, pins=SOURCE_FACT_PINS):
    """Return a list of violations for one ledger row (empty = acceptable)."""
    problems = []
    status = row['status']
    for col in ('item', 'value', 'source', 'reason'):
        m = FORBIDDEN_WORD_RE.search(row[col])
        if m:
            problems.append(f"{row['id']}: forbidden word {m.group(1)} in {col}")
    if status in FORBIDDEN_STATUS:
        problems.append(f"{row['id']}: forbidden status {status}")
    elif status not in ALLOWED_STATUS:
        problems.append(f"{row['id']}: unknown status {status!r}")
    if status == 'UNKNOWN' and row['value'] != 'UNKNOWN':
        problems.append(f"{row['id']}: UNKNOWN row must carry value UNKNOWN")
    if status == 'SOURCE_FACT':
        ids = set(SOURCE_ID_RE.findall(row['source']))
        if not ids:
            problems.append(f"{row['id']}: SOURCE_FACT without a source ID")
        elif not ids <= BSL_SOURCES:
            problems.append(f"{row['id']}: SOURCE_FACT cites non-BSL source {sorted(ids - BSL_SOURCES)}")
        if pins.get(row['id']) != row['value']:
            problems.append(f"{row['id']}: SOURCE_FACT value is not the pinned source value")
    if status == 'HYPOTHESIS' and (row['value'] in ('', 'UNKNOWN') or not row['source']):
        problems.append(f"{row['id']}: HYPOTHESIS needs a value and a source")
    return problems


def ledger_problems(text, pins=SOURCE_FACT_PINS):
    """Row checks for the whole ledger, plus the Section 4 naming-conflict pin on F17."""
    rows = parse_rows(text)
    problems = [p for r in rows for p in check_row(r, pins)]
    f17 = next((r for r in rows if r['id'] == 'F17'), None)
    if f17 is None or f17['value'] != CD_SUBA_VALUE or '未解決' not in f17['reason']:
        problems.append('F17: Section 4 naming conflict must stay unresolved (CD_SUBA form, 未解決)')
    return problems


class CheckerSelfTests(unittest.TestCase):
    """Promotion attempts on synthetic rows must be rejected by the same checker."""

    PIN = {'F99': 'decl'}

    def _row(self, **kw):
        base = {'id': 'F99', 'item': 'x', 'status': 'UNKNOWN', 'value': 'UNKNOWN',
                'source': '', 'reason': ''}
        base.update(kw)
        return base

    def test_unknown_promoted_to_verified_is_rejected(self):
        self.assertTrue(check_row(self._row(status='VERIFIED', value='x', source='S1')))

    def test_observed_and_pass_are_rejected(self):
        self.assertTrue(check_row(self._row(status='OBSERVED', value='x', source='S1')))
        self.assertTrue(check_row(self._row(status='PASS', value='x', source='S1')))

    def test_source_fact_without_source_is_rejected(self):
        self.assertTrue(check_row(self._row(status='SOURCE_FACT', value='decl', source=''), self.PIN))

    def test_source_fact_from_non_bsl_source_is_rejected(self):
        self.assertTrue(check_row(self._row(status='SOURCE_FACT', value='decl', source='S7'), self.PIN))

    def test_source_fact_from_pending_s5_is_rejected(self):
        self.assertTrue(check_row(self._row(status='SOURCE_FACT', value='decl', source='S5'), self.PIN))

    def test_source_fact_with_mixed_non_bsl_cite_is_rejected(self):
        self.assertTrue(check_row(self._row(status='SOURCE_FACT', value='decl', source='S2、Z1'), self.PIN))

    def test_source_fact_with_unpinned_value_is_rejected(self):
        self.assertTrue(check_row(self._row(status='SOURCE_FACT', value='other', source='S1'), self.PIN))

    def test_forbidden_word_in_any_cell_is_rejected(self):
        self.assertTrue(check_row(self._row(status='UNKNOWN', value='UNKNOWN', reason='VERIFIED 扱い')))

    def test_forbidden_word_adjacent_to_cjk_is_rejected(self):
        # \b used to miss these; the forbidden-word violation must be reported.
        for col, val in (('reason', 'VERIFIED扱い'), ('value', 'PASS中')):
            with self.subTest(col=col):
                row = self._row()
                row[col] = val
                problems = check_row(row)
                self.assertTrue(any('forbidden word' in p and f'in {col}' in p for p in problems), problems)

    def test_hypothesis_without_value_is_rejected(self):
        self.assertTrue(check_row(self._row(status='HYPOTHESIS', value='UNKNOWN', source='Z1')))

    def test_unknown_with_a_value_is_rejected(self):
        self.assertTrue(check_row(self._row(status='UNKNOWN', value='PAUSED')))

    def test_valid_rows_are_accepted(self):
        self.assertEqual(check_row(self._row(status='UNKNOWN', value='UNKNOWN')), [])
        self.assertEqual(check_row(self._row(status='HYPOTHESIS', value='BX buffer', source='Z1')), [])
        self.assertEqual(check_row(self._row(status='SOURCE_FACT', value='decl', source='S1 478'), self.PIN), [])


class RealGateState(unittest.TestCase):
    """Slot 10: the release state is checked on the real gate, since the research document is withdrawn."""

    def test_the_real_gate_keeps_the_slot_blocked_and_not_released(self):
        ledger, contracts = gate.load()
        record = gate.resolve(10, ledger, contracts)
        self.assertEqual(record['execution'], 'BLOCKED')
        self.assertIs(record['release_eligible'], False)


if __name__ == '__main__':
    unittest.main()
