#!/usr/bin/env python3
"""Issue #717: host tests for side-effect-free peek and fail-closed fetch.

Host-only, synthetic inputs. No core is run, no BIOS/ROM byte is read. The
tests judge the host peek model (``tools/peek_table.py``) against the
acceptance rules of ``docs/717-peek-failclosed.md``: the peek-possibility
table, the D/T fetch order with fail-closed refusal, the transfer byte-range
sequences (TDD/TIN/TIA/TAI/len=0) consistent with the pinned-core matrix
model, and positive/negative/mutation coverage.

Evidence classes (AGENTS.md): everything here is ``HOST_MODEL``. Real-core
execution evidence (``REAL_CORE_EXECUTION`` on pinned Geargrafx ``b49ae82``
with synthetic BIOS/CD) is recorded in the document, not claimed here.
"""
import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests" / "integration"))

import tools.peek_table as peek  # noqa: E402

MPR_WRAM = [0x00, 0xF8, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]


def make_backing(cells=None, unmapped=()):
    """8 KiB synthetic backing stores for banks $00 and $F8 plus overrides."""
    wram = bytearray(0x2000)
    rom = bytearray([0xEA] * 0x2000)
    for addr, value in (cells or {}).items():
        bank = MPR_WRAM[(addr >> 13) & 7]
        if bank == 0xF8:
            wram[addr & 0x1FFF] = value
        elif bank == 0x00:
            rom[addr & 0x1FFF] = value
        else:
            # Cells in non-WRAM/non-ROM pages are not materialised: those
            # pages are unpeekable in the synthetic setup, so the value is
            # unreachable by design (fail-closed regardless of contents).
            continue
    backing = {0xF8: bytes(wram), 0x00: bytes(rom)}
    for bank in unmapped:
        backing.pop(bank, None)
    return backing


def block_cells(pc, op, src=0x3E80, dst=0x3E90, length=4):
    cells = {pc: op, pc + 1: src & 0xFF, pc + 2: (src >> 8) & 0xFF,
             pc + 3: dst & 0xFF, pc + 4: (dst >> 8) & 0xFF,
             pc + 5: length & 0xFF, pc + 6: (length >> 8) & 0xFF}
    return cells


class PeekTableTests(unittest.TestCase):
    def test_hardware_page_never_peekable(self):
        self.assertFalse(peek.is_peekable(0xFF))
        self.assertFalse(peek.is_peekable(0xFF, has_mapping=True))

    def test_mapper_banks_fail_closed_without_mapper(self):
        for bank in (0x00, 0x01, 0x40, 0x7F):
            with self.subTest(bank=hex(bank)):
                self.assertFalse(peek.is_peekable(bank, has_mapping=True))

    def test_wram_bank_peekable_when_mapped(self):
        self.assertTrue(peek.is_peekable(0xF8, has_mapping=True))
        self.assertFalse(peek.is_peekable(0xF8, has_mapping=False))

    def test_bank_boundaries(self):
        self.assertFalse(peek.is_peekable(0x7F))
        self.assertTrue(peek.is_peekable(0x80))
        self.assertTrue(peek.is_peekable(0xF7))
        self.assertTrue(peek.is_peekable(0xFE))

    def test_bad_bank_rejected(self):
        for bad in (None, -1, 256, "FF", True):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    peek.is_peekable(bad)


