"""Host-only result classification tests for the #603 CD/tracer bridge."""
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "integration"))
import geargrafx_frame_tracer_cd as bridge  # noqa: E402


class FrameTracerCdResultTests(unittest.TestCase):
    def invoke(self, error):
        stderr = StringIO()
        with mock.patch.object(bridge, "run", side_effect=error), redirect_stderr(stderr):
            code = bridge.main([])
        return code, stderr.getvalue()

    def test_tracer_blocker_is_explicit_and_exit_three(self):
        code, stderr = self.invoke(bridge.TracerBlocked("RAM observer ON: doctor prerequisites"))
        self.assertEqual(code, 3)
        self.assertEqual(stderr, "BLOCKED: frame-tracer CD integration: RAM observer ON: doctor prerequisites\n")

    def test_probe_execution_failure_is_not_reported_as_blocked_or_assertion(self):
        code, stderr = self.invoke(bridge.ProbeExecutionFailed("CD diagnostic probe execution: exit 7"))
        self.assertEqual(code, 1)
        self.assertEqual(stderr, "FAIL: frame-tracer CD integration: CD diagnostic probe execution: exit 7\n")

    def test_probe_compile_exception_is_caught_as_probe_execution_failure(self):
        with mock.patch.object(bridge, "compile_probe", side_effect=OSError("core unavailable")):
            with self.assertRaisesRegex(bridge.ProbeExecutionFailed, "CD diagnostic probe execution: core unavailable"):
                bridge.execute_cd_diagnostic(Path("temporary"), Path("rom"), Path("cue"), Path("labels"))

    def test_probe_value_error_is_not_misclassified_as_an_assertion(self):
        with mock.patch.object(bridge, "compile_probe", return_value=Path("probe")), \
                mock.patch.object(bridge, "execute", side_effect=ValueError("malformed probe record")):
            with self.assertRaisesRegex(bridge.ProbeExecutionFailed, "malformed probe record"):
                bridge.execute_cd_diagnostic(Path("temporary"), Path("rom"), Path("cue"), Path("labels"))

    def test_assertion_failure_is_distinct_from_probe_execution(self):
        code, stderr = self.invoke(ValueError("RAM region classification mismatch"))
        self.assertEqual(code, 1)
        self.assertEqual(stderr, "FAIL: frame-tracer CD integration assertion: RAM region classification mismatch\n")


if __name__ == "__main__":
    unittest.main()
