#!/usr/bin/env python3
"""Issue #722: five-opcode transfer regression table + boundary lengths.

Host-only, synthetic inputs. No core is run, no BIOS/ROM byte is read. The
tests judge the host matrix model (``tools/xfer_722_matrix.py``), which
reuses ``tools/peek_table.py`` from Issue #717 (PR #766) for every peek,
fetch and address-sequence computation -- nothing here reimplements peek.

Evidence classes (AGENTS.md): everything here is ``HOST_MODEL``. Real-core
execution evidence (``REAL_CORE_EXECUTION`` on pinned Geargrafx ``b49ae82``
with synthetic BIOS/CD) is recorded in ``docs/722-five-xfer.md``, not
claimed here. ``gate`` is always ``BLOCKED``; no verdict here is PASS.
"""
import inspect
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests" / "integration"))

import tools.peek_table as peek  # noqa: E402
import tools.xfer_722_matrix as matrix  # noqa: E402


def block_cells(pc, op, src=0x3E80, dst=0x3E90, length=4):
    return {pc: op, pc + 1: src & 0xFF, pc + 2: (src >> 8) & 0xFF,
            pc + 3: dst & 0xFF, pc + 4: (dst >> 8) & 0xFF,
            pc + 5: length & 0xFF, pc + 6: (length >> 8) & 0xFF}


OPCODES = {"TII": 0x73, "TIN": 0xD3, "TDD": 0xC3, "TIA": 0xE3, "TAI": 0xF3}


class LengthMatrixTests(unittest.TestCase):
    """5 ops x lengths 1/2/3/4/8 match the pinned-core matrix model exactly."""

    def test_all_ops_all_lengths_match_addr_lists(self):
        from geargrafx_block_transfer_matrix import addr_lists  # noqa: E402
        for name, opcode in OPCODES.items():
            index = matrix.OP_INDEX[name]
            for n in matrix.LENGTHS:
                with self.subTest(op=name, length=n):
                    src = 0x3E80
                    dst = 0x3E90
                    srcs, dsts = peek.transfer_sequences(name, src, dst, n)
                    expect_src, expect_dst = addr_lists(index, src, dst, n)
                    self.assertEqual(srcs, expect_src)
                    self.assertEqual(dsts, expect_dst)
                    self.assertEqual(OPCODES[name], opcode)

    def test_matrix_tool_length_rows_allow(self):
        for row in matrix.case_rows():
            if "-len" in row["case"] and not row["case"].endswith("len0"):
                with self.subTest(case=row["case"]):
                    backing = matrix.wram_backing()
                    got = matrix.verdict(row["op"], row["src"], row["dst"],
                                         row["length"], row["mpr"], backing)
                    self.assertEqual(got["verdict"], "WOULD_ALLOW_STATIC")
                    self.assertEqual(got["gate"], "BLOCKED")

    def test_len0_all_ops_refused_without_expansion(self):
        for op in matrix.OPS:
            with self.subTest(op=op):
                backing = matrix.wram_backing()
                got = matrix.verdict(op, 0x3E80, 0x3E90, 0,
                                     list(matrix.MPR_WRAM), backing)
                self.assertEqual(got["verdict"], "REFUSED")
                self.assertEqual(got["reason"], "len0_never_executed")
                self.assertEqual(got["count"], 0x10000)
                self.assertEqual(got["gate"], "BLOCKED")


