"""Host tests for the pre-side-effect probe plan (Issue #361).

Synthetic only: no emulator is built or run. Checks the case table covers V01-V08 exactly, that
the overall gate can never report PASS without every case PASS, that a probe mismatch fails
closed, and that Windows (no pinned core) is BLOCKED rather than skipped.
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "integration"))

import geargrafx_preside_probe as P  # noqa: E402
import geargrafx_st_bypass as st  # noqa: E402


class PresideProbeTest(unittest.TestCase):
    def test_cases_cover_v01_to_v08_exactly_once(self):
        self.assertEqual([c[0] for c in P.CASES], [f"V0{i}" for i in range(1, 9)])

    def test_st_cases_use_existing_probe_modes(self):
        modes = {m for case in P.CASES if case[2] == "st" for m in case[3]}
        self.assertEqual(modes, set(st.ST_MODES))

    def test_overall_is_blocked_unless_every_case_passes(self):
        self.assertEqual(P.overall([P.PASS] * 8), P.PASS)
        self.assertEqual(P.overall([]), P.BLOCKED)
        for bad in (P.BLOCKED, P.PARTIAL, P.NOT_RUN, P.FAIL):
            self.assertEqual(P.overall([P.PASS] * 7 + [bad]), P.BLOCKED)

    def test_v05_partial_lists_unrun_targets(self):
        with mock.patch.object(P.guard, "check_neg"), \
                mock.patch.object(P.guard, "run_mode", return_value={}):
            case = next(c for c in P.CASES if c[0] == "V05")
            _, verdict, detail = P.run_case(case, None, None, None)
        self.assertEqual(verdict, P.PARTIAL)
        self.assertIn("$6000", detail)
        self.assertNotEqual(verdict, P.PASS)

    def test_not_run_case_never_calls_the_probe(self):
        with mock.patch.object(P.guard, "run_mode") as run:
            case = next(c for c in P.CASES if c[0] == "V03")
            _, verdict, _ = P.run_case(case, None, None, None)
        self.assertEqual(verdict, P.NOT_RUN)
        run.assert_not_called()

    def test_probe_mismatch_fails_closed(self):
        # A probe row that does not match the expected ST behaviour must be FAIL, never BLOCKED or PASS.
        with mock.patch.object(P.guard, "run_mode", return_value={"exit": 0, "status": "rejected"}):
            case = next(c for c in P.CASES if c[0] == "V01")
            _, verdict, _ = P.run_case(case, None, None, None)
        self.assertEqual(verdict, P.FAIL)

    def test_non_linux_is_blocked_not_skipped(self):
        with mock.patch.object(P.sys, "platform", "win32"):
            with self.assertRaisesRegex(ValueError, "BLOCKED, not PASS"):
                P.main()


if __name__ == "__main__":
    unittest.main()
