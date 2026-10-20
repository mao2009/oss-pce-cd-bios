"""Host tests for the pre-side-effect probe plan (Issue #361).

Synthetic only: no emulator is built or run. Checks the case table covers V01-V08 exactly, that
the overall gate can never report PASS without every case PASS, that a probe mismatch fails
closed, and that Windows (no pinned core) is BLOCKED rather than skipped.
"""
import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
# A non-empty observation: the run produced evidence; the checker itself is mocked where used.
OBS = {"exit": 0, "status": "ok", "mode": "stub"}
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
                mock.patch.object(P.guard, "run_mode", return_value=OBS):
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

    def test_invalid_observation_is_blocked_not_fail(self):
        # An observation that fails validation (wrong status) is no valid evidence: BLOCKED, never FAIL or PASS.
        with mock.patch.object(P.guard, "run_mode", return_value={"exit": 0, "status": "rejected"}):
            case = next(c for c in P.CASES if c[0] == "V01")
            _, verdict, detail = P.run_case(case, None, None, None)
        self.assertEqual(verdict, P.BLOCKED)
        self.assertIn("no valid evidence", detail)

    def test_valid_landed_observation_stays_fail(self):
        # A VALID V05 observation in which the store landed (before != after) contradicts the expectation: FAIL.
        landed = {"exit": 0, "status": "ok", "detected": True, "guard_target": 0,
                  "guard_before": 0x11, "guard_after": 0x22}
        case = next(c for c in P.CASES if c[0] == "V05")
        with mock.patch.object(P.guard, "run_mode", return_value=landed):
            _, verdict, detail = P.run_case(case, None, None, None)
        self.assertEqual(verdict, P.FAIL)
        self.assertIn("NOT prevented", detail)

    def test_v05_invalid_observation_is_blocked(self):
        # Wrong status on V05 is an invalid observation (BLOCKED), not a landed store (FAIL).
        case = next(c for c in P.CASES if c[0] == "V05")
        with mock.patch.object(P.guard, "run_mode", return_value={"exit": 0, "status": "rejected"}):
            _, verdict, _ = P.run_case(case, None, None, None)
        self.assertEqual(verdict, P.BLOCKED)

    def test_v05_run_targets_are_planned_samples(self):
        # Every measured V05 target must be one of the planned samples.
        self.assertTrue(set(P.V05_RUN) <= set(P.V05_PLANNED))

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
        self.assertTrue(all(g in ("RUN", "NOT_RUN", "UNMAPPED") for g, _ in P.W_MAP.values()))

    def test_w_map_ids_are_rows_of_the_360_inventory(self):
        # #360 spec/write_path_inventory.json is the contract: every W-id here must be one of its rows.
        inventory = json.loads((ROOT / "spec" / "write_path_inventory.json").read_text(encoding="utf-8"))
        rows = {row["id"]: row for row in inventory["rows"]}
        self.assertEqual(sorted(rows), sorted(P.W_MAP))

    def test_w18_matches_360_contract_and_is_never_claimed(self):
        # #360 records W18 as an inventory row: UNKNOWN status, UNKNOWN prevention, NOT_MEASURED, BLOCKED.
        inventory = json.loads((ROOT / "spec" / "write_path_inventory.json").read_text(encoding="utf-8"))
        row = next(r for r in inventory["rows"] if r["id"] == "W18")
        self.assertEqual((row["monitor_status"], row["prevents_before_side_effect"], row["measurement"],
                          row["gate_status"]), ("UNKNOWN", "UNKNOWN", "NOT_MEASURED", "BLOCKED"))
        # No probe covers W18's range (pages 3-7 and the rest of page 2), so no V-case or run is mapped to it.
        self.assertEqual(P.W_MAP["W18"], ("UNMAPPED", None))
        self.assertNotIn("PREVENTED", "".join(str(v) for v in row.values()))

    def test_v05_samples_do_not_stand_in_for_w18(self):
        # V05 is a W02 sample plan; its unrun points are not W18's remainder.
        self.assertNotIn("W18", [w for w, (g, c) in P.W_MAP.items() if c == "V05"])
        self.assertEqual({w for w, (g, c) in P.W_MAP.items() if c == "V05"}, {"W02"})

    def test_missing_evidence_is_blocked_not_pass_or_fail(self):
        case = next(c for c in P.CASES if c[0] == "V05")
        # A refused run (exit 8) and a run with no observation are both missing evidence.
        with mock.patch.object(P.guard, "run_mode", return_value={"exit": 8, "stderr": "alias"}):
            _, verdict, detail = P.run_case(case, None, None, None)
        self.assertEqual(verdict, P.BLOCKED)
        self.assertIn("no evidence", detail)
        with mock.patch.object(P.guard, "run_mode", side_effect=ValueError("no JSON")):
            _, verdict, _ = P.run_case(case, None, None, None)
        self.assertEqual(verdict, P.BLOCKED)

    def test_empty_observation_is_blocked_for_v01_v05_v06(self):
        # exit 0 with no observation fields is missing evidence: BLOCKED, never FAIL.
        for case_id in ("V01", "V05", "V06"):
            case = next(c for c in P.CASES if c[0] == case_id)
            with mock.patch.object(P.guard, "run_mode", return_value={"exit": 0}):
                _, verdict, detail = P.run_case(case, None, None, None)
            self.assertEqual(verdict, P.BLOCKED, case_id)
            self.assertIn("empty observation", detail)

    def test_v05_landed_store_is_fail_not_blocked(self):
        # A store that lands contradicts the expectation: the run executed, so FAIL.
        case = next(c for c in P.CASES if c[0] == "V05")
        with mock.patch.object(P.guard, "run_mode", return_value=OBS), \
                mock.patch.object(P.guard, "check_neg",
                                  side_effect=ValueError("forbidden store $0000 was NOT prevented")):
            _, verdict, detail = P.run_case(case, None, None, None)
        self.assertEqual(verdict, P.FAIL)
        self.assertIn("NOT prevented", detail)

    def test_v01_prevented_st_store_is_fail_not_pass(self):
        # #360 records W05 as prevents NO, so an ST store that does not land is a contradiction: FAIL.
        case = next(c for c in P.CASES if c[0] == "V01")
        with mock.patch.object(P.guard, "run_mode", return_value=OBS), \
                mock.patch.object(P.st, "check_st",
                                  side_effect=ValueError("st0: address register unchanged (side effect did not land)")):
            _, verdict, _ = P.run_case(case, None, None, None)
        self.assertEqual(verdict, P.FAIL)
        self.assertNotEqual(verdict, P.PASS)

    def test_v05_detail_never_claims_prevention(self):
        with mock.patch.object(P.guard, "check_neg"), mock.patch.object(P.guard, "run_mode", return_value=OBS):
            case = next(c for c in P.CASES if c[0] == "V05")
            _, _, detail = P.run_case(case, None, None, None)
        self.assertNotIn("prevented", detail)
        self.assertIn("no PREVENTED claim", detail)

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

    def test_v05_is_the_neg_write_case(self):
        # V05 is the W02 neg-write sample plan; the kind keeps it on the neg-write probe path.
        kinds = {c[0]: c[2] for c in P.CASES}
        self.assertEqual(kinds["V05"], "neg")
        self.assertEqual(P.W_MAP["W02"], ("RUN", "V05"))

    def test_unmapped_ids_are_printed_not_dropped(self):
        rc, out, _ = self._run_main_with(P.NOT_RUN)
        unmapped = [w for w, (g, _) in P.W_MAP.items() if g == "UNMAPPED"]
        self.assertEqual(unmapped, ["W01", "W04", "W08", "W09", "W10", "W11", "W12", "W13", "W16", "W18"])
        self.assertNotIn("W-MAP PARTIAL", out)
        self.assertIn("W-MAP UNMAPPED: " + ", ".join(unmapped), out)
        self.assertIn("W-MAP NOT_RUN: W14->V03, W15->V04, W17->V07", out)

    def test_counts_keep_verdicts_distinct(self):
        rc, out, _ = self._run_main_with(P.PARTIAL)
        self.assertIn(f"COUNTS PASS=0 PARTIAL={len(P.CASES)} NOT_RUN=0 BLOCKED=0 FAIL=0", out)

    def test_v05_planned_is_non_allowlisted_page_boundaries(self):
        # Page starts and ends outside the allowlisted window [$2000,$4000) (#360 W16), nothing else.
        expected = (0x0000, 0x1FFF, 0x4000, 0x5FFF, 0x6000, 0x7FFF, 0x8000, 0x9FFF,
                    0xA000, 0xBFFF, 0xC000, 0xDFFF, 0xE000, 0xFFFF)
        self.assertEqual(P.V05_PLANNED, expected)

    def test_v05_planned_never_targets_allowlisted_addresses(self):
        for addr in P.V05_PLANNED:
            self.assertFalse(0x2000 <= addr < 0x4000, f"${addr:04X} is inside the allowlist")
        self.assertNotIn(0x2000, P.V05_PLANNED)
        self.assertNotIn(0x3FFF, P.V05_PLANNED)

    def test_v05_partial_detail_lists_unrun_remainder(self):
        with mock.patch.object(P.guard, "check_neg"), \
                mock.patch.object(P.guard, "run_mode", return_value=OBS):
            case = next(c for c in P.CASES if c[0] == "V05")
            _, verdict, detail = P.run_case(case, None, None, None)
        self.assertEqual(verdict, P.PARTIAL)
        for addr in ("$C000", "$5FFF", "$FFFF"):
            self.assertIn(addr, detail)
        self.assertNotIn("W18", detail)
        remainder = detail.split("unrun planned samples")[-1]
        self.assertNotIn("$2000", remainder)
        self.assertNotIn("$3FFF", remainder)
        self.assertNotIn("$0000", remainder)

    def test_no_case_can_yield_pass(self):
        with mock.patch.object(P.guard, "run_mode", return_value=OBS), \
                mock.patch.object(P.st, "check_st", return_value=st.BLOCKED_VERDICT), \
                mock.patch.object(P.guard, "check_neg"), mock.patch.object(P.guard, "check_tam"):
            verdicts = [P.run_case(c, None, None, None)[1] for c in P.CASES]
        self.assertNotIn(P.PASS, verdicts)

    def test_docstring_states_no_pass_and_no_gate_release(self):
        self.assertIn("PASS を出力しない", P.__doc__)
        self.assertIn("上限 PARTIAL", P.__doc__)
        self.assertIn("ゲート解放の経路はない", P.__doc__)

    def test_v06_partial_states_detection_only_not_block(self):
        with mock.patch.object(P.guard, "run_mode", return_value=OBS), mock.patch.object(P.guard, "check_tam"):
            case = next(c for c in P.CASES if c[0] == "V06")
            _, verdict, detail = P.run_case(case, None, None, None)
        self.assertEqual(verdict, P.PARTIAL)
        self.assertIn("検出のみ、遮断ではない", detail)
        self.assertIn("PARTIAL", P.__doc__)


if __name__ == "__main__":
    unittest.main()
