"""MA_MUL8S (Issue #74): host model, judge negatives and the real-core exhaustive run."""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "integration"))
import geargrafx_ma_mul8s as mul  # noqa: E402


class ModelTest(unittest.TestCase):
    def test_edge_products(self):
        cases = {(-128, -128): 0x4000, (-128, 127): 0xC080, (127, -128): 0xC080,
                 (-128, -1): 0x0080, (-1, -1): 0x0001, (-1, 1): 0xFFFF, (127, 127): 0x3F01,
                 (1, -128): 0xFF80, (0, -128): 0x0000, (-128, 0): 0x0000, (1, 1): 0x0001}
        for (a, b), product in cases.items():
            self.assertEqual(mul.expected(a & 0xFF, b & 0xFF), product, (a, b))

    def test_all_pairs_fit_signed_16_bit(self):
        for a in range(256):
            for b in range(256):
                value = mul.expected(a, b)
                value = value - 0x10000 if value & 0x8000 else value
                self.assertEqual(value, mul.signed(a) * mul.signed(b))
                self.assertTrue(-16256 <= value <= 16384)

    def test_judge_rejects_corruption(self):
        good = bytearray(mul.expected_row(0x80) + bytes(512))
        for b in range(256):
            good[0x400 + b] = good[0x500 + b] = (b & 1) | (b & 2) << 2 | 0x04
        self.assertEqual(mul.check_row(0x80, bytes(good)), [])
        for offset in (0x000, 0x1FF, 0x200, 0x300, 0x400):
            bad = bytearray(good)
            bad[offset] ^= 0x01
            self.assertEqual(len(mul.check_row(0x80, bytes(bad))), 1, hex(offset))


class RealCoreTest(unittest.TestCase):
    def test_exhaustive_pairs_on_geargrafx(self):
        """Real Geargrafx execution of the assembled routine; missing tools fail, never skip."""
        with tempfile.TemporaryDirectory(prefix="ma-mul8s-") as directory:
            evidence = mul.run(Path(directory))
        self.assertEqual((evidence["status"], evidence["pairs_checked"]), ("PASS", 65536))
        self.assertEqual(evidence["edge_products_hex"]["-128,-128"], "4000")
        self.assertEqual(evidence["edge_products_hex"]["-1,127"], "ff81")


if __name__ == "__main__":
    unittest.main()