class Len0ExactnessTests(unittest.TestCase):
    """length 0 = 65536 bytes, computed exactly, never executed."""

    def test_len0_size_all_ops(self):
        for op in matrix.OPS:
            with self.subTest(op=op):
                srcs, dsts = peek.transfer_sequences(op, 0x3E80, 0x3E90, 0)
                self.assertEqual(len(srcs), 0x10000)
                self.assertEqual(len(dsts), 0x10000)

    def test_len0_tii_wrap(self):
        _srcs, dsts = peek.transfer_sequences("TII", 0x3E80, 0x3E90, 0)
        self.assertEqual(dsts[-1], (0x3E90 + 0xFFFF) & 0xFFFF)
        self.assertEqual(len(set(dsts)), 0x10000)

    def test_len0_tdd_wraps_down(self):
        srcs, dsts = peek.transfer_sequences("TDD", 0x3E90, 0x3E90, 0)
        self.assertEqual(srcs[0], 0x3E90)
        self.assertEqual(srcs[1], 0x3E8F)
        self.assertEqual(dsts[-1], (0x3E90 - 0xFFFF) & 0xFFFF)

    def test_len0_tin_single_destination(self):
        _srcs, dsts = peek.transfer_sequences("TIN", 0x3E80, 0x3E90, 0)
        self.assertEqual(set(dsts), {0x3E90})

    def test_len0_tia_tai_alternation_phase(self):
        _srcs, dsts = peek.transfer_sequences("TIA", 0x3E80, 0x4080, 0)
        self.assertEqual(dsts[0], 0x4080)
        self.assertEqual(dsts[1], 0x4081)
        self.assertEqual(dsts[2], 0x4080)
        srcs, _dsts = peek.transfer_sequences("TAI", 0x3E80, 0x3E90, 0)
        self.assertEqual(srcs[:4], [0x3E80, 0x3E81, 0x3E80, 0x3E81])


class PerOpWalkTests(unittest.TestCase):
    """Operand-level walks: TDD down, TIN fixed, TIA/TAI alternation."""

    def test_tii_increments_both(self):
        srcs, dsts = peek.transfer_sequences("TII", 0x3E80, 0x3E90, 4)
        self.assertEqual(srcs, [0x3E80, 0x3E81, 0x3E82, 0x3E83])
        self.assertEqual(dsts, [0x3E90, 0x3E91, 0x3E92, 0x3E93])

    def test_tdd_decrements_both(self):
        srcs, dsts = peek.transfer_sequences("TDD", 0x3E83, 0x3E93, 4)
        self.assertEqual(srcs, [0x3E83, 0x3E82, 0x3E81, 0x3E80])
        self.assertEqual(dsts, [0x3E93, 0x3E92, 0x3E91, 0x3E90])

    def test_tin_destination_fixed(self):
        srcs, dsts = peek.transfer_sequences("TIN", 0x3E80, 0x3E90, 4)
        self.assertEqual(srcs, [0x3E80, 0x3E81, 0x3E82, 0x3E83])
        self.assertEqual(dsts, [0x3E90] * 4)

    def test_tia_destination_alternates(self):
        srcs, dsts = peek.transfer_sequences("TIA", 0x3E80, 0x4080, 4)
        self.assertEqual(srcs, [0x3E80, 0x3E81, 0x3E82, 0x3E83])
        self.assertEqual(dsts, [0x4080, 0x4081, 0x4080, 0x4081])

    def test_tai_source_alternates(self):
        srcs, dsts = peek.transfer_sequences("TAI", 0x3E80, 0x3E90, 4)
        self.assertEqual(srcs, [0x3E80, 0x3E81, 0x3E80, 0x3E81])
        self.assertEqual(dsts, [0x3E90, 0x3E91, 0x3E92, 0x3E93])

    def test_length1_single_byte_all_ops(self):
        for op in matrix.OPS:
            with self.subTest(op=op):
                srcs, dsts = peek.transfer_sequences(op, 0x3E80, 0x3E90, 1)
                self.assertEqual(srcs, [0x3E80])
                self.assertEqual(dsts, [0x3E90])


