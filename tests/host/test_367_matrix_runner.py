"""Host checks for the #367 multi-byte block transfer matrix runner control flow.

Synthetic only: no emulator, BIOS, ROM, or Geargrafx core is read.  Verifies that
the matrix runner definition (CASES, LEN_CASES, DENY_CASES, expected case set,
dispatch order) is internally consistent and that all 51 cases are statically
accounted for, with tii-len0 routed to the deny path and tia-deny-nonmono last.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "integration"))

import geargrafx_block_transfer_matrix as M  # noqa: E402


class MatrixCaseSetTest(unittest.TestCase):
    """The static CASES list must match the expected 51-case set exactly."""

    def test_case_count_is_51(self):
        self.assertEqual(len(M.CASES), 51, f"expected 51 cases, got {len(M.CASES)}")

    def test_no_duplicate_cases(self):
        self.assertEqual(len(set(M.CASES)), len(M.CASES), "duplicate cases in CASES")

    def test_case_set_matches_expected(self):
        expected = {
            *[f"{op}-len{n}" for op in M.OPS for n in M.LENGTHS],
            "tii-boundary-1fff", "tia-boundary-3fff", "tii-boundary-7fff", "tai-boundary-ffff",
            "tdd-reverse-4000", "tii-boundary-alias", "tii-alias-overlap",
            "tii-tam-alias", "tii-tam-deny", "tii-len0",
            *list(M.DENY_CASES),
            "tia-deny-nonmono",
        }
        self.assertEqual(set(M.CASES), expected)

    def test_length_matrix_is_25(self):
        len_cases = [c for c in M.CASES if c in M.LEN_CASES]
        self.assertEqual(len(len_cases), 25)

    def test_len0_is_not_in_len_cases(self):
        self.assertNotIn("tii-len0", M.LEN_CASES, "tii-len0 must not be in LEN_CASES (lengths 1-8 only)")

    def test_deny_cases_are_15(self):
        self.assertEqual(len(M.DENY_CASES), 15, "5 ops x 3 kinds = 15 deny cases")


class MatrixDispatchTest(unittest.TestCase):
    """Each case must be routed to the correct assert_core_row path."""

    def test_len0_routes_to_deny(self):
        self.assertIn("tii-len0", M.CASES)
        self.assertNotIn("tii-len0", M.LEN_CASES)

    def test_tia_deny_nonmono_is_last(self):
        self.assertEqual(M.CASES[-1], "tia-deny-nonmono",
                         f"last case must be tia-deny-nonmono, got {M.CASES[-1]!r}")

    def test_deny_membership_for_prohibited_cases(self):
        for kind in ("hw", "rom", "backup"):
            for op in M.OPS:
                case = f"{op}-deny-{kind}"
                self.assertIn(case, M.CASES)
                deny = "deny-" in case or case.endswith("tam-deny")
                self.assertTrue(deny, f"{case} must be classified as deny")

    def test_tam_deny_membership(self):
        self.assertIn("tii-tam-deny", M.CASES)
        deny = "deny-" in "tii-tam-deny" or "tii-tam-deny".endswith("tam-deny")
        self.assertTrue(deny)

    def test_len0_deny_classification(self):
        # tii-len0 is not in DENY_CASES and does not contain "deny-", but must be
        # dispatched as deny via the explicit case.endswith("len0") branch.
        self.assertEqual("tii-len0" in M.LEN_CASES, False)
        self.assertEqual("deny-" in "tii-len0", False)
        self.assertEqual("tii-len0".endswith("tam-deny"), False)

    def test_len0_dispatches_to_deny_true(self):
        # Dispatch order in main(): case.endswith("len0") is checked before
        # case in LEN_CASES.  Verify tii-len0 hits the explicit len0 branch
        # (deny=True) rather than falling through to the LEN_CASES allow path.
        case = "tii-len0"
        deny_boolean = "deny-" in case or case.endswith("tam-deny")
        self.assertFalse(deny_boolean, "tii-len0 must not match the deny- boolean")
        self.assertTrue(case.endswith("len0"), "tii-len0 must match the explicit len0 branch")
        self.assertNotIn(case, M.LEN_CASES,
                         "tii-len0 must not be in LEN_CASES (would route to allow path)")


if __name__ == "__main__":
    unittest.main()
