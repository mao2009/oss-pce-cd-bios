"""Host checks for the Issue #229 CD_STAT ($09, $E01B) caller-contract ledger.

Checks the ledger's rules and pins each row's side, status and value, so a change fails until the pin is
updated. No emulator, no ROM, no BIOS bytes. Passing these is not evidence of System Card behaviour.
"""
import re
import unittest
from pathlib import Path

DOC = Path(__file__).resolve().parents[2] / 'docs' / 'research' / 'cd-stat-contract.md'
FIELDS = ['id', 'side', 'item', 'status', 'value', 'source', 'reason']
SIDES = {'caller', 'callee', 'reference', 'name'}
STATUSES = {'SOURCE_FACT', 'HYPOTHESIS', 'UNKNOWN', 'BLOCKED_ABI'}
FORBIDDEN = ('VERIFIED', 'OBSERVED', 'PASS', 'DEV_CALLABLE')
REFERENCE_ONLY = {'S6'}  # Hu-Go HLE: never a basis for SOURCE_FACT.

# Pinned (side, status, value) per row.
EXPECTED = {
    'F01': ('caller', 'SOURCE_FACT', '`$1B`（JSR 先 `$E01B`）'),
    'F02': ('caller', 'SOURCE_FACT', '`$00`'),
    'F03': ('caller', 'SOURCE_FACT', '`$01`'),
    'F04': ('callee', 'HYPOTHESIS', '`$00` を busy 確認、非 0 を ready 確認とみなす'),
    'F05': ('callee', 'UNKNOWN', 'UNKNOWN'),
    'F06': ('callee', 'UNKNOWN', 'UNKNOWN'),
    'F07': ('caller', 'SOURCE_FACT', '呼出側は書き込まない'),
    'F08': ('callee', 'UNKNOWN', 'UNKNOWN'),
    'F09': ('caller', 'SOURCE_FACT', 'A≠0 で待機を続け、A=0 で次へ進む'),
    'F10': ('caller', 'SOURCE_FACT', 'A=0 で ready へ進み、非 0 で約 1 秒待って再試行する'),
    'F11': ('callee', 'HYPOTHESIS', '`$00` を非 busy とみなす'),
    'F12': ('callee', 'HYPOTHESIS', '`$00` を ready、非 0 を未 ready またはエラーとみなす'),
    'F13': ('callee', 'UNKNOWN', 'UNKNOWN'),
    'F14': ('caller', 'SOURCE_FACT', 'N/Z は復元された戻り A で決まる'),
    'F15': ('caller', 'SOURCE_FACT', 'ラッパは戻り後に Y を `$00` にする'),
    'F16': ('callee', 'UNKNOWN', 'UNKNOWN'),
    'F17': ('callee', 'HYPOTHESIS', '呼出先は RTS で `call_bios` へ戻る'),
    'F18': ('caller', 'SOURCE_FACT', '呼出し中 MPR7=`$00`、戻り時に呼出側の値へ復元'),
    'F19': ('callee', 'UNKNOWN', 'UNKNOWN'),
    'F20': ('callee', 'UNKNOWN', 'UNKNOWN'),
    'F21': ('caller', 'SOURCE_FACT', 'S5 681–691 の範囲に IRQ マスクの操作はない'),
    'F22': ('callee', 'UNKNOWN', 'UNKNOWN'),
    'F23': ('caller', 'SOURCE_FACT', '呼出側は reset 後に busy を待ち、ready を待つ。媒体なしを区別する分岐はない'),
    'F24': ('callee', 'UNKNOWN', 'UNKNOWN'),
    'F25': ('callee', 'UNKNOWN', 'UNKNOWN'),
    'F26': ('reference', 'HYPOTHESIS', 'A=0 を設定して N/Z を更新し、mode 入力は読まない（HLE）'),
    'F27': ('name', 'SOURCE_FACT', 'HuC `cd_stat`、Hu-Go `CD_STAT`、本リポジトリ `CD_STAT`'),
}
COUNTS = {'SOURCE_FACT': 12, 'HYPOTHESIS': 5, 'UNKNOWN': 10}
# Keyword -> row whose item cell must contain it (Issue #229 field list).
REQUIRED = {'Y': 'F01', 'A': 'F02', 'X': 'F05', 'P': 'F06', '$20F8': 'F07', 'フラグ': 'F16', 'SP': 'F17',
            'MPR': 'F19', '副作用': 'F20', 'IRQ': 'F22', '媒体': 'F23', '失敗': 'F25'}


