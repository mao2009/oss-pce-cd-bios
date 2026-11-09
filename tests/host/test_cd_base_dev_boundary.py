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

    def test_side_effects_stay_unknown_even_with_callee_claim(self):
        m = B.side_effect_matrix(True)
        self.assertTrue(all(v == "UNKNOWN" for v in m.values()))

    def test_release_not_eligible(self):
        self.assertFalse(B.RELEASE_ELIGIBLE)

    def test_plan_rejects_bool(self):
        with self.assertRaises(ValueError):
            B.plan_fixture(True)

    def test_plan_rejects_out_of_range(self):
        with self.assertRaises(ValueError):
            B.plan_fixture(0x1000000)

    def test_unknown_mode_blocked_even_with_callee(self):
        r = B.judge({"mode": "UNKNOWN_X", "block": 1, "callee_observed": True})
        self.assertEqual(r["verdict"], "BLOCKED")
        self.assertEqual(r["reason"], "UNKNOWN_MODE")

    def test_invalid_block_blocked_even_with_callee(self):
        r = B.judge({"mode": "ABS_LBA", "block": -1, "callee_observed": True})
        self.assertEqual(r["verdict"], "BLOCKED")
        self.assertEqual(r["reason"], "INVALID_BLOCK")

    def test_no_callee_truthy_non_true_blocked(self):
        for v in (1, "yes", [True]):
            r = B.judge({"mode": "ABS_LBA", "block": 1, "callee_observed": v})
            self.assertEqual(r["verdict"], "BLOCKED", v)
            self.assertEqual(r["reason"], "NO_CALLEE", v)

    def test_callee_true_stays_blocked_not_observed(self):
        r = B.judge({"mode": "ABS_LBA", "block": 1, "callee_observed": True})
        self.assertEqual(r["verdict"], "BLOCKED")
        self.assertEqual(r["reason"], "NO_TRUSTED_OBSERVATION")
        self.assertNotEqual(r["verdict"], "OBSERVED")
        self.assertNotEqual(r.get("verdict"), "VERIFIED")

    def test_judge_never_emits_verified_or_observed(self):
        cases = [
            {"mode": "ABS_LBA", "block": 1, "callee_observed": True},
            {"mode": "INDEX01_REL", "block": 0, "callee_observed": True},
            {"mode": "ABS_LBA", "block": 1},
            {"mode": "UNKNOWN_X", "block": 1, "callee_observed": True},
            {"mode": "ABS_LBA", "block": -1, "callee_observed": True},
            "not a dict",
        ]
        for c in cases:
            self.assertNotEqual(B.judge(c).get("verdict"), "VERIFIED", c)
            self.assertNotEqual(B.judge(c).get("verdict"), "OBSERVED", c)

    def test_plan_release_not_eligible(self):
        self.assertFalse(B.plan_fixture(150)["release_eligible"])
        self.assertFalse(B.plan_fixture(0)["release_eligible"])

    def test_side_effects_truthy_non_true_unknown(self):
        for v in (1, "yes", None):
            m = B.side_effect_matrix(v)
            self.assertTrue(all(x == "UNKNOWN" for x in m.values()), v)

    def test_index01_150_fixture_label(self):
        self.assertEqual(B.plan_fixture(150)["fixture"], "INDEX01=150")


if __name__ == "__main__":
    unittest.main()
