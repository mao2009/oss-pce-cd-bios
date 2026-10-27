"""Host model of the DEV MA_COS override (slot $46, Issue #80). Exhaustive over all 256 angle indices.

The model mirrors src/overrides/slot_46.s: quadrant folding of a 65-entry Q15 quarter-wave table.
  k = A (0..255), r = k & 63, q = k >> 6
  index = 64 - r for odd q, r for even q; negate for q in {1, 2}
  result = signed 16-bit Q15, value = round(32767 * cos(2*pi*k/256))
The oracle is math.cos directly (not the table), so the table and the folding are both checked.

This does NOT run the assembled bytes; the real-core run is tests/integration/geargrafx_dev_cos.py.
This is not the System Card MA_COS ABI (UNVERIFIED). The SIN contract is not on main, so no
sin/cos cross-check is made here.
"""
import math
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from build_rom import overrides  # noqa: E402

SOURCE = ROOT / "src" / "overrides" / "slot_46.s"


def q15_round(k):
    """round(32767 * cos(2*pi*k/256)) as an integer; no tie is possible (cos is irrational except at 0/64/128/192)."""
    return math.floor(32767 * math.cos(2 * math.pi * k / 256) + 0.5)


TABLE = [q15_round(i) for i in range(65)]  # T[0..64]


def source_table():
    """(lo, hi) byte lists parsed from the cos_lo / cos_hi .byte lines of slot_46.s."""
    text = SOURCE.read_text(encoding="utf-8")
    lo_block = text.split("cos_lo:", 1)[1].split("cos_hi:", 1)[0]
    hi_block = text.split("cos_hi:", 1)[1]
    parse = lambda block: [int(h, 16) for h in re.findall(r"\$([0-9A-Fa-f]{2})", block)]
    return parse(lo_block), parse(hi_block)


def dev_cos(k, table=TABLE):
    """Signed 16-bit result (as Python int) for angle index k, computed as slot_46.s does."""
    r = k & 0x3F
    odd = (k & 0x40) != 0
    index = (64 - r) if odd else r
    value = table[index]
    negate = ((k >> 6) ^ (k >> 7)) & 1
    if negate:
        value = -value
    return value


def as_word(value):
    return value & 0xFFFF


