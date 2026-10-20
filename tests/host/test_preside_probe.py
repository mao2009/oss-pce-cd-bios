"""Host tests for the pre-side-effect probe plan (Issue #361).

Synthetic only: no emulator is built or run. Checks the case table covers V01-V08 exactly, that
the overall gate can never report PASS without every case PASS, that a probe mismatch fails
closed, and that Windows (no pinned core) is BLOCKED rather than skipped.
"""
import io
import sys
import unittest
from contextlib import redirect_stdout
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
        out = io.StringIO()
        with mock.patch.object(P.sys, "platform", "win32"), redirect_stdout(out):
            rc = P.main()
        self.assertEqual(rc, 1)
        self.assertIn("BLOCKED", out.getvalue())
        self.assertIn("OVERALL BLOCKED", out.getvalue())
        self.assertNotIn("OVERALL PASS", out.getvalue())

    def _run_main_with(self, verdict):
        out = io.StringIO()
        results = [(c[0], verdict, "x") for c in P.CASES]
        with mock.patch.object(P.sys, "platform", "linux"), \
                mock.patch.object(P.guard, "synthetic_media", return_value=(Path("b"), Path("c"))), \
                mock.patch.object(P, "compile_probe", return_value=None), \
                mock.patch.object(P, "run_case", side_effect=lambda case, *_: (case[0], verdict, "x")), \
                redirect_stdout(out):
            rc = P.main()
        return rc, out.getvalue(), results

    def test_main_exit_code_is_nonzero_when_overall_blocked(self):
        for verdict in (P.NOT_RUN, P.PARTIAL, P.BLOCKED):
            rc, out, _ = self._run_main_with(verdict)
            self.assertEqual(rc, 1, verdict)
            self.assertIn("OVERALL BLOCKED", out)

    def test_main_exit_code_is_zero_only_when_every_case_passes(self):
        rc, out, _ = self._run_main_with(P.PASS)
        self.assertEqual(rc, 0)
        self.assertIn("OVERALL PASS", out)

    def test_output_states_pass_is_not_a_gate_release(self):
        rc, out, _ = self._run_main_with(P.PASS)
        self.assertIn("V01-V08 のみ", out)
        self.assertIn("ゲート解放ではない", out)
        self.assertIn("ゲート解放ではない", P.__doc__)

    def test_w_map_covers_w01_to_w18_exactly_once(self):
        self.assertEqual(sorted(P.W_MAP), [f"W{i:02d}" for i in range(1, 19)])
        self.assertTrue(all(g in ("RUN", "NOT_RUN", "RANGE", "UNMAPPED") for g, _ in P.W_MAP.values()))

    def test_not_run_group_points_only_at_not_run_cases(self):
        kinds = {c[0]: c[2] for c in P.CASES}
        not_run = {w: c for w, (g, c) in P.W_MAP.items() if g == "NOT_RUN"}
        self.assertEqual(not_run, {"W14": "V03", "W15": "V04", "W17": "V07"})
        for case_id in not_run.values():
            self.assertEqual(kinds[case_id], "none")

    def test_run_group_points_only_at_runnable_cases(self):
        kinds = {c[0]: c[2] for c in P.CASES}
        for w, (g, c) in P.W_MAP.items():
            if g == "RUN":
                self.assertIn(kinds[c], ("st", "neg", "tam"), w)

    def test_w18_is_a_range_remainder_not_a_row(self):
        self.assertEqual(P.W_MAP["W18"], ("RANGE", "V05"))
        rc, out, _ = self._run_main_with(P.NOT_RUN)
        self.assertIn("W-MAP RANGE: W18->V05", out)

    def test_unmapped_ids_are_printed_not_dropped(self):
        rc, out, _ = self._run_main_with(P.NOT_RUN)
        unmapped = [w for w, (g, _) in P.W_MAP.items() if g == "UNMAPPED"]
        self.assertEqual(unmapped, ["W01", "W04", "W08", "W09", "W10", "W11", "W12", "W13", "W16"])
        self.assertIn("W-MAP UNMAPPED: " + ", ".join(unmapped), out)
        self.assertIn("W-MAP NOT_RUN: W14->V03, W15->V04, W17->V07", out)

    def test_counts_keep_verdicts_distinct(self):
        rc, out, _ = self._run_main_with(P.PARTIAL)
        self.assertIn(f"COUNTS PASS=0 PARTIAL={len(P.CASES)} NOT_RUN=0 BLOCKED=0 FAIL=0", out)

    def test_v05_planned_covers_every_page_start_and_end(self):
        starts = {p * 0x2000 for p in range(8)}
        ends = {p * 0x2000 + 0x1FFF for p in range(8)}
        self.assertEqual(set(P.V05_PLANNED), starts | ends)

    def test_v05_partial_detail_lists_unrun_remainder(self):
        with mock.patch.object(P.guard, "check_neg"), \
                mock.patch.object(P.guard, "run_mode", return_value={}):
            case = next(c for c in P.CASES if c[0] == "V05")
            _, verdict, detail = P.run_case(case, None, None, None)
        self.assertEqual(verdict, P.PARTIAL)
        for addr in ("$C000", "$5FFF", "$FFFF", "$2000"):
            self.assertIn(addr, detail)
        self.assertNotIn("$0000", detail.split("unrun")[-1])

    def test_no_case_can_yield_pass(self):
        with mock.patch.object(P.guard, "run_mode", return_value={}), \
                mock.patch.object(P.st, "check_st", return_value=st.BLOCKED_VERDICT), \
                mock.patch.object(P.guard, "check_neg"), mock.patch.object(P.guard, "check_tam"):
            verdicts = [P.run_case(c, None, None, None)[1] for c in P.CASES]
        self.assertNotIn(P.PASS, verdicts)

    def test_docstring_states_no_pass_and_no_gate_release(self):
        self.assertIn("PASS を出力しない", P.__doc__)
        self.assertIn("上限 PARTIAL", P.__doc__)
        self.assertIn("ゲート解放の経路はない", P.__doc__)

    def test_v06_partial_states_detection_only_not_block(self):
        with mock.patch.object(P.guard, "run_mode", return_value={}), mock.patch.object(P.guard, "check_tam"):
            case = next(c for c in P.CASES if c[0] == "V06")
            _, verdict, detail = P.run_case(case, None, None, None)
        self.assertEqual(verdict, P.PARTIAL)
        self.assertIn("検出のみ、遮断ではない", detail)
        self.assertIn("PARTIAL", P.__doc__)


if __name__ == "__main__":
    unittest.main()
