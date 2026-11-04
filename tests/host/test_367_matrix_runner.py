"""Host checks for the #367 multi-byte block transfer matrix runner control flow.

Synthetic only: no emulator, BIOS, ROM, or Geargrafx core is read.  Verifies that
the matrix runner definition (CASES, LEN_CASES, DENY_CASES, expected case set,
dispatch order) is internally consistent and that all 51 cases are statically
accounted for, with tii-len0 routed to the deny path and tia-deny-nonmono last.

Also verifies MPR immutability mutation definitions and fail-closed properties
(no Python `assert` for safety checks; explicit exceptions only).
"""
import contextlib
import io
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "integration"))

import geargrafx_block_transfer_matrix as M  # noqa: E402


def synthetic_row(*, length, deny=False, tam_alias=False):
    """Minimal synthetic probe row carrying only the observation fields the mutation suite reads."""
    n = length
    mpr = [0xF8] * 8
    if tam_alias:
        mpr[2] = 0xF9  # legal guest TAM MPR2 change, made before preflight
    return {
        "preflight_calls": 1,
        "preflight_allow": 0 if deny else 1,
        "preflight_deny": 1 if deny else 0,
        "byte_guard_calls": 0 if deny else n,
        "byte_src": [] if deny else [0x3E80 + i for i in range(n)],
        "preflight_src": [0x3E80 + i for i in range(n)],
        "preflight_dst": [0x3E90 + i for i in range(n)],
        "dst_bank": [0xF8] * n,
        "dest_values": [0x13 + i for i in range(n)],
        "mpr_before": list(mpr),
        "mpr_after": list(mpr),
        "preflight_mpr": list(mpr),
        "vdc_sr": [0, 0],
        "stack_wram_after": [0x34, 0x12, 0x07],
    }


def reject_all(broken, case, deny):
    raise ValueError("rejected")


def accept_all(broken, case, deny):
    return None


def ignoring(row, ignored):
    """Classifier that accepts only changes confined to `ignored` fields (models an unobserved field)."""
    def classify(broken, case, deny):
        for key in row:
            if key in ignored:
                continue
            if M._field_state(broken, key) != M._field_state(row, key):
                raise ValueError("rejected")
    return classify


def mpr_rule(broken, case, deny):
    """Classifier for MPR immutability only: preflight MPR snapshot must be an intact 8-entry list equal to mpr_after."""
    pre = broken.get("preflight_mpr")
    if type(pre) is not list or len(pre) != 8 or pre != broken["mpr_after"]:
        raise ValueError("MPR immutability violated")


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
                self.assertTrue(M.is_deny_case(case), f"{case} must be classified as deny")

    def test_tam_deny_membership(self):
        self.assertIn("tii-tam-deny", M.CASES)
        self.assertTrue(M.is_deny_case("tii-tam-deny"))
        self.assertFalse(M.is_deny_case("tii-tam-alias"))

    def test_len0_deny_classification(self):
        # tii-len0 is not in LEN_CASES and is not a "deny-" name, yet must be denied.
        self.assertNotIn("tii-len0", M.LEN_CASES)
        self.assertTrue(M.is_deny_case("tii-len0"))

    def test_len0_dispatches_to_deny_true(self):
        # Behavioural: the real deny predicate sends tii-len0 to the deny path.
        self.assertTrue(M.is_deny_case("tii-len0"))
        self.assertNotIn("tii-len0", M.LEN_CASES,
                         "tii-len0 must not be in LEN_CASES (would route to allow path)")

    def test_is_deny_case_matches_exact_18_deny_cases(self):
        expected = {*M.DENY_CASES, "tii-len0", "tii-tam-deny", "tia-deny-nonmono"}
        self.assertEqual(len(expected), 18)
        actual = {c for c in M.CASES if M.is_deny_case(c)}
        self.assertEqual(actual, expected)
        allowed = [c for c in M.CASES if not M.is_deny_case(c)]
        self.assertEqual(len(allowed), 33)
        self.assertFalse(any(c in expected for c in allowed))


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
        names = {name for name, _, _ in M.mutation_specs(synthetic_row(length=2), "tii-boundary-1fff", False)}
        for i in range(8):
            self.assertIn(f"MPR{i}_destroyed", names,
                          f"MPR{i}_destroyed mutation must be defined")
        self.assertIn("preflight_mpr_missing", names)
        self.assertIn("preflight_mpr_short", names)
        self.assertIn("preflight_mpr_type_wrong", names)

    def test_mpr_mutations_apply_to_len0(self):
        # MPR snapshot destruction mutations must NOT be excluded for length=0.
        # Behavioural check on the real definitions (synthetic length=0 row).
        row = synthetic_row(length=0, deny=True)
        excluded = {e["name"] for e in M.run_mutation_suite(row, "tii-len0", True, classify=reject_all)["excluded"]}
        self.assertEqual(excluded, {"partial_destination_result", "MPR_alias_resolution"})
        for i in range(8):
            self.assertNotIn(f"MPR{i}_destroyed", excluded)
        for name in ("preflight_mpr_missing", "preflight_mpr_short", "preflight_mpr_type_wrong"):
            self.assertNotIn(name, excluded)


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


