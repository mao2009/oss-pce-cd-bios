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


def dev_mul16s(a, b, mut=None):
    """(a = multiplicand, b = multiplier), 16-bit patterns -> 32-bit product pattern, as slot_4F.s computes it.
    mut (Issue #89 sensitivity only) names one single-slip mutant of this model; None is the original."""
    m_lo, m_hi, q_lo, q_hi = a & 0xFF, a >> 8, b & 0xFF, b >> 8  # X+12, X+11, X+10, X+9
    cp_lo, cp_hi = q_lo, q_hi  # X+3, X+4: the multiplier copy pushed before the shift destroys it
    h_lo = h_hi = 0  # X+2, X+1
    for i in range(15 if mut == "rounds_15" else 16):
        if q_lo & 1:  # lda X+10; lsr a; bcc skips the add when bit 0 is 0
            h_lo, c = adc(h_lo, m_lo, 0)  # clc; adc
            h_hi, carry = adc(h_hi, m_hi, 0 if mut == "adc_hi_no_carry" else c)
        else:
            carry = 0  # the lsr left C=0
        h_hi, carry = ror(h_hi, carry)
        h_lo, carry = ror(h_lo, 0 if mut == "ror_lo_no_carry_in" else carry)
        q_hi, carry = ror(q_hi, carry)
        q_lo, carry = ror(q_lo, carry)
    lo_c = 0 if mut == "lo_sbc_no_carry" else 1
    if m_hi & 0x80 and mut != "no_a_fix":  # lda X+11; bpl skipped when positive
        h_lo, c = sbc(h_lo, cp_lo, lo_c)
        h_hi, _ = sbc(h_hi, cp_hi, 1 if mut == "hi_sbc_ignores_borrow" else c)
    sign_b = (q_hi if mut == "fix_uses_final_multiplier" else cp_hi) & 0x80
    if sign_b and mut != "no_b_fix":
        h_lo, c = sbc(h_lo, m_lo, lo_c)
        h_hi, _ = sbc(h_hi, m_hi, 1 if mut == "hi_sbc_ignores_borrow" else c)
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


# Issue #89 prep: sensitivity of the reduced-domain check. Each mutant is one plausible slip in slot_4F.s, applied to
# the byte model only. A mutant must differ from the original somewhere (applied), and the check must reject it.
MUTANTS = ("no_a_fix", "no_b_fix", "hi_sbc_ignores_borrow", "lo_sbc_no_carry", "adc_hi_no_carry",
           "ror_lo_no_carry_in", "fix_uses_final_multiplier", "rounds_15")
SENSITIVITY_RANDOM = 2000


class DevMul16sSensitivityTests(unittest.TestCase):
    def sensitivity_pairs(self):
        rng = random.Random(89)
        return [(a, b) for a in EDGES for b in EDGES] + [(rng.randrange(65536), rng.randrange(65536))
                                                         for _ in range(SENSITIVITY_RANDOM)]

    def test_each_mutant_is_applied_and_detected(self):
        pairs = self.sensitivity_pairs()
        gaps = []
        for mut in MUTANTS:
            diffs = [(a, b) for a, b in pairs if dev_mul16s(a, b, mut=mut) != dev_mul16s(a, b)]
            self.assertTrue(diffs, f"mutant {mut} is not applied on the domain")  # applied
            detected = [(a, b) for a, b in pairs if dev_mul16s(a, b, mut=mut) != model.expected(a, b)]
            if not detected:
                gaps.append(mut)
        self.assertEqual([], gaps, "sensitivity gap: undetected mutants")

    def test_original_matches_model_on_sensitivity_pairs(self):
        for a, b in self.sensitivity_pairs():
            self.assertEqual(model.expected(a, b), dev_mul16s(a, b), (a, b))

    def test_routine_writes_and_reads_only_its_frame(self):
        # Static guard on slot_4F.s: every memory operand (not #imm, not A) is $21xx,X inside the 6-push frame
        # ($2101..$210C), so no zero page, BSS, I/O, MPR or out-of-frame stack byte is touched.
        body = (ROOT / "src" / "overrides" / "slot_4F.s").read_text(encoding="utf-8").split(".export api_slot_4F")[1]
        operands = []
        for line in body.splitlines():
            code = line.split(";")[0].strip()
            parts = code.split(None, 1)
            if len(parts) == 2 and parts[0] not in ("tsx", "php", "phy", "pha", "plp", "ply", "pla", "rts", "cld"):
                operands.append((parts[0], parts[1].strip()))
        self.assertTrue(operands)
        touched = set()
        for mnem, op in operands:
            if op.startswith("#") or op == "a" or mnem.startswith("b"):
                continue
            m = re.fullmatch(r"\$(21[0-9A-F]{2}),x", op)
            self.assertIsNotNone(m, f"{mnem} {op} is outside the stack frame form $21xx,X")
            addr = int(m.group(1), 16)
            self.assertTrue(0x2101 <= addr <= 0x210C, f"{mnem} {op} outside $2101..$210C")
            touched.add(addr)
        self.assertTrue(touched <= set(range(0x2101, 0x210D)), touched)


if __name__ == "__main__":
    unittest.main()
