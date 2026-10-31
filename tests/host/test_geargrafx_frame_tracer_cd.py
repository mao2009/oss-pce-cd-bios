"""Host-only result classification tests for the #603 CD/tracer bridge."""
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
import subprocess
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
        code, stderr = self.invoke(bridge.IntegrationBlocked("RAM observer ON: doctor prerequisites"))
        self.assertEqual(code, 3)
        self.assertEqual(stderr, "BLOCKED: frame-tracer CD integration: RAM observer ON: doctor prerequisites\n")

    def test_compile_probe_blocker_is_structured_blocked_exit_three(self):
        with mock.patch.object(bridge, "compile_probe", side_effect=ValueError("BLOCKED: missing g++")):
            with self.assertRaisesRegex(bridge.IntegrationBlocked, "CD diagnostic probe build"):
                bridge.execute_cd_diagnostic(Path("temporary"), Path("rom"), Path("cue"), Path("labels"))
        code, stderr = self.invoke(bridge.IntegrationBlocked("CD diagnostic probe build: BLOCKED: missing g++"))
        self.assertEqual(code, 3)
        self.assertEqual(stderr, "BLOCKED: frame-tracer CD integration: CD diagnostic probe build: BLOCKED: missing g++\n")

    def test_probe_exit_seven_is_structured_blocked_exit_three(self):
        with mock.patch.object(bridge, "compile_probe", return_value=Path("probe")), \
                mock.patch.object(bridge, "execute", side_effect=subprocess.CalledProcessError(7, ["probe"])):
            with self.assertRaisesRegex(bridge.IntegrationBlocked, "probe exit 7"):
                bridge.execute_cd_diagnostic(Path("temporary"), Path("rom"), Path("cue"), Path("labels"))
        code, stderr = self.invoke(bridge.IntegrationBlocked("CD diagnostic probe: core did not enable CD hardware (probe exit 7)"))
        self.assertEqual(code, 3)
        self.assertEqual(stderr, "BLOCKED: frame-tracer CD integration: CD diagnostic probe: core did not enable CD hardware (probe exit 7)\n")

    def test_oracle_assertion_is_fail_exit_one(self):
        code, stderr = self.invoke(ValueError("RAM region classification mismatch"))
        self.assertEqual(code, 1)
        self.assertEqual(stderr, "FAIL: frame-tracer CD integration oracle assertion: RAM region classification mismatch\n")

        code, stderr = self.invoke(AssertionError("CD-read oracle rejected records"))
        self.assertEqual(code, 1)
        self.assertEqual(stderr, "FAIL: frame-tracer CD integration oracle assertion: CD-read oracle rejected records\n")

    def test_unexpected_exception_is_fail_exit_one(self):
        code, stderr = self.invoke(KeyError("unexpected record shape"))
        self.assertEqual(code, 1)
        self.assertEqual(stderr, "FAIL: frame-tracer CD integration unexpected KeyError: 'unexpected record shape'\n")


if __name__ == "__main__":
    unittest.main()
