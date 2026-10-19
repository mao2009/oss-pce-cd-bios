"""Host tests for the Issue #297 AD_TRANS ($11, $E033) caller-ABI evidence ledger.

These tests check the ledger's rules only: the exact row set, the status vocabulary, no
VERIFIED/OBSERVED/PASS/DEV_CALLABLE wording in the table, callee rows never promoted from caller
source, UNKNOWN/BLOCKED values kept as UNKNOWN, and cited source ids. No emulator, ROM or System
Card code is involved. Passing these tests is NOT evidence of BIOS behaviour.
"""
import re
import unittest
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / 'docs' / 'research' / 'ad-trans-contract.md'

EXPECTED_SIDES = {
    **{f'K{i}': 'K' for i in range(1, 15)},
    **{f'C{i}': 'C' for i in range(1, 17)},
    **{f'R{i}': 'R' for i in range(1, 6)},
}
# Every row's status and value are pinned: a ledger change must update this table on purpose.
PINNED_ROWS = {
    'K1': ('SOURCE_FACT', '呼出側が目標 `$E033` を設定する。マクロは `ldy #<ad_trans`（Y=`$33`）の後に `call_bios` を呼び、`call_bios` が `jsr $E000` の下位を Y で書き換える。MPR7 を `$00` に切替え、戻りで保存値に戻す'),
    'K2': ('SOURCE_FACT', '`get_file_lba` がファイル先頭 LBA を書き（`_cl` は、ファイル番号が `iso_128mb` 以上なら 1、それ以外は 0）、`_add_sectors` が sect_offset を 24 bit で加算する。入口の `_cl` はファイル番号ではなく LBA の上位バイト'),
    'K3': ('SOURCE_FACT', '`ldx <_cl` でファイル番号を X に入れる。`get_file_info` と以後の命令は X を変えない。入口の X はファイル番号'),
    'K4': ('SOURCE_FACT', '`_add_sectors` の前に Y を使うが、`system` が Y を `$33` に設定する。`call_bios` は Y を変えない。入口の Y は `$33`'),
    'K5': ('SOURCE_FACT', '`lda <_dh` の値（元の nb_sectors）が `beq` と `sta` の後も残り、`call_bios` の `pha`/`pla` で保存されて入口の A になる'),
    'K6': ('SOURCE_FACT', 'マクロは P を明示的に設定しない。入口の P は直前の演算（`beq`、`pla` など）の結果で、呼出側は意味のある値を渡さない'),
    'K7': ('SOURCE_FACT', '`_dh` が 0 のとき `get_file_info` が書いたファイル長の下位 8 bit を使う。`_dh` が非 0 のときは `_dh` の値を `_al` に書く。`_ah`（上位）も呼出側が設定する。呼出側が書くのは `_al` と `_ah` で、BIOS がどちらを読むかは本行の範囲外'),
    'K8': ('SOURCE_FACT', 'マクロは `system` の前に `_dh` を 0 にする（`stz <_dh`）。宣言は `nb_sectors<_dh>` として `_dh` を引数に取る'),
    'K9': ('SOURCE_FACT', 'マクロは `_bx` を設定も変更もしない。呼出側が事前に設定する前提（宣言 `ad_addr<_bx>`）。`get_file_info` と `_add_sectors` は `_bx` を使わない'),
    'K10': ('SOURCE_FACT', 'マクロが書く番地: `$20F8`（`_al`）、`$20F9`（`_ah`）、`$20FC`〜`$20FE`（`_cl`、`_ch`、`_dl`）、`$20FF`（`_dh` を 0 に）。書かない番地: `$20FA`〜`$20FB`（`_bx`）'),
    'K11': ('SOURCE_FACT', 'trampoline は MPR7 を `$00` にして呼び、戻りで保存値を復元する。`call_bios` は A を 1 バイト push し、`jsr` が戻り先を積む'),
    'K12': ('SOURCE_FACT', 'マクロは SEI/CLI を使わず、割込み関連の作業 RAM を操作しない。戻りの後に待ちや再試行はなく、すぐ `cly` を実行する'),
    'K13': ('SOURCE_FACT', 'マクロは `cly`（Y のみ）以外で A を変えず、戻りの A をそのまま残す。宣言は `unsigned char`。A の判定は本マクロにない'),
    'K14': ('SOURCE_FACT', '`get_file_info` は `iso_dirlo` / `iso_dirhi` の表を X で添字参照し、範囲の検査がない'),
    'C1': ('HYPOTHESIS', '`$00:E033`（呼出し時は `$E000 + 3 × $11`）'),
    'C2': ('HYPOTHESIS', 'S7 は `cl` = blockadr >> 16、`ch` = blockadr >> 8、`dl` = blockadr と記す'),
    'C3': ('HYPOTHESIS', 'S7 は「読むブロック数」と記す。0 の意味、8 bit の上限、`_ah` の扱いは記載なし'),
    'C4': ('UNKNOWN', 'UNKNOWN'),
    'C5': ('HYPOTHESIS', 'S7 は宛先のバッファアドレスと記す'),
    'C6': ('UNKNOWN', 'UNKNOWN'),
    'C7': ('UNKNOWN', 'UNKNOWN'),
    'C8': ('HYPOTHESIS', 'S7 は A をエラー状態とし、`$04` を「エラー、ADPCM busy」と記す'),
    'C9': ('UNKNOWN', 'UNKNOWN'),
    'C10': ('UNKNOWN', 'UNKNOWN'),
    'C11': ('UNKNOWN', 'UNKNOWN'),
    'C12': ('UNKNOWN', 'UNKNOWN'),
    'C13': ('UNKNOWN', 'UNKNOWN'),
    'C14': ('HYPOTHESIS', '戻りの時点で転送が完了している'),
    'C15': ('UNKNOWN', 'UNKNOWN'),
    'C16': ('UNKNOWN', 'UNKNOWN'),
    'R1': ('UNKNOWN', 'UNKNOWN'),
    'R2': ('HYPOTHESIS', '同じ 24 bit のブロック表現を使う'),
    'R3': ('UNKNOWN', 'UNKNOWN'),
    'R4': ('UNKNOWN', 'UNKNOWN'),
    'R5': ('BLOCKED', 'UNKNOWN'),
}
# Source and reason cells of rows whose wording is a known conflict: a resolution in prose must fail.
PINNED_CELLS = {
    'C6': {
        'source': 'S7（dh が偽なら宛先を設定しない、真なら bx に設定する）、S1 493–498（HuCC は dh を 0 にして bx を渡す）',
        'reason': '二つの記述は両立しない（矛盾）。S7 は RE 由来である。dh = 0 の呼出しで宛先が更新されるか、前回の宛先が残るかは未確定。宛先の副作用に直結する',
    },
}
FIELDS = ['id', 'side', 'item', 'status', 'value', 'source', 'reason', 'test']
STATUSES = {'SOURCE_FACT', 'HYPOTHESIS', 'UNKNOWN', 'BLOCKED'}
CALLEE_STATUSES = {'HYPOTHESIS', 'UNKNOWN', 'BLOCKED'}
NEEDS_SOURCE = {'SOURCE_FACT', 'HYPOTHESIS'}
FORBIDDEN = ('VERIFIED', 'OBSERVED', 'PASS', 'DEV_CALLABLE')
SOURCE_ID_RE = re.compile(r'\bS\d+\b')
SECTION3_RE = re.compile(r'^## 3\..*?$(.*?)(?=^## |\Z)', re.M | re.S)