class MutationAuditTest(unittest.TestCase):
    """Mutation counting: defined = excluded + applied, applied = detected, no duplicates or no-ops."""

    def test_normal_case_is_20_0_20(self):
        res = M.run_mutation_suite(synthetic_row(length=2), "tii-boundary-1fff", False, classify=reject_all)
        self.assertEqual((res["defined"], len(res["excluded"]), len(res["applied"]), len(res["detected"])), (20, 0, 20, 20))

    def test_len0_is_20_2_18_and_transfer_byte_count_applies(self):
        res = M.run_mutation_suite(synthetic_row(length=0, deny=True), "tii-len0", True, classify=reject_all)
        self.assertEqual((res["defined"], len(res["excluded"]), len(res["applied"]), len(res["detected"])), (20, 2, 18, 18))
        self.assertIn("transfer_byte_count", res["applied"])
        self.assertEqual(M.expected_mutation_counts("tii-len0"), (20, 2, 18))

    def test_tam_is_20_1_19_with_unobserved_stack_exclusion(self):
        row = synthetic_row(length=2, tam_alias=True)
        res = M.run_mutation_suite(row, "tii-tam-alias", False, classify=ignoring(row, {"stack_wram_after"}))
        self.assertEqual((res["defined"], len(res["excluded"]), len(res["applied"]), len(res["detected"])), (20, 1, 19, 19))
        self.assertEqual(res["excluded"][0]["name"], "TransferEnd_stack")
        self.assertEqual(res["excluded"][0]["kind"], "unobserved")
        self.assertEqual(M.expected_mutation_counts("tii-tam-deny"), (20, 0, 20))

    def test_tam_deny_refusal_checks_stack_frame(self):
        # The refusal branch compares stack_wram_after with before, so TransferEnd_stack is observed.
        row = synthetic_row(length=2, deny=True, tam_alias=True)
        res = M.run_mutation_suite(row, "tii-tam-deny", True, classify=reject_all)
        self.assertEqual((res["defined"], len(res["excluded"]), len(res["applied"]), len(res["detected"])), (20, 0, 20, 20))
        self.assertIn("TransferEnd_stack", res["applied"])

    def test_len0_duplicate_mutation_is_removed(self):
        names = [name for name, _, _ in M.mutation_specs(synthetic_row(length=0, deny=True), "tii-len0", True)]
        self.assertNotIn("len0_mutation_guard", names)
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(len(names), 20)

    def test_duplicate_observation_under_new_name_is_rejected(self):
        row = synthetic_row(length=0, deny=True)
        specs = M.mutation_specs(row, "tii-len0", True) + [("len0_mutation_guard", {"preflight_calls": 0}, None)]
        with self.assertRaisesRegex(ValueError, "duplicates preflight_callback_missing"):
            M.run_mutation_suite(row, "tii-len0", True, classify=reject_all, specs=specs)

    def test_same_name_twice_is_rejected(self):
        row = synthetic_row(length=2)
        specs = M.mutation_specs(row, "tii-boundary-1fff", False)
        specs.append(specs[0])
        with self.assertRaisesRegex(ValueError, "duplicate"):
            M.run_mutation_suite(row, "tii-boundary-1fff", False, classify=reject_all, specs=specs)

    def test_undeclared_noop_is_rejected(self):
        specs = [("noop_mut", {"preflight_calls": 1}, None)]
        with self.assertRaisesRegex(ValueError, "unexpected_noop"):
            M.run_mutation_suite(synthetic_row(length=2), "tii-boundary-1fff", False, classify=reject_all, specs=specs)

    def test_declared_noop_that_changes_observation_is_rejected(self):
        specs = [("bogus_noop", {"preflight_calls": 0}, ("noop", "claimed no-op"))]
        with self.assertRaisesRegex(ValueError, "declared no-op but changed"):
            M.run_mutation_suite(synthetic_row(length=2), "tii-boundary-1fff", False, classify=reject_all, specs=specs)

    def test_excluded_is_not_counted_as_applied(self):
        specs = [("real_noop", {"preflight_calls": 1}, ("noop", "no-op by design")),
                 ("real", {"preflight_calls": 0}, None)]
        res = M.run_mutation_suite(synthetic_row(length=2), "tii-boundary-1fff", False, classify=reject_all, specs=specs)
        self.assertEqual([e["name"] for e in res["excluded"]], ["real_noop"])
        self.assertEqual(res["applied"], ["real"])
        self.assertEqual(res["detected"], ["real"])
        self.assertEqual(res["defined"], 2)

    def test_applied_detected_mismatch_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "accepted"):
            M.run_mutation_suite(synthetic_row(length=2), "tii-boundary-1fff", False, classify=accept_all)

    def test_unobserved_exclusion_contradicted_by_classifier_is_rejected(self):
        row = synthetic_row(length=2, tam_alias=True)
        # reject_all detects the stack change, so the TAM 'unobserved' declaration is wrong.
        with self.assertRaisesRegex(ValueError, "declared unobserved but classifier rejects"):
            M.run_mutation_suite(row, "tii-tam-alias", False, classify=reject_all)

    def test_totals_invariants(self):
        res = M.run_mutation_suite(synthetic_row(length=2), "tii-boundary-1fff", False, classify=reject_all)
        totals = M.mutation_totals([res])
        self.assertEqual(totals["defined"], totals["excluded"] + totals["applied"])
        self.assertEqual(totals["applied"], totals["detected"])
        self.assertEqual((totals["duplicate"], totals["unexpected_noop"], totals["accepted"]), (0, 0, 0))

    def test_mpr_mutations_all_applied_and_detected(self):
        for row, case, deny in ((synthetic_row(length=2), "tii-boundary-1fff", False),
                                (synthetic_row(length=0, deny=True), "tii-len0", True),
                                (synthetic_row(length=2, tam_alias=True), "tii-tam-alias", False)):
            specs = [s for s in M.mutation_specs(row, case, deny)
                     if s[0].endswith("_destroyed") or s[0].startswith("preflight_mpr")]
            self.assertEqual(len(specs), 11, case)
            res = M.run_mutation_suite(row, case, deny, classify=mpr_rule, specs=specs)
            self.assertEqual(len(res["detected"]), 11, case)
            self.assertEqual(res["excluded"], [], case)

    def test_mpr_rule_does_not_flag_legal_tam_mpr2_change(self):
        mpr_rule(synthetic_row(length=2, tam_alias=True), "tii-tam-alias", False)  # must not raise

    def test_fail_closed_under_python_O(self):
        script = (
            "import sys\n"
            f"sys.path.insert(0, {str(ROOT / 'tests' / 'integration')!r})\n"
            "import geargrafx_block_transfer_matrix as M\n"
            "def reject(b, c, d):\n"
            "    raise ValueError('rejected')\n"
            "specs = [('a', {'preflight_calls': 0}, None), ('b', {'preflight_calls': 0}, None)]\n"
            "try:\n"
            "    M.run_mutation_suite({'preflight_calls': 1}, 'tii-boundary-1fff', False, classify=reject, specs=specs)\n"
            "except ValueError:\n"
            "    sys.exit(3)\n"
            "sys.exit(0)\n"
        )
        import subprocess
        proc = subprocess.run([sys.executable, "-O", "-c", script], capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 3, proc.stderr)


