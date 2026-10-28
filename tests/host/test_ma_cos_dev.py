"""Host checks of the DEV MA_COS override (slot $46, Issue #80), stacked on MA_SIN (slot $45, Issue #79).

MA_COS has no table of its own. src/overrides/slot_46.s is a tail jump into api_slot_45 with a 64-step
phase shift (a quarter turn):  cos(a) = sin((a + 64) & $FF).  These tests check that identity against
independent math oracles (math.cos / math.sin, not the table), using the table that slot_45.s actually
contains, and check the wrapper's source contract (binary add, P restored, no own table).

This does NOT run the assembled bytes; the real-core run is tests/integration/geargrafx_dev_cos.py.
This is not the System Card MA_COS ABI (UNVERIFIED).
"""
import math
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from build_rom import overrides  # noqa: E402

COS_SOURCE = ROOT / "src" / "overrides" / "slot_46.s"
SIN_SOURCE = ROOT / "src" / "overrides" / "slot_45.s"


def cos_oracle(a):
    """round(32767 * cos(2*pi*a/256)) with round-half-up (no tie is possible except at the axes)."""
    return math.floor(32767 * math.cos(2 * math.pi * a / 256) + 0.5)


def sin_oracle(a):
    """round(32767 * sin(2*pi*a/256)) with round-half-up."""
    return math.floor(32767 * math.sin(2 * math.pi * a / 256) + 0.5)


def slot45_table():
    """The 65-entry quarter-wave table that slot_45.s actually contains: T[i] for i = 0..64."""
    text = SIN_SOURCE.read_text(encoding="utf-8")
    lo_block = text.split("sin_lo:", 1)[1].split("sin_hi:", 1)[0]
    hi_block = text.split("sin_hi:", 1)[1]
    parse = lambda block: [int(h, 16) for h in re.findall(r"\$([0-9A-Fa-f]{2})", block)]
    lo, hi = parse(lo_block), parse(hi_block)
    assert len(lo) == 65 and len(hi) == 65, "slot_45.s table is not 65 + 65 entries"
    return [lo[i] | (hi[i] << 8) for i in range(65)]


def sin_routine(a, table):
    """MA_SIN folding as slot_45.s does it, over an explicit table (used for mutants too)."""
    r = a & 0x3F
    index = (64 - r) if (a & 0x40) else r
    value = table[index]
    if a & 0x80:
        value = -value
    return value


def as_signed16(word):
    return word - 0x10000 if word & 0x8000 else word


def sin_word(a, table):
    """MA_SIN result as the signed 16-bit value the routine returns in X:A."""
    return as_signed16(sin_routine(a, table) & 0xFFFF)


def cos_by_shift(a, table):
    """MA_COS as specified: the sine routine at (a + 64) & $FF."""
    return sin_word((a + 64) & 0xFF, table)


TABLE = slot45_table()


class CosShiftIdentityTests(unittest.TestCase):
    def test_slot45_table_is_the_sine_oracle(self):
        """The shared table is round(32767*sin(pi/2*i/64)) for i = 0..64 (independent check of MA_SIN's table)."""
        for i in range(65):
            self.assertEqual(TABLE[i], sin_oracle(i), i)

    def test_all_256_angles_match_independent_cosine(self):
        """cos(a) via the shifted sine routine == round(32767*cos(2*pi*a/256)) for every a in 0..255."""
        bad = [a for a in range(256) if cos_by_shift(a, TABLE) != cos_oracle(a)]
        self.assertEqual(bad, [])

    def test_quadrant_boundaries(self):
        expect = {0: 32767, 64: 0, 128: -32767, 192: 0}
        for a, want in expect.items():
            self.assertEqual(cos_by_shift(a, TABLE), want, a)

    def test_range_and_no_minus_32768(self):
        for a in range(256):
            v = cos_by_shift(a, TABLE)
            self.assertTrue(-32767 <= v <= 32767, a)

    def test_precision_bound(self):
        worst = max(abs(cos_by_shift(a, TABLE) - 32767 * math.cos(2 * math.pi * a / 256)) for a in range(256))
        self.assertLessEqual(worst, 0.5 + 1e-9)


class CosMutantTests(unittest.TestCase):
    """Each mutant must be detected by the 256-angle identity (a mutant that passes is a failed test)."""

    def assertDetected(self, name, fn):
        differs = [a for a in range(256) if fn(a) != cos_oracle(a)]
        self.assertTrue(differs, f"mutant {name} was not detected")

    def test_phase_shift_mutants(self):
        for shift in (63, 65, 0, 128):
            self.assertDetected(f"shift_{shift}", lambda a, s=shift: sin_word((a + s) & 0xFF, TABLE))

    def test_table_byte_flip_in_slot45_is_detected(self):
        bad = list(TABLE)
        bad[16] ^= 0x01
        self.assertDetected("table_byte_flip", lambda a: cos_by_shift(a, bad))

    def test_missing_negation_is_detected(self):
        def no_negate(a):
            r = a & 0x3F
            return TABLE[(64 - r) if (a & 0x40) else r]
        self.assertDetected("missing_negation", lambda a: no_negate((a + 64) & 0xFF))


class CosWrapperSourceTests(unittest.TestCase):
    """The wrapper must stay a pure tail jump: binary add, P restored, no table, no zero-page use."""

    def code(self):
        return [line.split(";", 1)[0].rstrip() for line in COS_SOURCE.read_text(encoding="utf-8").splitlines()]

    def test_slot_46_is_a_dev_override_and_depends_on_slot_45(self):
        self.assertIn(0x46, overrides())
        self.assertIn(0x45, overrides())
        source = COS_SOURCE.read_text(encoding="utf-8")
        self.assertIn("api_slot_46:", source)
        self.assertIn(".import api_slot_45", source)

    def test_wrapper_contract(self):
        code = [c.strip() for c in self.code() if c.strip()]
        for needed in ("php", "cld", "clc", "adc #64", "plp", "jmp api_slot_45"):
            self.assertIn(needed, code, needed)
        self.assertNotIn("cos_lo:", COS_SOURCE.read_text(encoding="utf-8"))
        self.assertNotIn("cos_hi:", COS_SOURCE.read_text(encoding="utf-8"))

    def test_wrapper_mutants_are_rejected(self):
        """Source-level mutants of the wrapper: each must fail the contract (a no-op mutant is rejected too)."""
        code = [c.strip() for c in self.code() if c.strip()]
        required = ("php", "cld", "clc", "adc #64", "plp", "jmp api_slot_45")
        mutants = {
            "no_plp (P not restored)": [c for c in code if c != "plp"],
            "no_cld (decimal add)": [c for c in code if c != "cld"],
            "phase_63": [c.replace("adc #64", "adc #63") for c in code],
            "jump_to_fallback": [c.replace("jmp api_slot_45", "jmp api_slot_44") for c in code],
        }
        for name, lines in mutants.items():
            missing = [n for n in required if n not in lines]
            self.assertTrue(missing, f"mutant {name} was not rejected")
        # no-op mutant: the unchanged wrapper must satisfy the contract
        self.assertEqual([n for n in required if n not in code], [])

    def test_no_zero_page_operands(self):
        operands = set()
        for line in self.code():
            if line.strip() and not line.strip().startswith("."):
                operands.update(re.findall(r"(?<![#\w])\$([0-9A-Fa-f]{2})\b", line))
        self.assertEqual(operands, set())


if __name__ == "__main__":
    unittest.main()
