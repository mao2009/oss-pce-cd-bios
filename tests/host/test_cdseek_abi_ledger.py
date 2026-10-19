"""Host tests for the Issue #214 CD_SEEK ($04, $E00C) caller-ABI ledger.

These tests check the ledger's rules (status vocabulary, no VERIFIED/OBSERVED, callee rows never
promoted from caller source, UNKNOWN values never filled), the exact row set (K1-K10, C1-C11,
R1-R3), and one pure fact quoted from the pinned HuCC source: the cd_seek equate. No emulator,
no ROM and no System Card code is involved, and the table arithmetic is NOT evidence of BIOS
behaviour.

外部由来の LBA 表は本ファイルに埋め込まない。表の算術の検査は合成値で行い、固定版での一回限りの
確認結果は docs/cd-seek-abi-ledger.md §4 に残す。
"""
import re
import sys
import unittest
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / 'docs' / 'cd-seek-abi-ledger.md'

# Quoted from the pinned HuCC source (include/hucc/pcengine.inc L472; caller-side equate only).
CD_SEEK_EQUATE = 'cd_seek\t\t= $00:E00C, FUNC'
# call_bios (include/hucc/core-kernel.asm L500-516) enters at $E000 + Y, and the caller sets Y=$0C.
CALL_BIOS_BASE = 0xE000
CALL_BIOS_Y_FOR_SEEK = 0x0C

# 合成値の表（外部データではない）。台帳の記載どおり 38 件、先頭 37 件が映像表、末尾 1 件は
# 映像の LBA ではない（順序を外れる）という形だけを真似る。
SYNTH_TABLE_COUNT = 38
SYNTH_VIDEO_COUNT = 37
SYNTH_TABLE = [0x010000 + 0x100 * i for i in range(SYNTH_VIDEO_COUNT)] + [0x000001]

# 台帳の行の正本: ID と側。K1-K10（呼出側）、C1-C11（呼出先）、R1-R3（関係）の 24 項目。
EXPECTED_SIDES = {
    **{f'K{i}': 'K' for i in range(1, 11)},
    **{f'C{i}': 'C' for i in range(1, 12)},
    **{f'R{i}': 'R' for i in range(1, 4)},
}
C1_ENTRY = '$E00C'
FIELDS = ['id', 'side', 'item', 'status', 'value', 'source', 'reason', 'test']

STATUSES = {'SOURCE_FACT', 'CALLER_INFERENCE', 'HYPOTHESIS', 'UNKNOWN', 'BLOCKED'}
CALLEE_STATUSES = {'HYPOTHESIS', 'UNKNOWN', 'BLOCKED'}
FORBIDDEN = {'VERIFIED', 'OBSERVED'}
NEEDS_SOURCE = {'SOURCE_FACT', 'CALLER_INFERENCE', 'HYPOTHESIS'}


def _cells(line):
    return [c.strip() for c in line.strip().strip('|').split('|')]


def _bare(value):
    """Cell text without the Markdown code-span backticks."""
    return value.strip().strip('`').strip()


CANONICAL_ID_RE = re.compile(r'[A-Z]+\d+')
SECTION3_RE = re.compile(r'^## 3\..*?$(.*?)(?=^## |\Z)', re.M | re.S)


def _section3(text):
    m = SECTION3_RE.search(text)
    return m.group(1) if m else ''


def _id_like_token(s):
    """Return the first word of a pipe-less line if it looks like an ID (K11, **K11**, `k11`, - K11:).

    Prose such as headings, notes or Japanese sentences does not match and returns None.
    """
    s = re.sub(r'^[-+>]\s+', '', s)
    tok = s.split()[0]
    return tok if re.fullmatch(r'[A-Za-z]+\d+', tok.strip('*`:.,;')) else None


