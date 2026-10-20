"""Negative paths of the real-core ex_bcdbin run (Issue #71), moved out of tests/host (Issue #333).

These launch tests/integration/geargrafx_bcdbin.py, which loads a Geargrafx core, so they run in
`make integration-test` and never in the host stage.
"""
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.environment import requires_cc65

INTEGRATION = Path(__file__).resolve().parent / "geargrafx_bcdbin.py"


class RealCoreFailurePathTests(unittest.TestCase):
    """Negative paths of the real-core run: each must fail, never SKIP (OK) or PASS.

    Linux (make setup) checks the exact exit code (3 for BLOCKED, 1 for run failures) and the cause.
    Non-Linux hosts stop at the platform check: every path must then exit 3 with the BLOCKED tag.
    Only Linux exercises the core-missing, tool-missing, launch and timeout causes.
    """

    def run_integration(self, env_updates, timeout=120):
        env = dict(os.environ, **env_updates)
        result = subprocess.run([sys.executable, str(INTEGRATION)], capture_output=True, text=True,
                                env=env, timeout=timeout)
        return result.returncode, result.stdout + result.stderr

    def assert_failure(self, env_updates, cause, exit_code, timeout=120):
        returncode, output = self.run_integration(env_updates, timeout)
        self.assertNotIn("skipped", output.lower())
        if sys.platform.startswith("linux"):
            self.assertEqual(returncode, exit_code, output)
            self.assertIn(cause, output)
        else:
            self.assertEqual(returncode, 3, output)
            self.assertIn("BLOCKED real core", output)

    def test_core_missing_is_blocked(self):
        missing = Path(tempfile.gettempdir()) / "missing-geargrafx-core-issue71.so"
        self.assert_failure({"GEARGRAFX_CORE": str(missing)}, "missing Geargrafx core", 3)

    def test_ca65_missing_is_blocked(self):
        self.assert_failure({"GEARGRAFX_CORE": str(INTEGRATION), "CA65": "/nonexistent/ca65-issue71"},
                            "missing tool ca65", 3)

    def test_ld65_missing_is_blocked(self):
        self.assert_failure({"GEARGRAFX_CORE": str(INTEGRATION), "CA65": sys.executable,
                             "LD65": "/nonexistent/ld65-issue71"}, "missing tool ld65", 3)

    @requires_cc65
    def test_launch_failure_exits_nonzero(self):
        with tempfile.TemporaryDirectory(prefix="issue71-bogus-core-") as directory:
            bogus = Path(directory) / "bogus_core.so"
            bogus.write_bytes(b"not a shared object\n")
            self.assert_failure({"GEARGRAFX_CORE": str(bogus)}, "Geargrafx run failed", 1)

    @requires_cc65
    def test_timeout_exits_nonzero(self):
        # A core whose constructor never returns: the worker hangs until run_core's 30 s timeout.
        with tempfile.TemporaryDirectory(prefix="issue71-hang-core-") as directory:
            hang = Path(directory) / "hang_core.so"
            if sys.platform.startswith("linux"):
                source = Path(directory) / "hang.c"
                source.write_text("__attribute__((constructor)) static void hang(void) { for (;;) {} }\n")
                subprocess.run(["gcc", "-shared", "-fPIC", "-o", str(hang), str(source)], check=True)
            else:
                hang.write_bytes(b"placeholder: non-Linux hosts stop at the platform check\n")
            self.assert_failure({"GEARGRAFX_CORE": str(hang)}, "TimeoutExpired", 1, timeout=300)


if __name__ == "__main__":
    unittest.main()
