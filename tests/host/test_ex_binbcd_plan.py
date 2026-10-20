"""Host checks for the non-executing EX_BINBCD measurement-plan fixture (Issue #310, parent #70).

The fixture is data only. These tests pin its input coverage and require every reference expectation
to stay UNKNOWN and every case BLOCKED. They never assemble, load or call the reference $E0B4 routine,
and they do not re-verify the project-own routine (tests/host/test_ex_binbcd.py covers that).
"""
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "ex-binbcd-measurement-plan.json"
PLAN = ROOT / "docs" / "ex-binbcd-measurement-plan.md"
REQUIRED_INPUTS = [0, 1, 9, 10, 99, 100, 127, 128, 255]
OBSERVABLES = {"A", "X", "Y", "P_C", "P_Z", "P_N", "P_V", "P_D", "P_I", "P_T", "SP",
               "MPR0-MPR7", "related_RAM", "return_PC"}


class ExBinBcdMeasurementPlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def test_reference_status_stays_blocked_and_not_executed(self):
        self.assertEqual(self.plan["slot"], "$3C")
        self.assertEqual(self.plan["entry_candidate"], "$E0B4")
        self.assertEqual(self.plan["abi_status"], "BLOCKED_ABI")
        self.assertIs(self.plan["release_eligible"], False)
        self.assertEqual(self.plan["execution"], "NOT_RUN")
        self.assertEqual(self.plan["reference_jsr"], "NOT_CALLED")
        self.assertEqual(self.plan["gates"], ["#173", "#228"])

    def test_input_coverage_is_exactly_the_required_nine(self):
        inputs = [case["input"] for case in self.plan["cases"]]
        self.assertEqual(inputs, REQUIRED_INPUTS)
        for value in inputs:
            self.assertIs(type(value), int)
            self.assertTrue(0 <= value <= 255)

    def test_every_case_is_blocked_with_unknown_expectation(self):
        for case in self.plan["cases"]:
            with self.subTest(case=case["id"]):
                self.assertEqual(case["status"], "BLOCKED")
                self.assertEqual(case["expected"], "UNKNOWN")
                self.assertEqual(case["hex"], "$%02X" % case["input"])

    def test_out_of_range_inputs_are_not_characterised(self):
        # The issue requires > 99 to stay UNKNOWN; no source supports any concrete value there.
        out = [c for c in self.plan["cases"] if c["input"] > 99]
        self.assertEqual([c["input"] for c in out], [100, 127, 128, 255])
        for case in out:
            self.assertEqual(case["expected"], "UNKNOWN")
        self.assertEqual(self.plan["callee_abi"]["out_of_range"],
                         "UNKNOWN (input > 99 is not characterised by any source)")

    def test_observables_cover_required_items_and_callee_abi_is_unknown(self):
        self.assertEqual(set(self.plan["observables"]), OBSERVABLES)
        self.assertEqual(self.plan["expected_default"], "UNKNOWN")
        for key in ("returns", "registers", "flags"):
            self.assertEqual(self.plan["callee_abi"][key], "UNKNOWN")

    def test_plan_document_states_the_same_gate_and_scope(self):
        text = PLAN.read_text(encoding="utf-8")
        for token in ("BLOCKED", "#173", "#228", "NOT_RUN", "UNKNOWN", "tests/host/test_ex_binbcd.py"):
            self.assertIn(token, text)


if __name__ == "__main__":
    unittest.main()