def parse_ledger(text):
    """Return (item rows, source ids, malformed item rows) for the ledger.

    Items come only from the §3 table. Every non-blank line in §3 must be a canonical ID row with a
    leading and trailing pipe and all cells (the header and separator excepted), or it is malformed.
    A line with a pipe that does not start with one is malformed too. A line without a pipe is
    malformed when its first word looks like an ID (K11 K x, **K11** K x, - K11: K x); other
    prose (headings, notes, blank lines) is ignored. Source ids (S1..S7) come from the whole
    document and never become items.
    """
    sources = set()
    for line in text.splitlines():
        if line.lstrip().startswith('|'):
            first = _bare(_cells(line)[0])
            if re.fullmatch(r'S\d+', first):
                sources.add(first)
    items, malformed = [], []
    for line in _section3(text).splitlines():
        s = line.strip()
        if not s:
            continue
        if not s.startswith('|'):
            if '|' in s:
                malformed.append(s.split('|', 1)[0].strip())
            else:
                tok = _id_like_token(s)
                if tok is not None:
                    malformed.append(tok)
            continue
        if set(s) <= set('|-: ') and '-' in s:
            continue
        cells = [c.strip() for c in s.strip('|').split('|')]
        first = cells[0]
        if first == 'ID':
            continue
        if s.endswith('|') and CANONICAL_ID_RE.fullmatch(first) and len(cells) == len(FIELDS):
            items.append(dict(zip(FIELDS, cells)))
        else:
            malformed.append(first)
    return items, sources, malformed


def ledger_problems(items, malformed=()):
    """Return a list of row-set problems; an empty list means the ledger is exactly the 24 rows."""
    problems = [f'malformed row {i}' for i in malformed]
    counts = Counter(r['id'] for r in items)
    problems += [f'duplicate {i} x{n}' for i, n in counts.items() if n > 1]
    problems += [f'missing {i}' for i in EXPECTED_SIDES if i not in counts]
    problems += [f'unexpected {i}' for i in counts if i not in EXPECTED_SIDES]
    for r in items:
        want = EXPECTED_SIDES.get(r['id'])
        if want is not None and r['side'] != want:
            problems.append(f"side {r['id']}={r['side']} expected {want}")
    for r in items:
        if r['id'] == 'C1' and _bare(r['value']) != C1_ENTRY:
            problems.append(f"C1 value {r['value']!r} expected {C1_ENTRY!r}")
    return problems


def is_valid_binary_msf(b):
    m, s, f = b
    return m < 100 and s < 60 and f < 75


def is_valid_bcd_msf(b):
    def bcd(x):
        return (x >> 4) <= 9 and (x & 0x0F) <= 9
    if not all(bcd(x) for x in b):
        return False
    m, s, f = ((x >> 4) * 10 + (x & 0x0F) for x in b)
    return s < 60 and f < 75 and m < 100


TEXT = DOC.read_text(encoding='utf-8')


class LedgerRowTests(unittest.TestCase):
    def test_ledger_rows_are_exactly_the_24_expected_ids_and_sides(self):
        items, _, malformed = parse_ledger(TEXT)
        self.assertEqual(len(EXPECTED_SIDES), 24)
        self.assertEqual(ledger_problems(items, malformed), [])
        self.assertEqual(len(items), 24)

    def test_c1_entry_cell_is_e00c_in_the_parsed_row(self):
        items, _, _ = parse_ledger(TEXT)
        c1 = [r for r in items if r['id'] == 'C1']
        self.assertEqual(len(c1), 1)
        self.assertEqual(c1[0]['side'], 'C')
        self.assertEqual(_bare(c1[0]['value']), C1_ENTRY)


class LedgerRuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.items, cls.sources, _ = parse_ledger(TEXT)
        cls.text = TEXT

    def test_status_vocabulary_excludes_verified_and_observed(self):
        for r in self.items:
            self.assertIn(r['status'], STATUSES, r['id'])
            self.assertNotIn(r['status'], FORBIDDEN, r['id'])
        for word in FORBIDDEN:
            self.assertNotRegex(self.text, r'\|\s*' + word + r'\s*\|', word)

    def test_callee_and_relation_rows_never_claim_source_facts(self):
        for r in self.items:
            if r['side'] in ('C', 'R'):
                self.assertIn(r['status'], CALLEE_STATUSES, r['id'])

    def test_unknown_and_blocked_rows_fill_no_value(self):
        for r in self.items:
            if r['status'] in ('UNKNOWN', 'BLOCKED'):
                self.assertEqual(r['value'], 'UNKNOWN', r['id'])

    def test_claims_cite_declared_sources(self):
        for r in self.items:
            cited = set(re.findall(r'S\d+', r['source']))
            if r['status'] in NEEDS_SOURCE:
                self.assertTrue(cited, r['id'])
            self.assertTrue(cited <= self.sources, (r['id'], cited - self.sources))

    def test_ids_used_in_table_are_defined_sources(self):
        self.assertEqual(self.sources, {'S1', 'S2', 'S3', 'S4', 'S5', 'S6', 'S7'})


