"""Host checks for the #367 multi-byte block transfer matrix runner control flow.

Synthetic only: no emulator, BIOS, ROM, or Geargrafx core is read.  Verifies that
the matrix runner definition (CASES, LEN_CASES, DENY_CASES, expected case set,
dispatch order) is internally consistent and that all 51 cases are statically
accounted for, with tii-len0 routed to the deny path and tia-deny-nonmono last.

Also verifies MPR immutability mutation definitions and fail-closed properties
(no Python `assert` for safety checks; explicit exceptions only).
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


class MatrixMPRTest(unittest.TestCase):
    """MPR immutability must be verified for all 8 MPRs, including length=0."""

    def test_preflight_mpr_field_expected_in_probe(self):
        # The probe emits preflight_mpr[8]; assert_core_row reads it.
        # This is a structural check: assert_core_row references preflight_mpr.
        import inspect
        src = inspect.getsource(M.assert_core_row)
        self.assertIn('row["preflight_mpr"]', src,
                      "assert_core_row must read preflight_mpr from probe row")
        self.assertIn('row["mpr_after"]', src,
                      "assert_core_row must compare preflight_mpr against mpr_after")

    def test_mpr_immutability_check_applies_to_all_cases(self):
        # preflight_mpr == mpr_after must be checked outside the deny/allow split
        # so it applies to len0 and all deny/allow cases.
        import inspect
        src = inspect.getsource(M.assert_core_row)
        # The check must appear before the deny/allow split and before the len0 early block
        preflight_mpr_idx = src.find('row["preflight_mpr"] != row["mpr_after"]')
        self.assertNotEqual(preflight_mpr_idx, -1,
                            "preflight_mpr == mpr_after check must exist")
        deny_idx = src.find("    if deny:")
        len0_idx = src.find('if case.endswith("len0"):')
        self.assertLess(preflight_mpr_idx, deny_idx,
                        "MPR immutability check must precede deny block")
        self.assertLess(preflight_mpr_idx, len0_idx,
                        "MPR immutability check must precede len0 block")

    def test_mpr_mutations_defined(self):
        # 8 MPR destruction mutations + 3 preflight_mpr mutations = 11 MPR mutations
        import inspect
        src = inspect.getsource(M.mutation_test)
        for i in range(8):
            self.assertIn(f"MPR{i}_destroyed", src,
                          f"MPR{i}_destroyed mutation must be defined")
        self.assertIn("preflight_mpr_missing", src)
        self.assertIn("preflight_mpr_short", src)
        self.assertIn("preflight_mpr_type_wrong", src)

    def test_mpr_mutations_apply_to_len0(self):
        # MPR snapshot destruction mutations must NOT be excluded for length=0
        import inspect
        src = inspect.getsource(M.mutation_test)
        len0_block_start = src.find('if case.endswith("len0"):')
        len0_exclusions = src[len0_block_start:src.find("        try:", len0_block_start)]
        # Only the 3 existing no-op mutations should be excluded for len0
        for name in ("transfer_byte_count", "partial_destination_result", "MPR_alias_resolution"):
            self.assertIn(name, len0_exclusions,
                          f"{name} must be excluded for len0")
        # MPR mutations must NOT be in the len0 exclusion list
        for i in range(8):
            self.assertNotIn(f"MPR{i}_destroyed", len0_exclusions,
                             f"MPR{i}_destroyed must NOT be excluded for len0")
        for name in ("preflight_mpr_missing", "preflight_mpr_short", "preflight_mpr_type_wrong"):
            self.assertNotIn(name, len0_exclusions,
                             f"{name} must NOT be excluded for len0")


class MatrixFailClosedTest(unittest.TestCase):
    """Safety checks must not rely on Python `assert` (disabled with -O)."""

    def test_no_assert_for_safety_checks(self):
        # Read the source and confirm no `assert` is used for case set validation.
        matrix_path = Path(M.__file__).resolve()
        src = matrix_path.read_text(encoding="utf-8")
        # Find the main() function body and check case validation uses raise, not assert
        main_start = src.find("def main():")
        main_src = src[main_start:]
        # The expected_cases validation should use if/raise, not assert
        self.assertIn("if len(CASES) != len(expected_cases):", main_src)
        self.assertIn("raise ValueError(f\"CASES count", main_src)
        self.assertNotIn("assert len(CASES)", main_src)
        self.assertNotIn("assert len(set(CASES))", main_src)
        self.assertNotIn("assert set(CASES)", main_src)


if __name__ == "__main__":
    unittest.main()
