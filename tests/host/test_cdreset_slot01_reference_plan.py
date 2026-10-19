"""Positive/negative host tests for the slot-$01 observation plan and execution gate (#181).

Synthetic only: the pure plan/gate functions and the ``--check`` CLI of
``tools/cdreset_slot01_reference_plan.py``. No emulator is launched, no reference BIOS is read
and no reference API JSR is executed. With the real materials both media conditions must stay
NOT_RUN + BLOCKED (exit 3); a self-declared gate flag, a faked $1804 result, mistyped review
evidence, guessed observation values and any non-$01 API must each fail closed; only an input
where every material is synthetic-complete may flip the gate verdict (falsifiability).
"""
import copy
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import cdreset_safety_preflight as safety
import cdreset_slot01_reference_plan as M
from cdreset_slot01_fixture import CASES

REVIEW = {"reviewer": "reviewer-a", "pr": 194, "label": "approved"}
BANNED_OUTPUT = ("MODEL_ELIGIBLE", "ALLOWED", "PASS", "VERIFIED", "270")


def complete_materials():
    """Synthetic materials where every gate material is satisfied (never real evidence)."""
    return {"mmio_1804": M.MMIO_PROVEN,
            "posture": dict(safety.CURRENT_POSTURE, hardware_page_policy="intercept",
                            st_instruction_intercept=True, irq_register_intercept=True,
                            entry_environment_reproduced=True),
            "abi": {"status": "REVIEWED", "entry_environment": "KNOWN"},
            "unproven": {"st0_st1_st2": False, "irq_registers": False,
                         "mpr_change": False, "bank_alias": False},
            "review": dict(REVIEW), "self_declared_gate": False}


def plans():
    return [M.build_plan(case) for case in CASES]


