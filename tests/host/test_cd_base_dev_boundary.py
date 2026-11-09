"""Host tests for cd_base_dev_boundary (#735). No callee, synthetic only."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import cd_base_dev_boundary as B  # noqa: E402


class BoundaryTests(unittest.TestCase):
    def test_valid_block_passes(self):
        self.assertEqual(B.validate_block(150), 150)

    def test_out_of_range_rejected(self):
        with self.assertRaises(ValueError):
            B.validate_block(0x1000000)

    def test_bool_rejected(self):
        with self.assertRaises(ValueError):
            B.validate_block(True)

    def test_negative_rejected(self):
        with self.assertRaises(ValueError):
            B.validate_block(-1)

    def test_unknown_mode_blocked(self):
        self.assertEqual(B.judge({"mode": "UNKNOWN_X", "block": 1})["verdict"], "BLOCKED")

    def test_no_callee_blocked(self):
        self.assertEqual(B.judge({"mode": "ABS_LBA", "block": 1})["verdict"], "BLOCKED")

    def test_invalid_block_blocked(self):
        self.assertEqual(B.judge({"mode": "ABS_LBA", "block": -1})["verdict"], "BLOCKED")

    def test_bad_request_blocked(self):
        self.assertEqual(B.judge("not a dict")["verdict"], "BLOCKED")

    def test_index01_zero_collapses(self):
        self.assertTrue(B.plan_fixture(0)["index01_zero_collapses"])

    def test_index01_150_separates(self):
        self.assertFalse(B.plan_fixture(150)["index01_zero_collapses"])

    def test_side_effects_unknown_without_callee(self):
        m = B.side_effect_matrix(False)
        self.assertTrue(all(v == "UNKNOWN" for v in m.values()))

    def test_side_effects_observed_with_callee(self):
        m = B.side_effect_matrix(True)
        self.assertTrue(all(v == "OBSERVED" for v in m.values()))

    def test_release_not_eligible(self):
        self.assertFalse(B.RELEASE_ELIGIBLE)

    def test_plan_rejects_bool(self):
        with self.assertRaises(ValueError):
            B.plan_fixture(True)

    def test_plan_rejects_out_of_range(self):
        with self.assertRaises(ValueError):
            B.plan_fixture(0x1000000)


if __name__ == "__main__":
    unittest.main()