class FetchOrderTests(unittest.TestCase):
    def test_d_fetch_order_opcode_then_operand(self):
        backing = make_backing({0x3E00: 0x53, 0x3E01: 0x02})
        got = peek.fetch_d(0x3E00, MPR_WRAM, backing)
        self.assertEqual(got, {"ok": True, "opcode": 0x53, "operand": 0x02})

    def test_d_unpeekable_opcode_fails_closed(self):
        mpr = [0x00, 0xF8, 0xFF, 0x00, 0x00, 0x00, 0x00, 0x00]
        backing = make_backing({0x4000: 0x53, 0x4001: 0x02})
        self.assertEqual(peek.fetch_d(0x4000, mpr, backing)["reason"], "fail_closed")

    def test_d_unpeekable_second_byte_fails_whole_fetch(self):
        # Opcode at $3FFF is peekable, operand at $4000 maps to $FF: whole fetch fails.
        mpr = [0x00, 0xF8, 0xFF, 0x00, 0x00, 0x00, 0x00, 0x00]
        backing = make_backing({0x3FFF: 0x53})
        got = peek.fetch_d(0x3FFF, mpr, backing)
        self.assertFalse(got["ok"])
        self.assertEqual(got["reason"], "fail_closed")

    def test_d_non_tam_rejected_without_side_effect_claim(self):
        backing = make_backing({0x3E00: 0xEA, 0x3E01: 0x00})
        self.assertEqual(peek.fetch_d(0x3E00, MPR_WRAM, backing)["reason"], "not_tam")

    def test_t_fetch_seven_bytes_in_order(self):
        backing = make_backing(block_cells(0x3E00, 0x73))
        got = peek.fetch_t(0x3E00, MPR_WRAM, backing)
        self.assertTrue(got["ok"])
        self.assertEqual(got["opcode"], 0x73)
        self.assertEqual(got["name"], "TII")
        self.assertEqual((got["src"], got["dst"], got["length"]), (0x3E80, 0x3E90, 4))

    def test_t_late_unpeekable_byte_fails_whole_fetch(self):
        mpr = [0x00, 0xF8, 0xFF, 0x00, 0x00, 0x00, 0x00, 0x00]
        # Six bytes in WRAM, the 7th (pc+6) crosses into the $FF-mapped page.
        backing = make_backing({0x3FFA: 0x73, 0x3FFB: 0x80, 0x3FFC: 0x3E,
                                0x3FFD: 0x90, 0x3FFE: 0x3E, 0x3FFF: 0x04})
        got = peek.fetch_t(0x3FFA, mpr, backing)
        self.assertFalse(got["ok"])
        self.assertEqual(got["reason"], "fail_closed")

    def test_t_non_block_opcode_rejected(self):
        backing = make_backing(block_cells(0x3E00, 0xEA))
        self.assertEqual(peek.fetch_t(0x3E00, MPR_WRAM, backing)["reason"], "not_block")

    def test_t_hwpage_opcode_fails_closed(self):
        mpr = [0x00, 0xF8, 0xFF, 0x00, 0x00, 0x00, 0x00, 0x00]
        backing = make_backing({})
        got = peek.fetch_t(0x4000, mpr, backing)
        self.assertFalse(got["ok"])
        self.assertEqual(got["reason"], "fail_closed")


class ByteRangeTests(unittest.TestCase):
    def test_matrix_consistency_all_ops(self):
        from geargrafx_block_transfer_matrix import addr_lists  # noqa: E402
        op_index = {"TII": 0, "TIN": 1, "TIA": 2, "TDD": 3, "TAI": 4}
        for name, index in op_index.items():
            with self.subTest(op=name):
                srcs, dsts = peek.transfer_sequences(name, 0x3E80, 0x3E90, 4)
                expect_src, expect_dst = addr_lists(index, 0x3E80, 0x3E90, 4)
                self.assertEqual(srcs, expect_src)
                self.assertEqual(dsts, expect_dst)

    def test_tdd_decrements_both(self):
        srcs, dsts = peek.transfer_sequences("TDD", 0x3E80, 0x3E90, 4)
        self.assertEqual(srcs, [0x3E80, 0x3E7F, 0x3E7E, 0x3E7D])
        self.assertEqual(dsts, [0x3E90, 0x3E8F, 0x3E8E, 0x3E8D])

    def test_tin_destination_fixed(self):
        _srcs, dsts = peek.transfer_sequences("TIN", 0x3E80, 0x3E90, 4)
        self.assertEqual(dsts, [0x3E90] * 4)

    def test_tia_destination_alternates(self):
        _srcs, dsts = peek.transfer_sequences("TIA", 0x3E80, 0x4080, 4)
        self.assertEqual(dsts, [0x4080, 0x4081, 0x4080, 0x4081])

    def test_tai_source_alternates_destination_increments(self):
        srcs, dsts = peek.transfer_sequences("TAI", 0x3E80, 0x3E90, 4)
        self.assertEqual(srcs, [0x3E80, 0x3E81, 0x3E80, 0x3E81])
        self.assertEqual(dsts, [0x3E90, 0x3E91, 0x3E92, 0x3E93])

    def test_len0_means_65536_and_must_be_refused(self):
        srcs, dsts = peek.transfer_sequences("TII", 0x3E80, 0x3E90, 0)
        self.assertEqual(len(srcs), 0x10000)
        self.assertEqual(len(dsts), 0x10000)
        # The 16-bit wrap is exact: the last destination is start + 0xFFFF.
        self.assertEqual(dsts[-1], (0x3E90 + 0xFFFF) & 0xFFFF)

    def test_self_overwrite_detected(self):
        _srcs, dsts = peek.transfer_sequences("TII", 0x3E80, 0x3E00, 4)
        self.assertTrue(peek.overlaps_instruction(0x3E00, dsts))
        _srcs2, dsts2 = peek.transfer_sequences("TII", 0x3E80, 0x3E90, 4)
        self.assertFalse(peek.overlaps_instruction(0x3E00, dsts2))

    def test_unknown_op_rejected(self):
        with self.assertRaises(ValueError):
            peek.transfer_sequences("TXY", 0x3E80, 0x3E90, 4)


