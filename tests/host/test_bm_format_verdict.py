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

    def test_non_bytes_still_raises(self):
        with self.assertRaises(TypeError):
            v.guarded_format([0] * 64, allow_destructive=True)


class JudgeTest(unittest.TestCase):
    def test_matching_result_passes(self):
        img = bytes(64)
        self.assertEqual(v.judge(bm.OK, bm.OK, img, bytes(64)), v.PASS)

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


if __name__ == "__main__":
    unittest.main()