def parse_rows(text):
    rows = []
    for line in text.splitlines():
        if re.match(r'^\|\s*F\d{2}\s*\|', line.strip()):
            cells = [c.strip() for c in line.strip().strip('|').split('|')]
            if len(cells) != len(FIELDS):
                raise ValueError(f'malformed row: {cells[0]}')
            rows.append(dict(zip(FIELDS, cells)))
    return rows


def problems(rows, text=''):
    """Rule violations plus pin differences. An empty list means the ledger is accepted."""
    out = []
    defined = set(re.findall(r'^\|\s*(S\d+)\s*\|', text, re.M))  # ids in the 出典 table
    for r in rows:
        rid, side, status, src = r['id'], r['side'], r['status'], r['source']
        if side not in SIDES or status not in STATUSES:
            out.append(f'{rid}: bad side/status {side!r}/{status!r}')
        unknown = sorted(set(re.findall(r'S\d+', src)) - defined)
        if unknown:
            out.append(f'{rid}: unknown source id {unknown}')
        if status == 'HYPOTHESIS' and '未確認' not in r['reason']:
            out.append(f'{rid}: HYPOTHESIS without 未確認 marker')
        if side == 'callee' and status == 'SOURCE_FACT':
            out.append(f'{rid}: callee fact promoted to SOURCE_FACT')
        if status == 'SOURCE_FACT':
            if src in {'', '—', '-'}:
                out.append(f'{rid}: SOURCE_FACT without a source')
            elif all(s in REFERENCE_ONLY for s in re.findall(r'S\d+', src)):
                out.append(f'{rid}: SOURCE_FACT cites only reference sources')
        if status == 'UNKNOWN' and r['value'].strip('`') != 'UNKNOWN':
            out.append(f'{rid}: UNKNOWN row with a filled value')
    for word in FORBIDDEN:
        for line in text.splitlines():
            if line.strip().startswith('|') and re.search(rf'\b{word}\b', line):
                out.append(f'forbidden word {word!r} in a table row')
    ids = [r['id'] for r in rows]
    out += [f'{rid}: duplicate row' for rid in set(i for i in ids if ids.count(i) > 1)]
    for r in rows:
        if r['id'] not in EXPECTED:
            out.append(f"{r['id']}: row not in the pinned snapshot")
            continue
        pin = EXPECTED[r['id']]
        got = (r['side'], r['status'], r['value'])
        if got != pin:
            out.append(f'{r["id"]}: {got} differs from pinned {pin}')
    out += [f'{rid}: pinned row missing' for rid in EXPECTED if rid not in ids]
    return out


class CdStatLedgerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = DOC.read_text(encoding='utf-8')
        cls.rows = parse_rows(cls.text)

    def test_ledger_accepted(self):
        self.assertEqual(problems(self.rows, self.text), [])

    def test_status_counts(self):
        got = {s: sum(r['status'] == s for r in self.rows) for s in COUNTS}
        self.assertEqual(got, COUNTS)

    def test_required_fields_present(self):
        byid = {r['id']: r for r in self.rows}
        for kw, rid in REQUIRED.items():
            self.assertRegex(byid[rid]['item'], rf'(?<![A-Za-z]){re.escape(kw)}(?![A-Za-z])', kw)

    def test_release_stays_closed(self):
        for phrase in ('BLOCKED_ABI', 'release_eligible=false', 'DEV_CALLABLE にしない'):
            self.assertIn(phrase, self.text)

    def test_rejects_bad_rows(self):
        base = self.rows[0]
        self.assertTrue(any('promoted' in p for p in problems([dict(base, id='F01', side='callee')], self.text)))
        self.assertTrue(any('without a source' in p for p in problems([dict(base, source='—')], self.text)))
        self.assertTrue(any('reference sources' in p for p in problems([dict(base, source='S6 591–600')], self.text)))
        self.assertTrue(any('missing' in p for p in problems(self.rows[1:], self.text)))
        self.assertTrue(any('forbidden' in p for p in problems(self.rows, self.text + '\n| x | VERIFIED |\n')))
        self.assertTrue(any('unknown source' in p for p in problems([dict(base, source='S9 1–2')], self.text)))
        self.assertTrue(any('unknown source' in p for p in problems([dict(base, source='S1 477、S7 1')], self.text)))
        hyp = dict(base, status='HYPOTHESIS', reason='根拠のみ')
        self.assertTrue(any('without 未確認' in p for p in problems([hyp], self.text)))


if __name__ == '__main__':
    unittest.main()
