"""Result gate: the shell semantics of the gate command (Issue #688 follow-up).

The gate in the four required guard jobs is `test "${{ needs.<upstream>.result }}" = "success"`. This module runs that
command in a shell for each upstream result value. Only SUCCESS passes. The rule that accepts the gate lives in the
trusted guard on main (the migration rule, stage 1). The rule tests and the strict rule (stage 3) are NOT in this branch,
so that merging this branch does not change the trusted guard.
"""
import shutil
import subprocess
import unittest


class GateSemanticsTests(unittest.TestCase):
    def setUp(self):
        self.sh = shutil.which("sh") or shutil.which("bash")
        if not self.sh:
            self.skipTest("no POSIX shell available to run the gate command")

    def code(self, result):
        return subprocess.run([self.sh, "-c", f'test "{result}" = "success"'], capture_output=True).returncode

    def test_success_passes(self):
        self.assertEqual(self.code("success"), 0)

    def test_failure_fails(self):
        self.assertNotEqual(self.code("failure"), 0)

    def test_cancelled_fails(self):
        self.assertNotEqual(self.code("cancelled"), 0)

    def test_skipped_fails(self):
        self.assertNotEqual(self.code("skipped"), 0)

    def test_unexpected_or_empty_result_fails(self):
        for value in ("", "neutral", "timed_out", "SUCCESS"):
            with self.subTest(value=value):
                self.assertNotEqual(self.code(value), 0)


if __name__ == "__main__":
    unittest.main()