class DevCosModelTests(unittest.TestCase):
    def test_all_angles_exhaustive(self):
        """Every angle index 0..255: model == round(32767*cos(2*pi*k/256)) (independent oracle)."""
        for k in range(256):
            self.assertEqual(dev_cos(k), q15_round(k), k)

    def test_quadrant_and_period_boundaries(self):
        """Quadrant edges and the wrap points: 0, 63..65, 127..129, 191..193, 255."""
        expect = {0: 32767, 64: 0, 128: -32767, 192: 0, 255: q15_round(1), 1: q15_round(1)}
        for k, want in expect.items():
            self.assertEqual(dev_cos(k), want, k)
        for k in (63, 65, 127, 129, 191, 193):
            self.assertEqual(dev_cos(k), q15_round(k), k)

    def test_symmetry(self):
        """cos is even: cos(k) == cos(256-k) for k in 1..255; quarter-turn antisymmetry about 64 and 192."""
        for k in range(1, 256):
            self.assertEqual(dev_cos(k), dev_cos(256 - k), k)
        for d in range(0, 65):
            self.assertEqual(dev_cos(64 + d), -dev_cos(64 - d) if d else 0, d)

    def test_range_and_no_minus_32768(self):
        """Output stays in -32767..+32767; the 16-bit encoding never yields $8000."""
        for k in range(256):
            v = dev_cos(k)
            self.assertTrue(-32767 <= v <= 32767, k)
            self.assertNotEqual(as_word(v), 0x8000, k)

    def test_precision_error_bound(self):
        """Quantization error of the Q15 table is at most 0.5 LSB (|error| <= 0.5/32767 of full scale)."""
        worst = max(abs(dev_cos(k) - 32767 * math.cos(2 * math.pi * k / 256)) for k in range(256))
        self.assertLessEqual(worst, 0.5 + 1e-9)

    def test_source_table_matches_model(self):
        """The bytes in slot_46.s equal the model table (65 entries each, Q15 little split)."""
        lo, hi = source_table()
        self.assertEqual(len(lo), 65)
        self.assertEqual(len(hi), 65)
        for i, want in enumerate(TABLE):
            self.assertEqual(lo[i] | (hi[i] << 8), as_word(want), i)

    def test_negative_mutants_detected(self):
        """Mutants must differ from the oracle: missing negation, wrong quadrant index, off-by-one table."""
        def no_negate(k):
            r = k & 0x3F
            return TABLE[(64 - r) if (k & 0x40) else r]

        def wrong_index(k):
            r = k & 0x3F
            value = TABLE[r]  # ignores the odd-quadrant fold
            return -value if ((k >> 6) ^ (k >> 7)) & 1 else value

        bad_table = list(TABLE)
        bad_table[32] += 1
        mutants = {
            "no_negate": lambda k: no_negate(k),
            "wrong_index": wrong_index,
            "table_off_by_one": lambda k: dev_cos(k, bad_table),
        }
        for name, fn in mutants.items():
            differs = [k for k in range(256) if fn(k) != q15_round(k)]
            self.assertTrue(differs, f"mutant {name} was not detected")

    def test_slot_46_is_a_dev_override(self):
        self.assertIn(0x46, overrides())
        source = SOURCE.read_text(encoding="utf-8")
        self.assertIn("api_slot_46:", source)
        self.assertIn("MA_COS", source)
        self.assertIn("Issue #80", source)
        # Every zero-page operand in an instruction must be in $F7-$FA (immediates excluded).
        code_lines = [line.split(";", 1)[0] for line in source.splitlines()
                      if line.split(";", 1)[0].startswith("    ")
                      and not line.split(";", 1)[0].strip().startswith(".")]
        operands = set()
        for line in code_lines:
            operands.update(re.findall(r"(?<![#\w])\$([0-9A-Fa-f]{2})\b", line))
        self.assertTrue(operands <= {"F7", "F8", "F9", "FA"}, operands)
        self.assertNotIn("sta $00", source)
        self.assertNotIn("sta $01", source)




# --- Cross-identity with the MA_SIN contract (Issue #79, PR #522) -----------------------------
# MA_SIN is a separate, not-yet-merged PR, so src/overrides/slot_45.s may be absent on this
# branch. The comparison below therefore has two layers:
#   1. A Q15-scale identity that does NOT depend on slot_45.s: COS(a) must equal a Q15 sine at
#      (a + 64) mod 256, for all 256 angles. The sine is computed with math (independent oracle),
#      not by copying the cosine formula, so the identity is a real cross-check of the cosine model.
#   2. When slot_45.s IS present (after PR #522 lands), its quarter-wave table and folding are
#      compared against this cosine table: T_sin[i] must equal T_cos[64 - i], and the MA_SIN model
#      built from that table must agree with the cosine model on all 256 angles.
import os

SLOT45_PATH = ROOT / "src" / "overrides" / "slot_45.s"


def sin_q15_oracle(k):
    """Independent Q15 sine oracle: round(32767 * sin(2*pi*k/256))."""
    return math.floor(32767 * math.sin(2 * math.pi * k / 256) + 0.5)


def sin_table_from_cos():
    """MA_SIN's quarter-wave table derived from MA_COS's own table: T_sin[i] = T_cos[64 - i]."""
    return [TABLE[64 - i] for i in range(65)]


def dev_sin_from_table(k, table):
    """MA_SIN quadrant folding using an explicit 65-entry quarter-wave table."""
    r = k & 0x3F
    index = (64 - r) if (k & 0x40) else r
    value = table[index]
    if k & 0x80:
        value = -value
    return value


def parse_slot45_table():
    """Return the (lo, hi) byte lists from slot_45.s, or None if the file is absent."""
    if not SLOT45_PATH.exists():
        return None
    text = SLOT45_PATH.read_text(encoding="utf-8")
    lo_block = text.split("sin_lo:", 1)[1].split("sin_hi:", 1)[0]
    hi_block = text.split("sin_hi:", 1)[1]
    parse = lambda block: [int(h, 16) for h in __import__("re").findall(r"\$([0-9A-Fa-f]{2})", block)]
    return parse(lo_block), parse(hi_block)


