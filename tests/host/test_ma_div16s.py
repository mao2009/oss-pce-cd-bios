"""MA_DIV16S (Issue #76): host model and judge checks, core-free. Model: tools/ma_div16s_model.py."""
from pathlib import Path
import random
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import ma_div16s_model as div  # noqa: E402


def u(value):
    return value & 0xFFFF


class ModelTest(unittest.TestCase):
    def test_signed_division_and_remainder(self):
        cases = {(7, 2): (3, 1), (-7, 2): (-3, -1), (7, -2): (-3, 1), (-7, -2): (3, -1),
                 (0, 5): (0, 0), (-1, 2): (0, -1), (32767, 1): (32767, 0)}
        for (a, b), (q, r) in cases.items():
            got_q, got_r, c, v, _, _ = div.expected(u(a), u(b))
            self.assertEqual((got_q, got_r, c, v), (u(q), u(r), 0, 0), (a, b))

    def test_minimum_boundary(self):
        self.assertEqual(div.expected(u(-32768), u(1))[:4], (u(-32768), 0, 0, 0))
        self.assertEqual(div.expected(u(-32768), u(-1))[:4], (0x8000, 0, 0, 1))
        self.assertEqual(div.expected(u(-32768), u(-32768))[:4], (1, 0, 0, 0))
        self.assertEqual(div.expected(u(32767), u(-32768))[:4], (0, 32767, 0, 0))
        self.assertEqual(div.expected(u(-32767), u(-32768))[:4], (0, u(-32767), 0, 0))

    def test_divide_by_zero_sets_carry_and_keeps_dividend(self):
        for a in (0, 1, u(-1), 0x8000, 0x7FFF):
            q, r, c, v, n, z = div.expected(a, 0)
            self.assertEqual((q, r, c, v, n, z), (0, a, 1, 0, 0, 1), hex(a))

    def test_flags_follow_quotient(self):
        self.assertEqual(div.expected(u(-2), u(1))[4:], (1, 0))   # negative quotient: N
        self.assertEqual(div.expected(u(1), u(2))[4:], (0, 1))    # zero quotient: Z
        self.assertEqual(div.expected(u(-3), u(-1))[4:], (0, 0))

    def test_identity_and_remainder_bound_on_grid(self):
        values = [-32768, -32767, -256, -7, -2, -1, 0, 1, 2, 7, 255, 16384, 32766, 32767]
        rng = random.Random(76)
        values += [rng.randint(-32768, 32767) for _ in range(40)]
        for a in values:
            for b in values:
                if b == 0:
                    continue
                q, r, c, v, _, _ = div.expected(u(a), u(b))
                self.assertEqual(c, 0)
                if v:
                    self.assertEqual((a, b), (-32768, -1))
                    continue
                qs, rs = div.signed(q), div.signed(r)
                self.assertEqual(qs * b + rs, a, (a, b))
                self.assertLess(abs(rs), abs(b))
                self.assertTrue(rs == 0 or (rs < 0) == (a < 0), (a, b))

    def test_judge_rejects_each_field(self):
        good = div.expected(u(-100), u(7))
        self.assertEqual(div.check_case(u(-100), u(7), good), [])
        for index in range(6):
            bad = list(good)
            bad[index] ^= 0x0001
            self.assertEqual(len(div.check_case(u(-100), u(7), tuple(bad))), 1, index)


if __name__ == "__main__":
    unittest.main()