class PositiveNegativeMutationTests(unittest.TestCase):
    """Pos/neg/mutation at the host-model level (no core)."""

    def test_positive_wram_block_fetchable(self):
        backing = make_backing(block_cells(0x3E00, 0xD3, length=1))
        got = peek.fetch_t(0x3E00, MPR_WRAM, backing)
        self.assertTrue(got["ok"])
        self.assertEqual(got["name"], "TIN")

    def test_positive_tam_allowed_mask_fetchable(self):
        backing = make_backing({0x3E00: 0x53, 0x3E01: 0x02})
        self.assertTrue(peek.fetch_d(0x3E00, MPR_WRAM, backing)["ok"])

    def test_negative_hwpage_fetch_fail_closed(self):
        mpr = [0x00, 0xF8, 0xFF, 0x00, 0x00, 0x00, 0x00, 0x00]
        backing = make_backing({})
        self.assertFalse(peek.fetch_t(0x4000, mpr, backing)["ok"])
        self.assertFalse(peek.fetch_d(0x4000, mpr, backing)["ok"])

    def test_negative_unmapped_bank_fail_closed(self):
        backing = make_backing(block_cells(0x3E00, 0x73), unmapped=(0xF8,))
        self.assertEqual(peek.fetch_t(0x3E00, MPR_WRAM, backing)["reason"], "fail_closed")

    def test_mutation_wrong_opcode_not_accepted_as_block(self):
        backing = make_backing(block_cells(0x3E00, 0xEA))
        got = peek.fetch_t(0x3E00, MPR_WRAM, backing)
        self.assertFalse(got["ok"])
        self.assertNotEqual(got.get("name"), "TII")

    def test_mutation_partial_capture_never_passes(self):
        mpr = [0x00, 0xF8, 0xFF, 0x00, 0x00, 0x00, 0x00, 0x00]
        backing = make_backing({0x3FFA: 0x73, 0x3FFB: 0x80, 0x3FFC: 0x3E,
                                0x3FFD: 0x90, 0x3FFE: 0x3E, 0x3FFF: 0x04})
        broken = peek.fetch_t(0x3FFA, mpr, backing)
        # A caller that treated the 6 readable bytes as sufficient would be wrong.
        self.assertFalse(broken["ok"])
        self.assertEqual(len(broken["raw"]), 6)

    def test_mutation_falsified_mpr_rejected(self):
        backing = make_backing(block_cells(0x3E00, 0x73))
        bad_mpr = [0xFF] * 8
        # MPR0=$FF would map the zero page to hardware: the fetch must fail
        # closed, never return a usable byte.
        ok, _value = peek.peek_byte(0x0000, bad_mpr, backing)
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
