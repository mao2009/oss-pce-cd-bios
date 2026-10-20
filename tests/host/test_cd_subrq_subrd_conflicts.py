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


class CdSubrqSubrdConflictLedgerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = DOC.read_text(encoding='utf-8')
        cls.rows = _claim_rows(cls.text)

    def test_canonical_source_fact_definition_present(self):
        self.assertIn(SOURCE_FACT_CANON, self.text)

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
        self.assertEqual(self.rows['C18'][3], 'HYPOTHESIS')
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

    def test_every_sha_token_is_a_full_40_hex_pin(self):
        # Short SHAs (8 hex) are not allowed anywhere in the doc, including revision cells.
        self.assertEqual(len(FULL_PINS), 6)
        for pin in FULL_PINS:
            self.assertEqual(len(pin), 40, pin)
            self.assertIn(pin, self.text, pin)
        for tok in SHA_TOKEN.findall(self.text):
            if tok in REJECTED_SHORT_PINS:
                continue
            self.assertEqual(len(tok), 40, f'short SHA in doc: {tok}')
            self.assertIn(tok, FULL_PINS, tok)

    def test_c1_source_fact_is_hc_name_only(self):
        # Review: HG and HX are HLE reimplementations, not primary sources, so they cannot back SOURCE_FACT.
        c1 = self.rows['C1']
        self.assertEqual(c1[3], 'SOURCE_FACT')
        self.assertIn('HC', c1[2])
        self.assertIn('481 行', c1[2])
        self.assertNotIn('HG', c1[2])
        self.assertNotIn('HX', c1[2])

    def test_hg_hx_name_legs_are_hypothesis(self):
        # Ledger SOURCE_FACT definition: reimplementation / developer claims are HYPOTHESIS.
        c18 = self.rows['C18']
        self.assertEqual(c18[3], 'HYPOTHESIS')
        for src in ('Z', 'HG', 'HX'):
            self.assertIn(src, c18[2], src)
        self.assertIn('HLE', c18[2])
        self.assertIn('conflict unresolved', c18[4])

    def test_c19_notes_zeograd_general_description_only(self):
        self.assertIn('Z の一般記述のみで $0D 固有の記載はない', self.rows['C19'][2])
        self.assertNotIn('18 行目', self.rows['C19'][2])

    def test_hc_zero_callers_is_local_grep_only(self):
        hc = [l for l in self.text.splitlines() if l.startswith('| HC | cd_subrd = $00:E027')][0]
        self.assertIn('local `git grep -i`', hc)
        self.assertIn('独立の再確認は未実施', hc)

    def test_claim_rows_are_in_ascending_id_order(self):
        ids = list(self.rows)
        self.assertEqual(ids, sorted(ids, key=lambda c: int(c[1:])))

    def test_zeograd_source_records_version_and_hash_status(self):
        z = _source_row(self.text, 'Z')
        self.assertIn('版なし', z)
        self.assertIn('本文ハッシュは未記録', z)
        self.assertIn('SECONDARY_SOURCE', z)

    def test_no_reference_bios_execution_claimed(self):
        self.assertIn('一切していない', self.text)
        # The verbatim canonical definition (negated callee wording, 'pinned') is exempt.
        scanned = self.text.replace(SOURCE_FACT_CANON, '')
        self.assertEqual(_exec_violations(scanned), [], 'affirmative execution wording found')
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

    def test_real_doc_rows_each_duplicated_are_rejected(self):
        lines = [l for l in DOC.read_text(encoding='utf-8').splitlines() if l.startswith('| C')]
        self.assertEqual(len(lines), 19)
        for l in lines:
            for dup in (lines + [l], [l] + lines):
                with self.assertRaises(ValueError, msg=l[:8]):
                    _claim_rows('\n'.join(dup))

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