class CrossIdentityTests(unittest.TestCase):
    def test_identity_all_256_q15(self):
        """SIN(a) == COS((a - 64) & 0xFF) for every angle, on the Q15 scale with the same rounding.

        Rearranged as COS(a) == SIN((a + 64) mod 256). The sine is an independent math oracle,
        so this is a genuine cross-check rather than a copy of the cosine formula.
        """
        bad = [a for a in range(256)
               if dev_cos(a) != sin_q15_oracle((a + 64) % 256)]
        self.assertEqual(bad, [])

    def test_identity_boundaries(self):
        """COS(a) == SIN((a+64) mod 256) at the quadrant boundaries 0, 64, 128, 192, 255."""
        for a in (0, 64, 128, 192, 255):
            self.assertEqual(dev_cos(a), sin_q15_oracle((a + 64) % 256), a)
        self.assertEqual([dev_cos(a) for a in (0, 64, 128, 192)], [32767, 0, -32767, 0])

    def test_sign_rule_matches_sine(self):
        """Sign of COS(a) equals sign of the Q15 sine at (a + 64) mod 256, for all 256 angles."""
        for a in range(256):
            self.assertEqual((dev_cos(a) > 0) - (dev_cos(a) < 0),
                             (sin_q15_oracle((a + 64) % 256) > 0) - (sin_q15_oracle((a + 64) % 256) < 0), a)

    def test_derived_sin_table_identity(self):
        """The MA_SIN table derived from MA_COS's table (T_sin[i]=T_cos[64-i]) reproduces the identity."""
        st = sin_table_from_cos()
        for a in range(256):
            self.assertEqual(dev_cos(a), dev_sin_from_table((a + 64) % 256, st), a)

    def test_mutants_detected_table_byte_quadrant_sign(self):
        """Mutants must differ from the oracle: flipped table byte, wrong quadrant index, missing negation."""
        bad_table = list(TABLE)
        bad_table[16] ^= 0x01
        mutants = {
            "table_byte_flip": lambda k: dev_cos(k, bad_table),
            "wrong_quadrant_index": lambda k: dev_cos(k, TABLE) if False else None,
        }
        # quadrant mutant: use r instead of 64-r for odd quadrants
        def wrong_quadrant(k):
            r = k & 0x3F
            index = r  # ignores odd-quadrant fold
            value = TABLE[index]
            if ((k >> 6) ^ (k >> 7)) & 1:
                value = -value
            return value
        # sign mutant: never negate
        def no_negate(k):
            r = k & 0x3F
            index = (64 - r) if (k & 0x40) else r
            return TABLE[index]
        mutants = {
            "table_byte_flip": lambda k: dev_cos(k, bad_table),
            "wrong_quadrant": wrong_quadrant,
            "missing_negation": no_negate,
        }
        for name, fn in mutants.items():
            differs = [k for k in range(256) if fn(k) != sin_q15_oracle((k + 64) % 256) * 0 + q15_round(k) and fn(k) != q15_round(k)]
            # A mutant is "detected" if it disagrees with the cosine oracle on at least one angle.
            differs = [k for k in range(256) if fn(k) != q15_round(k)]
            self.assertTrue(differs, f"mutant {name} was not detected")

    def test_actual_slot45_table_when_available(self):
        """If PR #522 has landed (slot_45.s present), its table and folding must match MA_COS."""
        parsed = parse_slot45_table()
        if parsed is None:
            self.skipTest("slot_45.s not present on this branch (PR #522 not merged); "
                          "cross-check deferred to integration branch")
        lo, hi = parsed
        self.assertEqual(len(lo), 65)
        self.assertEqual(len(hi), 65)
        sin_table = [(h << 8) | l for l, h in zip(lo, hi)]
        # T_sin[i] must equal T_cos[64 - i].
        for i in range(65):
            self.assertEqual(sin_table[i], TABLE[64 - i], i)
        # Full 256-angle identity using the actual MA_SIN table and folding.
        for a in range(256):
            r = a & 0x3F
            index = (64 - r) if (a & 0x40) else r
            value = sin_table[index]
            if a & 0x80:
                value = -value
            self.assertEqual(dev_cos((a - 64) & 0xFF), value, a)


if __name__ == "__main__":
    unittest.main()
