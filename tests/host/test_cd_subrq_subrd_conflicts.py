"""Host tests for Issue #271 (CD_SUBRQ / CD_SUBRD, $0D) research ledger.

Uses synthetic inputs and rule functions only; the ledger document is withdrawn. No emulator, ROM or BIOS
execution is involved. The claim-row parser refuses a VERIFIED or an otherwise invalid status, and the
execution-claim rule refuses a past-tense claim that the BIOS was run. Promotion of UNKNOWN or HYPOTHESIS
rows is refused by the promotion rule of the pause ledger tests.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

SOURCE_FACT_CANON = (
    '- `SOURCE_FACT`: SOURCE_FACT = a caller-side fact written in a pinned primary source. It is independent of provenance PENDING. It does NOT indicate BIOS callee behavior.'
    + '\n'
    + '  （訳: 固定版の一次出典に書かれた呼出側の事実。出典の provenance が PENDING かどうかとは独立。BIOS 呼出先の振る舞いを示さない。）出典が第三者の記述のみ、または再実装・開発者の主張である場合は `HYPOTHESIS` とする。ライセンス上の利用可否、呼出先の実測、実装可能性は別の軸であり、SOURCE_FACT はそのいずれも示さない。'
)

ALLOWED = {'SOURCE_FACT', 'SECONDARY_SOURCE', 'HYPOTHESIS', 'UNKNOWN'}
EXPECTED = {
    'C1': 'SOURCE_FACT', 'C2': 'SOURCE_FACT', 'C3': 'SECONDARY_SOURCE', 'C4': 'UNKNOWN',
    'C5': 'UNKNOWN', 'C6': 'SECONDARY_SOURCE', 'C7': 'SECONDARY_SOURCE', 'C8': 'UNKNOWN',
    'C9': 'SECONDARY_SOURCE', 'C10': 'UNKNOWN', 'C11': 'UNKNOWN', 'C12': 'UNKNOWN',
    'C13': 'UNKNOWN', 'C14': 'UNKNOWN', 'C15': 'SECONDARY_SOURCE', 'C16': 'UNKNOWN',
    'C17': 'HYPOTHESIS', 'C18': 'HYPOTHESIS', 'C19': 'UNKNOWN',
}
# Full 40-hex pins, resolved with `gh api` (commits) and the retro-go tree (blob).
# 797845ce... is a git blob (pctech.txt), not a commit, so it is cited only as a blob.
HUC_SHA = '54f5c73606c1e7142e8095c727cab2767e2b3cb3'
RETRO_GO_SHA = '05e97e80ef0943404ed0d37671f6ab3074017d55'
HUGO_SHA = '64ed226da2288738781743eeef228c069e8973a4'
HUEXPRESS_SHA = 'b91fd8e30d5898c8ca9c6c749a5f5106a03e22d0'
GEARGRAFX_SHA = '3685f8cf50070a1647dd3536942718a8581d9db5'
PCTECH_BLOB_SHA = '797845ce11715712a740dc0272fc6ad1fa26c490'
FULL_PINS = {HUC_SHA, RETRO_GO_SHA, HUGO_SHA, HUEXPRESS_SHA, GEARGRAFX_SHA, PCTECH_BLOB_SHA}
MD_COMMIT = f'ducalex/retro-go@{RETRO_GO_SHA}'
MD_BLOB = f'blob `{PCTECH_BLOB_SHA}`'
GG_COMMIT = f'drhelius/Geargrafx@{GEARGRAFX_SHA}'
GG_UNRESOLVED = 'drhelius/Geargrafx@1e52d13f'
# Short token that appears in the doc only as a rejected (unresolvable) old pin.
REJECTED_SHORT_PINS = {'1e52d13f'}
# Any lowercase hex token of 7+ chars in the doc must be a full pin (or an explicitly rejected one).
SHA_TOKEN = re.compile(r'(?<![0-9A-Za-z])[0-9a-f]{7,40}(?![0-9A-Za-z])')
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


CLAIM_COLUMNS = 5  # ID, 項目, 主張, 状態, 裁定


def _claim_rows(text):
    # Fail closed: a malformed or duplicate claim row raises instead of being overwritten
    # by (and hidden behind) a later valid row.
    rows = {}
    for line in text.splitlines():
        if not re.match(r'\s*\|\s*[Cc]\d', line):
            continue
        cells = [c.strip() for c in line.strip().strip('|').split('|')]
        if not re.fullmatch(r'C\d+', cells[0]):
            raise ValueError(f'malformed claim id: {cells[0]}')
        if len(cells) != CLAIM_COLUMNS:
            raise ValueError(f'{cells[0]}: {len(cells)} columns, expected {CLAIM_COLUMNS}')
        if cells[0] in rows:
            raise ValueError(f'duplicate claim id: {cells[0]}')
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


class ExecRuleTests(unittest.TestCase):
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


class ClaimRowNegativeTests(unittest.TestCase):
    ROW = '| {i} | 項目 | 主張 | {st} | 裁定 |\n'

    def _doc(self, *rows):
        return '# t\n' + ''.join(rows)

    def test_every_id_duplicate_is_rejected_in_either_order(self):
        for n in range(1, 20):
            cid = f'C{n}'
            good = self.ROW.format(i=cid, st='UNKNOWN')
            bad = self.ROW.format(i=cid, st='VERIFIED')
            for order in ((bad, good), (good, bad), (good, good)):
                with self.assertRaises(ValueError, msg=(cid, order)):
                    _claim_rows(self._doc(*order))

    def test_wrong_column_count_is_rejected_for_every_id(self):
        for n in range(1, 20):
            cid = f'C{n}'
            for row in (f'| {cid} | a | b | UNKNOWN |\n', f'| {cid} | a | b | UNKNOWN | c | d |\n'):
                with self.assertRaises(ValueError, msg=row):
                    _claim_rows(self._doc(row))

    def test_invalid_row_is_not_hidden_by_later_valid_row(self):
        bad = '| C4 | a | b | UNKNOWN |\n'
        good = self.ROW.format(i='C4', st='UNKNOWN')
        for order in ((bad, good), (good, bad)):
            with self.assertRaises(ValueError):
                _claim_rows(self._doc(*order))

    def test_malformed_id_is_rejected(self):
        with self.assertRaises(ValueError):
            _claim_rows(self._doc('| C5x | a | b | UNKNOWN | c |\n'))

    def test_loosely_formatted_claim_rows_are_not_skipped(self):
        for row in ('|C5| a | b | UNKNOWN |\n', '  | C5 | a | b | UNKNOWN |\n', '| c5 | a | b | UNKNOWN | c |\n'):
            with self.assertRaises(ValueError, msg=row):
                _claim_rows(self._doc(row))

    def test_valid_rows_still_parse(self):
        rows = _claim_rows(self._doc(self.ROW.format(i='C1', st='UNKNOWN'), self.ROW.format(i='C2', st='HYPOTHESIS')))
        self.assertEqual(list(rows), ['C1', 'C2'])


if __name__ == '__main__':
    unittest.main()