def parse_ledger(text):
    """Return (item rows, defined source ids, malformed §3 lines)."""
    defined = set()
    for line in text.splitlines():
        s = line.strip()
        if s.startswith('|'):
            first = s.strip('|').split('|')[0].strip()
            if re.fullmatch(r'S\d+', first):
                defined.add(first)
    m = SECTION3_RE.search(text)
    items, malformed = [], []
    for line in (m.group(1) if m else '').splitlines():
        s = line.strip()
        if not s:
            continue
        if not s.startswith('|') or not s.endswith('|'):
            malformed.append(s)
            continue
        cells = [c.strip() for c in s.strip('|').split('|')]
        if cells[0] == 'ID' or set(s) <= set('|-: '):
            continue
        if len(cells) == len(FIELDS):
            items.append(dict(zip(FIELDS, cells)))
        else:
            malformed.append(s)
    return items, defined, malformed


def ledger_problems(items, defined, malformed=()):
    """Return rule violations; an empty list means the ledger passes every rule here."""
    problems = [f'malformed row: {m[:40]}' for m in malformed]
    counts = Counter(r['id'] for r in items)
    problems += [f'duplicate {i} x{n}' for i, n in counts.items() if n > 1]
    problems += [f'missing {i}' for i in EXPECTED_SIDES if i not in counts]
    problems += [f'unexpected {i}' for i in counts if i not in EXPECTED_SIDES]
    for r in items:
        rid, side, status, value = r['id'], r['side'], r['status'], r['value']
        if rid in EXPECTED_SIDES and side != EXPECTED_SIDES[rid]:
            problems.append(f'side {rid}={side}')
        if status not in STATUSES:
            problems.append(f'status {rid}={status}')
            continue
        if rid in PINNED_ROWS and (status, value) != PINNED_ROWS[rid]:
            problems.append(f'{rid} differs from its pinned status/value')
        for cell, text in PINNED_CELLS.get(rid, {}).items():
            if r[cell] != text:
                problems.append(f'{rid} {cell} differs from its pinned text')
        if side in ('C', 'R') and status not in CALLEE_STATUSES:
            problems.append(f'{side} row {rid} promoted to {status}')
        if status in ('UNKNOWN', 'BLOCKED') and value != 'UNKNOWN':
            problems.append(f'{rid} value {value!r} must be UNKNOWN')
        if status in NEEDS_SOURCE:
            cited = set(SOURCE_ID_RE.findall(r['source']))
            if not cited:
                problems.append(f'{rid} cites no source')
            problems += [f'{rid} cites undefined {s}' for s in cited - defined]
        for word in FORBIDDEN:
            if any(word in cell for cell in (r['status'], r['value'], r['source'], r['reason'], r['test'])):
                problems.append(f'{rid} contains forbidden word {word}')
    return problems


