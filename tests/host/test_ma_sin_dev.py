"""Host model of the DEV MA_SIN override (slot $45, Issue #79). Exhaustive over all 256 angles.

The model mirrors src/overrides/slot_45.s: quarter-wave table lookup with quadrant folding and sign.
  r        = angle & 63
  index    = r if bit 6 clear else 64 - r
  value    = T[index]                       (T from the .byte table in slot_45.s)
  result   = -value if bit 7 set else value
The independent oracle is trunc(127 * math.sin(2*pi*angle/256)) (no table, no folding).

Contract (DEV-only, project's own spec): binary angle, 256 units per turn; output signed, scale 127,
range -127..+127. This does NOT test the assembled bytes; the real-core run is not part of this test
(see the PR's open items). This is not the System Card MA_SIN ABI, which stays UNVERIFIED.
"""
import math
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from build_rom import overrides  # noqa: E402

SOURCE = ROOT / "src" / "overrides" / "slot_45.s"


def table_from_source(text=None):
    """The 65 quarter-wave bytes from the `.byte` lines after the `sin_quarter:` label."""
    text = SOURCE.read_text(encoding="utf-8") if text is None else text
    body = text.split("sin_quarter:", 1)[1]
    values = []
    for line in body.splitlines():
        line = line.split(";", 1)[0].strip()
        if not line.startswith(".byte"):
            continue
        values.extend(int(v) for v in line[len(".byte"):].split(","))
    return values


def dev_sin(angle, mutant=None):
    """Model of slot_45.s for angle 0..255. `mutant` names a deliberately wrong variant (negative tests)."""
    if type(angle) is not int or not 0 <= angle <= 255:
        raise ValueError(f"angle must be an integer 0..255, got {angle!r}")
    table = TABLE
    r = angle & 63
    if mutant == "no_fold":
        index = r                      # odd quadrants read T[r] instead of T[64 - r]
    else:
        index = (64 - r) if (angle & 0x40) else r
    value = table[index]
    if angle & 0x80:
        value = -value
    if mutant == "no_sign":
        value = abs(value)
    return value


def oracle_sin(angle):
    """Independent oracle: trunc(127 * sin(2*pi*angle/256)), truncation toward zero (int())."""
    return int(127 * math.sin(2 * math.pi * angle / 256))


TABLE = table_from_source()


class DevSinModelTests(unittest.TestCase):
    def test_table_has_65_entries_and_matches_oracle_quarter(self):
        """The source table equals trunc(127*sin(pi/2*q/64)) for q = 0..64 (generated values)."""
        self.assertEqual(len(TABLE), 65)
        expected = [int(127 * math.sin(math.pi / 2 * q / 64)) for q in range(65)]
        self.assertEqual(TABLE, expected)
        self.assertEqual(TABLE[0], 0)
        self.assertEqual(TABLE[64], 127)

    def test_all_256_angles_match_oracle(self):
        """Every 8-bit angle: model == independent oracle (exhaustive)."""
        for a in range(256):
            self.assertEqual(dev_sin(a), oracle_sin(a), a)

    def test_oracle_margin_is_far_from_truncation_boundaries(self):
        """Float oracle is trustworthy: no non-exact value lies within 1e-6 of an integer."""
        for a in range(256):
            if a in (0, 64, 128, 192):
                continue
            v = 127 * math.sin(2 * math.pi * a / 256)
            self.assertGreater(abs(v - round(v)), 1e-6, a)

    def test_quadrant_boundaries(self):
        """Quadrant edges: 0, 63/64, 127/128, 191/192, 255."""
        # Hand-checked values (also equal to the oracle).
        cases = {0: 0, 1: 3, 63: 126, 64: 127, 65: 126, 128: 0, 129: -3,
                 191: -126, 192: -127, 193: -126}
        for a, want in cases.items():
            self.assertEqual(dev_sin(a), want, a)
            self.assertEqual(oracle_sin(a), want, a)
        for a in (127, 255):  # just before a half-turn / quarter boundary
            self.assertEqual(dev_sin(a), oracle_sin(a), a)

    def test_zero_max_min(self):
        """Zero at 0/128; max +127 only at 64; min -127 only at 192; never -128."""
        self.assertEqual(dev_sin(0), 0)
        self.assertEqual(dev_sin(128), 0)
        values = [dev_sin(a) for a in range(256)]
        self.assertEqual(max(values), 127)
        self.assertEqual(min(values), -127)
        self.assertEqual([a for a in range(256) if values[a] == 127], [64])
        self.assertEqual([a for a in range(256) if values[a] == -127], [192])
        self.assertNotIn(-128, values)

    def test_period_and_symmetry(self):
        """Period 256 (mod 256 by the 8-bit input); half-turn antisymmetry; quarter mirror."""
        for a in range(256):
            self.assertEqual(dev_sin(a), dev_sin(a & 0xFF))
            self.assertEqual(dev_sin((a + 128) & 0xFF), -dev_sin(a), a)
            self.assertEqual(dev_sin((64 - a) & 0xFF), dev_sin((64 + a) & 0xFF), a)

    def test_negative_half_turn_signs(self):
        """Negative inputs (128..255) give non-positive output; 0..128 give non-negative output."""
        for a in range(128, 256):
            self.assertLessEqual(dev_sin(a), 0, a)
        for a in range(0, 129):
            self.assertGreaterEqual(dev_sin(a), 0, a)

    def test_out_of_range_inputs_rejected(self):
        """Negative test: inputs outside 0..255 (or non-int) are rejected, never wrapped silently."""
        for bad in (-1, 256, 512, 1.0, "64", None):
            with self.assertRaises(ValueError, msg=repr(bad)):
                dev_sin(bad)

    def test_mutants_are_detected(self):
        """Negative test: plausible wrong routines (no quadrant fold, no sign) differ from the oracle."""
        for mutant in ("no_fold", "no_sign"):
            wrong = [a for a in range(256) if dev_sin(a, mutant) != oracle_sin(a)]
            self.assertTrue(wrong, mutant)

    def test_slot_45_is_a_dev_override(self):
        self.assertIn(0x45, overrides())
        source = SOURCE.read_text(encoding="utf-8")
        self.assertIn("api_slot_45:", source)
        self.assertIn("MA_SIN", source)
        self.assertIn("Issue #79", source)
        self.assertIn("release_eligible: false", source)
        self.assertIn(".segment \"OVERRIDES\"", source)
        # Zero page $F7-$F8 only; no writes to ZP $00-$07 (used by adt_dma_routine.s).
        self.assertIn("$F7", source)
        self.assertIn("$F8", source)
        self.assertIsNone(re.search(r"\bst[azx]\s+\$0[0-7]\b", source))
        # Entry sequence saves and the exit sequence restores P and X; the routine ends in rts.
        body = source.split("api_slot_45:", 1)[1].split("; T[q] = trunc", 1)[0]
        self.assertTrue(body.lstrip().startswith("php"))
        self.assertIn("plx", body)
        self.assertIn("plp", body)
        self.assertTrue(body.rstrip().endswith("rts"))


if __name__ == "__main__":
    unittest.main()
