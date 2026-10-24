"""Host model of the DEV MA_SQRT override (slot $44, Issue #78). Exhaustive over all 65,536 inputs.

The model mirrors src/overrides/slot_44.s step by step: a 16-bit running square, an
8-bit root candidate, and a 16-bit trial value (2r + 1). Each round compares the
running square against the input, updates the best root, then advances r and trial.
Every input 0..65535 is checked against math.isqrt (the independent oracle).

This does NOT test the assembled bytes; the assembled routine is exercised only by
the real-core run in tests/integration/geargrafx_dev_sqrt.py. This is not the
System Card MA_SQRT ABI.
"""
import math
import random
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from build_rom import overrides  # noqa: E402


def dev_sqrt(n):
    """floor(sqrt(n)) computed as slot_44.s does, for n in 0..65535.

    State: r (8-bit root candidate), sq (16-bit running square), trial (16-bit, 2r+1).
    Each round:
      if sq > n: stop, return best
      best = r
      sq = sq + trial        (16-bit wrap)
      r = r + 1              (8-bit wrap)
      if r == 0: stop, return best   (wrapped past 255)
      trial = trial + 2      (16-bit wrap)
    """
    r = 0
    sq = 0
    trial = 1
    best = 0
    while True:
        if sq > n:
            return best
        best = r
        sq = (sq + trial) & 0xFFFF
        r = (r + 1) & 0xFF
        if r == 0:
            return best
        trial = (trial + 2) & 0xFFFF


class DevSqrtModelTests(unittest.TestCase):
    def test_all_inputs_exhaustive(self):
        """Every 16-bit input: model == math.isqrt (exhaustive, 65,536 cases)."""
        for n in range(65536):
            self.assertEqual(math.isqrt(n), dev_sqrt(n), n)

    def test_boundary_and_edge_values(self):
        """Explicit boundary / perfect-square / non-perfect-square / max cases."""
        cases = [
            0, 1, 2, 3, 4,
            15, 16, 17,
            254, 255, 256, 257,
            1023, 1024, 1025,
            32767, 32768,
            65024, 65025, 65026,
            65534, 65535,
        ]
        for n in cases:
            self.assertEqual(math.isqrt(n), dev_sqrt(n), n)

    def test_output_range(self):
        """Every output lies in 0..255 and X would be 0."""
        for n in range(65536):
            r = dev_sqrt(n)
            self.assertTrue(0 <= r <= 255, n)
            self.assertEqual(r, math.isqrt(n), n)

    def test_overflow_at_max_root(self):
        """r=255: sq=65025, trial=511, sq+trial=65536 overflows; r wraps to 0;
        best=255 is correctly returned for n in [65025, 65535]."""
        for n in range(65025, 65536):
            self.assertEqual(255, dev_sqrt(n), n)
        # Just below the range: floor(sqrt(65024)) = 254
        self.assertEqual(254, dev_sqrt(65024))

    def test_slot_44_is_a_dev_override(self):
        self.assertIn(0x44, overrides())
        source = (ROOT / "src" / "overrides" / "slot_44.s").read_text(encoding="utf-8")
        self.assertIn("api_slot_44:", source)
        self.assertIn("MA_SQRT", source)
        self.assertIn("Issue #78", source)
        # Uses zero page $F8-$FF (not stack page, not ZP $00-$07).
        self.assertIn("$F7", source)
        self.assertIn("$F8", source)
        self.assertIn("$FF", source)
        self.assertNotIn("sta $00", source)
        self.assertNotIn("sta $01", source)


if __name__ == "__main__":
    unittest.main()
