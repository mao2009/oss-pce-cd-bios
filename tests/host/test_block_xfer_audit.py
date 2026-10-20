"""Host tests for the block transfer pre-block audit model (Issue #367).

Synthetic only: exercises tools/block_xfer_audit.py with hand-built MPR maps. No emulator, BIOS or ROM is
read. Positive, negative and mutation cases. A mutant policy must change a verdict, otherwise the
check is not sensitive to the thing it claims to guard.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import block_xfer_audit as B

MPR = [0xF8, 0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0xFF]  # page 1 -> bank 0, page 2 -> bank 1, page 7 -> I/O
WINDOW = (0x2000, 0x4000)  # page 1 only; 0x4000 (page 2) is allowed by bank but outside the window
ALLOWED = B.policy_from_mpr(MPR, {0x00, 0x01}, WINDOW)


class DestinationSetTest(unittest.TestCase):
    def test_tii_walks_up(self):
        self.assertEqual(B.destination_set("TII", 0x2000, 4), {0x2000, 0x2001, 0x2002, 0x2003})

    def test_tdd_walks_down_with_wrap(self):
        self.assertEqual(B.destination_set("TDD", 0x0001, 3), {0x0001, 0x0000, 0xFFFF})

    def test_tin_fixed_destination(self):
        self.assertEqual(B.destination_set("TIN", 0x2100, 50), {0x2100})

    def test_tia_covers_pair_superset(self):
        self.assertIn(0x2104, B.destination_set("TIA", 0x2100, 4))

    def test_length_zero_is_worst_case(self):
        self.assertEqual(len(B.destination_set("TII", 0x2000, 0)), B.MAX_BYTES)

    def test_length_65536_is_worst_case(self):
        # 0x10000 stores: the model must cover every address, so the count saturates at MAX_BYTES.
        self.assertEqual(len(B.destination_set("TII", 0x2000, 0x10000)), B.MAX_BYTES)

    def test_unknown_op_rejected(self):
        with self.assertRaises(ValueError):
            B.destination_set("TXX", 0, 1)


class AuditVerdictTest(unittest.TestCase):
    # positive
    def test_inside_window_permitted(self):
        for op in B.OPS:
            r = B.audit(op, 0x2000, 0x2100, 16, ALLOWED)
            self.assertEqual(r["verdict"], "PERMITTED_STATIC_ONLY", op)

    # negative: destination leaves the window or reaches the hardware page
    def test_tii_crossing_window_end_refused(self):
        r = B.audit("TII", 0x2000, 0x3FF8, 16, ALLOWED)
        self.assertEqual(r["verdict"], "REFUSED_STATIC")
        self.assertEqual(r["first_refused"], 0x4000)

    def test_tdd_below_window_refused(self):
        r = B.audit("TDD", 0x0000, 0x2002, 8, ALLOWED)
        self.assertEqual(r["verdict"], "REFUSED_STATIC")

    def test_hardware_page_refused_even_when_bank_listed(self):
        mpr = [0xF8, 0xFF, 0x01, 0x02, 0x03, 0x04, 0x05, 0xFF]  # page 1 mapped to the I/O page
        allowed = B.policy_from_mpr(mpr, {0x00, 0x01, 0xFF}, WINDOW)
        self.assertEqual(B.audit("TIN", 0x2000, 0x2000, 1, allowed)["verdict"], "REFUSED_STATIC")
        self.assertEqual(B.audit("TII", 0x2000, 0x2000, 4, allowed)["verdict"], "REFUSED_STATIC")

    def test_rom_bank_not_in_allowed_set_refused(self):
        allowed = B.policy_from_mpr(MPR, {0x01}, WINDOW)  # bank 0x00 not allowed
        self.assertEqual(B.audit("TIN", 0x2000, 0x2000, 1, allowed)["verdict"], "REFUSED_STATIC")

    def test_tia_crossing_window_end_refused(self):
        # TIA walks dst upward (the pair is a finite superset), so a run that ends past the window is refused.
        r = B.audit("TIA", 0x2000, 0x3FFE, 8, ALLOWED)
        self.assertEqual(r["verdict"], "REFUSED_STATIC")
        self.assertEqual(r["first_refused"], 0x4000)

    def test_gate_never_passes(self):
        for op in B.OPS:
            r = B.audit(op, 0x2000, 0x2000, 1, ALLOWED)
            self.assertEqual(r["gate"], B.BLOCKED)
            self.assertEqual(r["mid_instruction_stop"], "UNMEASURED")
            self.assertNotEqual(r["verdict"], "PASS")

    def test_stores_per_instruction_reported(self):
        self.assertEqual(B.audit("TII", 0x2000, 0x2100, 256, ALLOWED)["stores_in_one_instruction"], 256)


class MutationTest(unittest.TestCase):
    """Mutants must flip the verdict. A surviving mutant means the test is blind to that guard."""

    def test_mutant_ignoring_hardware_bank_is_caught(self):
        mpr = [0xF8, 0xFF, 0x01, 0x02, 0x03, 0x04, 0x05, 0xFF]  # page 1 is the I/O page
        real = B.policy_from_mpr(mpr, {0x00, 0x01, 0xFF}, WINDOW)  # 0xFF in allowed set, still refused

        def mutant(addr):  # drops the hardware-page guard
            return WINDOW[0] <= addr < WINDOW[1] and mpr[(addr >> 13) & 7] in {0x00, 0x01, 0xFF}

        self.assertEqual(B.audit("TII", 0x2000, 0x2000, 4, real)["verdict"], "REFUSED_STATIC")
        self.assertEqual(B.audit("TII", 0x2000, 0x2000, 4, mutant)["verdict"], "PERMITTED_STATIC_ONLY")

    def test_mutant_dropping_window_is_caught(self):
        def mutant(addr):
            return ALLOWED(addr) or addr >= 0x4000  # wrong: lets the upper window through
        self.assertEqual(B.audit("TII", 0x3FFC, 0x3FFC, 8, ALLOWED)["verdict"], "REFUSED_STATIC")
        self.assertEqual(B.audit("TII", 0x3FFC, 0x3FFC, 8, mutant)["verdict"], "PERMITTED_STATIC_ONLY")

    def test_mutant_tdd_direction_is_caught(self):
        # if TDD were modelled as walking up, a store near the top would be missed
        up = {(0x3FFE + i) & 0xFFFF for i in range(4)}
        down = B.destination_set("TDD", 0x3FFE, 4)
        self.assertNotEqual(up, down)
        self.assertEqual(B.audit("TDD", 0x0000, 0x3FFE, 4, ALLOWED)["verdict"], "PERMITTED_STATIC_ONLY")


if __name__ == "__main__":
    unittest.main()
