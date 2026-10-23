"""Fail-closed verdict gate for the synthetic BM_FORMAT model (Issue #303). Synthetic only."""
import ast
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import bm_format_model as bm  # noqa: E402
import bm_format_verdict as v  # noqa: E402

MODULE_PATH = Path(__file__).resolve().parents[2] / "tools" / "bm_format_verdict.py"


class GateTest(unittest.TestCase):
    def test_blank_image_passes_through_to_model(self):
        self.assertEqual(v.guarded_format(bytes(64), allow_destructive=True)[0], bm.OK)

    def test_nonzero_invalid_signature_is_rejected_and_kept(self):
        image = v._corrupt()
        status, out = v.guarded_format(image, allow_destructive=True)
        self.assertEqual(status, v.REJECTED_INVALID_SIGNATURE)
        self.assertEqual(out, image)

    def test_unconfirmed_invalid_signature_is_refused_by_model_first(self):
        status, _ = v.guarded_format(v._corrupt(), allow_destructive=False)
        self.assertEqual(status, bm.REFUSED_UNCONFIRMED)

    def test_short_invalid_image_is_capacity_not_signature(self):
        status, _ = v.guarded_format(b"\xAA" * (bm.MIN_CAPACITY - 1), allow_destructive=True)
        self.assertEqual(status, bm.INSUFFICIENT_CAPACITY)

    def test_63_byte_valid_signature_is_capacity_not_already_formatted(self):
        status, out = v.guarded_format(v._formatted(bm.MIN_CAPACITY - 1), allow_destructive=True)
        self.assertEqual(status, bm.INSUFFICIENT_CAPACITY)
        self.assertEqual(len(out), bm.MIN_CAPACITY - 1)

    def test_non_bytes_still_raises(self):
        with self.assertRaises(TypeError):
            v.guarded_format([0] * 64, allow_destructive=True)


class JudgeTest(unittest.TestCase):
    def test_matching_result_passes(self):
        img = bytes(64)
        self.assertEqual(v.judge(bm.OK, bm.OK, img, v._formatted()), v.PASS)

    def test_none_expectation_is_not_run(self):
        self.assertEqual(v.judge(None, bm.OK, bytes(64), bytes(64)), v.NOT_RUN)

    def test_missing_result_fails_closed(self):
        self.assertEqual(v.judge(bm.OK, None, bytes(64), None), v.FAIL)

    def test_unexpected_status_fails(self):
        self.assertEqual(v.judge(bm.OK, bm.ALREADY_FORMATTED, bytes(64), bytes(64)), v.FAIL)

    def test_refused_path_that_changed_bytes_fails(self):
        img = bytes(64)
        self.assertEqual(v.judge(bm.REFUSED_UNCONFIRMED, bm.REFUSED_UNCONFIRMED, img,
                                 b"\x01" + img[1:]), v.FAIL)

    def test_formatted_output_with_wrong_size_fails(self):
        self.assertEqual(v.judge(bm.OK, bm.OK, bytes(64), bytes(63)), v.FAIL)

    def test_ok_without_written_signature_fails(self):
        # Length-only match is not enough: an OK that never writes the signature must be FAIL.
        self.assertEqual(v.judge(bm.OK, bm.OK, bytes(64), bytes(64)), v.FAIL)

    def test_ok_that_returns_input_unchanged_fails(self):
        img = b"\xAA" * 64
        self.assertEqual(v.judge(bm.OK, bm.OK, img, img), v.FAIL)

    def test_ok_with_nonzero_body_fails(self):
        out = bytearray(v._formatted())
        out[40] = 0x01
        self.assertEqual(v.judge(bm.OK, bm.OK, bytes(64), bytes(out)), v.FAIL)

    def test_non_bytes_out_fails_without_raising(self):
        self.assertEqual(v.judge(bm.OK, bm.OK, bytes(64), 5), v.FAIL)
        self.assertEqual(v.judge(bm.OK, bm.OK, bytes(64), "OK"), v.FAIL)

    def test_bogus_status_matching_expectation_is_fail(self):
        # BOGUS_STATUS == BOGUS_STATUS must not reach the refused-path PASS branch.
        img = bytes(64)
        self.assertEqual(v.judge("BOGUS_STATUS", "BOGUS_STATUS", img, img), v.FAIL)
        self.assertEqual(v.judge("", "", img, img), v.FAIL)

    def test_allowed_refused_status_with_unchanged_bytes_passes(self):
        img = bytes(64)
        self.assertEqual(v.judge(bm.REFUSED_UNCONFIRMED, bm.REFUSED_UNCONFIRMED, img, img), v.PASS)
        self.assertEqual(v.judge(v.REJECTED_INVALID_SIGNATURE, v.REJECTED_INVALID_SIGNATURE,
                                 img, img), v.PASS)

    def test_unhashable_expectation_fails_without_raising(self):
        self.assertEqual(v.judge(["OK"], ["OK"], bytes(64), bytes(64)), v.FAIL)

    def test_exception_while_judging_output_is_fail(self):
        class Explodes(bytearray):
            def __len__(self):
                raise RuntimeError("boom")

        self.assertEqual(v.judge(bm.OK, bm.OK, bytes(64), Explodes(64)), v.FAIL)


