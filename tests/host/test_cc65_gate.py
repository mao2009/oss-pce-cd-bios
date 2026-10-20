"""Gate contract for cc65-dependent host tests (PR #351, Issue #332).

Only tests that really need cc65 are skipped when it is missing; every other test always runs.
In required mode (OSS_PCE_REQUIRE_CC65=1, set by `make test`) a missing cc65 is a FAILURE, never a SKIP.
"""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
MISSING = {name: "/nonexistent/cc65-gate-test/" + name.lower() for name in ("CA65", "LD65", "CC65", "DA65")}

# Driver run in a child interpreter: reports one status per test id, so skips are visible per test.
DRIVER = r'''
import json, sys, unittest
root, names = sys.argv[1], sys.argv[2:]
sys.path[:0] = [root, root + "/tests/host"]
rows = []
class Recorder(unittest.TestResult):
    def addSuccess(self, test): super().addSuccess(test); rows.append([test.id(), "ok", ""])
    def addSkip(self, test, reason): super().addSkip(test, reason); rows.append([test.id(), "skipped", reason])
    def addFailure(self, test, err): super().addFailure(test, err); rows.append([test.id(), "FAIL", ""])
    def addError(self, test, err): super().addError(test, err); rows.append([test.id(), "ERROR", ""])
result = Recorder()
unittest.defaultTestLoader.loadTestsFromNames(names).run(result)
print("ROWS=" + json.dumps(rows))
'''


def run_modules(names, env_updates=None):
    env = dict(os.environ)
    env.pop("OSS_PCE_REQUIRE_CC65", None)
    env.update(MISSING)
    env.update(env_updates or {})
    with tempfile.TemporaryDirectory(prefix="cc65-gate-driver-") as directory:
        driver = Path(directory) / "driver.py"
        driver.write_text(DRIVER)
        proc = subprocess.run([sys.executable, str(driver), str(ROOT), *names], capture_output=True,
                              text=True, env=env, cwd=directory, timeout=300)
    lines = [line for line in proc.stdout.splitlines() if line.startswith("ROWS=")]
    if not lines:
        raise AssertionError(f"driver produced no rows (exit {proc.returncode}): {proc.stderr[-2000:]}")
    return {test_id: (status, reason) for test_id, status, reason in json.loads(lines[-1][len("ROWS="):])}


