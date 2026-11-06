"""Host checks for the Issue #264 CD_CONTNTS ($0C, $E024) caller-contract ledger.

These tests check the ledger's rules and pin every row's side, status and value to a snapshot, so any
change to those fields fails regardless of its citation. They run no emulator, read no ROM and quote no
BIOS bytes. Passing them is not evidence of System Card behaviour.
"""
import re
import sys
import unittest
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))

import reference_jsr_gate as gate  # noqa: E402

FIELDS = ['id', 'side', 'item', 'status', 'value', 'source', 'reason']
SIDES = {'caller', 'callee', 'reference', 'name'}
STATUSES = {'SOURCE_FACT', 'HYPOTHESIS', 'UNKNOWN', 'BLOCKED_ABI'}
FORBIDDEN_WORDS = ('VERIFIED', 'OBSERVED', 'PASS', 'DEV_CALLABLE')
# Summary sources (S6, S7) and PENDING are not a basis for SOURCE_FACT on any side.
SUMMARY_SOURCES = {'S6', 'S7'}

# Pinned snapshot of (side, status, value) for every ledger row. Changing any of them fails the ledger
# test until this snapshot is updated in the same change.
EXPECTED = {
    'F01': ('caller', 'SOURCE_FACT', '`$24`（JSR 先 `$E024`）'),
    'F02': ('caller', 'SOURCE_FACT', '`0`'),
    'F03': ('callee', 'UNKNOWN', 'UNKNOWN'),
    'F04': ('callee', 'UNKNOWN', 'UNKNOWN'),
    'F05': ('callee', 'UNKNOWN', 'UNKNOWN'),
    'F06': ('callee', 'UNKNOWN', 'UNKNOWN'),
    'F07': ('callee', 'UNKNOWN', 'UNKNOWN'),
    'F08': ('caller', 'SOURCE_FACT', 'A≠0 で `cd_reset` へ戻る、A=0 で TOC 取得済みとして進む'),
    'F09': ('callee', 'HYPOTHESIS', '0 を成功とみなす'),
    'F10': ('callee', 'UNKNOWN', 'UNKNOWN'),
    'F11': ('caller', 'SOURCE_FACT', 'N/Z は復元された戻り A で決まる'),
    'F12': ('callee', 'UNKNOWN', 'UNKNOWN'),
    'F13': ('callee', 'HYPOTHESIS', '呼出先は RTS で `call_bios` へ戻る'),
    'F14': ('caller', 'SOURCE_FACT', '呼出し中 MPR7=`$00`、戻り時に呼出側の値へ復元'),
    'F15': ('callee', 'UNKNOWN', 'UNKNOWN'),
    'F16': ('callee', 'UNKNOWN', 'UNKNOWN'),
    'F17': ('callee', 'UNKNOWN', 'UNKNOWN'),
    'F18': ('caller', 'SOURCE_FACT', '呼出側は `cd_stat` の戻りを busy・ready の分岐に使い、待機してから呼ぶ'),
    'F19': ('callee', 'UNKNOWN', 'UNKNOWN'),
    'F20': ('caller', 'SOURCE_FACT', '非 0 で `cd_reset` に戻り、待機からやり直す'),
    'F21': ('callee', 'UNKNOWN', 'UNKNOWN'),
    'F22': ('name', 'SOURCE_FACT', 'HuC `cd_contnts`、Hu-Go `CD_CONTENTS`、本リポジトリ `CD_CONTNTS`'),
    'F23': ('callee', 'HYPOTHESIS', '所在は本書では記録しない'),
    'F24': ('reference', 'HYPOTHESIS', '名前定義のみ。`default:` で未処理扱い（元のバイト列へ戻す、dispatcher の挙動）'),
    'F25': ('reference', 'UNKNOWN', 'UNKNOWN'),
}

# Status counts as the rows stand. Enforced exactly by test_status_counts.
EXPECTED_COUNTS = {'SOURCE_FACT': 8, 'HYPOTHESIS': 4, 'UNKNOWN': 13}

# Field coverage required by the Issue #264 acceptance list. Each keyword must appear in the item cell of
# the named row (exact row, not a substring of the whole table).
REQUIRED_FIELD_ROWS = {
    'Y': 'F01', 'A': 'F03', 'X': 'F04', 'P': 'F05', '$20F8': 'F06', 'フラグ': 'F12', 'SP': 'F13',
    'MPR': 'F15', '副作用': 'F16', 'IRQ': 'F17', '失敗': 'F21',
}


def token_in(kw, text):
    """True if kw occurs as a whole token: not adjacent to an ASCII letter (so 'A' does not match 'Abc', 'MPR' matches 'MPR0–6')."""
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
        if status == 'SOURCE_FACT' and not any(i not in SUMMARY_SOURCES for i in re.findall(r'S\d+', source)):
            out.append(f'{rid}: SOURCE_FACT cites only summary or PENDING sources {source!r}')
        if status == 'UNKNOWN' and value.strip('`') != 'UNKNOWN':
            out.append(f'{rid}: UNKNOWN row with a filled value {value!r}')
        if side == 'callee' and status not in {'HYPOTHESIS', 'UNKNOWN', 'BLOCKED_ABI'}:
            out.append(f'{rid}: callee row with status {status!r}')
    for cell in cells_text:
        for word in FORBIDDEN_WORDS:
            if re.search(rf'\b{word}\b', cell):
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


class RuleCheckerNegativeTest(unittest.TestCase):
    """The checker itself must reject promotion; these synthetic rows are not ledger data."""

    def _row(self, **kw):
        base = dict(id='F90', side='caller', item='x', status='SOURCE_FACT', value='v',
                    source='S1 1', reason='r')
        base.update(kw)
        return base

    def test_rejects_callee_source_fact(self):
        self.assertTrue(problems([self._row(side='callee')]))

    def test_rejects_source_fact_without_source(self):
        self.assertTrue(problems([self._row(source='—')]))

    def test_rejects_filled_unknown(self):
        self.assertTrue(problems([self._row(status='UNKNOWN', value='0')]))

    def test_rejects_source_fact_from_summary_only(self):
        self.assertTrue(problems([self._row(side='reference', source='S6')]))
        self.assertTrue(problems([self._row(side='caller', source='S7 表')]))

    def test_accepts_source_fact_with_primary_and_summary(self):
        self.assertEqual(problems([self._row(side='reference', source='S1 480、S6')]), [])

    def test_rejects_forbidden_status_word(self):
        self.assertTrue(problems([], ['VERIFIED']))
        self.assertTrue(problems([], ['PASS']))

    def test_accepts_honest_callee_unknown(self):
        self.assertEqual(problems([self._row(side='callee', status='UNKNOWN', value='UNKNOWN',
                                             source='—')]), [])


class RealGateState(unittest.TestCase):
    """Slot 12: the release state is checked on the real gate, since the research document is withdrawn."""

    def test_the_real_gate_keeps_the_slot_blocked_and_not_released(self):
        ledger, contracts = gate.load()
        record = gate.resolve(12, ledger, contracts)
        self.assertEqual(record['execution'], 'BLOCKED')
        self.assertIs(record['release_eligible'], False)


if __name__ == '__main__':
    unittest.main()
