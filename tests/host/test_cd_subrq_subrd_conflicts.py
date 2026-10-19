"""Host tests for Issue #271 (CD_SUBRQ / CD_SUBRD, $0D) research ledger.

Reads docs/research/cd-subrq-subrd-conflicts.md as text only. No emulator, ROM or BIOS
execution is involved. The pinned statuses guard against silent promotion of an UNKNOWN
or silent resolution of a name/return conflict; changing one requires changing this file
in review.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / 'docs' / 'research' / 'cd-subrq-subrd-conflicts.md'

ALLOWED = {'SOURCE_FACT', 'SECONDARY_SOURCE', 'HYPOTHESIS', 'UNKNOWN'}
EXPECTED = {
    'C1': 'SOURCE_FACT', 'C2': 'SOURCE_FACT', 'C3': 'SECONDARY_SOURCE', 'C4': 'UNKNOWN',
    'C5': 'UNKNOWN', 'C6': 'SECONDARY_SOURCE', 'C7': 'SECONDARY_SOURCE', 'C8': 'UNKNOWN',
    'C9': 'SECONDARY_SOURCE', 'C10': 'UNKNOWN', 'C11': 'UNKNOWN', 'C12': 'UNKNOWN',
    'C13': 'UNKNOWN', 'C14': 'UNKNOWN', 'C15': 'SECONDARY_SOURCE', 'C16': 'UNKNOWN',
    'C17': 'HYPOTHESIS', 'C18': 'SECONDARY_SOURCE',
}
# Commit pins. 797845ce is a git blob (pctech.txt), not a commit, so it is cited only as a blob.
MD_COMMIT = 'ducalex/retro-go@05e97e80'
MD_BLOB = 'blob `797845ce`'
GG_COMMIT = 'drhelius/Geargrafx@3685f8cf'
GG_UNRESOLVED = 'drhelius/Geargrafx@1e52d13f'
# Execution rule (structural, not a verb list). A line is a claim when it contains a
# completed-action or past-tense marker AND either a reference-callee subject (BIOS, ROM,
# callee, slot, $0D, $E0xx, CD_SUBRQ/CD_SUBRD). A line that asserts an observed result
# (観測した, 実測値 ...) fails on its own. A failing line fails even if it also says
# 未実行/NOT_RUN. A line with no past marker is not a claim, so 未実行/NOT_RUN wording passes.
# Limits: a past verb with no subject word on the same line is not caught; present-tense
# plans ("呼ぶ") are not caught; subject detection is a fixed token set. Keep the doc to
# present or negated wording about execution.
# English: "-ed" words, except "un-" adjectives such as "unresolved" (the doc's own status words).
PAST_MARK = re.compile(r'(?:た|んだ)(?![ぁ-ん])|(?i:\b(?!un)\w+ed\b|\bran\b)')
CALLEE_SUBJECT = re.compile(
    r'\bBIOS\b|\bROM\b|[Cc]allee|呼出先|呼び出し先|スロット|\bslot\b|\$0D|\$E0[0-9A-Fa-f]{2}'
    r'|CD_SUBR[QD]')
RESULT_CLAIM = re.compile(r'(?:観測|実測|確認)(?:した|され|できた)')
FORBIDDEN_CELL_TOKENS = {'VERIFIED', 'OBSERVED', 'PASS'}


def _claim_rows(text):
    rows = {}
    for line in text.splitlines():
        if not line.startswith('| C'):
            continue
        cells = [c.strip() for c in line.strip().strip('|').split('|')]
        if re.fullmatch(r'C\d+', cells[0]):
            rows[cells[0]] = cells
    return rows


def _exec_violations(text):
    out = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if (PAST_MARK.search(line) and CALLEE_SUBJECT.search(line)) or RESULT_CLAIM.search(line):
            out.append((lineno, line.strip()))
    return out


def _source_row(text, sid):
    # Section 1 (source table) comes first; section 2 reuses the same ids.
    hits = [line for line in text.splitlines() if line.startswith(f'| {sid} |')]
    return hits[0]


class CdSubrqSubrdConflictLedgerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = DOC.read_text(encoding='utf-8')
        cls.rows = _claim_rows(cls.text)

    def test_claim_statuses_are_pinned_and_within_allowed_set(self):
        statuses = {cid: cells[3] for cid, cells in self.rows.items()}
        self.assertEqual(statuses, EXPECTED)
        self.assertTrue(set(statuses.values()) <= ALLOWED)

    def test_no_verified_or_observed_claim_in_table(self):
        for cid, cells in self.rows.items():
            self.assertNotIn(cells[3], {'VERIFIED', 'OBSERVED', 'PASS'}, cid)

    def test_name_conflict_is_kept_unresolved(self):
        self.assertIn('conflict unresolved', self.rows['C1'][4])
        self.assertIn('同一と断定しない', self.rows['C1'][4])

    def test_zeograd_name_leg_is_not_source_fact(self):
        # C1 is the HuC/Hu-Go/huexpress leg only; the Zeograd leg is split into C18.
        self.assertIsNone(re.search(r'(?<![A-Za-z0-9_])Z(?![A-Za-z0-9_])', ' '.join(self.rows['C1'])))
        self.assertEqual(self.rows['C18'][3], 'SECONDARY_SOURCE')
        self.assertIn('conflict unresolved', self.rows['C18'][4])

    def test_return_and_q_conflicts_are_kept_unresolved(self):
        self.assertIn('conflict unresolved', self.rows['C4'][4])
        self.assertIn('conflict unresolved', self.rows['C8'][4])

    def test_same_mechanism_hypothesis_is_not_adopted(self):
        self.assertIn('採用しない', self.rows['C17'][4])

    def test_blocked_and_release_gate_are_stated(self):
        self.assertIn('BLOCKED_ABI', self.text)
        self.assertIn('release_eligible=false', self.text)
        self.assertIn('DEV_CALLABLE', self.text)

    def test_citation_pins_resolve_to_commits(self):
        md = _source_row(self.text, 'MD')
        self.assertIn(MD_COMMIT, md)
        self.assertIn(MD_BLOB, md)
        self.assertNotIn('@797845ce', self.text)
        self.assertIn('1473–1487 行', md)
        gg = _source_row(self.text, 'GG')
        self.assertIn(GG_COMMIT, gg)
        self.assertNotIn(GG_UNRESOLVED, self.text)
        hc = _source_row(self.text, 'HC')
        self.assertIn('692 行', hc)
        self.assertIn('481 行', hc)

    def test_no_reference_bios_execution_claimed(self):
        self.assertIn('一切していない', self.text)
        self.assertEqual(_exec_violations(self.text), [], 'affirmative execution wording found')
        for cid, cells in self.rows.items():
            for cell in cells:
                self.assertFalse(set(re.findall(r'[A-Z_]+', cell)) & FORBIDDEN_CELL_TOKENS, cid)

    def test_exec_rule_flags_past_tense_callee_phrasings(self):
        # Regression: each of these passed the former verb-stem list.
        for phrase in (
            'BIOS を呼んだ',
            'BIOS を呼出した',
            '$E027 を call した',
            'BIOS を動作させた',
            'ROM をブートした',
            # Earlier regressions, kept with an explicit subject so the rule covers them.
            'BIOS を呼び出した',
            'ROM を走らせた',
            'ROM を boot した',
        ):
            self.assertTrue(_exec_violations(phrase), phrase)

    def test_exec_rule_flags_not_run_mixed_with_past_claim(self):
        # Regression: a 未実行/NOT_RUN mark must not exempt a past-tense execution claim.
        self.assertTrue(_exec_violations('BIOS を呼び出した。実行は未実行（NOT_RUN）'))

    def test_exec_rule_flags_observed_result_claim(self):
        self.assertTrue(_exec_violations('CD_SUBRQ の戻り値を観測した'))

    def test_exec_rule_allows_not_run_and_plans_without_past_claim(self):
        self.assertEqual(_exec_violations('| 実行は未実行（NOT_RUN） |'), [])
        self.assertEqual(_exec_violations('BIOS は呼んでいない。$E027 を呼ぶ前後で差分を取る'), [])


if __name__ == '__main__':
    unittest.main()