class BoundaryBankTests(unittest.TestCase):
    """Bank-boundary / MPR-alias / I/O-page crossings, read and write."""

    def test_tii_crosses_3fff_into_next_page(self):
        srcs, dsts = peek.transfer_sequences("TII", 0x3E80, 0x3FFF, 2)
        self.assertEqual(dsts, [0x3FFF, 0x4000])

    def test_ffff_wraps_to_zero(self):
        _srcs, dsts = peek.transfer_sequences("TII", 0x3E80, 0xFFFF, 2)
        self.assertEqual(dsts, [0xFFFF, 0x0000])

    def test_tdd_reverse_crosses_down(self):
        srcs, dsts = peek.transfer_sequences("TDD", 0x3E80, 0x4000, 2)
        self.assertEqual(srcs, [0x3E80, 0x3E7F])
        self.assertEqual(dsts, [0x4000, 0x3FFF])

    def test_alias_page_resolves_to_wram_bank(self):
        backing = matrix.wram_backing()
        ok, value = peek.peek_byte(0x4010, list(matrix.MPR_ALIAS), backing)
        self.assertTrue(ok)
        got = matrix.verdict("TII", 0x3E80, 0x4010, 2,
                             list(matrix.MPR_ALIAS), backing)
        self.assertEqual(got["verdict"], "WOULD_ALLOW_STATIC")

    def test_hwpage_destination_refused(self):
        backing = matrix.wram_backing()
        got = matrix.verdict("TII", 0x3E80, 0x4000, 2,
                             list(matrix.MPR_HW), backing)
        self.assertEqual(got["verdict"], "REFUSED")
        self.assertEqual(got["reason"], "dst_hardware_page")
        self.assertEqual(got["gate"], "BLOCKED")

    def test_hwpage_fetch_fail_closed(self):
        backing = matrix.wram_backing()
        got = peek.fetch_t(0x4000, list(matrix.MPR_HW), backing)
        self.assertFalse(got["ok"])
        self.assertEqual(got["reason"], "fail_closed")

    def test_unmapped_bank_fail_closed(self):
        backing = matrix.wram_backing(unmapped=(0xF8,))
        got = matrix.verdict("TII", 0x3E80, 0x3E90, 2,
                             list(matrix.MPR_WRAM), backing)
        self.assertEqual(got["verdict"], "FAIL_CLOSED")
        self.assertEqual(got["reason"], "src_unpeekable")

    def test_rom_bank_destination_unknown_other_bank(self):
        # A mapped bank outside $F8-$FB ($E0) is peekable but its
        # writability is unreconciled with the #367/#648 WRAM-only
        # preflight, so UNKNOWN (never allow, never PASS).
        backing = dict(matrix.wram_backing())
        backing[0xE0] = bytes(bytearray(0x2000))
        mpr = [0x00, 0xF8, 0xE0, 0xF8, 0x00, 0x00, 0x00, 0x00]
        got = matrix.verdict("TII", 0x3E80, 0x4000, 1, mpr, backing)
        self.assertEqual(got["verdict"], "UNKNOWN")
        self.assertIn("other-bank", got["unknown"])


class FailClosedTests(unittest.TestCase):
    """Unpredictable / unpeekable source or destination fails closed."""

    def test_fetch_unpeekable_fails_whole_verdict(self):
        backing = matrix.wram_backing()
        cells = block_cells(0x3FFA, 0x73)
        for addr, value in cells.items():
            bank = matrix.MPR_WRAM[(addr >> 13) & 7]
            if bank == 0xF8:
                raw = bytearray(backing[0xF8])
                raw[addr & 0x1FFF] = value
                backing[0xF8] = bytes(raw)
        mpr = [0x00, 0xF8, 0xFF, 0x00, 0x00, 0x00, 0x00, 0x00]
        got = matrix.verdict("TII", 0x3E80, 0x3E90, 4, mpr, backing,
                             pc=0x3FFA)
        self.assertEqual(got["verdict"], "FAIL_CLOSED")
        self.assertEqual(got["reason"], "fetch_unpeekable")

    def test_unpeekable_src_byte_fails_closed(self):
        backing = matrix.wram_backing()
        mpr = [0x00, 0xF8, 0xFF, 0xF8, 0x00, 0x00, 0x00, 0x00]
        # TII from $3FFF reads $3FFF (WRAM page 3) then $4000 ($FF page).
        got = matrix.verdict("TII", 0x3FFF, 0x3E90, 2, mpr, backing)
        self.assertEqual(got["verdict"], "FAIL_CLOSED")
        self.assertEqual(got["reason"], "src_unpeekable")
        self.assertEqual(got["byte"], 1)

    def test_pc_none_means_fetch_not_run(self):
        backing = matrix.wram_backing()
        got = matrix.verdict("TII", 0x3E80, 0x3E90, 2,
                             list(matrix.MPR_WRAM), backing)
        self.assertIsNone(got["fetch"])
        self.assertEqual(got["verdict"], "WOULD_ALLOW_STATIC")

    def test_unknown_op_rejected(self):
        with self.assertRaises(ValueError):
            matrix.verdict("TXY", 0x3E80, 0x3E90, 2,
                           list(matrix.MPR_WRAM), matrix.wram_backing())