TEXT = DOC.read_text(encoding='utf-8')


class LedgerRuleTests(unittest.TestCase):
    def test_document_keeps_release_flag_false(self):
        self.assertIn('release_eligible=false', TEXT)

    def test_rows_are_exactly_the_expected_ids_and_no_rule_is_broken(self):
        items, defined, malformed = parse_ledger(TEXT)
        self.assertEqual(len(EXPECTED_SIDES), 35)
        self.assertEqual(set(PINNED_ROWS), set(EXPECTED_SIDES))
        self.assertEqual(ledger_problems(items, defined, malformed), [])
        self.assertEqual(len(items), 35)

    def test_c1_entry_is_hypothesis_and_names_e033(self):
        items, _, _ = parse_ledger(TEXT)
        c1 = [r for r in items if r['id'] == 'C1']
        self.assertEqual(c1[0]['status'], 'HYPOTHESIS')
        self.assertIn('E033', c1[0]['value'])

    def test_no_callee_row_is_source_fact(self):
        items, _, _ = parse_ledger(TEXT)
        self.assertFalse([r['id'] for r in items if r['side'] != 'K' and r['status'] == 'SOURCE_FACT'])


class LedgerMutationTests(unittest.TestCase):
    """Each mutated copy of the ledger must be rejected by the rules above."""

    def _problems_for(self, old, new):
        self.assertIn(old, TEXT, 'mutation target not found in ledger')
        return ledger_problems(*parse_ledger(TEXT.replace(old, new, 1)))

    def _row(self, rid):
        return next(line for line in TEXT.splitlines() if line.startswith(f'| {rid} |'))

    def test_promoting_callee_unknown_to_source_fact_fails(self):
        row = self._row('C4')
        new = row.replace('| UNKNOWN | UNKNOWN |', '| SOURCE_FACT | UNKNOWN |', 1)
        self.assertNotEqual(row, new)
        self.assertTrue(self._problems_for(row, new))

    def test_filling_an_unknown_value_fails(self):
        row = self._row('C6')
        new = row.replace('| UNKNOWN | UNKNOWN |', '| UNKNOWN | 2 |', 1)
        self.assertNotEqual(row, new)
        self.assertTrue(self._problems_for(row, new))

    def test_verified_wording_in_table_fails(self):
        row = self._row('C14')
        new = row.replace('戻りの時点で転送が完了している', 'VERIFIED: 戻りの時点で転送が完了している')
        self.assertNotEqual(row, new)
        self.assertTrue(self._problems_for(row, new))

    def test_removed_row_fails(self):
        row = self._row('R5')
        self.assertTrue(self._problems_for(row + '\n', ''))

    def test_c6_promoted_to_hypothesis_fails(self):
        # Regression: C6 (dh, source conflict) must not change status silently.
        row = self._row('C6')
        new = row.replace('| UNKNOWN | UNKNOWN |', '| HYPOTHESIS | UNKNOWN |', 1)
        self.assertNotEqual(row, new)
        self.assertTrue(self._problems_for(row, new))

    def test_c6_reason_resolved_in_prose_fails(self):
        # Regression: resolving the C6 conflict in the reason cell must fail without a status change.
        row = self._row('C6')
        new = row.replace(PINNED_CELLS['C6']['reason'], '_dh=0 で宛先は更新されない', 1)
        self.assertNotEqual(row, new)
        self.assertTrue(self._problems_for(row, new))

    def test_hypothesis_without_source_fails(self):
        row = self._row('R2')
        new = row.replace('| S1、S3、S7 |', '| なし |', 1)
        self.assertNotEqual(row, new)
        self.assertTrue(self._problems_for(row, new))


if __name__ == '__main__':
    unittest.main()
