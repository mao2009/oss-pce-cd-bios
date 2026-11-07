"""Host checks for the Issue #234 slot $0B caller-contract ledger.

These tests check the ledger's rules and pin every row's side, status and value to a snapshot, so any
change to those fields fails regardless of its citation. They run no emulator, read no ROM and quote no
BIOS bytes. Passing them is not evidence of System Card behaviour.
"""
import json
import re
import sys
import unittest
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))

import reference_jsr_gate as gate  # noqa: E402

DOC = ROOT / 'docs' / 'research' / 'cd-dinfo-contract.md'

FIELDS = ['id', 'side', 'item', 'status', 'value', 'source', 'reason']
SIDES = {'caller', 'callee', 'reference', 'name'}
STATUSES = {'SOURCE_FACT', 'HYPOTHESIS', 'UNKNOWN', 'BLOCKED_ABI'}
FORBIDDEN_WORDS = ('VERIFIED', 'OBSERVED', 'PASS', 'DEV_CALLABLE')
# SOURCE_FACT must cite at least one primary source. S4 (no licence shown), S5 (name only) and S6 (repository
# inventory) are not a basis for any fact.
PRIMARY_SOURCES = {'S1', 'S2', 'S3'}

# Pinned snapshot of (side, status, value) for every ledger row. Changing any of them fails the ledger
# test until this snapshot is updated in the same change. Candidate entry and name values are not
# carried here; the ledger records declaration existence only and refers to the allowed ledger.
EXPECTED = {
    'F01': ('name', 'SOURCE_FACT', '公開 caller ソースに候補宣言が存在する（入口値の再掲なし）'),
    'F02': ('caller', 'UNKNOWN', 'UNKNOWN'),
    'F03': ('caller', 'SOURCE_FACT', '`0`'),
    'F04': ('caller', 'SOURCE_FACT', '`1`'),
    'F05': ('caller', 'SOURCE_FACT', '`2`'),
    'F06': ('caller', 'SOURCE_FACT', '`$20F8`（_al）、`$20F9`（_ah）'),
    'F07': ('callee', 'HYPOTHESIS', '下位 2 ビットのみ使用'),
    'F08': ('caller', 'SOURCE_FACT', '最初の呼出しは `tnomin`、以降は十進加算で増やした値'),
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
    'F29': ('name', 'UNKNOWN', 'UNKNOWN'),
    'F30': ('reference', 'UNKNOWN', 'UNKNOWN'),
    'F31': ('callee', 'UNKNOWN', 'UNKNOWN'),
    'F32': ('callee', 'UNKNOWN', 'UNKNOWN'),
    'F33': ('callee', 'UNKNOWN', 'UNKNOWN'),
}

# Status counts as the rows stand. Enforced exactly by test_status_counts.
EXPECTED_COUNTS = {'SOURCE_FACT': 9, 'HYPOTHESIS': 6, 'UNKNOWN': 18}