class SelfOverwriteTests(unittest.TestCase):
    """Whole-range self-overwrite is refused before the first step."""

    def test_forward_self_overwrite_refused(self):
        backing = matrix.wram_backing(block_cells(0x3E00, 0x73))
        got = matrix.verdict("TII", 0x3E80, 0x3E00, 4,
                             list(matrix.MPR_WRAM), backing, pc=0x3E00)
        self.assertEqual(got["verdict"], "REFUSED")
        self.assertEqual(got["reason"], "self_overwrite")

    def test_tdd_backward_self_overwrite_refused(self):
        backing = matrix.wram_backing(block_cells(0x3E00, 0xC3))
        # TDD from $3E10 walks $3E10..$3E0D: clear of the $3E00..$3E06
        # instruction, so allowed; from $3E03 it walks into the opcode.
        got = matrix.verdict("TDD", 0x3E80, 0x3E10, 4,
                             list(matrix.MPR_WRAM), backing, pc=0x3E00)
        got2 = matrix.verdict("TDD", 0x3E80, 0x3E03, 4,
                              list(matrix.MPR_WRAM), backing, pc=0x3E00)
        self.assertEqual(got["verdict"], "WOULD_ALLOW_STATIC")
        self.assertEqual(got2["verdict"], "REFUSED")
        self.assertEqual(got2["reason"], "self_overwrite")

    def test_wrap_self_overwrite_refused(self):
        # All pages mapped to WRAM so the 16-bit wrap is exercised:
        # TII from $FFFF walks $FFFF, $0000, $0001 into the instruction.
        wram = bytearray(0x2000)
        for addr, value in block_cells(0x0000, 0x73, dst=0xFFFF,
                                       length=3).items():
            wram[addr & 0x1FFF] = value
        backing = {0xF8: bytes(wram)}
        mpr = [0xF8] * 8
        _srcs, dsts = peek.transfer_sequences("TII", 0x3E80, 0xFFFF, 3)
        self.assertEqual(dsts, [0xFFFF, 0x0000, 0x0001])
        self.assertTrue(peek.overlaps_instruction(0x0000, dsts))
        got = matrix.verdict("TII", 0x3E80, 0xFFFF, 3, mpr, backing,
                             pc=0x0000)
        self.assertTrue(got["fetch"]["ok"])
        self.assertEqual(got["verdict"], "REFUSED")
        self.assertEqual(got["reason"], "self_overwrite")

    def test_clear_range_allowed(self):
        backing = matrix.wram_backing(block_cells(0x3E00, 0x73))
        got = matrix.verdict("TII", 0x3E80, 0x3E90, 4,
                             list(matrix.MPR_WRAM), backing, pc=0x3E00)
        self.assertTrue(got["fetch"]["ok"])
        self.assertEqual(got["verdict"], "WOULD_ALLOW_STATIC")


class UnknownLedgerTests(unittest.TestCase):
    """#367-unreconciled rows stay UNKNOWN, never PASS, never allow."""

    def test_tia_cross_page_unknown(self):
        backing = matrix.wram_backing()
        got = matrix.verdict("TIA", 0x3E80, 0x3FFF, 2,
                             list(matrix.MPR_WRAM), backing)
        self.assertEqual(got["verdict"], "UNKNOWN")
        self.assertIn("alternation", got["unknown"])

    def test_tai_cross_page_unknown(self):
        backing = matrix.wram_backing()
        got = matrix.verdict("TAI", 0x3E80, 0x3FFF, 2,
                             list(matrix.MPR_WRAM), backing)
        self.assertEqual(got["verdict"], "UNKNOWN")

    def test_unknown_rows_never_allow_and_gate_blocked(self):
        for row in matrix.case_rows():
            backing = matrix.wram_backing(unmapped=row.get("unmapped", ()))
            got = matrix.verdict(row["op"], row["src"], row["dst"],
                                 row["length"], row["mpr"], backing)
            with self.subTest(case=row["case"]):
                self.assertEqual(got["gate"], "BLOCKED")
                self.assertNotEqual(got["verdict"], "PASS")
                if got["verdict"] == "UNKNOWN":
                    self.assertIsNotNone(got["unknown"])

    def test_device_dma_w17_unknown_pinned(self):
        self.assertIn("W17", matrix.UNKNOWN_DEVICE_DMA_W17)
        self.assertIn("other-bank", matrix.UNKNOWN_OTHER_BANK)
        self.assertIn("alternation", matrix.UNKNOWN_ALTERNATION_XPAGE)
        self.assertIn("mapper", matrix.UNKNOWN_MAPPER_BANK)