class Cc65GateContractTests(unittest.TestCase):
    maxDiff = None

    def test_missing_cc65_skips_only_dependent_tests(self):
        rows = run_modules(["test_api_build", "test_build", "test_cdread", "test_ex_binbcd"])
        # cc65-independent tests run and pass, including ones that used to sit in a skipped class.
        for test_id in ("test_build.BuildTests.test_invalid_mode_fails_before_output",
                        "test_ex_binbcd.ExBinBcdPackedModelTests.test_boundary_vectors",
                        "test_cdread.JudgeTests.test_accepts_correct_single_and_multi_sector_reads",
                        "test_api_build.ApiContractTests.test_invalid_mode",
                        "test_api_build.ApiContractTests.test_release_gate_missing_and_all_overrides"):
            self.assertEqual(rows.get(test_id, ("absent", ""))[0], "ok", test_id)
        # cc65-dependent tests are skipped with an explicit cc65 reason.
        for test_id in ("test_build.BuildTests.test_tools_present",
                        "test_cdread.CdReadBuildTests.test_symbols_and_vectors",
                        "test_api_build.ApiBuildTests.test_actual_image_structure"):
            status, reason = rows[test_id]
            self.assertEqual(status, "skipped", test_id)
            self.assertIn("cc65", reason)
        bad = {k: v for k, v in rows.items() if v[0] in ("FAIL", "ERROR")}
        # Windows hosts also error on out-of-scope platform issues (os.getuid, cp932 reads, no make): see the PR.
        if sys.platform.startswith("linux"):
            self.assertEqual(bad, {}, "tests must not fail when cc65 is absent")

    def test_required_mode_turns_dependent_skips_into_failures(self):
        rows = run_modules(["test_build", "test_cdread"], {"OSS_PCE_REQUIRE_CC65": "1"})
        for test_id in ("test_build.BuildTests.test_tools_present",
                        "test_cdread.CdReadBuildTests.test_symbols_and_vectors"):
            self.assertIn(rows[test_id][0], ("FAIL", "ERROR"), test_id)
        self.assertEqual([k for k, v in rows.items() if v[0] == "skipped"], [], "no SKIP may pass in required mode")
        self.assertEqual(rows["test_cdread.JudgeTests.test_accepts_correct_single_and_multi_sector_reads"][0], "ok")

    def test_cc65_present_runs_gated_body(self):
        from tools.environment import requires_cc65

        ran = []

        class Gated(unittest.TestCase):
            @requires_cc65
            def test_dependent(self):
                ran.append(self.id())

        with patch.dict(os.environ, {"OSS_PCE_REQUIRE_CC65": ""}),                 patch("tools.environment.executable", return_value="/fake/cc65-tool"):
            result = unittest.TestResult()
            unittest.defaultTestLoader.loadTestsFromTestCase(Gated).run(result)
        self.assertEqual((result.skipped, result.failures, result.errors), ([], [], []))
        self.assertEqual(len(ran), 1)

    def test_independent_failure_is_detected_when_cc65_missing(self):
        from tools.environment import requires_cc65

        class Mixed(unittest.TestCase):
            @requires_cc65
            def test_dependent(self):
                self.fail("must be skipped, not run")

            def test_independent_failure(self):
                self.assertEqual(1, 2)

        with patch.dict(os.environ, {"OSS_PCE_REQUIRE_CC65": ""}),                 patch("tools.environment.executable", side_effect=ValueError("missing tool ca65")):
            result = unittest.TestResult()
            unittest.defaultTestLoader.loadTestsFromTestCase(Mixed).run(result)
        self.assertEqual(len(result.skipped), 1)
        self.assertIn("cc65", result.skipped[0][1])
        self.assertEqual([t.id().rsplit(".", 1)[1] for t, _ in result.failures], ["test_independent_failure"])
        self.assertEqual(result.errors, [])

    def test_required_missing_cc65_is_failure_not_skip(self):
        from tools.environment import requires_cc65

        class Gated(unittest.TestCase):
            @requires_cc65
            def test_dependent(self):
                pass

        with patch.dict(os.environ, {"OSS_PCE_REQUIRE_CC65": "1"}), \
                patch("tools.environment.executable", side_effect=ValueError("missing tool ca65")):
            result = unittest.TestResult()
            unittest.defaultTestLoader.loadTestsFromTestCase(Gated).run(result)
        self.assertEqual(result.skipped, [])
        self.assertEqual(len(result.failures) + len(result.errors), 1)

    def test_cc65_independent_tests_in_gated_modules_always_run(self):
        # Tests that call only pure helpers must not sit inside a @requires_cc65 class (the gate is per class).
        rows = run_modules(["test_cdload_exec", "test_reset_entry", "test_api_build"])
        for test_id in ("test_cdload_exec.CdLoadExecContractTests.test_payload_size_limits_are_enforced",
                        "test_reset_entry.ResetEntryContractTests.test_only_sourced_init_no_guessed_state",
                        "test_api_build.ApiContractTests.test_subdirectory_of_unrelated_repo_is_not_attributed",
                        "test_api_build.ApiContractTests.test_bad_override_filename"):
            self.assertEqual(rows.get(test_id, ("absent", ""))[0], "ok", test_id)

    def test_makefile_test_target_is_required_and_host_test_needs_no_build(self):
        text = (ROOT / "Makefile").read_text()
        test_rule = re.search(r"^test:[^\n]*\n((?:\t[^\n]*\n?)+)", text, re.M)
        self.assertIsNotNone(test_rule)
        self.assertIn("OSS_PCE_REQUIRE_CC65=1", test_rule.group(1))
        host_rule = re.search(r"^host-test:([^\n]*)\n((?:\t[^\n]*\n?)+)", text, re.M)
        self.assertIsNotNone(host_rule, "host-only target missing")
        self.assertEqual(host_rule.group(1).strip(), "", "host-test must not depend on build")
        self.assertIn("unittest discover -s tests/host", host_rule.group(2))
        self.assertNotIn("OSS_PCE_REQUIRE_CC65=1", host_rule.group(2))


if __name__ == "__main__":
    unittest.main()
