"""MA_MUL16S design oracle (Issue #479): boundary products, step model vs integer model, judge and mutant negatives."""
from pathlib import Path
import random
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import ma_mul16s_model as m  # noqa: E402

EDGES = (0x0000, 0x0001, 0xFFFF, 0x0002, 0xFFFE, 0x7FFF, 0x8001, 0x8000, 0x00FF, 0x0100, 0xFF00, 0xFF01,
         0x5555, 0xAAAA, 0x1234, 0xEDCC)


class OracleTest(unittest.TestCase):
    def test_boundary_products(self):
        cases = {(-32768, -32768): 0x40000000, (-32768, 32767): 0xC0008000, (32767, -32768): 0xC0008000,
                 (32767, 32767): 0x3FFF0001, (-1, -1): 1, (-1, 1): 0xFFFFFFFF, (-1, 32767): 0xFFFF8001,
                 (-32768, -1): 0x8000, (-32768, 1): 0xFFFF8000, (1, 1): 1, (0, -32768): 0, (-32768, 0): 0}
        for (a, b), want in cases.items():
            self.assertEqual(m.expected(a & 0xFFFF, b & 0xFFFF), want, (a, b))
            self.assertEqual(m.shift_add(a, b), want, (a, b))

    def test_range_and_models_agree(self):
        rng = random.Random(479)
        pairs = [(a, b) for a in EDGES for b in EDGES] + [(rng.randrange(65536), rng.randrange(65536)) for _ in range(20000)]
        for a, b in pairs:
            want = m.expected(a, b)
            self.assertEqual(m.shift_add(a, b), want, (a, b))
            sv = want - (1 << 32) if want >> 31 else want
            self.assertTrue(-1073709056 <= sv <= 0x40000000, (a, b))

    def test_frame_order(self):
        self.assertEqual(m.frame(0x40302010), (0x10, 0x20, 0x30, 0x40))

    def test_mutants_are_detected(self):  # negative controls: each correction removed must fail on a reduced domain
        for kw, neg_operand in (({"skip_a_fix": True}, 0), ({"skip_b_fix": True}, 1)):
            bad = sum(m.shift_add(a, b, **kw) != m.expected(a, b) for a in EDGES for b in EDGES)
            want = sum(((a, b)[neg_operand] & 0x8000) != 0 and (a, b)[1 - neg_operand] != 0 for a in EDGES for b in EDGES)
            self.assertEqual(bad, want)
            self.assertGreater(bad, 0)
        unsigned = sum(m.shift_add(a, b, True, True) != m.expected(a, b) for a in EDGES for b in EDGES)
        self.assertGreater(unsigned, 0)

    def test_judge(self):
        good = dict(got_frame=m.frame(m.expected(0x8000, 0xFFFF)), y_in=0x5A, y_out=0x5A, s_in=0xF9, s_out=0xF9,
                    p_in=0x09, p_out=0x19)  # B differs only
        self.assertEqual(m.check_case(0x8000, 0xFFFF, **good), [])
        for key, val in (("y_out", 0), ("s_out", 0xF8), ("p_out", 0x0A), ("p_out", 0x29), ("p_in", 0x29)):
            bad = dict(good, **{key: val})
            self.assertEqual(len(m.check_case(0x8000, 0xFFFF, **bad)), 1, key)
        for bit in (0x01, 0x02, 0x04, 0x08, 0x40, 0x80):  # C, Z, I, D, V, N each detected
            self.assertEqual(len(m.check_case(0x8000, 0xFFFF, **dict(good, p_out=0x09 ^ bit))), 1, bit)
        for i in range(4):
            f = list(good["got_frame"])
            f[i] ^= 1
            self.assertEqual(len(m.check_case(0x8000, 0xFFFF, **dict(good, got_frame=f))), 1, i)


if __name__ == "__main__":
    unittest.main()