class NoReimplementationTests(unittest.TestCase):
    """The matrix reuses #717 peek; it must not redefine it."""

    def test_uses_peek_table_functions(self):
        self.assertIs(matrix.peek.transfer_sequences, peek.transfer_sequences)
        self.assertIs(matrix.peek.fetch_t, peek.fetch_t)
        self.assertIs(matrix.peek.is_peekable, peek.is_peekable)
        self.assertIs(matrix.peek.overlaps_instruction, peek.overlaps_instruction)

    def test_no_local_peek_definitions(self):
        source = inspect.getsource(matrix)
        for name in ("def is_peekable", "def peek_byte", "def fetch_d",
                     "def fetch_t", "def transfer_sequences",
                     "def overlaps_instruction"):
            self.assertNotIn(name, source)


class MutationTests(unittest.TestCase):
    """Wrong-model mutants are caught (RED): the exact model is load-bearing."""

    def test_mut_tseq_every_opcode_as_tii_mismatch(self):
        from geargrafx_block_transfer_matrix import addr_lists  # noqa: E402
        # Modelling every opcode as TII (index 0) disagrees with the exact
        # walk for TDD/TIN/TIA/TAI: the mutant must be detected.
        for name, index in (("TDD", 3), ("TIN", 1), ("TIA", 2), ("TAI", 4)):
            with self.subTest(op=name):
                exact_src, exact_dst = peek.transfer_sequences(
                    name, 0x3E80, 0x3E90, 4)
                as_tii_src, as_tii_dst = addr_lists(0, 0x3E80, 0x3E90, 4)
                self.assertNotEqual((exact_src, exact_dst),
                                    (as_tii_src, as_tii_dst))

    def test_mut_tfirst_first_byte_only_misses_late_hw(self):
        backing = matrix.wram_backing()
        mpr = [0x00, 0xF8, 0xFF, 0xF8, 0x00, 0x00, 0x00, 0x00]
        # First destination byte $3FFF is WRAM; the second ($4000) is the
        # hardware page. A first-byte-only check would wrongly allow.
        first_only = matrix._bank_of(0x3FFF, mpr) in matrix.WRAM_BANKS
        self.assertTrue(first_only)
        got = matrix.verdict("TII", 0x3E80, 0x3FFF, 2, mpr, backing)
        self.assertEqual(got["verdict"], "REFUSED")
        self.assertEqual(got["byte"], 1)

    def test_mut_len0_executed_is_forbidden(self):
        # A caller that expanded len0 into an executed walk instead of
        # refusing would touch 65536 bytes: the model refuses before any step.
        backing = matrix.wram_backing()
        for op in matrix.OPS:
            with self.subTest(op=op):
                got = matrix.verdict(op, 0x3E80, 0x3E90, 0,
                                     list(matrix.MPR_WRAM), backing)
                self.assertNotEqual(got["verdict"], "WOULD_ALLOW_STATIC")

    def test_mut_partial_capture_never_passes(self):
        mpr = [0x00, 0xF8, 0xFF, 0x00, 0x00, 0x00, 0x00, 0x00]
        backing = matrix.wram_backing({0x3FFA: 0x73, 0x3FFB: 0x80,
                                       0x3FFC: 0x3E, 0x3FFD: 0x90,
                                       0x3FFE: 0x3E, 0x3FFF: 0x04})
        broken = peek.fetch_t(0x3FFA, mpr, backing)
        self.assertFalse(broken["ok"])
        self.assertEqual(len(broken["raw"]), 6)


if __name__ == "__main__":
    unittest.main()
