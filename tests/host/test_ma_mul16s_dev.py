"""Host model of the DEV MA_MUL16S override (slot $4F, Issue #494). Reduced-domain check, not exhaustive.

dev_mul16s() mirrors src/overrides/slot_4F.s instruction by instruction at byte level (8-bit ADC/SBC with carry,
ROR through carry, the stack slots X+1..X+12). The expectation is Python integer multiplication of the signed
operands, which does not share the routine's algorithm.

Domain: 2^32 pairs are too many for Python. Checked here: every multiplier 0..65535 for 12 multiplicands, every
multiplicand 0..65535 for the same 12 multipliers, EDGES x EDGES, and 20,000 seeded random pairs.
This does NOT test the assembled bytes; the assembled routine runs only in tests/integration/geargrafx_dev_mul16s.py
(pinned Geargrafx core). This is not the System Card MA_MUL16S ABI.
"""
import random
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from build_rom import overrides  # noqa: E402
import ma_mul16s_model as model  # noqa: E402

FIXED = (0, 1, 2, 0x00FF, 0x0100, 0x7FFF, 0x8000, 0x8001, 0xAAAA, 0x5555, 0xFFFF, 0x1234)
EDGES = (0x0000, 0x0001, 0xFFFF, 0x0002, 0xFFFE, 0x7FFF, 0x8001, 0x8000, 0x00FF, 0x0100, 0xFF00, 0xFF01,
         0x5555, 0xAAAA, 0x1234, 0xEDCC)


def adc(a, m, c):
    t = a + m + c
    return t & 0xFF, t >> 8


def sbc(a, m, c):  # 6502 SBC: a - m - (1 - c); the returned carry is 1 when no borrow occurred
    t = a - m - (1 - c)
    return t & 0xFF, 1 if t >= 0 else 0


def ror(v, c):
    return (c << 7) | (v >> 1), v & 1


def dev_mul16s(a, b):
    """(a = multiplicand, b = multiplier), 16-bit patterns -> 32-bit product pattern, as slot_4F.s computes it."""
    m_lo, m_hi, q_lo, q_hi = a & 0xFF, a >> 8, b & 0xFF, b >> 8  # X+12, X+11, X+10, X+9
    cp_lo, cp_hi = q_lo, q_hi  # X+3, X+4: the multiplier copy pushed before the shift destroys it
    h_lo = h_hi = 0  # X+2, X+1
    for _ in range(16):
        if q_lo & 1:  # lda X+10; lsr a; bcc skips the add when bit 0 is 0
            h_lo, c = adc(h_lo, m_lo, 0)  # clc; adc
            h_hi, carry = adc(h_hi, m_hi, c)
        else:
            carry = 0  # the lsr left C=0
        h_hi, carry = ror(h_hi, carry)
        h_lo, carry = ror(h_lo, carry)
        q_hi, carry = ror(q_hi, carry)
        q_lo, carry = ror(q_lo, carry)
    if m_hi & 0x80:  # lda X+11; bpl skipped when positive
        h_lo, c = sbc(h_lo, cp_lo, 1)
        h_hi, _ = sbc(h_hi, cp_hi, c)
    if cp_hi & 0x80:
        h_lo, c = sbc(h_lo, m_lo, 1)
        h_hi, _ = sbc(h_hi, m_hi, c)
    return q_lo | q_hi << 8 | h_lo << 16 | h_hi << 24


class DevMul16sModelTests(unittest.TestCase):
    def check(self, a, b):
        self.assertEqual(model.expected(a, b), dev_mul16s(a, b), (a, b))

    def test_every_multiplier_for_fixed_multiplicands(self):
        for a in FIXED:
            for b in range(65536):
                self.check(a, b)

    def test_every_multiplicand_for_fixed_multipliers(self):
        for b in FIXED:
            for a in range(65536):
                self.check(a, b)

    def test_edges_and_random(self):
        for a in EDGES:
            for b in EDGES:
                self.check(a, b)
        rng = random.Random(494)
        for _ in range(20000):
            self.check(rng.randrange(65536), rng.randrange(65536))

    def test_independent_checks_agree_with_the_signed_range(self):
        for a, b, want in ((0x8000, 0x8000, 0x40000000), (0x8000, 0x7FFF, 0xC0008000), (0xFFFF, 0xFFFF, 1),
                           (0xFFFF, 1, 0xFFFFFFFF), (0, 0x8000, 0)):
            self.assertEqual(want, dev_mul16s(a, b))

    def test_slot_4f_is_a_dev_override_with_the_documented_shape(self):
        self.assertIn(0x4F, overrides())
        source = (ROOT / "src" / "overrides" / "slot_4F.s").read_text(encoding="utf-8")
        self.assertIn("api_slot_4F:", source)
        self.assertIn("ldy #16", source)
        self.assertIn("bne @round", source)
        pushes = len(re.findall(r"^\s+(?:php|phy|pha)\b", source, re.M))
        self.assertEqual(6, pushes)  # P, Y, multiplier copy x2, high word x2 (design budget: 8)
        pops = len(re.findall(r"^\s+(?:plp|ply|pla)\b", source, re.M))
        self.assertEqual(6, pops)
        for forbidden in ("tam", "tma", "tai", "tia", "tii", "tin", "tdd", "set", "stz", "$20", "$1"):
            self.assertNotRegex(source.split(".segment")[1], rf"^\s+{re.escape(forbidden)}\b", forbidden)


if __name__ == "__main__":
    unittest.main()