class MainDispatchTest(unittest.TestCase):
    """Runs the real main() loop with the emulator, probe build, and mutation suite replaced by recorders.

    Observes the deny value actually passed to assert_core_row and mutation_test for each of the 51 cases.
    """

    def _run_main_recording(self):
        core_deny, mut_deny = {}, {}
        fake_row = {
            "preflight_calls": 1, "preflight_deny": 0, "byte_guard_calls": 0,
            "src_wram_index": [0, 1, 2], "dst_wram_index": [1, 2, 3], "dest_values": [0x11, 0x11, 0x11],
        }
        applied = [f"m{i}" for i in range(20)]

        def fake_core(row, case, *, deny=False):
            core_deny[case] = deny

        def fake_mut(row, case, deny=False):
            mut_deny[case] = deny
            return {"case": case, "deny": deny, "defined": 20, "excluded": [], "applied": applied,
                    "detected": applied, "accepted": [], "duplicate": [], "unexpected_noop": [],
                    "exclusion_violation": []}

        with mock.patch.object(M, "build_probes", return_value=None), \
             mock.patch.object(M, "synthetic_media", return_value=(Path("bios"), Path("cue"))), \
             mock.patch.object(M, "run", side_effect=lambda *a: dict(fake_row)), \
             mock.patch.object(M, "assert_core_row", side_effect=fake_core), \
             mock.patch.object(M, "mutation_test", side_effect=fake_mut), \
             contextlib.redirect_stdout(io.StringIO()):
            M.main()
        return core_deny, mut_deny

    def test_main_passes_is_deny_case_to_mutation_suite_for_all_51(self):
        core_deny, mut_deny = self._run_main_recording()
        self.assertEqual(set(mut_deny), set(M.CASES))
        self.assertEqual(len(mut_deny), 51)
        for case in M.CASES:
            self.assertEqual(mut_deny[case], M.is_deny_case(case), f"mutation deny mismatch for {case}")

    def test_main_len0_mutation_and_classifier_are_denied(self):
        core_deny, mut_deny = self._run_main_recording()
        self.assertTrue(mut_deny["tii-len0"])
        self.assertTrue(core_deny["tii-len0"])

    def test_main_classifier_deny_matches_predicate_for_all_51(self):
        core_deny, _ = self._run_main_recording()
        for case in M.CASES:
            # main() passes deny=True explicitly for len0 and deny cases; allowed cases use the default False.
            self.assertEqual(core_deny[case], M.is_deny_case(case), f"classifier deny mismatch for {case}")


if __name__ == "__main__":
    unittest.main()
