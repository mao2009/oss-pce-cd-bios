"""Host tests for the Issue #214 CD_SEEK ($04, $E00C) caller-ABI ledger.

These tests check the ledger's rules (status vocabulary, no VERIFIED/OBSERVED, callee rows never
promoted from caller source, UNKNOWN values never filled) and two pure facts quoted from the
pinned HuCC source: the cd_seek equate and the gulliver LBA table. No emulator, no ROM and no
System Card code is involved, and the table arithmetic is NOT evidence of BIOS behaviour.
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

# Caller data from examples/asm/elmer/ted2-core-gulliver/gulliver.asm L536-582 (24-bit dd entries).
# $00-$24 are the video table (37 entries). $25 is a read-speed test sector, not a video LBA.
GULLIVER_LBA = [
    0x015002, 0x018660, 0x018FA0, 0x019A90, 0x019CCE, 0x01A34A, 0x01A6DE, 0x01B27C,
    0x01B736, 0x01CF1C, 0x01F038, 0x01FC2A, 0x01FFFA, 0x020D12, 0x021196, 0x02149A,
    0x021840, 0x021C7C, 0x02288C, 0x022BEA, 0x023476, 0x0239F0, 0x023F82, 0x02448A,
    0x0258E6, 0x025F20, 0x026D9A, 0x0270CE, 0x027750, 0x027CDC, 0x028D54, 0x029586,
    0x02A07C, 0x02C23A, 0x02D858, 0x02EF9C, 0x02F4B0,
    0x000F25,
]
GULLIVER_VIDEO_COUNT = 37

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

    def test_gulliver_table_is_24bit_and_video_part_is_increasing(self):
        self.assertEqual(len(GULLIVER_LBA), 38)
        for v in GULLIVER_LBA:
            self.assertLess(v, 1 << 24)
        video = GULLIVER_LBA[:GULLIVER_VIDEO_COUNT]
        for a, b in zip(video, video[1:]):
            self.assertLess(a, b)
        # The $25 read-speed sector breaks the order on purpose: it is not a video LBA.
        self.assertLess(GULLIVER_LBA[-1], video[-1])

    def test_table_is_not_uniformly_msf_under_either_encoding(self):
        # Host arithmetic only: rules out a uniform MSF reading, does not choose LBA or any base.
        def split(v):
            return (v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF

        bad = [v for v in GULLIVER_LBA
               if not (is_valid_binary_msf(split(v)) or is_valid_bcd_msf(split(v)))]
        self.assertIn(0x018660, bad)
        self.assertGreater(len(bad), 0)


if __name__ == '__main__':
    unittest.main()