class Slot01ReferencePlanTests(unittest.TestCase):
    def test_two_media_records_are_unknown_and_stay_blocked(self):
        records = plans()
        self.assertEqual({record["media"] for record in records}, {"present", "absent"})
        self.assertEqual(len(records), 2)
        for record in records:
            with self.subTest(case=record["case_id"]):
                self.assertEqual(M.validate_plan(record), [])
                self.assertEqual((record["slot"], record["entry_candidate"], record["execution"]),
                                 ("$01", "$E003", "NOT_RUN"))
                # No field may hold anything but UNKNOWN observation / tagged expectation.
                self.assertTrue(all(value == M.UNKNOWN for value in record["observation"].values()))
                self.assertTrue(all(value in M.EXPECTATION_TAGS for value in record["expectation"].values()))
        execution, verdict, reasons = M.evaluate(records, M.CURRENT_MATERIALS)
        self.assertEqual((execution, verdict), ("NOT_RUN", "BLOCKED"))
        joined = "; ".join(reasons)
        for marker in ("mmio_1804_pre_side_effect_rejection_not_established",
                       "hazard_posture_incomplete", "unproven_st0_st1_st2",
                       "unproven_irq_registers", "independent_review_missing"):
            self.assertIn(marker, joined)
        for banned in BANNED_OUTPUT:
            self.assertNotIn(banned, joined)

    def test_self_declared_gate_alone_never_unblocks(self):
        # ① a self-declared safety_gate_approved flag is input, never approval evidence.
        materials = dict(M.CURRENT_MATERIALS, self_declared_gate=True)
        verdict, reasons = M.decide(materials)
        self.assertEqual(verdict, "BLOCKED")
        self.assertIn("self_declared_gate_not_evidence", "; ".join(reasons))
        self.assertIsNone(materials["review"])

    def test_faked_mmio1804_result_still_blocked_by_unproven_st_irq(self):
        # ② even a faked $1804 pre-trap success cannot pass while ST/IRQ lack guarantees.
        verdict, reasons = M.decide(dict(M.CURRENT_MATERIALS, mmio_1804=M.MMIO_PROVEN))
        self.assertEqual(verdict, "BLOCKED")
        joined = "; ".join(reasons)
        self.assertIn("unproven_st0_st1_st2", joined)
        self.assertIn("unproven_irq_registers", joined)
        unproven = complete_materials()
        unproven["unproven"] = dict(unproven["unproven"], st0_st1_st2=True, irq_registers=True)
        verdict, reasons = M.decide(unproven)  # every other material is synthetic-complete
        self.assertEqual(verdict, "BLOCKED")
        self.assertEqual([reason.split(":")[0] for reason in reasons],
                         ["unproven_st0_st1_st2", "unproven_irq_registers"])

    def test_review_evidence_missing_or_mistyped_fails_closed(self):
        malformed = ([], {}, {"reviewer": "a", "pr": 194}, {"reviewer": "", "pr": 194, "label": "approved"},
                     {"reviewer": "a", "pr": "194", "label": "approved"},
                     {"reviewer": "a", "pr": 0, "label": "approved"},
                     {"reviewer": "a", "pr": 194, "label": "lgtm"},
                     {"reviewer": "a", "pr": 194, "label": "approved", "extra": 1})
        # ③ absent evidence and mistyped evidence both block; a wrong label is not approval.
        missing = M.decide(dict(M.CURRENT_MATERIALS, review=None))
        self.assertEqual(missing[0], "BLOCKED")
        self.assertIn("independent_review_missing", "; ".join(missing[1]))
        for bad in malformed:
            with self.subTest(review=bad):
                verdict, reasons = M.decide(dict(complete_materials(), review=bad))
                self.assertEqual(verdict, "BLOCKED")
                self.assertTrue(any("malformed_configuration" in reason for reason in reasons))

    def test_guessed_observation_or_untagged_expectation_is_rejected(self):
        # ④ an observation value that is not UNKNOWN is a guess, not evidence.
        for field, guess in (("return_pc", "0xE006"), ("A", 0), ("core_sha256", "deadbeef")):
            with self.subTest(field=field):
                record = copy.deepcopy(plans()[0])
                record["observation"][field] = guess
                self.assertTrue(any("UNKNOWN" in error for error in M.validate_plan(record)))
                self.assertEqual(M.evaluate([record], M.CURRENT_MATERIALS)[1], "BLOCKED")
        record = copy.deepcopy(plans()[1])
        record["expectation"]["returns"] = "returns to JSR+3"  # guess: tags only
        self.assertTrue(any("tagged" in error for error in M.validate_plan(record)))

    def test_any_api_other_than_slot01_is_rejected(self):
        # ⑤ executing any API other than slot $01 stays forbidden; foreign slots never plan.
        for slot, entry in (("$1E", "$E05A"), ("$01", "$E05A"), ("$00", "$E000")):
            with self.subTest(slot=slot, entry=entry):
                record = dict(plans()[0], slot=slot, entry_candidate=entry)
                errors = M.validate_plan(record)
                self.assertTrue(any("$01" in error for error in errors))
                execution, verdict, reasons = M.evaluate([record], M.CURRENT_MATERIALS)
                self.assertEqual((execution, verdict), ("NOT_RUN", "BLOCKED"))
                self.assertTrue(any("malformed_configuration" in reason for reason in reasons))

    def test_complete_synthetic_materials_flip_the_verdict(self):
        # Falsifiability: only synthetic-complete materials leave BLOCKED — never the real ones.
        execution, verdict, reasons = M.evaluate(plans(), complete_materials())
        self.assertEqual((execution, verdict, reasons), ("NOT_RUN", "MATERIALS_COMPLETE", []))
        self.assertNotIn(verdict, ("BLOCKED", "ALLOWED", "PASS", "VERIFIED"))
        self.assertEqual(M.evaluate(plans(), M.CURRENT_MATERIALS)[1], "BLOCKED")

    def test_cli_prints_not_run_blocked_and_exits_3(self):
        # ⑥ the real materials can only ever be NOT_RUN + BLOCKED, exit 3, with no permission.
        result = subprocess.run([sys.executable, str(ROOT / "tools" / "cdreset_slot01_reference_plan.py"),
                                 "--check"], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, M.EXIT_BLOCKED, result.stderr)
        lines = result.stdout.splitlines()
        self.assertTrue(lines[0].startswith("NOT_RUN: "), result.stdout)
        self.assertTrue(lines[1].startswith("BLOCKED: "), result.stdout)
        for banned in BANNED_OUTPUT:
            self.assertNotIn(banned, result.stdout)
        no_argument = subprocess.run([sys.executable, str(ROOT / "tools" / "cdreset_slot01_reference_plan.py")],
                                     capture_output=True, text=True, check=False)
        self.assertNotEqual(no_argument.returncode, 0)

    def test_materials_complete_output_is_explicitly_not_a_permission(self):
        # If the synthetic materials are complete, the wording must say it is not permission.
        import contextlib
        import io
        from unittest import mock
        with mock.patch.object(M, "CURRENT_MATERIALS", complete_materials()), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            code = M.main(["--check"])
        self.assertEqual(code, M.EXIT_BLOCKED)
        self.assertIn("NOT an execution permission", out.getvalue())
        self.assertNotIn("BLOCKED:", out.getvalue())

    def test_tool_has_no_execution_capability(self):
        source = (ROOT / "tools" / "cdreset_slot01_reference_plan.py").read_text(encoding="utf-8")
        for banned in ("import subprocess", "os.system", "popen(", ".read_bytes", "ALLOWED",
                       "PASS", "VERIFIED", "270", "rom/"):
            self.assertNotIn(banned, source)


if __name__ == "__main__":
    unittest.main()