class LedgerMutationTests(unittest.TestCase):
    """Deliberate ledger edits must be caught by ledger_problems (the real file is not touched)."""

    def _lines(self):
        return TEXT.splitlines(keepends=True)

    def _idx(self, lines, prefix):
        hits = [i for i, l in enumerate(lines) if l.startswith(prefix)]
        self.assertEqual(len(hits), 1, prefix)
        return hits[0]

    def _problems(self, lines):
        items, _, malformed = parse_ledger(''.join(lines))
        return ledger_problems(items, malformed)

    def test_real_ledger_has_no_problems(self):
        self.assertEqual(self._problems(self._lines()), [])

    def test_deleted_row_is_detected(self):
        lines = self._lines()
        del lines[self._idx(lines, '| K5 |')]
        self.assertIn('missing K5', self._problems(lines))

    def test_changed_c1_entry_is_detected_even_though_e00c_appears_elsewhere(self):
        # 文書の他の箇所に $E00C が残っていても、C1 の入口欄が変われば失敗する。
        lines = self._lines()
        i = self._idx(lines, '| C1 |')
        self.assertIn('`$E00C`', lines[i])
        lines[i] = lines[i].replace('`$E00C`', '`$E00D`')
        self.assertIn('$E00C', ''.join(lines))
        self.assertTrue(any(p.startswith('C1 value') for p in self._problems(lines)))

    def test_swapped_side_is_detected(self):
        lines = self._lines()
        i = self._idx(lines, '| K3 |')
        self.assertIn('| K3 | K |', lines[i])
        lines[i] = lines[i].replace('| K3 | K |', '| K3 | C |', 1)
        self.assertIn('side K3=C expected K', self._problems(lines))

    def test_duplicate_row_is_detected(self):
        lines = self._lines()
        i = self._idx(lines, '| K1 |')
        lines.insert(i + 1, lines[i])
        self.assertIn('duplicate K1 x2', self._problems(lines))

    def test_unexpected_row_is_detected(self):
        lines = self._lines()
        i = self._idx(lines, '| K10 |')
        lines.insert(i + 1, lines[i].replace('| K10 |', '| K11 |', 1))
        self.assertIn('unexpected K11', self._problems(lines))

    def test_row_with_other_prefix_is_detected_as_unexpected(self):
        lines = self._lines()
        i = self._idx(lines, '| K10 |')
        lines.insert(i + 1, lines[i].replace('| K10 |', '| D1 |', 1))
        self.assertIn('unexpected D1', self._problems(lines))

    def test_row_with_malformed_id_is_detected(self):
        lines = self._lines()
        i = self._idx(lines, '| K10 |')
        lines.insert(i + 1, lines[i].replace('| K10 |', '| K 11 |', 1))
        self.assertIn('malformed row K 11', self._problems(lines))

    def test_row_without_space_after_pipe_is_detected(self):
        lines = self._lines()
        i = self._idx(lines, '| K10 |')
        lines.insert(i + 1, lines[i].replace('| K10 |', '|K11 |', 1))
        self.assertIn('unexpected K11', self._problems(lines))

    def test_row_with_backticked_id_is_detected(self):
        lines = self._lines()
        i = self._idx(lines, '| K10 |')
        lines.insert(i + 1, lines[i].replace('| K10 |', '| `K11` |', 1))
        self.assertIn('malformed row `K11`', self._problems(lines))

    def test_row_with_bold_id_is_detected(self):
        lines = self._lines()
        i = self._idx(lines, '| K10 |')
        lines.insert(i + 1, lines[i].replace('| K10 |', '| **K11** |', 1))
        self.assertIn('malformed row **K11**', self._problems(lines))

    def test_row_with_lowercase_id_is_detected(self):
        lines = self._lines()
        i = self._idx(lines, '| K10 |')
        lines.insert(i + 1, lines[i].replace('| K10 |', '| k11 |', 1))
        self.assertIn('malformed row k11', self._problems(lines))

    def test_row_without_leading_pipe_is_detected(self):
        lines = self._lines()
        i = self._idx(lines, '| K10 |')
        lines.insert(i + 1, 'K11 | K | x\n')
        self.assertIn('malformed row K11', self._problems(lines))

    def test_existing_k1_changed_to_lowercase_is_detected(self):
        lines = self._lines()
        i = self._idx(lines, '| K1 |')
        lines[i] = lines[i].replace('| K1 |', '| k1 |', 1)
        problems = self._problems(lines)
        self.assertIn('malformed row k1', problems)
        self.assertIn('missing K1', problems)

    def test_existing_k1_changed_to_bold_is_detected(self):
        lines = self._lines()
        i = self._idx(lines, '| K1 |')
        lines[i] = lines[i].replace('| K1 |', '| **K1** |', 1)
        problems = self._problems(lines)
        self.assertIn('malformed row **K1**', problems)
        self.assertIn('missing K1', problems)

    def test_pipeless_id_line_is_detected_as_malformed(self):
        lines = self._lines()
        i = self._idx(lines, '| K10 |')
        lines.insert(i + 1, 'K11 K x\n')
        self.assertIn('malformed row K11', self._problems(lines))

    def test_pipeless_bold_id_line_is_detected_as_malformed(self):
        lines = self._lines()
        i = self._idx(lines, '| K10 |')
        lines.insert(i + 1, '**K11** K x\n')
        self.assertIn('malformed row **K11**', self._problems(lines))

    def test_row_with_missing_cell_is_detected(self):
        lines = self._lines()
        i = self._idx(lines, '| K2 |')
        cells = _cells(lines[i])
        lines[i] = '| ' + ' | '.join(cells[:-1]) + ' |\n'
        self.assertIn('malformed row K2', self._problems(lines))


