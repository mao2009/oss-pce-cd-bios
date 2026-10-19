"""Host checks for the Issue #259 CD_SUBQ caller ABI ledger (docs/research/cd-subq-contract.md).

Pure text checks: the ledger table is parsed and each row's status is validated. No ROM, no
emulator, no reference BIOS. A promotion (UNKNOWN/HYPOTHESIS -> SOURCE_FACT without a pinned
value, or any VERIFIED/OBSERVED/PASS/DEV_CALLABLE status) must fail these tests.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LEDGER = ROOT / 'docs' / 'research' / 'cd-subq-contract.md'

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


class LedgerTableTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = LEDGER.read_text(encoding='utf-8')
        cls.rows = parse_rows(cls.text)

    def test_ledger_has_rows(self):
        self.assertGreaterEqual(len(self.rows), 15)

    def test_every_row_is_acceptable(self):
        self.assertEqual(ledger_problems(self.text), [])

    def test_no_row_claims_verified_or_callable(self):
        for row in self.rows:
            self.assertNotIn(row['status'], FORBIDDEN_STATUS, row['id'])

    def test_callee_stays_blocked_and_not_release_eligible(self):
        self.assertIn('release_eligible=false', self.text)
        self.assertIn('BLOCKED_ABI', self.text)
        self.assertIn('DEV_CALLABLE` なし', self.text)

    def test_abi_fields_present_as_unknown_or_hypothesis(self):
        # The issue's field list: inputs A/X/Y/P, $20F8-$20FF, return flags, SP/MPR, IRQ,
        # side effects and failure. None of these may be SOURCE_FACT for the callee.
        by_id = {r['id']: r for r in self.rows}
        for fid in ('F6', 'F8', 'F11', 'F13', 'F14', 'F15', 'F16'):
            self.assertIn(by_id[fid]['status'], {'UNKNOWN', 'BLOCKED'}, fid)


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


class MutationProbeTests(unittest.TestCase):
    """Mutations applied in memory to the real ledger text. Each must produce a problem."""

    @classmethod
    def setUpClass(cls):
        cls.text = LEDGER.read_text(encoding='utf-8')
        cls.rows = parse_rows(cls.text)

    def _flagged(self, mutated, row_id):
        return [p for p in ledger_problems(mutated) if p.startswith(row_id + ':')]

    def test_baseline_ledger_is_clean(self):
        self.assertEqual(ledger_problems(self.text), [])

    def test_f7_hypothesis_promoted_to_source_fact_with_bsl_cite_is_rejected(self):
        mutated = mutate_row(self.text, 'F7', status='SOURCE_FACT', source='S2')
        self.assertTrue(self._flagged(mutated, 'F7'))

    def test_every_hypothesis_or_unknown_promotion_is_rejected(self):
        for row in self.rows:
            if row['status'] in ('HYPOTHESIS', 'UNKNOWN'):
                with self.subTest(row=row['id']):
                    mutated = mutate_row(self.text, row['id'], status='SOURCE_FACT', source='S2')
                    self.assertTrue(self._flagged(mutated, row['id']))

    def test_mixed_cite_with_z_source_is_rejected(self):
        mutated = mutate_row(self.text, 'F12', source='S2、Z1')
        self.assertTrue(self._flagged(mutated, 'F12'))

    def test_excluded_or_pending_sources_are_rejected_as_source_fact_support(self):
        for src in ('S5', 'X1', 'X2'):
            with self.subTest(src=src):
                mutated = mutate_row(self.text, 'F12', source=src)
                self.assertTrue(self._flagged(mutated, 'F12'))

    def test_f2_is_not_source_fact(self):
        f2 = next(r for r in self.rows if r['id'] == 'F2')
        self.assertNotEqual(f2['status'], 'SOURCE_FACT')

    def test_naming_conflict_silently_resolved_to_cd_subq_is_rejected(self):
        mutated = mutate_row(self.text, 'F17', value='`CD_SUBQ`')
        self.assertTrue(any(p.startswith('F17:') for p in ledger_problems(mutated)))

    def test_naming_conflict_reason_claiming_resolution_is_rejected(self):
        f17 = next(r for r in self.rows if r['id'] == 'F17')
        mutated = mutate_row(self.text, 'F17', reason=f17['reason'].replace('未解決', '解決済み'))
        self.assertTrue(any(p.startswith('F17:') for p in ledger_problems(mutated)))

    def test_forbidden_word_in_each_cell_is_rejected(self):
        for col in ('item', 'value', 'source', 'reason'):
            with self.subTest(col=col):
                mutated = mutate_row(self.text, 'F9', **{col: 'PASS'})
                self.assertTrue(any(f'in {col}' in p for p in ledger_problems(mutated)))


if __name__ == '__main__':
    unittest.main()
