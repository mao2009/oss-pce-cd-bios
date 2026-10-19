"""Host checks for the Issue #259 CD_SUBQ caller ABI ledger (docs/research/cd-subq-contract.md).

Pure text checks: the ledger table is parsed and each row's status is validated. No ROM, no
emulator, no reference BIOS. A promotion (UNKNOWN/HYPOTHESIS -> SOURCE_FACT without a source,
or any VERIFIED/OBSERVED/PASS/DEV_CALLABLE status) must fail these tests.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LEDGER = ROOT / 'docs' / 'research' / 'cd-subq-contract.md'

ALLOWED_STATUS = {'SOURCE_FACT', 'HYPOTHESIS', 'UNKNOWN', 'BLOCKED'}
FORBIDDEN_STATUS = {'VERIFIED', 'OBSERVED', 'PASS', 'DEV_CALLABLE', 'CALLABLE'}
# Only sources whose licence is recorded as BSL-1.0 may back a SOURCE_FACT row.
BSL_SOURCES = {'S1', 'S2', 'S3', 'S5'}
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


def check_row(row):
    """Return a list of violations for one ledger row (empty = acceptable)."""
    problems = []
    status = row['status']
    if status in FORBIDDEN_STATUS:
        problems.append(f"{row['id']}: forbidden status {status}")
    elif status not in ALLOWED_STATUS:
        problems.append(f"{row['id']}: unknown status {status!r}")
    if status == 'UNKNOWN' and row['value'] != 'UNKNOWN':
        problems.append(f"{row['id']}: UNKNOWN row must carry value UNKNOWN")
    if status == 'SOURCE_FACT':
        ids = set(re.findall(r'S\d+', row['source']))
        if not ids:
            problems.append(f"{row['id']}: SOURCE_FACT without a source ID")
        elif not ids <= BSL_SOURCES:
            problems.append(f"{row['id']}: SOURCE_FACT cites non-BSL source {sorted(ids - BSL_SOURCES)}")
    if status == 'HYPOTHESIS' and (row['value'] in ('', 'UNKNOWN') or not row['source']):
        problems.append(f"{row['id']}: HYPOTHESIS needs a value and a source")
    return problems


class LedgerTableTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = LEDGER.read_text(encoding='utf-8')
        cls.rows = parse_rows(cls.text)

    def test_ledger_has_rows(self):
        self.assertGreaterEqual(len(self.rows), 15)

    def test_every_row_is_acceptable(self):
        problems = [p for row in self.rows for p in check_row(row)]
        self.assertEqual(problems, [])

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
        self.assertTrue(check_row(self._row(status='SOURCE_FACT', value='x', source='')))

    def test_source_fact_from_non_bsl_source_is_rejected(self):
        self.assertTrue(check_row(self._row(status='SOURCE_FACT', value='x', source='S7')))

    def test_hypothesis_without_value_is_rejected(self):
        self.assertTrue(check_row(self._row(status='HYPOTHESIS', value='UNKNOWN', source='Z1')))

    def test_unknown_with_a_value_is_rejected(self):
        self.assertTrue(check_row(self._row(status='UNKNOWN', value='PAUSED')))

    def test_valid_rows_are_accepted(self):
        self.assertEqual(check_row(self._row(status='UNKNOWN', value='UNKNOWN')), [])
        self.assertEqual(check_row(self._row(status='HYPOTHESIS', value='BX buffer', source='Z1')), [])
        self.assertEqual(check_row(self._row(status='SOURCE_FACT', value='decl', source='S1 478')), [])


if __name__ == '__main__':
    unittest.main()
