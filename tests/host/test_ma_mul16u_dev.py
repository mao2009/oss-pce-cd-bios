"""Host model of the DEV MA_MUL16U override (slot $41, Issue #448). Reduced-domain check, not exhaustive.

The model mirrors src/overrides/slot_41.s step by step: the 4 pushed bytes are the stack slots m_lo, m_hi,
q_lo, q_hi and the high word h_lo/h_hi is a scratch pair. Each of the 16 rounds does
the 16-bit ADC of m into h when bit 0 of q is set, then ROR h_hi, h_lo, q_hi, q_lo.

Domain: 2^32 pairs are too many for Python. Checked here: every multiplier (0..65535) for 12 multiplicands
(0, 1, 2, 0x00FF, 0x0100, 0x7FFF, 0x8000, 0xAAAA, 0x5555, 0xFFFE, 0xFFFF, 0x1234), plus 20,000 seeded random pairs.
This does NOT test the assembled bytes; the assembled routine is exercised only by the real-core run in
tests/integration/geargrafx_dev_mul16u.py. This is not the System Card MA_MUL16U ABI.
"""
import random
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from build_rom import overrides  # noqa: E402

MULTIPLICANDS = (0, 1, 2, 0x00FF, 0x0100, 0x7FFF, 0x8000, 0xAAAA, 0x5555, 0xFFFE, 0xFFFF, 0x1234)


def dev_mul16u(m, q):
    """(m = multiplicand, q = multiplier), both 16-bit -> 32-bit product, computed as slot_41.s does."""
    s = {"h_lo": 0, "h_hi": 0, "q_lo": q & 0xFF, "q_hi": q >> 8, "m_lo": m & 0xFF, "m_hi": m >> 8}
    for _ in range(16):
        carry = s["q_lo"] & 1  # lda $2108,x / lsr a: C = multiplier bit 0
        if carry:  # clc; adc m into high word, with carry through both bytes
            total = s["h_lo"] + s["m_lo"]
            s["h_lo"], carry = total & 0xFF, total >> 8
            total = s["h_hi"] + s["m_hi"] + carry
            s["h_hi"], carry = total & 0xFF, total >> 8
        for key_in, key_out in (("h_hi", "h_hi"), ("h_lo", "h_lo"), ("q_hi", "q_hi"), ("q_lo", "q_lo")):
            new_carry = s[key_in] & 1  # ror: this byte's bit 0 leaves as the next carry
            s[key_out] = (carry << 7) | (s[key_out] >> 1)
            carry = new_carry
    product = s["q_lo"] | (s["q_hi"] << 8) | (s["h_lo"] << 16) | (s["h_hi"] << 24)
    return product


class DevMul16uModelTests(unittest.TestCase):
    def check(self, m, q):
        self.assertEqual(m * q, dev_mul16u(m, q), (m, q))

    def test_all_multipliers_for_reduced_multiplicands(self):
        for m in MULTIPLICANDS:
            for q in range(65536):
                self.check(m, q)

    def test_seeded_random_pairs(self):
        rng = random.Random(448)
        for _ in range(20000):
            self.check(rng.randrange(65536), rng.randrange(65536))

    def test_slot_41_is_a_dev_override(self):
        self.assertIn(0x41, overrides())
        source = (ROOT / "src" / "overrides" / "slot_41.s").read_text(encoding="utf-8")
        self.assertIn("api_slot_41:", source)
        self.assertIn("ldy #16", source)  # the 16 rounds: Y counter, BNE back to @round
        self.assertIn("bne @round", source)


if __name__ == "__main__":
    unittest.main()
