"""MA_DIV16U DEV contract (Issue #478): core-free checks of the independent oracle tools/ma_div16u_model.py.

The real-core judge is tests/integration/geargrafx_dev_div16u.py. These tests pin the oracle itself, so that the
real-core expectation is not wrong in the same way as the routine.
"""
from pathlib import Path
import random
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import ma_div16u_model as div  # noqa: E402


class OracleTest(unittest.TestCase):
    def test_unsigned_quotient_and_remainder(self):
        self.assertEqual(div.expected(100, 7)[:2], (14, 2))
        self.assertEqual(div.expected(0x1234, 0x0100)[:2], (0x12, 0x34))
        self.assertEqual(div.expected(0xFFFF, 0x0001)[:2], (0xFFFF, 0))
        self.assertEqual(div.expected(0xFFFF, 0xFFFF)[:2], (1, 0))
        self.assertEqual(div.expected(0x8000, 0xFFFF)[:2], (0, 0x8000))

    def test_no_sign_interpretation(self):
        # $FFFF is 65535 unsigned, not -1: the quotient must be large, not a negative value.
        self.assertEqual(div.expected(0xFFFF, 0x0002)[0], 0x7FFF)

    def test_divide_by_zero_is_the_project_contract(self):
        q, r, c, v, n, z = div.expected(0x1234, 0)
        self.assertEqual((q, r, c, v), (0xFFFF, 0x1234, 1, 0))
        self.assertEqual((n, z), (1, 0))
        self.assertEqual(div.expected(0, 0)[:3], (0xFFFF, 0, 1))

    def test_carry_and_overflow_flags_for_non_zero_divisor(self):
        for n, d in ((5, 3), (0xFFFF, 0xFFFF), (0, 1)):
            self.assertEqual(div.expected(n, d)[2:4], (0, 0), (n, d))

    def test_negative_flag_follows_quotient_bit_15(self):
        self.assertEqual(div.expected(0xFFFF, 0x0001)[4], 1)
        self.assertEqual(div.expected(0x7FFF, 0x0001)[4], 0)

    def test_zero_flag_follows_quotient(self):
        self.assertEqual(div.expected(3, 7)[5], 1)
        self.assertEqual(div.expected(7, 3)[5], 0)

    def test_quotient_times_divisor_plus_remainder_restores_dividend(self):
        rng = random.Random(478)
        for _ in range(2000):
            n, d = rng.randrange(0x10000), rng.randrange(1, 0x10000)
            q, r = div.expected(n, d)[:2]
            self.assertEqual(q * d + r, n, (n, d))
            self.assertLess(r, d, (n, d))

    def test_check_case_names_each_differing_field(self):
        good = div.expected(9, 4)
        self.assertEqual(div.check_case(9, 4, good[:6]), [])
        self.assertEqual(len(div.check_case(9, 4, (good[0] ^ 1,) + good[1:6])), 1)


if __name__ == "__main__":
    unittest.main()