class RunAllTest(unittest.TestCase):
    def test_all_cases_pass_but_overall_is_blocked_abi(self):
        report = v.run_all()
        self.assertTrue(all(r["verdict"] == v.PASS for r in report["cases"]), report)
        self.assertEqual(report["overall"], v.BLOCKED_ABI)
        self.assertEqual(report["abi"], "UNKNOWN")
        self.assertFalse(report["release_eligible"])

    def test_exception_in_model_is_fail_not_pass(self):
        with mock.patch.object(bm, "format_image", side_effect=RuntimeError("boom")):
            report = v.run_all([("x", bytes(64), True, bm.OK)])
        self.assertEqual(report["cases"][0]["verdict"], v.FAIL)
        self.assertEqual(report["overall"], v.FAIL)

    def test_empty_or_all_not_run_is_not_run(self):
        self.assertEqual(v.run_all([])["overall"], v.NOT_RUN)
        self.assertEqual(v.run_all([("n", bytes(64), True, None)])["overall"], v.NOT_RUN)

    def test_snapshot_failure_is_fail_not_exception(self):
        # bytes(object()) raises before the model is called; run_case must return FAIL.
        result = v.run_case("unsnapshottable", object(), True, bm.OK)
        self.assertEqual(result["verdict"], v.FAIL)

    def test_bogus_expectation_with_model_bogus_status_is_fail(self):
        with mock.patch.object(v, "guarded_format", return_value=("BOGUS_STATUS", bytes(64))):
            result = v.run_case("bogus", bytes(64), True, "BOGUS_STATUS")
        self.assertEqual(result["verdict"], v.FAIL)

    def test_mutant_model_ok_without_signature_is_fail(self):
        # Mutation: the model reports OK and returns a same-length image with no signature written.
        mutant = lambda image, *, allow_destructive=False: (bm.OK, bytes(len(image)))  # noqa: E731
        with mock.patch.object(bm, "format_image", side_effect=mutant):
            report = v.run_all([("mutant", bytes(64), True, bm.OK)])
        self.assertEqual(report["cases"][0]["verdict"], v.FAIL)
        self.assertEqual(report["overall"], v.FAIL)

    def test_mutant_model_bad_return_shape_is_fail(self):
        for bad in ("OK", (bm.OK, None), (bm.OK,)):
            with self.subTest(bad=bad), mock.patch.object(bm, "format_image", return_value=bad):
                report = v.run_all([("bad", bytes(64), True, bm.OK)])
                self.assertEqual(report["cases"][0]["verdict"], v.FAIL)

    def test_input_buffer_mutation_fails(self):
        def mutating(image, *, allow_destructive=False):
            image[0] = 0x7F
            return bm.OK, bytes(image)

        with mock.patch.object(v, "guarded_format", side_effect=mutating):
            result = v.run_case("m", bytearray(64), True, bm.OK)
        self.assertEqual(result["verdict"], v.FAIL)

    def test_verdict_module_imports_only_the_model(self):
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
        self.assertLessEqual(imported, {"bm_format_model", "json"}, imported)  # json: __main__ print only