class CallerFactTests(unittest.TestCase):
    def test_cd_seek_entry_is_call_bios_base_plus_y(self):
        m = re.search(r'\$00:([0-9A-F]{4})', CD_SEEK_EQUATE)
        self.assertIsNotNone(m)
        entry = int(m.group(1), 16)
        self.assertEqual(entry, CALL_BIOS_BASE + CALL_BIOS_Y_FOR_SEEK)
        self.assertEqual(entry, 0xE00C)

    def test_synthetic_table_is_24bit_and_video_part_is_increasing(self):
        # 合成表の形だけを確かめる。実表の検査ではない。
        self.assertEqual(len(SYNTH_TABLE), SYNTH_TABLE_COUNT)
        for v in SYNTH_TABLE:
            self.assertLess(v, 1 << 24)
        video = SYNTH_TABLE[:SYNTH_VIDEO_COUNT]
        for a, b in zip(video, video[1:]):
            self.assertLess(a, b)
        # 末尾の非映像エントリは意図的に順序を外す（映像 LBA ではない）。
        self.assertLess(SYNTH_TABLE[-1], video[-1])

    def test_msf_checkers_accept_and_reject_synthetic_values(self):
        # 判定器の挙動だけを確かめる。BIOS の証拠ではない。
        self.assertTrue(is_valid_binary_msf((0, 2, 0)))
        self.assertFalse(is_valid_binary_msf((0, 60, 0)))
        self.assertFalse(is_valid_binary_msf((0, 0, 75)))
        self.assertFalse(is_valid_binary_msf((100, 0, 0)))
        self.assertTrue(is_valid_bcd_msf((0x00, 0x02, 0x00)))
        self.assertFalse(is_valid_bcd_msf((0x00, 0x0A, 0x00)))
        self.assertFalse(is_valid_bcd_msf((0x00, 0x60, 0x00)))

        # 二通りのどちらの MSF としても成立しない合成値は、判定器で必ず不成立になる。
        def split(v):
            return (v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF

        bad = [v for v in SYNTH_TABLE + [0xFF0000]
               if not (is_valid_binary_msf(split(v)) or is_valid_bcd_msf(split(v)))]
        self.assertEqual(bad, [0xFF0000])


if __name__ == '__main__':
    unittest.main()