# Field coverage required by the Issue #234 acceptance list. Each keyword must appear in the item cell of
# the named row (token match, not a substring of the whole table).
REQUIRED_FIELD_ROWS = {
    'A': 'F19', 'X': 'F22', 'Y': 'F18', 'P': 'F23', '$20F8': 'F10', 'フラグ': 'F23', 'SP': 'F24',
    'MPR': 'F25', '副作用': 'F26', 'IRQ': 'F27', '失敗': 'F21', 'エラー': 'F20', '戻り': 'F18',
    '媒体なし': 'F31', '範囲外トラック': 'F32', '未対応の照会種別': 'F33',
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
            if re.search(rf'\b{word}\b', cell, re.IGNORECASE):
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


# Public-boundary scan for the rebuilt ledger (see Issue #234 section on the withdrawal reasons).
# It refuses private absolute paths, private-image filenames/byte dumps, raw disassembly, detailed
# code quotes, secondary-source reconstruction, and any new candidate-entry or candidate-name carrier
# outside the two allowed ledger files. Forbidden entry/name values are read at run time from the
# allowed ledger (slot record), never written here as literals. Positive control is the real ledger;
# every negative sample is built at run time from that value or from a fake value that does not match
# the slot record. Short-pattern checks are recorded as residual-risk heuristics only: they do not
# claim to fully detect arbitrary commercial-derived information. Known evasions not detected by
# these heuristics (recorded as residual risk): octal, binary or other-base encodings, full-width
# digits, and alternate internal spellings that differ by an inserted character. Hex expressions
# containing an entry-shaped value are covered by the shape check; decimal-arithmetic reconstruction
# is checked separately. Allowed source locators (file paths and allowed line ranges for primary
# sources) remain pointers to public sources; they are not entry/name values.
PRIVATE_ABS_RE = re.compile(r'(?<![\w/])/(?:home|Users|private)/')
PRIVATE_FILE_RE = re.compile(r'syscard3\.pce')
BYTE_DUMP_RE = re.compile(r'[0-9A-Fa-f]{64,}')
DISASM_MNEMONICS = ('LDA', 'LDB', 'STA', 'JSR', 'RTS', 'BNE', 'BEQ', 'TAX', 'TXA', 'PHA', 'PLA')
SECONDARY_RE = re.compile(r'(?:stum\.de|zeograd\.com)', re.IGNORECASE)
ENTRY_SHAPE_RE = re.compile(r'\$?0?x?E0[0-9A-Fa-f]{2}\b', re.IGNORECASE)
TITLE_NAME_RE = re.compile(r'\b[A-Za-z]{2,}_[A-Za-z0-9_]+\b')
CLAIM_WORD_RE = re.compile(r'\b(?:OBSERVED|VERIFIED)\b', re.IGNORECASE)
DEC_ARITH_RE = re.compile(r'(\d+)\s*\+\s*(\d+)')


def _slot_record():
    data = json.loads((ROOT / 'spec' / 'api_contracts.json').read_text(encoding='utf-8'))
    return next(e for e in data['apis'] if e.get('slot') == 11)


def _forbidden_forms():
    rec = _slot_record()
    entry = rec['candidate_entry']
    bare = entry[2:] if entry.lower().startswith('0x') else entry
    return {
        'dollar': '$' + bare,
        'hex': entry,
        'bare': bare,
        'decimal': str(int(entry, 16)),
        'name': rec['candidate_name'],
    }


def _has_token(haystack_lower, token_lower):
    pat = r'(?<![A-Za-z0-9])' + re.escape(token_lower) + r'(?![A-Za-z0-9])'
    return re.search(pat, haystack_lower) is not None


def boundary_problems(text, rows=None):
    """Return public-boundary violations in a ledger text. An empty list means the text is publishable."""
    out = []
    if PRIVATE_ABS_RE.search(text):
        out.append('private absolute path in ledger')
    if PRIVATE_FILE_RE.search(text):
        out.append('private image filename in ledger')
    if BYTE_DUMP_RE.search(text):
        out.append('byte dump / third-party hash in ledger')
    if SECONDARY_RE.search(text):
        out.append('secondary-source reconstruction in ledger')
    for lineno, line in enumerate(text.splitlines(), 1):
        if line.strip().startswith('|') and '%' not in line:
            pass
        hits = [m for m in DISASM_MNEMONICS if re.search(rf'\b{m}\b', line, re.IGNORECASE)]
        if len(hits) >= 2 and ('#' in line or '$' in line):
            out.append(f'raw disassembly-like line {lineno}')
            break
    if '```' in text:
        for block in text.split('```')[1::2]:
            if any(re.search(rf'\b{m}\b', block, re.IGNORECASE) for m in DISASM_MNEMONICS):
                out.append('detailed code quote in ledger')
                break
    forms = _forbidden_forms()
    lowered = text.lower()
    for label in ('dollar', 'hex', 'bare'):
        if _has_token(lowered, forms[label].lower()):
            out.append(f'candidate forbidden form ({label}) in ledger')
            break
    if _has_token(lowered, forms['name'].lower()):
        out.append('candidate forbidden form (name) in ledger')
    if re.search(r'(?<![0-9A-Za-z])' + re.escape(forms['decimal']) + r'(?![0-9A-Za-z])', text):
        out.append('candidate forbidden form (decimal) in ledger')
    squashed = lowered.replace(' ', '').replace('_', '').replace('-', '')
    if forms['bare'].lower() in squashed and not any(_has_token(lowered, forms[l].lower()) for l in ('dollar', 'hex', 'bare')):
        out.append('candidate forbidden form (bare-squashed) in ledger')
    if ENTRY_SHAPE_RE.search(text):
        out.append('candidate entry shape in ledger')
    entry_num = int(forms['hex'], 16)
    for m in DEC_ARITH_RE.finditer(text):
        if int(m.group(1)) + int(m.group(2)) == entry_num:
            out.append('candidate entry decimal arithmetic reconstruction in ledger')
            break
    first = text.splitlines()[0] if text.splitlines() else ''
    if TITLE_NAME_RE.search(first):
        out.append('candidate name shape in ledger title')
    for lineno, line in enumerate(text.splitlines(), 1):
        if line.strip().startswith('|'):
            continue
        if '使わない' in line or '禁止' in line:
            continue
        if CLAIM_WORD_RE.search(line):
            out.append(f'forbidden claim word in ledger prose line {lineno}')
            break
    if rows is None:
        try:
            rows = parse_rows(text)
        except ValueError:
            rows = []
    by_id = {r['id']: r for r in rows}
    if 'F01' in by_id:
        value = by_id['F01']['value']
        value_lower = value.lower()
        if any(_has_token(value_lower, t.lower()) for t in forms.values()) or ENTRY_SHAPE_RE.search(value):
            out.append('candidate entry in F01 value')
    if 'F29' in by_id:
        value = by_id['F29']['value']
        value_lower = value.lower()
        if ' / ' in value or TITLE_NAME_RE.search(value) or any(
                _has_token(value_lower, t.lower()) for t in forms.values()):
            out.append('candidate name correspondence in F29 value')
    section4_all = ' '.join(part.split('## 5.', 1)[0] for part in text.split('## 4.', 1)[1:])
    if re.search(r'\bS4\b|cdrom\.asm', section4_all, re.IGNORECASE):
        out.append('unlicensed source content comparison in section 4')
    return out


class RebuiltLedgerTest(unittest.TestCase):
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

    def test_caller_fact_does_not_carry_callee_format(self):
        row = {r['id']: r for r in self.rows}['F08']
        self.assertEqual(row['side'], 'caller')
        self.assertNotIn('BCD', row['value'])
        self.assertNotIn('BCD', row['reason'])

    def test_unknown_values_are_not_filled(self):
        unknown = [r for r in self.rows if r['status'] == 'UNKNOWN']
        self.assertTrue(unknown)
        for r in unknown:
            self.assertEqual(r['value'], 'UNKNOWN', r['id'])

    def test_f08_covers_first_call_and_increment(self):
        row = {r['id']: r for r in self.rows}['F08']
        self.assertIn('最初の呼出し', row['value'])
        self.assertIn('以降', row['value'])
        self.assertIn('1883–1891', row['source'])

    def test_f09_cites_author_comment_and_increment(self):
        row = {r['id']: r for r in self.rows}['F09']
        self.assertIn('S2 1842', row['source'])
        self.assertIn('1888–1889', row['source'])

    def test_f15_cites_only_type2_spec(self):
        row = {r['id']: r for r in self.rows}['F15']
        self.assertIn('S2 2005–2011', row['source'])
        self.assertNotIn('1574', row['source'])

    def test_s1_range_covers_ax_to_dh(self):
        self.assertIn('329–343（`_ax`〜`_dh`）', self.text)
        self.assertNotIn('330–343（`_al`〜`_dh`）', self.text)

    def test_source_pin_and_query_classification_present(self):
        self.assertIn('54f5c73606c1e7142e8095c727cab2767e2b3cb3', self.text)
        self.assertIn('https://github.com/pce-devel/huc', self.text)
        self.assertIn('include/hucc/pcengine.inc', self.text)
        by_id = {r['id']: r for r in self.rows}
        self.assertEqual(by_id['F03']['value'], '`0`')
        self.assertEqual(by_id['F04']['value'], '`1`')
        self.assertEqual(by_id['F05']['value'], '`2`')

    def test_conflict_and_not_run_stated(self):
        self.assertIn('CONFLICT', self.text)
        self.assertIn('NOT_RUN', self.text)
        self.assertIn('## 4. 出典間の食い違い', self.text)

    def test_f11_f12_consistent(self):
        by_id = {r['id']: r for r in self.rows}
        f11, f12 = by_id['F11'], by_id['F12']
        self.assertEqual((f11['status'], f12['status']), ('UNKNOWN', 'UNKNOWN'))
        self.assertNotIn('一致しない', f12['reason'])
        self.assertIn('決定できない', f12['reason'])
        self.assertIn('未確定', f12['reason'])
        section4 = self.text.split('## 4.', 1)[1].split('## 5.', 1)[0]
        line2 = [line for line in section4.splitlines() if line.strip().startswith('- 2.')][0]
        self.assertNotIn('CONFLICT', line2)
        self.assertIn('UNKNOWN', line2)

    def test_f17_f18_distinction(self):
        by_id = {r['id']: r for r in self.rows}
        f17, f18 = by_id['F17'], by_id['F18']
        self.assertEqual((f17['side'], f17['status']), ('caller', 'SOURCE_FACT'))
        self.assertEqual((f18['side'], f18['status']), ('callee', 'HYPOTHESIS'))
        self.assertIn('未実測', f18['reason'])
        self.assertIn('直接の矛盾は確認できない', f18['reason'])
        section4 = self.text.split('## 4.', 1)[1].split('## 5.', 1)[0]
        line1 = [line for line in section4.splitlines() if line.strip().startswith('- 1.')][0]
        self.assertNotIn('CONFLICT', line1)
        self.assertIn('UNKNOWN', line1)

    def test_section4_has_no_unfounded_conflict(self):
        section4 = self.text.split('## 4.', 1)[1].split('## 5.', 1)[0]
        self.assertNotIn('CONFLICT', section4)
        self.assertIn('UNKNOWN', section4)


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
        rows = self._mutate('F03', source='S4 218–226')
        self.assertTrue(any(p.startswith('F03:') for p in problems(rows)))

    def test_name_only_source_is_rejected(self):
        rows = self._mutate('F01', source='S5')
        self.assertTrue(any(p.startswith('F01:') for p in problems(rows)))

    def test_pinned_value_change_is_rejected(self):
        rows = self._mutate('F13', value='+0 は最小トラック（2 進）')
        self.assertTrue(any(p.startswith('F13:') for p in pin_problems(rows)))

    def test_unknown_filled_with_guess_is_rejected(self):
        rows = self._mutate('F31', value='非 0', source='S2 2014')
        self.assertTrue(any('UNKNOWN row with a filled value' in p for p in problems(rows)))


class PublicBoundaryTest(unittest.TestCase):
    """The rebuilt ledger must not reintroduce withdrawal reasons; synthetic bad text must be caught."""

    @classmethod
    def setUpClass(cls):
        cls.text = DOC.read_text(encoding='utf-8')

    def test_real_ledger_passes_public_boundary(self):
        self.assertEqual(boundary_problems(self.text), [])

    def test_rejects_private_absolute_path(self):
        self.assertTrue(boundary_problems('/home/synthetic/private/rom.bin'))

    def test_rejects_private_image_filename(self):
        self.assertTrue(boundary_problems('loaded syscard3.pce for inspection'))

    def test_rejects_byte_dump(self):
        self.assertTrue(boundary_problems('A0' * 32))

    def test_rejects_raw_disassembly(self):
        self.assertTrue(boundary_problems('LDA #$00; STA $20F8; RTS'))

    def test_rejects_detailed_code_quote(self):
        self.assertTrue(boundary_problems('```asm\nLDA #$00\nJSR $E999\nRTS\n```'))

    def test_rejects_secondary_reconstruction(self):
        self.assertTrue(boundary_problems('see https://www.stum.de/x lines 100–200'))

    def test_rejects_observed_and_verified_in_tables(self):
        self.assertTrue(problems([], ['OBSERVED']))
        self.assertTrue(problems([], ['VERIFIED']))

    def test_rejects_candidate_entry_in_markdown(self):
        token = _forbidden_forms()['dollar']
        self.assertTrue(boundary_problems(f'# title\n\nvalue {token}\n'))

    def test_rejects_candidate_entry_shape_with_fake_value(self):
        self.assertTrue(boundary_problems('# title\n\nvalue $E0AB\n'))

    def test_rejects_candidate_entry_in_f01_value(self):
        token = _forbidden_forms()['dollar']
        rows = parse_rows(self.text)
        for r in rows:
            if r['id'] == 'F01':
                r['value'] = token
        self.assertTrue(any('F01' in p for p in boundary_problems(self.text, rows)))

    def test_rejects_candidate_name_in_title(self):
        token = _forbidden_forms()['name']
        self.assertTrue(boundary_problems(f'# {token} title\n\nbody\n'))

    def test_rejects_name_shape_in_title_with_fake_value(self):
        self.assertTrue(boundary_problems('# SYNTH_NAME title\n\nbody\n'))

    def test_rejects_name_correspondence_in_f29_value(self):
        rows = parse_rows(self.text)
        for r in rows:
            if r['id'] == 'F29':
                r['value'] = 'SYNTH_A / SYNTH_B'
        self.assertTrue(any('F29' in p for p in boundary_problems(self.text, rows)))

    def test_rejects_entry_alternative_notation(self):
        decimal = _forbidden_forms()['decimal']
        self.assertTrue(boundary_problems(f'# title\n\ndecimal {decimal}\n'))
        self.assertTrue(boundary_problems('# title\n\nformula $E000 + 3 * 11\n'))
        entry_num = int(_forbidden_forms()['hex'], 16)
        self.assertTrue(boundary_problems(f'# title\n\npieces {entry_num - 33} + 33\n'))

    def test_rejects_prose_claim_word(self):
        self.assertTrue(boundary_problems('# title\n\nObserved: returned value noted\n'))
        self.assertTrue(boundary_problems('# title\n\nVerified by run\n'))

    def test_rejects_lowercase_claim_word_in_table_cell(self):
        self.assertTrue(problems([], ['observed']))
        self.assertTrue(problems([], ['verified']))

    def test_rejects_unlicensed_source_as_fact_basis(self):
        rows = parse_rows(self.text)
        for r in rows:
            if r['id'] == 'F03':
                r['source'] = 'S4 1–2'
        self.assertTrue(any('F03' in p for p in problems(rows)))

    def test_rejects_unlicensed_content_in_conflict_section(self):
        bad = '# title\n\n## 4. 出典間の食い違い\n\n- S4 content comparison here\n\n## 5. rest\n'
        self.assertTrue(boundary_problems(bad))

    def test_rejects_observed_promotion_of_fact_row(self):
        rows = parse_rows(self.text)
        for r in rows:
            if r['id'] == 'F03':
                r['status'] = 'OBSERVED'
        self.assertTrue(any('unknown status' in p for p in problems(rows)))
        self.assertTrue(any('F03' in p for p in pin_problems(rows)))


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


class RealGateState(unittest.TestCase):
    """Slot 11: the release state is checked on the real gate."""

    def test_the_real_gate_keeps_the_slot_blocked_and_not_released(self):
        ledger, contracts = gate.load()
        record = gate.resolve(11, ledger, contracts)
        self.assertEqual(record['execution'], 'BLOCKED')
        self.assertIs(record['release_eligible'], False)


if __name__ == '__main__':
    unittest.main()