class FailClosedNegativeTest(unittest.TestCase):
    """PR #509 negative cases (Issue #303). Synthetic only. GAP tests are expectedFailure on head."""

    def test_expected_type_errors_are_never_pass(self):
        # Guard: non-str expectations (bool, int, bytes, digit strings) never match an allowed status.
        img = bytes(64)
        for expected in (True, 1, 0, 1.0, b"OK", "0", "1", [], {}, object()):
            with self.subTest(expected=expected):
                self.assertNotEqual(v.judge(expected, expected, img, img), v.PASS)
                self.assertEqual(v.judge(expected, bm.OK, img, v._formatted()), v.FAIL)

    def test_expected_whose_equality_raises_is_fail(self):
        # Guard: an exception from the expected value's __eq__ is caught inside judge().
        class RaisingEq:
            def __hash__(self):
                return hash(bm.OK)

            def __eq__(self, other):
                raise RuntimeError("boom")

        self.assertEqual(v.judge(RaisingEq(), bm.OK, bytes(64), v._formatted()), v.FAIL)

    @unittest.expectedFailure  # GAP on head: equality-only membership lets an always-equal proxy pass.
    def test_expected_always_equal_proxy_is_fail(self):
        class AlwaysEqual:
            def __hash__(self):
                return hash(bm.OK)

            def __eq__(self, other):
                return True

            def __ne__(self, other):
                return False

        img = bytes(64)
        self.assertEqual(v.judge(AlwaysEqual(), bm.REFUSED_UNCONFIRMED, img, img), v.FAIL)

    def test_guarded_bytearray_invalid_signature_is_rejected_and_kept(self):
        image = bytearray(v._corrupt())
        status, out = v.guarded_format(image, allow_destructive=True)
        self.assertEqual(status, v.REJECTED_INVALID_SIGNATURE)
        self.assertIsInstance(out, bytes)
        self.assertEqual(out, bytes(image))

    def test_guarded_truthy_non_true_flag_never_formats_invalid_signature(self):
        for flag in (1, "yes", b"x", [1]):
            with self.subTest(flag=flag):
                image = v._corrupt()
                status, out = v.guarded_format(image, allow_destructive=flag)
                self.assertEqual(status, bm.REFUSED_UNCONFIRMED)
                self.assertEqual(out, image)

    def test_ok_with_dirty_reserved_header_byte_fails(self):
        # Zero-fill covers the reserved header bytes after the signature, not only the body.
        out = bytearray(v._formatted())
        out[bm.HEADER_LEN - 1] = 0x01
        self.assertEqual(v.judge(bm.OK, bm.OK, bytes(64), bytes(out)), v.FAIL)

    def test_ok_with_any_signature_byte_off_fails(self):
        for index in range(len(bm.SIGNATURE)):
            with self.subTest(index=index):
                out = bytearray(v._formatted())
                out[index] ^= 0x01
                self.assertEqual(v.judge(bm.OK, bm.OK, bytes(64), bytes(out)), v.FAIL)

    def test_input_buffer_mutated_on_refused_path_fails(self):
        # Only the buffer check can fail here: the returned bytes equal the original snapshot.
        original = bytes(64)

        def mutating(image, *, allow_destructive=False):
            image[0] = 0x7F
            return bm.REFUSED_UNCONFIRMED, original

        with mock.patch.object(v, "guarded_format", side_effect=mutating):
            result = v.run_case("m", bytearray(64), False, bm.REFUSED_UNCONFIRMED)
        self.assertEqual(result["verdict"], v.FAIL)

    def test_non_bytes_buffer_protocol_input_is_fail(self):
        # memoryview snapshots fine but is rejected by the model; the exception must become FAIL.
        self.assertEqual(v.run_case("mv", memoryview(bytes(64)), True, bm.OK)["verdict"], v.FAIL)

    def test_snapshot_failure_makes_overall_fail(self):
        report = v.run_all([("unsnap", object(), True, bm.OK)])
        self.assertEqual(report["overall"], v.FAIL)
        self.assertNotEqual(report["overall"], v.PASS)

    def test_invalid_judge_return_never_makes_overall_pass(self):
        # Guard: a non-verdict from judge() must never surface as an overall PASS.
        for bad in (None, True, "PASS"):
            with self.subTest(bad=bad), mock.patch.object(v, "judge", return_value=bad):
                report = v.run_all([("a", bytes(64), True, bm.OK)])
                self.assertNotEqual(report["overall"], v.PASS)

    @unittest.expectedFailure  # GAP on head: run_case counts a None verdict as a ran case, not FAIL.
    def test_invalid_judge_return_is_fail_per_case(self):
        with mock.patch.object(v, "judge", return_value=None):
            report = v.run_all([("a", bytes(64), True, bm.OK)])
        self.assertEqual(report["cases"][0]["verdict"], v.FAIL)

    @unittest.expectedFailure  # GAP on head: run_case returns judge()'s verdict even after a model exception.
    def test_model_exception_cannot_be_overridden_by_judge_return(self):
        with mock.patch.object(bm, "format_image", side_effect=RuntimeError("boom")), \
                mock.patch.object(v, "judge", return_value=v.PASS):
            report = v.run_all([("a", bytes(64), True, bm.OK)])
        self.assertEqual(report["cases"][0]["verdict"], v.FAIL)


if __name__ == "__main__":
    unittest.main()
