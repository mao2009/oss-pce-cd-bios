"""Host model of the DEV MA_MUL8U override (slot $3F, Issue #436), checked for all 65,536 pairs.

The model mirrors src/overrides/slot_3F.s step by step: hi/lo/m live in stack bytes at X+1/X+2/X+3, and each of
one LSR of lo runs first, then each of the 8 rounds is ADC m on carry, ROR hi, ROR lo.

This is a Python reference-model check of the algorithm. It does NOT test the assembled bytes exhaustively; the
assembled routine is exercised only by the real-core run (18 cases) in tests/integration/geargrafx_dev_mul8u.py.
This is not the System Card MA_MUL8U ABI; it only checks that the emitted algorithm multiplies.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from build_rom import overrides  # noqa: E402


def dev_mul8u(a, x):
    """A = multiplicand, X = multiplier -> (A = product low, X = product high), as slot_3F.s."""
    stack = {"hi": 0, "lo": x >> 1, "m": a}
    carry = x & 1  # lda $2102,x / lsr a / sta $2102,x before the rounds: C = multiplier bit 0
    for _ in range(8):
        if carry:  # bcc not taken: clc; lda hi; adc m
            total = stack["hi"] + stack["m"]
            stack["hi"], carry = total & 0xFF, total >> 8
        bit0 = stack["hi"] & 1  # ror $0101,x: carry in, bit 0 out
        stack["hi"] = (carry << 7) | (stack["hi"] >> 1)
        carry = stack["lo"] & 1  # ror $0102,x: C leaves as the next multiplier bit
        stack["lo"] = (bit0 << 7) | (stack["lo"] >> 1)  # hi bit 0 enters bit 7
    return stack["lo"], stack["hi"]


class DevMul8uModelTests(unittest.TestCase):
    def test_all_pairs_match_product(self):
        for a in range(256):
            for x in range(256):
                product = a * x
                self.assertEqual((product & 0xFF, product >> 8), dev_mul8u(a, x), (a, x))

    def test_slot_3f_is_a_dev_override(self):
        self.assertIn(0x3F, overrides())
        source = (ROOT / "src" / "overrides" / "slot_3F.s").read_text(encoding="utf-8")
        self.assertIn("api_slot_3F:", source)
        self.assertIn("ldy #8", source)  # the 8 rounds: Y counter, BNE back to @round
        self.assertIn("bne @round", source)


if __name__ == "__main__":
    unittest.main()
