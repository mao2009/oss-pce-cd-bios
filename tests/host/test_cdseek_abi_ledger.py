"""Host tests for the Issue #214 CD_SEEK ($04, $E00C) caller-ABI ledger.

These tests check the ledger's rules (status vocabulary, no VERIFIED/OBSERVED, callee rows never
promoted from caller source, UNKNOWN values never filled) and one pure fact quoted from the
pinned HuCC source: the cd_seek equate. No emulator, no ROM and no System Card code is involved,
and the table arithmetic is NOT evidence of BIOS behaviour.

外部由来の LBA 表は本ファイルに埋め込まない。表の算術の検査は合成値で行い、固定版での一回限りの
確認結果は docs/cd-seek-abi-ledger.md §4 に残す。
"""
import re
import sys
import unittest
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

STATUSES = {'SOURCE_FACT', 'CALLER_INFERENCE', 'HYPOTHESIS', 'UNKNOWN', 'BLOCKED'}
CALLEE_STATUSES = {'HYPOTHESIS', 'UNKNOWN', 'BLOCKED'}
FORBIDDEN = {'VERIFIED', 'OBSERVED'}
NEEDS_SOURCE = {'SOURCE_FACT', 'CALLER_INFERENCE', 'HYPOTHESIS'}


def _cells(line):
    return [c.strip() for c in line.strip().strip('|').split('|')]


def load_rows():
    """Return (item rows, source ids) parsed from the ledger's markdown tables."""
    items, sources = [], set()
    for line in DOC.read_text(encoding='utf-8').splitlines():
        if not line.startswith('| '):
            continue
        cells = _cells(line)
        if re.fullmatch(r'S\d+', cells[0]):
            sources.add(cells[0])
        elif re.fullmatch(r'[KCR]\d+', cells[0]) and len(cells) == 8:
            items.append(dict(zip(
                ['id', 'side', 'item', 'status', 'value', 'source', 'reason', 'test'], cells)))
    return items, sources


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


class LedgerRuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.items, cls.sources = load_rows()
        cls.text = DOC.read_text(encoding='utf-8')

    def test_ledger_has_enough_rows_and_unique_ids(self):
        ids = [r['id'] for r in self.items]
        self.assertGreaterEqual(len(ids), 20)
        self.assertEqual(len(ids), len(set(ids)))
        for side in 'KCR':
            self.assertTrue(any(r['side'] == side for r in self.items), side)

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


class CallerFactTests(unittest.TestCase):
    def test_cd_seek_entry_is_call_bios_base_plus_y(self):
        m = re.search(r'\$00:([0-9A-F]{4})', CD_SEEK_EQUATE)
        self.assertIsNotNone(m)
        entry = int(m.group(1), 16)
        self.assertEqual(entry, CALL_BIOS_BASE + CALL_BIOS_Y_FOR_SEEK)
        self.assertEqual(entry, 0xE00C)

    def test_ledger_entry_matches_quoted_equate(self):
        self.assertIn('$E00C', DOC.read_text(encoding='utf-8'))

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
